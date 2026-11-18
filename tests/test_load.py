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

from print_shop.bench import assembly, dovetail, host, through
from print_shop.bench.load import MIDS, beyond, bolt_pulls, element_forces
from print_shop.bench.report import NoAnswer
from print_shop.rounds import needs, reports, skills, toolsets

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
        ({"joints": [{"between": ["a", "b"], "kind": "touching", "nut": "m5-nut"}]}, "only a bolt"),
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


# A through-bolt, its nut and its washers (mqucifer/print-shop#168)

M5 = {"between": ["a", "b"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
      "washer": "m5-washer", "at": [[60, 10, 5]]}  # fmt: skip


def test_a_nut_s_and_a_washer_s_sizes_are_the_standards_with_their_source() -> None:
    """ISO 4032:2012's table 1 for a hexagon regular nut: M5 is 8 mm across its flats at
    most, 8.79 across its corners at least, 4.7 tall at most, and bears on a face at least 6.9
    across. ISO 7089:2000 for a plain washer: M5's has a hole of 5.3, and is 10 across and 1
    thick. M3 to M8, each naming its standard."""
    nut = assembly.NUTS["m5-nut"]
    assert (nut.flats_mm, nut.corners_mm, nut.height_mm, nut.bears_mm) == (8.0, 8.79, 4.7, 6.9)
    assert "ISO 4032:2012" in nut.source and "table 1" in nut.source
    washer = assembly.WASHERS["m5-washer"]
    assert (washer.hole_mm, washer.outer_mm, washer.thick_mm) == (5.3, 10.0, 1.0)
    assert "ISO 7089:2000" in washer.source
    flats = {n.thread: (n.flats_mm, n.height_mm) for n in assembly.NUTS.values()}
    assert flats == {"m3": (5.5, 2.4), "m4": (7, 3.2), "m5": (8, 4.7), "m6": (10, 5.2),
                     "m8": (13, 6.8)}  # fmt: skip
    outer = {w.thread: (w.outer_mm, w.thick_mm) for w in assembly.WASHERS.values()}
    assert outer == {"m3": (7, 0.5), "m4": (9, 0.8), "m5": (10, 1), "m6": (12, 1.6),
                     "m8": (16, 1.6)}  # fmt: skip
    for thread, width in assembly.DIAMETER.items():
        nut, washer = assembly.NUTS[f"{thread}-nut"], assembly.WASHERS[f"{thread}-washer"]
        # A nut can't turn in a pocket narrower than its corners, and bears inside its flats;
        # a washer's hole clears its bolt.
        assert width < nut.bears_mm < nut.flats_mm < nut.corners_mm
        assert width < washer.hole_mm < washer.outer_mm


def test_a_through_joint_names_its_bolt_its_nut_and_its_washer(tmp_path: Path) -> None:
    joint = assembly.read(written(tmp_path, joints=[M5])).joints[0]
    assert joint.kind == "through" and joint.insert is None and joint.pocket is None
    assert (joint.bolt.name, joint.nut.name, joint.washer.name) == (  # type: ignore[union-attr]
        "m5-8.8",
        "m5-nut",
        "m5-washer",
    )
    assert joint.at == ((60.0, 10.0, 5.0),)
    # What its bolt holds is the bolt's own proof load: nothing here is an insert's.
    assert joint.holds() == (pytest.approx(580 * 14.2), f"the bolt m5-8.8: {joint.bolt.source}")  # type: ignore[union-attr]
    pocketed = assembly.read(written(tmp_path, joints=[{**M5, "pocket": 0.2}])).joints[0]
    assert pocketed.pocket == 0.2


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"nut": "m6-nut"}, "the nut m6-nut doesn't go on the bolt m5-8.8"),
        ({"bolt": "m4-8.8"}, "the nut m5-nut doesn't go on the bolt m4-8.8"),
        ({"washer": "m8-washer"}, "the washer m8-washer isn't the bolt m5-8.8's size"),
        ({"nut": None}, "`nut` is one of m3-nut"),
        ({"washer": "m5"}, "`washer` is one of m3-washer"),
        ({"at": []}, "where each bolt is"),
        ({"insert": "m5-heat-set"}, "goes into a nut, not an insert"),
        ({"pull_n": 300, "source": "made up"}, "what it holds is read"),
        ({"pocket": -0.1}, "`pocket` is the play"),
        ({"pocket": True}, "`pocket` is the play"),
        ({"kind": "bolted", "insert": "m5-heat-set"}, "a bolt with a nut is a `through` joint"),
    ],
)
def test_a_through_joint_of_the_wrong_sizes_is_refused(
    tmp_path: Path, changed: dict[str, Any], said: str
) -> None:
    """A bolt and a nut of different threads, or a washer of another size, is refused before
    anything is read, as a bolt and an insert that don't match are."""
    with pytest.raises(NoAnswer, match=said):
        assembly.read(written(tmp_path, joints=[{**M5, **changed}]))


