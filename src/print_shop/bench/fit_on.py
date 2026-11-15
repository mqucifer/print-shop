"""`fit-on`: a part's way on, and what it has to pass through (mqucifer/print-shop#142).

A design run approved a snap-on lid that could never be fitted. `clearance --slide` read it held
once seated, and nothing read the way on, where each clip had to bend several times what the
material takes. `--slide` says what's on the path and how far in it reaches from the part's
outline, which isn't how far anything bends. This reads the bend: the part is placed at each
step along its way, from `--from` mm out to where it sits, and at each step it's intersected with
everything it goes onto.

**Each piece of overlap** has its volume, its box, and its size seen along the way on: `depth_mm`,
its least width across, with the direction that's along, and `along_mm`, its length the other
way. Across a wall the part passes, the depth is how far the wall, or the arm on it, has to move
aside for the part to pass: the bend. The bench doesn't know which way anything bends, so it
gives the direction, and a piece narrower along its wall than it is deep reads the narrower way.
Pieces that lie over each other from step to step are one place, said once, at the step where
it's deepest, with the stretch of the way it lasts. A place's deepest step is then looked for
between the steps, down to `REFINE`.

**The verdict** is `CLEAR` when nothing overlaps at any step, and `BLOCKED` otherwise, naming the
deepest place and the step. A snap fit is meant to be `BLOCKED`: its depth is the `--push` that
`bend` takes, which reads whether the arm survives it.

**Second method.** Rays along the way on, through a mesh of each part, with no boolean. The
moving part only moves along the rays, so one set of rays reads every step: each ray's stretches
inside the moving part slide along it by the step, and what they share with its stretches inside
the other part is that step's overlap. Rays `COARSE` apart over the whole outline look for an
overlap the booleans didn't find, at every step, and rays `FINE` apart over each place read its
depth again, from the outline of the rays that pass through both parts. A depth past `DEPTH`
apart, a place the rays don't find, or an overlap only the rays find is `DISAGREE`.

**What it can't read:** friction, how hard the part has to be pushed, whether what's in the way
can bend at all (that's `bend`), a way on that isn't one straight line, and anything thinner
along the way than a step that passes something as thin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .cae import freecad
from .report import NoAnswer, Report

AXES = ("x", "y", "z")
#: An overlap under this, in mm³, isn't a piece: two faces that touch share none.
VOLUME = 1e-6
#: The chord the rays' meshes keep to, in mm.
CHORD = 0.005
#: The rays over the whole outline are this far apart, in mm, or further on a large part, to
#: keep to `CELLS` rays. They find an overlap two rays or more across, both ways.
COARSE = 0.1
CELLS = 400_000
#: The rays over one place, in mm apart, or further over a large place, to keep to `PLACE` rays.
FINE = 0.02
PLACE = 250_000
#: A ray inside both parts for less than this, in mm, isn't in an overlap: the chords of two
#: curved faces that touch cross by up to twice `CHORD`.
LENGTH = 0.012
#: How far the two depths may differ, in mm, past twice the place's rays' spacing: the chords.
DEPTH = 0.01
#: A place's deepest step is looked for between the steps, down to this, in mm.
REFINE = 0.02
#: The places a reply says whole. The saved numbers have every one.
SHOWN = 6
#: Offsets, as shares of the spacing, that keep rays off the parts' edges: irrational, and
#: different each way, so a ray lies on no edge at a round figure and on no diagonal.
OFF = (0.3819660112501051, 0.2928932188134524)


def hull(points: np.ndarray) -> np.ndarray:
    """The convex outline of points in a plane, anticlockwise (Andrew's monotone chain)."""
    unique = np.unique(np.round(points, 9), axis=0)
    if len(unique) < 3:
        return unique
    order = unique[np.lexsort((unique[:, 1], unique[:, 0]))]

    def half(run: np.ndarray) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for point in run:
            while len(out) >= 2:
                a, b = out[-2], out[-1]
                if (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) > 1e-12:
                    break
                out.pop()
            out.append(point)
        return out

    lower, upper = half(order), half(order[::-1])
    return np.array(lower[:-1] + upper[:-1])


def least_width(points: np.ndarray) -> tuple[float, np.ndarray, float]:
    """The least width of points in a plane, the unit direction it's along, and their length the
    other way: the narrowest pair of parallel lines the points fit between, one of them on a
    side of their convex outline."""
    outline = hull(points)
    if len(outline) < 3:
        length = float(np.linalg.norm(outline[-1] - outline[0])) if len(outline) == 2 else 0.0
        return 0.0, np.array([1.0, 0.0]), length
    best: tuple[float, np.ndarray, float] | None = None
    for index in range(len(outline)):
        side = outline[(index + 1) % len(outline)] - outline[index]
        length = float(np.linalg.norm(side))
        if length < 1e-9:
            continue
        side = side / length
        square = np.array([-side[1], side[0]])
        off = (outline - outline[index]) @ square
        run = (outline - outline[index]) @ side
        width = float(off.max() - off.min())
        if best is None or width < best[0] - 1e-9:
            best = (width, square, float(run.max() - run.min()))
    assert best is not None
    width, square, along = best
    # One way round, so a direction reads alike from step to step.
    lead = square[np.argmax(np.abs(square) > 1e-9)]
    return width, (square if lead > 0 else -square), along


@dataclass(frozen=True)
class Grid:
    """Rays along one axis, on a square grid over the other two."""

    axis: int
    low: tuple[float, float]
    spacing: float
    count: tuple[int, int]

    @property
    def across(self) -> tuple[int, int]:
        first, second = (a for a in range(3) if a != self.axis)
        return first, second

    @property
    def cells(self) -> int:
        return self.count[0] * self.count[1]

    def centres(self, cells: np.ndarray) -> np.ndarray:
        """Where each of these rays is, in the grid's two axes."""
        first, second = np.divmod(cells, self.count[1])
        return np.column_stack(
            [self.low[0] + first * self.spacing, self.low[1] + second * self.spacing]
        )


def grid_over(axis: int, low: Any, high: Any, spacing: float, most: int) -> Grid:
    """A grid over a rectangle in the two axes across `axis`, its rays `spacing` apart or
    further, to keep to `most` rays, and off every round figure."""
    size = [max(0.0, b - a) for a, b in zip(low, high, strict=True)]
    spacing = max(spacing, math.sqrt(size[0] * size[1] / most))
    start = (low[0] + OFF[0] * spacing, low[1] + OFF[1] * spacing)
    count = (max(1, math.ceil(size[0] / spacing)), max(1, math.ceil(size[1] / spacing)))
    return Grid(axis, start, spacing, count)


def crossings(points: np.ndarray, facets: np.ndarray, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Where each ray of the grid crosses a mesh: the ray's cell and how far along the axis, for
    every crossing. Each triangle is tried against the rays in its own rectangle."""
    first, second = grid.across
    corners = points[facets]
    u, v, w = corners[:, :, first], corners[:, :, second], corners[:, :, grid.axis]
    span = (v[:, 1] - v[:, 2]) * (u[:, 0] - u[:, 2]) + (u[:, 2] - u[:, 1]) * (v[:, 0] - v[:, 2])
    lows = np.ceil(
        np.column_stack([u.min(axis=1) - grid.low[0], v.min(axis=1) - grid.low[1]]) / grid.spacing
    )
    highs = np.floor(
        np.column_stack([u.max(axis=1) - grid.low[0], v.max(axis=1) - grid.low[1]]) / grid.spacing
    )
    lows = np.maximum(lows, 0).astype(int)
    highs = np.minimum(highs, [grid.count[0] - 1, grid.count[1] - 1]).astype(int)
    met = (np.abs(span) > 1e-12) & (lows[:, 0] <= highs[:, 0]) & (lows[:, 1] <= highs[:, 1])
    cells: list[np.ndarray] = []
    depths: list[np.ndarray] = []
    for index in np.flatnonzero(met):
        i = np.arange(lows[index, 0], highs[index, 0] + 1)
        j = np.arange(lows[index, 1], highs[index, 1] + 1)
        gu = (grid.low[0] + i * grid.spacing)[:, None]
        gv = (grid.low[1] + j * grid.spacing)[None, :]
        uu, vv, ww = u[index], v[index], w[index]
        a = ((vv[1] - vv[2]) * (gu - uu[2]) + (uu[2] - uu[1]) * (gv - vv[2])) / span[index]
        b = ((vv[2] - vv[0]) * (gu - uu[2]) + (uu[0] - uu[2]) * (gv - vv[2])) / span[index]
        c = 1.0 - a - b
        inside = (a >= 0) & (b >= 0) & (c >= 0)
        if not inside.any():
            continue
        ii, jj = np.nonzero(inside)
        cells.append(i[ii] * grid.count[1] + j[jj])
        depths.append((a * ww[0] + b * ww[1] + c * ww[2])[inside])
    if not cells:
        return np.zeros(0, dtype=int), np.zeros(0)
    return np.concatenate(cells), np.concatenate(depths)


def spans(cells: np.ndarray, depths: np.ndarray, count: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Each ray's stretches inside a closed mesh, from its crossings in order: in at the first,
    out at the second. Rows are rays, padded with NaN. A ray with an odd number of crossings ran
    along an edge: it's dropped, and counted."""
    if not len(cells):
        return np.full((count, 1), np.nan), np.full((count, 1), np.nan), 0
    order = np.lexsort((depths, cells))
    cells, depths = cells[order], depths[order]
    each = np.bincount(cells, minlength=count)
    rank = np.arange(len(cells)) - (np.cumsum(each) - each)[cells]
    most = max(1, int(each.max()) // 2)
    low = np.full((count, most), np.nan)
    high = np.full((count, most), np.nan)
    odd = each % 2 == 1
    keep = ~odd[cells]
    enters = keep & (rank % 2 == 0)
    leaves = keep & (rank % 2 == 1)
    low[cells[enters], rank[enters] // 2] = depths[enters]
    high[cells[leaves], rank[leaves] // 2] = depths[leaves]
    return low, high, int(odd.sum())


def shared(
    moving: tuple[np.ndarray, np.ndarray], other: tuple[np.ndarray, np.ndarray], shift: float
) -> np.ndarray:
    """For each ray, how far it runs inside both meshes with the moving one `shift` along it:
    the sum of every stretch the two share that's longer than `LENGTH`."""
    low = np.maximum(moving[0][:, :, None] + shift, other[0][:, None, :])
    high = np.minimum(moving[1][:, :, None] + shift, other[1][:, None, :])
    each = np.nan_to_num(high - low, nan=0.0)
    each[each < LENGTH] = 0.0
    return np.asarray(each.sum(axis=(1, 2)))


def thick(flagged: np.ndarray, grid: Grid) -> np.ndarray:
    """The rays, of those flagged, in a block of rays two by two all flagged: an overlap that's
    more than a sliver between two faces that touch."""
    cells = flagged.reshape(grid.count)
    block = cells[:-1, :-1] & cells[1:, :-1] & cells[:-1, 1:] & cells[1:, 1:]
    out = np.zeros_like(cells)
    for di in (0, 1):
        for dj in (0, 1):
            out[di : di + block.shape[0], dj : dj + block.shape[1]] |= block
    return np.asarray(out.reshape(-1))


def offsets(start: float, step: float) -> list[float]:
    """How far out the part is at each reading: from `start` in steps down to 0, where it sits."""
    whole = int(math.floor(start / step + 1e-9))
    out = [round(i * step, 9) for i in range(whole, -1, -1)]
    if start - out[0] > 1e-9:
        out.insert(0, start)
    return out


def _xyz(point: Any) -> str:
    return "(" + ", ".join(f"{c:g}" for c in np.round(np.asarray(point, dtype=float), 3)) + ")"


def _way(vector: Any) -> str:
    """A direction as a reply says it: an axis where it is one, else its three figures."""
    vector = np.asarray(vector, dtype=float)
    for axis, name in enumerate(AXES):
        if abs(abs(vector[axis]) - 1) < 1e-6:
            return name
    return _xyz(vector)


def _mesh(shape: Any) -> tuple[np.ndarray, np.ndarray]:
    points, facets = shape.tessellate(CHORD)
    return np.array([[p.x, p.y, p.z] for p in points]), np.array(facets, dtype=int)


def _box(shape: Any) -> tuple[np.ndarray, np.ndarray]:
    box = shape.BoundBox
    return np.array([box.XMin, box.YMin, box.ZMin]), np.array([box.XMax, box.YMax, box.ZMax])


class Way:
    """The moving part, what it goes onto, and its way on."""

    def __init__(self, args: Any) -> None:
        FreeCAD, Part = freecad("fit-on")
        for name in [args.moving, *args.onto]:
            if not name.lower().endswith((".step", ".stp")):
                raise NoAnswer(f"fit-on reads STEP files, and {name} isn't one")
        if args.start <= 0 or args.step <= 0:
            raise NoAnswer("--from and --step are distances, more than 0")
        if args.start / args.step > 2000:
            raise NoAnswer(
                f"--from {args.start:g} in steps of {args.step:g} is over 2000 readings: give a "
                "larger --step"
            )
        self.vector = FreeCAD.Vector
        self.moving = Part.read(args.moving)
        self.onto = [Part.read(name) for name in args.onto]
        self.names = list(args.onto)
        self.name = args.moving
        for name, shape in zip([self.name, *self.names], [self.moving, *self.onto], strict=True):
            if not shape.Solids:
                raise NoAnswer(f"{name} has no solid in it")
        self.axis = AXES.index(args.along[1])
        self.sign = 1 if args.along[0] == "+" else -1
        self.across = tuple(a for a in range(3) if a != self.axis)
        self.offsets = offsets(args.start, args.step)

    def pieces(self, out: float) -> list[dict[str, Any]]:
        """Each piece of overlap with the moving part `out` mm along its way out, by OCCT's
        `common` of the solids."""
        move = [0.0, 0.0, 0.0]
        move[self.axis] = self.sign * out
        moved = self.moving.copy()
        moved.translate(self.vector(*move))
        found = []
        for name, other in zip(self.names, self.onto, strict=True):
            if not moved.BoundBox.intersect(other.BoundBox):
                continue
            for solid in other.common(moved).Solids:
                if solid.Volume < VOLUME:
                    continue
                points = np.array([[p.x, p.y, p.z] for p in solid.tessellate(0.01)[0]])
                depth, square, along = least_width(points[:, self.across])
                way = np.zeros(3)
                way[list(self.across)] = square
                low, high = _box(solid)
                found.append(
                    {
                        "file": name,
                        "out_mm": out,
                        "volume_mm3": float(solid.Volume),
                        "box": [low.tolist(), high.tolist()],
                        "depth_mm": depth,
                        "across": [round(float(c), 6) for c in way],
                        "along_mm": along,
                        "height_mm": float(high[self.axis] - low[self.axis]),
                    }
                )
        return found


def _over(one: dict[str, Any], other: dict[str, Any], across: tuple[int, ...]) -> bool:
    """Whether two pieces lie over each other seen along the way on: the same place."""
    return one["file"] == other["file"] and all(
        one["box"][0][a] <= other["box"][1][a] + 1e-6
        and other["box"][0][a] <= one["box"][1][a] + 1e-6
        for a in across
    )


def _places(pieces: list[dict[str, Any]], across: tuple[int, ...]) -> list[list[dict[str, Any]]]:
    """The pieces of every step, gathered into places: those that lie over each other."""
    group = list(range(len(pieces)))

    def find(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for i, one in enumerate(pieces):
        for j in range(i):
            if _over(one, pieces[j], across):
                group[find(i)] = find(j)
    gathered: dict[int, list[dict[str, Any]]] = {}
    for i, one in enumerate(pieces):
        gathered.setdefault(find(i), []).append(one)
    return list(gathered.values())


def _deepest(way: Way, place: list[dict[str, Any]], step: float) -> dict[str, Any]:
    """A place's deepest piece, looked for between the steps too, halving down to `REFINE`."""
    best = max(place, key=lambda p: (p["depth_mm"], p["volume_mm3"]))
    half = step / 2
    while half >= REFINE:
        for out in (best["out_mm"] - half, best["out_mm"] + half):
            if out < 0 or out > way.offsets[0]:
                continue
            for piece in way.pieces(out):
                if _over(piece, best, way.across) and piece["depth_mm"] > best["depth_mm"] + 1e-9:
                    best = piece
        half /= 2
    return best


class Rays:
    """The second method: rays along the way on, through a mesh of each part."""

    def __init__(self, way: Way) -> None:
        self.way = way
        self.moving = _mesh(way.moving)
        self.onto = [_mesh(shape) for shape in way.onto]
        self.dropped = 0
        low, high = _box(way.moving)
        lows, highs = zip(*(_box(shape) for shape in way.onto), strict=True)
        across = list(way.across)
        self.low = np.maximum(low, np.min(lows, axis=0))[across]
        self.high = np.minimum(high, np.max(highs, axis=0))[across]
        self.whole: Grid | None = None
        if (self.high > self.low).all():
            self.whole = grid_over(way.axis, self.low, self.high, COARSE, CELLS)

    def _spans(
        self, mesh: tuple[np.ndarray, np.ndarray], grid: Grid
    ) -> tuple[np.ndarray, np.ndarray]:
        first, second = grid.across
        points, facets = mesh
        corners = points[facets]
        end = (
            grid.low[0] + grid.count[0] * grid.spacing,
            grid.low[1] + grid.count[1] * grid.spacing,
        )
        near = (
            (corners[:, :, first].max(axis=1) >= grid.low[0])
            & (corners[:, :, first].min(axis=1) <= end[0])
            & (corners[:, :, second].max(axis=1) >= grid.low[1])
            & (corners[:, :, second].min(axis=1) <= end[1])
        )
        low, high, odd = spans(*crossings(points, facets[near], grid), grid.cells)
        self.dropped += odd
        return low, high

    def every_step(self) -> list[dict[str, Any]]:
        """At each step, for each file, the rays over the whole outline that run inside both
        parts, where they're two rays or more across."""
        if self.whole is None:
            return []
        grid = self.whole
        moving = self._spans(self.moving, grid)
        found = []
        for name, mesh in zip(self.way.names, self.onto, strict=True):
            other = self._spans(mesh, grid)
            both = np.flatnonzero(~np.isnan(moving[0][:, 0]) & ~np.isnan(other[0][:, 0]))
            if not len(both):
                continue
            mine = (moving[0][both], moving[1][both])
            theirs = (other[0][both], other[1][both])
            for out in self.way.offsets:
                flagged = np.zeros(grid.cells, dtype=bool)
                flagged[both] = shared(mine, theirs, self.way.sign * out) > 0
                wide = thick(flagged, grid)
                if wide.any():
                    found.append(
                        {"file": name, "out_mm": out, "at": grid.centres(np.flatnonzero(wide))}
                    )
        return found

    def depth(self, piece: dict[str, Any]) -> tuple[float | None, float]:
        """A place's depth again: rays `FINE` apart over its box, and the least width of the
        outline of those that run inside both parts, with a ray's spacing for the rays' own
        width. None where no ray runs inside both."""
        across = list(self.way.across)
        low = np.array(piece["box"][0])[across] - 10 * FINE
        high = np.array(piece["box"][1])[across] + 10 * FINE
        grid = grid_over(self.way.axis, low, high, FINE, PLACE)
        moving = self._spans(self.moving, grid)
        other = self._spans(self.onto[self.way.names.index(piece["file"])], grid)
        both = np.flatnonzero(~np.isnan(moving[0][:, 0]) & ~np.isnan(other[0][:, 0]))
        if not len(both):
            return None, grid.spacing
        inside = shared(
            (moving[0][both], moving[1][both]),
            (other[0][both], other[1][both]),
            self.way.sign * piece["out_mm"],
        )
        cells = both[inside > 0]
        if not len(cells):
            return None, grid.spacing
        return least_width(grid.centres(cells))[0] + grid.spacing, grid.spacing


def run(args: Any, report: Report) -> None:
    way = Way(args)
    every = [piece for out in way.offsets for piece in way.pieces(out)]
    places = []
    for group in _places(every, way.across):
        outs = sorted({p["out_mm"] for p in group})
        best = dict(_deepest(way, group, args.step))
        best.update(first_mm=outs[-1], last_mm=outs[0], readings=len(outs), seated=outs[0] == 0)
        places.append(best)
    places.sort(key=lambda p: (-p["depth_mm"], -p["volume_mm3"]))
    rays = Rays(way)
    apart = []
    for place in places:
        again, spacing = rays.depth(place)
        place["depth_rays_mm"] = again
        place["rays_mm"] = spacing
        if again is None:
            apart.append(
                f"OCCT's common finds an overlap with {place['file']} at "
                f"{_xyz(place['box'][0])} to {_xyz(place['box'][1])}, {place['out_mm']:g} mm "
                f"out, and no ray {spacing:.3g} mm apart runs inside both parts there"
            )
        elif abs(again - place["depth_mm"]) > 2 * spacing + DEPTH:
            apart.append(
                f"the overlap with {place['file']} at {_xyz(place['box'][0])} to "
                f"{_xyz(place['box'][1])}, {place['out_mm']:g} mm out, is "
                f"{place['depth_mm']:.3f} mm deep by OCCT's common and {again:.3f} by rays "
                f"{spacing:.3g} mm apart"
            )
    strays = []
    for met in rays.every_step():
        boxes = [
            p["box"] for p in every if p["file"] == met["file"] and p["out_mm"] == met["out_mm"]
        ]
        pad = rays.whole.spacing if rays.whole else 0.0
        lone = np.ones(len(met["at"]), dtype=bool)
        for low, high in boxes:
            inside = np.ones(len(met["at"]), dtype=bool)
            for column, axis in enumerate(way.across):
                inside &= (met["at"][:, column] >= low[axis] - pad) & (
                    met["at"][:, column] <= high[axis] + pad
                )
            lone &= ~inside
        if lone.any():
            where = met["at"][lone]
            strays.append(
                {
                    "file": met["file"],
                    "out_mm": met["out_mm"],
                    "rays": int(lone.sum()),
                    "from": where.min(axis=0).tolist(),
                    "to": where.max(axis=0).tolist(),
                }
            )
    if strays:
        first = strays[0]
        names = "".join(AXES[a] for a in way.across)
        apart.append(
            f"{first['rays']} rays run inside both {way.name} and {first['file']} with it "
            f"{first['out_mm']:g} mm out, from {names} {_xyz(first['from'])} to "
            f"{_xyz(first['to'])}, where OCCT's common finds no overlap"
            + (f"; and at {len(strays) - 1} more readings" if len(strays) > 1 else "")
        )
    _say(args, report, way, places, rays, apart, strays, every)


def _say(
    args: Any,
    report: Report,
    way: Way,
    places: list[dict[str, Any]],
    rays: Rays,
    apart: list[str],
    strays: list[dict[str, Any]],
    every: list[dict[str, Any]],
) -> None:
    """The reply: each place, deepest first, the readings, the second check, and the verdict."""
    outs = way.offsets
    steps = []
    for out in outs:
        here = [p for p in every if p["out_mm"] == out]
        steps.append(
            {
                "out_mm": out,
                "pieces": len(here),
                "overlap_mm3": sum(p["volume_mm3"] for p in here),
                "depth_mm": max((p["depth_mm"] for p in here), default=0.0),
            }
        )
    spacing = rays.whole.spacing if rays.whole else None
    report.numbers["moving"] = way.name
    report.numbers["onto"] = way.names
    report.numbers["along"] = args.along
    report.numbers["from_mm"] = outs[0]
    report.numbers["step_mm"] = args.step
    report.numbers["readings"] = len(outs)
    report.numbers["places"] = places
    report.numbers["depth_mm"] = places[0]["depth_mm"] if places else 0.0
    report.numbers["steps"] = steps
    report.numbers["rays"] = {
        "over_all_mm": spacing,
        "count": rays.whole.cells if rays.whole else 0,
        "dropped": rays.dropped,
        "strays": strays,
    }
    read = (
        f"from {outs[0]:g} mm out along {args.along} to where it sits, read every {args.step:g} mm"
    )
    for number, place in enumerate(places[:SHOWN]):
        lasts = (
            f"at {place['first_mm']:g} mm out"
            if place["readings"] == 1
            else f"from {place['first_mm']:g} mm out to {place['last_mm']:g}"
        )
        seated = (
            ", where it sits"
            if place["seated"] and place["readings"] == 1
            else (", and still where it sits" if place["seated"] else "")
        )
        report.line(
            f"places.{number}: {way.name} passes through {place['file']} {lasts}{seated}; "
            f"deepest {place['out_mm']:g} mm out, {place['depth_mm']:.3f} mm deep across "
            f"{_way(place['across'])} and {place['along_mm']:.3f} mm along, "
            f"{place['height_mm']:.3f} mm of the way and {place['volume_mm3']:.3f} mm³, at "
            f"{_xyz(place['box'][0])} to {_xyz(place['box'][1])}"
        )
    if len(places) > SHOWN:
        report.line(f"and {len(places) - SHOWN} more places, each shallower, in places")
    if places:
        said = "; ".join(
            f"places.{n} {p['depth_mm']:.3f} mm by OCCT's common and "
            + (f"{p['depth_rays_mm']:.3f}" if p["depth_rays_mm"] is not None else "none")
            + f" by rays {p['rays_mm']:.3g} mm apart"
            for n, p in enumerate(places[:SHOWN])
        )
        report.second(
            f"each place's depth again, by rays along the way on through both meshes: {said}"
        )
    if spacing is None:
        report.second(
            f"seen along {args.along}, {way.name}'s box and the others' don't cross, so no ray "
            "passes through both"
        )
    else:
        found = "none" if not strays else f"{len(strays)} readings with one"
        report.second(
            f"{rays.whole.cells if rays.whole else 0} rays {spacing:.3g} mm apart over the whole "
            f"outline, at each of the {len(outs)} readings, looking for an overlap two rays or "
            f"more across that OCCT's common didn't find: {found}"
            + (f" ({rays.dropped} rays ran along an edge and weren't read)" if rays.dropped else "")
        )
    if apart:
        report.verdict("DISAGREE", "; ".join(apart)[:900])
        return
    if not places:
        report.verdict("CLEAR", f"nothing is in {way.name}'s way {read}")
        return
    passing = [p for p in places if not p["seated"]]
    sits = [p for p in places if p["seated"]]
    why = []
    if passing:
        deepest = passing[0]
        why.append(
            f"{way.name} passes through {deepest['file']} on its way on, {read}, at "
            f"{len(passing)} place(s): the deepest is {deepest['depth_mm']:.3f} mm deep across "
            f"{_way(deepest['across'])}, as places.{places.index(deepest)}.depth_mm, and "
            f"{deepest['along_mm']:.3f} mm along, at {_xyz(deepest['box'][0])} to "
            f"{_xyz(deepest['box'][1])}, with it {deepest['out_mm']:g} mm out. What's there has "
            "to move aside that far for it to pass"
        )
        report.line(
            "What bends there: read whether it takes it with `bend FILE --arm BOX --root PLANE "
            f"--push {deepest['depth_mm']:.3g} --toward DIR`, the box round the arm that has to "
            "move, DIR the way across that it moves"
        )
    if sits:
        first = sits[0]
        why.append(
            f"{way.name} overlaps {first['file']} where it sits, at {_xyz(first['box'][0])} to "
            f"{_xyz(first['box'][1])} with it {first['out_mm']:g} mm out"
            + (f", and at {len(sits) - 1} more place(s)" if len(sits) > 1 else "")
            + ", as places."
            + f"{places.index(first)}: no bend clears an overlap where a part sits, and "
            "`clearance` reads it"
        )
    report.verdict("BLOCKED", "; and ".join(why))
