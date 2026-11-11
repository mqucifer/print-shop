"""What holds a bought part: the last file named, slid along its way out (mqucifer/print-shop#76).

In a design run the model put a fan's clip toes inside the fan's frame, as an overlap at the face
the fan was pushed against, and took the overlap for its catch: nothing sat in the fan's way out,
so nothing held it. It had reasoned for hours over its own picture of which way the fan goes in.
So the bench does the picturing: `clearance --slide DIR` moves the last file named along DIR, from
where it lies until it's clear of every other part, and says, in this order:

- **Where it lies.** An overlap there is said first, with its volume and box. An overlap is never
  a hold: the part can't be put there, so the slide is `NOT HELD` while one stands.
- **The hold.** The first distance at which the part meets another, which part it meets, and the
  box of that first contact. That's `HELD` within `--hold` (1 mm unless said), and `NOT HELD` if
  the part leaves without meeting anything, or meets something only further than that.
- **The path.** Every part the slide meets before it's clear: its box, and how far in it
  reaches from the edge of the moving part's outline, seen along the slide: not how far anything
  bends, as the bench doesn't know which way an arm bends. Read backwards, that's everything the
  part has to pass on its way in, so it's what has to bend, or be fitted afterwards.
- **The way in, blocked** (mqucifer/print-shop#100). A piece on the path that isn't one of the
  first contacts and reaches further in than every one of them is named, as `slide.blocked`: the
  part can't be fitted that way unless it bends or that piece is fitted afterwards.

To meet is for the overlap to grow past what it is where the part lies, so a part touching its
seat, or sliding along a wall, meets nothing. The step is `STEP`, a third of a 0.3 mm catch, so
none is stepped over, and where the parts are further apart than that the slide moves by their
gap, which no contact can be nearer than. Each contact is then halved down to `HALVED`, and the
gap read there closes the rest, so a contact head on is read to the mesh's own figure.

**Second method:** rays. From points on the moving part (its mesh's vertices, points along its
sharp edges every `EDGE_STEP` and points sampled over its faces) along the slide, and from points
on each other part back along it, each ray counting only where it enters the other part: the
nearest is where the slide first meets it. It reads every contact of a vertex on a face exactly
and one of two edges within `EDGE_STEP`. The reach is read again from the points the rays found on
the path. The bench doesn't model friction: a lid held only by its lip's fit slides out.

**Where the parts touch already** (mqucifer/print-shop#84), as a fan's sides on its pocket's walls,
the gap can't close a contact: it's 0 all the way. Their overlap has no volume, but its faces give
the band its chords, and on eval-pocket-2's lid four feet 0.1 mm under the fan were read 0.053 mm
late, as it took that long for their overlap to pass the band. So there the contact is
read back along the overlap's growth to where it began, which head on is exact. And where the
methods read a contact's distance differently but agree on the verdict and on where it is, the
verdict stands, with both distances: `DISAGREE` is kept for a real difference.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import manifold3d as m3
import numpy as np
import shapely
import trimesh

from .limits import over
from .report import Report
from .shapes import COARSE, FINE, VOLUME_FLOOR, VOLUME_SHARE, Part, polygons

#: How far each step of the slide goes, in mm: a third of a 0.3 mm catch, so none is stepped over.
STEP = 0.1
#: Each contact is halved down to this, in mm, before the gap closes the rest.
HALVED = 1e-4
#: How far past its first contact the part is moved to read the contact's box, in mm.
PROBE = 0.02
#: The way a part slides out, as `--facing` and `edges` name ways.
WAYS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
#: Each axis's view along it, by a turn that keeps a solid the right way out: (u, v) across the
#: slide, and w along it.
ACROSS = {
    "x": ((0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "y": ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    "z": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
}
#: An edge of the mesh is sharp where its two faces turn through more than this, in degrees.
SHARP_DEG = 20.0
#: Points along each sharp edge are this far apart, in mm: two edges crossing are read within it.
EDGE_STEP = 0.05
#: A ray starts this far back of its point, in mm, so a point on a face is read; it's taken off.
BACK = 1e-4
#: A ray's hit counts where the ray is inside the part this far past it, in mm: one that only
#: grazes an edge as it runs along a face isn't.
PAST = 0.01
#: Points sampled over each part's faces, as `clearance` samples them.
SAMPLES = 6000
#: The most contacts said one by one; the rest are counted.
SHOWN = 8
#: A first contact's place, seen along the slide, is opened by this, in mm: a face sliding along
#: another leaves a sliver no wider, which isn't a contact.
OPEN = 1e-4
#: A piece on the path blocks the way in where it reaches further in than the catch by more than
#: this, in mm (mqucifer/print-shop#100): the rays' reading of a reach may be over the section's by
#: `2 * EDGE_STEP`, and a piece no further in than that is the catch's own depth.
PAST_CATCH = 2 * EDGE_STEP


def _turned(manifold: Any, axis: str) -> Any:
    rows = ACROSS[axis]
    return manifold.transform(np.array([[*r, 0.0] for r in rows], dtype=float))


def _across(points: np.ndarray, axis: str) -> np.ndarray:
    """Points as seen along the slide: (u, v) across it."""
    rows = np.asarray(ACROSS[axis][:2], dtype=float)
    return np.asarray(points, dtype=float) @ rows.T


def _shadow(manifold: Any, axis: str) -> Any:
    """What a solid covers seen along an axis, as Shapely polygons."""
    return shapely.make_valid(polygons(_turned(manifold, axis).project().to_polygons()))


def _bounds(manifold: Any) -> tuple[np.ndarray, np.ndarray]:
    """A solid's box as two corners: Manifold gives six numbers."""
    box = np.asarray(manifold.bounding_box(), dtype=float)
    return box[:3], box[3:]


