"""The trial's 66 near-touching cases, on our own hinge: OCCT against Manifold on the mesh.

Prints one line of JSON. The right answer stays near 1,930 mm³ throughout, since each case
only nudges a leaf by a tiny angle. It's in the image's own Python, like everything geometric.
"""

import json
import time
from typing import Any

import cadquery as cq
import manifold3d as m3
import numpy as np
import trimesh
from make_fixtures import along_y, box  # type: ignore[import-not-found]


def manifold(shape: Any) -> Any:
    vertices, triangles = shape.tessellate(0.01, 0.1)
    mesh = trimesh.Trimesh(np.array([v.toTuple() for v in vertices]), np.array(triangles))
    return m3.Manifold(
        m3.Mesh64(
            vert_properties=np.asarray(mesh.vertices, dtype=np.float64),
            tri_verts=np.asarray(mesh.faces, dtype=np.uint64),
        )
    )


def main() -> None:
    a = box(-25, -3.3, -15, 15, -1.5, 1.5)
    for y0, y1 in ((-15, -5.15), (5.15, 15)):
        a = a.union(along_y(3, y0, y1)).union(box(-3.3, -2, y0, y1, -1.5, 1.5))
    a = a.union(along_y(1.5, -15, 15)).val()
    b = (
        box(3.3, 25, -15, 15, -1.5, 1.5)
        .union(along_y(3, -4.85, 4.85, hole=1.8))
        .union(box(2, 3.3, -4.85, 4.85, -1.5, 1.5))
        .val()
    )
    rows = []
    start = time.time()
    for y in (0.0, 0.3, 0.6):
        for angle in (0, 1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 3e-5, 1e-4, 3e-4, 1e-3, 1e-2):
            for sign in (1, -1):
                moved = b.moved(
                    cq.Location(cq.Vector(0, y, 0), cq.Vector(0, 1, 0), 180 + sign * angle)
                )
                common = a.intersect(moved).Volume()
                cut = a.Volume() - a.cut(moved).Volume()
                mesh = (manifold(a) ^ manifold(moved)).volume()
                rows.append(
                    {"y": y, "angle": sign * angle, "common": common, "cut": cut, "mesh": mesh}
                )
    occt_wrong = [r for r in rows if abs(r["common"] - r["cut"]) >= 1 or r["common"] <= 1000]
    print(
        json.dumps(
            {
                "cases": len(rows),
                "occt_wrong_or_empty": len(occt_wrong),
                "occt_zero": sum(r["common"] < 1 for r in rows),
                "mesh_lowest": min(r["mesh"] for r in rows),
                "mesh_highest": max(r["mesh"] for r in rows),
                "seconds": round(time.time() - start, 1),
                "rows": rows,
            }
        )
    )


if __name__ == "__main__":
    main()
