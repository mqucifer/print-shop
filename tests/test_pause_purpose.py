"""A pause says swap or insert, and a swap records the spool swapped to (mqucifer/print-shop#218).

Each print here runs through the real review at the terminal, gate, ledger, watcher and outcome
desk, on fakes: a recorded slice with the pause put in where Orca puts it, a made-up stock, a
broker in a list and a printer that answers from frames. Nothing is the Sponsor's.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jinja2
import pytest
import yaml

from print_shop.core.mqtt import (
    gate_topic,
    outcome_result_topic,
    outcome_spool_select,
    outcome_spool_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
    state_topic,
)
from print_shop.desk.again import again
from print_shop.desk.page import index_page, job_page
from print_shop.desk.read import Desk
from print_shop.ingest.analyzer import analyze
from print_shop.inventory import Inventory, SpoolUse
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import spools as after_print
from print_shop.orchestrator.daemon import Daemon, JobResult
from print_shop.orchestrator.outcome import Watcher
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.offer import Offering, ThisPart, offering
from print_shop.setup.proposal import Proposal
from print_shop.setup.review import Review, cli_reviewer, parse_answer, render
from print_shop.slicer.store import Store, choose
from tests.fakes import CI_PROFILES, STORE, FramePrinter, MemoryBridge, frame
from tests.fixtures import write_stl
from tests.test_colour_change import LAYERS, PausingSlicer, _request
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_outcome import wait_until
from tests.test_stock import _load

WHITE, BLACK, CLEAR = 1, 2, 3


@dataclass
class Ran:
    """One job, and everything that was said of it on the way."""

    job_id: str
    result: JobResult
    row: Row | None
    terminal: str
    prompts: list[str]
    gate: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    outcome: dict[str, Any] = field(default_factory=dict)


class Shop:
    """A made-up shop: white and black PLA, a clear PETG, and white loaded."""

    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "ledger.sqlite"
        self.jobs = tmp_path / "work"
        self.jobs.mkdir()
        self.parts = tmp_path / "parts"
        self.parts.mkdir()
        self.part = write_stl("cube", self.parts)
        process = json.loads(CI_PROFILES.process.read_text())
        self.process = tmp_path / "process.json"
        self.process.write_text(json.dumps({**process, "wall_loops": "2", **LAYERS}))
        with Inventory(self.path) as stock:
            stock.add_printer("cc1", "Made-up printer")
            stock.fit(0.4)
            for kind, variety, colour in (("PLA", "Elegoo PLA", "white"),
                                          ("PLA", "Elegoo PLA", "black"),
                                          ("PETG", "Elegoo PETG", "clear")):
                stock.add_spool(manufacturer="Elegoo", type=kind, variety=variety, colour=colour)
            stock.load(WHITE)

    def load(self, spool: int) -> None:
        with Inventory(self.path) as stock:
            stock.load(spool)

    def loaded(self) -> int | None:
        with Inventory(self.path) as stock:
            spool = stock.loaded("cc1")
        return spool.id if spool is not None else None

    def run(
        self, *typed: str, press: str = "approve", ends: str = "completed",
        at_gate: Callable[[], None] | None = None,
        at_outcome: Callable[[MemoryBridge], None] | None = None,
    ) -> Ran:
        """A job, typed at as at the terminal, pressed at the gate and answered at the end."""
        with Inventory(self.path) as stock:
            material = stock.material()
        choice = choose(Store(STORE), nozzle_mm=0.4, filament=material.variety)
        profiles = CI_PROFILES.model_copy(update={
            "process": self.process,
            "used": choice.used(STORE).model_copy(update={"material": material}),
        })
        answers = iter(typed)
        prompts: list[str] = []
        said: list[str] = []

        def read(prompt: str) -> str:
            prompts.append(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise AssertionError("asked more than was typed:\n" + "\n".join(said)) from None

        bridge = MemoryBridge()
        printer = FramePrinter(after_start=["printing", ends])
        with Ledger(self.path) as ledger:
            daemon = Daemon(
                cfg=CFG, profiles=profiles, workdir=self.jobs, printer=printer,
                slicer=PausingSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
                proposer=Proposer(), asker=lambda questions: (),
                reviewer=cli_reviewer(read=read, write=said.append),
                watch_printer=printer, watch_poll_s=0.01,
            )
            with daemon:
                job_id = daemon.submit(self.part, as_laid=True)
                opened = daemon.wait_for_gate(job_id, timeout_s=30)
                gate: dict[str, Any] = {}
                state: dict[str, Any] = {}
                outcome: dict[str, Any] = {}
                if opened is not None:
                    gate = dict(bridge.retained_json(gate_topic(CFG)))
                    state = dict(bridge.retained_json(state_topic(CFG)))
                    if at_gate is not None:
                        at_gate()
                    bridge.press(CFG, press)
                result = daemon.wait_for_finish(job_id, timeout_s=30)
                if result.printed:
                    wait_until(lambda: bridge.retained_json(
                        outcome_topic(CFG)).get("job_id") == job_id)
                    outcome = dict(bridge.retained_json(outcome_topic(CFG)))
                    if at_outcome is not None:
                        at_outcome(bridge)
                    bridge.deliver(outcome_result_topic(CFG), "success")
                    bridge.deliver(outcome_symptom_topic(CFG), "none")
                    bridge.deliver(outcome_submit_topic(CFG), "record")
            row = ledger.get(job_id)
        return Ran(job_id=job_id, result=result, row=row, terminal="\n".join(said),
                   prompts=prompts, gate=gate, state=state, outcome=outcome)

    def desk(self, job_id: str) -> tuple[str, str]:
        """The desk's front page and the job's page, as text."""
        desk = Desk(self.path, self.jobs, self.parts)
        record = desk.job(job_id)
        assert record is not None
        pages = index_page(desk.on_hand(), desk.jobs(), self.path), job_page(record)
        front, page = (re.sub(r"<(style|script)>.*?</\1>", "", p, flags=re.S) for p in pages)
        return front, page

    def correction_line(self, job_id: str) -> str:
        record = Desk(self.path, self.jobs, self.parts).job(job_id)
        assert record is not None and record.row is not None
        return again(record.row, record.part_path, record.printers, record.part_found).corrections


@pytest.fixture
def shop(tmp_path: Path) -> Shop:
    return Shop(tmp_path)


def spools(row: Row | None) -> list[tuple[int, int | None]]:
    assert row is not None
    return [(s.material.spool_id, s.from_layer) for s in row.spools]


# Done when
# --------------------------------------------------------------------------


def test_white_then_black_is_both_spools_and_the_layer_in_the_ledger_and_on_the_desk(
    shop: Shop,
) -> None:
    ran = shop.run("colour_change=2 from #1 to #2 because a white floor", "ok")

    assert ran.result.printed, ran.result.error
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)]
    assert ran.row is not None and ran.row.material is not None
    assert ran.row.material.spool_id == WHITE, "the print's material is the spool it started on"
    front, page = shop.desk(ran.job_id)
    assert "white, then black" in front
    assert "spool #1, Elegoo PLA, white, from layer 1" in page
    assert "spool #2, Elegoo PLA, black, from layer 11" in page


@pytest.mark.parametrize("by", ["typed", "use"])
def test_a_reprint_of_white_then_black_is_white_then_black(shop: Shop, by: str) -> None:
    first = shop.run("colour_change=2 from #1 to #2 because a white floor", "ok")
    assert shop.loaded() == BLACK, "what the first left loaded"
    line = shop.correction_line(first.job_id) if by == "typed" else "use"

    second = shop.run(line, "ok", at_gate=lambda: shop.load(WHITE))

    assert second.result.printed, second.result.error
    assert spools(second.row) == [(WHITE, 1), (BLACK, 11)]
    assert shop.loaded() == BLACK


def test_an_insert_never_says_swap_or_filament_at_any_step(shop: Shop) -> None:
    ran = shop.run("pause_at=2 for insert because the nuts go in", "ok")

    assert ran.result.printed, ran.result.error
    assert ran.row is not None
    reviewed = ran.terminal.split("Settings for this print")[-1]
    front, page = shop.desk(ran.job_id)
    pausing = {
        "the review": re.sub(r"(?m)^  filament .*$", "", reviewed),
        "the gate": json.dumps([ran.gate.get("pause"), ran.gate.get("pause_for")]),
        "the outcome": json.dumps({k: v for k, v in ran.outcome.items() if k != "gcode_name"}),
        "the ledger's read-back": "\n".join(
            line for line in describe(ran.row).splitlines() if "paus" in line or "insert" in line),
        "the desk's front page": front.split("<h2>Jobs")[-1],
        # Less the command's option and the part's path, which holds this test's own name.
        "the desk's job": re.sub(r"--filament\b|/\S*cube\.stl", "", page),
        "the reprint's line": shop.correction_line(ran.job_id),
        "the G-code's message": json.dumps(
            [c.model_dump(mode="json") for c in ran.row.corrections or ()]),
    }
    for step, text in pausing.items():
        assert not re.search(r"swap|filament|colour change", text, re.I), f"{step}: {text}"
    assert "insert" in pausing["the review"] and "insert" in pausing["the gate"]
    assert "insert" in pausing["the outcome"] and "insert" in pausing["the desk's job"]


def test_afterwards_the_stock_shows_the_last_spool_loaded(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "ok")
    assert ran.result.printed, ran.result.error
    assert shop.loaded() == BLACK


def test_a_row_from_before_reads_back_as_it_was(shop: Shop, tmp_path: Path) -> None:
    ran = shop.run("pause_at=2 for insert because it was typed then", "ok")
    db = sqlite3.connect(shop.path)
    # The file as it was before: no table of spools, and nothing said of what a pause was for.
    db.execute("DROP TABLE print_spools")
    for table, column in (("prints", "proposal"), ("prints", "corrections"),
                          ("slice_readings", "reading")):
        [(kept,)] = db.execute(f"SELECT {column} FROM {table}").fetchall()
        db.execute(f"UPDATE {table} SET {column}=?", (json.dumps(_as_before(json.loads(kept))),))
    db.commit()
    before = db.execute("SELECT proposal, corrections FROM prints").fetchall()
    db.close()

    with Ledger.read_only(shop.path) as ledger:
        looked = ledger.get(ran.job_id)
    with Ledger(shop.path) as ledger:
        row = ledger.get(ran.job_id)

    assert looked == row and row is not None and row.spools == ()
    assert row.material is not None and row.material.spool_id == WHITE
    [correction] = row.corrections or ()
    assert (correction.value, correction.reason) == ("2", "it was typed then")
    assert row.reading is not None
    assert row.reading.pauses[0].line().startswith("pauses after layer 10, at z=2, ")
    assert sqlite3.connect(shop.path).execute(
        "SELECT proposal, corrections FROM prints").fetchall() == before, "nothing was rewritten"


def _as_before(kept: Any) -> Any:
    """A row's JSON without the keys this change adds."""
    if isinstance(kept, list):
        return [_as_before(v) for v in kept]
    if isinstance(kept, dict):
        return {k: _as_before(v) for k, v in kept.items() if k not in ("pause", "kind", "to")
                or (k == "kind" and "time_s" in kept)}
    return kept


