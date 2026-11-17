"""The fixes a glass cutter makes to a piece map, each a command that writes a new map and says
what changed (mqucifer/print-shop#153).

On the first test from a traced scene the request asked for a sliver merged into a neighbour, a
dead-end line run on or removed, and a necked piece split. The run ran one morphological
opening over every piece and dropped what it cut into the lead: every corner was rounded, and
the glass share fell from 91% to 83%. Here each fix is one piece's, by name:

- **`merge A B`**: A joins its neighbour B, and the line between them goes. The line is the
  lead that a closing of the two fills, out and back in by a lead and a quarter with mitred
  joins, within reach of both: so where the line met another, the two outer sides run on to
  each other as lines, and no corner is rounded. It's refused when they don't touch.
- **`split ID --line ...`**: a new lead line across a piece, the map's lead wide, square-ended.
  Across a piece that surrounds another, from its hole to its edge, it opens the piece.
- **`extend --at X,Y`**: a dead-end line runs on, as wide as it is, straight on or toward
  `--aim`, to where it leaves the piece's glass.
- **`drop-line --at X,Y`**: a dead end is filled with its piece's glass; a line between two
  pieces is their merge, the smaller joining the larger.

**A fix becomes glass, not lead** (the Sponsor, 2026-11-17): a merge and a dropped line only
add glass, and a split or an extension takes one lead line's worth. Every reply says the pieces,
the glass share and the lead's widest point before and after, and nothing but the pieces named
changes: every other piece's outline is the same, point for point.

**Second method.** The map before and after as pixels: the glass a fix added or took, counted,
against the outlines' area, and the pieces a split or an extension made, counted where their
pixels join.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import piecemap
from .piecemap import Map, Piece
from .pieces_check import (
    PIXELS_OFF,
    Limits,
    dead_ends,
    kept_flags,
    kept_rows,
    quick,
    quick_differs,
    quick_said,
    read_map,
)
from .report import NoAnswer, Report

#: Two pieces are neighbours where the lead between them is no wider than this many of the
#: map's usual lead: wider, and something else lies between them.
REACH = 2.5
#: A dead end is the one meant where `--at` is within this many leads of it.
NEAR = 3.0
#: Coordinates are joined on this grid, in mm, so sides that lie on each other become one.
GRID = 1e-6
#: A crumb of glass under this, in mm², left by a cut is lead.
CRUMB_MM2 = 0.05


def next_name(path: str, to: str | None) -> str:
    """The new map's file: `--to`, or the old one's name with the next number not yet there."""
    if to:
        return to
    found = re.fullmatch(r"(.*?)(?:-(\d+))?(\.json)?", path)
    assert found
    stem, number, ending = found.group(1), int(found.group(2) or 0), found.group(3) or ".json"
    while True:
        number += 1
        name = f"{stem}-{number}{ending}"
        if not Path(name).exists():
            return name


def _joined(shapes: list[Any]) -> Any:
    return shapely.union_all(shapes, grid_size=GRID)


def _one(shape: Any, what: str) -> Any:
    parts = [p for p in piecemap.polygons(shape) if p.area > CRUMB_MM2]
    if len(parts) != 1:
        raise NoAnswer(f"{what} came out as {len(parts)} parts, not one piece")
    return parts[0]


def _refilled(found: Map, shape: Any, was: list[Any], keep: int) -> Any:
    """A joined shape with each new hole that holds nothing but lead filled: what's left of the
    line where it was wider than a line."""
    kept = []
    for ring in shape.interiors:
        hole = shapely.Polygon(ring)
        holds = any(o.id != keep and o.shape.intersects(hole) for o in found.pieces)
        old = any(hole.symmetric_difference(w).area < 0.01 * hole.area for w in was)
        if holds or old:
            kept.append(ring)
    return shapely.Polygon(shape.exterior, kept)


def line_between(found: Map, a: Piece, b: Piece) -> list[Any]:
    """The lead line between two pieces, as the parts of it: what a closing of the two fills,
    within reach of both and clear of every other piece, touching both."""
    reach = REACH * found.lead_mm
    both = _joined([a.shape, b.shape])
    zone = a.shape.buffer(reach).intersection(b.shape.buffer(reach))
    others = [o.shape for o in found.pieces if o.id not in (a.id, b.id)]
    line = piecemap.closed(both, reach / 2.0).difference(both).intersection(zone)
    if others:
        line = line.difference(shapely.union_all(others))
    touch = 1e-3 * found.lead_mm
    return [
        part
        for part in piecemap.polygons(line)
        if part.area > CRUMB_MM2
        and part.distance(a.shape) <= touch
        and part.distance(b.shape) <= touch
    ]


def merged(found: Map, a: Piece, b: Piece) -> tuple[Any, float]:
    """A joined to B, and the area of the line that went."""
    if a.id == b.id:
        raise NoAnswer("a piece joins a neighbour, not itself: name two pieces")
    gap = float(a.shape.distance(b.shape))
    reach = REACH * found.lead_mm
    line = line_between(found, a, b) if gap <= reach else []
    if not line:
        nearest = sorted(
            (float(a.shape.distance(o.shape)), o.id) for o in found.pieces if o.id != a.id
        )[:4]
        raise NoAnswer(
            f"pieces {a.id} and {b.id} don't touch: the nearest they come is {gap:.2f} mm, and "
            f"a lead line here is about {found.lead_mm:.2f} mm wide. Piece {a.id}'s neighbours "
            f"are {', '.join(f'{n} ({d:.2f} mm off)' for d, n in nearest if d <= reach) or 'none'}"
        )
    lone = [shapely.Polygon(ring) for one in (a, b) for ring in one.shape.interiors]
    shape = _one(_joined([a.shape, b.shape, *line]), f"pieces {a.id} and {b.id} joined")
    shape = _refilled(found, shape, lone, b.id)
    return shape, float(shape.area - a.shape.area - b.shape.area)


def cut(piece: Piece, points: list[Any], lead: float) -> tuple[list[Any], Any]:
    """A piece less a line `lead` wide through `points`, run on a lead's width past each end:
    its parts, largest first, and the line as a strip."""
    path = np.asarray(points, dtype=float)
    if len(path) < 2 or np.allclose(path[0], path[-1]):
        raise NoAnswer("a line takes two points or more, apart")

    def past(end: np.ndarray, before: np.ndarray) -> np.ndarray:
        way = end - before
        return np.asarray(end + way / max(float(np.linalg.norm(way)), 1e-12) * lead)

    path = np.vstack([past(path[0], path[1]), path, past(path[-1], path[-2])])
    strip = shapely.LineString(path).buffer(lead / 2.0, cap_style="flat", join_style="mitre")
    parts = [p for p in piecemap.polygons(piece.shape.difference(strip)) if p.area > CRUMB_MM2]
    return parts, strip


def _take(found: Map, piece: Piece, parts: list[Any]) -> list[Piece]:
    """A cut piece's parts as pieces: the largest keeps its number, the rest take new ones."""
    made = []
    number = found.next_id()
    paint = {"rgb": piece.paint["rgb"]} if piece.paint and "rgb" in piece.paint else None
    for k, part in enumerate(parts):
        made.append(
            Piece(
                piece.id if k == 0 else number + k - 1,
                piece.colour,
                part,
                piece.thickness_mm,
                paint,
            )  # fmt: skip
        )
    return made


