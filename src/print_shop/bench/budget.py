"""What a host can run at once: its budget, and the slots jobs take (mqucifer/print-shop#364).

The Sponsor, 2026-11-27: "I just want to ensure we're actually maximizing our available
processing across many machines with different resources." Each bench command runs in a
container of its own limits (`host.container`): its memory, 3g, or 4g for `load --parts`
(`host.memory_for`), and its solves kept to one thread so its figures are the same every time
(mqucifer/print-shop#348). So a host runs as many at once as its memory and cores hold, and each
one's figures are what it would read alone.

**The budget is read, not guessed:** Docker's own memory and CPUs (`docker info`), less what's
kept for other work on the host, a design run's bench or a builder's tests. What's kept is stated
in one place, the Mac's settings (`BENCH_RESERVE_MEMORY`, `BENCH_RESERVE_CPUS`), or given; where
it's neither, one bench command's own limits, `host.MEMORY` and `host.CPUS`, so one more command
can always run beside it. A job takes its memory and one core while it runs: a bench command its
container's memory, and an Orca slice, which runs on the host outside Docker, a core alone.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import host

__all__ = ["RESERVE_CPUS", "RESERVE_MEMORY", "Budget", "Slots", "megabytes", "reserved"]

#: The settings that state what's kept for other work on the host, and what's kept where they
#: don't: one bench command's own limits.
RESERVE_MEMORY = "BENCH_RESERVE_MEMORY"
RESERVE_CPUS = "BENCH_RESERVE_CPUS"
_UNITS = {"": 1 / 1024**2, "b": 1 / 1024**2, "k": 1 / 1024, "m": 1.0, "g": 1024.0, "t": 1024.0**2}


def megabytes(size: str | int) -> int:
    """A size as Docker takes it (`3g`, `512m`, or bytes as a number) in MiB.

    Raises:
        ValueError: If it isn't one.
    """
    if isinstance(size, int):
        return size // 1024**2
    found = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([bkmgt]?)i?b?\s*", size.lower())
    if not found:
        raise ValueError(f"{size!r} isn't a size, as 3g or 512m")
    return int(float(found.group(1)) * _UNITS[found.group(2)])


def reserved(env: dict[str, str], memory: str | None = None,
             cpus: int | None = None) -> tuple[int, int]:
    """What's kept for other work, in MiB and CPUs: as given, else as the settings state it,
    else one bench command's own limits.

    Raises:
        ValueError: If a setting isn't a size or a count.
    """
    said_memory = memory or env.get(RESERVE_MEMORY) or host.MEMORY
    said_cpus = cpus if cpus is not None else env.get(RESERVE_CPUS) or host.CPUS
    try:
        count = int(said_cpus)
    except ValueError as exc:
        raise ValueError(f"{RESERVE_CPUS} is a count of CPUs, not {said_cpus!r}") from exc
    return megabytes(said_memory), count


@dataclass(frozen=True)
class Budget:
    """A host's memory and CPUs for jobs, and what's kept from them for other work."""

    memory_mb: int
    cpus: int
    reserved_memory_mb: int = 0
    reserved_cpus: int = 0
    #: Where the memory and CPUs were read from.
    source: str = "given"

    @classmethod
    def docker(cls, reserved_memory_mb: int, reserved_cpus: int) -> Budget:
        """Docker's own memory and CPUs on this host, less what's kept.

        Raises:
            RuntimeError: If Docker doesn't say.
        """
        done = subprocess.run(["docker", "info", "--format", "{{.MemTotal}} {{.NCPU}}"],
                              capture_output=True, text=True, timeout=60, check=False)
        words = done.stdout.split()
        if done.returncode != 0 or len(words) != 2 or not all(w.isdigit() for w in words):
            said = (done.stderr or done.stdout).strip()[-200:]
            raise RuntimeError(f"Docker didn't say its memory and CPUs: {said or 'no reply'}")
        return cls(int(words[0]) // 1024**2, int(words[1]), reserved_memory_mb, reserved_cpus,
                   "docker info")

    @classmethod
    def serial(cls) -> Budget:
        """One job at a time, whatever its memory."""
        return cls(10**9, 1)

    @property
    def free_memory_mb(self) -> int:
        return max(0, self.memory_mb - self.reserved_memory_mb)

    @property
    def free_cpus(self) -> int:
        return max(0, self.cpus - self.reserved_cpus)

    def at_once(self, memory_mb: int) -> int:
        """How many jobs of `memory_mb` and a core each run at once."""
        by_memory = self.free_memory_mb // memory_mb if memory_mb else self.free_cpus
        return min(by_memory, self.free_cpus)

    def said(self, memory: str = host.MEMORY) -> str:
        if self == Budget.serial():
            return "one job at a time"
        return (f"{self.memory_mb / 1024:.1f} GiB and {self.cpus} CPUs by {self.source}, less "
                f"{self.reserved_memory_mb / 1024:.1f} GiB and {self.reserved_cpus} CPUs kept for "
                f"other work: {self.at_once(megabytes(memory))} bench commands of {memory} at once")


class Slots:
    """The jobs running on a host, each holding its memory and a core of the budget until it
    ends: a job waits until both are free. One that the whole budget can't hold is refused."""

    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self._lock = threading.Condition()
        self._memory = 0
        self._cpus = 0
        #: The most jobs that ran at once, and their memory then.
        self.most = 0

    @contextmanager
    def take(self, memory_mb: int = 0) -> Iterator[None]:
        """Hold `memory_mb` and a core while the block runs.

        Raises:
            ValueError: If the budget can't hold it at all, saying what it holds.
        """
        if memory_mb > self.budget.free_memory_mb or self.budget.free_cpus < 1:
            raise ValueError(f"a job of {memory_mb} MiB and a core is past the budget: "
                             f"{self.budget.free_memory_mb} MiB and {self.budget.free_cpus} CPUs "
                             "free of what's kept for other work")
        with self._lock:
            while (self._memory + memory_mb > self.budget.free_memory_mb
                   or self._cpus + 1 > self.budget.free_cpus):
                self._lock.wait()
            self._memory += memory_mb
            self._cpus += 1
            self.most = max(self.most, self._cpus)
        try:
            yield
        finally:
            with self._lock:
                self._memory -= memory_mb
                self._cpus -= 1
                self._lock.notify_all()
