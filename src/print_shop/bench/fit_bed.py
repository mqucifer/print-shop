"""`fit-bed`: each part against the bed, and the side it's laid on (mqucifer/print-shop#156).

A side table is bigger than the bed, so it's split into parts. This reads whether each part of a
split fits, and on which side.

**The sides.** A part lies on a flat face that the rest of it stands wholly above. Given STEP
files, every such face is a side it could lie on, and the best is the one that fits with the most
flat on the bed, which is what holds a print down. Given an assembly file, each part's `layers`
says the way up it's printed, so only the two faces square to that are read, and a part with no
flat face there is said.

**Whether it fits.** Laid on a side, the part is turned about the bed's upright axis to where its
footprint fits, by the least turn that does: the fine mesh's points seen from above, their hull
turned a quarter of a degree at a time and to each of its own edges. A part longer than the bed
is wide can still fit corner to corner. Where no turn fits, it's the one that needs least of the
bed. Its height is its extent above the side. It fits where its
footprint is within the bed's width and depth and its height within the bed's height: the whole
bed, with no margin kept for a brim or a skirt, which the slicer adds.

**Second method:** the exact solid turned the same way and measured by OpenCascade's own box
round it, with no mesh. The two differ by the mesh's chords on a curved part, and not at all on a
flat-sided one; where they land on opposite sides of the bed's size it's `DISAGREE`.

It reads the part as drawn: what a slicer adds round it, and whether the side needs supports,
aren't in it.

**A nut's pocket** (mqucifer/print-shop#168). Where an assembly's `through` joint states a
`pocket`, its nut sits in one in the second part, held by its flats. The pocket is read from
that part's own flat faces (`through.py`): the walls that stand on the hole's end, with their
faces at the hole, and face each other across its axis. A face is at the hole where, along its
wall, it comes as near as the nut's corners reach (mqucifer/print-shop#204). It fails where the
nut doesn't go in, by its flats or by its
corners, where it's wide enough for the nut to turn, and where its play isn't the file's. The
corners (mqucifer/print-shop#183): the nut as a hexagon at its least size across them must clear
every wall in some turn, its middle on the bolt's axis or as far off it as the bolt moves in its
hole. Second method: the fine mesh cut square to the bolt half way up the pocket, the nearest
that outline comes to the axis, and how far it reaches into the nut's hexagon placed the same.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import MultiPoint

from . import through
from .assembly import AXES, DIAMETER, Assembly, Joint, read
from .faces import _cylinders, _flat_by_mesh, _flats, facing
from .limits import over, said
from .report import NoAnswer, Report
from .shapes import Part, load

#: How far the footprint is turned between readings, in degrees.
TURN = 0.25
#: A face is a side to lie on where nothing of the part is below it by more than this, in mm.
BELOW = 1e-3
#: A side has at least this much flat on the bed, in mm².
LEAST_FLAT = 1.0
#: How many sides a reply names for a part; every one is in the record.
SIDES_SAID = 3
#: A pocket's nearest wall by its faces and by the mesh's section are one within this, in mm.
POCKET_AGREE = 0.01


def _down(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """The turn that lays a face on the bed, its outward normal straight down: as a matrix, and
    as the axis and the angle in degrees OpenCascade turns by."""
    target = np.array([0.0, 0.0, -1.0])
    axis = np.cross(normal, target)
    sine, cosine = float(np.linalg.norm(axis)), float(normal @ target)
    if sine < 1e-12:
        axis, angle = np.array([1.0, 0.0, 0.0]), 0.0 if cosine > 0 else math.pi
    else:
        axis, angle = axis / sine, math.atan2(sine, cosine)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    matrix = np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)
    return matrix, axis, math.degrees(angle)


def footprint(points: np.ndarray, bed: tuple[float, float]) -> tuple[float, float, float]:
    """The least turn about the upright axis at which a footprint fits the bed, in degrees, or
    where none does, the turn at which it needs least of it; and its width and depth there.
    `points` are the part's points seen from above."""
    hull = MultiPoint(points).convex_hull
    outline = np.asarray(hull.exterior.coords if hull.geom_type == "Polygon" else hull.coords)
    edges = np.diff(outline, axis=0)
    angles = set(np.arange(0.0, 180.0, TURN).round(6))
    # Each of the hull's own edges laid along the bed's width, and along its depth.
    for dx, dy in edges:
        if abs(dx) + abs(dy) > 1e-9:
            turn = -math.degrees(math.atan2(dy, dx))
            angles.update((round(turn % 180.0, 6), round((turn + 90.0) % 180.0, 6)))
    best: tuple[float, float, float, float] | None = None
    for angle in sorted(angles):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        turned = outline @ np.array([[c, s], [-s, c]])
        width, depth = float(np.ptp(turned[:, 0])), float(np.ptp(turned[:, 1]))
        if not over(width, bed[0]) and not over(depth, bed[1]):
            # The least turn that fits: as it's drawn, where that does.
            return angle, width, depth
        need = max(width / bed[0], depth / bed[1])
        if best is None or need < best[0] - 1e-12:
            best = (need, angle, width, depth)
    assert best is not None
    return best[1], best[2], best[3]


