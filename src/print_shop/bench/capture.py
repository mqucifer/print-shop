"""Whether each pane is held by the frame's shape: on both faces, and by how much lip.

A pane is set in at a pause and printed over, like a lens in a pair of sunglasses without the
snap. Nothing here reads a material, or counts on anything bonding: a pane is held if the frame's
shape won't let it out, upward or downward, wherever its play lets it sit.

For each face, the first method stacks plane sections of everything beyond the pocket, and reads
them as polygons: the window that's left, the lip round the pane's edge, and whether the pane
passes the window at any shift its play allows. The second asks the mesh: rays beside the pane's
edge find the lip and how thick it is, and the pane itself, as a solid swept out of the frame,
meets material or doesn't.

It reads a pane sliding straight out, and swinging out about a lip on one side. It doesn't read a
pane forced past a lip that bends, since nothing here knows a material.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
import shapely
import shapely.affinity
import shapely.ops
import trimesh

from .fabrication import (
    BESIDE,
    INSET,
    PLAIN,
    SHARED,
    TIE,
    WALK,
    _openings,
    _prism,
    _section,
    islands_line,
    pocket_heights,
)
from .glass import grid, shadow, span, visible, visible_by_rays
from .report import Report
from .shapes import NoAnswer, Part, flat_heights, hits, load, slice_at

#: Rays along one pane's edge, at most: a long edge is walked more coarsely.
MOST_RIM_POINTS = 400
#: The fewest, so a small pane is still walked all round.
FEWEST_RIM_POINTS = 16
#: Steps of a ray walk inward from the edge, and how finely its end is then found, in mm.
INWARD_STEP = 0.1
INWARD_FINE = 0.005
#: A pane's path may graze this much of the frame, per mm of its edge, in mm: the chords of a mesh.
GRAZE = 0.002
#: How far two ways of reading a lip may differ, in mm.
LIP_BAND = 0.03
#: How fast a lip must rise, per mm walked from where it stops, for that to be its end and not a
#: taper. A window's corner rises at about 1 when it's square, and a triangular pane's reads about
#: 0.58 along its edge: a frame of triangles read as disagreeing at 0.7. A gentler rise is a taper.
END_SLOPE = 0.33
#: Directions a pane is shifted in, by its play.
SHIFTS = 8
#: A rim sized to the visible share (mqucifer/print-shop#37, criterion 6). The rim the pane has
#: may fall short of the rim it needs by this much, in mm: the mesh's chords on both edges, with
#: room for the bisection that finds the rim needed. Its visible share may fall short by
#: `SHARE_TOL` percentage points: a rim drawn exactly at the need leaves exactly the share, so
#: this is the same chords read as area, on a pane as small as the smallest the runs drew.
RIM_TOL = 0.01
SHARE_TOL = 0.5
#: Halvings of the bisection for the rim needed: 2 mm of range to under a micrometre.
HALVINGS = 40
#: The rim a reply tells the model to set, in mm: the rim needed rounded down to this. Down keeps
#: the share at least the one wanted, and the lip falls short of the need by under a thousandth,
#: inside `RIM_TOL`. Rounded to the hundredth, a 2.71 mm pane needing 0.5554 mm was told 0.56,
#: which leaves 59.48% visible, under 60% less `SHARE_TOL`, and was told 0.56 again.
SET_STEP = 0.001
#: A pane file is the pane the bench read if their outlines at the pocket's height share this much
#: of what either covers.
MATCH_SHARE = 0.9
#: A file that shares less than this with a pane isn't named as the nearest to it.
NEAREST_SHARE = 0.5
#: A file this much larger than a pane, by its box's area, is a frame or a cap, and isn't read.
MATCH_LARGER = 3.0
#: Files over this many bytes aren't read to match panes.
MATCH_BYTES = 20_000_000


@dataclass
class Stack:
    """Plane sections through a frame, each with its height, ready to read."""

    heights: np.ndarray
    sections: list[Any]

    def beyond(self, z: float, up: bool) -> Any:
        """All the material above or below a height, seen along z."""
        chosen = [s for h, s in zip(self.heights, self.sections, strict=True) if (h > z) == up]
        return shapely.union_all(chosen) if chosen else shapely.Polygon()

    def thickness_at(self, x: float, y: float, z: float, up: bool) -> float:
        """How far the material beyond a height runs, from first to last, at one point."""
        point = shapely.Point(x, y)
        found = [
            float(h)
            for h, s in zip(self.heights, self.sections, strict=True)
            if (h > z) == up and s.contains(point)
        ]
        return max(found) - min(found) if found else 0.0


def _stack(built: Any, mesh: trimesh.Trimesh, layer: float) -> Stack:
    low, high = np.asarray(mesh.bounds)[:, 2]
    count = max(1, int(np.ceil((high - low) / layer)))
    heights = list(low + (np.arange(count) + 0.5) * (high - low) / count)
    # Each side of each level face, so a lip thinner than a layer isn't stepped over.
    for flat in flat_heights(mesh, least=0.01):
        heights += [flat["z"] - TIE, flat["z"] + TIE]
    kept = np.unique(np.round([h for h in heights if low < h < high], 6))
    return Stack(kept, [slice_at(built, float(z)) for z in kept])


#: A stretch of a pane's edge: where its points start, how many, and their spacing in mm.
Ring = tuple[int, int, float]


def _rim(pane: Any) -> tuple[np.ndarray, np.ndarray, list[Ring]]:
    """Points evenly round each ring of a pane's edge, the way inward at each, and the rings.

    A pane with holes, round an island of frame, has an edge round each hole as well, and its
    lip there is read the same way. Oriented, every ring has the pane's material on its left.
    """
    upright = shapely.geometry.polygon.orient(pane, 1.0)
    rings = [upright.exterior, *upright.interiors]
    total = sum(r.length for r in rings)
    most = MOST_RIM_POINTS * len(rings)
    spacing = total / int(np.clip(total / WALK, FEWEST_RIM_POINTS, most))
    points: list[tuple[float, float]] = []
    inward: list[tuple[float, float]] = []
    stretches: list[Ring] = []
    for ring in rings:
        count = max(FEWEST_RIM_POINTS, int(round(ring.length / spacing)))
        step = ring.length / count
        near = min(step / 4, 0.01)
        stretches.append((len(points), count, float(step)))
        for k in range(count):
            at = k * step
            a, b = ring.interpolate(at - near), ring.interpolate(at + near)
            along = np.array([b.x - a.x, b.y - a.y])
            along /= max(float(np.linalg.norm(along)), 1e-12)
            here = ring.interpolate(at)
            points.append((here.x, here.y))
            inward.append((-along[1], along[0]))
    return np.array(points), np.array(inward), stretches


def _runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """The stretches of a ring's flags that are set, as (start, length), wrapping round."""
    count = len(flags)
    if flags.all():
        return [(0, count)]
    if not flags.any():
        return []
    start = int(np.argmin(flags))
    runs = []
    length = 0
    for k in range(count):
        index = (start + k) % count
        if flags[index]:
            length += 1
        elif length:
            runs.append(((index - length) % count, length))
            length = 0
    if length:
        runs.append(((start - length) % count, length))
    return runs


