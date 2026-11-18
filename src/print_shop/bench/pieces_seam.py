"""A dome's slices joined edge to edge: `pieces build --seam`, which builds a slice's two long
edges as seams, and `pieces seam`, which places two built neighbours round the dome and reads
how they meet (mqucifer/print-shop#166).

**The seam** (agreed with the Sponsor, 2026-11-17). Two neighbouring slices stand at an angle to
each other, a different one at each of a slice's flat rows. So each long edge is a row of
straight lengths, one a row, and each is bevelled by half the angle between the two slices
there: the two faces then lie on one plane, the one through the dome's axis and the cut, and
meet flat. Small pins on one edge enter holes in the other, to line the slices up and hold them
while the glue sets.

**Where the faces are.** A slice's rows have their corners on the dome (`pieces flatten`), and
the frame's middle, half its thickness up, is taken to lie on them. A length's face passes
through the cut's middle there and leans out toward the outer face by its bevel, so the frame
is wider than the cut at its outer face and narrower at its inner. With a seam the frame's side
is the cut's middle whatever `--border` is; the border is the frame at the top and the hem.

**A pin** is a small key, square in section so it prints as part of each layer's outline: so
far along the edge, so far through the frame's thickness, and so far out of the face, square to
it. Its hole is the play larger all round and deeper. Each length has `--pins` of them, evenly
along it, at the same places on both slices.

**What's refused.** A seam is inside the lead line, half of which is this slice's: `flatten`
leaves the glass half a lead from the cut's middle. A bevel that leaves less than `WALL` of
lead beside a pocket, or passes under a pane's edge at the inner face, and a hole that leaves
less than `WALL` round it, are named, and nothing is built. On a lead of 1.4 mm each half is
0.7 mm, and no hole a nozzle prints fits: the bevels alone do (`--seam plain,plain`).

**`pieces seam`** reads two built frames. Each is cut at its folds into its flat rows, and each
row is set where the slice's map puts it: bent by its folds from the hem up, and turned round
the dome. Then, at each length: the two faces that meet, the angle between them and how far
either stands off the other; what the two rows share, by OpenCascade's `common`; and each pin,
which is whatever of one row stands past its face, with whether the other row is round it.
**Second method:** points. Each face's and each pin's own corners, moved a little into their
own row, are asked whether they're inside the other row too, and the nearest the two rows come is
read by OpenCascade's distance between shapes, with no boolean.

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

#: What an edge may be built as.
KINDS = ("pins", "holes", "plain")
#: The least lead a seam leaves beside a pocket and round a hole, in mm: a nozzle's width.
WALL = 0.4
#: A pin unless given, in mm: along the edge, through the frame, and out of the face.
PIN = (1.2, 1.6, 0.5)
#: The gap round a pin in its hole, and past its end, in mm, unless given.
PIN_PLAY = 0.1
#: The least frame over and under a hole, in mm: two layers.
SKIN = 0.4
#: A pin's hole stays this far from its length's ends, in mm: a fold is where a slice bends.
OFF_FOLD = 1.0
#: How far two faces may stand apart and still meet, in mm, unless given.
GAP = 0.05
#: Two rows share nothing where their overlap is under this, in mm³.
SHARED = 1e-3
#: A face is one that meets the neighbour where it's within this of facing it, in degrees.
FACING_DEG = 35.0
#: Two faces that meet are this near parallel, in degrees.
PARALLEL_DEG = 0.1
#: A point is asked whether it's inside the other row from this far inside its own, in mm.
NUDGE = 0.02
#: A pin is in a hole where the other row is this far past each of its sides, in mm.
PROBE = 0.3
#: A part of a row that stands less than this past its face, in mm, isn't a pin: a side built
#: with no seam is a shallow arc, and stands a few hundredths past the flat through its middle.
PIN_LEAST = 0.1
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
    pins: int = 0
    half_mm: float = math.inf
    wall_mm: float = math.inf
    by_corners_deg: float = 0.0
    by_dome_deg: float = 0.0
    at: list[np.ndarray] = field(default_factory=list)

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

    def x_at(self, y: float, z: float, mid: float) -> float:
        """The face's x at a height on the map and a height in the frame."""
        o = self.out
        return float(self.a[0] + ((z - mid) * self.lean - (y - self.a[1]) * o[1]) / o[0])

    def frame(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """The face's own directions, in the flat frame: along the edge, across the face from
        the inner side of the frame to the outer, and out of it."""
        e, o = self.way, self.out
        a = math.radians(self.bevel_deg)
        return (
            np.array([e[0], e[1], 0.0]),
            np.array([math.sin(a) * o[0], math.sin(a) * o[1], math.cos(a)]),
            np.array([math.cos(a) * o[0], math.cos(a) * o[1], -math.sin(a)]),
        )


@dataclass
class Edge:
    name: str
    side: int
    kind: str
    slice: int
    lengths: list[Length]


@dataclass
class Seam:
    """A slice's two edges as seams, and what building them takes."""

    edges: list[Edge]
    thickness: float
    pin: tuple[float, float, float]
    play: float
    refused: list[dict[str, Any]] = field(default_factory=list)
    lead_needed: float | None = None

    @property
    def mid(self) -> float:
        return self.thickness / 2.0

    def count(self, kind: str) -> int:
        mine = [edge for edge in self.edges if edge.kind == kind]
        return sum(length.pins for edge in mine for length in edge.lengths)


def kinds_of(text: str) -> tuple[str, str]:
    """`holes,pins`: what the left edge and the right are built as."""
    words = [w.strip().lower() for w in str(text).split(",")]
    if len(words) != 2 or any(w not in KINDS for w in words):
        raise NoAnswer(
            f"--seam {text}: it's LEFT,RIGHT, what the slice's left edge and its right are "
            f"built as, each one of {', '.join(KINDS)}: `--seam holes,pins` on every slice puts "
            "each slice's pins in its neighbour's holes"
        )
    if words[0] == words[1] != "plain":
        raise NoAnswer(
            f"--seam {text}: {words[0]} on both edges meet the same on each neighbour. Give "
            f"pins on one edge and holes on the other, `--seam holes,pins`, or `plain,plain` "
            "for bevels alone"
        )
    return words[0], words[1]


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


def edges_of(found: Map, name: str, kinds: tuple[str, str]) -> list[Edge]:
    """A slice's two long edges as their straight lengths, from the hem up."""
    surface = slice_surface(found, name)
    edges = []
    for side, word, kind in ((-1, "left", kinds[0]), (1, "right", kinds[1])):
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
            lengths.append(
                Length(
                    j + 1, side, flat[j], flat[j + 1], angle, angle / 2.0,
                    None if j == 0 else float(flat[j][1]),
                    None if j == len(join) - 1 else float(flat[j + 1][1]),
                    by_corners_deg=checks[j][0], by_dome_deg=checks[j][1],
                )  # fmt: skip
            )
        edges.append(Edge(word, side, kind, int(given["slice"]), lengths))
    return edges


def _hole_corners(length: Length, at: np.ndarray, seam: Seam, grow: float) -> np.ndarray:
    """A pin's eight corners in the flat frame, or with `grow` its hole's: the hole is that
    much larger all round and deeper, and goes into the frame where the pin stands out."""
    e, d, m = length.frame()
    along, through, out = seam.pin
    centre = np.array([at[0], at[1], seam.mid])
    reach = (0.1, -(out + grow)) if grow > 0 else (-0.1, out)
    return np.array(
        [
            centre + i * (along / 2.0 + grow) * e + j * (through / 2.0 + grow) * d + k * m
            for i in (-1.0, 1.0)
            for j in (-1.0, 1.0)
            for k in reach
        ]
    )


def seam_plan(found: Map, name: str, args: Any, z: dict[str, float]) -> Seam:
    """A slice's seams, with every place one would cut a pocket or a lip.

    A piece's pocket never stands past its outline, so the lead beside a seam is read from the
    pieces' outlines: how far the nearest is from the cut's middle, at each length. A bevel
    takes its lean off that at every height under the frame's middle, and a hole its depth."""
    kinds = kinds_of(args.seam)
    thickness = float(args.thickness)
    pin = tuple(float(v) for v in (args.pin or PIN))
    play = PIN_PLAY if args.pin_play is None else float(args.pin_play)
    count = 1 if args.pins is None else int(args.pins)
    if len(pin) != 3 or not all(v > 0 for v in pin):
        raise NoAnswer(
            "--pin is ALONG,THROUGH,OUT: how far a pin runs along the edge, through the "
            "frame's thickness, and out of the face, in mm, each over 0"
        )
    if not play > 0:
        raise NoAnswer("--pin-play is the gap round a pin in its hole, in mm, over 0")
    if count < 1:
        raise NoAnswer(
            "--pins is how many pins each straight length has, 1 or more: `--seam plain,plain` "
            "builds the bevels with none"
        )
    seam = Seam(edges_of(found, name, kinds), thickness, (pin[0], pin[1], pin[2]), play)
    if "pins" in kinds and under(thickness - pin[1] - 2.0 * play, 2.0 * SKIN):
        raise NoAnswer(
            f"a pin {pin[1]:g} mm through a frame {thickness:g} mm thick, with {play:g} mm of "
            f"play, leaves under {SKIN:g} mm of frame over and under its hole: give a --pin "
            f"no more than {thickness - 2.0 * SKIN - 2.0 * play:g} mm through"
        )
    floor, top = z["pocket_floor_z"], z["pocket_top_z"]
    needed = 0.0
    for edge in seam.edges:
        for length in edge.lengths:
            mine = [
                p
                for p in found.pieces
                if (length.low is None or p.shape.bounds[3] > length.low)
                and (length.high is None or p.shape.bounds[1] < length.high)
            ]
            if mine:
                every = np.vstack([shapely.get_coordinates(p.shape.exterior) for p in mine])
                length.half_mm = float((-(every - length.a) @ length.out).min())
            at_floor = max(seam.mid - floor, seam.mid - top, 0.0) * length.lean
            at_face = seam.mid * length.lean
            length.wall_mm = length.half_mm - at_floor
            where = piecemap.xy((length.a + length.b) / 2.0)
            said = f"the {edge.name} edge's length {length.row}"
            if under(length.wall_mm, WALL):
                needed = max(needed, 2.0 * (WALL + at_floor))
                seam.refused.append(
                    {
                        "edge": edge.name, "length": length.row, "what": "bevel", "at": where,
                        "wall_mm": length.wall_mm, "least_mm": WALL,
                        "why": f"{said}: a bevel of {length.bevel_deg:.2f}° comes in "
                        f"{at_floor:.2f} mm at the pocket's floor, and the nearest pocket is "
                        f"{length.half_mm:.2f} mm from the cut's middle, which leaves "
                        + (f"{length.wall_mm:.2f} mm of" if length.wall_mm > 0.005 else "no")
                        + f" lead beside that pocket, where a nozzle prints {WALL:g} mm",
                    }  # fmt: skip
                )
            elif under(length.half_mm - at_face, 0.0):
                needed = max(needed, 2.0 * at_face)
                seam.refused.append(
                    {
                        "edge": edge.name, "length": length.row, "what": "lip", "at": where,
                        "wall_mm": length.half_mm - at_face, "least_mm": 0.0,
                        "why": f"{said}: a bevel of {length.bevel_deg:.2f}° comes in "
                        f"{at_face:.2f} mm at the frame's inner face, past the edge of a pane "
                        f"{length.half_mm:.2f} mm from the cut's middle: it would cut the lip "
                        "under that pane",
                    }  # fmt: skip
                )
            if edge.kind == "plain":
                continue
            room = length.length / count
            if under(room / 2.0, pin[0] / 2.0 + play + OFF_FOLD):
                fits = int(length.length // (pin[0] + 2.0 * (play + OFF_FOLD)))
                raise NoAnswer(
                    f"{said} is {length.length:.2f} mm long: {count} pin(s) {pin[0]:g} mm along "
                    f"it, each {OFF_FOLD:g} mm or more from a fold and the next, don't fit. "
                    + (f"--pins {fits} do" if fits else "A shorter --pin does, or fewer rows")
                )
            length.pins = count
            length.at = [
                length.a + (n + 0.5) / count * (length.b - length.a) for n in range(count)
            ]
            if edge.kind != "holes" or not mine:
                continue
            glass = shapely.union_all([p.shape for p in mine])
            for at in length.at:
                corners = _hole_corners(length, at, seam, play)
                foot = shapely.MultiPoint(corners[:, :2]).convex_hull
                wall = float(foot.distance(glass))
                if under(wall, WALL):
                    reach = float((-(corners[:, :2] - length.a) @ length.out).max())
                    needed = max(needed, 2.0 * (reach + WALL))
                    seam.refused.append(
                        {
                            "edge": edge.name, "length": length.row, "what": "hole",
                            "at": piecemap.xy(at), "wall_mm": wall, "least_mm": WALL,
                            "why": f"{said}: a hole for a pin {pin[2]:g} mm out, at "
                            f"{piecemap.said_at(at)}, reaches {reach:.2f} mm in from the cut's "
                            f"middle, and the nearest pocket is {length.half_mm:.2f} mm from "
                            "it, which leaves "
                            + (f"{wall:.2f} mm of" if wall > 0.005 else "no")
                            + f" lead between the hole and that pocket, where a nozzle prints "
                            f"{WALL:g} mm",
                        }  # fmt: skip
                    )
    if seam.refused:
        seam.lead_needed = math.ceil(needed * 10.0 - 1e-9) / 10.0
    return seam


def widened(found: Map, seam: Seam) -> Any:
    """The panel and a strip past each cut, level with the panel's own corners there: the
    plate the seams are cut from. It's wider than any bevel reaches at the outer face."""
    most = max(length.lean for edge in seam.edges for length in edge.lengths)
    past = seam.mid * most + 0.5
    outline = shapely.get_coordinates(found.panel.exterior)
    parts = [found.panel]
    for edge in seam.edges:
        near = np.zeros(len(outline), dtype=bool)
        for length in edge.lengths:
            inside = -(outline - length.a) @ length.out
            along = (outline - length.a) @ length.way
            near |= (inside < 2.0 * max(found.lead_mm, 1.0)) & (along > -1.0) & (
                along < length.length + 1.0
            )
        if not near.any():
            raise NoAnswer(f"the panel's outline doesn't reach its {edge.name} cut")
        low, high = float(outline[near, 1].min()), float(outline[near, 1].max())
        x0, _y0, x1, _y1 = found.panel.bounds
        band = shapely.box(x0 - past - 5.0, low, x1 + past + 5.0, high)
        for length in edge.lengths:
            e, o = length.way, length.out
            a, b = length.a - 2.0 * e, length.b + 2.0 * e
            inner = max(found.lead_mm, 1.0)
            strip = shapely.Polygon([a - inner * o, b - inner * o, b + past * o, a + past * o])
            parts.append(strip.intersection(band))
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
                    (one.x_at(low, z, seam.mid), low),
                    (two.x_at(low, z, seam.mid), low),
                    (two.x_at(high, z, seam.mid), high),
                    (one.x_at(high, z, seam.mid), high),
                ]
            )
        )
    return base.intersection(shapely.union_all(quads))


