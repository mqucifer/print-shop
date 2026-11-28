"""A study's bench jobs placed across this host and the remote hosts the settings name
(mqucifer/print-shop#373), each within its own budget.

Each host has its slots (`budget.Slots`): this one's read from Docker less what's kept, as
before (mqucifer/print-shop#364); a remote's as its settings state them, its memory for jobs and
its cores, each job given cores of its own from them to be pinned to (`remote.RemoteHost`). A
slice runs only here, where Orca is. A bench job goes to whichever host has room for it now, its
memory and cores free and, on a remote, its MemFree over its floor plus the job's limit
(`Remote.reserve`), read before it starts.

**Where there's room on more than one, the fastest for its kind, as measured:** each host's
seconds for each kind of job (its command's words, the same for every candidate under a case)
are kept as its jobs end, and a host not yet measured for a kind is tried first, so it is. Never
assumed from a host's cores or clock.

**Every figure is a local run's:** a remote runs the same image, by ID (`Remote.refused`), and
the same code, each solve single-threaded inside, so where a job runs changes only how long it
takes. A host that stops answering mid-job (`job.HostLost`) takes no more jobs, and the study
runs that one again elsewhere.

The pool is the study's slots (`take`) and its runner (`run`): a job runs on the host whose slot
its thread holds.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import host
from .budget import LOOK_AGAIN, Budget, Slots, megabytes
from .job import HERE, BenchJob, HostLost, JobResult, Runner, run_here, stop_here
from .remote import NoRoom, Remote

__all__ = ["Place", "Pool"]


@dataclass
class Place:
    """A host jobs are placed on: its slots, and its Docker where it's a remote."""

    name: str
    slots: Slots
    remote: Remote | None = None
    #: Why it takes no more jobs, where it doesn't: it stopped answering.
    down: str = ""
    #: The jobs it ran, and each kind's seconds.
    placed: int = 0
    seconds: dict[str, list[float]] = field(default_factory=dict)
    #: Where its free memory last held no more: not asked again until then, or a job there ends.
    no_room_until: float = 0.0
    no_room: str = ""
    refusals: int = 0

    def mean(self, kind: str) -> float | None:
        found = self.seconds.get(kind)
        return sum(found) / len(found) if found else None


@dataclass
class _Held:
    place: Place
    cores: tuple[int, ...]
    reserved: str | None
    kind: str | None


