"""A print's settings, proposed over the chosen presets from what the part is and is for.

Print-flow stage 5's first recorded form (mqucifer/print-shop#7). The Sponsor
tunes presets; this proposes, per print, what to change from them. The
Sponsor, 2026-10-25: "It's all very dependent on the actual part ... This
goes back to my want for this to understand what it's doing." So:

- **the reasoning is per part,** from its measured features (`features.py`)
  and the intake, and each change names the feature, or the purpose, behind
  it ("the 2 mm tab carries the load, so 4 walls");
- **a skill, not defaults:** what each setting does and when to change it
  is the print-settings skill's (`skills/print-settings/`, loaded in layers
  by `skill.py`); code holds only the bounds every value must fall in
  (`bounds.py`);
- **the model reasons** (`model.py`), inside those bounds. A value outside
  them is refused and recorded as refused;
- **two guards** hold whatever the model says: a part with an overhang and
  no decision about supports gets them (an intake answer is a decision, even one that keeps
  the preset's value, mqucifer/print-shop#124), and supports are always tree (the
  Sponsor's practice). An overhang also gets a note that it could be
  designed out, for the CAD side;
- **the Sponsor's correction** at the review replaces any of it;
- **lessons from earlier prints,** each kept by a reviewer and keyed to the
  part's traits (mqucifer/print-shop#106, `lessons.py`), set their setting
  for a later part with the same traits, as an intake answer does, and the
  model reads them. The review says which lesson set what.

The changes are applied as whole copies of the process and filament
profiles, the way the plate is (`with_bed_type`), each in the profile it
belongs to (`reach.profile_of`). Then each is read back from the slice
(`read_back`): one that didn't reach it is never shown or kept as applied
(mqucifer/print-shop#206).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from print_shop.bench.pausing import TOPS_OUT
from print_shop.materials import material_name
from print_shop.setup.context import PartContext, refuses
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.reach import not_sliced, profile_of, unplaced
from print_shop.setup.skill import WARPS_MM2

if TYPE_CHECKING:  # the slicer imports the job state, which imports this
    from print_shop.slicer.orca import SlicerProfiles

__all__ = [
    "ORIENTATION",
    "PAUSE",
    "PAUSE_NAMES",
    "Change",
    "Ending",
    "GivenLesson",
    "LoopCall",
    "NotPlaced",
    "PartFacts",
    "Proposal",
    "Source",
    "Suggested",
    "Used",
    "answered",
    "answered_kept",
    "apply",
    "from_lessons",
    "guard",
    "pause_z",
    "placeable",
    "pose_value",
    "read_back",
    "reached",
]


class Source(StrEnum):
    ANSWER = "answer"  # the Sponsor's answer at the intake
    MODEL = "model"
    GUARD = "guard"  # a rule that holds whatever the model says
    SPONSOR = "sponsor"
    LESSON = "lesson"  # a reviewed lesson from an earlier print (mqucifer/print-shop#106)
    #: Read from the slice at the review, and taken there by the Sponsor (mqucifer/print-shop#105).
    READING = "reading"


class Used(BaseModel):
    """Where a correction came from, when the person used it from a past print
    (mqucifer/print-shop#211, `offer.py`)."""

    model_config = {"frozen": True}

    job_id: str  # the print it was used from
    stl_name: str
    by: Source  # whose it was: the person's correction, what they took, an answer, a lesson
    lesson: int | None = None
    #: The print it began on, where that print had used it from another.
    first_job_id: str = ""
    first_stl_name: str = ""

    def line(self) -> str:
        lesson = f", lesson {self.lesson}'s" if self.by is Source.LESSON else ""
        return f"used from {self.stl_name}, job {self.job_id[:8]}{lesson}"


class Change(BaseModel):
    """One setting changed from the preset, and why."""

    model_config = {"frozen": True}

    key: str
    preset: str  # the preset's own value, as Orca writes it
    #: Where the preset is the Sponsor's tuning of a standard (mqucifer/print-shop#8),
    #: the standard's value, so the review shows standard -> tuning -> this print.
    standard: str = ""
    value: str
    reason: str
    source: Source
    feature: str = ""  # the feature id behind it, "purpose", or the reading's ("layer_time")
    knowledge: str = ""  # the skill's reference group it cites
    lesson: int | None = None  # the lesson that set it, for a change from one
    #: What the slice showed that the change rests on, with its figures (a `Source.READING`).
    reading: str = ""
    #: Why it didn't reach the slice, as asked and sliced ("asked 30, sliced 80"), or "" when
    #: it did or hasn't been sliced yet (mqucifer/print-shop#206).
    not_applied: str = ""
    #: The past print it was used from, or None (mqucifer/print-shop#211).
    used: Used | None = None
    #: The model's proposal, taken by the person at the review (mqucifer/print-shop#242). One of
    #: the model's without it was applied unasked, as they were until then.
    taken: bool = False


class GivenLesson(BaseModel):
    """A reviewed lesson as a proposal was given it: its words, its setting, where it came from,
    and whether it set its setting here, or why not."""

    model_config = {"frozen": True}

    id: int
    text: str
    key: str = ""  # its setting, or "" for one that's only read
    value: str = ""
    job_id: str  # the print it was learned on
    stl_name: str
    traits: tuple[str, ...]  # what it's keyed to, which this part has
    reviewed_by: str
    applied: bool = False
    why: str = ""  # why it set nothing, when it didn't
    #: The filament type it holds for, for a setting that depends on it (`bounds.MATERIAL`), or
    #: "any" if a reviewer widened it; "" for a lesson that goes by shape alone.
    material: str = ""
    #: Why it wasn't given though the part's shape matched: the filament isn't its own
    #: (mqucifer/print-shop#117). The review lists it; nothing else reads it.
    withheld: str = ""
    #: The print loop looked it up with `past_prints` (mqucifer/print-shop#129): the review says so.
    looked_up: bool = False

    def line(self) -> str:
        """The lesson as the model and the review read it."""
        sets = f"{self.key}={self.value}: " if self.key else ""
        made = f", in {self.material}" if self.material and self.material != "any" else ""
        return (
            f"Lesson {self.id}, from {self.stl_name}, for a part that's "
            f"{', '.join(self.traits)}{made}, kept by {self.reviewed_by}: {sets}{self.text}"
        )


class LoopCall(BaseModel):
    """One tool call of the print loop (mqucifer/print-shop#129): what it called, with what, what
    came back, and how long it took. A proposal keeps them all, so the ledger has them."""

    model_config = {"frozen": True}

    tool: str
    arguments: dict[str, Any]
    result: str
    seconds: float


class Ending(StrEnum):
    """How the model's work on a pose's settings ended (mqucifer/print-shop#242)."""

    WORKING = "working"
    CAME = "came"
    CUT_OFF = "cut off"
    TIMED_OUT = "timed out"
    UNAVAILABLE = "unavailable"
    #: The person went on without it.
    WENT_ON = "went on"
    #: The part was turned to another pose first.
    TURNED = "turned"


class Suggested(BaseModel):
    """What the model proposed for one pose and what came of it: what the review shows and what
    the record keeps (mqucifer/print-shop#242, `suggest.py`)."""

    model_config = {"frozen": True}

    #: The pose it was worked out in.
    pose: tuple[float, float, float]
    ending: Ending = Ending.WORKING
    #: How it failed, in the client's words.
    said: str = ""
    seconds: float = 0.0
    #: Its proposals, in the order they're numbered: settings, or a turn of the print loop's
    #: (`ORIENTATION`). A big change's words are its `reading`.
    proposed: tuple[Change, ...] = ()
    #: The settings the loop chose with its turn, which are offered once the part is turned.
    once_turned: tuple[Change, ...] = ()
    #: What the person took of them, and what they turned down saying why, by key.
    taken: tuple[str, ...] = ()
    declined: tuple[str, ...] = ()
    reasoning: str = ""
    design_notes: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    model: str = ""
    call: dict[str, Any] = {}
    skill_groups: tuple[str, ...] = ()
    skill_sha256: str = ""
    loop: tuple[LoopCall, ...] = ()
    loop_ending: str = ""


class Proposal(BaseModel):
    """The changes for one print, the reasoning behind them, and what wasn't done."""

    model_config = {"frozen": True}

    changes: tuple[Change, ...] = ()
    #: The changes that didn't reach the slice, each saying why (`read_back`,
    #: mqucifer/print-shop#206). Once read back, none of them is in `changes`.
    not_applied: tuple[Change, ...] = ()
    #: Settings the model weighed and left at the preset's value, with why.
    kept: tuple[Change, ...] = ()
    #: How the part was read, in the model's words: what it is, what matters.
    reasoning: str = ""
    #: Where a change to the design would serve better than a setting.
    design_notes: tuple[str, ...] = ()
    #: Things the Sponsor should know that no setting answers ("PLA outdoors").
    notes: tuple[str, ...] = ()
    #: Model suggestions refused, each with why.
    refused: tuple[str, ...] = ()
    #: Which model reasoned: an alias, "" for none, or why it couldn't.
    model: str = ""
    #: The call's finish reason and tokens, reasoning tokens included (`CallInfo`).
    call: dict[str, Any] = {}
    #: The skill's reference groups the model read for this part, and the hash
    #: of what it read (SKILL.md and those references), so a proposal can be
    #: traced to the skill as it stood.
    skill_groups: tuple[str, ...] = ()
    skill_sha256: str = ""
    #: The reviewed lessons from earlier prints this part was given (mqucifer/print-shop#106).
    lessons: tuple[GivenLesson, ...] = ()
    #: The print loop's tool calls, in order, and how it ended (mqucifer/print-shop#129). Empty
    #: for a proposal from the single calls.
    loop: tuple[LoopCall, ...] = ()
    loop_ending: str = ""
    #: What the model proposed at this job's reviews, a pose at a time, with what was taken and
    #: how each ended (mqucifer/print-shop#242). () in a row from before, whose model's changes
    #: were applied unasked.
    suggested: tuple[Suggested, ...] = ()
    #: The part's context as it was read, from its design run or a sidecar, in lines
    #: (mqucifer/print-shop#130), or why there was none.
    context: tuple[str, ...] = ()
    #: The sidecar or the design run's folder that context was read from, or "": what a run of
    #: the job again names with `--context`, wherever its part is found.
    context_from: str = ""

    def get(self, key: str) -> Change | None:
        return next((c for c in self.changes if c.key == key), None)

    def corrected(self, corrections: list[Change]) -> Proposal:
        """This proposal with the Sponsor's corrections in place of what they correct. Of two
        for one setting the last stands: it's the one written."""
        last = {c.key: c for c in corrections}
        kept = tuple(c for c in self.changes if c.key not in last)
        return self.model_copy(update={"changes": kept + tuple(last.values())})


@dataclass(frozen=True)
class PartFacts:
    """What the proposal reads: the intake, the features, and the presets."""

    intake: Intake
    features: tuple[Feature, ...]
    machine: dict[str, Any]
    process: dict[str, Any]
    filament: dict[str, Any]
    #: The standards the process and filament tune, when they are tunings.
    standard_process: dict[str, Any] = field(default_factory=dict)
    standard_filament: dict[str, Any] = field(default_factory=dict)
    #: The reviewed lessons this part was given, each marked with whether it set its setting.
    lessons: tuple[GivenLesson, ...] = ()
    #: The part's context from its design run or a sidecar (mqucifer/print-shop#130), or None.
    context: PartContext | None = None
    #: The pose the features are read in, which what's seen is carried to
    #: (mqucifer/print-shop#248). None: not known.
    pose: tuple[float, float, float] | None = None

    def feature(self, id: str) -> float:
        return next((f.value for f in self.features if f.id == id), 0.0)

    def preset(self, key: str) -> str:
        tier = profile_of(key, self.process, self.filament)
        if tier is None:
            return ""
        return _first((self.filament if tier == "filament" else self.process).get(key, ""))

    def standard(self, key: str) -> str:
        """The standard's value for `key`, or '' when the preset is the standard itself."""
        tier = profile_of(key, self.process, self.filament)
        if tier is None:
            return ""
        data = self.standard_filament if tier == "filament" else self.standard_process
        return _first(data.get(key, ""))

    @property
    def presets(self) -> dict[str, dict[str, Any]]:
        return {"machine": self.machine, "process": self.process, "filament": self.filament}

    @property
    def filament_type(self) -> str:
        return material_name(_first(self.filament.get("filament_type", "")))


def answered(facts: PartFacts) -> list[Change]:
    """The settings of each option the Sponsor chose at the intake, as changes."""
    return [c for c in _answers(facts) if c.value != c.preset]


def answered_kept(facts: PartFacts) -> list[Change]:
    """The settings an option the Sponsor chose sets to the preset's own value.

    A decision all the same (mqucifer/print-shop#124): "Bridge it — no support" over a preset
    with supports off changes nothing, and it's still the person's answer, so the guard leaves
    it and the review says it's kept because of it.
    """
    return [c for c in _answers(facts) if c.value == c.preset]


def _answers(facts: PartFacts) -> list[Change]:
    """Each setting the chosen options set, as a change from the preset; a later answer's value
    for the same setting wins."""
    changes: dict[str, Change] = {}
    for question, option in facts.intake.chosen():
        for key, value in option.settings.items():
            changes[key] = Change(
                key=key, preset=facts.preset(key), value=value, source=Source.ANSWER,
                reason=f"you chose '{option.label}': {option.impact}",
                feature=question.feature,
            )
    return list(changes.values())


def from_lessons(
    facts: PartFacts, given: tuple[GivenLesson, ...]
) -> tuple[list[Change], list[Change], tuple[GivenLesson, ...]]:
    """The settings the lessons set: changes, those that keep the preset's value (a decision all
    the same, so the guard leaves it), and each lesson marked with whether it set its setting.

    An intake answer comes first, as it's the Sponsor's for this part. Of two lessons for one
    setting, the one keyed to more traits sets it, then the newer. A setting that can't be
    placed in a profile sets nothing, and says why (mqucifer/print-shop#206).
    """
    answered_keys = {k for _, o in facts.intake.chosen() for k in o.settings}
    order = sorted(
        (g for g in given if g.key and not g.withheld), key=lambda g: (-len(g.traits), -g.id)
    )
    setting: dict[str, GivenLesson] = {}
    for g in order:
        setting.setdefault(g.key, g)
    changes: list[Change] = []
    kept: list[Change] = []
    marked = []
    for g in given:
        if g.withheld:
            marked.append(g.model_copy(update={"applied": False, "why": g.withheld}))
            continue
        if not g.key:
            marked.append(g.model_copy(update={"why": "read by the model; it sets nothing"}))
            continue
        if g.key in answered_keys:
            marked.append(g.model_copy(update={"why": "your intake answer set it"}))
            continue
        if why := unplaced(g.key, facts.process, facts.filament):
            marked.append(g.model_copy(update={"why": f"it set nothing: {why}"}))
            continue
        if setting[g.key].id != g.id:
            marked.append(g.model_copy(
                update={"why": f"lesson {setting[g.key].id}, keyed closer, set it"}
            ))
            continue
        preset = facts.preset(g.key)
        change = Change(
            key=g.key, preset=preset, value=g.value, source=Source.LESSON, lesson=g.id,
            reason=f"learned on {g.stl_name}: {g.text}", feature=", ".join(g.traits),
        )
        same = _same_value(g.value, preset)
        (kept if same else changes).append(change)
        marked.append(g.model_copy(update={"applied": True}))
    return changes, kept, tuple(marked)


def _same_value(value: str, preset: str) -> bool:
    try:
        return float(value.rstrip("%")) == float(preset.rstrip("%"))
    except ValueError:
        return value == preset


def guard(facts: PartFacts, proposal: Proposal) -> Proposal:
    """The rules that hold whatever the model proposed, and the notes for the Sponsor."""
    changes = list(proposal.changes)
    overhang = facts.feature("overhang")

    def value(key: str) -> str:
        change = next((c for c in changes if c.key == key), None)
        return change.value if change else facts.preset(key)

    decided = {c.key for c in changes} | {c.key for c in proposal.kept}
    if overhang > 0 and value("enable_support") != "1" and "enable_support" not in decided:
        changes.append(Change(
            key="enable_support", preset=facts.preset("enable_support"), value="1",
            reason=f"the {overhang:g} mm2 overhang would print into the air, and nothing "
                   "decided otherwise",
            source=Source.GUARD, feature="overhang", knowledge="supports"))
    if value("enable_support") == "1" and value("support_type") != "tree(auto)":
        changes = [c for c in changes if c.key != "support_type"]
        changes.append(Change(
            key="support_type", preset=facts.preset("support_type"), value="tree(auto)",
            reason="tree supports, as you use them", source=Source.GUARD,
            feature="overhang", knowledge="supports"))

    # A part with a fit never gets a random seam from the model or a lesson
    # (mqucifer/print-shop#130); the person's own answer or correction stands.
    refused = list(proposal.refused)
    for c in [c for c in changes if c.source in (Source.MODEL, Source.LESSON)]:
        why = refuses(facts.context, c.key, c.value)
        if why:
            changes.remove(c)
            refused.append(f"{c.key}={c.value}: {why}")

    design = list(proposal.design_notes)
    if overhang > 0:
        design.insert(0, (
            f"{overhang:g} mm2 overhangs, up to {facts.feature('steepest_overhang'):g} degrees "
            "past vertical: a chamfer to 45 degrees or a support designed into the part "
            "would print without Orca's supports. That's a design change, for the CAD side."
        ))
    def with_standard(c: Change) -> Change:
        return c.model_copy(update={"standard": facts.standard(c.key)})

    return proposal.model_copy(update={
        "changes": tuple(with_standard(c) for c in changes),
        "kept": tuple(with_standard(c) for c in proposal.kept),
        "design_notes": tuple(design),
        "notes": proposal.notes + tuple(_notes(facts)),
        "refused": tuple(refused),
    })


def _notes(f: PartFacts) -> list[str]:
    notes = []
    low = _first(f.filament.get("nozzle_temperature_range_low", ""))
    high = _first(f.filament.get("nozzle_temperature_range_high", ""))
    temp = f.preset("nozzle_temperature")
    try:
        if not float(low) <= float(temp) <= float(high):
            notes.append(
                f"the filament preset's own nozzle temperature, {temp} C, is outside its "
                f"maker's range of {low}-{high} C"
            )
    except ValueError:
        pass
    contact = f.feature("bed_contact")
    if contact >= WARPS_MM2:
        # A risk the settings answer, never the orientation (mqucifer/print-shop#128): named by
        # Orca's own keys, the plate's for the plate it's sliced on, as the skill's map has them.
        plate = _first(f.process.get("curr_bed_type", "Cool Plate"))
        temp = "textured_plate_temp" if plate == "Textured PEI Plate" else "cool_plate_temp"
        notes.append(
            f"warping risk: {contact:g} mm2 on the plate. The plate's temperature ({temp}), a "
            "brim (brim_type), the chamber's exhaust (during_print_exhaust_fan_speed) and the side "
            "fan (additional_cooling_fan_speed) answer it, not turning the part"
        )
    return notes


#: A pause at a height, asked for at the review: a colour change (mqucifer/print-shop#119), or
#: parts dropped in. It comes after the layer that tops out at that height, as the bench's
#: `pause` gives it (`bench.pausing`, mqucifer/print-shop#203). It's no preset's setting but
#: Orca's own pause, from the machine's pause G-code (`M600` on the Centauri Carbon), so `apply`
#: makes it one. Each name here is the same thing; `none` removes it.
PAUSE = "pause_at"
PAUSE_NAMES = (PAUSE, "colour_change", "color_change")


#: The pose sent back at the review (mqucifer/print-shop#128): a correction, recorded and drawn
#: as a lesson like any, that turns the part rather than setting anything in a preset. Its value
#: is the rotation chosen (`pose_value`); `apply` never sees it.
ORIENTATION = "orientation"


def pose_value(rotation_deg: tuple[float, float, float]) -> str:
    """A rotation as a correction's value: "0,90,0"."""
    return ",".join(f"{a:g}" for a in rotation_deg)


def pause_z(process: dict[str, Any], z: float) -> float:
    """What Orca is given to pause after the layer that tops out at `z`: the next layer's top,
    from the process's own layer heights (mqucifer/print-shop#203).

    Orca puts a pause on the layer whose top is nearest the height it's given, a tie going to
    the lower, and runs it before that layer prints (`PauseAt`). So it's the first layer topping
    out above `z`: everything up to `z` is under the pause, and that layer is the first over it.
    Where no layer tops out at `z`, the review says so from the G-code.
    """
    first = float(_first(process.get("initial_layer_print_height", "0.2")) or 0.2)
    height = float(_first(process.get("layer_height", "0.2")) or 0.2)
    steps = max(0, math.floor((z + TOPS_OUT - first) / height) + 1)
    return round(first + steps * height, 3)


class NotPlaced(ValueError):  # noqa: N818 - a refusal, said as what happened
    """A change whose key can't be placed in a profile, so nothing was written."""


def placeable(proposal: Proposal, process: dict[str, Any], filament: dict[str, Any]) -> Proposal:
    """The proposal with only the changes `apply` can write. `read_back` says what was left
    out, and why."""
    return proposal.model_copy(update={"changes": tuple(
        c for c in proposal.changes if c.key == PAUSE or not unplaced(c.key, process, filament)
    )})


def reached(
    change: Change, process: dict[str, Any], filament: dict[str, Any],
    config: dict[str, str] | None,
) -> Change:
    """The change marked with why it didn't reach the slice whose config block is `config`, or
    with nothing when it did. A colour change isn't a setting: the slice's reading has it."""
    why = "" if change.key == PAUSE else not_sliced(
        change.key, change.value, process, filament, config
    )
    return change.model_copy(update={"not_applied": why})


def read_back(
    asked: Proposal, process: dict[str, Any], filament: dict[str, Any],
    config: dict[str, str] | None,
) -> Proposal:
    """`asked` as its slice has it (mqucifer/print-shop#206): the changes that reached it, those
    that didn't, each saying why, and a lesson whose change didn't marked as having set nothing.

    `asked` is still what's sliced again after a correction: dropping a change Orca took at
    another value would slice something else than what was reviewed.
    """
    read = [reached(c, process, filament, config) for c in asked.changes]
    missed = tuple(c for c in read if c.not_applied)
    unset = {c.lesson: c.not_applied for c in missed if c.source is Source.LESSON}
    return asked.model_copy(update={
        "changes": tuple(c for c in read if not c.not_applied),
        "not_applied": missed,
        "lessons": tuple(
            g.model_copy(update={"applied": False,
                                 "why": f"it didn't reach the slice: {unset[g.id]}"})
            if g.id in unset else g
            for g in asked.lessons
        ),
    })


def apply(profiles: SlicerProfiles, proposal: Proposal, workdir: Path) -> SlicerProfiles:
    """`profiles` with the changes written into whole copies of the process and filament.

    Each is written to the profile it belongs to (`reach.profile_of`). A filament setting is a
    per-extruder list in Orca's JSON; the CC1 has one. A pause (`PAUSE`) is Orca's own, after
    the layer that tops out at its height, by the layer heights these changes leave.

    Raises:
        NotPlaced: If a change's key can't be placed in a profile. Nothing is written: not to
            the process by default, and not the changes beside it.
    """
    from print_shop.slicer.orca import PauseAt  # noqa: PLC0415 - the slicer imports this

    process = json.loads(profiles.process.read_text())
    filament = json.loads(profiles.filament.read_text()) if profiles.filament else {}
    asked: list[Change] = []
    # Placed by the presets as they were, before this writes to either.
    tiers = {c.key: profile_of(c.key, process, filament) for c in proposal.changes}
    refused = [f"{c.key}={c.value}: {unplaced(c.key, process, filament)}"
               for c in proposal.changes if c.key != PAUSE and tiers[c.key] is None]
    if refused:
        raise NotPlaced("; ".join(refused))
    for change in proposal.changes:
        if change.key == PAUSE:
            if change.value != "none":
                asked.append(change)
            continue
        if tiers[change.key] == "filament":
            filament[change.key] = [change.value]
        else:
            process[change.key] = change.value
    # Last, as a layer height changed beside a pause moves the layer after it.
    pauses = [PauseAt(print_z=pause_z(process, float(c.value)), message=c.reason) for c in asked]
    workdir.mkdir(parents=True, exist_ok=True)
    out_process = workdir / "process_proposed.json"
    out_process.write_text(json.dumps(process, indent=2))
    update: dict[str, Any] = {"process": out_process, "pauses": tuple(pauses)}
    if profiles.filament is not None:
        out_filament = workdir / "filament_proposed.json"
        out_filament.write_text(json.dumps(filament, indent=2))
        update["filament"] = out_filament
    return profiles.model_copy(update=update)


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)
