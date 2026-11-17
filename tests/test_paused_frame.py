"""Two readings of a paused stained-glass frame that read it wrong (mqucifer/print-shop#153): the
top lips, which print over the panes after the pause, as ceilings over air; and the pane count,
taken from how many pane files there are. No Docker: each frame is made here, and each answer is
arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import manifold3d as m3
import numpy as np
import trimesh

from print_shop.rounds import downward, needs, standing
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from tests.rounds_fakes import laid

SAID = 'the request says "It prints flat, with no supports."'
#: A frame 40 x 40 x 3 mm with one pocket 20 x 20 from z 0.8 to 2, and an opening 16 x 16 through
#: it: a lip 2 mm wide all round on both faces. The top lip's underside is 20² - 16², 144 mm²,
#: and the print loop's reading carries the 0.4 mm of it beside the pocket's wall, a 45 degree
#: slope from the layer below: 19.2² - 16², 112.64 mm², is over air.
POCKET, OPENING, LIP_MM2 = 20.0, 16.0, 19.2**2 - 16.0**2


def _box(x: float, y: float, z0: float, z1: float, at: tuple[float, float] = (0, 0)) -> Any:
    return m3.Manifold.cube((x, y, z1 - z0), True).translate((at[0], at[1], (z0 + z1) / 2))


def _mesh(built: Any) -> trimesh.Trimesh:
    made = built.to_mesh()
    return trimesh.Trimesh(
        np.asarray(made.vert_properties)[:, :3], np.asarray(made.tri_verts), process=True
    )


FRAME = _mesh(_box(40, 40, 0, 3) - _box(POCKET, POCKET, 0.8, 2.0) - _box(OPENING, OPENING, -1, 4))
#: Its pane, 0.25 mm of play all round and 0.2 mm under the pocket's top.
PANE = _mesh(_box(POCKET - 0.5, POCKET - 0.5, 0.8, 1.8))


def test_a_frame_alone_reads_its_top_lip_as_a_ceiling_and_doesnt_pass() -> None:
    """The control: the same frame without its panes fails as it should."""
    read = downward.reading("frame.step", "-z", FRAME, SAID)
    assert not read.passed and read.verdict == "NEEDS SUPPORT"
    assert abs(read.numbers["to_support_mm2"] - LIP_MM2) < 1.0
    assert "panes_in_place" not in read.numbers


def test_a_paused_frames_lips_print_over_its_panes_and_pass() -> None:
    read = downward.reading("frame.step", "-z", FRAME, SAID, PANE, 1)
    assert read.passed and read.verdict == "NONE", read.why
    assert read.numbers["to_support_mm2"] < downward.LEAST_MM2
    assert read.numbers["panes_in_place"] == 1
    assert abs(read.numbers["without_panes_mm2"] - LIP_MM2) < 1.0
    assert "with its 1 pane(s) in place from the pause up, as `pause` has them" in read.why
    assert "of lip prints over them" in read.why


def test_a_ceiling_no_pane_is_under_still_needs_support() -> None:
    """A pane carries only what's right over it: a ledge elsewhere on the frame still reads."""
    ledged = _mesh(
        _box(40, 40, 0, 3)
        - _box(POCKET, POCKET, 0.8, 2.0)
        - _box(OPENING, OPENING, -1, 4)
        - _box(6, 6, 0.8, 2.0, at=(-16, -16))
    )
    read = downward.reading("frame.step", "-z", ledged, SAID, PANE, 1)
    assert not read.passed
    alone = (6 - 2 * 0.4) ** 2
    assert abs(read.numbers["to_support_mm2"] - alone) < 1.0, "the 6 x 6 pocket's ceiling alone"


class Box:
    """A box whose bench lays each file on the bed as the mesh given by its name."""

    def __init__(self, meshes: dict[str, trimesh.Trimesh]) -> None:
        self.meshes = meshes
        self.asked: list[dict[str, Any]] = []

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        self.asked.append(json.loads(args[0]))
        return laid(args, where, self.meshes)


def _glass(panes: list[str], bodies: int | None = None) -> standing.Glass:
    return standing.Glass("frame.step", tuple(panes), 1.3, 0.25, 1.0, bodies=bodies)


