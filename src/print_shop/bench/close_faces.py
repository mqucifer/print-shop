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
their edges; but every curved edge is a curved face's too, and meshing that face finer, its
edges with it, makes its edges' chords as fine.

**What's found.** The pairs of the joined parts' faces that share no corner, whose distance
apart is less than the sum of their sags at the mesh, each face's from its own curvature, at
`LONGEST` times the mesh, the longest side gmsh leaves an element: every such pair of two
parts' faces, and any pair of a face within the mesh of where gmsh says its triangles cross.
Their boxes are compared first, each grown by its own sag, then how far apart their surfaces
are at the least (`least_apart`), and only the pairs neither rules out are measured, by OCC.
Only each face's box and curvature is read of all of them: on a design run's table, reading
every face's edges and corners as well met the container's memory.

**What's done** (`load._Model.solve`). The parts are meshed as they always were, and only
where gmsh refuses them for triangles that cross are the pairs found, and meshed again, each
curved face of a pair fine enough that it sags no more than half the gap at its longest sides,
so the two together sag no more than the gap, and the size grows from there to the mesh's own
by one mm in `GRADE`; and again where gmsh refuses them at another place, a
few times at most. gmsh is given that as a local size on those faces and their edges alone,
by a constant field, and every other face is meshed as it was. The test is a bound, not gmsh's
own mesh: on the made-up traced glass frame of `test_cae.py`, a tip rounded 0.1 mm lies 0.02
mm from an arc of its own part and meshes as it is, its elements sized by its own small edges,
so nothing is changed where gmsh meshes the parts. A pair that would need elements finer than
`LEAST`, within the mesh of where gmsh's triangles cross, is refused, naming the parts, the
faces, how far apart they are and how far apart they'd mesh: that's the parts, to be drawn
meeting or further apart. One elsewhere is left as gmsh meshed it. Where gmsh still refuses
them, the faces nearest where it says they cross are named.

