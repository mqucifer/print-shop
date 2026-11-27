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
- **`read`**: a candidate (`study.candidate`), queued to be sliced through Orca for its time and
  grams and read by `load` under each of the part's load cases (`study.reading`). It replies at
  once: the slices and reads run in parallel, as many at once as the host's budget holds
  (`bench.budget`, mqucifer/print-shop#364), each single-threaded inside, so every figure is
  what it would be read alone;
- **`wait`**: the readings that have landed, in the candidates' order: each case's worst figures
  against their limits, where and which way, what rests on the infill model, and the trade-off
  so far, which candidates no other beats on time, grams and margin at once;
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

import contextlib
import json
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.bench.as_printed import wall_thickness
from print_shop.bench.assembly import Material
from print_shop.bench.budget import LOAD_CORES, SLICE_CORES, Budget, Slots, megabytes
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
from print_shop.study.limits import Limits, OutOfLimits
from print_shop.study.published import published
from print_shop.study.reading import (
    Case,
    CaseRead,
    Loading,
    Reading,
    Slicing,
    figures,
    margin_said,
    unchecked,
)

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
#: What a candidate the study's end cut off says: it wasn't read, which isn't failing.
CUT_OFF = "not read: the study ended before it was"

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
    "another pose only where it changes what the layers carry. `read` queues each, to be sliced "
    "through Orca for its time and grams and read with load under each of the part's load "
    "cases: several read at once, so queue those you'd read anyway, and `wait` for their "
    "readings as they land. Go on from what you read: where the trade-off is still open, read a "
    "candidate between. Once every candidate you queued has landed, "
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
    _fn("read", "Queue a candidate, to be sliced through Orca for its time and grams and read "
        "with load under each of the part's load cases: it replies at once, and `wait` gives its "
        "reading as it lands. Give only what differs from the defaults. A region with walls of "
        "its own is walled round its own outline from every side, so it holds one wall for each "
        "two bead spacings of its depth in the part (a face mesh 1 mm deep holds 1): choose its "
        "depth (margin) and walls together. One asking more than it holds is refused before "
        "it's sliced, saying what fits.",
        {"why": _TEXT,
         "settings": {"type": "object", "properties": _OWN, "additionalProperties": False},
         "wall_sequence": {"type": "string", "enum": list(WALL_SEQUENCES)},
         "turn": {"type": "array", "items": _NUMBER, "minItems": 3, "maxItems": 3},
         "regions": {"type": "array", "maxItems": 8, "items": _REGION}},
        ["why"]),
    _fn("wait", "Wait for a queued candidate to land, and read each that has: its time and "
        "grams, each case's worst figures against their limits, where and which way, what Orca "
        "built in each region, and the trade-off so far.", {}, []),
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
    #: The part's plastic as `load` reads it, or None where it knows none (`published`).
    material: Material | None
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
    #: What the host runs at once: each slice holds a core, and each `load` its memory and a
    #: core (`bench.budget`). One at a time unless given.
    slots: Slots = field(default_factory=lambda: Slots(Budget.serial()))
    #: The Sponsor's limits, binding on it (`study.limits`).
    limits: Limits = field(default_factory=Limits)


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
    #: The host's budget it read within, and the most jobs that ran at once.
    budget: str = ""
    most_at_once: int = 0
    #: The Sponsor's limits, and each candidate they ruled out before it was sliced, as asked.
    limits: str = ""
    ruled_out: list[str] = field(default_factory=list)

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


def _load_memory(case: Case) -> int:
    """The memory `load` runs in under a case, in MiB: its container's own (`host.memory_for`)."""
    return megabytes(host.memory_for(["load", "-", *case.args]))


def read_candidate(part: StudyPart, candidate: Candidate,
                   cancel: threading.Event | None = None) -> Reading:
    """A candidate sliced and read under each case. Never raises for Orca or `load`: what fails
    is said, as the reading's error or a case's verdict. Its slice holds a core of the host's
    budget, and each case's `load` its own memory and a core, all its cases at once as the
    budget allows; each is single-threaded inside, so what it reads is what it would alone. Its
    cases are in the part's order, however they land. One `cancel`led before it starts isn't
    read."""
    out = part.workdir / candidate.name
    reading = Reading(candidate)
    if cancel is not None and cancel.is_set():
        reading.error = CUT_OFF
        return reading
    try:
        with part.slots.take(cpus=SLICE_CORES):
            # Queued, it may have waited for its slot past the study's end.
            if cancel is not None and cancel.is_set():
                reading.error = CUT_OFF
                return reading
            reading.sliced = part.slicing(candidate, out)
    except Exception as exc:  # noqa: BLE001 - Orca failing is the candidate's answer, said
        reading.error = f"Orca couldn't slice it: {type(exc).__name__}: {exc}"[:400]
        return reading
    if past := part.limits.sliced_past(reading.sliced.time_s, reading.sliced.grams):
        reading.ruled_out = past
        if candidate.name != DEFAULTS:
            return reading
    if reading.sliced.unbuilt:
        # `load` reads a region as asked: where Orca built less, it would read the part stronger
        # than it prints, so the candidate fails as not as sliced, and isn't read.
        reading.error = ("not as sliced: Orca didn't build what its regions ask, and load would "
                         "read them as asked, stronger than printed: "
                         + "; ".join(reading.sliced.unbuilt))[:900]
        return reading
    sliced = reading.sliced

    def one(case: Case) -> CaseRead | None:
        try:
            with part.slots.take(_load_memory(case), LOAD_CORES):
                if cancel is not None and cancel.is_set():
                    return None
                record = part.loading(candidate, sliced, case, out / case.name)
        except Exception as exc:  # noqa: BLE001 - load failing is the case's answer, said
            record = {"verdict": "NO ANSWER", "why": f"{type(exc).__name__}: {exc}"[:400]}
        return figures(record, part.name, case)

    with ThreadPoolExecutor(max_workers=max(1, len(part.cases)),
                            thread_name_prefix=f"study-{candidate.name}") as cases:
        found = list(cases.map(one, part.cases))
    if any(c is None for c in found):
        # Cut off by the study's end under some of its cases: not a failure, not read.
        reading.error = CUT_OFF
        return reading
    reading.cases = [c for c in found if c is not None]
    return reading


def front(readings: list[Reading]) -> list[Reading]:
    """The readings that pass every case and that no other passing one beats on time, grams and
    margin at once: the trade-off as it stands."""
    passing = [r for r in readings if r.passes and r.sliced is not None
               and r.sliced.time_s is not None and r.sliced.grams is not None]

    def figures_of(r: Reading) -> tuple[float, float, float]:
        # With no load cases there's no margin: time and grams alone.
        assert r.sliced is not None
        return float(r.sliced.time_s or 0), float(r.sliced.grams or 0), -(r.margin or 0.0)

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
    if reading.candidate.notes:
        lines.append("  before slicing: " + "; ".join(reading.candidate.notes))
    if reading.sliced is not None:
        lines.append("  " + _against(reading, base))
    if reading.error:
        return [*lines, f"  {reading.error}"]
    for case in reading.cases:
        worst = sorted(case.figures, key=lambda f: -f.share)[:most]
        verdict = case.verdict if case.passes or case.verdict != "OK" else "OVER ITS LIMIT"
        lines.append(f"  {case.case}: {verdict}" + (
            f", {case.why[:300]}" if case.verdict not in ("OK", "FAIL") else ""))
        lines += [f"    {f.said()}; {margin_said(f.margin)}" for f in worst]
    if reading.sliced is not None:
        lines += [f"  {line}" for line in reading.sliced.band_said()]
        lines += [f"  not read from the layers, so load takes it as asked: {line}"
                  for line in reading.sliced.unread]
        if loose := unchecked(reading.sliced.printed):
            lines.append("  below the densities the published tests reach, so unchecked by "
                         "any: " + "; ".join(loose))
    return lines


@dataclass
class _State:
    readings: list[Reading]
    calls: list[LoopCall]
    by_key: dict[str, Reading] = field(default_factory=dict)
    own: int = 0
    #: The candidates queued that haven't landed, and those landed that the model hasn't been
    #: given yet, by name; and the names as they land, from the reads' threads.
    pending: dict[str, Future[None]] = field(default_factory=dict)
    unsaid: list[str] = field(default_factory=list)
    landed: queue.Queue[str] = field(default_factory=queue.Queue)
    cancel: threading.Event = field(default_factory=threading.Event)
    reads: ThreadPoolExecutor | None = None
    #: Each candidate the Sponsor's limits ruled out before it was sliced, and why.
    ruled_out: list[str] = field(default_factory=list)
    #: Called in the loop's thread as each reading lands: the table as it fills in.
    on_land: Callable[[], None] | None = None

    def land(self, name: str) -> None:
        self.pending.pop(name, None)
        self.unsaid.append(name)
        if self.on_land is not None:
            self.on_land()

    def drain(self) -> None:
        """Take what's landed off the queue, in the loop's thread."""
        while True:
            try:
                name = self.landed.get_nowait()
            except queue.Empty:
                return
            self.land(name)


def _user(part: StudyPart, loop: StudyLoop, base: Reading) -> str:
    d = part.defaults
    joints = part.shaper.joints()
    lines = [
        f"The part: {part.name} ({part.stl.name}), printed in {part.filament or 'its filament'}, "
        f"turned {poses_said([part.turn])}, standing own {d.up or '?'} up.",
        f"Its profile's own settings: {d.said()}; walls laid "
        f"{part.wall_sequence or 'as the profile says'}. A bead's spacing is "
        f"{wall_thickness(1, float(d.line_width or 0.45), float(d.layer_height or 0.2)):.2f} mm: "
        "a region holds one wall of its own for each two of them across its depth in the part.",
        *(["Its load cases, each read by load on the part as printed:",
           *(f"- {c.said()}" for c in part.cases)] if part.cases else [
            "It has no load cases: it's studied for time, grams and finish only, the wall "
            "order and the shells as much as the infill. A candidate passes on its slice, and "
            "the trade-off is time against grams."]),
        *([f"The Sponsor's limits, binding: {part.limits.said()}. A candidate past what's "
           "known before slicing is refused; one Orca slices past the time or grams is ruled "
           "out, and can't be recommended."] if part.limits.given() else []),
        "Its joints, each a region can be put round (`regions: [{\"joints\": [NAME], "
        "\"margin\": MM, \"faces\": true, ...settings}]`):",
        *(f"- {name}: {said}" if name else f"- ({said})" for name, said in joints),
    ]
    if not joints:
        lines.append("- none named in its context or assembly")
    lines.append("The poses it may print in, each that fits the bed, turned about its up axis "
                 "where it must to fit (turn, then overhang, plate contact and height):")
    lines += [f"- {poses_said([p.rotation_deg])}: {p.overhang_area_mm2:g} mm2 overhang, "
              f"{p.bed_contact_area_mm2:g} mm2 on the plate, {p.height_mm:g} mm tall"
              for p in part.poses] or [f"- {poses_said(part.shaper.poses)}"]
    lines += ["", f"The defaults, read ({loop.max_candidates} candidates of your own at most; "
                  f"settings are {', '.join(OWN)}):", *reading_said(base, None, most=6)]
    if part.lessons:
        lines += ["", "Kept lessons from studies of parts like this one:",
                  *(f"- {line}" for line in part.lessons)]
    return "\n".join(lines)


def run(loop: StudyLoop, part: StudyPart, stop: threading.Event | None = None,
        progress: Callable[[StudyResult], None] | None = None) -> StudyResult:
    """The study, to `recommend`, a limit or `stop`. It never raises for the model: a failure is
    an ending, with the table read so far."""
    started = time.monotonic()
    base = read_candidate(part, Candidate(DEFAULTS, Settings(), turn=part.turn,
                                          why="the profile's own settings, read first by code"))
    state = _State(readings=[base], calls=[],
                   reads=ThreadPoolExecutor(max_workers=max(1, loop.max_candidates),
                                            thread_name_prefix="study-read"))
    state.by_key[base.candidate.key()] = base
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user(part, loop, base)},
    ]
    model_calls = 0

    def outcome(why: str, **done: Any) -> StudyResult:
        return StudyResult(why, state.readings, state.calls, model_calls,
                           round(time.monotonic() - started, 1), loop.model, **done,
                           budget=part.slots.budget.said(), most_at_once=part.slots.most,
                           limits=part.limits.said(), ruled_out=list(state.ruled_out))

    def so_far() -> None:
        assert progress is not None
        progress(outcome("reading"))

    if progress is not None:
        state.on_land = so_far
        so_far()

    def ended(why: str, **done: Any) -> StudyResult:
        # What's queued and not begun isn't read; what's running is waited for, as a container
        # can't be stopped mid-solve and still say what it read.
        state.cancel.set()
        if state.reads is not None:
            state.reads.shutdown(wait=True, cancel_futures=True)
        for reading in state.readings:
            # Never sliced: a reading whose loads were cut off says so as it's read.
            if reading.sliced is None and not reading.error:
                reading.error = CUT_OFF
        return outcome(why, **done)

    try:
        part.limits.check(Candidate(DEFAULTS, part.defaults))
    except OutOfLimits as exc:
        base.ruled_out = "; ".join(w for w in (base.ruled_out, f"the profile's own: {exc}") if w)
    if base.error:
        # The part and the profiles are every candidate's: what refuses the defaults, such as a
        # plate that won't take the filament, refuses them all.
        return ended(f"the profile's own settings couldn't be sliced, so nothing was studied: "
                     f"{base.error}"[:600])
    try:
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
                    result, done = _tool(loop, part, state, name, args, started, stop)
                except (json.JSONDecodeError, TypeError, KeyError, ValueError,
                        AttributeError) as exc:
                    # Arguments that aren't of the tools' shape are refused to the model, never a
                    # crash: the first real run's lessons came as words alone.
                    said = (str(exc) if isinstance(exc, ValueError)
                            else f"{type(exc).__name__}: {exc}")
                    args, result = {}, f"refused: {said}"
                state.calls.append(LoopCall(tool=name, arguments=args, result=result,
                                            seconds=round(time.monotonic() - began, 2)))
                messages.append({"role": "tool", "tool_call_id": call.get("id", name),
                                 "content": result})
                if done is not None:
                    return ended("recommended", **done)
    except BaseException as exc:
        # An interrupt, or what no tool answers for (a mesh that won't write, a full disk):
        # nothing is left running, and the table so far is written, before it's raised.
        state.cancel.set()
        for stopping in (getattr(part.slicing, "stop", None), getattr(part.loading, "stop", None)):
            if callable(stopping):
                with contextlib.suppress(Exception):
                    stopping()
        if state.reads is not None:
            state.reads.shutdown(wait=True, cancel_futures=True)
        for reading in state.readings:
            # Never sliced: a reading whose loads were cut off says so as it's read.
            if reading.sliced is None and not reading.error:
                reading.error = CUT_OFF
        if progress is not None:
            with contextlib.suppress(Exception):
                progress(outcome(f"stopped by {type(exc).__name__}: {exc}"[:300]))
        raise


