"""A flat layout of named pieces, read in 2D: each piece, whether it's one piece, its least width
and where, its sharp tips, what an inset leaves of it, and a picture with each piece labelled
(mqucifer/print-shop#61).

On `suncatcher-3`, 12 of the 35 calls that reasoned past 20,000 characters and repeated
themselves stalled on which piece or neck an inset makes, and which piece is which: it printed
bounding boxes to find out. This answers those questions about a layout the model's own script
writes, before any CAD.

**The file.** JSON with one key, `pieces`: each piece's name, and its outline as GeoJSON, which
`shapely.geometry.mapping(polygon)` writes, or as WKT. A piece may be a polygon with holes or a
multipolygon. Units are millimetres:

    {"pieces": {"frame": {"type": "Polygon", "coordinates": [...]}, "cell_1": "POLYGON ((...))"}}

**Tips.** Every polygon narrows to nothing at a corner sharper than a right angle, so a corner is
read as a corner: one sharper than `SHARP_DEG` is a **tip**, said with where it is and its angle,
from the outline's own points. Nothing is narrower at a tip than the nozzle can print, so a tip is
under any `--min-width`.

**Width.** A piece's width is the distance straight across it, from an edge to the edge that
faces back to within `NECK_DEG`: across a waist between two parts, a bar, an arm, a ring, or a
cell from side to side. Where the two sides run further apart than that, it's a corner, or a tip,
rounded or not, which an inset moves back and never cuts, so it isn't a width. The least width is
the narrowest such place.
- **First method:** points every `step` along every edge, and from each a ray square to its edge
  into the material, to where it first leaves the piece. It counts where the edge it meets faces
  back along it to within `NECK_DEG`.
- **Second method:** from each of the same points, the nearest other point whose edge faces
  back to within `NECK_DEG`, straight through the material: no ray, and no direction but the
  edges' own.

A narrow place within reach of a sharp tip is the tip's, by both methods. Across parallel sides
the two read the same. With the sides up to `NECK_DEG` apart, the nearest point reads as little
as cos(`NECK_DEG`) of the ray, and never more: further apart than that, and the points' spacing,
is DISAGREE. First versions measured other things, and disagreed: chords up to 37° from square
read an arm's rounded end at 0.8 of its width, the widest disc that fits read a wedge's rounded
tip by its cap, and a Delaunay edge joins only points that share an empty circle, which in a
cell with no two sides parallel aren't the nearest across.

**An inset** is Shapely's `buffer` by minus the distance, with mitred joins as a layout's pockets
are made, unless `--join round` says the true distance. Its parts are counted, and its least
width read the same two ways. Where it cuts the piece, or takes an arm away, is said: each
stretch of the piece narrower than twice the inset, by its narrowest and how far it runs.

**Parts.** Shapely's count, and as the second method, the picture's own pixels joined where they
touch, where every part, and every gap between two, is at least `PART_PX` pixels across.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry import shape as from_geojson
from shapely.geometry.polygon import orient

from . import colours
from .report import Report
from .shapes import NoAnswer

#: A corner sharper than this, in degrees, is a tip; a blunter one isn't a width at all. An
#: equilateral triangle's 60° corners are tips, a square's aren't.
SHARP_DEG = 75.0
#: A width is across a neck, where the two sides run within this many degrees of parallel: a
#: waist, a bar, an arm or a ring. Where they run further apart, the place is a corner or a tip,
#: which an inset moves back and never cuts.
NECK_DEG = 20.0
#: Points along every edge are this far apart at most, in mm, and at least `FINEST`; a layout's
#: longest side over `ALONG` sets it in between.
COARSEST = 0.05
FINEST = 0.005
ALONG = 2000
#: The most points along a piece's edges, so a large layout still answers in seconds.
MOST_POINTS = 60_000
#: The second method's bins of the way an edge faces, in degrees, and how many of the nearest
#: points in each bin it asks.
BIN_DEG = 10.0
NEAREST = 4
#: Where an inset cuts a piece, and the tips, said for at most this many places each.
SAID = 6
#: The picture's longest side, in pixels.
PICTURE_PX = 1024
#: A part, and a gap between two parts, the picture must have this many pixels across before
#: its pixels are a second count of the parts.
PART_PX = 3


def load(path: str) -> dict[str, Any]:
    """The pieces, by name, as Shapely shapes, from the file."""
    found = Path(path)
    if not found.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise NoAnswer(f"{path} isn't JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("pieces"), dict) or not data["pieces"]:
        raise NoAnswer(
            f'{path} holds {{"pieces": {{NAME: OUTLINE, ...}}}}: each outline GeoJSON, as '
            "`shapely.geometry.mapping(polygon)` writes it, or WKT, in mm"
        )
    pieces: dict[str, Any] = {}
    for name, given in data["pieces"].items():
        try:
            shape = shapely.from_wkt(given) if isinstance(given, str) else from_geojson(given)
        except Exception as exc:  # noqa: BLE001 - whatever it is, it doesn't read as an outline
            raise NoAnswer(f"piece {name!r} doesn't read as GeoJSON or WKT: {exc}") from exc
        pieces[str(name)] = shape
    return pieces


def areas(name: str, shape: Any, report: Report) -> Any:
    """A piece's area as one multipolygon, each ring turned with the material on its left: an
    invalid outline made valid, and said."""
    if not shape.is_valid:
        report.note(
            f"piece {name}: its outline isn't valid ({shapely.is_valid_reason(shape)}), so it's "
            "read as Shapely's `make_valid` makes it"
        )
        shape = shapely.make_valid(shape)
    found = _polygons(shape)
    if found is None:
        raise NoAnswer(f"piece {name} has no area: it's a {shape.geom_type}")
    return found


def _polygons(shape: Any) -> Any:
    """Every polygon with area in a shape, as one multipolygon, or None for none."""
    found: list[Any] = []
    todo = [shape]
    while todo:
        one = todo.pop()
        if one.geom_type == "Polygon":
            if one.area > 0:
                found.append(orient(one, 1.0))
        elif hasattr(one, "geoms"):
            todo.extend(one.geoms)
    return shapely.MultiPolygon(found) if found else None


def spacing(pieces: dict[str, Any]) -> float:
    """The points' spacing along the edges, from the layout's size and its edges' length."""
    x0, y0, x1, y1 = shapely.GeometryCollection(list(pieces.values())).bounds
    step = min(COARSEST, max(FINEST, max(x1 - x0, y1 - y0) / ALONG))
    longest = max(float(p.boundary.length) for p in pieces.values())
    return float(max(step, longest / MOST_POINTS))


