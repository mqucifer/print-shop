"""The review laid out to be read (mqucifer/print-shop#223).

The Sponsor, 2026-11-21: "I think most of the details in teh cli are good, they're just presented
poorly as giant walls of text". So the review says what it said, in sections with headings, in
this order: what to look at first, the print, the settings by who set them, what the slice
shows, the part, the pose and the lessons, then the answers the prompt takes.

- **Nothing is left out, and nothing is said twice.** A fit and a lesson are said once, where
  they belong, and named by number elsewhere ("fit 1", "lesson 17").
- **Aligned and wrapped** at `WIDTH` columns, or the terminal's where it's narrower. A path or
  a setting's name is never broken: one longer than the line runs past it, on its own. Times in
  the tables and the pause are m:ss. It reads without colour, so the job's log holds the same.
- **One number space:** Use these' items first, then the slice's proposals, then the model's,
  so that a number on screen is one thing (`ReviewRequest.offered`).
- **The end of a long review is what a terminal leaves on screen,** so just above the prompt it
  says each Look first line again, in a short form written whole.
- **After a correction, what moved is marked** with `*` in its section, and listed at the top
  and just above the prompt (`what_moved`).

The sections are made once (`sections`) and the terminal joins them (`review.render`), so the
phone can lay out the same (mqucifer/print-shop#221).
"""

from __future__ import annotations

import re
import sqlite3
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from print_shop.bench.pausing import TOPS_OUT, mm
from print_shop.core.state import turn_words
from print_shop.setup import pauses
from print_shop.setup.context import SET_ASIDE
from print_shop.setup.offer import render as render_offer
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    Change,
    Ending,
    GivenLesson,
    Source,
    pose_value,
)
from print_shop.setup.reading import (
    NOW_PRINTS,
    PAUSE_PAST_FIT,
    PRINTS,
    FeatureReading,
    PauseReading,
    SliceReading,
)
from print_shop.setup.suggest import didnt_come, fell_back, turning, waited

if TYPE_CHECKING:
    from print_shop.setup.review import ReviewRequest

__all__ = ["WIDTH", "Moved", "Said", "Section", "by_model", "clock", "sections", "what_moved",
           "warnings", "wrap"]

#: The widest a review's line is.
WIDTH = 100

#: A row that moved since the last round.
MOVED = "*"
#: A row that matters for this part.
MATTERS = "!"

#: Who each change is from, as the review names them.
_BY = {Source.ANSWER: "your answer", Source.MODEL: "model", Source.GUARD: "guard",
       Source.SPONSOR: "you", Source.READING: "the slice", Source.LESSON: "lesson"}

#: The settings' groups, by who set them, in the order they're shown.
_GROUPS: tuple[tuple[str, tuple[Source, ...]], ...] = (
    ("YOURS", (Source.SPONSOR, Source.ANSWER)),
    ("FROM THE SLICE", (Source.READING,)),
    ("LESSONS AND GUARDS", (Source.LESSON, Source.GUARD)),
    ("THE MODEL", (Source.MODEL,)),
)

#: What a plate's review says where one part's has "use these" (mqucifer/print-shop#109).
NOT_FOR_A_PLATE = (
    "USE THESE: not offered for a plate of several parts yet, as one part's settings would go on "
    "every part. Type what you want, or to print the same plate again, copy its line from the "
    "print desk."
)

#: How far a value's column runs before the value is said in full beneath its row.
_CELL = 14


@dataclass(frozen=True)
class Section:
    """One part of the review: its heading and its lines, laid out to a width."""

    key: str
    title: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Said:
    """One thing Look first says: in full, and in a short form written whole, for the lines
    above the prompt. `waiting` is what waits to be taken, which isn't a warning."""

    full: str
    short: str
    #: What waits: to be taken, or on the model. Said again each round, and never a move.
    waiting: bool = False


@dataclass(frozen=True)
class Moved:
    """What a correction moved, against the round before: each said as a line."""

    settings: tuple[str, ...] = ()
    estimate: tuple[str, ...] = ()
    slice: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    #: What moved, to mark where it's shown: setting keys, "time", "plastic" and "layers",
    #: features, and Look first lines (in full).
    keys: frozenset[str] = frozenset()
    figures: frozenset[str] = frozenset()
    features: frozenset[str] = frozenset()
    new_warnings: frozenset[str] = frozenset()

    @property
    def lines(self) -> tuple[str, ...]:
        return (*self.settings, *self.estimate, *self.slice, *self.warnings)

    def __bool__(self) -> bool:
        return bool(self.lines)


# --------------------------------------------------------------------------
# Figures and lines
# --------------------------------------------------------------------------


def clock(seconds: float) -> str:
    """A time as m:ss, h:mm:ss past an hour, and 0:09.6 under a minute, where a tenth tells."""
    if seconds < 60:
        whole = seconds == round(seconds)
        return f"0:{round(seconds):02d}" if whole else f"0:{seconds:04.1f}"
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _signed(seconds: float) -> str:
    return ("+" if seconds >= 0 else "-") + clock(abs(seconds))


#: A time as `reading._s` and `waited` say it.
_SAID_TIME = re.compile(r"\b(\d+) min (\d+(?:\.\d+)?) s\b|\b(\d+(?:\.\d+)?) s\b")


