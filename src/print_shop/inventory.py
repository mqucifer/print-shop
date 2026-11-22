"""What's on hand: printers, the nozzles owned and fitted, and the filament spools.

mqucifer/print-shop#11. The Sponsor, 2026-10-25: "Maybe there's a quick way to
start maintaining WHAT materials I have as well. I do not today have any type
of AMS system to read from." So the Sponsor is the only source, and keeping it
up is one command line per change (`scripts/stock.py`).

It lives in the ledger's own file (`~/print-shop/ledger.sqlite`), so the
nightly archive keeps it and a print can point at the spool it used. Its
tables are created if missing, beside the ledger's, without moving the
ledger's schema version.

- **More than one printer** from the start. A nozzle or spool belongs to a
  printer only while it's fitted or loaded: one fitted nozzle and one loaded
  spool per printer, which the database enforces.
- **A spool is named the Sponsor's way:** manufacturer, type and variety,
  with a colour, and optionally grams left and which of the Sponsor's
  tunings to print it with, chosen when it's added. The colour and the tuning
  can be corrected (`edit`, mqucifer/print-shop#220); a print keeps its own
  copy of the spool, so one already printed isn't changed by it.
- **A print takes its defaults from it** (`scripts/print_job.py`): the fitted
  nozzle and the loaded spool. What isn't on hand is refused.
- **A swap's print keeps each spool in order, with its layer** (`print_spools`,
  mqucifer/print-shop#218): it starts only with the first loaded, and leaves the last loaded.
- **So a job starts only on what it was prepared for** (`why_not_start`): as the stock moves by
  itself at a swap's end, what's loaded is compared again at Start, for every job. Another spool
  of the same variety, colour and tuning will do, and is the one recorded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

__all__ = [
    "SCHEMA",
    "Inventory",
    "InventoryError",
    "Material",
    "NotYet",
    "Nozzle",
    "Printer",
    "Spool",
    "SpoolState",
    "SpoolUse",
    "leave_loaded",
    "loads_recorded",
    "spool_lines",
    "swap_made",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS printers (
    id          TEXT PRIMARY KEY,                -- the Sponsor's short name, e.g. "cc1"
    model       TEXT NOT NULL,
    added_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nozzles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    diameter_mm REAL NOT NULL CHECK (diameter_mm > 0),
    note        TEXT NOT NULL DEFAULT '',
    printer_id  TEXT REFERENCES printers(id),    -- set while fitted
    added_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_fitted_nozzle ON nozzles(printer_id)
    WHERE printer_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS spools (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    manufacturer TEXT NOT NULL,
    type         TEXT NOT NULL,                  -- PLA, PETG, TPU, ...
    variety      TEXT NOT NULL,                  -- "Elegoo Rapid PETG"
    colour       TEXT NOT NULL,
    grams_left   REAL CHECK (grams_left IS NULL OR grams_left >= 0),
    tuning       TEXT,                           -- the Sponsor's tuning to print it with
    state        TEXT NOT NULL CHECK (state IN ('on_hand', 'loaded', 'empty')),
    printer_id   TEXT REFERENCES printers(id),   -- set while loaded
    added_at     TEXT NOT NULL,
    emptied_at   TEXT,
    CHECK ((state = 'loaded') = (printer_id IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_loaded_spool ON spools(printer_id)
    WHERE state = 'loaded';
CREATE TABLE IF NOT EXISTS print_material (
    job_id      TEXT PRIMARY KEY REFERENCES prints(job_id),
    printer_id  TEXT NOT NULL,
    nozzle_mm   REAL NOT NULL,
    spool_id    INTEGER REFERENCES spools(id),
    spool       TEXT NOT NULL                    -- JSON Material: the spool as it was
);
CREATE TABLE IF NOT EXISTS print_spools (
    job_id      TEXT NOT NULL REFERENCES prints(job_id),
    position    INTEGER NOT NULL,                -- 0 is the spool it started on
    spool_id    INTEGER NOT NULL REFERENCES spools(id),
    from_layer  INTEGER,                         -- the first layer it printed, if it's known
    from_z      REAL,                            -- the top of the layer under that one
    spool       TEXT NOT NULL,                   -- JSON Material: the spool as it was
    planned     TEXT,                            -- JSON Material: the one planned, if another was
    loads       INTEGER,                         -- at 0: the printer's loads as it started
    PRIMARY KEY (job_id, position)
);
CREATE TABLE IF NOT EXISTS printer_loads (
    printer_id  TEXT PRIMARY KEY REFERENCES printers(id),
    n           INTEGER NOT NULL                 -- how many times a person has recorded a load
);
"""