def _box(shape: Any) -> list[float]:
    """A solid's size along x, y and z by OpenCascade's own box round its exact surface, with no
    mesh and no margin: CadQuery's box is a triangulation's, padded."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    box.SetGap(0.0)
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    low_x, low_y, low_z, high_x, high_y, high_z = box.Get()
    return [high_x - low_x, high_y - low_y, high_z - low_z]


def _side(part: Part, plane: dict[str, Any], bed: list[float]) -> dict[str, Any] | None:
    """A part laid on one flat face: its footprint turned to fit, its height, and the same by
    OpenCascade's box round the exact solid. None where the part isn't wholly above the face."""
    import cadquery as cq

    mesh = part.fine()
    normal = np.asarray(plane["normal"], dtype=float)
    points = np.asarray(mesh.vertices)
    if float((points @ normal).max()) > plane["offset"] + BELOW:
        return None
    matrix, axis, degrees = _down(normal)
    laid = points @ matrix.T
    turn, width, depth = footprint(laid[:, :2], (bed[0], bed[1]))
    height = float(np.ptp(laid[:, 2]))
    exact = part.shape
    if degrees:
        exact = exact.rotate(cq.Vector(0, 0, 0), cq.Vector(*axis), degrees)
    if turn:
        exact = exact.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), turn)
    sizes = _box(exact)
    spare = min(bed[0] - width, bed[1] - depth, bed[2] - height)
    return {
        "side": facing(normal),
        "at": float(plane["offset"]),
        "flat_mm2": float(plane["area"]),
        "flat_by_mesh_mm2": _flat_by_mesh(mesh, normal, plane["offset"]),
        "turned_deg": turn,
        "footprint_mm": [width, depth],
        "height_mm": height,
        "exact_mm": sizes,
        "spare_mm": spare,
        "fits": not any(over(v, most) for v, most in zip((width, depth, height), bed, strict=True)),
        "exact_fits": not any(over(v, most) for v, most in zip(sizes, bed, strict=True)),
    }


def _sides(part: Part, bed: list[float], layers: str | None) -> list[dict[str, Any]]:
    """Each side a part could lie on, the best first: one that fits, then the most flat."""
    found = []
    for plane in _flats(part):
        if plane["area"] < LEAST_FLAT:
            continue
        way = facing(np.asarray(plane["normal"]))
        if layers is not None and way not in (f"+{layers}", f"-{layers}"):
            continue
        side = _side(part, plane, bed)
        if side is not None:
            found.append(side)
    return sorted(found, key=lambda s: (not s["fits"], -s["flat_mm2"], s["height_mm"]))


def _in_place(assembly: Assembly, name: str, part: Part) -> dict[str, Any]:
    """A part's round and flat faces, and its fine mesh's triangles, where the assembly puts
    it, as `through.py` reads them."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    placed = assembly.part(name)
    turn, move = placed.matrix(), np.asarray(placed.at, dtype=float)
    cylinders = []
    for one in _cylinders(part)[0]:
        axis = turn @ one["axis"]
        shift = float(move @ axis)
        cylinders.append(
            {
                "axis": axis,
                "origin": turn @ one["origin"] + move,
                "radius": one["radius"],
                "hole": one["hole"],
                "low": one["along"][0] + shift,
                "high": one["along"][1] + shift,
                "turns": one["turns"],
            }
        )
    planes, corners = [], []
    for face in part.shape.Faces():
        if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_Plane:
            continue
        normal = np.asarray(face.normalAt().toTuple(), dtype=float)
        planes.append(
            {
                "normal": turn @ (normal / np.linalg.norm(normal)),
                "at": turn @ np.asarray(face.Center().toTuple(), dtype=float) + move,
            }
        )
        own = np.array([v.toTuple() for v in face.Vertices()], dtype=float)
        corners.append(own @ turn.T + move)
    mesh = part.fine()
    triangles = np.asarray(mesh.triangles, dtype=float) @ turn.T + move
    return {"cylinders": cylinders, "planes": planes, "corners": corners, "triangles": triangles}


