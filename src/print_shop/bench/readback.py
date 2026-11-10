"""Read-back: a part's facts, volume before and after an operation, and rays on a cut feature.

Each row here is something a tool reported as success when it was wrong: a pocket that missed
the body ("feature valid"), a repair that kept 5% of the volume ("watertight"), a shell whose
CAD volume swung ("a plain number"), a seat mostly in mid-air ("no error, watertight, one
body").
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import shapely

from . import strips
from .limits import over, under
from .report import Report
from .shapes import (
    VOLUME_FLOOR,
    VOLUME_SHARE,
    NoAnswer,
    Part,
    diagonal,
    flat_heights,
    hits,
    load,
    slice_at,
    to_manifold,
)

#: Sections stacked for an STL's second volume.
SECTIONS = 128
#: A stack's share of a volume that's only its steps, for an STL.
STACK_SHARE = 0.005
#: With no change stated, losing or gaining this share of the volume needs saying first.
LARGE_CHANGE = 0.5
def volumes(part: Part) -> dict[str, Any]:
    """The volume by two methods, and whether they agree. Raises when the mesh isn't closed."""
    coarse, fine = part.meshes()
    found: dict[str, Any] = {"watertight": bool(fine.is_watertight)}
    mesh = float(fine.volume)
    if not fine.is_watertight:
        said = ""
        if part.is_brep:
            said = (
                f"; OCCT says {float(part.shape.Volume()):.3f} mm³ and valid "
                f"{bool(part.shape.isValid())}, with no closed mesh behind it"
            )
        raise NoAnswer(
            f"{part.name}'s surface isn't closed ({len(fine.faces)} triangles), so it holds no "
            f"volume to read: the mesh's arithmetic gives {mesh:.3f} mm³ and means nothing{said}"
        )
    found["mesh_mm3"] = round(mesh, 4)
    found["bodies_mesh"] = len(part.manifold().decompose())
    if part.is_brep:
        exact = float(part.shape.Volume())
        band = max(
            VOLUME_FLOOR,
            VOLUME_SHARE * abs(mesh),
            part.chord_band(),
            2 * abs(float(coarse.volume) - mesh),
        )
        found.update(occt_mm3=round(exact, 4), band_mm3=round(band, 4), other="OCCT")
        found.update(valid=bool(part.shape.isValid()), solids=len(part.shape.Solids()))
        found["agrees"] = abs(exact - mesh) <= band and exact > 0
        found["other_mm3"] = exact
        found["exact_mm3"] = exact
        found["resolved_mm3"] = band
        return found
    low, high = np.asarray(fine.bounds)[:, 2]
    step = (high - low) / SECTIONS
    built = part.manifold()
    stacked = step * sum(
        float(slice_at(built, low + (k + 0.5) * step).area) for k in range(SECTIONS)
    )
    band = max(VOLUME_FLOOR, STACK_SHARE * abs(mesh))
    found.update(sections_mm3=round(stacked, 4), band_mm3=round(band, 4), other="sections")
    found["agrees"] = abs(stacked - mesh) <= band and mesh > 0
    found["other_mm3"] = stacked
    found["exact_mm3"] = mesh
    # An STL's mesh is the part: its own arithmetic is exact, and the stack only a cross-check.
    found["resolved_mm3"] = max(VOLUME_FLOOR, VOLUME_SHARE * abs(mesh))
    return found


def _bounds(part: Part) -> list[list[float]]:
    found: list[list[float]] = (np.round(part.extent(), 4) + 0.0).tolist()
    return found