# Asked, where it isn't typed
# --------------------------------------------------------------------------


def kind_of(row: Row | None) -> str | None:
    assert row is not None and row.proposal is not None
    change = row.proposal.get("pause_at")
    return change.pause.kind.value if change is not None and change.pause is not None else None


@pytest.mark.parametrize(("answer", "kind", "reason"), [
    ("1", "insert", "parts dropped in"),
    ("insert", "insert", "parts dropped in"),
])
def test_what_a_pause_at_is_for_is_asked(
    shop: Shop, answer: str, kind: str, reason: str
) -> None:
    ran = shop.run("pause_at=2", answer, "ok")
    assert ran.prompts == ["> ", "pause> ", "> "]
    assert kind_of(ran.row) == kind
    assert ran.row is not None and [c.reason for c in ran.row.corrections or ()] == [reason]


def test_a_swap_asked_takes_both_spools_from_the_stock(shop: Shop) -> None:
    ran = shop.run("pause_at=2 because a white floor", "2", "", "9", "1", "#2", "ok")
    assert ran.prompts == ["> ", "pause> ", "spool> ", "spool> ", "spool> ", "spool> ", "> "]
    assert "Enter for spool #1, Elegoo PLA, white" in ran.terminal
    assert "spool #9 isn't on hand" in ran.terminal
    assert "it starts on spool #1, Elegoo PLA, white: a swap is to another" in ran.terminal
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)]


