"""The bench on planted cases, in its image with no network. Needs `scripts/bench.py build`.

The map's "Never take a tool's word" table is the spec: each row that touches these commands
is a case here that the command must catch, named after the row. Everything's made by
`bench_cases/make_fixtures.py`, and right answers come from arithmetic.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"


@dataclass
class Answer:
    code: int
    text: str
    record: dict[str, Any]

    @property
    def verdict(self) -> str:
        return str(self.record["verdict"])

    @property
    def numbers(self) -> dict[str, Any]:
        return dict(self.record["numbers"])


@pytest.fixture(scope="session")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("bench")
    for script in CASES.glob("*.py"):
        shutil.copy(script, work / script.name)
    made = host.run(["make_fixtures.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr

    def ask(*args: str) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


def only_pair(answer: Answer) -> dict[str, Any]:
    pairs = answer.numbers["pairs"]
    assert len(pairs) == 1
    return dict(pairs[0])


# How every answer runs


def test_it_runs_with_no_network_inside_its_limits_as_the_invoking_user(bench: Any) -> None:
    ran = bench("info", "block.step").record["ran"]
    assert ran["network_up"] == ["lo"]
    assert ran["network_routes"] == 0
    assert ran["cpu_max"].split()[0] == "200000", "2 CPUs"
    assert int(ran["memory_max"]) == 3 * 1024**3
    assert ran["user"] != 0
    assert ran["image"] == host.IMAGE and ran["image_id"].startswith("sha256:")


def test_generated_code_runs_there_too_and_reaches_nothing(bench: Any) -> None:
    script = bench.work / "reach.py"
    script.write_text(
        "import socket\n"
        "s = socket.socket()\ns.settimeout(3)\n"
        "try:\n    s.connect(('example.com', 80))\n    print('REACHED')\n"
        "except OSError as e:\n    print('refused', e)\n"
        "try:\n    open('/opt/bench/bench/cli.py', 'a')\n    print('WROTE')\n"
        "except OSError as e:\n    print('read-only', e)\n"
    )
    done = host.run(["reach.py"], bench.work, python=True)
    assert "refused" in done.stdout and "REACHED" not in done.stdout
    assert "read-only" in done.stdout and "WROTE" not in done.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("info", "block.step"),
        ("clearance", "cube.step", "cube-gap-0p1.step"),
        ("readback", "block.step", "block-seat.step"),
        ("rays", "block-seat.step", "--centre", "30,20,16", "--d", "22"),
        ("panes", "frame.step", "--pocket-z", "2", "--clearance", "0.1"),
        ("thin", "wall-0p8.step"),
        ("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2"),
        ("render", "block.step", "--views", "iso"),
        ("params", "design.py", "--set", "Thickness=4"),
    ],
)
def test_every_command_says_its_verdict_second_check_and_file(
    bench: Any, args: tuple[str, ...]
) -> None:
    answer = bench(*args)
    assert answer.text.startswith("verdict: ")
    assert answer.record["second_check"], "no answer without a second check"
    assert "second check: " in answer.text
    assert "saved: " in answer.text
    assert answer.verdict in ("OK", "CLEAR", "TOUCHING"), answer.text


def test_a_file_that_isnt_there_is_no_answer(bench: Any) -> None:
    answer = bench("info", "nothing.step")
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    assert "nothing.step" in answer.text


def test_a_wrong_command_is_told_so(bench: Any) -> None:
    done = host.run(["rays", "block.step"], bench.work)
    assert done.returncode == 3
    assert "usage error" in done.stderr


# Near-touching faces: results empty or disagreeing, often zero overlap


def test_near_touching_zero_overlap_from_occt_never_reads_as_clear(bench: Any) -> None:
    """A leaf swung over another, faces all but coincident: about 1,920 mm³ of real overlap."""
    answer = bench("clearance", "hinge-a.step", "hinge-b-y06-m3e4.step")
    pair = only_pair(answer)
    assert answer.verdict not in ("CLEAR", "TOUCHING"), answer.text
    assert answer.code != 0
    assert 1900 < pair["overlap_mesh_mm3"] < 1940


@pytest.mark.parametrize("name", ["y03-p3e5", "y03-p1e3", "y06-m3e4"])
def test_near_touching_the_mesh_holds_steady(bench: Any, name: str) -> None:
    pair = only_pair(bench("clearance", "hinge-a.step", f"hinge-b-{name}.step"))
    assert 1900 < pair["overlap_mesh_mm3"] < 1940


def test_near_touching_the_trials_66_cases(bench: Any) -> None:
    """OCCT goes wrong in some, and the mesh stays within 1% of the truth in all of them."""
    done = host.run(["near_touching_sweep.py"], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["cases"] == 66
    assert found["occt_wrong_or_empty"] > 0, "the plant has stopped biting: no OCCT failure here"
    assert found["mesh_lowest"] > 1900 and found["mesh_highest"] < 1960


@pytest.mark.parametrize(
    ("other", "verdict", "overlap", "gap"),
    [
        ("cube-gap-0p1.step", "CLEAR", 0.0, 0.1),
        ("cube-gap-0p001.step", "CLEAR", 0.0, 0.001),
        ("cube-touch.step", "TOUCHING", 0.0, 0.0),
        ("cube-overlap-0p001.step", "INTERFERE", 0.1, 0.0),
        ("cube-overlap-1.step", "INTERFERE", 100.0, 0.0),
    ],
)
def test_clearance_against_arithmetic(
    bench: Any, other: str, verdict: str, overlap: float, gap: float
) -> None:
    answer = bench("clearance", "cube.step", other)
    pair = only_pair(answer)
    assert answer.verdict == verdict, answer.text
    assert pair["overlap_mesh_mm3"] == pytest.approx(overlap, abs=1e-3)
    assert pair["overlap_occt_common_mm3"] == pytest.approx(overlap, abs=1e-3)
    assert pair["gap_mesh_mm"] == pytest.approx(gap, abs=1e-4)
    assert pair["gap_occt_mm"] == pytest.approx(gap, abs=1e-4)


def test_clearance_of_a_pin_in_its_hole(bench: Any) -> None:
    clear = only_pair(bench("clearance", "plate.step", "pin.step"))
    assert clear["verdict"] == "CLEAR"
    assert clear["gap_occt_mm"] == pytest.approx(0.05, abs=1e-4)
    assert clear["gap_mesh_mm"] == pytest.approx(0.05, abs=2 * 0.002 + 1e-4)
    # A coincident curved wall's chords make no phantom interference.
    assert bench("clearance", "plate.step", "pin-exact.step").verdict == "TOUCHING"
    # (5.1² - 5.05²)π × 8 mm of plate.
    tight = bench("clearance", "plate.step", "pin-tight.step")
    assert tight.verdict == "INTERFERE"
    assert only_pair(tight)["overlap_occt_common_mm3"] == pytest.approx(12.755, abs=0.01)


def test_clearance_holds_a_stated_gap(bench: Any) -> None:
    assert bench("clearance", "plate.step", "pin.step", "--min-gap", "0.04").verdict == "CLEAR"
    answer = bench("clearance", "plate.step", "pin.step", "--min-gap", "0.1")
    assert answer.verdict == "TOO CLOSE" and answer.code == 1


def test_clearance_of_a_file_of_several_solids_reads_each_pair(bench: Any) -> None:
    answer = bench("clearance", "speck.step")
    assert len(answer.numbers["pairs"]) == 1
    assert only_pair(answer)["gap_occt_mm"] == pytest.approx(10.0, abs=1e-3)


def test_clearance_of_one_solid_is_no_answer(bench: Any) -> None:
    assert bench("clearance", "block.step").verdict == "NO ANSWER"


@pytest.mark.parametrize(
    ("other", "verdict", "overlap", "gap"),
    [("cube-gap-0p1.stl", "CLEAR", 0.0, 0.1), ("cube-overlap-1.stl", "INTERFERE", 100.0, 0.0)],
)
def test_clearance_of_meshes_has_its_own_second_method(
    bench: Any, other: str, verdict: str, overlap: float, gap: float
) -> None:
    answer = bench("clearance", "cube.stl", other)
    pair = only_pair(answer)
    assert answer.verdict == verdict
    assert pair["overlap_sections_mm3"] == pytest.approx(overlap, abs=0.5)
    assert pair["gap_sampled_mm"] == pytest.approx(gap, abs=0.5)
    assert "sections" in answer.text


# A pocket that missed the body: "feature valid"


def test_a_pocket_that_missed_the_body_is_caught_by_volume(bench: Any) -> None:
    answer = bench("readback", "block.step", "block-missed.step")
    assert answer.verdict == "NO CHANGE" and answer.code == 1
    assert answer.numbers["change_mm3"] == pytest.approx(0.0, abs=1e-3)


def test_a_pocket_that_missed_the_body_is_caught_by_rays(bench: Any) -> None:
    answer = bench("rays", "block-missed.step", "--centre", "30,20,16", "--d", "22")
    assert answer.verdict == "FAIL"
    assert "in material" in answer.text


def test_a_pocket_that_missed_reads_the_cutter_outside(bench: Any) -> None:
    answer = bench("readback", "block.step", "block-missed.step", "--cutter", "cutter-away.step")
    assert answer.code == 1
    assert answer.numbers["cutter_in_body_share"] == 0


def test_a_pocket_cut_well_passes_with_the_right_volume(bench: Any) -> None:
    answer = bench(
        "readback",
        "block.step",
        "block-seat.step",
        "--cutter",
        "cutter.step",
        "--min-share",
        "0.99",
    )
    assert answer.verdict == "OK", answer.text
    # π × 11² × 7.
    assert answer.numbers["change_mm3"] == pytest.approx(-2660.93, abs=1.0)
    assert answer.numbers["cutter_in_body_share"] == pytest.approx(1.0, abs=1e-3)


def test_a_small_cut_in_a_large_part_isnt_read_as_nothing(bench: Any) -> None:
    answer = bench("readback", "block.step", "block-nick.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["change_mm3"] == pytest.approx(-1.0, abs=1e-3)


def test_an_expected_change_is_held_to(bench: Any) -> None:
    good = bench("readback", "block.step", "block-nick.step", "--expect-delta", "-1")
    assert good.verdict == "OK"
    bad = bench("readback", "block.step", "block-nick.step", "--expect-delta", "-2")
    assert bad.verdict == "FAIL"


# A bearing seat mostly in mid-air: no error, watertight, one body


def test_a_seat_in_mid_air_is_caught_by_rays(bench: Any) -> None:
    answer = bench("rays", "block-seat-air.step", "--centre", "60,20,16", "--d", "22")
    level = answer.numbers["levels"][0]
    assert answer.verdict == "FAIL", answer.text
    assert level["missed"] == pytest.approx(360, abs=2), "half of its 720 rays hit nothing"
    assert level["closed_loop_around_centre"] is False


def test_a_seat_in_mid_air_looks_fine_to_the_things_that_said_so(bench: Any) -> None:
    """The plant is the real failure: the tools' own word on it is all good."""
    answer = bench("info", "block-seat-air.step")
    assert answer.verdict == "OK"
    assert answer.numbers["watertight"] is True
    assert answer.numbers["valid"] is True
    assert answer.numbers["bodies_mesh"] == 1


