"""`load` with one of its steps forced, in the bench's second image (mqucifer/print-shop#319).
Nothing here changes how the bench reads anything else.

`python load_forced.py MODE load ASSEMBLY ...` runs `load` as the bench does, with:

- `straight`: every mesh made with its elements' sides straight, as where gmsh can't curve
  them onto the parts' faces without turning one inside out, which a turn can bring about;
- `unfound`: the mesh's triangles on the faces where parts meet not found, as FreeCAD's own
  lookup didn't find them on a curved face of a straight-sided mesh;
- `raises`: reading the last part stopped by the error the lamp's rib met;
- `guess`: the one pass's wiring before mqucifer/print-shop#320, each element's mid-side nodes
  taken by where they lie, each side the one nearest its straight midpoint;
- `misordered`: one element of the last part with two of its mid-side nodes given the other
  way round, as FreeCAD's order would be were it wrong (mqucifer/print-shop#320).
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402

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
elif mode == "guess":
    import numpy as np

    def guess(self: Any) -> Any:
        ids = self.fem.Volumes
        wired = np.array([[self.row[n] for n in self.fem.getElementNodes(e)] for e in ids])
        places = self.points[wired]
        between = np.stack([(places[:, i] + places[:, j]) / 2 for i, j in load.MIDS], axis=1)
        apart = np.linalg.norm(between[:, :, None, :] - places[:, None, 4:, :], axis=3)
        pick = apart.argmin(axis=2)
        return np.concatenate([wired[:, :4], np.take_along_axis(wired[:, 4:], pick, axis=1)], 1)

    load._Model._wired = guess
elif mode == "misordered":
    import numpy as np

    wired = load._Model._wired

    class Swapped:
        """FreeCAD's mesh, but one element's first two mid-side nodes the other way round."""

        def __init__(self, fem: Any, element: int) -> None:
            self.fem, self.element = fem, element

        def __getattr__(self, name: str) -> Any:
            return getattr(self.fem, name)

        def getElementNodes(self, element: int) -> Any:  # noqa: N802 - FreeCAD's name
            nodes = list(self.fem.getElementNodes(element))
            if element == self.element:
                nodes[4], nodes[5] = nodes[5], nodes[4]
            return tuple(nodes)

    def misordered(self: Any) -> Any:
        if self._wiring is None and not isinstance(self.fem, Swapped):
            last = self.solids[-1]
            element = next(
                e
                for e in self.fem.Volumes
                if last.isInside(
                    FreeCAD.Vector(
                        *np.mean([self.nodes[n] for n in self.fem.getElementNodes(e)[:4]], axis=0)
                    ),
                    1e-6,
                    False,
                )
            )
            self.fem = Swapped(self.fem, element)
        return wired(self)

    load._Model._wired = misordered
else:
    raise SystemExit(f"no such mode: {mode}")
sys.exit(main(given))
