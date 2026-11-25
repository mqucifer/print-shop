"""A menu bar on every signed-in page of the phone's (mqucifer/print-shop#289).

Queue · Finished · Filaments in fixed places, and Start last, filled, only while a job waits at
the gate. The page you're on is marked. Signed out or away from home, there's no bar. The page is
the real request handler through a fake socket; the shop is `test_phone_screens`'s made-up one.
No test arms.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from print_shop.phone.phones import Phones
from print_shop.phone.routes import ROUTES
from print_shop.phone.server import handler_for
from tests import test_phone_screens as screens
from tests.test_phone_screens import Page, _finished, _paths, _ready
from tests.test_phone_start import AWAY, Browser, FakeStarts, home, signed_in

#: `test_phone_screens`'s made-up shop and its page, as fixtures here.
made, phone_page = screens.made, screens.page

BAR = '<nav class="bar"'


def marked(raw: str) -> list[str]:
    """The bar's items marked as the page you're on."""
    bar = raw.split(BAR, 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', bar)


def items(raw: str) -> int:
    """How many places the bar has: always four, Start's empty while nothing waits."""
    bar = raw.split(BAR, 1)[1].split("</nav>", 1)[0]
    return len(re.findall(r"<a |<span ", bar))


SECTION_HREF = {"queue": "/", "finished": "/runs", "filaments": "/filaments", "start": "/start"}


def test_every_signed_in_page_in_the_table_has_the_bar_with_its_own_marked(
    phone_page: Page,
) -> None:
    job_id = _ready(phone_page, "bracket")
    _finished(phone_page.made.ledger, "run-1")
    phone = phone_page.phone()
    seen = 0
    for (method, path), route in zip(_paths(job_id, "run-1"), ROUTES, strict=True):
        if method != "GET" or route.signed_out:
            continue
        reply = phone.get(path)
        assert BAR in reply.raw, (path, reply.status)
        assert marked(reply.raw) == [SECTION_HREF[route.section]], path
        assert items(reply.raw) == 4, path
        seen += 1
    assert seen >= 15, "the table's signed-in pages, each"


def test_start_is_in_the_bar_only_while_a_job_waits_at_the_gate(tmp_path: Path) -> None:
    phones = Phones(tmp_path / "phones.sqlite")
    starts = FakeStarts()
    phone = signed_in(handler_for(home=home(), phones=phones, starts=starts), phones)
    waiting = phone.get("/runs").raw
    assert '<a href="/start" class="start">Start</a>' in waiting
    starts.ready = None
    quiet = phone.get("/runs").raw
    assert 'href="/start"' not in quiet.split("</nav>")[0]
    assert items(quiet) == 4 == items(waiting), "the others keep their places"


def test_the_bar_reads_the_daemons_gate_never_the_printer(tmp_path: Path) -> None:
    class Gated(FakeStarts):
        gate: dict[str, str] | None = None

        def pending_gate(self) -> dict[str, str] | None:
            return self.gate

        def ready_to_start(self):  # type: ignore[no-untyped-def]
            raise AssertionError("the bar never reads what Start checks")

    phones = Phones(tmp_path / "phones.sqlite")
    starts = Gated()
    phone = signed_in(handler_for(home=home(), phones=phones, starts=starts), phones)
    assert 'href="/start"' not in phone.get("/runs").raw.split("</nav>")[0]
    starts.gate = {"gate": "gate.before_print", "job_id": "j1"}
    assert '<a href="/start" class="start">Start</a>' in phone.get("/runs").raw
    starts.gate = {"gate": "gate.mesh", "job_id": "j1"}
    assert 'href="/start"' not in phone.get("/runs").raw.split("</nav>")[0]


@pytest.mark.parametrize("path", ["/", "/runs", "/filaments", "/start", "/pair"])
def test_signed_out_and_away_pages_have_no_bar(tmp_path: Path, path: str) -> None:
    phones = Phones(tmp_path / "phones.sqlite")
    handler = handler_for(home=home(), phones=phones, starts=FakeStarts())
    assert BAR not in Browser(handler).get(path).raw, "signed out"
    at_home = signed_in(handler, phones)
    away = Browser(handler, AWAY)
    away.cookie = at_home.cookie
    assert BAR not in away.get(path).raw, "away from home"


def test_starts_own_page_marks_start(tmp_path: Path) -> None:
    phones = Phones(tmp_path / "phones.sqlite")
    phone = signed_in(handler_for(home=home(), phones=phones, starts=FakeStarts()), phones)
    raw = phone.get("/start").raw
    assert marked(raw) == ["/start"] and 'class="start on"' in raw


def test_a_lone_job_at_its_questions_has_no_start_in_the_bar(tmp_path: Path) -> None:
    """print-shop-ux's check: a lone job can't be at its questions and at the gate at once, so
    the bar has no Start while it asks. It reads the run's own gate, which is shut."""
    from print_shop.phone.one_job import OneJob  # noqa: PLC0415
    from print_shop.phone.shop import Shop  # noqa: PLC0415
    from tests.test_phone_one_job import JOB, Daemonish, Mac, ask, at_the_mac  # noqa: PLC0415
    from tests.test_phone_one_job import Rig as OneRig  # noqa: PLC0415
    from tests.test_queue import wait_until  # noqa: PLC0415

    mac = Mac()
    said: list[str] = []
    one = OneJob(part="bracket.stl", write=said.append, races=True, keys_ready=mac.keys_ready,
                 read_line=mac.read_line, forget=mac.forget)
    daemon = Daemonish()  # its gate is None: nothing waits at it
    one.daemon = daemon
    shop = Shop(tmp_path / "ledger.sqlite", queue=one, daemon=daemon)
    phones = Phones(tmp_path / "phones.sqlite")
    handler = handler_for(home=home(), phones=phones, starts=daemon, shop=shop)  # type: ignore[arg-type]
    lone = OneRig(one, mac, said, shop, handler, phones)
    thread, _ = at_the_mac(lone, ask(lone))
    wait_until(lambda: one.asked(JOB) is not None)
    raw = signed_in(handler, phones).get("/").raw
    assert "Answer" in raw and 'href="/start"' not in raw.split("</nav>")[0]
    mac.type("1", "1")
    thread.join(5)
