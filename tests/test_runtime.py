"""A real run's wiring, built offline: nothing connects until the daemon is entered."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.runtime import build_run, describe, orca_profiles
from tests.fakes import DATA
from tests.test_ledger import record


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("MQTT_USER", "CREW_LLM_API_KEY", "PRINTER_HOST", "PRINT_SHOP_LEDGER"):
        monkeypatch.delenv(key, raising=False)
    filament = tmp_path / "filament.json"
    filament.write_text("{}")
    path = tmp_path / ".env"
    path.write_text(
        "MQTT_HOST=broker.invalid\n"
        "MQTT_USER=print-shop\n"
        "MQTT_PASSWORD=secret\n"
        "CREW_LLM_API_KEY=sk-test\n"
        "PRINTER_HOST=printer.invalid\n"
        "PRINTER_MAINBOARD_ID=feedface\n"
        f"PRINT_SHOP_LEDGER={tmp_path}/ledger.sqlite\n"
        f"ORCA_DATADIR={tmp_path}\n"
        f"ORCA_MACHINE={DATA}/profiles/machine.json\n"
        f"ORCA_PROCESS={DATA}/profiles/process.json\n"
        f"ORCA_FILAMENT={filament}\n"
    )
    return path


def test_a_run_is_unarmed_unless_handed_a_printer(settings: Path, tmp_path: Path) -> None:
    run = build_run(printer=None, settings=settings, workdir=tmp_path / "jobs")
    try:
        assert run.daemon._watch_printer is not None
        assert run.daemon._watch_printer.enable_control is False  # type: ignore[attr-defined]
        assert run.ledger.path == tmp_path / "ledger.sqlite"
    finally:
        run.ledger.close()


def test_a_run_without_a_model_key_is_refused(settings: Path, tmp_path: Path) -> None:
    settings.write_text(settings.read_text().replace("CREW_LLM_API_KEY=sk-test\n", ""))
    with pytest.raises(RuntimeError, match="CREW_LLM_API_KEY"):
        build_run(printer=None, settings=settings, workdir=tmp_path / "jobs")


def test_a_run_without_the_broker_is_refused(settings: Path, tmp_path: Path) -> None:
    settings.write_text(settings.read_text().replace("MQTT_USER=print-shop\n", ""))
    with pytest.raises(RuntimeError, match="MQTT_USER"):
        build_run(printer=None, settings=settings, workdir=tmp_path / "jobs")


def test_a_missing_profile_is_found_before_a_job(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Orca profile"):
        orca_profiles({"ORCA_DATADIR": str(tmp_path)})


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
        script.main([str(part), "--settings", str(settings)])
