"""`pieces build`: a piece map built into a frame and a pane file for each colour, every curve
built as a curve (mqucifer/print-shop#153).

**The parts.** Each piece's pane is its outline inset by the play, less what a nozzle can't
print: a tip or a spur under `NOZZLE` wide, and its corners rounded by half a nozzle. Its
pocket is that pane and the play round it, so the pocket's wall is the piece's outline but at
its tips and corners, and a pane fills its pocket to the play everywhere. The opening over and
under it is the pocket inset by the lip, so the lead a person sees is the map's line and a lip
either side of it. The reply says how much glass the tips and corners were:

- `frame.step`: the panel and a border round it, the frame's thickness tall, with each
  opening cut through and each pocket cut between the two lips. One connected body.
- `pane_COLOUR.step`: every pane of one colour, each its own solid, where it sits in its pocket.

**The heights** stand on layer lines. The pocket is in the middle of the frame: its floor on
the layer line at or under half of what the pane leaves, and its top on the first layer line
that leaves the pane `ROOM` or more. The pause is at the pocket's top, as `pause` reads it.

**The lip** is `--lip`'s FULL on every piece whose opening is still one piece with it, and
otherwise the widest, by tenths of a mm down to LEAST, that leaves one. A piece that has no
opening even at LEAST, or no pane once the play is taken, is named, and nothing is built:
it's a sliver or a neck, which `pieces merge` or `pieces split` fixes in the map.

**Curves** (the Sponsor, 2026-11-05: "Build it right the first time with right tools"). A
map's outline is points. A turn sharper than `CORNER_DEG` is a corner, and stays one. Between
corners a run of points is a straight line where they lie on one within `--fit`, and otherwise
arcs: each pair of arcs (a biarc) leaves one point along the points' own direction and
arrives at another along its, spanning as many points as stay within `--fit` of it, and the
sides between them within twice that. So a
curve is a few arcs that meet without a kink, a file's faces are planes and cylinders, and
none is a strip. The pane is Shapely's inset of those curves by the play, rounded, fitted
again: the inset of an arc is an arc, so a few arcs take it within `CLOSE` mm. The pocket is
the pane's own arcs moved out by the play (`offset_ring`): a line moves over, an arc keeps
its centre, and two sides at a corner run on or are cut back to where they meet. So each arc
of the pocket has its twin in the pane, and the play is exact. Where the arcs moved out aren't
the pane and the play round it, the pocket is fitted within `CLOSE` instead, and the reply
names the piece. (OpenCascade's own `offset2D` failed on 11 of 36 outlines of a traced test
scene, so it isn't used.)

**Second method.** Each solid's volume by OpenCascade against its outline's area in Shapely
times its height; the built curves' distance from the map's outline, at points along them; and
each pane's distance from its pocket's wall, at points round it.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry.polygon import orient

from . import piecemap
from .limits import under
from .piecemap import Map, Piece
from .report import NoAnswer, Report

#: A turn sharper than this between two sides, in degrees, is a corner, not a curve.
CORNER_DEG = 30.0
#: The least air over a pane at the pause, in mm: the pocket's top is the first layer line
#: that leaves it.
ROOM = 0.1
#: A lip, top or bottom, is at least this thick, in mm: two layers.
LEAST_LIP_THICK = 0.4
#: The narrowest an opening or a pane may be at its widest and still be one, in mm: two
#: nozzle widths.
LEAST_ACROSS = 0.8
#: The nozzle's width, in mm: a pane has nothing narrower, and its corners are rounded by half.
NOZZLE = 0.4
#: A rounded corner is drawn in Shapely with this many points a quarter turn, then fitted.
ROUND_SEGS = 32
#: The lip steps down by this, in mm, where a piece has no opening at the full one.
LIP_STEP = 0.1
#: A built curve is drawn as points this close to it, in mm, where Shapely insets it.
DENSE = 0.002
#: How far a solid's volume may be from its outline's area times its height.
VOLUME_OFF = 0.005
#: A point's direction along a curve is read from the points this far before and after it, mm.
REACH = 1.5
#: The same for points drawn from curves, as Shapely's inset of built arcs is: they lie on it.
NEAR = 0.06
#: Arcs fitted to such points keep within this of them, in mm.
CLOSE = 0.003
#: A biarc's arc turns through no more than this, in degrees.
MOST_SWEEP = 150.0


# Curves from points


def _unit(v: np.ndarray) -> np.ndarray:
    size = float(np.hypot(v[0], v[1]))
    return v / size if size > 0 else v


def _turn(a: np.ndarray, b: np.ndarray) -> float:
    """The angle from one direction to the next, in degrees, signed."""
    return math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))


class Arc:
    """An arc from `start` to `end`, leaving along `leave`."""

    def __init__(self, start: np.ndarray, leave: np.ndarray, end: np.ndarray) -> None:
        self.start, self.end = start, end
        chord = end - start
        normal = np.array([-leave[1], leave[0]])
        across = float(chord @ normal)
        length = float(chord @ chord)
        self.straight = abs(across) < 1e-9 * max(length, 1e-12) ** 0.5 or length < 1e-18
        if self.straight:
            self.radius, self.centre, self.sweep = math.inf, start, 0.0
            self.arrive = _unit(chord) if length > 0 else leave
            return
        # Signed: over 0 turns left.
        self.radius = length / (2.0 * across)
        self.centre = start + self.radius * normal
        a0 = math.atan2(start[1] - self.centre[1], start[0] - self.centre[0])
        a1 = math.atan2(end[1] - self.centre[1], end[0] - self.centre[0])
        sweep = a1 - a0
        if self.radius > 0:
            sweep %= 2.0 * math.pi
        else:
            sweep = -((-sweep) % (2.0 * math.pi))
        self.sweep, self.a0 = sweep, a0
        tangent = np.array([-math.sin(a0 + sweep), math.cos(a0 + sweep)])
        self.arrive = tangent if self.radius > 0 else -tangent

    def at(self, share: float) -> np.ndarray:
        if self.straight:
            return np.asarray(self.start + share * (self.end - self.start))
        angle = self.a0 + share * self.sweep
        return self.centre + abs(self.radius) * np.array([math.cos(angle), math.sin(angle)])

    def off(self, points: np.ndarray) -> np.ndarray:
        """How far each point is from the arc."""
        if self.straight:
            return np.asarray(
                shapely.distance(shapely.points(points), shapely.LineString([self.start, self.end]))
            )
        rel = points - self.centre
        angle = np.arctan2(rel[:, 1], rel[:, 0]) - self.a0
        if self.radius > 0:
            within = (angle % (2.0 * math.pi)) <= self.sweep
        else:
            within = ((-angle) % (2.0 * math.pi)) <= -self.sweep
        on = np.abs(np.hypot(rel[:, 0], rel[:, 1]) - abs(self.radius))
        ends = np.minimum(np.hypot(*(points - self.start).T), np.hypot(*(points - self.end).T))
        return np.asarray(np.where(within, on, ends))


def biarc(p0: np.ndarray, t0: np.ndarray, p1: np.ndarray, t1: np.ndarray) -> list[Arc] | None:
    """Two arcs from p0, leaving along t0, to p1, arriving along t1, that meet without a kink:
    the one whose two arcs have the same length of tangent. None where there isn't one."""
    v = p1 - p0
    t = t0 + t1
    a = 2.0 * (1.0 - float(t0 @ t1))
    vt, vv = float(v @ t), float(v @ v)
    if vv < 1e-18:
        return None
    if a < 1e-12:
        if abs(vt) < 1e-12:
            return None
        d = vv / (2.0 * vt)
    else:
        d = (-vt + math.sqrt(vt * vt + a * vv)) / a
    if not d > 1e-9:
        return None
    middle = (p0 + d * t0 + p1 - d * t1) / 2.0
    first = Arc(p0, t0, middle)
    second = Arc(middle, first.arrive, p1)
    most = math.radians(MOST_SWEEP)
    if abs(first.sweep) > most or abs(second.sweep) > most:
        return None
    if float(second.arrive @ t1) < math.cos(math.radians(2.0)):
        return None
    return [first, second]


