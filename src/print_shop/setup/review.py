"""The Sponsor's review of a print's settings, on the command, before the approval gate.

mqucifer/print-shop#7: the command shows the presets, each change against the
preset's own value with its reason, Orca's estimate and the orientation. The
Sponsor approves, or corrects a setting by name, and it slices again. Home
Assistant's approval gate comes after this, unchanged: the review decides the
settings, the gate decides the print.

A correction is relative to the Sponsor's own tuning, so it is shown and kept
next to the preset's value. Corrections are the learning signal for
print-flow stage 9: what the Sponsor would have tuned.

A change is shown as this print's only if it reached the slice
(mqucifer/print-shop#206, `proposal.read_back`). One that didn't is shown as asked and
sliced, and a key that can't be placed in a profile is refused when it's typed.

The slice is read too (mqucifer/print-shop#105, `reading.py`): each layer's time,
each feature's time and area, and the seam. What matters for this part is
flagged, each with the change it proposes, its reason and the reading it rests
on. `take` takes them, or `take 2` one of them; nothing from a reading is
applied otherwise.

**A plate of several parts is one review** (mqucifer/print-shop#109): how it prints, by object or
by layer and why, where each part is, and each part's pose, which is sent back by the part. A
pause on a plate printing by object is refused, as Orca writes none.

**The orientation can be sent back** (mqucifer/print-shop#128), in a short conversation
(`send_back`): the step says why it turned the part, the person names the pose it should print
in and says why the turn was wrong, or meets the step's concern by settings instead, each priced
by Orca. A reason is required: it's recorded as the correction, and drawn as a lesson the
orientation step is given next time. The part is then turned and sliced again.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from print_shop.core.state import AppliedOrientation, Orientation, SliceResult
from print_shop.plate import BY_LAYER, BY_OBJECT, SEQUENCE, SEQUENCES, PlateLayout
from print_shop.setup.concerns import concern_in, ways
from print_shop.setup.intake import Estimate
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    PAUSE_NAMES,
    Change,
    GivenLesson,
    Proposal,
    Source,
    pose_value,
)
from print_shop.setup.reach import profile_of, unplaced
from print_shop.setup.reading import Flag, SliceReading
from print_shop.slicer.orca import SlicerProfiles

__all__ = [
    "Review", "ReviewPart", "Reviewer", "ReviewRequest", "cli_reviewer", "parse_answer", "render",
    "send_back",
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


@dataclass(frozen=True)
class ReviewPart:
    """One part of a plate at its review: its pose, and what sending that back needs."""

    n: int
    name: str
    orientation: AppliedOrientation
    candidates: tuple[Orientation, ...] = ()
    laid_traits: tuple[str, ...] = ()
    lessons_for: Callable[[Orientation], tuple[GivenLesson, ...]] | None = None


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
    #: A job of several parts (mqucifer/print-shop#109): the plate as sliced, and each part.
    #: `candidates` and `laid_traits` are then each part's own.
    plate: PlateLayout | None = None
    parts: tuple[ReviewPart, ...] = ()

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
    #: Whose pose was sent back, by the part's number, on a plate of several.
    part: int | None = None


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
    if request.plate is not None:
        lines += [*request.plate.lines(), ""]
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
        + f", {s.layer_count} layers" + (" for the whole plate" if request.plate else ""),
        *(_parts(request) if request.parts else _oriented(request)),
    ]
    if request.context:
        lines += ["", "The part's context (its design run, or a sidecar beside it):",
                  *(f"  {line}" for line in request.context)]
    lines += _read(request)
    take = " or take what the slice proposes (take, or take 1)," if request.proposed else ""
    back = (" send a part's orientation back (orientation)," if request.parts
            else " send the orientation back (orientation)," if request.candidates else "")
    pause = "pause_at=12.4 because white to black for a colour change at a height"
    if request.plate is not None and request.plate.by_object:
        pause = f"{SEQUENCE}={BY_LAYER} to print them all at once (a pause_at needs that)"
    lines += [
        "",
        f"Type ok to go on to the approval gate,{take}{back} or correct a setting by name, "
        "e.g. wall_loops=3 because it's decorative (several separated by ';'), "
        f"{pause}, or stop.",
    ]
    return "\n".join(lines)


def _oriented(request: ReviewRequest) -> list[str]:
    return [
        f"Orientation: {request.orientation.rotation_deg}, {request.orientation.rationale}",
        # The pose the features, and the lessons keyed to them, were read in
        # (mqucifer/print-shop#128).
        f"  the part's features were read in this pose: {request.orientation.pose()}",
        *_poses(request),
    ]


def _parts(request: ReviewRequest) -> list[str]:
    """Each part's pose, as one part's is shown."""
    lines = []
    for part in request.parts:
        own = replace(request, orientation=part.orientation, candidates=part.candidates)
        lines += ["", f"Part {part.n}, {part.name}:", *(f"  {line}" for line in _oriented(own))]
    return lines


def _by(change: Change) -> str:
    """Who a change is from, as the review names them."""
    return _BY[change.source] + (f" {change.lesson}" if change.lesson is not None else "")


def _poses(request: ReviewRequest) -> list[str]:
    """Each pose the orientation step weighed, with the figures its choice rested on
    (mqucifer/print-shop#128): what would need support, the round or chamfer at the plate left out
    of it, the area on the plate and the height."""
    if not request.candidates:
        return []
    lines = ["  the poses it weighed (overhang is what would need support):"]
    for c in request.candidates:
        here = "  <- this print" if c.rotation_deg == request.orientation.rotation_deg else ""
        flare = (f", {c.flare_area_mm2:g} mm2 rising from the plate left out"
                 if c.flare_area_mm2 else "")
        lines.append(
            f"    {pose_value(c.rotation_deg):>9}  {c.overhang_area_mm2:g} mm2 overhang{flare}, "
            f"{c.bed_contact_area_mm2:g} mm2 on the plate, {c.height_mm:g} mm tall{here}"
        )
    return lines


def _read(request: ReviewRequest) -> list[str]:
    """What the slice shows, and what it proposes."""
    reading = request.reading
    if reading is None:
        return []
    lines = ["", "What the slice shows, layer by layer:"]
    lines += [f"  {line}" for line in reading.lines()]
    for flag in request.flags:
        if flag.change is None:
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
    for part in filter(None, (p.strip() for p in text.split(";"))):
        took = _TAKE.match(part)
        if took is not None:
            found = _take(took.group(1), request)
            if isinstance(found, str):
                return found
            taken += found
            continue
        match = _CORRECTION.match(part)
        if match is None:
            return f"'{part}' isn't setting=value"
        key, value, reason = match.group(1), match.group(2), (match.group(3) or "").strip()
        if request.plate is not None and key == SEQUENCE and value not in SEQUENCES:
            return f"{SEQUENCE} is {' or '.join(SEQUENCES)}"
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
    refused = _pause_by_object(corrections, request)
    if refused:
        return refused
    keys = {c.key for c in corrections}
    return Review(corrections=tuple(corrections),
                  taken=tuple(c for c in taken if c.key not in keys))


def _pause(value: str, reason: str, request: ReviewRequest) -> Change | str:
    """A colour change at a height inside the part (mqucifer/print-shop#119), or why not.
    `none` takes one away."""
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
        return f"a colour change is inside the part: above 0 and below its top, {top:g} mm"
    return Change(key=PAUSE, preset=shown.value if shown else "", value=f"{z:g}",
                  reason=reason or "a colour change", source=Source.SPONSOR)


def _pause_by_object(corrections: list[Change], request: ReviewRequest) -> str:
    """Why a plate can't both pause and print by object, or "": Orca writes no pause then, and
    says nothing (mqucifer/print-shop#109)."""
    if request.plate is None:
        return ""
    typed = {c.key: c for c in corrections}
    pause = typed.get(PAUSE) or request.proposal.get(PAUSE)
    if pause is None or pause.value == "none":
        return ""
    order = typed[SEQUENCE].value if SEQUENCE in typed else request.plate.sequence
    if order != BY_OBJECT:
        return ""
    return (
        "Orca writes no pause when it prints by object, so it would never pause: type "
        f"{SEQUENCE}={BY_LAYER}; {PAUSE}={pause.value} to print every part at once and pause "
        f"them all at that height, {PAUSE}=none for no pause, or print that part by itself"
    )


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
    read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> Reviewer:
    """Show the review on the command line and read the answer, again until it's readable.
    `orientation` starts the send-back conversation (`send_back`)."""

    def review(request: ReviewRequest) -> Review:
        write(render(request))
        while True:
            answer = read("> ")
            back = _SEND_BACK.match(answer)
            if back is not None and (request.candidates or request.parts):
                send = _send_back_part if request.parts else send_back
                sent = send(request, read, write, back.group(1), back.group(2))
                if sent is not None:
                    return sent
                write("  Back to the review: ok, a correction, orientation, or stop.")
                continue
            parsed = parse_answer(answer, request)
            if isinstance(parsed, Review):
                return parsed
            write(f"  {parsed}")

    return review


def _pose(o: Orientation) -> str:
    laid = "  as the file lays it" if o.rotation_deg == (0.0, 0.0, 0.0) else ""
    return (f"{pose_value(o.rotation_deg)}{laid}: {o.overhang_area_mm2:g} mm2 of overhang, "
            f"{o.bed_contact_area_mm2:g} mm2 on the plate, {o.height_mm:g} mm tall")


def _send_back_part(
    request: ReviewRequest, read: Callable[[str], str], write: Callable[[str], None],
    pose: str | None = None, reason: str | None = None,
) -> Review | None:
    """Which part's pose, on a plate of several, then that part's send-back as any part's."""
    write("Which part's orientation?")
    write("\n".join(f"  {p.n}) {p.name}, at {pose_value(p.orientation.rotation_deg)}"
                    for p in request.parts))
    numbered = {str(p.n): p for p in request.parts}
    while True:
        said = read("part> ").strip()
        if said.lower() in ("", "back"):
            return None
        if said in numbered:
            break
        write(f"  a number from 1 to {len(request.parts)}, or back")
    part = numbered[said]
    own = replace(request, orientation=part.orientation, candidates=part.candidates,
                  laid_traits=part.laid_traits, lessons_for=part.lessons_for)
    sent = send_back(own, read, write, pose, reason)
    return replace(sent, part=part.n) if sent is not None else None


def send_back(
    request: ReviewRequest, read: Callable[[str], str], write: Callable[[str], None],
    pose: str | None = None, reason: str | None = None,
) -> Review | None:
    """The pose sent back at the review, in a short conversation (mqucifer/print-shop#128).

    The step says why it turned the part; the person names the pose it should print in, and says
    why the turn was wrong in their own words, or by number takes a way of meeting the step's
    concern by settings, each priced by Orca. A reason is required. The correction is the pose,
    with that reason and both poses' readings; a way taken adds its settings as corrections.
    None is the person going back to the review without sending anything.
    """
    now = request.orientation
    current = next((c for c in request.candidates if c.rotation_deg == now.rotation_deg), None)
    others = [c for c in request.candidates if c.rotation_deg != now.rotation_deg]
    write(f"The orientation step printed it at {pose_value(now.rotation_deg)}: {now.rationale}")
    chosen: Orientation | None = None
    if pose is not None and pose.isdigit() and 1 <= int(pose) <= len(others):
        chosen = others[int(pose) - 1]
    if chosen is None:
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
