"""Use these: what decided the most similar past print, offered at the review.

mqucifer/print-shop#211. The Sponsor, 2026-11-21: "i would accept a use these option similar to
the take option - they can be pre-selected so long as its clear to read as to why. That feels
more like learning than me remembering".

- **The past prints most like this part are found without a model** (`similar.py`): the same
  bytes first, then closeness. The most similar is shown, with why, what differs and how it came
  out, and another can be picked (`like 2`).
- **What's offered is all that decided that print** (`_decided`): the person's corrections, the
  pause, the pose they sent back, what they took, their answers, and the lessons' settings that
  applied to it.
- **Each is pre-selected, unless it may not fit, and then it says why** (`_doubts`, and
  `Offering.standing` for what depends on this review): another filament for a temperature or
  fan, a pause off the part's feature, a feature it rests on that's far from that part's, a
  failed print whose symptom it bears on, a value corrected since.
- **Nothing is applied until it's used** (`use`, `review.parse_answer`). What's used is the
  person's correction, carrying the print it came from (`proposal.Used`). Carrying an override
  isn't a lesson accepted: those still go through the reviewer (`lessons.draw`).

A failed print is told by its result, never by a symptom, as a success may carry one
(mqucifer/print-shop#189).
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import trimesh

from print_shop.core.state import Orientation
from print_shop.materials import material_name
from print_shop.outcome_words import outcome_words
from print_shop.rounds.lessons import REJECTED, RETIRED
from print_shop.setup import similar
from print_shop.setup.bounds import MATERIAL, check
from print_shop.setup.context import PartContext, refuses
from print_shop.setup.features import Feature
from print_shop.setup.lessons import material_of
from print_shop.setup.proposal import ORIENTATION, PAUSE, Change, Proposal, Source, Used, pose_value
from print_shop.setup.reach import not_sliced, profile_of, unplaced
from print_shop.setup.similar import Likeness, Numbers
from print_shop.setup.traits import TRAITS, traits
from print_shop.sliced import same_setting

if TYPE_CHECKING:  # the ledger imports the proposal, and the graph imports this
    from print_shop.ledger import Ledger, Row

__all__ = ["Item", "Offering", "Past", "Standing", "ThisPart", "offering", "render"]

#: How many past prints can be picked from, the most similar first.
SHOWN = 5
#: A feature a setting rests on this far from that part's (`similar.apart`) puts it in doubt: one
#: part has it and the other hasn't, or has over twice as much.
FAR = 0.5
#: A part less alike in shape than this, and from no common source, has nothing pre-selected.
UNLIKE = 0.6
#: A pause is on a feature when a flat face of the part is this many layers from it, or nearer.
PAUSE_NEAR_LAYERS = 1.0
#: Two heights this near are one layer's top.
SAME_Z = 0.02
#: The review's width for the offer's lines, wrapped under their own indent.
WIDTH = 100
#: A face this near horizontal is flat.
_FLAT = 0.98

#: The measures a setting rests on, by its key's words: the first that matches.
RESTS_ON: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"support|overhang"), ("overhang",)),
    (re.compile(r"bridge"), ("bridge",)),
    (re.compile(r"brim|raft|plate_temp|elefant|exhaust|additional_cooling|"
                r"initial_layer|first_layer"), ("bed_contact", "length")),
    (re.compile(r"^top_|ironing"), ("flat_top",)),
    (re.compile(r"^bottom_"), ("bed_contact",)),
    (re.compile(r"wall|infill"), ("section",)),
)

#: The keys each recorded symptom bears on (`ledger.Symptom`). Adhesion and warping are the
#: print-settings skill's symptom map, with the first layer's own keys; the others are the levers
#: Orca has for each. A symptom not here (`other`) could be any setting's.
BEARS_ON: dict[str, re.Pattern[str]] = {
    "adhesion": re.compile(r"brim|raft|plate_temp|fan|chamber|elefant|initial_layer|"
                           r"first_layer|^orientation$"),
    "warping": re.compile(r"brim|raft|plate_temp|fan|chamber|elefant|initial_layer|"
                          r"first_layer|^orientation$"),
    "stringing": re.compile(r"nozzle_temp|retract|wipe|travel|z_hop|fan|flow"),
    "layer_shift": re.compile(r"speed|accel|jerk|retract|z_hop|travel|^orientation$"),
    "spaghetti": re.compile(r"support|bridge|overhang|brim|raft|plate_temp|speed|^orientation$"),
    "under_extrusion": re.compile(r"nozzle_temp|flow|speed|retract|line_width|layer_height"),
}

#: The measures a trait is read from, where that isn't a measure of its own.
_TRAIT_RESTS_ON = {"slenderness": ("height", "width"), "footprint_aspect": ("length", "width")}

#: Whose each was, as the offer names them.
_WHO = {Source.SPONSOR: "you", Source.READING: "you, taking the slice's", Source.ANSWER:
        "your answer", Source.LESSON: "lesson"}
#: What's the person's own: their corrections and what they took.
_PERSONS = (Source.SPONSOR, Source.READING)
#: What `review.parse_answer` records for a correction with no reason.
_NO_REASON = "your correction"


@dataclass(frozen=True)
class Item:
    """One thing that decided a past print, as it's offered to this one."""

    key: str
    value: str
    preset: str  # this print's preset's value
    reason: str  # the reason given then
    fits: str  # why it fits this file
    used: Used  # where it's from, as it's recorded once used
    #: Why it isn't pre-selected. It can still be used by its number.
    doubts: tuple[str, ...] = ()
    #: Why it can't be used at all.
    blocked: str = ""
    #: What's worth knowing that doesn't unselect it, such as a trait just missed.
    notes: tuple[str, ...] = ()
    reading: str = ""
    #: The pose it holds in, where using it goes with turning the part to that pose.
    pose: str = ""