def test_colour_change_is_a_swap_and_only_its_spools_are_asked(shop: Shop) -> None:
    ran = shop.run("colour_change=2", "1", "2", "ok")
    assert "pause> " not in ran.prompts
    assert kind_of(ran.row) == "swap" and spools(ran.row) == [(WHITE, 1), (BLACK, 11)]


def test_the_only_spool_it_can_start_on_is_still_the_persons_to_confirm(shop: Shop) -> None:
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    ran = shop.run("colour_change=2", "", "3", "ok")
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "], "never chosen for them"
    assert spools(ran.row) == [(WHITE, 1), (CLEAR, 11)]


@pytest.mark.parametrize("answers", [("back",), ("2", "back"), ("2", "1", "back")])
def test_back_from_what_its_for_sets_no_pause(shop: Shop, answers: tuple[str, ...]) -> None:
    ran = shop.run("pause_at=2", *answers, "ok")
    assert "Back to the review" in ran.terminal
    assert ran.row is not None and ran.row.corrections == () and ran.row.spools == ()


def test_a_height_typed_again_has_this_reviews_purpose_preselected(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "pause_at=3", "", "", "", "ok")
    assert "Enter for a swap, as it is now" in ran.terminal
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 16)]


def test_a_typed_pause_has_the_past_prints_purpose_preselected(shop: Shop) -> None:
    shop.run("pause_at=2 for insert because the nuts go in", "ok")
    ran = shop.run("pause_at=2", "", "ok")
    assert "Enter for an insert, as on cube.stl" in ran.terminal
    assert kind_of(ran.row) == "insert"


