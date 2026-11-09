"""A part cut by a plane: each separate piece, its outline, its box, its area and its holes
(mqucifer/print-shop#77).

On `pi5-case-1` the model wrote 9 cross-section scripts of its own, on a fan's frame, a board
stack, a plug and the lid, each printing a box with no second reading. `slice FILE --at z=H`
reads the part's material on that plane in one plain reply: `z=H` is level at height H, `x=H` and
`y=H` upright. Give `--at` more than once for several planes.

**Each piece**, largest first: its area, its box, and its outline, said as a rectangle or a circle
where it is one, its points where it has 12 or fewer, and else its point count. **Each hole** in
it: its centre, its size, its area, and its diameter where it's round. All in the part's own
axes, so a figure can be put straight into a script.

**The second method**: on a STEP, OCCT's section of the exact surface: its area, its pieces and
its holes, against the mesh's. The mesh's chords cut inside a curved edge, so the areas may differ
by the chord tolerance along the outline. On an STL, its triangles cut one by one.
"""

from __future__ import annotations

from typing import Any

import cadquery as cq
import numpy as np
import shapely
import trimesh

from .report import Report
from .shapes import FINE, NoAnswer, Part, load, polygons

#: The axes a cut's plane lies across: the two in the plane, then the one it's square to.
PLANES = {"z": (0, 1, 2), "x": (1, 2, 0), "y": (0, 2, 1)}
#: How many pieces, and holes of each, a reply names; every one is in the record.
PIECES_SAID = 12
HOLES_SAID = 12
#: An outline with this many points or fewer, once simplified, is said point by point.
POINTS_SAID = 12


def _turned(part: Part, axis: str) -> Any:
    """The part's manifold turned so the plane's normal is z, as a 3 x 4 matrix moves it."""
    built = part.manifold()
    if axis == "z":
        return built
    u, v, w = PLANES[axis]
    matrix = np.zeros((3, 4))
    for row, k in enumerate((u, v, w)):
        matrix[row, k] = 1.0
    return built.transform(matrix.tolist())


def _in_3d(axis: str, at: float, u: float, v: float) -> list[float]:
    point = [0.0, 0.0, 0.0]
    a, b, c = PLANES[axis]
    point[a], point[b], point[c] = u, v, at
    return [round(p, 4) + 0.0 for p in point]


def _shape_of(ring: Any, tol: float) -> tuple[str, dict[str, Any]]:
    """What an outline is: a rectangle, a circle, or its points, and the figures that say so."""
    poly = shapely.Polygon(ring)
    area = float(poly.area)
    centre = poly.centroid
    points = np.asarray(ring.coords)[:-1]
    radii = np.hypot(points[:, 0] - centre.x, points[:, 1] - centre.y)
    if len(points) >= 12 and np.ptp(radii) <= 2 * tol + 1e-3:
        diameter = 2 * float(np.sqrt(area / np.pi))
        return f"a circle Ø{diameter:.4g}", {"circle_d_mm": round(diameter, 4)}
    rect = poly.minimum_rotated_rectangle
    if abs(float(rect.area) - area) <= max(1e-3, 1e-4 * area) + tol * float(poly.length):
        x0, y0, x1, y1 = poly.bounds
        if abs((x1 - x0) * (y1 - y0) - float(rect.area)) <= max(1e-3, 1e-4 * area):
            return "a rectangle", {"rectangle_mm": [round(x1 - x0, 4), round(y1 - y0, 4)]}
        corners = np.asarray(rect.exterior.coords)
        sides = [corners[1] - corners[0], corners[2] - corners[1]]
        long, short = sorted(sides, key=lambda d: -float(np.linalg.norm(d)))
        turned = float(np.degrees(np.arctan2(long[1], long[0]))) % 180
        lengths = [round(float(np.linalg.norm(long)), 4), round(float(np.linalg.norm(short)), 4)]
        return (
            f"a rectangle {lengths[0]:g} x {lengths[1]:g}, its long side at {turned:.1f}° to the "
            "first axis",
            {"rectangle_mm": lengths, "turned_deg": round(turned, 2)},
        )
    simple = poly.simplify(tol)
    kept = np.asarray(simple.exterior.coords)[:-1]
    if len(kept) <= POINTS_SAID:
        return f"an outline of {len(kept)} points", {"points": kept.round(4).tolist()}
    return f"an outline of {len(points)} points", {}


