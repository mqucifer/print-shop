"""Meshes with analytically known properties.

Generated rather than committed — `.gitignore` excludes `*.stl`, and a fixture
you can derive by hand is worth more than one you have to trust.

Every value asserted in `test_analyzer.py` is computed on paper below, not read
back out of the implementation under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

__all__ = ["CUBE", "NICKED_CUBE", "OPEN_BOX", "TEE", "Expected", "write_stl"]


@dataclass(frozen=True, slots=True)
class Expected:
    """Hand-computed properties. See the derivations in each fixture."""

    volume_mm3: float | None
    surface_area_mm2: float
    bbox_mm: tuple[float, float, float]
    bed_contact_area_mm2: float
    overhang_area_mm2: float
    max_overhang_deg: float
    is_manifold: bool


def _cube() -> trimesh.Trimesh:
    """20 mm cube resting on the plate.

    volume      20^3                       = 8000 mm^3
    area        6 x 20^2                   = 2400 mm^2
    bed contact one 20x20 face             =  400 mm^2
    overhang    none: the only downward-facing face is on the plate = 0
    """
    cube: trimesh.Trimesh = trimesh.creation.box(extents=(20, 20, 20))
    return cube


def _tee() -> trimesh.Trimesh:
    """A 20x20x10 slab on a 10x10x10 pillar. The classic support case.

    volume      10^3 + 20^2 x 10           = 5000 mm^3
    bed contact the pillar's 10x10 base    =  100 mm^2
    overhang    slab underside minus pillar
                20^2 - 10^2                =  300 mm^2
    max angle   that underside is horizontal, i.e. 90 deg from vertical
    """
    pillar: trimesh.Trimesh = trimesh.creation.box(extents=(10, 10, 10))
    pillar.apply_translation((0, 0, 5))
    slab: trimesh.Trimesh = trimesh.creation.box(extents=(20, 20, 10))
    slab.apply_translation((0, 0, 15))
    tee: trimesh.Trimesh = trimesh.boolean.union([pillar, slab])
    return tee


CUBE = Expected(
    volume_mm3=8000.0,
    surface_area_mm2=2400.0,
    bbox_mm=(20.0, 20.0, 20.0),
    bed_contact_area_mm2=400.0,
    overhang_area_mm2=0.0,
    max_overhang_deg=0.0,
    is_manifold=True,
)

TEE = Expected(
    volume_mm3=5000.0,
    surface_area_mm2=2000.0,
    bbox_mm=(20.0, 20.0, 20.0),
    bed_contact_area_mm2=100.0,
    overhang_area_mm2=300.0,
    max_overhang_deg=90.0,
    is_manifold=True,
)


def _open_box() -> trimesh.Trimesh:
    """A 20 mm cube with two faces removed: not watertight.

    trimesh still returns a volume for this (6000 mm^3 rather than 8000), which
    is exactly the plausible-but-wrong number CONTRACTS.md section 2 is about.
    An open mesh has no defined volume, so the analyzer must report None.

    Surface area: 2400 - one 20x20 face = 2000 mm^2 (the two removed triangles
    are the two halves of a single square face).
    """
    box: trimesh.Trimesh = trimesh.creation.box(extents=(20, 20, 20))
    box.update_faces([i for i in range(len(box.faces)) if i not in (0, 1)])
    return box


OPEN_BOX = Expected(
    volume_mm3=None,  # undefined: the mesh is not closed
    surface_area_mm2=2000.0,
    bbox_mm=(20.0, 20.0, 20.0),
    bed_contact_area_mm2=400.0,
    overhang_area_mm2=0.0,
    max_overhang_deg=0.0,
    is_manifold=False,
)

def _nicked_cube() -> trimesh.Trimesh:
    """A 20 mm cube with a single triangle removed: broken but still sliceable.

    This is what a real damaged STL looks like — a small hole, not a missing
    face. `open_box` is unsliceable (Orca exits 156, "No layers were detected");
    this one Orca accepts.

    It accepts it and gets it wrong: 10 layers and 0.79 cm3, against 100 layers
    and ~3 cm3 for the intact cube. A successful exit code from the slicer is
    not evidence that the G-code describes the part you asked for.
    """
    box: trimesh.Trimesh = trimesh.creation.box(extents=(20, 20, 20))
    box.update_faces([i for i in range(len(box.faces)) if i != 0])
    return box


NICKED_CUBE = Expected(
    volume_mm3=None,  # not watertight
    surface_area_mm2=2200.0,  # 2400 minus one of the two 200 mm2 triangles
    bbox_mm=(20.0, 20.0, 20.0),
    bed_contact_area_mm2=400.0,
    overhang_area_mm2=0.0,
    max_overhang_deg=0.0,
    is_manifold=False,
)

# --------------------------------------------------------------------------
# Three parts with different purposes, for the settings proposal
# (mqucifer/print-shop#7). Their features are what the proposals must trace
# to: a tall thin-walled vase, a thick load-bearing bracket, and a snap fit
# whose hook overhangs.
# --------------------------------------------------------------------------


def _vase() -> trimesh.Trimesh:
    """A 120 mm tall cylinder, 60 mm across, with 2 mm walls and a 2 mm floor."""
    outer = trimesh.creation.cylinder(radius=30, height=120, sections=96)
    outer.apply_translation((0, 0, 60))
    inner = trimesh.creation.cylinder(radius=28, height=120, sections=96)
    inner.apply_translation((0, 0, 62))
    vase: trimesh.Trimesh = trimesh.boolean.difference([outer, inner])
    return vase


def _bracket() -> trimesh.Trimesh:
    """An L-bracket: a 60x30x6 mm plate flat on the bed, and a 6 mm flange standing 40 mm up."""
    plate = trimesh.creation.box(extents=(60, 30, 6))
    plate.apply_translation((30, 15, 3))
    flange = trimesh.creation.box(extents=(6, 30, 40))
    flange.apply_translation((3, 15, 26))
    bracket: trimesh.Trimesh = trimesh.boolean.union([plate, flange])
    return bracket


def _snap() -> trimesh.Trimesh:
    """A snap-fit clip: a 1.6 mm cantilever 18 mm tall on a base, a 1.2 mm hook at its tip.

    The hook's flat underside is the overhang.
    """
    base = trimesh.creation.box(extents=(16, 8, 4))
    base.apply_translation((8, 4, 2))
    beam = trimesh.creation.box(extents=(1.6, 8, 18))
    beam.apply_translation((7.2, 4, 13))
    hook = trimesh.creation.box(extents=(1.2, 8, 2.5))
    hook.apply_translation((8.6, 4, 20.75))
    snap: trimesh.Trimesh = trimesh.boolean.union([base, beam, hook])
    return snap


# --------------------------------------------------------------------------
# Slopes drawn in 0.1 mm steps (mqucifer/print-shop#107). Some CAD exports a
# slope or a round as a stack of 0.1 mm layers: flat undersides and vertical
# risers. Each underside, alone, is a 90 degree ceiling; the printer lays the
# slope the steps make. Each is a profile in x and z, 20 mm deep in y.
# --------------------------------------------------------------------------

_STEP = 0.1


def _profile(points: list[tuple[float, float]], depth: float = 20.0) -> trimesh.Trimesh:
    """A counter-clockwise (x, z) profile, extruded `depth` mm along y."""
    from manifold3d import CrossSection

    solid = CrossSection([points]).extrude(depth).to_mesh()
    mesh = trimesh.Trimesh(vertices=solid.vert_properties[:, :3], faces=solid.tri_verts)
    turn = trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0))  # type: ignore[no-untyped-call]
    mesh.apply_transform(turn)
    mesh.apply_translation((0, depth, 0))
    return mesh


def _stepped_ledge(degrees: float) -> trimesh.Trimesh:
    """A 6 mm wall, 12 mm tall, whose side leans out at `degrees` past vertical from 4 to 8 mm.

    The lean is drawn in 0.1 mm steps: each step's underside is a flat strip
    0.1 x tan(degrees) mm deep, so 0.1 mm at 45 degrees and 0.12 mm at 50.
    """
    run = _STEP * np.tan(np.radians(degrees))
    points = [(0.0, 0.0), (6.0, 0.0)]
    for k in range(40):
        z = 4.0 + k * _STEP
        points += [(6.0 + k * run, z), (6.0 + k * run, z + _STEP)]
    points += [(6.0 + 40 * run, 12.0), (0.0, 12.0)]
    return _profile(_tidy(points))


def _stepped_round() -> trimesh.Trimesh:
    """A 30 x 3 mm cap lying on its face, both long face edges a 1 mm round in 0.1 mm steps.

    Each step is set in by the round at its middle, r - sqrt(r^2 - (r - z)^2):
    0.69 mm at the plate, 0.47, 0.34 and so on to 0 at 1 mm up. The lowest
    steps' undersides are flat strips up to 0.22 mm deep.
    """
    r = 1.0
    insets = [r - np.sqrt(r * r - (r - (k + 0.5) * _STEP) ** 2) for k in range(10)]
    right = [(30.0 - insets[0], 0.0)]
    for k, inset in enumerate(insets):
        right += [(30.0 - inset, k * _STEP), (30.0 - inset, (k + 1) * _STEP)]
    right.append((30.0, 3.0))
    left = [(30.0 - x, z) for x, z in reversed(right)]
    return _profile(_tidy([(insets[0], 0.0), *right, *left]))


def _tidy(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Without repeats, which a step of no depth would leave."""
    out: list[tuple[float, float]] = []
    for x, z in points:
        point = (round(x, 6), round(z, 6))
        if not out or out[-1] != point:
            out.append(point)
    if out[0] == out[-1]:
        out.pop()
    return out


def _long_strip() -> trimesh.Trimesh:
    """A strip 150 mm long, 25 mm wide and 3 mm tall: a long, narrow footprint on a big contact."""
    strip: trimesh.Trimesh = trimesh.creation.box(extents=(150, 25, 3))
    return strip


_BUILDERS = {
    "cube": _cube,
    "tee": _tee,
    "open_box": _open_box,
    "nicked_cube": _nicked_cube,
    "vase": _vase,
    "bracket": _bracket,
    "snap": _snap,
    "stepped_ledge_45": lambda: _stepped_ledge(45.0),
    "stepped_ledge_50": lambda: _stepped_ledge(50.0),
    "stepped_round": _stepped_round,
    "long_strip": _long_strip,
}


def write_stl(name: str, directory: Path) -> Path:
    """Write a fixture mesh to `directory`, seated on the build plate."""
    mesh = _BUILDERS[name]()
    mesh.apply_translation((0, 0, -mesh.bounds[0][2]))
    path = directory / f"{name}.stl"
    mesh.export(path)
    return path
