"""Use these: what decided the most similar past print, offered at the review.

mqucifer/print-shop#211. The Sponsor, 2026-11-21: "i would accept a use these option similar to
the take option - they can be pre-selected so long as its clear to read as to why. That feels
more like learning than me remembering".

- **The past prints most like this part are found without a model** (`similar.py`): the same
  bytes first, then closeness. The most similar is shown, with why, what differs and how it came
  out, and another can be picked (`from 2`).
- **What's offered is all that decided that print** (`_decided`): the person's corrections, the
  pause, the pose they sent back, what they took, their answers, and the lessons' settings that
  applied to it.
- **Each is pre-selected, unless it may not fit, and then it says why** (`_doubts`, and
  `Offering.standing` for what depends on this review): another filament for a temperature or
  fan, a pause off the part's feature, a feature it rests on that's far from that part's, a
  failed print whose symptom it bears on, a value corrected since. A past intake answer isn't
  pre-selected, as each job asks again, but on the same bytes where this job didn't ask.
- **It's laid out to be read** (`render`): the print and how it came out once, what differs in
  columns, the items grouped by whose they were, and a reason several share said once.
- **A pause's height is converted, never copied raw** (`_pause_height`): a row from before
  mqucifer/print-shop#228 gives the height that pauses where its own G-code did, and one whose
  G-code doesn't say where it paused can't be used.
- **Nothing is applied until it's used** (`use`, `review.parse_answer`). What's used is the
  person's correction, carrying the print it came from (`proposal.Used`). Carrying an override
  isn't a lesson accepted: those still go through the reviewer (`lessons.draw`).

A failed print is told by its result, never by a symptom, as a success may carry one
(mqucifer/print-shop#189).

**A plate of several parts is one print among them** (mqucifer/print-shop#109), shown by the part
of it most like this one. Its order was that plate's choice and is never offered: by object
carried to one part would lose its pause. Its settings and pause were all its parts', so a pause
from it isn't pre-selected, and nor is a setting a failed part of it bears on.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import trimesh

from print_shop.core.state import Orientation
from print_shop.materials import material_name
from print_shop.outcome_words import outcome_words
from print_shop.plate import SEQUENCE, plate_job
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
#: A feature this near that part's is about the same, and is said as why a setting fits.
NEAR = 0.2
#: A part less alike in shape than this, and from no common source, has nothing pre-selected.
UNLIKE = 0.6
#: Two heights this near are one layer's top. A pause is on another file's feature only where a
#: flat face is at its height: a layer off is mqucifer/print-shop#203's fault again.
SAME_Z = 0.02
#: Why a past intake answer isn't pre-selected. Its group's heading says it, once.
ASKED_AGAIN = "you're asked again for each job"
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
    (re.compile(r"^(wall_loops|sparse_infill_density|sparse_infill_pattern)$"), ("section",)),
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
    fits: str  # why it fits this file, where that's particular to this setting
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
    #: How it came out without the note, for where the note's been said.
    outcome: str
    why: str
    #: The measures that differ, the most first: its name, then, now, and a trait it moves across.
    measures: tuple[tuple[str, float, float, str], ...]
    #: What else differs: the pose, a trait, the context, the filament.
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

    def all_differences(self) -> list[str]:
        """Every difference in a line, as the model is given them."""
        return [f"{similar.measured(name, now)} now, {similar.figure(then)} then"
                + (f" ({line})" if line else "")
                for name, then, now, line in self.measures] + list(self.differs)


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

    def pick(self, n: int) -> Offering | str:
        if not 1 <= n <= len(self.options):
            return (f"from picks a print from 1 to {len(self.options)}" if self.options
                    else "there's no past print to pick")
        return replace(self, picked=n - 1)

    def clear(self, numbers: list[int]) -> Offering | str:
        items = self._numbered(numbers)
        if isinstance(items, str):
            return items
        assert self.past is not None
        job = plate_job(self.past.job_id)
        return replace(self, cleared=self.cleared | {(job, i.key) for i in items})

    def carried_to(self, built: Offering) -> Offering:
        """`built` for another pose, with the print picked and what was cleared here kept."""
        # By the print: turned, another part of the same plate may be the one most like it.
        job = plate_job(self.past.job_id) if self.past else ""
        at = next((n for n, p in enumerate(built.options) if plate_job(p.job_id) == job), 0)
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
                if now.used is not None and plate_job(now.used.job_id) == plate_job(past.job_id):
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
        if (plate_job(past.job_id), item.key) in self.cleared:
            why.insert(0, "you cleared it")
        if item.key != ORIENTATION and (why or item.used.by not in _PERSONS):
            here = now.value if now is not None else item.preset
            if here and _same(item.key, here, item.value):
                # There's nothing to choose. The person's own that's ticked stays so, as using
                # it keeps it theirs for the print after.
                return Standing("=", (f"it's {here} here already",))
        return Standing(" " if why else "x", tuple(why))

    def use(self, numbers: list[int], proposal: Proposal, pose: str) -> list[Item] | str:
        """The items `use` takes: those pre-selected, or those numbered, doubted or not."""
        past = self.past
        if past is None:
            return "there's no past print's settings to use"
        if not numbers:
            chosen = [i for i in past.items if self.standing(i, proposal, pose).selected]
            if not chosen:
                return "nothing is ticked: name one by its number, such as use 1"
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

    def ticked(self, proposal: Proposal, pose: str) -> list[Item]:
        """The items pre-selected and not used: what a plain ok goes on without."""
        past = self.past
        if past is None:
            return []
        return [i for i in past.items if self.standing(i, proposal, pose).selected]

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
    others = {Source.ANSWER: "your answer", Source.MODEL: "the model", Source.GUARD: "a guard"}
    return others.get(change.source, "you")


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
    #: A pose by its value, waited for where it's still being measured
    #: (mqucifer/print-shop#235). None reads `candidates`.
    measured: Callable[[str], Orientation | None] | None = None
    build_volume_mm: tuple[float, float, float] | None = None
    context: PartContext | None = None
    #: The settings this job's questions could set: what it asked again.
    asked: frozenset[str] = frozenset()
    #: The person chose this pose for this print: kept as laid, or sent back at this review.
    pose_chosen: str = ""

    @property
    def pose(self) -> str:
        return pose_value(self.rotation_deg)

    def lying(self, value: str) -> Orientation | None:
        """The part's figures in that pose."""
        if self.measured is not None:
            return self.measured(value)
        return next((c for c in self.candidates if pose_value(c.rotation_deg) == value), None)

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
    ranked = sorted(
        (f for f in found if any(c.value != "none" for c in f.decided.values())),
        key=_Found.rank,
    )
    prints: dict[str, _Found] = {}  # a plate is one print, by its part most like this one
    for f in ranked:
        prints.setdefault(plate_job(f.row.job_id), f)
    offered = list(prints.values())[:SHOWN]
    return Offering(options=tuple(_past(ledger, f, found, part) for f in offered))


