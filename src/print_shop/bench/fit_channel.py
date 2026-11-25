"""`fit-bed`'s reading of a through-bolt in a drop-in channel (mqucifer/print-shop#302).

A drop-in channel is a U with a round bottom, open on one side, running across the join: the
bolt drops in from the open side, its head bears on one part's wall and its nut on the other's,
and it clamps the two together. A through joint declares one in the assembly file (`channel`),
and nothing undeclared is read as one. For each bolt, in each of the two parts, read from that
part's own faces where the assembly puts it (`through.channel`):

- **the round** against the bolt's shank: its diameter and the play a side;
- **each wall** the joint names, the head's in the first part and the nut's in the second: where
  the channel ends, square to the bolt, with the washer under the head or the nut on it. It
  bears on the ring from the round, or the washer's own hole where that's wider, to the washer's
  outside, less the channel's cut, by arithmetic; and the part's flat faces in that plane, cut
  to the ring, by OpenCascade, must hold that, or the washer overhangs the wall. Second method:
  the fine mesh's triangles in that plane, cut to the ring with Shapely;
- **the open side clear** for the bolt to drop in: nothing of the part within the shank's width
  of the axis, from the axis out through the open side, along the channel's stretch. By the
  exact solid cut to that box, and again by the fine mesh's triangles clipped to it;
- **facing up** as the part is laid on the bed, so its round prints as a floor and its walls
  straight up: laid any other way, its round or its walls overhang, and it needs support. Where
  a part could lie either way up, the side with every channel up is taken, as long as it fits.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from . import through
from .assembly import AXES, DIAMETER, Assembly, Joint
from .report import NoAnswer, Report

#: A channel faces up where its open way is the way up within this.
UP = 1e-6
#: The shank's way in is read this far inside its own width and the channel's ends, in mm, so
#: a wall exactly at the shank's width, or the next part's face at the join, isn't in its way.
INSIDE = through.SLIP
#: Something of the part is in the bolt's way past this much, by volume in mm³, or by area of
#: surface in mm².
BLOCKS_MM3, BLOCKS_MM2 = 1e-6, 1e-6
#: A washer sits wholly on its wall where this share of its seat does, as `load` holds it.
SEATED = 0.999
#: How many straight runs a quarter of a ring's circle is drawn with, to cut the mesh's faces.
QUARTER = 512


def _way(side: str) -> np.ndarray:
    """`+z` as a unit vector."""
    unit = np.zeros(3)
    unit[AXES.index(side[1])] = 1.0 if side[0] == "+" else -1.0
    return unit


def up_of(assembly: Assembly, name: str, side: str) -> np.ndarray:
    """The way up, in the assembly, of a part laid on `side` of its own file, such as `-z`."""
    return np.asarray(assembly.part(name).matrix() @ -_way(side))


def _said(vector: Any) -> str:
    return ", ".join(f"{round(float(v), 6) + 0.0:g}" for v in vector)


def prefers(assembly: Assembly) -> dict[str, Callable[[str], bool]]:
    """For each part a declared channel runs through, whether a side it's laid on has every
    such channel facing up."""
    opens: dict[str, list[np.ndarray]] = {}
    for joint in assembly.joints:
        if joint.kind == "through" and joint.channel is not None:
            for name in joint.between:
                opens.setdefault(name, []).append(joint.channel.open_way())

    def judge(name: str) -> Callable[[str], bool]:
        def up(side: str) -> bool:
            if len(side) != 2 or side[1] not in AXES:
                return False
            way = up_of(assembly, name, side)
            return all(float(o @ way) > 1 - UP for o in opens[name])

        return up

    return {name: judge(name) for name in opens}


def read_channels(
    assembly: Assembly,
    faces_of: Callable[[str], dict[str, Any]],
    laid: dict[str, str | None],
    report: Report,
    fails: list[str],
    apart: list[str],
) -> int:
    """Each declared channel's bolts, read in both parts; how many bolts were read."""
    saved: dict[str, Any] = {}
    for joint in assembly.joints:
        if joint.kind != "through" or joint.channel is None:
            continue
        saved[joint.name] = [
            _bolt(assembly, joint, point, [faces_of(n) for n in joint.between], laid, report,
                  fails, apart)
            for point in joint.at
        ]  # fmt: skip
    if saved:
        report.numbers["channels"] = saved
    return sum(len(b) for b in saved.values())


