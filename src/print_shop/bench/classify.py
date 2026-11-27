"""A printed part's mesh read as Orca prints it: each element skin, core, or a share of each
(mqucifer/print-shop#363).

`load` meshes a printed part as it meshes a solid one, and then reads each element by where it
lies in the part's own layers. A point is core where Orca lays sparse infill in the layer it
prints in, and skin anywhere else (`Layered.core`): the layer's section, each face of it offset
in by its walls, opened as Orca opens it before it lays infill, less what its top and bottom
shells take, grown across the layer as Orca grows them (`as_printed.layer_core`). So its walls
are a distance in its layer's plane, and its shells a distance along its layers; a thin spot is
all skin by itself, and nothing is refused for its shape. An element's share of core is its 64
points', the middles of its 8 sub-tetrahedra's 8 each (`shares`), as STORMFEA reads an
element's share of its walls' band from sub-tetrahedra (MIT,
github.com/micahtstoll-ai/STORMFEA; its idea, not its code).

An element that's part skin and part core is a blend of the two, and the blend is the weaker
one: the Reuss bound, skin and core in series, their compliances added by their shares
(`reuss`). Voigt's, their stiffnesses added, is the stiffest any mix of them can be, and a
part read by it could read stiffer and stronger than it prints. Each blend's strength is read
from the weaker side too: the element against the core's limits, and against the skin's.
Elements are blended in 9 steps of share, an eighth apart, one CalculiX set each.

The mesh is checked against the layers it's read by (`checked`): at each of the planes
`as_printed.heights` picks, each element crossing the plane is held to the layer's own share
of core across its section there, the share short and the share over, as a share of its walls
along the outline, to `as_printed.SHORT` and `as_printed.AGREE`. That's how well the mesh
resolves the walls, and a finer mesh near the part's faces (`load`) is what meets it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import as_printed
from .printed import Cylinder, Layers, Mesh, Printed, Region, Settings
from .report import NoAnswer

#: How many steps of share the mixed elements are blended in, past skin: an eighth each, as
#: each element's share is its 8 sub-tetrahedra's.
STEPS = 8
#: How far a polygon of a layer's sparse area may be from its curves, in mm.
POLYGON = 1e-3


@dataclass(frozen=True)
class Cell:
    """A piece of a printed part with one set of settings: its name, its settings, and the
    regions round it, in order."""

    name: str
    settings: Settings
    regions: tuple[str, ...]

    @property
    def walls_mm(self) -> float:
        s = self.settings
        return as_printed.wall_thickness(int(s.walls or 0), float(s.line_width or 0),
                                         float(s.layer_height or 0))  # fmt: skip

    @property
    def top_mm(self) -> float:
        return float(self.settings.top or 0) * float(self.settings.layer_height or 0)

    @property
    def bottom_mm(self) -> float:
        return float(self.settings.bottom or 0) * float(self.settings.layer_height or 0)


def _rings(faces: list[Any], first: int, second: int) -> list[np.ndarray]:
    """Each wire of faces in a layer's plane as a closed polygon, in its two axes there."""
    rings = []
    for face in faces:
        for wire in face.Wires:
            points = wire.discretize(Deflection=POLYGON)
            rings.append(np.array([(p[first], p[second]) for p in points]))
    return rings


