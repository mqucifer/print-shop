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
- `bench: memory 12.3 current=123 peak=456 anon=78 shmem=9`, in bytes, from the container's
  cgroup: what it holds now, the most it has held, of that what's the process's own, and what's
  in /tmp, which is memory. `-` where the kernel keeps no count.

Standard library only.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

#: The lines' kinds: the same as `host.STEP_LINE` and `host.MEMORY_LINE`, which run outside
#: the image.
STEP_LINE = "bench: step"
MEMORY_LINE = "bench: memory"
#: How often the memory is said while a command runs, in s.
EVERY = 5.0
CGROUP = Path("/sys/fs/cgroup")
#: The fields of the cgroup's `memory.stat` that are said.
STAT = ("anon", "shmem")

_began = time.monotonic()
_steps: list[str] = []
_lock = threading.Lock()
_watching: dict[str, threading.Event | None] = {"stop": None}


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
    return found


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


def watch(every: float = EVERY) -> None:
    """Say the memory every `every` s from here until `stop`, on a thread of its own."""
    stop()
    event = threading.Event()
    _watching["stop"] = event

    def beat() -> None:
        while not event.wait(every):
            say_memory()

    threading.Thread(target=beat, name="bench-memory", daemon=True).start()


def stop() -> None:
    event = _watching["stop"]
    if event is not None:
        event.set()
    _watching["stop"] = None