def _lip_ends(lips: np.ndarray, none: np.ndarray, step: float) -> np.ndarray:
    """Where a lip falls away to a stretch with none, going round the corner of a window: it rises
    about as fast as you walk away. One that tapers over a long way isn't an end: it's thin.
    `lips` is one ring, which wraps round."""
    count = len(lips)
    ends = np.zeros(count, dtype=bool)
    for start, length in _runs(none):
        for origin, way in ((start - 1, -1), (start + length, 1)):
            highest = 0.0
            level = 0
            for k in range(count):
                index = (origin + way * k) % count
                walked = (k + 1) * step
                if none[index] or lips[index] < END_SLOPE * walked - step:
                    break
                # Level for a few steps is the lip itself, past its end. Rays read it to within
                # their last step, so a small fall isn't the end of the rise.
                level = 0 if lips[index] > highest + LIP_BAND / 3 else level + 1
                if level >= 3:
                    break
                ends[index] = True
                highest = max(highest, float(lips[index]))
    return ends


def rim_reading(
    lips: np.ndarray,
    points: np.ndarray,
    step: float,
    play: float,
    min_lip: float,
    rings: list[Ring] | None = None,
) -> dict[str, Any]:
    """What the lip at each point of a pane's edge comes to.

    A lip that stops part way round a pane falls to nothing over the last stretch before it stops:
    that's where it ends, and isn't a thin spot. A thin stretch anywhere else is one. With
    `rings`, the edge is several rings, round a pane's holes as well, each read on its own.
    """
    rings = rings or [(0, len(lips), step)]
    none = lips <= SHARED
    thin = ~none & (lips - play < min_lip)
    ends = np.zeros(len(lips), dtype=bool)
    spacing = np.zeros(len(lips))
    for start, count, ring_step in rings:
        part = slice(start, start + count)
        ends[part] = _lip_ends(lips[part], none[part], ring_step)
        spacing[part] = ring_step
    counted = thin & ~ends
    lipped = lips[~none & ~ends]
    holding = points[lips - play > SHARED]
    centre = shapely.MultiPoint(points).convex_hull.centroid
    # It can swing out about a lip that lies all on one side of it.
    surrounds = len(holding) >= 3 and shapely.MultiPoint(holding).convex_hull.buffer(
        -1e-6
    ).contains(centre)
    found: dict[str, Any] = {
        "no_lip_mm": round(float(spacing[none].sum()), 2),
        "thin_mm": round(float(spacing[counted].sum()), 2),
        "surrounds": bool(surrounds),
    }
    finite = lipped[np.isfinite(lipped)]
    found["least_lip_mm"] = round(float(finite.min()), 4) if finite.size else None
    if counted.any():
        worst = np.nonzero(counted)[0][np.argmin(lips[counted])]
        found["thin_at"] = [round(float(v), 3) for v in points[worst]]
    return found