def inside(rings: list[np.ndarray], points: np.ndarray) -> np.ndarray:
    """Which of `points`, in a layer's two axes, lie inside `rings`, by the even-odd rule: a
    face's holes are rings of their own, so a point in one is crossed twice."""
    hit = np.zeros(len(points), dtype=bool)
    if not len(points):
        return hit
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    for ring in rings:
        a, b = ring, np.roll(ring, -1, axis=0)
        ax, ay, bx, by = a[:, 0][None], a[:, 1][None], b[:, 0][None], b[:, 1][None]
        straddles = (ay > y) != (by > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            cross = ax + (y - ay) * (bx - ax) / (by - ay)
        hit ^= (straddles & (x < cross)).sum(axis=1) % 2 == 1
    return hit


@dataclass
class Layered:
    """A printed part read layer by layer as Orca slices it, in its own axes: which cell each
    point is in, and whether it's core there (mqucifer/print-shop#363)."""

    part: Any
    printed: Printed
    layers: int
    name: str
    folder: Path
    cells: list[Cell] = field(default_factory=list)
    #: Each region's solid, in Orca's order: the height ranges first, then the volumes.
    regions: list[tuple[Region, Any]] = field(default_factory=list)
    #: Each layer's sparse area by its settings, as rings, and the layers OCCT couldn't read.
    _sparse: dict[tuple[int, tuple[float, ...]], list[np.ndarray] | None] = field(
        default_factory=dict)
    unread: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        settings = self.printed.settings
        left_out = settings.missing()
        if left_out:
            raise NoAnswer(
                f"the part {self.name} is given print settings without {', '.join(left_out)}: "
                "`load` reads a printed part from the settings it's sliced with, all of them "
                "given at the part's own level, as the slice's profile has them, and guesses none"
            )
        up = settings.up
        if up is not None and up[1] != "xyz"[self.layers]:
            raise NoAnswer(
                f"the part {self.name}'s print says it stands own {up} up, and its layers are "
                f"along its own {'xyz'[self.layers]}: `up` is its layers axis with its sign as "
                "it prints"
            )
        self.up = -1.0 if up is not None and up[0] == "-" else 1.0
        self.height = float(settings.layer_height or 0)
        self.low, self.high = as_printed._span(self.part, self.layers)
        self.count = max(1, math.ceil((self.high - self.low) / self.height - 1e-9))
        ordered = [r for r in self.printed.regions if isinstance(r.shape, Layers)]
        ordered += [r for r in self.printed.regions if not isinstance(r.shape, Layers)]
        for region in ordered:
            solid = as_printed.region_solid(region, self.part, self.layers, self.folder)
            if (not self.part.BoundBox.intersect(solid.BoundBox)
                    or self.part.common(solid).Volume <= as_printed.NOTHING):  # fmt: skip
                raise NoAnswer(
                    f"the part {self.name}'s region {region.said()} holds none of the part, in "
                    "the part's own axes as its file has them"
                )
            self.regions.append((region, solid))
        self.line = float(settings.line_width or 0)
        #: One ring of an island's walls, a bead's spacing (`as_printed.wall_thickness`).
        self.ring = as_printed.wall_thickness(1, self.line, self.height)
        self.cells = [self._cell(())]

    # Which cell

    def _cell(self, labels: tuple[str, ...]) -> Cell:
        own = self.printed.settings
        by_name = {r.name: r for r, _ in self.regions}
        for label in labels:
            own = by_name[label].settings.over(own)
        where = f"the part {self.name}" + (f"'s {' and '.join(labels)}" if labels else "")
        for name in ("walls", "top", "bottom"):
            if not getattr(own, name):
                raise NoAnswer(
                    f"{where} is given {name} 0: its core would reach its outside, which "
                    "`load` doesn't read yet. Give it 1 or more, or leave out its `print` and "
                    "it's read solid"
                )
        if own.top != own.bottom and self.printed.settings.up is None:
            raise NoAnswer(
                f"{where} has {own.top} top layers and {own.bottom} bottom, and its print "
                "doesn't say which way it stands: give `up`, its layers axis with its sign as "
                f"it prints, such as \"+{'xyz'[self.layers]}\" or \"-{'xyz'[self.layers]}\""
            )
        return Cell(" and ".join(labels) if labels else "the part", own, labels)

    def _in_region(self, region: Region, solid: Any, points: np.ndarray) -> np.ndarray:
        """Which of `points`, in the part's own axes, are in a region's shape."""
        import FreeCAD as App

        shape = region.shape
        if isinstance(shape, Layers):
            along = points[:, self.layers]
            return np.asarray((along >= shape.low) & (along <= shape.high), dtype=bool)
        if isinstance(shape, Cylinder):
            axis = np.array(shape.axis, dtype=float)
            axis /= np.linalg.norm(axis)
            off = points - np.array(shape.at, dtype=float)
            along = off @ axis
            apart = np.linalg.norm(off - np.outer(along, axis), axis=1)
            return np.asarray((along >= 0) & (along <= shape.length) & (apart <= shape.radius),
                              dtype=bool)  # fmt: skip
        if isinstance(shape, Mesh):
            return np.array([bool(solid.isInside(App.Vector(*p), 1e-6, True)) for p in points],
                            dtype=bool)  # fmt: skip
        low, high = (np.array(c, dtype=float) for c in shape)
        return np.asarray(np.all((points >= low) & (points <= high), axis=1), dtype=bool)

    def cell_of(self, points: np.ndarray) -> np.ndarray:
        """Each point's cell, by its place in `cells`: the regions round it, in Orca's order,
        a volume's over a height range's, each later over an earlier."""
        labels: list[tuple[str, ...]] = [()] * len(points)
        for region, solid in self.regions:
            hit = self._in_region(region, solid, points)
            for k in np.flatnonzero(hit):
                labels[k] = (*labels[k], region.name)
        known: dict[tuple[str, ...], int] = {c.regions: k for k, c in enumerate(self.cells)}
        found = np.zeros(len(points), dtype=int)
        for at, given in enumerate(labels):
            if given not in known:
                known[given] = len(self.cells)
                self.cells.append(self._cell(given))
            found[at] = known[given]
        return found

    # Whether core

    def layer_of(self, along: np.ndarray) -> np.ndarray:
        """Each height along the part's layers as the layer it prints in, from its low."""
        return np.clip(np.floor((along - self.low) / self.height), 0, self.count - 1).astype(int)

    def _key(self, cell: Cell) -> tuple[float, ...]:
        return (cell.walls_mm, cell.top_mm, cell.bottom_mm)

    def sparse(self, layer: int, cell: Cell) -> list[np.ndarray] | None:
        """A layer's sparse area for a cell's settings, as rings in the layer's two axes: the
        area inside its walls, opened as Orca opens it, less its shells', grown across the
        layer as Orca grows them (`as_printed.layer_core`), and less an island region's ring
        of walls each side of its faces; or None where OCCT can't read it."""
        key = (layer, self._key(cell))
        if key in self._sparse:
            return self._sparse[key]
        at = self.low + (layer + 0.5) * self.height
        walls, top, bottom = self._key(cell)
        try:
            found = as_printed.layer_core(
                self.part, self.layers, at, walls, top, bottom, self.up,
                as_printed.open_radius(self.line, self.height),
                as_printed.MARGIN * as_printed.wall_thickness(1, self.line, self.height),
            )  # fmt: skip
            faces = [] if found is None or found[0] is None else list(found[0].Faces)
            if faces:
                faces = self._less_islands(faces, at)
        except Exception:  # noqa: BLE001 - a layer OCCT can't read is read from its neighbour's
            self.unread.add(layer)
            self._sparse[key] = None
            return None
        first, second = (a for a in range(3) if a != self.layers)
        rings = _rings(faces, first, second)
        self._sparse[key] = rings
        return rings

    def _less_islands(self, faces: list[Any], at: float) -> list[Any]:
        """A layer's sparse area less an island's ring of walls each side of its region's faces
        where they cut the part, for each region with walls of its own but a height range's."""
        import Part

        left = Part.makeCompound(faces)
        for region, solid in self.regions:
            if region.settings.walls is None or isinstance(region.shape, Layers):
                continue
            section = as_printed._section(solid, self.layers, at)
            if section is None:
                continue
            for face in section.Faces:
                out = as_printed._offset_2d(face, self.ring, self.layers)
                inner = as_printed._offset_2d(face, -self.ring, self.layers)
                band = Part.makeCompound(out)
                if inner:
                    band = band.cut(Part.makeCompound(inner))
                left = left.cut(band)
        return [f for f in left.Faces if f.Area > 1e-9]

    def core(self, points: np.ndarray, cells: np.ndarray) -> np.ndarray:
        """Whether each of `points`, in the part's own axes, is core: in the sparse area of
        the layer it prints in, for its cell's settings. A layer OCCT can't read is read from
        the nearest one it can."""
        layer = self.layer_of(points[:, self.layers])
        first, second = (a for a in range(3) if a != self.layers)
        found = np.zeros(len(points), dtype=bool)
        for k in np.unique(layer):
            for c in np.unique(cells[layer == k]):
                mine = (layer == k) & (cells == c)
                rings = self._readable(int(k), self.cells[int(c)])
                if rings:
                    found[mine] = inside(rings, points[mine][:, [first, second]])
        return found

    def _readable(self, layer: int, cell: Cell) -> list[np.ndarray] | None:
        for step in range(self.count):
            for k in (layer - step, layer + step):
                if 0 <= k < self.count:
                    rings = self.sparse(k, cell)
                    if rings is not None:
                        return rings
        return None


#: Each sub-tetrahedron of a tetrahedron split at its sides' midpoints, by its corners: the 4
#: at its corners and the 4 its middle octahedron makes, each an eighth of it.
_SUB = (
    (0, 4, 6, 7), (4, 1, 5, 8), (6, 5, 2, 9), (7, 8, 9, 3),
    (4, 5, 6, 8), (4, 6, 7, 8), (6, 7, 8, 9), (5, 6, 8, 9),
)
#: The sides' midpoints, by the corners they're between, in the order `_SUB` numbers them 4-9.
_MIDS = ((0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3))


def sub_middles(corners: np.ndarray) -> np.ndarray:
    """Each element's 8 sub-tetrahedra's middles, from its 4 corners: `[element, 8, 3]`."""
    mids = np.stack([(corners[:, i] + corners[:, j]) / 2 for i, j in _MIDS], axis=1)
    points = np.concatenate([corners, mids], axis=1)
    return np.stack([points[:, list(sub)].mean(axis=1) for sub in _SUB], axis=1)


def fine_middles(corners: np.ndarray) -> np.ndarray:
    """Each element's 64 sub-sub-tetrahedra's middles, from its 4 corners: `[element, 64, 3]`,
    each an eighth of an eighth of it."""
    mids = np.stack([(corners[:, i] + corners[:, j]) / 2 for i, j in _MIDS], axis=1)
    points = np.concatenate([corners, mids], axis=1)
    subs = np.stack([points[:, list(sub)] for sub in _SUB], axis=1)
    return sub_middles(subs.reshape(-1, 4, 3)).reshape(len(corners), 64, 3)


#: How many points each element is read at: the middles of its 8 sub-tetrahedra's 8 each.
POINTS = 64


@dataclass(frozen=True)
class Read:
    """A part's elements as read by their layers (`shares`): each one's cell, its share of
    core, its `POINTS` points in the part's own axes and which of them are core."""

    cells: np.ndarray
    share: np.ndarray
    points: np.ndarray
    core: np.ndarray


def shares(layered: Layered, corners: np.ndarray, to_own: Any) -> Read:
    """Each element's cell and its share of core, from its 4 corners in the assembly's axes,
    `to_own` taking points there to the part's own: its cell is its middle's, and its share
    its `POINTS` points' (`fine_middles`) in the sparse area of their layers, each an equal
    part of it."""
    points = to_own(fine_middles(corners).reshape(-1, 3))
    cells = layered.cell_of(to_own(corners.mean(axis=1)))
    core = layered.core(points, np.repeat(cells, POINTS)).reshape(len(corners), POINTS)
    return Read(cells, core.mean(axis=1), points.reshape(len(corners), POINTS, 3), core)


# The blend


def _mandel(tensor: np.ndarray) -> np.ndarray:
    """A stiffness C_ijkl as its 6 x 6 Mandel matrix, which inverts as the tensor does."""
    pairs = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))
    m = np.zeros((6, 6))
    for a, (i, j) in enumerate(pairs):
        for b, (k, m_) in enumerate(pairs):
            scale = (math.sqrt(2) if a > 2 else 1.0) * (math.sqrt(2) if b > 2 else 1.0)
            m[a, b] = tensor[i, j, k, m_] * scale
    return m


