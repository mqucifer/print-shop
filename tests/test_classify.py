"""A printed part's elements' blends of skin and core (mqucifer/print-shop#363), without FreeCAD:
the laminate of a band of walls or shells, the column where two bands meet along an edge, and
the series blend, each against the bounds the Sponsor holds them to; and the polygon test a
layer's sparse area is read by."""

from __future__ import annotations

import math

import numpy as np
import pytest

from print_shop.bench import as_printed, classify, infill
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
    # Toward core, the soft side: any core is an eighth at least (mqucifer/print-shop#363's
    # review).
    assert classify.step_of(np.array([0.0, 0.06, 0.07, 0.5, 0.99, 1.0])).tolist() == [
        0, 1, 1, 4, 8, 8]


def test_an_element_s_64_points_are_each_an_equal_part_of_it() -> None:
    corners = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]], dtype=float)
    points = classify.fine_middles(corners)
    assert points.shape == (1, 64, 3)
    assert np.allclose(points[0].mean(axis=0), corners[0].mean(axis=0))
    assert len({tuple(np.round(p, 9)) for p in points[0]}) == 64


@pytest.mark.parametrize("share", [0.125, 0.5, 0.875])
@pytest.mark.parametrize("kind", [classify.LAMINATE, classify.COLUMN, classify.SERIES])
def test_a_mixed_element_s_core_strain_is_the_one_its_blend_gives_it(
    phases: tuple[np.ndarray, np.ndarray], share: float, kind: int
) -> None:
    """A mixed element's core is judged at its own strain (`classify.core_strain`,
    mqucifer/print-shop#363), the weaker side by its own limits: with the skin's the rest of
    the element's, the two phases' stresses by share are the blend's at the element's strain;
    along a laminate's face and a column's edge both are strained alike, and what's across
    carries the same stress in both."""
    skin, core = phases
    way = {classify.LAMINATE: np.array([0.3, -0.5, 0.81]), classify.COLUMN: ALONG,
           classify.SERIES: None}[kind]
    if way is not None:
        way = way / np.linalg.norm(way)
    blend = {classify.LAMINATE: classify.laminate, classify.COLUMN: classify.column}
    tensor = (blend[kind](skin, core, share, way) if way is not None
              else classify.reuss(skin, core, share))
    strain = np.array([[1.0, 0.3, 0.2], [0.3, -0.4, 0.1], [0.2, 0.1, -0.2]]) * 1e-3
    own = np.einsum("ijkl,kl->ij", classify.core_strain(kind, skin, core, share, way), strain)
    rest = (strain - share * own) / (1 - share)
    stress_core = np.einsum("ijkl,kl->ij", core, own)
    stress_skin = np.einsum("ijkl,kl->ij", skin, rest)
    assert np.allclose(share * stress_core + (1 - share) * stress_skin,
                       np.einsum("ijkl,kl->ij", tensor, strain), atol=1e-12)
    if way is None:
        assert np.allclose(stress_core, stress_skin, atol=1e-12)
        return
    turn = classify._frame_of(way) if kind == classify.LAMINATE else classify._frame_of(way)[
        [2, 0, 1]]
    along, across = ((classify._ALONG, classify._ACROSS) if kind == classify.LAMINATE
                     else (classify._AXIS, classify._AROUND))

    def mandel(t: np.ndarray) -> np.ndarray:
        """A strain or stress as Mandel's six, in axes `turn`."""
        m = turn @ t @ turn.T
        r = math.sqrt(2)
        return np.array([m[0, 0], m[1, 1], m[2, 2], r * m[1, 2], r * m[0, 2], r * m[0, 1]])

    assert np.allclose(mandel(own)[along], mandel(rest)[along], atol=1e-12)
    assert np.allclose(mandel(stress_core)[across], mandel(stress_skin)[across], atol=1e-12)
    assert np.allclose(classify.core_strain(kind, skin, core, 1.0, way),
                       np.einsum("ik,jl->ijkl", np.eye(3), np.eye(3)))


