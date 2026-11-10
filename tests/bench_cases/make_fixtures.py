"""The bench's planted cases, written to the current directory, inside the image.

Each is something a tool reported as success when it was wrong (the map's "Never take a tool's
word"), or a plain part whose right answer is known by arithmetic. All of them are made here,
from scratch: no Sponsor's design, or anything from one, is in this repository.
"""

from __future__ import annotations

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


if __name__ == "__main__":
    where()
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
    designs()
    sheets()
    glass()
    enclosure()
    places()
    holds()
    strips()
    at_limits()
    print("made")
