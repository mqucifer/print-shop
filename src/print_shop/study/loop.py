"""A settings study: the print loop's model studies a part's settings for strength, filament and
time, with tools, and recommends one set against the profile's own (mqucifer/print-shop#339).

The Sponsor, 2026-11-26: "I was hoping that the model could go through a couple of different
iterations of infill or other strength settings to try and see where the trade off between
strength, material use and time is ... depending on print orientation different infill will
have a different impact. ... Today it's just spitting my defaults back at me." So it's a loop
with tools, as the print loop is (`setup.loop`, mqucifer/print-shop#129), never a fixed sweep:

- **the profile's own settings are read first, by code** (`DEFAULTS`), so every candidate is
  compared with them, and the model starts from where the part is stressed and which way;
- **`published`**: what the published tests say of each pattern, its walls and its wall order
  (`study.published`): direction matters as much as density;
- **`read`**: a candidate (`study.candidate`), sliced through Orca for its time and grams and
  read by `load` under each of the part's load cases (`study.reading`). The reply says each
  case's worst figures against their limits, where and which way, what rests on the infill
  model, and the trade-off so far: which candidates no other beats on time, grams and margin
  at once;
- **`past_studies`**: the lessons kept from studies of parts with the same traits and joints,
  and what those studies recommended (`study.lessons`);
- **`recommend`**: one candidate read, or none, with the reason, and what it found, as lessons
  for later parts. It ends the loop. Code refuses one that fails a case or has no answer under
  one, and one before the model has read `LEAST_OWN` candidates of its own: it's there to study
  the settings, not to restate them.

Nothing is printed, armed or sent: the result is a table and a recommendation for the Sponsor at
the gate (`study.table`), and the ledger keeps the study and its lessons (`study.lessons`). The
loop has limits on calls, time and candidates, and past one it ends with the table so far and
no recommendation, saying so. Every tool call is kept with its arguments, result and time.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench.assembly import Material
from print_shop.bench.printed import Settings
from print_shop.core.state import Orientation
from print_shop.expert.client import CutOff, LLMConfig, Stopped, TimedOut, chat_with_tools
from print_shop.setup.proposal import LoopCall
from print_shop.study.candidate import (
    DEFAULTS,
    OWN,
    PATTERNS,
    WALL_SEQUENCES,
    Candidate,
    Shaper,
    poses_said,
)
from print_shop.study.published import published
from print_shop.study.reading import Case, Loading, Reading, Slicing, figures, margin_said

__all__ = [
    "LEAST_OWN",
    "MAX_CALLS",
    "MAX_CANDIDATES",
    "MAX_SECONDS",
    "TOOLS",
    "StudyLoop",
    "StudyPart",
    "StudyResult",
    "front",
    "llm_chat",
    "read_candidate",
    "run",
]

#: The model's calls, the seconds and the candidates of its own the loop takes at most. A
#: candidate is two slices and a `load` for each case, minutes each, so the time is hours.
MAX_CALLS = 40
MAX_SECONDS = 6 * 3600.0
MAX_CANDIDATES = 10
#: The candidates of its own the model reads before it may recommend.
LEAST_OWN = 2
#: The most characters of one answer (`chat_with_tools`).
ANSWER_CHARS = 16_000

Chat = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]

_SYSTEM = (
    "You study how this part should print for strength, filament and time: the trade-off "
    "between them, not the profile's defaults restated. The defaults are read for you first. "
    "Read where the part is stressed and which way, and choose candidates for that stress: a "
    "pattern is strongest along its lines (`published` says which way each runs and what the "
    "published tests give it), walls run along the outline, and a pull across the layers is "
    "the layers' bond. Choose walls, top and bottom shells, infill density, pattern and angle, "
    "regions of their own round the joints (a cylinder round each bolt's hole, a mesh of the "
    "joint's own faces, or a band of layers), the wall order only where the finish matters, and "
    "another pose only where it changes what the layers carry. `read` slices each through Orca "
    "for its time and grams and reads it with load under each of the part's load cases. Go on "
    "from what you read: where the trade-off is still open, read a candidate between. Then "
    "`recommend` one that passes every case, with your reason against the defaults, and what "
    f"you found as lessons for later parts like this. Read at least {LEAST_OWN} candidates of "
    "your own first. Nothing is printed: the Sponsor decides at the gate."
)


def _fn(name: str, description: str, properties: dict[str, Any],
        required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


_TEXT = {"type": "string", "maxLength": 400}
_NUMBER = {"type": "number"}
_OWN: dict[str, Any] = {
    "walls": {"type": "integer"}, "top": {"type": "integer"}, "bottom": {"type": "integer"},
    "infill": _NUMBER, "pattern": {"type": "string", "enum": list(PATTERNS)},
    "infill_angle": _NUMBER,
}
_POINT = {"type": "array", "items": _NUMBER, "minItems": 3, "maxItems": 3}
_REGION = {"type": "object", "properties": {
    "name": {"type": "string", "maxLength": 40}, "why": _TEXT,
    "joints": {"description": "\"all\", or the joints' names, for a region round each"},
    "margin": _NUMBER, "faces": {"type": "boolean"},
    "box": {"type": "array", "items": _POINT, "minItems": 2, "maxItems": 2},
    "cylinder": {"type": "object"},
    "layers": {"type": "array", "items": _NUMBER, "minItems": 2, "maxItems": 2},
    **_OWN,
}}

#: The tools, as the model is given them.
TOOLS: list[dict[str, Any]] = [
    _fn("published", "What the published tests say of a pattern, or of each load reads: which "
        "way its lines run and build walls, how stiff it is at 20, 40 and 60%, what strain it "
        "takes, and from what density a test checks it; the walls' thickness by count; Orca's "
        "own words for each wall order.",
        {"pattern": {"type": "string", "enum": list(PATTERNS)}}, []),
    _fn("read", "Slice a candidate through Orca for its time and grams, and read it with load "
        "under each of the part's load cases: each case's worst figures against their limits, "
        "where and which way. Give only what differs from the defaults.",
        {"why": _TEXT,
         "settings": {"type": "object", "properties": _OWN, "additionalProperties": False},
         "wall_sequence": {"type": "string", "enum": list(WALL_SEQUENCES)},
         "turn": {"type": "array", "items": _NUMBER, "minItems": 3, "maxItems": 3},
         "regions": {"type": "array", "maxItems": 8, "items": _REGION}},
        ["why"]),
    _fn("past_studies", "The lessons kept from studies of parts with this part's traits and "
        "joints, and what those studies recommended.", {}, []),
    _fn("recommend", "The candidate to print with, by its name (or \"none\" where none "
        "passes), why against the defaults, and what you found as lessons for later parts. "
        "Ends the study.",
        {"candidate": {"type": "string", "maxLength": 40},
         "reason": {"type": "string", "maxLength": 1200},
         "lessons": {"type": "array", "maxItems": 4, "items": {
             "type": "object", "additionalProperties": False, "required": ["text", "shown_by"],
             "properties": {"text": _TEXT, "shown_by": {"type": "array", "items": {
                 "type": "string", "maxLength": 40}, "maxItems": 6}}}}},
        ["candidate", "reason"]),
]


@dataclass(frozen=True)
class StudyLoop:
    """The study's model and its limits."""

    chat: Chat
    model: str = "fake"
    max_calls: int = MAX_CALLS
    max_seconds: float = MAX_SECONDS
    max_candidates: int = MAX_CANDIDATES
    least_own: int = LEAST_OWN


