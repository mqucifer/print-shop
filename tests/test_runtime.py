"""A real run's wiring, built offline: nothing connects until the daemon is entered."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.runtime import build_run, describe, profiles_for
from print_shop.slicer.store import Store, choose
from tests.fakes import DATA, STORE, STORE_PROFILES
from tests.test_ledger import record


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("MQTT_USER", "CREW_LLM_API_KEY", "PRINTER_HOST", "PRINT_SHOP_LEDGER"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "MQTT_HOST=broker.invalid\n"
        "MQTT_USER=print-shop\n"
        "MQTT_PASSWORD=secret\n"
        "CREW_LLM_API_KEY=sk-test\n"
        "PRINTER_HOST=printer.invalid\n"
        "PRINTER_MAINBOARD_ID=feedface\n"
        f"PRINT_SHOP_LEDGER={tmp_path}/ledger.sqlite\n"
    )
    return path


def test_a_run_is_unarmed_unless_handed_a_printer(settings: Path, tmp_path: Path) -> None:
    run = build_run(
        printer=None, profiles=STORE_PROFILES, settings=settings, workdir=tmp_path / "jobs"
    )
    try:
        assert run.daemon._watch_printer is not None
        assert run.daemon._watch_printer.enable_control is False  # type: ignore[attr-defined]
        assert run.ledger.path == tmp_path / "ledger.sqlite"
        assert run.daemon._profiles is not None
        process = json.loads(run.daemon._profiles.process.read_text())
        assert process["curr_bed_type"] == "Textured PEI Plate", "the Sponsor's plate"
    finally:
        run.ledger.close()


def test_a_run_without_a_model_key_is_refused(settings: Path, tmp_path: Path) -> None:
    settings.write_text(settings.read_text().replace("CREW_LLM_API_KEY=sk-test\n", ""))
    with pytest.raises(RuntimeError, match="CREW_LLM_API_KEY"):
        build_run(printer=None, profiles=None, settings=settings, workdir=tmp_path / "jobs")


def test_a_run_without_the_broker_is_refused(settings: Path, tmp_path: Path) -> None:
    settings.write_text(settings.read_text().replace("MQTT_USER=print-shop\n", ""))
    with pytest.raises(RuntimeError, match="MQTT_USER"):
        build_run(printer=None, profiles=None, settings=settings, workdir=tmp_path / "jobs")


def test_a_serving_run_slices_nothing(settings: Path, tmp_path: Path) -> None:
    """`--serve` names no nozzle: its daemon has no profiles, and says so if asked."""
    run = build_run(printer=None, profiles=None, settings=settings, workdir=tmp_path / "jobs")
    try:
        assert run.daemon._profiles is None
    finally:
        run.ledger.close()


def test_the_chosen_profiles_carry_their_store_files_and_commit() -> None:
    choice = choose(Store(STORE), nozzle_mm=0.4, filament="Elegoo PLA @ECC - Copy")
    used = profiles_for(choice, STORE).used
    assert used is not None
    assert used.machine.file == "machine/Elegoo Centauri Carbon 0.4 nozzle - Copy.json"
    assert len(used.filament.sha256) == 64
    assert len(used.commit) == 40, "print-shop's commit, from its own checkout"


def test_describe_says_an_open_print_is_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    ledger.record_started("job-1")
    row = ledger.get("job-1")
    assert row is not None
    text = describe(row)
    assert "open: no answer yet" in text
    assert "Elegoo PLA @ECC" in text
    ledger.record_ended("job-1", 9)
    ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.WARPING))
    row = ledger.get("job-1")
    assert row is not None
    assert "failed, warping" in describe(row)
    assert "(printer: completed)" in describe(row)


def _script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_lists_the_ledger(
    settings: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _script().main(["--list", "--settings", str(settings)]) == 0
    assert "the ledger is empty" in capsys.readouterr().out


def test_the_script_does_not_arm_without_the_flag(
    settings: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = _script()
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)

    def no(**_: object) -> None:
        raise AssertionError("live_printer() was called without the flag")

    class Stop(Exception):
        pass

    def stop(**kwargs: object) -> None:
        assert kwargs["printer"] is None
        raise Stop

    monkeypatch.setattr(script, "live_printer", no)
    monkeypatch.setattr(script, "build_run", stop)
    part = tmp_path / "part.stl"
    part.write_text("solid x\nendsolid x\n")
    with pytest.raises(Stop):
        script.main([str(part), *FITTED, "--settings", str(settings)])


#: The fitted nozzle and the loaded filament preset; its pairing names the rest.
FITTED = [
    "--nozzle", "0.4",
    "--filament", "Elegoo PLA @ECC - Copy",
    "--intake", str(DATA / "intake" / "cube.json"),
]


def test_the_script_refuses_a_print_without_the_nozzle_and_filament(
    settings: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    part = tmp_path / "part.stl"
    part.write_text("solid x\nendsolid x\n")
    for missing in (["--nozzle", "0.4"], ["--filament", "PLA"], []):
        with pytest.raises(SystemExit) as exit_:
            _script().main([str(part), *missing, "--settings", str(settings)])
        assert exit_.value.code == 2
        assert "--nozzle (mm) and --filament" in capsys.readouterr().err


def test_the_script_lists_the_candidates_on_a_tie(
    settings: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    part = tmp_path / "part.stl"
    part.write_text("solid x\nendsolid x\n")
    code = _script().main(
        [str(part), "--nozzle", "0.4", "--filament", "PLA", "--settings", str(settings)]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Several PLA presets are in the store; name the one loaded" in err
    assert '"Elegoo PLA @ECC - Copy"' in err
