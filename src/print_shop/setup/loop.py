"""The print loop: one short conversation with tools decides a part's pose and its settings.

mqucifer/print-shop#129, planned with the Sponsor on 2026-11-14: "one loop, only ask on big
changes. it is asking good questions so far at the initial request". Orientation and the
settings were each one structured call, a table in and an answer out, with nothing to look up:
a flat lid was stood on its edge for warping, which the plate and a brim answer. So after the
intake's questions, which stay as they are, one loop through LiteLLM :4000 decides both, with:

- **`slice`**: Orca's slice of a candidate pose with some settings, read layer by layer
  (mqucifer/print-shop#105), before choosing;
- **`orca_setting`**: what a setting does, its Orca key, its bound here, and which setting
  answers which symptom (the print-settings skill's map, mqucifer/print-shop#128);
- **`past_prints`**: the ledger's earlier prints of parts with these traits, their outcomes, and
  the kept lessons for this part;
- **`ask`**: the person, in the terminal, **only before a big change** (`big`): a pose other
  than the file's own, supports on, a temperature or fan past what's backed, or more than
  `BIG_TIME_SHARE` added to the print. Code decides what's big, not the model: `ask` about a
  small change asks nobody, and a proposal holding a big change nobody agreed to is refused.
  A no is the person's correction, with its reason, and a lesson candidate like any;
- **`propose`**: the pose and the changes, which ends the loop. Its pose must be one of the
  candidates and pass `validate_orientation`; each value its bound. A refusal goes back to the
  model, which may choose again.

What it proposes goes through the same steps as the single call's reply (`propose_by_model`): the
intake's answers and the kept lessons set their settings first, every value is bounded, and the
guards hold. The review and the approval gate are unchanged. Every tool call, with its arguments,
its result and its time, is kept in the proposal (`Proposal.loop`), so the ledger has it.

The loop has a limit on calls and on time (`PrintLoop`), recorded with how it ended. Past
either, or if the model can't be reached, the job falls back to the single calls and says so.
**A call cut off at its output limit is a turn, not an ending:** the single settings call was
cut off at 16,000 tokens after 499 s on the Sponsor's lid, and none of its proposals were used
(mqucifer/print-shop#129). In the loop the cut-off is recorded, the model is told to answer with
one short tool call, and it counts against the loop's limits. Each tool's arguments are capped,
so an answer is a few hundred tokens, not thousands.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from print_shop.core.contracts import Proposal as Chosen
from print_shop.core.state import Orientation
from print_shop.expert.client import CutOff, LLMConfig, chat_with_tools
from print_shop.expert.orientation import OrientationRequest, symptom_map, validate_orientation
from print_shop.ingest.orientation import write_rotated
from print_shop.outcome_words import outcome_words
from print_shop.setup.bounds import SETTINGS, check
from print_shop.setup.context import refuses
from print_shop.setup.model import prompt
from print_shop.setup.proposal import (
    Change,
    GivenLesson,
    LoopCall,
    PartFacts,
    Proposal,
    Source,
    answered,
    apply,
    pose_value,
)
from print_shop.setup.reading import carried, read_slice
from print_shop.setup.skill import Skill
from print_shop.setup.traits import TRAITS, traits

if TYPE_CHECKING:  # the slicer imports the job state, which imports the setup
    from print_shop.ledger import Ledger
    from print_shop.slicer.orca import Slicer, SlicerProfiles

__all__ = [
    "BIG_TIME_SHARE",
    "MAX_CALLS",
    "MAX_SECONDS",
    "TOOLS",
    "LoopResult",
    "Part",
    "PrintLoop",
    "big",
    "cli_person",
    "llm_chat",
    "run",
]

#: The model's calls the loop makes at most, and the seconds it runs at most, before the job
#: falls back to the single calls. Started high, as the CAD loop's are, and recorded with the
#: proposal, to lower from what runs show.
MAX_CALLS = 16
MAX_SECONDS = 900.0
#: A proposal that adds more than this share to the print's time, against its slice in the
#: file's own pose with the intake's answers, is a big change: asked first.
BIG_TIME_SHARE = 0.25
#: A temperature or fan this far past what's backed is a big change: the value a kept lesson for
#: this part sets, or the preset's. A warmer plate by 5 C, or a fan by 20 points, isn't.
TEMPERATURE_MARGIN_C = 5.0
FAN_MARGIN = 20.0
TEMPERATURES = frozenset({
    "nozzle_temperature", "textured_plate_temp", "textured_plate_temp_initial_layer",
    "cool_plate_temp", "cool_plate_temp_initial_layer",
})
FANS = frozenset({
    "fan_max_speed", "fan_min_speed", "additional_cooling_fan_speed",
    "during_print_exhaust_fan_speed",
})
#: The pose the file has, which keeping is never big.
AS_LAID = (0.0, 0.0, 0.0)

#: How the model is called: the messages and the tools in, the assistant's message out.
Chat = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]
#: How the person is asked: the question in, their answer out.
Person = Callable[[str], str]

_SYSTEM = (
    "You decide how this part prints: the pose it lies in, and its settings over the person's "
    "own presets. Use the tools: `slice` to read a pose and settings in Orca before choosing; "
    "`orca_setting` for what a setting does, its bound, and which setting answers which "
    "symptom; `past_prints` for earlier prints of parts like this, and the lessons kept from "
    "them. A risk that a setting answers, such as warping, lifting or elephant foot, is "
    "answered by the setting, never by turning the part. Ask the person with `ask` only before "
    "a big change: a pose other than the file's own, supports on, a temperature or fan past "
    "what a lesson or the preset backs, or a lot of added time. Anything else, just propose: "
    "the person reviews every change. Name the feature or purpose behind each change and the "
    "skill group it relies on. End with `propose`.\n\n"
)


def _fn(name: str, description: str, properties: dict[str, Any],
        required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False},
    }}


_ROTATION = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3,
             "description": "one of the candidate poses' rotation, in degrees"}
#: Every free text is capped, so an answer stays short (the module docstring).
_WORD = {"type": "string", "maxLength": 120}
_TEXT = {"type": "string", "maxLength": 400}
_SETTINGS = {"type": "array", "maxItems": 12, "items": {
    "type": "object", "additionalProperties": False, "required": ["key", "value"],
    "properties": {"key": _WORD, "value": _WORD, "feature": _WORD, "knowledge": _WORD,
                   "reason": _TEXT},
}}

#: The tools, as the model is given them.
TOOLS: list[dict[str, Any]] = [
    _fn("slice", "Slice the part in a candidate pose with these settings over the presets and "
        "the intake's answers; read Orca's estimate and the slice layer by layer.",
        {"rotation": _ROTATION, "settings": _SETTINGS}, ["rotation"]),
    _fn("orca_setting", "What a setting does, its Orca key and its bound here; or, with no key, "
        "which setting answers which symptom.",
        {"key": _WORD}, []),
    _fn("past_prints", "Earlier prints of parts with these traits (this part's, by default), "
        "their outcomes, and the kept lessons for this part.",
        {"traits": {"type": "array", "maxItems": 8,
                    "items": {"type": "string", "enum": list(TRAITS), "maxLength": 40}}}, []),
    _fn("ask", "Ask the person before a big change. Say what you'd change and why.",
        {"question": _TEXT, "reason": _TEXT, "rotation": _ROTATION, "settings": _SETTINGS},
        ["question", "reason"]),
    _fn("propose", "The pose and the settings to print with. Ends the loop.",
        {"rotation": _ROTATION, "settings": _SETTINGS,
         "reasoning": {"type": "string", "maxLength": 1200},
         "design_notes": {"type": "array", "maxItems": 4, "items": _TEXT}},
        ["rotation", "settings", "reasoning"]),
]


@dataclass(frozen=True)
class PrintLoop:
    """The loop's model, its person, and its limits."""

    chat: Chat
    person: Person
    model: str = "fake"
    max_calls: int = MAX_CALLS
    max_seconds: float = MAX_SECONDS


