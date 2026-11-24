"""A pause says swap or insert, and a swap records the spool swapped to (mqucifer/print-shop#218).

Each print here runs through the real review at the terminal, gate, ledger, watcher and outcome
desk, on fakes: a recorded slice with the pause put in where Orca puts it, a made-up stock, a
broker in a list and a printer that answers from frames. Nothing is the Sponsor's.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import select
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from print_shop.orchestrator import daemon as daemon_module
from print_shop.orchestrator import graph as graph_module
from print_shop.orchestrator.daemon import Daemon, JobResult
from print_shop.orchestrator.graph import why_not_start
from print_shop.orchestrator.outcome import OutcomeDesk, Watcher
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup import pauses as pauses_module
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
from tests.test_runtime import settings  # noqa: F401  (the fixture)
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
        self.told: list[str] = []
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
        """A load recorded, and nothing said of a print being over."""
        with Inventory(self.path) as stock:
            stock.load(spool)

    def says(self, spool: int) -> None:
        """The person's word saying both: that print is over, and `spool` is on now."""
        with Inventory(self.path) as stock:
            found = stock.unknown("cc1")
            assert found is not None, "there's nothing to say is over"
            stock.load(spool, over=found)

    def told_so_far(self, bridge: MemoryBridge) -> str:
        return str(bridge.retained_json(gate_topic(CFG))["held"]).removeprefix("Not started: ")

    def loaded(self) -> int | None:
        with Inventory(self.path) as stock:
            spool = stock.loaded("cc1")
        return spool.id if spool is not None else None

    def run(
        self, *typed: str, press: str = "approve", ends: str = "completed",
        at_gate: Callable[[], None] | None = None,
        held: Callable[[MemoryBridge], None] | None = None, gate_wait_s: float = 60.0,
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
        printer = self.printer = FramePrinter(after_start=["printing", ends])
        self.told = []
        with Ledger(self.path) as ledger:
            daemon = Daemon(
                cfg=CFG, profiles=profiles, workdir=self.jobs, printer=printer,
                slicer=PausingSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
                proposer=Proposer(), asker=lambda questions: (),
                reviewer=cli_reviewer(read=read, write=said.append, asks=lambda: True),
                watch_printer=printer, watch_poll_s=0.01, gate_wait_s=gate_wait_s,
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
                    if held is not None:
                        wait_until(lambda: bool(bridge.retained_json(gate_topic(CFG)).get("held")))
                        held(bridge)
                result = daemon.wait_for_finish(job_id, timeout_s=30, tell=self.told.append)
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
        return again(record.row, record.part_path, record.printers, record.part_found,
                     record.on_hand).corrections


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
    assert shop.loaded() == WHITE and not_known(shop), "not moved by itself"
    line = shop.correction_line(first.job_id) if by == "typed" else "use"

    # LOAD FIRST is asked at `ok`, and Enter is later: it's loaded at the gate.
    second = shop.run(line, "ok", "", at_gate=lambda: shop.says(WHITE))

    assert second.result.printed, second.result.error
    assert spools(second.row) == [(WHITE, 1), (BLACK, 11)]
    assert shop.loaded() == WHITE and not_known(shop)


def test_an_insert_never_says_swap_or_filament_at_any_step(shop: Shop) -> None:
    ran = shop.run("pause_at=2 for insert because the nuts go in", "ok")

    assert ran.result.printed, ran.result.error
    assert ran.row is not None
    # The review, from its first heading (mqucifer/print-shop#223): it's shown once here.
    reviewed = ran.terminal[ran.terminal.rindex("\nLOOK FIRST\n"):]
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


def test_a_row_from_before_reads_back_as_it_was(shop: Shop, tmp_path: Path) -> None:
    ran = shop.run("pause_at=2 for insert because it was typed then", "ok")
    db = sqlite3.connect(shop.path)
    # The file as it was before: no table of spools, and nothing said of what a pause was for.
    db.executescript("DROP TABLE print_spools; DROP TABLE print_swaps; "
                     "DROP TRIGGER stock_changed_loaded; DROP TRIGGER stock_changed_colour; "
                     "DROP TABLE swap_not_known; "
                     "DROP TABLE stock_changes;")
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

    assert looked == row and row is not None and row.spools == () and row.ended_with is None
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
    ran = shop.run("pause_at=2 because a white floor", "2", "1", "9", "1", "#2", "ok")
    assert ran.prompts == ["> ", "pause> ", "spool> ", "spool> ", "spool> ", "spool> ", "> "]
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
    ran = shop.run("colour_change=2", "1", "3", "ok")
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "], "never chosen for them"
    assert spools(ran.row) == [(WHITE, 1), (CLEAR, 11)]


@pytest.mark.parametrize("answers", [("back",), ("2", "back"), ("2", "1", "back")])
def test_back_from_what_its_for_sets_no_pause(shop: Shop, answers: tuple[str, ...]) -> None:
    ran = shop.run("pause_at=2", *answers, "ok")
    assert "Back to the review" in ran.terminal
    assert ran.row is not None and ran.row.corrections == () and ran.row.spools == ()


def test_a_height_typed_again_has_this_reviews_purpose_preselected(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #1 to #2", "pause_at=3", "", "", "", "ok")
    assert "Enter for a swap, as it is now" in ran.terminal
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 16)]


def test_a_typed_pause_has_the_past_prints_purpose_preselected(shop: Shop) -> None:
    shop.run("pause_at=2 for insert because the nuts go in", "ok")
    ran = shop.run("pause_at=2", "", "ok")
    assert "Enter for an insert, as on cube.stl" in ran.terminal
    assert kind_of(ran.row) == "insert"


def test_a_pause_at_another_height_doesnt_come_from_the_past_print(shop: Shop) -> None:
    """Enter would have made an insert the swap of the print before it."""
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(WHITE)
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
    ("colour_change=2 from #1 to #1",
     "it starts on spool #1, Elegoo PLA, white, so it swaps to another"),
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
    refused = parse_answer("colour_change=2 from #1 to #2", request)
    assert isinstance(refused, str) and "there's no stock to name a spool from" in refused


# A carried swap never comes back reversed
# --------------------------------------------------------------------------


def test_a_swap_between_varieties_isnt_offered_reversed(shop: Shop) -> None:
    """print-shop-ux, on 4f33d68: white then clear, used again with clear loaded, came back
    clear then white."""
    shop.run("colour_change=2 from #1 to #3", "ok")
    shop.says(CLEAR)

    again_ = shop.run("use 1", "stop")

    why = ("that print started on spool #1, Elegoo PLA, and this job is sliced for "
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
    shop.run("colour_change=2 from #1 to #3", "ok")
    shop.says(CLEAR)
    refused = shop.run("use 1", "stop")
    assert not refused.result.printed
    shop.load(WHITE)

    again_ = shop.run("use", "ok")

    assert again_.result.printed, again_.result.error
    assert spools(again_.row) == [(WHITE, 1), (CLEAR, 11)]


def test_the_desks_line_for_it_is_refused_too(shop: Shop) -> None:
    first = shop.run("colour_change=2 from #1 to #3", "ok")
    shop.says(CLEAR)
    again_ = shop.run(shop.correction_line(first.job_id), "stop")
    assert "the job is sliced for Elegoo PETG, and spool #1 is Elegoo PLA" in again_.terminal


def test_typed_at_that_prints_height_its_start_is_never_defaulted(shop: Shop) -> None:
    shop.run("colour_change=2 from #1 to #3", "ok")
    shop.says(CLEAR)
    again_ = shop.run("colour_change=2", "", "back", "stop")
    assert "that print started on spool #1, Elegoo PLA, and this job" in " ".join(
        again_.terminal.split())
    assert again_.prompts == ["> ", "spool> ", "spool> ", "> "], "Enter alone chooses nothing"
    assert "swap to?" not in again_.terminal


def test_a_first_spool_used_up_isnt_swapped_for_the_second(shop: Shop) -> None:
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(BLACK)
    with Inventory(shop.path) as stock:
        stock.empty(WHITE)
    again_ = shop.run("use 1", "stop")
    assert "that print started on spool #1, Elegoo PLA, white, which isn't on hand now" in (
        " ".join(again_.terminal.split()))
    assert "[x] 1" not in again_.terminal and "spool> " not in again_.prompts


def test_a_first_spool_used_up_during_the_review_is_refused_at_use(shop: Shop) -> None:
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(BLACK)
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
    ran = shop.run("colour_change=2 from #1 to #3", "ok")
    assert ("note: spool #3 is Elegoo PETG, and the slice has one filament's settings, those of "
            "Elegoo PLA") in ran.terminal
    assert shop.loaded() == WHITE


def test_the_same_variety_at_a_swap_isnt_noted(shop: Shop) -> None:
    assert "one filament's settings" not in shop.run("colour_change=2 from #1 to #2", "ok").terminal


def test_start_refuses_a_swap_whose_first_spool_isnt_loaded(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The print node's own refusal, with the gate's check bypassed."""
    monkeypatch.setattr(daemon_module, "why_not_start", lambda state, ledger: "")
    ran = shop.run("colour_change=2 from #2 to #1", "ok", "")

    assert "LOAD FIRST: it starts on spool #2, Elegoo PLA, black" in ran.terminal
    assert ran.gate["spools"].startswith("LOAD FIRST")
    assert shop.printer.calls == []
    assert not ran.result.printed and ran.row is None
    assert "this print starts on spool #2, Elegoo PLA, black" in (ran.result.error or "")
    assert "nothing was uploaded or printed" in (ran.result.error or "")
    assert shop.loaded() == WHITE


def test_start_refuses_when_the_first_was_unloaded_while_the_job_waited(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon_module, "why_not_start", lambda state, ledger: "")
    ran = shop.run("colour_change=2 from #1 to #2", "ok", at_gate=lambda: shop.load(CLEAR))
    assert not ran.result.printed and ran.row is None
    assert "has spool #3, Elegoo PETG, clear loaded" in (ran.result.error or "")


def test_a_colour_corrected_while_waiting_is_the_one_kept(shop: Shop) -> None:
    def edit() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, colour="cream")
            stock.edit(BLACK, colour="charcoal")

    ran = shop.run("colour_change=2 from #1 to #2", "ok", at_gate=edit)
    assert ran.row is not None and ran.row.material is not None and ran.row.profiles is not None
    assert [s.material.colour for s in ran.row.spools] == ["cream", "charcoal"]
    assert ran.row.profiles.material == ran.row.material == ran.row.spools[0].material


def test_a_pause_taken_away_swaps_nothing(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #1 to #2", "pause_at=none", "ok")
    assert ran.row is not None and ran.row.spools == ()
    assert ran.gate["pause"] == ran.gate["pause_for"] == ran.gate["spools"] == ""
    assert shop.loaded() == WHITE and not_known(shop) == ""


def test_a_rejected_job_moves_no_stock(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #1 to #2", "ok", press="reject")
    assert not ran.result.printed and ran.row is None and shop.loaded() == WHITE
    assert not_known(shop) == ""


# At the outcome
# --------------------------------------------------------------------------


def test_a_spool_that_isnt_offered_or_is_replayed_corrects_nothing(shop: Shop) -> None:
    def wrong(bridge: MemoryBridge) -> None:
        bridge.deliver(outcome_spool_topic(CFG), "spool #9, made up")
        bridge.deliver(outcome_spool_topic(CFG), over_on("spool #3, Elegoo PETG, clear"),
                       retain=True)
        assert bridge.retained_json(outcome_topic(CFG))["spool"] == "as recorded"

    ran = shop.run("colour_change=2 from #1 to #2", "ok", at_outcome=wrong)
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)] and shop.loaded() == WHITE
    assert not_known(shop)


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


def over_on(named: str) -> str:
    """The outcome select's words, which say both."""
    return f"{named} is on now, and that print is over"


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
    ran = shop.run("colour_change=2 from #1 to #2", "ok")

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


# The cold review of 4f33d68
# --------------------------------------------------------------------------


def ends(
    shop: Shop, row: Row, how: str = "completed", *, layer: int | None = 100,
    named: str | None = None, status: int | None = None,
) -> None:
    """The watcher sees `row`'s print end, as the printer says it."""
    seen = frame(how, row.gcode_name if named is None else named, layer=layer)
    if status is not None:
        seen = seen.model_copy(update={"print_status": status})

    class Printer:
        def snapshot(self) -> Any:
            return seen

    with Ledger(shop.path) as ledger:
        Watcher(ledger=ledger, printer=Printer(), on_ended=lambda: None).look()  # type: ignore[arg-type]


def answered_with(shop: Shop, spool: str) -> dict[str, Any]:
    """The outcome desk's oldest print answered a success, ended with `spool`."""
    bridge = MemoryBridge()
    with Ledger(shop.path) as ledger:
        desk = OutcomeDesk(ledger=ledger, cfg=CFG,
                           publish=lambda topic, payload: bridge.publish(topic, payload, True))
        for topic, handler in desk.topics.items():
            bridge.subscribe(topic, handler)
        desk.refresh()
        bridge.deliver(outcome_spool_topic(CFG), spool)
        bridge.deliver(outcome_result_topic(CFG), "success")
        bridge.deliver(outcome_symptom_topic(CFG), "none")
        bridge.deliver(outcome_submit_topic(CFG), "record")
    return dict(bridge.retained_json(outcome_topic(CFG)))


def kept(shop: Shop, job_id: str = "job-1") -> Row:
    with Ledger(shop.path) as ledger:
        row = ledger.get(job_id)
    assert row is not None
    return row


def stock_write_fails(shop: Shop, *, fails: bool) -> None:
    db = sqlite3.connect(shop.path)
    db.executescript(
        "CREATE TRIGGER no_load BEFORE UPDATE OF state ON spools WHEN NEW.state = 'loaded' "
        "BEGIN SELECT RAISE(ABORT, 'the stock is busy'); END;"
        if fails else "DROP TRIGGER no_load;")
    db.commit()
    db.close()


def test_a_record_the_stock_couldnt_take_is_said_and_can_be_pressed_again(shop: Shop) -> None:
    ends(shop, swap_started(shop))
    stock_write_fails(shop, fails=True)
    shown = answered_with(shop, over_on("spool #3, Elegoo PETG, clear"))
    row = kept(shop)
    assert row.result is None and row.ended_with is None and not_known(shop), "nothing half done"
    assert "the stock is busy" in (shown.get("error") or "")
    stock_write_fails(shop, fails=False)
    answered_with(shop, over_on("spool #3, Elegoo PETG, clear"))
    ended_with = kept(shop).ended_with
    assert ended_with is not None and ended_with.spool_id == CLEAR
    assert shop.loaded() == CLEAR and not_known(shop) == ""


def test_a_spool_edited_after_its_picked_is_picked_again_by_its_new_colour(shop: Shop) -> None:
    ends(shop, swap_started(shop))  # finding 11, and the fourth read of d196fb1
    bridge = MemoryBridge()
    with Ledger(shop.path) as ledger:
        desk = OutcomeDesk(ledger=ledger, cfg=CFG,
                           publish=lambda topic, payload: bridge.publish(topic, payload, True))
        for topic, handler in desk.topics.items():
            bridge.subscribe(topic, handler)
        desk.refresh()
        bridge.deliver(outcome_spool_topic(CFG), over_on("spool #3, Elegoo PETG, clear"))
        with Inventory(shop.path) as stock:
            stock.edit(CLEAR, colour="natural")
        bridge.deliver(outcome_result_topic(CFG), "success")
        shown = bridge.retained_json(outcome_topic(CFG))["spool"]
        assert shown in bridge.retained_json(outcome_spool_select(CFG)[0])["options"]
        bridge.deliver(outcome_symptom_topic(CFG), "none")
        bridge.deliver(outcome_submit_topic(CFG), "record")
        assert kept(shop).result is None, "the words it was picked by aren't the spool's now"
        bridge.deliver(outcome_spool_topic(CFG), over_on("spool #3, Elegoo PETG, natural"))
        bridge.deliver(outcome_submit_topic(CFG), "record")
    ended_with = kept(shop).ended_with
    assert ended_with is not None and (ended_with.spool_id, ended_with.colour) == (CLEAR, "natural")


def test_the_desks_line_parses_back_with_the_first_spool_used_up(shop: Shop) -> None:
    # Finding 8.
    first = shop.run("wall_loops=3 because strong; colour_change=2 from #1 to #2", "ok")
    with Inventory(shop.path) as stock:
        stock.empty(WHITE)
        new = stock.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA",
                              colour="white")
        stock.load(new.id)
    shop.says(new.id)
    line = shop.correction_line(first.job_id)
    assert "wall_loops=3 because strong" in line and "#1" not in line

    second = shop.run(line, str(new.id), "ok")

    assert second.result.printed, second.result.error
    assert spools(second.row) == [(new.id, 1), (BLACK, 11)]
    assert second.row is not None
    assert "wall_loops" in [c.key for c in second.row.corrections or ()]


def test_the_desks_line_parses_back_with_the_second_spool_used_up(shop: Shop) -> None:
    first = shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(WHITE)
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    second = shop.run(shop.correction_line(first.job_id), "3", "ok")
    assert spools(second.row) == [(WHITE, 1), (CLEAR, 11)]


def test_enter_preselects_from_the_print_picked_in_the_same_answer(shop: Shop) -> None:
    shop.run("pause_at=2 for insert", "ok")  # finding 13
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(WHITE)
    ran = shop.run("from 2; pause_at=2", "", "ok")
    assert "Enter for an insert" in ran.terminal
    assert kind_of(ran.row) == "insert"


def test_a_cleared_item_isnt_preselected(shop: Shop) -> None:
    shop.run("pause_at=2 for insert", "ok")
    ran = shop.run("clear 1; pause_at=2", "", "2", "back", "stop")
    assert "Enter for an insert" not in ran.terminal


def test_enter_never_starts_on_the_spool_the_last_swap_left(shop: Shop) -> None:
    """Asked afresh with the second left loaded, Enter would be the swap reversed."""
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(BLACK)
    ran = shop.run("colour_change=4", "", "back", "stop")
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "]


def test_a_superscript_digit_at_the_spool_prompt_is_asked_again(shop: Shop) -> None:
    ran = shop.run("colour_change=2", "1", "\u00b2", "2", "ok")
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)]


