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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
import trimesh
from PIL import Image, ImageDraw

from .shapes import NoAnswer, Part, to_manifold

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
#: The mesh's area and OCCT's may differ by this share, or this many mm², whichever is more.
AREA_SHARE = 0.03
AREA_FLOOR = 0.5
#: What's painted and the polygons may differ by this share of the drawing.
PIXEL_SHARE = 0.03
#: Two files' sections overlapping by less than this, in mm², are touching, not overlapping.
OVERLAP_FLOOR = 0.05
NUMBER = re.compile(r"-?\d+(?:\.\d*)?|-?\.\d+")


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


def parse(text: str) -> Cut:
    """`z=3`, `x=50`, `x=50,y=20:40` or `z=3,x=0:40,y=10:30`."""
    words = [w.strip() for w in text.split(",") if w.strip()]
    if not words:
        raise NoAnswer("a cut is AXIS=VALUE, such as z=3 or x=50,y=20:40")
    axis, _, value = words[0].partition("=")
    if axis not in AXES or not NUMBER.fullmatch(value):
        raise NoAnswer(f"a cut starts with x, y or z and where: z=3 or x=50, not {words[0]!r}")
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


def section(mesh: trimesh.Trimesh, cut: Cut, name: str) -> Any:
    """A mesh's material on the plane, as Shapely, in the picture's right and up."""
    _, turn, sign = TURNS[cut.axis]
    turned = mesh.copy()
    matrix = np.eye(4)
    matrix[:3, :3] = turn
    turned.apply_transform(matrix)
    found = to_manifold(turned, name).slice(sign * cut.at).to_polygons()
    # Material is what's inside an odd number of outlines, so an island in an opening is kept: the
    # outlines of a closed mesh's section don't cross, and taking each in turn flips what's inside.
    material: Any = shapely.Polygon()
    for contour in found:
        points = np.asarray(contour, dtype=float)
        if len(points) >= 3:
            material = material.symmetric_difference(shapely.make_valid(shapely.Polygon(points)))
    return shapely.make_valid(material)


def occt_area(shape: Any, cut: Cut) -> float | None:
    """The exact surface's area on the plane, by OCCT, or None where it gave no faces."""
    import cadquery as cq

    box = shape.BoundingBox()
    size = 2 * max(box.xlen, box.ylen, box.zlen) + 10
    centre = [(box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2, (box.zmin + box.zmax) / 2]
    centre[AXES.index(cut.axis)] = cut.at
    normal = [0.0, 0.0, 0.0]
    normal[AXES.index(cut.axis)] = 1.0
    plane = cq.Face.makePlane(size, size, cq.Vector(*centre), cq.Vector(*normal))
    faces = shape.intersect(plane).Faces()
    return sum(f.Area() for f in faces) if faces else None


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


def _clip(material: Any, cut: Cut, low: np.ndarray, high: np.ndarray) -> Any:
    return material.intersection(shapely.box(low[0], low[1], high[0], high[1]))


def draw(files: list[tuple[str, list[Part], tuple[int, int, int]]], cut: Cut, size: int) -> Drawn:
    """Each file's section on the plane, in its colour, with the checks behind the picture."""
    right, up = TURNS[cut.axis][0]
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
    scale = size / (FILL * float(max(span)))
    margin = (size - span * scale) / 2

    def pixel(coords: Any) -> list[tuple[float, float]]:
        xy = (np.asarray(coords)[:, :2] - low) * scale + margin
        return [(float(x), float(size - y)) for x, y in xy]

    picture = Image.new("RGB", (size, size), "white")
    paint = ImageDraw.Draw(picture)
    ids = Image.new("L", (size, size), 0)
    numbers: dict[str, Any] = {"plane": f"{cut.axis}={cut.at:g}", "right": right, "up": up}
    per_file: dict[str, Any] = {}
    problems: list[str] = []
    seconds: list[str] = []
    notes: list[str] = []
    marks: list[tuple[int, float, float]] = []
    # The largest first, so a pane in an opening, or an island in a pane, is drawn over it.
    for k, name, material, parts in sorted(sections, key=lambda s: -s[2].area):
        colour = files[k][2]
        here = _clip(material, cut, low, high)
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
    return Drawn(np.asarray(picture), numbers, problems, seconds, notes, marks)