def test_a_seat_in_mid_air_is_caught_by_the_cutter_share(bench: Any) -> None:
    answer = bench(
        "readback",
        "block.step",
        "block-seat-air.step",
        "--cutter",
        "cutter-air.step",
        "--min-share",
        "0.99",
    )
    assert answer.verdict == "FAIL"
    assert answer.numbers["cutter_in_body_share"] == pytest.approx(0.5, abs=1e-3)


def test_rays_measure_a_good_seat_in_mm(bench: Any) -> None:
    """In mm: Manifold's own `distance` is a fraction of the ray, which read as 0.147 mm."""
    answer = bench("rays", "block-seat.step", "--centre", "30,20,20", "--d", "22", "--depth", "-7")
    assert answer.verdict == "OK", answer.text
    level = answer.numbers["levels"][1]
    assert level["missed"] == 0
    assert level["across_mm"]["mean"] == pytest.approx(22.0, abs=0.01)
    assert level["section_fit_d_mm"] == pytest.approx(22.0, abs=0.01)
    # The block's y walls are 20 mm from its centre, and the seat's radius is 11.
    assert level["wall_beyond_min_mm"] == pytest.approx(9.0, abs=0.01)
    assert answer.numbers["floor_mm"]["min"] == pytest.approx(7.0, abs=0.01)


