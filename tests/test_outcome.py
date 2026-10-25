"""When a print ends, the Sponsor is asked how it came out; nothing guesses it.

mqucifer/print-shop#2: "the outcome is asked through Home Assistant when the
print ends, the same way as the approval gate. A print with no answer stays
open, never guessed." Run against the same fakes as `tests/test_job_e2e.py`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import (
    OUTCOME_RESULTS,
    OUTCOME_SYMPTOMS,
    outcome_note_topic,
    outcome_result_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
)
from print_shop.ledger import Ledger, Result, Symptom
from print_shop.orchestrator.outcome import END_STATES, UNSET, OutcomeDesk, Watcher, print_ended
from print_shop.printer.centauri import READY_TO_START
from tests.fakes import FramePrinter, MemoryBridge, frame
from tests.test_job_e2e import CFG, Rig
from tests.test_ledger import record


def wait_until(check: Callable[[], bool], timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


# --------------------------------------------------------------------------
# What counts as ended
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["completed", "stopped", "error", "idle"])
def test_these_end_a_print(name: str) -> None:
    assert print_ended(frame(name, "job.gcode"))


@pytest.mark.parametrize("name", ["preheating", "printing", "paused"])
def test_these_do_not(name: str) -> None:
    assert not print_ended(frame(name, "job.gcode"))


def test_an_unknown_state_keeps_watching() -> None:
    snap = frame("printing", "job.gcode").model_copy(update={"print_status": 99})
    assert not print_ended(snap)
    assert not print_ended(snap.model_copy(update={"print_status": None}))


def test_every_ready_state_is_an_end_state() -> None:
    """If the printer is free to start, whatever ran before it is over."""
    assert READY_TO_START <= END_STATES


def test_the_selects_offer_exactly_the_ledgers_values() -> None:
    assert set(OUTCOME_RESULTS) == {UNSET, *Result}
    assert set(OUTCOME_SYMPTOMS) == {UNSET, *Symptom}


# --------------------------------------------------------------------------
# The watcher
# --------------------------------------------------------------------------


def started(ledger: Ledger, job_id: str = "job-1") -> str:
    ledger.record_start_requested(record(job_id))
    ledger.record_started(job_id)
    return f"cube_{job_id}.gcode"


def watcher(ledger: Ledger, printer: Any, ended: list[int]) -> Watcher:
    return Watcher(ledger=ledger, printer=printer, on_ended=lambda: ended.append(1))


class Frames:
    """A read-only printer that shows whatever frame the test sets."""

    def __init__(self, name: str, filename: str = "") -> None:
        self.name, self.filename, self.reads = name, filename, 0

    def snapshot(self) -> Any:
        self.reads += 1
        return frame(self.name, self.filename)

    def upload(self, local_path: Path) -> str:
        raise AssertionError("a watcher never uploads")

    def start(self, remote_name: str) -> Any:
        raise AssertionError("a watcher never starts")


def test_a_running_print_is_left_open_while_it_prints(tmp_path: Path) -> None:
    ledger, ended = Ledger(tmp_path / "l.sqlite"), list[int]()
    name = started(ledger)
    watcher(ledger, Frames("printing", name), ended).look()
    assert [r.job_id for r in ledger.running()] == ["job-1"]
    assert ended == []


def test_the_end_is_recorded_with_what_the_printer_said(tmp_path: Path) -> None:
    ledger, ended = Ledger(tmp_path / "l.sqlite"), list[int]()
    name = started(ledger)
    watcher(ledger, Frames("stopped", name), ended).look()
    row = ledger.get("job-1")
    assert row is not None
    assert row.ended_at is not None and row.printer_end_status == 8
    assert row.result is None
    assert ended == [1]


def test_another_file_on_the_printer_ends_ours_without_a_guess(tmp_path: Path) -> None:
    ledger, ended = Ledger(tmp_path / "l.sqlite"), list[int]()
    started(ledger)
    watcher(ledger, Frames("printing", "someone_else.gcode"), ended).look()
    row = ledger.get("job-1")
    assert row is not None
    assert row.ended_at is not None
    assert row.printer_end_status is None, "how it ended is not known"


def test_nothing_to_watch_means_no_read(tmp_path: Path) -> None:
    ledger, printer = Ledger(tmp_path / "l.sqlite"), Frames("idle")
    watcher(ledger, printer, []).look()
    assert printer.reads == 0


def test_a_start_that_raised_but_took_is_watched_after_all(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    ledger.record_start_failed("job-1", "RuntimeError: never left a ready state in 300s")
    printer = Frames("printing", "cube_job-1.gcode")
    w = watcher(ledger, printer, [])
    w.look()
    assert [r.job_id for r in ledger.running()] == ["job-1"]
    printer.name = "completed"
    w.look()
    row = ledger.get("job-1")
    assert row is not None and row.printer_end_status == 9
    assert row.start_error is not None, "the error stays on record"


def test_a_start_that_raised_and_did_not_take_stays_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    ledger.record_start_failed("job-1", "RuntimeError: printer refused to start: Ack=1")
    watcher(ledger, Frames("idle"), []).look()
    row = ledger.get("job-1")
    assert row is not None
    assert (row.started_at, row.ended_at, row.result) == (None, None, None)


def test_a_printer_that_does_not_answer_keeps_the_row_open(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    started(ledger)

    class Off(Frames):
        def snapshot(self) -> Any:
            raise TimeoutError("no answer")

    w = Watcher(ledger=ledger, printer=Off("idle"), on_ended=lambda: None, poll_s=0.01)
    w.start()
    time.sleep(0.05)
    w.stop()
    assert [r.job_id for r in ledger.running()] == ["job-1"]


# --------------------------------------------------------------------------
# The desk
# --------------------------------------------------------------------------


@pytest.fixture
def desk_rig(tmp_path: Path) -> Iterator[tuple[Ledger, MemoryBridge, OutcomeDesk]]:
    ledger, bridge = Ledger(tmp_path / "l.sqlite"), MemoryBridge()
    # Retained, as the daemon's own publish is: the gate outlives a dashboard reload.
    desk = OutcomeDesk(
        ledger=ledger, cfg=CFG, publish=lambda t, p: bridge.publish(t, p, retain=True)
    )
    for topic, handler in desk.topics.items():
        bridge.subscribe(topic, handler)
    yield ledger, bridge, desk


def ended(ledger: Ledger, job_id: str = "job-1", status: int = 9) -> None:
    started(ledger, job_id)
    ledger.record_ended(job_id, status)


def shown(bridge: MemoryBridge) -> Any:
    return bridge.retained_json(outcome_topic(CFG))


def answer(bridge: MemoryBridge, result: str, symptom: str, note: str = "") -> None:
    bridge.deliver(outcome_result_topic(CFG), result)
    bridge.deliver(outcome_symptom_topic(CFG), symptom)
    if note:
        bridge.deliver(outcome_note_topic(CFG), note)
    bridge.deliver(outcome_submit_topic(CFG), "record")


def test_nothing_waiting_shows_nothing(desk_rig: Any) -> None:
    _, bridge, desk = desk_rig
    desk.refresh()
    assert shown(bridge) == {"pending": False, "waiting": 0}


def test_an_ended_print_is_asked_about(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    gate = shown(bridge)
    assert gate["pending"] is True
    assert gate["job_id"] == "job-1"
    assert gate["stl_name"] == "cube.stl"
    assert gate["printer_end"] == "completed"
    assert (gate["result"], gate["symptom"]) == (UNSET, UNSET)


def test_an_answer_is_recorded_and_the_gate_closes(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    answer(bridge, "failed", "adhesion", "corner lifted at layer 3")
    row = ledger.get("job-1")
    assert row is not None
    assert (row.result, row.symptom, row.note) == (
        Result.FAILED,
        Symptom.ADHESION,
        "corner lifted at layer 3",
    )
    assert shown(bridge) == {"pending": False, "waiting": 0}


def test_record_with_nothing_chosen_is_refused_and_says_why(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    bridge.deliver(outcome_submit_topic(CFG), "record")
    assert ledger.awaiting_answer() != []
    assert "choose a result and a symptom" in shown(bridge)["error"]


def test_an_answer_that_does_not_add_up_is_shown_not_stored(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    answer(bridge, "success", "warping")
    assert ledger.awaiting_answer() != []
    assert "a success has no symptom" in shown(bridge)["error"]


def test_a_retained_choice_or_record_is_not_an_answer(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    bridge.deliver(outcome_result_topic(CFG), "success", retain=True)
    bridge.deliver(outcome_symptom_topic(CFG), "none", retain=True)
    assert shown(bridge)["result"] == UNSET
    bridge.deliver(outcome_result_topic(CFG), "success")
    bridge.deliver(outcome_symptom_topic(CFG), "none")
    bridge.deliver(outcome_submit_topic(CFG), "record", retain=True)
    assert ledger.awaiting_answer() != []


def test_a_value_off_the_list_is_ignored(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger)
    desk.refresh()
    bridge.deliver(outcome_symptom_topic(CFG), "looked fine I guess")
    assert shown(bridge)["symptom"] == UNSET


def test_a_choice_with_nothing_on_show_goes_nowhere(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    desk.refresh()
    bridge.deliver(outcome_result_topic(CFG), "success")
    ended(ledger)
    desk.refresh()
    assert shown(bridge)["result"] == UNSET, "a press before the print was shown is dropped"


def test_the_oldest_is_asked_first_and_the_draft_does_not_carry_over(desk_rig: Any) -> None:
    ledger, bridge, desk = desk_rig
    ended(ledger, "job-1")
    ended(ledger, "job-2")
    desk.refresh()
    assert (shown(bridge)["job_id"], shown(bridge)["waiting"]) == ("job-1", 2)
    answer(bridge, "success", "none")
    gate = shown(bridge)
    assert (gate["job_id"], gate["waiting"]) == ("job-2", 1)
    assert (gate["result"], gate["symptom"]) == (UNSET, UNSET)


# --------------------------------------------------------------------------
# The whole thing, through the daemon
# --------------------------------------------------------------------------


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    (tmp_path / "work").mkdir()
    return Rig(tmp_path)


def watched(r: Rig, after_start: list[str]) -> FramePrinter:
    """A second, read-only view of the same scripted printer, for the watcher."""
    r.printer.after_start = after_start
    r.daemon._watch_printer = r.printer
    r.daemon._watch_poll_s = 0.01
    return r.printer


def test_approve_print_end_ask_answer(rig: Rig) -> None:
    watched(rig, ["printing", "printing", "completed"])
    with rig.daemon:
        job_id = rig.daemon.submit(rig.stl)
        rig.daemon.wait_for_gate(job_id, timeout_s=30)
        rig.press("approve")
        assert rig.daemon.wait_for_finish(job_id, timeout_s=30).printed

        wait_until(lambda: rig.bridge.retained_json(outcome_topic(CFG)).get("pending") is True)
        gate = rig.bridge.retained_json(outcome_topic(CFG))
        assert (gate["job_id"], gate["printer_end"]) == (job_id, "completed")
        row = rig.ledger.get(job_id)
        assert row is not None and row.result is None, "COMPLETED was not taken as success"

        answer(rig.bridge, "success", "none")
        wait_until(lambda: not rig.bridge.retained_json(outcome_topic(CFG))["pending"])

    row = rig.ledger.get(job_id)
    assert row is not None
    assert (row.result, row.symptom) == (Result.SUCCESS, Symptom.NONE)
    assert row.started_at is not None and row.ended_at is not None
    assert "start" in rig.printer.verbs
    assert rig.printer.verbs.count("start") == 1, "watching never starts anything"


def test_an_unanswered_print_is_asked_again_after_a_restart(rig: Rig, tmp_path: Path) -> None:
    watched(rig, ["printing", "completed"])
    with rig.daemon:
        job_id = rig.daemon.submit(rig.stl)
        rig.daemon.wait_for_gate(job_id, timeout_s=30)
        rig.press("approve")
        rig.daemon.wait_for_finish(job_id, timeout_s=30)
        wait_until(lambda: rig.bridge.retained_json(outcome_topic(CFG)).get("pending") is True)

    # A new daemon process: nothing in memory, the same ledger on disk.
    again = Rig(tmp_path)
    with again.daemon:
        gate = again.bridge.retained_json(outcome_topic(CFG))
        assert (gate["pending"], gate["job_id"]) == (True, job_id)
    row = again.ledger.get(job_id)
    assert row is not None and row.result is None, "still open, never guessed"


def test_without_a_watcher_a_print_stays_running_and_unasked(rig: Rig) -> None:
    with rig.daemon:
        job_id = rig.daemon.submit(rig.stl)
        rig.daemon.wait_for_gate(job_id, timeout_s=30)
        rig.press("approve")
        rig.daemon.wait_for_finish(job_id, timeout_s=30)
        assert rig.bridge.retained_json(outcome_topic(CFG))["pending"] is False
    assert [r.job_id for r in rig.ledger.running()] == [job_id]
