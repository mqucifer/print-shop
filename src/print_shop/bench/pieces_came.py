"""`pieces came` and `pieces ring`: what holds a dome's slices together with no glue, came
strips along the seams and a ring at the top and the hem (mqucifer/print-shop#166).

The Sponsor, 2026-11-18: "I'm not a fan of glued seems for 3d prints. It needs some mechanical
hold." So a seam is held as a glass worker's is, by came: a strip with an H-shaped section
that clips over both slices' edges. And the shade is kept round by two hoops, each with a
groove that takes every slice's edge.

**A came strip** is one straight length of a seam. Its section is the same all along:

- **the web** lies between the two slices' faces, on the plane through the dome's axis and
  the cut. It's the seam's gap less a play on each side;
- **two channels,** one a slice, each at its slice's own angle, so the two stand at the angle
  between the slices there. A channel's slot is the frame's thickness and the play;
- **four flanges,** over each slice's outer face and its inner. Each ends in a low bead that
  stands the grip into the slot, so the strip is pressed on and holds;
- **the flanges' ends** lie on two planes square to the web, so a strip stands on two of them
  on the bed.

A strip prints standing on one pair of flanges, its web level between them: the web is a
bridge a slot wide, and nothing else overhangs. A flange is wider than the lead line, so a seam
reads as a heavier line, and never reaches a pane's opening: the lead beside the seam and the
lip over the pane's edge are what it may cover.

**The rings.** The hem ring is one part, a channel that opens upward and takes each slice's
hem edge. The top ring is two, as a groove near the top of a dome lies nearly level and its
upper wall would print in the air: a seat under the slices' top edges and a cap over them,
each printed with its sloping face up, and held together through a hole in the middle by the
lamp's own fitting. That hole is a stated figure until the fitting is chosen.

**Second method.** A strip's angle and its slot are read back from its solid's own faces, and
its volume against its section's area. Each ring is asked, at every slice's edge, whether it's
solid just past the edge's outer face, its inner and its end, and empty where the edge goes.

**Printing** is read from each part as it's laid: what stands on the bed, each face that
looks down more steeply than `OVERHANG_DEG`, and whether such a face is a bridge held at both
ends. A part that needs a support is `FAIL`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely import affinity

from . import piecemap
from .limits import over, under
from .pieces_seam import GAP, PROBE, _angle, edges_of, inside, placings, side_of, slice_surface
from .report import NoAnswer, Report

#: The bed a part is laid on, in mm, where none is given.
BED = (256.0, 256.0)
#: A face may look down this far from upright, in degrees, and print with nothing under it.
OVERHANG_DEG = 45.0
#: The longest bridge, in mm.
BRIDGE = 10.0
#: The play between a strip and a slice, in mm, unless given: on each side of the web, and
#: in all across a channel's slot.
PLAY = 0.1
#: How far each bead stands into a slice's face, in mm, unless given.
GRIP = 0.05
#: A flange unless given, in mm: how far it reaches from the cut's middle, and how thick.
FLANGE = (1.5, 0.8)
#: A bead's length along its flange, in mm.
BEAD = 0.6
#: A web is at least this thick, in mm: a layer.
LEAST_WEB = 0.2
#: A strip stops this far short of a fold, in mm, and of the hem and the top unless given.
OFF_FOLD = 0.5
OFF_END = 5.0
#: The shortest strip worth making, in mm.
LEAST_STRIP = 3.0
#: A ring unless given, in mm: how deep its groove is, its walls, and the hole in the top's.
DEPTH, RING_WALL, HOLE = 4.0, 1.2, 10.0
#: The play across a ring's groove and past the edge in it, in mm, unless given.
RING_PLAY = 0.2
#: How far a read-back angle or slot may be off the one built, in degrees and mm.
ANGLE_OFF, SLOT_OFF = 0.01, 0.005


# What prints


def prints(solid: Any, bed: tuple[float, float]) -> dict[str, Any]:
    """A part as it's laid, read for printing: what stands on the bed, each flat face that
    looks down more than `OVERHANG_DEG` from upright, and whether that face is a bridge, held
    at both ends of its narrower way."""
    box = solid.BoundingBox()
    limit = -math.cos(math.radians(OVERHANG_DEG)) - 1e-6
    on_bed, bridges, hanging = 0.0, [], []
    for face in solid.Faces():
        centre = face.Center()
        normal = face.normalAt(centre)
        if normal.z > limit:
            continue
        span = face.BoundingBox()
        if span.zmax - box.zmin < 1e-4:
            on_bed += float(face.Area())
            continue
        steep = math.degrees(math.acos(max(-1.0, min(1.0, -normal.z))))
        along_x = span.xlen <= span.ylen
        narrow = np.array([1.0, 0.0, 0.0]) if along_x else np.array([0.0, 1.0, 0.0])
        across = min(span.xlen, span.ylen)
        at = np.array(centre.toTuple())
        under_it = np.array([0.0, 0.0, 0.05])
        ends = [at + sign * (across / 2.0 + 0.05) * narrow - under_it for sign in (-1, 1)]
        if steep < 1.0 and all(inside([solid], end) for end in ends):
            bridges.append(float(across))
        else:
            hanging.append({"at": [round(float(v), 2) for v in at], "from_level_deg": steep})
    size = [float(box.xlen), float(box.ylen), float(box.zlen)]
    fits = (not over(size[0], bed[0]) and not over(size[1], bed[1])) or (
        not over(size[0], bed[1]) and not over(size[1], bed[0])
    )
    return {
        "size_mm": [round(v, 3) for v in size],
        "on_bed_mm2": round(on_bed, 3),
        "bridge_mm": round(max(bridges), 3) if bridges else 0.0,
        "bridges": len(bridges),
        "unsupported": hanging,
        "fits_bed": fits,
        "supports": bool(hanging) or (bool(bridges) and over(max(bridges), BRIDGE)),
    }


def print_wrong(name: str, read: dict[str, Any], bed: tuple[float, float]) -> list[str]:
    """What stops a part printing as it's laid, in words."""
    wrong = []
    if read["unsupported"]:
        worst = min(read["unsupported"], key=lambda h: float(h["from_level_deg"]))
        wrong.append(
            f"{name} has {len(read['unsupported'])} face(s) that look down with nothing "
            f"under them, one {worst['from_level_deg']:.0f}° from level at {worst['at']}: it "
            "needs a support"
        )
    if over(float(read["bridge_mm"]), BRIDGE):
        wrong.append(f"{name} has a bridge {read['bridge_mm']:g} mm long, over {BRIDGE:g} mm")
    if not read["fits_bed"]:
        size = read["size_mm"]
        wrong.append(
            f"{name} is {size[0]:g} by {size[1]:g} mm, and the bed {bed[0]:g} by {bed[1]:g}"
        )
    if not read["on_bed_mm2"] > 0:
        wrong.append(f"{name} has no flat face on the bed")
    return wrong


