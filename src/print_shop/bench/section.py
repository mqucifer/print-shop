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

**Each piece's least width** (mqucifer/print-shop#101), the thickness of its thinnest part
across: a straight wall's thickness, or a tab's. `info` reads only a part's outer size and `wall`
only the wall round a hole, so on `eval-edges-5` a 2 mm wall was held by nothing but the script's
`WALL = 2`; and a snap's tab fused to its wall read as one piece with it, where a tab standing
free is a piece of its own. Read from the polygon's edges: for each two that face each other
within `FACING_DEG` and don't share a corner, the least distance along one's inward normal to
the other where they overlap, through the piece. So a straight wall reads its thickness, and a
sharp tip, where two sides meet, isn't a width. **Second method:** rays from points along each
edge, `RAY_STEP` apart and at its ends, along its inward normal to the first edge they meet,
counted between the same pairs. They agree within `WIDTH_TOL`, or it's `DISAGREE`.

**A place named** by `--box NAME=X0,Y0,Z0,X1,Y1,Z1` reads the section inside that box, at each
level that passes through it: its pieces, their least width and size, and how many of the level's
pieces lie wholly inside the box. So a need reads one wall, or one tab, after a rebuild changes
the pieces' order.
"""

from __future__ import annotations

from typing import Any, NamedTuple

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
#: The rays' starts along each edge, in mm: at least one an edge, at its middle.
RAY_STEP = 0.1
#: The most rays one piece's outline is read by: past it, the starts spread out.
RAYS_MOST = 20_000
#: A ray's width counts where the edge it meets faces back within this, in degrees.
FACING_DEG = 30.0
#: The two readings of a least width agree within this, in mm: each side's chords, twice, and
#: where two facing edges close at the most `FACING_DEG`, the rays' half-step along them.
WIDTH_TOL = 4 * FINE[0] + RAY_STEP / 2 * float(np.tan(np.radians(FACING_DEG)))


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
    rays = width_by_rays(poly)
    width = least_width(poly, None if rays is None else rays + WIDTH_TOL)
    return {
        "area_mm2": round(float(poly.area), 4),
        "box": [_in_3d(axis, at, x0, y0), _in_3d(axis, at, x1, y1)],
        "size_mm": [round(x1 - x0, 4), round(y1 - y0, 4)],
        "least_width_mm": None if width is None else round(width, 4),
        "least_width_rays_mm": None if rays is None else round(rays, 4),
        "outline": kind,
        **figures,
        "holes": holes,
    }


def _edges(poly: Any) -> dict[str, np.ndarray]:
    """A piece's edges, the outline counter-clockwise and its holes clockwise, so the material is
    on each edge's left: each edge's start, its run, length and inward normal, and its ring, its
    place in it and the ring's size, to tell which edges share a corner."""
    upright = shapely.geometry.polygon.orient(poly, 1.0)
    starts, ends, ring_of, place, sizes = [], [], [], [], []
    for r, ring in enumerate([upright.exterior, *upright.interiors]):
        coords = np.asarray(ring.coords)[:, :2]
        starts.append(coords[:-1])
        ends.append(coords[1:])
        ring_of.append(np.full(len(coords) - 1, r))
        place.append(np.arange(len(coords) - 1))
        sizes.append(np.full(len(coords) - 1, len(coords) - 1))
    a, b = np.vstack(starts), np.vstack(ends)
    run = b - a
    length = np.hypot(run[:, 0], run[:, 1])
    real = length > 1e-9
    found = {
        "a": a[real],
        "run": run[real],
        "length": length[real],
        "ring": np.concatenate(ring_of)[real],
        "at": np.concatenate(place)[real],
        "size": np.concatenate(sizes)[real],
    }
    found["inward"] = (
        np.column_stack([-found["run"][:, 1], found["run"][:, 0]]) / found["length"][:, None]
    )
    return found