def grid_points(low: tuple[float, ...], high: tuple[float, ...], step: float) -> np.ndarray:
    axes = [np.arange(a + step / 2, b, step) for a, b in zip(low, high, strict=True)]
    return np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)


def test_a_core_s_strain_is_averaged_over_its_own_cell_and_only_its_core() -> None:
    """mqucifer/print-shop#363: a core's strain is read no finer than its own cell, place by
    place of its elements (`classify.element_averaged`): a strain the same throughout is read
    as it is, every place's cell full of core in the middle; a spike at one place is spread
    over its window, its cell's mean in the core's middle and its eighth's at its corner; and a
    core thinner than its cell is read over what core there is, its fill saying so."""
    cell, step = 4.0, 1.0
    where = grid_points((0, 0, 0), (20, 20, 12), step)
    weights = np.full(len(where), step**3)
    even = np.tile(np.diag([1e-3, -4e-4, 2e-4]), (len(where), 1, 1))
    read = classify.element_averaged(where, even, weights, cell)
    assert np.allclose(read.strain, even[0])
    assert read.fill.max() <= 1 + 1e-9
    middle = np.all(np.abs(read.at - [10, 10, 6]) < 1, axis=1)
    assert np.allclose(read.fill[middle], 1.0)
    # A spike at one place, an element's own peak, is its window's mean, not a line through
    # it: a line explains a window's spread, a spike doesn't (`FIT_HOLDS`). In the core's
    # middle, its cell's mean; at its corner, where an eighth of the cell is core, the mean of
    # that eighth, 8 times the cell's, and no more (mqucifer/print-shop#363's review).
    share = 1e-2 * step**3 / cell**3
    for spot, most in (([10.5, 10.5, 6.5], 1.0), ([0.5, 0.5, 0.5], 8.0)):
        spiked = np.zeros((len(where), 3, 3))
        spiked[np.argmin(np.linalg.norm(where - spot, axis=1))] = np.diag([1e-2, 0, 0])
        peak = classify.element_averaged(where, spiked, weights, cell).strain[:, 0, 0].max()
        assert peak <= most * share * 1.01, (spot, peak / share)
    thin = grid_points((0, 0, 0), (20, 1, 12), step)
    read = classify.element_averaged(thin, np.tile(even[0], (len(thin), 1, 1)),
                                     np.full(len(thin), step**3), cell)  # fmt: skip
    assert np.allclose(read.strain, even[0])
    assert read.fill.max() == pytest.approx(1 / 4, abs=0.06)
    assert np.all(read.at[:, 1] < 1)


def test_a_core_s_cell_is_its_lines_spacing_over_the_density_each_layer_holds() -> None:
    """Grid shares each layer between its two sets, rectilinear lays one a layer at the whole
    density, triangles three a layer (`infill.cell_mm`)."""
    assert infill.cell_mm("grid", 20, 0.407) == pytest.approx(4.07)
    assert infill.cell_mm("rectilinear", 20, 0.407) == pytest.approx(2.035)
    assert infill.cell_mm("triangles", 20, 0.407) == pytest.approx(6.105)
    assert infill.cell_mm("grid", 60, 0.407) == pytest.approx(4.07 / 3)



def _bent_core(cell: float, spacing: float = 1.25) -> tuple[np.ndarray, ...]:
    """A beam's core 8.4 mm across its layers, from 0.6 to 9.0 mm, bent by a strain along it
    that's linear across them: zero at its middle, 1e-3 at its top and bottom. Its places
    `spacing` apart, as sub-tetrahedra's middles are, each weighted alike."""
    xs = np.arange(spacing / 2, 40, spacing)
    ys = np.arange(spacing / 2, 12, spacing)
    zs = np.arange(0.6 + spacing / 2, 9.0, spacing)
    where = np.array([[x, y, z] for x in xs for y in ys for z in zs])
    strain = np.zeros((len(where), 3, 3))
    strain[:, 0, 0] = 1e-3 * (where[:, 2] - 4.8) / 4.2
    return where, strain, np.full(len(where), spacing**3)


