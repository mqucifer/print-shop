"""`load` as the bench runs it, in the bench's second image, with the container's limit given
(mqucifer/print-shop#331): `python slow_forced.py S OFFERED load ASSEMBLY ...` gives the
container S s and offers it OFFERED s for two passes, as the host offers a plain `load`. Nothing
of `load` is replaced: given a limit its one pass can't end inside, on any machine, an assembly's
one pass goes on to two passes, as it would at 600 s on a larger one.
"""

from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401

# The bench as the container mounts it, `bench`, not as this repository installs it.
main = importlib.import_module("bench.cli").main
os.environ["BENCH_TIMEOUT"], os.environ["BENCH_TIMEOUT_TWO_PASSES"] = sys.argv[1:3]
sys.exit(main(sys.argv[3:]))