def _band(overlap: Any, a: Part, b: Part) -> float:
    """How much an overlap may differ by and still be the same, in mm³, as `measure_pair` reads it:
    arithmetic's share of it, a floor, and the chords where the parts curve."""
    volume = float(overlap.volume())
    chords = min(FINE[0] * float(overlap.surface_area()), a.chord_band() + b.chord_band())
    return max(VOLUME_FLOOR, VOLUME_SHARE * volume, chords)


def _clear_at(a: Any, b: Any, d: np.ndarray) -> float:
    """How far a slides along d before it's past every point of b."""
    low_a, high_a = _bounds(a)
    low_b, high_b = _bounds(b)
    i = int(np.argmax(np.abs(d)))
    reach = high_b[i] - low_a[i] if d[i] > 0 else high_a[i] - low_b[i]
    return max(0.0, float(reach))


def _corners(low: Any, high: Any) -> list[list[float]]:
    return [[round(float(v), 4) for v in low], [round(float(v), 4) for v in high]]


def _meet(a: Any, b: Any, d: np.ndarray, rest: float, band: float, length: float) -> float | None:
    """The least distance a, slid along d, meets b: where their overlap first grows past what it
    is where a lies. Stepped by `STEP` or the gap, halved to `HALVED`, then closed: by the gap
    where the parts are apart, or where they touch, back to where the overlap began to grow."""

    def moved(t: float) -> Any:
        return a.translate(tuple(map(float, d * t)))

    def volume(t: float) -> float:
        return float((moved(t) ^ b).volume())

    def grows(t: float) -> bool:
        return volume(t) > rest + band

    clear, t = 0.0, 0.0
    while t <= length + STEP:
        if t > 0 and grows(t):
            low, high = clear, t
            while high - low > HALVED:
                mid = (low + high) / 2
                low, high = (low, mid) if grows(mid) else (mid, high)
            # Head on, the gap left is the rest of the way; at a slant it's less, never more.
            gap = float(moved(low).min_gap(b, high - low)) if rest <= band else 0.0
            if gap > 0:
                return low + min(gap, high - low)
            return _began(volume, rest, high)
        clear = t
        gap = float(moved(t).min_gap(b, length + 1.0)) if rest <= band else 0.0
        t += max(STEP, gap)
    return None


def _began(volume: Callable[[float], float], rest: float, high: float) -> float:
    """Where an overlap that has grown past its band by `high` began to grow, for parts that
    touch already, so the gap can't close it (mqucifer/print-shop#84). A part on its seat, its
    sides on a pocket's walls, has an overlap of no volume whose faces give the band its chords:
    in `hold/feet`, 0.64 mm³, which four feet 16 mm² across take 0.04 mm to pass. So the
    overlap's growth, read over `PROBE` past `high`, is followed back to where it was `rest`.
    Head on it grows evenly, and that's exact; where the contact widens as it goes in, it grows
    faster past `high`, and that's short of `high` but never before the contact."""
    grown = float(volume(high)) - rest
    rate = (float(volume(high + PROBE)) - rest - grown) / PROBE
    if rate <= 0:
        return high
    return max(0.0, high - grown / rate)


def _path(
    a: Any,
    b: Any,
    d: np.ndarray,
    axis: str,
    rest: float,
    band: float,
    start: float,
    length: float,
) -> list[tuple[np.ndarray, np.ndarray, Any]]:
    """b's material in a's path, from where a first meets it until a is past it, on the coarse
    mesh: each piece of it, such as each hook a fan passes, as its box and what it covers seen
    along the slide. A piece at one step and one at the next whose boxes meet are one piece.
    Overlaps with no volume, where a face slides along another, aren't in it, and each step's
    pieces are read by place (`_by_place`), so a part's sides on its pocket's walls don't join a
    bump on one wall to a wall across the way in on another (mqucifer/print-shop#100)."""
    seen: list[tuple[np.ndarray, np.ndarray, Any]] = []
    t = start + PROBE
    while t <= length + STEP:
        overlap = a.translate(tuple(map(float, d * t))) ^ b
        if float(overlap.volume()) > rest + band:
            for piece, low, high in _by_place(overlap, d, axis, VOLUME_FLOOR):
                seen.append((low, high, _shadow(piece, axis)))
        t += STEP
    group = list(range(len(seen)))

    def root(k: int) -> int:
        while group[k] != k:
            group[k] = group[group[k]]
            k = group[k]
        return k

    lows = np.array([s[0] for s in seen]).reshape(-1, 3)
    highs = np.array([s[1] for s in seen]).reshape(-1, 3)
    for k in range(len(seen)):
        touching = np.all(lows <= highs[k] + 1e-6, axis=1) & np.all(highs >= lows[k] - 1e-6, axis=1)
        for j in np.flatnonzero(touching[:k]):
            group[root(int(j))] = root(k)
    pieces: dict[int, list[int]] = {}
    for k in range(len(seen)):
        pieces.setdefault(root(k), []).append(k)
    return [
        (lows[ks].min(axis=0), highs[ks].max(axis=0), shapely.union_all([seen[k][2] for k in ks]))
        for ks in pieces.values()
    ]


