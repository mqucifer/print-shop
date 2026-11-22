"""A through-bolt's hole, its seats and its nut's pocket, read from a part's own faces
(mqucifer/print-shop#168).

A through-bolt passes right through two parts, with a washer under its head and a nut on the far
side, on a washer or in a pocket that holds it by its flats. What `load` and `fit-bed` need from
each part is the same: the round hole along the bolt's axis, where it ends, and what's round that
end. `load` reads a part's faces with FreeCAD and `fit-bed` with OpenCascade through CadQuery, so
each hands its faces here as plain rows and the reading is one, in plain Python and NumPy:

- a **cylinder** row: `axis`, a unit vector; `origin`, a point on it; `radius`; `hole`, whether
  the material is outside it; `low` and `high`, where the face runs along the axis, as a point's
  dot product with `axis`; `turns`, how far round it goes, in radians; and `middle`, the unit way
  out from the axis to its middle, which says where round the axis a part of a circle is;
- a **plane** row: `normal`, out of the material; `at`, a point on it; `outline`, its edges as
  straight runs, each its two ends; and, filled in by `along`, `low` and `high`, where the face
  runs along whatever axis the caller is reading, and `corners`, the face's own, which bound it
  sideways too. A row with no corners is a plane without end.

**A peaked bore** (mqucifer/print-shop#237) is how a hole that lies level on the bed is printed
with nothing to support: one round face about the axis and, where that face is missing, two flat
faces tangent to it that rise to a point, no flatter than `FLATTEST` degrees, along the whole
stretch. It's a bore, read by what's there: what bears on its end is the ring less the peak's
cut, and a shank leaning toward the peak has only what's left of the round to lean on. A round
face is read stretch by stretch, so two arcs apart along the axis never add up to a hole.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from .limits import under

#: A bolt's place is on its hole's axis within this, in mm: a point read off a drawing.
ON_AXIS = 0.05
#: Two faces are at one level, one radius or one distance within this, in mm.
SAME = 1e-4
#: Two directions are the same, or square, within this.
SQUARE = 1e-6
#: Why an arc that doesn't close is no hole.
NOT_ROUND = "the round face there doesn't go all the way round: a slot's end, not a hole"
#: A pocket's play is what the file says within this, in mm.
PLAY_TOL = 0.005
#: A peak's flat faces rise to its point no flatter than this, in degrees: the slope past which
#: the print loop reads a face as needing support.
FLATTEST = 45.0
#: Where a flat face meets a round one it's tangent to, the two kernels put the edge within this
#: of the tangent point, in mm: a circle and its tangent part by only d²/2R, so a place good to
#: 1e-6 mm is good to some thousandths along them.
SLIP = 0.005


def bore(
    cylinders: list[dict[str, Any]], point: Any, planes: list[dict[str, Any]] | None = None
) -> dict[str, Any] | str:
    """The round or peaked hole whose axis passes through `point`: its `axis`, a point on it,
    its `radius`, `low` and `high`, where it runs along the axis, and its `peak`, None where
    it's round. Where holes of several sizes share the axis, a counterbore over a hole, it's
    the narrowest: the one the bolt's shank is in. Or, as words, why there's none."""
    at = np.asarray(point, dtype=float)
    found = []
    for one in cylinders:
        if not one["hole"]:
            continue
        axis, origin = np.asarray(one["axis"], dtype=float), np.asarray(one["origin"], dtype=float)
        off = at - origin
        if float(np.linalg.norm(off - (off @ axis) * axis)) <= ON_AXIS:
            found.append(one)
    if not found:
        return "no round hole has its axis through that point"
    first = np.asarray(found[0]["axis"], dtype=float)
    if any(abs(abs(float(np.asarray(c["axis"]) @ first)) - 1) > SQUARE for c in found):
        return "round holes cross there, along more than one axis"
    radius = min(c["radius"] for c in found)
    arcs = []
    for one in found:
        if one["radius"] > radius + SAME:
            continue
        same_way = float(np.asarray(one["axis"]) @ first) > 0
        low, high = (one["low"], one["high"]) if same_way else (-one["high"], -one["low"])
        arcs.append((low, high, one))
    stretches: list[list[float]] = []
    for start, end, _ in sorted(arcs, key=lambda a: a[:2]):
        if stretches and start <= stretches[-1][1] + SAME:
            stretches[-1][1] = max(stretches[-1][1], end)
        else:
            stretches.append([start, end])
    # Where the axis has more than one stretch of hole, the bolt's place says which: the nearest.
    here = float(at @ first)
    low, high = min(stretches, key=lambda s: abs(here - min(max(here, s[0]), s[1])))
    arcs = [a for a in arcs if a[0] >= low and a[1] <= high]
    origin = np.asarray(arcs[0][2]["origin"], dtype=float)
    hole = {
        "axis": first,
        "origin": origin,
        "radius": float(radius),
        "low": low,
        "high": high,
        "peak": None,
    }
    # What's missing from the round at each level between two faces' ends.
    ends = sorted({v for a in arcs for v in a[:2]})
    levels = [(a, b) for a, b in itertools.pairwise(ends) if b - a > 1e-3] or [(low, high)]
    missing = [
        _missing([c for start, end, c in arcs if start <= (a + b) / 2 <= end], first)
        for a, b in levels
    ]
    if not any(missing):
        return hole
    if any(len(gaps) != 1 for gaps in missing):
        return NOT_ROUND
    middle, half = missing[0][0]
    for other, wide in (gaps[0] for gaps in missing[1:]):
        if abs(wide - half) * radius > SLIP or np.linalg.norm(other - middle) * radius > SLIP:
            return NOT_ROUND
    for _, _, one in arcs:
        off = np.asarray(one["origin"], dtype=float) - origin
        if float(np.linalg.norm(off - (off @ first) * first)) > SAME:
            return NOT_ROUND
    peak = _peak(hole, middle, half, planes or [])
    return peak if isinstance(peak, str) else {**hole, "peak": peak}


def _missing(arcs: list[dict[str, Any]], axis: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """What round faces at one level leave open round the axis: each gap's middle, as the way
    out from the axis, and half how far round it goes, in radians. None where they close."""
    if any(c["turns"] >= 2 * math.pi - 1e-3 for c in arcs):
        return []
    first, second = basis(axis)
    spans = []
    for one in arcs:
        toward = np.asarray(one["middle"], dtype=float)
        middle = math.atan2(float(toward @ second), float(toward @ first))
        start = (middle - one["turns"] / 2) % (2 * math.pi)
        spans.append((start, start + one["turns"]))
    if not spans:
        return [(first, math.pi)]
    # As far round as the faces before 0 reach past it.
    reach = max(end for _, end in spans) - 2 * math.pi
    gaps = []
    for start, end in sorted(spans):
        if start > reach + 1e-3:
            middle = (reach + start) / 2
            toward = math.cos(middle) * first + math.sin(middle) * second
            gaps.append((toward, (start - reach) / 2))
        reach = max(reach, end)
    return gaps


def _peak(
    hole: dict[str, Any], middle: np.ndarray, half: float, planes: list[dict[str, Any]]
) -> dict[str, Any] | str:
    """The peak over a round face's one gap: `toward`, the way its point is; `half`, half the
    gap in radians, which is also how steeply its flat faces rise to the point; `rises_deg`,
    the same in degrees; and `point_mm`, how far the point is from the axis. Or why the gap
    isn't a peak."""
    axis, origin, radius = hole["axis"], hole["origin"], hole["radius"]
    if half >= math.pi / 2 - SLIP / radius:
        return NOT_ROUND  # Flats that never meet: a slot's end, or less of the round than that.
    side = np.cross(axis, middle)
    tangents, flanks = [], []
    for sign in (-1.0, 1.0):
        out = math.cos(half) * middle + sign * math.sin(half) * side
        flank = [
            p
            for p in planes
            if np.linalg.norm(np.asarray(p["normal"]) + out) * radius <= SLIP
            and abs(float(np.asarray(p["normal"]) @ axis)) <= SQUARE
            and abs(float((origin - np.asarray(p["at"])) @ np.asarray(p["normal"])) - radius)
            <= SAME
        ]
        if not flank:
            return f"{NOT_ROUND}, and with no flat face tangent to it there, not a peaked bore"
        tangents.append(-np.asarray(flank[0]["normal"], dtype=float))
        flanks.append(flank)
    # From the flat faces' own normals, which are exact where the round face's ends aren't.
    both, apart = tangents[0] + tangents[1], tangents[0] - tangents[1]
    toward = both / np.linalg.norm(both)
    rises = math.atan2(float(np.linalg.norm(apart)), float(np.linalg.norm(both)))
    if under(math.degrees(rises), FLATTEST):
        return (
            "the round face there doesn't go all the way round, and the flat faces over its "
            f"gap rise at {math.degrees(rises):.3f}°: a peak flatter than {FLATTEST:g}°, "
            "which isn't a peaked bore"
        )
    wide = radius * math.tan(rises)
    for tangent, flank in zip(tangents, flanks, strict=True):
        along_it = (toward - math.cos(rises) * tangent) / math.sin(rises)
        if not _covers(flank, hole, along_it, wide):
            return (
                f"{NOT_ROUND}: the two flat faces tangent to it don't rise to a point along "
                "the whole stretch, as a peaked bore's do"
            )
    return {
        "toward": toward,
        "half": rises,
        "rises_deg": math.degrees(rises),
        "point_mm": radius / math.cos(rises),
    }