def rim_differs(first: dict[str, Any], second: dict[str, Any], step: float) -> str | None:
    """Whether rays and sections tell a different story of one lip.

    Rays run square to the edge, so they can read a lip wider than it is beside a window's corner,
    and never narrower. So rays finding it narrower is a difference, and wider isn't.
    """
    a, b = first["least_lip_mm"], second["least_lip_mm"]
    if (a is None) != (b is None):
        return f"a lip's there by {'sections' if a is not None else 'rays'} only"
    if a is not None and b is not None and b < a - LIP_BAND:
        return f"its least lip is {a} mm by sections, {b} by rays"
    if abs(first["no_lip_mm"] - second["no_lip_mm"]) > max(3 * step, 0.05 * first["no_lip_mm"]):
        return (
            f"its edge with no lip is {first['no_lip_mm']} mm by sections, "
            f"{second['no_lip_mm']} by rays"
        )
    if first["surrounds"] != second["surrounds"]:
        return "sections and rays differ on whether its lip goes round it"
    return None


def _shifts(pane: Any, opening: Any, play: float) -> list[tuple[float, float]]:
    """Where a pane's play lets it sit: centred, and against the walls, whichever way."""
    found = [(0.0, 0.0)]
    if play <= 0:
        return found
    for reach in (play, play * np.sqrt(2)):
        for angle in np.linspace(0, 2 * np.pi, SHIFTS, endpoint=False):
            x, y = float(reach * np.cos(angle)), float(reach * np.sin(angle))
            moved = shapely.affinity.translate(pane, x, y)
            if moved.within(opening.buffer(1e-3)):
                found.append((x, y))
    return found


def _way_out(built: Any, pane: Any, shift: tuple[float, float], z0: float, z1: float) -> float:
    """The frame's material in the path of the pane as a solid, in mm³."""
    moved = shapely.affinity.translate(pane, *shift)
    return float((built ^ _prism(moved, z0, z1)).volume())


def _inward_ray_width(
    built: Any, x: float, y: float, way: np.ndarray, z: float, ray: np.ndarray, reach: float
) -> float:
    """How far in from the edge there's still material along a ray, by walking in with rays."""
    length = 1e4

    def covered(d: float) -> bool:
        at = np.array([x + way[0] * d, y + way[1] * d, z])
        return bool(hits(built, at, ray, length))

    if not covered(BESIDE):
        return 0.0
    inside = BESIDE
    while inside < reach:
        further = inside + INWARD_STEP
        if not covered(further):
            low, high = inside, further
            while high - low > INWARD_FINE:
                middle = (low + high) / 2
                low, high = (middle, high) if covered(middle) else (low, middle)
            return low
        inside = further
    return float("inf")


