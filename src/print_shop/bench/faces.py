"""A part's flat faces and its round holes and bosses, read off its exact surface
(mqucifer/print-shop#77).

On `pi5-case-1` the model wrote 24 measuring scripts of its own because the bench had no command
for what it measured: the faces of a bought part, a fan's holes, a plug's ends and feet. Each
printed figures with no second reading, and each cost a call or more. `faces FILE` reads them in
one plain reply, and checks each figure by a second method.

**Flat faces**, largest first: the way each faces (`+z`, or its normal where it faces no axis),
where its plane is (`z=7.5`, or its offset along the normal), its area and its box. Faces in one
plane are one row. The second method is the fine mesh: the area of its triangles lying in that
plane, facing that way, within the chords round the faces' curved edges.

**Round holes and bosses**: each full cylinder of the exact surface, its split faces joined: its
centre (on its axis, half way along it), its axis, its radius and its depth, from where to where.
A hole's surface faces its axis, a boss's faces away. Whether each end of a hole is open is read
by rays along its axis. A part of a cylinder, such as a rounded edge, isn't a hole: it's counted,
and `edges` reads it. The second method is the fine mesh cut square to the axis half way along,
with a circle fitted to the cut's points round it (its centre and radius, within the chords), and
the depth from the mesh's points on the cylinder.

It reads a STEP file: an STL has no faces of its own, and `slice` reads one.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

from .readback import _circle
from .report import Report
from .shapes import FINE, NoAnswer, Part, hits, load

#: A face is facing an axis where its normal is within this of it, in radians (about 0.06°).
AXIS_TOL = 1e-3
#: Two flat faces are in one plane where their offsets differ by less than this, in mm.
PLANE_TOL = 1e-4
#: Two cylinders are one where their axes and radii agree to this, in mm.
CYLINDER_TOL = 1e-4
#: How many flat faces and holes a reply names; every one is in the record.
FLATS_SAID = 12
ROUND_SAID = 12
AXIS_NAMES = {"x": 0, "y": 1, "z": 2}


def facing(normal: np.ndarray) -> str:
    """The way a normal faces: `+z` within `AXIS_TOL` of an axis, else its three figures."""
    for name, k in AXIS_NAMES.items():
        if abs(abs(normal[k]) - 1.0) <= AXIS_TOL:
            return f"{'+' if normal[k] > 0 else '-'}{name}"
    return "(" + ", ".join(f"{v:.3f}" for v in normal) + ")"


def _at(p: Any, places: int = 4) -> list[float]:
    """Rounded, and never -0."""
    return [round(float(v), places) + 0.0 for v in p]


def _point(p: Any) -> str:
    return "(" + ", ".join(f"{float(v) + 0.0:g}" for v in p) + ")"


def _plane_at(way: str, offset: float) -> str:
    """Where a plane is, as a reply says it: `z=7.5`, or its offset along its normal."""
    if way[0] in "+-" and len(way) == 2:
        return f"{way[1]}={offset * (1 if way[0] == '+' else -1) + 0.0:g}"
    return f"{offset:g} mm along its normal"


def _flats(part: Part) -> list[dict[str, Any]]:
    """Each plane of flat faces: the way it faces, its offset, its area, box and edges' length."""
    planes: list[dict[str, Any]] = []
    for face in part.shape.Faces():
        if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_Plane:
            continue
        normal = np.asarray(face.normalAt().toTuple(), dtype=float)
        normal /= np.linalg.norm(normal)
        way = facing(normal)
        if way[0] in "+-" and len(way) == 2:
            normal = np.zeros(3)
            normal[AXIS_NAMES[way[1]]] = 1.0 if way[0] == "+" else -1.0
        offset = float(normal @ np.asarray(face.Center().toTuple()))
        box = face.BoundingBox()
        low = np.array([box.xmin, box.ymin, box.zmin])
        high = np.array([box.xmax, box.ymax, box.zmax])
        edges = float(sum(e.Length() for e in face.Edges()))
        same = next(
            (
                p
                for p in planes
                if p["facing"] == way
                and np.allclose(p["normal"], normal, atol=AXIS_TOL)
                and abs(p["offset"] - offset) <= PLANE_TOL
            ),
            None,
        )
        if same is None:
            planes.append(
                {
                    "facing": way,
                    "normal": normal,
                    "offset": offset,
                    "area": float(face.Area()),
                    "low": low,
                    "high": high,
                    "faces": 1,
                    "edges": edges,
                }
            )
        else:
            same["area"] += float(face.Area())
            same["low"] = np.minimum(same["low"], low)
            same["high"] = np.maximum(same["high"], high)
            same["faces"] += 1
            same["edges"] += edges
    return sorted(planes, key=lambda p: -p["area"])


