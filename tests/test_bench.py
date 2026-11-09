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
from print_shop.rounds import reports, skills

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
        ("edges", "block.step"),
        # mqucifer/print-shop#65's: a lifted file and edges by group and by place, held to the
        # shapes `needs` walks a path through (mqucifer/print-shop#66).
        ("clearance", "cube.step", "cube-gap-0p1.step", "--lift", "1"),
        ("edges", "block.step", "--box", "corner=-1,-1,-1,5,5,50", "--facing", "front=-y"),
        ("thickness", "cube.step"),
        ("light", "frame.step", "cube.step"),
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
    # Each number has the shape `needs` walks a path through (mqucifer/print-shop#66).
    assert not reports.unlike(args[0], answer.numbers), reports.unlike(args[0], answer.numbers)


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
    assert "over" not in pane["up"] and "over" not in pane["down"], "held: nothing to say why"
    assert "Over it" not in answer.text and "Under it" not in answer.text


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
    # Why: no frame beyond that face at all, with the heights it read.
    assert read["over"]["heights_read"] == 0
    side, end = ("top", "top") if face == "up" else ("floor", "bottom")
    assert f"nothing {'above' if face == 'up' else 'below'} the pane's {side}" in answer.text
    assert f"its {end} is z={read['over']['frame_end_z']:g}" in answer.text


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
    # Why, edge by edge: 0.1 mm of lip on the left, the pocket's own outline top and bottom, and
    # 0.5 mm wider than the pocket on the right.
    said = {s["what"]: s for s in read["over"]["stretches"] if s["spots"] > 1}
    assert said["thin"]["least_mm"] == pytest.approx(0.1, abs=1e-3)
    assert said["pocket"]["edge_mm"] == pytest.approx(39.8, abs=1)
    assert said["wider"]["beyond_wall_mm"] == pytest.approx(0.5, abs=1e-3)
    assert all(s["rays"]["what"] == s["what"] for s in said.values())


def test_a_pocket_cut_on_through_its_top_face_is_said_to_be_why(bench: Any) -> None:
    """`eval-tip-1`: the pocket's cut ran from its floor through the top face, and `capture` said
    only that the pane comes out. Its hole over the pane is the pocket's outline, all round."""
    answer = capture(bench, "held-pocket-through.step")
    assert answer.verdict == "FAIL", answer.text
    assert "comes out upward" in answer.text
    assert "the frame read from z=3 to its top at z=4: all round its edge, 159.2 mm" in answer.text
    assert "the hole is as wide as the pocket" in answer.text
    assert "the pocket's cut may run through the top face" in answer.text
    pane = answer.numbers["panes"][0]
    (stretch,) = pane["up"]["over"]["stretches"]
    assert stretch["what"] == "pocket" and stretch["whole_ring"]
    assert stretch["least_mm"] == pytest.approx(0.1, abs=1e-3), "out to the pocket's wall"
    assert stretch["rays"]["what"] == "pocket"
    assert stretch["rays"]["mm"] == pytest.approx(0.1, abs=0.01)
    assert "over" not in pane["down"], "held below"


def test_a_top_opening_wider_than_the_pocket_on_one_side_says_which_and_by_how_much(
    bench: Any,
) -> None:
    """2 mm wider than the pocket on the right, and a 0.2 mm lip over the pane on the others."""
    answer = capture(bench, "held-wide-side.step")
    assert answer.verdict == "FAIL", answer.text
    assert "the hole is 2 mm wider than the pocket, so the frame starts 2.1 mm out" in answer.text
    assert "the frame covers it by only 0.2 to 0.283 mm, where 0.5 is needed" in answer.text, (
        "0.2 along each edge, and 0.2 times the square root of 2 square to a corner"
    )
    said = {s["what"]: s for s in answer.numbers["panes"][0]["up"]["over"]["stretches"]}
    wider = said["wider"]
    assert wider["edge_mm"] == pytest.approx(39.8, abs=1)
    assert wider["middle"][0] == pytest.approx(19.9, abs=1e-3), "on the right-hand edge"
    assert wider["beyond_wall_mm"] == pytest.approx(2.0, abs=1e-3)
    assert wider["rays"]["what"] == "wider"
    assert wider["rays"]["mm"] == pytest.approx(2.1, abs=0.01)
    assert said["thin"]["edge_mm"] == pytest.approx(119.4, abs=1)


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
    assert found["over_alike"] is None
    assert "the sections read pocket and rays wider" in found["over_kind"]
    assert "the sections read 2.1 mm and rays 1.6" in found["over_figure"]
    assert found["over_lip_wider_by_rays"] is None, "beside a corner, as for the lip"
    assert "a 0.2 mm lip and rays 0.1" in found["over_lip_narrower_by_rays"]


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


def test_panes_whose_files_have_another_play_are_named(bench: Any) -> None:
    """One `--clearance` for two plays (`eval-choice-1`): the pane whose own file has 1.5 mm of
    play, by arithmetic, is named though it doesn't match at 0.1, and the reply says one run can't
    judge them all. At 1.5 the other is named."""
    answer = capture(bench, "plays/frame.step")
    differ = answer.numbers["plays_differ"]
    assert [(d["pane"], d["file"]) for d in differ] == [("2", "pane_2.step")], answer.text
    assert differ[0]["play_mm"] == pytest.approx(1.5, abs=0.005)
    assert differ[0]["by_points_mm"] == pytest.approx(1.5, abs=0.005)
    rows = {row["pane"]: row for row in answer.numbers["panes"]}
    assert rows["1"]["file"] == rows["1"]["play_file"] == "pane_1.step"
    assert rows["1"]["file_play_mm"] == pytest.approx(0.1, abs=0.005)
    assert rows["2"]["file"] is None and rows["2"]["play_file"] == "pane_2.step"
    assert "pane 2 (pane_2.step) has 1.5 mm" in answer.text
    said = "One run can't judge panes of different plays: run capture again at --clearance 1.5"
    assert said in answer.text
    assert answer.verdict != "DISAGREE", "a reading, which judges nothing"
    other = capture(bench, "plays/frame.step", "--clearance", "1.5")
    assert [d["file"] for d in other.numbers["plays_differ"]] == ["pane_1.step"], other.text


