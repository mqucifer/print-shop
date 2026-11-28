"""Each layer's core read from the part's mesh as Orca slices it, by GEOS (`slices.layer_rings`),
against OCCT's reading on the B-rep (`as_printed.layer_core`), on the planted parts, in the
bench's second image (mqucifer/print-shop#363). Prints one JSON object: for each part, at 12
layers spread over its height and at each of its flat faces' levels, a layer above and below:
the largest distance between the two cores' outlines (Hausdorff's), the mean (their symmetric
difference over their outline's length), and their areas apart. And for the bar bowed along
its layers, its core's volume by OCCT on the B-rep, every layer's area by its height: the test's
truth for it, read apart from the mesh Orca slices and its GEOS.
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
        layers = {int(k) for k in np.linspace(0, count - 1, 12)}
        # Each flat face's level, the layer it bounds above and below.
        for face in part.Faces:
            if face.Surface.__class__.__name__ == "Plane" and abs(face.normalAt(0, 0).z) > 0.999:
                k = int(round((face.CenterOfMass.z - box.ZMin) / 0.2))
                layers.update(j for j in (k - 1, k) if 0 <= j < count)
        worst = {"apart_mm": 0.0, "mean_mm": 0.0, "area_pct": 0.0, "layers": 0}
        for k in sorted(layers):
            at = box.ZMin + (k + 0.5) * 0.2
            got = as_printed.layer_core(part, 2, at, WALLS, 1.0, 0.6, 1.0, OPEN, MARGIN)
            brep = shapely.Polygon() if got is None or got[0] is None else brep_rings(got[0])
            rings = slices.layer_rings(mesh, at, WALLS, 1.0, 0.6, 1.0, OPEN, MARGIN)
            ours = shapely.Polygon()
            for ring in rings:
                ours = ours.symmetric_difference(shapely.Polygon(ring).buffer(0))
            length = max(brep.length, ours.length, 1e-9)
            mean = brep.symmetric_difference(ours).area / length
            if brep.is_empty or ours.is_empty:
                apart = 0.0 if brep.is_empty and ours.is_empty else float("inf")
            else:
                apart = float(shapely.hausdorff_distance(brep.boundary, ours.boundary))
            share = abs(brep.area - ours.area) / max(brep.area, 1e-9) * 100 if brep.area else 0.0
            worst["apart_mm"] = max(worst["apart_mm"], apart)
            worst["mean_mm"] = max(worst["mean_mm"], mean)
            worst["area_pct"] = max(worst["area_pct"], share)
            worst["layers"] += 1
        worst["triangles"] = int(len(mesh.triangles))
        if name == "bowed-up":
            # Its core by OCCT on the B-rep, every layer, apart from the mesh and GEOS.
            volume = 0.0
            for k in range(count):
                at = box.ZMin + (k + 0.5) * 0.2
                got = as_printed.layer_core(part, 2, at, WALLS, 1.0, 0.6, 1.0, OPEN, MARGIN)
                if got is not None and got[0] is not None:
                    volume += brep_rings(got[0]).area * 0.2
            worst["brep_core_mm3"] = volume
        found[name] = worst
    print(json.dumps(found))


if __name__ == "__main__":
    main()
