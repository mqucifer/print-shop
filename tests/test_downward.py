"""A part that prints without supports has its downward faces read at each stop, by the print
loop's own overhang reading (mqucifer/print-shop#147).

No Docker: a scripted model drives each run, and a box answers each bench command and runs
nothing. The bench's laying helper is answered with meshes made here: a tray with a flat ledge in
from its rim, and the same with the ledge's underside sloped at 45 degrees. `test_bench.py` reads
the same two through the real helper, in the image.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import manifold3d as m3
import numpy as np
import pytest
import trimesh

from print_shop.rounds import downward, needs
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from print_shop.setup import features
from tests import test_print_side
from tests.rounds_fakes import Scripted, laid, tool
from tests.test_holding import replies

TRAY = """\
A small tray for keys, with a ledge in from its rim to hold a lid.

## Requirements
- The tray is one piece.
- It prints on its base, open side up, with no supports.

## Stops
- no plan
"""
SIDE = {
    **test_print_side.SIDE,
    "quote": "It prints on its base, open side up, with no supports.",
}
NEEDS = [test_print_side.ONE_PIECE, SIDE]
SAID = 'the request says "It prints on its base, open side up, with no supports."'


def _box(x: float, y: float, z0: float, z1: float) -> Any:
    return m3.Manifold.cube((x, y, z1 - z0), True).translate((0, 0, (z0 + z1) / 2))


def tray(sloped: bool) -> trimesh.Trimesh:
    """A tray 40 x 40 x 20 mm, its walls and floor 2 mm, with a ledge 3 mm in from its walls all
    round, its top at the rim and its underside at z 16: flat, or sloped at 45 degrees down to
    the wall at z 13. The flat one is 36 x 36 less 30 x 30, 396 mm², of ceiling."""
    hollow = _box(36, 36, 2, 13 if sloped else 16) + _box(30, 30, 15.99, 21)
    if sloped:
        square = m3.CrossSection.square((36, 36), True)
        taper = m3.Manifold.extrude(square, 3, scale_top=(30 / 36, 30 / 36))
        hollow += taper.translate((0, 0, 13))
    built = (_box(40, 40, 0, 20) - hollow).to_mesh()
    return trimesh.Trimesh(
        np.asarray(built.vert_properties)[:, :3], np.asarray(built.tri_verts), process=True
    )


LEDGE, SLOPED = tray(False), tray(True)


# The reading


def test_a_flat_ledge_is_read_and_a_45_degree_underside_passes() -> None:
    flat = downward.reading("tray.step", "-z", LEDGE, SAID)
    assert not flat.passed and flat.failed and flat.verdict == "NEEDS SUPPORT"
    # The print loop's own figure, and where: the ledge's underside, in the file's coordinates.
    area, _ = features.overhang_to_support(LEDGE)
    assert flat.numbers["to_support_mm2"] == pytest.approx(area, abs=0.01)
    assert 300 < area <= 396.0
    (place,) = flat.numbers["places"]
    assert place["box"][0][2] == place["box"][1][2] == 16.0
    assert place["flat_mm2"] == place["area_mm2"] and place["steepest_deg"] == 90.0
    assert flat.why.startswith(f"lying on its -z side, {area:.1f} mm² looks down past 45°")
    assert f"And {SAID}: slope each of these faces to 45° or steeper" in flat.why
    sloped = downward.reading("tray.step", "-z", SLOPED, SAID)
    assert sloped.passed and sloped.verdict == "NONE"
    assert sloped.numbers["to_support_mm2"] == 0.0 and sloped.numbers["places"] == []
    assert sloped.why == (
        "lying on its -z side, no face looks down past 45° off the plate: nothing would need "
        "support."
    )


def test_with_nothing_said_of_supports_it_is_a_reading_that_passes() -> None:
    read = downward.reading("tray.step", "-z", LEDGE, "")
    assert read.passed and read.verdict == "NEEDS SUPPORT"
    assert "would need support" in read.why and "slope each" not in read.why


def test_where_is_said_in_the_files_own_coordinates_whichever_side_it_lies_on() -> None:
    """Turned over, the tray's floor is a ceiling over its hollow: its inside face, z 2 as the
    file is stored."""
    over = LEDGE.copy()
    over.apply_transform(
        trimesh.transformations.rotation_matrix(np.pi, (1, 0, 0))  # type: ignore[no-untyped-call]
    )
    read = downward.reading("tray.step", "+z", over, "")
    assert read.numbers["side"] == "+z"
    assert [p["box"][0][2] for p in read.numbers["places"]] == [2.0]
    assert "lying on its +z side" in read.why


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("The channel prints open side up with no supports.", True),
        ("It prints without any supports.", True),
        ("It needs no support material.", True),
        ("A support-free print.", True),
        ("It must support a 6 kg shelf.", False),
        ("There's no support for the shelf's far end.", False),
        ("Supports are fine under the arm.", False),
    ],
)
def test_the_words_that_say_no_supports(text: str, says: bool) -> None:
    assert bool(downward.said_no_supports(text)) is says


def test_which_files_print_without_supports_by_the_request_or_by_a_need() -> None:
    files = ["tray.step", "lid.step"]
    by_request = downward.unsupported(TRAY, [], files)
    assert by_request == {"tray.step": SAID, "lid.step": SAID}
    plain = "A tray and its lid.\n"
    (lid,), _ = needs.read(
        [
            {
                "id": "lid-flat",
                "what": "the lid prints flat with no supports",
                "source": "chosen",
                "check": {"by": "bed", "file": "lid.step", "side": "+z"},
            }
        ],
        plain,
    )
    assert downward.unsupported(plain, [lid], files) == {
        "lid.step": 'the need `lid-flat` says "the lid prints flat with no supports"'
    }
    assert downward.sides([lid]) == {"lid.step": "+z"}
    assert downward.sides([lid], {"lid.step": "-z", "tray.step": "-x"}) == {
        "lid.step": "-z",
        "tray.step": "-x",
    }


# At the stops


class Box(test_print_side.Box):
    """Every bench command OK, and the tray laid on the bed as the mesh given."""

    mesh: trimesh.Trimesh = LEDGE

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "refine":
            return laid(args, where, {"tray.step": self.mesh})
        return super().helper(module, args, timeout, where)


def _run(root: Path, mesh: trimesh.Trimesh, *turns: Any) -> Run:
    model = Scripted(*[[t] for t in turns], [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", TRAY, [], model, sleep=lambda _: None)
    for name in ("make.py", "tray.step"):
        (run.work / name).write_text("x")
    box: Any = Box(run.work)
    box.mesh = mesh
    run.box = box
    run.tools.box = box
    return run


DONE = {**test_print_side.DONE, "on_bed": {"tray.step": "-z"}}
ASK = tool("ask", question="Is the ledge right?", variants=[{"name": "A", "files": ["tray.step"]}])


def test_a_flat_ledge_is_a_check_that_doesnt_pass_at_an_ask_and_at_done(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        LEDGE,
        test_print_side.BROKEN_DOWN,
        tool("needs", needs=NEEDS),
        ASK,
        tool("send_ask", says="The tray, its ledge in from the rim."),
    )
    assert run.drive().name == "ask"
    stop = (run.folder / "asks" / "1" / "question.md").read_text()
    assert f"## {downward.HEADING}" in stop
    assert "- tray.step, NEEDS SUPPORT: lying on its -z side, " in stop
    listed = stop.split("## Checks that don't pass on these files")[1]
    assert "- `downward faces of tray.step, on its -z side`: lying on its -z side" in listed
    (row,) = run.ledger.rows("SELECT * FROM standing WHERE check_name='downward'")
    assert (row["stop"], row["file"], row["passed"], row["verdict"]) == (
        "ask", "tray.step", 0, "NEEDS SUPPORT"
    )  # fmt: skip


def test_done_isnt_met_while_a_part_that_prints_without_supports_needs_them(
    tmp_path: Path,
) -> None:
    run = _run(
        tmp_path,
        LEDGE,
        test_print_side.BROKEN_DOWN,
        tool("needs", needs=NEEDS),
        tool("done", **DONE),
    )
    assert run.drive().name == "blocked"
    done = replies(run)[2]
    assert done["outcome"] == "ok" and done["detail"]["met"] is False, done["reply"]
    reply = done["reply"]
    assert "Every need passed, and the harness's own checks on the files to be printed" in reply
    assert f"{downward.HEADING}:\n- tray.step, NEEDS SUPPORT: lying on its -z side" in reply
    kept = json.loads((run.folder / "rounds" / "001" / "standing.json").read_text())
    (read,) = kept["downward"]
    assert read["passed"] is False and read["numbers"]["no_supports"] == SAID
    assert read["numbers"]["places"][0]["box"][0][2] == 16.0


def test_a_sloped_ledge_is_met_and_the_approval_stop_shows_the_reading(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        SLOPED,
        test_print_side.BROKEN_DOWN,
        tool("needs", needs=NEEDS),
        tool("done", **DONE),
    )
    assert run.drive().name == "met"
    done = replies(run)[2]
    assert done["detail"]["met"] is True, done["reply"]
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"## {downward.HEADING}" in stop
    assert (
        "- tray.step, ok: lying on its -z side, no face looks down past 45° off the plate: "
        "nothing would need support."
    ) in stop
    assert stop.index("by `thin`, whatever the needs say") < stop.index(downward.HEADING)
