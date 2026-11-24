"""After a print with a swap, a person says what's on (mqucifer/print-shop#218).

print-shop never works that out, and never moves the stock by itself. From the swap's start
request what's on isn't known (`Ledger.record_start_requested`), and one word of a person's that
says both halves clears it: that print is over, and this spool is on the printer now. It's asked
in words that say both, with what was typed before dropped:

- before a job prepares, at "From the inventory" (`ask_on_now`);
- at that job's review, with a y (`review._loaded_first`);
- at `stock.py load`, which asks whether it's over (`ask_over`);
- on that print's own outcome card, whose choices say both (`choices`).

Nothing is pre-selected. With no terminal to ask in, nothing is asked or cleared. An answer
counts only while the stock hasn't changed since it was asked (`inventory.stock_changes`): what's
seen, loaded or said since stands. Each print with a swap is asked of by itself.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable

from print_shop.inventory import Inventory, InventoryError, NotKnown, Pick, Spool, spool_lines
from print_shop.ledger import Ledger, Row
from print_shop.plate import plate_job

__all__ = ["ask_on_now", "ask_over", "choices", "said"]

#: Answers that leave it for later.
LATER = ("", "n", "no", "later", "back")


def _likely_first(found: NotKnown, on_hand: list[Spool]) -> list[Spool]:
    likely = [used.spool_id for used in found.could_be]
    return sorted(on_hand, key=lambda spool: (
        likely.index(spool.id) if spool.id in likely else len(likely)))


def ask_on_now(
    stock: Inventory, printer_id: str, *,
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
    asks: bool,
) -> None:
    """Before anything's prepared: whether that print is over, and which spool is on now. One
    answer says both, and the job is then prepared with that spool. `asks` is whether there's a
    terminal to ask in."""
    found = stock.unknown(printer_id)
    if found is None:
        return
    write(f"{found.why()}.")
    if not asks:
        write(f"  There's no terminal to ask in, so nothing's cleared. {found.hold().do}.")
        return
    on_hand = _likely_first(found, stock.spools())
    now = stock.loaded(printer_id)
    write("\n".join(f"    {spool.id}  {spool.named()}" for spool in on_hand))
    if now is not None:
        write(f"{found.over_and_on(now.named())} y says both. If another is on, its number says "
              "the same of that print, and that that one's on.")
    else:
        write(f"{found.over_and_which()} Its number says both.")
    write("Enter for later: nothing's recorded, and Start holds until you say.")
    while True:
        forget()
        try:
            typed = read("on now> ").strip().lower().lstrip("#")
        except EOFError:
            return
        if typed in LATER:
            return
        on = None
        if typed == "y" and now is not None:
            on = now.id
        elif re.fullmatch(r"[0-9]+", typed) and int(typed) in {spool.id for spool in on_hand}:
            on = int(typed)
        if on is None:
            write("  a spool's number from those, " + ("y, " if now else "") + "or Enter for later")
            continue
        try:
            spool = stock.load(on, printer_id, over=found)
        except (InventoryError, sqlite3.Error) as exc:
            write(f"  Not recorded: {exc}")
            return
        over = "is over" if found.started else "is over or can't begin"
        write(f"  Recorded: {found.print_named()} {over}, and {spool.named()}, is on.")
        # Saying one's over says nothing of another.
        ask_on_now(stock, printer_id, read=read, write=write, forget=forget, asks=asks)
        return


def ask_over(
    found: NotKnown, spool: Spool, *,
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
) -> NotKnown | None:
    """At a load: what's said over, where a y says it is and that `spool` is on now."""
    write(f"{found.over_and_on(spool.named())} y says both. Anything else records the load "
          "alone, and Start still holds.")
    forget()
    try:
        typed = read("over> ")
    except EOFError:
        return None
    return found if typed.strip().lower() == "y" else None


def choices(ledger: Ledger, row: Row) -> dict[str, Pick]:
    """What the outcome can be told is on now, by words that say both halves: only on the card
    of a print since which it isn't known, and only a spool on hand."""
    if row.material is None:
        return {}
    found = ledger.not_known(row.material.printer_id, row.job_id)
    if found is None:
        return {}
    with Inventory(ledger.path) as stock:
        on_hand = _likely_first(found, stock.spools())
    return {f"{spool.named()} is on now, and that print is over": Pick(spool.id, found.changes)
            for spool in on_hand}


def said(ledger: Ledger, row: Row) -> str:
    """A swap's spools, and whether this card can say what's on, as the outcome says them."""
    used = "; ".join(spool_lines(row.spools, row.ended_with))
    if row.material is None:
        return used
    printer = row.material.printer_id
    found = ledger.not_known(printer)
    if found is not None and ledger.not_known(printer, row.job_id) is not None:
        return (f"{used}. " if used else "") + (
            f"print-shop doesn't know what's on {printer} until you say this print is over and "
            "which spool is on")
    first = plate_job(row.job_id)
    plate = (ledger.not_known(printer, first)
             if first != row.job_id and found is not None else None)
    if plate is not None:
        # One mark a plate, on its first row: its first card takes the pick
        # (mqucifer/print-shop#109).
        return (f"{used}. " if used else "") + (
            f"print-shop doesn't know what's on {printer} until you say this print is over and "
            f"which spool is on: on the card for {plate.stl_name}, this plate's first, or at "
            "the terminal")
    if not used:
        return ""
    if found is not None:
        return f"{used}. What's on {printer} isn't known since another print: not this card's"
    return f"{used}. What's on {printer} was said since"