def _against(at: int, row: Row, part: ThisPart) -> _Found:
    then = similar.numbers(row.features or ())
    here = similar.numbers(part.features)
    shapes = [here]
    if row.rotation_deg != part.rotation_deg:
        there = part.lying(pose_value(row.rotation_deg))
        shapes += [similar.posed(here, there)] if there is not None else []
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
    if row.plate is not None:
        found.pop(SEQUENCE, None)  # that plate's choice, as a lesson never takes it
    return found


def _never_started(row: Row) -> bool:
    return row.started_at is None and row.start_error is not None


def _came_out(row: Row) -> str:
    note = " ".join((row.note or "").split()) if row.result is not None else ""
    return _outcome(row) + (f' ("{note}")' if note else "")


def _outcome(row: Row) -> str:
    if row.result is not None:
        return outcome_words(str(row.result), row.symptom)
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
        like = f.likeness
        why = (f"{100 * like.score:.0f}% (shape {100 * like.shape:.0f}, "
               f"context {100 * like.context:.0f}, source {100 * like.source:.0f})"
               + (", the same name or run" if like.source == 1 else ""))
    moved = [] if same_part else similar.differences(f.here, f.then)
    differs: list[str] = []
    if row.plate is not None:
        count, sequence = len(row.plate.parts), row.plate.layout.sequence
        differs.append(f"it was part {row.plate.n} of {count} on one plate, printed {sequence}: "
                       "its settings were all of theirs")
    crossed: dict[str, list[str]] = {}
    if then_pose != part.pose:
        differs.append(f"it printed in the pose {then_pose}, this is {part.pose}")
    if not same_part and row.features:
        had, has = set(traits(row.features)), set(traits(part.features))
        for trait in sorted(had ^ has):
            feature, means = TRAITS[trait]
            said = f"{'not ' if trait in had else ''}{trait} now: {means}"
            if feature in moved:
                crossed.setdefault(feature, []).append(said)
            else:
                differs.append(said)
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
        came_out=_came_out(row), outcome=_outcome(row), why=why, differs=tuple(differs),
        measures=tuple((m, f.then[m], f.here[m], "; ".join(crossed.get(m, ()))) for m in moved),
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
    return 2 + (*_PERSONS, Source.LESSON, Source.ANSWER).index(by)


