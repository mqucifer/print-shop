"""Mesh analysis: load an STL, seat it on the build plate, and measure geometry.

The overhang metric measures the angle each face makes *from vertical*:
a vertical wall is 0 degrees, a horizontal ceiling is 90 degrees.
A face is an overhang when that angle exceeds the threshold AND the face
is not resting on the build plate (bed contact).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import trimesh

from print_shop.core.state import MeshReport

__all__ = ["StlChangedError", "analyze", "file_sha256", "measure_orientation", "verify_unchanged"]


class StlChangedError(RuntimeError):
    """The STL on disk is not the one that was analysed."""


def file_sha256(path: str | Path) -> str:
    """Hex sha256 of the file contents, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_unchanged(path: str | Path, expected_sha256: str) -> None:
    """Raise StlChangedError unless the file still hashes to expected.

    **This narrows the window; it does not close it.** The caller re-opens the
    file afterwards, so a swap between this check and that read is still
    possible — a time-of-check/time-of-use gap. What it removes is the large
    window: `ingest` and `orient` are separated by a checkpoint write, and a
    job parked at a gate can resume hours later, on a different process, with
    the upload long since overwritten. That span goes from hours to
    microseconds.

    Closing the residual gap means reading the bytes once and parsing from
    memory through orientation *and* slicing, since `candidates`,
    `write_rotated` and `slice_model` each open the path again. That is a
    larger change than this invariant justifies today; see `BACKLOG.md`.
    """
    if not expected_sha256:
        raise StlChangedError(
            f"No recorded hash for {path}; cannot verify the file is unchanged. "
            f"Either ingest did not run, or this job was checkpointed before "
            f"STL hashing existed. Re-submit it rather than resuming."
        )
    actual = file_sha256(path)
    if actual != expected_sha256:
        raise StlChangedError(
            f"STL file changed since analysis: {path}\n"
            f"expected {expected_sha256[:16]}..., got {actual[:16]}..."
        )


def measure_orientation(
    mesh: trimesh.Trimesh, overhang_threshold_deg: float = 45.0
) -> tuple[float, float, float]:
    """Measure a seated mesh: (overhang_area_mm2, bed_contact_area_mm2, height_mm).

    Same definitions as `analyze`: overhang is downward-facing area (not on the
    plate) whose angle from vertical exceeds the threshold; bed contact is
    downward-facing area at z ≈ 0. The mesh must already be seated (z_min == 0).
    """
    normals: np.ndarray = mesh.face_normals
    areas: np.ndarray = mesh.area_faces
    face_centroids: np.ndarray = mesh.triangles.mean(axis=1)

    # Downward-facing: normal.z < 0 (with small epsilon to avoid float noise)
    downward = normals[:, 2] < -1e-6

    # Bed contact: downward-facing AND at the bottom of the model (z ≈ 0 after seating)
    bed_contact = downward & (face_centroids[:, 2] < 1e-6)

    # Overhang: downward-facing, NOT bed contact, AND angle from vertical > threshold.
    # Angle from vertical = degrees(asin(|n.z|))).
    angles_from_vertical = np.degrees(np.arcsin(np.abs(normals[:, 2])))

    overhang_mask = (
        downward
        & ~bed_contact
        & (angles_from_vertical > overhang_threshold_deg)
    )

    overhang_area = float(areas[overhang_mask].sum()) if np.any(overhang_mask) else 0.0
    bed_contact_area = float(areas[bed_contact].sum()) if np.any(bed_contact) else 0.0
    height_mm = float(mesh.bounds[1][2] - mesh.bounds[0][2])
    return overhang_area, bed_contact_area, height_mm


def analyze(stl_path: str | Path, overhang_threshold_deg: float = 45.0) -> MeshReport:
    """Load an STL file, seat it on the build plate, and return a MeshReport.

    Args:
        stl_path: Path to the STL file.
        overhang_threshold_deg: Angle (in degrees) from vertical above which
            a downward-facing face is considered an overhang. A vertical wall
            is 0 degrees; a horizontal ceiling is 90 degrees.

    Returns:
        A frozen MeshReport with measured geometry facts.

    Raises:
        FileNotFoundError: If the STL file does not exist.
        ValueError: If the file cannot be parsed as a valid mesh.
    """
    path = Path(stl_path)
    if not path.is_file():
        raise FileNotFoundError(f"STL file not found: {stl_path}")
    sha = file_sha256(path)

    mesh = trimesh.load(path, force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Expected a Trimesh, got {type(mesh).__name__}")

    # Seat on the build plate: translate so z_min == 0
    z_min = float(mesh.bounds[0][2])
    if z_min != 0.0:
        mesh.apply_translation((0.0, 0.0, -z_min))

    # Basic geometry
    # An open (non-watertight) mesh encloses nothing, so its volume is undefined.
    # trimesh still returns a number for open meshes (e.g. a cube missing two
    # faces reports 6000 instead of 8000), which is the plausible-but-wrong
    # failure mode CONTRACTS.md section 2 exists to prevent.
    is_manifold = bool(mesh.is_watertight)
    volume: float | None = float(mesh.volume) if is_manifold else None
    surface_area = float(mesh.area)
    bounds = mesh.bounds
    bbox = (
        float(bounds[1][0] - bounds[0][0]),
        float(bounds[1][1] - bounds[0][1]),
        float(bounds[1][2] - bounds[0][2]),
    )
    # Face-level analysis
    overhang_area, bed_contact_area, _height = measure_orientation(
        mesh, overhang_threshold_deg
    )

    # Max overhang angle: the largest angle from vertical among overhang faces
    normals = mesh.face_normals
    downward = normals[:, 2] < -1e-6
    bed_contact = downward & (mesh.triangles.mean(axis=1)[:, 2] < 1e-6)
    angles_from_vertical = np.degrees(np.arcsin(np.abs(normals[:, 2])))
    overhang_mask = (
        downward
        & ~bed_contact
        & (angles_from_vertical > overhang_threshold_deg)
    )
    if np.any(overhang_mask):
        max_overhang_deg = float(angles_from_vertical[overhang_mask].max())
    else:
        max_overhang_deg = 0.0

    return MeshReport(
        is_manifold=is_manifold,
        volume_mm3=volume,
        surface_area_mm2=surface_area,
        bbox_mm=bbox,
        overhang_area_mm2=overhang_area,
        overhang_threshold_deg=overhang_threshold_deg,
        max_overhang_deg=max_overhang_deg,
        bed_contact_area_mm2=bed_contact_area,
        stl_sha256=sha,
    )
