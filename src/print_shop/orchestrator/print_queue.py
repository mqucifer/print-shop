"""Many files queue, and preparing runs apart from printing (mqucifer/print-shop#210).

One command was one job, from the file to the outcome: each file waited for the last print and its
outcome before its questions could even begin. The printer is single; preparing for it needn't be.

- **Several files are added in one go** (`PrintQueue.add`), parts and hand-sliced files alike,
  each with a quantity and the spool it's for, the loaded one by default. **That spool needn't be
  loaded to prepare:** the job slices with its variety and tuning (`Inventory.for_spool`), and
  Start holds until it's loaded (`graph.why_not_start`).
- **Preparing runs apart from printing.** Each job prepares on a thread of its own, up to its gate
  before printing, while a print runs. How many do the machine's work at once (Orca, the model)
  is `at_once`. A job waiting for the person holds no place: its place goes to the next.
- **The person answers in any order** (`needs`, `give`), and one answer can be given to several
  files at once (`give_alike`): a question another file asks alike is answered for it, now or when
  it comes, and only what's left is asked. As for a lone job (mqucifer/print-shop#208),
  print-shop's two are asked at once and the model's when they come, and `go` goes on without
  them (`_waiting`).
- **The queue can be read and changed** (`lines`, `order`, `first`, `together`, `hold`, `remove`):
  each job's step and spool, what needs the person, and the jobs for one spool taken together.
- **A waiting job's spool can be changed, colour or material** (`move`). Another spool of the same
  variety and tuning moves at once, its slice and review standing, and the job names it (the
  Sponsor, 2026-11-24). Another variety or tuning is prepared again with the person's answers and
  changes kept (`Kept.move`) and comes back to their review, and a temperature or fan they set
  for another variety comes back unticked, in the review's offer.
- **A quantity is that many prints,** each its own job, with its own ledger row and outcome. The
  copies wait for the first to be prepared, and then print what its review settled: its G-code,
  under each copy's own name.
- **Printing stays one at a time, started by the person.** When nothing prints, the first ready
  job that isn't held is offered: its gate opens, on the card and here, saying the spool it
  needs (`Daemon.resume`). An outcome not yet recorded doesn't keep it back: the card still asks
  for it. Nothing here starts a print, approves one or arms the printer: the jobs prepare with no
  printer at all, and only a Start at the gate, the daemon's, can reach it.
- **The machine's steps keep their limit** (mqucifer/print-shop#209): a job whose preparing runs
  past the slice's hour from when it last came back from the person stops, saying so, and is
  retried or removed (`retry`). Each job keeps its own `job.log`, as a lone job does.
- **Files can be sent to it from elsewhere** (mqucifer/print-shop#226, `sending.py`): by
  Claude or the person, with who sent them and a note, while it's open or not. A sent job
  waits `SENT` until a queue is open, which takes it up within a second, and "Sent to you"
  (`sent`) says who sent what, when, the note, and what each needs next. A sent file sliced by
  hand waits for the person to look at what it prints (`Need.LOOK`) before it's ready, so no
  Start's bed question is the only screen it has. Every file added, sent
  or typed at `add`, is the queue's own copy, with what belongs to it.
- **The queue is the same after a restart:** it's the jobs folder's waiting jobs (`WaitingJobs`),
  in order, held or not, with their spools and copies, each taken up where it was.
- **A finished run is added again** (`sending.Adding.again`, mqucifer/print-shop#216): its
  kept file, or a change's new slice (`reprint`), read as a hand-sliced file is and linked to
  that run. A copy is given its first's kept part and profiles, so each copy's run can print
  again too.

**Built so another screen can drive it:** every action is a method here that returns what
happened in words, and what needs the person is read as data (`Asked`), never a terminal prompt
(mqucifer/print-shop#139). `scripts/print_job.py --queue` is one driver of it. It's for the
hardware in use now: one printer, with one loaded spool at a time.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from langgraph.types import Command as LgCommand

from print_shop.core.contracts import Gate
from print_shop.expert.client import LLMConfig
from print_shop.inventory import Inventory, InventoryError, Material, Spool
from print_shop.joblog import JobLog, JobLogs
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.graph import build_graph
from print_shop.orchestrator.kept import Kept, Sent, WaitingJob, WaitingJobs, Waits
from print_shop.orchestrator.sending import Adding, material_for, queue_one
from print_shop.reprint import copy_kept
from print_shop.setup import Proposer
from print_shop.setup.intake import Answer, Question
from print_shop.setup.loop import PrintLoop
from print_shop.setup.questions import Asked as QuestionsAsked
from print_shop.setup.review import Review, Reviewer, ReviewRequest
from print_shop.setup.waiting import QuestionsCall, Waiting, took
from print_shop.sliced import SlicedJob
from print_shop.slicer.arrange import SlicePlate
from print_shop.slicer.naming import job_id_from_filename
from print_shop.slicer.orca import Slicer, SlicerProfiles, slice_model, slice_plate
from print_shop.slicer.store import Store

__all__ = ["Adding", "Asked", "Gone", "Line", "Need", "Preparing", "PrintQueue", "SentTo",
           "alike"]

logger = logging.getLogger(__name__)

#: What the machine's work may do at once, by default: Orca and the model host on one Mac.
AT_ONCE = 2
#: After a Start: the upload and the start, as `print_job.py` waits for them.
FINISH_WAIT_S = 900.0
#: The machine's steps a job's preparing may take, from when it last came back from the person: the
#: slice's hour, as a lone job's (`print_job.APPROVAL_WAIT_S`, mqucifer/print-shop#209).
SLICE_LIMIT_S = 3600.0


class Need(StrEnum):
    """What a job waits on the person for."""

    QUESTIONS = "the questions"
    #: The model's closer choices on what it's for, beside the answer given
    #: (mqucifer/print-shop#208).
    CLOSER = "the model's closer choices"
    REVIEW = "the review"
    MESH = "the mesh gate"
    #: A file sliced by hand that someone else sent: what it prints, looked at once before it's
    #: ready (mqucifer/print-shop#226).
    LOOK = "look at what it prints"


class Gone(Exception):  # noqa: N818 - a signal, not an error
    """The job was moved, removed or put by while it waited for the person: its preparing stops
    there, and what was kept stays kept."""


def alike(question: Question) -> tuple[str, str, tuple[str, ...]]:
    """A question as another file's is matched to it: its words, feature and options, never
    their prices, which are each part's own (as `Kept` matches one again)."""
    return question.text, question.feature, tuple(o.label for o in question.options)


