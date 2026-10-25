"""Doubles that let the whole job run in CI: no Orca, no broker, no printer.

Each one stands in for a transport and decides nothing. The code under test
(the graph, the daemon's gates, `preflight`, the G-code parser, the snapshot
conversion) is the real code.

- `RecordedSlicer` replays a G-code the real Orca wrote, through the real
  header parser, and refuses any STL it has no recording for.
- `MemoryBridge` is a broker in a list: what was published, what is retained,
  and a `deliver` that calls subscribers as a broker would.
- `FramePrinter` answers from SDCP payloads (`tests/data/sdcp/frames.json`,
  synthetic until the first real print records some) through the real
  `snapshot_from`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt
from pycentauri.models import Attributes, Status

from print_shop.core.mqtt import Bridge, MessageHandler
from print_shop.printer.centauri import PrinterSnapshot, snapshot_from
from print_shop.slicer.orca import SliceResult, SlicerProfiles, parse_gcode_header

DATA = Path(__file__).parent / "data"

#: The cube from `tests/fixtures.py`, as the real Orca sliced it (data/README.md).
CUBE_SHA256 = "bd3eda612de075a1e177e7dbe8e97941c8393df936ccab0087cf24b910fa027a"

CI_PROFILES = SlicerProfiles(
    machine=DATA / "profiles" / "machine.json",
    process=DATA / "profiles" / "process.json",
)


def installed_or_ci(real: SlicerProfiles) -> SlicerProfiles:
    """The Mac's Orca profiles where they are installed, the CI ones elsewhere.

    For tests that build a graph but never slice: the graph reads only the
    build volume from a profile, which both give as 257 mm. Tests that do
    slice skip without Orca (`needs_orca`), whichever profiles they get.
    """
    if real.machine.is_file() and real.process.is_file():
        return real
    return CI_PROFILES


# --------------------------------------------------------------------------
# Slicing
# --------------------------------------------------------------------------


@dataclass
class RecordedSlicer:
    """A `Slicer` that replays recordings keyed by the STL's sha256."""

    recordings: dict[str, Path] = field(
        default_factory=lambda: {CUBE_SHA256: DATA / "recorded" / "cube.gcode"}
    )
    calls: list[Path] = field(default_factory=list)

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        stl = Path(stl_path)
        self.calls.append(stl)
        digest = hashlib.sha256(stl.read_bytes()).hexdigest()
        recording = self.recordings.get(digest)
        if recording is None:
            raise RuntimeError(
                f"no recorded slice for {stl.name} (sha256 {digest[:12]}); "
                f"slice it once with the real Orca and add it to tests/data/recorded/"
            )
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        gcode = out / "plate_1.gcode"
        shutil.copyfile(recording, gcode)
        return parse_gcode_header(gcode)


# --------------------------------------------------------------------------
# The broker
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Published:
    topic: str
    payload: str
    retain: bool

    def json(self) -> Any:
        return json.loads(self.payload)


class MemoryBridge:
    """A `Bridge` with no network. Thread-safe: the daemon publishes from a worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.published: list[Published] = []
        self.retained: dict[str, str] = {}
        self._subscriptions: list[tuple[str, MessageHandler]] = []
        self.open = False

    # --- the Bridge protocol ---------------------------------------------

    def __enter__(self) -> Bridge:
        self.open = True
        return self

    def __exit__(self, *exc: object) -> None:
        self.open = False

    def announce(self) -> None:
        self.publish("discovery/announced", "", retain=True)

    def publish_availability(self, online: bool) -> None:
        self.publish("availability", "online" if online else "offline", retain=True)

    def publish(
        self, topic: str, payload: dict[str, object] | str, retain: bool = False, qos: int = 1
    ) -> None:
        text = json.dumps(payload) if isinstance(payload, dict) else payload
        with self._lock:
            self.published.append(Published(topic, text, retain))
            if retain:
                self.retained[topic] = text

    def subscribe(self, topic: str, handler: MessageHandler) -> None:
        with self._lock:
            self._subscriptions.append((topic, handler))

    # --- what a test does ------------------------------------------------

    def deliver(self, topic: str, payload: dict[str, Any] | str, *, retain: bool = False) -> None:
        """Hand a message to every matching subscriber, as the broker would."""
        raw = (json.dumps(payload) if isinstance(payload, dict) else payload).encode()
        with self._lock:
            handlers = [h for f, h in self._subscriptions if mqtt.topic_matches_sub(f, topic)]
        for handler in handlers:
            handler(topic, raw, retain)

    def on(self, topic: str) -> list[Any]:
        """Every JSON payload published to `topic`, in order."""
        with self._lock:
            return [p.json() for p in self.published if p.topic == topic and p.payload]

    def retained_json(self, topic: str) -> Any:
        with self._lock:
            return json.loads(self.retained[topic])


# --------------------------------------------------------------------------
# The printer
# --------------------------------------------------------------------------


def _frames() -> dict[str, Any]:
    frames: dict[str, Any] = json.loads((DATA / "sdcp" / "frames.json").read_text())
    return frames


def frame(name: str, filename: str = "", *, free_bytes: int | None = None) -> PrinterSnapshot:
    """One SDCP status frame (and the attributes) as the real client would see it."""
    frames = _frames()
    payload = json.loads(json.dumps(frames["status"][name]).replace("{filename}", filename))
    attributes = dict(frames["attributes"])
    if free_bytes is not None:
        attributes["RemainingMemory"] = free_bytes
    return snapshot_from(Status.from_payload(payload), Attributes.from_payload(attributes))


@dataclass
class FramePrinter:
    """A `PrinterClient` that answers from frames.

    Before a start it reports `before` (idle by default). `start` reports the
    first frame of `after_start`; each later `snapshot` takes the next one, and
    the last repeats. So `after_start=["printing", "printing", "completed"]`
    is a print that runs for two looks and then finishes.
    """

    before: str = "idle"
    after_start: list[str] = field(default_factory=lambda: ["printing"])
    stored_as: str | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    _started: str | None = None
    _cursor: int = 0

    def snapshot(self) -> PrinterSnapshot:
        self.calls.append(("snapshot", ""))
        if self._started is None:
            return frame(self.before)
        index = min(self._cursor, len(self.after_start) - 1)
        self._cursor += 1
        return frame(self.after_start[index], self._started)

    def upload(self, local_path: Path) -> str:
        self.calls.append(("upload", str(local_path)))
        return self.stored_as if self.stored_as is not None else Path(local_path).name

    def start(self, remote_name: str) -> PrinterSnapshot:
        self.calls.append(("start", remote_name))
        self._started = remote_name
        self._cursor = 1
        return frame(self.after_start[0], remote_name)

    @property
    def verbs(self) -> list[str]:
        return [verb for verb, _ in self.calls]
