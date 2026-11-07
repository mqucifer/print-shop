"""The refinement pass's geometry, inside the container (mqucifer/print-shop#51).

Two requests, each one JSON argument, each answered by one line, `MARK` then JSON:

- **`mesh`**: each STEP file as an STL for Orca, whose CLI refuses STEP (exit 250, "Input file
  must have .stl, .obj, .amf(.xml) extension"; `~/print-shop/proofs/m18-refine/cli-probe/`, run
  s1). Meshed by OCCT at Orca's own GUI defaults, 0.003 mm linear and 0.5 rad angular deflection
  (`AppConfig.cpp`, v2.4.2), absolute, as `shapes._tessellated` meshes. For each file: the
  tolerance, the triangles, whether the mesh is closed, and its volume and box beside OCCT's, so
  what Orca is given is held to the file. An STL is copied through and read the same way. Each
  file's outline at its mid-height comes too, for reading where Orca's lines fall. A file is laid
  first on the face the run named for the bed (`on_bed`, mqucifer/print-shop#66), turned as a
  whole, and the mesh says how much of it then touches the bed: on `eval-lid-1` the lid printed
  lip down, as stored, where a lid prints with its outside on the bed.
- **`cover`**: where a part's section has no line of Orca's at all. On the probe a 0.5 and a 0.7 mm
  bar across a window got no line and no warning. For each layer, the section at its mid-height
  less every line laid there, each as wide as Orca says: what's left, shrunk by `least`, and
  within `least` of the section's edge, is a stretch of the part Orca didn't print. Sparse infill
  leaves gaps inside, which its walls keep away from the edge, so those aren't counted. And how
  wide the part is where Orca gap-fills, twice the way from each gap line's middle to the
  section's edge: gap fill in the middle of a wall is a wall that isn't a whole number of lines
  (mqucifer/print-shop#66; on `eval-edges-1` 14.3 m of it inside 2 mm walls).

Nothing here edits G-code: the lines are read on the host, from Orca's own file.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

MARK = "ROUNDS_REFINE "
#: A line's kind in `cover`'s request: 1 a wall, 2 gap fill, 0 anything else.
WALL, GAP = 1, 2
#: How far two wall lines must overlap across to be laid over each other, in mm.
DEEP_MM = 0.15
#: Either side of a layer's mid-height, in mm.
TIE = 1e-3
#: Orca's GUI defaults for meshing a STEP file, in mm and radians.
LINEAR = 0.003
ANGULAR = 0.5
#: Which way of the file, as stored, lies on the bed, and the turn that lays it there: about an
#: axis through the origin, in degrees. `-z` is as stored.
ON_BED: dict[str, tuple[tuple[float, float, float], float]] = {
    "-z": ((1.0, 0.0, 0.0), 0.0),
    "+z": ((1.0, 0.0, 0.0), 180.0),
    "+x": ((0.0, 1.0, 0.0), 90.0),
    "-x": ((0.0, 1.0, 0.0), -90.0),
    "+y": ((1.0, 0.0, 0.0), -90.0),
    "-y": ((1.0, 0.0, 0.0), 90.0),
}
#: How near the lowest point a face facing down counts as on the bed, in mm.
ON_BED_MM = 0.01


def _box(low: Any, high: Any) -> list[list[float]]:
    return [[round(float(v), 4) for v in low], [round(float(v), 4) for v in high]]


def _rings(geometry: Any) -> list[dict[str, Any]]:
    import shapely

    polygons = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    found = []
    for polygon in polygons:
        if not isinstance(polygon, shapely.Polygon) or polygon.is_empty:
            continue
        found.append(
            {
                "outer": [[round(x, 4), round(y, 4)] for x, y in polygon.exterior.coords],
                "holes": [
                    [[round(x, 4), round(y, 4)] for x, y in ring.coords]
                    for ring in polygon.interiors
                ],
                "area": round(float(polygon.area), 4),
            }
        )
    return found


def mesh(request: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import trimesh
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.GProp import GProp_GProps

    from .shapes import NoAnswer, load, slice_at, to_manifold

    linear = float(request.get("linear", LINEAR))
    angular = float(request.get("angular", ANGULAR))
    out = Path(request["out"])
    out.mkdir(parents=True, exist_ok=True)
    laid: dict[str, str] = request.get("on_bed") or {}
    files: dict[str, Any] = {}
    for name in request["files"]:
        row: dict[str, Any] = {"linear_mm": linear, "angular_rad": angular}
        face = laid.get(name, "-z")
        axis, degrees = ON_BED[face]
        row["on_bed"] = face
        try:
            part = load(name)
            target = out / (Path(name).stem + ".stl")
            if part.is_brep:
                shape = part.shape
                if degrees:
                    shape = shape.rotate((0, 0, 0), axis, degrees)
                BRepTools.Clean_s(shape.wrapped)
                BRepMesh_IncrementalMesh(shape.wrapped, linear, False, angular, True)
                vertices, triangles = shape.tessellate(1e9)
                built = trimesh.Trimesh(
                    np.array([v.toTuple() for v in vertices], dtype=float),
                    np.array(triangles, dtype=np.int64),
                    process=True,
                )
                props = GProp_GProps()
                BRepGProp.VolumeProperties_s(shape.wrapped, props)
                box = shape.BoundingBox()
                row["occt_volume_mm3"] = round(float(props.Mass()), 4)
                row["occt_box_mm"] = _box(
                    (box.xmin, box.ymin, box.zmin), (box.xmax, box.ymax, box.zmax)
                )
                row["faces"] = len(shape.Faces())
                row["curved_faces"] = sum(1 for f in shape.Faces() if f.geomType() != "PLANE")
                row["solids"] = len(shape.Solids())
                built.export(str(target))
            else:
                built = part.fine()
                if degrees:
                    built = built.copy()
                    built.apply_transform(
                        trimesh.transformations.rotation_matrix(  # type: ignore[no-untyped-call]
                            np.radians(degrees), axis
                        )
                    )
                    built.export(str(target))
                    row["meshed"] = f"an STL, turned to lie on its {face} side"
                else:
                    shutil.copyfile(name, target)
                    row["meshed"] = "an STL, copied as it is"
            low, high = np.asarray(built.bounds)
            row["bed_contact_mm2"] = bed_contact(built)
            row["stl"] = str(target)
            row["triangles"] = int(len(built.faces))
            row["closed"] = bool(built.is_watertight)
            row["mesh_volume_mm3"] = round(float(built.volume), 4)
            row["mesh_box_mm"] = _box(low, high)
            mid = float((low[2] + high[2]) / 2)
            row["outline_z"] = round(mid, 4)
            row["outline"] = _rings(slice_at(to_manifold(built, name), mid))
        except (NoAnswer, OSError, ValueError) as exc:
            row["unread"] = str(exc)[:400]
        files[name] = row
    return {"files": files}


def bed_contact(built: Any) -> float:
    """How much of a mesh, as it lies, touches the bed: the faces facing down at its lowest."""
    import numpy as np

    normals = np.asarray(built.face_normals)
    centres = np.asarray(built.triangles_center)
    low = float(np.asarray(built.bounds)[0][2])
    down = (normals[:, 2] < -0.9999) & (centres[:, 2] <= low + ON_BED_MM)
    return round(float(np.asarray(built.area_faces)[down].sum()), 3)


def cover(request: dict[str, Any]) -> dict[str, Any]:
    """Each layer's stretches of the part with no line of Orca's within them."""
    import numpy as np
    import shapely
    import trimesh

    from .shapes import slice_at, to_manifold

    least = float(request.get("least", 0.1))
    smallest = float(request.get("smallest", 0.05))
    part: Any = trimesh.load(request["stl"], force="mesh", process=True)
    part.apply_translation(np.asarray(request["offset"], dtype=float))
    built = to_manifold(part, request["stl"])
    with open(request["lines"], encoding="utf-8") as fh:
        layers = json.load(fh)["layers"]
    found = []
    gaps = []
    for layer in layers:
        mid = float(layer["z"]) - float(layer["height"]) / 2
        # Just under and just over the mid-height: on a level face there, a slicer may read either
        # side, so only what's bare by both counts.
        below, above = slice_at(built, mid - TIE), slice_at(built, mid + TIE)
        section = below.intersection(above)
        if section.is_empty:
            continue
        lines = np.asarray(layer["lines"], dtype=float).reshape(-1, 6)
        gap = lines[lines[:, 5] == GAP]
        if len(gap):
            middles = shapely.points((gap[:, 0:2] + gap[:, 2:4]) / 2)
            inside = shapely.contains(section, middles)
            if inside.any():
                lengths = np.hypot(*(gap[:, 2:4] - gap[:, 0:2]).T)[inside]
                across = 2 * shapely.distance(section.boundary, middles[inside])
                order = np.argsort(across)
                weights = np.cumsum(lengths[order])
                median = float(across[order][np.searchsorted(weights, weights[-1] / 2)])
                gaps.append(
                    {
                        "layer": layer["n"],
                        "z": layer["z"],
                        "length_mm": round(float(lengths.sum()), 3),
                        "wall_mm": round(median, 3),
                    }
                )
        if len(lines):
            strokes = shapely.linestrings(np.stack([lines[:, 0:2], lines[:, 2:4]], axis=1))
            laid = shapely.union_all(shapely.buffer(strokes, lines[:, 4] / 2, cap_style="flat"))
        else:
            laid = shapely.Polygon()
        bare = section.difference(laid).buffer(-least)
        edge = section.boundary
        pieces = list(bare.geoms) if hasattr(bare, "geoms") else [bare]
        for piece in pieces:
            if piece.is_empty or piece.area < smallest:
                continue
            if piece.distance(edge) > least + 0.05:
                continue
            x0, y0, x1, y1 = piece.bounds
            # Its width: the least rectangle round it, its short side, and what shrinking took off.
            corners = np.asarray(shapely.minimum_rotated_rectangle(piece).exterior.coords)
            sides = np.hypot(*np.diff(corners[:3], axis=0).T)
            width = float(sides.min()) + 2 * least
            found.append(
                {
                    "layer": layer["n"],
                    "z": layer["z"],
                    "box": [[round(x0, 3), round(y0, 3)], [round(x1, 3), round(y1, 3)]],
                    "area_mm2": round(float(piece.area), 3),
                    "width_mm": round(float(width), 3),
                }
            )
    return {"bare": found, "overlaps": overlaps(layers), "layers": len(layers), "gap_walls": gaps}


def overlaps(layers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wall lines laid over each other, each layer: a feature narrower than two walls.

    On the probe a 0.5 mm bar got one 0.42 mm outer wall down each side, their centres 0.08 mm
    apart, so each layer laid the bar twice over. Two wall lines that run the same way (within
    10°), don't meet end to end, and whose strokes share more than `least_mm2` are a pair. What's
    said is how far they overlap across, `width - distance`, and over what length.
    """
    import numpy as np
    import shapely

    least_mm2 = 0.05
    found = []
    for layer in layers:
        lines = np.asarray(layer["lines"], dtype=float).reshape(-1, 6)
        walls = lines[lines[:, 5] == WALL]
        if len(walls) < 2:
            continue
        strokes = shapely.linestrings(np.stack([walls[:, 0:2], walls[:, 2:4]], axis=1))
        shapes = shapely.buffer(strokes, walls[:, 4] / 2, cap_style="flat")
        tree = shapely.STRtree(shapes)
        left, right = tree.query(shapes, predicate="intersects")
        keep = left < right
        pieces = []
        for i, j in zip(left[keep], right[keep], strict=True):
            a, b = walls[i], walls[j]
            ends_a = {(round(a[0], 3), round(a[1], 3)), (round(a[2], 3), round(a[3], 3))}
            ends_b = {(round(b[0], 3), round(b[1], 3)), (round(b[2], 3), round(b[3], 3))}
            if ends_a & ends_b:
                continue
            da = np.subtract(a[2:4], a[0:2])
            db = np.subtract(b[2:4], b[0:2])
            na, nb = float(np.hypot(*da)), float(np.hypot(*db))
            if na < 0.5 or nb < 0.5:
                continue
            if abs(float(np.dot(da, db))) / (na * nb) < np.cos(np.radians(10)):
                continue
            shared = shapes[i].intersection(shapes[j])
            if shared.area < least_mm2:
                continue
            apart = float(strokes[i].distance(strokes[j]))
            depth = (a[4] + b[4]) / 2 - apart
            # Walls side by side overlap a little by design (Orca spaces them closer than their
            # width): only one laid well over another counts.
            if depth >= DEEP_MM:
                pieces.append((shared, depth))
        if not pieces:
            continue
        union = shapely.union_all([p for p, _ in pieces])
        for part in list(union.geoms) if hasattr(union, "geoms") else [union]:
            near = [depth for p, depth in pieces if p.intersects(part)]
            x0, y0, x1, y1 = part.bounds
            found.append(
                {
                    "layer": layer["n"],
                    "z": layer["z"],
                    "box": [[round(x0, 3), round(y0, 3)], [round(x1, 3), round(y1, 3)]],
                    "length_mm": round(max(x1 - x0, y1 - y0), 3),
                    "overlap_mm": round(float(max(near)), 3),
                }
            )
    return found


def main(argv: list[str] | None = None) -> None:
    request = json.loads((sys.argv[1:] if argv is None else argv)[0])
    kind = request.get("request")
    if kind == "mesh":
        answer = mesh(request)
    elif kind == "cover":
        answer = cover(request)
    else:
        answer = {"error": f"no request {kind!r}: mesh or cover"}
    print(MARK + json.dumps(answer))