@dataclass(frozen=True)
class Standing:
    """An item at this review: pre-selected, not, already so here, or not usable."""

    mark: Literal["x", " ", "=", "-"]
    why: tuple[str, ...] = ()

    @property
    def selected(self) -> bool:
        return self.mark == "x"


@dataclass(frozen=True)
class Past:
    """A past print as it's offered: which, why it's similar, what differs, how it came out."""

    job_id: str
    stl_name: str
    on: str
    came_out: str
    why: str
    differs: tuple[str, ...]
    items: tuple[Item, ...]
    #: Their answers then that set nothing, to read.
    answers: tuple[str, ...] = ()
    #: The lessons that print was given, and what its answers set: a value here from the same
    #: one was corrected then, so it doesn't put the correction in doubt.
    given: frozenset[int] = frozenset()
    answered: tuple[tuple[str, str], ...] = ()
    #: The same bytes: there are no differences for a model to read.
    same_file: bool = False

    def title(self) -> str:
        return f"{self.stl_name}, job {self.job_id[:8]}, {self.on}: {self.came_out}"


@dataclass(frozen=True)
class Offering:
    """The past prints on offer, which is picked, and what's been cleared."""

    options: tuple[Past, ...] = ()
    picked: int = 0
    cleared: frozenset[tuple[str, str]] = frozenset()  # (job, key)
    #: Why there's no offer, where reading the past prints failed.
    failed: str = ""
    #: What the model's reading of the differences came to (`doubts.py`), said under the offer.
    model: str = ""

    @property
    def past(self) -> Past | None:
        return self.options[self.picked] if self.options else None

    def like(self, n: int) -> Offering | str:
        if not 1 <= n <= len(self.options):
            return (f"like picks a print from 1 to {len(self.options)}" if self.options
                    else "there's no past print to pick")
        return replace(self, picked=n - 1)

    def clear(self, numbers: list[int]) -> Offering | str:
        items = self._numbered(numbers)
        if isinstance(items, str):
            return items
        assert self.past is not None
        return replace(self, cleared=self.cleared | {(self.past.job_id, i.key) for i in items})

    def carried_to(self, built: Offering) -> Offering:
        """`built` for another pose, with the print picked and what was cleared here kept."""
        job = self.past.job_id if self.past else ""
        at = next((n for n, p in enumerate(built.options) if p.job_id == job), 0)
        return replace(built, picked=at, cleared=self.cleared)

    def standing(self, item: Item, proposal: Proposal, pose: str) -> Standing:
        """An item against what's set at this review: what's already so, what's the person's
        since, and what was cleared."""
        past = self.past
        assert past is not None
        if item.key == ORIENTATION:
            if item.value == pose:
                return Standing("=", ("it's this print's pose already",))
        else:
            now = _now(proposal, item.key)
            if now is not None and _same(item.key, now.value, item.value):
                if now.used is not None and now.used.job_id == past.job_id:
                    return Standing("=", ("used",))
                if now.source in (*_PERSONS, Source.ANSWER, Source.LESSON):
                    return Standing("=", (f"{_set_by(now)} set it here already",))
        if item.blocked:
            return Standing("-", (item.blocked,))
        why = list(item.doubts)
        if item.key != ORIENTATION and now is not None and not _same(
            item.key, now.value, item.value
        ):
            if now.source in _PERSONS:
                why.append(f"you set it to {now.value} at this review")
            elif now.source is Source.ANSWER and (
                item.used.by not in _PERSONS or (item.key, now.value) not in past.answered
            ):
                why.append(f"your answer set it to {now.value} here")
            elif now.source is Source.LESSON and (
                item.used.by not in _PERSONS or now.lesson not in past.given
            ):
                why.append(f"lesson {now.lesson} sets it to {now.value} here")
        if item.pose and item.pose != pose:
            turn = next((i for i in past.items if i.key == ORIENTATION), None)
            if turn is None or not self.standing(turn, proposal, pose).selected:
                why.append(f"it held in the pose {item.pose}, and the turn to it isn't selected")
        if (past.job_id, item.key) in self.cleared:
            why.insert(0, "you cleared it")
        return Standing(" " if why else "x", tuple(why))

    def use(self, numbers: list[int], proposal: Proposal, pose: str) -> list[Item] | str:
        """The items `use` takes: those pre-selected, or those numbered, doubted or not."""
        past = self.past
        if past is None:
            return "there's no past print's settings to use"
        if not numbers:
            chosen = [i for i in past.items if self.standing(i, proposal, pose).selected]
            if not chosen:
                return "nothing is selected: name one by its number, such as use 1"
        else:
            named = self._numbered(numbers)
            if isinstance(named, str):
                return named
            chosen = []
            for item in named:
                if item.blocked:
                    return f"{item.key} can't be used: {item.blocked}"
                found = self.standing(item, proposal, pose)
                if found.why == ("used",) or (item.key == ORIENTATION and item.value == pose):
                    continue
                chosen.append(item)
            if not chosen:
                return "that's so here already"
        turning = any(i.key == ORIENTATION and i.value != pose for i in chosen)
        for item in chosen:
            # A setting may be used in another pose, by its number; a height is for its pose.
            if item.key == PAUSE and item.pose and item.pose != pose and not turning:
                return (f"{PAUSE}={item.value} is a height in the pose {item.pose}: use it with "
                        "the orientation")
        return chosen

    def _numbered(self, numbers: list[int]) -> list[Item] | str:
        past = self.past
        if past is None:
            return "there's no past print's settings to use"
        if not numbers:
            return list(past.items)
        if any(not 1 <= n <= len(past.items) for n in numbers):
            return f"a number from 1 to {len(past.items)}"
        return [past.items[n - 1] for n in dict.fromkeys(numbers)]


