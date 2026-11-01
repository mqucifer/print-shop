"""What a frame and its panes must be to print: panes from openings, thin features, the pause.

All three start from plane sections of the mesh. Each answers again another way: a pane's fit
in 3D against the frame, a no-lip length by rays, a thin feature by rays across it, and a
pause layer from flat faces as well as from sections layer by layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cadquery as cq
import manifold3d as m3
import numpy as np
import shapely
import trimesh

from .report import Report
from .shapes import (
    FINE,
    NoAnswer,
    Part,
    diagonal,
    flat_heights,
    hits,
    load,
    section_checked,
    slice_at,
)

#: A section's area may differ from a second method's by this share.
SECTION_SHARE = 2e-3
#: Heights closer than this are the same height, in mm.
LEVEL = 1e-3
#: Sliver holes in a section are ignored below this, in mm².
MIN_OPENING = 0.05
#: How far from a boundary a point stands to read what's beside it, in mm.
BESIDE = 0.02
#: Spacing of the rays that walk a boundary, in mm.
WALK = 0.25
#: A boundary within this of another is shared with it, in mm.
SHARED = 0.01
#: Where a pocket's walls are read for fit: this far inside its floor and ceiling, in mm.
INSET = 0.01
#: A surface steeper than this (the z of its unit normal) is a wall.
WALL_SLOPE = 0.9
#: The fewest rays through the surface that can stand for a thin wall.
MIN_RAYS = 5
#: A sharp point's least area, as a share of the nozzle's width squared. A 40 degree point
#: leaves about 0.4 of that; chords along a curve leave far less than this.
TIP_SHARE = 0.05
#: The share of an opening the lip may cover and it still be a plain hole.
PLAIN = 1e-3
#: A layer's mid-height within this of a level face is on it, in mm.
TIE = 1e-4
#: A pane with more points than this is written as a mesh only.
STEP_POINTS = 800


@dataclass
class Pocket:
    floor: float | None
    ceiling: float | None


def pocket_heights(mesh: trimesh.Trimesh, opening: Any, z: float) -> Pocket:
    """The level faces inside an opening that bound it, from the mesh's face normals."""
    normals = np.asarray(mesh.face_normals)
    centres = np.asarray(mesh.triangles_center)
    level = np.abs(normals[:, 2]) > 0.9999
    inside = shapely.contains_xy(opening.buffer(LEVEL), centres[level, 0], centres[level, 1])
    heights, up = centres[level, 2][inside], normals[level, 2][inside] > 0
    below = heights[up & (heights < z)]
    above = heights[~up & (heights > z)]
    return Pocket(
        float(below.max()) if below.size else None,
        float(above.min()) if above.size else None,
    )


def _prism(polygon: Any, z0: float, z1: float) -> Any:
    """A polygon stood up as a solid, checked: a clockwise ring would extrude to nothing."""
    upright = shapely.geometry.polygon.orient(polygon, 1.0)
    rings = [np.asarray(upright.exterior.coords)[:-1]]
    rings += [np.asarray(r.coords)[:-1] for r in upright.interiors]
    built = m3.CrossSection(rings).extrude(z1 - z0).translate((0.0, 0.0, z0))
    wanted = float(polygon.area) * (z1 - z0)
    if abs(float(built.volume()) - wanted) > max(1e-3, 1e-3 * wanted):
        raise NoAnswer(f"a pane solid came out at {built.volume():.3f} mm³, not {wanted:.3f}")
    return built


def _write_pane(polygon: Any, z0: float, thickness: float, stem: Path) -> list[str]:
    """The pane as a file a slicer or a later check can read."""
    built = _prism(polygon, z0, z0 + thickness).to_mesh64()
    mesh = trimesh.Trimesh(np.asarray(built.vert_properties)[:, :3], np.asarray(built.tri_verts))
    if not mesh.is_watertight:
        raise NoAnswer(f"{stem.name}'s mesh isn't closed")
    mesh.export(str(stem.with_suffix(".stl")))
    wrote = [str(stem.with_suffix(".stl"))]
    points = list(polygon.exterior.coords)[:-1]
    if len(points) <= STEP_POINTS and not polygon.interiors:
        solid = cq.Workplane("XY").workplane(offset=z0).polyline(points).close().extrude(thickness)
        cq.exporters.export(solid, str(stem.with_suffix(".step")))
        wrote.append(str(stem.with_suffix(".step")))
    return wrote