@dataclass(frozen=True)
class Part:
    """What the loop is given: the part in the file's own pose, its intake and presets, its
    candidate poses, and where to slice and look things up."""

    stl: Path
    facts: PartFacts
    candidates: tuple[Orientation, ...]
    build_volume_mm: tuple[float, float, float]
    skill: Skill
    profiles: SlicerProfiles
    slicer: Slicer
    workdir: Path
    ledger: Ledger | None = None
    #: The kept orientation lessons for this part (mqucifer/print-shop#128).
    orienting: tuple[GivenLesson, ...] = ()
    #: The kept settings lessons for this part, in the file's own pose.
    lessons: tuple[GivenLesson, ...] = ()
    #: The pose kept unless the loop asks: the file's own, or the side the part's context lays
    #: it on (mqucifer/print-shop#130), and where that side was read.
    kept: tuple[float, float, float] = AS_LAID
    kept_from: str = ""


@dataclass(frozen=True)
class LoopResult:
    """How the loop ended, and what it decided, said and was told."""

    ended: str  # "proposed", or why it stopped: past its limit, or the model unreachable
    calls: tuple[LoopCall, ...]
    model_calls: int
    seconds: float
    pose: Orientation | None = None
    #: The proposal as the single call's reply has it: reasoning, changes, design notes.
    reply: dict[str, Any] = field(default_factory=dict)
    #: What the person said no to, with why: their corrections, and lesson candidates.
    corrections: tuple[Change, ...] = ()
    #: The lessons `past_prints` showed.
    looked_up: tuple[int, ...] = ()

    @property
    def proposed(self) -> bool:
        return self.ended == "proposed"


