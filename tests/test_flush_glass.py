"""A flush frame in `glass`, with no image (mqucifer/print-shop#293): its declaration, how a flush
pane's print readings come from its laid file, and a flat frame as before. Meshes made here: the
frame of `test_paused_frame.py`, and a pane stepped as `pieces build --flush` makes one, its body
in the pocket and a raised face down through the opening to the bed. `test_flush_glass_bench.py`
reads a built panel on the real bench."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import manifold3d as m3
import numpy as np
import pytest
import trimesh

from print_shop.rounds import downward, standing
from print_shop.rounds.box import Ran
from tests.rounds_fakes import laid

SAID = 'the request says "It prints flat, with no supports."'
POCKET, OPENING = 20.0, 16.0


def _box(x: float, y: float, z0: float, z1: float) -> Any:
    return m3.Manifold.cube((x, y, z1 - z0), True).translate((0, 0, (z0 + z1) / 2))


def _mesh(built: Any) -> trimesh.Trimesh:
    made = built.to_mesh()
    return trimesh.Trimesh(
        np.asarray(made.vert_properties)[:, :3], np.asarray(made.tri_verts), process=True
    )


FRAME = _mesh(_box(40, 40, 0, 3) - _box(POCKET, POCKET, 0.8, 2.0) - _box(OPENING, OPENING, -1, 4))
#: A flush pane where it sits: its body 1 mm on the 0.8 mm lip, 0.25 mm of play round it, and a
#: raised face 0.8 mm tall down through the opening, 0.25 mm of play round that too.
BODY, FACE = _box(POCKET - 0.5, POCKET - 0.5, 0.8, 1.8), _box(OPENING - 0.5, OPENING - 0.5, 0, 0.8)
PLACED = _mesh(BODY + FACE)
#: The same pane laid for the bed: its body down, its raised face up.
LAID = _mesh((BODY + FACE).mirror((0, 0, 1)).translate((0, 0, 1.8)))
#: The flush declaration `pieces build --flush` gives.
ENTRY = {
    "frame": "frame.step", "panes": ["pane_blue.step"],
    "laid": {"pane_blue.step": "pane_blue-laid.step"}, "pocket_z": 1.3, "clearance": 0.25,
    "pane_thickness": 1.0, "face_thickness": 0.8, "face_clearance": 0.25,
}  # fmt: skip
PART = ["frame.step", "pane_blue.step", "pane_blue-laid.step"]


class Box:
    """A box whose bench lays each file on the bed as the mesh given by its name."""

    def __init__(self, meshes: dict[str, trimesh.Trimesh]) -> None:
        self.meshes = meshes

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        return laid(args, where, self.meshes)


def _work(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    for name in PART:
        (work / name).write_text("x")
    return work


def _flush(tmp_path: Path) -> standing.Glass:
    found = standing.read([ENTRY], PART, _work(tmp_path))
    assert isinstance(found, list), found
    return found[0]


def test_a_flush_frame_is_declared_with_each_panes_laid_file_and_both_thicknesses(
    tmp_path: Path,
) -> None:
    one = _flush(tmp_path)
    assert one.flush and one.prints_from() == {"pane_blue.step": "pane_blue-laid.step"}
    assert (one.pane_thickness, one.face_thickness, one.face_clearance) == (1.0, 0.8, 0.25)
    assert one.files() == PART
    assert standing.load([one.plain()]) == [one], "kept and read back the same"
    capture = one.commands()[standing.HOLD]
    assert capture.endswith("--raised-faces pane_blue.step --face-play 0.25"), capture
    assert one.commands()[standing.INSERT].endswith("--pane-thickness 1"), "the body's"
    assert standing.placed_panes([ENTRY]) == {"pane_blue.step"}


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"laid": {}}, "`laid` names no file to print `pane_blue.step` from"),
        ({"laid": {"pane_blue.step": "references/x.step"}}, "a given file: a laid file is what"),
        ({"laid": {"pane_blue.step": "pane_blue.step"}}, "for more than one pane, or as a pane"),
        ({"laid": {"other.step": "x.step", "pane_blue.step": "pane_blue-laid.step"}},
         "names `other.step`, which isn't one of its `panes`"),
        ({"face_thickness": None}, "`face_thickness` isn't its raised face's thickness"),
        ({"laid": ["pane_blue-laid.step"]}, "isn't each pane's file with the file it prints"),
    ],
)  # fmt: skip
def test_a_flush_declaration_missing_a_laid_file_or_a_figure_is_refused_saying_which(
    tmp_path: Path, change: dict[str, Any], says: str
) -> None:
    entry = {k: v for k, v in {**ENTRY, **change}.items() if v is not None}
    found = standing.read([entry], PART, _work(tmp_path))
    assert isinstance(found, str) and says in found, found


def test_a_laid_file_left_out_of_glass_is_refused_as_any_file_of_the_part(tmp_path: Path) -> None:
    flat = {k: v for k, v in ENTRY.items() if k not in standing.FLUSH_KEYS}
    found = standing.read([flat], PART, _work(tmp_path))
    assert isinstance(found, str) and "`glass` leaves out `pane_blue-laid.step`" in found


def _steps(placed: tuple[float, float, float], laid_at: tuple[float, float, float]) -> Any:
    """Level faces as `bench/pane_steps.py` reads them: the pane where it sits, from its lowest
    point to its body's underside and its top; laid, from the bed to its body's top and on."""
    low, step, high = placed
    return {
        "pane_blue.step": {"low": low, "high": high, "level": [
            {"z": low, "up": 0.0, "down": 247.0}, {"z": step, "up": 0.0, "down": 133.0},
            {"z": high, "up": 380.0, "down": 0.0}]},
        "pane_blue-laid.step": {"low": laid_at[0], "high": laid_at[2], "level": [
            {"z": laid_at[0], "up": 0.0, "down": 380.0}, {"z": laid_at[1], "up": 133.0,
            "down": 0.0}, {"z": laid_at[2], "up": 247.0, "down": 0.0}]},
    }  # fmt: skip


def test_a_flush_declarations_thicknesses_are_held_to_what_its_files_bear_out(
    tmp_path: Path,
) -> None:
    one = _flush(tmp_path)
    assert standing.steps_wanted([one]) == ["pane_blue.step", "pane_blue-laid.step"]
    assert standing.borne_out([one], _steps((0, 0.8, 1.8), (0, 1.0, 1.8))) == ""
    off = standing.borne_out([one], _steps((0, 0.6, 1.8), (0, 1.0, 1.8)))
    assert (
        "`pane_blue.step`, where it sits in the frame, is a body 1.200 mm thick and a raised "
        "face 0.600 mm, where `glass` says `pane_thickness` 1, the body's"
    ) in off, off
    flat = {"pane_blue.step": {"low": 0, "high": 1.0, "level": [
        {"z": 0, "up": 0, "down": 300.0}, {"z": 1.0, "up": 300.0, "down": 0}]},
        "pane_blue-laid.step": _steps((0, 0.8, 1.8), (0, 1.0, 1.8))["pane_blue-laid.step"]}
    assert "`pane_blue.step`, where it sits in the frame, has no step" in standing.borne_out(
        [one], flat
    )  # fmt: skip
    gone = standing.borne_out([one], {"pane_blue.step": {"unread": "no such file"}})
    assert "`pane_blue.step` couldn't be read for its steps: no such file" in gone
    # A flat frame has nothing to read, so nothing is asked of the bench.
    assert standing.steps(None, [_flat()], tmp_path) == {}


def _flat() -> standing.Glass:
    return standing.Glass("frame.step", ("pane_blue.step",), 1.3, 0.25, 1.0)


def test_a_flat_frame_is_declared_kept_and_checked_as_on_main() -> None:
    """Its kept form, and the commands its checks run, word for word as before."""
    one = _flat()
    assert not one.flush and one.files() == ["frame.step", "pane_blue.step"]
    assert one.plain() == {
        "frame": "frame.step", "panes": ["pane_blue.step"], "with": [], "pocket_z": 1.3,
        "clearance": 0.25, "pane_thickness": 1.0,
    }  # fmt: skip
    assert one.commands() == {
        standing.LIGHT: "light frame.step pane_blue.step",
        standing.HOLD: (
            "capture frame.step --pocket-z 1.3 --clearance 0.25 --pane-thickness 1 --panes 1"
        ),
        standing.INSERT: "pause frame.step --pocket-z 1.3 --pane-thickness 1",
    }
    assert standing.load([one.plain()]) == [one]


def test_a_stepped_pane_declared_flat_fails_light_naming_it() -> None:
    """A flush pane declared as a flat one, with no laid file: `light` reads it as stepped, and
    the standing reading doesn't pass, though `light` itself said OK."""
    numbers = {"stepped": ["pane_blue.step"], "least_open_pct": 60.0}
    said = standing._stepped_flat(standing.LIGHT, _flat(), numbers)
    assert "`pane_blue.step` is stepped, a body over a raised face" in said
    assert "declare it flush, each pane with its laid file" in said
    assert standing._stepped_flat(standing.HOLD, _flat(), numbers) == ""
    flush = standing.Glass("frame.step", ("pane_blue.step",), 1.3, 0.25, 1.0,
                           laid=(("pane_blue.step", "pane_blue-laid.step"),), face_thickness=0.8,
                           face_clearance=0.25)  # fmt: skip
    assert standing._stepped_flat(standing.LIGHT, flush, numbers) == ""
    assert standing._stepped_flat(standing.LIGHT, _flat(), {"least_open_pct": 60.0}) == ""


