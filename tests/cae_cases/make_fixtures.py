"""Planted parts for the bench's second image, made from scratch in FreeCAD, with right answers
from arithmetic (mqucifer/print-shop#142). Nothing here comes from anyone's design.

`snap/`: an open box 60 x 40 x 30 with 2 mm walls, and inside its front wall one snap clip: an arm
8 mm wide and 1.2 mm thick, free from its root at z 10 to its top at z 26, 1 mm clear of the
wall behind it, with a hook 2 mm tall standing 0.8 mm out from its face at the top. The lid's
skirt comes down inside the walls, 0.2 mm clear of the arm's face, with a window the hook snaps
into: the band of skirt under that window has to pass over the hook, 0.8 - 0.2 = 0.6 mm deep and
8 mm along, from 4 mm out down to 0.4 mm out.

`load/` (mqucifer/print-shop#156): a beam 120 x 20 x 10 along x, whole and as two halves that
meet at x 60; a block 20 x 20 x 20 on a slab 60 x 60 x 10; and a small table, a top 100 x 100 x 6
on four legs 10 x 10 x 60, with an assembly file for each way they're joined, held and printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

#: The clip, as the tests' arithmetic reads it.
ARM_T, ARM_W, ROOT_Z, TOP_Z = 1.2, 8.0, 10.0, 26.0
HOOK_OUT, HOOK_TALL, PLAY, ROOM = 0.8, 2.0, 0.2, 1.0
WALL_Y = -18.0


def box(low: tuple[float, float, float], high: tuple[float, float, float]) -> Part.Shape:
    size = [b - a for a, b in zip(low, high, strict=True)]
    return Part.makeBox(*size, App.Vector(*low))


def clip(back: float, root_z: float = ROOT_Z, ramp: bool = False) -> Part.Shape:
    """The arm, its hook and the block that joins its root to the wall. `back` is the y of the
    arm's back face. A ramped hook is a wedge: as far out at its foot, and sloping back to the
    arm's face at its top, so it has one crest and no flat face."""
    face = back + ARM_T
    arm = box((-ARM_W / 2, back, root_z), (ARM_W / 2, face, TOP_Z))
    hook = box((-ARM_W / 2, face, TOP_Z - HOOK_TALL), (ARM_W / 2, face + HOOK_OUT, TOP_Z))
    if ramp:
        side = [
            App.Vector(-ARM_W / 2, face, TOP_Z - HOOK_TALL),
            App.Vector(-ARM_W / 2, face + HOOK_OUT, TOP_Z - HOOK_TALL),
            App.Vector(-ARM_W / 2, face, TOP_Z),
        ]
        hook = Part.Face(Part.makePolygon([*side, side[0]])).extrude(App.Vector(ARM_W, 0, 0))
    foot = box((-ARM_W / 2, WALL_Y, root_z - 2), (ARM_W / 2, face, root_z))
    return arm.fuse(hook).fuse(foot)


def base(
    back: float,
    wall_y: float = WALL_Y,
    ramp: bool = False,
    rim: float = 30.0,
    root_z: float = ROOT_Z,
) -> Part.Shape:
    """The open box with its clip. `wall_y` thickens the front wall toward the clip, `rim` is
    the height of the walls' top, and `root_z` where the clip's arm starts."""
    shell = box((-30, -20, 0), (30, 20, rim)).cut(box((-28, wall_y, 2), (28, 18, rim + 0.1)))
    return shell.fuse(clip(back, root_z, ramp=ramp)).removeSplitter()


#: The channel and its strip (mqucifer/print-shop#150): the strip is 10 mm wide and 1.5 thick,
#: 0.2 mm clear of each wall where it lies on the floor, and a catch on one wall's top stands
#: 0.5 mm in over the channel, so the strip passes 0.3 mm through it on its way down.
STRIP_W, STRIP_T, CHANNEL_PLAY, CATCH_IN, CHANNEL_LONG = 10.0, 1.5, 0.2, 0.5, 40.0


