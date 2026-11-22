"""The Sponsor's review of a print's settings, on the command, before the approval gate.

mqucifer/print-shop#7: the command shows the presets, each change against the
preset's own value with its reason, Orca's estimate and the orientation. The
Sponsor approves, or corrects a setting by name, and it slices again. Home
Assistant's approval gate comes after this, unchanged: the review decides the
settings, the gate decides the print.

A correction is relative to the Sponsor's own tuning, so it is shown and kept
next to the preset's value. Corrections are the learning signal for
print-flow stage 9: what the Sponsor would have tuned.

A pause is shown where it landed in the sliced G-code, beside the height asked for, and a
mismatch is said (mqucifer/print-shop#203, `_pauses`).

A change is shown as this print's only if it reached the slice
(mqucifer/print-shop#206, `proposal.read_back`). One that didn't is shown as asked and
sliced, and a key that can't be placed in a profile is refused when it's typed.

The slice is read too (mqucifer/print-shop#105, `reading.py`): each layer's time,
each feature's time and area, and the seam. What matters for this part is
flagged, each with the change it proposes, its reason and the reading it rests
on. `take` takes them, or `take 2` one of them; nothing from a reading is
applied otherwise.

**The model's settings are proposals** (mqucifer/print-shop#242, `suggest.py`). The review
doesn't wait for them: it says the model is working, and adds them to the list when Enter asks,
numbered on from the slice's. `take 2` takes one, bare `take` stays the slice's, and
`no 2 because ...` turns one down as a correction. An `ok` before they're in, or before they've
been shown, says so once.

**Use these** (mqucifer/print-shop#211, `offer.py`): what decided the most similar past print,
pre-selected, each with why. `use` takes the selected ones, `use 2 4` those numbered, `clear 3`
clears one and `from 2` picks another print. Only `use` applies any. An `ok` with ticked ones
unused says so once, and the next goes on without them (`cli_reviewer`).

**The orientation can be sent back** (mqucifer/print-shop#128), in a short conversation
(`send_back`): the step says why it turned the part, the person names the pose it should print
in and says why the turn was wrong, or meets the step's concern by settings instead, each priced
by Orca. A reason is required: it's recorded as the correction, and drawn as a lesson the
orientation step is given next time. The part is then turned and sliced again.

**A pose still being measured says so** (mqucifer/print-shop#235). A kept pose's job doesn't
wait for the other five, so the table can be drawn before they're in. `orientation` waits for
them, saying that it is, and then lists them as ever.
"""

from __future__ import annotations

import re
import textwrap
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, TextIO

from print_shop.bench.pausing import TOPS_OUT, mm
from print_shop.core.state import AppliedOrientation, Orientation, SliceResult
from print_shop.setup.concerns import concern_in, ways
from print_shop.setup.intake import Estimate, drop_typed
from print_shop.setup.offer import Item, Offering
from print_shop.setup.offer import render as render_offer
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    PAUSE_NAMES,
    Change,
    Ending,
    GivenLesson,
    Proposal,
    Source,
    Suggested,
    pose_value,
)
from print_shop.setup.reach import profile_of, unplaced
from print_shop.setup.reading import PAUSE_PAST_FIT, Flag, SliceReading
from print_shop.setup.suggest import (
    Listed,
    Suggesting,
    by_number,
    didnt_come,
    fell_back,
    listed,
    looked_up,
    turning,
    waited,
    went_on,
)
from print_shop.setup.waiting import terminal
from print_shop.slicer.orca import SlicerProfiles

if TYPE_CHECKING:
    from print_shop.ingest.measuring import Measuring

__all__ = [
    "Review", "Reviewer", "ReviewRequest", "cli_reviewer", "parse_answer", "render", "send_back",
]

#: `key=value`, then an optional reason after "because" or a dash. A value may have spaces,
#: as Orca's own do (`ironing_type=no ironing`).
_CORRECTION = re.compile(
    r"^\s*([a-z_0-9]+)\s*=\s*(.+?)\s*(?:(?:\bbecause\b|\s-\s|\s?—\s?)\s*(.*))?$"
)
#: `orientation`, `orientation=2`, or `orientation=2 because ...`: the pose sent back.
_SEND_BACK = re.compile(r"^\s*orientation\b(?:\s*=\s*(\d+))?\s*(?:(?:because|-|—)\s*(.*))?$",
                        re.I)
#: `take`, or `take 1 3`: the changes the slice's reading proposed.
_TAKE = re.compile(r"^\s*take\b\s*([\d ,]*)$", re.I)
#: `no 3 because ...`: one of the model's proposals turned down, saying why.
_NO = re.compile(r"^\s*no\s+(\d+)\b\s*(?:(?:because\b|-|—|:)\s*(.*))?$", re.I)
#: `use`, `use 2 4`, `clear 3` or `from 2`: what the most similar past print offers.
_OFFER = re.compile(r"^\s*(use|clear|from)\b\s*([\d ,]*)$", re.I)