def test_info_reads_a_curve_stored_as_flat_strips_and_the_files_size(bench: Any) -> None:
    """Not a failure: a reading of what bloats a file (mqucifer/print-shop#54)."""
    faceted = bench("info", "ring-faceted.step")
    curved = bench("info", "ring-curved.step")
    assert faceted.verdict == curved.verdict == "OK", faceted.text + curved.text
    assert faceted.numbers["flat_faces"] == 770 and faceted.numbers["small_flat_faces"] == 768
    assert "flat faces under 1 mm by points along their edges: 768" in faceted.text
    assert "768 flat face(s) under 1 mm across" in faceted.text
    assert curved.numbers["flat_faces"] == 2 and curved.numbers["small_flat_faces"] == 0
    assert faceted.numbers["file_kb"] > 100 * curved.numbers["file_kb"]
    assert "flat strips" not in curved.text


def test_the_skills_new_code_runs_in_the_image(bench: Any) -> None:
    """Every Python block print-shop added to the skills that stands alone, run as the model
    would run it: each that builds with CadQuery, and since mqucifer/print-shop#61 each after
    `ADDED` that lays a figure out in Shapely. The stained-glass layout's own step-2 block uses
    the reader's cells, so it doesn't stand alone, and isn't here."""

    def found(name: str, after: bool) -> list[str]:
        text = (skills.SKILLS / name).read_text()
        part = text.split(skills.ADDED)[-1] if after else text
        return re.findall(r"```python\n(.*?)```", part, re.DOTALL)

    names = (
        "cadquery/SKILL.md",
        "cadquery/references/layout-to-arcs.md",
        "cadquery/references/errors.md",
        "designing-stained-glass/SKILL.md",
    )
    measured = ("cadquery/SKILL.md", "cadquery/references/errors.md")
    blocks = [b for n in names for b in found(n, n in measured) if "import cadquery" in b]
    blocks += [
        b
        for n in names
        if skills.ADDED in (skills.SKILLS / n).read_text()
        for b in found(n, True)
        if "import shapely" in b and "import cadquery" not in b
    ]
    # The U from pieces, curves from a layout, the errors' two, the stained-glass cells built as
    # curves, and its figure from pieces.
    assert len(blocks) == 6
    for k, block in enumerate(blocks):
        (bench.work / f"skill_block_{k}.py").write_text(block)
        ran = host.run([f"skill_block_{k}.py"], bench.work, python=True)
        assert ran.returncode == 0, f"block {k}:\n{block}\n{ran.stdout}{ran.stderr}"


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
        # The part's point is 60 px, about 16 mm, below the middle: off the box its name is
        # marked in, which is wider than the number it once was (mqucifer/print-shop#54).
        ["read_picture.py", f"marked/plate-3mm-top.png:{x_part},316",
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
    assert "drawn: glass-pane-1.step: cup (blue)" in answer.text
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


def test_each_file_is_marked_by_its_name_and_a_long_legend_wraps(bench: Any) -> None:
    """The approval sheet marked `pane_1.step` "2" beside the model's own numbered layout, and a
    legend was cut at its edge ("none was giv") (mqucifer/print-shop#54)."""
    answer = bench(
        "render", "island/frame-island.step", "island/pane_1.step", "--views", "top",
        "--size", "128", "--look", "island/pane_1.step=blue:the small pane in the middle",
        "--renders-out", "wrapped",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    marks = {f["file"]: f["mark"] for f in answer.numbers["files"]}
    assert marks == {"island/frame-island.step": "frame-island", "island/pane_1.step": "pane_1"}
    assert "drawn: island/pane_1.step: the small pane in the middle (blue)" in answer.text
    assert "drawn: island/frame-island.step (a colour of the sheet's, none was given)" in (
        answer.text
    )
    with png_size(bench.work / answer.record["files"][-1]) as size:
        assert size[0] == 256, "one view and two files: a sheet two pictures wide"
        assert size[1] > 128 + legend_px(2, 256), "each entry's words wrap, and none is cut"


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
    # How each was chosen, in three figures (mqucifer/print-shop#61): the full rim, the share it
    # leaves, and the smaller rim that keeps the share wanted.
    assert rims[19.5]["chosen"] == "full" and rims[5.5]["chosen"] == "smaller"
    full = rims[19.5]["visible_at_full_pct"]
    assert (
        f"At the full rim, 1.2 mm before play, {full:.2f}% of it is visible, at least the 60% "
        "wanted, so it keeps the full rim." in held.text
    )
    small = rims[5.5]
    assert small["visible_at_full_pct"] < 60
    assert (
        f"At the full rim, 1.2 mm before play, {small['visible_at_full_pct']:.2f}% of it would be "
        "visible, under the 60% wanted, so its rim is the widest that keeps 60%: "
        f"{small['needs_mm']:g} mm, set as 0.869 mm, which leaves "
        f"{small['visible_at_need_pct']:.2f}%." in held.text
    )
    unheld = bench("capture", "rims/unheld.step", *share)
    assert unheld.verdict == "FAIL", unheld.text
    assert "set this pane's rim to 0.869 mm before play. It has 1.2 mm" in unheld.record["why"]
    assert "42.8% of it is visible, where 60% is wanted" in unheld.record["why"]
    assert "remove this pane. Even the smallest rim, 0.55 mm before play" in unheld.record["why"]
    assert "so it's to be removed." in unheld.text and "both under the 60% wanted" in unheld.text
    assert "under the 60% wanted, so its rim is the widest that keeps 60%" in unheld.record["why"]
    done = host.run(["capture", "rims/held.step", *share[:-2]], bench.work)
    assert done.returncode == 3 and "no default for any of them" in done.stdout + done.stderr
    # Which way each rim moves, and what that leaves visible (mqucifer/print-shop#65): both
    # panes' rims at 1.0 mm, as on `eval-rims-4`.
    both = bench("capture", "rims/both-at-1.step", *share)
    assert (
        "Widen its rim by 0.2 mm, from 1 to 1.2 mm before play: the opening's edge moves 0.2 mm "
        "in, over the pane, which covers more of it, and leaves 81.46% of it visible" in both.text
    )
    assert (
        "Narrow its rim by 0.131 mm, from 1 to 0.869 mm before play: the opening's edge moves "
        "0.131 mm out, off the pane, which shows more of it, and leaves 60.00% of it visible"
        in both.text
    )
    assert "Its rim is the rim it needs: leave it." in held.text


def test_the_rim_a_reply_names_passes_when_its_set(bench: Any) -> None:
    """The figure is the rim needed rounded down to the thousandth: a cell built at it passes, so
    following the reply ends the loop. At the hundredth, 0.5554 mm was told 0.56, which fails."""
    share = ("--pocket-z", "2", "--clearance", "0.25", "--pane-thickness", "1",
             "--full-rim", "1.2", "--least-rim", "0.55", "--min-visible", "60")  # fmt: skip
    answer = bench("capture", "rims/thousandth.step", *share)
    assert answer.verdict == "FAIL", answer.text
    told = [p for p in answer.numbers["panes"] if p["rim"]["has_mm"] > 0.58]
    held = [p for p in answer.numbers["panes"] if p["rim"]["has_mm"] < 0.58]
    assert len(told) == len(held) == 1
    assert held[0]["rim"]["set_mm"] == 0.555 and held[0]["rim"]["visible_pct"] >= 60.0
    assert "set this pane's rim to 0.555 mm before play. It has 0.6 mm" in answer.record["why"]
    assert answer.record["why"].count("set this pane's rim") == 1


# The enclosure (mqucifer/print-shop#60)


def test_clearance_holds_a_lid_from_both_ends(bench: Any) -> None:
    """A lid's lip with 0.2 mm of play, lifted 0.5 mm off its seat, is in a range of 0.1 to
    0.3 mm; one with 0.6 mm of play reads its lift, 0.5, and is too loose. Seated, it touches,
    and the reply says how to read its play."""
    held = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p2-up.step",
                 "--min-gap", "0.1", "--max-gap", "0.3")  # fmt: skip
    assert held.verdict == "CLEAR", held.text
    assert only_pair(held)["gap_mesh_mm"] == pytest.approx(0.2, abs=0.005)
    loose = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p6-up.step",
                  "--min-gap", "0.1", "--max-gap", "0.3")  # fmt: skip
    assert loose.verdict == "TOO LOOSE" and loose.code == 1, loose.text
    assert only_pair(loose)["gap_mesh_mm"] == pytest.approx(0.5, abs=0.005)
    seated = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p2.step",
                   "--max-gap", "0.3")  # fmt: skip
    assert seated.verdict == "TOUCHING"
    assert any("run this again with --lift D" in n for n in seated.record["notes"])