def _between_faces(mesh: trimesh.Trimesh, z: float, name: str) -> None:
    """A section exactly on a level face could read either side of it, so it's refused."""
    for flat in flat_heights(mesh, least=0.01):
        if abs(flat["z"] - z) <= LEVEL:
            raise NoAnswer(f"{name}={z} is on a level face: choose a height between two faces")


def _section(part: Part, z: float, report: Report, name: str) -> Any:
    """A section, with its area checked by a different method."""
    _between_faces(part.fine(), z, f"{name}-z")
    found, area, other, how = section_checked(part, z)
    report.fact(f"{name}_section_mm2", round(area, 3), how=f"mesh at z={z}")
    if other is None:
        report.second(f"{how} at z={z} returned no faces")
        raise NoAnswer(f"{how} at z={z} found nothing where the mesh found {area:.3f} mm²")
    report.second(f"{name} section at z={z} by {how}: {other:.3f} mm²")
    if abs(other - area) > max(SECTION_SHARE * area, 0.01):
        report.verdict(
            "DISAGREE", f"the section at z={z} is {area:.3f} mm² by the mesh, {other:.3f} by {how}"
        )
        return None
    return found


def _openings(section: Any) -> list[Any]:
    holes = [shapely.Polygon(r) for poly in section.geoms for r in poly.interiors]
    return sorted((h for h in holes if h.area >= MIN_OPENING), key=lambda h: -h.area)


def _no_lip_by_rays(frame: Any, opening: Any, z: float, length: float) -> float:
    """Walk the opening's edge just inside it, and look up: mm of edge with nothing above."""
    ring = opening.exterior
    inward = opening.buffer(-BESIDE, join_style="mitre").exterior
    count = max(8, int(ring.length / WALK))
    step = ring.length / count
    missed = 0
    for k in range(count):
        at = inward.interpolate(k / count, normalized=True)
        missed += not hits(frame, np.array([at.x, at.y, z]), np.array([0.0, 0.0, 1.0]), length)
    return float(missed * step)


def run_panes(args: Any, report: Report) -> None:
    frame = load(args.frame)
    report.notes += frame.notes
    built = frame.manifold()
    mesh = frame.fine()
    section = _section(frame, args.pocket_z, report, "pocket")
    if section is None:
        return
    lip = None
    if args.lip_z is not None:
        lip = _section(frame, args.lip_z, report, "lip")
        if lip is None:
            return
    openings = _openings(section)
    report.fact(
        "openings", len(openings), how=f"holes over {MIN_OPENING} mm² at the pocket's height"
    )
    if not openings:
        raise NoAnswer(f"the section at z={args.pocket_z} has no closed opening")
    windows = _openings(lip) if lip is not None else []
    # A folder of this frame's own, emptied of its last panes, so none is left from another run.
    out = Path(args.out) / Path(args.frame).stem
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("pane-*"):
        old.unlink()
    panes: list[dict[str, Any]] = []
    problems: list[str] = []
    differs: list[str] = []
    length = 2 * diagonal(mesh)
    holes: list[dict[str, Any]] = []
    for k, opening in enumerate(openings):
        if _plain_hole(opening, windows):
            holes.append(
                {
                    "opening": k + 1,
                    "mm2": round(float(opening.area), 3),
                    "perimeter_mm": round(float(opening.exterior.length), 3),
                }
            )
            report.line(
                f"opening {k + 1}: {holes[-1]['mm2']} mm², with no lip at all: "
                "a plain hole, no pane"
            )
            continue
        row = _pane(k, opening, built, mesh, windows, args, out, length, problems, differs)
        panes.append(row)
        report.files += row.pop("files", [])
        report.line(_pane_line(row))
        report.second(
            f"pane {row['pane']} in 3D against the frame: gap {row['gap_3d_mm']} mm, "
            f"overlap {row['overlap_3d_mm3']} mm³"
            + (
                f"; no lip by rays {row['no_lip_rays_mm']} mm"
                if row.get("no_lip_rays_mm") is not None
                else ""
            )
        )
    report.numbers["panes"] = panes
    report.numbers["plain_holes"] = holes
    if not panes:
        raise NoAnswer("every opening is a plain hole, with nothing above to hold a pane")
    no_lip = [p["no_lip_mm"] for p in panes if p.get("no_lip_mm") is not None]
    if no_lip and max(no_lip) > WALK:
        report.note(
            f"up to {max(no_lip):.1f} mm of a pocket's edge has no lip above it. It may be "
            "deliberate: this is read and reported, not judged"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:5]))
    elif problems:
        report.verdict("FAIL", "; ".join(problems[:5]) + f" ({len(problems)} in all)")
    else:
        report.verdict("OK", f"{len(panes)} pane(s) fit at {args.clearance} mm by both methods")