@dataclass(frozen=True)
class ReviewRequest:
    """Everything the review shows."""

    presets: tuple[str, str, str]  # machine, process, filament names
    proposal: Proposal
    slice: SliceResult
    orientation: AppliedOrientation
    profiles: SlicerProfiles  # the presets as sliced, to look up a corrected key
    process: dict[str, Any] = field(default_factory=dict)  # the preset's own values
    filament: dict[str, Any] = field(default_factory=dict)
    #: The slice, read layer by layer, and what it says matters for this part.
    reading: SliceReading | None = None
    flags: tuple[Flag, ...] = ()
    #: The poses the orientation step weighed, to send the pose back to one
    #: (mqucifer/print-shop#128).
    candidates: tuple[Orientation, ...] = ()
    #: Those still being measured beside the job, to say so and to wait for at a send-back
    #: (mqucifer/print-shop#235). `candidates` are those that were ready when this was made.
    measuring: Measuring | None = None
    #: The part's traits in the file's own pose, before any turn: what an orientation lesson is
    #: keyed to.
    laid_traits: tuple[str, ...] = ()
    #: Orca's estimate with these changes over the proposal, to price a way by settings.
    cost: Callable[[tuple[Change, ...]], Estimate | None] | None = None
    #: The kept lessons for this part in a pose, on its filament: a way by settings offers
    #: their values for the keys they set.
    lessons_for: Callable[[Orientation], tuple[GivenLesson, ...]] | None = None
    #: The part's context from its design run or a sidecar, and placed in this pose, in lines
    #: (mqucifer/print-shop#130): what was read, or that there was none.
    context: tuple[str, ...] = ()
    #: What decided the past prints most like this one, to use (mqucifer/print-shop#211).
    offering: Offering | None = None
    #: The model's proposals as this screen has them (mqucifer/print-shop#242): what a number
    #: typed is read against. None: no model proposes here.
    suggested: Suggested | None = None
    #: The model at work on them, beside the review: how long, and what it's come to since.
    suggesting: Suggesting | None = None
    #: The model's proposals turned down at this job so far, each saying why.
    turned_down: tuple[Change, ...] = ()

    @property
    def proposed(self) -> tuple[Flag, ...]:
        """The flags that propose a change, numbered from 1 at the review."""
        return tuple(f for f in self.flags if f.change is not None)

    @property
    def by_model(self) -> list[Listed]:
        """The model's proposals on this screen, numbered on from the slice's."""
        return listed(self.suggested, self.proposal,
                      pose_value(self.orientation.rotation_deg), len(self.proposed))

    def refreshed(self) -> ReviewRequest:
        """The same review with what the model has come to by now added: a new screen, whose
        numbers are the ones to type. The phone asks for it as Enter does, and says it in
        `Review.saw` when it answers, or what came is recorded as unseen."""
        if self.suggesting is None:
            return self
        now = self.suggesting.now()
        return replace(self, suggested=now, proposal=looked_up(self.proposal, now))


@dataclass(frozen=True)
class Review:
    """The Sponsor's answer: approve, corrections or readings' changes to slice again with,
    or stop."""

    approved: bool = False
    corrections: tuple[Change, ...] = ()
    stop: bool = False
    #: The changes proposed, by the reading or the model, that the Sponsor took (`take`).
    taken: tuple[Change, ...] = ()
    #: The model's proposals turned down, saying why, by key. Each is a correction as well.
    declined: tuple[str, ...] = ()
    #: The model's proposals as the last screen before the answer had them, where a reviewer
    #: added to them itself. None: as the request had them.
    saw: Suggested | None = None
    #: The offer as the answer left it, where it picked another print or cleared an item.
    offering: Offering | None = None

    @property
    def browsing(self) -> bool:
        """Only the offer was changed: there's nothing to slice again."""
        return self.offering is not None and not (
            self.approved or self.stop or self.corrections or self.taken
        )


class Reviewer(Protocol):
    def __call__(self, request: ReviewRequest) -> Review: ...


#: Who each change is from, as the review names it. Every `Source` has one, so a
#: change from an intake answer shows like any other (it raised KeyError, 2026-11-13).
_BY = {Source.ANSWER: "your answer", Source.MODEL: "model", Source.GUARD: "guard",
       Source.SPONSOR: "you", Source.READING: "the slice", Source.LESSON: "lesson"}


