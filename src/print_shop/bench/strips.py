"""Curves stored as flat strips, told from narrow flat faces (mqucifer/print-shop#85).

A curve built from a polygon's points through `polyline` is stored as a flat face for each
segment: suncatcher-3's frame was 23 MB of them. `info` counted every flat face under 1 mm
across, which caught that frame, and also a lid built from boxes, one circle and fillets, whose
small faces were the narrow sides of four clip beams and their feet and the seams where fillets
meet. Its run had to argue past a blocking check for a polygon it never drew.

Strips come in runs: each flat face a neighbour of the next along a shared edge, turning a few
degrees to it, so that together they follow a curve. A beam's narrow side meets its wide sides
square, and a seam meets the fillets' curved faces, so neither is in a run. A run is `RUN_LEAST`
faces or more with a narrow one among them, and only the narrow faces in runs count, so a coarse
polygon, every strip of it over 1 mm, is no more counted than it was. The reply says how many
runs there are and how long each is.

Strips needn't be about as wide as each other. A polygon whose points come in close pairs has
strips that go wide, narrow, wide round its curve, and only the narrow ones are under 1 mm: a rule
that asked for neighbours of about the same width read such a pane as no strips at all.

- **First method:** OCCT's own topology: which faces share each edge, each face's normal, and
  its width across its plane by OCCT's oriented box.
- **Second method:** points along each face's edges: two faces are neighbours where they share
  points, the turn between them is between the planes fitted to their points, and the width is the
  narrower side of the least rectangle round the points, laid in the plane.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import shapely

#: A flat face narrower than this, across its plane, is read as small (mqucifer/print-shop#54):
#: 121 of 226 approved STEP files stored every curve as such strips, at 96 to 200 a quarter circle.
SMALL_FACE_MM = 1.0
#: Points read along each edge of a flat face, for the second reading.
EDGE_POINTS = 9
#: Two strips of a polygon turn less than this to each other, in degrees: a polygon of more than
#: 12 sides. Shapely's default is 16 segments a quarter, 5.6°; the frame's 96 to 200, under 1°.
#: A beam's or a slot's narrow side turns 90° to the next face.
TURN_DEG = 30.0
#: Two faces turning less than this, in degrees, are one plane split in two, not a curve.
TURN_FLOOR_DEG = 0.01
#: The fewest faces in a run: two turns in a row.
RUN_LEAST = 3
#: Points along edges are matched to this, in mm, for the second reading's neighbours.
POINT_ROUND = 5


def obb_width(face: Any) -> float:
    """A flat face's width across its plane, by OCCT's oriented box: its middle extent, since a flat
    face's smallest is its thickness, nothing."""
    from OCP.Bnd import Bnd_OBB
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_OBB()
    BRepBndLib.AddOBB_s(face.wrapped, box, True, True, False)
    return float(sorted([2 * box.XHSize(), 2 * box.YHSize(), 2 * box.ZHSize()])[1])


def edge_points(face: Any) -> np.ndarray:
    """Points along each of a flat face's edges, `EDGE_POINTS` to an edge."""
    return np.array(
        [
            p.toTuple()
            for edge in face.Edges()
            for p in edge.positions(np.linspace(0.0, 1.0, EDGE_POINTS).tolist())
        ]
    )


def drawn_width(face: Any, points: np.ndarray | None = None) -> float:
    """The same width by points along the face's edges, laid in its plane, and the narrower side
    of the least rectangle round them."""
    points = edge_points(face) if points is None else points
    normal = _fitted_normal(points)
    across = np.cross(normal, [1.0, 0.0, 0.0])
    if np.linalg.norm(across) < 1e-6:
        across = np.cross(normal, [0.0, 1.0, 0.0])
    across /= np.linalg.norm(across)
    up = np.cross(normal, across)
    flat = shapely.MultiPoint(np.column_stack([points @ across, points @ up]))
    rectangle = flat.minimum_rotated_rectangle
    if rectangle.geom_type != "Polygon":
        return 0.0
    a, b, c = np.asarray(rectangle.exterior.coords)[:3]
    return float(min(np.linalg.norm(b - a), np.linalg.norm(c - b)))


