"""The bench on planted cases, in its image with no network. Needs `scripts/bench.py build`.

The map's "Never take a tool's word" table is the spec: each row that touches these commands
is a case here that the command must catch, named after the row. Everything's made by
`bench_cases/make_fixtures.py`, and right answers come from arithmetic.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def png_size(path: Path) -> Iterator[tuple[int, int]]:
    """A PNG's width and height, from its header: nothing here has an image library."""
    head = path.read_bytes()[16:24]
    yield int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")


def legend_px(files: int, width: int) -> int:
    """The legend's height under a sheet, as `render` draws it: a line for each few files."""
    return 18 * -(-files // max(1, width // 440)) + 8


def only_pair(answer: Answer) -> dict[str, Any]:
    pairs = answer.numbers["pairs"]
    assert len(pairs) == 1
    return dict(pairs[0])


# How every answer runs


def test_a_sheet_of_variants_has_a_row_for_each_and_checks_each_picture(bench: Any) -> None:
    answer = bench(
        "render",
        "--variant",
        "A=block.step",
        "--variant",
        "B=block.step,block.step",
        "--views",
        "iso",
        "top",
        "--size",
        "256",
    )
    assert answer.verdict == "OK"
    assert sorted(answer.numbers["views"]) == ["A iso", "A top", "B iso", "B top"]
    assert len(answer.record["second_check"]) == 4
    assert answer.record["files"][-1].endswith("variants-A-B-sheet.png")
    with png_size(bench.work / answer.record["files"][-1]) as size:
        assert size == (512, 512 + legend_px(1, 512)), "two rows, and the legend"


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("render",), "name a file"),
        (("render", "block.step", "--variant", "A=block.step"), "not both"),
        (("render", "--variant", "A"), "NAME=FILE"),
        (("render", "--variant", "A=block.step", "--variant", "A=block.step"), "two variants"),
    ],
)
def test_a_render_that_cant_be_read_says_so(bench: Any, args: tuple[str, ...], says: str) -> None:
    answer = bench(*args)
    assert answer.verdict == "NO ANSWER"
    assert says in answer.record["why"]


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
        ("capture", "frame.step", "--pocket-z", "2", "--clearance", "0.1", "--pane-thickness", "2"),
        ("thin", "wall-0p8.step"),
        ("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2"),
        ("wall", "hang.step", "--hole", "50,121.5,2"),
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


@pytest.mark.parametrize("command", ["capture", "pause"])
def test_a_height_on_a_level_face_is_refused_with_the_pockets_middle(
    bench: Any, command: str
) -> None:
    """On `cup-1` the model and the operator both gave the pocket's floor. The refusal says the
    middle either side, and how many openings each has: the pocket runs from 1 to 3."""
    answer = bench(
        command,
        "frame.step",
        "--pocket-z",
        "1",
        "--pane-thickness",
        "2",
        *(("--clearance", "0.1") if command == "capture" else ()),
    )
    assert answer.verdict == "NO ANSWER"
    assert "above it, z=2 is midway between the faces at 1 and 3 (4 opening(s) there)" in (
        answer.text
    )
    assert "below it, z=0.5 is midway between the faces at 0 and 1" in answer.text


def test_panes_from_an_opening_with_an_island_have_a_hole_round_it(bench: Any) -> None:
    """A 60 by 40 mm opening less a 20 by 16 mm island, at 0.1 mm of play: 59.8 x 39.8 less
    20.2 x 16.2, by arithmetic. The window over it is split by the island's lip and two bridges."""
    answer = bench(
        "panes",
        "island/frame-island.step",
        "--pocket-z",
        "2",
        "--lip-z",
        "3.5",
        "--clearance",
        "0.1",
    )
    assert answer.verdict == "OK", answer.text
    assert "opening 1 has 1 island(s) of frame inside it" in answer.text
    pane = answer.numbers["panes"][0]
    assert pane["holes"] == 1
    assert pane["pane_mm2"] == pytest.approx(59.8 * 39.8 - 20.2 * 16.2, abs=0.01)
    assert pane["gap_3d_mm"] == pytest.approx(0.1, abs=0.005)
    assert pane["overlap_3d_mm3"] == pytest.approx(0.0, abs=1e-3)
    assert pane["window_mm2"] == pytest.approx(56 * 36 - 24 * 20 - 2 * 8 * 2, abs=0.01)
    assert pane["no_lip_mm"] == 0 and pane["no_lip_rays_mm"] == 0
    assert any(f.endswith("pane-1.step") for f in answer.record["files"]), "with its hole"


# Capture: held by the frame's shape, on both faces, with nothing depending on a bond


def capture(bench: Any, frame: str, *more: str, thick: str = "2") -> Answer:
    answer: Answer = bench(
        "capture", frame, "--pocket-z", "2", "--clearance", "0.1", "--pane-thickness", thick, *more
    )
    return answer


def test_a_pane_held_on_both_faces_says_by_how_much_two_ways(bench: Any) -> None:
    """A 39.8 mm pane under a 36 mm window, each way: (39.8 - 36) / 2 of lip, by arithmetic."""
    answer = capture(bench, "held.step")
    assert answer.verdict == "OK", answer.text
    pane = answer.numbers["panes"][0]
    for face in (pane["up"], pane["down"]):
        assert face["least_lip_mm"] == pytest.approx(1.9, abs=1e-3)
        assert face["rays"]["least_lip_mm"] == pytest.approx(1.9, abs=0.03)
        assert face["least_lip_with_play_mm"] == pytest.approx(1.8, abs=1e-3)
        assert face["window_mm2"] == pytest.approx(36 * 36, abs=0.1)
        assert face["lip_thick_mm"] == pytest.approx(1.0, abs=1e-3)
        assert face["lip_thick_sections_mm"] == pytest.approx(1.0, abs=2e-3)
        assert face["passes_at_shifts"] == face["passes_3d_at_shifts"] == 0
        assert face["least_in_path_mm3"] > 0
    assert answer.numbers["least_lip_mm"] == pytest.approx(1.9, abs=1e-3)
    assert pane["overlap_3d_mm3"] == pytest.approx(0.0, abs=1e-3)