def _covers(
    flank: list[dict[str, Any]], hole: dict[str, Any], along_it: np.ndarray, wide: float
) -> bool:
    """Whether the faces in one flank's plane cover it from where it leaves the round to the
    point, `wide` mm along it, at every level of the hole's stretch: each face's outline is
    crossed level by level, and between two crossings is face."""
    runs = [np.reshape(p.get("outline", []), (-1, 2, 3)) for p in flank]
    segments = np.concatenate(runs)
    levels = segments @ hole["axis"]
    across = (segments - hole["origin"]) @ along_it
    low, high = hole["low"], hole["high"]
    ends = sorted({low, high, *(float(v) for v in levels.ravel() if low < v < high)})
    for start, end in itertools.pairwise(ends):
        if end - start <= 1e-3:
            continue
        level = (start + end) / 2
        cut = (levels[:, 0] < level) != (levels[:, 1] < level)
        share = (level - levels[cut, 0]) / (levels[cut, 1] - levels[cut, 0])
        met = np.sort(across[cut, 0] + share * (across[cut, 1] - across[cut, 0]))
        if len(met) % 2:
            return False
        kept = np.clip(met, 0.0, wide)
        if float((kept[1::2] - kept[0::2]).sum()) < wide - SLIP:
            return False
    return True


