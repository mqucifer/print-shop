"""A dome's slices joined edge to edge: `pieces build --seam`, which builds a slice's two long
edges as seams, and `pieces seam`, which places two built neighbours round the dome and reads
how they meet, with the came strips and the rings that hold them (mqucifer/print-shop#166).

**The seam** (the Sponsor, 2026-11-18: "I'm not a fan of glued seems for 3d prints. It needs
some mechanical hold"). Two neighbouring slices stand at an angle to each other, a different
one at each of a slice's flat rows. So each long edge is a row of straight lengths, one a row,
and each is bevelled by half the angle between the two slices there: the two faces are then
parallel to the plane through the dome's axis and the cut, and meet flat. A came strip, an H in
section, clips over both edges (`pieces came`), and its web lies between the two faces. So each
face stands back from the cut's middle by half the seam's gap, the web and its play.

**Where the faces are.** A slice's rows have their corners on the dome (`pieces flatten`), and
the frame's inner face is taken to lie on them. A length's face leans out toward the outer face
by its bevel, so the frame is widest at its outer face, and a bevel takes no lead from beside a
pocket: only the gap does. With a seam the frame's side is set by the cut, whatever `--border`
is; the border is the frame at the top and the hem.

**What's refused.** A seam is inside the lead line, half of which is this slice's: `flatten`
leaves the glass half a lead from the cut's middle. A face that leaves less than `WALL` of
lead beside a pocket, or stands back past a pane's edge at the inner face, is named, and
nothing is built.

**`pieces seam`** reads two built frames. Each is cut at its folds into its flat rows, and each
row is set where the slice's map puts it: bent by its folds from the hem up, and turned round
the dome. Then, at each length: the two faces that meet, the angle between them and how far
apart they stand, against the gap they were built for; and what the two rows share, by
OpenCascade's `common`. With `--came`, each strip as `pieces came` placed it: whether it's
round both edges, how far it presses into a face, and whether a flange lies over a pane's
opening. With `--rings`, whether each slice's hem and top edge is in a ring's groove.
**Second method:** points, with no boolean. Each face's own corners, moved a little into their
own row, are asked whether they're inside the other row too, and the nearest the two rows come
is read by OpenCascade's distance between shapes.

**What it can't read:** how a heated lead line bends. Each row is placed as a rigid flat piece,
so a real slice's seam is as true as its folds are bent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import piecemap
from .limits import over, under
from .piecemap import Map
from .report import NoAnswer, Report

#: The least lead a seam leaves beside a pocket, in mm: a nozzle's width.
WALL = 0.4
#: How far apart two slices' faces stand, in mm, unless given: a came's web of two layers and
#: its play on each side.
GAP = 0.6
#: How far the gap two faces stand apart by may be off the one they were built for, in mm.
WITHIN = 0.05
#: Two rows share nothing where their overlap is under this, in mm³.
SHARED = 1e-3
#: A face is one that meets the neighbour where it's within this of facing it, in degrees.
FACING_DEG = 35.0
#: Two faces that meet are this near parallel, in degrees.
PARALLEL_DEG = 0.1
#: A point is asked whether it's inside the other row from this far inside its own, in mm.
NUDGE = 0.02
#: A strip or a ring is round an edge where it's this far past each of the edge's sides, mm.
PROBE = 0.3
#: The most a strip may press into a slice's face, in mm, unless given.
PRESS = 0.1
#: A flange's end is read against the openings at places this far apart along it, in mm.
ALONG = 1.0
#: A join by the corners and by `flatten`'s own figure may differ by this, in degrees.
JOIN_OFF_DEG = 0.01


@dataclass
class Length:
    """One straight length of a slice's long edge: a flat row's side, on the cut's middle."""

    row: int
    side: int
    a: np.ndarray
    b: np.ndarray
    join_deg: float
    bevel_deg: float
    low: float | None
    high: float | None
    half_mm: float = math.inf
    wall_mm: float = math.inf
    by_corners_deg: float = 0.0
    by_dome_deg: float = 0.0

    @property
    def way(self) -> np.ndarray:
        v = self.b - self.a
        return np.asarray(v / np.hypot(*v))

    @property
    def length(self) -> float:
        return float(np.hypot(*(self.b - self.a)))

    @property
    def out(self) -> np.ndarray:
        """Out of the slice, in the map's plane, square to the length."""
        e = self.way
        return self.side * np.array([e[1], -e[0]])

    @property
    def lean(self) -> float:
        return math.tan(math.radians(self.bevel_deg))

    def x_at(self, y: float, z: float, back: float) -> float:
        """The face's x at a height on the map and a height in the frame: it stands `back`
        from the plane through the cut, square to that plane."""
        o = self.out
        off = z * self.lean - back / math.cos(math.radians(self.bevel_deg))
        return float(self.a[0] + (off - (y - self.a[1]) * o[1]) / o[0])

    def normal(self) -> np.ndarray:
        """Out of the face, in the flat frame."""
        o = self.out
        a = math.radians(self.bevel_deg)
        return np.array([math.cos(a) * o[0], math.cos(a) * o[1], -math.sin(a)])


@dataclass
class Edge:
    name: str
    side: int
    slice: int
    lengths: list[Length]
    #: From the panel's edge to the cut's middle, on the dome.
    half_line: float = 0.0


@dataclass
class Seam:
    """A slice's two edges as seams, and what building them takes."""

    edges: list[Edge]
    thickness: float
    gap: float
    refused: list[dict[str, Any]] = field(default_factory=list)
    lead_needed: float | None = None

    @property
    def back(self) -> float:
        return self.gap / 2.0


def slice_surface(found: Map, name: str) -> dict[str, Any]:
    surface = found.surface
    if surface.get("kind") != "dome-slice" or "edges" not in surface:
        raise NoAnswer(
            f"{name} isn't a dome's slice, so it has no edge shared with a neighbour: `pieces "
            "flatten` writes each slice's map, with its edges and the angle at each"
        )
    return surface