def _now(proposal: Proposal, key: str) -> Change | None:
    """What sets `key` at this review, or keeps it."""
    changed = (*proposal.changes, *proposal.not_applied)
    return next((c for c in (*changed, *proposal.kept) if c.key == key), None)


def _set_by(change: Change) -> str:
    if change.source is Source.LESSON:
        return f"lesson {change.lesson}"
    return "your answer" if change.source is Source.ANSWER else "you"


def _same(key: str, a: str, b: str) -> bool:
    return a == b or (key != ORIENTATION and same_setting(key, a, b))


# --------------------------------------------------------------------------
# Finding them
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ThisPart:
    """This print's part, in the pose it's being reviewed in, and its presets."""

    stl: Path  # posed as it's sliced
    stl_name: str
    sha256: str
    features: tuple[Feature, ...]
    rotation_deg: tuple[float, float, float]
    height_mm: float
    machine: dict[str, Any]
    process: dict[str, Any]
    filament: dict[str, Any]
    #: The filament's preset's name, as the store has it or the preset says.
    filament_names: tuple[str, ...] = ()
    candidates: tuple[Orientation, ...] = ()
    build_volume_mm: tuple[float, float, float] | None = None
    context: PartContext | None = None
    #: The person chose this pose for this print: kept as laid, or sent back at this review.
    pose_chosen: str = ""

    @property
    def pose(self) -> str:
        return pose_value(self.rotation_deg)

    @property
    def presets(self) -> dict[str, dict[str, Any]]:
        return {"machine": self.machine, "process": self.process, "filament": self.filament}