def test_nothing_in_a_capture_answer_rests_on_a_bond(bench: Any) -> None:
    text = capture(bench, "held.step").text
    assert "nothing here reads a material or counts on a bond" in text
    assert "adhesi" not in text.lower()


@pytest.mark.parametrize(
    ("frame", "thick", "way", "face"),
    [("frame-open.step", "2", "upward", "up"), ("frame-bed.step", "2.8", "downward", "down")],
)
def test_a_pane_with_nothing_over_one_face_comes_out_that_way(
    bench: Any, frame: str, thick: str, way: str, face: str
) -> None:
    answer = capture(bench, frame, thick=thick)
    assert answer.verdict == "FAIL", answer.text
    assert f"comes out {way}" in answer.text
    read = answer.numbers["panes"][0][face]
    assert read["passes_at_shifts"] == read["shifts_read"], "at every place its play lets it sit"
    assert read["passes_3d_at_shifts"] == read["shifts_read"], "and as a solid"
    assert read["no_lip_mm"] == pytest.approx(159.2, abs=1)


def test_a_lip_narrower_than_the_play_lets_the_pane_through(bench: Any) -> None:
    """A 0.05 mm lip in the pocket, and 0.1 mm of play: a section of the pocket would say it's
    there, and the pane is smaller than the window."""
    answer = capture(bench, "held-through.step")
    assert answer.verdict == "FAIL", answer.text
    assert "comes out upward" in answer.text
    assert answer.numbers["panes"][0]["up"]["passes_3d_at_shifts"] >= 1


def test_a_pane_that_only_passes_once_its_play_has_moved_it_comes_out(bench: Any) -> None:
    answer = capture(bench, "held-shifted.step")
    assert answer.verdict == "FAIL", answer.text
    read = answer.numbers["panes"][0]["up"]
    assert 0 < read["passes_at_shifts"] < read["shifts_read"], "centred, it's held"
    assert 0 < read["passes_3d_at_shifts"] < read["shifts_read"], "by the second method too"
    assert read["least_in_path_mm3"] == pytest.approx(0.0, abs=0.5)


def test_a_lip_along_one_side_only_lets_it_swing_out(bench: Any) -> None:
    answer = capture(bench, "held-hinge.step")
    assert answer.verdict == "FAIL", answer.text
    assert "swing out upward" in answer.text
    read = answer.numbers["panes"][0]["up"]
    assert read["passes_at_shifts"] == 0, "it can't slide out"
    assert read["surrounds"] is False and read["rays"]["surrounds"] is False


def test_a_thin_stretch_of_lip_is_named_with_where_it_is(bench: Any) -> None:
    answer = capture(bench, "held-thin.step")
    assert answer.verdict == "FAIL", answer.text
    read = answer.numbers["panes"][0]["up"]
    assert read["least_lip_mm"] == pytest.approx(0.1, abs=1e-3)
    assert read["rays"]["least_lip_mm"] == pytest.approx(0.1, abs=0.03)
    assert read["thin_mm"] == pytest.approx(36, abs=2), "the one long edge"
    assert read["thin_at"][0] == pytest.approx(19.9, abs=0.01), "on the right-hand edge"
    assert answer.numbers["panes"][0]["down"]["thin_mm"] == 0
    # The same frame with a smaller limit holds.
    assert capture(bench, "held-thin.step", "--min-lip", "0").verdict == "OK"


def test_a_lip_thinner_than_a_layer_is_caught(bench: Any) -> None:
    answer = capture(bench, "held-thin-lip.step")
    assert answer.verdict == "FAIL", answer.text
    read = answer.numbers["panes"][0]["up"]
    assert read["lip_thick_mm"] == pytest.approx(0.1, abs=1e-3)
    assert read["air_mm"] == pytest.approx(0.9, abs=1e-3), "the pocket's taller than the pane"


def test_a_lip_that_stops_part_way_round_is_reported_and_its_end_isnt_thin(bench: Any) -> None:
    """26 mm of edge with no lip, and the rest of it goes round: held, and said so."""
    answer = capture(bench, "frame.step")
    assert answer.verdict == "OK", answer.text
    open_edge = answer.numbers["panes"][1]["up"]
    assert open_edge["no_lip_mm"] == pytest.approx(26.0, abs=1)
    assert open_edge["rays"]["no_lip_mm"] == pytest.approx(26.0, abs=1)
    assert open_edge["least_lip_mm"] == pytest.approx(1.9, abs=1e-3), "not the lip's falling end"
    assert open_edge["thin_mm"] == 0 and open_edge["surrounds"] is True
    assert "may be deliberate" in answer.text


def test_a_plain_hole_isnt_read_as_a_pane(bench: Any) -> None:
    answer = capture(bench, "frame.step")
    assert len(answer.numbers["panes"]) == 3
    assert answer.numbers["plain_holes"][0]["mm2"] == pytest.approx(16.0, abs=0.01)


def test_a_sloped_lip_is_read_at_its_narrowest_opening(bench: Any) -> None:
    """Sloped back at 45 degrees, from 40 mm at the ceiling to 38 at the top: its section at any
    one height would read a lip of its own, and what holds the pane is the narrowest."""
    answer = capture(bench, "held-sloped.step")
    assert answer.verdict == "OK", answer.text
    read = answer.numbers["panes"][0]["up"]
    assert read["least_lip_mm"] == pytest.approx(0.9, abs=1e-3)
    assert read["rays"]["least_lip_mm"] == pytest.approx(0.9, abs=0.03)


@pytest.mark.parametrize("frame", ["held-round.step", "held-round.stl"])
def test_a_round_pane_reads_the_same_from_a_step_and_a_mesh(bench: Any, frame: str) -> None:
    answer = capture(bench, frame)
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["least_lip_mm"] == pytest.approx(1.9, abs=2e-3)
    read = answer.numbers["panes"][0]["down"]
    assert read["window_mm2"] == pytest.approx(3.14159265 * 18**2, abs=0.5), "less a mesh's chords"


