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

**The model's settings are proposals** (mqucifer/print-shop#242, `suggest.py`). The review
doesn't wait for them: it says the model is working, and adds them to the list when Enter asks,
numbered on from the slice's. `take 2` takes one, bare `take` stays the slice's, and
`no 2 because ...` turns one down as a correction. An `ok` before they're in, or before they've
been shown, says so once.

**Use these** (mqucifer/print-shop#211, `offer.py`): what decided the most similar past print,
pre-selected, each with why. `use` takes the selected ones, `use 2 4` those numbered, `clear 3`
clears one and `from 2` picks another print. Only `use` applies any. An `ok` with ticked ones
unused says so once, and the next goes on without them (`cli_reviewer`).

**A plate of several parts is one review** (mqucifer/print-shop#109): how it prints, by object or
by layer and why, where each part is, and each part's pose, which is sent back by the part. A
pause on a plate printing by object is refused, as Orca writes none.

**A part is never turned on the plate unsaid** (mqucifer/print-shop#257). One that doesn't fit as
posed is shown sliced in a turn that fits, saying so. `turn` takes that turn, and `ok` waits
for it.

**The orientation can be sent back** (mqucifer/print-shop#128), in a short conversation
(`send_back`): the step says why it turned the part, the person names the pose it should print
in and says why the turn was wrong, or meets the step's concern by settings instead, each priced
by Orca. A reason is required: it's recorded as the correction, and drawn as a lesson the
orientation step is given next time. The part is then turned and sliced again.

**A pose still being measured says so** (mqucifer/print-shop#235). A kept pose's job doesn't
wait for the other five, so the table can be drawn before they're in. `orientation` waits for
them, saying that it is, and then lists them as ever.

**It's laid out to be read** (mqucifer/print-shop#223, `screen.py`): in sections, aligned and
wrapped to the terminal, with Look first said again above the prompt and, from the second round
on, what moved marked and listed. Use these' items, the slice's and the model's share one number
space, in that order, and a number typed to the wrong command says whose it is.

**A slip doesn't cost the rest of the line** (mqucifer/print-shop#224, `read_answer`). The
parts that read are held, applied to nothing, while the one that doesn't is asked about at
`fix>`: corrected, dropped, or `back` for none of it. Nothing's sliced till it's settled, so it's
one slice. A `;` starts an answer only before one (`key=`, `take`, `use`, `clear`, `from`,
`no 2`): anywhere else it's the reason's. Pasted lines are one answer: its corrections are read,
its other lines asked about once, and an `ok` or `stop` in it approves and ends nothing.

**A setting is found by plain words** (mqucifer/print-shop#222, `names.py`). `find bridge fan`
lists the settings this filament and process take that fit, best first, each by its plain name,
today's value and its key, at the prompt or at `fix>`; it changes nothing. A key that can't be
used says whether it's no Orca setting, with the nearest, or a real one these presets don't
list, with the one that does its job here (`_refused`), and `bridge fan=60` says the key to type.
A setting is shown by its plain name wherever it's listed, with its key beside or beneath.
"""

from __future__ import annotations

import functools
import re
import shutil
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TextIO

from print_shop.bench.pausing import TOPS_OUT, mm
from print_shop.core.state import AppliedOrientation, Orientation, SliceResult, turn_words
from print_shop.inventory import Asked, InventoryError, Material
from print_shop.plate import BY_LAYER, BY_OBJECT, SEQUENCE, SEQUENCES, PlateLayout
from print_shop.setup import names, pauses
from print_shop.setup.concerns import concern_in, ways
from print_shop.setup.intake import Estimate, drop_typed
from print_shop.setup.offer import Item, Offering
from print_shop.setup.offer import render as render_offer
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    PAUSE_NAMES,
    SWAP_NAMES,
    Change,
    Ending,
    GivenLesson,
    Proposal,
    Source,
    Suggested,
    pose_value,
)
from print_shop.setup.reach import profile_of
from print_shop.setup.reading import Flag, SliceReading
from print_shop.setup.screen import (
    NOT_FOR_A_PLATE,
    WIDTH,
    Moved,
    by_model,
    found,
    sections,
    warnings,
    what_moved,
    wrap,
)
from print_shop.setup.suggest import (
    Listed,
    Suggesting,
    by_number,
    didnt_come,
    listed,
    looked_up,
    turning,
    waited,
    went_on,
)
from print_shop.setup.waiting import _keys_waiting, can_ask, terminal
from print_shop.slicer.orca import SlicerProfiles, parse_config_block

if TYPE_CHECKING:
    from print_shop.ingest.measuring import Measuring

__all__ = [
    "Left", "Review", "ReviewPart", "Reviewer", "ReviewRequest", "Slipped", "cli_reviewer",
    "parse_answer", "read_answer", "render", "send_back",
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
#: `find bridge fan`: the settings that fit the words, looked up (mqucifer/print-shop#222).
_FIND = re.compile(r"^\s*find\b\s*(.*)$", re.I | re.S)
#: `bridge fan=60`: a setting named in words where its key goes.
_WORDS_SET = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9_ ]*?)\s*=\s*(\S.*?)\s*$")
#: `turn`: the turn on the plate that a part which doesn't fit as posed is offered.
_TURN = "turn"
#: `use`, `use 2 4`, `clear 3` or `from 2`: what the most similar past print offers.
_OFFER = re.compile(r"^\s*(use|clear|from)\b\s*([\d ,]*)$", re.I)
#: What a `;` must stand before to start the next answer (mqucifer/print-shop#224): a `key=`,
#: or the whole of a `take`, `use`, `clear` or `from`, or a `no 2` and its reason. Anywhere
#: else, as in a reason, it's the answer's own.
_STARTS = re.compile(
    r"\s*(?:[a-z_0-9]+\s*="
    r"|(?i:take|use|clear|from)\b[\d ,]*\s*(?=;|$)"
    r"|(?i:no)\s+\d+\b\s*(?:(?i:because)\b|-|—|:|(?=;|$)))"
)
#: How an `ok` and the words like it are typed: in a paste they approve and end nothing.
_ALONE = ("ok", "yes", "y", "approve", "stop", "no", "n", _TURN)


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
    #: A job of several parts (mqucifer/print-shop#109): the plate as sliced, and each part.
    #: `candidates` and `laid_traits` are then each part's own.
    plate: PlateLayout | None = None
    parts: tuple[ReviewPart, ...] = ()
    #: Why the last answer was taken back, where the plate couldn't be sliced with it.
    undone: str = ""
    #: The model's proposals as this screen has them (mqucifer/print-shop#242): what a number
    #: typed is read against. None: no model proposes here.
    suggested: Suggested | None = None
    #: The model at work on them, beside the review: how long, and what it's come to since.
    suggesting: Suggesting | None = None
    #: The model's proposals turned down at this job so far, each saying why.
    turned_down: tuple[Change, ...] = ()
    #: The spool the job was prepared with, and the spools on hand, for a swap to name its two
    #: (mqucifer/print-shop#218). None with no stock set up.
    material: Material | None = None
    stock: pauses.Stock | None = None
    #: The stock's changes, and since which print what's on isn't known, read as the person is
    #: about to be asked (mqucifer/print-shop#218).
    stands: Callable[[], Asked] | None = None
    #: Records a spool as loaded, and the print said to be over in the same word, on the
    #: person's typed y and only that (`_loaded_first`): only while it's as it was when asked.
    record_load: Callable[[int, Asked], None] | None = None
    #: The turn this slice needed to fit (`SliceResult.turn_deg`) has been taken
    #: (mqucifer/print-shop#257).
    turn_taken: bool = False

    @property
    def turn_waiting(self) -> bool:
        """It fits only turned on the plate, and the person hasn't taken the turn."""
        return bool(self.slice.turn_deg) and not self.turn_taken

    @property
    def other_sides(self) -> tuple[Orientation, ...]:
        """The poses it can be sent back to: the others, save one that fits in no turn on the
        plate, which couldn't be sliced (mqucifer/print-shop#257)."""
        return tuple(c for c in self.candidates
                     if c.rotation_deg != self.orientation.rotation_deg and not c.fits_no_way)

    @property
    def another_side(self) -> bool:
        """Whether orientation may have another side down to offer: one that fits, or one still
        being measured, which it waits for."""
        measuring = self.measuring
        if measuring is None:
            return bool(self.other_sides)
        if measuring.pending():
            return True
        return bool(replace(self, candidates=measuring.ready()).other_sides)

    @property
    def proposed(self) -> tuple[Flag, ...]:
        """The flags that propose a change, in the order they're numbered (`numbered`)."""
        return tuple(f for f in self.flags if f.change is not None)

    @property
    def offered(self) -> int:
        """The numbers Use these holds, which come first: the most items of any past print on
        offer, so that picking another moves none of the slice's or the model's numbers. One
        number space, so that a number on screen is one thing (mqucifer/print-shop#223)."""
        offering = self.offering
        return offering.span() if offering else 0

    @property
    def numbered(self) -> list[tuple[int, Flag]]:
        """The slice's proposals, numbered on from Use these' items."""
        return list(enumerate(self.proposed, self.offered + 1))

    @property
    def by_model(self) -> list[Listed]:
        """The model's proposals on this screen, numbered on from the slice's."""
        return listed(self.suggested, self.proposal,
                      pose_value(self.orientation.rotation_deg),
                      self.offered + len(self.proposed))

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
    #: Whose pose was sent back, by the part's number, on a plate of several.
    part: int | None = None
    #: The model's proposals turned down, saying why, by key. Each is a correction as well.
    declined: tuple[str, ...] = ()
    #: The model's proposals as the last screen before the answer had them, where a reviewer
    #: added to them itself. None: as the request had them.
    saw: Suggested | None = None
    #: The offer as the answer left it, where it picked another print or cleared an item.
    offering: Offering | None = None
    #: The turn offered to fit the plate was taken (`turn`, mqucifer/print-shop#257).
    turn_taken: bool = False

    @property
    def browsing(self) -> bool:
        """Only the offer was changed, or the turn taken: there's nothing to slice again."""
        return (self.offering is not None or self.turn_taken) and not (
            self.approved or self.stop or self.corrections or self.taken
        )


@dataclass(frozen=True)
class Left:
    """The lines of a paste that aren't answers: one part, asked about once
    (mqucifer/print-shop#224)."""

    lines: tuple[str, ...]

    def why(self) -> str:
        first = self.lines[0].strip()
        shown = first if len(first) <= 40 else first[:39] + "\u2026"
        them = (f"'{shown}'" if len(self.lines) == 1
                else f"'{shown}' and {len(self.lines) - 1} more lines")
        one = "isn't an answer" if len(self.lines) == 1 else "of the paste aren't answers"
        said = [w for w in _ALONE if any(line.strip().lower() == w for line in self.lines)]
        alone = (f" ({' or '.join(said)} in a paste approves and ends nothing: type it on its "
                 "own)") if said else ""
        return f"{them} {one}{alone}"


@dataclass(frozen=True)
class Slipped:
    """An answer some of whose parts can't be read (mqucifer/print-shop#224): the others are held,
    applied to nothing, while the first that can't is asked about, to correct or drop. Nothing
    is sliced till it's all read."""

    parts: tuple[str | Left, ...]
    #: Each part that can't be read, by its place in `parts`, and why not.
    slips: tuple[tuple[int, str], ...]

    @property
    def held(self) -> tuple[str, ...]:
        """The parts that are read, each as short as says what it is: a correction's reason
        isn't repeated."""
        bad = {at for at, _ in self.slips}
        out = []
        for at, part in enumerate(self.parts):
            if at in bad or isinstance(part, Left):
                continue
            match = _CORRECTION.match(part)
            out.append(f"{match.group(1)}={match.group(2)}" if match else part)
        return tuple(out)

    def asked(self, width: int) -> list[str]:
        """What's held and what isn't, as the command shows it above the question."""
        at, why = self.slips[0]
        part = self.parts[at]
        lines = wrap("  Held, nothing sliced yet: ", " \u00b7 ".join(self.held), width,
                     "    ") if self.held else []
        lines += wrap("  ", why if isinstance(part, Left) or why.startswith(f"'{part}'")
                      else f"'{part}': {why}", width, "    ")
        it = "them" if isinstance(part, Left) and len(part.lines) > 1 else "it"
        what = "what it should say" if isinstance(part, Left) else "it corrected"
        if it == "them":
            what = "what they should say"
        lines += wrap("  ", f"Type {what}, drop to leave {it} out, or back to leave the whole "
                      "answer.", width, "    ")
        return lines

    def answered(self, typed: str) -> list[str | Left] | None:
        """The parts with the one asked about corrected, or dropped by `drop`; None for `back`.
        A blank line changes nothing, and is asked again."""
        at, _ = self.slips[0]
        parts: list[str | Left] = list(self.parts)
        lines = [line for line in typed.splitlines() if line.strip()]
        word = typed.strip().lower()
        if word == "back":
            return None
        if word == "drop":
            del parts[at]
        elif len(lines) > 1:
            parts[at:at + 1] = _paste_parts(lines)
        elif lines:
            parts[at:at + 1] = _split(lines[0])
        return parts


def _split(text: str) -> list[str]:
    """A line's answers: split at a `;` only before another answer (`_STARTS`), so that a reason
    keeps its own (mqucifer/print-shop#224)."""
    text = text.strip().strip(";")
    parts, start = [], 0
    for semi in re.finditer(";", text):
        if _STARTS.match(text, semi.end()):
            parts.append(text[start:semi.start()])
            start = semi.end()
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _paste_parts(lines: list[str]) -> list[str | Left]:
    """A paste's answers, from its lines that start as one, unindented: the review's own lines
    are indented, its examples among them, so a pasted screen offers none. The other lines are
    one part, after them."""
    parts: list[str | Left] = []
    left: list[str] = []
    for line in lines:
        if line[:1].isspace() or not _STARTS.match(line):
            left.append(line)
        else:
            parts += _split(line)
    return [*parts, Left(tuple(left))] if left else parts


def _no_correction(lines: list[str]) -> str:
    """A paste with no answer in it, said in one sentence (mqucifer/print-shop#224)."""
    said = [w for w in _ALONE if any(line.strip().lower() == w for line in lines)]
    alone = (f", and the {' and the '.join(said)} in it approves and ends nothing"
             if said else "")
    return (f"that paste of {len(lines)} lines is one answer with no correction in it, so "
            f"nothing's taken from it{alone}: type a correction as key=value because \u2026, "
            "or ok on its own")


class Reviewer(Protocol):
    def __call__(self, request: ReviewRequest) -> Review: ...


#: Who each change is from, as the review names it. Every `Source` has one, so a
#: change from an intake answer shows like any other (it raised KeyError, 2026-11-13).
def render(
    request: ReviewRequest, width: int = WIDTH, since: Moved | None = None, first: bool = True,
) -> str:
    """The review as the command prints it, in sections, to `width` (mqucifer/print-shop#223).
    `since` is what moved since the last round, marked and listed at the top and above the
    prompt; `first` is the first round, which shows the correction examples."""
    width = min(width, WIDTH)
    lines: list[str] = []
    if since:
        lines += ["MOVED SINCE YOUR LAST ANSWER",
                  *(x for line in since.lines for x in wrap("  * ", line, width, "    ")), ""]
    for n, section in enumerate(sections(request, width, since)):
        lines += [*([""] if n else []), section.title, *section.lines]
    return "\n".join([*lines, *_end(request, width, since, first)])


def render_arrived(request: ReviewRequest, width: int = WIDTH) -> str:
    """Only what came since the screen above it was drawn: the model's block, or how it failed,
    and the answers (mqucifer/print-shop#242). The rest of the review hasn't changed."""
    found = request.suggested
    assert found is not None
    width = min(width, WIDTH)
    said = [w for w in warnings(request) if w.full.startswith(_OF_MODEL)]
    lines = [x for w in said for x in wrap("  ", w.full, width, "    ")]
    lines += by_model(request, width)
    if not turning(found):  # else they're under the turn
        lines += [x for note in found.design_notes if note not in request.proposal.design_notes
                  for x in wrap("  design: ", note, width, "    ")]
        lines += [x for each in found.refused if each not in request.proposal.refused
                  for x in wrap("  refused from the model: ", each, width, "    ")]
    return "\n".join([*lines, "", *_answers(request, width)])


#: How Look first's lines of the model's work begin, to say again when it arrives.
_OF_MODEL = ("The model", "The model's", "note: the print loop stopped", "THE MODEL PROPOSES")


def _end(request: ReviewRequest, width: int, since: Moved | None, first: bool) -> list[str]:
    """What a terminal leaves on screen above the prompt: Look first again, each line in its
    short form, what moved, and the answers."""
    lines = ["", "-" * width]
    said = warnings(request)
    if said:
        lines += ["LOOK FIRST, AGAIN",
                  *(x for w in said for x in wrap("  ", w.short, width, "    "))]
    if since:
        lines += ["MOVED", *(x for line in since.lines for x in wrap("  * ", line, width, "    "))]
    lines += _answers(request, width)
    if first:
        lines += _examples(request, width)
    return lines


def _answers(request: ReviewRequest, width: int) -> list[str]:
    """What the prompt takes, as it's now listed, on a line."""
    # ok isn't offered before a turn it needs to fit is taken: it waits for that.
    said = ["turn (ok waits for it)" if request.turn_waiting else "ok (approval gate)"]
    if request.numbered:
        said.append("take (the slice's)")
    models = sorted(by_number(request.by_model))
    if models:
        said += [f"take {' '.join(map(str, models))} (the model's)",
                 f"no {models[0]} because \u2026"]
    offering = request.offering
    if offering is not None and offering.items():
        pose = pose_value(request.orientation.rotation_deg)
        ticked = [n for n, item in enumerate(offering.items(), 1)
                  if offering.standing(item, request.proposal, pose).selected]
        said += ["use", f"clear {ticked[0]}"] if ticked else ["use 1"]
    if request.parts:
        said.append("orientation (send a part's back)")
    elif request.candidates:
        said.append("orientation (send it back)")
    said += ["key=value because \u2026", "find <words> (a setting's key)", "stop"]
    # Broken between answers, never inside one, where they don't fit a line.
    lines = ["Answers: " + said[0]]
    for each in said[1:]:
        if len(lines[-1]) + 3 + len(each) + 2 <= width:  # and room for a " ·" after it
            lines[-1] += f" \u00b7 {each}"
        else:
            lines[-1] += " \u00b7"
            lines.append(" " * 9 + each)
    return lines


def _examples(request: ReviewRequest, width: int) -> list[str]:
    """The corrections' examples, on the first round."""
    lands = "it pauses once the layer topping out at 12.4 mm is done"
    pause = (
        f"pause_at=12.4 for insert because the nuts go in ({lands})"
        if _insert_stands_or_is_ticked(request)
        else f"pause_at=12.4 because the nuts go in ({lands}, and it asks what the pause is "
             "for); colour_change=12.4 because white to black (a swap)"
    )
    if request.plate is not None and request.plate.by_object:
        pause = (f"{SEQUENCE}={BY_LAYER} to print them all at once (a pause_at or a "
                 "colour_change needs that)")
    return wrap("  e.g. ", f"wall_loops=3 because it's decorative (several separated by ';'); "
                f"{pause}", width, " " * 7)


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



def parse_answer(answer: str, request: ReviewRequest) -> Review | str:
    """The Sponsor's answer as a `Review`, or why it can't be read: for an answer with a slip in
    it, why its first slip can't be (`read_answer` holds the rest)."""
    read = read_answer(answer, request)
    return read.slips[0][1] if isinstance(read, Slipped) else read


def read_answer(answer: str, request: ReviewRequest) -> Review | Slipped | str:
    """The answer as a `Review`; or, where some of its parts can't be read, those that can held
    and those that can't to ask about (`Slipped`); or why it can't be read at all.

    Pasted text of several lines is one answer (mqucifer/print-shop#224): its corrections are
    read, and its other lines are one part to ask about. One that holds no correction is said in
    a sentence, and an `ok` or `stop` in it neither approves nor ends anything."""
    lines = [line for line in answer.splitlines() if line.strip()]
    if len(lines) > 1:
        parts = _paste_parts(lines)
        if not any(isinstance(p, str) for p in parts):
            return _no_correction(lines)
        return _read_parts(parts, request)
    text = answer.strip()
    if text.lower() in ("ok", "yes", "y", "approve"):
        if request.turn_waiting:
            side = " orientation for another side down," if request.another_side else ""
            return ("it doesn't fit as posed: type turn to take it turned "
                    f"{turn_words(request.slice.turn_deg)},{side} or stop")
        return Review(approved=True)
    if text.lower() in ("no", "n") and by_number(request.by_model):
        # The screen teaches `no 2 because ...`: a bare no doesn't end the job here.
        return ("no turns one of the model's down, saying why: no "
                f"{min(by_number(request.by_model))} because ... To end the job, type stop.")
    if text.lower() == _TURN:
        if not request.slice.turn_deg:
            return ("it fits as posed, so there's no turn to take: orientation sends the pose "
                    "back")
        return Review(turn_taken=True)
    if text.lower() in ("stop", "no", "n"):
        return Review(stop=True)
    looked = _FIND.match(text)
    if looked is not None:
        # Said, and asked again: finding changes nothing (mqucifer/print-shop#222).
        if not looked.group(1).strip():
            return "find looks a setting up by plain words, such as find bridge fan"
        return "\n".join(_finding(looked.group(1), request, WIDTH)).lstrip()
    return _read_parts(list(_split(text)), request)


def _read_parts(parts: list[str | Left], request: ReviewRequest) -> Review | Slipped | str:
    """The answer's parts, each read: a `Review` once all are, else what's held and what isn't
    (mqucifer/print-shop#224). Why the whole can't stand, where its parts are read but don't go
    together, is said for the whole."""
    slips: list[tuple[int, str]] = []
    corrections = []
    taken: list[Change] = []
    used: list[Change] = []
    offering = request.offering
    picked = False
    models = by_number(request.by_model)
    down: list[Change] = []
    for at, part in enumerate(parts):
        if isinstance(part, Left):
            slips.append((at, part.why()))
            continue
        said_no = _NO.match(part)
        if said_no is not None:
            change = _turned_down(int(said_no.group(1)), said_no.group(2) or "", request, models)
            if isinstance(change, str):
                slips.append((at, change))
                continue
            down.append(change)
            continue
        took = _TAKE.match(part)
        if took is not None:
            found = _take(took.group(1), request, models)
            if isinstance(found, str):
                slips.append((at, found))
                continue
            taken += found
            continue
        offered = _OFFER.match(part)
        if offered is not None:
            if offering is None:
                slips.append((at, NOT_FOR_A_PLATE if request.plate is not None
                              else "there's no past print's settings to use"))
                continue
            verb = offered.group(1).lower()
            numbers = [int(n) for n in re.findall(r"\d+", offered.group(2))]
            if verb != "from" and offering.items():
                # Another's number: said whose. Any other is the offer's to refuse.
                theirs = [n for n in numbers if n > request.offered
                          and (n in models or n in dict(request.numbered))]
                if theirs:
                    slips.append((at, _whose_number(theirs[0], request, models, verb)))
                    continue
            if verb == "use":
                if picked:
                    slips.append((at, "look at that print's settings first: from picks it, "
                                      "then use"))
                    continue
                items = offering.use(numbers, request.proposal,
                                     pose_value(request.orientation.rotation_deg))
                if isinstance(items, str):
                    slips.append((at, items))
                    continue
                # Again here, as the stock can change while the review waits.
                gone = next((why for item in items if item.key == PAUSE and (
                    why := pauses.cant_start(item.pause, request.material,
                                             request.stock() if request.stock else ()))), "")
                if gone:
                    slips.append((at, f"{PAUSE} can't be used: {gone}"))
                    continue
                used += [_used_change(item, request) for item in items]
                continue
            if verb == "from" and len(numbers) != 1:
                slips.append((at, "from picks one print, such as from 2"))
                continue
            changed = offering.pick(numbers[0]) if verb == "from" else offering.clear(numbers)
            if isinstance(changed, str):
                slips.append((at, changed))
                continue
            offering, picked = changed, picked or verb == "from"
            continue
        if _FIND.match(part) is not None:
            slips.append((at, "find looks a setting up, and changes nothing: type it on its own"))
            continue
        match = _CORRECTION.match(part)
        if match is None:
            slips.append((at, _not_a_correction(part, request)))
            continue
        key, value, reason = match.group(1), match.group(2), (match.group(3) or "").strip()
        value = names.typed(key, value)  # a switch's on or off, as Orca writes it
        if ";" in value:
            # A `;` is the next answer's only before one (`_split`), and a reason's otherwise.
            slips.append((at, f"'{value}' isn't a value of {key}: a ';' goes before the next "
                              "key=value or take, or in a reason after because"))
            continue
        if request.plate is not None and key == SEQUENCE and value not in SEQUENCES:
            slips.append((at, f"{SEQUENCE} is {' or '.join(SEQUENCES)}"))
            continue
        if key in PAUSE_NAMES:
            change = _pause(key, value, reason, request)
            if isinstance(change, str):
                slips.append((at, change))
                continue
            corrections.append(change)
            continue
        preset = _preset_of(request, key)
        if preset is None:
            slips.append((at, _refused(key, request)))
            continue
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
    # What's used counts as what's typed: neither gets a pause onto a plate printing by object.
    if slips:
        return Slipped(parts=tuple(parts), slips=tuple(slips))
    refused = _pause_by_object([*used, *corrections], request)
    if refused:
        return refused
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
    if request.stock is None or request.record_load is None or request.stands is None:
        return
    try:
        asked = request.stands()  # before what the question names is read
        on_hand, unknown = request.stock(), asked.not_known
    except sqlite3.Error as exc:
        write(f"  The stock couldn't be read ({exc}): an Approve checks it again.")
        return
    change = request.proposal.get(PAUSE)
    pause = change.pause if change is not None and change.value != "none" else None
    if pause is not None and pause.start is not None:
        first = pauses.first_to_load(pause, on_hand, unknown.why() if unknown else "")
    else:
        first = request.material if unknown is not None else None
    if first is None:
        return
    spool = next((spool for spool in on_hand if spool.id == first.spool_id), None)
    if spool is None:
        return
    first = first.of(spool)  # named as it is now, not as it was when the job was prepared
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
        request.record_load(first.spool_id, asked)
    except (InventoryError, sqlite3.Error) as exc:
        write(f"  Not recorded: {exc}")
        return
    write(f"  Recorded: {first.named()} is loaded.")
    if unknown is not None:
        # Saying one's over says nothing of another.
        _loaded_first(request, read, write, forget, asks)


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
        f"them all once the layer topping out at {pause.value} mm is done, {PAUSE}=none for no "
        "pause, or print that part by itself"
    )


def _take(
    numbers: str, request: ReviewRequest, models: dict[int, Change]
) -> list[Change] | str:
    """The changes `take` names, by the numbers on screen: the slice's, all of them by a bare
    `take`, and the model's, by number alone (mqucifer/print-shop#242). Use these' numbers come
    first and are `use`'s (mqucifer/print-shop#223)."""
    slices = dict(request.numbered)
    picked = list(dict.fromkeys(int(n) for n in re.findall(r"\d+", numbers)))
    if not picked:
        if not slices:
            return ("the slice proposes nothing to take"
                    + (f": the model's are taken by number, take {min(models)}" if models else ""))
        picked = list(slices)
    for n in picked:
        if n not in slices and n not in models:
            return _whose_number(n, request, models, "take")
    taken: list[Change] = []
    for n in sorted(picked):  # the slice's first, so that the model's stands where both name one
        if n in slices:
            change = slices[n].change
            assert change is not None
            taken.append(change)
        else:
            taken.append(models[n].model_copy(update={"taken": True}))
    # Named for one setting as well, the model's replaces the slice's: it was asked for by number.
    return list({c.key: c for c in taken}.values())


def _whose_number(n: int, request: ReviewRequest, models: dict[int, Change], verb: str) -> str:
    """Why `verb n` can't take `n`: whose number it is, and what takes it, or the numbers there
    are (mqucifer/print-shop#223)."""
    if 1 <= n <= request.offered:
        return f"{verb} {n} is one of Use these: use {n}"
    if n in dict(request.numbered):
        return f"{n} is the slice's: take {n}"
    if n in models:
        return f"{n} is the model's: take {n}"
    runs = _ranges(request, models)
    if not runs:
        return "the slice proposes nothing to take"
    if len(runs) == 1:
        _, lo, hi = runs[0]
        return f"take a number from {lo} to {hi}"
    there = [f"{whose} {lo}-{hi}" if hi > lo else f"{whose} {lo}" for whose, lo, hi in runs]
    return f"there's no {n}: {', '.join(there)}"


def _ranges(request: ReviewRequest, models: dict[int, Change]) -> list[tuple[str, int, int]]:
    """The numbers on screen, as runs, and whose: Use these', the slice's, the model's."""
    runs = []
    if request.offered:
        runs.append(("Use these", 1, request.offered))
    slices = [n for n, _ in request.numbered]
    if slices:
        runs.append(("the slice's", slices[0], slices[-1]))
    if models:
        runs.append(("the model's", min(models), max(models)))
    return runs


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
    asks: Callable[[], bool] | None = None,
    screen: Callable[[], TextIO | None] = terminal,
    width: Callable[[], int] = lambda: shutil.get_terminal_size((WIDTH, 24)).columns,
    more: Callable[[], bool] | None = None,
) -> Reviewer:
    """Show the review on the command line and read the answer, again until it's readable.
    `orientation` starts the send-back conversation (`send_back`). `forget` drops what was typed
    before something was shown that a number or a y answers: the model's list, or the load's
    question. `asks` is whether there's a terminal to ask that in (`can_ask`). `more` is whether
    another line is already waiting, to read a paste as one answer (mqucifer/print-shop#224).

    The model's proposals are said as they arrive, under the open prompt, and added to the list
    by Enter (mqucifer/print-shop#242). What's typed is read against the list on screen. While
    the model works, a timer ticks on the line above the prompt, on the screen alone.

    The review is laid out to `width`, the terminal's, up to `WIDTH` (mqucifer/print-shop#223).
    From the second round of a job on, what moved since the round before is marked and listed.
    """
    shown: list[ReviewRequest] = []  # the round before, of this job
    waiting = _pasted if more is None else more

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
                out = screen()
                if out is not None and timed.is_set():
                    # The timer's line, above what's being typed, cleared: it's said below.
                    out.write("\0337\033[1A\r\033[2K\0338")
                    out.flush()
                    timed.clear()
                write(f"\n{said_in(found)} (what you've typed is kept).")
                out = screen()
                if out is not None:
                    # On the screen alone: the log gets the prompt with its answer.
                    out.write("> ")
                    out.flush()

        timed = threading.Event()  # the timer's line is drawn above the open prompt
        done = threading.Event()

        def working() -> bool:
            return work is not None and work.now().ending is Ending.WORKING

        def timer() -> str:
            assert work is not None
            return f"  waiting for the model's settings: {waited(work.now().seconds)}"

        def tick() -> None:
            """The timer's line, rewritten each second above what's being typed, with the cursor
            put back: as the questions' wait does (`waiting.cli_waiting`)."""
            while not done.wait(1.0):
                with lock:
                    out = screen()
                    if out is None or not timed.is_set() or not asking.is_set():
                        continue
                    if not working():
                        continue  # its end is said by `heard`
                    out.write(f"\0337\033[1A\r\033[2K{timer()}\0338")
                    out.flush()

        def show() -> None:
            before = shown[-1] if shown and _same_job(shown[-1], request) else None
            since = what_moved(before, request) if before is not None else None
            write(render(request, width(), since, first=before is None))
            shown[:] = [request]
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
                out = screen()
                timed.clear()
                if out is not None and working():
                    # On the screen alone, and ticking (`tick`): the log has the review's line.
                    out.write(f"{timer()}\n")
                    out.flush()
                    timed.set()
                asking.set()
            try:
                return read("> ")
            finally:
                with lock:
                    asking.clear()

        def left(answer: Review) -> Review:
            """The answer as this review leaves it: with what was picked or taken here, and
            what of the model's it had on screen."""
            answer = _as_left(answer, moved, took)
            return replace(answer, saw=request.suggested) if added else answer

        if work is not None:
            work.hear(heard)
            if screen() is not None:
                threading.Thread(target=tick, daemon=True, name="settings-timer").start()
        try:
            show()
            moved: Offering | None = None  # the offer as picked and cleared here
            took = False  # the turn to fit, taken here
            told = False  # that what's ticked isn't used yet
            told_model: set[str] = set()  # what's been said of the model's, at an ok
            added = False
            while True:
                # Typed ahead, before the prompt opened, each line is an answer as ever: lines
                # waiting once one is read at the open prompt were pasted with it, and are one
                # answer with it (mqucifer/print-shop#224).
                ahead = waiting()
                answer = ask()
                while not ahead and waiting():
                    answer += "\n" + read("")
                if request.suggested is not None and not answer.strip():
                    newer = request.refreshed()
                    assert newer.suggested is not None
                    if newer.suggested.ending is not request.suggested.ending:
                        request, added = replace(newer, turn_taken=request.turn_taken), True
                        write(render_arrived(request, width()))
                        forget()  # its numbers are only now the ones on screen
                    elif newer.suggested.ending is Ending.WORKING:
                        write("  The model is still working on settings "
                              f"({waited(newer.suggested.seconds)}).")
                    else:
                        write("  Nothing new to add: ok, take, a correction, orientation, "
                              "or stop.")
                    continue
                looked = _FIND.match(answer)
                if looked is not None:
                    if not looked.group(1).strip():
                        write("  find looks a setting up by plain words, such as find bridge fan.")
                    else:
                        write("\n".join(_finding(looked.group(1), request, width())))
                    continue
                back = _SEND_BACK.match(answer)
                if back is not None and (request.candidates or request.parts):
                    send = _send_back_part if request.parts else send_back
                    sent = send(request, read, write, back.group(1), back.group(2))
                    if sent is not None:
                        return left(sent)
                    write("  Back to the review: ok, a correction, orientation, or stop.")
                    continue
                try:
                    parsed = _settled(read_answer(answer, request), request, read, write,
                                      width(), waiting)
                    if parsed is None:
                        write("  Back to the review: nothing from that answer is taken.")
                        continue
                    if isinstance(parsed, str):
                        # One line is wrapped to the width, as at fix>; several are laid out.
                        write(f"  {parsed}" if "\n" in parsed
                              else "\n".join(wrap("  ", parsed, min(width(), WIDTH), "    ")))
                        continue
                    purposed = _purposed(parsed, request, read, write)
                except sqlite3.Error as exc:
                    write(f"  The stock couldn't be read ({exc}): type it again.")
                    continue
                if purposed is None:
                    write("  Back to the review: ok, a correction, or stop.")
                    continue
                parsed = purposed
                if parsed.turn_taken:
                    took = True
                    request = replace(request, turn_taken=True)
                    write(f"  Taken: turned {turn_words(request.slice.turn_deg)}. "
                          "ok goes on to the approval gate.")
                    continue
                if parsed.browsing:
                    # Only the offer moved, so only the offer is shown again.
                    moved = parsed.offering
                    assert moved is not None
                    request = replace(request, offering=moved)
                    pose = pose_value(request.orientation.rotation_deg)
                    write("\n".join(render_offer(moved, request.proposal, pose,
                                                  width=min(width(), WIDTH) - 2, lead="  ")))
                    told = False
                    continue
                if parsed.approved:
                    waits: list[str] = []
                    if not told and request.offering is not None:
                        pose = pose_value(request.orientation.rotation_deg)
                        ticked = len(request.offering.ticked(request.proposal, pose))
                        if ticked:
                            told = True
                            aren, them = ("isn't", "it") if ticked == 1 else ("aren't", "them")
                            waits.append(f"  {ticked} ticked {aren} used yet: use to take {them}, "
                                         "or ok again to go on without")
                    state, said = _going_on(request)
                    if state and state not in told_model:
                        told_model.add(state)
                        waits.append(said)
                    if waits:
                        write("\n".join(waits))
                        continue
                    if without := _without(request):
                        write(f"  {without}.")
                    _loaded_first(request, read, write, forget, asks or can_ask)
                return left(parsed)
        finally:
            done.set()
            if work is not None:
                work.hear(None)

    return review


def _pasted() -> bool:
    """Whether a line is already waiting at a person's terminal. Piped lines are each an answer,
    and what can't be looked at is none."""
    try:
        return can_ask() and _keys_waiting(0.05)
    except (OSError, ValueError):
        return False


def _settled(
    read_: Review | Slipped | str, request: ReviewRequest, read: Callable[[str], str],
    write: Callable[[str], None], width: int, waiting: Callable[[], bool],
) -> Review | str | None:
    """The answer once each slip in it is corrected or dropped, the rest held meanwhile, so that
    it's sliced once (mqucifer/print-shop#224). None: `back`, or nothing left of it."""
    while isinstance(read_, Slipped):
        if not read_.held:
            return read_.slips[0][1]  # nothing to hold: said, and typed again at the review
        write("\n".join(read_.asked(min(width, WIDTH))))
        typed = read("fix> ")
        while waiting():  # a paste here is one fix, and leaves nothing for the next prompt
            typed += "\n" + read("")
        looked = _FIND.match(typed)
        if looked is not None and looked.group(1).strip():
            # Looked up, and asked again: finding changes nothing (mqucifer/print-shop#222).
            write("\n".join(_finding(looked.group(1), request, width)))
            continue
        parts = read_.answered(typed)
        if not parts:
            return None
        read_ = _read_parts(parts, request)
    return read_


def _same_job(before: ReviewRequest, now: ReviewRequest) -> bool:
    """Whether two reviews are rounds of one job: what moved is said between those alone."""
    return (before.presets == now.presets
            and before.slice.gcode_path.parent == now.slice.gcode_path.parent)


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


def _as_left(answer: Review, moved: Offering | None, took: bool) -> Review:
    """The answer, with what was picked or taken before it at the same review."""
    if moved is not None and answer.offering is None:
        answer = replace(answer, offering=moved)
    return replace(answer, turn_taken=True) if took else answer


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
    others = request.other_sides
    left_out = [c for c in request.candidates
                if c.fits_no_way and c.rotation_deg != now.rotation_deg]
    if left_out:
        pose = None  # a number typed ahead may have counted one, so the list is shown
    write(f"The orientation step printed it at {pose_value(now.rotation_deg)}: {now.rationale}")
    chosen: Orientation | None = None
    if pose is not None and pose.isdigit() and 1 <= int(pose) <= len(others):
        chosen = others[int(pose) - 1]
    if chosen is None:
        if not others:
            write("  No other side down fits the plate, however it's turned on it." if left_out
                  else "  There's no other pose to send it to.")
            return None
        write("Which pose should it print in?")
        write("\n".join(f"  {n}) {_pose(c)}" for n, c in enumerate(others, 1)))
        if left_out:
            write(f"  Left out, as they don't fit however they're turned on the plate: "
                  f"{'; '.join(pose_value(c.rotation_deg) for c in left_out)}")
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


def _today(request: ReviewRequest) -> dict[str, str]:
    """What this review changes, by key: a setting's value today, before the slice's."""
    return {c.key: c.value for c in request.proposal.changes}


def _refused(key: str, request: ReviewRequest) -> str:
    """Why `key` can't be corrected on this print, saying which case it is
    (mqucifer/print-shop#222, `names.refused`)."""
    return names.refused(key, request.process, request.filament, _sliced_with(request),
                         preset=_filament_name(request), changed=_today(request))


def _not_a_correction(part: str, request: ReviewRequest) -> str:
    """Why `part` isn't read as a correction: where a setting is named in words before an `=`,
    the one they find, to type in its place."""
    said = f"'{part}' isn't setting=value"
    words = _WORDS_SET.match(part)
    if words is None or " " not in words.group(1).strip():
        return said
    best = names.find(words.group(1), request.process, request.filament, _sliced_with(request),
                      _today(request))
    if not best:
        return f"{said}, and nothing this print takes fits '{words.group(1).strip()}'"
    key = best[0].key
    return (f"{said}: a setting's key goes before the =. '{words.group(1).strip()}' finds "
            f"{names.named(key)}: type {key}={words.group(2)}, or find {words.group(1).strip()} "
            "for others")


def _finding(words: str, request: ReviewRequest, width: int) -> list[str]:
    """What `find` says: the settings that fit the words, best first. It changes nothing."""
    settings = names.find(words, request.process, request.filament, _sliced_with(request),
                          _today(request))
    return found(words, settings, _filament_name(request), min(width, WIDTH))


def _filament_name(request: ReviewRequest) -> str:
    """The filament preset's name as it's said: `Bambu PETG HF @System` is Bambu PETG HF."""
    return request.presets[2].split(" @")[0].strip()


def _sliced_with(request: ReviewRequest) -> dict[str, str] | None:
    """The slice's config block, read once a slice: what Orca sliced with, and so which keys
    are Orca's settings. None where it can't be read."""
    path = request.slice.gcode_path
    try:
        stat = path.stat()
    except OSError:
        return None
    return _config_block(path, stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=4)
def _config_block(path: Path, mtime_ns: int, size: int) -> dict[str, str] | None:
    del mtime_ns, size  # the cache's key: a file sliced again over the same path is read again
    try:
        return parse_config_block(path)
    except (OSError, ValueError, RuntimeError):
        return None


def _setting_change(request: ReviewRequest, key: str, value: str, reason: str) -> Change:
    preset = _preset_of(request, key) or ""
    return Change(key=key, preset=preset, value=value, reason=reason or "a way by settings",
                  source=Source.SPONSOR)
