"""What a pause is for: a swap to another spool, or an insert (mqucifer/print-shop#218).

A print has one printer, one spool loaded and swaps by hand. So a pause says which of two it is,
where it's set, and every later step uses that word:

- **`colour_change=` is a swap.** For `pause_at=` it's asked at the review, or typed
  (`pause_at=2.6 for insert`), or carried from the print it came from.
- **A swap names two spools from the stock:** the one the print starts on, of the filament it's
  sliced with, and the one swapped to, of any. Another variety is said at the review, as the slice
  has one filament's settings.
- **A pause from before this says neither,** and is only "a pause".

What's typed is read here and what isn't typed is asked here. Where a pause lands is
`bench.pausing`'s and the reading's (mqucifer/print-shop#203).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from print_shop.bench.pausing import TOPS_OUT
from print_shop.inventory import Material, Spool, SpoolUse

if TYPE_CHECKING:  # both import this
    from print_shop.setup.proposal import Change
    from print_shop.setup.reading import SliceReading

__all__ = [
    "Pause",
    "PauseFor",
    "Stock",
    "Typed",
    "as_printed",
    "ask",
    "because",
    "carried_reason",
    "for_review",
    "marked",
    "planned",
    "read_typed",
    "reason_of",
    "refreshed",
    "said",
    "starting",
    "to_type",
    "uses",
]

#: The spools on hand, read when they're needed: `stock.py` runs beside a job.
Stock = Callable[[], tuple[Spool, ...]]


class PauseFor(StrEnum):
    SWAP = "swap"
    INSERT = "insert"


class Pause(BaseModel):
    """What a pause is for and, for a swap, its two spools."""

    model_config = {"frozen": True}

    kind: PauseFor
    #: The spool the print starts on. None where there's no stock to name one from.
    start: Material | None = None
    #: The spool swapped to.
    to: Material | None = None

    def complete(self, material: Material | None) -> bool:
        """Whether nothing's left to ask. With no stock there are no spools to ask for."""
        return (self.kind is PauseFor.INSERT or material is None
                or (self.start is not None and self.to is not None))


#: The reason recorded where none was given, for each kind and for a pause that says neither.
_DEFAULTS = {PauseFor.SWAP: "a swap", PauseFor.INSERT: "parts dropped in", None: "a pause"}
#: What every pause with no reason was recorded as before mqucifer/print-shop#218, inserts too.
_OLD_DEFAULT = "a colour change"
_SAID = {PauseFor.SWAP: "a swap", PauseFor.INSERT: "an insert", None: "a pause"}


def said(kind: PauseFor | None) -> str:
    return _SAID[kind]


def because(reason: str, pause: Pause | None) -> str:
    """The reason to record: the one given, else the kind's own."""
    given = reason and reason not in _DEFAULTS.values()
    return reason if given else _DEFAULTS[pause.kind if pause else None]


def carried_reason(change: Change) -> str:
    """The reason a pause is carried to another print with: the person's, never a default."""
    if change.reason in _DEFAULTS.values() or (
        change.pause is None and change.reason == _OLD_DEFAULT
    ):
        return ""
    return change.reason


def reason_of(change: Change) -> str:
    """A change's reason as it's shown: a pause from before says only "a pause"."""
    old = change.key == "pause_at" and change.pause is None and change.reason == _OLD_DEFAULT
    return _DEFAULTS[None] if old else change.reason


# --- typed ---------------------------------------------------------------------------------------

_TYPED = re.compile(
    r"^(?P<height>\S+?)(?:\s*mm)?"
    r"(?:\s+(?:for\s+)?(?:an?\s+)?(?P<kind>insert|swap))?"
    r"(?:\s+from\s+#?(?P<start>\d+))?"
    r"(?:\s+to\s+#?(?P<to>\d+))?$",
    re.I,
)


@dataclass(frozen=True)
class Typed:
    z: float
    kind: PauseFor | None = None
    start: int | None = None
    to: int | None = None