@dataclass(frozen=True)
class _Found:
    """A past print, read against this part."""

    at: int  # its place in the ledger, oldest first
    row: Row
    decided: dict[str, Change]
    same_file: bool
    likeness: Likeness
    #: This part's numbers in the pose it prints in, and that part's in the pose it printed in.
    #: The likeness may be read in that print's pose, where that's more alike.
    here: Numbers
    then: Numbers

    @property
    def related(self) -> bool:
        return self.same_file or self.likeness.source == 1 or self.likeness.shape >= UNLIKE

    @property
    def persons(self) -> bool:
        return any(c.source in _PERSONS for c in self.decided.values())

    def rank(self) -> tuple[bool, float, bool, bool, int]:
        """The same bytes, then closeness; of those alike, one that didn't fail, then one with
        the person's own overrides, then the newest."""
        return (not self.same_file, 0.0 if self.same_file else -round(self.likeness.score, 2),
                self.row.result == "failed" or _never_started(self.row), not self.persons,
                -self.at)


def offering(ledger: Ledger, part: ThisPart) -> Offering:
    """The past prints most like `part`, each with what decided it. No model is asked."""
    found = [_against(n, row, part) for n, row in enumerate(ledger.rows())
             if row.sliced is None]  # a hand-sliced file had nothing decided here
    offered = sorted(
        (f for f in found if any(c.value != "none" for c in f.decided.values())),
        key=_Found.rank,
    )[:SHOWN]
    return Offering(options=tuple(_past(ledger, f, found, part) for f in offered))


def _against(at: int, row: Row, part: ThisPart) -> _Found:
    then = similar.numbers(row.features or ())
    here = similar.numbers(part.features)
    shapes = [here]
    if row.rotation_deg != part.rotation_deg:
        shapes += [similar.posed(here, c) for c in part.candidates
                   if c.rotation_deg == row.rotation_deg]
    context = part.context or PartContext()
    proposal = row.proposal
    each = [
        similar.likeness(
            shape, then, tuple(context.lines()), proposal.context if proposal else (),
            (context.path, part.stl_name),
            (proposal.context_from if proposal else "", row.stl_name),
        )
        for shape in shapes
    ]
    best = max(each, key=lambda found: found.shape)
    return _Found(at=at, row=row, decided=_decided(row),
                  same_file=bool(part.sha256) and row.stl_sha256 == part.sha256,
                  likeness=best, here=here, then=then)


def _decided(row: Row) -> dict[str, Change]:
    """All that decided a print, by key, each the one that stood."""
    found: dict[str, Change] = {}
    proposal = row.proposal
    if proposal is not None:
        for c in proposal.kept:
            if c.source in (Source.ANSWER, Source.LESSON):
                found[c.key] = c
        for c in (*proposal.changes, *proposal.not_applied):
            if c.source in (*_PERSONS, Source.ANSWER, Source.LESSON):
                found[c.key] = c
            else:
                found.pop(c.key, None)
    standing = {c.key for c in (*proposal.changes, *proposal.not_applied)} if proposal else set()
    for key, c in {c.key: c for c in row.corrections or ()}.items():
        if key not in standing:  # a pose sent back, or a row from before the proposal was kept
            found[key] = c
    return found


def _never_started(row: Row) -> bool:
    return row.started_at is None and row.start_error is not None


def _came_out(row: Row) -> str:
    if row.result is not None:
        note = " ".join((row.note or "").split())
        return outcome_words(str(row.result), row.symptom) + (f' ("{note}")' if note else "")
    if _never_started(row):
        return "its start failed, so it may not have printed"
    return "no outcome yet"