def render(request: ReviewRequest) -> str:
    """The review as the command prints it."""
    machine, process, filament = request.presets
    s = request.slice
    lines = [
        "Settings for this print, over your presets:",
        f"  machine   {machine}",
        f"  process   {process}",
        f"  filament  {filament}",
        "",
    ]
    proposal = request.proposal
    found = request.suggested
    # A turn's reading, notes and refusals are for that pose, and shown only under it.
    here = found if found is not None and not turning(found) else None
    reasoning = proposal.reasoning if found is None else here.reasoning if here else ""
    if reasoning:
        lines += ["How the part was read:", *_wrap(reasoning), ""]
    changes = proposal.changes
    if changes:
        width = max(len(c.key) for c in changes)
        tuned = any(c.standard for c in changes)
        head = "standard -> your tuning -> this print" if tuned else "your preset -> this print"
        lines.append(f"  {'setting'.ljust(width)}  {head}")
        for c in changes:
            by = _by(c)
            cites = ", ".join(x for x in (c.feature, c.knowledge) if x)
            was = f"{c.standard} -> {c.preset or '-'}" if c.standard else (c.preset or "-")
            lines.append(f"  {c.key.ljust(width)}  {was} -> {c.value}")
            lines.append(f"  {''.ljust(width)}    {c.reason} [{by}{': ' + cites if cites else ''}]")
            if c.reading:
                lines.append(f"  {''.ljust(width)}    read: {c.reading}")
    elif proposal.not_applied:
        lines.append("  No change reached the slice.")
    else:
        lines.append("  No changes: your presets as they are.")
    for c in proposal.not_applied:
        lines.append(f"  NOT APPLIED {c.key}: {c.not_applied}")
        lines.append(f"    {c.reason} [{_by(c)}]")
    lines += _pauses(request)
    lines += [f"  note: {f.reading}" for f in request.flags if f.id == PAUSE_PAST_FIT]
    for c in proposal.kept:
        by = (
            f" [lesson {c.lesson}]" if c.lesson is not None
            # An answer that keeps the preset's value is a decision too (mqucifer/print-shop#124).
            else f" [{_BY[c.source]}: {c.feature}]" if c.source is Source.ANSWER
            else ""
        )
        lines.append(f"  kept {c.key} at {c.preset}: {c.reason}{by}")
    from_model = here.design_notes if here is not None else ()
    for note in dict.fromkeys((*proposal.design_notes, *from_model)):
        lines.append(f"  design: {note}")
    for note in proposal.notes:
        lines.append(f"  note: {note}")
    for refusal in dict.fromkeys((*proposal.refused, *(here.refused if here else ()))):
        lines.append(f"  refused from the model: {refusal}")
    if found is not None:
        lines += _model_line(found)
    elif proposal.model.startswith("cut off"):
        lines.append(
            f"  CUT OFF: the model's answer was {proposal.model}. Nothing it proposed is "
            "used; only the guards' changes stand. This is recorded, to tune the limit."
        )
    elif proposal.model.startswith(("unavailable", "timed out")):
        lines.append(f"  the model was {proposal.model}; only the guards' changes stand")
    elif not proposal.model:
        lines.append("  no model was asked; only the guards' changes stand")
    if proposal.lessons:
        lines += ["", "Lessons from earlier prints, kept by a reviewer, for a part like this:"]
        for g in proposal.lessons:
            used = (
                f"not given: {g.withheld}" if g.withheld
                else "set it" if g.applied else (g.why or "read by the model")
            )
            if g.looked_up:
                used += "; the print loop looked it up with past_prints"
            lines.append(f"  {g.line()} ({used})")
    lines += [
        "",
        f"Orca's estimate: {s.print_time_s // 60} min {s.print_time_s % 60} s, "
        f"{s.filament_cm3} cm3" + (f", {s.filament_g} g" if s.filament_g else "")
        + f", {s.layer_count} layers",
        f"Orientation: {request.orientation.rotation_deg}, {request.orientation.rationale}",
        # The pose the features, and the lessons keyed to them, were read in
        # (mqucifer/print-shop#128).
        f"  the part's features were read in this pose: {request.orientation.pose()}",
        *_poses(request),
    ]
    if request.context:
        lines += ["", "The part's context (its design run, or a sidecar beside it):",
                  *(f"  {line}" for line in request.context)]
    lines += _read(request)
    lines += _by_model(request)
    if request.offering is not None:
        pose = pose_value(request.orientation.rotation_deg)
        lines += render_offer(request.offering, proposal, pose)
    lines += _prompt(request)
    return "\n".join(lines)


def render_arrived(request: ReviewRequest) -> str:
    """Only what came since the screen above it was drawn: the model's block, or how it failed,
    and the prompt (mqucifer/print-shop#242). The rest of the review hasn't changed."""
    found = request.suggested
    assert found is not None
    lines = [*_model_line(found), *_by_model(request)]
    if not turning(found):  # else they're under the turn
        lines += [f"  design: {note}" for note in found.design_notes
                  if note not in request.proposal.design_notes]
        lines += [f"  refused from the model: {refusal}" for refusal in found.refused
                  if refusal not in request.proposal.refused]
    return "\n".join([*lines, *_prompt(request)])


def _prompt(request: ReviewRequest) -> list[str]:
    """What can be typed at the review as it's now listed."""
    use = (" use what decided that print (use, or use 1 3),"
           if request.offering is not None and request.offering.options else "")
    take = _take_says(request)
    back = " send the orientation back (orientation)," if request.candidates else ""
    return [
        "",
        f"Type ok to go on to the approval gate,{use}{take}{back} or correct a setting by name, "
        "e.g. wall_loops=3 because it's decorative (several separated by ';'), "
        "pause_at=12.4 because white to black (it pauses once the layer topping out at "
        "12.4 mm is done), or stop.",
    ]


def _pauses(request: ReviewRequest) -> list[str]:
    """Where the pause landed, read from the sliced G-code, beside the height asked for
    (mqucifer/print-shop#203). Where they differ that's said, with what the G-code has."""
    change = request.proposal.get(PAUSE)
    asked = change.value if change is not None and change.value != "none" else None
    name = f" {PAUSE}={asked}" if asked else ""
    reading = request.reading
    if reading is None:
        return ([f"  PAUSE NOT READ{name}: the G-code couldn't be read, so where it pauses "
                 "isn't known"] if asked else [])
    placed = reading.pauses
    if not asked and not placed:
        return []
    found = "; ".join(f"pause {p.where()}" for p in placed) or "no pause"
    if not asked:
        return [f"  PAUSE MISMATCH: none was asked for, and the G-code has: {found}"]
    z = float(asked)
    if reading.settings.get("raft_layers", "0") not in ("", "0"):
        return [f"  PAUSE MISMATCH{name}: the part stands on a raft, so z in the G-code counts "
                f"the raft too, and the G-code has: {found}"]
    if len(placed) == 1 and (top := placed[0].after_z) is not None and abs(top - z) <= TOPS_OUT:
        return [f"  {PAUSE}={asked}, read from the G-code: {found}"]
    at = next((t.n for t in reading.layers if abs(t.z - z) <= TOPS_OUT), None)
    if at is not None:
        return [f"  PAUSE MISMATCH{name}: layer {at} tops out at z={mm(z)}, and the G-code has: "
                f"{found}"]
    line = f"  PAUSE MISMATCH{name}: no layer tops out at z={mm(z)}, and the G-code has: {found}"
    # The layer either side, to type: inside the part, so never the last.
    inside = [t.z for t in reading.layers[:-1]]
    near = [*[t for t in inside if t < z][-1:], *[t for t in inside if t > z][:1]]
    if placed and near:
        line += ": type " + " or ".join(f"{PAUSE}={mm(t)}" for t in near)
    return [line]


