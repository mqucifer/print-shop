"""The ledger: one row for every print the agent starts, and how it went.

North Star phase 1 (mqucifer/project-print `ROADMAP.md`, M13): real prints are
logged with their settings and outcome until a symptom is seen to repeat. Only
the agent's own prints are logged, the ones it slices and starts (the Sponsor,
2026-10-24). The prototype's checkpoint holds job state, not outcomes, so this
is new.

**A row is written before the printer is told to start,** then marked started
once the printer is seen to leave its ready state. So a start that fails
halfway is still on record, with its error, and nothing the agent sent to the
printer goes unrecorded.

**An outcome is never guessed.** It is the Sponsor's answer through Home
Assistant when the print ends (mqucifer/print-shop#2). The printer's own end
state (`COMPLETED`, `STOPPED`, `ERROR`) is recorded beside it as what the
printer said, never as the outcome. A print with no answer stays open.

It follows the prototype's `Outcome` contract where it fits: an orientation is
recorded with who proposed it, why, and whether the validator accepted it.

**What a print teaches is drawn from its row** (mqucifer/print-shop#106): each
correction at the review, and an outcome with something to say, is a candidate
lesson, keyed to the part's traits (`setup/features.py`), in tables of its own
(`setup/lessons.py`). A reviewer checks each before any reaches a proposal.

The file lives outside every checkout, at `~/print-shop/ledger.sqlite`, and is
archived nightly by mqucifer/infra#36.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from print_shop.env import SETTINGS_PATH, load_env
from print_shop.inventory import SCHEMA as INVENTORY_SCHEMA
from print_shop.inventory import Material
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.lessons import SCHEMA as LESSONS_SCHEMA
from print_shop.setup.lessons import upgrade as upgrade_lessons
from print_shop.setup.proposal import Change, Proposal
from print_shop.setup.reading import Flag, SliceReading
from print_shop.slicer.store import ProfilesUsed

__all__ = [
    "DEFAULT_PATH",
    "Answer",
    "Ledger",
    "LedgerError",
    "PrintRecord",
    "Result",
    "Row",
    "Symptom",
    "ledger_path",
]

DEFAULT_PATH = Path("~/print-shop/ledger.sqlite").expanduser()

log = logging.getLogger(__name__)

#: The settings key that moves the ledger, for a test run or a second machine.
PATH_KEY = "PRINT_SHOP_LEDGER"

#: Settings promoted to their own field because they are what a symptom is
#: most often traced to. The whole config block is kept as well.
KEY_SETTINGS = (
    "printer_settings_id",
    "print_settings_id",
    "filament_settings_id",
    "filament_type",
    "nozzle_diameter",
    "layer_height",
    "wall_loops",
    "sparse_infill_density",
    "sparse_infill_pattern",
    "enable_support",
    "support_type",
    "brim_type",
    "nozzle_temperature",
    "hot_plate_temp",
    "curr_bed_type",
    "first_layer_bed_temperature",
)


class Result(StrEnum):
    """How the print came out, in the Sponsor's words."""

    SUCCESS = "success"
    FAILED = "failed"


class Symptom(StrEnum):
    """What went wrong, from a fixed list, so a repeat can be counted.

    `NONE` goes with a success. `OTHER` needs a note.
    """

    NONE = "none"
    ADHESION = "adhesion"
    WARPING = "warping"
    STRINGING = "stringing"
    LAYER_SHIFT = "layer_shift"
    SPAGHETTI = "spaghetti"
    UNDER_EXTRUSION = "under_extrusion"
    OTHER = "other"


class LedgerError(RuntimeError):
    """A write the ledger refuses: an unknown job, a second answer, or a bad pair."""


class Answer(BaseModel):
    """The Sponsor's answer at the outcome gate."""

    model_config = {"frozen": True}

    result: Result
    symptom: Symptom
    note: str = ""

    def problems(self) -> list[str]:
        """Why this answer cannot be recorded as given, if it cannot."""
        found = []
        if self.result is Result.SUCCESS and self.symptom is not Symptom.NONE:
            found.append("a success has no symptom; a flaw worth naming is a failure")
        if self.result is Result.FAILED and self.symptom is Symptom.NONE:
            found.append("a failure needs a symptom")
        if self.symptom is Symptom.OTHER and not self.note.strip():
            found.append("'other' needs a note saying what it was")
        return found


