"""After a print with a swap, a person says what's on (mqucifer/print-shop#218, "A").

print-shop never moves the stock by itself: from a swap's start request what's on isn't known,
and only a person's word that says both halves clears that ("that print is over, and this spool
is on the printer now"). All on fakes and a made-up stock, as in `test_pause_purpose`.
"""

from __future__ import annotations

import contextlib
import itertools
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from print_shop.core.mqtt import (
    gate_topic,
    outcome_result_topic,
    outcome_spool_select,
    outcome_spool_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
)
from print_shop.inventory import Inventory, InventoryError, SpoolUse
from print_shop.ledger import Ledger, LedgerError, Row
from print_shop.orchestrator import daemon as daemon_module
from print_shop.orchestrator import spools as after_print
from print_shop.orchestrator.outcome import OutcomeDesk
from print_shop.runtime import describe
from print_shop.setup import pauses
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.reading import PauseReading
from tests.fakes import FramePrinter, MemoryBridge
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_pause_purpose import (
    BLACK,
    CLEAR,
    WHITE,
    Shop,
    answered_with,
    cards,
    ends,
    kept,
    not_known,
    over_on,
    reject,
    sent_unconfirmed,
    spools,
    starts,
    swap_started,
)
from tests.test_runtime import settings  # noqa: F401  (the fixture)
from tests.test_stock import _load

SWAP = "colour_change=2 from #1 to #2"
ON_WHITE = over_on("spool #1, Elegoo PLA, white")
ON_BLACK = over_on("spool #2, Elegoo PLA, black")


@pytest.fixture
def shop(tmp_path: Path) -> Shop:
    return Shop(tmp_path)


def unknown_of(shop: Shop) -> str:
    """The job since which what's on isn't known, or ""."""
    with Inventory(shop.path) as stock:
        found = stock.unknown("cc1")
    return found.job_id if found is not None else ""


def ended_with(row: Row | None) -> int | None:
    assert row is not None
    return row.ended_with.spool_id if row.ended_with is not None else None


# 1. From a swap's start request it isn't known, and nothing seen makes it known
# --------------------------------------------------------------------------


def test_after_a_swap_print_the_stock_says_it_doesnt_know_and_start_holds(shop: Shop) -> None:
    """The issue's Done when, as amended."""
    ran = shop.run(SWAP, "ok")

    assert ran.result.printed, ran.result.error
    assert shop.loaded() == WHITE, "the stock is never moved by itself"
    with Inventory(shop.path) as stock:
        assert "print-shop doesn't know what's on cc1" in stock.listing()
    assert "know what" in shop.desk(ran.job_id)[0].split("<h2>Jobs")[0], "the desk says it too"
    # The second ok: the past print's pause is ticked, and not used.
    following = shop.run("ok", "ok", "", held=reject)
    assert not following.result.printed and starts(shop) == 0
    assert "doesn't know what's on cc1" in shop.told[0]


