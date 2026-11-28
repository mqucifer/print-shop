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
    #: Each layer's sparse area by the sections it's read from (`sparse`), and each section's
    #: profile (`profile`).
    _same: dict[tuple[Any, ...], list[np.ndarray] | None] = field(default_factory=dict)
    _profiles: dict[tuple[Any, ...], tuple[Any, ...]] = field(default_factory=dict)

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
        #: Every how many layers its rings are read where no face making shells is near
        #: (`read_at`), and its deepest shells over all its cells, for their reach.
        self.stride = max(1, round(STRIDE_MM / self.height))
        self.top_most = max([settings.top or 0 for settings in self._all_settings()]) * self.height
        self.bottom_most = max([settings.bottom or 0 for settings in self._all_settings()]
                               ) * self.height  # fmt: skip
        self._full: set[int] | None = None

    def _all_settings(self) -> list[Settings]:
        base = self.printed.settings
        return [base, *(r.settings.over(base) for r, _ in self.regions)]

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
        # A layer's sparse area is its section's, less what its shells take, by its sections
        # `top` above and `bottom` below (`as_printed.layer_core`), and less its islands': where
        # each of those is the same as a layer already read, so is its sparse area, as on a
        # part that's prismatic between its features, read once (`profile`).
        shape = (self._key(cell), self.profile(at), self.profile(at + top * self.up),
                 self.profile(at - bottom * self.up),
                 tuple(self.profile(at, solid) for region, solid in self.regions
                       if region.settings.walls is not None
                       and not isinstance(region.shape, Layers)))  # fmt: skip
        if shape in self._same:
            self._sparse[key] = self._same[shape]
            if self._same[shape] is None:
                self.unread.add(layer)
            return self._same[shape]
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
        self._same[shape] = rings
        return rings

    def profile(self, at: float, shape: Any = None) -> tuple[Any, ...]:
        """The part's section at `at` along its layers, or `shape`'s, as what tells it from
        another: each of its wires' length and box, to a micrometre. Two the same are one
        section, the one moved along the layers; none, an empty tuple."""
        solid = self.part if shape is None else shape
        cache = (id(solid), round(at, 9))
        if cache not in self._profiles:
            # From its section as `as_printed` makes it, once for each height.
            section = as_printed._section(solid, self.layers, float(at))
            wires = [] if section is None else list(section.Wires)
            first, second = (a for a in range(3) if a != self.layers)
            found = []
            for wire in wires:
                box = wire.BoundBox
                low = (box.XMin, box.YMin, box.ZMin)
                high = (box.XMax, box.YMax, box.ZMax)
                found.append((round(wire.Length, 6), round(low[first], 6), round(low[second], 6),
                              round(high[first], 6), round(high[second], 6)))  # fmt: skip
            self._profiles[cache] = tuple(sorted(found))
        return self._profiles[cache]

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

    def read_at(self, layer: int) -> int:
        """The layer whose rings a layer is read by (mqucifer/print-shop#363): itself where a
        face that makes shells, or a band of layers' edge, is within reach of it
        (`full_layers`); else the nearest of every `STRIDE_MM`'s, the part's section changing
        little between them, and the reply saying so."""
        if self._full is None:
            self._full = self.full_layers()
        if layer in self._full or self.stride <= 1:
            return layer
        return int(min(self.count - 1, round(layer / self.stride) * self.stride))

    def full_layers(self) -> set[int]:
        """Every layer within reach of a face that makes shells, one leaning `SHELL_DEGREES` or
        more from upright to the layers, its top and bottom shells and a margin either side;
        and of each band of layers' edges."""
        reach = max(1, math.ceil((self.top_most + self.bottom_most) / self.height)) + 2
        spans: list[tuple[float, float]] = []
        lean = math.sin(math.radians(SHELL_DEGREES))
        for face in self.part.Faces:
            u0, u1, v0, v1 = face.ParameterRange
            steep = False
            for u in np.linspace(u0, u1, 5):
                for v in np.linspace(v0, v1, 5):
                    try:
                        normal = face.normalAt(float(u), float(v))
                    except Exception:  # noqa: BLE001 - a face that has no normal there
                        continue
                    if abs((normal.x, normal.y, normal.z)[self.layers]) >= lean:
                        steep = True
            if steep:
                box = face.BoundBox
                spans.append(((box.XMin, box.YMin, box.ZMin)[self.layers],
                              (box.XMax, box.YMax, box.ZMax)[self.layers]))  # fmt: skip
        for region, _ in self.regions:
            if isinstance(region.shape, Layers):
                spans += [(region.shape.low, region.shape.low), (region.shape.high,
                                                                 region.shape.high)]  # fmt: skip
        full: set[int] = set()
        for low, high in spans:
            first = int(self.layer_of(np.array([low]))[0]) - reach
            last = int(self.layer_of(np.array([high]))[0]) + reach
            full.update(range(max(0, first), min(self.count, last + 1)))
        return full

    def _readable(self, layer: int, cell: Cell) -> list[np.ndarray] | None:
        layer = self.read_at(layer)
        for step in range(self.count):
            for k in (layer - step, layer + step):
                if 0 <= k < self.count:
                    rings = self.sparse(k, cell)
                    if rings is not None:
                        return rings
        return None


