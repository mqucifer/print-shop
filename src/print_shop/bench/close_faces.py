"""Faces closer together than the mesh near them (mqucifer/print-shop#346), and gmsh's local size
there.

gmsh meshes every face of the joined parts, then fills the volumes that share faces as one
Delaunay mesh of all their faces. Each triangle on a curved face is a chord of it, and lies
inside the curve by the chord's sag: on a cylinder of radius r, an element's side c across
sags r - √(r² − c²/4), where gmsh's own curvature sizing leaves c at most 2πr/12. Two faces
that meet nowhere, as a cap sits in its counterbore or a frame's lips sit in a recess with
play round them, are fine as drawn, but where the gap between them is less than their two
sags, one's triangles cross the other's, and gmsh refuses the lot: "PLC Error: A segment and a
facet intersect at point; Invalid boundary mesh (segment-facet intersection)". Turning one of
the parts so its seam isn't the other's is enough to cross them. A curved edge of a flat face
is a chord the same way, so two flat faces side by side, a cap's top in its top's, cross along
their edges.

**What's found.** Every pair of the joined parts' faces that share no corner, whose distance
apart is less than the sum of their sags at the mesh: each face's from its own curvature and
its curved edges', at `LONGEST` times the mesh, the longest side gmsh leaves an element.
Their boxes are compared first, each grown by its own sag, then how far apart their surfaces
are at the least (`least_apart`), and only the pairs neither rules out are measured, by OCC.

**What's done** (`load._Model.solve`). The parts are meshed as they always were, and only
where gmsh refuses them for triangles that cross are the pairs found, and meshed again: each
curved face of a pair fine enough that it sags no more than a quarter of the gap, and each
curved edge of a flat face that's as near the other face the same, so the two together sag no
more than half of it. gmsh is given that as a local size on those faces and edges alone, by a
constant field, and every other face is meshed as it was. The test is a bound, not gmsh's own
mesh: on the made-up traced glass frame of `test_cae.py`, a tip rounded 0.1 mm lies 0.02 mm
from an arc of its own part and meshes as it is, its elements sized by its own small edges,
so nothing is changed where gmsh meshes the parts. A pair that would need elements finer than
`LEAST`, within the mesh of where gmsh's triangles cross, is refused, naming the parts, the
faces, how far apart they are and how far apart they'd mesh: that's the parts, to be drawn
meeting or further apart. One elsewhere is left as gmsh meshed it. Where gmsh still refuses
them, the faces nearest where it says they cross are named.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np

#: The longest side gmsh leaves an element, as a multiple of the size it's asked for: its
#: Delaunay and frontal triangles' sides run from about 0.7 to 1.4 times it.
LONGEST = 1.5
#: gmsh's elements a full turn of a curve, as FreeCAD asks for them (`MeshSizeFromCurvature`).
PER_TURN = 12
#: Each face of a pair is meshed to sag no more than this share of the gap between them.
SHARE = 0.25
#: The finest local size given, in mm: two faces that would need finer are the parts' to fix.
LEAST = 0.05
#: Two faces closer than this, in mm, are taken as meeting where they share no corner: OCC's
#: fuse would have shared them.
TOUCH = 1e-6
#: Each local size is rounded down to one of these steps, so faces of about the same size share
#: one field: a quarter of a doubling.
STEPS = 4
#: Curvature is sampled at this many points along each of a face's or an edge's parameters,
#: where it isn't given by the surface's own radius.
SAMPLES = 5


def sag(curvature: float, size: float, per_turn: int = PER_TURN) -> float:
    """How far a chord of a curve with this curvature, at an element of `size` and its longest
    sides, lies inside it, in mm: none on a flat face or a straight edge."""
    if curvature <= 0:
        return 0.0
    radius = 1.0 / curvature
    chord = LONGEST * min(size, 2 * math.pi * radius / per_turn)
    if chord >= 2 * radius:
        return radius
    return radius - math.sqrt(radius * radius - chord * chord / 4)


def size_for(curvature: float, most: float) -> float:
    """The element size whose longest sides sag no more than `most` on a curve this curved."""
    if curvature <= 0:
        return math.inf
    radius = 1.0 / curvature
    if most >= radius:
        return 2 * radius / LONGEST
    return 2 * math.sqrt(2 * radius * most - most * most) / LONGEST


def stepped(size: float) -> float:
    """`size` rounded down to a step of `STEPS` a doubling, and no finer than `LEAST`."""
    if size <= LEAST:
        return LEAST
    return max(float(2 ** (math.floor(STEPS * math.log2(size)) / STEPS)), LEAST)


def meeting(low: np.ndarray, high: np.ndarray, reach: np.ndarray) -> Iterator[tuple[int, int]]:
    """Each pair of boxes, `low` to `high` each, that meet once each is grown by its own reach,
    the pair's first the lower: so their gap along every axis is within their two reaches.
    A box with no reach meets only one that has some."""
    grown_low, grown_high = low - reach[:, None], high + reach[:, None]
    order = np.argsort(grown_low[:, 0], kind="stable")
    by_low, by_high = grown_low[order], grown_high[order]
    curved = reach[order] > 0
    for k in range(len(order)):
        end = int(np.searchsorted(by_low[:, 0], by_high[k, 0], side="right"))
        if end <= k + 1:
            continue
        rest = slice(k + 1, end)
        meets = np.all(by_low[rest, 1:] <= by_high[k, 1:], axis=1) & np.all(
            by_high[rest, 1:] >= by_low[k, 1:], axis=1
        )
        if not curved[k]:
            meets &= curved[rest]
        for j in np.nonzero(meets)[0]:
            first, second = int(order[k]), int(order[k + 1 + j])
            yield min(first, second), max(first, second)


@dataclasses.dataclass
class Close:
    """Two faces closer than their mesh's sag: their numbers in the joined shape, from 1, as
    FreeCAD and gmsh number them, and how far apart they are, in mm."""

    first: int
    second: int
    apart: float
    #: Each curved face's and each curved edge's local size, by its number from 1.
    faces: dict[int, float] = dataclasses.field(default_factory=dict)
    edges: dict[int, float] = dataclasses.field(default_factory=dict)

    @property
    def finest(self) -> float:
        return min([*self.faces.values(), *self.edges.values()], default=math.inf)


def surface_bend(face: Any) -> float:
    """A face's greatest curvature, 1/mm: a plane's none, a cylinder's and a sphere's one over
    the radius, a torus's over its smaller radius, and any other's the most of its principal
    curvatures at `SAMPLES` points along each parameter."""
    surface = face.Surface
    kind = type(surface).__name__
    if kind == "Plane":
        return 0.0
    if kind in ("Cylinder", "Sphere"):
        return 1.0 / float(surface.Radius)
    if kind == "Toroid":
        return 1.0 / float(surface.MinorRadius)
    u0, u1, v0, v1 = face.ParameterRange
    most = 0.0
    for u in np.linspace(u0, u1, SAMPLES):
        for v in np.linspace(v0, v1, SAMPLES):
            try:
                most = max(most, *(abs(k) for k in face.curvatureAt(float(u), float(v))))
            except Exception:  # noqa: BLE001 - a point where OCC can't say, as a pole
                continue
    return most


def edge_bend(edge: Any) -> float:
    """An edge's greatest curvature, 1/mm: a line's none, a circle's one over its radius, and
    any other's the most at `SAMPLES` points along it."""
    curve = edge.Curve
    kind = type(curve).__name__
    if kind in ("Line", "LineSegment"):
        return 0.0
    if kind in ("Circle", "ArcOfCircle"):
        return 1.0 / float(curve.Radius)
    most = 0.0
    for t in np.linspace(edge.FirstParameter, edge.LastParameter, SAMPLES):
        try:
            most = max(most, abs(edge.curvatureAt(float(t))))
        except Exception:  # noqa: BLE001 - a point where OCC can't say
            continue
    return most


