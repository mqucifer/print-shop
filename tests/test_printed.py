"""Specification for `bench/printed.py`: a part's print settings and its regions, the one
format `load` (mqucifer/print-shop#337) and the slice (mqucifer/print-shop#338) both read."""

from __future__ import annotations

import math
from typing import Any

import pytest

from print_shop.bench.printed import (
    ORCA,
    PATTERNS,
    Cylinder,
    Layers,
    Mesh,
    Printed,
    Region,
    Settings,
    as_json,
    own_way,
    read,
)

WHOLE: dict[str, Any] = {
    "walls": 3, "line_width": 0.45, "top": 5, "bottom": 4, "infill": 15, "pattern": "gyroid",
    "infill_angle": 45, "layer_height": 0.2,
    "regions": [
        {"name": "bolt-1", "box": [[0, 0, 0], [20, 12, 8]], "walls": 6, "infill": 60,
         "why": "round the bolt hole, 3 mm margin"},
        {"name": "latch", "box": [[10, 0, 0], [30, 12, 8]], "infill": 40, "pattern": "grid",
         "infill_angle": 0, "top": 8, "bottom": 6},
        {"name": "hole", "cylinder": {"at": [50, 6, 0], "axis": "z", "radius": 5, "length": 8},
         "walls": 5},
        {"name": "face", "mesh": "faces/leg-face.stl", "infill": 40},
        {"name": "tab", "layers": [3, 6], "walls": 4, "layer_height": 0.1},
    ],
}


def test_a_whole_part_reads_and_writes_back_the_same() -> None:
    printed = read(WHOLE)
    assert printed.settings == Settings(walls=3, line_width=0.45, top=5, bottom=4, infill=15.0,
                                        pattern="gyroid", infill_angle=45.0, layer_height=0.2)
    assert [r.name for r in printed.regions] == ["bolt-1", "latch", "hole", "face", "tab"]
    assert [r.kind for r in printed.regions] == ["box", "box", "cylinder", "mesh", "layers"]
    assert printed.regions[0].shape == ((0.0, 0.0, 0.0), (20.0, 12.0, 8.0))
    assert printed.regions[2].shape == Cylinder((50.0, 6.0, 0.0), (0.0, 0.0, 1.0), 5.0, 8.0)
    assert printed.regions[3].shape == Mesh("faces/leg-face.stl")
    assert printed.regions[4].shape == Layers(3.0, 6.0)
    assert printed.regions[4].settings == Settings(walls=4, layer_height=0.1)
    assert printed.regions[0].settings == Settings(walls=6, infill=60.0)
    assert printed.regions[0].why == "round the bolt hole, 3 mm margin"
    assert read(as_json(printed)) == printed


def test_nothing_given_is_the_profiles_own() -> None:
    assert read({}) == Printed()
    assert Printed().at((1.0, 2.0, 3.0)) == Settings()


def test_a_point_takes_the_last_region_that_holds_it_over_the_part() -> None:
    printed = read({**WHOLE, "regions": WHOLE["regions"][:2]})
    # Only bolt-1: its walls and infill, the part's pattern and shells.
    assert printed.at((5, 5, 5)) == Settings(walls=6, line_width=0.45, top=5, bottom=4,
                                             infill=60.0, pattern="gyroid", infill_angle=45.0,
                                             layer_height=0.2)
    # Both: latch is later, so its infill wins, and bolt-1's walls stay where latch says none.
    both = printed.at((15, 5, 5))
    assert (both.walls, both.infill, both.pattern, both.infill_angle, both.top) == (
        6, 40.0, "grid", 0.0, 8)
    # Neither: the part's own.
    assert printed.at((50, 5, 5)) == printed.settings


def test_each_shape_places_a_point() -> None:
    printed = read(WHOLE)
    hole, face, tab = printed.regions[2:]
    assert hole.contains((52, 6, 4)) and not hole.contains((56, 6, 4))
    assert not hole.contains((50, 6, 9))  # past its length
    turned = Region("r", Cylinder((0, 0, 0), (-1.0, 0.0, 0.0), 2.0, 10.0), Settings(walls=3))
    assert turned.contains((-5, 1, 1)) and not turned.contains((5, 0, 0))
    # A layers region is in the part's own axes too, along its layers axis.
    assert tab.contains((0, 0, 4), layers="z") and not tab.contains((0, 0, 7), layers="z")
    assert tab.contains((4, 0, 0), layers="x") and not tab.contains((0, 0, 4), layers="x")
    with pytest.raises(ValueError, match="the part's layers axis is needed"):
        tab.contains((0, 0, 0))
    with pytest.raises(ValueError, match="it's read with its mesh"):
        face.contains((0, 0, 0))
    assert face.contains((0, 0, 0), in_mesh=lambda p: True)