def _by(change: Change) -> str:
    """Who a change is from, as the review names them."""
    if change.said_no_to:
        return f"{_BY[change.source]}, saying no to the model's {change.said_no_to}"
    if change.taken and change.used is None:
        return f"{_BY[change.source]}, taken by you"
    if change.used is not None:
        return f"{_BY[change.source]}, {change.used.line()}"
    return _BY[change.source] + (f" {change.lesson}" if change.lesson is not None else "")


def _poses(request: ReviewRequest) -> list[str]:
    """Each pose the orientation step weighed, with the figures its choice rested on
    (mqucifer/print-shop#128): what would need support, the round or chamfer at the plate left out
    of it, the area on the plate and the height."""
    if not request.candidates:
        return []
    lines = ["  the poses it weighed (overhang is what would need support):"]
    measuring = request.measuring
    # What's pending is read first: one that lands between the two reads is shown measured.
    pending = measuring.pending() if measuring is not None else ()
    failed = measuring.failed() if measuring is not None else {}
    ready = measuring.ready() if measuring is not None else request.candidates
    for c in ready:
        here = "  <- this print" if c.rotation_deg == request.orientation.rotation_deg else ""
        flare = (f", {c.flare_area_mm2:g} mm2 rising from the plate left out"
                 if c.flare_area_mm2 else "")
        lines.append(
            f"    {pose_value(c.rotation_deg):>9}  {c.overhang_area_mm2:g} mm2 overhang{flare}, "
            f"{c.bed_contact_area_mm2:g} mm2 on the plate, {c.height_mm:g} mm tall{here}"
        )
    measured = {c.rotation_deg for c in ready}
    lines += [f"    {pose_value(r):>9}  still being measured: orientation waits for it"
              for r in pending if r not in measured and r not in failed]
    lines += [f"    {pose_value(r):>9}  couldn't be measured: {why}" for r, why in failed.items()]
    return lines


def _read(request: ReviewRequest) -> list[str]:
    """What the slice shows, and what it proposes."""
    reading = request.reading
    if reading is None:
        return []
    lines = ["", "What the slice shows, layer by layer:"]
    lines += [f"  {line}" for line in reading.lines()]
    for flag in request.flags:
        if flag.change is None and flag.id != PAUSE_PAST_FIT:  # that one's beside its pause
            lines.append(f"  note: {flag.reading}")
    for n, flag in enumerate(request.proposed, 1):
        c = flag.change
        assert c is not None
        if n == 1:
            lines += ["", "Proposed from the slice (not applied unless you take it):"]
        sliced = reading.settings.get(c.key, c.preset)
        yours = f" (your preset: {c.preset})" if c.preset and c.preset != sliced else ""
        lines.append(f"  {n}) {c.key}  {sliced or '-'} -> {c.value}{yours}")
        lines.append(f"       {c.reason} [the slice: {c.knowledge}]")
        lines.append(f"       read: {flag.reading}")
    return lines


def _model_line(found: Suggested) -> list[str]:
    """The model's work on the settings, in one line: still at it, or what it came to."""
    if found.ending is Ending.WORKING:
        return [f"  The model is still working on settings ({waited(found.seconds)}). What it "
                "proposes is added below, and nothing waits for it."]
    lines = [f"  note: {stopped}"] if (stopped := fell_back(found)) else []
    if found.ending is not Ending.CAME:
        lines.append(f"  {didnt_come(found)}")
    elif not found.proposed:
        lines.append(f"  The model proposed no change, after {waited(found.seconds)}.")
    return lines


def _by_model(request: ReviewRequest) -> list[str]:
    """The model's proposals, numbered on from the slice's, and those that don't stand."""
    rows = request.by_model
    found = request.suggested
    down = [c for c in request.turned_down if found is not None and c.key in found.declined]
    if found is None or not (rows or down or found.asked):
        return []
    lines = ["", "Proposed by the model (not applied unless you take it):"]
    if found.asked:
        lines.append(f"  It would have asked first: {found.asked}")
    for row in rows:
        c = row.change
        if row.standing == "theirs":
            lines.append(f"  -  {c.key}: {row.by} set {row.now or '-'}, and the model would set "
                         f"{c.value}. {'Yours' if row.by == 'you' else 'That'} stands.")
            continue
        if row.standing == "so":
            lines.append(f"  -  {c.key} {c.value}: taken." if c.key in found.taken
                         else f"  -  {c.key}: the model's {c.value} is so here already.")
            continue
        if c.key == ORIENTATION:
            here = pose_value(request.orientation.rotation_deg)
        else:
            now = request.proposal.get(c.key)
            here = now.value if now is not None else c.preset
        lines.append(f"  {row.number}) {c.key}  {here or '-'} -> {c.value}")
        cites = ", ".join(x for x in (c.feature, c.knowledge) if x and c.key != ORIENTATION)
        if c.reading:
            lines.append(f"       {c.reading}")
        if c.reason:
            lines.append(f"       {c.reason} [model{': ' + cites if cites else ''}]")
        if c.key == ORIENTATION:
            if found.reasoning and not found.reasoning.startswith(c.reason):
                lines.append(f"       How it read the part turned so: {found.reasoning}")
            if found.once_turned:
                then = ", ".join(f"{t.key} {t.preset or '-'} -> {t.value}"
                                 for t in found.once_turned)
                lines.append(f"       Once turned it would also propose: {then}")
            lines += [f"       design, turned so: {note}" for note in found.design_notes]
            lines += [f"       refused from the model, turned so: {each}" for each in found.refused]
    for c in down:
        lines.append(f"  -  {c.key} stays {c.value}: {c.reason} [you, saying no to the model's "
                     f"{c.said_no_to}]")
    return lines