def _pane(
    k: int,
    opening: Any,
    built: Any,
    mesh: trimesh.Trimesh,
    windows: list[Any],
    args: Any,
    out: Path,
    length: float,
    problems: list[str],
    differs: list[str],
) -> dict[str, Any]:
    name = str(k + 1)
    pane = opening.buffer(-args.clearance, join_style="mitre")
    row: dict[str, Any] = {"pane": name, "opening_mm2": round(float(opening.area), 3)}
    if pane.is_empty or pane.geom_type != "Polygon":
        problems.append(f"pane {name} is nothing or several pieces at {args.clearance} mm")
        row.update(gap_3d_mm=None, overlap_3d_mm3=None, pane_mm2=0.0, gap_2d_mm=None)
        return row
    pane = pane.simplify(1e-5)
    edge_nearest = float(pane.exterior.distance(opening.exterior))
    row.update(
        pane_mm2=round(float(pane.area), 3),
        centre=[round(float(pane.centroid.x), 3), round(float(pane.centroid.y), 3)],
        perimeter_mm=round(float(pane.exterior.length), 3),
        gap_2d_mm=round(edge_nearest, 4),
    )
    # What a nozzle's line can't fill in the pane, and whether it's big enough to print.
    half = args.nozzle / 2
    opened = pane.buffer(-half, join_style="mitre").buffer(half, join_style="mitre")
    lost = 100 * (1 - float(opened.area) / float(pane.area))
    row["lost_to_nozzle_pct"] = round(lost, 2)
    if pane.area < args.min_area or lost > 40:
        problems.append(
            f"pane {name} is too small to print: {pane.area:.2f} mm², with {lost:.0f}% narrower "
            f"than the {args.nozzle} mm nozzle"
        )
        row["too_small"] = True
    # Second method: the pane as a solid, against the frame's walls over the pocket's height.
    heights = pocket_heights(mesh, opening, args.pocket_z)
    low, high = np.asarray(mesh.bounds)[:, 2]
    floor = heights.floor if heights.floor is not None else float(low)
    ceiling = heights.ceiling if heights.ceiling is not None else float(high)
    row.update(pocket_floor_z=heights.floor, pocket_ceiling_z=heights.ceiling)
    z0, z1 = floor + INSET, ceiling - INSET
    (x0, y0, _), (x1, y1, _) = np.asarray(mesh.bounds)
    slab = m3.Manifold.cube((x1 - x0 + 2, y1 - y0 + 2, z1 - z0)).translate((x0 - 1, y0 - 1, z0))
    walls = built ^ slab
    solid = _prism(pane, z0, z1)
    overlap = float((walls ^ solid).volume())
    reach = 2 * args.clearance + 1.0
    gap = float(walls.min_gap(solid, reach))
    row.update(gap_3d_mm=round(gap, 4), overlap_3d_mm3=round(overlap, 4))
    if args.pane_thickness:
        row["room_over_pane_mm"] = round(ceiling - floor - args.pane_thickness, 4)
        if ceiling - floor < args.pane_thickness:
            problems.append(
                f"pane {name} is {args.pane_thickness} mm thick, in a pocket of "
                f"{ceiling - floor:.3f} mm"
            )
    band = 2 * FINE[0] + 1e-3
    if overlap > 1e-3 or gap < args.clearance - band:
        problems.append(
            f"pane {name} doesn't fit in 3D: gap {gap:.4f} mm, overlap {overlap:.4f} mm³, "
            f"though its section is {edge_nearest:.4f} mm clear"
        )
    elif gap >= reach or gap > args.clearance + band:
        # It's as far from the walls as it was asked to be at one height, so it can't be further
        # over them all. A figure at the search's limit means nothing was measured.
        differs.append(
            f"pane {name}'s gap is {gap:.4f} mm in 3D, {edge_nearest:.4f} in the section"
        )
    if abs(edge_nearest - args.clearance) > 1e-3:
        differs.append(f"pane {name}'s measured gap is {edge_nearest:.4f} mm in the section")
    if windows:
        _lip(row, name, pane, opening, windows, built, args, length, differs)
    thickness = args.pane_thickness or (ceiling - floor)
    row["files"] = _write_pane(pane, floor, thickness, out / f"pane-{name}")
    return row