def test_rays_catch_the_wrong_size_by_both_methods(bench: Any) -> None:
    answer = bench("rays", "block-seat-wide.step", "--centre", "30,20,16", "--d", "22")
    assert answer.verdict == "FAIL", answer.text
    assert answer.numbers["levels"][0]["section_fit_d_mm"] == pytest.approx(23.0, abs=0.01)


def test_rays_catch_the_wrong_depth(bench: Any) -> None:
    answer = bench("rays", "block-seat.step", "--centre", "30,20,20", "--d", "22", "--depth", "-9")
    assert answer.verdict == "FAIL"
    assert "floor" in answer.text


def test_rays_refuse_a_height_on_a_level_face(bench: Any) -> None:
    answer = bench("rays", "block-seat.step", "--centre", "30,20,20", "--d", "22")
    assert answer.verdict == "NO ANSWER"
    assert "level face" in answer.text


def test_rays_work_along_any_axis(bench: Any) -> None:
    answer = bench(
        "rays",
        "block-bore-x.step",
        "--centre",
        "0,20,10",
        "--axis",
        "x",
        "--d",
        "10",
        "--depth",
        "10",
    )
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["levels"][0]["across_mm"]["mean"] == pytest.approx(10.0, abs=0.01)


# Mesh repair that kept 5% of the volume: "watertight"


