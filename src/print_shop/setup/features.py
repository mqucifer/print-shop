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
#: Off the plate: a face below this height is the bottom, not a bridge.
_ABOVE_PLATE_MM = 0.1


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
    centroids_z = mesh.triangles_center[:, 2] - lo[2]
    above = centroids_z > _ABOVE_PLATE_MM

    down = normals[:, 2] < -np.cos(np.radians(_OVERHANG_DEG))
    overhang = float(areas[down & above].sum())
    # Degrees past vertical: 0 for a wall, 90 for a ceiling.
    steepest = (
        float(np.degrees(np.arcsin(np.clip(-normals[down & above, 2], -1, 1))).max())
        if (down & above).any()
        else 0.0
    )
    bridge = float(areas[(normals[:, 2] < -_FLAT) & above].sum())
    flat_top = float(areas[normals[:, 2] > _FLAT].sum())
    volume = float(mesh.volume) if mesh.is_watertight else 0.0
    thickness = 2 * volume / float(mesh.area) if mesh.area and volume else 0.0
    narrow = min(x, y)

    return (
        Feature(id="height", value=round(height, 1), unit="mm", says="height as it prints"),
        Feature(id="footprint", value=round(x, 1), unit="mm",
                says=f"x by {round(y, 1)} mm in y, the outline on the plate"),
        Feature(id="bed_contact", value=round(bed_contact_area_mm2), unit="mm2",
                says="area touching the plate, which holds it down"),
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