def test_a_pause_at_another_height_doesnt_come_from_the_past_print(shop: Shop) -> None:
    """Enter would have made an insert the swap of the print before it."""
    shop.run("colour_change=2 to #2", "ok")
    ran = shop.run("pause_at=3", "", "1", "ok", "ok")
    assert ran.prompts.count("pause> ") == 2, "Enter alone chooses nothing"
    assert "Enter for a swap" not in ran.terminal
    assert kind_of(ran.row) == "insert"


def test_a_pause_from_before_is_asked_when_its_used(shop: Shop) -> None:
    first = shop.run("pause_at=2 for insert", "ok")
    db = sqlite3.connect(shop.path)
    for column in ("proposal", "corrections"):
        [(kept,)] = db.execute(f"SELECT {column} FROM prints").fetchall()
        before = json.dumps(_as_before(json.loads(kept))).replace(
            "parts dropped in", "a colour change")  # every pause's default then, inserts too
        db.execute(f"UPDATE prints SET {column}=?", (before,))
    db.commit()
    db.close()
    _, page = shop.desk(first.job_id)
    assert "colour change" not in page and "At 2 mm: a pause" in page
    assert shop.correction_line(first.job_id) == "pause_at=2"

    second = shop.run("use", "1", "ok")

    assert second.prompts == ["> ", "pause> ", "> "]
    assert kind_of(second.row) == "insert"
    assert second.row is not None
    assert [c.reason for c in second.row.corrections or ()] == ["parts dropped in"]


@pytest.mark.parametrize(("typed", "why"), [
    ("colour_change=2 from #3 to #1", "the job is sliced for Elegoo PLA, and spool #3 is "
     "Elegoo PETG: to start on it, load it"),
    ("colour_change=2 to #1", "it starts on spool #1, Elegoo PLA, white, so it swaps to another"),
    ("colour_change=2 to #9", "spool #9 isn't on hand"),
    ("colour_change=2 for insert", "colour_change is a swap: for parts dropped in"),
    ("pause_at=2 for insert to #2", "a pause for an insert names no spool"),
    ("pause_at=2 for lunch", "isn't a height"),
])
def test_what_a_pause_cant_be_is_refused_saying_why(shop: Shop, typed: str, why: str) -> None:
    ran = shop.run(typed, "stop")
    assert not ran.result.printed and ran.row is None
    assert why in ran.terminal


