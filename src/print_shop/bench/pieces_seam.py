"""A dome's slices joined edge to edge: `pieces build --seam`, which builds a slice's two long
edges as seams, and `pieces seam`, which places two built neighbours round the dome and reads
how they meet, with the came strips and the rings that hold them (mqucifer/print-shop#166,
mqucifer/print-shop#190).

**The seam** (the Sponsor, 2026-11-18: "I'm not a fan of glued seems for 3d prints. It needs
some mechanical hold"). Two neighbouring slices stand at an angle to each other, a different
one at each of a slice's flat rows. So each long edge is a row of straight lengths, one a row,
and each is bevelled by half the angle between the two slices there: the two faces are then
parallel to the plane through the dome's axis and the cut, and stand face to face, the seam's
gap apart.

**The hold is from inside only** (the Sponsor, 2026-11-20: "Should be hidden"). On the outer
face a seam is the two slices' edges and the gap between them, and nothing over them. Along each
length the frame has a groove in its inner face, `GROOVE_WIDE` wide and `GROOVE_DEEP` deep,
`WALL` in from the seam's face, and a came strip (`pieces came`) lies inside the dome over both
slices' inner faces with a rib pressed into each groove, `SNAP` a side tighter than it. The two
ribs stand at the slices' angle, so once both slices are on, a strip comes off neither: the
ribs hold the edges together, and the strip under both inner faces holds them in line.

Why this hold, of those the Sponsor's brief named. A barb into a groove in a seam's face needs
something in the gap, and a gap of 0.6 mm leaves a web of one line once each side has its
play; a groove there would come out of the lead beside a pocket, 0.45 mm at a lead of 1.4. A
flange in a rebate along the inner face needs the rebate's roof printed in the air, as a frame
prints on its inner face, and a rebate open to the seam doesn't stop the slices parting. A
groove closed on both sides is a bridge as it prints, and its seam-side wall is what holds.

**Where the faces are.** A slice's rows have their corners on the dome (`pieces flatten`), and
the frame's inner face is taken to lie on them. A length's face leans out toward the outer face
by its bevel, so the frame is widest at its outer face, and a bevel takes no lead from beside a
pocket: only the gap does. With a seam the frame's side is set by the cut, whatever `--border`
is; the border is the frame at the top and the hem.

**What's refused.** A seam is inside the lead line, half of which is this slice's: `flatten`
leaves the glass half a lead from the cut's middle. A face that leaves less than `WALL` of
lead beside a pocket, or stands back past a pane's edge at the inner face, is named, and
nothing is built. So is a groove that leaves less than `WALL` between it and a pane's opening,
or less than `LIP_OVER` of lip over it under a pocket.

**`pieces seam`** reads two built frames. Each is cut at its folds into its flat rows, and each
row is set where the slice's map puts it: bent by its folds from the hem up, and turned round
the dome. Then, at each length: the two faces that meet, the angle between them and how far
apart they stand, against the gap they were built for; and what the two rows share, by
OpenCascade's `common`. With `--came`, each strip as `pieces came` placed it: what of it lies
over each slice's outer face and its inner, how it holds each slice, by a rib in a groove or a
channel round its edge, how far it presses into a slice, and whether it lies over a pane's
opening. With `--rings`, whether each slice's hem and top edge is in a ring's groove.
**Second method:** points, with no boolean. Each face's own corners, moved a little into their
own row, are asked whether they're inside the other row too, and the nearest the two rows come
is read by OpenCascade's distance between shapes. What lies over an outer face is read from a
strip's corners and again by points past the face.

**What it can't read:** how a heated lead line bends. Each row is placed as a rigid flat piece,
so a real slice's seam is as true as its folds are bent. Nor how hard a rib is to press home,
or how much it holds by: that's PETG's, and the snap is the Sponsor's figure for it.
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
#: How far apart two slices' faces stand, in mm, unless given. Nothing lies in the gap.
GAP = 0.6
#: The Sponsor's PETG snap, in mm a side (2026-11-20): how much tighter a rib is than its
#: groove. A slide is 0.15.
SNAP = 0.10
#: A groove in a slice's inner face for a strip's rib, in mm: how wide and how deep. Its rib is
#: the groove and the snap on each side, 0.8 mm, two lines.
GROOVE_WIDE, GROOVE_DEEP = 0.6, 0.4
#: The least lip left over a groove, under a pocket, in mm: two layers, `pieces build`'s least.
LIP_OVER = 0.4
#: How far a groove runs on past each end of its strip, in mm.
GROOVE_PAST = 0.25
#: A strip stops this far short of a fold, in mm, and of the hem and the top unless given.
OFF_FOLD = 0.5
OFF_END = 5.0
#: The shortest strip worth making, in mm.
LEAST_STRIP = 3.0
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
#: The most a strip may press into a slice, in mm, unless given: the snap.
PRESS = SNAP
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
    #: Each length's groove for a strip's rib, where `groove_plan` has placed them.
    grooves: list[dict[str, Any]] = field(default_factory=list)

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


def groove_across(gap: float, bevel_deg: float) -> tuple[float, float]:
    """Where a length's groove lies in its slice's inner face, square to the cut, from the cut's
    middle, in mm: its near side, `WALL` in from the seam's face there, and its far side."""
    near = gap / 2.0 / math.cos(math.radians(bevel_deg)) + WALL
    return near, near + GROOVE_WIDE