def test_a_volume_region_wins_over_a_layers_region_whatever_their_order() -> None:
    """mqucifer/print-shop#341's review: the hole's cylinder comes before the tab's band of
    layers, and Orca builds the cylinder's 5 walls where they meet, as it applies a height range
    to the object and its modifiers over it."""
    printed = read(WHOLE)
    found = printed.at((50, 6, 4), layers="z", in_mesh={"face": lambda p: False})
    assert (found.walls, found.layer_height) == (5, 0.1)
    # Outside the cylinder, the band's own.
    assert printed.at((5, 30, 4), layers="z", in_mesh={"face": lambda p: False}).walls == 4


def test_a_part_given_whole_misses_nothing() -> None:
    assert read(WHOLE).settings.missing() == []
    assert read({"walls": 3, "infill": 15}).settings.missing() == [
        "line_width", "top", "bottom", "pattern", "infill_angle", "layer_height"]


def test_settings_are_said_in_orcas_own_keys() -> None:
    # The angle is the part's own: only the pose turns it onto the plate.
    assert Settings(walls=6, infill=60.0, pattern="gyroid", infill_angle=0.0, top=8).orca() == {
        "wall_loops": "6", "sparse_infill_density": "60%", "sparse_infill_pattern": "gyroid",
        "top_shell_layers": "8", "top_shell_thickness": "0",
    }
    # One line width is both walls'; shells are layers, exactly.
    assert Settings(line_width=0.45, bottom=4).orca() == {
        "inner_wall_line_width": "0.45", "outer_wall_line_width": "0.45",
        "bottom_shell_layers": "4", "bottom_shell_thickness": "0",
    }
    # Every setting is Orca's but `up`, the way the part stands, which the pose gives.
    assert set(ORCA) == {f for f in Settings.__dataclass_fields__} - {"up"}


@pytest.mark.parametrize("layers,angle,way", [
    ("z", 0, (1, 0, 0)), ("z", 90, (0, 1, 0)),
    ("x", 0, (0, 1, 0)), ("x", 90, (0, 0, 1)),
    ("y", 0, (0, 0, 1)), ("y", 90, (1, 0, 0)),
])
def test_the_infill_angle_is_in_the_parts_own_axes_cyclic_from_its_layers(
    layers: str, angle: float, way: tuple[float, float, float]
) -> None:
    assert own_way(angle, layers) == pytest.approx(way, abs=1e-12)


def test_a_layers_region_is_in_the_parts_own_coordinates_below_zero_too() -> None:
    region = read({"regions": [{"name": "r", "layers": [-4, -1], "infill": 30}]}).regions[0]
    assert region.shape == Layers(-4.0, -1.0)
    assert region.contains((0, -2, 0), layers="y")


def test_two_layers_regions_cant_overlap() -> None:
    with pytest.raises(ValueError, match="layers regions a and b overlap"):
        read({"regions": [{"name": "a", "layers": [0, 4], "infill": 30},
                          {"name": "b", "layers": [3, 6], "infill": 60}]})
    read({"regions": [{"name": "a", "layers": [0, 4], "infill": 30},
                      {"name": "b", "layers": [4, 6], "infill": 60}]})


def test_a_region_says_where_it_is_and_what_it_sets() -> None:
    region = Region("bolt-1", ((0, 0, 0), (20, 12, 8)), Settings(walls=6, infill=60.0), "why")
    assert region.said() == "bolt-1 (why): from (0, 0, 0) to (20, 12, 8), 6 walls, 60% infill"
    said = [r.said() for r in read(WHOLE).regions[2:]]
    assert said == [
        "hole: a cylinder 5 mm in radius and 8 long from (50, 6, 0) along (0, 0, 1), 5 walls",
        "face: the mesh faces/leg-face.stl, 40% infill",
        "tab: the layers from 3 to 6 mm along the part's layers axis, 4 walls, 0.1 mm layers",
    ]


@pytest.mark.parametrize("raw,said", [
    ({"walls": 2.5}, "print's walls is a whole number"),
    ({"walls": -1}, "print's walls is 0 or more"),
    ({"top": "five"}, "print's top is a number"),
    ({"bottom": True}, "print's bottom is a number"),
    ({"infill": 101}, "print's infill is 0 to 100"),
    ({"infill": -1}, "print's infill is 0 to 100"),
    ({"infill": math.nan}, "print's infill is a number"),
    ({"line_width": 0}, "print's line_width is 0.05 to 2"),
    ({"layer_height": 0}, "print's layer_height is 0.01 to 1"),
    ({"infill_angle": math.inf}, "print's infill_angle is a number"),
    ({"pattern": "nonsense"}, "print's pattern is one of Orca's own names"),
    ({"pattern": "zig-zag"}, "print's pattern is one of Orca's own names"),
    ({"pattern": "monotonic"}, "print's pattern is one of Orca's own names"),
    ({"density": 15}, "print has no setting density"),
    ({"regions": {}}, "print's regions are a list"),
], ids=lambda v: str(v)[:40])
def test_each_field_is_checked_and_named(raw: dict[str, Any], said: str) -> None:
    with pytest.raises(ValueError, match="^" + said.replace("(", r"\(")):
        read(raw)