def _tangents(run: np.ndarray, closed: bool, span: float) -> np.ndarray:
    """Each point's direction along a run: that of the circle through it and the run's points
    `REACH` before and after it along the run, so a traced outline's pixel steps don't turn
    it. At an open run's ends, the circle through the end and the points half and all of
    `REACH` on."""
    count = len(run)
    path = np.vstack([run, run[:1]]) if closed else run
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(path, axis=0).T))])
    total = float(along[-1])
    reach = min(span, total / 6.0)

    def at(where: float) -> np.ndarray:
        where = where % total if closed else min(max(where, 0.0), total)
        return np.array([np.interp(where, along, path[:, 0]), np.interp(where, along, path[:, 1])])

    def through(before: np.ndarray, here: np.ndarray, after: np.ndarray) -> np.ndarray:
        u, w = here - before, after - here
        lu, lw = float(np.hypot(*u)), float(np.hypot(*w))
        if lu <= 0 or lw <= 0:
            return _unit(after - before)
        return _unit(u * (lw / lu) + w * (lu / lw))

    found = np.zeros_like(run)
    for k in range(count):
        s = float(along[k])
        if closed or reach <= s <= total - reach:
            found[k] = through(at(s - reach), run[k], at(s + reach))
            continue
        # Near an end: the circle through this point and two further in, read at this point.
        sign = 1.0 if s < reach else -1.0
        middle, far = at(s + sign * reach / 2.0), at(s + sign * reach)
        inner = through(run[k], middle, far)
        chord = _unit(middle - run[k])
        found[k] = sign * _unit(2.0 * float(inner @ chord) * chord - inner)
        if sign < 0:
            found[k] = _unit(2.0 * float(inner @ chord) * chord - inner) * -1.0
    return found


def _samples(run: np.ndarray, start: int, stop: int, step: float) -> np.ndarray:
    """Points along a run's sides from one of its points to another: each point, and the sides
    between at `step` apart."""
    found = []
    for k in range(start, stop):
        a, b = run[k % len(run)], run[(k + 1) % len(run)]
        n = max(1, int(math.ceil(float(np.hypot(*(b - a))) / step)))
        found.append(a + np.linspace(0.0, 1.0, n, endpoint=False)[:, None] * (b - a))
    found.append(run[stop % len(run)][None, :])
    return np.concatenate(found)


