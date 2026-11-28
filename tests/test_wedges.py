"""A printed part's skin as a layer of fifteen-node wedges on its faces (`bench/wedges.py`,
mqucifer/print-shop#363), without FreeCAD: the wedge's shape and its forces as CalculiX takes
them, each face's skin's depth, the mitre where two faces meet, and which triangles have none."""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest

from print_shop.bench import as_printed, classify, infill, load, wedges

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
    # An inner node past the part, in a void under its top face, takes every wedge it's in
    # with it; the face's own nodes are in.
    outside = wedges.layer(points, rows, normals, depth, kinds, np.ones(12, dtype=bool),
                           lambda p: (p[:, 2] < 9.0) | (p[:, 2] >= 10.0 - 1e-12))  # fmt: skip
    assert outside.outside > 0 and len(outside.wedges) + outside.outside == 12


def test_a_point_is_covered_where_a_wedge_lies_over_it() -> None:
    points, wired = cube()
    rows, normals = wedges.faces_of(wired, points, load.MIDS)
    skin = wedges.layer(points, rows, normals, np.full(12, 0.5), np.zeros(12, dtype=int),
                        np.ones(12, dtype=bool), lambda p: np.ones(len(p), dtype=bool))
    # The face z = 0 looks down: a point 0.3 above it is under its skin, 0.7 above isn't.
    under = np.array([[[4.0, 6, 0.3], [4.0, 6, 0.7]]])
    assert wedges.covered(under, skin).tolist() == [[True, False]]
    # And the cube's own six tetrahedra hold a point inside it, and none outside.
    tets = points[wired[:, :4]][:, None]
    probe = np.array([[5.0, 5, 5], [0.1, 9.9, 0.1], [10.5, 5, 5], [-0.1, 5, 5]])
    assert wedges.within(probe, tets).tolist() == [True, True, False, False]


class Cubed:
    """A cube's layers along z, 0.2 mm thick, its core 1 mm in from every face, one cell."""

    layers, low, high, height, count = 2, 0.0, 10.0, 0.2, 50
    regions: list[object] = []
    unread: set[int] = set()
    cells: list[object] = [None]

    def cell_of(self, points: np.ndarray) -> np.ndarray:
        return np.zeros(len(points), dtype=int)

    def _readable(self, layer: int, cell: object) -> list[np.ndarray]:
        at = (layer + 0.5) * self.height
        return [np.array([[1.0, 1], [9, 1], [9, 9], [1, 9]])] if 1 < at < 9 else []


def test_a_part_s_elements_and_wedges_read_exactly_by_its_layers() -> None:
    """mqucifer/print-shop#363: a cube of six tetrahedra, its skin a mitred layer of wedges 1 mm
    deep, its layers' core 1 mm in from each face (`classify.exact`): its elements hold all of
    it, 1,000 mm³; their core is 8³ exactly; the wedges in them are their own volume, none of
    it core; and the check finds the mesh's core Orca's, band by band, its skin all there."""
    points, wired = cube()
    rows, normals = wedges.faces_of(wired, points, load.MIDS)
    skin = wedges.layer(points, rows, normals, np.full(12, 1.0), np.zeros(12, dtype=int),
                        np.ones(12, dtype=bool), lambda p: np.ones(len(p), dtype=bool))
    found = classify.exact(Cubed(), points[wired[:, :4]], skin.places[:, :6])  # type: ignore[arg-type]
    assert found.whole.sum() == pytest.approx(1000.0)
    assert found.core.sum() == pytest.approx(512.0)
    assert found.in_wedges.sum() == pytest.approx(float(wedges.volumes(skin.places)[0].sum()))
    assert found.wedges_core.sum() == pytest.approx(0.0, abs=1e-3)
    assert found.layer_core.sum() == pytest.approx(512.0)
    share = (found.core + found.in_wedges - found.wedges_core) / found.whole
    bands = classify.exact_checked(Cubed(), found, share, 2.0)  # type: ignore[arg-type]
    assert bands.short < 1e-6 and bands.over < 1e-6
    # A wedge too deep, its core's edge 1.5 mm in, is the mesh's core short of Orca's.
    deep = wedges.layer(points, rows, normals, np.full(12, 1.5), np.zeros(12, dtype=int),
                        np.ones(12, dtype=bool), lambda p: np.ones(len(p), dtype=bool))
    found = classify.exact(Cubed(), points[wired[:, :4]], deep.places[:, :6])  # type: ignore[arg-type]
    share = (found.core + found.in_wedges - found.wedges_core) / found.whole
    assert classify.exact_checked(Cubed(), found, share, 2.0).short > 0.05  # type: ignore[arg-type]