def _prism(points: np.ndarray, way: np.ndarray) -> Any:
    """A flat outline, as points in space, swept along a direction."""
    import cadquery as cq

    wire = cq.Wire.makePolygon([cq.Vector(*(float(v) for v in p)) for p in points], close=True)
    solid = cq.Solid.extrudeLinear(wire, [], cq.Vector(*(float(v) for v in way)))
    return solid if solid.Volume() > 0 else cq.Solid(solid.wrapped.Reversed())


def _ring_points(shape: Any) -> np.ndarray:
    tidy = shapely.Polygon(shape.exterior).simplify(1e-7)
    return np.asarray(tidy.exterior.coords)[:-1]


# A came strip


def came_section(
    bevel_deg: float,
    thick: float,
    gap: float,
    play: float,
    grip: float,
    reach: float,
    flange: float,
) -> tuple[Any, dict[str, float]]:
    """A strip's section: across the seam, out from the plane between the two slices, by out
    of the dome along that plane. The cut's middle at the frames' inner face is (0, 0)."""
    a = math.radians(bevel_deg)
    u = np.array([math.cos(a), -math.sin(a)])
    n = np.array([math.sin(a), math.cos(a)])
    half_play = play / 2.0

    def at(along: float, up: float) -> tuple[float, float]:
        p = along * u + up * n
        return float(p[0]), float(p[1])

    foot = at(reach, -half_play)[0]
    web = gap / 2.0 - play
    stands = half_play + grip
    parts = [
        shapely.Polygon(
            [at(-3.0, low), at(reach + 1.0, low), at(reach + 1.0, high), at(-3.0, high)]
        )
        for low, high in (
            (thick + half_play, thick + half_play + flange),
            (-half_play - flange, -half_play),
        )
    ]
    tips = {}
    for name, face, sign in (("outer", thick + half_play, -1.0), ("inner", -half_play, 1.0)):
        tip = (foot - face * math.sin(a)) / math.cos(a)
        tips[name] = tip
        # Its base a little inside the flange, so the two are one outline.
        base = face - sign * 0.05
        parts.append(
            shapely.Polygon(
                [
                    at(tip - BEAD, base),
                    at(tip - BEAD, face),
                    at(tip - BEAD / 2.0, face + sign * stands),
                    at(tip, face),
                    at(tip, base),
                ]
            )
        )
    low = (-half_play - flange) / math.cos(a)
    high = (thick + half_play + flange) / math.cos(a) - web * math.tan(a)
    parts.append(shapely.box(0.0, low, web, high))
    half = shapely.union_all(parts).intersection(shapely.box(0.0, -100.0, foot, 100.0))
    whole = shapely.union_all([half, affinity.scale(half, xfact=-1.0, origin=(0, 0))]).buffer(0)
    found = piecemap.polygons(whole)
    if len(found) != 1 or found[0].interiors:
        raise NoAnswer("a came strip's section isn't one outline: its flanges don't reach its web")
    # Where a slice's own face meets its outer side and its inner, along the slice.
    faces_from = {
        "outer": (gap / 2.0 - thick * math.sin(a)) / math.cos(a),
        "inner": gap / 2.0 / math.cos(a),
    }
    return found[0], {
        "outer_cover_mm": tips["outer"] - faces_from["outer"],
        "inner_cover_mm": tips["inner"] - faces_from["inner"],
        "web_mm": 2.0 * web,
        "slot_mm": thick + play,
        "mouth_mm": thick - 2.0 * grip,
        "foot_mm": foot,
        "outer_reach_mm": tips["outer"],
        "inner_reach_mm": tips["inner"],
        "wide_mm": 2.0 * foot,
    }