def volume_by_sections(seam: Seam, base: Any, steps: int = 12) -> float:
    """The bevelled plate's volume from its sections in Shapely, by Simpson's rule, with each
    pin added and each hole taken."""
    heights = np.linspace(0.0, seam.thickness, steps + 1)
    areas = [section(seam, base, float(h)).area for h in heights]
    weights = [1.0] + [4.0 if k % 2 else 2.0 for k in range(1, steps)] + [1.0]
    plate = float(np.dot(weights, areas)) * seam.thickness / steps / 3.0
    along, through, out = seam.pin
    play = seam.play
    pins = seam.count("pins") * along * through * out
    holes = seam.count("holes") * (along + 2 * play) * (through + 2 * play) * (out + play)
    return plate + pins - holes


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


def kept(seam: Seam, base: Any) -> Any:
    """What the seams leave of a plate: between each row's two faces, a block a row."""
    import cadquery as cq

    _x0, y0, _x1, y1 = base.bounds
    z0, z1 = -1.0, seam.thickness + 1.0
    blocks = []
    for low, high, one, two in _bands(seam, y0 - 5.0, y1 + 5.0):
        blocks.append(
            block(
                np.array(
                    [
                        [[[side.x_at(y, z, seam.mid), y, z] for z in (z0, z1)] for y in (low, high)]
                        for side in (one, two)
                    ]
                )
            )
        )
    whole = blocks[0]
    for more in blocks[1:]:
        whole = whole.fuse(more)
    return cq.Compound.makeCompound(whole.Solids()) if len(whole.Solids()) != 1 else whole


