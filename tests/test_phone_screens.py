"""The phone's screens besides Start: Main, a job's page and Finished (mqucifer/print-shop#221).

Every service is a fake: the queue and daemon are `test_queue`'s made-up shop (a fake printer,
broker, Orca and model), the ledger is made up, and the page is the real request handler driven
through a fake socket, with the peer address the socket would give (`test_phone_start`). No test
arms, and no request but #214's confirm reaches the gate.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from print_shop.inventory import Pick
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.kept import Waits
from print_shop.orchestrator.print_queue import Adding, PrintQueue
from print_shop.phone import server as server_module
from print_shop.phone.confirm import Confirms
from print_shop.phone.phones import Phones
from print_shop.phone.routes import ARMS, ROUTES
from print_shop.phone.server import handler_for
from print_shop.phone.shop import Shop as PhoneShop
from print_shop.setup.pauses import PauseFor
from print_shop.setup.proposal import Change, Source
from print_shop.setup.reading import (
    FeatureReading,
    Flag,
    LayerTime,
    PauseReading,
    SeamReading,
    SliceReading,
)
from tests.test_ledger import record
from tests.test_phone_start import (
    AWAY,
    HA,
    MAC,
    ROUTER,
    Browser,
    Clock,
    FakeStarts,
    home,
    signed_in,
)
from tests.test_queue import Shop as QueueShop
from tests.test_queue import wait_until

# --------------------------------------------------------------------------
# A made-up shop, its queue, and the phone's page over them
# --------------------------------------------------------------------------


@dataclass
class Page:
    """The phone's page over a made-up shop: its handler, the phone's shop, and the fakes."""

    handler: type
    shop: PhoneShop
    phones: Phones
    starts: FakeStarts
    queue: PrintQueue
    daemon: Daemon
    made: QueueShop
    jobs: dict[str, str] = field(default_factory=dict)

    def phone(self) -> Browser:
        return signed_in(self.handler, self.phones)


def press(phone: Browser, path: str, **kw: Any) -> Any:
    """A button with no field of its own, as Safari posts it: the form's type, and no body."""
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": "0",
               **kw.pop("headers", {})}
    return phone.post(path, {}, headers=headers, **kw)


def ticked(raw: str) -> bool:
    """Whether any choice on the page is pre-selected."""
    return re.search(r"<input[^>]*\bchecked\b", raw) is not None


@pytest.fixture
def made(tmp_path: Path) -> Iterator[QueueShop]:
    found = QueueShop(tmp_path)
    yield found
    found.ledger.close()


@pytest.fixture
def page(made: QueueShop, tmp_path: Path) -> Iterator[Page]:
    phones = Phones(tmp_path / "phones.sqlite")
    starts = FakeStarts()
    with ExitStack() as stack:
        daemon = stack.enter_context(made.daemon())
        queue = stack.enter_context(made.queue(daemon))
        shop = PhoneShop(made.ledger_path, queue=queue, daemon=daemon)
        handler = handler_for(home=home(), phones=phones, starts=starts,
                              confirms=Confirms(clock=Clock()), shop=shop)
        yield Page(handler, shop, phones, starts, queue, daemon, made)


def _ready(page: Page, name: str) -> str:
    job_id = page.queue.add([Adding(page.made.part(name))])[0][0]
    page.made.ready(page.queue, job_id)
    return job_id


def _finished(ledger: Ledger, job_id: str, *, note: str = "") -> None:
    ledger.record_start_requested(record(job_id))
    ledger.record_started(job_id)
    ledger.record_ended(job_id, 9)


# --------------------------------------------------------------------------
# Done when: no request but #214's confirm can arm, over every route
# --------------------------------------------------------------------------


def test_only_start_arms_and_every_route_answers_through_the_table() -> None:
    assert [(r.method, r.path) for r in ARMS] == [("POST", "/start")]
    answered = {name.removeprefix("_r_") for name, _ in
                inspect.getmembers(server_module._Handler, inspect.isfunction)
                if name.startswith("_r_")}
    assert answered == {r.name for r in ROUTES}, "a route's handler is in the table, and only"
    # Only Start's handler calls the gate.
    for route in ROUTES:
        source = inspect.getsource(getattr(server_module._Handler, f"_r_{route.name}"))
        assert ("start_from_phone" in source) is route.arms, route