def llm_chat(config: LLMConfig) -> Chat:
    """The real model: one chat completion with tools through LiteLLM (`chat_with_tools`)."""

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return chat_with_tools(config, messages, tools)[0]

    return chat


def cli_person(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> Person:
    """Ask on the command line."""

    def ask(question: str) -> str:
        write(question)
        return read("answer> ")

    return ask


def big(
    part: Part, pose: Orientation, changes: dict[str, str], seconds: int | None,
    baseline_s: int | None,
) -> list[tuple[str, str]]:
    """The big changes in a pose and settings, each as `(what, in words)`: what the loop asks
    before. Decided here, in code, never by the model."""
    found: list[tuple[str, str]] = []
    if pose.rotation_deg != part.kept:
        own = (f"the pose {part.kept_from} lays it in ({pose_value(part.kept)})" if part.kept_from
               else "the file's own pose")
        found.append((f"pose {pose_value(pose.rotation_deg)}",
                      f"turn it to {pose_value(pose.rotation_deg)} from {own}"))
    decided = {k for _, o in part.facts.intake.chosen() for k in o.settings}
    support = changes.get("enable_support", part.facts.preset("enable_support"))
    guarded = (pose.overhang_area_mm2 > 0 and "enable_support" not in changes
               and "enable_support" not in decided)
    if "enable_support" not in decided and part.facts.preset("enable_support") != "1" and (
        support == "1" or guarded
    ):
        found.append(("supports", f"supports on, for {pose.overhang_area_mm2:g} mm2 that needs "
                                  "them" if guarded else "supports on"))
    backed = {g.key: g.value for g in part.lessons if g.key and not g.withheld}
    for key, value in changes.items():
        margin = (TEMPERATURE_MARGIN_C if key in TEMPERATURES
                  else FAN_MARGIN if key in FANS else None)
        if margin is None or key in decided:
            continue
        base = backed.get(key, part.facts.preset(key))
        try:
            past = abs(float(value) - float(base)) > margin
        except ValueError:
            past = value != base
        if past:
            by = "a kept lesson" if key in backed else "the preset"
            found.append((f"setting {key}", f"{key} {base} -> {value}, past what {by} backs"))
    if seconds is not None and baseline_s and seconds > baseline_s * (1 + BIG_TIME_SHARE):
        found.append(("time", f"{(seconds - baseline_s) // 60} more minutes, "
                              f"{seconds / baseline_s - 1:.0%} longer"))
    return found


@dataclass
class _State:
    agreed: set[str] = field(default_factory=set)
    refused: dict[str, str] = field(default_factory=dict)
    corrections: list[Change] = field(default_factory=list)
    looked_up: list[int] = field(default_factory=list)
    calls: list[LoopCall] = field(default_factory=list)
    baseline_s: int | None = None
    slices: int = 0


def run(loop: PrintLoop, part: Part) -> LoopResult:
    """The loop, to `propose` or a limit. It never raises for the model: a failure is an
    ending, and the caller falls back."""
    state = _State()
    started = time.monotonic()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM + symptom_map() + "\n\n" + part.skill.text},
        {"role": "user", "content": _user(part)},
    ]
    model_calls = 0

    def ended(why: str, **done: Any) -> LoopResult:
        return LoopResult(
            ended=why, calls=tuple(state.calls), model_calls=model_calls,
            seconds=round(time.monotonic() - started, 1), corrections=tuple(state.corrections),
            looked_up=tuple(dict.fromkeys(state.looked_up)), **done,
        )

    while True:
        if model_calls >= loop.max_calls:
            return ended(f"past its limit of {loop.max_calls} calls")
        if time.monotonic() - started > loop.max_seconds:
            return ended(f"past its limit of {loop.max_seconds:g} s")
        try:
            message = loop.chat(messages, TOOLS)
        except CutOff as exc:
            # A turn, not an ending: said to the model, recorded, and counted.
            model_calls += 1
            state.calls.append(LoopCall(tool="(model)", arguments={}, result=f"cut off: {exc}",
                                        seconds=exc.info.seconds))
            messages.append({"role": "user", "content": (
                f"Your last answer was cut off at its output limit ({exc}). Answer with one "
                "tool call, and keep your reasoning short.")})
            continue
        except Exception as exc:  # noqa: BLE001 - the model failing is an ending, said
            return ended(f"the model couldn't be reached: {type(exc).__name__}: {exc}"[:300])
        model_calls += 1
        calls = list(message.get("tool_calls") or [])
        messages.append({"role": "assistant", "content": message.get("content") or "",
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            messages.append({"role": "user", "content": "Call a tool. End with `propose`."})
            continue
        for call in calls:
            name = str(call["function"]["name"])
            began = time.monotonic()
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                result, done = _tool(part, loop, state, name, args)
            except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
                args, result, done = {}, f"refused: {exc}", None
            state.calls.append(LoopCall(tool=name, arguments=args, result=result,
                                        seconds=round(time.monotonic() - began, 2)))
            messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                             "content": result})
            if done is not None:
                pose, reply = done
                return ended("proposed", pose=pose, reply=reply)