#: How far apart the layers are read whose rings are taken for those between, in mm, where no
#: face that makes shells is near (`Layered.read_at`, mqucifer/print-shop#363): a tall part
#: whose section moves with its height, as a bowed leg's, has no two layers alike, and each
#: layer's OCCT offsets and booleans were most of its reading.
STRIDE_MM = 1.0
#: How far a face leans from upright to the layers before its shells are read layer by layer.
SHELL_DEGREES = 30.0

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
    core, its `POINTS` points in the part's own axes, which of them are core, and each one's
    own cell."""

    cells: np.ndarray
    share: np.ndarray
    points: np.ndarray
    core: np.ndarray
    #: Each point's own cell.
    point_cells: np.ndarray | None = None


def shares(layered: Layered, corners: np.ndarray, to_own: Any) -> Read:
    """Each element's cell and its share of core, from its 4 corners in the assembly's axes,
    `to_own` taking points there to the part's own: its cell is its middle's, and its share
    its `POINTS` points' (`fine_middles`) in the sparse area of their layers, each an equal
    part of it."""
    points = to_own(fine_middles(corners).reshape(-1, 3))
    cells = layered.cell_of(to_own(corners.mean(axis=1)))
    # Each point by its own cell's layers, not its element's: an element across a band's or a
    # box's edge has points in both, and a cell's layers are read within its own region.
    own = layered.cell_of(points)
    core = layered.core(points, own).reshape(len(corners), POINTS)
    return Read(cells, core.mean(axis=1), points.reshape(len(corners), POINTS, 3), core,
                own.reshape(len(corners), POINTS))


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


#: A stiffness's Mandel entries along a layer's plane, (11, 22, 12), and across it, (33, 23, 13),
#: in axes whose third is the layers' normal; and along a column's axis, (11), and across it,
#: in axes whose first is its axis.
_ALONG, _ACROSS = [0, 1, 5], [2, 3, 4]
_AXIS, _AROUND = [0], [1, 2, 3, 4, 5]


def _mixed(skin: np.ndarray, core: np.ndarray, share: float, turn: np.ndarray,
           along: list[int], across: list[int]) -> np.ndarray:
    """Skin and core mixed, `share` of it core, in axes `turn`: the strains `along` alike in
    both and the stresses `across` the same in both, so those are averaged by share, exactly,
    and turned back into a stiffness."""
    mixed = np.zeros((6, 6))
    for tensor, weight in ((skin, 1 - share), (core, share)):
        m = _mandel(_turned(tensor, turn))
        pp, pn = m[np.ix_(along, along)], m[np.ix_(along, across)]
        np_, nn = m[np.ix_(across, along)], m[np.ix_(across, across)]
        soft = np.linalg.inv(nn)
        block = np.zeros((6, 6))
        a = len(along)
        block[:a, :a] = pp - pn @ soft @ np_
        block[:a, a:] = pn @ soft
        block[a:, :a] = -soft @ np_
        block[a:, a:] = soft
        mixed += weight * block
    a = len(along)
    first, second, third, fourth = mixed[:a, :a], mixed[:a, a:], mixed[a:, :a], mixed[a:, a:]
    stiff = np.linalg.inv(fourth)
    m = np.zeros((6, 6))
    m[np.ix_(across, across)] = stiff
    m[np.ix_(across, along)] = -stiff @ third
    m[np.ix_(along, across)] = second @ stiff
    m[np.ix_(along, along)] = first - second @ stiff @ third
    return np.asarray(_turned(_tensor(m), turn.T))


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
    return _mixed(skin, core, share, _frame_of(np.asarray(normal, dtype=float)), _ALONG,
                  _ACROSS)  # fmt: skip


def column(skin: np.ndarray, core: np.ndarray, share: float, axis: np.ndarray) -> np.ndarray:
    """Skin and core side by side along `axis` and in series every way across it, `share` of
    it core: where two bands meet along an edge, a long edge's walls and shells, both are
    layered along the edge, so a strain along it is alike in both, exactly, as in a laminate
    along it, and across it they're read as the weaker blend, Reuss's (mqucifer/print-shop
    #363)."""
    if share <= 0:
        return skin
    if share >= 1:
        return core
    turn = _frame_of(np.asarray(axis, dtype=float))[[2, 0, 1]]
    return _mixed(skin, core, share, turn, _AXIS, _AROUND)


def _core_part(skin: np.ndarray, core: np.ndarray, share: float, turn: np.ndarray,
               along: list[int], across: list[int]) -> np.ndarray:
    """What of a mixed element's strain is its core's own, as a map from its strain to its
    core's (`_mixed`'s mix, in axes `turn`): the strains `along` its core's, the same in both;
    and across, where both carry the same stress and the element's is their shares' sum, its
    core's, ε_core = M⁻¹((C_s - C_c) ε_along + C_s ε / (1 - f)), M = C_c + C_s f / (1 - f),
    of the across block C_nn and its coupling C_np to the along. As a tensor, in the given
    axes."""
    m_skin, m_core = _mandel(_turned(skin, turn)), _mandel(_turned(core, turn))
    pp = np.ix_(across, across)
    pn = np.ix_(across, along)
    weight = share / (1 - share)
    lead = np.linalg.inv(m_core[pp] + weight * m_skin[pp])
    mapped = np.zeros((6, 6))
    mapped[np.ix_(along, along)] = np.eye(len(along))
    mapped[pn] = lead @ (m_skin[pn] - m_core[pn])
    mapped[pp] = lead @ m_skin[pp] / (1 - share)
    return np.asarray(_turned(_tensor(mapped), turn.T))


def core_strain(kind: int, skin: np.ndarray, core: np.ndarray, share: float,
                way: np.ndarray | None) -> np.ndarray:
    """A map from a mixed element's strain to its core's own (mqucifer/print-shop#363), by the
    blend its stiffness is (`laminate`, `column`, `reuss`): so its core is judged by its own
    limits at its own strain, the weaker side of the element, and not at the element's mean,
    which an element mostly skin holds well under its core's. All core, the strain itself."""
    if share >= 1:
        return np.asarray(np.einsum("ik,jl->ijkl", np.eye(3), np.eye(3)))
    if kind == LAMINATE and way is not None:
        return _core_part(skin, core, share, _frame_of(way), _ALONG, _ACROSS)
    if kind == COLUMN and way is not None:
        return _core_part(skin, core, share, _frame_of(way)[[2, 0, 1]], _AXIS, _AROUND)
    blended = _mandel(reuss(skin, core, share))
    return np.asarray(_tensor(np.linalg.inv(_mandel(core)) @ blended))


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


#: How far apart two faces' normals must be, in degrees, to be two faces meeting at an edge,
#: and how far a third must lean along their edge to make a corner of three.
EDGE_DEGREES = 30.0

#: A mixed element's blend: a laminate across one face's normal, a column along the edge two
#: faces share, or in series every way where three meet or none is found.
LAMINATE, COLUMN, SERIES = 1, 2, 0


def band_normals(read: Read, mixed: np.ndarray, centres: np.ndarray, faces: np.ndarray,
                 normals: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each mixed element's blend and its way (mqucifer/print-shop#363), in the part's own axes.
    Its core points and its skin points are parted by the way that best parts them, Fisher's,
    their mean apart over their spread. Where that parts every one, as in a band of walls or
    shells, it's a laminate across the normal of whichever of the part's `NEAREST` nearest
    boundary triangles, by their middles `faces` from the element's middle `centres`, lies most
    along it: the nearest face's own, which Fisher's, from 64 points of a skinny element,
    misses by some degrees. Where it doesn't, and one face is all there is near it, it's that
    face's laminate still; where two faces are, two bands meet, and along the edge they share,
    the two nearby normals furthest apart, it's a column (`column`); where a third face leans
    along that edge, a corner of three, it's in series every way (`reuss`)."""
    ways = np.zeros((len(read.share), 3))
    kinds = np.full(len(read.share), SERIES, dtype=int)
    clean = np.zeros(len(read.share), dtype=bool)
    for e in np.flatnonzero(mixed):
        points, core = read.points[e], read.core[e]
        if core.all() or not core.any():
            # Mixed by its exact share but not at its points: its nearest face's way, below.
            continue
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
    chosen = np.flatnonzero(mixed)
    count = min(NEAREST, len(faces))
    apart_cos = math.cos(math.radians(EDGE_DEGREES))
    lean_sin = math.sin(math.radians(EDGE_DEGREES))
    for start in range(0, len(chosen), 256):
        chunk = chosen[start : start + 256]
        apart = ((centres[chunk][:, None, :] - faces[None]) ** 2).sum(axis=2)
        near = np.argpartition(apart, count - 1, axis=1)[:, :count]
        nearby = normals[near]
        dots = np.abs(np.einsum("nk,nmk->nm", ways[chunk], nearby))
        first = nearby[np.arange(len(chunk)), dots.argmax(axis=1)]
        for row, e in enumerate(chunk):
            if clean[e]:
                ways[e], kinds[e] = first[row], LAMINATE
                continue
            others = nearby[row]
            cos = np.abs(others @ first[row])
            if cos.min() > apart_cos:
                # One face is all there is near it: its band's, which its 64 points part
                # only roughly, not a corner.
                ways[e], kinds[e] = first[row], LAMINATE
                continue
            second = others[cos.argmin()]
            edge = np.cross(first[row], second)
            edge /= np.linalg.norm(edge)
            if np.abs(others @ edge).max() > lean_sin:
                continue
            ways[e], kinds[e] = edge, COLUMN
    return ways, kinds


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


#: How near to the fullest cell round it a cell's core must be for it to be read
#: (`element_averaged`).
FULLEST = 0.9


@dataclass
class Averaged:
    """A core's strain averaged over its own cell (`element_averaged`): each place's that's
    read, its middle, and the share of its cell that's core."""

    strain: np.ndarray
    at: np.ndarray
    fill: np.ndarray


# Exact, layer by layer


@dataclass
class Exact:
    """A printed part's elements read exactly by its layers (`exact`, mqucifer/print-shop#363):
    each element's volume, straight-sided between its corners, and what of it is core, lies in
    a wedge of its skin, and lies in a wedge and is core, in mm³; each cell's volume and core;
    and each layer's core by Orca's own rings, its wedges' volume, and each element's volume
    in it, for the check (`exact_checked`)."""

    whole: np.ndarray
    core: np.ndarray
    in_wedges: np.ndarray
    wedges_core: np.ndarray
    cell_whole: dict[int, float]
    cell_core: dict[int, float]
    #: Each layer's core by Orca's rings, where the layer is one cell; else its elements'.
    layer_core: np.ndarray
    layer_wedges: np.ndarray
    #: Each element's volume in each layer, as (element, layer, mm³) rows.
    spans: np.ndarray
    #: Whether a region cut a layer across its plane, where each element's part in it is its
    #: section's middle's cell.
    in_plane: bool


def exact(layered: Layered, corners: np.ndarray, prisms: np.ndarray) -> Exact:
    """Each element of a printed part, by its straight-sided `corners` `[e, 4, 3]` in the part's
    own axes, read exactly by its layers (`areas`): in each layer it spans, its section where
    it spans the layer, inside that layer's core rings for its section's middle's cell, and
    inside each of the skin's wedges, by their straight-sided `prisms` `[w, 6, 3]`, and inside
    both; each by how far it spans the layer. Each layer's own core is its rings' area where
    no region but a band of layers cuts it, else its elements' sum."""
    from . import areas

    axis, low, height = layered.layers, layered.low, layered.height
    first, second = (a for a in range(3) if a != axis)
    whole, core = np.zeros(len(corners)), np.zeros(len(corners))
    in_wedges, wedges_core = np.zeros(len(corners)), np.zeros(len(corners))
    cell_whole: dict[int, float] = {}
    cell_core: dict[int, float] = {}
    layer_core = np.zeros(layered.count)
    layer_wedges = np.zeros(layered.count)
    spans: list[tuple[int, int, float]] = []
    in_plane = any(not isinstance(r.shape, Layers) for r, _ in layered.regions)
    e_low, e_high = corners[..., axis].min(axis=1), corners[..., axis].max(axis=1)
    w_low = prisms[..., axis].min(axis=1) if len(prisms) else np.zeros(0)
    w_high = prisms[..., axis].max(axis=1) if len(prisms) else np.zeros(0)
    pair_e, pair_w = areas.overlapping(corners.min(axis=1), corners.max(axis=1),
                                       prisms.min(axis=1), prisms.max(axis=1)) if len(
        prisms) else (np.zeros(0, dtype=int), np.zeros(0, dtype=int))  # fmt: skip
    rings_of: dict[tuple[int, int], list[np.ndarray]] = {}

    def rings(k: int, c: int) -> list[np.ndarray]:
        if (k, c) not in rings_of:
            rings_of[(k, c)] = layered._readable(k, layered.cells[c]) or []
        return rings_of[(k, c)]

    for k in range(layered.count):
        bottom = low + k * height
        top = min(low + (k + 1) * height, layered.high)
        if top <= bottom:
            continue
        mine = np.flatnonzero((e_low < top) & (e_high > bottom))
        if len(mine):
            under, over = np.maximum(e_low[mine], bottom), np.minimum(e_high[mine], top)
            at, span = (under + over) / 2, over - under
            poly, count = areas.sections(corners[mine], areas.TET_EDGES, axis, at)
            size = areas.area(poly, count)
            middle = np.zeros((len(mine), 3))
            middle[:, [first, second]] = (poly * (np.arange(poly.shape[1])[None] <
                                          count[:, None])[..., None]).sum(axis=1) / np.maximum(
                count, 1)[:, None]  # fmt: skip
            middle[:, axis] = at
            cells = layered.cell_of(middle)
            held = np.zeros(len(mine))
            for c in np.unique(cells):
                its = cells == c
                held[its] = areas.inside_rings(poly[its], count[its], rings(k, int(c)))
                cell_whole[int(c)] = cell_whole.get(int(c), 0.0) + float((size * span)[its].sum())
                cell_core[int(c)] = cell_core.get(int(c), 0.0) + float((held * span)[its].sum())
            whole[mine] += size * span
            core[mine] += held * span
            spans += [(int(e), k, float(v)) for e, v in zip(mine, size * span, strict=True)]
            alone = len(np.unique(cells)) == 1 and not in_plane
            layer_core[k] = (areas.rings_area(rings(k, int(cells[0]))) * (top - bottom)
                             if alone else float((held * span).sum()))  # fmt: skip
        live_w: np.ndarray = (np.flatnonzero((w_low < top) & (w_high > bottom)) if len(prisms)
                              else np.zeros(0, dtype=int))  # fmt: skip
        if len(live_w):
            under, over = np.maximum(w_low[live_w], bottom), np.minimum(w_high[live_w], top)
            poly, count = areas.sections(prisms[live_w], areas.PRISM_EDGES, axis,
                                         (under + over) / 2)  # fmt: skip
            layer_wedges[k] = float((areas.area(poly, count) * (over - under)).sum())
        live: np.ndarray = np.flatnonzero(
            (np.maximum(e_low[pair_e], w_low[pair_w]) < top)
            & (np.minimum(e_high[pair_e], w_high[pair_w]) > bottom)) if len(pair_e) else np.zeros(
            0, dtype=int)  # fmt: skip
        if len(live):
            e, w = pair_e[live], pair_w[live]
            under = np.maximum.reduce([e_low[e], w_low[w], np.full(len(e), bottom)])
            over = np.minimum.reduce([e_high[e], w_high[w], np.full(len(e), top)])
            at, span = (under + over) / 2, over - under
            se, ce = areas.sections(corners[e], areas.TET_EDGES, axis, at)
            sw, cw = areas.sections(prisms[w], areas.PRISM_EDGES, axis, at)
            both, cb = areas.clip(se, ce, sw, cw)
            shared = areas.area(both, cb)
            middle = np.zeros((len(e), 3))
            middle[:, [first, second]] = (se * (np.arange(se.shape[1])[None] < ce[:, None])[
                ..., None]).sum(axis=1) / np.maximum(ce, 1)[:, None]  # fmt: skip
            middle[:, axis] = at
            cells = layered.cell_of(middle)
            cored = np.zeros(len(e))
            for c in np.unique(cells):
                its = cells == c
                cored[its] = areas.inside_rings(both[its], cb[its], rings(k, int(c)))
            np.add.at(in_wedges, e, shared * span)
            np.add.at(wedges_core, e, cored * span)
    return Exact(whole, core, in_wedges, wedges_core, cell_whole, cell_core, layer_core,
                 layer_wedges, np.array(spans).reshape(-1, 3), in_plane)


def exact_checked(layered: Layered, found: Exact, share: np.ndarray, band: float = BAND,
                  ) -> Bands:
    """The mesh's core held to Orca's, band by band of the part's layers, `band` thick, exactly
    (mqucifer/print-shop#363): in each band, each element's `share` of its core spread over
    its volume in each of its layers, less the skin's wedges' volume there, against Orca's own
    core there, its rings' area; what's short and over as a share of the band's volume. Its
    whole skin: the wedges, and what of each element is skin by its share."""
    count = max(1, round((layered.high - layered.low) / band))
    each = max(1, math.ceil(layered.count / count))
    rows = found.spans
    element, layer, volume = rows[:, 0].astype(int), rows[:, 1].astype(int), rows[:, 2]
    bands = np.minimum(layer // each, count - 1)
    whole = np.bincount(bands, weights=volume, minlength=count)
    mesh = np.bincount(bands, weights=volume * share[element], minlength=count)
    where = np.minimum(np.arange(layered.count) // each, count - 1)
    mesh -= np.bincount(where, weights=found.layer_wedges, minlength=count)
    orca = np.bincount(where, weights=found.layer_core, minlength=count)
    full = whole > 0
    ratio = np.where(full, whole, 1.0)
    short = float(np.max(np.where(full, (orca - mesh) / ratio, 0.0), initial=0.0))
    over = float(np.max(np.where(full, (mesh - orca) / ratio, 0.0), initial=0.0))
    return Bands(int(full.sum()), max(short, 0.0), max(over, 0.0), len(layered.unread))


def element_averaged(middles: np.ndarray, strains: np.ndarray, weights: np.ndarray,
                     cell: float) -> Averaged:
    """A core's strain averaged over its own cell, element by element (mqucifer/print-shop#363):
    each element's core's mean strain, at its core's middle, weighted by its core's volume, and
    averaged over the elements whose middles lie within its cell, a cube `cell` across centred
    on it; its fill the share of that cube their core holds. Its stiffness is its cell's mean,
    so it's read no finer than that; and a thick core is read in its fullest cells, each that
    holds `FULLEST` of the most any within a cell of it holds, as a lattice's cell lies in its
    core and not centred on its edge. Per element, not per voxel, so it holds what the mesh
    does, whatever the part's size."""
    held = np.flatnonzero(weights > 0)
    if not len(held):
        return Averaged(np.zeros((0, 3, 3)), np.zeros((0, 3)), np.zeros(0))
    where, strain, weight = middles[held], strains[held], weights[held]
    # Voxels half a cell across: the 27 round one hold every place within its cell, and
    # little more.
    key = np.floor(where / (cell / 2)).astype(int)
    key -= key.min(axis=0)
    shape = key.max(axis=0) + 3
    flat = np.ravel_multi_index((key + 1).T, shape)
    order = np.argsort(flat, kind="stable")
    sorted_flat = flat[order]
    offsets = [np.array([dx, dy, dz]) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
               for dz in (-1, 0, 1)]  # fmt: skip

    def pairs(rows: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
        """Each of `rows` with each place within its cell, by the 27 voxels round it."""
        found = []
        for offset in offsets:
            other = np.ravel_multi_index((key[rows] + 1 + offset).T, shape)
            low = np.searchsorted(sorted_flat, other, side="left")
            many = np.searchsorted(sorted_flat, other, side="right") - low
            if not many.any():
                continue
            mine = np.repeat(rows, many)
            step = np.arange(len(mine)) - np.repeat(np.cumsum(many) - many, many)
            theirs = order[np.repeat(low, many) + step]
            apart = where[theirs] - where[mine]
            inside = np.all((apart >= -cell / 2) & (apart < cell / 2), axis=1)
            found.append((mine[inside], theirs[inside]))
        return found

    # A few thousand places at a time, so their pairs fit the memory however fine the mesh.
    per_voxel = max(1, int(np.bincount(flat).max()))
    chunk = max(1, 2_000_000 // (27 * per_voxel))
    summed = np.zeros(len(where))
    weighted = np.zeros((len(where), 3, 3))
    for start in range(0, len(where), chunk):
        rows = np.arange(start, min(start + chunk, len(where)))
        for mine, theirs in pairs(rows):
            summed += np.bincount(mine, weights=weight[theirs], minlength=len(where))
            for i in range(3):
                for j in range(3):
                    weighted[:, i, j] += np.bincount(
                        mine, weights=weight[theirs] * strain[theirs, i, j],
                        minlength=len(where))  # fmt: skip
    averaged = weighted / summed[:, None, None]
    fill = summed / cell**3
    fullest = np.zeros(len(where))
    for start in range(0, len(where), chunk):
        rows = np.arange(start, min(start + chunk, len(where)))
        for mine, theirs in pairs(rows):
            np.maximum.at(fullest, mine, fill[theirs])
    read = fill >= FULLEST * fullest
    return Averaged(averaged[read], where[read], fill[read])