def _paths(job_id: str, run_id: str) -> list[tuple[str, str]]:
    """Every route in the table, with a real job's and a real run's id where it takes one."""
    out = []
    for route in ROUTES:
        path = route.path.replace(r"(?P<id>[A-Za-z0-9_-]{1,100})",
                                  run_id if route.path.startswith("/run") else job_id)
        path = re.sub(r"\(\?P<verb>[^)]*\)", "hold", path)
        out.append((route.method, path))
    return out


def test_no_route_but_starts_confirm_reaches_the_gate(page: Page) -> None:
    job_id = _ready(page, "bracket")
    _finished(page.made.ledger, "run-1")
    phone = page.phone()
    confirm = phone.get("/start").confirm()
    form = {"confirm": confirm, "bed": "clear", "sure": "yes", "result": "success",
            "symptom": "none"}
    for method, path in _paths(job_id, "run-1"):
        if path == "/start":
            continue
        reply = phone.get(path) if method == "GET" else phone.post(path, form)
        assert reply.status < 500, (method, path, reply.body)
        assert page.starts.calls == [], f"{method} {path} reached the gate"
    for verb in ("release", "first", "remove"):
        phone.post(f"/job/{job_id}/{verb}", form)
        assert page.starts.calls == []
    assert phone.start(confirm).status == 200
    assert len(page.starts.calls) == 1, "Start alone did"
    assert page.made.printer.verbs.count("start") == 0, "and nothing here reached the printer"


# --------------------------------------------------------------------------
# Done when: signed out, or from outside the home network, nothing is shown or accepted
# --------------------------------------------------------------------------


@pytest.mark.parametrize("peer", [AWAY, ROUTER, HA, MAC, "127.0.0.1"])
def test_from_outside_the_home_network_nothing_is_shown_or_done(page: Page, peer: str) -> None:
    job_id = _ready(page, "bracket")
    _finished(page.made.ledger, "run-1")
    at_home = page.phone()
    away = Browser(page.handler, peer)
    away.cookie = at_home.cookie  # the phone's own sign-in, from elsewhere
    for method, path in _paths(job_id, "run-1"):
        if path in ("/start", "/pair"):
            continue  # #214's own, tested there
        reply = away.get(path) if method == "GET" else away.post(
            path, {"sure": "yes", "result": "success", "symptom": "none"})
        assert "bracket" not in reply.body and "cube" not in reply.body, (method, path)
        assert "away from home" in reply.body, (method, path)
    kept = page.queue.find(job_id[:8])
    assert kept is not None and not kept.held
    row = page.made.ledger.get("run-1")
    assert row is not None and row.result is None


def test_signed_out_nothing_is_shown_or_done(page: Page) -> None:
    job_id = _ready(page, "bracket")
    _finished(page.made.ledger, "run-1")
    stranger = Browser(page.handler)
    for cookie in ("", "made-up-cookie"):
        stranger.cookie = cookie
        for method, path in _paths(job_id, "run-1"):
            if path in ("/start", "/pair"):
                continue
            reply = stranger.get(path) if method == "GET" else stranger.post(
                path, {"sure": "yes", "result": "success", "symptom": "none"})
            assert "bracket" not in reply.body and "cube" not in reply.body, (method, path)
            assert "Not signed in" in reply.body, (method, path)
    kept = page.queue.find(job_id[:8])
    assert kept is not None and not kept.held
    row = page.made.ledger.get("run-1")
    assert row is not None and row.result is None


def test_a_post_from_another_page_does_nothing(page: Page) -> None:
    job_id = _ready(page, "bracket")
    phone = page.phone()
    for origin in (None, "null", "http://example.invalid"):
        reply = press(phone, f"/job/{job_id}/hold", origin=origin)
        assert reply.status == 403
    job = page.queue.find(job_id[:8])
    assert job is not None and not job.held


# --------------------------------------------------------------------------
# Main, a job, and the queue's own actions
# --------------------------------------------------------------------------


def test_main_shows_whats_ready_by_spool_and_whats_getting_ready(page: Page) -> None:
    ready = _ready(page, "bracket")
    page.queue.add([Adding(page.made.part("hook"))])
    phone = page.phone()
    shown = phone.get("/").body
    assert "Ready. They wait until you start them." in shown
    assert "white Elegoo PLA, loaded" in shown
    assert "bracket" in shown and "Next" in shown
    assert "Sent to you, or waiting on you" in shown
    assert "hook" in shown, "the other is getting ready, or waits on you"
    assert f"/job/{ready}" in phone.get("/").raw or "/start" in phone.get("/").raw
    assert "Finished" in shown


