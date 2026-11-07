"""Each outside edge of a part: sharp or rounded, and the least radius of the rounded ones
(mqucifer/print-shop#60).

The Sponsor wants a case's edges, corners and lid softened, and nothing on the bench read an
edge. This reads them on the exact surface of a STEP file, and checks each by plane sections of
its mesh.

**An edge** is where two faces meet. At points along it, the angle the faces' normals turn
through: under `SMOOTH_DEG` it's smooth, where a rounding meets a face, and over it, sharp. A
sharp edge is outside, convex, when a point a little way into each face, and then between the
two, is in the part, and otherwise it's an inside corner, which isn't read. **A rounded edge**
is a curved face that meets a neighbour smoothly: a fillet, or a corner where fillets meet. Its
radius is the least radius it bends through convexly, read on a grid over the face. A curved
face that meets a neighbour smoothly is read as rounding, whatever it was built as.

**Outside** is what a hand can reach: a ball `FINGER_MM` across, the test finger's width (IEC
60529's IPXX-B and IEC 61032's test probe B are 12 mm across), coming from outside every file
named, and touching the edge from between its faces (`MEETS`), not only flat-on to one of them.
So the inside of a closed case isn't read when its lid is named too, nor a seat flush with the
lid, nor the rim of a slot too narrow for a fingertip, and an edge round a plug's opening is.
Every file named blocks the ball. A file under `references/`, or named with `--with`, isn't read:
a reference's edges aren't the part's. The reach is read on a grid: each layer of every file is
sliced, and Euclidean distance transforms find where a ball's centre fits and which of those
places join the outside. Where a ball would touch an edge, its centre is held clear of every file
by the mesh's exact distance, within `EXACT_BAND`.

**A sharp edge is radius 0**, so `least_radius_mm` is 0 where one is in reach, and
`--min-radius R` fails a sharp edge or a rounding under R. A rounding drawn at R passes: its
radius may read short of it by `RADIUS_SHARE` of R, or `RADIUS_FLOOR`, which is OCCT's
arithmetic on a spline's curvature. A rounding stored as flat strips reads as sharp edges, each
turning a few degrees: the reply says so, as a curve built as a curve is one rounded face.

**By group and by place** (mqucifer/print-shop#65). On `eval-edges-1` a need on the four upright
corners read `rounded.19.radius_mm`, the 20th rounding in a list sorted by radius, which a rebuild
can move, and three long calls went on picking corners out of the list. So each edge and rounding
read is also in a group, in the file's axes: `upright` where it runs within `UPRIGHT_DEG` of z,
and otherwise `top` or `bottom` where it faces more than `LEVEL_DEG` above or below level, and
`side` for the rest, a level edge facing sideways. Each group's least radius is a number a need
can name, `groups.upright.least_radius_mm`. `--box NAME=X0,Y0,Z0,X1,Y1,Z1` and `--facing NAME=DIR`
read the same of what lies in a box or faces a way, as `selected.NAME`. Each group's and
selection's least is among those the second method checks.

**Second checks.** At each sharp edge read, the fine mesh cut by a plane square to the edge, and
the angle its outline turns through at the edge, within the chords on both faces. At each
rounded face's least radius, the mesh cut along the direction it bends most, and a circle
fitted to the outline's points on that face. And at each edge's touch point, the ball's centre
the grid found, held clear of every file by the mesh's exact distance. That the centre got there
from outside is the grid's alone.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import numpy as np
import shapely
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepTools import BRepTools
from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Dir, gp_Pnt, gp_Pnt2d, gp_Vec
from OCP.ShapeAnalysis import ShapeAnalysis_Surface
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from scipy import ndimage

from .readback import _circle
from .report import Report
from .shapes import FINE, NoAnswer, Part, load, slice_at

#: Two faces whose normals turn through less than this, in degrees, meet smoothly.
SMOOTH_DEG = 1.0
#: A sharp edge turning less than this, in degrees, may be a rounding stored as flat strips.
FACETED_DEG = 20.0
#: The ball that reaches an outside edge, in mm across: the test finger of IEC 60529 (IPXX-B) and
#: IEC 61032 (test probe B), 12 mm across.
FINGER_MM = 12.0
#: The grid the reach is read on: at least this fine, in mm, and at most this many cells.
GRID_MM = 0.25
GRID_CELLS = 8_000_000
#: A point is in reach when a ball's centre is within its radius and this many grid steps.
REACH_STEPS = 1.5
#: Where a ball meets a sharp edge from: so far round from one face's normal to the other's.
MEETS = (0.25, 0.5, 0.75)
#: How far a ball may cut into a file and still fit, in mm: the coarse mesh's chords on both.
EXACT_BAND = 0.021
#: How far into each face an edge's side is read from, in mm.
CONVEX_STEP = 0.01
#: Along an edge: a point every so many mm, at least 3 and at most so many.
EDGE_STEP_MM = 2.0
EDGE_MOST = 25
#: Over a curved face: a grid of points, so many each way.
FACE_GRID = 9
#: How far a rounding drawn at the limit may read short of it.
RADIUS_SHARE = 0.005
RADIUS_FLOOR = 0.005
#: The most sharp edges and rounded faces the sections check, spread over all of them.
SECOND_MOST = 150
#: How far the sections' angle may differ, in degrees, beyond the chords on both faces.
ANGLE_BAND_DEG = 1.0
#: How far the sections' radius may differ: so much, and a share of the radius.
RADIUS_BAND = 0.02
RADIUS_BAND_SHARE = 0.03
#: How many places a reply names before it says how many more.
NAMED = 8
#: An edge in the `upright` group runs within this of z, in degrees.
UPRIGHT_DEG = 30.0
#: An edge in the `top` or `bottom` group faces more than this above or below level, in degrees.
LEVEL_DEG = 20.0
#: An edge `--facing` a way faces within this of it, in degrees: so a box's edges round its front
#: face, each 45° from it, are read, and those round its back, 135° away, aren't.
FACING_DEG = 60.0
#: The groups, in the order a reply says them.
GROUPS = ("upright", "top", "bottom", "side")
#: The directions `--facing` takes.
DIRECTIONS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}


def _xyz(p: Any) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


def _at(p: np.ndarray) -> list[float]:
    return [round(float(v), 2) for v in p]


def _shown(p: Any) -> str:
    return "(" + ", ".join(f"{float(v):.2f}" for v in p) + ")"


class Face:
    """One face of the exact surface: where a point is on it, and its outward normal and
    curvature there."""

    def __init__(self, face: Any) -> None:
        self.face = face
        self.adaptor = BRepAdaptor_Surface(face)
        self.analysis = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face))
        self.flip = face.Orientation() == TopAbs_REVERSED
        self.plane = self.adaptor.GetType() == GeomAbs_Plane
        self.kind = str(self.adaptor.GetType()).rsplit("_", 1)[-1].upper()
        self._inside: Any = None

    def uv(self, point: np.ndarray) -> tuple[float, float, float]:
        """The point's (u, v) on the face's surface, and how far it is from it."""
        found = self.analysis.ValueOfUV(gp_Pnt(*map(float, point)), 1e-7)
        return found.X(), found.Y(), float(self.analysis.Gap())

    def contains(self, u: float, v: float) -> bool:
        if self._inside is None:
            self._inside = BRepTopAdaptor_FClass2d(self.face, 1e-7)
        return self._inside.Perform(gp_Pnt2d(u, v)) in (TopAbs_IN, TopAbs_ON)

    def props(self, u: float, v: float) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
        """The point, its outward normal, the most it bends convexly (1/mm, 0 for none), and
        the direction it bends that much."""
        props = BRepLProp_SLProps(self.adaptor, u, v, 2, 1e-7)
        point = _xyz(props.Value())
        if not props.IsNormalDefined():
            return point, np.zeros(3), 0.0, np.zeros(3)
        sign = -1.0 if self.flip else 1.0
        normal = sign * _xyz(props.Normal())
        if self.plane or not props.IsCurvatureDefined():
            return point, normal, 0.0, np.zeros(3)
        most, least = gp_Dir(), gp_Dir()
        props.CurvatureDirections(most, least)
        # OCCT's curvatures are signed against the surface's own normal: a surface bending away
        # from its outward normal, as a rounded edge does, has negative curvature against it.
        bends = [
            (-sign * props.MaxCurvature(), _xyz(most)),
            (-sign * props.MinCurvature(), _xyz(least)),
        ]
        k, direction = max(bends, key=lambda b: b[0])
        return point, normal, max(0.0, float(k)), direction


@dataclass
class Sharp:
    """A sharp outside edge, as read."""

    file: str
    points: list[np.ndarray]
    turns: list[float]
    tangent: np.ndarray
    kinds: tuple[str, str]
    faces: tuple[int, int]
    length: float
    outward: np.ndarray = field(default_factory=lambda: np.zeros(3))

    def worst(self) -> int:
        return int(np.argmax(self.turns))

    def plain(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "at": _at(self.points[self.worst()]),
            "from": _at(self.points[0]),
            "to": _at(self.points[-1]),
            "length_mm": round(self.length, 2),
            "turn_deg": round(max(self.turns), 1),
            "faces": list(self.kinds),
            "group": self.group(),
        }

    def group(self) -> str:
        return group(self.tangent, self.outward)

    def where(self) -> list[np.ndarray]:
        return self.points


@dataclass
class Rounded:
    """A rounded face in reach, and its least radius."""

    file: str
    index: int
    kind: str
    radius: float
    at: np.ndarray
    normal: np.ndarray
    bends: np.ndarray
    spots: int = 0
    second: dict[str, Any] = field(default_factory=dict)
    outward: np.ndarray = field(default_factory=lambda: np.zeros(3))
    points: list[np.ndarray] = field(default_factory=list)

    def plain(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "at": _at(self.at),
            "radius_mm": round(self.radius, 3),
            "face": self.kind,
            "spots_in_reach": self.spots,
            "group": self.group(),
        }

    def axis(self) -> np.ndarray:
        """The way it runs at its least radius: square to its normal and to where it bends."""
        return np.cross(self.normal, self.bends)

    def group(self) -> str:
        # A rounding that faces up or down is a top's or a bottom's, whichever way its least
        # radius runs: a ball's corner bends alike both ways, so it has no way of its own.
        if abs(float(self.outward[2])) >= math.sin(math.radians(LEVEL_DEG)):
            return group(np.zeros(3), self.outward)
        return group(self.axis(), self.outward)

    def where(self) -> list[np.ndarray]:
        return self.points


def _unit(v: np.ndarray) -> np.ndarray:
    size = float(np.linalg.norm(v))
    return v / size if size > 1e-12 else np.zeros(3)


def group(runs: np.ndarray, outward: np.ndarray) -> str:
    """An edge's group, in the file's axes: `upright` if it runs within `UPRIGHT_DEG` of z, else
    `top` or `bottom` if it faces more than `LEVEL_DEG` above or below level, else `side`."""
    runs, outward = _unit(np.asarray(runs, float)), _unit(np.asarray(outward, float))
    if abs(float(runs[2])) >= math.cos(math.radians(UPRIGHT_DEG)):
        return "upright"
    level = math.sin(math.radians(LEVEL_DEG))
    if outward[2] > level:
        return "top"
    if outward[2] < -level:
        return "bottom"
    return "side"


class Reach:
    """Where a ball `FINGER_MM` across, from outside every file named, can touch: on a grid."""

    def __init__(self, parts: list[Part]) -> None:
        self.radius = FINGER_MM / 2
        low = np.min([p.extent()[0] for p in parts], axis=0)
        high = np.max([p.extent()[1] for p in parts], axis=0)
        pad = self.radius + 4 * GRID_MM
        low, high = low - pad, high + pad
        size = high - low
        self.step = max(GRID_MM, float((np.prod(size) / GRID_CELLS) ** (1 / 3)))
        counts = np.maximum(1, np.ceil(size / self.step).astype(int))
        self.low = low
        self.counts = counts
        centres = [low[i] + (np.arange(counts[i]) + 0.5) * self.step for i in range(3)]
        xs, ys = np.meshgrid(centres[0], centres[1])
        solid = np.zeros((counts[2], counts[1], counts[0]), dtype=bool)
        for k, z in enumerate(centres[2]):
            for part in parts:
                section = slice_at(part.manifold(False), float(z))
                if not section.is_empty:
                    solid[k] |= shapely.contains_xy(section, xs, ys)
        # The distance from each free cell's centre to the nearest solid cell's: a ball's centre
        # fits where that's its radius. Rounded toward fitting, so an edge a ball might touch
        # is read rather than missed.
        free = ndimage.distance_transform_edt(~solid) * self.step
        fits = free >= self.radius
        labels, _ = ndimage.label(fits)
        rim = np.zeros_like(fits)
        rim[[0, -1], :, :] = rim[:, [0, -1], :] = rim[:, :, [0, -1]] = True
        outside = np.setdiff1d(np.unique(labels[rim & fits]), [0])
        reached = np.isin(labels, outside)
        if not reached.any():
            raise NoAnswer("no ball's centre fits outside the files on the grid")
        distance, nearest = ndimage.distance_transform_edt(~reached, return_indices=True)
        self.distance = distance * self.step
        self.nearest = nearest
        self.cells = int(np.prod(counts))
        self.whole = trimesh.util.concatenate([p.meshes()[0] for p in parts])

    def _cell(self, point: np.ndarray) -> tuple[int, int, int]:
        i = np.clip(((point - self.low) / self.step).astype(int), 0, self.counts - 1)
        return int(i[2]), int(i[1]), int(i[0])

    def touches(self, point: np.ndarray) -> bool:
        return bool(self.distance[self._cell(point)] <= self.radius + REACH_STEPS * self.step)

    def fits(self, centres: np.ndarray) -> np.ndarray:
        """Whether a ball centred at each point clears every file, by the mesh's exact distance,
        and the grid joins it to the outside."""
        _, clear, _ = trimesh.proximity.closest_point(self.whole, centres)  # type: ignore[no-untyped-call]
        joined = np.array(
            [self.distance[self._cell(c)] <= REACH_STEPS * self.step for c in centres]
        )
        return np.asarray((np.asarray(clear) >= self.radius - EXACT_BAND) & joined, dtype=bool)

    def meets(self, point: np.ndarray, n1: np.ndarray, n2: np.ndarray) -> bool:
        """Whether a ball touches the edge at the point from between its faces, not only flat-on
        to one of them, as across a seam flush with another file."""
        centres = []
        for f in MEETS:
            way = (1 - f) * n1 + f * n2
            size = float(np.linalg.norm(way))
            if size > 1e-9:
                centres.append(point + self.radius * way / size)
        return bool(centres) and bool(self.fits(np.array(centres)).any())

    def centre(self, point: np.ndarray) -> np.ndarray:
        """The ball's centre the grid found nearest the point."""
        k, j, i = self._cell(point)
        found = self.nearest[:, k, j, i]
        return self.low + (np.array([found[2], found[1], found[0]]) + 0.5) * self.step


