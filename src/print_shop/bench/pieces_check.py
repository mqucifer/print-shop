"""`pieces check`: a piece map read against the rules glass cutting follows, each a figure
(mqucifer/print-shop#152).

A glass cutter can't end a score in mid-glass, cut a sliver or a deep notch, or keep a thin
neck from breaking, and a piece is one colour of glass. So a map, traced or made from rules, is
read for each, with where, before anything is built from it. Each flag is read two ways:

- **`dead-end`**, a lead line that ends in mid-glass: a slit into one piece, or a line inside
  one joined to no other. By the piece with every gap narrower than two leads filled, out and
  back in with mitred joins: a gap open at one end only. And by rays from each end, which meet
  that piece ahead and on both sides, and the map's pixels: lead with one piece's glass, and
  nothing else, within a lead's width.
- **`surrounds`**, a piece with another inside it: glass isn't cut with a hole in it, and the
  lead round the inner piece joins no other line, so the frame would be two bodies. By each
  hole of its outline that holds another piece. And by the map's pixels: lead joined to no
  lead that reaches another line or the panel's edge, counted where its pixels join.
- **`sliver`**, a piece under `--min-width` at its widest. By the largest circle that fits in
  its outline, and by the furthest any of its pixels is from its edge.
- **`small`**, a piece under `--min-area`. By its outline's area, and its pixels'.
- **`neck`**, the narrowest place straight across, by the nearest points facing each other,
  under `--min-neck` with glass `--min-width` wide or wider on both sides of it, by a cut
  across the place: narrower on one side it's a spur, a piece's thin end, and isn't flagged.
  Its second check is twice the pixels' distance to the edge at the place's middle, on pixels
  drawn round it fine enough to tell: the same line across, by another method. `layout`'s
  rays square to each edge measure something else on a slanted neck, the slant's long way, so
  what they read is a note and settles nothing (mqucifer/print-shop#161).
- **`notch`**, a notch deeper than `--max-notch` times its mouth. By the piece's hull less
  the piece: each part's furthest point from its mouth, the hull's edge across it. And by the
  furthest pixel's centre in it from that mouth, which is short by whatever of the notch is too
  thin for a pixel's centre to lie in: that's the reading's resolution, and it's said.

**A second check measures the same thing, and carries its resolution**
(mqucifer/print-shop#161). Two readings closer than the coarser one's resolution are the same,
and what's between them is a note. `DISAGREE` is two readings of the same thing on opposite
sides of a limit, each beyond the other's resolution (`limits.apart`).
- **`gradient`**, a colour that runs across one piece by more than `--max-colour-run`. By the
  picture's colour at the piece's two ends, as `pieces trace` read it, and by a plane fitted to
  its colour, as it read it. A piece a fix has changed has no run of its own: a merge kept one
  piece's colour and dropped the run, so two colours joined read as one. Its run comes from
  the picture again, over the piece as it is now (mqucifer/print-shop#172).
- **`wide-lead`**, lead wider than `--max-lead` at its widest. By the largest circle that fits
  in the lead, and by the furthest a lead pixel is from glass.

- **`thin-lead`**, lead between two pieces narrower than `--min-lead`, a nozzle's width: no
  nozzle prints it, and the frame built from the map has a wall that thin between two
  pockets. By the nearest the two outlines come. And by twice the pixels' distance to the
  glass at the middle of that place, on pixels drawn round it as a neck's are. On a traced
  scene 17 pairs of pieces were under 0.4 mm apart, and a run learned it only from `thin` on
  the frame it had built (mqucifer/print-shop#161).

- **`no-opening`**, with `--lip`, the lip a build will be given: a piece that lip leaves no
  one opening in. A waist the lip closes over, with the line to split it along; an opening
  pinched in two where an edge comes in; or none two nozzles wide. By `pieces build`'s own
  plan, the piece's pocket as it would be built. And by the piece's pixels further than the
  lip from its edge, which hold at least what a build's opening holds. A run learned each
  only from a build's refusal (mqucifer/print-shop#161).

And two readings with every answer: the glass share, the pieces' area over the panel's, by
their outlines and by their pixels, against the share the map's source had; and the lead's
widest point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import colours, layout, piecemap
from .limits import apart, over, said, under
from .piecemap import Map, Piece, Pixels
from .pieces_picture import picture_of
from .report import NoAnswer, Report

#: The flags, in the order a reply says them.
FLAGS = (
    "dead-end", "surrounds", "sliver", "small", "neck", "notch", "gradient", "wide-lead",
    "thin-lead", "no-opening",
)  # fmt: skip
#: The limits when none is given. A piece under 3 mm across or 25 mm² can't be handled, and a
#: place under 1.2 mm leaves no pane once its play and lips are taken: the operator's tracer's
#: figures, on the first traced scene (2026-11-17).
MIN_WIDTH = 3.0
MIN_AREA = 25.0
MIN_NECK = 1.2
#: A notch deeper than its mouth is wide can't be cut out of glass in one score.
MAX_NOTCH = 1.0
#: A colour's run across a piece, as a distance in 0 to 255 RGB: past this it's a gradient.
MAX_COLOUR_RUN = 40.0
#: Lead wider than this many of its usual width is a patch, not a line: where three lines meet
#: it's up to one and a half.
MAX_LEAD = 3.0
#: Lead under this, in mm, is under a nozzle's width: `pieces build` and `thin` hold 0.4 mm.
MIN_LEAD = 0.4
#: An opening is one where it's this wide somewhere, in mm: two nozzles, as `pieces build` has
#: it (`LEAST_ACROSS`).
LEAST_OPENING = 0.8
#: Thin places of the lead said one by one, at most a map: the rest are counted.
THIN_SAID = 40
#: A gap is a dead end from this many lead widths deep: shallower is a corner's own shape.
DEAD_END = 1.5
#: A body of lead shows in the map's pixels where it's this share of the usual lead wide.
LOOSE_SHARE = 0.5
#: A gap is a lead line where it's at least this share of the usual lead wide.
LINE_SHARE = 0.35
#: A place under `--min-neck` is a neck where the glass on both sides of it is this wide or
#: wider, `--min-width`: narrower on one side, it's a spur, the thin end of a piece, which a pane
#: and an opening simply lose. At most this many spurs are looked past for a neck behind them.
SPURS = 6
#: How far the pixels' reading of a width may be from the outline's, in pixels.
PIXELS_OFF = 2.5
#: How far the pixels' glass share may be from the outlines', in points of a percent, for each
#: pixel's width over the panel's mean piece: a pixel's width of each outline is uncertain.
SHARE_OFF = 0.5
#: A neck's width is read again on pixels drawn round it, this many across the narrower of the
#: place's width and the neck limit, so a pixel there is a small part of what's judged, and
#: never coarser than the map's own.
NECK_PX_ACROSS = 64.0
#: Those pixels' window is this many across at most: a place a hundredth of a millimetre wide
#: is then read on coarser pixels, and its resolution says so.
NECK_PX_MOST = 800
#: Flags said one by one, at most.
SAID = 12
#: Notes said, at most.
NOTES = 6
PICTURE_PX = 1400


@dataclass(frozen=True)
class Limits:
    min_width: float = MIN_WIDTH
    min_area: float = MIN_AREA
    min_neck: float = MIN_NECK
    max_notch: float = MAX_NOTCH
    max_colour_run: float = MAX_COLOUR_RUN
    max_lead: float | None = None
    min_lead: float = MIN_LEAD
    #: The lip a build will be given, where it's said, and its play and fit: the build's
    #: defaults unless given.
    lip: float | None = None
    play: float = 0.25
    fit: float = 0.1
    least_lip: float | None = None
    min_visible: float | None = None

    def lead(self, usual: float) -> float:
        return self.max_lead if self.max_lead is not None else MAX_LEAD * usual


def limits_of(args: Any) -> Limits:
    """A command's limits, each the default unless it's given."""

    def given(name: str, default: float) -> float:
        value = getattr(args, name, None)
        return default if value is None else float(value)

    lips = getattr(args, "lip", None)
    lip, least = lips if isinstance(lips, tuple) else (lips, lips)
    neck = given("min_neck", MIN_NECK)
    if lip is not None and not lip > 0:
        raise NoAnswer("--lip is the lip a build will be given, in mm, over 0")
    return Limits(
        given("min_width", MIN_WIDTH),
        given("min_area", MIN_AREA),
        neck,
        given("max_notch", MAX_NOTCH),
        given("max_colour_run", MAX_COLOUR_RUN),
        getattr(args, "max_lead", None),
        given("min_lead", MIN_LEAD),
        float(lip) if lip is not None else None,
        given("play", 0.25),
        given("fit", 0.1),
        float(least) if least is not None else None,
        getattr(args, "min_visible", None),
    )


# Dead ends


def _unit(v: Any) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    size = float(np.linalg.norm(v))
    return v / size if size > 0 else np.array([1.0, 0.0])


def _stretches(lines: Any, least: float) -> list[Any]:
    """Each connected stretch of some lines longer than `least`."""
    if lines.is_empty:
        return []
    merged = shapely.line_merge(lines) if lines.geom_type != "LineString" else lines
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    return [p for p in parts if p.geom_type == "LineString" and p.length > least]


def solid_of(found: Map, piece: Piece) -> tuple[Any, list[Any]]:
    """A piece with each hole that holds no other piece filled, and those holes: a hole with
    nothing in it but lead is a line inside the piece, joined to no other."""
    kept, lone = [], []
    for ring in piece.shape.interiors:
        hole = shapely.Polygon(ring)
        if any(o.id != piece.id and o.shape.intersects(hole) for o in found.pieces):
            kept.append(ring)
        else:
            lone.append(hole)
    return shapely.Polygon(piece.shape.exterior, kept), lone


def surrounding(found: Map, pieces: list[Piece]) -> list[dict[str, Any]]:
    """Each hole of a piece's outline that holds another piece: which piece, which it holds,
    and a point of the lead between them."""
    rows = []
    for piece in pieces:
        for ring in piece.shape.interiors:
            hole = shapely.Polygon(ring)
            holds = sorted(
                o.id for o in found.pieces if o.id != piece.id and o.shape.intersects(hole)
            )
            if holds:
                at = shapely.get_coordinates(ring)[0]
                rows.append({"piece": piece.id, "holds": holds, "at": piecemap.xy(at)})
    return rows


def lead_bodies(found: Map, drawn: Pixels) -> tuple[np.ndarray, dict[int, list[float]]]:
    """The lead's pixels by the body each is in, joined edge to edge or corner to corner, and
    each body that joins no other, with a point in it. Lead that reaches the panel's edge is
    joined by the frame round it. A body counts where it's `LOOSE_SHARE` of a lead wide
    somewhere: a hair of lead between two outlines is no line, and a pixel can't show one."""
    from scipy import ndimage

    lead = drawn.ids == 0
    labels, count = ndimage.label(lead, structure=np.ones((3, 3)))
    if not count:
        return labels, {}
    half = ndimage.distance_transform_edt(np.pad(lead, 1))[1:-1, 1:-1]
    index = np.arange(1, count + 1)
    widest = np.atleast_1d(ndimage.maximum(half, labels, index))
    spots = ndimage.maximum_position(half, labels, index)
    outside = ndimage.binary_dilation(drawn.ids < 0, structure=np.ones((3, 3)))
    rim = np.zeros_like(lead)
    rim[0, :] = rim[-1, :] = rim[:, 0] = rim[:, -1] = True
    at_edge = np.atleast_1d(ndimage.maximum(rim | outside, labels, index)).astype(bool)
    real = 2.0 * widest * drawn.mm >= LOOSE_SHARE * found.lead_mm
    loose = [k for k in range(count) if real[k] and not at_edge[k]]
    if not (real & at_edge).any() and loose:
        # With no lead at the edge at all, the widest body is the one the rest should join.
        loose.remove(max(loose, key=lambda k: float(widest[k])))
    return labels, {k + 1: drawn.at(float(spots[k][0]), float(spots[k][1])) for k in loose}