def test_clearance_lifts_a_seated_lid_itself_so_the_need_is_on_the_printed_file(
    bench: Any,
) -> None:
    """mqucifer/print-shop#65: the printed lids, seated, read as the lifted copies do with
    `--lift 0.5`, and the reply says the lid was moved and its file wasn't. A lift that's the gap
    itself is said, and one no more than a limit is refused."""
    lift = ("--lift", "0.5", "--min-gap", "0.1", "--max-gap", "0.3")
    held = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p2.step", *lift)
    assert held.verdict == "CLEAR", held.text
    assert only_pair(held)["gap_mm"] == pytest.approx(0.2, abs=1e-4)
    assert held.numbers["lift"] == {"file": "lid-lip-0p2.step", "along": "z", "mm": 0.5}
    assert "lifted: lid-lip-0p2.step, moved 0.5 mm up along z before measuring" in held.text
    loose = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p6.step", *lift)
    assert loose.verdict == "TOO LOOSE", loose.text
    assert any("the least gap is the lift, 0.5 mm" in n for n in loose.record["notes"])
    short = host.run(
        ["clearance", "enclosure/tray.step", "enclosure/lid-lip-0p2.step", "--lift", "0.3",
         "--max-gap", "0.3"], bench.work)  # fmt: skip
    assert short.returncode == 3 and "lift by more than 0.3 mm" in short.stdout + short.stderr


def test_a_gap_drawn_on_a_curve_is_held_at_both_ends_by_the_exact_distance(bench: Any) -> None:
    """mqucifer/print-shop#65: on `eval-fit-2` a pane 0.25 mm from its pocket all round, round
    its corners too, was TOO CLOSE under `--min-gap 0.25`: the mesh's chords cut into the
    pocket's concave corners and read it short. On a STEP pair the verdict reads OCCT's exact
    distance, at both ends, so 0.25 passes and 0.2501 doesn't."""
    pair = ("clearance", "enclosure/curved-pocket.step", "enclosure/curved-pane.step")
    exact = bench(*pair, "--min-gap", "0.25", "--max-gap", "0.25")
    assert exact.verdict == "CLEAR", exact.text
    found = only_pair(exact)
    # The figure the limits judged, as it is (mqucifer/print-shop#72): 0.24999999999999853.
    assert found["gap_mm"] == pytest.approx(0.25, abs=1e-6) and found["gap_by"] == "occt"
    assert found["gap_mesh_mm"] <= 0.25
    assert bench(*pair, "--min-gap", "0.2501").verdict == "TOO CLOSE"
    assert bench(*pair, "--max-gap", "0.2499").verdict == "TOO LOOSE"