def _part(xy: Any) -> list[float]:
    return [round(float(xy[0]), 3), round(float(xy[1]), 3)]


def _xy(at: list[float]) -> str:
    return f"({at[0]:.2f}, {at[1]:.2f})"


def _unit(v: np.ndarray) -> np.ndarray:
    size = np.linalg.norm(v, axis=1, keepdims=True)
    return np.asarray(np.divide(v, size, out=np.zeros_like(v), where=size > 0))


def corners(ring: np.ndarray) -> np.ndarray:
    """Each corner's angle in the material, in degrees, of a ring with the material on its left
    and no closing point."""
    ahead = _unit(np.roll(ring, -1, axis=0) - ring)
    behind = _unit(ring - np.roll(ring, 1, axis=0))
    turn = np.degrees(
        np.arctan2(
            behind[:, 0] * ahead[:, 1] - behind[:, 1] * ahead[:, 0],
            (behind * ahead).sum(axis=1),
        )
    )
    return np.asarray(180.0 - turn)


class Edges:
    """Points along every edge of one area, each with its direction, and its sharp tips."""

    def __init__(self, area: Any, step: float) -> None:
        points, tangents = [], []
        tips_xy, tips_deg = [], []
        for poly in area.geoms:
            for ring in (poly.exterior, *poly.interiors):
                line = shapely.LineString(ring.coords)
                length = float(line.length)
                n = max(12, math.ceil(length / step))
                at = np.linspace(0.0, length, n, endpoint=False)
                vertices = np.asarray(ring.coords)[:-1]
                angle = corners(vertices)
                sharp = angle < SHARP_DEG
                tips_xy.append(vertices[sharp])
                tips_deg.append(angle[sharp])
                # Every corner is a point too, so a corner's own direction is read; a point a
                # hair from one would turn it to its own.
                corner_at = np.sort(
                    np.asarray(shapely.line_locate_point(line, shapely.points(vertices)))
                )
                near = np.searchsorted(corner_at, at)
                ahead = corner_at[np.minimum(near, len(corner_at) - 1)] - at
                behind = at - corner_at[np.maximum(near - 1, 0)]
                wrap = np.minimum(at - corner_at[0] + length, corner_at[-1] + length - at)
                gap = np.minimum(np.minimum(np.abs(ahead), np.abs(behind)), np.abs(wrap))
                at = np.unique(np.concatenate([at[gap > 0.25 * step], corner_at]))
                xy = shapely.get_coordinates(shapely.line_interpolate_point(line, at))
                way = _unit(
                    _unit(np.roll(xy, -1, axis=0) - xy) + _unit(xy - np.roll(xy, 1, axis=0))
                )
                points.append(xy)
                tangents.append(way)
        self.step = step
        self.xy = np.concatenate(points)
        self.tangent = np.concatenate(tangents)
        # Every ring is turned so the material is on its left (`orient`).
        self.inward = np.stack([-self.tangent[:, 1], self.tangent[:, 0]], axis=1)
        self.tips_xy = np.concatenate(tips_xy) if tips_xy else np.zeros((0, 2))
        self.tips_deg = np.concatenate(tips_deg) if tips_deg else np.zeros(0)

    def at_tip(self, at: np.ndarray, width: float) -> bool:
        """Whether a narrow place is a tip's: a disc that wide in that corner, or a ray across it,
        has its middle within this reach of the corner's point."""
        if not len(self.tips_xy):
            return False
        far = np.linalg.norm(self.tips_xy - at, axis=1)
        reach = width / np.sin(np.radians(self.tips_deg) / 2) + 2.0 * self.step
        return bool((far <= reach).any())