def _fitted_normal(points: np.ndarray) -> np.ndarray:
    """The normal of the plane fitted to points: their least spread."""
    centred = points - points.mean(axis=0)
    return np.asarray(np.linalg.svd(centred, full_matrices=False)[2][-1], dtype=float)


def runs(
    count: int,
    widths: list[float],
    neighbours: list[tuple[int, int]],
    turn: Callable[[int, int], float],
) -> list[list[int]]:
    """The runs of strips among `count` flat faces: faces joined where they're neighbours and turn
    between `TURN_FLOOR_DEG` and `TURN_DEG` to each other, kept where a run has `RUN_LEAST` faces
    or more and a narrow one among them. Longest first."""
    group = list(range(count))

    def root(k: int) -> int:
        while group[k] != k:
            group[k] = group[group[k]]
            k = group[k]
        return k

    for i, j in neighbours:
        if i != j and TURN_FLOOR_DEG <= turn(i, j) < TURN_DEG:
            group[root(i)] = root(j)
    found: dict[int, list[int]] = {}
    for k in range(count):
        found.setdefault(root(k), []).append(k)
    kept = [
        r
        for r in found.values()
        if len(r) >= RUN_LEAST and any(widths[k] < SMALL_FACE_MM for k in r)
    ]
    return sorted(kept, key=len, reverse=True)


def narrow_in(found: list[list[int]], widths: list[float]) -> int:
    """How many faces in the runs are narrow: the strips counted."""
    return sum(1 for r in found for k in r if widths[k] < SMALL_FACE_MM)


def _degrees(a: np.ndarray, b: np.ndarray) -> float:
    cos = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def by_topology(shape: Any, flat: list[Any]) -> tuple[list[float], list[list[int]]]:
    """The first reading: each flat face's width by OCCT's oriented box, its neighbours by the
    edges OCCT says it shares, and the turn between their normals. (Widths, runs.)"""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    widths = [obb_width(f) for f in flat]
    index = {f: k for k, f in enumerate(flat)}
    owners = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape.wrapped, TopAbs_EDGE, TopAbs_FACE, owners)
    if not flat:
        return [], []
    face_type = type(flat[0])
    neighbours: list[tuple[int, int]] = []
    for e in range(1, owners.Extent() + 1):
        sharing = sorted(
            {index[k] for k in (face_type(f) for f in owners.FindFromIndex(e)) if k in index}
        )
        neighbours += [(i, j) for n, i in enumerate(sharing) for j in sharing[n + 1 :]]
    normals = [np.array(f.normalAt().toTuple()) for f in flat]

    def turn(i: int, j: int) -> float:
        return _degrees(normals[i], normals[j])

    return widths, runs(len(flat), widths, neighbours, turn)


def by_points(flat: list[Any]) -> tuple[list[float], list[list[int]]]:
    """The second reading, from points along each face's edges alone: two faces are neighbours
    where they share two points or more, the turn is between the planes fitted to their points,
    either way up, and the width is `drawn_width`. (Widths, runs.)"""
    points = [edge_points(f) for f in flat]
    widths = [drawn_width(f, p) for f, p in zip(flat, points, strict=True)]
    at: dict[tuple[float, ...], set[int]] = {}
    for k, p in enumerate(points):
        for point in np.round(p, POINT_ROUND):
            at.setdefault(tuple(point), set()).add(k)
    shared: dict[tuple[int, int], int] = {}
    for faces in at.values():
        ordered = sorted(faces)
        for n, i in enumerate(ordered):
            for j in ordered[n + 1 :]:
                shared[(i, j)] = shared.get((i, j), 0) + 1
    neighbours = [pair for pair, n in shared.items() if n >= 2]
    normals = [_fitted_normal(p) for p in points]

    def turn(i: int, j: int) -> float:
        between = _degrees(normals[i], normals[j])
        return min(between, 180.0 - between)

    return widths, runs(len(flat), widths, neighbours, turn)


def said(lengths: list[int]) -> str:
    """Runs as the reply says them: "2 runs of strips (the longest 48 faces)"."""
    if not lengths:
        return "no strips"
    runs = "1 run" if len(lengths) == 1 else f"{len(lengths)} runs"
    return f"{runs} of strips (the longest {max(lengths)} faces)"
