"""A printed part's skin as a layer of fifteen-node wedges on its faces (mqucifer/print-shop#363).

A part read as printed is a skin, its walls and its top and bottom shells, round an infill core.
Meshed fine enough at its faces for its elements to tell a wall from the core behind it, a real
part came to a million elements and past the container's memory. So the part is meshed as any
part is, at `load`'s own size, every element of it the core's, and its skin is laid over its
faces: on each of its boundary triangles, one CalculiX C3D15 wedge as deep as its skin is
there. Its outer face is the solid's own triangle, its six nodes shared, and its inner face
and middle nodes are its own, so nothing but those shared nodes ties it to the solid: no
constraint, as CalculiX's own shells are tied, each node's fibre a rigid knot, so that a wall's
shell and a top's meeting at an edge read the planted beam 15% stiffer than it is.

Where the layer lies over the solid, the solid is core and the wedge is skin: the core is
counted twice there, so each wedge is the skin's stiffness less the core's (`load`), and the
two together are the skin's. Left in, the planted beam reads 3.4% stiffer.

Each wedge's depth is its skin's, normal to its face: its walls by how far its face stands
upright to its layers, its top or bottom shells by how far it lies in their plane, whichever
is more (`depths`). Each node of the part's faces has one inner node, the point its faces'
depths put it at, so that two faces meeting at an edge are mitred, a wall's wedges and a top's
sharing the face between them, neither overlapping nor apart (`inner_nodes`). A triangle has
no wedge where the part doesn't go on behind it for its skin and the skin across from it,
with Orca's opening of the core between (`Layer.thin`): a web or a fin thinner than two walls
is all skin, read so in the solid by its layers (`classify.shares`), never counted twice;
nor where its wedge's inner nodes would leave the part (`Layer.outside`), as by a hole near a
face, or where the wedge is turned over or too sharp to read (`Layer.inverted`). The reply
counts each.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

#: The wedge's nodes in its own coordinates (ξ, η, ζ): its outer triangle's corners at ζ -1,
#: its inner's at +1, their sides' middles, and the middles between them, as CalculiX's C3D15
#: numbers them (Abaqus's order).
NODES = (
    (0.0, 0.0, -1.0), (1.0, 0.0, -1.0), (0.0, 1.0, -1.0),
    (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0),
    (0.5, 0.0, -1.0), (0.5, 0.5, -1.0), (0.0, 0.5, -1.0),
    (0.5, 0.0, 1.0), (0.5, 0.5, 1.0), (0.0, 0.5, 1.0),
    (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
)
#: CalculiX's nine integration points of a C3D15 (`gauss3d8` in its `gauss.f`): its triangle's
#: three at a sixth and two thirds, by Gauss's three through its depth, and their weights.
_ROOT = math.sqrt(0.6)
POINTS = tuple((a, b, c) for c in (-_ROOT, 0.0, _ROOT)
               for a, b in ((1 / 6, 1 / 6), (2 / 3, 1 / 6), (1 / 6, 2 / 3)))
WEIGHTS = tuple(w / 6 for w in (5 / 9, 8 / 9, 5 / 9) for _ in range(3))
#: Its edges, by its node numbers: each side's middle node between its two corners.
_SIDES = ((0, 1, 6), (1, 2, 7), (2, 0, 8), (3, 4, 9), (4, 5, 10), (5, 3, 11))


def shape(point: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """The wedge's 15 shape functions at `point`, and their slopes along ξ, η and ζ."""
    xi, eta, zeta = point
    share = (1 - xi - eta, xi, eta)
    # Each share's slope along ξ and η.
    turns = ((-1.0, -1.0), (1.0, 0.0), (0.0, 1.0))
    values, by_share, by_zeta = np.zeros(15), np.zeros((15, 3)), np.zeros(15)
    for i in range(3):
        for node, level in ((i, -1.0), (i + 3, 1.0)):
            a = share[i]
            values[node] = 0.5 * a * (2 * a - 1) * (1 + zeta * level) - 0.5 * a * (1 - zeta**2)
            by_share[node, i] = 0.5 * (4 * a - 1) * (1 + zeta * level) - 0.5 * (1 - zeta**2)
            by_zeta[node] = 0.5 * a * (2 * a - 1) * level + a * zeta
        values[12 + i] = share[i] * (1 - zeta**2)
        by_share[12 + i, i] = 1 - zeta**2
        by_zeta[12 + i] = -2 * share[i] * zeta
    for first, second, node in _SIDES:
        i, j = first % 3, second % 3
        level = -1.0 if node < 9 else 1.0
        values[node] = 2 * share[i] * share[j] * (1 + zeta * level)
        by_share[node, i] = 2 * share[j] * (1 + zeta * level)
        by_share[node, j] = 2 * share[i] * (1 + zeta * level)
        by_zeta[node] = 2 * share[i] * share[j] * level
    slopes = np.zeros((15, 3))
    for i in range(3):
        slopes[:, 0] += by_share[:, i] * turns[i][0]
        slopes[:, 1] += by_share[:, i] * turns[i][1]
    slopes[:, 2] = by_zeta
    return values, slopes


