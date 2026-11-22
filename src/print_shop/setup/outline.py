"""How far points are, sideways, from a part's outline at their heights below: all at once.

The overhang reading (`features.py`) asks this of every point it samples. It was asked a height
at a time, each a cut through the whole mesh, and a point far from the outline was read against
every line of it. A flat plate on edge has thousands of heights, and a wide ceiling millions of
points: that was the wait in mqucifer/print-shop#235. Here the cuts are made together, a point
meets only the lines near it, or, for which side it's on, those level with it, and what's held at
once is bounded by `AT_ONCE`.

**The answers are the ones it gave a height at a time, bit for bit,** and
`tests/test_overhang_unchanged.py` holds them to that. So the arithmetic is trimesh's
`mesh_plane`'s, the lines of a cut keep the order it returns them in, and of lines equally near
the first decides the side.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import trimesh
from trimesh.constants import tol

__all__ = ["AT_ONCE", "REACH_MM", "Outlines", "lengths"]

#: How far a point is measured exactly. Past it, it's only inside or outside the outline.
REACH_MM = 4.0
#: The most lines, or pairs of a point and a line, held at once.
AT_ONCE = 1 << 16
#: A hair over the reach, so rounding drops no line that's at it.
_NEAR_MM = REACH_MM + 1e-6
#: For each corner of a triangle, the other two, in the triangle's order.
_OTHERS = np.array([[1, 2], [0, 2], [0, 1]])


@dataclass(frozen=True)
class _Lines:
    """The outline at some heights: each line's height, by number, its ends and its outward
    side. They're sorted by height, then as `mesh_plane` orders a height's lines."""

    level: np.ndarray
    a: np.ndarray
    b: np.ndarray
    outward: np.ndarray


def lengths(vectors: np.ndarray) -> np.ndarray:
    """`np.linalg.norm` along the last axis, to the bit: the squares added in the same order.
    For rows of two or three it's several times quicker."""
    total = vectors[..., 0] * vectors[..., 0]
    for n in range(1, vectors.shape[-1]):
        total += vectors[..., n] * vectors[..., n]
    found: np.ndarray = np.sqrt(total)
    return found