def _face(
    up: bool,
    pane: Any,
    opening: Any,
    stack: Stack,
    covered: Any,
    built: Any,
    args: Any,
    limits: tuple[float, float],
    surface: float,
    min_lip: float | None = None,
) -> dict[str, Any]:
    """One face of one pane: its window, lip and way out, by sections and then by the mesh.
    `min_lip` is the lip after play it's held to, if not `--min-lip`'s."""
    low, high = limits
    z = args.pocket_z
    window = pane.difference(covered)
    # A lip that meets the pane's edge exactly can leave a thread of nothing along it.
    window = window.buffer(-1e-4, join_style="mitre").buffer(1e-4, join_style="mitre")
    points, inward, rings = _rim(pane)
    step = max(r[2] for r in rings)
    edge = shapely.points(points)
    if window.is_empty:
        by_sections = np.full(len(points), np.inf)
    else:
        by_sections = np.asarray(shapely.distance(edge, window), dtype=float)
    held = args.min_lip if min_lip is None else min_lip
    first = rim_reading(by_sections, points, step, args.clearance, held, rings)
    row: dict[str, Any] = {
        "window_mm2": round(float(window.area), 3),
        "covered_mm2": round(float(pane.area - window.area), 3),
        "rim_step_mm": round(step, 4),
        **first,
    }
    row["least_lip_with_play_mm"] = (
        round(first["least_lip_mm"] - args.clearance, 4)
        if first["least_lip_mm"] is not None
        else None
    )

    # The first method's way out: the pane at each place its play lets it sit, against the stack.
    shifts = _shifts(pane, opening, args.clearance)
    allowed = max(1e-3, GRAZE * float(pane.boundary.length))
    passes = [
        s
        for s in shifts
        if shapely.affinity.translate(pane, *s).intersection(covered).area <= allowed
    ]
    row["shifts_read"] = len(shifts)
    row["passes_at_shifts"] = len(passes)

    # The second: rays beside the edge, from inside the pocket.
    ray = np.array([0.0, 0.0, 1.0 if up else -1.0])
    reach = float(np.sqrt(pane.area))
    by_rays = np.array(
        [
            _inward_ray_width(built, x, y, way, z, ray, reach)
            for (x, y), way in zip(points, inward, strict=True)
        ]
    )
    second = rim_reading(by_rays, points, step, args.clearance, held, rings)
    row["rays"] = {k: second[k] for k in ("least_lip_mm", "no_lip_mm", "surrounds")}

    # How thick the lip is at its root, and how much air lies between it and the pane's face.
    thicknesses, airs = [], []
    for (x, y), way in zip(points, inward, strict=True):
        at = np.array([x + way[0] * BESIDE, y + way[1] * BESIDE, z])
        met = hits(built, at, ray, 1e4)
        if len(met) >= 2:
            thicknesses.append((met[1] - met[0], at))
            airs.append(met[0] + (z - surface if up else surface - z))
    if thicknesses:
        thickness, at = min(thicknesses, key=lambda t: t[0])
        row["lip_thick_mm"] = round(float(thickness), 4)
        row["lip_thick_at"] = [round(float(at[0]), 3), round(float(at[1]), 3)]
        row["lip_thick_sections_mm"] = round(stack.thickness_at(at[0], at[1], z, up), 4)
        row["air_mm"] = round(float(min(airs)), 4)
    # The way out, again: the pane as a solid, swept to beyond the frame.
    z0, z1 = (z, high + 1.0) if up else (low - 1.0, z)
    volumes = [_way_out(built, pane, s, z0, z1) for s in shifts]
    row["least_in_path_mm3"] = round(min(volumes), 4)
    row["passes_3d_at_shifts"] = sum(v <= allowed * (z1 - z0) for v in volumes)
    return row


def _face_says(
    name: str,
    way: str,
    face: dict[str, Any],
    rim_differ: str | None,
    args: Any,
    problems: list[str],
    differs: list[str],
) -> None:
    """What one face comes to, for the verdict."""
    if rim_differ:
        differs.append(f"pane {name} {way}: {rim_differ}")
    if (face["passes_at_shifts"] > 0) != (face["passes_3d_at_shifts"] > 0):
        differs.append(
            f"pane {name} {way}: it comes out at {face['passes_at_shifts']} place(s) by sections, "
            f"{face['passes_3d_at_shifts']} as a solid"
        )
    thick, by_sections = face.get("lip_thick_mm"), face.get("lip_thick_sections_mm")
    if thick is not None and abs(thick - by_sections) > 1.5 * args.layer + 2 * TIE:
        differs.append(
            f"pane {name} {way}: its lip is {thick} mm thick by rays, {by_sections} by sections"
        )
    if face["passes_at_shifts"]:
        problems.append(
            f"pane {name} comes out {way}: it passes the window at {face['passes_at_shifts']} of "
            f"the {face['shifts_read']} places its play lets it sit"
        )
        return
    if not face["surrounds"]:
        problems.append(
            f"pane {name} can swing out {way}: what lip there is lies to one side of it, "
            f"with {face['no_lip_mm']} mm of its edge free"
        )
    if face["thin_mm"] and args.min_visible is None:
        problems.append(
            f"pane {name}'s lip {way} is under {args.min_lip} mm after {args.clearance} mm of play "
            f"for {face['thin_mm']} mm of its edge, near {face['thin_at']}"
        )
    if thick is not None and thick < args.layer - 2 * TIE:
        problems.append(
            f"pane {name}'s lip {way} is {thick} mm thick near {face['lip_thick_at']}, "
            f"under a {args.layer} mm layer"
        )


