"""Contracts between the parts of the pipeline.

Every structured decision that crosses a boundary uses these types. The rule
that shapes all of them (`AGENTS.md` rule 4):

    Components exchange typed objects, never prose.

The previous implementation had a validator emit English ("Set nozzle temp to
200-250C") and a corrector substring-match it::

    if "nozzle temp" in suggestion.lower():
        fixed_config["nozzle_temp"] = 220      # regardless of material

Here a validator emits ``Violation(field="nozzle_temp", suggested=215, ...)``
and the corrector assigns it. Rationale and detail strings are retained for
humans and logs and are **never** read to make a decision.

Proposal payloads are Pydantic models because two things need it: the
LangGraph checkpointer has to serialise them across a multi-hour print, and
the corrector has to update fields by name.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

__all__ = [
    "Bounds",
    "Corrector",
    "EscalationReason",
    "Gate",
    "GateDecision",
    "NoAutoFix",
    "OneOf",
    "Outcome",
    "Proposal",
    "Proposer",
    "Severity",
    "Validator",
    "Violation",
    "approved_at",
    "default_corrector",
    "propose_validate",
]


# --------------------------------------------------------------------------
# What a validator is allowed to say
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bounds:
    """A numeric field must lie in [lo, hi]. Auto-fixable by clamping."""

    lo: float
    hi: float


@dataclass(frozen=True, slots=True)
class OneOf:
    """A field must be one of these values. Auto-fixable only via `suggested`."""

    options: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class NoAutoFix:
    """Nothing the corrector can do. Escalates to a human immediately.

    Use this for anything a retry cannot repair — a non-manifold mesh, a part
    that does not fit the build volume, a proposal that is wrong in kind
    rather than in degree. Burning three retries on an unfixable proposal
    just delays the gate.
    """

    reason: str


type Allowed = Bounds | OneOf | NoAutoFix


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Violation:
    """One thing wrong with a proposal, in machine-readable form.

    `field` is a dotted path into the proposal payload ("supports.z_distance").
    `suggested` is the validator's concrete replacement value — the validator
    has the domain knowledge, so it proposes the fix as *data*. `detail` is for
    humans and must never be parsed.
    """

    field: str
    rule: str  # stable id, e.g. "nozzle_temp.range" — safe to match on
    observed: Any
    allowed: Allowed
    severity: Severity = Severity.BLOCKER
    suggested: Any = None
    detail: str = ""

    @property
    def auto_fixable(self) -> bool:
        if isinstance(self.allowed, NoAutoFix):
            return False
        return self.suggested is not None or isinstance(self.allowed, Bounds)


@dataclass(frozen=True, slots=True)
class Verdict:
    """A validator's answer. Empty violations means accepted."""

    violations: tuple[Violation, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.blockers

    @property
    def blockers(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.BLOCKER)

    @property
    def fixable(self) -> bool:
        """True when every blocker can be repaired without a human."""
        return bool(self.blockers) and all(v.auto_fixable for v in self.blockers)


# --------------------------------------------------------------------------
# What a proposer produces
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal[T: BaseModel]:
    """A value plus where it came from.

    `rationale` and `confidence` are recorded for humans, gates and later
    evaluation. Control flow reads neither — a confident model is not a
    validated one.
    """

    value: T
    source: str  # "model:qwen3.8-27b" | "default" | "corrector"
    rationale: str = ""
    confidence: float | None = None


class EscalationReason(StrEnum):
    UNFIXABLE = "unfixable"  # a NoAutoFix violation
    EXHAUSTED = "exhausted"  # max_attempts spent, still rejected
    LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True, slots=True)
class Outcome[T: BaseModel]:
    """The result of one propose/validate loop.

    `accepted` is the only field control flow may branch on. When it is False,
    `escalation` says why and the caller must route to a gate — never onward.
    """

    proposal: Proposal[T]
    accepted: bool
    attempts: int
    history: tuple[Verdict, ...] = ()
    escalation: EscalationReason | None = None

    @property
    def outstanding(self) -> tuple[Violation, ...]:
        return self.history[-1].violations if self.history else ()


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


class Proposer[T: BaseModel, R](Protocol):
    def __call__(self, request: R, /) -> Proposal[T]: ...


class Validator[T: BaseModel, R](Protocol):
    def __call__(self, proposal: Proposal[T], request: R, /) -> Verdict: ...


class Corrector[T: BaseModel, R](Protocol):
    def __call__(self, proposal: Proposal[T], verdict: Verdict, request: R, /) -> Proposal[T]: ...


def _set_path(model: BaseModel, path: str, value: Any) -> BaseModel:
    """Return a copy of `model` with the dotted `path` set to `value`."""
    head, _, rest = path.partition(".")
    if not rest:
        return model.model_copy(update={head: value})
    child = getattr(model, head)
    if not isinstance(child, BaseModel):
        raise TypeError(f"cannot descend into {head!r} ({type(child).__name__}) for path {path!r}")
    return model.model_copy(update={head: _set_path(child, rest, value)})


