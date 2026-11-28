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

**Every verdict and every rounded figure is a local run's, but not every last digit:** a remote
runs the same image, by ID (`Remote.refused`), and the same code, each solve single-threaded
inside. Read on the Spark and on the Mac, a plate pulled off its bolts gave the same reply but
for its bolt's sheared area, which its processor rounded otherwise in its last binary digit
(mqucifer/print-shop#373). So the cases a candidate is compared by are read on one host, its
`group`'s: the first of them that takes a slot sets it, and only where that host fails them do
they move, said (`said`). A host that fails a job (`job.HostLost`) takes no more, and the
study runs that one again elsewhere; one that ran past its limit there (`RanPastThere`) is
passed over for that job alone.

The pool is the study's slots (`take`) and its runner (`run`): a job runs on the host whose slot
its thread holds.

**A `load` that met its container's memory is offered more** (mqucifer/print-shop#380): where its
read met the memory, by the cgroup's own count or a check of a step's need against its room, and
its answer isn't the whole read (`job.Met`), it's read again on a host whose settings state a
larger memory for it (`remote.RemoteHost.load_mb`), the same command over the same files with the
same limits of time, in that memory (`offer`). It's held within that host's budget and over its
floor as any job is, and its record names the host, its limit and what it met where it ran first
(`report.offered_said`). Where no host states more, or none takes it, the first answer stands,
and the budget line says why.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

# By its own name: `scripts/check_addresses.py` reads a dotted `.local` as an mDNS host.
from threading import local as per_thread
from typing import Any

from . import host
from .budget import LOOK_AGAIN, Budget, Slots, megabytes
from .job import (
    HERE,
    READS,
    BenchJob,
    HostLost,
    JobResult,
    Met,
    Runner,
    reads,
    run_here,
    stop_here,
    verdict_of,
)
from .remote import NoRoom, RanPastThere, Remote, stated

__all__ = ["NoPlace", "Offered", "Offers", "Place", "Pool"]


class NoPlace(ValueError):
    """No host's budget holds a job at all: the study's, never the part's answer."""


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
    #: Jobs that ran past their limit there, each read again on another host.
    ran_past: int = 0

    def mean(self, kind: str) -> float | None:
        found = self.seconds.get(kind)
        return sum(found) / len(found) if found else None


@dataclass
class _Held:
    place: Place
    cores: tuple[int, ...]
    reserved: str | None
    kind: str | None
    group: str | None = None
    #: What it holds of its place's budget, and whether it's let go already: a job offered
    #: another host lets its first slot go before it waits for one there.
    memory_mb: int = 0
    cpus: int = 1
    gone: bool = False


@dataclass(frozen=True)
class Offered:
    """What became of an offer, by kinds alone, with no name or figure of the job's: its
    `outcome`; how each host that failed it failed (`HostLost.kind`); and where it was read
    there, what that read met, how much it read and its verdict's word, the host and its
    memory, in MiB."""

    outcome: str
    failures: list[str] = field(default_factory=list)
    met: Met | None = None
    read: str = ""
    verdict: str = ""
    host: str = ""
    memory_mb: int = 0


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
        self._local = per_thread()
        self._running = 0
        #: The most jobs that ran at once, on every host.
        self.most = 0
        #: The host each group's jobs are placed on (a candidate's cases), the hosts each one's
        #: jobs ran on, and a lock for each, held while one of its jobs is placed.
        self._groups: dict[str, str] = {}
        self._ran_on: dict[str, set[str]] = {}
        self._group_locks: dict[str, threading.Lock] = {}
        #: What became of each `load` that met its container's memory and was offered more, in
        #: words, for `said` (mqucifer/print-shop#380).
        self.offers: list[str] = []
        #: The groups a case of which was read on another host for its memory, and whether the
        #: pool is stopping: nothing is offered then.
        self._offered_groups: set[str] = set()
        self._stopping = threading.Event()
        #: The last offer's outcome, by kinds alone, for a probe to say (`Offered`).
        self.last_offer: Offered | None = None

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
    def take(self, memory_mb: int = 0, cpus: int = 1, kind: str | None = None,
             group: str | None = None,
             group_mb: int | None = None) -> Iterator[tuple[int, ...]]:
        """Hold `memory_mb` and `cpus` on a host while the block runs, and give it its cores
        where they're stated: here for anything that isn't a bench job (`kind` None, as a
        slice), else on the fastest host with room for it now; or, in a `group` (a candidate's
        cases), on the host its first job took, while that host takes jobs: only ever one
        whose budget holds the group's largest job, `group_mb`. `run` runs its job there.

        Raises:
            NoPlace: If no host's budget can hold it at all.
        """
        lock = None
        if group is not None:
            with self._lock:
                lock = self._group_locks.setdefault(group, threading.Lock())
            # One of a group's jobs is placed at a time, so its first sets the host for all.
            lock.acquire()
        try:
            held = self._place(memory_mb, cpus, kind, group, group_mb or memory_mb)
        finally:
            if lock is not None:
                lock.release()
        with self._lock:
            self._running += 1
            self.most = max(self.most, self._running)
        held.memory_mb, held.cpus = memory_mb, cpus
        stack = self._held_here()
        stack.append(held)
        try:
            yield held.cores
        finally:
            stack.remove(held)
            self._let_go(held)
            with self._lock:
                self._running -= 1

    def _let_go(self, held: _Held) -> None:
        """A slot let go, once: its reservation, and its memory and cores."""
        if held.gone:
            return
        held.gone = True
        if held.reserved is not None and held.place.remote is not None:
            held.place.remote.release(held.reserved)
        held.place.slots.let_go(held.memory_mb, held.cpus, held.cores)
        with self._lock:
            held.place.no_room_until = 0.0
            self._lock.notify_all()

    def _place(self, memory_mb: int, cpus: int, kind: str | None, group: str | None,
               group_mb: int) -> _Held:
        while True:
            avoid = self._avoided()
            with self._lock:
                seen = sum(p.slots.ended for p in self.places)
                pinned = self._groups.get(group) if group is not None else None
                usable = [p for p in self.places if not p.down and p.name not in avoid
                          and (kind is not None or p.remote is None)
                          and memory_mb <= p.slots.budget.free_memory_mb
                          and p.slots.budget.free_cpus >= 1]
                places = [p for p in usable if p.name == pinned]
                if not places:
                    # Not set yet, or its host can't take this one: set where every job of the
                    # group fits, else wherever this one does, said as it moves (`said`).
                    places = ([p for p in usable if group_mb <= p.slots.budget.free_memory_mb]
                              if group is not None else usable) or usable
            if not places:
                try:
                    self.places[0].slots.refuse_past(memory_mb)
                except ValueError as exc:
                    raise NoPlace(str(exc)) from exc
                raise NoPlace(f"no host takes a job of {memory_mb} MiB: "
                              + "; ".join(f"{p.name} {p.down}" for p in self.places[1:]))
            held = next((h for p in self._order(places, kind)
                         if (h := self._try(p, memory_mb, cpus, kind)) is not None), None)
            if held is not None:
                if group is not None:
                    held.group = group
                    with self._lock:
                        self._groups[group] = held.place.name
                        self._ran_on.setdefault(group, set()).add(held.place.name)
                return held
            with self._lock:
                if sum(p.slots.ended for p in self.places) == seen:
                    self._lock.wait(timeout=LOOK_AGAIN)

    def _avoided(self) -> set[str]:
        """The hosts this thread's job ran past its limit on: it isn't placed there again."""
        if not hasattr(self._local, "avoid"):
            self._local.avoid = set()
        avoid: set[str] = self._local.avoid
        return avoid

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
            HostLost: If the host failed it: it takes no more jobs. `RanPastThere` where it ran
                past its limit there: the host stays in use, and this thread's next take of a
                slot for it is on another, so a slower host never makes the part's answer.
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
        except RanPastThere:
            self._avoided().add(place.name)
            with self._lock:
                place.ran_past += 1
                if held.kind is not None:
                    place.seconds.setdefault(held.kind, []).append(time.monotonic() - began)
            raise
        except HostLost as exc:
            with self._lock:
                place.down = str(exc)
            raise
        self._avoided().clear()
        seconds = time.monotonic() - began
        with self._lock:
            place.placed += 1
            if done.saved and held.kind is not None:
                place.seconds.setdefault(held.kind, []).append(seconds)
        if done.met is not None and job.offered is None:
            what = f"{held.group}'s `{held.kind}`" if held.group and held.kind else None
            # Its read here is done: its slot goes before it waits for one with more, so two
            # offered jobs never each hold what the other waits for.
            self._let_go(held)
            again = self.offer(job, done.met, what, done.saved)
            if again is not None:
                if held.group is not None:
                    with self._lock:
                        self._ran_on.setdefault(held.group, set()).add(again.host)
                        self._offered_groups.add(held.group)
                return again
        return done

    def larger(self, met: Met, job: BenchJob) -> list[Place]:
        """The remote hosts that state a larger memory for a `load` than the container its read
        met (`remote.RemoteHost.load_mb`): its limit as it said it, else the job's own."""
        limit_mb = (met.limit // 2**20 if met.limit is not None
                    else megabytes(job.memory or host.memory_for(list(job.args))))
        return [p for p in self.places if p.remote is not None
                and p.remote.host.load_mb > limit_mb]

    def offer(self, job: BenchJob, met: Met, what: str | None = None,
              first: tuple[str, ...] | None = None) -> JobResult | None:
        """A `load` whose read met its container's memory (`met`) read again on a host that
        states a larger memory for it, in that memory: the same command over the same files,
        with the same limits of time, held within that host's budget and over its floor
        (`Remote.reserve`) as any job is, and told what it met (`job.OFFERED`). It waits for its
        slot while any of this process's jobs there may end and make room.

        Its result, where it reads more than the first answer, the files `first` names in the
        job's folder (`job.reads`): the whole over a glass band's bound, or either over a NO
        ANSWER. Else None, and the first answer stands, said in `offers` with why: no host
        states more, every one that does is down or holds no more with nothing of this
        process's there to end, the one it ran on failed it or raised, its read there read no
        more, or the study is stopping."""
        if host.command_word(list(job.args)) != "load":
            return None
        said = what or f"`{' '.join(job.args)}`"
        met_said = met.said()
        if self._stopping.is_set():
            self._said(f"{said} met its container's memory ({met_said}), as the study was "
                       "stopping: it wasn't offered more")
            self.last_offer = Offered("stopping")
            return None
        larger = self.larger(met, job)
        if not larger:
            self._said(f"{said} met its container's memory ({met_said}), and no host states "
                       "more for a load: its answer stands")
            self.last_offer = Offered("no host states more")
            return None
        failures: list[str] = []
        #: Each host passed over for good, and why.
        tried: dict[str, str] = {}
        while True:
            with self._lock:
                seen = sum(p.slots.ended for p in self.places)
                usable = [p for p in larger if not p.down and p.name not in tried]
            if not usable or self._stopping.is_set():
                why = "; ".join(f"{p.name}: {tried.get(p.name) or p.down}" for p in larger)
                self._said(f"{said} met its container's memory ({met_said}), and no host "
                           f"with more read it: {why}. Its first answer stands")
                self.last_offer = Offered("the host failed it" if failures else
                                          "no host took it", failures)
                return None
            for place in usable:
                assert place.remote is not None
                memory_mb = place.remote.host.load_mb
                cores = place.slots.hold(memory_mb, int(host.CPUS))
                if cores is None:
                    continue
                try:
                    reserved = place.remote.reserve(memory_mb)
                except NoRoom as exc:
                    place.slots.let_go(memory_mb, int(host.CPUS), cores)
                    with self._lock:
                        place.no_room, place.refusals = str(exc), place.refusals + 1
                    continue
                except HostLost as exc:
                    place.slots.let_go(memory_mb, int(host.CPUS), cores)
                    with self._lock:
                        place.down = str(exc)
                    tried[place.name] = (f"it failed it ({exc.kind or 'reserving its memory'}) "
                                         "and takes no more jobs this run")
                    failures.append(exc.kind or "reserving its memory")
                    continue
                offered = dataclasses.replace(job, memory=f"{memory_mb}m", offered=met)
                there = f"{place.name}'s {memory_mb / 1024:.1f} GiB"
                try:
                    done = place.remote.run(offered, cores, reserved)
                except HostLost as exc:
                    # Never the part's answer: the first one stands, or another host reads it.
                    kind = exc.kind or "unknown"
                    with self._lock:
                        if isinstance(exc, RanPastThere):
                            place.ran_past += 1
                            tried[place.name] = f"it failed it ({kind})"
                        else:
                            place.down = str(exc)
                            tried[place.name] = (f"it failed it ({kind}) and takes no more "
                                                 "jobs this run")
                    self._said(f"{said} was offered {there}, which failed it ({kind}): {exc}")
                    failures.append(kind)
                    continue
                except Exception as exc:  # noqa: BLE001 - the first answer stands, said
                    tried[place.name] = f"its read raised {type(exc).__name__}"
                    self._said(f"{said} was offered {there}, whose read raised "
                               f"{type(exc).__name__}: {exc}"[:600])
                    failures.append(f"its read raised {type(exc).__name__}")
                    continue
                finally:
                    place.remote.release(reserved)
                    place.slots.let_go(memory_mb, int(host.CPUS), cores)
                    with self._lock:
                        self._lock.notify_all()
                with self._lock:
                    place.placed += 1
                before = reads(job.folder, first or ())
                after = reads(job.folder, done.saved)
                self.last_offer = Offered(
                    "read there" if first is None or after > before else
                    "read there, no more than its first answer", failures, done.met,
                    READS[after], verdict_of(job.folder, done.saved), place.name, memory_mb)
                if first is not None and after <= before:
                    self._said(
                        f"{said} met its container's memory ({met_said}), and was read on "
                        f"{place.name} in its {memory_mb / 1024:.1f} GiB as "
                        f"{verdict_of(job.folder, done.saved)}, {READS[after]}: no more than "
                        f"its first answer, {verdict_of(job.folder, first)}, {READS[before]}, "
                        "which stands" + (f". There it met its memory too ({done.met.said()})"
                                          if done.met is not None else ""))
                    return None
                self._said(f"{said} met its container's memory ({met_said}), and was read on "
                           f"{place.name} in its {memory_mb / 1024:.1f} GiB"
                           + (f": {READS[after]}, where its first answer was {READS[before]}"
                              if first is not None else ""))
                return done
            # Room may come only as one of this process's jobs there ends: where none is there,
            # what holds it is the host's own, and it isn't waited for.
            for place in usable:
                if place.name in tried or place.down:
                    continue
                if not place.slots.running and not (place.remote and place.remote.held()):
                    tried[place.name] = place.no_room or "its budget holds no job of that memory"
            if all(p.name in tried or p.down for p in usable):
                continue
            with self._lock:
                if sum(p.slots.ended for p in self.places) == seen:
                    self._lock.wait(timeout=LOOK_AGAIN)

    def _said(self, text: str) -> None:
        with self._lock:
            self.offers.append(text)

    def stop(self) -> None:
        """Stop every container this process's jobs are running, here and on each remote: and
        offer none more memory, as their kills aren't their memory's."""
        self._stopping.set()
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
        if len(self.places) > 1:
            parts[0] += ("; each candidate's cases on one host, as the last digits of a figure "
                         "may differ between hosts")
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
            if place.ran_past:
                said += (f". {place.ran_past} jobs ran past their limits on {place.name}, each "
                         "read again on another host")
            if place.remote is not None and place.remote.over:
                said += f". On {place.name}, " + "; ".join(place.remote.over)
        with self._lock:
            mixed = {g: sorted(h) for g, h in self._ran_on.items() if len(h) > 1}
        for group, hosts in sorted(mixed.items()):
            why = ("as one was offered a host with more memory"
                   if group in self._offered_groups else "as one failed them")
            said += f". {group}'s cases were read on more than one host: {', '.join(hosts)}, {why}"
        with self._lock:
            offers = list(self.offers)
        for text in offers:
            said += f". {text}"
        if left := self.left():
            said += f". Volumes left behind: {', '.join(left)}"
        return said


class Offers:
    """The remote hosts the settings name that state a larger memory for a `load`
    (`remote.RemoteHost.load_mb`), offered a `load` whose read met its container's memory by a
    process that isn't a study, as a design round's commands (`rounds.box`,
    mqucifer/print-shop#380). Asked about nothing until a read is first offered: then each is
    checked, its images by ID (`Remote.refused`) and what an earlier process left there cleared
    (`Remote.clear_stale`), and any refused is said. Each offer is `Pool.offer`'s."""

    def __init__(self, env: dict[str, str],
                 remote_of: Callable[[Any], Remote] = Remote) -> None:
        self.hosts = [h for h in stated(env) if h.load_mb]
        self._remote_of = remote_of
        self._pool: Pool | None = None
        self._lock = threading.Lock()
        #: What was said as the hosts were checked: refused, or volumes cleared.
        self.checked: list[str] = []
        self._stopping = threading.Event()

    def __bool__(self) -> bool:
        return bool(self.hosts)

    def pool(self) -> Pool:
        with self._lock:
            if self._pool is None:
                remotes = []
                for stated_host in self.hosts:
                    found = self._remote_of(stated_host)
                    why = found.refused()
                    if why:
                        self.checked.append(f"{stated_host.name} wasn't offered it: {why}")
                        continue
                    removed, kept = found.clear_stale()
                    self.checked += [f"removed {v} on {stated_host.name}, left by an earlier "
                                     "process" for v in removed]
                    self.checked += [f"couldn't remove {v} on {stated_host.name}, left by an "
                                     "earlier process" for v in kept]
                    remotes.append(found)
                self._pool = Pool(Slots(Budget.serial()), remotes)
            return self._pool

    def __call__(self, job: BenchJob, met: Met,
                 first: tuple[str, ...] | None = None) -> tuple[JobResult | None, str]:
        """A `load` that met its container's memory read again on a host that states more, where
        it reads more than the first answer, the files `first` names (`Pool.offer`), or None;
        and what became of the offer, in words."""
        if self._stopping.is_set():
            return None, "the run was stopping: it wasn't offered more memory"
        pool = self.pool()
        before = len(pool.offers)
        done = pool.offer(job, met, first=first)
        said = ". ".join([*self.checked, *pool.offers[before:]])
        self.checked.clear()
        return done, said

    def stop(self) -> None:
        self._stopping.set()
        if self._pool is not None:
            self._pool.stop()