def _faces_toward(solid: Any, way: np.ndarray) -> list[tuple[float, np.ndarray, float]]:
    """A solid's flat faces by how near they face a way: the angle, the middle and the area."""
    found = []
    for face in solid.Faces():
        if face.geomType() != "PLANE":
            continue
        found.append(
            (
                _angle(np.array(face.normalAt().toTuple()), way),
                np.array(face.Center().toTuple()),
                float(face.Area()),
            )
        )
    return sorted(found, key=lambda f: (round(f[0], 3), -f[2]))


def strip_read(laid: Any, bevel_deg: float) -> tuple[float, float]:
    """A laid strip read back from its own faces: the angle between its two channels, from
    the largest face of each that looks along its slice's normal, and a slot's width, from
    that face to the one that looks back at it."""
    a = math.radians(bevel_deg)
    walls = []
    for sign in (1.0, -1.0):
        normal = np.array([math.cos(a), 0.0, sign * math.sin(a)])
        down = [f for f in _faces_toward(laid, -normal) if f[0] < 0.01]
        up = [f for f in _faces_toward(laid, normal) if f[0] < 0.01]
        # A slot is between a face that looks into the slice and the nearest that looks back.
        across = sorted(
            float((d[1] - f[1]) @ normal)
            for d in down
            for f in up
            if float((d[1] - f[1]) @ normal) > 0.05
        )
        nearest = _faces_toward(laid, -normal)[0][0]
        walls.append((nearest, across[0] if across else math.nan))
    angle = 2.0 * bevel_deg + walls[0][0] + walls[1][0]
    return angle, walls[0][1]