def test_a_gap_drawn_at_the_most_passes_and_a_range_the_wrong_way_round_is_refused(
    bench: Any,
) -> None:
    assert bench("clearance", "cube.step", "cube-gap-0p1.step", "--max-gap", "0.1").verdict == (
        "CLEAR"
    )
    assert bench("clearance", "cube.step", "cube-gap-0p1.step", "--max-gap", "0.09").verdict == (
        "TOO LOOSE"
    )
    done = host.run(
        ["clearance", "cube.step", "cube-gap-0p1.step", "--min-gap", "0.2", "--max-gap", "0.1"],
        bench.work,
    )
    assert done.returncode == 3 and "is over --max-gap" in done.stdout + done.stderr


def test_edges_reads_each_rounding_and_holds_a_radius(bench: Any) -> None:
    """Every edge rounded 2 mm: 2 passes, a rounding drawn at it, and 2.1 fails."""
    at = bench("edges", "enclosure/round2.step", "--min-radius", "2")
    assert at.verdict == "OK", at.text
    assert at.numbers["least_rounded_mm"] == pytest.approx(2.0, abs=0.001)
    assert at.numbers["least_radius_mm"] == pytest.approx(2.0, abs=0.001)
    assert at.numbers["edges"]["sharp_outside"] == 0
    assert any("a circle through the section" in s for s in at.record["second_check"])
    over = bench("edges", "enclosure/round2.step", "--min-radius", "2.1")
    assert over.verdict == "FAIL" and "under 2.1 mm" in over.record["why"]


def test_edges_names_each_sharp_edge_and_where(bench: Any) -> None:
    """Upright edges 3 mm, the top 1.5 and the bottom sharp: 8 sharp edges at z = -10."""
    answer = bench("edges", "enclosure/mixed.step", "--min-radius", "1.5")
    assert answer.verdict == "FAIL", answer.text
    sharp = answer.numbers["sharp_edges"]
    assert len(sharp) == 8
    assert {e["at"][2] for e in sharp} == {-10.0}
    assert {e["turn_deg"] for e in sharp} == {90.0}
    assert answer.numbers["least_radius_mm"] == 0.0
    assert answer.numbers["least_rounded_mm"] == pytest.approx(1.5, abs=0.001)
    assert "8 sharp outside edge(s)" in answer.record["why"]
    assert any("8 turn as the exact surface does" in s for s in answer.record["second_check"])


def test_edges_reads_an_open_tray_and_not_the_inside_of_a_closed_one(bench: Any) -> None:
    """Open, a finger meets the rim's 8 outside edges, its 4 inside and the post's top 4, and
    the 12 inside corners aren't read. Closed by its lid, nothing sharp is in reach: the seat is
    flush. A reference closes it the same, and isn't read."""
    tray = bench("edges", "enclosure/tray.step", "--min-radius", "2")
    assert tray.verdict == "FAIL"
    assert tray.numbers["edges"]["sharp_outside"] == 16
    assert tray.numbers["edges"]["inside_corners"] == 12
    closed = bench("edges", "enclosure/tray.step", "enclosure/tray-lid.step", "--min-radius", "2")
    assert closed.verdict == "OK", closed.text
    assert closed.numbers["edges"]["sharp_outside"] == 0
    given = bench("edges", "enclosure/tray.step", "references/tray-lid.step", "--min-radius", "2")
    assert given.verdict == "OK", given.text
    assert given.numbers["not_read"] == [{"file": "references/tray-lid.step", "why": "a reference"}]
    assert {r["file"] for r in given.numbers["rounded"]} == {"enclosure/tray.step"}
    assert bench("edges", "references/tray-lid.step").verdict == "NO ANSWER"


def test_edges_reads_the_least_of_each_group_and_of_a_place(bench: Any) -> None:
    """mqucifer/print-shop#65: a need names the upright corners' radius by its group, not by an
    index into a list a rebuild can reorder. `mixed` has its upright edges 3 mm, its top 1.5 and
    its bottom sharp. A box round one corner reads its upright rounding and the sharp foot
    beside it; facing -y reads the edges round the front face."""
    answer = bench("edges", "enclosure/mixed.step", "--box", "corner=15,10,-12,25,20,12",
                   "--facing", "front=-y")  # fmt: skip
    groups = answer.numbers["groups"]
    assert groups["upright"]["least_radius_mm"] == pytest.approx(3.0, abs=0.001)
    assert (groups["upright"]["sharp"], groups["upright"]["rounded"]) == (0, 4)
    assert groups["top"]["least_radius_mm"] == pytest.approx(1.5, abs=0.001)
    assert (groups["bottom"]["least_radius_mm"], groups["bottom"]["sharp"]) == (0.0, 8)
    assert groups["side"]["least_radius_mm"] is None
    corner, front = answer.numbers["selected"]["corner"], answer.numbers["selected"]["front"]
    assert corner["least_radius_mm"] == 0.0 and corner["rounded"] >= 1
    x, y, _ = corner["least_at"]
    assert 15 <= x <= 25 and 10 <= y <= 20, "where it's said is in the box"
    assert front["least_radius_mm"] == 0.0 and front["least_at"][1] == pytest.approx(-15.0)
    assert {r["group"] for r in answer.numbers["rounded"]} == {"upright", "top"}
    assert "groups.NAME.least_radius_mm" in answer.text and "as selected.corner" in answer.text
    twice = host.run(["edges", "enclosure/mixed.step", "--box", "a=0,0,0,1,1,1", "--facing",
                      "a=+z"], bench.work)  # fmt: skip
    assert twice.returncode == 3 and "a name of its own" in twice.stdout + twice.stderr


