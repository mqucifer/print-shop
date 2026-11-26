"""`load` where two passes can't end before the time a glass band's lower bound is kept, in the
bench's second image (mqucifer/print-shop#301): the control for the band's fallback. Nothing here
changes how the bench reads anything else.

`python reserve_forced.py load ASSEMBLY ...` runs `load` as the bench does, with the whole
assembly's one-pass reading replaced by "met the container's memory", and two passes given no
time but two seconds, as if what's kept for the band took the rest: so they're stopped by their
clock, whatever the machine, and each part that names its band is read without it.
"""

from __future__ import annotations

import importlib
import sys
import time
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401

# The bench as the container mounts it, `bench`, not as this repository installs it.
load: Any = importlib.import_module("bench.load")
load_parts: Any = importlib.import_module("bench.load_parts")
main = importlib.import_module("bench.cli").main

READ = load._run
CLOCK = load_parts.Clock
#: What stands in for what the whole met.
FORCED = "forced by the test: the one pass met the container's memory"


def met_memory_first(
    args: object, report: object, without_bands: bool = False, two_pass: object = None,
    reserve: float = 0.0,
) -> None:  # fmt: skip
    if two_pass is None and not without_bands:
        raise load._Unread(FORCED, FORCED, memory=True)
    READ(args, report, without_bands, two_pass, reserve)


class Late(CLOCK):  # type: ignore[misc, valid-type]
    """Two passes' clock with all but two seconds of the container's time kept for the band."""

    def __init__(self, started: float, reserve: float = 0.0, label: str = "two passes") -> None:
        super().__init__(started, reserve, label)
        if label == "two passes" and self.limit is not None:
            self.reserve = self.limit - load_parts.MARGIN - (time.time() - started) - 2


load._run = met_memory_first
load_parts.Clock = Late
sys.exit(main(sys.argv[1:]))
