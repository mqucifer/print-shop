"""A job's opening lines, as one block (mqucifer/print-shop#266).

Before, `print_job.py` said each as it came: what the inventory has, the presets and whose tuning
each is, whether it's armed, the part's context and the job's log, up to 132 columns wide. Now
they're one block, THIS JOB, its rows aligned and wrapped as the review is. The block is said
before the job is submitted, as those lines were, and the job's id, its log and what comes next
follow it once it's submitted. What's asked (the spool that's on) and what's refused still say
so when they happen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from print_shop.setup.layout import WIDTH, wrap

__all__ = ["ThisJob"]

#: A row's name, and where its value starts.
_ROW = 12

#: A preset's note from the store: whose tuning it is, or that it's the standard.
_NOTE = re.compile(r"^(machine|process|filament) '(.+)': (.+)$")
_TUNING = "your tuning of '"


@dataclass
class ThisJob:
    """What the command says about the job before it starts, filled as it's found."""

    #: The part's path, or a plate's parts, each with what its context says.
    part: str = ""
    parts: list[tuple[str, str]] = field(default_factory=list)
    #: What the inventory has, or why it isn't read.
    inventory: str = ""
    #: The nozzle and filament it's sliced for.
    fitted: str = ""
    #: Each preset, by kind, and the store's notes on them.
    presets: list[tuple[str, str]] = field(default_factory=list)
    notes: tuple[str, ...] = ()
    armed: str = ""
    recording: str = ""
    context: str = ""

    def lines(self, width: int = WIDTH) -> list[str]:
        """The block, to `width`."""
        width = min(width, WIDTH)
        out = ["THIS JOB"]
        if self.part:
            out += _row("part", self.part, width)
        for n, (name, said) in enumerate(self.parts, 1):
            out += _row(f"part {n}", f"{name}. Its context: {said}", width)
        if self.inventory:
            out += _row("inventory", self.inventory, width)
        if self.fitted:
            out += _row("fitted", self.fitted, width)
        whose, other = _whose(self.notes, dict(self.presets))
        for kind, name in self.presets:
            out += _row(kind, name, width)
            if kind in whose:
                out += wrap(" " * (_ROW + 2), whose[kind], width)
        out += [line for note in other for line in _row("", f"({note})", width)]
        if self.armed:
            out += _row("arming", self.armed, width)
        if self.recording:
            out += _row("recording", self.recording, width)
        if self.context:
            out += _row("context", self.context, width)
        return out

    @staticmethod
    def started(job_id: str, log: str, how: str, width: int = WIDTH) -> list[str]:
        """The rows that follow the block once the job is submitted: its id and what comes next,
        and its log."""
        width = min(width, WIDTH)
        out = _row("job", f"{job_id}: {how}", width) if how else _row("job", job_id, width)
        return [*out, *(_row("log", log, width) if log else [])]


def _row(name: str, value: str, width: int) -> list[str]:
    return wrap(f"  {name:<{_ROW - 2}}", value, width, " " * _ROW)


def _whose(notes: tuple[str, ...], presets: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """The store's notes by the preset they're on ("your tuning of …", "the standard; you
    haven't tuned it"), said beneath it; and those that name no preset shown, as they are."""
    found: dict[str, str] = {}
    other: list[str] = []
    for note in notes:
        m = _NOTE.match(note)
        if m is None or m.group(1) in found or presets.get(m.group(1)) != m.group(2):
            other.append(note)
        else:
            # "your tuning of 'X'": the name unquoted, as it's on a row of its own.
            said = m.group(3)
            if said.startswith(_TUNING) and said.endswith("'"):
                said = f"your tuning of {said[len(_TUNING):-1]}"
            found[m.group(1)] = said
    return found, other