class Outlines:
    """A mesh's outline at any height, to measure points from."""

    def __init__(self, mesh: trimesh.Trimesh) -> None:
        # Plain arrays: trimesh hashes its own at each use.
        self._vertices = np.array(mesh.vertices)
        self._faces = np.array(mesh.faces)
        self._outward = np.array(mesh.face_normals[:, :2])
        self._z = self._vertices[self._faces, 2]
        self._low, self._high = self._z.min(axis=1), self._z.max(axis=1)

    def reach(self, points: np.ndarray, heights: np.ndarray) -> np.ndarray:
        """How far each point is, sideways, from the outline at its height.

        0 where the part is right under it, exact up to `REACH_MM`, and infinite further out.
        Heights are read to 0.01 mm, a hair above, so an outline isn't cut along a flat face.
        """
        levels, level = np.unique(np.round(heights, 2) + 1e-3, return_inverse=True)
        out = np.full(len(points), np.inf)
        order = np.argsort(level)
        ranked = level[order]
        # The heights each face spans, as numbers into `levels`.
        first = np.searchsorted(levels, self._low, "left")
        after = np.searchsorted(levels, self._high, "right")
        count = len(levels) + 1
        spanning = np.cumsum(
            np.bincount(first, minlength=count) - np.bincount(after, minlength=count)
        )
        held = np.concatenate([[0], np.cumsum(spanning[:-1])])
        start = 0
        while start < len(levels):
            end = max(start + 1, int(np.searchsorted(held, held[start] + AT_ONCE, "right")) - 1)
            lines = self._cut(levels[start:end], first - start, after - start)
            mine = order[np.searchsorted(ranked, start):np.searchsorted(ranked, end)]
            out[mine] = _outside(points[mine, :2], level[mine] - start, end - start, lines)
            start = end
        return out

    def _cut(self, levels: np.ndarray, first: np.ndarray, after: np.ndarray) -> _Lines:
        """The outline at each of `levels`, as `trimesh.intersections.mesh_plane` cuts one."""
        first = np.clip(first, 0, len(levels))
        spans = np.clip(after, 0, len(levels)) - first
        face = np.repeat(np.arange(len(spans)), spans)
        level = np.arange(len(face)) + np.repeat(first - (np.cumsum(spans) - spans), spans)
        height = levels[level]
        off = self._z[face] - height[:, None]
        below, above = off < -tol.merge, off > tol.merge
        on = ~below & ~above
        below_n, above_n, on_n = _in_row(below), _in_row(above), _in_row(on)

        # Two corners one side and one the other: from the lone one to each of the two.
        through = np.flatnonzero((on_n == 0) & (below_n > 0) & (above_n > 0))
        lone = np.where((above_n[through] == 1)[:, None], above[through], below[through]).argmax(1)
        corners = self._faces[face[through]]
        rows = np.arange(len(through))
        at = height[through]
        through_a = self._crossing(corners[rows, lone], corners[rows, (lone + 1) % 3], at)
        through_b = self._crossing(corners[rows, lone], corners[rows, (lone + 2) % 3], at)

        # One corner on the height, with the other two either side of it.
        touching = np.flatnonzero((on_n == 1) & (below_n == 1) & (above_n == 1))
        lone = on[touching].argmax(axis=1)
        corners = self._faces[face[touching]]
        rows = np.arange(len(touching))
        others = _OTHERS[lone]
        touching_a = self._vertices[corners[rows, lone], :2]
        touching_b = self._crossing(
            corners[rows, others[:, 0]], corners[rows, others[:, 1]], height[touching]
        )

        # An edge along the height, with the rest of its face above.
        along = np.flatnonzero((on_n == 2) & (above_n == 1))
        others = _OTHERS[above[along].argmax(axis=1)]
        corners = self._faces[face[along]]
        rows = np.arange(len(along))
        along_a = self._vertices[corners[rows, others[:, 0]], :2]
        along_b = self._vertices[corners[rows, others[:, 1]], :2]

        kept = np.concatenate([through, touching, along])
        order = np.argsort(level[kept], kind="stable")
        return _Lines(
            level=level[kept][order],
            a=np.concatenate([through_a, touching_a, along_a])[order],
            b=np.concatenate([through_b, touching_b, along_b])[order],
            outward=self._outward[face[kept]][order],
        )

    def _crossing(self, start: np.ndarray, end: np.ndarray, height: np.ndarray) -> np.ndarray:
        """Where the edges from `start` to `end` cross their heights, in x and y."""
        origin = self._vertices.take(start, axis=0)
        # trimesh's own, for its rounding.
        along = trimesh.util.unitize(self._vertices.take(end, axis=0) - origin)
        far = (height - origin[:, 2]) / along[:, 2]
        crossed: np.ndarray = origin[:, :2] + far[:, None] * along[:, :2]
        return crossed


def _in_row(corners: np.ndarray) -> np.ndarray:
    """How many of each triangle's three corners: many times quicker than summing along rows."""
    each = corners.view(np.int8)
    count: np.ndarray = each[:, 0] + each[:, 1] + each[:, 2]
    return count


def _ranges(first: np.ndarray, after: np.ndarray) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Each range's number beside each place in it, `AT_ONCE` at most at a time."""
    counts = np.maximum(after - first, 0)
    before = np.concatenate([[0], np.cumsum(counts)])
    for start in range(0, int(before[-1]), AT_ONCE):
        end = min(start + AT_ONCE, int(before[-1]))
        low = int(np.searchsorted(before, start, "right")) - 1
        high = int(np.searchsorted(before, end, "left"))
        held = np.minimum(before[low + 1:high + 1], end) - np.maximum(before[low:high], start)
        which = np.repeat(np.arange(low, high), held)
        yield which, np.arange(start, end) - before[which] + first[which]