def with_seams(plate: Any, seam: Seam, base: Any) -> Any:
    """A plate cut to its seams' faces, with each pin standing out of them and each hole cut."""
    frame = plate.intersect(kept(seam, base))
    for edge in seam.edges:
        for length in edge.lengths:
            for at in length.at:
                if edge.kind == "pins":
                    frame = frame.fuse(block(_hole_corners(length, at, seam, 0.0)))
                elif edge.kind == "holes":
                    frame = frame.cut(block(_hole_corners(length, at, seam, seam.play)))
    return frame


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
            _e, _d, m = length.frame()
            middle = np.array([*((length.a + length.b) / 2.0), seam.mid])
            near = [
                _angle(normal, m)
                for normal, centre in flats
                if abs(float((centre - middle) @ m)) < 0.05
            ]
            worst = max(worst, min(near) if near else 180.0)
    return worst


def say_seams(seam: Seam, report: Report, lead: float) -> None:
    """A build's lines about its seams: each length's bevel and its pins."""
    numbers: dict[str, Any] = {}
    for edge in seam.edges:
        numbers[edge.name] = {
            "kind": edge.kind,
            "slice": edge.slice,
            "lengths": [
                {
                    "length": length.row,
                    "length_mm": round(length.length, 4),
                    "join_deg": length.join_deg,
                    "bevel_deg": length.bevel_deg,
                    "pins": length.pins,
                    "lead_beside_mm": round(length.half_mm, 4),
                    "wall_at_pocket_mm": round(length.wall_mm, 4),
                }
                for length in edge.lengths
            ],
        }
        report.line(
            f"seam, {edge.name} edge, shared with slice {edge.slice}, built as "
            + ("bevels alone" if edge.kind == "plain" else f"bevels and {edge.kind}")
            + ", each straight length from the hem up, its bevel"
            + ("" if edge.kind == "plain" else f" and its {edge.kind}")
            + ": "
            + "; ".join(
                f"length {length.row}, {length.length:.1f} mm, {length.bevel_deg:.2f}°"
                + ("" if edge.kind == "plain" else f", {length.pins}")
                for length in edge.lengths
            )
        )
    report.numbers["seam"] = numbers
    report.numbers["pins_count"] = seam.count("pins")
    report.numbers["holes_count"] = seam.count("holes")
    along, through, out = seam.pin
    if seam.count("pins") or seam.count("holes"):
        report.numbers["pin_mm"] = list(seam.pin)
        report.numbers["pin_play_mm"] = seam.play
        report.line(
            f"pins: {seam.count('pins')} pin(s) and {seam.count('holes')} hole(s). A pin is "
            f"{along:g} mm along the edge, {through:g} mm through the frame and stands "
            f"{out:g} mm out of the face, square to it, on the frame's middle; its hole is "
            f"{seam.play:g} mm larger all round and deeper. Each is in the lead line, "
            f"{WALL:g} mm or more from any pocket"
        )
    thin = min(length.wall_mm for edge in seam.edges for length in edge.lengths)
    report.fact(
        "seam_wall_mm", thin, "mm",
        f"the least lead a bevel leaves beside a pocket, at the pocket's floor: the cut's "
        f"middle is half the map's lead, {lead / 2.0:g} mm, from the glass",
    )  # fmt: skip
    report.line(
        "the seam's faces pass through each cut's middle at half the frame's thickness and "
        "lean out toward the outer face, the one the map is seen from. --border is the frame "
        "at the top and the hem only. `pieces seam A B --frames ...` places two built "
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
    kinds = sorted({row["what"] for row in seam.refused})
    only_holes = kinds == ["hole"]
    report.second(
        f"each of the {len(seam.refused)} place(s) named is read from the pieces' outlines "
        "against the cut's middle, and from each hole's own corners against the nearest piece"
    )
    report.verdict(
        "FAIL",
        f"the seams would cut a pocket or a lip at {len(seam.refused)} place(s), so nothing "
        f"was built: {seam.refused[0]['why']}"
        + (f"; and {len(seam.refused) - 1} more" if len(seam.refused) > 1 else "")
        + f". A lead of about {seam.lead_needed:g} mm in the bands file would leave room"
        + (
            ", or `--seam plain,plain` builds the bevels with no pin, or a shorter --pin"
            if only_holes
            else ", or more slices, which meet at a smaller angle, or a thinner frame"
        ),
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


def placings(surface: dict[str, Any], thickness: float) -> list[dict[str, Any]]:
    """Where each flat row of a slice goes: its stretch of the map, and the turn and the move
    that take the flat frame there to its place on the dome. A chain from the hem up, each row
    turned by the bend at its fold, as `flatten` reads a slice back; the frame's middle lies on
    the row."""
    hem = surface["hem"]
    folds = sorted(surface["folds"], key=lambda f: f["y_mm"])
    starts = [float(hem["at"][1]), *[float(f["y_mm"]) for f in folds]]
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
        move = origin - turned @ np.array([float(hem["at"][0]), start, thickness / 2.0])
        found.append(
            {
                "row": i + 1,
                "low": None if i == 0 else start,
                "high": None if i == len(starts) - 1 else starts[i + 1],
                "turn": turned,
                "move": move,
            }
        )
        if i < len(folds):
            long = starts[i + 1] - start
            out, high = out - long * math.cos(tilt), high + long * math.sin(tilt)
            tilt -= math.radians(float(folds[i]["bend_deg"]))
    return found


def _solid_of(path: str) -> Any:
    import cadquery as cq

    if not Path(path).is_file():
        raise NoAnswer(
            f"no file {path}: --frames takes each slice's frame, as `pieces build` wrote it"
        )
    solids = cq.importers.importStep(path).vals()
    found = [solid for shape in solids for solid in shape.Solids()]
    if len(found) != 1:
        raise NoAnswer(f"{path} holds {len(found)} solid(s): a slice's frame is one body")
    return found[0]


def _rows_placed(frame: Any, surface: dict[str, Any]) -> tuple[list[Any], float]:
    """A built frame cut at its folds into flat rows, each set at its place on the dome."""
    import cadquery as cq
    from OCP.gp import gp_Trsf

    box = frame.BoundingBox()
    thickness = float(box.zmax - box.zmin)
    if abs(box.zmin) > 1e-3:
        raise NoAnswer(f"the frame's lower face is at z = {box.zmin:g}, not 0, as a build's is")
    placed = []
    for row in placings(surface, thickness):
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
            key, {"normal": normal, "at": centre, "area": 0.0, "corners": [], "faces": []}
        )
        group["area"] += float(face.Area())
        group["corners"] += [np.array(v.toTuple()) for v in face.Vertices()]
        group["faces"].append(face)
    if not groups:
        return None
    return max(groups.values(), key=lambda g: float(g["area"]))


def _half_space(at: np.ndarray, normal: np.ndarray, start: float, size: float = 400.0) -> Any:
    """Everything from `start` past a plane, on its normal's side, as a large block."""
    seed = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, seed)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    return block(
        np.array(
            [
                at + i * size * u + j * size * v + k * normal
                for i in (-1.0, 1.0)
                for j in (-1.0, 1.0)
                for k in (start, size)
            ]
        )
    )


