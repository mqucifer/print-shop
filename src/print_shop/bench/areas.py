"""Exact areas of a printed part's layers inside its mesh's elements and its skin's wedges
(mqucifer/print-shop#363), with no sampling.

Orca's layers are 2D rings: each layer's sparse area, holes and all, by the even-odd rule
(`classify.Layered.sparse`). An element's section at a layer's height, straight-sided between
its corners, is a convex polygon, and so is a wedge's. So the area of an element's section
inside the core is a convex polygon clipped by rings, by Green's theorem: the polygon's edges
inside the rings and the rings' edges inside the polygon, each ring turned so the core is on
its left, holes the other way (`inside_rings`); and its section inside a wedge's is two convex
polygons clipped (`clip`). Each summed over the layers an element spans, by how far it spans
each, is its volume in the core and in the wedges exactly as the layers have them
(`classify.exact`), where sampling it at 64 points of a 5 mm element was 5.8% out on a region's
core, and points 0.3 mm apart need hundreds of thousands in a 20 mm element.
"""

from __future__ import annotations

import numpy as np

#: A tetrahedron's six edges and a wedge's straight-sided prism's nine, by their corners.
TET_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
PRISM_EDGES = ((0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5))
#: How near two of a section's corners are taken for one, in mm.
SAME = 1e-9
#: How far a layer's rings are moved before a polygon's area inside them is read, in mm, along
#: a way nothing of a part lies along: a wedge's inner face is its skin's depth in from its
#: face, as the core's edge is, so the two lie on one line, and a line on a line is read twice
#: or not at all. Moved so, either is read once, its area out by the rings' length times this.
NUDGE = np.array([1e-7 * 2**0.5, 1e-7 * 3**0.5])