@pytest.mark.parametrize("value", ["2", "2mm", "2 mm"])
def test_a_height_is_read_with_or_without_mm(value: str) -> None:
    found = parse_answer(f"pause_at={value} for insert", _request())
    assert isinstance(found, Review)
    [change] = found.corrections
    assert change.value == "2" and change.pause is not None and change.pause.kind == "insert"


def test_with_no_stock_a_swap_is_still_a_swap_and_names_no_spool() -> None:
    request = _request()
    found = parse_answer("colour_change=2", request)
    assert isinstance(found, Review)
    [change] = found.corrections
    assert change.pause is not None and change.pause.complete(None)
    shown = render(type(request)(**{**request.__dict__, "proposal": Proposal(changes=(change,))}))
    assert "the pause is a swap: there's no stock, so its spools aren't recorded" in shown
    refused = parse_answer("colour_change=2 to #2", request)
    assert isinstance(refused, str) and "there's no stock to name a spool from" in refused


# A carried swap never comes back reversed
# --------------------------------------------------------------------------


def test_a_swap_between_varieties_isnt_offered_reversed(shop: Shop) -> None:
    """print-shop-ux, on 4f33d68: white then clear, used again with clear loaded, came back
    clear then white."""
    shop.run("colour_change=2 to #3", "ok")
    assert shop.loaded() == CLEAR

    again_ = shop.run("use 1", "stop")

    why = ("that print started on spool #1, Elegoo PLA, white, and this job is sliced for "
           "Elegoo PETG")
    said = " ".join(again_.terminal.split())
    assert "CAN'T USE 1: " + why in said
    assert "pause_at can't be used: " + why in said
    assert "[x] 1" not in again_.terminal and again_.prompts == ["> ", "> "]


def test_it_prints_again_under_use_the_right_way_round_after_the_second_was_left_loaded(
    shop: Shop,
) -> None:
    """The operator's case: with the second loaded it's refused, and with the first loaded as
    that says, `use` prints white then clear, never clear then white."""
    shop.run("colour_change=2 to #3", "ok")
    assert shop.loaded() == CLEAR
    refused = shop.run("use 1", "stop")
    assert not refused.result.printed
    shop.load(WHITE)

    again_ = shop.run("use", "ok")

    assert again_.result.printed, again_.result.error
    assert spools(again_.row) == [(WHITE, 1), (CLEAR, 11)]


def test_the_desks_line_for_it_is_refused_too(shop: Shop) -> None:
    first = shop.run("colour_change=2 to #3", "ok")
    again_ = shop.run(shop.correction_line(first.job_id), "stop")
    assert "the job is sliced for Elegoo PETG, and spool #1 is Elegoo PLA" in again_.terminal


def test_typed_at_that_prints_height_its_start_is_never_defaulted(shop: Shop) -> None:
    shop.run("colour_change=2 to #3", "ok")
    again_ = shop.run("colour_change=2", "", "back", "stop")
    assert "that print started on spool #1, Elegoo PLA, white" in " ".join(again_.terminal.split())
    assert again_.prompts == ["> ", "spool> ", "spool> ", "> "], "Enter alone chooses nothing"
    assert "swap to?" not in again_.terminal


def test_a_first_spool_used_up_isnt_swapped_for_the_second(shop: Shop) -> None:
    shop.run("colour_change=2 to #2", "ok")
    with Inventory(shop.path) as stock:
        stock.empty(WHITE)
    again_ = shop.run("use 1", "stop")
    assert "that print started on spool #1, Elegoo PLA, white, which isn't on hand now" in (
        " ".join(again_.terminal.split()))
    assert "[x] 1" not in again_.terminal and "spool> " not in again_.prompts


