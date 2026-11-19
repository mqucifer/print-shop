"""The fixes that work on one place of a piece map and keep the rest
(mqucifer/print-shop#172).

`pieces trace` reads a whole picture at one darkness and one palette, and the fixes in
`pieces_fix.py` each work on a piece or a line. Nothing worked on an **area**. On a traced scene
(2026-11-18) the default trace read one dark tree shadow as a patch of lead 8.4 mm wide. To
lose it the run traced the whole picture again at `--dark 18`: 41 of the picture's lines broke
into dead ends, and dropping them took away the trees, the river's course and a band of hill.
Most of the map was right, and the only lever changed all of it. So:

- **`retrace MAP --box X0,Y0,X1,Y1`**: the picture traced again inside the box, at its own
  `--dark` and `--palette`, and spliced into the map. The box is moved in to the picture's
  pixels. A piece that reaches out of the box keeps everything it has outside, point for
  point, and joins what's found inside wherever the two share a stretch of the box's edge:
  so a line that crosses the edge stays where it was outside, and runs on into the line
  found inside. A piece wholly inside is replaced by what's found.
- **`fill MAP --at X,Y`**: a patch of lead becomes glass, shared among the pieces round it.
  The patch is the lead there wider than a line and a half. Each piece takes the part
  nearer to it than to any other, less half a line along each border between two shares: so
  a line as wide as the map's runs down the middle between each two pieces, where the lines
  that met the patch run on to each other. Each border is drawn as a line, to the map's own
  detail, and a share's edge is its side. A scrap of lead beside the patch, too short to be
  a line, is glass in the piece round it. Only lead becomes glass.
- **`paint MAP ID COLOUR`**: one piece takes another of the map's colours, or a new one with
  its RGB. Nothing moves.
- **`move-line MAP A B --line ...`**: the line between two neighbours is drawn again along
  new points, as wide as it was. The two pieces and their line are one shape, and the new
  line cuts it in two from edge to edge, so every line that met the old one still meets
  lead. Each piece keeps its number and its colour, and the glass each side of the new line.

**What a fix made** (mqucifer/print-shop#174). On an eval a fill's shares followed their
borders point by point, a tenth of a mm apart: one piece went from 62 outline points to 268,
seven scraps of lead were shut in the pieces, and `pieces build` refused the map the fill had
called OK. So a retrace, a fill and a moved line each say every changed piece's outline
points before and after and whether it builds, by the build's own fit
(`pieces_build.pane_refused`), and a piece that built before and doesn't after is a FAIL.

**Second methods.** A retrace: the new map drawn back as pixels at the picture's scale, over
the patches found in the box, and the two maps' pixels outside the box, which are the same.
A fill: each pixel that was lead and is glass, against the piece nearest it in the map
before, by the pixels' own distances. A paint: the picture's colour over the piece read now,
against the colour the trace kept. And every fix's own: the glass added and taken by the two
maps' pixels, and every piece not named the same point for point.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import shapely

from . import colours, layout, piecemap
from .piecemap import Map, Piece
from .pieces_check import DEAD_END, LINE_SHARE, Limits, flag_piece, gaps, piece_row
from .pieces_fix import (
    CRUMB_MM2,
    GRID,
    NEAR,
    WIDE_GAP,
    Made,
    _finish,
    _joined,
    _one,
    _swap,
    cut,
    merged,
    reader,
)
from .pieces_picture import DEFAULT_DARK, Picture, picture_of
from .pieces_trace import (
    CORNER_PX,
    HAIR_MM,
    SIMPLIFY_PX,
    SPECK_PX,
    _nearest,
    _outline,
    _parted,
    _smooth,
    lead_of,
    lines_of,
    palette_of,
    unspecked,
)
from .report import NoAnswer, Report

#: A box is this many of the picture's pixels a side or more: under it there's no outline to
#: read a corner on.
LEAST_BOX_PX = 4 * CORNER_PX
#: A piece outside the box and a patch inside it are one piece where they share this many
#: pixels of the box's edge or more: less is a corner's touch.
JOIN_PX = 2.0
#: Two shapes share a stretch of the box's edge where they're both within this of it, in mm.
EDGE_TOUCH = 1e-4
#: A patch is lead wider than this many of the map's usual lead: where three lines meet it's
#: up to one and a half (`pieces_check.MAX_LEAD`).
PATCH = 1.5
#: A filled patch's shares are worked out from points this far apart along the pieces round
#: it, in mm, and never more than an eighth of a lead.
SHARE_STEP = 0.1
#: A border's line is drawn round each bend of its path with this many points a quarter turn.
ROUND = 4
#: A scrap of lead a fill left in a piece is a gap in it narrower than this share of a line,
#: of this many mm² or more: smaller is the join's own rounding. And it's glass only where
#: no other piece is within this share of a line of it.
SCRAP_REACH = 0.45
SCRAP_MM2 = 1e-4
SCRAP_CLEAR = 0.75
#: A filled patch's new glass is the nearest piece's in this share of its pixels, or more.
NEAREST_PCT = 98.0
#: Two points of a share's edge nearer than this, in mm, are one: where a border's round end
#: meets the patch's edge they come a few ten-thousandths apart, and no curve fits through.
SAME_POINT = 0.01
#: The rules a moved line may not break on either piece: a piece under the map's limits.
UNDER = ("sliver", "small", "neck")
#: Lines that end at a box's edge said one by one, at most.
ENDS_SAID = 8


# The box


def _window(picture: Picture, box: Any) -> tuple[int, int, int, int]:
    """The box on the picture's pixels, moved in to their edges: rows from the top, columns
    from the left."""
    height, width = picture.size
    mm = picture.mm
    x0, x1 = sorted((float(box[0]), float(box[2])))
    y0, y1 = sorted((float(box[1]), float(box[3])))
    # A box a map's history kept is on the pixels to the file's last place: it's read as
    # the same pixels, and not as a pixel further in.
    slack = 2.0 * 10.0**-piecemap.PLACES / mm
    c0 = max(0, math.ceil(x0 / mm - slack))
    c1 = min(width, math.floor(x1 / mm + slack))
    r0 = max(0, math.ceil(height - y1 / mm - slack))
    r1 = min(height, math.floor(height - y0 / mm + slack))
    if c1 - c0 < LEAST_BOX_PX or r1 - r0 < LEAST_BOX_PX:
        raise NoAnswer(
            f"the box is {max(c1 - c0, 0)} by {max(r1 - r0, 0)} of the picture's pixels inside "
            f"the panel, and a retrace needs {LEAST_BOX_PX} each way, "
            f"{LEAST_BOX_PX * mm:.1f} mm: --box X0,Y0,X1,Y1 is two corners in the map's mm, "
            f"and the panel is {width * mm:g} by {height * mm:g} mm"
        )
    return r0, r1, c0, c1


def _found_in(
    picture: Picture, window: tuple[int, int, int, int], dark: int, lead_mm: float
) -> dict[str, Any]:
    """The picture traced again inside a window, as `pieces trace` reads it: each patch of
    colour as a piece's outline, cut at the window's edge, in mm. The lead is read on the
    window and a margin round it, so an outline that crosses the edge is smoothed as it runs
    and then cut: it ends on the edge, with no corner of the cut's own. With the patches'
    pixels in the window, for the second check."""
    from scipy import ndimage

    r0, r1, c0, c1 = window
    height, width = picture.size
    mm = picture.mm
    close = max(3, 2 * int(HAIR_MM / mm / 2.0) + 1)
    pad = max(4 * CORNER_PX, 2 * close)
    top, left = max(0, r0 - pad), max(0, c0 - pad)
    bottom, right = min(height, r1 + pad), min(width, c1 + pad)
    rgb = picture.rgb[top:bottom, left:right]
    lines, _ = lead_of(rgb.max(axis=2) < dark, mm)
    labels, count = ndimage.label(~lines)
    sizes = np.atleast_1d(ndimage.sum(~lines, labels, np.arange(1, count + 1)))
    box = shapely.box(c0 * mm, (height - r1) * mm, c1 * mm, (height - r0) * mm)
    inner = (slice(r0 - top, r1 - top), slice(c0 - left, c1 - left))
    glass = np.zeros((r1 - r0, c1 - c0), dtype=bool)
    pieces: list[Piece] = []
    raw: list[Any] = []
    specks = 0

    def in_mm(shape: Any) -> Any:
        return shapely.transform(
            shape, lambda p: np.column_stack([p[:, 0], height - p[:, 1]]) * mm
        )

    for k, found in enumerate(ndimage.find_objects(labels), start=1):
        if found is None or not (labels[inner] == k).any():
            continue
        if sizes[k - 1] < SPECK_PX:
            specks += 1
            continue
        mask = labels[found] == k
        pixels = _outline(mask, top + found[0].start, left + found[1].start)
        if pixels is None:
            continue
        glass |= labels[inner] == k
        coarse = piecemap.polygons(in_mm(pixels).intersection(box))
        for part in piecemap.polygons(in_mm(_smooth(pixels)).intersection(box)):
            if part.area <= CRUMB_MM2 or not coarse:
                continue
            kept = shapely.set_precision(part, 10.0**-piecemap.PLACES)
            mine = max(coarse, key=lambda one: one.intersection(part).area)
            if kept.geom_type != "Polygon" or not kept.is_valid or kept.is_empty:
                part = mine
            pieces.append(Piece(0, "", part, paint=None))
            raw.append(mine)
    parted = _parted(pieces, raw, mm)
    filled = unspecked(pieces, lead_mm)
    return {
        "box": box,
        "parts": [p.shape for p in pieces],
        "glass": glass,
        "specks": specks,
        "specks_filled": filled,
        "parted": len(parted),
    }


def _strokes(lines: Any, least: float = 0.0) -> list[Any]:
    """Each stretch of some lines, joined end to end, longer than `least`."""
    if lines.is_empty:
        return []
    joined = shapely.line_merge(lines) if lines.geom_type != "LineString" else lines
    parts = list(joined.geoms) if hasattr(joined, "geoms") else [joined]
    return [p for p in parts if p.geom_type == "LineString" and p.length > least]


def _on_edge(shape: Any, band: Any) -> Any:
    """The stretches of a shape's edge that lie along the box's edge."""
    return shape.boundary.intersection(band)


def _groups(count: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """`count` things in the groups `pairs` join them into."""
    root = list(range(count))

    def top(k: int) -> int:
        while root[k] != k:
            root[k] = root[root[k]]
            k = root[k]
        return k

    for a, b in pairs:
        root[top(a)] = top(b)
    found: dict[int, list[int]] = {}
    for k in range(count):
        found.setdefault(top(k), []).append(k)
    return list(found.values())


def _edge_read(
    found: Map, box: Any, outside: list[Any], inside: list[Any], mm: float
) -> dict[str, Any]:
    """The box's edge read on both sides: where the lead outside it meets the lead found
    inside, a line that crosses the edge; where only one side has lead, a line that ends at
    the edge; and the widest step where the two sides of a crossing line don't quite meet."""
    lead = found.lead_mm
    edge = box.exterior.difference(found.panel.exterior.buffer(piecemap.PAST_EDGE))
    if edge.is_empty:
        return {"crossing": 0, "ends": [], "step_mm": 0.0}

    def lead_along(shapes: list[Any]) -> Any:
        if not shapes:
            return edge
        return edge.difference(shapely.union_all(shapes).buffer(EDGE_TOUCH))

    theirs, mine = lead_along(outside), lead_along(inside)
    least = LINE_SHARE * lead
    reach = 2.0 * mm
    crossing = 0
    ends: list[dict[str, Any]] = []

    def apart(part: Any, other: Any) -> bool:
        """No lead on the other side of the edge here: a distance to nothing is no number."""
        return bool(other.is_empty or part.distance(other) > reach)

    for part in _strokes(mine, least):
        if not apart(part, theirs):
            crossing += 1
        else:
            at = shapely.get_coordinates(part.interpolate(0.5, normalized=True))[0]
            ends.append({"at": piecemap.xy(at), "width_mm": round(part.length, 3), "of": "inside"})
    for part in _strokes(theirs, least):
        if apart(part, mine):
            at = shapely.get_coordinates(part.interpolate(0.5, normalized=True))[0]
            ends.append(
                {"at": piecemap.xy(at), "width_mm": round(part.length, 3), "of": "outside"}
            )
    both = mine.intersection(theirs)
    steps = [
        part.length
        for part in _strokes(mine.symmetric_difference(theirs))
        if not both.is_empty and part.distance(both) <= EDGE_TOUCH and part.length < least
    ]
    return {"crossing": crossing, "ends": ends, "step_mm": max(steps, default=0.0)}