def _take_says(request: ReviewRequest) -> str:
    """What the prompt says `take` takes: bare, the slice's, and the model's by number."""
    slices = len(request.proposed)
    models = sorted(by_number(request.by_model))
    if not models:
        return " or take what the slice proposes (take, or take 1)," if slices else ""
    numbers = " ".join(map(str, models))
    of_slice = (f"take (the slice's {'1' if slices == 1 else f'1-{slices}'}), " if slices else "")
    return (f" {of_slice}take {numbers} (the model's, by number), no {models[0]} because ... "
            "(turn one down, saying why),")


def _wrap(text: str, width: int = 96) -> list[str]:
    return ["  " + line for line in textwrap.wrap(text, width)]


def parse_answer(answer: str, request: ReviewRequest) -> Review | str:
    """The Sponsor's answer as a `Review`, or why it can't be read."""
    text = answer.strip()
    if text.lower() in ("ok", "yes", "y", "approve"):
        return Review(approved=True)
    if text.lower() in ("no", "n") and by_number(request.by_model):
        # The screen teaches `no 2 because ...`: a bare no doesn't end the job here.
        return ("no turns one of the model's down, saying why: no "
                f"{min(by_number(request.by_model))} because ... To end the job, type stop.")
    if text.lower() in ("stop", "no", "n"):
        return Review(stop=True)
    corrections = []
    taken: list[Change] = []
    used: list[Change] = []
    offering = request.offering
    picked = False
    models = by_number(request.by_model)
    down: list[Change] = []
    for part in filter(None, (p.strip() for p in text.split(";"))):
        said_no = _NO.match(part)
        if said_no is not None:
            change = _turned_down(int(said_no.group(1)), said_no.group(2) or "", request, models)
            if isinstance(change, str):
                return change
            down.append(change)
            continue
        took = _TAKE.match(part)
        if took is not None:
            found = _take(took.group(1), request, models)
            if isinstance(found, str):
                return found
            taken += found
            continue
        offered = _OFFER.match(part)
        if offered is not None:
            if offering is None:
                return "there's no past print's settings to use"
            verb = offered.group(1).lower()
            numbers = [int(n) for n in re.findall(r"\d+", offered.group(2))]
            if verb == "use":
                if picked:
                    return "look at that print's settings first: from picks it, then use"
                items = offering.use(numbers, request.proposal,
                                     pose_value(request.orientation.rotation_deg))
                if isinstance(items, str):
                    return items
                used += [_used_change(item, request) for item in items]
                continue
            if verb == "from" and len(numbers) != 1:
                return "from picks one print, such as from 2"
            changed = offering.pick(numbers[0]) if verb == "from" else offering.clear(numbers)
            if isinstance(changed, str):
                return changed
            offering, picked = changed, picked or verb == "from"
            continue
        match = _CORRECTION.match(part)
        if match is None:
            return f"'{part}' isn't setting=value"
        key, value, reason = match.group(1), match.group(2), (match.group(3) or "").strip()
        if key in PAUSE_NAMES:
            change = _pause(value, reason, request)
            if isinstance(change, str):
                return change
            corrections.append(change)
            continue
        preset = _preset_of(request, key)
        if preset is None:
            return unplaced(key, request.process, request.filament)
        known = request.proposal.get(key) or next(
            (c for c in (*request.proposal.not_applied, *request.proposal.kept) if c.key == key),
            None,
        )
        corrections.append(
            Change(key=key, preset=preset, value=value, reason=reason or "your correction",
                   source=Source.SPONSOR, standard=known.standard if known else "")
        )
    keys = {c.key for c in corrections}
    # Named twice, as `take 2; take`, the model's taken by number is the one taken.
    taken = list({c.key: c for c in sorted(taken, key=lambda c: c.taken)}.values())
    taken = [c for c in taken if c.key not in keys]
    # Typed wins over taken, and both over used.
    over = keys | {c.key for c in taken}
    used = [c for c in {c.key: c for c in used}.values() if c.key not in over]
    if taken and any(c.key == ORIENTATION for c in used):
        return "that turns the part, which slices it afresh: use it, then take at the next review"
    turn = next((n for n, c in models.items() if c.key == ORIENTATION), None)
    if any(c.key == ORIENTATION for c in taken) and (
        len(taken) > 1 or used or corrections or down
    ):
        return (f"that turns the part, which slices it afresh: take {turn} alone first, then "
                "the rest at the next review")
    if {c.key for c in down} & {c.key for c in taken}:
        return "that's both taken and turned down: one or the other"
    # What's typed is the no's answer, and what's used stands in its place: the no says so.
    stands = {c.key: c.value for c in used}
    down = [c.model_copy(update={"value": stands.get(c.key, c.value)}) for c in down
            if c.key not in keys]
    return Review(corrections=(*used, *down, *corrections), taken=tuple(taken),
                  declined=tuple(c.key for c in down),
                  offering=None if offering == request.offering else offering)


