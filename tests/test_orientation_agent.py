"""Specification for the orientation agent — the first node where a model decides.

Everything before this was deterministic. `CONTRACTS.md` section 3 governs what
happens here: a model proposes, a validator disposes.

The validator's job is to catch choices that are *wrong*, not to enforce the
choice that is *optimal*. If it rejected anything short of the geometric
minimum, the model would be decoration — the arg-min is a one-liner and needs no
LLM. So:

- picking a rotation that is not on the candidate list is a hallucination and
  escalates immediately, unfixable
- a part that does not fit the plate is corrected to one that does
- a *large* overhang regression against the best available is an error and is
  corrected
- a *small* regression is the model's judgment and is allowed through as a
  warning

The tee is the case with a knowable answer: (180,0,0) has zero overhang, and
the geometry, the slicer and the model all have to agree on it.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from pydantic import SecretStr

from print_shop.core.contracts import EscalationReason, NoAutoFix, Proposal, propose_validate
from print_shop.core.state import Orientation
from print_shop.env import SETTINGS_PATH
from print_shop.expert.client import LLMConfig
from print_shop.expert.orientation import (
    MAX_OVERHANG_REGRESSION_MM2,
    OrientationRequest,
    propose_orientation,
    validate_orientation,
)

BUILD_VOLUME = (257.0, 257.0, 257.0)


def _cand(rot: tuple[float, float, float], overhang: float, bed: float, h: float = 20.0,
          bbox: tuple[float, float, float] = (20.0, 20.0, 20.0)) -> Orientation:
    return Orientation(
        rotation_deg=rot, overhang_area_mm2=overhang, bed_contact_area_mm2=bed,
        height_mm=h, bbox_mm=bbox,
    )


#: The tee's real candidate scores, from tests/test_orientation.py.
TEE_CANDIDATES = (
    _cand((0.0, 0.0, 0.0), 300.0, 100.0),
    _cand((180.0, 0.0, 0.0), 0.0, 400.0),
    _cand((90.0, 0.0, 0.0), 100.0, 200.0),
    _cand((270.0, 0.0, 0.0), 100.0, 200.0),
    _cand((0.0, 90.0, 0.0), 100.0, 200.0),
    _cand((0.0, 270.0, 0.0), 100.0, 200.0),
)


@pytest.fixture
def request_() -> OrientationRequest:
    return OrientationRequest(
        candidates=TEE_CANDIDATES,
        build_volume_mm=BUILD_VOLUME,
        part_description="a bracket; the wide flat face is the visible surface",
    )


def _proposal(o: Orientation, confidence: float = 0.9) -> Proposal[Orientation]:
    return Proposal(value=o, source="test", rationale="because", confidence=confidence)


# --------------------------------------------------------------------------
# The validator — pure, no model involved
# --------------------------------------------------------------------------


def test_accepts_the_best_candidate(request_: OrientationRequest) -> None:
    v = validate_orientation(_proposal(TEE_CANDIDATES[1]), request_)
    assert v.accepted
    assert v.violations == ()


def test_an_invented_rotation_escalates_and_cannot_be_fixed(
    request_: OrientationRequest,
) -> None:
    """A rotation that is not on the list means the model made one up.

    There is no safe repair for that — picking a different candidate on its
    behalf would be inventing a decision nobody made — so it goes to a human.
    """
    made_up = _cand((37.0, 12.0, 0.0), 5.0, 390.0)
    v = validate_orientation(_proposal(made_up), request_)

    assert not v.accepted
    assert len(v.blockers) == 1
    assert isinstance(v.blockers[0].allowed, NoAutoFix)
    assert not v.fixable


def test_a_large_overhang_regression_is_corrected_to_the_best(
    request_: OrientationRequest,
) -> None:
    """300 mm2 against an available 0 is not judgment, it is a mistake."""
    v = validate_orientation(_proposal(TEE_CANDIDATES[0]), request_)

    assert not v.accepted
    assert v.fixable, "a real candidate exists, so this is repairable"
    blocker = v.blockers[0]
    assert blocker.field == "rotation_deg"
    assert blocker.suggested == (180.0, 0.0, 0.0)


def test_a_small_overhang_regression_is_the_models_call(
    request_: OrientationRequest,
) -> None:
    """100 mm2 over the best is within tolerance — the model may have a reason.

    This is the test that keeps the validator from making the model pointless.
    """
    assert MAX_OVERHANG_REGRESSION_MM2 >= 100.0
    v = validate_orientation(_proposal(TEE_CANDIDATES[2]), request_)

    assert v.accepted
    assert len(v.violations) == 1
    assert v.violations[0].severity.value == "warning"


def test_a_part_that_does_not_fit_the_plate_is_corrected() -> None:
    too_tall = _cand((90.0, 0.0, 0.0), 0.0, 400.0, h=400.0, bbox=(20.0, 20.0, 400.0))
    fits = _cand((0.0, 0.0, 0.0), 50.0, 400.0, h=20.0, bbox=(20.0, 20.0, 20.0))
    req = OrientationRequest(
        candidates=(too_tall, fits), build_volume_mm=BUILD_VOLUME, part_description=""
    )
    v = validate_orientation(_proposal(too_tall), req)

    assert not v.accepted
    assert any(b.rule.startswith("orientation.build_volume") for b in v.blockers)
    assert v.fixable


def test_the_loop_corrects_a_bad_choice_without_a_human(
    request_: OrientationRequest,
) -> None:
    """End to end through propose_validate with a deliberately bad proposer."""
    outcome = propose_validate(
        lambda r: _proposal(TEE_CANDIDATES[0]),  # the 300 mm2 one
        validate_orientation,
        request_,
    )
    assert outcome.accepted
    assert outcome.proposal.value.rotation_deg == (180.0, 0.0, 0.0)
    assert outcome.proposal.source == "corrector"


def test_a_hallucinated_choice_reaches_a_human(request_: OrientationRequest) -> None:
    outcome = propose_validate(
        lambda r: _proposal(_cand((37.0, 12.0, 0.0), 5.0, 390.0)),
        validate_orientation,
        request_,
    )
    assert not outcome.accepted
    assert outcome.escalation is EscalationReason.UNFIXABLE


# --------------------------------------------------------------------------
# The model itself
# --------------------------------------------------------------------------


#: The live tests read a key the way the daemon does. On the Mac a builder
#: points this at its own key file, e.g.
#: `PRINT_SHOP_LLM_SETTINGS=~/.config/crew/builders.env`; it is a path, never a key.
LIVE_SETTINGS = Path(os.environ.get("PRINT_SHOP_LLM_SETTINGS", str(SETTINGS_PATH))).expanduser()


def _live_config() -> LLMConfig | None:
    try:
        return LLMConfig.from_settings(LIVE_SETTINGS)
    except RuntimeError:
        return None


def _endpoint_up(cfg: LLMConfig | None) -> bool:
    if cfg is None:
        return False
    request = urllib.request.Request(
        f"{cfg.base_url}/models",
        headers={"Authorization": f"Bearer {cfg.api_key.get_secret_value()}"},
    )
    try:
        urllib.request.urlopen(request, timeout=5)
        return True
    except (urllib.error.URLError, OSError):
        return False


LIVE = _live_config()

needs_llm = pytest.mark.skipif(
    not _endpoint_up(LIVE),
    reason="no LiteLLM key in the settings, or LiteLLM :4000 is not answering",
)


def live() -> LLMConfig:
    assert LIVE is not None
    return LIVE


@pytest.mark.llm
@needs_llm
def test_the_model_returns_a_candidate_it_was_offered(
    request_: OrientationRequest,
) -> None:
    """Structured output means it cannot answer in prose, but it can still
    answer with a rotation nobody offered. That is what the validator is for."""
    proposal = propose_orientation(request_, live())

    assert isinstance(proposal.value, Orientation)
    assert proposal.source.startswith("model:")
    assert proposal.rationale, "a gate has to show a human why"
    assert proposal.confidence is not None


@pytest.mark.llm
@needs_llm
def test_the_model_picks_the_orientation_with_no_overhang(
    request_: OrientationRequest,
) -> None:
    """The honest test of whether the model adds anything.

    The tee has one obviously correct answer and the model is given the numbers.
    If it cannot get this right, it is not ready to make this decision and the
    validator is carrying the whole node.
    """
    outcome = propose_validate(
        lambda r: propose_orientation(r, live()), validate_orientation, request_
    )
    assert outcome.accepted
    assert outcome.proposal.value.rotation_deg == (180.0, 0.0, 0.0)
    assert outcome.proposal.source.startswith("model:"), (
        "the corrector fixed it, so the model did not actually choose correctly"
    )


@pytest.mark.llm
@needs_llm
def test_the_model_is_not_just_echoing_the_first_candidate() -> None:
    """Re-order the list so the best answer is no longer first or last."""
    shuffled = (
        TEE_CANDIDATES[2],  # 100
        TEE_CANDIDATES[0],  # 300
        TEE_CANDIDATES[1],  # 0  <- correct, now in the middle
        TEE_CANDIDATES[3],  # 100
    )
    req = OrientationRequest(
        candidates=shuffled, build_volume_mm=BUILD_VOLUME, part_description=""
    )
    proposal = propose_orientation(req, live())
    assert proposal.value.rotation_deg == (180.0, 0.0, 0.0)


def test_the_config_is_read_from_the_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".env"
    settings.write_text("CREW_LLM_API_KEY=sk-test\n")
    cfg = LLMConfig.from_settings(settings)
    assert cfg.base_url == "http://127.0.0.1:4000/v1"
    assert cfg.model == "crew-code"
    assert cfg.api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(cfg)


def test_the_endpoint_and_alias_can_be_changed_in_the_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".env"
    settings.write_text(
        "CREW_LLM_API_KEY=sk-test\n"
        "PRINT_SHOP_LLM_URL=http://127.0.0.1:4001/v1\n"
        "PRINT_SHOP_LLM_MODEL=crew-local\n"
    )
    cfg = LLMConfig.from_settings(settings)
    assert (cfg.base_url, cfg.model) == ("http://127.0.0.1:4001/v1", "crew-local")


def test_no_key_is_refused_rather_than_falling_back_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CREW_LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CREW_LLM_API_KEY"):
        LLMConfig.from_settings(tmp_path / "absent.env")


# --------------------------------------------------------------------------
# When the model is unreachable — the second review finding
# --------------------------------------------------------------------------


DEAD_LLM = LLMConfig(api_key=SecretStr("unused"), base_url="http://127.0.0.1:9/v1", timeout_s=2.0)


def test_an_unreachable_model_raises_rather_than_guessing(
    request_: OrientationRequest,
) -> None:
    """`complete_json` must not invent a result when the endpoint is down."""
    with pytest.raises(RuntimeError):
        propose_orientation(request_, DEAD_LLM)


def test_the_geometric_fallback_picks_the_best_candidate(
    request_: OrientationRequest,
) -> None:
    """A deterministic proposer for when the Spark is off.

    The geometry is already measured, so the arg-min is a perfectly reasonable
    orientation. What must not happen is that it looks like a model decision.
    """
    from print_shop.expert.orientation import propose_geometric

    proposal = propose_geometric(request_)
    assert proposal.value.rotation_deg == (180.0, 0.0, 0.0)
    assert proposal.source == "geometric"
    assert "overhang" in proposal.rationale.lower()


def test_the_fallback_is_validated_like_any_other_proposal(
    request_: OrientationRequest,
) -> None:
    from print_shop.expert.orientation import propose_geometric

    outcome = propose_validate(propose_geometric, validate_orientation, request_)
    assert outcome.accepted
    assert outcome.proposal.source == "geometric"


def test_a_degenerate_part_still_gets_a_choice() -> None:
    """Six identical candidates: the fallback must pick one, not fail."""
    from print_shop.expert.orientation import propose_geometric

    same = tuple(
        Orientation(
            rotation_deg=r, overhang_area_mm2=0.0, bed_contact_area_mm2=400.0,
            height_mm=20.0, bbox_mm=(20.0, 20.0, 20.0),
        )
        for r in ((0.0, 0.0, 0.0), (180.0, 0.0, 0.0), (90.0, 0.0, 0.0))
    )
    req = OrientationRequest(
        candidates=same, build_volume_mm=BUILD_VOLUME, part_description=""
    )
    assert propose_geometric(req).value.rotation_deg in {r.rotation_deg for r in same}
