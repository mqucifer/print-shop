"""A settings study's candidates sliced by the real Orca (mqucifer/print-shop#339): the bar of
`test_slice_regions.py`, 80 x 20 x 10 mm with a 6 mm hole, bolted through it and latched at its
end, with the store's profiles. Marked `slicer`, and skipped without Orca: CI's filter leaves it
out, and the Mac runs it.
"""

from __future__ import annotations

from dataclasses import replace

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
    fitting = shaper.candidate("fitting", {"settings": {"walls": 3}, "regions": [
        {"joints": "all", "margin": 2, "faces": True, "walls": 2, "infill": 50}]})
    # Past what the regions' 2 mm hold: the shaper refuses it before slicing
    # (mqucifer/print-shop#357), so it's made here as it was asked, for the reader to find.
    thin = replace(fitting, name="joints", regions=tuple(
        replace(r, settings=replace(r.settings, walls=5)) for r in fitting.regions))
    asked = {
        "defaults": Candidate("defaults", Settings()),
        "walls": shaper.candidate("walls", {"settings": {"walls": 4, "infill": 30,
                                                         "pattern": "grid", "infill_angle": 0},
                                            "wall_sequence": "inner-outer-inner wall"}),
        "joints": thin,
        "fitting": fitting,
        "roomy": shaper.candidate("roomy", {"settings": {"walls": 2}, "regions": [
            {"joints": ["bar+post-bolt-1"], "margin": 6, "walls": 4, "infill": 50}]}),
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
    roomy = read["roomy"]
    assert roomy.base_time_s is not None and roomy.time_s is not None
    assert roomy.time_s > roomy.base_time_s, "the region costs time"
    assert roomy.printed["regions"][0]["name"] == "bar+post-bolt-1"
    said = "\n".join(roomy.regions)
    assert "bar+post-bolt-1: built as asked" in said
    assert "walls: asked 4, read 4, without the regions 2: as asked" in said
    assert "infill: asked 50, read 50" in said
    assert roomy.unbuilt == [] and roomy.unread == []
    assert set(roomy.region_bands) == {"bar+post-bolt-1"}


def test_walls_orca_couldnt_fit_in_a_region_are_said_as_not_built(
    read: dict[str, Sliced],
) -> None:
    """mqucifer/print-shop#345's cold review: 5 walls asked in a 2 mm band round the hole and
    the latch, where Orca laid 2. `load` would read 5, so the study fails it as not as sliced."""
    joints = read["joints"]
    names = [r["name"] for r in joints.printed["regions"]]
    assert names == ["bar+post-bolt-1", "bar+lid-latch"]
    assert joints.printed["regions"][1]["mesh"].startswith("bar+lid-latch-faces-")
    assert joints.unbuilt == [
        f"{name}'s walls: asked 5, read 2, without the regions 3: not as asked (the walls fill "
        "it: only 2 fit in its width, and nothing is left inside them)"
        for name in names]
    assert joints.unread == ["bar+post-bolt-1's infill: asked 50, read -: not read (nothing of "
                             "it inside the region)"]


def test_regions_whose_depth_holds_their_walls_are_built_as_asked(
    read: dict[str, Sliced],
) -> None:
    """The same 2 mm regions round the hole and the latch with the 2 walls their depth holds
    (mqucifer/print-shop#357): Orca builds what's asked, and load reads what's printed."""
    fitting = read["fitting"]
    said = "\n".join(fitting.regions)
    assert fitting.unbuilt == [], said
    for name in ("bar+post-bolt-1", "bar+lid-latch"):
        assert f"{name}: " in said and "walls: asked 2, read 2" in said, said