def _reach(covered: Any, shadow: Any) -> float:
    """How far into the moving part's shadow a region reaches from the shadow's outline, in mm:
    the most its points are from that outline, halved down to `HALVED`."""
    inside = shapely.intersection(covered, shadow)
    if inside.area <= 0:
        return 0.0
    low, high = 0.0, float(np.max(np.ptp(np.asarray(shadow.bounds).reshape(2, 2), axis=0)))
    while high - low > HALVED:
        mid = (low + high) / 2
        left = shapely.intersection(inside, shadow.buffer(-mid))
        low, high = (mid, high) if left.area > 1e-9 else (low, mid)
    return low


def _reach_of_points(points: np.ndarray, axis: str, shadow: Any) -> float:
    """The second reading of a reach: the most any point found on the path, seen along the slide,
    is inside the moving part's shadow from its outline."""
    if not len(points):
        return 0.0
    seen = shapely.points(_across(points, axis))
    inside = shapely.contains(shadow, seen) | shapely.intersects(shadow.boundary, seen)
    if not np.any(inside):
        return 0.0
    return float(np.max(shapely.distance(shadow.boundary, seen[inside])))


def _points(mesh: trimesh.Trimesh) -> np.ndarray:
    """A mesh's vertices, points along its sharp edges every `EDGE_STEP`, and points over its
    faces."""
    found = [np.asarray(mesh.vertices)]
    sharp = np.asarray(mesh.face_adjacency_angles) > math.radians(SHARP_DEG)
    for i, j in np.asarray(mesh.face_adjacency_edges)[sharp]:
        p, q = mesh.vertices[i], mesh.vertices[j]
        n = int(np.linalg.norm(q - p) / EDGE_STEP)
        if n > 1:
            f = np.linspace(0.0, 1.0, n + 1)[1:-1, None]
            found.append(p + f * (q - p))
    sampled, _ = trimesh.sample.sample_surface(mesh, SAMPLES, seed=7)
    found.append(np.asarray(sampled))
    return np.vstack(found)


