"""A settings study's candidates sliced by the real Orca (mqucifer/print-shop#339): the bar of
`test_slice_regions.py`, 80 x 20 x 10 mm with a 6 mm hole, bolted through it and latched at its
end, with the store's profiles. Marked `slicer`, and skipped without Orca: CI's filter leaves it
out, and the Mac runs it.
"""

from __future__ import annotations

import pytest

from print_shop.bench.as_printed import wall_thickness
from print_shop.bench.printed import Settings
from print_shop.ingest.orientation import read_part
from print_shop.slicer.orca import parse_config_block
from print_shop.study.candidate import Candidate, Shaper
from print_shop.study.reading import OrcaSlicing, Sliced
from tests.fakes import STORE_PROFILES
from tests.test_slice_regions import _assembly, bar, needs_orca

pytestmark = [pytest.mark.slicer, needs_orca]


@pytest.fixture(scope="module")
def read(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Sliced]:
    folder = tmp_path_factory.mktemp("study")
    stl = bar(folder)
    shaper = Shaper(read_part(stl), folder / "meshes", None, _assembly(folder), "bar")
    slicing = OrcaSlicing(stl, STORE_PROFILES, folder / "meshes")
    asked = {
        "defaults": Candidate("defaults", Settings()),
        "walls": shaper.candidate("walls", {"settings": {"walls": 4, "infill": 30,
                                                         "pattern": "grid", "infill_angle": 0},
                                            "wall_sequence": "inner-outer-inner wall"}),
        "joints": shaper.candidate("joints", {"settings": {"walls": 3}, "regions": [
            {"joints": "all", "margin": 2, "faces": True, "walls": 5, "infill": 50}]}),
    }
    return {name: slicing(candidate, folder / name) for name, candidate in asked.items()}


def test_the_defaults_are_the_profiles_own_with_the_way_the_part_stands(
    read: dict[str, Sliced],
) -> None:
    base = read["defaults"]
    assert base.printed == {"walls": 2, "line_width": 0.45, "top": 5, "bottom": 3,
                            "infill": 15.0, "pattern": "rectilinear", "infill_angle": 45.0,
                            "layer_height": 0.2, "up": "+z"}
    assert base.time_s and base.grams and base.base_time_s is None
    assert base.walls_mm == pytest.approx(wall_thickness(2, 0.45, 0.2))
    assert base.band_mm == pytest.approx(0.90, abs=0.05)
    assert base.band_said()[0].endswith("as load reads them")


def test_more_walls_and_infill_cost_time_and_grams_and_the_wall_order_is_orcas(
    read: dict[str, Sliced],
) -> None:
    base, walls = read["defaults"], read["walls"]
    assert walls.time_s is not None and base.time_s is not None and walls.time_s > base.time_s
    assert walls.grams is not None and base.grams is not None and walls.grams > base.grams
    assert walls.printed["walls"] == 4 and walls.printed["pattern"] == "grid"
    assert walls.band_mm == pytest.approx(1.70, abs=0.07)
    assert walls.gcode is not None
    config = parse_config_block(walls.gcode)
    assert config["wall_sequence"] == "inner-outer-inner wall"


def test_regions_round_the_joints_are_sliced_as_orcas_modifiers_and_read_back(
    read: dict[str, Sliced],
) -> None:
    joints = read["joints"]
    assert joints.base_time_s is not None and joints.time_s is not None
    assert joints.time_s > joints.base_time_s, "the regions cost time"
    names = [r["name"] for r in joints.printed["regions"]]
    assert names == ["bar+post-bolt-1", "bar+lid-latch"]
    assert joints.printed["regions"][1]["mesh"].startswith("bar+lid-latch-faces-")
    said = "\n".join(joints.regions)
    assert "bar+post-bolt-1: built as asked" in said or "bar+post-bolt-1: not all" in said
    assert "infill: asked 50" in said
    assert set(joints.region_bands) == {"bar+post-bolt-1", "bar+lid-latch"}
