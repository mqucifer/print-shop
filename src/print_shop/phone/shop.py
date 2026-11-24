"""What the phone's screens read and do, besides Start (mqucifer/print-shop#221).

The page has no logic of its own: each screen reads what the terminal reads, and each button
calls the action the terminal's command calls.

- **The queue** is the run's `PrintQueue`, the one `queue>` drives: its lines, its sends, and
  `hold`, `first` and `remove`.
- **The ledger and the stock** are read-only for each view (`Ledger.read_only`, SQLite's
  `mode=ro`), as the desk reads them.
- **An outcome is recorded by the card's own Record** (`Daemon.record_outcome`), so the card
  and the phone can't disagree.

Nothing here starts, arms or approves anything: no method reaches the gate.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from print_shop import reprint
from print_shop.desk.read import Desk, OnHand
from print_shop.inventory import Asked as StockAsked
from print_shop.inventory import (
    Inventory,
    InventoryError,
    Material,
    Pick,
    Spool,
    SpoolState,
)
from print_shop.ledger import Ledger, LedgerError, Row
from print_shop.orchestrator.kept import WaitingJob, Waits
from print_shop.orchestrator.print_queue import Adding, Asked, Line, Need, SentTo, alike
from print_shop.phone.session import Session
from print_shop.queue_terminal import Runs
from print_shop.setup.intake import Answer, Question, cli_asker
from print_shop.setup.pauses import said as pause_said
from print_shop.setup.reading import PauseReading
from print_shop.setup.review import ReviewRequest, cli_reviewer
from print_shop.setup.screen import Moved, what_moved
from print_shop.setup.waiting import cli_waiting

__all__ = [
    "LOOK_AGAIN", "AlsoDiffers", "Daemonish", "LoadAsk", "NowPrinting", "Queued", "Queueish",
    "ReadyGroup", "ReadyRow", "Shop", "ToRecord", "spool_words",
]

logger = logging.getLogger(__name__)

#: The width the terminal's screens are laid out to for the phone: what a phone shows in a line.
PAGE_WIDTH = 60


def _nothing() -> None:
    """Nothing typed ahead to drop: the phone's answers come only from its forms."""


class Queueish(Protocol):
    """The queue's reads and actions the page uses: `PrintQueue`'s own."""

    def lines(self) -> list[Line]: ...

    def sent(self) -> list[SentTo]: ...

    def find(self, named: str) -> WaitingJob | None: ...

    def hold(self, job_id: str, held: bool = True) -> str: ...

    def first(self, job_id: str) -> str: ...

    def remove(self, job_id: str) -> str: ...

    def move(self, job_id: str, spool_id: int) -> list[str] | str: ...

    def add(self, adding: Sequence[Adding]) -> tuple[list[str], list[str]]: ...

    def asked(self, job_id: str) -> Asked | None: ...

    def give(self, job_id: str, answer: Any) -> str: ...

    def give_alike(self, job_ids: Sequence[str], questions: Sequence[Question],
                   answers: Sequence[Answer]) -> list[str]: ...

    def go(self, job_id: str) -> str: ...

    def reading(self, job_id: str) -> float | None: ...


class Daemonish(Protocol):
    """The daemon's reads and the card's Record, as the page uses them."""

    def prepared(self, job_id: str) -> dict[str, Any]: ...

    def outcome_choices(self, job_id: str) -> dict[str, Pick]: ...

    def record_outcome(self, job_id: str, *, result: str, symptom: str, note: str = "",
                       pick: str = "", picked: Pick | None = None) -> str: ...


def spool_words(spool: Material | Spool | None) -> str:
    """A spool as the screens say it: "Black PETG"."""
    if spool is None:
        return ""
    return f"{spool.colour} {spool.variety}".strip()


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


@dataclass(frozen=True)
class NowPrinting:
    """The print on the printer, by the ledger and the slice's own times: what's left, and a
    pause ahead, each an estimate from when it started."""

    job_id: str
    part: str
    #: "14 min left. Black PETG.", or the spool alone where the time's run out.
    said: str
    #: "Pauses in 6 min for an insert. Be at the printer.", or "".
    pause: str


@dataclass(frozen=True)
class ToRecord:
    """A print that ended with no outcome yet: asked on the card, and here."""

    job_id: str
    part: str
    ended_at: str


@dataclass(frozen=True)
class ReadyRow:
    """A ready job, with its copies alike counted once."""

    line: Line
    count: int
    #: The slice's time for one, in seconds, or None where it isn't read.
    each_s: int | None