def test_rays_from_a_point_in_air_say_what_across_measures(bench: Any) -> None:
    """mqucifer/print-shop#65: on `eval-edges-1` rays from a rounded corner's centre, which lay
    in the box's hollow, were read as the corner's radius. The tray's corner centre is 1 mm from
    two inside walls: the nearest wall reads 1, and the least across 2√2, the hollow's width."""
    answer = bench("rays", "enclosure/tray.step", "--centre", "17,12,11")
    level = answer.numbers["levels"][0]
    assert level["start_in_material"] == 0.0
    assert level["nearest_wall_mm"] == pytest.approx(1.0, abs=0.01)
    assert level["across_mm"]["min"] == pytest.approx(2 * 2**0.5, abs=0.01)
    told = " ".join(answer.record["notes"])
    assert "the centre is in air, so `across` is the open space's width" in told
    assert "It isn't an outside corner's radius" in told and "`edges`" in told


def test_edges_says_a_rounding_stored_as_strips(bench: Any) -> None:
    """Each rounded corner as 16 flat strips: 17 sharp edges a corner, each turning under 6°."""
    answer = bench("edges", "enclosure/strips.step")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["faceted_edges"] == 68
    assert answer.numbers["rounded"] == []
    assert any("stored as flat strips" in n for n in answer.record["notes"])


# A flat layout, read in 2D (mqucifer/print-shop#61)


def _layout(work: Path, name: str, pieces: dict[str, Any]) -> str:
    """A layout file as a model's script writes it: GeoJSON or WKT outlines, by name."""
    (work / name).write_text(json.dumps({"pieces": pieces}))
    return name


def _circle(cx: float, cy: float, r: float, n: int = 256) -> list[list[float]]:
    import math

    ring = [[cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)]
            for k in range(n)]  # fmt: skip
    return [*ring, ring[0]]


def test_a_layout_says_each_piece_its_least_width_its_tips_and_what_an_inset_leaves(
    bench: Any,
) -> None:
    """Planted pieces whose answers are arithmetic: two squares joined by a bar 1 mm wide, a ring
    3 mm wide, an equilateral triangle, a piece in two parts, and an arm 2 mm wide with a round
    end, which is no narrower than the arm."""
    import math

    # An arm 2 mm wide, 10 mm long, with a round end of radius 1.
    end = [[90 + math.sin(math.pi * k / 64), 1 - math.cos(math.pi * k / 64)] for k in range(1, 64)]
    pieces = {
        "dumbbell": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 4.5], [16, 4.5],
                     [16, 0], [26, 0], [26, 10], [16, 10], [16, 5.5], [10, 5.5], [10, 10], [0, 10],
                     [0, 0]]]},  # fmt: skip
        "ring": {"type": "Polygon", "coordinates": [_circle(40, 5, 8), _circle(40, 5, 5)[::-1]]},
        "triangle": {"type": "Polygon",
                     "coordinates": [[[60, 0], [70, 0], [65, 5 * math.sqrt(3)], [60, 0]]]},
        "apart": "MULTIPOLYGON (((0 20, 5 20, 5 25, 0 25, 0 20)), ((8 20, 13 20, 13 25, 8 25, "
        "8 20)))",
        "arm": {"type": "Polygon",
                "coordinates": [[[80, 0], [90, 0], *end, [90, 2], [80, 2], [80, 0]]]},
    }
    name = _layout(bench.work, "flat.json", pieces)
    answer = bench("layout", name, "--inset", "0.6", "--min-width", "0.9")
    rows = answer.numbers["pieces"]
    assert answer.verdict == "FAIL", answer.text
    assert answer.record["why"].count("sharp tip, 60°") == 3, "the triangle's tips, and no width"
    assert "dumbbell" not in answer.record["why"] and "arm" not in answer.record["why"]
    assert rows["dumbbell"]["least_width_mm"] == pytest.approx(1.0, abs=0.002)
    at = rows["dumbbell"]["least_width_at"]
    assert 10 <= at[0] <= 16 and at[1] == pytest.approx(5.0, abs=0.01), "across the bar"
    inset = rows["dumbbell"]["insets"]["0.6"]
    assert inset["parts_count"] == 2, "the bar is cut"
    assert [c["width_mm"] for c in inset["cut_at"]] == [pytest.approx(1.0, abs=0.002)]
    assert inset["cut_at"][0]["runs_mm"] == pytest.approx(6.0, abs=0.1), "one place, all the bar"
    assert rows["ring"]["least_width_mm"] == pytest.approx(3.0, abs=0.002)
    assert rows["ring"]["insets"]["0.6"]["least_width_mm"] == pytest.approx(1.8, abs=0.002)
    assert rows["triangle"]["least_width_mm"] is None and len(rows["triangle"]["tips"]) == 3
    assert rows["apart"]["parts_count"] == 2
    assert "piece apart: NOT one piece: 2 parts" in answer.text
    assert rows["arm"]["least_width_mm"] == pytest.approx(2.0, abs=0.002), "a round end isn't less"
    for row in rows.values():
        second = row["width_by_facing"]
        assert (second is None) == (row["least_width_mm"] is None)
        if second is not None:
            # The nearest facing point reads from cos(20°) of the ray to the ray, and the same
            # across parallel sides: an arm's round end is the one place here they differ.
            first = row["least_width_mm"]
            assert first * math.cos(math.radians(20)) - 0.1 <= second["width_mm"] <= first + 0.1
    assert rows["dumbbell"]["width_by_facing"]["width_mm"] == pytest.approx(1.0, abs=0.002)
    assert rows["ring"]["width_by_facing"]["width_mm"] == pytest.approx(3.0, abs=0.002)
    assert answer.numbers["overlaps"] == []
    picture = bench.work / answer.numbers["picture"]
    with png_size(picture) as (width, height):
        assert max(width, height) >= 1000
    assert len(answer.record["second_check"]) == len(pieces)

    told = bench("layout", _layout(bench.work, "bare.json", {}))
    assert told.verdict == "NO ANSWER" and '{"pieces": {NAME: OUTLINE' in told.record["why"]


