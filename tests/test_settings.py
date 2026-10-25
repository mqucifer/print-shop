"""The printer's and the broker's settings come from the Mac's settings file."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.core.mqtt import MqttConfig
from print_shop.printer.centauri import PrinterConfig


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in ("PRINTER_HOST", "PRINTER_MAINBOARD_ID", "MQTT_HOST", "MQTT_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "PRINTER_HOST=printer.invalid\n"
        "PRINTER_MAINBOARD_ID=feedface\n"
        "MQTT_HOST=broker.invalid\n"
        "MQTT_PASSWORD='with a space'\n"
    )
    return path


def test_the_printer_is_read_from_the_settings(settings: Path) -> None:
    cfg = PrinterConfig.from_env(settings)
    assert (cfg.host, cfg.mainboard_id) == ("printer.invalid", "feedface")


def test_a_missing_printer_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRINTER_HOST", raising=False)
    with pytest.raises(RuntimeError, match="PRINTER_HOST"):
        PrinterConfig.from_env(tmp_path / "absent.env")


def test_the_broker_is_read_from_the_settings(settings: Path) -> None:
    cfg = MqttConfig.from_env(settings)
    assert cfg.host == "broker.invalid"
    assert cfg.password.get_secret_value() == "with a space"
