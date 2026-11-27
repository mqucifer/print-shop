"""`load` with one of its steps forced, in the bench's second image (mqucifer/print-shop#319).
Nothing here changes how the bench reads anything else.

`python load_forced.py MODE load ASSEMBLY ...` runs `load` as the bench does, with:

- `straight`: every mesh made with its elements' sides straight, as where gmsh can't curve
  them onto the parts' faces without turning one inside out, which a turn can bring about;
- `unfound`: the mesh's triangles on the faces where parts meet not found, as FreeCAD's own
  lookup didn't find them on a curved face of a straight-sided mesh;
- `raises`: reading the last part stopped by the error the lamp's rib met;
- `guess`: the one pass's wiring before mqucifer/print-shop#320, each element's mid-side nodes
  taken by where they lie, each side the one nearest its straight midpoint, and read as it was
  then, with no balance to hold it to; `guess-checked` the same, held to the balance;
- `misordered`: the most stressed element of the last part with no node on a joint's faces,
  with two of its mid-side nodes given the other way round, as FreeCAD's order would be were
  it wrong (mqucifer/print-shop#320); `unshared` the same, with the checks of its sides' shared
  nodes and of where they lie turned off, as for a swap they can't tell;
- `turned-away`: the curved mesh, finer where faces are close, turned away after its solve as
  if an element were inside out, so the straight one starts from the pairs it found;
- `inverted`: CalculiX's words for the first element of its input turned inside out, so the
  curved and the straight try both fail on it;
- `unsized`: the faces found close where gmsh's triangles cross given no local size, so gmsh
  refuses them as it did (mqucifer/print-shop#346).
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
elif mode in ("guess", "guess-checked"):
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
    if mode == "guess":
        # As the one pass read before mqucifer/print-shop#320, with no balance to hold it to.
        load._Model._balanced = lambda self, *given: None
elif mode in ("misordered", "unshared"):
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

    def chosen(self: Any) -> int:
        """The element of the last part with no node on a joint's faces, so every element it
        shares a side with is of that part too, whose corners are the most stressed."""
        joined = {n for face in self.between for n in self._face_nodes(face)}
        last = self.solids[-1]

        def stressed(element: int) -> float:
            rows = [self.row[n] for n in self.fem.getElementNodes(element)[:4]]
            return float(np.abs(self.stress[rows]).max())

        return int(max(
            (
                e
                for e in self.fem.Volumes
                if not joined & set(self.fem.getElementNodes(e))
                and last.isInside(
                    FreeCAD.Vector(
                        *np.mean([self.nodes[n] for n in self.fem.getElementNodes(e)[:4]], axis=0)
                    ),
                    1e-6,
                    False,
                )
            ),
            key=stressed,
        ))

    def misordered(self: Any) -> Any:
        if self._wiring is None and not isinstance(self.fem, Swapped):
            self.fem = Swapped(self.fem, chosen(self))
        return wired(self)

    load._Model._wired = misordered
    if mode == "unshared":
        # A swap neither the sides' shared nodes nor where they lie tells: the balance must.
        load.misordered = lambda wired, points: np.zeros(len(wired), dtype=bool)
elif mode == "turned-away":
    # The curved try meshed finer where faces are close and solved, then turned away as if
    # CalculiX had found an element turned inside out (mqucifer/print-shop#346).
    solve = load._Model.solve

    def turned_away(self: Any, doc: Any) -> None:
        solve(self, doc)
        if not self.straight:
            raise load._Curved("CalculiX found an element of the curved mesh turned inside out "
                               f"({FORCED})", self.closes)  # fmt: skip

    load._Model.solve = turned_away
elif mode == "inverted":
    # CalculiX's words for an element turned inside out, the first of its input, as on a
    # design run's table, curved and straight (mqucifer/print-shop#346).
    import re

    ccx = load._ccx

    def inverted(fea: Any, size: float) -> None:
        ccx(fea, size)
        with open(fea.inp_file_name) as given:
            first = re.search(r"^\*ELEMENT[^\n]*\n\s*(\d+),", given.read(), re.M | re.I)
        fea.ccx_stdout += (" *ERROR in e_c3d: nonpositive jacobian\n        determinant in "
                           f"element {first.group(1) if first else 1}\n")  # fmt: skip

    load._ccx = inverted
elif mode == "unsized":
    # Faces that meet nowhere found close where gmsh's triangles cross, but given no local size
    # (mqucifer/print-shop#346), as where the sizes don't part them.
    load.close_faces.fields = lambda closes, size: ""
else:
    raise SystemExit(f"no such mode: {mode}")
sys.exit(main(given))