def _flat_by_mesh(mesh: trimesh.Trimesh, normal: np.ndarray, offset: float) -> float:
    """The area of the mesh's triangles in a plane, facing its way."""
    normals = np.asarray(mesh.face_normals)
    centres = np.asarray(mesh.triangles_center)
    facing_it = normals @ normal >= math.cos(AXIS_TOL)
    on = np.abs(centres @ normal - offset) <= 1e-3
    return float(np.asarray(mesh.area_faces)[facing_it & on].sum())


def _cylinders(part: Part) -> tuple[list[dict[str, Any]], int]:
    """Each full cylinder of the surface, its split faces joined, and how many partial ones."""
    found: list[dict[str, Any]] = []
    for face in part.shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        axis = cylinder.Axis()
        direction = np.array([axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()])
        origin = np.array([axis.Location().X(), axis.Location().Y(), axis.Location().Z()])
        # One way along each axis, and its origin moved to the point nearest the part's origin.
        k = int(np.argmax(np.abs(direction)))
        if direction[k] < 0:
            direction = -direction
        origin = origin - (origin @ direction) * direction
        u0, u1 = surface.FirstUParameter(), surface.LastUParameter()
        v0, v1 = surface.FirstVParameter(), surface.LastVParameter()
        # Along the axis from the moved origin: the surface's own v is from its own.
        shift = float(
            (np.array([axis.Location().X(), axis.Location().Y(), axis.Location().Z()]) - origin)
            @ direction
        )
        sign = 1.0 if np.asarray(_dir(axis)) @ direction > 0 else -1.0
        along = sorted((shift + sign * v0, shift + sign * v1))
        # Outward normal against the way out from the axis: toward it is a hole. A point on the
        # surface, as a full cylinder's centre of mass is on its axis.
        on = surface.Value((u0 + u1) / 2, (v0 + v1) / 2)
        point = np.array([on.X(), on.Y(), on.Z()])
        normal = np.asarray(face.normalAt(cq.Vector(*point)).toTuple())
        radial = point - origin - ((point - origin) @ direction) * direction
        hole = bool(normal @ radial < 0)
        radius = float(cylinder.Radius())
        same = next(
            (
                c
                for c in found
                if np.allclose(c["axis"], direction, atol=1e-6)
                and np.linalg.norm(c["origin"] - origin) <= CYLINDER_TOL
                and abs(c["radius"] - radius) <= CYLINDER_TOL
                and c["hole"] == hole
                and c["along"][0] <= along[1] + CYLINDER_TOL
                and along[0] <= c["along"][1] + CYLINDER_TOL
            ),
            None,
        )
        if same is None:
            found.append(
                {
                    "axis": direction,
                    "origin": origin,
                    "radius": radius,
                    "hole": hole,
                    "along": along,
                    "turns": u1 - u0,
                }
            )
        else:
            same["turns"] += u1 - u0
            same["along"] = [min(same["along"][0], along[0]), max(same["along"][1], along[1])]
    full = [c for c in found if c["turns"] >= 2 * math.pi - 1e-3]
    return full, len(found) - len(full)


def _dir(axis: Any) -> list[float]:
    return [axis.Direction().X(), axis.Direction().Y(), axis.Direction().Z()]


def _axis_name(axis: np.ndarray) -> str:
    way = facing(axis)
    return way[1] if len(way) == 2 else way


