"""What Start checks again, when it's time to print (mqucifer/print-shop#209).

A ready job waits for the person with no deadline, for days and through a restart. What can go
wrong with an old approval isn't its age but that something changed meanwhile: the file, the
spool, the nozzle, the profiles or the printer. So Start is the approval, given when it's time to
print, and these are read again at that moment, in place of a clock:

- **the G-code** still hashes to what was sliced, and is there;
- **the part's file** still hashes to what was prepared from (a file sliced by hand is its
  G-code, taken into the job's folder when it's given);
- **the profiles** the slice used are the store's files as they were, by hash;
- **the loaded spool** is the one the job was prepared for, a swap's first, or one like it:
  that's `graph.why_not_start` (mqucifer/print-shop#218), which the daemon reads first;
- **the fitted nozzle** is the size the job was sliced for;
- **the printer** is free to take a job, read once, read-only.

Each change is said, with what to do, and nothing prints. The bed is the person's: how it was
said to be clear is the daemon's to ask (`Daemon.start`).

Only reads: nothing here writes, arms or commands the printer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from print_shop.core.contracts import NoAutoFix
from print_shop.core.state import PrintJobState
from print_shop.inventory import Inventory, Material
from print_shop.ledger import Ledger
from print_shop.printer.centauri import PrinterClient, preflight
from print_shop.sliced import sha256_of as file_sha256
from print_shop.slicer.store import ProfileRef

__all__ = ["AGAIN", "why_not_now"]

logger = logging.getLogger(__name__)

#: The way out for what Start can't mend: a job prepared from what's since changed.
AGAIN = "Reject and run the job again to prepare it as things are now"


def why_not_now(
    state: PrintJobState, ledger: Ledger | None, *, store_root: Path | None = None,
    printer: PrinterClient | None = None,
) -> list[str]:
    """Why Start mustn't start this job now, each with what to do, or [] if it may. The loaded
    spool is `graph.why_not_start`'s, read before these.

    `store_root` is the profile store the slice's profiles came from; None leaves them unread.
    `printer` is read once, for a snapshot only; None leaves it to the print node.

    A check that can't be made is a reason not to start, said as such.
    """
    found: list[str] = []
    for check in (_gcode, _part, lambda s: _profiles(s, store_root)):
        said = check(state)
        if said:
            found.append(said)
    if ledger is not None:
        nozzle = _nozzle(state, ledger)
        if nozzle:
            found.append(nozzle)
    if printer is not None:
        busy = _printer(state, printer)
        if busy:
            found.append(busy)
    return found


def _gcode(state: PrintJobState) -> str:
    """The G-code to send is the one sliced, byte for byte."""
    path = state.get("gcode_path")
    taken = state.get("sliced")
    expected = taken.file.sha256 if taken is not None else state.get("gcode_sha256", "")
    if not path:
        return f"this job has no sliced file. {AGAIN}"
    gcode = Path(path)
    if not expected:
        return (f"{gcode.name} wasn't hashed when it was sliced, so it can't be checked. "
                f"{AGAIN}")
    try:
        now = file_sha256(gcode)
    except OSError as exc:
        return f"{gcode.name} can't be read ({exc.strerror or exc}). {AGAIN}"
    if now != expected:
        when = "this job took it" if taken is not None else "it was sliced"
        return f"{gcode.name} was changed after {when}. {AGAIN}"
    return ""


def _part(state: PrintJobState) -> str:
    """The part's file is the one the job was prepared from, where it's still to be read."""
    if state.get("sliced") is not None:
        # A file sliced by hand is its G-code, taken into the job's folder at submit and checked
        # above: the file it was taken from may change, and isn't what's sent
        # (mqucifer/print-shop#164).
        return ""
    path, expected = state.get("stl_path"), state.get("stl_sha256", "")
    if not path:
        return ""
    stl = Path(path)
    if not stl.is_file():
        return f"the part's file, {stl}, isn't there any more. {AGAIN}"
    try:
        now = file_sha256(stl)
    except OSError as exc:
        return f"the part's file, {stl.name}, can't be read ({exc.strerror or exc}). {AGAIN}"
    if expected and now != expected:
        return f"the part's file, {stl.name}, was changed after this job was prepared. {AGAIN}"
    return ""


def _profiles(state: PrintJobState, root: Path | None) -> str:
    """The store's profiles the slice used are as they were."""
    used = state.get("profiles_used")
    if used is None or root is None:
        return ""
    changed = [said for kind, ref in (("machine", used.machine), ("process", used.process),
                                      ("filament", used.filament))
               if (said := _profile(kind, ref, root))]
    if not changed:
        return ""
    return f"{'; '.join(changed)}, after this job was sliced. {AGAIN}"


def _profile(kind: str, ref: ProfileRef, root: Path) -> str:
    for file, expected in ((ref.file, ref.sha256), (ref.standard_file, ref.standard_sha256)):
        if not file or not expected:
            continue
        path = root / file
        try:
            if file_sha256(path) != expected:
                return f'the {kind} profile "{ref.name}" was changed ({file})'
        except OSError:
            return f'the {kind} profile "{ref.name}" isn\'t in the store any more ({file})'
    return ""


def _prepared(state: PrintJobState) -> Material | None:
    job = state.get("sliced")
    if job is not None:
        return job.material
    used = state.get("profiles_used")
    return used.material if used is not None else None


def _nozzle(state: PrintJobState, ledger: Ledger) -> str:
    """The fitted nozzle is the size the job was sliced for."""
    material = _prepared(state)
    if material is None:
        return ""
    with Inventory(ledger.path) as inventory:
        fitted = inventory.fitted(material.printer_id)
    want = f"{material.nozzle_mm:g} mm"
    if fitted is None:
        return (f"this job was sliced for a {want} nozzle, and the stock has none fitted on "
                f"{material.printer_id}. Fit it and record it (`stock.py fit "
                f"{material.nozzle_mm:g}`), then Start again")
    if abs(fitted.diameter_mm - material.nozzle_mm) > 1e-6:
        return (f"this job was sliced for a {want} nozzle, and the stock has a "
                f"{fitted.diameter_mm:g} mm one fitted on {material.printer_id}. Fit the {want} "
                f"one and record it (`stock.py fit {material.nozzle_mm:g}`), then Start again; "
                f"or, to print with the {fitted.diameter_mm:g} mm, {AGAIN[0].lower()}{AGAIN[1:]}")
    return ""


def _printer(state: PrintJobState, printer: PrinterClient) -> str:
    """The printer is free, and has room: read once, never waited on."""
    path = state.get("gcode_path")
    try:
        snapshot = printer.snapshot()
    except Exception as exc:  # noqa: BLE001 - a printer that can't be read isn't started on
        logger.warning("the printer couldn't be read at Start: %s", exc)
        return f"the printer couldn't be read ({type(exc).__name__}: {exc}). Start again"
    verdict = preflight(snapshot, Path(path) if path else Path())
    blockers = [v for v in verdict.blockers if v.field != "gcode_path"]  # said above
    if not blockers:
        return ""
    said = "; ".join(
        f"{v.allowed.reason}: {v.detail}" if isinstance(v.allowed, NoAutoFix) else v.detail
        for v in blockers)
    return f"{said}. Start again when it's free"