def _pose(part: ThisPart, value: str) -> Orientation | None:
    """This part's candidate in that pose, if it fits the plate so."""
    found = part.lying(value)
    if found is None or (part.build_volume_mm and not found.fits_in(part.build_volume_mm)):
        return None
    return found


def _day(at: str) -> str:
    when = datetime.fromisoformat(at).astimezone()
    return f"{when.day} {when:%b %Y}"


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
    """The height to type for that print's pause, with a note where it was converted, or None
    and why it isn't carried. Never copied raw: a row from before mqucifer/print-shop#228 has
    another meaning of a height, and replayed raw it pauses a layer late."""
    from print_shop.desk.again import _pause_again  # noqa: PLC0415 - the desk imports the graph

    height = _pause_again(row, change)
    if height is None:
        return None, ("that print's G-code doesn't say where it paused, so its "
                      f"{change.value} mm isn't carried: type the top of the layer to pause after")
    if height == change.value:
        return height, ""
    return height, (f"{change.value} mm then is {height} mm now, to pause where that print did: "
                    "a pause's height is now the top of the layer it comes after")


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
            fits="" if blocked or doubts else self._fits(key, value),
            used=used, doubts=tuple(dict.fromkeys(doubts)),
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
        if by is Source.ANSWER and (not f.same_file or key in part.asked):
            why.append(ASKED_AGAIN)
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
                why.append(f"that print failed ({symptom or 'no symptom'}), and which setting "
                           "that bears on isn't known")
            elif bears.search(key):
                why.append(f"that print failed with {symptom}, which this bears on")
        if row.plate is not None:
            why += self._plates(key)
        since = self._since(key, value)
        if since:
            why.append(since)
        return why

    def _plates(self, key: str) -> list[str]:
        """What's in doubt as it was decided for a whole plate (mqucifer/print-shop#109)."""
        row = self.f.row
        assert row.plate is not None
        if key == ORIENTATION:
            return []  # each part's pose was its own
        if key == PAUSE:
            return [f"it paused all {len(row.plate.parts)} parts of a plate, so it may have been "
                    "another part's"]
        why = []
        for other in self.found:
            beside = other.row
            if (beside.job_id == row.job_id or beside.result != "failed"
                    or plate_job(beside.job_id) != row.plate.job_id):
                continue
            bears = BEARS_ON.get(str(beside.symptom or ""))
            if bears is None:
                why.append(f"{beside.part_said} of that plate failed ({beside.symptom}), and "
                           "which setting that bears on isn't known")
            elif bears.search(key):
                why.append(f"{beside.part_said} of that plate failed with {beside.symptom}, "
                           "which this bears on")
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
        return [f"its {similar.MEASURES[m].label} is too far from that part's"
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
            why += self._off_feature(z, first, layer)
        return why

    def _off_feature(self, z: float, first: float, layer: float) -> list[str]:
        """Why a pause isn't at a flat face of this part. A face within a layer of it is named
        with the height to type, and the value is left as it was: choosing it is the person's."""
        levels = self._flat_levels()
        if any(abs(level - z) <= SAME_Z for level in levels):
            return []
        near = sorted(
            (level for level in levels
             if abs(level - z) <= layer + SAME_Z and level < self.part.height_mm - SAME_Z),
            key=lambda level: (abs(level - z), -level),
        )
        if not near:
            return [f"it's off this part's features: nothing starts or ends at {z:g} mm"]
        face = near[0]
        steps = (face - first) / layer if layer else 0.0
        if steps >= 0 and abs(steps - round(steps)) * layer <= SAME_Z:
            how = f"{PAUSE}={face:g} pauses there"
        else:
            how = f"no layer tops out at {face:g}, so check where it's to pause"
        return [f"this part's face is at z {face:g}, not {z:g}: {how}"]

    def _since(self, key: str, value: str) -> str:
        """A later value of the person's on a print like this one."""
        row = self.f.row
        shape_bound = key in (PAUSE, ORIENTATION)  # a height or pose is for its own file
        for other in reversed(self.found):
            if other.at <= self.f.at:
                break
            if plate_job(other.row.job_id) == plate_job(row.job_id):
                continue  # the parts beside it on its plate aren't later prints
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
                    f"({_outcome(other.row)})")
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
            feature, _ = TRAITS[trait]
            here = f" ({feature} {values[feature]:g} here)" if feature in values else ""
            notes.append(f"doesn't reach this part by itself: it isn't {trait}{here}")
            default = (feature,) if feature in similar.MEASURES else ()
            doubts += self._far(_TRAIT_RESTS_ON.get(feature, default))
        return notes, doubts

    def _fits(self, key: str, value: str) -> str:
        """Why it fits, where that's particular to this setting: the same file, and how alike
        the part is, are said once above the items."""
        if self.same_part:
            return ""
        if key == PAUSE:
            z = float(value)
            at = any(abs(level - z) <= SAME_Z for level in self._flat_levels())
            return f"a flat face is at z {z:g} here too" if at else ""
        here, then = self.f.here, self.f.then
        for measure in _rests_on(key):
            if measure in here and measure in then:
                if similar.apart(measure, here[measure], then[measure]) <= NEAR:
                    return f"about the same {similar.MEASURES[measure].label}"
                return ""
        return ""

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
    """The offer, laid out to be read: the print and what's shared said once, what differs in
    columns, then the items by whose they were, numbered as `use` and `clear` take them."""
    if offer.failed:
        return ["", *_wrapped("", "USE THESE: the past prints couldn't be read, so none is "
                                  f"offered: {offer.failed}")]
    past = offer.past
    if past is None:
        return ["", "USE THESE: no past print has settings to offer."]
    rows = [(n, item, offer.standing(item, proposal, pose))
            for n, item in enumerate(past.items, 1)]
    lines = ["", *_wrapped("", f"USE THESE: from {past.stl_name}, {past.on}, job "
                               f"{past.job_id[:8]}. None is applied until you use it.")]
    lines += _about(past)

    # Why one isn't ticked, said once where several share it.
    unticked = [(n, why) for n, _, found in rows if found.mark == " "
                for why in found.why if why != ASKED_AGAIN]
    shared = {why: ns for why, ns in _by_text(unticked).items() if len(ns) > 1}
    open_rows = [(n, item, found) for n, item, found in rows if found.mark in ("x", " ")]
    for why, ns in shared.items():
        which = "None is ticked" if len(ns) == len(open_rows) else f"Not ticked {_listed(ns)}"
        lines += _wrapped("  ", f"{which}: {why}.")

    # One set of columns for every group, headed once.
    cells = {n: _cells(n, item, found, proposal, pose, len(str(len(rows))))
             for n, item, found in open_rows}
    widths = [max([len(cell[i]) for cell in cells.values()] + [len(least)])
              for i, least in enumerate(("", "now", ""))]
    headed = False
    for heading, sources in _GROUPS:
        group = [row for row in open_rows if row[1].used.by in sources]
        if not group:
            continue
        if Source.ANSWER in sources:
            # The same file may hold one this job didn't ask, which is ticked.
            again = any(ASKED_AGAIN in found.why for _, _, found in group)
            heading += f" ({ASKED_AGAIN if again else 'not asked for this job'})"
        lines += _group(heading, group, cells, widths, proposal, set(shared), not headed)
        headed = True
    lines += _settled(rows, proposal)
    for answer in past.answers:
        lines += _wrapped("  you answered then: ", answer)
    if offer.model:
        lines += _wrapped("  ", offer.model)
    others = [(n, p) for n, p in enumerate(offer.options, 1) if n - 1 != offer.picked]
    if others:
        lines += ["", "  OTHER PRINTS"]
        for n, other in others:
            lines += _wrapped(f"  from {n}  ",
                              f"{other.stl_name}, {other.on}: {_gives(other)}; {other.outcome}")
    return [*lines, "", *_wrapped("  ", _next(rows, bool(others)))]


