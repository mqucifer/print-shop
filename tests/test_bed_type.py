"""The printer's plate is textured PEI, so the bed is heated for it: 45 C, not 35.

The Sponsor's answer, 2026-10-25, written on mqucifer/print-shop#3. The Elegoo
process profile defaults to Orca's Cool Plate, which slices Elegoo PLA with
the bed at 35 C (`tests/data/recorded/cube.gcode`). `with_bed_type` sets the
plate, and the G-code then heats the bed to 45 C
(`tests/data/recorded/cube_pei.gcode`, sliced with it by the real Orca).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from print_shop.orchestrator.fitness import scoring_profiles
from print_shop.runtime import bed_type, describe
from print_shop.slicer.orca import (
    BedType,
    SlicerProfiles,
    parse_config_block,
    slice_model,
    with_bed_type,
)
from tests.fakes import CI_PROFILES, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_graph import DATADIR, FILAMENT, MACHINE, PROCESS, needs_orca
from tests.test_job_e2e import Rig

#: The commands that heat the bed: set (M140) and set-and-wait (M190).
BED = re.compile(r"^M1[49]0 S(\d+)", re.MULTILINE)


def bed_temps(gcode: Path) -> set[int]:
    """Every non-zero bed temperature the G-code commands."""
    return {int(t) for t in BED.findall(gcode.read_text())} - {0}


def test_the_override_sets_the_plate_and_keeps_the_rest(tmp_path: Path) -> None:
    derived = with_bed_type(CI_PROFILES, BedType.TEXTURED_PEI, tmp_path)
    base = json.loads(CI_PROFILES.process.read_text())
    data = json.loads(derived.process.read_text())
    assert data == {**base, "curr_bed_type": "Textured PEI Plate"}
    assert derived.machine == CI_PROFILES.machine


def test_scoring_keeps_the_plate(tmp_path: Path) -> None:
    """Supports are forced on for scoring; the plate must survive the overlay."""
    pei = with_bed_type(CI_PROFILES, BedType.TEXTURED_PEI, tmp_path / "p")
    scored = json.loads(scoring_profiles(pei, tmp_path / "s").process.read_text())
    assert scored["curr_bed_type"] == "Textured PEI Plate"
    assert scored["enable_support"] == "1"


def test_the_replayed_pei_slice_heats_the_bed_to_45(tmp_path: Path) -> None:
    pei = with_bed_type(CI_PROFILES, BedType.TEXTURED_PEI, tmp_path / "p")
    result = RecordedSlicer()(write_stl("cube", tmp_path), pei, tmp_path / "out")
    assert bed_temps(result.gcode_path) == {45}
    config = parse_config_block(result.gcode_path)
    assert config["curr_bed_type"] == "Textured PEI Plate"
    assert config["first_layer_bed_temperature"] == "45"


def test_the_cool_plate_slice_it_replaces_heated_to_35(tmp_path: Path) -> None:
    """The baseline, so the test above is a difference and not a coincidence."""
    result = RecordedSlicer()(write_stl("cube", tmp_path), CI_PROFILES, tmp_path / "out")
    assert bed_temps(result.gcode_path) == {35}


def test_the_plate_defaults_to_textured_pei() -> None:
    assert bed_type({}) is BedType.TEXTURED_PEI
    assert bed_type({"ORCA_BED_TYPE": "Cool Plate"}) is BedType.COOL


def test_a_plate_nobody_has_read_back_is_refused() -> None:
    with pytest.raises(ValueError, match="ORCA_BED_TYPE"):
        bed_type({"ORCA_BED_TYPE": "Textured PEI"})


def test_a_pei_job_is_on_record_with_its_plate_and_bed(tmp_path: Path) -> None:
    """Through the daemon: the ledger row says which plate, and how hot."""
    (tmp_path / "work").mkdir()
    r = Rig(tmp_path, with_bed_type(CI_PROFILES, BedType.TEXTURED_PEI, tmp_path / "p"))
    with r.daemon:
        job_id = r.daemon.submit(r.stl)
        r.daemon.wait_for_gate(job_id, timeout_s=30)
        r.press("approve")
        assert r.daemon.wait_for_finish(job_id, timeout_s=30).printed

    row = r.ledger.get(job_id)
    assert row is not None
    assert row.slicer_settings["curr_bed_type"] == "Textured PEI Plate"
    assert row.slicer_settings["first_layer_bed_temperature"] == "45"
    assert "plate          Textured PEI Plate, bed 45 C" in describe(row)


# --------------------------------------------------------------------------
# With the real Orca, on the Mac
# --------------------------------------------------------------------------


@pytest.fixture
def real() -> SlicerProfiles:
    return SlicerProfiles(machine=MACHINE, process=PROCESS, filament=FILAMENT, datadir=DATADIR)


@pytest.mark.slicer
@needs_orca
def test_orca_heats_the_bed_for_pei(real: SlicerProfiles, tmp_path: Path) -> None:
    pei = with_bed_type(real, BedType.TEXTURED_PEI, tmp_path / "p")
    result = slice_model(write_stl("cube", tmp_path), pei, tmp_path / "out")
    assert bed_temps(result.gcode_path) == {45}
    assert parse_config_block(result.gcode_path)["curr_bed_type"] == "Textured PEI Plate"


@pytest.mark.slicer
@needs_orca
def test_orca_keeps_the_plate_when_scoring_forces_supports(
    real: SlicerProfiles, tmp_path: Path
) -> None:
    pei = with_bed_type(real, BedType.TEXTURED_PEI, tmp_path / "p")
    scored = scoring_profiles(pei, tmp_path / "s")
    result = slice_model(write_stl("tee", tmp_path), scored, tmp_path / "out")
    config = parse_config_block(result.gcode_path)
    assert bed_temps(result.gcode_path) == {45}
    assert (config["curr_bed_type"], config["enable_support"]) == ("Textured PEI Plate", "1")
