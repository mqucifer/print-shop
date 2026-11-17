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

- **`widen A B`**: the line between two pieces, where it's under a nozzle's width, becomes
  that wide: each piece's edge moves back by half of what's short, only along the stretch
  that's under, so the line keeps its path. A line the picture means, a sun's ray or a
  river's inner line between two pieces of one colour, is kept that way where a merge would
  take it out (mqucifer/print-shop#161).
- **`fix STEPS`**: a file of those fixes, run in order in one call with one reply
  (mqucifer/print-shop#161). A map takes dozens of fixes, and a run chained them from its own
  script, where none was a bench call. It says each step's result, stops at the first step
  that's refused, and writes the map up to it.

**A fix becomes glass, not lead** (the Sponsor, 2026-11-17): a merge and a dropped line only
add glass, and a split or an extension takes one lead line's worth. Every reply says the pieces,
the glass share and the lead's widest point before and after, and nothing but the pieces named
changes: every other piece's outline is the same, point for point.

**Second method.** The map before and after as pixels: the glass a fix added or took, counted,
against the outlines' area, and the pieces a split or an extension made, counted where their
pixels join.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import piecemap
from .limits import under
from .piecemap import Map, Piece
from .pieces_check import (
    MIN_LEAD,
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
#: The fixes a steps file may name, and what each needs.
FIXES = ("merge", "split", "extend", "drop-line", "widen")
#: A widened line is this much wider than asked, in mm, so it reads at its width however its
#: curve is drawn as points.
WIDEN_OVER = 0.004
#: Steps said one by one in a reply, at most: the rest are in the saved numbers.
STEPS_SAID = 60


@dataclass
class Made:
    """One fix, made: the map after it, the pieces it changed, what it did in words, and the
    glass it added and took, in mm², by the outlines."""

    after: Map
    changed: list[int]
    did: str
    glass: tuple[float, float]
    numbers: dict[str, Any] = field(default_factory=dict)


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
    # Within the panel: two pieces on its edge are joined up to the edge, and no further.
    zone = zone.intersection(found.panel)
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
        dict(found.surface),
    )


def _glass_by_pixels(before: Map, after: Map) -> tuple[float, float]:
    """The glass a fix added and took, in mm², by the two maps' pixels at one scale."""
    was = piecemap.pixels(before)
    now = piecemap.pixels(after, was.px_per_mm)
    added = float(((was.ids == 0) & (now.ids > 0)).sum()) * was.mm**2
    took = float(((was.ids > 0) & (now.ids == 0)).sum()) * was.mm**2
    return added, took


def _differ_by_pixels(
    before: Map, after: Map, changed: list[int], glass: tuple[float, float]
) -> tuple[tuple[float, float], bool, list[str]]:
    """One fix read again: the glass it added and took by the two maps' pixels, whether every
    piece it didn't name is the same point for point, and where either differs from what the
    outlines say."""
    same = all(
        p.shape.equals(before.piece(p.id).shape)
        for p in after.pieces
        if p.id not in changed and any(o.id == p.id for o in before.pieces)
    )
    by_pixels = _glass_by_pixels(before, after)
    px_per_mm = piecemap.pixels(after).px_per_mm
    rims = sum(after.piece(n).shape.length for n in changed)
    slack = PIXELS_OFF * rims / px_per_mm + 0.5
    differs = []
    both = (("added", glass[0], by_pixels[0]), ("taken", glass[1], by_pixels[1]))
    for name, mine, theirs in both:
        if abs(mine - theirs) > slack:
            differs.append(
                f"the glass {name} is {mine:.2f} mm² by the outlines and {theirs:.2f} mm² by "
                "the pixels"
            )
    if not same:
        differs.append("a piece that wasn't named changed")
    return by_pixels, same, differs


def _settle(
    report: Report,
    before: Map,
    after: Map,
    changed: list[int],
    glass: tuple[float, float],
    by_pixels: tuple[float, float],
    same: bool,
    target: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Write the new map and say what changed: the pieces, the glass share and the lead's
    widest point before and after, and each changed piece as `pieces check` reads it. With the
    readings before and after, and where the second method differs."""
    was, now = quick(before), quick(after)
    reading = read_map(after, Limits(), only=changed)
    after.flags = [*after.flags, *kept_flags(reading.flags)]
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
    for text in reading.notes[:4]:
        report.note(text)
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
    return was, now, quick_differs(after, now) + list(reading.differs)


def _finish(args: Any, report: Report, before: Map, made: Made) -> None:
    """One fix's reply: the new map written, what changed, and its verdict."""
    if "widened_to_mm" in made.numbers:
        report.numbers["widened_from_mm"] = made.numbers["widened_from_mm"]
        report.numbers["widened_to_mm"] = made.numbers["widened_to_mm"]
        report.numbers["widened_along_mm"] = made.numbers["widened_along_mm"]
    if "ran_to" in made.numbers:
        report.numbers["ran_on_mm"] = made.numbers["ran_on_mm"]
        report.numbers["ran_from"] = made.numbers["ran_from"]
        report.numbers["ran_to"] = made.numbers["ran_to"]
    by_pixels, same, differs = _differ_by_pixels(before, made.after, made.changed, made.glass)
    target = next_name(args.map, args.to)
    was, now, more = _settle(
        report, before, made.after, made.changed, made.glass, by_pixels, same, target
    )
    differs = more + differs
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:6]))
        return
    report.verdict(
        "OK",
        f"{made.did}. Before: {quick_said(was)}. After: {quick_said(now)}. The new map is "
        f"{target}",
    )