def _fit_run(run: np.ndarray, closed: bool, fit: float, span: float) -> list[Arc]:
    """A run of points as lines and arcs, within `fit` of its points and twice that of the
    sides between them: from each point, the biarc to the furthest point on that still fits."""
    count = len(run)
    last = count if closed else count - 1
    if not closed:
        whole = shapely.LineString([run[0], run[-1]])
        if float(shapely.distance(shapely.points(run), whole).max()) <= fit:
            return [Arc(run[0], _unit(run[-1] - run[0]), run[-1])]
    tangents = _tangents(run, closed, span)
    step = min(max(fit * 4.0, 0.02), 0.2)

    def end_way(k: int, j: int) -> np.ndarray:
        """The direction at point k of the circle through it and two points on toward j."""
        hop = j - k
        a, b, c = run[k % count], run[(k + hop // 4) % count], run[(k + hop // 2) % count]
        u, w = b - a, c - b
        lu, lw = float(np.hypot(*u)), float(np.hypot(*w))
        if lu <= 0 or lw <= 0:
            return _unit(c - a)
        middle = _unit(u * (lw / lu) + w * (lu / lw))
        chord = u / lu
        return _unit(2.0 * float(middle @ chord) * chord - middle)

    def fits(k: int, j: int) -> list[Arc] | None:
        if span <= NEAR and j - k >= 4:
            # Points that lie on curves: each end's direction from the span's own points, so
            # a span that is one arc is fitted by that arc, however long.
            pair = biarc(run[k], end_way(k, j), run[j % count], -end_way(j, k))
        else:
            pair = biarc(run[k], tangents[k], run[j % count], tangents[j % count])
        if pair is None:
            return None
        # Within `fit` of the run's points, and twice that of the sides between them: a side
        # is a chord of the curve its points stand for, and lies inside it.
        own = np.array([run[n % count] for n in range(k, j + 1)])
        if float(np.minimum(pair[0].off(own), pair[1].off(own)).max()) > fit:
            return None
        points = _samples(run, k, j, step)
        off = np.minimum(pair[0].off(points), pair[1].off(points))
        return pair if float(off.max()) <= 2.0 * fit else None

    found: list[Arc] = []
    k = 0
    while k < last:
        most = min(last, k + count - 1) if closed else last
        best: list[Arc] | None = None
        reach = k
        # Further and further on, doubling, then back between the last that fit and the first
        # that didn't.
        hop, low, high = 1, k, most + 1
        while k + hop <= most:
            pair = fits(k, k + hop)
            if pair is None:
                high = k + hop
                break
            best, reach, low = pair, k + hop, k + hop
            hop *= 2
        else:
            pair = fits(k, most) if most > low else None
            if pair is not None:
                best, reach, low = pair, most, most
            high = most
        while high - low > 1:
            middle = (low + high) // 2
            pair = fits(k, middle)
            if pair is None:
                high = middle
            else:
                best, reach, low = pair, middle, middle
        if best is None:
            # No arcs fit even one side: it stays a straight side.
            a, b = run[k], run[(k + 1) % count]
            found.append(Arc(a, _unit(b - a), b))
            k += 1
        else:
            found += best
            k = reach
    return found


def fit_ring(ring: np.ndarray, fit: float, span: float = REACH) -> list[Arc]:
    """A closed ring of points as lines and arcs: corners kept, and each run between them
    fitted. The ring has no closing point. `span` is how far each side of a point its
    direction is read over: `REACH` for a traced outline, whose points wander, and `NEAR` for
    points drawn from curves, which don't."""
    count = len(ring)
    turns = [
        abs(_turn(ring[k] - ring[k - 1], ring[(k + 1) % count] - ring[k])) for k in range(count)
    ]
    corners = [k for k in range(count) if turns[k] > CORNER_DEG]
    if not corners:
        return _fit_run(ring, True, fit, span)
    found: list[Arc] = []
    for n, start in enumerate(corners):
        stop = corners[(n + 1) % len(corners)]
        stop = stop if stop > start else stop + count
        run = np.array([ring[k % count] for k in range(start, stop + 1)])
        found += _fit_run(run, False, fit, span)
    return found


def sampled(arcs: list[Arc], step: float = 0.05) -> np.ndarray:
    """Points along a ring of arcs, no further apart than `step` off the curve."""
    found = []
    for arc in arcs:
        if arc.straight:
            found.append(arc.start[None, :])
            continue
        n = max(
            2,
            int(
                math.ceil(
                    abs(arc.sweep)
                    / (2.0 * math.acos(max(0.0, 1.0 - step / max(abs(arc.radius), step))))
                )
            ),
        )
        found.append(
            np.array([arc.at(share) for share in np.linspace(0.0, 1.0, n, endpoint=False)])
        )
    return np.concatenate(found)


def _points(ring: Any) -> np.ndarray:
    return np.asarray(ring.coords)[:-1]


# The plan, in 2D


def heights(thickness: float, pane: float, layer: float) -> dict[str, float]:
    """The pocket's floor and top, on layer lines, and what's left for each lip."""
    if not (thickness > 0 and pane > 0 and layer > 0):
        raise NoAnswer("--thickness, --pane and --layer are each over 0, in mm")
    floor = math.floor(round((thickness - pane - ROOM) / 2.0 / layer, 6)) * layer
    top = math.ceil(round((floor + pane + ROOM) / layer, 6)) * layer
    floor, top = round(floor, 6), round(top, 6)
    if floor < LEAST_LIP_THICK - 1e-9 or thickness - top < LEAST_LIP_THICK - 1e-9:
        need = pane + ROOM + 2.0 * LEAST_LIP_THICK
        raise NoAnswer(
            f"a frame {thickness:g} mm thick has no room for a {pane:g} mm pane with a lip of "
            f"{LEAST_LIP_THICK:g} mm or more on both faces: the lower lip would be {floor:g} mm "
            f"and the upper {thickness - top:g} mm. Give --thickness "
            f"{math.ceil(round(need / layer, 6)) * layer:g} or more, or a thinner --pane"
        )
    return {
        "pocket_floor_z": floor,
        "pocket_top_z": top,
        "pane_top_z": round(floor + pane, 6),
        "room_over_pane_mm": round(top - floor - pane, 6),
        "lower_lip_mm": floor,
        "upper_lip_mm": round(thickness - top, 6),
        "pocket_z": round(floor + pane / 2.0, 6),
    }


def _inset(shape: Any, by: float) -> list[Any]:
    return piecemap.polygons(shape.buffer(-by, join_style="mitre", mitre_limit=5.0))


def _one_wide(shape: Any, by: float) -> Any:
    """What an inset leaves of a shape, where that's one piece at least `LEAST_ACROSS` wide:
    a crumb narrower than that, off a spur, is left out."""
    parts = [p for p in _inset(shape, by) if not under(piecemap.widest(p, 0.02)[0], LEAST_ACROSS)]
    return parts[0] if len(parts) == 1 else None


def pane_of(shape: Any, play: float) -> Any:
    """A piece's pane in 2D: the piece inset by the play, less whatever of it a nozzle can't
    print, a tip or a spur under `NOZZLE` wide, with its corners rounded by half a nozzle as
    the nozzle rounds them. None where that isn't one pane `LEAST_ACROSS` wide."""
    inset = shape.buffer(-play, join_style="mitre", mitre_limit=5.0)
    opened = inset.buffer(-NOZZLE / 2.0, quad_segs=ROUND_SEGS).buffer(
        NOZZLE / 2.0, quad_segs=ROUND_SEGS
    )
    parts = [
        p for p in piecemap.polygons(opened) if not under(piecemap.widest(p, 0.02)[0], LEAST_ACROSS)
    ]
    return parts[0] if len(parts) == 1 else None


def plan(
    found: Map, lips: tuple[float, float], play: float
) -> tuple[list[dict[str, Any]], list[str]]:
    """Each piece's pane, pocket and opening in 2D, with the lip it takes, and what's wrong
    with any that can't be built. The pocket is the pane and the play round it, so a pane
    fills its pocket to the play everywhere, and what a pane can't fill stays lead."""
    full, least = lips
    rows, wrong = [], []
    for piece in sorted(found.pieces, key=lambda p: p.id):
        where = piecemap.said_at(shapely.get_coordinates(piece.shape.representative_point())[0])
        inside = [
            o.id
            for ring in piece.shape.interiors
            for o in found.pieces
            if o.id != piece.id and o.shape.intersects(shapely.Polygon(ring))
        ]
        if inside:
            wrong.append(
                f"piece {piece.id} surrounds piece {piecemap.numbered(inside)}: the lead round "
                "the inner piece would be a body of its own, joined to nothing. `pieces split` "
                f"piece {piece.id} from that lead to its outer edge"
            )
            continue
        pane = pane_of(piece.shape, play)
        if pane is None:
            wrong.append(
                f"piece {piece.id} has no pane once {play:g} mm of play is taken all round: "
                f"it's {piecemap.widest(piece.shape)[0]:.2f} mm at its widest, round {where}"
            )
            continue
        pocket = pane.buffer(play, quad_segs=ROUND_SEGS)
        lip = full
        opening = _one_wide(pocket, lip)
        while opening is None and lip - LIP_STEP >= least - 1e-9:
            lip = round(lip - LIP_STEP, 6)
            opening = _one_wide(pocket, lip)
        if opening is None and lip > least:
            lip = least
            opening = _one_wide(pocket, lip)
        if opening is None:
            parts = _inset(pocket, least)
            why = (
                "leaves nothing of it"
                if not parts
                else f"cuts it into {len(parts)} openings"
                if len(parts) > 1
                else f"leaves it under {LEAST_ACROSS:g} mm wide"
            )
            wrong.append(
                f"piece {piece.id} has no opening: even the least lip, {least:g} mm, {why}. "
                f"It's {piecemap.widest(piece.shape)[0]:.2f} mm at its widest, round {where}"
            )
            continue
        rows.append({"piece": piece, "lip_mm": lip, "pocket": pocket})
    return rows, wrong


# The solids


def _wire(arcs: list[Arc]) -> Any:
    import cadquery as cq

    edges = []
    for arc in arcs:
        a = cq.Vector(float(arc.start[0]), float(arc.start[1]), 0.0)
        b = cq.Vector(float(arc.end[0]), float(arc.end[1]), 0.0)
        if arc.straight:
            edges.append(cq.Edge.makeLine(a, b))
        else:
            m = arc.at(0.5)
            edges.append(cq.Edge.makeThreePointArc(a, cq.Vector(float(m[0]), float(m[1]), 0.0), b))
    return cq.Wire.assembleEdges(edges)


def _wire_points(wire: Any, step: float = 0.25) -> np.ndarray:
    """Points along a wire, each edge at `step` apart."""
    found = []
    for edge in wire.Edges():
        n = max(2, int(math.ceil(edge.Length() / step)))
        found += [edge.positionAt(share) for share in np.linspace(0.0, 1.0, n, endpoint=False)]
    return np.array([[p.x, p.y] for p in found])


def _face_area(outer: Any, inners: list[Any]) -> float:
    import cadquery as cq

    return float(cq.Face.makeFromWires(outer, inners).Area())


def _on_centre(centre: np.ndarray, left: bool, start: np.ndarray, end: np.ndarray) -> Arc:
    """The arc round a centre from one point to another, turning left or right."""
    out = _unit(start - centre)
    leave = np.array([-out[1], out[0]]) if left else np.array([out[1], -out[0]])
    return Arc(start, leave, end)


def _meet(one: dict[str, Any], two: dict[str, Any], near: np.ndarray) -> np.ndarray | None:
    """Where two offset sides meet, each run on as its whole line or circle: the meeting
    nearest `near`. None where they don't."""
    if one["line"] and two["line"]:
        u, v = one["b"] - one["a"], two["b"] - two["a"]
        cross = u[0] * v[1] - u[1] * v[0]
        if abs(cross) < 1e-12:
            return None
        w = two["a"] - one["a"]
        return np.asarray(one["a"] + u * ((w[0] * v[1] - w[1] * v[0]) / cross))
    if one["line"] or two["line"]:
        line, circle = (one, two) if one["line"] else (two, one)
        u = _unit(line["b"] - line["a"])
        w = circle["c"] - line["a"]
        along = float(w @ u)
        off = float(w[0] * u[1] - w[1] * u[0])
        inside = circle["r"] ** 2 - off**2
        if inside < 0:
            return None
        reach = math.sqrt(inside)
        found = [line["a"] + u * (along + sign * reach) for sign in (-1.0, 1.0)]
    else:
        between = two["c"] - one["c"]
        far = float(np.hypot(*between))
        if far < 1e-12 or far > one["r"] + two["r"] or far < abs(one["r"] - two["r"]):
            return None
        along = (far**2 + one["r"] ** 2 - two["r"] ** 2) / (2.0 * far)
        up = math.sqrt(max(one["r"] ** 2 - along**2, 0.0))
        way = between / far
        side = np.array([-way[1], way[0]])
        found = [one["c"] + way * along + side * sign * up for sign in (-1.0, 1.0)]
    return np.asarray(min(found, key=lambda q: float(np.hypot(*(q - near)))))


def _moved(arc: Arc, by: float) -> dict[str, Any] | None:
    """One side moved `by` to its left, as its line or its circle and its two ends. None
    where an arc turning left is no wider than `by`: it vanishes."""
    if arc.straight:
        way = _unit(arc.end - arc.start)
        shift = by * np.array([-way[1], way[0]])
        return {"line": True, "a": arc.start + shift, "b": arc.end + shift}
    radius = arc.radius - by  # signed: over 0 turns left, its centre on the left
    if arc.radius * radius <= 0 or abs(radius) < 1e-6:
        return None
    scale = radius / arc.radius
    return {
        "line": False, "c": arc.centre, "r": abs(radius), "left": arc.radius > 0,
        "a": arc.centre + (arc.start - arc.centre) * scale,
        "b": arc.centre + (arc.end - arc.centre) * scale,
    }  # fmt: skip


def _made(side: dict[str, Any], like: Arc) -> Arc | None:
    """A moved or cut side as an arc again. None where it was cut back past its own start."""
    if float(np.hypot(*(side["b"] - side["a"]))) < 1e-9:
        return None
    if side["line"]:
        made = Arc(side["a"], _unit(side["b"] - side["a"]), side["b"])
        return made if float(made.arrive @ _unit(like.end - like.start)) > 0 else None
    made = _on_centre(side["c"], side["left"], side["a"], side["b"])
    if made.straight or abs(made.sweep) > abs(like.sweep) + math.radians(60.0):
        return None
    return made


def offset_ring(arcs: list[Arc], by: float) -> list[Arc] | None:
    """A ring of lines and arcs moved `by` to its left, into the material of an outline that
    has it on its left, or out of it for a negative `by`: a line moves over, an arc keeps its
    centre, and where two sides meet at a corner each is run on or cut back to where their
    offsets meet, so a corner stays a corner. None where a side vanishes or two don't meet:
    that offset isn't this simple."""
    moved = [_moved(arc, by) for arc in arcs]
    sides = [side for side in moved if side is not None]
    if len(sides) != len(moved):
        return None
    count = len(sides)
    for k in range(count):
        one, two = sides[k], sides[(k + 1) % count]
        if float(np.hypot(*(one["b"] - two["a"]))) < 1e-7:
            continue
        met = _meet(one, two, (one["b"] + two["a"]) / 2.0)
        if met is None or float(np.hypot(*(met - one["b"]))) > 6.0 * abs(by) + 1.0:
            return None
        one["b"], two["a"] = met, met
    made = [_made(side, arc) for side, arc in zip(sides, arcs, strict=True)]
    found = [one for one in made if one is not None]
    return found if len(found) == len(made) else None


def _dense(arcs: list[Arc]) -> np.ndarray:
    """A ring of arcs as points, within `DENSE` mm of the curve."""
    return sampled(arcs, DENSE)


def _ring_polygon(rings: list[list[Arc]]) -> Any:
    """Rings of arcs as a polygon fine enough to inset and measure."""
    return shapely.Polygon(_dense(rings[0]), [_dense(ring) for ring in rings[1:]])


def _rings_of(shape: Any, fit: float, span: float = REACH) -> list[list[Arc]]:
    """A shape's outline and holes fitted to arcs, the material on the left of each."""
    turned = orient(shape, 1.0)
    return [fit_ring(_points(ring), fit, span) for ring in (turned.exterior, *turned.interiors)]


def _close(shape: Any) -> list[list[Arc]]:
    """A shape Shapely made from built curves, as arcs within `CLOSE` of it: its points lie on
    arcs, so a few arcs take them all."""
    return _rings_of(shape.simplify(CLOSE / 10.0), CLOSE, NEAR)


def _same(rings: list[list[Arc] | None], wanted: Any, slack: float) -> bool:
    """Whether rings of arcs are one valid outline with `wanted`'s shape, to `slack` mm²."""
    if any(ring is None for ring in rings) or len(rings) != 1 + len(wanted.interiors):
        return False
    made = _ring_polygon(rings)  # type: ignore[arg-type]
    return bool(made.is_valid and made.symmetric_difference(wanted).area <= slack)


def piece_curves(piece: Piece, lip: float, play: float, fit: float) -> dict[str, Any]:
    """One piece's pocket, pane and opening as rings of lines and arcs.

    The piece's outline is fitted to arcs, within `fit`. Its pane is those curves inset by
    the play and rounded (`pane_of`), which Shapely makes, fitted again within `CLOSE`: the
    inset of an arc is an arc, so a few take it. The pocket is the pane's own arcs moved out
    by the play, each the twin of one of the pane's, so the play is exact; where that isn't
    the pane and the play round it, as where a notch is tighter than the play, it's fitted
    within `CLOSE` instead. The opening is the pocket's arcs moved in by the lip, or where a
    bend is tighter than the lip, Shapely's inset fitted within half of `fit`."""
    # Fitted a little inside `fit`, and closer still where the curves cross themselves or
    # pinch the pane in two, as loose arcs can at a narrow place.
    pane_2d = None
    for share in (0.8, 0.4, 0.2, 0.1, 0.05):
        built = _ring_polygon(_rings_of(piece.shape, share * fit))
        pane_2d = pane_of(built, play) if built.is_valid else None
        if pane_2d is not None:
            break
    if pane_2d is None:
        raise NoAnswer(f"piece {piece.id}'s outline, fitted to curves, leaves no one pane")
    pane = _close(pane_2d)
    grown = pane_2d.buffer(play, quad_segs=4 * ROUND_SEGS)
    pocket: list[Any] = [offset_ring(ring, -play) for ring in pane]
    exact = _same(pocket, grown, 0.001 * grown.area + 0.01)
    if not exact:
        pocket = _close(grown)
    pocket_2d = _ring_polygon(pocket)
    parts = [
        part
        for part in _inset(pocket_2d, lip)
        if not under(piecemap.widest(part, 0.02)[0], LEAST_ACROSS)
    ]
    if len(parts) != 1:
        raise NoAnswer(f"piece {piece.id}'s pocket, as built, leaves no one opening")
    opening: list[Any] = [offset_ring(ring, lip) for ring in pocket]
    opening_exact = _same(opening, parts[0], 0.001 * parts[0].area + 0.01)
    if not opening_exact:
        opening = _rings_of(parts[0].simplify(fit / 20.0), fit / 2.0, NEAR)
    return {
        "pocket": pocket,
        "pane": pane,
        "opening": opening,
        "exact": exact,
        "opening_exact": opening_exact,
        "pocket_2d": pocket_2d,
        "pane_2d": _ring_polygon(pane),
        "off_mm": float(
            shapely.distance(
                shapely.points(shapely.get_coordinates(built.boundary)[::10]),
                orient(piece.shape, 1.0).boundary,
            ).max()
        ),
    }


def _wires(rings: list[list[Arc]]) -> tuple[Any, list[Any]]:
    return _wire(rings[0]), [_wire(ring) for ring in rings[1:]]


def _prism(outer: Any, inners: list[Any], low: float, high: float) -> Any:
    import cadquery as cq

    solid = cq.Solid.extrudeLinear(outer, inners, cq.Vector(0.0, 0.0, high - low))
    return solid.moved(cq.Location(cq.Vector(0.0, 0.0, low)))


def _file_word(colour: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", colour.lower()).strip("-") or "pane"


def run_build(args: Any, report: Report) -> None:
    import cadquery as cq

    found = piecemap.load(args.map)
    odd = sorted(p.id for p in found.pieces if p.thickness_mm not in (None, args.pane))
    if odd:
        raise NoAnswer(
            f"piece {piecemap.numbered(odd)} names a thickness of its own, and `pieces build` "
            f"builds every pane at --pane {args.pane:g}: panes of several thicknesses in one "
            "frame aren't built yet"
        )
    full, least = args.lip
    if not args.play > 0:
        raise NoAnswer("--play is the gap round each pane, in mm, over 0")
    if not least > args.play:
        raise NoAnswer(
            f"the least lip, {least:g} mm before play, is no more than the play, "
            f"{args.play:g} mm: a pane slid to one side would have no lip over it"
        )
    if not args.fit > 0:
        raise NoAnswer("--fit is how far a curve may be from the map's outline, in mm, over 0")
    z = heights(args.thickness, args.pane, args.layer)
    border = args.border if args.border is not None else 2.0 * found.lead_mm
    if not border > 0:
        raise NoAnswer("--border is the frame round the panel's edge, in mm, over 0")
    rows, wrong = plan(found, (full, least), args.play)
    report.numbers["unbuildable"] = wrong
    if wrong:
        for line in wrong[:12]:
            report.line(line)
        report.second(
            f"each of the {len(wrong)} piece(s) named has no pane or no opening by Shapely's "
            "inset of its outline, and by the largest circle that fits in what's left"
        )
        report.verdict(
            "FAIL",
            f"{len(wrong)} of {len(found.pieces)} piece(s) can't be built, so nothing was: "
            + "; ".join(wrong[:3])
            + (f"; and {len(wrong) - 3} more" if len(wrong) > 3 else "")
            + ". Fix each in the map, `pieces merge` for a sliver and `pieces split` for a "
            "neck, and build again",
        )
        return
    out = Path(args.out_build)
    out.mkdir(parents=True, exist_ok=True)
    outline = found.panel.buffer(border, join_style="mitre")
    frame_wire = _wire(fit_ring(_points(outline.exterior), args.fit))
    openings: list[Any] = []
    pockets: list[Any] = []
    panes: dict[str, list[Any]] = {}
    refitted: list[int] = []
    refitted_lips = 0
    lost = 0.0
    off, play_off = 0.0, 0.0
    expected = outline.area * args.thickness
    pocket_tall = z["pocket_top_z"] - z["pocket_floor_z"]
    pane_volumes: list[tuple[int, float, float]] = []
    for row in rows:
        piece: Piece = row["piece"]
        curves = piece_curves(piece, row["lip_mm"], args.play, args.fit)
        off = max(off, curves["off_mm"])
        lost += max(0.0, float(piece.shape.area - curves["pocket_2d"].area))
        if not curves["exact"]:
            refitted.append(piece.id)
        refitted_lips += not curves["opening_exact"]
        pocket_outer, pocket_inners = _wires(curves["pocket"])
        pane_outer, pane_inners = _wires(curves["pane"])
        # The pane's edge from its pocket's wall, at points round the pane: nowhere closer
        # than the play, and at it along the sides.
        wall = shapely.points(_wire_points(pane_outer, 0.5))
        gaps = shapely.distance(wall, curves["pocket_2d"].boundary)
        play_off = max(
            play_off, args.play - float(gaps.min()), abs(float(np.median(gaps)) - args.play)
        )
        pockets.append(_prism(pocket_outer, pocket_inners, z["pocket_floor_z"], z["pocket_top_z"]))
        openings.append(_wires(curves["opening"]))
        solid = _prism(pane_outer, pane_inners, z["pocket_floor_z"], z["pane_top_z"])
        panes.setdefault(piece.colour or "pane", []).append(solid)
        pane_volumes.append((piece.id, float(solid.Volume()), curves["pane_2d"].area * args.pane))
        pocket_area = _face_area(pocket_outer, pocket_inners)
        open_area = _face_area(*openings[-1])
        expected -= open_area * (args.thickness - pocket_tall) + pocket_area * pocket_tall
        row["pocket_mm2"], row["opening_mm2"] = pocket_area, open_area
    # Each opening is cut through the plate, an island inside one left standing, and then each
    # pocket between the lips: two cuts, each by tools that don't meet each other.
    plate = _prism(frame_wire, [], 0.0, args.thickness)
    through = [_prism(outer, inners, 0.0, args.thickness) for outer, inners in openings]
    frame = plate.cut(cq.Compound.makeCompound(through))
    frame = frame.cut(cq.Compound.makeCompound(pockets)).clean()
    solids = frame.Solids()
    frame_file = out / "frame.step"
    frame.exportStep(str(frame_file))
    report.wrote(str(frame_file))
    pane_files: dict[str, dict[str, Any]] = {}
    for colour, made in panes.items():
        target = out / f"pane_{_file_word(colour)}.step"
        cq.Compound.makeCompound(made).exportStep(str(target))
        report.wrote(str(target))
        pane_files[colour] = {"file": str(target), "panes": len(made)}
    volume = float(frame.Volume())
    report.fact(
        "frame", str(frame_file), how="the lead as one body: pockets, and a lip on both faces"
    )
    report.fact("panes_count", len(rows), how="one in each piece's pocket")
    report.numbers["pane_files"] = pane_files
    report.line(
        "pane files, one a colour: "
        + ", ".join(f"{v['file']} ({v['panes']} pane(s))" for v in pane_files.values())
    )
    report.numbers["pocket_z"] = z["pocket_z"]
    report.numbers["pocket_floor_z"] = z["pocket_floor_z"]
    report.numbers["pocket_top_z"] = z["pocket_top_z"]
    report.numbers["pane_top_z"] = z["pane_top_z"]
    report.numbers["room_over_pane_mm"] = z["room_over_pane_mm"]
    report.numbers["lower_lip_mm"] = z["lower_lip_mm"]
    report.numbers["upper_lip_mm"] = z["upper_lip_mm"]
    report.fact(
        "pause_z", z["pocket_top_z"], "mm",
        f"the pocket's top: its floor is at z={z['pocket_floor_z']:g}, a pane's top at "
        f"z={z['pane_top_z']:g}, {z['room_over_pane_mm']:g} mm under it; the lips are "
        f"{z['lower_lip_mm']:g} mm thick below and {z['upper_lip_mm']:g} mm above. `pause` "
        "reads the layer",
    )  # fmt: skip
    lips = sorted({r["lip_mm"] for r in rows})
    narrow = [r for r in rows if r["lip_mm"] < full]
    report.numbers["lips"] = [
        {
            "piece": r["piece"].id,
            "lip_mm": r["lip_mm"],
            "pocket_mm2": r["pocket_mm2"],
            "opening_mm2": r["opening_mm2"],
        }  # fmt: skip
        for r in rows
    ]
    report.fact(
        "least_lip_before_play_mm", lips[0], "mm",
        f"{len(rows) - len(narrow)} piece(s) at the full {full:g} mm"
        + (
            "; narrower so each keeps one opening: "
            + ", ".join(f"piece {r['piece'].id} at {r['lip_mm']:g}" for r in narrow[:12])
            + (f", and {len(narrow) - 12} more" if len(narrow) > 12 else "")
            if narrow
            else ""
        ),
    )  # fmt: skip
    report.fact("border_mm", border, "mm", "the frame round the panel's edge")
    glass_mm2 = sum(p.shape.area for p in found.pieces)
    report.fact(
        "glass_left_as_lead_mm2", lost, "mm²",
        f"{100.0 * lost / glass_mm2:.2f}% of the map's glass: tips and spurs under "
        f"{NOZZLE:g} mm, which no nozzle prints as a pane, and each pocket's corners, rounded "
        f"{NOZZLE / 2.0 + args.play:g} mm as its pane's are {NOZZLE / 2.0:g}",
    )  # fmt: skip
    report.fact(
        "curve_off_outline_mm",
        off,
        "mm",
        f"the furthest a piece's fitted curve is from the map's outline: within --fit "
        f"{args.fit:g} of its points, and twice that of the sides between them",
    )
    report.fact(
        "play_off_mm",
        play_off,
        "mm",
        f"how far a pane's edge is from {args.play:g} mm off its pocket's wall, at its closest "
        "and along its sides",
    )
    report.numbers["refitted"] = refitted
    report.numbers["refitted_lips"] = refitted_lips
    kinds = sorted({face.geomType() for face in frame.Faces()})
    report.fact("frame_faces", len(frame.Faces()), how=f"each a {' or a '.join(kinds).lower()}")
    report.numbers["frame_volume_mm3"] = volume
    report.numbers["frame_solids"] = len(solids)
    glass = {
        "frame": str(frame_file),
        "panes": [v["file"] for v in pane_files.values()],
        "pocket_z": z["pocket_z"],
        "clearance": args.play,
        "pane_thickness": args.pane,
    }
    report.numbers["glass"] = glass
    q = " ".join(glass["panes"])
    report.line(
        f"to check it: `capture {frame_file} --pocket-z {z['pocket_z']:g} --clearance "
        f"{args.play:g} --pane-thickness {args.pane:g} --panes {len(rows)}`, `pause {frame_file} "
        f"--pocket-z {z['pocket_z']:g} --pane-thickness {args.pane:g}`, `light {frame_file} {q}` "
        f"and `thin` on each file. `done`'s `glass` takes pocket_z {z['pocket_z']:g}, clearance "
        f"{args.play:g} and pane_thickness {args.pane:g}"
    )
    if refitted:
        report.note(
            f"piece {piecemap.numbered(refitted)}: its pocket isn't its pane's arcs moved "
            f"out, as where a notch is tighter than the play, so it's fitted to the pane and "
            f"the play round it, within {CLOSE:g} mm"
        )
    if refitted_lips:
        report.note(
            f"{refitted_lips} opening(s) are fitted to Shapely's inset of the pocket, where a "
            f"bend tighter than the lip goes: each within {args.fit / 2.0:g} mm of its lip"
        )
    report.second(
        f"the frame's volume, {volume:.1f} mm³ by OpenCascade, against {expected:.1f} mm³ from "
        f"its faces' areas and the heights; {len(solids)} solid(s)"
    )
    worst = max(pane_volumes, key=lambda v: abs(v[1] - v[2]) / v[2])
    report.second(
        f"each pane's volume against its pocket's curves inset in Shapely times {args.pane:g} "
        "mm: the "
        f"furthest off is piece {worst[0]}'s, {worst[1]:.2f} against {worst[2]:.2f} mm³; and "
        f"each pane's edge is {args.play:g} mm from its pocket's wall to within "
        f"{play_off:.4f} mm, at points round it"
    )
    differs = []
    if len(solids) != 1:
        differs.append(f"the frame is {len(solids)} solids, not one body")
    if abs(volume - expected) > VOLUME_OFF * expected:
        differs.append(f"the frame's volume is {volume:.1f} mm³, and its faces make {expected:.1f}")
    if off > 2.0 * args.fit + 1e-6:
        differs.append(
            f"a fitted curve is {off:.3f} mm from the map's outline, over twice --fit {args.fit:g}"
        )
    if abs(worst[1] - worst[2]) > VOLUME_OFF * worst[2]:
        differs.append(
            f"piece {worst[0]}'s pane is {worst[1]:.2f} mm³, and its inset makes {worst[2]:.2f}"
        )
    if play_off > 4.0 * CLOSE:
        differs.append(f"a pane's edge is {play_off:.3f} mm off the play")
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    report.verdict(
        "OK",
        f"{frame_file} and {len(pane_files)} pane file(s) holding {len(rows)} pane(s), from "
        f"{len(found.pieces)} piece(s): the frame {args.thickness:g} mm thick, the pause at "
        f"z={z['pocket_top_z']:g}",
    )
