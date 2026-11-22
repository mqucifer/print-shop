"""A swap's spools after the print: the last is the one loaded (mqucifer/print-shop#218).

- **When the print ends, the stock has its last spool as loaded.** One stopped, or failed, at the
  swap's pause or under it ended with the first, and the ledger says so. Orca counts a layer
  before it writes that layer's pause, so paused there the printer says the swap's own layer.
  Where the printer doesn't say how far it got, the last is taken, and the outcome can correct it.
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
    SpoolState,
    SpoolUse,
    spool_lines,
    swap_made,
)
from print_shop.ledger import Ledger, Row

__all__ = ["at_end", "choices", "correct", "left_by_swap", "left_said", "said"]

#: The ends that can come before the swap, at any layer it says, 0 included.
_CUT_SHORT = (SdcpPrintStatus.STOPPED, SdcpPrintStatus.ERROR)


def at_end(row: Row, status: int | None, layer: int | None) -> tuple[SpoolUse, ...] | None:
    """A swap's spools as the print ended, or None where they're as was written at its start.
    `layer` is the one the printer said, if it was still showing this print."""
    if len(row.spools) < 2 or layer is None:
        return None
    first, swap = row.spools[0], row.spools[1]
    says = status in _CUT_SHORT or (status == SdcpPrintStatus.IDLE and layer > 0)
    if not says or swap.from_layer is None or layer > swap.from_layer:
        return None
    return first, swap.model_copy(update={"material": first.material, "planned": swap.material})


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
    if row.spools[-1].material.spool_id == spool_id:
        return row.spools
    first, swap = row.spools[0], row.spools[1]
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
    planned = swap.planned or swap.material
    return first, swap.model_copy(update={
        "material": now, "planned": None if planned.spool_id == spool_id else planned})


def said(ledger: Ledger, row: Row) -> str:
    """A print's spools and what the stock has loaded, as the outcome says them, or ""."""
    if not row.spools:
        return ""
    with Inventory(ledger.path) as stock:
        now = stock.loaded(row.spools[0].material.printer_id)
    has = now.named() if now is not None else "none"
    return "; ".join(spool_lines(row.spools)) + f". The stock has {has} loaded"


def left_by_swap(ledger: Ledger, material: Material | None) -> Row | None:
    """The print whose swap left `material`'s spool loaded: the printer's latest, or None.
    Starting on that spool is that print reversed, so it's never pre-selected."""
    if material is None:
        return None
    started = [row for row in ledger.rows() if row.started_at is not None]
    if not started:
        return None
    last = started[-1]
    if not swap_made(last.spools) or last.spools[-1].material.spool_id != material.spool_id:
        return None
    return last


def left_said(ledger: Ledger, material: Material) -> str:
    """Said before the wait, where the spool loaded is the last swap's second: which to load to
    print that again, or ""."""
    last = left_by_swap(ledger, material)
    if last is None:
        return ""
    first = last.spools[0].material
    return (f"Loaded since the swap in {last.stl_name}, which started on {first.named()}. "
            f"Printing that again? Stop now (Ctrl-C) and `stock.py load {first.spool_id}`.")
