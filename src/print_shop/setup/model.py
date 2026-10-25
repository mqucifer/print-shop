"""The model reads the part through the print-settings skill and proposes its settings.

mqucifer/print-shop#7, as the Sponsor shaped it on 2026-10-25: every setting
depends on the part, so the model reasons per part, from its measured
features (`features.py`) and its intake. What each setting does and when to
change it is the print-settings skill's (`skill.py`, `skills/print-settings/`):
the model is given SKILL.md and only the reference groups the part touches,
and may change only those groups' settings. It's asked once per proposal,
through LiteLLM :4000 (`expert.client`).

Every change must name the feature, or the purpose, behind it, and the
skill group it relies on. Each value is checked against its setting's bound
(`bounds.check`), set by the presets or a source; one outside it is
refused, saying why, and never clamped. A failed call is a proposal without
the model's part, saying so: the guards still hold, and the Sponsor reviews.
A reply cut off at the output limit is one such failure, never retried: its
finish reason and tokens go in the proposal, and so in the ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from print_shop.expert.client import CutOff, LLMConfig, complete_json_call
from print_shop.setup.bounds import SETTINGS, check
from print_shop.setup.proposal import Change, PartFacts, Proposal, Source
from print_shop.setup.skill import Skill

__all__ = ["Ask", "llm_ask", "propose_by_model", "schema", "setup_llm"]

#: Settings keys: the alias the proposal reasons with, and its output limit,
#: thinking included (the operator, 2026-10-25: a setting, not a constant).
MODEL_KEY = "PRINT_SHOP_SETUP_MODEL"
MAX_TOKENS_KEY = "PRINT_SHOP_SETUP_MAX_TOKENS"

#: The Sponsor expects these judgements to reason, so the thinking alias.
DEFAULT_MODEL = "crew-code-think"
#: Room for the reasoning and the answer. The Sponsor's standing rule for a
#: new limit: start high, about 3x the most seen, record every signal, and
#: tune it down from evidence. The most seen on 2026-10-25 was 5,313 tokens,
#: and two replies were cut off at 8,000. At the 27.5 tokens a second
#: measured on the Spark (2026-10-08), 16,000 is about ten minutes at most.
DEFAULT_MAX_TOKENS = 16000

#: How the model is called: `complete_json`, or a test's recorded reply.
Ask = Callable[[str, str, dict[str, Any]], dict[str, Any]]

_SYSTEM = (
    "You set up FDM prints in OrcaSlicer for a person who has tuned their own presets. "
    "Follow the print-settings skill below: read the part from its features and its "
    "intake, change a preset only where this part gives a reason, name the feature or "
    "purpose behind each change, cite the skill's reference group by its id, and stay "
    "inside each setting's bound.\n\n"
)


#: A value is a number (a density may end in %) or one of a setting's choices.
#: Without it, crew-code-think wrote placeholders such as "reasoning_continue"
#: for 4 of its 8 values on 2026-10-25; the bounds refused them all.
_VALUE = "^(" + "|".join(
    [r"[0-9]+(\.[0-9]+)?%?"]
    + sorted({c.replace("(", r"\(").replace(")", r"\)") for s in SETTINGS.values()
              for c in s.choices if not c.isdigit()})
) + ")$"


def schema(feature_ids: list[str], skill: Skill) -> dict[str, Any]:
    """The reply's shape, enforced by the server: the loaded groups' settings, cited."""
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
                            "key": {"type": "string", "enum": list(skill.settings)},
                            "value": {"type": "string", "pattern": _VALUE},
                            "feature": {"type": "string", "enum": [*feature_ids, "purpose"]},
                            "knowledge": {"type": "string", "enum": list(skill.ids)},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "design_notes": {"type": "array", "items": {"type": "string"}},
            },
        },
    }


def propose_by_model(facts: PartFacts, skill: Skill, ask: Ask, model: str) -> Proposal:
    """The model's reading of the part and its bounded changes; refusals recorded."""
    read = Proposal(skill_groups=skill.ids, skill_sha256=skill.sha256)
    try:
        reply = ask(
            _SYSTEM + skill.text,
            prompt(facts, skill),
            schema([f.id for f in facts.features], skill),
        )
        suggestions = list(reply["changes"])
        reasoning = str(reply.get("reasoning", ""))
        design = tuple(str(n) for n in reply.get("design_notes", ()))
    except CutOff as exc:
        # Recorded, never retried: every cut-off is a signal for tuning the limit.
        return read.model_copy(update={"model": f"cut off: {exc}", "call": exc.info.model_dump()})
    except (RuntimeError, OSError, KeyError, TypeError) as exc:
        return read.model_copy(update={"model": f"unavailable: {exc}"[:600]})

    changes: list[Change] = []
    kept: list[Change] = []
    refused: list[str] = []
    for s in suggestions:
        key, value = str(s.get("key")), str(s.get("value")).strip()
        if key == "sparse_infill_density" and value and not value.endswith("%"):
            value += "%"  # as Orca writes it; crew-code wrote "50" on 2026-10-25
        why = check(key, value, facts.presets)
        if key not in skill.settings:
            why = "its skill group wasn't read for this part"
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
    return read.model_copy(update={
        "changes": tuple(changes), "kept": tuple(kept), "reasoning": reasoning,
        "design_notes": design, "refused": tuple(refused), "model": model,
        "call": dict(reply.get("_call") or {}),
    })


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


def prompt(facts: PartFacts, skill: Skill) -> str:
    """What the model is told about this part: the intake, features, presets and bounds."""
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
    for key in skill.settings:
        setting = SETTINGS[key]
        allowed = (", ".join(setting.choices) if setting.choices
                   else "{:g} to {:g}".format(*setting.bound(facts.presets)))  # type: ignore[misc]
        lines.append(f"- {key}: preset {facts.preset(key) or '(unset)'}; allowed {allowed}")
    lines += ["", f"Skill groups read for this part: {', '.join(skill.ids)}."]
    return "\n".join(lines)


def _same(value: str, preset: str) -> bool:
    try:
        return float(value.rstrip("%")) == float(preset.rstrip("%"))
    except ValueError:
        return value == preset