def sections(corners: np.ndarray, edges: tuple[tuple[int, int], ...], axis: int,
             at: float | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each shape's section at `at` along `axis`, one height or one each, from its `corners`
    `[n, k, 3]` and `edges`: a convex polygon in the other two axes, its corners turned
    anticlockwise, padded to the most any has: `[n, v, 2]`, and how many each has."""
    first, second = (a for a in range(3) if a != axis)
    heights = np.broadcast_to(np.asarray(at, dtype=float), (len(corners),))[:, None]
    a = corners[:, [i for i, _ in edges]]
    b = corners[:, [j for _, j in edges]]
    da, db = a[..., axis] - heights, b[..., axis] - heights
    span = db - da
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(np.abs(span) > 0, -da / span, np.nan)
    hit = np.isfinite(t) & (t >= -1e-12) & (t <= 1 + 1e-12)
    t = np.clip(np.nan_to_num(t), 0.0, 1.0)
    point = a + t[..., None] * (b - a)
    flat = point[..., [first, second]]
    count = hit.sum(axis=1)
    middle = np.where(count[:, None] > 0,
                      (flat * hit[..., None]).sum(axis=1) / np.maximum(count, 1)[:, None], 0.0)
    angle = np.arctan2(flat[..., 1] - middle[:, None, 1], flat[..., 0] - middle[:, None, 0])
    angle = np.where(hit, angle, np.inf)
    order = np.argsort(angle, axis=1, kind="stable")
    flat = np.take_along_axis(flat, order[..., None], axis=1)
    hit = np.take_along_axis(hit, order, axis=1)
    # A corner met by two edges at once is one corner.
    previous = np.roll(flat, 1, axis=1)
    last = np.take_along_axis(flat, np.maximum(count - 1, 0)[:, None, None].repeat(2, axis=2),
                              axis=1)  # fmt: skip
    previous[:, 0] = last[:, 0]
    repeated = hit & (np.linalg.norm(flat - previous, axis=2) < SAME) & (count[:, None] > 1)
    repeated[:, 0] &= False
    keep = hit & ~repeated
    order = np.argsort(~keep, axis=1, kind="stable")
    flat = np.take_along_axis(flat, order[..., None], axis=1)
    count = keep.sum(axis=1)
    return flat, np.where(count >= 3, count, 0)


def _next(count: np.ndarray, width: int) -> np.ndarray:
    """Each corner's next, round each polygon, as indices into `width` slots."""
    index = np.arange(width)[None]
    return np.where(index + 1 < count[:, None], index + 1, 0)


def area(poly: np.ndarray, count: np.ndarray) -> np.ndarray:
    """Each polygon's area, signed, anticlockwise positive."""
    if poly.shape[1] == 0:
        return np.zeros(len(poly))
    following = np.take_along_axis(poly, _next(count, poly.shape[1])[..., None].repeat(2, 2), 1)
    valid = np.arange(poly.shape[1])[None] < count[:, None]
    cross = poly[..., 0] * following[..., 1] - following[..., 0] * poly[..., 1]
    return 0.5 * np.where(valid, cross, 0.0).sum(axis=1)


def clip(subject: np.ndarray, subject_count: np.ndarray, by: np.ndarray,
         by_count: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each convex `subject` polygon clipped by its convex `by` polygon, both anticlockwise
    (Sutherland and Hodgman's): their common polygon, padded, and its corners' count."""
    poly, count = subject.copy(), subject_count.copy()
    for edge in range(by.shape[1]):
        live = edge < by_count
        if not live.any():
            break
        start = by[:, edge]
        end = np.take_along_axis(by, _next(by_count, by.shape[1])[:, edge][:, None, None]
                                 .repeat(2, 2), 1)[:, 0]  # fmt: skip
        along = end - start
        width = poly.shape[1]
        following = np.take_along_axis(poly, _next(count, width)[..., None].repeat(2, 2), 1)
        side = (along[:, None, 0] * (poly[..., 1] - start[:, None, 1])
                - along[:, None, 1] * (poly[..., 0] - start[:, None, 0]))  # fmt: skip
        side_next = np.take_along_axis(side, _next(count, width), 1)
        valid = np.arange(width)[None] < count[:, None]
        inside, inside_next = side >= 0, side_next >= 0
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(side != side_next, side / (side - side_next), 0.0)
        cut = poly + t[..., None] * (following - poly)
        out = np.zeros((len(poly), 2 * width, 2))
        keep = np.zeros((len(poly), 2 * width), dtype=bool)
        out[:, 0::2], keep[:, 0::2] = poly, valid & inside
        out[:, 1::2], keep[:, 1::2] = cut, valid & (inside != inside_next)
        order = np.argsort(~keep, axis=1, kind="stable")
        new = np.take_along_axis(out, order[..., None].repeat(2, 2), 1)
        new_count = keep.sum(axis=1)
        poly = np.where(live[:, None, None], new, np.pad(poly, ((0, 0), (0, width), (0, 0))))
        count = np.where(live, new_count, count)
        trim = max(int(count.max(initial=0)), 1)
        poly = poly[:, :trim]
    return poly, np.where((count >= 3) & (by_count >= 3), count, 0)


def oriented(rings: list[np.ndarray]) -> list[np.ndarray]:
    """A layer's rings turned so what they hold by the even-odd rule is on their left: each
    one anticlockwise where an even number of the others hold it, clockwise where odd."""
    from .classify import inside

    turned = []
    for k, ring in enumerate(rings):
        others = [r for j, r in enumerate(rings) if j != k]
        depth = sum(bool(inside([r], ring[:1])[0]) for r in others)
        signed = area(ring[None], np.array([len(ring)]))[0]
        want = 1.0 if depth % 2 == 0 else -1.0
        turned.append(ring if signed * want >= 0 else ring[::-1].copy())
    return turned


def rings_area(rings: list[np.ndarray]) -> float:
    """What a layer's rings hold, by the even-odd rule, in mm²."""
    return float(sum(area(r[None], np.array([len(r)]))[0] for r in oriented(rings)))


def inside_rings(poly: np.ndarray, count: np.ndarray, rings: list[np.ndarray],
                 chunk: int = 512) -> np.ndarray:
    """Each convex anticlockwise polygon's area inside `rings`, by the even-odd rule (Green's
    theorem): its own edges' parts inside them, and the rings' edges' parts inside it, the
    rings turned so what they hold is on their left (`oriented`). Only the rings' edges whose
    boxes meet a polygon's can cross it: one that meets none is wholly in or out, by its first
    corner."""
    from .classify import inside

    found = np.zeros(len(poly))
    if not rings or not len(poly):
        return found
    rings = [r + NUDGE for r in rings]
    turned = oriented(rings)
    a_all = np.concatenate(turned)
    b_all = np.concatenate([np.roll(r, -1, axis=0) for r in turned])
    seg_low, seg_high = np.minimum(a_all, b_all), np.maximum(a_all, b_all)
    # A polygon of no area, its corners on one point or one line, has none inside anything.
    count = np.where(np.abs(area(poly, count)) > 1e-12, count, 0)
    width = poly.shape[1]
    valid_all = np.arange(width)[None] < count[:, None]
    big = np.where(valid_all[..., None], poly, np.inf).min(axis=1)
    small = np.where(valid_all[..., None], poly, -np.inf).max(axis=1)
    for start in range(0, len(poly), chunk):
        rows = slice(start, start + chunk)
        lo, hi = big[rows], small[rows]
        meets = ((seg_low[None, :, 0] <= hi[:, None, 0]) & (seg_high[None, :, 0] >= lo[:, None, 0])
                 & (seg_low[None, :, 1] <= hi[:, None, 1])
                 & (seg_high[None, :, 1] >= lo[:, None, 1]))  # fmt: skip
        many = meets.sum(axis=1)
        p, n = poly[rows], count[rows]
        # Wholly in or wholly out: by its first corner.
        alone = (many == 0) & (n >= 3)
        if alone.any():
            whole = area(p[alone], n[alone])
            held = inside(rings, p[alone, 0])
            found[start + np.flatnonzero(alone)] = np.where(held, whole, 0.0)
        crossed = np.flatnonzero((many > 0) & (n >= 3))
        if not len(crossed):
            continue
        k = int(many[crossed].max())
        order = np.argsort(~meets[crossed], axis=1, kind="stable")[:, :k]
        live_seg = np.take_along_axis(meets[crossed], order, axis=1)
        a, b = a_all[order], b_all[order]  # [c, k, 2]
        p, n = p[crossed], n[crossed]
        following = np.take_along_axis(p, _next(n, width)[..., None].repeat(2, 2), 1)
        valid = np.arange(width)[None] < n[:, None]
        edge = following - p
        # The rings' edges inside the polygon: each clipped to it by its edges' half planes.
        normal = np.stack([edge[..., 1], -edge[..., 0]], axis=-1)
        near = np.einsum("cvq,ckvq->ckv", normal, a[:, :, None, :] - p[:, None, :, :])
        rate = np.einsum("cvq,ckq->ckv", normal, b - a)
        with np.errstate(divide="ignore", invalid="ignore"):
            bound = -near / rate
        lower = np.where(valid[:, None] & (rate < 0), bound, -np.inf).max(axis=2)
        upper = np.where(valid[:, None] & (rate > 0), bound, np.inf).min(axis=2)
        outside = (valid[:, None] & (rate == 0) & (near > 0)).any(axis=2)
        low, high = np.clip(lower, 0, 1), np.clip(upper, 0, 1)
        live = (high > low) & ~outside & live_seg
        s, e = a + low[..., None] * (b - a), a + high[..., None] * (b - a)
        total = 0.5 * np.where(live, s[..., 0] * e[..., 1] - e[..., 0] * s[..., 1], 0.0).sum(1)
        # Its own edges inside the rings: each cut where a ring's edge crosses it, inside and
        # out by turns from where it starts.
        d = b - a  # [c, k, 2]
        ex, ey = edge[:, :, None, 0], edge[:, :, None, 1]
        dx, dy = d[:, None, :, 0], d[:, None, :, 1]
        denominator = ex * dy - ey * dx
        off = a[:, None] - p[:, :, None]  # [c, v, k, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = (off[..., 0] * dy - off[..., 1] * dx) / denominator
            w = (off[..., 0] * ey - off[..., 1] * ex) / denominator
        crosses = ((denominator != 0) & (u > 0) & (u < 1) & (w >= 0) & (w < 1)
                   & live_seg[:, None, :])  # fmt: skip
        cuts = np.sort(np.where(crosses, u, 1.0), axis=2)
        breaks = np.concatenate([np.zeros(cuts.shape[:2] + (1,)), cuts], axis=2)
        ends = np.concatenate([cuts, np.ones(cuts.shape[:2] + (1,))], axis=2)
        starts_in = inside(rings, p.reshape(-1, 2)).reshape(p.shape[:2])
        pieces = np.arange(breaks.shape[2])[None, None]
        held = starts_in[..., None] ^ (pieces % 2 == 1)
        s2 = p[:, :, None] + breaks[..., None] * edge[:, :, None]
        e2 = p[:, :, None] + ends[..., None] * edge[:, :, None]
        cross = s2[..., 0] * e2[..., 1] - e2[..., 0] * s2[..., 1]
        total += 0.5 * np.where(held & valid[..., None], cross, 0.0).sum(axis=(1, 2))
        found[start + crossed] = total
    return found

def overlapping(low: np.ndarray, high: np.ndarray, other_low: np.ndarray,
                other_high: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each pair of a box of the first and one of the second that overlap, as their indices:
    the second's boxes in voxels about their own size, each of the first's tried against
    those in the voxels it reaches."""
    if not len(low) or not len(other_low):
        return np.zeros(0, dtype=int), np.zeros(0, dtype=int)
    size = max(float(np.median((other_high - other_low).max(axis=1))), 1e-6)
    origin = np.minimum(low.min(axis=0), other_low.min(axis=0))
    voxels: dict[tuple[int, int, int], list[int]] = {}
    first = np.floor((other_low - origin) / size).astype(int)
    last = np.floor((other_high - origin) / size).astype(int)
    for k in range(len(other_low)):
        for x in range(first[k, 0], last[k, 0] + 1):
            for y in range(first[k, 1], last[k, 1] + 1):
                for z in range(first[k, 2], last[k, 2] + 1):
                    voxels.setdefault((x, y, z), []).append(k)
    mine, theirs = [], []
    start = np.floor((low - origin) / size).astype(int)
    end = np.floor((high - origin) / size).astype(int)
    for k in range(len(low)):
        found: set[int] = set()
        for x in range(start[k, 0], end[k, 0] + 1):
            for y in range(start[k, 1], end[k, 1] + 1):
                for z in range(start[k, 2], end[k, 2] + 1):
                    found.update(voxels.get((x, y, z), ()))
        for j in found:
            if np.all(low[k] <= other_high[j]) and np.all(other_low[j] <= high[k]):
                mine.append(k)
                theirs.append(j)
    return np.array(mine, dtype=int), np.array(theirs, dtype=int)
