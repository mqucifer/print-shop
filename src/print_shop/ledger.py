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

**A job that ends without printing because a wait ran out** has a row of its own, in
`unprinted_jobs`, whose outcome says it timed out (mqucifer/print-shop#125). Nothing was sent to
the printer, so it's no print, and it never reaches the outcome gate or a lesson.

**A job of several parts has a row for each object** (mqucifer/print-shop#109), each with a row of
`plate_objects` beside it, so each part's outcome is its own answer. They're one print: started,
failed to start and ended together, with one set of frames, and watched by the first's row, which
has the job's id. Orca's time, filament and layers in each are the whole plate's.

**A print with a swap keeps each spool in order, with the layer planned**
(mqucifer/print-shop#218), in `print_spools` beside its `print_material`, which stays the spool
it started on. A row from before has none, and reads back as it was. From its start request, and
again when it's first seen to start, what's on the printer isn't known. An end never clears that
and never moves the stock. The answer at its outcome does where the person picks the spool on,
in the same transaction, and that's the spool it ended with (`print_swaps`): else it's not said.

**A hand-sliced file is kept like any other print** (mqucifer/print-shop#164), with a row of
`sliced_prints` beside it: marked `sliced: by hand`, with the file's own settings by profile,
what differs from the inventory's tuning, each object's overrides, Orca's estimate, and the job
it prints again, if it's a reprint. Nothing in it was chosen by the loop, so it has no intake,
proposal or corrections, and no lesson is given to it.

**A finished run printed again** (mqucifer/print-shop#216) is kept the same way, marked `again`,
its `reprint_of` the run it came from, and its record says what was changed, how its slice
differed from that run's and what was suggested. Each print keeps the sha256 of the G-code it
sent (`gcode_sha256`), which a print again is checked by; a row from before has none.

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

from print_shop.arming import Armed
from print_shop.env import SETTINGS_PATH, load_env, refused
from print_shop.inventory import SCHEMA as INVENTORY_SCHEMA
from print_shop.inventory import (
    Material,
    NotKnown,
    Pick,
    SpoolUse,
    clear_not_known,
    find_not_known,
    mark_not_known,
    set_loaded,
    stock_changes,
)
from print_shop.plate import PlateRow, plate_job
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.lessons import SCHEMA as LESSONS_SCHEMA
from print_shop.setup.lessons import upgrade as upgrade_lessons
from print_shop.setup.pauses import PauseFor
from print_shop.setup.proposal import PAUSE, Change, Proposal
from print_shop.setup.reading import Flag, SliceReading
from print_shop.sliced import SlicedRecord
from print_shop.slicer.store import ProfilesUsed

__all__ = [
    "DEFAULT_PATH",
    "Answer",
    "Frame",
    "JobEnding",
    "Ledger",
    "LedgerError",
    "PrintRecord",
    "Result",
    "Row",
    "Symptom",
    "UnprintedJob",
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
    """How the print came out, in the Sponsor's words: whether the part is usable."""

    SUCCESS = "success"
    FAILED = "failed"


class Symptom(StrEnum):
    """What went wrong, from a fixed list, so a repeat can be counted.

    On a success it's a minor flaw on a usable part (mqucifer/print-shop#189). `NONE` goes
    only with a success. `OTHER` needs a note, whatever the result.
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


class SaidSince(LedgerError):
    """A pick at a swap's outcome that a later word of the person's has overtaken."""


class Answer(BaseModel):
    """The Sponsor's answer at the outcome gate."""

    model_config = {"frozen": True}

    result: Result
    symptom: Symptom
    note: str = ""

    def problems(self) -> list[str]:
        """Why this answer cannot be recorded as given, if it cannot."""
        found = []
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
    #: A hand-sliced file's own record (mqucifer/print-shop#164): None for a job the shop sliced.
    sliced: SlicedRecord | None = None
    #: The printer, nozzle and spool from the inventory, for a print with no store profiles to
    #: carry them: a hand-sliced one. Else it's `profiles.material`.
    material: Material | None = None
    #: The plate it's an object of, for a job of several parts (mqucifer/print-shop#109).
    plate: PlateRow | None = None
    #: A swap's spools, in order, each with the layer planned (mqucifer/print-shop#218).
    spools: tuple[SpoolUse, ...] = ()
    #: The sha256 of the G-code sent, which a reprint of it is checked by
    #: (mqucifer/print-shop#216). A hand-sliced file's is also its record's.
    gcode_sha256: str = ""

    def has_swap(self) -> bool:
        """Whether it pauses for a swap, however many spools of it could be recorded."""
        change = self.proposal.get(PAUSE) if self.proposal is not None else None
        return bool(self.spools) or (
            change is not None and change.value != "none" and change.pause is not None
            and change.pause.kind is PauseFor.SWAP
        )


class Frame(BaseModel):
    """One camera frame of a print: evidence beside its outcome, never the outcome."""

    model_config = {"frozen": True}

    kind: str  # "layer", taken as the printer moved to a layer, or "end"
    layer: int | None
    total_layers: int | None
    path: str
    bytes: int
    taken_at: str

    def line(self) -> str:
        at = f"layer {self.layer}" + (f" of {self.total_layers}" if self.total_layers else "")
        return ("the end, " + at if self.kind == "end" else at) if self.layer else self.kind


class JobEnding(StrEnum):
    """How a job ended with nothing printed, when the ledger keeps it (mqucifer/print-shop#125)."""

    TIMED_OUT = "timed_out"


class UnprintedJob(BaseModel):
    """A job that ended before anything was sent to the printer, and why, in one sentence."""

    model_config = {"frozen": True}

    job_id: str
    stl_name: str
    outcome: JobEnding
    detail: str
    ended_at: str

    def line(self) -> str:
        return f"job {self.job_id}  {self.stl_name}  {self.outcome}: {self.detail}"


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
    #: A swap's spools, in order, each with the layer planned (mqucifer/print-shop#218). ():
    #: no swap was recorded, as in every row from before.
    spools: tuple[SpoolUse, ...] = ()
    #: The spool a swap's print ended with, as the person said. None: not said.
    ended_with: Material | None = None
    #: The slice as read, and its flags (mqucifer/print-shop#105). None: from before, or
    #: its G-code couldn't be read.
    reading: SliceReading | None = None
    flags: tuple[Flag, ...] | None = None
    #: A hand-sliced file's own record (mqucifer/print-shop#164). None: the shop sliced it.
    sliced: SlicedRecord | None = None
    #: The plate it's an object of, with the others (mqucifer/print-shop#109). None: a job's
    #: only part.
    plate: PlateRow | None = None
    #: The sha256 of the G-code sent (mqucifer/print-shop#216). None: from before it was kept,
    #: where only a hand-sliced file's record has it.
    gcode_sha256: str | None = None
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
    def heads_job(self) -> bool:
        """The row its job is watched by: its only one, or a plate's first."""
        return self.plate is None or self.plate.first

    @property
    def part_said(self) -> str:
        """The part as a list names it: by its file, and on a plate its number too."""
        return self.stl_name if self.plate is None else self.plate.short()

    @property
    def awaiting_answer(self) -> bool:
        """Ended and unanswered: the outcome gate should be asking about it."""
        return self.ended_at is not None and self.result is None

    @property
    def real_time_s(self) -> int | None:
        """How long the print took, from when it was seen to start to when it was seen to end:
        heating and any pause included, to the watcher's poll. None until both are seen."""
        if self.started_at is None or self.ended_at is None:
            return None
        took = datetime.fromisoformat(self.ended_at) - datetime.fromisoformat(self.started_at)
        return int(took.total_seconds())


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

#: The camera's frames of each print (mqucifer/print-shop#110), one row a frame, in a table of
#: their own created if missing, without moving the schema version. The JPEGs are files in the
#: job's folder; the ledger keeps where, at which layer, and when.
FRAMES_SCHEMA = """
CREATE TABLE IF NOT EXISTS print_frames (
    job_id        TEXT NOT NULL REFERENCES prints(job_id),
    kind          TEXT NOT NULL CHECK (kind IN ('layer', 'end')),
    layer         INTEGER,                -- the layer the printer said it was on, if it said
    total_layers  INTEGER,
    path          TEXT NOT NULL,          -- the JPEG, under jobs/<id>/frames/
    bytes         INTEGER NOT NULL,
    taken_at      TEXT NOT NULL,
    PRIMARY KEY (job_id, path)
);
"""

#: Why a print has no frame, or is missing some: the last grab's reason, or that the camera is
#: off (mqucifer/print-shop#123), so the outcome step can say it. A table of its own, created if
#: missing, as the frames' is.
FRAMES_MISSED_SCHEMA = """
CREATE TABLE IF NOT EXISTS print_frames_missed (
    job_id   TEXT PRIMARY KEY REFERENCES prints(job_id),
    reason   TEXT NOT NULL,                -- one line: why the last grab gave no frame
    at       TEXT NOT NULL
);
"""

#: Jobs that ended with nothing sent to the printer because a wait ran out
#: (mqucifer/print-shop#125). Not prints, so not in `prints`, whose rows are what the agent
#: started; a table of their own, created if missing.
UNPRINTED_SCHEMA = """
CREATE TABLE IF NOT EXISTS unprinted_jobs (
    job_id    TEXT PRIMARY KEY,
    stl_name  TEXT NOT NULL,
    outcome   TEXT NOT NULL CHECK (outcome IN ('timed_out')),
    detail    TEXT NOT NULL,               -- one sentence: what ran out, after how long
    ended_at  TEXT NOT NULL
);
"""

#: A hand-sliced print's own record (mqucifer/print-shop#164), in a table of its own, created if
#: missing, without moving the schema version: a print the shop sliced has no row here. What a
#: reprint is found by, and what a reader asks first, are columns; the rest is the record.
SLICED_SCHEMA = """
CREATE TABLE IF NOT EXISTS sliced_prints (
    job_id        TEXT PRIMARY KEY REFERENCES prints(job_id),
    sliced        TEXT NOT NULL,           -- 'by hand': nothing here was chosen by the loop
    gcode_sha256  TEXT NOT NULL,           -- of the bytes sent
    reprint_of    TEXT,                    -- the job this one prints again
    record        TEXT NOT NULL            -- JSON SlicedRecord: settings, differences, overrides
);
"""

#: Each object of a job of several parts (mqucifer/print-shop#109), in a table of its own, created
#: if missing, without moving the schema version: a job's only part has no row here. `plate_job`
#: is the first object's row, which the others are started and ended with.
PLATES_SCHEMA = """
CREATE TABLE IF NOT EXISTS plate_objects (
    job_id     TEXT PRIMARY KEY REFERENCES prints(job_id),
    plate_job  TEXT NOT NULL REFERENCES prints(job_id),
    n          INTEGER NOT NULL,        -- the part's number on the plate, from 1, as given
    record     TEXT NOT NULL,           -- JSON PlateRow: the parts, and the plate as sliced
    UNIQUE (plate_job, n)
);
"""

#: Who armed each start, how, from where and when (mqucifer/print-shop#214), in a table of its own,
#: created if missing, without moving the schema version. Written in the same transaction as the
#: print's row, so a row started from now on never lacks it; a row from before has none here.
#: A plate's is its first object's row's.
ARMED_SCHEMA = """
CREATE TABLE IF NOT EXISTS armed_starts (
    job_id       TEXT PRIMARY KEY REFERENCES prints(job_id),
    how          TEXT NOT NULL CHECK (how IN ('phone', 'flag')),
    armed_by     TEXT NOT NULL,           -- the phone's name, as paired, or the flag
    armed_from   TEXT NOT NULL,           -- the phone's address on the home Wi-Fi, or the Mac
    armed_at     TEXT NOT NULL            -- UTC, when the person armed it
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
    # The sha256 of the G-code sent, which a reprint is checked by (mqucifer/print-shop#216).
    ("gcode_sha256", "TEXT"),
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
        refused(path, "a ledger")  # under tests, never the Mac's (mqucifer/print-shop#263)
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
        self._conn.executescript(FRAMES_SCHEMA)
        self._conn.executescript(FRAMES_MISSED_SCHEMA)
        self._conn.executescript(UNPRINTED_SCHEMA)
        self._conn.executescript(SLICED_SCHEMA)
        self._conn.executescript(PLATES_SCHEMA)
        self._conn.executescript(ARMED_SCHEMA)
        self._has_sliced = self._has_swaps = True
        self._has_plates = True

    @classmethod
    def read_only(cls, path: Path) -> Ledger:
        """The file opened read-only, for a reader that must change nothing: the print desk
        (mqucifer/print-shop#139). Nothing is made, added or upgraded, as opening it to write
        does; SQLite itself refuses any write (`mode=ro`). Every read below works on it.

        Raises:
            LedgerError: If there's no ledger at `path`, or it's newer than this code.
        """
        refused(path, "a ledger")  # under tests, never the Mac's (mqucifer/print-shop#263)
        if not path.is_file():
            raise LedgerError(f"there's no ledger at {path}")
        self = cls.__new__(cls)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=5000")
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            self._conn.close()
            raise LedgerError(
                f"{path} is schema version {version}; this code knows {SCHEMA_VERSION}"
            )
        # A file last written before mqucifer/print-shop#164 has no `sliced_prints`, and nothing
        # is made here: its prints read back with no sliced record.
        self._has_sliced = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sliced_prints'"
        ).fetchone() is not None
        # Nor `plate_objects`, before mqucifer/print-shop#109: each print was a job's only part.
        self._has_plates = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'plate_objects'"
        ).fetchone() is not None
        # The same for a file from before mqucifer/print-shop#218, which has no swaps' tables.
        self._has_swaps = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'swap_not_known'"
        ).fetchone() is not None
        return self

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

    def record_start_requested(
        self, record: PrintRecord, *others: PrintRecord, loaded: Material | None = None,
        armed: Armed | None = None,
    ) -> None:
        """Before `start()`: the agent is about to start this print. `others` are the plate's
        other objects (mqucifer/print-shop#109), written with it or not at all.

        Refused for a job that already ended unprinted (mqucifer/print-shop#125): the person was
        told nothing would print, so nothing does. And, in the same transaction as the row,
        unless `loaded`, the spool it was checked to start on, is still the one loaded and known
        to be. With a swap, what's on the printer isn't known from this request, whatever comes
        of it (mqucifer/print-shop#218).

        `armed` is who armed this start, how, from where and when (mqucifer/print-shop#214),
        kept with the row in the same transaction.
        """
        with self._write() as db:
            ended = db.execute(
                "SELECT detail FROM unprinted_jobs WHERE job_id = ?", (record.job_id,)
            ).fetchone()
            if ended is not None:
                raise LedgerError(
                    f"job {record.job_id} already ended unprinted ({ended['detail']}); it isn't "
                    "started now"
                )
            if loaded is not None:
                on = db.execute(
                    "SELECT id, tuning FROM spools WHERE printer_id = ? AND state = 'loaded'",
                    (loaded.printer_id,),
                ).fetchone()
                not_known = find_not_known(db, loaded.printer_id)
                if on is None or not_known or (
                    (on["id"], on["tuning"]) != (loaded.spool_id, loaded.tuning)
                ):
                    raise LedgerError(
                        "what's loaded changed as the print was to start, so the file was "
                        "uploaded, and not started. Run the job again."
                    )
            at = _now()
            for each in (record, *others):
                self._insert(db, each, at)
            if armed is not None:
                db.execute(
                    "INSERT INTO armed_starts (job_id, how, armed_by, armed_from, armed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (record.job_id, armed.how, armed.by, armed.where, armed.at),
                )
            # A plate is one print: what's loaded is marked not known once, by its first row,
            # the one its watcher and the first outcome card name (mqucifer/print-shop#218).
            material = record.material or (
                record.profiles.material if record.profiles else None)
            if material is not None and record.has_swap():
                db.execute(
                    "INSERT INTO print_swaps (job_id, printer_id) VALUES (?, ?)",
                    (record.job_id, material.printer_id),
                )
                mark_not_known(db, material.printer_id, record.job_id)
        for each in (record, *others):
            self._learn(each.job_id)

    def _insert(self, db: sqlite3.Connection, record: PrintRecord, at: str) -> None:
        """One print's row, with what's kept beside it."""
        config = record.slicer_config
        key = {k: config[k] for k in KEY_SETTINGS if k in config}
        config_json = json.dumps(config, sort_keys=True)
        try:
            db.execute(
                """INSERT INTO prints (
                    job_id, stl_name, stl_sha256, rotation_deg, orientation_source,
                    orientation_rationale, orientation_accepted, gcode_name,
                    print_time_s, filament_cm3, layer_count, slicer_settings,
                    slicer_config, slicer_config_sha256, printer_machine,
                    printer_firmware, filament, profiles, intake, proposal,
                    corrections, features, gcode_sha256, start_requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?)""",
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
                    record.gcode_sha256 or (
                        record.sliced.gcode_sha256 if record.sliced is not None else None),
                    at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"job {record.job_id} is already in the ledger") from exc
        material = record.material or (
            record.profiles.material if record.profiles else None)
        if material is not None:
            db.execute(
                "INSERT INTO print_material (job_id, printer_id, nozzle_mm, spool_id, spool)"
                " VALUES (?, ?, ?, ?, ?)",
                (record.job_id, material.printer_id, material.nozzle_mm,
                 material.spool_id, material.model_dump_json()),
            )
        for position, used in enumerate(record.spools):
            db.execute(
                "INSERT INTO print_spools (job_id, position, spool_id, from_layer, from_z,"
                " spool) VALUES (?, ?, ?, ?, ?, ?)",
                (record.job_id, position, used.material.spool_id, used.from_layer,
                 used.from_z, used.material.model_dump_json()),
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
        if record.sliced is not None:
            db.execute(
                "INSERT INTO sliced_prints (job_id, sliced, gcode_sha256, reprint_of, record)"
                " VALUES (?, ?, ?, ?, ?)",
                (record.job_id, record.sliced.sliced, record.sliced.gcode_sha256,
                 record.sliced.reprint_of, record.sliced.model_dump_json()),
            )
        if record.plate is not None:
            db.execute(
                "INSERT INTO plate_objects (job_id, plate_job, n, record) VALUES (?, ?, ?, ?)",
                (record.job_id, record.plate.job_id, record.plate.n,
                 record.plate.model_dump_json()),
            )

    def record_started(self, job_id: str) -> None:
        """The printer was seen to leave its ready state for this job: all its objects. The
        first time seen stands: the watcher can see it before the print node has finished
        confirming it, and that's no failure of the start (mqucifer/print-shop#218). The first
        time, a swap's makes what's on not known again: a person can truly have said it was over,
        and then it began. A plate's swap is its first row's.
        """
        first = plate_job(job_id)
        with self._write() as db:
            before = db.execute(
                "SELECT started_at FROM prints WHERE job_id = ?", (first,)
            ).fetchone()
            self._updated(db, job_id, "started_at = COALESCE(started_at, ?)", (_now(),), "1 = 1",
                          whole_job=True)
            if before is None or before["started_at"] is not None:
                return
            swap = db.execute(
                "SELECT printer_id FROM print_swaps WHERE job_id = ?", (first,)
            ).fetchone()
            if swap is not None:
                mark_not_known(db, swap["printer_id"], first)

    def record_start_failed(self, job_id: str, error: str) -> None:
        """The start raised. The printer may or may not be printing; say so, and where the
        watcher has seen it printing, say so all the same. All its objects."""
        self._update(job_id, "start_error = ?", (error[:2000],), "1 = 1", whole_job=True)

    def record_ended(self, job_id: str, printer_end_status: int | None) -> None:
        """The printer was seen to finish, one way or another. Not the outcome, and no word on
        what's loaded (mqucifer/print-shop#218)."""
        self._update(
            job_id,
            "ended_at = ?, printer_end_status = ?",
            (_now(), printer_end_status),
            "ended_at IS NULL",
            whole_job=True,
        )

    def record_answer(self, job_id: str, answer: Answer, on: Pick | None = None) -> None:
        """The Sponsor's answer. Once: a second answer is refused, not merged.

        `on` is the spool they picked at a swap's outcome, in words that say both: that print is
        over, and this one is on now. It's loaded, what isn't known is cleared, and it's the
        spool the print ended with, written with the answer or none of them
        (mqucifer/print-shop#218).
        """
        problems = answer.problems()
        if problems:
            raise LedgerError("; ".join(problems))
        with self._write() as db:
            self._updated(
                db, job_id,
                "result = ?, symptom = ?, note = ?, answered_at = ?",
                (answer.result.value, answer.symptom.value, answer.note, _now()),
                "ended_at IS NOT NULL AND result IS NULL",
            )
            if on is not None:
                _said_on(db, job_id, on)
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

    def _update(
        self, job_id: str, assignments: str, values: tuple[Any, ...], guard: str,
        *, whole_job: bool = False,
    ) -> None:
        """One row's change, or with `whole_job` every object's of its job, all or none: a
        plate is one print. An answer is always one row's."""
        with self._write() as db:
            self._updated(db, job_id, assignments, values, guard, whole_job=whole_job)

    @staticmethod
    def _updated(
        db: sqlite3.Connection, job_id: str, assignments: str, values: tuple[Any, ...], guard: str,
        *, whole_job: bool = False,
    ) -> None:
        """`_update`'s, in the caller's transaction."""
        which = "job_id = ?"
        named: tuple[str, ...] = (job_id,)
        if whole_job:
            which = ("(job_id = ? OR job_id IN"
                     " (SELECT job_id FROM plate_objects WHERE plate_job = ?))")
            named = (plate_job(job_id),) * 2
        cursor = db.execute(
            f"UPDATE prints SET {assignments} WHERE {which} AND {guard}",
            (*values, *named),
        )
        if cursor.rowcount >= 1:
            return
        if not db.execute("SELECT 1 FROM prints WHERE job_id = ?", (job_id,)).fetchone():
            raise LedgerError(f"job {job_id} is not in the ledger")
        raise LedgerError(f"job {job_id} is not in a state for that ({guard})")

    def record_frame(
        self, job_id: str, kind: str, layer: int | None, total_layers: int | None, path: Path,
        size: int,
    ) -> None:
        """A frame saved for a print. The same file twice is kept once."""
        with self._write() as db:
            db.execute(
                "INSERT OR IGNORE INTO print_frames (job_id, kind, layer, total_layers, path,"
                " bytes, taken_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, kind, layer, total_layers, str(path), size, _now()),
            )

    def record_frames_missed(self, job_id: str, reason: str) -> None:
        """Why the last grab for a print gave no frame. A later reason replaces it."""
        with self._write() as db:
            db.execute(
                "INSERT INTO print_frames_missed (job_id, reason, at) VALUES (?, ?, ?)"
                " ON CONFLICT (job_id) DO UPDATE SET reason = excluded.reason, at = excluded.at",
                (job_id, reason[:500], _now()),
            )

    def record_unprinted(
        self, job_id: str, stl_name: str, outcome: JobEnding, detail: str
    ) -> UnprintedJob:
        """A job that ended before anything was sent to the printer. Once a job."""
        with self._write() as db:
            if db.execute("SELECT 1 FROM prints WHERE job_id = ?", (job_id,)).fetchone():
                raise LedgerError(f"job {job_id} was sent to the printer; it's a print")
            db.execute(
                "INSERT OR IGNORE INTO unprinted_jobs (job_id, stl_name, outcome, detail,"
                " ended_at) VALUES (?, ?, ?, ?, ?)",
                (job_id, stl_name, str(outcome), detail, _now()),
            )
        found = self.unprinted(job_id)
        assert found is not None
        return found

    # --- reads -----------------------------------------------------------

    def frames_missed(self, job_id: str) -> str | None:
        """Why the last grab for a print gave no frame, if one didn't."""
        with self._lock:
            found = self._conn.execute(
                "SELECT reason FROM print_frames_missed WHERE job_id = ?", (plate_job(job_id),)
            ).fetchone()
        return str(found["reason"]) if found is not None else None

    def unprinted(self, job_id: str) -> UnprintedJob | None:
        """The job, if it ended before anything was sent to the printer."""
        with self._lock:
            found = self._conn.execute(
                "SELECT * FROM unprinted_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return UnprintedJob.model_validate(dict(found)) if found is not None else None

    def armed(self, job_id: str) -> Armed | None:
        """Who armed the job's start, how, from where and when (mqucifer/print-shop#214); None
        for a print from before that was kept. An object of a plate's is its plate's."""
        with self._lock:
            found = self._conn.execute(
                "SELECT job_id, how, armed_by, armed_from, armed_at FROM armed_starts"
                " WHERE job_id = ?", (plate_job(job_id),),
            ).fetchone()
        if found is None:
            return None
        return Armed(job_id=found["job_id"], how=found["how"], by=found["armed_by"],
                     where=found["armed_from"], at=found["armed_at"])

    def unprinted_jobs(self) -> list[UnprintedJob]:
        """Every job that ended unprinted, oldest first."""
        with self._lock:
            found = self._conn.execute(
                "SELECT * FROM unprinted_jobs ORDER BY ended_at, rowid"
            ).fetchall()
        return [UnprintedJob.model_validate(dict(r)) for r in found]

    def frames(self, job_id: str) -> list[Frame]:
        """A print's frames in the order they were taken, the end's last. An object's are its
        plate's."""
        with self._lock:
            found = self._conn.execute(
                "SELECT kind, layer, total_layers, path, bytes, taken_at FROM print_frames"
                " WHERE job_id = ? ORDER BY kind = 'end', taken_at, layer",
                (plate_job(job_id),),
            ).fetchall()
        return [Frame.model_validate(dict(r)) for r in found]

    @property
    def _select(self) -> str:
        """A print with what's kept beside it; its sliced record where the file has the table."""
        sliced, plate, join = "NULL", "NULL", ""
        if self._has_sliced:
            sliced, join = "s.record", _SLICED_JOIN
        if self._has_plates:
            plate, join = "o.record", join + _PLATE_JOIN
        return _SELECT.format(sliced=sliced, plate=plate, join=join)

    def get(self, job_id: str) -> Row | None:
        with self._lock:
            row = self._conn.execute(self._select + " WHERE p.job_id = ?", (job_id,)).fetchone()
            swaps = {**self._swaps(job_id), **self._swaps(plate_job(job_id))}
        return _row(row, *_swapped(job_id, swaps)) if row is not None else None

    def rows(self) -> list[Row]:
        with self._lock:
            found = self._conn.execute(
                self._select + " ORDER BY p.start_requested_at, p.rowid"
            ).fetchall()
            swaps = self._swaps()
        return [_row(r, *_swapped(r["job_id"], swaps)) for r in found]

    def _swaps(
        self, job_id: str | None = None
    ) -> dict[str, tuple[tuple[SpoolUse, ...], Material | None]]:
        """Each swap's spools as planned, and the one it ended with where that's said, or one
        print's. The lock is the caller's."""
        if not self._has_swaps:
            return {}
        where, args = (" WHERE job_id = ?", (job_id,)) if job_id is not None else ("", ())
        planned: dict[str, tuple[SpoolUse, ...]] = {}
        for r in self._conn.execute(
            f"SELECT * FROM print_spools{where} ORDER BY job_id, position", args,
        ):
            planned[r["job_id"]] = (*planned.get(r["job_id"], ()), SpoolUse(
                material=Material.model_validate_json(r["spool"]),
                from_layer=r["from_layer"], from_z=r["from_z"],
            ))
        ended = {
            r["job_id"]: Material.model_validate_json(r["ended_with"])
            for r in self._conn.execute(f"SELECT * FROM print_swaps{where}", args)
            if r["ended_with"]
        }
        return {job: (planned.get(job, ()), ended.get(job)) for job in planned | ended}

    def swaps_unseen(self) -> list[Row]:
        """Swaps' prints sent and never seen to start, killed while confirming or however long
        ago, that could still begin: none once a later print has started. Seen printing by its
        own file, one is only made not known again (mqucifer/print-shop#218)."""
        if not self._has_swaps:
            return []
        with self._lock:
            found = {r["job_id"] for r in self._conn.execute(
                "SELECT p.job_id FROM print_swaps w JOIN prints p ON p.job_id = w.job_id"
                " WHERE p.started_at IS NULL AND p.ended_at IS NULL AND NOT EXISTS ("
                "  SELECT 1 FROM prints later WHERE later.started_at IS NOT NULL"
                "  AND (later.start_requested_at > p.start_requested_at"
                "       OR (later.start_requested_at = p.start_requested_at"
                "           AND later.rowid > p.rowid)))"
            )}
        return [row for row in self.rows() if row.job_id in found] if found else []

    def not_known(self, printer_id: str, job_id: str | None = None) -> NotKnown | None:
        """Since which print what's on the printer isn't known, the latest marked or `job_id`'s,
        or None."""
        with self._lock:
            return find_not_known(self._conn, printer_id, job_id) if self._has_swaps else None

    def job_rows(self, job_id: str) -> list[Row]:
        """Every row of the job `job_id` is a row of: a plate's objects in their order, or the
        one print. Empty where there's none."""
        job = plate_job(job_id)
        if not self._has_plates:
            row = self.get(job)
            return [row] if row is not None else []
        with self._lock:
            found = self._conn.execute(
                self._select + " WHERE p.job_id = ? OR o.plate_job = ?"
                " ORDER BY COALESCE(o.n, 1)", (job, job)
            ).fetchall()
            swaps = self._swaps()
        return [_row(r, *_swapped(r["job_id"], swaps)) for r in found]

    def config(self, job_id: str) -> dict[str, str] | None:
        """The whole config block of the G-code a print sent, as kept, or None where there's no
        such print (mqucifer/print-shop#216)."""
        with self._lock:
            found = self._conn.execute(
                "SELECT slicer_config FROM prints WHERE job_id = ?", (job_id,)).fetchone()
        return dict(json.loads(found["slicer_config"])) if found is not None else None

    def reprints(self, job_id: str) -> list[str]:
        """The jobs that print `job_id`'s file again, oldest first."""
        if not self._has_sliced:
            return []
        with self._lock:
            found = self._conn.execute(
                "SELECT s.job_id FROM sliced_prints s JOIN prints p ON p.job_id = s.job_id"
                " WHERE s.reprint_of = ? ORDER BY p.start_requested_at, p.rowid", (job_id,)
            ).fetchall()
        return [str(r["job_id"]) for r in found]

    def running(self) -> list[Row]:
        """Started and not yet seen to end: the prints the daemon must watch. A plate of several
        objects is one print, by its first's row."""
        return [r for r in self.rows()
                if r.heads_job and r.started_at is not None and r.ended_at is None]

    def awaiting_answer(self) -> list[Row]:
        """Ended and unanswered: the prints the outcome gate must ask about."""
        return [r for r in self.rows() if r.awaiting_answer]

    def unconfirmed_starts(self) -> list[Row]:
        """A start that raised and was never seen to begin. It may be printing."""
        return [
            r
            for r in self.rows()
            if r.heads_job and r.start_error is not None and r.started_at is None
            and r.ended_at is None
        ]


#: A print, what it used from the inventory if it did, its slice's reading, and a hand-sliced
#: file's record, and its plate's (`Ledger._select` fills the places).
_SELECT = (
    "SELECT p.*, m.spool AS material, r.reading AS reading, r.flags AS flags, "
    "{sliced} AS sliced, {plate} AS plate FROM prints p "
    "LEFT JOIN print_material m ON m.job_id = p.job_id "
    "LEFT JOIN slice_readings r ON r.job_id = p.job_id{join}"
)
_SLICED_JOIN = " LEFT JOIN sliced_prints s ON s.job_id = p.job_id"
_PLATE_JOIN = " LEFT JOIN plate_objects o ON o.job_id = p.job_id"


def _swapped(
    job_id: str, swaps: dict[str, tuple[tuple[SpoolUse, ...], Material | None]],
) -> tuple[tuple[SpoolUse, ...], Material | None]:
    """A row's spools, its own, and the spool its print ended with: a plate's is said once, on
    its first row, and is every object's (mqucifer/print-shop#109)."""
    return swaps.get(job_id, ((), None))[0], swaps.get(plate_job(job_id), ((), None))[1]


def _said_on(db: sqlite3.Connection, job_id: str, pick: Pick) -> None:
    """The person's pick at a swap's outcome, in the answer's transaction. It counts only while
    the stock hasn't changed since it was picked: a load, a word or a start seen since stands."""
    spool_id = pick.spool_id
    swap = db.execute(
        "SELECT m.spool, w.printer_id FROM print_swaps w"
        " JOIN swap_not_known k ON k.job_id = w.job_id AND k.printer_id = w.printer_id"
        " JOIN print_material m ON m.job_id = w.job_id"
        " WHERE w.job_id = ?",
        (job_id,),
    ).fetchone()
    if swap is None or pick.changes != stock_changes(db):
        raise SaidSince(
            "what's on the printer was said since, so the pick is dropped: pick again, or press "
            "Record again without one"
        )
    if not set_loaded(db, swap["printer_id"], spool_id):
        raise LedgerError(f"spool #{spool_id} isn't on hand: pick another")
    now = db.execute(
        "SELECT manufacturer, type, variety, colour, tuning FROM spools WHERE id = ?", (spool_id,)
    ).fetchone()
    on = Material.model_validate_json(swap["spool"]).model_copy(
        update={"spool_id": spool_id, **dict(now)}
    )
    later = db.execute(
        "SELECT 1 FROM prints me JOIN prints p ON (p.start_requested_at > me.start_requested_at"
        " OR (p.start_requested_at = me.start_requested_at AND p.rowid > me.rowid))"
        " WHERE me.job_id = ? AND NOT EXISTS (SELECT 1 FROM plate_objects o"
        "  WHERE o.job_id = p.job_id AND o.plate_job = me.job_id)",
        (job_id,),
    ).fetchone()
    # With a print sent since, what's on now needn't be what this one ended with: not said. A
    # plate's other objects are this print, not one since (mqucifer/print-shop#109).
    if later is None:
        db.execute(
            "UPDATE print_swaps SET ended_with = ? WHERE job_id = ?",
            (on.model_dump_json(), job_id),
        )
    clear_not_known(db, swap["printer_id"], job_id)


def _row(
    row: sqlite3.Row, spools: tuple[SpoolUse, ...] = (), ended_with: Material | None = None
) -> Row:
    data = dict(row)
    data["spools"], data["ended_with"] = spools, ended_with
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
    if data["sliced"] is not None:
        data["sliced"] = SlicedRecord.model_validate_json(data["sliced"])
    if data["plate"] is not None:
        data["plate"] = PlateRow.model_validate_json(data["plate"])
    del data["slicer_config"]
    return Row.model_validate(data)
