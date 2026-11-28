"""A printed part's skin as a layer of fifteen-node wedges on its faces (`bench/wedges.py`,
mqucifer/print-shop#363), without FreeCAD: the wedge's shape and its forces as CalculiX takes
them, each face's skin's depth, the mitre where two faces meet, and which triangles have none."""

from __future__ import annotations

import math

import numpy as np
import pytest

from print_shop.bench import infill, load, wedges

#: The unit wedge, its depth 1 along z.
UNIT = np.array([[[x, y, (z + 1) / 2] for x, y, z in wedges.NODES]])


def test_a_wedge_s_shape_is_one_at_its_own_node_and_sums_to_one() -> None:
    for k, point in enumerate(wedges.NODES):
        values, _ = wedges.shape(point)
        assert np.allclose(values, np.eye(15)[k])
    for point in wedges.POINTS:
        values, slopes = wedges.shape(point)
        assert values.sum() == pytest.approx(1.0) and np.allclose(slopes.sum(axis=0), 0)
    point, step = (0.2, 0.3, 0.4), 1e-6
    values, slopes = wedges.shape(point)
    for axis in range(3):
        moved = list(point)
        moved[axis] += step
        assert np.allclose((wedges.shape(tuple(moved))[0] - values) / step, slopes[:, axis],
                           atol=1e-5)  # fmt: skip


def test_a_wedge_reads_a_strain_the_same_throughout_and_carries_none_turned_or_moved() -> None:
    """A strain the same throughout comes back at every point; turned a little or moved whole,
    it carries no force; its stiffness by its movement is its forces; its volume is its
    triangle's by its depth."""
    strain = np.array([[1e-3, 2e-4, 0], [2e-4, -5e-4, 1e-4], [0, 1e-4, 3e-4]])
    read, _ = wedges.strains(UNIT, np.einsum("ij,naj->nai", strain, UNIT))
    assert np.allclose(read, strain)
    tensor = infill.isotropic(1500.0, 0.36)[None]
    turned = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 0]]) * 1e-4
    assert np.allclose(wedges.forces(UNIT, UNIT @ turned.T + 0.1, tensor), 0, atol=1e-9)
    moved = np.einsum("ij,naj->nai", strain, UNIT)
    stiff = wedges.stiffness(UNIT, tensor)
    assert np.allclose(np.einsum("naibk,nbk->nai", stiff, moved),
                       wedges.forces(UNIT, moved, tensor))  # fmt: skip
    volume, middle = wedges.volumes(UNIT)
    assert volume[0] == pytest.approx(0.5) and middle[0, 2] == pytest.approx(0.5)
    assert wedges.lowest_jacobian(UNIT)[0] > 0
    assert wedges.lowest_jacobian(UNIT[:, [0, 2, 1, 3, 5, 4, 8, 7, 6, 11, 10, 9, 12, 14, 13]]
                                  )[0] < 0  # fmt: skip


def test_a_face_s_skin_is_its_walls_upright_and_its_shells_flat_whichever_is_deeper() -> None:
    walls, top, bottom = 0.814, 1.0, 0.6
    tilt = math.radians(30)
    normals = np.array([[1, 0, 0], [0, 0, 1], [0, 0, -1],
                        [math.cos(tilt), 0, math.sin(tilt)]])  # fmt: skip
    found = wedges.depths(normals, 2, 1.0, *(np.full(4, v) for v in (walls, top, bottom)))
    assert found[:3] == pytest.approx([walls, top, bottom])
    assert found[3] == pytest.approx(max(walls * math.cos(tilt), top * math.sin(tilt)))
    # Printed own -z up, its top shells are on its other face.
    over = wedges.depths(normals[1:3], 2, -1.0, *(np.full(2, v) for v in (walls, top, bottom)))
    assert over == pytest.approx([bottom, top])


