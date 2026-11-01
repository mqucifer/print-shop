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

from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely
import shapely.affinity
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
    pocket_heights,
)
from .report import Report
from .shapes import NoAnswer, flat_heights, hits, load, slice_at

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


def _rim(pane: Any) -> tuple[np.ndarray, np.ndarray, float]:
    """Points evenly round a pane's edge, the way inward at each, and the spacing between them."""
    ring = shapely.geometry.polygon.orient(pane, 1.0).exterior
    count = int(np.clip(ring.length / WALK, FEWEST_RIM_POINTS, MOST_RIM_POINTS))
    step = ring.length / count
    near = min(step / 4, 0.01)
    points, inward = [], []
    for k in range(count):
        at = k * step
        a, b = ring.interpolate(at - near), ring.interpolate(at + near)
        along = np.array([b.x - a.x, b.y - a.y])
        along /= max(float(np.linalg.norm(along)), 1e-12)
        here = ring.interpolate(at)
        points.append((here.x, here.y))
        inward.append((-along[1], along[0]))
    return np.array(points), np.array(inward), float(step)


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
    about as fast as you walk away. One that tapers over a long way isn't an end: it's thin."""
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
    lips: np.ndarray, points: np.ndarray, step: float, play: float, min_lip: float
) -> dict[str, Any]:
    """What the lip at each point of a pane's edge comes to.

    A lip that stops part way round a pane falls to nothing over the last stretch before it stops:
    that's where it ends, and isn't a thin spot. A thin stretch anywhere else is one.
    """
    none = lips <= SHARED
    thin = ~none & (lips - play < min_lip)
    ends = _lip_ends(lips, none, step)
    counted = thin & ~ends
    lipped = lips[~none & ~ends]
    holding = points[lips - play > SHARED]
    centre = shapely.MultiPoint(points).convex_hull.centroid
    # It can swing out about a lip that lies all on one side of it.
    surrounds = (
        len(holding) >= 3 and shapely.MultiPoint(holding).convex_hull.buffer(-1e-6).contains(centre)
    )
    found: dict[str, Any] = {
        "no_lip_mm": round(float(none.sum()) * step, 2),
        "thin_mm": round(float(counted.sum()) * step, 2),
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
) -> dict[str, Any]:
    """One face of one pane: its window, lip and way out, by sections and then by the mesh."""
    low, high = limits
    z = args.pocket_z
    window = pane.difference(covered)
    # A lip that meets the pane's edge exactly can leave a thread of nothing along it.
    window = window.buffer(-1e-4, join_style="mitre").buffer(1e-4, join_style="mitre")
    points, inward, step = _rim(pane)
    edge = shapely.points(points)
    if window.is_empty:
        by_sections = np.full(len(points), np.inf)
    else:
        by_sections = np.asarray(shapely.distance(edge, window), dtype=float)
    first = rim_reading(by_sections, points, step, args.clearance, args.min_lip)
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
    allowed = max(1e-3, GRAZE * float(pane.exterior.length))
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
    second = rim_reading(by_rays, points, step, args.clearance, args.min_lip)
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
    if face["thin_mm"]:
        problems.append(
            f"pane {name}'s lip {way} is under {args.min_lip} mm after {args.clearance} mm of play "
            f"for {face['thin_mm']} mm of its edge, near {face['thin_at']}"
        )
    if thick is not None and thick < args.layer - 2 * TIE:
        problems.append(
            f"pane {name}'s lip {way} is {thick} mm thick near {face['lip_thick_at']}, "
            f"under a {args.layer} mm layer"
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
    panes: list[dict[str, Any]] = []
    holes: list[dict[str, Any]] = []
    problems: list[str] = []
    differs: list[str] = []
    for k, opening in enumerate(openings):
        name = str(k + 1)
        pane = opening.buffer(-args.clearance, join_style="mitre")
        if pane.is_empty or pane.geom_type != "Polygon":
            problems.append(f"pane {name} is nothing or several pieces at {args.clearance} mm")
            continue
        pane = pane.simplify(1e-5)
        area = float(pane.area)
        if max(pane.intersection(above).area, pane.intersection(below).area) <= PLAIN * area:
            holes.append({"opening": k + 1, "mm2": round(float(opening.area), 3)})
            report.line(f"opening {k + 1}: nothing above or below it: a plain hole, no pane")
            continue
        heights = pocket_heights(mesh, opening, args.pocket_z)
        floor = args.pane_floor_z if args.pane_floor_z is not None else heights.floor
        floor = low if floor is None else float(floor)
        top = floor + args.pane_thickness
        centre = pane.representative_point()
        row: dict[str, Any] = {
            "pane": name,
            "at": [round(centre.x, 3), round(centre.y, 3)],
            "pane_mm2": round(area, 3),
            "pane_floor_z": round(floor, 4),
            "pane_top_z": round(top, 4),
        }
        # The pane where it sits, against the frame: one too tall for its pocket is in the lip.
        if not floor < args.pocket_z < top:
            problems.append(
                f"pane {name} runs from z={floor:.3f} to z={top:.3f}, which misses "
                f"--pocket-z {args.pocket_z}"
            )
        solid = _prism(pane, floor + INSET, top - INSET)
        row["overlap_3d_mm3"] = round(float((built ^ solid).volume()), 4)
        if row["overlap_3d_mm3"] > 1e-3:
            problems.append(
                f"pane {name} is in the frame by {row['overlap_3d_mm3']} mm³ where it sits: "
                f"{args.pane_thickness} mm is too thick for its pocket"
            )
        for key, up, covered, surface, way in (
            ("up", True, above, top, "upward"),
            ("down", False, below, floor, "downward"),
        ):
            face = _face(up, pane, opening, stack, covered, built, args, (low, high), surface)
            row[key] = face
            found = rim_differs(face, {**face, **face["rays"]}, face["rim_step_mm"])
            _face_says(name, way, face, found, args, problems, differs)
            report.second(
                f"pane {name} {way} by rays: least lip {face['rays']['least_lip_mm']} mm, "
                f"{face['rays']['no_lip_mm']} mm of edge with none; as a solid swept out, "
                f"{face['least_in_path_mm3']} mm³ of the frame in its way at the least"
            )
        panes.append(row)
        report.line(_pane_line(name, row))
    report.numbers["panes"] = panes
    report.numbers["plain_holes"] = holes
    if not panes and not problems:
        raise NoAnswer("every opening is a plain hole, with nothing to hold a pane")
    faces = [p[key] for p in panes for key in ("up", "down")]
    lips = [f["least_lip_mm"] for f in faces if f["least_lip_mm"] is not None]
    report.fact("panes_read", len(panes))
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
