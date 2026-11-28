"""`load` whose first pass's mesh, made, won't fit read back, in the bench's second image (a
design run's assembly on mqucifer/print-shop#376): the control for the room held before
FreeCAD's mesh reader, which aborts the process where it meets the memory. Nothing here changes
how the bench reads anything else.

`python room_forced.py load ASSEMBLY ... --parts` runs `load` as the bench does. The first time
the first pass asks the container's room (`bench.cae.room`), as gmsh's mesh is about to be read
back, a process of its own takes all of the room but a few MB, really, and holds it until the
first pass ends: so the room the cgroup counts is too small for the mesh, on any machine. It's
started first to go, so if anything does meet the memory, it's the one stopped. Every other
step, and each glass band's read, has the container as it is.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401

# The bench as the container mounts it, `bench`, not as this repository installs it.
cae: Any = importlib.import_module("bench.cae")
load_parts: Any = importlib.import_module("bench.load_parts")
main = importlib.import_module("bench.cli").main

#: What the holder leaves of the room, in bytes.
LEAVES = 8 * 2**20
ROOM = cae.room
FIRST = load_parts.first
held: list[subprocess.Popen[str]] = []
state = {"first": False}


def room(cgroup: Any = None) -> Any:
    left = ROOM(cgroup)
    if state["first"] and not held and left is not None:
        take = max(left - LEAVES, 0)
        holder = subprocess.Popen(
            [sys.executable, "-c",
             f"import sys; held = b'\\1' * {take}; print('held', flush=True); sys.stdin.read()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, preexec_fn=cae.first_to_go,
        )  # fmt: skip
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        held.append(holder)
        left = ROOM(cgroup)
    return left


def first(*args: Any, **given: Any) -> Any:
    state["first"] = True
    try:
        return FIRST(*args, **given)
    finally:
        state["first"] = False
        for holder in held:
            holder.kill()
            holder.wait()


cae.room = room
load_parts.first = first
sys.exit(main(sys.argv[1:]))