def channel() -> Part.Shape:
    """A printed channel, open at both ends: a floor 2 mm thick, two walls 2 mm thick and 3 mm
    tall, and on the +x wall's top a catch 1 mm tall along its whole length."""
    inner = STRIP_W / 2 + CHANNEL_PLAY
    outer = inner + 2
    half = CHANNEL_LONG / 2
    room = box((-inner, -half - 1, 2), (inner, half + 1, 6))
    shape = box((-outer, -half, 0), (outer, half, 5)).cut(room)
    catch = box((inner - CATCH_IN, -half, 4), (inner, half, 5))
    return shape.fuse(catch).removeSplitter()


def strip() -> Part.Shape:
    """The given strip where it sits, on the channel's floor."""
    half = CHANNEL_LONG / 2
    return box((-STRIP_W / 2, -half, 2), (STRIP_W / 2, half, 2 + STRIP_T))


def lid() -> Part.Shape:
    """A plate on the box's rim and a skirt inside its walls, its front face 0.2 mm clear of the
    arm's face, with a window for the hook."""
    front = WALL_Y + ROOM + ARM_T + PLAY
    plate = box((-30, -20, 30), (30, 20, 32))
    skirt = box((-27.8, front, 22), (27.8, 17.8, 30)).cut(
        box((-26.3, front + 1.5, 21.9), (26.3, 16.3, 30))
    )
    window = box((-ARM_W / 2 - 0.5, front - 0.1, 23.6), (ARM_W / 2 + 0.5, front + 1.6, 26.4))
    return plate.fuse(skirt.cut(window)).removeSplitter()