def _bolt(
    assembly: Assembly,
    joint: Joint,
    point: tuple[float, float, float],
    faces: list[dict[str, Any]],
    laid: dict[str, str | None],
    report: Report,
    fails: list[str],
    apart: list[str],
) -> dict[str, Any]:
    assert joint.channel is not None and joint.bolt is not None and joint.washer is not None
    spot = "(" + ", ".join(f"{v:g}" for v in point) + ")"
    what = f"the channel of the through joint {joint.name}'s bolt at {spot}"
    holes = []
    for name, found in zip(joint.between, faces, strict=True):
        hole = through.seat_of(joint, found["cylinders"], point, found["planes"])
        if isinstance(hole, str):
            raise NoAnswer(f"the through joint {joint.name}'s bolt at {spot}: in {name}, {hole}")
        holes.append(hole)
    way = through.meet(holes[0], holes[1])
    if isinstance(way, str):
        raise NoAnswer(f"the through joint {joint.name}'s bolt at {spot}: {way}")
    shank = DIAMETER[joint.bolt.thread]
    wrong = through.walls_met(joint, way)
    misplaced = {w.split(":")[0] for w in wrong}
    one: dict[str, Any] = {
        "at": list(point),
        "bolt": joint.bolt.name,
        "shank_mm": shank,
        "washer": joint.washer.name,
        "ends": [],
    }
    for k, (name, found, hole) in enumerate(zip(joint.between, faces, holes, strict=True)):
        wall = "nut" if k else "head"
        one["ends"].append(
            _end(assembly, joint, wall, name, found, hole, way, k, shank, misplaced, laid,
                 report, wrong, apart, what)
        )  # fmt: skip
    one["drops_in"] = all(e["drops_in"] for e in one["ends"])
    one["fits"] = not wrong
    if wrong:
        fails.append(f"{what}: " + "; and ".join(wrong))
    return one