def _piece(poly: Any, axis: str, at: float) -> dict[str, Any]:
    x0, y0, x1, y1 = poly.bounds
    kind, figures = _shape_of(poly.exterior, FINE[0])
    holes = []
    for ring in poly.interiors:
        hole = shapely.Polygon(ring)
        hx0, hy0, hx1, hy1 = hole.bounds
        what, more = _shape_of(ring, FINE[0])
        holes.append(
            {
                "centre": _in_3d(axis, at, hole.centroid.x, hole.centroid.y),
                "size_mm": [round(hx1 - hx0, 4), round(hy1 - hy0, 4)],
                "area_mm2": round(float(hole.area), 4),
                "shape": what,
                **more,
            }
        )
    holes.sort(key=lambda h: -h["area_mm2"])
    return {
        "area_mm2": round(float(poly.area), 4),
        "box": [_in_3d(axis, at, x0, y0), _in_3d(axis, at, x1, y1)],
        "size_mm": [round(x1 - x0, 4), round(y1 - y0, 4)],
        "outline": kind,
        **figures,
        "holes": holes,
    }


def _by_occt(part: Part, axis: str, at: float) -> tuple[float, int, int] | None:
    """OCCT's section of the exact surface: its area, its pieces and its holes."""
    box = part.shape.BoundingBox()
    size = 2 * max(box.xlen, box.ylen, box.zlen) + 10
    centre = [(box.xmin + box.xmax) / 2, (box.ymin + box.ymax) / 2, (box.zmin + box.zmax) / 2]
    centre[PLANES[axis][2]] = at
    normal = [0.0, 0.0, 0.0]
    normal[PLANES[axis][2]] = 1.0
    plane = cq.Face.makePlane(size, size, cq.Vector(*centre), cq.Vector(*normal))
    faces = part.shape.intersect(plane).Faces()
    if not faces:
        return None
    holes = sum(len(f.innerWires()) for f in faces)
    return float(sum(f.Area() for f in faces)), len(faces), holes


def _by_triangles(part: Part, axis: str, at: float) -> float:
    """An STL's section, its triangles cut one by one, as `shapes.section_checked` does."""
    normal = np.zeros(3)
    normal[PLANES[axis][2]] = 1.0
    lines = trimesh.intersections.mesh_plane(  # type: ignore[no-untyped-call]
        part.fine(), normal, normal * at
    )
    u, v, _ = PLANES[axis]
    segments = [shapely.LineString(np.round(s[:, [u, v]], 7)) for s in lines]
    faces = list(shapely.polygonize(segments).geoms)
    outlines = [shapely.Polygon(f.exterior) for f in faces]
    area = 0.0
    for region in faces:
        inside = region.representative_point()
        if sum(o.contains(inside) for o in outlines) % 2 == 1:
            area += float(region.area)
    return area


