"""Exact areas of a printed part's layers inside its elements and wedges (`bench/areas.py`,
mqucifer/print-shop#363), without FreeCAD: sections, clipping and areas inside rings, each
against its own arithmetic."""

from __future__ import annotations

import math

import numpy as np
import pytest

from print_shop.bench import areas


def square(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)


def test_a_tetrahedron_s_section_is_its_triangle_or_quadrilateral_turned_anticlockwise() -> None:
    corners = np.array([[[0.0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]])
    poly, count = areas.sections(corners, areas.TET_EDGES, 2, 1.0)
    assert count[0] == 3
    assert areas.area(poly, count)[0] == pytest.approx(0.5)  # (2 - 1)² / 2
    # A plane between two opposite edges cuts a quadrilateral.
    corners = np.array([[[0.0, 0, 0], [1, 0, 0], [0, 0, 1], [0, 1, 1]]])
    poly, count = areas.sections(corners, areas.TET_EDGES, 2, 0.5)
    assert count[0] == 4 and areas.area(poly, count)[0] == pytest.approx(0.25)
    # Through a corner only, nothing.
    _, count = areas.sections(np.array([[[0.0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]]),
                              areas.TET_EDGES, 2, 2.0)  # fmt: skip
    assert count[0] == 0


def test_a_tetrahedron_s_sections_add_up_to_its_volume() -> None:
    rng = np.random.default_rng(4)
    corners = rng.uniform(0, 5, (20, 4, 3))
    volume = np.abs(np.linalg.det(corners[:, 1:] - corners[:, :1])) / 6
    step = 0.01
    total = np.zeros(20)
    for at in np.arange(step / 2, 5, step):
        poly, count = areas.sections(corners, areas.TET_EDGES, 2, at)
        total += areas.area(poly, count) * step
    assert total == pytest.approx(volume, rel=1e-3)


def test_a_polygon_on_a_ring_s_edge_or_of_no_area_is_read_once_or_not_at_all() -> None:
    """A wedge's inner face lies on the core's edge by construction: a square against the ring
    along one edge holds nothing of it, one along it inside, all of itself; a polygon of no
    area, nothing; and one clipped by nothing, nothing."""
    ring = [square(1, 1, 9, 9)]
    polys = np.stack([square(9, 2, 10, 4), square(8, 2, 9, 4), np.zeros((4, 2))])
    found = areas.inside_rings(polys, np.array([4, 4, 4]), ring)
    assert found == pytest.approx([0.0, 2.0, 0.0], abs=1e-5)
    _, count = areas.clip(square(0, 0, 1, 1)[None], np.array([4]), np.zeros((1, 4, 2)),
                          np.array([0]))  # fmt: skip
    assert count[0] == 0


def test_two_convex_polygons_clip_to_their_common_one() -> None:
    a, b = square(0, 0, 2, 2)[None], square(1, 1, 3, 3)[None]
    poly, count = areas.clip(a, np.array([4]), b, np.array([4]))
    assert areas.area(poly, count)[0] == pytest.approx(1.0)
    triangle = np.array([[[0.0, 0], [4, 0], [0, 4]]])
    poly, count = areas.clip(square(0, 0, 2, 2)[None], np.array([4]), triangle, np.array([3]))
    assert areas.area(poly, count)[0] == pytest.approx(4.0)
    poly, count = areas.clip(square(0, 0, 1, 1)[None], np.array([4]), square(5, 5, 6, 6)[None],
                             np.array([4]))  # fmt: skip
    assert areas.area(poly, count)[0] == pytest.approx(0.0)


def test_a_polygon_s_area_inside_rings_holes_and_all_is_its_arithmetic() -> None:
    """A ring 0..10 square with a hole 4..6, either way round each: a square 3..7 holds 16 less
    the hole's 4; one 8..12, its quarter 2 x 2 inside; one inside the hole, none; one wholly
    inside the ring and off the hole, all of it."""
    outer, hole = square(0, 0, 10, 10), square(4, 4, 6, 6)
    for rings in ([outer, hole], [outer[::-1], hole], [outer, hole[::-1]]):
        polys = np.stack([square(3, 3, 7, 7), square(8, 8, 12, 12), square(4.5, 4.5, 5.5, 5.5),
                          square(1, 1, 2, 3)])  # fmt: skip
        found = areas.inside_rings(polys, np.full(4, 4), rings)
        # To the rings' length times their nudge off any line a part's faces lie on.
        assert found == pytest.approx([12.0, 4.0, 0.0, 2.0], abs=1e-5)
        assert areas.rings_area(rings) == pytest.approx(96.0)
    # A circle's polygon ring, and a triangle across its edge, against a fine grid's count.
    angle = np.linspace(0, 2 * math.pi, 400, endpoint=False)
    circle = np.column_stack([3 * np.cos(angle), 3 * np.sin(angle)])
    triangle = np.array([[[0.0, 0], [5, 0], [0, 5]]])
    found = areas.inside_rings(triangle, np.array([3]), [circle])[0]
    assert found == pytest.approx(math.pi * 9 / 4, rel=1e-3)


def test_a_section_is_told_from_another_by_where_its_features_are() -> None:
    """mqucifer/print-shop#363's review: a layer's rings were reused where its section had the
    same area, length and box as one read, so a hole drilled at 45° through a block reused the
    first layer's rings all the way up. A section is told by its rings themselves."""
    import shapely

    from print_shop.bench import slices

    class Drilled:
        """A block 20 across, a hole 4 across moving 1 mm along x for each mm up."""

        def section(self, at: float) -> object:
            block = shapely.box(0, 0, 20, 20)
            return block.difference(shapely.Point(6 + at, 10).buffer(2, quad_segs=16))

    mesh = Drilled()
    assert slices.profile(mesh, 2.0) != slices.profile(mesh, 3.0)  # type: ignore[arg-type]
    assert slices.profile(mesh, 2.0) == slices.profile(mesh, 2.0)  # type: ignore[arg-type]


def test_a_layer_stands_for_another_only_where_its_section_moves_too_little_to_matter() -> None:
    """mqucifer/print-shop#363's review: a layer read every 1 mm stood for those between it and
    the next beside a face leaning under 30° from upright, where the section moves 0.23 mm in
    1 mm, a quarter of a 2-wall skin. Each layer's lean, as its section moves for its height
    (`Mesh.leans`), bounds how far a layer read stands for it (`classify.stands_for`): under
    `classify.STRIDE_MOVE_MM`."""
    from print_shop.bench import classify, slices

    lean = math.tan(math.radians(13))
    # A wall leaning 13° from upright over z 0 to 10, and one upright beside it.
    points = np.array([[0, 0, 0], [0, 10, 0], [10 * lean, 0, 10], [10 * lean, 10, 10],
                       [20, 0, 0], [20, 10, 0], [20, 0, 10], [20, 10, 10]], dtype=float)
    triangles = np.array([[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7]])
    mesh = slices.Mesh(points, triangles, 2)
    leans = mesh.leans(50, 0.0, 0.2)
    assert leans.max() == pytest.approx(lean, rel=1e-6)
    # Read every 1 mm, five layers apart: a layer 0.2 mm from the one read moves 0.046 mm.
    assert classify.stands_for(leans, 11, 10, 0.2)
    assert not classify.stands_for(leans, 12, 10, 0.2)
    # Beside an upright wall alone, the section doesn't move: it stands for all five.
    upright = slices.Mesh(points[4:] - [20, 0, 0], triangles[2:] - 4, 2).leans(50, 0.0, 0.2)
    assert classify.stands_for(upright, 12, 10, 0.2)
