"""A person arms and starts one named, ready job from their phone, on the home Wi-Fi only.

mqucifer/print-shop#214. Every service is a fake: the printer is #209's `FramePrinter`, the
broker is in memory, the shop is made up, and the phone's page is the real request handler driven
through a fake socket, so the peer address is the socket's, as on the Mac. The addresses are
made-up private ones, built from numbers so no address is written in this public repository. No
test arms: `tests/conftest.py` refuses every armed connection, and `live_printer` is reached only
to show it refuses, or that what it builds can't connect.
"""

from __future__ import annotations

import html
import io
import ipaddress
import json
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import pytest

from print_shop import arming
from print_shop.arming import Armed, OneStart, armed_by_flag, spend
from print_shop.core.mqtt import command_topic, gate_topic
from print_shop.ledger import Ledger
from print_shop.orchestrator import daemon as daemon_module
from print_shop.orchestrator.daemon import (
    CARD_DOESNT_START,
    NOT_ARMED_PHONE,
    Daemon,
    PhoneStart,
    ReadyToStart,
)
from print_shop.orchestrator.start import Checked
from print_shop.phone import confirm as confirm_module
from print_shop.phone.confirm import CONFIRM_LASTS_S, EXPIRED, UNKNOWN, Confirms
from print_shop.phone.home import (
    NO_ROUTER,
    OUTSIDE,
    PROXY_HEADERS,
    THROUGH_HA,
    THROUGH_THE_MAC,
    THROUGH_THE_ROUTER,
    Home,
    HomeRefused,
)
from print_shop.phone.phones import ALPHABET, CODE_LASTS_S, CODE_LENGTH, WRONG_TRIES, Phones
from print_shop.phone.server import COOKIE, PhoneOff, handler_for, make_server
from print_shop.printer import centauri
from print_shop.printer.centauri import PrinterConfig, PrinterSnapshot, live_printer
from print_shop.setup import Proposer
from print_shop.setup.intake import Answer
from print_shop.setup.review import Review
from tests.conftest import HardwareDisarmed
from tests.fakes import FramePrinter
from tests.test_job_e2e import CFG
from tests.test_review import _model
from tests.test_waits_for_you import WAIT_S, Shop, _script, _top_question, wait_until


def ip(*parts: int) -> str:
    return ".".join(str(p) for p in parts)


NETWORK = f"{ip(10, 77, 0, 0)}/24"
MAC = ip(10, 77, 0, 20)
PHONE = ip(10, 77, 0, 31)
ANOTHER_PHONE = ip(10, 77, 0, 32)
ROUTER = ip(10, 77, 0, 1)
HA = ip(10, 77, 0, 5)
AWAY = ip(10, 78, 0, 9)
PORT = 8765
HOST = f"{MAC}:{PORT}"
ORIGIN = f"http://{HOST}"
HA_NAME = "made-up-ha"


def _resolve(name: str) -> str:
    if name == HA_NAME:
        return HA
    raise OSError(f"no such host {name}")


def home(**changes: Any) -> Home:
    given: dict[str, Any] = {
        "address": ipaddress.IPv4Address(MAC), "network": ipaddress.IPv4Network(NETWORK),
        "port": PORT, "router": ipaddress.IPv4Address(ROUTER), "ha": ipaddress.IPv4Address(HA),
    }
    return Home(**{**given, **changes})


# --------------------------------------------------------------------------
# Driving the page's real handler, with the peer address the socket would give
# --------------------------------------------------------------------------


class _Conn:
    """A socket, as `StreamRequestHandler` uses one: the request in, the reply out."""

    def __init__(self, raw: bytes) -> None:
        self._in = io.BytesIO(raw)
        self.out = bytearray()

    def makefile(self, mode: str, *args: Any, **kwargs: Any) -> io.BytesIO:
        assert "r" in mode
        return self._in

    def sendall(self, data: bytes) -> None:
        self.out += data

    def settimeout(self, value: float | None) -> None:
        pass


@dataclass
class Reply:
    status: int
    headers: list[tuple[str, str]]
    #: The page as sent, escaped.
    raw: str

    @property
    def body(self) -> str:
        """The page's text, as the phone shows it."""
        return html.unescape(self.raw)

    def header(self, name: str) -> str:
        return next((v for k, v in self.headers if k.lower() == name.lower()), "")

    def confirm(self) -> str:
        marker = 'name="confirm" value="'
        at = self.raw.find(marker)
        assert at >= 0, "no confirm on the page"
        return self.raw[at + len(marker):self.raw.index('"', at + len(marker))]


def _parse(raw: bytes) -> Reply:
    head, _, body = bytes(raw).partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = [tuple(line.split(": ", 1)) for line in lines[1:]]
    return Reply(status, [(k, v) for k, v in headers], body.decode())


class Browser:
    """A phone's browser, at `peer`, keeping its sign-in's cookie as Safari would."""

    def __init__(self, handler: type, peer: str = PHONE) -> None:
        self.handler = handler
        self.peer = peer
        self.cookie = ""

    def send(self, method: str, path: str, *, body: bytes = b"",
             headers: dict[str, str] | None = None) -> Reply:
        sent = {"Host": HOST, **({"Cookie": f"{COOKIE}={self.cookie}"} if self.cookie else {}),
                **(headers or {})}
        sent = {k: v for k, v in sent.items() if v is not None}
        if body:
            sent.setdefault("Content-Type", "application/x-www-form-urlencoded")
            sent["Content-Length"] = str(len(body))
        raw = f"{method} {path} HTTP/1.1\r\n" + "".join(
            f"{k}: {v}\r\n" for k, v in sent.items()) + "\r\n"
        conn = _Conn(raw.encode("latin-1") + body)
        self.handler(conn, (self.peer, 50123), SimpleNamespace())
        reply = _parse(bytes(conn.out))
        for key, value in reply.headers:
            if key.lower() == "set-cookie" and value.startswith(f"{COOKIE}="):
                self.cookie = value.split(";")[0].split("=", 1)[1]
        return reply

    def get(self, path: str = "/", **kw: Any) -> Reply:
        return self.send("GET", path, **kw)

    def post(self, path: str, form: dict[str, str], *, origin: str | None = ORIGIN,
             headers: dict[str, str] | None = None) -> Reply:
        extra = {**({"Origin": origin} if origin is not None else {}), **(headers or {})}
        return self.send("POST", path, body=urlencode(form).encode(), headers=extra)

    def start(self, confirm: str, *, bed: str | None = "clear", **kw: Any) -> Reply:
        form = {"confirm": confirm, **({"bed": bed} if bed is not None else {})}
        return self.post("/start", form, **kw)