def test_what_petg_takes_pressed_depends_on_the_way_to_its_layers() -> None:
    """The sheet has no compressive figure, so its figures for a pull stand in, and the source
    says so: 45 MPa pressed across the layers, the low end of its tensile yield printed flat,
    and 14 MPa along them, the low end of its interlayer adhesion."""
    petg = assembly.MATERIALS["petg"]
    x, z = np.array([1.0, 0, 0]), Z
    assert petg.press(z, z) == (45.0, "across its layers")
    assert petg.press(-z, z) == (45.0, "across its layers")
    assert petg.press(x, z) == (14.0, "along its layers")
    assert petg.press((x + 0.9 * z) / np.linalg.norm(x + 0.9 * z), z)[0] == 14.0
    assert "no compressive figure" in petg.press_source
    assert "47 ± 2 MPa" in petg.press_source and "18 ± 4 MPa" in petg.press_source


def cylinder(radius: float, low: float, high: float, hole: bool = True, **more: Any) -> dict:  # type: ignore[type-arg]
    return {"axis": Z, "origin": np.array([20.0, 20, 0]), "radius": radius, "hole": hole,
            "low": low, "high": high, "turns": 2 * np.pi, **more}  # fmt: skip


def test_a_bolt_s_hole_is_the_round_hole_its_place_is_on() -> None:
    """A 5.5 mm hole from z 0 to 8 under a counterbore to 12: the bolt's shank is in the
    narrower. A place 0.2 mm off its axis, a boss, or a round face that doesn't close isn't a
    hole for it."""
    faces = [cylinder(2.75, 0, 8), cylinder(5.0, 8, 12), cylinder(15.0, 0, 12, hole=False)]
    hole = through.bore(faces, [20, 20, 8])
    assert isinstance(hole, dict)
    assert (hole["radius"], hole["low"], hole["high"]) == (2.75, 0, 8)
    assert "no round hole" in through.bore(faces, [20.2, 20, 8])
    assert "no round hole" in through.bore(faces[2:], [20, 20, 8])
    half = [cylinder(2.75, 0, 8, turns=np.pi)]
    assert "doesn't go all the way round" in through.bore(half, [20, 20, 8])
    # Two halves of one hole are one hole.
    assert isinstance(through.bore(half * 2, [20, 20, 8]), dict)
    crossed = [*faces, {**cylinder(2.75, 0, 40), "axis": np.array([1.0, 0, 0]),
                        "origin": np.array([0.0, 20, 8])}]  # fmt: skip
    assert "more than one axis" in through.bore(crossed, [20, 20, 8])


