"""The Sponsor's review of a print's settings, on the command, before the approval gate.

mqucifer/print-shop#7: the command shows the presets, each change against the
preset's own value with its reason, Orca's estimate and the orientation. The
Sponsor approves, or corrects a setting by name, and it slices again. Home
Assistant's approval gate comes after this, unchanged: the review decides the
settings, the gate decides the print.

A correction is relative to the Sponsor's own tuning, so it is shown and kept
next to the preset's value. Corrections are the learning signal for
print-flow stage 9: what the Sponsor would have tuned.

The slice is read too (mqucifer/print-shop#105, `reading.py`): each layer's time,
each feature's time and area, and the seam. What matters for this part is
flagged, each with the change it proposes, its reason and the reading it rests
on. `take` takes them, or `take 2` one of them; nothing from a reading is
applied otherwise.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.setup.bounds import SETTINGS
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.reading import Flag, SliceReading
from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Review", "Reviewer", "ReviewRequest", "cli_reviewer", "parse_answer", "render"]

#: `key=value`, then an optional reason after "because" or a dash. A value may have spaces,
#: as Orca's own do (`ironing_type=no ironing`).
_CORRECTION = re.compile(
    r"^\s*([a-z_0-9]+)\s*=\s*(.+?)\s*(?:(?:\bbecause\b|\s-\s|\s?—\s?)\s*(.*))?$"
)
#: `take`, or `take 1 3`: the changes the slice's reading proposed.
_TAKE = re.compile(r"^\s*take\b\s*([\d ,]*)$", re.I)


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
            by = _BY[c.source] + (f" {c.lesson}" if c.lesson is not None else "")
            cites = ", ".join(x for x in (c.feature, c.knowledge) if x)
            was = f"{c.standard} -> {c.preset or '-'}" if c.standard else (c.preset or "-")
            lines.append(f"  {c.key.ljust(width)}  {was} -> {c.value}")
            lines.append(f"  {''.ljust(width)}    {c.reason} [{by}{': ' + cites if cites else ''}]")
            if c.reading:
                lines.append(f"  {''.ljust(width)}    read: {c.reading}")
    else:
        lines.append("  No changes: your presets as they are.")
    for c in proposal.kept:
        by = f" [lesson {c.lesson}]" if c.lesson is not None else ""
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
            lines.append(f"  {g.line()} ({used})")
    lines += [
        "",
        f"Orca's estimate: {s.print_time_s // 60} min {s.print_time_s % 60} s, "
        f"{s.filament_cm3} cm3" + (f", {s.filament_g} g" if s.filament_g else "")
        + f", {s.layer_count} layers",
        f"Orientation: {request.orientation.rotation_deg}, {request.orientation.rationale}",
    ]
    lines += _read(request)
    take = " or take what the slice proposes (take, or take 1)," if request.proposed else ""
    lines += [
        "",
        f"Type ok to go on to the approval gate,{take} or correct a setting by name, "
        "e.g. wall_loops=3 because it's decorative (several separated by ';'), or stop.",
    ]
    return "\n".join(lines)


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
        setting = SETTINGS.get(key)
        tier = "filament" if setting is not None and setting.filament else "process"
        data = request.filament if tier == "filament" else request.process
        if key not in data:
            return f"'{key}' isn't a setting in the {tier} preset"
        preset = data[key]
        preset = str(preset[0] if isinstance(preset, list) and preset else preset)
        known = request.proposal.get(key) or next(
            (c for c in request.proposal.kept if c.key == key), None
        )
        corrections.append(
            Change(key=key, preset=preset, value=value, reason=reason or "your correction",
                   source=Source.SPONSOR, standard=known.standard if known else "")
        )
    keys = {c.key for c in corrections}
    return Review(corrections=tuple(corrections),
                  taken=tuple(c for c in taken if c.key not in keys))


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
    """Show the review on the command line and read the answer, again until it's readable."""

    def review(request: ReviewRequest) -> Review:
        write(render(request))
        while True:
            parsed = parse_answer(read("> "), request)
            if isinstance(parsed, Review):
                return parsed
            write(f"  {parsed}")

    return review