def _user(part: Part) -> str:
    lines = [prompt(part.facts, part.skill), "",
             "The poses it may lie in (the file's own is 0,0,0). Overhang is what would need "
             "support; a round or chamfer rising from the plate is left out of it:"]
    if part.kept_from:
        lines[-1] = (f"The poses it may lie in. {part.kept_from.capitalize()} lays it in "
                     f"{pose_value(part.kept)}, which is kept unless you ask. Overhang is what "
                     "would need support; a round or chamfer rising from the plate is left out "
                     "of it:")
    for c in part.candidates:
        faces = carried(part.facts.intake, part.facts.context, c.rotation_deg)
        shown = f"; seen there: {faces.as_printed()}" if faces is not None else ""
        lines.append(f"- {pose_value(c.rotation_deg)}: {c.overhang_area_mm2:g} mm2 overhang, "
                     f"{c.bed_contact_area_mm2:g} mm2 on the plate, {c.height_mm:g} mm tall"
                     f"{shown}")
    if part.orienting:
        lines += ["", "Kept lessons on posing parts like this one: a person sent a pose back, "
                      "and why:", *(f"- {g.line()}" for g in part.orienting)]
    return "\n".join(lines)


def _tool(
    part: Part, loop: PrintLoop, state: _State, name: str, args: dict[str, Any]
) -> tuple[str, tuple[Orientation, dict[str, Any]] | None]:
    if name == "slice":
        return _slice(part, state, _pose(part, args), _settings(args)), None
    if name == "orca_setting":
        return _orca_setting(part, args.get("key")), None
    if name == "past_prints":
        return _past_prints(part, state, args.get("traits")), None
    if name == "ask":
        return _ask(part, loop, state, args), None
    if name == "propose":
        return _propose(part, state, args)
    return f"no tool {name!r}: slice, orca_setting, past_prints, ask or propose", None


def _pose(part: Part, args: dict[str, Any]) -> Orientation:
    raw = args.get("rotation") or list(part.kept)
    rotation = tuple(float(a) for a in raw)
    found = next((c for c in part.candidates if c.rotation_deg == rotation), None)
    if found is None:
        raise ValueError(f"{pose_value(rotation)} isn't one of the candidate poses: "  # type: ignore[arg-type]
                         f"{', '.join(pose_value(c.rotation_deg) for c in part.candidates)}")
    return found


def _settings(args: dict[str, Any]) -> dict[str, str]:
    return {str(s["key"]): str(s["value"]).strip() for s in args.get("settings") or ()}


