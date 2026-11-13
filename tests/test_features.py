"""The part's features, read as the printer lays the part (mqucifer/print-shop#107).

The first real prints showed two misreadings:

- **A slope drawn in 0.1 mm steps read as a ceiling.** Each step's underside,
  alone, is a flat face pointing down: 90 degrees. Every flat cap with a
  rounded face edge got supports from the overhang guard, and the Sponsor
  typed `enable_support=0` at each review. The slope is now read over 0.4 mm,
  across the steps, as the printer lays it, and a real ceiling still reads
  as one and still gets supports.
- **A long, narrow part lifted at its ends** while its large contact area
  kept the adhesion group from being read. Its length and its footprint's
  aspect now bring it in.

The fixtures are `tests/fixtures.py`'s, each derived on paper there.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from print_shop.ingest.analyzer import analyze
from print_shop.setup.features import _overhang, measure
from print_shop.setup.intake import Intake
from print_shop.setup.proposal import PartFacts, Proposal, guard
from print_shop.setup.skill import groups_for
from tests.fakes import STORE_PROFILES
from tests.fixtures import _profile, write_stl


def _read(part: str, tmp_path: Path) -> dict[str, float]:
    stl = write_stl(part, tmp_path)
    return {f.id: f.value for f in measure(stl, analyze(stl).bed_contact_area_mm2)}


def _guarded(part: str, tmp_path: Path) -> dict[str, str]:
    """The guard's changes for the part, over the store's presets, with no model."""
    stl = write_stl(part, tmp_path)
    facts = PartFacts(
        intake=Intake(),
        features=measure(stl, analyze(stl).bed_contact_area_mm2),
        machine=json.loads(STORE_PROFILES.machine.read_text()),
        process=json.loads(STORE_PROFILES.process.read_text()),
        filament=json.loads(STORE_PROFILES.filament.read_text()) if STORE_PROFILES.filament
        else {},
    )
    return {c.key: c.value for c in guard(facts, Proposal()).changes}


# --------------------------------------------------------------------------
# Stepped slopes are slopes; a ceiling is still a ceiling
# --------------------------------------------------------------------------


@pytest.mark.parametrize("part", ["stepped_ledge_45", "stepped_round"])
def test_a_slope_drawn_in_steps_is_not_a_ceiling(part: str, tmp_path: Path) -> None:
    """A 45 degree lean, and a 1 mm round at the plate, each in 0.1 mm steps."""
    stl = write_stl(part, tmp_path)
    # What each step's underside is, face by face: a 90 degree ceiling. The fixture
    # really is steps, and this is the reading that turned supports on.
    assert analyze(stl).max_overhang_deg == pytest.approx(90)
    assert analyze(stl).overhang_area_mm2 > 20
    f = _read(part, tmp_path)
    assert f["overhang"] == f["steepest_overhang"] == f["bridge"] == 0
    assert "enable_support" not in _guarded(part, tmp_path)


def test_a_steeper_stepped_slope_reads_at_its_slope_not_as_a_ceiling(tmp_path: Path) -> None:
    """50 degrees in 0.1 mm steps: an overhang, at 50 degrees, and no bridge."""
    f = _read("stepped_ledge_50", tmp_path)
    assert f["steepest_overhang"] == pytest.approx(50, abs=1)
    assert f["overhang"] > 0 and f["bridge"] == 0


def test_a_real_ceiling_still_reads_as_one_and_gets_supports(tmp_path: Path) -> None:
    """The tee: a 20 x 20 mm slab on a 10 x 10 mm pillar, its underside flat.

    300 mm2 of underside, less the 0.4 mm around the pillar that the pillar
    holds, 40 x 0.4 = 16 mm2: about 284 mm2, at 90 degrees, all of it bridge.
    """
    f = _read("tee", tmp_path)
    assert f["steepest_overhang"] == 90
    assert f["overhang"] == pytest.approx(284, abs=4)
    assert f["bridge"] == f["overhang"]
    guarded = _guarded("tee", tmp_path)
    assert guarded["enable_support"] == "1" and guarded["support_type"] == "tree(auto)"


def test_the_snap_hook_is_still_a_ceiling(tmp_path: Path) -> None:
    """Its 1.2 mm hook: 0.8 x 8 mm of it past what the beam holds, at 90 degrees."""
    f = _read("snap", tmp_path)
    assert f["steepest_overhang"] == 90
    assert f["overhang"] == pytest.approx(6.4, abs=0.7)
    assert _guarded("snap", tmp_path)["enable_support"] == "1"


@pytest.mark.parametrize("degrees", [30, 45, 50, 60, 80])
def test_a_smooth_slope_reads_at_its_own_angle(degrees: int) -> None:
    """The same lean drawn as one face, from 4 to 8 mm up: past 45 degrees it overhangs."""
    run = 4 * np.tan(np.radians(degrees))
    mesh = _profile([(0, 0), (6, 0), (6, 4), (6 + run, 8), (6 + run, 12), (0, 12)])
    area, steepest, bridge = _overhang(mesh)
    if degrees <= 45:
        assert area == steepest == 0
    else:
        assert steepest == pytest.approx(degrees, abs=0.5)
        # The face is 20 mm deep and rises 4 mm. The wall below holds it until it
        # reaches 0.4 mm out, 0.4 / tan(degrees) mm up; the rest, (4 - that) /
        # cos(degrees) mm up its slope, overhangs.
        theta = np.radians(degrees)
        assert area == pytest.approx(20 * (4 - 0.4 / np.tan(theta)) / np.cos(theta), rel=0.02)
    assert bridge == 0


# --------------------------------------------------------------------------
# A long, narrow footprint brings in adhesion
# --------------------------------------------------------------------------


def test_a_long_narrow_part_reads_the_adhesion_group(tmp_path: Path) -> None:
    """150 x 25 mm on the plate: 3750 mm2 of contact, which alone read as no concern."""
    stl = write_stl("long_strip", tmp_path)
    features = measure(stl, analyze(stl).bed_contact_area_mm2)
    f = {x.id: x.value for x in features}
    assert f["bed_contact"] == 3750 and f["slenderness"] < 2.5
    assert f["length"] == 150 and f["footprint_aspect"] == 6
    assert "adhesion" in [g.id for g in groups_for(Intake(), features)]


def test_a_long_part_across_the_plate_is_as_long(tmp_path: Path) -> None:
    strip = trimesh.creation.box(extents=(150, 25, 3))
    turn = trimesh.transformations.rotation_matrix(np.radians(37), (0, 0, 1))  # type: ignore[no-untyped-call]
    strip.apply_transform(turn)
    stl = tmp_path / "across.stl"
    strip.export(stl)
    f = {x.id: x.value for x in measure(stl, 3750)}
    assert f["length"] == pytest.approx(150, abs=0.5)
    assert f["footprint_aspect"] == pytest.approx(6, abs=0.1)


@pytest.mark.parametrize("part", ["cube", "bracket", "vase"])
def test_a_short_or_square_part_doesnt(part: str, tmp_path: Path) -> None:
    stl = write_stl(part, tmp_path)
    features = measure(stl, analyze(stl).bed_contact_area_mm2)
    assert "adhesion" not in [g.id for g in groups_for(Intake(), features)]