@dataclass
class Asked:
    """One thing a job waits on the person for, answered once: its questions (those not answered
    alike for it), its review, or its mesh gate. Read as data by whichever screen answers it."""

    job_id: str
    need: Need
    questions: tuple[Question, ...] = ()
    review: ReviewRequest | None = None
    gate: dict[str, Any] | None = None
    #: For a look: what `print_job.py --sliced` shows of the file, a line each.
    shown: tuple[str, ...] = ()
    #: For the closer choices: the answer given, which None keeps, and how long the model took.
    kept: Answer | None = None
    seconds: float = 0.0
    since: float = field(default_factory=time.time)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)
    _answer: Any = None
    _gone: bool = False

    @property
    def open(self) -> bool:
        return not self._done.is_set()

    def _give(self, answer: Any) -> bool:
        if self._done.is_set():
            return False
        self._answer = answer
        self._done.set()
        return True

    def _cancel(self) -> None:
        self._gone = True
        self._done.set()

    def _wait(self) -> Any:
        self._done.wait()
        if self._gone:
            raise Gone(self.job_id)
        return self._answer


@dataclass(frozen=True)
class Line:
    """A job as the queue shows it, in its order."""

    number: int
    job: WaitingJob
    #: Where it is, in words: "at the questions", "ready to print", "offered to print".
    step: str
    needs: Need | None
    offered: bool
    #: The spool it's for, as the stock names it now, and whether that's the one loaded.
    spool: str
    loaded: bool

    def said(self) -> str:
        copied = f" ({self.job.copied})" if self.job.copied else ""
        held = ", HELD" if self.job.held else ""
        spool = f"; {self.spool}{'' if self.loaded or not self.spool else ', on the shelf'}"
        return (f"{self.number:>2}. {self.job.part}{copied}  {self.step}{held}{spool}"
                f"  [{self.job.job_id[:8]}]")


@dataclass(frozen=True)
class SentTo:
    """One send, as "Sent to you" shows it (mqucifer/print-shop#226): who, when, the note, and
    its jobs still in the queue, each with what it needs next."""

    sent: Sent
    lines: tuple[Line, ...]

    def said(self) -> str:
        """The send in a line, with its jobs' numbers: each job's step is on its own line in
        the queue, and `sent N` has the rest (print-shop-ux, 2026-11-23)."""
        said = self.sent.said()
        numbers = [str(line.number) for line in self.lines]
        jobs = f"job {numbers[0]}" if len(numbers) == 1 else f"jobs {', '.join(numbers)}"
        return f"{said[0].upper()}{said[1:]}: {jobs}"


@dataclass(frozen=True)
class Preparing:
    """What the queue's jobs are prepared with: the same as one job's (`build_graph`), less the
    printer, which nothing that prepares is ever given."""

    llm: LLMConfig | None = None
    slicer: Slicer = slice_model
    plate_slicer: SlicePlate = slice_plate
    proposer: Proposer | None = None
    loop: PrintLoop | None = None
    store: Store | None = None


@dataclass
class _Run:
    """A job's preparing, in this process: its thread, and which preparing it is."""

    generation: int = 0
    thread: threading.Thread | None = None
    asked: Asked | None = None
    #: The model's questions call while it runs (mqucifer/print-shop#208), and `go` on it.
    call: QuestionsCall | None = None
    go: threading.Event = field(default_factory=threading.Event)
    #: When it last came back from the person: the machine's steps count from here.
    back_at: float = field(default_factory=time.monotonic)


def _on_spool(values: dict[str, Any], now: Material | None) -> dict[str, Any]:
    """The state's update for a job prepared for one spool to start on `now`, another of the same
    variety and tuning; {} where it's that spool already, or another filament."""
    sliced = values.get("sliced")
    used = values.get("profiles_used")
    had = sliced.material if sliced is not None else (used.material if used is not None else None)
    if had is None or now is None or had.spool_id == now.spool_id or not had.same_filament(now):
        return {}
    if sliced is not None:
        return {"sliced": sliced.model_copy(update={"material": now})}
    assert used is not None
    return {"profiles_used": used.model_copy(update={"material": now})}


def _said_limit(seconds: float) -> str:
    return f"{seconds / 3600:g} h" if seconds >= 3600 else took(seconds)


def _never_asked(question: str) -> str:
    raise RuntimeError("the queue's print loop proposes at the review; it never asks the person "
                       f"in a prompt of its own ({question[:60]!r})")


