"""Regions round a part's joints, from its design run (mqucifer/print-shop#338).

So a study can ask for "denser at the joints" without anyone drawing boxes. A part's joints are
what its design run says of them, and each gives a region of the settings asked, by a stated
margin, in the part's own file's axes (`bench.printed`):

- **its fits**: the faces it meets another part at, each a box in the part's context
  (`setup.context`, mqucifer/print-shop#130), grown by the margin;
- **its bolt holes**: each bolt of a `bolted` or `through` joint on it in the run's assembly file
  (`bench.assembly`), as a cylinder round the hole, the margin wider than the hole, through the
  part along the hole's axis. The hole is found in the part's own mesh: of its three axes, the
  one a section through the bolt's point shows a hole round it, closed by the part on every
  side. Its radius is the nearest the section's edge comes to the point;
- **its latch**: a dovetail's latch box on this part, grown by the margin.

A joint whose place can't be read is said, never guessed: a fit with no box, a bolt with no hole
round it in this part's mesh.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import trimesh

from print_shop.bench.assembly import Assembly, PartAt
from print_shop.bench.printed import Box, Cylinder, Region, Settings
from print_shop.setup.context import PartContext

__all__ = ["Joints", "hole_at", "round_joints"]

#: A hole is closed by the part where a ray from its point meets the part's edge within this
#: many of its radius, every way across it.
CLOSED = 4.0


@dataclass
class Joints:
    """Regions round a part's joints, and what couldn't be placed."""

    regions: list[Region]
    notes: list[str]


def _grown(box: Box, margin: float) -> Box:
    low, high = box
    return ((low[0] - margin, low[1] - margin, low[2] - margin),
            (high[0] + margin, high[1] + margin, high[2] + margin))


def _section(mesh: trimesh.Trimesh, axis: int, at: float) -> np.ndarray:
    """The part's section square to own axis `axis` at `at`, as 2D segments (u0, v0, u1, v1) in
    the other two axes, cyclic from it."""
    normal = [0.0, 0.0, 0.0]
    normal[axis] = 1.0
    origin = [0.0, 0.0, 0.0]
    origin[axis] = at
    lines = trimesh.intersections.mesh_plane(  # type: ignore[no-untyped-call]
        mesh, normal, origin)
    u, v = (axis + 1) % 3, (axis + 2) % 3
    return np.column_stack([lines[:, 0, u], lines[:, 0, v], lines[:, 1, u], lines[:, 1, v]]) \
        if len(lines) else np.zeros((0, 4))


def _ray(segments: np.ndarray, p: np.ndarray, way: np.ndarray) -> np.ndarray:
    """How far along `way` from `p` the ray meets each segment it meets."""
    a, e = segments[:, :2], segments[:, 2:] - segments[:, :2]
    den = way[0] * e[:, 1] - way[1] * e[:, 0]
    ok = np.abs(den) > 1e-12
    q = a - p
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ok, (q[:, 0] * e[:, 1] - q[:, 1] * e[:, 0]) / den, -1.0)
        s = np.where(ok, (q[:, 0] * way[1] - q[:, 1] * way[0]) / den, -1.0)
    return np.asarray(t[(t > 1e-9) & (s >= 0) & (s <= 1)])


def hole_at(mesh: trimesh.Trimesh, point: Any) -> tuple[int, float] | None:
    """The hole through `point` in the part's own mesh: the own axis it runs along and its
    radius. None where no section through the point shows it in a hole closed by the part."""
    p3 = np.asarray(point, dtype=float)
    found: list[tuple[float, int]] = []
    for axis in range(3):
        # Just off the point, so the plane doesn't run along the mesh's own edges.
        segments = _section(mesh, axis, float(p3[axis]) + 1e-3)
        if len(segments) == 0:
            continue
        p = np.array([p3[(axis + 1) % 3], p3[(axis + 2) % 3]])
        if len(_ray(segments, p, np.array([1.0, 0.000731]))) % 2 == 1:
            continue  # inside the part, not in a hole
        a, e = segments[:, :2], segments[:, 2:] - segments[:, :2]
        length2 = np.maximum((e * e).sum(axis=1), 1e-24)
        t = np.clip(((p - a) * e).sum(axis=1) / length2, 0.0, 1.0)
        radius = float(np.min(np.hypot(*(a + t[:, None] * e - p).T)))
        if radius <= 0:
            continue
        closed = all(
            (hits := _ray(segments, p, np.array(way))).size and float(hits.min()) <= CLOSED
            * radius
            for way in ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)))
        if closed:
            found.append((radius, axis))
    if not found:
        return None
    radius, axis = min(found)
    return axis, radius


def _own(part: PartAt, point: Any) -> np.ndarray:
    """A point of the assembly in the part's own file's axes."""
    return np.asarray(part.matrix().T @ (np.asarray(point, dtype=float) - np.asarray(part.at)))


def round_joints(
    settings: Settings, margin: float, mesh: trimesh.Trimesh, context: PartContext | None = None,
    assembly: Assembly | None = None, part: str | None = None,
) -> Joints:
    """Regions of `settings` round each of the part's joints, `margin` mm round each, in the
    part's own axes: its fits from its context, and its bolt holes and latch from the run's
    assembly file, where `part` names it there."""
    if margin < 0:
        raise ValueError("a margin is 0 or more, in mm")
    if not settings.given():
        raise ValueError("a region round the joints sets something of its own")
    regions: list[Region] = []
    notes: list[str] = []
    for fit in (context.fits if context is not None else ()):
        name = f"fit-{fit.name}"
        if fit.box is None:
            notes.append(f"{name}: its place wasn't derived ({fit.derived or 'no box'}), so no "
                         "region is put round it")
            continue
        regions.append(Region(name, _grown(fit.box, margin), settings,
                              f"round {fit.line()}, {margin:g} mm margin"))
    if assembly is not None and part is not None:
        at = assembly.part(part)
        low, high = mesh.bounds
        for joint in assembly.joints:
            if part not in joint.between:
                continue
            if joint.kind in ("bolted", "through"):
                for k, point in enumerate(joint.at, 1):
                    name = f"{joint.name}-bolt-{k}"
                    own = _own(at, point)
                    hole = hole_at(mesh, own)
                    if hole is None:
                        where = ", ".join(f"{v:g}" for v in own)
                        notes.append(f"{name}: no hole round its point ({where}) in {part}'s "
                                     "mesh, so no region is put round it")
                        continue
                    axis, radius = hole
                    start = own.copy()
                    start[axis] = low[axis] - margin
                    way = [0.0, 0.0, 0.0]
                    way[axis] = 1.0
                    shape = Cylinder((float(start[0]), float(start[1]), float(start[2])),
                                     (way[0], way[1], way[2]), radius + margin,
                                     float(high[axis] - low[axis] + 2 * margin))
                    regions.append(Region(name, shape, settings,
                                          f"round the {2 * radius:.3g} mm hole along "
                                          f"{'xyz'[axis]}, {margin:g} mm margin"))
            latch = joint.latch
            if latch is not None and latch.on == part:
                # With `own`, its box is in the tab's part's own axes already; else in the
                # assembly's, turned back into the part's.
                own_corners = (np.array(latch.arm, dtype=float) if latch.own
                               else np.array([_own(at, c) for c in latch.corners()]))
                lo, hi = own_corners.min(axis=0), own_corners.max(axis=0)
                box: Box = ((float(lo[0]), float(lo[1]), float(lo[2])),
                            (float(hi[0]), float(hi[1]), float(hi[2])))
                regions.append(Region(f"{joint.name}-latch", _grown(box, margin), settings,
                                      f"round the latch of {joint.name}, {margin:g} mm margin"))
    if not regions and not notes:
        notes.append("the context and the assembly name no joint on this part")
    return Joints(regions, notes)
