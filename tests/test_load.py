"""`load` and `fit-bed` outside the images (mqucifer/print-shop#156): the assembly file, the
arithmetic that shares a joint's force between its bolts, statics across a joint, and the
furniture toolset. No Docker, and no model. The solve itself is in `test_cae.py`, and `fit-bed`
on planted parts in `test_bench.py`.
"""

from __future__ import annotations

import json
import math
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


def test_a_triangle_across_the_stretchs_end_is_loaded_over_the_part_between() -> None:
    """mqucifer/print-shop#245: 12 mm² from z 0 to 6. Between 0 and 6 it's all there, with its
    own middle; cut at 3, the trapezium of 9 under and the 3 over; past it, none."""
    corners = [np.array([0.0, 0, 0]), np.array([4.0, 0, 0]), np.array([0.0, 0, 6])]
    area, middle = through.between(corners, Z, 0, 6)
    assert area == 12 and middle == pytest.approx([4 / 3, 0, 2])
    under, low = through.between(corners, Z, -10, 3 - through.SAME)
    assert under == pytest.approx(9) and low == pytest.approx([14 / 9, 0, 4 / 3])
    over, high = through.between(corners, Z, 3 + through.SAME, 10)
    assert over == pytest.approx(3) and high == pytest.approx([2 / 3, 0, 4])
    assert through.between(corners, Z, 7, 9)[0] == 0
    assert through.between(corners, Z, -9, -1)[0] == 0
    assert through.between(corners, -Z, -6, 0)[0] == 12, "the axis the other way"


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


def wall(
    start: tuple[float, float], end: tuple[float, float], floor: float = 8.0, top: float = 12.0
) -> dict:  # type: ignore[type-arg]
    """An upright wall from `start` to `end` as a row with its face's four corners, the open
    side on the left of the way from one to the other."""
    run = np.subtract(end, start)
    low = [np.array([*p, floor]) for p in (start, end)]
    high = [np.array([*p, top]) for p in (start, end)]
    return {"normal": np.array([-run[1], run[0], 0.0]) / np.linalg.norm(run),
            "at": (low[0] + high[1]) / 2, "low": floor, "high": top,
            "corners": np.array([*low, *high])}  # fmt: skip


def nut_slot(x: float, y: float, wide: float, long: float = 16.0) -> list[dict]:  # type: ignore[type-arg]
    """A closed slot round (x, y), `long` along x and `wide` along y: its four walls."""
    a, b, c, d = x - long / 2, x + long / 2, y - wide / 2, y + wide / 2
    round_it = [(a, c), (b, c), (b, d), (a, d)]
    return [wall(round_it[k], round_it[(k + 1) % 4]) for k in range(4)]


@pytest.mark.parametrize("step", [0.0, 0.5])
@pytest.mark.parametrize("narrow", [None, 0, 1, 2])
def test_a_pocket_s_walls_are_only_those_whose_faces_are_at_the_hole(
    step: float, narrow: int | None
) -> None:
    """mqucifer/print-shop#204: three slots 70 mm apart along their walls, each 0.15 mm a side
    over an M5 nut, in line or each 0.5 mm off the last. Each is its own four walls, whichever
    way the axis is read, and a slot too narrow is the only one the nut doesn't go into."""
    nut = assembly.NUTS["m5-nut"]
    places = [(20.0 + 70 * k, 20 + step * k) for k in range(3)]
    wide = [7.9 if k == narrow else 8.3 for k in range(3)]
    planes = [w for p, across in zip(places, wide, strict=True) for w in nut_slot(*p, across)]
    for k, (x, y) in enumerate(places):
        hole = {**cylinder(2.75, 0, 8), "origin": np.array([x, y, 0.0])}
        found = through.pocket(planes, hole, 8.0, Z, 2 * nut.corners_mm, nut.corners_mm / 2)
        assert isinstance(found, dict)
        assert found["walls"] == 4 and found["across_flats_mm"] == pytest.approx(wide[k])
        wrong = through.nut_fits(found, nut, 0.15)
        assert any("the nut doesn't go in" in w for w in wrong) is (k == narrow), wrong
        assert (wrong == []) is (k != narrow)
        assert {tuple(np.round(f["at"][:2], 6)) for f in found["facing"]} == {
            (x, round(y - wide[k] / 2, 6)), (x, round(y + wide[k] / 2, 6)),
            (x - 8, y), (x + 8, y),
        }  # fmt: skip
        # The same part, the bolt's way out along -z: the seat is its level that way.
        over = [{**w, "at": w["at"] * [1, 1, -1], "corners": w["corners"] * [1, 1, -1]}
                for w in planes]
        back = through.pocket(over, hole, 8.0, -Z, 2 * nut.corners_mm, nut.corners_mm / 2)
        assert isinstance(back, dict) and back["walls"] == 4
        assert back["across_flats_mm"] == pytest.approx(wide[k])


