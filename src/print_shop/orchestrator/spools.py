"""A swap's spools after the print: the last is the one loaded (mqucifer/print-shop#218).

- **When the print ends, the stock has its last spool as loaded,** where that's known: it
  completed, or stopped over the swap's layer. One stopped under that layer ended with the first.
- **What can't be known isn't guessed.** Orca counts a layer before it writes that layer's
  pause, so a print stopped in the swap's layer may or may not have swapped. Nor is it known
  where the printer doesn't say how far it got, where a start was never confirmed, or where a
  load was recorded while it printed. Then Start holds (`not_known`) until a person records a
  load or picks at that print's outcome. For a start never confirmed, that's a load recorded
  once the printer has been seen done with it: one before can be from under its swap.
- **The person can correct it at the outcome,** and the layers a spool printed stay its own: one
  that ran out keeps them, with the spool that finished after it.
- **The writes are the ledger's,** with the end and with the answer, each one transaction
  (`Ledger.record_ended`, `Ledger.record_answer`): never over a load a person has recorded since
  the print started, nor a later print's.

This works out what to write, and what to say.
"""

from __future__ import annotations

from pycentauri.models import PrintStatus as SdcpPrintStatus

from print_shop.inventory import (
    Inventory,
    Material,
    NotYet,
    SpoolState,
    SpoolUse,
    spool_lines,
    swap_made,
)
from print_shop.ledger import Ledger, Row

__all__ = ["at_end", "choices", "correct", "left_by_swap", "left_said", "not_known", "said"]

def at_end(row: Row, status: int | None, layer: int | None) -> tuple[SpoolUse, ...] | None:
    """A swap's spools as the print ended, or None where they're as was written at its start.

    `status` is the printer's end state, None where it isn't this print's to read, and `layer`
    the one it said, if it was still showing this print.
    """
    if len(row.spools) < 2 or status == SdcpPrintStatus.COMPLETED:
        return None
    first, swap = row.spools[0], row.spools[1]
    if status is not None and layer and swap.from_layer:  # layer 0 says nothing of how far
        if layer > swap.from_layer:
            return None
        if layer < swap.from_layer:
            return first, swap.model_copy(
                update={"material": first.material, "planned": swap.material})
    return first, swap.model_copy(update={"unsure": True})


def choices(ledger: Ledger, row: Row) -> dict[str, int]:
    """The spools the outcome can say it ended with, by the words they're picked by: those on
    hand, and the print's own, which may have run out since."""
    if len(row.spools) < 2:
        return {}
    with Inventory(ledger.path) as stock:
        found = {spool.id: spool.named() for spool in stock.spools()}
    for used in row.spools:
        for own in (used.material, used.planned):
            if own is not None:
                found.setdefault(own.spool_id, own.named())
    return {named: spool_id for spool_id, named in found.items()}


def correct(ledger: Ledger, row: Row, spool_id: int) -> tuple[SpoolUse, ...] | None:
    """The spools as the person says it ended, with `spool_id` on at the end."""
    if len(row.spools) < 2:
        return None
    first, swap = row.spools[0], row.spools[1]
    if row.spools[-1].material.spool_id == spool_id:
        if len(row.spools) == 2 and swap.unsure:
            return first, swap.model_copy(update={"unsure": False})  # so it was made
        return row.spools
    with Inventory(ledger.path) as stock:
        now = first.material.of(stock.spool(spool_id))
        ran_out = stock.spool(swap.material.spool_id).state is SpoolState.EMPTY
    after = (first, swap, SpoolUse(material=now))
    if not swap_made(row.spools):
        # It never printed over the swap, unless it's the spool planned that they say.
        if swap.planned is not None and swap.planned.spool_id == spool_id:
            return first, swap.model_copy(update={"material": now, "planned": None})
        return after
    if ran_out:
        return after  # it printed its layers, and this one finished
    if swap.unsure:
        if spool_id == first.material.spool_id:
            return first, swap.model_copy(update={  # so it wasn't made
                "material": now, "planned": swap.material, "unsure": False})
        return after
    planned = swap.planned or swap.material
    return first, swap.model_copy(update={
        "material": now, "planned": None if planned.spool_id == spool_id else planned})