def peak_said(hole: dict[str, Any]) -> str:
    """A peaked bore, as words: its diameter and which way its point is."""
    peak = hole["peak"]
    return (
        f"peaked, {2 * hole['radius']:.3f} mm across its round, its point "
        f"{peak['point_mm']:.3f} mm from the axis toward ({_way(peak['toward'])}), its flat "
        f"faces rising to it at {peak['rises_deg']:.2f}°"
    )


def peak_saved(peak: dict[str, Any]) -> dict[str, Any]:
    """A peak as a reply's numbers."""
    return {
        "toward": [round(float(v), 6) + 0.0 for v in peak["toward"]],
        "rises_deg": peak["rises_deg"],
        "point_mm": peak["point_mm"],
    }


def peak_cut(hole: dict[str, Any], inner: float, outer: float) -> float:
    """The area a hole's peak takes out of the ring between `inner` and `outer` round its axis,
    `inner` no less than its radius, by arithmetic. At r from the axis the peak is open for
    h - acos(R/r) either side of its point, h half the gap: that times 2r, summed along r."""
    if hole["peak"] is None:
        return 0.0
    half, radius = float(hole["peak"]["half"]), float(hole["radius"])
    least, most = max(inner, radius), min(outer, hole["peak"]["point_mm"])

    def taken(r: float) -> float:
        root = math.sqrt(max(r * r - radius * radius, 0.0))
        return r * r * (half - math.acos(min(radius / r, 1.0))) + radius * root

    return taken(most) - taken(least) if most > least else 0.0


def lean_share(hole: dict[str, Any], leans: Any) -> float:
    """How much of a round hole's bearing is there for a shank leaning `leans`, square to the
    axis: 1 in a round hole. A shank bears on the half of the round it leans toward, each bit
    by how square it is to that, its diameter by its length in all. Of that a peak leaves what
    of the half is still round, so least toward its point: 1 - sin h."""
    if hole["peak"] is None:
        return 1.0
    half, turn = hole["peak"]["half"], lean_off(hole, leans)
    low, high = max(-half, turn - math.pi / 2), min(half, turn + math.pi / 2)
    if high <= low:
        return 1.0
    return 1.0 - (math.sin(high - turn) - math.sin(low - turn)) / 2


