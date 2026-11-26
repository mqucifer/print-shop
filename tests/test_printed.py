"""Specification for `bench/printed.py`: a part's print settings and its regions, the one
format `load` (mqucifer/print-shop#337) and the slice (mqucifer/print-shop#338) both read."""

from __future__ import annotations

import math
from typing import Any

import pytest

from print_shop.bench.printed import (
    ORCA,
    PATTERNS,
    Printed,
    Region,
    Settings,
    as_json,
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
    ],
}


def test_a_whole_part_reads_and_writes_back_the_same() -> None:
    printed = read(WHOLE)
    assert printed.settings == Settings(walls=3, line_width=0.45, top=5, bottom=4, infill=15.0,
                                        pattern="gyroid", infill_angle=45.0, layer_height=0.2)
    assert [r.name for r in printed.regions] == ["bolt-1", "latch"]
    assert printed.regions[0].box == ((0.0, 0.0, 0.0), (20.0, 12.0, 8.0))
    assert printed.regions[0].settings == Settings(walls=6, infill=60.0)
    assert printed.regions[0].why == "round the bolt hole, 3 mm margin"
    assert read(as_json(printed)) == printed


def test_nothing_given_is_the_profiles_own() -> None:
    assert read({}) == Printed()
    assert Printed().at((1.0, 2.0, 3.0)) == Settings()


def test_a_point_takes_the_last_region_that_holds_it_over_the_part() -> None:
    printed = read(WHOLE)
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


def test_settings_are_said_in_orcas_own_keys() -> None:
    assert Settings(walls=6, infill=60.0, pattern="gyroid", infill_angle=0.0, top=8).orca() == {
        "wall_loops": "6", "sparse_infill_density": "60%", "sparse_infill_pattern": "gyroid",
        "infill_direction": "0", "top_shell_layers": "8",
    }
    assert set(ORCA) == {f for f in Settings.__dataclass_fields__}


def test_a_region_says_where_it_is_and_what_it_sets() -> None:
    region = Region("bolt-1", ((0, 0, 0), (20, 12, 8)), Settings(walls=6, infill=60.0), "why")
    assert region.said() == "bolt-1 (why): from (0, 0, 0) to (20, 12, 8), 6 walls, 60% infill"


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
    ({"name": "r", "infill": 60}, "print's region r has a box"),
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