class InventoryError(ValueError):
    """A change the inventory refuses, saying why and what there is instead."""


class SpoolState(StrEnum):
    ON_HAND = "on_hand"
    LOADED = "loaded"
    EMPTY = "empty"


class Printer(BaseModel):
    id: str
    model: str


class Nozzle(BaseModel):
    id: int
    diameter_mm: float
    note: str
    printer_id: str | None

    def line(self) -> str:
        where = f"fitted on {self.printer_id}" if self.printer_id else "owned, not fitted"
        note = f" {self.note}" if self.note else ""
        return f"nozzle #{self.id}  {self.diameter_mm:g} mm{note}  ({where})"


class Spool(BaseModel):
    id: int
    manufacturer: str
    type: str
    variety: str
    colour: str
    grams_left: float | None
    tuning: str | None
    state: SpoolState
    printer_id: str | None

    @property
    def filament(self) -> str:
        """What picks its profile (mqucifer/print-shop#8): the tuning, else the variety."""
        return self.tuning or self.variety

    def named(self) -> str:
        return f"spool #{self.id}, {self.variety}, {self.colour}"

    def line(self) -> str:
        parts = [f"spool #{self.id}", self.variety, self.colour]
        if self.grams_left is not None:
            parts.append(f"{self.grams_left:g} g")
        if self.tuning:
            parts.append(f"tuning \"{self.tuning}\"")
        where = {SpoolState.LOADED: f"loaded on {self.printer_id}",
                 SpoolState.ON_HAND: "on hand", SpoolState.EMPTY: "empty"}[self.state]
        return "  ".join(parts) + f"  ({where})"


class Material(BaseModel):
    """What a print used from the inventory, as it was when it printed."""

    model_config = {"frozen": True}

    printer_id: str
    printer_model: str
    nozzle_mm: float
    spool_id: int
    manufacturer: str
    type: str
    variety: str
    colour: str
    tuning: str | None = None

    def line(self) -> str:
        return f"{self.printer_id} with a {self.nozzle_mm:g} mm nozzle; {self.named()}"

    def named(self) -> str:
        return f"spool #{self.spool_id}, {self.variety}, {self.colour}"

    def of(self, spool: Spool) -> Material:
        """The same printer and nozzle, with `spool` on it."""
        return self.model_copy(update={
            "spool_id": spool.id, "manufacturer": spool.manufacturer, "type": spool.type,
            "variety": spool.variety, "colour": spool.colour, "tuning": spool.tuning,
        })

    def same_filament(self, other: Material) -> bool:
        """Whether both are sliced with one filament's settings: the variety and its tuning."""
        return (self.variety.casefold(), self.tuning) == (other.variety.casefold(), other.tuning)

    def filament_said(self) -> str:
        return self.variety + (f' tuned "{self.tuning}"' if self.tuning else "")

    def takes(self, spool: Spool, not_on: int | None = None) -> bool:
        """Whether a job prepared for this spool starts on `spool`: this one, or another that
        prints the same, of its variety, colour and tuning. Never `not_on`, the spool a swap is
        to: starting there is the swap not made, or reversed."""
        if spool.id == self.spool_id:
            return True
        other = self.of(spool)
        return (spool.id != not_on and other.same_filament(self)
                and other.colour.casefold() == self.colour.casefold())


class NotYet(NamedTuple):
    """Why a job doesn't start yet, and what to do about it."""

    why: str
    do: str  # at the gate, before approving again
    again: str  # once refused at Start, where the job has ended
    instead: str = ""  # the way out, for a change of mind: what Reject and a new run gives

    def at_gate(self) -> str:
        out = f"; or, {self.instead}, Reject and run the job again" if self.instead else ""
        return f"{self.why}. {self.do}, then approve again{out}"


