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
from print_shop.plate import PlateLayout
from print_shop.setup.context import PartContext
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.proposal import Change, GivenLesson, Proposal
from print_shop.setup.reading import Flag, SliceReading
from print_shop.sliced import SlicedJob
from print_shop.slicer.store import ProfilesUsed

__all__ = [
    "EntryPath",
    "MeshReport",
    "Orientation",
    "PlatePart",
    "PrintJobState",
    "PrintStatus",
    "SliceResult",
    "AppliedOrientation",
    "pose_of",
    "turn_words",
]


def pose_of(rotation_deg: tuple[float, float, float]) -> tuple[float, float, float]:
    """The pose in a rotation as printed. No pose turns about z, so a z there is a turn on the
    plate to fit (mqucifer/print-shop#257)."""
    return (rotation_deg[0], rotation_deg[1], 0.0)


def turn_words(deg: float) -> str:
    """A turn on the plate as a person makes it: "45° anticlockwise, seen from above"."""
    if deg == 180:
        return "180°"
    by, way = (deg, "anticlockwise") if deg < 180 else (360 - deg, "clockwise")
    return f"{by:g}° {way}, seen from above"


class EntryPath(StrEnum):
    STL_UPLOAD = "stl_upload"
    #: A file the person sliced by hand: sent as it is (mqucifer/print-shop#164).
    SLICED_FILE = "sliced_file"


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

    #: How far it was turned about z to fit the plate, where it didn't as posed, or 0. It's
    #: printed so only once the person has taken the turn (mqucifer/print-shop#257).
    turn_deg: float = 0.0


class Orientation(BaseModel):
    """How the part sits on the plate, plus the scores that justified it.

    The scores are deterministic and comparable across candidates; choosing
    between them is the judgment, and that is what the model does. The fitness
    function is re-slicing the top two and comparing support mass and time.
    """

    model_config = {"frozen": True}

    #: Euler XYZ in degrees, applied before translating onto the plate.
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)

    #: What would need support, as it lies (`features.overhang_to_support`).
    overhang_area_mm2: float
    bed_contact_area_mm2: float
    height_mm: float
    #: The round or chamfer rising from the plate, left out of the overhang: it's carried by the
    #: layers under it (mqucifer/print-shop#128). Shown at the review beside the overhang.
    flare_area_mm2: float = 0.0

    #: Extents after rotation. Orientation changes which axis is tallest, so the
    #: build-volume check is per-candidate, not per-part.
    bbox_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    #: It doesn't fit as posed and does turned on the plate, which the review offers
    #: (mqucifer/print-shop#257, `orchestrator.fitting.with_turns`).
    fits_turned: bool = False
    #: It fits neither as posed nor in any turn on the plate, so the pose isn't sent back to it
    #: (mqucifer/print-shop#257).
    fits_no_way: bool = False

    #: Which face the user sees. Free text from the model, never control flow.
    primary_visible_face: str = ""

    def fits_in(self, build_volume_mm: tuple[float, float, float]) -> bool:
        return self.fits_turned or all(
            a <= b for a, b in zip(self.bbox_mm, build_volume_mm, strict=True))


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
    #: The pose as measured, which the part's features and the lessons keyed to them are read
    #: in (mqucifer/print-shop#128): its height, and its area on the plate. None: not measured.
    height_mm: float | None = None
    bed_contact_area_mm2: float | None = None
    #: The turn on the plate the person took at the review, to fit (mqucifer/print-shop#257).
    #: `rotation_deg` stays the pose, which is what the features and the faces are read in.
    turn_deg: float = 0.0

    def as_printed(self) -> tuple[float, float, float]:
        """The whole rotation: the pose, then the turn on the plate."""
        x, y, z = self.rotation_deg
        return (x, y, z + self.turn_deg)

    def pose(self) -> str:
        """The pose as the review names it."""
        if self.height_mm is None or self.bed_contact_area_mm2 is None:
            return f"rotation {self.rotation_deg}"
        return (f"rotation {self.rotation_deg}, {self.height_mm:g} mm tall, "
                f"{self.bed_contact_area_mm2:g} mm2 on the plate")