def strip_spans(
    found: Map, edge: Edge, hem: float | None = None, top: float | None = None
) -> list[tuple[float, float]]:
    """Where each length's strip lies along it, from the length's lower end, in mm: short of
    each fold by `OFF_FOLD`, and of the hem and the top by `hem` and `top`, `OFF_END` unless
    given, or further where the panel's own corner there is further up or down."""
    ring, _near, low, high = side_of(found, edge)
    first, last = edge.lengths[0], edge.lengths[-1]
    starts = max(OFF_END if hem is None else hem, float((ring[low] - first.a) @ first.way) + 1.0)
    stops = max(
        OFF_END if top is None else top,
        last.length - float((ring[high] - last.a) @ last.way) + 1.0,
    )
    spans = []
    for j, length in enumerate(edge.lengths):
        start = starts if j == 0 else OFF_FOLD
        stop = length.length - (stops if j == len(edge.lengths) - 1 else OFF_FOLD)
        if under(stop - start, LEAST_STRIP):
            raise NoAnswer(
                f"the {edge.name} edge's length {length.row} is {length.length:.1f} mm long, and "
                f"a strip on it from {start:.1f} to {stop:.1f} mm is under {LEAST_STRIP:g} mm: a "
                "smaller --hem or --top"
            )
        spans.append((start, stop))
    return spans


def groove_plan(seam: Seam, found: Map, openings: Any, z: dict[str, float]) -> None:
    """Each length's groove for a strip's rib, and whether the frame has room for it: `WALL` of
    lead between it and the nearest pane's opening, and `LIP_OVER` of lip over it, under the
    pocket. What hasn't room is refused, as a face that would cut a pocket is."""
    over_it = z["pocket_floor_z"] - GROOVE_DEEP
    needed = 0.0
    for edge in seam.edges:
        for length, (start, stop) in zip(edge.lengths, strip_spans(found, edge), strict=True):
            near, far = groove_across(seam.gap, length.bevel_deg)
            begin, end = start - GROOVE_PAST, stop + GROOVE_PAST
            e, o = length.way, length.out
            foot = shapely.Polygon(
                [
                    length.a + begin * e - near * o,
                    length.a + end * e - near * o,
                    length.a + end * e - far * o,
                    length.a + begin * e - far * o,
                ]
            )
            wall = float(foot.distance(openings)) if not openings.is_empty else math.inf
            said = f"the {edge.name} edge's length {length.row}"
            row = {
                "edge": edge.name,
                "length": length.row,
                "at": piecemap.xy((length.a + length.b) / 2.0),
            }
            seam.grooves.append(
                {
                    **row, "near_mm": near, "far_mm": far, "from_mm": begin, "to_mm": end,
                    "wall_to_opening_mm": wall, "lip_over_mm": over_it, "foot": foot,
                    "length_ref": length,
                }  # fmt: skip
            )
            if under(wall, WALL):
                needed = max(needed, found.lead_mm + 2.0 * (WALL - wall))
                seam.refused.append(
                    {
                        **row, "what": "opening", "wall_mm": wall, "least_mm": WALL,
                        "why": f"{said}: a groove for the strip's rib, {near:.2f} to {far:.2f} "
                        "mm from the cut's middle in the inner face, would leave "
                        + (f"{wall:.2f} mm of" if wall > 0.005 else "no")
                        + f" lead between it and the nearest pane's opening, where a nozzle "
                        f"prints {WALL:g} mm",
                    }  # fmt: skip
                )
        if under(over_it, LIP_OVER):
            seam.refused.append(
                {
                    "edge": edge.name, "length": 0, "at": None, "what": "lip over groove",
                    "wall_mm": over_it, "least_mm": LIP_OVER,
                    "why": f"a groove {GROOVE_DEEP:g} mm deep in the inner face leaves "
                    f"{over_it:.2f} mm of lip over it under the pocket, whose floor is at "
                    f"z={z['pocket_floor_z']:g}, where two layers are {LIP_OVER:g}: a thicker "
                    "frame, or a thinner pane, puts the floor higher",
                }  # fmt: skip
            )
            break
    if needed:
        lead = math.ceil(needed * 10.0 - 1e-9) / 10.0
        seam.lead_needed = max(seam.lead_needed or 0.0, lead)


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
    """A plate cut to its seams' faces: what's between each row's two faces, a block a row; and
    each length's groove in the inner face, a block square to the plate."""
    import cadquery as cq

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
    cut = plate.intersect(whole)
    if not seam.grooves:
        return cut
    blocks = []
    for groove in seam.grooves:
        length: Length = groove["length_ref"]
        e, o = length.way, length.out
        corners = [
            [
                [
                    [*(length.a + along * e - across * o), z]
                    for z in (-1.0, GROOVE_DEEP)
                ]
                for across in (groove["near_mm"], groove["far_mm"])
            ]
            for along in (groove["from_mm"], groove["to_mm"])
        ]
        blocks.append(block(np.array(corners)))
    return cut.cut(cq.Compound.makeCompound(blocks))