def _share_says(
    label: str,
    row: dict[str, Any],
    pane: Any,
    cover: Any,
    built: Any,
    through: tuple[float, float],
    args: Any,
    report: Report,
    problems: list[str],
    differs: list[str],
) -> None:
    """A pane's visible share on the built frame and the rim it has, against the rim it needs:
    what to set it to, or that it goes."""
    rim = row["rim"]
    region, share = visible(pane, cover)
    by_rays, rays, band = visible_by_rays(pane, region, [built], *through, 4000)
    lips = [row[k]["least_lip_mm"] for k in ("up", "down") if row[k]["least_lip_mm"] is not None]
    has = round(min(lips) + args.clearance, 4) if lips else None
    rim.update(
        visible_pct=round(share, 2),
        visible_rays_pct=round(by_rays, 2),
        rays=rays,
        has_mm=has,
    )
    name = label[5:]
    needs = "none: it's to be removed" if rim["remove"] else f"{rim['set_mm']:g} mm"
    report.line(
        f"pane {name}: {share:.1f}% visible, {args.min_visible:g}% wanted; its rim needs "
        f"{needs} before play, and has {has} mm at the least"
    )
    report.second(
        f"pane {name}'s visible share by rays straight up through the frame: {by_rays:.1f}% of "
        f"{rays}, against {share:.1f}% by area; the rim it needs, {rim['needs_mm']} mm, leaves "
        f"{rim.get('visible_at_need_grid_pct')}% by a grid against "
        f"{rim.get('visible_at_need_pct')}% by inset"
    )
    if abs(share - by_rays) > band:
        differs.append(
            f"{name}: {share:.1f}% visible by area and {by_rays:.1f}% by rays, more than the "
            f"{band:.1f} points a grid of {rays} can be off by"
        )
    if not rim["remove"] and abs(
        rim["visible_at_need_pct"] - rim["visible_at_need_grid_pct"]
    ) > rim["grid_band_pct"]:
        differs.append(
            f"{name}: at the rim it needs, {rim['visible_at_need_pct']}% is visible by inset and "
            f"{rim['visible_at_need_grid_pct']}% by a grid"
        )
    if rim["remove"]:
        problems.append(
            f"{label}: remove this pane. Even the smallest rim, {args.least_rim:g} mm before "
            f"play, leaves {rim['visible_at_least_pct']:.1f}% of it visible, and "
            f"{args.min_visible:g}% is wanted"
        )
        return
    faces = [row[k] for k in ("up", "down")]
    short = [f for f in faces if f["thin_mm"]]
    covered = share < args.min_visible - SHARE_TOL
    if not short and covered and has is not None and has <= rim["needs_mm"] + RIM_TOL:
        problems.append(
            f"{label}: its rim is the {rim['set_mm']:g} mm before play it needs, and only "
            f"{share:.1f}% of it is visible, where {args.min_visible:g}% is wanted: the frame "
            "covers it elsewhere, as a floor under it or a bar across it would"
        )
        return
    if short or share < args.min_visible - SHARE_TOL:
        where = "; ".join(
            f"{way} short of it for {f['thin_mm']} mm of its edge, near {f['thin_at']}"
            for way, f in zip(("above", "below"), faces, strict=True)
            if f["thin_mm"]
        )
        problems.append(
            f"{label}: set this pane's rim to {rim['set_mm']:g} mm before play. It has "
            f"{has} mm at the least"
            + (f" ({where})" if where else "")
            + f", and {share:.1f}% of it is visible, where {args.min_visible:g}% is wanted"
        )


def inset_share(pane: Any, overlap: float) -> float:
    """The share of a pane, in percent, that a rim covering `overlap` of it all round leaves
    visible: the pane's outline inset by that much, true distance with round joins, area against
    area."""
    return 100.0 * float(pane.buffer(-overlap, join_style="round").area) / float(pane.area)


def rim_needed(pane: Any, args: Any) -> dict[str, Any]:
    """The rim a pane needs, before play: the full rim if the share stays visible with it, the
    widest that keeps it if not, and none if even the smallest rim can't, so the pane goes.

    A rim before play runs from the pocket's wall, as the request gives it. On the pane, in the
    middle of its pocket, it covers that less the play: what `inset_share` insets by."""
    play = args.clearance
    full, least, share = args.full_rim, args.least_rim, args.min_visible
    found: dict[str, Any] = {
        "full_rim_mm": full,
        "least_rim_mm": least,
        "min_visible_pct": share,
        "visible_at_full_pct": round(inset_share(pane, full - play), 2),
        "visible_at_least_pct": round(inset_share(pane, least - play), 2),
    }
    if found["visible_at_full_pct"] >= share:
        need = full
    elif inset_share(pane, least - play) < share:
        found.update(remove=True, needs_mm=None)
        return found
    else:
        low, high = least - play, full - play
        for _ in range(HALVINGS):
            middle = (low + high) / 2
            low, high = (middle, high) if inset_share(pane, middle) >= share else (low, middle)
        need = low + play
    found.update(
        remove=False,
        needs_mm=round(need, 4),
        set_mm=round(math.floor(need / SET_STEP + 1e-6) * SET_STEP, 3),
    )
    # The second method: a grid in the pane, each point visible if it's at least the rim's
    # overlap from the pane's edge, against the inset's area.
    points, step = grid(pane, 4000)
    far = np.asarray(shapely.distance(shapely.points(points), pane.boundary), dtype=float)
    by_grid = 100.0 * float((far >= need - play).mean())
    band = max(SHARE_TOL, 100.0 * 0.25 * 2 * float(pane.boundary.length) * step / pane.area)
    found["visible_at_need_pct"] = round(inset_share(pane, need - play), 2)
    found["visible_at_need_grid_pct"] = round(by_grid, 2)
    found["grid_band_pct"] = round(band, 2)
    return found


