"""PrintJobState — what flows between pipeline nodes.

Two rules shape this schema:

1. **Values are typed models, not `dict[str, Any]`.** The previous version had
   ``mesh_stats: Optional[dict[str, Any]]``, which is how a field named
   `thin_features_mm` shipped for months while actually returning short edge
   lengths. A wrong number in a typed model is still wrong, but it is at least
   named and reviewable.

2. **Decisions are `Outcome`s, not bare values.** A node never writes a config
   into state; it writes the proposal, the verdict history, and whether it was
   accepted. If `accepted` is False the caller routes to a gate.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import BaseModel

from print_shop.core.contracts import Gate, GateDecision, Outcome
from print_shop.setup.intake import Intake
from print_shop.setup.proposal import Change, Proposal
from print_shop.slicer.store import ProfilesUsed

__all__ = [
    "EntryPath",
    "MeshReport",
    "Orientation",
    "PrintJobState",
    "PrintStatus",
    "SliceResult",
    "AppliedOrientation",
]


class EntryPath(StrEnum):
    STL_UPLOAD = "stl_upload"


class PrintStatus(StrEnum):
    PENDING = "pending"
    PRINTING = "printing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


# --------------------------------------------------------------------------
# Domain payloads
# --------------------------------------------------------------------------


class MeshReport(BaseModel):
    """Deterministic geometry facts. No judgment here — see `Orientation`.

    Measured for one specific orientation; re-measure per candidate.
    """

    model_config = {"frozen": True}

    is_manifold: bool

    #: None when the mesh is not watertight, because an open mesh has no
    #: defined volume. trimesh will still return a number — a cube missing two
    #: faces reports 6000 instead of 8000 — and a plausible wrong number is
    #: the failure mode CONTRACTS.md section 2 exists to prevent.
    volume_mm3: float | None
    surface_area_mm2: float
    bbox_mm: tuple[float, float, float]

    #: Area of faces needing support: normal.z < -cos(overhang_threshold_deg).
    #: A vertical wall has normal.z == 0 and is NOT an overhang. The previous
    #: implementation measured the angle to +Z and flagged everything past 45
    #: degrees, so every vertical wall counted and every model got supports.
    overhang_area_mm2: float
    overhang_threshold_deg: float = 45.0

    #: Worst downward tilt present, degrees from vertical. 90 = ceiling.
    max_overhang_deg: float = 0.0

    #: Footprint touching the build plate. Drives adhesion and warp risk.
    bed_contact_area_mm2: float = 0.0

    #: sha256 of the STL bytes at the moment this report was produced.
    #: Lets downstream nodes verify the file has not been swapped out.
    stl_sha256: str = ""

    @property
    def height_mm(self) -> float:
        return self.bbox_mm[2]


class SliceResult(BaseModel):
    """What the slicer produced, and what the approval gate shows a human.

    Lives here rather than in `src/slicer/` because it is a contract: the graph
    stores it in state, the gate renders it, and `print_time_s` / `filament_cm3`
    are the numbers the orientation agent will be scored on. The slicer
    *implements* this type; it does not own it.
    """

    model_config = {"frozen": True}

    gcode_path: Path
    layer_count: int
    max_z_mm: float
    filament_mm: float
    filament_cm3: float
    #: None when Orca reports 0.00 g (density unknown), not a real measurement.
    filament_g: float | None
    print_time_s: int

    #: What the slicer actually used, read back out of the G-code it wrote
    #: rather than out of the profiles it was handed. These name the file that
    #: goes to the printer (`src/slicer/naming.py`) and they are what a human
    #: at the machine reads off its screen. Optional because a profile that
    #: omits one must not fail a job that has already sliced successfully.
    nozzle_mm: float | None = None
    material: str | None = None
    layer_height_mm: float | None = None


class Orientation(BaseModel):
    """How the part sits on the plate, plus the scores that justified it.

    The scores are deterministic and comparable across candidates; choosing
    between them is the judgment, and that is what the model does. The fitness
    function is re-slicing the top two and comparing support mass and time.
    """

    model_config = {"frozen": True}

    #: Euler XYZ in degrees, applied before translating onto the plate.
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)

    overhang_area_mm2: float
    bed_contact_area_mm2: float
    height_mm: float

    #: Extents after rotation. Orientation changes which axis is tallest, so the
    #: build-volume check is per-candidate, not per-part.
    bbox_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    #: Which face the user sees. Free text from the model, never control flow.
    primary_visible_face: str = ""

    def fits_in(self, build_volume_mm: tuple[float, float, float]) -> bool:
        return all(a <= b for a, b in zip(self.bbox_mm, build_volume_mm, strict=True))


class AppliedOrientation(BaseModel):
    """The orientation the slicer actually used, and why.

    Distinct from `PrintJobState.orientation`, which is the *decision*. They
    differ whenever no proposal was accepted — a non-manifold mesh approved at
    the review gate never runs `orient` at all, and is sliced in its authored
    pose. The gate shows a human this field, because approving a print you were
    not told the orientation of is not informed consent.
    """

    model_config = {"frozen": True}

    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rationale: str = ""


# --------------------------------------------------------------------------
# Job state
# --------------------------------------------------------------------------


class PrintJobState(TypedDict):
    """State threaded through the graph and checkpointed after every node."""

    # Identity — written once at entry, never mutated.
    job_id: str
    trace_id: str
    entry: EntryPath
    created_at: str  # ISO-8601

    # Artifacts. Local paths; the pipeline is one process (see CONTRACTS.md §1).
    stl_path: NotRequired[str]
    gcode_path: NotRequired[str]

    # Deterministic analysis.
    mesh: NotRequired[MeshReport]
    stl_sha256: NotRequired[str]
    slice: NotRequired[SliceResult]

    # Judgment. Each is a full Outcome — proposal, verdict history, accepted.
    orientation: NotRequired[Outcome[Orientation]]

    #: What the slicer actually did, which is not always what was decided.
    #: Every key a node returns must be declared here — LangGraph silently
    #: drops undeclared keys, so an omission loses data with no error at all.
    applied_orientation: NotRequired[AppliedOrientation]
    #: The store's profiles the slice used, for the ledger. Kept in state so a
    #: job resumed by another process records what it was sliced with.
    profiles_used: NotRequired[ProfilesUsed | None]

    #: What the part is for (mqucifer/print-shop#7), the settings proposed
    #: from it over the presets, and the Sponsor's corrections at the review.
    intake: NotRequired[Intake]
    proposal: NotRequired[Proposal]
    corrections: NotRequired[tuple[Change, ...]]

    # Gates. `pending_gate` set means the graph is parked awaiting a human.
    pending_gate: NotRequired[Gate]
    gate_decisions: NotRequired[tuple[GateDecision, ...]]

    # Execution.
    print_status: NotRequired[PrintStatus]
    print_attempts: NotRequired[int]
