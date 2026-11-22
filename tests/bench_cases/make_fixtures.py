"""The bench's planted cases, written to the current directory, inside the image.

Each is something a tool reported as success when it was wrong (the map's "Never take a tool's
word"), or a plain part whose right answer is known by arithmetic. All of them are made here,
from scratch: no Sponsor's design, or anything from one, is in this repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import cadquery as cq
import numpy as np
import trimesh


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Any:
    return (
        cq.Workplane("XY")
        .box(x1 - x0, y1 - y0, z1 - z0)
        .translate(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
    )


def save(shape: Any, name: str) -> None:
    cq.exporters.export(shape, f"{name}.step")


def along_y(radius: float, y0: float, y1: float, hole: float = 0.0) -> Any:
    tube = cq.Workplane("XZ").workplane(offset=-y1).circle(radius)
    if hole:
        tube = tube.circle(hole)
    return tube.extrude(y1 - y0)


def level_bore(kind: str, leaves: float = 43.0, turn: float = 0.0) -> Any:
    """What's cut for a 5.5 mm bore along x through (y 15, z 10). A peak's flats leave the round
    `leaves` degrees above level, so rise at 90 less that; `turn` turns the whole cut about it."""
    radius, way = 2.75, cq.Vector(1, 0, 0)
    made = cq.Solid.makeCylinder(radius, 44, cq.Vector(-2, 15, 10), way)
    if kind == "end":
        over = cq.Solid.makeCylinder(radius, 44, cq.Vector(-2, 15, 13), way)
        sides = cq.Solid.makeBox(44, 2 * radius, 3, cq.Vector(-2, 15 - radius, 10))
        made = made.fuse(over).fuse(sides).clean()
    elif kind == "d":
        made = made.intersect(cq.Solid.makeBox(44, 30, 11.5, cq.Vector(-2, 0, 0)))
    elif kind in ("peaked", "short"):
        angle = np.radians(leaves)
        y, z = radius * np.cos(angle), 10 + radius * np.sin(angle)
        corners = ((15 + y, z), (15, 10 + radius / np.sin(angle)), (15 - y, z))
        roof = cq.Wire.makePolygon([cq.Vector(-2, *p) for p in corners], close=True)
        prism = cq.Solid.extrudeLinear(cq.Face.makeFromWires(roof), cq.Vector(44, 0, 0))
        made = made.fuse(prism).clean()
        if kind == "short":
            made = made.intersect(cq.Solid.makeBox(44, 30, 13.5, cq.Vector(-2, 0, 0)))
    return made.rotate(cq.Vector(0, 15, 10), cq.Vector(1, 15, 10), turn) if turn else made


def bores() -> None:
    """A bore lying level on the bed (mqucifer/print-shop#237), round or peaked, and what isn't
    a peaked bore; and two blocks on one bolt through it, with the nut in a slot or a hex
    pocket, as drawn and as the assembly turns them."""
    out = Path("bore")
    out.mkdir(exist_ok=True)
    solid = cq.Solid.makeBox
    one = solid(20, 30, 20)
    two = solid(20, 30, 20, cq.Vector(20, 0, 0))
    slot = solid(4.9, 8.3, 15.8, cq.Vector(30, 10.85, 5.2))
    made = {"one-round": one.cut(level_bore("round")), "one-peaked": one.cut(level_bore("peaked"))}
    for turn in (90, 180, 270):
        made[f"one-peaked-{turn}"] = one.cut(level_bore("peaked", turn=turn))
    made["one-45"] = one.cut(level_bore("peaked", 45.0))
    made["one-44"] = one.cut(level_bore("peaked", 46.0))
    for kind in ("end", "d", "short"):
        made[f"one-{kind}"] = one.cut(level_bore(kind))
    made["one-groove"] = solid(20, 30, 10).cut(level_bore("round"))
    made["one-upright"] = made["one-peaked"].rotate(cq.Vector(), cq.Vector(0, 1, 0), -90)
    made["two-slot"] = two.cut(level_bore("peaked")).cut(slot)
    made["two-split"] = two.cut(level_bore("d")).cut(slot)
    made["two-flat"] = two.cut(level_bore("peaked", 46.0)).cut(slot)
    # A steeper peak, whose point runs on into the flat side of the nut's hex pocket over it.
    corners = [
        cq.Vector(30, 15 + 8.3 / 3**0.5 * np.cos(a), 10 + 8.3 / 3**0.5 * np.sin(a))
        for a in np.arange(6) * np.pi / 3
    ]
    hexagon = cq.Face.makeFromWires(cq.Wire.makePolygon(corners, close=True))
    pocket = cq.Solid.extrudeLinear(hexagon, cq.Vector(10, 0, 0))
    made["two-hex"] = two.cut(level_bore("peaked", 40.0)).cut(pocket)
    for name, shape in made.items():
        save(cq.Workplane().add(shape), f"bore/{name}")
    for name in ("one-round", "one-peaked"):
        tolerance = {"tolerance": 0.01, "angularTolerance": 0.1}
        cq.exporters.export(cq.Workplane().add(made[name]), f"bore/{name}.stl", **tolerance)
    joint = {"between": ["one", "two"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
             "washer": "m5-washer", "pocket": 0.15}  # fmt: skip
    for name, two_is, turns, at, held in (
        ("slot", "slot", [], [20, 15, 10], "x=40"),
        ("split", "split", [], [20, 15, 10], "x=40"),
        ("flat", "flat", [], [20, 15, 10], "x=40"),
        ("hex", "hex", [], [20, 15, 10], "x=40"),
        ("turned", "slot", [["z", 90]], [-15, 20, 10], "y=40"),
        ("over", "slot", [["x", 180]], [20, -15, -10], "x=40"),
    ):
        parts = [
            {"name": side, "file": f"{side}-{file}.step", "layers": "z", "turn": turns}
            for side, file in (("one", "round"), ("two", two_is))
        ]
        data = {"parts": parts, "joints": [{**joint, "at": [at]}],
                "held": [{"plane": held, "how": "fixed"}]}  # fmt: skip
        (out / f"{name}.json").write_text(json.dumps(data))


def unreadable() -> None:
    """What `clearance` can't read (mqucifer/print-shop#244): a box turned inside out, a loft
    whose mesh doesn't close, and a prism that crosses itself, with a lobe facing inward and its
    whole volume more than nothing. And an STL with an enclosed hollow."""
    out = Path("untrusted")
    out.mkdir(exist_ok=True)
    V, solid = cq.Vector, cq.Solid.makeBox

    def step(name: str, shape: Any) -> None:
        save(cq.Workplane().add(shape), f"untrusted/{name}")

    step("block", solid(70, 70, 8, V(-25, -25, 40)))
    step("beside", solid(10, 10, 8, V(120, 120, 40)))
    step("on-top", solid(20, 20, 5, V(0, 0, 60)))
    step("good", solid(20, 20, 60))
    mesh = trimesh.creation.box(extents=(20, 20, 60))
    mesh.apply_translation((10, 10, 30))
    mesh.export(out / "good-mesh.stl")
    hole = trimesh.creation.box(extents=(6, 6, 20))
    hole.apply_translation((10, 10, 30))
    hole.faces = hole.faces[:, ::-1]
    both: Any = trimesh.util.concatenate([mesh, hole])
    both.export(out / "hollow.stl")
    mesh.faces = mesh.faces[:, ::-1]
    mesh.export(out / "inward.stl")
    away = mesh.copy()
    away.apply_translation((200, 200, 0))
    away.export(out / "inward-away.stl")

    def octagon(wide: float, z: float) -> Any:
        c = 0.3 * wide
        a, b = wide - c, wide
        points = [(-a, -b), (a, -b), (b, -a), (b, a), (a, b), (-a, b), (-b, a), (-b, -a)]
        return cq.Wire.makePolygon([V(10 + x, 10 + y, z) for x, y in points], close=True)

    heights = ((0, 10), (2, 10), (30, 10), (32, 3), (60, 3))
    step("unclosed-loft", cq.Solid.makeLoft([octagon(w, z) for z, w in heights], ruled=False))
    # Its outline crosses itself at (8, 16): 320 mm² out, and a lobe of 20 mm² to y 20 inward.
    outline = [(0, 0), (40, 0), (0, 20), (10, 20)]
    corners = np.array([(*p, z) for z in (0.0, 10.0) for p in outline])
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
    for k in range(4):
        n = (k + 1) % 4
        faces += [(k, n, n + 4), (k, n + 4, k + 4)]
    trimesh.Trimesh(corners, np.array(faces), process=False).export(out / "crossed.stl")
    step("in-lobe", solid(1, 1, 6, V(6, 18.5, 2)))
    step("at-lobe", solid(1, 1, 6, V(6, 20.3, 2)))
    # In the lobe by 0.00003 mm: -0.00018 mm³ of overlap, inside the 0.001 band.
    step("on-lobe", solid(1, 1, 6, V(6, 20 - 3e-5, 2)))


def near_touching() -> None:
    """Two leaves of a hinge, one swung over the other with faces all but coincident."""
    a = box(-25, -3.3, -15, 15, -1.5, 1.5)
    for y0, y1 in ((-15, -5.15), (5.15, 15)):
        a = a.union(along_y(3, y0, y1)).union(box(-3.3, -2, y0, y1, -1.5, 1.5))
    a = a.union(along_y(1.5, -15, 15))
    b = (
        box(3.3, 25, -15, 15, -1.5, 1.5)
        .union(along_y(3, -4.85, 4.85, hole=1.8))
        .union(box(2, 3.3, -4.85, 4.85, -1.5, 1.5))
    )
    save(a, "hinge-a")
    save(b, "hinge-b")
    moved = {"y03-p3e5": (0.3, 3e-5), "y03-p1e3": (0.3, 1e-3), "y06-m3e4": (0.6, -3e-4)}
    for name, (y, angle) in moved.items():
        shape = b.val().moved(cq.Location(cq.Vector(0, y, 0), cq.Vector(0, 1, 0), 180 + angle))
        save(cq.Workplane(obj=shape), f"hinge-b-{name}")


def boxes() -> None:
    """A 10 mm cube, and another beside it: every overlap and gap known by arithmetic."""
    save(box(0, 10, 0, 10, 0, 10), "cube")
    for name, x in (
        ("gap-0p1", 10.1),
        ("gap-0p001", 10.001),
        ("touch", 10.0),
        ("overlap-0p001", 9.999),
        ("overlap-1", 9.0),
    ):
        save(box(x, x + 10, 0, 10, 0, 10), f"cube-{name}")
    for name, angle in (("tilt-1e-5", 1e-5), ("tilt-1e-3", 1e-3)):
        shape = box(10, 20, 0, 10, 0, 10).val().rotate((10, 5, 5), (10, 5, 6), angle)
        save(cq.Workplane(obj=shape), f"cube-{name}")
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.apply_translation((5, 5, 5))
    mesh.export("cube.stl")
    for name, x in (("gap-0p1", 10.1), ("overlap-1", 9.0)):
        other = trimesh.creation.box((10, 10, 10))
        other.apply_translation((x + 5, 5, 5))
        other.export(f"cube-{name}.stl")


def pin_in_hole() -> None:
    plate = cq.Workplane("XY").box(30, 30, 8).faces(">Z").workplane().hole(10.1)
    save(plate, "plate")
    save(cq.Workplane("XY").circle(5.0).extrude(20).translate((0, 0, -10)), "pin")
    save(cq.Workplane("XY").circle(5.1).extrude(20).translate((0, 0, -10)), "pin-tight")
    save(cq.Workplane("XY").circle(5.05).extrude(20).translate((0, 0, -10)), "pin-exact")


def pockets() -> None:
    """A block; a pocket cut well; and the same operation aimed where the block isn't."""
    block = box(0, 60, 0, 40, 0, 20)
    save(block, "block")
    cutter = cq.Workplane("XY").circle(11).extrude(7).translate((30, 20, 13))
    save(cutter, "cutter")
    save(block.cut(cutter), "block-seat")
    away = cq.Workplane("XY").circle(11).extrude(7).translate((130, 20, 13))
    save(away, "cutter-away")
    save(block.cut(away), "block-missed")
    # A seat whose centre is off the block's edge: no error, watertight, one body, mostly air.
    air = cq.Workplane("XY").circle(11).extrude(7).translate((60, 20, 13))
    save(air, "cutter-air")
    save(block.cut(air), "block-seat-air")
    # A very small cut in a large part: it must not read as nothing.
    save(block.cut(box(29, 30, 19, 20, 19, 20)), "block-nick")
    # Along x, so a feature's axis needn't be z.
    across = cq.Workplane("YZ").circle(5).extrude(10).translate((0, 20, 10))
    save(block.cut(across), "block-bore-x")
    # The wrong size, in the right place.
    wide = cq.Workplane("XY").circle(11.5).extrude(7).translate((30, 20, 13))
    save(block.cut(wide), "block-seat-wide")


def volumes() -> None:
    """Volumes a tool got wrong while calling them valid."""
    form = trimesh.creation.icosphere(subdivisions=4, radius=20)
    form.export("form.stl")
    # A "repair" that keeps 5% of the volume and comes out perfectly watertight.
    kept = trimesh.creation.icosphere(subdivisions=4, radius=20 * 0.05 ** (1 / 3))
    kept.export("form-repaired.stl")
    inverted = form.copy()
    inverted.invert()
    inverted.export("form-inverted.stl")
    holed = form.copy()
    holed.update_faces(np.arange(len(holed.faces)) != 3)
    holed.export("form-holed.stl", file_type="stl")
    # A thin shell of a sphere. OCCT calls it valid and gives the solid sphere's volume.
    save(cq.Workplane().sphere(20).faces(">Z").shell(-0.02), "sphere-shell")


def frames() -> None:
    """A frame with three pockets under a lip, one of them open under the lip along one edge."""
    plate = box(-60, 60, -30, 30, 0, 4)
    pocket = (-50, -10, -20, 20)
    plate = plate.cut(box(pocket[0], pocket[1], pocket[2], pocket[3], 1, 3))
    plate = plate.cut(box(pocket[0] + 2, pocket[1] - 2, pocket[2] + 2, pocket[3] - 2, 3, 4))
    plate = plate.cut(box(-5, 15, -20, 20, 1, 3)).cut(box(-3, 13, -18, 18, 3, 4))
    # This one's right-hand window runs to the pocket's edge: 26 mm of that edge has no lip.
    plate = plate.cut(box(20, 50, -15, 15, 1, 3)).cut(box(22, 50, -13, 13, 3, 4))
    # Every pocket needs a way through to light, below as well as above.
    plate = plate.cut(box(-48, -12, -18, 18, 0, 1))
    plate = plate.cut(box(-3, 13, -18, 18, 0, 1))
    plate = plate.cut(box(22, 48, -13, 13, 0, 1))
    # And a plain hole, which has no lip and holds no pane.
    plate = plate.cut(box(-58, -54, -2, 2, 0, 4))
    save(plate, "frame")
    plate.val().exportStl("frame.stl", tolerance=0.002)
    # Ceilings at other heights: on a layer's mid-height (3.1), and between two (3.15).
    for name, ceiling in (("tie", 3.1), ("between", 3.15)):
        off = box(-30, 30, -30, 30, 0, ceiling + 1)
        off = off.cut(box(-20, 20, -20, 20, 1, ceiling))
        off = off.cut(box(-18, 18, -18, 18, ceiling, ceiling + 1))
        off = off.cut(box(-18, 18, -18, 18, 0, 1))
        save(off, f"frame-{name}")
    # A pocket open at the underside: a pane stands on the build plate, under a lip.
    bed = (
        box(-30, 30, -30, 30, 0, 4)
        .cut(box(-20, 20, -20, 20, 0, 3))
        .cut(box(-18, 18, -18, 18, 3, 4))
    )
    save(bed, "frame-bed")
    # A plate with one sharp point of material, about 23 degrees, and no thin wall.
    sharp = box(-30, 0, -30, 30, 0, 4).union(
        cq.Workplane("XY").polyline([(0, -5), (25, 0), (0, 5)]).close().extrude(4)
    )
    save(sharp, "frame-sharp")
    # Walls drafted: the opening at mid-height is wider than it is lower down.
    tapered = (
        cq.Workplane("XY")
        .rect(40, 40)
        .workplane(offset=2)
        .rect(41, 41)
        .loft(combine=True)
        .translate((0, 0, 1))
    )
    draft = box(-30, 30, -30, 30, 0, 4).cut(tapered).cut(box(-19, 19, -19, 19, 0, 1))
    draft = draft.cut(box(-19, 19, -19, 19, 3, 4))
    save(draft, "frame-draft")
    # An opening too small to hold a pane worth printing.
    small = (
        box(-30, 30, -30, 30, 0, 4).cut(box(-20, -5, -7, 8, 0, 4)).cut(box(10, 11.5, 0, 1.5, 0, 4))
    )
    save(small, "frame-small")
    # A frame with no lip: panes drop in from the top, and nothing closes over them.
    open_top = box(-30, 30, -30, 30, 0, 3).cut(box(-20, 20, -20, 20, 1, 3))
    open_top = open_top.cut(box(-18, 18, -18, 18, 0, 1))
    save(open_top, "frame-open")


def pane_frame(window_up: Any, window_down: Any, name: str) -> None:
    """A 40 mm pocket from z=1 to z=3 in a plate 4 tall, with a window cut each way out of it."""
    plate = box(-30, 30, -30, 30, 0, 4).cut(box(-20, 20, -20, 20, 1, 3))
    if window_up is not None:
        plate = plate.cut(window_up)
    if window_down is not None:
        plate = plate.cut(window_down)
    save(plate, name)


def captured() -> None:
    """Panes held by shape, and each way one isn't. Every lip is known by arithmetic."""
    up = box(-18, 18, -18, 18, 3, 4)
    down = box(-18, 18, -18, 18, 0, 1)
    pane_frame(up, down, "held")
    # Lip 0.05 mm in the pocket, so a pane with 0.1 mm of play falls straight through.
    pane_frame(box(-19.95, 19.95, -19.95, 19.95, 3, 4), down, "held-through")
    # Centred, a pane catches 0.1 mm of lip on one side. Moved by its play, it comes straight out.
    pane_frame(box(-19.8, 20.5, -20, 20, 3, 4), down, "held-shifted")
    # A lip along one edge only, and nothing else above: it swings out about that edge.
    pane_frame(box(-25, 25, -25, 18, 3, 4), down, "held-hinge")
    # A window off to one side: the pane has 1.9 mm of lip, and 0.1 mm along one edge.
    pane_frame(box(-18, 19.8, -18, 18, 3, 4), down, "held-thin")
    # The pocket's cut run on through the top face, as a prism given the pocket's top for its
    # depth would: the hole over the pane is the pocket's own outline, all round.
    pane_frame(box(-20, 20, -20, 20, 3, 4), down, "held-pocket-through")
    # A top opening 2 mm wider than the pocket on its right-hand side only, and 0.3 mm narrower
    # on the others: 0.2 mm over a pane with 0.1 mm of play, under the 0.4 a lip needs after it.
    pane_frame(box(-19.7, 22, -19.7, 19.7, 3, 4), down, "held-wide-side")
    # A lip 0.1 mm thick, under a layer: the pocket's ceiling at 3.9.
    thin_lip = box(-30, 30, -30, 30, 0, 4).cut(box(-20, 20, -20, 20, 1, 3.9))
    save(thin_lip.cut(up).cut(down), "held-thin-lip")
    # A lip that slopes back at 45 degrees, as a lip printed with no support does.
    sloped = (
        cq.Workplane("XY")
        .workplane(offset=3)
        .rect(40, 40)
        .workplane(offset=1)
        .rect(38, 38)
        .loft(combine=True)
    )
    slope_frame = box(-30, 30, -30, 30, 0, 4).cut(box(-20, 20, -20, 20, 1, 3)).cut(sloped).cut(down)
    save(slope_frame, "held-sloped")
    # Round, in a mesh too, since a real pane isn't a square.
    round_frame = (
        cq.Workplane("XY")
        .circle(30)
        .extrude(4)
        .cut(cq.Workplane("XY").workplane(offset=1).circle(20).extrude(2))
        .cut(cq.Workplane("XY").workplane(offset=3).circle(18).extrude(1))
        .cut(cq.Workplane("XY").circle(18).extrude(1))
    )
    save(round_frame, "held-round")
    round_frame.val().exportStl("held-round.stl", tolerance=0.002)
    # Two pockets under one ceiling, the second with its floor 0.5 mm higher.
    floors = (
        box(-50, 50, -30, 30, 0, 4)
        .cut(box(-45, -5, -20, 20, 1, 3))
        .cut(box(5, 45, -20, 20, 1.5, 3))
        .cut(box(-43, -7, -18, 18, 3, 4))
        .cut(box(7, 43, -18, 18, 3, 4))
        .cut(box(-43, -7, -18, 18, 0, 1))
        .cut(box(7, 43, -18, 18, 0, 1.5))
    )
    save(floors, "frame-floors")
    # A pocket the first layer closes: a 0.1 mm slot on the plate, in a tall part.
    first = box(-30, 30, -30, 30, 0, 8).cut(box(-20, 20, -20, 20, 0, 0.1))
    save(first, "frame-first")


def island_frame(island: tuple[float, float, float, float], name: str) -> Any:
    """`cup-1`'s shape from boxes: a 60 by 40 mm pocket from z=1 to z=3 with an island of frame
    inside it, a 2 mm ring round a 16 by 12 mm pocket of its own. The island's joined to the rest
    only through the lips above and below it, by a bridge each side, so its pane has a hole.
    Every lip is 2 mm, so 1.9 mm with 0.1 mm of play."""
    x0, x1, y0, y1 = island
    plate = box(-40, 40, -30, 30, 0, 4).cut(box(-30, 30, -20, 20, 1, 3))
    for z0, z1 in ((0, 1), (3, 4)):
        plate = plate.cut(box(-28, 28, -18, 18, z0, z1))
        lip = (max(x0 - 2, -30), min(x1 + 2, 30), max(y0 - 2, -20), min(y1 + 2, 20))
        plate = plate.union(box(*lip, z0, z1))
        if y0 > -18:
            plate = plate.union(box(-1, 1, -18, y0, z0, z1)).union(box(-1, 1, y1, 18, z0, z1))
    plate = plate.union(box(x0, x1, y0, y1, 1, 3))
    plate = plate.cut(box(-8, 8, -6, 6, 1, 3))
    plate = plate.cut(box(-6, 6, -4, 4, 0, 1)).cut(box(-6, 6, -4, 4, 3, 4))
    save(plate, name)
    return plate


def islands() -> None:
    """Frames with an island of frame inside an opening, each read by arithmetic."""
    Path("island").mkdir(exist_ok=True)
    island_frame((-10, 10, -8, 8), "island/frame-island")
    # Its panes, named as a model might: the bench numbers the larger opening first, so its
    # pane 1 is pane_2.step. On `cup-1` reading one for the other cost 33 calls.
    save(box(-7.9, 7.9, -5.9, 5.9, 1, 3), "island/pane_1")
    save(
        box(-29.9, 29.9, -19.9, 19.9, 1, 3).cut(box(-10.1, 10.1, -8.1, 8.1, 1, 3)), "island/pane_2"
    )
    # An island 0.15 mm from the opening's right-hand edge: with 0.1 mm of play the pane can't
    # pass between, so it goes round the island open, as a C, as on `cup-1`'s first round.
    island_frame((-10, 29.85, -8, 8), "frame-island-open")
    # An island 0.15 mm from the top and the bottom: the pane round it comes apart in two.
    island_frame((-10, 10, -19.85, 19.85), "frame-island-split")


def plays() -> None:
    """Two 40 mm pockets side by side, each with 2 mm lips, and a pane file for each: one with
    0.1 mm of play all round, one with 1.5 (mqucifer/print-shop#54). On `eval-choice-1` two plays
    had one `--clearance`, and nothing in `capture`'s reply said so. At 0.1 mm the bench's pane
    shares 86% with the 1.5 mm file, too little to match it, as the eval's small pane did."""
    Path("plays").mkdir(exist_ok=True)
    frame = box(-50, 50, -30, 30, 0, 4)
    for x in (-25, 25):
        frame = frame.cut(box(x - 20, x + 20, -20, 20, 1, 3))
        for z0, z1 in ((0, 1), (3, 4)):
            frame = frame.cut(box(x - 18, x + 18, -18, 18, z0, z1))
    save(frame, "plays/frame")
    save(box(-44.9, -5.1, -19.9, 19.9, 1, 3), "plays/pane_1")
    save(box(6.5, 43.5, -18.5, 18.5, 1, 3), "plays/pane_2")


def rings() -> None:
    """One 80 mm ring, 5 mm wide and 3 mm thick, built two ways (mqucifer/print-shop#54): from
    polygons of 96 segments a quarter, as Shapely's points through `polyline` gave the runs, and
    from two circles. Each of the 768 sides is a flat strip 0.57 and 0.50 mm wide, by arithmetic:
    2 x 40 x sin(pi / 384) and 2 x 35 x sin(pi / 384)."""
    angles = np.linspace(0, 2 * np.pi, 384, endpoint=False)
    outer = [(40 * np.cos(a), 40 * np.sin(a)) for a in angles]
    inner = [(35 * np.cos(a), 35 * np.sin(a)) for a in angles]
    faceted = cq.Workplane("XY").polyline(outer).close().polyline(inner).close().extrude(3)
    save(faceted, "ring-faceted")
    save(cq.Workplane("XY").circle(40).circle(35).extrude(3), "ring-curved")


def hanging() -> None:
    """Holes whose least wall is known by arithmetic."""
    # A 3 mm hole at y=121.5 in a top rail from y=117 to 125: 2.0 mm to the outside, 3.0 to the
    # window below.
    plate = box(0, 100, 0, 125, 0, 4).cut(box(10, 90, 10, 117, 0, 4))
    save(plate.cut(cq.Workplane("XY").center(50, 121.5).circle(1.5).extrude(4)), "hang")
    # The window runs up to y=119: 1.0 mm below the hole, toward the window.
    plate = box(0, 100, 0, 125, 0, 4).cut(box(10, 90, 10, 119, 0, 4))
    save(plate.cut(cq.Workplane("XY").center(50, 121.5).circle(1.5).extrude(4)), "hang-thin")
    # Along y, through a block 10 tall: 3.5 mm above and below it, 8.5 each side.
    bore = cq.Workplane("XZ").center(10, 5).circle(1.5).extrude(-30)
    save(box(0, 20, 0, 30, 0, 10).cut(bore), "bore-y")
    # A square hole and a square opening corner to corner: the least wall, 1.414 mm, runs between
    # two corners, square to neither, so rays square to the hole's wall can't read it.
    save(box(0, 20, 0, 20, 0, 4).cut(box(5, 8, 5, 8, 0, 4)).cut(box(9, 12, 9, 12, 0, 4)), "corners")


def closes() -> None:
    """A plate with a 3 mm hole through it, a part to judge close up. Its test piece sits apart."""
    save(cq.Workplane("XY").box(100, 100, 4).faces(">Z").workplane().hole(3), "plate-3mm")
    save(box(60, 80, -10, 10, -2, 2), "plate-test-piece")


def thin_parts() -> None:
    base = box(0, 20, 0, 20, 0, 2)
    save(base.union(box(5, 15, 9.85, 10.15, 2, 8)), "wall-0p3")
    save(base.union(box(5, 15, 9.6, 10.4, 2, 8)), "wall-0p8")
    # A thin fin low on a tall post: one section at mid-height doesn't cross it.
    save(box(0, 10, 0, 10, 0, 20).union(box(10, 20, 4.85, 5.15, 0, 2)), "fin-low")
    # A cylinder on its side: narrow only in a strip at its top, which no layer prints.
    save(along_y(2.5, -14, 14), "lying")
    # A wire on its side, thin all along: narrower than the nozzle in every layer it has.
    save(box(-10, 10, -10, -8, -3, 3).union(along_y(0.15, -8, 8)), "wire")
    # A disc through a block: cusps where its curve meets the block's flat faces.
    cusped = cq.Workplane("XY").box(5, 50, 80, centered=(False, True, True))
    save(cusped.union(cq.Workplane("YZ").circle(26).extrude(30)), "cusped")
    # A floor thinner than a layer.
    floor = box(0, 20, 0, 20, 0, 5).cut(box(2, 18, 2, 18, 0.1, 5))
    save(floor, "floor-0p1")
    # Two good bodies, and one.
    save(box(0, 20, 0, 20, 0, 5), "one-body")
    save(
        cq.Workplane(obj=cq.Compound.makeCompound([base.val(), box(30, 50, 0, 20, 0, 2).val()])),
        "two-bodies",
    )
    # A body far too small to print, beside a good one.
    save(
        cq.Workplane(
            obj=cq.Compound.makeCompound([base.val(), box(30, 30.5, 0, 0.5, 0, 0.5).val()])
        ),
        "speck",
    )


def rim_points() -> None:
    """A plate 20 mm thick leant at 45°, and 5.5 mm bores square to it (mqucifer/print-shop#238):
    at each rim one layer's section leaves a point. And what keeps such a point thin."""
    out = Path("rim")
    out.mkdir(exist_ok=True)
    V = cq.Vector
    plate = cq.Solid.makeBox(20, 50, 50, V(-10, -25, -25)).rotate(V(), V(0, 1, 0), 45)
    plain = plate.intersect(cq.Solid.makeBox(200, 200, 28, V(-100, -100, -14)))
    plain = plain.translate((0, 0, 14))

    def cut(
        part: Any,
        start: float = -30.0,
        end: float = 30.0,
        y: float = 0.0,
        radius: float = 2.75,
        lean: float = 45.0,
    ) -> Any:
        way = V(np.cos(np.radians(lean)), 0, -np.sin(np.radians(lean)))
        at = V(0, y, 14) + way * start
        return part.cut(cq.Solid.makeCylinder(radius, end - start, at, way))

    bored = cut(plain)
    made = {
        "plain": plain,
        "bored": bored,
        "turned": bored.rotate(V(), V(0, 0, 1), 17).translate((183.3, 122.2, 5)),
        "blind": cut(plain, end=0),
        "counterbore": cut(bored, end=-6, radius=5.2),
        # What keeps it thin: 0.3 mm to the plate's side, and 0.3 mm over the bore's end.
        "side": cut(plain, y=25 - 2.75 - 0.3),
        "skin": cut(plain, end=10 - 0.3),
        "skin-up": cut(plain, start=-10 + 0.3),
        # A rim sharper than square, the bore 5° off the face's square.
        "sharp": cut(plain, lean=50),
    }
    # The far end breaking out at a slant through the top, 0.199 mm square to the bore at its
    # edge, which is no wall; and with the bore 0.3 mm from the plate's side as well.
    slant = V(np.cos(np.radians(45)), 0, -np.sin(np.radians(45)))
    for name, y in (("breakout", 0.0), ("breakout-side", 25 - 2.75 - 0.3)):
        at = V(0, y, 25.2) - slant * 40
        made[name] = plain.cut(cq.Solid.makeCylinder(2.75, 80, at, slant))
    # A hole so short that all of its surface is within a nozzle of an end, 0.3 mm from the side.
    sheet = cq.Solid.makeBox(0.7, 50, 50, V(-0.35, -25, -25)).rotate(V(), V(0, 1, 0), 45)
    sheet = sheet.intersect(cq.Solid.makeBox(200, 200, 28, V(-100, -100, -14)))
    made["short"] = cut(sheet.translate((0, 0, 14)), y=25 - 2.75 - 0.3)
    eight = plain
    for y in (-21, -15, -9, -3, 3, 9, 15, 21):
        eight = cut(eight, y=y)
    made["eight"] = eight
    # A rib one layer tall and 0.3 mm wide round an upright bore's rim.
    slab = cq.Solid.makeBox(20, 20, 5, V(-10, -10, 0))
    rib = cq.Solid.makeCylinder(3.05, 0.2, V(0, 0, 5))
    made["rib"] = slab.fuse(rib).cut(cq.Solid.makeCylinder(2.75, 8, V(0, 0, -1)))
    for name, shape in made.items():
        save(cq.Workplane().add(shape), f"rim/{name}")
    cq.exporters.export(
        cq.Workplane().add(bored), "rim/bored.stl", tolerance=0.01, angularTolerance=0.1
    )


def designs() -> None:
    """Design scripts for the parameter check, as a model would write them."""
    ring = """
import cadquery as cq

BaseCircle = 20.0
Thickness = 3.0
HolePlace = 0.0
Unused = 7.0
derived = BaseCircle * 2

# The centre follows BaseCircle. The outer ring was typed in, and doesn't: the Fusion finding.
centre = cq.Workplane().circle(BaseCircle).extrude(Thickness)
ring = cq.Workplane().circle(60.0).circle(50.0).extrude(Thickness)
motif = cq.Workplane().center(35, 0).rect(6, 6).extrude(Thickness)
plate = cq.Workplane().center(0, 90).rect(30, 30).extrude(Thickness)
plate = plate.faces(">Z").workplane(origin=(HolePlace, 90, 0)).hole(6)
parts = [centre.val(), ring.val(), motif.val(), plate.val()]
cq.exporters.export(cq.Workplane(obj=cq.Compound.makeCompound(parts)), "design.step")
"""
    Path("design.py").write_text(ring)
    Path("design-random.py").write_text(
        """
import random
import cadquery as cq

Size = 10.0
cq.exporters.export(
    cq.Workplane().box(Size, Size, random.uniform(1, 9)), "random.step"
)
"""
    )
    Path("design-fails.py").write_text(
        """
import cadquery as cq

Size = 10.0
if Size > 20:
    raise ValueError("too big for the frame")
cq.exporters.export(cq.Workplane().box(Size, Size, Size), "fails.step")
"""
    )
    Path("design-twice.py").write_text(
        """
import cadquery as cq

Size = 10.0
Size = 12.0
cq.exporters.export(cq.Workplane().box(Size, Size, Size), "twice.step")
"""
    )
    Path("design-nothing.py").write_text("Size = 10.0\nprint(Size)\n")


SHEETS_X = 100.0


def sheets() -> None:
    """What a sheet must show: panes in their pockets, a pane with a hole round an island of
    frame, files that would share a colour in a cycle of 5, a file hidden in another, and two
    that take the same space. All 100 mm along x, clear of the other cases, as `capture` names a
    file beside a frame whose outline it shares."""

    def save(shape: Any, name: str) -> None:
        cq.exporters.export(shape.translate((SHEETS_X, 0, 0)), f"{name}.step")

    # A frame 60 x 40 x 5 with two pockets at z 2 to 4, each with a window through, and its panes.
    frame = box(0, 60, 0, 40, 0, 5)
    for x0 in (5, 32):
        frame = frame.cut(box(x0, x0 + 23, 5, 35, 2, 4))
        frame = frame.cut(box(x0 + 1.5, x0 + 21.5, 6.5, 33.5, 0, 5))
    save(frame, "glass-frame")
    for k, x0 in enumerate((5, 32)):
        save(box(x0 + 0.2, x0 + 22.8, 5.2, 34.8, 2, 3.8), f"glass-pane-{k + 1}")
    # One opening with an island of frame standing in it: its pane has a
    # hole for the island. At z=3 the frame is the outer ring and the island, 8 x 8 mm.
    island = box(0, 40, 0, 40, 0, 4).cut(box(5, 35, 5, 35, 1, 3))
    island = island.cut(box(7, 33, 7, 33, 0, 1)).cut(box(7, 33, 7, 33, 3, 4))
    island = island.union(box(16, 24, 16, 24, 0, 4))
    save(island, "island-frame")
    holed = box(5.2, 34.8, 5.2, 34.8, 1, 2.8).cut(box(15.8, 24.2, 15.8, 24.2, 1, 2.8))
    save(holed, "holed-pane")
    # Seven boxes in a row: a cycle of 5 colours would draw the 6th in the 1st's.
    for k in range(7):
        save(box(12 * k, 12 * k + 10, 0, 10, 0, 3), f"row-{k + 1}")
    # A box wholly inside another, which no view can show.
    save(box(0, 20, 0, 20, 0, 10), "outer")
    save(box(5, 10, 5, 10, 2, 6), "hidden")
    # Two that take the same space: 10 x 10 x 3 of them shared.
    save(box(0, 20, 0, 10, 0, 3), "share-a")
    save(box(10, 30, 0, 10, 0, 3), "share-b")


def glass() -> None:
    """A frame of three cells whose light and thickness are known by arithmetic (mqucifer/print-
    shop#37). Each pane is 11.5 or 3.5 mm square in a pocket 0.25 mm larger all round, 1 mm thick.
    - `pane_a`: a 10 mm opening, so 100 of 132.25 mm² open, 75.61%.
    - `pane_b`: a floor under it, so none open: the fault on `suncatcher-2`'s first round.
    - `pane_c`: a 2 mm opening, so 4 of 12.25 mm², 32.65%. Its opening is 3 mm from `pane_a`'s,
      the narrowest band of frame a person sees.
    """
    Path("glass").mkdir(exist_ok=True)
    frame = box(0, 42, 0, 20, 0, 4)
    frame = frame.cut(box(5, 15, 5, 15, 0, 4)).cut(box(4, 16, 4, 16, 1.5, 2.5))
    frame = frame.cut(box(18, 20, 9, 11, 0, 4)).cut(box(17, 21, 8, 12, 1.5, 2.5))
    frame = frame.cut(box(25, 37, 4, 16, 1.5, 2.5)).cut(box(26, 36, 5, 15, 2.5, 4))
    save(frame, "glass/frame")
    save(box(4.25, 15.75, 4.25, 15.75, 1.5, 2.5), "glass/pane_a")
    save(box(25.25, 36.75, 4.25, 15.75, 1.5, 2.5), "glass/pane_b")
    save(box(17.25, 20.75, 8.25, 11.75, 1.5, 2.5), "glass/pane_c")
    # A rim sized to the visible share (criterion 6), at a full rim of 1.2 mm, a least of 0.55,
    # both before play, and 60% visible, with 0.25 mm of play and pockets at z 1.5 to 2.5.
    # - A pane 19.5 mm square keeps 81.5% visible at the full rim, so it needs 1.2 mm.
    # - One 5.5 mm square keeps 42.8% at 1.2 mm and 79.3% at 0.55: it needs the rim whose overlap
    #   w leaves (5.5 - 2w)² = 60% of 30.25 mm², w = 0.6199, so 0.8699 mm before play.
    # - One 1.5 mm square keeps 36% even at 0.55 mm: it's to be removed.
    # `rims/held` has the first two at 1.2 and 0.87 mm; `rims/unheld` has the 5.5 mm pane at
    # 1.2 mm, and the 1.5 mm one at 0.55; `rims/both-at-1` has the first two at 1.0 mm, so the
    # big pane's rim is to widen by 0.2 and the small one's to narrow by 0.131
    # (mqucifer/print-shop#65).
    Path("rims").mkdir(exist_ok=True)
    for name, big_rim, small_rim, tiny in (
        ("held", 1.2, 0.87, False),
        ("unheld", 1.2, 1.2, True),
        ("both-at-1", 1.0, 1.0, False),
    ):
        rims = box(0, 40, 0, 24, 0, 4)
        rims = rims.cut(box(2, 22, 2, 22, 1.5, 2.5))
        rims = rims.cut(box(2 + big_rim, 22 - big_rim, 2 + big_rim, 22 - big_rim, 0, 4))
        rims = rims.cut(box(26, 32, 9, 15, 1.5, 2.5))
        rims = rims.cut(box(26 + small_rim, 32 - small_rim, 9 + small_rim, 15 - small_rim, 0, 4))
        if tiny:
            rims = rims.cut(box(35, 37, 11, 13, 1.5, 2.5))
            rims = rims.cut(box(35.55, 36.45, 11.55, 12.45, 0, 4))
        save(rims, f"rims/{name}")
    # A pane 2.71 mm square needs 0.5554 mm: the reply says 0.555, rounded down to the
    # thousandth, which leaves 60.05% visible. `rims/thousandth` has one cell at 0.555, which
    # passes, and one at 0.60, which is told to set 0.555.
    rims = box(0, 12, 0, 6, 0, 4)
    for x0, rim in ((1.0, 0.555), (7.0, 0.6)):
        x1 = x0 + 3.21
        rims = rims.cut(box(x0, x1, 1.0, 4.21, 1.5, 2.5))
        rims = rims.cut(box(x0 + rim, x1 - rim, 1.0 + rim, 4.21 - rim, 0, 4))
    save(rims, "rims/thousandth")
    # A pane twice as thick, and one with a ridge 0.5 mm higher along one edge.
    save(box(0, 10, 0, 10, 0, 2), "glass/pane_thick")
    save(box(0, 10, 0, 10, 0, 1).union(box(0, 10, 9.5, 10, 1, 1.5)), "glass/pane_ridge")


def enclosure() -> None:
    """Edges and lids whose answers are known by arithmetic (mqucifer/print-shop#60).
    - `round2`: a 40 x 30 x 20 block, every edge rounded 2 mm; `mixed`, its upright edges 3 mm,
      its top 1.5 mm and its bottom left sharp, so 8 sharp edges: 4 straight, 4 round a corner.
    - `strips`: a slab whose upright edges are rounded 3 mm as 16 flat strips each.
    - `tray`: 40 x 30 x 15, its upright edges rounded 3 mm and its bottom 2 mm, its top rim
      sharp, walls and floor 2 mm, and a 4 mm post 6 mm tall in the middle. Open, a finger
      meets 16 sharp edges: the rim's 8 outside, its 4 inside and the post's top 4.
    - `tray-lid`: a 3 mm lid of the same outline, flush on the rim, its top rounded 2 mm.
      Closed, nothing sharp is in reach: the rim and the lid's underside meet flush.
    - `lid-lip-P-up`: a lid with a lip 3 mm deep inside the walls, P mm of play all round,
      lifted 0.5 mm off its seat, so the least gap to the tray is the play, or 0.5.
    - `lid-lip-P`: the same lids seated, as printed: `clearance --lift 0.5` reads them as the
      lifted copies (mqucifer/print-shop#65).
    - `curved-pocket` and `curved-pane`: a pane 20 x 14 with corners rounded 3 mm in a pocket
      0.25 mm wider all round, its corners 3.25, so the gap is 0.25 on a curve as well as flat.
    Shelling a block whose every edge is rounded fails quietly in CadQuery 2.8, so the tray
    is cut, not shelled.
    """
    Path("enclosure").mkdir(exist_ok=True)
    Path("references").mkdir(exist_ok=True)
    save(cq.Workplane().box(40, 30, 20).edges().fillet(2), "enclosure/round2")
    mixed = cq.Workplane().box(40, 30, 20).edges("|Z").fillet(3).faces(">Z").edges().fillet(1.5)
    save(mixed, "enclosure/mixed")
    corners = []
    for cx, cy, a0 in ((17, 12, 0), (-17, 12, 90), (-17, -12, 180), (17, -12, 270)):
        for i in range(17):
            a = np.radians(a0 + 90 * i / 16)
            corners.append((cx + 3 * float(np.cos(a)), cy + 3 * float(np.sin(a))))
    save(cq.Workplane().polyline(corners).close().extrude(5), "enclosure/strips")
    tray = box(-20, 20, -15, 15, 0, 15).edges("|Z").fillet(3).faces("<Z").edges().fillet(2)
    tray = tray.cut(box(-18, 18, -13, 13, 2, 15)).union(box(-2, 2, -2, 2, 2, 8))
    save(tray, "enclosure/tray")
    plate = box(-20, 20, -15, 15, 15, 18).edges("|Z").fillet(3).faces(">Z").edges().fillet(2)
    save(plate, "enclosure/tray-lid")
    save(plate, "references/tray-lid")
    for play, name in ((0.2, "0p2"), (0.6, "0p6")):
        lip = box(-18 + play, 18 - play, -13 + play, 13 - play, 12, 15)
        save(plate.union(lip).translate((0, 0, 0.5)), f"enclosure/lid-lip-{name}-up")
    save(plate.union(box(-17.8, 17.8, -12.8, 12.8, 12, 15)), "enclosure/lid-lip-0p2")
    save(plate.union(box(-17.4, 17.4, -12.4, 12.4, 12, 15)), "enclosure/lid-lip-0p6")

    def rounded(w: float, h: float, r: float, z0: float, z1: float) -> Any:
        return box(-w / 2, w / 2, -h / 2, h / 2, z0, z1).edges("|Z").fillet(r)

    save(rounded(20, 14, 3, 1, 2), "enclosure/curved-pane")
    save(box(-15, 15, -12, 12, 0, 3).cut(rounded(20.5, 14.5, 3.25, 0.5, 2.5)),
         "enclosure/curved-pocket")  # fmt: skip


def places() -> None:
    """Gaps of a mated pair read one place at a time (mqucifer/print-shop#72), on parts of our own.

    - `places/pane` and `places/pocket`: a pane 20 x 14, its corners rounded 3 mm, 1.2 mm thick,
      in a frame's pocket 0.25 mm wider round its edge (corners 3.25) and 0.15 mm over each face,
      with a lip 1 mm over its edge on both faces. Each also as an STL.
    - `places/base` and `places/plate`: a plate 20 x 20 x 3 seated on a base, between two upright
      arms 0.3 mm off its sides, each with a barb over the plate's top 0.3 mm above it.
    """
    Path("places").mkdir(exist_ok=True)

    def rounded(w: float, h: float, r: float, z0: float, z1: float) -> Any:
        return box(-w / 2, w / 2, -h / 2, h / 2, z0, z1).edges("|Z").fillet(r)

    pane = rounded(20, 14, 3, 0, 1.2)
    frame = (
        box(-14, 14, -11, 11, -1.15, 2.35)
        .cut(rounded(20.5, 14.5, 3.25, -0.15, 1.35))
        .cut(rounded(18, 12, 2, -2, 3))
    )
    for shape, name in ((pane, "places/pane"), (frame, "places/pocket")):
        save(shape, name)
        shape.val().exportStl(f"{name}.stl", tolerance=0.002, angularTolerance=0.05)
    base = box(-20, 20, -10, 10, 0, 2)
    for side in (1, -1):
        x0, x1 = sorted((side * 10.3, side * 11.8))
        b0, b1 = sorted((side * 9.5, side * 10.3))
        base = base.union(box(x0, x1, -3, 3, 0, 8)).union(box(b0, b1, -3, 3, 5.3, 6.5))
    save(base, "places/base")
    save(box(-10, 10, -10, 10, 2, 5), "places/plate")


def mirrored(side: int, a: float, b: float) -> tuple[float, float]:
    """a to b along x, on the side named: +1 as given, -1 mirrored, low end first."""
    return min(side * a, side * b), max(side * a, side * b)


def holds() -> None:
    """What holds a bought part, slid along its way out (mqucifer/print-shop#76), on parts of our
    own: a fan block 30 x 30 x 7.5 under a plate, pushed up into it, so its way out is down.

    - `hold/fan`: the block, x and y from -15 to 15, z from 0 to 7.5.
    - `hold/plain`: a plate 40 x 40 x 2 on the block's top, z 7.5 to 9.5, and nothing else.
    - `hold/hooks`: the plate, with an arm down each side 0.3 mm off the block, 6 mm wide, and a
      hook from each reaching 1 mm in under the block's bottom face, its top 0.2 mm below it.
    - `hold/toes`: the plate, with each arm stopping 0.5 mm down and a toe 1 mm into the block
      at its top face: an overlap of 4 x 1 x 6 x 0.5 = 12 mm³, with nothing under the block.
    - `hold/snap-base` and `hold/snap-lid`: a lid held by snaps, its way off up. The base is
      40 x 30 x 15, walls and floor 2 mm, with a ledge 1 mm deep and 8 mm wide inside each end
      wall, z 12 to 13. The lid, 2 mm thick on the rim, has an arm 0.5 mm thick down each end,
      0.2 mm in from its ledge, and a hook reaching 0.8 mm out under it, its top 0.2 mm below the
      ledge. The ledges are inside the lid's footprint, 2 mm in from its edge, so they reach 3 mm
      in from its outline, though each hook catches 0.8 mm.
    """
    Path("hold").mkdir(exist_ok=True)
    save(box(-15, 15, -15, 15, 0, 7.5), "hold/fan")
    plate = box(-20, 20, -20, 20, 7.5, 9.5)
    save(plate, "hold/plain")
    hooks, toes = plate, plate
    for turn in (0, 90, 180, 270):
        arm = box(15.3, 17.3, -3, 3, -1.2, 7.5).union(box(14, 15.3, -3, 3, -1.2, -0.2))
        toe = box(15.3, 17.3, -3, 3, 7, 7.5).union(box(14, 15.3, -3, 3, 7, 7.5))
        hooks = hooks.union(arm.rotate((0, 0, 0), (0, 0, 1), turn))
        toes = toes.union(toe.rotate((0, 0, 0), (0, 0, 1), turn))
    save(hooks, "hold/hooks")
    save(toes, "hold/toes")
    base = box(-20, 20, -15, 15, 0, 15).cut(box(-18, 18, -13, 13, 2, 15))
    lid = box(-20, 20, -15, 15, 15, 17)
    for side in (1, -1):
        base = base.union(box(*mirrored(side, 17, 18), -4, 4, 12, 13))
        lid = lid.union(box(*mirrored(side, 16.3, 16.8), -3.5, 3.5, 10.5, 15))
        lid = lid.union(box(*mirrored(side, 16.8, 17.8), -3.5, 3.5, 10.5, 11.8))
    save(base, "hold/snap-base")
    save(lid, "hold/snap-lid")
    pocket_feet()


def pocket_feet() -> None:
    """A fan held in a pocket by feet 0.1 mm under it (mqucifer/print-shop#84), on parts of our own.

    - `hold/holed-fan`: the fan block, with four 3.4 mm holes 24 mm apart, through: its holes'
      curved faces give its mesh chords.
    - `hold/feet`: a plate 40 x 40 x 2 on the block's top, and walls 2 mm thick round it, their
      inside faces on the block's sides, down to z -1.1. A foot inside each wall, 8 mm wide,
      reaching 0.5 mm in under the block, z -1.1 to -0.1: its top 0.1 mm under the block's
      bottom face. Slid down, the block meets the four feet after 0.1 mm, 16 mm² of them.

    The block's sides on the walls are an overlap of no volume whose faces give the slide's band
    its chords, 0.002 mm on the holes' 320 mm²: the feet take 0.04 mm to pass that, so the step
    and halving read 0.14 where the rays read 0.1, and it was `DISAGREE`.
    """
    fan = box(-15, 15, -15, 15, 0, 7.5)
    for x in (-12, 12):
        for y in (-12, 12):
            fan = fan.cut(cq.Workplane("XY").center(x, y).circle(1.7).extrude(7.5))
    save(fan, "hold/holed-fan")
    feet = box(-20, 20, -20, 20, 7.5, 9.5)
    for turn in (0, 90, 180, 270):
        wall = box(15, 17, -17, 17, -1.1, 7.5).union(box(14.5, 15, -4, 4, -1.1, -0.1))
        feet = feet.union(wall.rotate((0, 0, 0), (0, 0, 1), turn))
    save(feet, "hold/feet")
    blocked_way_in()


def blocked_way_in() -> None:
    """A fan held in a pocket whose way in is blocked (mqucifer/print-shop#100), on parts of our
    own. The fan is `hold/fan`, the block 30 x 30 x 7.5, z 0 to 7.5, and its way out is down.

    - `hold/bump`: a plate 40 x 40 x 2 on the block's top, and walls 2 mm thick round it, their
      inside faces on the block's sides, down to z -3.5. One bump on the +x wall, 8 mm wide,
      reaching 0.5 mm in under the block, z -1.2 to -0.2: its top 0.2 mm under the block's bottom.
    - `hold/bump-walled`: the same, with a wall of the same part across the block's way in: from
      the -x wall 3.75 mm in, the block's whole depth in y, z -3 to -2. Sliding down, the block
      meets the bump after 0.2 mm, then the wall, 3.75 mm into its path against the bump's 0.5.
    - `hold/bump-roomy`: the walls down to z -9.5, and the same wall at z -9 to -8: met after
      8 mm, past the block's 7.5 mm length from the bump at 0.2, so there's room to set the block
      in before it, and it isn't in the way (the operator's review of mqucifer/print-shop#100).
    """
    bump = box(-20, 20, -20, 20, 7.5, 9.5)
    for turn in (0, 90, 180, 270):
        bump = bump.union(box(15, 17, -17, 17, -3.5, 7.5).rotate((0, 0, 0), (0, 0, 1), turn))
    bump = bump.union(box(14.5, 15, -4, 4, -1.2, -0.2))
    save(bump, "hold/bump")
    save(bump.union(box(-15, -11.25, -15, 15, -3, -2)), "hold/bump-walled")
    # The wall past the block's own 7.5 mm length from the bump: room to set it in before it.
    roomy = box(-20, 20, -20, 20, 7.5, 9.5)
    for turn in (0, 90, 180, 270):
        roomy = roomy.union(box(15, 17, -17, 17, -9.5, 7.5).rotate((0, 0, 0), (0, 0, 1), turn))
    roomy = roomy.union(box(14.5, 15, -4, 4, -1.2, -0.2))
    save(roomy.union(box(-15, -11.25, -15, 15, -9, -8)), "hold/bump-roomy")


def separate_bodies() -> None:
    """A hold by a body joined to nothing else in its file (mqucifer/print-shop#122), on parts of
    our own: a block in a channel, pushed down onto its floor, so its way out is up.

    - `bodies/block`: x -5 to 5, y -10 to 10, z 2 to 8, on the channel's floor.
    - `bodies/channel`: a floor 20 x 20 x 2, z 0 to 2, and a wall each side 2 mm thick, x 8 to 10
      and -10 to -8, up to z 12. Nothing over the block.
    - `bodies/ledge`: a bar x 2 to 6, the channel's length in y, z 8.2 to 9.2: over the block,
      0.2 mm above its top, and 2 mm off the +x wall's inside face.
    - `bodies/channel-loose`: the channel and the ledge in one file, not joined: 2 bodies. As a
      STEP of 2 solids, and as an STL of 2 closed bodies in one mesh.
    - `bodies/channel-joined`: the channel with the ledge run out to the wall, x 2 to 8, so it's
      joined: one body. Slid up, the block meets the ledge after 0.2 mm in each.
    - `bodies/channel-seam.stl`: the 2 bodies of `channel-loose` as one mesh, one of the two
      triangles of the ledge's top face left out: 3 edges with one face each, so trimesh's `split`
      fills the hole, which takes networkx. `print-shop/bench:0.12` had none, and the slide gave
      no answer.
    - `bodies/channel-tee.stl`: the same, the ledge's top face meshed with a T-junction instead: a
      vertex at the middle of its -y edge that the side face below doesn't share, as a CAD export
      can leave. Its hole has no area, so nothing fills it, and the ledge isn't a closed body.
    """
    Path("bodies").mkdir(exist_ok=True)
    save(box(-5, 5, -10, 10, 2, 8), "bodies/block")
    channel = box(-10, 10, -10, 10, 0, 2)
    for side in (1, -1):
        channel = channel.union(box(*mirrored(side, 8, 10), -10, 10, 0, 12))
    ledge = box(2, 6, -10, 10, 8.2, 9.2)
    save(channel, "bodies/channel")
    save(ledge, "bodies/ledge")
    loose = cq.Workplane(obj=cq.Compound.makeCompound([channel.val(), ledge.val()]))
    save(loose, "bodies/channel-loose")
    loose.val().exportStl("bodies/channel-loose.stl", tolerance=0.002)
    save(channel.union(box(2, 8, -10, 10, 8.2, 9.2)), "bodies/channel-joined")
    channel.val().exportStl("bodies/channel-only.stl", tolerance=0.002)
    walls: Any = trimesh.load("bodies/channel-only.stl", force="mesh")
    Path("bodies/channel-only.stl").unlink()
    bar = trimesh.creation.box(bounds=[(2, -10, 8.2), (6, 10, 9.2)])
    up = np.flatnonzero(np.asarray(bar.face_normals)[:, 2] > 0.5)
    # One of the two triangles of the ledge's top face left out: a hole trimesh fills.
    kept = np.delete(np.arange(len(bar.faces)), up[0])
    stl(walls, [trimesh.Trimesh(bar.vertices, bar.faces[kept])], "bodies/channel-seam.stl")
    corners = [(2, -10, 9.2), (6, -10, 9.2), (6, 10, 9.2), (2, 10, 9.2)]
    middle = (4, -10, 9.2)
    # The top face as a fan from the middle of its -y edge, counter-clockwise seen from above.
    fan = [(middle, corners[1], corners[2]), (middle, corners[2], corners[3]),
           (middle, corners[3], corners[0])]  # fmt: skip
    top = trimesh.Trimesh(np.asarray(fan, dtype=float).reshape(-1, 3), np.arange(9).reshape(3, 3))
    sides = trimesh.Trimesh(bar.vertices, np.delete(bar.faces, up, axis=0))
    stl(walls, [sides, top], "bodies/channel-tee.stl")


def ring_ledge() -> None:
    """A slide whose first contact is a tie all round a ring (mqucifer/print-shop#135), on parts of
    our own: a sloped ledge inside a channel, catching a strip slid up from below.

    - `ring/channel`: a block 20 x 20, z 0 to 10, with a bore 12 mm square up to z 4, a ledge
      sloping in at 45 degrees from there to a 10 mm square opening at z 5, and that opening on up
      through the top. All its faces are flat, so its mesh is exact.
    - `ring/strip`: a strip 11.6 mm square, z -2 to 3.4, in the bore. Its top edge meets the
      slope where it is 11.6 mm across, at z 4.2, all round at once: slid up, it's caught after
      0.8 mm, and every place on that ring is as first as another.
    - `ring/strip-at-hold`: the same strip, z -2 to 3.2: caught after 1.0 mm, at `--hold 1`.
    """
    Path("ring").mkdir(exist_ok=True)
    channel = box(-10, 10, -10, 10, 0, 10).cut(box(-6, 6, -6, 6, -1, 4))
    channel = channel.cut(box(-5, 5, -5, 5, 4, 11))

    def square(half: float, z: float) -> Any:
        corners = [(-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)]
        return cq.Wire.makePolygon(corners, close=True)

    slope = cq.Solid.makeLoft([square(6, 4), square(5, 5)], True)
    save(channel.cut(cq.Workplane(obj=slope)), "ring/channel")
    save(box(-5.8, 5.8, -5.8, 5.8, -2, 3.4), "ring/strip")
    save(box(-5.8, 5.8, -5.8, 5.8, -2, 3.2), "ring/strip-at-hold")


def catch_round() -> None:
    """A catch over a thin band's edge all round a closed outline, whose underside slopes
    (mqucifer/print-shop#143), on parts of our own. In an eval the two methods read such a hold
    0.026 mm apart, at different first spots, and it was `DISAGREE`.

    - `catch/strip.stl`: a band 1 mm thick round a 30 x 20 mm outline with 4 mm corners, z 0 to 5.
    - `catch/channel`: a block with a groove the band fills, its sides on both walls and its foot
      on the floor, so the pair touches where it lies. From z 5.2, 0.2 mm over the band's top, the
      outer wall slopes in at 45 degrees for 0.25 mm: the catch, whose overlap with the band
      slid up grows as a square of the distance. Six gaps 0.3 mm wide cut it into three long
      stretches and three teeth 0.03 mm long, each tooth between two of the points the rays go
      from along the band's edge, 0.05 mm apart: no ray first meets one.
    """
    Path("catch").mkdir(exist_ok=True)

    def column(w: float, h: float, r: float, z0: float, z1: float, taper: float = 0.0) -> Any:
        sketch = cq.Workplane("XY").workplane(offset=z0).sketch().rect(w, h).vertices().fillet(r)
        return sketch.finalize().extrude(z1 - z0, taper=taper)

    island = column(28, 18, 3, -1, 8)
    strip = column(30, 20, 4, 0, 5).cut(island)
    cq.exporters.export(strip, "catch/strip.stl", tolerance=0.002, angularTolerance=0.05)
    way = column(30, 20, 4, 0, 5.2).union(column(30, 20, 4, 5.2, 5.45, taper=45))
    way = way.union(column(29.5, 19.5, 3.75, 5.45, 8))
    gaps: list[tuple[float, float, float, float]] = [
        (14, 15, 0.04, 0.34),
        (14, 15, -0.29, 0.01),
        (-15, -14, 0.04, 0.34),
        (-15, -14, -0.29, 0.01),
        (0.04, 0.34, 9, 10),
        (-0.29, 0.01, 9, 10),
    ]
    for x0, x1, y0, y1 in gaps:
        way = way.union(box(x0, x1, y0, y1, 5.2, 8).intersect(column(30, 20, 4, 5.2, 8)))
    save(box(-20, 20, -15, 15, -2, 7).cut(way.cut(island)), "catch/channel")


def given_stls() -> None:
    """A plain STL as one is given (mqucifer/print-shop#144), on parts of our own. In an eval a
    channel letter, watertight and one body, read `DISAGREE` on `info`: its floor's top lay between
    two of the sections stacked for its second volume.

    - `given/tray.stl`: a tray 60 x 40 mm with 10 mm corners, 25 mm tall, open at the top, its
      floor 2 mm and its curved walls 1.6 mm: 11171.55 mm³ by arithmetic on the exact shape. Its
      floor's top, z 2, is 0.047 mm into one of 128 even steps of its height.
    - `given/crossing.stl`: two 10 mm cubes in one mesh, one moved (5, 0, 3) into the other. Each
      is closed, so the mesh is watertight, and its own arithmetic counts their shared 350 mm³
      twice: 2000 mm³, where the material is 1650.
    """
    Path("given").mkdir(exist_ok=True)

    def column(w: float, h: float, r: float, z0: float, z1: float) -> Any:
        sketch = cq.Workplane("XY").workplane(offset=z0).sketch().rect(w, h).vertices().fillet(r)
        return sketch.finalize().extrude(z1 - z0)

    tray = column(60, 40, 10, 0, 25).cut(column(56.8, 36.8, 8.4, 2, 26))
    tray.val().exportStl("given/tray.stl", tolerance=0.002, angularTolerance=0.05)
    one = trimesh.creation.box(extents=(10, 10, 10))
    other = trimesh.creation.box(extents=(10, 10, 10))
    other.apply_translation((5, 0, 3))
    vertices = np.vstack([one.vertices, other.vertices])
    faces = np.vstack([one.faces, other.faces + len(one.vertices)])
    trimesh.Trimesh(vertices, faces, process=False).export("given/crossing.stl")


def ledges() -> None:
    """A part that prints with no supports, and a ledge inside it (mqucifer/print-shop#147), on
    parts of our own. In an eval a catch was built with a flat underside in a channel the request
    said prints with no supports, and nothing read it.

    - `ledge/tray-flat`: a tray 40 x 40 x 20 mm, walls and floor 2 mm, with a ledge 3 mm in from
      its walls all round, from z 16 to its rim: its underside is flat, 36 x 36 less 30 x 30,
      396 mm² of ceiling.
    - `ledge/tray-sloped`: the same, the ledge's underside sloped at 45 degrees from the wall
      at z 13 to its edge at z 16.
    """
    Path("ledge").mkdir(exist_ok=True)
    outside = box(-20, 20, -20, 20, 0, 20)
    opening = box(-15, 15, -15, 15, 16, 21)
    save(outside.cut(box(-18, 18, -18, 18, 2, 16)).cut(opening), "ledge/tray-flat")

    def square(half: float, z: float) -> Any:
        corners = [(-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)]
        return cq.Wire.makePolygon(corners, close=True)

    slope = cq.Solid.makeLoft([square(18, 13), square(15, 16)], True)
    sloped = outside.cut(box(-18, 18, -18, 18, 2, 13)).cut(cq.Workplane(obj=slope)).cut(opening)
    save(sloped, "ledge/tray-sloped")


def hollow_floor() -> None:
    """A screw hole through a thin floor, under a hollow (mqucifer/print-shop#136), on a part of
    our own: `wall/hollow-floor`, a box 20 x 20, z 0 to 12, hollowed from z 2 up and open at the
    top, its side walls 1.6 mm thick, with a 3 mm hole through its 2 mm floor at (5, 0). The
    floor round the hole is 3.5 mm to the outside, by arithmetic; the hole opens into the hollow
    at z 2, and the hollow's walls are 1.6 mm."""
    Path("wall").mkdir(exist_ok=True)
    part = box(-10, 10, -10, 10, 0, 12).cut(box(-8.4, 8.4, -8.4, 8.4, 2, 12))
    save(part.cut(cq.Workplane("XY").center(5, 0).circle(1.5).extrude(2)), "wall/hollow-floor")


def stl(first: Any, rest: list[Any], name: str) -> None:
    """Meshes as one STL, merged as the bench reads one: a T-junction's middle stays apart."""
    meshes = [first, *rest]
    starts = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
    vertices = np.vstack([m.vertices for m in meshes])
    faces = np.vstack([m.faces + s for m, s in zip(meshes, starts, strict=True)])
    trimesh.Trimesh(vertices, faces, process=True).export(name)


def strips() -> None:
    """Strips told from narrow flat faces (mqucifer/print-shop#85), on parts of our own.

    - `strips/circle48`: a circle radius 5 drawn as a polygon of 48 points through `polyline`,
      extruded 3 mm: 48 strips, each 2 x 5 x sin(pi / 48) = 0.654 mm wide, turning 7.5° to the
      next, in one run of 48.
    - `strips/beams`: a lid 40 x 40 x 3, its upright edges rounded 3 mm and its top 1 mm, with
      one round opening radius 12 and four clip beams. Each beam is 0.6 mm thick and 8 mm wide in
      a slot 1.6 mm wide, joined at the slot's ends, hanging 6 mm under the lid with a foot
      reaching 0.9 mm in, its edges along the beam rounded 0.2 mm. It has narrow flat faces, the
      beams' and feet's sides and ends, and no polygon: no strips.
    """
    Path("strips").mkdir(exist_ok=True)
    angles = np.linspace(0, 2 * np.pi, 48, endpoint=False)
    circle = [(5 * float(np.cos(a)), 5 * float(np.sin(a))) for a in angles]
    save(cq.Workplane("XY").polyline(circle).close().extrude(3), "strips/circle48")
    lid = box(-20, 20, -20, 20, 0, 3).edges("|Z").fillet(3).faces(">Z").edges().fillet(1)
    lid = lid.cut(cq.Workplane("XY").circle(12).extrude(3))
    for turn in (0, 90, 180, 270):
        slot = box(15, 16.6, -4, 4, 0, 3).rotate((0, 0, 0), (0, 0, 1), turn)
        beam = box(15.5, 16.1, -4, 4, -6, 3).union(box(14.6, 15.5, -4, 4, -6, -5.4))
        beam = beam.edges("|Y").fillet(0.2).rotate((0, 0, 0), (0, 0, 1), turn)
        lid = lid.cut(slot).union(beam)
    save(lid, "strips/beams")


def at_limits() -> None:
    """A figure exactly at its limit (mqucifer/print-shop#72): a wall 0.4 mm wide, a nozzle's."""
    base = box(0, 20, 0, 20, 0, 2)
    save(base.union(box(5, 15, 9.8, 10.2, 2, 8)), "wall-0p4")


def where() -> None:
    """Replies that say where (mqucifer/print-shop#77). A fan block 30 x 30 x 7.5 with four
    3.4 mm holes 24 mm apart, through. A lid over it in 5 bodies: a plate 2 mm above the fan and
    4 legs touching its underside, not joined to it, 1 mm off the fan's sides. The +x leg has a
    toe reaching 0.5 mm into the fan: 0.5 x 2 x 1 = 1 mm³, from (14.5, -1, 1) to (15, 1, 2)."""
    Path("where").mkdir(exist_ok=True)
    fan = box(-15, 15, -15, 15, 0, 7.5)
    for x in (-12, 12):
        for y in (-12, 12):
            fan = fan.cut(cq.Workplane("XY").center(x, y).circle(1.7).extrude(7.5))
    save(fan, "where/fan")
    legs = [
        box(16, 18, -1, 1, 1, 9.5).union(box(14.5, 16, -1, 1, 1, 2)),
        box(-18, -16, -1, 1, 1, 9.5),
        box(-1, 1, 16, 18, 1, 9.5),
        box(-1, 1, -18, -16, 1, 9.5),
    ]
    parts = [box(-20, 20, -20, 20, 9.5, 11.5).val(), *(leg.val() for leg in legs)]
    save(cq.Workplane(obj=cq.Compound.makeCompound(parts)), "where/lid5")
    # A plate 0.1 mm thick tilted 5° over a pane: sections a layer apart cut it into strips with
    # gaps between, and rays straight up meet it everywhere, so `light`'s two methods differ.
    plate = box(-1, 11, -1, 11, 0, 0.1).val().rotate((5, 5, 0), (5, 6, 0), 5)
    save(cq.Workplane(obj=plate.translate((0, 0, 2))), "where/light-slats")
    save(box(0, 10, 0, 10, 0, 1), "where/light-pane")


def sections() -> None:
    """A section's pieces and least widths (mqucifer/print-shop#101), on parts of our own.

    - `slice/open-box`: 50 x 35 x 20 outside, its floor and walls 2 mm: at mid-height its walls
      are one ring, 2 mm thick.
    - `slice/tabs-free`: a wall 50 x 35 x 20, 2 mm thick on a 2 mm floor, with a tab inside each
      side, 8 mm wide and 1.2 mm thick, standing from the floor to the top with a hook at its top
      reaching 1 mm in, z 18 to 20. Each is cut free of the wall, 0.5 mm off its inside face, so at
      a level between root and hook the section is 5 pieces: the wall's ring and four tabs.
    - `slice/tabs-fused`: the same tabs against the wall's inside face, fused to it for their whole
      height: one piece.
    """
    Path("slice").mkdir(exist_ok=True)
    ring = box(-25, 25, -17.5, 17.5, 0, 20).cut(box(-23, 23, -15.5, 15.5, 2, 20))
    save(ring, "slice/open-box")
    for name, gap in (("tabs-free", 0.5), ("tabs-fused", 0.0)):
        part = ring
        for side in (1, -1):
            # Each tab's face toward the wall, `gap` off the wall's inside face, and its hook.
            x_out, y_out = 23 - gap, 15.5 - gap
            part = part.union(box(*mirrored(side, x_out - 1.2, x_out), -4, 4, 2, 20))
            part = part.union(box(*mirrored(side, x_out - 2.2, x_out - 1.2), -4, 4, 18, 20))
            y0, y1 = mirrored(side, y_out - 1.2, y_out)
            part = part.union(box(-4, 4, y0, y1, 2, 20))
            y0, y1 = mirrored(side, y_out - 2.2, y_out - 1.2)
            part = part.union(box(-4, 4, y0, y1, 18, 20))
        save(part, f"slice/{name}")


def beds() -> None:
    """Parts for `fit-bed` (mqucifer/print-shop#156), against a 256 mm bed: a beam that fits as
    it's drawn, one that fits only corner to corner, one too long any way round, a disc whose
    curved edge the mesh's chords cut, one 0.2 mm too wide, and an assembly whose rail is said
    to be printed standing on its end."""
    import json

    Path("bed").mkdir(exist_ok=True)
    save(box(0, 120, 0, 20, 0, 10), "bed/beam")
    save(box(0, 300, 0, 30, 0, 20), "bed/diagonal")
    save(box(0, 400, 0, 30, 0, 20), "bed/long")
    save(box(0, 30, 0, 20, 0, 300), "bed/rail")
    save(cq.Workplane("XY").circle(128.0).extrude(5), "bed/disc")
    save(cq.Workplane("XY").circle(128.1).extrude(5), "bed/disc-wide")
    save(cq.Workplane("XY").sphere(20), "bed/ball")
    parts = [
        {"name": "beam", "file": "beam.step", "layers": "z"},
        {"name": "rail", "file": "rail.step", "layers": "z"},
    ]
    Path("bed/standing.json").write_text(json.dumps({"parts": parts}))
    parts[1]["layers"] = "y"
    Path("bed/lying.json").write_text(json.dumps({"parts": parts}))
    parts.append({"name": "disc", "file": "disc.step", "layers": "x"})
    Path("bed/no-side.json").write_text(json.dumps({"parts": parts}))
    nut_pockets()
    nut_rows()


def nut_pockets() -> None:
    """A nut's pocket for `fit-bed` (mqucifer/print-shop#168): two plates 40 x 40, the lower
    8 mm thick and the upper 12, a 5.5 mm hole through both at their middle for one M5
    through-bolt, and in the upper's top face a hex pocket 4 mm deep for the nut, which is
    8 mm across its flats and at least 8.79 across its corners (ISO 4032): 8.4 mm across, a
    play of 0.2 a side; 7.9, which the nut doesn't go into; 9.0, which it turns in; a slot the
    same 8.4 mm wide run out to the plate's edge; and a round pocket, with no flats at all.
    The upper plate is also turned on its side in the assembly, to be read where it's put."""
    import json

    def holed(thick: float) -> Any:
        return box(0, 40, 0, 40, 0, thick).cut(
            cq.Workplane("XY").center(20, 20).circle(2.75).extrude(thick)
        )

    def cut(tool: Any) -> Any:
        return holed(12).cut(tool.translate((20, 20, 8)))

    def hexagon(flats: float) -> Any:
        return cq.Workplane("XY").polygon(6, flats, circumscribed=True).extrude(4)

    save(holed(8), "bed/plate")
    save(cut(hexagon(8.4)), "bed/pocket")
    save(cut(hexagon(7.9)), "bed/pocket-tight")
    save(cut(hexagon(9.0)), "bed/pocket-loose")
    # From 4.2 mm short of the bolt's axis out past the plate's edge at x 40.
    slot = cq.Workplane("XY").rect(30, 8.4).extrude(4).translate((10.8, 0, 0))
    save(cut(slot), "bed/pocket-slot")
    save(cut(cq.Workplane("XY").circle(4.5).extrude(4)), "bed/pocket-round")
    # The nut's corners (mqucifer/print-shop#183): a square 8.2 mm across holds its flats with
    # 0.1 mm of play, and its least 8.79 mm across its corners is at least 8.79 x cos 15° =
    # 8.49 wide along one side or the other in any turn, so it doesn't go in. An octagon 8.75
    # mm across its flats takes it turned 7.5° off a flat, where it reaches 8.79 / 2 x cos 7.5°
    # = 4.357 mm toward each wall, and it can't turn, at under 8.79.
    save(cut(cq.Workplane("XY").rect(8.2, 8.2).extrude(4)), "bed/pocket-square")
    save(cut(cq.Workplane("XY").polygon(8, 8.75, circumscribed=True).extrude(4)),
         "bed/pocket-octagon")  # fmt: skip
    joint = {"between": ["lower", "upper"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
             "washer": "m5-washer", "at": [[20, 20, 8]], "pocket": 0.2}  # fmt: skip
    plays = {"pocket-square": 0.1, "pocket-octagon": 0.375}
    for name in ("pocket", "pocket-tight", "pocket-loose", "pocket-slot", "pocket-round",
                 "pocket-square", "pocket-octagon"):  # fmt: skip
        parts = [
            {"name": "lower", "file": "plate.step", "layers": "z"},
            {"name": "upper", "file": f"{name}.step", "at": [0, 0, 8], "layers": "z"},
        ]
        stated = {**joint, "pocket": plays.get(name, 0.2)}
        Path(f"bed/{name}.json").write_text(json.dumps({"parts": parts, "joints": [stated]}))
    # The same pair stood on its side: turned a quarter about x, so the bolt runs along y.
    parts = [
        {"name": "lower", "file": "plate.step", "turn": [["x", 90]], "layers": "z"},
        {"name": "upper", "file": "pocket.step", "turn": [["x", 90]], "at": [0, -8, 0],
         "layers": "z"},
    ]  # fmt: skip
    turned = {**joint, "at": [[20, -8, 20]], "pocket": 0.3}
    Path("bed/pocket-turned.json").write_text(json.dumps({"parts": parts, "joints": [turned]}))


def nut_rows() -> None:
    """Three nut slots in a row on one level (mqucifer/print-shop#204): a plate 200 x 40 x 12
    on one 8 thick, an M5 through-bolt every 70 mm along x, each 0.5 mm further along y than
    the one before, and round each a closed slot 16 mm long and 8.3 wide, 4 deep: 0.15 mm a
    side over the nut. Read as planes without end, another slot's wall stands 3.65 or 3.15 mm
    from a bolt's axis. `row-narrow` has its middle slot 7.9 wide, which the nut doesn't go
    into."""
    import json

    places = [(30.0, 19.5), (100.0, 20.0), (170.0, 20.5)]

    def plate(thick: float) -> Any:
        part = box(0, 200, 0, 40, 0, thick)
        for x, y in places:
            part = part.cut(cq.Workplane("XY").center(x, y).circle(2.75).extrude(thick))
        return part

    save(plate(8), "bed/row-plate")
    for name, middle in (("row", 8.3), ("row-narrow", 7.9)):
        part = plate(12)
        for (x, y), wide in zip(places, (8.3, middle, 8.3), strict=True):
            part = part.cut(box(x - 8, x + 8, y - wide / 2, y + wide / 2, 8, 12))
        save(part, f"bed/{name}")
        parts = [
            {"name": "lower", "file": "row-plate.step", "layers": "z"},
            {"name": "upper", "file": f"{name}.step", "at": [0, 0, 8], "layers": "z"},
        ]
        joint = {"between": ["lower", "upper"], "kind": "through", "bolt": "m5-8.8",
                 "nut": "m5-nut", "washer": "m5-washer", "pocket": 0.15,
                 "at": [[x, y, 8] for x, y in places]}  # fmt: skip
        Path(f"bed/{name}.json").write_text(json.dumps({"parts": parts, "joints": [joint]}))


if __name__ == "__main__":
    beds()
    bores()
    unreadable()
    where()
    sections()
    near_touching()
    boxes()
    pin_in_hole()
    pockets()
    volumes()
    frames()
    captured()
    islands()
    plays()
    rings()
    hanging()
    closes()
    thin_parts()
    rim_points()
    designs()
    sheets()
    glass()
    enclosure()
    places()
    holds()
    separate_bodies()
    ring_ledge()
    catch_round()
    given_stls()
    ledges()
    hollow_floor()
    strips()
    at_limits()
    print("made")