def test_it_isnt_known_from_the_start_request_before_the_printer_is_told(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FramePrinter.start
    seen: list[str] = []

    def start(printer: FramePrinter, name: str) -> Any:
        seen.append(unknown_of(shop))
        return real(printer, name)

    monkeypatch.setattr(FramePrinter, "start", start)
    ran = shop.run(SWAP, "ok")
    assert seen == [ran.job_id]


@pytest.mark.parametrize("killed", [False, True])
def test_a_start_that_failed_or_was_never_confirmed_isnt_known(shop: Shop, killed: bool) -> None:
    sent_unconfirmed(shop, killed=killed)
    said = not_known(shop)
    assert unknown_of(shop) == "sent"
    assert "`stock.py load 3`" in said and "`stock.py load 1`" in said


def test_a_start_thats_refused_before_the_row_leaves_it_known(shop: Shop) -> None:
    with Inventory(shop.path) as stock:
        first = stock.material()
        other = first.of(stock.spool(BLACK))
    with Ledger(shop.path) as ledger, pytest.raises(LedgerError):
        ledger.record_start_requested(record("job-1").model_copy(update={
            "material": other,
            "spools": (SpoolUse(material=other, from_layer=1), SpoolUse(material=first)),
        }), loaded=other)
    assert not_known(shop) == ""


def test_a_start_seen_makes_it_not_known_again(shop: Shop) -> None:
    """Over can be truthful and then false: sent, they say it never began, then it begins."""
    row = sent_unconfirmed(shop, killed=False)
    shop.says(WHITE)
    assert not_known(shop) == ""

    ends(shop, row, "printing", layer=3)

    assert kept(shop, "sent").started_at is not None
    assert unknown_of(shop) == "sent" and shop.loaded() == WHITE


def test_a_start_confirmed_again_doesnt_undo_the_persons_word(shop: Shop) -> None:
    row = swap_started(shop)
    shop.says(BLACK)
    with Ledger(shop.path) as ledger:
        ledger.record_started(row.job_id)
    assert not_known(shop) == "", "only the first time it's seen to start"


def test_the_print_seen_on_the_printer_is_the_one_thats_not_known(shop: Shop) -> None:
    older = sent_unconfirmed(shop, killed=False)
    shop.says(WHITE)
    with Inventory(shop.path) as stock:
        first = stock.material()
        to = first.of(stock.spool(BLACK))
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record("newer").model_copy(update={
            "material": first,
            "spools": (SpoolUse(material=first, from_layer=1), SpoolUse(material=to)),
        }))
    ends(shop, older, "printing", layer=3)
    assert unknown_of(shop) == "sent"


SEEN = [
    ("completed", 100, None), ("completed", 100, ""), ("completed", 100, "another.gcode"),
    ("stopped", 5, None), ("stopped", 11, None), ("stopped", None, ""),
    ("idle", 0, ""), ("error", 30, None), ("printing", 12, None), ("paused", 11, ""),
]


@pytest.mark.parametrize("sent", ["started", "failed", "killed"])
@pytest.mark.parametrize(("seen", "then"), list(itertools.product(SEEN, SEEN[::4])))
def test_nothing_seen_ever_makes_it_known_or_moves_the_stock(
    shop: Shop, sent: str, seen: tuple[str, int | None, str | None],
    then: tuple[str, int | None, str | None],
) -> None:
    row = (swap_started(shop) if sent == "started"
           else sent_unconfirmed(shop, killed=sent == "killed"))
    for how, layer, named in (seen, then):
        ends(shop, row, how, layer=layer, named=named)
        assert shop.loaded() == WHITE
        assert unknown_of(shop) == row.job_id
    shop.load(BLACK)
    how, layer, named = then
    ends(shop, row, how, layer=layer, named=named)
    assert shop.loaded() == BLACK, "a load is never written over"
    assert unknown_of(shop) == row.job_id, "and alone it says nothing of the print being over"