def test_a_core_bent_across_its_layers_reads_its_edge_not_half_a_cell_in() -> None:
    """mqucifer/print-shop#363's review: a core's cell is a spacing in its layers' plane, so
    across its layers the strain is the material's own. Averaged over a cube a cell across, a
    linear bending field read half a cell in from the core's edge: 0.525 of the edge's strain
    at a 4.07 mm cell. Fitted across the layers and read at the core's own edge, it reads its
    edge's, to within the layers' own step."""
    for cell in (4.07, 1.36):
        where, strain, weight = _bent_core(cell)

        def core_at(points: np.ndarray) -> np.ndarray:
            return (points[:, 2] >= 0.6) & (points[:, 2] <= 9.0)

        found = classify.element_averaged(where, strain, weight, cell, up=2, core_at=core_at,
                                          step=0.2)  # fmt: skip
        stretch = np.abs(found.strain[:, 0, 0]).max()
        assert stretch >= 0.95e-3, (cell, stretch)
        # And never more than it: the line is the field's own.
        assert stretch <= 1.02e-3, (cell, stretch)


def test_a_layer_that_can_t_be_read_fails_the_band_check_never_borrowed() -> None:
    """mqucifer/print-shop#363's review: a layer GEOS can't read was read from its neighbour's
    rings, and the check, held to those same rings, couldn't see it. It's unread, counted, and
    the check fails on it; short or over past its limits, it fails as before."""
    assert classify.Bands(4, 0.0, 0.0, 0).fails() is None
    unread = classify.Bands(4, 0.0, 0.0, 1).fails()
    assert unread is not None and "1 of its layers" in unread
    assert classify.Bands(4, 1.5 * as_printed.SHORT, 0.0, 0).fails() is not None
    assert classify.Bands(4, 0.0, 1.5 * as_printed.AGREE, 0).fails() is not None


def test_a_share_steps_toward_core_never_away() -> None:
    """mqucifer/print-shop#363's review: a share stepped to the nearest eighth read an element
    with less core than it has, stiffer and stronger. It steps toward core, the soft side: no
    core is none, any is at least an eighth, all is all."""
    share = np.array([0.0, 1e-12, 0.01, 0.124, 0.126, 0.51, 0.99, 1.0])
    assert classify.step_of(share).tolist() == [0, 0, 1, 1, 2, 5, 8, 8]


def test_a_core_pulled_hardest_by_its_walls_reads_its_edge_in_the_layers_plane_too() -> None:
    """mqucifer/print-shop#363's review, the operator's ruling: a cell average that sat inside
    the walls' edge, as `FULLEST` read it, missed where the strain is highest, the unsafe side.
    A core whose strain across its layers rises linearly from the middle of its width to its
    walls reads its walls' edge, as it reads its layers' edge."""
    spacing, cell = 1.25, 4.07
    xs = np.arange(spacing / 2, 40, spacing)
    ys = np.arange(1.0 + spacing / 2, 19.0, spacing)
    zs = np.arange(0.6 + spacing / 2, 9.0, spacing)
    where = np.array([[x, y, z] for x in xs for y in ys for z in zs])
    strain = np.zeros((len(where), 3, 3))
    strain[:, 2, 2] = 1e-3 * np.abs(where[:, 1] - 10.0) / 9.0
    weight = np.full(len(where), spacing**3)

    def core_at(points: np.ndarray) -> np.ndarray:
        return ((points[:, 1] >= 1.0) & (points[:, 1] <= 19.0)
                & (points[:, 2] >= 0.6) & (points[:, 2] <= 9.0))  # fmt: skip

    found = classify.element_averaged(where, strain, weight, cell, up=2, core_at=core_at,
                                      step=0.2)  # fmt: skip
    across = found.strain[:, 2, 2].max()
    assert 0.95e-3 <= across <= 1.05e-3, across