def run_came(args: Any, report: Report) -> None:
    import cadquery as cq

    found = piecemap.load(args.map)
    surface = slice_surface(found, args.map)
    thick = float(args.thickness)
    gap = GAP if args.gap is None else float(args.gap)
    play = PLAY if args.play is None else float(args.play)
    grip = GRIP if args.grip is None else float(args.grip)
    reach, flange = args.flange or FLANGE
    lip = float(args.lip)
    bed = args.bed or BED
    if not (thick > 0 and play > 0 and grip >= 0 and reach > 0 and flange > 0 and lip > 0):
        raise NoAnswer(
            "--thickness, --play, --lip and --flange REACH,THICK are each over 0, in mm, and "
            "--grip 0 or more"
        )
    if under(gap - 2.0 * play, LEAST_WEB):
        raise NoAnswer(
            f"a gap of {gap:g} mm with {play:g} mm of play on each side leaves a web of "
            f"{gap - 2.0 * play:.2f} mm, under a layer's {LEAST_WEB:g}: build the slices with a "
            f"--seam-gap of {LEAST_WEB + 2.0 * play:g} or more, and give it here as --gap"
        )
    edges = edges_of(found, args.map)
    edge = edges[1]
    cut = np.array(surface["edges"]["right"]["on_dome"], dtype=float)
    phi = math.atan2(cut[0][1], cut[0][0])
    toward = np.array([-math.sin(phi), math.cos(phi), 0.0])
    places = placings(surface)
    ring, _near, low, high = side_of(found, edge)
    first, last = edge.lengths[0], edge.lengths[-1]
    starts = max(OFF_END if args.hem is None else float(args.hem),
                 float((ring[low] - first.a) @ first.way) + 1.0)  # fmt: skip
    stops = max(OFF_END if args.top is None else float(args.top),
                last.length - float((ring[high] - last.a) @ last.way) + 1.0)  # fmt: skip
    out = Path(args.out_came)
    out.mkdir(parents=True, exist_ok=True)
    strips, placed, wrong, differs = [], [], [], []
    for j, length in enumerate(edge.lengths):
        said = f"length {length.row}"
        if length.bevel_deg > OVERHANG_DEG:
            raise NoAnswer(
                f"at {said} the slices stand {length.join_deg:.1f}° apart, so a strip's "
                f"flanges would lean {length.bevel_deg:.1f}° on the bed, over "
                f"{OVERHANG_DEG:g}°: more slices stand at a smaller angle"
            )
        section, sizes = came_section(length.bevel_deg, thick, gap, play, grip, reach, flange)
        start = starts if j == 0 else OFF_FOLD
        stop = length.length - (stops if j == len(edge.lengths) - 1 else OFF_FOLD)
        if under(stop - start, LEAST_STRIP):
            raise NoAnswer(
                f"{said} is {length.length:.1f} mm long, and a strip on it from {start:.1f} to "
                f"{stop:.1f} mm is under {LEAST_STRIP:g} mm: a smaller --hem or --top"
            )
        longest = max(sizes["outer_reach_mm"], sizes["inner_reach_mm"])
        room = length.half_mm + lip
        if over(longest, room):
            wrong.append(
                f"at {said} a flange reaches {longest:.2f} mm from the cut's middle, and the "
                f"nearest pane's opening is {room:.2f} mm from it, {length.half_mm:.2f} of lead "
                f"and a lip of {lip:g}: a --flange reach of {room - 0.05:.2f} or less"
            )
        if not over(sizes["wide_mm"], found.lead_mm):
            wrong.append(
                f"at {said} the strip is {sizes['wide_mm']:.2f} mm wide, no wider than the "
                f"lead line, {found.lead_mm:g} mm: a longer --flange reach"
            )
        points = _ring_points(section)
        long = stop - start
        # Laid: the web level, the strip's length along y. A section is the same either side
        # of its web, so the strip as laid is the strip as placed.
        laid = _prism(
            np.column_stack([points[:, 1], np.zeros(len(points)), points[:, 0] + sizes["foot_mm"]]),
            np.array([0.0, long, 0.0]),
        )
        way = (cut[j + 1] - cut[j]) / np.linalg.norm(cut[j + 1] - cut[j])
        radial = np.cross(toward, way)
        radial = radial if float(radial @ places[j]["normal"]) > 0 else -radial
        base = cut[j] + start * way
        on_dome = _prism(
            base + points[:, :1] * toward + points[:, 1:] * radial, long * way
        )
        target = out / f"strip-{length.row:02d}.step"
        laid.exportStep(str(target))
        report.wrote(str(target))
        placed.append(on_dome)
        read = prints(laid, bed)
        angle, slot = strip_read(laid, length.bevel_deg)
        volume, by_area = float(laid.Volume()), section.area * long
        wrong += print_wrong(f"the strip for {said}", read, bed)
        if abs(angle - length.join_deg) > ANGLE_OFF or not abs(slot - sizes["slot_mm"]) <= SLOT_OFF:
            differs.append(
                f"the strip for {said} reads {angle:.3f}° between its channels and a slot of "
                f"{slot:.3f} mm from its own faces, where it was made for "
                f"{length.join_deg:.3f}° and {sizes['slot_mm']:.3f} mm"
            )
        if abs(volume - by_area) > 0.005 * by_area:
            differs.append(
                f"the strip for {said} is {volume:.2f} mm³, and its section makes {by_area:.2f}"
            )
        strips.append(
            {
                "length": length.row,
                "file": str(target),
                "length_mm": round(long, 3),
                "from_mm": round(start, 3),
                "channels_deg": length.join_deg,
                "channels_read_deg": angle,
                "slot_read_mm": slot,
                "volume_mm3": volume,
                "section_mm2": section.area,
                **{k: round(v, 4) for k, v in sizes.items()},
                "prints": read,
            }
        )
    together = out / f"placed-{int(surface['slice']):02d}-{edge.slice:02d}.step"
    cq.Compound.makeCompound(placed).exportStep(str(together))
    report.wrote(str(together))
    report.numbers["strips"] = strips
    report.fact("strips_count", len(strips), how="one for each straight length of a seam")
    report.fact(
        "placed", str(together),
        how=f"the strips where they sit on the seam between slice {surface['slice']} and "
        f"slice {edge.slice}, the dome's axis along z: `pieces seam ... --came` reads them on "
        "two built slices",
    )  # fmt: skip
    one = strips[0]
    report.fact(
        "slot_mm", one["slot_mm"], "mm", f"each channel: the frame's {thick:g} and the play"
    )
    report.fact(
        "mouth_mm", one["mouth_mm"], "mm",
        f"between a channel's two beads: each stands {grip:g} mm into its slice's face, the grip",
    )  # fmt: skip
    report.fact("web_mm", one["web_mm"], "mm", f"the gap, {gap:g}, less {play:g} on each side")
    report.fact(
        "flange_wide_mm", one["wide_mm"], "mm",
        f"across both flanges, over a lead line of {found.lead_mm:g} mm: the seam's line as "
        "it's seen",
    )  # fmt: skip
    report.line(
        "strips, each its length, the angle between its two channels, and how much of a "
        "slice's outer face and its inner a flange lies over, in from the slice's edge: "
        + "; ".join(
            f"strip {s['length']}, {s['length_mm']:.1f} mm, {s['channels_deg']:.2f}°, "
            f"{s['outer_cover_mm']:.2f} and {s['inner_cover_mm']:.2f} mm"
            for s in strips
        )
    )
    tall = max(s["prints"]["size_mm"][2] for s in strips)
    report.line(
        f"printing: each strip stands on the ends of one pair of flanges, its length along "
        f"the bed and its web level, {tall:.2f} mm tall at the most; the web is a bridge "
        f"{max(s['prints']['bridge_mm'] for s in strips):.2f} mm long between the flanges, and "
        "nothing else overhangs. A dome's other seams take the same strips where its slices "
        "are alike"
    )
    report.second(
        "each strip read back from its own faces: the angle between its two channels is "
        f"within {max(abs(s['channels_read_deg'] - s['channels_deg']) for s in strips):.4f}° of "
        "the angle between the slices, and a slot is "
        f"{strips[0]['slot_read_mm']:.3f} mm; its volume is within "
        f"{max(abs(s['volume_mm3'] - s['section_mm2'] * s['length_mm']) for s in strips):.3f} "
        "mm³ of its section's area in Shapely times its length"
    )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    if wrong:
        report.verdict("FAIL", "; ".join(wrong[:4]))
        return
    report.verdict(
        "OK",
        f"{len(strips)} came strip(s) in {out}, one a straight length, each with its channels "
        f"at the slices' angle there, a slot of {one['slot_mm']:g} mm and a grip of {grip:g}; "
        "each prints with no support",
    )