def _rotated(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]])


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    cos = float(a @ b) / float(np.linalg.norm(a) * np.linalg.norm(b))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def joins(surface: dict[str, Any], side: int) -> list[tuple[float, float]]:
    """The angle between this slice's rows and a neighbour's, two ways that aren't `flatten`'s
    own figure. By the corners: each row's normal from three of its corners on the dome,
    against the same turned one slice round the axis. And by the dome's own surface: its normal
    at the two slices' middles, at the row's middle height, which is the curve's angle and not
    the flat rows'."""
    left = np.array(surface["edges"]["left"]["on_dome"], dtype=float)
    right = np.array(surface["edges"]["right"]["on_dome"], dtype=float)
    step = side * 2.0 * math.pi / int(surface["slices"])
    turn = math.radians(float(surface["turn_deg"]))
    dome = surface.get("dome", {})
    a, b = float(dome.get("diameter_mm", 0.0)) / 2.0, float(dome.get("height_mm", 0.0))
    found = []
    for j in range(len(left) - 1):
        normal = np.cross(right[j] - left[j], left[j + 1] - left[j])
        by_corners = _angle(normal, _rotated(normal, step))
        by_dome = math.nan
        if a > 0 and b > 0:
            z = float(left[j][2] + left[j + 1][2]) / 2.0
            r = a * math.sqrt(max(0.0, 1.0 - (z / b) ** 2))
            mine = np.array([r * math.cos(turn) / a**2, r * math.sin(turn) / a**2, z / b**2])
            by_dome = _angle(mine, _rotated(mine, step))
        found.append((by_corners, by_dome))
    return found


def edges_of(found: Map, name: str) -> list[Edge]:
    """A slice's two long edges as their straight lengths, from the hem up, each with how far
    the nearest piece is from the cut's middle beside it."""
    surface = slice_surface(found, name)
    edges = []
    for side, word in ((-1, "left"), (1, "right")):
        given = surface["edges"][word]
        flat = np.array(given["flat"], dtype=float)
        join = [float(v) for v in given["join_deg"]]
        if len(flat) != len(join) + 1 or len(join) < 1:
            raise NoAnswer(
                f"{name}'s {word} edge has {len(flat)} point(s) and {len(join)} angle(s)"
            )
        checks = joins(surface, side)
        lengths = []
        for j, angle in enumerate(join):
            length = Length(
                j + 1, side, flat[j], flat[j + 1], angle, angle / 2.0,
                None if j == 0 else float(flat[j][1]),
                None if j == len(join) - 1 else float(flat[j + 1][1]),
                by_corners_deg=checks[j][0], by_dome_deg=checks[j][1],
            )  # fmt: skip
            mine = [
                p
                for p in found.pieces
                if (length.low is None or p.shape.bounds[3] > length.low)
                and (length.high is None or p.shape.bounds[1] < length.high)
            ]
            if mine:
                every = np.vstack([shapely.get_coordinates(p.shape.exterior) for p in mine])
                length.half_mm = float((-(every - length.a) @ length.out).min())
            lengths.append(length)
        half_line = float(given.get("half_line_mm") or found.lead_mm / 2.0)
        edges.append(Edge(word, side, int(given["slice"]), lengths, half_line))
    return edges


def seam_plan(found: Map, name: str, args: Any, z: dict[str, float]) -> Seam:
    """A slice's seams, with every place one would cut a pocket or a lip.

    A piece's pocket never stands past its outline, so the lead beside a seam is read from the
    pieces' outlines: how far the nearest is from the cut's middle, at each length. A face
    stands back from that by half the gap, less its lean at the pocket's floor."""
    thickness = float(args.thickness)
    gap = GAP if getattr(args, "seam_gap", None) is None else float(args.seam_gap)
    if gap < 0:
        raise NoAnswer(
            "--seam-gap is how far apart two slices' faces stand, in mm, 0 or more: a came "
            "strip's web and its play on each side"
        )
    seam = Seam(edges_of(found, name), thickness, gap)
    floor = z["pocket_floor_z"]
    needed = 0.0
    for edge in seam.edges:
        for length in edge.lengths:
            back = seam.back / math.cos(math.radians(length.bevel_deg))
            leans = floor * length.lean
            length.wall_mm = length.half_mm + leans - back
            said = f"the {edge.name} edge's length {length.row}"
            row = {
                "edge": edge.name,
                "length": length.row,
                "at": piecemap.xy((length.a + length.b) / 2.0),
            }
            if under(length.wall_mm, WALL):
                needed = max(needed, 2.0 * (WALL + back - leans))
                seam.refused.append(
                    {
                        **row, "what": "pocket", "wall_mm": length.wall_mm, "least_mm": WALL,
                        "why": f"{said}: its face stands {back:.2f} mm back from the cut's "
                        f"middle at the inner face, for a gap of {gap:g} mm, and leans "
                        f"{leans:.2f} mm out by the pocket's floor at "
                        f"{length.bevel_deg:.2f}°; the nearest pocket is {length.half_mm:.2f} "
                        "mm from the cut's middle, which leaves "
                        + (f"{length.wall_mm:.2f} mm of" if length.wall_mm > 0.005 else "no")
                        + f" lead beside that pocket, where a nozzle prints {WALL:g} mm",
                    }  # fmt: skip
                )
            elif under(length.half_mm - back, 0.0):
                needed = max(needed, 2.0 * back)
                seam.refused.append(
                    {
                        **row, "what": "lip", "wall_mm": length.half_mm - back, "least_mm": 0.0,
                        "why": f"{said}: its face stands {back:.2f} mm back from the cut's "
                        f"middle at the frame's inner face, past the edge of a pane "
                        f"{length.half_mm:.2f} mm from it: it would cut the lip under that pane",
                    }  # fmt: skip
                )
    if seam.refused:
        seam.lead_needed = math.ceil(needed * 10.0 - 1e-9) / 10.0
    return seam