# A mated pair's gaps by place, and a figure exactly at its limit (mqucifer/print-shop#72)

#: A box round the pane's +x edge, between its faces: its edge and the pocket's wall, and neither
#: face's play. One round the lip over that edge, above the pane's top face: its face play.
#: One round a rounded corner, between the faces.
EDGE = "edge=9.6,-3,0.1,10.6,3,1.1"
FACE = "face=9.2,-3,1.0,9.8,3,1.5"
CORNER = "corner=8.5,5.5,0.1,10,7,1.1"


def test_a_pairs_gaps_are_read_by_place_round_the_edge_and_round_a_face(bench: Any) -> None:
    """On `eval-fit-3` a pane sat in its pocket with 0.25 mm of play round its edge and 0.15 mm on
    its faces, and `clearance` read only 0.15, the least. On a pair of our own like it, a box round
    the edge reads 0.25, one round a face 0.15, and a rounded corner 0.25, OCCT and the mesh
    agreeing; facing +z reads the face play and +x the edge's. With places named, the range holds
    each place, and the pair's own least gap is read and not held."""
    answer = bench("clearance", "places/pocket.step", "places/pane.step", "--box", EDGE,
                   "--box", FACE, "--box", CORNER, "--facing", "top=+z", "--facing", "side=+x",
                   "--min-gap", "0.25", "--max-gap", "0.25")  # fmt: skip
    pair = only_pair(answer)
    assert pair["gap_mm"] == pytest.approx(0.15, abs=1e-6) and pair["verdict"] == "CLEAR"
    at = pair["places"]
    for name, gap in (("edge", 0.25), ("face", 0.15), ("corner", 0.25), ("top", 0.15),
                      ("side", 0.25)):  # fmt: skip
        assert at[name]["gap_mm"] == pytest.approx(gap, abs=1e-6), (name, answer.text)
        assert at[name]["gap_by"] == "occt"
        assert at[name]["gap_second_mm"] == pytest.approx(gap, abs=2 * 0.002 + 1e-4), name
    assert [at[n]["verdict"] for n in ("edge", "face", "corner")] == ["CLEAR", "TOO CLOSE", "CLEAR"]
    assert answer.verdict == "TOO CLOSE" and answer.code == 1, "a place's verdict counts"
    assert "as pairs.N.places.edge.gap_mm" in answer.text
    assert "the range is the play at those places" in answer.text
    assert answer.numbers["places"]["edge"] == {"box": [[9.6, -3.0, 0.1], [10.6, 3.0, 1.1]]}
    assert answer.numbers["places"]["top"] == {"facing": "+z"}
    assert not reports.unlike("clearance", answer.numbers), reports.unlike(
        "clearance", answer.numbers
    )
    edge = bench("clearance", "places/pocket.step", "places/pane.step", "--box", EDGE,
                 "--min-gap", "0.25", "--max-gap", "0.25")  # fmt: skip
    assert edge.verdict == "CLEAR" and edge.code == 0, edge.text
    close = bench("clearance", "places/pocket.step", "places/pane.step", "--box", EDGE,
                  "--min-gap", "0.2501")  # fmt: skip
    assert close.verdict == "TOO CLOSE", close.text


def test_a_place_on_an_stl_pair_reads_the_mesh_and_samples_it(bench: Any) -> None:
    answer = bench("clearance", "places/pocket.stl", "places/pane.stl", "--box", EDGE,
                   "--box", CORNER)  # fmt: skip
    at = only_pair(answer)["places"]
    assert at["edge"]["gap_by"] == "mesh"
    assert at["edge"]["gap_mm"] == pytest.approx(0.25, abs=1e-4)
    assert at["corner"]["gap_mm"] == pytest.approx(0.25, abs=2 * 0.002 + 1e-4)
    assert "points sampled on the mesh clipped by the box" in answer.text
    facing = bench("clearance", "places/pocket.stl", "places/pane.stl", "--facing", "up=+z")
    assert facing.verdict == "NO ANSWER" and "with --box" in facing.text


def test_a_box_round_a_clip_arm_reads_its_side_play_with_the_plate_seated(bench: Any) -> None:
    """On `eval-fan-1` the clip arms' side play couldn't be read apart from the seated plate's
    contact, and lifted, the barb overlapped. A box round an arm, clear of the seat, reads the
    side play with the plate seated; one at the seat reads its contact, 0."""
    answer = bench("clearance", "places/base.step", "places/plate.step",
                   "--box", "arm=9.8,-3,2.5,12,3,5", "--box", "seat=-5,-5,1.5,5,5,2.5")  # fmt: skip
    pair = only_pair(answer)
    assert pair["verdict"] == "TOUCHING"
    assert pair["places"]["arm"]["gap_mm"] == pytest.approx(0.3, abs=1e-6), answer.text
    assert pair["places"]["arm"]["verdict"] == "CLEAR"
    assert pair["places"]["seat"]["gap_mm"] == pytest.approx(0.0, abs=1e-6)
    assert pair["places"]["seat"]["verdict"] == "TOUCHING"
    assert answer.verdict == "TOUCHING" and answer.code == 0


