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
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from print_shop import reprint
from print_shop.desk.read import Desk, OnHand
from print_shop.inventory import (
    Asked,
    Inventory,
    InventoryError,
    Material,
    Pick,
    Spool,
    SpoolState,
)
from print_shop.ledger import Ledger, LedgerError, Row
from print_shop.orchestrator.kept import WaitingJob, Waits
from print_shop.orchestrator.print_queue import Adding, Line, SentTo
from print_shop.queue_terminal import Runs
from print_shop.setup.pauses import said as pause_said
from print_shop.setup.reading import PauseReading

__all__ = [
    "LOOK_AGAIN", "AlsoDiffers", "Daemonish", "LoadAsk", "NowPrinting", "Queued", "Queueish",
    "ReadyGroup", "ReadyRow", "Shop", "ToRecord", "spool_words",
]

logger = logging.getLogger(__name__)


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
    """A print again, in the queue: what was said of it."""

    lines: tuple[str, ...]


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

    # -- print again (mqucifer/print-shop#216's, as `again` does it) -----------

    def reprint(self, run_id: str) -> reprint.Reprint | str:
        """The run, found to print again, or why it can't be."""
        if self.runs is None:
            return "this run can't print a run again: `again` at the Mac's queue does"
        runs = self.runs
        try:
            with Ledger(runs.ledger_path) as ledger:
                return reprint.find(ledger, runs.jobs, run_id, store_root=runs.store_root,
                                    bed=runs.bed, parts=runs.parts)
        except reprint.Refused as exc:
            return str(exc)

    def again(self, run_id: str, typed: str = "", *, also: bool = False,
              spool_id: int | None = None) -> Queued | AlsoDiffers | str:
        """A finished run put in the queue, as `again` does it: as it was where nothing is
        typed, else sliced again from its part, pose, profiles and settings with `typed` over
        them (`key=value; ...`, as `again>` reads it), and compared with its G-code. What
        differs beyond what was changed is asked once (`also`). Only queued: Start is still a
        person's, at the gate."""
        if self.queue is None or self.runs is None:
            return "the queue isn't running: start it at the Mac with `print_job.py --queue`"
        found = self.reprint(run_id)
        if isinstance(found, str):
            return found
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
                       "still yours, at the gate.", *said, *refused))

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
                                    asked=Asked(changes, found))
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