def grooves_volume(seam: Seam) -> float:
    """What the grooves take from the frame, in mm³: each a box."""
    return float(
        sum(
            (g["far_mm"] - g["near_mm"]) * GROOVE_DEEP * (g["to_mm"] - g["from_mm"])
            for g in seam.grooves
        )
    )


def grooves_read(frame: Any, seam: Seam) -> tuple[int, int]:
    """Each groove read back from the built frame, at its middle: empty where the rib goes,
    and solid beside it on the seam's side, beside it on the far side, and over it. How many
    of those points read as they should, of how many."""
    right, asked = 0, 0
    for groove in seam.grooves:
        length: Length = groove["length_ref"]
        e, o = length.way, length.out
        along = (groove["from_mm"] + groove["to_mm"]) / 2.0
        middle = (groove["near_mm"] + groove["far_mm"]) / 2.0
        half = GROOVE_DEEP / 2.0
        for across, z, solid in (
            (middle, half, False),
            (groove["near_mm"] - WALL / 2.0, half, True),
            (groove["far_mm"] + WALL / 2.0, half, True),
            (middle, GROOVE_DEEP + LIP_OVER / 2.0, True),
        ):
            at = np.array([*(length.a + along * e - across * o), z])
            asked += 1
            right += inside([frame], at) == solid
    return right, asked


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
        "through the dome's axis and the cut. Nothing lies in it",
    )  # fmt: skip
    thin = min(length.wall_mm for edge in seam.edges for length in edge.lengths)
    report.fact(
        "seam_wall_mm", thin, "mm",
        f"the least lead a seam leaves beside a pocket, at the pocket's floor: the cut's "
        f"middle is half the map's lead, {lead / 2.0:g} mm, from the glass",
    )  # fmt: skip
    if seam.grooves:
        say_hold(seam, report)
    report.line(
        "the frame's inner face lies on the slice's rows, and each seam's face leans out "
        "toward the outer face, the one the map is seen from. --border is the frame at the top "
        "and the hem only. `pieces came` makes the strips whose ribs go in the grooves, "
        "`pieces ring` the rings at the top and the hem, and `pieces seam` places two built "
        "neighbours and reads how they meet"
    )


