"""The least wall round a named hole, along its whole length, by sections and by rays.

`thin`'s `thinnest_flat_mm` is about the whole part, and on `cup-1` it was quoted as the hanging
hole's wall. This reads one hole: the one round a point given inside it.

The first method cuts the part across the hole's axis at every layer the hole runs through, and
takes the least distance from the hole's edge to any other edge of the material round it: the
part's outside, or another opening. The second never touches a section: rays from the point out
to the hole's wall, and from where each meets it, a ray through the wall along the surface's
normal, to the far side. The least wall runs square to the hole's wall where it's least, so the
two meet there. A least wall between two corners isn't square to either, and rays read it
thicker: that's a disagreement, and said.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import shapely
import shapely.ops

from .readback import _circle, _frame, _turned
from .report import Report
from .shapes import FINE, NoAnswer, flat_heights, load, slice_at, to_manifold

#: How far the two methods may differ, in mm, before it's a disagreement: the mesh's chords on
#: both walls, and the spacing of rays round a small hole.
BAND = 0.03
#: A height this close to a level face is moved off it, in mm, so a section reads one side.
LEVEL = 1e-3
#: A wall whose normal is steeper than this (the z of its unit normal) isn't read by a level ray.
STEEP = 0.9


def _state(section: Any, x: float, y: float) -> tuple[str, Any, int | None]:
    """At one height: 'closed' with the material round the hole and which ring it is, 'material',
    or 'open' to the outside."""
    point = shapely.Point(x, y)
    for poly in section.geoms:
        if poly.contains(point):
            return "material", None, None
    for poly in section.geoms:
        for k, ring in enumerate(poly.interiors):
            if shapely.Polygon(ring).contains(point):
                return "closed", poly, k
    return "open", None, None


def _by_section(poly: Any, k: int) -> dict[str, Any]:
    """The least distance from one hole's edge to every other edge of its material."""
    hole = shapely.LinearRing(poly.interiors[k].coords)
    others = [("the part's outside", shapely.LinearRing(poly.exterior.coords))]
    for j, ring in enumerate(poly.interiors):
        if j != k:
            x0, y0, x1, y1 = shapely.Polygon(ring).bounds
            said = (
                f"the opening round {(x0 + x1) / 2:.2f},{(y0 + y1) / 2:.2f} "
                f"({x1 - x0:.2f} x {y1 - y0:.2f} mm)"
            )
            others.append((said, shapely.LinearRing(ring.coords)))
    best = min(others, key=lambda o: float(hole.distance(o[1])))
    near, far = shapely.ops.nearest_points(hole, best[1])
    return {
        "wall": float(hole.distance(best[1])),
        "from": (near.x, near.y),
        "to": (far.x, far.y),
        "toward": best[0],
        "hole": shapely.Polygon(hole),
    }


def _by_rays(built: Any, x: float, y: float, z: float, count: int, length: float) -> dict[str, Any]:
    """Rays out to the hole's wall, then through the wall along its normal: the least, and how
    many rays met no wall."""
    least, at, missed = np.inf, None, 0
    start = np.array([x, y, z])
    for angle in np.linspace(0, 2 * np.pi, count, endpoint=False):
        way = np.array([np.cos(angle), np.sin(angle), 0.0])
        met = built.ray_cast(tuple(start), tuple(start + way * length))
        if not met:
            missed += 1
            continue
        first = min(met, key=lambda h: float(np.linalg.norm(np.asarray(h.position) - start)))
        normal = np.asarray(first.normal, dtype=float)
        if abs(normal[2]) > STEEP:
            continue
        level = -np.array([normal[0], normal[1], 0.0])
        level /= np.linalg.norm(level)
        wall = np.asarray(first.position, dtype=float)
        inside = wall + level * 1e-5
        through = built.ray_cast(tuple(inside), tuple(inside + level * length))
        if not through:
            continue
        far = min(float(np.linalg.norm(np.asarray(h.position) - inside)) for h in through)
        if far < least:
            least, at = far, wall
    return {"wall": float(least), "at": at, "missed": missed}