def lean_off(hole: dict[str, Any], leans: Any) -> float:
    """How far round from a peak's point a shank leans, in radians: 0 is right at it."""
    toward, way = hole["peak"]["toward"], np.asarray(leans, dtype=float)
    return math.atan2(float(np.linalg.norm(np.cross(toward, way))), float(toward @ way))


def as_round(peak: dict[str, Any], offsets: np.ndarray, toward: np.ndarray) -> np.ndarray:
    """Points on a peaked bore's wall, `offsets` from its axis in a plane square to it, each as
    the diameter it says: twice its distance from the axis on the round, and on a flank twice
    the flank's plane's, which is the same where the flank is tangent."""
    distance = np.linalg.norm(offsets, axis=1)
    ahead = offsets @ toward
    aside = np.abs(offsets[:, 0] * toward[1] - offsets[:, 1] * toward[0])
    on_flank = np.maximum(peak["half"] - np.arctan2(aside, ahead), 0.0)
    return np.asarray(2 * distance * np.cos(on_flank))


def meet(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any] | str:
    """Two parts' holes as one bolt's way through both: the `axis`, pointing from the first
    part's seat to the second's, the level the two holes `meet` at, each part's `seat`, the far
    end of its hole, and each hole's `length`. Or why they aren't one way through."""
    axis = np.asarray(first["axis"], dtype=float)
    theirs = np.asarray(second["axis"], dtype=float)
    turn = float(axis @ theirs)
    if abs(abs(turn) - 1) > SQUARE:
        return "the two parts' holes there don't run the same way"
    off = np.asarray(second["origin"], dtype=float) - np.asarray(first["origin"], dtype=float)
    if float(np.linalg.norm(off - (off @ axis) * axis)) > 2 * ON_AXIS:
        return "the two parts' holes there aren't on one axis"
    low, high = (second["low"], second["high"]) if turn > 0 else (-second["high"], -second["low"])
    if abs(first["high"] - low) <= 1e-3:
        return {
            "axis": axis,
            "meet": first["high"],
            "seats": (first["low"], high),
            "lengths": (first["high"] - first["low"], high - low),
        }
    if abs(high - first["low"]) <= 1e-3:
        return {
            "axis": -axis,
            "meet": -first["low"],
            "seats": (-first["high"], -low),
            "lengths": (first["high"] - first["low"], high - low),
        }
    gap = min(abs(first["high"] - low), abs(high - first["low"]))
    return f"the two parts' holes there don't meet end to end: they're {gap:.3f} mm apart"


def _way(toward: Any) -> str:
    return ", ".join(f"{round(float(c), 6) + 0.0:.3g}" for c in toward)


def along(planes: list[dict[str, Any]], axis: Any, corners: list[np.ndarray]) -> None:
    """Fill in where each plane's face runs along `axis`, from its own corners, and keep them."""
    way = np.asarray(axis, dtype=float)
    for plane, points in zip(planes, corners, strict=True):
        reach = np.asarray(points, dtype=float) @ way
        plane["low"], plane["high"] = float(reach.min()), float(reach.max())
        plane["corners"] = np.asarray(points, dtype=float)


def _aside(plane: dict[str, Any], origin: np.ndarray, way: np.ndarray) -> float:
    """How far along a wall its face is from the hole: from the point of the wall's plane nearest
    the axis to the face's nearer end, square to `way`, and 0 where that point is between its
    ends. A face is one piece, so where its ends are either side of the hole some of it is at
    the hole, at the seat or over it."""
    if "corners" not in plane:
        return 0.0
    sideways = np.cross(way, np.asarray(plane["normal"], dtype=float))
    ends = (np.asarray(plane["corners"], dtype=float) - origin) @ sideways
    return max(float(ends.min()), -float(ends.max()), 0.0)


