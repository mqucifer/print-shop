"""Meshes with analytically known properties.

Generated rather than committed — `.gitignore` excludes `*.stl`, and a fixture
you can derive by hand is worth more than one you have to trust.

Every value asserted in `test_analyzer.py` is computed on paper below, not read
back out of the implementation under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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

_BUILDERS = {
    "cube": _cube,
    "tee": _tee,
    "open_box": _open_box,
    "nicked_cube": _nicked_cube,
}


def write_stl(name: str, directory: Path) -> Path:
    """Write a fixture mesh to `directory`, seated on the build plate."""
    mesh = _BUILDERS[name]()
    mesh.apply_translation((0, 0, -mesh.bounds[0][2]))
    path = directory / f"{name}.stl"
    mesh.export(path)
    return path