@dataclass
class StudyPart:
    """The part a study reads: its file, pose and load cases, and how each candidate is made,
    sliced and read."""

    name: str
    stl: Path
    turn: tuple[float, float, float]
    cases: tuple[Case, ...]
    shaper: Shaper
    slicing: Slicing
    loading: Loading
    #: The profile's own settings, as the part prints with them in its pose.
    defaults: Settings
    material: Material
    workdir: Path
    #: What the part's lessons are keyed by: its traits and its joints (`study.lessons.keys`).
    keys: tuple[str, ...] = ()
    #: The poses it may print in, with their figures, the first its own.
    poses: tuple[Orientation, ...] = ()
    #: The kept lessons from earlier studies of parts with every one of their keys, and those
    #: studies' recommendations, in words (`past_studies`).
    lessons: tuple[str, ...] = ()
    earlier: tuple[str, ...] = ()
    #: The profile's own wall order.
    wall_sequence: str = ""
    filament: str = ""


@dataclass
class StudyResult:
    """How a study ended, what it read, and what it recommends."""

    ended: str
    readings: list[Reading]
    calls: list[LoopCall]
    model_calls: int
    seconds: float
    model: str
    recommended: str | None = None
    reason: str = ""
    #: What it found, each with the candidates that show it.
    lessons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def recommendation(self) -> Reading | None:
        return next((r for r in self.readings if r.candidate.name == self.recommended), None)

    @property
    def defaults(self) -> Reading | None:
        return next((r for r in self.readings if r.candidate.name == DEFAULTS), None)