def small_faces(part: Part, path: str, report: Report) -> None:
    """How many flat faces are under 1 mm across, how many of them lie in runs of strips, and how
    big the file is: a reading, not a failure (mqucifer/print-shop#54, mqucifer/print-shop#85). A
    curve built from a polygon is stored as a strip for each segment: an 80 mm ring at 96 segments
    a quarter is 770 flat faces and 3 MB, where two circles make 4 faces and 10 kB. A slicer can't
    fit arcs to it, and the file drags. A beam's narrow side is a small flat face too, but not a
    strip: strips come in runs, each turning a few degrees to the next (`strips`)."""
    report.fact("file_kb", round(os.path.getsize(path) / 1024, 1), how="on the disk")
    if not part.is_brep:
        report.note("an STL has no faces of its own, only triangles, so no flat faces are read")
        return
    flat = [f for f in part.shape.Faces() if f.geomType() == "PLANE"]
    widths, found = strips.by_topology(part.shape, flat)
    small = sum(1 for w in widths if w < strips.SMALL_FACE_MM)
    lengths = [len(r) for r in found]
    report.fact("flat_faces", len(flat), how="OCCT's planes")
    report.fact(
        "small_flat_faces",
        small,
        how=f"flat faces under {strips.SMALL_FACE_MM:g} mm across their plane, by OCCT's "
        "oriented box",
    )
    counted = strips.narrow_in(found, widths)
    report.fact(
        "strip_faces",
        counted,
        how=f"small flat faces in runs of strips: {strips.RUN_LEAST} flat faces or more, each a "
        f"neighbour of the next by OCCT's edges, turning under {strips.TURN_DEG:g}° to it",
    )
    report.fact("strip_runs", lengths, how="each run's flat faces, longest first")
    drawn_widths, drawn = strips.by_points(flat)
    drawn_small = sum(1 for w in drawn_widths if w < strips.SMALL_FACE_MM)
    drawn_lengths = [len(r) for r in drawn]
    drawn_counted = strips.narrow_in(drawn, drawn_widths)
    report.second(
        f"flat faces under {strips.SMALL_FACE_MM:g} mm by points along their edges: "
        f"{drawn_small}; by the same points, {strips.said(drawn_lengths)}"
        + (f" {drawn_lengths[:8]}, {drawn_counted} small faces in them" if drawn_lengths else "")
    )
    if drawn_small != small:
        report.note(
            f"the two readings of small flat faces differ, {small} by the box and {drawn_small} "
            "by the points: a face near 1 mm across reads either side"
        )
    if drawn_lengths != lengths or drawn_counted != counted:
        report.note(
            f"the two readings of strips differ: {strips.said(lengths)} {lengths[:8]}, "
            f"{counted} small faces in them, by OCCT's edges, and {strips.said(drawn_lengths)} "
            f"{drawn_lengths[:8]}, {drawn_counted} small, by points along them"
        )
    if lengths:
        report.note(
            f"{strips.said(lengths)}, {counted} of {small} flat face(s) under "
            f"{strips.SMALL_FACE_MM:g} mm across: a curve stored as flat strips, a face for each "
            "segment of a polygon. Build a curve with CadQuery's circles and arcs (the skill "
            "`building-curves-from-a-layout`), not `polyline` through a polygon's points"
        )
    elif small:
        report.note(
            f"{small} flat face(s) under {strips.SMALL_FACE_MM:g} mm across, narrow flat faces "
            "only, no strips: none is in a run of neighbours each turning a few degrees to the "
            "next, as a polygon's strips are. A beam's or a slot's narrow side, or a seam where "
            "fillets meet, reads so"
        )


def run_info(args: Any, report: Report) -> None:
    part = load(args.file)
    report.notes += part.notes
    report.fact("bounds_mm", _bounds(part))
    size = (part.extent()[1] - part.extent()[0]).round(4).tolist()
    report.fact("size_mm", size)
    if part.is_brep:
        report.fact("solids", len(part.shape.Solids()))
        report.fact("faces", len(part.shape.Faces()))
        report.fact("valid", bool(part.shape.isValid()), how="OCCT")
    fine = part.fine()
    report.fact("triangles", len(fine.faces))
    report.fact("watertight", bool(fine.is_watertight))
    report.fact("flat_heights", flat_heights(fine), how="z and mm² of level faces up and down")
    small_faces(part, args.file, report)
    found = volumes(part)
    report.fact("volume_mesh_mm3", found["mesh_mm3"], how="mesh, divergence theorem")
    report.fact("bodies_mesh", found["bodies_mesh"])
    report.second(f"volume by {found['other']}: {found['other_mm3']:.4f} mm³")
    if found["mesh_mm3"] < 0:
        report.note("the mesh's volume is negative: its surface is inside out")
    if not found["agrees"]:
        report.verdict(
            "DISAGREE",
            f"the volume is {found['mesh_mm3']:.3f} mm³ by the mesh and "
            f"{found['other_mm3']:.3f} mm³ by {found['other']}",
        )
    elif part.is_brep and not found["valid"]:
        report.verdict("FAIL", "OCCT says the shape isn't valid")
    elif part.is_brep and found["solids"] != found["bodies_mesh"]:
        report.verdict(
            "DISAGREE",
            f"OCCT has {found['solids']} solid(s), the mesh {found['bodies_mesh']} body(ies)",
        )
    else:
        report.verdict("OK", f"{found['mesh_mm3']:.3f} mm³, in {found['bodies_mesh']} body(ies)")