def loose_differ(
    found: Map, drawn: Pixels, ends: list[dict[str, Any]], rings: list[dict[str, Any]]
) -> list[str]:
    """Where the outlines and the pixels differ on lead that joins no other, each by place:
    a line inside a piece, or the lead round a surrounded piece, with none of its pixels in
    such a body; and a body in the pixels that lies in neither. Both count the same thing, by
    where it is and not by how many: two lines a pixel apart are one body, and each is found
    in it. A line inside a piece thinner than `LOOSE_SHARE` of a lead isn't one a pixel shows,
    so it isn't looked for, though it's still flagged."""
    labels, loose = lead_bodies(found, drawn)
    x0, _, _, y1 = found.panel.bounds
    slack = 2.0 * drawn.mm
    holes = []
    for end in ends:
        if end["kind"] != "island":
            continue
        # Shown as a body of its own where it's wide enough for a pixel, and further than two
        # pixels from any other lead: nearer, the pixels join the two.
        piece = found.piece(end["piece"])
        edges = [piece.shape.exterior, *piece.shape.interiors]
        apart_mm = min(
            float(end["shape"].distance(edge))
            for edge in edges
            if shapely.Polygon(edge).symmetric_difference(end["shape"]).area > 1e-9
        )
        shown = (
            piecemap.widest(end["shape"], 0.02)[0] >= LOOSE_SHARE * found.lead_mm + slack
            and apart_mm >= slack
        )
        holes.append((end["shape"], shown, f"the line inside piece {end['piece']}"))
    for ring in rings:
        piece = found.piece(ring["piece"])
        for inner in piece.shape.interiors:
            hole = shapely.Polygon(inner)
            if any(found.piece(n).shape.intersects(hole) for n in ring["holds"]):
                holes.append((hole, True, f"the lead round piece {ring['holds'][0]}"))
    differs = []
    seen: set[int] = set()
    for hole, shown, name in holes:
        window, inside = piecemap._cover(hole.buffer(slack), x0, y1, drawn.mm, labels.shape)
        mine = set(np.unique(labels[window][inside]).tolist()) & set(loose)
        seen |= mine
        if shown and not mine:
            at = shapely.get_coordinates(hole.representative_point())[0]
            differs.append(
                f"{name}, round {piecemap.said_at(at)}, joins no other by the outlines, and "
                "the map's pixels have no such body of lead there"
            )
    for body in sorted(set(loose) - seen):
        differs.append(
            f"a body of lead round {piecemap.said_at(loose[body])} joins no other by the "
            "map's pixels, and by the outlines it's neither a line inside a piece nor the lead "
            "round a surrounded one"
        )
    return differs


def gaps(found: Map, piece: Piece, reach: float | None = None) -> list[dict[str, Any]]:
    """Each gap in a piece narrower than twice `reach` and open at one end only, which is a lead
    line that ends in the piece's glass: where it ends, which way it runs, how long and wide it
    is, and its shape. Deepest first. A gap open at both ends is a line that goes somewhere."""
    lead = found.lead_mm
    reach = lead if reach is None else reach
    solid, lone = solid_of(found, piece)
    rim = solid.boundary.buffer(lead * 1e-3)
    others = [o.shape for o in found.pieces if o.id != piece.id]
    rows: list[dict[str, Any]] = []
    for gap in piecemap.polygons(piecemap.closed(solid, reach).difference(solid)):
        if gap.area < 0.05 * lead * lead:
            continue
        if any(o.intersects(gap) and o.intersection(gap).area > 0.05 * gap.area for o in others):
            continue  # another piece lies in it: it's a notch, with glass in it
        mouths = _stretches(gap.boundary.difference(rim), 0.05 * lead)
        if len(mouths) != 1:
            continue
        mouth = mouths[0]
        points = shapely.get_coordinates(gap.exterior)
        far = shapely.distance(shapely.points(points), mouth)
        tip = points[int(np.argmax(far))]
        depth = float(far.max())
        width = float(gap.area / depth) if depth > 0 else 0.0
        if width < LINE_SHARE * lead:
            continue  # a hair along a curved side, where its points' chords were filled
        end = gap.intersection(shapely.Point(tip).buffer(1.2 * max(width, 0.2 * lead)))
        end_at = shapely.get_coordinates(end.centroid)[0] if not end.is_empty else tip
        ring = gap.intersection(
            shapely.Point(tip).buffer(3.5 * lead).difference(shapely.Point(tip).buffer(1.5 * lead))
        )
        back = (
            shapely.get_coordinates(ring.centroid)[0]
            if not ring.is_empty and ring.area > 0
            else shapely.get_coordinates(mouth.interpolate(0.5, normalized=True))[0]
        )
        rows.append(
            {
                "piece": piece.id,
                "kind": "slit",
                "at": piecemap.xy(tip),
                "end": [float(end_at[0]), float(end_at[1])],
                "way": [float(v) for v in _unit(np.asarray(end_at) - np.asarray(back))],
                "from": piecemap.xy(shapely.get_coordinates(mouth.interpolate(0.5, True))[0]),
                "length_mm": round(depth, 3),
                "width_mm": round(width, 3),
                "shape": gap,
            }
        )
    for hole in lone:
        box = hole.minimum_rotated_rectangle
        corners = shapely.get_coordinates(box)[:4]
        sides = [float(np.linalg.norm(corners[k] - corners[(k + 1) % 4])) for k in range(2)]
        long = 0 if sides[0] >= sides[1] else 1
        a = (corners[long] + corners[(long + 3) % 4]) / 2
        b = (corners[(long + 1) % 4] + corners[(long + 2) % 4]) / 2
        rows.append(
            {
                "piece": piece.id,
                "kind": "island",
                "at": piecemap.xy(a),
                "end": [float(a[0]), float(a[1])],
                "way": [float(v) for v in _unit(a - b)],
                "from": piecemap.xy(b),
                "length_mm": round(max(sides), 3),
                "width_mm": round(float(hole.area / max(max(sides), 1e-9)), 3),
                "shape": hole,
            }
        )
    return sorted(rows, key=lambda r: -r["length_mm"])


def every_gap(found: Map) -> dict[int, list[dict[str, Any]]]:
    """Each piece's gaps, read once."""
    return {piece.id: gaps(found, piece) for piece in found.pieces}