def _run_on(ring: np.ndarray, near: np.ndarray, corner: int, inside: np.ndarray) -> Any:
    """The half of the plane on the panel's side of an edge run on past a corner: the hem, or
    the top, as it arrives at the panel's side, carried on to the cut and past it."""
    count = len(ring)
    for step in (1, -1):
        k = (corner + step) % count
        if near[k]:
            continue
        while float(np.hypot(*(ring[k] - ring[corner]))) < 0.5 and not near[(k + step) % count]:
            k = (k + step) % count
        way = ring[corner] - ring[k]
        way = way / np.hypot(*way)
        square = np.array([-way[1], way[0]])
        if float((inside - ring[corner]) @ square) < 0:
            square = -square
        far = 1000.0
        a, b = ring[corner] - far * way, ring[corner] + far * way
        return shapely.Polygon([a, b, b + far * square, a + far * square])
    raise NoAnswer("the panel's side has no hem or top beside it")


def side_of(found: Map, edge: Edge) -> tuple[np.ndarray, np.ndarray, int, int]:
    """The panel's own side beside a cut: its outline, each point once; which of its points
    lie half a lead from the cut; and its two corners there, the hem's and the top's."""
    ring = shapely.get_coordinates(found.panel.exterior)[:-1]
    # A map may say a corner twice: each point once, so a corner's neighbours are other places.
    ring = ring[np.hypot(*(ring - np.roll(ring, 1, axis=0)).T) > 1e-6]
    near = np.zeros(len(ring), dtype=bool)
    for length in edge.lengths:
        off = -(ring - length.a) @ length.out
        along = (ring - length.a) @ length.way
        near |= (off < 1.3 * edge.half_line + 0.05) & (along > -1.0) & (
            along < length.length + 1.0
        )
    if not near.any():
        raise NoAnswer(f"the panel's outline doesn't reach its {edge.name} cut")
    mine = np.flatnonzero(near)
    low, high = int(mine[ring[mine, 1].argmin()]), int(mine[ring[mine, 1].argmax()])
    return ring, near, low, high


def widened(found: Map, seam: Seam) -> Any:
    """The panel and a strip past each cut, between the panel's hem and its top run on as they
    arrive at its side: the plate the seams are cut from. It's wider than any bevel reaches at
    the outer face, and two neighbours' hems meet at the cut."""
    most = max(length.lean for edge in seam.edges for length in edge.lengths)
    past = seam.thickness * most + 0.5
    inside = shapely.get_coordinates(found.panel.representative_point())[0]
    parts = [found.panel]
    for edge in seam.edges:
        ring, near, low, high = side_of(found, edge)
        between = _run_on(ring, near, low, inside).intersection(
            _run_on(ring, near, high, inside)
        )
        for length in edge.lengths:
            e, o = length.way, length.out
            a, b = length.a - 2.0 * e, length.b + 2.0 * e
            inner = 1.3 * edge.half_line + 0.05
            strip = shapely.Polygon([a - inner * o, b - inner * o, b + past * o, a + past * o])
            parts.append(strip.intersection(between))
    whole = piecemap.polygons(shapely.union_all(parts).buffer(0))
    if len(whole) != 1:
        raise NoAnswer("the panel and its seams' strips aren't one outline")
    return shapely.Polygon(whole[0].exterior)


def _bands(seam: Seam, low: float, high: float) -> list[tuple[float, float, Length, Length]]:
    left, right = seam.edges[0].lengths, seam.edges[1].lengths
    return [
        (
            low if one.low is None else one.low,
            high if one.high is None else one.high,
            one,
            two,
        )
        for one, two in zip(left, right, strict=True)
    ]


def section(seam: Seam, base: Any, z: float) -> Any:
    """The plate between its two seams at one height, in 2D."""
    _x0, y0, _x1, y1 = base.bounds
    quads = []
    for low, high, one, two in _bands(seam, y0 - 5.0, y1 + 5.0):
        quads.append(
            shapely.Polygon(
                [
                    (one.x_at(low, z, seam.back), low),
                    (two.x_at(low, z, seam.back), low),
                    (two.x_at(high, z, seam.back), high),
                    (one.x_at(high, z, seam.back), high),
                ]
            )
        )
    return base.intersection(shapely.union_all(quads))


def volume_by_sections(seam: Seam, base: Any, steps: int = 12) -> float:
    """The bevelled plate's volume from its sections in Shapely, by Simpson's rule."""
    heights = np.linspace(0.0, seam.thickness, steps + 1)
    areas = [section(seam, base, float(h)).area for h in heights]
    weights = [1.0] + [4.0 if k % 2 else 2.0 for k in range(1, steps)] + [1.0]
    return float(np.dot(weights, areas)) * seam.thickness / steps / 3.0


# The solids


def block(corners: np.ndarray) -> Any:
    """Eight corners as a solid: `corners[i][j][k]`, each of i, j and k one of its two ends
    along one of its three ways. Each of its six sides is flat."""
    import cadquery as cq

    p = np.asarray(corners, dtype=float).reshape(2, 2, 2, 3)
    quads = [
        [p[0, 0, 0], p[0, 1, 0], p[0, 1, 1], p[0, 0, 1]],
        [p[1, 0, 0], p[1, 1, 0], p[1, 1, 1], p[1, 0, 1]],
        [p[0, 0, 0], p[1, 0, 0], p[1, 0, 1], p[0, 0, 1]],
        [p[0, 1, 0], p[1, 1, 0], p[1, 1, 1], p[0, 1, 1]],
        [p[0, 0, 0], p[1, 0, 0], p[1, 1, 0], p[0, 1, 0]],
        [p[0, 0, 1], p[1, 0, 1], p[1, 1, 1], p[0, 1, 1]],
    ]
    faces = [
        cq.Face.makeFromWires(
            cq.Wire.makePolygon([cq.Vector(*(float(v) for v in at)) for at in quad], close=True)
        )
        for quad in quads
    ]
    solid = cq.Solid.makeSolid(cq.Shell.makeShell(faces))
    if solid.Volume() < 0:
        solid = cq.Solid(solid.wrapped.Reversed())
    return solid