def test_the_readings_colour_is_the_spools_as_the_print_started(shop: Shop) -> None:
    def edit() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(BLACK, colour="charcoal")

    ran = shop.run("colour_change=2 from #1 to #2", "ok", at_gate=edit)
    assert ran.row is not None and ran.row.reading is not None
    assert "charcoal" in ran.row.reading.pauses[0].line()


def test_reprinting_an_insert_the_first_review_doesnt_say_swap(shop: Shop) -> None:
    shop.run("pause_at=2 for insert", "ok")
    ran = shop.run("use", "ok")
    assert not re.search("swap", ran.terminal, re.I), ran.terminal


def test_the_card_points_at_the_select_only_where_its_shown(shop: Shop) -> None:
    ran = shop.run("pause_at=2 for insert", "ok")
    outcome = {**ran.outcome, "spools": "spool #1, from layer 1", "spool_choices": 0}
    assert "pick it below" not in cards(dataclasses.replace(ran, outcome=outcome))


# (a) An Approve with the spool not loaded keeps the gate open
# --------------------------------------------------------------------------

#: A swap that's to start on black, with white loaded, and "later" at the question on ok.
UNLOADED = ("colour_change=2 from #2 to #1", "ok", "")


def starts(shop: Shop) -> int:
    return shop.printer.verbs.count("start")


