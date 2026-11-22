"""Whether the head clears the objects already printed, read from a by-object slice's own moves.

mqucifer/print-shop#109. Printed by object, the head comes down to the plate beside objects that
are finished. The machine profile says what room that takes: `extruder_clearance_radius` round
the nozzle (and `extruder_clearance_max_radius`, the larger, which is the one held to here),
`extruder_clearance_height_to_rod` up to the rod that spans the plate, and
`extruder_clearance_height_to_lid` up to the lid.

**Orca's exit 0 isn't proof of any of it.** Tested on 2.4.2 with made-up boxes
(`~/print-shop/proofs/ux-109/`): its arranger spaces the objects' outlines by the radius and
counts no brim, so a 5 mm brim brought the nozzle inside it, and it said nothing. So every move
is read against each object finished before it:
- **the head:** a move under an object's top stays the radius or more from its material;
- **the rod:** past an object taller than the rod's height, a move under its top stays the
  radius or more from it in y, as the rod spans the plate in x;
- **the lid:** nothing is printed after an object taller than the lid's height.

What the printer's own macros do (homing, the nozzle's wipe) isn't in the G-code, so isn't read.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from print_shop.slicer.gcode import Sliced

__all__ = ["Clearance", "Hit", "Limit", "Run", "first_hit", "runs"]

#: Two heights or distances this close, in mm, are the same.
_EPS = 1e-3


class Limit(StrEnum):
    """Which of the machine's clearances."""

    HEAD = "head"
    ROD = "rod"
    LID = "lid"


@dataclass(frozen=True)
class Clearance:
    """The room the machine profile says printing by object takes, in mm."""

    radius: float
    rod: float
    lid: float

    @classmethod
    def of(cls, machine: dict[str, Any]) -> Clearance | None:
        """The machine profile's, or None where it doesn't give all three: then nothing can be
        checked, so nothing is printed by object."""

        def number(key: str) -> float:
            value = machine.get(key)
            if isinstance(value, list):
                value = value[0] if value else None
            try:
                return float(str(value))
            except ValueError:
                return 0.0

        radius = number("extruder_clearance_radius")
        rod = number("extruder_clearance_height_to_rod")
        lid = number("extruder_clearance_height_to_lid")
        if not (radius > 0 and rod > 0 and lid > 0):
            return None
        return cls(radius=max(radius, number("extruder_clearance_max_radius")), rod=rod, lid=lid)


@dataclass(frozen=True)
class Run:
    """One object's stretch of a by-object slice: its layers, counted through the whole print."""

    name: str
    first: int
    last: int


@dataclass(frozen=True)
class Hit:
    """A move that breaks a clearance against an object printed before it."""

    limit: Limit
    #: The finished object it's too near, as the G-code names it.
    passed: str
    #: That object's height, in mm.
    height: float
    #: How far inside the clearance it comes, in mm: 0 for the lid, which no spacing mends.
    short: float


def runs(sliced: Sliced) -> tuple[Run, ...] | None:
    """Each object's stretch, in the order they print, or None where the slice doesn't print
    them one after another: two objects on a layer, one returned to, or none named."""
    found: list[tuple[str, int]] = []  # each object, and its first layer
    for layer in sliced.layers:
        laid = [name for name, length in layer.objects.items() if length > 0]
        if len(laid) > 1:
            return None
        if not laid or (found and found[-1][0] == laid[0]):
            continue
        if laid[0] in (name for name, _ in found):
            return None
        found.append((laid[0], layer.n if found else 1))
    if not found:
        return None
    lasts = [first - 1 for _, first in found[1:]] + [len(sliced.layers)]
    return tuple(Run(name, first, last) for (name, first), last in zip(found, lasts, strict=True))