def _in_space(places: np.ndarray, point: tuple[float, ...]
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each wedge's shape functions' slopes in space at `point`, `[wedge, node, axis]`, its
    Jacobian's determinant there, and where the point is."""
    values, slopes = shape(point)
    mapped = np.einsum("ak,nai->nik", slopes, places)
    det = np.linalg.det(mapped)
    safe = np.where(np.abs(det)[:, None, None] > 0, mapped, np.eye(3))
    in_space = np.einsum("nki,ak->nai", np.linalg.inv(safe), slopes)
    return in_space, det, np.einsum("a,nai->ni", values, places)


def strains(places: np.ndarray, moved: np.ndarray,
            points: tuple[tuple[float, ...], ...] = POINTS + NODES
            ) -> tuple[np.ndarray, np.ndarray]:
    """Each wedge's own strain at `points`, its integration points and its nodes unless given,
    from its 15 nodes' movement: `[wedge, point, 3, 3]`, and where each point is."""
    strain = np.zeros((len(places), len(points), 3, 3))
    at = np.zeros((len(places), len(points), 3))
    for k, point in enumerate(points):
        in_space, _, at[:, k] = _in_space(places, point)
        grown = np.einsum("nai,naj->nij", moved, in_space)
        strain[:, k] = (grown + grown.transpose(0, 2, 1)) / 2
    return strain, at


def stiffness(places: np.ndarray, tensor: np.ndarray) -> np.ndarray:
    """Each wedge's stiffness matrix, `[wedge, node, axis, node, axis]`, in N/mm, by CalculiX's
    own nine points: for nodes a, b along i, k, the sum of g_aj C_ijkl g_bl over them, each
    weighted by its share of the wedge's volume. `tensor` is each wedge's C_ijkl."""
    found = np.zeros((len(places), 15, 3, 15, 3))
    for point, weight in zip(POINTS, WEIGHTS, strict=True):
        in_space, det, _ = _in_space(places, point)
        found += np.einsum("n,naj,nijkl,nbl->naibk", weight * det, in_space, tensor, in_space)
    return found


def forces(places: np.ndarray, moved: np.ndarray, tensor: np.ndarray) -> np.ndarray:
    """The force each wedge puts on each of its nodes, `[wedge, node, axis]`, in N: its stress
    at each of CalculiX's nine points against its shape's slopes, weighted as its stiffness."""
    found = np.zeros((len(places), 15, 3))
    for point, weight in zip(POINTS, WEIGHTS, strict=True):
        in_space, det, _ = _in_space(places, point)
        grown = np.einsum("nai,naj->nij", moved, in_space)
        strain = (grown + grown.transpose(0, 2, 1)) / 2
        stress = np.einsum("nijkl,nkl->nij", tensor, strain)
        found += np.einsum("n,nij,naj->nai", weight * det, stress, in_space)
    return found


def lowest_jacobian(places: np.ndarray) -> np.ndarray:
    """Each wedge's least Jacobian determinant over its integration points and its corners: at
    or under none, it's turned inside out."""
    least = np.full(len(places), np.inf)
    for point in POINTS + NODES[:6]:
        _, det, _ = _in_space(places, point)
        least = np.minimum(least, det)
    return least


# The layer


#: How far behind a triangle the part must go on for it to have a wedge, as a share of its
#: skin's depth, with Orca's opening of the core besides: its own skin and the skin of the face
#: across from it (`load._Model._skin_layer`).
BEHIND = 2.0
#: The most a mitred inner node may lie from its own, as a share of its faces' deepest skin:
#: past it, two faces meet too sharply for one wedge's depth to stand for the skin there.
SHARPEST = 3.0
#: How many times wedges are dropped and their inner nodes found again, before what's left is
#: taken as it is.
ROUNDS = 4


@dataclass
class Layer:
    """A printed part's skin as wedges (`layer`): each wedge's 15 nodes, as rows of the mesh's
    points or, past them, of `new`, the inner and middle nodes it adds; each one's cell and
    depth; each kept triangle's middle, unit normal out of the part, depth and reach, for
    which of the solid's points it covers (`covered`); and how many of the part's boundary
    triangles have no wedge, and why."""

    wedges: np.ndarray
    new: np.ndarray
    places: np.ndarray
    cells: np.ndarray
    depth: np.ndarray
    middles: np.ndarray
    normals: np.ndarray
    reach: np.ndarray
    triangles: int = 0
    held: int = 0
    thinned: int = 0
    thin: int = 0
    outside: int = 0
    inverted: int = 0
    said: list[str] = field(default_factory=list)


def faces_of(wired: np.ndarray, points: np.ndarray, mids: tuple[tuple[int, int], ...]
             ) -> tuple[np.ndarray, np.ndarray]:
    """A part's boundary triangles from its ten-node elements, `wired` as rows of `points` with
    their mid-side nodes in `mids`' order: each face no other of its elements shares, as six
    rows, corners first then each side's middle, turned so its normal points out of the part,
    and that normal."""
    side = {frozenset(pair): 4 + k for k, pair in enumerate(mids)}
    seen: dict[tuple[int, ...], tuple[int, int]] = {}
    for e, nodes in enumerate(wired[:, :4].tolist()):
        for skip in range(4):
            face = tuple(sorted(nodes[:skip] + nodes[skip + 1 :]))
            seen[face] = (-1, -1) if face in seen else (e, skip)
    kept = [(e, skip) for e, skip in seen.values() if e >= 0]
    rows = np.zeros((len(kept), 6), dtype=int)
    for k, (e, skip) in enumerate(kept):
        a, b, c = (i for i in range(4) if i != skip)
        corners = wired[e, [a, b, c]]
        middles = [wired[e, side[frozenset(p)]] for p in ((a, b), (b, c), (c, a))]
        normal = np.cross(points[corners[1]] - points[corners[0]],
                          points[corners[2]] - points[corners[0]])  # fmt: skip
        if normal @ (points[corners].mean(axis=0) - points[wired[e, skip]]) < 0:
            corners = corners[[0, 2, 1]]
            middles = [middles[2], middles[1], middles[0]]
        rows[k] = [*corners, *middles]
    first, second, third = (points[rows[:, i]] for i in range(3))
    normal = np.cross(second - first, third - first)
    return rows, normal / np.linalg.norm(normal, axis=1)[:, None]


def depths(normals_own: np.ndarray, layers: int, up: float, walls: np.ndarray,
           top: np.ndarray, bottom: np.ndarray) -> np.ndarray:
    """Each face's skin's depth normal to it: its walls, a bead's spacing each, by how far it
    stands upright to its layers, and its top shells where it looks `up` or its bottom shells
    where it looks the other way, by how far it lies in their plane, whichever is deeper."""
    along = normals_own[:, layers] * up
    upright = np.sqrt(np.clip(1 - along**2, 0, 1))
    shells = np.where(along > 0, top, bottom)
    return np.asarray(np.maximum(walls * upright, shells * np.abs(along)))


def inner_nodes(points: np.ndarray, rows: np.ndarray, normals: np.ndarray, depth: np.ndarray
                ) -> tuple[dict[int, np.ndarray], set[int]]:
    """Each face node's inner node: the point whose depth below each of its kept triangles'
    planes is that triangle's skin's, by least squares, mitred where two faces meet, and on a
    face of one plane its depth straight in. Its nodes too sharp to read so (`SHARPEST`)."""
    planes: dict[int, list[int]] = {}
    for t, row in enumerate(rows.tolist()):
        for n in row:
            planes.setdefault(n, []).append(t)
    inner: dict[int, np.ndarray] = {}
    sharp: set[int] = set()
    for n, ts in planes.items():
        ways = normals[ts]
        offset, *_ = np.linalg.lstsq(ways, -depth[ts], rcond=1e-3)
        if np.linalg.norm(offset) > SHARPEST * float(depth[ts].max()):
            sharp.add(n)
        inner[n] = points[n] + offset
    return inner, sharp


def layer(points: np.ndarray, rows: np.ndarray, normals: np.ndarray, depth: np.ndarray,
          cells: np.ndarray, over_core: np.ndarray,
          inside: Callable[[np.ndarray], np.ndarray],
          planes: np.ndarray | None = None) -> Layer:
    """The part's skin as wedges on its boundary triangles `rows` (`faces_of`), each as deep
    as `depth`, none where its skin isn't `over_core`. Where a wedge would be turned over, as a
    face narrower than its neighbour's skin is by the mitre, or an inner node would leave the
    part (`inside`, of points in the assembly's axes) or lie too far off (`SHARPEST`), its
    nodes' inner offsets are halved, `ROUNDS` times at most (`Layer.thinned`), the rest of its
    skin left to the solid's layers; and where that doesn't stand it, it's dropped and the rest
    found again. `planes`, triangles with no wedge, a held face's, still mitre their neighbours'
    inner nodes at no depth, so a wall leaning onto a held face keeps its nodes on that face's
    plane, not past it, out of the part."""
    keep = over_core.copy()
    fixed = np.zeros(len(rows), dtype=bool) if planes is None else planes & ~over_core
    outside = inverted = 0
    built: Layer | None = None
    for _ in range(ROUNDS):
        mine = np.flatnonzero(keep)
        held_rows = np.flatnonzero(fixed)
        both = np.concatenate([mine, held_rows])
        offset_of, sharp = inner_nodes(points, rows[both], normals[both],
                                       np.concatenate([depth[mine], np.zeros(len(held_rows))]))
        used = {n for row in rows[mine].tolist() for n in row}
        offset_of = {n: v for n, v in offset_of.items() if n in used}
        sharp &= used
        order = sorted(offset_of)
        where = {n: k for k, n in enumerate(order)}
        offsets = np.array([offset_of[n] - points[n] for n in order]).reshape(-1, 3)
        scale = np.ones(len(order))
        for n in sharp:
            scale[where[n]] = 0.5
        corners = sorted({n for row in rows[mine].tolist() for n in row[:3]})
        middle_of = {n: k for k, n in enumerate(corners)}
        base = len(points)
        wedges = np.zeros((len(mine), 15), dtype=int)
        for k, t in enumerate(mine):
            row = rows[t]
            # Its outer face turned so its normal points in, to its inner face.
            o = [row[0], row[2], row[1], row[5], row[4], row[3]]
            wedges[k] = (o[:3] + [base + where[n] for n in o[:3]] + o[3:]
                         + [base + where[n] for n in o[3:]]
                         + [base + len(order) + middle_of[n] for n in o[:3]])
        nodes_of = [[where[n] for n in rows[t]] for t in mine]
        surface = np.array([points[n] for n in order]).reshape(-1, 3)
        halves = np.array([points[n] for n in corners]).reshape(-1, 3)
        half_of = np.array([where[n] for n in corners], dtype=int)
        # Each inner side's middle, and the corners it's between.
        sides = {}
        for t in mine:
            row = rows[t]
            for m, (a, b) in zip(row[3:], ((row[0], row[1]), (row[1], row[2]), (row[2], row[0])),
                                 strict=True):
                sides[where[m]] = (where[a], where[b])
        side_at = np.array(sorted(sides), dtype=int)
        side_ends = np.array([sides[m] for m in side_at], dtype=int).reshape(-1, 2)
        for _ in range(ROUNDS + 1):
            inner_at = surface + scale[:, None] * offsets
            if len(side_at):
                # A side's middle by its own offset, at its corners' mean scale: a curved face's
                # skin as deep at its middle as at its corners, and a shallower wedge's even.
                mean = (scale[side_ends[:, 0]] + scale[side_ends[:, 1]]) / 2
                inner_at[side_at] = surface[side_at] + mean[:, None] * offsets[side_at]
            new = np.concatenate([inner_at, (halves + inner_at[half_of]) / 2])
            every = np.concatenate([points, new])
            least = lowest_jacobian(every[wedges]) if len(wedges) else np.zeros(0)
            out = ~inside(inner_at) if len(inner_at) else np.zeros(0, dtype=bool)
            leaves = np.array([out[ns].any() for ns in nodes_of], dtype=bool)
            turned = least <= 0
            bad = leaves | turned
            if not bad.any():
                break
            for turned_at in np.flatnonzero(bad):
                scale[nodes_of[turned_at]] *= 0.5
        built = Layer(wedges, new, every[wedges] if len(wedges) else np.zeros((0, 15, 3)),
                      cells[mine], depth[mine], every[rows[mine, :3]].mean(axis=1),
                      normals[mine], np.zeros(len(mine)))
        built.thinned = int(np.array([(scale[ns] < 1).any() for ns in nodes_of],
                                     dtype=bool).sum()) if len(mine) else 0  # fmt: skip
        if not bad.any():
            break
        outside += int((leaves & ~turned).sum())
        inverted += int(turned.sum())
        keep[mine[bad]] = False
    assert built is not None
    if bad.any():
        # Its last round's that still don't stand: left out, and counted.
        good = ~bad
        outside += int((leaves & ~turned).sum())
        inverted += int(turned.sum())
        built.wedges, built.places = built.wedges[good], built.places[good]
        built.cells, built.depth = built.cells[good], built.depth[good]
        built.middles, built.normals = built.middles[good], built.normals[good]
        built.reach = built.reach[good]
    built.triangles = len(rows)
    built.thin = int((~over_core).sum())
    built.outside, built.inverted = outside, inverted
    return built


#: Each wedge's straight-sided prism as three tetrahedra, by its corner nodes: what it covers.
_PRISM = ((0, 1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5))


def volumes(places: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each wedge's volume, by CalculiX's nine points, and its middle."""
    if not len(places):
        return np.zeros(0), np.zeros((0, 3))
    volume = np.zeros(len(places))
    for point, weight in zip(POINTS, WEIGHTS, strict=True):
        volume += weight * _in_space(places, point)[1]
    return volume, places.mean(axis=1)


def within(queries: np.ndarray, tets: np.ndarray) -> np.ndarray:
    """Which of `queries`, `[n, 3]`, lie in any of `tets`, `[shape, tetrahedron, 4, 3]`: each
    shape's straight-sided tetrahedra, each query tried against the shapes whose boxes reach
    the voxel it's in, the voxels about two shapes across."""
    hit = np.zeros(len(queries), dtype=bool)
    if not len(tets) or not len(queries):
        return hit
    edges = (tets[:, :, 1:] - tets[:, :, :1]).transpose(0, 1, 3, 2)
    singular = np.abs(np.linalg.det(edges)) < 1e-12
    inverse = np.linalg.inv(np.where(singular[..., None, None], np.eye(3), edges))
    flat = tets.reshape(len(tets), -1, 3)
    low, high = flat.min(axis=1), flat.max(axis=1)
    size = max(2 * float(np.median((high - low).max(axis=1))), 1e-6)
    origin = low.min(axis=0)
    shape = np.floor((high.max(axis=0) - origin) / size).astype(int) + 1
    first = np.floor((low - origin) / size).astype(int)
    last = np.floor((high - origin) / size).astype(int)
    pairs = []
    for w in range(len(tets)):
        span = [np.arange(first[w, a], last[w, a] + 1) for a in range(3)]
        cells = np.stack(np.meshgrid(*span, indexing="ij"), axis=-1).reshape(-1, 3)
        pairs.append(np.column_stack([np.ravel_multi_index(cells.T, shape),
                                      np.full(len(cells), w)]))  # fmt: skip
    pair = np.concatenate(pairs)
    pair = pair[np.argsort(pair[:, 0], kind="stable")]
    voxels, starts, counts = np.unique(pair[:, 0], return_index=True, return_counts=True)
    most = int(counts.max())
    table = np.full((len(voxels), most), -1, dtype=int)
    for k in range(most):
        has = counts > k
        table[has, k] = pair[starts[has] + k, 1]
    step = max(1, 2_000_000 // max(1, most * tets.shape[1] * 9))
    for start in range(0, len(queries), step):
        some = queries[start : start + step]
        at = np.floor((some - origin) / size).astype(int)
        in_box = np.all((at >= 0) & (at < shape), axis=1)
        key = np.ravel_multi_index(np.clip(at, 0, shape - 1).T, shape)
        where = np.minimum(np.searchsorted(voxels, key), len(voxels) - 1)
        known = in_box & (voxels[where] == key)
        if not known.any():
            continue
        rows = np.flatnonzero(known)
        near = table[where[rows]]
        valid = near >= 0
        safe = np.where(valid, near, 0)
        towards = some[rows][:, None, None, :] - tets[safe][:, :, :, 0]
        share = np.einsum("nmtij,nmtj->nmti", inverse[safe], towards)
        inside = ((share >= -1e-9).all(axis=3) & (share.sum(axis=3) <= 1 + 1e-9)
                  & ~singular[safe] & valid[:, :, None])  # fmt: skip
        hit[start + rows] = inside.any(axis=(1, 2))
    return hit


def covered(points: np.ndarray, skin: Layer) -> np.ndarray:
    """Which of elements' `points`, `[element, point, 3]`, lie in a wedge of `skin`: in one of
    its straight-sided prism's three tetrahedra (`within`), its outer face moved out past any
    bulge of a curved face."""
    if not len(skin.middles) or not len(points):
        return np.zeros(points.shape[:2], dtype=bool)
    corners = skin.places[:, :6].copy()
    # Its outer corners moved out by its depth: a curved face bulges past its chord, and the
    # skin between is the wedge's too. Nothing of the part lies out past its own face.
    corners[:, :3] += (skin.depth[:, None] * skin.normals)[:, None, :]
    tets = np.stack([corners[:, list(t)] for t in _PRISM], axis=1)
    return within(points.reshape(-1, 3), tets).reshape(points.shape[:2])