def test_a_pane_too_thick_for_its_pocket_is_in_the_frame(bench: Any) -> None:
    answer = capture(bench, "held.step", thick="2.5")
    assert answer.verdict == "FAIL", answer.text
    assert "too thick" in answer.text
    assert answer.numbers["panes"][0]["overlap_3d_mm3"] > 100


def test_a_pane_whose_height_misses_the_pocket_is_told_so(bench: Any) -> None:
    answer = capture(bench, "held.step", "--pane-floor-z", "2.5")
    assert answer.verdict == "FAIL", answer.text
    assert "misses --pocket-z" in answer.text


def test_a_pane_count_a_need_asks_for_is_held(bench: Any) -> None:
    """4 panes read against 5 files exited 0: a hole had joined two openings."""
    answer = capture(bench, "held.step", "--panes", "1")
    assert answer.verdict == "OK", answer.text
    wrong = capture(bench, "held.step", "--panes", "2")
    assert wrong.verdict == "FAIL" and wrong.code == 1
    assert "1 pane(s) read, and 2 were asked for" in wrong.text


def test_capture_on_no_opening_is_no_answer(bench: Any) -> None:
    answer = bench(
        "capture", "cube.step", "--pocket-z", "5", "--clearance", "0.1", "--pane-thickness", "2"
    )
    assert answer.verdict == "NO ANSWER", answer.text


