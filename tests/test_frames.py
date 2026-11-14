"""Each print keeps camera frames, a layer apart, as evidence beside its outcome.

mqucifer/print-shop#110. The watcher takes a frame each time the layer it reads has moved on,
and one at the end; a grab that fails is logged and skipped, never stopping the watcher. The
ledger keeps where each is, the outcome gate shows the last and pages through the rest, and a
lesson drawn from an outcome with a symptom names them.

mqucifer/print-shop#123: a frame is one GET of Home Assistant's camera entity, never a
connection to the printer's own stream; a grab that fails is one line saying why, and a print
with no frame says why at the outcome step. Every camera here is a fake, and Home Assistant is a
`MockTransport`: no frame is grabbed from the real printer or the real Home Assistant.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
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
from print_shop.printer.camera import (
    JPEG_START,
    CameraError,
    CameraOff,
    HomeAssistantCamera,
    camera_from_env,
)
from print_shop.setup import lessons
from tests.conftest import LiveServiceRefused
from tests.fakes import JPEG, FakeCamera, MemoryBridge, frame
from tests.live import opted_in
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


#: A token no test may print.
TOKEN = "tok-not-a-real-one"
ENTITY = "camera.test_printer"


class HomeAssistant:
    """Home Assistant's REST API as a `MockTransport`: each request kept, each answered by
    `answer`, which may raise as the network would."""

    def __init__(self, answer: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.answer = answer or (lambda request: httpx.Response(
            200, content=JPEG, headers={"content-type": "image/jpeg"}))

    def camera(self, timeout_s: float = 10.0) -> HomeAssistantCamera:
        return HomeAssistantCamera(url="http://ha.invalid:8123", entity_id=ENTITY, token=TOKEN,
                                   timeout_s=timeout_s, transport=httpx.MockTransport(self._on))

    def _on(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)


def test_a_frame_is_one_get_of_home_assistants_camera_entity() -> None:
    ha = HomeAssistant()
    assert ha.camera().grab() == JPEG
    [request] = ha.requests
    assert (request.method, str(request.url)) == (
        "GET", f"http://ha.invalid:8123/api/camera_proxy/{ENTITY}"
    )
    assert request.headers["authorization"] == f"Bearer {TOKEN}"


def _raises(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def answer(request: httpx.Request) -> httpx.Response:
        raise exc

    return answer


@pytest.mark.parametrize(("answer", "said"), [
    (lambda r: httpx.Response(401), "Home Assistant refused HA_TOKEN (401)"),
    (lambda r: httpx.Response(404), f"Home Assistant has no camera {ENTITY} (404)"),
    (lambda r: httpx.Response(500), f"Home Assistant answered 500 Internal Server Error for "
                                    f"{ENTITY}"),
    (lambda r: httpx.Response(200, content=b""), f"Home Assistant sent an empty image of "
                                                 f"{ENTITY}"),
    (lambda r: httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
     f"what Home Assistant sent of {ENTITY} isn't a JPEG (text/html)"),
    (_raises(httpx.ReadTimeout("timed out")), f"Home Assistant gave no image of {ENTITY} in 2 s"),
    (_raises(httpx.ConnectError("[Errno 61] Connection refused")),
     "Home Assistant couldn't be reached at http://ha.invalid:8123: ConnectError: [Errno 61] "
     "Connection refused"),
])
def test_an_entity_that_answers_with_an_error_or_nothing_says_why_in_one_line(
    answer: Callable[[httpx.Request], httpx.Response], said: str
) -> None:
    with pytest.raises(CameraError) as raised:
        HomeAssistant(answer).camera(timeout_s=2).grab()
    assert str(raised.value) == said
    assert "\n" not in said and TOKEN not in said
    assert raised.value.__cause__ is None, "the reason is the line; no chain to print"


def test_the_settings_name_the_camera_and_the_token_never_shows(tmp_path: Path) -> None:
    settings = tmp_path / ".env"
    settings.write_text(f"HA_URL=http://ha.invalid:8123\nHA_TOKEN={TOKEN}\n"
                        f"HA_CAMERA_ENTITY={ENTITY}\n")
    camera = camera_from_env(settings)
    assert isinstance(camera, HomeAssistantCamera)
    assert (camera.url, camera.entity_id, camera.token) == ("http://ha.invalid:8123", ENTITY, TOKEN)
    assert TOKEN not in repr(camera)


def test_home_assistants_address_defaults_to_the_brokers_host(tmp_path: Path) -> None:
    """As the live dashboard checks read it: Home Assistant beside the broker."""
    settings = tmp_path / ".env"
    settings.write_text(f"MQTT_HOST=pi.invalid\nHA_TOKEN={TOKEN}\nHA_CAMERA_ENTITY={ENTITY}\n")
    camera = camera_from_env(settings)
    assert isinstance(camera, HomeAssistantCamera) and camera.url == "http://pi.invalid:8123"


@pytest.mark.parametrize(("lines", "missing"), [
    ("", "HA_CAMERA_ENTITY and HA_TOKEN and HA_URL"),
    (f"HA_URL=http://ha.invalid\nHA_TOKEN={TOKEN}\n", "HA_CAMERA_ENTITY"),
    (f"HA_URL=http://ha.invalid\nHA_CAMERA_ENTITY={ENTITY}\n", "HA_TOKEN"),
])
def test_without_the_token_or_the_entity_frames_are_off_saying_which(
    tmp_path: Path, lines: str, missing: str
) -> None:
    settings = tmp_path / ".env"
    settings.write_text(lines)
    camera = camera_from_env(settings)
    assert camera == CameraOff(f"frames are off: {missing} not set in {settings}")
    with pytest.raises(CameraError, match="frames are off"):
        camera.grab()


@pytest.mark.skipif(opted_in("ha"), reason="opted in to the real Home Assistant")
def test_no_test_reaches_a_real_home_assistant_camera() -> None:
    """Under CI's filter, a camera's image without a MockTransport is refused before the network:
    the guard in `conftest.py`, not discipline."""
    camera = HomeAssistantCamera(url="http://127.0.0.1:9", entity_id=ENTITY, token=TOKEN)
    with pytest.raises(LiveServiceRefused, match="Home Assistant"):
        camera.grab()


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
    [line] = caplog.records
    assert line.getMessage() == (
        "no layer frame for job job-1 at layer 5; skipped: no frame from the printer: busy"
    ), "one line: why, and the layer"
    assert line.exc_info is None, "the traceback goes to debug, not the warning"
    assert ledger.frames_missed("job-1") == "no frame from the printer: busy"
    assert ledger.frames("job-1") == []
    assert not (tmp_path / "jobs" / "job-1" / "frames" / "layer-0005.jpg").exists()
    camera.fail = None
    assert keeper.at_layer("job-1", 5, 100) is None, "that layer's chance has passed"
    assert keeper.at_layer("job-1", 6, 100) is not None, "the next layer's is taken"


def test_a_silent_home_assistant_is_one_line_a_layer_and_its_traceback_is_debug(
    ledger: Ledger, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The first print after mqucifer/print-shop#121: a traceback each layer. Now one line."""
    started(ledger)
    ha = HomeAssistant(_raises(httpx.ReadTimeout("timed out")))
    keeper = FrameKeeper(camera=ha.camera(timeout_s=5), ledger=ledger, jobs=tmp_path / "jobs")
    with caplog.at_level(logging.DEBUG, logger="print_shop.orchestrator.frames"):
        for layer in (1, 2):
            keeper.at_layer("job-1", layer, 100)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == [
        f"no layer frame for job job-1 at layer {n}; skipped: Home Assistant gave no image of "
        f"{ENTITY} in 5 s" for n in (1, 2)
    ]
    assert all(r.exc_info is None for r in warnings)
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(debug) == 2 and all(r.exc_info is not None for r in debug)
    assert len(ha.requests) == 2, "one GET a frame, and nothing else"