def _enter(
    starts: np.ndarray, d: np.ndarray, target: trimesh.Trimesh
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each point's ray along d into the target, only where it enters a face from outside, so a
    face it leaves or runs along isn't read: (how far each goes first, its point's index, where)."""
    none = np.empty(0), np.empty(0, dtype=int), np.empty((0, 3))
    if not len(starts):
        return none
    origins = starts - d * BACK
    caster = trimesh.ray.ray_triangle.RayMeshIntersector(target)  # type: ignore[no-untyped-call]
    hits, ray, tri = caster.intersects_location(  # type: ignore[no-untyped-call]
        origins, np.tile(d, (len(origins), 1)), multiple_hits=True
    )
    if not len(hits):
        return none
    entering = np.asarray(target.face_normals)[np.asarray(tri)] @ d < -1e-9
    hits, ray = np.asarray(hits)[entering], np.asarray(ray)[entering]
    if not len(hits):
        return none
    # A ray that meets an edge as it runs along a face doesn't go in: one that does is inside the
    # target a little past where it met it.
    into = _inside(hits + d * PAST, target, d, PAST / 2)
    hits, ray = hits[into], ray[into]
    far = np.maximum((hits - origins[ray]) @ d - BACK, 0.0)
    return far, ray, hits


def _inside(points: np.ndarray, target: trimesh.Trimesh, d: np.ndarray, by: float) -> np.ndarray:
    """Which points are inside the target by more than `by`: each point moved `by` each way
    along x, y and z is inside, by the parity of a ray from it along d. A point on a face has one
    of those moves outside it; a ray along d never crosses a face it runs along."""
    if not len(points):
        return np.zeros(0, dtype=bool)
    inside = np.ones(len(points), dtype=bool)
    caster = trimesh.ray.ray_triangle.RayMeshIntersector(target)  # type: ignore[no-untyped-call]
    for e in np.vstack([np.eye(3), -np.eye(3)]):
        starts = points[inside] + e * by
        if not len(starts):
            break
        hits, ray, _ = caster.intersects_location(  # type: ignore[no-untyped-call]
            starts, np.tile(d, (len(starts), 1)), multiple_hits=True
        )
        crossed = np.zeros(len(starts), dtype=int)
        if len(hits):
            # A ray through an edge or a corner hits each face there: count each crossing once.
            far = np.round((np.asarray(hits) - starts[np.asarray(ray)]) @ d, 6)
            once = np.unique(np.column_stack([np.asarray(ray), far]), axis=0)
            crossed = np.bincount(once[:, 0].astype(int), minlength=len(starts))
        still = np.flatnonzero(inside)
        inside[still[crossed % 2 == 0]] = False
    return inside


def _outside(points: np.ndarray, mesh: trimesh.Trimesh, d: np.ndarray) -> np.ndarray:
    """The points not inside a mesh, more than `BACK` in: where a part lies in another, its points
    in there would read the overlap as a contact."""
    return np.asarray(points[~_inside(points, mesh, d, BACK * 10)])


def _near(points: np.ndarray, other: Any, axis: str) -> np.ndarray:
    """The points whose ray could reach the other part: within its box seen along the slide."""
    low, high = _bounds(other)
    seen = _across(points, axis)
    box = _across(np.array([low, high]), axis)
    lo, hi = box.min(axis=0) - 0.01, box.max(axis=0) + 0.01
    return points[np.all((seen >= lo) & (seen <= hi), axis=1)]


def _by_rays(
    a_mesh: trimesh.Trimesh,
    b_mesh: trimesh.Trimesh,
    a: Any,
    b: Any,
    d: np.ndarray,
    axis: str,
    overlapping: bool,
) -> tuple[float | None, np.ndarray, np.ndarray, int]:
    """The second method: rays from a's points along d into b, and from b's back along it into a.
    (The nearest, b's points on a's path, the points on b where the nearest met it, how many rays
    ran.) Where a lies in b, the points of
    each inside the other are left out, or they'd read the overlap as a contact."""
    a_points = _near(_points(a_mesh), b, axis)
    b_points = _near(_points(b_mesh), a, axis)
    if overlapping:
        a_points, b_points = _outside(a_points, b_mesh, d), _outside(b_points, a_mesh, d)
    forward, _, on_b = _enter(a_points, d, b_mesh)
    back, ray, _ = _enter(b_points, -d, a_mesh)
    # b's points whose ray back along the slide enters a: a passes through them.
    path = np.vstack([on_b, b_points[np.unique(ray)]])
    found = np.concatenate([forward, back])
    if not len(found):
        return None, path, np.empty((0, 3)), len(a_points) + len(b_points)
    first = float(found.min())
    # Where on b the rays first met it: each point within the chords of the nearest.
    at = np.vstack([on_b, b_points[ray]])
    firsts = at[found <= first + 2 * FINE[0] + HALVED]
    return first, path, firsts, len(a_points) + len(b_points)


def _held(found: dict[str, Any], first: float | None, hold: float) -> bool:
    """Whether a first contact holds: within `--hold`, unless the part overlaps another where it
    lies, which is never a hold."""
    return not found["overlaps"] and first is not None and not over(first, hold)


def _figures_differ(
    found: dict[str, Any], met: list[dict[str, Any]], hold: float, figures: list[str]
) -> list[str]:
    """Where the two methods both meet each part but read the distance differently: what they
    disagree on, if anything (mqucifer/print-shop#84). They agree where each method's own first
    contact gives the same verdict, the rays first meet the same parts, and each point where they
    first met is in a box the step and halving read at its first contact, and each box has one.
    Then the figures are given and the verdict stands; otherwise it's `DISAGREE`."""
    rays_first = min(m["met_rays_mm"] for m in met)
    by_step, by_rays = _held(found, found["first_mm"], hold), _held(found, rays_first, hold)
    if by_step != by_rays:
        step_word, rays_word = ("HELD", "NOT HELD") if by_step else ("NOT HELD", "HELD")
        return [
            f"the step and halving read {step_word} at {found['first_mm']:.4f} mm, the rays "
            f"{rays_word} at {rays_first:.4f}, against --hold {hold:g} mm: "
            + "; ".join(figures)
        ]
    band_mm = 2 * FINE[0] + HALVED
    by_rays_parts = sorted(m["part"] for m in met if m["met_rays_mm"] <= rays_first + band_mm)
    if by_rays_parts != sorted(found["meets"]):
        return [
            f"the step and halving meet {', '.join(found['meets'])} first, the rays "
            f"{', '.join(by_rays_parts)}: " + "; ".join(figures)
        ]
    tol = 2 * FINE[0] + 1e-6
    boxes = [
        (c["part"], np.asarray(c["box"][0]), np.asarray(c["box"][1])) for c in found["contacts"]
    ]
    seen = [False] * len(boxes)
    for m in met:
        if m["part"] not in by_rays_parts:
            continue
        for point in m["_firsts"]:
            inside = [
                k
                for k, (part, low, high) in enumerate(boxes)
                if part == m["part"] and np.all(point >= low - tol) and np.all(point <= high + tol)
            ]
            if not inside:
                return [
                    f"the rays first meet {m['part']} at {_point(point)}, outside every box the "
                    "step and halving read at its first contact (slide.contacts): "
                    + "; ".join(figures)
                ]
            for k in inside:
                seen[k] = True
    if not all(seen):
        k = seen.index(False)
        return [
            f"the step and halving first meet {boxes[k][0]} in "
            f"{_box_said(boxes[k][1], boxes[k][2])}, where the rays find no first contact: "
            + "; ".join(figures)
        ]
    found["first_rays_mm"] = round(rays_first, 4)
    return []