def _unstepped(shape: Any, box: Any, least: float) -> Any:
    """A piece joined across the box's edge, less the step where its outline crosses it.

    What a piece keeps outside the box and what's found inside meet along the edge, and where
    the two don't quite agree on where a line's side is, the joined outline runs along the
    edge for that far, a step under `least` long with a square turn at each end: as short as
    a ten-thousandth of a mm, which no curve can be fitted through. The point of the step
    that the outside's side runs to stays, and the inside's goes, so the outline found
    inside runs to where the outside's crosses the edge. Nothing outside the box moves."""
    edge = box.exterior
    within = box.buffer(EDGE_TOUCH)
    rings = []
    changed = False
    for ring in (shape.exterior, *shape.interiors):
        points = shapely.get_coordinates(ring)[:-1]
        count = len(points)
        spots = shapely.points(points)
        on = shapely.distance(spots, edge) <= EDGE_TOUCH
        out = ~shapely.covers(within, spots)
        drop: set[int] = set()
        for k in range(count):
            j = (k + 1) % count
            if not (on[k] and on[j]) or math.dist(points[k], points[j]) >= least:
                continue
            before, after = (k - 1) % count, (j + 1) % count
            if out[before] and not out[after]:
                drop.add(j)
            elif out[after] and not out[before]:
                drop.add(k)
        if drop and count - len(drop) >= 3:
            points = points[[k for k in range(count) if k not in drop]]
            changed = True
        rings.append(points)
    if not changed:
        return shape
    made = shapely.Polygon(rings[0], rings[1:])
    if made.is_valid and made.geom_type == "Polygon" and not made.is_empty:
        return shapely.geometry.polygon.orient(made, 1.0)
    return shape


def _dead_ends_in(found: Map, box: Any) -> int:
    """How many dead ends a map has in a box: those of the pieces that reach into it."""
    least = DEAD_END * found.lead_mm
    return sum(
        1
        for piece in found.pieces
        if piece.shape.intersects(box)
        for row in gaps(found, piece)
        if (row["length_mm"] >= least or row["kind"] == "island") and row["shape"].intersects(box)
    )


def _held(found: Map, box: Any) -> list[Piece]:
    return [p for p in found.pieces if p.shape.intersection(box).area > 1e-9]