# The rings


def _across(points: np.ndarray, wide: float) -> Any:
    """A ring's section at a slice's middle, how far out by how high, as that slice's part
    of the ring: swept across the slice, and cut to the slice's share of the way round."""
    return _prism(
        np.column_stack([points[:, 0], np.full(len(points), -wide), points[:, 1]]),
        np.array([0.0, 2.0 * wide, 0.0]),
    )


def _hoop(section: Any, slices: int, turn: float) -> Any:
    """A section turned into a ring of flat parts, one a slice, mitred where slices meet."""
    points = _ring_points(section)
    far = float(points[:, 0].max()) + 5.0
    half = math.pi / slices
    low, high = float(points[:, 1].min()) - 1.0, float(points[:, 1].max()) + 1.0
    wedge = _prism(
        np.array(
            [
                [0.0, 0.0, low],
                [far, -far * math.tan(half), low],
                [far, far * math.tan(half), low],
            ]
        ),
        np.array([0.0, 0.0, high - low]),
    )
    part = _across(points, far * math.tan(half) + 1.0).intersect(wedge)
    whole = None
    for k in range(slices):
        one = part.rotate((0, 0, 0), (0, 0, 1), math.degrees(turn) + 360.0 * k / slices)
        whole = one if whole is None else whole.fuse(one)
    assert whole is not None
    return whole.clean()