def _hashed(shapes: list[Any]) -> dict[int, list[Any]]:
    found: dict[int, list[Any]] = {}
    for shape in shapes:
        found.setdefault(shape.hashCode(), []).append(shape)
    return found


def _share_a_corner(first: dict[int, list[Any]], second: list[Any]) -> bool:
    return any(v.isSame(w) for v in second for w in first.get(v.hashCode(), ()))


def _beside_plane(plane: Any, other: Any) -> float:
    """How far a face's box lies to one side of a plane face's plane, in mm: none where the
    plane passes through it."""
    surface = plane.Surface
    normal = np.array(surface.Axis) / np.linalg.norm(np.array(surface.Axis))
    box = other.BoundBox
    corners = np.array([[x, y, z] for x in (box.XMin, box.XMax) for y in (box.YMin, box.YMax)
                        for z in (box.ZMin, box.ZMax)])  # fmt: skip
    sides = (corners - np.array(surface.Position)) @ normal
    if sides.min() > 0 or sides.max() < 0:
        return float(np.abs(sides).min())
    return 0.0


def least_apart(first: Any, second: Any) -> float:
    """How far apart two faces are at the least, in mm: the most of what their surfaces give,
    as if each went on without end (two cylinders on parallel axes, a cylinder and a plane along
    its axis, two parallel planes), and how far either's box lies beside the other's plane.
    Any other pair, none: OCC's own distance is then the only one."""
    beside = max(
        [_beside_plane(a, b) for a, b in ((first, second), (second, first))
         if type(a.Surface).__name__ == "Plane"],
        default=0.0,
    )  # fmt: skip
    return max(beside, _surfaces_apart(first, second))