def test_a_stop_reads_a_declared_frame_with_its_panes_and_each_pane_alone(tmp_path: Path) -> None:
    work, kept = tmp_path / "work", tmp_path / "kept"
    work.mkdir()
    for name in ("frame.step", "pane_blue.step"):
        (work / name).write_text("x")
    box: Any = Box({"frame.step": FRAME, "pane_blue.step": PANE})
    files = ["frame.step", "pane_blue.step"]
    plain = {name: SAID for name in files}
    frame, pane = downward.read(box, files, {}, work, kept, plain, [_glass(["pane_blue.step"])])
    assert frame.passed and frame.numbers["panes_in_place"] == 1
    assert pane.passed and "panes_in_place" not in pane.numbers
    turned = box.asked[0]["on_bed"]
    assert set(turned.values()) == {"-z"} and len(turned) == 3, "the pane again, as its frame lies"
    # With no frame declared, it's read alone, as any file is.
    (alone, _) = downward.read(box, files, {}, work, tmp_path / "kept2", plain)
    assert not alone.passed
    # On its edge it isn't printed with a pause: read alone.
    (edge, _) = downward.read(
        box, files, {"frame.step": "+x"}, work, tmp_path / "kept3", plain,
        [_glass(["pane_blue.step"])],
    )  # fmt: skip
    assert "panes_in_place" not in edge.numbers


# The pane count


def _step(solids: int) -> str:
    """A STEP file's text with that many solids, as OpenCascade writes each one."""
    body = "".join(
        f"#{10 * k} = MANIFOLD_SOLID_BREP('',#{10 * k + 1});\n" for k in range(1, solids + 1)
    )
    return f"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\n{body}ENDSEC;\nEND-ISO-10303-21;\n"


def _declared(work: Path, panes: dict[str, int]) -> standing.Glass:
    work.mkdir(exist_ok=True)
    (work / "frame.step").write_text(_step(1))
    for name, solids in panes.items():
        (work / name).write_text(_step(solids))
    entry = {
        "frame": "frame.step", "panes": list(panes), "pocket_z": 1.3, "clearance": 0.25,
        "pane_thickness": 1.0,
    }  # fmt: skip
    found = standing.read([entry], ["frame.step", *panes], work)
    assert isinstance(found, list), found
    return found[0]


def test_a_capture_over_two_pane_files_holding_five_panes_reads_five(tmp_path: Path) -> None:
    glass = _declared(tmp_path / "work", {"pane_blue.step": 3, "pane_gold.step": 2})
    assert glass.count() == 5
    assert glass.commands()[standing.HOLD].endswith("--panes 5")
    # Kept and read back as it was counted.
    assert standing.load([glass.plain()])[0].count() == 5


def test_one_file_holding_one_pane_reads_one(tmp_path: Path) -> None:
    """The control: a file a pane, as every frame before, counts as it did."""
    glass = _declared(tmp_path / "work", {"pane_1.step": 1})
    assert glass.count() == 1 and glass.commands()[standing.HOLD].endswith("--panes 1")
    several = _declared(tmp_path / "more", {"pane_1.step": 1, "pane_2.step": 1, "pane_3.step": 1})
    assert several.count() == 3


def test_a_file_that_cant_be_counted_counts_as_one_pane_a_file(tmp_path: Path) -> None:
    work = tmp_path / "work"
    glass = _declared(work, {"pane_1.step": 1})
    (work / "pane_1.step").write_text("not a STEP file")
    assert standing.bodies_in(work, "pane_1.step") is None
    assert standing.pane_bodies(work, ["pane_1.step"]) is None
    assert standing.Glass("frame.step", ("a.step", "b.step"), 1.3, 0.25, 1.0).count() == 2
    assert glass.count() == 1


def test_an_stl_pane_file_counts_its_separate_bodies(tmp_path: Path) -> None:
    boxes = [trimesh.creation.box(extents=(2, 2, 1)) for _ in range(3)]
    for k, one in enumerate(boxes):
        one.apply_translation((5 * k, 0, 0))
    joined = trimesh.Trimesh(
        np.vstack([one.vertices for one in boxes]),
        np.vstack([one.faces + 8 * k for k, one in enumerate(boxes)]),
        process=False,
    )
    joined.export(str(tmp_path / "panes.stl"))
    assert standing.bodies_in(tmp_path, "panes.stl") == 3


def test_a_settled_pane_count_follows_the_bodies_not_the_files() -> None:
    """Six colour files holding 52 panes: a need settled at `--panes 52` stays 52. It was
    rewritten to 6, the number of files, and failed."""
    files = [f"pane_{c}.step" for c in ("blue", "gold", "green", "red", "sky", "white")]
    hold = Need(
        "held", "every pane is held", "request", "bench", quote="each pane held by its lips",
        command="capture frame.step --pocket-z 1.3 --clearance 0.25 --pane-thickness 1 --panes 52",
    )  # fmt: skip
    same, said = needs.on_part(hold, [_glass(files, bodies=52)])
    assert same.command == hold.command and said == ""
    # A part that grows to 58 panes in the same six files moves the count, and nothing else.
    grown, said = needs.on_part(hold, [_glass(files, bodies=58)])
    assert grown.command.endswith("--panes 58") and "--panes 58" in said
    # Where the files couldn't be counted, a file is a pane, as it was.
    by_files, _ = needs.on_part(hold, [_glass(files)])
    assert by_files.command.endswith("--panes 6")