def _round_by_mesh(
    mesh: trimesh.Trimesh, row: dict[str, Any]
) -> tuple[np.ndarray, float, float] | None:
    """The mesh cut square to the axis half way along, a circle fitted to the cut's points round
    the axis, and the depth from the mesh's points on the cylinder: (centre, radius, depth)."""
    axis, centre, radius = row["axis_v"], row["centre_v"], row["radius_mm"]
    lines = trimesh.intersections.mesh_plane(mesh, axis, centre)  # type: ignore[no-untyped-call]
    points = np.asarray(lines, dtype=float).reshape(-1, 3)
    if not len(points):
        return None
    off = points - centre
    radial = np.linalg.norm(off - np.outer(off @ axis, axis), axis=1)
    near = points[np.abs(radial - radius) <= max(0.05, 0.05 * radius)]
    if len(near) < 6:
        return None
    # The circle in the cut's own plane, by two directions square to the axis.
    u = np.cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    flat = np.c_[(near - centre) @ u, (near - centre) @ v]
    middle, diameter = _circle(flat)
    fitted = centre + middle[0] * u + middle[1] * v
    vertices = np.asarray(mesh.vertices)
    rel = vertices - fitted
    along = rel @ axis
    out = np.linalg.norm(rel - np.outer(along, axis), axis=1)
    lo, hi = row["from_to_mm"]
    # Points on the cylinder, within a radius of its ends: a longer coaxial one isn't it.
    reach = (hi - lo) / 2 + radius
    on = (np.abs(out - radius) <= 2 * FINE[0] + 1e-4) & (np.abs(along) <= reach)
    if not on.any():
        return fitted, diameter / 2, 0.0
    depth = float(along[on].max() - along[on].min())
    return fitted, diameter / 2, depth