def _dead_end_at(found: Map, at: tuple[float, float]) -> dict[str, Any] | None:
    """The dead end `--at` names: the nearest within `NEAR` leads of it."""
    here = shapely.Point(at)
    rows = sorted(
        (float(here.distance(row["shape"])), k, row) for k, row in enumerate(dead_ends(found))
    )
    if rows and rows[0][0] <= NEAR * found.lead_mm:
        return rows[0][2]
    return None


def _no_dead_end(found: Map, at: tuple[float, float]) -> NoAnswer:
    rows = dead_ends(found)
    where = "; ".join(f"piece {r['piece']} at {piecemap.said_at(r['at'])}" for r in rows[:8])
    return NoAnswer(
        f"no dead-end line within {NEAR * found.lead_mm:.1f} mm of {piecemap.said_at(at)}: "
        + (f"the map's dead ends are in {where}" if rows else "the map has none")
    )


def _swap(found: Map, gone: list[int], made: list[Piece], entry: dict[str, Any]) -> Map:
    """The map with some pieces replaced, and the fix written in its history."""
    for one in made:
        # A point left on a straight side where two sides were joined goes: nothing moves by
        # more than the grid they were joined on.
        tidy = one.shape.simplify(10.0 * GRID, preserve_topology=True)
        if tidy.is_valid and tidy.geom_type == "Polygon":
            one.shape = shapely.geometry.polygon.orient(tidy, 1.0)
    pieces = [p for p in found.pieces if p.id not in gone] + made
    return Map(
        found.panel,
        found.lead_mm,
        {name: dict(c) for name, c in found.colours.items()},
        sorted(pieces, key=lambda p: p.id),
        dict(found.source),
        [*found.history, entry],
        [f for f in found.flags if f.get("piece") not in gone],
    )


