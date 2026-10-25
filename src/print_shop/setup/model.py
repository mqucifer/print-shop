"""The model reads the part and proposes its settings, inside the knowledge's bounds.

mqucifer/print-shop#7, as the Sponsor reshaped it on 2026-10-25: every setting
depends on the part, so the model reasons per part, from its measured
features (`features.py`) and its intake, citing the sourced knowledge
(`knowledge.py`). It's asked once per proposal, through LiteLLM :4000
(`expert.client`).

Every change must name the feature, or the purpose, behind it, and the
knowledge it relies on. Each value is checked against its setting's bound
(`knowledge.check`), set by the presets or a source; one outside it is
refused, saying why, and never clamped. A failed call is a proposal without
the model's part, saying so: the guards still hold, and the Sponsor reviews.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from print_shop.expert.client import LLMConfig, complete_json_call
from print_shop.setup.knowledge import KNOWLEDGE, SETTINGS, check
from print_shop.setup.proposal import Change, PartFacts, Proposal, Source

__all__ = ["Ask", "llm_ask", "propose_by_model", "schema", "setup_llm"]

#: Settings keys: the alias the proposal reasons with, and its output limit,
#: thinking included (the operator, 2026-10-25: a setting, not a constant).
MODEL_KEY = "PRINT_SHOP_SETUP_MODEL"
MAX_TOKENS_KEY = "PRINT_SHOP_SETUP_MAX_TOKENS"

#: The Sponsor expects these judgements to reason, so the thinking alias.
DEFAULT_MODEL = "crew-code-think"
#: Room for the reasoning and the answer: at the 27.5 tokens a second
#: measured on the Spark (2026-10-08), about five minutes at most.
DEFAULT_MAX_TOKENS = 8000

#: How the model is called: `complete_json`, or a test's recorded reply.
Ask = Callable[[str, str, dict[str, Any]], dict[str, Any]]

_SYSTEM = (
    "You set up FDM prints in OrcaSlicer for a person who prints many different parts and "
    "tunes every setting to the part's design and purpose. Read the part from its measured "
    "features and its intake, then say how you read it: what it is, what will make it "
    "succeed or fail. Propose changes from the person's presets only where this part gives "
    "a reason; keeping a preset value is a good answer. Each change names the feature (by "
    "its id) or 'purpose' that drives it, cites one knowledge entry by id, and gives a "
    "reason that points at the part: 'the 3 mm hook carries the load, so 4 walls', not "
    "'load-bearing, so 4 walls'. Stay inside each setting's bound. Supports, when needed, "
    "are tree. Where a change to the design would serve better than a setting, say so in "
    "design_notes. Values are plain, as Orca writes them: numbers without units, infill "
    "density with %."
)


#: A value is a number (a density may end in %) or one of a setting's choices.
#: Without it, crew-code-think wrote placeholders such as "reasoning_continue"
#: for 4 of its 8 values on 2026-10-25; the bounds refused them all.
_VALUE = "^(" + "|".join(
    [r"[0-9]+(\.[0-9]+)?%?"]
    + sorted({c.replace("(", r"\(").replace(")", r"\)") for s in SETTINGS.values()
              for c in s.choices if not c.isdigit()})
) + ")$"


def schema(feature_ids: list[str]) -> dict[str, Any]:
    """The reply's shape, enforced by the server: changes cite known features and knowledge."""
    return {
        "name": "print_setup",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["reasoning", "changes", "design_notes"],
            "properties": {
                "reasoning": {"type": "string"},
                "changes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["key", "value", "feature", "knowledge", "reason"],
                        "properties": {
                            "key": {"type": "string", "enum": sorted(SETTINGS)},
                            "value": {"type": "string", "pattern": _VALUE},
                            "feature": {"type": "string", "enum": [*feature_ids, "purpose"]},
                            "knowledge": {"type": "string", "enum": [k.id for k in KNOWLEDGE]},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "design_notes": {"type": "array", "items": {"type": "string"}},
            },
        },
    }