#: The items' groups, by whose each was.
_GROUPS: tuple[tuple[str, tuple[Source, ...]], ...] = (
    ("YOUR OVERRIDES", _PERSONS),
    ("LESSONS IT HAD", (Source.LESSON,)),
    ("YOUR ANSWERS THEN", (Source.ANSWER,)),
)


def _about(past: Past) -> list[str]:
    """How it came out, why it's alike, and every difference, then and now."""
    out = past.came_out
    if out.startswith("failed"):
        out = "FAILED" + out.removeprefix("failed")
    lines = [*_wrapped("  Came out  ", out), *_wrapped("  Alike     ", past.why)]
    if past.measures:
        table = [("Differs", "then", "now", "", "")] + [
            (f"  {similar.MEASURES[name].label}", similar.figure(then), similar.figure(now),
             similar.MEASURES[name].unit, line)
            for name, then, now, line in past.measures
        ]
        first, second, third, fourth = (max(len(row[i]) for row in table) for i in range(4))
        for name, was, is_now, unit, line in table:
            lead = f"  {name:<{first}}  {was:>{second}}  {is_now:>{third}} {unit:<{fourth}}"
            lines += _wrapped(f"{lead}  ", line) if line else [lead.rstrip()]
    for other in past.differs:
        lines += _wrapped("    " if past.measures else "  Differs   ", other)
    return lines