def _plain_hole(opening: Any, windows: list[Any]) -> bool:
    """An opening the lip's height leaves just as open has nothing to hold a pane in."""
    if not windows:
        return False
    window = max(windows, key=lambda w: w.intersection(opening).area)
    return bool(opening.difference(window).area <= PLAIN * opening.area)


def _lip(
    row: dict[str, Any],
    name: str,
    pane: Any,
    opening: Any,
    windows: list[Any],
    built: Any,
    args: Any,
    length: float,
    differs: list[str],
) -> None:
    """What holds the pane in: the lip above it, and how much of its edge has none."""
    window = max(windows, key=lambda w: w.intersection(opening).area)
    if window.intersection(opening).area <= 0:
        return
    shared = window.exterior.intersection(opening.exterior.buffer(SHARED)).length
    row.update(
        window_mm2=round(float(window.area), 3),
        window_uncovered_mm2=round(float(window.difference(pane).area), 3),
        pane_behind_lip_mm2=round(float(pane.difference(window).area), 3),
        no_lip_mm=round(float(shared), 2),
    )
    walked = _no_lip_by_rays(built, opening, args.pocket_z, length)
    row["no_lip_rays_mm"] = round(walked, 2)
    # A walked figure is good to a step at each end of each stretch; a pocket has few stretches.
    if abs(walked - shared) > max(3 * WALK, 0.05 * shared):
        differs.append(
            f"pane {name}'s edge with no lip is {shared:.1f} mm by sections, {walked:.1f} by rays"
        )


def _pane_line(row: dict[str, Any]) -> str:
    text = (
        f"pane {row['pane']}: {row['pane_mm2']} mm² from a {row['opening_mm2']} mm² opening, "
        f"gap {row['gap_2d_mm']} mm in the section"
    )
    if "lost_to_nozzle_pct" in row:
        text += f", {row['lost_to_nozzle_pct']}% narrower than the nozzle"
    if "no_lip_mm" in row:
        text += (
            f", window {row['window_mm2']} mm², {row['pane_behind_lip_mm2']} mm² behind the lip, "
            f"{row['no_lip_mm']} mm of edge with no lip"
        )
    return text


# Thin features


def _opened(polys: Any, width: float) -> Any:
    half = width / 2
    return polys.buffer(-half, join_style="mitre").buffer(half, join_style="mitre")


def _tips(section: Any, width: float) -> Any:
    """What a nozzle's round end can't reach: sharp points longer than the nozzle is wide.

    A round end leaves a little in every corner, so a piece counts only if it runs as far as
    the nozzle is wide, which takes a point sharper than about 40 degrees. Opening with mitred
    corners, as `_opened` does, gives those points straight back, so it never sees them.
    """
    half = width / 2
    lost = section.difference(
        section.buffer(-half, join_style="mitre").buffer(half, join_style="round")
    )
    pieces = []
    for piece in getattr(lost, "geoms", [lost]):
        if piece.is_empty:
            continue
        x0, y0, x1, y1 = piece.bounds
        # The area rules out the slivers a round end leaves along a curve's chords.
        if max(x1 - x0, y1 - y0) >= width and piece.area >= TIP_SHARE * width**2:
            pieces.append(piece)
    return shapely.MultiPolygon([p for p in pieces if p.geom_type == "Polygon"])


def _across(built: Any, x: float, y: float, z: float, length: float) -> float:
    """The narrowest way across the material at a point, by rays both ways on eight lines."""
    best = np.inf
    for angle in np.linspace(0, np.pi, 8, endpoint=False):
        way = np.array([np.cos(angle), np.sin(angle), 0.0])
        at = np.array([x, y, z])
        forth, back = hits(built, at, way, length), hits(built, at, -way, length)
        if forth and back:
            best = min(best, forth[0] + back[0])
    return float(best)