def _tensor(m: np.ndarray) -> np.ndarray:
    pairs = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))
    tensor = np.zeros((3, 3, 3, 3))
    for a, (i, j) in enumerate(pairs):
        for b, (k, m_) in enumerate(pairs):
            scale = (math.sqrt(2) if a > 2 else 1.0) * (math.sqrt(2) if b > 2 else 1.0)
            value = m[a, b] / scale
            for p, q in ((i, j), (j, i)):
                for r, s in ((k, m_), (m_, k)):
                    tensor[p, q, r, s] = value
    return tensor


def reuss(skin: np.ndarray, core: np.ndarray, share: float) -> np.ndarray:
    """Skin and core blended in series, `share` of it core: their compliances added by their
    shares and inverted, the Reuss bound, the least stiffness any mix of them can have. Voigt's,
    their stiffnesses added, is the most, and isn't used: a part read by it could read stiffer
    and stronger than it prints (mqucifer/print-shop#363)."""
    if share <= 0:
        return skin
    if share >= 1:
        return core
    compliance = share * np.linalg.inv(_mandel(core)) + (1 - share) * np.linalg.inv(_mandel(skin))
    return _tensor(np.linalg.inv(compliance))


def _turned(tensor: np.ndarray, turn: np.ndarray) -> np.ndarray:
    """A stiffness C_ijkl in axes turned by `turn`, whose rows are the new axes in the old."""
    return np.asarray(np.einsum("ai,bj,ck,dl,ijkl->abcd", turn, turn, turn, turn, tensor))