def test_a_wall_is_at_the_hole_as_far_aside_as_the_nut_reaches() -> None:
    """The point of a wall's plane nearest the axis is (20, 24.2). A wall cut in two by a notch
    over it, and a neck that starts short of it, are walls the nut's corners meet, 4.395 mm
    either side; a neck 4.5 mm aside isn't. With no room given, it's the face's edge itself."""
    nut, hole = assembly.NUTS["m5-nut"], cylinder(2.75, 0, 8)
    reach, aside = 2 * nut.corners_mm, nut.corners_mm / 2
    under = wall((4, 15.8), (36, 15.8))
    notched = [under, wall((36, 24.2), (21.5, 24.2)), wall((18.5, 24.2), (4, 24.2))]
    cut = through.pocket(notched, hole, 8.0, Z, reach, aside)
    assert isinstance(cut, dict) and (cut["walls"], cut["pairs"]) == (3, 2)
    assert cut["across_flats_mm"] == pytest.approx(8.4)

    for start, across in ((22.0, 7.8), (24.39, 7.8), (24.5, 8.4)):
        neck = [under, wall((start, 24.2), (4, 24.2)), wall((36, 23.6), (start, 23.6))]
        found = through.pocket(neck, hole, 8.0, Z, reach, aside)
        assert isinstance(found, dict)
        assert found["across_flats_mm"] == pytest.approx(across), start
        said = any("the nut doesn't go in" in w for w in through.nut_fits(found, nut, 0.2))
        assert said is (across < nut.flats_mm), start
    ends = [wall((20, 15.8), (36, 15.8)), wall((36, 24.2), (20, 24.2))]
    assert isinstance(through.pocket(ends, hole, 8.0, Z, reach), dict)
    short = [wall((20.01, 15.8), (36, 15.8)), wall((36, 24.2), (20.01, 24.2))]
    assert "0 flat wall(s)" in through.pocket(short, hole, 8.0, Z, reach)


def test_a_face_that_comes_over_the_hole_from_a_far_wall_is_still_in_the_nut_s_way() -> None:
    """A far slot's wall that runs on over this hole from 3 mm above its seat, 3.65 mm from the
    axis: one face, with its ends either side of the hole, and the nut doesn't go in past it.
    The same far wall with nothing over the hole isn't this pocket's, and a hole with no pocket
    says the walls it did take."""
    nut, hole = assembly.NUTS["m5-nut"], cylinder(2.75, 0, 8)
    under, far = wall((4, 15.8), (36, 15.8)), wall((80, 23.65), (40, 23.65))
    reach, aside = 2 * nut.corners_mm, nut.corners_mm / 2
    over = [[0.0, 23.65, z] for z in (11.0, 12.0)]
    joined = {**far, "corners": np.array([*far["corners"], *over])}
    found = through.pocket([under, joined], hole, 8.0, Z, reach, aside)
    assert isinstance(found, dict) and found["across_flats_mm"] == pytest.approx(7.85)
    assert any("the nut doesn't go in" in w for w in through.nut_fits(found, nut, 0.2))
    none = through.pocket([under, far], hole, 8.0, Z, reach, aside)
    assert isinstance(none, str) and "1 flat wall(s)" in none
    assert "4.200 mm from the axis toward (0, -1, 0), its face round (20, 15.8, 10)" in none


def sides(across: float, count: int, turn: float = 0.0) -> list[dict]:  # type: ignore[type-arg]
    """A pocket of `count` flat walls round the axis through (20, 20), each `across` / 2 from
    it, the first facing it from `turn` degrees round from x, from z 8 to 12."""
    found = []
    for k in range(count):
        angle = np.radians(turn + k * 360 / count)
        out = np.array([np.cos(angle), np.sin(angle), 0.0])
        found.append({"normal": -out, "at": np.array([20.0, 20, 10]) + across / 2 * out,
                      "low": 8.0, "high": 12.0})  # fmt: skip
    return found


