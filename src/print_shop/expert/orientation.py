"""The orientation agent — the first node where a model decides.

`src/ingest/orientation.py` enumerates and scores candidates deterministically.
This module chooses among them: the model proposes, the validator disposes
(`CONTRACTS.md` section 3). The validator's job is to catch choices that are
*wrong*, not to enforce the geometric minimum — a small overhang regression is
the model's judgment and passes as a warning.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from print_shop.core.contracts import NoAutoFix, OneOf, Proposal, Severity, Verdict, Violation
from print_shop.core.state import Orientation
from print_shop.expert.client import LLMConfig, complete_json

__all__ = [
    "MAX_OVERHANG_REGRESSION_MM2",
    "OrientationRequest",
    "propose_geometric",
    "propose_orientation",
    "validate_orientation",
]

#: Overhang regression (mm2) against the best candidate that the model is
#: allowed to make on its own judgment. Beyond this, the choice is corrected
#: to the best candidate.
MAX_OVERHANG_REGRESSION_MM2: float = 150.0


class OrientationRequest(BaseModel):
    """What the orientation node is given: scored candidates and the plate."""

    model_config = {"frozen": True}

    candidates: tuple[Orientation, ...]
    build_volume_mm: tuple[float, float, float]
    part_description: str = ""


def _best(candidates: tuple[Orientation, ...]) -> Orientation:
    """Lowest overhang; ties broken by highest bed contact."""
    return min(
        candidates, key=lambda c: (c.overhang_area_mm2, -c.bed_contact_area_mm2)
    )


def propose_geometric(request: OrientationRequest) -> Proposal[Orientation]:
    """Deterministic fallback proposer: pick the geometrically best candidate.

    Used when the model endpoint is unreachable. The rationale states that the
    model was unavailable so the approval gate shows a human exactly what
    happened. This proposal goes through the same ``validate_orientation`` as
    any other — it is a different proposer, not an escape hatch.
    """
    best = _best(request.candidates)
    rationale = (
        f"Model endpoint was unavailable; fell back to geometric selection. "
        f"Chose rotation {best.rotation_deg} with the lowest overhang "
        f"({best.overhang_area_mm2:.1f} mm2) and highest bed contact "
        f"({best.bed_contact_area_mm2:.1f} mm2) among the scored candidates."
    )
    return Proposal(
        value=best,
        source="geometric",
        rationale=rationale,
        confidence=None,
    )


def _candidate_by_rotation(
    candidates: tuple[Orientation, ...], rotation: tuple[float, float, float]
) -> Orientation | None:
    for c in candidates:
        if c.rotation_deg == rotation:
            return c
    return None


def _rebuild(
    candidates: tuple[Orientation, ...], rotation: tuple[float, float, float]
) -> Orientation:
    """The candidate with this rotation, or a zeroed stand-in if none.

    The stand-in exists so a hallucinated rotation can be carried in a
    Proposal at all; the validator rejects it and there is no safe repair.
    """
    found = _candidate_by_rotation(candidates, rotation)
    if found is not None:
        return found
    return Orientation(
        rotation_deg=rotation,
        overhang_area_mm2=0.0,
        bed_contact_area_mm2=0.0,
        height_mm=0.0,
        bbox_mm=(0.0, 0.0, 0.0),
    )


def propose_orientation(
    request: OrientationRequest, config: LLMConfig
) -> Proposal[Orientation]:
    """Ask the model to pick one of the scored candidates.

    The proposal's value is the full candidate from the list, so its scores
    stay trustworthy. If the model returns a rotation that is not on the list,
    the proposal carries that rotation with zeroed scores and the validator
    rejects it.
    """
    lines = [
        f"Rotation {c.rotation_deg}: overhang {c.overhang_area_mm2:.1f} mm2, "
        f"bed contact {c.bed_contact_area_mm2:.1f} mm2, "
        f"height {c.height_mm:.1f} mm, bbox {c.bbox_mm}"
        for c in request.candidates
    ]
    user = (
        "Pick the best orientation for printing this part.\n"
        f"Build volume (mm): {request.build_volume_mm}\n"
        + (f"Part: {request.part_description}\n" if request.part_description else "")
        + "Candidates:\n"
        + "\n".join(lines)
        + "\nReturn the rotation_deg of your choice, a rationale, and a confidence in [0, 1]."
    )
    schema: dict[str, Any] = {
        "name": "orientation_choice",
        "schema": {
            "type": "object",
            "properties": {
                "rotation_deg": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["rotation_deg", "rationale", "confidence"],
            "additionalProperties": False,
        },
        "strict": True,
    }
    reply = complete_json(
        config, "You pick a print orientation. Reply with JSON only.", user, schema
    )

    raw = reply["rotation_deg"]
    if len(raw) != 3:
        raise ValueError(f"expected 3 rotation values, got {raw!r}")
    rotation: tuple[float, float, float] = (float(raw[0]), float(raw[1]), float(raw[2]))
    return Proposal(
        value=_rebuild(request.candidates, rotation),
        source=f"model:{config.model}",
        rationale=str(reply["rationale"]),
        confidence=float(reply["confidence"]),
    )


def validate_orientation(
    proposal: Proposal[Orientation], request: OrientationRequest
) -> Verdict:
    """Pure check of a proposed orientation against the candidates and plate."""
    chosen = proposal.value
    best = _best(request.candidates)
    violations: list[Violation] = []

    # 1. The model invented an orientation nobody scored.
    if _candidate_by_rotation(request.candidates, chosen.rotation_deg) is None:
        return Verdict(
            (
                Violation(
                    field="rotation_deg",
                    rule="orientation.not_a_candidate",
                    observed=chosen.rotation_deg,
                    allowed=NoAutoFix(
                        reason="rotation is not one of the scored candidates; "
                        "picking on its behalf would invent a decision"
                    ),
                    detail="the model returned a rotation that was not offered",
                ),
            )
        )

    # A corrected proposal carries the real candidate's scores: the corrector
    # only rewrites rotation_deg, so the scores must be re-read from the
    # candidate list or the corrected value would keep the old candidate's
    # numbers and never pass its own validator.
    resolved = _candidate_by_rotation(request.candidates, chosen.rotation_deg)
    assert resolved is not None
    chosen = resolved

    # 2. The chosen candidate does not fit the plate.
    if not chosen.fits_in(request.build_volume_mm):
        fitting = [c for c in request.candidates if c.fits_in(request.build_volume_mm)]
        suggested = _best(tuple(fitting)).rotation_deg if fitting else best.rotation_deg
        violations.append(
            Violation(
                field="rotation_deg",
                rule="orientation.build_volume",
                observed=chosen.rotation_deg,
                allowed=OneOf(tuple(c.rotation_deg for c in fitting)) if fitting else NoAutoFix(
                    reason="no offered candidate fits the build volume"
                ),
                suggested=suggested,
                detail=f"bbox {chosen.bbox_mm} exceeds build volume {request.build_volume_mm}",
            )
        )

    # 3. Large overhang regression against the best: a mistake, correct it.
    regression = chosen.overhang_area_mm2 - best.overhang_area_mm2
    if regression > MAX_OVERHANG_REGRESSION_MM2:
        violations.append(
            Violation(
                field="rotation_deg",
                rule="orientation.overhang_regression",
                observed=chosen.rotation_deg,
                allowed=OneOf(tuple(c.rotation_deg for c in request.candidates)),
                suggested=best.rotation_deg,
                detail=f"overhang {chosen.overhang_area_mm2:.1f} mm2 is "
                f"{regression:.1f} mm2 worse than the best {best.overhang_area_mm2:.1f} mm2",
            )
        )
    elif regression > 0:
        # 4. Small regression: the model's judgment. Warn, do not block.
        violations.append(
            Violation(
                field="rotation_deg",
                rule="orientation.overhang_regression_minor",
                observed=chosen.rotation_deg,
                allowed=NoAutoFix(reason="within tolerance; the model's call"),
                severity=Severity.WARNING,
                detail=f"overhang {chosen.overhang_area_mm2:.1f} mm2 is "
                f"{regression:.1f} mm2 worse than the best {best.overhang_area_mm2:.1f} mm2",
            )
        )

    return Verdict(tuple(violations))