def _bounded(part: Part, settings: dict[str, str]) -> list[str]:
    return [f"{k}={v}: {why}" for k, v in settings.items()
            if (why := check(k, v, part.facts.presets) or refuses(part.facts.context, k, v))]


def _seconds(part: Part, state: _State, pose: Orientation, settings: dict[str, str]) -> Any:
    """Orca's slice of the pose with the intake's answers and these settings over the presets."""
    state.slices += 1
    out = part.workdir / f"slice-{state.slices}"
    stl: Path = part.stl
    if pose.rotation_deg != AS_LAID:
        stl = write_rotated(part.stl, pose.rotation_deg, out / "posed.stl")
    changes = [*answered(part.facts), *(
        Change(key=k, preset=part.facts.preset(k), value=v, reason="", source=Source.MODEL)
        for k, v in settings.items())]
    return part.slicer(stl, apply(part.profiles, Proposal().corrected(changes), out), out)


def _slice(part: Part, state: _State, pose: Orientation, settings: dict[str, str]) -> str:
    refused = _bounded(part, settings)
    if refused:
        return "refused, outside their bounds: " + "; ".join(refused)
    try:
        result = _seconds(part, state, pose, settings)
    except (RuntimeError, OSError, ValueError) as exc:
        return f"Orca couldn't slice it: {exc}"[:400]
    said = [f"Orca: {result.print_time_s // 60} min {result.print_time_s % 60} s, "
            f"{result.filament_cm3} cm3, {result.layer_count} layers"]
    try:
        said += read_slice(result.gcode_path).lines()
    except (OSError, ValueError) as exc:
        said.append(f"(its layers couldn't be read: {exc})")
    return "\n".join(said)


def _orca_setting(part: Part, key: str | None) -> str:
    if not key:
        return symptom_map()
    setting = SETTINGS.get(key)
    if setting is None:
        return f"{key} isn't a setting this proposal may change. " + symptom_map()
    allowed = (", ".join(setting.choices) if setting.choices
               else "{:g} to {:g}".format(*setting.bound(part.facts.presets)))  # type: ignore[misc]
    said = [f"{key}: preset {part.facts.preset(key) or '(unset)'}; allowed {allowed}"]
    for group in part.skill.groups:
        if key in group.settings:
            text = group.path.read_text()
            if f"(`{key}`)" in text:
                said.append(text.split(f"(`{key}`)", 1)[1].split("\n## ", 1)[0].strip())
    return "\n".join(said)


def _past_prints(part: Part, state: _State, wanted: list[str] | None) -> str:
    # The real model, asked once at the boundary, gave words ("flat", "lid", "PLA") where traits
    # go: a word that isn't one is said and set aside, and none leaves the part's own.
    unknown = [w for w in wanted or () if w not in TRAITS]
    has = {w for w in wanted or () if w in TRAITS} or set(traits(part.facts.features))
    lines = [f"Not traits, so set aside: {', '.join(unknown)}. The traits are: "
             f"{', '.join(TRAITS)}."] if unknown else []
    lines.append(f"Kept lessons for this part ({', '.join(sorted(has)) or 'no traits'}):")
    given = [g for g in part.lessons if not g.withheld]
    for g in given:
        state.looked_up.append(g.id)
        lines.append(f"- {g.line()}")
    if not given:
        lines.append("- none")
    lines.append("Earlier prints of parts with those traits:")
    shown = 0
    for row in reversed(part.ledger.rows() if part.ledger is not None else []):
        if not has or not has <= set(traits(row.features or ())):
            continue
        outcome = outcome_words(row.result, row.symptom) if row.result else "no outcome yet"
        fixed = ", ".join(f"{c.key}={c.value}" for c in row.corrections or ()) or "nothing"
        lines.append(f"- {row.stl_name}, job {row.job_id}: {outcome}"
                     + (f" ({row.note})" if row.note else "") + f"; corrected {fixed}")
        shown += 1
        if shown == 8:
            break
    if not shown:
        lines.append("- none")
    return "\n".join(lines)


