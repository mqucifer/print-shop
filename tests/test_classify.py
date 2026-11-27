"""A printed part's elements' blends of skin and core (mqucifer/print-shop#363), without FreeCAD:
the laminate of a band of walls or shells, the column where two bands meet along an edge, and
the series blend, each against the bounds the Sponsor holds them to; and the polygon test a
layer's sparse area is read by."""

from __future__ import annotations

import math

import numpy as np
import pytest

from print_shop.bench import classify, infill
from print_shop.bench.assembly import MATERIALS
from print_shop.bench.printed import Settings

UP, ALONG, ACROSS = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]


@pytest.fixture(scope="module")
def phases() -> tuple[np.ndarray, np.ndarray]:
    """PETG's skin, and a 20% grid's core laid along x, in the assembly's axes."""
    core = infill.core(Settings(pattern="grid", infill=20, infill_angle=0), MATERIALS["petg"])
    skin = infill.isotropic(MATERIALS["petg"].modulus_mpa, MATERIALS["petg"].poisson)
    return skin, core.stiffness(UP, ALONG, ACROSS)


def modulus(tensor: np.ndarray, way: np.ndarray) -> float:
    return float(infill.pulled(tensor, way / np.linalg.norm(way)))


def across(tensor: np.ndarray, turn: np.ndarray, which: list[int]) -> np.ndarray:
    """A stiffness's block of the terms `which`, in axes `turn`, the others' strain held."""
    return np.asarray(classify._mandel(classify._turned(tensor, turn))[np.ix_(which, which)])


def in_series(skin: np.ndarray, core: np.ndarray, share: float, turn: np.ndarray,
              which: list[int]) -> np.ndarray:
    """The two phases' blocks of `which` in series by share: each inverted, added, inverted."""
    soft = share * np.linalg.inv(across(core, turn, which))
    soft += (1 - share) * np.linalg.inv(across(skin, turn, which))
    return np.asarray(np.linalg.inv(soft))


@pytest.mark.parametrize("share", [0.125, 0.25, 0.5, 0.75, 0.875])
@pytest.mark.parametrize("normal", [UP, ACROSS, np.array([0.3, -0.5, 0.81])])
def test_a_band_element_is_in_series_across_its_face_and_no_stiffer_along_it_than_its_skin(
    phases: tuple[np.ndarray, np.ndarray], share: float, normal: np.ndarray
) -> None:
    """The Sponsor's test (mqucifer/print-shop#363): a band element's laminate is in series
    across its face, its stiffness across, with its strain along the face held as its layers
    hold it, the two phases' own in series by share, exactly; and along the face no stiffer
    than its shares side by side, Voigt's, and within 2% of it, Poisson's coupling between.

    Not that a pull across reads no stiffer than Reuss's series of the two free to shrink
    every way: a laminate's layers are held to one another along the face, so its stiff skin
    stops its core shrinking there, as it does in a print, and a pull across reads up to 17%
    stiffer than Reuss's at a core of seven eighths. That's the layers' own, not a mix read
    stiffer than it is. On a face slanted to the core's lines, its across terms are coupled,
    so it's the whole of what's across, held so, that's in series: its block of the stiffness,
    each phase's inverted, added by share and inverted back."""
    skin, core = phases
    normal = normal / np.linalg.norm(normal)
    blend = classify.laminate(skin, core, share, normal)
    turn = classify._frame_of(normal)
    assert np.allclose(across(blend, turn, classify._ACROSS),
                       in_series(skin, core, share, turn, classify._ACROSS), rtol=1e-9)
    side_by_side = share * core + (1 - share) * skin
    assert modulus(blend, normal) <= modulus(side_by_side, normal)
    for way in turn[:2]:
        along = modulus(blend, way)
        assert along <= modulus(side_by_side, way) * (1 + 1e-9)
        assert along == pytest.approx(modulus(side_by_side, way), rel=0.03)
        assert along > 2 * modulus(classify.reuss(skin, core, share), way)


