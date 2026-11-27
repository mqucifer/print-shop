#!/usr/bin/env python3
"""What of `load`'s first step, each through-bolt's way through its parts, holds memory, on an
assembly of one's own (mqucifer/print-shop#336).

Run from the folder the assembly is in:

    uv run python <print-shop>/scripts/load_ways_probe.py ASSEMBLY.json

It runs in the bench's second image under the bench's limits, places the parts as `load` does,
and reads each through joint's bolts' ways as `load` does, saying after each part of that what
`load`'s process has in use and has freed but kept. It prints only counts, kinds of face,
seconds and memory: each part and joint by its number, never its name, and no figure of any
shape. Nothing is written.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import uuid
from pathlib import Path

from print_shop.bench import host


def inner(assembly_file: str) -> None:  # pragma: no cover - runs in the image
    """Inside the container: each stage, and the heap after it."""
    import collections
    import ctypes
    import gc
    import time

    sys.path.insert(0, "/opt/bench")
    sys.path.append("/opt/conda/lib")
    from bench import assembly, cae, load, load_through, progress, through

    began = time.time()

    def said(what: str) -> None:
        heap = progress.heap()
        gib = {k: (v or 0) / 2**30 for k, v in heap.items()}
        print(
            f"{time.time() - began:8.1f} s  in use {gib['used']:.2f} GiB, kept {gib['kept']:.2f}"
            f" GiB  {what}",
            flush=True,
        )

    cae.freecad("probe")
    said("FreeCAD loaded")
    made = assembly.read(assembly_file)
    names = [p.name for p in made.parts]
    with load._quiet():
        placed = load._placed(made, False)
    said(f"{len(placed)} parts placed")
    for k, solid in enumerate(placed, 1):
        kinds = collections.Counter(type(f.Surface).__name__ for f in solid.Faces)
        print(
            f"  part{k}: {len(solid.Faces)} faces, {len(solid.Edges)} edges: "
            + ", ".join(f"{n} {c}" for c, n in sorted(kinds.items())),
            flush=True,
        )
    said("faces counted")
    # Each part's faces read as the ways read them, twice: whether reading them keeps anything.
    for solid in placed:
        found = load_through.rows(solid)
        del found
    said("each part's faces read once, as `rows` reads them")
    for solid in placed:
        load_through.rows(solid)
    gc.collect()
    said("and again, and collected")
    numbers = {j.name: n for n, j in enumerate(made.joints, 1)}
    kept_ways = {}
    for joint in made.joints:
        if joint.kind != "through":
            continue
        parts = [made.part(n) for n in joint.between]
        solids = [placed[names.index(n)] for n in joint.between]
        faces = [load_through.rows(s) for s in solids]
        bolts = []
        for point in joint.at:
            holes = [
                through.seat_of(joint, cylinders, point, planes)
                for cylinders, planes in faces
            ]
            if any(isinstance(h, str) for h in holes):
                print(f"  joint{numbers[joint.name]}: a bolt has no seat; stopping", flush=True)
                return
            way = through.meet(holes[0], holes[1])
            if isinstance(way, str):
                print(f"  joint{numbers[joint.name]}: a bolt has no way; stopping", flush=True)
                return
            sides = [
                load_through._side(joint, parts[k], solids[k], holes[k], way, k) for k in range(2)
            ]
            bolts.append({"axis": way["axis"], "meet": way["meet"], "sides": sides})
        kept_ways[joint.name] = bolts
        del faces
        said(f"joint{numbers[joint.name]}'s {len(joint.at)} bolt(s) read")
    said("every through joint's ways read")
    del kept_ways
    gc.collect()
    said("the ways let go, and collected")
    ctypes.CDLL("libc.so.6").malloc_trim(0)
    said("and trimmed")
    fresh = [s.copy() for s in placed]
    said("the placed parts copied afresh")
    del placed
    gc.collect()
    ctypes.CDLL("libc.so.6").malloc_trim(0)
    said("the parts the ways read let go, the copies kept, collected and trimmed")
    del fresh


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].endswith(".json"):
        print("give the assembly: load_ways_probe.py ASSEMBLY.json")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    code = inspect.getsource(inner) + f"\ninner({argv[0]!r})\n"
    code = "import sys\n" + code
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv_ = host.container(
        Path.cwd(), [host.CAE_PYTHON, "-I", "-c", code], name, image=host.CAE_IMAGE
    )
    try:
        done = subprocess.run(
            argv_, capture_output=True, text=True, timeout=host.TWO_PASSES, check=False
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        print(f"stopped at {host.TWO_PASSES} s")
        return 2
    print(done.stdout, end="")
    if done.returncode:
        # Only the error's kind: its words may name a part or give a figure.
        last = (done.stderr.strip().splitlines() or ["none"])[-1].split(":", 1)[0]
        print(f"exit {done.returncode}" + (" (killed)" if done.returncode == host.KILLED else "")
              + f", the error's kind: {last}")  # fmt: skip
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
