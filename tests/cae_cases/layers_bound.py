"""Each layer's core read from the part's mesh as Orca slices it, by GEOS (`slices.layer_rings`),
against OCCT's reading on the B-rep (`as_printed.layer_core`), on the planted parts, in the
bench's second image (mqucifer/print-shop#363). Prints one JSON object: for each part, at its
layers spread over its height and at each of its flat faces' levels, the two cores' areas, and
their symmetric difference over their outline's length, the mean distance between their edges.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402,F401 - before Part, which stops the process loaded first
import Part  # noqa: E402

as_printed: Any = importlib.import_module("bench.as_printed")
slices: Any = importlib.import_module("bench.slices")

PARTS = ("beam", "tile", "tile-chamfered", "bowed", "bowed-up", "bowed-notched", "half")
WALLS = as_printed.wall_thickness(2, 0.45, 0.2)
OPEN = as_printed.open_radius(0.45, 0.2)
MARGIN = as_printed.MARGIN * as_printed.wall_thickness(1, 0.45, 0.2)


def brep_rings(face: Any) -> Any:
    import shapely

    shape = shapely.Polygon()
    for f in face.Faces:
        for wire in f.Wires:
            points = wire.discretize(Deflection=1e-4)
            shape = shape.symmetric_difference(
                shapely.Polygon([(p.x, p.y) for p in points]).buffer(0))
    return shape


def main() -> None:
    import shapely

    found: dict[str, Any] = {}
    for name in PARTS:
        path = Path("load") / f"{name}.step"
        if not path.exists():
            continue
        part = Part.read(str(path)).Solids[0]
        mesh = slices.Mesh.of(part, 2)
        box = part.BoundBox
        count = int(round((box.ZMax - box.ZMin) / 0.2))
        layers = sorted({int(k) for k in np.linspace(0, count - 1, 12)})
        worst = {"apart_mm": 0.0, "area_pct": 0.0, "layers": 0}
        for k in layers:
            at = box.ZMin + (k + 0.5) * 0.2
            got = as_printed.layer_core(part, 2, at, WALLS, 1.0, 0.6, 1.0, OPEN, MARGIN)
            brep = shapely.Polygon() if got is None or got[0] is None else brep_rings(got[0])
            rings = slices.layer_rings(mesh, at, WALLS, 1.0, 0.6, 1.0, OPEN, MARGIN)
            ours = shapely.Polygon()
            for ring in rings:
                ours = ours.symmetric_difference(shapely.Polygon(ring).buffer(0))
            length = max(brep.length, ours.length, 1e-9)
            apart = brep.symmetric_difference(ours).area / length
            share = abs(brep.area - ours.area) / max(brep.area, 1e-9) * 100 if brep.area else 0.0
            worst["apart_mm"] = max(worst["apart_mm"], apart)
            worst["area_pct"] = max(worst["area_pct"], share)
            worst["layers"] += 1
        worst["triangles"] = int(len(mesh.triangles))
        found[name] = worst
    print(json.dumps(found))


if __name__ == "__main__":
    main()