def do_merge(found: Map, a_id: int, b_id: int) -> Made:
    a, b = found.piece(a_id), found.piece(b_id)
    shape, line = merged(found, a, b)
    made = Piece(
        b.id, b.colour, shape, b.thickness_mm, {"rgb": b.paint["rgb"]} if b.paint else None
    )
    after = _swap(found, [a.id, b.id], [made], {"fix": "merge", "a": a.id, "b": b.id})
    lost = float(_joined([a.shape, b.shape]).difference(shape).area)
    return Made(
        after, [b.id],
        f"piece {a.id} joined piece {b.id}, which keeps its number and its {b.colour}: the "
        f"line between them, {line:.1f} mm² of lead, is glass",
        (line, lost),
    )  # fmt: skip


def do_split(found: Map, number: int, line: list[Any], lead_mm: float | None) -> Made:
    piece = found.piece(number)
    lead = lead_mm if lead_mm is not None else found.lead_mm
    if not lead > 0:
        raise NoAnswer("--lead is the new line's width in mm, over 0")
    parts, strip = cut(piece, line, lead)
    # A piece that surrounds another is opened by a line from its hole to its edge: still one
    # piece, with one hole fewer.
    opened = len(parts) == 1 and len(parts[0].interiors) < len(piece.shape.interiors)
    if len(parts) < 2 and not opened:
        ends = [
            piecemap.said_at(p)
            for p in (line[0], line[-1])
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
        {"fix": "split", "piece": piece.id, "line": [list(p) for p in line], "lead_mm": lead,
         "made": [m.id for m in made]},
    )  # fmt: skip
    took = float(piece.shape.area - sum(p.area for p in parts))
    return Made(
        after, [m.id for m in made],
        f"piece {piece.id} is opened by a new lead line {lead:.2f} mm wide from the lead "
        "inside it to its edge: it surrounds no piece now"
        if opened
        else f"piece {piece.id} is cut by a new lead line {lead:.2f} mm wide into "
        f"{len(made)} pieces, {piecemap.numbered([m.id for m in made])}, each {piece.colour}",
        (0.0, took),
    )  # fmt: skip