def _past(ledger: Ledger, f: _Found, found: list[_Found], part: ThisPart) -> Past:
    row = f.row
    then_pose = pose_value(row.rotation_deg)
    decided = dict(f.decided)
    laid = row.orientation_source == "as_laid"  # `graph.AS_LAID`, which imports this
    if laid and ORIENTATION not in decided and then_pose != part.pose:
        decided[ORIENTATION] = Change(key=ORIENTATION, preset=part.pose, value=then_pose,
                                      reason="you kept it as laid", source=Source.SPONSOR)
    turn = decided.get(ORIENTATION)
    if turn is not None:
        # What stood is the pose it printed in: a later send-back may have replaced the first.
        decided[ORIENTATION] = turn.model_copy(update={"value": then_pose})
    to = _pose(part, then_pose)
    # The same file in the same pose, or one the offer itself turns it to: nothing to measure.
    turns = f.same_file and turn is not None and to is not None and then_pose != part.pose
    same_part = f.same_file and (then_pose == part.pose or turns)
    build = _Items(ledger, f, found, part, same_part, then_pose if turns else "")
    ordered = sorted((c for c in decided.values() if c.value != "none"), key=_order)
    if f.same_file:
        why = "the same file, byte for byte"
    elif not f.then:
        why = "by its name and context only: that part's shape wasn't kept"
    else:
        why = (f"{f.likeness.score:.0%} like this part: shape {f.likeness.shape:.0%}, "
               f"context {f.likeness.context:.0%}, source {f.likeness.source:.0%}")
    differs = [] if same_part else similar.differences(f.here, f.then)
    if then_pose != part.pose:
        differs.append(f"it printed in the pose {then_pose}, this is {part.pose}")
    if not same_part and row.features:
        had, has = set(traits(row.features)), set(traits(part.features))
        differs += [f"this isn't {t}, as that was ({TRAITS[t][1]})" for t in sorted(had - has)]
        differs += [f"this is {t}, and that wasn't ({TRAITS[t][1]})" for t in sorted(has - had)]
    if not f.same_file and f.likeness.context < 1:
        differs.append("its fits, holds or seen faces weren't these")
    names = part.filament_names
    if names and _filament_name(row) not in names:
        differs.append(f"its filament was {_filament_name(row) or 'not recorded'}, this is "
                       f"{names[0]}")
    intake = row.intake
    answers = []
    if intake is not None:
        for q, a in zip(intake.questions, intake.answers, strict=False):
            if a.choice is None:
                answers.append(f"{q.text} -> {a.other}")
            elif not q.options[a.choice].settings:
                answers.append(f"{q.text} -> {q.options[a.choice].label}")
    return Past(
        job_id=row.job_id, stl_name=row.stl_name, on=_day(row.start_requested_at),
        came_out=_came_out(row), why=why, differs=tuple(differs),
        items=tuple(build.item(c) for c in ordered), answers=tuple(answers),
        given=frozenset(g.id for g in row.proposal.lessons if not g.withheld)
        if row.proposal else frozenset(),
        answered=tuple((k, v) for _, o in intake.chosen() for k, v in o.settings.items())
        if intake is not None and len(intake.answers) == len(intake.questions) else (),
        same_file=f.same_file,
    )


def _order(change: Change) -> int:
    if change.key in (ORIENTATION, PAUSE):
        return (ORIENTATION, PAUSE).index(change.key)
    by = change.used.by if change.used else change.source
    return 2 + (*_PERSONS, Source.ANSWER, Source.LESSON).index(by)


def _pose(part: ThisPart, value: str) -> Orientation | None:
    """This part's candidate in that pose, if it fits the plate so."""
    found = next((c for c in part.candidates if pose_value(c.rotation_deg) == value), None)
    if found is None or (part.build_volume_mm and not found.fits_in(part.build_volume_mm)):
        return None
    return found


def _day(at: str) -> str:
    return datetime.fromisoformat(at).astimezone().strftime("%Y-%m-%d")


def _filament_name(row: Row) -> str:
    return row.profiles.filament.name if row.profiles is not None else row.filament


def _number(preset: dict[str, Any], key: str) -> float:
    try:
        return float(_first(preset.get(key, "0.2")) or 0.2)
    except ValueError:
        return 0.2


def _first(value: Any) -> str:
    return str(value[0] if isinstance(value, list) and value else value or "")


def _pause_height(row: Row, change: Change) -> tuple[str | None, str]:
    """The height to type for that print's pause, or None and why it isn't carried."""
    # Until mqucifer/print-shop#228 merges, which changes what a pause's height means: no
    # height is carried, as one copied raw would replay a layer late after it.
    return None, "a pause's height isn't carried yet (mqucifer/print-shop#228): type it"