def test_with_the_camera_off_a_print_says_so_once_and_grabs_nothing(
    ledger: Ledger, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started(ledger)
    off = CameraOff("frames are off: HA_CAMERA_ENTITY not set in /settings/.env")
    keeper = FrameKeeper(camera=off, ledger=ledger, jobs=tmp_path / "jobs")
    with caplog.at_level(logging.INFO):
        for layer in (1, 2, 3):
            assert keeper.at_layer("job-1", layer, 3) is None
        assert keeper.at_end("job-1", 3, 3) is None
    assert [r.getMessage() for r in caplog.records] == [
        "no camera frames for job job-1: frames are off: HA_CAMERA_ENTITY not set in "
        "/settings/.env"
    ]
    assert ledger.frames("job-1") == []
    assert not (tmp_path / "jobs" / "job-1").exists()
    assert ledger.frames_missed("job-1") == off.why


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


def test_a_prints_frames_are_one_get_each_to_home_assistant_and_none_to_the_printer(
    ledger: Ledger, tmp_path: Path
) -> None:
    """mqucifer/print-shop#123: the printer's camera slots are few and held badly, so print-shop
    opens none of them. Home Assistant's integration holds the one connection."""
    name = started(ledger)
    printer = Printer("printing", name, layer=1)
    ha = HomeAssistant()
    keeper = FrameKeeper(camera=ha.camera(), ledger=ledger, jobs=tmp_path / "jobs")
    watcher = Watcher(ledger=ledger, printer=printer, on_ended=lambda: None, frames=keeper)
    for layer in (1, 2, 2, 5):
        printer.layer = layer
        watcher.look()
    printer.name = "completed"
    watcher.look()
    assert [(f.kind, f.layer) for f in ledger.frames("job-1")] == [
        ("layer", 1), ("layer", 2), ("layer", 5), ("end", 5)
    ]
    assert len(ha.requests) == 4
    assert {(r.url.host, r.url.port, r.url.path) for r in ha.requests} == {
        ("ha.invalid", 8123, f"/api/camera_proxy/{ENTITY}")
    }


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


def test_a_print_with_no_frame_says_why_at_the_outcome_step(
    ledger: Ledger, tmp_path: Path
) -> None:
    bridge = MemoryBridge()
    desk = OutcomeDesk(ledger=ledger, cfg=CFG, publish=lambda t, p: bridge.publish(t, p, retain=True),
                       publish_image=lambda t, b: bridge.publish(t, b, retain=True))
    started(ledger)
    keeper = FrameKeeper(camera=HomeAssistant(lambda r: httpx.Response(404)).camera(),
                         ledger=ledger, jobs=tmp_path / "jobs")
    keeper.at_layer("job-1", 1, 3)
    keeper.at_end("job-1", 3, 3)
    ledger.record_ended("job-1", 9)
    desk.refresh()
    gate = shown(bridge)
    assert (gate["frames"], gate["frame"]) == (0, 0)
    assert gate["frames_why"] == (
        f"No camera frame was kept for this print: Home Assistant has no camera {ENTITY} (404)."
    )


def test_a_print_nothing_asked_the_camera_about_says_that(ledger: Ledger) -> None:
    bridge = MemoryBridge()
    desk = OutcomeDesk(ledger=ledger, cfg=CFG, publish=lambda t, p: bridge.publish(t, p, retain=True))
    started(ledger)
    ledger.record_ended("job-1", 9)
    desk.refresh()
    assert shown(bridge)["frames_why"] == (
        "No camera frame was kept for this print: nothing was asked of the camera."
    )


def test_a_print_with_frames_has_no_why(desk: Any) -> None:
    _, bridge, _ = desk
    assert shown(bridge)["frames_why"] == ""


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