def test_hold_let_go_and_first_are_the_queues_own(page: Page) -> None:
    one = _ready(page, "bracket")
    two = _ready(page, "hook")
    phone = page.phone()
    held = press(phone, f"/job/{two}/hold")
    assert held.status == 303 and held.header("Location") == f"/job/{two}?said=hold"
    job = page.queue.find(two[:8])
    assert job is not None and job.held
    assert "Held:" in phone.get(f"/job/{two}?said=hold").body
    press(phone, f"/job/{two}/release")
    job = page.queue.find(two[:8])
    assert job is not None and not job.held
    press(phone, f"/job/{two}/first")
    assert [j.job_id for j in page.queue.jobs()][:2] == [two, one]


def test_remove_asks_once(page: Page) -> None:
    job_id = _ready(page, "bracket")
    phone = page.phone()
    asked = phone.get(f"/job/{job_id}/remove")
    assert "Remove bracket.stl?" in asked.body and "unprinted" in asked.body
    unasked = press(phone, f"/job/{job_id}/remove")
    assert unasked.status == 400 and page.queue.find(job_id[:8]) is not None
    done = phone.post(f"/job/{job_id}/remove", {"sure": "yes"})
    assert done.status == 303 and done.header("Location") == "/"
    wait_until(lambda: page.queue.find(job_id[:8]) is None)
    assert page.made.waits(job_id) is None


def test_a_job_that_needs_you_says_where_to_answer_it(page: Page) -> None:
    job_id = page.queue.add([Adding(page.made.part("bracket"))])[0][0]
    wait_until(lambda: page.queue.asked(job_id) is not None)
    phone = page.phone()
    assert "Answer" in phone.get("/").body
    shown = phone.get(f"/job/{job_id}").body
    assert "It needs you" in shown and "answer 1" in shown


def test_a_job_not_in_the_queue_is_said_so(page: Page) -> None:
    assert page.phone().get("/job/no-such-job").status == 404


def test_main_without_a_queue_says_it_runs_at_the_mac(tmp_path: Path) -> None:
    phones = Phones(tmp_path / "phones.sqlite")
    starts = FakeStarts()
    handler = handler_for(home=home(), phones=phones, starts=starts,
                          shop=PhoneShop(tmp_path / "ledger.sqlite"))
    shown = signed_in(handler, phones).get("/")
    assert "The queue isn't running" in shown.body
    assert "Cable clip" in shown.body and "/start" in shown.raw, "the job offered links to Start"


def test_printing_now_says_whats_left_and_the_pause_ahead(page: Page) -> None:
    ledger = page.made.ledger
    reading = SliceReading(
        layers=(LayerTime(n=1, z=0.2, time_s=600.0),), features=(),
        seam=SeamReading(seams=0, stacked=0.0),
        pauses=(PauseReading(before_layer=12, z=2.4, after_s=960.0, kind=PauseFor.INSERT),))
    ledger.record_start_requested(record("printing").model_copy(
        update={"print_time_s": 1800, "reading": reading}))
    ledger.record_started("printing")
    row = ledger.get("printing")
    assert row is not None and row.started_at is not None
    started = datetime.fromisoformat(row.started_at)
    page.shop.now = lambda: started + timedelta(minutes=10)
    shown = page.phone().get("/").body
    assert "Printing now" in shown and "cube.stl" in shown
    assert "About 20 min left." in shown
    assert "Pauses in 6 min for an insert. Be at the printer." in shown


# --------------------------------------------------------------------------
# Finished, and recording an outcome by the card's own Record
# --------------------------------------------------------------------------


def test_finished_lists_runs_newest_first_and_finds_them(page: Page) -> None:
    ledger = page.made.ledger
    _finished(ledger, "run-old")
    _finished(ledger, "run-new")
    ledger.record_answer("run-old", _answer("failed", "warping", "lifted at the long end"))
    phone = page.phone()
    shown = phone.get("/runs").raw
    assert shown.index("/run/run-new") < shown.index("/run/run-old")
    assert "Failed, warping" in phone.get("/runs").body
    assert "Your note: lifted at the long end" in phone.get("/runs").body
    found = phone.get("/runs?find=lifted").raw
    assert "/run/run-old" in found and "/run/run-new" not in found
    assert "No finished run has" in phone.get("/runs?find=nothing-like-it").body


def _answer(result: str, symptom: str, note: str = "") -> Any:
    from print_shop.ledger import Answer, Result, Symptom  # noqa: PLC0415

    return Answer(result=Result(result), symptom=Symptom(symptom), note=note)


