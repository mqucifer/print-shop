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

`peak/` (mqucifer/print-shop#237): two blocks 20 x 30 x 20 end to end along x, an M5 bolt's
5.5 mm bore lying level through both, round or peaked, its flats leaving the round 43° above
level and so rising at 47° to a point; the second block's cut by a nut's slot; and what isn't a
peaked bore: a slot's end, a half-round groove, a D, a peak that rises at 44°, one cut short of
its point, and a D cut in two by the slot, whose two arcs add up past a full turn.

`shear/` (mqucifer/print-shop#245): two blocks end to end along x on one M5 bolt lying level,
the second's bore carrying on past its nut's slot for the bolt's tip; the same with that far
stretch longer; with the first's bore counterbored at the head's end; and one where the shear
rules, the bore 4 mm long to a washer's slot and 5.2 mm above the part's bottom.

`channel/` (mqucifer/print-shop#302): two halves of a panel 20 x 30 x 20 end to end along x on
one M5 bolt in a drop-in channel open up, a U 5.5 mm across running from the head's wall at x 12
to the nut's at x 28, each wall with a recess behind it; the second half 9 mm wide, too narrow
for the washer; and the same with no channel declared.

`dovetail/` (mqucifer/print-shop#170): two bars 30 mm wide that lap over 40 mm, joined by a
tapered dovetail: a tail 6 mm proud of the upper bar's lap face, 6 mm wide at its neck where
it's lowest and 10 at its top, each flank undercut by 2 mm, in a socket in the lower bar that's
open at its top. It slides in downward, so a weight on the upper bar seats it. With the tail
4 mm short of its seat each flank has 0.2 mm of play; with the socket 1 mm deeper than the tail
it's seated on its flanks alone; mirrored top to bottom, the same weight drives it out; and
with a tab hanging from the upper bar, its hook 0.6 mm into a window in the lower, it's latched.
With the socket 0.15 mm clear all round, a slip fit's play, its flanks are clear and its tip
seated.

`play/` (mqucifer/print-shop#188): a block 40 x 40 x 20 on one 40 x 40 x 80, a square tail
20 x 20 under the upper going 20 mm down into a square socket in the lower, its tip on the
socket's floor and the blocks' faces touching round it, the socket's walls 0.15 mm clear of
the tail, a slip fit's play. A slender tab hangs from the upper into a pocket in the lower's
top, its hook in a window in the pocket's wall 0.2 mm under its ledge: near the +x edge, with
seat beside it all round, or at the middle with the window's sides drawn touching the hook's.
And the socket 1.5 mm clear, more play than a tilt closes, and 2.5 mm clear, more than is read.

`turns/` (mqucifer/print-shop#319): a shelf bracket, an L 5 mm thick and 10 wide, its upright
25 tall bolted to a wall 10 thick at x 0 and its arm 30 long along x at the top, its inside
corner rounded 3 mm, printed on its side; and two wall slices of a ring, 50 to 56 mm from its
axis and 80 tall, with a 2° seam between them, and a rib across the seam: a cap 4 thick curved
round the slices' outsides, and a fin under it that fills the seam, touching both. Each is drawn
in place, and again in its print pose and placed by its turns: about x, y or z, and two combined.
And a peg 6 across standing in a blind hole 10 deep in a cube of 20, touching its wall and floor.

`seam/` (mqucifer/print-shop#334): a strip 1.5 thick, 30 long and 40 tall, bonded in a seam
between two blocks and standing 10 proud of their tops, the seam turned 30° about z. The strip
is drawn flat, as printed, and placed by a turn about x and then about z, its `at` written to six
decimals, as an assembly's file gives it, so it sits a hair, 0.26 µm, off the blocks; and drawn
standing and placed by the one turn about z, exactly where it is, and by its `at` to six decimals.

`load/bowed*` (mqucifer/print-shop#356): a leg 20 x 10, straight for 40 mm and then curving
25 mm over 80 on a B-spline tangent to it, bowing at 45° to its layers; a bar 120 x 20 x 10
lofted through 9 sections bowing 25 mm along its layers, its flat sides B-spline as a loft makes
them; and the leg bowing along its layers with a notch 6 x 4 in its flat side.

`load/web*` (mqucifer/print-shop#363): two round bosses 12 across and 20 tall, 40 apart, joined
by a web 1.75 thick, and 2.0 thick.

`load/tile*` (mqucifer/print-shop#363): a plate 60 x 60 x 8 with four counterbored holes, a
pocket in its top and a boss in the pocket against its wall.

`capped/` (mqucifer/print-shop#346): a top 40 x 40 and 6 thick with a counterbore 10 across and
2 deep at its middle, and a cap 2 tall standing on the counterbore's floor with play round it,
turned 15° about its axis so its seam isn't the counterbore's: 0.15 mm of play, a fit's, and
0.2 µm, a hair, which no mesh parts; and with the top's corner at the origin rounded 0.03 mm, a
micro-arc no print has.
"""

from __future__ import annotations

import json
import math
import shutil
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


def level_bore(kind: str) -> Part.Shape:
    """What's cut for a bore along x through (y 15, z 10), by its kind."""
    radius, way = HOLE / 2, App.Vector(1, 0, 0)
    round_ = Part.makeCylinder(radius, 44, App.Vector(-2, 15, 10), way)
    if kind == "round":
        return round_
    if kind == "end":  # A slot standing up: each end half a round, its sides parallel.
        over = Part.makeCylinder(radius, 44, App.Vector(-2, 15, 13), way)
        sides = box((-2, 15 - radius, 10), (42, 15 + radius, 13))
        return round_.fuse(over).fuse(sides).removeSplitter()
    if kind == "d":
        return round_.common(box((-2, 0, 0), (42, 30, 11.5)))
    leaves = math.radians({"peaked": 43.0, "short": 43.0, "flat": 46.0}[kind])
    y, z = radius * math.cos(leaves), 10 + radius * math.sin(leaves)
    point = 10 + radius / math.sin(leaves)
    roof = [App.Vector(-2, *p) for p in ((15 + y, z), (15, point), (15 - y, z), (15 + y, z))]
    made = round_.fuse(Part.Face(Part.makePolygon(roof)).extrude(App.Vector(44, 0, 0)))
    if kind == "short":
        made = made.common(box((-2, 0, 0), (42, 30, 13.5)))
    return made.removeSplitter()


def peaks() -> None:
    """Two blocks on one bolt lying level, and each bore it may be in."""
    out = Path("peak")
    first, second = box((0, 0, 0), (20, 30, 20)), box((20, 0, 0), (40, 30, 20))
    slot = box((30, 10.85, 5.2), (34.9, 19.15, 21))
    for kind in ("round", "peaked"):
        save(first.cut(level_bore(kind)), out / f"one-{kind}.step")
    for kind in ("round", "peaked", "end", "d", "flat", "short"):
        save(second.cut(level_bore(kind)), out / f"two-{kind}.step")
    save(second.cut(level_bore("peaked")).cut(slot), out / "two-slot.step")
    save(second.cut(level_bore("d")).cut(slot), out / "two-split.step")
    # The bore on the part's top: half a round, open to the air.
    save(box((20, 0, 0), (40, 30, 10)).cut(level_bore("round")), out / "two-groove.step")
    # The same peaked bore 4 mm from the part's side, where a washer 10 mm across overhangs it.
    save(box((20, 0, 0), (40, 19, 20)).cut(level_bore("peaked")), out / "two-edge.step")
    # And with a 1 mm groove across its seat, under the peaked bore: a cut that isn't the peak's.
    notch = box((39, 0, 6), (41, 30, 7))
    save(second.cut(level_bore("peaked")).cut(notch), out / "two-notched.step")
    held = [{"plane": "x=40", "how": "fixed"}]
    joint: dict[str, Any] = {"between": ["one", "two"], **M5, "at": [[20, 15, 10]]}
    for name, one, two, more in (
        ("peaked", "peaked", "peaked", {}),
        ("slot", "round", "slot", {"pocket": 0.15}),
        ("split", "round", "split", {"pocket": 0.15}),
        *((kind, "round", kind, {}) for kind in ("end", "groove", "d", "flat", "short")),
        ("edge", "peaked", "edge", {}),
        ("notched", "peaked", "notched", {}),
    ):
        parts: Rows = [
            {"name": "one", "file": f"one-{one}.step", "layers": "z"},
            {"name": "two", "file": f"two-{two}.step", "layers": "z"},
        ]
        assembly(out / f"{name}.json", parts, [{**joint, **more}], held)


def shears() -> None:
    """A bolt's shear, and the bore that carries on past its nut's slot."""
    out = Path("shear")
    way = App.Vector(1, 0, 0)

    def bored(part: Part.Shape, height: float = 10.0) -> Part.Shape:
        return part.cut(Part.makeCylinder(HOLE / 2, 64, App.Vector(-2, 15, height), way))

    first = box((0, 0, 0), (20, 30, 20))
    save(bored(first), out / "one.step")
    sunk = Part.makeCylinder(5.5, 4, App.Vector(0, 15, 10), way)
    save(bored(first).cut(sunk), out / "one-sunk.step")
    save(bored(first, 5.2), out / "one-low.step")
    slot = box((30, 10.85, 5.2), (34.9, 19.15, 21))
    for name, end in (("two", 40.0), ("two-longer", 50.0)):
        save(bored(box((20, 0, 0), (end, 30, 20))).cut(slot), out / f"{name}.step")
    save(bored(box((20, 0, 0), (40, 30, 20))), out / "two-plain.step")
    washers = box((24, 9.5, -1), (29, 20.5, 21))
    save(bored(box((20, 0, 0), (40, 30, 20)), 5.2).cut(washers), out / "two-low.step")
    for name, one, two, z, end, more in (
        ("round-slot", "one", "two", 10.0, 40, {"pocket": 0.15}),
        ("longer", "one", "two-longer", 10.0, 50, {"pocket": 0.15}),
        ("sunk", "one-sunk", "two-plain", 10.0, 40, {}),
        ("tears", "one-low", "two-low", 5.2, 40, {}),
    ):
        parts: Rows = [
            {"name": "one", "file": f"{one}.step", "layers": "z"},
            {"name": "two", "file": f"{two}.step", "layers": "z"},
        ]
        joint: dict[str, Any] = {"between": ["one", "two"], **M5, **more, "at": [[20, 15, z]]}
        assembly(out / f"{name}.json", parts, [joint], [{"plane": f"x={end}", "how": "fixed"}])


#: The drop-in channel's halves (mqucifer/print-shop#302), as the tests' arithmetic reads them:
#: the U's round about the axis through (y 15, z 10), and its walls up to the top at z 20.
CHANNEL_AXIS_YZ, CHANNEL_TOP = (15.0, 10.0), 20.0
CHANNEL_DECLARED = {"axis": "x", "open": "+z", "head_wall": "x=12", "nut_wall": "x=28"}


def channels() -> None:
    """Two halves of a panel on one M5 bolt in a drop-in channel open up, from x 12 to 28 across
    the join at x 20, each with a recess behind its wall for the head or the nut; the second
    9 mm wide with no recess, its end the nut's wall; and the same with no declaration."""
    out = Path("channel")
    y, z = CHANNEL_AXIS_YZ
    radius = HOLE / 2
    u = Part.makeCylinder(radius, 20, App.Vector(10, y, z), App.Vector(1, 0, 0)).fuse(
        box((10, y - radius, z), (30, y + radius, CHANNEL_TOP + 1))
    )
    left = box((0, 0, 0), (20, 30, CHANNEL_TOP)).cut(box((4, 7, 3), (12, 23, 21))).cut(u)
    right = box((20, 0, 0), (40, 30, CHANNEL_TOP)).cut(box((28, 7, 3), (36, 23, 21))).cut(u)
    narrow = box((20, 10.5, 0), (28, 19.5, CHANNEL_TOP)).cut(u)
    for name, shape in (("left", left), ("right", right), ("right-narrow", narrow)):
        save(shape.removeSplitter(), out / f"{name}.step")
    held = [{"plane": "x=0", "how": "fixed"}]
    joint: dict[str, Any] = {"between": ["left", "right"], **M5, "at": [[20, y, z]],
                             "channel": CHANNEL_DECLARED}  # fmt: skip
    for name, right_is, changed in (
        ("joined", "right", {}),
        ("narrow", "right-narrow", {}),
        ("undeclared", "right", {"channel": None}),
    ):
        parts: Rows = [
            {"name": "left", "file": "left.step", "layers": "z"},
            {"name": "right", "file": f"{right_is}.step", "layers": "z"},
        ]
        stated = {k: v for k, v in {**joint, **changed}.items() if v is not None}
        assembly(out / f"{name}.json", parts, [stated], held)

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
#: A slip fit's play, in mm, each face of a socket clear of its tail's (mqucifer/print-shop#188).
SLIP = 0.15
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
    # A slip fit (mqucifer/print-shop#188): the socket 0.15 mm clear of the tail's every face,
    # each face moved square to itself, and its floor where it was, so the tail's tip is
    # seated and its flanks clear.
    slip = wedge(low, high, NECK).makeOffsetShape(SLIP, 1e-7, join=2)
    slip = slip.common(box((-BAR_W, -SOCKET_T, low), (BAR_W, SOCKET_T, BAR_TOP)))
    save(socket_bar.cut(slip), out / "socket-slip.step")
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
    assembly(out / "slip.json", pair("tail.step", "socket-slip.step"), down, floor)
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
    # A latch that can't be fitted (mqucifer/print-shop#184), as eval-table-1's was: a solid
    # stub, the hook alone, 1 mm long from a stiff post on the tail's bar, named as the latch's
    # arm rooted on the post's face. It catches the window's ledge as the tab does, and on the
    # way in it has to move 0.6 mm across y, which is along it: it can't bend that way.
    post = box((-TAB_W / 2, TAB_CLEAR, foot), (TAB_W / 2, TAB_CLEAR + 2.6, low))
    hook = box((-TAB_W / 2, -HOOK_IN, foot), (TAB_W / 2, TAB_CLEAR, foot + 2))
    save(tail.fuse(post).fuse(hook).removeSplitter(), out / "tail-stub.step")
    stub = {
        "on": "upper",
        "arm": [[-TAB_W / 2 - 0.1, -HOOK_IN - 0.1, foot - 0.1],
                [TAB_W / 2 + 0.1, TAB_CLEAR, foot + 2.1]],
        "root": f"y={TAB_CLEAR:g}",
    }  # fmt: skip
    stubbed: Rows = [{**down[0], "latch": stub}]
    assembly(out / "stub.json", pair("tail-stub.step", "socket-latch.step"), stubbed, floor)
    # A way in the file names that the parts can't take (mqucifer/print-shop#185), as
    # eval-table-1's leg split was: a straight key 8 mm wide in a pocket 1 mm taller than it,
    # whose mouth at the top of the socket's bar is 6 mm wide, narrower than the key. The file
    # says it slides in -z, and its tip is seated, but it can't pass the mouth either way along
    # z: the only way it comes out is +y, off the lap face, so it goes in -y.
    key = box((-4, -TAIL_DEEP, low), (4, 0, low + 34))
    save(tail_bar.fuse(key).removeSplitter(), out / "tail-key.step")
    pocket = box((-4, -TAIL_DEEP, low), (4, 0, low + 35))
    mouth = box((-3, -TAIL_DEEP, low + 35), (3, 0, high))
    save(socket_bar.cut(pocket).cut(mouth), out / "socket-mouth.step")
    assembly(out / "mouth.json", pair("tail-key.step", "socket-mouth.step"), down, floor)
    # The same pair as it does go together, the file saying so: in -y.
    sideways: Rows = [{**down[0], "slides": "-y"}]
    assembly(out / "mouth-sideways.json", pair("tail-key.step", "socket-mouth.step"), sideways,
             floor)  # fmt: skip
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


#: The blocks with play (mqucifer/print-shop#188): each `BLOCK` square, the lower `LOWER_T` tall
#: and the upper `UPPER_T` on it, the tail `TAIL_SIDE` square and `TAIL_LONG` long, the socket
#: `SLIP` wider a side, or `LOOSE`.
BLOCK, LOWER_T, UPPER_T, TAIL_SIDE, TAIL_LONG, LOOSE = 40.0, 80.0, 20.0, 20.0, 20.0, 1.5
#: More play than `load` reads as able to close, its `PLAY`, 2 mm.
WIDE = 2.5
#: Their latch: a slender tab `SLENDER` thick, `TAB_W` wide, hanging `TAB_L` from the upper's
#: underside into a pocket in the lower's top, `TAB_CLEAR` off the pocket's -y wall at y
#: `WALL_AT`, its hook `HOOK_IN` into a window in that wall and `LATCH_PLAY` under its ledge,
#: with `ROOM` behind it to bend. Near the +x edge it's at x `NEAR_EDGE`, with seat beside it all
#: round; at the middle, the window's sides touch the hook's.
SLENDER, WALL_AT, ROOM_BEHIND, NEAR_EDGE = 0.8, -15.0, 1.0, 14.0


def plays() -> None:
    """A square tail in a square socket with play, latched near an edge or at the middle."""
    out = Path("play")
    half, side = BLOCK / 2, TAIL_SIDE / 2
    top = LOWER_T
    seat = top - TAIL_LONG
    foot = top - TAB_L
    lower = box((-half, -half, 0), (half, half, top))
    upper = box((-half, -half, top), (half, half, top + UPPER_T))
    tail = box((-side, -side, seat), (side, side, top))
    face = WALL_AT + TAB_CLEAR

    def tabbed(at: float) -> Part.Shape:
        arm = box((at - TAB_W / 2, face, foot), (at + TAB_W / 2, face + SLENDER, top))
        hook = box((at - TAB_W / 2, WALL_AT - HOOK_IN, foot), (at + TAB_W / 2, face, foot + 2))
        return arm.fuse(hook)

    def socketed(play: float, at: float, wide: float) -> Part.Shape:
        """The lower with its socket, the latch's pocket, and the window in the pocket's wall,
        `wide` wider a side than the hook."""
        socket = box((-side - play, -side - play, seat), (side + play, side + play, top))
        pocket = box((at - TAB_W / 2 - 1, WALL_AT, foot - 0.5),
                     (at + TAB_W / 2 + 1, face + SLENDER + ROOM_BEHIND, top))  # fmt: skip
        window = box((at - TAB_W / 2 - wide, WALL_AT - 2 * HOOK_IN, foot - 0.5),
                     (at + TAB_W / 2 + wide, WALL_AT, foot + 2 + LATCH_PLAY))  # fmt: skip
        return lower.cut(socket).cut(pocket).cut(window)

    for name, at in (("edge", NEAR_EDGE), ("middle", 0.0)):
        save(upper.fuse(tail).fuse(tabbed(at)).removeSplitter(), out / f"upper-{name}.step")
    save(socketed(SLIP, NEAR_EDGE, 1.0), out / "lower-edge.step")
    save(socketed(SLIP, 0.0, 0.0), out / "lower-middle.step")
    save(socketed(LOOSE, NEAR_EDGE, 1.0), out / "lower-loose.step")
    save(socketed(WIDE, 0.0, 0.0), out / "lower-wide.step")

    def latched(at: float) -> Rows:
        latch = {
            "on": "upper",
            "arm": [[at - TAB_W / 2 - 0.1, WALL_AT - HOOK_IN - 0.1, foot - 0.1],
                    [at + TAB_W / 2 + 0.1, face + SLENDER + 0.1, top]],
            "root": f"z={top:g}",
        }  # fmt: skip
        return [{"between": ["upper", "lower"], "kind": "dovetail", "slides": "-z",
                 "latch": latch}]  # fmt: skip

    floor = [{"plane": "z=0", "how": "fixed"}]
    for name, upper_file, lower_file, at in (
        ("edge", "upper-edge.step", "lower-edge.step", NEAR_EDGE),
        ("middle", "upper-middle.step", "lower-middle.step", 0.0),
        ("loose", "upper-edge.step", "lower-loose.step", NEAR_EDGE),
        ("wide", "upper-middle.step", "lower-wide.step", 0.0),
    ):
        parts = [
            {"name": "upper", "file": upper_file, "layers": "z"},
            {"name": "lower", "file": lower_file, "layers": "z"},
        ]
        assembly(out / f"{name}.json", parts, latched(at), floor)


#: The posts' ring (mqucifer/print-shop#295): each post this far from the middle, in mm.
RING = 100.0


def turned() -> None:
    """The latched post of `play/` (`upper-edge.step` on `lower-edge.step`, its latch near the
    edge) placed four times round the middle, as a table's legs are at its corners: each pair
    turned about z by the same angle and moved out `RING` the way it then faces. A quarter turn
    apart from 0, and from 45. Each latch is named once, in the upper's own file's axes, as
    `play/edge.json` names it in the assembly's, and carried with the post."""
    out = Path("turned")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("upper-edge.step", "lower-edge.step"):
        shutil.copyfile(Path("play") / name, out / name)
    foot = LOWER_T - TAB_L
    face = WALL_AT + TAB_CLEAR
    latch = {
        "on": "upper",
        "arm": [[NEAR_EDGE - TAB_W / 2 - 0.1, WALL_AT - HOOK_IN - 0.1, foot - 0.1],
                [NEAR_EDGE + TAB_W / 2 + 0.1, face + SLENDER + 0.1, LOWER_T]],
        "root": f"z={LOWER_T:g}",
        "frame": "part",
    }  # fmt: skip
    floor = [{"plane": "z=0", "how": "fixed"}]

    def posts(first: float, count: int = 4, frame: str = "part") -> tuple[Rows, Rows]:
        parts: Rows = []
        joints: Rows = []
        for k in range(count):
            angle = first + 90.0 * k
            at = [RING * math.cos(math.radians(angle)), RING * math.sin(math.radians(angle)), 0]
            where = {"turn": [["z", angle]], "at": at, "layers": "z"}
            parts += [
                {"name": f"upper{k}", "file": "upper-edge.step", **where},
                {"name": f"lower{k}", "file": "lower-edge.step", **where},
            ]
            joints.append({"between": [f"upper{k}", f"lower{k}"], "kind": "dovetail",
                           "slides": "-z", "latch": {**latch, "on": f"upper{k}"}})  # fmt: skip
        return parts, joints

    for name, first in (("quarter", 0.0), ("eighth", 45.0)):
        parts, joints = posts(first)
        assembly(out / f"{name}.json", parts, joints, floor)
    # One post, unturned and in place: its latch named in its own axes reads as `play/edge.json`
    # names it in the assembly's.
    one = [
        {"name": "upper", "file": "upper-edge.step", "layers": "z"},
        {"name": "lower", "file": "lower-edge.step", "layers": "z"},
    ]
    alone: Rows = [{"between": ["upper", "lower"], "kind": "dovetail", "slides": "-z",
                    "latch": latch}]  # fmt: skip
    assembly(out / "own.json", one, alone, floor)


#: The large table (mqucifer/print-shop#295), in mm: `SIDE` square, its top `TOP_T` thick on
#: aprons `APRON_T` thick and `APRON_H` tall, standing `HIGH` to the top's underside, on legs
#: `LEG` square split at `SPLIT` and spliced by a plate `SPLICE_T` thick. Round figures only.
SIDE, TOP_T, APRON_T, APRON_H, HIGH, LEG, SPLIT, SPLICE_T = 400, 10, 20, 40, 300, 40, 150, 10
#: An M5 bolt's hole, a nut's slot 0.2 mm clear of its flats, and a washer's half width.
HOLE_R, SLOT_W, SLOT_H = 2.75, 8.4, 5.0


def _bore(start: tuple[float, float, float], way: tuple[float, float, float], long: float) -> Any:
    return Part.makeCylinder(HOLE_R, long, App.Vector(*start), App.Vector(*way))


def _slot(centre: tuple[float, float, float], way: str, out: float, deep: str) -> Any:
    """A nut's slot round a bore's end: `SLOT_W` across its flats, `SLOT_H` tall along the
    bore, and from the bore's axis out to `out` along `way`. `deep` is the bore's axis."""
    half = SLOT_W / 2
    low, high = [c - half for c in centre], [c + half for c in centre]
    k, d = "xyz".index(way[1]), "xyz".index(deep)
    if way[0] == "+":
        low[k], high[k] = centre[k] - half, out
    else:
        low[k], high[k] = out, centre[k] + half
    low[d], high[d] = centre[d] - SLOT_H, centre[d]
    return box(tuple(low), tuple(high))  # type: ignore[arg-type]


def large() -> None:
    """A table of 24 parts held by 56 M5 through-bolts (mqucifer/print-shop#295): a top of four
    tiles, each on its two aprons, its leg and a rib under it; each leg in two pieces end to end,
    spliced by a plate bolted to both; each apron bolted to the legs it laps. One corner's six
    parts are drawn, and each other corner's are the same turned a quarter turn about the
    table's middle, so the aprons run round it as a pinwheel, each 1 mm short of the next.

    A tile is bolted down through each apron and its rib, its washer under the apron or the rib,
    and into its leg's top, the nut in a slot in the leg's side; an apron through its leg, the
    nut on the leg's inner face; the plate through each piece of the leg, the nut on its outer
    face; and an apron into its rib's end, the nut in a slot in the rib. The leg's two pieces
    touch end to end, held by the plate."""
    out = Path("large")
    top, low = HIGH, HIGH - APRON_H
    inner, half = LEG + 20, (SIDE - 1) / 2
    # One corner's parts, where they sit: x and y from 0, the top's underside at z = HIGH.
    tile = box((0, 0, top), (half, half, top + TOP_T))
    apron = box((0, 0, low), (SIDE - 21, APRON_T, top))
    upper = box((APRON_T, APRON_T, SPLIT), (inner, inner, top))
    lower = box((APRON_T, APRON_T, 0), (inner, inner, SPLIT))
    plate = box((inner, APRON_T, SPLIT - 50), (inner + SPLICE_T, inner, SPLIT + 50))
    rib = box((120, APRON_T, low), (140, half - 0.5, top))
    # Where each bolt is, a point on its axis.
    along_y, along_x = (0, 1, 0), (1, 0, 0)
    bolts: dict[str, list[tuple[float, float, float]]] = {
        "tile+apron": [(100, 10, top), (170, 10, top)],
        "tile+side": [(10, 100, top), (10, 170, top)],
        "tile+rib": [(130, 90, top), (130, 170, top)],
        "tile+upper": [(40, 40, top)],
        "apron+upper": [(30, 0, 265)],
        "side+upper": [(0, 30, 272)],
        "plate+upper": [(inner + SPLICE_T, 40, 165), (inner + SPLICE_T, 40, 185)],
        "plate+lower": [(inner + SPLICE_T, 40, 115), (inner + SPLICE_T, 40, 135)],
        "apron+rib": [(130, 0, 280)],
    }
    for x, y, _ in bolts["tile+apron"]:
        tile, apron = tile.cut(_bore((x, y, low), (0, 0, 1), 60)), apron.cut(
            _bore((x, y, low), (0, 0, 1), 60))
    for x, y, _ in bolts["tile+rib"]:
        tile, rib = tile.cut(_bore((x, y, low), (0, 0, 1), 60)), rib.cut(
            _bore((x, y, low), (0, 0, 1), 60))
    for x, y, _ in bolts["tile+side"]:
        # Through the next corner's tile too, which this apron is the side of: the same
        # bolt a quarter turn on, (x, y) to (SIDE - y, x).
        tile = tile.cut(_bore((x, y, low), (0, 0, 1), 60))
        apron = apron.cut(_bore((SIDE - y, x, low), (0, 0, 1), 60))
    # The leg's top: a bore 20 deep and the nut's slot, open to the leg's inner x face.
    x, y, z = bolts["tile+upper"][0]
    tile = tile.cut(_bore((x, y, top - 20), (0, 0, 1), 40))
    upper = upper.cut(_bore((x, y, top - 20), (0, 0, 1), 40)).cut(
        _slot((x, y, top - 20), "+x", inner, "z"))
    for x, _, z in bolts["apron+upper"]:
        apron, upper = (s.cut(_bore((x, -1, z), along_y, inner + 2)) for s in (apron, upper))
    for _, y, z in bolts["side+upper"]:
        upper = upper.cut(_bore((-1, y, z), along_x, inner + 2))
        # And the next corner's leg's through this apron, a quarter turn on.
        apron = apron.cut(_bore((SIDE - y, -1, z), along_y, APRON_T + 2))
    for _, y, z in bolts["plate+upper"] + bolts["plate+lower"]:
        plate, upper, lower = (s.cut(_bore((APRON_T - 1, y, z), along_x, LEG + SPLICE_T + 2))
                               for s in (plate, upper, lower))  # fmt: skip
    # The apron into its rib's end, the nut in a slot open to the rib's +x side, 40 mm in.
    x, y, z = bolts["apron+rib"][0]
    apron = apron.cut(_bore((x, -1, z), along_y, APRON_T + 2))
    rib = rib.cut(_bore((x, APRON_T - 1, z), along_y, 41)).cut(
        _slot((x, APRON_T + 40, z), "+x", 140, "y"))
    corner = {"tile": tile, "apron": apron, "upper": upper, "lower": lower, "plate": plate,
              "rib": rib}  # fmt: skip
    middle = App.Vector(SIDE / 2, SIDE / 2, 0)
    parts: Rows = []
    for k in range(4):
        for name, shape in corner.items():
            turned = shape.copy()
            turned.rotate(middle, App.Vector(0, 0, 1), 90 * k)
            save(turned, out / f"{name}{k}.step")
            parts.append({"name": f"{name}{k}", "file": f"{name}{k}.step", "layers": "z"})

    def turn(point: tuple[float, float, float], k: int) -> list[float]:
        x, y, z = point
        for _ in range(k):
            x, y = SIDE - y, x
        return [x, y, z]

    joints: Rows = []
    for k in range(4):
        side = (k + 3) % 4
        names = {"tile": f"tile{k}", "apron": f"apron{k}", "side": f"apron{side}",
                 "upper": f"upper{k}", "lower": f"lower{k}", "plate": f"plate{k}",
                 "rib": f"rib{k}"}  # fmt: skip
        for pair, at in bolts.items():
            first, second = (names[n] for n in pair.split("+"))
            joint: dict[str, Any] = {
                "between": [first, second], "kind": "through", "bolt": "m5-8.8",
                "nut": "m5-nut", "washer": "m5-washer", "at": [turn(p, k) for p in at],
            }  # fmt: skip
            if pair in ("tile+upper", "apron+rib"):
                joint["pocket"] = 0.2
            joints.append(joint)
        joints.append({"between": [names["upper"], names["lower"]], "kind": "touching"})
    standing = [{"plane": "z=0", "how": "standing"}]
    assembly(out / "table.json", parts, joints, standing)
    # The control: one bolt of the first tile's rib left out, the other still in.
    fewer = [dict(j) for j in joints]
    rib = next(j for j in fewer if j["between"] == ["tile0", "rib0"])
    rib["at"] = rib["at"][1:]
    assembly(out / "one-out.json", parts, fewer, standing)


#: The stool (mqucifer/print-shop#295): a top `STOOL` square and `STOOL_T` thick on four legs
#: `STOOL_LEG` square and `STOOL_H` tall, each held by one M5 through-bolt down through the top
#: into the leg's top, `STOOL_BORE` deep, its nut in a slot open to the leg's inner side.
STOOL, STOOL_T, STOOL_LEG, STOOL_H, STOOL_BORE = 200.0, 8.0, 30.0, 150.0, 20.0


def stool() -> None:
    """Four legs bolted under a top, one bolt each, fixed to the floor: the control for the
    large table (mqucifer/print-shop#295), small enough to read. Pushed sideways at the top, each
    leg's joint carries a moment, which its bolt holds; with leg2's bolt left out, that joint is
    `touching`, held by nothing, and comes away."""
    out = Path("large")
    corners = [(0.0, 0.0), (STOOL - STOOL_LEG, 0.0), (STOOL - STOOL_LEG, STOOL - STOOL_LEG),
               (0.0, STOOL - STOOL_LEG)]  # fmt: skip
    top = box((0, 0, STOOL_H), (STOOL, STOOL, STOOL_H + STOOL_T))
    parts: Rows = []
    joints: Rows = []
    for k, (x, y) in enumerate(corners):
        axis = (x + STOOL_LEG / 2, y + STOOL_LEG / 2)
        bore = _bore((*axis, STOOL_H - STOOL_BORE), (0, 0, 1), STOOL_BORE + STOOL_T + 1)
        top = top.cut(bore)
        inward = "+x" if x == 0 else "-x"
        far = x + STOOL_LEG if x == 0 else x
        leg = box((x, y, 0), (x + STOOL_LEG, y + STOOL_LEG, STOOL_H)).cut(bore).cut(
            _slot((*axis, STOOL_H - STOOL_BORE), inward, far, "z"))
        save(leg, out / f"stool-leg{k}.step")
        parts.append({"name": f"leg{k}", "file": f"stool-leg{k}.step", "layers": "z"})
        joints.append({"between": ["top", f"leg{k}"], "kind": "through", "bolt": "m5-8.8",
                       "nut": "m5-nut", "washer": "m5-washer",
                       "at": [[*axis, STOOL_H]], "pocket": 0.2})  # fmt: skip
    save(top, out / "stool-top.step")
    parts.insert(0, {"name": "top", "file": "stool-top.step", "layers": "z"})
    # Its feet fixed to the floor, as a push along it needs.
    fixed = [{"plane": "z=0", "how": "fixed"}]
    assembly(out / "stool.json", parts, joints, fixed)
    # The control: leg2's bolt left out, so its joint is held by nothing.
    fewer = [dict(j) for j in joints]
    fewer[2] = {"between": ["top", "leg2"], "kind": "touching"}
    assembly(out / "stool-one-out.json", parts, fewer, fixed)


#: The sleeved core (mqucifer/print-shop#320): a core `CORE_R` round and `CORE_H` tall in a sleeve
#: `SLEEVE_T` thick, in a ring `RING_T` thick.
CORE_R, CORE_H, SLEEVE_T, RING_T = 10.0, 8.0, 0.5, 5.0


def sleeved() -> None:
    """A core in a thin sleeve in a ring, each joint bonded all round, the ring held at its
    foot and the core pushed down (mqucifer/print-shop#320). Meshed curved at 5 mm, the sleeve
    is one element thick, each a sliver between its two curved faces, with every node on a
    joint's faces: there the guess by position took one side's mid-side node for another's."""
    out = Path("order")
    up = App.Vector(0, 0, 1)

    def round_(radius: float) -> Any:
        return Part.makeCylinder(radius, CORE_H, App.Vector(0, 0, 0), up)

    outer = CORE_R + SLEEVE_T
    save(round_(CORE_R), out / "core.step")
    save(round_(outer).cut(round_(CORE_R)), out / "sleeve.step")
    save(round_(outer + RING_T).cut(round_(outer)), out / "ring.step")
    parts = [{"name": n, "file": f"{n}.step", "layers": "z"} for n in ("core", "sleeve", "ring")]
    joints = [{"between": ["core", "sleeve"], "kind": "bonded"},
              {"between": ["sleeve", "ring"], "kind": "bonded"}]  # fmt: skip
    held = [{"plane": "z=0", "how": "fixed", "part": "ring"}]
    assembly(out / "sleeved.json", parts, joints, held)


#: The shelf bracket and the ribbed slices (mqucifer/print-shop#319), in mm and degrees.
SHELF_T, SHELF_W, SHELF_H, SHELF_L, SHELF_WALL, SHELF_ROUND = 5.0, 10.0, 25.0, 30.0, 10.0, 3.0
RIB_IN, RIB_OUT, RIB_CAP, RIB_TALL, RIB_LOW, RIB_HIGH = 50.0, 56.0, 4.0, 80.0, 30.0, 40.0
#: Each slice's angles, the seam's between them, and the cap's.
RIB_SLICES, RIB_SEAM, RIB_SPAN = ((0.0, 29.0), (31.0, 60.0)), (29.0, 31.0), (10.0, 50.0)
#: The peg in its hole: the socket a cube `PEG_SIDE` with a hole `PEG_DEEP` deep, the peg as
#: wide and `PEG_LONG` long, standing on the hole's floor.
PEG_R, PEG_DEEP, PEG_SIDE, PEG_LONG = 3.0, 10.0, 20.0, 20.0
#: Each way the print pose is placed: its name in the files and its turns, in order.
PLACINGS = {
    "x": [["x", -90]], "y": [["y", 90]], "z": [["z", 90]], "xz": [["x", -90], ["z", 90]],
}  # fmt: skip


def _turned(turns: list[list[Any]]) -> Any:
    """Turns about the origin's axes, in order, as one rotation."""
    total = App.Matrix()
    for axis, degrees in turns:
        one = App.Matrix()
        {"x": one.rotateX, "y": one.rotateY, "z": one.rotateZ}[axis](math.radians(degrees))
        total = one.multiply(total)
    return total


def print_pose(shape: Part.Shape, turns: list[list[Any]]) -> Part.Shape:
    """A part drawn in place, drawn again as it lies on the bed: turned back from where `turns`
    take it, in its geometry, with no placement left, as a design run draws it."""
    drawn = shape.copy()
    drawn.transformShape(_turned(turns).inverse(), True)
    assert drawn.Placement.isIdentity()
    return drawn


def layers_drawn(layers: str, turns: list[list[Any]]) -> str:
    """The axis of the print pose's own file that its layers, `layers` in place, are along."""
    way = _turned(turns).inverse().multVec(App.Vector(*(float(a == layers) for a in "xyz")))
    return "xyz"[max(range(3), key=lambda k: abs(way[k]))]


def ring_slice(inner: float, outer: float, angles: tuple[float, float], low: float,
               high: float) -> Part.Shape:  # fmt: skip
    """A ring's slice round z, between two angles in degrees, drawn where it is."""
    way, at, wide = App.Vector(0, 0, 1), App.Vector(0, 0, low), angles[1] - angles[0]
    made = Part.makeCylinder(outer, high - low, at, way, wide)
    made = made.cut(Part.makeCylinder(inner, high - low, at, way, wide))
    made.transformShape(_turned([["z", angles[0]]]), True)
    assert made.Placement.isIdentity()
    return made


def turns() -> None:
    """Each part drawn in place, and in its print pose placed by its turns."""
    out = Path("turns")
    upright = box((0, 0, 0), (SHELF_T, SHELF_W, SHELF_H))
    arm = box((0, 0, SHELF_H - SHELF_T), (SHELF_L, SHELF_W, SHELF_H))
    bracket = upright.fuse(arm).removeSplitter()
    # Its inside corner rounded, so no figure is a sharp corner's, which no mesh reads alike.
    inside = [e for e in bracket.Edges
              if abs(e.BoundBox.XMin - SHELF_T) < 1e-6 and abs(e.BoundBox.XMax - SHELF_T) < 1e-6
              and abs(e.BoundBox.ZMin - (SHELF_H - SHELF_T)) < 1e-6 and e.BoundBox.ZLength < 1e-6]
    assert len(inside) == 1, inside
    bracket = bracket.makeFillet(SHELF_ROUND, inside)
    save(box((-SHELF_WALL, 0, 0), (0, SHELF_W, SHELF_H)), out / "wall.step")
    save(bracket, out / "bracket.step")
    middle = [[0, SHELF_W / 2, SHELF_H / 2]]
    shelf_joint: Rows = [{"between": ["wall", "bracket"], **BOLT, **HOLDS, "at": middle}]
    held = [{"plane": f"x={-SHELF_WALL:g}", "how": "fixed"}]

    def shelf(bracket_row: dict[str, Any]) -> Rows:
        # Printed on its side: the L flat on the bed, its layers along its width.
        return [{"name": "wall", "file": "wall.step", "layers": "z"},
                {"name": "bracket", **bracket_row}]  # fmt: skip

    assembly(out / "shelf.json", shelf({"file": "bracket.step", "layers": "y"}), shelf_joint, held)
    for name, placing in PLACINGS.items():
        save(print_pose(bracket, placing), out / f"bracket-{name}.step")
        row = {"file": f"bracket-{name}.step", "turn": placing,
               "layers": layers_drawn("y", placing)}  # fmt: skip
        assembly(out / f"shelf-{name}.json", shelf(row), shelf_joint, held)
    # The ribbed slices: the rib touches each slice on its cap's curved inside and its fin's
    # flat side, and nothing else holds it.
    for k, angles in enumerate(RIB_SLICES, 1):
        save(ring_slice(RIB_IN, RIB_OUT, angles, 0, RIB_TALL), out / f"slice{k}.step")
    cap = ring_slice(RIB_OUT, RIB_OUT + RIB_CAP, RIB_SPAN, RIB_LOW, RIB_HIGH)
    fin = ring_slice(RIB_IN, RIB_OUT, RIB_SEAM, RIB_LOW, RIB_HIGH)
    rib = cap.fuse(fin).removeSplitter()
    touching: Rows = [{"between": ["rib", f"slice{k}"], "kind": "touching"} for k in (1, 2)]
    floor = [{"plane": "z=0", "how": "fixed"}]

    def ribbed(rib_row: dict[str, Any]) -> Rows:
        slices: Rows = [{"name": f"slice{k}", "file": f"slice{k}.step", "layers": "z"}
                        for k in (1, 2)]  # fmt: skip
        return [*slices, {"name": "rib", **rib_row}]

    # In place, printed with its cap on the bed: its layers along y once placed.
    save(rib, out / "rib.step")
    assembly(out / "ribbed.json", ribbed({"file": "rib.step", "layers": "y"}), touching, floor)
    on_side = PLACINGS["x"]
    save(print_pose(rib, on_side), out / "rib-x.step")
    row = {"file": "rib-x.step", "turn": on_side, "layers": layers_drawn("y", on_side)}
    assembly(out / "ribbed-x.json", ribbed(row), touching, floor)
    # Turned 30° about z as well, as a lamp's second rib is: in place but for that turn, and in
    # its print pose placed by both.
    round_z = [["z", 30.0]]
    save(print_pose(rib, round_z), out / "rib-z30.step")
    row = {"file": "rib-z30.step", "turn": round_z, "layers": "y"}
    assembly(out / "ribbed-z30.json", ribbed(row), touching, floor)
    both = [*on_side, *round_z]
    save(print_pose(rib, both), out / "rib-xz30.step")
    row = {"file": "rib-xz30.step", "turn": both, "layers": layers_drawn("y", on_side)}
    assembly(out / "ribbed-xz30.json", ribbed(row), touching, floor)
    # A peg in a blind hole, touching its wall and its floor, which meet along a round edge: on
    # a coarse straight-sided mesh the floor's triangles have all their corners on that edge.
    hole = Part.makeCylinder(PEG_R, PEG_DEEP, App.Vector(PEG_SIDE / 2, PEG_SIDE / 2,
                                                         PEG_SIDE - PEG_DEEP))  # fmt: skip
    save(box((0, 0, 0), (PEG_SIDE, PEG_SIDE, PEG_SIDE)).cut(hole), out / "socket.step")
    peg = Part.makeCylinder(PEG_R, PEG_LONG, App.Vector(PEG_SIDE / 2, PEG_SIDE / 2,
                                                        PEG_SIDE - PEG_DEEP))  # fmt: skip
    save(peg, out / "peg.step")
    pegged: Rows = [{"name": "socket", "file": "socket.step", "layers": "z"},
                    {"name": "peg", "file": "peg.step", "layers": "z"}]  # fmt: skip
    assembly(out / "peg.json", pegged, [{"between": ["peg", "socket"], "kind": "touching"}], floor)


#: The strip in its seam (mqucifer/print-shop#334): its thickness, length and height, how far it
#: stands off the floor, and the seam's turn about z.
SEAM_T, SEAM_L, SEAM_H, SEAM_LOW, SEAM_TURN = 1.5, 30.0, 40.0, 10.0, 30.0
#: Where it's placed in its seam's own axes before that turn, as a design run's ribs are.
SEAM_AT = (0.0, -2.35, 0.0)


def seam() -> None:
    """A strip in a seam between two blocks, the seam off the axes: placed by two turns, with
    its `at` to six decimals, and by one, exactly."""
    out = Path("seam")
    round_z = [["z", SEAM_TURN]]
    both = [["x", -90], ["z", SEAM_TURN]]

    def in_seam(low: tuple[float, float, float], high: tuple[float, float, float]) -> Any:
        made = box(low, high)
        made.transformShape(_turned(round_z), True)
        return made

    t, long, h = SEAM_T, SEAM_L, SEAM_H
    # The blocks' tops below the strip's: it stands proud of them, so the faces it meets them
    # by are bounded by their edges as well as its own.
    save(in_seam((-10, -20, 0), (long + 10, 0, h)), out / "block1.step")
    save(in_seam((-10, t, 0), (long + 10, t + 20, h)), out / "block2.step")
    at = _turned(round_z).multVec(App.Vector(*SEAM_AT))
    strip = box((0, 0, SEAM_LOW), (long, t, SEAM_LOW + h))
    strip.transformShape(App.Matrix(1, 0, 0, -SEAM_AT[0], 0, 1, 0, -SEAM_AT[1], 0, 0, 1,
                                    -SEAM_AT[2], 0, 0, 0, 1), True)  # fmt: skip
    strip.transformShape(_turned(round_z), True)
    save(print_pose(strip, both), out / "strip-flat.step")
    save(print_pose(strip, round_z), out / "strip-standing.step")
    blocks: Rows = [{"name": f"block{k}", "file": f"block{k}.step", "layers": "z"} for k in (1, 2)]
    joints: Rows = [{"between": ["strip", f"block{k}"], "kind": "bonded"} for k in (1, 2)]
    held = [{"plane": "z=0", "how": "fixed"}]
    flat = {"name": "strip", "file": "strip-flat.step", "turn": both, "layers": "z"}
    standing = {"name": "strip", "file": "strip-standing.step", "turn": round_z, "layers": "y"}
    exact, written = [at.x, at.y, at.z], [round(v, 6) for v in (at.x, at.y, at.z)]
    assembly(out / "two-turns.json", [*blocks, {**flat, "at": written}], joints, held)
    assembly(out / "one-turn.json", [*blocks, {**standing, "at": exact}], joints, held)
    assembly(out / "one-turn-written.json", [*blocks, {**standing, "at": written}], joints, held)


#: A printed bar's settings (mqucifer/print-shop#337): the store's own lines, 0.45 mm walls and
#: 0.2 mm layers, five layers on top and three under, as `profiles/standard` gives them.
PRINT = {"line_width": 0.45, "top": 5, "bottom": 3, "pattern": "grid", "infill_angle": 0,
         "layer_height": 0.2, "up": "+z"}
#: The printed bar's walls and densities, and its box round the root.
PRINT_WALLS, PRINT_INFILL = (2, 4), (20, 40, 60)


def printed() -> None:
    """The beam printed (mqucifer/print-shop#337): at each of 2 wall counts and 3 densities,
    held at its root and loaded at its tip as `load/one.json` is; and at 2 walls and 20% with a
    box round its root, its first 30 mm, at 60% and with walls of its own."""
    out = Path("load")
    wall = [{"plane": "x=0", "how": "fixed"}]
    for walls in PRINT_WALLS:
        for infill in PRINT_INFILL:
            settings = {**PRINT, "walls": walls, "infill": infill}
            row = {"name": "beam", "file": "beam.step", "layers": "z", "print": settings}
            assembly(out / f"printed-{walls}-{infill}.json", [row], held=wall)
    root = {"name": "root", "box": [[-1, -1, -1], [30, BEAM_W + 1, BEAM_T + 1]], "infill": 60,
            "why": "denser at the root, where it's bent most"}
    walled = {**root, "name": "walled", "walls": 4}
    # The beam's lower half denser, as a band of layers in its own z, and a box over it at its
    # root, which wins over the band whatever their order.
    band = {"name": "band", "layers": [0, 5], "infill": 40}
    for name, region in (("dense", root), ("walled", walled)):
        settings = {**PRINT, "walls": 2, "infill": 20, "regions": [region]}
        row = {"name": "beam", "file": "beam.step", "layers": "z", "print": settings}
        assembly(out / f"printed-{name}.json", [row], held=wall)
    settings = {**PRINT, "walls": 2, "infill": 20, "regions": [root, band]}
    row = {"name": "beam", "file": "beam.step", "layers": "z", "print": settings}
    assembly(out / "printed-band.json", [row], held=wall)
    # The beam printed turned over, own -z up: its 5 top layers on its z 0 face.
    settings = {**PRINT, "walls": 2, "infill": 20, "up": "-z"}
    row = {"name": "beam", "file": "beam.step", "layers": "z", "print": settings}
    assembly(out / "printed-over.json", [row], held=wall)
    # A block pulled straight across its layers, held on its bed face, its top pulled up.
    save(box((0, 0, 0), (40, 40, 10)), out / "pulled.step")
    settings = {**PRINT, "walls": 2, "infill": 20}
    row = {"name": "block", "file": "pulled.step", "layers": "z", "print": settings}
    assembly(out / "printed-pulled.json", [row], held=[{"plane": "z=0", "how": "fixed"}])
    # The beam as two halves bolted at its middle, as `two.json`, each printed: each has its own
    # walls at the faces where they meet.
    half = BEAM_L / 2
    halves: Rows = [
        {"name": "root", "file": "half.step", "layers": "z",
         "print": {**PRINT, "walls": 2, "infill": 20}},
        {"name": "tip", "file": "half.step", "at": [half, 0, 0], "layers": "z",
         "print": {**PRINT, "walls": 2, "infill": 20}},
    ]
    bolted: Rows = [
        {"between": ["root", "tip"], **BOLT, **HOLDS, "at": [[half, BEAM_W / 2, BEAM_T / 2]]}
    ]
    assembly(out / "printed-two.json", halves, bolted, wall)
    # The same bar's lines along it, and across it.
    for angle in (0, 45):
        settings = {**PRINT, "pattern": "rectilinear", "walls": 2, "infill": 20,
                    "infill_angle": angle}
        row = {"name": "beam", "file": "beam.step", "layers": "z", "print": settings}
        assembly(out / f"printed-lines-{angle}.json", [row], held=wall)


#: The bowed bars (mqucifer/print-shop#356): a leg straight for 40 mm along x and then curving
#: 25 mm over 80 on a B-spline tangent to it, 20 across in the plane it bows in and 10 thick
#: square to it; and a bar 120 x 20 x 10 lofted through 9 square sections bowed 25 mm, in mm.
LEG_STRAIGHT, LEG_RISE, LEG_RUN, BOW = 40.0, 25.0, 80.0, 25.0
#: The notch in the leg bowed along its layers: 6 along x from x 77, 4 into its flat side.
NOTCH_AT, NOTCH_LONG, NOTCH_DEEP = 77.0, 6.0, 4.0
#: Where each bowed bar is loaded: along x, over the middle of its section there.
BOWED_AT = 110.0


def bowed_leg(tilt: float) -> Part.Shape:
    """The leg, bowing in the plane turned `tilt` degrees about x from its layers' plane: its
    sides flat along the straight and B-spline along the curve, tangent where they meet, made
    by interpolating points offset square to its middle line; flat across its thickness."""
    turn = math.radians(tilt)
    up = App.Vector(0, math.cos(turn), math.sin(turn))
    across = App.Vector(0, -math.sin(turn), math.cos(turn))

    def middle(s: float) -> tuple[App.Vector, App.Vector]:
        point = App.Vector(LEG_STRAIGHT + LEG_RUN * s, 0, 0) + up * (LEG_RISE * s * s)
        return point, (App.Vector(LEG_RUN, 0, 0) + up * (2 * LEG_RISE * s)).normalize()

    sides = []
    for k in (-1, 1):
        points = []
        for i in range(13):
            point, way = middle(i / 12)
            points.append(point + across.cross(way).normalize() * (k * BEAM_W / 2))
        curve = Part.BSplineCurve()
        curve.interpolate(points, InitialTangent=middle(0)[1], FinalTangent=middle(1)[1])
        root = across.cross(middle(0)[1]).normalize() * (k * BEAM_W / 2)
        sides.append((Part.makeLine(root, points[0]), curve.toShape()))
    (line1, curve1), (line2, curve2) = sides
    ends = [Part.makeLine(line1.Vertexes[0].Point, line2.Vertexes[0].Point),
            Part.makeLine(curve1.Vertexes[-1].Point, curve2.Vertexes[-1].Point)]  # fmt: skip
    face = Part.Face(Part.Wire(Part.__sortEdges__([line1, curve1, line2, curve2, *ends])))
    face.translate(across * (-BEAM_T / 2))
    solid = face.extrude(across * BEAM_T)
    faces = [f.toNurbs().Faces[0] if type(f.Surface).__name__ == "SurfaceOfExtrusion" else f
             for f in solid.Faces]  # fmt: skip
    shell = Part.Shell(faces)
    shell.sewShape()
    solid = Part.Solid(shell)
    if solid.Volume < 0:
        solid.reverse()
    solid.translate(App.Vector(0, -solid.BoundBox.YMin, -solid.BoundBox.ZMin))
    return solid


def lofted(tilt: float) -> Part.Shape:
    """The bar lofted through 9 square sections, each moved `BOW` × sin(πx/120) in the plane
    turned `tilt` degrees about x from its layers' plane: every face B-spline but its ends,
    its flat sides too, as a loft makes them."""
    turn = math.radians(tilt)
    up = App.Vector(0, math.cos(turn), math.sin(turn))
    wires = []
    for i in range(9):
        x = BEAM_L * i / 8
        at = App.Vector(x, 0, 0) + up * (BOW * math.sin(math.pi * i / 8))
        wires.append(Part.makePolygon([at + App.Vector(0, a, b) for a, b in
                                       ((0, 0), (BEAM_W, 0), (BEAM_W, BEAM_T), (0, BEAM_T),
                                        (0, 0))]))  # fmt: skip
    return Part.makeLoft(wires, True, False)


def bowed() -> None:
    """The bowed bars (mqucifer/print-shop#356), each held at x 0 and loaded at x 110, read
    solid and as printed: the leg bowing at 45° to its layers; the lofted bar bowing along its
    layers, whose flat B-spline sides made main's common of 16 copies empty, so it read solid
    and said nothing; and the leg bowing along its layers with a notch in its flat side, whose
    concave edges run between its B-spline faces, where main's copies left 16 steps of core
    under a bead's width by them. Where each is loaded is in `bowed-at.json`."""
    out = Path("load")
    wall = [{"plane": "x=0", "how": "fixed"}]
    notch = box((NOTCH_AT, -1, -50), (NOTCH_AT + NOTCH_LONG, NOTCH_DEEP, 100))
    shapes = {"bowed": bowed_leg(45), "bowed-up": lofted(90),
              "bowed-notched": bowed_leg(90).cut(notch)}  # fmt: skip
    at = {}
    for name, shape in shapes.items():
        save(shape, out / f"{name}.step")
        section = shape.slice(App.Vector(1, 0, 0), BOWED_AT)[0].BoundBox
        y = (section.YMin + section.YMax) / 2
        line = Part.makeCylinder(0.01, 200, App.Vector(BOWED_AT, y, -100))
        at[name] = [BOWED_AT, round(y, 4), round(shape.common(line).optimalBoundingBox().ZMax, 4)]
        for kind, extra in (("", {}), ("printed-", {"print": {**PRINT, "walls": 2, "infill": 20}})):
            row = {"name": "bar", "file": f"{name}.step", "layers": "z", **extra}
            assembly(out / f"{kind}{name}.json", [row], held=wall)
    (out / "bowed-at.json").write_text(json.dumps(at))


#: The slotted bar (mqucifer/print-shop#363): 120 by 20 by 20, held at x 0, a slot 4 wide and 12
#: deep across its top at its middle, printed at 60% grid, its core's cell 1.36 mm.
SLOT_AT, SLOT_WIDE, SLOT_DEEP = 58.0, 4.0, 12.0


def slotted() -> None:
    """The slotted bar (mqucifer/print-shop#363): loaded at its end, its core's worst cells lie
    at the slot's inside corners, far from its held face, in elements of a coarse mesh many
    times its cell, so read at the mesh's scale: where that's near its limit, it's meshed
    again finer round them (`load.refined`)."""
    out = Path("load")
    bar = box((0, 0, 0), (120, 20, 20)).cut(
        box((SLOT_AT, -1, 20 - SLOT_DEEP), (SLOT_AT + SLOT_WIDE, 21, 21)))
    save(bar, out / "slotted.step")
    row = {"name": "bar", "file": "slotted.step", "layers": "z",
           "print": {**PRINT, "walls": 2, "infill": 60}}
    assembly(out / "printed-slotted.json", [row], held=[{"plane": "x=0", "how": "fixed"}])


#: The webbed bosses (mqucifer/print-shop#363): two round bosses 12 across and 20 tall, their
#: axes 40 apart, joined by a web between them, 1.75 thick, its core 0.12 mm across between its
#: 2 walls, under what Orca's opening leaves infill in, and 2.0 thick, 0.37 mm, over it.
WEB_BOSS_R, WEB_APART, WEB_TALL, WEB_THIN, WEB_WIDE = 6.0, 40.0, 20.0, 1.75, 2.0


def webbed() -> None:
    """The webbed bosses (mqucifer/print-shop#363), each printed and solid, held on their bed
    face and pushed across the web's top at its middle."""
    out = Path("load")
    for name, web in (("web", WEB_THIN), ("web-wide", WEB_WIDE)):
        bosses = [Part.makeCylinder(WEB_BOSS_R, WEB_TALL, App.Vector(x, 0, 0))
                  for x in (0.0, WEB_APART)]  # fmt: skip
        plate = box((0, -web / 2, 0), (WEB_APART, web / 2, WEB_TALL))
        save(bosses[0].fuse([bosses[1], plate]).removeSplitter(), out / f"{name}.step")
        for kind, extra in (("", {}), ("printed-", {"print": {**PRINT, "walls": 2, "infill": 20}})):
            row = {"name": "webbed", "file": f"{name}.step", "layers": "z", **extra}
            assembly(out / f"{kind}{name}.json", [row], held=[{"plane": "z=0", "how": "fixed"}])


#: The tile (mqucifer/print-shop#363): a plate 60 x 60 and 8 thick, four holes 5.5 across
#: through it, each counterbored 10 across and 3 deep, a pocket 20 x 10 and 4 deep in its top,
#: and a boss 6 across standing in the pocket against its wall: every face upright to its
#: layers or flat in their plane, planes and cylinders, as a real tile's.
TILE_SIDE, TILE_T, TILE_HOLE_R, TILE_BORE_R, TILE_BORE_DEEP = 60.0, 8.0, 2.75, 5.0, 3.0


def tile() -> None:
    """The tile (mqucifer/print-shop#363), printed and solid, held on its bed face and pushed
    down on its top by a corner."""
    out = Path("load")
    shape = box((0, 0, 0), (TILE_SIDE, TILE_SIDE, TILE_T))
    for x, y in ((10, 10), (50, 10), (10, 50), (50, 50)):
        shape = shape.cut(Part.makeCylinder(TILE_HOLE_R, TILE_T + 2, App.Vector(x, y, -1)))
        shape = shape.cut(Part.makeCylinder(TILE_BORE_R, TILE_BORE_DEEP + 1,
                                            App.Vector(x, y, TILE_T - TILE_BORE_DEEP)))  # fmt: skip
    shape = shape.cut(box((20, 25, TILE_T - 4), (40, 35, TILE_T + 1)))
    shape = shape.fuse(Part.makeCylinder(3, 4, App.Vector(30, 25, TILE_T - 4))).removeSplitter()
    save(shape, out / "tile.step")
    printing = {"print": {**PRINT, "walls": 2, "infill": 20}}
    for kind, extra in (("", {}), ("printed-", printing)):
        row = {"name": "tile", "file": "tile.step", "layers": "z", **extra}
        assembly(out / f"{kind}tile.json", [row], held=[{"plane": "z=0", "how": "fixed"}])
    # Its top's four outer edges chamfered 1 mm, its skin on sloped faces (mqucifer/print-shop#363).
    def on_rim(edge: Any) -> bool:
        span = edge.BoundBox
        return bool(type(edge.Curve).__name__ == "Line" and span.ZMin > TILE_T - 1e-6
                    and (min(span.XMax, span.YMax) < 1e-6
                         or max(span.XMin, span.YMin) > TILE_SIDE - 1e-6))

    save(shape.makeChamfer(1.0, [e for e in shape.Edges if on_rim(e)]),
         out / "tile-chamfered.step")
    row = {"name": "tile", "file": "tile-chamfered.step", "layers": "z", **printing}
    assembly(out / "printed-tile-chamfered.json", [row],
             held=[{"plane": "z=0", "how": "fixed"}])
    # And with a solid pin bonded in each of its holes, below its counterbore: parts beside a
    # printed one, each to keep elements of its own (mqucifer/print-shop#363's real tile).
    rows: Rows = [{"name": "tile", "file": "tile.step", "layers": "z", **printing}]
    joints: Rows = []
    for k, (x, y) in enumerate(((10, 10), (50, 10), (10, 50), (50, 50)), 1):
        pin = Part.makeCylinder(TILE_HOLE_R, TILE_T - TILE_BORE_DEEP, App.Vector(x, y, 0))
        save(pin, out / f"tile-pin-{k}.step")
        rows.append({"name": f"pin{k}", "file": f"tile-pin-{k}.step", "layers": "z"})
        joints.append({"between": ["tile", f"pin{k}"], "kind": "bonded"})
    assembly(out / "printed-tile-pinned.json", rows, joints,
             [{"plane": "z=0", "how": "fixed"}])  # fmt: skip


#: The capped top (mqucifer/print-shop#346): its side and thickness, the counterbore's radius
#: and depth, and the cap's play round it, a fit's and a hair's, and its turn, in mm and degrees.
CAPPED_SIDE, CAPPED_T, CAPPED_BORE_R, CAPPED_DEEP = 40.0, 6.0, 5.0, 2.0
CAPPED_PLAY, CAPPED_HAIR, CAPPED_TURN, CAPPED_ROUND = 0.15, 2e-4, 15.0, 0.03


def capped() -> None:
    """A cap in a counterbore with play round it, touching its floor (mqucifer/print-shop#346):
    the two cylinders meet nowhere, and nor do the cap's top and the top's, a gap apart."""
    out = Path("capped")
    middle = App.Vector(CAPPED_SIDE / 2, CAPPED_SIDE / 2, CAPPED_T - CAPPED_DEEP)
    top = box((0, 0, 0), (CAPPED_SIDE, CAPPED_SIDE, CAPPED_T)).cut(
        Part.makeCylinder(CAPPED_BORE_R, CAPPED_DEEP + 1, middle)
    )
    save(top, out / "top.step")
    # The same top with one upright corner rounded 0.03 mm, as a traced frame's micro-arc is.
    corner = next(e for e in top.Edges if e.BoundBox.XMax < 1e-6 and e.BoundBox.YMax < 1e-6)
    save(top.makeFillet(CAPPED_ROUND, [corner]), out / "top-micro.step")
    parts: Rows = [{"name": "top", "file": "top.step", "layers": "z"}]
    for name, play in (("capped", CAPPED_PLAY), ("capped-hair", CAPPED_HAIR)):
        cap = Part.makeCylinder(CAPPED_BORE_R - play, CAPPED_DEEP, middle)
        cap.rotate(middle, App.Vector(0, 0, 1), CAPPED_TURN)
        save(cap, out / f"{name}.step")
        rows = [*parts, {"name": "cap", "file": f"{name}.step", "layers": "z"}]
        assembly(out / f"{name}.json", rows, [{"between": ["top", "cap"], "kind": "touching"}],
                 [{"plane": "z=0", "how": "fixed"}])  # fmt: skip
    micro = [{"name": "top", "file": "top-micro.step", "layers": "z"},
             {"name": "cap", "file": "capped.step", "layers": "z"}]  # fmt: skip
    assembly(out / "capped-micro.json", micro, [{"between": ["top", "cap"], "kind": "touching"}],
             [{"plane": "z=0", "how": "fixed"}])  # fmt: skip


def main() -> None:
    loads()
    capped()
    printed()
    bowed()
    slotted()
    webbed()
    tile()
    throughs()
    peaks()
    shears()
    channels()
    dovetails()
    plays()
    turned()
    turns()
    seam()
    large()
    stool()
    sleeved()
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