def save(shape: Part.Shape, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    assert shape.isValid() and len(shape.Solids) == 1, path
    shape.exportStep(str(path))


#: The beam (mqucifer/print-shop#156), as the tests' arithmetic reads it.
BEAM_L, BEAM_W, BEAM_T = 120.0, 20.0, 10.0
BOLT = {"kind": "bolted", "bolt": "m4-8.8", "insert": "m4-heat-set"}


Rows = list[dict[str, Any]]


def assembly(path: Path, parts: Rows, joints: Rows | None = None, held: Rows | None = None) -> None:
    path.write_text(json.dumps({"parts": parts, "joints": joints or [], "held": held or []}))


def loads() -> None:
    """The parts `load` and `fit-bed` read, and an assembly file for each case."""
    out = Path("load")
    half = BEAM_L / 2
    save(box((0, 0, 0), (BEAM_L, BEAM_W, BEAM_T)), out / "beam.step")
    save(box((0, 0, 0), (half, BEAM_W, BEAM_T)), out / "half.step")
    save(box((0, 0, 0), (60, 60, 10)), out / "slab.step")
    save(box((0, 0, 0), (20, 20, 20)), out / "block.step")
    save(box((0, 0, 0), (100, 100, 6)), out / "top.step")
    save(box((0, 0, 0), (10, 10, 60)), out / "leg.step")
    # Too long for a 256 mm bed any way round, and one that fits only corner to corner.
    save(box((0, 0, 0), (400, 30, 20)), out / "long.step")
    save(box((0, 0, 0), (300, 30, 20)), out / "diagonal.step")
    wall = [{"plane": "x=0", "how": "fixed"}]

    def beam(layers: str) -> Rows:
        return [{"name": "beam", "file": "beam.step", "layers": layers}]

    def halves(second_at: float = half) -> Rows:
        return [
            {"name": "root", "file": "half.step", "layers": "z"},
            {"name": "tip", "file": "half.step", "at": [second_at, 0, 0], "layers": "z"},
        ]

    middle = [[half, BEAM_W / 2, BEAM_T / 2]]
    bolted: Rows = [{"between": ["root", "tip"], **BOLT, "at": middle}]
    assembly(out / "one.json", beam("z"), held=wall)
    # The same beam printed standing on its end: its layers are stacked along its length.
    assembly(out / "across.json", beam("x"), held=wall)
    assembly(out / "two.json", halves(), bolted, wall)
    bonded: Rows = [{"between": ["root", "tip"], "kind": "bonded"}]
    assembly(out / "two-bonded.json", halves(), bonded, wall)
    weak: Rows = [{**bolted[0], "pull_n": 60, "source": "a figure made up for this test"}]
    assembly(out / "two-weak.json", halves(), weak, wall)
    assembly(out / "unnamed.json", halves(), held=wall)
    assembly(out / "gap.json", halves(half + 0.5), bolted, wall)
    assembly(out / "through.json", halves(half - 1), bolted, wall)
    rest: Rows = [
        {"name": "slab", "file": "slab.step", "layers": "z"},
        {"name": "block", "file": "block.step", "at": [20, 20, 10], "layers": "z"},
    ]
    touching: Rows = [{"between": ["slab", "block"], "kind": "touching"}]
    assembly(out / "rest.json", rest, touching)
    assembly(out / "rest-fixed.json", rest, touching, [{"plane": "z=0", "how": "fixed"}])
    corners = [(0, 0), (90, 0), (0, 90), (90, 90)]
    table: Rows = [{"name": "top", "file": "top.step", "at": [0, 0, 60], "layers": "z"}]
    table += [
        {"name": f"leg{n}", "file": "leg.step", "at": [x, y, 0], "layers": "z"}
        for n, (x, y) in enumerate(corners, 1)
    ]
    joints: Rows = [
        {"between": ["top", f"leg{n}"], **BOLT, "at": [[x + 5, y + 5, 60]]}
        for n, (x, y) in enumerate(corners, 1)
    ]
    assembly(out / "table.json", table, joints)
    # A leg printed lying down, turned upright in the assembly: its layers turn with it.
    lying = [dict(p) for p in table]
    lying[1] = {**lying[1], "layers": "x"}
    assembly(out / "table-lying.json", lying, joints)


def main() -> None:
    loads()
    out = Path("snap")
    seated = WALL_Y + ROOM
    save(base(seated), out / "box.step")
    # The clip 0.8 mm further back, into the room behind it: its hook stops 0.2 mm short of the
    # skirt, so nothing is in the lid's way.
    save(base(seated - 0.8), out / "box-back.step")
    # The wall 0.55 mm thicker behind the clip: 0.45 mm of room, less than the 0.6 mm it bends.
    save(base(seated, WALL_Y + 0.55), out / "box-tight.step")
    # The hook as a wedge: 0.6 mm deep only while the band is level with its foot, from 2 mm out
    # down to 0.4, and shallower further out.
    save(base(seated, ramp=True), out / "box-ramp.step")
    # The walls 0.5 mm taller than the lid's seat: the lid's plate is in the rim where it sits.
    save(base(seated - 0.8, rim=30.5), out / "box-tall.step")
    save(lid(), out / "lid.step")
    # The clip alone on a slab of wall, and one with half the free length.
    slab = box((-10, -20, 0), (10, WALL_Y, 30))
    save(slab.fuse(clip(seated)).removeSplitter(), out / "clip.step")
    save(slab.fuse(clip(seated, 18.0)).removeSplitter(), out / "clip-short.step")
    save(slab.fuse(clip(seated, ramp=True)).removeSplitter(), out / "clip-ramp.step")
    # The clip half as long, from z 18: pushed the same 0.6 mm it takes more than PETG does
    # across its layers (mqucifer/print-shop#150).
    save(base(seated, root_z=18.0), out / "box-short.step")
    save(channel(), out / "channel.step")
    save(strip(), out / "strip.step")
    print("made", sorted(str(p) for p in out.glob("*.step")))


if __name__ == "__main__":
    main()