def _counted(edges: dict[str, np.ndarray], i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """Which pairs of edges a width is read between: j faces back at i within `FACING_DEG`, and
    the two don't share a corner, so a sharp tip, where two sides meet, isn't a width."""
    same = edges["ring"][i] == edges["ring"][j]
    apart = np.abs(edges["at"][i] - edges["at"][j])
    shares = same & ((apart <= 1) | (apart >= edges["size"][i] - 1))
    facing = np.einsum("ij,ij->i", edges["inward"][i], -edges["inward"][j])
    return np.asarray(~shares & (facing >= np.cos(np.radians(FACING_DEG))))


def least_width(poly: Any, within: float | None = None) -> float | None:
    """The thickness of a piece's thinnest part across (mqucifer/print-shop#101), from its edges:
    for each two that face each other (`_counted`), the least distance along one's inward normal
    to the other, over the stretch where they overlap seen along it, which, both being straight,
    is at one end of that stretch; kept where that line lies in the piece. None where no two
    edges face each other, as in a triangle. `within` leaves out pairs further apart than it,
    which can't be the least."""
    edges = _edges(poly)
    a, run, inward = edges["a"], edges["run"], edges["inward"]
    segments = shapely.linestrings(np.stack([a, a + run], axis=1))
    reach = within if within is not None else float(poly.length)
    i, j = shapely.STRtree(segments).query(segments, predicate="dwithin", distance=reach)
    keep = (i != j) & _counted(edges, i, j)
    i, j = i[keep], j[keep]
    if not len(i):
        return None
    squared = np.einsum("ij,ij->i", run[i], run[i])
    # Where j's ends lie along i, as a share of i: the stretch where they overlap.
    t0 = np.einsum("ij,ij->i", a[j] - a[i], run[i]) / squared
    t1 = np.einsum("ij,ij->i", a[j] + run[j] - a[i], run[i]) / squared
    low = np.clip(np.minimum(t0, t1), 0.0, 1.0)
    high = np.clip(np.maximum(t0, t1), 0.0, 1.0)
    overlap = high > low
    i, j, low, high = i[overlap], j[overlap], low[overlap], high[overlap]
    if not len(i):
        return None
    ends = np.concatenate([low, high])
    i2, j2 = np.concatenate([i, i]), np.concatenate([j, j])
    point = a[i2] + ends[:, None] * run[i2]
    turn = np.einsum("ij,ij->i", inward[j2], inward[i2])
    far = np.einsum("ij,ij->i", inward[j2], a[j2] - point) / turn
    ahead = far > 1e-9
    point, far, i2 = point[ahead], far[ahead], i2[ahead]
    if not len(far):
        return None
    lines = shapely.linestrings(np.stack([point, point + inward[i2] * far[:, None]], axis=1))
    inside = shapely.covers(poly.buffer(1e-7, join_style="mitre"), lines)
    if not np.any(inside):
        return None
    return float(far[inside].min())


def width_by_rays(poly: Any) -> float | None:
    """The second reading of a least width: from points along each edge, its ends among them, a
    ray along its inward normal to the first edge it meets, counted between the same pairs as the
    first (`_counted`). The least of them, or None where no ray counts."""
    edges = _edges(poly)
    a, run, inward, length = edges["a"], edges["run"], edges["inward"], edges["length"]
    step = max(RAY_STEP, float(length.sum()) / RAYS_MOST)
    count = np.maximum(1, (length / step).astype(int)) + 1
    edge = np.repeat(np.arange(len(a)), count)
    first = np.concatenate([[0], np.cumsum(count)[:-1]])
    k = np.arange(len(edge)) - np.repeat(first, count)
    # From just inside one end to just inside the other, so each end is read.
    inset = np.minimum(1e-4 / length[edge], 0.5)
    t = inset + (1 - 2 * inset) * k / np.repeat(count - 1, count)
    eps = 1e-6
    origin = a[edge] + t[:, None] * run[edge] + inward[edge] * eps
    way = inward[edge]
    far = float(np.hypot(*np.ptp(np.asarray(poly.bounds).reshape(2, 2), axis=0))) + 1.0
    segments = shapely.linestrings(np.stack([a, a + run], axis=1))
    rays = shapely.linestrings(np.stack([origin, origin + way * far], axis=1))
    ray_i, seg_j = shapely.STRtree(segments).query(rays, predicate="intersects")
    if not len(ray_i):
        return None
    d, e = way[ray_i], run[seg_j]
    w = a[seg_j] - origin[ray_i]
    denom = d[:, 0] * e[:, 1] - d[:, 1] * e[:, 0]
    usable = np.abs(denom) > 1e-12
    safe = np.where(usable, denom, 1.0)
    s = (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / safe
    u = (w[:, 0] * d[:, 1] - w[:, 1] * d[:, 0]) / safe
    usable &= (s > 0) & (u >= -1e-9) & (u <= 1 + 1e-9)
    ray_i, seg_j, s = ray_i[usable], seg_j[usable], s[usable]
    if not len(ray_i):
        return None
    # Each ray's first edge: the nearest along it.
    order = np.lexsort((s, ray_i))
    ray_i, seg_j, s = ray_i[order], seg_j[order], s[order]
    keep = np.concatenate([[True], ray_i[1:] != ray_i[:-1]])
    ray_i, seg_j, s = ray_i[keep], seg_j[keep], s[keep]
    counted = s[_counted(edges, edge[ray_i], seg_j)]
    if not len(counted):
        return None
    return float(counted.min()) + eps


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
    named_boxes = [
        NamedBox(name, np.asarray(lo, dtype=float), np.asarray(hi, dtype=float))
        for name, (lo, hi) in getattr(args, "boxes", None) or []
    ]
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
            "pieces_count": len(rows),
            "area_mm2": round(area, 4),
            "holes": holes,
        }
        named = {place.name: _place(place, section, pieces, axis, at) for place in named_boxes}
        if named:
            cut["places"] = named
        report.line(
            f"{part.name} at {axis}={at:g}: {len(rows)} piece(s), {area:.4g} mm² in all, "
            f"{holes} hole(s); sizes are along {u} then {v}"
        )
        for n, row in enumerate(rows, 1):
            differs += _widths_differ(row, f"at {axis}={at:g}, piece {n}")
        for name, place in named.items():
            differs += _widths_differ(place, f"at {axis}={at:g}, in {name}")
        for n, row in enumerate(rows[:PIECES_SAID], 1):
            text = (
                f"  piece {n}: {row['area_mm2']:g} mm², {row['outline']}, "
                f"{row['size_mm'][0]:g} x {row['size_mm'][1]:g} mm from {_point(row['box'][0])} "
                f"to {_point(row['box'][1])}, {_width_said(row)}"
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
        for name, place in named.items():
            report.line(f"  {name}: {_place_said(place, u, v)}")
        widths = [r["least_width_rays_mm"] for r in rows[:PIECES_SAID]]
        report.second(
            f"at {axis}={at:g}, each piece's least width by rays along each edge's inward normal, "
            f"{RAY_STEP:g} mm apart: "
            + ", ".join("none" if w is None else f"{w:.4f}" for w in widths)
            + f" mm, within {WIDTH_TOL:.3f} of the edges' own"
        )
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


class NamedBox(NamedTuple):
    """A place a `--box NAME=X0,Y0,Z0,X1,Y1,Z1` names."""

    name: str
    low: np.ndarray
    high: np.ndarray


def _place(place: Any, section: Any, pieces: list[Any], axis: str, at: float) -> dict[str, Any]:
    """The section inside a named box, at a level that passes through it: its pieces, their least
    width and their size, and how many of the level's pieces lie wholly inside the box. Nothing,
    at a level that doesn't."""
    u, v, w = PLANES[axis]
    low, high = place.low, place.high
    found: dict[str, Any] = {
        "box": [[round(float(x), 4) for x in low], [round(float(x), 4) for x in high]],
        "pieces": 0,
        "whole_pieces": 0,
        "least_width_mm": None,
        "least_width_rays_mm": None,
        "size_mm": None,
    }
    if not low[w] <= at <= high[w]:
        return found
    window = shapely.box(low[u], low[v], high[u], high[v])
    inside = shapely.intersection(section, window)
    parts = [g for g in getattr(inside, "geoms", [inside]) if g.geom_type == "Polygon"]
    parts = [g for g in parts if g.area > 1e-6]
    found["pieces"] = len(parts)
    found["whole_pieces"] = sum(1 for p in pieces if window.buffer(1e-6).covers(p))
    if not parts:
        return found
    rays = [width_by_rays(g) for g in parts]
    widths = [
        least_width(g, None if r is None else r + WIDTH_TOL)
        for g, r in zip(parts, rays, strict=True)
    ]
    known = [x for x in widths if x is not None]
    seen = [x for x in rays if x is not None]
    found["least_width_mm"] = round(min(known), 4) if known else None
    found["least_width_rays_mm"] = round(min(seen), 4) if seen else None
    x0, y0, x1, y1 = shapely.union_all(parts).bounds
    found["size_mm"] = [round(x1 - x0, 4), round(y1 - y0, 4)]
    return found


def _widths_differ(row: dict[str, Any], where: str) -> list[str]:
    """Where a piece's or a place's two readings of its least width differ."""
    first, rays = row["least_width_mm"], row["least_width_rays_mm"]
    if first is None and rays is None:
        return []
    if first is None or rays is None or abs(first - rays) > WIDTH_TOL:
        said = "none" if first is None else f"{first:.4f} mm"
        by_rays = "none" if rays is None else f"{rays:.4f}"
        return [
            f"{where} the least width by its facing edges is {said}, and by the rays {by_rays}, "
            f"against {WIDTH_TOL:.3f} mm"
        ]
    return []


def _width_said(row: dict[str, Any]) -> str:
    width = row["least_width_mm"]
    if width is None:
        return "no two of its edges face each other, so no least width"
    return f"least width {width:.4f} mm"


def _place_said(place: dict[str, Any], u: str, v: str) -> str:
    if place["size_mm"] is None:
        return "nothing of the section in its box at this level"
    width = place["least_width_mm"]
    least = "no least width" if width is None else f"least width {width:.4f} mm"
    size = place["size_mm"]
    return (
        f"{place['pieces']} piece(s) of the section in its box, {least}, {size[0]:g} x "
        f"{size[1]:g} mm along {u} then {v}; {place['whole_pieces']} of the level's pieces lie "
        "wholly inside it"
    )


def _point(p: Any) -> str:
    return "(" + ", ".join(f"{float(v) + 0.0:g}" for v in p) + ")"