@dataclass
class _Items:
    """What one past print's decisions come to for this part."""

    ledger: Ledger
    f: _Found
    found: list[_Found]
    part: ThisPart
    same_part: bool
    #: The pose the items hold in, when using them goes with turning the part to it.
    turned_to: str
    _config: dict[str, str] | None = None
    _levels: tuple[float, ...] | None = None

    def item(self, change: Change) -> Item:
        row, part, key = self.f.row, self.part, change.key
        origin = change.used
        by = origin.by if origin else change.source
        lesson = origin.lesson if origin else change.lesson
        used = Used(
            job_id=row.job_id, stl_name=row.stl_name, by=by, lesson=lesson,
            first_job_id=(origin.first_job_id or origin.job_id) if origin else "",
            first_stl_name=(origin.first_stl_name or origin.stl_name) if origin else "",
        )
        value, blocked, notes = change.value, "", []
        if key == ORIENTATION:
            preset = part.pose
            if value != part.pose and _pose(part, value) is None:
                blocked = "this part doesn't fit the plate in that pose, or can't be laid in it"
        elif key == PAUSE:
            preset = ""
            height, said = _pause_height(row, change)
            if height is None:
                blocked = said
            else:
                value = height
                if said:
                    notes.append(said)
                blocked = self._outside(value)
        else:
            tier = profile_of(key, part.process, part.filament)
            data = part.filament if tier == "filament" else part.process
            preset = _first(data.get(key, ""))
            blocked = unplaced(key, part.process, part.filament)
        doubts = [] if blocked else self._doubts(change, value, by)
        if lesson is not None and by is Source.LESSON:
            said_of, doubted = self._lesson(lesson, key, value)
            notes += said_of
            if not blocked:
                doubts += doubted
        return Item(
            key=key, value=value, preset=preset, reason=" ".join(change.reason.split()),
            fits=self._fits(key), used=used, doubts=tuple(dict.fromkeys(doubts)),
            blocked=blocked, notes=tuple(notes), reading=change.reading,
            pose="" if key == ORIENTATION else self.turned_to,
        )

    def _outside(self, value: str) -> str:
        height = self.part.height_mm
        if self.turned_to and (pose := _pose(self.part, self.turned_to)) is not None:
            height = pose.height_mm
        first = _number(self.part.process, "initial_layer_print_height")
        if not first - SAME_Z <= float(value) < height:
            return f"a pause at {value} mm is outside this part: it's {height:g} mm tall"
        return ""

    def _doubts(self, change: Change, value: str, by: Source) -> list[str]:
        row, part, f, key = self.f.row, self.part, self.f, change.key
        why = []
        if not f.related:
            why.append(f"the part is unlike it: its shape is {f.likeness.shape:.0%} alike")
        if key == ORIENTATION:
            if part.pose_chosen and part.pose != value:
                why.append(f"you chose this pose for this print ({part.pose_chosen})")
            elif not f.same_file:
                why.append("it's another file's pose, whose axes may not be this one's")
        elif key == PAUSE:
            why += self._pause(value)
        else:
            why += self._setting(change, value)
        if row.result == "failed":
            symptom = str(row.symptom or "")
            bears = BEARS_ON.get(symptom)
            if bears is None:
                why.append(f"that print failed ({_came_out(row)}), and which setting that bears "
                           "on isn't known")
            elif bears.search(key):
                why.append(f"that print failed with {symptom}, which this bears on")
        since = self._since(key, value)
        if since:
            why.append(since)
        return why

    def _setting(self, change: Change, value: str) -> list[str]:
        part, f, key = self.part, self.f, change.key
        why = []
        if change.not_applied:
            why.append(f"it didn't reach that print's slice: {change.not_applied}")
        else:
            # Rows from before mqucifer/print-shop#206 don't say, and their config block does.
            config = self._sliced_with()
            if key in config and (
                missed := not_sliced(key, value, part.process, part.filament, config)
            ):
                why.append(f"it didn't reach that print's slice: {missed}")
        if key in MATERIAL or profile_of(key, part.process, part.filament) == "filament":
            why += self._filament()
        if not self.same_part:
            rests = _rests_on(key)
            if rests and not f.then:
                why.append("that part's shape wasn't kept, so whether it fits can't be read")
            why += self._far(rests)
        # The machine's own limit. The other bounds are the model's, and the person's override
        # of one on the same file isn't in doubt for it.
        if key == "layer_height" and (wrong := check(key, value, part.presets)):
            why.append(f"it's past this nozzle's bound: {wrong}")
        if not self.same_part and (refused := refuses(part.context, key, value)):
            why.append(refused)
        return why

    def _filament(self) -> list[str]:
        row, part = self.f.row, self.part
        then = material_of(row.slicer_settings) or ""
        now = material_name(_first(part.filament.get("filament_type")))
        if then and now and then != now:
            return [f"another filament: that was {then}, this is {now}"]
        name = _filament_name(row)
        if not name or name not in part.filament_names:
            here = part.filament_names[0] if part.filament_names else now or "not known"
            return [f"another filament: that was {name or then or 'not known'}, this is {here}"]
        return []

    def _far(self, measures: tuple[str, ...]) -> list[str]:
        here, then = self.f.here, self.f.then
        return [f"{similar.measured(m, here[m])} here, and {similar.measured(m, then[m])} then"
                for m in measures
                if m in here and m in then and similar.apart(m, here[m], then[m]) > FAR]

    def _pause(self, value: str) -> list[str]:
        part, z = self.part, float(value)
        first = _number(part.process, "initial_layer_print_height")
        layer = _number(part.process, "layer_height")
        change = self.f.decided.get("layer_height")
        if change is not None:  # offered beside it
            layer = _number({"h": change.value}, "h")
        why = []
        then = self.f.row.slicer_settings.get("layer_height", "")
        steps = (z - first) / layer if layer else 0.0
        off_grid = abs(steps - round(steps)) * layer > SAME_Z
        if off_grid and then and not same_setting("layer_height", then, f"{layer:g}"):
            why.append(f"no layer tops out at {z:g} mm in {layer:g} mm layers (it was {then})")
        if not self.same_part:
            near = PAUSE_NEAR_LAYERS * layer + SAME_Z
            if not any(abs(level - z) <= near for level in self._flat_levels()):
                why.append(f"it's off this part's features: nothing starts or ends at {z:g} mm")
        return why

    def _since(self, key: str, value: str) -> str:
        """A later value of the person's on a print like this one."""
        row = self.f.row
        shape_bound = key in (PAUSE, ORIENTATION)  # a height or pose is for its own file
        for other in reversed(self.found):
            if other.at <= self.f.at:
                break
            if not (other.same_file if shape_bound else other.related):
                continue
            later = other.decided.get(key)
            if later is None or later.source not in _PERSONS:
                continue
            if key in MATERIAL and _filament_name(other.row) != _filament_name(row):
                continue
            set_to: str | None = later.value
            if key == PAUSE and later.value != "none":
                set_to, _ = _pause_height(other.row, later)
            if set_to is None or _same(key, set_to, value):
                return ""
            return (f"corrected since, to {set_to}, on {other.row.stl_name} "
                    f"({_came_out(other.row)})")
        return ""

    def _lesson(self, lesson: int, key: str, value: str) -> tuple[list[str], list[str]]:
        """A lesson that applied to that print: where it stands for this one, never silent."""
        rows = self.ledger.query(
            "SELECT status, setting, value, traits, became FROM print_lessons WHERE id=?", lesson
        )
        if not rows:
            return [], [f"lesson {lesson} isn't in the ledger now"]
        held = rows[0]
        if held["status"] in (RETIRED, REJECTED):
            return [], [f"lesson {lesson} has been {held['status']} since"]
        if held["setting"] != key or not _same(key, held["value"] or "", value):
            return [], [f"lesson {lesson} was edited since, to "
                        f"{held['setting'] or 'no setting'}={held['value'] or '-'}"]
        if held["became"]:
            return [f"lesson {lesson} is in the {held['became']} now, so it sets nothing by "
                    "itself"], []
        has = set(traits(self.part.features))
        missed = [t for t in json.loads(held["traits"]) if t not in has and t in TRAITS]
        if not missed:
            return [], []
        notes, doubts = [], []
        values = {x.id: x.value for x in self.part.features}
        for trait in missed:
            feature, means = TRAITS[trait]
            here = f": {feature} is {values[feature]:g} here" if feature in values else ""
            notes.append(f"lesson {lesson} doesn't reach this part by itself, as it isn't "
                         f"{trait} ({means}){here}")
            default = (feature,) if feature in similar.MEASURES else ()
            doubts += self._far(_TRAIT_RESTS_ON.get(feature, default))
        return notes, doubts

    def _fits(self, key: str) -> str:
        here, then = self.f.here, self.f.then
        if self.same_part or (key == ORIENTATION and self.f.same_file):
            return "it's the same file"
        if key == PAUSE:
            return f"this part is {self.part.height_mm:g} mm tall"
        for measure in _rests_on(key):
            if measure in here and measure in then:
                return f"{similar.measured(measure, here[measure])} here, {then[measure]:.10g} then"
        if self.f.same_file:
            return "it's the same file, in another pose"
        return f"the part is {self.f.likeness.score:.0%} like that one"

    def _sliced_with(self) -> dict[str, str]:
        if self._config is None:
            kept = self.ledger.query(
                "SELECT slicer_config FROM prints WHERE job_id=?", self.f.row.job_id
            )
            self._config = dict(json.loads(kept[0]["slicer_config"] or "{}")) if kept else {}
        return self._config

    def _flat_levels(self) -> tuple[float, ...]:
        """The heights of the part's flat faces as it prints, the plate's left out."""
        if self._levels is None:
            mesh = trimesh.load_mesh(str(self.part.stl), force="mesh")
            flat = abs(mesh.face_normals[:, 2]) > _FLAT
            z = mesh.triangles[flat][:, :, 2].mean(axis=1) - mesh.bounds[0][2]
            self._levels = tuple({round(float(v), 2) for v in z if v > SAME_Z})
        return self._levels


