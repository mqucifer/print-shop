"""`load` and `fit-bed` outside the images (mqucifer/print-shop#156): the assembly file, the
arithmetic that shares a joint's force between its bolts, statics across a joint, and the
furniture toolset. No Docker, and no model. The solve itself is in `test_cae.py`, and `fit-bed`
on planted parts in `test_bench.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import assembly, host
from print_shop.bench.load import MIDS, beyond, bolt_pulls, element_forces
from print_shop.bench.report import NoAnswer
from print_shop.rounds import needs, skills, toolsets

FURNITURE = toolsets.FURNITURE
Z = np.array([0.0, 0.0, 1.0])
#: A face 20 x 10 in the plane z 0, its centre at the origin.
FACE = np.array([[-10.0, -5, 0], [10, -5, 0], [10, 5, 0], [-10, 5, 0]])
REQUEST = """A side table.

## Requirements

- It holds 5 kg at the middle of its top.

## Kind

furniture
"""


def written(tmp_path: Path, **changed: Any) -> str:
    """An assembly file of two halves, bolted, with whatever a test changes."""
    for name in ("a.step", "b.step"):
        (tmp_path / name).write_text("")
    data: dict[str, Any] = {
        "parts": [
            {"name": "a", "file": "a.step", "layers": "z"},
            {"name": "b", "file": "b.step", "at": [60, 0, 0], "turn": [["z", 90]], "layers": "x"},
        ],
        "joints": [
            {"between": ["a", "b"], "kind": "bolted", "bolt": "m4-8.8",
             "insert": "m4-heat-set", "at": [[60, 10, 5]]}
        ],
        "held": [{"plane": "x=0", "how": "fixed"}],
    }  # fmt: skip
    data.update(changed)
    path = tmp_path / "assembly.json"
    path.write_text(json.dumps(data))
    return str(path)


# The assembly file


def test_an_assembly_file_gives_parts_joints_and_where_its_held(tmp_path: Path) -> None:
    read = assembly.read(written(tmp_path))
    first, second = read.parts
    assert (first.name, first.layers, first.at, first.material.name) == (
        "a",
        "z",
        (0, 0, 0),
        "petg",
    )
    assert first.layers_way() == pytest.approx([0, 0, 1])
    # Turned a quarter about z, a part's own x points along the assembly's y.
    assert second.layers_way() == pytest.approx([0, 1, 0])
    joint = read.joints[0]
    assert joint.name == "a+b" and joint.kind == "bolted" and joint.at == ((60.0, 10.0, 5.0),)
    assert read.held[0] == assembly.Held("x", 0.0, "fixed")


def test_a_bolt_s_figure_is_its_proof_load_with_its_source(tmp_path: Path) -> None:
    """ISO 898-1: a class 8.8 bolt's proof stress is 580 MPa, and M4's stress area 8.78 mm².
    An insert has no sourced figure, so the bolt's is the joint's, unless the joint gives its
    own, which is then the least."""
    bolt = assembly.BOLTS["m4-8.8"]
    assert bolt.pull_n == pytest.approx(580 * 8.78) and "ISO 898-1" in bolt.source
    assert assembly.BOLTS["m5-a2-70"].pull_n == pytest.approx(450 * 14.2)
    assert all(i.pull_n is None and i.source for i in assembly.INSERTS.values())
    joint = assembly.read(written(tmp_path)).joints[0]
    assert joint.holds() == (bolt.pull_n, f"the bolt m4-8.8: {bolt.source}")
    own = {"between": ["a", "b"], "kind": "bolted", "bolt": "m4-8.8", "insert": "m4-heat-set",
           "at": [[60, 10, 5]], "pull_n": 400, "source": "a pull test"}  # fmt: skip
    held = assembly.read(written(tmp_path, joints=[own])).joints[0].holds()
    assert held == (400.0, "the joint's own figure: a pull test")


def test_a_material_names_where_its_limits_are_from() -> None:
    petg = assembly.MATERIALS["petg"]
    assert (petg.across_pct, petg.along_pct, petg.modulus_mpa) == (1.0, 5.1, 1500.0)
    assert "Prusament PETG's datasheet" in petg.source


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"parts": []}, "has no `parts`"),
        ({"parts": [{"name": "a", "file": "a.step"}]}, "has no `layers`"),
        ({"parts": [{"name": "a", "file": "a.stl", "layers": "z"}]}, "is a STEP file"),
        ({"parts": [{"name": "a", "file": "gone.step", "layers": "z"}]}, "isn't beside"),
        ({"parts": [{"name": "a", "file": "a.step", "layers": "z"}] * 2}, "a name of its own"),
        ({"joints": [{"between": ["a", "c"], "kind": "bonded"}]}, "names the part c"),
        ({"joints": [{"between": ["a", "b"], "kind": "glued"}]}, "is one of bolted, bonded"),
        ({"joints": [{"between": ["a", "b"], "kind": "bonded", "bolt": "m4-8.8"}]}, "only a bolt"),
        ({"joints": [{"between": ["a", "b"], "kind": "bolted", "bolt": "m4-8.8",
                      "insert": "m5-heat-set", "at": [[0, 0, 0]]}]}, "doesn't go into"),
        ({"joints": [{"between": ["a", "b"], "kind": "bolted", "bolt": "m4-8.8",
                      "insert": "m4-heat-set"}]}, "where each bolt is"),
        ({"joints": [{"between": ["a", "b"], "kind": "bolted", "bolt": "m4-8.8",
                      "insert": "m4-heat-set", "at": [[0, 0, 0]], "pull_n": 300}]}, "its `source`"),
        ({"joints": [{"between": ["a", "b"], "kind": "bonded"},
                     {"between": ["b", "a"], "kind": "touching"}]}, "given twice"),
        ({"held": [{"plane": "w=0"}]}, "a held plane's axis is one of"),
        ({"held": [{"plane": "z=0", "how": "glued"}]}, "`how` it's held is one of"),
        ({"held": [{"how": "fixed"}]}, "a plane and how"),
    ],
)  # fmt: skip
def test_an_assembly_file_that_cant_be_read_says_why(
    tmp_path: Path, changed: dict[str, Any], said: str
) -> None:
    with pytest.raises(NoAnswer, match=said):
        assembly.read(written(tmp_path, **changed))


# A joint's force, shared between its bolts


def pulls(force: list[float], moment: list[float], bolts: list[list[float]]) -> list[float]:
    found = bolt_pulls(
        np.array(force, dtype=float), np.array(moment, dtype=float), np.zeros(3), Z,
        np.array(bolts, dtype=float), FACE,
    )  # fmt: skip
    return [b["pull_n"] for b in found]


def test_a_pull_is_shared_between_the_bolts_and_a_push_needs_none() -> None:
    assert pulls([0, 0, 90], [0, 0, 0], [[-5, 0, 0], [5, 0, 0], [0, 3, 0]]) == pytest.approx(
        [30] * 3
    )
    assert pulls([0, 0, -90], [0, 0, 0], [[-5, 0, 0], [5, 0, 0]]) == pytest.approx([0, 0])
    sheared = bolt_pulls(np.array([30.0, 40, 0]), np.zeros(3), np.zeros(3), Z, FACE[:2], FACE)
    assert [b["shear_n"] for b in sheared] == pytest.approx([25, 25])


def test_a_bending_moment_is_held_about_the_face_s_edge_on_the_pushed_side() -> None:
    """A moment of 600 N·mm about y pulls the face's -x side, so the face turns about its edge
    at x +10. One bolt at the middle is 10 mm from it: 60 N.
    Two at x -5 and +5 are 15 and 5 mm from it: 600·15/250 = 36 N and 12 N, which hold 600
    between them. The other way round, the edge is at x -10 and the far bolt is the other."""
    assert pulls([0, 0, 0], [0, 600, 0], [[0, 0, 0]]) == pytest.approx([60])
    two = pulls([0, 0, 0], [0, 600, 0], [[-5, 0, 0], [5, 0, 0]])
    assert two == pytest.approx([36, 12]) and pytest.approx(600) == 36 * 15 + 12 * 5
    assert pulls([0, 0, 0], [0, -600, 0], [[-5, 0, 0], [5, 0, 0]]) == pytest.approx([12, 36])
    # A twist about the faces' normal pulls no bolt.
    assert pulls([0, 0, 0], [0, 0, 600], [[0, 0, 0]]) == pytest.approx([0])


def test_a_push_takes_off_the_moment_and_a_pull_adds_to_it() -> None:
    """A push of 40 N through the middle holds 40 x 10 = 400 N·mm of the 600 about the edge,
    and the bolt the other 200: 20 N. A push of 60 N or more holds it all. A pull of 40 N is the
    bolt's as well as the moment's 60."""
    assert pulls([0, 0, -40], [0, 600, 0], [[0, 0, 0]]) == pytest.approx([20])
    assert pulls([0, 0, -60], [0, 600, 0], [[0, 0, 0]]) == pytest.approx([0])
    assert pulls([0, 0, 40], [0, 600, 0], [[0, 0, 0]]) == pytest.approx([100])


def test_an_element_s_forces_balance_and_do_the_work_its_strain_stores() -> None:
    """One second-order tetrahedron, stretched evenly: 1% along x, shrinking 0.38 of that along
    y and z, which is a plain pull of 15 MPa at 1500 MPa. The forces on its ten nodes add up to
    nothing and turn it no way, and the work they do, Σ f·u, is the stress times the strain
    times its volume. Moved and turned as a whole, it carries nothing."""
    corners = np.array([[0.0, 0, 0], [6, 0, 0], [1, 5, 0], [2, 1, 4]])
    mids = np.array([(corners[i] + corners[j]) / 2 for i, j in MIDS])
    places = np.concatenate([corners, mids])[None]
    stretch = np.diag([0.01, -0.0038, -0.0038])
    forces = element_forces(places, places @ stretch, 1500.0, 0.38)[0]
    assert forces.sum(axis=0) == pytest.approx([0, 0, 0], abs=1e-9)
    assert np.cross(places[0], forces).sum(axis=0) == pytest.approx([0, 0, 0], abs=1e-9)
    volume = abs(np.linalg.det(corners[1:] - corners[0])) / 6
    assert (forces * (places[0] @ stretch)).sum() == pytest.approx(15.0 * 0.01 * volume)
    turn = np.array([[0.0, -1e-3, 0], [1e-3, 0, 0], [0, 0, 0]])
    still = element_forces(places, places @ turn.T + 0.5, 1500.0, 0.38)[0]
    assert still == pytest.approx(np.zeros((10, 3)), abs=1e-9)


# Statics across a joint


def chain(tmp_path: Path, held: str | None = "a", loop: bool = False) -> assembly.Assembly:
    for name in ("a.step", "b.step", "c.step"):
        (tmp_path / name).write_text("")
    joints = [{"between": ["a", "b"], "kind": "bonded"}, {"between": ["b", "c"], "kind": "bonded"}]
    if loop:
        joints.append({"between": ["c", "a"], "kind": "bonded"})
    parts = [{"name": n, "file": f"{n}.step", "layers": "z"} for n in "abc"]
    path = tmp_path / "chain.json"
    path.write_text(json.dumps({"parts": parts, "joints": joints}))
    return assembly.read(str(path))


def test_statics_gives_a_joint_the_load_beyond_it(tmp_path: Path) -> None:
    """a is held, and b and c hang off it in a row. The joint a+b carries both loads, and b+c
    only c's, each with its moment about the joint. With c held too, or the parts joined in a
    ring, statics alone doesn't say."""
    row = chain(tmp_path)
    loads = [
        {"part": "b", "middle": [10.0, 0, 0], "force": [0.0, 0, -3]},
        {"part": "c", "middle": [20.0, 0, 0], "force": [0.0, 0, -5]},
    ]
    centre = np.zeros(3)
    force, moment = beyond(row, row.joints[0], loads, {"a"}, centre)  # type: ignore[misc]
    assert force == pytest.approx([0, 0, -8]) and moment == pytest.approx([0, 130, 0])
    force, moment = beyond(row, row.joints[1], loads, {"a"}, centre)  # type: ignore[misc]
    assert force == pytest.approx([0, 0, -5]) and moment == pytest.approx([0, 100, 0])
    # Held at the far end instead, what the second part puts on the first is the other way.
    force, _ = beyond(row, row.joints[0], loads, {"c"}, centre)  # type: ignore[misc]
    assert force == pytest.approx([0, 0, 0]), "nothing is on a, the free side"
    loads.append({"part": "a", "middle": [0.0, 0, 0], "force": [0.0, 0, -2]})
    force, _ = beyond(row, row.joints[0], loads, {"c"}, centre)  # type: ignore[misc]
    assert force == pytest.approx([0, 0, 2]), "b holds a up against its 2 N"
    assert beyond(row, row.joints[0], loads, {"a", "c"}, centre) is None
    ring = chain(tmp_path, loop=True)
    assert beyond(ring, ring.joints[0], loads, {"a"}, centre) is None