def test_a_box_that_catches_no_surface_of_one_of_the_pair_says_which_and_gives_no_figure(
    bench: Any,
) -> None:
    inside = "inside=-15,-5,0.5,-14,-4,1.5"
    answer = bench("clearance", "places/base.step", "places/plate.step",
                   "--box", "arm=9.8,-3,2.5,12,3,5", "--box", inside)  # fmt: skip
    found = only_pair(answer)["places"]["inside"]
    assert found["gap_mm"] is None and found["verdict"] == "NOT CAUGHT"
    assert "base.step fills the box with material and plate.step lies all outside" in found["why"]
    assert answer.verdict == "NO ANSWER" and answer.code == 2, "no pair has a figure there"
    assert "second check: arm:" in answer.text, "the other places are still read and checked"
    twice = host.run(["clearance", "places/base.step", "places/plate.step", "--box",
                      "a=0,0,0,1,1,1", "--facing", "a=+z"], bench.work)  # fmt: skip
    assert twice.returncode == 3 and "a name of its own: a" in twice.stdout + twice.stderr
    flat = host.run(["clearance", "places/base.step", "places/plate.step", "--box",
                     "a=0,0,0,1,1,0"], bench.work)  # fmt: skip
    assert flat.returncode == 3 and "has no room inside it" in flat.stdout + flat.stderr


def test_pause_passes_a_room_exactly_at_its_limit_and_fails_past_it(bench: Any) -> None:
    """On `eval-fit-3` `pause` said "0.400 mm above the pane at the pause, over 0.4 mm": the room
    was 0.40000000000000036 by the floats that add up the layers. A 1.6 mm pane under the frame's
    ceiling at z=3 has that room exactly."""
    args = ("pause", "frame.step", "--pocket-z", "2", "--pane-thickness")
    at = bench(*args, "1.6", "--max-gap", "0.4", "--min-room", "0.4")
    assert at.verdict == "OK", at.text
    assert at.numbers["least_room_over_pane_mm"] == pytest.approx(0.4, abs=1e-6)
    over = bench(*args, "1.5999", "--max-gap", "0.4")
    assert over.verdict == "FAIL", over.text
    assert "0.4001 mm above the pane at the pause, over 0.4 mm" in over.text
    assert "Raise the pocket's floor by at least 0.0001 mm" in over.text
    under = bench(*args, "1.6001", "--min-room", "0.4")
    assert under.verdict == "FAIL", under.text
    assert "0.3999 mm above the pane at the pause, under the 0.4 mm asked for" in under.text


def test_thin_passes_a_wall_exactly_the_nozzles_width_and_a_body_exactly_the_least(
    bench: Any,
) -> None:
    """A wall 0.4 mm wide at a 0.4 mm nozzle read DISAGREE: its rays started 1e-5 mm inside it
    and didn't add that back. Its body is 824 mm³."""
    at = bench("thin", "wall-0p4.step", "--nozzle", "0.4", "--min-body", "824")
    assert at.verdict == "OK", at.text
    narrow = bench("thin", "wall-0p4.step", "--nozzle", "0.4001")
    assert narrow.verdict == "FAIL" and "the thinnest 0.4000 mm" in narrow.text, narrow.text
    small = bench("thin", "wall-0p4.step", "--min-body", "824.001")
    assert small.verdict == "FAIL" and "under 824.001 mm³" in small.text, small.text


def test_light_passes_a_share_exactly_at_its_limit_and_fails_past_it(bench: Any) -> None:
    files = ("light", "glass/frame.step", "glass/pane_a.step")
    share = bench(*files).numbers["least_open_pct"]
    assert bench(*files, "--min-open", repr(share)).verdict == "OK"
    past = bench(*files, "--min-open", repr(share + 1e-4))
    assert past.verdict == "FAIL", past.text


def test_thickness_wall_and_capture_pass_at_their_limit_with_their_readings_error(
    bench: Any,
) -> None:
    """Each reads on the mesh, and holds a figure at its limit within its stated error: 2 chords
    of 0.002 mm for `thickness` and `wall`, and for `capture` none past arithmetic's noise, as a
    straight lip reads exactly (mqucifer/print-shop#72's answer: those errors stay as they are)."""
    assert bench("thickness", "cube.step", "--max", "9.996", "--min", "10.004").verdict == "OK"
    assert bench("thickness", "cube.step", "--max", "9.9959").verdict == "FAIL"
    assert bench("thickness", "cube.step", "--min", "10.0041").verdict == "FAIL"
    hole = ("wall", "hang.step", "--hole", "50,121.5,2", "--min-wall")
    held = bench(*hole, "2.004")
    assert held.verdict == "OK" and "within the mesh's 0.004 mm" in held.text, held.text
    assert bench(*hole, "2.0041").verdict == "FAIL"
    assert capture(bench, "held.step", "--min-lip", "1.8").verdict == "OK"
    assert capture(bench, "held.step", "--min-lip", "1.8001").verdict == "FAIL"


def test_edges_passes_a_rounding_at_its_limit_with_its_readings_error(bench: Any) -> None:
    """A rounding drawn at R may read short of it by 0.5% of R: a block rounded 2 mm passes at
    2 / 0.995 mm and fails past it."""
    block = ("edges", "enclosure/round2.step", "--min-radius")
    radius = bench(*block, "2").numbers["least_rounded_mm"]
    held = bench(*block, repr(radius / 0.995))
    assert held.verdict == "OK" and "within 0.01005 mm" in held.text, held.text
    assert bench(*block, repr(radius / 0.995 + 1e-4)).verdict == "FAIL"