def test_two_holes_are_one_way_through_where_they_meet_end_to_end() -> None:
    """The first part's hole from z 0 to 8 and the second's from 8 to 20: the bolt runs from
    its head's seat at 0 to its nut's at 20. Named the other way round, the axis turns over.
    Holes 1 mm apart, or side by side, aren't one way through."""
    lower, upper = cylinder(2.75, 0, 8), cylinder(2.75, 8, 20)
    way = through.meet(lower, upper)
    assert isinstance(way, dict)
    assert way["axis"] == pytest.approx(Z) and way["meet"] == 8
    assert way["seats"] == (0, 20) and way["lengths"] == (8, 12)
    back = through.meet(upper, lower)
    assert isinstance(back, dict)
    assert back["axis"] == pytest.approx(-Z) and back["seats"] == (-20, 0)
    assert back["lengths"] == (12, 8)
    # The second part's own faces may say its axis the other way.
    flipped = {**upper, "axis": -Z, "low": -20, "high": -8}
    assert through.meet(lower, flipped)["seats"] == (0, 20)  # type: ignore[index]
    assert "1.000 mm apart" in through.meet(lower, cylinder(2.75, 9, 20))
    aside = {**upper, "origin": np.array([21.0, 20, 0])}
    assert "aren't on one axis" in through.meet(lower, aside)


def walls(flats: float, sides: int = 6, floor: float = 8.0, top: float = 12.0) -> list[dict]:  # type: ignore[type-arg]
    """A pocket's flat walls round the axis through (20, 20), as rows: each facing the axis
    from half of `flats` away, standing from `floor` to `top`."""
    found = []
    for k in range(sides):
        angle = k * np.pi / 3
        out = np.array([np.cos(angle), np.sin(angle), 0.0])
        found.append({"normal": -out, "at": np.array([20.0, 20, 10]) + flats / 2 * out,
                      "low": floor, "high": top})  # fmt: skip
    return found


