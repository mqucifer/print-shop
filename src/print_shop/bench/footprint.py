"""A part's footprint turned about the bed's upright axis to fit (mqucifer/print-shop#156).

`fit-bed`'s own reading of whether a part's footprint fits the bed, and by what turn. It's plain
Python and NumPy, as `limits` is, so the settings study reads the same turn outside the bench's
image (mqucifer/print-shop#351): a part that fits only turned on the plate is studied in the
turn `fit-bed` gives it.
"""

from __future__ import annotations

import math

import numpy as np

from .limits import over

__all__ = ["TURN", "footprint", "hull"]

#: How far the footprint is turned between readings, in degrees.
TURN = 0.25


def hull(points: np.ndarray) -> np.ndarray:
    """The convex hull of points seen from above, as a closed ring of its corners: its first
    corner again at its end. Points in a line are its two ends, and one point is itself."""
    unique = np.unique(np.asarray(points, dtype=float)[:, :2], axis=0)
    if len(unique) < 3:
        return np.vstack([unique, unique[:1]])

    def half(ordered: np.ndarray) -> list[np.ndarray]:
        kept: list[np.ndarray] = []
        for p in ordered:
            while len(kept) >= 2:
                (ax, ay), (bx, by) = kept[-2], kept[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                kept.pop()
            kept.append(p)
        return kept

    # Andrew's monotone chain: its lower half, left to right, then its upper, right to left.
    lower, upper = half(unique), half(unique[::-1])
    ring = np.array(lower[:-1] + upper[:-1])
    return np.vstack([ring, ring[:1]])


def footprint(points: np.ndarray, bed: tuple[float, float]) -> tuple[float, float, float]:
    """The least turn about the upright axis at which a footprint fits the bed, in degrees, or
    where none does, the turn at which it needs least of it; and its width and depth there.
    `points` are the part's points seen from above. A turn is anticlockwise seen from above:
    the third of the print loop's Euler turns (`ingest.orientation`), and OpenCascade's about
    +z."""
    outline = hull(points)
    edges = np.diff(outline, axis=0)
    angles = set(np.arange(0.0, 180.0, TURN).round(6))
    # Each of the hull's own edges laid along the bed's width, and along its depth.
    for dx, dy in edges:
        if abs(dx) + abs(dy) > 1e-9:
            turn = -math.degrees(math.atan2(dy, dx))
            angles.update((round(turn % 180.0, 6), round((turn + 90.0) % 180.0, 6)))
    best: tuple[float, float, float, float] | None = None
    for angle in sorted(angles):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        turned = outline @ np.array([[c, s], [-s, c]])
        width, depth = float(np.ptp(turned[:, 0])), float(np.ptp(turned[:, 1]))
        if not over(width, bed[0]) and not over(depth, bed[1]):
            # The least turn that fits: as it's drawn, where that does.
            return angle, width, depth
        need = max(width / bed[0], depth / bed[1])
        if best is None or need < best[0] - 1e-12:
            best = (need, angle, width, depth)
    assert best is not None
    return best[1], best[2], best[3]
