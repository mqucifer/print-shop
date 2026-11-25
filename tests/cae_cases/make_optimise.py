"""The parts `optimise` is read on (mqucifer/print-shop#286), made from scratch with FreeCAD in the
bench's second image: run from the folder they're written to.

- **The MBB beam** (`mbb/`): the standard test of topology optimisation, a simply supported beam
  loaded in the middle of its top. 120 mm long on x, 20 tall on z and 4 deep on y, on two feet 4
  mm long and 2 tall at its ends, which it stands on at z = -2. Its layers stack along z.
- **The cantilever** (`cantilever/`): 80 long on x, 20 tall on z and 4 deep on y, fixed to the
  wall at x = 0 and loaded at the top of its free end, with a 10 mm block at its middle that
  `--keep` holds solid.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402


def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Part.Shape:
    return Part.makeBox(x1 - x0, y1 - y0, z1 - z0, App.Vector(x0, y0, z0))


def write(folder: str, shape: Part.Shape, held: list[dict[str, str]], name: str = "beam") -> None:
    Path(folder).mkdir(exist_ok=True)
    solids = shape.removeSplitter().Solids
    assert len(solids) == 1, f"{folder}: {len(solids)} solids"
    solids[0].exportStep(f"{folder}/{name}.step")
    assembly = {"parts": [{"name": name, "file": f"{name}.step", "layers": "z"}], "held": held}
    Path(f"{folder}/assembly.json").write_text(json.dumps(assembly, indent=1) + "\n")


beam = box(0, 0, 0, 120, 4, 20).fuse([box(0, 0, -2, 4, 4, 0), box(116, 0, -2, 120, 4, 0)])
write("mbb", beam, [{"plane": "z=-2", "how": "standing"}])
write("cantilever", box(0, 0, 0, 80, 4, 20), [{"plane": "x=0", "how": "fixed"}])
print("made")