def default_corrector[T: BaseModel, R](
    proposal: Proposal[T], verdict: Verdict, request: R
) -> Proposal[T]:
    """Apply every auto-fixable violation: `suggested` if given, else clamp.

    Deliberately boring. A corrector that reasons is a second proposer, and
    then nothing is validating it.
    """
    value: BaseModel = proposal.value
    for v in verdict.blockers:
        if not v.auto_fixable:
            continue
        if v.suggested is not None:
            value = _set_path(value, v.field, v.suggested)
        elif isinstance(v.allowed, Bounds):
            clamped = min(max(float(v.observed), v.allowed.lo), v.allowed.hi)
            value = _set_path(value, v.field, clamped)
    return Proposal(value=value, source="corrector", rationale=proposal.rationale)  # type: ignore[arg-type]


def propose_validate[T: BaseModel, R](
    propose: Proposer[T, R],
    validate: Validator[T, R],
    request: R,
    correct: Corrector[T, R] = default_corrector,
    max_attempts: int = 3,
    min_confidence: float | None = None,
) -> Outcome[T]:
    """Propose, validate, correct, re-validate. Escalate rather than guess.

    A model proposes. A validator disposes. The loop never returns an
    unvalidated proposal as accepted — when it cannot converge it sets
    `escalation` and the caller routes to a gate.

    Escalates immediately (without spending retries) on any violation the
    corrector cannot repair.
    """
    proposal = propose(request)
    history: list[Verdict] = []

    if (
        min_confidence is not None
        and proposal.confidence is not None
        and proposal.confidence < min_confidence
    ):
        return Outcome(
            proposal=proposal,
            accepted=False,
            attempts=1,
            history=(),
            escalation=EscalationReason.LOW_CONFIDENCE,
        )

    for attempt in range(1, max_attempts + 1):
        verdict = validate(proposal, request)
        history.append(verdict)

        if verdict.accepted:
            return Outcome(proposal, True, attempt, tuple(history))

        if not verdict.fixable:
            return Outcome(
                proposal, False, attempt, tuple(history), EscalationReason.UNFIXABLE
            )

        if attempt == max_attempts:
            break

        proposal = correct(proposal, verdict, request)

    return Outcome(proposal, False, max_attempts, tuple(history), EscalationReason.EXHAUSTED)


# --------------------------------------------------------------------------
# Approval gates
#
# THE SEAM. Everything ABOVE this line is domain-agnostic — it knows nothing
# about printers, meshes or G-code, only that something proposes a Pydantic
# model and something else objects in machine-readable terms. Everything BELOW
# is specific to this project's hardware safety.
#
# That split is a candidate for extraction into a small reusable library; see
# BACKLOG.md. Do not extract it while this repo is the only consumer — a
# library with one user is a guess about the second one. Keep the seam clean so
# whoever does it later does not have to re-derive where it falls.
# --------------------------------------------------------------------------


class Gate(StrEnum):
    """Points where the pipeline stops for a human, via Home Assistant.

    `AGENTS.md` rule 5: approval gates are never routed around.
    """

    BEFORE_PRINT = "gate.before_print"
    AFTER_FAILURE = "gate.after_failure"
    REVIEW_PROPOSAL = "gate.review_proposal"


#: Nodes that command the printer. Every inbound edge must come from a Gate.
HARDWARE_NODES: frozenset[str] = frozenset({"print"})


@dataclass(frozen=True, slots=True)
class GateDecision:
    gate: Gate
    approved: bool
    at: str  # ISO-8601
    note: str = ""
    edits: dict[str, Any] = dc_field(default_factory=dict)


def approved_at(decisions: Sequence[GateDecision], gate: Gate) -> bool:
    """True when the most recent decision is an approval *made at* `gate`.

    Two things this checks that reading `.approved` alone does not:

    **The gate identity.** Consent given at one gate is not consent at another.
    Commit d6374b9 fixed two ways a decision could reach a gate it was not made
    for; a router or a node that reads only the boolean is the same bug one
    step further on.

    **Recency.** Only the last decision counts. An approval that has been
    superseded by a later rejection is history, not permission.
    """
    if not decisions:
        return False
    last = decisions[-1]
    return last.gate is gate and last.approved


def ungated_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return edges that reach a hardware node without passing a gate.

    The safety rule as a property you can test. A graph is acceptable only
    when this returns []. See `tests/test_contracts.py`.
    """
    gates = {g.value for g in Gate}
    return [(src, dst) for src, dst in edges if dst in HARDWARE_NODES and src not in gates]