def pocket(
    planes: list[dict[str, Any]],
    hole: dict[str, Any],
    seat: float,
    out: Any,
    reach: float,
    aside: float = 0.0,
) -> dict[str, Any] | str:
    """The pocket a nut sits in at a hole's end: the flat walls that stand on the seat's level,
    `seat` along `out`, the way out of the part there, and face the hole's axis from within
    `reach` of it, with their faces at the hole: no more than `aside` along the wall from the
    point of its plane nearest the axis, so a like wall further along a row is another pocket's
    (mqucifer/print-shop#204). A nut is held from turning by walls in opposite pairs, on its
    flats: each pair's distance apart is an `across_flats_mm`, the narrowest first, with how deep
    the pocket is and how far off its middle the axis is. Or why there's no pocket."""
    way = np.asarray(out, dtype=float)
    origin = np.asarray(hole["origin"], dtype=float)
    walls = []
    facing: list[dict[str, Any]] = []
    for plane in planes:
        normal = np.asarray(plane["normal"], dtype=float)
        if abs(float(normal @ way)) > SQUARE:
            continue
        low, high = sorted((plane["low"], plane["high"]))
        if abs(low - seat) > 1e-3 or high <= seat + SAME:
            continue
        off = origin - np.asarray(plane["at"], dtype=float)
        distance = float((off - (off @ way) * way) @ normal)
        if 0 < distance <= reach and _aside(plane, origin, way) <= aside + SAME:
            walls.append((distance, normal, high - seat))
            # The way from the axis to it and how far, what the nut's corners meet; and where.
            at = np.asarray(plane["at"], dtype=float).tolist()
            facing.append({"toward": (-normal).tolist(), "distance": distance, "at": at})
    pairs = []
    for i, (near, normal, depth) in enumerate(walls):
        for far, other, deep in walls[i + 1 :]:
            if float(normal @ other) < -1 + SQUARE:
                pairs.append((near + far, abs(near - far) / 2, min(depth, deep)))
    if not pairs:
        return (
            f"no pocket there holds a nut by its flats: {len(walls)} flat wall(s) stand on the "
            "hole's end facing its axis, and none faces another across it"
            + (f"; {walls_said(facing)}" if facing else "")
        )
    pairs.sort()
    return {
        "across_flats_mm": pairs[0][0],
        "every_across_mm": [p[0] for p in pairs],
        "off_middle_mm": max(p[1] for p in pairs),
        "depth_mm": min(p[2] for p in pairs),
        "walls": len(walls),
        "pairs": len(pairs),
        "facing": facing,
        "way": way.tolist(),
    }


def walls_said(facing: list[dict[str, Any]]) -> str:
    """The walls a pocket took, as words: each by how far from the axis and which way, and
    where its face is, so a wall that isn't this pocket's would be seen."""
    said = "; ".join(
        f"{f['distance']:.3f} mm from the axis toward "
        f"({', '.join(f'{round(c, 6) + 0.0:.3g}' for c in f['toward'])}), its face round "
        f"({', '.join(f'{round(c, 3) + 0.0:g}' for c in f['at'])})"
        for f in facing
    )
    return f"the walls it took: {said}"


def nut_fits(found: dict[str, Any], nut: Any, play: float) -> list[str]:
    """What's wrong with a pocket for `nut` at `play` mm between each flat and its wall, as
    the file states it: nothing where the nut goes in, can't turn, and the play is the file's."""
    across = found["across_flats_mm"]
    has = (across - nut.flats_mm) / 2
    wrong = []
    if across < nut.flats_mm - SAME:
        wrong.append(
            f"it's {across:.3f} mm across its flats and the nut {nut.name} is {nut.flats_mm:g}: "
            "the nut doesn't go in"
        )
    elif across >= nut.corners_mm - SAME:
        wrong.append(
            f"it's {across:.3f} mm across its flats, and the nut {nut.name} is as little as "
            f"{nut.corners_mm:g} across its corners: the nut turns in it"
        )
    if abs(has - play) > PLAY_TOL:
        wrong.append(
            f"its play is {has:.3f} mm a side round the nut's {nut.flats_mm:g} mm, and the "
            f"assembly file states {play:g}"
        )
    return wrong


# The nut going in (mqucifer/print-shop#183). Walls that hold a nut by its flats in pairs don't
# say whether its corners get between the others: an 8.2 mm square holds an M5 nut's 8 mm flats
# with 0.1 mm of play each way, and in no turn does the nut go into it.