def _frame_of(normal: np.ndarray) -> np.ndarray:
    """Axes whose third is `normal`: as rows, in the given axes."""
    third = normal / np.linalg.norm(normal)
    other = np.eye(3)[int(np.argmin(np.abs(third)))]
    first = np.cross(other, third)
    first /= np.linalg.norm(first)
    return np.array([first, np.cross(third, first), third])


#: A stiffness's Mandel entries along a layer's plane, (11, 22, 12), and across it, (33, 23, 13).
_ALONG, _ACROSS = [0, 1, 5], [2, 3, 4]


def laminate(skin: np.ndarray, core: np.ndarray, share: float, normal: np.ndarray) -> np.ndarray:
    """Skin and core as layers square to `normal`, `share` of it core: what's along the layers
    strains alike in both, and what's across them carries the same stress in both, so the
    strain along and the stress across are averaged by share, exactly, and turned back into a
    stiffness (mqucifer/print-shop#363). For a band of walls or shells, layers along the face,
    it's their own mix: across the face as soft as Reuss's series, and along it no stiffer than
    their shares side by side, Voigt's."""
    if share <= 0:
        return skin
    if share >= 1:
        return core
    turn = _frame_of(np.asarray(normal, dtype=float))
    mixed = np.zeros((6, 6))
    for tensor, weight in ((skin, 1 - share), (core, share)):
        m = _mandel(_turned(tensor, turn))
        pp, pn = m[np.ix_(_ALONG, _ALONG)], m[np.ix_(_ALONG, _ACROSS)]
        np_, nn = m[np.ix_(_ACROSS, _ALONG)], m[np.ix_(_ACROSS, _ACROSS)]
        soft = np.linalg.inv(nn)
        block = np.zeros((6, 6))
        block[:3, :3] = pp - pn @ soft @ np_
        block[:3, 3:] = pn @ soft
        block[3:, :3] = -soft @ np_
        block[3:, 3:] = soft
        mixed += weight * block
    a, b, c, d = mixed[:3, :3], mixed[:3, 3:], mixed[3:, :3], mixed[3:, 3:]
    stiff = np.linalg.inv(d)
    m = np.zeros((6, 6))
    m[np.ix_(_ACROSS, _ACROSS)] = stiff
    m[np.ix_(_ACROSS, _ALONG)] = -stiff @ c
    m[np.ix_(_ALONG, _ACROSS)] = b @ stiff
    m[np.ix_(_ALONG, _ALONG)] = a - b @ stiff @ c
    return np.asarray(_turned(_tensor(m), turn.T))