def first_hit(sliced: Sliced, order: tuple[Run, ...], room: Clearance) -> Hit | None:
    """The first clearance broken, object by object in the order they print, or None. `sliced`
    was read with its moves (`keep_moves`).

    Raises:
        ValueError: If the moves weren't kept, or an object lays nothing: it can't be checked,
            which is never read as clear.
    """
    if not sliced.head:
        raise ValueError("the slice's moves weren't kept, so the head's clearance can't be read")
    moves = np.array(sliced.head, dtype=float)
    layer, width, laying = moves[:, 0], moves[:, 7], moves[:, 8] > 0
    starts, ends = moves[:, 1:3], moves[:, 4:6]
    lowest = np.minimum(moves[:, 3], moves[:, 6])
    index = np.arange(len(moves))
    for at, run in enumerate(order):
        own = laying & (layer >= run.first) & (layer <= run.last)
        if not own.any():
            raise ValueError(f"{run.name} lays nothing, so its clearance can't be read")
        tops = moves[own, 6]
        height = float(tops.max())
        if at + 1 < len(order) and height > room.lid + _EPS:
            return Hit(Limit.LID, run.name, height, 0.0)
        # Its body is what stands: a brim is the first layer alone, under any nozzle.
        stands = tops > float(tops.min()) + _EPS
        body = stands if stands.any() else np.ones(len(tops), dtype=bool)
        points = np.concatenate([starts[own][body], ends[own][body]])
        hull = _hull(points)
        half = float(width[own][body].max()) / 2
        reach = room.radius + half  # the lines are read at their middles
        after = (index > int(index[own][-1])) & (lowest < height - _EPS)
        if not after.any():
            continue
        a, b = starts[after], ends[after]
        low, high = hull.min(axis=0), hull.max(axis=0)
        apart = np.maximum(np.maximum(low - np.maximum(a, b), np.minimum(a, b) - high), 0.0)
        if height > room.rod + _EPS:
            nearest = float(apart[:, 1].min())
            if nearest < reach - _EPS:
                return Hit(Limit.ROD, run.name, height, reach - nearest)
        near = np.hypot(apart[:, 0], apart[:, 1]) < reach
        if near.any():
            nearest = _gap(a[near], b[near], hull)
            if nearest < reach - _EPS:
                return Hit(Limit.HEAD, run.name, height, reach - nearest)
    return None


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.asarray(a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0])


def _hull(points: np.ndarray) -> np.ndarray:
    """The points' convex hull, anticlockwise, to a hundredth of a mm (Andrew's chain)."""
    ordered = [tuple(p) for p in np.unique(np.round(points, 2), axis=0).tolist()]
    if len(ordered) < 3:
        return np.array(ordered)

    def chain(through: list[tuple[float, ...]]) -> list[tuple[float, ...]]:
        kept: list[tuple[float, ...]] = []
        for p in through:
            while len(kept) >= 2:
                o, a = kept[-2], kept[-1]
                if (a[0] - o[0]) * (p[1] - o[1]) - (a[1] - o[1]) * (p[0] - o[0]) > 0:
                    break
                kept.pop()
            kept.append(p)
        return kept[:-1]

    return np.array(chain(ordered) + chain(ordered[::-1]))


def _to_segment(point: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Each point's distance to its segment."""
    along = b - a
    length2 = (along * along).sum(axis=-1)
    share = np.clip(((point - a) * along).sum(axis=-1) / np.where(length2 > 0, length2, 1.0), 0, 1)
    return np.asarray(np.linalg.norm(point - (a + along * share[..., None]), axis=-1))


def _gap(a: np.ndarray, b: np.ndarray, hull: np.ndarray) -> float:
    """The least distance from the segments `a` to `b` to a convex hull: 0 where one is in it."""
    edges = list(zip(hull, np.roll(hull, -1, axis=0), strict=True))
    if len(hull) >= 3:
        inside = np.ones(len(a), dtype=bool)
        for v, w in edges:
            inside &= _cross(w - v, a - v) >= 0
        if inside.any():
            return 0.0
    nearest = np.inf
    for v, w in edges:
        crossed = (_cross(w - v, a - v) * _cross(w - v, b - v) < 0) & (
            _cross(b - a, v - a) * _cross(b - a, w - a) < 0
        )
        if crossed.any():
            return 0.0
        nearest = min(nearest, float(_to_segment(a, v, w).min()),
                      float(_to_segment(b, v, w).min()), float(_to_segment(v, a, b).min()))
    return nearest