def _clash_says(
    label: str, clash: Any, row: dict[str, Any], floor: float, top: float, args: Any
) -> str:
    """What a pane meets where it sits, and where. Meeting only the ceiling is a pane too thick,
    and meeting only the floor one set too low. Frame all the way up the pane is something
    standing in its place, as an island was before it was read (`cup-1`)."""
    x0, y0, z0, x1, y1, z1 = clash.bounding_box()
    row["overlap_at"] = [[round(v, 3) for v in (x0, y0, z0)], [round(v, 3) for v in (x1, y1, z1)]]
    said = f"{label} is in the frame by {row['overlap_3d_mm3']} mm³ where it sits"
    if z0 > floor + 2 * INSET:
        return f"{said}, from z={z0:.3f} up: {args.pane_thickness} mm is too thick for its pocket"
    if z1 < top - 2 * INSET:
        return f"{said}, up to z={z1:.3f}: it's set below the pocket's floor"
    return (
        f"{said}: frame stands in the pane's place from x={x0:.2f} to {x1:.2f}, y={y0:.2f} to "
        f"{y1:.2f}, all the way up it. A section there shows what it is"
    )


def _pane_line(name: str, row: dict[str, Any]) -> str:
    def said(face: dict[str, Any]) -> str:
        if face["window_mm2"] == 0:
            return "covered right across"
        text = f"lip {face['least_lip_mm']} mm"
        if face.get("lip_thick_mm") is not None:
            text += f", {face['lip_thick_mm']} mm thick, {face['air_mm']} mm of air"
        if face["no_lip_mm"]:
            text += f", {face['no_lip_mm']} mm of edge with none"
        return text

    return f"pane {name}: above: {said(row['up'])}. Below: {said(row['down'])}"


def where(pane: Any) -> dict[str, Any]:
    """Where a pane is, to find it by: its box's centre and size, a point inside it, its holes."""
    x0, y0, x1, y1 = pane.bounds
    inside = pane.representative_point()
    return {
        "centre": [round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2)],
        "size_mm": [round(x1 - x0, 2), round(y1 - y0, 2)],
        "inside_at": [round(inside.x, 3), round(inside.y, 3)],
        "holes": len(pane.interiors),
    }


def _label(name: str, row: dict[str, Any]) -> str:
    """A pane as the bench numbers it, said with where it is, so the number isn't taken for a
    file's: on `cup-1` the model read the bench's "pane 1" as `pane_1.step` for 33 calls."""
    (x, y), (w, h) = row["centre"], row["size_mm"]
    text = f"pane {name} (at {x:g},{y:g}, {w:g} x {h:g} mm"
    if row["holes"]:
        text += f", {row['holes']} hole(s)"
    if row.get("file"):
        text += f", {row['file']}"
    elif len(row.get("files_within", [])) > 1:
        text += (
            f", no one file: {' and '.join(row['files_within'])} lie in it, with no wall between"
        )
    elif row.get("nearest_file") and row["file_share"] >= NEAREST_SHARE:
        text += f", no file: the nearest is {row['nearest_file']}, {row['file_share']:.0%} shared"
    else:
        text += ", no file beside the frame is it"
    return text + ")"


def _round_islands(opening: Any, pane: Any, play: float) -> str:
    """How a pane goes round the islands in its opening: with a hole round each, or open where an
    island comes closer to the opening's edge than twice the play, as on `cup-1`'s first round."""
    if pane.geom_type != "Polygon":
        return f"its pane comes apart round them, in {len(getattr(pane, 'geoms', []))} piece(s)"
    holes = len(pane.interiors)
    if holes == len(opening.interiors):
        return "its pane is read with a hole round each"
    near = []
    for ring in opening.interiors:
        island = shapely.LinearRing(ring.coords)
        others = [shapely.LinearRing(r.coords) for r in (opening.exterior, *opening.interiors)]
        others = [o for o in others if not o.equals(island)]
        gap = min(float(island.distance(o)) for o in others)
        if gap < 2 * play:
            at = shapely.ops.nearest_points(island, shapely.MultiLineString(others))[0]
            near.append(f"{gap:.3f} mm from another edge at {at.x:.2f},{at.y:.2f}")
    return (
        f"its pane goes round {len(opening.interiors) - holes} of them open, not with a hole, "
        f"as it can't pass between: {'; '.join(near)}"
    )


def _inside(part: Part, x: float, y: float, z: float) -> bool:
    """Whether a point is inside a part's solid: OCCT's classifier on a STEP, the mesh on an STL."""
    if part.is_brep:
        return any(s.isInside(cq.Vector(x, y, z)) for s in part.shape.Solids())
    return bool(part.fine().contains([[x, y, z]])[0])