def do_extend(found: Map, at: Any, aim: Any) -> Made:
    row = _dead_end_at(found, at)
    if row is None:
        raise _no_dead_end(found, at)
    piece = found.piece(row["piece"])
    lead = found.lead_mm
    width = min(max(row["width_mm"], 0.5 * lead), 1.5 * lead)
    end = np.asarray(row["end"])
    if row["kind"] == "island":
        # Whichever end of the lone line is nearer `--at` runs on.
        a, b = np.asarray(row["at"]), np.asarray(row["from"])
        if math.dist(at, b) < math.dist(at, a):
            a, b = b, a
        end, way = a, (a - b) / max(float(np.linalg.norm(a - b)), 1e-12)
    else:
        way = np.asarray(row["way"])
    if aim is not None:
        toward = np.asarray(aim) - end
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
        # Where it ran to as it is, not as a line says it: a history given back as steps
        # (`read_steps`) aims at this point, and has to cut the same line.
        {"fix": "extend", "piece": piece.id, "from": row["at"],
         "to": [float(reached[0]), float(reached[1])], "made": [m.id for m in made]},
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
    return Made(
        after, [m.id for m in made],
        f"the dead end in piece {piece.id} ran on {math.dist(end, reached):.1f} mm, "
        f"{width:.2f} mm wide, from {piecemap.said_at(row['at'])} to "
        f"{piecemap.said_at(reached)}, where it meets {meets}: piece {piece.id} is now pieces "
        f"{piecemap.numbered([m.id for m in made])}",
        (0.0, took),
        {
            "ran_on_mm": float(math.dist(end, reached)),
            "ran_from": row["at"],
            "ran_to": piecemap.xy(reached),
        },
    )  # fmt: skip


def do_drop_line(found: Map, at: Any) -> Made:
    row = _dead_end_at(found, at)
    if row is not None:
        piece = found.piece(row["piece"])
        shape = _one(_joined([piece.shape, row["shape"]]), f"piece {piece.id} with the line out")
        made = Piece(piece.id, piece.colour, shape, piece.thickness_mm, piece.paint)
        after = _swap(
            found, [piece.id], [made], {"fix": "drop-line", "piece": piece.id, "at": row["at"]}
        )
        return Made(
            after, [piece.id],
            f"the dead end in piece {piece.id}, {row['length_mm']:.1f} mm long to "
            f"{piecemap.said_at(row['at'])}, is out: its {row['shape'].area:.1f} mm² of lead "
            "is that piece's glass",
            (float(shape.area - piece.shape.area), float(piece.shape.difference(shape).area)),
        )  # fmt: skip
    here = shapely.Point(at)
    if any(p.shape.contains(here) for p in found.pieces):
        raise _no_dead_end(found, at)
    near = sorted((float(here.distance(p.shape)), p.id) for p in found.pieces)[:2]
    reach = REACH * found.lead_mm
    if len(near) < 2 or near[1][0] > reach:
        raise _no_dead_end(found, at)
    a, b = sorted((found.piece(n) for _, n in near), key=lambda p: p.shape.area)
    shape, line = merged(found, a, b)
    made = Piece(
        b.id, b.colour, shape, b.thickness_mm, {"rgb": b.paint["rgb"]} if b.paint else None
    )
    after = _swap(
        found, [a.id, b.id], [made], {"fix": "drop-line", "a": a.id, "b": b.id, "at": list(at)}
    )
    return Made(
        after, [b.id],
        f"the line at {piecemap.said_at(at)} lay between pieces {a.id} and {b.id}, and is "
        f"out: the smaller, {a.id}, joined {b.id}, which keeps its number and its {b.colour} "
        f"(`pieces merge` chooses which joins which), and {line:.1f} mm² of lead is glass",
        (line, float(_joined([a.shape, b.shape]).difference(shape).area)),
    )  # fmt: skip


def _between(a: Any, b: Any, reach: float, step: float) -> list[Any]:
    """The middle of the lead between two shapes, wherever they're nearer than `reach`: each
    stretch a line through the points half way from one's edge to the nearest of the
    other's."""
    ring = shapely.get_coordinates(a.exterior.segmentize(step))[:-1]
    feet = shapely.get_coordinates(shapely.shortest_line(shapely.points(ring), b))[1::2]
    near = np.hypot(*(feet - ring).T) < reach
    if not near.any():
        return []
    middle = (ring + feet) / 2.0
    # From a point that isn't near, so a stretch across the ring's start is one stretch.
    start = int(np.argmin(near)) if not near.all() else 0
    order = np.roll(np.arange(len(ring)), -start)
    found: list[Any] = []
    run: list[Any] = []
    for k in order:
        if near[k]:
            run.append(middle[k])
        elif run:
            found.append(shapely.LineString(run) if len(run) > 1 else shapely.Point(run[0]))
            run = []
    if run:
        found.append(shapely.LineString(run) if len(run) > 1 else shapely.Point(run[0]))
    return found