def _cells(
    n: int, item: Item, found: Standing, proposal: Proposal, pose: str, digits: int
) -> tuple[str, str, str]:
    """An item's columns: its mark, number and key, what it is here, and what use makes it."""
    if item.key == ORIENTATION:
        here = pose
    else:
        now = _now(proposal, item.key)
        here = now.value if now is not None else item.preset
    return f"[{found.mark}] {n:>{digits}} {item.key}", here or "-", item.value


def _group(
    heading: str, group: list[tuple[int, Item, Standing]], cells: dict[int, tuple[str, str, str]],
    widths: list[int], proposal: Proposal, shared: set[str], first: bool,
) -> list[str]:
    """One group's items in columns, each with its reason beside it, and what several of them
    share said once beneath."""
    key, was, to = widths
    said: list[list[str]] = []
    for _, item, found in group:
        reason = _reason(item, proposal)
        parts = reason.split(_AND, 1) if item.used.by is Source.LESSON else [reason]
        words = [*parts, *_tags(item), *item.notes]
        if found.mark == " ":
            words += [f"not ticked: {why}" for why in found.why
                      if why != ASKED_AGAIN and why not in shared]
        said.append([word for word in dict.fromkeys(words) if word])
    common = {text: ns for text, ns in _by_text(
        [(n, word) for (n, _, _), words in zip(group, said, strict=True) for word in words]
    ).items() if len(ns) > 1}

    title = f"  {heading}".ljust(key + 4)
    lines = [""]
    if not first:
        lines.append(title.rstrip())
    elif len(title) == key + 4:
        lines.append(f"{title}{'now':>{was}} -> use")
    else:
        lines += [title, f"{'':<{key + 4}}{'now':>{was}} -> use"]
    for (n, _, _), words in zip(group, said, strict=True):
        name, here, value = cells[n]
        row = f"  {name:<{key}}  {here:>{was}} -> {value:<{to}}"
        text = "; ".join(word for word in words if word not in common)
        if not text:
            lines += _wrapped("  ", row.strip()) if len(row) > WIDTH else [row.rstrip()]
        elif len(row) + 2 + len(text) <= WIDTH:
            lines.append(f"{row}  {text}")
        else:
            # Longer than its column: beneath, at full width.
            lines += [*(_wrapped("  ", row.strip()) if len(row) > WIDTH else [row.rstrip()]),
                      *_wrapped("      ", text)]
    for text, ns in common.items():
        lines += _wrapped(f"      {_listed(ns)}: ", text)
    return lines


#: Between a lesson's number and its words, which lessons with the same words share.
_AND = ": "


def _reason(item: Item, proposal: Proposal) -> str:
    """The reason given then. A lesson's words are in the Lessons section when it's there."""
    u = item.used
    if u.by is not Source.LESSON:
        return "no reason given" if item.reason in ("", _NO_REASON) else item.reason
    if any(g.id == u.lesson for g in proposal.lessons):
        return f"lesson {u.lesson}, in Lessons above"
    return f"lesson {u.lesson}{_AND}{item.reason}"


def _tags(item: Item) -> list[str]:
    u = item.used
    tags = [item.fits] if item.fits else []
    if u.by is Source.READING:
        tags.append("you took the slice's")
    if u.first_job_id and u.first_job_id != u.job_id:
        tags.append(f"first on {u.first_stl_name}, job {u.first_job_id[:8]}")
    return tags