def match_files(
    frame: str,
    z: float,
    heights: tuple[float, float],
    panes: list[tuple[str, Any]],
    differs: list[str],
) -> dict[str, dict[str, Any]]:
    """Which file beside the frame is each pane the bench read, if any.

    First, the outlines at the pocket's height: a file's mesh section against the bench's pane,
    by the share of what either covers. Then OCCT, or the mesh for an STL, says whether a point
    inside the bench's pane is inside the file's solid. A match needs both.
    """
    own = Path(frame).resolve()
    candidates = []
    for found in sorted(own.parent.iterdir()):
        if found.suffix.lower() not in (".step", ".stp", ".stl") or found.resolve() == own:
            continue
        if found.stat().st_size > MATCH_BYTES:
            continue
        try:
            part = load(str(found))
        except Exception:  # noqa: BLE001 - a file that can't be read just isn't a match
            continue
        box = part.bounds()
        # A pane lies within the frame's height: anything taller isn't one.
        within_frame = heights[0] - 0.5 <= box[0][2] and box[1][2] <= heights[1] + 0.5
        if within_frame and box[0][2] <= z <= box[1][2]:
            candidates.append((found.name, part, box))
    sections: dict[str, Any] = {}
    matched: dict[str, dict[str, Any]] = {}
    for name, pane in panes:
        x0, y0, x1, y1 = pane.bounds
        area = (x1 - x0) * (y1 - y0)
        best: tuple[float, str, Part] | None = None
        within: list[str] = []
        for file, part, box in candidates:
            (fx0, fy0, _), (fx1, fy1, _) = box
            if fx0 > x1 or fx1 < x0 or fy0 > y1 or fy1 < y0:
                continue
            if (fx1 - fx0) * (fy1 - fy0) > MATCH_LARGER * area + 1.0:
                continue
            if file not in sections:
                try:
                    sections[file] = slice_at(part.manifold(fine=False), z)
                except NoAnswer:
                    sections[file] = shapely.Polygon()
            outline = sections[file]
            union = float(pane.union(outline).area)
            common = float(pane.intersection(outline).area)
            share = common / union if union else 0.0
            if outline.area and common >= MATCH_SHARE * float(outline.area):
                within.append(file)
            if best is None or share > best[0]:
                best = (share, file, part)
        row: dict[str, Any] = {"file": None, "files_within": within}
        if best is not None:
            share, file, part = best
            row["nearest_file"], row["file_share"] = file, round(share, 4)
            point = pane.representative_point()
            row["file_holds_point"] = _inside(part, point.x, point.y, z)
            if share >= MATCH_SHARE and row["file_holds_point"]:
                row["file"] = file
            elif share >= MATCH_SHARE:
                differs.append(
                    f"pane {name}'s outline is {file}'s ({share:.0%} shared), and OCCT finds a "
                    f"point inside the pane, {point.x:.3f},{point.y:.3f},{z}, outside {file}"
                )
        if row["file"] is not None:
            row["files_within"] = [f for f in within if f != row["file"]]
        matched[name] = row
    return matched