def test_an_outcome_recorded_on_the_phone_is_the_cards_record(page: Page) -> None:
    _finished(page.made.ledger, "run-1")
    phone = page.phone()
    assert "Record" in phone.get("/").body, "Main asks for it"
    form = phone.get("/run/run-1")
    assert "How did it come out?" in form.body and not ticked(form.raw)
    done = phone.post("/run/run-1/outcome",
                      {"result": "success", "symptom": "stringing", "note": "a little"})
    assert done.status == 303, done.body
    row = page.made.ledger.get("run-1")
    assert row is not None and (row.result, row.symptom, row.note) == (
        "success", "stringing", "a little")
    assert "Success, with stringing" in phone.get("/run/run-1?said=recorded").body
    again = phone.post("/run/run-1/outcome", {"result": "failed", "symptom": "warping"})
    assert again.status == 409 and "recorded already" in again.body
    row = page.made.ledger.get("run-1")
    assert row is not None and row.result == "success", "the first answer stands"


def test_an_outcome_that_doesnt_add_up_is_refused_in_words(page: Page) -> None:
    _finished(page.made.ledger, "run-1")
    reply = page.phone().post("/run/run-1/outcome", {"result": "failed", "symptom": "none"})
    assert reply.status == 409 and "Not recorded" in reply.body
    row = page.made.ledger.get("run-1")
    assert row is not None and row.result is None


@dataclass
class PickingDaemon:
    """A daemon whose outcome offers a swap's pick, counted."""

    offered: dict[str, Pick]
    recorded: list[dict[str, Any]] = field(default_factory=list)

    def prepared(self, job_id: str) -> dict[str, Any]:
        return {}

    def outcome_choices(self, job_id: str) -> dict[str, Pick]:
        return dict(self.offered)

    def record_outcome(self, job_id: str, **given: Any) -> str:
        self.recorded.append(given)
        return ""


def test_after_a_swap_the_pick_says_both_halves_and_none_is_preselected(
    tmp_path: Path,
) -> None:
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        _finished(ledger, "run-1")
    words = "spool #2, Elegoo PLA, black is on now, and that print is over"
    daemon = PickingDaemon({words: Pick(2, 5)})
    phones = Phones(tmp_path / "phones.sqlite")
    handler = handler_for(home=home(), phones=phones, starts=FakeStarts(),
                          shop=PhoneShop(tmp_path / "ledger.sqlite", daemon=daemon))
    phone = signed_in(handler, phones)
    shown = phone.get("/run/run-1")
    assert words in shown.body and not ticked(shown.raw)
    picked = {"result": "success", "symptom": "none", "pick": words, "picked_0": "2:5"}
    daemon.offered = {words: Pick(2, 6)}  # a load since the page was shown
    stale = phone.post("/run/run-1/outcome", picked)
    assert stale.status == 409 and "changed since" in stale.body
    assert daemon.recorded == []
    daemon.offered = {words: Pick(2, 5)}
    assert phone.post("/run/run-1/outcome", picked).status == 303
    assert daemon.recorded[0]["pick"] == words and daemon.recorded[0]["picked"] == Pick(2, 5)


# --------------------------------------------------------------------------
# Done when: each screen reads at 390 px with no sideways scroll
# --------------------------------------------------------------------------


def test_each_screen_is_built_for_a_phones_width(page: Page) -> None:
    """What can be checked without a browser: the viewport, nothing wider than a phone, long
    words wrap. A headless browser's measure at 390 px is in the PR's proofs."""
    job_id = _ready(page, "a-part-with-a-very-long-name-that-has-no-spaces-in-it-at-all")
    _finished(page.made.ledger, "run-1")
    phone = page.phone()
    for path in ("/", f"/job/{job_id}", f"/job/{job_id}/remove", "/runs", "/run/run-1"):
        raw = phone.get(path).raw
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in raw, path
        assert "<table" not in raw, path
        widths = [int(w) for w in re.findall(r"(?<!max-)width:\s*(\d+)px", raw)]
        assert all(w <= 358 for w in widths), (path, widths)
        assert "overflow-wrap: anywhere" in raw, path


def test_ready_jobs_waiting_for_their_first_are_counted_once(page: Page) -> None:
    added = page.queue.add([Adding(page.made.part("clip"), copies=3)])[0]
    page.made.ready(page.queue, added[0])
    wait_until(lambda: all(page.made.waits(j) is Waits.READY for j in added))
    assert "clip.stl × 3" in page.phone().get("/").body


