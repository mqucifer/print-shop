"""A through-bolt's hole, its seats and its nut's pocket, read from a part's own faces
(mqucifer/print-shop#168).

A through-bolt passes right through two parts, with a washer under its head and a nut on the far
side, on a washer or in a pocket that holds it by its flats. What `load` and `fit-bed` need from
each part is the same: the round hole along the bolt's axis, where it ends, and what's round that
end. `load` reads a part's faces with FreeCAD and `fit-bed` with OpenCascade through CadQuery, so
each hands its faces here as plain rows and the reading is one, in plain Python and NumPy:

- a **cylinder** row: `axis`, a unit vector; `origin`, a point on it; `radius`; `hole`, whether
  the material is outside it; `low` and `high`, where the face runs along the axis, as a point's
  dot product with `axis`; and `turns`, how far round it goes, in radians;
- a **plane** row: `normal`, out of the material; `at`, a point on it; and `low` and `high`, where
  the face runs along whatever axis the caller is reading, filled in by `along`.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

#: A bolt's place is on its hole's axis within this, in mm: a point read off a drawing.
ON_AXIS = 0.05
#: Two faces are at one level, one radius or one distance within this, in mm.
SAME = 1e-4
#: Two directions are the same, or square, within this.
SQUARE = 1e-6
#: A pocket's play is what the file says within this, in mm.
PLAY_TOL = 0.005


def bore(cylinders: list[dict[str, Any]], point: Any) -> dict[str, Any] | str:
    """The round hole whose axis passes through `point`: its `axis`, a point on it, its
    `radius`, and `low` and `high`, where it runs along the axis. Where holes of several sizes
    share the axis, a counterbore over a hole, it's the narrowest: the one the bolt's shank is
    in. Or, as words, why there's none."""
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
    mine = [c for c in found if c["radius"] <= radius + SAME]
    if sum(c["turns"] for c in mine) < 2 * math.pi - 1e-3:
        return "the round face there doesn't go all the way round: a slot's end, not a hole"
    spans = []
    for one in mine:
        same_way = float(np.asarray(one["axis"]) @ first) > 0
        low, high = (one["low"], one["high"]) if same_way else (-one["high"], -one["low"])
        spans.append((low, high))
    stretches: list[list[float]] = []
    for start, end in sorted(spans):
        if stretches and start <= stretches[-1][1] + SAME:
            stretches[-1][1] = max(stretches[-1][1], end)
        else:
            stretches.append([start, end])
    # Where the axis has more than one stretch of hole, the bolt's place says which: the nearest.
    here = float(at @ first)
    low, high = min(stretches, key=lambda s: abs(here - min(max(here, s[0]), s[1])))
    origin = np.asarray(mine[0]["origin"], dtype=float)
    return {"axis": first, "origin": origin, "radius": float(radius), "low": low, "high": high}


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


def along(planes: list[dict[str, Any]], axis: Any, corners: list[np.ndarray]) -> None:
    """Fill in where each plane's face runs along `axis`, from its own corners."""
    way = np.asarray(axis, dtype=float)
    for plane, points in zip(planes, corners, strict=True):
        reach = np.asarray(points, dtype=float) @ way
        plane["low"], plane["high"] = float(reach.min()), float(reach.max())


def pocket(
    planes: list[dict[str, Any]], hole: dict[str, Any], seat: float, out: Any, reach: float
) -> dict[str, Any] | str:
    """The pocket a nut sits in at a hole's end: the flat walls that stand on the seat's level,
    `seat` along `out`, the way out of the part there, and face the hole's axis from within
    `reach` of it. A nut is held from turning by walls in opposite pairs, on its flats: each
    pair's distance apart is an `across_flats_mm`, the narrowest first, with how deep the
    pocket is and how far off its middle the axis is. Or why there's no pocket."""
    way = np.asarray(out, dtype=float)
    origin = np.asarray(hole["origin"], dtype=float)
    walls = []
    for plane in planes:
        normal = np.asarray(plane["normal"], dtype=float)
        if abs(float(normal @ way)) > SQUARE:
            continue
        low, high = sorted((plane["low"], plane["high"]))
        if abs(low - seat) > 1e-3 or high <= seat + SAME:
            continue
        off = origin - np.asarray(plane["at"], dtype=float)
        distance = float((off - (off @ way) * way) @ normal)
        if 0 < distance <= reach:
            walls.append((distance, normal, high - seat))
    pairs = []
    for i, (near, normal, depth) in enumerate(walls):
        for far, other, deep in walls[i + 1 :]:
            if float(normal @ other) < -1 + SQUARE:
                pairs.append((near + far, abs(near - far) / 2, min(depth, deep)))
    if not pairs:
        return (
            f"no pocket there holds a nut by its flats: {len(walls)} flat wall(s) stand on the "
            "hole's end facing its axis, and none faces another across it"
        )
    pairs.sort()
    return {
        "across_flats_mm": pairs[0][0],
        "every_across_mm": [p[0] for p in pairs],
        "off_middle_mm": max(p[1] for p in pairs),
        "depth_mm": min(p[2] for p in pairs),
        "walls": len(walls),
        "pairs": len(pairs),
    }


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