def _palette(found: Map, text: str | None) -> dict[str, tuple[int, int, int]]:
    """The colours a piece found in the box may be: `--palette`, or the map's own."""
    given = palette_of(text)
    if given:
        return given
    own = {
        name: (int(entry["rgb"][0]), int(entry["rgb"][1]), int(entry["rgb"][2]))
        for name, entry in found.colours.items()
        if isinstance(entry.get("rgb"), list) and len(entry["rgb"]) == 3
    }
    return own or dict(colours.NAMED)


def do_retrace(
    found: Map, picture: Picture, box: Any, dark: int | None, palette: str | None
) -> Made:
    if dark is None:
        said = found.source.get("dark_under")
        dark = said if isinstance(said, int) and not isinstance(said, bool) else DEFAULT_DARK
    if not 0 < dark <= 255:
        raise NoAnswer("--dark is a whole number, 1 to 255: a pixel is lead where it's under it")
    named = _palette(found, palette)
    window = _window(picture, box)
    mm = picture.mm
    traced = _found_in(picture, window, dark, found.lead_mm)
    zone = traced["box"]
    held = _held(found, zone)
    # What each piece keeps: all of it that lies outside the box, as it was.
    kept: list[tuple[Any, int]] = [
        (part, piece.id)
        for piece in held
        for part in piecemap.polygons(piece.shape.difference(zone))
    ]
    parts: list[Any] = traced["parts"]
    band = zone.exterior.buffer(EDGE_TOUCH)
    feet = [_on_edge(shape, band) for shape in [*parts, *[shape for shape, _ in kept]]]
    pairs = []
    for a in range(len(parts)):
        for b in range(len(kept)):
            if feet[a].is_empty or feet[len(parts) + b].is_empty:
                continue
            shared = feet[a].intersection(feet[len(parts) + b].buffer(2.0 * EDGE_TOUCH))
            if shared.length >= JOIN_PX * mm:
                pairs.append((a, len(parts) + b))
    shapes = [*parts, *[shape for shape, _ in kept]]
    wholes: list[tuple[Any, dict[int, float], float]] = []
    for group in _groups(len(shapes), pairs):
        owners: dict[int, float] = {}
        for k in group:
            if k >= len(parts):
                owner = kept[k - len(parts)][1]
                owners[owner] = owners.get(owner, 0.0) + float(shapes[k].area)
        joined = _joined([shapes[k] for k in group]) if len(group) > 1 else shapes[group[0]]
        for whole in piecemap.polygons(joined):
            if whole.area > CRUMB_MM2:
                if len(group) > 1:
                    whole = _unstepped(whole, zone, LINE_SHARE * found.lead_mm)
                wholes.append((whole, owners, float(sum(owners.values()))))
    # A piece that reaches out of the box keeps its number, where it's the most of that
    # piece left outside. What's found wholly inside keeps the number of the piece it
    # stands where one stood, or takes the next, read as a picture is, a row at a time
    # from the top-left.
    by_id = {p.id: p for p in held}
    taken: set[int] = set()
    numbered: list[tuple[int | None, Any, dict[int, float]]] = []
    for whole, owners, _ in sorted(wholes, key=lambda row: -row[2]):
        mine = next(
            (n for n in sorted(owners, key=lambda n: -owners[n]) if n not in taken), None
        )
        if mine is not None:
            taken.add(mine)
        numbered.append((mine, whole, owners))
    band_mm = max((zone.bounds[3] - zone.bounds[1]) / 12.0, 1e-6)

    def reading_order(row: tuple[int | None, Any, dict[int, float]]) -> tuple[float, float]:
        x, y = shapely.get_coordinates(row[1].representative_point())[0]
        return (-math.floor(y / band_mm), x)

    table = {name: dict(entry) for name, entry in found.colours.items()}
    made: list[Piece] = []
    number = found.next_id()
    outside = {owner for _, owner in kept}
    inside = [p for p in held if p.id not in outside]
    for mine, whole, owners in sorted(numbered, key=reading_order):
        # What the box calls glass is glass at its own darkness.
        paint = picture.paint(whole, min(dark, picture.under))
        like = by_id[max(owners, key=lambda n: owners[n])] if owners else None
        if like is not None:
            colour, thickness = like.colour, like.thickness_mm
        else:
            colour = _nearest(paint["rgb"], named) if paint else next(iter(named))
            thickness = None
            table.setdefault(colour, {"rgb": list(named[colour])})
        if mine is None:
            # The piece it stands where one wholly inside the box stood keeps that number.
            stood = [
                p.id
                for p in inside
                if p.id not in taken and p.shape.intersection(whole).area > 0.5 * p.shape.area
            ]
            if stood and not owners:
                mine = stood[0]
                taken.add(mine)
            else:
                mine, number = number, number + 1
        made.append(
            Piece(mine, colour, shapely.geometry.polygon.orient(whole, 1.0), thickness, paint)
        )
    if not made:
        raise NoAnswer(
            f"at --dark {dark} the box from {piecemap.said_at(zone.bounds[:2])} to "
            f"{piecemap.said_at(zone.bounds[2:])} is lead all over: nothing in it is glass"
        )
    box_mm = [round(v, piecemap.PLACES) for v in zone.bounds]
    entry = {"fix": "retrace", "box": box_mm, "dark": dark, "palette": palette}
    after = _swap(found, [p.id for p in held], made, entry, tidy=False, colours=table)
    was_glass = shapely.union_all([p.shape for p in held]) if held else shapely.Polygon()
    now_glass = shapely.union_all([p.shape for p in made])
    glass = (
        float(now_glass.difference(was_glass).area),
        float(was_glass.difference(now_glass).area),
    )
    ids = {p.id for p in held}
    beside = [
        p.shape for p in found.pieces if p.id not in ids and p.shape.distance(zone) <= EDGE_TOUCH
    ]
    edge = _edge_read(found, zone, [*[shape for shape, _ in kept], *beside], parts, mm)
    figures = {
        "box_mm": box_mm,
        "dark_under": dark,
        "pieces_in_box_before": len(held),
        "pieces_in_box_after": len(_held(after, zone)),
        "lines_in_box_before": lines_of(found, zone),
        "lines_in_box_after": lines_of(after, zone),
        "dead_ends_in_box_before": _dead_ends_in(found, zone),
        "dead_ends_in_box_after": _dead_ends_in(after, zone),
        "lines_across_box_edge": edge["crossing"],
        "lines_ending_at_box_edge": edge["ends"],
        "box_edge_step_mm": round(edge["step_mm"], 4),
    }
    where = f"{piecemap.said_at(zone.bounds[:2])} to {piecemap.said_at(zone.bounds[2:])}"
    lines = [
        f"the box as read: {where}, its sides on the picture's pixels and inside the box "
        f"given; traced at --dark {dark}, in the nearest of "
        + (", ".join(named) if len(named) <= 12 else f"{len(named)} colours"),
        f"in the box before: {figures['pieces_in_box_before']} piece(s), "
        f"{figures['lines_in_box_before']} line(s), {figures['dead_ends_in_box_before']} dead "
        f"end(s). After: {figures['pieces_in_box_after']} piece(s), "
        f"{figures['lines_in_box_after']} line(s), {figures['dead_ends_in_box_after']} dead "
        "end(s)",
        f"{edge['crossing']} line(s) cross the box's edge: each stays where it was outside, "
        "and joins the line found inside"
        + (
            f", with a step of {edge['step_mm']:.2f} mm at the most where the two meet, "
            "which the line found inside takes up: no outline has a step at the edge"
            if edge["step_mm"] >= 0.005
            else ""
        ),
    ]
    for end in edge["ends"][:ENDS_SAID]:
        lines.append(
            f"  a line ends at the box's edge at {piecemap.said_at(end['at'])}, "
            f"{end['width_mm']:.2f} mm wide there: "
            + (
                "found inside the box, with glass outside it"
                if end["of"] == "inside"
                else "it runs to the box from outside, and inside is glass"
            )
        )
    if len(edge["ends"]) > ENDS_SAID:
        lines.append(f"  and {len(edge['ends']) - ENDS_SAID} more, in the saved numbers")
    if traced["specks"] or traced["specks_filled"] or traced["parted"]:
        lines.append(
            f"in the box, as a trace reads it: {traced['specks']} speck(s) of colour read as "
            f"lead, {traced['specks_filled']} dark speck(s) filled as glass, "
            f"{traced['parted']} piece(s) parted from a neighbour"
        )
    seconds, differs = _laid_over_box(found, after, picture, window, traced["glass"], parts)
    figures["outlines"], read, fails = outlines_read(found, after, [p.id for p in made])
    lines += read
    ends = len(edge["ends"])
    return Made(
        after,
        [p.id for p in made],
        f"the picture is traced again inside {where} at --dark {dark}: the box held "
        f"{figures['pieces_in_box_before']} piece(s) and {figures['lines_in_box_before']} "
        f"line(s), and holds {figures['pieces_in_box_after']} and "
        f"{figures['lines_in_box_after']}; {edge['crossing']} line(s) cross its edge and stay "
        f"where they were, and {ends} end(s) at it"
        + (": make the box larger, or `pieces extend` or `pieces drop-line` each" if ends else ""),
        glass,
        figures,
        lines,
        seconds,
        differs,
        fails,
    )


