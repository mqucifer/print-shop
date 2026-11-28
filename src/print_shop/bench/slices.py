"""Each layer of a printed part read from the triangle mesh Orca slices for it, with GEOS for its
offsets and booleans (mqucifer/print-shop#363).

The Sponsor's decision: "Orca will be generating the gcode from the same logic". So the part is
meshed as the study meshes it for Orca (`bench/refine.py`): OCCT's `BRepMesh` in the part's own
axes at Orca's own defaults, 0.003 mm linear and 0.5 rad angular deflection, absolute (`Mesh.of`).
Each layer's section is the loops where its plane crosses that mesh's triangles, joined by the
mesh's own edges, so no join is a guess (`Mesh.section`); and each layer's core is read from it
as Orca reads its sparse area (`layer_rings`): the section offset in by its walls, opened by
Orca's opening, less what its shells take, those grown across the layer by Orca's margin, and
less an island region's ring of walls. Shapely's buffers are round at their corners, as OCCT's
arc offsets were, with `QUAD` segments to a quarter circle.

Where OCCT read each layer on the B-rep, a tall bowed part's thousands of layers took most of a
read: its slices, offsets and booleans. The mesh's sections differ from the B-rep's by at most
the mesh's chord, 0.003 mm, and the round corners' segments by `QUAD`'s, both of Orca's own
kind; the tests bound each layer's core against OCCT's on the B-rep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Orca's own STEP meshing, as the study meshes a part for it (`bench/refine.py`).
LINEAR, ANGULAR = 0.003, 0.5
#: How far a layer's plane is moved off where it's read, in mm, so it never passes through one
#: of the mesh's points, where a loop's join would be a guess.
NUDGE = 1e-7
#: Segments to a quarter circle in a buffer's round corner: a corner of radius r is out by
#: r (1 - cos(pi / (4 QUAD))), 0.2% of r at 16.
QUAD = 16


@dataclass
class Mesh:
    """A solid's triangle mesh, in its own axes, its layers along `axis`."""

    points: np.ndarray
    triangles: np.ndarray
    axis: int
    _sections: dict[float, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        along = self.points[self.triangles][..., self.axis]
        self.low, self.high = along.min(axis=1), along.max(axis=1)
        self.order = np.argsort(self.low, kind="stable")
        self.sorted_low = self.low[self.order]

    @classmethod
    def of(cls, shape: Any, axis: int) -> Mesh:
        """A solid meshed as the study meshes it for Orca: OCCT's `BRepMesh` at `LINEAR` and
        `ANGULAR`, absolute (`MeshPart.meshFromShape`), in FreeCAD's image."""
        import MeshPart

        meshed = MeshPart.meshFromShape(Shape=shape, LinearDeflection=LINEAR,
                                        AngularDeflection=ANGULAR, Relative=False)  # fmt: skip
        points, facets = meshed.Topology
        return cls(np.array([[p.x, p.y, p.z] for p in points], dtype=float),
                   np.array(facets, dtype=int).reshape(-1, 3), axis)  # fmt: skip

    def steep(self, lean: float) -> np.ndarray:
        """Each triangle's span along the layers where its normal leans `lean` (its sine) or
        more toward them, as rows of low and high."""
        a, b, c = (self.points[self.triangles[:, i]] for i in range(3))
        normal = np.cross(b - a, c - a)
        size = np.linalg.norm(normal, axis=1)
        steep = np.abs(normal[:, self.axis]) >= lean * np.maximum(size, 1e-30)
        return np.column_stack([self.low[steep], self.high[steep]])

    def leans(self, count: int, low: float, height: float, least: float = 0.0) -> np.ndarray:
        """Each of `count` layers `height` thick from `low`, how far its section moves for its
        height (mqucifer/print-shop#363's review): the most of the triangles that cross it,
        each its lean's tangent from upright, its normal's share along the layers over its
        share across them; those leaning less than `least` left out, as moving it too little
        to matter. A flat triangle's is infinite."""
        a, b, c = (self.points[self.triangles[:, i]] for i in range(3))
        normal = np.cross(b - a, c - a)
        size = np.maximum(np.linalg.norm(normal, axis=1), 1e-30)
        along = np.abs(normal[:, self.axis]) / size
        across = np.sqrt(np.maximum(1 - along**2, 0.0))
        tangent = np.where(across > 1e-12, along / np.maximum(across, 1e-12), np.inf)
        found = np.zeros(count)
        mine = np.flatnonzero(tangent > least)
        if not len(mine):
            return found
        first = np.clip(np.floor((self.low[mine] - low) / height).astype(int), 0, count - 1)
        last = np.clip(np.floor((self.high[mine] - low) / height).astype(int), 0, count - 1)
        many = last - first + 1
        layer = np.repeat(first, many) + (np.arange(many.sum()) - np.repeat(np.cumsum(many) - many,
                                                                           many))  # fmt: skip
        np.maximum.at(found, layer, np.repeat(tangent[mine], many))
        return found

    def section(self, at: float) -> Any:
        """The solid's section at `at` along its layers, as a Shapely geometry in its other two
        axes: each loop where the plane crosses its triangles, joined by the mesh's own edges,
        held by the even-odd rule. Made once for each height."""
        import shapely

        key = round(float(at), 9)
        if key in self._sections:
            return self._sections[key]
        plane = float(at) + NUDGE
        upto = int(np.searchsorted(self.sorted_low, plane, side="right"))
        crossing = self.order[:upto]
        crossing = crossing[self.high[crossing] > plane]
        found: Any = shapely.Polygon()
        if len(crossing):
            found = self._loops(crossing, plane)
        self._sections[key] = found
        return found

    def _loops(self, crossing: np.ndarray, plane: float) -> Any:
        import shapely

        first, second = (a for a in range(3) if a != self.axis)
        tri = self.triangles[crossing]
        heights = self.points[tri][..., self.axis]
        below = heights < plane
        # Each triangle's two edges the plane crosses, each by its two points' numbers.
        edges = ((0, 1), (1, 2), (2, 0))
        cut = np.stack([below[:, i] != below[:, j] for i, j in edges], axis=1)
        ends: list[list[Any]] = []
        for row, flags in zip(tri.tolist(), cut.tolist(), strict=True):
            pair = [tuple(sorted((row[i], row[j]))) for (i, j), f in zip(edges, flags,
                                                                          strict=True) if f]
            if len(pair) == 2:
                ends.append(pair)
        where: dict[tuple[int, int], np.ndarray] = {}
        joined: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for a, b in ends:
            joined.setdefault(a, []).append(b)
            joined.setdefault(b, []).append(a)
        for i, j in joined:
            p, q = self.points[i], self.points[j]
            t = (plane - p[self.axis]) / (q[self.axis] - p[self.axis])
            point = p + t * (q - p)
            where[(i, j)] = np.array([point[first], point[second]])
        seen: set[tuple[int, int]] = set()
        rings: list[np.ndarray] = []
        for start in joined:
            if start in seen:
                continue
            loop: list[tuple[int, int]] = [start]
            previous: tuple[int, int] | None = None
            here = start
            seen.add(start)
            while True:
                nexts = [n for n in joined[here] if n != previous]
                if not nexts:
                    break
                previous, here = here, nexts[0]
                if here == start:
                    break
                if here in seen:
                    break
                seen.add(here)
                loop.append(here)
            if len(loop) >= 3:
                rings.append(np.array([where[k] for k in loop]))
        shape: Any = shapely.Polygon()
        for ring in sorted(rings, key=lambda r: -abs(_signed(r))):
            shape = shape.symmetric_difference(shapely.Polygon(ring).buffer(0))
        return shape


def _signed(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def rings_of(shape: Any) -> list[np.ndarray]:
    """A Shapely geometry's rings, outer and holes alike, for the even-odd rule."""
    import shapely

    rings: list[np.ndarray] = []
    for polygon in shapely.get_parts(shape):
        if polygon.geom_type != "Polygon" or polygon.is_empty:
            continue
        rings.append(np.asarray(polygon.exterior.coords)[:-1])
        rings += [np.asarray(hole.coords)[:-1] for hole in polygon.interiors]
    return [r for r in rings if len(r) >= 3]


def layer_rings(mesh: Mesh, at: float, walls: float, top: float, bottom: float, up: float,
                open_by: float, margin: float,
                islands: tuple[tuple[Mesh, float], ...] = ()) -> list[np.ndarray]:
    """A layer's sparse area at `at` along the part's layers, as Orca reads it from its slice,
    as rings: its section (`Mesh.section`) offset in by its `walls`, opened by `open_by` as
    Orca opens it before it lays sparse infill, less what its shells take, what of it isn't
    there `top` above or `bottom` below (`up` the way its top shells look), grown `margin`
    across the layer as Orca grows them, and less each island region's ring of walls, `ring`
    each side of its faces. None where the part has no section there: no rings."""
    own = mesh.section(at)
    if own.is_empty:
        return []
    inner = own.buffer(-walls, quad_segs=QUAD)
    if open_by > 0 and not inner.is_empty:
        inner = inner.buffer(-open_by, quad_segs=QUAD).buffer(open_by, quad_segs=QUAD)
    taken: Any = None
    for along in (top * up, -bottom * up):
        if along == 0:
            continue
        rest = own.difference(mesh.section(at + along))
        taken = rest if taken is None else taken.union(rest)
    if taken is not None and not taken.is_empty:
        if margin > 0:
            taken = taken.buffer(margin, quad_segs=QUAD)
        inner = inner.difference(taken)
    for island, ring in islands:
        section = island.section(at)
        if section.is_empty:
            continue
        band = section.buffer(ring, quad_segs=QUAD).difference(
            section.buffer(-ring, quad_segs=QUAD))
        inner = inner.difference(band)
    return rings_of(inner)


#: How finely a section's rings tell it from another's (`profile`), in mm.
PROFILE_MM = 1e-5


def profile(mesh: Mesh, at: float) -> tuple[Any, ...]:
    """What tells a section from another: its rings themselves, each point to `PROFILE_MM`,
    as a hash (mqucifer/print-shop#363's review: by its area, length and box alone, a hole
    drilled at 45° through a block reused the first layer's rings all the way up). Points on a
    straight side between two others are left out first, as where a face's diagonal triangles
    cut it at a point that slides along the side with the height, the same section."""
    import shapely

    shape = mesh.section(at)
    if shape.is_empty:
        return ()
    plain = shapely.normalize(shape.simplify(PROFILE_MM / 10, preserve_topology=True))
    rounded = np.round(shapely.get_coordinates(plain) / PROFILE_MM).astype(np.int64)
    return (round(float(shape.area), 6), hash(rounded.tobytes()), len(rounded))
