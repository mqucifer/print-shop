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
