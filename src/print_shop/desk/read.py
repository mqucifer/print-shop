"""What the desk reads: the inventory, the ledger's jobs and each job's folder, read-only.

The ledger is opened read-only for each view (`Ledger.read_only`, SQLite's `mode=ro`) and closed
after it, so the desk holds nothing open between views and can't change a row. The jobs folder
is only listed and read. A job id from a URL is checked to be a plain name before any path is
made from it, and a frame is served by its number in the ledger, never by a path from the URL.

**A past job's part is found by its hash** (`PartFinder`): the ledger keeps each part's sha256, so
the file to print again is the one with the same bytes, wherever it is now: at the path its log
names, else under the parts folder (`PRINT_SHOP_PARTS` in the settings, default
`~/print-shop/parts`, and its subfolders), else in the job's own folder. A file is hashed once
for each size and change time, so a page doesn't read every part again.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from print_shop.env import load_env
from print_shop.inventory import Nozzle, Printer, Spool
from print_shop.joblog import LOG_NAME
from print_shop.ledger import Frame, Ledger, LedgerError, Row, UnprintedJob
from print_shop.orchestrator.frames import FRAMES_DIR
from print_shop.setup.proposal import PAUSE

__all__ = [
    "PARTS_DIR", "Desk", "JobRecord", "JobSummary", "OnHand", "PartFinder", "PrinterNow",
    "parts_dir", "plain_id",
]

#: Where the Sponsor's part files are kept, searched with its subfolders for a past job's part.
PARTS_DIR = Path("~/print-shop/parts").expanduser()

#: The settings key that moves it.
PARTS_KEY = "PRINT_SHOP_PARTS"

#: The files a part can be: what `print_job.py` slices.
_PART_SUFFIXES = (".stl", ".3mf")

#: A job id as the daemon makes them (a uuid), or any plain name: nothing a path can climb out
#: of the jobs folder with.
_PLAIN = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

#: The log's line naming the part's full path (`scripts/print_job.py`'s header).
_PART = re.compile(r"^part: (.+)$", re.M)


def plain_id(job_id: str) -> bool:
    return _PLAIN.fullmatch(job_id) is not None


def parts_dir(settings: Path) -> Path:
    """The parts folder: `PRINT_SHOP_PARTS` in the settings, or the default."""
    value = load_env(settings).get(PARTS_KEY)
    return Path(value).expanduser() if value else PARTS_DIR


class PartFinder:
    """Part files by their sha256, each file hashed once while its size and change time hold."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hashes: dict[Path, tuple[int, int, str]] = {}

    def sha256(self, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = (stat.st_size, stat.st_mtime_ns)
        with self._lock:
            known = self._hashes.get(path)
        if known is not None and known[:2] == key:
            return known[2]
        digest = hashlib.sha256()
        try:
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    digest.update(block)
        except OSError:
            return None
        found = digest.hexdigest()
        with self._lock:
            self._hashes[path] = (*key, found)
        return found

    def find(self, sha256: str, named: Path | None, folders: tuple[Path, ...]) -> Path | None:
        """The file with these bytes: `named` if it still has them, else the first under
        `folders`, in order, by path."""
        if named is not None and named.is_file() and self.sha256(named) == sha256:
            return named
        for folder in folders:
            if not folder.is_dir():
                continue
            files = sorted(p for p in folder.rglob("*")
                           if p.suffix.lower() in _PART_SUFFIXES and p.is_file())
            for path in files:
                if self.sha256(path) == sha256:
                    return path
        return None


@dataclass(frozen=True)
class PrinterNow:
    """A printer, the nozzle fitted on it and the spool loaded on it."""

    printer: Printer
    nozzle: Nozzle | None
    spool: Spool | None


@dataclass(frozen=True)
class OnHand:
    """What's on the printers, and every nozzle and spool on hand."""

    printers: tuple[PrinterNow, ...]
    nozzles: tuple[Nozzle, ...]
    spools: tuple[Spool, ...]


@dataclass(frozen=True)
class JobSummary:
    """One line of the job list."""

    job_id: str
    at: str  # ISO-8601 UTC: asked to start, ended unprinted, or the log's last write
    part: str
    status: str
    print_time_s: int | None = None
    filament_cm3: float | None = None
    swap: bool = False
    frames: int = 0
    failed: bool = False


@dataclass(frozen=True)
class JobRecord:
    """One job, everything the desk shows of it."""

    job_id: str
    row: Row | None = None
    unprinted: UnprintedJob | None = None
    frames: tuple[Frame, ...] = ()
    frames_missed: str | None = None
    log: str | None = None
    #: The part's file: the one with its bytes (`PartFinder`), else the path its log names, else
    #: None.
    part_path: Path | None = None
    #: Whether `part_path` holds the bytes the job printed, by their sha256.
    part_found: bool = False
    printers: int = 0


def status(row: Row) -> str:
    """Where a print is, in a few words."""
    if row.result is not None:
        return f"{row.result}" + (f", {row.symptom}" if row.symptom and row.symptom != "none"
                                  else "")
    if row.ended_at is not None:
        return "ended, waiting for your answer"
    if row.started_at is not None:
        return "printing"
    if row.start_error is not None:
        return "the start failed"
    return "sent, not seen to start"


def has_swap(row: Row) -> bool:
    """A colour change asked for, or written into the slice."""
    if row.reading is not None and row.reading.pauses:
        return True
    change = row.proposal.get(PAUSE) if row.proposal is not None else None
    return change is not None and change.value != "none"


class Desk:
    """The ledger file and the jobs folder, read for each view."""

    def __init__(self, ledger: Path, jobs: Path, parts: Path | None = None) -> None:
        self.ledger_path = ledger
        self.jobs_dir = jobs
        self.parts_dir = parts if parts is not None else PARTS_DIR
        self._finder = PartFinder()

    @contextmanager
    def _ledger(self) -> Iterator[Ledger | None]:
        """The ledger, read-only, for one view; None when there's no file yet."""
        try:
            ledger = Ledger.read_only(self.ledger_path)
        except LedgerError:
            if self.ledger_path.exists():
                raise
            yield None
            return
        try:
            yield ledger
        finally:
            ledger.close()

    # --- what's on hand ------------------------------------------------------

    def on_hand(self) -> OnHand:
        with self._ledger() as ledger:
            if ledger is None:
                return OnHand((), (), ())
            printers = [Printer(**r) for r in ledger.query(
                "SELECT id, model FROM printers ORDER BY id")]
            nozzles = tuple(Nozzle(**r) for r in ledger.query(
                "SELECT id, diameter_mm, note, printer_id FROM nozzles ORDER BY diameter_mm, id"))
            spools = tuple(Spool(**r) for r in ledger.query(
                "SELECT id, manufacturer, type, variety, colour, grams_left, tuning, state, "
                "printer_id FROM spools WHERE state != 'empty' ORDER BY type, variety, id"))
        return OnHand(
            printers=tuple(
                PrinterNow(
                    printer=p,
                    nozzle=next((n for n in nozzles if n.printer_id == p.id), None),
                    spool=next((s for s in spools if s.printer_id == p.id), None),
                )
                for p in printers
            ),
            nozzles=nozzles,
            spools=spools,
        )

    # --- the jobs ------------------------------------------------------------

    def jobs(self) -> list[JobSummary]:
        """Every job the ledger or the jobs folder knows, newest first."""
        found: list[JobSummary] = []
        with self._ledger() as ledger:
            rows = ledger.rows() if ledger is not None else []
            unprinted = ledger.unprinted_jobs() if ledger is not None else []
            frames = {
                r["job_id"]: int(r["n"]) for r in ledger.query(
                    "SELECT job_id, count(*) AS n FROM print_frames GROUP BY job_id")
            } if ledger is not None else {}
        for row in rows:
            found.append(JobSummary(
                job_id=row.job_id, at=row.start_requested_at, part=row.stl_name,
                status=status(row), print_time_s=row.print_time_s,
                filament_cm3=row.filament_cm3, swap=has_swap(row),
                frames=frames.get(row.job_id, 0), failed=row.result == "failed",
            ))
        for job in unprinted:
            found.append(JobSummary(job_id=job.job_id, at=job.ended_at, part=job.stl_name,
                                    status=f"ended unprinted: {job.outcome}"))
        known = {j.job_id for j in found}
        for log in self._logs():
            job_id = log.parent.name
            if job_id in known:
                continue
            part = _part(log.read_text(encoding="utf-8", errors="replace"))
            at = datetime.fromtimestamp(log.stat().st_mtime, UTC).isoformat(timespec="seconds")
            found.append(JobSummary(
                job_id=job_id, at=at, part=part.name if part else "?",
                status="never sent to the printer: its log only",
            ))
        return sorted(found, key=lambda j: j.at, reverse=True)

    def _logs(self) -> list[Path]:
        if not self.jobs_dir.is_dir():
            return []
        return [p / LOG_NAME for p in self.jobs_dir.iterdir()
                if plain_id(p.name) and (p / LOG_NAME).is_file()]

    def job(self, job_id: str) -> JobRecord | None:
        """One job, or None if neither the ledger nor the jobs folder has it."""
        if not plain_id(job_id):
            return None
        with self._ledger() as ledger:
            row = ledger.get(job_id) if ledger is not None else None
            unprinted = ledger.unprinted(job_id) if ledger is not None else None
            frames = tuple(ledger.frames(job_id)) if ledger is not None else ()
            missed = ledger.frames_missed(job_id) if ledger is not None else None
            printers = (len(ledger.query("SELECT id FROM printers"))
                        if ledger is not None else 0)
        log = self.log_path(job_id)
        text = log.read_text(encoding="utf-8", errors="replace") if log is not None else None
        if row is None and unprinted is None and text is None:
            return None
        named = _part(text) if text is not None else None
        found = (self._finder.find(row.stl_sha256, named,
                                   (self.parts_dir, self.jobs_dir / job_id))
                 if row is not None else None)
        return JobRecord(
            job_id=job_id, row=row, unprinted=unprinted, frames=frames, frames_missed=missed,
            log=text, part_path=found or named, part_found=found is not None, printers=printers,
        )

    def log_path(self, job_id: str) -> Path | None:
        if not plain_id(job_id):
            return None
        path = self.jobs_dir / job_id / LOG_NAME
        return path if path.is_file() else None

    def frame_path(self, job_id: str, n: int) -> Path | None:
        """The `n`th frame's file (from 0), in the job's own frames folder, if it's there."""
        record = self.job(job_id)
        if record is None or not 0 <= n < len(record.frames):
            return None
        path = self.jobs_dir / job_id / FRAMES_DIR / Path(record.frames[n].path).name
        return path if path.is_file() else None


def _part(log: str) -> Path | None:
    found = _PART.search(log)
    return Path(found.group(1).strip()) if found else None