def test_two_faces_meeting_at_an_edge_share_one_mitred_inner_node() -> None:
    """A node on the edge of a wall and a top: its inner node lies its walls' depth in from the
    wall and its shells' depth down from the top, so their wedges share the face between them;
    on one plane it's its depth straight in."""
    points = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, -1]])
    rows = np.array([[0, 1, 2, 0, 0, 0], [0, 1, 3, 0, 0, 0]])
    normals = np.array([[0.0, 0, 1], [0, 1, 0]])
    inner, sharp = wedges.inner_nodes(points, rows, normals, np.array([1.0, 0.8]))
    assert inner[0] == pytest.approx([0, -0.8, -1.0]) and not sharp
    assert inner[2] == pytest.approx([0, 1, -1.0])


def cube(size: float = 10.0) -> tuple[np.ndarray, np.ndarray]:
    """A cube cut into 6 second-order tetrahedra (Kuhn's: one for each order its three axes are
    stepped in), their mid-side nodes shared and in `load.MIDS`' order; its nodes and rows."""
    points = [np.array(c, dtype=float) * size
              for c in ((x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1))]
    index = {tuple(np.round(p, 9)): k for k, p in enumerate(points)}

    def node(at: np.ndarray) -> int:
        key = tuple(np.round(at, 9))
        if key not in index:
            index[key] = len(points)
            points.append(at)
        return index[key]

    rows = []
    for order in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        corner, path = np.zeros(3), [np.zeros(3)]
        for axis in order:
            corner = corner.copy()
            corner[axis] = size
            path.append(corner)
        four = [node(c) for c in path]
        rows.append(four + [node((points[four[i]] + points[four[j]]) / 2) for i, j in load.MIDS])
    return np.array(points), np.array(rows)


def test_each_triangle_has_a_wedge_where_core_is_behind_its_skin_and_none_turned() -> None:
    """On a cube's twelve triangles, turned out of it: a wedge on each where its layers have
    core behind it, each its depth deep, the six faces' wedges mitred at its edges and corners,
    none turned inside out, their volume its skin's; none where they don't, as a web too thin
    for infill, nor where an inner node would leave the part, each counted."""
    points, wired = cube()
    rows, normals = wedges.faces_of(wired, points, load.MIDS)
    assert len(rows) == 12
    out = np.einsum("nk,nk->n", points[rows[:, :3]].mean(axis=1) - 5.0, normals)
    assert np.all(out > 0)
    depth, kinds = np.full(12, 0.5), np.zeros(12, dtype=int)
    anywhere = lambda p: np.ones(len(p), dtype=bool)  # noqa: E731
    every = wedges.layer(points, rows, normals, depth, kinds, np.ones(12, dtype=bool), anywhere)
    assert len(every.wedges) == 12 and every.thin == every.outside == every.inverted == 0
    assert np.all(wedges.lowest_jacobian(every.places) > 0)
    volume, _ = wedges.volumes(every.places)
    # Mitred, the skin is the cube less the cube its depth in from every face, counted once.
    assert volume.sum() == pytest.approx(10.0**3 - 9.0**3, rel=1e-9)
    thin = np.ones(12, dtype=bool)
    thin[0] = False
    some = wedges.layer(points, rows, normals, depth, kinds, thin, anywhere)
    assert len(some.wedges) == 11 and some.thin == 1
    # An inner node past the part takes every wedge it's in with it.
    outside = wedges.layer(points, rows, normals, depth, kinds, np.ones(12, dtype=bool),
                           lambda p: p[:, 2] < 9.0)  # fmt: skip
    assert outside.outside > 0 and len(outside.wedges) + outside.outside == 12


def test_a_point_is_covered_where_a_wedge_lies_over_it() -> None:
    points, wired = cube()
    rows, normals = wedges.faces_of(wired, points, load.MIDS)
    skin = wedges.layer(points, rows, normals, np.full(12, 0.5), np.zeros(12, dtype=int),
                        np.ones(12, dtype=bool), lambda p: np.ones(len(p), dtype=bool))
    # The face z = 0 looks down: a point 0.3 above it is under its skin, 0.7 above isn't.
    under = np.array([[[4.0, 6, 0.3], [4.0, 6, 0.7]]])
    assert wedges.covered(under, np.array([[5.0, 5, 2]]), skin).tolist() == [[True, False]]