class PrintRecord(BaseModel):
    """What the agent is about to start, written before the printer is told to."""

    model_config = {"frozen": True}

    job_id: str
    stl_name: str
    stl_sha256: str
    rotation_deg: tuple[float, float, float]
    orientation_source: str
    orientation_rationale: str
    orientation_accepted: bool
    gcode_name: str
    print_time_s: int
    filament_cm3: float
    layer_count: int
    slicer_config: dict[str, str]
    printer_machine: str
    printer_firmware: str
    #: print-shop's own profiles the slice used (mqucifer/print-shop#6).
    profiles: ProfilesUsed | None = None
    #: What the part is for, the settings proposed over the presets (the
    #: Sponsor's corrections in place), and the corrections on their own:
    #: what the Sponsor would have tuned (mqucifer/print-shop#7).
    intake: Intake | None = None
    proposal: Proposal | None = None
    corrections: tuple[Change, ...] = ()
    #: The part's measured features as it printed, which its lessons are keyed to
    #: (mqucifer/print-shop#106).
    features: tuple[Feature, ...] = ()
    #: The slice that goes to the printer, read layer by layer, and what it flagged for this
    #: part (mqucifer/print-shop#105), so a lesson can be keyed to it.
    reading: SliceReading | None = None
    flags: tuple[Flag, ...] = ()


class Row(BaseModel):
    """One print as the ledger holds it."""

    job_id: str
    stl_name: str
    stl_sha256: str
    rotation_deg: tuple[float, float, float]
    orientation_source: str
    orientation_rationale: str
    orientation_accepted: bool
    gcode_name: str
    print_time_s: int
    filament_cm3: float
    layer_count: int
    slicer_settings: dict[str, str]
    slicer_config_sha256: str
    printer_machine: str
    printer_firmware: str
    filament: str
    profiles: ProfilesUsed | None
    intake: Intake | None
    proposal: Proposal | None
    corrections: tuple[Change, ...] | None  # None: from before the review existed
    #: None: from before the features were kept (mqucifer/print-shop#106).
    features: tuple[Feature, ...] | None = None
    #: The printer, nozzle and spool from the inventory (mqucifer/print-shop#11).
    material: Material | None = None
    #: The slice as read, and its flags (mqucifer/print-shop#105). None: from before, or
    #: its G-code couldn't be read.
    reading: SliceReading | None = None
    flags: tuple[Flag, ...] | None = None
    start_requested_at: str
    started_at: str | None
    start_error: str | None
    ended_at: str | None
    printer_end_status: int | None
    result: Result | None
    symptom: Symptom | None
    note: str | None
    answered_at: str | None

    @property
    def awaiting_answer(self) -> bool:
        """Ended and unanswered: the outcome gate should be asking about it."""
        return self.ended_at is not None and self.result is None


SCHEMA = """
CREATE TABLE IF NOT EXISTS prints (
    job_id                TEXT PRIMARY KEY,
    stl_name              TEXT NOT NULL,
    stl_sha256            TEXT NOT NULL,
    rotation_deg          TEXT NOT NULL,          -- JSON [x, y, z]
    orientation_source    TEXT NOT NULL,          -- "model:<alias>" | "geometric" | ...
    orientation_rationale TEXT NOT NULL,
    orientation_accepted  INTEGER NOT NULL CHECK (orientation_accepted IN (0, 1)),
    gcode_name            TEXT NOT NULL,
    print_time_s          INTEGER NOT NULL,       -- Orca's estimate
    filament_cm3          REAL NOT NULL,
    layer_count           INTEGER NOT NULL,
    slicer_settings       TEXT NOT NULL,          -- JSON, the KEY_SETTINGS
    slicer_config         TEXT NOT NULL,          -- JSON, the whole config block
    slicer_config_sha256  TEXT NOT NULL,
    printer_machine       TEXT NOT NULL,
    printer_firmware      TEXT NOT NULL,
    filament              TEXT NOT NULL,          -- the filament profile sliced with
    profiles              TEXT,                   -- JSON ProfilesUsed: store files, hashes, commit
    intake                TEXT,                   -- JSON Intake: what the part is for
    proposal              TEXT,                   -- JSON Proposal: changes over presets, why
    corrections           TEXT,                   -- JSON [Change]: the Sponsor's, at the review
    start_requested_at    TEXT NOT NULL,          -- ISO-8601 UTC, before start()
    started_at            TEXT,                   -- the printer was seen to start
    start_error           TEXT,
    ended_at              TEXT,                   -- the printer was seen to finish
    printer_end_status    INTEGER,                -- SDCP PrintInfo.Status at the end
    result                TEXT CHECK (result IN ('success', 'failed')),
    symptom               TEXT CHECK (symptom IN ('none', 'adhesion', 'warping',
                              'stringing', 'layer_shift', 'spaghetti',
                              'under_extrusion', 'other')),
    note                  TEXT,
    answered_at           TEXT,
    CHECK ((result IS NULL) = (symptom IS NULL)),
    CHECK ((result IS NULL) = (answered_at IS NULL)),
    CHECK (result IS NULL OR ended_at IS NOT NULL)
);
"""

