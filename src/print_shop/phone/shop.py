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

from print_shop.desk.read import Desk, OnHand
from print_shop.inventory import Material, Pick, Spool
from print_shop.ledger import Ledger, LedgerError, Row
from print_shop.orchestrator.kept import WaitingJob, Waits
from print_shop.orchestrator.print_queue import Line, SentTo
from print_shop.setup.pauses import said as pause_said

__all__ = [
    "Daemonish", "NowPrinting", "Queueish", "ReadyGroup", "ReadyRow", "Shop", "ToRecord",
    "spool_words",
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


@dataclass
class Shop:
    """The run's queue and daemon, and where the ledger is: what the screens read.

    `queue` is None in a run without one (a lone job's), and `daemon` None where only the
    ledger is read."""

    ledger_path: Path
    queue: Queueish | None = None
    daemon: Daemonish | None = None
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
        for p in row.reading.pauses if row.reading else ():
            ahead = p.after_s - elapsed
            if ahead > 0:
                what = pause_said(p.kind)
                to = f" to {p.to}" if p.to else ""
                pause = (f"Pauses in {_minutes(ahead)} min for {what}{to}. "
                         "Be at the printer.")
                break
        return NowPrinting(row.job_id, row.part_said, said, pause)

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
            out.append(ReadyGroup(named, members[0].loaded, tuple(rows)))
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

    # -- the outcome ----------------------------------------------------------

    def choices(self, job_id: str) -> dict[str, Pick]:
        return self.daemon.outcome_choices(job_id) if self.daemon is not None else {}

    def record(self, job_id: str, *, result: str, symptom: str, note: str, pick: str,
               picked: Pick | None) -> str:
        if self.daemon is None:
            return "this run can't record an outcome: record it on the card"
        return self.daemon.record_outcome(job_id, result=result, symptom=symptom, note=note,
                                          pick=pick, picked=picked)
