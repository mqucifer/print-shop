"""The ledger records what the agent started, and never a guessed outcome."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from print_shop.ledger import (
    DEFAULT_PATH,
    Answer,
    Ledger,
    LedgerError,
    PrintRecord,
    Result,
    Symptom,
    ledger_path,
)
from print_shop.slicer.orca import parse_config_block
from print_shop.slicer.store import ProfileRef, ProfilesUsed
from tests.fakes import DATA

CONFIG = parse_config_block(DATA / "recorded" / "cube.gcode")


def record(job_id: str = "job-1") -> PrintRecord:
    return PrintRecord(
        job_id=job_id,
        stl_name="cube.stl",
        stl_sha256="ab" * 32,
        rotation_deg=(0.0, 0.0, 0.0),
        orientation_source="model:crew-code",
        orientation_rationale="flat face down",
        orientation_accepted=True,
        gcode_name=f"cube_{job_id}.gcode",
        print_time_s=1009,
        filament_cm3=3.34,
        layer_count=100,
        slicer_config=CONFIG,
        printer_machine="Centauri Carbon",
        printer_firmware="V1.1.46",
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "nested" / "ledger.sqlite")


def ended(ledger: Ledger, job_id: str = "job-1", status: int = 9) -> None:
    ledger.record_start_requested(record(job_id))
    ledger.record_started(job_id)
    ledger.record_ended(job_id, status)


def test_a_start_is_on_record_before_the_printer_is_told(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    row = ledger.get("job-1")
    assert row is not None
    assert row.start_requested_at
    assert row.started_at is None and row.ended_at is None and row.result is None


def test_the_row_holds_what_a_symptom_is_traced_to(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    row = ledger.get("job-1")
    assert row is not None
    assert row.stl_sha256 == "ab" * 32
    assert row.rotation_deg == (0.0, 0.0, 0.0)
    assert row.orientation_source == "model:crew-code"
    assert row.filament == "Elegoo PLA @ECC"
    assert row.slicer_settings["layer_height"] == "0.2"
    assert row.slicer_settings["printer_settings_id"] == "Elegoo Centauri Carbon 0.4 nozzle"
    assert row.printer_firmware == "V1.1.46"
    assert len(row.slicer_config_sha256) == 64


def test_the_whole_config_block_is_kept(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    conn = sqlite3.connect(ledger.path)
    (stored,) = conn.execute("SELECT slicer_config FROM prints").fetchone()
    assert '"wall_loops": "2"' in stored
    assert stored.count('": "') == len(CONFIG)


def test_a_failed_start_keeps_its_error(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    ledger.record_start_failed("job-1", "RuntimeError: never left a ready state")
    row = ledger.get("job-1")
    assert row is not None
    assert row.started_at is None
    assert row.start_error == "RuntimeError: never left a ready state"


def test_the_printers_end_is_not_the_outcome(ledger: Ledger) -> None:
    ended(ledger, status=9)
    row = ledger.get("job-1")
    assert row is not None
    assert row.printer_end_status == 9
    assert row.result is None, "COMPLETED is what the printer said, not how the part came out"
    assert ledger.awaiting_answer() == [row]


def test_an_answer_closes_the_row(ledger: Ledger) -> None:
    ended(ledger)
    ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.WARPING))
    row = ledger.get("job-1")
    assert row is not None
    assert (row.result, row.symptom) == (Result.FAILED, Symptom.WARPING)
    assert row.answered_at
    assert ledger.awaiting_answer() == []


def test_a_second_answer_is_refused(ledger: Ledger) -> None:
    ended(ledger)
    ledger.record_answer("job-1", Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
    with pytest.raises(LedgerError):
        ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.ADHESION))
    row = ledger.get("job-1")
    assert row is not None and row.result is Result.SUCCESS


def test_a_print_still_running_cannot_be_answered(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    ledger.record_started("job-1")
    with pytest.raises(LedgerError):
        ledger.record_answer("job-1", Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
    assert [r.job_id for r in ledger.running()] == ["job-1"]


@pytest.mark.parametrize(
    ("result", "symptom", "note"),
    [
        (Result.SUCCESS, Symptom.STRINGING, ""),
        (Result.FAILED, Symptom.NONE, ""),
        (Result.FAILED, Symptom.OTHER, "   "),
    ],
)
def test_an_answer_that_does_not_add_up_is_refused(
    ledger: Ledger, result: Result, symptom: Symptom, note: str
) -> None:
    ended(ledger)
    with pytest.raises(LedgerError):
        ledger.record_answer("job-1", Answer(result=result, symptom=symptom, note=note))
    row = ledger.get("job-1")
    assert row is not None and row.result is None


def test_an_unknown_job_is_refused(ledger: Ledger) -> None:
    with pytest.raises(LedgerError, match="not in the ledger"):
        ledger.record_started("nobody")


def test_a_job_is_recorded_once(ledger: Ledger) -> None:
    ledger.record_start_requested(record())
    with pytest.raises(LedgerError, match="already"):
        ledger.record_start_requested(record())


def test_the_schema_itself_refuses_a_guess(ledger: Ledger) -> None:
    """Even a write that skips this module cannot record an outcome before the end."""
    ledger.record_start_requested(record())
    conn = sqlite3.connect(ledger.path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE prints SET result='success', symptom='none', answered_at='now' "
            "WHERE job_id='job-1'"
        )


def test_it_reopens_with_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as first:
        ended(first)
    with Ledger(path) as again:
        assert [r.job_id for r in again.awaiting_answer()] == ["job-1"]


def test_an_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    sqlite3.connect(path).execute("PRAGMA user_version=99")
    with pytest.raises(LedgerError, match="schema version 99"):
        Ledger(path)


def test_the_path_comes_from_the_settings(tmp_path: Path) -> None:
    settings = tmp_path / ".env"
    settings.write_text(f"PRINT_SHOP_LEDGER={tmp_path}/elsewhere.sqlite\n")
    assert ledger_path(settings) == tmp_path / "elsewhere.sqlite"
    assert ledger_path(tmp_path / "absent.env") == DEFAULT_PATH
    assert Path("~/print-shop/ledger.sqlite").expanduser() == DEFAULT_PATH


def test_a_copy_of_the_file_alone_holds_every_row(ledger: Ledger, tmp_path: Path) -> None:
    """The nightly archive copies files; the main file must not lag behind its WAL."""
    import shutil

    ended(ledger)
    ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.STRINGING))
    copy = tmp_path / "copy.sqlite"
    shutil.copyfile(ledger.path, copy)  # not the -wal, not the -shm
    with Ledger(copy) as restored:
        row = restored.get("job-1")
    assert row is not None and row.symptom is Symptom.STRINGING


def _used() -> ProfilesUsed:
    def ref(kind: str, name: str) -> ProfileRef:
        return ProfileRef(name=name, file=f"{kind}/{name}.json", sha256="ab" * 32)

    return ProfilesUsed(
        machine=ref("machine", "Elegoo Centauri Carbon 0.4 nozzle - Copy"),
        process=ref("process", "0.20mm Standard @Elegoo CC 0.4 nozzle - Copy"),
        filament=ref("filament", "Elegoo PLA @ECC - Copy"),
        commit="c0ffee" * 6 + "c0ff",
        store_dirty=False,
    )


def test_a_print_keeps_its_profiles_by_file_hash_and_commit(tmp_path: Path) -> None:
    with Ledger(tmp_path / "l.sqlite") as ledger:
        ledger.record_start_requested(record().model_copy(update={"profiles": _used()}))
        row = ledger.get("job-1")
    assert row is not None and row.profiles == _used()


def test_a_version_1_ledger_gains_the_new_columns_and_keeps_its_rows(
    tmp_path: Path,
) -> None:
    """The first ledger predates print-shop's own profiles: its rows say none, not a guess."""
    path = tmp_path / "l.sqlite"
    with Ledger(path) as ledger:
        ledger.record_start_requested(record())
    db = sqlite3.connect(path)
    for column in ("profiles", "intake", "proposal", "corrections"):
        db.execute(f"ALTER TABLE prints DROP COLUMN {column}")
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()

    with Ledger(path) as again:
        row = again.get("job-1")
        assert row is not None and row.profiles is None
        assert (row.intake, row.proposal, row.corrections) == (None, None, None)
        again.record_start_requested(record("job-2").model_copy(update={"profiles": _used()}))
        assert again.get("job-2").profiles == _used()  # type: ignore[union-attr]
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 3