def with_seams(plate: Any, seam: Seam, base: Any) -> Any:
    """A plate cut to its seams' faces: what's between each row's two faces, a block a row."""
    _x0, y0, _x1, y1 = base.bounds
    heights = (-1.0, seam.thickness + 1.0)
    whole = None
    for low, high, one, two in _bands(seam, y0 - 5.0, y1 + 5.0):
        ends = [
            [[[side.x_at(y, z, seam.back), y, z] for z in heights] for y in (low, high)]
            for side in (one, two)
        ]
        made = block(np.array(ends))
        whole = made if whole is None else whole.fuse(made)
    return plate.intersect(whole)


def faces_read(frame: Any, seam: Seam) -> float:
    """The built frame's seam faces, read back: for each length, the flat face whose normal is
    nearest the bevel's, and how far off it is, in degrees. The worst of them."""
    flats = [
        (np.array(face.normalAt().toTuple()), np.array(face.Center().toTuple()))
        for face in frame.Faces()
        if face.geomType() == "PLANE"
    ]
    worst = 0.0
    for edge in seam.edges:
        for length in edge.lengths:
            m = length.normal()
            middle = np.array([*((length.a + length.b) / 2.0), 0.0]) - seam.back * m
            near = [
                _angle(normal, m)
                for normal, centre in flats
                if abs(float((centre - middle) @ m)) < 0.05
            ]
            worst = max(worst, min(near) if near else 180.0)
    return worst


def say_seams(seam: Seam, report: Report, lead: float) -> None:
    """A build's lines about its seams: each length's bevel."""
    numbers: dict[str, Any] = {}
    for edge in seam.edges:
        numbers[edge.name] = {
            "slice": edge.slice,
            "lengths": [
                {
                    "length": length.row,
                    "length_mm": round(length.length, 4),
                    "join_deg": length.join_deg,
                    "bevel_deg": length.bevel_deg,
                    "lead_beside_mm": round(length.half_mm, 4),
                    "wall_at_pocket_mm": round(length.wall_mm, 4),
                }
                for length in edge.lengths
            ],
        }
        report.line(
            f"seam, {edge.name} edge, shared with slice {edge.slice}, each straight length "
            "from the hem up and its bevel: "
            + "; ".join(
                f"length {length.row}, {length.length:.1f} mm, {length.bevel_deg:.2f}°"
                for length in edge.lengths
            )
        )
    report.numbers["seam"] = numbers
    report.fact(
        "seam_gap_mm", seam.gap, "mm",
        "how far apart two neighbours' faces stand: each is half of it back from the plane "
        "through the dome's axis and the cut, for a came strip's web and its play",
    )  # fmt: skip
    thin = min(length.wall_mm for edge in seam.edges for length in edge.lengths)
    report.fact(
        "seam_wall_mm", thin, "mm",
        f"the least lead a seam leaves beside a pocket, at the pocket's floor: the cut's "
        f"middle is half the map's lead, {lead / 2.0:g} mm, from the glass",
    )  # fmt: skip
    report.line(
        "the frame's inner face lies on the slice's rows, and each seam's face leans out "
        "toward the outer face, the one the map is seen from. --border is the frame at the top "
        "and the hem only. `pieces came` makes the strips that clip over two slices' edges, "
        "`pieces ring` the rings at the top and the hem, and `pieces seam` places two built "
        "neighbours and reads how they meet"
    )


def refuse_seams(seam: Seam, report: Report) -> None:
    """A seam that would cut a pocket or a lip: each place, and what would fit."""
    report.numbers["seam_refused"] = [
        {k: v for k, v in row.items() if k != "why"} for row in seam.refused
    ]
    report.numbers["lead_needed_mm"] = seam.lead_needed
    for row in seam.refused[:12]:
        report.line(row["why"])
    report.second(
        f"each of the {len(seam.refused)} place(s) named is read from the pieces' outlines "
        "against the cut's middle, and from the face's own place at the pocket's floor"
    )
    report.verdict(
        "FAIL",
        f"the seams would cut a pocket or a lip at {len(seam.refused)} place(s), so nothing "
        f"was built: {seam.refused[0]['why']}"
        + (f"; and {len(seam.refused) - 1} more" if len(seam.refused) > 1 else "")
        + f". A lead of about {seam.lead_needed:g} mm in the bands file would leave room, or "
        "a smaller --seam-gap, for a thinner web",
    )


def seam_checks(seam: Seam, report: Report) -> list[str]:
    """The joins read a second way, and what differs."""
    lengths = [length for edge in seam.edges for length in edge.lengths]
    off = max(abs(length.by_corners_deg - length.join_deg) for length in lengths)
    report.second(
        f"each length's join, the angle between this slice's row and its neighbour's, by the "
        f"rows' own corners on the dome: each row's normal against the same turned one slice "
        f"round the axis. It's within {off:.5f}° of `flatten`'s figure at every length"
    )
    by_dome = [length for length in lengths if not math.isnan(length.by_dome_deg)]
    if by_dome:
        furthest = max(by_dome, key=lambda v: abs(v.by_dome_deg - v.join_deg))
        report.note(
            f"the dome's own surface, at the two slices' middles, turns "
            f"{furthest.by_dome_deg:.2f}° at length {furthest.row}'s middle height where the "
            f"flat rows there stand {furthest.join_deg:.2f}° apart: the curve's angle, not "
            "the rows', so it's a different thing measured, and the rows' is what's built"
        )
    if over(off, JOIN_OFF_DEG):
        return [
            f"a join is {off:.4f}° off `flatten`'s figure by the rows' own corners, over "
            f"{JOIN_OFF_DEG:g}°"
        ]
    return []


