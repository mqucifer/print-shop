"""The overhang reading as it was before mqucifer/print-shop#235, to hold today's to it.

Copied from `setup/features.py` at 9406d08, with mqucifer/print-shop#239's allowance at the
limit, and not to be improved: it cuts a height at a time and reads every point against every
line. `test_overhang_unchanged.py` compares the two to the last bit, on whatever platform runs
it, which figures written down couldn't.
"""

from __future__ import annotations

import numpy as np
import trimesh

from print_shop.setup.features import (
    _AT_LIMIT_DEG,
    _ENVELOPE_MM,
    _FIRST_LAYER_MM,
    _FLAT,
    _OVERHANG_DEG,
    _SAMPLE_MM,
    _from_the_plate,
)

__all__ = ["overhang", "overhang_to_support", "overhanging"]


def overhang_to_support(
    mesh: trimesh.Trimesh, beneath: trimesh.Trimesh | None = None
) -> tuple[float, float]:
    needs = overhang(mesh, beneath=beneath)[0]
    return needs, max(overhang(mesh, flares=False, beneath=beneath)[0] - needs, 0.0)


def overhang(
    mesh: trimesh.Trimesh, flares: bool = True, beneath: trimesh.Trimesh | None = None
) -> tuple[float, float, float]:
    found = overhanging(mesh, flares, beneath)
    if found is None:
        return 0.0, 0.0, 0.0
    _, areas, read, flat = found
    return float(areas.sum()), float(read.max()), float(areas[flat].sum())


def overhanging(
    mesh: trimesh.Trimesh, flares: bool = True, beneath: trimesh.Trimesh | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Each point of a seated mesh that overhangs, as `_overhang` reads it: where it is, in
    the mesh's own coordinates, the area it stands for, its slope in degrees past vertical, and
    whether it's on a flat face deeper than the slope is read over. None where nothing does."""
    lo = mesh.bounds[0]
    normals = mesh.face_normals
    top = mesh.triangles[:, :, 2].max(axis=1) - lo[2]
    # Facing down past the threshold, not wholly in the first layer, on the plate, and, unless
    # `flares` is False, not a round or chamfer rising from the plate (mqucifer/print-shop#128).
    rising = _from_the_plate(mesh) if flares else np.zeros(len(mesh.faces), dtype=bool)
    # An angle from vertical is the arcsine of how far the normal points down: a cosine of the
    # threshold is the same only at 45 degrees exactly.
    past = _OVERHANG_DEG + _AT_LIMIT_DEG
    candidates = np.flatnonzero(
        (normals[:, 2] < -np.sin(np.radians(past))) & (top > _FIRST_LAYER_MM) & ~rising
    )
    if not len(candidates):
        return None
    triangles, index = _bisect(mesh.triangles[candidates] - lo, _SAMPLE_MM)
    points = triangles.mean(axis=1)
    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
    ) / 2
    down = -normals[candidates[index], 2]
    keep = points[:, 2] > _FIRST_LAYER_MM
    points, areas, down = points[keep], areas[keep], down[keep]
    if not len(points):
        return None
    own = np.degrees(np.arcsin(np.clip(down, -1, 1)))

    seated = mesh.copy()
    if beneath is not None:
        # What a point has under it is the part and whatever else stands there by then.
        seated = trimesh.Trimesh(
            np.vstack([mesh.vertices, beneath.vertices]),
            np.vstack([mesh.faces, beneath.faces + len(mesh.vertices)]),
            process=False,
        )
    seated.apply_translation(-lo)
    # Below the first layer is the plate, which holds the first layer: a point lower than
    # that over the first layer is read against the first layer's outline, at its middle,
    # where Orca slices it.
    probe = np.maximum(points[:, 2] - _ENVELOPE_MM, _FIRST_LAYER_MM / 2)
    reach = _reach(seated, points, probe)
    depth = _reach(seated, points, points[:, 2] - 0.01)  # its own face's depth
    slope = np.degrees(np.arctan2(reach, _ENVELOPE_MM))
    over = slope > past
    if not over.any():
        return None
    read = np.where(depth > _ENVELOPE_MM, own, np.minimum(own, slope))
    flat = (down > _FLAT) & (depth > _ENVELOPE_MM)
    return points[over] + lo, areas[over], read[over], flat[over]


