"""How far apart two bodies surely are, from their bounds alone (mqucifer/print-shop#354).

`clearance` reads a pair farther apart than any gap it judges by these, not by its exact distance,
about a second a pair. Each figure is never more than the pair's true least gap: a body lies inside
its padded axis-aligned box and inside its padded convex hull, and a gap along any way is no more
than the gap itself, as a projection never lengthens a distance. Only numpy, so the bound is
tested with no CAD.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: The least a STEP body is padded by, in mm, for the curve between its fine mesh's chords. Each
#: body is padded by `PAD_TIMES` what its own triangulation says it's off the surface, where
#: that's more (`clearance.pad_of`): BRepMesh misses its nominal 0.002 mm on B-spline faces, by
#: up to 11 times on a swept spline (mqucifer/print-shop#354).
HULL_PAD = 0.05
#: How many times its triangulation's own deflection a body is padded by.
PAD_TIMES = 2.0


@dataclass
class Bounds:
    """What a body surely lies inside, each figure padded by `pad`: its axis-aligned box, and its
    hull's points with their middle and main axes."""

    low: np.ndarray
    high: np.ndarray
    hull: np.ndarray
    middle: np.ndarray
    axes: np.ndarray
    pad: float


def bounds_from(low: np.ndarray, high: np.ndarray, hull: np.ndarray, pad: float) -> Bounds:
    """A body's bounds from its box and its hull's points: the points' middle, and their main
    axes, the ways a long or flat body is narrowest and widest."""
    points = np.asarray(hull, dtype=float)
    middle = points.mean(axis=0)
    axes = np.linalg.svd(points - middle, full_matrices=False)[2]
    if axes.shape != (3, 3):
        axes = np.eye(3)
    return Bounds(np.asarray(low, float), np.asarray(high, float), points, middle, axes, pad)


def least_apart(a: Bounds, b: Bounds) -> tuple[float, float]:
    """(by the boxes, by the hulls), in mm, each no more than the pair's true least gap, and less
    than nothing where they can't tell the pair apart. The boxes give the distance between them;
    the hulls the most they're apart along the way from one's middle to the other's, or along
    either's main axes, which holds a turned part that its box doesn't."""
    pads = a.pad + b.pad
    apart = np.maximum(0.0, np.maximum(b.low - a.high, a.low - b.high))
    by_box = float(np.linalg.norm(apart)) - pads
    ways = np.vstack([b.middle - a.middle, a.axes, b.axes])
    lengths = np.linalg.norm(ways, axis=1)
    kept = lengths > 1e-9
    ways = ways[kept] / lengths[kept, None]
    if not len(ways):
        return by_box, -math.inf
    on_a, on_b = a.hull @ ways.T, b.hull @ ways.T
    along = np.maximum(on_b.min(axis=0) - on_a.max(axis=0), on_a.min(axis=0) - on_b.max(axis=0))
    return by_box, float(along.max()) - pads