def test_an_approve_with_the_first_not_loaded_starts_nothing_and_waits(shop: Shop) -> None:
    seen: dict[str, Any] = {}

    def held(bridge: MemoryBridge) -> None:
        seen.update(gate=bridge.retained_json(gate_topic(CFG)),
                    state=bridge.retained_json(state_topic(CFG)), calls=list(shop.printer.calls))
        shop.load(BLACK)
        bridge.press(CFG, "approve")

    ran = shop.run(*UNLOADED, held=held)

    assert seen["calls"] == [], "nothing read, uploaded or started"
    why = seen["gate"]["held"]
    assert seen["gate"]["pending"] is True and seen["state"]["status"] == "awaiting_approval"
    assert seen["state"]["held"] == why
    assert why.startswith("Not started: this print starts on spool #2, Elegoo PLA, black")
    assert "`stock.py load 2`), then approve again" in why
    assert shop.told == [why], "the terminal says it too"
    assert ran.result.printed and starts(shop) == 1
    assert spools(ran.row) == [(BLACK, 1), (WHITE, 11)]


def test_a_tap_before_its_loaded_never_starts_it_afterwards(shop: Shop) -> None:
    def held(bridge: MemoryBridge) -> None:
        shop.load(BLACK)
        time.sleep(0.4)
        assert starts(shop) == 0, "the first tap is spent"
        assert bridge.retained_json(gate_topic(CFG))["pending"] is True
        bridge.press(CFG, "reject")

    ran = shop.run(*UNLOADED, held=held)
    assert not ran.result.printed and ran.row is None and starts(shop) == 0


