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
finish reason and tokens go in the proposal, and so in the ledger. It's streamed
with the questions' cuts (mqucifer/print-shop#242), and a timeout says so.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from print_shop.expert.client import CutOff, LLMConfig, TimedOut, stream_json_call
from print_shop.setup.bounds import SETTINGS, check
from print_shop.setup.context import Seen
from print_shop.setup.proposal import Change, PartFacts, Proposal, Source
from print_shop.setup.reading import carried
from print_shop.setup.skill import Skill

__all__ = ["SETTINGS_ANSWER_CHARS", "Ask", "context_lines", "llm_ask", "llm_ask_streamed",
           "propose_by_model", "schema", "setup_llm"]

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

#: What a model is told where only part of the walls is seen (mqucifer/print-shop#248).
LEAVE_THE_SEAM = ("leave seam_position as it is there: where a seam would land against the wall "
                  "that's seen can't be told")
#: The most characters a settings answer may be (mqucifer/print-shop#242). One at every cap of
#: its schema is about 11,000; the largest recorded answer was 3,741.
SETTINGS_ANSWER_CHARS = 16_000

#: How the model is called: `complete_json`, or a test's recorded reply.
Ask = Callable[[str, str, dict[str, Any]], dict[str, Any]]

_SYSTEM = (
    "You set up FDM prints in OrcaSlicer for a person who has tuned their own presets. "
    "Follow the print-settings skill below: read the part from its features and its "
    "intake, change a preset only where this part gives a reason, name the feature or "
    "purpose behind each change, cite the skill's reference group by its id, and stay "
    "inside each setting's bound.\n\n"
)


#: Each setting's value has its own shape, so the server can't accept a value
#: that isn't one. With one pattern for every value, crew-code-think wrote
#: placeholders ("reasoning_continue") and a layer height ("0.20") as a wall
#: count and as enable_support, on 2026-10-25; the bounds refused them all,
#: and a question whose options were all refused was lost.
_SHAPES = {
    "layer_height": r"^0\.[0-9]{1,3}$",
    "sparse_infill_density": r"^[0-9]{1,3}%$",
}
_WHOLE = r"^[0-9]{1,3}$"

#: Free text is capped, so an answer can't run on to the output limit: the
#: vase's questions ran 13,500 tokens past their reasoning and were cut off.
TEXT = {"type": "string", "maxLength": 400}
SHORT = {"type": "string", "maxLength": 120}


