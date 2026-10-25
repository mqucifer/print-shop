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
  tunings to print it with, chosen once when it's added.
- **A print takes its defaults from it** (`scripts/print_job.py`): the fitted
  nozzle and the loaded spool. What isn't on hand is refused.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

__all__ = [
    "SCHEMA",
    "Inventory",
    "InventoryError",
    "Material",
    "Nozzle",
    "Printer",
    "Spool",
    "SpoolState",
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
        return (f"{self.printer_id} with a {self.nozzle_mm:g} mm nozzle; spool #{self.spool_id}, "
                f"{self.variety}, {self.colour}")


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
        """Load a spool; the one loaded before on that printer goes back on hand."""
        printer = self.printer(printer_id)
        spool = self.spool(spool_id)
        if spool.state is SpoolState.EMPTY:
            raise InventoryError(f"spool #{spool_id} is empty")
        with self._write() as db:
            db.execute(
                "UPDATE spools SET state = 'on_hand', printer_id = NULL "
                "WHERE printer_id = ? AND state = 'loaded'",
                (printer.id,),
            )
            db.execute(
                "UPDATE spools SET state = 'loaded', printer_id = ? WHERE id = ?",
                (printer.id, spool_id),
            )
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

    def listing(self, *, with_empty: bool = False) -> str:
        """Everything on hand, loaded and fitted, a line each."""
        lines = [f"printer {p.id}  {p.model}" for p in self.printers()]
        lines += [n.line() for n in self.nozzles()]
        lines += [s.line() for s in self.spools(with_empty=with_empty)]
        return "\n".join(lines) if lines else "the inventory is empty"


#: The one line that sets the inventory up.
SETUP = 'uv run python scripts/stock.py printer add cc1 "Elegoo Centauri Carbon"'