def test_layout_readback_and_rays_pass_at_their_limit_and_fail_past_it(bench: Any) -> None:
    name = _layout(bench.work, "bar.json", {"bar": "POLYGON ((0 0, 10 0, 10 1, 0 1, 0 0))"})
    width = bench("layout", name).numbers["least_width_mm"]
    assert bench("layout", name, "--min-width", repr(width)).verdict == "OK"
    assert bench("layout", name, "--min-width", repr(width + 1e-4)).verdict == "FAIL"
    cut = ("readback", "block.step", "block-seat.step")
    change = bench(*cut).numbers["change_mm3"]
    expect = ("--expect-delta", repr(change + 0.5))
    assert bench(*cut, *expect, "--tol", "0.5").verdict == "OK"
    assert bench(*cut, *expect, "--tol", "0.4999").verdict == "FAIL"
    seat = ("rays", "block-seat.step", "--centre", "30,20,16", "--d", "22")
    level = bench(*seat).numbers["levels"][0]["across_mm"]
    tol = max(22 - level["min"], level["max"] - 22)
    assert bench(*seat, "--tol", repr(tol)).verdict == "OK"
    # Past it the rays' span is out while the section's circle fit, the second method, may still
    # be in: FAIL or DISAGREE, and never OK.
    past = bench(*seat, "--tol", repr(tol - 1e-4))
    assert past.verdict in ("FAIL", "DISAGREE"), past.text


# What holds a bought part (mqucifer/print-shop#76)

HOOKS = ("clearance", "hold/hooks.step", "hold/fan.step", "--slide", "-z")
#: The same words every reply with a path gives, as the issue has them.
BACKWARDS = (
    "Read backwards, that is everything fan.step has to pass on its way in, so it is what has to "
    "bend, or be fitted afterwards"
)


def test_a_fan_on_hooks_slid_out_is_held_by_the_hooks_and_they_are_its_path(bench: Any) -> None:
    """A fan block 30 x 30 x 7.5 under a plate, four hooks 0.2 mm under its bottom face reaching
    1 mm in: slid down, its way out, it meets the hooks after 0.2 mm, at all four, and they're
    everything on its path, each reaching 1 mm in. The rays read the same."""
    answer = bench(*HOOKS, "--box", "east=13.5,-4,-1.5,15.5,4,0")
    assert answer.verdict == "HELD" and answer.code == 0, answer.text
    slide = answer.numbers["slide"]
    assert slide["first_mm"] == pytest.approx(0.2, abs=2 * 0.002 + 1e-4), answer.text
    assert slide["first_rays_mm"] == pytest.approx(0.2, abs=1e-4)
    assert slide["meets"] == ["hooks.step"] and slide["hold_mm"] == 1.0
    assert len(slide["contacts"]) == 4
    assert [c["in"] for c in slide["contacts"] if c["box"][0][0] > 13] == [["east"]]
    (path,) = slide["path"]
    assert path["part"] == "hooks.step" and len(path["pieces"]) == 4
    assert path["reach_mm"] == pytest.approx(1.0, abs=0.01)
    assert path["reach_rays_mm"] == pytest.approx(1.0, abs=0.01)
    assert path["box"] == [[-15.0, -15.0, -1.2], [15.0, 15.0, -0.2]]
    assert "fan.step first meets hooks.step after 0.2000 mm, as slide.first_mm" in answer.text
    assert BACKWARDS in answer.text and "in steps of 0.1 mm" in answer.text
    assert not reports.unlike("clearance", answer.numbers), reports.unlike(
        "clearance", answer.numbers
    )
    # A first contact at --hold holds, as any figure at its limit passes; past it, it doesn't.
    first = repr(slide["first_mm"])
    assert bench(*HOOKS, "--hold", first).verdict == "HELD"
    loose = bench(*HOOKS, "--hold", "0.1")
    assert loose.verdict == "NOT HELD" and loose.code == 1, loose.text
    assert "more than 0.1. Bring what holds it within 0.1 mm" in loose.text


def test_toes_inside_the_frame_overlap_where_they_lie_and_hold_nothing(bench: Any) -> None:
    """The design run's fault, on parts of our own: toes 1 mm into the fan's frame at its top
    face, 12 mm³, taken for a catch. The overlap is said first, and the fan slides out."""
    answer = bench("clearance", "hold/toes.step", "hold/fan.step", "--slide", "-z")
    assert answer.verdict == "NOT HELD" and answer.code == 1, answer.text
    lines = answer.text.splitlines()
    assert lines[1].startswith("OVERLAP WHERE IT LIES: fan.step | toes.step: 12.000 mm³"), lines
    assert "An overlap is never a hold" in lines[1]
    assert "an overlap is never a hold; sliding -z, it leaves without meeting anything" in lines[0]
    slide = answer.numbers["slide"]
    assert slide["overlaps"][0]["overlap_mm3"] == pytest.approx(12.0, abs=1e-3)
    assert slide["overlaps"][0]["box"] == [[-15.0, -15.0, 7.0], [15.0, 15.0, 7.5]]
    assert slide["first_mm"] is None and slide["path"] == []


def test_a_plain_plate_and_a_lid_on_its_tray_are_not_held(bench: Any) -> None:
    """Nothing under the fan, and a lid whose lip only fits its walls: both slide out. The bench
    doesn't model friction, and says so."""
    plain = bench("clearance", "hold/plain.step", "hold/fan.step", "--slide", "-z")
    assert plain.verdict == "NOT HELD", plain.text
    assert "fan.step leaves without meeting anything" in plain.text
    lid = bench("clearance", "enclosure/tray.step", "enclosure/lid-lip-0p2.step", "--slide", "+z")
    assert lid.verdict == "NOT HELD" and lid.numbers["slide"]["first_mm"] is None, lid.text
    assert "The bench doesn't model friction" in lid.text


def test_a_slide_takes_two_files_and_no_gap_range(bench: Any) -> None:
    for args, says in (
        (("hold/fan.step", "--slide", "-z"), "takes two files or more"),
        ((*HOOKS[1:], "--min-gap", "0.1"), "and --min-gap its play"),
        (("hold/hooks.step", "hold/fan.step", "--hold", "1"), "add --slide"),
        ((*HOOKS[1:], "--facing", "top=+z"), "give it as --box"),
    ):
        done = host.run(["clearance", *args], bench.work)
        assert done.returncode == 3 and says in done.stdout + done.stderr, (args, done.stderr)
