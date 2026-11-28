"""What `load` is doing as it goes, and the container's memory, said to the host on stderr
(mqucifer/print-shop#336).

A process the kernel kills for memory takes its reply with it, so a whole assembly killed at the
container's limit said only that it was killed: no step, no figure. Each step `load` names
(`load.step`) is said as it begins, and again as the step it was inside goes on, with the
container's memory then; and the memory is said every `EVERY` s while it runs. The host keeps
the last of each, and where the command is killed, says which step it was in and the most the
container had held by then (`host.killed`). The lines are the host's, and aren't passed on.

Each line is the line's kind, the seconds since the command began, and what it says:
- `bench: step 12.3 meshing and solving the joined parts, at a mesh of at most 5 mm`, or once
  a step has ended and no other is going on, `bench: step 15.0 between steps, after ...`
- `bench: memory 12.3 current=123 peak=456 anon=78 shmem=9 used=50 kept=20`, in bytes: from
  the container's cgroup, what it holds now, the most it has held, of that what's its processes'
  own, and what's in /tmp, which is memory; and from this process's C library, what it has in
  use and what it has freed but kept, which no other process, as gmsh or CalculiX, can use.
  `-` where the kernel or the C library keeps no count.
- `bench: met 812.4 limit=4294967296 peak=4290000000 need=5100000000 room=3000000000 at
  meshing ...`, once, as the command ends (mqucifer/print-shop#380): its read met the
  container's memory on the way to its answer, by the cgroup's own count or a check of a step's
  need against its room (`met`), and the answer isn't the whole read: its limit, the most it
  held, and the need and room where a check said them, in bytes, and the step it was in. The
  host may offer the same command to a host with more memory (`bench/offer.py`).

Standard library only.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

#: The lines' kinds: the same as `host.STEP_LINE` and `host.MEMORY_LINE`, which run outside
#: the image.
STEP_LINE = "bench: step"
MEMORY_LINE = "bench: memory"
#: And the line a command says as it ends, where its read met the container's memory: the same
#: as `host.MET_LINE` (mqucifer/print-shop#380).
MET_LINE = "bench: met"
#: How often the memory is said while a command runs, in s.
EVERY = 5.0
CGROUP = Path("/sys/fs/cgroup")
#: The fields of the cgroup's `memory.stat` that are said.
STAT = ("anon", "shmem")

_began = time.monotonic()
_steps: list[str] = []
_lock = threading.Lock()
_watching: dict[str, threading.Event | None] = {"stop": None}
_thread: dict[str, threading.Thread | None] = {"now": None}
#: The last time the read met the container's memory, as `met` kept it, or empty.
_met: dict[str, int | str | None] = {}


def memory(cgroup: Path | None = None) -> dict[str, int | None]:
    """The container's memory now, in bytes: `current` and `peak`, and `anon` and `shmem` from
    `memory.stat`; each None where the kernel keeps no count."""
    cgroup = CGROUP if cgroup is None else cgroup

    def number(name: str) -> int | None:
        try:
            return int((cgroup / name).read_text().strip())
        except (OSError, ValueError):
            return None

    found: dict[str, int | None] = {
        "current": number("memory.current"),
        "peak": number("memory.peak"),
    }
    found.update(dict.fromkeys(STAT))
    try:
        for line in (cgroup / "memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in STAT and value.strip().isdigit():
                found[key] = int(value)
    except OSError:
        pass
    found.update(heap())
    return found


class _Mallinfo2(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_size_t)
        for name in (
            "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks",
            "fordblks", "keepcost",
        )
    ]  # fmt: skip


def heap() -> dict[str, int | None]:
    """This process's C library's heap, in bytes: `used`, what's in use, its own and what it
    maps for large blocks; and `kept`, what's freed and kept for the process. None off glibc."""
    try:
        info = ctypes.CDLL("libc.so.6").mallinfo2
    except (OSError, AttributeError):
        return {"used": None, "kept": None}
    info.restype = _Mallinfo2
    got = info()
    return {"used": int(got.uordblks + got.hblkhd), "kept": int(got.fordblks)}


def _say(kind: str, text: str) -> None:
    with _lock:
        print(f"{kind} {time.monotonic() - _began:.1f} {text}", file=sys.stderr, flush=True)


def say_memory() -> None:
    counts = memory()
    _say(MEMORY_LINE, " ".join(f"{k}={'-' if v is None else v}" for k, v in counts.items()))


@contextlib.contextmanager
def step(what: str) -> Iterator[None]:
    """`what` said as it begins, with the memory then; and as it ends, the step it was inside
    said again, or that it's between steps, so the host's last step is where the command is."""
    _steps.append(" ".join(what.split()))
    _say(STEP_LINE, _steps[-1])
    say_memory()
    try:
        yield
    finally:
        done = _steps.pop()
        _say(STEP_LINE, _steps[-1] if _steps else f"between steps, after {done}")


def since(proc: Path = Path("/proc")) -> float:
    """Seconds since the command's process began, as the host's limit counts them: its age, by
    the kernel, where it says, as a reading's own clock starts only once its modules are
    imported; else since this module was."""
    try:
        stat = (proc / "self" / "stat").read_text()
        started = int(stat.rsplit(")", 1)[1].split()[19])
        uptime = float((proc / "uptime").read_text().split()[0])
        return max(0.0, uptime - started / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return time.monotonic() - _began


def again(what: str) -> None:
    """The step going on said again, as `what`: how far it's got."""
    if _steps:
        _steps[-1] = " ".join(what.split())
    _say(STEP_LINE, " ".join(what.split()))


def watch(every: float = EVERY) -> None:
    """Say the memory every `every` s from here until `stop`, on a thread of its own."""
    stop()
    event = threading.Event()
    _watching["stop"] = event

    def beat() -> None:
        while not event.wait(every):
            say_memory()

    thread = threading.Thread(target=beat, name="bench-memory", daemon=True)
    _thread["now"] = thread
    thread.start()


def stop() -> None:
    """The memory no longer said: its thread told to stop, and waited for."""
    event, thread = _watching["stop"], _thread["now"]
    if event is not None:
        event.set()
    if thread is not None:
        thread.join(timeout=5)
    _watching["stop"] = _thread["now"] = None


def _number(cgroup: Path, name: str) -> int | None:
    try:
        text = (cgroup / name).read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def met(need: float | None = None, room: float | None = None,
        cgroup: Path | None = None) -> None:
    """Keep that the read met the container's memory now (mqucifer/print-shop#380): by the
    cgroup's own count, or where a step's `need` was held against its `room`, in bytes. The
    container's limit and the step it's in are kept with it. `say_met` says it as the command
    ends."""
    cgroup = CGROUP if cgroup is None else cgroup
    _met.clear()
    _met.update({
        "limit": _number(cgroup, "memory.max"),
        "need": None if need is None else int(need),
        "room": None if room is None else int(room),
        "at": _steps[-1] if _steps else "between steps",
    })


def forget_met() -> None:
    _met.clear()


def has_met() -> bool:
    return bool(_met)


def say_met(cgroup: Path | None = None) -> None:
    """Say, once, that the read met the container's memory, where `met` kept it: its limit,
    the most the container held, the need and room where they were said, and the step."""
    if not _met:
        return
    cgroup = CGROUP if cgroup is None else cgroup
    counts = {"limit": _met["limit"], "peak": _number(cgroup, "memory.peak"),
              "need": _met["need"], "room": _met["room"]}
    fields = " ".join(f"{k}={'-' if v is None else v}" for k, v in counts.items())
    _say(MET_LINE, f"{fields} at {_met['at']}")
    _met.clear()