def test_capture_says_so_when_its_two_methods_differ(bench: Any) -> None:
    done = host.run(["planted_capture.py"], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["alike"] is None
    assert "by rays" in found["rays_thinner"]
    assert "no lip" in found["rays_more_open"]
    assert "goes round" in found["surrounds"]
    assert "sections only" in found["rays_none"]
    assert found["rays_wider_is_natural"] is None
    assert found["ramp"] == {"least": 1.9, "thin": 0.0}
    assert found["slant"] == {"least": 1.9, "thin": 0.0}
    assert found["taper"]["thin"] > 2, "a lip that tapers a long way is thin, not its end"
    assert found["taper"]["least"] < 0.3


# An island of frame inside an opening: a pane with holes (`cup-1`)


def test_a_pane_with_a_hole_round_an_island_is_held_and_read_two_ways(bench: Any) -> None:
    """Read as one solid pane, it was "in the frame by 253 mm³: too thick for its pocket", the
    island's volume. Every lip is 1.9 mm with 0.1 mm of play, round the island's too."""
    answer = capture(bench, "island/frame-island.step", "--panes", "2")
    assert answer.verdict == "OK", answer.text
    assert "opening 1 has 1 island(s) of frame inside it" in answer.text
    assert "its pane is read with a hole round each" in answer.text
    pane = answer.numbers["panes"][0]
    assert pane["holes"] == 1
    assert pane["overlap_3d_mm3"] == pytest.approx(0.0, abs=1e-3)
    assert pane["pane_mm2"] == pytest.approx(59.8 * 39.8 - 20.2 * 16.2, abs=0.01)
    for face in (pane["up"], pane["down"]):
        assert face["least_lip_mm"] == pytest.approx(1.9, abs=1e-3)
        assert face["rays"]["least_lip_mm"] == pytest.approx(1.9, abs=0.03)
        assert face["no_lip_mm"] == 0 and face["rays"]["no_lip_mm"] == 0
        assert face["passes_at_shifts"] == face["passes_3d_at_shifts"] == 0
        assert face["surrounds"] and face["rays"]["surrounds"]


def test_each_pane_says_where_it_is_and_which_file_it_is(bench: Any) -> None:
    """The bench numbers the larger opening first, so its pane 1 is `pane_2.step`: said with its
    centre and size, and matched by outline and by a point inside it."""
    answer = capture(bench, "island/frame-island.step")
    one, two = answer.numbers["panes"]
    assert one["file"] == "pane_2.step" and two["file"] == "pane_1.step"
    assert one["file_share"] == pytest.approx(1.0, abs=1e-3) and one["file_holds_point"]
    assert one["centre"] == [0.0, 0.0] and one["size_mm"] == [59.8, 39.8]
    assert two["size_mm"] == [15.8, 11.8]
    assert "pane 1 (at 0,0, 59.8 x 39.8 mm, 1 hole(s), pane_2.step)" in answer.text
    assert "pane numbers are the bench's own" in answer.text


def test_a_pane_no_file_matches_says_so(bench: Any) -> None:
    answer = capture(bench, "held.step")
    assert answer.numbers["panes"][0]["file"] is None
    assert "no file beside the frame is it" in answer.text


def test_an_island_too_near_the_edge_for_the_play_is_gone_round_open_and_said(bench: Any) -> None:
    """0.15 mm from the edge, with 0.1 mm of play each side: no pane passes between, so it wraps
    the island as a C, as `cup-1`'s first round did, and is held."""
    answer = capture(bench, "frame-island-open.step")
    assert answer.verdict == "OK", answer.text
    assert "goes round 1 of them open, not with a hole" in answer.text
    assert "0.150 mm from another edge at 29.85" in answer.text
    assert answer.numbers["panes"][0]["holes"] == 0


def test_an_island_a_pane_cant_go_round_is_named(bench: Any) -> None:
    """Within twice the play of the edge top and bottom: the pane comes apart in two, and the
    bench says so, by name, rather than read either piece."""
    answer = capture(bench, "frame-island-split.step")
    assert answer.verdict == "FAIL", answer.text
    assert "opening 1 (at 0.00,0.00, 60.00 x 40.00 mm) leaves 2 piece(s) of pane" in answer.text
    assert "it has 1 island(s) inside it" in answer.text


def test_frame_standing_in_a_panes_place_is_said_with_where(bench: Any) -> None:
    """A pane set below its pocket's floor meets the frame at the bottom only."""
    answer = capture(bench, "held.step", "--pane-floor-z", "0.5", thick="2")
    assert answer.verdict == "FAIL", answer.text
    assert "it's set below the pocket's floor" in answer.text
    assert answer.numbers["panes"][0]["overlap_at"][1][2] == pytest.approx(1.0, abs=0.02)


def test_the_pause_over_a_pane_with_a_hole_is_read_by_both_methods(bench: Any) -> None:
    """Sections read the hole round one point of the opening, which an island could stand on: on
    `cup-1` that was a ceiling mid-pocket, at layer 15 against 21 by the flat faces."""
    answer = bench(
        "pause",
        "island/frame-island.step",
        "--pocket-z",
        "2",
        "--pane-thickness",
        "1.8",
        "--min-room",
        "0.2",
    )
    assert answer.verdict == "OK", answer.text
    assert "opening 1 has 1 island(s) of frame inside it" in answer.text
    rows = answer.numbers["openings"]
    assert [r["islands"] for r in rows] == [1, 0]
    for row in rows:
        assert row["pause_after_layer"] == row["pause_after_layer_faces"] == 15
        assert row["room_over_pane_mm"] == pytest.approx(0.2, abs=1e-4)


def test_a_section_draws_the_island_inside_an_opening(bench: Any) -> None:
    """80 x 60 less the 60 x 40 opening, plus a 20 x 16 island less its 16 x 12 pocket."""
    answer = bench("render", "island/frame-island.step", "--views", "top", "--section", "2")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["sections"] == [{"z": 2.0, "pieces": 2, "islands": 1, "mm2": 2528.0}]
    assert "1 of them island(s) inside an opening, drawn: 128.0 mm²" in answer.text
    drawn = re.search(r"section z=2: 2528.0 mm² by the mesh, ([\d.]+) mm² by pixels", answer.text)
    assert drawn and float(drawn.group(1)) == pytest.approx(2528, rel=0.02)


def test_a_section_drawing_that_drops_an_island_is_caught_and_the_piece_named(bench: Any) -> None:
    done = host.run(["planted_section.py"], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["pieces"] == 2
    assert found["now"] == []
    assert found["old_way"] == ["a 128.0 mm² piece at -9.00,3.00"]


# The wall round a hole


def test_the_wall_round_a_hanging_hole_is_read_two_ways(bench: Any) -> None:
    """A 3 mm hole at y=121.5 in a rail from 117 to 125: 2.0 mm to the outside by arithmetic,
    3.0 to the window. `thin`'s thinnest flat was quoted for this on `cup-1`."""
    answer = bench("wall", "hang.step", "--hole", "50,121.5,2", "--min-wall", "2")
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert numbers["least_wall_mm"] == pytest.approx(2.0, abs=1e-3)
    assert numbers["least_wall_rays_mm"] == pytest.approx(2.0, abs=0.01)
    assert numbers["least_wall_toward"] == "the part's outside"
    assert numbers["least_wall_from"][:2] == pytest.approx([50.0, 123.0], abs=0.01)
    assert numbers["hole_fit_d_mm"] == pytest.approx(3.0, abs=0.01)
    assert numbers["hole_from"] == pytest.approx(0.1) and numbers["hole_to"] == pytest.approx(3.9)


def test_any_point_inside_the_hole_names_it(bench: Any) -> None:
    """Not its centre: `rays` wanted the axis, and on `cup-1` the model couldn't aim it."""
    answer = bench("wall", "hang.step", "--hole", "49,122.3,3.95")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["least_wall_mm"] == pytest.approx(2.0, abs=1e-3)


def test_a_wall_under_what_a_need_asks_fails_and_says_toward_what(bench: Any) -> None:
    answer = bench("wall", "hang-thin.step", "--hole", "50,121.5,2", "--min-wall", "2")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert answer.numbers["least_wall_mm"] == pytest.approx(1.0, abs=1e-3)
    assert answer.numbers["least_wall_rays_mm"] == pytest.approx(1.0, abs=0.01)
    assert (
        answer.numbers["least_wall_toward"] == "the opening round 50.00,64.50 (80.00 x 109.00 mm)"
    )


def test_the_wall_round_a_hole_along_another_axis(bench: Any) -> None:
    answer = bench("wall", "bore-y.step", "--hole", "10,15,5", "--axis", "y")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["least_wall_mm"] == pytest.approx(3.5, abs=1e-3)
    assert answer.numbers["least_wall_rays_mm"] == pytest.approx(3.5, abs=0.01)
    assert answer.numbers["hole_from"] == pytest.approx(0.1) and answer.numbers[
        "hole_to"
    ] == pytest.approx(29.9)


def test_a_least_wall_between_corners_is_a_disagreement_said_why(bench: Any) -> None:
    """1.414 mm corner to corner, by arithmetic, and no ray square to the hole's wall crosses it."""
    answer = bench("wall", "corners.step", "--hole", "6,6,2")
    assert answer.verdict == "DISAGREE" and answer.code == 2, answer.text
    assert answer.numbers["least_wall_mm"] == pytest.approx(2**0.5, abs=1e-3)
    assert "isn't square to the hole, as between two corners" in answer.text


@pytest.mark.parametrize(
    ("point", "says"), [("5,5,2", "it's in material"), ("50,130,2", "open to the outside")]
)
def test_a_point_not_in_a_closed_hole_is_no_answer(bench: Any, point: str, says: str) -> None:
    answer = bench("wall", "hang.step", "--hole", point)
    assert answer.verdict == "NO ANSWER", answer.text
    assert says in answer.text


# Thin features and small pieces


def test_several_bodies_fail_when_a_need_asks_for_one(bench: Any) -> None:
    """3 bodies passed "one connected solid"."""
    answer = bench("thin", "two-bodies.step")
    assert answer.verdict == "OK" and answer.numbers["bodies"] == 2, answer.text
    asked = bench("thin", "two-bodies.step", "--one-body")
    assert asked.verdict == "FAIL" and asked.code == 1, asked.text
    assert "2 separate bodies, and one was asked for" in asked.text
    assert "OCCT has 2 solid(s)" in asked.text, "the second method"
    assert bench("thin", "one-body.step", "--one-body").verdict == "OK"


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


def test_a_cylinder_on_its_side_isnt_thin_at_its_top(bench: Any) -> None:
    """A level ray there crosses a tiny chord, in a strip no layer prints: five reference parts
    read falsely as thin this way."""
    answer = bench("thin", "lying.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["thin_rays_not_at_a_point"] == 0


def test_a_wire_on_its_side_is_thin_all_the_same(bench: Any) -> None:
    answer = bench("thin", "wire.step", "--layer", "0.1")
    assert answer.verdict == "FAIL", answer.text
    assert answer.numbers["thinnest_across_mm"] < 0.4


def test_a_cusp_where_a_curve_meets_a_flat_is_noted_and_isnt_thin(bench: Any) -> None:
    answer = bench("thin", "cusped.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["thin_spots"] == 0


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


def test_a_pane_with_no_room_over_it_fails_when_a_need_asks_for_room(bench: Any) -> None:
    """0.0 mm under the next layer exited 0 where a need said a 0.2 mm margin."""
    args = ("pause", "frame.step", "--pocket-z", "2", "--pane-thickness")
    answer = bench(*args, "2", "--min-room", "0.2")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert "under the 0.2 mm asked for" in answer.text
    assert answer.numbers["least_room_over_pane_mm"] == pytest.approx(0.0)
    held = bench(*args, "1.8", "--min-room", "0.2")
    assert held.verdict == "OK", held.text
    assert held.numbers["least_room_over_pane_mm"] == pytest.approx(0.2)
    assert bench(*args, "2").verdict == "OK", "with nothing asked, a ceiling is still all it reads"


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


def test_a_pane_standing_above_the_next_layer_is_a_fail_by_both_methods(bench: Any) -> None:
    """The next layer's nozzle runs at z=3.2 over a pause at z=3, and this pane stands to 3.5."""
    answer = bench("pause", "frame.step", "--pocket-z", "2", "--pane-thickness", "2.5")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert "nozzle would hit" in answer.text
    row = answer.numbers["openings"][0]
    assert row["pause_after_layer"] == row["pause_after_layer_faces"] == 15, "the methods agree"
    assert row["pane_top_z"] == pytest.approx(3.5)
    assert row["nozzle_over_pane_mm"] == pytest.approx(-0.3)


@pytest.mark.parametrize(
    ("thick", "verdict"), [("2", "OK"), ("2.0005", "OK"), ("2.002", "FAIL"), ("2.2", "FAIL")]
)
def test_a_pane_above_the_pause_by_more_than_the_mesh_can_say_is_a_fail(
    bench: Any, thick: str, verdict: str
) -> None:
    """Above the pause and under the nozzle's height still squeezes the next layer into it."""
    answer = bench("pause", "frame.stl", "--pocket-z", "2", "--pane-thickness", thick)
    assert answer.verdict == verdict, answer.text


def test_a_pane_too_tall_on_the_plate_is_a_fail(bench: Any) -> None:
    answer = bench("pause", "frame-bed.step", "--pocket-z", "2", "--pane-thickness", "3.5")
    assert answer.verdict == "FAIL", answer.text
    assert "nozzle would hit" in answer.text


def test_one_pane_too_tall_among_several_is_a_fail(bench: Any) -> None:
    """Both openings close at the one layer, so the answer prints one line: the second's floor is
    higher, so the same pane stands proud of it alone."""
    answer = bench("pause", "frame-floors.step", "--pocket-z", "2", "--pane-thickness", "2")
    assert answer.verdict == "FAIL", answer.text
    assert "opening 2" in answer.record["why"] and "nozzle would hit" in answer.record["why"]
    tops = [row["pane_top_z"] for row in answer.numbers["openings"]]
    assert sorted(tops) == pytest.approx([3.0, 3.5])


def test_an_opening_the_first_layer_closes_has_no_layer_to_pause_after(bench: Any) -> None:
    """`tops[-1]`, the part's top, used to be read as the pause, so this could pass."""
    answer = bench("pause", "frame-first.step", "--pocket-z", "0.05", "--pane-thickness", "7.9")
    assert answer.verdict == "FAIL", answer.text
    assert "no layer to pause" in answer.record["why"]


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


def test_a_close_up_draws_a_3mm_hole_large_and_checks_it_against_arithmetic(bench: Any) -> None:
    """The first real run's 3 mm holes were 27 px on its sheet, too small to see they were wrong."""
    whole = bench("render", "plate-3mm.step", "--views", "top", "--size", "512")
    assert whole.verdict == "OK", whole.text
    answer = bench(
        "render", "plate-3mm.step", "--views", "--close", "0,0,2,4", "--size", "512",
        "--renders-out", "closes",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    close = answer.numbers["close_ups"][0]
    assert close["px_per_mm"] == 64.0 and close["across_mm"] == 8.0
    # All but the hole, of an 8 mm square: arithmetic, by the picture and by the triangles.
    wanted = 1 - 3.14159265 * 1.5**2 / 8**2
    assert close["painted_share"] == pytest.approx(wanted, abs=0.01)
    assert close["projected_share"] == pytest.approx(wanted, abs=0.01)
    assert close["differs"] == 0
    assert "second check: close 1" in answer.text
    # Across the hole's centre: 3 mm at 64 px per mm, against about 14 px on the whole sheet.
    done = host.run(
        ["read_picture.py", f"{close['file']}:row=256", "renders/plate-3mm-top.png:row=256"],
        bench.work,
        python=True,
    )
    assert done.returncode == 0, done.stderr
    near, far = json.loads(done.stdout)
    assert near == pytest.approx(192, abs=4)
    assert far < 20 < near


def test_a_close_up_off_the_part_says_nothing_is_there(bench: Any) -> None:
    answer = bench("render", "plate-3mm.step", "--views", "--close", "500,500,0,4")
    assert answer.verdict == "FAIL", answer.text
    assert "nothing of the parts" in answer.record["why"]


def test_a_point_left_of_the_origin_isnt_taken_for_an_option(bench: Any) -> None:
    assert bench("render", "plate-3mm.step", "--views", "--close", "-20,0,2,4").verdict == "OK"
    across = bench("rays", "block-bore-x.step", "--centre", "-1,20,10", "--axis", "x")
    assert "usage error" not in across.text


def test_a_close_up_looks_along_the_view_it_names(bench: Any) -> None:
    """A 20 mm window on the plate's 4 mm edge, from the front: a band across its middle."""
    answer = bench("render", "plate-3mm.step", "--views", "--close", "0,-50,0,10,front")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["close_ups"][0]["projected_share"] == pytest.approx(0.2, abs=0.01)
    assert answer.numbers["close_ups"][0]["painted_share"] == pytest.approx(0.2, abs=0.01)


@pytest.mark.parametrize(
    ("close", "says"),
    [
        ("0,0,2", "X,Y,Z,R"),
        ("a,0,2,4", "X,Y,Z,R"),
        ("0,0,2,0", "over nothing"),
        ("0,0,2,4,underneath", "no view underneath"),
    ],
)
def test_a_close_up_that_cant_be_read_says_so(bench: Any, close: str, says: str) -> None:
    answer = bench("render", "plate-3mm.step", f"--close={close}")
    assert answer.verdict == "NO ANSWER", answer.text
    assert says in answer.record["why"]


def test_a_close_ups_check_catches_a_wrong_picture(bench: Any) -> None:
    done = host.run(["planted_render.py"], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    found = json.loads(done.stdout)
    assert found["close_right"] is None
    assert "differs" in found["close_filled_hole"], "a hole drawn over"
    assert "differs" in found["close_wrong_place"], "a picture aimed elsewhere"
    assert "nothing of the parts" in found["close_blank"]


def test_a_sheet_of_variants_can_hold_a_close_up_of_each(bench: Any) -> None:
    answer = bench(
        "render", "--variant", "A=plate-3mm.step", "--variant", "B=block-seat.step",
        "--views", "top", "--close", "30,20,20,14", "--size", "256",
        "--renders-out", "with-close",
    )  # fmt: skip
    # Variant A's plate reaches 50 mm, and has nothing at z over the point: both are drawn.
    assert sorted(answer.numbers["views"]) == ["A close 1", "A top", "B close 1", "B top"]
    assert answer.verdict == "OK", answer.text
    with png_size(bench.work / answer.record["files"][-1]) as size:
        assert size == (512, 512 + legend_px(2, 512))


def test_a_test_piece_is_drawn_grey_and_marked_and_the_part_isnt(bench: Any) -> None:
    """The first real run's final sheet drew two test cylinders as if they were the part."""
    answer = bench(
        "render", "plate-3mm.step", "--test-piece", "plate-test-piece.step", "--views", "top",
        "--size", "512", "--renders-out", "marked",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["test_pieces"] == ["plate-test-piece.step"]
    assert "not the part" in answer.text
    # Plate to x=50, and the piece from 60 to 80, on a picture 130 x 1.08 mm across.
    scale = 512 / (130 * 1.08)
    plain = bench(
        "render", "plate-3mm.step", "--views", "top", "--size", "512", "--renders-out", "plain",
    )  # fmt: skip
    assert plain.numbers.get("test_pieces") is None
    # The picture's centre is at x=15: the plate at x=-20, and the piece at x=70.
    x_part = int(256 + (-20 - 15) * scale)
    x_piece = int(256 + (70 - 15) * scale)
    done = host.run(
        ["read_picture.py", f"marked/plate-3mm-top.png:{x_part},256",
         f"marked/plate-3mm-top.png:{x_piece},256"],
        bench.work,
        python=True,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    part, piece = json.loads(done.stdout)
    assert max(piece) - min(piece) <= 2, f"a test piece is grey: {piece}"
    assert piece[0] > 180, f"and see-through: {piece}"
    assert max(part) - min(part) > 20, f"the part keeps its colour: {part}"


def test_a_file_cant_be_the_part_and_a_test_piece(bench: Any) -> None:
    answer = bench("render", "block.step", "--test-piece", "block.step")
    assert answer.verdict == "NO ANSWER", answer.text
    assert "as the part and as a test piece" in answer.record["why"]


def test_a_sheet_of_variants_marks_a_test_piece_on_every_row(bench: Any) -> None:
    answer = bench(
        "render", "--variant", "A=block.step", "--variant", "B=block-seat.step",
        "--test-piece", "cube.step", "--views", "top", "--size", "256",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["test_pieces"] == ["cube.step"]


# The parameter check: a Fusion design reported healthy with its parameter driving part of it


def test_a_parameter_driving_only_part_of_a_design_shows_which(bench: Any) -> None:
    """Like `BaseCircle` on the Fusion copy: the centre follows, the outer ring and motif don't."""
    answer = bench("params", "design.py", "--set", "BaseCircle=25")
    assert answer.verdict == "PARTIAL" and answer.code == 1, answer.text
    assert answer.numbers["pieces_moved"] == 1
    assert answer.numbers["pieces_not_moved"] == 3
    assert len(answer.numbers["pieces_left"]) == 3
    assert "and 3 didn't" in answer.text
    moved = [r for rows in answer.numbers["files"].values() for r in rows if r["moved"]]
    assert "radii [20.0] to [25.0]" in moved[0]["moved"]
    assert any(word.startswith("size x ") for word in moved[0]["moved"])
    assert any(word.startswith("volume ") for word in moved[0]["moved"])


@pytest.mark.parametrize(("left", "verdict"), [("2", "PARTIAL"), ("3", "OK"), ("4", "OK")])
def test_a_need_can_say_how_many_pieces_may_stay_behind(
    bench: Any, left: str, verdict: str
) -> None:
    answer = bench("params", "design.py", "--set", "BaseCircle=25", "--max-left", left)
    assert answer.verdict == verdict, answer.text
    assert answer.numbers["pieces_not_moved"] == 3


def test_a_parameter_that_drives_nothing_shows_up_at_once(bench: Any) -> None:
    answer = bench("params", "design.py", "--set", "Unused=9")
    assert answer.verdict == "NO CHANGE" and answer.code == 1
    assert "drives nothing" in answer.text
    assert answer.numbers["pieces_moved"] == 0


def test_a_feature_that_moved_with_no_figure_changing_is_seen_by_the_second_check(
    bench: Any,
) -> None:
    """A hole slid along a plate: same size, same volume, same radii, same heights."""
    answer = bench("params", "design.py", "--set", "HolePlace=4", "--max-left", "3")
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


# A sheet shows what it's asked about (mqucifer/print-shop#26)


def pixels(bench: Any, *where: str) -> list[Any]:
    done = host.run(["read_picture.py", *where], bench.work, python=True)
    assert done.returncode == 0, done.stderr
    return list(json.loads(done.stdout))


def test_each_file_is_drawn_in_the_colour_given_and_named_in_the_legend(bench: Any) -> None:
    """On `cup-1` a cycle of 5 colours drew a pane in the frame's, and nothing said which."""
    answer = bench(
        "render", "glass-frame.step", "glass-pane-1.step", "glass-pane-2.step", "--views", "top",
        "--look", "glass-frame.step=grey:frame", "--look", "glass-pane-1.step=blue:cup",
        "--look", "glass-pane-2.step=amber:steam", "--size", "512", "--renders-out", "looks",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    assert "drawn: 2 glass-pane-1.step: cup (blue)" in answer.text
    files = {f["file"]: f for f in answer.numbers["files"]}
    assert files["glass-pane-1.step"]["rgb"] == [40, 90, 220]
    # 7.9 px a mm about (30, 20): a point on each pane, away from its number.
    cup, steam = pixels(bench, "looks/glass-frame-glass-pane-1-glass-pane-2-top.png:98,319",
                        "looks/glass-frame-glass-pane-1-glass-pane-2-top.png:312,319")  # fmt: skip
    assert cup[2] > cup[0] + 60, f"blue: {cup}"
    assert steam[0] > steam[2] + 60, f"amber: {steam}"
    # Every file was seen in the view drawn, so nothing's said hidden.
    assert all(px > 0 for px in answer.numbers["seen_px"]["top"].values())
    assert "not seen" not in answer.text


def test_a_file_with_no_colour_never_takes_another_files(bench: Any) -> None:
    rows = [f"row-{k}.step" for k in range(1, 8)]
    answer = bench("render", *rows, "--views", "top", "--size", "256", "--renders-out", "seven")
    assert answer.verdict == "OK", answer.text
    drawn = [tuple(f["rgb"]) for f in answer.numbers["files"]]
    assert len(set(drawn)) == 7
    assert all(
        sum(abs(a - b) for a, b in zip(one, other, strict=True)) > 60
        for i, one in enumerate(drawn)
        for other in drawn[i + 1 :]
    ), drawn
    assert answer.record["files"][-1].endswith("-sheet.png"), "more than one file has a legend"


def test_a_file_no_view_shows_is_said_to_be_hidden(bench: Any) -> None:
    answer = bench("render", "outer.step", "hidden.step", "--views", "top", "iso", "--size", "256")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["seen_px"]["top"]["hidden.step"] == 0
    assert answer.numbers["seen_px"]["top"]["outer.step"] > 1000
    assert "not seen in any view drawn: hidden.step" in answer.text


def test_a_colour_the_sheet_cant_draw_is_no_answer(bench: Any) -> None:
    answer = bench("render", "outer.step", "--look", "outer.step=sparkly")
    assert answer.verdict == "NO ANSWER"
    assert "isn't a colour the sheet can draw" in answer.record["why"]
    answer = bench("render", "outer.step", "--look", "nothere.step=blue")
    assert answer.verdict == "NO ANSWER" and "isn't drawn here" in answer.record["why"]


def test_a_level_cut_draws_each_file_in_its_pocket_and_an_island_in_its_opening(
    bench: Any,
) -> None:
    """`render --section` dropped an island of frame inside an opening, and drew one file."""
    answer = bench(
        "render", "island-frame.step", "holed-pane.step", "--views", "top", "--cut", "z=2",
        "--look", "island-frame.step=grey", "--look", "holed-pane.step=green:background",
        "--size", "512", "--renders-out", "island",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    cut = answer.numbers["cuts"]["cut z=2"]
    # The ring, 40² less 30², and the 8 mm island; the pane, 29.6² less its 8.4 mm hole.
    assert cut["files"]["island-frame.step"]["area_mm2"] == pytest.approx(1600 - 900 + 64, abs=0.5)
    assert cut["files"]["island-frame.step"]["area_occt_mm2"] == pytest.approx(764, abs=0.5)
    assert cut["files"]["holed-pane.step"]["area_mm2"] == pytest.approx(29.6**2 - 8.4**2, abs=0.5)
    assert cut["overlaps"] == []
    # The island's middle is the frame's grey, drawn over the pane's opening: at (20, 20), the
    # picture's centre, which the numbers stay off.
    (island,) = pixels(bench, "island/island-frame-holed-pane-cut-z-2.png:256,280")
    assert max(island) - min(island) < 12 and island[0] < 200, f"grey: {island}"


def test_an_upright_cut_shows_a_pane_in_its_pocket_over_a_window_of_the_plane(bench: Any) -> None:
    answer = bench(
        "render", "glass-frame.step", "glass-pane-1.step", "--views", "--cut", "y=20,x=100:112",
        "--size", "512", "--renders-out", "upright",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    # The sheet's cases are 100 mm along x.
    cut = answer.numbers["cuts"]["cut y=20, x 100 to 112"]
    assert (cut["right"], cut["up"]) == ("x", "z")
    # Across the whole plane: the frame's 60 x 5 less the two pockets and windows; the pane 2.
    assert cut["files"]["glass-pane-1.step"]["area_mm2"] == pytest.approx(22.6 * 1.8, abs=0.05)
    # 12 mm across the picture: about 39 px a mm, so a 1.8 mm pane is 70 px tall.
    assert cut["px_per_mm"] == pytest.approx(512 / (12 * 1.1), rel=0.01)


def test_a_cut_says_where_two_files_take_the_same_space(bench: Any) -> None:
    answer = bench("render", "share-a.step", "share-b.step", "--views", "--cut", "z=1.5")
    assert answer.verdict == "OK", answer.text
    (overlap,) = answer.numbers["cuts"]["cut z=1.5"]["overlaps"]
    assert overlap["files"] == ["share-a.step", "share-b.step"]
    assert overlap["area_mm2"] == pytest.approx(100, abs=0.1)
    assert "overlap by 100.00 mm²" in answer.text


@pytest.mark.parametrize(
    ("cut", "says"),
    [
        ("w=3", "starts with x, y or z"),
        ("z=3,z=0:4", "a window is x="),
        ("x=50,y=4:2", "low to high"),
    ],
)
def test_a_cut_that_cant_be_read_says_so(bench: Any, cut: str, says: str) -> None:
    answer = bench("render", "outer.step", "--views", "--cut", cut)
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text


def test_a_sheet_of_variants_draws_cuts_and_colours_on_each_row(bench: Any) -> None:
    answer = bench(
        "render", "--variant", "A=glass-frame.step,glass-pane-1.step",
        "--variant", "B=glass-frame.step,glass-pane-2.step", "--views", "top", "--cut", "z=3",
        "--look", "glass-pane-1.step=red:one", "--size", "256",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    assert set(answer.numbers["cuts"]) == {"A cut z=3", "B cut z=3"}
    numbered = {f["file"]: f["number"] for f in answer.numbers["files"]}
    assert numbered == {"glass-frame.step": 1, "glass-pane-1.step": 2, "glass-pane-2.step": 3}
    with png_size(bench.work / answer.record["files"][-1]) as size:
        assert size == (512, 512 + legend_px(3, 512))


def test_the_overlap_helper_reads_what_two_files_share_by_two_methods(bench: Any) -> None:
    done = host.run(
        ["-I", "-c", "import sys; sys.path.insert(0, '/opt/bench'); from bench.overlap import "
         "main; main()", "share-a.step", "share-b.step", "outer.step", "row-7.step"],
        bench.work,
        python=True,
    )  # fmt: skip
    line = next(x for x in done.stdout.splitlines() if x.startswith("ROUNDS_OVERLAP "))
    found = json.loads(line[len("ROUNDS_OVERLAP ") :])
    pairs = {tuple(p["files"]): p for p in found["pairs"]}
    assert pairs[("share-a.step", "share-b.step")]["mesh_mm3"] == pytest.approx(300, abs=0.5)
    assert pairs[("share-a.step", "share-b.step")]["occt_mm3"] == pytest.approx(300, abs=0.01)
    # outer and share-a share 20 x 10 x 3, and row-7 is far from both.
    assert pairs[("share-a.step", "outer.step")]["mesh_mm3"] == pytest.approx(600, abs=0.5)
    assert not any("row-7.step" in p for p in pairs)
    assert found["files"]["outer.step"]["bounds_mm"][1] == pytest.approx([120, 20, 10], abs=0.01)


# Light through each pane, and each pane's thickness (mqucifer/print-shop#37): the stained-glass
# toolset's readings. In three runs light was measured by hand, and a floor went under panes.


def test_light_through_each_pane_is_read_two_ways_and_a_floor_under_one_fails(bench: Any) -> None:
    answer = bench(
        "light", "glass/frame.step", "glass/pane_a.step", "glass/pane_b.step",
        "glass/pane_c.step", "--min-open", "30",
    )  # fmt: skip
    assert answer.verdict == "FAIL", answer.text
    assert "glass/pane_b.step 0.0%" in answer.record["why"], "the pane with a floor is named"
    shares = {p["file"]: p for p in answer.numbers["panes"]}
    assert shares["glass/pane_a.step"]["open_pct"] == pytest.approx(100 / 132.25 * 100, abs=0.05)
    assert shares["glass/pane_c.step"]["open_pct"] == pytest.approx(4 / 12.25 * 100, abs=0.05)
    for one in shares.values():
        assert abs(one["open_pct"] - one["open_pct_rays"]) <= one["band_pct"]
    assert answer.numbers["least_open_file"] == "glass/pane_b.step"
    assert answer.numbers["narrowest_band_mm"] == pytest.approx(3.0, abs=0.01)


def test_pane_thickness_is_read_two_ways_and_held_to_the_limit_given(bench: Any) -> None:
    within = bench("thickness", "glass/pane_a.step", "glass/pane_ridge.step", "--max", "1.5")
    assert within.verdict == "OK", within.text
    rows = {r["file"]: r for r in within.numbers["files"]}
    assert rows["glass/pane_ridge.step"]["thickest_rays_mm"] == pytest.approx(1.5, abs=0.005)
    assert within.numbers["thinnest_mm"] == pytest.approx(1.0, abs=0.005)
    over = bench("thickness", "glass/pane_a.step", "glass/pane_thick.step", "--max", "1.2")
    assert over.verdict == "FAIL" and "glass/pane_thick.step 2.000 mm" in over.record["why"]
    assert over.numbers["thickest_mm"] == pytest.approx(2.0, abs=0.005)


def test_each_panes_rim_is_sized_to_keep_its_visible_share(bench: Any) -> None:
    """Criterion 6: the full rim where it keeps the share visible, the widest that does where it
    doesn't, and the pane removed where even the smallest can't."""
    share = ("--pocket-z", "2", "--clearance", "0.25", "--pane-thickness", "1",
             "--full-rim", "1.2", "--least-rim", "0.55", "--min-visible", "60")  # fmt: skip
    held = bench("capture", "rims/held.step", *share)
    assert held.verdict == "OK", held.text
    rims = {p["size_mm"][0]: p["rim"] for p in held.numbers["panes"]}
    assert rims[19.5]["needs_mm"] == 1.2
    assert rims[19.5]["visible_pct"] == pytest.approx(81.46, abs=0.1)
    assert rims[5.5]["needs_mm"] == pytest.approx(0.8699, abs=0.001)
    assert rims[5.5]["visible_pct"] == pytest.approx(60.0, abs=0.1)
    unheld = bench("capture", "rims/unheld.step", *share)
    assert unheld.verdict == "FAIL", unheld.text
    assert "set this pane's rim to 0.87 mm before play. It has 1.2 mm" in unheld.record["why"]
    assert "42.8% of it is visible, where 60% is wanted" in unheld.record["why"]
    assert "remove this pane. Even the smallest rim, 0.55 mm before play" in unheld.record["why"]
    done = host.run(["capture", "rims/held.step", *share[:-2]], bench.work)
    assert done.returncode == 3 and "no default for any of them" in done.stdout + done.stderr
