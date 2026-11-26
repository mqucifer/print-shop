"""`load` with one of its steps forced, in the bench's second image (mqucifer/print-shop#319).
Nothing here changes how the bench reads anything else.

`python load_forced.py MODE load ASSEMBLY ...` runs `load` as the bench does, with:

- `straight`: every mesh made with its elements' sides straight, as where gmsh can't curve
  them onto the parts' faces without turning one inside out, which a turn can bring about;
- `unfound`: the mesh's triangles on the faces where parts meet not found, as FreeCAD's own
  lookup didn't find them on a curved face of a straight-sided mesh;
- `raises`: reading the last part stopped by the error the lamp's rib met.
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
FORCED = "forced by the test, which reads the curved mesh beside this"

mode, given = sys.argv[1], sys.argv[2:]
if mode == "straight":
    solve = load._Model.solve

    def straight_only(self: Any, doc: Any) -> None:
        if not self.straight:
            raise load._Curved(FORCED)
        solve(self, doc)

    load._Model.solve = straight_only
elif mode == "unfound":
    on_face = load._Model._on_face

    def unfound(self: Any, face: Any) -> list[Any]:
        if any(face.isSame(b) for b in self.between):
            return []
        return list(on_face(self, face))

    load._Model._on_face = unfound
elif mode == "raises":
    part_read = load._Model._part_read

    def raises(self: Any, part: Any) -> Any:
        if part.name == self.assembly.parts[-1].name:
            raise ValueError("attempt to get argmax of an empty sequence")
        return part_read(self, part)

    load._Model._part_read = raises
else:
    raise SystemExit(f"no such mode: {mode}")
sys.exit(main(given))