Meshed again, no element anywhere is finer than `LEAST`, a quarter of a nozzle's line, and the
reply counts the faces curved tighter than gmsh's own sizing would mesh at it: a curve no print
has, such as a traced frame's micro-arc, is read as the corner it prints as.
"""

from __future__ import annotations

import contextlib
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
#: Each face of a pair is meshed to sag no more than this share of the gap between them, at
#: its longest sides: so the two together sag no more than the gap, and at sides of the size
#: asked for, less than half of it. A quarter left four or five times the margin a crossing
#: needs, and twice the triangles on those faces.
SHARE = 0.5
#: Away from a face meshed finer, the size grows by one mm for this many mm, to the mesh's own:
#: gmsh's elements beside a face meshed finer aren't cut from a fine edge to the mesh's size at
#: once. And the faces it grows from are sampled at this many points along each parameter.
GRADE = 2.0
SAMPLING = 40
#: The finest element anywhere once the parts are meshed again, in mm: a quarter of a 0.4 mm
#: nozzle's line, and half the round `pieces build` gives a corner. gmsh sizes a curve by its
#: own curvature, 12 elements a turn, so a traced glass frame's micro-arc, under a tenth of the
#: nozzle round, which no print has, was meshed in elements a fiftieth of a mm across beside
#: ones of several mm: gmsh couldn't curve them, and straight, CalculiX found hundreds of them
#: turned inside out. Meshed no finer than this, the arc is a corner of the mesh, as it is of
#: the print. Two faces that would need finer are the parts' to fix.
LEAST = 0.1
#: Two faces closer than this, in mm, are taken as meeting where they share no corner: OCC's
#: fuse would have shared them.
TOUCH = 1e-6
#: Each local size is rounded down to one of these steps, so faces of about the same size share
#: one field: a quarter of a doubling.
STEPS = 4
#: Curvature is sampled at this many points along each of a face's parameters,
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
    #: Each curved face's local size, by its number from 1.
    faces: dict[int, float] = dataclasses.field(default_factory=dict)

    @property
    def finest(self) -> float:
        return min(self.faces.values(), default=math.inf)


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


def _share_a_corner(first: Any, second: Any) -> bool:
    """Whether two faces share a corner, by OCC's hash and `isSame`."""
    mine: dict[int, list[Any]] = {}
    for corner in first.Vertexes:
        mine.setdefault(corner.hashCode(), []).append(corner)
    return any(v.isSame(w) for v in second.Vertexes for w in mine.get(v.hashCode(), ()))


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
    owners: list[frozenset[int]] | None = None,
    near: Any = None,
    tight: list[int] | None = None,
) -> list[Close]:
    """The pairs of `shape`'s faces that share no corner and lie closer than their sags at a
    mesh of `size`, with the local sizes that mesh them (`close_faces` above).

    With `owners`, each face's parts by number, a pair is one of two parts' faces, or else of a
    face within the mesh of `near`, a vertex where gmsh says its triangles cross: two parts
    meet only at their joints, but a glass frame's leading is thousands of a part's own faces,
    and measuring every pair of them doesn't fit the container. Each of two faces whose
    triangles cross passes within its sag of where they cross, so the pair there is found.

    Only each face's box and its surface's curvature are read of every face, and what FreeCAD
    caches of each face is let go once it's measured. A pair whose boxes, or surfaces, are
    further apart than their sags is left unmeasured; each measured is put in `known`, by the
    pair's numbers from 0, and taken from it where it's there. With `tight`, the faces curved
    tighter than gmsh's own sizing meshes finer than `LEAST` are put in it, by number from 1."""
    known = {} if known is None else known
    faces = shape.Faces
    count = len(faces)
    low, high, bends = np.empty((count, 3)), np.empty((count, 3)), np.empty(count)
    for k, face in enumerate(faces):
        box = face.BoundBox
        low[k], high[k] = (box.XMin, box.YMin, box.ZMin), (box.XMax, box.YMax, box.ZMax)
        bends[k] = surface_bend(face)
        _let_go(face)
    _let_go(shape)
    if tight is not None:
        # Each face curved tighter than gmsh's elements a turn of it at `LEAST` (`fields`).
        # gmsh's own size there, 2πr over `per_turn`, finer than `LEAST`.
        tight.extend(int(k) + 1 for k in np.nonzero(bends > 2 * math.pi / (per_turn * LEAST))[0])
    reach = np.array([sag(b, size, per_turn) for b in bends])
    here: set[int] = set()
    if near is not None:
        point = np.array([near.Point.x, near.Point.y, near.Point.z])
        out = np.maximum(np.maximum(low - point, point - high), 0.0)
        # The faces themselves within the mesh of it, not only their boxes: a top's box, with
        # every hole in it, takes in every point.
        for k in np.nonzero(np.linalg.norm(out, axis=1) <= size)[0].tolist():
            if near.distToShape(faces[k])[0] <= size:
                here.add(k)
            _let_go(faces[k])
    closes = []
    for i, j in meeting(low, high, reach):
        if (i, j) in known:
            apart = known[(i, j)]
        else:
            if owners is not None and owners[i] & owners[j] and i not in here and j not in here:
                continue
            within = float(reach[i] + reach[j])
            gap = np.maximum(np.maximum(low[i] - high[j], low[j] - high[i]), 0.0)
            if float(np.linalg.norm(gap)) >= within:
                continue
            first, second = faces[i], faces[j]
            if least_apart(first, second) >= within or _share_a_corner(first, second):
                _let_go(first, second)
                continue
            apart = known[(i, j)] = float(first.distToShape(second)[0])
            _let_go(first, second)
        if apart >= reach[i] + reach[j]:
            continue
        close = Close(i + 1, j + 1, apart)
        most = SHARE * apart
        for me in (i, j):
            if bends[me] > 0 and sag(bends[me], size, per_turn) > most:
                close.faces[me + 1] = size_for(bends[me], most) if apart > TOUCH else 0.0
        if close.faces:
            closes.append(close)
    return closes