def _clocked(text: str) -> str:
    """`text` with each "4 min 12 s" or "9.6 s" said as m:ss. Only for text made of a reading's
    figures, such as a pause's line: a sentence's own seconds are left as they are."""

    def one(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            return clock(int(m.group(1)) * 60 + float(m.group(2)))
        return clock(float(m.group(3)))

    return _SAID_TIME.sub(one, text)


def wrap(lead: str, text: str, width: int, rest: str | None = None) -> list[str]:
    """`text` after `lead`, wrapped under its own start (or `rest`) to `width`. A word is never
    broken: a path or a setting's name longer than the line runs past it, on its own."""
    hang = " " * len(lead) if rest is None else rest
    return textwrap.wrap(text, width, initial_indent=lead, subsequent_indent=hang,
                         break_long_words=False, break_on_hyphens=False) or [lead.rstrip()]


def _table(rows: Sequence[Sequence[str]], right: Iterable[int] = (), lead: str = "") -> list[str]:
    """Rows in columns, each as wide as its widest cell, those named in `right` aligned right."""
    right = set(right)
    count = max((len(r) for r in rows), default=0)
    widths = [max((len(r[i]) for r in rows if i < len(r)), default=0) for i in range(count)]
    return [(lead + "  ".join(cell.rjust(widths[i]) if i in right else cell.ljust(widths[i])
                              for i, cell in enumerate(row))).rstrip() for row in rows]


def _short(text: str, width: int) -> str:
    """A sentence's first clause, where it's longer than a line: written whole, never cut."""
    if len(text) <= width:
        return text
    first = re.split(r"(?<=[^\d])\. |; ", text, maxsplit=1)[0].rstrip(".")
    return first


# --------------------------------------------------------------------------
# The sections
# --------------------------------------------------------------------------


def sections(
    request: ReviewRequest, width: int = WIDTH, since: Moved | None = None,
) -> list[Section]:
    """The review's sections, in order, laid out to `width`. `since` is what moved since the
    last round, to mark."""
    width = min(width, WIDTH)
    marks = since or Moved()
    fits = _Fits(request.context)
    found = [
        Section("look", "LOOK FIRST", tuple(_look(request, width, marks, fits))),
        Section("print", "PRINT", tuple(_print(request, width, marks))),
        Section("settings", "SETTINGS, over your presets",
                tuple(_settings(request, width, marks, fits))),
        Section("slice", "WHAT THE SLICE SHOWS", tuple(_slice(request, width, marks, fits))),
        Section("part", "THE PART", tuple(_part(request, width, fits))),
        Section("pose", "POSE", tuple(_pose(request, width))),
        Section("lessons", "LESSONS FROM EARLIER PRINTS, kept by a reviewer, for a part like this",
                tuple(_lessons(request, width))),
    ]
    return [s for s in found if s.lines]


def warnings(request: ReviewRequest) -> list[Said]:
    """What Look first says, in order."""
    return _warnings(request, _Fits(request.context))


def _look(request: ReviewRequest, width: int, marks: Moved, fits: _Fits) -> list[str]:
    lines: list[str] = []
    for said in _warnings(request, fits):
        mark = MOVED if said.full in marks.new_warnings else " "
        lines += wrap(f"{mark} ", said.full, width, "    ")
    return lines


def _warnings(request: ReviewRequest, fits: _Fits) -> list[Said]:
    """Warnings, anything not applied, how the model's work went, each pause with when it
    comes, the turn it needs to fit, and what waits to be taken."""
    proposal = request.proposal
    out: list[Said] = []
    if request.undone:
        out.append(Said(f"NOT DONE, your last answer: {request.undone}. The plate is as it was "
                        "before it.", f"NOT DONE, your last answer: {_short(request.undone, 70)}"))
    out += _pause_said(request, fits)
    for c in proposal.not_applied:
        out.append(Said(f"NOT APPLIED {c.key}: {c.not_applied}. {c.reason} [{_by(c)}]",
                        f"NOT APPLIED {c.key}: {c.not_applied}"))
    out += _turn_said(request)
    out += _model_said(request)
    for note in proposal.notes:
        if _seen(note):
            continue  # what's seen is the part's: under The part
        out.append(Said(f"note: {note}", _upper_head(_short(note, WIDTH - 2))))
    out += _waiting(request)
    return out


def _seen(note: str) -> bool:
    """Whether a note says what's seen as it prints: one part's, or on a plate a part's or every
    part's (mqucifer/print-shop#109). That's the part's, under The part."""
    return _SEEN.match(note) is not None


#: A note on what's seen, as one part has it or a plate names the part it's of.
_SEEN = re.compile(rf"^(?:every part: |part \d+, [^:]+: )?(?:{re.escape(PRINTS)}|"
                   rf"{re.escape(NOW_PRINTS)})")


def _upper_head(text: str) -> str:
    """A note's head, up to its first colon, in capitals, as a warning reads above the prompt."""
    head, colon, rest = text.partition(":")
    return f"{head.upper()}{colon}{rest}" if colon and len(head) <= 30 else text


def _pause_said(request: ReviewRequest, fits: _Fits) -> list[Said]:
    """Each pause with when it comes and what it's for, and its check against the G-code."""
    from print_shop.setup.review import _pauses  # the check against what was asked

    checked = [line.strip() for line in _pauses(request)]
    reading = request.reading
    placed = list(reading.pauses) if reading is not None else []
    out: list[Said] = []
    if len(checked) == 1 and len(placed) == 1 and ", read from the G-code: " in checked[0]:
        # Where it landed as asked, it's said once, with when it comes.
        p = placed[0]
        asked = checked[0].partition(", read from the G-code: ")[0]
        at = f"{mm(p.after_z)} mm" if p.after_z is not None else f"z={mm(p.z)}"
        out.append(Said(f"PAUSE {asked}: {_clocked(p.line())}, as asked (read from the G-code)",
                        f"PAUSE at {at}{_for(p)}, about {clock(p.after_s)} in"))
    else:
        for line in checked:
            # Its short form: what was asked, and what the G-code has, without the advice.
            head, _, rest = line.partition(": ")
            has = rest.partition("the G-code has: ")[2].partition(": type ")[0]
            short = f"{head}: the G-code has {has}" if has else head
            out.append(Said(line, short))
        for p in placed:
            out.append(Said(_clocked(p.line()), f"PAUSE {p.where()}{_for(p)}, about "
                                                f"{clock(p.after_s)} in"))
    out += [Said(fits.named(f"note: {f.reading}"), "note: the pause comes after the layer that "
                 "closes over a fit") for f in request.flags if f.id == PAUSE_PAST_FIT]
    proposal = request.proposal
    paused = proposal.get(PAUSE)
    purpose = paused.pause if paused is not None and paused.value != "none" else None
    try:
        unknown = request.stands().not_known if request.stands else None
        why = unknown.why() if unknown is not None else ""
        if purpose is not None:
            for line in pauses.for_review(purpose, request.reading, request.material,
                                          request.stock, why):
                line = line.strip()
                if line == "the pause is for an insert" and out and "for an insert" in out[0].full:
                    continue  # said with the pause
                out.append(Said(line, _short(line, WIDTH - 2)))
        if why and (purpose is None or purpose.start is None):  # a swap's LOAD FIRST says it
            line = f"{why}: at ok you're asked, and Start holds until you say"
            out.append(Said(line, line))
    except sqlite3.Error as exc:
        line = f"the stock couldn't be read ({exc}): an Approve checks it again"
        out.append(Said(line, "the stock couldn't be read: an Approve checks it again"))
    return out


def _for(p: PauseReading) -> str:
    """What a pause is for, as its short form says it, or "" where the G-code doesn't say."""
    return f" for {pauses.said(p.kind)}" if p.kind is not None else ""


def _turn_said(request: ReviewRequest) -> list[Said]:
    """The turn it needs to fit the plate, offered or taken (mqucifer/print-shop#257)."""
    deg = request.slice.turn_deg
    if not deg:
        return []
    turned = f"turned {turn_words(deg)}"
    theirs = 'x, y and the seam\'s "back" are the turned part\'s'
    if request.turn_taken:
        full = f"TURNED ON THE PLATE to fit, as you took it: {turned}; {theirs}"
        return [Said(full, f"TURNED ON THE PLATE to fit: {turned}")]
    posed = next((c for c in request.candidates
                  if c.rotation_deg == request.orientation.rotation_deg), None)
    size = f" ({posed.bbox_mm[0]:.1f} x {posed.bbox_mm[1]:.1f} mm)" if posed else ""
    other = " For another side down, type orientation." if request.another_side else ""
    full = (f"DOESN'T FIT AS POSED{size}: Orca has no room for it that way. It fits {turned}, "
            f"with the same side down. What's shown here is already sliced that way: the time, "
            f"{theirs}. Nothing prints that way unless you type turn.{other}")
    return [Said(full, f"DOESN'T FIT AS POSED: it fits {turned}; type turn")]


def _model_said(request: ReviewRequest) -> list[Said]:
    """How the model's work went."""
    proposal = request.proposal
    found = request.suggested
    if found is not None:
        if found.ending is Ending.WORKING:
            return [Said(f"The model is still working on settings ({waited(found.seconds)}). "
                         "What it proposes is added under Settings when you press Enter, and "
                         "nothing waits for it.",
                         f"The model is still working on settings ({waited(found.seconds)})",
                         # Its work under way is no warning: what it comes to may be.
                         waiting=True)]
        out = [Said(f"note: {stopped}", "note: the print loop stopped, so the single calls "
                    "proposed") for stopped in (fell_back(found),) if stopped]
        if found.ending is not Ending.CAME:
            line = didnt_come(found)
            out.append(Said(line, line.partition(". ")[0]))
        elif not found.proposed:
            line = f"The model proposed no change, after {waited(found.seconds)}."
            out.append(Said(line, line))
        return out
    if proposal.model.startswith("cut off"):
        return [Said(f"CUT OFF: the model's answer was {proposal.model}. Nothing it proposed is "
                     "used; only the guards' changes stand. This is recorded, to tune the "
                     "limit.", "CUT OFF: the model's answer was cut off; only the guards' "
                     "changes stand")]
    if proposal.model.startswith(("unavailable", "timed out")):
        line = f"the model was {proposal.model}; only the guards' changes stand"
        return [Said(line, f"the model was {proposal.model.split(':')[0]}; only the guards' "
                     "changes stand")]
    if not proposal.model:
        line = "no model was asked; only the guards' changes stand"
        return [Said(line, line)]
    return []


def _waiting(request: ReviewRequest) -> list[Said]:
    """What waits to be taken: Use these' ticked, the model's and the slice's, by number."""
    out: list[Said] = []
    offering = request.offering
    if offering is not None and offering.past is not None:
        pose = pose_value(request.orientation.rotation_deg)
        ticked = len(offering.ticked(request.proposal, pose))
        past = offering.past
        if ticked:
            out.append(Said(f"USE THESE: {ticked} ticked from {past.stl_name}, {past.on}, under "
                            "Settings. None is applied until you use it.",
                            f"USE THESE: {ticked} ticked from {past.stl_name} (use)", True))
    slices = [n for n, _ in request.numbered]
    if slices:
        them = "1 change" if len(slices) == 1 else f"{len(slices)} changes"
        out.append(Said(f"THE SLICE PROPOSES {them} ({_listed(slices)}), under What the slice "
                        "shows. None is applied until you take it.",
                        f"THE SLICE PROPOSES {len(slices)}: take {_listed(slices)}", True))
    models = [row.number for row in request.by_model if row.standing == "open"]
    if models:
        them = "1 change" if len(models) == 1 else f"{len(models)} changes"
        numbers = " ".join(map(str, models))
        out.append(Said(f"THE MODEL PROPOSES {them} ({numbers}), under Settings. None is applied "
                        "until you take it.", f"THE MODEL PROPOSES {len(models)}: take {numbers}",
                        True))
    return out


def _print(request: ReviewRequest, width: int, marks: Moved) -> list[str]:
    s = request.slice
    machine, process, filament = request.presets

    def mark(name: str) -> str:
        return MOVED if name in marks.figures else " "

    plastic = f"{s.filament_cm3} cm3" + (f", {s.filament_g} g" if s.filament_g else "")
    rows = [
        (f"{mark('time')} time", f"{clock(s.print_time_s)}, Orca's estimate"),
        (f"{mark('plastic')} plastic", plastic),
        (f"{mark('layers')} layers",
         str(s.layer_count) + (" for the whole plate" if request.plate else "")),
        ("  machine", machine),
        ("  process", process),
        ("  filament", filament),
    ]
    lines = [line for name, value in rows for line in wrap(f"{name:<12}", value, width, " " * 12)]
    if request.plate is not None:
        # How the plate prints and where each part is (mqucifer/print-shop#109). Its map is drawn,
        # so only its words are wrapped.
        plate = request.plate.lines()
        lines += ["", *wrap("  ", plate[0], width, "    ")]
        for line in plate[1:]:
            drawn = line.startswith("    ")
            lines += [f"  {line}"] if drawn else wrap(f"  {line[:2]}", line[2:], width, "      ")
    return lines


def _by(change: Change) -> str:
    """Who a change is from, as the review names them."""
    if change.taken and change.used is None:
        return f"{_BY[change.source]}, taken by you"
    if change.used is not None:
        return f"{_BY[change.source]}, {change.used.line()}"
    return _BY[change.source] + (f" {change.lesson}" if change.lesson is not None else "")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def _settings(request: ReviewRequest, width: int, marks: Moved, fits: _Fits) -> list[str]:
    """The changes grouped by who set them, in columns, each reason beneath; those kept at the
    preset; the past print's to use; and the model's proposals, numbered on."""
    proposal = request.proposal
    changes = proposal.changes
    lessons = {g.id: g for g in proposal.lessons}
    tuned = any(c.standard for c in (*changes, *proposal.kept))
    yours = "your tuning" if tuned else "your preset"
    head = ("setting", *(("standard",) if tuned else ()), yours, "this print", "who")
    kept = list(proposal.kept)
    groups = [(title, [c for c in changes if c.source in sources]) for title, sources in _GROUPS]
    rows = [head, *(_row(c, tuned, c.value) for c in changes),
            *(_row(c, tuned, c.preset) for c in kept)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(head))]
    widths = [w if i in (0, len(head) - 1) else min(w, _CELL) for i, w in enumerate(widths)]
    rest = " " * 6

    def laid(cells: tuple[str, ...], mark: str) -> list[str]:
        """A row in the columns. A value longer than its column is said in full beneath."""
        last = len(cells) - 1
        over = [i for i in range(1, last) if len(cells[i]) > widths[i]]
        shown = [("(below)" if i in over else cell).ljust(widths[i]) if i < last else cell
                 for i, cell in enumerate(cells)]
        out = wrap(f"{mark} ", "  ".join(shown).rstrip(), width, rest)
        if len(out) == 1 or len(shown[0]) + 2 > width:
            out = [f"{mark} " + "  ".join(shown).rstrip()]
        out += [x for i in over for x in wrap(f"{rest}{head[i]}: ", cells[i], width, rest)]
        return out

    lines: list[str] = []
    if not changes:
        lines.append("  No change reached the slice." if proposal.not_applied
                     else "  No changes: your presets as they are.")
    if changes or kept:
        lines += laid(head, " ")
    found = request.suggested
    here = found if found is not None and not turning(found) else None
    refused = list(dict.fromkeys((*proposal.refused, *(here.refused if here else ()))))
    for title, group in groups:
        model = title == "THE MODEL"
        if not group and not (model and refused):
            continue
        lines += ["", f"  {title}"]
        for c in group:
            lines += laid(_row(c, tuned, c.value), MOVED if c.key in marks.keys else " ")
            lines += _beneath(c, lessons, width, fits)
        if model:
            # What the model asked for that wasn't let through, under the model's own.
            lines += [x for each in refused
                      for x in wrap("    refused: ", fits.named(each), width, rest)]
    if kept:
        lines += ["", "  KEPT AT YOUR PRESET"]
        for c in kept:
            who = (f"lesson {c.lesson}" if c.lesson is not None
                   else f"{_BY[c.source]}: {c.feature}" if c.source is Source.ANSWER
                   else _BY[c.source])
            cells = _row(c, tuned, c.preset)
            lines += laid((*cells[:-1], who), MOVED if c.key in marks.keys else " ")
            lines += wrap(rest, fits.named(c.reason), width)
    if request.offering is not None:
        pose = pose_value(request.orientation.rotation_deg)
        lines += render_offer(request.offering, proposal, pose, width=width - 2, lead="  ")
    elif request.plate is not None:
        lines += ["", *wrap("  ", NOT_FOR_A_PLATE, width, "    ")]
    lines += by_model(request, width)
    return lines