# Two built slices, placed


def placings(surface: dict[str, Any]) -> list[dict[str, Any]]:
    """Where each flat row of a slice goes: its stretch of the map, and the turn and the move
    that take the flat frame there to its place on the dome. A chain from the hem up, each row
    turned by the bend at its fold, as `flatten` reads a slice back; the frame's inner face
    lies on the row. With each row's own place before it's turned round the dome: how far out
    its lower end is and how high, its tilt and how long it is."""
    hem = surface["hem"]
    folds = sorted(surface["folds"], key=lambda f: f["y_mm"])
    starts = [float(hem["at"][1]), *[float(f["y_mm"]) for f in folds]]
    top = float(surface["edges"]["left"]["flat"][-1][1])
    tilt = math.radians(float(hem["tilt_deg"]))
    out, high = float(hem["out_mm"]), float(hem["z_mm"])
    turn = math.radians(float(surface["turn_deg"]))
    found = []
    for i, start in enumerate(starts):
        across = np.array([0.0, 1.0, 0.0])
        up = np.array([-math.cos(tilt), 0.0, math.sin(tilt)])
        normal = np.cross(across, up)
        turned = np.column_stack([_rotated(v, turn) for v in (across, up, normal)])
        origin = _rotated(np.array([out, 0.0, high]), turn)
        move = origin - turned @ np.array([float(hem["at"][0]), start, 0.0])
        long = (starts[i + 1] if i < len(folds) else top) - start
        found.append(
            {
                "row": i + 1,
                "low": None if i == 0 else start,
                "high": None if i == len(starts) - 1 else starts[i + 1],
                "turn": turned,
                "move": move,
                "out": out,
                "z": high,
                "tilt": tilt,
                "long": long,
                "up": _rotated(up, turn),
                "normal": _rotated(normal, turn),
            }
        )
        if i < len(folds):
            out, high = out - long * math.cos(tilt), high + long * math.sin(tilt)
            tilt -= math.radians(float(folds[i]["bend_deg"]))
    return found


def solids_of(path: str, what: str) -> list[Any]:
    import cadquery as cq

    if not Path(path).is_file():
        raise NoAnswer(f"no file {path}: {what}")
    return [solid for shape in cq.importers.importStep(path).vals() for solid in shape.Solids()]


def _rows_placed(path: str, surface: dict[str, Any]) -> tuple[list[Any], float]:
    """A built frame cut at its folds into flat rows, each set at its place on the dome."""
    import cadquery as cq
    from OCP.gp import gp_Trsf

    found = solids_of(path, "--frames takes each slice's frame, as `pieces build` wrote it")
    if len(found) != 1:
        raise NoAnswer(f"{path} holds {len(found)} solid(s): a slice's frame is one body")
    frame = found[0]
    box = frame.BoundingBox()
    thickness = float(box.zmax - box.zmin)
    if abs(box.zmin) > 1e-3:
        raise NoAnswer(f"the frame's lower face is at z = {box.zmin:g}, not 0, as a build's is")
    placed = []
    for row in placings(surface):
        low = box.ymin - 1.0 if row["low"] is None else row["low"]
        high = box.ymax + 1.0 if row["high"] is None else row["high"]
        band = cq.Solid.makeBox(
            box.xlen + 2.0, high - low, thickness + 2.0, cq.Vector(box.xmin - 1.0, low, -1.0)
        )
        part = frame.intersect(band)
        turn, move = row["turn"], row["move"]
        place = gp_Trsf()
        place.SetValues(*[float(v) for k in range(3) for v in (*turn[k], move[k])])
        placed.append(part.moved(cq.Location(place)))
    return placed, thickness


def _facing(row: Any, toward: np.ndarray) -> dict[str, Any] | None:
    """A row's face toward its neighbour: of its faces within `FACING_DEG` of facing that
    way and near the plane through the dome's axis and the cut, those on one plane with the
    most area."""
    groups: dict[tuple[int, ...], dict[str, Any]] = {}
    for face in row.Faces():
        # A side built with no seam may be a shallow arc: it's read where its middle is.
        centre = np.array(face.Center().toTuple())
        normal = np.array(face.normalAt(face.Center()).toTuple())
        if _angle(normal, toward) > FACING_DEG or abs(float(centre @ toward)) > 3.0:
            continue
        key = (*np.round(normal * 1e4).astype(int), round(float(centre @ normal) * 1e3))
        group = groups.setdefault(
            key, {"normal": normal, "at": centre, "area": 0.0, "corners": []}
        )
        group["area"] += float(face.Area())
        group["corners"] += [np.array(v.toTuple()) for v in face.Vertices()]
    if not groups:
        return None
    return max(groups.values(), key=lambda g: float(g["area"]))