# Where they run, and the toolset


def test_load_runs_in_the_second_image_and_fit_bed_in_the_first() -> None:
    assert host.image_for(["load", "table.json", "--weight", "5"]) == host.CAE_IMAGE
    assert host.image_for(["fit-bed", "table.json"]) == host.IMAGE


def need(command: str, measures: str) -> dict[str, Any]:
    return {
        "id": "it",
        "what": "what it must do",
        "source": "request",
        "quote": "It holds 5 kg at the middle of its top",
        "check": {"by": "bench", "command": command, "measures": measures},
    }


def test_the_furniture_toolset_gives_load_and_fit_bed_and_its_skill() -> None:
    assert toolsets.named("Furniture") is FURNITURE
    assert FURNITURE.added == ("load", "fit-bed") and set(FURNITURE.added) <= set(needs.COMMANDS)
    assert set(toolsets.CORE.commands) < set(FURNITURE.commands)
    assert FURNITURE.required[0] == "designing-furniture"
    assert "designing-furniture" in skills.registry(FURNITURE.skills)
    assert "designing-furniture" not in skills.registry(toolsets.CORE.skills)
    assert toolsets.where("load") is FURNITURE and toolsets.where("fit-bed") is FURNITURE
    assert "`load`, `fit-bed`" in FURNITURE.said() and "`designing-furniture`" in FURNITURE.said()