def _row(c: Change, tuned: bool, value: str) -> tuple[str, ...]:
    yours = c.preset or "-"
    standard = (c.standard or yours,) if tuned else ()
    return (c.key, *standard, yours, value, _by(c))


def _beneath(
    change: Change, lessons: dict[int, GivenLesson], width: int, fits: _Fits,
) -> list[str]:
    """A change's reason, with what it cites and rests on. A lesson's own words, and what it's
    keyed to, are under Lessons, by its number."""
    rest = " " * 6
    lesson = lessons.get(change.lesson) if change.lesson is not None else None
    if lesson is not None and change.reason == f"learned on {lesson.stl_name}: {lesson.text}":
        return []
    cites = ", ".join(x for x in (change.feature, change.knowledge) if x)
    out = wrap(rest, fits.named(change.reason) + (f" [{cites}]" if cites else ""), width)
    if change.reading:
        out += wrap(f"{rest}read: ", fits.named(change.reading), width, rest)
    return out


def by_model(request: ReviewRequest, width: int = WIDTH) -> list[str]:
    """The model's proposals, numbered on from the slice's, and those that don't stand."""
    rows = request.by_model
    found = request.suggested
    down = [c for c in request.turned_down if found is not None and c.key in found.declined]
    if found is None or not (rows or down or found.asked):
        return []
    rest = " " * 8
    lines = ["", "  PROPOSED BY THE MODEL (not applied unless you take it)"]
    if found.asked:
        lines += wrap("    It would have asked first: ", found.asked, width, rest)
    for row in rows:
        c = row.change
        if row.standing == "theirs":
            lines += wrap("     -  ", f"{c.key}: {row.by} set {row.now or '-'}, and the model "
                          f"would set {c.value}. {'Yours' if row.by == 'you' else 'That'} "
                          "stands.", width, rest)
            continue
        if row.standing == "so":
            lines += wrap("     -  ", f"{c.key} {c.value}: taken." if c.key in found.taken
                          else f"{c.key}: the model's {c.value} is so here already.", width, rest)
            continue
        if c.key == ORIENTATION:
            here = pose_value(request.orientation.rotation_deg)
        else:
            now = request.proposal.get(c.key)
            here = now.value if now is not None else c.preset
        lines += wrap(f"  {row.number:>3}) ", f"{c.key}  {here or '-'} -> {c.value}", width, rest)
        cites = ", ".join(x for x in (c.feature, c.knowledge) if x and c.key != ORIENTATION)
        if c.reading:
            lines += wrap(rest, c.reading, width)
        if c.reason:
            lines += wrap(rest, f"{c.reason} [model{': ' + cites if cites else ''}]", width)
        if c.key == ORIENTATION:
            if found.reasoning and not found.reasoning.startswith(c.reason):
                lines += wrap(f"{rest}How it read the part turned so: ", found.reasoning, width,
                              rest)
            if found.once_turned:
                then = ", ".join(f"{t.key} {t.preset or '-'} -> {t.value}"
                                 for t in found.once_turned)
                lines += wrap(f"{rest}Once turned it would also propose: ", then, width, rest)
            lines += [x for note in found.design_notes
                      for x in wrap(f"{rest}design, turned so: ", note, width, rest)]
            lines += [x for each in found.refused
                      for x in wrap(f"{rest}refused from the model, turned so: ", each, width,
                                    rest)]
    for c in down:
        lines += wrap("     -  ", f"{c.key} stays {c.value}: {c.reason} [you, saying no to the "
                      f"model's {c.said_no_to}]", width, rest)
    return lines