def _apart(one: Any, two: Any) -> float:
    """The nearest two shapes come, by OpenCascade's distance between shapes."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    found = BRepExtrema_DistShapeShape(one.wrapped, two.wrapped)
    if not found.IsDone():
        raise NoAnswer("OpenCascade couldn't read how far apart two shapes are")
    return float(found.Value())


def inside(solids: list[Any], at: np.ndarray) -> bool:
    """Whether a point is inside any of some solids."""
    import cadquery as cq

    point = cq.Vector(*(float(v) for v in at))
    return any(bool(solid.isInside(point)) for solid in solids)


def _deepest(shared: Any, normal: np.ndarray) -> float:
    """How deep what two shapes share goes along a direction: its thickest piece."""
    most = 0.0
    for lump in shared.Solids():
        if lump.Volume() < 1e-7:
            continue
        along = np.array([v.toTuple() for v in lump.Vertices()]) @ normal
        most = max(most, float(along.max() - along.min()))
    return most


def _strips_read(
    strips: list[Any],
    rows: list[list[Any]],
    cut: np.ndarray,
    toward: np.ndarray,
    places: list[list[dict[str, Any]]],
    thickness: float,
) -> list[dict[str, Any]]:
    """Each came strip against the two rows it's on: whether it's round both edges, how far
    it presses into them, and whether a flange's end lies over a pane's opening.

    A strip is on the length whose stretch of the cut its middle lies nearest. It's round an
    edge where a point `PROBE` past the row's outer face and one past its inner, each half a
    mm in from the row's edge there, are both inside it, and a point between the two rows is
    too. A
    flange's end is over lead where the row, just under its own face there, is solid."""
    found = []
    middles = [(cut[k] + cut[k + 1]) / 2.0 for k in range(len(cut) - 1)]
    for strip in strips:
        points = np.array([v.toTuple() for v in strip.Vertices()])
        middle = (points.max(axis=0) + points.min(axis=0)) / 2.0
        j = int(np.argmin([np.linalg.norm(middle - at) for at in middles]))
        way = cut[j + 1] - cut[j]
        way = way / np.linalg.norm(way)
        along = (points - cut[j]) @ way
        start, stop = float(along.min()), float(along.max())
        centre = cut[j] + (start + stop) / 2.0 * way
        held = 0
        pressed, shared = 0.0, 0.0
        over_opening: list[list[float]] = []
        for side, mine in ((-1.0, rows[0][j]), (1.0, rows[1][j])):
            normal = places[0 if side < 0 else 1][j]["normal"]
            # Into the row from the cut, in the row's own plane, square to the cut.
            into = np.cross(normal, way)
            into = into if float(into @ toward) * side > 0 else -into
            # The row's own face leans, so its outer edge and its inner are at different
            # places: each is read where the face's plane meets that side of the frame.
            face = _facing(mine, -side * toward)
            for height, past in ((thickness, PROBE), (0.0, -PROBE)):
                start_in = 0.0
                if face is not None:
                    lean = float(face["normal"] @ normal) / float(face["normal"] @ into)
                    start_in = float((face["at"] - cut[j]) @ into) - lean * (
                        height - float((face["at"] - cut[j]) @ normal)
                    )
                at = centre + (start_in + 0.5) * into + (height + past) * normal
                held += inside([strip], at)
            common = strip.intersect(mine)
            shared += float(common.Volume())
            pressed = max(pressed, _deepest(common, normal))
            # The strip's own corners furthest into this row, at its near end: each flange's
            # end, carried along the strip.
            reach = (points - cut[j]) @ into
            ends = points[(reach > reach.max() - 0.02) & (along < start + 0.02)]
            for corner in ends:
                height = float((corner - cut[j]) @ normal)
                under_face = thickness - 0.15 if height > thickness / 2.0 else 0.15
                for step in np.arange(0.5, stop - start, ALONG):
                    at = corner + step * way + (under_face - height) * normal
                    if not inside([mine], at):
                        over_opening.append([round(float(v), 2) for v in at])
        # Between the two rows: out of the dome along the plane between them, where the web is.
        out = places[0][j]["normal"] - float(places[0][j]["normal"] @ toward) * toward
        held += inside([strip], centre + thickness / 2.0 * out / np.linalg.norm(out))
        found.append(
            {
                "length": j + 1,
                "length_mm": stop - start,
                "held": held,
                "grips": held == 5,
                "press_mm": pressed,
                "shared_mm3": shared,
                "over_opening": over_opening,
            }
        )
    return sorted(found, key=lambda r: int(r["length"]))


def _rings_read(
    rings: list[Any], rows: list[list[Any]], places: list[list[dict[str, Any]]], thickness: float
) -> dict[str, Any]:
    """Each slice's hem edge and top edge against the rings: an edge is in a groove where a
    point `PROBE` past the row's outer face, one past its inner, and one past the edge itself
    are each inside a ring."""
    found: dict[str, Any] = {"shared_mm3": 0.0, "nearest_mm": 0.0}
    for name, j, sign in (("hem", 0, -1.0), ("top", -1, 1.0)):
        held = []
        for k in (0, 1):
            row, place = rows[k][j], places[k][j]
            normal, up = place["normal"], place["up"]
            points = np.array([v.toTuple() for v in row.Vertices()])
            along = points @ up * sign
            ends = points[along > along.max() - 1e-3]
            heights = (ends - ends[0]) @ normal
            edge = ends.mean(axis=0)
            middle = edge + (float(heights.min()) + thickness / 2.0 - float(
                (edge - ends[0]) @ normal
            )) * normal
            back = middle - sign * 1.5 * up
            probes = [
                back + (thickness / 2.0 + PROBE) * normal,
                back - (thickness / 2.0 + PROBE) * normal,
                middle + sign * (PROBE + 0.1) * up,
            ]
            held.append(sum(inside(rings, p) for p in probes))
            for ring in rings:
                found["shared_mm3"] += float(ring.intersect(row).Volume())
        found[name] = held
    return found


