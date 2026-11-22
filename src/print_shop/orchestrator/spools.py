"""A swap's spools after the print: the last is the one loaded (mqucifer/print-shop#218).

- **When the print ends, the stock has its last spool as loaded.** A print stopped, or failed,
  under the swap's layer never got to it, so it ended with the first, and the ledger says so.
  Where the printer doesn't say how far it got, the last is taken, and the outcome can correct it.
- **The person can correct it at the outcome:** the ledger keeps the spool it ended with beside
  the one planned.
- **What the stock was told since is never written over.** It's moved only while it still has
  what this print left (or, where that spool ran out and is marked empty, the one before it), and
  only where no later print has started on that printer.
"""

from __future__ import annotations

import logging

from pycentauri.models import PrintStatus as SdcpPrintStatus

from print_shop.inventory import Inventory, Material, SpoolState, SpoolUse
from print_shop.ledger import Ledger, Row

__all__ = ["at_end", "choices", "correct", "said"]

logger = logging.getLogger(__name__)

#: The ends that can come before the swap.
_CUT_SHORT = (SdcpPrintStatus.STOPPED, SdcpPrintStatus.ERROR)


def at_end(ledger: Ledger, row: Row, status: int | None, layer: int | None) -> None:
    """The stock left as a print with a swap left the printer. `layer` is the one the printer
    said it was on, if it was still showing this print."""
    if len(row.spools) < 2:
        return
    first, last = row.spools[0].material, row.spools[-1]
    under = bool(layer and last.from_layer and layer < last.from_layer)
    if status in _CUT_SHORT and under:
        ledger.record_last_spool(row.job_id, last.model_copy(update={
            "material": first, "planned": last.planned or last.material}))
        return
    _load(ledger, row, left=(first,), now=last.material)


def choices(ledger: Ledger, row: Row) -> dict[str, int]:
    """The spools the outcome can say it ended with, by the words they're picked by."""
    if len(row.spools) < 2:
        return {}
    with Inventory(ledger.path) as stock:
        return {spool.named(): spool.id for spool in stock.spools()}


def correct(ledger: Ledger, row: Row, spool_id: int) -> None:
    """The person says at the outcome which spool it ended with."""
    if len(row.spools) < 2:
        return
    last = row.spools[-1]
    was = last.material
    if was.spool_id == spool_id:
        return
    with Inventory(ledger.path) as stock:
        now = was.of(stock.spool(spool_id))
        ran_out = stock.spool(was.spool_id).state is SpoolState.EMPTY
    planned = last.planned or was
    ledger.record_last_spool(row.job_id, last.model_copy(update={
        "material": now, "planned": None if planned.spool_id == spool_id else planned}))
    _load(ledger, row, left=(was, row.spools[0].material) if ran_out else (was,), now=now)


def said(ledger: Ledger, row: Row) -> str:
    """A print's spools and what the stock has loaded, as the outcome says them, or ""."""
    if not row.spools:
        return ""
    used: tuple[SpoolUse, ...] = row.spools
    with Inventory(ledger.path) as stock:
        now = stock.loaded(used[0].material.printer_id)
    has = now.named() if now is not None else "none"
    return "; ".join(s.line() for s in used) + f". The stock has {has} loaded"


def _load(ledger: Ledger, row: Row, *, left: tuple[Material, ...], now: Material) -> None:
    """`now` loaded, where the stock still has one of `left` and this is the printer's latest
    print."""
    if not _latest(ledger, row):
        return
    with Inventory(ledger.path) as stock:
        printer = now.printer_id
        loaded = stock.loaded(printer)
        if loaded is None or loaded.id not in {spool.spool_id for spool in left}:
            return  # told otherwise since
        if stock.spool(now.spool_id).state is SpoolState.EMPTY:
            logger.warning("job %s ended with %s, which the stock has as empty: not loaded",
                           row.job_id, now.named())
            return
        stock.load(now.spool_id, printer)


def _latest(ledger: Ledger, row: Row) -> bool:
    """Whether no print of another file has started on this one's printer since it did."""
    rows = ledger.rows()
    at = next(n for n, r in enumerate(rows) if r.job_id == row.job_id)
    printer = row.spools[0].material.printer_id
    return not any(
        later.started_at is not None and later.gcode_name != row.gcode_name
        and (later.material is None or later.material.printer_id == printer)
        for later in rows[at + 1:]
    )