# --------------------------------------------------------------------------
# What the slice shows
# --------------------------------------------------------------------------

#: How wide a feature's bar is at its longest.
_BAR = 16
#: The shortest it's drawn, however narrow the terminal.
_MIN_BAR = 4


def _slice(request: ReviewRequest, width: int, marks: Moved, fits: _Fits) -> list[str]:
    """A row a feature, longest first, with what matters marked; the layers and the seam; what's
    worth a look; and what the slice proposes, numbered."""
    reading = request.reading
    if reading is None:
        return []
    kept = sorted((f for f in reading.features if f.kind != "travel"), key=lambda f: -f.time_s)
    longest = max((f.time_s for f in kept), default=0.0) or 1.0
    why = _marked(request, reading, kept)
    rows: list[tuple[str, ...]] = [("    feature", "time", "", "area mm2", "layers", "z mm")]
    for f in kept:
        mark = (MOVED if f.kind in marks.features else " ") + (MATTERS if f.kind in why else " ")
        z = f"{f.from_z:g}" if f.from_z == f.to_z else f"{f.from_z:g} to {f.to_z:g}"
        rows.append((f"{mark}  {f.kind}", clock(f.time_s), "", f"{f.area_mm2:.0f}",
                     str(f.layers), z))
    # The bar takes what the other columns leave, up to `_BAR`, so the table fits a narrower
    # terminal.
    room = max(_MIN_BAR, min(_BAR, width - max(len(r) for r in _table(rows))))
    for i, f in enumerate(kept, 1):
        bar = "#" * max(1, round(room * f.time_s / longest))
        rows[i] = (*rows[i][:2], bar.ljust(room), *rows[i][3:])
    lines = _table(rows, right=(1, 3, 4))
    for f in kept:
        for each in why.get(f.kind, ()):
            lines += wrap(f"     {MATTERS} {f.kind}: ", each, width, " " * 8)
    lines.append("")
    again = reading.starts_again()
    if again:
        lines += wrap("    by object ", f"z starts again at layer{'s' if len(again) != 1 else ''} "
                      f"{', '.join(map(str, again))}, so a layer's z is up its own object", width,
                      " " * 14)
    shortest = reading.shortest()
    if shortest is not None:
        times = sorted(layer.time_s for layer in reading.layers)
        lines += wrap("    layers  ", f"{len(reading.layers)}, {clock(times[0])} to "
                      f"{clock(times[-1])} each, {clock(times[len(times) // 2])} typical; the "
                      f"shortest is layer {shortest.n} at z {shortest.z:g} mm", width, " " * 12)
    if reading.seam.seams:
        seam = reading.seam
        where = (f", most at x {seam.at[0]:g} y {seam.at[1]:g} ({seam.at_layers} layers)"
                 if seam.at else "")
        shape = "a line up the wall" if seam.is_line else "scattered"
        lines += wrap("    seam    ", f"{reading.settings.get('seam_position', '?')}: "
                      f"{seam.seams} loop starts, {seam.stacked:.0%} on the one below, so "
                      f"{shape}{where}", width, " " * 12)
    worth = [f for f in request.flags if f.change is None and f.id != PAUSE_PAST_FIT]
    if worth:
        lines += ["", "  WORTH A LOOK"]
        for flag in worth:
            lines += wrap("    - ", fits.named(flag.reading), width, " " * 6)
    for i, (n, flag) in enumerate(request.numbered):
        c = flag.change
        assert c is not None
        if not i:
            lines += ["", "  PROPOSED FROM THE SLICE (not applied unless you take it)"]
        sliced = reading.settings.get(c.key, c.preset)
        yours = f" (your preset: {c.preset})" if c.preset and c.preset != sliced else ""
        lines += wrap(f"  {n:>3}) ", f"{c.key}  {sliced or '-'} -> {c.value}{yours}", width,
                      " " * 8)
        lines += wrap(" " * 8, f"{fits.named(c.reason)} [the slice: {c.knowledge}]", width)
        lines += wrap(" " * 8 + "read: ", fits.named(flag.reading), width, " " * 8)
    return lines


