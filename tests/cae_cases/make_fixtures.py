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

`through/` (mqucifer/print-shop#168): a plate 100 x 40 x 8 held at both ends, and a plate
60 x 40 on it, each with a 5.5 mm hole, joined by one M5 through-bolt: pulled straight up off
it, on washers and with the nut in a hex pocket; and as a lap pulled along, the upper plate's
hole 5.5 mm from its end and 20 mm from it.

`dovetail/` (mqucifer/print-shop#170): two bars 30 mm wide that lap over 40 mm, joined by a
tapered dovetail: a tail 6 mm proud of the upper bar's lap face, 6 mm wide at its neck where
it's lowest and 10 at its top, each flank undercut by 2 mm, in a socket in the lower bar that's
open at its top. It slides in downward, so a weight on the upper bar seats it. With the tail
4 mm short of its seat each flank has 0.2 mm of play; with the socket 1 mm deeper than the tail
it's seated on its flanks alone; mirrored top to bottom, the same weight drives it out; and
with a tab hanging from the upper bar, its hook 0.6 mm into a window in the lower, it's latched.
"""

from __future__ import annotations

import json
import math
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
#: What each bolt holds, for the cases that are read through: no insert has a sourced figure.
HOLDS = {"pull_n": 500, "source": "a figure made up for this test"}


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
    unread: Rows = [{"between": ["root", "tip"], **BOLT, "at": middle}]
    bolted: Rows = [{**unread[0], **HOLDS}]
    assembly(out / "two-unread.json", halves(), unread, wall)
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
        {"between": ["top", f"leg{n}"], **BOLT, **HOLDS, "at": [[x + 5, y + 5, 60]]}
        for n, (x, y) in enumerate(corners, 1)
    ]
    assembly(out / "table.json", table, joints)
    # A leg printed lying down, turned upright in the assembly: its layers turn with it.
    lying = [dict(p) for p in table]
    lying[1] = {**lying[1], "layers": "x"}
    assembly(out / "table-lying.json", lying, joints)


#: The through-bolted plates (mqucifer/print-shop#168), as the tests' arithmetic reads them.
PLATE_T, HOLE, POCKET_FLATS, POCKET_DEEP = 8.0, 5.5, 8.4, 4.0
M5 = {"kind": "through", "bolt": "m5-8.8", "nut": "m5-nut", "washer": "m5-washer"}


def plate(length: float, hole_x: float, thick: float = PLATE_T, pocket: bool = False) -> Part.Shape:
    """A plate `length` x 40, with a hole through it at `hole_x` on its centre line and, with
    `pocket`, a hex pocket for the nut in its top face."""
    made = box((0, 0, 0), (length, 40, thick))
    made = made.cut(Part.makeCylinder(HOLE / 2, thick, App.Vector(hole_x, 20, 0)))
    if pocket:
        reach = POCKET_FLATS / 3**0.5
        corners = [
            App.Vector(hole_x + reach * math.cos(k * math.pi / 3),
                       20 + reach * math.sin(k * math.pi / 3), thick - POCKET_DEEP)
            for k in range(7)
        ]  # fmt: skip
        hexagon = Part.Face(Part.makePolygon(corners)).extrude(App.Vector(0, 0, POCKET_DEEP))
        made = made.cut(hexagon)
    return made


def throughs() -> None:
    """Two plates joined by one M5 through-bolt, pulled apart and pulled along."""
    out = Path("through")
    save(plate(100, 50), out / "lower.step")
    save(plate(60, 30), out / "upper.step")
    save(plate(60, 30, 12, pocket=True), out / "upper-pocket.step")
    save(box((0, 0, 0), (60, 40, PLATE_T)), out / "upper-solid.step")
    ends = [{"plane": "x=0", "how": "fixed"}, {"plane": "x=100", "how": "fixed"}]

    def pair(upper: str, at: float, layers: str = "z") -> Rows:
        return [
            {"name": "lower", "file": "lower.step", "layers": "z"},
            {"name": "upper", "file": upper, "at": [at, 0, PLATE_T], "layers": layers},
        ]

    joint: Rows = [{"between": ["lower", "upper"], **M5, "at": [[50, 20, PLATE_T]]}]
    assembly(out / "pull.json", pair("upper.step", 20), joint, ends)
    pocketed: Rows = [{**joint[0], "pocket": 0.2}]
    assembly(out / "pull-pocket.json", pair("upper-pocket.step", 20), pocketed, ends)
    assembly(out / "pull-no-hole.json", pair("upper-solid.step", 20), joint, ends)
    # A lap pulled along: the upper plate's hole 5.5 mm from its end at x 40, where
    # its washer just sits on it, and 20 mm from it.
    wall = [{"plane": "x=0", "how": "fixed"}]
    for name, edge in (("edge", 5.5), ("in", 20.0)):
        save(plate(60, edge), out / f"upper-{name}.step")
        save(plate(100, 40 + edge), out / f"lower-{name}.step")
        parts: Rows = [
            {"name": "lower", "file": f"lower-{name}.step", "layers": "z"},
            {"name": "upper", "file": f"upper-{name}.step", "at": [40, 0, PLATE_T], "layers": "y"},
        ]
        lapped: Rows = [{**joint[0], "at": [[40 + edge, 20, PLATE_T]]}]
        assembly(out / f"lap-{name}.json", parts, lapped, wall)


#: The dovetailed bars (mqucifer/print-shop#170), as the tests' arithmetic reads them. Both are
#: `BAR_W` wide along x and meet on the plane y 0: the socket's bar behind it, the tail's in
#: front, lapped from `LAP[0]` up to `LAP[1]`.
BAR_W, SOCKET_T, TAIL_T, LAP, BAR_TOP = 30.0, 20.0, 12.0, (30.0, 70.0), 110.0
#: The tail: how far it stands off the lap face, half its neck's width at its tip and at its
#: wide end, and how much wider each side is at its far face, the undercut.
TAIL_DEEP, NECK, FLARE = 6.0, (3.0, 5.0), 2.0
#: The latch: a tab `TAB_W` wide and `TAB_T` thick hanging `TAB_L` from the tail's bar, its face
#: `TAB_CLEAR` off the socket's bar, with a hook `HOOK_IN` into a window there, `HOOK_TALL`
#: tall, `LATCH_PLAY` under the window's top.
TAB_W, TAB_T, TAB_L, TAB_CLEAR, HOOK_IN, LATCH_PLAY = 5.0, 1.2, 20.0, 0.4, 0.6, 0.2
#: The arm on the tail's bar that levers the tail off the lap face: how far out, and how tall.
ARM_OUT, ARM_TALL = 60.0, 40.0


def wedge(low: float, high: float, necks: tuple[float, float], grow: float = 0.0) -> Part.Shape:
    """A tail from z `low` to `high`: a trapezoid `TAIL_DEEP` into -y from the lap face, half
    as wide at its neck as `necks` says at each end and `FLARE` more at its far face, every face
    flat. `grow` makes it that much wider a side and deeper, as a socket with play is."""
    rings = []
    for z, neck in zip((low, high), necks, strict=True):
        near, far, deep = neck + grow, neck + FLARE + grow, -(TAIL_DEEP + grow)
        rings.append([App.Vector(x, y, z) for x, y in
                      ((-near, 0), (near, 0), (far, deep), (-far, deep))])  # fmt: skip
    bottom, top = rings
    faces = [bottom[::-1], top]
    faces += [[bottom[k], bottom[(k + 1) % 4], top[(k + 1) % 4], top[k]] for k in range(4)]
    shell = Part.makeShell([Part.Face(Part.makePolygon([*f, f[0]])) for f in faces])
    solid = Part.makeSolid(shell)
    if solid.Volume < 0:
        solid.reverse()
    return solid


def tab() -> Part.Shape:
    """The latch's tab, hanging from the tail's bar, with its hook at its foot."""
    foot = LAP[0] - TAB_L
    arm = box((-TAB_W / 2, TAB_CLEAR, foot), (TAB_W / 2, TAB_CLEAR + TAB_T, LAP[0]))
    hook = box((-TAB_W / 2, -HOOK_IN, foot), (TAB_W / 2, TAB_CLEAR, foot + 2))
    return arm.fuse(hook)


def window() -> Part.Shape:
    """The window the hook snaps into, in the socket's bar: its top is the ledge."""
    foot = LAP[0] - TAB_L
    top = foot + 2 + LATCH_PLAY
    return box((-TAB_W / 2 - 1, -2 * HOOK_IN, foot - 0.5), (TAB_W / 2 + 1, 0, top))


def dovetails() -> None:
    """Two bars joined by a tapered dovetail, each way up, seated, latched and on its way in."""
    out = Path("dovetail")
    half = BAR_W / 2
    low, high = LAP
    socket_bar = box((-half, -SOCKET_T, 0), (half, 0, high))
    tail_bar = box((-half, 0, low), (half, TAIL_T, BAR_TOP))
    tail = tail_bar.fuse(wedge(low, high, NECK)).removeSplitter()
    socket = socket_bar.cut(wedge(low, high, NECK))
    save(tail, out / "tail.step")
    save(socket, out / "socket.step")
    # The socket 1 mm deeper than the tail is long: the tail's tip is clear, and it's seated on
    # its two flanks alone, as a wedge.
    slope = (NECK[1] - NECK[0]) / (high - low)
    deep = socket_bar.cut(wedge(low - 1, high, (NECK[0] - slope, NECK[1])))
    save(deep, out / "socket-deep.step")
    # The tail 4 mm short of its seat: each flank is 4 x (5 - 3) / 40 = 0.2 mm clear, across x.
    out_by = tail.copy()
    out_by.translate(App.Vector(0, 0, 4))
    save(out_by, out / "tail-out.step")
    # Mirrored top to bottom: the tail is widest at its foot, so it goes in upward, through a
    # channel as wide as its foot, and a weight on its bar drives it back out.
    up = NECK[::-1]
    save(tail_bar.fuse(wedge(low, high, up)).removeSplitter(), out / "tail-up.step")
    channel = wedge(0, low, (up[0], up[0]))
    save(socket_bar.cut(wedge(low, high, up)).cut(channel), out / "socket-up.step")
    # Latched: a tab on the tail's bar, and the window its hook snaps into.
    save(tail.fuse(tab()).removeSplitter(), out / "tail-latch.step")
    save(socket.cut(window()), out / "socket-latch.step")

    def pair(tail_file: str, socket_file: str, layers: tuple[str, str] = ("y", "y")) -> Rows:
        return [
            {"name": "upper", "file": tail_file, "layers": layers[0]},
            {"name": "lower", "file": socket_file, "layers": layers[1]},
        ]

    floor = [{"plane": "z=0", "how": "fixed"}]
    down: Rows = [{"between": ["upper", "lower"], "kind": "dovetail", "slides": "-z"}]
    assembly(out / "seat.json", pair("tail.step", "socket.step"), down, floor)
    assembly(out / "wedge.json", pair("tail.step", "socket-deep.step"), down, floor)
    # The file says the tail goes in upward, and its tip's seat faces the other way.
    lied: Rows = [{**down[0], "slides": "+z"}]
    assembly(out / "lied.json", pair("tail.step", "socket.step"), lied, floor)
    assembly(out / "wrong.json", pair("tail-up.step", "socket-up.step"), lied, floor)
    foot = low - TAB_L
    latch = {
        "on": "upper",
        "arm": [[-TAB_W / 2 - 0.1, -HOOK_IN - 0.1, foot - 0.1],
                [TAB_W / 2 + 0.1, TAB_CLEAR + TAB_T + 0.1, low]],
        "root": f"z={low:g}",
    }  # fmt: skip
    latched: Rows = [{**down[0], "latch": latch}]
    assembly(out / "latch.json", pair("tail-latch.step", "socket-latch.step"), latched, floor)
    # The same, the tail's bar printed standing: the tab's length is across its layers.
    standing = pair("tail-latch.step", "socket-latch.step", ("z", "y"))
    assembly(out / "latch-standing.json", standing, latched, floor)
    # An arm 60 mm out from the top of the tail's bar, the lower bar held by its back: a weight
    # at the arm's end seats the tail and levers its top off the lap face, so the tail hangs
    # by its neck there. That's across its layers printed lap face down, and along them
    # printed on its side.
    arm = box((-half, TAIL_T, BAR_TOP - ARM_TALL), (half, TAIL_T + ARM_OUT, BAR_TOP))
    save(tail.fuse(arm).removeSplitter(), out / "tail-arm.step")
    back = [{"plane": f"y={-SOCKET_T:g}", "how": "fixed"}]
    for name, layers in (("across", "y"), ("along", "x")):
        parts = pair("tail-arm.step", "socket.step", (layers, "z"))
        assembly(out / f"neck-{name}.json", parts, down, back)


def main() -> None:
    loads()
    throughs()
    dovetails()
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