def test_a_flush_panes_print_readings_are_its_laid_files_and_the_frames_its_panes_in_place(
    tmp_path: Path,
) -> None:
    one = _flush(tmp_path)
    box: Any = Box({"frame.step": FRAME, "pane_blue.step": PLACED, "pane_blue-laid.step": LAID})
    plain = {name: SAID for name in PART}
    frame, placed, laid_file = downward.read(
        box, PART, {}, tmp_path / "work", tmp_path / "kept", plain, [one]
    )
    assert frame.passed and frame.numbers["panes_in_place"] == 1, frame.why
    assert laid_file.passed and laid_file.verdict == "NONE", laid_file.why
    assert placed.passed and placed.numbers["prints_from"] == "pane_blue-laid.step"
    assert placed.why.startswith("a flush pane, read as it prints, from its laid file")
    assert placed.command == "downward faces of pane_blue.step, as it prints: pane_blue-laid.step"


def test_a_laid_file_turned_raised_face_down_fails_support_naming_the_pane(tmp_path: Path) -> None:
    """The control: the reading still bites. A laid file that's the pane the wrong way up, its
    raised face down, as it sits in the frame, needs support, and so does the pane."""
    one = _flush(tmp_path)
    box: Any = Box({"frame.step": FRAME, "pane_blue.step": PLACED, "pane_blue-laid.step": PLACED})
    plain = {name: SAID for name in PART}
    _, placed, laid_file = downward.read(
        box, PART, {}, tmp_path / "work", tmp_path / "kept", plain, [one]
    )
    assert not laid_file.passed and laid_file.verdict == "NEEDS SUPPORT", laid_file.why
    assert not placed.passed and placed.file == "pane_blue.step"
    assert "from its laid file `pane_blue-laid.step`" in placed.why
    assert "would need support" in placed.why
    # And the pane read alone where it sits, as a frame declared flat would have it.
    alone = downward.reading("pane_blue.step", "-z", PLACED, SAID)
    assert not alone.passed and alone.numbers["to_support_mm2"] > 100


def test_the_bench_reads_a_flush_declarations_steps_in_one_call(tmp_path: Path) -> None:
    one = _flush(tmp_path)
    asked: list[tuple[str, list[str]]] = []

    class Steps:
        def helper(self, module: str, args: list[str], timeout: int = 120,
                   where: Path | None = None) -> Ran:  # fmt: skip
            asked.append((module, args))
            from print_shop.bench.pane_steps import MARK

            return Ran(0, "noise\n" + MARK + json.dumps(_steps((0, 0.8, 1.8), (0, 1, 1.8))), 0.1)

    found = standing.steps(Steps(), [one], tmp_path)
    assert asked == [("pane_steps", ["pane_blue.step", "pane_blue-laid.step"])]
    assert isinstance(found, dict) and standing.borne_out([one], found) == ""