def _ask(part: Part, loop: PrintLoop, state: _State, args: dict[str, Any]) -> str:
    pose = _pose(part, args)
    settings = _settings(args)
    big_ones = big(part, pose, settings, None, None)
    if not big_ones:
        return ("not a big change, so nobody was asked: propose it, and the person reviews it "
                "with the rest")
    asked = "; ".join(said for _, said in big_ones)
    question = f"{args['question']}\n  ({asked}; because {args['reason']})\n  yes, or say why not"
    while True:
        answer = loop.person(question).strip()
        if answer.lower() in ("y", "yes", "ok"):
            state.agreed.update(what for what, _ in big_ones)
            return f"yes: {asked}"
        if answer:
            break
        question = "A reason is needed: it's what the next print learns from. Why not?"
    for what, _ in big_ones:
        state.refused[what] = answer
    state.corrections += _said_no(part, pose, settings, answer, big_ones)
    return f"no: {answer}. Keep to what they said, and propose."


def _said_no(
    part: Part, pose: Orientation, settings: dict[str, str], why: str,
    big_ones: list[tuple[str, str]],
) -> list[Change]:
    """The person's no, as their corrections: the pose kept as the file lays it, or the setting
    kept at its preset. Each is a lesson candidate, as a send-back is (mqucifer/print-shop#128)."""
    made = []
    for what, _ in big_ones:
        if what.startswith("pose"):
            made.append(Change(key="orientation", preset=pose_value(pose.rotation_deg),
                               value=pose_value(part.kept), reason=why, source=Source.SPONSOR,
                               feature=", ".join(traits(part.facts.features))))
        elif what == "supports":
            made.append(Change(key="enable_support", preset=part.facts.preset("enable_support"),
                               value=part.facts.preset("enable_support") or "0", reason=why,
                               source=Source.SPONSOR))
        elif what.startswith("setting "):
            key = what.removeprefix("setting ")
            made.append(Change(key=key, preset=part.facts.preset(key),
                               value=part.facts.preset(key), reason=why, source=Source.SPONSOR))
    return made


def _propose(
    part: Part, state: _State, args: dict[str, Any]
) -> tuple[str, tuple[Orientation, dict[str, Any]] | None]:
    pose = _pose(part, args)
    settings = _settings(args)
    request = OrientationRequest(candidates=part.candidates, build_volume_mm=part.build_volume_mm)
    verdict = validate_orientation(Chosen(value=pose, source="loop", rationale=""), request)
    blockers = list(verdict.blockers)
    if pose.rotation_deg == part.kept and any(w.startswith("pose") for w in state.refused):
        # The person said no to turning it: keeping the file's own pose is their decision, as a
        # send-back is. Only what the plate can't take still refuses it.
        blockers = [v for v in blockers if v.rule == "orientation.build_volume"]
    if blockers:
        return ("refused: " + "; ".join(v.detail for v in blockers)
                + ". Choose another pose.", None)
    refused = _bounded(part, settings)
    if refused:
        return "refused, outside their bounds: " + "; ".join(refused), None
    said_no = [what for what in state.refused if what in _whats(part, pose, settings)]
    if said_no:
        return ("refused: the person said no to " + ", ".join(said_no) + ": "
                + "; ".join(state.refused[w] for w in said_no), None)
    seconds = baseline = None
    try:
        if state.baseline_s is None:
            laid = next(c for c in part.candidates if c.rotation_deg == part.kept)
            state.baseline_s = _seconds(part, state, laid, {}).print_time_s
        baseline = state.baseline_s
        seconds = _seconds(part, state, pose, settings).print_time_s
    except (RuntimeError, OSError, ValueError):
        pass  # no estimate: time isn't judged
    unasked = [(w, s) for w, s in big(part, pose, settings, seconds, baseline)
               if w not in state.agreed]
    if unasked:
        return ("ask first: " + "; ".join(s for _, s in unasked)
                + ". Ask the person with `ask`, then propose.", None)
    reply = {
        "reasoning": str(args.get("reasoning", "")),
        "changes": [
            {"key": str(c["key"]), "value": str(c["value"]).strip(),
             "feature": str(c.get("feature", "")), "knowledge": str(c.get("knowledge", "")),
             "reason": str(c.get("reason", ""))}
            for c in args.get("settings") or ()
        ],
        "design_notes": [str(n) for n in args.get("design_notes") or ()],
    }
    return f"proposed: {pose_value(pose.rotation_deg)}, {len(settings)} settings", (pose, reply)


def _whats(part: Part, pose: Orientation, settings: dict[str, str]) -> set[str]:
    return {what for what, _ in big(part, pose, settings, None, None)}
