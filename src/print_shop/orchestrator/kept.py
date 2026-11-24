"""A job that waits for the person outlives the process that prepared it (mqucifer/print-shop#209).

Two things are kept, both on disk beside the job, so a closed terminal or a restart is never a run
from the start:

- **Which jobs wait, and where** (`WaitingJobs`): a table in the jobs folder's `jobs.sqlite`,
  beside the graph's checkpoints. A job is added when it's submitted, with what it was given and
  a copy of the profiles it slices with, and goes when it ends, however it ends. Only a job in
  it is ever taken up again, so the checkpoints of jobs from before it are left alone.
- **What the person and the model have given a job** (`Kept`): `kept.json` in its folder. The
  questions' answers as each set is answered, the questions and answers whole once they're done,
  the print loop's result, each proposal, and each answer at the review. The slice step is one
  node of the graph, checkpointed only when it ends, so a restart runs it again from its start:
  what's kept is given back in place of asking or calling again, and the person is back at the
  step they left, with what they'd answered and corrected, and the same slice.

**An approval is never kept.** A Start is spent when it's taken, and nothing here records one:
a restart leaves a job ready, never approved and never armed. The review's `ok` is kept, as it's
the review's and not the print's.

Nothing here depends on the Mac: plain files and SQLite in the jobs folder.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter

from print_shop.setup.intake import Answer, Asker, Intake, Question
from print_shop.setup.loop import LoopResult
from print_shop.setup.proposal import Change, Proposal
from print_shop.setup.review import Review, Reviewer, ReviewRequest
from print_shop.sliced import SlicedJob
from print_shop.slicer.orca import SlicerProfiles

__all__ = ["KEPT_NAME", "Kept", "WaitingJob", "WaitingJobs", "Waits"]

logger = logging.getLogger(__name__)

#: The file in a job's folder that keeps what the person and the model gave it.
KEPT_NAME = "kept.json"


class Waits(StrEnum):
    """Where a waiting job is, as it's said."""

    PREPARING = "being prepared"
    QUESTIONS = "at the questions"
    REVIEW = "at the review"
    MESH = "at the mesh gate"
    READY = "ready to print"


class WaitingJob(BaseModel):
    """A job that's waiting, as the jobs folder keeps it."""

    job_id: str
    part: str
    #: What the job was submitted with: its graph's first state, less what each run adds.
    given: dict[str, Any]
    #: The profiles it slices with, copied into its folder. None for a file sliced by hand.
    profiles: SlicerProfiles | None = None
    waits: Waits = Waits.PREPARING
    added_at: str = ""
    #: When it reached the step it waits at.
    since: str = ""

    @property
    def by_hand(self) -> bool:
        return self.given.get("entry") == "sliced_file"

    def line(self) -> str:
        since = f", since {self.since[:16].replace('T', ' ')} UTC" if self.since else ""
        return f"job {self.job_id}  {self.part}  {self.waits}{since}"

    def state(self) -> dict[str, Any]:
        """The given state as the graph takes it."""
        given = dict(self.given)
        if given.get("sliced") is not None:
            given["sliced"] = SlicedJob.model_validate(given["sliced"])
        return given