def _surfaces_apart(first: Any, second: Any) -> float:
    kinds = (type(first.Surface).__name__, type(second.Surface).__name__)
    if kinds == ("Plane", "Cylinder"):
        first, second = second, first
        kinds = ("Cylinder", "Plane")
    a, b = first.Surface, second.Surface
    if kinds == ("Cylinder", "Cylinder"):
        axis = np.array(a.Axis) / np.linalg.norm(np.array(a.Axis))
        other = np.array(b.Axis) / np.linalg.norm(np.array(b.Axis))
        if np.linalg.norm(np.cross(axis, other)) > 1e-9:
            return 0.0
        offset = np.array(b.Center) - np.array(a.Center)
        between = float(np.linalg.norm(offset - (offset @ axis) * axis))
        ra, rb = float(a.Radius), float(b.Radius)
        return max(between - ra - rb, abs(ra - rb) - between, 0.0)
    if kinds == ("Cylinder", "Plane"):
        axis = np.array(a.Axis) / np.linalg.norm(np.array(a.Axis))
        normal = np.array(b.Axis) / np.linalg.norm(np.array(b.Axis))
        if abs(float(axis @ normal)) > 1e-9:
            return 0.0
        off = abs(float((np.array(a.Center) - np.array(b.Position)) @ normal))
        return max(off - float(a.Radius), 0.0)
    if kinds == ("Plane", "Plane"):
        normal = np.array(a.Axis) / np.linalg.norm(np.array(a.Axis))
        if np.linalg.norm(np.cross(normal, np.array(b.Axis))) > 1e-9:
            return 0.0
        return abs(float((np.array(b.Position) - np.array(a.Position)) @ normal))
    return 0.0


def found(
    shape: Any,
    size: float,
    per_turn: int = PER_TURN,
    known: dict[tuple[int, int], float] | None = None,
) -> list[Close]:
    """Every pair of `shape`'s faces that share no corner and lie closer than their sags at a
    mesh of `size`, with the local sizes that mesh them (`close_faces` above). Each pair whose
    surfaces, or boxes, are further apart than that is left unmeasured; each measured is put in
    `known`, by the pair's numbers from 0, and taken from it where it's there: the same shape
    is asked of at two meshes, curved and straight."""
    known = {} if known is None else known
    faces = shape.Faces
    edges = shape.Edges
    edge_number: dict[int, list[tuple[int, Any]]] = {}
    for n, edge in enumerate(edges, 1):
        edge_number.setdefault(edge.hashCode(), []).append((n, edge))

    def number_of(edge: Any) -> int:
        for n, other in edge_number.get(edge.hashCode(), ()):
            if edge.isSame(other):
                return n
        raise ValueError("an edge of a face isn't one of the shape's")

    bends = [surface_bend(f) for f in faces]
    curved_edges: list[list[tuple[Any, float]]] = []
    for face in faces:
        mine = [(e, edge_bend(e)) for e in face.Edges]
        curved_edges.append([(e, k) for e, k in mine if k > 0])
    reach = np.array([
        max([sag(bends[i], size, per_turn), *(sag(k, size, per_turn) for _, k in curved_edges[i])])
        for i in range(len(faces))
    ])  # fmt: skip
    boxes = [f.BoundBox for f in faces]
    low = np.array([[b.XMin, b.YMin, b.ZMin] for b in boxes]).reshape(-1, 3)
    high = np.array([[b.XMax, b.YMax, b.ZMax] for b in boxes]).reshape(-1, 3)
    corners = [_hashed(f.Vertexes) for f in faces]
    closes = []
    for i, j in meeting(low, high, reach):
        if (i, j) in known:
            apart = known[(i, j)]
        else:
            if _share_a_corner(corners[i], faces[j].Vertexes):
                continue
            within = float(reach[i] + reach[j])
            gap = np.maximum(np.maximum(low[i] - high[j], low[j] - high[i]), 0.0)
            if max(float(np.linalg.norm(gap)), least_apart(faces[i], faces[j])) >= within:
                continue
            apart = known[(i, j)] = float(faces[i].distToShape(faces[j])[0])
        if apart >= reach[i] + reach[j]:
            continue
        close = Close(i + 1, j + 1, apart)
        most = SHARE * apart
        for me, other in ((i, j), (j, i)):
            if bends[me] > 0 and sag(bends[me], size, per_turn) > most:
                close.faces[me + 1] = size_for(bends[me], most) if apart > TOUCH else 0.0
            for edge, bend in curved_edges[me]:
                edge_sag = sag(bend, size, per_turn)
                if edge_sag <= most or (bends[me] > 0 and bend <= bends[me]):
                    # As straight as its face needs, or meshed with its face at the face's size.
                    continue
                if not _within(edge, faces[other], edge_sag + reach[other]):
                    continue
                close.edges[number_of(edge)] = size_for(bend, most) if apart > TOUCH else 0.0
        if close.faces or close.edges:
            closes.append(close)
    return closes


