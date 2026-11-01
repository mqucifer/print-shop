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


def thin_parts() -> None:
    base = box(0, 20, 0, 20, 0, 2)
    save(base.union(box(5, 15, 9.85, 10.15, 2, 8)), "wall-0p3")
    save(base.union(box(5, 15, 9.6, 10.4, 2, 8)), "wall-0p8")
    # A thin fin low on a tall post: one section at mid-height doesn't cross it.
    save(box(0, 10, 0, 10, 0, 20).union(box(10, 20, 4.85, 5.15, 0, 2)), "fin-low")
    # A floor thinner than a layer.
    floor = box(0, 20, 0, 20, 0, 5).cut(box(2, 18, 2, 18, 0.1, 5))
    save(floor, "floor-0p1")
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


if __name__ == "__main__":
    near_touching()
    boxes()
    pin_in_hole()
    pockets()
    volumes()
    frames()
    thin_parts()
    designs()
    print("made")