def run_thin(args: Any, report: Report) -> None:
    part = load(args.file)
    report.notes += part.notes
    built = part.manifold()
    mesh = part.fine()
    length = 2 * diagonal(mesh)
    nozzle, layer = args.nozzle, args.layer
    low, high = np.asarray(mesh.bounds)[:, 2]
    count = max(1, int(np.ceil((high - low) / layer)))
    sampled = ""
    if count > args.max_layers:
        count = args.max_layers
        sampled = f" (sampled: {count} heights, not every layer)"
    heights = low + (np.arange(count) + 0.5) * (high - low) / count
    thin_rows: list[dict[str, Any]] = []
    too_small_rows: list[dict[str, Any]] = []
    lost_mm3 = 0.0
    step = float((high - low) / count)
    least = round(nozzle**2 / 2, 4)
    tips_by_layer = []
    for z in heights:
        section = slice_at(built, float(z))
        tips_by_layer.append(_tips(section, nozzle))
        opened = _opened(section, nozzle)
        lost = section.difference(opened)
        pieces = list(getattr(lost, "geoms", [lost]))
        lost_mm3 += float(lost.area) * step
        for poly in section.geoms:
            if _opened(poly, nozzle).is_empty and poly.area >= 1e-4:
                c = poly.representative_point()
                too_small_rows.append(
                    {
                        "z": round(float(z), 3),
                        "x": round(c.x, 3),
                        "y": round(c.y, 3),
                        "mm2": round(float(poly.area), 4),
                    }
                )
        for piece in pieces:
            if not piece.is_empty and piece.area >= least:
                c = piece.representative_point()
                thin_rows.append(
                    {
                        "z": round(float(z), 3),
                        "x": round(c.x, 3),
                        "y": round(c.y, 3),
                        "mm2": round(float(piece.area), 3),
                    }
                )
    report.fact("layers_read", f"{count} at {step:.3f} mm" + sampled)
    report.fact(
        "narrower_than_nozzle_mm3", round(lost_mm3, 3), how=f"sections opened by {nozzle} mm"
    )
    report.numbers["too_small_at"] = too_small_rows[:50]
    # Second method 1: rays across each thin spot the sections found. A small one they find
    # wide is a cusp where a curve meets a flat, which opening by the nozzle nibbles: it isn't a
    # thin spot, and is noted. A large one they find wide is a real difference between methods.
    measured = [_across(built, r["x"], r["y"], r["z"], length) for r in thin_rows[:50]]
    wide = [r for r, w in zip(thin_rows, measured, strict=False) if w >= nozzle + 2 * FINE[0]]
    cusps = [r for r in wide if r["mm2"] < nozzle**2]
    if cusps:
        report.note(
            f"{len(cusps)} small spot(s) the sections read as thin measure wider across by rays: "
            f"a cusp where a curve meets a flat, at {cusps[:2]}"
        )
    thin_rows = [r for r in thin_rows if r not in cusps]
    report.fact("thin_spots", len(thin_rows), how=f"over {least} mm² in a layer")
    report.numbers["thin_at"] = thin_rows[:50]
    widths = [w for w in measured if np.isfinite(w) and w < nozzle + 2 * FINE[0]]
    unconfirmed = len(wide) - len(cusps)
    if widths:
        report.fact("thinnest_across_mm", round(min(widths), 4), how="rays across the thin spots")
    # Second method 2: rays straight through the walls and floors from all over the surface.
    wall, flat, starts = _through(mesh, built, args.samples, length)
    # Each ray stands for its share of the surface.
    per_ray = float(mesh.area) / args.samples
    wall_thin = float((wall < nozzle).sum()) * per_ray
    flat_thin = flat[flat < layer]
    # A thin reading beside a sharp point is that point: a real one, and not a wall.
    tip_rows = sum(len(t.geoms) for t in tips_by_layer)
    tips_per_layer = max((len(t.geoms) for t in tips_by_layer), default=0)
    report.fact(
        "sharp_points", tips_per_layer, how=f"sharper than the {nozzle} mm nozzle's round end"
    )
    alone = []
    for point in starts[wall < nozzle]:
        index = min(count - 1, max(0, int((point[2] - low) / step)))
        near = tips_by_layer[index]
        if near.is_empty or near.distance(shapely.Point(point[0], point[1])) > 2 * nozzle:
            alone.append(np.round(point, 3).tolist())
    wall_alone = len(alone) * per_ray
    report.fact("thin_rays_not_at_a_point", len(alone), how=f"each stands for {per_ray:.2f} mm²")
    report.numbers["thin_rays_at"] = alone[:50]
    # A wall shows as many rays. A few alone are where two walls come together in a wedge, and
    # the same part's next sample finds a different few: that's the sampling's limit.
    wall_counts = len(alone) >= MIN_RAYS and wall_alone > args.wall_area
    # A floor is thin evenly. A level face running out to a shallow slope is a feather edge, thin
    # from nothing upward: real, and for a slicer to blunt, but it isn't a floor.
    is_floor = False
    if flat_thin.size >= MIN_RAYS:
        middle = float(np.median(flat_thin))
        is_floor = float((np.abs(flat_thin - middle) <= 0.2 * middle).mean()) >= 0.5
    if flat_thin.size and not is_floor:
        report.note(
            f"{flat_thin.size} ray(s) up or down read under {layer} mm, the least "
            f"{float(flat_thin.min()):.3f}: a feather edge or a few rays, and not a floor"
        )
    if alone and not wall_counts:
        report.note(
            f"{len(alone)} ray(s) read under {nozzle} mm, too few to be a wall: a wedge where "
            f"walls meet, at {alone[:3]}"
        )
    if tip_rows:
        report.note(
            f"{tips_per_layer} sharp point(s) come to less than the nozzle's width. A slicer "
            "blunts them: that's normal for a sharp corner, so they aren't counted as thin walls"
        )
    report.fact(
        "thin_wall_area_mm2", round(wall_thin, 2), how=f"{args.samples} rays through the walls"
    )
    report.fact("thinnest_wall_mm", round(float(wall.min()), 4) if wall.size else None)
    report.fact(
        "thinnest_flat_mm",
        round(float(flat.min()), 4) if flat.size else None,
        how="rays up and down",
    )
    report.second(
        f"rays across {len(widths)} thin spot(s), {unconfirmed} wider than the nozzle; rays "
        f"through the surface find {wall_thin:.2f} mm² of wall under {nozzle} mm"
    )
    bodies = _bodies(part, built, args, report)
    problems = []
    if thin_rows:
        problems.append(
            f"{len(thin_rows)} spot(s) narrower than the {nozzle} mm nozzle, the thinnest "
            f"{min(widths):.3f} mm"
            if widths
            else f"{len(thin_rows)} spot(s) narrower than the nozzle"
        )
    if too_small_rows:
        problems.append(f"{len(too_small_rows)} section piece(s) vanish at the nozzle's width")
    if is_floor:
        problems.append(
            f"a flat part is {float(np.median(flat_thin)):.3f} mm thick, under the {layer} mm layer"
        )
    problems += bodies["problems"]
    differs = list(bodies["differs"])
    if unconfirmed:
        differs.append(f"{unconfirmed} spot(s) the sections call thin measure wider by rays")
    if not thin_rows and wall_counts:
        differs.append(
            f"rays find {wall_alone:.2f} mm² of wall under {nozzle} mm away from any sharp "
            "point, and the sections no thin spot"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"nothing narrower than the {nozzle} mm nozzle")