def _samples(edge: Any) -> tuple[list[np.ndarray], list[np.ndarray], float]:
    """Points along an edge, away from its ends, and its tangent at each."""
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    length = float(GCPnts_AbscissaPoint.Length_s(curve))
    n = min(EDGE_MOST, max(3, math.ceil(length / EDGE_STEP_MM)))
    points, tangents = [], []
    # A little off the half-steps, so a section rarely passes through one of the mesh's vertices.
    for f in (np.arange(n) + 0.5 + 0.0618) / (n + 0.2):
        p, d = gp_Pnt(), gp_Vec()
        curve.D1(first + f * (last - first), p, d)
        points.append(_xyz(p))
        size = d.Magnitude()
        tangents.append(_xyz(d) / size if size > 1e-12 else np.zeros(3))
    return points, tangents, length


def _turn(n1: np.ndarray, n2: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(n1 @ n2, -1.0, 1.0))))


class Solid:
    """One solid of a file read: its faces, its edges as sharp or smooth, and its roundings."""

    def __init__(self, name: str, part: Part, reach: Reach) -> None:
        self.name = name
        self.part = part
        self.reach = reach
        self.faces: dict[int, Face] = {}
        self.sharp: list[Sharp] = []
        self.rounded: list[Rounded] = []
        self.counts = {
            "smooth": 0,
            "inside_corners": 0,
            "out_of_reach": 0,
            "not_met": 0,
            "seams": 0,
        }
        self._classifier = BRepClass3d_SolidClassifier(part.shape.wrapped)
        self._read()

    def _face(self, index: int, faces: Any) -> Face:
        if index not in self.faces:
            self.faces[index] = Face(TopoDS.Face_s(faces.FindKey(index)))
        return self.faces[index]

    def _convex(
        self, point: np.ndarray, tangent: np.ndarray, faces: tuple[Face, Face], normals: Any
    ) -> bool:
        """Whether the part lies between its faces as they leave the edge: a point a little way
        into each face, and then between the two, is in the part at an outside edge and in the air
        at an inside corner."""
        ways = []
        for face, normal in zip(faces, normals, strict=True):
            way = np.cross(normal, tangent)
            size = float(np.linalg.norm(way))
            if size < 1e-9:
                return True
            way /= size
            for signed in (way, -way):
                u, v, gap = face.uv(point + CONVEX_STEP * signed)
                if gap <= 1e-4 and face.contains(u, v):
                    ways.append(signed)
                    break
            else:
                return True  # a face too small to step into: read the edge, not lose it
        between = ways[0] + ways[1]
        size = float(np.linalg.norm(between))
        if size < 1e-9:
            return True  # faces leaving it in line: no corner either way
        inward = point + CONVEX_STEP * between / size
        self._classifier.Perform(gp_Pnt(*map(float, inward)), 1e-7)
        return bool(self._classifier.State() == TopAbs_IN)

    def _read(self) -> None:
        shape = self.part.shape.wrapped
        faces = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_FACE, TopAbs_FACE, faces)
        edges = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edges)
        smooth_faces: set[int] = set()
        for e in range(1, edges.Extent() + 1):
            edge = TopoDS.Edge_s(edges.FindKey(e))
            found = list(edges.FindFromIndex(e))
            indices = sorted({faces.FindIndex(f) for f in found})
            if BRep_Tool.Degenerated_s(edge) or len(indices) != 2:
                self.counts["seams"] += 1
                continue
            a, b = self._face(indices[0], faces), self._face(indices[1], faces)
            points, tangents, length = _samples(edge)
            turns: list[float] = []
            normals: list[tuple[np.ndarray, np.ndarray]] = []
            for p in points:
                n1 = a.props(*a.uv(p)[:2])[1]
                n2 = b.props(*b.uv(p)[:2])[1]
                turns.append(_turn(n1, n2))
                normals.append((n1, n2))
            sharp = [i for i, t in enumerate(turns) if t >= SMOOTH_DEG]
            if not sharp:
                self.counts["smooth"] += 1
                smooth_faces.update(indices)
                continue
            # Convex or not, at no more than three points of it: an edge doesn't change sides.
            picks = sharp[:: max(1, len(sharp) // 3)][:3]
            votes = [self._convex(points[i], tangents[i], (a, b), normals[i]) for i in picks]
            if sum(votes) * 2 <= len(votes):
                self.counts["inside_corners"] += 1
                continue
            touched = [i for i in sharp if self.reach.touches(points[i])]
            if not touched:
                self.counts["out_of_reach"] += 1
                continue
            met = touched[:: max(1, len(touched) // 3)][:3]
            if not any(self.reach.meets(points[i], *normals[i]) for i in met):
                self.counts["not_met"] += 1
                continue
            mid = touched[len(touched) // 2]
            outward = _unit(sum((normals[i][0] + normals[i][1] for i in touched), np.zeros(3)))
            self.sharp.append(
                Sharp(
                    self.name,
                    [points[i] for i in touched],
                    [turns[i] for i in touched],
                    tangents[mid],
                    (a.kind, b.kind),
                    (indices[0], indices[1]),
                    length,
                    outward,
                )
            )
        for index in sorted(smooth_faces):
            face = self._face(index, faces)
            if not face.plane:
                self._rounding(index, face)

    def _rounding(self, index: int, face: Face) -> None:
        """The least radius a curved face bends through convexly, where a ball reaches it."""
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face.face)
        centre = np.array([(u0 + u1) / 2, (v0 + v1) / 2])
        span = np.array([max(u1 - u0, 1e-12), max(v1 - v0, 1e-12)])
        found: list[tuple[float, float, np.ndarray, np.ndarray, np.ndarray]] = []
        fractions = (np.arange(FACE_GRID) + 0.5) / FACE_GRID
        for fu in fractions:
            for fv in fractions:
                u, v = u0 + fu * (u1 - u0), v0 + fv * (v1 - v0)
                if not face.contains(u, v):
                    continue
                point, normal, k, bends = face.props(u, v)
                if k <= 1e-9 or not self.reach.touches(point):
                    continue
                off = float(np.linalg.norm((np.array([u, v]) - centre) / span))
                found.append((1.0 / k, off, point, normal, bends))
        if not found:
            return
        fits = self.reach.fits(np.array([f[2] + self.reach.radius * f[3] for f in found]))
        met = [f for f, ok in zip(found, fits, strict=True) if ok]
        if not met:
            self.counts["not_met"] += 1
            return
        # The least radius; between equal ones, the spot nearest the face's middle, where the
        # section's window stays on the face.
        best = min(met, key=lambda f: (round(f[0], 6), f[1]))
        outward = _unit(sum((f[3] for f in met), np.zeros(3)))
        self.rounded.append(
            Rounded(
                self.name,
                index,
                face.kind,
                best[0],
                best[2],
                best[3],
                best[4],
                len(met),
                outward=outward,
                points=[f[2] for f in met],
            )
        )


def _segments_near(
    mesh: trimesh.Trimesh, point: np.ndarray, normal: np.ndarray, window: float
) -> np.ndarray:
    """The mesh's outline on the plane through the point, square to `normal`, near the point."""
    box = np.r_[point - window, point + window]
    near = list(mesh.triangles_tree.intersection(box))
    if not near:
        return np.zeros((0, 2, 3))
    local = trimesh.Trimesh(mesh.vertices, mesh.faces[near], process=False)
    lines = trimesh.intersections.mesh_plane(local, normal, point)  # type: ignore[no-untyped-call]
    return np.asarray(lines, dtype=float).reshape(-1, 2, 3)


def section_turn(mesh: trimesh.Trimesh, sharp: Sharp, solid: Solid) -> dict[str, Any]:
    """The angle the mesh's outline turns through at the edge, on a plane square to it."""
    i = sharp.worst()
    point = sharp.points[i]
    lines = _segments_near(mesh, point, sharp.tangent, 1.0)
    if len(lines) < 2:
        return {"read": False, "why": "the section has no outline there"}
    ends = np.round(lines.reshape(-1, 3), 6)
    corner = ends[np.argmin(np.linalg.norm(ends - point, axis=1))]
    if float(np.linalg.norm(corner - point)) > 4 * FINE[0] + 1e-6:
        off = float(np.linalg.norm(corner - point))
        return {"read": False, "why": f"the outline's nearest corner is {off:.4f} mm off"}
    rounded = np.round(lines, 6)
    others = []
    for seg in rounded:
        if np.allclose(seg[0], corner, atol=1e-6) and not np.allclose(seg[1], corner, atol=1e-6):
            others.append(seg[1])
        elif np.allclose(seg[1], corner, atol=1e-6) and not np.allclose(seg[0], corner, atol=1e-6):
            others.append(seg[0])
    if len(others) != 2:
        return {"read": False, "why": f"the outline has {len(others)} segment(s) at the corner"}
    d1, d2 = others[0] - corner, others[1] - corner
    inner = _turn(d1 / np.linalg.norm(d1), d2 / np.linalg.norm(d2))
    turn = 180.0 - inner
    # Each segment is a chord of its face: on a face of radius r it leans off the face's
    # tangent by up to its length over 2r, either way.
    lean = 0.0
    for index, d in zip(sharp.faces, (d1, d2), strict=True):
        face = solid.faces[index]
        if not face.plane:
            lean += math.degrees(
                float(np.linalg.norm(d)) / 2 / max(1e-3, _radius_any(face, corner))
            )
    band = ANGLE_BAND_DEG + lean
    return {
        "read": True,
        "turn_deg": round(turn, 2),
        "band_deg": round(band, 2),
        "brep_deg": round(sharp.turns[i], 2),
    }


def _radius_any(face: Face, point: np.ndarray) -> float:
    """The least radius the face bends through either way at the point, for a chord's lean."""
    u, v, _ = face.uv(point)
    props = BRepLProp_SLProps(face.adaptor, u, v, 2, 1e-7)
    if not props.IsCurvatureDefined():
        return math.inf
    k = max(abs(props.MaxCurvature()), abs(props.MinCurvature()))
    return math.inf if k < 1e-12 else 1.0 / k


def section_radius(mesh: trimesh.Trimesh, rounded: Rounded, solid: Solid) -> dict[str, Any]:
    """A circle through the mesh's outline on the rounded face, cut along where it bends most."""
    across = np.cross(rounded.normal, rounded.bends)
    size = float(np.linalg.norm(across))
    if size < 1e-9:
        return {"read": False, "why": "no direction it bends"}
    window = 0.5 * rounded.radius
    lines = _segments_near(mesh, rounded.at, across / size, 1.2 * rounded.radius + 0.1)
    face = solid.faces[rounded.index]
    ends = np.unique(np.round(lines.reshape(-1, 3), 6), axis=0)
    kept = []
    for p in ends:
        if np.linalg.norm(p - rounded.at) > window:
            continue
        u, v, gap = face.uv(p)
        if gap <= 4 * FINE[0] and face.contains(u, v):
            kept.append(p)
    if len(kept) < 5:
        return {"read": False, "why": f"{len(kept)} point(s) of the outline on the face"}
    pts = np.asarray(kept) - rounded.at
    flat = np.c_[pts @ rounded.bends, pts @ rounded.normal]
    _, diameter = _circle(flat)
    band = RADIUS_BAND + RADIUS_BAND_SHARE * rounded.radius
    return {
        "read": True,
        "radius_mm": round(diameter / 2, 4),
        "band_mm": round(band, 4),
        "points": len(kept),
    }


def _spread(items: list[Any], most: int) -> list[Any]:
    if len(items) <= most:
        return items
    step = len(items) / most
    return [items[int(i * step)] for i in range(most)]


def _with(items: list[Any], more: list[Any]) -> list[Any]:
    """The items, and each of `more` not among them already: each group's least is checked."""
    found = list(items)
    for m in more:
        if all(m is not i for i in found):
            found.append(m)
    return found


def _is_reference(path: str) -> bool:
    return PurePosixPath(path).parts[:1] == ("references",)


def run(args: Any, report: Report) -> None:
    read_paths = [p for p in args.files if not _is_reference(p)]
    references = [p for p in args.files if _is_reference(p)]
    blocking = list(dict.fromkeys(references + list(args.with_files)))
    if not read_paths:
        raise NoAnswer(
            "every file named is a reference or --with, and their edges aren't read: name the "
            "part's own files"
        )
    loaded = {path: load(path) for path in dict.fromkeys(read_paths + blocking)}
    for path in read_paths:
        if not loaded[path].is_brep:
            raise NoAnswer(f"{path} is an STL: a mesh has no edges or faces of its own to read")
    parts = [piece for part in loaded.values() for piece in part.pieces()]
    for part in loaded.values():
        report.notes += part.notes
    reach = Reach(parts)
    report.numbers["reach_mm"] = FINGER_MM
    report.numbers["grid_mm"] = round(reach.step, 4)
    report.numbers["not_read"] = [
        {"file": p, "why": "a reference" if _is_reference(p) else "named with --with"}
        for p in blocking
    ]
    solids: list[Solid] = []
    for path in read_paths:
        pieces = loaded[path].pieces()
        for k, piece in enumerate(pieces):
            name = path if len(pieces) == 1 else f"{path}#{k + 1}"
            solids.append(Solid(name, piece, reach))
    sharp = [s for solid in solids for s in solid.sharp]
    rounded = sorted((r for solid in solids for r in solid.rounded), key=lambda r: r.radius)
    counts = {key: sum(s.counts[key] for s in solids) for key in solids[0].counts}
    faceted = [s for s in sharp if max(s.turns) < FACETED_DEG]
    report.numbers["edges"] = {"sharp_outside": len(sharp), **counts}
    report.numbers["sharp_edges"] = [s.plain() for s in sharp]
    report.numbers["faceted_edges"] = len(faceted)
    report.numbers["rounded"] = [r.plain() for r in rounded]
    least_rounded = rounded[0] if rounded else None
    report.numbers["least_rounded_mm"] = round(least_rounded.radius, 3) if least_rounded else None
    report.numbers["least_rounded_at"] = _at(least_rounded.at) if least_rounded else None
    if sharp:
        worst = max(sharp, key=lambda s: max(s.turns))
        report.numbers["least_radius_mm"] = 0.0
        report.numbers["least_radius_at"] = _at(worst.points[worst.worst()])
    else:
        report.numbers["least_radius_mm"] = report.numbers["least_rounded_mm"]
        report.numbers["least_radius_at"] = report.numbers["least_rounded_at"]

    report.line(
        f"read: {', '.join(read_paths)}"
        + (f"; not read, as a reference or --with: {', '.join(blocking)}" if blocking else "")
    )
    report.line(
        f"outside: what a ball {FINGER_MM:g} mm across (the test finger) reaches from outside "
        f"every file named, on a {reach.step:.2f} mm grid"
    )
    if sharp:
        turned = max(max(s.turns) for s in sharp)
        report.line(f"sharp outside edges: {len(sharp)}, turning up to {turned:.1f}°")
        for s in sorted(sharp, key=lambda s: -max(s.turns))[:NAMED]:
            p = s.plain()
            report.line(
                f"  sharp: {s.file}, {p['turn_deg']:g}° from {_shown(p['from'])} to "
                f"{_shown(p['to'])}, between a {s.kinds[0].lower()} and a {s.kinds[1].lower()}"
            )
        if len(sharp) > NAMED:
            report.line(f"  and {len(sharp) - NAMED} more, in the saved numbers' sharp_edges")
    else:
        report.line("sharp outside edges: none")
    if rounded:
        report.line(
            f"rounded: {len(rounded)} face(s), least radius {rounded[0].radius:.3f} mm at "
            f"{_shown(rounded[0].at)} on {rounded[0].file} (a {rounded[0].kind.lower()})"
        )
    else:
        report.line("rounded: no rounded face in reach")
    report.line(
        f"not read: {counts['inside_corners']} inside corner(s), {counts['out_of_reach']} sharp "
        f"edge(s) no {FINGER_MM:g} mm ball reaches (inside, or deep in an opening), "
        f"{counts['not_met']} a ball touches only flat-on to a face beside it (flush with another "
        "file, the rim of a slot too narrow for it, or low in a tight corner), "
        f"{counts['smooth']} smooth edge(s) where faces meet tangent"
    )
    if faceted:
        report.note(
            f"{len(faceted)} sharp edge(s) turn by under {FACETED_DEG:g}°: a rounding stored as "
            "flat strips reads as sharp edges, each turning a few degrees. Build it with "
            "`fillet`, or an arc, and it reads as one rounded face"
        )

    read: list[Sharp | Rounded] = [*sharp, *rounded]
    groups = {
        name: _least([(e, _point(e)) for e in read if e.group() == name]) for name in GROUPS
    }
    chosen = {
        name: _least([(e, at) for e in read if (at := pick(e)) is not None])
        for name, pick in selectors(args).items()
    }
    must = [g["item"] for g in (*groups.values(), *chosen.values()) if g["item"] is not None]
    differs = _second(report, solids, sharp, rounded, reach, parts, must)
    report.notes += differs
    report.numbers["groups"] = {name: _plain_least(g) for name, g in groups.items()}
    report.line(
        f"by group, in the file's axes: upright runs within {UPRIGHT_DEG:g}° of z; top and bottom "
        f"face more than {LEVEL_DEG:g}° above or below level; side is the rest, a level edge "
        "facing sideways. A need names one's least as groups.NAME.least_radius_mm"
    )
    for name, g in groups.items():
        report.line(f"  {_said(name, g)}")
    if chosen:
        report.numbers["selected"] = {name: _plain_least(g) for name, g in chosen.items()}
        words = selectors(args, said=True)
        for name, g in chosen.items():
            report.line(f"selected, {words[name]}: {_said(name, g)}, as selected.{name}")
    _judge(report, args.min_radius, sharp, rounded, differs)


def _least(found: list[tuple[Sharp | Rounded, np.ndarray]]) -> dict[str, Any]:
    """A group's or a selection's counts and its least radius, with a point of it in the group or
    selection: 0 at a sharp edge, the sharpest first, else its tightest rounding. None if nothing
    in it is read. A rounding's radius is its own least, wherever on it that is."""
    sharp = [(e, at) for e, at in found if isinstance(e, Sharp)]
    rounded = [(e, at) for e, at in found if isinstance(e, Rounded)]
    least: tuple[Any, Any] = (None, None)
    if sharp:
        least = max(sharp, key=lambda f: max(f[0].turns))
    elif rounded:
        least = min(rounded, key=lambda f: f[0].radius)
    return {"sharp": len(sharp), "rounded": len(rounded), "item": least[0], "at": least[1]}


def _point(item: Sharp | Rounded) -> np.ndarray:
    return item.points[item.worst()] if isinstance(item, Sharp) else item.at


def _plain_least(g: dict[str, Any]) -> dict[str, Any]:
    item = g["item"]
    found: dict[str, Any] = {"sharp": g["sharp"], "rounded": g["rounded"]}
    if item is None:
        return {**found, "least_radius_mm": None, "least_at": None, "file": None}
    radius = 0.0 if isinstance(item, Sharp) else round(item.radius, 3)
    return {**found, "least_radius_mm": radius, "least_at": _at(g["at"]), "file": item.file}


def _said(name: str, g: dict[str, Any]) -> str:
    item = g["item"]
    if item is None:
        return f"{name}: none read"
    counts = f"{g['sharp']} sharp, {g['rounded']} rounded"
    if isinstance(item, Sharp):
        least = f"least radius 0, sharp {max(item.turns):.0f}° at {_shown(g['at'])}"
    else:
        least = f"least radius {item.radius:.3f} mm at {_shown(g['at'])}"
    return f"{name}: {counts}, {least} on {item.file}"


def selectors(args: Any, said: bool = False) -> dict[str, Any]:
    """Each `--box` and `--facing` by its name: a test of an edge or a rounding, or with `said`,
    what it is in words. An edge is in a box where any point of it read is; it faces a way where
    its outward direction is within `FACING_DEG` of it. Each test gives a point of it, or None."""
    found: dict[str, Any] = {}
    for name, (low, high) in getattr(args, "boxes", None) or []:
        words = f"in the box from {_shown(low)} to {_shown(high)}"
        found[name] = words if said else _in_box(np.asarray(low), np.asarray(high))
    for name, way in getattr(args, "facings", None) or []:
        words = f"facing within {FACING_DEG:g}° of {way}"
        found[name] = words if said else _facing(np.asarray(DIRECTIONS[way]))
    return found


def _in_box(low: np.ndarray, high: np.ndarray) -> Any:
    """A test giving the point of an edge read in the box nearest its least, or None."""

    def test(item: Sharp | Rounded) -> np.ndarray | None:
        inside = [p for p in item.where() if np.all(p >= low) and np.all(p <= high)]
        if not inside:
            return None
        least = _point(item)
        return min(inside, key=lambda p: float(np.linalg.norm(p - least)))

    return test


def _facing(toward: np.ndarray) -> Any:
    least = math.cos(math.radians(FACING_DEG))

    def test(item: Sharp | Rounded) -> np.ndarray | None:
        return _point(item) if float(_unit(item.outward) @ toward) >= least else None

    return test


def _second(
    report: Report,
    solids: list[Solid],
    sharp: list[Sharp],
    rounded: list[Rounded],
    reach: Reach,
    parts: list[Part],
    must: list[Sharp | Rounded] | None = None,
) -> list[str]:
    """Sections of the mesh at the sharp edges and the least radii, and the ball's centres held
    clear by the mesh's exact distance."""
    differs: list[str] = []
    owner = {s.name: s for s in solids}
    must = must or []
    checked_sharp = _with(_spread(sharp, SECOND_MOST), [m for m in must if isinstance(m, Sharp)])
    unread = 0
    agreed = 0
    for s in checked_sharp:
        solid = owner[s.file]
        found = section_turn(solid.part.fine(), s, solid)
        if not found["read"]:
            unread += 1
            continue
        if abs(found["turn_deg"] - found["brep_deg"]) > found["band_deg"]:
            differs.append(
                f"at {_shown(s.points[s.worst()])} on {s.file} the section turns "
                f"{found['turn_deg']:.1f}°, the exact surface {found['brep_deg']:.1f}° "
                f"(± {found['band_deg']:.1f})"
            )
        else:
            agreed += 1
    if checked_sharp:
        report.second(
            f"sections square to {len(checked_sharp)} of {len(sharp)} sharp edge(s): {agreed} "
            f"turn as the exact surface does, within {ANGLE_BAND_DEG:g}° and the chords"
            + (f"; {unread} couldn't be read there" if unread else "")
        )
    checked_round = _with(
        _spread(rounded, SECOND_MOST), [m for m in must if isinstance(m, Rounded)]
    )
    unread = agreed = 0
    for r in checked_round:
        solid = owner[r.file]
        found = section_radius(solid.part.fine(), r, solid)
        r.second = found
        if not found["read"]:
            unread += 1
            continue
        if abs(found["radius_mm"] - r.radius) > found["band_mm"]:
            differs.append(
                f"at {_shown(r.at)} on {r.file} a circle through the section is "
                f"{found['radius_mm']:.3f} mm, the exact surface's radius {r.radius:.3f} "
                f"(± {found['band_mm']:.3f})"
            )
        else:
            agreed += 1
    if checked_round:
        report.second(
            f"a circle through the section at {len(checked_round)} of {len(rounded)} rounded "
            f"face(s)' least radius: {agreed} agree within {RADIUS_BAND:g} mm and "
            f"{RADIUS_BAND_SHARE:.0%} of it" + (f"; {unread} couldn't be read" if unread else "")
        )
    if rounded and checked_round and not any(r.second.get("read") for r in checked_round):
        differs.append("no rounded face's section could be read, so no radius is checked")
    touched = [s.points[s.worst()] for s in sharp] + [r.at for r in rounded]
    if touched:
        whole = reach.whole
        centres = np.array([reach.centre(p) for p in _spread(touched, 4 * SECOND_MOST)])
        points = np.array(_spread(touched, 4 * SECOND_MOST))
        _, clear, _ = trimesh.proximity.closest_point(whole, centres)  # type: ignore[no-untyped-call]
        apart = np.linalg.norm(centres - points, axis=1)
        band = 2.5 * reach.step
        bad = (clear < reach.radius - band) | (apart > reach.radius + band)
        report.second(
            f"the ball's centre the grid found for {len(centres)} touch point(s): by the mesh's "
            f"exact distance it clears every file by {float(np.min(clear)):.2f} mm at least, and "
            f"is {float(np.max(apart)):.2f} mm at most from the point, against "
            f"{reach.radius:g} ± {band:.2f}"
        )
        if bad.any():
            i = int(np.argmax(bad))
            differs.append(
                f"the grid puts a ball touching {_shown(points[i])}, and by the mesh's distance "
                f"its centre is {clear[i]:.2f} mm from the files and {apart[i]:.2f} mm from the "
                "point"
            )
    elif not sharp and not rounded:
        report.second("nothing in reach to check: no sharp edge and no rounded face read")
    return differs


def _judge(
    report: Report,
    least: float | None,
    sharp: list[Sharp],
    rounded: list[Rounded],
    differs: list[str],
) -> None:
    if differs:
        report.verdict("DISAGREE", f"{len(differs)} reading(s) differ between the two methods")
        return
    said = f"{len(sharp)} sharp outside edge(s), {len(rounded)} rounded face(s)" + (
        f", least radius {rounded[0].radius:.3f} mm" if rounded else ""
    )
    if least is None:
        report.verdict("OK", f"a reading: {said}. No --min-radius, so nothing is judged")
        return
    short = max(RADIUS_FLOOR, RADIUS_SHARE * least)
    report.numbers["min_radius_mm"] = least
    tight = [r for r in rounded if r.radius < least - short]
    if not sharp and not tight:
        report.verdict("OK", f"every outside edge read is rounded to at least {least:g} mm: {said}")
        return
    places = [
        f"sharp {max(s.turns):.0f}° at {_shown(s.points[s.worst()])} on {s.file}" for s in sharp
    ] + [f"{r.radius:.3f} mm at {_shown(r.at)} on {r.file}" for r in tight]
    more = f", and {len(places) - 3} more" if len(places) > 3 else ""
    report.verdict(
        "FAIL",
        f"{len(sharp)} sharp outside edge(s) and {len(tight)} rounded face(s) under {least:g} mm: "
        + "; ".join(places[:3])
        + more,
    )