def propose_by_model(facts: PartFacts, ask: Ask, model: str) -> Proposal:
    """The model's reading of the part and its bounded changes; refusals recorded."""
    try:
        reply = ask(_SYSTEM, prompt(facts), schema([f.id for f in facts.features]))
        suggestions = list(reply["changes"])
        reasoning = str(reply.get("reasoning", ""))
        design = tuple(str(n) for n in reply.get("design_notes", ()))
    except (RuntimeError, OSError, KeyError, TypeError) as exc:
        return Proposal(model=f"unavailable: {exc}"[:600])

    changes: list[Change] = []
    kept: list[Change] = []
    refused: list[str] = []
    for s in suggestions:
        key, value = str(s.get("key")), str(s.get("value")).strip()
        why = check(key, value, facts.presets)
        if key in {c.key for c in changes + kept}:
            why = "proposed twice"
        if why:
            refused.append(f"{key}={value}: {why}")
            continue
        preset = facts.preset(key)
        change = Change(
            key=key, preset=preset, value=value, reason=str(s.get("reason", "")),
            source=Source.MODEL, feature=str(s.get("feature", "")),
            knowledge=str(s.get("knowledge", "")),
        )
        (kept if _same(value, preset) else changes).append(change)
    return Proposal(changes=tuple(changes), kept=tuple(kept), reasoning=reasoning,
                    design_notes=design, refused=tuple(refused), model=model,
                    call=dict(reply.get("_call") or {}))


def setup_llm(base: LLMConfig, env: dict[str, str]) -> LLMConfig:
    """The proposal's model: the orientation's endpoint and key, its own alias and limit.

    Raises:
        ValueError: If the output limit in the settings isn't a number.
    """
    raw = env.get(MAX_TOKENS_KEY) or str(DEFAULT_MAX_TOKENS)
    try:
        max_tokens = int(raw)
    except ValueError:
        raise ValueError(f"{MAX_TOKENS_KEY}={raw!r} is not a number of tokens") from None
    return base.model_copy(update={
        "model": env.get(MODEL_KEY) or DEFAULT_MODEL,
        "max_tokens": max_tokens,
        # A thinking answer runs at about 27.5 tokens a second.
        "timeout_s": max(base.timeout_s, max_tokens / 20),
    })


def llm_ask(config: LLMConfig) -> Ask:
    """The real call: one structured completion through LiteLLM (`setup_llm`).

    The reply carries `_call`: its finish reason and tokens, reasoning
    included, so a proposal records what its judgement cost and whether it
    was cut off.
    """

    def ask(system: str, user: str, shape: dict[str, Any]) -> dict[str, Any]:
        reply, info = complete_json_call(config, system, user, shape)
        return {**reply, "_call": info.model_dump()}

    return ask


def prompt(facts: PartFacts) -> str:
    """What the model is told: the part, the presets with bounds, and the knowledge."""
    lines = [
        f"The part's intake: {facts.intake.summary()}.",
        f"Filament: {facts.filament.get('name')} ({facts.filament_type}). "
        f"Process: {facts.process.get('name')}.",
        "",
        "Its features, as it will sit on the plate:",
        *(f"- {f.line()}" for f in facts.features),
        "",
        "Settings you may change: the preset's value, and the bound a value must stay in:",
    ]
    for key, setting in SETTINGS.items():
        allowed = (", ".join(setting.choices) if setting.choices
                   else "{:g} to {:g}".format(*setting.bound(facts.presets)))  # type: ignore[misc]
        lines.append(f"- {key}: preset {facts.preset(key) or '(unset)'}; allowed {allowed}")
    lines += ["", "Knowledge to cite:"]
    lines += [f"- {k.id} ({', '.join(k.settings)}): {k.effect} [{k.source}]" for k in KNOWLEDGE]
    return "\n".join(lines)


def _same(value: str, preset: str) -> bool:
    try:
        return float(value.rstrip("%")) == float(preset.rstrip("%"))
    except ValueError:
        return value == preset