def test_a_nut_goes_in_only_where_its_corners_fit_between_the_walls_in_some_turn() -> None:
    """mqucifer/print-shop#183: eval-table-1's frame pockets were 8.2 mm squares round M5 nuts,
    which hold the nut's 8 mm flats with 0.1 mm of play and passed. The nut is at least 8.79 mm
    across its corners, so in any turn it's at least 8.79 x cos 15° = 8.490 mm wide along one
    side or the other: its corners pass each wall by 0.145, turned 15°. A hex 8.4 across takes
    it square on, and an octagon 8.75 across turned 7.5° off a flat, where a corner reaches
    4.395 x cos 7.5° = 4.357 mm; one 8.6 across doesn't."""
    nut, hole = assembly.NUTS["m5-nut"], cylinder(2.75, 0, 8)
    square = through.pocket(sides(8.2, 4), hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(square, dict) and through.nut_fits(square, nut, 0.1) == []
    found = through.nut_goes_in(square, nut, 0.25)
    assert found["goes_in"] is False and found["least_off_axis_mm"] is None
    assert found["turn_deg"] % 30 == pytest.approx(15, abs=0.01)
    assert len(found["met"]) == 4
    for met in found["met"]:
        assert met["by_mm"] == pytest.approx(8.79 / 2 * math.cos(math.radians(15)) - 4.1, abs=1e-4)
    for across, count, goes in ((8.4, 6, True), (8.0, 6, True), (8.75, 8, True),
                                (8.6, 8, False), (8.5, 4, True)):  # fmt: skip
        walls_ = sides(across, count)
        pocket = through.pocket(walls_, hole, 8.0, Z, 2 * nut.corners_mm)
        assert isinstance(pocket, dict)
        assert through.nut_goes_in(pocket, nut, 0.0)["goes_in"] is goes, (across, count)
    octagon = through.nut_goes_in(
        through.pocket(sides(8.75, 8), hole, 8.0, Z, 2 * nut.corners_mm), nut, 0.0  # type: ignore[arg-type]
    )
    assert octagon["turn_deg"] % 15 == pytest.approx(7.5, abs=0.01)
    assert -octagon["deepest_mm"] > 0


def test_a_nut_goes_in_off_the_axis_only_as_far_as_the_bolt_moves_in_its_hole() -> None:
    """A slot 8.4 mm wide whose closed end is 4.2 mm from the axis, as `bed/pocket-slot` is: with
    its middle on the axis the nut's corners pass all three walls, turned their best, by
    4.395 x cos 15° - 4.2 = 0.045 mm. Moved 0.085 mm toward the open end, which an M5 bolt in a
    5.5 mm hole can, it goes in; a bolt with no room in its hole can't take it there."""
    nut, hole = assembly.NUTS["m5-nut"], cylinder(2.75, 0, 8)
    slot = [s for k, s in enumerate(sides(8.4, 4)) if k != 0]
    pocket = through.pocket(slot, hole, 8.0, Z, 2 * nut.corners_mm)
    assert isinstance(pocket, dict) and pocket["walls"] == 3
    tight = through.nut_goes_in(pocket, nut, 0.0)
    assert tight["goes_in"] is False and len(tight["met"]) == 3
    assert tight["least_off_axis_mm"] == pytest.approx(0.0846, abs=1e-3)
    assert {round(m["by_mm"], 3) for m in tight["met"]} == {0.045}
    loose = through.nut_goes_in(pocket, nut, 0.25)
    assert loose["goes_in"] is True and loose["met"] == []
    assert loose["off_axis_mm"] == pytest.approx(0.0846, abs=1e-3)
    assert loose["deepest_mm"] == pytest.approx(0, abs=1e-6)
    # Toward the open end, +x.
    first, second = (np.array(b) for b in loose["basis"])
    centre = loose["centre"][0] * first + loose["centre"][1] * second
    assert centre[0] == pytest.approx(0.0846, abs=1e-3)


def test_how_far_a_wall_reaches_into_the_nut_is_read_the_same_from_any_segments() -> None:
    """The second method's figure: a straight wall 4.1 mm from the middle of a hexagon 8.79
    across its corners, turned so a corner points at it, cuts that corner off; the deepest
    point of the wall inside is on the corner's line, 4.395 - 4.1 = 0.295 mm in from the
    corner, and cos 30° x that from the sides either side of it."""
    wall = np.array([[[4.1, -3.0], [4.1, 3.0]]])
    depth = through.deepest_in(wall, 8.79, 0.0, (0.0, 0.0))
    assert depth == pytest.approx(0.295 * math.cos(math.radians(30)), abs=1e-9)
    clear = through.deepest_in(wall + [10.0, 0.0], 8.79, 0.0, (0.0, 0.0))
    assert clear < 0


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


# Play (mqucifer/print-shop#188)


def square(
    play: float, latch_at: tuple[float, float] | None = None, latch_play: float = 0.2
) -> Any:
    """A square tail 20 x 20 going 20 mm down into a block 40 x 40, by pieces of 0.25 mm²:
    its tip on the socket's floor at z 20 and the blocks' faces round it at z 40, touching,
    and its four flanks `play` clear of the socket's walls. With `latch_at`, a catch of 3 mm²
    at that x and y, at z 22, that pushes the tail home once its own play has closed. Each
    piece's centre, its way out of the tail, its area and its play, and where each face's
    pieces start."""
    step = 0.5
    centres: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    gaps: list[np.ndarray] = []
    starts: dict[str, int] = {}

    def face(name: str, corner: Any, u: Any, v: Any, size: tuple[float, float],
             normal: Any, gap: float, keep: Any = None) -> None:  # fmt: skip
        a, b = np.meshgrid(np.arange(step / 2, size[0], step), np.arange(step / 2, size[1], step))
        at = np.asarray(corner) + a.ravel()[:, None] * u + b.ravel()[:, None] * v
        if keep is not None:
            at = at[keep(at)]
        starts[name] = sum(len(c) for c in centres)
        centres.append(at)
        normals.append(np.tile(normal, (len(at), 1)))
        gaps.append(np.full(len(at), gap))

    x, y, z = np.eye(3)
    face("tip", [-10, -10, 20], x, y, (20, 20), -z, 0.0)
    face("shoulder", [-20, -20, 40], x, y, (40, 40), -z, 0.0,
         lambda at: np.abs(at[:, :2]).max(axis=1) > 10 + play)  # fmt: skip
    for name, corner, along, normal in (
        ("+x", [10, -10, 20], y, x), ("-x", [-10, -10, 20], y, -x),
        ("+y", [-10, 10, 20], x, y), ("-y", [-10, -10, 20], x, -y),
    ):  # fmt: skip
        face(name, corner, along, z, (20, 20), normal, play)
    if latch_at is not None:
        starts["latch"] = sum(len(c) for c in centres)
        centres.append(np.array([[*latch_at, 22.0]]))
        normals.append(np.array([[0.0, 0.0, 1.0]]))
        gaps.append(np.array([latch_play]))
    areas = np.full(sum(len(c) for c in centres), step**2)
    if latch_at is not None:
        areas[-1] = 3.0
    return np.vstack(centres), np.vstack(normals), areas, np.concatenate(gaps), starts


def by_face(push: np.ndarray, starts: dict[str, int]) -> dict[str, float]:
    ends = [*list(starts.values())[1:], len(push)]
    return {k: float(push[a:b].sum()) for (k, a), b in zip(starts.items(), ends, strict=True)}


ABOUT = np.array([0.0, 0.0, 35.0])


def test_a_tail_with_play_shifts_until_a_flank_closes_and_that_flank_takes_the_push() -> None:
    """Pushed sideways, +x, so the socket pushes back -x, a tail whose flanks are clear by
    0.15 mm can't be held by its seats: they only push square to themselves. So it shifts
    0.15 mm, the +x flank closes, and that flank takes the push. Read with no play, nothing
    along x touches, and it's left over."""
    centres, normals, areas, gaps, starts = square(0.15)
    stiff = dovetail.stiffness(1500, centres, ABOUT)
    found = dovetail.bed(centres, normals, areas, np.array([-20.0, 0, 0]), np.zeros(3), ABOUT,
                         gaps, stiff)  # fmt: skip
    push = by_face(found["pressure"] * areas, starts)
    assert found["holds"]
    assert push["+x"] == pytest.approx(20.0, rel=1e-3) and push["-x"] == 0
    assert 0.15 < found["shift_mm"][0] < 0.16, "its play, and a little more as the flank bears"
    assert found["pressed_mm"][starts["+x"]:starts["-x"]].max() > 0.15
    drawn_tight = dovetail.bed(centres[:starts["+x"]], normals[:starts["+x"]],
                               areas[:starts["+x"]], np.array([-20.0, 0, 0]), np.zeros(3),
                               ABOUT)  # fmt: skip
    assert not drawn_tight["holds"], "today's reading: the flanks don't touch, so don't count"


def test_a_weight_on_a_seat_stays_on_it_and_the_flanks_play_doesnt_close() -> None:
    """Pushed straight down its slide, the tail's tip and the blocks' faces round it stop it
    within a few thousandths of a mm, far short of closing 0.15 mm of play: the weight stays on
    the seats, and the flanks carry nothing."""
    centres, normals, areas, gaps, starts = square(0.15)
    stiff = dovetail.stiffness(1500, centres, ABOUT)
    found = dovetail.bed(centres, normals, areas, np.array([0, 0, 49.0]), np.zeros(3), ABOUT,
                         gaps, stiff)  # fmt: skip
    push = by_face(found["pressure"] * areas, starts)
    assert found["holds"]
    assert push["tip"] + push["shoulder"] == pytest.approx(49.0, rel=1e-6)
    assert all(push[k] == 0 for k in ("+x", "-x", "+y", "-y"))
    assert -0.01 < found["shift_mm"][2] < 0, "down, into its seat"
    assert found["pressed_mm"][starts["+x"]:].max() < 0.01


def test_with_no_play_the_faces_stiffness_changes_nothing() -> None:
    """Every face drawn touching: the pushes are the same whatever stiffness they're read at,
    as they always were."""
    centres, normals, areas = tail(seat=True)
    about = np.array([0.0, -3.0, 50.0])
    force, moment = np.array([3.0, -30.0, 49.0]), np.array([100.0, 0, 20.0])
    plain = dovetail.bed(centres, normals, areas, force, moment, about)
    for stiff in (1.0, 1e4):
        again = dovetail.bed(centres, normals, areas, force, moment, about,
                             np.zeros(len(areas)), stiff)  # fmt: skip
        assert again["pressure"] == pytest.approx(plain["pressure"], rel=1e-6, abs=1e-9)
    with pytest.raises(ValueError, match="stiffness"):
        dovetail.bed(centres, normals, areas, force, moment, about, np.full(len(areas), 0.1))


def test_an_off_centre_latch_holds_the_hanging_weight_once_the_tilt_closes_the_flanks() -> None:
    """mqucifer/print-shop#188's lift: the lower part, 1.5 N, hangs on a latch 14 mm and 15 mm
    off the joint's middle. Read with every face where it's drawn, the latch's hook touching,
    the seat beside the latch levers against it and the latch holds several times the weight.
    Read as a print takes it, the lower drops by the latch's 0.2 mm of play, the seats come
    away, and it tilts on the latch until its flanks close: the latch holds the weight, the
    flanks only the turn. With the flanks 1.5 mm clear, the tilt closes the seat beside the
    latch first, and the lever is back."""
    weight = np.array([0.0, 0.0, -1.5])
    for play, latch_play in ((0.15, 0.0), (0.15, 0.2), (1.5, 0.2)):
        centres, normals, areas, gaps, starts = square(play, (14.0, -15.6), latch_play)
        stiff = dovetail.stiffness(1500, centres, ABOUT)
        moment = np.cross(np.array([0, 0, 40.0]) - ABOUT, weight)
        found = dovetail.bed(centres, normals, areas, weight, moment, ABOUT, gaps, stiff)
        push = by_face(found["pressure"] * areas, starts)
        assert found["holds"]
        flanks = sum(push[k] for k in ("+x", "-x", "+y", "-y"))
        if play == 0.15 and latch_play == 0.2:
            assert push["latch"] == pytest.approx(1.5, rel=1e-3)
            assert push["tip"] == push["shoulder"] == 0, "the seats come away"
            assert flanks > 0
            assert all(found["pressed_mm"][starts[k]:starts[k] + 1600].max() >= 0.15
                       for k in ("+x", "-x", "+y", "-y")), "all four close"  # fmt: skip
        else:
            assert push["latch"] > 3 * 1.5, "levered by the seat beside it"
            assert push["shoulder"] > 2 * 1.5 and flanks < 1e-3 * push["latch"]


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
            "pressed": "along its layers", "pressed_part": "lower", "play_mm": 0.15,
            "closed_mm": 0.15}  # fmt: skip
    joint = {"kind": "dovetail", "slides": "-z", "drive_n": 49.0, "opens": False,
             "latched": True, "left_n": [0, 0, 0], "left_nmm": [0, 0, 0], "faces": [face],
             "shift_mm": [0.1, 0, 0], "turn_deg": [0, 0.4, 0], "faces_move_mm": 0.3,
             "latch": {"on": "upper", "catch_mm2": 3.0, "catch_at": [0, 0, 12], "play_mm": 0.2,
                       "holds_n": 5.0, "pressure_mpa": 1.7, "pressure_limit_mpa": 14.0,
                       "tab": tab},
             "neck": tab, "walls": tab}  # fmt: skip
    numbers = {"joints": {"upper+lower": joint}, "lift": "upper"}
    assert reports.unlike("load", numbers) == []
    assert reports.wrong_path("load", "joints.upper+lower.latch.holds_n") is None
    assert reports.wrong_path("load", "joints.upper+lower.drive_n") is None
    assert reports.wrong_path("load", "joints.upper+lower.neck.across_layers_pct") is None
    assert reports.wrong_path("load", "joints.upper+lower.faces_move_mm") is None
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