def test_a_swap_thats_sent_during_the_upload_is_never_started_over(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FramePrinter.upload

    def upload(printer: FramePrinter, path: Path) -> str:
        stored = real(printer, path)
        swap_started(shop, "earlier")
        return stored

    monkeypatch.setattr(FramePrinter, "upload", upload)
    monkeypatch.setattr(daemon_module, "why_not_start", lambda state, ledger: "")
    ran = shop.run("ok")
    assert not ran.result.printed and starts(shop) == 0
    assert "uploaded, and not started" in (ran.result.error or "")


def test_the_row_itself_refuses_while_it_isnt_known(shop: Shop) -> None:
    swap_started(shop, "earlier")
    with Inventory(shop.path) as stock:
        loaded = stock.material()
    with Ledger(shop.path) as ledger, pytest.raises(LedgerError, match="not started"):
        ledger.record_start_requested(record("job-2"), loaded=loaded)


# 2. Only a person's word that says both clears it
# --------------------------------------------------------------------------


def asked(shop: Shop, *typed: str, asks: bool = True) -> tuple[list[str], list[str]]:
    """The question before any preparing: what happened in order, and what was said."""
    answers = iter(typed)
    did: list[str] = []
    said: list[str] = []

    def read(prompt: str) -> str:
        did.append(prompt)
        return next(answers)

    with Inventory(shop.path) as stock:
        after_print.ask_on_now(stock, "cc1", read=read, write=said.append,
                               forget=lambda: did.append("forget"), asks=asks)
    return did, said


@pytest.mark.parametrize(("typed", "on"), [("2", BLACK), ("#2", BLACK), ("y", WHITE), ("1", WHITE)])
def test_before_preparing_one_answer_says_both(shop: Shop, typed: str, on: int) -> None:
    swap_started(shop)
    did, said = asked(shop, typed)

    text = "\n".join(said)
    assert did == ["forget", "on now> "], "what was typed before it's shown is dropped"
    assert "cube.stl's print" in text and "over" in text, "the first half is in the question"
    assert "on the printer now" in text, "and the second"
    assert "spool #2, Elegoo PLA, black" in text and "spool #1, Elegoo PLA, white" in text
    assert shop.loaded() == on and not_known(shop) == ""


def test_the_likely_spools_are_listed_first_and_none_is_preselected(shop: Shop) -> None:
    sent_unconfirmed(shop, killed=False)  # white, then clear
    _, said = asked(shop, "")
    listed = [line.split()[0] for line in "\n".join(said).splitlines() if line.startswith("    ")]
    assert listed == ["3", "1", "2"]
    assert not_known(shop), "Enter takes none of them"


@pytest.mark.parametrize("typed", ["", "n", "later"])
def test_later_records_nothing_and_start_holds(shop: Shop, typed: str) -> None:
    swap_started(shop)
    did, _ = asked(shop, typed)
    assert did == ["forget", "on now> "]
    assert shop.loaded() == WHITE and not_known(shop)


@pytest.mark.parametrize("typed", ["yes please", "9", "²", "load 2"])
def test_what_cant_be_read_is_asked_again_with_typed_ahead_dropped(shop: Shop, typed: str) -> None:
    swap_started(shop)
    did, _ = asked(shop, typed, "2")
    assert did == ["forget", "on now> "] * 2
    assert shop.loaded() == BLACK and not_known(shop) == ""


def test_no_input_left_is_later(shop: Shop) -> None:
    swap_started(shop)

    def closed(prompt: str) -> str:
        raise EOFError

    with Inventory(shop.path) as stock:
        after_print.ask_on_now(stock, "cc1", read=closed, write=lambda said: None,
                               forget=lambda: None, asks=True)
    assert not_known(shop)


def test_with_no_terminal_it_only_says(shop: Shop) -> None:
    swap_started(shop)
    did, said = asked(shop, asks=False)
    assert did == [] and "doesn't know what's on cc1" in "\n".join(said)
    assert not_known(shop)


def test_nothing_is_asked_where_its_known(shop: Shop) -> None:
    assert asked(shop) == ([], [])


def test_an_answer_about_a_print_that_isnt_the_one_now_writes_nothing(shop: Shop) -> None:
    swap_started(shop, "older")
    with Inventory(shop.path) as stock:
        stock.load(WHITE, over="older")
    swap_started(shop, "newer")
    with Inventory(shop.path) as stock, pytest.raises(InventoryError, match="since"):
        stock.load(BLACK, over="older")
    assert shop.loaded() == WHITE and unknown_of(shop) == "newer"


def test_a_load_alone_never_clears_it(shop: Shop) -> None:
    swap_started(shop)
    shop.load(BLACK)
    assert shop.loaded() == BLACK and not_known(shop)


def print_job(
    shop: Shop, env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *answers: str,
    asks: bool = True, by_hand: bool = False,
) -> list[Any]:
    """`print_job.py` as far as its preparing: each prompt, and the spool it prepared with."""
    script = _load("print_job")
    work = tmp_path / "composed-jobs"
    work.mkdir(exist_ok=True)
    monkeypatch.setattr(script, "WORK_DIR", work)
    monkeypatch.setattr(script, "ledger_path", lambda at: shop.path)
    monkeypatch.setattr(script, "can_ask", lambda: asks)
    monkeypatch.setattr(script, "drop_typed", lambda: None)
    did: list[Any] = []
    typed = iter(answers)

    def read(prompt: str = "") -> str:
        did.append(prompt)
        return next(typed)

    def prepared(*args: Any, **kwargs: Any) -> Any:
        did.append(next(a for a in args if hasattr(a, "spool_id")).spool_id)
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", read)
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    if by_hand:
        file = SimpleNamespace(said=lambda: "a made-up file", sha256="ab", print_time_s=600,
                               filament_g=None, filament_cm3=1.0, layer_count=10)
        monkeypatch.setattr(script.sliced, "read", lambda *args: file)
        monkeypatch.setattr(script.sliced, "loaded_mismatch", prepared)
        sent = tmp_path / "by-hand.gcode"
        sent.write_text("; made up")
        argv = ["--sliced", str(sent)]
    else:
        monkeypatch.setattr(script, "profiles_for", prepared)
        argv = [str(shop.part)]
    with contextlib.suppress(KeyboardInterrupt):
        script.main([*argv, "--settings", str(env)])
    return did


@pytest.mark.parametrize("by_hand", [False, True])
def test_print_job_asks_at_from_the_inventory_before_any_preparing(
    shop: Shop, settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str], by_hand: bool,
) -> None:
    swap_started(shop)
    did = print_job(shop, settings, tmp_path, monkeypatch, "2", by_hand=by_hand)

    assert did == ["on now> ", BLACK], "asked first, then prepared on the answer"
    out = capsys.readouterr().out
    assert out.index("doesn't know what's on cc1") < out.index("From the inventory")
    assert not_known(shop) == ""