def run(args: Any, report: Report) -> None:
    part = load(args.file)
    report.notes += part.notes
    order, names = _frame(args.axis)
    mesh = _turned(part, order)
    built = to_manifold(mesh, part.name)
    x, y, z = (float(v) for v in np.asarray(args.hole, dtype=float)[order])
    low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
    length = 2 * float(np.linalg.norm(np.ptp(np.asarray(mesh.bounds), axis=0)))
    if not low < z < high:
        raise NoAnswer(f"{z:g} along {args.axis} is outside the part, from {low:g} to {high:g}")
    flats = [f["z"] for f in flat_heights(mesh, least=0.01)]

    def off_faces(h: float) -> float:
        for f in flats:
            if abs(f - h) <= LEVEL:
                return f + 2 * LEVEL if f + 2 * LEVEL < high else f - 2 * LEVEL
        return h

    def unturned(v: tuple[float, float], h: float) -> list[float]:
        found = np.empty(3)
        found[order] = [v[0], v[1], h]
        return [round(float(c), 3) for c in found]

    start = off_faces(z)
    state, poly, k = _state(slice_at(built, start), x, y)
    if state != "closed":
        said = "in material" if state == "material" else "open to the outside, not a closed hole"
        raise NoAnswer(
            f"{','.join(f'{v:g}' for v in args.hole)} isn't inside a closed hole: at {start:g} "
            f"along {args.axis} it's {said}. Name a point inside the hole"
        )
    # The hole's whole length: layer by layer from the point, each way, while it's still closed.
    count = max(1, int(np.ceil((high - low) / args.layer)))
    grid = low + (np.arange(count) + 0.5) * (high - low) / count
    heights = [start]
    ends = {}
    for way, run_of in ((1, grid[grid > start]), (-1, grid[grid < start][::-1])):
        ends[way] = "the part's face"
        for h in run_of:
            h = off_faces(float(h))
            found, _, _ = _state(slice_at(built, h), x, y)
            if found != "closed":
                ends[way] = "material" if found == "material" else "the outside"
                break
            heights.append(h)
    heights.sort()
    rows = []
    differs, problems = [], []
    for h in heights:
        _, poly, k = _state(slice_at(built, h), x, y)
        assert k is not None, "every height kept was closed"
        first = _by_section(poly, k)
        second = _by_rays(built, x, y, h, args.n, length)
        rows.append((h, first, second))
    least_h, least, _ = min(rows, key=lambda r: r[1]["wall"])
    rays_h, _, rays = min(rows, key=lambda r: r[2]["wall"])
    hole = least["hole"]
    centre, diameter = _circle(np.asarray(hole.exterior.coords)[:-1])
    report.fact(
        "hole_runs",
        f"{heights[0]:.3f} to {heights[-1]:.3f} along {args.axis}",
        how=f"{len(heights)} section(s), a layer apart; it ends in {ends[-1]} below and "
        f"{ends[1]} above",
    )
    report.numbers["hole_from"], report.numbers["hole_to"] = heights[0], heights[-1]
    report.fact("hole_mm2", round(float(hole.area), 3), how="the section where the wall is least")
    report.fact("hole_fit_d_mm", round(diameter, 3), how="a circle fit to that section's edge")
    report.numbers["hole_fit_centre"] = unturned((float(centre[0]), float(centre[1])), least_h)
    report.fact(
        "least_wall_mm",
        round(least["wall"], 4),
        how=f"sections across the hole, toward {least['toward']}",
    )
    report.numbers["least_wall_from"] = unturned(least["from"], least_h)
    report.numbers["least_wall_to"] = unturned(least["to"], least_h)
    report.numbers["least_wall_toward"] = least["toward"]
    report.line(
        f"the least wall is from {report.numbers['least_wall_from']} on the hole's edge to "
        f"{report.numbers['least_wall_to']}, on {least['toward']}"
    )
    report.numbers["least_wall_rays_mm"] = (
        round(rays["wall"], 4) if np.isfinite(rays["wall"]) else None
    )
    missed = sum(r[2]["missed"] for r in rows)
    report.second(
        f"rays from the point to the hole's wall, and on through it along the wall's normal, "
        f"{args.n} at each of {len(rows)} height(s): the least wall {rays['wall']:.4f} mm"
        + (f" at {unturned(tuple(rays['at'][:2]), rays_h)}" if rays["at"] is not None else "")
        + f"; {missed} ray(s) met no wall"
    )
    report.note(
        "read across the hole's axis only: material beyond the hole's ends isn't counted as wall"
    )
    band = max(BAND, 2 * FINE[0] + 0.01 * least["wall"])
    if missed:
        differs.append(f"the sections find a closed hole, and {missed} ray(s) met no wall")
    elif not np.isfinite(rays["wall"]):
        differs.append("no ray through the hole's wall came out the far side")
    elif rays["wall"] < least["wall"] - band:
        differs.append(
            f"rays read a wall of {rays['wall']:.4f} mm, thinner than the sections' "
            f"{least['wall']:.4f}"
        )
    elif rays["wall"] > least["wall"] + band:
        differs.append(
            f"the sections' least wall is {least['wall']:.4f} mm, toward {least['toward']}, and "
            f"rays square to the hole's wall read {rays['wall']:.4f}: where it's least, the wall "
            "isn't square to the hole, as between two corners"
        )
    # A wall drawn at the limit reads a hair under it on the mesh's chords.
    if args.min_wall is not None and least["wall"] < args.min_wall - 2 * FINE[0]:
        problems.append(
            f"the least wall round the hole is {least['wall']:.3f} mm, toward {least['toward']}, "
            f"under the {args.min_wall} mm asked for"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict(
            "OK",
            f"the least wall round the hole is {least['wall']:.3f} mm, toward {least['toward']}"
            + (f", at least the {args.min_wall} mm asked for" if args.min_wall is not None else ""),
        )