def _used_change(item: Item, request: ReviewRequest) -> Change:
    """A past print's item as the person's correction, with the print it came from."""
    if item.key == ORIENTATION:
        # Keyed as a pose sent back is, by the traits in the file's own pose.
        return Change(key=ORIENTATION, preset=pose_value(request.orientation.rotation_deg),
                      value=item.value, reason=item.reason, source=Source.SPONSOR,
                      feature=", ".join(request.laid_traits), reading=item.reading,
                      used=item.used)
    known = request.proposal.get(item.key) or next(
        (c for c in (*request.proposal.not_applied, *request.proposal.kept) if c.key == item.key),
        None,
    )
    return Change(key=item.key, preset=item.preset, value=item.value, reason=item.reason,
                  source=Source.SPONSOR, standard=known.standard if known else "",
                  reading=item.reading, used=item.used)


def _pause(value: str, reason: str, request: ReviewRequest) -> Change | str:
    """A pause after the layer that tops out at a height inside the part
    (mqucifer/print-shop#119, mqucifer/print-shop#203), or why not. `none` takes one away."""
    shown = request.proposal.get(PAUSE)
    if value.lower() == "none":
        return Change(key=PAUSE, preset="", value="none", reason=reason or "no colour change",
                      source=Source.SPONSOR)
    try:
        z = float(value.removesuffix("mm"))
    except ValueError:
        return f"'{value}' isn't a height: give one in mm, e.g. pause_at=12.4, or none"
    top = request.slice.max_z_mm
    if not 0 < z < top:
        return f"a pause is inside the part: above 0 and below its top, {top:g} mm"
    layers = request.reading.layers if request.reading is not None else ()
    if layers and z < layers[0].z - TOPS_OUT:
        return (f"there's no layer to pause after at {z:g} mm: the first tops out at "
                f"{layers[0].z:g} mm")
    return Change(key=PAUSE, preset=shown.value if shown else "", value=f"{z:g}",
                  reason=reason or "a colour change", source=Source.SPONSOR)


def _take(
    numbers: str, request: ReviewRequest, models: dict[int, Change]
) -> list[Change] | str:
    """The changes `take` names, by the numbers on screen: the slice's, all of them by a bare
    `take`, and the model's, by number alone (mqucifer/print-shop#242)."""
    proposed = request.proposed
    picked = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", numbers)))
    if not picked:
        if not proposed:
            return ("the slice proposes nothing to take"
                    + (f": the model's are taken by number, take {min(models)}" if models else ""))
        picked = list(range(1, len(proposed) + 1))
    highest = max([len(proposed), *models])
    if not highest:
        return "the slice proposes nothing to take"
    if any(not 1 <= n <= highest for n in picked):
        return f"take a number from 1 to {highest}"
    taken: list[Change] = []
    for n in sorted(picked):  # the slice's first, so that the model's stands where both name one
        change = proposed[n - 1].change if n <= len(proposed) else models[n]
        assert change is not None
        taken.append(change if n <= len(proposed) else change.model_copy(update={"taken": True}))
    # Named for one setting as well, the model's replaces the slice's: it was asked for by number.
    return list({c.key: c for c in taken}.values())


def _turned_down(
    n: int, why: str, request: ReviewRequest, models: dict[int, Change]
) -> Change | str:
    """`no n because ...`: the person's no, with why, and a lesson candidate as a no to the print
    loop's question was (mqucifer/print-shop#242). It records what stands here and sets
    nothing: a guard's setting stays the guard's, and another pose has its own."""
    change = models.get(n)
    if change is None:
        if not models:
            return "there's nothing of the model's to turn down"
        return ("no turns down one of the model's: "
                + " or ".join(map(str, sorted(models))))
    if not why.strip():
        return f"say why, it's what the next print learns from: no {n} because ..."
    if change.key == ORIENTATION:
        # Shaped as a pose sent back is: the pose refused, the one kept, the laid pose's traits.
        here = pose_value(request.orientation.rotation_deg)
        preset, feature = change.value, ", ".join(request.laid_traits)
    else:
        proposal = request.proposal
        now = next((c for c in (*proposal.changes, *proposal.not_applied, *proposal.kept)
                    if c.key == change.key), None)
        here = now.value if now is not None else change.preset
        preset, feature = change.preset, ""
    return Change(key=change.key, preset=preset, value=here, reason=why.strip(),
                  source=Source.SPONSOR, feature=feature, standard=change.standard,
                  said_no_to=change.value)


