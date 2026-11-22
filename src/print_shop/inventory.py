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
- **A swap's print keeps each spool in order, with the layer planned** (`print_spools`,
  mqucifer/print-shop#218), and starts only with the first loaded.
- **After it the stock says it doesn't know what's on** (`swap_not_known`): from its start
  request, and again if it's seen to start. Nothing print-shop sees clears that or moves a spool.
  Only a person's word that says both halves does, that print is over and this spool is on now
  (`load`'s `over`, and the pick at that print's outcome). A load alone records, and doesn't clear.
- **An answer counts for what it was asked about** (`swap_not_known.mark`): each time it's marked
  and each load recorded under it is a new mark, and a word given to an older one writes nothing.
- **So a job starts only on what it was prepared for** (`why_not_start`): what's loaded is
  compared again at Start, for every job, and Start holds while what's on isn't known. Another
  spool of the same variety, colour and tuning will do, and is the one recorded.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from pydantic import BaseModel

__all__ = [
    "SCHEMA",
    "Inventory",
    "InventoryError",
    "Material",
    "NotKnown",
    "NotYet",
    "Nozzle",
    "Pick",
    "Printer",
    "Spool",
    "SpoolState",
    "SpoolUse",
    "clear_not_known",
    "find_not_known",
    "mark_not_known",
    "set_loaded",
    "spool_lines",
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
    PRIMARY KEY (job_id, position)
);
CREATE TABLE IF NOT EXISTS print_swaps (
    job_id      TEXT PRIMARY KEY REFERENCES prints(job_id),
    printer_id  TEXT NOT NULL REFERENCES printers(id),
    ended_with  TEXT                             -- JSON Material: as the person said, else NULL
);
CREATE TABLE IF NOT EXISTS swap_not_known (
    printer_id  TEXT PRIMARY KEY REFERENCES printers(id),
    job_id      TEXT NOT NULL,                   -- the swap's print since which it isn't known
    mark        TEXT NOT NULL                    -- new at each marking, and each load under it
);
"""


#: How a new run takes the spool that's loaded. An insert's hold can say it, so it doesn't name
#: the option for it (mqucifer/print-shop#218: never "filament" of an insert).
OWN_CHOICE = " (leaving out any variety or tuning its command named)"


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
    note: str = ""  # what that new run needs

    def at_gate(self) -> str:
        out = (f"; or, {self.instead}, Reject and run the job again{self.note}"
               if self.instead else "")
        return f"{self.why}. {self.do}, then approve again{out}"


class SpoolUse(BaseModel):
    """One of the spools a print with a swap was to use, in order, and from which layer, as
    planned when it started (mqucifer/print-shop#218). What it ended with is the person's to
    say (`Row.ended_with`)."""

    model_config = {"frozen": True}

    material: Material
    from_layer: int | None = None
    #: The top of the last layer printed before it.
    from_z: float | None = None

    def line(self) -> str:
        at = f", from layer {self.from_layer}" if self.from_layer else ""
        if self.from_z is not None:
            at += f", over z={self.from_z:g}"
        return f"{self.material.named()}{at}"


def spool_lines(spools: tuple[SpoolUse, ...], ended_with: Material | None = None) -> list[str]:
    """A swap's spools as they're said: those planned, in order, and the one it ended with."""
    if not spools and ended_with is None:
        return []
    last = ended_with.named() if ended_with is not None else "not said"
    return [
        *(used.line() + (", as planned" if n else "") for n, used in enumerate(spools)),
        f"ended with: {last}",
    ]


class Pick(NamedTuple):
    """A spool picked as on now at a swap's outcome, and the mark it was picked under."""

    spool_id: int
    mark: str


class NotKnown(NamedTuple):
    """What's on a printer isn't known since `job_id`, a print with a swap, was sent
    (mqucifer/print-shop#218). Said without the word swap: an insert's job can be told it."""

    printer_id: str
    job_id: str
    stl_name: str
    sent_at: str
    #: The spools likely to be on, the likelier first, of those on hand.
    could_be: tuple[Material, ...] = ()
    #: Whether its outcome card can take the pick: seen to end, and not answered.
    card: bool = False
    #: Whether it was seen to start. If not, it may still begin, however sure that it's over.
    started: bool = True
    #: What a person's answer is to: it counts only while this is still the mark.
    mark: str = ""

    def print_named(self) -> str:
        sent = datetime.fromisoformat(self.sent_at).astimezone()
        return f"{self.stl_name}'s print (sent {sent:%d %b %H:%M})"

    def _over_asked(self) -> str:
        if self.started:
            return f"Is {self.print_named()} over"
        return (f"The start of {self.print_named()} was never confirmed, so it may still begin. "
                "Is it over, or cancelled on the printer so it can't begin")

    def why(self) -> str:
        return f"print-shop doesn't know what's on {self.printer_id} since {self.print_named()}"

    def over_and_on(self, named: str) -> str:
        """The question that says both halves."""
        return f"{self._over_asked()}, and is {named}, on the printer now?"

    def over_and_which(self) -> str:
        """The same, where no spool can be named in it."""
        return f"{self._over_asked()}, and which spool is on the printer now?"

    def hold(self) -> NotYet:
        lines = [f"`stock.py load {used.spool_id}` if it's {used.named()}"
                 for used in self.could_be]
        when = ("When that print's over" if self.started else
                "Its start was never confirmed, so it may still begin: when it's over, or "
                "cancelled on the printer so it can't begin")
        do = (f"{when}, say which spool is on: "
              + (", ".join(lines) or "`stock.py load <id>`") + " (it asks whether that's over)")
        if self.card:
            do += f", or pick it on {self.stl_name}'s outcome card"
        return NotYet(self.why(), do, f"{do}, and run the job again")


def find_not_known(db: sqlite3.Connection, printer_id: str) -> NotKnown | None:
    """Since which print what's on the printer isn't known, or None."""
    marked = db.execute(
        "SELECT job_id, mark FROM swap_not_known WHERE printer_id = ?", (printer_id,)
    ).fetchone()
    if marked is None:
        return None
    sent = db.execute(
        "SELECT stl_name, start_requested_at, started_at, ended_at, result FROM prints"
        " WHERE job_id = ?",
        (marked["job_id"],),
    ).fetchone()
    could_be = tuple(
        Material.model_validate_json(used["spool"]) for used in db.execute(
            "SELECT u.spool FROM print_spools u JOIN spools s ON s.id = u.spool_id"
            " WHERE u.job_id = ? AND s.state != 'empty' ORDER BY u.position DESC",
            (marked["job_id"],),
        ).fetchall()
    )
    return NotKnown(
        printer_id=printer_id, job_id=marked["job_id"], stl_name=sent["stl_name"],
        sent_at=sent["start_requested_at"], could_be=could_be,
        card=sent["ended_at"] is not None and sent["result"] is None,
        started=sent["started_at"] is not None, mark=marked["mark"],
    )


def mark_not_known(db: sqlite3.Connection, printer_id: str, job_id: str) -> None:
    """What's on the printer isn't known since this print, in the caller's transaction. The
    stock's own word isn't written over."""
    db.execute(
        "INSERT OR REPLACE INTO swap_not_known (printer_id, job_id, mark) VALUES (?, ?, ?)",
        (printer_id, job_id, uuid4().hex),
    )


def clear_not_known(db: sqlite3.Connection, printer_id: str, job_id: str) -> None:
    """A person has said that print is over and what's on, in the caller's transaction. It's
    never called on what print-shop saw."""
    db.execute(
        "DELETE FROM swap_not_known WHERE printer_id = ? AND job_id = ?", (printer_id, job_id)
    )


def set_loaded(db: sqlite3.Connection, printer_id: str, spool_id: int) -> bool:
    """A spool loaded as a person says, in the caller's transaction. False where it's empty."""
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

    def load(
        self, spool_id: int, printer_id: str | None = None, *, over: NotKnown | None = None
    ) -> Spool:
        """Load a spool, as a person says; the one loaded before on that printer goes back on
        hand. `over` is what they were asked of and said is over in the same word, and only that
        clears what isn't known (mqucifer/print-shop#218). A load alone records, and doesn't
        clear: it's a new mark, so an answer from before it doesn't write over it.

        Raises:
            InventoryError: If it's empty, or if what they were asked of has changed since: a
                start seen, another print sent, a load or another word recorded. Then nothing's
                recorded.
        """
        printer = self.printer(printer_id)
        self.spool(spool_id)
        with self._write() as db:
            # Read in the write's own transaction: a job and stock.py run side by side.
            if over is not None:
                now = find_not_known(db, printer.id)
                if now is None or now.mark != over.mark:
                    raise InventoryError(
                        f"what's on {printer.id} was said or seen otherwise since that was "
                        "asked, so nothing's recorded"
                    )
            if not set_loaded(db, printer.id, spool_id):
                raise InventoryError(f"spool #{spool_id} is empty")
            if over is not None:
                clear_not_known(db, printer.id, over.job_id)
            else:
                db.execute(
                    "UPDATE swap_not_known SET mark = ? WHERE printer_id = ?",
                    (uuid4().hex, printer.id),
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

    def unknown(self, printer_id: str) -> NotKnown | None:
        """Since which print what's on the printer isn't known, or None."""
        return find_not_known(self._conn, printer_id)

    def why_not_start(
        self, first: Material, *, not_on: int | None = None, prepared: Material | None = None,
        by_hand: bool = False,
    ) -> NotYet | None:
        """Why a job can't start with the stock as it is, or None (mqucifer/print-shop#218).

        `first` is the spool it was prepared for, or a swap's first, with `not_on` the spool
        it's swapped to. `prepared` is the job's own where a swap starts on another: its tuning
        still has to be the one the job was sliced with. `by_hand` is a file sliced by hand,
        which can't be sent again with another filament loaded.
        """
        if not_known := self.unknown(first.printer_id):
            return not_known.hold()
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
                OWN_CHOICE,
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
        instead = note = ""
        if not_on is not None:
            instead = "for other spools"
        elif now is not None:
            same = first.of(now).same_filament(first)
            if same:
                instead = f"to print in {now.colour}"
            elif not by_hand:
                instead = f"to print with {now.named()}"
                note = OWN_CHOICE
        return NotYet(
            f"{what} {first.named()}, and the stock has {has} loaded on {first.printer_id}",
            load, f"{load}, and run the job again", instead, note,
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
        for printer in self.printers():
            if found := self.unknown(printer.id):
                lines.append(f"{found.why()}. {found.hold().do}")
        return "\n".join(lines) if lines else "the inventory is empty"


#: The one line that sets the inventory up.
SETUP = 'uv run python scripts/stock.py printer add cc1 "Elegoo Centauri Carbon"'
