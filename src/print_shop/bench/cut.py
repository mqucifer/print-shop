"""A cut-through as a view: every file cut by one plane, each drawn in its own colour.

`render --section` draws one file's material in grey. On `cup-1` that hid what mattered: a pane
and its pocket can only be judged together, and two panes sharing one pocket looked like a split.
Here a cut is a plane, level at a height (`z=3`) or upright through a pane (`x=50`), and each file's
section is drawn in its colour, the largest first so what sits inside an opening is drawn over it.
A window along the plane (`x=50,y=20:40`) draws only that stretch, so a 6 mm tall frame is more
than a line.

Checked by a different method: each file's area on the plane by the mesh against OCCT's section of
its exact surface, where it's a STEP, and what's painted against the polygons. Where two files'
sections overlap, the cut says by how much, as nothing else on a sheet can show it.

**Any plane** (mqucifer/print-shop#352). A joint on a sloping seam, a rib down a dome's seam or a
dovetail on a leg turned to a corner, is seen in a cut square to that seam, and a cut along an axis
slices it slantwise. `at=X,Y,Z,normal=NX,NY,NZ` is the plane through that point square to that
direction, seen from the normal's side. Its own axes are `u`, the picture's right, and `v`, its up,
in mm from the point: `v` is `up=UX,UY,UZ` laid on the plane where that's given, else +z laid on
it, or +y where the plane is within 30° of level, and `u` is `v` across the normal. `u=FROM:TO`
and `v=FROM:TO` draw only that stretch, as the axis form's window does. Its picture has both axes
on its sides in mm, and the reply says the plane, its point, normal and axes.

**Gaps read on the cut** (mqucifer/print-shop#352). With `gaps`, each pair of files' least gap on
the drawn stretch of the plane is marked where it lies, with its figure, read from the section's
outlines: the figure itself is OCCT's, between its sections of both exact surfaces, where every
piece of the pair is a STEP, as `clearance`'s is, and the outlines' otherwise, each the other's
second check. A hand-drawn figure on a picture is a claim; this one is a reading.
"""


from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
import shapely.ops
import trimesh
from PIL import Image, ImageDraw

from .shapes import FINE, NoAnswer, Part, to_manifold

AXES = ("x", "y", "z")
#: For an upright cut, which axes the picture's right and up are, and a turn of the part that
#: makes the plane level, so Manifold's slice can cut it. Each turn keeps a polygon's handedness.
TURNS = {
    # A plane x = c, seen from +x: right is +y, up is +z.
    "x": (("y", "z"), np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=float), 1.0),
    # A plane y = c, seen from -y, as the front view: right is +x, up is +z.
    "y": (("x", "z"), np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float), -1.0),
    "z": (("x", "y"), np.eye(3), 1.0),
}
#: The picture's margin, over the drawing's longer side.
FILL = 1.1
#: A plane's picture leaves more, for its axes in mm on its sides.
FILL_RULED = 1.3
#: The mesh's area and OCCT's may differ by this share, or this many mm², whichever is more.
AREA_SHARE = 0.03
AREA_FLOOR = 0.5
#: What's painted and the polygons may differ by this share of the drawing.
PIXEL_SHARE = 0.03
#: Two files' sections overlapping by less than this, in mm², are touching, not overlapping.
OVERLAP_FLOOR = 0.05
#: A face this near the plane, in mm, lies on it; and how far off it a cut is suggested.
ON_FACE = 1e-4
OFF_FACE = 0.05
NUMBER = re.compile(r"-?\d+(?:\.\d*)?|-?\.\d+")
#: A plane's own axes, as its window names them: right, then up.
PLANE_AXES = ("u", "v")
#: With no `up`, +z is laid on the plane, unless the plane is within this of level, in degrees,
#: where +y is: +z laid on a plane near level turns wildly with a small tilt.
LEVEL_DEG = 30.0
#: Closer than this, in mm, two files' sections touch: `clearance`'s figure.
TOUCHING = 1e-4
#: A plane's figures, which may come from arithmetic and so carry an exponent: 1e-17, 2.5E3.
PLANE_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
#: How far the outlines' gap may be from OCCT's: each outline is the fine mesh's, whose chords
#: lie within its tolerance of the surface, on both files: `clearance`'s band on a cut square to
#: both surfaces. A surface the plane meets slantwise moves on it by its chord over the sine of
#: the angle between their normals, so each file's share is its own surface's, there.
GAP_BAND = 2 * FINE[0] + TOUCHING
#: The least that sine is taken as: a surface nearly in the plane has no edge on it to rely on.
SLANT_FLOOR = 0.05
#: What a gap read on a plane is, said wherever one's marked.
IN_PLANE = (
    "each gap is read in the plane: never less than the parts' least gap in 3D, which clearance "
    "reads, and more where the plane meets them slantwise, so a joint can look looser here than "
    "it is"
)
#: A gap's mark on the picture: its line and the box behind its figure.
GAP_INK = (20, 20, 20)


