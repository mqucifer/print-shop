"""The whole job, end to end, with no Orca, broker, printer or model.

STL → ingest → orient → slice → the approval gate in "Home Assistant" →
approve → upload → start. Everything is the real code except the transports
(`tests/fakes.py`): the slice is one the real Orca wrote, the broker is a list,
and the printer answers from SDCP frames. This is what CI proves on every
pull request (mqucifer/print-shop#1's "runs end to end against fakes").
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.core.mqtt import MqttConfig, command_topic, gate_topic, state_topic
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.slicer.naming import job_id_from_filename
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl

CFG = MqttConfig(
    host="broker.invalid",
    port=1883,
    user="",
    password=SecretStr(""),
    topic_prefix="3dprint-ci",
    discovery_prefix="homeassistant",
    node_id="3dprint_ci",
)


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.slicer = RecordedSlicer()
        self.stl = write_stl("cube", tmp_path)
        self.ledger = Ledger(tmp_path / "ledger.sqlite")
        self.daemon = Daemon(
            cfg=CFG,
            profiles=CI_PROFILES,
            workdir=tmp_path / "work",
            printer=self.printer,
            slicer=self.slicer,
            bridge=lambda cfg: self.bridge,
            ledger=self.ledger,
        )

    def press(self, decision: str, *, retain: bool = False) -> None:
        self.bridge.deliver(command_topic(CFG), {"decision": decision}, retain=retain)


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[Rig]:
    (tmp_path / "work").mkdir()
    r = Rig(tmp_path)
    with r.daemon:
        yield r


def test_an_approved_job_is_sliced_gated_uploaded_and_started(rig: Rig) -> None:
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)

    gate = rig.bridge.retained_json(gate_topic(CFG))
    assert gate["pending"] is True
    assert gate["job_id"] == job_id
    assert gate["print_time_s"] == 1009, "the recorded slice's own estimate"
    assert rig.printer.calls == [], "nothing touches the printer before the gate"
    shown = rig.daemon.pending_gate()
    assert shown is not None and shown["job_id"] == job_id
    assert shown["orientation_rationale"] == gate["orientation_rationale"]

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert (result.approved, result.printed, result.error) == (True, True, None)
    assert rig.printer.verbs == ["snapshot", "upload", "start"]
    started = rig.printer.calls[-1][1]
    assert job_id_from_filename(started) == job_id
    assert rig.bridge.retained_json(gate_topic(CFG)) == {"pending": False}
    assert rig.daemon.pending_gate() is None
    assert rig.bridge.retained_json(state_topic(CFG))["status"] == "done"
    assert rig.slicer.calls == [rig.stl], "the cube sits flat, so it is sliced unrotated"

    (row,) = rig.ledger.rows()
    assert row.job_id == job_id
    assert row.gcode_name == started
    assert row.started_at is not None
    assert row.start_requested_at <= row.started_at
    assert row.orientation_source == "geometric"
    assert row.filament == "Elegoo PLA @ECC"
    assert row.printer_machine == "Centauri Carbon"
    assert row.result is None, "nobody has said how it came out"


def test_a_rejected_job_never_touches_the_printer(rig: Rig) -> None:
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    rig.press("reject")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert (result.approved, result.printed) == (False, False)
    assert rig.printer.calls == []
    assert rig.ledger.rows() == [], "the ledger logs only prints the agent starts"


def test_a_retained_approval_is_not_an_answer(rig: Rig) -> None:
    """A broker replays retained messages; a stale press must not print."""
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    rig.press("approve", retain=True)
    rig.press("reject")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False
    assert rig.printer.calls == []


def test_a_busy_printer_fails_the_job_at_preflight(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    r = Rig(tmp_path)
    r.printer.before = "printing"
    with r.daemon:
        job_id = r.daemon.submit(r.stl)
        r.daemon.wait_for_gate(job_id, timeout_s=30)
        r.press("approve")
        result = r.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False
    assert result.error is not None and "printer.ready" in result.error
    assert r.printer.verbs == ["snapshot"], "refused before any upload"
    assert r.ledger.rows() == []


def test_orientation_says_no_model_was_asked(rig: Rig) -> None:
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    gate = rig.bridge.retained_json(gate_topic(CFG))
    assert gate["orientation_rationale"].startswith("No model was configured")
    rig.press("reject")
    rig.daemon.wait_for_finish(job_id, timeout_s=30)


def test_a_start_that_fails_is_still_on_record(tmp_path: Path) -> None:
    """The row is written before start(), so a start that raises is not lost."""
    (tmp_path / "work").mkdir()
    r = Rig(tmp_path)

    def refuse(remote_name: str) -> Any:
        r.printer.calls.append(("start", remote_name))
        raise RuntimeError("printer refused to start: Ack=1")

    r.printer.start = refuse  # type: ignore[method-assign]
    with r.daemon:
        job_id = r.daemon.submit(r.stl)
        r.daemon.wait_for_gate(job_id, timeout_s=30)
        r.press("approve")
        result = r.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False
    (row,) = r.ledger.rows()
    assert row.job_id == job_id
    assert row.started_at is None
    assert row.start_error == "RuntimeError: printer refused to start: Ack=1"


def test_without_a_ledger_nothing_is_started(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    bridge, printer = MemoryBridge(), FramePrinter()
    daemon = Daemon(
        cfg=CFG,
        profiles=CI_PROFILES,
        workdir=tmp_path / "work",
        printer=printer,
        slicer=RecordedSlicer(),
        bridge=lambda cfg: bridge,
    )
    with daemon:
        job_id = daemon.submit(write_stl("cube", tmp_path))
        daemon.wait_for_gate(job_id, timeout_s=30)
        bridge.deliver(command_topic(CFG), {"decision": "approve"})
        result = daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False
    assert result.error is not None and "without a ledger" in result.error
    assert printer.calls == []