def _within(edge: Any, face: Any, reach: float) -> bool:
    """Whether an edge comes within `reach` of a face: their boxes first."""
    if not edge.BoundBox.intersect(_grown(face.BoundBox, reach)):
        return False
    return bool(edge.distToShape(face)[0] < reach)


def _grown(box: Any, by: float) -> Any:
    grown = box.__class__(box)
    grown.enlarge(by)
    return grown


def too_close(closes: list[Close]) -> list[Close]:
    """The pairs that would need elements finer than `LEAST`: the parts' to fix."""
    return [c for c in closes if c.finest < LEAST]


def apart_for(face: Any, least: float = LEAST, per_turn: int = PER_TURN) -> float:
    """How far apart a face must be from another, in mm, to be meshed at `least` or coarser:
    so its sag there, and its curved edges', is at most `SHARE` of the gap."""
    bend = max([surface_bend(face), *(edge_bend(e) for e in face.Edges)])
    return sag(bend, least, per_turn) / SHARE


def fields(closes: list[Close], size: float) -> str:
    """gmsh's input for the local sizes: a constant field for each step of size, on its faces
    and edges and their own edges, and the least of them as the background field. Nothing where
    nothing is close."""
    faces: dict[int, float] = {}
    edges: dict[int, float] = {}
    for close in closes:
        for n, h in close.faces.items():
            faces[n] = min(faces.get(n, math.inf), h)
        for n, h in close.edges.items():
            edges[n] = min(edges.get(n, math.inf), h)
    steps: dict[float, tuple[list[int], list[int]]] = {}
    for kind, sizes in ((0, faces), (1, edges)):
        for n, h in sizes.items():
            if h >= size:
                continue
            steps.setdefault(stepped(h), ([], []))[kind].append(n)
    if not steps:
        return ""
    lines = ["// faces closer than the mesh's sag (mqucifer/print-shop#346)"]
    numbers = []
    for k, (h, (on_faces, on_edges)) in enumerate(sorted(steps.items()), 1):
        numbers.append(k)
        lines.append(f"Field[{k}] = Constant;")
        if on_faces:
            lines.append(f"Field[{k}].SurfacesList = {{{', '.join(map(str, sorted(on_faces)))}}};")
        if on_edges:
            lines.append(f"Field[{k}].CurvesList = {{{', '.join(map(str, sorted(on_edges)))}}};")
        lines += [f"Field[{k}].VIn = {h:.6g};", f"Field[{k}].VOut = 1e22;",
                  f"Field[{k}].IncludeBoundary = 1;"]  # fmt: skip
    least = len(numbers) + 1
    lines += [f"Field[{least}] = Min;",
              f"Field[{least}].FieldsList = {{{', '.join(map(str, numbers))}}};",
              f"Background Field = {least};", ""]  # fmt: skip
    return "\n".join(lines)


def kind_of(face: Any) -> str:
    """A face's kind in words: its surface's, as `load` names faces."""
    return {
        "Plane": "flat", "Cylinder": "cylindrical", "Cone": "conical", "Sphere": "spherical",
        "Toroid": "toroidal", "BSplineSurface": "B-spline", "BezierSurface": "Bézier",
        "SurfaceOfRevolution": "revolved", "SurfaceOfExtrusion": "extruded",
    }.get(type(face.Surface).__name__, type(face.Surface).__name__)


def said(
    faces: list[Any],
    closes: list[Close],
    owners: Callable[[Any], list[str]],
    where: Callable[[Any], str],
) -> str:
    """The pairs too close to mesh, in words: each one's parts, its two faces, how far apart
    they are and how far apart they'd mesh at `LEAST`."""
    out = []
    for close in sorted(closes, key=lambda c: c.apart):
        first, second = faces[close.first - 1], faces[close.second - 1]
        need = max(apart_for(first), apart_for(second))
        parts = sorted({*owners(first), *owners(second)})
        out.append(
            f"{' and '.join(parts)}: the {_face_said(first, owners, where)} and the "
            f"{_face_said(second, owners, where)} are {close.apart:.4f} mm apart, and meet "
            f"nowhere; they'd mesh {need:.4f} mm or more apart"
        )
    return "; ".join(out)


def _face_said(face: Any, owners: Callable[[Any], list[str]], where: Callable[[Any], str]) -> str:
    surface = face.Surface
    radius = getattr(surface, "Radius", None)
    shape = f" of radius {radius:g} mm" if isinstance(radius, float) else ""
    return f"{'/'.join(owners(face))}'s {kind_of(face)} face{shape} round {where(face)}"