def run(args: Any, report: Report) -> None:
    part = load(args.file)
    report.notes += part.notes
    low, high = part.extent()
    cuts: list[dict[str, Any]] = []
    differs: list[str] = []
    for axis, at in args.at:
        k = PLANES[axis][2]
        if not low[k] < at < high[k]:
            raise NoAnswer(
                f"{part.name} runs from {axis}={low[k]:.4g} to {axis}={high[k]:.4g}: nothing of it "
                f"is at {axis}={at:g}"
            )
        section = polygons(_turned(part, axis).slice(at).to_polygons())
        pieces = sorted(section.geoms, key=lambda p: -p.area)
        rows = [_piece(p, axis, at) for p in pieces]
        area = float(section.area)
        holes = sum(len(r["holes"]) for r in rows)
        u, v, _ = ("xyz"[i] for i in PLANES[axis])
        cut: dict[str, Any] = {
            "at": f"{axis}={at:g}",
            "pieces": rows,
            "area_mm2": round(area, 4),
            "holes": holes,
        }
        report.line(
            f"{part.name} at {axis}={at:g}: {len(rows)} piece(s), {area:.4g} mm² in all, "
            f"{holes} hole(s); sizes are along {u} then {v}"
        )
        for n, row in enumerate(rows[:PIECES_SAID], 1):
            text = (
                f"  piece {n}: {row['area_mm2']:g} mm², {row['outline']}, "
                f"{row['size_mm'][0]:g} x {row['size_mm'][1]:g} mm from {_point(row['box'][0])} "
                f"to {_point(row['box'][1])}"
            )
            if "points" in row:
                text += f", its points as ({u}, {v}): " + " ".join(
                    f"({p[0]:g}, {p[1]:g})" for p in row["points"]
                )
            report.line(text)
            for h in row["holes"][:HOLES_SAID]:
                width, height = h["size_mm"]
                size = "" if "circle_d_mm" in h else f", {width:g} x {height:g} mm"
                report.line(
                    f"    hole at {_point(h['centre'])}: {h['shape']}{size}, {h['area_mm2']:g} mm²"
                )
            if len(row["holes"]) > HOLES_SAID:
                report.line(f"    and {len(row['holes']) - HOLES_SAID} more holes, in the record")
        if len(rows) > PIECES_SAID:
            report.line(f"  and {len(rows) - PIECES_SAID} smaller pieces, in the record")
        # The chords cut inside a curved edge by at most the chord tolerance along it.
        band = 2 * FINE[0] * float(section.length) + 1e-3
        if part.is_brep:
            other = _by_occt(part, axis, at)
            if other is None:
                differs.append(
                    f"at {axis}={at:g} OCCT's section has no face, and the mesh's is {area:.3f} mm²"
                )
                continue
            exact, count, inner = other
            cut["second"] = {"area_mm2": round(exact, 4), "pieces": count, "holes": inner}
            report.second(
                f"at {axis}={at:g}, OCCT's section of the exact surface: {exact:.4f} mm² in "
                f"{count} piece(s) with {inner} hole(s), against the mesh's {area:.4f} in "
                f"{len(rows)} with {holes}"
            )
            if abs(exact - area) > band or count != len(rows) or inner != holes:
                differs.append(
                    f"at {axis}={at:g} OCCT reads {exact:.4f} mm² in {count} piece(s) with "
                    f"{inner} hole(s), and the mesh {area:.4f} in {len(rows)} with {holes}, "
                    f"its chords allowing {band:.4f} mm²"
                )
        else:
            cut_area = _by_triangles(part, axis, at)
            cut["second"] = {"area_mm2": round(cut_area, 4)}
            report.second(
                f"at {axis}={at:g}, the mesh's triangles cut one by one: {cut_area:.4f} mm², "
                f"against {area:.4f}"
            )
            if abs(cut_area - area) > band:
                differs.append(
                    f"at {axis}={at:g} the triangles cut one by one read {cut_area:.4f} mm², "
                    f"and the mesh's section {area:.4f}"
                )
        cuts.append(cut)
    report.numbers["cuts"] = cuts
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    said = "; ".join(
        f"{c['at']}: {len(c['pieces'])} piece(s), {c['holes']} hole(s)" for c in cuts
    )
    report.verdict("OK", f"a reading of {part.name}: {said}")


def _point(p: Any) -> str:
    return "(" + ", ".join(f"{float(v) + 0.0:g}" for v in p) + ")"