SCHEMA_VERSION = 3

#: The slice's reading for each print (mqucifer/print-shop#105), in its own table, created if
#: missing, without moving the schema version: a print from before has no row here, and reads
#: back with no reading. The figures a lesson is most often keyed to are columns of their own.
READINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS slice_readings (
    job_id            TEXT PRIMARY KEY REFERENCES prints(job_id),
    reading           TEXT NOT NULL,          -- JSON SliceReading: layers, features, seam
    flags             TEXT NOT NULL,          -- JSON [Flag]: what it said matters, and proposed
    shortest_layer_s  REAL,                   -- the shortest layer's time
    ironing_s         REAL,                   -- the time spent ironing, 0 for none
    seam_stacked      REAL,                   -- the share of seams on the one below
    read_at           TEXT NOT NULL
);
"""

#: What each version adds to the one before, applied in order to an older file.
#: 2: the profiles a print used, by store file, hash and commit (print-shop#6).
#: A row from before has none: NULL, not a guess.
#: 3: the intake, the proposed settings and the Sponsor's corrections (print-shop#7).
#: Columns added since are in `ADDED`, each only if it's missing, without moving the version, so
#: two pieces of work can each add one (mqucifer/print-shop#106 and
#: mqucifer/print-shop#105, agreed 2026-11-13).
MIGRATIONS = {
    2: "ALTER TABLE prints ADD COLUMN profiles TEXT",
    3: "ALTER TABLE prints ADD COLUMN intake TEXT;"
    " ALTER TABLE prints ADD COLUMN proposal TEXT;"
    " ALTER TABLE prints ADD COLUMN corrections TEXT",
}


#: Columns added to `prints` if missing, by name and type. A row from before has NULL.
ADDED = (
    ("features", "TEXT"),  # JSON [Feature]: the part's features (mqucifer/print-shop#106)
)


def ledger_path(settings: Path | None = None) -> Path:
    """Where the ledger lives: `PRINT_SHOP_LEDGER` in the settings, or the default."""
    value = load_env(settings or SETTINGS_PATH).get(PATH_KEY)
    return Path(value).expanduser() if value else DEFAULT_PATH


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Ledger:
    """The ledger file, open for the life of the process.

    One connection, shared by the daemon's threads under a lock, in WAL mode.
    The nightly archive (mqucifer/infra#36) copies files rather than reading
    through SQLite, so every write is checkpointed into the main file at once:
    a copy of `ledger.sqlite` alone, without its `-wal`, holds every row.
    Writes are a handful a day, so the checkpoint costs nothing.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise LedgerError(
                f"{path} is schema version {version}; this code knows {SCHEMA_VERSION}"
            )
        if version == 0:
            self._conn.executescript(SCHEMA)
        else:
            for step in range(version + 1, SCHEMA_VERSION + 1):
                self._conn.executescript(MIGRATIONS[step])
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        held = {r["name"] for r in self._conn.execute("PRAGMA table_info(prints)")}
        for column, kind in ADDED:
            if column not in held:
                self._conn.execute(f"ALTER TABLE prints ADD COLUMN {column} {kind}")
        # The inventory's tables (mqucifer/print-shop#11) and the lessons'
        # (mqucifer/print-shop#106) live in this file too, created if missing,
        # without moving the ledger's schema version.
        self._conn.executescript(INVENTORY_SCHEMA)
        self._conn.executescript(LESSONS_SCHEMA)
        # A lesson's material, added and read from its print once (mqucifer/print-shop#117).
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            upgrade_lessons(self._conn)
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        self._conn.execute("COMMIT")
        self._conn.executescript(READINGS_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # --- writes, in the order a print goes through them ------------------

    def record_start_requested(self, record: PrintRecord) -> None:
        """Before `start()`: the agent is about to start this print."""
        config = record.slicer_config
        key = {k: config[k] for k in KEY_SETTINGS if k in config}
        config_json = json.dumps(config, sort_keys=True)
        with self._write() as db:
            try:
                db.execute(
                    """INSERT INTO prints (
                        job_id, stl_name, stl_sha256, rotation_deg, orientation_source,
                        orientation_rationale, orientation_accepted, gcode_name,
                        print_time_s, filament_cm3, layer_count, slicer_settings,
                        slicer_config, slicer_config_sha256, printer_machine,
                        printer_firmware, filament, profiles, intake, proposal,
                        corrections, features, start_requested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?)""",
                    (
                        record.job_id,
                        record.stl_name,
                        record.stl_sha256,
                        json.dumps(list(record.rotation_deg)),
                        record.orientation_source,
                        record.orientation_rationale,
                        int(record.orientation_accepted),
                        record.gcode_name,
                        record.print_time_s,
                        record.filament_cm3,
                        record.layer_count,
                        json.dumps(key, sort_keys=True),
                        config_json,
                        hashlib.sha256(config_json.encode()).hexdigest(),
                        record.printer_machine,
                        record.printer_firmware,
                        config.get("filament_settings_id", ""),
                        record.profiles.model_dump_json() if record.profiles else None,
                        record.intake.model_dump_json() if record.intake else None,
                        record.proposal.model_dump_json() if record.proposal else None,
                        json.dumps([c.model_dump(mode="json") for c in record.corrections]),
                        json.dumps([f.model_dump(mode="json") for f in record.features])
                        if record.features else None,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LedgerError(f"job {record.job_id} is already in the ledger") from exc
            material = record.profiles.material if record.profiles else None
            if material is not None:
                db.execute(
                    "INSERT INTO print_material (job_id, printer_id, nozzle_mm, spool_id, spool)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (record.job_id, material.printer_id, material.nozzle_mm,
                     material.spool_id, material.model_dump_json()),
                )
            if record.reading is not None:
                reading = record.reading
                shortest = reading.shortest()
                ironing = reading.feature("Ironing")
                db.execute(
                    "INSERT INTO slice_readings (job_id, reading, flags, shortest_layer_s,"
                    " ironing_s, seam_stacked, read_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (record.job_id, reading.model_dump_json(),
                     json.dumps([f.model_dump(mode="json") for f in record.flags]),
                     shortest.time_s if shortest else None,
                     ironing.time_s if ironing else 0.0,
                     reading.seam.stacked, _now()),
                )
        self._learn(record.job_id)

    def record_started(self, job_id: str) -> None:
        """The printer was seen to leave its ready state for this job."""
        self._update(job_id, "started_at = ?", (_now(),), "started_at IS NULL")

    def record_start_failed(self, job_id: str, error: str) -> None:
        """The start raised. The printer may or may not be printing; say so."""
        self._update(job_id, "start_error = ?", (error[:2000],), "started_at IS NULL")

    def record_ended(self, job_id: str, printer_end_status: int | None) -> None:
        """The printer was seen to finish, one way or another. Not the outcome."""
        self._update(
            job_id,
            "ended_at = ?, printer_end_status = ?",
            (_now(), printer_end_status),
            "ended_at IS NULL",
        )

    def record_answer(self, job_id: str, answer: Answer) -> None:
        """The Sponsor's answer. Once: a second answer is refused, not merged."""
        problems = answer.problems()
        if problems:
            raise LedgerError("; ".join(problems))
        self._update(
            job_id,
            "result = ?, symptom = ?, note = ?, answered_at = ?",
            (answer.result.value, answer.symptom.value, answer.note, _now()),
            "ended_at IS NOT NULL AND result IS NULL",
        )
        self._learn(job_id)

    def _learn(self, job_id: str) -> None:
        """What this print's row teaches: candidates from its corrections and outcome, and a
        score for each lesson it was given (`lessons.after_print`). Never in the way of a
        print: a failure here is logged, and `scripts/print_lessons.py draw` does it again."""
        from print_shop.setup import lessons  # noqa: PLC0415 - lessons reads the ledger

        try:
            lessons.after_print(self, job_id)
        except Exception:  # noqa: BLE001 - a print is never stopped by its lessons
            log.exception("drawing lessons from job %s failed; draw them again by hand", job_id)

    # --- the lessons' own tables (`setup/lessons.py`) -----------------------

    def query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        """Rows of any table in this file, as dicts."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    @contextmanager
    def writing(self) -> Iterator[sqlite3.Connection]:
        """One transaction, for writes to the lessons' tables."""
        with self._write() as db:
            yield db

    def _update(self, job_id: str, assignments: str, values: tuple[Any, ...], guard: str) -> None:
        with self._write() as db:
            cursor = db.execute(
                f"UPDATE prints SET {assignments} WHERE job_id = ? AND {guard}",
                (*values, job_id),
            )
            if cursor.rowcount == 1:
                return
            exists = db.execute("SELECT 1 FROM prints WHERE job_id = ?", (job_id,)).fetchone()
        if not exists:
            raise LedgerError(f"job {job_id} is not in the ledger")
        raise LedgerError(f"job {job_id} is not in a state for that ({guard})")

    # --- reads -----------------------------------------------------------

    def get(self, job_id: str) -> Row | None:
        with self._lock:
            row = self._conn.execute(_SELECT + " WHERE p.job_id = ?", (job_id,)).fetchone()
        return _row(row) if row is not None else None

    def rows(self) -> list[Row]:
        with self._lock:
            found = self._conn.execute(
                _SELECT + " ORDER BY p.start_requested_at, p.rowid"
            ).fetchall()
        return [_row(r) for r in found]

    def running(self) -> list[Row]:
        """Started and not yet seen to end: the prints the daemon must watch."""
        return [r for r in self.rows() if r.started_at is not None and r.ended_at is None]

    def awaiting_answer(self) -> list[Row]:
        """Ended and unanswered: the prints the outcome gate must ask about."""
        return [r for r in self.rows() if r.awaiting_answer]

    def unconfirmed_starts(self) -> list[Row]:
        """A start that raised and was never seen to begin. It may be printing."""
        return [
            r
            for r in self.rows()
            if r.start_error is not None and r.started_at is None and r.ended_at is None
        ]


#: A print, and what it used from the inventory if it did.
_SELECT = (
    "SELECT p.*, m.spool AS material, r.reading AS reading, r.flags AS flags FROM prints p "
    "LEFT JOIN print_material m ON m.job_id = p.job_id "
    "LEFT JOIN slice_readings r ON r.job_id = p.job_id"
)


def _row(row: sqlite3.Row) -> Row:
    data = dict(row)
    if data["material"] is not None:
        data["material"] = Material.model_validate_json(data["material"])
    data["rotation_deg"] = tuple(json.loads(data["rotation_deg"]))
    data["orientation_accepted"] = bool(data["orientation_accepted"])
    data["slicer_settings"] = json.loads(data["slicer_settings"])
    if data["profiles"] is not None:
        data["profiles"] = ProfilesUsed.model_validate_json(data["profiles"])
    if data["intake"] is not None:
        data["intake"] = Intake.model_validate_json(data["intake"])
    if data["proposal"] is not None:
        data["proposal"] = Proposal.model_validate_json(data["proposal"])
    if data["corrections"] is not None:
        found = json.loads(data["corrections"])
        data["corrections"] = tuple(Change.model_validate(c) for c in found)
    if data.get("features") is not None:
        data["features"] = tuple(Feature.model_validate(f) for f in json.loads(data["features"]))
    if data["reading"] is not None:
        data["reading"] = SliceReading.model_validate_json(data["reading"])
    if data["flags"] is not None:
        data["flags"] = tuple(Flag.model_validate(f) for f in json.loads(data["flags"]))
    del data["slicer_config"]
    return Row.model_validate(data)
