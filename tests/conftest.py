"""No test arms the printer. Enforced here, not left to discipline.

On 2026-09-16 the prototype's test suite started two real prints. Neither was a
gate failure: a test published a genuine MQTT approval to the genuine broker,
so the approval gate was satisfied exactly as designed. A gate protects against
the wrong *decision*; it cannot tell a person's thumb from a fixture sending
the same bytes.

So the rule underneath the gate is enforced where the gate cannot see it:
**while pytest runs, nothing may open an armed connection to a printer.** The
guard sits on `pycentauri.Printer.connect`, the only door: every write in the
library (`upload_file`, `start_print`, `stop`, every setter) needs a connection
armed there.

This is stricter than the prototype's guard, which let
`PRINT_AGENT_ALLOW_HARDWARE=1` through. Here nothing does. Tests use fakes and
recorded printer replies; the first real contact with the printer is the
Sponsor's, at the machine (mqucifer/print-shop#3), and it is not a test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pycentauri import Printer


class HardwareDisarmed(RuntimeError):
    """Raised when a test tries to open a connection that could move the machine."""


@pytest.fixture(autouse=True, scope="session")
def _disarm_the_printer() -> Iterator[None]:
    """Refuse `enable_control=True` for the whole session, whatever the environment.

    Autouse and session-scoped, so it covers every test, including ones added
    later by someone who has never read this file.
    """
    monkeypatch = pytest.MonkeyPatch()
    original_connect = Printer.connect

    async def guarded_connect(
        host: str, *, enable_control: bool = False, **kwargs: object
    ) -> Printer:
        if enable_control:
            raise HardwareDisarmed(
                "a test tried to open an armed connection to a printer. Tests use "
                "a fake; no test in this repository arms the printer, whatever "
                "the environment says."
            )
        return await original_connect(host, enable_control=False, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Printer, "connect", staticmethod(guarded_connect))
    try:
        yield
    finally:
        monkeypatch.undo()


# CI's split of the slow tests (mqucifer/print-shop#34)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard",
        default=None,
        metavar="I/N",
        help="run only part I of N of the tests the markers selected, as CI's matrix does",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep every Nth test from the Ith, after `-m` has chosen, so the N parts are the whole.

    Last, so it splits only what the markers kept; dealt in turn, so each part gets a share of
    every file. Collection's order is fixed, so each xdist worker keeps the same part.
    """
    shard = config.getoption("--shard")
    if shard is None:
        return
    part, _, parts = str(shard).partition("/")
    i, n = int(part), int(parts)
    if not 1 <= i <= n:
        raise pytest.UsageError(f"--shard {shard}: I must be from 1 to N")
    kept = items[i - 1 :: n]
    config.hook.pytest_deselected(items=[t for t in items if t not in kept])
    items[:] = kept