def read_typed(value: str, *, swap: bool) -> Typed | str:
    """A pause as typed, or why it can't be read. `swap` says the name typed was colour_change."""
    found = _TYPED.match(value.strip())
    try:
        if found is None:
            raise ValueError(value)
        z = float(found.group("height"))
    except ValueError:
        example = ("colour_change=12.4 from #1 to #2" if swap
                   else "pause_at=12.4 or pause_at=12.4 for insert")
        return f"'{value}' isn't a height: give one in mm, e.g. {example}, or none"
    kind = PauseFor(found.group("kind").lower()) if found.group("kind") else None
    start, to = (int(n) if n else None for n in found.group("start", "to"))
    if kind is PauseFor.INSERT:
        if swap:
            return f"colour_change is a swap: for parts dropped in, pause_at={z:g} for insert"
        if start is not None or to is not None:
            return f"a pause for an insert names no spool: pause_at={z:g} for insert"
    elif swap or start is not None or to is not None:
        kind = PauseFor.SWAP
    return Typed(z=z, kind=kind, start=start, to=to)


def planned(
    typed: Typed, standing: Pause | None, material: Material | None, stock: Stock | None
) -> Pause | None | str:
    """What a typed pause is for, as far as it was said, or why a spool named can't be it.

    `material` is the spool the job was prepared with. A swap that names only the spool to swap
    to starts on the one this review has it starting on, else on that.
    """
    if typed.kind is None:
        return None
    if typed.start is None and typed.to is None:
        return Pause(kind=typed.kind)
    if material is None or stock is None:
        return "there's no stock to name a spool from: set it up with stock.py, or leave it out"
    on_hand = stock()
    start: Material | None
    if typed.start is not None:
        found = _starts_on(typed.start, material, on_hand)
        if isinstance(found, str):
            return found
        start = found
    else:
        start = _still(standing.start if standing else None, material, on_hand) or material
    to: Material | None = None
    if typed.to is not None:
        if typed.to == start.spool_id:
            return (f"it starts on {start.named()}, so it swaps to another: to start on another, "
                    f"colour_change={typed.z:g} from #N to #{typed.to}")
        found = _on_hand(typed.to, material, on_hand)
        if isinstance(found, str):
            return found
        to = found
    return Pause(kind=typed.kind, start=start, to=to)


def to_type(value: str, pause: Pause | None) -> tuple[str, str]:
    """A pause as it's typed at the review: its name, and its value."""
    if pause is None or value == "none":
        return "pause_at", value
    if pause.kind is PauseFor.INSERT:
        return "pause_at", f"{value} for insert"
    if pause.start is None or pause.to is None:
        return "colour_change", value
    return "colour_change", f"{value} from #{pause.start.spool_id} to #{pause.to.spool_id}"


def _on_hand(spool_id: int, material: Material, on_hand: Iterable[Spool]) -> Material | str:
    spool = next((s for s in on_hand if s.id == spool_id), None)
    if spool is None:
        return f"spool #{spool_id} isn't on hand: `stock.py` lists what is"
    return material.of(spool)


def _starts_on(spool_id: int, material: Material, on_hand: Iterable[Spool]) -> Material | str:
    """The spool a print sliced with `material`'s filament starts on, or why it can't be that."""
    found = _on_hand(spool_id, material, on_hand)
    if isinstance(found, str) or found.same_filament(material):
        return found
    return (f"the job is sliced for {material.filament_said()}, and {found.named()} is "
            f"{found.filament_said()}: to start on it, load it (`stock.py load {spool_id}`) and "
            "run the job again")


def _still(
    spool: Material | None, material: Material, on_hand: Iterable[Spool]
) -> Material | None:
    """A spool named before, if this print can still start on it."""
    if spool is None:
        return None
    found = _starts_on(spool.spool_id, material, on_hand)
    return None if isinstance(found, str) else found


# --- carried from a past print -------------------------------------------------------------------


