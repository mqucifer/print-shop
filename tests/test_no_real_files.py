"""No test opens the Mac's ledger, stock or settings (mqucifer/print-shop#263).

Each check stands a made-up folder and settings file in for the Mac's: the real ones are never
reached for, to prove it or otherwise.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest

from print_shop import env
from print_shop import ledger as ledger_module
from print_shop.env import SETTINGS_PATH, RealFileRefused, load_env, refuse_under_tests
from print_shop.inventory import Inventory
from print_shop.ledger import Ledger, ledger_path
from tests import conftest
from tests.fixtures import write_stl

SECRET = "MQTT_PASSWORD=never-read-this"


class StandIn:
    """A made-up `~/print-shop` and settings file, refused as the real ones are."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home" / "print-shop"
        self.settings = tmp_path / "home" / ".config" / "print-shop" / ".env"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(f"{SECRET}\nPRINT_SHOP_LEDGER={self.home}/ledger.sqlite\n")


@pytest.fixture
def stand_in(tmp_path: Path) -> Iterator[StandIn]:
    made_up = StandIn(tmp_path)
    before = refuse_under_tests(made_up.home, made_up.settings)
    try:
        yield made_up
    finally:
        refuse_under_tests(*before)


def test_the_guard_is_armed_for_every_test_after_conftests_one_read() -> None:
    assert tuple(p.resolve() for p in conftest.REFUSED) == env._REFUSED
    assert (Path("~/print-shop").expanduser(), SETTINGS_PATH) == conftest.REFUSED
    # The one read before it: only where LiteLLM answers is kept, as hosts and ports.
    assert all(isinstance(host, str) and isinstance(port, int) for host, port in conftest.LLM_HOSTS)


def test_every_tests_default_ledger_is_the_sessions_own() -> None:
    assert not ledger_module.DEFAULT_PATH.resolve().is_relative_to(env._REFUSED[0])


def test_a_ledger_under_it_is_refused_before_its_opened(stand_in: StandIn) -> None:
    with pytest.raises(RealFileRefused, match="a ledger at .*the Mac's own"):
        Ledger(stand_in.home / "ledger.sqlite")
    assert not stand_in.home.exists(), "not even its folder is made"


def test_a_ledger_opened_to_read_is_refused_too(stand_in: StandIn) -> None:
    stand_in.home.mkdir(parents=True)
    (stand_in.home / "ledger.sqlite").write_bytes(b"")
    with pytest.raises(RealFileRefused, match="a ledger"):
        Ledger.read_only(stand_in.home / "ledger.sqlite")


def test_the_stock_under_it_is_refused(stand_in: StandIn) -> None:
    with pytest.raises(RealFileRefused, match="the stock"):
        Inventory(stand_in.home / "ledger.sqlite")
    assert not stand_in.home.exists()


def test_the_settings_are_refused_before_theyre_read(stand_in: StandIn) -> None:
    with pytest.raises(RealFileRefused, match="a settings file") as refused:
        load_env(stand_in.settings)
    assert "never-read-this" not in str(refused.value)


def test_a_ledger_named_by_made_up_settings_is_refused_where_it_points(
    stand_in: StandIn, tmp_path: Path,
) -> None:
    """Whatever the path came from: here, a settings file of the test's own."""
    own = tmp_path / "own.env"
    own.write_text(f"PRINT_SHOP_LEDGER={stand_in.home}/ledger.sqlite\n")
    with pytest.raises(RealFileRefused):
        Ledger(ledger_path(own))


def test_a_path_through_a_link_is_refused(stand_in: StandIn, tmp_path: Path) -> None:
    stand_in.home.mkdir(parents=True)
    link = tmp_path / "looks-like-a-temp-folder"
    link.symlink_to(stand_in.home)
    with pytest.raises(RealFileRefused):
        Ledger(link / "ledger.sqlite")


def test_a_made_up_ledger_opens_as_ever(tmp_path: Path) -> None:
    Ledger(tmp_path / "ledger.sqlite").close()
    with Inventory(tmp_path / "ledger.sqlite"):
        pass


def test_a_script_that_drops_its_settings_fails_saying_why(
    stand_in: StandIn, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The way it happened: `print_job.py` run with no settings of the test's own falls back
    to the default, which stands for the Mac's here."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job_no_settings", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(script, "SETTINGS_PATH", stand_in.settings)
    part = write_stl("cube", tmp_path)
    with pytest.raises(RealFileRefused, match="Tests use made-up ones") as refused:
        script.main([str(part), "--nozzle", "0.4", "--filament", "Elegoo PLA"])
    assert "never-read-this" not in str(refused.value)
    assert not stand_in.home.exists()
