"""A job that waits for the person outlives the process that prepared it (mqucifer/print-shop#209).

Two things are kept, both on disk beside the job, so a closed terminal or a restart is never a run
from the start:

- **Which jobs wait, and where** (`WaitingJobs`): a table in the jobs folder's `jobs.sqlite`,
  beside the graph's checkpoints. A job is added when it's submitted, with what it was given and
  a copy of the profiles it slices with, and goes when it ends, however it ends. Only a job in
  it is ever taken up again, so the checkpoints of jobs from before it are left alone.
- **What the person and the model have given a job** (`Kept`): `kept.json` in its folder. The
  questions' answers as each set is answered, the questions and answers whole once they're done,
  the print loop's result, each proposal, what the model proposed beside the review once it
  came, and each answer at the review. The slice step is one
  node of the graph, checkpointed only when it ends, so a restart runs it again from its start:
  what's kept is given back in place of asking or calling again, and the person is back at the
  step they left, with what they'd answered and corrected, and the same slice.

**They're the print queue too** (mqucifer/print-shop#210, `print_queue.py`): each job's place,
whether it's held, the spool it's for and, for one of several copies, its first. A job moved to
another spool keeps its answers and the person's changes (`Kept.move`), less the review's own
`ok`, and a temperature or fan they set for another variety is kept to offer back, unticked.

**A job can be sent to the queue from elsewhere** (mqucifer/print-shop#226, `sending.py`): it
waits `SENT` until a queue is open to prepare it, with who sent it and their note (`Sent`), and
what the sender suggested for its settings is kept here (`Kept.suggested`), offered at the review
unticked and never applied unless the person uses it.

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

from print_shop.inventory import Material
from print_shop.setup.bounds import MATERIAL
from print_shop.setup.intake import Answer, Asker, Intake, Question
from print_shop.setup.loop import LoopResult
from print_shop.setup.names import setting_said
from print_shop.setup.proposal import Change, Ending, Proposal, Suggested
from print_shop.setup.review import Review, Reviewer, ReviewRequest
from print_shop.sliced import SlicedJob
from print_shop.slicer.orca import SlicerProfiles

__all__ = ["KEPT_NAME", "Kept", "Moved", "Sent", "Suggestion", "WaitingJob", "WaitingJobs",
           "Waits", "local_when"]

logger = logging.getLogger(__name__)

#: The file in a job's folder that keeps what the person and the model gave it.
KEPT_NAME = "kept.json"


class Waits(StrEnum):
    """Where a waiting job is, as it's said."""

    PREPARING = "being prepared"
    #: A copy, waiting for its first to be prepared: it prints what that one's review settled
    #: (mqucifer/print-shop#210).
    WITH_FIRST = "prepared with its first copy"
    QUESTIONS = "at the questions"
    REVIEW = "at the review"
    MESH = "at the mesh gate"
    READY = "ready to print"
    #: Its preparing stopped, saying why (`WaitingJob.note`): it's retried or removed by the
    #: person (mqucifer/print-shop#210).
    STOPPED = "stopped"
    #: Sent from elsewhere, and not yet taken up by a queue: it's prepared once the queue is open
    #: (mqucifer/print-shop#226).
    SENT = "to be prepared once the queue is open"
    #: A file sliced by hand that someone else sent, waiting for the person to look at what it
    #: prints before it's ready (mqucifer/print-shop#226).
    LOOK = "at its look"


def local_when(at: str, now: datetime | None = None) -> str:
    """When, as the Mac's clock says it: "04:43 today", "22:43 yesterday", or "21 Nov 22:43"
    beyond that, with the year where it isn't this one (mqucifer/print-shop#226)."""
    try:
        when = datetime.fromisoformat(at).astimezone()
    except ValueError:
        return at
    today = (now or datetime.now(UTC)).astimezone().date()
    clock = f"{when:%H:%M}"
    days = (today - when.date()).days
    if days == 0:
        return f"{clock} today"
    if days == 1:
        return f"{clock} yesterday"
    year = f" {when.year}" if when.year != today.year else ""
    return f"{when.day} {when:%b}{year} {clock}"