def _tool(loop: StudyLoop, part: StudyPart, state: _State, name: str, args: dict[str, Any],
          started: float = 0.0, stop: threading.Event | None = None,
          ) -> tuple[str, dict[str, Any] | None]:
    if name == "published":
        pattern = args.get("pattern")
        return published(str(pattern) if pattern else None, part.material,
                         float(part.defaults.line_width or 0.45),
                         float(part.defaults.layer_height or 0.2)), None
    if name == "read":
        return _read(loop, part, state, args), None
    if name == "wait":
        return _wait(loop, state, started, stop), None
    if name == "past_studies":
        lines = ["Kept lessons for parts with these keys "
                 f"({', '.join(part.keys) or 'no traits or joints'}):",
                 *(f"- {line}" for line in part.lessons or ("none",)),
                 "Earlier studies of parts with them:",
                 *(f"- {line}" for line in part.earlier or ("none",))]
        return "\n".join(lines), None
    if name == "recommend":
        state.drain()
        if state.pending:
            return (f"refused: {', '.join(state.pending)} still reading: `wait` for "
                    f"{'them' if len(state.pending) > 1 else 'it'} first", None)
        if state.unsaid:
            # Landed, and never seen: each is given now, and the model recommends again.
            return ("refused: these landed and you haven't read them yet; recommend again with "
                    "them in hand:\n" + "\n".join(_landed_said(state)), None)
        return _recommend(loop, state, args)
    return f"no tool {name!r}: published, read, wait, past_studies or recommend", None