#: A flag's id, and the feature row it's about.
_FLAGGED = {"ironing_hidden": "Ironing", "ironing_inside": "Ironing",
            "ironing_unknown": "Ironing", "fit_elephant_foot": "Bottom surface"}


def _marked(
    request: ReviewRequest, reading: SliceReading, kept: list[FeatureReading],
) -> dict[str, list[str]]:
    """The feature rows that matter, each with why: the first layer after a pause, and what a
    flag is about."""
    marks: dict[str, list[str]] = {}
    for p in reading.pauses:
        for f in kept:
            if abs(f.from_z - p.z) <= TOPS_OUT:
                marks.setdefault(f.kind, []).append(
                    f"starts on layer {p.before_layer}, the first after the pause, at z {p.z:g}")
    numbers = {id(flag): n for n, flag in request.numbered}
    for flag in request.flags:
        kind = _FLAGGED.get(flag.id)
        if kind is None or reading.feature(kind) is None:
            continue
        n = numbers.get(id(flag))
        marks.setdefault(kind, []).append(
            f"the slice proposes {n} for it, below" if n else "worth a look, below")
    return marks


# --------------------------------------------------------------------------
# The part, the pose and the lessons
# --------------------------------------------------------------------------

#: A fit as the part's context says it, and placed in the pose it prints in.
_FIT = re.compile(r"^fit: (.*)$")
_FIT_AT = re.compile(r"^fit (?:at (z \S+ to \S+ mm)|(at z \S+ mm)) as it prints: (.*)$")