@pytest.mark.parametrize("region,said", [
    ({"name": "r", "infill": 60}, "print's region r has one shape, one of box, cylinder"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "layers": [0, 1], "infill": 60},
     "print's region r has one shape, one of box, cylinder, mesh, layers: not box and layers"),
    ({"name": "r", "cylinder": {"at": [0, 0, 0], "axis": "z", "radius": 1}, "infill": 60},
     "print's region r's cylinder is"),
    ({"name": "r", "cylinder": {"at": [0, 0, 0], "axis": "w", "radius": 1, "length": 2},
      "infill": 60}, "print's region r's cylinder's axis is x, y or z"),
    ({"name": "r", "cylinder": {"at": [0, 0, 0], "axis": [0, 0, 0], "radius": 1, "length": 2},
      "infill": 60}, "print's region r's cylinder's axis has no length"),
    ({"name": "r", "cylinder": {"at": [0, 0, math.nan], "axis": "z", "radius": 1, "length": 2},
      "infill": 60}, "print's region r's cylinder's at is a finite number"),
    ({"name": "r", "cylinder": {"at": [0, 0, 0], "axis": "z", "radius": 0, "length": 2},
      "infill": 60}, "print's region r's cylinder's radius and length are each more than"),
    ({"name": "r", "mesh": "face.step", "infill": 60}, "print's region r's mesh is an STL"),
    ({"name": "r", "mesh": "../face.stl", "infill": 60},
     "print's region r's mesh is named relative to the assembly or job file, inside"),
    ({"name": "r", "layers": [6, 3], "infill": 60}, "print's region r's layers run from 6 to 3"),
    ({"name": "r", "layers": [3, 3], "infill": 60}, "print's region r's layers run from 3 to 3"),
    ({"name": "r", "layers": [1], "infill": 60}, "print's region r's layers are \\[low, high\\]"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]]}, "print's region r sets nothing of its own"),
    ({"name": "r", "box": [[0, 0], [1, 1]], "infill": 60}, "print's region r's box is two"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, math.inf]], "infill": 60},
     "print's region r's box has a corner that isn't a finite number"),
    ({"name": "r", "box": [[0, 0, 5], [1, 1, 1]], "infill": 60},
     "print's region r's box runs from 5 to 1 along z"),
    ({"name": "r", "box": [[0, 0, 0], [1, 0, 1]], "infill": 60},
     "print's region r's box runs from 0 to 0 along y"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "infill": 160},
     "print's region r's infill is 0 to 100"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "walls": -2},
     "print's region r's walls is 0 or more"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "pattern": "nonsense"},
     "print's region r's pattern is one of Orca's own names"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "layer_height": 0.1},
     "print's region r can't set layer_height"),
    ({"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "line_width": 0.5},
     "print's region r can't set line_width"),
    ({"name": "", "box": [[0, 0, 0], [1, 1, 1]], "infill": 60},
     "print's region 1 has a name"),
], ids=lambda v: str(v)[:40])
def test_each_region_field_is_checked_and_names_the_region(
    region: dict[str, Any], said: str
) -> None:
    with pytest.raises(ValueError, match="^" + said.replace("(", r"\(")):
        read({"regions": [region]})


def test_two_regions_of_one_name_are_refused() -> None:
    one = {"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "infill": 60}
    with pytest.raises(ValueError, match="each have a name of their own: r"):
        read({"regions": [one, one]})


def test_every_pattern_name_is_orcas() -> None:
    assert {"gyroid", "grid", "rectilinear", "cubic", "line", "triangles"} <= PATTERNS
    assert "zig-zag" not in PATTERNS  # Orca reads it as rectilinear: say that


def test_the_way_up_is_the_parts_own_and_signed_and_not_orcas() -> None:
    """`up` is the part's layers axis with its sign as it prints (mqucifer/print-shop#337): a
    part posed upside down has its top shells on its own -z faces. It's the whole part's, Orca
    has no key for it, and `load` needs it only where top and bottom differ."""
    whole = read({"walls": 2, "top": 5, "bottom": 3, "up": "-z"})
    assert whole.settings.up == "-z"
    assert "up" not in whole.settings.missing()
    assert "up" not in str(whole.settings.orca())
    assert "own -z up" in whole.settings.said()
    assert as_json(whole)["up"] == "-z"
    with pytest.raises(ValueError, match="print's up is"):
        read({"up": "z"})
    with pytest.raises(ValueError, match="can't set up"):
        read({"regions": [{"name": "r", "box": [[0, 0, 0], [1, 1, 1]], "up": "+z"}]})