def llm_chat(config: LLMConfig, stop: threading.Event | None = None) -> Chat:
    """The real model: one chat completion with tools through LiteLLM (`chat_with_tools`)."""

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return chat_with_tools(config, messages, tools, answer_chars=ANSWER_CHARS, stop=stop)[0]

    return chat


def read_candidate(part: StudyPart, candidate: Candidate) -> Reading:
    """A candidate sliced and read under each case. Never raises for Orca or `load`: what fails
    is said, as the reading's error or a case's verdict."""
    out = part.workdir / candidate.name
    reading = Reading(candidate)
    try:
        reading.sliced = part.slicing(candidate, out)
    except Exception as exc:  # noqa: BLE001 - Orca failing is the candidate's answer, said
        reading.error = f"Orca couldn't slice it: {type(exc).__name__}: {exc}"[:400]
        return reading
    for case in part.cases:
        try:
            record = part.loading(candidate, reading.sliced, case, out / case.name)
        except Exception as exc:  # noqa: BLE001 - load failing is the case's answer, said
            record = {"verdict": "NO ANSWER", "why": f"{type(exc).__name__}: {exc}"[:400]}
        reading.cases.append(figures(record, part.name, case))
    return reading


def front(readings: list[Reading]) -> list[Reading]:
    """The readings that pass every case and that no other passing one beats on time, grams and
    margin at once: the trade-off as it stands."""
    passing = [r for r in readings if r.passes and r.sliced is not None
               and r.sliced.time_s is not None and r.sliced.grams is not None
               and r.margin is not None]

    def figures_of(r: Reading) -> tuple[float, float, float]:
        assert r.sliced is not None and r.margin is not None
        return float(r.sliced.time_s or 0), float(r.sliced.grams or 0), -r.margin

    found = []
    for r in passing:
        mine = figures_of(r)
        beaten = any(
            all(o <= m for o, m in zip(figures_of(other), mine, strict=True))
            and figures_of(other) != mine
            for other in passing if other is not r
        )
        if not beaten:
            found.append(r)
    return found


def _minutes(seconds: int | None) -> str:
    return "?" if seconds is None else f"{seconds // 60} min {seconds % 60} s"


def _against(reading: Reading, base: Reading | None) -> str:
    s = reading.sliced
    if s is None:
        return ""
    said = f"Orca: {_minutes(s.time_s)}, {s.grams if s.grams is not None else '?'} g"
    b = base.sliced if base is not None and base is not reading else None
    if b is not None and s.time_s is not None and b.time_s is not None:
        said += f" ({(s.time_s - b.time_s) / 60:+.1f} min"
        if s.grams is not None and b.grams is not None:
            said += f", {s.grams - b.grams:+.1f} g"
        said += " against the defaults)"
    if s.base_time_s is not None and s.time_s is not None:
        said += f"; its regions add {s.time_s - s.base_time_s:+d} s"
        if s.base_grams is not None and s.grams is not None:
            said += f" and {s.grams - s.base_grams:+.2f} g"
    return said


def reading_said(reading: Reading, base: Reading | None, most: int = 3) -> list[str]:
    """A reading as the model and the table say it: its time and grams against the defaults, and
    each case's worst figures, where and which way."""
    lines = [f"{reading.candidate.name}: {reading.candidate.said()}"]
    if reading.error:
        return [*lines, f"  {reading.error}"]
    lines.append("  " + _against(reading, base))
    for case in reading.cases:
        worst = sorted(case.figures, key=lambda f: -f.share)[:most]
        verdict = case.verdict if case.passes or case.verdict != "OK" else "OVER ITS LIMIT"
        lines.append(f"  {case.case}: {verdict}" + (
            f", {case.why[:300]}" if case.verdict not in ("OK", "FAIL") else ""))
        lines += [f"    {f.said()}; {margin_said(f.margin)}" for f in worst]
    if reading.sliced is not None:
        lines += [f"  {line}" for line in reading.sliced.band_said()]
    return lines


