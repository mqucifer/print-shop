"""The slice, read at the review: layer times, features, the seam, and what they flag.

mqucifer/print-shop#105. On the first real prints, reading the G-code by hand found layers of
about 8 s with slow-down off, ironing on faces nobody sees, and an aligned seam on walls that
are the part's look. These tests show each flagged, with a proposed change, on G-code we make:
small G-code written here, and a channel of our own sliced by the real Orca. Never anyone's
design.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

import pytest

from print_shop.setup.bounds import check
from print_shop.setup.intake import Answer, Intake
from print_shop.setup.proposal import Change, Proposal, Source, apply
from print_shop.setup.questions import PURPOSE, SEEN
from print_shop.setup.reading import Seen, SliceReading, flags, read_slice, seen
from print_shop.slicer import gcode
from tests.fakes import DATA, STORE
from tests.fixtures import write_stl
from tests.test_graph import needs_orca

PETG = {
    "filament_type": "PETG",
    "slow_down_for_layer_cooling": "0",
    "slow_down_layer_time": "12",
    "slow_down_min_speed": "10",
    "ironing_type": "top",
    "seam_position": "aligned",
}
#: The presets' own values, as a change is shown against them.
PROCESS: dict[str, Any] = {"ironing_type": "top", "seam_position": "aligned",
                           "outer_wall_speed": "200"}
FILAMENT: dict[str, Any] = {"slow_down_for_layer_cooling": ["1"], "slow_down_layer_time": ["12"],
                            "slow_down_min_speed": ["10"]}
WALLS_ONLY = Seen(walls=True, top=False, bottom=False, said="the walls; the top is hidden")
ALL_SEEN = Seen(walls=True, top=True, bottom=False, said="the walls and the top")
TOP_ONLY = Seen(walls=False, top=True, bottom=False, said="the top; the walls are hidden")


def _gcode(
    layers: int = 20,
    side: float = 10.0,
    feed: float = 6000,
    config: dict[str, str] | None = None,
    ironed: tuple[int, ...] = (),
    seam_stays: bool = True,
) -> str:
    """A square wall loop a layer, `side` mm a side, at `feed` mm/min; ironing on `ironed`."""
    corners = [(10.0, 10.0), (10.0 + side, 10.0), (10.0 + side, 10.0 + side),
               (10.0, 10.0 + side)]
    out = ["M83", "SET_VELOCITY_LIMIT ACCEL=5000", "G1 X0 Y0 F12000"]
    for n in range(1, layers + 1):
        z = round(0.2 * n, 2)
        out += [";LAYER_CHANGE", f";Z:{z}", ";HEIGHT:0.2", f"G1 Z{z} F12000"]
        start = 0 if seam_stays else n % 4
        loop = corners[start:] + corners[:start]
        out += [f"G1 X{loop[0][0]} Y{loop[0][1]} F12000", ";TYPE:Outer wall", ";WIDTH:0.42",
                f"G1 F{feed:g}"]
        for x, y in [*loop[1:], loop[0]]:
            out.append(f"G1 X{x} Y{y} E0.4")
        if n in ironed:
            out += [";TYPE:Ironing", ";WIDTH:0.4", "G1 F1800",
                    f"G1 X{10 + side} Y{10 + side / 2} E0.01"]
        out.append("G1 E-0.8 F1800")
    out += ["; estimated printing time (normal mode) = 1m 0s", "; CONFIG_BLOCK_START"]
    out += [f"; {k} = {v}" for k, v in (config if config is not None else PETG).items()]
    out.append("; CONFIG_BLOCK_END")
    return "\n".join(out) + "\n"


def _read(tmp_path: Path, **kw: Any) -> SliceReading:
    path = tmp_path / "plate_1.gcode"
    path.write_text(_gcode(**kw))
    return read_slice(path)


# --------------------------------------------------------------------------
# Timing the moves
# --------------------------------------------------------------------------


def test_a_straight_move_takes_its_time_accelerating_and_braking(tmp_path: Path) -> None:
    """100 mm at 100 mm/s under 1000 mm/s2: 5 mm up to speed, 90 at it, 5 braking."""
    path = tmp_path / "plate_1.gcode"
    path.write_text("M83\nM204 S1000\n;LAYER_CHANGE\n;Z:0.2\n;HEIGHT:0.2\n;TYPE:Outer wall\n"
                    ";WIDTH:0.4\nG1 X0 Y0 F6000\nG1 X100 Y0 E1\n")
    layer = gcode.read(path).layers[0]
    assert layer.times["Outer wall"] == pytest.approx(0.1 + 0.9 + 0.1)
    assert layer.areas["Outer wall"] == pytest.approx(100 * 0.4)


def _layer(moves: str) -> gcode.Layer:
    path = Path(tempfile.mkdtemp()) / "plate_1.gcode"
    path.write_text("M83\nM204 S1000\n;LAYER_CHANGE\n;Z:0.2\n;HEIGHT:0.2\n;TYPE:Outer wall\n"
                    ";WIDTH:0.4\nG1 X0 Y0 F6000\n" + moves)
    return gcode.read(path).layers[0]


def test_a_corner_slows_the_head_and_a_stop_starts_it_again() -> None:
    # Two moves in a line take what one does.
    assert _layer("G1 X50 Y0 E1\nG1 X100 Y0 E1\n").times["Outer wall"] == pytest.approx(1.1)
    cornered = _layer("G1 X50 Y0 E1\nG1 X50 Y50 E1\n").times["Outer wall"]
    assert 1.1 < cornered < 2 * (0.1 + 0.4 + 0.1), "slowed, not stopped"
    # A retraction and a dwell stop the head between them, and their own time counts.
    stopped = _layer("G1 X50 Y0 E1\nG1 E-0.8 F1800\nG4 P500\nG1 E0.8\nG1 X100 Y0 E1 F6000\n")
    assert stopped.times["Outer wall"] == pytest.approx(2 * (0.1 + 0.4 + 0.1))
    assert stopped.times[gcode.TRAVEL] == pytest.approx(2 * 0.8 / 30 + 0.5, abs=1e-3)


def test_relative_moves_and_the_end_gcode_are_read_as_such() -> None:
    relative = _layer("G91\nG1 X50 E1\nG1 X50 E1\nG90\n")
    assert relative.lengths["Outer wall"] == pytest.approx(100)
    # Parking after the last line laid is the end G-code's, not the last layer's.
    parked = _layer("G1 X100 Y0 E1\nG1 E-0.8 F1800\nG1 Z100 F600\nG1 X200 Y200 F12000\n")
    assert parked.time_s == pytest.approx(1.1)


def test_a_slice_orca_wrote_takes_what_orca_says_it_takes() -> None:
    """The recorded cube: the layers' sum is within 1% of Orca's own estimate."""
    sliced = gcode.read(DATA / "recorded" / "cube.gcode")
    total = sum(layer.time_s for layer in sliced.layers)
    assert sliced.time_s is not None
    assert total == pytest.approx(sliced.time_s, rel=0.01)


# --------------------------------------------------------------------------
# The reading
# --------------------------------------------------------------------------


def test_the_reading_has_each_layers_time_each_features_cost_and_the_seam(
    tmp_path: Path,
) -> None:
    reading = _read(tmp_path, ironed=(5, 20))
    assert len(reading.layers) == 20
    assert all(0 < layer.time_s < 3 for layer in reading.layers)
    wall = reading.feature("Outer wall")
    assert wall is not None and wall.layers == 20 and (wall.from_z, wall.to_z) == (0.2, 4.0)
    assert wall.area_mm2 == pytest.approx(20 * 40 * 0.42, rel=1e-3)
    assert [layer.z for layer in reading.ironed] == [1.0, 4.0]
    assert reading.seam.is_line and reading.seam.at == (10.0, 10.0)
    assert reading.seam.at_layers == 20
    assert reading.settings == PETG
    assert reading.orca_time_s == 60
    lines = reading.lines()
    assert lines[0].startswith("layers: 20, ")
    assert any(line.startswith("Ironing: ") for line in lines)
    assert "a line up the wall" in lines[-1]


def test_a_seam_that_moves_each_layer_reads_scattered(tmp_path: Path) -> None:
    reading = _read(tmp_path, seam_stays=False)
    assert reading.seam.seams == 20 and reading.seam.stacked == 0.0
    assert not reading.seam.is_line


# --------------------------------------------------------------------------
# What it flags, and the change each proposes
# --------------------------------------------------------------------------


def test_short_layers_with_slow_down_off_propose_slow_down_on(tmp_path: Path) -> None:
    reading = _read(tmp_path)
    (flag,) = [f for f in flags(reading, None, PROCESS, FILAMENT) if f.id == "layer_time"]
    assert flag.change is not None
    c = flag.change
    assert (c.key, c.preset, c.value, c.source) == (
        "slow_down_for_layer_cooling", "1", "1", Source.READING)
    assert "20 of 20 layers take under 12 s" in flag.reading
    assert c.reading == flag.reading and "too short for PETG" in c.reason
    assert check(c.key, c.value, {"process": PROCESS, "filament": FILAMENT}) == ""


def test_short_layers_with_slow_down_on_propose_a_lower_floor(tmp_path: Path) -> None:
    reading = _read(tmp_path, config=PETG | {"slow_down_for_layer_cooling": "1"})
    (flag,) = [f for f in flags(reading, None, PROCESS, FILAMENT) if f.id == "layer_time"]
    assert flag.change is not None
    assert (flag.change.key, flag.change.value) == ("slow_down_min_speed", "5")
    assert "won't go under 10 mm/s" in flag.change.reason


def test_layers_long_enough_to_cool_flag_nothing(tmp_path: Path) -> None:
    reading = _read(tmp_path, side=200.0, feed=3000)  # 800 mm a layer at 50 mm/s
    assert min(layer.time_s for layer in reading.layers) > 12
    assert not [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT) if f.id == "layer_time"]


def test_ironing_a_top_the_intake_says_is_hidden_proposes_no_ironing(tmp_path: Path) -> None:
    reading = _read(tmp_path, ironed=(20,))
    (flag,) = [f for f in flags(reading, WALLS_ONLY, PROCESS, FILAMENT) if f.id.startswith("ir")]
    assert flag.id == "ironing_hidden" and flag.change is not None
    assert (flag.change.key, flag.change.preset, flag.change.value) == (
        "ironing_type", "top", "no ironing")
    assert "you said 'the walls; the top is hidden'" in flag.change.reason


def test_ironing_a_top_inside_the_part_proposes_topmost(tmp_path: Path) -> None:
    """A channel's floor is a top as it prints, under the rim's: ironed, and nobody sees it."""
    reading = _read(tmp_path, ironed=(5, 20))
    (flag,) = [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT) if f.id.startswith("ir")]
    assert flag.id == "ironing_inside" and flag.change is not None
    assert flag.change.value == "topmost" and "(z 1 mm)" in flag.change.reason


def test_ironing_only_the_seen_top_flags_nothing(tmp_path: Path) -> None:
    reading = _read(tmp_path, ironed=(20,))
    assert not [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT) if f.id.startswith("ir")]


def test_an_aligned_seam_on_walls_that_are_seen_proposes_moving_it(tmp_path: Path) -> None:
    reading = _read(tmp_path)
    (flag,) = [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT) if f.id == "seam_seen"]
    assert flag.change is not None
    assert (flag.change.key, flag.change.preset, flag.change.value) == (
        "seam_position", "aligned", "back")
    at_back = _read(tmp_path, config=PETG | {"seam_position": "back"})
    (moved,) = [f for f in flags(at_back, ALL_SEEN, PROCESS, FILAMENT) if f.id == "seam_seen"]
    assert moved.change is not None and moved.change.value == "random"


def test_the_seam_is_left_alone_on_hidden_walls_or_when_scattered(tmp_path: Path) -> None:
    line = _read(tmp_path)
    assert not [f for f in flags(line, TOP_ONLY, PROCESS, FILAMENT) if f.id == "seam_seen"]
    scattered = _read(tmp_path, seam_stays=False)
    assert not [f for f in flags(scattered, ALL_SEEN, PROCESS, FILAMENT) if f.id == "seam_seen"]


def test_without_an_answer_about_faces_nothing_about_them_is_proposed(tmp_path: Path) -> None:
    """Which face is seen comes from the intake, never from which face is on top."""
    reading = _read(tmp_path, ironed=(20,))
    found = {f.id: f for f in flags(reading, None, PROCESS, FILAMENT)}
    assert "seam_seen" not in found
    assert found["ironing_unknown"].change is None
    assert "the intake didn't say which faces are seen" in found["ironing_unknown"].reading


def test_every_proposed_change_is_inside_its_bound(tmp_path: Path) -> None:
    presets: dict[str, dict[str, Any]] = {"process": PROCESS, "filament": FILAMENT}
    for config in (PETG, PETG | {"slow_down_for_layer_cooling": "1"},
                   PETG | {"seam_position": "back"}):
        reading = _read(tmp_path, ironed=(5, 20), config=config)
        for faces in (None, ALL_SEEN, WALLS_ONLY, TOP_ONLY):
            for flag in flags(reading, faces, PROCESS, FILAMENT):
                if flag.change is not None:
                    assert check(flag.change.key, flag.change.value, presets) == "", flag


# --------------------------------------------------------------------------
# Which faces are seen, from the intake
# --------------------------------------------------------------------------


def test_which_faces_are_seen_is_read_from_the_intakes_answer() -> None:
    def answered(answer: Answer) -> Intake:
        return Intake(questions=(PURPOSE, SEEN), answers=(Answer(choice=0), answer))

    hidden_top = seen(answered(Answer(choice=1)))
    assert hidden_top is not None
    assert (hidden_top.walls, hidden_top.top, hidden_top.bottom) == (True, False, False)
    plate = seen(answered(Answer(choice=3)))
    assert plate is not None and (plate.walls, plate.top, plate.bottom) == (False, False, True)
    assert seen(answered(Answer(other="only from the front"))) is None, "words aren't guessed"
    assert seen(Intake(questions=(PURPOSE,), answers=(Answer(choice=0),))) is None
    assert seen(None) is None


# --------------------------------------------------------------------------
# On the real Orca, a part of our own
# --------------------------------------------------------------------------


@needs_orca
def test_a_channel_sliced_by_orca_flags_all_three(tmp_path: Path) -> None:
    """The faults of the first real prints, on an open-top channel of our own, in PETG.

    Ironing on (`top`), the seam aligned, slow-down off. The walls are seen and the top as it
    prints is hidden: short layers, ironing on the hidden tops, and the seam a line on the walls
    are each flagged, with a change. And the layers take what Orca's own progress (`M73`) says
    they take, between the first layers and the last.
    """
    from print_shop.slicer.orca import BedType, SlicerProfiles, slice_model, with_bed_type
    from print_shop.slicer.store import Store, choose

    composed = choose(Store(STORE), nozzle_mm=0.4, filament="Elegoo PETG").compose(
        tmp_path / "composed")
    profiles = with_bed_type(
        SlicerProfiles(machine=composed["machine"], process=composed["process"],
                       filament=composed["filament"]),
        BedType.TEXTURED_PEI, tmp_path / "bed")
    assert profiles.filament is not None
    process = json.loads(profiles.process.read_text())
    filament = json.loads(profiles.filament.read_text())

    def guard(key: str, value: str) -> Change:
        return Change(key=key, preset="", value=value, reason="the test's", source=Source.GUARD)

    profiles = apply(profiles, Proposal(changes=(
        guard("ironing_type", "top"), guard("seam_position", "aligned"),
        guard("slow_down_for_layer_cooling", "0"))), tmp_path / "set")
    result = slice_model(write_stl("channel", tmp_path), profiles, tmp_path / "slice")
    reading = read_slice(result.gcode_path)

    found = {f.id: f for f in flags(reading, WALLS_ONLY, process, filament)}
    assert set(found) == {"layer_time", "ironing_hidden", "seam_seen"}
    assert all(f.change is not None for f in found.values())
    assert reading.shortest() is not None and reading.shortest().time_s < 12  # type: ignore[union-attr]
    assert len({layer.z for layer in reading.ironed}) == 2, "the floor and the rim"

    # Orca's progress at each layer's start, as a percent of its estimate.
    progress, percent = [], 0
    for line in result.gcode_path.read_text().splitlines():
        hit = re.match(r"M73 P(\d+)", line)
        if hit:
            percent = int(hit.group(1))
        elif line.startswith(";LAYER_CHANGE"):
            progress.append(percent)
    assert reading.orca_time_s is not None
    a, b = 12, len(reading.layers) - 6
    orca = (progress[b] - progress[a]) / 100 * reading.orca_time_s
    ours = sum(layer.time_s for layer in reading.layers[a:b])
    # M73 counts whole percents, so Orca's side is known to about 2 percent of the whole.
    assert ours == pytest.approx(orca, abs=0.02 * reading.orca_time_s)