def test_a_tap_queued_during_the_check_is_never_kept(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = why_not_start
    queued: list[str] = []
    bridges: list[MemoryBridge] = []
    press = MemoryBridge.press

    def pressed(bridge: MemoryBridge, *args: Any, **kwargs: Any) -> None:
        bridges.append(bridge)
        press(bridge, *args, **kwargs)

    def check(state: Any, ledger: Any) -> str:
        why = real(state, ledger)
        if why and not queued:
            queued.append(why)
            press(bridges[0], CFG, "approve")  # the second of a double tap
            shop.load(BLACK)
        return why

    monkeypatch.setattr(MemoryBridge, "press", pressed)
    monkeypatch.setattr(daemon_module, "why_not_start", check)

    def held(bridge: MemoryBridge) -> None:
        time.sleep(0.4)
        assert starts(shop) == 0, "loaded by now, and only an earlier tap was queued"
        bridge.press(CFG, "reject")

    ran = shop.run(*UNLOADED, held=held)
    assert queued and not ran.result.printed and starts(shop) == 0


@pytest.mark.parametrize("typed", [UNLOADED, ("colour_change=2 from #1 to #2", "ok")])
def test_a_reject_during_the_check_is_kept(
    shop: Shop, monkeypatch: pytest.MonkeyPatch, typed: tuple[str, ...]
) -> None:
    bridges: list[MemoryBridge] = []
    press = MemoryBridge.press
    real = why_not_start

    def pressed(bridge: MemoryBridge, *args: Any, **kwargs: Any) -> None:
        bridges.append(bridge)
        press(bridge, *args, **kwargs)

    def check(state: Any, ledger: Any) -> str:
        press(bridges[0], CFG, "reject")
        return real(state, ledger)

    monkeypatch.setattr(MemoryBridge, "press", pressed)
    monkeypatch.setattr(daemon_module, "why_not_start", check)
    ran = shop.run(*typed)
    assert not ran.result.printed and ran.result.error is None and starts(shop) == 0


def test_a_double_tap_with_it_loaded_starts_once_at_most(shop: Shop) -> None:
    def twice(bridge: MemoryBridge) -> None:
        shop.load(BLACK)
        bridge.press(CFG, "approve")
        bridge.press(CFG, "approve")

    ran = shop.run(*UNLOADED, held=twice)
    assert ran.result.printed and starts(shop) == 1


def test_a_double_tap_before_its_loaded_starts_nothing(shop: Shop) -> None:
    def twice(bridge: MemoryBridge) -> None:
        bridge.press(CFG, "approve")
        bridge.press(CFG, "approve")
        time.sleep(0.4)
        shop.load(BLACK)
        time.sleep(0.4)
        assert starts(shop) == 0
        bridge.press(CFG, "reject")

    assert not shop.run(*UNLOADED, held=twice).result.printed


def test_the_hour_still_ends_a_held_job(shop: Shop) -> None:
    began: list[float] = []

    def keep_pressing(bridge: MemoryBridge) -> None:
        while bridge.retained_json(gate_topic(CFG)).get("pending"):
            assert time.monotonic() - began[0] < 4, "the presses extended the wait"
            bridge.press(CFG, "approve")
            time.sleep(0.1)

    ran = shop.run(*UNLOADED, gate_wait_s=1.0, held=keep_pressing,
                   at_gate=lambda: began.append(time.monotonic()))
    assert time.monotonic() - began[0] < 4
    assert not ran.result.printed and "Timed out waiting for Approve" in (ran.result.error or "")
    with Ledger(shop.path) as ledger:
        assert ledger.unprinted(ran.job_id) is not None
    assert starts(shop) == 0


def test_a_check_that_cant_be_made_holds(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(state: Any, ledger: Any) -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(daemon_module, "why_not_start", broken)
    ran = shop.run("colour_change=2 from #1 to #2", "ok",
                   held=lambda bridge: bridge.press(CFG, "reject"))
    assert not ran.result.printed and "couldn't be checked" in shop.told[0]


def test_the_held_gate_is_on_the_card(shop: Shop) -> None:
    state: dict[str, Any] = {}

    def held(bridge: MemoryBridge) -> None:
        state.update(bridge.retained_json(state_topic(CFG)))
        bridge.press(CFG, "reject")

    ran = shop.run(*UNLOADED, held=held)
    shown = " ".join(cards(dataclasses.replace(ran, state=state)).split())
    assert "**Not started:** this print starts on spool #2, Elegoo PLA, black" in shown


# (b) The load is recorded on a typed y, and only that
# --------------------------------------------------------------------------


def test_a_typed_y_at_ok_records_the_load(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #2 to #1", "ok", "y")
    assert "Is spool #2, Elegoo PLA, black, on the printer now?" in ran.terminal
    assert ran.prompts == ["> ", "> ", "loaded> "]
    assert ran.result.printed and shop.told == []
    assert spools(ran.row) == [(BLACK, 1), (WHITE, 11)]


@pytest.mark.parametrize("typed", ["", "n", "yes", "ok", "2", "load 2", "y please"])
def test_anything_but_a_typed_y_writes_nothing(shop: Shop, typed: str) -> None:
    ran = shop.run("colour_change=2 from #2 to #1", "ok", typed, press="reject")
    assert ran.prompts[-1] == "loaded> "
    assert shop.loaded() == WHITE


def test_stop_at_the_review_is_never_asked_and_writes_nothing(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #2 to #1", "stop")
    assert "loaded> " not in ran.prompts and shop.loaded() == WHITE


def test_the_first_loaded_already_isnt_asked(shop: Shop) -> None:
    assert "loaded> " not in shop.run("colour_change=2 from #1 to #2", "ok").prompts


# (d) Every job starts on what it was prepared for
# --------------------------------------------------------------------------


def add_spool(shop: Shop, colour: str, variety: str = "Elegoo PLA", kind: str = "PLA",
              tuning: str | None = None, load: bool = True) -> int:
    with Inventory(shop.path) as stock:
        spool = stock.add_spool(manufacturer="Elegoo", type=kind, variety=variety, colour=colour,
                                tuning=tuning)
        if load:
            stock.load(spool.id)
    return spool.id


def reject(bridge: MemoryBridge) -> None:
    bridge.press(CFG, "reject")


@pytest.mark.parametrize(("colour", "variety", "kind", "tuning"), [
    ("black", "Elegoo PLA", "PLA", None),
    ("white", "Elegoo PETG", "PETG", None),
    ("white", "Elegoo PLA", "PLA", "another tuning"),
])
def test_a_spool_that_would_print_otherwise_holds_a_job_with_no_swap(
    shop: Shop, colour: str, variety: str, kind: str, tuning: str | None
) -> None:
    ran = shop.run(
        "ok", held=reject,
        at_gate=lambda: void(add_spool(shop, colour, variety, kind, tuning)),
    )
    assert not ran.result.printed and starts(shop) == 0
    assert "this job was prepared for spool #1, Elegoo PLA, white" in shop.told[0]
    assert "`stock.py load 1`" in shop.told[0]


def void(_: object) -> None:
    return None


@pytest.mark.parametrize(("colour", "variety", "kind", "instead"), [
    ("black", "Elegoo PLA", "PLA", "to print in black"),
    ("natural", "Elegoo PETG", "PETG", "to print with spool #4, Elegoo PETG, natural"),
])
def test_a_held_job_says_the_way_out_too(
    shop: Shop, colour: str, variety: str, kind: str, instead: str
) -> None:
    """print-shop-ux, on b34a39b: a change of mind was only told to put the old colour back."""
    shop.run("ok", held=reject, at_gate=lambda: void(add_spool(shop, colour, variety, kind)))
    note = " (leaving out any variety or tuning its command named)" if kind == "PETG" else ""
    assert shop.told[0].endswith(
        f"(`stock.py load 1`), then approve again; or, {instead}, Reject and run the job again"
        f"{note}")


def test_a_held_swap_and_an_edited_tuning_say_their_way_out(shop: Shop) -> None:
    shop.run(*UNLOADED, held=reject)
    assert shop.told[0].endswith(
        "then approve again; or, for other spools, Reject and run the job again")

    def retune() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, tuning="another tuning")

    shop.run("ok", at_gate=retune, held=reject)
    assert shop.told[0].endswith(
        "--tuning none`), then approve again; or, to print with it as it is, Reject and run the "
        "job again (leaving out any variety or tuning its command named)")


def test_a_spool_of_the_same_variety_colour_and_tuning_starts_and_is_recorded(shop: Shop) -> None:
    new: list[int] = []
    ran = shop.run("ok", at_gate=lambda: new.append(add_spool(shop, "White")))

    assert ran.result.printed, ran.result.error
    assert ran.row is not None and ran.row.material is not None and ran.row.profiles is not None
    assert ran.row.material.spool_id == new[0]
    assert ran.row.profiles.material == ran.row.material
    assert shop.told == []


def test_an_equal_spool_starts_a_swap_and_is_recorded_as_its_first(shop: Shop) -> None:
    new: list[int] = []
    ran = shop.run("colour_change=2 from #1 to #2", "ok",
                   at_gate=lambda: new.append(add_spool(shop, "white")))
    assert spools(ran.row) == [(new[0], 1), (BLACK, 11)] and shop.loaded() == new[0]
    assert not_known(shop)


def test_the_spool_swapped_to_is_never_taken_as_the_first(shop: Shop) -> None:
    other = add_spool(shop, "white", load=False)
    ran = shop.run(f"colour_change=2 from #1 to #{other}", "ok",
                   at_gate=lambda: shop.load(other), held=reject)
    assert not ran.result.printed and "this print starts on spool #1" in shop.told[0]


def test_a_tuning_edited_since_holds_the_gate(shop: Shop) -> None:
    def retune() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, tuning="another tuning")

    ran = shop.run("ok", at_gate=retune, held=reject)
    assert not ran.result.printed and "spool #1's tuning was changed" in shop.told[0]


def test_a_colour_corrected_on_the_same_spool_still_starts(shop: Shop) -> None:
    def edit() -> None:
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, colour="cream")

    ran = shop.run("ok", at_gate=edit)
    assert ran.result.printed and shop.told == []
    assert ran.row is not None and ran.row.material is not None
    assert ran.row.material.colour == "cream"


def test_with_the_gates_check_bypassed_start_still_refuses_any_jobs_other_spool(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(daemon_module, "why_not_start", lambda state, ledger: "")
    ran = shop.run("ok", at_gate=lambda: shop.load(CLEAR))
    assert not ran.result.printed and ran.row is None and shop.printer.calls == []
    assert "nothing was uploaded or printed" in (ran.result.error or "")


def test_a_swap_that_ended_unseen_is_seen_before_the_next_job_starts(shop: Shop) -> None:
    """The review's finding 1: prepared for white, and black's on since a swap ended unseen."""
    ran = shop.run("ok", at_gate=lambda: void(swap_started(shop, "earlier")), held=reject)
    assert not ran.result.printed and starts(shop) == 0
    assert shop.told[0].startswith("Not started: print-shop doesn't know what's on cc1")


def test_a_spool_changed_during_the_upload_is_never_started(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FramePrinter.upload

    def upload(printer: FramePrinter, path: Path) -> str:
        stored = real(printer, path)
        shop.load(CLEAR)
        return stored

    monkeypatch.setattr(FramePrinter, "upload", upload)
    ran = shop.run("ok")
    assert not ran.result.printed and ran.row is None and starts(shop) == 0
    assert "uploaded, and not started" in (ran.result.error or "")


def test_a_colour_corrected_during_the_upload_is_the_one_recorded(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What reading again after the upload gives, that the check with the row can't."""
    real = FramePrinter.upload

    def upload(printer: FramePrinter, path: Path) -> str:
        stored = real(printer, path)
        with Inventory(shop.path) as stock:
            stock.edit(WHITE, colour="cream")
        return stored

    monkeypatch.setattr(FramePrinter, "upload", upload)
    ran = shop.run("ok")
    assert ran.result.printed, ran.result.error
    assert ran.row is not None and ran.row.material is not None
    assert ran.row.material.colour == "cream"


# The second cold review, of 3bbd4f7
# --------------------------------------------------------------------------


def test_a_press_is_judged_on_the_stock_as_it_was_pressed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """N1: the printer was read first, up to a minute, and a load meanwhile started that press."""
    real = Watcher.look

    def slow(watcher: Watcher) -> None:
        time.sleep(0.8)
        real(watcher)

    monkeypatch.setattr(Watcher, "look", slow)
    later = threading.Timer(0.3, lambda: shop.load(BLACK))

    def held(bridge: MemoryBridge) -> None:
        later.join()
        time.sleep(0.3)
        assert starts(shop) == 0
        bridge.press(CFG, "reject")

    ran = shop.run(*UNLOADED, at_gate=later.start, held=held)
    assert not ran.result.printed and starts(shop) == 0


def test_the_check_never_waits_on_the_printer(shop: Shop, monkeypatch: pytest.MonkeyPatch) -> None:
    """N12: a slow printer held up Reject, and the hour."""
    real = Watcher.look

    def slow(watcher: Watcher) -> None:
        time.sleep(3)
        real(watcher)

    monkeypatch.setattr(Watcher, "look", slow)
    pressed: list[float] = []

    def held(bridge: MemoryBridge) -> None:
        assert time.monotonic() - pressed[0] < 1.5
        bridge.press(CFG, "reject")

    ran = shop.run(*UNLOADED, at_gate=lambda: pressed.append(time.monotonic()), held=held)
    assert not ran.result.printed


_TYPED_AHEAD = """
import sys, time
from pathlib import Path
from print_shop.inventory import Asked, Inventory
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.pauses import Pause, PauseFor
from print_shop.setup.review import cli_reviewer
from tests.test_colour_change import _request
import dataclasses

at = Path(sys.argv[1])
with Inventory(at) as stock:
    material = stock.material()
    spools = tuple(stock.spools())
black = material.of(next(s for s in spools if s.colour == "black"))
change = Change(key="pause_at", preset="", value="2", reason="a swap", source=Source.SPONSOR,
                pause=Pause(kind=PauseFor.SWAP, start=black, to=material))
request = dataclasses.replace(
    _request(), proposal=Proposal(changes=(change,)), material=material, stock=lambda: spools,
    stands=lambda: Asked(0, None),
    record_load=lambda spool, over: print("RECORDED", spool, flush=True))
print("READY", flush=True)
time.sleep(1)  # slicing, as it's typed
cli_reviewer()(request)
print("DONE", flush=True)
"""


def test_a_doubled_y_typed_ahead_never_answers_the_loads_question(shop: Shop) -> None:
    """N2: y approves too. On a real terminal, with input() itself."""
    ours, theirs = os.openpty()
    root = Path(__file__).parents[1]
    child = subprocess.Popen(
        [sys.executable, "-c", _TYPED_AHEAD, str(shop.path)],
        stdin=theirs, stdout=theirs, stderr=theirs, cwd=root, start_new_session=True,
        env={**os.environ, "PYTHONPATH": f"{root / 'src'}{os.pathsep}{root}"},
    )
    os.close(theirs)
    out = b""

    def until(word: bytes) -> None:
        nonlocal out
        end = time.monotonic() + 30
        while word not in out and time.monotonic() < end:
            if not select.select([ours], [], [], 0.2)[0]:
                continue
            try:
                got = os.read(ours, 65536)
            except OSError:
                break
            if not got:
                break
            out += got
        assert word in out, out.decode(errors="replace")[-1500:]

    try:
        until(b"READY")
        os.write(ours, b"y\ny\n")
        until(b"loaded> ")
        time.sleep(0.3)
        os.write(ours, b"\n")
        until(b"DONE")
    finally:
        child.kill()
        child.wait(timeout=10)
        os.close(ours)
    assert b"RECORDED" not in out


def test_a_database_error_at_the_y_doesnt_lose_the_job(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(stock: Inventory, *args: Any, **kwargs: Any) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Inventory, "load", locked)  # N7
    ran = shop.run("colour_change=2 from #2 to #1", "ok", "y", press="reject")
    assert ran.gate and ran.result.error is None
    assert "Not recorded: database is locked" in ran.terminal


def test_a_load_as_the_row_is_written_never_starts(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = graph_module._print_record  # N10

    def between(*args: Any, **kwargs: Any) -> Any:
        made = real(*args, **kwargs)
        shop.load(CLEAR)
        return made

    monkeypatch.setattr(graph_module, "_print_record", between)
    ran = shop.run("colour_change=2 from #1 to #2", "ok")
    assert not ran.result.printed and ran.row is None and starts(shop) == 0
    assert "uploaded, and not started" in (ran.result.error or "")
    assert shop.loaded() == CLEAR


def test_enter_is_no_first_spool_but_the_one_that_past_print_started_on(shop: Shop) -> None:
    fresh = shop.run("colour_change=2", "", "1", "2", "ok")
    assert fresh.prompts.count("spool> ") == 3, "nothing to take by Enter"
    assert "Enter for" not in fresh.terminal
    shop.says(WHITE)
    again_ = shop.run("colour_change=2", "", "", "ok")
    assert "Enter for spool #1, Elegoo PLA, white" in again_.terminal
    assert spools(again_.row) == [(WHITE, 1), (BLACK, 11)]


def test_a_reprints_line_with_another_print_picked_never_reverses_on_enter(shop: Shop) -> None:
    first = shop.run("colour_change=2 from #1 to #2", "ok")  # N9
    shop.says(BLACK)
    shop.run("pause_at=4 for insert", "ok", "ok")
    with Inventory(shop.path) as stock:
        stock.empty(WHITE)
    white = add_spool(shop, "white", load=False)
    line = shop.correction_line(first.job_id)
    assert line.startswith("colour_change=2 from ? to #2")

    again_ = shop.run(line, "", str(white), "ok", "y", "y")

    assert again_.prompts.count("spool> ") == 2, "Enter chose nothing, and #2 wasn't asked"
    assert spools(again_.row) == [(white, 1), (BLACK, 11)]


def test_the_desks_line_parses_back_with_the_firsts_tuning_edited(shop: Shop) -> None:
    first = shop.run("wall_loops=3 because strong; colour_change=2 from #1 to #2", "ok")
    shop.says(BLACK)
    with Inventory(shop.path) as stock:
        stock.edit(WHITE, tuning="another tuning")
    line = shop.correction_line(first.job_id)
    assert "colour_change=2 from ? to #2" in line
    ran = shop.run(line, "back", "stop")
    assert "spool> " in ran.prompts and "the job is sliced for" not in ran.terminal.split("> ")[0]


def test_a_typed_to_never_starts_on_a_prepared_spool_thats_used_up(shop: Shop) -> None:
    with Inventory(shop.path) as stock:
        material = stock.material()
        stock.empty(WHITE)
        on_hand = tuple(stock.spools())
    request = dataclasses.replace(_request(), material=material, stock=lambda: on_hand)
    said = parse_answer("colour_change=2 from #1 to #2", request)
    assert isinstance(said, str) and "spool #1 isn't on hand" in said


def test_held_the_gate_doesnt_still_say_load_first(shop: Shop) -> None:
    state: dict[str, Any] = {}

    def held(bridge: MemoryBridge) -> None:
        state.update(bridge.retained_json(state_topic(CFG)))
        bridge.press(CFG, "reject")

    shop.run(*UNLOADED, held=held)
    assert state["held"] and state["spools"] == ""


def test_a_hand_sliced_files_way_out_isnt_a_run_thats_refused(shop: Shop) -> None:
    with Inventory(shop.path) as stock:
        prepared = stock.material()
        stock.load(CLEAR)
        found = stock.why_not_start(prepared, by_hand=True)
    assert found is not None and "to print with" not in found.at_gate()


# What isn't known holds (the second review; the operator's 3)
# --------------------------------------------------------------------------


def not_known(shop: Shop) -> str:
    """Why a job would be held as what's on the printer isn't known, or ""."""
    with Inventory(shop.path) as stock:
        found = stock.unknown("cc1")
    return found.hold().at_gate() if found is not None else ""


def one_step(said: str, *spool_ids: int) -> None:
    """A hold says what isn't known, and the line to type with the likely spool's number."""
    assert said.startswith("print-shop doesn't know what's on cc1 since cube.stl's print"), said
    for spool_id in spool_ids:
        assert f"`stock.py load {spool_id}`" in said, said
    assert "then approve again" in said and "run the job again" not in said


def test_held_for_what_isnt_known_one_load_and_a_press_print_it(shop: Shop) -> None:
    """print-shop-ux's 3: one action, and the job is never run again."""
    ends(shop, swap_started(shop, "earlier"), "stopped", layer=11)
    answered_with(shop, "as recorded")

    def held(bridge: MemoryBridge) -> None:
        one_step(shop.told_so_far(bridge), BLACK, WHITE)
        shop.says(WHITE)
        bridge.press(CFG, "approve")

    ran = shop.run("ok", "", held=held)
    assert ran.result.printed, ran.result.error


def test_the_print_node_holds_to_it_with_the_gates_check_bypassed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    ends(shop, swap_started(shop, "earlier"), "stopped", layer=11)
    monkeypatch.setattr(daemon_module, "why_not_start", lambda state, ledger: "")
    ran = shop.run("ok")
    assert not ran.result.printed and shop.printer.calls == []
    assert "doesn't know what's on cc1" in (ran.result.error or "")


def test_the_y_clears_it_though_the_stock_has_that_spool(shop: Shop) -> None:
    ends(shop, swap_started(shop, "earlier"), "stopped", layer=11)
    answered_with(shop, "as recorded")
    ran = shop.run("colour_change=2 from #1 to #2", "ok", "y")
    assert "doesn't know what's on cc1" in ran.terminal and "LOAD FIRST" in ran.terminal
    assert ran.prompts[-1] == "loaded> "
    assert ran.result.printed, ran.result.error


def test_to_alone_asks_for_the_first_and_marks_the_one_loaded(shop: Shop) -> None:
    """print-shop-ux's 1 and 2."""
    ran = shop.run("colour_change=2 to #2", "", "1", "ok")
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "]
    assert "spool #1  Elegoo PLA  white  (loaded on cc1)" in ran.terminal
    assert "spool #2  Elegoo PLA  black  (on hand)" in ran.terminal
    assert spools(ran.row) == [(WHITE, 1), (BLACK, 11)]


def test_a_hold_for_whats_not_known_says_what_the_job_needs_in_the_same_line(shop: Shop) -> None:
    """print-shop-ux, on cc4eee7: black recorded as told, then held again for white."""
    ends(shop, swap_started(shop, "earlier"), "stopped", layer=11)
    shop.run("ok", "", held=reject)
    assert shop.told[0].endswith(
        "then approve again. This job needs spool #1, Elegoo PLA, white: if another's on, put "
        "that on first and record it (`stock.py load 1`)")


# The third cold review, of cc4eee7
# --------------------------------------------------------------------------


def sent_unconfirmed(shop: Shop, *, killed: bool, minutes_ago: int = 0) -> Row:
    """A white PLA to clear PETG print, sent and never seen to start."""
    with Inventory(shop.path) as stock:
        first = stock.material()
        to = first.of(stock.spool(CLEAR))
    with Ledger(shop.path) as ledger:
        ledger.record_start_requested(record("sent").model_copy(update={
            "material": first,
            "spools": (SpoolUse(material=first, from_layer=1),
                       SpoolUse(material=to, from_layer=11, from_z=2.0)),
        }))
        if not killed:
            ledger.record_start_failed("sent", "confirm timed out")
        if minutes_ago:
            at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
            with ledger.writing() as db:
                db.execute("UPDATE prints SET start_requested_at = ? WHERE job_id = 'sent'",
                           (at.isoformat(timespec="seconds"),))
    return kept(shop, "sent")


def test_the_desks_line_with_another_print_picked_never_takes_its_first(shop: Shop) -> None:
    """M2: A is white then black, B black then white and the one picked. A's line printed as B."""
    a = shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(BLACK)
    shop.run("colour_change=2 from #2 to #1", "ok")
    with Inventory(shop.path) as stock:
        stock.edit(WHITE, tuning="another tuning")
    shop.says(BLACK)
    line = shop.correction_line(a.job_id)
    assert "from ? to #2" in line

    ran = shop.run(line, "", "2", "back", "stop")

    assert ran.prompts == ["> ", "spool> ", "spool> ", "spool> ", "> "]
    assert "Enter for" not in ran.terminal
    assert "spool #2 is the one it swaps to" in ran.terminal


def test_to_typed_with_another_print_picked_has_no_enter_default(shop: Shop) -> None:
    shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(WHITE)
    ran = shop.run("colour_change=2 to #2", "", "back", "stop")
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "]
    assert "Enter for" not in ran.terminal


def test_from_question_mark_asks_where_a_swap_stands_already(shop: Shop) -> None:
    ran = shop.run("colour_change=2 from #1 to #2", "colour_change=3 from ? to #2", "", "back",
                   "stop")  # L7
    assert ran.prompts == ["> ", "> ", "spool> ", "spool> ", "> "]


def test_a_locked_stock_at_the_review_or_the_gate_doesnt_lose_the_job(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*args: Any, **kwargs: Any) -> str:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(pauses_module, "starting", locked)  # L9: both reads go through it
    ran = shop.run("colour_change=2 from #1 to #2", "ok")
    assert "couldn't be read" in ran.terminal and "couldn't be read" in ran.gate["spools"]
    assert ran.result.printed, ran.result.error


# The fourth cold read, of 1b3888c
# --------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["printing", "paused", "preheating"])
def test_a_busy_frame_with_no_file_name_isnt_another_print(shop: Shop, how: str) -> None:
    """F1: "" read as another file, so the print was over and a load under its swap cleared it."""
    row = swap_started(shop)
    ends(shop, row, how, layer=3, named="")
    assert kept(shop).ended_at is None, "it's still printing"
    assert not_known(shop)


def test_a_start_the_watcher_saw_first_is_no_error_to_the_print_node(shop: Shop) -> None:
    row = swap_started(shop)  # F3
    with Ledger(shop.path) as ledger:
        seen = kept(shop).started_at
        ledger.record_started(row.job_id)
        assert kept(shop).started_at == seen, "the first time seen stands"


def test_a_failed_confirm_after_the_watcher_saw_it_keeps_its_own_error(shop: Shop) -> None:
    row = swap_started(shop)
    with Ledger(shop.path) as ledger:
        ledger.record_start_failed(row.job_id, "the watch stream ended")
    assert kept(shop).start_error == "the watch stream ended"


def test_the_print_node_started_by_the_watcher_still_says_printed(
    shop: Shop, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = FramePrinter.start

    def start(printer: FramePrinter, name: str) -> Any:
        with Ledger(shop.path) as ledger:
            ledger.record_started(ledger.rows()[-1].job_id)
        return real(printer, name)

    monkeypatch.setattr(FramePrinter, "start", start)
    ran = shop.run("colour_change=2 from #1 to #2", "ok")
    assert ran.result.printed, ran.result.error


def test_the_desks_line_never_takes_its_second_from_the_print_picked(shop: Shop) -> None:
    """F4: A is white then black, black's used up. C, white then clear, is the one picked."""
    a = shop.run("colour_change=2 from #1 to #2", "ok")
    shop.says(WHITE)
    shop.run("colour_change=2 from #1 to #3", "ok")
    shop.says(WHITE)
    with Inventory(shop.path) as stock:
        stock.empty(BLACK)
    line = shop.correction_line(a.job_id)
    assert line.startswith("colour_change=2 from #1") and " to #" not in line

    ran = shop.run(line, "", "back", "stop")

    assert "Enter for" not in ran.terminal
    assert ran.prompts == ["> ", "spool> ", "spool> ", "> "]


def test_the_second_is_preselected_only_after_that_prints_own_first(shop: Shop) -> None:
    shop.run("colour_change=2 from #1 to #3", "ok")
    white = add_spool(shop, "white")
    shop.says(white)
    ran = shop.run("colour_change=2", str(white), "", "back", "stop")
    assert "Enter for spool #3" not in ran.terminal