def as_printed(pause: Pause | None, spools: tuple[SpoolUse, ...]) -> Pause | None:
    """A past print's pause as it's printed again: with the spools that print used, where its
    swap was made, else with those planned."""
    if pause is None or pause.kind is not PauseFor.SWAP or not spools:
        return pause
    first, last = spools[0].material, spools[-1].material
    to = last if last.spool_id != first.spool_id else pause.to
    return Pause(kind=pause.kind, start=first, to=to)


def refreshed(
    pause: Pause | None, material: Material | None, stock: Stock | None
) -> Pause | None:
    """A past pause against the stock as it is. A spool it can't have here is left to ask."""
    if pause is None or pause.kind is PauseFor.INSERT:
        return pause
    if material is None or stock is None:
        return Pause(kind=pause.kind)
    on_hand = stock()
    start = _still(pause.start, material, on_hand)
    to = _on_hand(pause.to.spool_id, material, on_hand) if pause.to is not None else None
    if isinstance(to, str) or (to and start and to.spool_id == start.spool_id):
        to = None
    return Pause(kind=pause.kind, start=start, to=to)


# --- asked ---------------------------------------------------------------------------------------


def ask(
    value: str, pause: Pause | None, before: tuple[Pause, str] | None,
    material: Material | None, stock: Stock | None,
    read: Callable[[str], str], write: Callable[[str], None],
) -> Pause | None:
    """What's left to say of a pause, asked. None is the person going back to the review.

    `before` is what's pre-selected, and where it's from: this review's pause, or the past
    print's on offer.
    """
    if pause is None:
        kind = _ask_kind(value, before, read, write)
        if kind is None:
            return None
        pause = Pause(kind=kind)
    if pause.complete(material):
        return pause
    assert material is not None
    if stock is None:
        return pause
    earlier = before[0] if before and before[0].kind is pause.kind else None
    start = pause.start or _ask_start(earlier, material, stock, read, write)
    if start is None:
        return None
    to = pause.to
    if to is None or to.spool_id == start.spool_id:
        to = _ask_to(earlier, start, material, stock, read, write)
        if to is None:
            return None
    return Pause(kind=pause.kind, start=start, to=to)


def _ask_kind(
    value: str, before: tuple[Pause, str] | None,
    read: Callable[[str], str], write: Callable[[str], None],
) -> PauseFor | None:
    write(f"What's the pause at {value} mm for?\n"
          "  1) an insert: parts dropped in\n"
          "  2) a swap: another spool from there up")
    enter = f"Enter for {said(before[0].kind)}, {before[1]}, " if before else ""
    write(f"1 or 2, {enter}or back.")
    while True:
        answer = read("pause> ").strip().lower()
        if answer == "back":
            return None
        if not answer and before:
            return before[0].kind
        if answer in ("1", "insert", "an insert"):
            return PauseFor.INSERT
        if answer in ("2", "swap", "a swap"):
            return PauseFor.SWAP
        write("  1 or 2, or back")


def _ask_start(
    earlier: Pause | None, material: Material, stock: Stock,
    read: Callable[[str], str], write: Callable[[str], None],
) -> Material | None:
    on_hand = stock()
    can = [s for s in on_hand if material.of(s).same_filament(material)]
    if len(can) < 2:
        only = material.of(can[0]) if can else material
        write(f"It starts on {only.named()}: the only spool of the filament it's sliced for.")
        return only
    default = _still(earlier.start if earlier else None, material, on_hand) or material
    write("Which spool does it start on?")
    write("\n".join(f"  {s.line()}" for s in can))
    write(f"Its number, Enter for {default.named()}, or back.")
    return _ask_spool(default, lambda n: _starts_on(n, material, stock()), read, write)


