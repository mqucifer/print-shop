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
mismatch is said (mqucifer/print-shop#203, `_pauses`). It says what it's for, a swap or an insert
(mqucifer/print-shop#218, `pauses`): `colour_change=` is a swap, and what wasn't typed is asked
(`_purposed`), with this review's or the past print's pre-selected.

A change is shown as this print's only if it reached the slice
(mqucifer/print-shop#206, `proposal.read_back`). One that didn't is shown as asked and
sliced, and a key that can't be placed in a profile is refused when it's typed.

The slice is read too (mqucifer/print-shop#105, `reading.py`): each layer's time,
each feature's time and area, and the seam. What matters for this part is
flagged, each with the change it proposes, its reason and the reading it rests
on. `take` takes them, or `take 2` one of them; nothing from a reading is
applied otherwise.

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
import sqlite3
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from print_shop.bench.pausing import TOPS_OUT, mm
from print_shop.core.state import AppliedOrientation, Orientation, SliceResult
from print_shop.inventory import InventoryError, Material, NotKnown
from print_shop.setup import pauses
from print_shop.setup.concerns import concern_in, ways
from print_shop.setup.intake import Estimate
from print_shop.setup.offer import Item, Offering
from print_shop.setup.offer import render as render_offer
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    PAUSE_NAMES,
    SWAP_NAMES,
    Change,
    GivenLesson,
    Proposal,
    Source,
    pose_value,
)
from print_shop.setup.reach import profile_of, unplaced
from print_shop.setup.reading import PAUSE_PAST_FIT, Flag, SliceReading
from print_shop.setup.waiting import can_ask, drop_typed
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
    #: The spool the job was prepared with, and the spools on hand, for a swap to name its two
    #: (mqucifer/print-shop#218). None with no stock set up.
    material: Material | None = None
    stock: pauses.Stock | None = None
    #: Since which print what's on the printer isn't known (mqucifer/print-shop#218).
    not_known: Callable[[], NotKnown | None] | None = None
    #: Records a spool as loaded, with the print said to be over in the same word or None, on
    #: the person's typed y and only that (`_loaded_first`).
    record_load: Callable[[int, NotKnown | None], None] | None = None

    @property
    def proposed(self) -> tuple[Flag, ...]:
        """The flags that propose a change, numbered from 1 at the review."""
        return tuple(f for f in self.flags if f.change is not None)


@dataclass(frozen=True)
class Review:
    """The Sponsor's answer: approve, corrections or readings' changes to slice again with,
    or stop."""

    approved: bool = False
    corrections: tuple[Change, ...] = ()
    stop: bool = False
    #: The changes the reading proposed that the Sponsor took (`take`).
    taken: tuple[Change, ...] = ()
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
    if proposal.reasoning:
        lines += ["How the part was read:", *_wrap(proposal.reasoning), ""]
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
    paused = proposal.get(PAUSE)
    purpose = paused.pause if paused is not None and paused.value != "none" else None
    try:
        unknown = request.not_known() if request.not_known else None
        why = unknown.why() if unknown is not None else ""
        if purpose is not None:
            lines += pauses.for_review(purpose, request.reading, request.material, request.stock,
                                       why)
        if why and (purpose is None or purpose.start is None):  # a swap's LOAD FIRST says it
            lines.append(f"  {why}: at ok you're asked, and Start holds until you say")
    except sqlite3.Error as exc:
        lines.append(f"  the stock couldn't be read ({exc}): an Approve checks it again")
    for c in proposal.kept:
        by = (
            f" [lesson {c.lesson}]" if c.lesson is not None
            # An answer that keeps the preset's value is a decision too (mqucifer/print-shop#124).
            else f" [{_BY[c.source]}: {c.feature}]" if c.source is Source.ANSWER
            else ""
        )
        lines.append(f"  kept {c.key} at {c.preset}: {c.reason}{by}")
    for note in proposal.design_notes:
        lines.append(f"  design: {note}")
    for note in proposal.notes:
        lines.append(f"  note: {note}")
    for refusal in proposal.refused:
        lines.append(f"  refused from the model: {refusal}")
    if proposal.model.startswith("cut off"):
        lines.append(
            f"  CUT OFF: the model's answer was {proposal.model}. Nothing it proposed is "
            "used; only the guards' changes stand. This is recorded, to tune the limit."
        )
    elif proposal.model.startswith("unavailable"):
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
    if request.offering is not None:
        pose = pose_value(request.orientation.rotation_deg)
        lines += render_offer(request.offering, proposal, pose)
    use = (" use what decided that print (use, or use 1 3),"
           if request.offering is not None and request.offering.options else "")
    take = " or take what the slice proposes (take, or take 1)," if request.proposed else ""
    back = " send the orientation back (orientation)," if request.candidates else ""
    lands = "it pauses once the layer topping out at 12.4 mm is done"
    example = (
        f"pause_at=12.4 for insert because the nuts go in ({lands})"
        if _insert_stands_or_is_ticked(request)
        else f"pause_at=12.4 because the nuts go in ({lands}, and it asks what the pause is "
             "for), colour_change=12.4 because white to black (a swap)"
    )
    lines += [
        "",
        f"Type ok to go on to the approval gate,{use}{take}{back} or correct a setting by name, "
        f"e.g. wall_loops=3 because it's decorative (several separated by ';'), {example}, "
        "or stop.",
    ]
    return "\n".join(lines)


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


def _wrap(text: str, width: int = 96) -> list[str]:
    return ["  " + line for line in textwrap.wrap(text, width)]


def parse_answer(answer: str, request: ReviewRequest) -> Review | str:
    """The Sponsor's answer as a `Review`, or why it can't be read."""
    text = answer.strip()
    if text.lower() in ("ok", "yes", "y", "approve"):
        return Review(approved=True)
    if text.lower() in ("stop", "no", "n"):
        return Review(stop=True)
    corrections = []
    taken: list[Change] = []
    used: list[Change] = []
    offering = request.offering
    picked = False
    for part in filter(None, (p.strip() for p in text.split(";"))):
        took = _TAKE.match(part)
        if took is not None:
            found = _take(took.group(1), request)
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
                for item in items:
                    # Again here, as the stock can change while the review waits.
                    if item.key == PAUSE and (why := pauses.cant_start(
                        item.pause, request.material, request.stock() if request.stock else ()
                    )):
                        return f"{PAUSE} can't be used: {why}"
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
            change = _pause(key, value, reason, request)
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
    taken = [c for c in taken if c.key not in keys]
    # Typed wins over taken, and both over used.
    over = keys | {c.key for c in taken}
    used = [c for c in {c.key: c for c in used}.values() if c.key not in over]
    if taken and any(c.key == ORIENTATION for c in used):
        return "that turns the part, which slices it afresh: use it, then take at the next review"
    return Review(corrections=(*used, *corrections), taken=tuple(taken),
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
    purpose, reason = None, item.reason
    if item.key == PAUSE:
        purpose = pauses.refreshed(item.pause, request.material, request.stock)
        reason = pauses.because(reason, purpose)
    return Change(key=item.key, preset=item.preset, value=item.value, reason=reason,
                  source=Source.SPONSOR, standard=known.standard if known else "",
                  reading=item.reading, used=item.used, pause=purpose)


def _pause(key: str, value: str, reason: str, request: ReviewRequest) -> Change | str:
    """A pause after the layer that tops out at a height inside the part
    (mqucifer/print-shop#119, mqucifer/print-shop#203), and what it's for as far as that's typed
    (mqucifer/print-shop#218), or why not. `none` takes one away."""
    shown = request.proposal.get(PAUSE)
    if value.lower() == "none":
        return Change(key=PAUSE, preset="", value="none", reason=reason or "no pause",
                      source=Source.SPONSOR)
    typed = pauses.read_typed(value, swap=key in SWAP_NAMES)
    if isinstance(typed, str):
        return typed
    z = typed.z
    top = request.slice.max_z_mm
    if not 0 < z < top:
        return f"a pause is inside the part: above 0 and below its top, {top:g} mm"
    layers = request.reading.layers if request.reading is not None else ()
    if layers and z < layers[0].z - TOPS_OUT:
        return (f"there's no layer to pause after at {z:g} mm: the first tops out at "
                f"{layers[0].z:g} mm")
    purpose = pauses.planned(typed, shown.pause if shown else None, request.material,
                             request.stock)
    if isinstance(purpose, str):
        return purpose
    return Change(key=PAUSE, preset=shown.value if shown else "", value=f"{z:g}",
                  reason=pauses.because(reason, purpose), source=Source.SPONSOR, pause=purpose)


def _purposed(
    review: Review, request: ReviewRequest, read: Callable[[str], str],
    write: Callable[[str], None],
) -> Review | None:
    """The answer with what its pause is for, asking what wasn't said (mqucifer/print-shop#218).
    Only the last pause in it stands, so only that's asked. None is the person going back."""
    at = next((n for n in reversed(range(len(review.corrections)))
               if review.corrections[n].key == PAUSE), None)
    if at is None or review.corrections[at].value == "none":
        return review
    change = review.corrections[at]
    if change.pause is not None and change.pause.complete(request.material):
        return review
    # With the print picked, and what was cleared, in this same answer.
    if review.offering is not None:
        request = replace(request, offering=review.offering)
    found = pauses.ask(change.value, change.pause, _before(request, change.value),
                       request.material, request.stock, read, write,
                       gone=_past_cant_start(request, change.value))
    if found is None:
        return None
    done = change.model_copy(update={"pause": found,
                                     "reason": pauses.because(change.reason, found)})
    return replace(review, corrections=(*review.corrections[:at], done,
                                        *review.corrections[at + 1:]))


def _loaded_first(
    request: ReviewRequest, read: Callable[[str], str], write: Callable[[str], None],
    forget: Callable[[], None], asks: Callable[[], bool],
) -> None:
    """On `ok`, whether the spool this print starts on is on the printer now: a swap's first with
    LOAD FIRST, and any job's while what's on isn't known. Then the question says both halves,
    that print's being over too. A typed y records the load, and clears what it was asked of and
    only that; anything else writes nothing (mqucifer/print-shop#218).

    What was typed before the question was shown is dropped first: a doubled y would answer it
    unseen. With no terminal to ask in (`asks`) it isn't asked, as a piped y would answer it. A
    stock that can't be read or written, or input that ends, doesn't lose the job, as Start still
    checks.
    """
    if request.stock is None or request.record_load is None:
        return
    try:
        on_hand = request.stock()
        unknown = request.not_known() if request.not_known else None
    except sqlite3.Error as exc:
        write(f"  The stock couldn't be read ({exc}): an Approve checks it again.")
        return
    change = request.proposal.get(PAUSE)
    pause = change.pause if change is not None and change.value != "none" else None
    if pause is not None and pause.start is not None:
        first = pauses.first_to_load(pause, on_hand, unknown.why() if unknown else "")
    else:
        first = request.material if unknown is not None else None
    if first is None or all(spool.id != first.spool_id for spool in on_hand):
        return
    if not asks():
        write("  There's no terminal to ask in, so nothing's recorded: `stock.py load "
              f"{first.spool_id}` in one before you approve, as an Approve before starts nothing.")
        return
    if unknown is not None:
        write(f"{unknown.over_and_on(first.named())} y says both, and records it; Enter for "
              "later, and an Approve before you've said starts nothing.")
    else:
        write(f"Is {first.named()}, on the printer now? y records it (stock load "
              f"{first.spool_id}); Enter for later, and an Approve before it's loaded starts "
              "nothing.")
    forget()
    try:
        typed = read("loaded> ")
    except EOFError:
        typed = ""
    if typed.strip().lower() != "y":
        write(f"  Nothing recorded. Load it and `stock.py load {first.spool_id}` before you "
              "approve.")
        return
    try:
        request.record_load(first.spool_id, unknown)
    except (InventoryError, sqlite3.Error) as exc:
        write(f"  Not recorded: {exc}")
        return
    write(f"  Recorded: {first.named()} is loaded.")


def _before(request: ReviewRequest, value: str) -> tuple[pauses.Pause, str] | None:
    """What a pause at `value` is pre-selected to be for, and from where: this review's pause,
    else the picked past print's at that height, while it's ticked. One typed at another height
    didn't come from that print, and one cleared, in doubt or not usable isn't pre-selected:
    Enter mustn't make it what that one was."""
    standing = request.proposal.get(PAUSE)
    if standing is not None and standing.pause is not None:
        return standing.pause, "as it is now"
    item = _past_pause(request, value)
    if item is None or item.pause is None:
        return None
    assert request.offering is not None
    pose = pose_value(request.orientation.rotation_deg)
    if not request.offering.standing(item, request.proposal, pose).selected:
        return None
    return item.pause, f"as on {item.used.stl_name}"


def _past_pause(request: ReviewRequest, value: str) -> Item | None:
    past = request.offering.past if request.offering is not None else None
    items = past.items if past is not None else ()
    return next((i for i in items if i.key == PAUSE and i.value == value), None)


def _past_cant_start(request: ReviewRequest, value: str) -> str:
    """Why the picked print's swap at that height can't start this job as it did, or ""."""
    item = _past_pause(request, value)
    if item is None or request.stock is None:
        return ""
    return pauses.cant_start(item.pause, request.material, request.stock())


def _insert_stands_or_is_ticked(request: ReviewRequest) -> bool:
    """Whether the pause here is an insert, or the one ticked to use is: then the review's
    example doesn't speak of a swap."""
    standing = request.proposal.get(PAUSE)
    if standing is not None and standing.value != "none" and standing.pause is not None:
        return standing.pause.kind is pauses.PauseFor.INSERT
    if request.offering is None:
        return False
    pose = pose_value(request.orientation.rotation_deg)
    return any(item.key == PAUSE and item.pause is not None
               and item.pause.kind is pauses.PauseFor.INSERT
               for item in request.offering.ticked(request.proposal, pose))


def _take(numbers: str, request: ReviewRequest) -> list[Change] | str:
    """The reading's changes `take` names: all of them, or those numbered."""
    proposed = request.proposed
    if not proposed:
        return "the slice proposes nothing to take"
    picked = [int(n) for n in re.findall(r"\d+", numbers)]
    if not picked:
        picked = list(range(1, len(proposed) + 1))
    wrong = [n for n in picked if not 1 <= n <= len(proposed)]
    if wrong:
        return f"take a number from 1 to {len(proposed)}"
    return [c for n in picked if (c := proposed[n - 1].change) is not None]


def cli_reviewer(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print,
    forget: Callable[[], None] = drop_typed,
    asks: Callable[[], bool] | None = None,
) -> Reviewer:
    """Show the review on the command line and read the answer, again until it's readable.
    `orientation` starts the send-back conversation (`send_back`). `forget` drops what was typed
    before the load's question was shown: a y approves here too, and mustn't answer that. `asks`
    is whether there's a terminal to ask that in (`can_ask`)."""

    def review(request: ReviewRequest) -> Review:
        write(render(request))
        moved: Offering | None = None  # the offer as picked and cleared here
        told = False  # that what's ticked isn't used yet
        while True:
            answer = read("> ")
            back = _SEND_BACK.match(answer)
            if back is not None and request.candidates:
                sent = send_back(request, read, write, back.group(1), back.group(2))
                if sent is not None:
                    return replace(sent, offering=moved) if moved is not None else sent
                write("  Back to the review: ok, a correction, orientation, or stop.")
                continue
            try:
                parsed = parse_answer(answer, request)
                if isinstance(parsed, str):
                    write(f"  {parsed}")
                    continue
                purposed = _purposed(parsed, request, read, write)
            except sqlite3.Error as exc:
                write(f"  The stock couldn't be read ({exc}): type it again.")
                continue
            if purposed is None:
                write("  Back to the review: ok, a correction, or stop.")
                continue
            parsed = purposed
            if parsed.browsing:
                # Only the offer moved, so only the offer is shown again.
                moved = parsed.offering
                assert moved is not None
                request = replace(request, offering=moved)
                pose = pose_value(request.orientation.rotation_deg)
                write("\n".join(render_offer(moved, request.proposal, pose)))
                told = False
                continue
            if parsed.approved and not told and request.offering is not None:
                pose = pose_value(request.orientation.rotation_deg)
                left = len(request.offering.ticked(request.proposal, pose))
                if left:
                    told = True
                    aren, them = ("isn't", "it") if left == 1 else ("aren't", "them")
                    write(f"  {left} ticked {aren} used yet: use to take {them}, "
                          "or ok again to go on without")
                    continue
            if parsed.approved:
                _loaded_first(request, read, write, forget, asks or can_ask)
            if moved is not None and parsed.offering is None:
                return replace(parsed, offering=moved)
            return parsed

    return review


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