class PrintQueue:
    """The queue of files to print: added in one go, prepared apart from printing, answered in
    any order, and printed one at a time, each started by the person.

    `daemon` is the printer's side, entered by the caller: one gate at a time, the Start, the
    print and the watching (`Daemon`). `profiles_for` composes the profiles a job slices with for
    the spool it's for into its own folder (None where the stock isn't set up). `sliced_for`
    reads a hand-sliced file for a spool, refusing (ValueError) one that names another filament.
    """

    def __init__(
        self,
        *,
        daemon: Daemon,
        workdir: Path,
        ledger: Ledger | None,
        profiles_for: Callable[[Material | None, Path], SlicerProfiles],
        sliced_for: Callable[[Path, Material | None], SlicedJob] | None = None,
        preparing: Preparing | None = None,
        at_once: int = AT_ONCE,
        finish_wait_s: float = FINISH_WAIT_S,
        slice_limit_s: float = SLICE_LIMIT_S,
        logs: JobLogs | None = None,
    ) -> None:
        self._daemon = daemon
        self._workdir = workdir
        self._ledger = ledger
        self._profiles_for = profiles_for
        self._sliced_for = sliced_for
        self._preparing = preparing or Preparing()
        self._finish_wait_s = finish_wait_s
        self._slice_limit_s = slice_limit_s
        #: Each job's own job.log, as a lone job keeps one. None keeps none.
        self._logs = logs
        self._jobs = WaitingJobs(workdir / "jobs.sqlite")
        #: The machine's work at once. A job waiting for the person gives its place up.
        self._slot = threading.Semaphore(max(1, at_once))
        self._lock = threading.RLock()
        self._runs: dict[str, _Run] = {}
        #: How often each job's been offered, and the jobs followed now: one follower a job, so
        #: a job offered again (after a move, say) has nothing said twice (mqucifer/print-shop#305).
        self._offers: dict[str, int] = {}
        self._following: set[str] = set()
        #: Answers given alike for a job, by its question (`give_alike`).
        self._ahead: dict[str, dict[tuple[str, str, tuple[str, ...]], Answer]] = {}
        #: What happened, in order, for a screen to say: started, held, ended, offered.
        self._said: list[str] = []
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._offerer: threading.Thread | None = None
        #: Jobs being moved or removed: none of them is offered meanwhile.
        self._changing: set[str] = set()

    # -- lifecycle ------------------------------------------------------------

    def __enter__(self) -> PrintQueue:
        """Take the queue up where it was: each job not ready is prepared on from its
        checkpoint, a ready one waits to be offered, and nothing is offered while a print runs."""
        for job in self._jobs.all():
            if job.waits not in (Waits.READY, Waits.WITH_FIRST, Waits.STOPPED):
                self._take_up(job.job_id)
        self._offerer = threading.Thread(target=self._offer_loop, name="queue-offer", daemon=True)
        self._offerer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        """Put by: what waits for the person stops asking, and every job stays where it was, to
        be taken up again."""
        self._closing.set()
        self._wake.set()
        with self._lock:
            runs = list(self._runs.values())
            for run in runs:
                if run.asked is not None:
                    run.asked._cancel()
        if self._offerer is not None:
            self._offerer.join(timeout=5)
        for run in runs:
            if run.thread is not None:
                run.thread.join(timeout=5)

    # -- reading --------------------------------------------------------------

    def jobs(self) -> list[WaitingJob]:
        return self._jobs.all()

    @property
    def stock_path(self) -> Path | None:
        """Where the stock is, for a load said at the queue (mqucifer/print-shop#305), or None
        where there's no stock."""
        return self._ledger.path if self._ledger is not None else None

    def lines(self) -> list[Line]:
        """Every job in the queue's order, with its step, what it needs and its spool."""
        jobs = self._jobs.all()
        spools: dict[int, Spool] = {}
        loaded: set[int] = set()
        if self._ledger is not None:
            try:
                with Inventory(self._ledger.path) as inventory:
                    spools = {s.id: s for s in inventory.spools(with_empty=True)}
                    loaded = {s.id for s in spools.values() if s.printer_id is not None}
            except Exception:  # noqa: BLE001 - the queue reads without the stock, saying less
                logger.warning("the stock couldn't be read for the queue", exc_info=True)
        lines = []
        with self._lock:
            asked = {job_id: run.asked for job_id, run in self._runs.items()
                     if run.asked is not None and run.asked.open}
        offered = self._offered_now()
        for n, job in enumerate(jobs, 1):
            need = asked[job.job_id].need if job.job_id in asked else None
            step = str(job.waits)
            reading = self.reading(job.job_id)
            if job.waits is Waits.STOPPED:
                step = f"STOPPED: {job.note}"
            elif need is not None:
                step = f"needs you: {need}"
            elif reading is not None:
                step = (f"the model's questions: still reading, {took(reading)} (`go {n}` goes "
                        "on without them)")
            elif job.job_id == offered:
                step = "offered to print: Start it here or on the card"
            spool = spools.get(job.spool_id) if job.spool_id is not None else None
            lines.append(Line(
                number=n, job=job, step=step, needs=need, offered=job.job_id == offered,
                spool=spool.named() if spool is not None else (
                    f"spool #{job.spool_id}" if job.spool_id is not None else ""),
                loaded=job.spool_id in loaded))
        return lines

    def sent(self) -> list[SentTo]:
        """"Sent to you": each send whose jobs are still in the queue, the latest first, with who
        sent it, when, the note, and what each job needs next (mqucifer/print-shop#226)."""
        found: dict[str, tuple[Sent, list[Line]]] = {}
        for line in self.lines():
            sent = line.job.sent
            if sent is None or not sent.by:
                continue
            found.setdefault(sent.batch or line.job.job_id, (sent, []))[1].append(line)
        sends = [SentTo(sent=sent, lines=tuple(lines)) for sent, lines in found.values()]
        return sorted(sends, key=lambda s: s.sent.at, reverse=True)

    def printing(self) -> list[str]:
        """The print running now, as the ledger has it, and those that ended with no outcome
        yet: each is still asked for on the card."""
        if self._ledger is None:
            return []
        said = [f"printing now: {row.part_said}, job {row.job_id[:8]}"
                for row in self._ledger.running()]
        said += [f"ended, its outcome not yet recorded (asked on the card): {row.part_said}, "
                 f"job {row.job_id[:8]}" for row in self._ledger.awaiting_answer()
                 if row.heads_job]
        return said

    def needs(self) -> list[Asked]:
        """What waits for the person, in the queue's order."""
        order = {job.job_id: n for n, job in enumerate(self._jobs.all())}
        with self._lock:
            found = [run.asked for run in self._runs.values()
                     if run.asked is not None and run.asked.open]
        return sorted(found, key=lambda a: order.get(a.job_id, len(order)))

    def asked(self, job_id: str) -> Asked | None:
        with self._lock:
            run = self._runs.get(job_id)
            return run.asked if run is not None and run.asked is not None and run.asked.open \
                else None

    def said(self, since: int = 0) -> list[str]:
        """What's happened since the `since`th thing said: offered, started, held, ended."""
        with self._lock:
            return self._said[since:]

    def find(self, named: str) -> WaitingJob | None:
        """A job by its number in the queue, or the start of its id."""
        jobs = self._jobs.all()
        if named.isdigit() and 1 <= int(named) <= len(jobs):
            return jobs[int(named) - 1]
        found = [j for j in jobs if j.job_id.startswith(named)] if len(named) >= 4 else []
        return found[0] if len(found) == 1 else None

    # -- adding ---------------------------------------------------------------

    def add(self, adding: Sequence[Adding]) -> tuple[list[str], list[str]]:
        """Add several files in one go. Returns the jobs added, the first copy of each, and what
        wasn't, each with why. Each starts preparing at once, as places allow."""
        added: list[str] = []
        refused: list[str] = []
        for one in adding:
            try:
                added.append(self._add(one))
            except (OSError, ValueError, InventoryError) as exc:
                refused.append(f"{one.path.name}: {exc}")
        return added, refused

    def _add(self, one: Adding) -> str:
        """The queue's own copy of the file and what belongs to it, as a sent file's
        (`sending.queue_one`), prepared at once."""
        queued = queue_one(
            one, workdir=self._workdir, ledger=self._ledger,
            material=self._material(one.spool_id), profiles_for=self._profiles_for,
            sliced_for=self._sliced_for, sent=Sent(at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                                    time.gmtime())))
        for line in queued.came:
            self._say(f"{queued.part}, job {queued.job_id[:8]}: {line}", queued.job_id)
        self._take_up(queued.job_id)
        return queued.job_id

    def _material(self, spool_id: int | None) -> Material | None:
        """The printer, its nozzle and the spool a job is for: the loaded one by default. None
        where the stock isn't set up."""
        return material_for(self._ledger.path if self._ledger is not None else None, spool_id)

    def _take_up(self, job_id: str) -> None:
        """Prepare a job this queue isn't preparing yet: one sent while it was open or shut, or
        one from before a restart. A job it prepares already is left to it."""
        with self._lock:
            if job_id in self._runs or self._closing.is_set():
                return
            job = self._jobs.get(job_id)
            if job is None:
                return
            if job.waits is Waits.SENT:
                self._jobs.at(job_id, Waits.PREPARING)
            self._prepare(job_id)

    def _sent_in(self) -> None:
        """Jobs sent to the queue since it was opened (mqucifer/print-shop#226), taken up."""
        for job in self._jobs.all():
            if job.waits is Waits.SENT:
                self._take_up(job.job_id)
                if job.sent is not None and job.sent.by:
                    self._say(f"Sent to you: {job.part}, job {job.job_id[:8]}, "
                              f"{job.sent.said()}.", job.job_id)

    # -- the person's answers -------------------------------------------------

    def give(self, job_id: str, answer: Any, *, to: Asked | None = None) -> str:
        """Answer what the job waits on the person for: its questions' answers in order, a
        `Review`, the mesh gate's `(approved, note)`, or for the model's closer choices an
        `Answer` from them, or None to keep the answer given. `to` is the ask the answer was
        made for (the phone's, mqucifer/print-shop#221): one the job has moved on from is
        refused, never given to what it asks now. Returns "" once given, or why not."""
        asked = self.asked(job_id)
        if asked is None:
            return "it isn't waiting for you"
        if to is not None and asked is not to:
            return "it moved on since: what it asks now wasn't answered"
        if asked.need is Need.CLOSER and answer is not None and not isinstance(answer, Answer):
            return "the closer choices are answered with one of them, or None to keep yours"
        if asked.need is Need.QUESTIONS:
            answers = tuple(answer)
            if len(answers) != len(asked.questions):
                return f"it asks {len(asked.questions)} questions, and {len(answers)} were answered"
            answer = {alike(q): a for q, a in zip(asked.questions, answers, strict=True)}
        elif asked.need is Need.REVIEW and not isinstance(answer, Review):
            return "a review is answered with a Review"
        elif asked.need is Need.LOOK and not isinstance(answer, bool):
            return "a look is answered ok (True) or reject (False)"
        return "" if asked._give(answer) else "it was answered already"

    def give_alike(self, job_ids: Sequence[str], questions: Sequence[Question],
                   answers: Sequence[Answer]) -> list[str]:
        """The answers given to one file's questions, given to these files too: each question
        another asks alike is answered for it, now or when it's asked, and only what's left is
        asked of the person. Returns what was done, a line each."""
        given = {alike(q): a for q, a in zip(questions, answers, strict=True)}
        said = []
        for job_id in job_ids:
            with self._lock:
                self._ahead.setdefault(job_id, {}).update(given)
            asked = self.asked(job_id)
            job = self._jobs.get(job_id)
            name = job.part if job is not None else job_id[:8]
            if asked is not None and asked.need is Need.QUESTIONS:
                covered = {alike(q): given[alike(q)] for q in asked.questions
                           if alike(q) in given}
                if covered:
                    asked._give(covered)
                left = len(asked.questions) - len(covered)
                same = ("the same answer" if len(covered) == 1
                        else f"the same {len(covered)} answers")
                said.append(f"{name}: {same}" + (
                    f", and {left} of its own still to answer" if left else ""))
            else:
                said.append(f"{name}: the same answers, given when its questions come")
        return said

    def _asker(self, job_id: str, generation: int) -> Callable[[Sequence[Question]], tuple[
            Answer, ...]]:
        def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
            have: dict[tuple[str, str, tuple[str, ...]], Answer] = {}
            while True:
                with self._lock:
                    have.update({k: a for k, a in self._ahead.get(job_id, {}).items()
                                 if any(alike(q) == k for q in questions)})
                left = tuple(q for q in questions if alike(q) not in have)
                if not left:
                    return tuple(have[alike(q)] for q in questions)
                if len(left) == len(questions):
                    left = tuple(questions)  # as they came: added ones are numbered on
                have.update(self._ask(job_id, generation, Asked(
                    job_id=job_id, need=Need.QUESTIONS, questions=left)))

        return ask

    def _waiting(self, job_id: str, generation: int) -> Waiting:
        """The person's side of the wait for the model's questions (mqucifer/print-shop#208), as
        a lone job has it: print-shop's two, or what the notes give, are asked at once, and the
        model's are asked as a second need when they come. Waiting on them is said in the job's
        line, gives the machine's place up, and `go` goes on without them."""

        def run_now() -> _Run:
            with self._lock:
                run = self._runs.get(job_id)
            if run is None or run.generation != generation or self._closing.is_set():
                raise Gone(job_id)
            return run

        def started(call: QuestionsCall) -> None:
            run = run_now()
            run.call = call
            run.go.clear()

        def ended(call: QuestionsCall, asked: QuestionsAsked) -> None:
            if call.stopped:
                return
            job = self._jobs.get(job_id)
            name = job.part if job is not None else job_id[:8]
            if asked.failed:
                said = f"the model {asked.failed} after {took(call.seconds)}"
            elif asked.closer or asked.added:
                said = f"the model's questions came after {took(call.seconds)}"
            else:
                said = f"the model read it in {took(call.seconds)} and has no other question"
            self._say(f"{name}, job {job_id[:8]}: {said}.", job_id)

        def wait(call: QuestionsCall) -> bool:
            run = run_now()
            self._slot.release()
            try:
                while not call.ended(0.2):
                    if run.go.is_set():
                        return False
                    if self._stale(job_id, generation):
                        raise Gone(job_id)
                return True
            finally:
                self._slot.acquire()
                run.back_at = time.monotonic()

        def closer(question: Question, kept: Answer, seconds: float) -> Answer | None:
            picked: Answer | None = self._ask(job_id, generation, Asked(
                job_id=job_id, need=Need.CLOSER, questions=(question,), kept=kept,
                seconds=seconds))
            return picked

        def went_on(call: QuestionsCall) -> None:
            self._say(f"job {job_id[:8]}: the notes gave what it's for and what's seen, so the "
                      "model's questions weren't waited for.", job_id)

        return Waiting(started=started, ended=ended, wait=wait, closer=closer, went_on=went_on)

    def go(self, job_id: str) -> str:
        """Go on without the model's questions for this job, as `go` does at a lone job's wait
        (mqucifer/print-shop#208). Returns "", or why not."""
        with self._lock:
            run = self._runs.get(job_id)
        call = run.call if run is not None else None
        if run is None or call is None or call.ended() or call.stopped:
            return "it isn't waiting for the model's questions"
        run.go.set()
        return ""

    def reading(self, job_id: str) -> float | None:
        """How long the model has been reading this job for its questions, or None."""
        with self._lock:
            run = self._runs.get(job_id)
        call = run.call if run is not None else None
        if call is None or call.ended() or call.stopped:
            return None
        return call.seconds

    def _reviewer(self, job_id: str, generation: int) -> Reviewer:
        def review(request: ReviewRequest) -> Review:
            answer: Review = self._ask(job_id, generation, Asked(
                job_id=job_id, need=Need.REVIEW, review=request))
            return answer

        return review

    def _ask(self, job_id: str, generation: int, asked: Asked) -> Any:
        """Wait for the person, giving the machine's place to another job meanwhile."""
        waits = {Need.QUESTIONS: Waits.QUESTIONS, Need.CLOSER: Waits.QUESTIONS,
                 Need.REVIEW: Waits.REVIEW, Need.MESH: Waits.MESH,
                 Need.LOOK: Waits.LOOK}[asked.need]
        with self._lock:
            run = self._runs.get(job_id)
            if run is None or run.generation != generation or self._closing.is_set():
                raise Gone(job_id)
            run.asked = asked
        self._jobs.at(job_id, waits)
        self._slot.release()
        try:
            return asked._wait()
        finally:
            self._slot.acquire()
            with self._lock:
                if run.asked is asked:
                    run.asked = None
                run.back_at = time.monotonic()
            if not asked._gone:
                self._jobs.at(job_id, Waits.PREPARING)

    # -- preparing ------------------------------------------------------------

    def _prepare(self, job_id: str) -> None:
        """Start the job's preparing, on a thread of its own, from where its checkpoint is."""
        with self._lock:
            run = self._runs.setdefault(job_id, _Run())
            run.generation += 1
            run.thread = threading.Thread(target=self._preparing_run,
                                          args=(job_id, run.generation),
                                          name=f"prepare-{job_id}", daemon=True)
            run.thread.start()

    def _stale(self, job_id: str, generation: int) -> bool:
        with self._lock:
            run = self._runs.get(job_id)
            return run is None or run.generation != generation or self._closing.is_set()

    def _graph(self, job: WaitingJob, generation: int) -> Any:
        p = self._preparing
        return build_graph(
            job.profiles, outdir_root=self._workdir, checkpointer=self._daemon.checkpointer,
            llm=p.llm, printer=None, slicer=p.slicer, ledger=self._ledger, proposer=p.proposer,
            asker=self._asker(job.job_id, generation),
            reviewer=self._reviewer(job.job_id, generation),
            loop=(dataclasses.replace(p.loop, person=_never_asked) if p.loop is not None
                  else None),
            show=None, store=p.store, waiting=self._waiting(job.job_id, generation),
            plate_slicer=p.plate_slicer,
        )

    def _preparing_run(self, job_id: str, generation: int) -> None:
        self._slot.acquire()
        try:
            job = self._jobs.get(job_id)
            if job is None or self._stale(job_id, generation):
                return
            graph = self._graph(job, generation)
            config: dict[str, Any] = {"configurable": {"thread_id": job_id}}
            snapshot = graph.get_state(config)
            interrupts = [i for task in snapshot.tasks for i in task.interrupts]
            if not snapshot.values:
                result = graph.invoke({
                    "job_id": job_id, "trace_id": str(uuid.uuid4()),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    **job.state()}, config=config)
            elif tuple(snapshot.next) == ("print",):
                # Started before a restart, and stopped before the printer: ready, and its
                # gate's checkpoint is found again when it's offered (`Daemon.resume`).
                result = {"__interrupt__": [{"gate": Gate.BEFORE_PRINT.value}]}
            elif interrupts:
                result = {"__interrupt__": interrupts}
            elif snapshot.next:
                result = graph.invoke(None, config=config)
            else:
                self._ended(job_id, "it had already ended")
                return
            while not self._stale(job_id, generation):
                if "__interrupt__" not in result:
                    self._ended(job_id, "the mesh gate was rejected: nothing was sliced")
                    return
                found = result["__interrupt__"][0]
                gate = dict(getattr(found, "value", found))
                if gate.get("gate") == Gate.BEFORE_PRINT:
                    if not self._looked(job_id, generation, graph):
                        return
                    self._ready(job_id, graph)
                    return
                approved, note = self._ask(job_id, generation, Asked(
                    job_id=job_id, need=Need.MESH, gate=gate))
                result = graph.invoke(LgCommand(resume={"approved": approved, "note": note}),
                                      config=config)
        except Gone:
            return
        except Exception as exc:  # noqa: BLE001 - one job's failure is said, and the rest go on
            if not self._stale(job_id, generation):
                logger.warning("job %s ended while it was prepared", job_id, exc_info=True)
                self._ended(job_id, f"{exc}")
        finally:
            self._slot.release()

    def _looked(self, job_id: str, generation: int, graph: Any) -> bool:
        """A file sliced by hand that someone else sent needs the person once before it's ready:
        what it prints, as `print_job.py --sliced` shows it (mqucifer/print-shop#226). Else a
        Start, whose bed question is its only screen, could print what nobody here looked at.
        False where they rejected it: it's out of the queue, unprinted."""
        job = self._jobs.get(job_id)
        kept = Kept(self._workdir / job_id)
        if job is None or not job.by_hand or job.sent is None or not job.sent.by \
                or job.copy_of or kept.looked():
            return True
        values = graph.get_state({"configurable": {"thread_id": job_id}}).values
        ok = self._ask(job_id, generation, Asked(
            job_id=job_id, need=Need.LOOK, shown=tuple(self._shown(values))))
        if not ok:
            self._jobs.remove(job_id)
            for copy in [j for j in self._jobs.all() if j.copy_of == job_id]:
                self._jobs.remove(copy.job_id)
            self._say(f"Removed: {job.part}, job {job_id[:8]}: you rejected what it prints. "
                      "Nothing printed.", job_id)
            self._wake.set()
            return False
        kept.look()
        self._jobs.at(job_id, Waits.PREPARING)
        return True

    def _shown(self, values: dict[str, Any]) -> list[str]:
        """What `print_job.py --sliced` shows of a hand-sliced file: what it is, Orca's estimate,
        what's kept of it (its settings, its objects' overrides), and the slice's reading with
        its pauses."""
        from print_shop import sliced as hand_sliced  # noqa: PLC0415

        job: SlicedJob = values["sliced"]
        file = job.file
        grams = f"{file.filament_g:g} g, " if file.filament_g is not None else ""
        out = [f"Sliced by hand: {file.said()}.",
               "No questions, no proposal, no lesson applied, nothing sliced again.",
               f"  sha256      {file.sha256}",
               f"  Orca        {file.print_time_s // 60} min, {grams}{file.filament_cm3:g} cm3, "
               f"{file.layer_count} layers"]
        out += [f"  {line}" for line in job.record(self._preparing.store).lines()]
        out += hand_sliced.report(job, values.get("slice_reading"),
                                  tuple(values.get("slice_flags") or ()))
        return out

    def _ready(self, job_id: str, graph: Any) -> None:
        """Ready to print, and its copies with it, each under its own name."""
        self._respooled(job_id, graph)  # moved to another colour while it was prepared
        self._jobs.at(job_id, Waits.READY)
        job = self._jobs.get(job_id)
        for copy in [j for j in self._jobs.all() if j.copy_of == job_id
                     and j.waits is Waits.WITH_FIRST]:
            try:
                self._copied(job_id, copy, graph)
            except Exception as exc:  # noqa: BLE001 - said, and the copy is prepared itself
                logger.warning("copy %s couldn't take its first's slice", copy.job_id,
                               exc_info=True)
                self._say(f"{copy.part} ({copy.copied}) couldn't take its first copy's slice "
                          f"({exc}), so it's prepared on its own", copy.job_id)
                self._jobs.add(copy.model_copy(update={"waits": Waits.PREPARING, "copy_of": ""}))
                self._prepare(copy.job_id)
        if job is not None:
            self._say(f"Ready: {job.part}" + (f" ({job.copied})" if job.copied else "")
                      + f", job {job_id[:8]}.", job_id)
        self._wake.set()

    def _copied(self, first: str, copy: WaitingJob, graph: Any) -> None:
        """A copy made ready from its first's slice: the same G-code, under the copy's own name,
        so the printer and the ledger keep it apart, and what the first kept, so a move keeps the
        answers. Its gate opens as any job's does, unapproved."""
        values = dict(graph.get_state({"configurable": {"thread_id": first}}).values)
        folder = self._workdir / copy.job_id
        folder.mkdir(parents=True, exist_ok=True)
        gcode = Path(values["gcode_path"])
        target = folder / gcode.name.replace(first, copy.job_id)
        shutil.copyfile(gcode, target)
        if job_id_from_filename(target.name) != copy.job_id:
            raise RuntimeError(f"{target.name} doesn't carry job {copy.job_id}")
        Kept(self._workdir / first).copy_to(folder)
        # What the first kept to print again, as each copy is its run (mqucifer/print-shop#216).
        copy_kept(self._workdir / first, folder)
        values.update({
            "job_id": copy.job_id, "trace_id": str(uuid.uuid4()),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "gcode_path": str(target),
            "slice": values["slice"].model_copy(update={"gcode_path": target}),
        })
        values.pop("gate_decisions", None)  # its own gate decides it, as a new job's does
        values.update(_on_spool(values, self._spool_material(copy)))
        config: dict[str, Any] = {"configurable": {"thread_id": copy.job_id}}
        by = "sliced" if values.get("sliced") is not None else "slice"
        graph.update_state(config, values, as_node=by)
        result = graph.invoke(None, config=config)
        if "__interrupt__" not in result:
            raise RuntimeError("its gate didn't open")
        self._jobs.at(copy.job_id, Waits.READY)

    def _ended(self, job_id: str, why: str) -> None:
        """Ended without printing, saying why. Its copies still waiting for it end too."""
        job = self._jobs.get(job_id)
        name = job.part if job is not None else job_id[:8]
        self._jobs.remove(job_id)
        self._say(f"Ended without printing: {name}, job {job_id[:8]}: {why}", job_id)
        for copy in [j for j in self._jobs.all() if j.copy_of == job_id
                     and j.waits is Waits.WITH_FIRST]:
            self._jobs.remove(copy.job_id)
            self._say(f"Ended without printing: {copy.part} ({copy.copied}), which waited for "
                      "it", copy.job_id)
        self._wake.set()

    def _say(self, said: str, job_id: str = "") -> None:
        logger.info("queue: %s", said)
        with self._lock:
            self._said.append(said)
        if self._logs is not None and job_id:
            self._logs.log(job_id).write(f"queue: {said}\n")

    def log(self, job_id: str) -> JobLog | None:
        """The job's own log, for a screen to keep what it shows of the job and what's typed."""
        return self._logs.log(job_id) if self._logs is not None else None

    # -- changing the queue ---------------------------------------------------

    def order(self, job_ids: Sequence[str]) -> str:
        """These first, in this order: the next offered is the first ready that isn't held."""
        known = {j.job_id for j in self._jobs.all()}
        if unknown := [j for j in job_ids if j not in known]:
            return f"not in the queue: {', '.join(unknown)}"
        self._jobs.order(job_ids)
        self._wake.set()
        return ""

    def first(self, job_id: str) -> str:
        return self.order([job_id])

    def place(self, job_id: str, number: int) -> str:
        """The job moved to `number` in the queue's order."""
        ids = [j.job_id for j in self._jobs.all() if j.job_id != job_id]
        if len(ids) + 1 == len(self._jobs.all()):
            at = max(0, min(number - 1, len(ids)))
            return self.order([*ids[:at], job_id, *ids[at:]])
        return "not in the queue"

    def together(self, spool_id: int) -> str:
        """The jobs for one spool taken together, first, in their order."""
        mine = [j.job_id for j in self._jobs.all() if j.spool_id == spool_id]
        if not mine:
            return f"no job in the queue is for spool #{spool_id}"
        return self.order(mine)

    def hold(self, job_id: str, held: bool = True) -> str:
        """Held, a job is prepared, and never offered to print until it's let go."""
        if self._jobs.get(job_id) is None:
            return "not in the queue"
        self._jobs.hold(job_id, held)
        if held:
            self._daemon.withdraw(job_id)
        self._wake.set()
        return ""

    def remove(self, job_id: str) -> str:
        """Out of the queue, unprinted. A copy waiting for it takes its place, with its answers."""
        with self._changed(job_id):
            return self._remove(job_id)

    @contextmanager
    def _changed(self, job_id: str) -> Iterator[None]:
        """While a job is moved or removed, it isn't offered."""
        with self._lock:
            self._changing.add(job_id)
        try:
            yield
        finally:
            with self._lock:
                self._changing.discard(job_id)
            self._wake.set()

    def _remove(self, job_id: str) -> str:
        job = self._jobs.get(job_id)
        if job is None:
            return "not in the queue"
        if self._daemon.active() == job_id and not self._daemon.withdraw(job_id):
            return "it was started, and is being sent"
        self._stop(job_id)
        self._jobs.remove(job_id)
        copies = [j for j in self._jobs.all() if j.copy_of == job_id
                  and j.waits is Waits.WITH_FIRST]
        if copies:
            heir, *rest = copies
            Kept(self._workdir / job_id).copy_to(self._workdir / heir.job_id)
            self._jobs.add(heir.model_copy(update={"waits": Waits.PREPARING, "copy_of": ""}))
            for other in rest:
                self._jobs.add(other.model_copy(update={"copy_of": heir.job_id}))
            self._prepare(heir.job_id)
        self._say(f"Removed: {job.part}" + (f" ({job.copied})" if job.copied else "")
                  + f", job {job_id[:8]}. Nothing printed.", job_id)
        self._wake.set()
        return ""

    def _stop(self, job_id: str) -> threading.Thread | None:
        """Its preparing in this process stops at the next thing it asks of the person, and
        nothing it comes to after that is taken."""
        with self._lock:
            run = self._runs.setdefault(job_id, _Run())
            run.generation += 1
            if run.asked is not None:
                run.asked._cancel()
            return run.thread

    def move(self, job_id: str, spool_id: int) -> list[str] | str:
        """The job, and its copies waiting with it, moved to another spool, colour or material:
        prepared again for it with the answers and changes kept, back to the review. A
        temperature or fan set for another variety comes back unticked. Returns what was done,
        a line each, or why it can't be moved."""
        with self._changed(job_id):
            return self._move(job_id, spool_id)

    def _move(self, job_id: str, spool_id: int) -> list[str] | str:
        job = self._jobs.get(job_id)
        if job is None:
            return "not in the queue"
        if job.spool_id == spool_id:
            return f"it's for spool #{spool_id} already"
        try:
            now = self._material(spool_id)
        except InventoryError as exc:
            return str(exc)
        was = self._prepared_for(job)
        if job.by_hand and was is not None and now is not None and not was.same_filament(now):
            return ("a file sliced by hand prints with the filament it was sliced for: slice "
                    "it again in Orca for the other, and add that")
        if self._daemon.active() == job_id and not self._daemon.withdraw(job_id):
            return "it was started, and is being sent"
        if was is not None and now is not None and was.same_filament(now):
            return self._recoloured(job, now)
        thread = self._stop(job_id)
        if job.copy_of and job.waits is Waits.WITH_FIRST:
            # A copy moved alone leaves its first: prepared itself, with what its first kept.
            Kept(self._workdir / job.copy_of).copy_to(self._workdir / job_id)
            job = job.model_copy(update={"copy_of": ""})
        moving = [job, *(j for j in self._jobs.all() if j.copy_of == job_id
                         and j.waits is Waits.WITH_FIRST)]
        said = Kept(self._workdir / job_id).move(was, now) if not job.by_hand else []
        for one in moving:
            given = dict(one.given)
            profiles = one.profiles
            if one.by_hand:
                sliced = one.state()["sliced"]
                given["sliced"] = sliced.model_copy(update={"material": now})
            else:
                profiles = self._profiles_for(now, self._workdir / one.job_id)
            self._jobs.add(one.model_copy(update={
                "given": given, "profiles": profiles,
                "spool_id": now.spool_id if now is not None else None,
                "waits": Waits.WITH_FIRST if one.copy_of else Waits.PREPARING}))
        threading.Thread(target=self._prepared_again, args=(job_id, thread),
                         name=f"move-{job_id}", daemon=True).start()
        named = now.named() if now is not None else f"spool #{spool_id}"
        return [f"{job.part}: moved to {named}, prepared again, and back to your review", *said]

    def _recoloured(self, job: WaitingJob, now: Material) -> list[str]:
        """Another spool of the same variety and tuning, only its colour different: the job moves
        at once, its slice and review standing, and it names the new spool, which is what its
        Start asks to be loaded (the Sponsor, 2026-11-24). Its copies waiting with it move too."""
        moving = [job, *(j for j in self._jobs.all() if j.copy_of == job.job_id
                         and j.waits is Waits.WITH_FIRST)]
        for one in moving:
            given = dict(one.given)
            profiles = one.profiles
            if one.by_hand:
                given["sliced"] = one.state()["sliced"].model_copy(update={"material": now})
            elif profiles is not None and profiles.used is not None:
                profiles = profiles.model_copy(update={
                    "used": profiles.used.model_copy(update={"material": now})})
            self._jobs.add(one.model_copy(update={
                "given": given, "profiles": profiles, "spool_id": now.spool_id}))
        if job.waits is Waits.READY:
            moved = self._jobs.get(job.job_id)
            assert moved is not None
            self._respooled(job.job_id, self._graph(moved, 0))
        self._wake.set()
        return [f"{job.part}: moved to {now.named()}: the same filament, so its slice and "
                "review stand, and nothing is prepared again"]

    def _respooled(self, job_id: str, graph: Any) -> None:
        """A job at its gate names the spool the queue has it for: its checkpoint's spool, which
        Start checks, changed to it where only the colour differs, and the gate made again."""
        job = self._jobs.get(job_id)
        config: dict[str, Any] = {"configurable": {"thread_id": job_id}}
        values = graph.get_state(config).values
        update = _on_spool(values, self._spool_material(job))
        if not update:
            return
        by = "sliced" if values.get("sliced") is not None else "slice"
        graph.update_state(config, update, as_node=by)
        graph.invoke(None, config=config)  # its gate, unapproved, as it was

    def _spool_material(self, job: WaitingJob | None) -> Material | None:
        if job is None or job.spool_id is None:
            return None
        try:
            return self._material(job.spool_id)
        except InventoryError:
            return None

    def _prepared_again(self, job_id: str, thread: threading.Thread | None) -> None:
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._daemon.checkpointer.delete_thread(job_id)
        self._prepare(job_id)

    def _prepared_for(self, job: WaitingJob) -> Material | None:
        if job.by_hand:
            sliced = job.state()["sliced"]
            return sliced.material if isinstance(sliced, SlicedJob) else None
        used = job.profiles.used if job.profiles is not None else None
        return used.material if used is not None else None

    # -- offering, one at a time ----------------------------------------------

    def _offered_now(self) -> str | None:
        gate = self._daemon.pending_gate()
        return str(gate["job_id"]) if gate is not None and gate.get("job_id") else None

    def next_ready(self) -> WaitingJob | None:
        """The job offered next: the first ready one in the queue that isn't held."""
        with self._lock:
            changing = set(self._changing)
        return next((j for j in self._jobs.all()
                     if j.waits is Waits.READY and not j.held and j.job_id not in changing), None)

    def _busy(self) -> bool:
        """A print runs, as the ledger has it: nothing is offered until it ends."""
        if self._ledger is None:
            return False
        try:
            return bool(self._ledger.running())
        except Exception:  # noqa: BLE001 - a ledger that can't be read offers nothing
            logger.warning("the ledger couldn't be read for what prints", exc_info=True)
            return True

    def _offer_loop(self) -> None:
        while not self._closing.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._closing.is_set():
                return
            try:
                self._sent_in()
                self._limit()
                self._offer()
            except Exception:  # noqa: BLE001 - the loop goes on, and says it
                logger.exception("the queue couldn't offer its next job")

    def _limit(self) -> None:
        """A job whose machine steps have run past the slice's limit stops, saying so: it never
        sits as being prepared for ever (mqucifer/print-shop#209's limits on machine steps)."""
        now = time.monotonic()
        with self._lock:
            over = [(job_id, run) for job_id, run in self._runs.items()
                    if run.thread is not None and run.thread.is_alive()
                    and (run.asked is None or not run.asked.open)
                    and self.reading(job_id) is None
                    and now - run.back_at >= self._slice_limit_s]
        for job_id, _ in over:
            numbers = {j.job_id: n for n, j in enumerate(self._jobs.all(), 1)}
            if job_id not in numbers:
                continue
            n = numbers[job_id]
            self._stop(job_id)
            why = (f"its slice took over {_said_limit(self._slice_limit_s)}: `retry {n}` "
                   f"prepares it again, with what you gave it, or `remove {n}`")
            self._jobs.at(job_id, Waits.STOPPED, why)
            job = self._jobs.get(job_id)
            self._say(f"Stopped: {job.part if job else job_id[:8]}, job {job_id[:8]}: {why}.",
                      job_id)

    def retry(self, job_id: str) -> str:
        """A stopped job prepared again, with the answers and changes kept: once what it was
        doing has ended, from its start. Returns "", or why not."""
        job = self._jobs.get(job_id)
        if job is None:
            return "not in the queue"
        if job.waits is not Waits.STOPPED:
            return "it hasn't stopped"
        thread = self._stop(job_id)
        self._jobs.at(job_id, Waits.PREPARING)
        threading.Thread(target=self._prepared_again, args=(job_id, thread),
                         name=f"retry-{job_id}", daemon=True).start()
        return ""

    def _offer(self) -> None:
        active = self._daemon.active()
        ready = self.next_ready()
        if active is not None:
            # Another before it now, or it's held or gone: it goes back to wait, undecided.
            with self._lock:
                changing = active in self._changing  # the change puts it back itself
            if not changing and self._offered_now() == active and (
                    ready is None or ready.job_id != active):
                self._daemon.withdraw(active)
            return
        if ready is None or self._busy():
            return
        try:
            self._daemon.resume(ready.job_id)
        except Exception as exc:  # noqa: BLE001 - said, and tried again at the next wake
            logger.warning("job %s couldn't be offered: %s", ready.job_id, exc)
            return
        spool = ""
        if ready.spool_id is not None:
            spool = next((line.spool + ("" if line.loaded else ": load it and record it "
                                        f"(`stock.py load {ready.spool_id}`) first")
                          for line in self.lines() if line.job.job_id == ready.job_id), "")
        self._say(f"Offered to print: {ready.part}" + (f" ({ready.copied})" if ready.copied
                                                       else "")
                  + f", job {ready.job_id[:8]}" + (f", on {spool}" if spool else "")
                  + ". Start it here or on the card.", ready.job_id)
        with self._lock:
            self._offers[ready.job_id] = self._offers.get(ready.job_id, 0) + 1
            followed = ready.job_id in self._following
            self._following.add(ready.job_id)
        if not followed:  # followed already, its follower follows this offer too
            threading.Thread(target=self._followed, args=(ready.job_id, ready.part),
                             daemon=True, name=f"follow-{ready.job_id}").start()

    def _followed(self, job_id: str, part: str) -> None:
        """The offered job to its end: put back, started, rejected or failed, each said. Put back
        and offered again meanwhile, it's followed on."""
        with self._lock:
            seen = self._offers.get(job_id, 0)
        following = True
        try:
            while True:
                try:
                    result = self._daemon.wait_for_finish(
                        job_id, self._finish_wait_s, tell=lambda said: self._say(said, job_id))
                except TimeoutError:
                    if self._closing.is_set():
                        return  # put by: the job waits where it was, for the queue taken up again
                    self._say(self._daemon.abandon(job_id, "waiting for the job to finish",
                                                   self._finish_wait_s), job_id)
                    self._wake.set()
                    return
                if not result.withdrawn:
                    break
                with self._lock:
                    if self._offers.get(job_id, 0) != seen:  # offered again since: follow on
                        seen = self._offers[job_id]
                        continue
                    self._following.discard(job_id)
                    following = False
                self._wake.set()
                return  # back to wait, and its preparing is the queue's again
            if result.printed:
                self._say(f"Started: {part}, job {job_id[:8]}. Its outcome is asked on the card "
                          "when it ends.", job_id)
            elif result.error:
                self._say(f"Ended without printing: {part}, job {job_id[:8]}: {result.error}",
                          job_id)
            else:
                self._say(f"Rejected: {part}, job {job_id[:8]}. Nothing printed.", job_id)
            with self._lock:
                self._runs.pop(job_id, None)
            self._wake.set()
        finally:
            if following:
                with self._lock:
                    self._following.discard(job_id)
