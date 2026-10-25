"""The propose/validate loop and the gate invariant.

These are the tests that make `AGENTS.md` rules 3, 4 and 5 enforceable rather
than aspirational.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from print_shop.core.contracts import (
    Bounds,
    EscalationReason,
    Gate,
    GateDecision,
    NoAutoFix,
    Proposal,
    Severity,
    Verdict,
    Violation,
    approved_at,
    propose_validate,
    ungated_edges,
)


class Nested(BaseModel):
    depth: float = 0.2


class Config(BaseModel):
    temp: float = 200.0
    material: str = "PLA"
    supports: Nested = Nested()


def _propose(
    value: Config, confidence: float | None = None
) -> Callable[[object], Proposal[Config]]:
    def propose(_request: object) -> Proposal[Config]:
        return Proposal(value=value, source="test", confidence=confidence)

    return propose


def _rejects(*violations: Violation) -> Callable[[Proposal[Config], object], Verdict]:
    """Validator that rejects until the named fields are fixed."""

    def validate(proposal: Proposal[Config], _request: object) -> Verdict:
        remaining = tuple(
            v for v in violations if getattr(proposal.value, v.field.split(".")[0]) == v.observed
        )
        return Verdict(remaining)

    return validate


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def test_accepts_a_valid_proposal_without_correcting() -> None:
    outcome = propose_validate(
        _propose(Config()), lambda p, r: Verdict(), request=None
    )
    assert outcome.accepted
    assert outcome.attempts == 1
    assert outcome.proposal.source == "test"


def test_applies_the_validators_suggested_value() -> None:
    """The validator knows the domain, so it supplies the fix as data."""
    outcome = propose_validate(
        _propose(Config(temp=400.0)),
        _rejects(
            Violation(
                field="temp",
                rule="temp.range",
                observed=400.0,
                allowed=Bounds(150, 320),
                suggested=215.0,
                detail="400C exceeds PLA limits",  # never parsed
            )
        ),
        request=None,
    )
    assert outcome.accepted
    assert outcome.proposal.value.temp == 215.0
    assert outcome.proposal.source == "corrector"


def test_clamps_to_bounds_when_no_value_is_suggested() -> None:
    outcome = propose_validate(
        _propose(Config(temp=400.0)),
        _rejects(
            Violation(field="temp", rule="temp.range", observed=400.0, allowed=Bounds(150, 320))
        ),
        request=None,
    )
    assert outcome.accepted
    assert outcome.proposal.value.temp == 320.0


def test_corrects_a_nested_field_by_dotted_path() -> None:
    outcome = propose_validate(
        _propose(Config(supports=Nested(depth=9.0))),
        lambda p, r: Verdict(
            ()
            if p.value.supports.depth <= 1.0
            else (
                Violation(
                    field="supports.depth",
                    rule="supports.depth.range",
                    observed=9.0,
                    allowed=Bounds(0.0, 1.0),
                ),
            )
        ),
        request=None,
    )
    assert outcome.accepted
    assert outcome.proposal.value.supports.depth == 1.0


def test_escalates_immediately_on_an_unfixable_violation() -> None:
    """Retries cannot repair a wrong-in-kind proposal, so do not spend them."""
    outcome = propose_validate(
        _propose(Config()),
        lambda p, r: Verdict(
            (
                Violation(
                    field="material",
                    rule="mesh.non_manifold",
                    observed="PLA",
                    allowed=NoAutoFix("mesh is not watertight"),
                ),
            )
        ),
        request=None,
        max_attempts=3,
    )
    assert not outcome.accepted
    assert outcome.escalation is EscalationReason.UNFIXABLE
    assert outcome.attempts == 1


def test_escalates_when_attempts_are_exhausted() -> None:
    def never_satisfied(p: Proposal[Config], r: object) -> Verdict:
        return Verdict(
            (
                Violation(
                    field="temp",
                    rule="temp.range",
                    observed=p.value.temp,
                    allowed=Bounds(150, 320),
                    suggested=999.0,  # corrector applies it; validator still objects
                ),
            )
        )

    outcome = propose_validate(
        _propose(Config()), never_satisfied, request=None, max_attempts=3
    )
    assert not outcome.accepted
    assert outcome.escalation is EscalationReason.EXHAUSTED
    assert outcome.attempts == 3
    assert len(outcome.history) == 3


def test_escalates_on_low_confidence_before_validating() -> None:
    outcome = propose_validate(
        _propose(Config(), confidence=0.2),
        lambda p, r: Verdict(),
        request=None,
        min_confidence=0.6,
    )
    assert not outcome.accepted
    assert outcome.escalation is EscalationReason.LOW_CONFIDENCE


def test_warnings_do_not_block_acceptance() -> None:
    outcome = propose_validate(
        _propose(Config()),
        lambda p, r: Verdict(
            (
                Violation(
                    field="temp",
                    rule="temp.conservative",
                    observed=200.0,
                    allowed=Bounds(150, 320),
                    severity=Severity.WARNING,
                ),
            )
        ),
        request=None,
    )
    assert outcome.accepted
    assert len(outcome.outstanding) == 1


# --------------------------------------------------------------------------
# The gate invariant (AGENTS.md rule 5)
# --------------------------------------------------------------------------


def test_detects_a_path_to_hardware_that_skips_a_gate() -> None:
    """This is the shape of the original bug: failure re-sliced and reprinted."""
    edges = [
        ("slice", Gate.BEFORE_PRINT.value),
        (Gate.BEFORE_PRINT.value, "print"),
        ("watch", "learn"),
        ("learn", "print"),  # unattended retry after a failed print
    ]
    assert ungated_edges(edges) == [("learn", "print")]


def test_accepts_a_graph_where_every_hardware_edge_is_gated() -> None:
    edges = [
        ("slice", Gate.BEFORE_PRINT.value),
        (Gate.BEFORE_PRINT.value, "print"),
        ("watch", Gate.AFTER_FAILURE.value),
        (Gate.AFTER_FAILURE.value, "print"),
    ]
    assert ungated_edges(edges) == []


# --------------------------------------------------------------------------
# approved_at — consent belongs to the gate that asked
# --------------------------------------------------------------------------


def _decision(gate: Gate, approved: bool) -> GateDecision:
    return GateDecision(gate=gate, approved=approved, at="2026-09-16T00:00:00Z")


def test_an_approval_at_this_gate_is_consent() -> None:
    decisions = (_decision(Gate.BEFORE_PRINT, True),)
    assert approved_at(decisions, Gate.BEFORE_PRINT)


def test_an_approval_at_another_gate_is_not_consent() -> None:
    """The bug d6374b9 fixed, one node further on.

    A human approving "yes, slice this non-manifold mesh anyway" at
    `review_proposal` has not said "yes, print it". A check that reads only
    `.approved` cannot tell those apart.
    """
    decisions = (_decision(Gate.REVIEW_PROPOSAL, True),)
    assert not approved_at(decisions, Gate.BEFORE_PRINT)


def test_a_rejection_is_not_consent() -> None:
    assert not approved_at((_decision(Gate.BEFORE_PRINT, False),), Gate.BEFORE_PRINT)


def test_no_decision_at_all_is_not_consent() -> None:
    """The default must be refusal — silence is never approval."""
    assert not approved_at((), Gate.BEFORE_PRINT)


def test_only_the_most_recent_decision_counts() -> None:
    """An approval that was superseded is history, not permission."""
    decisions = (
        _decision(Gate.BEFORE_PRINT, True),
        _decision(Gate.BEFORE_PRINT, False),
    )
    assert not approved_at(decisions, Gate.BEFORE_PRINT)


def test_an_earlier_approval_cannot_be_reached_past_a_later_foreign_decision() -> None:
    """Approve the print, then answer a different gate: the print consent is spent."""
    decisions = (
        _decision(Gate.BEFORE_PRINT, True),
        _decision(Gate.REVIEW_PROPOSAL, True),
    )
    assert not approved_at(decisions, Gate.BEFORE_PRINT)