def _three(text: str) -> tuple[float, float, float] | None:
    words = text.split(",")
    if len(words) != 3 or not all(PLANE_NUMBER.fullmatch(w.strip()) for w in words):
        return None
    return (float(words[0]), float(words[1]), float(words[2]))


def _said(values: Any) -> str:
    # Plus 0.0, so a figure rounded to nothing is 0, not -0.
    return ",".join(f"{float(v) + 0.0:g}" for v in values)


@dataclass
class Cut:
    """A plane, and the stretch of it to draw: `window` is (low, high) along right and up."""

    axis: str
    at: float
    window: dict[str, tuple[float, float]]

    @property
    def label(self) -> str:
        span = "".join(f", {a} {lo:g} to {hi:g}" for a, (lo, hi) in self.window.items())
        return f"cut {self.axis}={self.at:g}{span}"

    @property
    def name(self) -> str:
        return self.label.replace(", ", " ").replace("cut ", "cut-")

    def given(self, at: float | None = None) -> str:
        """As `--cut` takes it, at `at` along its axis if that's given."""
        span = "".join(f",{a}={lo:g}:{hi:g}" for a, (lo, hi) in self.window.items())
        return f"{self.axis}={self.at if at is None else at:g}{span}"

    def shifted(self, by: float) -> str:
        """As `--cut` takes it, `by` mm along its axis."""
        return self.given(self.at + by)

    @property
    def sides(self) -> tuple[str, str]:
        """The picture's right and up, by name."""
        return TURNS[self.axis][0]

    @property
    def ruled(self) -> bool:
        """Whether its picture has its axes in mm on its sides: the axis form's never has."""
        return False

    def frame(self) -> tuple[np.ndarray, float]:
        """The move that lays the plane level, and the height it's then at."""
        _, turn, sign = TURNS[self.axis]
        matrix = np.eye(4)
        matrix[:3, :3] = turn
        return matrix, sign * self.at

    @property
    def normal(self) -> np.ndarray:
        found = np.zeros(3)
        found[AXES.index(self.axis)] = 1.0
        return found

    @property
    def point(self) -> np.ndarray:
        return self.normal * self.at

    def numbers(self) -> dict[str, Any]:
        return {"plane": f"{self.axis}={self.at:g}", "right": self.sides[0], "up": self.sides[1]}


@dataclass
class Plane:
    """Any plane: through `point`, square to `normal`, seen from its side, `right` and `up` its
    own axes in it (mqucifer/print-shop#352). In-plane figures are in mm from the point."""

    point: np.ndarray
    normal: np.ndarray
    right: np.ndarray
    up: np.ndarray
    window: dict[str, tuple[float, float]]
    #: The normal and up as they were given, to say the cut as it was asked for.
    asked: tuple[tuple[float, float, float], tuple[float, float, float] | None]
    up_from: str = ""

    @property
    def label(self) -> str:
        span = "".join(f", {a} {lo:g} to {hi:g}" for a, (lo, hi) in self.window.items())
        up = f" up {_said(self.asked[1])}" if self.asked[1] is not None else ""
        return f"cut at {_said(self.point)} normal {_said(self.asked[0])}{up}{span}"

    @property
    def name(self) -> str:
        return self.label.replace(", ", " ").replace("cut ", "cut-")

    def given(self, point: np.ndarray | None = None) -> str:
        """As `--cut` takes it, through `point` if that's given."""
        at = self.point if point is None else point
        up = f",up={_said(self.asked[1])}" if self.asked[1] is not None else ""
        span = "".join(f",{a}={lo:g}:{hi:g}" for a, (lo, hi) in self.window.items())
        return f"at={_said(at)},normal={_said(self.asked[0])}{up}{span}"

    def shifted(self, by: float) -> str:
        """As `--cut` takes it, `by` mm along its normal: its axes are the same, so a window is
        the same stretch of the plane beside it."""
        return self.given(self.point + by * self.normal)

    @property
    def sides(self) -> tuple[str, str]:
        return PLANE_AXES

    @property
    def ruled(self) -> bool:
        return True

    def frame(self) -> tuple[np.ndarray, float]:
        """Rows u, v and the normal, a turn with u x v = n, so a polygon keeps its handedness,
        and the point moved to the origin."""
        turn = np.vstack([self.right, self.up, self.normal])
        matrix = np.eye(4)
        matrix[:3, :3] = turn
        matrix[:3, 3] = -turn @ self.point
        return matrix, 0.0

    @property
    def said(self) -> str:
        return (
            f"{self.label}: the plane through {_said(np.round(self.point, 4))}, normal "
            f"{_said(np.round(self.normal, 4))}, seen from that side; its axes, in mm from that "
            f"point: u, the picture's right, {_said(np.round(self.right, 4))}, and v, its up, "
            f"{_said(np.round(self.up, 4))} ({self.up_from})"
        )

    def numbers(self) -> dict[str, Any]:
        return {
            "plane": {
                "point": [round(float(v), 6) for v in self.point],
                "normal": [round(float(v), 6) for v in self.normal],
                "u": [round(float(v), 6) for v in self.right],
                "v": [round(float(v), 6) for v in self.up],
            },
            "right": "u",
            "up": "v",
        }