def _read(loop: StudyLoop, part: StudyPart, state: _State, args: dict[str, Any]) -> str:
    """Queue a candidate: it's read on the reads' threads, within the host's budget, and the
    model goes on. Its row is in the table from now, so the table's order is the order it was
    asked in, whatever order the readings land in."""
    state.drain()
    if state.own >= loop.max_candidates:
        return (f"refused: {state.own} candidates of your own is the most. Recommend one of "
                "those read.")
    candidate = part.shaper.candidate(f"c{state.own + 1}", args)
    try:
        part.limits.check(candidate)
    except OutOfLimits as exc:
        # Kept for the table: what was asked, and the limit that ruled it out.
        state.ruled_out.append(f"{candidate.said()}: {exc}")
        raise
    same = state.by_key.get(candidate.key())
    if same is not None:
        if same.candidate.name in state.pending:
            return f"that's {same.candidate.name}, queued already: `wait` for its reading"
        return (f"that's {same.candidate.name}, read already:\n"
                + "\n".join(reading_said(same, state.readings[0])))
    state.own += 1
    reading = Reading(candidate)
    state.readings.append(reading)
    state.by_key[candidate.key()] = reading
    assert state.reads is not None
    name = candidate.name

    def read() -> None:
        try:
            found = read_candidate(part, candidate, state.cancel)
            reading.sliced, reading.cases, reading.error = found.sliced, found.cases, found.error
            reading.ruled_out = found.ruled_out
        finally:
            state.landed.put(name)

    state.pending[name] = state.reads.submit(read)
    notes = [f"  before slicing: {'; '.join(candidate.notes)}"] if candidate.notes else []
    return "\n".join([
        f"queued {name}: {candidate.said()}", *notes,
        f"Reading or queued: {', '.join(state.pending)}, within the host's budget "
        f"({part.slots.budget.said()}). Queue more you'd read anyway, or `wait` for readings "
        "as they land."])