def run_seam(args: Any, report: Report) -> None:
    import cadquery as cq

    if len(args.maps) != 2 or len(args.frames) != 2:
        raise NoAnswer("`pieces seam A B --frames FA FB`: two slices' maps, and each one's frame")
    gap = GAP if args.gap is None else float(args.gap)
    press = PRESS if args.press is None else float(args.press)
    if gap < 0 or press < 0:
        raise NoAnswer("--gap, the gap the seams were built for, and --press are in mm, 0 or more")
    surfaces = [slice_surface(piecemap.load(path), path) for path in args.maps]
    frames = list(args.frames)
    numbers = [int(s["slice"]) for s in surfaces]

    def shares(k: int) -> bool:
        return (
            int(surfaces[k]["edges"]["right"]["slice"]) == numbers[1 - k]
            and int(surfaces[1 - k]["edges"]["left"]["slice"]) == numbers[k]
        )

    same_dome = surfaces[0].get("of") == surfaces[1].get("of") and surfaces[0].get(
        "slices"
    ) == surfaces[1].get("slices")
    if not same_dome or numbers[0] == numbers[1] or not (shares(0) or shares(1)):
        raise NoAnswer(
            f"slice {numbers[0]} and slice {numbers[1]} aren't neighbours on one dome, so they "
            f"share no edge: slice {numbers[0]}'s neighbours are slice "
            f"{surfaces[0]['edges']['left']['slice']} and slice "
            f"{surfaces[0]['edges']['right']['slice']}"
        )
    if not shares(0):
        surfaces, frames, numbers = surfaces[::-1], frames[::-1], numbers[::-1]
    # Slice A's right edge is slice B's left: the plane through the dome's axis and the cut.
    cut = np.array(surfaces[0]["edges"]["right"]["on_dome"], dtype=float)
    theirs = np.array(surfaces[1]["edges"]["left"]["on_dome"], dtype=float)
    cuts_off = float(np.linalg.norm(cut - theirs, axis=1).max())
    phi = math.atan2(cut[0][1], cut[0][0])
    toward = np.array([-math.sin(phi), math.cos(phi), 0.0])
    rows_a, thick_a = _rows_placed(frames[0], surfaces[0])
    rows_b, thick_b = _rows_placed(frames[1], surfaces[1])
    places = [placings(surfaces[0]), placings(surfaces[1])]
    if len(rows_a) != len(rows_b) or len(rows_a) != len(cut) - 1:
        raise NoAnswer(
            f"slice {numbers[0]} has {len(rows_a)} flat row(s) and slice {numbers[1]} "
            f"{len(rows_b)}: neighbours fold at the same lines"
        )
    report.fact("seam_between", numbers, how="the first's right edge and the second's left")
    report.fact("lengths_count", len(rows_a), how="straight lengths, one a flat row")
    report.numbers["frames"] = frames
    report.numbers["thickness_mm"] = [thick_a, thick_b]
    lengths = []
    overlap = 0.0
    points_inside = 0
    points_asked = 0
    nearest = 0.0
    no_face = []
    for j, (one, two) in enumerate(zip(rows_a, rows_b, strict=True)):
        face_a, face_b = _facing(one, toward), _facing(two, -toward)
        shared = float(one.intersect(two).Volume())
        overlap += shared
        close = _apart(one, two)
        nearest = max(nearest, close)
        row: dict[str, Any] = {"length": j + 1, "shared_mm3": shared, "nearest_mm": close}
        if face_a is None or face_b is None:
            no_face.append(j + 1)
            lengths.append(row)
            continue
        apart = _angle(face_a["normal"], -face_b["normal"])
        off_b = [float((p - face_b["at"]) @ face_b["normal"]) for p in face_a["corners"]]
        off_a = [float((p - face_a["at"]) @ face_a["normal"]) for p in face_b["corners"]]
        row.update(
            {
                "faces_apart_deg": apart,
                "gap_mm": max(*off_a, *off_b),
                "gap_least_mm": min(*off_a, *off_b),
                "face_mm2": [face_a["area"], face_b["area"]],
            }
        )
        # The second method: each face's own corners, a little inside it: in the other too?
        for other, face in ((two, face_a), (one, face_b)):
            for p in face["corners"]:
                points_asked += 1
                at = p - NUDGE * face["normal"] + 5.0 * NUDGE * (face["at"] - p) / max(
                    float(np.linalg.norm(face["at"] - p)), 1e-9
                )
                points_inside += inside([other], at)
        lengths.append(row)
    read = [row for row in lengths if "gap_mm" in row]
    widest = max((row["gap_mm"] for row in read), default=math.inf)
    least = min((row["gap_least_mm"] for row in read), default=-math.inf)
    turned = max((row["faces_apart_deg"] for row in read), default=180.0)
    report.numbers["lengths"] = lengths
    report.fact(
        "faces_apart_deg", turned, "°",
        "the most the two faces that meet at a length are off parallel",
    )  # fmt: skip
    report.fact(
        "gap_mm", widest, "mm",
        "the furthest a corner of either face stands off the other's plane, at any length",
    )  # fmt: skip
    report.fact("gap_least_mm", least, "mm", "and the nearest: under 0 is one face past the other")
    report.fact(
        "gap_built_mm", gap, "mm",
        f"--gap, the gap the seams were built for: the faces are held to within {WITHIN:g} mm "
        "of it",
    )  # fmt: skip
    report.fact(
        "overlap_mm3", overlap, "mm³",
        "what the two slices' rows share, by OpenCascade's `common`, length by length",
    )  # fmt: skip
    report.numbers["cuts_off_mm"] = cuts_off
    target = Path(args.out) / f"seam-{numbers[0]:02d}-{numbers[1]:02d}.step"
    target.parent.mkdir(parents=True, exist_ok=True)
    cq.Compound.makeCompound([*rows_a, *rows_b]).exportStep(str(target))
    report.wrote(str(target))
    each = []
    for number, rows in zip(numbers, (rows_a, rows_b), strict=True):
        one = target.with_name(f"{target.stem}.slice-{number:02d}.step")
        cq.Compound.makeCompound(rows).exportStep(str(one))
        report.wrote(str(one))
        each.append(str(one))
    report.numbers["placed_slices"] = each
    report.fact(
        "placed", str(target),
        how="both slices' rows where the maps put them, the dome's axis along z, and each "
        "slice's in a file of its own beside it: `render` draws them. Each row is a rigid "
        "flat piece, so they overlap a little at a fold's inner side, where a real slice bends",
    )  # fmt: skip
    wrong = []
    clash = []
    strips: list[dict[str, Any]] = []
    pressed = 0.0
    if args.came:
        solids = solids_of(args.came, "--came takes the strips as `pieces came` placed them")
        strips = _strips_read(solids, [rows_a, rows_b], cut, toward, places, thick_a)
        gripping = sum(bool(s["grips"]) for s in strips)
        covered = sum(len(s["over_opening"]) for s in strips)
        pressed = max((float(s["press_mm"]) for s in strips), default=0.0)
        report.numbers["strips"] = strips
        report.fact("strips_count", len(strips), how=f"in {args.came}")
        report.fact(
            "strips_gripping", gripping,
            how=f"round both slices' edges: each of five points, {PROBE:g} mm past a row's "
            "outer face and its inner, on both rows, and between the two faces, is inside it",
        )  # fmt: skip
        report.fact(
            "press_mm", pressed, "mm",
            f"the deepest a strip goes into a slice's face, its grip: --press allows {press:g}",
        )  # fmt: skip
        report.fact(
            "flanges_over_openings", covered,
            how=f"places along the flanges' ends, {ALONG:g} mm apart, where the row just "
            "under its own face isn't solid: a pane's opening, or past the frame",
        )  # fmt: skip
        if len(strips) != len(rows_a):
            wrong.append(f"there are {len(strips)} strip(s) for {len(rows_a)} length(s)")
        if gripping != len(strips):
            loose = [int(s["length"]) for s in strips if not s["grips"]]
            wrong.append(f"the strip at length {piecemap.numbered(loose)} isn't round both edges")
        if covered:
            first = next(s for s in strips if s["over_opening"])
            wrong.append(
                f"a flange lies over a pane's opening at {covered} place(s), the first at "
                f"{first['over_opening'][0]} on length {first['length']}: a narrower --flange"
            )
        if over(pressed, press):
            clash.append(
                f"a strip goes {pressed:.3f} mm into a slice's face, over the {press:g} mm "
                "--press allows: it was made for another thickness or gap"
            )
    if args.rings:
        rings = [
            solid
            for path in args.rings
            for solid in solids_of(path, "--rings takes the rings as `pieces ring` placed them")
        ]
        hoops = _rings_read(rings, [rows_a, rows_b], places, thick_a)
        report.numbers["rings"] = hoops
        report.fact(
            "edges_in_rings", {"hem": hoops["hem"], "top": hoops["top"]},
            how=f"for each slice, how many of three points are inside a ring: {PROBE:g} mm "
            "past the row's outer face, past its inner, and past the edge itself",
        )  # fmt: skip
        for name in ("hem", "top"):
            if any(0 < count < 3 for count in hoops[name]):
                wrong.append(f"a slice's {name} edge is only partly in a ring's groove")
        if all(count == 0 for name in ("hem", "top") for count in hoops[name]):
            wrong.append("neither slice's hem or top edge is in a ring's groove")
        if over(float(hoops["shared_mm3"]), SHARED):
            clash.append(
                f"the rings and the slices share {hoops['shared_mm3']:.3f} mm³: a ring made "
                "for another thickness or border"
            )
    report.second(
        f"points, with no boolean: of {points_asked} corner(s) of the faces that meet, each "
        f"moved {NUDGE:g} mm into its own row, {points_inside} are inside the other slice's; "
        f"and the nearest the two rows come at a length is {nearest:.4f} mm at the furthest, "
        "by OpenCascade's distance between shapes"
    )
    report.second(
        f"the two maps' own edges: slice {numbers[0]}'s right, on the dome, is within "
        f"{cuts_off:.5f} mm of slice {numbers[1]}'s left at every fold"
    )
    differs = []
    if overlap <= SHARED and points_inside:
        differs.append(
            f"the rows share {overlap:.4f} mm³ by `common`, and {points_inside} point(s) just "
            "inside one row's surface are inside the other too"
        )
    if read and not no_face and abs(nearest - widest) > WITHIN and turned <= PARALLEL_DEG:
        differs.append(
            f"the faces' corners are {widest:.3f} mm off each other's planes, and the rows "
            f"are {nearest:.3f} mm apart by the distance between shapes"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    if no_face:
        report.verdict(
            "FAIL",
            f"at length {piecemap.numbered(no_face)} a slice has no face toward its "
            "neighbour: `pieces build --seam` builds each length's",
        )
        return
    if over(overlap, SHARED):
        worst = max(lengths, key=lambda r: float(r["shared_mm3"]))
        clash.insert(
            0,
            f"slices {numbers[0]} and {numbers[1]} share {overlap:.3f} mm³ placed, the most at "
            f"length {worst['length']}: the faces are {turned:.2f}° off parallel, and one "
            f"passes {max(-least, 0.0):.3f} mm into the other",
        )
    if clash:
        report.verdict("INTERFERE", "; ".join(clash))
        return
    if over(turned, PARALLEL_DEG) or over(widest - gap, WITHIN) or over(gap - least, WITHIN):
        wrong.insert(
            0,
            f"the faces stand {least:.3f} to {widest:.3f} mm apart and {turned:.2f}° off "
            f"parallel, where they were built for a gap of {gap:g} mm",
        )
    if wrong:
        report.verdict("FAIL", "; ".join(wrong))
        return
    report.verdict(
        "OK",
        f"slices {numbers[0]} and {numbers[1]}, bent by their folds and placed, stand face "
        f"to face at all {len(lengths)} length(s), {widest:.4f} mm apart for a gap of "
        f"{gap:g} mm, and share nothing"
        + (
            f"; all {len(strips)} came strip(s) are round both edges, press {pressed:.3f} mm "
            "at the most, and none lies over a pane's opening"
            if args.came
            else ""
        )
        + ("; each slice's edges are in the rings' grooves" if args.rings else ""),
    )