@pytest.mark.parametrize("by_hand", [False, True])
def test_print_job_with_no_terminal_asks_nothing_and_clears_nothing(
    shop: Shop, settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    by_hand: bool,
) -> None:
    swap_started(shop)
    assert print_job(shop, settings, tmp_path, monkeypatch, asks=False, by_hand=by_hand) == [WHITE]
    assert not_known(shop)


def test_print_job_asks_though_the_spool_the_stock_had_is_used_up(
    shop: Shop, settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    swap_started(shop)
    with Inventory(shop.path) as stock:
        stock.empty(WHITE)
    assert print_job(shop, settings, tmp_path, monkeypatch, "2") == ["on now> ", BLACK]


def stock_load(
    shop: Shop, env: Path, monkeypatch: pytest.MonkeyPatch, spool: int,
    *answers: str, asks: bool = True,
) -> tuple[int, list[str]]:
    script = _load("stock")
    monkeypatch.setattr(script, "ledger_path", lambda at: shop.path)
    monkeypatch.setattr(script, "can_ask", lambda: asks)
    did: list[str] = []
    monkeypatch.setattr(script, "drop_typed", lambda: did.append("forget"))
    typed = iter(answers)

    def read(prompt: str = "") -> str:
        did.append(prompt)
        return next(typed)

    monkeypatch.setattr("builtins.input", read)
    return script.main(["--settings", str(env), "load", str(spool)]), did


def test_stock_py_load_asks_whether_its_over_and_a_y_clears_it(
    shop: Shop, settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    swap_started(shop)
    assert stock_load(shop, settings, monkeypatch, BLACK, "y") == (0, ["forget", "over> "])
    out = capsys.readouterr().out
    assert "cube.stl's print" in out and "over" in out
    assert "spool #2, Elegoo PLA, black, on the printer now?" in out
    assert shop.loaded() == BLACK and not_known(shop) == ""


@pytest.mark.parametrize("typed", ["", "n", "yes", "2"])
def test_stock_py_load_without_the_y_records_and_doesnt_clear(
    shop: Shop, settings: Path, monkeypatch: pytest.MonkeyPatch, typed: str,  # noqa: F811
) -> None:
    swap_started(shop)
    assert stock_load(shop, settings, monkeypatch, BLACK, typed)[0] == 0
    assert shop.loaded() == BLACK and not_known(shop)


def test_stock_py_load_with_no_terminal_records_and_doesnt_clear(
    shop: Shop, settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    swap_started(shop)
    assert stock_load(shop, settings, monkeypatch, BLACK, asks=False) == (0, [])
    assert shop.loaded() == BLACK and not_known(shop)
    assert "still doesn't know what's on cc1" in capsys.readouterr().out


def test_stock_py_load_of_an_empty_spool_asks_nothing(
    shop: Shop, settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    swap_started(shop)
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    assert stock_load(shop, settings, monkeypatch, BLACK) == (2, [])


def test_stock_py_load_asks_nothing_where_its_known(
    shop: Shop, settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    assert stock_load(shop, settings, monkeypatch, BLACK) == (0, [])


def test_the_y_at_any_jobs_review_says_both(shop: Shop) -> None:
    ends(shop, swap_started(shop, "earlier"))
    answered_with(shop, "as recorded")  # out of the way: the desk shows the oldest first
    ran = shop.run("ok", "y")
    assert ran.prompts == ["> ", "loaded> "]
    question = next(line for line in ran.terminal.splitlines() if "on the printer now?" in line)
    assert "cube.stl's print" in question and "over" in question
    assert ran.result.printed, ran.result.error


def test_a_y_after_another_swap_was_sent_writes_nothing(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    swap_started(shop, "earlier")
    real = Inventory.load

    def load(stock: Inventory, *args: Any, **kwargs: Any) -> Any:
        monkeypatch.setattr(Inventory, "load", real)
        swap_started(shop, "another")
        return real(stock, *args, **kwargs)

    monkeypatch.setattr(Inventory, "load", load)
    ran = shop.run("ok", "y", press="reject")
    assert "Not recorded" in ran.terminal
    assert shop.loaded() == WHITE


def test_the_outcomes_select_says_both_and_preselects_nothing(shop: Shop) -> None:
    seen: dict[str, Any] = {}

    def look(bridge: MemoryBridge) -> None:
        seen.update(options=bridge.retained_json(outcome_spool_select(CFG)[0])["options"],
                    card=bridge.retained_json(outcome_topic(CFG)))

    ran = shop.run(SWAP, "ok", at_outcome=look)

    assert seen["options"] == [
        "as recorded", ON_BLACK, ON_WHITE, over_on("spool #3, Elegoo PETG, clear")]
    assert seen["card"]["spool"] == "as recorded"
    assert "ended with: not said" in seen["card"]["spools"]
    shown = " ".join(cards(ran).split())
    assert "over" in shown and "pick it below" in shown
    assert not_known(shop), "as recorded doesn't clear it"
    assert ended_with(kept(shop, ran.job_id)) is None


@pytest.mark.parametrize(("option", "on"), [(ON_BLACK, BLACK), (ON_WHITE, WHITE)])
def test_the_outcomes_pick_clears_it_and_is_what_it_ended_with(
    shop: Shop, option: str, on: int
) -> None:
    ran = shop.run(
        SWAP, "ok",
        at_outcome=lambda bridge: bridge.deliver(outcome_spool_topic(CFG), option))
    assert shop.loaded() == on and not_known(shop) == ""
    row = kept(shop, ran.job_id)
    assert ended_with(row) == on
    assert spools(row) == [(WHITE, 1), (BLACK, 11)], "the plan stays as it was"


def test_nothing_moves_until_record(shop: Shop) -> None:
    ends(shop, swap_started(shop))
    bridge = MemoryBridge()
    with Ledger(shop.path) as ledger:
        desk = OutcomeDesk(ledger=ledger, cfg=CFG,
                           publish=lambda topic, payload: bridge.publish(topic, payload, True))
        for topic, handler in desk.topics.items():
            bridge.subscribe(topic, handler)
        desk.refresh()
        bridge.deliver(outcome_spool_topic(CFG), ON_BLACK)
        bridge.deliver(outcome_result_topic(CFG), "failed")
        bridge.deliver(outcome_symptom_topic(CFG), "none")
        bridge.deliver(outcome_submit_topic(CFG), "record")  # refused: a failure names one
    assert shop.loaded() == WHITE and not_known(shop)


def test_only_that_prints_own_card_offers_it(shop: Shop) -> None:
    ends(shop, swap_started(shop, "older"))
    shop.says(WHITE)
    ends(shop, swap_started(shop, "newer"))

    shown = answered_with(shop, ON_BLACK)  # the desk shows the older first

    assert shown["job_id"] == "newer", "the older's answered, and the newer's is shown"
    assert ended_with(kept(shop, "older")) is None
    assert shop.loaded() == WHITE and unknown_of(shop) == "newer"


def test_a_pick_thats_been_overtaken_by_the_terminals_word_writes_nothing(shop: Shop) -> None:
    ends(shop, swap_started(shop))
    bridge = MemoryBridge()
    with Ledger(shop.path) as ledger:
        desk = OutcomeDesk(ledger=ledger, cfg=CFG,
                           publish=lambda topic, payload: bridge.publish(topic, payload, True))
        for topic, handler in desk.topics.items():
            bridge.subscribe(topic, handler)
        desk.refresh()
        bridge.deliver(outcome_spool_topic(CFG), ON_BLACK)
        shop.says(WHITE)
        bridge.deliver(outcome_result_topic(CFG), "success")
        bridge.deliver(outcome_symptom_topic(CFG), "none")
        bridge.deliver(outcome_submit_topic(CFG), "record")
        assert "said since" in bridge.retained_json(outcome_topic(CFG))["error"]
        assert kept(shop).result is None and shop.loaded() == WHITE
        bridge.deliver(outcome_submit_topic(CFG), "record")
    assert kept(shop).result == "success" and ended_with(kept(shop)) is None


def test_a_spool_used_up_cant_be_picked_as_on_now(shop: Shop) -> None:
    ends(shop, swap_started(shop))
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    answered_with(shop, ON_BLACK)
    assert shop.loaded() == WHITE and not_known(shop)


def test_the_hold_names_the_card_only_while_it_can_be_picked_there(shop: Shop) -> None:
    row = swap_started(shop)
    assert "outcome card" not in not_known(shop), "there's no card before it's seen to end"
    ends(shop, row)
    assert "outcome card" in not_known(shop)
    answered_with(shop, "as recorded")
    assert not_known(shop) and "outcome card" not in not_known(shop)


def test_a_hold_never_names_a_spool_thats_used_up(shop: Shop) -> None:
    swap_started(shop)
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    assert "`stock.py load 2`" not in not_known(shop)
    assert "`stock.py load 1`" in not_known(shop)


# 3. The spool it ended with is the person's pick, or "not said"
# --------------------------------------------------------------------------


def test_not_said_is_what_the_ledger_and_the_desk_say(shop: Shop) -> None:
    ran = shop.run(SWAP, "ok", ends="stopped")
    assert ran.row is not None
    front, page = shop.desk(ran.job_id)
    assert "ended with: not said" in describe(ran.row)
    assert "ended with: not said" in page and "not said" in front


def test_a_reprint_is_as_planned_whatever_it_ended_with(shop: Shop) -> None:
    ran = shop.run(SWAP, "ok", at_outcome=lambda bridge: bridge.deliver(
        outcome_spool_topic(CFG), over_on("spool #3, Elegoo PETG, clear")))
    row = kept(shop, ran.job_id)
    assert ended_with(row) == CLEAR
    assert "ended with: spool #3, Elegoo PETG, clear" in describe(row)
    assert "to #2" in shop.correction_line(ran.job_id)


# 4. It hangs on the pause being a swap
# --------------------------------------------------------------------------


@pytest.mark.parametrize("recorded", [0, 1])
def test_a_swap_with_one_spool_recorded_or_none_isnt_known_too(shop: Shop, recorded: int) -> None:
    change = Change(key="pause_at", preset="", value="2", reason="a swap", source=Source.SPONSOR,
                    pause=pauses.Pause(kind=pauses.PauseFor.SWAP))
    with Inventory(shop.path) as stock:
        first = stock.material()
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record().model_copy(update={
            "material": first, "proposal": Proposal(changes=(change,)),
            "spools": (SpoolUse(material=first, from_layer=1),)[:recorded],
        }))
    assert unknown_of(shop) == "job-1"


def test_an_unmatched_pause_still_records_the_spool_swapped_to(shop: Shop) -> None:
    with Inventory(shop.path) as stock:
        first = stock.material()
        to = first.of(stock.spool(BLACK))
    found = (PauseReading(before_layer=5, z=1.0, after_s=60, after_z=0.8),
             PauseReading(before_layer=9, z=1.8, after_s=90, after_z=1.6))
    used = pauses.uses(pauses.Pause(kind=pauses.PauseFor.SWAP, start=first, to=to),
                       SimpleNamespace(pauses=found))  # type: ignore[arg-type]
    assert [(u.material.spool_id, u.from_layer) for u in used] == [(WHITE, 1), (BLACK, None)]


def test_an_insert_never_makes_it_not_known(shop: Shop) -> None:
    assert shop.run("pause_at=2 for insert", "ok").result.printed
    assert not_known(shop) == ""


def test_an_insert_after_a_swap_print_never_says_swap_or_filament(shop: Shop) -> None:
    shop.run(SWAP, "ok")
    held: dict[str, Any] = {}

    def look(bridge: MemoryBridge) -> None:
        held.update(bridge.retained_json(gate_topic(CFG)))
        reject(bridge)

    ran = shop.run("pause_at=2 for insert", "ok", "", held=look)

    about = [line for line in ran.terminal.splitlines()
             if "know what" in line or "on the printer now?" in line]
    assert len(about) > 1
    for text in [*about, ran.gate["spools"], held["held"], shop.told[0]]:
        assert text and not any(word in text.lower() for word in ("swap", "filament")), text


# What the Sponsor was told it costs
# --------------------------------------------------------------------------


def test_the_same_plate_again_costs_one_y_at_the_review(shop: Shop) -> None:
    shop.run(SWAP, "ok")
    again_ = shop.run("use", "ok", "y")
    assert again_.prompts.count("loaded> ") == 1 and shop.told == []
    assert again_.result.printed and spools(again_.row) == [(WHITE, 1), (BLACK, 11)]


def test_the_same_plate_again_costs_one_y_before_preparing(shop: Shop) -> None:
    shop.run(SWAP, "ok")
    asked(shop, "y")
    again_ = shop.run("use", "ok")
    assert "loaded> " not in again_.prompts and shop.told == []
    assert again_.result.printed


def test_a_next_job_on_the_second_colour_costs_one_answer_before_preparing(shop: Shop) -> None:
    shop.run(SWAP, "ok")
    did, _ = asked(shop, "2")
    following = shop.run("ok", "ok")
    assert did.count("on now> ") == 1
    assert "loaded> " not in following.prompts and shop.told == []
    assert following.result.printed and following.row is not None
    assert following.row.material is not None and following.row.material.spool_id == BLACK


def test_a_failed_start_costs_one_answer(shop: Shop) -> None:
    sent_unconfirmed(shop, killed=False)
    did, _ = asked(shop, "y")
    assert did.count("on now> ") == 1
    assert shop.run("ok").result.printed


def test_the_phone_settles_a_held_job(shop: Shop) -> None:
    ends(shop, swap_started(shop, "earlier"))

    def phone(bridge: MemoryBridge) -> None:
        answered_with(shop, ON_WHITE)
        bridge.press(CFG, "approve")

    ran = shop.run("ok", "", held=phone)
    assert ran.result.printed, ran.result.error


# 6. Nothing of the inference is left
# --------------------------------------------------------------------------


def test_the_inferences_tables_and_columns_are_gone(tmp_path: Path) -> None:
    at = tmp_path / "new.sqlite"
    Ledger(at).close()
    db = sqlite3.connect(at)
    tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns = {row[1] for row in db.execute("PRAGMA table_info(print_spools)")}
    db.close()
    assert "printer_loads" not in tables
    assert columns == {"job_id", "position", "spool_id", "from_layer", "from_z", "spool"}