def _end(
    assembly: Assembly,
    joint: Joint,
    wall: str,
    name: str,
    found: dict[str, Any],
    hole: dict[str, Any],
    way: dict[str, Any],
    k: int,
    shank: float,
    misplaced: set[str],
    laid: dict[str, str | None],
    report: Report,
    wrong: list[str],
    apart: list[str],
    what: str,
) -> dict[str, Any]:
    """One part's stretch of the channel: its round, its wall, its open side and which way up
    it's printed."""
    assert joint.channel is not None and joint.washer is not None
    gap, radius = hole["channel"], hole["radius"]
    said = joint.channel.wall_said(wall)
    at = f"{what}, in {name}"
    end: dict[str, Any] = {
        "part": name,
        "wall": wall,
        "plane": said.split(" at ")[1],
        "round_mm": 2 * radius,
        "play_mm": radius - shank / 2,
        **through.channel_saved(gap),
        "length_mm": way["lengths"][k],
    }
    report.line(
        f"{at}: {through.channel_said(hole)}, {way['lengths'][k]:.2f} mm long, round the bolt "
        f"{joint.bolt.name if joint.bolt else ''}'s {shank:g} mm: "
        + (
            f"a play of {end['play_mm']:.3f} mm a side"
            if end["play_mm"] >= -through.SAME
            else "narrower than it"
        )
    )
    if end["play_mm"] < -through.SAME:
        wrong.append(
            f"in {name}, its round is {2 * radius:.3f} mm across, narrower than the bolt's "
            f"{shank:g} mm: the bolt doesn't lie in it"
        )
    if gap["open_mm"] < shank - through.SAME:
        wrong.append(
            f"in {name}, it's open {gap['open_mm']:.3f} mm wide where its round leaves off, "
            f"narrower than the bolt's {shank:g} mm: the bolt doesn't drop in"
        )
    # The wall it bears on.
    axis = way["axis"]
    out = -axis if k == 0 else axis
    origin = np.asarray(hole["origin"], dtype=float)
    centre = origin + (way["seats"][k] - float(origin @ axis)) * axis
    inner = max(radius, joint.washer.hole_mm / 2)
    outer = joint.washer.outer_mm / 2
    under = f"the washer {joint.washer.name} under the {wall}"
    if said in misplaced:
        end.update(seated=False)
        report.line(f"{at}: {said} isn't where the channel ends, so nothing bears on it there")
    elif inner >= outer - through.SAME:
        end.update(seated=False)
        wrong.append(f"in {name}, {under} is {2 * outer:g} mm across: it has nothing to bear on")
    else:
        ring = math.pi * (outer**2 - inner**2)
        seat = ring - through.channel_cut(hole, inner, outer)
        exact = _seat_exact(found, centre, out, inner, outer)
        by_mesh = _seat_by_mesh(found["triangles"], centre, out, inner, outer)
        seated = exact >= SEATED * seat
        end.update(ring_mm2=ring, seat_mm2=seat, bears_mm2=exact, bears_by_mesh_mm2=by_mesh,
                   seated=seated)  # fmt: skip
        report.line(
            f"{at}: {under} bears on {exact:.2f} mm² of {said}, by the part's own faces, where "
            f"its ring's {ring:.2f} mm² less the channel's cut is {seat:.2f}"
        )
        report.second(
            f"{at}: {said} under {under}, by the fine mesh's triangles in its plane cut to the "
            f"ring: {by_mesh:.3f} mm², where its faces give {exact:.3f}"
        )
        if exact * SEATED > seat:
            apart.append(
                f"{at}: {under} bears on {exact:.3f} mm² by the part's faces, more than the "
                f"{seat:.3f} mm² its ring has less the channel's cut"
            )
        elif (by_mesh >= SEATED * seat) != seated:
            apart.append(
                f"{at}: {under} sits on {exact:.3f} mm² of {said} by its faces and "
                f"{by_mesh:.3f} by the mesh, either side of the {seat:.3f} mm² its seat is"
            )
        elif not seated:
            wrong.append(
                f"in {name}, {under} sits on {exact:.1f} mm² of {said}, of its ring's "
                f"{ring:.1f} mm², and {seat:.1f} mm² less the channel's cut: the wall is too "
                "narrow for it, or isn't flat and square to the bolt there. Make the wall "
                "wider round the channel, or move the bolt"
            )
    # The open side, clear for the bolt to drop in.
    over, over_mesh = _blocked(found, hole, gap, shank / 2)
    end.update(over_mm3=over, over_by_mesh_mm2=over_mesh, drops_in=over <= BLOCKS_MM3)
    report.line(
        f"{at}: from the axis out through its open side, within the bolt's {shank:g} mm, "
        + (
            "nothing of the part is in its way: it drops in"
            if over <= BLOCKS_MM3
            else f"{over:.3f} mm³ of the part is in its way"
        )
    )
    report.second(
        f"{at}: the fine mesh's triangles in the bolt's way in: {over_mesh:.4f} mm² of surface"
    )
    if (over > BLOCKS_MM3) != (over_mesh > BLOCKS_MM2):
        apart.append(
            f"{at}: {over:.4f} mm³ of the part is in the bolt's way in by its exact solid, and "
            f"{over_mesh:.4f} mm² of its surface by the mesh"
        )
    elif over > BLOCKS_MM3:
        wrong.append(
            f"in {name}, {over:.3f} mm³ of the part is over the channel within the bolt's "
            f"{shank:g} mm, between the axis and the open side: the bolt doesn't drop in"
        )
    # Which way up it's printed.
    side = laid.get(name)
    if side is None:
        end.update(side=None, faces_up=False)
        report.line(f"{at}: {name} has no side to lie on, so which way it faces isn't read")
        return end
    up = up_of(assembly, name, side)
    faces = float(gap["toward"] @ up) > 1 - UP
    end.update(side=side, faces_up=faces)
    own = _said(_in_own(assembly, name, gap["toward"]))
    report.line(
        f"{at}: open toward ({own}) in {name}'s own file, and {name} is laid on its {side} "
        "face: "
        + (
            "up, so it prints without supports"
            if faces
            else f"not up, which is ({_said(-_way(side))})"
        )
    )
    if not faces:
        wrong.append(
            f"in {name}, laid on its {side} face, its open side is toward ({own}) of {name}'s "
            f"own file, where up is ({_said(-_way(side))}): it needs support. A channel prints "
            "without supports only with its open side up"
        )
    return end


def _in_own(assembly: Assembly, name: str, toward: Any) -> np.ndarray:
    """A way in the assembly as it is in the part's own file, which is how it's laid."""
    return np.asarray(assembly.part(name).matrix().T @ np.asarray(toward, dtype=float))


def _own(found: dict[str, Any], point: Any) -> np.ndarray:
    """A point where the assembly puts it, in the part's own file."""
    return np.asarray(found["turn"].T @ (np.asarray(point, dtype=float) - found["move"]))


def _seat_exact(
    found: dict[str, Any], centre: np.ndarray, out: np.ndarray, inner: float, outer: float
) -> float:
    """The part's flat faces at the wall, facing `out`, cut to the ring round the axis, by
    OpenCascade: the area a washer bears on."""
    import cadquery as cq

    at, normal = _own(found, centre), found["turn"].T @ out
    middle, way = cq.Vector(*at), cq.Vector(*normal)
    ring = cq.Face.makeFromWires(
        cq.Wire.makeCircle(outer, middle, way), [cq.Wire.makeCircle(inner, middle, way)]
    )
    total = 0.0
    for face in found["part"].shape.Faces():
        if face.geomType() != "PLANE":
            continue
        facing = np.asarray(face.normalAt().toTuple(), dtype=float)
        if float(facing @ normal) < 1 - through.SQUARE:
            continue
        if abs(float((np.asarray(face.Center().toTuple()) - at) @ normal)) > 1e-3:
            continue
        total += float(ring.intersect(face).Area())
    return total