def dead_ends(
    found: Map, read: dict[int, list[dict[str, Any]]] | None = None
) -> list[dict[str, Any]]:
    """Every dead end in the map: each piece's gaps and lone holes, from `DEAD_END` leads deep."""
    least = DEAD_END * found.lead_mm
    read = every_gap(found) if read is None else read
    return [
        row
        for rows in read.values()
        for row in rows
        if row["length_mm"] >= least or row["kind"] == "island"
    ]


def enclosed(found: Map, row: dict[str, Any]) -> tuple[bool, float]:
    """The second reading of one dead end: rays from the line's end, ahead and to both sides,
    each meeting its own piece's glass before any other's. With the furthest it had to go."""
    piece = found.piece(row["piece"])
    if row["kind"] == "island":
        inside = shapely.Polygon(piece.shape.exterior).contains(row["shape"])
        return bool(inside), 0.0
    reach = 3.0 * max(found.lead_mm, row["width_mm"])
    start = np.asarray(row["end"])
    way = np.asarray(row["way"])
    others = shapely.unary_union([o.shape for o in found.pieces if o.id != piece.id])
    furthest = 0.0
    for degrees in (-90.0, -45.0, 0.0, 45.0, 90.0):
        turn = math.radians(degrees)
        along = np.array(
            [
                way[0] * math.cos(turn) - way[1] * math.sin(turn),
                way[0] * math.sin(turn) + way[1] * math.cos(turn),
            ]
        )
        ray = shapely.LineString([start, start + reach * along])
        mine = ray.intersection(piece.shape)
        if mine.is_empty:
            return False, reach
        met = float(shapely.Point(start).distance(mine))
        theirs = ray.intersection(others)
        if not theirs.is_empty and float(shapely.Point(start).distance(theirs)) < met:
            return False, met
        furthest = max(furthest, met)
    return True, furthest


def _alone_px(found: Map, drawn: Pixels) -> int:
    """How far round a lead pixel the pixels look for one piece's glass and nothing else: a
    lead and a quarter, and a pixel, so lead at one side of a usual line sees across it."""
    return max(1, round(1.25 * found.lead_mm * drawn.px_per_mm) + 1)


def _pixels_cant_show(found: Map, drawn: Pixels, row: dict[str, Any]) -> str | None:
    """Why the map's pixels can't surely show a dead end as lead alone with its piece, or None
    where they can (mqucifer/print-shop#161): the reading's resolution. It needs a lead pixel
    in the line with no other piece, and no panel's edge, in the square it looks round: so a
    line under two pixels wide may have none, and a line with another piece nearer than that
    square's corner all along has none. On a trace at a low `--dark`, lines a pixel or two
    thin beside narrow pieces read `DISAGREE` this way."""
    across = piecemap.widest(row["shape"], 0.02)[0]
    if across < 2.0 * drawn.mm:
        return (
            f"it's {across:.2f} mm wide, under two of the map's {drawn.mm:.2f} mm pixels, so "
            "they may not have it"
        )
    others = [o.shape for o in found.pieces if o.id != row["piece"]]
    rest = shapely.union_all([*others, found.panel.exterior])
    points = shapely.get_coordinates(row["shape"].segmentize(drawn.mm))
    clear = float(shapely.distance(shapely.points(points), rest).max())
    looks = (_alone_px(found, drawn) * math.sqrt(2.0) + 1.0) * drawn.mm
    if clear < looks:
        return (
            f"another piece or the panel's edge is within {clear:.2f} mm of all of it, and "
            f"the pixels look {looks:.2f} mm round a line for one piece's glass alone, so "
            "they can't show it"
        )
    return None


def _alone(found: Map, drawn: Pixels) -> tuple[np.ndarray, np.ndarray]:
    """The lead pixels with one piece's glass, and nothing else, within a lead and a quarter
    all round, and at each which piece that is."""
    from scipy import ndimage

    ids = drawn.ids
    lead = ids == 0
    radius = _alone_px(found, drawn)
    big = int(ids.max()) + 2
    # Outside the panel is its own neighbour, so lead along the edge isn't alone with a piece.
    high = np.where(ids < 0, big, ids)
    low = np.where(ids == 0, big + 1, high)
    size = 2 * radius + 1
    most = ndimage.maximum_filter(high, size=size, mode="constant", cval=big)
    least = ndimage.minimum_filter(low, size=size, mode="constant", cval=big)
    return lead & (most == least) & (most > 0) & (most < big), most


def lone_lead(found: Map, drawn: Pixels) -> list[dict[str, Any]]:
    """Dead ends by the map's pixels: each stretch of lead with one piece's glass, and nothing
    else, within a lead and a quarter all round, with how long it is and how wide at its widest."""
    from scipy import ndimage

    lead = drawn.ids == 0
    if not lead.any():
        return []
    alone, most = _alone(found, drawn)
    labels, count = ndimage.label(alone, structure=np.ones((3, 3)))
    if not count:
        return []
    half = ndimage.distance_transform_edt(np.pad(lead, 1))[1:-1, 1:-1]
    rows = []
    for k, window in enumerate(ndimage.find_objects(labels), start=1):
        mine = labels[window] == k
        height, width = mine.shape
        centre = ndimage.center_of_mass(mine)
        rows.append(
            {
                "piece": int(most[window][mine][0]),
                "at": drawn.at(window[0].start + centre[0], window[1].start + centre[1]),
                # A pixel of the stretch itself: its middle may lie in the piece it runs round.
                "on": drawn.at(
                    window[0].start + float(np.argwhere(mine)[0][0]),
                    window[1].start + float(np.argwhere(mine)[0][1]),
                ),
                "length_mm": round(math.hypot(height, width) * drawn.mm, 3),
                "across_mm": 2.0 * float(half[window][mine].max()) * drawn.mm,
            }
        )
    return rows


