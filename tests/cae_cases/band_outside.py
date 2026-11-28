"""`load` read as the bench reads it, with each banded part's strain read again on its nodes
outside its glass band, in the bench's second image (mqucifer/print-shop#375): the control for a
lift read with the band's stiffness left out, whose strain is read on the rest of the part
alone. Nothing here changes what `load` reads or says.

`python band_outside.py load ASSEMBLY ...` runs `load` as the bench does, and then prints one
line, `outside: ` and JSON: for each part that names its band, its worst pull across its layers
and its largest stretch on its nodes outside the band's box, in the part's own axes as placed,
at each mesh it was read at. Only a part not turned is read so: the made-up frames are placed
by `at` alone.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401
import numpy as np  # noqa: E402
import Part  # noqa: E402

# The bench as the container mounts it, `bench`, not as this repository installs it.
load: Any = importlib.import_module("bench.load")
assembly: Any = importlib.import_module("bench.assembly")
main = importlib.import_module("bench.cli").main

#: A node on the band's box, as one on a face where the band met the rest, is outside it.
ON = 1e-3
READ = load._Model._read_rows
found: dict[str, list[dict[str, Any]]] = {}
boxes: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def outside(self: Any, part: Any, rows: np.ndarray) -> dict[str, Any]:
    read: dict[str, Any] = READ(self, part, rows)
    if part.name not in boxes:
        return read
    low, high = boxes[part.name]
    points = self.points[rows]
    within = np.all((points > low + ON) & (points < high - ON), axis=1)
    rest = READ(self, part, rows[~within])
    found.setdefault(part.name, []).append(
        {k: rest[k] for k in ("across_layers_pct", "across_at", "stretch_pct", "stretch_at")}
    )
    return read


given = sys.argv[1:]
path = Path(given[1])
for one in assembly.read(str(path)).parts:
    if one.band is None:
        continue
    if one.turn:
        sys.exit(f"{one.name} is turned: only a part placed by `at` alone is read here")
    box = Part.read(str(path.parent / one.band)).BoundBox
    at = np.asarray(one.at, dtype=float)
    boxes[one.name] = (
        np.array([box.XMin, box.YMin, box.ZMin]) + at,
        np.array([box.XMax, box.YMax, box.ZMax]) + at,
    )
load._Model._read_rows = outside
code = main(given)
print("outside: " + json.dumps(found))
sys.exit(code)