class _Fits:
    """The part's fits, numbered as The part says each once."""

    def __init__(self, context: Sequence[str]) -> None:
        self.texts: list[str] = []
        self.placed: dict[str, str] = {}
        for line in context:
            if (m := _FIT.match(line)) is not None:
                fit = m.group(1)
            elif (m := _FIT_AT.match(line)) is not None:
                fit = m.group(3)
                self.placed[fit] = f"{m.group(1) or m.group(2)} as it prints"
            else:
                continue
            if fit not in self.texts:
                self.texts.append(fit)

    def named(self, text: str) -> str:
        """`text` with each fit's words in it said as its number: they're under The part."""
        for n, fit in enumerate(self.texts, 1):
            at = self.placed.get(fit)
            if at is not None:
                text = text.replace(f"{at}: {fit}", f"fit {n} ({at})")
            text = text.replace(fit, f"fit {n}")
        return text


def _part(request: ReviewRequest, width: int, fits: _Fits) -> list[str]:
    """How the part was read, what's seen as it prints, its context with each fit said once,
    and the design notes."""
    proposal = request.proposal
    found = request.suggested
    here = found if found is not None and not turning(found) else None
    reasoning = proposal.reasoning if found is None else here.reasoning if here else ""
    lines: list[str] = []
    if reasoning.strip():
        lines += ["  How the model read it", *wrap("    ", reasoning, width)]
    # A turn since the answer that leaves nothing judged is a warning: it's under Look first.
    seen = [n for n in proposal.notes if _seen(n)]
    if seen:
        lines += ["", "  Seen"]
        lines += [x for note in seen for x in wrap("    ", note, width, "      ")]
    if request.context:
        lines += ["", "  Its context (its design run, or a sidecar beside it)"]
        said: set[str] = set()
        for line in request.context:
            m = _FIT.match(line) or _FIT_AT.match(line)
            if m is not None:
                fit = m.group(1) if m.re is _FIT else m.group(3)
                if fit in said:
                    continue  # said with the fit
                said.add(fit)
                n = fits.texts.index(fit) + 1
                at = fits.placed.get(fit)
                lines += wrap(f"    fit {n}  ", fit + (f"; {at}" if at else ""), width, " " * 11)
            elif seen and line.startswith("seen: ") and SET_ASIDE not in line:
                continue  # said once, under Seen, as it prints
            else:
                lines += wrap("    ", fits.named(line), width, "      ")
    notes = dict.fromkeys((*proposal.design_notes, *(here.design_notes if here else ())))
    if notes:
        lines += ["", "  Design"]
        lines += [x for note in notes for x in wrap("    - ", fits.named(note), width, " " * 6)]
    return lines[1:] if lines and not lines[0] else lines


