"""Specification for `src/ingest/analyzer.py`.

Written before the implementation. Every expected value in `fixtures.py` is
derived on paper, so a passing run means the analyzer agrees with the geometry
rather than with itself.

The overhang tests exist because the previous implementation measured the angle
between each face normal and **+Z** and flagged anything past 45 degrees — so a
vertical wall (90 degrees) counted as an overhang and every model got supports.
`test_a_cube_has_no_overhangs` is precisely that regression.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from print_shop.core.state import MeshReport
from print_shop.ingest.analyzer import analyze
from tests.fixtures import CUBE, OPEN_BOX, TEE, Expected, write_stl

TOL = 1e-3


@pytest.fixture(scope="module")
def stls(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("stl")
    return {name: write_stl(name, d) for name in ("cube", "tee", "open_box")}


# --------------------------------------------------------------------------
# Basic geometry
# --------------------------------------------------------------------------


def test_returns_a_mesh_report(stls: dict[str, Path]) -> None:
    assert isinstance(analyze(stls["cube"]), MeshReport)


@pytest.mark.parametrize(
    "name,expected", [("cube", CUBE), ("tee", TEE), ("open_box", OPEN_BOX)]
)
def test_measures_volume_area_and_bounds(
    stls: dict[str, Path], name: str, expected: Expected
) -> None:
    r = analyze(stls[name])
    assert r.volume_mm3 == pytest.approx(expected.volume_mm3, abs=TOL)
    assert r.surface_area_mm2 == pytest.approx(expected.surface_area_mm2, abs=TOL)
    assert r.bbox_mm == pytest.approx(expected.bbox_mm, abs=TOL)
    assert r.is_manifold is expected.is_manifold


# --------------------------------------------------------------------------
# Overhangs — the metric the previous implementation got wrong
# --------------------------------------------------------------------------


def test_a_cube_has_no_overhangs(stls: dict[str, Path]) -> None:
    """A cube is six faces: four vertical walls, one ceiling, one on the plate.

    None of them need support. The old code flagged all four walls, because it
    measured the angle to +Z (a wall is 90 degrees from +Z) instead of asking
    whether the face points downward.
    """
    r = analyze(stls["cube"])
    assert r.overhang_area_mm2 == pytest.approx(0.0, abs=TOL)
    assert r.max_overhang_deg == pytest.approx(0.0, abs=TOL)


def test_a_face_resting_on_the_plate_is_bed_contact_not_overhang(
    stls: dict[str, Path],
) -> None:
    """The cube's bottom face points straight down but rests on the plate."""
    r = analyze(stls["cube"])
    assert r.bed_contact_area_mm2 == pytest.approx(CUBE.bed_contact_area_mm2, abs=TOL)
    assert r.overhang_area_mm2 == pytest.approx(0.0, abs=TOL)


def test_measures_a_real_overhang(stls: dict[str, Path]) -> None:
    """The tee's slab underside, minus the pillar footprint, is 300 mm^2."""
    r = analyze(stls["tee"])
    assert r.overhang_area_mm2 == pytest.approx(TEE.overhang_area_mm2, abs=TOL)
    assert r.bed_contact_area_mm2 == pytest.approx(TEE.bed_contact_area_mm2, abs=TOL)
    assert r.max_overhang_deg == pytest.approx(90.0, abs=TOL)


def test_threshold_is_measured_from_vertical(stls: dict[str, Path]) -> None:
    """A horizontal ceiling is 90 degrees; a vertical wall is 0.

    Raising the threshold above 90 must therefore find nothing at all, and the
    report records the threshold it used.
    """
    r = analyze(stls["tee"], overhang_threshold_deg=91.0)
    assert r.overhang_area_mm2 == pytest.approx(0.0, abs=TOL)
    assert r.overhang_threshold_deg == pytest.approx(91.0, abs=TOL)


def test_reports_the_threshold_it_used(stls: dict[str, Path]) -> None:
    assert analyze(stls["cube"]).overhang_threshold_deg == pytest.approx(45.0, abs=TOL)


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_the_report_is_immutable(stls: dict[str, Path]) -> None:
    """MeshReport is frozen — a downstream node must not edit measurements."""
    r = analyze(stls["cube"])
    with pytest.raises(ValidationError):
        r.volume_mm3 = 1.0


def test_height_comes_from_the_bounding_box(stls: dict[str, Path]) -> None:
    assert analyze(stls["cube"]).height_mm == pytest.approx(20.0, abs=TOL)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, ValueError)):
        analyze(tmp_path / "nope.stl")


def test_an_open_mesh_has_no_volume(stls: dict[str, Path]) -> None:
    """trimesh reports 6000 for a cube missing two faces. The true answer is
    "undefined" — an open mesh encloses nothing. A plausible wrong number is
    worse than None, because nothing downstream can tell it is wrong."""
    r = analyze(stls["open_box"])
    assert r.is_manifold is False
    assert r.volume_mm3 is None
    assert r.surface_area_mm2 == pytest.approx(2000.0, abs=TOL)