def facing(area: Any, edges: Edges) -> list[dict[str, Any]]:
    """The second method: from each point, the nearest point whose edge faces back to within
    `NECK_DEG`, straight through the material. Shortest first. The points are kept in bins by
    the way their edge faces, `BIN_DEG` wide, and each point asks only the bins facing it."""
    from scipy.spatial import cKDTree

    facing_way = np.degrees(np.arctan2(edges.inward[:, 1], edges.inward[:, 0])) % 360.0
    bins = np.floor(facing_way / BIN_DEG).astype(int)
    count = int(round(360.0 / BIN_DEG))
    trees = {b: (cKDTree(edges.xy[bins == b]), np.flatnonzero(bins == b)) for b in set(bins)}
    reach = int(math.ceil(NECK_DEG / BIN_DEG)) + 1
    least = -math.cos(math.radians(NECK_DEG))
    best_q = np.full((len(edges.xy), NEAREST * (2 * reach + 1)), -1)
    best_d = np.full(best_q.shape, np.inf)
    column = 0
    for shift in range(-reach, reach + 1):
        for b, (tree, members) in trees.items():
            mine = np.flatnonzero((bins + count // 2 + shift) % count == b)
            if not len(mine):
                continue
            k = min(NEAREST, len(members))
            far, near = tree.query(edges.xy[mine], k=k)
            far, near = far.reshape(len(mine), k), near.reshape(len(mine), k)
            best_q[mine, column : column + k] = members[near]
            best_d[mine, column : column + k] = far
        column += NEAREST
    i = np.repeat(np.arange(len(edges.xy)), best_q.shape[1])
    j = best_q.ravel()
    far = best_d.ravel()
    keep = (j >= 0) & np.isfinite(far) & (far > 0)
    i, j, far = i[keep], j[keep], far[keep]
    keep = (edges.inward[i] * edges.inward[j]).sum(axis=1) <= least
    i, j, far = i[keep], j[keep], far[keep]
    order = np.lexsort((far, i))
    i, j, far = i[order], j[order], far[order]
    middle = (edges.xy[i] + edges.xy[j]) / 2
    shapely.prepare(area)
    inside = shapely.contains_xy(area, middle[:, 0], middle[:, 1])
    held = area.buffer(edges.step * 0.05)
    shapely.prepare(held)
    lines = shapely.linestrings(np.stack([edges.xy[i[inside]], edges.xy[j[inside]]], axis=1))
    inside[np.flatnonzero(inside)] = shapely.covers(held, lines)
    i, j, far = i[inside], j[inside], far[inside]
    # Each point's nearest that's through the material.
    first = np.unique(i, return_index=True)[1]
    i, j, far = i[first], j[first], far[first]
    return [
        {
            "width": float(far[k]),
            "at": (edges.xy[i[k]] + edges.xy[j[k]]) / 2,
            "from": edges.xy[i[k]],
            "to": edges.xy[j[k]],
        }  # fmt: skip
        for k in np.argsort(far, kind="stable")
    ]


def rays(area: Any, edges: Edges) -> list[dict[str, Any]]:
    """The first method: from each point, a ray square to its edge into the material, to where
    it first leaves, counted where the edge it meets faces back along it to within `NECK_DEG`.
    Shortest first."""
    from scipy.spatial import cKDTree

    x0, y0, x1, y1 = area.bounds
    reach = 2.0 * math.hypot(x1 - x0, y1 - y0) + 1.0
    nudge = edges.step * 1e-3
    start = edges.xy + edges.inward * nudge
    lines = shapely.linestrings(np.stack([start, start + edges.inward * reach], axis=1))
    met = shapely.intersection(lines, area.boundary)
    xy, which = shapely.get_coordinates(met, return_index=True)
    if not len(xy):
        return []
    far = np.linalg.norm(xy - start[which], axis=1)
    # Each ray's first meeting: its nearest point of all it met.
    order = np.lexsort((far, which))
    first = order[np.unique(which[order], return_index=True)[1]]
    k, to, far = which[first], xy[first], far[first]
    _, near = cKDTree(edges.xy).query(to)
    t, way = edges.tangent[near], edges.inward[k]
    square = np.abs(way[:, 0] * t[:, 1] - way[:, 1] * t[:, 0]) >= math.cos(math.radians(NECK_DEG))
    found = [
        {
            "width": float(far[n]) + nudge,
            "at": (edges.xy[k[n]] + to[n]) / 2,
            "from": edges.xy[k[n]],
            "to": to[n],
        }  # fmt: skip
        for n in np.flatnonzero(square)
    ]
    found.sort(key=lambda r: r["width"])
    return found


def narrowest(area: Any, step: float) -> dict[str, Any]:
    """An area's least width by both methods, with where, its narrow places by rays, and its
    tips."""
    edges = Edges(area, step)
    said: dict[str, Any] = {}
    for method, rows in (("rays", rays(area, edges)), ("facing", facing(area, edges))):
        necks = [r for r in rows if not edges.at_tip(r["at"], r["width"])]
        said[method] = (
            {
                "width_mm": round(necks[0]["width"], 4),
                "at": _part(necks[0]["at"]),
                "from": _part(necks[0]["from"]),
                "to": _part(necks[0]["to"]),
            }
            if necks
            else None
        )
        if method == "rays":
            said["necks"] = necks
    said["tips"] = [
        {"at": _part(xy), "angle_deg": round(float(deg), 1)}
        for xy, deg in sorted(
            zip(edges.tips_xy, edges.tips_deg, strict=True), key=lambda t: float(t[1])
        )
    ]
    return said


def tolerance(step: float, width: float) -> float:
    """How far the two methods may differ. A ray square to one side and the nearest point facing
    back read the same across parallel sides. With the sides up to `NECK_DEG` apart, the nearest
    point is square to the far side, not this one, and reads as little as cos(`NECK_DEG`) of the
    ray and never more; each is off by the points' spacing."""
    return 2.0 * step + 1e-3 + width * (1.0 - math.cos(math.radians(NECK_DEG)))


def differ(said: dict[str, Any], step: float) -> str | None:
    """How the two methods' least widths differ, if by more than they can."""
    one, two = said["rays"], said["facing"]
    if one is None and two is None:
        return None
    if one is None or two is None:
        return f"only the {'rays' if one is not None else 'nearest facing points'} found a width"
    short = one["width_mm"] - two["width_mm"]
    if short < -2.0 * step - 1e-3 or short > tolerance(step, one["width_mm"]):
        return (
            f"{one['width_mm']:.3f} mm by rays at {_xy(one['at'])} and {two['width_mm']:.3f} "
            f"mm by nearest facing points at {_xy(two['at'])}"
        )
    return None


def cuts(said: dict[str, Any], inset: float, step: float) -> list[dict[str, Any]]:
    """Where an inset cuts a piece: its places narrower than twice the inset, each stretch of them
    one place, said by its narrowest and how far it runs. A bar is narrow all along: one place."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    narrow = [r for r in said["necks"] if r["width"] < 2.0 * inset] if inset > 0 else []
    if not narrow:
        return []
    middle = np.array([r["at"] for r in narrow])
    pairs = np.array(sorted(cKDTree(middle).query_pairs(4.0 * step)), dtype=int).reshape(-1, 2)
    joined = coo_matrix(
        (np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(narrow), len(narrow))
    )
    _, group = connected_components(joined, directed=False)
    found: list[dict[str, Any]] = []
    # In order of each stretch's narrowest, as `narrow` is narrowest first.
    for g in dict.fromkeys(int(k) for k in group):
        members = np.flatnonzero(group == g)
        found.append(
            {
                "width_mm": round(narrow[int(members[0])]["width"], 4),
                "at": _part(middle[int(members[0])]),
                "runs_mm": round(float(np.ptp(middle[members], axis=0).max()), 3),
            }
        )
        if len(found) >= SAID:
            break
    return found


def parts(area: Any) -> list[dict[str, Any]]:
    """Each part of an area, largest first: where it is, how big, and its holes."""
    found = []
    for poly in sorted(area.geoms, key=lambda p: -p.area):
        x0, y0, x1, y1 = poly.bounds
        found.append(
            {
                "area_mm2": round(float(poly.area), 3),
                "inside_at": _part(shapely.get_coordinates(poly.representative_point())[0]),
                "box": [round(v, 3) for v in (x0, y0, x1, y1)],
                "holes": len(poly.interiors),
            }
        )
    return found


def _parts_said(found: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{p['area_mm2']:g} mm² round {_xy(p['inside_at'])}, x {p['box'][0]:g} to {p['box'][2]:g}"
        f", y {p['box'][1]:g} to {p['box'][3]:g}"
        + (f", {p['holes']} hole(s)" if p["holes"] else "")
        for p in found
    )


def _neck_said(found: dict[str, Any] | None) -> str:
    if found is None:
        return "none: no two of its edges face each other, away from its tips"
    return (
        f"{found['width_mm']:.3f} mm at {_xy(found['at'])}, across from {_xy(found['from'])} to "
        f"{_xy(found['to'])}"
    )


def _tips_said(tips: list[dict[str, Any]]) -> str:
    shown = "; ".join(f"{t['angle_deg']:g}° at {_xy(t['at'])}" for t in tips[:SAID])
    more = f", and {len(tips) - SAID} more" if len(tips) > SAID else ""
    return f"{len(tips)} sharp tip(s), sharper than {SHARP_DEG:g}°, sharpest first: {shown}{more}"


def inset_of(area: Any, inset: float, join: str) -> Any:
    """What an inset leaves of an area, as a multipolygon, or None for nothing."""
    return _polygons(area.buffer(-inset, join_style=join))


def run(args: Any, report: Report) -> None:
    given = load(args.file)
    pieces = {name: areas(name, shape, report) for name, shape in given.items()}
    step = spacing(pieces)
    report.fact("pieces_count", len(pieces), how="named in the file")
    report.fact("step_mm", round(step, 4), how="points along every edge, this far apart")
    report.line(f"insets with {args.join} joins" if args.insets else "no inset asked")
    rows: dict[str, Any] = {}
    differs: list[str] = []
    for name, area in pieces.items():
        rows[name] = _piece(name, area, args, step, report, differs)
    report.numbers["pieces"] = rows
    _overlaps(pieces, report)
    widths = [(r["least_width_mm"], n) for n, r in rows.items() if r["least_width_mm"] is not None]
    if widths:
        least, piece = min(widths)
        report.fact("least_width_mm", least, "mm", f"piece {piece}, straight across, by rays")
        report.numbers["least_width_piece"] = piece
    report.numbers["tips_count"] = sum(len(r["tips"]) for r in rows.values())
    counted = _picture(pieces, rows, args, report)
    for name, (by_pixels, readable) in counted.items():
        shown = rows[name]["parts_count"]
        if readable and by_pixels != shown:
            differs.append(f"{name}: {shown} part(s) by Shapely, {by_pixels} by the picture")
        second = rows[name]["width_by_facing"]
        report.second(
            f"{name}: {by_pixels} part(s) by the picture's pixels"
            + ("" if readable else ", too close together at its scale to count")
            + "; least width by the nearest points facing each other "
            + (f"{second['width_mm']:.3f} mm at {_xy(second['at'])}" if second else "none")
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    if args.min_width is not None:
        under = [
            f"{n} is {r['least_width_mm']:.3f} mm across at {_xy(r['least_width_at'])}"
            for n, r in rows.items()
            if r["least_width_mm"] is not None and r["least_width_mm"] < args.min_width
        ] + [
            f"{n} has a sharp tip, {t['angle_deg']:g}°, at {_xy(t['at'])}, which narrows to nothing"
            for n, r in rows.items()
            for t in r["tips"]
        ]
        if under:
            report.verdict("FAIL", "; ".join(under) + f": under {args.min_width:g} mm")
            return
    report.verdict("OK", f"{len(pieces)} piece(s) read, by rays and by nearest facing points")


def _piece(
    name: str, area: Any, args: Any, step: float, report: Report, differs: list[str]
) -> dict[str, Any]:
    """One piece's lines and numbers: its parts, its least width, its tips and each inset."""
    said = narrowest(area, step)
    found = parts(area)
    neck = said["rays"]
    row: dict[str, Any] = {
        "parts_count": len(found),
        "parts": found,
        "least_width_mm": neck["width_mm"] if neck else None,
        "least_width_at": neck["at"] if neck else None,
        "least_width_from": neck["from"] if neck else None,
        "least_width_to": neck["to"] if neck else None,
        "width_by_facing": said["facing"],
        "tips": said["tips"],
        "insets": {},
    }
    one = "one piece" if len(found) == 1 else f"NOT one piece: {len(found)} parts, largest first"
    report.line(f"piece {name}: {one}: {_parts_said(found)}")
    report.line(f"  least width: {_neck_said(neck)}")
    if said["tips"]:
        report.line(f"  {_tips_said(said['tips'])}")
    gone = differ(said, step)
    if gone:
        differs.append(f"{name}'s least width: {gone}")
    for inset in args.insets:
        kept = inset_of(area, inset, args.join)
        key = f"{inset:g}"
        if kept is None:
            row["insets"][key] = {"parts_count": 0, "parts": [], "least_width_mm": None}
            report.line(f"  inset {inset:g} mm: nothing is left")
            continue
        after = narrowest(kept, step)
        cut = cuts(said, inset, step)
        row["insets"][key] = {
            "parts_count": len(kept.geoms),
            "parts": parts(kept),
            "least_width_mm": after["rays"]["width_mm"] if after["rays"] else None,
            "least_width_at": after["rays"]["at"] if after["rays"] else None,
            "cut_at": cut,
        }
        report.line(f"  inset {inset:g} mm: {len(kept.geoms)} part(s): {_parts_said(parts(kept))}")
        report.line(f"    its least width: {_neck_said(after['rays'])}")
        if cut:
            report.line(
                "    it cuts the piece in two, or takes an arm away, where it's narrower than "
                "twice the inset: "
                + "; ".join(
                    f"{c['width_mm']:.3f} mm at {_xy(c['at'])}, narrow for {c['runs_mm']:g} mm"
                    for c in cut
                )
            )
        gone = differ(after, step)
        if gone:
            differs.append(f"{name} inset {inset:g} mm, its least width: {gone}")
    return row


def _overlaps(pieces: dict[str, Any], report: Report) -> None:
    """Pieces that take the same space: a layout's cells share their edges and never overlap."""
    names = list(pieces)
    found: list[dict[str, Any]] = []
    for k, a in enumerate(names):
        for b in names[k + 1 :]:
            shared = float(pieces[a].intersection(pieces[b]).area)
            if shared > 1e-6:
                found.append({"pieces": [a, b], "area_mm2": round(shared, 4)})
    report.numbers["overlaps"] = found
    if found:
        report.line(
            "overlapping: "
            + "; ".join(
                f"{o['pieces'][0]} and {o['pieces'][1]} share {o['area_mm2']:g} mm²" for o in found
            )
        )


def _picture(
    pieces: dict[str, Any], rows: dict[str, Any], args: Any, report: Report
) -> dict[str, tuple[int, bool]]:
    """Each piece in its colour, labelled by name on each part, its least width in red, its
    tips ringed, and each inset's outline in black. Returns each piece's parts as its pixels
    count them, and whether they're far enough apart at this scale to count."""
    from PIL import Image, ImageDraw, ImageFont
    from scipy import ndimage

    x0, y0, x1, y1 = shapely.GeometryCollection(list(pieces.values())).bounds
    margin, key_px = 24, 20
    scale = (PICTURE_PX - 2 * margin) / max(x1 - x0, y1 - y0, 1e-6)
    width = int((x1 - x0) * scale) + 2 * margin
    height = int((y1 - y0) * scale) + 2 * margin + key_px

    def px(xy: Any) -> list[tuple[float, float]]:
        return [
            (margin + (x - x0) * scale, height - key_px - margin - (y - y0) * scale) for x, y in xy
        ]

    picture = Image.new("RGB", (width, height), "white")
    tints = colours.distinct(len(pieces), [(255, 255, 255), (0, 0, 0), (220, 30, 30)])
    counted: dict[str, tuple[int, bool]] = {}
    for (name, area), tint in zip(pieces.items(), tints, strict=True):
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        for poly in area.geoms:
            draw.polygon(px(poly.exterior.coords), fill=255)
            for ring in poly.interiors:
                draw.polygon(px(ring.coords), fill=0)
        picture.paste(Image.new("RGB", (width, height), tint), (0, 0), mask)
        by_pixels = int(ndimage.label(np.asarray(mask) > 0, structure=np.ones((3, 3)))[1])
        polys = list(area.geoms)
        gaps = [float(a.distance(b)) * scale for k, a in enumerate(polys) for b in polys[k + 1 :]]
        sizes = [min(p.bounds[2] - p.bounds[0], p.bounds[3] - p.bounds[1]) * scale for p in polys]
        counted[name] = (by_pixels, min([*gaps, *sizes]) >= PART_PX)
    draw = ImageDraw.Draw(picture)
    for name, area in pieces.items():
        for poly in area.geoms:
            for ring in (poly.exterior, *poly.interiors):
                draw.line(px(ring.coords), fill=(40, 40, 40), width=1)
        for inset in args.insets:
            kept = inset_of(area, inset, args.join)
            for poly in kept.geoms if kept is not None else []:
                for ring in (poly.exterior, *poly.interiors):
                    draw.line(px(ring.coords), fill=(0, 0, 0), width=1)
        row = rows[name]
        if row["least_width_from"]:
            ends = px([row["least_width_from"], row["least_width_to"]])
            draw.line(ends, fill=(220, 30, 30), width=3)
        for tip in row["tips"]:
            (x, y) = px([tip["at"]])[0]
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), outline=(220, 30, 30), width=2)
    font = ImageFont.load_default(size=14)
    for name, area in pieces.items():
        polys = sorted(area.geoms, key=lambda p: -p.area)
        for k, poly in enumerate(polys, start=1):
            (x, y) = px(shapely.get_coordinates(poly.representative_point()))[0]
            label = f"{name} {k}/{len(polys)}" if len(polys) > 1 else name
            draw.text(
                (x, y), label, fill=(0, 0, 0), font=font, anchor="mm",
                stroke_width=2, stroke_fill=(255, 255, 255),
            )  # fmt: skip
    key = "red: each piece's least width; red rings: sharp tips" + (
        f"; black: inset {', '.join(f'{d:g}' for d in args.insets)} mm" if args.insets else ""
    )
    draw.text((margin, height - key_px + 2), key, fill=(0, 0, 0), font=font)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{Path(args.file).stem}-layout.png"
    picture.save(target)
    report.wrote(str(target))
    report.numbers["picture"] = str(target)
    return counted
