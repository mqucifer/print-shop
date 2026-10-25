"""The session guard refuses an armed connection before any packet is sent."""

from __future__ import annotations

import asyncio

import pytest
from pycentauri import Printer

from print_shop.arming import ALLOW_HARDWARE_ENV, hardware_allowed
from tests.conftest import HardwareDisarmed

#: `.invalid` never resolves (RFC 2606), so if the guard ever let a call
#: through, it would fail on the name rather than reach a machine.
NOWHERE = "printer.invalid"


def test_an_armed_connection_is_refused() -> None:
    with pytest.raises(HardwareDisarmed):
        asyncio.run(Printer.connect(NOWHERE, enable_control=True))


def test_it_is_refused_even_when_the_environment_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALLOW_HARDWARE_ENV, "1")
    assert hardware_allowed()
    with pytest.raises(HardwareDisarmed):
        asyncio.run(Printer.connect(NOWHERE, enable_control=True))


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_only_exactly_one_opts_in(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(ALLOW_HARDWARE_ENV, raising=False)
    else:
        monkeypatch.setenv(ALLOW_HARDWARE_ENV, value)
    assert not hardware_allowed()