def _pose(request: ReviewRequest, width: int) -> list[str]:
    """This print's pose, or on a plate each part's, as one part's is shown
    (mqucifer/print-shop#109)."""
    if not request.parts:
        return _one_pose(request, width)
    lines: list[str] = []
    for part in request.parts:
        own = replace(request, orientation=part.orientation, candidates=part.candidates)
        lines += [*([""] if lines else []), f"  PART {part.n}, {part.name}",
                  *_one_pose(own, width)]
    return lines


def _one_pose(request: ReviewRequest, width: int) -> list[str]:
    """This print's pose and why, the turn it needs to fit, then the poses weighed, as a table,
    with this print's marked."""
    o = request.orientation
    lines = wrap(f"    this print  {pose_value(o.rotation_deg)}: ", o.rationale, width, " " * 16)
    # The pose the features, and the lessons keyed to them, were read in
    # (mqucifer/print-shop#128).
    lines += wrap("    features    ", f"read in this pose: {o.pose()}", width, " " * 16)
    if request.slice.turn_deg:
        taken = "taken" if request.turn_taken else "not taken: see Look first"
        lines += wrap("    plate       ", f"turned {turn_words(request.slice.turn_deg)} to fit, "
                      f"{taken}", width, " " * 16)
    if not request.candidates:
        return lines
    measuring = request.measuring
    # What's pending is read first: one that lands between the two reads is shown measured.
    pending = measuring.pending() if measuring is not None else ()
    failed = measuring.failed() if measuring is not None else {}
    ready = measuring.ready() if measuring is not None else request.candidates
    flare = any(c.flare_area_mm2 for c in ready)
    head = ("    pose", "overhang mm2", "on the plate mm2", "tall mm",
            *(("left out mm2",) if flare else ()))
    rows: list[tuple[str, ...]] = [head]
    for c in ready:
        mark = MATTERS if c.rotation_deg == o.rotation_deg else " "
        rows.append((f"  {mark} {pose_value(c.rotation_deg)}", f"{c.overhang_area_mm2:g}",
                     f"{c.bed_contact_area_mm2:g}", f"{c.height_mm:g}",
                     *((f"{c.flare_area_mm2:g}" if c.flare_area_mm2 else "",) if flare else ())))
    lines += ["", *wrap("  ", "The poses it weighed (overhang is what would need support; ! is "
                        "this print)", width)]
    lines += _table(rows, right=range(1, len(head)))
    if flare:
        lines += wrap("    ", "left out: the round or chamfer rising from the plate, left out of "
                      "the overhang, as the layers under it carry it", width, "    ")
    lines += [f"    {pose_value(c.rotation_deg)}  doesn't fit, however it's turned on the plate"
              for c in ready if c.fits_no_way]
    measured = {c.rotation_deg for c in ready}
    lines += [f"    {pose_value(r)}  still being measured: orientation waits for it"
              for r in pending if r not in measured and r not in failed]
    for r, why in failed.items():
        lines += wrap(f"    {pose_value(r)}  ", f"couldn't be measured: {why}", width, " " * 6)
    return lines