def _section(triangles: np.ndarray, centre: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """A part's surface cut by the plane through `centre` square to `axis`: each triangle's cut
    as a segment, its two ends from `centre`."""
    height = (triangles - centre) @ axis
    crossing = (height.min(axis=1) < 0) & (height.max(axis=1) > 0)
    found = []
    for corners, heights in zip(triangles[crossing], height[crossing], strict=True):
        ends = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if (heights[i] < 0) != (heights[j] < 0):
                share = heights[i] / (heights[i] - heights[j])
                ends.append(corners[i] + share * (corners[j] - corners[i]) - centre)
        if len(ends) == 2:
            found.append(ends)
    return np.asarray(found, dtype=float).reshape(-1, 2, 3)


def _nearest_by_mesh(segments: np.ndarray) -> float | None:
    """The nearest a section's segments come to the point they're measured from."""
    least: float | None = None
    for start, end in segments:
        run = end - start
        length = float(run @ run)
        along_it = 0.0 if length < 1e-18 else min(1.0, max(0.0, float(-start @ run) / length))
        distance = float(np.linalg.norm(start + along_it * run))
        least = distance if least is None else min(least, distance)
    return least


def _pockets(
    assembly: Assembly, folder: Path, report: Report, fails: list[str], apart: list[str]
) -> None:
    """Each nut's pocket against its nut, for the through joints that state one."""
    saved: dict[str, Any] = {}
    read_parts: dict[str, dict[str, Any]] = {}

    def faces_of(name: str) -> dict[str, Any]:
        if name not in read_parts:
            read_parts[name] = _in_place(
                assembly, name, load(str(folder / assembly.part(name).file))
            )
        return read_parts[name]

    for joint in assembly.joints:
        if joint.kind != "through" or joint.pocket is None:
            continue
        saved[joint.name] = [
            _pocket(joint, k, point, [faces_of(n) for n in joint.between], report, fails, apart)
            for k, point in enumerate(joint.at)
        ]
    if saved:
        report.numbers["pockets"] = saved


def _pocket(
    joint: Joint,
    index: int,
    point: tuple[float, float, float],
    faces: list[dict[str, Any]],
    report: Report,
    fails: list[str],
    apart: list[str],
) -> dict[str, Any]:
    assert joint.nut is not None and joint.pocket is not None
    nut, part = joint.nut, joint.between[1]
    spot = "(" + ", ".join(f"{v:g}" for v in point) + ")"
    what = f"the nut's pocket of the through joint {joint.name}'s bolt at {spot}, in {part}"
    holes = []
    for name, read_faces in zip(joint.between, faces, strict=True):
        hole = through.bore(read_faces["cylinders"], point)
        if isinstance(hole, str):
            raise NoAnswer(
                f"the through joint {joint.name}'s bolt at {spot}: in {name}, {hole}. A "
                "through-bolt passes right through both parts, in a round hole in each"
            )
        holes.append(hole)
    way = through.meet(holes[0], holes[1])
    if isinstance(way, str):
        raise NoAnswer(f"the through joint {joint.name}'s bolt at {spot}: {way}")
    axis, seat = way["axis"], way["seats"][1]
    planes = faces[1]["planes"]
    through.along(planes, axis, faces[1]["corners"])
    # The bolt moves in its hole as far as the narrower of the two holes lets it, and the nut
    # with it (mqucifer/print-shop#183). So its corners reach that far and half its size across
    # them, and a wall's face further along than that isn't at the hole (mqucifer/print-shop#204).
    assert joint.bolt is not None
    room = max(0.0, min(h["radius"] for h in holes) - DIAMETER[joint.bolt.thread] / 2)
    found = through.pocket(
        planes, holes[1], seat, axis, 2 * nut.corners_mm, nut.corners_mm / 2 + room
    )
    one: dict[str, Any] = {
        "at": list(point),
        "part": part,
        "nut": nut.name,
        "nut_flats_mm": nut.flats_mm,
        "nut_corners_mm": nut.corners_mm,
        "nut_height_mm": nut.height_mm,
        "stated_play_mm": joint.pocket,
        "fits": False,
    }
    if isinstance(found, str):
        fails.append(f"{what}: {found}")
        report.line(f"{what}: {found}")
        report.second(f"{what}: no pocket, so nothing to measure again")
        return one
    across = found["across_flats_mm"]
    play = (across - nut.flats_mm) / 2
    goes = through.nut_goes_in(found, nut, room)
    wrong = through.nut_fits(found, nut, joint.pocket)
    if not goes["goes_in"]:
        wrong.insert(0, _corners_said(goes, nut, room))
    nearest = across / 2 - found["off_middle_mm"]
    origin = np.asarray(holes[1]["origin"], dtype=float)
    centre = origin + (seat + found["depth_mm"] / 2 - float(origin @ axis)) * axis
    cut = _section(faces[1]["triangles"], centre, axis)
    by_mesh = _nearest_by_mesh(cut)
    first, second = (np.asarray(b) for b in goes["basis"])
    flat = np.stack([cut @ first, cut @ second], axis=2)
    in_by_mesh = through.deepest_in(flat, nut.corners_mm, goes["turn_deg"], goes["centre"])
    one.update(
        across_flats_mm=across,
        play_mm=play,
        depth_mm=found["depth_mm"],
        walls=found["walls"],
        nearest_wall_mm=nearest,
        nearest_by_mesh_mm=by_mesh,
        goes_in=goes["goes_in"],
        turn_deg=goes["turn_deg"],
        off_axis_mm=goes["off_axis_mm"],
        room_in_hole_mm=room,
        corners_met=goes["met"] if not goes["goes_in"] else [],
        into_nut_mm=goes["deepest_mm"],
        into_nut_by_mesh_mm=in_by_mesh,
        fits=not wrong,
    )
    proud = nut.height_mm - found["depth_mm"]
    report.line(
        f"{what}: {found['walls']} flat walls in {found['pairs']} pair(s) across the hole, "
        f"{across:.3f} mm across the flats, as pockets.{joint.name}.{index}.across_flats_mm, "
        f"round the nut {nut.name}'s "
        f"{nut.flats_mm:g} mm ({nut.source}): a play of {play:.3f} mm a side, where the file "
        f"states {joint.pocket:g}; {found['depth_mm']:.2f} mm deep, so the nut "
        + (f"stands {proud:.2f} mm proud" if proud > 1e-6 else "sits within it")
    )
    report.line(
        f"{what}: the nut as a hexagon {nut.corners_mm:g} mm across its corners, its least, "
        + (
            f"goes in turned {goes['turn_deg']:.2f}°"
            + (
                f" with its middle {goes['off_axis_mm']:.3f} mm off the bolt's axis, of the "
                f"{room:.3f} mm the bolt has in its hole"
                if goes["off_axis_mm"] > 0
                else " on the bolt's axis"
            )
            + f", as pockets.{joint.name}.{index}.goes_in"
            if goes["goes_in"]
            else f"doesn't go in, in any turn: {_met_said(goes)}"
        )
    )
    report.second(
        f"{what}, by the fine mesh cut square to the bolt half way up it: the outline's "
        + (
            "nothing there"
            if by_mesh is None
            else f"nearest point is {by_mesh:.4f} mm from the axis, where its walls' planes put "
            f"the nearest at {nearest:.4f}"
        )
        + "; with the nut's hexagon placed as its walls' reading puts it, the outline "
        + _into_said(in_by_mesh)
        + ", and the outline of its walls' planes "
        + _into_said(goes["deepest_mm"])
    )
    if by_mesh is None or abs(by_mesh - nearest) > POCKET_AGREE:
        apart.append(
            f"{what}: its nearest wall is {nearest:.4f} mm from the bolt's axis by its faces and "
            + ("not found" if by_mesh is None else f"{by_mesh:.4f} mm")
            + " by the mesh's section"
        )
    elif abs(max(in_by_mesh, 0.0) - max(goes["deepest_mm"], 0.0)) > POCKET_AGREE:
        apart.append(
            f"{what}: with the nut's hexagon turned {goes['turn_deg']:.2f}°, the outline of its "
            f"walls' planes {_into_said(goes['deepest_mm'])} and the mesh's section "
            f"{_into_said(in_by_mesh)}"
        )
    elif wrong:
        fails.append(f"{what}: " + "; and ".join(wrong))
    else:
        return one
    report.line(f"{what}: {through.walls_said(found['facing'])}")
    return one


def _into_said(depth: float) -> str:
    """How far into the nut's outline a pocket's walls reach, as words."""
    if depth > through.SAME:
        return f"reaches {depth:.4f} mm into it"
    return f"is clear of it, {max(-depth, 0.0) + 0.0:.4f} mm at the nearest"


def _met_said(goes: dict[str, Any]) -> str:
    """Which walls a nut's corners pass, with its middle on the bolt's axis, and by how much."""
    walls = "; ".join(
        f"the wall {m['distance_mm']:.3f} mm from the axis toward "
        f"({', '.join(f'{c + 0.0:.3g}' for c in m['toward'])}) by {m['by_mm']:.3f} mm"
        for m in goes["met"]
    )
    return (
        f"turned its best, {goes['turn_deg']:.2f}°, with its middle on the bolt's axis, its "
        f"corners pass {walls}"
    )


def _corners_said(goes: dict[str, Any], nut: Any, room: float) -> str:
    """Why a nut's corners don't go into its pocket."""
    off = goes["least_off_axis_mm"]
    return (
        f"the nut {nut.name} is at least {nut.corners_mm:g} mm across its corners, and in no "
        f"turn does it go between the pocket's walls: {_met_said(goes)}. "
        + (
            f"It would go in only {off:.3f} mm off the bolt's axis, and the bolt has "
            f"{room:.3f} mm in its hole"
            if off is not None
            else f"Nowhere within the {room:.3f} mm the bolt has in its hole does it go in"
        )
        + ": the walls hold its flats, and its corners don't fit between them"
    )


def _parts(args: Any) -> list[tuple[str, str, str | None]]:
    """Each part's name, its file and, from an assembly, the axis its layers are stacked along."""
    given = list(args.parts)
    if any(p.lower().endswith(".json") for p in given):
        if len(given) != 1:
            raise NoAnswer("fit-bed reads STEP files, or one assembly file, not both")
        assembly = read(given[0])
        folder = Path(given[0]).parent
        return [(p.name, str(folder / p.file), p.layers) for p in assembly.parts]
    if not all(p.lower().endswith((".step", ".stp")) for p in given):
        raise NoAnswer(
            "fit-bed reads STEP files: a side to lie on is a flat face, and an STL has none"
        )
    stems = [Path(p).stem for p in given]
    # By its file's name where that's its own, so a need can name `parts.NAME.fits`.
    return [(s if stems.count(s) == 1 else p, p, None) for s, p in zip(stems, given, strict=True)]


def run(args: Any, report: Report) -> None:
    bed = [float(v) for v in args.bed]
    if min(bed) <= 0:
        raise NoAnswer("--bed is the bed's width, depth and height in mm, each more than 0")
    size = f"{bed[0]:g} x {bed[1]:g} mm and {bed[2]:g} mm tall"
    saved: dict[str, Any] = {}
    fails: list[str] = []
    apart: list[str] = []
    for name, file, layers in _parts(args):
        part = load(file)
        if not part.is_brep or len(part.shape.Solids()) != 1:
            raise NoAnswer(f"{file} isn't one solid: each part to print is a file of its own")
        sides = _sides(part, bed, layers)
        box = np.ptp(part.extent(), axis=0)
        saved[name] = {"file": file, "layers": layers, "box_mm": [float(v) for v in box]}
        asked = f", printed with its layers stacked along {layers}" if layers else ""
        if not sides:
            saved[name].update(fits=False, sides=[])
            fails.append(
                f"{name} has no flat face to lie on"
                + (f" square to {layers}, the way its `layers` says it's printed" if layers else "")
            )
            report.line(f"{name} ({file}{asked}), {_dims(box)}: no flat side to lie on")
            report.second(f"{name}: no side, so nothing to measure again")
            continue
        best = sides[0]
        saved[name].update(fits=best["fits"], side=best["side"], sides=sides, **_best(best))
        report.line(
            f"{name} ({file}{asked}), {_dims(box)}: laid on its {best['side']} face at "
            f"{_where(best)}, {best['flat_mm2']:.1f} mm² flat on the bed, and turned "
            f"{best['turned_deg']:g}° on it, its footprint is "
            f"{_fit(best['footprint_mm'][0], bed[0])} x {_fit(best['footprint_mm'][1], bed[1])} mm "
            f"and it's {_fit(best['height_mm'], bed[2])} mm tall: it "
            + ("fits" if best["fits"] else "doesn't fit")
            + f" the bed, {size}, as parts.{name}.fits"
        )
        for other in sides[1:SIDES_SAID]:
            report.line(
                f"  or on its {other['side']} face at {_where(other)}, {other['flat_mm2']:.1f} "
                f"mm² flat: {other['footprint_mm'][0]:.2f} x {other['footprint_mm'][1]:.2f} mm "
                f"and {other['height_mm']:.2f} mm tall, which "
                + ("fits" if other["fits"] else "doesn't fit")
            )
        exact = best["exact_mm"]
        report.second(
            f"{name} on its {best['side']} face, the exact solid turned the same way, by "
            f"OpenCascade's box: {exact[0]:.3f} x {exact[1]:.3f} mm and {exact[2]:.3f} mm tall, "
            f"where the mesh reads {best['footprint_mm'][0]:.3f} x {best['footprint_mm'][1]:.3f} "
            f"and {best['height_mm']:.3f}; its flat by the mesh's triangles, "
            f"{best['flat_by_mesh_mm2']:.1f} mm²"
        )
        if best["fits"] != best["exact_fits"]:
            apart.append(
                f"{name} on its {best['side']} face "
                + ("fits" if best["fits"] else "doesn't fit")
                + " by the mesh and "
                + ("fits" if best["exact_fits"] else "doesn't fit")
                + " by the exact solid"
            )
        elif not best["fits"]:
            longest = max(box)
            fails.append(
                f"{name} doesn't fit on any flat side"
                + (f" square to {layers}" if layers else "")
                + f": at its best, on its {best['side']} face, it needs "
                f"{best['footprint_mm'][0]:.1f} x {best['footprint_mm'][1]:.1f} mm and "
                f"{best['height_mm']:.1f} mm of height, {said(-best['spare_mm'], 0.0, 1)} mm "
                f"over. It's {longest:.1f} mm at its longest: split it"
            )
    report.numbers["bed_mm"] = bed
    report.numbers["parts"] = saved
    if len(args.parts) == 1 and args.parts[0].lower().endswith(".json"):
        _pockets(read(args.parts[0]), Path(args.parts[0]).parent, report, fails, apart)
    if apart:
        report.verdict("DISAGREE", "; and ".join(apart) + ": read both, and don't pick one")
    elif fails:
        report.verdict("FAIL", "; and ".join(fails))
    else:
        laid = ", ".join(f"{n} on its {p['side']} face" for n, p in saved.items())
        pockets = sum(len(p) for p in report.numbers.get("pockets", {}).values())
        report.verdict(
            "OK",
            f"every part fits the bed, {size}: {laid}"
            + f"; and each of {pockets} nut's pocket(s) holds its nut by its flats" * bool(pockets),
        )


def _where(side: dict[str, Any]) -> str:
    """Where a side's plane is: `z=0` for a face square to an axis."""
    way = side["side"]
    if len(way) == 2 and way[1] in AXES:
        return f"{way[1]}={side['at'] * (1 if way[0] == '+' else -1) + 0.0:g}"
    return f"{side['at']:g} mm along its normal"


def _best(side: dict[str, Any]) -> dict[str, Any]:
    keys = ("turned_deg", "footprint_mm", "height_mm", "flat_mm2", "spare_mm", "exact_mm")
    return {k: side[k] for k in keys}


def _dims(box: Any) -> str:
    return " x ".join(f"{v:g}" for v in np.round(box, 2)) + " mm"


def _fit(value: float, most: float) -> str:
    return said(value, most, 2)