def test_a_first_spool_used_up_during_the_review_is_refused_at_use(shop: Shop) -> None:
    shop.run("colour_change=2 to #2", "ok")
    with Ledger(shop.path) as ledger:
        offered = offer_for(shop, ledger)
    with Inventory(shop.path) as stock:
        material = stock.material()
        stock.empty(WHITE)
        on_hand = tuple(stock.spools())
    request = dataclasses.replace(
        _request(), offering=offered, material=material, stock=lambda: on_hand)
    said = parse_answer("use 1", request)
    assert isinstance(said, str) and "which isn't on hand now" in said


def offer_for(shop: Shop, ledger: Ledger) -> Offering:
    with Inventory(shop.path) as stock:
        material, on_hand = stock.material(), tuple(stock.spools())
    report = analyze(str(shop.part))
    found = offering(ledger, ThisPart(
        stl=shop.part, stl_name=shop.part.name, sha256=report.stl_sha256,
        features=measure(shop.part, report.bed_contact_area_mm2), rotation_deg=(0.0, 0.0, 0.0),
        height_mm=report.bbox_mm[2], machine={}, process=dict(LAYERS), filament={},
        material=material, on_hand=on_hand,
    ))
    assert found.past is not None
    return found


# The review and Start
# --------------------------------------------------------------------------


def test_another_variety_at_a_swap_is_said_at_the_review(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #3", "ok")
    assert ("note: spool #3 is Elegoo PETG, and the slice has one filament's settings, those of "
            "Elegoo PLA") in ran.terminal
    assert shop.loaded() == CLEAR


def test_the_same_variety_at_a_swap_isnt_noted(shop: Shop) -> None:
    assert "one filament's settings" not in shop.run("colour_change=2 to #2", "ok").terminal


def test_start_refuses_a_swap_whose_first_spool_isnt_loaded(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #2 to #1", "ok")

    assert "LOAD FIRST: it starts on spool #2, Elegoo PLA, black" in ran.terminal
    assert ran.gate["spools"].startswith("LOAD FIRST")
    assert not ran.result.printed and ran.row is None
    assert "this print starts on spool #2, Elegoo PLA, black" in (ran.result.error or "")
    assert "nothing was uploaded or printed" in (ran.result.error or "")
    assert shop.loaded() == WHITE


def test_start_refuses_when_the_first_was_unloaded_while_the_job_waited(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "ok", at_gate=lambda: shop.load(CLEAR))
    assert not ran.result.printed and ran.row is None
    assert "has spool #3, Elegoo PETG, clear loaded" in (ran.result.error or "")


def test_a_colour_corrected_while_waiting_is_the_one_kept(shop: Shop) -> None:
    def edit() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, colour="cream")
            stock.edit(BLACK, colour="charcoal")

    ran = shop.run("colour_change=2 to #2", "ok", at_gate=edit)
    assert ran.row is not None and ran.row.material is not None and ran.row.profiles is not None
    assert [s.material.colour for s in ran.row.spools] == ["cream", "charcoal"]
    assert ran.row.profiles.material == ran.row.material == ran.row.spools[0].material


def test_a_pause_taken_away_swaps_nothing(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "pause_at=none", "ok")
    assert ran.row is not None and ran.row.spools == ()
    assert ran.gate["pause"] == ran.gate["pause_for"] == ran.gate["spools"] == ""
    assert shop.loaded() == WHITE


def test_a_rejected_job_moves_no_stock(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "ok", press="reject")
    assert not ran.result.printed and ran.row is None and shop.loaded() == WHITE


# When it ends, and at the outcome
# --------------------------------------------------------------------------


def test_stopped_under_the_swap_it_ended_with_the_first(shop: Shop) -> None:
    # The fake printer stops at layer 57, and the swap is before 71.
    ran = shop.run("colour_change=14 to #2", "ok", ends="stopped")

    assert shop.loaded() == WHITE
    assert spools(ran.row) == [(WHITE, 1), (WHITE, 71)]
    assert ran.row is not None and ran.row.spools[-1].planned is not None
    assert ran.row.spools[-1].planned.spool_id == BLACK
    assert "swap, not made: white throughout" in shop.desk(ran.job_id)[0]
    assert "to #2" in shop.correction_line(ran.job_id), "printed again, it's as it was meant"
    never = "all the way: the swap to spool #2, Elegoo PLA, black at layer 71 wasn't made"
    assert never in ran.outcome["spools"]
    assert never in describe(ran.row) and never in re.sub("&#x27;", "'", shop.desk(ran.job_id)[1])
    assert "white, from layer 71" not in describe(ran.row)


def test_stopped_over_the_swap_it_ended_with_the_last(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "ok", ends="stopped")
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)] and shop.loaded() == BLACK