def _bisect(triangles: np.ndarray, longest: float) -> tuple[np.ndarray, np.ndarray]:
    """The triangles halved across their longest edge until none is longer than `longest`.

    With the index of the triangle each piece came from. A long thin step,
    150 x 0.1 mm, becomes a few hundred pieces, not the 4^n of quartering.
    """
    index = np.arange(len(triangles))
    for _ in range(64):
        edges = np.linalg.norm(triangles - np.roll(triangles, -1, axis=1), axis=2)
        split = edges.max(axis=1) > longest
        if not split.any():
            break
        tri, at = triangles[split], index[split]
        k = edges[split].argmax(axis=1)  # the edge from corner k to corner k+1
        rows = np.arange(len(tri))
        a, b, c = tri[rows, k], tri[rows, (k + 1) % 3], tri[rows, (k + 2) % 3]
        mid = (a + b) / 2
        halves = np.concatenate([np.stack([a, mid, c], 1), np.stack([mid, b, c], 1)])
        triangles = np.concatenate([triangles[~split], halves])
        index = np.concatenate([index[~split], at, at])
    return triangles, index


def _reach(mesh: trimesh.Trimesh, points: np.ndarray, heights: np.ndarray) -> np.ndarray:
    """How far each point is, sideways, from the part's outline at its height below.

    0 when the part is right under it, and exact up to `_REACH_MM`; further
    than that reads as infinite. Heights are read to 0.01 mm, a hair above, so
    an outline isn't cut along a flat face.
    """
    z = mesh.triangles[:, :, 2]
    z_lo, z_hi = z.min(axis=1), z.max(axis=1)
    outward = mesh.face_normals[:, :2]
    levels = np.round(heights, 2) + 1e-3
    out = np.full(len(points), np.inf)
    for level in np.unique(levels):
        at = np.flatnonzero(levels == level)
        spanning = np.flatnonzero((z_lo <= level) & (z_hi >= level))
        if not len(spanning):
            continue
        segments, faces = trimesh.intersections.mesh_plane(  # type: ignore[no-untyped-call]
            mesh, (0, 0, 1), (0, 0, level), return_faces=True, local_faces=spanning
        )
        if len(segments):
            out[at] = _outside(points[at, :2], segments[:, :, :2], outward[faces])
    return out


#: How far `_reach` measures exactly. Past it a point is only inside or outside:
#: read over `_ENVELOPE_MM`, that's a slope past 84 degrees.
_REACH_MM = 10 * _ENVELOPE_MM


def _outside(points: np.ndarray, segments: np.ndarray, outward: np.ndarray) -> np.ndarray:
    """Each 2D point's distance outside the outline the segments draw: 0 inside it.

    Measured cell by cell against the segments near the cell, and inside or
    outside by the nearest segment's outward side. A point with none near,
    or none whose side is clear, is decided by a ray's crossings instead.
    """
    out = np.full(len(points), np.inf)
    undecided = np.zeros(len(points), dtype=bool)
    seg_lo, seg_hi = segments.min(axis=1), segments.max(axis=1)
    cells, which = np.unique(np.floor(points / _REACH_MM), axis=0, return_inverse=True)
    for c, cell in enumerate(cells):
        mine = np.flatnonzero(which.ravel() == c)
        lo, hi = (cell - 1) * _REACH_MM, (cell + 2) * _REACH_MM
        near = np.flatnonzero(((seg_lo <= hi) & (seg_hi >= lo)).all(axis=1))
        if not len(near):
            undecided[mine] = True
            continue
        p = points[mine][:, None, :]
        a, b = segments[near, 0][None], segments[near, 1][None]
        ab = b - a
        t = np.clip(((p - a) * ab).sum(-1) / np.maximum((ab * ab).sum(-1), 1e-12), 0, 1)
        gap = p - (a + t[..., None] * ab)
        dist = np.linalg.norm(gap, axis=-1)
        k = dist.argmin(axis=1)
        rows = np.arange(len(mine))
        nearest = dist[rows, k]
        side = (gap[rows, k] * outward[near[k]]).sum(-1)
        clear = (nearest <= _REACH_MM) & (np.abs(side) > 1e-9)
        out[mine] = np.where(side > 0, nearest, 0.0)
        undecided[mine] = ~clear & (nearest > 1e-9)
        out[mine[nearest <= 1e-9]] = 0.0
    if undecided.any():
        inside = _inside(points[undecided], segments)
        out[np.flatnonzero(undecided)] = np.where(inside, 0.0, np.inf)
    return out


def _inside(points: np.ndarray, segments: np.ndarray) -> np.ndarray:
    """Whether each 2D point is inside the outline, by the crossings of a ray along +x."""
    inside = np.zeros(len(points), dtype=bool)
    for chunk in np.array_split(np.arange(len(points)), max(1, len(points) // 512)):
        p = points[chunk][:, None, :]
        a, b = segments[None, :, 0, :], segments[None, :, 1, :]
        ab = b - a
        crosses = (a[..., 1] > p[..., 1]) != (b[..., 1] > p[..., 1])
        x_at = a[..., 0] + (p[..., 1] - a[..., 1]) * ab[..., 0] / np.where(
            ab[..., 1] == 0, 1, ab[..., 1]
        )
        inside[chunk] = ((crosses & (p[..., 0] < x_at)).sum(axis=1) % 2) == 1
    return inside
