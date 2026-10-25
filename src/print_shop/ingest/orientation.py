"""Candidate orientation enumeration and deterministic geometric scoring.

No judgment here: a fixed set of rotations, each measured with the same
overhang/bed-contact definitions as `analyzer.analyze`. Choosing between the
candidates is the judgment node's job, and it will be scored against the
slicing-based fitness function in `src/orchestrator/fitness.py`.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from print_shop.core.state import Orientation
from print_shop.ingest.analyzer import measure_orientation

__all__ = ["AXIS_ALIGNED", "candidates", "write_rotated"]

#: The six axis-aligned orientations, in order.
AXIS_ALIGNED: tuple[tuple[float, float, float], ...] = (
    (0.0, 0.0, 0.0),
    (180.0, 0.0, 0.0),
    (90.0, 0.0, 0.0),
    (270.0, 0.0, 0.0),
    (0.0, 90.0, 0.0),
    (0.0, 270.0, 0.0),
)


def _rotation_matrix(rotation_deg: tuple[float, float, float]) -> np.ndarray:
    """Euler XYZ in degrees, applied about X then Y then Z."""
    # trimesh.transformations.rotation_matrix is untyped; build the matrices
    # directly so the return type is a concrete ndarray.
    def rot(axis: tuple[float, float, float], deg: float) -> np.ndarray:
        c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
        x, y, z = axis
        return np.array(
            [
                [x * x * (1 - c) + c, x * y * (1 - c) - z * s, x * z * (1 - c) + y * s, 0.0],
                [y * x * (1 - c) + z * s, y * y * (1 - c) + c, y * z * (1 - c) - x * s, 0.0],
                [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, z * z * (1 - c) + c, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )

    rx = rot((1.0, 0.0, 0.0), rotation_deg[0])
    ry = rot((0.0, 1.0, 0.0), rotation_deg[1])
    rz = rot((0.0, 0.0, 1.0), rotation_deg[2])
    return np.asarray(rz @ ry @ rx)


def _seated(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Translate a mesh so it rests on the plate (z_min == 0)."""
    z_min = float(mesh.bounds[0][2])
    if z_min != 0.0:
        mesh.apply_translation((0.0, 0.0, -z_min))
    return mesh


def write_rotated(
    stl_path: str | Path, rotation_deg: tuple[float, float, float], out_path: str | Path
) -> Path:
    """Write the mesh rotated by `rotation_deg` and seated on the plate, as STL."""
    path = Path(stl_path)
    if not path.exists():
        raise FileNotFoundError(f"STL file not found: {stl_path}")

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Expected a Trimesh, got {type(mesh).__name__}")

    mesh.apply_transform(_rotation_matrix(rotation_deg))
    _seated(mesh)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(out)
    return out


def candidates(
    stl_path: str | Path, overhang_threshold_deg: float = 45.0
) -> list[Orientation]:
    """One `Orientation` per entry in `AXIS_ALIGNED`, in that order.

    Each candidate is the mesh rotated by the Euler angles and seated on the
    plate, measured with the same overhang/bed-contact definitions as
    `analyzer.analyze`.
    """
    path = Path(stl_path)
    if not path.exists():
        raise FileNotFoundError(f"STL file not found: {stl_path}")

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Expected a Trimesh, got {type(mesh).__name__}")

    result: list[Orientation] = []
    for rotation in AXIS_ALIGNED:
        rotated = mesh.copy()
        rotated.apply_transform(_rotation_matrix(rotation))
        _seated(rotated)
        overhang_area, bed_contact_area, height_mm = measure_orientation(
            rotated, overhang_threshold_deg
        )
        result.append(
            Orientation(
                rotation_deg=rotation,
                overhang_area_mm2=overhang_area,
                bed_contact_area_mm2=bed_contact_area,
                height_mm=height_mm,
            )
        )
    return result