def say_hold(seam: Seam, report: Report) -> None:
    """A build's lines about the grooves that hold its seams, from the inside only."""
    report.numbers["grooves"] = [
        {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in groove.items()
            if k not in ("foot", "length_ref")
        }
        for groove in seam.grooves
    ]
    wall = min(float(g["wall_to_opening_mm"]) for g in seam.grooves)
    report.fact(
        "groove_mm", [GROOVE_WIDE, GROOVE_DEEP], "mm",
        f"each length's groove in the inner face, wide and deep, {WALL:g} mm in from the "
        f"seam's face: a came strip's rib goes in it, {SNAP:g} mm a side tighter",
    )  # fmt: skip
    report.fact(
        "groove_wall_mm", wall, "mm",
        "the least lead between a groove and the nearest pane's opening, beside it in the "
        "inner face",
    )  # fmt: skip
    report.fact(
        "lip_over_groove_mm", seam.grooves[0]["lip_over_mm"], "mm",
        "the lip left over a groove, under the pocket",
    )  # fmt: skip
    report.line(
        "the hold, from the inside only (the Sponsor, 2026-11-20: \"Should be hidden\"): along "
        f"each length a groove {GROOVE_WIDE:g} mm wide and {GROOVE_DEEP:g} deep in the inner "
        f"face, {WALL:g} mm in from the seam's face, for a strip's rib. Its roof is a bridge "
        f"{GROOVE_WIDE:g} mm wide as the frame prints, on its inner face. The outer face is the "
        "slices' edges and the gap. A barb in a seam's face would need a web in the gap, one "
        "line wide, and its groove would cut the lead beside a pocket; a flange in a rebate "
        "along the inner face would print the rebate's roof in the air, and an open rebate "
        "doesn't stop the slices parting"
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
        "against the cut's middle, from the face's own place at the pocket's floor, and for a "
        "groove from its outline against the panes' openings as they'd be built, in Shapely"
    )
    room = (
        f". A lead of about {seam.lead_needed:g} mm in the bands file would leave room, or a "
        "smaller --seam-gap"
        if seam.lead_needed
        else ""
    )
    report.verdict(
        "FAIL",
        f"the seams would cut a pocket, a lip or the lead beside an opening at "
        f"{len(seam.refused)} place(s), so nothing was built: {seam.refused[0]['why']}"
        + (f"; and {len(seam.refused) - 1} more" if len(seam.refused) > 1 else "")
        + room,
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


def _deepest(shared: Any, normal: np.ndarray, into: np.ndarray) -> float:
    """How far what two shapes share goes into one: each piece's thinner way, along the slice's
    normal or across it, and the thickest such piece. A bead pressed into a face is thin along
    the normal, and a rib pressed into a groove's wall is thin across."""
    most = 0.0
    for lump in shared.Solids():
        if lump.Volume() < 1e-7:
            continue
        corners = np.array([v.toTuple() for v in lump.Vertices()])
        spans = [float(np.ptp(corners @ way)) for way in (normal, into)]
        most = max(most, min(spans))
    return most


def _strips_read(
    strips: list[Any],
    rows: list[list[Any]],
    cut: np.ndarray,
    toward: np.ndarray,
    places: list[list[dict[str, Any]]],
    thickness: float,
) -> list[dict[str, Any]]:
    """Each came strip against the two rows it's on: what of it lies over each row's outer face
    and its inner, how it holds each, how far it presses into them, and whether it lies over a
    pane's opening.

    A strip is on the length whose stretch of the cut its middle lies nearest. Its corners say
    what lies where, each against the row's own face toward the seam: past the outer face is
    over it, past the inner face under it, and between the two a rib, whose middle, a little
    under its top, is inside the strip and outside the row where it's in a groove. So a strip
    holds a row by a rib in its groove with the strip under the inner face too, or by a channel
    round its edge, over both faces. Points `PROBE` past the outer face, from the row's edge
    2 mm in, read the outer face a second way. A strip's end furthest into a row is over lead
    where the row, just past its own face there, is solid."""
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
        pressed, shared = 0.0, 0.0
        over_opening: list[list[float]] = []
        sides: list[dict[str, Any]] = []
        for k, (side, mine) in enumerate(((-1.0, rows[0][j]), (1.0, rows[1][j]))):
            normal = places[k][j]["normal"]
            # Into the row from the cut, in the row's own plane, square to the cut.
            into = np.cross(normal, way)
            into = into if float(into @ toward) * side > 0 else -into
            # The row's own face leans, so its outer edge and its inner are at different
            # places: each is read where the face's plane meets that height of the frame.
            face = _facing(mine, -side * toward)
            lean, face_at, face_high = 0.0, 0.0, 0.0
            if face is not None:
                lean = float(face["normal"] @ normal) / float(face["normal"] @ into)
                face_at = float((face["at"] - cut[j]) @ into)
                face_high = float((face["at"] - cut[j]) @ normal)

            def face_in(height: float, lean: float = lean, at: float = face_at,
                        high: float = face_high) -> float:  # fmt: skip
                return at - lean * (min(max(height, 0.0), thickness) - high)

            rel = points - cut[j]
            high, deep = rel @ normal, rel @ into
            past = deep > np.array([face_in(h) for h in high]) + 0.01
            outer = past & (high > thickness + 0.01)
            inner = past & (high < -0.01)
            within = past & (high > 0.1) & (high < thickness - 0.1)
            # The second way: points past the outer face, from the row's edge in.
            probes = [
                centre + (face_in(thickness) + r) * into + (thickness + PROBE) * normal
                for r in np.arange(0.1, 2.0 + 1e-9, 0.1)
            ]
            probed = sum(inside([strip], at) for at in probes)
            rib = None
            if within.any():
                top = float(high[within].max())
                at = points[within & (high > top - 0.01)].mean(axis=0) - 0.05 * normal
                rib = {
                    "in_strip": inside([strip], at),
                    "in_groove": not inside([mine], at),
                    "at_mm": round(float((at - cut[j]) @ into), 4),
                    "tall_mm": round(top, 4),
                }
            if rib and rib["in_strip"] and rib["in_groove"] and inner.any():
                holds = "a rib in its groove"
            elif outer.any() and inner.any():
                holds = "a channel round its edge"
            else:
                holds = ""
            sides.append(
                {
                    "holds": holds,
                    "rib": rib,
                    "outer_mm": round(float(high[outer].max()) - thickness, 4)
                    if outer.any()
                    else 0.0,
                    "outer_reach_mm": round(float(deep[outer].max()), 4) if outer.any() else 0.0,
                    "outer_cover_mm": round(float(deep[outer].max()) - face_in(thickness), 4)
                    if outer.any()
                    else 0.0,
                    "outer_probed": int(probed),
                    "inner_mm": round(-float(high[inner].min()), 4) if inner.any() else 0.0,
                    "inner_reach_mm": round(float(deep[inner].max()), 4) if inner.any() else 0.0,
                }
            )
            # Fuzzy by a tenth of a micron: a strip's bar lies on the inner face, and an exact
            # `common` leaves a fin of no thickness there, joined to what a rib presses into.
            common = strip.intersect(mine, tol=1e-4)
            shared += float(common.Volume())
            pressed = max(pressed, _deepest(common, normal, into))
            # The strip's own corners furthest into this row, at its near end, carried along it.
            ends = points[(deep > deep.max() - 0.02) & (along < start + 0.02)]
            for corner in ends:
                height = float((corner - cut[j]) @ normal)
                under_face = thickness - 0.15 if height > thickness / 2.0 else 0.15
                for step in np.arange(0.5, stop - start, ALONG):
                    at = corner + step * way + (under_face - height) * normal
                    if not inside([mine], at):
                        over_opening.append([round(float(v), 2) for v in at])
        # Between the two rows: out of the dome along the plane between them, where a web is.
        out = places[0][j]["normal"] - float(places[0][j]["normal"] @ toward) * toward
        found.append(
            {
                "length": j + 1,
                "length_mm": stop - start,
                "sides": sides,
                "holds": [s["holds"] for s in sides],
                "holds_both": all(s["holds"] for s in sides),
                "over_outer_face": any(s["outer_mm"] > 0 for s in sides),
                "over_outer_probed": any(s["outer_probed"] for s in sides),
                "in_gap": inside([strip], centre + thickness / 2.0 * out / np.linalg.norm(out)),
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
    disagree: list[str] = []
    strips: list[dict[str, Any]] = []
    pressed = 0.0
    if args.came:
        solids = solids_of(args.came, "--came takes the strips as `pieces came` placed them")
        strips = _strips_read(solids, [rows_a, rows_b], cut, toward, places, thick_a)
        holding = sum(bool(s["holds_both"]) for s in strips)
        covered = sum(len(s["over_opening"]) for s in strips)
        pressed = max((float(s["press_mm"]) for s in strips), default=0.0)
        over_outer = [s for s in strips if s["over_outer_face"]]
        proud = max((float(x["outer_mm"]) for s in strips for x in s["sides"]), default=0.0)
        under_inner = max((float(x["inner_mm"]) for s in strips for x in s["sides"]), default=0.0)
        report.numbers["strips"] = strips
        report.fact("strips_count", len(strips), how=f"in {args.came}")
        report.fact(
            "over_outer_face", len(over_outer),
            how="strips with anything over either slice's outer face, by their corners past "
            "it: the seam is to show only the slices' edges and the gap there",
        )  # fmt: skip
        report.fact(
            "over_outer_face_mm", proud, "mm",
            "how far proud of an outer face a strip stands, at the most",
        )  # fmt: skip
        report.fact(
            "over_inner_face_mm", under_inner, "mm",
            "how far in from an inner face, inside the dome, a strip stands, at the most",
        )  # fmt: skip
        report.fact(
            "strips_holding", holding,
            how="those that hold both slices: each by a rib in its groove with the strip under "
            "its inner face, or by a channel round its edge",
        )  # fmt: skip
        report.fact(
            "press_mm", pressed, "mm",
            f"the deepest a strip goes into a slice, its grip: --press allows {press:g}",
        )  # fmt: skip
        report.fact(
            "strips_over_openings", covered,
            how=f"places along each strip's end furthest into a slice, {ALONG:g} mm apart, "
            "where the slice just past its own face isn't solid: a pane's opening, or past the "
            "frame",
        )  # fmt: skip
        numbers_ab = numbers

        def held(s: dict[str, Any]) -> str:
            pairs = zip(numbers_ab, s["holds"], strict=True)
            return ", ".join(f"slice {n} by {h or 'nothing'}" for n, h in pairs)

        report.line(
            "strips, each its length, how it holds each slice, and what lies over the slices' "
            "faces: "
            + "; ".join(
                f"strip {s['length']}, {s['length_mm']:.1f} mm: {held(s)}; under the inner "
                f"faces {max(x['inner_reach_mm'] for x in s['sides']):.2f} mm in from the cut's "
                "middle, and "
                + (
                    f"over {max(x['outer_cover_mm'] for x in s['sides']):.2f} mm of an outer "
                    "face from its edge"
                    if s["over_outer_face"]
                    else "nothing over the outer faces"
                )
                for s in strips
            )
        )
        if len(strips) != len(rows_a):
            wrong.append(f"there are {len(strips)} strip(s) for {len(rows_a)} length(s)")
        if over_outer:
            first = over_outer[0]
            side = next(x for x in first["sides"] if x["outer_mm"] > 0)
            wrong.append(
                f"{len(over_outer)} strip(s) lie over a slice's outer face, the first at length "
                f"{first['length']}, {side['outer_mm']:.2f} mm proud and over "
                f"{side['outer_cover_mm']:.2f} mm of it from the slice's edge: the seam is to be "
                "hidden from outside (the Sponsor, 2026-11-20), so `pieces came` holds it from "
                "the inside"
            )
        if holding != len(strips):
            loose = [int(s["length"]) for s in strips if not s["holds_both"]]
            wrong.append(
                f"the strip at length {piecemap.numbered(loose)} doesn't hold both slices, by a "
                "rib in a groove or a channel round the edge"
            )
        if covered:
            first = next(s for s in strips if s["over_opening"])
            wrong.append(
                f"a strip lies over a pane's opening at {covered} place(s), the first at "
                f"{first['over_opening'][0]} on length {first['length']}: a narrower --bar or "
                "--flange"
            )
        if over(pressed, press):
            clash.append(
                f"a strip goes {pressed:.3f} mm into a slice, over the {press:g} mm --press "
                "allows: it was made for another thickness or gap"
            )
        split = [s for s in strips if s["over_outer_face"] != s["over_outer_probed"]]
        report.second(
            "what lies over each outer face, read again by points: "
            f"{PROBE:g} mm past it, from the slice's edge 2 mm in, 0.1 mm apart, at each "
            f"strip's middle; {len(strips) - len(split)} of {len(strips)} strip(s) read the same "
            "both ways"
        )
        if split:
            disagree.append(
                f"the strip at length {piecemap.numbered([int(s['length']) for s in split])} "
                "reads over an outer face by its corners and not by points past it, or the "
                "other way"
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
    differs = list(disagree)
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
            f"; all {len(strips)} came strip(s) hold both slices, "
            + (
                "each by a rib in its groove"
                if all(h == "a rib in its groove" for s in strips for h in s["holds"])
                else "by a rib or a channel"
            )
            + f", press {pressed:.3f} mm at the most, lie over neither outer face, and none "
            "lies over a pane's opening"
            if args.came
            else ""
        )
        + ("; each slice's edges are in the rings' grooves" if args.rings else ""),
    )