def cli_reviewer(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print,
    *,
    forget: Callable[[], None] = drop_typed,
    screen: Callable[[], TextIO | None] = terminal,
) -> Reviewer:
    """Show the review on the command line and read the answer, again until it's readable.
    `orientation` starts the send-back conversation (`send_back`).

    The model's proposals are said as they arrive, under the open prompt, and added to the list
    by Enter (mqucifer/print-shop#242). What's typed is read against the list on screen, so
    where a list of theirs can be shown, what was typed while it drew is forgotten.
    """

    def review(request: ReviewRequest) -> Review:
        work = request.suggesting
        lock = threading.Lock()  # the model's end and the prompt both write
        asking = threading.Event()
        announced = False

        def said_in(found: Suggested) -> str:
            if found.proposed:
                many = len(found.proposed)
                them = "proposal" if many == 1 else f"{many} proposals"
                return (f"The model's {them} came after {waited(found.seconds)}. Enter adds "
                        f"{'it' if many == 1 else 'them'} to the list")
            if found.ending is Ending.CAME:
                return f"The model proposed no change, after {waited(found.seconds)}"
            return didnt_come(found).rstrip(".")

        def heard(found: Suggested) -> None:
            nonlocal announced
            with lock:
                if not asking.is_set():
                    return  # said before the next prompt
                announced = True
                write(f"\n{said_in(found)} (what you've typed is kept).")
                out = screen()
                if out is not None:
                    # On the screen alone: the log gets the prompt with its answer.
                    out.write("> ")
                    out.flush()

        def show() -> None:
            write(render(request))
            if request.suggested is not None:
                forget()  # its numbers are only now the ones on screen

        def ask() -> str:
            nonlocal announced
            # Looked at and marked open together, so that an end between the two is said once,
            # by one or the other.
            with lock:
                if work is not None and not announced:
                    now = work.now()
                    if (request.suggested is not None and now.ending is not Ending.WORKING
                            and request.suggested.ending is Ending.WORKING):
                        announced = True
                        write(f"  {said_in(now)}.")
                asking.set()
            try:
                return read("> ")
            finally:
                with lock:
                    asking.clear()

        if work is not None:
            work.hear(heard)
        try:
            show()
            moved: Offering | None = None  # the offer as picked and cleared here
            told = False  # that what's ticked isn't used yet
            told_model: set[str] = set()  # what's been said of the model's, at an ok
            added = False
            while True:
                answer = ask()
                if request.suggested is not None and not answer.strip():
                    newer = request.refreshed()
                    assert newer.suggested is not None
                    if newer.suggested.ending is not request.suggested.ending:
                        request, added = newer, True
                        write(render_arrived(request))
                        forget()  # its numbers are only now the ones on screen
                    elif newer.suggested.ending is Ending.WORKING:
                        write("  The model is still working on settings "
                              f"({waited(newer.suggested.seconds)}).")
                    else:
                        write("  Nothing new to add: ok, take, a correction, orientation, "
                              "or stop.")
                    continue
                back = _SEND_BACK.match(answer)
                if back is not None and request.candidates:
                    sent = send_back(request, read, write, back.group(1), back.group(2))
                    if sent is not None:
                        saw = request.suggested if added else None
                        return replace(sent, offering=moved, saw=saw)
                    write("  Back to the review: ok, a correction, orientation, or stop.")
                    continue
                parsed = parse_answer(answer, request)
                if isinstance(parsed, str):
                    write(f"  {parsed}")
                    continue
                if parsed.browsing:
                    # Only the offer moved, so only the offer is shown again.
                    moved = parsed.offering
                    assert moved is not None
                    request = replace(request, offering=moved)
                    pose = pose_value(request.orientation.rotation_deg)
                    write("\n".join(render_offer(moved, request.proposal, pose)))
                    told = False
                    continue
                if parsed.approved:
                    waits: list[str] = []
                    if not told and request.offering is not None:
                        pose = pose_value(request.orientation.rotation_deg)
                        left = len(request.offering.ticked(request.proposal, pose))
                        if left:
                            told = True
                            aren, them = ("isn't", "it") if left == 1 else ("aren't", "them")
                            waits.append(f"  {left} ticked {aren} used yet: use to take {them}, "
                                         "or ok again to go on without")
                    state, said = _going_on(request)
                    if state and state not in told_model:
                        told_model.add(state)
                        waits.append(said)
                    if waits:
                        write("\n".join(waits))
                        continue
                if parsed.approved and (without := _without(request)):
                    write(f"  {without}.")
                if moved is not None and parsed.offering is None:
                    parsed = replace(parsed, offering=moved)
                return replace(parsed, saw=request.suggested) if added else parsed
        finally:
            if work is not None:
                work.hear(None)

    return review


def _going_on(request: ReviewRequest) -> tuple[str, str]:
    """What an `ok` goes on without, of the model's: which, and what's said of it once."""
    shown = request.suggested
    if request.suggesting is None or shown is None or shown.ending is not Ending.WORKING:
        return "", ""
    now = request.suggesting.now()
    if now.ending is Ending.WORKING:
        return "working", (f"  The model is still working ({waited(now.seconds)}): ok again to "
                           "go on without its proposals, which closes its call.")
    if not now.proposed:
        return "", ""
    many = len(now.proposed)
    of, are, them = (("proposal", "is", "it") if many == 1
                     else (f"{many} proposals", "are", "them"))
    return "unseen", (f"  The model's {of} {are} in and you haven't seen {them}: Enter adds "
                      f"{them} to the list, ok again goes on without {them}.")


def _without(request: ReviewRequest) -> str:
    """What this approval goes on without, of the model's, said as it's given, or ""."""
    shown = request.suggested
    if request.suggesting is None or shown is None or shown.ending is not Ending.WORKING:
        return ""
    now = request.suggesting.now()
    update = ({"ending": Ending.WENT_ON} if now.ending is Ending.WORKING
              else {"unseen": bool(now.proposed)})
    return went_on(now.model_copy(update=update))


