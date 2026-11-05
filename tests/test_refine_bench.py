"""The refinement pass's geometry in the bench's image (`bench/refine.py`), with no Orca.

Meshing for Orca, held to OCCT, and the pass's own reading of lines against a part: a stretch
with no line, and wall lines laid over each other. The lines are made here, as Orca would lay
them, so the right answers come from arithmetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import trimesh

from print_shop.bench import host
from print_shop.bench.refine import ANGULAR, LINEAR, MARK
from print_shop.rounds.box import Box

pytestmark = pytest.mark.bench

STEP = """\
import cadquery as cq
plate = cq.Workplane("XY").box(20, 10, 2, centered=False)
plate = plate.faces(">Z").workplane().center(5, 5).hole(4)
cq.exporters.export(plate, "plate.step")
"""


def _ask(work: Path, request: dict[str, Any]) -> dict[str, Any]:
    ran = Box(work).helper("refine", [json.dumps(request)], timeout=300)
    assert MARK in ran.output, ran.output[-2000:]
    answer: dict[str, Any] = json.loads(ran.output.split(MARK, 1)[1].splitlines()[0])
    return answer


@pytest.fixture
def work(tmp_path: Path) -> Path:
    if host.image_id() is None:
        pytest.skip(f"no bench image {host.IMAGE}")
    return tmp_path


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Any:
    made = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
    made.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    return made


def test_a_step_file_is_meshed_at_orcas_own_defaults_and_held_to_occt(work: Path) -> None:
    (work / "make.py").write_text(STEP)
    made = host.run(["make.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    _box(0, 0, 0, 5, 5, 1).export(work / "block.stl")
    answer = _ask(
        work,
        {
            "request": "mesh",
            "files": ["plate.step", "block.stl"],
            "out": "m",
            "linear": LINEAR,
            "angular": ANGULAR,
        },
    )
    plate = answer["files"]["plate.step"]
    assert (plate["linear_mm"], plate["angular_rad"]) == (0.003, 0.5)
    assert plate["closed"] and plate["solids"] == 1 and plate["curved_faces"] == 1
    # 20 x 10 x 2 less a 4 mm hole, by arithmetic; the mesh within its chords of OCCT's.
    wanted = 400 - 3.14159265 * 4 * 2
    assert plate["occt_volume_mm3"] == pytest.approx(wanted, rel=1e-6)
    assert plate["mesh_volume_mm3"] == pytest.approx(wanted, rel=1e-3)
    assert plate["mesh_box_mm"] == [[0, 0, 0], [20, 10, 2]]
    (outline,) = plate["outline"]
    assert outline["area"] == pytest.approx(200 - 3.14159265 * 4, rel=1e-3)
    assert len(outline["holes"]) == 1
    assert (work / plate["stl"]).is_file()
    block = answer["files"]["block.stl"]
    assert block["meshed"] == "an STL, copied as it is" and block["triangles"] == 12


def _layer(lines: list[list[float]]) -> dict[str, Any]:
    return {"n": 1, "z": 0.2, "height": 0.2, "lines": lines}


def test_a_stretch_with_no_line_at_the_edge_is_bare_and_infills_gaps_inside_arent(
    work: Path,
) -> None:
    _box(0, 0, 0, 10, 2, 0.2).export(work / "strip.stl")
    # Four lines along the strip from x 0 to 6: x 6-10 has none.
    strip = [[0, y, 6, y, 0.5, 1] for y in (0.25, 0.75, 1.25, 1.75)]
    _box(0, 0, 0, 10, 10, 0.2).export(work / "square.stl")
    # A wall round the edge and nothing inside: sparse infill's gaps are like this.
    ring = [
        [0.25, 0.25, 9.75, 0.25, 0.5, 1],
        [9.75, 0.25, 9.75, 9.75, 0.5, 1],
        [9.75, 9.75, 0.25, 9.75, 0.5, 1],
        [0.25, 9.75, 0.25, 0.25, 0.5, 1],
    ]
    for name, lines in (("strip", strip), ("square", ring)):
        (work / f"{name}.json").write_text(json.dumps({"layers": [_layer(lines)]}))
    bare = _ask(
        work, {"request": "cover", "stl": "strip.stl", "offset": [0, 0, 0], "lines": "strip.json"}
    )["bare"]
    (piece,) = bare
    assert piece["box"][0][0] == pytest.approx(6.1, abs=0.02)
    assert piece["width_mm"] == pytest.approx(2.0, abs=0.1)
    inside = _ask(
        work, {"request": "cover", "stl": "square.stl", "offset": [0, 0, 0], "lines": "square.json"}
    )
    assert inside["bare"] == []


def test_two_walls_laid_over_each_other_are_said_and_walls_side_by_side_arent(
    work: Path,
) -> None:
    _box(0, 0, 0, 10, 10, 0.2).export(work / "square.stl")
    over = [[1, 5, 9, 5, 0.42, 1], [9, 5.08, 1, 5.08, 0.42, 1]]
    beside = [[1, 5, 9, 5, 0.42, 1], [9, 5.4, 1, 5.4, 0.45, 1]]
    infill = [[1, 5, 9, 5, 0.42, 0], [9, 5.08, 1, 5.08, 0.42, 0]]
    found = {}
    for name, lines in (("over", over), ("beside", beside), ("infill", infill)):
        (work / f"{name}.json").write_text(json.dumps({"layers": [_layer(lines)]}))
        found[name] = _ask(
            work,
            {"request": "cover", "stl": "square.stl", "offset": [0, 0, 0], "lines": f"{name}.json"},
        )["overlaps"]
    (pair,) = found["over"]
    assert pair["overlap_mm"] == pytest.approx(0.34, abs=1e-3)
    assert pair["length_mm"] == pytest.approx(8, abs=0.1)
    assert found["beside"] == [] and found["infill"] == []


def test_params_keeps_what_the_changed_run_made(work: Path) -> None:
    (work / "cube.py").write_text(
        "import trimesh\nSIZE = 2.0\n"
        "trimesh.creation.box(extents=(SIZE, SIZE, 1)).export('cube.stl')\n"
    )
    ran = host.run(["params", "cube.py", "--set", "SIZE=3", "--keep", "kept"], work)
    assert "verdict: OK" in ran.stdout, ran.stdout
    kept: Any = trimesh.load(work / "kept" / "cube.stl")
    assert [round(float(v), 6) for v in kept.extents] == [3.0, 3.0, 1.0]
    assert not (work / "cube.stl").exists()
    again = host.run(["params", "cube.py", "--set", "SIZE=4", "--keep", "kept"], work)
    assert "has to be a new folder" in again.stdout
