"""What a host can run at once: its budget, and the slots jobs take (mqucifer/print-shop#364).

The Sponsor, 2026-11-27: "I just want to ensure we're actually maximizing our available
processing across many machines with different resources." Each bench command runs in a
container of its own limits (`host.container`): its memory, 3g, or 4g for `load --parts`
(`host.memory_for`), and two CPUs, its solves kept to one thread so its figures are the same
every time (mqucifer/print-shop#348). So a host runs as many at once as its memory and cores
hold.

**The budget is read, not guessed:** Docker's own memory and CPUs (`docker info`), less what's
kept for other work on the host, a design run's bench or a builder's tests. What's kept is stated
in one place, the Mac's settings (`BENCH_RESERVE_MEMORY`, `BENCH_RESERVE_CPUS`), or given; where
it's neither, one bench command's own limits, `host.MEMORY` and `host.CPUS`, so one more command
can always run beside it.

**What Docker's memory holds besides:** the VM's own use, and containers with no limit of their
own (a LiteLLM, its database), are measured as the budget's margin, as it's read: what the VM has
in use (`/proc/meminfo` in a container) less what the bench's and the rounds' containers then use
(`docker stats`). And as each job takes its slot, the memory limits of the bench's and the
rounds' containers that other processes are running (`docker ps`, `print-bench-` and
`print-rounds-`) are taken from what's free: another session's `load` holds its 3g whether it
uses it yet or not.

**A job holds its cores:** a bench command its container's two (`host.CPUS`); an Orca slice, which
runs on the host outside Docker with as many threads as it likes and no option to say fewer,
`SLICE_CORES`, as measured. A job never holds more than all of them, so it runs alone where
they're fewer.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from . import host

__all__ = ["LOAD_CORES", "RESERVE_CPUS", "RESERVE_MEMORY", "SLICE_CORES", "Budget", "Slots",
           "megabytes", "others_mb", "reserved"]

#: The settings that state what's kept for other work on the host, and what's kept where they
#: don't: one bench command's own limits.
RESERVE_MEMORY = "BENCH_RESERVE_MEMORY"
RESERVE_CPUS = "BENCH_RESERVE_CPUS"
#: The cores a bench command holds: its container's.
LOAD_CORES = int(host.CPUS)
#: The cores an Orca slice holds. Orca 2.4.2 slices with as many threads as it likes, and its CLI
#: has no option for fewer (`--help`, read 2026-11-27): slicing a column 80 mm across and 200 mm
#: tall with four holes on the Mac, it kept 2.25 cores busy on average in rectilinear and 2.64 in
#: gyroid (its CPU time over its wall time), so it holds 3.
SLICE_CORES = 3
#: The prefixes of the containers the bench and the rounds run in (`host.run`, `rounds.box`).
CONTAINERS = ("print-bench-", "print-rounds-")
#: How often a job waiting for its slot looks again at what other processes are running, in s.
LOOK_AGAIN = 10.0
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


def _docker(*args: str) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60,
                          check=False)
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()[-200:]
        raise RuntimeError(f"docker {args[0]} didn't answer: {said or 'no reply'}")
    return done.stdout


def _ours(names: list[str]) -> list[str]:
    return [n for n in names if n.startswith(CONTAINERS)]


def others_mb(mine: set[str]) -> int:
    """The memory limits, in MiB, of the bench's and the rounds' containers running on this host
    that aren't `mine`.

    Raises:
        RuntimeError: If Docker doesn't say.
    """
    names = [n for n in _ours(_docker("ps", "--format", "{{.Names}}").split()) if n not in mine]
    if not names:
        return 0
    limits = _docker("inspect", "--format", "{{.HostConfig.Memory}}", *names).split()
    return sum(int(v) for v in limits if v.isdigit()) // 1024**2


def _used_by_containers() -> int:
    """What the bench's and the rounds' running containers use now, in MiB."""
    lines = _docker("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}").splitlines()
    used = 0
    for line in lines:
        name, _, usage = line.partition(" ")
        if name.startswith(CONTAINERS):
            used += megabytes(usage.split("/")[0].strip())
    return used


@dataclass(frozen=True)
class Budget:
    """A host's memory and CPUs for jobs, and what's kept from them for other work."""

    memory_mb: int
    cpus: int
    reserved_memory_mb: int = 0
    reserved_cpus: int = 0
    #: Where the memory and CPUs were read from.
    source: str = "given"
    #: What Docker's memory holds besides jobs: the VM's own use and containers of no limit.
    margin_mb: int = 0

    @classmethod
    def docker(cls, reserved_memory_mb: int, reserved_cpus: int) -> Budget:
        """Docker's own memory and CPUs on this host, less what's kept, and the margin the VM's
        own use takes, measured now.

        Raises:
            RuntimeError: If Docker doesn't say.
        """
        words = _docker("info", "--format", "{{.MemTotal}} {{.NCPU}}").split()
        if len(words) != 2 or not all(w.isdigit() for w in words):
            raise RuntimeError(f"Docker didn't say its memory and CPUs: {' '.join(words)}")
        meminfo = _docker("run", "--rm", "--network", "none", "--memory", "64m", host.IMAGE,
                          "cat", "/proc/meminfo")
        found = dict(re.findall(r"^(\w+):\s+(\d+) kB", meminfo, re.MULTILINE))
        if "MemTotal" not in found or "MemAvailable" not in found:
            raise RuntimeError("the VM's /proc/meminfo didn't say its memory")
        in_use = (int(found["MemTotal"]) - int(found["MemAvailable"])) // 1024
        margin = max(0, in_use - _used_by_containers())
        return cls(int(words[0]) // 1024**2, int(words[1]), reserved_memory_mb, reserved_cpus,
                   "docker info", margin)

    @classmethod
    def serial(cls) -> Budget:
        """One job at a time, whatever its memory."""
        return cls(10**9, 1)

    @property
    def free_memory_mb(self) -> int:
        return max(0, self.memory_mb - self.reserved_memory_mb - self.margin_mb)

    @property
    def free_cpus(self) -> int:
        return max(0, self.cpus - self.reserved_cpus)

    def holds(self, cpus: int) -> int:
        """The cores a job of `cpus` holds: its own, or all there are where they're fewer."""
        return max(1, min(cpus, self.free_cpus))

    def at_once(self, memory_mb: int, cpus: int = LOAD_CORES) -> int:
        """How many jobs of `memory_mb` and `cpus` each run at once, with nothing else running."""
        by_memory = self.free_memory_mb // memory_mb if memory_mb else self.free_cpus
        return min(by_memory, self.free_cpus // self.holds(cpus))

    def said(self, memory: str = host.MEMORY) -> str:
        if self == Budget.serial():
            return "one job at a time"
        return (f"{self.memory_mb / 1024:.1f} GiB and {self.cpus} CPUs by {self.source}, less "
                f"{self.reserved_memory_mb / 1024:.1f} GiB and {self.reserved_cpus} CPUs kept for "
                f"other work and {self.margin_mb / 1024:.1f} GiB the VM itself had in use: "
                f"{self.at_once(megabytes(memory))} bench commands of {memory} and "
                f"{LOAD_CORES} CPUs at once, an Orca slice holding {SLICE_CORES}")


class Slots:
    """The jobs running on a host, each holding its memory and its cores of the budget until it
    ends: a job waits until both are free, past what other processes' containers hold (`others`,
    MiB, asked again every `LOOK_AGAIN` s as it waits). One the whole budget can't hold is
    refused."""

    def __init__(self, budget: Budget, others: Callable[[], int] | None = None) -> None:
        self.budget = budget
        self.others = others
        self._lock = threading.Condition()
        self._memory = 0
        self._cpus = 0
        #: The most jobs that ran at once.
        self.most = 0
        self._running = 0

    @contextmanager
    def take(self, memory_mb: int = 0, cpus: int = 1) -> Iterator[None]:
        """Hold `memory_mb` and `cpus` while the block runs.

        Raises:
            ValueError: If the budget can't hold it at all, saying what it holds.
        """
        cores = self.budget.holds(cpus)
        if memory_mb > self.budget.free_memory_mb or self.budget.free_cpus < 1:
            raise ValueError(f"a job of {memory_mb} MiB is past the budget: "
                             f"{self.budget.free_memory_mb} MiB and {self.budget.free_cpus} CPUs "
                             "free of what's kept for other work and the VM's own")
        while True:
            elsewhere = self.others() if self.others is not None else 0
            with self._lock:
                if (self._memory + elsewhere + memory_mb <= self.budget.free_memory_mb
                        and self._cpus + cores <= self.budget.free_cpus):
                    self._memory += memory_mb
                    self._cpus += cores
                    self._running += 1
                    self.most = max(self.most, self._running)
                    break
                # Woken as one of its own ends; looks again at other processes' as it waits.
                self._lock.wait(timeout=LOOK_AGAIN if self.others is not None else None)
        try:
            yield
        finally:
            with self._lock:
                self._memory -= memory_mb
                self._cpus -= cores
                self._running -= 1
                self._lock.notify_all()