def _dead_ends_differ(
    found: Map,
    vector: list[dict[str, Any]],
    drawn: Pixels,
    read: dict[int, list[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Where the two readings of the dead ends differ: a long one by its outline that the
    pixels don't have, which is no answer; and a long, thin stretch of lone lead by the pixels
    that no gap in an outline accounts for, which is said, as lead beside a patch reads so too."""
    lead = found.lead_mm
    pixel_rows = lone_lead(found, drawn)
    alone, beside_piece = _alone(found, drawn)
    x0, _, _, y1 = found.panel.bounds
    all_gaps = [g for rows in read.values() for g in rows]
    differs: list[str] = []
    notes: list[str] = []
    unflagged: list[dict[str, Any]] = []
    for row in vector:
        if row["length_mm"] < (DEAD_END + 2.5) * lead:
            continue
        # By where it lies: a lead pixel alone with this piece's glass, in the dead end's own
        # shape or a pixel from it. The stretch it's part of may run on far past it, where the
        # line all but meets another line inside the piece, so its middle is no place to match
        # by: on a traced scene a dead end 5.4 mm long in a piece 115 mm wide wasn't found.
        window, inside = piecemap._cover(
            row["shape"].buffer(drawn.mm), x0, y1, drawn.mm, alone.shape
        )
        shown = bool((alone[window] & inside & (beside_piece[window] == row["piece"])).any())
        if not shown:
            both = (
                f"piece {row['piece']}'s dead end at {piecemap.said_at(row['at'])}, "
                f"{row['length_mm']:.1f} mm long and {row['width_mm']:.2f} mm wide by its "
                "outline, isn't lone lead by the pixels"
            )
            why = _pixels_cant_show(found, drawn, row)
            if why is None:
                differs.append(both)
            else:
                # The dead end is flagged by its outline, and has no second reading here.
                notes.append(f"{both}: {why}. Only its outline reads it")
    for r in pixel_rows:
        if r["length_mm"] < (DEAD_END + 2.5) * lead or r["across_mm"] > 1.5 * lead:
            continue
        # By where it lies: within two leads of a gap's own shape, whichever piece's.
        spots = shapely.MultiPoint([r["at"], r["on"]])
        near = [g for g in all_gaps if g["shape"].distance(spots) <= 2.0 * lead]
        # Along a thick line between two pieces, the lead on each side is nearer its own
        # piece: that's the line's width, which the lead's widest point reads, and no dead end.
        here = shapely.Point(r["on"])
        beside = any(
            o.id != r["piece"] and o.shape.distance(here) <= 3.0 * lead for o in found.pieces
        )
        if not near and not beside:
            unflagged.append(r)
    if unflagged:
        # No rule flags these places, so they're one line, not a note each.
        first = unflagged[0]
        notes.append(
            f"{len(unflagged)} stretch(es) of lead have one piece's glass alone round them by "
            "the pixels where no outline has a dead end, and none is flagged: the first is "
            f"{first['length_mm']:.1f} mm round {piecemap.said_at(first['at'])}, by piece "
            f"{first['piece']}. Look at the picture"
        )
    return differs, notes


# Each piece


def notches(shape: Any, lead: float) -> list[dict[str, Any]]:
    """Each notch in a piece: a part of its hull that isn't the piece, with how deep it goes
    from its mouth, the stretch of the hull's edge across it, and how wide that mouth is.
    Deepest for its mouth first."""
    outline = shapely.Polygon(shape.exterior)
    hull = outline.convex_hull
    rim = hull.exterior.buffer(lead * 1e-3)
    rows = []
    for part in piecemap.polygons(hull.difference(outline)):
        mouth = part.boundary.intersection(rim)
        if mouth.is_empty or mouth.length <= 0:
            continue
        points = shapely.get_coordinates(part.exterior)
        far = shapely.distance(shapely.points(points), mouth)
        depth = float(far.max())
        if depth <= 0:
            continue
        rows.append(
            {
                "depth_mm": depth,
                "mouth_mm": float(mouth.length),
                "at": piecemap.xy(points[int(np.argmax(far))]),
                "shape": part,
                "mouth": mouth,
            }
        )
    return sorted(rows, key=lambda r: -r["depth_mm"] / r["mouth_mm"])


def _notch_by_pixels(notch: dict[str, Any], mm: float) -> tuple[float, float]:
    """A notch's depth by pixels, and that reading's resolution. The depth is the furthest any
    pixel's centre in it is from its mouth. The resolution is how far short of the outline's
    depth that can be: a pixel's centre lies within half a pixel's diagonal of any point, so
    what's left of the notch that far in from its edge is surely seen, to that diagonal; a
    part too thin for that, a spike or a hair, may have no pixel in it at all. At least a
    pixel."""
    x0, y0, x1, y1 = notch["shape"].bounds
    size = (int(math.ceil((y1 - y0) / mm)) + 2, int(math.ceil((x1 - x0) / mm)) + 2)
    left, top = x0 - mm, y1 + mm
    window, inside = piecemap._cover(notch["shape"], left, top, mm, size)
    rows, cols = np.nonzero(inside)
    depth = 0.0
    if len(rows):
        xs = left + (window[1].start + cols + 0.5) * mm
        ys = top - (window[0].start + rows + 0.5) * mm
        depth = float(shapely.distance(shapely.points(xs, ys), notch["mouth"]).max())
    reach = mm * math.sqrt(0.5)
    core = notch["shape"].buffer(-reach)
    seen = 0.0
    if not core.is_empty:
        points = shapely.get_coordinates(core)
        seen = max(0.0, float(shapely.distance(shapely.points(points), notch["mouth"]).max()))
    return depth, max(mm, notch["depth_mm"] - max(0.0, seen - reach))


def neck_by_pixels(
    shape: Any, place: dict[str, Any], limit: float, map_mm: float
) -> dict[str, Any]:
    """A narrow place's width again: twice the distance from its middle to the nearest pixel
    that isn't the piece's glass, on pixels drawn round the place. It's the same line across
    as the nearest points facing each other, since the nearest edge to the middle of the
    narrowest place is one of its two ends. With the pixel's size, and the reading's
    resolution: a pixel's width at each end, and half a pixel's diagonal for where the middle
    lies in its own pixel."""
    from scipy import ndimage

    width = float(place["width_mm"])
    a, b = np.asarray(place["from"], dtype=float), np.asarray(place["to"], dtype=float)
    middle = (a + b) / 2.0
    # Wide enough to read a clear width well past the limit, and no wider.
    reach = max(2.0 * limit, width)
    mm = max(min(map_mm, min(width, limit) / NECK_PX_ACROSS), 2.0 * reach / NECK_PX_MOST)
    half = reach + 4.0 * mm
    count = 2 * int(math.ceil(half / mm)) + 1
    left, top = middle[0] - count * mm / 2.0, middle[1] + count * mm / 2.0
    glass = np.zeros((count, count), dtype=bool)
    window, inside = piecemap._cover(shape, left, top, mm, (count, count))
    glass[window] = inside
    centre = count // 2
    resolution = (2.0 + math.sqrt(2.0)) * mm
    if not glass[centre, centre]:
        return {"width_mm": 0.0, "px_mm": mm, "resolution_mm": resolution, "clear": None}
    # A ring that isn't glass round the window, so a window of nothing but glass reads to it.
    far = ndimage.distance_transform_edt(np.pad(glass, 1))[1:-1, 1:-1]
    # To the nearest pixel that isn't glass, centre to centre: half a pixel less to its edge.
    reached = float(far[centre, centre])
    return {
        "width_mm": max(0.0, 2.0 * reached - 1.0) * mm,
        "px_mm": mm,
        "resolution_mm": resolution,
        # Nothing but glass as far as the window goes: wider than this, however wide.
        "clear": round(half, 3) if reached > centre else None,
    }


def _sides(work: Any, place: dict[str, Any], step: float) -> list[Any]:
    """What a cut straight across a narrow place leaves, largest first."""
    a, b = np.asarray(place["from"]), np.asarray(place["to"])
    way = (b - a) / max(float(np.hypot(*(b - a))), 1e-9)
    bar = shapely.LineString([a - 0.05 * way, b + 0.05 * way])
    return piecemap.polygons(work.difference(bar.buffer(max(step, 0.01), cap_style="flat")))


def _is_neck(work: Any, place: dict[str, Any] | None, step: float, limits: Limits) -> bool:
    """Whether a narrow place is a neck: under `--min-neck`, and a cut across it leaves glass
    `--min-width` wide or wider on both sides."""
    if place is None or not under(place["width_mm"], limits.min_neck):
        return False
    parts = _sides(work, place, step)
    return len(parts) >= 2 and any(
        not under(piecemap.widest(part, 0.05)[0], limits.min_width) for part in parts[1:]
    )


def necks(shape: Any, step: float, limits: Limits, map_mm: float) -> dict[str, Any]:
    """A piece's narrowest place straight across, and whether it's a neck.

    **The reading** is `layout`'s nearest points facing each other: the narrowest place, as
    the rule is said. It looks past each spur, a place under `--min-neck` with glass
    `--min-width` wide on one side of it only, and a neck is one with it on both, by a cut
    across the place.

    **The second check**, of a place under `--min-neck`, is `neck_by_pixels`: twice the
    pixels' distance to the edge at the place's middle, the same line across. The two are the
    same within the pixels' resolution. Further apart with the limit between them, there's no
    answer; further apart on one side of it, they agree on the rule and the gap is said.

    **Rays square to each edge are a note.** Across a slanted neck they run the slant's long
    way: on a traced scene one read 1.022 mm by the nearest points and 1.828 mm by rays, at
    the same place, and every check after a split was `DISAGREE` (mqucifer/print-shop#161).
    That's another line's length, so it's said and settles nothing, as it is where the rays'
    narrowest is another place altogether."""
    work, spurs, neck = shape, 0, False
    while True:
        widths = layout.narrowest(shapely.MultiPolygon([work]), step)
        place = widths["facing"]
        if place is None or not under(place["width_mm"], limits.min_neck):
            break
        if _is_neck(work, place, step, limits):
            neck = True
            break
        parts = _sides(work, place, step)
        if len(parts) < 2 or spurs >= SPURS:
            break
        work, spurs = parts[0], spurs + 1
    found: dict[str, Any] = {
        "place": place,
        "spurs": spurs,
        "neck": neck,
        "tips": len(widths["tips"]),
        "by_rays_mm": None,
        "by_pixels": None,
        "differs": None,
        "noted": None,
        "elsewhere": None,
    }
    if place is None or not neck:
        # Over the limit there's no neck to judge, and the widest way across a wide piece is
        # no one place for two readings to agree on. A place under it that's a spur or a tip
        # isn't flagged, so a second reading of it has no rule to settle and isn't said.
        return found
    # The same place is the same two sides: a ray whose ends are each within reach of one
    # of the place's own. A ray through its middle to sides further off is another width.
    reach = max(1.0, place["width_mm"])

    def across(ray: dict[str, Any]) -> bool:
        ends = (place["from"], place["to"])
        return any(
            math.dist(ray["from"], a) <= reach and math.dist(ray["to"], b) <= reach
            for a, b in (ends, ends[::-1])
        )

    again = neck_by_pixels(shape, place, limits.min_neck, map_mm)
    found["by_pixels"] = again
    by_pixels = (
        f"no edge within {again['clear']:g} mm of its middle by its pixels' distance to the edge"
        if again["clear"]
        else f"{again['width_mm']:.3f} mm by twice its pixels' distance to the edge"
    )
    how = apart(
        place["width_mm"], again["width_mm"], again["resolution_mm"], limits.min_neck, least=True
    )
    if how == "split":
        found["differs"] = (
            f"{place['width_mm']:.3f} mm by the nearest points facing each other, under "
            f"--min-neck {limits.min_neck:g}, and {by_pixels}, "
            f"not under it, both at {piecemap.said_at(place['at'])}, and further "
            f"apart than the {again['resolution_mm']:.3f} mm those pixels can tell"
        )
    elif how == "off":
        found["noted"] = (
            f"{place['width_mm']:.3f} mm by the nearest points facing each other and "
            f"{by_pixels}, at {piecemap.said_at(place['at'])}: further apart than the "
            f"{again['resolution_mm']:.3f} mm those pixels can tell, and both under "
            f"--min-neck {limits.min_neck:g}"
        )
    here = [r for r in widths["necks"] if across(r)]
    if here:
        found["by_rays_mm"] = float(min(here, key=lambda r: r["width"])["width"])
    elif widths["rays"] is not None:
        found["elsewhere"] = (
            f"{widths['rays']['width_mm']:.3f} mm at {piecemap.said_at(widths['rays']['at'])}"
        )
    return found


def build_refuses(found: Map, limits: Limits) -> dict[int, str]:
    """Each piece `pieces build` would refuse at the lip it will be given, for want of one
    opening or a pane, with the build's own words: a waist the lip closes over and the line
    to split it along, an opening pinched in two, or none left. It's the build's own plan,
    made here, so the check and the build can't disagree: a run learned each of these only
    from a build's refusal (mqucifer/print-shop#161). A line inside a piece, or a piece round
    another, is another flag's."""
    import re

    from .pieces_build import plan

    assert limits.lip is not None
    least = limits.least_lip if limits.least_lip is not None else limits.lip
    _, wrong = plan(found, (limits.lip, least), limits.play, limits.fit, limits.min_visible)
    found_wrong: dict[int, str] = {}
    for line in wrong:
        named = re.match(r"piece (\d+), ", line)
        if named and any(
            word in line for word in ("no one opening", "has no pane", "of its pane visible")
        ):
            found_wrong[int(named.group(1))] = line
    return found_wrong


def openings_by_pixels(drawn: Pixels, far: np.ndarray, number: int, lip: float) -> int:
    """The same by the map's pixels: those of the piece further than the lip from its edge,
    counted where they join, each lot `LEAST_OPENING` wide at its widest."""
    from scipy import ndimage

    window = ndimage.find_objects((drawn.ids == number).astype(np.int8))[0]
    if window is None:
        return 0
    mine = drawn.ids[window] == number
    # A pixel's distance is to the nearest other pixel's centre: half a pixel less to the edge.
    deep = (far[window] - 0.5) * drawn.mm
    labels, count = ndimage.label(mine & (deep > lip), structure=np.ones((3, 3)))
    if not count:
        return 0
    most = np.atleast_1d(ndimage.maximum(deep, labels, np.arange(1, count + 1)))
    return int((2.0 * (most - lip) >= LEAST_OPENING).sum())


def thin_lead(
    found: Map, limits: Limits, map_mm: float, only: list[int] | None
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Each two pieces with lead between them narrower than `--min-lead`, at the nearest they
    come. As flags, narrowest first, with where the pixels' reading of the same place differs,
    and what's worth a note. Lead between a piece and the panel's edge isn't read: the
    frame's border stands outside it, so the wall there is the border and that lead."""
    least = limits.min_lead
    places: list[tuple[float, Piece, Piece, np.ndarray, np.ndarray]] = []
    shapes = [p.shape for p in found.pieces]
    tree = shapely.STRtree(shapes)
    for a, b in tree.query(shapes, predicate="dwithin", distance=least).T:
        if a >= b:
            continue
        one, two = found.pieces[int(a)], found.pieces[int(b)]
        if only is not None and one.id not in only and two.id not in only:
            continue
        ends = shapely.get_coordinates(shapely.shortest_line(one.shape, two.shape))
        places.append((float(math.dist(ends[0], ends[1])), one, two, ends[0], ends[1]))
    flags: list[dict[str, Any]] = []
    differs: list[str] = []
    notes: list[str] = []
    lead_shape = found.lead()
    for width, mine, other, start, end in sorted(places, key=lambda row: row[0]):
        if not under(width, least):
            continue
        middle = (start + end) / 2.0
        between = f"pieces {mine.id} and {other.id}"
        fix = (
            f"`pieces widen MAP {mine.id} {other.id}` moves each piece's edge back so the "
            f"line is {least:g} mm there and keeps its path"
        )
        if mine.colour == other.colour:
            # A merge takes the line out, so it's a fix only where the two are one colour
            # and the picture doesn't mean the line.
            fix += (
                f"; or, if the picture doesn't mean the line, `pieces merge MAP {mine.id} "
                f"{other.id}` joins the two, both {mine.colour}"
            )
        flags.append(
            {
                "flag": "thin-lead",
                "piece": mine.id,
                "figure": width,
                "limit": least,
                "at": piecemap.xy(middle),
                "said": f"the lead between {between} is {said(width, least, 2)} mm at "
                f"{piecemap.said_at(middle)}, under the {least:g} mm a nozzle prints: {fix}",
            }
        )
        if len(flags) > THIN_SAID:
            continue  # counted, and read by its outline: the pixels read the first of them
        place = {"width_mm": width, "from": start, "to": end}
        window = shapely.Point(middle).buffer(3.0 * least + width, cap_style="square")
        again = neck_by_pixels(lead_shape.intersection(window), place, least, map_mm)
        how = apart(width, again["width_mm"], again["resolution_mm"], least, least=True)
        both = (
            f"the lead between {between} at {piecemap.said_at(middle)}: {width:.3f} mm by "
            f"the nearest the two outlines come and {again['width_mm']:.3f} mm by twice its "
            "pixels' distance to the glass"
        )
        if how == "split":
            differs.append(
                f"{both}, on opposite sides of --min-lead {least:g} and further apart than "
                f"the {again['resolution_mm']:.3f} mm those pixels can tell"
            )
        elif how == "off":
            notes.append(
                f"{both}, further apart than the {again['resolution_mm']:.3f} mm those pixels "
                f"can tell, and both under --min-lead {least:g}"
            )
    return flags, differs, notes


def piece_row(
    found: Map,
    piece: Piece,
    limits: Limits,
    step: float,
    lines: list[dict[str, Any]],
    map_mm: float,
) -> dict[str, Any]:
    """One piece's figures, by its outline: area, widest, narrowest straight across, deepest
    notch, and its colour's run. `lines` are its dead ends, which aren't notches too."""
    shape = piece.shape
    across, across_at = piecemap.widest(shape)
    read = necks(shape, step, limits, map_mm)
    again = read["by_pixels"]
    neck = read["place"]
    whole = shapely.union_all([shape, *[row["shape"] for row in lines]], grid_size=1e-6)
    whole = max(piecemap.polygons(whole), key=lambda p: p.area)
    found_notches = notches(whole, found.lead_mm)
    deep = [n for n in found_notches if n["depth_mm"] > limits.min_neck]
    notch = deep[0] if deep else None
    paint = piece.paint or {}
    x0, y0, x1, y1 = shape.bounds
    return {
        "id": piece.id,
        "colour": piece.colour,
        "area_mm2": float(shape.area),
        "centre": piecemap.xy(shapely.get_coordinates(shape.representative_point())[0]),
        "box": [round(v, 3) for v in (x0, y0, x1, y1)],
        "widest_mm": across,
        "widest_at": across_at,
        "narrowest_mm": neck["width_mm"] if neck else None,
        "narrowest_at": neck["at"] if neck else None,
        "narrowest_from": neck["from"] if neck else None,
        "narrowest_to": neck["to"] if neck else None,
        "narrowest_by_pixels_mm": again["width_mm"] if again and not again["clear"] else None,
        "narrowest_px_mm": again["resolution_mm"] if again else None,
        "narrowest_by_rays_mm": read["by_rays_mm"],
        "widths_differ": read["differs"],
        "widths_noted": read["noted"],
        "rays_elsewhere": read["elsewhere"],
        "sharp_tips": read["tips"],
        "spurs": read["spurs"],
        "neck": read["neck"],
        "notch_depth_mm": notch["depth_mm"] if notch else None,
        "notch_mouth_mm": notch["mouth_mm"] if notch else None,
        "notch_at": notch["at"] if notch else None,
        "notch": notch,
        "solidity": float(shape.area / shapely.Polygon(shape.exterior).convex_hull.area),
        "colour_run": paint.get("run"),
        "colour_run_fitted": paint.get("run_fitted"),
        "flags": [],
    }


def flag_piece(row: dict[str, Any], limits: Limits) -> list[dict[str, Any]]:
    """The rules one piece breaks, each with its figure, its limit and where."""
    number = row["id"]
    flags: list[dict[str, Any]] = []

    def flag(name: str, figure: float, limit: float, at: list[float], words: str) -> None:
        flags.append(
            {
                "flag": name,
                "piece": number,
                "figure": figure,
                "limit": limit,
                "at": at,
                "said": words,
            }  # fmt: skip
        )

    sliver = under(row["widest_mm"], limits.min_width)
    if sliver:
        flag(
            "sliver", row["widest_mm"], limits.min_width, row["widest_at"],
            f"piece {number} is a sliver: {said(row['widest_mm'], limits.min_width, 2)} mm at "
            f"its widest, under {limits.min_width:g} mm",
        )  # fmt: skip
    if under(row["area_mm2"], limits.min_area):
        flag(
            "small", row["area_mm2"], limits.min_area, row["centre"],
            f"piece {number} is too small to handle: {said(row['area_mm2'], limits.min_area, 1)} "
            f"mm², under {limits.min_area:g} mm²",
        )  # fmt: skip
    narrow = row["narrowest_mm"]
    if not sliver and narrow is not None and row["neck"]:
        flag(
            "neck", narrow, limits.min_neck, row["narrowest_at"],
            f"piece {number} has a thin neck: {said(narrow, limits.min_neck, 2)} mm across at "
            f"{piecemap.said_at(row['narrowest_at'])}, under {limits.min_neck:g} mm, with glass "
            f"{limits.min_width:g} mm wide or wider on both sides of it"
            + (
                f". Twice its pixels' distance to the edge there reads "
                f"{row['narrowest_by_pixels_mm']:.2f} mm"
                if row.get("narrowest_by_pixels_mm") is not None
                else ""
            ),
        )  # fmt: skip
    if row["notch_depth_mm"] is not None:
        ratio = row["notch_depth_mm"] / row["notch_mouth_mm"]
        if over(ratio, limits.max_notch):
            flag(
                "notch", ratio, limits.max_notch, row["notch_at"],
                f"piece {number} has a deep notch: {row['notch_depth_mm']:.1f} mm deep to "
                f"{piecemap.said_at(row['notch_at'])} from a mouth {row['notch_mouth_mm']:.1f} "
                f"mm wide, {said(ratio, limits.max_notch, 2)} times its mouth, over "
                f"{limits.max_notch:g}",
            )  # fmt: skip
    run = row["colour_run"]
    if run is not None and over(run, limits.max_colour_run):
        flag(
            "gradient", run, limits.max_colour_run, row["centre"],
            f"piece {number}'s colour runs across it: {said(run, limits.max_colour_run, 0)} of "
            f"255 from one end to the other in the picture, over {limits.max_colour_run:g}",
        )  # fmt: skip
    return flags


@dataclass
class Reading:
    """A map as `pieces check` reads it."""

    rows: list[dict[str, Any]]
    flags: list[dict[str, Any]]
    dead_ends: list[dict[str, Any]]
    numbers: dict[str, Any]
    seconds: list[str]
    differs: list[str]
    drawn: Pixels
    notes: list[str]


def quick(found: Map, drawn: Pixels | None = None) -> dict[str, Any]:
    """The readings every `pieces` reply gives: how many pieces, the glass share and the lead's
    widest point, each by the outlines and by the pixels."""
    drawn = drawn or piecemap.pixels(found)
    across, at = piecemap.widest(found.lead())
    by_pixels, at_pixels = piecemap.lead_widest_by_pixels(drawn)
    return {
        "pieces_count": len(found.pieces),
        "glass_share_pct": piecemap.share(found),
        "glass_share_by_pixels_pct": piecemap.share_by_pixels(drawn),
        "lead_widest_mm": across,
        "lead_widest_at": at,
        "lead_widest_by_pixels_mm": by_pixels,
        "lead_widest_by_pixels_at": at_pixels,
        "px_per_mm": drawn.px_per_mm,
    }


def quick_differs(found: Map, figures: dict[str, Any]) -> list[str]:
    """Where the outlines and the pixels differ by more than a pixel's width allows."""
    mm = 1.0 / figures["px_per_mm"]
    differs = []
    rims = sum(p.shape.length for p in found.pieces)
    allowed = SHARE_OFF * 100.0 * rims * mm / found.panel.area + 0.05
    gap = abs(figures["glass_share_pct"] - figures["glass_share_by_pixels_pct"])
    if gap > allowed:
        differs.append(
            f"the glass share is {figures['glass_share_pct']:.2f}% by the outlines and "
            f"{figures['glass_share_by_pixels_pct']:.2f}% by the pixels, more than the "
            f"{allowed:.2f} points a pixel's width allows"
        )
    gap = abs(figures["lead_widest_mm"] - figures["lead_widest_by_pixels_mm"])
    if gap > PIXELS_OFF * mm:
        differs.append(
            f"the lead's widest point is {figures['lead_widest_mm']:.2f} mm by the largest "
            f"circle that fits and {figures['lead_widest_by_pixels_mm']:.2f} mm by the pixels"
        )
    return differs


def quick_said(figures: dict[str, Any]) -> str:
    return (
        f"{figures['pieces_count']} piece(s), glass share {figures['glass_share_pct']:.2f}%, the "
        f"lead's widest point {figures['lead_widest_mm']:.2f} mm at "
        f"{piecemap.said_at(figures['lead_widest_at'])}"
    )


def repainted(found: Map, pieces: list[Piece], image: str | None = None) -> str | None:
    """Each piece with no colour run of its own, one a fix has changed, given the picture's
    colour and its run over the piece as it is now: a merge keeps one piece's colour, so two
    colours joined would read as one and no gradient would show (mqucifer/print-shop#172).
    The pieces keep what's read, so a fix's new map has it. With what to say where the
    picture can't be read; a map from rules has no picture, and nothing to say."""
    unread = [p for p in pieces if not (p.paint and "run" in p.paint)]
    if not unread or not (image or found.source.get("image")):
        return None
    try:
        picture = picture_of(found, image)
    except NoAnswer as exc:
        return (
            f"{len(unread)} piece(s) a fix has changed, "
            f"{piecemap.numbered([p.id for p in unread])}, have no colour run read, so no "
            f"gradient is read on them: {exc}"
        )
    for piece in unread:
        paint = picture.paint(piece.shape)
        if paint is not None:
            piece.paint = paint
    return None


def read_map(
    found: Map, limits: Limits, only: list[int] | None = None, image: str | None = None
) -> Reading:
    """Every figure and flag of a map, or with `only`, the map's own figures and those pieces'.
    `image` is the picture it was traced from, where that isn't the one its source names."""
    from scipy import ndimage

    drawn = piecemap.pixels(found)
    figures = quick(found, drawn)
    differs = quick_differs(found, figures)
    pieces = [p for p in found.pieces if only is None or p.id in only]
    unpainted = repainted(found, pieces, image)
    step = layout.spacing({str(p.id): shapely.MultiPolygon([p.shape]) for p in pieces})
    read = every_gap(found) if only is None else {p.id: gaps(found, p) for p in pieces}
    ends = dead_ends(found, read)
    rows = [
        piece_row(found, p, limits, step, [e for e in ends if e["piece"] == p.id], drawn.mm)
        for p in pieces
    ]
    flags: list[dict[str, Any]] = []
    held = 0
    for end in ends:
        ok, _ = enclosed(found, end)
        held += ok
        what = (
            f"a lead line inside piece {end['piece']} is joined to no other: "
            f"{end['length_mm']:.1f} mm long, round {piecemap.said_at(end['at'])}"
            if end["kind"] == "island"
            else f"a lead line ends in piece {end['piece']}'s glass at "
            f"{piecemap.said_at(end['at'])}: a dead end {end['length_mm']:.1f} mm long and "
            f"{end['width_mm']:.1f} mm wide, from {piecemap.said_at(end['from'])}"
        )
        flags.append(
            {
                "flag": "dead-end",
                "piece": end["piece"],
                "figure": end["length_mm"],
                "limit": DEAD_END * found.lead_mm,
                "at": end["at"],
                "said": what,
            }  # fmt: skip
        )
        if not ok:
            differs.append(
                f"the dead end at {piecemap.said_at(end['at'])} isn't closed in by piece "
                f"{end['piece']}'s glass by rays from its end"
            )
    notes: list[str] = []
    if only is None:
        more, notes = _dead_ends_differ(found, ends, drawn, read)
        differs += more
    if unpainted:
        notes.append(unpainted)
    rings = surrounding(found, pieces)
    for ring in rings:
        inside = piecemap.numbered(ring["holds"])
        flags.append(
            {
                "flag": "surrounds",
                "piece": ring["piece"],
                "figure": float(len(ring["holds"])),
                "limit": 0.0,
                "at": ring["at"],
                "said": f"piece {ring['piece']} surrounds piece {inside}: glass isn't cut with a "
                f"hole in it, and the lead round piece {inside} joins no other line, so the frame "
                f"would be two bodies. `pieces split MAP {ring['piece']} --line` from that lead, "
                f"near {piecemap.said_at(ring['at'])}, to piece {ring['piece']}'s outer edge "
                "opens it",
            }  # fmt: skip
        )
    if only is None:
        differs += loose_differ(found, drawn, ends, rings)
    # Each piece's widest point and area again, by its pixels.
    glass = np.pad(drawn.ids > 0, 1)
    far = ndimage.distance_transform_edt(glass)[1:-1, 1:-1]
    ids = [r["id"] for r in rows]
    by_pixels = dict(zip(ids, np.atleast_1d(ndimage.maximum(far, drawn.ids, ids)), strict=True))
    counted = dict(zip(ids, np.atleast_1d(ndimage.sum(drawn.ids > 0, drawn.ids, ids)), strict=True))
    refused = build_refuses(found, limits) if limits.lip is not None else {}
    for row in rows:
        row["widest_by_pixels_mm"] = max(0.0, 2.0 * float(by_pixels[row["id"]]) - 1.0) * drawn.mm
        row["area_by_pixels_mm2"] = float(counted[row["id"]]) * drawn.mm**2
        if limits.lip is not None:
            refusal = refused.get(row["id"])
            # The pixels of the piece further than the lip from its edge, by true distance:
            # they hold at least what a build's opening holds, as its lip keeps a corner a
            # corner and so covers more. A pixel less of lip, so its own coarseness can't
            # close an opening.
            seen = openings_by_pixels(drawn, far, row["id"], limits.lip - drawn.mm)
            row["openings_by_pixels"] = seen
            if refusal is None and seen == 0:
                differs.append(
                    f"piece {row['id']} has an opening at a lip of {limits.lip:g} mm as "
                    "`pieces build` would build it, and none by its pixels further than the "
                    "lip from its edge"
                )
            if refusal is not None:
                row["no_opening"] = True
                flags.append(
                    {
                        "flag": "no-opening",
                        "piece": row["id"],
                        "figure": float(seen),
                        "limit": 1.0,
                        "at": row["centre"],
                        "said": refusal,
                    }  # fmt: skip
                )
                if seen >= 1 and "leaves it no" in refusal:
                    notes.append(
                        f"piece {row['id']}: by true distance a lip of {limits.lip:g} mm "
                        f"leaves {seen} opening(s) {LEAST_OPENING:g} mm wide. A build's lip "
                        "keeps each corner a corner, so past the end of a slit in the piece's "
                        "edge it covers more, and that's what leaves none"
                    )
        if abs(row["widest_by_pixels_mm"] - row["widest_mm"]) > PIXELS_OFF * drawn.mm:
            differs.append(
                f"piece {row['id']} is {row['widest_mm']:.2f} mm at its widest by the largest "
                f"circle that fits and {row['widest_by_pixels_mm']:.2f} mm by its pixels"
            )
        if row["widths_differ"]:
            differs.append(f"piece {row['id']}'s narrowest place: {row['widths_differ']}")
        elif row["widths_noted"]:
            notes.append(f"piece {row['id']}'s narrowest place: {row['widths_noted']}")
        rays = row["narrowest_by_rays_mm"]
        if row["neck"] and rays is not None and rays - row["narrowest_mm"] > 0.005:
            notes.append(
                f"piece {row['id']}'s neck at {piecemap.said_at(row['narrowest_at'])}: Rays "
                f"square to its edges read {rays:.2f} mm there, "
                f"{rays - row['narrowest_mm']:.2f} mm more than the nearest points facing each "
                "other. On a slant they run its long way, so they measure something else, "
                "and neither confirm the neck nor differ"
            )
        elif row["neck"] and rays is None and row["rays_elsewhere"]:
            notes.append(
                f"piece {row['id']}'s neck at {piecemap.said_at(row['narrowest_at'])} is read "
                f"by the nearest points facing each other. Rays square to its edges read no "
                f"width across that place: their narrowest is {row['rays_elsewhere']}, another "
                "place, so they neither confirm it nor differ"
            )
        if row["notch"] is not None:
            again, resolution = _notch_by_pixels(row["notch"], drawn.mm)
            row["notch_depth_by_pixels_mm"] = again
            row["notch_px_mm"] = resolution
            mouth = row["notch_mouth_mm"]
            # A deeper reading by the pixels is out by a pixel at most; a shallower one by
            # whatever of the notch no pixel shows.
            allowed = resolution if again < row["notch_depth_mm"] else drawn.mm
            how = apart(
                row["notch_depth_mm"], again, allowed, limits.max_notch * mouth, least=False
            )
            both = (
                f"piece {row['id']}'s notch is {row['notch_depth_mm']:.2f} mm deep by its "
                f"outline and {again:.2f} mm by its pixels"
            )
            if how == "split":
                differs.append(
                    f"{both}, on opposite sides of {limits.max_notch:g} times its "
                    f"{mouth:.2f} mm mouth and further apart than the {allowed:.2f} mm its "
                    "pixels can tell"
                )
            over_it = over(row["notch_depth_mm"] / mouth, limits.max_notch)
            between = over_it != over(again / mouth, limits.max_notch)
            # A note only where the rule flags the notch, or the limit lies between the two.
            if how == "off" and over_it:
                notes.append(
                    f"{both}, further apart than the {allowed:.2f} mm its pixels can tell, "
                    f"and on the same side of {limits.max_notch:g} times its mouth"
                )
            elif (
                how == "same"
                and (over_it or between)
                and abs(again - row["notch_depth_mm"]) > PIXELS_OFF * drawn.mm
            ):
                notes.append(
                    (
                        f"{both}: it's too thin all along for a pixel to show, so only its "
                        "outline reads it, and it has no second check"
                        if resolution >= row["notch_depth_mm"]
                        else f"{both}: closer than the {resolution:.2f} mm its pixels can "
                        "tell, as its end is too thin for a pixel to show"
                    )
                    + (
                        f". {limits.max_notch:g} times its mouth lies between the two, and "
                        "the outline's figure is the one read"
                        if between
                        else ""
                    )
                )
        mine = flag_piece(row, limits)
        row["flags"] = sorted({f["flag"] for f in mine}, key=FLAGS.index)
        if row.pop("no_opening", False):
            row["flags"].append("no-opening")
        flags += mine
        del row["notch"]
    by_id = {r["id"]: r for r in rows}
    for ring in rings:
        if ring["piece"] in by_id:
            by_id[ring["piece"]]["flags"].insert(0, "surrounds")
    for end in ends:
        if end["piece"] in by_id and "dead-end" not in by_id[end["piece"]]["flags"]:
            by_id[end["piece"]]["flags"].insert(0, "dead-end")
    most = limits.lead(found.lead_mm)
    if only is None and over(figures["lead_widest_mm"], most):
        flags.append(
            {
                "flag": "wide-lead",
                "piece": None,
                "figure": figures["lead_widest_mm"],
                "limit": most,
                "at": figures["lead_widest_at"],
                "said": f"the lead is {said(figures['lead_widest_mm'], most, 2)} mm wide at "
                f"{piecemap.said_at(figures['lead_widest_at'])}, over {most:g} mm: a patch of "
                "lead, not a line. `pieces fill MAP --at "
                f"{figures['lead_widest_at'][0]:g},{figures['lead_widest_at'][1]:g}` turns it "
                "into glass, shared among the pieces round it",
            }  # fmt: skip
        )
    thin, more, noted = thin_lead(found, limits, drawn.mm, only)
    flags += thin
    differs += more
    notes += noted
    for one in thin:
        for row in rows:
            if row["id"] == one["piece"] and "thin-lead" not in row["flags"]:
                row["flags"].append("thin-lead")
    flags.sort(key=lambda f: (FLAGS.index(f["flag"]), f["piece"] or 0))
    seconds = [
        f"the glass share by the map's pixels, {drawn.px_per_mm:.1f} a mm: "
        f"{figures['glass_share_by_pixels_pct']:.2f}%; the lead's widest point by the furthest "
        f"lead pixel from glass: {figures['lead_widest_by_pixels_mm']:.2f} mm at "
        f"{piecemap.said_at(figures['lead_widest_by_pixels_at'])}",
        f"each piece's widest point by its pixels, a place under --min-neck by twice its "
        f"pixels' distance to the edge there, and its notch by its pixels' distance from its "
        f"mouth, each to its own resolution: {len(rows)} piece(s) read",
        f"{held} of {len(ends)} dead end(s) closed in by their own piece's glass, by rays from "
        "the line's end; and the map's pixels read for lead with one piece all round it, and "
        "for lead joined to no other",
        f"{len(thin)} place(s) where the lead is under --min-lead {limits.min_lead:g} mm, each "
        "by twice its pixels' distance to the glass at its middle",
    ]
    if limits.lip is not None:
        seconds.append(
            f"{len(refused)} piece(s) a lip of {limits.lip:g} mm leaves no one opening in, by "
            "`pieces build`'s own plan; and each piece's pixels further than the lip from its "
            "edge, which hold at least what a build's opening holds"
        )
    return Reading(rows, flags, ends, figures, seconds, differs, drawn, notes)


def flag_counts(flags: list[dict[str, Any]]) -> dict[str, int]:
    return {name: sum(1 for f in flags if f["flag"] == name) for name in FLAGS}


def counts_said(flags: list[dict[str, Any]]) -> str:
    counted = flag_counts(flags)
    return ", ".join(f"{count} {name}" for name, count in counted.items() if count) or "none"


def kept_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The flags as a map keeps them."""
    return [
        {
            "flag": f["flag"],
            "piece": f["piece"],
            "figure": round(float(f["figure"]), 4),
            "limit": f["limit"],
            "at": f["at"],
        }  # fmt: skip
        for f in flags
    ]


def kept_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            k: v
            for k, v in row.items()
            if k not in ("widths_differ", "widths_noted", "rays_elsewhere")
        }
        for row in rows
    ]


def say(found: Map, reading: Reading, report: Report, limits: Limits) -> None:
    """A reading's lines and numbers, as `pieces check` and `pieces trace` both give them."""
    figures = reading.numbers
    x0, y0, x1, y1 = found.panel.bounds
    report.fact("pieces_count", len(found.pieces), how="in the map")
    report.fact("panel_mm", [round(x1 - x0, 3), round(y1 - y0, 3)], how="the panel's size")
    report.fact(
        "glass_share_pct", figures["glass_share_pct"], "%",
        "the pieces' area over the panel's, by their outlines",
    )  # fmt: skip
    was = found.source.get("glass_share_pct")
    if isinstance(was, int | float):
        report.fact(
            "source_glass_share_pct", float(was), "%",
            f"as the map's source, {found.source.get('by', 'unnamed')}, had it",
        )  # fmt: skip
        report.fact(
            "glass_share_change_pct", figures["glass_share_pct"] - float(was), "points",
            "against its source: a fix adds glass, and never takes it into the lead",
        )  # fmt: skip
    report.fact("lead_mm", found.lead_mm, "mm", "the lead line's usual width, as the map says")
    report.fact(
        "lead_widest_mm", figures["lead_widest_mm"], "mm",
        "the largest circle that fits in the lead, at "
        f"{piecemap.said_at(figures['lead_widest_at'])}",
    )  # fmt: skip
    report.numbers["lead_widest_at"] = figures["lead_widest_at"]
    report.numbers["glass_share_by_pixels_pct"] = figures["glass_share_by_pixels_pct"]
    report.numbers["lead_widest_by_pixels_mm"] = figures["lead_widest_by_pixels_mm"]
    report.numbers["limits"] = {
        "min_width_mm": limits.min_width,
        "min_area_mm2": limits.min_area,
        "min_neck_mm": limits.min_neck,
        "max_notch": limits.max_notch,
        "max_colour_run": limits.max_colour_run,
        "max_lead_mm": limits.lead(found.lead_mm),
        "min_lead_mm": limits.min_lead,
    }
    report.numbers["flags"] = flag_counts(reading.flags)
    report.numbers["flagged"] = kept_flags(reading.flags)
    report.numbers["dead_ends"] = [
        {k: v for k, v in end.items() if k not in ("shape", "end", "way")}
        for end in reading.dead_ends
    ]
    report.numbers["pieces"] = kept_rows(reading.rows)
    if reading.flags:
        report.line(f"rule breaks, {len(reading.flags)}: {counts_said(reading.flags)}")
        for one in reading.flags[:SAID]:
            report.line(f"  {one['flag']}: {one['said']}")
        if len(reading.flags) > SAID:
            report.line(
                f"  and {len(reading.flags) - SAID} more, each in the saved numbers' `flagged` "
                "and marked on the picture"
            )
    else:
        report.line("rule breaks: none")
    for text in reading.seconds:
        report.second(text)
    for text in reading.notes[:NOTES]:
        report.note(text)


def fixes_said(flags: list[dict[str, Any]]) -> str:
    """Which command fixes each kind of flag there is, as the reply's last line."""
    names = {f["flag"] for f in flags}
    ways = []
    if "dead-end" in names:
        ways.append(
            "a dead end: `pieces extend MAP --at X,Y` runs it on to the next line, or `pieces "
            "drop-line MAP --at X,Y` takes it out"
        )
    if "surrounds" in names:
        ways.append(
            "a piece that surrounds another: `pieces split MAP ID --line x1,y1,x2,y2` from "
            "the lead round the inner piece to the outer piece's edge opens it"
        )
    if names & {"sliver", "small"}:
        ways.append("a sliver or a small piece: `pieces merge MAP A B` joins it to a neighbour B")
    if names & {"neck", "notch"}:
        ways.append(
            "a neck or a notch: `pieces split MAP ID --line x1,y1,x2,y2` cuts the piece across "
            "it with a new line"
        )
    if "gradient" in names:
        ways.append(
            "a gradient: `pieces split MAP ID --line x1,y1,x2,y2` cuts it where the colour "
            "changes, or leave it one colour"
        )
    if "thin-lead" in names:
        ways.append(
            "lead under a nozzle's width: `pieces widen MAP A B` widens the line there and "
            "keeps it; `pieces merge MAP A B` takes it out, where the two are one colour and "
            "the picture doesn't mean the line"
        )
    if "no-opening" in names:
        ways.append(
            "a piece the lip leaves no one opening in: its line says which, `pieces split` "
            "along the line it gives, `pieces merge` a small one into a neighbour, or build "
            "with a narrower lip"
        )
    if "wide-lead" in names:
        ways.append(
            "a patch of lead: `pieces fill MAP --at X,Y` turns it into glass, shared among "
            "the pieces round it; `pieces retrace MAP --box X0,Y0,X1,Y1 --dark N` reads the "
            "picture again there, where the patch is a dark place in the picture and not a "
            "line; `pieces merge MAP A B` joins two pieces across it"
        )
    return "; ".join(ways)


def draw(found: Map, reading: Reading, out: Path, name: str, beside: Any = None) -> str:
    """The map as a picture: each piece in its colour and numbered, each flagged piece outlined
    in red, each dead end ringed, each neck crossed, and the lead's widest point circled. With
    `beside`, the picture it was traced from, to its left."""
    from PIL import Image, ImageDraw, ImageFont

    x0, y0, x1, y1 = found.panel.bounds
    margin, key_px = 16, 22
    scale = (PICTURE_PX - 2 * margin) / max(x1 - x0, y1 - y0, 1e-6)
    width = int((x1 - x0) * scale) + 2 * margin
    height = int((y1 - y0) * scale) + 2 * margin + key_px

    def px(points: Any) -> list[tuple[float, float]]:
        return [
            (margin + (x - x0) * scale, height - key_px - margin - (y - y0) * scale)
            for x, y in points
        ]

    picture = Image.new("RGB", (width, height), "white")
    draw_on = ImageDraw.Draw(picture)
    draw_on.polygon(px(found.panel.exterior.coords), fill=(45, 45, 45))
    flagged = {f["piece"] for f in reading.flags if f["piece"] is not None}
    for piece in sorted(found.pieces, key=lambda p: -p.shape.area):
        rgb = (found.colours.get(piece.colour) or {}).get("rgb")
        tint = tuple(int(v) for v in rgb) if rgb else colours.NAMED.get(piece.colour, (200,) * 3)
        draw_on.polygon(px(piece.shape.exterior.coords), fill=tint)
        for ring in piece.shape.interiors:
            draw_on.polygon(px(ring.coords), fill=(45, 45, 45))
    for piece in found.pieces:
        if piece.id in flagged:
            for ring in (piece.shape.exterior, *piece.shape.interiors):
                draw_on.line(px(ring.coords), fill=(230, 20, 20), width=3)
    for row in reading.rows:
        if "neck" in row["flags"] and row["narrowest_from"]:
            draw_on.line(
                px([row["narrowest_from"], row["narrowest_to"]]), fill=(255, 0, 255), width=4
            )
    for end in reading.dead_ends:
        (x, y) = px([end["at"]])[0]
        draw_on.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(255, 0, 255), width=3)
    figures = reading.numbers
    (x, y) = px([figures["lead_widest_at"]])[0]
    r = max(4.0, figures["lead_widest_mm"] * scale / 2)
    draw_on.ellipse((x - r, y - r, x + r, y + r), outline=(255, 220, 0), width=2)
    font = ImageFont.load_default(size=13)
    for piece in found.pieces:
        (x, y) = px(shapely.get_coordinates(piece.shape.representative_point()))[0]
        draw_on.text(
            (x, y), str(piece.id), fill=(0, 0, 0), font=font, anchor="mm",
            stroke_width=2, stroke_fill=(255, 255, 255),
        )  # fmt: skip
    key = (
        "red outline: a piece that breaks a rule; magenta ring: a dead end; magenta bar: a "
        "neck; yellow circle: the lead's widest point"
    )
    draw_on.text((margin, height - key_px + 3), key, fill=(0, 0, 0), font=font)
    if beside is not None:
        left = beside.convert("RGB").resize(
            (int((x1 - x0) * scale), int((y1 - y0) * scale)), Image.Resampling.LANCZOS
        )
        both = Image.new("RGB", (width + left.width + margin, height), "white")
        both.paste(left, (margin, margin))
        both.paste(picture, (left.width + margin, 0))
        picture = both
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-pieces.png"
    picture.save(target)
    return str(target)


def run_check(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    limits = limits_of(args)
    reading = read_map(found, limits, image=getattr(args, "image", None))
    say(found, reading, report, limits)
    target = draw(found, reading, Path(args.out), Path(args.map).stem)
    report.wrote(target)
    report.numbers["picture"] = target
    if found.surface.get("kind") == "dome":
        report.note(
            "this map is a dome's chart, drawn wider than the dome nearer its top, so its "
            "widths and areas aren't the glass's: `pieces flatten` it, and check each slice"
        )
    if reading.differs:
        report.verdict("DISAGREE", "; ".join(reading.differs[:6]))
        return
    if reading.flags:
        report.note(f"to fix {fixes_said(reading.flags)}. Then run `pieces check` again")
        report.verdict(
            "FAIL",
            f"{len(reading.flags)} rule break(s) in {len(found.pieces)} piece(s): "
            f"{counts_said(reading.flags)}; glass share {reading.numbers['glass_share_pct']:.2f}%",
        )
        return
    report.verdict(
        "OK",
        f"{len(found.pieces)} piece(s) and no rule break; glass share "
        f"{reading.numbers['glass_share_pct']:.2f}%, the lead's widest point "
        f"{reading.numbers['lead_widest_mm']:.2f} mm",
    )