def _through(
    mesh: trimesh.Trimesh, built: Any, samples: int, length: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rays inward from random surface points: thickness of walls, of flat parts, and where
    each wall's ray started.

    A wall's ray runs level, across the layer, whatever the wall's slope: that's the width a
    slicer has to fill, and the question the sections answer. Along the surface's normal, a
    cone or a thread reads thinner than it prints.
    """
    points, faces = trimesh.sample.sample_surface(mesh, samples, seed=7)
    normals = np.asarray(mesh.face_normals)[faces]
    walls, flats, starts = [], [], []
    for point, normal in zip(points, normals, strict=True):
        # Only a level face is a floor: a shallow slope, like a thread's flank, runs out to an
        # edge, and any ray straight through it near there reads thin.
        if abs(normal[2]) > 0.9999:
            start = point - normal * 1e-5
            met = hits(built, start, -normal, length)
            if met:
                flats.append(met[0])
            continue
        # Nor is a shallow slope a wall. Near the top of a cylinder lying on its side, a level ray
        # crosses a tiny chord, in a strip less than a layer tall that no layer ever prints.
        if abs(normal[2]) > WALL_SLOPE:
            continue
        level = np.array([normal[0], normal[1], 0.0])
        level /= np.linalg.norm(level)
        met = hits(built, point - level * 1e-5, -level, length)
        if met:
            walls.append(met[0])
            starts.append(point)
    return np.array(walls), np.array(flats), np.array(starts).reshape(-1, 3)


def _bodies(part: Part, built: Any, args: Any, report: Report) -> dict[str, list[str]]:
    """Each separate body, by the mesh and by OCCT, against the smallest worth printing."""
    bodies = built.decompose()
    volumes = sorted(round(float(b.volume()), 3) for b in bodies)
    report.fact("bodies", len(bodies), how="mesh")
    report.fact("body_volumes_mm3", volumes[:20])
    problems, differs = [], []
    small = [v for v in volumes if v < args.min_body]
    if small:
        problems.append(f"{len(small)} body(ies) under {args.min_body} mm³, the least {small[0]}")
    if part.is_brep:
        solids = len(part.shape.Solids())
        report.second(f"OCCT has {solids} solid(s)")
        if solids != len(bodies):
            differs.append(f"OCCT has {solids} solid(s), the mesh {len(bodies)} body(ies)")
    return {"problems": problems, "differs": differs}


# The pause layer


def layer_tops(first: float, height: float, base: float, count: int) -> np.ndarray:
    return base + first + height * np.arange(count)


def run_pause(args: Any, report: Report) -> None:
    part = load(args.frame)
    built = part.manifold()
    mesh = part.fine()
    low, high = np.asarray(mesh.bounds)[:, 2]
    first, height = args.first_layer, args.layer
    # Rounded first: 4.8 / 0.2 is a hair over 24, and a layer past the top reads as closed.
    count = int(np.ceil(round((high - low - first) / height, 6))) + 1
    tops = layer_tops(first, height, low, count)
    mids = np.r_[low + first / 2, tops[:-1] + height / 2]
    _between_faces(mesh, args.pocket_z, "pocket-z")
    section = slice_at(built, args.pocket_z)
    openings = _openings(section)
    if not openings:
        raise NoAnswer(f"the section at z={args.pocket_z} has no closed opening")
    report.fact("layers", count, how=f"{first} mm then {height} mm, from z={low:.3f}")
    report.note(
        "worked out from the file's heights. Nothing was sliced, and no printer was touched"
    )
    # A layer's mid-height may land on a level face, where a section could read either side.
    # Reading just above it treats that layer as closed, which pauses earlier: the safe way.
    rows, sections = [], [slice_at(built, float(z) + TIE) for z in mids]
    problems: list[str] = []
    differs: list[str] = []
    for k, opening in enumerate(openings):
        row = _pause_one(k, opening, sections, mids, tops, mesh, args, problems, differs)
        rows.append(row)
    report.numbers["openings"] = rows
    closes = {r["pause_after_layer"] for r in rows if r["pause_after_layer"] is not None}
    open_ones = [r["opening"] for r in rows if r["pause_after_layer"] is None]
    if open_ones and closes:
        report.note(f"nothing closes over opening(s) {', '.join(open_ones)}")
    if not closes:
        report.second("flat faces agree: nothing closes over the openings")
        if not differs:
            report.verdict("OK", "no layer closes over the openings: a pane can go in afterwards")
        else:
            report.verdict("DISAGREE", "; ".join(differs))
        return
    closing = [r for r in rows if r["pause_after_layer"] is not None]
    report.fact("openings_closing", len(closing))
    alike = {(r["pause_after_layer"], r["pause_after_layer_faces"]) for r in closing}
    for row in closing[:1] if len(alike) == 1 else closing:
        report.line(_pause_line(row))
    for tie in sorted({r["tie"] for r in rows if "tie" in r}):
        report.note(tie)
    if any("stands_on" in r for r in rows):
        report.note("a pane in an opening with no floor stands on the build plate")
    report.second(
        "from flat faces: "
        + ", ".join(sorted({str(r["pause_after_layer_faces"]) for r in closing}))
        + f"; from sections layer by layer: {', '.join(sorted(str(c) for c in closes))}"
    )
    if len(closes) > 1:
        problems.append(f"the openings close at different layers: {sorted(map(str, closes))}")
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems[:5]))
    else:
        first_row = closing[0]
        report.verdict(
            "OK",
            f"pause after layer {first_row['pause_after_layer']}, at z={first_row['pause_top_z']}: "
            f"the pane sits {first_row['room_over_pane_mm']} mm under the next layer",
        )


def _pause_one(
    k: int,
    opening: Any,
    sections: list[Any],
    mids: np.ndarray,
    tops: np.ndarray,
    mesh: trimesh.Trimesh,
    args: Any,
    problems: list[str],
    differs: list[str],
) -> dict[str, Any]:
    """One opening's pause layer, by its sections layer by layer and by its flat faces."""
    name = str(k + 1)
    point = opening.representative_point()
    heights = pocket_heights(mesh, opening, args.pocket_z)
    areas = []
    for section in sections:
        found = next(
            (
                shapely.Polygon(r)
                for poly in section.geoms
                for r in poly.interiors
                if shapely.Polygon(r).contains(point)
            ),
            None,
        )
        areas.append(float(found.area) if found is not None else 0.0)
    here = int(np.searchsorted(mids, args.pocket_z))
    here = min(here, len(mids) - 1)
    full = float(opening.area)
    # Sections: the first layer above the pocket that doesn't leave the whole opening free.
    closing = next(
        (i for i in range(here, len(mids)) if areas[i] < full - max(1e-2, 1e-3 * full)), None
    )
    # Flat faces: the same thing from the ceiling's height and the rule that a layer takes its
    # outline from its mid-height.
    closing_faces = None
    if heights.ceiling is not None:
        above = np.nonzero(mids + TIE > heights.ceiling)[0]
        closing_faces = int(above[0]) if above.size else None
    row: dict[str, Any] = {
        "opening": name,
        "pocket_floor_z": heights.floor,
        "pocket_ceiling_z": heights.ceiling,
        "pause_after_layer": closing,
        "pause_after_layer_faces": closing_faces,
    }
    if (closing is None) != (closing_faces is None) or (
        closing is not None and closing != closing_faces
    ):
        differs.append(
            f"opening {name} closes after layer {closing} by sections, "
            f"{closing_faces} by flat faces"
        )
    if closing is None:
        return row
    if heights.ceiling is not None and abs(float(mids[closing]) - heights.ceiling) <= TIE:
        row["tie"] = (
            f"layer {closing + 1}'s mid-height is on the ceiling, so a slicer could close it there "
            "or one layer later: pause here is the earlier"
        )
    # Layers count from 1: the layer closing it is number closing + 1, the pause after `closing`.
    top = float(tops[closing - 1])
    row["pause_top_z"] = round(top, 4)
    floor = heights.floor if heights.floor is not None else float(mesh.bounds[0][2])
    pane_top = floor + args.pane_thickness
    row["pane_top_z"] = round(pane_top, 4)
    row["room_over_pane_mm"] = round(top - pane_top, 4)
    row["pocket_height_mm"] = (
        round(heights.ceiling - floor, 4) if heights.ceiling is not None else None
    )
    if heights.floor is None:
        row["stands_on"] = "the build plate: the opening runs to the part's underside"
    if pane_top > top + LEVEL:
        problems.append(
            f"opening {name}: the pane's top is at {pane_top:.3f}, above the pause at {top:.3f}: "
            "the nozzle would hit it"
        )
    elif top - pane_top > args.max_gap:
        problems.append(
            f"opening {name}: {top - pane_top:.3f} mm above the pane at the pause, over "
            f"{args.max_gap} mm: the next layer bridges too far above it"
        )
    return row


def _pause_line(row: dict[str, Any]) -> str:
    return (
        f"opening {row['opening']}: floor z={row['pocket_floor_z']}, ceiling "
        f"z={row['pocket_ceiling_z']}, pause after layer {row['pause_after_layer']} "
        f"(top z={row.get('pause_top_z')}), pane top z={row.get('pane_top_z')}, room over it "
        f"{row.get('room_over_pane_mm')} mm"
    )