def _glass_by_pixels(before: Map, after: Map) -> tuple[float, float]:
    """The glass a fix added and took, in mm², by the two maps' pixels at one scale."""
    was = piecemap.pixels(before)
    now = piecemap.pixels(after, was.px_per_mm)
    added = float(((was.ids == 0) & (now.ids > 0)).sum()) * was.mm**2
    took = float(((was.ids > 0) & (now.ids == 0)).sum()) * was.mm**2
    return added, took


def _finish(
    args: Any,
    report: Report,
    before: Map,
    after: Map,
    changed: list[int],
    did: str,
    glass: tuple[float, float],
) -> None:
    """Write the new map and say what changed: the pieces, the glass share and the lead's
    widest point before and after, and each changed piece as `pieces check` reads it."""
    was, now = quick(before), quick(after)
    reading = read_map(after, Limits(), only=changed)
    after.flags = [*after.flags, *kept_flags(reading.flags)]
    target = next_name(args.map, args.to)
    piecemap.save(after, target)
    report.wrote(target)
    report.fact("map", target, how="the new map: the one given is as it was")
    report.fact("pieces_before", was["pieces_count"], how="in the map given")
    report.fact("pieces_after", now["pieces_count"], how="in the new map")
    report.fact("glass_share_before_pct", was["glass_share_pct"], "%", "by the outlines")
    report.fact("glass_share_after_pct", now["glass_share_pct"], "%", "by the outlines")
    source = after.source.get("glass_share_pct")
    if isinstance(source, int | float):
        report.fact(
            "source_glass_share_pct", float(source), "%",
            f"as the map's source, {after.source.get('by', 'unnamed')}, had it",
        )  # fmt: skip
    report.fact(
        "lead_widest_before_mm", was["lead_widest_mm"], "mm",
        f"at {piecemap.said_at(was['lead_widest_at'])}",
    )  # fmt: skip
    report.fact(
        "lead_widest_after_mm", now["lead_widest_mm"], "mm",
        f"at {piecemap.said_at(now['lead_widest_at'])}",
    )  # fmt: skip
    added, took = glass
    report.fact("glass_added_mm2", added, "mm²", "lead that's glass now, by the outlines")
    report.fact("glass_taken_mm2", took, "mm²", "glass that's lead now, by the outlines")
    report.numbers["changed"] = kept_rows(reading.rows)
    report.numbers["flagged"] = kept_flags(reading.flags)
    for row in reading.rows:
        flags = ", ".join(row["flags"]) or "no rule break"
        narrow = f"{row['narrowest_mm']:.2f}" if row["narrowest_mm"] is not None else "no"
        report.line(
            f"piece {row['id']} now: {row['colour']}, {row['area_mm2']:.1f} mm², "
            f"{row['widest_mm']:.2f} mm at its widest, {narrow} mm at its narrowest, round "
            f"{piecemap.said_at(row['centre'])}: {flags}"
        )
    for one in reading.flags:
        report.line(f"  {one['flag']}: {one['said']}")
    same = all(
        p.shape.equals(before.piece(p.id).shape)
        for p in after.pieces
        if p.id not in changed and any(o.id == p.id for o in before.pieces)
    )
    by_pixels = _glass_by_pixels(before, after)
    report.numbers["glass_added_by_pixels_mm2"] = by_pixels[0]
    report.numbers["glass_taken_by_pixels_mm2"] = by_pixels[1]
    report.second(
        f"the two maps as pixels, {now['px_per_mm']:.1f} a mm: {by_pixels[0]:.2f} mm² of lead "
        f"is glass now and {by_pixels[1]:.2f} mm² of glass is lead; the glass share is "
        f"{now['glass_share_by_pixels_pct']:.2f}% and the lead's widest point "
        f"{now['lead_widest_by_pixels_mm']:.2f} mm"
    )
    report.second(
        "every other piece's outline is the same, point for point"
        if same
        else "a piece that wasn't named has a different outline"
    )
    differs = quick_differs(after, now) + list(reading.differs)
    rims = sum(after.piece(n).shape.length for n in changed)
    slack = PIXELS_OFF * rims / now["px_per_mm"] + 0.5
    for name, mine, theirs in (("added", added, by_pixels[0]), ("taken", took, by_pixels[1])):
        if abs(mine - theirs) > slack:
            differs.append(
                f"the glass {name} is {mine:.2f} mm² by the outlines and {theirs:.2f} mm² by "
                "the pixels"
            )
    if not same:
        differs.append("a piece that wasn't named changed")
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:6]))
        return
    report.verdict(
        "OK",
        f"{did}. Before: {quick_said(was)}. After: {quick_said(now)}. The new map is {target}",
    )


