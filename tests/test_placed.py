"""A part built flat in its own frame, placed where it's read against a given part
(mqucifer/print-shop#201).

The part is made here: a plate 60 x 60 x 4 with a pane's pocket on its face, the glass flat
along z, and its pane. Placed by a quarter turn about x and moved up 100, it stands on edge with
its glass square to y, and its +x end, at x 30, meets a given block, `references/leg.step`, at
x 30 to 50. Where it's built, it's 60 mm below the block. Nothing is any Sponsor's design.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import assembly as assembly_module
from print_shop.bench import host
from print_shop.bench import placed as placed_module
from print_shop.bench.report import NoAnswer
from print_shop.rounds import end_checks, placing, reports
from print_shop.rounds import standing as glass_checks
from print_shop.rounds.box import Box
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool

PARTS = '''
import cadquery as cq


def box(x0, x1, y0, y1, z0, z1):
    return (
        cq.Workplane("XY")
        .box(x1 - x0, y1 - y0, z1 - z0)
        .translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    )


# The frame, flat: a 40 mm pocket from z 1 to 3, under a 36 mm window each way.
plate = box(-30, 30, -30, 30, 0, 4).cut(box(-20, 20, -20, 20, 1, 3))
plate = plate.cut(box(-18, 18, -18, 18, 3, 4)).cut(box(-18, 18, -18, 18, 0, 1))
cq.exporters.export(plate, "apron.step")
# Its pane, 0.1 mm of play all round, filling the pocket's height.
cq.exporters.export(box(-19.9, 19.9, -19.9, 19.9, 1, 3), "pane.step")
# The given block the placed frame's +x end meets.
cq.exporters.export(box(30, 50, -10, 10, 60, 140), "references/leg.step")
'''

#: A quarter turn about x stands the plate on edge, its glass square to y, from y -4 to 0, and
#: from z 70 to 130 once moved up 100.
TURN = [["x", 90]]
AT: list[Any] = [0, 0, 100]
FRAME = glass_checks.Glass("apron.step", ("pane.step",), 2.0, 0.1, 2.0)


def entry(at: list[Any], others: list[str] | None = None) -> dict[str, Any]:
    found: dict[str, Any] = {
        "name": "apron",
        "file": "apron.step",
        "layers": "z",
        "turn": TURN,
        "at": at,
    }
    if others is not None:
        found["with"] = others
    return found


def write(work: Path, name: str, *parts: dict[str, Any]) -> None:
    (work / name).write_text(json.dumps({"parts": list(parts)}), encoding="utf-8")


# The harness, with no container.


def test_a_command_is_placed_only_where_it_reads_a_given_file(tmp_path: Path) -> None:
    (tmp_path / "references").mkdir()
    for name in ("apron.step", "pane.step", "lid.step", "references/leg.step"):
        (tmp_path / name).write_text("")
    write(tmp_path, "assembly.json", entry(AT, ["pane.step"]))
    found = placing.of_run(tmp_path, [])
    assert found.assemblies == ("assembly.json",)
    against = ["clearance", "apron.step", "references/leg.step"]
    assert found.argv(against, tmp_path) == [*against, "--placed", "assembly.json"]
    # The glass readings read no given file: as they're written, flat.
    flat = (["capture", "apron.step", "--pocket-z", "2"], ["light", "apron.step", "pane.step"])
    for words in flat:
        assert found.argv(words, tmp_path) == words
    # Two of the model's own parts: as written.
    own = ["clearance", "apron.step", "lid.step"]
    assert found.argv(own, tmp_path) == own
    # A part the assembly doesn't place, against a given file: as written, as today.
    lid = ["clearance", "lid.step", "references/leg.step"]
    assert found.argv(lid, tmp_path) == lid
    # A sheet's row, a pane in a part's `with`, and `fit-on`'s `--onto`.
    sheet = ["render", "--variant", "A=pane.step,references/leg.step", "--views", "iso"]
    assert found.argv(sheet, tmp_path)[-2:] == ["--placed", "assembly.json"]
    way = ["fit-on", "references/leg.step", "--onto", "apron.step", "--along", "+x"]
    assert found.argv(way, tmp_path)[-2:] == ["--placed", "assembly.json"]
    # One given by the model is kept.
    mine = [*against, "--placed", "other.json"]
    assert found.argv(mine, tmp_path) == mine
    # A test piece is drawn beside the part and never placed.
    piece = ["render", "lid.step", "--test-piece", "references/leg.step"]
    assert not placing.wanted(piece)


def test_the_box_runs_each_bench_command_as_the_run_places_it(tmp_path: Path) -> None:
    box = Box(tmp_path)
    seen: list[list[str]] = []
    box.placing = lambda argv, where: [*argv, "--placed", "a.json"]
    def ran(command: list[str], *_: Any, **__: Any) -> Any:
        seen.append(command)

    box._run = ran  # type: ignore[method-assign]
    box.bench(["clearance", "a.step", "references/b.step"])
    assert seen[0][-2:] == ["--placed", "a.json"]


def test_a_frame_read_against_a_given_part_with_no_placement_is_said(tmp_path: Path) -> None:
    (tmp_path / "references").mkdir()
    for name in ("apron.step", "pane.step", "references/leg.step"):
        (tmp_path / name).write_text("")
    frames = [("apron.step", ["pane.step"])]
    read = ["clearance apron.step references/leg.step --slide -x"]
    none = placing.unplaced(frames, read, tmp_path, placing.of_run(tmp_path, []))
    assert len(none) == 1 and "no assembly file places it" in none[0], none
    assert "`references/leg.step`" in none[0]
    # Read only flat, it's said of nothing.
    flat = ["capture apron.step --pocket-z 2", "light apron.step pane.step"]
    assert placing.unplaced(frames, flat, tmp_path, placing.of_run(tmp_path, [])) == []
    # Placed without its pane: that's said.
    write(tmp_path, "assembly.json", entry(AT))
    loose = placing.unplaced(frames, read, tmp_path, placing.of_run(tmp_path, []))
    assert len(loose) == 1 and "`pane.step`" in loose[0] and "`with`" in loose[0], loose
    # Placed with it: nothing to say.
    write(tmp_path, "assembly.json", entry(AT, ["pane.step"]))
    assert placing.unplaced(frames, read, tmp_path, placing.of_run(tmp_path, [])) == []


def test_an_assembly_part_carries_the_files_placed_with_it(tmp_path: Path) -> None:
    for name in ("apron.step", "pane.step"):
        (tmp_path / name).write_text("")
    write(tmp_path, "assembly.json", entry(AT, ["pane.step"]))
    found = placed_module.read(str(tmp_path / "assembly.json"))
    assert {Path(k).name for k in found} == {"apron.step", "pane.step"}
    write(tmp_path, "assembly.json", entry(AT, ["missing.step"]))
    with pytest.raises(NoAnswer, match="isn't beside the assembly file"):
        assembly_module.read(str(tmp_path / "assembly.json"))


def test_a_quarter_turn_about_x_stands_the_plate_on_edge() -> None:
    one = placed_module.Placed("apron.step", "apron", (("x", 90.0),), (0.0, 0.0, 100.0), "a")
    corners = np.array([[-30, -30, 0, 1], [30, 30, 4, 1]], dtype=float)
    moved = (one.matrix() @ corners.T).T[:, :3]
    low, high = moved.min(axis=0), moved.max(axis=0)
    assert low == pytest.approx([-30, -4, 70]) and high == pytest.approx([30, 0, 130])


def test_an_end_check_place_is_turned_back_into_the_part_s_own_frame() -> None:
    """`fit-on` reads a place where the part sits, and `arm` and `bend` read its file flat."""
    placed = {"part": "apron", "turn": TURN, "at": AT, "assembly": "assembly.json"}
    box, across = end_checks.own_frame([[28, -4, 80], [30, 0, 90]], "y", placed)
    assert box[0] == pytest.approx([28, -20, 0]) and box[1] == pytest.approx([30, -10, 4])
    assert across == "z"


APRON = """\
A table's apron with a stained-glass panel, between the given legs.