def test_the_person_corrects_the_last_spool_at_the_outcome(shop: Shop) -> None:
    def correct(bridge: MemoryBridge) -> None:
        offered = bridge.retained_json(outcome_spool_select(CFG)[0])["options"]
        assert "spool #3, Elegoo PETG, clear" in offered and offered[0] == "as recorded"
        assert bridge.retained_json(outcome_topic(CFG))["spool"] == "as recorded"
        bridge.deliver(outcome_spool_topic(CFG), "spool #3, Elegoo PETG, clear")
        assert bridge.retained_json(outcome_topic(CFG))["spool"] in offered
        assert shop.loaded() == BLACK, "nothing until Record"
        bridge.deliver(outcome_result_topic(CFG), "failed")
        bridge.deliver(outcome_symptom_topic(CFG), "none")
        bridge.deliver(outcome_submit_topic(CFG), "record")
        assert shop.loaded() == BLACK, "a refused answer corrects nothing"

    ran = shop.run("colour_change=2 to #2", "ok", at_outcome=correct)

    assert ran.outcome["spools"].endswith("The stock has spool #2, Elegoo PLA, black loaded")
    assert spools(ran.row) == [(WHITE, 1), (CLEAR, 11)] and shop.loaded() == CLEAR
    assert ran.row is not None and ran.row.spools[-1].planned is not None
    assert ran.row.spools[-1].planned.spool_id == BLACK
    assert "to #3" in shop.correction_line(ran.job_id), "printed again, with what it used"
    assert "spool #2, Elegoo PLA, black was planned" in describe(ran.row)


def test_stopped_and_corrected_to_the_one_planned(shop: Shop) -> None:
    ran = shop.run(
        "colour_change=14 to #2", "ok", ends="stopped",
        at_outcome=lambda bridge: bridge.deliver(
            outcome_spool_topic(CFG), "spool #2, Elegoo PLA, black"),
    )
    assert ran.row is not None and ran.row.spools[-1].planned is None
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 71)] and shop.loaded() == BLACK


def test_a_spool_that_isnt_offered_or_is_replayed_corrects_nothing(shop: Shop) -> None:
    def wrong(bridge: MemoryBridge) -> None:
        bridge.deliver(outcome_spool_topic(CFG), "spool #9, made up")
        bridge.deliver(outcome_spool_topic(CFG), "spool #3, Elegoo PETG, clear", retain=True)
        assert bridge.retained_json(outcome_topic(CFG))["spool"] == "as recorded"

    ran = shop.run("colour_change=2 to #2", "ok", at_outcome=wrong)
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)] and shop.loaded() == BLACK


def test_a_print_with_no_swap_offers_no_spool(shop: Shop) -> None:
    ran = shop.run("pause_at=2 for insert", "ok")
    assert ran.outcome["spool_choices"] == 0 and ran.outcome["spools"] == ""


def swap_started(shop: Shop, job_id: str = "job-1") -> Row:
    """A made-up print with a swap at layer 11, started and still printing."""
    with Inventory(shop.path) as stock:
        first = stock.material()
        to = first.of(stock.spool(BLACK))
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record(job_id).model_copy(update={
            "material": first,
            "spools": (SpoolUse(material=first, from_layer=1),
                       SpoolUse(material=to, from_layer=11, from_z=2.0)),
        }))
        ledger.record_started(job_id)
        row = ledger.get(job_id)
    assert row is not None
    return row


def test_the_stock_told_otherwise_since_isnt_written_over(shop: Shop) -> None:
    row = swap_started(shop)
    shop.load(CLEAR)
    with Ledger(shop.path) as ledger:
        after_print.at_end(ledger, row, 9, 100)
    assert shop.loaded() == CLEAR