def _ask_to(
    earlier: Pause | None, start: Material, material: Material, stock: Stock,
    read: Callable[[str], str], write: Callable[[str], None],
) -> Material | None:
    on_hand = stock()
    others = [s for s in on_hand if s.id != start.spool_id]
    default: Material | None = None
    if earlier and earlier.to and earlier.to.spool_id != start.spool_id:
        found = _on_hand(earlier.to.spool_id, material, on_hand)
        default = found if isinstance(found, Material) else None
    write("Which spool does it swap to?")
    write("\n".join(f"  {s.line()}" for s in others)
          or "  No other is on hand: add it beside this job (`stock.py add`), then give its number")
    enter = f"Enter for {default.named()}, " if default else ""
    write(f"Its number, {enter}or back.")

    def another(spool_id: int) -> Material | str:
        if spool_id == start.spool_id:
            return f"it starts on {start.named()}: a swap is to another"
        return _on_hand(spool_id, material, stock())

    return _ask_spool(default, another, read, write)


def _ask_spool(
    default: Material | None, named: Callable[[int], Material | str],
    read: Callable[[str], str], write: Callable[[str], None],
) -> Material | None:
    while True:
        answer = read("spool> ").strip().lower().lstrip("#")
        if answer == "back":
            return None
        if not answer and default is not None:
            return default
        if not answer.isdigit():
            write("  a spool's number, or back")
            continue
        found = named(int(answer))
        if isinstance(found, Material):
            return found
        write(f"  {found}")


# --- in the slice, and said ----------------------------------------------------------------------


def marked(reading: SliceReading | None, change: Change | None) -> SliceReading | None:
    """The reading with what its pause is for, which the G-code doesn't say."""
    if reading is None or change is None or change.pause is None or change.value == "none":
        return reading
    asked, pause = float(change.value), change.pause
    only = len(reading.pauses) == 1
    to = pause.to.named() if pause.to is not None else ""
    return reading.model_copy(update={"pauses": tuple(
        p.model_copy(update={"kind": pause.kind, "to": to})
        if only or (p.after_z is not None and abs(p.after_z - asked) <= TOPS_OUT) else p
        for p in reading.pauses
    )})


def uses(pause: Pause | None, reading: SliceReading | None) -> tuple[SpoolUse, ...]:
    """Each spool a swap prints with, in order, with its layer. A slice that was read and has no
    pause written has only the first: the printer never stops for the second."""
    if pause is None or pause.start is None or pause.to is None:
        return ()
    first = SpoolUse(material=pause.start, from_layer=1)
    if reading is None:
        return first, SpoolUse(material=pause.to)
    at = next((p for p in reading.pauses if p.kind is PauseFor.SWAP), None)
    if at is None:
        return (first,)
    return first, SpoolUse(material=pause.to, from_layer=at.before_layer, from_z=at.after_z)


def starting(pause: Pause, on_hand: Iterable[Spool]) -> str:
    """The spool a swap starts on and, where the stock has another loaded, what to do first."""
    start = pause.start
    if start is None:
        return ""
    now = next((s for s in on_hand if s.printer_id == start.printer_id), None)
    if now is not None and now.id == start.spool_id:
        return f"it starts on {start.named()}, as loaded"
    has = now.named() if now is not None else "none"
    return (f"LOAD FIRST: it starts on {start.named()}, and the stock has {has} loaded. "
            f"Load it and record it (`stock.py load {start.spool_id}`) before you approve, "
            "or nothing prints")


def for_review(
    pause: Pause, reading: SliceReading | None, material: Material | None, stock: Stock | None
) -> list[str]:
    """What the review says a pause is for, beside where it landed."""
    if pause.kind is PauseFor.INSERT:
        return ["  the pause is for an insert"]
    if pause.start is None or pause.to is None:
        why = "" if material is not None else ": there's no stock, so its spools aren't recorded"
        return [f"  the pause is a swap{why}"]
    used = uses(pause, reading)
    layer = used[1].from_layer if len(used) > 1 else None
    lines = [f"  the pause is a swap: {pause.start.named()}, then {pause.to.named()}"
             + (f", from layer {layer}" if layer else "")]
    if stock is not None:
        lines.append(f"  {starting(pause, stock())}")
    if not pause.to.same_filament(pause.start):
        lines.append(
            f"  note: spool #{pause.to.spool_id} is {pause.to.filament_said()}, and the slice has "
            f"one filament's settings, those of {pause.start.filament_said()}: the layers after "
            "the swap print with those"
        )
    return lines