def test_a_node_the_mesh_puts_out_of_the_part_keeps_its_wedges() -> None:
    """A side's middle the mesh itself puts out of the part, on a straight-sided mesh's chord
    off a concave face, isn't held to be in it: its inner node halved toward it would leave the
    part as the solid's own node does, and on a real leg every such wedge was dropped. Its inner
    node may be out by as much as it is, and 0.005 more, never further; and a node the mesh
    puts in, whose inner node leaves, still takes its wedges with it."""
    points, wired = cube()
    rows, normals = wedges.faces_of(wired, points, load.MIDS)
    depth, kinds, over = np.full(12, 0.5), np.zeros(12, dtype=int), np.ones(12, dtype=bool)
    top = next(n for n in np.unique(rows[:, 3:]) if points[n, 2] == 10.0
               and 0 < points[n, 0] < 10 and 0 < points[n, 1] < 10)  # fmt: skip

    def out_round(centre: np.ndarray, radius: float) -> Callable[[np.ndarray], np.ndarray]:
        return lambda p: np.linalg.norm(p - centre, axis=1) > radius

    column = points[top, :2]

    def out_by(node: float, below: float) -> Callable[[np.ndarray], np.ndarray]:
        """Out of the part by `node` at the node, as the mesh put it, and by `below` under it,
        at any depth; in elsewhere."""

        def apart(p: np.ndarray) -> np.ndarray:
            near = np.linalg.norm(p[:, :2] - column, axis=1) < 0.3
            on = np.abs(p[:, 2] - 10.0) < 1e-9
            return np.where(near & on, node, np.where(near & (p[:, 2] < 10.0), below, 0.0))

        return apart

    def inside_of(apart: Callable[[np.ndarray], np.ndarray]
                  ) -> Callable[[np.ndarray], np.ndarray]:
        return lambda p: apart(p) == 0

    # The node out by 0.3, as a concave face's chord is; its inner node by 0.2, less: kept.
    less = out_by(0.3, 0.2)
    kept = wedges.layer(points, rows, normals, depth, kinds, over, inside_of(less), apart=less)
    assert len(kept.wedges) == 12 and kept.outside == 0
    # Without how far, every inner node is held to be in, the node's own out or not.
    strict = wedges.layer(points, rows, normals, depth, kinds, over, inside_of(less))
    assert strict.outside > 0
    # Its inner node out by 1.0, at every depth, more than the node's 0.3 and 0.005: dropped.
    more = out_by(0.3, 1.0)
    dropped_deep = wedges.layer(points, rows, normals, depth, kinds, over, inside_of(more),
                                apart=more)  # fmt: skip
    assert dropped_deep.outside > 0
    # Out within 0.499 of its inner node, its depth under it: the node is in, and its inner
    # node out at every depth the halving tries, the last 0.498 under it.
    under = points[top] - np.array([0.0, 0.0, 0.5])
    dropped = wedges.layer(points, rows, normals, depth, kinds, over, out_round(under, 0.499))
    assert dropped.outside > 0 and len(dropped.wedges) < 12


class Shifted(Cubed):
    """`Cubed`'s core moved 0.4 mm along x, its area in each layer the same."""

    def _readable(self, layer: int, cell: object) -> list[np.ndarray]:
        return [ring + np.array([0.4, 0.0]) for ring in super()._readable(layer, cell)]


def _grid(n: int = 10, size: float = 10.0) -> np.ndarray:
    """A cube `size` across cut into n³ cubes of six tetrahedra each (Kuhn's): their corners,
    `[element, 4, 3]`."""
    step = size / n
    found = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                base = np.array([i, j, k], dtype=float) * step
                for order in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
                    corner, path = base.copy(), [base.copy()]
                    for axis in order:
                        corner = corner.copy()
                        corner[axis] += step
                        path.append(corner)
                    found.append(path)
    return np.array(found)