def run_readback(args: Any, report: Report) -> None:
    before, after = load(args.before), load(args.after)
    report.notes += before.notes + after.notes
    was, now = volumes(before), volumes(after)
    delta = now["mesh_mm3"] - was["mesh_mm3"]
    other = now["other_mm3"] - was["other_mm3"]
    band = max(was["band_mm3"], now["band_mm3"])
    report.fact("before_mm3", was["mesh_mm3"], how="mesh")
    report.fact("after_mm3", now["mesh_mm3"], how="mesh")
    resolved = max(was["resolved_mm3"], now["resolved_mm3"])
    report.fact("change_mm3", delta, how=f"mesh, ± {resolved:.4f}")
    if was["mesh_mm3"]:
        report.fact("change_pct", round(100 * delta / was["mesh_mm3"], 3))
    report.fact("bodies_before_after", [was["bodies_mesh"], now["bodies_mesh"]])
    report.fact("bounds_after_mm", _bounds(after))
    report.second(
        f"by {was['other']} before {was['other_mm3']:.4f} mm³, by {now['other']} after "
        f"{now['other_mm3']:.4f} mm³, change {other:.4f} mm³"
    )
    problems: list[str] = []
    if not (was["agrees"] and now["agrees"]) or abs(delta - other) > 2 * band:
        report.verdict(
            "DISAGREE", f"the change is {delta:.3f} mm³ by the mesh and {other:.3f} by the other"
        )
        return
    if args.cutter:
        share = _cutter(before, load(args.cutter), delta, band, report, problems)
        if args.min_share is not None and under(share, args.min_share):
            problems.append(f"only {share:.1%} of the cutter is in the body")
    if args.expect_delta is not None:
        tol = args.tol if args.tol is not None else max(resolved, 0.001 * abs(args.expect_delta))
        report.fact("expected_change_mm3", args.expect_delta, how=f"± {tol:.4f}")
        if over(abs(delta - args.expect_delta), tol):
            problems.append(f"the change is {delta:.3f} mm³, and {args.expect_delta} was expected")
    elif abs(now["exact_mm3"] - was["exact_mm3"]) <= VOLUME_FLOOR:
        report.verdict("NO CHANGE", f"the volume moved {delta:.4f} mm³: the operation did nothing")
        return
    elif abs(delta) >= LARGE_CHANGE * abs(was["mesh_mm3"]):
        problems.append(
            f"it changed {abs(delta) / abs(was['mesh_mm3']):.0%} of the volume: pass "
            "--expect-delta if that was meant"
        )
    if problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"the volume moved {delta:.3f} mm³")


def _cutter(
    before: Part, cutter: Part, delta: float, band: float, report: Report, problems: list[str]
) -> float:
    """What a cutter should have taken, and how much of it was in the body at all."""
    overlap = float((before.manifold() ^ cutter.manifold()).volume())
    total = float(cutter.manifold().volume())
    share = overlap / total if total else 0.0
    report.fact("cutter_mm3", round(total, 4), how="mesh")
    report.fact("cutter_in_body_mm3", round(overlap, 4), how="mesh")
    report.fact("cutter_in_body_share", round(share, 4))
    if before.is_brep and cutter.is_brep:
        exact = float(before.shape.intersect(cutter.shape).Volume())
        report.second(f"cutter in the body by OCCT: {exact:.4f} mm³")
        if abs(exact - overlap) > max(band, VOLUME_SHARE * overlap):
            problems.append(
                f"OCCT puts {exact:.3f} mm³ of the cutter in the body, the mesh {overlap:.3f}"
            )
    if overlap <= band:
        problems.append("the cutter missed the body")
    if abs(-delta - overlap) > max(2 * band, 0.01 * overlap):
        problems.append(f"it removed {-delta:.3f} mm³ where the cutter overlaps {overlap:.3f} mm³")
    return share


def _frame(axis: str) -> tuple[list[int], str]:
    """A cyclic swap that takes the feature's axis to z, keeping handedness, and its name."""
    return {"z": ([0, 1, 2], "x y z"), "x": ([1, 2, 0], "y z x"), "y": ([2, 0, 1], "z x y")}[axis]


def _turned(part: Part, order: list[int]) -> Any:
    mesh = part.fine().copy()
    mesh.vertices = np.asarray(mesh.vertices)[:, order]
    return mesh