AnyCut = Cut | Plane


def world(cut: AnyCut, uv: Any) -> np.ndarray:
    """Points on the picture's plane, by its right and up, where they are in the parts' frame."""
    matrix, level = cut.frame()
    flat = np.asarray(uv, dtype=float).reshape(-1, 2)
    turned = np.column_stack([flat, np.full(len(flat), level)])
    return np.asarray((turned - matrix[:3, 3]) @ matrix[:3, :3])


def offsets(cut: AnyCut, points: Any) -> np.ndarray:
    """How far each point is off the plane, along its normal."""
    return np.asarray((np.asarray(points, dtype=float) - cut.point) @ cut.normal)


def parse_plane(words: list[str], text: str) -> Plane:
    """`at=X,Y,Z,normal=NX,NY,NZ`, then `up=UX,UY,UZ`, `u=FROM:TO` and `v=FROM:TO`, any of them."""
    given: dict[str, str] = {}
    key = ""
    for word in words:
        if "=" in word:
            key, _, value = word.partition("=")
            if key in given:
                raise NoAnswer(f"a plane's cut names {key} once, not twice: {text!r}")
            given[key] = value
        elif key:
            given[key] += "," + word
    known = {"at", "normal", "up", *PLANE_AXES}
    unknown = [k for k in given if k not in known]
    if unknown or "normal" not in given:
        raise NoAnswer(
            "a plane's cut is at=X,Y,Z,normal=NX,NY,NZ, then up=UX,UY,UZ, u=FROM:TO and "
            f"v=FROM:TO if wanted, not {text!r}"
        )
    found: dict[str, tuple[float, float, float]] = {}
    for key in ("at", "normal", "up"):
        if key in given:
            three = _three(given[key])
            if three is None:
                raise NoAnswer(f"a plane's {key} is three numbers, X,Y,Z: not {given[key]!r}")
            found[key] = three
    normal = np.asarray(found["normal"], dtype=float)
    if float(np.linalg.norm(normal)) < 1e-9:
        raise NoAnswer("a plane's normal is a direction: not 0,0,0")
    normal = normal / np.linalg.norm(normal)
    if "up" in found:
        wanted = np.asarray(found["up"], dtype=float)
        up_from = "up as given, laid on the plane"
    elif abs(float(normal[2])) < math.cos(math.radians(LEVEL_DEG)):
        wanted = np.array([0.0, 0.0, 1.0])
        up_from = "+z laid on it, as no up was given"
    else:
        wanted = np.array([0.0, 1.0, 0.0])
        up_from = f"+y laid on it, as no up was given and it's within {LEVEL_DEG:g}° of level"
    laid = wanted - float(wanted @ normal) * normal
    if float(np.linalg.norm(laid)) < 1e-6 * max(float(np.linalg.norm(wanted)), 1.0):
        raise NoAnswer(
            f"a plane's up lies in it, not along its normal: up={_said(wanted)} is square to it"
        )
    up = laid / np.linalg.norm(laid)
    right = np.cross(up, normal)
    window: dict[str, tuple[float, float]] = {}
    for along in PLANE_AXES:
        if along not in given:
            continue
        low, _, high = given[along].partition(":")
        if not PLANE_NUMBER.fullmatch(low) or not PLANE_NUMBER.fullmatch(high):
            raise NoAnswer(f"a plane's window is {along}=FROM:TO, not {along}={given[along]!r}")
        if float(high) <= float(low):
            raise NoAnswer(f"a window runs from low to high: not {along}={given[along]}")
        window[along] = (float(low), float(high))
    return Plane(
        np.asarray(found["at"], dtype=float),
        normal,
        right,
        up,
        window,
        (found["normal"], found.get("up")),
        up_from,
    )