#: How finely the ways across mixed elements' bands are told apart, in degrees: a laminate's
#: shear across its layers is its core's, so soft that layers turned a few degrees from a face
#: read far softer along it, and a degree's half costs half a percent (mqucifer/print-shop#363).
WAY_DEGREES = 1.0


def way_groups(ways: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each way as a group of ways within `WAY_DEGREES` of one another, either way round, and
    each group's own, its members' mean: the faces' own normals on flat faces, which the ways
    are snapped to (`band_normals`), exactly."""
    turned = ways * np.where(ways[np.arange(len(ways)), np.abs(ways).argmax(axis=1)] < 0,
                             -1.0, 1.0)[:, None]  # fmt: skip
    tilt = np.degrees(np.arccos(np.clip(turned[:, 2], -1.0, 1.0)))
    round_ = np.degrees(np.arctan2(turned[:, 1], turned[:, 0]))
    keys = (np.rint(tilt / WAY_DEGREES) * 1000 + np.rint(round_ / WAY_DEGREES)).astype(np.int64)
    _, groups = np.unique(keys, return_inverse=True)
    count = int(groups.max()) + 1 if len(groups) else 0
    mean = np.zeros((count, 3))
    np.add.at(mean, groups, turned)
    mean /= np.maximum(np.linalg.norm(mean, axis=1), 1e-12)[:, None]
    return groups, mean


#: How many of the part's nearest boundary triangles a mixed element's way across its band is
#: chosen from.
NEAREST = 16


def band_normals(read: Read, mixed: np.ndarray, centres: np.ndarray, faces: np.ndarray,
                 normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each mixed element's way across its band, and whether its layering is clean
    (mqucifer/print-shop#363). Its core points and its skin points are parted by the way that
    best parts them, Fisher's, their mean apart over their spread; that's clean where it parts
    every one, as in a band of walls or shells, and not where two bands meet at a corner or an
    edge. The way is then the normal of whichever of the part's `NEAREST` nearest boundary
    triangles, by their middles `faces` from the element's middle `centres`, lies most along
    it: the nearest face's own, which Fisher's, from 64 points of a skinny element, misses by
    some degrees. All in the part's own axes."""
    ways = np.zeros((len(read.share), 3))
    clean = np.zeros(len(read.share), dtype=bool)
    for e in np.flatnonzero(mixed):
        points, core = read.points[e], read.core[e]
        mean_core, mean_skin = points[core].mean(axis=0), points[~core].mean(axis=0)
        spread = np.cov(np.vstack([points[core] - mean_core, points[~core] - mean_skin]).T)
        way = np.linalg.solve(spread + 1e-9 * np.eye(3), mean_skin - mean_core)
        size = np.linalg.norm(way)
        if size == 0:
            continue
        way /= size
        along = points @ way
        ways[e] = way
        clean[e] = bool(along[core].max() < along[~core].min())
    chosen = np.flatnonzero(mixed & clean)
    count = min(NEAREST, len(faces))
    for start in range(0, len(chosen), 256):
        chunk = chosen[start : start + 256]
        apart = ((centres[chunk][:, None, :] - faces[None]) ** 2).sum(axis=2)
        near = np.argpartition(apart, count - 1, axis=1)[:, :count]
        dots = np.abs(np.einsum("nk,nmk->nm", ways[chunk], normals[near]))
        ways[chunk] = normals[near[np.arange(len(chunk)), dots.argmax(axis=1)]]
    return ways, clean


def boundary(corners: np.ndarray, numbers: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A part's boundary triangles, from its elements' corners and their nodes' numbers: each
    element face no other of its elements shares, its middle and its unit normal."""
    seen: dict[tuple[int, ...], tuple[int, int]] = {}
    for e, four in enumerate(numbers.tolist()):
        for skip in range(4):
            face = tuple(sorted(four[:skip] + four[skip + 1 :]))
            seen[face] = (-1, -1) if face in seen else (e, skip)
    kept = [(e, skip) for e, skip in seen.values() if e >= 0]
    if not kept:
        return np.zeros((0, 3)), np.zeros((0, 3))
    tri = np.array([[corners[e, i] for i in range(4) if i != skip] for e, skip in kept])
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    return tri.mean(axis=1), normal


def step_of(share: np.ndarray) -> np.ndarray:
    """Each share as its step, 0 for skin to `STEPS` for core: an eighth's of 8 middles."""
    return np.clip(np.rint(share * STEPS), 0, STEPS).astype(int)


# The check


#: How thick each band of layers is that the mesh's core is held to Orca's in, in mm, at the
#: least, and as a share of the mesh's size at the part's faces: an element smears its share
#: of core over its own height, so a band thinner than a few elements reads that smearing,
#: not the core (mqucifer/print-shop#363).
BAND = 2.0
BAND_SHARE = 3.0


def band_for(near: float) -> float:
    """How thick the bands are, for a mesh `near` at the part's faces, in mm."""
    return max(BAND, BAND_SHARE * near)


@dataclass(frozen=True)
class Bands:
    """The mesh's core held to Orca's band by band of layers (`checked`): how many bands, and
    the most any one's core is short of Orca's and over it, as a share of the band's volume."""

    bands: int
    short: float
    over: float
    unread: int

    def fails(self) -> str | None:
        """Why it doesn't hold, in words, or None where it does."""
        if self.short > as_printed.SHORT:
            return (f"its mesh's core is short of Orca's in a band of its layers by "
                    f"{100 * self.short:.1f}% of the band, past {100 * as_printed.SHORT:.0f}%: "
                    "the part would read stronger than printed")
        if self.over > as_printed.AGREE:
            return (f"its mesh's core is over Orca's in a band of its layers by "
                    f"{100 * self.over:.1f}% of the band, past {100 * as_printed.AGREE:.0f}%")
        return None


def checked(layered: Layered, corners_own: np.ndarray, read: Read, band: float = BAND) -> Bands:
    """The mesh's core held to Orca's, band by band of the part's layers, `band` thick
    (mqucifer/print-shop#363): in each band, the mesh's core, each element's share spread
    evenly over it, against Orca's, each element read at its points in their own layers;
    what's short and over as a share of the part's volume in the band."""
    axis = layered.layers
    edges = corners_own[:, 1:] - corners_own[:, :1]
    volume = np.abs(np.einsum("ni,ni->n", np.cross(edges[:, 0], edges[:, 1]), edges[:, 2])) / 6
    where = np.floor((read.points[:, :, axis] - layered.low) / band).astype(int)
    count = int(where.max()) + 1 if where.size else 0
    each = np.repeat(volume / POINTS, POINTS)
    flat = where.ravel()
    part = np.bincount(flat, weights=each, minlength=count)
    orca = np.bincount(flat, weights=each * read.core.ravel(), minlength=count)
    mesh = np.bincount(flat, weights=each * np.repeat(read.share, POINTS), minlength=count)
    full = part > 0
    ratio = np.where(full, part, 1.0)
    short = float(np.max(np.where(full, (orca - mesh) / ratio, 0.0), initial=0.0))
    over = float(np.max(np.where(full, (mesh - orca) / ratio, 0.0), initial=0.0))
    return Bands(int(full.sum()), max(short, 0.0), max(over, 0.0), len(layered.unread))