def _let_go(*shapes: Any) -> None:
    """What FreeCAD has cached of each shape's own faces, edges and corners, let go
    (mqucifer/print-shop#336): read again as it's asked. A document's own shape, which FreeCAD
    hands out as one that can't be changed, keeps its cache, and its faces let go of theirs."""
    for shape in shapes:
        clear = getattr(shape, "clearCache", None)
        if clear is not None:
            with contextlib.suppress(ReferenceError):
                clear()


def too_close(closes: list[Close]) -> list[Close]:
    """The pairs that would need elements finer than `LEAST`: the parts' to fix."""
    return [c for c in closes if c.finest < LEAST]


def apart_for(face: Any, least: float = LEAST, per_turn: int = PER_TURN) -> float:
    """How far apart a face must be from another, in mm, to be meshed at `least` or coarser:
    so its sag there is at most `SHARE` of the gap."""
    return sag(surface_bend(face), least, per_turn) / SHARE


def fields(closes: list[Close], size: float, graded: dict[int, float] | None = None) -> str:
    """gmsh's input for the local sizes: for each step of size, a constant field on its faces
    and their edges, and a threshold that grows it away from them by one mm in `GRADE` to the
    mesh's own; the least of them all as the background field; and no element anywhere finer
    than `LEAST`. And for each face `graded` names, by number from 1, a threshold alone, from
    the size it gives: where CalculiX found elements turned inside out beside it, the mesh
    grows from it to the mesh's own instead of meeting it at once. Nothing where nothing is
    close or graded."""
    faces: dict[int, float] = {}
    for close in closes:
        for n, h in close.faces.items():
            faces[n] = min(faces.get(n, math.inf), h)
    steps: dict[float, list[int]] = {}
    for n, h in faces.items():
        if h < size:
            steps.setdefault(stepped(h), []).append(n)
    grades: dict[float, list[int]] = {}
    for n, h in (graded or {}).items():
        if h < size and n not in faces:
            grades.setdefault(stepped(h), []).append(n)
    if not steps and not grades:
        return ""
    lines = ["// faces closer than the mesh's sag (mqucifer/print-shop#346)"]
    numbers: list[int] = []
    k = 0
    for h, on, fixed in [*((h, on, True) for h, on in sorted(steps.items())),
                         *((h, on, False) for h, on in sorted(grades.items()))]:
        listed = ", ".join(map(str, sorted(on)))
        if fixed:
            k += 1
            numbers.append(k)
            lines += [f"Field[{k}] = Constant;", f"Field[{k}].SurfacesList = {{{listed}}};",
                      f"Field[{k}].VIn = {h:.6g};", f"Field[{k}].VOut = 1e22;",
                      f"Field[{k}].IncludeBoundary = 1;"]  # fmt: skip
        distance, threshold = k + 1, k + 2
        k += 2
        numbers.append(threshold)
        lines += [f"Field[{distance}] = Distance;",
                  f"Field[{distance}].SurfacesList = {{{listed}}};",
                  f"Field[{distance}].Sampling = {SAMPLING};",
                  f"Field[{threshold}] = Threshold;",
                  f"Field[{threshold}].InField = {distance};",
                  f"Field[{threshold}].SizeMin = {h:.6g};",
                  f"Field[{threshold}].SizeMax = {size:.6g};",
                  f"Field[{threshold}].DistMin = 0;",
                  f"Field[{threshold}].DistMax = {GRADE * (size - h):.6g};",
                  f"Field[{threshold}].StopAtDistMax = 1;"]  # fmt: skip
    least = k + 1
    lines += [f"Field[{least}] = Min;",
              f"Field[{least}].FieldsList = {{{', '.join(map(str, numbers))}}};",
              f"Background Field = {least};", f"Mesh.MeshSizeMin = {LEAST:g};", ""]  # fmt: skip
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