def parse(text: str) -> AnyCut:
    """`z=3`, `x=50`, `x=50,y=20:40` or `z=3,x=0:40,y=10:30`; or any plane, from `at=`."""
    words = [w.strip() for w in text.split(",") if w.strip()]
    if not words:
        raise NoAnswer("a cut is AXIS=VALUE, such as z=3 or x=50,y=20:40")
    if words[0].startswith("at="):
        return parse_plane(words, text)
    axis, _, value = words[0].partition("=")
    if axis not in AXES or not NUMBER.fullmatch(value):
        raise NoAnswer(
            f"a cut starts with x, y or z and where: z=3 or x=50, or is any plane, "
            f"at=X,Y,Z,normal=NX,NY,NZ: not {words[0]!r}"
        )
    in_plane = TURNS[axis][0]
    window: dict[str, tuple[float, float]] = {}
    for word in words[1:]:
        along, _, span = word.partition("=")
        low, _, high = span.partition(":")
        if along not in in_plane or not NUMBER.fullmatch(low) or not NUMBER.fullmatch(high):
            raise NoAnswer(
                f"after {words[0]}, a window is {in_plane[0]}=FROM:TO or {in_plane[1]}=FROM:TO, "
                f"not {word!r}"
            )
        if float(high) <= float(low):
            raise NoAnswer(f"a window runs from low to high: not {word!r}")
        window[along] = (float(low), float(high))
    return Cut(axis, float(value), window)


def section(mesh: trimesh.Trimesh, cut: AnyCut, name: str) -> Any:
    """A mesh's material on the plane, as Shapely, in the picture's right and up."""
    matrix, level = cut.frame()
    turned = mesh.copy()
    turned.apply_transform(matrix)
    found = to_manifold(turned, name).slice(level).to_polygons()
    # Material is what's inside an odd number of outlines, so an island in an opening is kept: the
    # outlines of a closed mesh's section don't cross, and taking each in turn flips what's inside.
    material: Any = shapely.Polygon()
    for contour in found:
        points = np.asarray(contour, dtype=float)
        if len(points) >= 3:
            material = material.symmetric_difference(shapely.make_valid(shapely.Polygon(points)))
    return shapely.make_valid(material)