def do_widen(found: Map, a_id: int, b_id: int, lead_mm: float | None) -> Made:
    a, b = found.piece(a_id), found.piece(b_id)
    if a.id == b.id:
        raise NoAnswer("a line lies between two pieces: name two")
    wide = MIN_LEAD if lead_mm is None else float(lead_mm)
    if not wide > 0:
        raise NoAnswer("--lead is the line's least width in mm, over 0")
    was = float(a.shape.distance(b.shape))
    if not under(was, wide):
        raise NoAnswer(
            f"the lead between pieces {a.id} and {b.id} is {was:.2f} mm at its narrowest, not "
            f"under {wide:g}: there's nothing to widen"
        )
    reach = wide + WIDEN_OVER
    # From each piece's own edge: where the line tapers, a point of one edge is under the
    # width from the other while the point facing it isn't, so each side finds its own.
    step = min(0.05, wide / 8.0)
    middles = _between(a.shape, b.shape, reach, step)
    theirs = _between(b.shape, a.shape, reach, step)
    cut = shapely.union_all([m.buffer(reach / 2.0, quad_segs=16) for m in [*middles, *theirs]])
    made: list[Piece] = []
    for one in (a, b):
        parts = [p for p in piecemap.polygons(one.shape.difference(cut)) if p.area > CRUMB_MM2]
        if len(parts) != 1:
            raise NoAnswer(
                f"widened to {wide:g} mm, the line would cut piece {one.id} into "
                f"{len(parts)}: it's that thin beside the line. `pieces merge MAP {a.id} "
                f"{b.id}` joins the two, or `pieces split` it there first"
            )
        made.append(Piece(one.id, one.colour, parts[0], one.thickness_mm, one.paint))
    now = float(made[0].shape.distance(made[1].shape))
    if under(now, wide):
        raise NoAnswer(
            f"the line between pieces {a.id} and {b.id} came out {now:.3f} mm, under "
            f"{wide:g}: it wasn't widened"
        )
    after = _swap(
        found, [a.id, b.id], made, {"fix": "widen", "a": a.id, "b": b.id, "lead_mm": wide}
    )
    along = max(float(sum(m.length for m in middles)), float(sum(m.length for m in theirs)))
    took = float(a.shape.area + b.shape.area - made[0].shape.area - made[1].shape.area)
    return Made(
        after, [a.id, b.id],
        f"the line between pieces {a.id} and {b.id}, {was:.2f} mm at its narrowest, is "
        f"{now:.2f} mm there now: each piece's edge moved back by up to "
        f"{(wide - was) / 2.0:.2f} mm, along {along:.1f} mm of the line, and "
        f"{took:.2f} mm² of glass is lead. The line keeps its path",
        (0.0, took),
        {"widened_from_mm": was, "widened_to_mm": now, "widened_along_mm": along},
    )  # fmt: skip


