"""`fit-on`'s second method with no FreeCAD: rays through two meshes, read against arithmetic.

The rays are what says a boolean was right, so they're held on their own here, on meshes made by
hand: a block, and a small block that comes down into it.
"""

from __future__ import annotations

import numpy as np
import pytest

from print_shop.bench import fit_on


def block(
    low: tuple[float, float, float], high: tuple[float, float, float]
) -> tuple[np.ndarray, np.ndarray]:
    """A box as a closed mesh of 12 triangles."""
    (x0, y0, z0), (x1, y1, z1) = low, high
    points = np.array(
        [[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], dtype=float
    )
    quads = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
    facets = np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))])
    return points, facets


def rays_over(
    grid: fit_on.Grid, *meshes: tuple[np.ndarray, np.ndarray]
) -> list[tuple[np.ndarray, np.ndarray]]:
    found = []
    for points, facets in meshes:
        low, high, odd = fit_on.spans(*fit_on.crossings(points, facets, grid), grid.cells)
        assert odd == 0, "no ray runs along an edge"
        found.append((low, high))
    return found


def test_a_rectangle_s_least_width_is_its_short_side_whichever_way_it_s_turned() -> None:
    corners = np.array([[0, 0], [8, 0], [8, 0.6], [0, 0.6]], dtype=float)
    for degrees in (0, 30, 90, 137):
        turn = np.radians(degrees)
        spin = np.array([[np.cos(turn), -np.sin(turn)], [np.sin(turn), np.cos(turn)]])
        width, across, along = fit_on.least_width(corners @ spin.T + [3, -2])
        assert width == pytest.approx(0.6) and along == pytest.approx(8.0)
        assert abs(across @ (spin @ [0, 1])) == pytest.approx(1.0), "across its short side"


def test_points_on_a_line_have_no_width() -> None:
    width, _, along = fit_on.least_width(np.array([[0, 0], [1, 1], [2, 2]], dtype=float))
    assert width == 0 and along == pytest.approx(2 * 2**0.5)


def test_readings_run_from_out_to_seated_and_start_where_asked() -> None:
    assert fit_on.offsets(6, 0.25)[:2] == [6, 5.75] and len(fit_on.offsets(6, 0.25)) == 25
    assert fit_on.offsets(1.1, 0.5) == [1.1, 1.0, 0.5, 0.0]
    assert fit_on.offsets(0.1, 0.25) == [0.1, 0.0]


def test_rays_read_how_far_a_block_is_inside_another_at_each_step() -> None:
    """A block 2 x 0.6 mm, 3 mm tall, sitting 1 mm into the top of a block 10 x 10 x 5. Moved
    up by less than 1 mm it's still inside by what's left, and at 1 mm it isn't."""
    base = block((0, 0, 0), (10, 10, 5))
    small = block((4, 4, 4), (6, 4.6, 7))
    grid = fit_on.grid_over(2, (0, 0), (10, 10), 0.02, 400_000)
    under, moving = rays_over(grid, base, small)
    both = np.flatnonzero(~np.isnan(under[0][:, 0]) & ~np.isnan(moving[0][:, 0]))
    assert len(both) == pytest.approx(2 * 0.6 / grid.spacing**2, rel=0.02), "its footprint"
    pick = (moving[0][both], moving[1][both]), (under[0][both], under[1][both])
    for up, inside in ((0.0, 1.0), (0.4, 0.6), (0.98, 0.02), (1.0, 0.0), (2.0, 0.0)):
        lengths = fit_on.shared(*pick, up)
        assert lengths == pytest.approx(inside, abs=1e-9), up
    # The depth the command reads from them: the least width of the rays inside both, and a
    # ray's spacing for the rays' own width.
    cells = both[fit_on.shared(*pick, 0.0) > 0]
    depth = fit_on.least_width(grid.centres(cells))[0] + grid.spacing
    assert depth == pytest.approx(0.6, abs=2 * grid.spacing)


def test_a_block_with_a_hollow_is_read_in_two_stretches() -> None:
    """A ray through a shell goes in, out, in and out: what's inside the hollow isn't inside the
    part."""
    points, facets = block((0, 0, 0), (10, 10, 10))
    hole_points, hole_facets = block((2, 2, 2), (8, 8, 8))
    shell = (np.vstack([points, hole_points]), np.vstack([facets, hole_facets + len(points)]))
    probe = block((4, 4, 3), (6, 6, 7))
    grid = fit_on.grid_over(2, (3, 3), (7, 7), 0.1, 400_000)
    walls, inside = rays_over(grid, shell, probe)
    both = np.flatnonzero(~np.isnan(walls[0][:, 0]) & ~np.isnan(inside[0][:, 0]))
    assert walls[0].shape[1] == 2, "two stretches a ray"
    pick = (inside[0][both], inside[1][both]), (walls[0][both], walls[1][both])
    assert fit_on.shared(*pick, 0.0).max() == 0, "in the hollow, clear of the walls"
    assert fit_on.shared(*pick, 2.0) == pytest.approx(1.0), "1 mm into the top wall"


def test_a_sliver_one_ray_wide_isnt_an_overlap_and_a_block_of_rays_is() -> None:
    grid = fit_on.Grid(2, (0.0, 0.0), 0.1, (6, 6))
    flagged = np.zeros((6, 6), dtype=bool)
    flagged[2, :] = True  # a line of rays: two faces whose chords cross
    assert not fit_on.thick(flagged.reshape(-1), grid).any()
    flagged[3, 1:3] = True
    wide = fit_on.thick(flagged.reshape(-1), grid).reshape(6, 6)
    assert wide.sum() == 4 and wide[2:4, 1:3].all()


def test_a_stretch_shorter_than_the_chords_cross_isnt_counted() -> None:
    low, high = np.array([[0.0]]), np.array([[5.0]])
    just = (np.array([[5.0 - fit_on.LENGTH / 2]]), np.array([[9.0]]))
    assert fit_on.shared(just, (low, high), 0.0) == pytest.approx(0.0)
    assert fit_on.shared(just, (low, high), -1.0) == pytest.approx(1 + fit_on.LENGTH / 2)


def test_a_ray_that_crosses_a_mesh_an_odd_number_of_times_is_dropped_and_counted() -> None:
    cells = np.array([0, 0, 1, 1, 1, 2])
    depths = np.array([1.0, 3.0, 0.0, 2.0, 4.0, 7.0])
    low, high, odd = fit_on.spans(cells, depths, 4)
    assert odd == 2
    assert (low[0, 0], high[0, 0]) == (1.0, 3.0)
    assert np.isnan(low[1:]).all() and np.isnan(high[1:]).all()