class Sent(BaseModel):
    """Where a job came from (mqucifer/print-shop#226): who sent it, when, their note, the bytes
    sent, and what came with it, each in words. `by` is "" for a file added at the queue."""

    by: str = ""
    note: str = ""
    at: str = ""
    #: The files sent together, for the person to see as one send.
    batch: str = ""
    #: The sha256 of the file's bytes, as sent: the same bytes sent again are said.
    sha256: str = ""
    #: Where the original was, when it was sent: the queue prints its own copy.
    source: str = ""
    #: What came with it, and what wasn't taken and why, each a line.
    came: tuple[str, ...] = ()

    def said(self) -> str:
        """"sent by Claude, 14:02 today: 'the lid set'", in the Mac's own time."""
        note = f": '{self.note}'" if self.note else ""
        return f"sent by {self.by}, {local_when(self.at)}{note}"


class Suggestion(BaseModel):
    """A setting the sender suggested, with why: offered at the review as theirs, unticked, and
    never applied unless the person uses it (mqucifer/print-shop#226)."""

    model_config = {"frozen": True}

    key: str
    value: str
    reason: str
    by: str


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
    #: Its place in the queue, the lowest first (mqucifer/print-shop#210). 0 puts it last.
    position: float = 0.0
    #: Held by the person: it's prepared, and never offered to print until it's let go.
    held: bool = False
    #: The spool it's prepared for, loaded or on the shelf. None where there's no stock.
    spool_id: int | None = None
    #: A copy of another job, which it's prepared with: that job's id, its number and how many.
    copy_of: str = ""
    copy_n: int = 1
    copies: int = 1
    #: Why its preparing stopped, where it did.
    note: str = ""
    #: Who sent it and what came with it (mqucifer/print-shop#226). None: from before.
    sent: Sent | None = None

    @property
    def by_hand(self) -> bool:
        return self.given.get("entry") == "sliced_file"

    @property
    def copied(self) -> str:
        """"2 of 3" for one of several copies, or ""."""
        return f"{self.copy_n} of {self.copies}" if self.copies > 1 else ""

    def line(self) -> str:
        since = f", since {local_when(self.since)}" if self.since else ""
        copied = f" ({self.copied})" if self.copied else ""
        held = ", held" if self.held else ""
        sent = f"  ({self.sent.said()})" if self.sent is not None and self.sent.by else ""
        return f"job {self.job_id}  {self.part}{copied}  {self.waits}{held}{since}{sent}"

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