@dataclass
class FakeStarts:
    """The daemon's two actions for the phone, counted."""

    ready: ReadyToStart | None = field(default_factory=lambda: ReadyToStart(
        job_id="job-1", part="Cable clip", press="word-1", gcode_sha256="a" * 64,
        checks=[Checked("Time and plastic", "18 min, 5.6 cm³", True),
                Checked("Printer", "free, checked just now", True)]))
    calls: list[tuple[str, Armed, str, str]] = field(default_factory=list)

    def ready_to_start(self) -> ReadyToStart | None:
        return self.ready

    def last_phone_start(self) -> str:
        return "Started by you. Disarmed again." if self.calls else ""

    def start_from_phone(self, job_id: str, *, armed: Armed, press: str,
                         gcode_sha256: str) -> PhoneStart:
        self.calls.append((job_id, armed, press, gcode_sha256))
        return PhoneStart(started=True, said="Started by you. Disarmed again.")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def phones(tmp_path: Path) -> Phones:
    return Phones(tmp_path / "phones.sqlite")


@pytest.fixture
def starts() -> FakeStarts:
    return FakeStarts()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def page(phones: Phones, starts: FakeStarts, clock: Clock) -> type:
    handler: type = handler_for(home=home(), phones=phones, starts=starts,
                                confirms=Confirms(clock=clock))
    return handler


def signed_in(page: type, phones: Phones, name: str = "Made-up phone",
              peer: str = PHONE) -> Browser:
    browser = Browser(page, peer)
    code = phones.mint(name)
    reply = browser.post("/pair", {"code": code})
    assert reply.status == 200 and browser.cookie, reply.body
    return browser


# --------------------------------------------------------------------------
# Done when: a confirm starts only its own job, and only once
# --------------------------------------------------------------------------