def _landed_said(state: _State) -> list[str]:
    """Each reading landed that the model hasn't been given, in the candidates' order, with what
    Orca built in its regions; then the trade-off so far."""
    order = {r.candidate.name: k for k, r in enumerate(state.readings)}
    names = sorted(state.unsaid, key=lambda n: order[n])
    state.unsaid.clear()
    lines = []
    for name in names:
        reading = state.readings[order[name]]
        lines += reading_said(reading, state.readings[0])
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
    if state.pending:
        lines.append(f"Still reading: {', '.join(state.pending)}.")
    return lines


def _wait(loop: StudyLoop, state: _State, started: float,
          stop: threading.Event | None) -> str:
    """The readings landed since the model was last given any, waiting for one where none has:
    no longer than the study's own time, and not past `stop`."""
    state.drain()
    while not state.unsaid and state.pending:
        if (stop is not None and stop.is_set()) or (
                time.monotonic() - started > loop.max_seconds):
            return "nothing landed before the study's limit"
        try:
            name = state.landed.get(timeout=1.0)
        except queue.Empty:
            continue
        state.land(name)
        state.drain()
    if not state.unsaid:
        return "nothing is reading: queue a candidate with `read`, or recommend one"
    return "\n".join(_landed_said(state))


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
        if isinstance(item, str):
            # A lesson as words alone, as the real model gave one: its text, shown by the study.
            item = {"text": item, "shown_by": []}
        if not isinstance(item, dict):
            return ("refused: each lesson is {\"text\": ..., \"shown_by\": [candidates]}", None)
        shown = [str(n) for n in item.get("shown_by") or ()]
        if unknown := [n for n in shown if n not in names]:
            return (f"refused: a lesson's shown_by names {', '.join(unknown)}, not read", None)
        if not str(item.get("text", "")).strip():
            return ("refused: a lesson has its text", None)
        lessons.append({"text": str(item["text"]).strip(), "shown_by": shown})
    return (f"recommended: {chosen}", {"recommended": None if chosen == "none" else chosen,
                                        "reason": str(args["reason"]).strip(),
                                        "lessons": lessons})