@pytest.mark.parametrize("share", [0.25, 0.5, 0.75])
def test_where_two_bands_meet_it_s_side_by_side_along_their_edge_and_in_series_across(
    phases: tuple[np.ndarray, np.ndarray], share: float
) -> None:
    """A column along an edge where two bands meet: along the edge, as stiff as its shares side
    by side and no stiffer; across it in series, all of what's across held with the strain
    along the edge as its bands hold it, each phase's block inverted, added by share and
    inverted back."""
    skin, core = phases
    blend = classify.column(skin, core, share, ALONG)
    series = classify.reuss(skin, core, share)
    side_by_side = share * core + (1 - share) * skin
    assert modulus(blend, ALONG) <= modulus(side_by_side, ALONG) * (1 + 1e-9)
    assert modulus(blend, ALONG) == pytest.approx(modulus(side_by_side, ALONG), rel=0.02)
    turn = classify._frame_of(ALONG)[[2, 0, 1]]
    assert np.allclose(across(blend, turn, classify._AROUND),
                       in_series(skin, core, share, turn, classify._AROUND), rtol=1e-9)
    for way in (UP, ACROSS):
        # A pull across reads above Reuss's free series only by the edge's strain held.
        assert modulus(blend, way) <= modulus(series, way) * 1.15


@pytest.mark.parametrize("share", [0.25, 0.5, 0.75])
def test_in_series_is_the_softest_blend_every_way(
    phases: tuple[np.ndarray, np.ndarray], share: float
) -> None:
    skin, core = phases
    series = classify.reuss(skin, core, share)
    side_by_side = share * core + (1 - share) * skin
    for way in (UP, ALONG, ACROSS, np.array([1.0, 1.0, 1.0])):
        assert modulus(series, way) <= modulus(side_by_side, way)
        assert modulus(series, way) <= modulus(classify.laminate(skin, core, share, UP), way) * (
            1 + 1e-6)


def test_a_blend_of_one_material_is_that_material(phases: tuple[np.ndarray, np.ndarray]) -> None:
    skin, core = phases
    turned = np.array([0.3, 0.5, 0.8]) / np.linalg.norm([0.3, 0.5, 0.8])
    for blend in (classify.laminate, classify.column):
        assert np.allclose(blend(skin, skin, 0.4, turned), skin)
        assert np.allclose(blend(skin, core, 0.0, turned), skin)
        assert np.allclose(blend(skin, core, 1.0, turned), core)
    assert np.allclose(classify.reuss(core, core, 0.3), core)


def test_a_face_s_normal_groups_with_itself_either_way_round() -> None:
    """Ways within a degree group together, and a flat face's own normal is its group's, so
    no laminate's layers lean off its face (mqucifer/print-shop#363)."""
    ways = np.array([UP, -UP, ACROSS, [0.0, -1.0, 0.0], [math.sin(0.001), 0.0, math.cos(0.001)]])
    groups, mean = classify.way_groups(ways)
    assert groups[0] == groups[1] == groups[4] and groups[2] == groups[3]
    assert groups[0] != groups[2]
    assert np.allclose(np.abs(mean[groups[2]]), ACROSS)
    assert abs(float(mean[groups[0]] @ UP)) == pytest.approx(1.0, abs=1e-6)


def test_a_point_is_in_a_layer_s_area_by_its_rings_holes_and_all() -> None:
    outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
    hole = np.array([[4, 4], [6, 4], [6, 6], [4, 6]], dtype=float)
    points = np.array([[1, 1], [5, 5], [11, 5], [5, 9.5], [4.5, 5]], dtype=float)
    assert classify.inside([outer, hole], points).tolist() == [True, False, False, True, False]


def test_a_share_is_its_step_an_eighth_apart() -> None:
    assert classify.step_of(np.array([0.0, 0.06, 0.07, 0.5, 0.99, 1.0])).tolist() == [
        0, 0, 1, 4, 8, 8]


def test_an_element_s_64_points_are_each_an_equal_part_of_it() -> None:
    corners = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]], dtype=float)
    points = classify.fine_middles(corners)
    assert points.shape == (1, 64, 3)
    assert np.allclose(points[0].mean(axis=0), corners[0].mean(axis=0))
    assert len({tuple(np.round(p, 9)) for p in points[0]}) == 64