def _settled(rows: list[tuple[int, Item, Standing]], proposal: Proposal) -> list[str]:
    """What's so here already, and what can't be used, in words."""
    used = [f"{n} {item.key} {item.value}" for n, item, found in rows if found.why == ("used",)]
    lines = ["", *_wrapped("  USED: ", ", ".join(used))] if used else []
    already: list[str] = []
    said: list[tuple[int, str]] = []
    for n, item, found in rows:
        if found.mark != "=" or found.why == ("used",):
            continue
        now = _now(proposal, item.key)
        if now is None:
            by = "this print's pose" if item.key == ORIENTATION else "your preset"
        else:
            by = _set_by(now)
        u = item.used
        if u.by is Source.LESSON:
            if by != f"lesson {u.lesson}":
                by += f"; lesson {u.lesson}"
            # Its words are in the Lessons section where it's there, or where it set this.
            listed = any(g.id == u.lesson for g in proposal.lessons)
            why = "" if listed or (now is not None and now.lesson == u.lesson) else item.reason
        elif item.key == ORIENTATION or (now is not None and now.reason == item.reason):
            why = ""
        else:
            why = _reason(item, proposal)
        already.append(f"{n} {item.key} {item.value} ({by})")
        if why:
            said.append((n, why))
    if already:
        lines += ["", "  ALREADY SET HERE"]
        for row in already:
            lines += _wrapped("  ", row)
        # The words beneath, one a row, those that several share once.
        for text, ns in _by_text(said).items():
            lines += _wrapped(f"      {_listed(ns)}: ", text)
    cant = [(n, item, found) for n, item, found in rows if found.mark == "-"]
    if cant:
        lines.append("")
        for why, ns in _by_text([(n, found.why[0]) for n, _, found in cant]).items():
            lines += _wrapped("  ", f"CAN'T USE {_listed(ns)}: {why}")
            for n, item, _ in cant:
                if n in ns:
                    lines += _wrapped("      ",
                                      f"{n} {item.key} {item.value}: {_reason(item, proposal)}")
    return lines


def _gives(past: Past) -> str:
    """What another print would give, in a few words."""
    words = []
    for item in past.items:
        if item.used.by in _PERSONS:
            if item.key == PAUSE:
                words.append(f"your pause at {item.value}")
            elif item.key == ORIENTATION:
                words.append("your pose")
            elif item.key == "enable_support":
                words.append("supports on" if item.value == "1" else "supports off")
            else:
                words.append(f"{item.key} {item.value}")
    for by, one, many in ((Source.LESSON, "a lesson's setting", "lessons' settings"),
                          (Source.ANSWER, "an answer's setting", "answers' settings")):
        count = sum(item.used.by is by for item in past.items)
        if count:
            words.append(one if count == 1 else f"{count} {many}")
    return ", ".join(words)


def _next(rows: list[tuple[int, Item, Standing]], others: bool) -> str:
    """What the prompt takes, for what's left to take."""
    ticked = [n for n, _, found in rows if found.mark == "x"]
    unticked = [n for n, _, found in rows if found.mark == " "]
    used = any(found.why == ("used",) for _, _, found in rows)
    takes = []
    if ticked:
        takes += [f"use (the {len(ticked)} ticked)", f"use {ticked[0]} (by number)",
                  f"clear {ticked[0]}"]
    elif used:
        if not unticked:
            return "All used. ok to go on."
        return (f"The ticked are used. ok to go on, or use {unticked[0]} for one that isn't "
                "ticked.")
    elif unticked:
        takes.append(f"use {unticked[0]} (none is ticked: by number)")
    if others:
        takes.append("from 2 (another print)")
    return "   ".join(takes) or "Nothing here is left to use."


def _by_text(said: list[tuple[int, str]]) -> dict[str, list[int]]:
    """Each thing said, with the numbers it's said of, in the order first said."""
    found: dict[str, list[int]] = {}
    for n, text in said:
        found.setdefault(text, []).append(n)
    return found


def _listed(numbers: list[int]) -> str:
    """Numbers, a run of them as 1-4."""
    runs: list[list[int]] = []
    for n in sorted(set(numbers)):
        if runs and n == runs[-1][-1] + 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 2 else ", ".join(map(str, r)) for r in runs)


def _wrapped(lead: str, text: str) -> list[str]:
    """One plain line, wrapped under its own start where it's longer than the review's width."""
    return textwrap.wrap(text, WIDTH, initial_indent=lead, subsequent_indent=" " * len(lead),
                         break_long_words=False, break_on_hyphens=False) or [lead.rstrip()]