def run(args: Any, report: Report) -> None:
    part = load(args.frame)
    report.notes += part.notes
    built = part.manifold()
    mesh = part.fine()
    section = _section(part, args.pocket_z, report, "pocket")
    if section is None:
        return
    openings = _openings(section)
    report.fact("openings", len(openings), how="closed holes at the pocket's height")
    if not openings:
        raise NoAnswer(f"the section at z={args.pocket_z} has no closed opening")
    low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
    stack = _stack(built, mesh, args.layer)
    above, below = stack.beyond(args.pocket_z, True), stack.beyond(args.pocket_z, False)
    if args.min_visible is not None:
        # The frame's shadow along z, as `light` reads it, for each pane's visible share.
        cover, _ = shadow([part], args.layer)
        through = span([part])
        report.line(
            f"each pane's rim sized to keep {args.min_visible:g}% of it visible: "
            f"{args.full_rim:g} mm before play where that keeps it, never under "
            f"{args.least_rim:g} mm"
        )
    panes: list[dict[str, Any]] = []
    holes: list[dict[str, Any]] = []
    problems: list[str] = []
    differs: list[str] = []
    readable: list[tuple[int, Any, Any]] = []
    for k, opening in enumerate(openings):
        name = str(k + 1)
        pane = opening.buffer(-args.clearance, join_style="mitre")
        said = islands_line(k, opening, _round_islands(opening, pane, args.clearance))
        if said:
            report.line(said)
        if pane.is_empty or pane.geom_type != "Polygon":
            pieces = len(getattr(pane, "geoms", [])) if not pane.is_empty else 0
            x0, y0, x1, y1 = opening.bounds
            text = (
                f"opening {name} (at {(x0 + x1) / 2:.2f},{(y0 + y1) / 2:.2f}, {x1 - x0:.2f} x "
                f"{y1 - y0:.2f} mm) leaves {pieces} piece(s) of pane at {args.clearance} mm of play"
            )
            if opening.interiors:
                text += (
                    f": it has {len(opening.interiors)} island(s) inside it, closer to its edge "
                    f"than twice the play, so no one pane fits round them"
                )
            problems.append(text)
            continue
        readable.append((k, opening, pane.simplify(1e-5)))
    files = match_files(
        args.frame,
        args.pocket_z,
        (low, high),
        [(str(k + 1), p) for k, _, p in readable],
        differs,
    )
    report.note(
        "pane numbers are the bench's own, largest opening first, and not a file's: each is said "
        "with where it is, and the file whose outline it is, if one beside the frame matches"
    )
    for k, opening, pane in readable:
        name = str(k + 1)
        area = float(pane.area)
        if max(pane.intersection(above).area, pane.intersection(below).area) <= PLAIN * area:
            holes.append({"opening": k + 1, "mm2": round(float(opening.area), 3)})
            report.line(f"opening {k + 1}: nothing above or below it: a plain hole, no pane")
            continue
        heights = pocket_heights(mesh, opening, args.pocket_z)
        floor = args.pane_floor_z if args.pane_floor_z is not None else heights.floor
        floor = low if floor is None else float(floor)
        top = floor + args.pane_thickness
        row: dict[str, Any] = {
            "pane": name,
            "at": where(pane)["inside_at"],
            **where(pane),
            **files[name],
            "pane_mm2": round(area, 3),
            "pane_floor_z": round(floor, 4),
            "pane_top_z": round(top, 4),
        }
        label = _label(name, row)
        # The pane where it sits, against the frame: one too tall for its pocket is in the lip.
        if not floor < args.pocket_z < top:
            problems.append(
                f"{label} runs from z={floor:.3f} to z={top:.3f}, which misses "
                f"--pocket-z {args.pocket_z}"
            )
        solid = _prism(pane, floor + INSET, top - INSET)
        clash = built ^ solid
        row["overlap_3d_mm3"] = round(float(clash.volume()), 4)
        if row["overlap_3d_mm3"] > 1e-3:
            problems.append(_clash_says(label, clash, row, floor, top, args))
        held = None
        if args.min_visible is not None:
            rim = row["rim"] = rim_needed(pane, args)
            needs = rim["needs_mm"] if rim["needs_mm"] is not None else args.least_rim
            # A rim before play covers that less the play on the pane in the middle of its
            # pocket, and `rim_reading` holds the lip once the pane has slid: less the play again.
            held = needs - 2 * args.clearance - RIM_TOL
        for key, up, covered, surface, way in (
            ("up", True, above, top, "upward"),
            ("down", False, below, floor, "downward"),
        ):
            face = _face(
                up, pane, opening, stack, covered, built, args, (low, high), surface, held
            )
            row[key] = face
            found = rim_differs(face, {**face, **face["rays"]}, face["rim_step_mm"])
            _face_says(label[5:], way, face, found, args, problems, differs)
            report.second(
                f"pane {name} {way} by rays: least lip {face['rays']['least_lip_mm']} mm, "
                f"{face['rays']['no_lip_mm']} mm of edge with none; as a solid swept out, "
                f"{face['least_in_path_mm3']} mm³ of the frame in its way at the least"
            )
        if args.min_visible is not None:
            _share_says(label, row, pane, cover, built, through, args, report, problems, differs)
        panes.append(row)
        report.line(_pane_line(label[5:], row))
    report.numbers["panes"] = panes
    report.numbers["plain_holes"] = holes
    if not panes and not problems:
        raise NoAnswer("every opening is a plain hole, with nothing to hold a pane")
    faces = [p[key] for p in panes for key in ("up", "down")]
    lips = [f["least_lip_mm"] for f in faces if f["least_lip_mm"] is not None]
    report.fact("panes_read", len(panes))
    if args.panes is not None and len(panes) != args.panes:
        shared = [
            f"pane {p['pane']} holds {' and '.join(p['files_within'])}"
            for p in panes
            if not p.get("file") and len(p.get("files_within", [])) > 1
        ]
        problems.append(
            f"{len(panes)} pane(s) read, and {args.panes} were asked for"
            + (f": {'; '.join(shared)}, in one opening" if shared else "")
        )
    if lips:
        report.fact("least_lip_mm", min(lips), how="round every pane's edge, on both faces")
    free = sum(f["no_lip_mm"] for f in faces)
    if free and not problems:
        report.note(
            f"{free:.1f} mm of pane edges have no lip. It may be deliberate: a pane is held if "
            "the rest of its lip goes round it, so this is read and reported, not judged"
        )
    report.note("Held means by shape: nothing here reads a material or counts on a bond")
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:5]))
    elif problems:
        report.verdict("FAIL", "; ".join(problems[:5]) + f" ({len(problems)} in all)")
    else:
        report.verdict(
            "OK",
            f"{len(panes)} pane(s) held on both faces, wherever their play lets them sit: "
            f"the least lip {min(lips) if lips else 'n/a'} mm",
        )