class PlatePart(BaseModel):
    """One part of a job of several (mqucifer/print-shop#109), as far as the job has taken it.

    Not a decision: each part's orientation is an `Outcome` in `PrintJobState.orientations`.
    """

    model_config = {"frozen": True}

    n: int  # from 1, as given
    name: str
    given: str  # the path it was given at, which its context is read beside
    stl: str  # the job's own copy, which is all that's read after it's taken
    sha256: str
    mesh: MeshReport
    candidates: tuple[Orientation, ...] = ()
    laid_traits: tuple[str, ...] = ()
    context: PartContext | None = None
    #: The orientation lessons it was given.
    lessons: tuple[GivenLesson, ...] = ()
    applied: AppliedOrientation | None = None
    features: tuple[Feature, ...] = ()
    #: The times its pose was sent back at the review. A correction doesn't name a part.
    sent_back: tuple[Change, ...] = ()
    #: The pose it lay in as the plate's questions were asked: what an answer on faces is of
    #: (mqucifer/print-shop#248). None before they're asked.
    asked_in: tuple[float, float, float] | None = None


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
    #: Keep the file's own pose: the person laid it for printing (`--as-laid`,
    #: mqucifer/print-shop#128), so no orientation is chosen for it.
    as_laid: NotRequired[bool]
    #: A sidecar or a design run's folder named for the part's context (`--context`,
    #: mqucifer/print-shop#130); None looks beside the STL and above it.
    context_path: NotRequired[str | None]

    #: A hand-sliced file, sent as it is (mqucifer/print-shop#164). With it the job has no STL,
    #: and nothing is ingested, oriented, asked, proposed, sliced or reviewed.
    sliced: NotRequired[SlicedJob]

    #: A job of several STLs (mqucifer/print-shop#109) has these in place of one `stl_path`: the
    #: paths as given, each part, each part's orientation in the same order, and the plate as
    #: it's sliced. A job of one part has none of them.
    stl_paths: NotRequired[tuple[str, ...]]
    parts: NotRequired[tuple[PlatePart, ...]]
    orientations: NotRequired[tuple[Outcome[Orientation], ...]]
    plate: NotRequired[PlateLayout]

    # Artifacts. Local paths; the pipeline is one process (see CONTRACTS.md §1).
    stl_path: NotRequired[str]
    gcode_path: NotRequired[str]
    #: Of the G-code's bytes as sliced: Start and the print node check it again, however long
    #: the job waited (mqucifer/print-shop#209).
    gcode_sha256: NotRequired[str]

    # Deterministic analysis.
    mesh: NotRequired[MeshReport]
    stl_sha256: NotRequired[str]
    slice: NotRequired[SliceResult]

    # Judgment. Each is a full Outcome — proposal, verdict history, accepted.
    orientation: NotRequired[Outcome[Orientation]]
    #: The poses the orientation step weighed (for a pose kept, as laid or from the part's
    #: context, that one, then those measured beside the job by the time it was sliced:
    #: mqucifer/print-shop#235), the part's traits in the file's own pose, and the
    #: kept lessons it was given for them: for the review to send the pose back to one, and for
    #: the lesson drawn from that (mqucifer/print-shop#128).
    orientation_candidates: NotRequired[tuple[Orientation, ...]]
    laid_traits: NotRequired[tuple[str, ...]]
    #: The part's measured features in the file's own pose, so a job that stays in it isn't
    #: measured again before its questions (mqucifer/print-shop#235).
    laid_features: NotRequired[tuple[Feature, ...]]
    orientation_lessons: NotRequired[tuple[GivenLesson, ...]]
    #: The part's context from its design run or a sidecar: its fits, seen faces, purpose, the
    #: side it's laid on and its holds (mqucifer/print-shop#130, mqucifer/print-shop#236).
    part_context: NotRequired[PartContext]

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
    #: The part's measured features as it's sliced, which its lessons are keyed to
    #: (mqucifer/print-shop#106).
    features: NotRequired[tuple[Feature, ...]]
    #: The slice that goes to the printer, read layer by layer, and what it says matters for
    #: this part (mqucifer/print-shop#105).
    slice_reading: NotRequired[SliceReading | None]
    slice_flags: NotRequired[tuple[Flag, ...]]

    # Gates. `pending_gate` set means the graph is parked awaiting a human.
    pending_gate: NotRequired[Gate]
    gate_decisions: NotRequired[tuple[GateDecision, ...]]

    # Execution.
    print_status: NotRequired[PrintStatus]
    print_attempts: NotRequired[int]