@dataclass(frozen=True)
class ReadyGroup:
    """Ready jobs for one spool."""

    spool: str
    loaded: bool
    rows: tuple[ReadyRow, ...]
    spool_id: int | None = None


@dataclass(frozen=True)
class LoadAsk:
    """A load that has to say both halves first: that print is over, and this spool is on
    (mqucifer/print-shop#218, #246's A), in the stock's own words."""

    spool: Spool
    question: str
    changes: int


#: A load refused because the stock changed since the page was read.
LOOK_AGAIN = ("what's on the printer was changed, said or seen otherwise since the page was "
              "shown, so nothing's recorded. Look again")


@dataclass(frozen=True)
class AlsoDiffers:
    """A change's slice that differs from the run in more than was changed: it's asked once,
    as Enter is asked again at the terminal."""

    lines: tuple[str, ...]
    beyond: tuple[str, ...]


@dataclass(frozen=True)
class Queued:
    """A print again, in the queue: what was said of it, and its job."""

    lines: tuple[str, ...]
    job_id: str = ""


@dataclass
class Shop:
    """The run's queue and daemon, and where the ledger is: what the screens read.

    `queue` is None in a run without one (a lone job's), and `daemon` None where only the
    ledger is read."""

    ledger_path: Path
    queue: Queueish | None = None
    daemon: Daemonish | None = None
    #: Where the finished runs are, and what slices a change to one: the terminal's `again`'s.
    runs: Runs | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    #: Each job's need being answered from the phone, by the terminal's own code.
    sessions: dict[str, Session] = field(default_factory=dict)
    #: What the last answer from the phone came to, by job: given, or why not.
    answered: dict[str, str] = field(default_factory=dict)
    #: Each job's review as last shown, and the one before it, to say what moved since.
    reviews: dict[str, tuple[ReviewRequest, ReviewRequest | None]] = field(default_factory=dict)

    # -- the ledger, read only ------------------------------------------------

    @contextmanager
    def ledger(self) -> Iterator[Ledger | None]:
        """The ledger, read-only for this view, or None where there's none yet."""
        try:
            ledger = Ledger.read_only(self.ledger_path)
        except LedgerError:
            yield None
            return
        try:
            yield ledger
        finally:
            ledger.close()

    def printing(self) -> list[NowPrinting]:
        with self.ledger() as ledger:
            rows = ledger.running() if ledger is not None else []
        return [self._now_printing(row) for row in rows]

    def _now_printing(self, row: Row) -> NowPrinting:
        spool = spool_words(row.material)
        spool_said = f" {spool}." if spool else ""
        elapsed = self._since(row.started_at)
        if elapsed is None:
            return NowPrinting(row.job_id, row.part_said, spool_said.strip(), "")
        left = row.print_time_s - elapsed
        said = (f"About {_minutes(left)} min left.{spool_said}" if left > 0
                else f"Past the slice's {_minutes(row.print_time_s)} min.{spool_said}")
        pause = ""
        ahead = self._pause_ahead(row, elapsed)
        if ahead is not None:
            p, seconds = ahead
            to = f" to {p.to}" if p.to else ""
            pause = (f"Pauses in {_minutes(seconds)} min for {pause_said(p.kind)}{to}. "
                     "Be at the printer.")
        return NowPrinting(row.job_id, row.part_said, said, pause)

    @staticmethod
    def _pause_ahead(row: Row, elapsed: float) -> tuple[PauseReading, float] | None:
        """The next pause, by the slice's own times from the start, and how far ahead."""
        for p in row.reading.pauses if row.reading else ():
            if p.after_s - elapsed > 0:
                return p, p.after_s - elapsed
        return None

    def pause_ahead(self, job_id: str) -> tuple[int, float] | None:
        """The running print's next pause, as the layer it comes before and the seconds to it,
        or None."""
        row = self.run(job_id)
        elapsed = self._since(row.started_at) if row is not None else None
        if row is None or elapsed is None:
            return None
        ahead = self._pause_ahead(row, elapsed)
        return (ahead[0].before_layer, ahead[1]) if ahead is not None else None

    def _since(self, at: str | None) -> float | None:
        if not at:
            return None
        try:
            started = datetime.fromisoformat(at)
        except ValueError:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        return (self.now() - started).total_seconds()

    def to_record(self) -> list[ToRecord]:
        with self.ledger() as ledger:
            rows = ledger.awaiting_answer() if ledger is not None else []
        return [ToRecord(r.job_id, r.part_said, r.ended_at or "") for r in rows]

    def on_hand(self) -> OnHand:
        """What's on the printers and on the shelf, as the desk reads it."""
        return Desk(self.ledger_path, self.ledger_path.parent).on_hand()

    def run(self, job_id: str) -> Row | None:
        with self.ledger() as ledger:
            return ledger.get(job_id) if ledger is not None else None

    # -- the queue ------------------------------------------------------------

    def lines(self) -> list[Line]:
        return self.queue.lines() if self.queue is not None else []

    def sent(self) -> list[SentTo]:
        return self.queue.sent() if self.queue is not None else []

    def line(self, job_id: str) -> Line | None:
        return next((li for li in self.lines() if li.job.job_id == job_id), None)

    def prepared(self, job_id: str) -> dict[str, Any]:
        """A job's state as its preparing left it, read only, or {}."""
        return self.daemon.prepared(job_id) if self.daemon is not None else {}

    def each_s(self, job: WaitingJob) -> int | None:
        """The slice's time for one of `job`, read from its preparing: a copy's is its first's."""
        if self.daemon is None:
            return None
        found = self.daemon.prepared(job.copy_of or job.job_id).get("slice")
        return int(found.print_time_s) if found is not None else None

    def ready(self, lines: Sequence[Line]) -> list[ReadyGroup]:
        """The ready jobs by spool, loaded spools first, each in the queue's order; copies of
        one job are one row, counted."""
        try:
            spools = {s.id: s for s in self.on_hand().spools}
        except LedgerError:
            spools = {}
        groups: dict[int | None, list[Line]] = {}
        for line in lines:
            if line.job.waits is not Waits.READY or line.needs is not None:
                continue
            groups.setdefault(line.job.spool_id, []).append(line)
        out = []
        for spool_id, members in groups.items():
            rows: list[ReadyRow] = []
            seen: dict[str, int] = {}
            for line in members:
                key = line.job.copy_of or line.job.job_id
                if key in seen:
                    at = seen[key]
                    rows[at] = ReadyRow(rows[at].line, rows[at].count + 1, rows[at].each_s)
                    continue
                seen[key] = len(rows)
                rows.append(ReadyRow(line, 1, self.each_s(line.job)))
            spool = spools.get(spool_id) if spool_id is not None else None
            named = spool_words(spool) or members[0].spool or "No spool named"
            out.append(ReadyGroup(named, members[0].loaded, tuple(rows), spool_id))
        return sorted(out, key=lambda g: not g.loaded)

    # -- the queue's actions --------------------------------------------------

    def act(self, verb: str, job_id: str) -> str:
        """`hold`, `release`, `first` or `remove`, as `queue>` does them. Returns "" once done,
        or why not."""
        if self.queue is None:
            return "the queue isn't running: start it at the Mac with `print_job.py --queue`"
        if verb == "hold":
            return self.queue.hold(job_id)
        if verb == "release":
            return self.queue.hold(job_id, False)
        if verb == "first":
            return self.queue.first(job_id)
        if verb == "remove":
            return self.queue.remove(job_id)
        raise ValueError(verb)

    def move(self, job_id: str, spool_id: int) -> list[str] | str:
        """The job printed with another spool: the queue's own `spool` (mqucifer/print-shop#210).
        Another colour moves at once; another material goes back to the review, its answers
        kept."""
        if self.queue is None:
            return "the queue isn't running: start it at the Mac with `print_job.py --queue`"
        return self.queue.move(job_id, spool_id)

    # -- what a job needs: the terminal's own screens, answered from the phone ---

    def asked(self, job_id: str) -> Asked | None:
        return self.queue.asked(job_id) if self.queue is not None else None

    def session(self, job_id: str) -> Session | None:
        """The phone's session on this job's need now, or None. One for another need, or that
        ended, is closed and forgotten."""
        found = self.sessions.get(job_id)
        if found is None:
            return None
        asked = self.asked(job_id)
        if found.ended or asked is None or asked.since != found.since:
            found.close()
            if found.ended and found.since == getattr(asked, "since", None):
                return found  # its end is still the page's news
            self.sessions.pop(job_id, None)
            if found.ended:
                self.answered[job_id] = found.said
            return None
        return found

    def alike(self, job_id: str) -> list[Line]:
        """The other jobs whose questions now are all asked alike to this one's: the same
        answers cover them, as `answer N M` gives them (`give_alike`)."""
        asked = self.asked(job_id)
        if self.queue is None or asked is None or asked.need is not Need.QUESTIONS:
            return []
        mine = {alike(q) for q in asked.questions}
        found = []
        for line in self.lines():
            other = self.queue.asked(line.job.job_id) if line.job.job_id != job_id else None
            if other is not None and other.need is Need.QUESTIONS and other.questions and all(
                    alike(q) in mine for q in other.questions):
                found.append(line)
        return found

    def begin(self, job_id: str, ahead: dict[str, list[str]], *,
              alike_to: Sequence[str] = ()) -> Session | str:
        """The terminal's own screen for this job's need, begun with the form's answers to the
        prompts it answers. Returns the session, or why not."""
        queue = self.queue
        asked = self.asked(job_id)
        if queue is None or asked is None:
            return "it isn't waiting for you now"
        if self.session(job_id) is not None:
            return "it's being answered on a phone already: look again"
        q = queue

        def alive() -> bool:
            return q.asked(job_id) is asked

        def give(answer: Any) -> str:
            return q.give(job_id, answer)

        after = None
        run: Callable[[Callable[[str], str], Callable[[str], None]], Any]
        if asked.need is Need.QUESTIONS:
            questions = asked.questions

            def run(read: Callable[[str], str], write: Callable[[str], None]) -> Any:
                return cli_asker(read=read, write=write, forget=_nothing,
                                 width=lambda: PAGE_WIDTH)(questions)

            if alike_to:
                others = list(alike_to)

                def after(answers: Any) -> None:
                    said = q.give_alike(others, questions, answers)
                    self.answered[job_id] = "; ".join(said)
        elif asked.need is Need.CLOSER and asked.kept is not None:
            question, kept, seconds = asked.questions[0], asked.kept, asked.seconds

            def run(read: Callable[[str], str], write: Callable[[str], None]) -> Any:
                waiting = cli_waiting(read=read, write=write, forget=_nothing,
                                      screen=lambda: None, typed=lambda s: False,
                                      width=lambda: PAGE_WIDTH)
                assert waiting.closer is not None
                return waiting.closer(question, kept, seconds)
        elif asked.need is Need.REVIEW and asked.review is not None:
            request = asked.review

            def run(read: Callable[[str], str], write: Callable[[str], None]) -> Any:
                return cli_reviewer(read=read, write=write, forget=_nothing,
                                    asks=lambda: True, screen=lambda: None,
                                    width=lambda: PAGE_WIDTH, more=lambda: False)(request)
        else:
            return f"it needs {asked.need}, which this screen doesn't answer"
        made = Session(job_id, asked.since, run, give, after=after, alive=alive)
        self.sessions[job_id] = made
        self.answered.pop(job_id, None)
        made.start(ahead)
        return made

    def give_now(self, job_id: str, since: float, answer: Any) -> str:
        """An answer that needs no screen of the terminal's: the mesh gate's or a look's, for
        the need the page showed (`since`). Returns "" once given, or why not."""
        asked = self.asked(job_id)
        if self.queue is None or asked is None:
            return "it isn't waiting for you now"
        if asked.since != since:
            return "that page is out of date: it moved on since. Look again"
        return self.queue.give(job_id, answer)

    def moved(self, job_id: str, request: ReviewRequest) -> Moved | None:
        """What moved since the review before this one of the job, as the terminal marks it
        from its second round on; None on its first."""
        shown = self.reviews.get(job_id)
        if shown is None or shown[0] is not request:
            before = shown[0] if shown is not None else None
            self.reviews[job_id] = (request, before)
            shown = self.reviews[job_id]
        before = shown[1]
        return what_moved(before, request) if before is not None else None

    def go(self, job_id: str) -> str:
        """On without the model's questions: the queue's own `go` (mqucifer/print-shop#208)."""
        if self.queue is None:
            return "the queue isn't running"
        return self.queue.go(job_id)

    def reading(self, job_id: str) -> float | None:
        return self.queue.reading(job_id) if self.queue is not None else None

    # -- print again (mqucifer/print-shop#216's, as `again` does it) -----------

    @contextmanager
    def again_of(self, run_id: str) -> Iterator[reprint.Reprint | str]:
        """The run, found to print again, or why it can't be: the ledger read-only, and a run
        from before has its profiles composed in a scratch folder that's gone after, so that
        showing it writes nothing (a GET changes nothing)."""
        if self.runs is None:
            yield "this run can't print a run again: `again` at the Mac's queue does"
            return
        runs = self.runs
        with tempfile.TemporaryDirectory(prefix="phone-again-") as scratch, \
                self.ledger() as ledger:
            if ledger is None:
                yield "there's no ledger yet"
                return
            try:
                found: reprint.Reprint | str = reprint.find(
                    ledger, runs.jobs, run_id, store_root=runs.store_root, bed=runs.bed,
                    parts=runs.parts, scratch=Path(scratch))
            except reprint.Refused as exc:
                found = str(exc)
            yield found

    def again(self, run_id: str, typed: str = "", *, also: bool = False,
              spool_id: int | None = None) -> Queued | AlsoDiffers | str:
        """A finished run put in the queue, as `again` does it: as it was where nothing is
        typed, else sliced again from its part, pose, profiles and settings with `typed` over
        them (`key=value; ...`, as `again>` reads it), and compared with its G-code. What
        differs beyond what was changed is asked once (`also`). Only queued: Start is still a
        person's, at the gate."""
        if self.queue is None or self.runs is None:
            return "the queue isn't running: start it at the Mac with `print_job.py --queue`"
        with self.again_of(run_id) as found:
            if isinstance(found, str):
                return found
            return self._again(found, typed, also=also, spool_id=spool_id)

    def _again(self, found: reprint.Reprint, typed: str, *, also: bool,
               spool_id: int | None) -> Queued | AlsoDiffers | str:
        assert self.queue is not None and self.runs is not None
        said: list[str] = []
        try:
            if not typed:
                chosen = found.as_it_was()
            else:
                changes = reprint.parse_changes(typed, found)
                if isinstance(changes, str):
                    return changes
                folder = reprint.slice_folder(self.runs.jobs, found.row)
                try:
                    made = found.change(changes, folder, self.runs.slicer)
                except reprint.Refused:
                    reprint.clear(folder)
                    raise
                if made.beyond and not also:
                    reprint.clear(folder)
                    return AlsoDiffers(tuple(made.lines()),
                                       tuple(d.line() for d in made.beyond))
                said = made.lines()
                chosen = found.changed(made)
        except reprint.Refused as exc:
            return str(exc)
        added, refused = self.queue.add([Adding(chosen.path, spool_id=spool_id, again=chosen)])
        if not added:
            return "; ".join(refused) or "it wasn't added"
        return Queued((f"In the queue: a print again of job {found.row.job_id[:8]}. Start is "
                       "still yours, at the gate.", *said, *refused), added[0])

    # -- the stock ------------------------------------------------------------

    def stock_changes(self) -> int:
        """How many times what's loaded, or what isn't known, has changed: a load from a page
        counts only while it's the same as when the page was read."""
        with self.ledger() as ledger:
            if ledger is None:
                return 0
            found = ledger.query("SELECT COALESCE(MAX(n), 0) AS n FROM stock_changes")
        return int(found[0]["n"]) if found else 0

    def load(self, spool_id: int, *, changes: int, answer: str = "") -> LoadAsk | list[str] | str:
        """A spool loaded, as `stock.py load` does it: where what's on isn't known since a print,
        the stock's question first, which says both halves; "both" says that print is over and
        this spool is on, and "alone" records the load alone, as any other answer at the
        terminal does. `changes` is the stock as the page was read: a change since refuses it.
        Returns the question, what was done a line each, or why not."""
        try:
            with Inventory(self.ledger_path) as stock:
                printer = stock.printer(None)
                spool = stock.spool(spool_id)
                stands = stock.asked(printer.id)
                if stands.changes != changes:
                    return LOOK_AGAIN
                found = stands.not_known
                if found is not None and spool.state is not SpoolState.EMPTY \
                        and answer not in ("both", "alone"):
                    return LoadAsk(spool, found.over_and_on(spool.named()), stands.changes)
                over = found if found is not None and answer == "both" else None
                loaded = stock.load(spool.id, printer.id, over=over,
                                    asked=StockAsked(changes, found))
                lines = [f"{spool_words(loaded)} is loaded on {printer.id}."]
                if (still := stock.unknown(printer.id)) is not None:
                    lines.append(f"print-shop still doesn't know what's on {printer.id}, as "
                                 f"nothing said {still.print_named()} is over: jobs don't "
                                 "start.")
                return lines
        except InventoryError as exc:
            return str(exc)

    # -- the outcome ----------------------------------------------------------

    def choices(self, job_id: str) -> dict[str, Pick]:
        return self.daemon.outcome_choices(job_id) if self.daemon is not None else {}

    def record(self, job_id: str, *, result: str, symptom: str, note: str, pick: str,
               picked: Pick | None) -> str:
        if self.daemon is None:
            return "this run can't record an outcome: record it on the card"
        return self.daemon.record_outcome(job_id, result=result, symptom=symptom, note=note,
                                          pick=pick, picked=picked)