_SCHEMA = """
CREATE TABLE IF NOT EXISTS waiting_jobs (
    job_id   TEXT PRIMARY KEY,
    part     TEXT NOT NULL,
    given    TEXT NOT NULL,
    profiles TEXT,
    waits    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    since    TEXT NOT NULL
)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class WaitingJobs:
    """The jobs folder's waiting jobs. Each call opens the file and closes it, so a second process
    (`print_job.py --list`) reads it while a job runs."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute(_SCHEMA)
                yield conn

    def add(self, job: WaitingJob) -> None:
        given = json.dumps(_plain(job.given))
        profiles = job.profiles.model_dump_json() if job.profiles is not None else None
        at = job.added_at or _now()
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO waiting_jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job.job_id, job.part, given, profiles, job.waits.value, at, job.since or at),
            )

    def at(self, job_id: str, waits: Waits) -> None:
        """The job waits at `waits` now. A job that isn't waiting is left out."""
        with self._db() as db:
            db.execute("UPDATE waiting_jobs SET waits = ?, since = ? WHERE job_id = ? "
                       "AND waits != ?", (waits.value, _now(), job_id, waits.value))

    def remove(self, job_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM waiting_jobs WHERE job_id = ?", (job_id,))

    def get(self, job_id: str) -> WaitingJob | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM waiting_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job(row) if row is not None else None

    def all(self) -> list[WaitingJob]:
        """Every waiting job, the oldest first."""
        try:
            with self._db() as db:
                rows = db.execute("SELECT * FROM waiting_jobs ORDER BY added_at, job_id").fetchall()
        except sqlite3.Error as exc:
            logger.warning("the waiting jobs couldn't be read: %s", exc)
            return []
        return [_job(r) for r in rows]


def _job(row: sqlite3.Row) -> WaitingJob:
    return WaitingJob(
        job_id=row["job_id"], part=row["part"], given=json.loads(row["given"]),
        profiles=(SlicerProfiles.model_validate_json(row["profiles"])
                  if row["profiles"] else None),
        waits=Waits(row["waits"]), added_at=row["added_at"], since=row["since"],
    )


def _plain(value: Any) -> Any:
    """`value` as JSON holds it: a model by its fields."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


# --------------------------------------------------------------------------
# What the person and the model gave one job
# --------------------------------------------------------------------------


class _Answered(BaseModel):
    question: Question
    answer: Answer


class _Reviewed(BaseModel):
    """An answer at the review, less the offer as it was left, which is shown, not used."""

    approved: bool = False
    corrections: tuple[Change, ...] = ()
    taken: tuple[Change, ...] = ()
    turn_taken: bool = False


class _Record(BaseModel):
    answers: list[_Answered] = []
    intake: Intake | None = None
    loop: Any = None
    proposals: dict[str, Proposal] = {}
    reviews: list[_Reviewed] = []


_LOOP = TypeAdapter(LoopResult)


def _key(question: Question) -> tuple[str, str, tuple[str, ...]]:
    """A question as it's matched again: its words, feature and options, not their prices."""
    return question.text, question.feature, tuple(o.label for o in question.options)


class Kept:
    """What the person and the model have given one job, in `folder/kept.json`."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / KEPT_NAME
        self._lock = threading.Lock()
        self._record = self._read()
        #: How many kept answers at the review have been given back in this run.
        self._replayed = 0

    def _read(self) -> _Record:
        try:
            return _Record.model_validate_json(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _Record()
        except (OSError, ValueError) as exc:
            # Not a reason to stop a job: it's asked again, as if nothing had been kept.
            logger.warning("what job %s kept couldn't be read (%s): it's asked again",
                           self.path.parent.name, exc)
            return _Record()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(".part")
        part.write_text(self._record.model_dump_json(), encoding="utf-8")
        os.replace(part, self.path)

    @property
    def empty(self) -> bool:
        record = self._record
        return not (record.answers or record.intake or record.loop or record.proposals
                    or record.reviews)

    def asker(self, asker: Asker) -> Asker:
        """`asker`, with a set of questions already answered given back unasked, and each set's
        answers kept as they're given."""

        def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
            with self._lock:
                known = {_key(a.question): a.answer for a in self._record.answers}
            given = [known.get(_key(q)) for q in questions]
            if questions and all(a is not None for a in given):
                logger.info("the answers given before the restart are kept: %d of them",
                            len(given))
                return tuple(a for a in given if a is not None)
            answers = tuple(asker(questions))
            with self._lock:
                self._record.answers += [_Answered(question=q, answer=a)
                                         for q, a in zip(questions, answers, strict=True)]
                self._write()
            return answers

        return ask

    def intake(self, ask: Callable[[], Intake]) -> Intake:
        """The questions and their answers, kept once they're all answered."""
        if self._record.intake is not None:
            return self._record.intake
        intake = ask()
        with self._lock:
            self._record.intake = intake
            self._write()
        return intake

    def loop(self, run: Callable[[], LoopResult]) -> LoopResult:
        """The print loop's result, kept: a restart doesn't ask the model again."""
        if self._record.loop is not None:
            try:
                return _LOOP.validate_python(self._record.loop)
            except ValueError as exc:
                logger.warning("the print loop's kept result couldn't be read: %s", exc)
        found = run()
        with self._lock:
            self._record.loop = _LOOP.dump_python(found, mode="json")
            self._write()
        return found

    def proposal(self, pose: str, make: Callable[[], Proposal]) -> Proposal:
        """The settings proposed for the part in `pose`, kept."""
        if pose in self._record.proposals:
            return self._record.proposals[pose]
        made = make()
        with self._lock:
            self._record.proposals[pose] = made
            self._write()
        return made

    def reviewer(self, reviewer: Reviewer) -> Reviewer:
        """`reviewer`, with the answers given at the review before a restart given back in
        order, and each new one kept. A stop isn't kept: it ends the job."""

        def review(request: ReviewRequest) -> Review:
            with self._lock:
                if self._replayed < len(self._record.reviews):
                    kept = self._record.reviews[self._replayed]
                    self._replayed += 1
                    logger.info("the review's answer from before the restart is kept: %s",
                                _said(kept))
                    return Review(approved=kept.approved, corrections=kept.corrections,
                                  taken=kept.taken, turn_taken=kept.turn_taken)
            answer = reviewer(request)
            if answer.stop or (answer.browsing and not answer.turn_taken):
                return answer  # an offer only browsed slices nothing, and isn't kept
            with self._lock:
                self._record.reviews.append(_Reviewed(
                    approved=answer.approved, corrections=answer.corrections,
                    taken=answer.taken, turn_taken=answer.turn_taken))
                self._replayed += 1
                self._write()
            return answer

        return review


def _said(kept: _Reviewed) -> str:
    if kept.approved:
        return "ok"
    changes = [*kept.corrections, *kept.taken]
    if changes:
        return ", ".join(f"{c.key}={c.value}" for c in changes)
    return "the turn taken" if kept.turn_taken else "no change"