## Kind
stained-glass

## Stops
- no plan
"""


def test_a_run_places_each_reading_against_a_given_part_and_says_a_missing_one(
    tmp_path: Path,
) -> None:
    """The run's own box runs a reading against a given part placed, from the assembly in its
    working directory; and with none, a stop says so of a frame `glass` declares."""
    model = Scripted([tool("blocked", reason="x")])
    run = Run.start(tmp_path, "r", APRON, [], model, sleep=lambda _: None)
    for name in ("apron.step", "pane.step", "references/leg.step"):
        (run.work / name).write_text("x")
    assert run.box.placing is not None
    against = ["clearance", "apron.step", "references/leg.step", "--slide", "-x"]
    assert run.box.placing(list(against), run.work) == against
    run.state.glass = [FRAME.plain()]
    joint = Need("joint", "the apron meets the leg", "derived", "bench")
    joint.command = "clearance apron.step references/leg.step"
    run.state.needs = [joint.plain()]
    said = run._unplaced(glass_checks.load(run.state.glass))
    assert len(said) == 1 and "no assembly file places it" in said[0], said
    write(run.work, "assembly.json", entry(AT, ["pane.step"]))
    assert run.box.placing(list(against), run.work) == [*against, "--placed", "assembly.json"]
    assert run._unplaced(glass_checks.load(run.state.glass)) == []


# The bench, in its image.


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("placed")
    (found / "references").mkdir()
    (found / "parts.py").write_text(PARTS, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    write(found, "assembly.json", entry(AT, ["pane.step"]))
    # Moved 10 along x as well, its end runs 10 mm into the block.
    write(found, "inside.json", entry([10, 0, 100], ["pane.step"]))
    return found


def ask(work: Path, *args: str, image: str | None = None) -> tuple[str, dict[str, Any]]:
    done = host.run(list(args), work, image=image)
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text())


@pytest.mark.bench
def test_built_flat_the_frame_passes_the_glass_reading(work: Path) -> None:
    """The harness's own reading at `done`, `light`, `capture` and `pause`, on the frame as
    it's built, with the run placing what it reads against a given part."""
    box = Box(work)
    box.placing = lambda argv, where: placing.of_run(work, []).argv(argv, where)
    read = glass_checks.check(box, [FRAME], work, work / ".kept")
    assert [(one.check, one.verdict) for one in read] == [
        (glass_checks.LIGHT, "OK"),
        (glass_checks.HOLD, "OK"),
        (glass_checks.INSERT, "OK"),
    ], [one.why for one in read]
    assert all(one.passed for one in read)
    assert all("--placed" not in one.command for one in read)