# --------------------------------------------------------------------------
# What the slice shows
# --------------------------------------------------------------------------


def _reading() -> tuple[SliceReading, tuple[Flag, ...]]:

    layers = tuple(LayerTime(n=n, z=round(0.2 * n, 2), time_s=30.0 + n) for n in range(1, 26))
    return SliceReading(
        layers=layers,
        features=(
            FeatureReading(kind="Outer wall", time_s=754.0, area_mm2=13576.0, layers=25,
                           from_z=0.2, to_z=5.0),
            FeatureReading(kind="Bridge", time_s=31.0, area_mm2=212.0, layers=1, from_z=4.2,
                           to_z=4.2),
            FeatureReading(kind="travel", time_s=60.0, area_mm2=0.0, layers=25, from_z=0.2,
                           to_z=5.0),
        ),
        seam=SeamReading(seams=169, stacked=0.79, at=(175.0, 49.0), at_layers=9),
        settings={"seam_position": "aligned", "overhang_fan_speed": "10"},
        pauses=(PauseReading(before_layer=21, z=4.2, after_s=1534.0, after_z=4.0,
                             kind=PauseFor.INSERT),),
    ), (
        Flag(id="layer_time", reading="Layer 20 takes 24 s, the shortest, just before the pause."),
        Flag(id="made_up", reading="the bridge is short",
             change=Change(key="bridge_flow", value="0.9", preset="1", source=Source.READING,
                           reason="A made-up reason.", knowledge="made-up")),
    )


def test_what_the_slice_shows_has_every_figure_the_review_reads(tmp_path: Path) -> None:
    from print_shop.phone.slice import slice_page  # noqa: PLC0415
    from print_shop.setup.screen import clock  # noqa: PLC0415

    reading, flags = _reading()
    shown = slice_page(part="tray.stl", reading=reading, flags=flags, print_time_s=2280,
                       grams=24.0, here="/run/r/slice", back="/run/r", back_words="Back",
                       layer=21)
    text = shown.replace("&#x27;", "'")
    assert "tray.stl. 38 min. 25 layers. 24 g." in text
    assert text.count('href="/run/r/slice?layer=') == 25 + 2, "a bar a layer, and the arrows"
    assert text.count('class="gap"') == 1, "the pause, a gap"
    assert "the gap is your pause, 25 min 34 s in" in text
    assert "Layer 21, z 4.2 mm: 51 s. The first layer after the pause." in text
    assert "Its features: Outer wall, Bridge." in text
    for kind, took, area, z in (("Outer wall", clock(754), "13,576 mm²", "z 0.2 to 5"),
                                ("Bridge", clock(31), "212 mm²", "z 4.2")):
        assert kind in text and took in text and area in text and z in text
    assert "travel" not in text.split("Where the time goes")[1], "as the review leaves it out"
    assert "Bridge starts on layer 21, the first after the pause, at z 4.2." in text
    assert "Layer 20 takes 24 s, the shortest, just before the pause." in text
    assert "A made-up reason. Read: the bridge is short" in text
    assert "aligned. 169 loop starts, 79% stacked: scattered. Most at x 175 y 49 (9 layers)." \
        in text
    assert "25 layers, 31 s to 55 s each, 43 s typical. The shortest is layer 1" in text
    assert "<script" not in text


def test_a_failed_runs_slice_opens_from_finished(page: Page) -> None:
    reading, flags = _reading()
    ledger = page.made.ledger
    ledger.record_start_requested(record("failed-run").model_copy(
        update={"reading": reading, "flags": flags}))
    ledger.record_started("failed-run")
    ledger.record_ended("failed-run", 9)
    ledger.record_answer("failed-run", _answer("failed", "adhesion"))
    phone = page.phone()
    assert "/run/failed-run/slice" in phone.get("/run/failed-run").raw
    shown = phone.get("/run/failed-run/slice?layer=3")
    assert shown.status == 200 and "Layer 3, z 0.6 mm" in shown.body
    assert phone.get("/run/failed-run/slice?layer=x").status == 200


def test_a_ready_jobs_slice_opens_from_its_page(page: Page) -> None:
    job_id = _ready(page, "bracket")
    phone = page.phone()
    assert f"/job/{job_id}/slice" in phone.get(f"/job/{job_id}").raw
    shown = phone.get(f"/job/{job_id}/slice")
    assert shown.status == 200 and "Time on each layer" in shown.body
    assert "Back to the job" in shown.body
