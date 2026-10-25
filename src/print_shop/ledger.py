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

The file lives outside every checkout, at `~/print-shop/ledger.sqlite`, and is
archived nightly by mqucifer/infra#36.
"""

from __future__ import annotations

import hashlib
import json
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

SCHEMA_VERSION = 1


def ledger_path(settings: Path | None = None) -> Path:
    """Where the ledger lives: `PRINT_SHOP_LEDGER` in the settings, or the default."""
    value = load_env(settings or SETTINGS_PATH).get(PATH_KEY)
    return Path(value).expanduser() if value else DEFAULT_PATH


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Ledger:
    """The ledger file, open for the life of the process.

    One connection, shared by the daemon's threads under a lock. WAL, so the
    nightly archive can read while a print is being recorded.
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
        if version not in (0, SCHEMA_VERSION):
            raise LedgerError(
                f"{path} is schema version {version}; this code knows {SCHEMA_VERSION}"
            )
        self._conn.executescript(SCHEMA)
        self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

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
                        printer_firmware, filament, start_requested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LedgerError(f"job {record.job_id} is already in the ledger") from exc

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
            row = self._conn.execute("SELECT * FROM prints WHERE job_id = ?", (job_id,)).fetchone()
        return _row(row) if row is not None else None

    def rows(self) -> list[Row]:
        with self._lock:
            found = self._conn.execute(
                "SELECT * FROM prints ORDER BY start_requested_at, rowid"
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


def _row(row: sqlite3.Row) -> Row:
    data = dict(row)
    data["rotation_deg"] = tuple(json.loads(data["rotation_deg"]))
    data["orientation_accepted"] = bool(data["orientation_accepted"])
    data["slicer_settings"] = json.loads(data["slicer_settings"])
    del data["slicer_config"]
    return Row.model_validate(data)