def _laid_over_box(
    before: Map,
    after: Map,
    picture: Picture,
    window: tuple[int, int, int, int],
    glass: np.ndarray,
    parts: list[Any],
) -> tuple[list[str], list[str]]:
    """A retrace read again, by the two maps' pixels at the picture's own scale: inside the
    box, the new map against the patches found there; outside it, the map before."""
    r0, r1, c0, c1 = window
    was = piecemap.pixels(before, 1.0 / picture.mm).ids > 0
    now = piecemap.pixels(after, 1.0 / picture.mm).ids > 0
    rows = min(was.shape[0], picture.size[0])
    cols = min(was.shape[1], picture.size[1])
    r1, c1 = min(r1, rows), min(c1, cols)
    inside = now[r0:r1, c0:c1]
    patches = glass[: r1 - r0, : c1 - c0]
    agree = 100.0 * float((inside == patches).mean())
    rims = sum(part.length for part in parts) / picture.mm
    allowed = 100.0 * rims / max(inside.size, 1) + 0.05
    apart = was[:rows, :cols] != now[:rows, :cols]
    apart[r0:r1, c0:c1] = False
    moved = int(apart.sum())
    seconds = [
        f"the new map drawn back over the patches found in the box, at the picture's "
        f"{1.0 / picture.mm:.1f} pixels a mm: {agree:.2f}% of the box's pixels are glass in "
        f"both or lead in both, where a pixel's width along every outline is {allowed:.2f}%",
        f"the two maps' pixels outside the box: {moved} differ",
    ]
    differs = []
    if 100.0 - agree > allowed + 0.05:
        differs.append(
            f"the new map differs from the patches found in the box in {100.0 - agree:.2f}% of "
            f"its pixels, more than the {allowed:.2f}% a pixel's width along every outline allows"
        )
    if moved:
        differs.append(
            f"{moved} pixel(s) outside the box are glass in one map and lead in the other"
        )
    return seconds, differs


