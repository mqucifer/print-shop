"""A one-layer point at a hole's rim, for `thin` (mqucifer/print-shop#238).

A bore at 45° to the layers that ends on a flat face square to it has, at each rim, one layer
whose section grazes it and leaves a point under the nozzle's width, one layer tall, in a plate
20 mm thick. It isn't a thin wall, and no design gets out of it. A thin spot is such a point
only where all three are true:

- it runs through one layer, which `thin` says;
- it lies at a hole's rim, where the hole meets a face: every point of it within the nozzle's
  width of a closed edge inside a flat face, past which the surface turns into the material,
  square to the face or wider. A point under the nozzle across is that near such an edge: the
  two sides of the corner are each shorter than the line across them. An edge sharper than
  square is a wedge, whose wall square to its surfaces runs out to nothing, so it stays thin;
- the wall there, read square to the surfaces and not in the layer's plane, is at least the
  nozzle. That's the surfaces the point lies on, on both sides of the rim; the hole's own
  surface, on from the rim as far as it doesn't turn a corner out of the hole; and what lies
  straight down the opening, where a floor has a skin behind it. So a hole with a thin wall
  beside it, or a thin skin over its end, keeps its points as thin.

Where a hole breaks out at a slant, its edge there isn't a wall: a reading of the hole's surface
within a nozzle of an edge sharper than square, that comes out through the face past that edge,
is the edge's. One that comes out anywhere else is a wall, however near an edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
import trimesh

from .limits import under
from .shapes import FINE, hits

#: Two triangles are one flat face within this, as 1 less their normals' dot product, and
#: within `LEVEL` of one plane, in mm: the finest chords along a curve turn far more.
FLAT = 5e-7
LEVEL = 1e-4
#: A rim is square or wider to within this, as the cosine between its two surfaces' normals.
SQUARE = 1e-3
#: An edge turns out of a hole's surface past this, as the sine of the turn.
TURNS = 1e-6
#: A section's point is on the surface within this, in mm.
ON = 1e-4
#: A ray starts this far inside the surface, in mm, as `thin`'s own do.
INTO = 1e-5
#: The most rays one hole's surface, and the straight down its opening, are each read with.
RAYS = 20_000


@dataclass
class Rim:
    """A closed edge inside a flat face, past which the surface turns into the material."""

    face: set[int]
    beside: list[int]
    corners: np.ndarray
    normal: np.ndarray
    #: The least wall of the hole under the nozzle and where, once read: None where there's none.
    thin: dict[str, Any] | None = None
    read: bool = False


class Rims:
    """A mesh's holes' rims, each found when a thin spot is near it."""

    def __init__(self, mesh: trimesh.Trimesh, built: Any, length: float, nozzle: float) -> None:
        self.built, self.length, self.nozzle = built, length, nozzle
        self.points = np.asarray(mesh.vertices, dtype=float)
        self.faces = np.asarray(mesh.faces)
        self.normals = np.asarray(mesh.face_normals, dtype=float)
        self.corners = self.points[self.faces]
        self.low, self.high = self.corners.min(axis=1), self.corners.max(axis=1)
        count = len(self.points)
        starts, ends = self.faces.ravel(), self.faces[:, [1, 2, 0]].ravel()
        keys, back = starts * count + ends, ends * count + starts
        order = np.argsort(keys)
        at = np.minimum(np.searchsorted(keys, back, sorter=order), len(keys) - 1)
        other = order[at]
        # The triangle across each edge, or -1 where the surface is open there.
        self.across = np.where(keys[other] == back, other // 3, -1).reshape(-1, 3)
        self.face_of: dict[int, list[Rim]] = {}
        #: The least wall under the nozzle of each surface read: a bore's two rims share one.
        self.walls: dict[frozenset[int], dict[str, Any] | None] = {}

    def point(self, piece: Any, z: float) -> dict[str, Any] | None:
        """Whether a section's thin `piece` at height `z` lies at a hole's rim: the rim, where
        it is and how far across, the least wall read square there, and `thin`, where that's
        under the nozzle. None where it isn't at a rim."""
        ring = shapely.segmentize(piece.exterior, self.nozzle / 8)
        flat = np.asarray(ring.coords)[:, :2]
        spot = np.c_[flat, np.full(len(flat), z)]
        reach = self.nozzle + FINE[0]
        low, high = spot.min(axis=0) - reach, spot.max(axis=0) + reach
        near = np.nonzero(((self.low <= high) & (self.high >= low)).all(axis=1))[0]
        found: dict[int, Rim] = {}
        for seed in near.tolist():
            if seed not in self.face_of:
                self._read_face(seed)
            for rim in self.face_of[seed]:
                found[id(rim)] = rim
        for rim in found.values():
            away = _to_loop(spot, rim.corners)
            if float(away.max()) > reach:
                continue
            here = self._there(spot, away, near, rim)
            if here is None:
                continue
            thin: dict[str, Any] | None = here
            if not under(here["wall_mm"], self.nozzle):
                if not rim.read:
                    rim.read, rim.thin = True, self._hole(rim)
                thin = rim.thin
            middle = rim.corners.mean(axis=0)
            over = np.linalg.norm(rim.corners - middle, axis=1).max()
            return {
                "rim": [round(float(v), 3) for v in middle],
                "rim_across_mm": round(2 * float(over), 3),
                "wall_mm": here["wall_mm"],
                "thin": thin,
            }
        return None

    def _read_face(self, seed: int) -> None:
        """The flat face a triangle is in, and the rims of the holes in it."""
        normal, level = self.normals[seed], float(self.corners[seed, 0] @ self.normals[seed])
        face, to_read = {seed}, [seed]
        while to_read:
            for other in self.across[to_read.pop()].tolist():
                if other < 0 or other in face:
                    continue
                if float(self.normals[other] @ normal) < 1 - FLAT:
                    continue
                if np.abs(self.corners[other] @ normal - level).max() > LEVEL:
                    continue
                face.add(other)
                to_read.append(other)
        rims = []
        for loop in self._edge(face):
            at = self.points[[start for start, _, _ in loop]]
            turned = np.cross(at - at[0], np.roll(at, -1, axis=0) - at[0]).sum(axis=0)
            if float(turned @ normal) >= 0:
                continue  # The face's own outline: material is inside it.
            beside = [other for _, _, other in loop]
            # A boss's foot is a loop inside a face too, and its surface rises from it.
            if min(beside) < 0 or not all(
                float((self.corners[other] @ normal - level).mean()) < 0 for other in beside
            ):
                continue
            if float((self.normals[beside] @ normal).min()) < -SQUARE:
                continue  # Sharper than square: a wedge.
            rims.append(Rim(face, beside, at, normal))
        for one in face:
            self.face_of[one] = rims

    def _edge(self, face: set[int]) -> list[list[tuple[int, int, int]]]:
        """A flat face's edge as closed loops, each edge its two points and what's across it."""
        out: dict[int, list[tuple[int, int, int]]] = {}
        for one in sorted(face):
            corners = self.faces[one].tolist()
            for k, other in enumerate(self.across[one].tolist()):
                if other not in face:
                    out.setdefault(corners[k], []).append((corners[k], corners[(k + 1) % 3], other))
        loops = []
        for start in list(out):
            while out[start]:
                loop = [out[start].pop()]
                while loop[-1][1] != start and out.get(loop[-1][1]):
                    loop.append(out[loop[-1][1]].pop())
                if loop[-1][1] == start and len(loop) >= 3:
                    loops.append(loop)
        return loops

    def _square(self, point: np.ndarray, normal: np.ndarray) -> float:
        """The material straight in from a point on the surface, square to it."""
        met = hits(self.built, point - normal * INTO, -normal, self.length)
        return met[0] + INTO if met else 0.0

    def _there(
        self, spot: np.ndarray, away: np.ndarray, near: np.ndarray, rim: Rim
    ) -> dict[str, Any] | None:
        """The least wall square to the surfaces the point lies on, or None where it doesn't lie
        on both the face and what turns into the material from it."""
        corners = self.corners[near]
        sides = set()
        least: dict[str, Any] | None = None
        for point, off in zip(spot, away, strict=True):
            if off <= FINE[0]:
                continue  # On the rim itself, where square to the surface is either way.
            closest = trimesh.triangles.closest_point(  # type: ignore[no-untyped-call]
                corners, np.tile(point, (len(corners), 1))
            )
            distance = np.linalg.norm(closest - point, axis=1)
            k = int(distance.argmin())
            if distance[k] > ON:
                continue  # Where opening by the nozzle cut it off, inside the material.
            sides.add(int(near[k]) in rim.face)
            wall = self._square(point, self.normals[near[k]])
            if least is None or wall < least["wall_mm"]:
                least = {"wall_mm": wall, "at": [round(float(v), 3) for v in point]}
        return least if len(sides) == 2 else None

    def _hole(self, rim: Rim) -> dict[str, Any] | None:
        """The least of a hole's wall under the nozzle, and where, or None."""
        mine = self._surface(rim)
        key = frozenset(mine)
        if key not in self.walls:
            self.walls[key] = self._least_square(mine)
        worst = self.walls[key]

        def read(wall: float, at: np.ndarray) -> None:
            nonlocal worst
            if under(wall, self.nozzle) and (worst is None or wall < worst["wall_mm"]):
                worst = {"wall_mm": wall, "at": [round(float(v), 3) for v in at]}

        for start in self._down(rim):
            met = hits(self.built, start, -rim.normal, self.length)
            met = met[len(met) % 2 :]
            for into, out in zip(met[0::2], met[1::2], strict=True):
                read(out - into, start - into * rim.normal)
        return worst

    def _surface(self, rim: Rim) -> list[int]:
        """The hole's own surface: from the rim on across every edge that doesn't turn out of
        the hole."""
        mine, to_read = set(rim.beside), list(dict.fromkeys(rim.beside))
        while to_read:
            one = to_read.pop()
            for k, other in enumerate(self.across[one].tolist()):
                if other < 0 or other in mine or other in rim.face:
                    continue
                edge = self.corners[one, (k + 1) % 3] - self.corners[one, k]
                turn = np.cross(self.normals[one], self.normals[other]) @ edge
                if float(turn) > TURNS * float(np.linalg.norm(edge)):
                    continue
                mine.add(other)
                to_read.append(other)
        return sorted(mine)

    def _least_square(self, order: list[int]) -> dict[str, Any] | None:
        """The least wall under the nozzle square to a surface, and where, read a nozzle apart
        all over it."""
        a, b, c = (self.corners[order, k] for k in range(3))
        area = float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum()) / 2
        step = max(self.nozzle, math.sqrt(area / RAYS))
        # Chords' slivers lie many to a nozzle's width, so one point stands for each cube.
        read: dict[tuple[int, ...], tuple[np.ndarray, np.ndarray]] = {}
        for one in order:
            tip = self.corners[one]
            # Along it from a corner to the shortest side, and across: chords' slivers are long.
            sides = [float(np.linalg.norm(tip[(k + 1) % 3] - tip[k])) for k in range(3)]
            k = int(np.argmin(sides))
            first, last, apex = tip[k], tip[(k + 1) % 3], tip[(k + 2) % 3]
            long = math.ceil(float(np.linalg.norm((first + last) / 2 - apex)) / step)
            wide = math.ceil(sides[k] / step)
            way = np.round(2 * self.normals[one]).astype(int).tolist()
            for i in range(max(long, 1)):
                along = (i + 0.5) / max(long, 1)
                for j in range(max(wide, 1)):
                    on = first + (j + 0.5) / max(wide, 1) * (last - first)
                    point = apex + along * (on - apex)
                    cube = np.floor(point / step).astype(int).tolist()
                    read.setdefault((*cube, *way), (point, self.normals[one]))
        # The edges sharper than square where the surface ends, and the face past each.
        mine = set(order)
        wedges = [
            (one, k, other)
            for one in order
            for k, other in enumerate(self.across[one].tolist())
            if other >= 0
            and other not in mine
            and float(self.normals[one] @ self.normals[other]) < -SQUARE
        ]
        starts = np.array([self.corners[one, k] for one, k, _ in wedges]).reshape(-1, 3)
        runs = np.array([self.corners[one, (k + 1) % 3] for one, k, _ in wedges]).reshape(-1, 3)
        runs -= starts
        past = np.array([self.normals[other] for _, _, other in wedges]).reshape(-1, 3)
        least: dict[str, Any] | None = None
        for point, normal in read.values():
            wall = self._square(point, normal)
            if not under(wall, self.nozzle):
                continue
            out = point - wall * normal
            share = np.clip(((point - starts) * runs).sum(axis=1) / (runs * runs).sum(axis=1), 0, 1)
            away = np.linalg.norm(point - starts - share[:, None] * runs, axis=1)
            on_past = np.abs(((out - starts) * past).sum(axis=1)) <= FINE[0]
            if bool((on_past & (away <= self.nozzle + FINE[0])).any()):
                continue  # The edge where the hole breaks out, not a wall.
            if least is None or wall < least["wall_mm"]:
                least = {"wall_mm": wall, "at": [round(float(v), 3) for v in point]}
        return least

    def _down(self, rim: Rim) -> list[np.ndarray]:
        """Where rays straight down a hole's opening start, on a grid, just over its face."""
        normal = rim.normal
        first = np.cross(normal, np.eye(3)[int(np.argmin(np.abs(normal)))])
        first /= np.linalg.norm(first)
        second = np.cross(normal, first)
        origin = rim.corners[0]
        opening = shapely.Polygon((rim.corners - origin) @ np.c_[first, second]).buffer(0)
        x0, y0, x1, y1 = opening.bounds
        step = max(self.nozzle, math.sqrt((x1 - x0) * (y1 - y0) / RAYS))
        # Off the half steps, so a ray rarely runs along an edge or a wall.
        x, y = np.meshgrid(np.arange(x0 + 0.37 * step, x1, step),
                           np.arange(y0 + 0.43 * step, y1, step))  # fmt: skip
        inside = shapely.contains_xy(opening, x.ravel(), y.ravel())
        flat = np.c_[x.ravel(), y.ravel()][inside]
        return list(origin + INTO * normal + flat[:, :1] * first + flat[:, 1:] * second)


def _to_loop(points: np.ndarray, loop: np.ndarray) -> np.ndarray:
    """How far each point is from a closed loop of straight runs."""
    runs = np.roll(loop, -1, axis=0) - loop
    off = points[:, None, :] - loop[None, :, :]
    share = np.clip((off * runs).sum(axis=2) / (runs * runs).sum(axis=1), 0, 1)
    return np.asarray(np.linalg.norm(off - share[:, :, None] * runs, axis=2).min(axis=1))