def run(args: Any, report: Report) -> None:
    part = load(args.file)
    report.notes += part.notes
    if not part.is_brep:
        raise NoAnswer(
            f"{part.name} is an STL, a mesh with no faces of its own: `faces` reads a STEP file. "
            "Read an STL's outline and holes at a height with `slice`"
        )
    mesh = part.fine()
    built = part.manifold()
    planes = _flats(part)
    every = part.shape.Faces()
    kinds: dict[str, int] = defaultdict(int)
    for face in every:
        kinds[face.geomType().lower()] += 1
    report.line(
        f"{part.name}: {len(every)} faces: "
        + ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items(), key=lambda k: -k[1]))
    )
    rows: list[dict[str, Any]] = []
    differs: list[str] = []
    seconds: list[str] = []
    for plane in planes:
        by_mesh = _flat_by_mesh(mesh, plane["normal"], plane["offset"])
        # The mesh's chords cut inside a curved edge, never across a straight one.
        band = 2 * FINE[0] * plane["edges"] + 1e-3
        row: dict[str, Any] = {
            "facing": plane["facing"],
            "normal": _at(plane["normal"], 6),
            "at_mm": round(plane["offset"], 6),
            "area_mm2": round(plane["area"], 4),
            "area_mesh_mm2": round(by_mesh, 4),
            "box": [_at(plane["low"]), _at(plane["high"])],
            "size_mm": _at(plane["high"] - plane["low"]),
            "faces": plane["faces"],
        }
        rows.append(row)
        if abs(by_mesh - plane["area"]) > band:
            differs.append(
                f"the flat faces facing {plane['facing']} at "
                f"{_plane_at(plane['facing'], plane['offset'])} are {plane['area']:.3f} mm² by "
                f"OCCT and {by_mesh:.3f} by the mesh's triangles there, more than the "
                f"{band:.3f} mm² its chords allow"
            )
    report.numbers["flat_faces"] = rows
    report.line(f"flat faces, by plane, largest first ({len(rows)} planes):")
    for row in rows[:FLATS_SAID]:
        size = " x ".join(f"{v:g}" for v, n in zip(row["size_mm"], "xyz", strict=True) if v > 0)
        report.line(
            f"  facing {row['facing']} at {_plane_at(row['facing'], row['at_mm'])}: "
            f"{row['area_mm2']:g} mm², {size} mm, from {_point(row['box'][0])} to "
            f"{_point(row['box'][1])}" + (f", in {row['faces']} faces" if row["faces"] > 1 else "")
        )
    if len(rows) > FLATS_SAID:
        report.line(f"  and {len(rows) - FLATS_SAID} smaller planes, in the record")
    agree = sum(abs(r["area_mesh_mm2"] - r["area_mm2"]) <= 0.01 + 1e-9 for r in rows)
    seconds.append(
        f"each plane's area by the fine mesh's triangles in it: {agree} of {len(rows)} within "
        "0.01 mm², the rest within the chords round their curved edges"
        if rows
        else "no flat face to check"
    )
    cylinders, partial = _cylinders(part)
    rounds: list[dict[str, Any]] = []
    length = 2 * float(np.linalg.norm(np.ptp(np.asarray(mesh.vertices), axis=0))) + 1.0
    checked = 0
    for c in cylinders:
        lo, hi = c["along"]
        axis = c["axis"]
        centre = c["origin"] + axis * (lo + hi) / 2
        row = {
            "kind": "hole" if c["hole"] else "boss",
            "centre": _at(centre),
            "axis": _axis_name(axis),
            "radius_mm": round(c["radius"], 6),
            "diameter_mm": round(2 * c["radius"], 6),
            "depth_mm": round(hi - lo, 6),
            "from_to_mm": [round(lo, 6), round(hi, 6)],
            "axis_v": axis,
            "centre_v": centre,
        }
        if c["hole"]:
            ends = []
            for way in (-1.0, 1.0):
                start = centre + way * axis * ((hi - lo) / 2 - 1e-3)
                ends.append(not hits(built, start, way * axis, length))
            row["open_ends"] = sum(ends)
        found = _round_by_mesh(mesh, row)
        if found is None:
            differs.append(
                f"the {row['kind']} at {_point(row['centre'])} has no cut of the mesh round its "
                "axis to fit a circle to"
            )
        else:
            fitted, radius, depth = found
            checked += 1
            row["second"] = {
                "centre": _at(fitted),
                "radius_mm": round(radius, 4),
                "depth_mm": round(depth, 4),
            }
            off = float(np.linalg.norm(fitted - centre))
            tol = 2 * FINE[0] + 1e-3
            if abs(radius - c["radius"]) > tol or off > tol or abs(depth - (hi - lo)) > tol:
                differs.append(
                    f"the {row['kind']} at {_point(row['centre'])}: OCCT reads radius "
                    f"{c['radius']:.4f} mm, {hi - lo:.4f} deep, and the mesh radius {radius:.4f}, "
                    f"{depth:.4f} deep, its centre {off:.4f} mm off, more than the chords' {tol:g}"
                )
        rounds.append(row)
    rounds.sort(key=lambda r: (r["kind"], -r["radius_mm"], r["centre"]))
    for row in rounds:
        row.pop("axis_v")
        row.pop("centre_v")
    holes = [r for r in rounds if r["kind"] == "hole"]
    bosses = [r for r in rounds if r["kind"] == "boss"]
    report.numbers["holes"] = holes
    report.numbers["bosses"] = bosses
    report.numbers["partial_cylinders"] = partial
    for name, group in (("round holes", holes), ("round bosses", bosses)):
        head = f"{name} ({len(group)}): centre, axis, radius, depth"
        report.line(head if group else f"{name}: none")
        for row in group[:ROUND_SAID]:
            a = row["axis"]
            span = f"from {a}={row['from_to_mm'][0]:g} to {row['from_to_mm'][1]:g}"
            if len(a) > 1:
                span = f"{row['depth_mm']:g} mm along it"
            text = (
                f"  {row['kind']} at {_point(row['centre'])}, axis {a}, radius "
                f"{row['radius_mm']:g} mm (Ø{row['diameter_mm']:g}), {row['depth_mm']:g} mm deep, "
                f"{span}"
            )
            if "open_ends" in row:
                text += {2: ", open at both ends", 1: ", open at one end", 0: ", closed"}[
                    row["open_ends"]
                ]
            report.line(text)
        if len(group) > ROUND_SAID:
            report.line(f"  and {len(group) - ROUND_SAID} more, in the record")
    if partial:
        report.line(
            f"{partial} part-cylinder face(s), such as a rounded edge, aren't holes or bosses: "
            "`edges` reads a rounding's radius"
        )
    if rounds:
        seconds.append(
            f"each hole and boss by the fine mesh cut square to its axis half way along, a circle "
            f"fitted round it, and its depth from the mesh's points on it: {checked} of "
            f"{len(rounds)} read"
        )
    for text in seconds:
        report.second(text)
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:5]))
        return
    report.verdict(
        "OK",
        f"a reading of {part.name}: {len(rows)} plane(s) of flat faces, {len(holes)} round "
        f"hole(s), {len(bosses)} round boss(es)",
    )