def _apart(one: Any, two: Any) -> float:
    """The nearest two shapes come, by OpenCascade's distance between shapes."""
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    found = BRepExtrema_DistShapeShape(one.wrapped, two.wrapped)
    if not found.IsDone():
        raise NoAnswer("OpenCascade couldn't read how far apart two rows are")
    return float(found.Value())


def _pins(row: Any, face: dict[str, Any], other: Any, way: np.ndarray) -> list[dict[str, Any]]:
    """Each pin of a row: a part of it that stands past its own face, with whether the other
    row is round it and how near the two come."""
    import cadquery as cq

    normal = face["normal"]
    past = row.intersect(_half_space(face["at"], normal, NUDGE))
    found = []
    for lump in past.Solids():
        if lump.Volume() < 1e-4:
            continue
        points = np.array([v.toTuple() for v in lump.Vertices()])
        along = way - float(way @ normal) * normal
        along /= np.linalg.norm(along)
        through = np.cross(normal, along)
        middle = (points.max(axis=0) + points.min(axis=0)) / 2.0
        spans = [
            ((points - middle) @ axis).max() - ((points - middle) @ axis).min()
            for axis in (along, through, normal)
        ]
        if spans[2] + NUDGE < PIN_LEAST:
            continue
        centre = middle
        probes = [
            centre + sign * (spans[k] / 2.0 + PROBE) * axis
            for k, axis in enumerate((along, through))
            for sign in (-1.0, 1.0)
        ] + [centre + (spans[2] / 2.0 + PROBE) * normal]
        round_it = sum(bool(other.isInside(cq.Vector(*(float(v) for v in p)))) for p in probes)
        shared = float(lump.intersect(other).Volume())
        inside = sum(
            bool(other.isInside(cq.Vector(*(float(v) for v in p + NUDGE * (centre - p)))))
            for p in points
        )
        found.append(
            {
                "at": [round(float(v), 3) for v in centre],
                "mm": [round(float(v), 3) for v in spans],
                "sides_held": round_it,
                "shared_mm3": shared,
                "corners_inside": inside,
                "corners": len(points),
                "play_mm": _apart(lump, other),
                "in_hole": round_it == len(probes) and shared <= SHARED,
            }
        )
    return found


