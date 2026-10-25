"""The doubles are honest: real parser, real conversion, no recording no slice."""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.printer.centauri import READY_TO_START, preflight
from tests.fakes import CI_PROFILES, FramePrinter, RecordedSlicer, frame
from tests.fixtures import write_stl


def test_the_recorded_slice_parses_with_the_real_parser(tmp_path: Path) -> None:
    result = RecordedSlicer()(write_stl("cube", tmp_path), CI_PROFILES, tmp_path / "out")
    assert (result.layer_count, result.max_z_mm, result.print_time_s) == (100, 20.0, 1009)
    assert (result.nozzle_mm, result.material, result.layer_height_mm) == (0.4, "PLA", 0.2)


def test_an_stl_with_no_recording_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="no recorded slice"):
        RecordedSlicer()(write_stl("tee", tmp_path), CI_PROFILES, tmp_path / "out")


@pytest.mark.parametrize(
    ("name", "ready"),
    [
        ("idle", True),
        ("completed", True),
        ("stopped", True),
        ("preheating", False),
        ("printing", False),
        ("paused", False),
        ("error", False),
    ],
)
def test_each_frame_reads_as_the_real_client_would(name: str, ready: bool) -> None:
    snap = frame(name, "job.gcode")
    assert snap.ready_to_start is ready
    assert snap.free_bytes == 5098209280
    assert snap.firmware == "V1.1.46"
    if ready:
        assert snap.print_status in READY_TO_START


def test_a_printing_frame_holds_its_file() -> None:
    assert frame("printing", "job.gcode").active_filename == "job.gcode"
    assert frame("error", "job.gcode").active_filename is None


def test_the_frame_printer_runs_its_script(tmp_path: Path) -> None:
    printer = FramePrinter(after_start=["preheating", "printing", "completed"])
    assert printer.snapshot().print_status == 0
    assert printer.start("job.gcode").print_status == 16
    assert [printer.snapshot().print_status for _ in range(3)] == [13, 9, 9]


def test_preflight_judges_a_frame(tmp_path: Path) -> None:
    gcode = tmp_path / "x.gcode"
    gcode.write_text("G28\n")
    assert preflight(frame("idle"), gcode).accepted
    assert not preflight(frame("printing", "other.gcode"), gcode).accepted