def _radial(
    built: Any, centre: np.ndarray, count: int, length: float
) -> tuple[np.ndarray, np.ndarray, float]:
    """Rays out from a point in the plane: first hit, second hit, share starting in material."""
    first, second = np.full(count, np.nan), np.full(count, np.nan)
    inside = 0
    for i, angle in enumerate(np.linspace(0, 2 * np.pi, count, endpoint=False)):
        ray = np.array([np.cos(angle), np.sin(angle), 0.0])
        found = hits(built, centre, ray, length)
        if found:
            first[i] = found[0]
        if len(found) > 1:
            second[i] = found[1]
        inside += len(found) % 2
    return first, second, inside / count


def _void_at(section: Any, point: Any) -> Any:
    """The hole in a section's material around a point, or None if it isn't closed."""
    for poly in section.geoms:
        for ring in poly.interiors:
            hole = shapely.Polygon(ring)
            if hole.contains(point):
                return hole
    return None


def _circle(points: np.ndarray) -> tuple[np.ndarray, float]:
    """A least-squares circle: centre and diameter."""
    a = np.c_[2 * points, np.ones(len(points))]
    solved = np.linalg.lstsq(a, (points**2).sum(1), rcond=None)[0]
    return solved[:2], float(2 * np.sqrt(solved[2] + (solved[:2] ** 2).sum()))


