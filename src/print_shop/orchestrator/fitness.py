"""The slicing-based fitness function for orientation candidates.

`AGENTS.md` rule 2: orientation is scored by re-slicing and comparing support
mass and print time. This module is that score — deterministic, no model.

The critical detail is `scoring_profiles`: with the stock process profile,
support generation is off, so Orca extrudes overhangs into mid-air and every
orientation measures identically. The derived profile forces tree supports on,
which is what makes the geometry visible to the slicer.
"""

from __future__ import annotations

import json
from pathlib import Path

from print_shop.core.state import SliceResult
from print_shop.ingest.orientation import write_rotated
from print_shop.slicer.orca import SlicerProfiles, slice_model

__all__ = ["scoring_profiles", "slice_candidate"]

#: Overrides forced into the derived scoring profile. Without these the fitness
#: function is blind: no supports means no overhang cost, no signal.
_SCORING_OVERRIDES: dict[str, str] = {
    "enable_support": "1",
    "support_type": "tree(auto)",
    "support_threshold_angle": "45",
}


def scoring_profiles(profiles: SlicerProfiles, workdir: Path) -> SlicerProfiles:
    """Return a copy of `profiles` whose process profile forces supports on.

    The derived JSON is written into `workdir` so it survives independently of
    the caller's temp directory. `compatible_printers` is copied verbatim from
    the base profile — omit it and Orca exits 239 with "process not compatible
    with printer". So is the plate, `curr_bed_type`, when the base sets one.
    """
    base = json.loads(Path(profiles.process).read_text())

    derived: dict[str, object] = {
        "type": "process",
        "name": f"{base['name']} + supports (scoring)",
        "from": "User",
        "inherits": base["name"],
        "compatible_printers": base["compatible_printers"],
    }
    derived.update(_SCORING_OVERRIDES)
    # The overlay inherits from the system profile by name, so a plate set on
    # the profile it was given (`with_bed_type`) would be lost: carry it.
    if "curr_bed_type" in base:
        derived["curr_bed_type"] = base["curr_bed_type"]

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "scoring_process.json"
    out.write_text(json.dumps(derived, indent=2))

    return profiles.model_copy(update={"process": out})


def slice_candidate(
    stl_path: str | Path,
    rotation_deg: tuple[float, float, float],
    profiles: SlicerProfiles,
    outdir: Path | str,
) -> SliceResult:
    """Rotate, seat, and slice one candidate; return the slicer's numbers.

    This is the fitness function itself: same input, same output, every time.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rotated = write_rotated(stl_path, rotation_deg, outdir / "candidate.stl")
    return slice_model(rotated, profiles, outdir)