def test_a_repair_that_kept_5_percent_is_caught(bench: Any) -> None:
    answer = bench("readback", "form.stl", "form-repaired.stl", "--expect-delta", "0")
    assert answer.verdict == "FAIL"
    assert answer.numbers["change_pct"] == pytest.approx(-95.0, abs=0.1)


def test_a_repair_that_kept_5_percent_is_caught_with_nothing_said(bench: Any) -> None:
    assert bench("readback", "form.stl", "form-repaired.stl").verdict == "FAIL"


def test_it_was_watertight_all_the_same(bench: Any) -> None:
    answer = bench("info", "form-repaired.stl")
    assert answer.verdict == "OK" and answer.numbers["watertight"] is True


def test_an_unchanged_repair_passes(bench: Any) -> None:
    assert bench("readback", "form.stl", "form.stl", "--expect-delta", "0").verdict == "OK"


# Thin shelled surface: CAD volume swung, even negative


def test_a_thin_shell_with_a_plain_cad_volume_gets_no_answer(bench: Any) -> None:
    """OCCT calls it valid and gives the solid sphere's volume. Its shell holds about 100 mm³."""
    answer = bench("info", "sphere-shell.step")
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    assert "valid True" in answer.text
    assert "isn't closed" in answer.text


def test_an_inside_out_mesh_isnt_given_a_volume(bench: Any) -> None:
    answer = bench("info", "form-inverted.stl")
    assert answer.verdict == "DISAGREE"
    assert "inside out" in answer.text


def test_an_open_mesh_isnt_given_a_volume(bench: Any) -> None:
    answer = bench("info", "form-holed.stl")
    assert answer.verdict == "NO ANSWER"
    assert answer.numbers["watertight"] is False


# Panes from openings