def run_retrace(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    made = do_retrace(found, picture_of(found, args.image), args.box, args.dark, args.palette)
    figures = made.numbers
    report.fact("box_mm", figures["box_mm"], how="the box as read, x0, y0, x1, y1")
    report.fact("dark_under", figures["dark_under"], how="a pixel in the box is lead under this")
    report.fact("pieces_in_box_before", figures["pieces_in_box_before"], how="that reach into it")
    report.fact("pieces_in_box_after", figures["pieces_in_box_after"], how="in the new map")
    report.fact(
        "lines_in_box_before", figures["lines_in_box_before"],
        how="each two pieces with a line of their own between them, in the box",
    )  # fmt: skip
    report.fact("lines_in_box_after", figures["lines_in_box_after"], how="in the new map")
    report.fact("dead_ends_in_box_before", figures["dead_ends_in_box_before"], how="in the box")
    report.fact("dead_ends_in_box_after", figures["dead_ends_in_box_after"], how="in the new map")
    report.fact(
        "lines_across_box_edge", figures["lines_across_box_edge"],
        how="lead on both sides of the box's edge at one place",
    )  # fmt: skip
    report.numbers["lines_ending_at_box_edge"] = figures["lines_ending_at_box_edge"]
    report.numbers["box_edge_step_mm"] = figures["box_edge_step_mm"]
    _finish(args, report, found, made)


# A patch of lead


def _patch(found: Map, at: Any) -> tuple[Any, Any]:
    """The patch of lead `--at` names, and the lead: the lead wider than a line and a half
    that holds the point, or the nearest such within `NEAR` leads of it."""
    usual = found.lead_mm
    reach = PATCH * usual / 2.0
    lead = found.lead()
    here = shapely.Point(at)
    cores = piecemap.polygons(lead.buffer(-reach))
    near = sorted(((float(core.distance(here)), k) for k, core in enumerate(cores)))
    if not near or near[0][0] > reach + NEAR * usual:
        across, where = piecemap.widest(lead)
        inside = [p.id for p in found.pieces if p.shape.contains(here)]
        raise NoAnswer(
            f"no patch of lead within {NEAR * usual:.1f} mm of {piecemap.said_at(at)}"
            + (f", which is in piece {inside[0]}'s glass" if inside else "")
            + f": a patch is lead wider than {2.0 * reach:.2f} mm, a line and a half, and "
            f"the lead's widest point is {across:.2f} mm at {piecemap.said_at(where)}. "
            "`pieces drop-line MAP --at X,Y` takes a line out"
        )
    core = cores[near[0][1]]
    # Out again by what it came in, its corners kept: the patch to its own edges, and none
    # of a line that leaves it.
    grown = core.buffer(reach, join_style="mitre", mitre_limit=5.0).intersection(lead)
    parts = [part for part in piecemap.polygons(grown) if part.intersects(core)]
    return shapely.union_all(parts), lead


def _detail(found: Map, wide: float) -> float:
    """The map's own detail, in mm: what a trace takes out of an outline, a pixel of the
    picture it was traced from (`pieces_trace.SIMPLIFY_PX`), or of the scale the bench reads
    the map at where it wasn't traced. Never over a quarter of a line."""
    said = found.source.get("px_per_mm")
    per_mm = piecemap.usual_px_per_mm(found)
    if isinstance(said, int | float) and not isinstance(said, bool) and said > 0:
        per_mm = float(said)
    return min(SIMPLIFY_PX / per_mm, wide / 4.0)


def _borders(found: Map, zone: Any, near: list[Piece], fine: float) -> list[Any]:
    """Where a patch is as near one of the pieces round it as another: the lines between
    their shares, each drawn through its points to the map's own detail.

    Points are set close along the pieces' edges, and the plane is cut into each point's
    cell, the part nearer it than any other. A side between the cells of two pieces' points
    is a step of a border: it's as near one piece as the other. The steps, end to end, turn
    at every point, a tenth of a mm apart, so each border is then read as the line they
    stand for, within `fine` of them, from one end to the other: a meeting of three stays
    where it is."""
    from scipy.spatial import Voronoi

    step = min(SHARE_STEP, found.lead_mm / 8.0)
    hull = zone.buffer(piecemap.widest(zone)[0] / 2.0 + found.lead_mm)
    points, owners = [], []
    for k, piece in enumerate(near):
        edge = piece.shape.boundary.intersection(hull)
        if edge.is_empty:
            continue
        along = shapely.get_coordinates(shapely.segmentize(edge, step))
        points.append(along)
        owners += [k] * len(along)
    spots, first = np.unique(np.round(np.vstack(points), 6), axis=0, return_index=True)
    # Four points far outside, no piece's: every cell of a piece's point is then closed,
    # and a side between two of them has two ends.
    x0, y0, x1, y1 = hull.bounds
    far = 8.0 * max(x1 - x0, y1 - y0)
    low, high = (x0 - far, y0 - far), (x1 + far, y1 + far)
    guards = [low, (high[0], low[1]), high, (low[0], high[1])]
    owner = np.concatenate([np.asarray(owners)[first], [-1] * len(guards)])
    cells = Voronoi(np.vstack([spots, guards]))
    pairs = np.asarray(cells.ridge_points)
    ends = np.asarray(cells.ridge_vertices)
    one, two = owner[pairs[:, 0]], owner[pairs[:, 1]]
    between = (one != two) & (one >= 0) & (two >= 0) & (ends >= 0).all(axis=1)
    if not between.any():
        return []
    steps = shapely.multilinestrings(cells.vertices[ends[between]])
    inside = shapely.line_merge(steps).intersection(zone)
    return [stroke.simplify(fine) for stroke in _strokes(inside)]


def _run_on(border: Any, zone: Any, by: float) -> Any:
    """A border run on by `by` past each end that's on the patch's edge, the way it was
    going: its line then crosses the edge with its sides, and not with its round end, which
    meets a straight edge at two points a few ten-thousandths of a mm apart."""
    points = [np.asarray(p) for p in border.coords]
    rim = zone.boundary

    def past(end: np.ndarray, back: np.ndarray) -> list[np.ndarray]:
        way = end - back
        length = float(np.hypot(*way))
        if length <= 0 or shapely.Point(end).distance(rim) > EDGE_TOUCH:
            return []
        return [end + way / length * by]

    return shapely.LineString(
        [*past(points[0], points[1]), *points, *past(points[-1], points[-2])]
    )


def _shares(
    found: Map, zone: Any, near: list[Piece], wide: float
) -> tuple[dict[int, list[Any]], list[Any]]:
    """A patch shared among the pieces round it: for each, by its place in `near`, the parts
    of the patch on its side of the borders, where the patch is as near one piece as
    another; and the borders. Each border is a line `wide` across, drawn along its own path
    and not along the points it was found from, so a share's edge is as smooth as the path.
    Two shares never touch: a border lies between them. A part no piece lies against, shut
    in by borders, stays lead."""
    if len(near) == 1:
        return {0: [zone]}, []
    borders = _borders(found, zone, near, _detail(found, wide))
    if not borders:
        raise NoAnswer(
            f"the patch round {piecemap.said_at(piecemap.widest(zone)[1])} has "
            f"{len(near)} pieces beside it and no place as near one as another: there's no "
            "line to leave between them"
        )
    strip = shapely.union_all(
        [_run_on(border, zone, wide).buffer(wide / 2.0, quad_segs=ROUND) for border in borders]
    )
    rims = [piece.shape.boundary.buffer(EDGE_TOUCH) for piece in near]
    shares: dict[int, list[Any]] = {}
    for part in piecemap.polygons(zone.difference(strip)):
        if part.area <= 1e-6:
            continue
        along = [float(part.boundary.intersection(rim).length) for rim in rims]
        order = sorted(range(len(near)), key=lambda k: -along[k])
        if along[order[0]] <= EDGE_TOUCH:
            continue
        if along[order[1]] > wide / 4.0:
            raise NoAnswer(
                f"lines {wide:g} mm wide don't part pieces {near[order[0]].id} and "
                f"{near[order[1]].id} round the patch: the line between them is wider where "
                "it leaves it. Give a wider --lead"
            )
        shares.setdefault(order[0], []).append(part)
    return shares, borders


def _scraps(found: Map, piece: Piece, shape: Any, zone: Any, wide: float) -> tuple[Any, int]:
    """A piece with its share, and the scraps of lead the share left in it turned to glass:
    with how many. A scrap is lead beside the patch that was no part of it, a hair between
    the patch's edge and the piece's or a nick in the piece's edge narrower than a line,
    which the share now shuts in or leaves as a line that ends in the piece's glass. It's
    glass where it's too short to be a line, under a dead end's length (`pieces_check`),
    it wasn't a gap in the piece before, and no other piece is near it."""
    longest = DEAD_END * found.lead_mm
    others = [o.shape for o in found.pieces if o.id != piece.id]
    tree = shapely.STRtree(others) if others else None
    beside = zone.buffer(wide)

    def lone(scrap: Any) -> bool:
        if tree is None:
            return True
        return not len(tree.query(scrap, predicate="dwithin", distance=SCRAP_CLEAR * wide))

    def short(scrap: Any) -> bool:
        corners = shapely.get_coordinates(scrap.minimum_rotated_rectangle)
        sides = np.hypot(*np.diff(corners, axis=0).T) if len(corners) >= 4 else np.zeros(1)
        return float(sides.max()) < longest

    count = 0
    # Shut in: a hole the piece didn't have.
    was = [shapely.Polygon(ring) for ring in piece.shape.interiors]
    holes = []
    for ring in shape.interiors:
        hole = shapely.Polygon(ring)
        new = not any(hole.intersection(old).area > 0.5 * hole.area for old in was)
        if new and hole.intersects(beside) and short(hole) and lone(hole):
            count += 1
        else:
            holes.append(ring)
    shape = shapely.Polygon(shape.exterior, holes)
    # Open at one end: a gap narrower than a line that the piece's own edge didn't have.
    reach = SCRAP_REACH * wide
    had = piecemap.closed(piece.shape, reach).difference(piece.shape)
    found_now = []
    for scrap in piecemap.polygons(piecemap.closed(shape, reach).difference(shape)):
        if scrap.area < SCRAP_MM2 or not scrap.intersects(beside):
            continue
        if scrap.intersection(had).area > 0.5 * scrap.area:
            continue
        if short(scrap) and lone(scrap):
            found_now.append(scrap)
    if found_now:
        whole = _joined([shape, *found_now])
        if whole.geom_type == "Polygon" and whole.is_valid:
            shape, count = whole, count + len(found_now)
    return shape, count


def _points(shape: Any) -> int:
    """The points of a piece's outline and its holes'."""
    return sum(len(ring.coords) - 1 for ring in (shape.exterior, *shape.interiors))


def _one_point(shape: Any, was: Any) -> Any:
    """A piece with its share, less each new point of its outline that's all but on the one
    before it or after it: the piece's own points, as they were, all stay."""
    old = {(round(x, 6), round(y, 6)) for x, y in shapely.get_coordinates(was)}
    rings = []
    for ring in (shape.exterior, *shape.interiors):
        points = shapely.get_coordinates(ring)[:-1]
        kept: list[Any] = []
        for k, point in enumerate(points):
            new = (round(point[0], 6), round(point[1], 6)) not in old
            after = points[(k + 1) % len(points)]
            near = (kept and math.dist(point, kept[-1]) < SAME_POINT) or (
                math.dist(point, after) < SAME_POINT
                and (round(after[0], 6), round(after[1], 6)) in old
            )
            if not (new and near):
                kept.append(point)
        # Where the ring begins between two such points, the first is the one that goes.
        first = (round(kept[0][0], 6), round(kept[0][1], 6)) if kept else None
        if len(kept) > 3 and first not in old and math.dist(kept[0], kept[-1]) < SAME_POINT:
            kept = kept[1:]
        rings.append(kept if len(kept) >= 3 else points)
    made = shapely.Polygon(rings[0], rings[1:])
    if made.is_valid and made.geom_type == "Polygon" and not made.is_empty:
        return shapely.geometry.polygon.orient(made, 1.0)
    return shape


def _fits(found: Map, piece: Piece) -> str:
    """Why a piece can't be built whatever its lip, or nothing: `pieces build`'s own reading
    of its pane and its outline's fit to lines and arcs, at the build's usual `--fit` and
    `--play`, as `pieces check` reads it.
    Whether a lip leaves it an opening is the lip's to say, and `pieces check --lip` reads it."""
    from .pieces_build import pane_refused

    limits = Limits()
    return pane_refused(found, piece, limits.play, limits.fit)


def outlines_read(
    before: Map, after: Map, changed: list[int]
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """What a fix made of each piece it changed: the points of its outline before and after,
    and whether it builds. As rows, as lines of the reply, and as what's wrong: a piece that
    built before the fix and doesn't after it."""
    limits = Limits()
    old = {p.id: p for p in before.pieces}
    rows: list[dict[str, Any]] = []
    lines: list[str] = []
    fails: list[str] = []
    for number in changed:
        piece = after.piece(number)
        was = old.get(number)
        why = _fits(after, piece)
        why_was = _fits(before, was) if was is not None and why else ""
        row = {
            "piece": number,
            "points_before": None if was is None else _points(was.shape),
            "points_after": _points(piece.shape),
            "builds": not why,
            "built_before": None if was is None else not why_was,
        }
        rows.append(row)
        count = (
            f"{row['points_after']} point(s), new to the map"
            if was is None
            else f"{row['points_before']} point(s) before and {row['points_after']} after"
        )
        if not why:
            lines.append(f"piece {number}'s outline: {count}; it builds")
        elif why_was:
            lines.append(
                f"piece {number}'s outline: {count}; it doesn't build, and didn't before: it {why}"
            )
        else:
            lines.append(f"piece {number}'s outline: {count}; it doesn't build: it {why}")
            fails.append(f"piece {number} doesn't build as the fix leaves it: it {why}")
    if rows:
        lines.append(
            "a piece builds where `pieces build`'s play leaves one pane of it and its outline "
            f"fits lines and arcs, at its usual --fit {limits.fit:g} and --play {limits.play:g}"
        )
    return rows, lines, fails


def do_fill(found: Map, at: Any, lead_mm: float | None) -> Made:
    wide = found.lead_mm if lead_mm is None else float(lead_mm)
    if not wide > 0:
        raise NoAnswer("--lead is the width of the lines the fill leaves, in mm, over 0")
    zone, _ = _patch(found, at)
    tree = shapely.STRtree([p.shape for p in found.pieces])
    near = [found.pieces[int(k)] for k in tree.query(zone, predicate="dwithin", distance=1e-6)]
    if not near:
        raise NoAnswer(
            f"the lead round {piecemap.said_at(at)} has no piece beside it: there's no glass "
            "for it to become"
        )
    near = sorted(near, key=lambda p: p.id)
    theirs, borders = _shares(found, zone, near, wide)
    made: list[Piece] = []
    shares: list[dict[str, Any]] = []
    scraps, beside = 0, 0.0
    for k, piece in enumerate(near):
        mine = theirs.get(k)
        if not mine:
            continue
        joined = _one(_joined([piece.shape, *mine]), f"piece {piece.id} with its share")
        shape, more = _scraps(found, piece, joined, zone, wide)
        shape = _one_point(shape, piece.shape)
        scraps += more
        took = float(shape.area - piece.shape.area)
        paint = {"rgb": piece.paint["rgb"]} if piece.paint and "rgb" in piece.paint else None
        made.append(Piece(piece.id, piece.colour, shape, piece.thickness_mm, paint))
        beside += float(shape.area - joined.area)
        shares.append({"piece": piece.id, "area_mm2": round(took, 3)})
    if not made:
        raise NoAnswer(
            f"the patch round {piecemap.said_at(at)} is too thin to share: lines "
            f"{wide:g} mm wide between the pieces round it leave no glass"
        )
    after = _swap(
        found, [p.id for p in made], made,
        {"fix": "fill", "at": [float(at[0]), float(at[1])], "lead_mm": lead_mm},
    )  # fmt: skip
    added = float(sum(row["area_mm2"] for row in shares))
    across, where = piecemap.widest(zone)
    seconds, differs = _nearest_by_pixels(found, after)
    rows, lines, fails = outlines_read(found, after, [p.id for p in made])
    box = zone.buffer(2.0 * wide)
    ends = (_dead_ends_in(found, box), _dead_ends_in(after, box))
    said = ", ".join(f"piece {row['piece']} {row['area_mm2']:.1f} mm²" for row in shares)
    return Made(
        after,
        [p.id for p in made],
        f"the patch of lead round {piecemap.said_at(where)}, {zone.area:.1f} mm² and "
        f"{across:.2f} mm at its widest, is glass: {said}; each took the part nearer to it "
        f"than to any other, and {zone.area - added + beside:.1f} mm² is left as "
        f"{len(borders)} line(s) {wide:.2f} mm wide between them"
        + (
            f"; {scraps} scrap(s) of lead beside the patch, too short to be a line, are "
            f"glass too, {beside:.2f} mm²"
            if scraps
            else ""
        ),
        (added, 0.0),
        {
            "patch_mm2": float(zone.area),
            "patch_widest_mm": across,
            "patch_at": where,
            "patch_shares": shares,
            "patch_left_as_lead_mm2": float(zone.area - added + beside),
            "patch_scraps_count": scraps,
            "patch_scraps_mm2": beside,
            "dead_ends_at_patch_before": ends[0],
            "dead_ends_at_patch_after": ends[1],
            "outlines": rows,
        },
        [
            *[f"piece {r['piece']} took {r['area_mm2']:.2f} mm² of the patch" for r in shares],
            *lines,
            f"dead ends at the patch: {ends[0]} before and {ends[1]} after",
        ],
        seconds,
        differs,
        fails,
    )


def _nearest_by_pixels(before: Map, after: Map) -> tuple[list[str], list[str]]:
    """A fill read again: each pixel that was lead and is glass, against the piece nearest
    it in the map before, by the pixels' own distances."""
    from scipy import ndimage

    was = piecemap.pixels(before)
    now = piecemap.pixels(after, was.px_per_mm)
    new = (was.ids == 0) & (now.ids > 0)
    count = int(new.sum())
    if not count:
        return ["no pixel that was lead is glass"], ["by the maps' pixels the fill added no glass"]
    _, (rows, cols) = ndimage.distance_transform_edt(was.ids <= 0, return_indices=True)
    nearest = was.ids[rows, cols]
    same = 100.0 * float((nearest[new] == now.ids[new]).mean())
    seconds = [
        f"{count} pixel(s) that were lead are glass, {count * was.mm**2:.1f} mm²: {same:.2f}% "
        "are the piece nearest them in the map before, by the pixels' own distances"
    ]
    differs = []
    if same < NEAREST_PCT:
        differs.append(
            f"only {same:.2f}% of the pixels the fill made glass are the piece nearest them "
            f"by the pixels, under {NEAREST_PCT:g}%"
        )
    return seconds, differs


def run_fill(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    made = do_fill(found, args.at, args.lead)
    report.fact("patch_mm2", made.numbers["patch_mm2"], "mm²", "the patch of lead, by its outline")
    report.fact(
        "patch_widest_mm", made.numbers["patch_widest_mm"], "mm",
        f"the largest circle that fits in it, at {piecemap.said_at(made.numbers['patch_at'])}",
    )  # fmt: skip
    report.fact(
        "patch_left_as_lead_mm2", made.numbers["patch_left_as_lead_mm2"], "mm²",
        "the lines left between the pieces' shares",
    )  # fmt: skip
    report.fact(
        "patch_scraps_count", made.numbers["patch_scraps_count"],
        how="scraps of lead beside the patch, too short to be a line, that are glass too",
    )  # fmt: skip
    report.numbers["patch_scraps_mm2"] = made.numbers["patch_scraps_mm2"]
    report.fact(
        "dead_ends_at_patch_before", made.numbers["dead_ends_at_patch_before"],
        how="lines that end in glass within two lines of the patch",
    )  # fmt: skip
    report.fact(
        "dead_ends_at_patch_after", made.numbers["dead_ends_at_patch_after"],
        how="in the new map",
    )  # fmt: skip
    report.numbers["patch_at"] = made.numbers["patch_at"]
    report.numbers["patch_shares"] = made.numbers["patch_shares"]
    _finish(args, report, found, made)


# A piece's colour


def colour_of(text: str) -> tuple[str, list[int] | None]:
    """`gold` or `gold=#f5c832`: a colour's name, and its RGB where it's given."""
    name, sep, value = text.strip().partition("=")
    name = name.strip().lower()
    if not name:
        raise NoAnswer("COLOUR is a colour's name, or NAME=#rrggbb for one new to the map")
    if not sep:
        return name, None
    if not colours.HEX.fullmatch(value.strip()):
        raise NoAnswer(f"COLOUR: {text!r} isn't NAME=#rrggbb")
    code = value.strip().lstrip("#")
    return name, [int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16)]


def do_paint(
    found: Map,
    number: int,
    name: str,
    rgb: list[int] | None,
    picture: Callable[[Map], Picture] | None,
) -> Made:
    piece = found.piece(number)
    name = name.strip().lower()
    table = {key: dict(entry) for key, entry in found.colours.items()}
    known = table.get(name, {}).get("rgb")
    if rgb is not None and known is not None and [int(v) for v in known] != [int(v) for v in rgb]:
        raise NoAnswer(
            f"the map's {name} is rgb {[int(v) for v in known]}, not {list(rgb)}: give `{name}` "
            "alone for the map's, or the new colour another name"
        )
    if rgb is None and known is not None:
        rgb = [int(v) for v in known]
    elif rgb is None and name in colours.NAMED:
        rgb = list(colours.NAMED[name])
    elif rgb is None and not any(p.colour == name for p in found.pieces):
        said = sorted(set(table) | {p.colour for p in found.pieces})
        raise NoAnswer(
            f"the map has no colour {name!r}, and the bench has no colour of that name: the "
            f"map's are {', '.join(said) or 'none'}. Give a new one as {name}=#rrggbb"
        )
    if name == piece.colour:
        raise NoAnswer(f"piece {piece.id} is {name} already")
    new = name not in table
    if rgb is not None:
        table.setdefault(name, {})["rgb"] = [int(v) for v in rgb]
    # The picture's own colour in the piece, two ways: as the trace kept it, and read now.
    kept = piece.paint.get("rgb") if piece.paint else None
    read, unread = None, ""
    if picture is not None:
        try:
            paint = picture(found).paint(piece.shape)
            read = paint["rgb"] if paint else None
        except NoAnswer as exc:
            unread = str(exc)
    own = read if read is not None else kept
    old_rgb = (found.colours.get(piece.colour) or {}).get("rgb") or colours.NAMED.get(piece.colour)
    figures: dict[str, Any] = {
        "colour": name,
        "colour_rgb": rgb,
        "colour_was": piece.colour,
        "colour_in_picture": own,
        "colour_off": None if own is None or rgb is None else round(math.dist(rgb, own), 2),
        "colour_was_off": (
            None if own is None or old_rgb is None else round(math.dist(old_rgb, own), 2)
        ),
    }
    made = Piece(piece.id, name, piece.shape, piece.thickness_mm, piece.paint)
    counted: dict[str, int] = {}
    for one in [*[p for p in found.pieces if p.id != piece.id], made]:
        counted[one.colour] = counted.get(one.colour, 0) + 1
    for key, entry in table.items():
        if "pieces" in entry or key == name:
            entry["pieces"] = counted.get(key, 0)
    after = _swap(
        found, [piece.id], [made],
        {"fix": "paint", "piece": piece.id, "colour": name, "rgb": rgb, "was": piece.colour},
        colours=table,
    )  # fmt: skip
    lines, seconds = [], []
    if own is None or rgb is None:
        lines.append(
            f"how far {name} is from the picture's own colour in piece {piece.id} isn't "
            + (
                "read: the map has no RGB for that colour"
                if rgb is None
                else "read: the map keeps no colour for it from a picture"
            )
            + (f", and {unread}" if unread else "")
        )
        far = ""
    else:
        nearest = min(
            (key for key, entry in table.items() if entry.get("rgb")),
            key=lambda key: math.dist(table[key]["rgb"], own),
        )
        figures["colour_nearest"] = nearest
        far = (
            f": {figures['colour_off']:.0f} from the picture's own colour in that piece, rgb "
            f"{[int(v) for v in own]}, as a distance in RGB"
            + (
                f", where {piece.colour} was {figures['colour_was_off']:.0f}"
                if figures["colour_was_off"] is not None
                else ""
            )
        )
        lines.append(
            f"the picture's colour in piece {piece.id} is rgb {[int(v) for v in own]}: "
            f"{name}, rgb {rgb}, is {figures['colour_off']:.1f} from it, as a distance in "
            f"RGB, each channel 0 to 255; the nearest of the map's colours to it is {nearest}"
        )
        if read is not None and kept is not None:
            seconds.append(
                f"the picture's colour in piece {piece.id}, read now over its outline: rgb "
                f"{[int(v) for v in read]}; as the map kept it from the trace: rgb "
                f"{[int(v) for v in kept]}, {math.dist(read, kept):.1f} apart"
            )
        elif read is None and unread:
            lines.append(f"the picture isn't read again: {unread}. The colour is the trace's")
    return Made(
        after,
        [piece.id],
        f"piece {piece.id} is {name} now, and was {piece.colour}"
        + (f"; {name} is new to the map, rgb {rgb}" if new and rgb is not None else "")
        + far,
        (0.0, 0.0),
        figures,
        lines,
        seconds,
    )


def run_paint(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    name, rgb = colour_of(args.colour)
    made = do_paint(found, args.id, name, rgb, reader(args.image))
    figures = made.numbers
    report.fact(
        "colour", figures["colour"], how=f"piece {args.id}'s, and was {figures['colour_was']}"
    )
    report.fact(
        "colour_off", figures["colour_off"], "",
        "from the picture's own colour in the piece, as a distance in RGB, each channel 0 to 255",
    )  # fmt: skip
    report.fact("colour_was_off", figures["colour_was_off"], "", "the colour it had, the same way")
    report.numbers["colour_rgb"] = figures["colour_rgb"]
    report.numbers["colour_in_picture"] = figures["colour_in_picture"]
    report.numbers["colour_nearest"] = figures.get("colour_nearest")
    _finish(args, report, found, made)


# A line between two neighbours


def _usual_gap(a: Any, b: Any, reach: float) -> float:
    """How wide the line between two shapes is, as it usually is: the middle of the
    distances from one's edge to the other, wherever they're within reach."""
    ring = shapely.get_coordinates(a.exterior.segmentize(0.1))[:-1]
    far = shapely.distance(shapely.points(ring), b)
    near = far[far <= reach]
    return float(np.median(near)) if len(near) else float(a.distance(b))


def _under(found: Map, piece: Piece) -> dict[str, str]:
    """The limits a piece is under, as `pieces check` reads them with its own: a sliver, a
    small piece or a neck, each in the check's words."""
    limits = Limits()
    step = layout.spacing({str(piece.id): shapely.MultiPolygon([piece.shape])})
    row = piece_row(found, piece, limits, step, [], 1.0 / piecemap.usual_px_per_mm(found))
    return {f["flag"]: f["said"] for f in flag_piece(row, limits) if f["flag"] in UNDER}


def do_move_line(found: Map, a_id: int, b_id: int, points: list[Any]) -> Made:
    a, b = found.piece(a_id), found.piece(b_id)
    if a.id == b.id:
        raise NoAnswer("a line lies between two pieces: name two")
    lead = found.lead_mm
    gap = float(a.shape.distance(b.shape))
    if gap > WIDE_GAP * lead:
        raise NoAnswer(
            f"pieces {a.id} and {b.id} have no line of their own between them: the nearest "
            f"they come is {gap:.2f} mm, and a line here is about {lead:.2f} mm wide"
        )
    whole, _ = merged(found, a, b)
    width = _usual_gap(a.shape, b.shape, 2.5 * lead)
    if len(points) < 2:
        raise NoAnswer("--line is the new path: two points or more, from one end to the other")
    path = shapely.LineString(points)
    if not path.is_simple:
        raise NoAnswer("the new path crosses itself: a lead line doesn't")
    # Into another piece's glass: across the line between that piece and one of these two.
    for other in found.pieces:
        if other.id in (a.id, b.id) or not path.intersects(other.shape):
            continue
        at = shapely.get_coordinates(path.intersection(other.shape).representative_point())[0]
        beside = min((a, b), key=lambda p: float(p.shape.distance(shapely.Point(at))))
        raise NoAnswer(
            f"the new path crosses another line: the one between pieces {beside.id} and "
            f"{other.id}, into piece {other.id}'s glass at {piecemap.said_at(at)}. A moved line "
            f"stays between pieces {a.id} and {b.id}"
        )
    # Out of the two and back in: across lead that isn't their line.
    ends = shapely.MultiPoint([points[0], points[-1]])
    for part in _strokes(path.difference(whole.buffer(GRID))):
        if part.distance(ends) <= GRID:
            continue
        at = shapely.get_coordinates(part.interpolate(0.5, normalized=True))[0]
        others = sorted(
            (float(o.shape.distance(shapely.Point(at))), o.id)
            for o in found.pieces
            if o.id not in (a.id, b.id)
        )
        raise NoAnswer(
            f"the new path crosses another line at {piecemap.said_at(at)}: it leaves pieces "
            f"{a.id} and {b.id} there, across lead that isn't the line between them"
            + (f", beside piece {others[0][1]}" if others else "")
        )
    parts, strip = cut(Piece(0, "", whole), points, width)
    if len(parts) != 2:
        short = [
            piecemap.said_at(p)
            for p in (points[0], points[-1])
            if whole.contains(shapely.Point(p))
            and whole.exterior.distance(shapely.Point(p)) > width
        ]
        raise NoAnswer(
            f"along the new path the line cuts pieces {a.id} and {b.id} into {len(parts)}, "
            "not two: a moved line runs from one end of the old line to the other, edge to edge"
            + (f", and this one ends in glass at {' and '.join(short)}" if short else "")
        )
    first, second = parts
    straight = first.intersection(a.shape).area + second.intersection(b.shape).area
    swapped = first.intersection(b.shape).area + second.intersection(a.shape).area
    mine, theirs = (first, second) if straight >= swapped else (second, first)
    for piece, shape in ((a, mine), (b, theirs)):
        if shape.intersection(piece.shape).area <= CRUMB_MM2:
            raise NoAnswer(
                f"the new path leaves none of piece {piece.id} on its own side: a moved line "
                f"lies between pieces {a.id} and {b.id}"
            )
    made = []
    for piece, shape in ((a, mine), (b, theirs)):
        paint = {"rgb": piece.paint["rgb"]} if piece.paint and "rgb" in piece.paint else None
        made.append(Piece(piece.id, piece.colour, shape, piece.thickness_mm, paint))
    after = _swap(
        found, [a.id, b.id], made,
        {"fix": "move-line", "a": a.id, "b": b.id,
         "line": [[float(x), float(y)] for x, y in points]},
    )  # fmt: skip
    # A piece the new line leaves under the map's rules, where it wasn't before.
    for piece in (a, b):
        was, now = _under(found, piece), _under(after, after.piece(piece.id))
        new = [words for flag, words in now.items() if flag not in was]
        if new:
            raise NoAnswer(
                f"the new path leaves piece {piece.id} under the map's rules: {'; '.join(new)}. "
                "The line isn't moved"
            )
    both = _joined([a.shape, b.shape])
    now_both = _joined([mine, theirs])
    glass = (float(now_both.difference(both).area), float(both.difference(now_both).area))
    old_line = whole.difference(both)
    moved = float(strip.intersection(whole).hausdorff_distance(old_line)) if old_line.area else 0.0
    rows, lines, fails = outlines_read(found, after, [a.id, b.id])
    return Made(
        after,
        [a.id, b.id],
        f"the line between pieces {a.id} and {b.id} runs along the new path, "
        f"{path.length:.1f} mm, and is {width:.2f} mm wide as it was: it moved by up to "
        f"{moved:.1f} mm. {glass[0]:.1f} mm² of the old line is glass, and {glass[1]:.1f} mm² "
        f"of glass is the new line; piece {a.id} is {mine.area - a.shape.area:+.1f} mm² and "
        f"piece {b.id} {theirs.area - b.shape.area:+.1f} mm²",
        glass,
        {
            "line_width_mm": width,
            "line_moved_mm": moved,
            "line_length_mm": float(path.length),
            "outlines": rows,
        },
        lines,
        fails=fails,
    )


def run_move_line(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    made = do_move_line(found, args.a, args.b, args.line)
    report.fact("line_width_mm", made.numbers["line_width_mm"], "mm", "as the old line was")
    report.fact(
        "line_moved_mm", made.numbers["line_moved_mm"], "mm",
        "the furthest the new line lies from the old one",
    )  # fmt: skip
    report.fact("line_length_mm", made.numbers["line_length_mm"], "mm", "along the new path")
    _finish(args, report, found, made)
