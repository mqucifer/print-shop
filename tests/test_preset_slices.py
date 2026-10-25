"""The same part sliced for each filament differs as its standard and the Sponsor's tuning say.

mqucifer/print-shop#6, part 5, and the prototype's BACKLOG test ("Profile &
material selection", items 1 and 2): the 20 mm cube is sliced with the store's
composed profiles, in PLA and in PETG, from a standard and from the Sponsor's
tuning of one, and the G-code's own record of its settings must match each.
The 0.8 nozzle is out of scope since the Sponsor's review of mqucifer/print-shop#8
(the 0.4 machine, PLA and PETG first), so every case is on the 0.4. Each is sliced as a
print is, for the textured PEI plate: Orca refuses PETG on its Cool Plate
("Cool Plate does not support filament 1", exit 195).

The real Orca slices on the Mac (`slicer`). Each slice is kept, comments and
temperature commands only, in `tests/data/recorded/presets/`, and CI replays
those through the same checks. `PRINT_SHOP_RECORD=1` rewrites them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from print_shop.runtime import profiles_for
from print_shop.slicer.orca import (
    BedType,
    parse_config_block,
    parse_gcode_header,
    slice_model,
    with_bed_type,
)
from print_shop.slicer.store import Choice, Store, choose
from tests.fakes import DATA, STORE
from tests.fixtures import write_stl
from tests.test_graph import needs_orca

RECORDED = DATA / "recorded" / "presets"
#: filament, type, nozzle C, max fan %: Elegoo's standards against the Sponsor's tunings.
EXPECTED = {
    "pla-tuned": ("Elegoo PLA @ECC - Copy", "PLA", "220", "100"),
    "pla-matte-standard": ("Elegoo PLA Matte @ECC", "PLA", "220", "80"),
    "petg-standard": ("Elegoo PETG @ECC", "PETG", "250", "40"),
    "petg-270-tuned": (
        "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,AuxPartCoolingFan=100", "PETG", "270", "50"
    ),
}

#: (label, fitted nozzle, the loaded filament as the Sponsor names it).
CASES = [
    ("pla-tuned", 0.4, "Elegoo PLA"),
    ("pla-matte-standard", 0.4, "Elegoo PLA Matte"),
    ("petg-standard", 0.4, "Elegoo PETG"),
    ("petg-270-tuned", 0.4, "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,AuxPartCoolingFan=100"),
]

#: What is kept of a slice: Orca's comments and the temperature commands.
_KEPT = re.compile(r"^(;|M10[49] |M1[49]0 )")


def _choice(nozzle: float, filament: str) -> Choice:
    return choose(Store(STORE), nozzle_mm=nozzle, filament=filament)


def _first(value: object) -> str:
    return str(value[0] if isinstance(value, list) else value)


def assert_matches_presets(gcode: Path, choice: Choice) -> None:
    """The G-code's settings are the chosen presets' own values."""
    config = parse_config_block(gcode)
    header = parse_gcode_header(gcode)
    machine, process, filament = (
        choice.machine.composed, choice.process.composed, choice.filament.composed
    )

    assert config["printer_settings_id"] == choice.machine.name
    assert config["print_settings_id"] == choice.process.name
    assert config["filament_settings_id"] == choice.filament.name
    assert header.nozzle_mm == float(_first(machine["nozzle_diameter"]))
    assert header.layer_height_mm == float(_first(process["layer_height"]))
    assert header.material == _first(filament["filament_type"])
    assert config["nozzle_temperature"] == _first(filament["nozzle_temperature"])
    assert config["curr_bed_type"] == "Textured PEI Plate"
    beds = {int(t) for t in re.findall(r"^M1[49]0 S(\d+)", gcode.read_text(), re.M)} - {0}
    assert beds == {int(_first(filament["textured_plate_temp"]))}, "the bed, for that plate"
    temps = {int(t) for t in re.findall(r"^M10[49] S(\d+)", gcode.read_text(), re.M)}
    assert int(_first(filament["nozzle_temperature"])) in temps, "the nozzle is heated to it"


@pytest.mark.parametrize(("label", "nozzle", "filament"), CASES)
def test_the_recorded_slice_matches_its_presets(label: str, nozzle: float, filament: str) -> None:
    assert_matches_presets(RECORDED / f"cube-{label}.gcode", _choice(nozzle, filament))


def test_the_slices_differ_where_the_presets_do() -> None:
    """The temperature and fans move with the filament, and with the Sponsor's tuning."""
    seen = {
        label: parse_config_block(RECORDED / f"cube-{label}.gcode") for label, _, _ in CASES
    }
    keys = ("filament_settings_id", "filament_type", "nozzle_temperature", "fan_max_speed")
    rows = {label: tuple(cfg[k] for k in keys) for label, cfg in seen.items()}
    assert rows == EXPECTED, rows
    assert {cfg["printer_settings_id"] for cfg in seen.values()} == {
        "Elegoo Centauri Carbon 0.4 nozzle - Copy"
    }, "the Sponsor's tuned machine, for every filament"


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize(("label", "nozzle", "filament"), CASES)
def test_orca_slices_each_with_its_presets(
    label: str, nozzle: float, filament: str, tmp_path: Path
) -> None:
    choice = _choice(nozzle, filament)
    composed = profiles_for(choice, STORE, tmp_path / "composed")
    pei = with_bed_type(composed, BedType.TEXTURED_PEI, tmp_path / "p")
    result = slice_model(write_stl("cube", tmp_path), pei, tmp_path / "o")
    assert_matches_presets(result.gcode_path, choice)

    kept = "".join(
        line for line in result.gcode_path.read_text().splitlines(keepends=True)
        if _KEPT.match(line)
    )
    recorded = RECORDED / f"cube-{label}.gcode"
    if os.environ.get("PRINT_SHOP_RECORD") == "1":
        recorded.parent.mkdir(parents=True, exist_ok=True)
        recorded.write_text(kept)
    # The recording is this slice, but for the time Orca stamps on it.
    stamp = re.compile(r"^; generated by .*$", re.M)
    assert stamp.sub("", recorded.read_text()) == stamp.sub("", kept)


def test_the_cases_cover_both_materials_standard_and_tuned() -> None:
    covered = {
        (_choice(n, f).filament.filament_type, _choice(n, f).filament.standard is not None)
        for _, n, f in CASES
    }
    assert covered == {("PLA", True), ("PLA", False), ("PETG", True), ("PETG", False)}