def run_rays(args: Any, report: Report) -> None:
    """Rays from a cut feature's axis, against a plane section of the same mesh."""
    part = load(args.file)
    order, names = _frame(args.axis)
    mesh = _turned(part, order)
    built = to_manifold(mesh, part.name)
    centre = np.asarray(args.centre, dtype=float)[order]
    length = 2 * diagonal(mesh)
    tol = args.tol
    levels = [centre[2] + f * args.depth for f in (0.1, 0.5, 0.9)] if args.depth else [centre[2]]
    if args.axis != "z":
        report.note(f"each 'at' is a position along {args.axis}, the feature's axis")
    report.numbers["axes"] = names
    levels_found = []
    problems: list[str] = []
    differs: list[str] = []
    for flat in flat_heights(mesh, least=0.01):
        for z in levels:
            if abs(flat["z"] - z) <= 1e-3:
                raise NoAnswer(
                    f"the height {z:g} along {args.axis} is on a level face, so a ray there could "
                    "read either side: start inside the feature, or give --depth"
                )
    for z in levels:
        at = np.array([centre[0], centre[1], z])
        first, second, inside = _radial(built, at, args.n, length)
        missed = int(np.isnan(first).sum())
        level: dict[str, Any] = {
            "at": round(float(z), 4),
            "rays": args.n,
            "missed": missed,
            "start_in_material": round(inside, 3),
        }
        half = args.n // 2
        across = first[:half] + first[half : half * 2]
        if np.isfinite(across).any():
            # The figures `--d` and `--tol` judge, as they are: a need reads the same (`limits`).
            level["across_mm"] = {
                "min": float(np.nanmin(across)),
                "mean": round(float(np.nanmean(across)), 4),
                "max": float(np.nanmax(across)),
            }
        walls = second - first
        if np.isfinite(walls).any():
            level["wall_beyond_min_mm"] = round(float(np.nanmin(walls)), 4)
        if np.isfinite(first).any():
            level["nearest_wall_mm"] = round(float(np.nanmin(first)), 4)
        section = slice_at(built, float(z))
        point = shapely.Point(at[0], at[1])
        hole = _void_at(section, point)
        level["closed_loop_around_centre"] = hole is not None
        if hole is not None:
            ring = np.asarray(hole.exterior.coords)[:-1]
            level["section_area_mm2"] = round(float(hole.area), 4)
            if args.d:
                fit_centre, fit_d = _circle(ring)
                level["section_fit_d_mm"] = round(fit_d, 4)
                level["section_fit_offset_mm"] = round(
                    float(np.linalg.norm(fit_centre - at[:2])), 4
                )
            angles = np.linspace(0, 2 * np.pi, args.n, endpoint=False)
            step = max(1, args.n // 72)
            worst = 0.0
            for angle, hit in zip(angles[::step], first[::step], strict=True):
                if np.isnan(hit):
                    continue
                far = (at[0] + length * np.cos(angle), at[1] + length * np.sin(angle))
                crossed = shapely.intersection(shapely.LineString([at[:2], far]), hole.exterior)
                worst = max(worst, abs(float(shapely.distance(point, crossed)) - float(hit)))
            level["ray_vs_section_worst_mm"] = round(worst, 4)
            if worst > 2 * tol:
                differs.append(f"at {z:.3f} rays and the section differ by {worst:.3f} mm")
        levels_found.append(level)
        report.line(_level_line(level))
        in_material = inside > 0.5
        if in_material:
            problems.append(f"at {z:.3f} the centre is in material: no feature there")
        elif missed:
            problems.append(f"at {z:.3f}, {missed} of {args.n} rays hit nothing: it's open to air")
        if (hole is not None) == bool(missed) and not in_material:
            differs.append(f"at {z:.3f} {missed} rays missed, closed section: {hole is not None}")
        rays_right = None
        if args.d and "across_mm" in level and not missed and not in_material:
            span = level["across_mm"]
            rays_right = not (under(span["min"], args.d - tol) or over(span["max"], args.d + tol))
            if not rays_right:
                problems.append(
                    f"at {z:.3f} it measures {span['min']:.3f} to {span['max']:.3f} mm across, "
                    f"against {args.d} ± {tol}"
                )
        if args.d and "section_fit_d_mm" in level and not missed:
            fit = level["section_fit_d_mm"]
            report.second(f"section at {z:.3f}: a circle fit gives d {fit}")
            if rays_right is not None and rays_right != (not over(abs(fit - args.d), tol)):
                differs.append(f"at {z:.3f} the rays and the section fit ({fit} mm) differ")
        elif hole is not None:
            report.second(
                f"section at {z:.3f}: closed hole of {level['section_area_mm2']} mm², within "
                f"{level['ray_vs_section_worst_mm']} mm of the rays"
            )
        else:
            report.second(f"section at {z:.3f}: no closed hole around the centre")
    report.numbers["levels"] = levels_found
    in_air = [lv for lv in levels_found if lv["start_in_material"] <= 0.5 and "across_mm" in lv]
    if in_air:
        report.note(in_air_said(in_air[0]))
    if args.depth:
        _floor(built, centre, args, length, report, problems)
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        report.verdict("OK", f"rays met the feature's wall all round at {len(levels)} height(s)")


def in_air_said(level: dict[str, Any]) -> str:
    """What `across` measures from a point in air (mqucifer/print-shop#65). On `eval-edges-1` it
    was read from a rounded corner's centre, which lay in the box's hollow, as the corner's
    radius, over three long calls: it was the hollow's width."""
    span = level["across_mm"]
    low, high = span["min"], span["max"]
    return (
        f"the centre is in air, so `across` is the open space's width through it: from the wall "
        f"one ray meets to the wall the opposite ray meets, {low:.4f} to {high:.4f} mm "
        f"here, and `nearest_wall_mm`, {level.get('nearest_wall_mm')} mm, how far the nearest "
        "wall is. Centred on a hole's or a bore's axis that's its diameter and radius, and "
        "anywhere else it's the width of whatever space the point is in. It isn't an outside "
        "corner's radius: a rounded outside corner's centre is inside the part, in its material "
        "or its hollow, and rays from there meet the walls round it, not the rounding. `edges` "
        "reads an outside corner's radius"
    )


def _level_line(level: dict[str, Any]) -> str:
    text = f"at {level['at']}: {level['missed']} of {level['rays']} rays missed"
    if "across_mm" in level:
        span = level["across_mm"]
        text += (
            f", across {span['min']:.4f} / {span['mean']} / {span['max']:.4f} mm "
            "(min / mean / max)"
        )
    if "wall_beyond_min_mm" in level:
        text += f", wall beyond at least {level['wall_beyond_min_mm']} mm"
    if "nearest_wall_mm" in level and level["start_in_material"] <= 0.5:
        text += f", nearest wall {level['nearest_wall_mm']} mm"
    return text


def _floor(
    built: Any,
    centre: np.ndarray,
    args: Any,
    length: float,
    report: Report,
    problems: list[str],
) -> None:
    """Rays along the axis, off-centre: how deep the feature goes, measured."""
    radius = 0.8 * (args.d / 2 if args.d else 1.0)
    found = []
    direction = np.array([0.0, 0.0, 1.0 if args.depth > 0 else -1.0])
    for angle in np.linspace(0, 2 * np.pi, 64, endpoint=False):
        start = centre + radius * np.array([np.cos(angle), np.sin(angle), 0.0])
        met = hits(built, start, direction, length)
        found.append(met[0] if met else np.nan)
    depths = np.array(found)
    if np.isnan(depths).all():
        report.line("floor: none met along the axis: it goes right through")
        report.numbers["floor_mm"] = None
        return
    report.fact(
        "floor_mm",
        {"min": float(np.nanmin(depths)), "max": round(float(np.nanmax(depths)), 4)},
    )
    if over(abs(float(np.nanmin(depths)) - abs(args.depth)), args.tol):
        problems.append(
            f"the floor is {np.nanmin(depths):.3f} mm in, and {abs(args.depth)} was asked"
        )