def test_a_confirm_starts_its_job_once_and_a_replay_byte_for_byte_starts_nothing(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    browser = signed_in(page, phones)
    shown = browser.get("/start")
    assert shown.status == 200 and "Cable clip" in shown.body
    made = shown.confirm()
    first = browser.start(made)
    assert first.status == 200 and "Started by you. Disarmed again." in first.body
    assert len(starts.calls) == 1
    job_id, armed, press, sha = starts.calls[0]
    assert (job_id, press, sha) == ("job-1", "word-1", "a" * 64), "bound to job, word, G-code"
    assert (armed.how, armed.by, armed.where) == ("phone", "Made-up phone", PHONE)
    again = browser.start(made)
    assert again.status == 403 and UNKNOWN.split(":")[0] in again.body
    assert len(starts.calls) == 1, "the same confirm never goes to the gate twice"


def test_each_view_makes_its_own_confirm_and_each_works_once(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    browser = signed_in(page, phones)
    one, two = browser.get("/start").confirm(), browser.get("/start").confirm()
    assert one != two
    assert browser.start(two).status == 200
    assert browser.start(two).status == 403
    assert len(starts.calls) == 1


def test_a_confirm_is_for_its_own_job_word_and_gcode_at_the_gate(tmp_path: Path) -> None:
    """At the gate, a confirm bound to another job, a spent gate word or another slice is
    refused, and nothing is armed."""
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        ready = daemon.ready_to_start()
        assert ready is not None and ready.job_id == job_id
        armed = _armed(job_id)
        other = daemon.start_from_phone(job_id, armed=_armed("another-job"), press=ready.press,
                                        gcode_sha256=ready.gcode_sha256)
        assert not other.started and "isn't for this job" in other.said
        old_word = daemon.start_from_phone(job_id, armed=armed, press="a-spent-word",
                                           gcode_sha256=ready.gcode_sha256)
        assert not old_word.started and "changed since this page was shown" in old_word.said
        resliced = daemon.start_from_phone(job_id, armed=armed, press=ready.press,
                                           gcode_sha256="b" * 64)
        assert not resliced.started and "sliced again" in resliced.said
        assert built == [] and one_start.armed() is None
        assert shop.printer.verbs.count("upload") == 0
        started = daemon.start_from_phone(job_id, armed=armed, press=ready.press,
                                          gcode_sha256=ready.gcode_sha256)
        assert started.started, started.said
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
        # The same confirm's binding again: the gate is gone.
        later = daemon.start_from_phone(job_id, armed=armed, press=ready.press,
                                        gcode_sha256=ready.gcode_sha256)
        assert not later.started
    assert len(built) == 1, "one armed client, for one start"
    assert _uploaded_then_started(shop.printer)


# --------------------------------------------------------------------------
# The whole road, from the phone's page to the printer, in a run the flag didn't arm
# --------------------------------------------------------------------------


def _phone_shop(tmp_path: Path, printer: FramePrinter | None = None,
                ) -> tuple[Shop, OneStart, list[Armed]]:
    shop = Shop(tmp_path)
    if printer is not None:
        shop.printer = printer
    built: list[Armed] = []

    def build(armed: Armed) -> Any:
        # What `print_job.py`'s builder does, with the made-up printer in place of
        # `live_printer`: the token is spent once, as `live_printer` spends it.
        assert spend(armed), "a start handed out once, and spent here"
        built.append(armed)
        return shop.printer

    return shop, OneStart(build), built


def _unarmed(shop: Shop, one_start: OneStart | None, *, flag: bool = False) -> Daemon:
    """A daemon as `print_job.py` makes one with the phone's page on: unarmed by the flag
    unless `flag`, reading the made-up printer for Start's snapshot."""
    return Daemon(
        cfg=CFG, profiles=shop.profiles, workdir=shop.work,
        printer=shop.printer if flag else None, watch_printer=shop.printer,
        slicer=shop.slicer, bridge=lambda cfg: shop.bridge, ledger=Ledger(shop.ledger_path),
        proposer=Proposer(ask=_model, ask_questions=_top_question),
        asker=lambda questions: tuple(Answer(choice=0) for _ in questions),
        reviewer=lambda request: Review(approved=True),
        store_root=shop.store, one_start=one_start,
    )


def _uploaded_then_started(printer: FramePrinter) -> bool:
    verbs = printer.verbs
    return verbs.count("upload") == verbs.count("start") == 1 and (
        verbs.index("upload") < verbs.index("start"))


def _armed(job_id: str, by: str = "Made-up phone") -> Armed:
    return Armed(job_id=job_id, how="phone", by=by, where=PHONE, at="2026-11-23T10:00:00+00:00")


def test_a_phone_on_the_home_wifi_starts_a_ready_job_in_a_run_the_flag_didnt_arm(
    tmp_path: Path,
) -> None:
    shop, one_start, built = _phone_shop(tmp_path)
    phones = Phones(tmp_path / "phones.sqlite")
    with _unarmed(shop, one_start) as daemon:
        page = handler_for(home=home(), phones=phones, starts=daemon)
        browser = signed_in(page, phones, "Sponsor's made-up phone")
        assert "Nothing is ready" in browser.get("/start").body
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        shown = browser.get("/start")
        assert shop.stl.name in shown.body and "I can see the bed is clear" in shown.body
        for label in ("Time and plastic", "Pause", "Spool", "Nozzle", "File and settings",
                      "Printer"):
            assert f"<dt>{label}</dt>" in shown.body
        sliced = daemon._graph.get_state({"configurable": {"thread_id": job_id}}).values["slice"]
        plastic = f"{round(sliced.print_time_s / 60)} min, {sliced.filament_cm3:.1f} cm³"
        if sliced.filament_g is not None:
            plastic += f", {sliced.filament_g:.0f} g"
        assert f"<dt>Time and plastic</dt><dd>{plastic}</dd>" in shown.body
        reply = browser.start(shown.confirm())
        assert reply.status == 200, reply.body
        assert "Started by you. Disarmed again." in reply.body
        result = daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
        assert result.printed and result.error is None
        assert one_start.armed() is None, "disarmed straight after"
    assert [a.job_id for a in built] == [job_id]
    assert _uploaded_then_started(shop.printer)
    with Ledger(shop.ledger_path) as ledger:
        kept = ledger.armed(job_id)
        assert kept is not None and ledger.get(job_id) is not None
    assert (kept.how, kept.by, kept.where) == ("phone", "Sponsor's made-up phone", PHONE)
    assert kept.at


def test_a_phones_start_held_by_a_check_arms_nothing_and_a_new_view_starts_it(
    tmp_path: Path,
) -> None:
    """The checks at Start stay (mqucifer/print-shop#209): a busy printer holds a phone's Start,
    says why on the phone, and nothing is armed. Once it's free, a new view starts it."""
    busy = FramePrinter(before="printing")
    shop, one_start, built = _phone_shop(tmp_path, busy)
    phones = Phones(tmp_path / "phones.sqlite")
    with _unarmed(shop, one_start) as daemon:
        browser = signed_in(handler_for(home=home(), phones=phones, starts=daemon), phones)
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        shown = browser.get("/start")
        assert 'class="bad"' in shown.body, "the printer's row says it's busy"
        held = browser.start(shown.confirm())
        assert held.status == 409 and "Not started:" in held.body
        assert one_start.armed() is None and built == []
        assert "upload" not in shop.printer.verbs
        busy.before = "idle"
        assert browser.get("/start").status == 200
        started = browser.start(browser.get("/start").confirm())
        assert started.status == 200, started.body
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    assert len(built) == 1


# --------------------------------------------------------------------------
# Done when: a bare, retained or replayed MQTT message starts nothing, nor does a card press
# --------------------------------------------------------------------------


def test_in_a_run_the_flag_didnt_arm_no_message_or_card_press_starts_and_none_reads_the_printer(
    tmp_path: Path,
) -> None:
    """The Sponsor's "B" (2026-11-24): a run the flag didn't arm holds the card's Start, and any
    bare, retained or replayed message, before anything is read."""
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        reads = shop.printer.verbs.count("snapshot")
        shop.bridge.press(CFG, "approve")
        assert NOT_ARMED_PHONE in _held(shop)
        word = json.loads(shop.bridge.button(CFG, "approve"))["press"]
        bare = json.dumps({"decision": "approve", "job_id": job_id, "press": word})
        shop.bridge.deliver(command_topic(CFG), bare)
        shop.bridge.deliver(command_topic(CFG), bare)  # replayed
        shop.bridge.deliver(command_topic(CFG), bare, retain=True)
        time.sleep(0.3)
        assert shop.printer.verbs.count("snapshot") == reads, "a broker message reads nothing"
        assert shop.bridge.retained_json(gate_topic(CFG)).get("pending")
        assert "upload" not in shop.printer.verbs and built == []
        assert one_start.armed() is None
        shop.bridge.press(CFG, "reject")
        assert not daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed


@pytest.mark.parametrize("flag", [False, True], ids=["unarmed", "armed-by-the-flag"])
def test_with_the_switch_off_the_cards_start_starts_nothing_in_any_run(
    tmp_path: Path, flag: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CARD_STARTS_UNDER_FLAG` False, the switch's other way: held in every run, and nothing
    read."""
    monkeypatch.setattr(daemon_module, "CARD_STARTS_UNDER_FLAG", False)
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start, flag=flag) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        reads = shop.printer.verbs.count("snapshot")
        shop.bridge.press(CFG, "approve")
        held = _held(shop)
        assert CARD_DOESNT_START in held
        assert shop.printer.verbs.count("snapshot") == reads, "a broker message reads nothing"
        # A bare message with the gate's word, as any client can read it from the button's
        # retained config, and a replay of it: nothing.
        bare = json.dumps({"decision": "approve", "job_id": job_id,
                           "press": json.loads(shop.bridge.button(CFG, "approve"))["press"]})
        shop.bridge.deliver(command_topic(CFG), bare)
        shop.bridge.deliver(command_topic(CFG), bare)
        shop.bridge.deliver(command_topic(CFG), bare, retain=True)
        time.sleep(0.3)
        assert shop.bridge.retained_json(gate_topic(CFG)).get("pending")
        assert "upload" not in shop.printer.verbs and built == []
        assert one_start.armed() is None
        shop.bridge.press(CFG, "reject")
        assert not daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed


def test_no_payload_can_carry_a_phones_confirm(tmp_path: Path) -> None:
    """`armed` comes only from the page, in the process: a payload naming one is read as a bare
    card press."""
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        word = json.loads(shop.bridge.button(CFG, "approve"))["press"]
        shop.bridge.deliver(command_topic(CFG), json.dumps({
            "decision": "approve", "job_id": job_id, "press": word,
            "phone": _armed(job_id).model_dump(), "bed": "on the phone", "by": "on the phone"}))
        assert NOT_ARMED_PHONE in _held(shop), "read as a card press, in a run not armed"
        assert built == [] and "upload" not in shop.printer.verbs
        shop.bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)


def test_the_cards_start_starts_under_the_flag_only(tmp_path: Path) -> None:
    """The Sponsor's "B" (2026-11-24): the card's Start starts in a run the flag armed, kept as
    armed by the flag, and is held in one it didn't."""
    assert daemon_module.CARD_STARTS_UNDER_FLAG is True
    shop, one_start, _ = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        shop.bridge.press(CFG, "approve")
        assert "this run isn't armed" in _held(shop)
        assert "upload" not in shop.printer.verbs
        shop.bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
    (tmp_path / "armed").mkdir()
    armed_run = Shop(tmp_path / "armed")
    with _unarmed(armed_run, None, flag=True) as daemon:
        job_id = daemon.submit(armed_run.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        armed_run.bridge.press(CFG, "approve")
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    with Ledger(armed_run.ledger_path) as ledger:
        kept = ledger.armed(job_id)
    assert kept is not None and kept.how == "flag"


def _held(shop: Shop) -> str:
    wait_until(lambda: bool(shop.bridge.retained_json(gate_topic(CFG)).get("held")))
    return str(shop.bridge.retained_json(gate_topic(CFG))["held"])


# --------------------------------------------------------------------------
# Done when: from outside the home network, or proxied from outside, Start is refused
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("peer", "said"), [
    ("127.0.0.1", THROUGH_THE_MAC),
    (MAC, THROUGH_THE_MAC),
    (ROUTER, THROUGH_THE_ROUTER),
    (HA, THROUGH_HA),
    (AWAY, OUTSIDE),
    (ip(10, 77, 0, 0), OUTSIDE),
    (ip(10, 77, 0, 255), OUTSIDE),
    ("::1", THROUGH_THE_MAC),
    (f"::ffff:{ROUTER}", THROUGH_THE_ROUTER),
], ids=["loopback", "the-macs-own", "the-router", "ha-host", "another-network", "network",
        "broadcast", "ipv6-loopback", "mapped-router"])
def test_start_is_refused_in_words_unless_from_a_phones_own_home_address(
    page: type, phones: Phones, starts: FakeStarts, peer: str, said: str,
) -> None:
    at_home = signed_in(page, phones)
    made = at_home.get("/start").confirm()
    relayed = Browser(page, peer)
    relayed.cookie = at_home.cookie  # the person's own sign-in, carried through a relay
    shown = relayed.get("/start")
    assert "You're away from home" in shown.body and said in shown.body
    assert "Cable clip" in shown.body, "the job can be read"
    assert 'name="confirm"' not in shown.body, "no confirm is made away from home"
    reply = relayed.start(made)
    assert reply.status == 403 and said in reply.body
    assert starts.calls == []


@pytest.mark.parametrize("header", sorted(PROXY_HEADERS) + ["X-Forwarded-For", "X-Original-URL"])
def test_a_request_with_any_proxys_mark_is_refused(
    page: type, phones: Phones, starts: FakeStarts, header: str,
) -> None:
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    reply = browser.start(made, headers={header: PHONE})
    assert reply.status == 403 and "through a proxy" in reply.body
    assert starts.calls == []


def test_a_phone_on_the_home_wifi_isnt_refused(page: type, phones: Phones) -> None:
    assert home().why_not(PHONE, ["Host", "Cookie", "Origin"]) == ""
    assert home().why_not(f"::ffff:{PHONE}", []) == ""


def test_where_the_router_or_ha_cant_be_told_start_is_refused(page: type) -> None:
    assert home(router=None).why_not(PHONE, []) == NO_ROUTER


def test_a_name_that_points_here_isnt_the_pages_own_host(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    """A DNS rebinding: another name for the same address. Refused, and nothing is shown."""
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    for host in ("rebound.example", f"{MAC}:{PORT + 1}", ""):
        got = browser.get("/", headers={"Host": host})
        assert got.status == 421 and "Cable clip" not in got.body
        assert browser.start(made, headers={"Host": host}).status == 421
    assert starts.calls == []


@pytest.mark.parametrize("origin", [None, "null", "http://rebound.example", f"https://{HOST}"])
def test_a_post_from_another_page_is_refused(
    page: type, phones: Phones, starts: FakeStarts, origin: str | None,
) -> None:
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    reply = browser.start(made, origin=origin)
    assert reply.status == 403 and "another page" in reply.body
    assert starts.calls == []
    assert browser.start(made).status == 200, "an Origin refusal doesn't spend the confirm"


def test_the_page_listens_only_on_its_home_address_and_loopback_is_refused_on_a_real_socket(
    phones: Phones, starts: FakeStarts,
) -> None:
    """Over a real socket, on loopback (`listen_on`, as tests only can): the peer is the Mac
    itself, so Start is refused, however the request names its host."""
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    server = make_server(home=home(port=port), phones=phones, starts=starts,
                         listen_on="127.0.0.1")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code = phones.mint("Made-up phone")
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            body = urlencode({"code": code}).encode()
            conn.sendall(
                f"POST /pair HTTP/1.1\r\nHost: {MAC}:{port}\r\nOrigin: http://{MAC}:{port}\r\n"
                "Content-Type: application/x-www-form-urlencoded\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            reply = b""
            while chunk := conn.recv(65536):
                reply += chunk
        assert b" 403 " in reply.split(b"\r\n")[0]
        assert THROUGH_THE_MAC.encode() in reply
        assert phones.all() == [], "nothing paired through the Mac"
    finally:
        server.shutdown()
        server.server_close()


def test_the_page_wont_listen_where_the_mac_hasnt_that_address(phones: Phones) -> None:
    with pytest.raises(PhoneOff, match="away from home, or its address changed"):
        make_server(home=home(), phones=phones, starts=None)


# --------------------------------------------------------------------------
# Done when: an expired or unsigned confirm is refused
# --------------------------------------------------------------------------


def test_an_expired_confirm_is_refused_and_a_new_view_starts(
    page: type, phones: Phones, starts: FakeStarts, clock: Clock,
) -> None:
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    clock.now += CONFIRM_LASTS_S + 1
    reply = browser.start(made)
    assert reply.status == 403 and "That confirm has expired (it lasts 2 minutes)" in reply.body
    assert browser.start(made).status == 403, "an expired one is spent too"
    assert starts.calls == []
    assert browser.start(browser.get("/start").confirm()).status == 200
    assert CONFIRM_LASTS_S == 120.0 and EXPIRED.startswith("That confirm has expired")


def test_signed_out_nothing_is_shown_or_accepted(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    stranger = Browser(page)
    shown = stranger.get("/start")
    assert "Sign in on this phone at the Mac" in shown.body and "Cable clip" not in shown.body
    assert stranger.start(made).status == 403
    stranger.cookie = "a" * 43  # a made-up sign-in
    assert stranger.start(made).status == 403
    assert starts.calls == []
    assert browser.start(made).status == 200, "the stranger didn't spend the person's confirm"


def test_another_phones_confirm_is_refused(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    mine = signed_in(page, phones, "One made-up phone")
    theirs = signed_in(page, phones, "Another made-up phone", peer=ANOTHER_PHONE)
    made = mine.get("/start").confirm()
    reply = theirs.start(made)
    assert reply.status == 403 and "another phone" in reply.body
    assert starts.calls == []
    confirms = Confirms()
    one = confirms.make(phone_hash="mine", phone="p", job_id="j", press="w", gcode_sha256="g",
                        address=PHONE)
    assert "another phone" in str(confirms.spend(one, phone_hash="theirs", address=PHONE))


def test_a_confirm_from_another_address_is_refused(phones: Phones, starts: FakeStarts) -> None:
    """The same phone, its address renewed between the page and the Start: refused, and a new
    view of the page starts."""
    renewed = ip(10, 77, 0, 33)
    page = handler_for(home=home(), phones=phones, starts=starts)
    browser = signed_in(page, phones)
    made = browser.get("/start").confirm()
    browser.peer = renewed
    reply = browser.start(made)
    assert reply.status == 403 and "another address" in reply.body
    assert starts.calls == []
    assert browser.start(browser.get("/start").confirm()).status == 200


def test_a_forgotten_phone_is_signed_out_at_once(
    page: type, phones: Phones, starts: FakeStarts,
) -> None:
    browser = signed_in(page, phones, "Made-up phone")
    made = browser.get("/start").confirm()
    assert phones.forget("Made-up phone")
    assert browser.start(made).status == 403
    assert "Sign in on this phone" in browser.get("/start").body
    assert starts.calls == []


def test_an_unticked_bed_starts_nothing(page: type, phones: Phones, starts: FakeStarts) -> None:
    browser = signed_in(page, phones)
    reply = browser.start(browser.get("/start").confirm(), bed=None)
    assert reply.status == 403 and "I can see the bed is clear" in reply.body
    assert browser.start(browser.get("/start").confirm(), bed="yes").status == 403
    assert starts.calls == []


# --------------------------------------------------------------------------
# Done when: a restart, a failed start and a finished start each leave nothing armed
# --------------------------------------------------------------------------


def test_a_restart_has_nothing_armed_and_knows_no_confirm(
    phones: Phones, starts: FakeStarts,
) -> None:
    before = handler_for(home=home(), phones=phones, starts=starts)
    browser = signed_in(before, phones)
    made = browser.get("/start").confirm()
    after = handler_for(home=home(), phones=phones, starts=starts)  # a new process's
    browser.handler = after
    reply = browser.start(made)
    assert reply.status == 403 and "already used, or isn't one" in reply.body
    assert starts.calls == []
    assert OneStart(lambda armed: FramePrinter()).armed() is None


def test_a_start_that_fails_at_the_printer_leaves_nothing_armed(tmp_path: Path) -> None:
    class Refuses(FramePrinter):
        def start(self, remote_name: str) -> PrinterSnapshot:
            raise RuntimeError("the made-up printer refused")

    shop, one_start, built = _phone_shop(tmp_path, Refuses())
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        ready = daemon.ready_to_start()
        assert ready is not None
        said = daemon.start_from_phone(job_id, armed=_armed(job_id), press=ready.press,
                                       gcode_sha256=ready.gcode_sha256)
        # The phone is told how the start went, not only that it was armed (the review).
        assert not said.started and "refused" in said.said and "Disarmed again." in said.said
        result = daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
        assert not result.printed and "refused" in (result.error or "")
        assert one_start.armed() is None
        assert "refused" in daemon.last_phone_start()
    assert len(built) == 1


def test_the_one_start_is_taken_before_the_printer_is_told_anything(tmp_path: Path) -> None:
    """Disarmed as it's taken: while the upload runs, nothing is armed."""
    seen: list[Armed | None] = []
    holder: list[OneStart] = []

    class Watches(FramePrinter):
        def upload(self, local_path: Path) -> str:
            seen.append(holder[0].armed())
            return super().upload(local_path)

    shop, one_start, _ = _phone_shop(tmp_path, Watches())
    holder.append(one_start)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        ready = daemon.ready_to_start()
        assert ready is not None
        daemon.start_from_phone(job_id, armed=_armed(job_id), press=ready.press,
                                gcode_sha256=ready.gcode_sha256)
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    assert seen == [None]


def test_one_start_is_once_and_for_its_own_job() -> None:
    clients: list[Armed] = []
    def build(armed: Armed) -> FramePrinter:
        clients.append(armed)
        return FramePrinter()

    one = OneStart(build)
    one.arm(_armed("job-a"))
    with pytest.raises(RuntimeError, match="already armed"):
        one.arm(_armed("job-b"))
    assert one.take("job-b") is None, "another job's take disarms, and builds nothing"
    assert one.armed() is None and clients == []
    one.arm(_armed("job-a"))
    taken = one.take("job-a")
    assert taken is not None and taken[1].job_id == "job-a"
    assert one.take("job-a") is None, "once"
    with pytest.raises(RuntimeError, match="only a phone's confirm"):
        one.arm(armed_by_flag("job-a"))
    with pytest.raises(RuntimeError, match="can't arm"):
        OneStart(None).arm(_armed("job-a"))


# --------------------------------------------------------------------------
# Done when: no test can arm
# --------------------------------------------------------------------------


@pytest.fixture
def made_up_printer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A made-up printer's settings, so `live_printer` doesn't read the Mac's."""
    monkeypatch.delenv(arming.ALLOW_HARDWARE_ENV, raising=False)
    monkeypatch.setattr(centauri.PrinterConfig, "from_env", classmethod(
        lambda cls, *a, **k: PrinterConfig(host="made-up-printer", mainboard_id="made-up")))


def test_live_printer_refuses_without_the_flag_or_a_phones_start(made_up_printer: None) -> None:
    with pytest.raises(RuntimeError, match="no phone's confirm armed this start"):
        live_printer()
    with pytest.raises(RuntimeError, match="no phone's confirm"):
        live_printer(armed=_armed("job-a"))  # made by hand: never handed out
    forged = _armed("job-a").model_copy(update={"token": "f" * 32})
    with pytest.raises(RuntimeError, match="no phone's confirm"):
        live_printer(armed=forged)
    with pytest.raises(RuntimeError, match="no phone's confirm"):
        live_printer(armed=armed_by_flag("job-a"))


def test_a_phones_start_builds_one_client_once_and_it_cant_connect_under_tests(
    made_up_printer: None,
) -> None:
    kept: list[Armed] = []

    def build(armed: Armed) -> Any:
        kept.append(armed)
        return live_printer(armed=armed)

    one = OneStart(build)
    one.arm(_armed("job-a"))
    taken = one.take("job-a")
    assert taken is not None
    client = taken[0]
    assert client.enable_control is True  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="no phone's confirm"):
        live_printer(armed=kept[0])  # spent: it never builds a second
    with pytest.raises(HardwareDisarmed):
        client.snapshot()  # conftest's guard: no test opens an armed connection


def test_a_start_builds_one_client_even_where_its_builder_asks_twice(
    made_up_printer: None,
) -> None:
    """`live_printer` spends the start itself: a builder that asked twice gets one client."""

    def build(armed: Armed) -> Any:
        first = live_printer(armed=armed)
        with pytest.raises(RuntimeError, match="no phone's confirm"):
            live_printer(armed=armed)
        return first

    one = OneStart(build)
    one.arm(_armed("job-a"))
    assert one.take("job-a") is not None


def test_a_phones_start_in_a_run_that_cant_arm_from_a_phone_is_held_in_words(
    tmp_path: Path,
) -> None:
    shop = Shop(tmp_path)
    with _unarmed(shop, None) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        ready = daemon.ready_to_start()
        assert ready is not None
        said = daemon.start_from_phone(job_id, armed=_armed(job_id), press=ready.press,
                                       gcode_sha256=ready.gcode_sha256)
        assert not said.started and "can't start a print from a phone" in said.said
        assert "upload" not in shop.printer.verbs
        shop.bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)


# --------------------------------------------------------------------------
# Signing in
# --------------------------------------------------------------------------


def test_a_pairing_code_is_ten_crockford_characters_and_works_once(
    page: type, phones: Phones,
) -> None:
    code = phones.mint("Made-up phone")
    assert len(code) == CODE_LENGTH == 10 and set(code) <= set(ALPHABET)
    assert not set("ILOU") & set(ALPHABET)
    browser = Browser(page)
    assert browser.post("/pair", {"code": f"{code[:5].lower()}-{code[5:]}"}).status == 200
    other = Browser(page, ANOTHER_PHONE)
    assert other.post("/pair", {"code": code}).status == 403
    assert [p.name for p in phones.all()] == ["Made-up phone"]


def test_five_wrong_tries_kill_the_code(page: type, phones: Phones) -> None:
    code = phones.mint("Made-up phone")
    browser = Browser(page)
    wrong = "0000000000" if code != "0000000000" else "1111111111"
    for _ in range(WRONG_TRIES):
        reply = browser.post("/pair", {"code": wrong})
        assert reply.status == 403
    assert "it's dead" in reply.body
    assert browser.post("/pair", {"code": code}).status == 403, "the right one, too late"
    assert phones.all() == [] and not browser.cookie


def test_a_pairing_code_expires(tmp_path: Path) -> None:
    clock = Clock()
    phones = Phones(tmp_path / "phones.sqlite", clock=clock)
    code = phones.mint("Made-up phone")
    clock.now += CODE_LASTS_S + 1
    assert "expired" in str(phones.pair(code))


def test_pairing_from_away_is_refused(page: type, phones: Phones) -> None:
    code = phones.mint("Made-up phone")
    for peer in ("127.0.0.1", ROUTER, HA, AWAY):
        assert Browser(page, peer).post("/pair", {"code": code}).status == 403
    assert phones.all() == []
    assert Browser(page).post("/pair", {"code": code}).status == 200, "not spent by those"


def test_the_mac_keeps_only_hashes(page: type, phones: Phones) -> None:
    code = phones.mint("Made-up phone")
    browser = Browser(page)
    browser.post("/pair", {"code": code})
    held = phones.path.read_bytes()
    assert browser.cookie and browser.cookie.encode() not in held
    assert code.encode() not in held
    assert oct(phones.path.stat().st_mode & 0o777) == "0o600"
    with sqlite3.connect(phones.path) as db:
        (row,) = db.execute("SELECT * FROM phones").fetchall()
    assert browser.cookie not in row


def test_the_sign_in_cookie_is_http_only_and_strict(page: type, phones: Phones) -> None:
    code = phones.mint("Made-up phone")
    reply = Browser(page).post("/pair", {"code": code})
    cookie = reply.header("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    assert len(cookie.split(";")[0].split("=", 1)[1]) >= 43, "256 bits"


# --------------------------------------------------------------------------
# What the page shows, and how it's served
# --------------------------------------------------------------------------


def test_the_page_shows_no_secret_and_cant_be_framed(
    tmp_path: Path, phones: Phones, starts: FakeStarts,
) -> None:
    settings = tmp_path / "made-up.env"
    settings.write_text(f"PHONE_HOME={MAC}/24\nPHONE_ROUTER={ROUTER}\nMQTT_HOST={HA_NAME}\n"
                        "MQTT_PASSWORD=made-up-password\n")
    loaded = Home.from_settings(settings, resolve=_resolve)
    assert loaded is not None and str(loaded.ha) == HA
    page = handler_for(home=loaded, phones=phones, starts=starts)
    browser = signed_in(page, phones)
    for reply in (browser.get("/"), browser.get("/start"), browser.get("/pair"),
                  Browser(page).get("/")):
        for secret in (browser.cookie, "made-up-password", HA_NAME, HA, ROUTER, "PHONE_",
                       "MQTT"):
            assert secret not in reply.body
        assert reply.header("X-Frame-Options") == "DENY"
        assert "frame-ancestors 'none'" in reply.header("Content-Security-Policy")
        assert "script-src" not in reply.header("Content-Security-Policy")
        assert "<script" not in reply.body
        assert reply.header("Cache-Control") == "no-store"
        # Not no-referrer, under which a browser sends a same-origin POST's Origin as "null".
        assert reply.header("Referrer-Policy") == "same-origin"


def test_every_other_method_and_route_is_refused(page: type, phones: Phones) -> None:
    browser = signed_in(page, phones)
    for method in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        assert browser.send(method, "/start").status == 405
    # Start's page is at /start (mqucifer/print-shop#221).
    assert browser.get("/start").status == 200
    assert browser.post("/elsewhere", {"a": "b"}).status == 404


def test_the_part_name_is_escaped(phones: Phones) -> None:
    starts = FakeStarts()
    assert starts.ready is not None
    starts.ready = starts.ready.model_copy(update={"part": "<img src=x onerror=alert(1)>"})
    page = handler_for(home=home(), phones=phones, starts=starts)
    raw = signed_in(page, phones).get("/start").raw
    assert "<img" not in raw and "&lt;img" in raw


# --------------------------------------------------------------------------
# The home network, from the settings
# --------------------------------------------------------------------------


def _settings(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "made-up.env"
    path.write_text(text)
    return path


def test_without_phone_home_the_phones_start_is_off(tmp_path: Path) -> None:
    assert Home.from_settings(_settings(tmp_path, "MQTT_HOST=x\n")) is None


@pytest.mark.parametrize(("given", "said"), [
    (f"{ip(8, 8, 8, 8)}/24", "public address"),
    ("127.0.0.1/8", "loopback"),
    (f"{ip(169, 254, 1, 2)}/16", "isn't an address on a home network"),
    (MAC, "needs its network"),
    ("not-an-address", "isn't an IPv4 address"),
    (f"{ip(10, 77, 0, 0)}/24", "network's own address"),
])
def test_phone_home_must_be_a_home_address_with_its_network(
    tmp_path: Path, given: str, said: str,
) -> None:
    with pytest.raises(HomeRefused, match=said):
        Home.from_settings(_settings(tmp_path, f"PHONE_HOME={given}\n"), router=lambda: ROUTER)


def test_the_router_is_read_from_the_settings_or_the_default_route(tmp_path: Path) -> None:
    named = Home.from_settings(_settings(tmp_path, f"PHONE_HOME={MAC}/24\n"
                                                   f"PHONE_ROUTER={ROUTER}\n"),
                               router=lambda: None)
    assert named is not None and str(named.router) == ROUTER
    routed = Home.from_settings(_settings(tmp_path, f"PHONE_HOME={MAC}/24\n"),
                                router=lambda: ROUTER)
    assert routed is not None and str(routed.router) == ROUTER
    outside = Home.from_settings(_settings(tmp_path, f"PHONE_HOME={MAC}/24\n"),
                                 router=lambda: AWAY)
    assert outside is not None and outside.router is None, "a router off the network isn't it"


# --------------------------------------------------------------------------
# The rest of what's kept, and said
# --------------------------------------------------------------------------


def test_who_armed_is_kept_with_the_row_and_an_older_ledger_gains_the_table(
    tmp_path: Path,
) -> None:
    """On a made-up ledger: a file from before `armed_starts` opens with it, and a row from
    before has none."""
    path = tmp_path / "made-up-ledger.sqlite"
    Ledger(path).close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE armed_starts")
    with Ledger(path) as ledger:
        assert ledger.armed("from-before") is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name = 'armed_starts'").fetchone()


def test_the_terminal_says_where_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _script()
    assert script.start_words(phone=False, here=True) == (
        "Ready to print. Start it here or on the card, or reject it on the card.",
        "  Type start here, or Yes on the card. It waits until you start or reject it.",
    ), "as before mqucifer/print-shop#214, with no phone"
    assert script.start_words(phone=False, here=False)[0] == (
        "Ready to print. Start or reject it on the card.")
    assert script.start_words(phone=True, here=True) == (
        "Ready to print. Start it here, on your phone or on the card, or reject it on the card.",
        "  Type start here, or Start on your phone, or Yes on the card. It waits until you "
        "start or reject it.")
    monkeypatch.setattr(script, "CARD_STARTS_UNDER_FLAG", False)
    assert script.start_words(phone=True, here=False)[0] == (
        "Ready to print. Start it on your phone, or reject it on the card.")
    assert "Nothing here can start it" in script.start_words(phone=False, here=False)[0]
    assert "on the card" not in script.start_words(phone=True, here=True)[1]


def test_the_terminals_start_in_a_run_the_flag_didnt_arm_says_the_phone(tmp_path: Path) -> None:
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        assert daemon.start(job_id, bed_clear=True) == ""
        assert NOT_ARMED_PHONE in _held(shop) and "on your phone" in NOT_ARMED_PHONE
        assert built == [] and "upload" not in shop.printer.verbs
        shop.bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)


def test_the_flags_start_is_kept_as_armed_by_the_flag(tmp_path: Path) -> None:
    shop = Shop(tmp_path)
    with _unarmed(shop, None, flag=True) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        assert daemon.start(job_id, bed_clear=True) == ""
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    with Ledger(shop.ledger_path) as ledger:
        kept = ledger.armed(job_id)
    assert kept is not None and (kept.how, kept.where) == ("flag", "the Mac")
    assert arming.ALLOW_HARDWARE_ENV in kept.by


def test_pairing_is_refused_away_from_the_macs_own_terminal() -> None:
    script = _load_phone_script()

    class Stream(io.StringIO):
        def __init__(self, tty: bool) -> None:
            super().__init__()
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    assert script.at_the_macs_own_terminal(Stream(True), Stream(True), {}) == ""
    assert "isn't a terminal" in script.at_the_macs_own_terminal(Stream(False), Stream(True), {})
    assert "isn't a terminal" in script.at_the_macs_own_terminal(Stream(True), Stream(False), {})
    for var in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        assert "elsewhere" in script.at_the_macs_own_terminal(
            Stream(True), Stream(True), {var: "made-up"})


def test_phone_py_pair_from_a_script_mints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """This test is itself a script: pairing refuses it, and no code waits."""
    settings = _settings(tmp_path, f"PHONE_HOME={MAC}/24\n"
                                   f"PRINT_SHOP_PHONES={tmp_path / 'phones.sqlite'}\n")
    script = _load_phone_script()
    assert script.main(["--settings", str(settings), "pair", "Made-up phone"]) == 2
    assert "pairing is done at the Mac's own terminal" in capsys.readouterr().err
    phones = Phones(tmp_path / "phones.sqlite")
    assert "No code is waiting" in str(phones.pair("0000000000"))
    assert script.main(["--settings", str(settings), "list"]) == 0
    assert script.main(["--settings", str(settings), "forget", "nobody"]) == 1


def _load_phone_script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "phone_script", Path(__file__).parents[1] / "scripts" / "phone.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_confirms_held_are_bounded(clock: Clock) -> None:
    confirms = Confirms(clock=clock)
    first = confirms.make(phone_hash="h", phone="p", job_id="j", press="w", gcode_sha256="g",
                          address=PHONE)
    for _ in range(confirm_module.HELD_AT_MOST):
        confirms.make(phone_hash="h", phone="p", job_id="j", press="w", gcode_sha256="g",
                      address=PHONE)
    assert confirms.spend(first, phone_hash="h", address=PHONE) == UNKNOWN


# --------------------------------------------------------------------------
# From the cold security review: the race, the outcome, Home Assistant's host, the port
# --------------------------------------------------------------------------


def test_port_80_and_an_unfound_ha_host_are_refused(tmp_path: Path) -> None:
    with pytest.raises(HomeRefused, match="can't be 80"):
        Home.from_settings(_settings(tmp_path, f"PHONE_HOME={MAC}/24\nPHONE_PORT=80\n"),
                           router=lambda: ROUTER)
    with pytest.raises(HomeRefused, match="Home Assistant's host"):
        Home.from_settings(_settings(tmp_path, f"PHONE_HOME={MAC}/24\nMQTT_HOST=nowhere\n"),
                           router=lambda: ROUTER, resolve=_resolve)
    named = Home.from_settings(
        _settings(tmp_path, f"PHONE_HOME={MAC}/24\nMQTT_HOST=nowhere\nPHONE_HA_HOST={HA_NAME}\n"),
        router=lambda: ROUTER, resolve=_resolve)
    assert named is not None and str(named.ha) == HA


def test_a_phones_start_its_phone_stopped_waiting_for_never_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review's race: checks that run past the phone's wait. The phone is told it was
    withdrawn, and the gate, coming to it after, starts nothing from it."""
    release = threading.Event()

    class Slow(FramePrinter):
        def snapshot(self) -> PrinterSnapshot:
            if threading.current_thread().name != "MainThread" and not release.is_set():
                release.wait(WAIT_S)
            return super().snapshot()

    monkeypatch.setattr(daemon_module, "_CHECKED_WITHIN_S", 0.3)
    shop, one_start, built = _phone_shop(tmp_path, Slow())
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        release.set()
        ready = daemon.ready_to_start()
        assert ready is not None
        release.clear()
        said = daemon.start_from_phone(job_id, armed=_armed(job_id), press=ready.press,
                                       gcode_sha256=ready.gcode_sha256)
        assert not said.started and "withdrawn" in said.said
        release.set()
        time.sleep(0.5)
        assert built == [] and one_start.armed() is None
        assert "upload" not in shop.printer.verbs
        assert shop.bridge.retained_json(gate_topic(CFG)).get("pending"), "the gate still waits"
        shop.bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)


def test_two_phones_starts_at_one_gate_start_once_and_the_other_is_told(tmp_path: Path) -> None:
    shop, one_start, built = _phone_shop(tmp_path)
    with _unarmed(shop, one_start) as daemon:
        job_id = daemon.submit(shop.stl)
        daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
        ready = daemon.ready_to_start()
        assert ready is not None
        said: list[PhoneStart] = []

        def press() -> None:
            said.append(daemon.start_from_phone(job_id, armed=_armed(job_id), press=ready.press,
                                                gcode_sha256=ready.gcode_sha256))

        both = [threading.Thread(target=press) for _ in range(2)]
        for each in both:
            each.start()
        for each in both:
            each.join(WAIT_S)
        assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    assert sorted(p.started for p in said) == [False, True]
    assert len(built) == 1 and shop.printer.verbs.count("start") == 1