def _seat_by_mesh(
    triangles: np.ndarray, centre: np.ndarray, out: np.ndarray, inner: float, outer: float
) -> float:
    """The fine mesh's triangles in the wall's plane, facing `out`, cut to the ring by Shapely."""
    edges = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    size = np.linalg.norm(edges, axis=1)
    facing = (edges @ out) / np.where(size > 0, size, 1.0)
    level = np.abs((triangles - centre) @ out).max(axis=1)
    kept = triangles[(size > 0) & (facing > 1 - through.SQUARE) & (level <= 1e-3)]
    if not len(kept):
        return 0.0
    first, second = through.basis(out)
    flat = np.stack([(kept - centre) @ first, (kept - centre) @ second], axis=2)
    face = unary_union([Polygon(t) for t in flat])
    ring = Point(0, 0).buffer(outer, quad_segs=QUARTER).difference(
        Point(0, 0).buffer(inner, quad_segs=QUARTER)
    )
    return float(face.intersection(ring).area)


def _box(
    found: dict[str, Any], hole: dict[str, Any], gap: dict[str, Any], half: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, tuple[float, float, float]]:
    """The bolt's way in, from the axis out through the open side: its corner on the axis at the
    stretch's low end, the axis, the way across and the way out, and how far it reaches each."""
    axis, toward = np.asarray(hole["axis"], dtype=float), np.asarray(gap["toward"], dtype=float)
    across = np.cross(axis, toward)
    origin = np.asarray(hole["origin"], dtype=float)
    start = origin + (hole["low"] + INSIDE - float(origin @ axis)) * axis
    points = np.asarray(found["triangles"], dtype=float).reshape(-1, 3)
    far = float(((points - origin) @ toward).max()) + 1.0
    length = hole["high"] - hole["low"] - 2 * INSIDE
    return start, axis, across, toward, (length, half - INSIDE, far)


def _blocked(
    found: dict[str, Any], hole: dict[str, Any], gap: dict[str, Any], half: float
) -> tuple[float, float]:
    """What of the part is in the bolt's way in: by volume, the exact solid cut to it by
    OpenCascade; and by area, the fine mesh's triangles clipped to it."""
    import cadquery as cq

    start, axis, across, toward, (length, wide, far) = _box(found, hole, gap, half)
    corners = [start + s * wide * across + u * toward for s, u in
               ((-1, INSIDE), (1, INSIDE), (1, far), (-1, far))]  # fmt: skip
    outline = cq.Wire.makePolygon([cq.Vector(*_own(found, c)) for c in corners], close=True)
    box = cq.Solid.extrudeLinear(
        cq.Face.makeFromWires(outline), cq.Vector(*(found["turn"].T @ (length * axis)))
    )
    exact = float(found["part"].shape.intersect(box).Volume())
    # The mesh: each triangle in the box's own axes, clipped to it.
    local = np.stack(
        [
            (found["triangles"] - start) @ axis,
            (found["triangles"] - start) @ across,
            (found["triangles"] - start) @ toward,
        ],
        axis=2,
    )
    low = np.array([0.0, -wide, INSIDE])
    high = np.array([length, wide, far])
    near = ((local.max(axis=1) > low) & (local.min(axis=1) < high)).all(axis=1)
    area = 0.0
    for triangle in local[near]:
        kept = triangle
        for k in range(3):
            for sign, limit in ((1.0, high[k]), (-1.0, -low[k])):
                kept = _cut(kept, k, sign, limit)
                if len(kept) < 3:
                    break
            if len(kept) < 3:
                break
        if len(kept) >= 3:
            area += sum(
                0.5 * float(np.linalg.norm(np.cross(b - kept[0], c - kept[0])))
                for b, c in zip(kept[1:-1], kept[2:], strict=True)
            )
    return exact, area


def _cut(polygon: np.ndarray, k: int, sign: float, limit: float) -> np.ndarray:
    """What of a flat convex polygon lies where `sign` times its `k`th coordinate is at most
    `limit`."""
    reach = sign * polygon[:, k] - limit
    kept: list[np.ndarray] = []
    for i in range(len(polygon)):
        here, there = polygon[i], polygon[(i + 1) % len(polygon)]
        a, b = reach[i], reach[(i + 1) % len(polygon)]
        if a <= 0:
            kept.append(here)
        if (a < 0 < b) or (b < 0 < a):
            kept.append(here + a / (a - b) * (there - here))
    return np.array(kept, dtype=float).reshape(-1, 3)