def run_seam(args: Any, report: Report) -> None:
    import cadquery as cq

    if len(args.maps) != 2 or len(args.frames) != 2:
        raise NoAnswer("`pieces seam A B --frames FA FB`: two slices' maps, and each one's frame")
    gap = GAP if args.gap is None else float(args.gap)
    if not gap > 0:
        raise NoAnswer("--gap is how far two faces may stand apart and still meet, in mm, over 0")
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
    if not shares(0) or (shares(1) and int(surfaces[0]["slices"]) == 2 and numbers[0] > numbers[1]):
        surfaces, frames, numbers = surfaces[::-1], frames[::-1], numbers[::-1]
    # Slice A's right edge is slice B's left: the plane through the dome's axis and the cut.
    cut = np.array(surfaces[0]["edges"]["right"]["on_dome"], dtype=float)
    theirs = np.array(surfaces[1]["edges"]["left"]["on_dome"], dtype=float)
    cuts_off = float(np.linalg.norm(cut - theirs, axis=1).max())
    phi = math.atan2(cut[0][1], cut[0][0])
    toward = np.array([-math.sin(phi), math.cos(phi), 0.0])
    solids = [_solid_of(path) for path in frames]
    rows_a, thick_a = _rows_placed(solids[0], surfaces[0])
    rows_b, thick_b = _rows_placed(solids[1], surfaces[1])
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
    pins: list[dict[str, Any]] = []
    overlap = 0.0
    points_inside = 0
    points_asked = 0
    nearest = 0.0
    no_face = []
    for j, (one, two) in enumerate(zip(rows_a, rows_b, strict=True)):
        way = cut[j + 1] - cut[j]
        way = way / np.linalg.norm(way)
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
                "gap_mm": max(*off_a, *off_b, 0.0),
                "past_mm": max(-min(*off_a, *off_b), 0.0),
                "face_mm2": [face_a["area"], face_b["area"]],
            }
        )
        for mine, other, face in ((one, two, face_a), (two, one, face_b)):
            found = _pins(mine, face, other, way)
            for pin in found:
                pin["length"] = j + 1
                pin["of"] = numbers[0] if mine is one else numbers[1]
            pins += found
            # The second method: this face's own corners, a little inside it: in the other too?
            for p in face["corners"]:
                points_asked += 1
                at = p - NUDGE * face["normal"] + 5.0 * NUDGE * (face["at"] - p) / max(
                    float(np.linalg.norm(face["at"] - p)), 1e-9
                )
                points_inside += bool(other.isInside(cq.Vector(*(float(v) for v in at))))
        lengths.append(row)
    held = [pin for pin in pins if pin["in_hole"]]
    read = [row for row in lengths if "gap_mm" in row]
    widest = max((row["gap_mm"] for row in read), default=math.inf)
    turned = max((row["faces_apart_deg"] for row in read), default=180.0)
    report.numbers["lengths"] = lengths
    report.numbers["pins"] = pins
    report.fact(
        "faces_apart_deg", turned, "°",
        "the most the two faces that meet at a length are off parallel",
    )  # fmt: skip
    report.fact(
        "gap_mm", widest, "mm",
        "the furthest a corner of either face stands off the other's plane, at any length",
    )  # fmt: skip
    report.numbers["gap_limit_mm"] = gap
    report.fact(
        "overlap_mm3", overlap, "mm³",
        "what the two slices' rows share, by OpenCascade's `common`, length by length",
    )  # fmt: skip
    report.fact("pins_count", len(pins), how="each a part of one row that stands past its face")
    report.fact(
        "pins_in_holes", len(held),
        how=f"the other row is {PROBE:g} mm past each of the pin's four sides and its end, "
        "and shares nothing with it",
    )  # fmt: skip
    if pins:
        report.fact(
            "pin_play_mm", min(pin["play_mm"] for pin in held) if held else 0.0, "mm",
            "the nearest a pin in its hole comes to the other row",
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
    corners_in = sum(pin["corners_inside"] for pin in pins)
    report.second(
        f"points, with no boolean: of {points_asked} corner(s) of the faces that meet, each "
        f"moved {NUDGE:g} mm into its own row, {points_inside} are inside the other slice's; "
        f"of the pins' own corners, {corners_in}; and the nearest the two rows come at a "
        f"length is {nearest:.4f} mm at the furthest, by OpenCascade's distance between shapes"
    )
    report.second(
        f"the two maps' own edges: slice {numbers[0]}'s right, on the dome, is within "
        f"{cuts_off:.5f} mm of slice {numbers[1]}'s left at every fold"
    )
    differs = []
    if overlap <= SHARED and (points_inside or corners_in):
        differs.append(
            f"the rows share {overlap:.4f} mm³ by `common`, and {points_inside + corners_in} "
            "point(s) just inside one row's surface are inside the other too"
        )
    if read and not over(widest, gap) and over(nearest, gap) and not no_face:
        differs.append(
            f"the faces' corners are within {widest:.3f} mm of each other's planes, and the "
            f"rows are {nearest:.3f} mm apart by the distance between shapes"
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
    stray = len(pins) - len(held)
    if over(overlap, SHARED):
        worst = max(lengths, key=lambda r: float(r["shared_mm3"]))
        report.verdict(
            "INTERFERE",
            f"slices {numbers[0]} and {numbers[1]} share {overlap:.3f} mm³ placed, the most at "
            f"length {worst['length']}"
            + (
                f": {stray} of {len(pins)} pin(s) meet the other slice's frame, not a hole"
                if stray
                else f": the faces are {turned:.2f}° off parallel, and one passes "
                f"{max(r['past_mm'] for r in read):.3f} mm into the other"
            ),
        )
        return
    wrong = []
    if over(turned, PARALLEL_DEG) or over(widest, gap):
        wrong.append(
            f"the faces stand {widest:.3f} mm apart at the furthest and {turned:.2f}° off "
            f"parallel, over the gap, {gap:g} mm"
        )
    if stray:
        wrong.append(f"{stray} of {len(pins)} pin(s) aren't in a hole")
    if wrong:
        report.verdict("FAIL", "; ".join(wrong))
        return
    report.verdict(
        "OK",
        f"slices {numbers[0]} and {numbers[1]}, bent by their folds and placed, meet face to "
        f"face at all {len(lengths)} length(s) within {widest:.4f} mm, under the gap, "
        f"{gap:g} mm, and share nothing; "
        + (
            f"all {len(pins)} pin(s) are in their holes"
            if pins
            else "neither has a pin: the bevels alone"
        ),
    )