def test_panes_from_openings_measure_their_fit_two_ways(bench: Any) -> None:
    answer = bench(
        "panes", "frame.step", "--pocket-z", "2", "--lip-z", "3.5", "--clearance", "0.1",
        "--pane-thickness", "2",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    panes = answer.numbers["panes"]
    assert len(panes) == 3
    assert panes[0]["pane_mm2"] == pytest.approx(39.8 * 39.8, abs=0.01)
    assert panes[1]["pane_mm2"] == pytest.approx(29.8 * 29.8, abs=0.01)
    for pane in panes:
        assert pane["gap_2d_mm"] == pytest.approx(0.1, abs=1e-3)
        assert pane["gap_3d_mm"] == pytest.approx(0.1, abs=1e-3)
        assert pane["overlap_3d_mm3"] == pytest.approx(0.0, abs=1e-3)


def test_the_clearance_is_a_parameter(bench: Any) -> None:
    answer = bench("panes", "frame.step", "--pocket-z", "2", "--clearance", "0.25")
    pane = answer.numbers["panes"][0]
    assert pane["pane_mm2"] == pytest.approx(39.5 * 39.5, abs=0.01)
    assert pane["gap_3d_mm"] == pytest.approx(0.25, abs=1e-3)


def test_panes_are_written_as_files_of_the_right_size(bench: Any) -> None:
    bench("panes", "frame.step", "--pocket-z", "2", "--clearance", "0.1", "--pane-thickness", "2")
    stl = bench("info", "panes/frame/pane-1.stl")
    assert stl.numbers["volume_mesh_mm3"] == pytest.approx(39.8 * 39.8 * 2, abs=0.1)
    step = bench("info", "panes/frame/pane-1.step")
    assert step.numbers["volume_mesh_mm3"] == pytest.approx(39.8 * 39.8 * 2, abs=0.1)


def test_a_written_pane_fits_its_frame_by_the_clearance_command(bench: Any) -> None:
    """A third way round: the pane's file, read back and measured against the frame's. It rests
    on the pocket's floor, so it touches, and doesn't overlap."""
    bench("panes", "frame.step", "--pocket-z", "2", "--clearance", "0.1", "--pane-thickness", "2")
    answer = bench("clearance", "frame.step", "panes/frame/pane-1.step")
    pair = only_pair(answer)
    assert answer.verdict == "TOUCHING", answer.text
    assert pair["overlap_occt_common_mm3"] == pytest.approx(0.0, abs=1e-3)


def test_a_pane_that_fits_its_section_and_not_its_pocket_is_caught(bench: Any) -> None:
    """Drafted walls: clear at the height it's read, into the wall lower down."""
    answer = bench("panes", "frame-draft.step", "--pocket-z", "2", "--clearance", "0.1")
    pane = answer.numbers["panes"][0]
    assert pane["gap_2d_mm"] == pytest.approx(0.1, abs=1e-3), "the section alone looks right"
    assert answer.verdict == "FAIL", answer.text
    assert pane["overlap_3d_mm3"] > 1


def test_too_small_a_pane_is_caught(bench: Any) -> None:
    answer = bench("panes", "frame-small.step", "--pocket-z", "2", "--clearance", "0.1")
    assert answer.verdict == "FAIL"
    assert "too small" in answer.text
    assert [bool(p.get("too_small")) for p in answer.numbers["panes"]] == [False, True]


def test_edge_with_no_lip_is_measured_two_ways_and_reported_not_judged(bench: Any) -> None:
    answer = bench("panes", "frame.step", "--pocket-z", "2", "--lip-z", "3.5", "--clearance", "0.1")
    assert answer.verdict == "OK"
    panes = answer.numbers["panes"]
    assert panes[1]["no_lip_mm"] == pytest.approx(26.0, abs=0.1)
    assert panes[1]["no_lip_rays_mm"] == pytest.approx(26.0, abs=0.75)
    assert panes[0]["no_lip_mm"] == pytest.approx(0.0, abs=0.01)
    assert panes[0]["no_lip_rays_mm"] == pytest.approx(0.0, abs=0.01)
    assert "may be deliberate" in answer.text


def test_a_plain_hole_isnt_given_a_pane(bench: Any) -> None:
    answer = bench("panes", "frame.step", "--pocket-z", "2", "--lip-z", "3.5", "--clearance", "0.1")
    assert answer.numbers["plain_holes"][0]["mm2"] == pytest.approx(16.0, abs=0.01)
    assert "plain hole" in answer.text


def test_panes_read_a_mesh_as_they_read_a_step(bench: Any) -> None:
    answer = bench("panes", "frame.stl", "--pocket-z", "2", "--lip-z", "3.5", "--clearance", "0.1")
    assert answer.verdict == "OK", answer.text
    assert "triangles cut one by one" in answer.text
    assert answer.numbers["panes"][0]["pane_mm2"] == pytest.approx(39.8 * 39.8, abs=0.01)


def test_a_height_on_a_level_face_is_refused(bench: Any) -> None:
    answer = bench("panes", "frame.step", "--pocket-z", "3", "--clearance", "0.1")
    assert answer.verdict == "NO ANSWER"
    assert "level face" in answer.text


# Thin features and small pieces


def test_a_wall_thinner_than_the_nozzle_is_caught_two_ways(bench: Any) -> None:
    answer = bench("thin", "wall-0p3.step")
    assert answer.verdict == "FAIL", answer.text
    assert answer.numbers["thinnest_across_mm"] == pytest.approx(0.3, abs=0.01)
    assert answer.numbers["thinnest_wall_mm"] == pytest.approx(0.3, abs=0.01)
    # 10 × 6 mm, both sides, give or take a sample.
    assert answer.numbers["thin_wall_area_mm2"] == pytest.approx(120, rel=0.25)


def test_a_thin_wall_the_sections_cant_see_is_a_disagreement(bench: Any) -> None:
    """One section misses the fin, and rays through the surface still find it."""
    answer = bench("thin", "fin-low.step", "--max-layers", "1")
    assert answer.verdict == "DISAGREE", answer.text
    assert answer.numbers["thin_spots"] == 0
    assert answer.numbers["thin_rays_not_at_a_point"] >= 5


def test_a_wall_wider_than_the_nozzle_passes(bench: Any) -> None:
    answer = bench("thin", "wall-0p8.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["thin_spots"] == 0


def test_the_nozzle_is_a_parameter(bench: Any) -> None:
    assert bench("thin", "wall-0p8.step", "--nozzle", "1.0").verdict == "FAIL"


def test_a_floor_thinner_than_a_layer_is_caught(bench: Any) -> None:
    answer = bench("thin", "floor-0p1.step")
    assert answer.verdict == "FAIL", answer.text
    assert answer.numbers["thinnest_flat_mm"] == pytest.approx(0.1, abs=0.01)


def test_a_piece_too_small_to_print_is_caught(bench: Any) -> None:
    answer = bench("thin", "speck.step")
    assert answer.verdict == "FAIL", answer.text
    assert answer.numbers["bodies"] == 2
    assert answer.numbers["body_volumes_mm3"][0] == pytest.approx(0.125, abs=1e-3)


def test_a_sharp_point_is_named_and_isnt_a_thin_wall(bench: Any) -> None:
    answer = bench("thin", "frame-sharp.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["sharp_points"] >= 1
    assert "sharp point" in answer.text


# The pause layer


def test_the_pause_layer_from_the_heights(bench: Any) -> None:
    """A ceiling at z=3 on 0.2 mm layers: layer 15's top."""
    answer = bench("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2")
    assert answer.verdict == "OK", answer.text
    row = answer.numbers["openings"][0]
    assert row["pause_after_layer"] == 15
    assert row["pause_after_layer_faces"] == 15, "the second method"
    assert row["pause_top_z"] == pytest.approx(3.0)
    assert row["room_over_pane_mm"] == pytest.approx(0.0)
    assert "Nothing was sliced" in answer.text


def test_a_first_layer_of_a_different_height_shifts_the_count(bench: Any) -> None:
    answer = bench(
        "pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2", "--first-layer", "0.25"
    )
    # Layer mids are 0.125, then 0.35, 0.55 and on: 3.15 is the first over the ceiling, layer 16.
    assert answer.numbers["openings"][0]["pause_after_layer"] == 15
    assert answer.numbers["openings"][0]["pause_top_z"] == pytest.approx(3.05)


def test_a_pane_too_thick_for_the_pause_is_caught(bench: Any) -> None:
    answer = bench("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2.5")
    assert answer.verdict == "FAIL"
    assert "nozzle would hit" in answer.text


def test_a_pane_too_thin_for_the_pause_is_caught(bench: Any) -> None:
    answer = bench("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "1")
    assert answer.verdict == "FAIL"
    assert "bridges" in answer.text


def test_a_ceiling_between_layers_counts_from_the_layer_that_reaches_it(bench: Any) -> None:
    answer = bench("pause", "frame-between.step", "--pocket-z", "2", "--pane-thickness", "2.1")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["openings"][0]["pause_after_layer"] == 16


def test_a_ceiling_on_a_mid_height_pauses_at_the_earlier_and_says_so(bench: Any) -> None:
    answer = bench("pause", "frame-tie.step", "--pocket-z", "2", "--pane-thickness", "2")
    assert answer.numbers["openings"][0]["pause_after_layer"] == 15
    assert "could close it there" in answer.text


def test_nothing_closing_over_an_opening_needs_no_pause(bench: Any) -> None:
    answer = bench("pause", "frame-open.step", "--pocket-z", "2", "--pane-thickness", "2")
    assert answer.verdict == "OK"
    assert "no layer closes" in answer.text


def test_a_pane_with_no_floor_stands_on_the_plate(bench: Any) -> None:
    answer = bench("pause", "frame-bed.step", "--pocket-z", "2", "--pane-thickness", "2.8")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["openings"][0]["pause_after_layer"] == 15
    assert "build plate" in answer.text


# Renders


def test_renders_are_written_and_checked_against_arithmetic(bench: Any) -> None:
    answer = bench("render", "frame.step", "--section", "2")
    assert answer.verdict == "OK", answer.text
    for view in ("iso", "top", "front", "side", "section-z2", "sheet"):
        assert (bench.work / f"renders/frame-{view}.png").stat().st_size > 1000
    assert answer.numbers["views"]["top"]["projected_mm"] == [120.0, 60.0]
    assert answer.numbers["views"]["top"]["outline_differs"] < 0.03


def test_a_long_flat_part_isnt_clipped_in_any_view(bench: Any) -> None:
    """A fixed zoom ran the iso view off the picture, and the check caught it."""
    views = bench("render", "frame.step").numbers["views"]
    for facts in views.values():
        assert max(facts["painted_px"]) < 768


def test_an_unknown_view_is_no_answer(bench: Any) -> None:
    assert bench("render", "frame.step", "--views", "underneath").verdict == "NO ANSWER"


def test_a_renders_check_catches_a_wrong_picture(bench: Any) -> None:
    done = host.run(["planted_render.py"], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["right"] is None
    assert "nothing was drawn" in found["blank"]
    assert "outline" in found["wrong_shape"]
    assert "edge" in found["clipped"]


# The parameter check: a Fusion design reported healthy with its parameter driving part of it


def test_a_parameter_driving_only_part_of_a_design_shows_which(bench: Any) -> None:
    """Like `BaseCircle` on the Fusion copy: the centre follows, the outer ring and motif don't."""
    answer = bench("params", "design.py", "--set", "BaseCircle=25")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["pieces_moved"] == 1
    assert answer.numbers["pieces_not_moved"] == 3
    assert "and 3 didn't" in answer.text
    moved = [r for rows in answer.numbers["files"].values() for r in rows if r["moved"]]
    assert "radii [20.0] to [25.0]" in moved[0]["moved"]
    assert any(word.startswith("size x ") for word in moved[0]["moved"])
    assert any(word.startswith("volume ") for word in moved[0]["moved"])


def test_a_parameter_that_drives_nothing_shows_up_at_once(bench: Any) -> None:
    answer = bench("params", "design.py", "--set", "Unused=9")
    assert answer.verdict == "NO CHANGE" and answer.code == 1
    assert "drives nothing" in answer.text
    assert answer.numbers["pieces_moved"] == 0


def test_a_feature_that_moved_with_no_figure_changing_is_seen_by_the_second_check(
    bench: Any,
) -> None:
    """A hole slid along a plate: same size, same volume, same radii, same heights."""
    answer = bench("params", "design.py", "--set", "HolePlace=4")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["pieces_moved"] == 1
    assert "no figure changing" in answer.text


def test_a_parameter_driving_everything_says_so(bench: Any) -> None:
    answer = bench("params", "design.py", "--set", "Thickness=4")
    assert answer.verdict == "OK"
    assert answer.numbers["pieces_not_moved"] == 0


def test_a_script_that_moves_on_its_own_cant_be_read(bench: Any) -> None:
    answer = bench("params", "design-random.py", "--set", "Size=11")
    assert answer.verdict == "DISAGREE"
    assert "nothing changed" in answer.text


def test_a_script_that_fails_at_the_new_value_says_how(bench: Any) -> None:
    answer = bench("params", "design-fails.py", "--set", "Size=25")
    assert answer.verdict == "NO ANSWER"
    assert "too big for the frame" in answer.text


def test_a_parameter_assigned_twice_is_refused(bench: Any) -> None:
    answer = bench("params", "design-twice.py", "--set", "Size=15")
    assert answer.verdict == "NO ANSWER"
    assert "2 times" in answer.text


def test_a_name_that_isnt_a_parameter_lists_those_that_are(bench: Any) -> None:
    answer = bench("params", "design.py", "--set", "Nope=4")
    assert answer.verdict == "NO ANSWER"
    assert "BaseCircle" in answer.text and "Thickness" in answer.text
    assert "derived" not in answer.text, "a calculated name isn't a parameter"


def test_a_script_that_writes_nothing_is_no_answer(bench: Any) -> None:
    assert bench("params", "design-nothing.py", "--set", "Size=15").verdict == "NO ANSWER"


def test_the_design_script_itself_is_left_as_it_was(bench: Any) -> None:
    before = (bench.work / "design.py").read_text()
    bench("params", "design.py", "--set", "BaseCircle=25")
    assert (bench.work / "design.py").read_text() == before
    assert not (bench.work / "design.step").exists(), "its runs stay in scratch"
