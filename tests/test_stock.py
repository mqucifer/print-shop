"""`scripts/stock.py` and the print command's use of the inventory (mqucifer/print-shop#11)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_runtime import settings  # noqa: F401  (the fixture)


def _load(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stock(env: Path, *args: str) -> int:
    code: int = _load("stock").main(["--settings", str(env), *args])
    return code


def test_a_spool_is_added_by_variety_with_its_profiles_names(
    settings: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    assert _stock(settings, "add", "elegoo pla matte", "black", "--grams", "750") == 0
    assert "spool #1  Elegoo PLA Matte  black  750 g  (on hand)" in capsys.readouterr().out


def test_a_variety_tuned_more_than_once_asks_which_tuning_once(
    settings: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    assert _stock(settings, "add", "Elegoo Rapid PETG", "black") == 2
    assert '--tuning "Elegoo RAPID PETG 270Degree' in capsys.readouterr().err
    tuning = "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,AuxPartCoolingFan=100"
    assert _stock(settings, "add", "Elegoo Rapid PETG", "black", "--tuning", tuning) == 0
    assert f'tuning "{tuning}"' in capsys.readouterr().out


def test_a_filament_without_a_profile_needs_its_type_and_is_marked_unused(
    settings: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    assert _stock(settings, "add", "Overture TPU", "red") == 2
    assert "has no profile in profiles/" in capsys.readouterr().err
    assert _stock(settings, "add", "Overture TPU", "red", "--type", "tpu",
                  "--manufacturer", "Overture") == 0
    assert "a print won't use it yet" in capsys.readouterr().out


def test_setting_up_takes_three_lines(
    settings: Path, capsys: pytest.CaptureFixture[str]  # noqa: F811
) -> None:
    assert _stock(settings, "printer", "add", "cc1", "Elegoo Centauri Carbon") == 0
    assert _stock(settings, "fit", "0.4") == 0
    assert _stock(settings, "add", "Elegoo PLA", "white") == 0
    assert _stock(settings, "load", "1") == 0
    capsys.readouterr()
    assert _stock(settings) == 0
    assert capsys.readouterr().out.splitlines() == [
        "printer cc1  Elegoo Centauri Carbon",
        "nozzle #1  0.4 mm  (fitted on cc1)",
        "spool #1  Elegoo PLA  white  (loaded on cc1)",
    ]


# --------------------------------------------------------------------------
# The print command
# --------------------------------------------------------------------------


class Stop(Exception):
    pass


def _print(env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
           *args: str) -> tuple[int | None, dict[str, object]]:
    """Run print_job.py up to building the run, unarmed, and say what it chose."""
    script = _load("print_job")
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", tmp_path / "jobs")
    seen: dict[str, object] = {}

    def stop(**kwargs: object) -> None:
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(script, "build_run", stop)
    part = tmp_path / "part.stl"
    part.write_text("solid x\nendsolid x\n")
    try:
        return script.main([str(part), *args, "--settings", str(env)]), seen
    except Stop:
        return None, seen


def test_without_an_inventory_the_print_says_so_and_takes_the_flags(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, seen = _print(settings, tmp_path, monkeypatch, "--nozzle", "0.4", "--filament",
                     "Elegoo PLA")
    out = capsys.readouterr().out
    assert out.startswith("The inventory isn't set up (uv run python scripts/stock.py printer")
    assert seen["profiles"] is not None


def test_with_an_inventory_the_print_takes_what_is_fitted_and_loaded(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    for line in (["printer", "add", "cc1", "Elegoo Centauri Carbon"], ["fit", "0.4"],
                 ["add", "Elegoo PETG", "clear"], ["load", "1"]):
        _stock(settings, *line)
    capsys.readouterr()
    _, seen = _print(settings, tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert "From the inventory: cc1 with a 0.4 mm nozzle; spool #1, Elegoo PETG, clear." in out
    assert "filament  Elegoo PETG @ECC" in out
    used = seen["profiles"].used  # type: ignore[attr-defined]
    assert used.material.spool_id == 1, "the ledger will point at the spool"
    # What #10's questions and proposal read is the loaded spool's preset.
    filament = seen["profiles"].filament.read_text()  # type: ignore[attr-defined]
    assert json.loads(filament)["name"] == "Elegoo PETG @ECC"
    assert seen["asker"] is not None, "the questions are asked on the command line"


def test_a_print_with_a_nozzle_outside_the_scope_is_refused_naming_it(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    for line in (["printer", "add", "cc1", "Elegoo Centauri Carbon"], ["fit", "0.8"],
                 ["add", "Elegoo PLA", "white"], ["load", "1"]):
        _stock(settings, *line)
    code, seen = _print(settings, tmp_path, monkeypatch)
    assert code == 2 and not seen
    assert "profiles/scope.json covers 0.4 mm" in capsys.readouterr().err


def test_a_print_naming_what_isnt_loaded_is_refused(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    for line in (["printer", "add", "cc1", "Elegoo Centauri Carbon"], ["fit", "0.4"],
                 ["add", "Elegoo PLA", "white"], ["load", "1"]):
        _stock(settings, *line)
    code, _ = _print(settings, tmp_path, monkeypatch, "--filament", "Elegoo PETG")
    assert code == 2
    assert "not Elegoo PETG: `stock load <id>` first" in capsys.readouterr().err
