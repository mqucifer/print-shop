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

import pytest
from pydantic import SecretStr

from print_shop.core.mqtt import MqttConfig, command_topic, gate_topic, state_topic
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
        self.daemon = Daemon(
            cfg=CFG,
            profiles=CI_PROFILES,
            workdir=tmp_path / "work",
            printer=self.printer,
            slicer=self.slicer,
            bridge=lambda cfg: self.bridge,
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

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert (result.approved, result.printed, result.error) == (True, True, None)
    assert rig.printer.verbs == ["snapshot", "upload", "start"]
    started = rig.printer.calls[-1][1]
    assert job_id_from_filename(started) == job_id
    assert rig.bridge.retained_json(gate_topic(CFG)) == {"pending": False}
    assert rig.bridge.retained_json(state_topic(CFG))["status"] == "done"
    assert rig.slicer.calls == [rig.stl], "the cube sits flat, so it is sliced unrotated"


def test_a_rejected_job_never_touches_the_printer(rig: Rig) -> None:
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    rig.press("reject")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert (result.approved, result.printed) == (False, False)
    assert rig.printer.calls == []


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


def test_orientation_says_no_model_was_asked(rig: Rig) -> None:
    job_id = rig.daemon.submit(rig.stl)
    rig.daemon.wait_for_gate(job_id, timeout_s=30)
    gate = rig.bridge.retained_json(gate_topic(CFG))
    assert gate["orientation_rationale"].startswith("No model was configured")
    rig.press("reject")
    rig.daemon.wait_for_finish(job_id, timeout_s=30)