#: The turns a nut is read at first, in degrees: a hexagon is the same every 60°.
TURN_STEP = 0.25
#: Then round the best of those, this far either side and at this step, in degrees.
TURN_NEAR, TURN_FINE = 0.5, 0.001


def basis(way: Any) -> tuple[np.ndarray, np.ndarray]:
    """Two directions square to `way` and to each other: the plane a pocket is read in."""
    axis = np.asarray(way, dtype=float)
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    return first, np.cross(axis, first)


def hexagon(corners: float, turn: float, centre: Any = (0.0, 0.0)) -> np.ndarray:
    """A hexagon `corners` mm across its corners, its first corner `turn` degrees round from
    the plane's first direction, about `centre`: its six corners in the plane."""
    angles = np.radians(turn + 60.0 * np.arange(6))
    return np.asarray(centre, dtype=float) + corners / 2 * np.stack(
        [np.cos(angles), np.sin(angles)], axis=1
    )


def _clip(polygon: np.ndarray, toward: np.ndarray, limit: float) -> np.ndarray:
    """What of a convex polygon lies where a point's reach `toward` is at most `limit`."""
    if not len(polygon):
        return polygon
    reach = polygon @ toward - limit
    kept: list[np.ndarray] = []
    for i in range(len(polygon)):
        here, there = polygon[i], polygon[(i + 1) % len(polygon)]
        a, b = reach[i], reach[(i + 1) % len(polygon)]
        if a <= 0:
            kept.append(here)
        if (a < 0 < b) or (b < 0 < a):
            kept.append(here + a / (a - b) * (there - here))
    return np.array(kept, dtype=float).reshape(-1, 2)


def _region(toward: np.ndarray, limits: np.ndarray, size: float) -> np.ndarray:
    """The points whose reach along each of `toward` is at most its limit, within a square
    `size` mm either side of the axis: a convex polygon, empty where there are none."""
    polygon = size * np.array([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]])
    for one, limit in zip(toward, limits, strict=True):
        polygon = _clip(polygon, one, float(limit))
    return polygon


def _off_axis(polygon: np.ndarray) -> tuple[float, np.ndarray] | None:
    """How far a convex polygon is from the axis, at the origin, and its nearest point: 0 and
    the origin where the axis is in it, None where it's empty."""
    if len(polygon) < 3:
        return None
    ends = np.roll(polygon, -1, axis=0)
    runs = ends - polygon
    # Inside where the origin is on the same side of every edge.
    sides = runs[:, 0] * -polygon[:, 1] - runs[:, 1] * -polygon[:, 0]
    if (sides >= -1e-12).all() or (sides <= 1e-12).all():
        return 0.0, np.zeros(2)
    lengths = (runs**2).sum(axis=1)
    share = np.clip(-(polygon * runs).sum(axis=1) / np.where(lengths > 0, lengths, 1), 0, 1)
    nearest = polygon + share[:, None] * runs
    distance = np.linalg.norm(nearest, axis=1)
    best = int(np.argmin(distance))
    return float(distance[best]), nearest[best]


def _reaches(toward: np.ndarray, corners: float, turns: np.ndarray) -> np.ndarray:
    """How far a hexagon about the axis reaches toward each wall at each turn: its corner
    furthest that way. One row per turn, one column per wall."""
    angles = np.radians(turns[:, None] + 60.0 * np.arange(6)[None, :])
    points = corners / 2 * np.stack([np.cos(angles), np.sin(angles)], axis=2)
    return np.asarray((points @ toward.T).max(axis=1))


def _best(score: Any, coarse: np.ndarray) -> tuple[float, float]:
    """The turn at which `score`, a figure for each of an array of turns, is least: over the
    coarse turns, then near the best of them."""
    found = np.asarray(score(coarse))
    start = float(coarse[int(np.argmin(found))])
    near = np.arange(start - TURN_NEAR, start + TURN_NEAR + TURN_FINE / 2, TURN_FINE)
    again = np.asarray(score(near))
    best = int(np.argmin(again))
    return float(near[best] % 60.0), float(again[best])


