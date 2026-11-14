"""Each print keeps camera frames, a layer apart, as evidence beside its outcome.

mqucifer/print-shop#110. The watcher takes a frame each time the layer it reads has moved on,
and one at the end; a grab that fails is logged and skipped, never stopping the watcher. The
ledger keeps where each is, the outcome gate shows the last and pages through the rest, and a
lesson drawn from an outcome with a symptom names them. Every camera here is a fake: no frame
is grabbed from the real printer.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import (
    discovery_payloads,
    outcome_frame_set_topic,
    outcome_frame_topic,
    outcome_topic,
)
from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.orchestrator.frames import FrameKeeper
from print_shop.orchestrator.outcome import OutcomeDesk, Watcher
from print_shop.printer.camera import JPEG_START, CameraError, CentauriCamera
from print_shop.setup import lessons
from tests.fakes import JPEG, FakeCamera, MemoryBridge, frame
from tests.test_job_e2e import CFG, Rig
from tests.test_ledger import record
from tests.test_outcome import answer, shown, wait_until


def started(ledger: Ledger, job_id: str = "job-1") -> str:
    ledger.record_start_requested(record(job_id))
    ledger.record_started(job_id)
    return f"cube_{job_id}.gcode"


class Printer:
    """A read-only printer showing the frame and layer the test sets."""

    def __init__(self, name: str, filename: str, layer: int | None = None) -> None:
        self.name, self.filename, self.layer = name, filename, layer

    def snapshot(self) -> Any:
        return frame(self.name, self.filename, layer=self.layer)

    def upload(self, local_path: Path) -> str:
        raise AssertionError("a watcher never uploads")

    def start(self, remote_name: str) -> Any:
        raise AssertionError("a watcher never starts")


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "ledger.sqlite") as opened:
        yield opened


# The printer's status and its camera
# --------------------------------------------------------------------------


def test_the_status_says_which_layer_it_is_on() -> None:
    snapshot = frame("printing", "x.gcode", layer=12)
    assert (snapshot.layer, snapshot.total_layers) == (12, 100)
    assert frame("idle").layer == 0, "as it says it: idle is layer 0, which takes no frame"


def test_the_real_camera_says_why_it_has_no_frame() -> None:
    """Nothing listens on this address: the grab fails fast, said, and raises nothing else."""
    with pytest.raises(CameraError, match="no frame from 127.0.0.1"):
        CentauriCamera("127.0.0.1", timeout_s=0.5).grab()


# The keeper
# --------------------------------------------------------------------------


def test_a_frame_is_kept_once_for_each_layer_and_once_at_the_end(
    ledger: Ledger, tmp_path: Path
) -> None:
    started(ledger)
    camera = FakeCamera()
    keeper = FrameKeeper(camera=camera, ledger=ledger, jobs=tmp_path / "jobs")
    assert keeper.at_layer("job-1", 0, 100) is None, "layer 0 is no layer printed yet"
    first = keeper.at_layer("job-1", 1, 100)
    assert first == tmp_path / "jobs" / "job-1" / "frames" / "layer-0001.jpg"
    assert first.read_bytes().startswith(JPEG_START)
    assert keeper.at_layer("job-1", 1, 100) is None, "the same layer, not again"
    keeper.at_layer("job-1", 2, 100)
    keeper.at_end("job-1", 100, 100)
    assert camera.grabs == 3
    assert [(f.kind, f.layer, Path(f.path).name) for f in ledger.frames("job-1")] == [
        ("layer", 1, "layer-0001.jpg"), ("layer", 2, "layer-0002.jpg"), ("end", 100, "end.jpg")
    ]
    assert ledger.frames("job-1")[-1].line() == "the end, layer 100 of 100"


def test_a_grab_that_fails_is_logged_and_skipped(
    ledger: Ledger, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started(ledger)
    camera = FakeCamera(fail=CameraError("no frame from the printer: busy"))
    keeper = FrameKeeper(camera=camera, ledger=ledger, jobs=tmp_path / "jobs")
    with caplog.at_level(logging.WARNING):
        assert keeper.at_layer("job-1", 5, 100) is None
    assert "no layer frame for job job-1 at layer 5; skipped" in caplog.text
    assert ledger.frames("job-1") == []
    assert not (tmp_path / "jobs" / "job-1" / "frames" / "layer-0005.jpg").exists()
    camera.fail = None
    assert keeper.at_layer("job-1", 5, 100) is None, "that layer's chance has passed"
    assert keeper.at_layer("job-1", 6, 100) is not None, "the next layer's is taken"


# The watcher
# --------------------------------------------------------------------------


def test_the_watcher_takes_a_frame_as_each_layer_moves_on_and_at_the_end(
    ledger: Ledger, tmp_path: Path
) -> None:
    name = started(ledger)
    printer = Printer("printing", name, layer=1)
    camera = FakeCamera()
    keeper = FrameKeeper(camera=camera, ledger=ledger, jobs=tmp_path / "jobs")
    watcher = Watcher(ledger=ledger, printer=printer, on_ended=lambda: None, frames=keeper)
    watcher.look()
    watcher.look()
    printer.layer = 7  # layers come faster than the watcher looks: it takes what it sees
    watcher.look()
    printer.name = "completed"
    watcher.look()
    assert [(f.kind, f.layer) for f in ledger.frames("job-1")] == [
        ("layer", 1), ("layer", 7), ("end", 7)
    ]
    row = ledger.get("job-1")
    assert row is not None and row.ended_at is not None and row.result is None, (
        "a frame is never the outcome"
    )


def test_a_camera_that_fails_never_stops_the_watcher(ledger: Ledger, tmp_path: Path) -> None:
    name = started(ledger)
    printer = Printer("printing", name, layer=3)
    keeper = FrameKeeper(camera=FakeCamera(fail=CameraError("off")), ledger=ledger,
                         jobs=tmp_path / "jobs")
    ended: list[int] = []
    watcher = Watcher(ledger=ledger, printer=printer, on_ended=lambda: ended.append(1),
                      frames=keeper)
    watcher.look()
    printer.name = "completed"
    watcher.look()
    row = ledger.get("job-1")
    assert row is not None and row.ended_at is not None
    assert ended == [1]
    assert ledger.frames("job-1") == []


def test_a_frame_is_never_taken_of_someone_elses_print(ledger: Ledger, tmp_path: Path) -> None:
    started(ledger)
    camera = FakeCamera()
    keeper = FrameKeeper(camera=camera, ledger=ledger, jobs=tmp_path / "jobs")
    Watcher(ledger=ledger, printer=Printer("printing", "theirs.gcode", layer=3),
            on_ended=lambda: None, frames=keeper).look()
    assert camera.grabs == 0


# The outcome gate
# --------------------------------------------------------------------------


@pytest.fixture
def desk(ledger: Ledger, tmp_path: Path) -> Iterator[tuple[Ledger, MemoryBridge, FrameKeeper]]:
    bridge = MemoryBridge()
    desk = OutcomeDesk(
        ledger=ledger, cfg=CFG, publish=lambda t, p: bridge.publish(t, p, retain=True),
        publish_image=lambda t, b: bridge.publish(t, b, retain=True),
    )
    for topic, handler in desk.topics.items():
        bridge.subscribe(topic, handler)
    keeper = FrameKeeper(camera=FakeCamera(), ledger=ledger, jobs=tmp_path / "jobs")
    started(ledger)
    for layer in (1, 2, 3):
        keeper.at_layer("job-1", layer, 3)
    keeper.at_end("job-1", 3, 3)
    ledger.record_ended("job-1", 9)
    desk.refresh()
    yield ledger, bridge, keeper


def test_the_gate_shows_the_last_frame_beside_the_answer(desk: Any) -> None:
    _, bridge, _ = desk
    gate = shown(bridge)
    assert (gate["frames"], gate["frame"], gate["frame_at"]) == (4, 4, "the end, layer 3 of 3")
    assert bridge.images[outcome_frame_topic(CFG)] == JPEG[:2] + b"4" + JPEG[-2:]


def test_earlier_and_later_page_through_the_frames(desk: Any) -> None:
    _, bridge, _ = desk
    for _ in range(5):
        bridge.deliver(outcome_frame_set_topic(CFG), "earlier")
    assert (shown(bridge)["frame"], shown(bridge)["frame_at"]) == (1, "layer 1 of 3")
    assert bridge.images[outcome_frame_topic(CFG)] == JPEG[:2] + b"1" + JPEG[-2:]
    bridge.deliver(outcome_frame_set_topic(CFG), "later")
    assert shown(bridge)["frame"] == 2
    bridge.deliver(outcome_frame_set_topic(CFG), "later", retain=True)
    assert shown(bridge)["frame"] == 2, "a replayed press is no one's press now"
    bridge.deliver(outcome_frame_set_topic(CFG), "last")
    assert shown(bridge)["frame"] == 4


def test_a_frame_on_show_is_cleared_once_the_answer_is_in(desk: Any) -> None:
    ledger, bridge, _ = desk
    answer(bridge, "success", "none")
    assert shown(bridge)["pending"] is False
    assert bridge.images[outcome_frame_topic(CFG)] == b"", "an empty retained frame clears it"
    row = ledger.get("job-1")
    assert row is not None and row.result is Result.SUCCESS


def test_home_assistant_gets_an_image_and_two_buttons() -> None:
    found = {topic.split("/")[-2]: config for topic, config in discovery_payloads(CFG)}
    assert found["outcome_frame"]["image_topic"] == outcome_frame_topic(CFG)
    assert found["outcome_frame"]["content_type"] == "image/jpeg"
    assert found["outcome_frame_earlier"]["payload_press"] == "earlier"
    assert found["outcome_frame_later"]["command_topic"] == outcome_frame_set_topic(CFG)


# A lesson's evidence
# --------------------------------------------------------------------------


def test_an_outcome_with_a_symptom_names_its_frames_in_the_lesson(desk: Any) -> None:
    ledger, _, _ = desk
    ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.WARPING,
                                         note="the corners lifted"))
    [evidence] = ledger.query("SELECT detail FROM print_lesson_evidence WHERE kind='outcome'")
    frames = json.loads(evidence["detail"])["frames"]
    assert [(f["kind"], f["layer"]) for f in frames] == [
        ("layer", 1), ("layer", 2), ("layer", 3), ("end", 3)
    ]
    assert "(4 frames, layers 1 to 3, in " in lessons.listing(ledger)


def test_a_success_with_a_note_doesnt_carry_the_frames(desk: Any) -> None:
    ledger, _, _ = desk
    ledger.record_answer("job-1", Answer(result=Result.SUCCESS, symptom=Symptom.NONE,
                                         note="came out well"))
    [evidence] = ledger.query("SELECT detail FROM print_lesson_evidence WHERE kind='outcome'")
    assert "frames" not in json.loads(evidence["detail"])


# A whole print, through the daemon
# --------------------------------------------------------------------------


def test_a_print_through_the_daemon_keeps_its_frames_and_the_gate_shows_them(
    tmp_path: Path,
) -> None:
    (tmp_path / "work").mkdir()
    rig = Rig(tmp_path)
    camera = FakeCamera()
    rig.printer.after_start = ["printing", "printing", "completed"]
    rig.daemon._watch_printer = rig.printer
    rig.daemon._watch_poll_s = 0.01
    rig.daemon._camera = camera
    with rig.daemon:
        job_id = rig.daemon.submit(rig.stl)
        rig.daemon.wait_for_gate(job_id, timeout_s=30)
        rig.press("approve")
        assert rig.daemon.wait_for_finish(job_id, timeout_s=30).printed
        wait_until(lambda: rig.bridge.retained_json(outcome_topic(CFG)).get("pending") is True)
        gate = rig.bridge.retained_json(outcome_topic(CFG))
        assert gate["frames"] == 2 and gate["frame_at"].startswith("the end")
        assert rig.bridge.images[outcome_frame_topic(CFG)].startswith(JPEG_START)
    frames = rig.ledger.frames(job_id)
    assert [(f.kind, f.layer) for f in frames] == [("layer", 40), ("end", 100)]
    assert all(Path(f.path).parent == tmp_path / "work" / job_id / "frames" for f in frames)
    assert rig.printer.verbs.count("start") == 1, "frames never start anything"
