"""`load` read without its glass bands though the whole could be read: the control for the
lower bound (mqucifer/print-shop#295), in the bench's second image. Nothing here changes how the
bench reads anything else.

`python band_forced.py load ASSEMBLY ...` runs `load` as the bench does, with the whole
assembly's reading replaced by "couldn't be read", so each part that names its band is read
without it, as where the whole meets the container's memory. A test reads the same assembly
whole beside it.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401

# The bench as the container mounts it, `bench`, not as this repository installs it.
load: Any = importlib.import_module("bench.load")
main = importlib.import_module("bench.cli").main

READ = load._run
#: What stands in for what the whole met.
FORCED = "forced by the test, which reads the whole beside this"


def without_bands_only(args: object, report: object, without_bands: bool = False) -> None:
    if not without_bands:
        raise load._Unread(FORCED, FORCED)
    READ(args, report, without_bands)


load._run = without_bands_only
sys.exit(main(sys.argv[1:]))