def _agreed(found: dict[str, Any]) -> str:
    """The words for two methods that read a contact's distance differently and agree on the rest:
    "both methods find the first contact at the feet; 0.1000 and 0.1533 mm"."""
    named = sorted({n for c in found["contacts"] for n in c["in"]})
    count = len(found["contacts"])
    where = (
        ", ".join(named)
        if named and all(c["in"] for c in found["contacts"])
        else f"the same {count} place{'s' if count != 1 else ''} on {', '.join(found['meets'])}"
    )
    return (
        f"both methods find the first contact at {where}; {found['first_rays_mm']:.4f} mm by the "
        f"rays and {found['first_mm']:.4f} by the step and halving, and both read "
        f"{found['verdict']} against --hold {found['hold_mm']:g} mm, so the verdict stands: "
        "their distances differ, not their answer"
    )


def _contacts(
    a: Any, b: Any, d: np.ndarray, axis: str, first: float
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Each place a first meets b: with a moved `PROBE` past its first contact, the box of each
    piece of their overlap. Flat on flat it's a slab that thin; round a curve, wider.

    A place is read from what the overlap covers seen along the slide, opened by `OPEN`, and the
    overlap inside it: where a's sides lie on b's walls, as a fan's in its pocket, their overlap
    is a sliver no thicker than arithmetic, with no area seen along the slide. On one machine it
    has no volume and on another a little, and joined to a foot's contact it made that box run up
    the wall (mqucifer/print-shop#84, on CI's x86)."""
    overlap = a.translate(tuple(map(float, d * (first + PROBE)))) ^ b
    if float(overlap.volume()) <= 0:
        return []
    found = [(low, high) for _, low, high in _by_place(overlap, d, axis, 0.0)]
    return sorted(found, key=lambda c: tuple(c[0]))


def _by_place(overlap: Any, d: np.ndarray, axis: str, floor: float) -> list[tuple[Any, Any, Any]]:
    """An overlap's pieces, each read from a place it covers seen along the slide, opened by
    `OPEN`, and the overlap inside that place: (the piece, its box's two corners, opened again
    across the slide). A sliver where a part's sides lie on another's walls has no area seen
    along the slide, so it joins no pieces (mqucifer/print-shop#84, #100). Pieces of no more than
    `floor` mm³ are left out."""
    rows = np.asarray(ACROSS[axis], dtype=float)
    back = np.hstack([rows.T, np.zeros((3, 1))])
    corner_low, corner_high = _bounds(overlap)
    low_w, high_w = sorted((float(rows[2] @ corner_low), float(rows[2] @ corner_high)))
    seen = _shadow(overlap, axis).buffer(-OPEN)
    across = [k for k in range(3) if k != int(np.argmax(np.abs(d)))]
    found = []
    for place in getattr(seen, "geoms", [seen]):
        if place.geom_type != "Polygon" or place.area <= 0:
            continue
        upright = shapely.geometry.polygon.orient(place, 1.0)
        rings = [np.asarray(upright.exterior.coords)[:-1]]
        rings += [np.asarray(r.coords)[:-1] for r in upright.interiors]
        prism = m3.CrossSection(rings).extrude(high_w - low_w + 2.0)
        prism = prism.translate((0.0, 0.0, low_w - 1.0)).transform(back)
        for piece in (overlap ^ prism).decompose():
            if float(piece.volume()) <= floor:
                continue
            low, high = _bounds(piece)
            low[across] -= OPEN
            high[across] += OPEN
            found.append((piece, low, high))
    return found


def _in_boxes(low: np.ndarray, high: np.ndarray, boxes: list[Any]) -> list[str]:
    """The named places a contact's box lies in, within the fine mesh's chords."""
    tol = 2 * FINE[0]
    return [
        p.name
        for p in boxes
        if p.low is not None
        and np.all(low >= p.low - tol)
        and np.all(high <= p.high + tol)
    ]


def _in_box(points: np.ndarray, low: Any, high: Any) -> np.ndarray:
    """The points in a piece's box, within the coarse mesh's chords: the rays' points on it."""
    if not len(points):
        return points
    tol = 2 * COARSE[0]
    inside = np.all(points >= np.asarray(low) - tol, axis=1)
    inside &= np.all(points <= np.asarray(high) + tol, axis=1)
    return np.asarray(points[inside])


def _blocked(
    found: dict[str, Any], met: list[dict[str, Any]]
) -> tuple[float | None, list[dict[str, Any]], list[str]]:
    """Whether the way in is blocked (mqucifer/print-shop#100): each piece on the path that isn't
    one of the first contacts and reaches further into the path than every one of them, by more
    than `PAST_CATCH`. (The catch's reach, those pieces, and where the two methods read a piece
    it turns on differently.)

    In a design run a bought fan read `HELD` on a bump 0.5 mm in, and its need passed, but a wall
    of the same part lay across its whole way in, 3.75 mm into the path. The path listed it and
    nothing read it. A piece is a first contact where its box meets a first contact's box. Each
    such piece and each blocking one is read again from the rays' points in its box, and held to
    its section's reach as the path's reach is: within the points' spacing below, and
    `2 * EDGE_STEP` above. Where the part overlaps where it lies there's no hold to block."""
    if found["overlaps"] or not found["contacts"]:
        return None, [], []
    tol = 2 * COARSE[0] + OPEN
    catches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    others: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for m in met:
        boxes = [np.asarray(c["box"]) for c in found["contacts"] if c["part"] == m["part"]]
        for piece in m["pieces"]:
            low, high = np.asarray(piece["box"][0]), np.asarray(piece["box"][1])
            first = any(
                np.all(low <= box[1] + tol) and np.all(high >= box[0] - tol) for box in boxes
            )
            (catches if first else others).append((m, piece))
    if not catches:
        return None, [], []
    catch = max(piece["reach_mm"] for _, piece in catches)
    blocking = [(m, p) for m, p in others if p["reach_mm"] > catch + PAST_CATCH]
    unlike = []
    for m, piece in [*catches, *blocking]:
        reach, rays = piece["reach_mm"], piece["reach_rays_mm"]
        if rays > reach + 2 * EDGE_STEP or rays < reach - m["_spacing"] - 2 * EDGE_STEP:
            unlike.append(
                f"{m['part']}'s piece in {_box_said(*piece['box'])}: it reaches {reach:.3f} mm "
                f"into the path by the sections, {rays:.3f} by the rays' points, "
                f"{m['_spacing']:.2f} mm apart"
            )
    blocked = [
        {
            "part": m["part"],
            "box": piece["box"],
            "reach_mm": piece["reach_mm"],
            "reach_rays_mm": piece["reach_rays_mm"],
        }
        for m, piece in blocking
    ]
    return round(catch, 4), blocked, unlike


def _blocked_said(found: dict[str, Any]) -> str:
    """The way in, blocked, in words: each piece past the catch, and what it means."""
    pieces = "; ".join(
        f"{b['part']}'s piece at {_box_said(*b['box'])} reaches {b['reach_mm']:.2f} mm into the "
        f"path, past the catch's {found['catch_mm']:.2f}"
        for b in found["blocked"][:SHOWN]
    )
    more = len(found["blocked"]) - SHOWN
    if more > 0:
        pieces += f"; and {more} more, in slide.blocked"
    return (
        f"but the way in is blocked: {pieces}; {found['file']} can't be fitted this way unless "
        "it bends or is fitted afterwards"
    )


def _point(p: Any) -> str:
    return "(" + ", ".join(f"{float(v):.4g}" for v in p) + ")"


def _box_said(low: Any, high: Any) -> str:
    return f"{_point(low)} to {_point(high)}"


def what_holds(
    report: Report, moving: Part, others: list[Part], way: str, hold: float, boxes: list[Any]
) -> dict[str, Any]:
    """Slide the moving part along `way` until it's clear of the others, and say what holds it:
    where it lies, its first contact and the hold, then everything on its path. The slide's own
    verdict, `HELD` or `NOT HELD`, or `DISAGREE` where the two methods differ: one meets a part
    and the other doesn't, they read a different reach, or they read the first contact's
    distance differently and also differ on the verdict or on where it is. Two distances that
    differ with the same verdict, at the same places, keep the verdict, and both are given."""
    d = np.asarray(WAYS[way], dtype=float)
    axis = way[1]
    a_fine, a_coarse = moving.manifold(True), moving.manifold(False)
    shadow = _shadow(a_coarse, axis)
    found: dict[str, Any] = {
        "file": moving.name,
        "way": way,
        "step_mm": STEP,
        "hold_mm": hold,
        "clear_mm": 0.0,
        "first_mm": None,
        "first_rays_mm": None,
        "meets": [],
        "contacts": [],
        "overlaps": [],
        "path": [],
        "catch_mm": None,
        "blocked": [],
        "verdict": "NOT HELD",
        "why": "",
    }
    differs: list[str] = []
    figures: list[str] = []
    met: list[dict[str, Any]] = []
    rays_run = 0
    for other in others:
        b_fine = other.manifold(True)
        length = _clear_at(a_fine, b_fine, d)
        found["clear_mm"] = max(found["clear_mm"], length)
        overlap = a_fine ^ b_fine
        rest, band = float(overlap.volume()), _band(overlap, moving, other)
        if rest > band:
            low, high = _bounds(overlap)
            found["overlaps"].append(
                {"part": other.name, "overlap_mm3": round(rest, 4), "box": _corners(low, high)}
            )
        first = _meet(a_fine, b_fine, d, rest, band, length)
        rays, on_path, firsts, count = _by_rays(
            moving.fine(), other.fine(), a_fine, b_fine, d, axis, rest > band
        )
        rays_run += count
        if first is None and rays is None:
            continue
        if first is None or rays is None:
            differs.append(
                f"{other.name}: the step and halving say "
                + (f"it's met after {first:.4f} mm" if first is not None else "it's never met")
                + ", the rays "
                + (f"after {rays:.4f} mm" if rays is not None else "never")
            )
            continue
        b_coarse = other.manifold(False)
        coarse_overlap = a_coarse ^ b_coarse
        coarse_rest = float(coarse_overlap.volume())
        coarse_band = _band(coarse_overlap, moving, other)
        pieces = [
            (low, high, _reach(covered, shadow))
            for low, high, covered in _path(
                a_coarse, b_coarse, d, axis, coarse_rest, coarse_band, first, length
            )
        ]
        pieces.sort(key=lambda p: tuple(p[0]))
        reach = max((p[2] for p in pieces), default=0.0)
        reach_rays = _reach_of_points(on_path, axis, shadow)
        band_mm = 2 * FINE[0] + HALVED
        if rays < first - band_mm or rays > first + EDGE_STEP + band_mm:
            # Both found it: whether they differ on the hold, or where, is judged on them all.
            figures.append(
                f"{other.name}: the rays meet it after {rays:.4f} mm, the step and halving "
                f"after {first:.4f}"
            )
        # The points are no deeper than the sections, and short of them by their spacing at most.
        spacing = math.sqrt(max(moving.fine().area, other.fine().area) / SAMPLES)
        if reach_rays > reach + 2 * EDGE_STEP or reach_rays < reach - spacing - 2 * EDGE_STEP:
            differs.append(
                f"{other.name}: it reaches {reach:.3f} mm in from the outline by the sections, "
                f"{reach_rays:.3f} by the rays' points, {spacing:.2f} mm apart"
            )
        lows = [p[0] for p in pieces]
        highs = [p[1] for p in pieces]
        met.append(
            {
                "part": other.name,
                "met_mm": first,
                "met_rays_mm": round(rays, 4),
                "box": _corners(np.min(lows, axis=0), np.max(highs, axis=0)) if pieces else None,
                "reach_mm": round(reach, 4),
                "reach_rays_mm": round(reach_rays, 4),
                "pieces": [
                    {
                        "box": _corners(low, high),
                        "reach_mm": round(r, 4),
                        "reach_rays_mm": round(
                            _reach_of_points(_in_box(on_path, low, high), axis, shadow), 4
                        ),
                    }
                    for low, high, r in pieces
                ],
                "_b": b_fine,
                "_firsts": firsts,
                "_spacing": spacing,
            }
        )
    met.sort(key=lambda m: m["met_mm"])
    if met:
        first = met[0]["met_mm"]
        tied = [m for m in met if m["met_mm"] - first <= 2 * HALVED + 2 * FINE[0]]
        found["first_mm"] = first
        found["first_rays_mm"] = min(m["met_rays_mm"] for m in tied)
        found["meets"] = [m["part"] for m in tied]
        for m in tied:
            for low, high in _contacts(a_fine, m["_b"], d, axis, m["met_mm"]):
                found["contacts"].append(
                    {
                        "part": m["part"],
                        "box": _corners(low, high),
                        "in": _in_boxes(low, high, boxes),
                    }
                )
    found["path"] = [{k: v for k, v in m.items() if not k.startswith("_")} for m in met]
    found["catch_mm"], found["blocked"], unlike = _blocked(found, met)
    if figures:
        # A figure on its own is a disagreement only where the answer or the place differs.
        differs += _figures_differ(found, met, hold, figures) if not differs else figures
    differs += unlike
    if differs:
        found["verdict"] = "DISAGREE"
        found["why"] = "; ".join(differs)
    elif _held(found, found["first_mm"], hold):
        found["verdict"] = "HELD"
    else:
        found["verdict"] = "NOT HELD"
    if figures and not differs:
        found["why"] = _agreed(found)
        figures.insert(0, found["why"])
    _said(report, found, boxes, rays_run, differs or figures)
    report.numbers["slide"] = {k: v for k, v in found.items() if k != "why"}
    return found


def _said(
    report: Report, found: dict[str, Any], boxes: list[Any], rays: int, differs: list[str]
) -> None:
    """The slide's lines: where it lies, the hold, the path, and how it was read."""
    name, way, hold = found["file"], found["way"], found["hold_mm"]
    for o in found["overlaps"]:
        report.line(
            f"OVERLAP WHERE IT LIES: {name} | {o['part']}: {o['overlap_mm3']:.3f} mm³, in the box "
            f"{_box_said(*o['box'])}. An overlap is never a hold: {name} can't be put where it "
            "lies, so nothing holds it there. Clear the overlap, then run this again"
        )
    first = found["first_mm"]
    if first is None:
        hold_line = (
            f"hold: sliding {way}, {name} leaves without meeting anything, clear after "
            f"{found['clear_mm']:.3f} mm. Nothing is in its way out, so nothing holds it"
        )
    else:
        meets = ", ".join(found["meets"])
        hold_line = (
            f"hold: sliding {way}, {name} first meets {meets} after {first:.4f} mm, as "
            f"slide.first_mm, against --hold {hold:g} mm"
        )
        if found["overlaps"]:
            hold_line += ": but it overlaps where it lies, so nothing holds it there"
        elif found["verdict"] == "HELD":
            hold_line += f": {meets} holds it"
        elif not found["overlaps"] and over(first, hold):
            hold_line += (
                f": it moves {first:.4f} mm before anything stops it, more than {hold:g}. "
                f"Bring what holds it within {hold:g} mm of where {name} lies"
            )
    report.line(hold_line)
    if found["blocked"]:
        report.line(
            f"BLOCKED WAY IN: {name} meets the catch first, {_blocked_said(found)}. Each is in "
            "slide.blocked: a need on HELD isn't met while one is there, unless it names "
            "slide.blocked for a piece the request says is fitted afterwards"
        )
    shown = found["contacts"][:SHOWN]
    for c in shown:
        where = (
            f", in {', '.join(c['in'])}"
            if c["in"]
            else (", outside every named box" if boxes else "")
        )
        report.line(
            f"  first contact with {c['part']}, {PROBE:g} mm past it: {_box_said(*c['box'])}{where}"
        )
    if len(found["contacts"]) > SHOWN:
        report.line(f"  and {len(found['contacts']) - SHOWN} more contacts, in slide.contacts")
    if found["path"]:
        report.line(
            f"path: sliding {way} until it's clear, {name} meets {len(found['path'])} part(s). "
            f"Read backwards, that is everything {name} has to pass on its way in, so it is what "
            "has to bend, or be fitted afterwards:"
        )
        for p in found["path"]:
            box = f", in the box {_box_said(*p['box'])}" if p["box"] else ""
            report.line(
                f"  {p['part']}: met after {p['met_mm']:.4f} mm{box}, reaching "
                f"{p['reach_mm']:.3f} mm in from the edge of {name}'s outline seen along {way[1]}"
                + (f", in {len(p['pieces'])} pieces:" if len(p["pieces"]) > 1 else "")
            )
            if len(p["pieces"]) > 1:
                for piece in p["pieces"][:SHOWN]:
                    report.line(
                        f"    {_box_said(*piece['box'])}, reaching {piece['reach_mm']:.3f} mm in"
                    )
                if len(p["pieces"]) > SHOWN:
                    report.line(f"    and {len(p['pieces']) - SHOWN} more, in slide.path")
        report.line(
            f"  Each reach_mm is how far in that part reaches from the edge of {name}'s outline, "
            f"seen along {way[1]}. It isn't how far anything bends: the bench doesn't know which "
            "way an arm bends. Read where the catch is from its box"
        )
    else:
        report.line(f"path: nothing is in {name}'s way {way}, so nothing has to bend to fit it")
    if found["verdict"] == "NOT HELD":
        report.line(
            "The bench doesn't model friction: a part held only by its fit, such as a lid's lip "
            "in its walls, slides out, and reads NOT HELD"
        )
    report.line(
        f"slide: in steps of {STEP:g} mm, a third of a 0.3 mm catch, so none is stepped over, or "
        f"by the gap where the parts are further apart; each contact halved to {HALVED:g} mm, then "
        "closed by the gap. To meet is for the overlap to grow past what it is where it lies, so "
        "a part on its seat, or sliding along a wall, meets nothing"
    )
    if found["path"]:
        report.second(
            f"slide: {rays} rays, from points on {name} along {way} and on each part back along "
            "it, counted only where they enter: "
            + "; ".join(
                f"{p['part']} met after {p['met_rays_mm']:.4f} mm, reaching "
                f"{p['reach_rays_mm']:.3f} mm in"
                for p in found["path"]
            )
            + ", against "
            + ", ".join(f"{p['met_mm']:.4f}" for p in found["path"])
            + f" by the step and halving, within the chords and {EDGE_STEP:g} mm along an edge"
        )
    else:
        report.second(
            f"slide: {rays} rays, from points on {name} along {way} and on each part back along "
            "it, enter nothing either"
        )
    report.notes += differs


def summary(found: dict[str, Any]) -> str:
    """The slide's verdict in a line, for the answer's first."""
    name, way = found["file"], found["way"]
    if found["verdict"] == "DISAGREE":
        return f"sliding {name} {way}, the two methods differ: {found['why']}"
    lead = ""
    if found["overlaps"]:
        parts = ", ".join(o["part"] for o in found["overlaps"])
        lead = f"{name} overlaps {parts} where it lies, and an overlap is never a hold; "
    first = found["first_mm"]
    if first is None:
        return lead + f"sliding {way}, it leaves without meeting anything"
    meets = ", ".join(found["meets"])
    within = "within" if found["verdict"] == "HELD" or not over(first, found["hold_mm"]) else "past"
    agreed = f"; {found['why']}" if found["why"] else ""
    blocked = f", {_blocked_said(found)}" if found.get("blocked") else ""
    return lead + (
        f"sliding {way}, it first meets {meets} after {first:.4f} mm, {within} --hold "
        f"{found['hold_mm']:g} mm{blocked}{agreed}"
    )