def _pose(o: Orientation) -> str:
    laid = "  as the file lays it" if o.rotation_deg == (0.0, 0.0, 0.0) else ""
    return (f"{pose_value(o.rotation_deg)}{laid}: {o.overhang_area_mm2:g} mm2 of overhang, "
            f"{o.bed_contact_area_mm2:g} mm2 on the plate, {o.height_mm:g} mm tall")


def send_back(
    request: ReviewRequest, read: Callable[[str], str], write: Callable[[str], None],
    pose: str | None = None, reason: str | None = None,
) -> Review | None:
    """The pose sent back at the review, in a short conversation (mqucifer/print-shop#128).

    The step says why it turned the part; the person names the pose it should print in, and says
    why the turn was wrong in their own words, or by number takes a way of meeting the step's
    concern by settings, each priced by Orca. A reason is required. The correction is the pose,
    with that reason and both poses' readings; a way taken adds its settings as corrections.
    None is the person going back to the review without sending anything. Poses still being
    measured are waited for first, saying so (mqucifer/print-shop#235).
    """
    measuring = request.measuring
    if measuring is not None:
        waited = measuring.pending()
        if waited:
            them = "pose" if len(waited) == 1 else f"{len(waited)} poses"
            write(f"Waiting for the {them} still being measured: "
                  f"{'; '.join(map(pose_value, waited))}.")
        request = replace(request, candidates=measuring.settled())
        failed = measuring.failed()
        for rotation, why in failed.items():
            write(f"  {pose_value(rotation)} couldn't be measured, so it isn't offered: {why}")
        if failed:
            pose = None  # a number typed ahead counted it, so the list is shown
    now = request.orientation
    current = next((c for c in request.candidates if c.rotation_deg == now.rotation_deg), None)
    others = [c for c in request.candidates if c.rotation_deg != now.rotation_deg]
    write(f"The orientation step printed it at {pose_value(now.rotation_deg)}: {now.rationale}")
    chosen: Orientation | None = None
    if pose is not None and pose.isdigit() and 1 <= int(pose) <= len(others):
        chosen = others[int(pose) - 1]
    if chosen is None:
        if not others:
            write("  There's no other pose to send it to.")
            return None
        write("Which pose should it print in?")
        write("\n".join(f"  {n}) {_pose(c)}" for n, c in enumerate(others, 1)))
        while chosen is None:
            said = read("pose> ").strip()
            if said.lower() in ("", "back"):
                return None
            if said.isdigit() and 1 <= int(said) <= len(others):
                chosen = others[int(said) - 1]
            else:
                write(f"  a number from 1 to {len(others)}, or back")
    concern = concern_in(now.rationale)
    presets = {"process": request.process, "filament": request.filament}
    learned = request.lessons_for(chosen) if request.lessons_for is not None else ()
    offered = ways(concern, presets, learned) if concern is not None else []
    question = f"Why is {pose_value(now.rotation_deg)} wrong for this part?"
    if offered:
        assert concern is not None
        question += f" It turned it for {concern.name}, which settings can meet instead:"
    write(question)
    for n, way in enumerate(offered, 1):
        write(f"  {n}) {way.label} ({_settings(way.settings)}){_priced(request, way.settings)}")
    write("Say why in your own words" + (", or take a way by its number." if offered else "."))
    taken: list[Change] = []
    while True:
        said = (reason if reason is not None else read("why> ")).strip()
        reason = None
        if said.isdigit() and offered and 1 <= int(said) <= len(offered):
            way = offered[int(said) - 1]
            assert concern is not None
            said = f"{concern.name} is met by settings, not by turning the part: {way.label}"
            taken = [_setting_change(request, k, v, said) for k, v in way.settings.items()]
            break
        if said:
            break
        write("  A reason is needed: it's what the orientation step learns from next time.")
    readings = (f"the step's {_pose(current) if current else pose_value(now.rotation_deg)}; "
                f"yours {_pose(chosen)}")
    turned = Change(
        key=ORIENTATION, preset=pose_value(now.rotation_deg), value=pose_value(chosen.rotation_deg),
        reason=said, source=Source.SPONSOR, feature=", ".join(request.laid_traits),
        reading=readings,
    )
    return Review(corrections=(turned, *taken))


def _settings(settings: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in settings.items())


def _priced(request: ReviewRequest, settings: dict[str, str]) -> str:
    """Orca's estimate with these settings, and the difference from the slice shown."""
    if request.cost is None:
        return ""
    found = request.cost(tuple(_setting_change(request, k, v, "") for k, v in settings.items()))
    if found is None:
        return ": no estimate"
    more = (found.print_time_s - request.slice.print_time_s) // 60
    return f": {found.line()}, {more:+d} min"


def _preset_of(request: ReviewRequest, key: str) -> str | None:
    """A setting's value in the preset it's written to (`reach.profile_of`), "" where that
    preset doesn't list it (a declared key: shown as "-"), or None if it can't be placed. So a
    filament setting outside the bounded ones, such as `overhang_fan_speed`, can be corrected
    (mqucifer/print-shop#129), and is written where it was read (mqucifer/print-shop#206)."""
    tier = profile_of(key, request.process, request.filament)
    if tier is None:
        return None
    data = request.filament if tier == "filament" else request.process
    found = data.get(key, "")
    return str(found[0] if isinstance(found, list) and found else found)


def _setting_change(request: ReviewRequest, key: str, value: str, reason: str) -> Change:
    preset = _preset_of(request, key) or ""
    return Change(key=key, preset=preset, value=value, reason=reason or "a way by settings",
                  source=Source.SPONSOR)