def run_merge(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    a, b = found.piece(args.a), found.piece(args.b)
    shape, line = merged(found, a, b)
    made = Piece(
        b.id, b.colour, shape, b.thickness_mm, {"rgb": b.paint["rgb"]} if b.paint else None
    )
    after = _swap(found, [a.id, b.id], [made], {"fix": "merge", "a": a.id, "b": b.id})
    lost = float(_joined([a.shape, b.shape]).difference(shape).area)
    _finish(
        args, report, found, after, [b.id],
        f"piece {a.id} joined piece {b.id}, which keeps its number and its {b.colour}: the "
        f"line between them, {line:.1f} mm² of lead, is glass",
        (line, lost),
    )  # fmt: skip


def run_split(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    piece = found.piece(args.id)
    lead = args.lead if args.lead is not None else found.lead_mm
    if not lead > 0:
        raise NoAnswer("--lead is the new line's width in mm, over 0")
    parts, strip = cut(piece, args.line, lead)
    # A piece that surrounds another is opened by a line from its hole to its edge: still one
    # piece, with one hole fewer.
    opened = len(parts) == 1 and len(parts[0].interiors) < len(piece.shape.interiors)
    if len(parts) < 2 and not opened:
        ends = [
            piecemap.said_at(p)
            for p in (args.line[0], args.line[-1])
            if piece.shape.contains(shapely.Point(p))
            and piece.shape.exterior.distance(shapely.Point(p)) > lead
        ]
        raise NoAnswer(
            f"the line doesn't cut piece {piece.id} in two: a new lead line runs from one edge "
            "of the piece to another"
            + (f", and this one ends in its glass at {' and '.join(ends)}" if ends else "")
            + f". The piece lies from {piecemap.said_at(piece.shape.bounds[:2])} to "
            f"{piecemap.said_at(piece.shape.bounds[2:])}"
        )
    made = _take(found, piece, parts)
    after = _swap(
        found, [piece.id], made,
        {"fix": "split", "piece": piece.id, "line": [list(p) for p in args.line], "lead_mm": lead,
         "made": [m.id for m in made]},
    )  # fmt: skip
    took = float(piece.shape.area - sum(p.area for p in parts))
    _finish(
        args, report, found, after, [m.id for m in made],
        f"piece {piece.id} is opened by a new lead line {lead:.2f} mm wide from the lead "
        "inside it to its edge: it surrounds no piece now"
        if opened
        else f"piece {piece.id} is cut by a new lead line {lead:.2f} mm wide into "
        f"{len(made)} pieces, {piecemap.numbered([m.id for m in made])}, each {piece.colour}",
        (0.0, took),
    )  # fmt: skip


def run_extend(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    row = _dead_end_at(found, args.at)
    if row is None:
        raise _no_dead_end(found, args.at)
    piece = found.piece(row["piece"])
    lead = found.lead_mm
    width = min(max(row["width_mm"], 0.5 * lead), 1.5 * lead)
    end = np.asarray(row["end"])
    if row["kind"] == "island":
        # Whichever end of the lone line is nearer `--at` runs on.
        a, b = np.asarray(row["at"]), np.asarray(row["from"])
        if math.dist(args.at, b) < math.dist(args.at, a):
            a, b = b, a
        end, way = a, (a - b) / max(float(np.linalg.norm(a - b)), 1e-12)
    else:
        way = np.asarray(row["way"])
    if args.aim is not None:
        toward = np.asarray(args.aim) - end
        if float(np.linalg.norm(toward)) < 1e-9:
            raise NoAnswer("--aim is the line's end itself: give a point to run it toward")
        way = toward / float(np.linalg.norm(toward))
    x0, y0, x1, y1 = found.panel.bounds
    ray = shapely.LineString([end, end + way * 2.0 * math.hypot(x1 - x0, y1 - y0)])
    ahead = [
        part
        for part in shapely.get_parts(ray.intersection(piece.shape))
        if part.geom_type == "LineString" and part.length > 1e-6
    ]
    if not ahead:
        raise NoAnswer(
            f"the dead end at {piecemap.said_at(row['at'])} has none of piece {piece.id}'s "
            "glass ahead of it that way: give --aim X,Y, a point to run it toward"
        )
    first = min(ahead, key=lambda part: float(shapely.Point(end).distance(part)))
    reached = max(shapely.get_coordinates(first), key=lambda p: math.dist(p, end))
    parts, strip = cut(piece, [end - way * 0.5 * width, reached], width)
    if len(parts) < 2:
        raise NoAnswer(
            f"run on from {piecemap.said_at(row['at'])} to {piecemap.said_at(reached)}, the "
            f"line doesn't cut piece {piece.id} in two: give --aim X,Y, a point on the line or "
            "the edge it should run to"
        )
    made = _take(found, piece, parts)
    after = _swap(
        found, [piece.id], made,
        {"fix": "extend", "piece": piece.id, "from": row["at"], "to": piecemap.xy(reached),
         "made": [m.id for m in made]},
    )  # fmt: skip
    beyond = shapely.Point(reached)
    if found.panel.exterior.distance(beyond) <= lead:
        meets = "the panel's edge"
    else:
        near = sorted(
            (float(beyond.distance(o.shape)), o.id) for o in found.pieces if o.id != piece.id
        )
        meets = f"the line beside piece {near[0][1]}" if near else "the lead"
    took = float(piece.shape.area - sum(p.area for p in parts))
    report.numbers["ran_on_mm"] = float(math.dist(end, reached))
    report.numbers["ran_from"] = row["at"]
    report.numbers["ran_to"] = piecemap.xy(reached)
    _finish(
        args, report, found, after, [m.id for m in made],
        f"the dead end in piece {piece.id} ran on {math.dist(end, reached):.1f} mm, "
        f"{width:.2f} mm wide, from {piecemap.said_at(row['at'])} to "
        f"{piecemap.said_at(reached)}, where it meets {meets}: piece {piece.id} is now pieces "
        f"{piecemap.numbered([m.id for m in made])}",
        (0.0, took),
    )  # fmt: skip


def run_drop_line(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    row = _dead_end_at(found, args.at)
    if row is not None:
        piece = found.piece(row["piece"])
        shape = _one(_joined([piece.shape, row["shape"]]), f"piece {piece.id} with the line out")
        made = Piece(piece.id, piece.colour, shape, piece.thickness_mm, piece.paint)
        after = _swap(
            found, [piece.id], [made], {"fix": "drop-line", "piece": piece.id, "at": row["at"]}
        )
        _finish(
            args, report, found, after, [piece.id],
            f"the dead end in piece {piece.id}, {row['length_mm']:.1f} mm long to "
            f"{piecemap.said_at(row['at'])}, is out: its {row['shape'].area:.1f} mm² of lead "
            "is that piece's glass",
            (float(shape.area - piece.shape.area), float(piece.shape.difference(shape).area)),
        )  # fmt: skip
        return
    here = shapely.Point(args.at)
    if any(p.shape.contains(here) for p in found.pieces):
        raise _no_dead_end(found, args.at)
    near = sorted((float(here.distance(p.shape)), p.id) for p in found.pieces)[:2]
    reach = REACH * found.lead_mm
    if len(near) < 2 or near[1][0] > reach:
        raise _no_dead_end(found, args.at)
    a, b = sorted((found.piece(n) for _, n in near), key=lambda p: p.shape.area)
    shape, line = merged(found, a, b)
    made = Piece(
        b.id, b.colour, shape, b.thickness_mm, {"rgb": b.paint["rgb"]} if b.paint else None
    )
    after = _swap(
        found, [a.id, b.id], [made], {"fix": "drop-line", "a": a.id, "b": b.id, "at": list(args.at)}
    )
    _finish(
        args, report, found, after, [b.id],
        f"the line at {piecemap.said_at(args.at)} lay between pieces {a.id} and {b.id}, and is "
        f"out: the smaller, {a.id}, joined {b.id}, which keeps its number and its {b.colour} "
        f"(`pieces merge` chooses which joins which), and {line:.1f} mm² of lead is glass",
        (line, float(_joined([a.shape, b.shape]).difference(shape).area)),
    )  # fmt: skip
