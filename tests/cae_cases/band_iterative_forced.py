"""`load` whose glass band's read meets the container's memory in CalculiX's direct solve, in
the bench's second image (the operator, on a design run's assembly on mqucifer/print-shop#376):
the control for the band's read solved again by the iterative solver, as two passes are.
Nothing here changes how the bench reads anything else.

`python band_iterative_forced.py load ASSEMBLY ...` runs `load` as the bench does, with the whole
assembly's reading replaced by "couldn't be read", as `band_forced.py` does, so each part that
names its band is read without it. In that read, CalculiX's first direct solve is replaced by
a program that takes memory until the kernel stops it, first to go: so the container's own count
of what it stopped for memory rises, really, and no result is left. Every later solve, the
iterative one and its corrections, the coarser mesh's, is CalculiX's own.
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
load: Any = importlib.import_module("bench.load")
cae: Any = importlib.import_module("bench.cae")
main = importlib.import_module("bench.cli").main

READ = load._run
CCX = load._ccx
FORCED = "forced by the test, which reads the band's read by the direct solver beside this"
state = {"stood_in": False}


def without_bands_only(args: object, report: object, without_bands: bool = False) -> None:
    if not without_bands:
        raise load._Unread(FORCED, FORCED)
    READ(args, report, without_bands)


def ccx(fea: Any, size: float) -> None:
    if load._bands and not state["stood_in"]:
        state["stood_in"] = True
        subprocess.run(
            [sys.executable, "-c", "held = []\nwhile True:\n    held.append(b'\\1' * (64 << 20))"],
            preexec_fn=cae.first_to_go, check=False,
        )  # fmt: skip
        return
    CCX(fea, size)


load._run = without_bands_only
load._ccx = ccx
sys.exit(main(sys.argv[1:]))