def test_a_later_print_started_keeps_the_stock_its_own(shop: Shop) -> None:
    row = swap_started(shop)
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record("job-2"))
        ledger.record_started("job-2")
        after_print.at_end(ledger, row, None, None)
    assert shop.loaded() == WHITE


@pytest.mark.parametrize("layer", [None, 0])
def test_a_stop_that_doesnt_say_how_far_takes_the_last(shop: Shop, layer: int | None) -> None:
    row = swap_started(shop)
    with Ledger(shop.path) as ledger:
        after_print.at_end(ledger, row, 8, layer)
    assert shop.loaded() == BLACK


def test_a_last_spool_marked_empty_isnt_loaded_and_the_outcome_can_say_which_was(
    shop: Shop,
) -> None:
    row = swap_started(shop)
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    with Ledger(shop.path) as ledger:
        after_print.at_end(ledger, row, 9, 100)
        assert shop.loaded() == WHITE
        after_print.correct(ledger, row, CLEAR)
    assert shop.loaded() == CLEAR


@pytest.mark.parametrize(("shown", "left"), [("ours", WHITE), ("another", BLACK), ("", BLACK)])
def test_a_stops_layer_counts_only_while_the_printer_still_names_this_print(
    shop: Shop, shown: str, left: int
) -> None:
    row = swap_started(shop)
    named = row.gcode_name if shown == "ours" else shown

    class Stopped:
        def snapshot(self) -> Any:
            return frame("stopped", named, layer=5)  # under the swap at layer 11

    with Ledger(shop.path) as ledger:
        Watcher(ledger=ledger, printer=Stopped(), on_ended=lambda: None).look()  # type: ignore[arg-type]
    assert shop.loaded() == left


def test_two_prints_keep_their_own_spools(shop: Shop) -> None:
    swap_started(shop)
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record("job-2"))
        first, second = ledger.rows()
    assert len(first.spools) == 2 and second.spools == ()


# The terminal's gate and Home Assistant's cards
# --------------------------------------------------------------------------


def cards(ran: Ran) -> str:
    """Every markdown card of the shipped dashboard, rendered with what this job published."""
    said = {"sensor.3d_print_agent_status": ran.state,
            "sensor.3d_print_agent_outcome_part": ran.outcome}
    env = jinja2.Environment()
    dashboard = yaml.safe_load(
        (Path(__file__).parents[1] / "ha-configs" / "lovelace_3dprint.yaml").read_text())
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "markdown":
                found.append(env.from_string(node["content"]).render(
                    states=lambda entity: "0",
                    state_attr=lambda entity, key: said.get(entity, {}).get(key),
                ))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(dashboard)
    return "\n".join(found)


def test_the_terminals_gate_and_the_cards_say_insert_and_never_swap(shop: Shop) -> None:
    ran = shop.run("pause_at=2 for insert", "ok")

    terminal = "\n".join(_load("print_job")._gate_lines(ran.gate, turned=True))
    assert "  INSERT      this print pauses for an insert after layer 10" in terminal
    shown = cards(ran)
    assert "**Insert:** this print pauses for an insert after layer 10" in shown
    assert "Say in the note how the insert went." in shown
    assert "**Spool" not in shown
    for text in (terminal, shown):
        assert not re.search(r"swap|filament|colour change", text, re.I), text


def test_the_terminals_gate_and_the_cards_name_a_swaps_spools(shop: Shop) -> None:
    ran = shop.run("colour_change=2 to #2", "ok")

    terminal = "\n".join(_load("print_job")._gate_lines(ran.gate, turned=True))
    assert ("  SWAP        this print pauses for a swap to spool #2, Elegoo PLA, black, "
            "after layer 10") in terminal
    assert "  SPOOL       it starts on spool #1, Elegoo PLA, white, as loaded" in terminal
    shown = cards(ran)
    assert "**Swap:** this print pauses for a swap to spool #2" in shown
    assert "**Spool:** it starts on spool #1, Elegoo PLA, white, as loaded." in shown
    assert "Say in the note how the swap went." in shown
    assert "**Spools:** spool #1, Elegoo PLA, white, from layer 1; spool #2" in shown
    assert "\n**Swap:**" in shown and "\n**Spool:**" in shown, "each on a line of its own"
