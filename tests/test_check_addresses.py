"""The address check catches each shape an address takes, and a known value."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_addresses.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_addresses", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


#: Built at run time, so this file holds no address for the check to find in
#: itself. Exempting the file instead would be a hole in the check.
DOT = "."


@pytest.mark.parametrize(
    "line",
    [
        "PRINTER_HOST=" + DOT.join(["10", "20", "30", "40"]),
        "url = 'http://some-box" + DOT + "local:8888/v1'",
        "ssh spark" + DOT + "tail1234" + DOT + "ts" + DOT + "net",
    ],
)
def test_an_address_is_found(line: str) -> None:
    assert len(check.findings(line, [])) == 1


@pytest.mark.parametrize(
    "line",
    [
        "base_url = 'http://127.0.0.1:4000/v1'",
        "bind 0.0.0.0",
        ".claude/settings.local.json",
        "version 1.1.46",
        "pycentauri>=0.9.1",
    ],
)
def test_what_is_not_an_address_passes(line: str) -> None:
    assert check.findings(line, []) == []


def test_a_known_value_is_found_without_being_printed() -> None:
    found = check.findings("mainboard = abc123def", ["abc123def"])
    assert found == [(1, "a value from the Mac's settings")]


def test_settings_values_are_read_by_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = tmp_path / ".env"
    settings.write_text(
        "PRINTER_HOST=printer-box\n"
        "PRINTER_MAINBOARD_ID=feedface01\n"
        "HA_TOKEN=tok-abc\n"
        "MQTT_PORT=1883\n"
        "MQTT_TOPIC_PREFIX=3dprint\n"
        "# PRINTER_HOST=commented\n"
    )
    monkeypatch.setenv("PRINT_SHOP_SETTINGS", str(settings))
    monkeypatch.setenv("INFRA_HOSTS", str(tmp_path / "absent.env"))
    assert sorted(check.local_values()) == ["feedface01", "printer-box", "tok-abc"]