def test_a_load_need_takes_its_weight_from_the_request() -> None:
    def read(entry: dict[str, Any], toolset: toolsets.Toolset = FURNITURE) -> list[str]:
        return needs.read([entry], REQUEST, "", toolset)[1]

    assert read(need("load table.json --weight 5 --at 200,200,520", "load")) == []
    assert (
        "its limit 2 isn't in its quote"
        in read(need("load table.json --weight 2 --at 200,200,520", "load"))[0]
    )
    assert read(need("fit-bed table.json", "bed-fit")) == []
    assert (
        "`fit-bed` doesn't measure that: `load` does" in read(need("fit-bed table.json", "load"))[0]
    )
    core = read(need("load table.json --weight 5 --at 200,200,520", "load"), toolsets.CORE)
    assert "`load` is in the furniture toolset, and this run's is the core" in core[0]


def test_the_skill_says_how_to_split_and_gives_each_check_its_command() -> None:
    body = skills.registry(skills.ALL)["designing-furniture"][1]
    for said in (
        "Split along the load's path, not across it",
        "Put each joint where the load is low",
        "Lay each part so its layers run along its pull",
        "never a printed thread",
        "Never change the weight",
    ):
        assert said in body, said
    for measure in FURNITURE.measures:
        if measure.name in ("load", "bed-fit"):
            assert f"| `{measure.name}` | `{measure.commands[0]} " in body, measure.name
    for term in ("load path", "layers", "split", "joint", "assembly file"):
        assert f"- **{term}**:" in body, term
