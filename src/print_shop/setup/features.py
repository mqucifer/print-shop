"""The part's features as it will sit on the plate: what a setting's reason points at.

The Sponsor, 2026-10-25, on mqucifer/print-shop#7: every setting depends on
the part's design and purpose, so each proposed change names the feature or
purpose behind it. These are the features, measured from the mesh as it will
be sliced (rotated and seated), each with an id the reasoning cites.

All are deterministic geometry. None is a judgment; judging what they mean
for a setting is the proposal's job.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from pydantic import BaseModel

from print_shop.setup.outline import Outlines

__all__ = ["FLARE_MM", "Feature", "Overhang", "measure", "overhang_places", "overhang_to_support"]

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
#: The most points read at once: a wide ceiling has millions (mqucifer/print-shop#235).
_POINTS_AT_ONCE = 1 << 16
#: The most times a face is halved. A piece's place in the halving fits 64 bits up to this, and
#: 0.5 mm is reached well before it for anything that fits a printer.
_HALVINGS = 63
#: A round or chamfer rising from the plate is carried by the layers under it, each a little
#: wider than the last, from the plate up: it needs no support (mqucifer/print-shop#128). Orca
#: sliced the Sponsor's lid lying on its rounded top edge with none, where this measure read
#: 2580 mm2 of overhang and the orientation model stood the lid on its edge for it. It's the
#: downward faces reached from the plate through downward faces, up to this high: a round's
#: part steeper than 45 degrees rises 0.29 of its radius, so this holds rounds to about 17 mm.
#: Above it, a face that leans out from the plate still counts.
FLARE_MM = 5.0


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


def overhang_to_support(
    mesh: trimesh.Trimesh, beneath: trimesh.Trimesh | None = None
) -> tuple[float, float]:
    """What of a seated mesh, as it lies, would need support, and the round or chamfer rising
    from the plate that's left out of it (`FLARE_MM`), in mm2: the orientation step's overhang
    for each pose it weighs (mqucifer/print-shop#128).

    `beneath` is what else stands under the mesh when its faces are printed, in the mesh's own
    coordinates, such as the panes put in a frame at its pause: a face with it right under
    is carried, as one with the part's own material under it is (mqucifer/print-shop#153).
    None of `beneath`'s own faces is read."""
    rising = _from_the_plate(mesh)
    # Read once, with the round in, and it's then left out: whether a point overhangs doesn't
    # depend on which other faces are read (mqucifer/print-shop#235).
    found = [(p.key, p.areas, rising[p.face]) for p in _over(mesh, _downward(mesh), beneath)]
    if not found:
        return 0.0, 0.0
    areas, aside = _in_order([f[0] for f in found], [f[1] for f in found], [f[2] for f in found])
    needs = float(areas[~aside].sum())
    return needs, max(float(areas.sum()) - needs, 0.0)


class Overhang(BaseModel):
    """One stretch of a seated mesh that would need support: its area, the box it fills in the
    mesh's own coordinates, how much of it is flat, and its worst slope."""

    model_config = {"frozen": True}

    area_mm2: float
    low: tuple[float, float, float]
    high: tuple[float, float, float]
    flat_mm2: float
    steepest_deg: float


#: Overhanging points further apart than this, in height, are two stretches: a ledge and a
#: ceiling over it are said apart.
_APART_MM = 1.0


def overhang_places(
    mesh: trimesh.Trimesh, beneath: trimesh.Trimesh | None = None
) -> tuple[Overhang, ...]:
    """Where a seated mesh, as it lies, would need support: `overhang_to_support`'s own area,
    by the same reading, as each stretch of height it's in, lowest first. For a design run's
    stops, which show a person where a part that prints without supports has downward faces
    (mqucifer/print-shop#147)."""
    found = _overhanging(mesh, beneath=beneath)
    if found is None:
        return ()
    points, areas, read, flat = found
    order = np.argsort(points[:, 2])
    points, areas, read, flat = points[order], areas[order], read[order], flat[order]
    starts = np.flatnonzero(np.diff(points[:, 2]) > _APART_MM) + 1
    places = []
    for at in np.split(np.arange(len(points)), starts):
        low, high = points[at].min(axis=0), points[at].max(axis=0)
        places.append(
            Overhang(
                area_mm2=float(areas[at].sum()),
                low=(float(low[0]), float(low[1]), float(low[2])),
                high=(float(high[0]), float(high[1]), float(high[2])),
                flat_mm2=float(areas[at][flat[at]].sum()),
                steepest_deg=float(read[at].max()),
            )
        )
    return tuple(places)


def _from_the_plate(mesh: trimesh.Trimesh) -> np.ndarray:
    """Each face that's part of a round or chamfer rising from the plate: a downward face reached
    from the plate through downward faces, none of them higher than `FLARE_MM`. A flat one joins
    only in the first layer: a ceiling reached through a chamfer is still a ceiling."""
    z = mesh.triangles[:, :, 2] - mesh.bounds[0][2]
    nz = mesh.face_normals[:, 2]
    flat = nz < -_FLAT
    low = (nz < -1e-6) & (z.max(axis=1) <= FLARE_MM) & (~flat | (z.max(axis=1) <= _FIRST_LAYER_MM))
    found = np.zeros(len(mesh.faces), dtype=bool)
    if not low.any():
        return found
    pairs = mesh.face_adjacency
    pairs = pairs[low[pairs[:, 0]] & low[pairs[:, 1]]]
    # Joined face to face, by union-find: no graph library is a dependency here.
    root = np.arange(len(mesh.faces))

    def find(face: int) -> int:
        while root[face] != face:
            root[face] = root[root[face]]
            face = int(root[face])
        return face

    for a, b in pairs.tolist():
        ra, rb = find(a), find(b)
        if ra != rb:
            root[ra] = rb
    lows = np.flatnonzero(low)
    groups = np.array([find(int(f)) for f in lows])
    # Touching the plate: as `analyzer.PLATE_MM` reads it, a few microns off zero included.
    touching = set(groups[z[lows].min(axis=1) <= 0.01 + 1e-9].tolist())
    found[lows[np.isin(groups, list(touching))]] = True
    return found


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
    rising = _from_the_plate(mesh)
    keys, areas, flats, steepest = [], [], [], []
    for points in _over(mesh, _downward(mesh) & ~rising):
        read, flat = points.slope_and_flat()
        keys.append(points.key)
        areas.append(points.areas)
        flats.append(flat)
        steepest.append(float(read.max()))
    if not keys:
        return 0.0, 0.0, 0.0
    area, bridge = _in_order(keys, areas, flats)
    return float(area.sum()), max(steepest), float(area[bridge].sum())


def _overhanging(
    mesh: trimesh.Trimesh, beneath: trimesh.Trimesh | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """Each point of a seated mesh that overhangs, as `_overhang` reads it: where it is, in
    the mesh's own coordinates, the area it stands for, its slope in degrees past vertical, and
    whether it's on a flat face deeper than the slope is read over. None where nothing does."""
    rising = _from_the_plate(mesh)
    found = [(p.key, p.at, p.areas, *p.slope_and_flat())
             for p in _over(mesh, _downward(mesh) & ~rising, beneath)]
    if not found:
        return None
    at, areas, read, flat = _in_order(
        [f[0] for f in found], *([f[n] for f in found] for n in range(1, 5))
    )
    return at + mesh.bounds[0], areas, read, flat


def _downward(mesh: trimesh.Trimesh) -> np.ndarray:
    """Each face that could overhang: facing down past the threshold, and not wholly in the
    first layer, on the plate."""
    top = mesh.triangles[:, :, 2].max(axis=1) - mesh.bounds[0][2]
    down: np.ndarray = (mesh.face_normals[:, 2] < -np.cos(np.radians(_OVERHANG_DEG))) & (
        top > _FIRST_LAYER_MM
    )
    return down


@dataclass(frozen=True)
class _Points:
    """Some of the points that overhang, with the place of each in the halving (`key`)."""

    key: np.ndarray
    at: np.ndarray  # from the mesh's lowest corner
    areas: np.ndarray
    face: np.ndarray
    down: np.ndarray
    slope: np.ndarray
    outlines: Outlines

    def slope_and_flat(self) -> tuple[np.ndarray, np.ndarray]:
        """Each point's slope as it's read, and whether it's a bridge: both turn on its own
        face's depth, which only what overhangs is measured for."""
        own = np.degrees(np.arcsin(np.clip(self.down, -1, 1)))
        depth = self.outlines.reach(self.at, self.at[:, 2] - 0.01)  # its own face's depth
        read = np.where(depth > _ENVELOPE_MM, own, np.minimum(own, self.slope))
        return read, (self.down > _FLAT) & (depth > _ENVELOPE_MM)


def _over(
    mesh: trimesh.Trimesh, faces: np.ndarray, beneath: trimesh.Trimesh | None = None
) -> Iterator[_Points]:
    """The points on `faces` that overhang, some at a time, so that a wide ceiling's millions
    are never all held with their triangles (mqucifer/print-shop#235). `_in_order` puts what's
    kept of them back in the halving's order."""
    chosen = np.flatnonzero(faces)
    if not len(chosen):
        return
    lo = mesh.bounds[0]
    normals = mesh.face_normals
    seated = mesh.copy()
    if beneath is not None:
        # What a point has under it is the part and whatever else stands there by then.
        seated = trimesh.Trimesh(
            np.vstack([mesh.vertices, beneath.vertices]),
            np.vstack([mesh.faces, beneath.faces + len(mesh.vertices)]),
            process=False,
        )
    seated.apply_translation(-lo)
    outlines = Outlines(seated)
    for triangles, index, key in _bisect(np.array(mesh.triangles[chosen] - lo), _SAMPLE_MM):
        points = triangles.mean(axis=1)
        keep = points[:, 2] > _FIRST_LAYER_MM
        if not keep.any():
            continue
        areas = np.linalg.norm(
            np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1
        ) / 2
        face = chosen[index[keep]]
        points, areas, key = points[keep], areas[keep], key[keep]
        # Below the first layer is the plate, which holds the first layer: a point lower than
        # that over the first layer is read against the first layer's outline, at its middle,
        # where Orca slices it.
        probe = np.maximum(points[:, 2] - _ENVELOPE_MM, _FIRST_LAYER_MM / 2)
        slope = np.degrees(np.arctan2(outlines.reach(points, probe), _ENVELOPE_MM))
        over = slope > _OVERHANG_DEG
        if over.any():
            yield _Points(key=key[over], at=points[over], areas=areas[over], face=face[over],
                          down=-normals[face[over], 2], slope=slope[over], outlines=outlines)


def _bisect(
    triangles: np.ndarray, longest: float
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """The triangles halved across their longest edge until none is longer than `longest`,
    some pieces at a time.

    With the index of the triangle each piece came from, and its place in the halving: a
    number by which a stable sort (`_in_order`) puts the pieces in the order of halving them
    all at once, the unsplit first, then each round's first halves and its second. An area is
    summed in that order whatever the pieces at a time, so it's the same to the last bit. A
    long thin step, 150 x 0.1 mm, becomes a few hundred pieces, not the 4^n of quartering.
    """
    waiting = [(triangles, np.arange(len(triangles)), np.ones(len(triangles), np.uint64), 0)]
    done: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    held = 0
    while waiting:
        tri, index, key, halved = waiting.pop()
        if len(tri) > _POINTS_AT_ONCE:
            n = _POINTS_AT_ONCE
            waiting.append((tri[n:], index[n:], key[n:], halved))
            tri, index, key = tri[:n], index[:n], key[:n]
        edges = np.linalg.norm(tri - tri[:, [1, 2, 0]], axis=2)
        split = (edges.max(axis=1) > longest) & (halved < _HALVINGS)
        if not split.all():
            done.append((tri[~split], index[~split], key[~split]))
            held += len(done[-1][0])
        if split.any():
            tri, index, key = tri[split], index[split], key[split]
            k = edges[split].argmax(axis=1)  # the edge from corner k to corner k+1
            rows = np.arange(len(tri))
            a, b, c = tri[rows, k], tri[rows, (k + 1) % 3], tri[rows, (k + 2) % 3]
            mid = (a + b) / 2
            step = np.uint64(1 << halved)
            waiting.append((
                np.concatenate([np.stack([a, mid, c], 1), np.stack([mid, b, c], 1)]),
                np.concatenate([index, index]),
                np.concatenate([key + step, key + step + step]),
                halved + 1,
            ))
        if done and (held >= _POINTS_AT_ONCE or not waiting):
            yield (np.concatenate([d[0] for d in done]), np.concatenate([d[1] for d in done]),
                   np.concatenate([d[2] for d in done]))
            done, held = [], 0


def _in_order(keys: list[np.ndarray], *found: list[np.ndarray]) -> tuple[np.ndarray, ...]:
    """What was read some at a time, in the halving's order (`_bisect`)."""
    order = np.argsort(np.concatenate(keys), kind="stable")
    return tuple(np.concatenate(parts)[order] for parts in found)