def test_a_wrong_core_is_caught_by_the_enforced_band_check() -> None:
    """mqucifer/print-shop#363's review, main's planted wrong cuts for the read by layers: a
    cube's mesh of 1 mm elements held to its layers passes; and each wrong core the model could
    be given is caught, never passed: shifted 0.4 mm, its area each layer the same, which the
    bands' totals can't see, so each element's share is held to its own exact core too; 4% of
    it short, the unsafe side; a slab of its layers with none, as a ledge missed; and a layer
    unread."""
    corners = _grid()
    none = np.zeros((0, 6, 3))
    true = Cubed()
    found = classify.exact(true, corners, none)  # type: ignore[arg-type]
    share = np.clip(found.core / found.whole, 0, 1)
    assert classify.exact_checked(true, found, share).fails() is None  # type: ignore[arg-type]
    moved = classify.exact(Shifted(), corners, none)  # type: ignore[arg-type]
    wrong = np.clip(moved.core / moved.whole, 0, 1)
    shifted = classify.exact_checked(true, found, wrong)  # type: ignore[arg-type]
    assert shifted.short <= as_printed.SHORT and shifted.over <= as_printed.AGREE, shifted
    assert shifted.fails() is not None and "place" in str(shifted.fails()), shifted
    short = classify.exact_checked(true, found, 0.96 * share)  # type: ignore[arg-type]
    assert short.fails() is not None and "short" in str(short.fails()), short
    ledge = share.copy()
    ledge[found.spans[found.spans[:, 1] == 25, 0].astype(int)] = 0.0
    assert classify.exact_checked(true, found, ledge).fails() is not None  # type: ignore[arg-type]
    unread = Cubed()
    unread.unread = {25}
    assert classify.exact_checked(unread, found, share).fails() is not None  # type: ignore[arg-type]


def test_each_boundary_triangle_s_element_is_the_one_it_s_a_face_of() -> None:
    """mqucifer/print-shop#363's review: a wedge took off its triangle's cell's core while the
    solid under it had its own element's; so each triangle's element (`face_elements`), in
    `faces_of`'s order, is the one it's a face of, whose cell its wedge takes."""
    points, wired = cube()
    rows, _ = wedges.faces_of(wired, points, load.MIDS)
    owners = wedges.face_elements(wired)
    assert len(owners) == len(rows)
    for face, e in zip(rows.tolist(), owners.tolist(), strict=True):
        assert set(face) <= set(wired[e].tolist())


class Strided(Cubed):
    """`Cubed` read every fifth layer, its core's rings shrinking 0.2 mm a layer above z 5, as
    beside a face leaning past what a layer may stand for."""

    stride = 5

    def read_at(self, layer: int) -> int:
        return (layer // self.stride) * self.stride

    def sparse(self, layer: int, cell: object) -> list[np.ndarray]:
        at = (layer + 0.5) * self.height
        if not 1 < at < 9:
            return []
        inset = 1.0 + max(0.0, at - 5.0) * 1.0
        return [np.array([[inset, 1.0], [9, 1], [9, 9], [inset, 9]])]

    def _readable(self, layer: int, cell: object) -> list[np.ndarray]:
        return self.sparse(self.read_at(layer), cell)


def test_a_layer_standing_for_others_is_checked_against_their_own_rings() -> None:
    """mqucifer/print-shop#363's review: the band check was held to the same spaced rings the
    read used, so a layer standing for others whose section moved couldn't be seen. Each band
    has a layer read on its own, the farthest from the one standing for it, and the two's core
    held within `as_printed.SHORT` of the layer."""
    corners = _grid()
    none = np.zeros((0, 6, 3))
    true = Cubed()
    found = classify.exact(true, corners, none)  # type: ignore[arg-type]
    share = np.clip(found.core / found.whole, 0, 1)
    assert classify.exact_checked(true, found, share).fails() is None  # type: ignore[arg-type]
    strided = Strided()
    read = classify.exact(strided, corners, none)  # type: ignore[arg-type]
    shares = np.clip(read.core / read.whole, 0, 1)
    bands = classify.exact_checked(strided, read, shares)  # type: ignore[arg-type]
    assert bands.fails() is not None and "stand" in str(bands.fails()), bands
    # Moving 0.4 mm along x for each mm up above z 5, its area the same: caught all the same.
    moving = Strided()
    moving.sparse = lambda layer, cell: [  # type: ignore[method-assign]
        ring + np.array([0.4 * max(0.0, (layer + 0.5) * 0.2 - 5.0), 0.0])
        for ring in Cubed._readable(moving, layer, cell)]  # fmt: skip
    read = classify.exact(moving, corners, none)  # type: ignore[arg-type]
    shares = np.clip(read.core / read.whole, 0, 1)
    assert classify.exact_checked(moving, read, shares).fails() is not None  # type: ignore[arg-type]