@dataclass
class _State:
    readings: list[Reading]
    calls: list[LoopCall]
    by_key: dict[str, Reading] = field(default_factory=dict)
    own: int = 0


def _user(part: StudyPart, loop: StudyLoop, base: Reading) -> str:
    d = part.defaults
    joints = part.shaper.joints()
    lines = [
        f"The part: {part.name} ({part.stl.name}), printed in {part.filament or 'its filament'}, "
        f"turned {','.join(f'{v:g}' for v in part.turn)}, standing own {d.up or '?'} up.",
        f"Its profile's own settings: {d.said()}; walls laid "
        f"{part.wall_sequence or 'as the profile says'}.",
        "Its load cases, each read by load on the part as printed:",
        *(f"- {c.said()}" for c in part.cases),
        "Its joints, each a region can be put round (`regions: [{\"joints\": [NAME], "
        "\"margin\": MM, \"faces\": true, ...settings}]`):",
        *(f"- {name}: {said}" if name else f"- ({said})" for name, said in joints),
    ]
    if not joints:
        lines.append("- none named in its context or assembly")
    lines.append("The poses it may print in (turn, then overhang, plate contact and height):")
    lines += [f"- {poses_said([p.rotation_deg])}: {p.overhang_area_mm2:g} mm2 overhang, "
              f"{p.bed_contact_area_mm2:g} mm2 on the plate, {p.height_mm:g} mm tall"
              for p in part.poses] or [f"- {poses_said(part.shaper.poses)}"]
    lines += ["", f"The defaults, read ({loop.max_candidates} candidates of your own at most; "
                  f"settings are {', '.join(OWN)}):", *reading_said(base, None, most=6)]
    if part.lessons:
        lines += ["", "Kept lessons from studies of parts like this one:",
                  *(f"- {line}" for line in part.lessons)]
    return "\n".join(lines)


def run(loop: StudyLoop, part: StudyPart, stop: threading.Event | None = None) -> StudyResult:
    """The study, to `recommend`, a limit or `stop`. It never raises for the model: a failure is
    an ending, with the table read so far."""
    started = time.monotonic()
    base = read_candidate(part, Candidate(DEFAULTS, Settings(), turn=part.turn,
                                          why="the profile's own settings, read first by code"))
    state = _State(readings=[base], calls=[])
    state.by_key[base.candidate.key()] = base
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user(part, loop, base)},
    ]
    model_calls = 0

    def ended(why: str, **done: Any) -> StudyResult:
        return StudyResult(why, state.readings, state.calls, model_calls,
                           round(time.monotonic() - started, 1), loop.model, **done)

    if base.error:
        # The part and the profiles are every candidate's: what refuses the defaults, such as a
        # plate that won't take the filament, refuses them all.
        return ended(f"the profile's own settings couldn't be sliced, so nothing was studied: "
                     f"{base.error}"[:600])
    while True:
        if stop is not None and stop.is_set():
            return ended("stopped")
        if model_calls >= loop.max_calls:
            return ended(f"past its limit of {loop.max_calls} calls, with no recommendation")
        if time.monotonic() - started > loop.max_seconds:
            return ended(f"past its limit of {loop.max_seconds:g} s, with no recommendation")
        try:
            message = loop.chat(messages, TOOLS)
        except CutOff as exc:
            model_calls += 1
            state.calls.append(LoopCall(tool="(model)", arguments={}, result=f"cut off: {exc}",
                                        seconds=exc.info.seconds))
            messages.append({"role": "user", "content": (
                f"Your last answer was cut off at its output limit ({exc}). Answer with one "
                "tool call, and keep your reasoning short.")})
            continue
        except Stopped:
            return ended("stopped")
        except TimedOut as exc:
            return ended(f"the model {exc}")
        except Exception as exc:  # noqa: BLE001 - the model failing is an ending, said
            return ended(f"the model couldn't be reached: {type(exc).__name__}: {exc}"[:300])
        model_calls += 1
        calls = list(message.get("tool_calls") or [])
        messages.append({"role": "assistant", "content": message.get("content") or "",
                         **({"tool_calls": calls} if calls else {})})
        if not calls:
            messages.append({"role": "user", "content": "Call a tool. End with `recommend`."})
            continue
        for call in calls:
            if stop is not None and stop.is_set():
                return ended("stopped")
            name = str(call["function"]["name"])
            began = time.monotonic()
            done: dict[str, Any] | None = None
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError("the arguments are an object")
                result, done = _tool(loop, part, state, name, args)
            except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
                args, result = {}, f"refused: {exc}"
            state.calls.append(LoopCall(tool=name, arguments=args, result=result,
                                        seconds=round(time.monotonic() - began, 2)))
            messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                             "content": result})
            if done is not None:
                return ended("recommended", **done)


