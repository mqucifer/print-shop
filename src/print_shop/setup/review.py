"""The Sponsor's review of a print's settings, on the command, before the approval gate.

mqucifer/print-shop#7: the command shows the presets, each change against the
preset's own value with its reason, Orca's estimate and the orientation. The
Sponsor approves, or corrects a setting by name, and it slices again. Home
Assistant's approval gate comes after this, unchanged: the review decides the
settings, the gate decides the print.

A correction is relative to the Sponsor's own tuning, so it is shown and kept
next to the preset's value. Corrections are the learning signal for
print-flow stage 9: what the Sponsor would have tuned.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.setup.knowledge import SETTINGS
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Review", "Reviewer", "ReviewRequest", "cli_reviewer", "parse_answer", "render"]

#: `key=value`, then an optional reason after "because" or a dash.
_CORRECTION = re.compile(r"^\s*([a-z_0-9]+)\s*=\s*([^\s;]+)\s*(?:(?:because|-|—)\s*(.*))?$")


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


@dataclass(frozen=True)
class Review:
    """The Sponsor's answer: approve, corrections to slice again with, or stop."""

    approved: bool = False
    corrections: tuple[Change, ...] = ()
    stop: bool = False


class Reviewer(Protocol):
    def __call__(self, request: ReviewRequest) -> Review: ...


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
        lines.append(f"  {'setting'.ljust(width)}  your preset -> this print")
        for c in changes:
            by = {Source.MODEL: "model", Source.GUARD: "guard", Source.SPONSOR: "you"}[c.source]
            cites = ", ".join(x for x in (c.feature, c.knowledge) if x)
            lines.append(f"  {c.key.ljust(width)}  {c.preset or '-'} -> {c.value}")
            lines.append(f"  {''.ljust(width)}    {c.reason} [{by}{': ' + cites if cites else ''}]")
    else:
        lines.append("  No changes: your presets as they are.")
    for c in proposal.kept:
        lines.append(f"  kept {c.key} at {c.preset}: {c.reason}")
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
    lines += [
        "",
        f"Orca's estimate: {s.print_time_s // 60} min {s.print_time_s % 60} s, "
        f"{s.filament_cm3} cm3" + (f", {s.filament_g} g" if s.filament_g else "")
        + f", {s.layer_count} layers",
        f"Orientation: {request.orientation.rotation_deg}, {request.orientation.rationale}",
        "",
        "Type ok to go on to the approval gate, or correct a setting by name, "
        "e.g. wall_loops=3 because it's decorative (several separated by ';'), or stop.",
    ]
    return "\n".join(lines)


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
    for part in filter(None, (p.strip() for p in text.split(";"))):
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
        corrections.append(
            Change(key=key, preset=preset, value=value, reason=reason or "your correction",
                   source=Source.SPONSOR)
        )
    return Review(corrections=tuple(corrections))


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