def run_widen(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    _finish(args, report, found, do_widen(found, args.a, args.b, args.lead))


def run_merge(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    _finish(args, report, found, do_merge(found, args.a, args.b))


def run_split(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    _finish(args, report, found, do_split(found, args.id, args.line, args.lead))


def run_extend(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    _finish(args, report, found, do_extend(found, args.at, args.aim))


def run_drop_line(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    _finish(args, report, found, do_drop_line(found, args.at))


# A file of fixes


def _xy(value: Any) -> list[float] | None:
    if (
        isinstance(value, list | tuple)
        and len(value) == 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        return [float(value[0]), float(value[1])]
    return None


def _whole(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_steps(path: str) -> list[dict[str, Any]]:
    """A steps file: a JSON list of fixes, or `{"steps": [...]}`, each `{"fix": NAME, ...}` with
    what that fix's own command takes. A map's `history` is such a list, so the fixes a map
    has had can be made again on the map they began from:

    - `merge`: `a`, the piece that joins, and `b`, the neighbour it joins;
    - `split`: `piece`, and `line`, its points `[[x, y], ...]`; `lead_mm`, the line's width;
    - `extend`: `at`, a point on the dead end (or `from`, as a history has it); `aim`, a point
      to run it toward (or `to`);
    - `drop-line`: `at`, a point on the line;
    - `widen`: `a` and `b`, the two pieces the line lies between; `lead_mm`, its least width.

    Every step is read before any is made, so a file that can't be read changes nothing."""
    found = Path(path)
    if not found.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise NoAnswer(f"{path} isn't JSON: {exc}") from exc
    rows = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise NoAnswer(
            f'{path} is a list of steps, or {{"steps": [...]}}, each {{"fix": NAME, ...}}'
        )
    if not rows:
        raise NoAnswer(f"{path} has no step: give one fix or more")
    steps = []
    for n, row in enumerate(rows, start=1):
        name = row.get("fix") if isinstance(row, dict) else None
        if name not in FIXES:
            raise NoAnswer(f"step {n}: `fix` is one of {', '.join(FIXES)}, as {{\"fix\": NAME}}")

        def needs(key: str, what: str, n: int = n, name: Any = name) -> NoAnswer:
            return NoAnswer(f"step {n}, a {name}, needs `{key}`: {what}")

        if name == "merge":
            a, b = _whole(row.get("a")), _whole(row.get("b"))
            if a is None:
                raise needs("a", "the piece that joins, by its number")
            if b is None:
                raise needs("b", "the neighbour it joins, by its number")
            steps.append({"fix": name, "a": a, "b": b})
        elif name == "widen":
            a, b = _whole(row.get("a")), _whole(row.get("b"))
            if a is None or b is None:
                raise needs("a" if a is None else "b", "a piece the line lies beside, by number")
            lead = row.get("lead_mm")
            if lead is not None and not (isinstance(lead, int | float) and lead > 0):
                raise NoAnswer(f"step {n}, a widen: `lead_mm` is the line's width in mm, over 0")
            steps.append({"fix": name, "a": a, "b": b, "lead_mm": lead})
        elif name == "split":
            piece = _whole(row.get("piece", row.get("id")))
            if piece is None:
                raise needs("piece", "the piece to cut, by its number")
            line = row.get("line")
            points = [_xy(p) for p in line] if isinstance(line, list) else []
            if len(points) < 2 or any(p is None for p in points):
                raise NoAnswer(
                    f"step {n}, a split: `line` is two points or more, [[x1, y1], [x2, y2]], "
                    "from one edge of the piece to another"
                )
            lead = row.get("lead_mm")
            if lead is not None and not (isinstance(lead, int | float) and lead > 0):
                raise NoAnswer(f"step {n}, a split: `lead_mm` is the line's width in mm, over 0")
            steps.append(
                {"fix": name, "piece": piece, "line": points, "lead_mm": lead}
            )
        else:
            at = _xy(row.get("at", row.get("from") if name == "extend" else None))
            if at is None:
                raise needs("at", "a point on the line, [x, y]")
            step: dict[str, Any] = {"fix": name, "at": at}
            if name == "extend":
                given = row.get("aim", row.get("to"))
                aim = _xy(given)
                if given is not None and aim is None:
                    raise NoAnswer(f"step {n}, an extend: `aim` is a point, [x, y]")
                step["aim"] = aim
            steps.append(step)
    return steps


def step_said(step: dict[str, Any]) -> str:
    """A step as a line names it."""
    if step["fix"] == "merge":
        return f"merge {step['a']} into {step['b']}"
    if step["fix"] == "widen":
        return f"widen the line between {step['a']} and {step['b']}"
    if step["fix"] == "split":
        ends = step["line"][0], step["line"][-1]
        return (
            f"split {step['piece']} from {piecemap.said_at(ends[0])} to "
            f"{piecemap.said_at(ends[1])}"
        )
    return f"{step['fix']} at {piecemap.said_at(step['at'])}"


def do_step(found: Map, step: dict[str, Any]) -> Made:
    if step["fix"] == "merge":
        return do_merge(found, step["a"], step["b"])
    if step["fix"] == "widen":
        return do_widen(found, step["a"], step["b"], step["lead_mm"])
    if step["fix"] == "split":
        return do_split(found, step["piece"], step["line"], step["lead_mm"])
    if step["fix"] == "extend":
        return do_extend(found, step["at"], step["aim"])
    return do_drop_line(found, step["at"])


def run_fix(args: Any, report: Report) -> None:
    """`pieces fix MAP STEPS`: each step made on the map the one before left, in memory, and
    one map written at the end. Each step is read again as a fix on its own is, by the two
    maps' pixels; the changed pieces are read once, on the last map."""
    steps = read_steps(args.steps)
    first = found = piecemap.load(args.map)
    rows: list[dict[str, Any]] = []
    changed: list[int] = []
    added = took = added_px = took_px = 0.0
    same = True
    differs: list[str] = []
    stopped = ""
    for n, step in enumerate(steps, start=1):
        name = f"step {n} of {len(steps)}, {step_said(step)}"
        try:
            made = do_step(found, step)
        except NoAnswer as exc:
            stopped = f"{name}, was refused: {exc}"
            rows.append({"step": n, "fix": step["fix"], "made": False, "said": str(exc)})
            break
        by_pixels, kept, differ = _differ_by_pixels(found, made.after, made.changed, made.glass)
        if differ:
            # The step's own second check differs: it isn't made, and nothing after it is.
            differs = [f"{name}: {one}" for one in differ]
            rows.append({"step": n, "fix": step["fix"], "made": False, "said": "; ".join(differ)})
            break
        # As the next command would read it from its file: a file of steps then writes the map
        # the same commands write one by one, point for point. A map that can't be read back
        # is one its own command wouldn't have written: the step is refused.
        try:
            after = piecemap.as_saved(made.after)
        except NoAnswer as exc:
            stopped = f"{name}, was refused: the map it leaves can't be read back: {exc}"
            rows.append({"step": n, "fix": step["fix"], "made": False, "said": str(exc)})
            break
        found = after
        same = same and kept
        added, took = added + made.glass[0], took + made.glass[1]
        added_px, took_px = added_px + by_pixels[0], took_px + by_pixels[1]
        changed = [*changed, *[c for c in made.changed if c not in changed]]
        rows.append(
            {
                "step": n,
                "fix": step["fix"],
                "made": True,
                "said": made.did,
                "changed": made.changed,
                "pieces_after": len(found.pieces),
                "glass_share_after_pct": piecemap.share(found),
                "glass_added_mm2": made.glass[0],
                "glass_taken_mm2": made.glass[1],
                **made.numbers,
            }
        )
    count = sum(1 for row in rows if row["made"])
    report.fact("steps_given", len(steps), how=f"in {args.steps}")
    report.fact("steps_made", count, how="in order, each on the map the one before left")
    report.numbers["steps"] = rows
    for row in rows[:STEPS_SAID]:
        step = steps[row["step"] - 1]
        name = f"step {row['step']} of {len(steps)}, {step_said(step)}"
        report.line(
            f"{name}: {row['said']}. {row['pieces_after']} piece(s), glass share "
            f"{row['glass_share_after_pct']:.2f}%"
            if row["made"]
            else f"{name}: not made: {row['said']}"
        )
    if len(rows) > STEPS_SAID:
        report.line(f"and {len(rows) - STEPS_SAID} more step(s), each in the saved numbers")
    if not count:
        if differs:
            report.second("the first step's two maps as pixels, and its other pieces' outlines")
            report.verdict("DISAGREE", "; ".join(differs[:6]) + ". No map is written")
            return
        raise NoAnswer(f"{stopped}. Nothing is made, and no map is written")
    present = {p.id for p in found.pieces}
    target = next_name(args.map, args.to)
    was, now, more = _settle(
        report, first, found, [c for c in changed if c in present], (added, took),
        (added_px, took_px), same, target,
    )  # fmt: skip
    differs = differs + more
    reach = (
        f"Before: {quick_said(was)}. After: {quick_said(now)}. The map "
        f"{'up to there' if count < len(steps) else 'they leave'} is {target}"
    )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:6]) + f". {count} step(s) are made. {reach}")
    elif stopped:
        report.verdict(
            "PARTIAL",
            f"{stopped}. The {count} step(s) before it are made, and none after it was tried. "
            f"{reach}: give it the steps that are left",
        )
    else:
        report.verdict("OK", f"{count} of {len(steps)} step(s) made, in order. {reach}")