def _outside(points: np.ndarray, level: np.ndarray, levels: int, lines: _Lines) -> np.ndarray:
    """Each 2D point's distance outside its height's outline: 0 inside it.

    Inside or outside by the nearest line's outward side. A point with none within `REACH_MM`,
    or none whose side is clear, is decided by a ray's crossings instead.
    """
    out = np.full(len(points), np.inf)
    if not len(lines.level) or not len(points):
        return out
    up = np.argsort(points[:, 1])
    ys = points[up, 1]
    # Each point's place up y, level ones sharing the first's: what `searchsorted` would give.
    starts = np.arange(len(ys))
    starts[1:][ys[1:] == ys[:-1]] = 0
    place = np.empty(len(ys), dtype=np.int64)
    place[up] = np.maximum.accumulate(starts)
    nearest, line = _nearest(points, level, levels, lines, ys, place)
    near = np.flatnonzero(nearest <= REACH_MM)
    gap = _gap(points[near], lines.a[line[near]], lines.b[line[near]])
    out_at = lines.outward[line[near]]
    side = gap[:, 0] * out_at[:, 0] + gap[:, 1] * out_at[:, 1]
    touches = nearest[near] <= 1e-9
    clear = (np.abs(side) > 1e-9) | touches
    out[near] = np.where((side > 0) & ~touches, nearest[near], 0.0)
    undecided = np.ones(len(points), dtype=bool)
    undecided[near[clear]] = False
    ask = np.flatnonzero(undecided)
    if len(ask):
        inside = _inside(points[ask], level[ask], lines, ys, place[ask])
        out[ask] = np.where(inside, 0.0, np.inf)
    return out


def _gap(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """From the nearest place on each line to its point."""
    ab = b - a
    along, square = (p - a) * ab, ab * ab
    t = np.clip((along[:, 0] + along[:, 1]) / np.maximum(square[:, 0] + square[:, 1], 1e-12), 0, 1)
    gap: np.ndarray = p - (a + t[:, None] * ab)
    return gap


def _nearest(
    points: np.ndarray, level: np.ndarray, levels: int, lines: _Lines, ys: np.ndarray,
    place: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Each point's distance to its nearest line, where that's within reach, and which line:
    the first of those equally near.

    The points are sorted by height, by a column `REACH_MM` wide in x, and by y, so a line
    finds those within reach of its box without reading the others. `ys` are the points' y in
    order, and `place` each point's place among them.
    """
    column = np.floor(points[:, 0] / REACH_MM).astype(np.int64)
    least = column.min()
    columns = int(column.max() - least) + 1
    places = len(ys) + 1
    key = (level * columns + column - least) * places + place
    order = np.argsort(key)
    key = key[order]

    low, high = np.minimum(lines.a, lines.b) - _NEAR_MM, np.maximum(lines.a, lines.b) + _NEAR_MM
    left = np.clip(np.floor(low[:, 0] / REACH_MM) - least, 0, columns).astype(np.int64)
    right = np.clip(np.floor(high[:, 0] / REACH_MM) - least + 1, 0, columns).astype(np.int64)
    from_y = np.searchsorted(ys, low[:, 1], "left")
    to_y = np.searchsorted(ys, high[:, 1], "right")

    nearest = np.full(len(points), np.inf)
    none = len(lines.level)
    line = np.full(len(points), none)
    for which, at_column in _ranges(left, right):
        base = (lines.level[which] * columns + at_column) * places
        first = np.searchsorted(key, base + from_y[which])
        after = np.searchsorted(key, base + to_y[which])
        for pair, at in _ranges(first, after):
            mine, point = which[pair], order[at]
            gap = _gap(points[point], lines.a[mine], lines.b[mine])
            far = lengths(gap)
            before = nearest[point]
            np.minimum.at(nearest, point, far)
            now = nearest[point]
            line[point[now < before]] = none
            least_far = far == now
            np.minimum.at(line, point[least_far], mine[least_far])
    return nearest, line


def _inside(
    points: np.ndarray, level: np.ndarray, lines: _Lines, ys: np.ndarray, place: np.ndarray
) -> np.ndarray:
    """Whether each 2D point is inside its height's outline, by the crossings of a ray along
    +x: each line meets only the points its ends are either side of, in y."""
    places = len(ys) + 1
    key = level * places + place
    order = np.argsort(key)
    key = key[order]
    a, b = lines.a, lines.b
    base = lines.level * places
    first = np.searchsorted(key, base + np.searchsorted(ys, np.minimum(a[:, 1], b[:, 1])))
    after = np.searchsorted(key, base + np.searchsorted(ys, np.maximum(a[:, 1], b[:, 1])))
    crossings = np.zeros(len(points), dtype=np.int64)
    ab = b - a
    for which, at in _ranges(first, after):
        point = order[at]
        rise = ab[which, 1]
        x_at = a[which, 0] + (points[point, 1] - a[which, 1]) * ab[which, 0] / np.where(
            rise == 0, 1, rise
        )
        np.add.at(crossings, point[points[point, 0] < x_at], 1)
    inside: np.ndarray = crossings % 2 == 1
    return inside