def _lessons(request: ReviewRequest, width: int) -> list[str]:
    """Each lesson once, led by its setting and what it did here, its words beneath."""
    lines: list[str] = []
    said: dict[str, int] = {}
    for g in request.proposal.lessons:
        used = (f"not given: {g.withheld}" if g.withheld
                else "set it" if g.applied else (g.why or "read by the model"))
        if g.looked_up:
            used += "; the print loop looked it up with past_prints"
        made = f", in {g.material}" if g.material and g.material != "any" else ""
        head = [f"{g.key}={g.value}" if g.key else "no setting", used, f"from {g.stl_name}",
                f"for a part that's {', '.join(g.traits)}{made}", f"kept by {g.reviewed_by}"]
        lines += wrap(f"  {g.id:>3}  ", " · ".join(head), width, " " * 7)
        # Two lessons kept with the same words are said once, the second by number.
        same = said.get(g.text)
        lines += wrap(" " * 7, f"the same words as lesson {same}" if same else g.text, width)
        said.setdefault(g.text, g.id)
    return lines


# --------------------------------------------------------------------------
# What moved
# --------------------------------------------------------------------------


def what_moved(before: ReviewRequest, now: ReviewRequest) -> Moved:
    """What moved between two rounds of one review: the settings, Orca's estimate, the slice's
    features, and the warnings new or gone."""
    was = {c.key: c.value for c in before.proposal.changes}
    is_ = {c.key: c.value for c in now.proposal.changes}
    settings: list[str] = []
    for key, value in is_.items():
        if key not in was:
            settings.append(f"{key} set to {value}")
        elif was[key] != value:
            settings.append(f"{key} {was[key]} -> {value}")
    settings += [f"{key} back to your preset (was {value})"
                 for key, value in was.items() if key not in is_]
    keys = {k for k in is_ if was.get(k) != is_[k]}
    a, b = before.slice, now.slice
    estimate: list[str] = []
    figures: set[str] = set()
    if a.print_time_s != b.print_time_s:
        estimate.append(f"time {clock(a.print_time_s)} -> {clock(b.print_time_s)} "
                        f"({_signed(b.print_time_s - a.print_time_s)})")
        figures.add("time")
    if (a.filament_g, a.filament_cm3) != (b.filament_g, b.filament_cm3):
        old = f"{a.filament_g} g" if a.filament_g else f"{a.filament_cm3} cm3"
        new = f"{b.filament_g} g" if b.filament_g else f"{b.filament_cm3} cm3"
        estimate.append(f"plastic {old} -> {new}")
        figures.add("plastic")
    if a.layer_count != b.layer_count:
        estimate.append(f"layers {a.layer_count} -> {b.layer_count}")
        figures.add("layers")
    slice_: list[str] = []
    features: set[str] = set()
    if before.reading is not None and now.reading is not None:
        old_f = {f.kind: f for f in before.reading.features if f.kind != "travel"}
        new_f = {f.kind: f for f in now.reading.features if f.kind != "travel"}
        slice_ += [f"{kind} gone, {_signed(-f.time_s)}"
                   for kind, f in old_f.items() if kind not in new_f]
        for kind, f in new_f.items():
            if kind not in old_f:
                slice_.append(f"{kind} new, {_signed(f.time_s)}")
                features.add(kind)
            elif abs(f.time_s - old_f[kind].time_s) >= _MOVED_S:
                slice_.append(f"{kind} {_signed(f.time_s - old_f[kind].time_s)}")
                features.add(kind)
    old_w = [w for w in warnings(before) if not w.waiting]
    new_w = [w for w in warnings(now) if not w.waiting]
    old_full = {w.full for w in old_w}
    new_full = {w.full for w in new_w}
    said = [f"new: {w.short}" for w in new_w if w.full not in old_full]
    said += [f"gone: {w.short}" for w in old_w if w.full not in new_full]
    return Moved(settings=tuple(settings), estimate=tuple(estimate), slice=tuple(slice_),
                 warnings=tuple(said), keys=frozenset(keys), figures=frozenset(figures),
                 features=frozenset(features), new_warnings=frozenset(new_full - old_full))


#: A feature's time that moved by less isn't said to have moved.
_MOVED_S = 30


def _listed(numbers: Iterable[int]) -> str:
    return " ".join(map(str, sorted(set(numbers))))