def run_ring(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    surface = slice_surface(found, args.map)
    thick = float(args.thickness)
    border = found.lead_mm / 2.0 if args.border is None else float(args.border)
    depth = DEPTH if args.depth is None else float(args.depth)
    play = RING_PLAY if args.play is None else float(args.play)
    wall = RING_WALL if args.wall is None else float(args.wall)
    hole = HOLE if args.hole is None else float(args.hole)
    bed = args.bed or BED
    if not (thick > 0 and border >= 0 and depth > 0 and play > 0 and wall > 0 and hole > 0):
        raise NoAnswer(
            "--thickness, --depth, --play, --wall and --hole are each over 0, in mm, and "
            "--border 0 or more"
        )
    slices = int(surface["slices"])
    turn = math.radians(float(surface["turn_deg"]))
    rows = placings(surface)
    half = play / 2.0

    def frame(row: dict[str, Any], top: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """An edge of the slice's frame at its middle, out by high: where its inner face
        ends, the way up the row, and the way out of it."""
        tilt = float(row["tilt"])
        up = np.array([-math.cos(tilt), math.sin(tilt)])
        normal = np.array([math.sin(tilt), math.cos(tilt)])
        start = np.array([float(row["out"]), float(row["z"])])
        end = start + (float(row["long"]) + border) * up if top else start - border * up
        return end, up, normal

    edges = edges_of(found, args.map)
    ring, _near, low, _high = side_of(found, edges[1])
    level = abs(float(ring[low][1]) - float(found.panel.bounds[1])) < 0.01
    hem_tilt = math.degrees(float(rows[0]["tilt"]))
    made: list[tuple[str, Any, Any]] = []
    skipped = ""
    if not level:
        skipped = (
            "no hem ring: the hem isn't one level edge, as a scalloped hem's isn't, so there's "
            "no one groove for it"
        )
    elif 90.0 - hem_tilt > OVERHANG_DEG:
        skipped = (
            f"no hem ring: the hem's row leans {90.0 - hem_tilt:.0f}° from upright, so a "
            f"groove's wall would overhang more than {OVERHANG_DEG:g}°"
        )
    else:
        end, up, normal = frame(rows[0], False)

        def spot(along: float, off: float) -> np.ndarray:
            return np.asarray(end + along * up + off * normal)

        deep = (thick + play + 2.0 * wall) / max(math.tan(math.radians(hem_tilt)), 0.2) + wall
        body = shapely.Polygon(
            [
                spot(-play - wall - deep, -half - wall),
                spot(-play - wall - deep, thick + half + wall),
                spot(depth, thick + half + wall),
                spot(depth, -half - wall),
            ]
        )
        slot = shapely.Polygon(
            [
                spot(-play, -half),
                spot(-play, thick + half),
                spot(depth + 1.0, thick + half),
                spot(depth + 1.0, -half),
            ]
        )
        floor = min(spot(-play, -half)[1], spot(-play, thick + half)[1]) - wall
        section = body.intersection(shapely.box(-1e3, floor, 1e3, 1e3)).difference(slot)
        made.append(("hem-ring", _hoop(piecemap.polygons(section)[0], slices, turn), None))
    end, up, normal = frame(rows[-1], True)

    def at(along: float, off: float) -> np.ndarray:
        return np.asarray(end + along * up + off * normal)

    if hole / 2.0 + wall > float(end[0]):
        raise NoAnswer(
            f"a hole {hole:g} mm across leaves no ring inside the slices' top edges, "
            f"{2.0 * float(end[0]):.1f} mm across: a smaller --hole"
        )
    stop = at(play, -half)
    seat_low = at(-depth, -half)
    seat = shapely.Polygon(
        [
            seat_low,
            stop,
            (hole / 2.0, stop[1]),
            (hole / 2.0, seat_low[1] - wall),
            (seat_low[0], seat_low[1] - wall),
        ]
    )
    skirt, over_edge = at(-depth, thick + half), at(play, thick + half)
    rim = at(-depth, thick + half + wall)
    crown = max(float(over_edge[1]), float(rim[1])) + wall
    cap = shapely.Polygon(
        [
            skirt,
            over_edge,
            stop,
            (hole / 2.0, stop[1]),
            (hole / 2.0, crown),
            (rim[0], crown),
            rim,
        ]
    )
    for name, section in (("top-seat", seat), ("top-cap", cap)):
        if not section.is_valid:
            raise NoAnswer(f"the {name}'s section isn't a plain outline: a shallower --depth")
        made.append((name, _hoop(section, slices, turn), None))
    out = Path(args.out_rings)
    out.mkdir(parents=True, exist_ok=True)
    parts, wrong = [], []
    for name, solid, _ in made:
        target = out / f"{name}.step"
        solid.exportStep(str(target))
        report.wrote(str(target))
        # The cap is printed the other way up, its sloping face up and its crown on the bed.
        laid = solid.rotate((0, 0, 0), (1, 0, 0), 180.0) if name == "top-cap" else solid
        laid = laid.translate((0, 0, -laid.BoundingBox().zmin))
        flat = out / f"{name}-laid.step"
        laid.exportStep(str(flat))
        report.wrote(str(flat))
        read = prints(laid, bed)
        wrong += print_wrong(f"the {name}", read, bed)
        parts.append(
            {
                "part": name,
                "file": str(target),
                "laid": str(flat),
                "solids": len(solid.Solids()),
                "volume_mm3": float(solid.Volume()),
                "prints": read,
            }
        )
    # The second method: each slice's edge, at its middle, asked of the rings.
    solids = [solid for _name, solid, _ in made]
    asked = {"hem": 0, "top": 0}
    held = {"hem": 0, "top": 0}
    empty = 0
    for name, row, top in (("hem", rows[0], False), ("top", rows[-1], True)):
        if name == "hem" and skipped:
            continue
        end, up, normal = frame(row, top)
        sign = 1.0 if top else -1.0
        back = end - sign * 1.5 * up + thick / 2.0 * normal
        probes = [
            back + (thick / 2.0 + PROBE) * normal,
            back - (thick / 2.0 + PROBE) * normal,
            end + thick / 2.0 * normal + sign * (play + PROBE) * up,
        ]
        for k in range(slices):
            angle = turn + 2.0 * math.pi * k / slices

            def space(p: np.ndarray, angle: float = angle) -> np.ndarray:
                return np.array([p[0] * math.cos(angle), p[0] * math.sin(angle), p[1]])

            asked[name] += len(probes)
            held[name] += sum(inside(solids, space(p)) for p in probes)
            empty += not inside(solids, space(back))
    report.numbers["rings"] = parts
    report.fact("rings_count", len(parts), how=", ".join(str(p["part"]) for p in parts))
    report.fact("groove_mm", thick + play, "mm", f"across: the frame's {thick:g} and the play")
    report.fact("groove_deep_mm", depth, "mm", "how much of each slice's edge is in it")
    report.fact(
        "hole_mm", hole, "mm",
        "through the top ring's seat and cap, across its flats: a stated figure, for the "
        "lamp's own fitting to hold the two together, until that fitting is chosen",
    )  # fmt: skip
    report.numbers["edges_held"] = held
    report.numbers["edges_asked"] = asked
    report.line(
        "the top ring is two parts, a seat under the slices' top edges and a cap over them: "
        f"the top row lies {math.degrees(float(rows[-1]['tilt'])):.0f}° from level, so one "
        "part's groove would have a wall printed in the air. Each prints with its sloping face "
        "up, the cap the other way up, as its `-laid` file has it"
    )
    if skipped:
        report.note(skipped)
    report.second(
        f"each of the {slices} slices' edges, at its middle, asked of the rings: of "
        f"{asked['hem']} point(s) at the hem and {asked['top']} at the top, {PROBE:g} mm past "
        f"the frame's outer face, its inner and its end, {held['hem']} and {held['top']} are "
        f"inside a ring; and of the {(len(made) > 2) * slices + slices} place(s) where an "
        f"edge goes, {empty} are empty"
    )
    loose = [n for n in ("hem", "top") if held[n] != asked[n]]
    if any(p["solids"] != 1 for p in parts):
        wrong.append("a ring is more than one body")
    if loose:
        wrong.append(f"a slice's {' and '.join(loose)} edge isn't held on every side")
    if empty != (len(made) > 2) * slices + slices:
        wrong.append("a ring fills the place where a slice's edge goes")
    if wrong:
        report.verdict("FAIL", "; ".join(wrong[:4]))
        return
    report.verdict(
        "OK",
        f"{len(parts)} ring part(s) in {out}: {', '.join(str(p['part']) for p in parts)}; "
        f"each of the {slices} slices' edges is in a groove {thick + play:g} mm across and "
        f"{depth:g} deep, and each part prints with no support",
    )