class SpoolUse(BaseModel):
    """One of the spools a print with a swap used, in order, and from which layer
    (mqucifer/print-shop#218)."""

    model_config = {"frozen": True}

    material: Material
    from_layer: int | None = None
    #: The top of the last layer printed before it.
    from_z: float | None = None
    #: The spool planned here, where it ended with another: the swap wasn't reached, or the
    #: person corrected it at the outcome.
    planned: Material | None = None

    def line(self) -> str:
        at = f", from layer {self.from_layer}" if self.from_layer else ""
        if self.from_z is not None:
            at += f", over z={self.from_z:g}"
        was = f" ({self.planned.named()} was planned)" if self.planned else ""
        return f"{self.material.named()}{at}{was}"


def swap_made(spools: tuple[SpoolUse, ...]) -> bool:
    """Whether a print with a swap got to it."""
    if len(spools) < 2:
        return False
    first, swap = spools[0], spools[1]
    return not (swap.planned is not None and swap.material.spool_id == first.material.spool_id)


def spool_lines(spools: tuple[SpoolUse, ...]) -> list[str]:
    """A print's spools as they're said, in order. A swap that wasn't made is said so, never
    as its first spool from the swap's layer up."""
    lines = [used.line() for used in spools[:2]]
    if len(spools) > 1 and not swap_made(spools):
        first, swap = spools[0], spools[1]
        assert swap.planned is not None
        at = f" at layer {swap.from_layer}" if swap.from_layer else ""
        through = "" if len(spools) > 2 else ", all the way"
        lines = [f"{first.material.named()}{through}: the swap to {swap.planned.named()}{at} "
                 "wasn't made"]
    lines += [f"then {used.material.named()}, from a layer that isn't known"
              for used in spools[2:]]
    return lines


def loads_recorded(db: sqlite3.Connection, printer_id: str) -> int:
    """How many times a person has recorded a load on the printer: whether they have since."""
    found = db.execute(
        "SELECT n FROM printer_loads WHERE printer_id = ?", (printer_id,)
    ).fetchone()
    return int(found[0]) if found is not None else 0


def leave_loaded(db: sqlite3.Connection, printer_id: str, spool_id: int) -> bool:
    """The spool a print left on the printer, as loaded, in the caller's transaction
    (mqucifer/print-shop#218). It isn't a person's load, so it isn't counted as one. False for
    a spool marked empty, which is left so."""
    return _loaded(db, printer_id, spool_id)