def deepest_in(segments: np.ndarray, corners: float, turn: float, centre: Any) -> float:
    """The furthest any point of `segments`, pairs of points in the plane, lies inside a
    hexagon, from its nearest side: more than 0 where something is in the nut's way, and
    otherwise how near the nearest comes, as less than 0."""
    if not len(segments):
        return -math.inf
    inside = corners / 2 * math.cos(math.pi / 6)
    angles = np.radians(turn + 30.0 + 60.0 * np.arange(6))
    sides = np.stack([np.cos(angles), np.sin(angles)], axis=1)
    start = np.asarray(segments[:, 0], dtype=float) - np.asarray(centre, dtype=float)
    run = np.asarray(segments[:, 1], dtype=float) - np.asarray(segments[:, 0], dtype=float)
    # Along each segment, its depth from each side is a straight line in t; the least of six
    # is highest at an end or where two of them cross.
    base = inside - start @ sides.T
    slope = -(run @ sides.T)
    candidates = [np.zeros(len(start)), np.ones(len(start))]
    for i in range(6):
        for j in range(i + 1, 6):
            gap = slope[:, i] - slope[:, j]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = np.where(np.abs(gap) > 1e-15, (base[:, j] - base[:, i]) / gap, 0.0)
            candidates.append(np.clip(t, 0.0, 1.0))
    t = np.stack(candidates, axis=1)
    depth = (base[:, None, :] + t[:, :, None] * slope[:, None, :]).min(axis=2)
    return float(depth.max())


def nut_goes_in(found: dict[str, Any], nut: Any, room: float) -> dict[str, Any]:
    """Whether `nut` goes into the pocket `pocket` found: its hexagon, at its least size across
    its corners, within every wall in some turn, its middle on the bolt's axis or as far off
    it as the bolt has `room` to move in its hole. Where it does, the turn and how far off the
    axis; where it doesn't, the turn with its middle on the axis at which its corners pass
    the walls least, each wall they pass, and by how much; and the walls' outline, for the
    reading to be checked against."""
    first, second = basis(found["way"])
    toward = np.array([[np.dot(f["toward"], first), np.dot(f["toward"], second)]
                       for f in found["facing"]])  # fmt: skip
    toward /= np.linalg.norm(toward, axis=1)[:, None]
    distance = np.array([f["distance"] for f in found["facing"]])
    corners = nut.corners_mm
    size = 2 * corners + room + max(distance.max(), 1.0)
    coarse = np.arange(0.0, 60.0, TURN_STEP)
    turn, worst = _best(lambda t: (_reaches(toward, corners, t) - distance).max(axis=1), coarse)
    off, centre, least = 0.0, np.zeros(2), 0.0
    if worst > SAME:

        def shift(turns: np.ndarray) -> list[float]:
            out = []
            for one in turns:
                limits = distance - _reaches(toward, corners, np.array([one]))[0]
                at = _off_axis(_region(toward, limits, size))
                out.append(math.inf if at is None else at[0])
            return out

        shifted, least = _best(shift, coarse)
        if least <= room + SAME:
            turn, off = shifted, least
            limits = distance - _reaches(toward, corners, np.array([turn]))[0]
            at = _off_axis(_region(toward, limits, size))
            assert at is not None
            centre = at[1]
    goes_in = worst <= SAME or off > 0
    reach = _reaches(toward, corners, np.array([turn]))[0] + toward @ centre
    # A wall split into several faces in one plane is one wall.
    met: dict[tuple[float, ...], dict[str, Any]] = {}
    for i in range(len(distance)):
        if reach[i] - distance[i] > SAME:
            plane = tuple(np.round([*found["facing"][i]["toward"], distance[i]], 6).tolist())
            met[plane] = {"toward": [round(c, 6) + 0.0 for c in found["facing"][i]["toward"]],
                          "distance_mm": float(distance[i]),
                          "by_mm": float(reach[i] - distance[i])}  # fmt: skip
    outline = _region(toward, distance, size)
    edges = (
        np.stack([outline, np.roll(outline, -1, axis=0)], axis=1)
        if len(outline)
        else np.zeros((0, 2, 2))
    )
    return {
        "goes_in": goes_in,
        "turn_deg": turn,
        "off_axis_mm": off,
        "centre": centre.tolist(),
        "least_off_axis_mm": None if goes_in or least == math.inf else least,
        "met": sorted(met.values(), key=lambda m: -m["by_mm"]),
        "deepest_mm": deepest_in(edges, corners, turn, centre),
        "basis": (first.tolist(), second.tolist()),
    }
