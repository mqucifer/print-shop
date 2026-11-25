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

**The live services get the same treatment** (mqucifer/print-shop#115). A
local `pytest` without `-m` ran the `mqtt`, `llm` and `ha` tests, configured
from the Mac's settings: under the live daemon's own topics, with the
settings' key. So a test marked with one is skipped unless its env var opts in
(`tests/live.py`), and, underneath the markers, while pytest runs:
- a real MQTT connect is refused unless a test broker is opted in, and a
  bridge is refused under any prefix or node that isn't a test run's;
- a request to LiteLLM is refused unless `PRINT_SHOP_TEST_LLM=1`.

**And so do the Mac's jobs folder and its live lock** (mqucifer/print-shop#207). The lock is in
that folder, so a test that ran `print_job.py`'s `main` would take the Sponsor's, and refuse
their print, or be refused by it. While pytest runs the folder is the session's own.

**And so do the Mac's ledger, stock and settings** (mqucifer/print-shop#263). A test whose
made-up settings were shadowed fell back to the real ones, and listed the real spools. So, while
pytest runs, `Ledger`, `Inventory` and `load_env` refuse the real settings file and anything
under `~/print-shop`, before opening it, whatever the path came from, and every test's default
ledger is the session's own. There's no opt-in. The single exception is this file's own read of
the settings' LLM address, below, taken at import before the guard is armed: it keeps only the
hosts and ports, to refuse the live model, and prints and logs nothing of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import paho.mqtt.client as paho
import pytest
from pycentauri import Printer

from print_shop import ledger as ledger_module
from print_shop import runtime
from print_shop.core.mqtt import MqttBridge
from print_shop.env import SETTINGS_PATH, load_env, refuse_under_tests
from print_shop.expert.client import DEFAULT_URL, URL_KEY
from tests.live import OPT_IN, TEST_NODE, TEST_PREFIX, opted_in


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


@pytest.fixture(autouse=True, scope="session")
def _its_own_jobs_folder(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """`WORK_DIR`, and so the live lock, for a script loaded in any test."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runtime, "WORK_DIR", tmp_path_factory.mktemp("print-shop") / "jobs")
    try:
        yield
    finally:
        monkeypatch.undo()


@pytest.fixture(autouse=True, scope="session")
def _its_own_ledger(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """The default ledger, where settings don't name one, for every test: the session's own,
    never `~/print-shop/ledger.sqlite` (mqucifer/print-shop#263)."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ledger_module, "DEFAULT_PATH",
                        tmp_path_factory.mktemp("print-shop") / "ledger.sqlite")
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


class LiveServiceRefused(RuntimeError):
    """Raised when a test reaches a live service it wasn't opted in to (mqucifer/print-shop#115)."""


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """A test marked `mqtt`, `llm` or `ha` runs only when its env var opts in."""
    for marker, var in OPT_IN.items():
        if item.get_closest_marker(marker) is not None and not opted_in(marker):
            pytest.skip(f"reaches a live service ({marker}): set {var} to opt in")


def _llm_hosts() -> set[tuple[str, int]]:
    """Where LiteLLM answers: the default, and the settings' URL if they name one."""
    urls = {DEFAULT_URL, "http://localhost:4000/v1"}
    try:
        named = load_env(SETTINGS_PATH).get(URL_KEY)
    except OSError:
        named = None
    if named:
        urls.add(named)
    found = set()
    for url in urls:
        parts = urlsplit(url)
        found.add((parts.hostname or "", parts.port or 80))
    return found


#: Where LiteLLM answers, read once at import (mqucifer/print-shop#263): the one read of the
#: real settings, before the guard below is armed. Only hosts and ports are kept.
LLM_HOSTS = _llm_hosts()
#: The Mac's own files, which no test opens from here on (mqucifer/print-shop#263).
#: The settings file is left readable only in a run that opts in to a live service on purpose,
#: whose tests need the real credentials; `~/print-shop` never is.
REFUSED = (Path("~/print-shop").expanduser(),
           *(() if any(opted_in(marker) for marker in OPT_IN) else (SETTINGS_PATH,)))
refuse_under_tests(*REFUSED)


@pytest.fixture(autouse=True, scope="session")
def _refuse_live_services() -> Iterator[None]:
    """The broker and LiteLLM, refused for the whole session unless opted in.

    Autouse and session-scoped, as the printer's guard is, so a test added later that forgets
    its marker still can't reach either.
    """
    monkeypatch = pytest.MonkeyPatch()

    def refuse_connect(original: Any) -> Any:
        def connect(self: paho.Client, *args: Any, **kwargs: Any) -> Any:
            if not opted_in("mqtt"):
                raise LiveServiceRefused(
                    f"a test tried to connect to an MQTT broker. Set {OPT_IN['mqtt']} to a "
                    "test broker to opt in; the tests never use the live one's topics"
                )
            return original(self, *args, **kwargs)

        return connect

    monkeypatch.setattr(paho.Client, "connect", refuse_connect(paho.Client.connect))
    monkeypatch.setattr(paho.Client, "connect_async", refuse_connect(paho.Client.connect_async))

    bridge_enter = MqttBridge.__enter__

    def enter(self: MqttBridge) -> Any:
        cfg = self._cfg
        if not (cfg.topic_prefix.startswith(TEST_PREFIX) and cfg.node_id.startswith(TEST_NODE)):
            raise LiveServiceRefused(
                f"a test opened a bridge as {cfg.topic_prefix!r}, node {cfg.node_id!r}: tests "
                f"publish only under a test run's own (`tests.live.broker_config()`)"
            )
        return bridge_enter(self)

    monkeypatch.setattr(MqttBridge, "__enter__", enter)

    llm = LLM_HOSTS
    send = httpx.Client.send

    def guarded_send(self: httpx.Client, request: httpx.Request, **kwargs: Any) -> Any:
        # A test's own MockTransport answers in the process: nothing reaches the network.
        mocked = isinstance(getattr(self, "_transport", None), httpx.MockTransport)
        if not mocked and (request.url.host, request.url.port or 80) in llm and not opted_in(
            "llm"
        ):
            raise LiveServiceRefused(
                f"a test called LiteLLM at {request.url}. Set {OPT_IN['llm']}=1 to opt in"
            )
        return send(self, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", guarded_send)
    try:
        yield
    finally:
        monkeypatch.undo()



@pytest.fixture(params=["http", "https"])
def phone_scheme(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
                 tmp_path_factory: pytest.TempPathFactory) -> str:
    """Every phone test, over plain HTTP and over HTTPS with a made-up certificate made here
    (mqucifer/print-shop#312): `tests/test_phone_start.py`'s `home()` and `Browser` read it."""
    from tests import test_phone_start as start  # noqa: PLC0415
    from tests.tls_made_up import make  # noqa: PLC0415

    monkeypatch.setattr(start, "SCHEME", request.param)
    if request.param == "https" and start.TLS is None:
        start.TLS = make(tmp_path_factory.mktemp("phone-tls"))
    return str(request.param)