def _loaded(db: sqlite3.Connection, printer_id: str, spool_id: int) -> bool:
    found = db.execute("SELECT state FROM spools WHERE id = ?", (spool_id,)).fetchone()
    if found is None or found[0] == SpoolState.EMPTY:
        return False
    db.execute(
        "UPDATE spools SET state = 'on_hand', printer_id = NULL "
        "WHERE printer_id = ? AND state = 'loaded'",
        (printer_id,),
    )
    db.execute(
        "UPDATE spools SET state = 'loaded', printer_id = ? WHERE id = ?", (printer_id, spool_id)
    )
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Inventory:
    """The inventory in the ledger's file, open for one command."""

    def __init__(self, path: Path) -> None:
        # The ledger makes its file and its tables, ours among them.
        from print_shop.ledger import Ledger

        Ledger(path).close()
        self.path = path
        self._conn = sqlite3.connect(path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Inventory:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
        # Like the ledger: a copy of the main file alone holds every change.
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # --- printers --------------------------------------------------------

    def add_printer(self, printer_id: str, model: str) -> Printer:
        with self._write() as db:
            try:
                db.execute("INSERT INTO printers VALUES (?, ?, ?)", (printer_id, model, _now()))
            except sqlite3.IntegrityError as exc:
                raise InventoryError(f"there's already a printer called {printer_id}") from exc
        return Printer(id=printer_id, model=model)

    def printers(self) -> list[Printer]:
        rows = self._conn.execute("SELECT id, model FROM printers ORDER BY id").fetchall()
        return [Printer(**dict(r)) for r in rows]

    def printer(self, printer_id: str | None = None) -> Printer:
        """The printer named, or the only one there is.

        Raises:
            InventoryError: If there's none, or several and none was named.
        """
        printers = self.printers()
        if printer_id is not None:
            found = [p for p in printers if p.id == printer_id]
            if not found:
                raise InventoryError(f"no printer is called {printer_id}; there are: "
                                     + (", ".join(p.id for p in printers) or "none"))
            return found[0]
        if len(printers) == 1:
            return printers[0]
        if not printers:
            raise InventoryError("no printer is set up yet: " + SETUP)
        raise InventoryError("name the printer (--printer): " + ", ".join(p.id for p in printers))

    # --- nozzles ---------------------------------------------------------

    def fit(self, diameter_mm: float, printer_id: str | None = None, note: str = "") -> Nozzle:
        """Fit a nozzle: one owned and unfitted of that size, else a new one recorded."""
        printer = self.printer(printer_id)
        with self._write() as db:
            db.execute("UPDATE nozzles SET printer_id = NULL WHERE printer_id = ?", (printer.id,))
            row = db.execute(
                "SELECT id FROM nozzles WHERE printer_id IS NULL AND diameter_mm = ? "
                "AND (? = '' OR note = ?) ORDER BY id LIMIT 1",
                (diameter_mm, note, note),
            ).fetchone()
            if row is None:
                nozzle_id = db.execute(
                    "INSERT INTO nozzles (diameter_mm, note, printer_id, added_at) "
                    "VALUES (?, ?, ?, ?)",
                    (diameter_mm, note, printer.id, _now()),
                ).lastrowid
            else:
                nozzle_id = row["id"]
                db.execute(
                    "UPDATE nozzles SET printer_id = ? WHERE id = ?", (printer.id, nozzle_id)
                )
        return self._nozzle(int(nozzle_id or 0))

    def add_nozzle(self, diameter_mm: float, note: str = "") -> Nozzle:
        """A nozzle owned and not fitted."""
        with self._write() as db:
            nozzle_id = db.execute(
                "INSERT INTO nozzles (diameter_mm, note, added_at) VALUES (?, ?, ?)",
                (diameter_mm, note, _now()),
            ).lastrowid
        return self._nozzle(int(nozzle_id or 0))

    def nozzles(self) -> list[Nozzle]:
        rows = self._conn.execute(
            "SELECT id, diameter_mm, note, printer_id FROM nozzles ORDER BY diameter_mm, id"
        ).fetchall()
        return [Nozzle(**dict(r)) for r in rows]

    def fitted(self, printer_id: str) -> Nozzle | None:
        return next((n for n in self.nozzles() if n.printer_id == printer_id), None)

    def _nozzle(self, nozzle_id: int) -> Nozzle:
        return next(n for n in self.nozzles() if n.id == nozzle_id)

    # --- spools ----------------------------------------------------------

    def add_spool(
        self,
        *,
        manufacturer: str,
        type: str,
        variety: str,
        colour: str,
        grams_left: float | None = None,
        tuning: str | None = None,
    ) -> Spool:
        with self._write() as db:
            spool_id = db.execute(
                "INSERT INTO spools (manufacturer, type, variety, colour, grams_left, tuning, "
                "state, added_at) VALUES (?, ?, ?, ?, ?, ?, 'on_hand', ?)",
                (manufacturer, type, variety, colour, grams_left, tuning, _now()),
            ).lastrowid
        return self.spool(int(spool_id or 0))

    def load(self, spool_id: int, printer_id: str | None = None) -> Spool:
        """Load a spool, as a person says; the one loaded before on that printer goes back on
        hand. It's counted, so that a print's end never writes over it, the same spool again
        included (`loads_recorded`)."""
        printer = self.printer(printer_id)
        self.spool(spool_id)
        with self._write() as db:
            # Read in the write's own transaction: a job's end and stock.py run side by side.
            if not _loaded(db, printer.id, spool_id):
                raise InventoryError(f"spool #{spool_id} is empty")
            db.execute(
                "INSERT INTO printer_loads (printer_id, n) VALUES (?, 1)"
                " ON CONFLICT (printer_id) DO UPDATE SET n = n + 1",
                (printer.id,),
            )
        return self.spool(spool_id)

    def edit(
        self,
        spool_id: int,
        *,
        colour: str | None = None,
        tuning: str | None = None,
        untuned: bool = False,
    ) -> Spool:
        """Correct a spool's colour or tuning (mqucifer/print-shop#220), and nothing else of it.

        None leaves that one as it is; `untuned` clears the tuning, for the variety's default.
        """
        self.spool(spool_id)
        with self._write() as db:
            if colour is not None:
                db.execute("UPDATE spools SET colour = ? WHERE id = ?", (colour, spool_id))
            if untuned or tuning is not None:
                db.execute("UPDATE spools SET tuning = ? WHERE id = ?",
                           (None if untuned else tuning, spool_id))
        return self.spool(spool_id)

    def empty(self, spool_id: int) -> Spool:
        self.spool(spool_id)
        with self._write() as db:
            db.execute(
                "UPDATE spools SET state = 'empty', printer_id = NULL, emptied_at = ? "
                "WHERE id = ?",
                (_now(), spool_id),
            )
        return self.spool(spool_id)

    def spools(self, *, with_empty: bool = False) -> list[Spool]:
        rows = self._conn.execute(
            "SELECT id, manufacturer, type, variety, colour, grams_left, tuning, state, "
            "printer_id FROM spools ORDER BY state = 'empty', type, variety, id"
        ).fetchall()
        found = [Spool(**dict(r)) for r in rows]
        return [s for s in found if with_empty or s.state is not SpoolState.EMPTY]

    def spool(self, spool_id: int) -> Spool:
        found = [s for s in self.spools(with_empty=True) if s.id == spool_id]
        if not found:
            raise InventoryError(f"there's no spool #{spool_id}")
        return found[0]

    def loaded(self, printer_id: str) -> Spool | None:
        return next((s for s in self.spools() if s.printer_id == printer_id), None)

    # --- what a print uses -------------------------------------------------

    def material(self, printer_id: str | None = None) -> Material:
        """The printer's fitted nozzle and loaded spool.

        Raises:
            InventoryError: If none is fitted or loaded, saying what's on hand.
        """
        printer = self.printer(printer_id)
        nozzle = self.fitted(printer.id)
        if nozzle is None:
            raise InventoryError(
                f"no nozzle is fitted on {printer.id}: `stock fit 0.4`. Owned: "
                + (", ".join(f"{n.diameter_mm:g} mm" for n in self.nozzles()) or "none")
            )
        spool = self.loaded(printer.id)
        if spool is None:
            raise InventoryError(
                f"no spool is loaded on {printer.id}: `stock load <id>`. On hand:\n"
                + self.listing()
            )
        return Material(
            printer_id=printer.id, printer_model=printer.model, nozzle_mm=nozzle.diameter_mm,
            spool_id=spool.id, manufacturer=spool.manufacturer, type=spool.type,
            variety=spool.variety, colour=spool.colour, tuning=spool.tuning,
        )

    def for_print(
        self, nozzle_mm: float | None, filament: str | None, printer_id: str | None = None
    ) -> Material:
        """What a print uses: the fitted nozzle and loaded spool, which it may name.

        A nozzle or filament named but not fitted or loaded is refused, saying
        what is and how to change it.

        Raises:
            InventoryError: On anything not fitted or loaded, or nothing set up.
        """
        material = self.material(printer_id)
        if nozzle_mm is not None and abs(nozzle_mm - material.nozzle_mm) > 1e-6:
            raise InventoryError(
                f"the fitted nozzle on {material.printer_id} is {material.nozzle_mm:g} mm, "
                f"not {nozzle_mm:g}: `stock fit {nozzle_mm:g}` first if you've changed it"
            )
        if filament is not None:
            names = {material.variety.casefold(), f"#{material.spool_id}"}
            if material.tuning:
                names.add(material.tuning.casefold())
            if filament.casefold() not in names:
                raise InventoryError(
                    f"loaded on {material.printer_id} is spool #{material.spool_id}, "
                    f"{material.variety}, {material.colour}, not {filament}: "
                    f"`stock load <id>` first if you've changed it. On hand:\n{self.listing()}"
                )
        return material

    def why_not_start(
        self, first: Material, *, not_on: int | None = None, prepared: Material | None = None,
    ) -> NotYet | None:
        """Why a job can't start with the stock as it is, or None (mqucifer/print-shop#218).

        `first` is the spool it was prepared for, or a swap's first, with `not_on` the spool
        it's swapped to. `prepared` is the job's own where a swap starts on another: its tuning
        still has to be the one the job was sliced with.
        """
        now = self.loaded(first.printer_id)
        sliced_for = [first] if now is not None and now.id == first.spool_id else []
        if prepared is not None and prepared.spool_id != first.spool_id:
            sliced_for.append(prepared)
        for used in sliced_for:
            spool = self.spool(used.spool_id)
            if spool.tuning == used.tuning:
                continue
            was, is_now = (f'"{t}"' if t else "the variety's default"
                           for t in (used.tuning, spool.tuning))
            back = f'--tuning "{used.tuning}"' if used.tuning else "--tuning none"
            return NotYet(
                f"spool #{spool.id}'s tuning was changed from {was} to {is_now} after this job "
                "was prepared",
                f"Set it back (`stock.py edit {spool.id} {back}`)",
                "Run the job again to prepare it with the spool as it is now",
                "to print with it as it is",
            )
        if now is not None and first.takes(now, not_on):
            return None
        what = "this print starts on" if not_on is not None else "this job was prepared for"
        has = now.named() if now is not None else "none"
        if now is not None and now.tuning != first.tuning:
            tuned = f'"{now.tuning}"' if now.tuning else "the variety's default"
            has += f" (tuning {tuned})"
        if self.spool(first.spool_id).state is SpoolState.EMPTY:
            load = (f"That spool is marked empty: load one of {first.filament_said()}, "
                    f"{first.colour}, and record it (`stock.py load <id>`)")
        else:
            load = f"Load {first.named()}, and record it (`stock.py load {first.spool_id}`)"
        instead = ""
        if not_on is not None:
            instead = "for other spools"
        elif now is not None:
            same = first.of(now).same_filament(first)
            instead = f"to print in {now.colour}" if same else f"to print with {now.named()}"
        return NotYet(
            f"{what} {first.named()}, and the stock has {has} loaded on {first.printer_id}",
            load, f"{load}, and run the job again", instead,
        )

    def at_start(
        self, first: Material, *, not_on: int | None = None, prepared: Material | None = None,
        done: str = "nothing was uploaded or printed",
    ) -> Material:
        """The spool a job starts on, as it starts (mqucifer/print-shop#220,
        mqucifer/print-shop#218): the one loaded.

        A colour corrected on the spool is taken, as nothing was sliced with it. `done` is what
        has happened, for the refusal to say.

        Raises:
            InventoryError: As `why_not_start` says.
        """
        refused = self.why_not_start(first, not_on=not_on, prepared=prepared)
        if refused is not None:
            raise InventoryError(f"{refused.why}, so {done}. {refused.again}.")
        now = self.loaded(first.printer_id)
        if now is None or not first.takes(now, not_on):
            raise InventoryError(f"what's loaded changed as it was checked, so {done}.")
        if now.id != first.spool_id:
            return first.of(now)
        return first.model_copy(update={"colour": now.colour})

    def listing(self, *, with_empty: bool = False) -> str:
        """Everything on hand, loaded and fitted, a line each."""
        lines = [f"printer {p.id}  {p.model}" for p in self.printers()]
        lines += [n.line() for n in self.nozzles()]
        lines += [s.line() for s in self.spools(with_empty=with_empty)]
        return "\n".join(lines) if lines else "the inventory is empty"


#: The one line that sets the inventory up.
SETUP = 'uv run python scripts/stock.py printer add cc1 "Elegoo Centauri Carbon"'