@pytest.mark.bench
def test_placed_it_reads_touching_against_the_given_part_it_meets(work: Path) -> None:
    box = Box(work)
    box.placing = lambda argv, where: placing.of_run(work, []).argv(argv, where)
    done = box.bench(["clearance", "apron.step", "references/leg.step"])
    assert "placed: apron.step, as the part apron in assembly.json" in done.output, done.output
    assert re.search(r"^verdict: TOUCHING", done.output, re.M), done.output
    against = ["clearance", "apron.step", "references/leg.step"]
    text, record = ask(work, *against, "--placed", "assembly.json")
    assert record["verdict"] == "TOUCHING", text
    assert record["numbers"]["placed"]["apron.step"]["turn"] == TURN
    assert reports.unlike("clearance", record["numbers"]) == []


@pytest.mark.bench
def test_with_no_placement_it_reads_its_joints_as_today(work: Path) -> None:
    text, record = ask(work, "clearance", "apron.step", "references/leg.step")
    assert record["verdict"] == "CLEAR", text
    assert "placed" not in record["numbers"]
    assert record["numbers"]["pairs"][0]["gap_mm"] == pytest.approx(56.0, abs=1e-3)


@pytest.mark.bench
def test_placed_inside_the_given_part_it_reads_interfere(work: Path) -> None:
    against = ["clearance", "apron.step", "references/leg.step"]
    text, record = ask(work, *against, "--placed", "inside.json")
    assert record["verdict"] == "INTERFERE", text
    # Its pocketed end, x 20 to 30 of it, 10 mm into the block: as an unplaced part reads it.
    pair = record["numbers"]["pairs"][0]
    assert pair["overlap_occt_common_mm3"] == pytest.approx(2400, rel=1e-3)
    # Turned by the right hand about x: its 4 mm runs from y -4 to 0, and it's up at z 70 to 130.
    low, high = pair["overlap_occt_box"]
    assert low == pytest.approx([30, -4, 70], abs=1e-3)
    assert high == pytest.approx([40, 0, 130], abs=1e-3)


@pytest.mark.bench
def test_the_sheet_shows_it_placed_among_the_given_parts_and_flat(work: Path) -> None:
    out = work / "sheet"
    text, record = ask(
        work,
        "render",
        "--variant",
        "A=apron.step,pane.step,references/leg.step",
        "--views",
        "iso",
        "front",
        "--size",
        "256",
        "--renders-out",
        "sheet",
        "--placed",
        "assembly.json",
    )
    assert record["verdict"] == "OK", text
    assert "variant A: apron.step, pane.step, references/leg.step, its placed files where" in text
    assert "variant A flat: apron.step, pane.step, flat, as built" in text
    assert set(record["numbers"]["placed"]) == {"apron.step", "pane.step"}
    assert set(record["numbers"]["seen_px"]) == {"A", "A flat"}
    # A variant sheet's `seen_px` is a level deeper than `SHAPES` says, as on main: only
    # `placed` is read here.
    assert reports.unlike("render", {"placed": record["numbers"]["placed"]}) == []
    # Two rows of 256: placed, then flat.
    sheet = next(out.glob("*-sheet.png"))
    height = int.from_bytes(sheet.read_bytes()[20:24], "big")
    assert height > 2 * 256


@pytest.mark.cae
def test_fit_on_reads_the_part_where_it_sits(work: Path) -> None:
    """Placed 10 mm into the block, its way out along -x is blocked where it sits; built flat,
    60 mm below the block, nothing is in its way."""
    if host.image_id(host.CAE_IMAGE) is None:
        pytest.fail(f"no image {host.CAE_IMAGE}: run `scripts/bench.py build cae` first")
    args = ["fit-on", "apron.step", "--onto", "references/leg.step", "--along", "-x"]
    args += ["--from", "clear", "--step", "2"]
    text, record = ask(work, *args, "--placed", "inside.json")
    assert record["verdict"] == "BLOCKED", text
    assert record["numbers"]["placed"]["apron.step"]["at"] == [10, 0, 100]
    assert reports.unlike("fit-on", record["numbers"]) == []
    text, record = ask(work, *args)
    assert record["verdict"] == "CLEAR", text