def _occt_plane(shape: Any, cut: AnyCut) -> Any:
    """A square face on the plane, larger than the shape, centred where its box's centre lies on
    the plane."""
    import cadquery as cq

    box = shape.BoundingBox()
    size = 2 * max(box.xlen, box.ylen, box.zlen) + 10
    centre = np.array([(box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2,
                       (box.zmin + box.zmax) / 2])  # fmt: skip
    if isinstance(cut, Cut):
        centre[AXES.index(cut.axis)] = cut.at
    else:
        centre = centre - float(offsets(cut, centre)) * cut.normal
        size += 2 * float(np.linalg.norm(centre - cut.point))
    return cq.Face.makePlane(size, size, cq.Vector(*centre), cq.Vector(*cut.normal))


def occt_area(shape: Any, cut: AnyCut) -> float | None:
    """The exact surface's area on the plane, by OCCT, or None where it gave no faces."""
    faces = shape.intersect(_occt_plane(shape, cut)).Faces()
    return sum(f.Area() for f in faces) if faces else None


def _occt_drawn(parts: list[Part], cut: AnyCut, low: np.ndarray, high: np.ndarray) -> Any:
    """OCCT's section of a file's exact surfaces on the drawn stretch of the plane, as one
    compound, or None where it has none there."""
    import cadquery as cq

    corners = world(cut, [(low[0], low[1]), (high[0], low[1]), (high[0], high[1]),
                          (low[0], high[1])])  # fmt: skip
    window = cq.Face.makeFromWires(
        cq.Wire.makePolygon([cq.Vector(*c) for c in corners], close=True)
    )
    faces = [f for p in parts for f in p.shape.intersect(window).Faces()]
    return cq.Compound.makeCompound(faces) if faces else None


def _occt_gap(a: Any, b: Any) -> float | None:
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    distance = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    return float(distance.Value()) if distance.IsDone() else None


@dataclass
class Drawn:
    """A cut's picture, and what it found."""

    pixels: np.ndarray
    numbers: dict[str, Any]
    problems: list[str]
    seconds: list[str]
    notes: list[str]
    #: Where each file's number is drawn, in pixels, for the legend's markers.
    marks: list[tuple[int, float, float]]


def _polygonal(found: Any) -> Any:
    """Only the area of a clipped section. Material that only meets the window's edge, a face on
    it, clips to a line or a point too, and Shapely gives a collection, whose boundary is None."""
    if found.geom_type in ("Polygon", "MultiPolygon"):
        return found
    parts = [
        g
        for g in getattr(found, "geoms", [])
        if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty
    ]
    return shapely.union_all(parts) if parts else shapely.Polygon()


def _clip(material: Any, cut: AnyCut, low: np.ndarray, high: np.ndarray) -> Any:
    return _polygonal(material.intersection(shapely.box(low[0], low[1], high[0], high[1])))


def slant(parts: list[Part], point: np.ndarray, cut: AnyCut) -> float:
    """How far a file's outline on the plane moves for its surface moving 1 mm, at a point on
    it: one over the sine of the angle between the plane's normal and the surface's there, the
    nearest triangle's of its fine mesh."""
    nearest, normal = math.inf, None
    for part in parts:
        mesh = part.fine()
        _, distance, triangle = trimesh.proximity.closest_point(mesh, [point])  # type: ignore[no-untyped-call]
        if float(distance[0]) < nearest:
            nearest, normal = float(distance[0]), np.asarray(mesh.face_normals[int(triangle[0])])
    if normal is None:
        return 1.0
    across = normal - float(normal @ cut.normal) * cut.normal
    return 1.0 / max(float(np.linalg.norm(across)), SLANT_FLOOR)


def on_face(mesh: trimesh.Trimesh, cut: AnyCut) -> bool:
    """Whether a face of the mesh lies on the plane: a triangle whose every corner is on it."""
    if isinstance(cut, Cut):
        along = np.asarray(mesh.triangles)[:, :, AXES.index(cut.axis)] - cut.at
    else:
        along = offsets(cut, np.asarray(mesh.triangles).reshape(-1, 3)).reshape(-1, 3)
    return bool(np.any(np.all(np.abs(along) <= ON_FACE, axis=1)))


def on_a_face(cut: AnyCut, lying: list[str], spans: list[tuple[float, float]]) -> str:
    """A cut exactly on a file's face, said with one to copy each side of it that passes through
    a file, by `spans`, each file's extent off the plane along its normal. On the runs a cut on a
    pocket's floor read as nothing by the mesh and as the whole face by OCCT, a FAIL of the
    picture and not of the part, and one on a top face as passing through no file
    (mqucifer/print-shop#191)."""
    sides = [-OFF_FACE, OFF_FACE]
    inside = [by for by in sides if any(low < by < high for low, high in spans)] or sides
    either = " or ".join(f"--cut {cut.shifted(by)}" for by in inside)
    return (
        f"{cut.label} lies exactly on a face of {', '.join(lying)}: a section on a face is "
        f"degenerate, the whole face by one method and nothing by the other. Cut a little off "
        f"it, such as {either}"
    )


def _spans(files: list[tuple[str, list[Part], tuple[int, int, int]]], cut: AnyCut) -> list[
    tuple[float, float]
]:
    found = []
    for _, parts, _ in files:
        for p in parts:
            if isinstance(cut, Cut):
                k = AXES.index(cut.axis)
                bounds = p.fine().bounds
                found.append((float(bounds[0][k]) - cut.at, float(bounds[1][k]) - cut.at))
            else:
                off = offsets(cut, p.fine().vertices)
                found.append((float(off.min()), float(off.max())))
    return found


def _step(span: float) -> float:
    """A ruler's step: 1, 2 or 5 times a power of ten, so the span takes at most 8 of them."""
    rough = span / 8
    power = 10.0 ** math.floor(math.log10(rough))
    return float(next(k * power for k in (1, 2, 5, 10) if k * power >= rough))


def _rule(
    paint: ImageDraw.ImageDraw,
    pixel: Any,
    low: np.ndarray,
    high: np.ndarray,
) -> None:
    """The plane's axes on the picture's bottom and left, in mm from the cut's point, and the
    point itself where it's drawn."""
    ink = (60, 60, 60)
    (left, bottom), (right, top) = pixel(np.array([low, high]))
    below, aside = bottom + 6, left - 6
    paint.line([(left, below), (right, below)], fill=ink)
    paint.line([(aside, bottom), (aside, top)], fill=ink)
    for k, axis in enumerate(PLANE_AXES):
        step = _step(float(high[k] - low[k]))
        first = math.ceil(low[k] / step - 1e-9)
        value = first * step
        while value <= high[k] + 1e-9 * step:
            where = np.array(low, dtype=float)
            where[k] = value
            x, y = pixel(where[None, :])[0]
            text = f"{value:g}" if abs(value) > 1e-9 * step else "0"
            if k == 0:
                paint.line([(x, below), (x, below + 4)], fill=ink)
                paint.text((x, below + 6), text, fill=ink, anchor="mt")
            else:
                paint.line([(aside - 4, y), (aside, y)], fill=ink)
                paint.text((aside - 6, y), text, fill=ink, anchor="rm")
            value += step
        if k == 0:
            paint.text((right, below + 18), f"{axis} mm", fill=ink, anchor="rt")
        else:
            paint.text((aside - 6, top - 14), f"{axis} mm", fill=ink, anchor="rb")
    if low[0] <= 0 <= high[0] and low[1] <= 0 <= high[1]:
        x, y = pixel(np.zeros((1, 2)))[0]
        paint.line([(x - 5, y), (x + 5, y)], fill=ink)
        paint.line([(x, y - 5), (x, y + 5)], fill=ink)


def _gaps(
    cut: AnyCut,
    sections: list[tuple[int, str, Any, list[Part]]],
    clipped: dict[int, Any],
    low: np.ndarray,
    high: np.ndarray,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """Each pair of files' least gap on the drawn stretch: where it lies, by the outlines, and its
    figure, OCCT's on a pair of STEP files and the outlines' otherwise, each the other's check."""
    found: list[dict[str, Any]] = []
    lines: list[str] = []
    seconds: list[str] = []
    problems: list[str] = []
    exact: dict[int, Any] = {}
    for k, _, _, parts in sections:
        if parts and all(p.is_brep for p in parts) and not clipped[k].is_empty:
            exact[k] = _occt_drawn(parts, cut, low, high)
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            (ka, a, _, _), (kb, b, _, _) = sections[i], sections[j]
            one, other = clipped[ka], clipped[kb]
            if one.is_empty or other.is_empty:
                continue
            outline = float(one.distance(other))
            uv = _where(one, other, outline)
            at = world(cut, uv)
            pair: dict[str, Any] = {
                "files": [a, b],
                "gap_outline_mm": round(outline, 4),
                "at_uv": [[round(v, 4) for v in p] for p in uv],
                "at": [[round(float(v), 4) for v in p] for p in at],
            }
            occt = None
            if exact.get(ka) is not None and exact.get(kb) is not None:
                occt = _occt_gap(exact[ka], exact[kb])
                pair["gap_occt_mm"] = None if occt is None else round(occt, 4)
            figure = occt if occt is not None else outline
            pair["gap_mm"] = figure
            pair["gap_by"] = "occt" if occt is not None else "outline"
            band = GAP_BAND
            if occt is not None:
                ends = [slant(sections[n][3], at[e], cut) for e, n in ((0, i), (1, j))]
                band = FINE[0] * sum(ends) + TOUCHING
                pair["gap_band_mm"] = round(band, 4)
            found.append(pair)
            if occt is not None:
                seconds.append(
                    f"{cut.label} gap {a} | {b}: {occt:.4f} mm by OCCT's sections, {outline:.4f} "
                    f"by the mesh's outlines, which the chords allow to differ by {band:.4f} here"
                )
                if abs(occt - outline) > band:
                    problems.append(
                        f"{cut.label}: the gap {a} | {b} is {occt:.4f} mm by OCCT's sections and "
                        f"{outline:.4f} by the mesh's outlines"
                    )
            elif any(not p.is_brep for p in [*sections[i][3], *sections[j][3]]):
                seconds.append(
                    f"{cut.label} gap {a} | {b}: {outline:.4f} mm by the outlines alone: an STL "
                    "has no exact surface to section"
                )
            if figure <= TOUCHING:
                lines.append(f"{cut.label}: {a} and {b} touch or overlap on the cut, a gap of 0")
                continue
            by = "OCCT" if occt is not None else "the outlines"
            lines.append(
                f"{cut.label}: least gap {a} | {b} {figure:.3f} mm by {by}, from "
                f"{','.join(cut.sides)} {_said(np.round(uv[0], 3))} to "
                f"{_said(np.round(uv[1], 3))}, at {_said(np.round(at[0], 3))} to "
                f"{_said(np.round(at[1], 3))}"
            )
    return found, lines, seconds, problems


def _where(one: Any, other: Any, least: float) -> list[list[float]]:
    """Where two sections' least gap lies: from the middle of the longest stretch of the first's
    outline within it, and the point on the second nearest that. Two parallel faces are that far
    apart all along, and Shapely's nearest points are then any pair, often a corner on another
    outline or on the window's edge."""
    reach = least + max(1e-3, 1e-3 * least)
    stretch = one.boundary.intersection(other.buffer(reach, quad_segs=32))
    if stretch.geom_type == "MultiLineString":
        # A ring's pieces either side of where it starts are one stretch.
        stretch = shapely.line_merge(stretch)
    lines = [g for g in getattr(stretch, "geoms", [stretch]) if g.geom_type == "LineString"]
    lines = [g for g in lines if not g.is_empty and g.length > 0]
    if least > TOUCHING and lines:
        start = max(lines, key=lambda g: g.length).interpolate(0.5, normalized=True)
        end = shapely.ops.nearest_points(start, other)[1]
        if float(start.distance(end)) <= reach:
            return [[float(start.x), float(start.y)], [float(end.x), float(end.y)]]
    return [[float(p.x), float(p.y)] for p in shapely.ops.nearest_points(one, other)]


def _mark_gaps(paint: ImageDraw.ImageDraw, pixel: Any, gaps: list[dict[str, Any]]) -> None:
    """Each gap's line where it lies, its ends dotted, and its figure beside it on white: the
    lines first, so no line crosses a figure, and each figure to one side of its line or further
    out, where it covers no other."""
    shown = [g for g in gaps if g["gap_mm"] > TOUCHING]
    ends = [pixel(np.asarray(g["at_uv"])) for g in shown]
    for (x0, y0), (x1, y1) in ends:
        paint.line([(x0, y0), (x1, y1)], fill=GAP_INK, width=2)
        for x, y in ((x0, y0), (x1, y1)):
            paint.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=GAP_INK)
    taken: list[tuple[float, float, float, float]] = []
    for gap, ((x0, y0), (x1, y1)) in zip(shown, ends, strict=True):
        across = np.array([y0 - y1, x1 - x0])
        length = float(np.linalg.norm(across))
        across = across / length if length > 1e-9 else np.array([0.0, -1.0])
        text = f"{gap['gap_mm']:.3f}"
        for away in (14, -14, 28, -28, 42, -42, 56, -56):
            x, y = (x0 + x1) / 2 + away * across[0], (y0 + y1) / 2 + away * across[1]
            left, top, right, bottom = paint.textbbox((x, y), text, anchor="mm")
            box = (left - 2, top - 1, right + 2, bottom + 1)
            if not any(
                box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in taken
            ):
                break
        taken.append(box)
        paint.rectangle(box, fill="white", outline=GAP_INK)
        paint.text((x, y), text, fill=GAP_INK, anchor="mm")


def draw(
    files: list[tuple[str, list[Part], tuple[int, int, int]]],
    cut: AnyCut,
    size: int,
    gaps: bool = False,
) -> Drawn:
    """Each file's section on the plane, in its colour, with the checks behind the picture. A
    plane on a file's face has no section to rely on, so it's said, with a plane to cut instead.
    With `gaps`, each pair's least gap on the drawn stretch is marked and said."""
    right, up = cut.sides
    lying = [name for name, parts, _ in files if any(on_face(p.fine(), cut) for p in parts)]
    if lying:
        raise NoAnswer(on_a_face(cut, lying, _spans(files, cut)))
    sections: list[tuple[int, str, Any, list[Part]]] = []
    for k, (name, parts, _) in enumerate(files):
        pieces = [section(p.fine(), cut, p.name) for p in parts]
        material = shapely.union_all(pieces) if pieces else shapely.Polygon()
        sections.append((k, name, material, parts))
    everything = shapely.union_all([s[2] for s in sections])
    if everything.is_empty:
        raise NoAnswer(f"{cut.label} passes through none of the files")
    low = np.asarray(everything.bounds[:2], dtype=float)
    high = np.asarray(everything.bounds[2:], dtype=float)
    for k, axis in enumerate((right, up)):
        if axis in cut.window:
            low[k], high[k] = cut.window[axis]
    shown = _clip(everything, cut, low, high)
    if shown.is_empty:
        raise NoAnswer(f"{cut.label}: nothing lies in that window")
    span = high - low
    scale = size / ((FILL_RULED if cut.ruled else FILL) * float(max(span)))
    margin = (size - span * scale) / 2

    def pixel(coords: Any) -> list[tuple[float, float]]:
        xy = (np.asarray(coords)[:, :2] - low) * scale + margin
        return [(float(x), float(size - y)) for x, y in xy]

    picture = Image.new("RGB", (size, size), "white")
    paint = ImageDraw.Draw(picture)
    ids = Image.new("L", (size, size), 0)
    numbers: dict[str, Any] = cut.numbers()
    per_file: dict[str, Any] = {}
    problems: list[str] = []
    seconds: list[str] = []
    notes: list[str] = [cut.said] if isinstance(cut, Plane) else []
    marks: list[tuple[int, float, float]] = []
    clipped: dict[int, Any] = {}
    # The largest first, so a pane in an opening, or an island in a pane, is drawn over it.
    for k, name, material, parts in sorted(sections, key=lambda s: -s[2].area):
        colour = files[k][2]
        here = _clip(material, cut, low, high)
        clipped[k] = here
        polygons = [
            g for g in getattr(here, "geoms", [here]) if g.geom_type == "Polygon" and not g.is_empty
        ]
        for poly in sorted(polygons, key=lambda p: -p.area):
            mask = Image.new("L", (size, size), 0)
            carve = ImageDraw.Draw(mask)
            carve.polygon(pixel(poly.exterior.coords), fill=1)
            for hole in poly.interiors:
                carve.polygon(pixel(hole.coords), fill=0)
            picture.paste(colour, mask=mask.point(lambda v: 255 if v else 0))
            ids.paste(k + 1, mask=mask.point(lambda v: 255 if v else 0))
            for ring in [poly.exterior, *poly.interiors]:
                paint.line(pixel(ring.coords), fill=(20, 20, 20), width=1)
        if polygons:
            biggest = max(polygons, key=lambda p: p.area)
            spot = biggest.representative_point()
            x, y = pixel(np.array([[spot.x, spot.y]]))[0]
            marks.append((k, x, y))
        area = float(material.area)
        facts: dict[str, Any] = {"area_mm2": round(area, 3), "pieces": len(polygons)}
        if cut.ruled and not here.is_empty:
            # Where it's drawn on the plane, in its own axes: the axis form's numbers are as they
            # were.
            box = np.round(np.asarray(here.bounds, dtype=float), 4)
            facts["box_mm"] = [[float(box[0]), float(box[1])], [float(box[2]), float(box[3])]]
        exact = [occt_area(p.shape, cut) for p in parts if p.is_brep]
        if exact and all(e is not None for e in exact) and len(exact) == len(parts):
            other = float(sum(e for e in exact if e is not None))
            facts["area_occt_mm2"] = round(other, 3)
            seconds.append(f"{cut.label} {name}: {area:.2f} mm² by the mesh, {other:.2f} by OCCT")
            if abs(other - area) > max(AREA_SHARE * max(area, other), AREA_FLOOR):
                problems.append(
                    f"{cut.label}: {name} is {area:.2f} mm² by the mesh and {other:.2f} by OCCT"
                )
        elif area > 0:
            notes.append(f"{cut.label}: {name} has no exact surface, so its area is the mesh's")
        per_file[name] = facts
    numbers["files"] = per_file
    overlaps = []
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            both = float(sections[i][2].intersection(sections[j][2]).area)
            if both > OVERLAP_FLOOR:
                pair = [sections[i][1], sections[j][1]]
                overlaps.append({"files": pair, "area_mm2": round(both, 3)})
                notes.append(
                    f"{cut.label}: {pair[0]} and {pair[1]} overlap by {both:.2f} mm²: they take "
                    "the same space here, and the one drawn after hides the other"
                )
    numbers["overlaps"] = overlaps
    painted = float((np.asarray(ids) > 0).sum()) / scale**2
    drawn_area = float(shown.area)
    numbers["painted_mm2"] = round(painted, 3)
    numbers["polygons_mm2"] = round(drawn_area, 3)
    numbers["px_per_mm"] = round(scale, 3)
    seconds.append(
        f"{cut.label}: {drawn_area:.1f} mm² by the polygons in the window, {painted:.1f} mm² "
        "painted"
    )
    # A filled polygon paints the pixels its outline crosses too: up to a pixel along its length.
    edge = float(shown.boundary.length) / scale
    if abs(painted - drawn_area) > PIXEL_SHARE * drawn_area + edge + 4 / scale**2:
        problems.append(f"{cut.label}: {painted:.1f} mm² painted, {drawn_area:.1f} mm² cut")
    if cut.ruled:
        _rule(paint, pixel, low, high)
    if gaps:
        found, lines, more, wrong = _gaps(cut, sections, clipped, low, high)
        numbers["gaps"] = found
        notes += lines
        seconds += more
        problems += wrong
        _mark_gaps(paint, pixel, found)
        if found:
            notes.append(f"{cut.label}: {IN_PLANE}")
            paint.text((8, 24), "gaps in-plane: never less than 3D", fill=GAP_INK)
    return Drawn(np.asarray(picture), numbers, problems, seconds, notes, marks)
