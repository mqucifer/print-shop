"""The part's features as it will sit on the plate: what a setting's reason points at.

The Sponsor, 2026-10-25, on mqucifer/print-shop#7: every setting depends on
the part's design and purpose, so each proposed change names the feature or
purpose behind it. These are the features, measured from the mesh as it will
be sliced (rotated and seated), each with an id the reasoning cites.

All are deterministic geometry. None is a judgment; judging what they mean
for a setting is the proposal's job.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh
from pydantic import BaseModel

__all__ = ["Feature", "measure"]

#: A face this far from vertical, facing down, needs support (Orca's own
#: threshold reasoning; the analyzer uses the same 45 degrees).
_OVERHANG_DEG = 45.0
#: Faces within this of horizontal are flat: a bridge below, a top surface above.
_FLAT = 0.98
#: The first layer: a face this low prints on the plate, not over the air. Orca
#: slices the first layer at its middle and lays it on the plate, so a 1 mm
#: round drawn in 0.1 mm steps at the plate is the plate's (mqucifer/print-shop#107).
_FIRST_LAYER_MM = 0.2
#: How far down a downward face's slope is read: two 0.2 mm layers. A slope
#: drawn in 0.1 mm steps is a stack of flat 0.1 mm "ceilings" and vertical
#: risers; read over this height, it's the slope the steps make, as the
#: printer lays it, layer on layer (mqucifer/print-shop#107).
_ENVELOPE_MM = 0.4
#: Each downward face is read at points this far apart at most.
_SAMPLE_MM = 0.5


class Feature(BaseModel):
    """One measured fact about the part, with the id a reason cites."""

    model_config = {"frozen": True}

    id: str
    value: float
    unit: str
    says: str  # what it is, in words a printer person would use

    def line(self) -> str:
        return f"{self.id}: {self.value:g} {self.unit}, {self.says}"


def measure(stl_path: str | Path, bed_contact_area_mm2: float) -> tuple[Feature, ...]:
    """The features of the mesh at `stl_path`, which is already as it will print."""
    mesh = trimesh.load_mesh(str(stl_path), force="mesh")
    lo, hi = mesh.bounds
    x, y, height = (hi - lo).tolist()
    normals = mesh.face_normals
    areas = mesh.area_faces

    overhang, steepest, bridge = _overhang(mesh)
    flat_top = float(areas[normals[:, 2] > _FLAT].sum())
    volume = float(mesh.volume) if mesh.is_watertight else 0.0
    thickness = 2 * volume / float(mesh.area) if mesh.area and volume else 0.0
    narrow = min(x, y)
    length, width = _footprint(mesh)

    return (
        Feature(id="height", value=round(height, 1), unit="mm", says="height as it prints"),
        Feature(id="footprint", value=round(x, 1), unit="mm",
                says=f"x by {round(y, 1)} mm in y, the outline on the plate"),
        Feature(id="aspect", value=round(max(x, y) / narrow, 1) if narrow else 0.0, unit="x",
                says="its longest side on the plate over its shortest; long thin parts lift at "
                     "the ends"),
        Feature(id="bed_contact", value=round(bed_contact_area_mm2), unit="mm2",
                says="area touching the plate, which holds it down"),
        Feature(id="length", value=round(length, 1), unit="mm",
                says=f"the footprint's long side, {round(width, 1)} mm across; a long part's "
                     "ends lift as its layers cool and shrink"),
        Feature(id="footprint_aspect", value=round(length / width, 1) if width else 0.0,
                unit="x", says="the footprint's length over its width; a long, narrow part "
                               "lifts at its ends however much of it touches the plate"),
        Feature(id="slenderness", value=round(height / narrow, 1) if narrow else 0.0, unit="x",
                says="height over its narrowest width; tall and narrow parts wobble and lift"),
        Feature(id="overhang", value=round(overhang), unit="mm2",
                says=f"faces past {_OVERHANG_DEG:g} degrees from vertical, off the plate"),
        Feature(id="steepest_overhang", value=round(steepest, 1), unit="deg",
                says="the worst overhang, degrees past vertical (90 is a ceiling)"),
        Feature(id="bridge", value=round(bridge), unit="mm2",
                says="flat downward faces off the plate, printed as bridges or on support"),
        Feature(id="flat_top", value=round(flat_top), unit="mm2",
                says="upward flat faces, whose finish the top layers decide"),
        Feature(id="section", value=round(thickness, 2), unit="mm",
                says="typical wall or section thickness (twice volume over surface)"),
        Feature(id="volume", value=round(volume), unit="mm3", says="solid volume"),
    )


def _footprint(mesh: trimesh.Trimesh) -> tuple[float, float]:
    """The footprint's length and width: the sides of the smallest box around it on the plate.

    Turned in 1 degree steps, so a long part lying across the plate's diagonal
    is as long as one lying along an axis.
    """
    xy = np.unique(mesh.vertices[:, :2], axis=0)
    turns = np.radians(np.arange(0, 90, 1.0))
    axes = np.stack([np.cos(turns), np.sin(turns)], axis=1)
    along = xy @ axes.T
    across = xy @ np.stack([-axes[:, 1], axes[:, 0]], axis=1).T
    sides = np.stack([np.ptp(along, axis=0), np.ptp(across, axis=0)], axis=1)
    best = sides[np.argmin(sides[:, 0] * sides[:, 1])]
    return float(best.max()), float(best.min())


def _overhang(mesh: trimesh.Trimesh) -> tuple[float, float, float]:
    """The overhang's area, its steepest point, and the flat part of it, a bridge.

    In mm2, degrees past vertical, and mm2.

    Read as the printer lays it: a point on a downward face overhangs when the
    part's outline `_ENVELOPE_MM` lower is further than that away, sideways,
    so the slope from there to here is past `_OVERHANG_DEG`. A slope drawn in
    0.1 mm steps reads as the slope its steps make, not as the 90 degree
    ceiling each step's underside is, while a real ceiling's underside, which
    nothing reaches from below, still reads as 90 degrees.

    A point is read at its face's own angle, unless its face is no deeper than
    the slope is read over (a step), when it's read at the steps' slope. The
    bridge is the overhang on flat faces deeper than that: a step is no bridge.
    """
    lo = mesh.bounds[0]
    normals = mesh.face_normals
    top = mesh.triangles[:, :, 2].max(axis=1) - lo[2]
    # Facing down past the threshold, and not wholly in the first layer, on the plate.
    candidates = np.flatnonzero(
        (normals[:, 2] < -np.cos(np.radians(_OVERHANG_DEG))) & (top > _FIRST_LAYER_MM)
    )
    if not len(candidates):
        return 0.0, 0.0, 0.0
    triangles, index = _bisect(mesh.triangles[candidates] - lo, _SAMPLE_MM)
    points = triangles.mean(axis=1)
    areas = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
    ) / 2
    down = -normals[candidates[index], 2]
    keep = points[:, 2] > _FIRST_LAYER_MM
    points, areas, down = points[keep], areas[keep], down[keep]
    if not len(points):
        return 0.0, 0.0, 0.0
    own = np.degrees(np.arcsin(np.clip(down, -1, 1)))

    seated = mesh.copy()
    seated.apply_translation(-lo)
    # Below the first layer is the plate, which holds the first layer: a point lower than
    # that over the first layer is read against the first layer's outline, at its middle,
    # where Orca slices it.
    probe = np.maximum(points[:, 2] - _ENVELOPE_MM, _FIRST_LAYER_MM / 2)
    reach = _reach(seated, points, probe)
    depth = _reach(seated, points, points[:, 2] - 0.01)  # its own face's depth
    slope = np.degrees(np.arctan2(reach, _ENVELOPE_MM))
    over = slope > _OVERHANG_DEG
    if not over.any():
        return 0.0, 0.0, 0.0
    read = np.where(depth > _ENVELOPE_MM, own, np.minimum(own, slope))
    flat = over & (down > _FLAT) & (depth > _ENVELOPE_MM)
    return float(areas[over].sum()), float(read[over].max()), float(areas[flat].sum())


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