class Pool:
    """This host's slots and each remote's, and the runner that runs a job where its slot is."""

    def __init__(self, here: Slots, remotes: list[Remote] | None = None,
                 here_runner: Runner = run_here) -> None:
        self.places = [Place(HERE, here)]
        for remote in remotes or ():
            stated = remote.host
            slots = Slots(Budget(stated.memory_mb, len(stated.cores), source="its settings"),
                          cores=stated.cores)
            self.places.append(Place(remote.name, slots, remote))
        self.here_runner = here_runner
        self._lock = threading.Condition()
        self._local = threading.local()
        self._running = 0
        #: The most jobs that ran at once, on every host.
        self.most = 0

    @property
    def budget(self) -> Budget:
        """This host's budget."""
        return self.places[0].slots.budget

    def _order(self, places: list[Place], kind: str | None) -> list[Place]:
        """The places to try, the fastest for `kind` first, as measured; those not measured for
        it yet before those that are, so they are; else in the settings' order."""
        if kind is None:
            return places

        def key(item: tuple[int, Place]) -> tuple[int, float, int]:
            index, place = item
            mean = place.mean(kind)
            return (0, 0.0, index) if mean is None else (1, mean, index)

        return [p for _, p in sorted(enumerate(places), key=key)]

    def _try(self, place: Place, memory_mb: int, cpus: int, kind: str | None) -> _Held | None:
        if place.remote is not None and time.monotonic() < place.no_room_until:
            return None
        cores = place.slots.hold(memory_mb, cpus)
        if cores is None:
            return None
        if place.remote is None:
            return _Held(place, cores, None, kind)
        try:
            reserved = place.remote.reserve(memory_mb)
        except NoRoom as exc:
            place.slots.let_go(memory_mb, cpus, cores)
            with self._lock:
                place.no_room, place.refusals = str(exc), place.refusals + 1
                place.no_room_until = time.monotonic() + LOOK_AGAIN
            return None
        except HostLost as exc:
            place.slots.let_go(memory_mb, cpus, cores)
            with self._lock:
                place.down = str(exc)
            return None
        return _Held(place, cores, reserved, kind)

    @contextmanager
    def take(self, memory_mb: int = 0, cpus: int = 1,
             kind: str | None = None) -> Iterator[tuple[int, ...]]:
        """Hold `memory_mb` and `cpus` on a host while the block runs, and give it its cores
        where they're stated: here for anything that isn't a bench job (`kind` None, as a
        slice), else on the fastest host with room for it now. `run` runs its job there.

        Raises:
            ValueError: If no host's budget can hold it at all.
        """
        while True:
            with self._lock:
                seen = sum(p.slots.ended for p in self.places)
                places = [p for p in self.places if not p.down
                          and (kind is not None or p.remote is None)
                          and memory_mb <= p.slots.budget.free_memory_mb
                          and p.slots.budget.free_cpus >= 1]
            if not places:
                self.places[0].slots.refuse_past(memory_mb)
                raise ValueError(f"no host takes a job of {memory_mb} MiB: "
                                 + "; ".join(f"{p.name} {p.down}" for p in self.places[1:]))
            held = next((h for p in self._order(places, kind)
                         if (h := self._try(p, memory_mb, cpus, kind)) is not None), None)
            if held is not None:
                break
            with self._lock:
                if sum(p.slots.ended for p in self.places) == seen:
                    self._lock.wait(timeout=LOOK_AGAIN)
        with self._lock:
            self._running += 1
            self.most = max(self.most, self._running)
        stack = self._held_here()
        stack.append(held)
        try:
            yield held.cores
        finally:
            stack.remove(held)
            if held.reserved is not None and held.place.remote is not None:
                held.place.remote.release(held.reserved)
            held.place.slots.let_go(memory_mb, cpus, held.cores)
            with self._lock:
                self._running -= 1
                held.place.no_room_until = 0.0
                self._lock.notify_all()

    def _held_here(self) -> list[_Held]:
        """The slots this thread holds, the last it took last."""
        if not hasattr(self._local, "held"):
            self._local.held = []
        stack: list[_Held] = self._local.held
        return stack

    def where(self) -> str | None:
        """The host whose slot this thread holds last, or None."""
        stack = self._held_here()
        return stack[-1].place.name if stack else None

    def run(self, job: BenchJob) -> JobResult:
        """Run a job on the host whose slot this thread holds (`take`), and keep how long it
        took there for its kind.

        Raises:
            RuntimeError: If this thread holds no slot.
            HostLost: If the host stopped answering: it takes no more jobs.
        """
        stack = self._held_here()
        if not stack:
            raise RuntimeError("a job runs on the host whose slot its thread holds: take one")
        held = stack[-1]
        place = held.place
        began = time.monotonic()
        try:
            if place.remote is None:
                done = self.here_runner(job)
            else:
                done = place.remote.run(job, held.cores, held.reserved)
        except HostLost as exc:
            with self._lock:
                place.down = str(exc)
            raise
        seconds = time.monotonic() - began
        with self._lock:
            place.placed += 1
            if done.saved and held.kind is not None:
                place.seconds.setdefault(held.kind, []).append(seconds)
        return done

    def stop(self) -> None:
        """Stop every container this process's jobs are running, here and on each remote."""
        stop_here()
        for place in self.places:
            if place.remote is not None:
                place.remote.stop()

    def left(self) -> list[str]:
        """Each remote's volumes this process couldn't remove, by host: none, as a rule."""
        return [f"{p.name}: {v}" for p in self.places if p.remote is not None
                for v in p.remote.left]

    def said(self, memory: str = host.MEMORY) -> str:
        """The budget on each host, where each one's jobs ran and how long they took, and any
        host that was refused or went down."""
        heavy = megabytes(memory)
        parts = [f"here, {self.budget.said(memory)}"]
        for place in self.places[1:]:
            assert place.remote is not None
            parts.append(f"{place.remote.host.said()}: {place.slots.budget.at_once(heavy)} "
                         f"bench commands of {memory} at once, each pinned to its own cores")
        ran = []
        for place in self.places:
            times = "; ".join(f"`{kind}` {sum(s) / len(s):.0f} s" for kind, s in
                              sorted(place.seconds.items()))
            ran.append(f"{place.name} ran {place.placed}" + (f" ({times})" if times else ""))
        said = "; ".join(parts) + ". " + ", ".join(ran)
        for place in self.places[1:]:
            if place.down:
                said += f". {place.name} took no more jobs: {place.down}"
            if place.refusals:
                said += (f". {place.name}'s free memory held no more {place.refusals} times, "
                         f"last: {place.no_room}")
        if left := self.left():
            said += f". Volumes left behind: {', '.join(left)}"
        return said