def said(ledger: Ledger, row: Row) -> str:
    """A print's spools and what the stock has loaded, as the outcome says them, or ""."""
    if not row.spools:
        return ""
    printer = row.spools[0].material.printer_id
    with Inventory(ledger.path) as stock:
        now, unknown = stock.loaded(printer), stock.unknown(printer)
        since = stock.unknown_since(printer)
    used = "; ".join(spool_lines(row.spools))
    if unknown is not None:
        if since == row.job_id:
            return f"{used}. {unknown.why}. {unknown.do}"
        # Another print's: only its own card offers the pick that settles it.
        return (f"{used}. What's on {printer} isn't known since another print, and this card's "
                "pick doesn't settle that")
    has = now.named() if now is not None else "none"
    return f"{used}. The stock has {has} loaded"


def not_known(ledger: Ledger, printer_id: str) -> NotYet | None:
    """Why what's on the printer isn't known, and what clears it, or None: a swap's end still to
    be seen, its start never confirmed, or what the stock was told at its end."""
    for row in ledger.rows():
        if len(row.spools) < 2 or row.ended_at is not None:
            continue
        first, swap = row.spools[0].material, row.spools[1].material
        if first.printer_id != printer_id:
            continue
        if row.started_at is not None:
            return NotYet(
                f"print-shop doesn't know what's on {printer_id}: {row.stl_name}'s print has a "
                "swap and hasn't been seen to end",
                "Wait for it to end (print-shop is looking now)",
                "Run `print_job.py --serve` until it's seen, then run the job again",
            )
        # It may have printed all the same, and swapped.
        seen_over, loaded_since = ledger.start_over(row.job_id)
        if loaded_since:
            continue  # the person's word, from after it was over
        sent = f"the start of {row.stl_name}'s print with a swap was never confirmed"
        if not seen_over:
            return NotYet(
                f"print-shop doesn't know what's on {printer_id}: {sent}, so it may still be "
                "printing",
                "Wait until the printer's seen done with it (print-shop looks, with the printer "
                "on), and record what's on it then",
                "Run `print_job.py --serve` until it's seen, then run the job again",
            )
        with Inventory(ledger.path) as stock:
            return stock.not_known_said(
                printer_id, f"{sent}, so it may have printed", [swap, first])
    with Inventory(ledger.path) as stock:
        return stock.unknown(printer_id)


def left_by_swap(ledger: Ledger, material: Material) -> Row | None:
    """The print whose swap left `material`'s spool loaded: the printer's latest, or None."""
    started = [row for row in ledger.rows() if row.started_at is not None]
    if not started:
        return None
    last = started[-1]
    if not swap_made(last.spools) or last.spools[1].unsure:
        return None
    ended_with = last.spools[-1].material.spool_id
    if ended_with != material.spool_id or ended_with == last.spools[0].material.spool_id:
        return None
    return last


def left_said(ledger: Ledger, material: Material) -> str:
    """Said before the wait, where what's on isn't known, or where the spool loaded is the last
    swap's second: which to load to print that again, or ""."""
    if found := not_known(ledger, material.printer_id):
        return f"{found.why}. {found.do}."
    last = left_by_swap(ledger, material)
    if last is None:
        return ""
    first = last.spools[0].material
    with Inventory(ledger.path) as stock:
        if stock.spool(first.spool_id).state is SpoolState.EMPTY:
            return (f"Loaded since the swap in {last.stl_name}, which started on "
                    f"{first.named()}, which isn't on hand now.")
    return (f"Loaded since the swap in {last.stl_name}, which started on {first.named()}. "
            f"Printing that again? Stop now (Ctrl-C) and `stock.py load {first.spool_id}`.")