#: The queue's own columns (mqucifer/print-shop#210), added to a table from before them.
_QUEUE_COLUMNS = (
    ("position", "REAL NOT NULL DEFAULT 0"),
    ("held", "INTEGER NOT NULL DEFAULT 0"),
    ("spool_id", "INTEGER"),
    ("copy_of", "TEXT NOT NULL DEFAULT ''"),
    ("copy_n", "INTEGER NOT NULL DEFAULT 1"),
    ("copies", "INTEGER NOT NULL DEFAULT 1"),
    ("note", "TEXT NOT NULL DEFAULT ''"),
    #: Who sent it and what came with it, as JSON (mqucifer/print-shop#226).
    ("sent", "TEXT"),
)


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
                have = {row["name"] for row in conn.execute("PRAGMA table_info(waiting_jobs)")}
                for name, kind in _QUEUE_COLUMNS:
                    if name not in have:
                        conn.execute(f"ALTER TABLE waiting_jobs ADD COLUMN {name} {kind}")
                yield conn

    def add(self, job: WaitingJob) -> None:
        """The job waits, last in the queue unless it has a place, or where it was if it's
        there already (its place, its hold and its copies are kept)."""
        given = json.dumps(_plain(job.given))
        profiles = job.profiles.model_dump_json() if job.profiles is not None else None
        at = job.added_at or _now()
        with self._db() as db:
            was = db.execute("SELECT position, held FROM waiting_jobs WHERE job_id = ?",
                             (job.job_id,)).fetchone()
            position, held = job.position, job.held
            if was is not None and not position:
                position, held = was["position"], bool(was["held"]) or held
            if not position:
                last = db.execute("SELECT MAX(position) FROM waiting_jobs").fetchone()[0]
                position = (last or 0) + 1
            db.execute(
                "INSERT OR REPLACE INTO waiting_jobs (job_id, part, given, profiles, waits, "
                "added_at, since, position, held, spool_id, copy_of, copy_n, copies, note, sent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job.job_id, job.part, given, profiles, job.waits.value, at, job.since or at,
                 position, int(held), job.spool_id, job.copy_of, job.copy_n, job.copies, job.note,
                 job.sent.model_dump_json() if job.sent is not None else None),
            )

    def order(self, job_ids: Sequence[str]) -> None:
        """The queue's order: these first, in this order, then the rest as they were."""
        with self._db() as db:
            rest = [row["job_id"] for row in db.execute(
                "SELECT job_id FROM waiting_jobs ORDER BY position, added_at, job_id")
                if row["job_id"] not in job_ids]
            for n, job_id in enumerate([*job_ids, *rest], 1):
                db.execute("UPDATE waiting_jobs SET position = ? WHERE job_id = ?", (n, job_id))

    def hold(self, job_id: str, held: bool) -> None:
        with self._db() as db:
            db.execute("UPDATE waiting_jobs SET held = ? WHERE job_id = ?", (int(held), job_id))

    def at(self, job_id: str, waits: Waits, note: str = "") -> None:
        """The job waits at `waits` now, with why where it stopped. A job that isn't waiting is
        left out."""
        with self._db() as db:
            db.execute("UPDATE waiting_jobs SET waits = ?, since = ?, note = ? WHERE job_id = ? "
                       "AND (waits != ? OR note != ?)",
                       (waits.value, _now(), note, job_id, waits.value, note))

    def remove(self, job_id: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM waiting_jobs WHERE job_id = ?", (job_id,))

    def get(self, job_id: str) -> WaitingJob | None:
        return next((j for j in self.all() if j.job_id == job_id), None)

    def all(self) -> list[WaitingJob]:
        """Every waiting job that can be read, in the queue's order: the oldest first, unless
        the person ordered them (mqucifer/print-shop#210). Reading creates nothing, and a row
        that can't be read is said and left out, never in another job's way."""
        if not self.path.is_file():
            return []
        try:
            with self._db() as db:
                rows = db.execute("SELECT * FROM waiting_jobs "
                                  "ORDER BY position, added_at, job_id").fetchall()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("the waiting jobs couldn't be read: %s", exc)
            return []
        found = []
        for row in rows:
            try:
                found.append(_job(row))
            except ValueError as exc:  # a pydantic ValidationError is one
                logger.warning("waiting job %s couldn't be read, so it's left out: %s",
                               row["job_id"], exc)
        return found


def _job(row: sqlite3.Row) -> WaitingJob:
    return WaitingJob(
        job_id=row["job_id"], part=row["part"], given=json.loads(row["given"]),
        profiles=(SlicerProfiles.model_validate_json(row["profiles"])
                  if row["profiles"] else None),
        waits=Waits(row["waits"]), added_at=row["added_at"], since=row["since"],
        position=row["position"], held=bool(row["held"]), spool_id=row["spool_id"],
        copy_of=row["copy_of"], copy_n=row["copy_n"], copies=row["copies"], note=row["note"],
        sent=Sent.model_validate_json(row["sent"]) if row["sent"] else None,
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
    """An answer at the review, less the offer as it was left: what it took from the offer is in
    `taken`, and which print it showed is shown again as the offer is built."""

    approved: bool = False
    corrections: tuple[Change, ...] = ()
    taken: tuple[Change, ...] = ()
    declined: tuple[str, ...] = ()
    saw: Suggested | None = None
    turn_taken: bool = False


class Moved(BaseModel):
    """A temperature or fan the person set for one variety, before the job was moved to another
    (mqucifer/print-shop#210): offered back at the review, unticked."""

    change: Change
    #: The variety it was set for.
    variety: str


class _Record(BaseModel):
    answers: list[_Answered] = []
    intake: Intake | None = None
    loop: Any = None
    proposals: dict[str, Proposal] = {}
    #: What the model proposed beside the review, by what it was for (mqucifer/print-shop#242).
    suggested: dict[str, Suggested] = {}
    reviews: list[_Reviewed] = []
    #: What the person set for another variety than the job's now (mqucifer/print-shop#210).
    moved: list[Moved] = []
    #: What the sender suggested for its settings (mqucifer/print-shop#226): offered, never
    #: given back as the person's.
    sender_suggested: list[Suggestion] = []
    #: The person looked at what a sent hand-sliced file prints, and let it be ready
    #: (mqucifer/print-shop#226).
    looked: bool = False


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
                    or record.suggested or record.reviews)

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

    def suggested(self, key: str, find: Callable[[], Suggested]) -> Suggested:
        """What the model proposed beside the review for `key` (a pose's settings, or the print
        loop's), kept once it came: a restart shows it at once, and doesn't call again. One that
        failed or was gone on without is asked again."""
        with self._lock:
            kept = self._record.suggested.get(key)
        if kept is not None:
            return kept
        found = find()
        if found.ending is Ending.CAME:
            with self._lock:
                self._record.suggested[key] = found
                self._write()
        return found

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
                                  taken=kept.taken, declined=kept.declined, saw=kept.saw,
                                  turn_taken=kept.turn_taken)
            answer = reviewer(request)
            if answer.stop or (answer.browsing and not answer.turn_taken):
                return answer  # an offer only browsed slices nothing, and isn't kept
            with self._lock:
                self._record.reviews.append(_Reviewed(
                    approved=answer.approved, corrections=answer.corrections,
                    taken=answer.taken, declined=answer.declined, saw=answer.saw,
                    turn_taken=answer.turn_taken))
                self._replayed += 1
                self._write()
            return answer

        return review


    # -- sent with suggestions (mqucifer/print-shop#226) ----------------------

    def suggested_by_sender(self) -> list[Suggestion]:
        """What the sender suggested for the job's settings, to offer unticked."""
        with self._lock:
            return list(self._record.sender_suggested)

    def suggest(self, suggestions: Sequence[Suggestion]) -> None:
        """The sender's suggestions, kept to offer: none of them is an answer or a change."""
        with self._lock:
            self._record.sender_suggested = list(suggestions)
            self._write()

    def looked(self) -> bool:
        """Whether the person has looked at what this sent hand-sliced file prints."""
        with self._lock:
            return self._record.looked

    def look(self) -> None:
        """The person looked at what it prints, and let it be ready: kept, so a restart doesn't
        ask again. It's no approval: Start is still theirs."""
        with self._lock:
            self._record.looked = True
            self._write()

    # -- moved to another spool (mqucifer/print-shop#210) --------------------

    def moved(self) -> list[Moved]:
        """What the person set for another variety, to offer back unticked."""
        with self._lock:
            return list(self._record.moved)

    def move(self, was: Material | None, now: Material | None) -> list[str]:
        """Kept for preparing again on another spool: the answers, and every change the person
        made at the review, with the review's own `ok` taken back, so it comes back to them.

        Moved to another variety, a temperature or fan they set (`bounds.MATERIAL`) is taken out
        of what's given back and kept to offer, unticked: it was set for the variety they left.
        One set for the variety moved to, on an earlier move, is given back again. On another
        filament (another variety or tuning) what the model proposed is asked again, for it.
        Returns what was done, in words."""
        said: list[str] = []
        other_variety = (was is not None and now is not None
                         and was.variety.casefold() != now.variety.casefold())
        other_filament = (was is not None and now is not None and not was.same_filament(now))
        with self._lock:
            record = self._record
            reviews: list[_Reviewed] = []
            moved = list(record.moved)
            if now is not None:
                back = [m.change for m in moved if m.variety.casefold() == now.variety.casefold()]
                moved = [m for m in moved if m.variety.casefold() != now.variety.casefold()]
                if back:
                    reviews.append(_Reviewed(corrections=tuple(back)))
                    said.append("given back, set for " + now.variety + ": "
                                + ", ".join(setting_said(c.key, c.value) for c in back))
            for kept in record.reviews:
                if kept.approved:
                    continue  # the review's ok: it comes back to the person
                corrections, taken = list(kept.corrections), list(kept.taken)
                if other_variety:
                    assert was is not None
                    out = [c for c in (*corrections, *taken)
                           if c.key in MATERIAL and not c.said_no_to]
                    moved += [Moved(change=c, variety=was.variety) for c in out]
                    corrections = [c for c in corrections if c not in out]
                    taken = [c for c in taken if c not in out]
                if corrections or taken or kept.turn_taken:
                    reviews.append(kept.model_copy(update={
                        "approved": False, "corrections": tuple(corrections),
                        "taken": tuple(taken)}))
            unticked = [m for m in moved if m not in record.moved]
            if unticked:
                said.append(f"set for {unticked[0].variety}, offered back unticked: "
                            + ", ".join(setting_said(m.change.key, m.change.value)
                                        for m in unticked))
            record.reviews, record.moved = reviews, moved
            if other_filament:
                record.proposals, record.suggested, record.loop = {}, {}, None
                said.append("what the model proposed was for the other filament, so it's asked "
                            "again")
            self._replayed = 0
            self._write()
        return said

    def copy_to(self, folder: Path) -> None:
        """What's kept here, kept for another job too: a copy of this one."""
        with self._lock:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / KEPT_NAME).write_text(self._record.model_dump_json(), encoding="utf-8")


def _said(kept: _Reviewed) -> str:
    if kept.approved:
        return "ok"
    changes = [*kept.corrections, *kept.taken]
    if changes:
        return ", ".join(setting_said(c.key, c.value) for c in changes)
    return "the turn taken" if kept.turn_taken else "no change"