def setting_item(keys: list[str], *extra: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    """A `{key, value, ...}` object whose value is shaped for its key."""
    shapes = []
    for key in keys:
        choices = SETTINGS[key].choices
        value = ({"type": "string", "enum": list(choices)} if choices
                 else {"type": "string", "pattern": _SHAPES.get(key, _WHOLE)})
        props = {"key": {"type": "string", "enum": [key]}, "value": value, **dict(extra)}
        shapes.append({"type": "object", "additionalProperties": False,
                       "required": list(props), "properties": props})
    return {"anyOf": shapes}


def schema(feature_ids: list[str], skill: Skill) -> dict[str, Any]:
    """The reply's shape, enforced by the server: the loaded groups' settings, cited."""
    return {
        "name": "print_setup",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["reasoning", "changes", "design_notes"],
            "properties": {
                "reasoning": {"type": "string", "maxLength": 2000},
                "changes": {
                    "type": "array",
                    "maxItems": 12,
                    "items": setting_item(
                        list(skill.settings),
                        ("feature", {"type": "string", "enum": [*feature_ids, "purpose"]}),
                        ("knowledge", {"type": "string", "enum": list(skill.ids)}),
                        ("reason", TEXT),
                    ),
                },
                "design_notes": {"type": "array", "maxItems": 4, "items": TEXT},
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
    except TimedOut as exc:
        return read.model_copy(update={
            "model": f"timed out: {exc}",
            "call": {"finish_reason": "timed out", "seconds": exc.seconds,
                     "timeout_s": exc.limit_s},
        })
    except (RuntimeError, OSError, KeyError, TypeError) as exc:
        return read.model_copy(update={"model": f"unavailable: {exc}"[:600]})

    answered_keys = {k for _, o in facts.intake.chosen() for k in o.settings}
    by_lesson = {g.key: g.id for g in facts.lessons if g.applied}
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
        if key in answered_keys:
            why = "you answered it at the intake"
        if key in by_lesson:
            why = f"lesson {by_lesson[key]} set it"
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


def llm_ask(config: LLMConfig, stop: threading.Event | None = None) -> Ask:
    """The real call: one structured completion through LiteLLM (`setup_llm`), streamed so that
    a runaway is cut and `stop` ends it (mqucifer/print-shop#242).

    The reply carries `_call`: its finish reason and tokens, reasoning
    included, so a proposal records what its judgement cost and whether it
    was cut off.
    """

    return llm_ask_streamed(config, SETTINGS_ANSWER_CHARS, stop)


def llm_ask_streamed(
    config: LLMConfig, answer_chars: int, stop: threading.Event | None = None
) -> Ask:
    """`llm_ask`, streamed: its answer bounded, and ended when `stop` is set
    (mqucifer/print-shop#208)."""

    def ask(system: str, user: str, shape: dict[str, Any]) -> dict[str, Any]:
        reply, info = stream_json_call(
            config, system, user, shape, answer_chars=answer_chars, stop=stop
        )
        return {**reply, "_call": info.model_dump()}

    return ask


def prompt(facts: PartFacts, skill: Skill) -> str:
    """What the model is told about this part: the intake, features, presets and bounds."""
    lines = [
        "The intake, asked of the person from the part's features, and their answers "
        "(what they chose is already set; don't change it):",
        *(f"- {line}" for line in _answers(facts)),
        f"Filament: {facts.filament.get('name')} ({facts.filament_type}). "
        f"Process: {facts.process.get('name')}.",
        "",
        "Its features, as it will sit on the plate:",
        *(f"- {f.line()}" for f in facts.features),
        "",
        *_seen(facts),
        *_lessons(facts),
        *context_lines(facts),
        "Settings you may change: the preset's value, and the bound a value must stay in:",
    ]
    for key in skill.settings:
        setting = SETTINGS[key]
        allowed = (", ".join(setting.choices) if setting.choices
                   else "{:g} to {:g}".format(*setting.bound(facts.presets)))  # type: ignore[misc]
        lines.append(f"- {key}: preset {facts.preset(key) or '(unset)'}; allowed {allowed}")
    lines += ["", f"Skill groups read for this part: {', '.join(skill.ids)}."]
    return "\n".join(lines)


def _seen(facts: PartFacts) -> list[str]:
    """What's seen in the pose the features are read in: an answer, or the notes', carried
    there (mqucifer/print-shop#248)."""
    intake = facts.intake
    if facts.seen is not None:
        now: Seen | None = facts.seen  # a plate's, carried part by part (mqucifer/print-shop#109)
    elif facts.pose is None:
        return []
    else:
        now = carried(intake, facts.context, facts.pose)
    if now is None:
        answered = any(q.feature == "seen" and q.pose is not None for q in intake.questions)
        return (["Which faces are seen as it lies here isn't known: judge none as seen or "
                 "hidden.", ""] if answered else [])
    words = now.unsaid_words()
    unsaid = f" {words[:1].upper()}{words[1:]}: don't judge that face hidden." if words else ""
    seam = f" Only part of the walls is seen, so {LEAVE_THE_SEAM}." if now.part_walls else ""
    return [f"Seen as it lies here, carried from what was said: {now.as_printed()}. Go by this "
            f"for which faces are seen.{unsaid}{seam}", ""]


def _lessons(facts: PartFacts) -> list[str]:
    """The reviewed lessons this part was given, if any (mqucifer/print-shop#106)."""
    given = [g for g in facts.lessons if not g.withheld]  # one for another filament isn't given
    if not given:
        return []
    return [
        "Lessons from earlier prints, each kept by a reviewer, for parts with traits this one "
        "has (what a lesson set is already set; don't change it):",
        *(f"- {g.line()}" + ("" if g.applied or not g.key else f" (not set: {g.why})")
          for g in given),
        "",
    ]


def context_lines(facts: PartFacts) -> list[str]:
    """The part's context from its design run or a sidecar (mqucifer/print-shop#130), if any."""
    if facts.context is None or not facts.context.found:
        return []
    return [
        "The part's context, as the file lies (a fit is two parts meant to mate: never a random "
        "seam on a part with one, and a fit at the plate wants elephant foot compensation):",
        *(f"- {line}" for line in facts.context.lines(facts.intake.set_aside())),
        "",
    ]


def _same(value: str, preset: str) -> bool:
    try:
        return float(value.rstrip("%")) == float(preset.rstrip("%"))
    except ValueError:
        return value == preset


def _answers(facts: PartFacts) -> list[str]:
    if not facts.intake.questions:
        return ["no questions: the part's features left nothing to ask"]
    lines = []
    for q, a in zip(facts.intake.questions, facts.intake.answers, strict=True):
        if a.choice is not None:
            o = q.options[a.choice]
            sets = ", ".join(f"{k}={v}" for k, v in o.settings.items()) or "no setting"
            said = f"{q.asked()} -> {q.as_answered(a)} ({sets})"
        else:
            said = f"{q.asked()} -> in their words: {a.other}"
        whose = q.whose(a)
        lines.append(said + (f" [{whose}]" if whose else ""))
    return lines
