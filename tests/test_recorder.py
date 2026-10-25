"""The first print's recorder keeps the printer's replies, minus who and where it is."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pycentauri.models import Attributes, Status

from print_shop.printer.centauri import CentauriClient, FrameRecorder, PrinterConfig
from tests.fakes import DATA

FRAMES = json.loads((DATA / "sdcp" / "frames.json").read_text())


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_what_identifies_or_locates_the_machine_is_redacted(tmp_path: Path) -> None:
    rec = FrameRecorder(tmp_path / "rec" / "frames.jsonl")
    rec.record(
        "attributes",
        {
            "MainboardID": "abc",
            "MainboardIP": "addr",
            "MainboardMAC": "aa:bb",
            "FirmwareVersion": "V1.1.46",
            "Nested": [{"SerialNumber": "s1", "Name": "Centauri Carbon"}],
        },
    )
    (line,) = lines(tmp_path / "rec" / "frames.jsonl")
    payload = line["payload"]
    assert payload["MainboardID"] == payload["MainboardIP"] == payload["MainboardMAC"] == "redacted"
    assert payload["Nested"][0] == {"SerialNumber": "redacted", "Name": "Centauri Carbon"}
    assert payload["FirmwareVersion"] == "V1.1.46"
    assert line["kind"] == "attributes" and line["at"]


def test_the_status_reply_is_kept_whole(tmp_path: Path) -> None:
    rec = FrameRecorder(tmp_path / "frames.jsonl")
    rec.record("status", FRAMES["status"]["printing"])
    (line,) = lines(tmp_path / "frames.jsonl")
    assert line["payload"] == FRAMES["status"]["printing"]


def test_a_snapshot_records_both_replies(tmp_path: Path) -> None:
    """The client's own path, with a stub transport; no printer is contacted."""

    class Stub:
        async def attributes(self) -> Attributes:
            return Attributes.from_payload(FRAMES["attributes"])

    rec = FrameRecorder(tmp_path / "frames.jsonl")
    client = CentauriClient(
        PrinterConfig(host="printer.invalid", mainboard_id="x"), recorder=rec
    )
    status = Status.from_payload(FRAMES["status"]["idle"])
    snap = asyncio.run(client._snapshot_from(Stub(), status))  # type: ignore[arg-type]

    assert snap.print_status == 0
    kinds = [line["kind"] for line in lines(tmp_path / "frames.jsonl")]
    assert kinds == ["status", "attributes"]
    assert lines(tmp_path / "frames.jsonl")[1]["payload"]["MainboardID"] == "redacted"


def test_a_recorder_is_off_unless_given(tmp_path: Path) -> None:
    client = CentauriClient(PrinterConfig(host="printer.invalid", mainboard_id="x"))
    assert client.recorder is None
    assert client.enable_control is False