def test_a_nut_s_pocket_is_the_walls_that_face_each_other_across_the_hole() -> None:
    """Six walls 4.2 mm from the axis are a hex pocket 8.4 mm across its flats, 4 mm deep:
    0.2 mm of play a side round an M5 nut's 8 mm. Three of them in a row have no two facing
    each other, and hold nothing; walls that don't stand on the hole's end aren't its pocket."""
    hole, nut = cylinder(2.75, 0, 8), assembly.NUTS["m5-nut"]
    found = through.pocket(walls(8.4), hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(found, dict)
    assert found["across_flats_mm"] == pytest.approx(8.4) and found["depth_mm"] == 4.0
    assert (found["walls"], found["pairs"]) == (6, 3)
    assert found["off_middle_mm"] == pytest.approx(0, abs=1e-9)
    assert through.nut_fits(found, nut, 0.2) == []
    # A slot: two walls facing each other, and its closed end.
    slot = [w for k, w in enumerate(walls(8.4)) if k in (1, 3, 4)]
    held = through.pocket(slot, hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(held, dict) and (held["walls"], held["pairs"]) == (3, 1)
    none = through.pocket(walls(8.4, sides=2), hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(none, str) and "2 flat wall(s)" in none and "none faces another" in none
    higher = through.pocket(walls(8.4, floor=9.0), hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(higher, str) and "0 flat wall(s)" in higher


@pytest.mark.parametrize(
    ("flats", "stated", "said"),
    [
        (8.4, 0.2, []),
        (8.0, 0.0, []),
        (7.9, 0.2, ["the nut doesn't go in", "the assembly file states 0.2"]),
        (8.79, 0.395, ["the nut turns in it"]),
        (9.0, 0.5, ["the nut turns in it"]),
        (8.4, 0.3, ["its play is 0.200 mm a side"]),
    ],
)
def test_a_pocket_fits_where_the_nut_goes_in_cant_turn_and_the_play_is_the_file_s(
    flats: float, stated: float, said: list[str]
) -> None:
    """An M5 nut is 8 mm across its flats at most and 8.79 across its corners at least (ISO
    4032): a pocket narrower than 8 doesn't take it, and one 8.79 or wider lets it turn."""
    nut = assembly.NUTS["m5-nut"]
    found = through.pocket(walls(flats), cylinder(2.75, 0, 8), 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(found, dict)
    wrong = through.nut_fits(found, nut, stated)
    assert len(wrong) == len(said), wrong
    for words, line in zip(said, wrong, strict=True):
        assert words in line


# A joint's force, shared between its bolts


def pulls(force: list[float], moment: list[float], bolts: list[list[float]]) -> list[float]:
    found = bolt_pulls(
        np.array(force, dtype=float), np.array(moment, dtype=float), np.zeros(3), Z,
        np.array(bolts, dtype=float), FACE,
    )  # fmt: skip
    return [b["pull_n"] for b in found]

# A dovetail: its joint in the file, and its faces pushing only (mqucifer/print-shop#170)

DOVETAIL = {"between": ["a", "b"], "kind": "dovetail", "slides": "-z"}
LATCH = {"on": "a", "arm": [[-3, -1, 10], [3, 2, 30]], "root": "z=30"}
#: A tapered tail's flank, as the planted one leans: 2 mm wider at its far face, 6 mm in, and
#: 2 mm narrower over its 40 mm length. Its share along the slide is the taper's slope.
FLANK = np.array([240.0, 80.0, -12.0]) / np.linalg.norm([240.0, 80.0, -12.0])
DOWN = np.array([0.0, 0.0, -1.0])


def test_a_dovetail_joint_names_the_way_its_tail_slides_in_and_its_latch(tmp_path: Path) -> None:
    """The tail's part is named first and the socket's second. `slides` is the way the tail
    goes in, toward its seat. A latch is the part its tab is on, a box round the tab and the
    plane of its root, as `bend` takes an arm."""
    joint = assembly.read(written(tmp_path, joints=[DOVETAIL])).joints[0]
    assert (joint.kind, joint.between, joint.slides, joint.latch) == (
        "dovetail", ("a", "b"), "-z", None,
    )  # fmt: skip
    assert joint.slides_way() == pytest.approx([0, 0, -1])
    assert joint.holds() is None, "nothing bought holds it"
    latched = assembly.read(written(tmp_path, joints=[{**DOVETAIL, "latch": LATCH}])).joints[0]
    assert latched.latch == assembly.Latch("a", ((-3, -1, 10), (3, 2, 30)), ("z", 30.0))
    # The box's corners in either order.
    turned = {**LATCH, "arm": [[3, 2, 30], [-3, -1, 10]]}
    again = assembly.read(written(tmp_path, joints=[{**DOVETAIL, "latch": turned}])).joints[0]
    assert again.latch == latched.latch


@pytest.mark.parametrize(
    ("joint", "said"),
    [
        ({"between": ["a", "b"], "kind": "dovetail"}, "`slides` is the way the tail, on a"),
        ({**DOVETAIL, "slides": "z"}, r"one of \+x, -x, \+y, -y, \+z, -z"),
        ({**DOVETAIL, "slides": "down"}, "toward its seat"),
        # Glue channels are the parts' shape: no field gives glue, or anything else, a figure.
        ({**DOVETAIL, "glue": "epoxy"}, "gives glue: a dovetail gives `slides`"),
        ({**DOVETAIL, "glue_n": 200}, "no field gives glue, a bolt or the joint a figure"),
        ({**DOVETAIL, "pull_n": 200, "source": "a test"}, "gives pull_n, source"),
        ({**DOVETAIL, "bolt": "m5-8.8"}, "gives bolt"),
        ({**DOVETAIL, "bonded": True}, "gives bonded"),
        # And glue can't stand in for it: a glued joint doesn't slide in, or latch.
        ({"between": ["a", "b"], "kind": "bonded", "slides": "-z"}, "never read as glued"),
        ({"between": ["a", "b"], "kind": "bonded", "latch": LATCH}, "only a dovetail slides in"),
        ({"between": ["a", "b"], "kind": "touching", "slides": "-z"}, "never read as touching"),
        ({**DOVETAIL, "latch": "yes"}, "`latch` is"),
        ({**DOVETAIL, "latch": {**LATCH, "holds_n": 50}}, "`latch` is"),
        ({**DOVETAIL, "latch": {**LATCH, "on": "c"}}, "latch is `on` a or b, not c"),
        ({**DOVETAIL, "latch": {**LATCH, "arm": [[0, 0, 0]]}}, "`latch` is"),
        ({**DOVETAIL, "latch": {**LATCH, "arm": [[-3, -1, 10], [3, -1, 30]]}}, "no room inside"),
        ({**DOVETAIL, "latch": {**LATCH, "root": "z=20"}}, "isn't a side of its `arm`"),
        ({**DOVETAIL, "latch": {**LATCH, "root": "w=30"}}, "`root` axis is one of"),
    ],
)  # fmt: skip
def test_a_dovetail_joint_that_gives_anything_else_is_refused(
    tmp_path: Path, joint: dict[str, Any], said: str
) -> None:
    with pytest.raises(NoAnswer, match=said):
        assembly.read(written(tmp_path, joints=[joint]))


def test_petg_s_density_is_the_datasheet_s_with_its_table() -> None:
    """A part's own weight is its solid volume at this: 1.27 g/cm³, ISO 1183, from the
    sheet's "Typical material properties"."""
    petg = assembly.MATERIALS["petg"]
    assert petg.density_g_cm3 == 1.27
    for said in (
        "Prusament PETG's datasheet",
        "version 1.1, 16-02-2022",
        '"Typical material properties"',
        '"Density [g/cm3] 1.27"',
        "ISO 1183",
    ):
        assert said in petg.density_source, said


def test_a_face_is_a_seat_or_a_flank_by_the_way_the_tail_slides_in() -> None:
    assert dovetail.kind(DOWN, DOWN) == "seat", "square to the slide: it stops the tail"
    assert dovetail.kind(FLANK, DOWN) == "flank", "a taper's flank leans into the way in"
    assert dovetail.kind([0, -1, 0], DOWN) == "flank", "along the slide"
    assert dovetail.kind([0, 0, 1], DOWN) == "against", "it faces the way out"
    assert dovetail.kind(FLANK * [1, 1, -1], DOWN) == "against", "a taper pointing the other way"


def tail(seat: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A tapered tail's faces in small pieces of 1 mm²: its two flanks, the floor of its
    socket under its far face, and with `seat` its tip, square to the slide."""
    along, deep = np.meshgrid(np.linspace(30, 70, 21), np.linspace(-6, 0, 7))
    centres, normals = [], []
    for side in (1.0, -1.0):
        across = side * (3 + (along - 30) / 20 - deep / 3)
        centres.append(np.column_stack([across.ravel(), deep.ravel(), along.ravel()]))
        normals.append(np.tile(FLANK * [side, 1, 1], (along.size, 1)))
    across, along = np.meshgrid(np.linspace(-6, 6, 7), np.linspace(30, 70, 21))
    centres.append(np.column_stack([across.ravel(), np.full(across.size, -6.0), along.ravel()]))
    normals.append(np.tile([0.0, -1.0, 0.0], (across.size, 1)))
    if seat:
        across, deep = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-6, 0, 7))
        centres.append(np.column_stack([across.ravel(), deep.ravel(), np.full(across.size, 30.0)]))
        normals.append(np.tile(DOWN, (across.size, 1)))
    both = np.vstack(centres)
    return both, np.vstack(normals), np.ones(len(both))


def bedded(faces: Any, force: list[float]) -> tuple[dict[str, Any], np.ndarray]:
    centres, normals, areas = faces
    about = np.array([0.0, -3.0, 50.0])
    found = dovetail.bed(centres, normals, areas, np.array(force), np.zeros(3), about)
    return found, found["pressure"] * areas


def test_a_weight_that_seats_a_taper_is_held_by_its_flanks_as_a_wedge() -> None:
    """A weight W on the tail's part pushes the tail down its taper, so the socket pushes it
    back up with W. With no seat under its tip, only the flanks have a share that way: each
    is pushed square to itself by W over twice the taper's slope, 12/253.3, so about 10.6 W.
    Every push is a push, and together they're what the joint carries."""
    faces = tail()
    found, push = bedded(faces, [0, 0, 49.0])
    # The weak spring that takes what no face can holds a millionth of the weight here.
    assert found["holds"] and np.linalg.norm(found["left_n"]) < 1e-3
    assert (found["pressure"] >= 0).all()
    slope = -float(FLANK @ [0, 0, 1])
    assert slope == pytest.approx(12 / np.linalg.norm([240, 80, 12]))
    flanks = push[:147].sum(), push[147:294].sum()
    assert flanks == pytest.approx([49.0 / (2 * slope)] * 2, rel=1e-5)
    assert -(push[:, None] * faces[1]).sum(axis=0) == pytest.approx([0, 0, 49.0], abs=1e-3)
    # The flanks' undercut pulls the tail into its socket, and the socket's floor pushes back.
    assert push[294:].sum() == pytest.approx(2 * flanks[0] * FLANK[1], rel=1e-5)


def test_a_seat_square_to_the_slide_takes_the_weight_off_the_taper() -> None:
    """Each bit of face pushes back in proportion to how far the tail presses into it, and a
    shift down the slide presses a seat by all of it and a flank by the taper's slope of it.
    So a seat of 49 mm² takes nearly all the weight, and the flanks a few percent."""
    found, push = bedded(tail(seat=True), [0, 0, 49.0])
    assert found["holds"]
    seat, flanks = push[-49:].sum(), push[:294].sum()
    assert 0.9 * 49 < seat < 49
    assert 0 < flanks < 0.1 * 49 / (12 / np.linalg.norm([240, 80, 12]))


def test_a_load_that_draws_the_tail_out_is_left_over_as_no_face_can_pull() -> None:
    """Pulled back up the way it came in, no face of the tail pushes that way, seat or no
    seat: the whole pull is left over, and it's said as not held."""
    for seat in (False, True):
        found, push = bedded(tail(seat), [0, 0, -49.0])
        assert not found["holds"]
        assert found["left_n"] == pytest.approx([0, 0, -49.0], abs=1e-3)
        assert push.sum() == pytest.approx(0, abs=1e-3), "nothing pushes"


def test_a_pull_off_the_socket_is_held_by_the_undercut_and_squeezes_a_taper_out() -> None:
    """Pulled off its socket, +y, the tail is held by its flanks' undercut. But a tapered
    flank's push has a share up the slide too, and with no friction counted nothing pushes
    back against that: it's left over, along the way out. With the weight seating it as well,
    more than that share, the joint holds."""
    found, push = bedded(tail(seat=True), [0, -30.0, 0])
    assert not found["holds"]
    assert push[:294].sum() > 0 and found["left_n"][2] < -1, "squeezed out, +z"
    assert abs(found["left_n"][2]) == pytest.approx(push[:294].sum() * 12 / 253.3, rel=0.02)
    seated, _ = bedded(tail(seat=True), [0, -30.0, 49.0])
    assert seated["holds"]


def test_a_latch_is_one_more_face_that_pushes_the_tail_home() -> None:
    """A latch's catch is a face of the tail's part that looks back along the way in. Added
    to the tail's own, it holds exactly what draws the tail out."""
    centres, normals, areas = tail(seat=True)
    catch = (np.array([[0.0, 0.3, 12.0]]), np.array([[0.0, 0.0, 1.0]]), np.array([3.0]))
    every = [np.concatenate([a, b]) for a, b in zip((centres, normals, areas), catch,
                                                    strict=True)]  # fmt: skip
    about = np.array([0.0, 0.3, 12.0])
    found = dovetail.bed(every[0], every[1], every[2], np.array([0, 0, -4.9]), np.zeros(3), about)
    assert found["holds"]
    assert found["pressure"][-1] * 3.0 == pytest.approx(4.9, rel=1e-3)


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


def test_a_dovetail_s_numbers_have_the_shape_a_need_reads() -> None:
    """A need names a dovetail's figures by their path, such as
    `joints.upper+lower.latch.holds_n`, so the shapes say what's there."""
    tab = {"across_at": [0, 0, 0], "across_layers_pct": 0.2, "elements": 9, "mesh_mm": 0.6,
           "stretch_at": [0, 0, 0], "stretch_is": "pull", "stretch_pct": 0.5}  # fmt: skip
    face = {"kind": "seat", "at": [0, 0, 0], "normal": [0, 0, -1], "area_mm2": 48.0,
            "push_n": 46.0, "bears_mm2": 48.0, "pressure_mpa": 1.0, "pressure_limit_mpa": 14.0,
            "pressed": "along its layers", "pressed_part": "lower"}  # fmt: skip
    joint = {"kind": "dovetail", "slides": "-z", "drive_n": 49.0, "opens": False,
             "latched": True, "left_n": [0, 0, 0], "left_nmm": [0, 0, 0], "faces": [face],
             "latch": {"on": "upper", "catch_mm2": 3.0, "catch_at": [0, 0, 12], "play_mm": 0.2,
                       "holds_n": 5.0, "pressure_mpa": 1.7, "pressure_limit_mpa": 14.0,
                       "tab": tab},
             "neck": tab, "walls": tab}  # fmt: skip
    numbers = {"joints": {"upper+lower": joint}, "lift": "upper"}
    assert reports.unlike("load", numbers) == []
    assert reports.wrong_path("load", "joints.upper+lower.latch.holds_n") is None
    assert reports.wrong_path("load", "joints.upper+lower.drive_n") is None
    assert reports.wrong_path("load", "joints.upper+lower.neck.across_layers_pct") is None
    wrong = {"joints": {"upper+lower": {**joint, "faces": [{**face, "glue_n": 9.0}]}}}
    assert "glue_n" in reports.unlike("load", wrong)[0]


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


def test_a_lift_need_has_no_weight_to_quote() -> None:
    """Lifted by its top, what hangs is the parts' own weight, which the bench works out
    (mqucifer/print-shop#170): a `lift` need gives `--lift` and no figure. A `load` need
    still gives the request's weight."""

    def read(entry: dict[str, Any]) -> list[str]:
        return needs.read([entry], REQUEST, "", FURNITURE)[1]

    assert read(need("load table.json --lift top", "lift")) == []
    assert "`lift` is read by `load` with `--lift`" in read(
        need("load table.json --weight 5 --at 200,200,520", "lift")
    )[0]
    assert "a `load` need gives its limit, `--weight`" in read(
        need("load table.json --lift top", "load")
    )[0]
    lift = FURNITURE.measure("lift")
    assert lift is not None and not lift.limited


def test_the_skill_says_how_to_split_and_gives_each_check_its_command() -> None:
    body = skills.registry(skills.ALL)["designing-furniture"][1]
    for said in (
        "Split along the load's path, not across it",
        "Put each joint where the load is low",
        "Lay each part so its layers run along its pull",
        "never a printed thread",
        "Never change the weight",
        # mqucifer/print-shop#168: through-bolts hold a printed assembly, and glue doesn't.
        "A printed assembly is held mechanically, never by glue",
        "Don't choose `bonded` to hold printed parts together",
        "`through` where it takes load",
        "A through joint is never `OK` on its bolt alone",
        '"kind": "through", "bolt": "m5-8.8", "nut": "m5-nut"',
        # mqucifer/print-shop#170: a part split for the bed, its dovetail, its latch.
        "a part split for the bed joins with a tapered dovetail",
        "The taper points so the weight seats it",
        "Glue channels are a second hold only",
        "A latch holds the lift",
        "A leg prints on its side, with its curve in the bed's plane",
        '"kind": "dovetail", "slides": "-z"',
        "a dovetail is never named `bonded`",
        "`load assembly.json --lift top`",
    ):
        assert said in body, said
    for measure in FURNITURE.measures:
        if measure.name in ("load", "lift", "bed-fit"):
            assert f"| `{measure.name}` | `{measure.commands[0]} " in body, measure.name
    for term in ("load path", "layers", "split", "joint", "seat", "tail", "latch", "assembly file"):
        assert f"- **{term}**:" in body, term
