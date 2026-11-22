"""One live `print_job.py` at a time, and what a second start is told (mqucifer/print-shop#207).

Every process uses the same gate, command and availability topics, and the same ledger. A second
one took down the first's approval card just by starting, and its exit published the device
offline. So a job and `--serve` first take a lock, before anything is published, built or written
for them. Commands that only read don't take it.

**The lock is the operating system's** (`flock` on `jobs/live.lock`). It goes when its process
does, however that ends, so there's nothing to clean up. The file is never deleted: a lock on a
deleted file would keep nobody out.

**The file also says who holds it,** for the refusal to repeat: the job, its part and its step up
to the printer. From there the ledger knows more than the holder wrote, whether it's printing or
ended and waiting for its outcome, so the refusal reads that, read-only. What's written is read
only after the lock is refused, so a killed process's last words are never taken for a live one's.
"""

from __future__ import annotations

import fcntl
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from print_shop.ledger import Ledger, LedgerError, Row

__all__ = ["LOCK_NAME", "AlreadyLive", "Holder", "Live", "Step", "only_live"]

logger = logging.getLogger(__name__)

#: The lock's name in the jobs folder, which every checkout on the machine shares.
LOCK_NAME = "live.lock"

#: How long a refused start tries again while the holder hasn't said who it is: it has only just
#: taken the lock, or is letting it go.
_SETTLE_S = 1.0


class Step(StrEnum):
    """Where a job is before the ledger has a row for it, as the refusal says it."""

    PREPARING = "preparing"
    AT_GATE = "at the gate, waiting for Approve or Reject"
    SENDING = "approved and being sent to the printer"
    ENDING = "ending"


class Holder(BaseModel):
    """What the live process writes in the lock's file."""

    pid: int
    ledger: Path
    #: None for `--serve`, which has no job of its own.
    part: str | None = None
    #: Empty until the job is submitted and has an id.
    job_id: str = ""
    step: Step = Step.PREPARING


class AlreadyLive(Exception):
    """Another process is live. The message is the sentence a second start says."""


class Live:
    """The lock, held: where the job records its step."""

    def __init__(self, fd: int, holder: Holder) -> None:
        self._fd: int | None = fd
        self._holder = holder
        # The job's worker and the command's own thread both end up here.
        self._mutex = threading.Lock()
        self._write()

    def at(self, job_id: str, part: str, step: Step) -> None:
        """The job is at `step` now."""
        with self._mutex:
            self._holder = self._holder.model_copy(
                update={"job_id": job_id, "part": part, "step": step}
            )
            self._write()

    def _write(self) -> None:
        if self._fd is None:
            return  # let go: the descriptor's number may be another file's by now
        said = self._holder.model_dump_json().encode()
        try:
            os.pwrite(self._fd, said, 0)
            os.ftruncate(self._fd, len(said))
        except OSError:
            # The lock still holds. Only the refusal's wording is the poorer for it.
            logger.warning("couldn't write what's live to the lock's file", exc_info=True)

    def release(self) -> None:
        with self._mutex:
            fd, self._fd = self._fd, None
            if fd is None:
                return
            try:
                os.ftruncate(fd, 0)
            finally:
                os.close(fd)  # which lets the lock go


@contextmanager
def only_live(work: Path, ledger: Path, part: str | None) -> Iterator[Live]:
    """Hold the one live lock of `work`, the jobs folder, for a job on `part`, or for `--serve`
    (`part` None).

    Raises:
        AlreadyLive: If another process holds it, saying what that one is doing.
        OSError: If the lock can't be taken at all. Nothing runs without it.
    """
    live = Live(_take(work), Holder(pid=os.getpid(), ledger=ledger.absolute(), part=part))
    try:
        yield live
    finally:
        live.release()


def _take(work: Path) -> int:
    """The lock file's descriptor, locked."""
    work.mkdir(parents=True, exist_ok=True)
    # Not inherited by what the job runs (Orca): a child can't keep a dead job's lock.
    fd = os.open(work / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        give_up = time.monotonic() + _SETTLE_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    holder = Holder.model_validate_json(os.pread(fd, 65536, 0))
                except ValueError:  # nothing written yet, or half written
                    if time.monotonic() < give_up:
                        time.sleep(0.05)
                        continue
                    raise AlreadyLive(
                        "Not started, as only one runs at a time and another print_job.py is live."
                    ) from None
                raise AlreadyLive(_refusal(holder)) from None
            # A killed holder's words go at once, before anyone can read them as this one's.
            os.ftruncate(fd, 0)
            return fd
    except BaseException:
        os.close(fd)
        raise


def _refusal(holder: Holder) -> str:
    """The sentence a second start says."""
    start = "Not started, as only one runs at a time:"
    if holder.part is None:
        return (f"{start} print_job.py --serve is running (process {holder.pid}), "
                f"{_served(holder.ledger)}.")
    doing: str = holder.step
    if holder.job_id:
        who = f"job {holder.job_id} ({_one_line(holder.part)})"
        row = _row(holder.ledger, holder.job_id)
        if row is not None:
            doing = _printed(row)
    else:
        who = f"a job for {_one_line(holder.part)}"
    return f"{start} {who} is {doing}, in process {holder.pid}."


def _printed(row: Row) -> str:
    """Where a job with a ledger row is. Its row is written before the printer is told to start."""
    if row.result is not None:
        return str(Step.ENDING)
    if row.ended_at is not None:
        return "waiting for an outcome"
    if row.started_at is not None:
        return "printing"
    if row.start_error is not None:
        return "waiting on a start the printer didn't confirm"
    return str(Step.SENDING)


def _row(ledger: Path, job_id: str) -> Row | None:
    try:
        with Ledger.read_only(ledger) as read:
            return read.get(job_id)
    except (LedgerError, sqlite3.Error, ValueError):
        return None  # the step the holder wrote is said instead


def _served(ledger: Path) -> str:
    """What `--serve` is serving, from the ledger."""
    try:
        with Ledger.read_only(ledger) as read:
            if running := read.running():
                row, doing = running[0], "printing"
            elif waiting := read.awaiting_answer():
                row, doing = waiting[0], "waiting for an outcome"
            else:
                return "with no print open"
    except (LedgerError, sqlite3.Error, ValueError):
        return "watching for outcomes"
    return f"and job {row.job_id} ({_one_line(row.stl_name)}) is {doing}"


def _one_line(name: str) -> str:
    return " ".join(name.split())