def _tool(loop: StudyLoop, part: StudyPart, state: _State, name: str,
          args: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    if name == "published":
        pattern = args.get("pattern")
        return published(str(pattern) if pattern else None, part.material,
                         float(part.defaults.line_width or 0.45),
                         float(part.defaults.layer_height or 0.2)), None
    if name == "read":
        return _read(loop, part, state, args), None
    if name == "past_studies":
        lines = ["Kept lessons for parts with these keys "
                 f"({', '.join(part.keys) or 'no traits or joints'}):",
                 *(f"- {line}" for line in part.lessons or ("none",)),
                 "Earlier studies of parts with them:",
                 *(f"- {line}" for line in part.earlier or ("none",))]
        return "\n".join(lines), None
    if name == "recommend":
        return _recommend(loop, state, args)
    return f"no tool {name!r}: published, read, past_studies or recommend", None


def _read(loop: StudyLoop, part: StudyPart, state: _State, args: dict[str, Any]) -> str:
    if state.own >= loop.max_candidates:
        return (f"refused: {state.own} candidates of your own is the most. Recommend one of "
                "those read.")
    candidate = part.shaper.candidate(f"c{state.own + 1}", args)
    same = state.by_key.get(candidate.key())
    if same is not None:
        return (f"that's {same.candidate.name}, read already:\n"
                + "\n".join(reading_said(same, state.readings[0])))
    state.own += 1
    reading = read_candidate(part, candidate)
    state.readings.append(reading)
    state.by_key[candidate.key()] = reading
    lines = reading_said(reading, state.readings[0])
    if reading.sliced is not None and reading.sliced.regions:
        lines += ["  What Orca built in each region:",
                  *(f"    {line}" for line in reading.sliced.regions[:24])]
    best = front(state.readings)
    if best:
        lines.append("The trade-off so far, the candidates no other passing one beats on time, "
                     "grams and margin at once: " + "; ".join(
                         f"{r.candidate.name} ({_minutes(r.sliced.time_s if r.sliced else None)}"
                         f", {r.sliced.grams if r.sliced else '?'} g, "
                         f"{margin_said(r.margin or 0)})" for r in best)
                     + (". It's open between them." if len(best) > 1 else "."))
    else:
        lines.append("Nothing read so far passes every case.")
    return "\n".join(lines)


def _recommend(loop: StudyLoop, state: _State,
               args: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    chosen = str(args["candidate"]).strip()
    names = {r.candidate.name: r for r in state.readings}
    if state.own < loop.least_own and state.own < loop.max_candidates:
        return (f"refused: read at least {loop.least_own} candidates of your own first "
                f"({state.own} so far): the study is of the settings, not the defaults "
                "restated", None)
    if chosen != "none":
        reading = names.get(chosen)
        if reading is None:
            return (f"refused: no candidate {chosen!r} was read: {', '.join(names)}", None)
        if not reading.passes:
            return (f"refused: {chosen} doesn't pass every case: {reading.why_not()}"[:600],
                    None)
    elif any(r.passes for r in state.readings):
        return ("refused: " + ", ".join(r.candidate.name for r in state.readings if r.passes)
                + " pass every case: recommend one of them", None)
    lessons = []
    for item in args.get("lessons") or ():
        shown = [str(n) for n in item.get("shown_by") or ()]
        if unknown := [n for n in shown if n not in names]:
            return (f"refused: a lesson's shown_by names {', '.join(unknown)}, not read", None)
        if not str(item.get("text", "")).strip():
            return ("refused: a lesson has its text", None)
        lessons.append({"text": str(item["text"]).strip(), "shown_by": shown})
    return (f"recommended: {chosen}", {"recommended": None if chosen == "none" else chosen,
                                        "reason": str(args["reason"]).strip(),
                                        "lessons": lessons})