def _rests_on(key: str) -> tuple[str, ...]:
    return next((on for words, on in RESTS_ON if words.search(key)), ())


# --------------------------------------------------------------------------
# As the review shows it
# --------------------------------------------------------------------------


def render(offer: Offering, proposal: Proposal, pose: str) -> list[str]:
    """The offer, numbered, each item with its source, its reason and why it fits or may not."""
    if offer.failed:
        return ["", f"Use these: the past prints couldn't be read, so none is offered: "
                    f"{offer.failed}"]
    past = offer.past
    if past is None:
        return ["", "Use these: no past print has settings to offer."]
    which = ("the most similar past print" if offer.picked == 0
             else f"print {offer.picked + 1}, which you picked")
    lines = [
        "",
        f"Use these: what decided {which} (not applied unless you use it):",
        *_wrapped("  ", past.title()),
        *_wrapped("    similar: ", past.why),
    ]
    for d in past.differs or ("nothing",):
        lines += _wrapped("    differs: ", d)
    for n, item in enumerate(past.items, 1):
        found = offer.standing(item, proposal, pose)
        if item.key == ORIENTATION:
            here = pose
        else:
            now = _now(proposal, item.key)
            here = now.value if now is not None else item.preset
        moves = (item.value if _same(item.key, here, item.value)
                 else f"{here or '-'} -> {item.value}")
        lines.append(f"  [{found.mark}] {n}) {item.key}  {moves}")
        indent = " " * 9
        lines += _wrapped(indent, _line(item))
        for note in item.notes:
            lines += _wrapped(f"{indent}note: ", note)
        if found.why:
            label = {" ": "not selected: ", "-": "can't be used: "}.get(found.mark, "")
            lines += _wrapped(f"{indent}{label}", "; ".join(found.why))
    for answer in past.answers:
        lines += _wrapped("  you answered then: ", answer)
    if offer.model:
        lines.append(f"  {offer.model}")
    others = [(n, p) for n, p in enumerate(offer.options, 1) if n - 1 != offer.picked]
    if others:
        lines.append("  Other past prints, by how alike:")
        for n, p in others:
            lines += _wrapped(f"    like {n}: ", f"{p.title()}; {p.why}")
    lines.append("  use takes those marked [x], use 2 4 those numbered, clear 2 clears one"
                 + (", like 2 picks another print." if len(offer.options) > 1 else "."))
    return lines


def _wrapped(lead: str, text: str) -> list[str]:
    """One plain line, wrapped under its own start where it's longer than the review's width."""
    return textwrap.wrap(text, WIDTH, initial_indent=lead, subsequent_indent=" " * len(lead),
                         break_long_words=False, break_on_hyphens=False)


def _line(item: Item) -> str:
    """Whose it was, from which print, the reason given then, and why it fits this file."""
    u = item.used
    who = f"lesson {u.lesson}" if u.by is Source.LESSON else _WHO[u.by]
    first = (f" (first on {u.first_stl_name}, job {u.first_job_id[:8]})"
             if u.first_job_id and u.first_job_id != u.job_id else "")
    reason = "no reason given" if item.reason in ("", _NO_REASON) else item.reason
    return f"{who}, on {u.stl_name}{first}: {reason} (fits: {item.fits})"
