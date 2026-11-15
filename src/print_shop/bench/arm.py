"""The arm behind a place `fit-on` read `BLOCKED`, inside the second image, for the harness
(mqucifer/print-shop#150).

`fit-on` says where a part passes through another on its way on, how deep, and across which way.
`bend` reads what that does to an arm, and takes the arm as a box, the plane of its root and the
way it's pushed. A run was never made to give those, so the harness works them out here, from
the place and the part's own shape, and then calls `bend` with them as anyone could.

**How the arm is found.** The place is a box in the part's own coordinates, with the axis its
depth is across.

1. **Which way it moves.** A column through the place along that axis, cut from the part, is the
   material the place is in: the hook and the arm behind it, from its front, the face the other
   part meets, to its back. The place lies at one end of that column, the front. What's there
   moves away from what passes it, so the push is from the front toward the back. A place at
   neither end, or at both, has no way read.
2. **What it's part of.** The part between two planes, `CLEAR` mm in front of the column and
   `CLEAR` mm behind it, falls into pieces. The piece the place is in is the arm with whatever
   joins it to the rest: where that piece was cut by one of the two planes, it's joined to
   material in front of it or behind it.
3. **Its root.** Along each of the other two axes, an arm has every such join on one side of
   the place: it stands free from the last of them to its tip. The root is the plane at the end
   of the joins nearest the place, and the arm is the piece past it, in a box `CLEAR` mm clear
   of it on every side but the root. With a join level with the place, or joins on both sides of
   it, there's no arm along that axis. Of two axes that give one, the longer arm is taken.

**When it isn't found:** the depth isn't across x, y or z; the place isn't at one face of what
it's in; nothing within `CLEAR` mm behind it is clear, as with a bump on a solid wall whose
ends are held; or what's there is held at both ends. Each is said, and nothing is guessed.

It prints one line, `MARK` then JSON. It reads nothing but the file, and judges nothing: `bend`
does that.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

MARK = "ROUNDS_ARM "
AXES = ("x", "y", "z")
#: How far in front of the arm and behind it the box reaches, in mm, and past its sides and its
#: tip: an arm with less than this behind it isn't found.
CLEAR = 0.1
#: A coordinate is at another within this, in mm: `fit-on`'s places are read off a mesh at 0.01.
AT = 0.02
#: Material under this, in mm³, isn't a piece.
VOLUME = 1e-6


def _span(box: Any, axis: int) -> tuple[float, float]:
    return (
        (box.XMin, box.XMax),
        (box.YMin, box.YMax),
        (box.ZMin, box.ZMax),
    )[axis]


def _make(low: list[float], high: list[float]) -> Any:
    import FreeCAD as App
    import Part

    return Part.makeBox(*[b - a for a, b in zip(low, high, strict=True)], App.Vector(*low))


def _holding(shape: Any, place: Any) -> Any:
    """The solid of a cut shape that the place is in, or None."""
    for solid in shape.Solids:
        if solid.Volume > VOLUME and solid.common(place).Volume > VOLUME:
            return solid
    return None


def find(shape: Any, low: list[float], high: list[float], push: int) -> dict[str, Any]:
    """The arm at a place, as `bend` takes it, or why there's none."""
    whole = shape.BoundBox
    outer_low = [_span(whole, a)[0] - 1 for a in range(3)]
    outer_high = [_span(whole, a)[1] + 1 for a in range(3)]
    place = _make(low, high)
    # 1. The column of material the place is in, along the push.
    column_low, column_high = list(low), list(high)
    column_low[push], column_high[push] = outer_low[push], outer_high[push]
    column = _holding(shape.common(_make(column_low, column_high)), place)
    if column is None:
        return {"found": False, "why": "none of the file is at the place"}
    ends = _span(column.BoundBox, push)
    at_low, at_high = abs(low[push] - ends[0]) <= AT, abs(high[push] - ends[1]) <= AT
    if at_low == at_high:
        where = "runs right through" if at_low else "is inside"
        return {
            "found": False,
            "why": f"the overlap {where} what it's in along {AXES[push]}, from {ends[0]:g} to "
            f"{ends[1]:g}, so it's at no one face of it and which way it would move isn't read",
        }
    sign = 1 if at_low else -1
    # 2. The part between two planes, in front of the column and behind it.
    slab_low, slab_high = list(outer_low), list(outer_high)
    slab_low[push], slab_high[push] = ends[0] - CLEAR, ends[1] + CLEAR
    piece = _holding(shape.common(_make(slab_low, slab_high)), place)
    if piece is None:
        return {"found": False, "why": "none of the file is at the place"}
    joins = [
        face
        for face in piece.Faces
        for plane in (slab_low[push], slab_high[push])
        if all(abs(c - plane) < 1e-6 for c in _span(face.BoundBox, push))
    ]
    thick = ends[1] - ends[0]
    if not joins:
        return {
            "found": False,
            "why": f"what the overlap is in, {thick:.3f} mm thick along {AXES[push]}, joins "
            "nothing in front of it or behind it: it isn't an arm on a part",
        }
    # 3. Along each other axis: every join on one side of the place.
    found: list[dict[str, Any]] = []
    for root in (a for a in range(3) if a != push):
        spans = [_span(face.BoundBox, root) for face in joins]
        below = all(span[1] <= low[root] + AT for span in spans)
        above = all(span[0] >= high[root] - AT for span in spans)
        if below == above:
            continue
        at = max(span[1] for span in spans) if below else min(span[0] for span in spans)
        away = 1 if below else -1
        free_low, free_high = list(slab_low), list(slab_high)
        if away > 0:
            free_low[root] = at
        else:
            free_high[root] = at
        arm = _holding(piece.common(_make(free_low, free_high)), place)
        if arm is None:
            continue
        box = arm.BoundBox
        arm_low = [_span(box, a)[0] - CLEAR for a in range(3)]
        arm_high = [_span(box, a)[1] + CLEAR for a in range(3)]
        arm_low[push], arm_high[push] = slab_low[push], slab_high[push]
        if away > 0:
            arm_low[root] = at
        else:
            arm_high[root] = at
        length = _span(box, root)[1] - _span(box, root)[0]
        found.append(
            {
                "found": True,
                "arm": [[round(c, 4) for c in arm_low], [round(c, 4) for c in arm_high]],
                "root": [AXES[root], round(at, 4)],
                "toward": f"{'+' if sign > 0 else '-'}{AXES[push]}",
                "length_mm": round(length, 4),
                "thickness_mm": round(thick, 4),
                "volume_mm3": round(float(arm.Volume), 4),
            }
        )
    if not found:
        across = [AXES[a] for a in range(3) if a != push]
        return {
            "found": False,
            "why": f"what the overlap is in, {thick:.3f} mm thick along {AXES[push]}, is joined "
            f"to the rest of the part level with the overlap or on both sides of it, along "
            f"{across[0]} and along {across[1]}, with less than {CLEAR:g} mm clear behind it: "
            "nothing there stands free from a root to a tip",
        }
    return max(found, key=lambda one: one["length_mm"])


def _corners(text: str) -> tuple[list[float], list[float]]:
    values = [float(v) for v in text.split(",")]
    if len(values) != 6:
        raise argparse.ArgumentTypeError("X0,Y0,Z0,X1,Y1,Z1")
    return values[:3], values[3:]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="arm")
    parser.add_argument("file")
    parser.add_argument("--place", required=True, type=_corners)
    parser.add_argument("--across", required=True, choices=AXES)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        import FreeCAD  # noqa: F401
        import Part

        low, high = args.place
        if min(b - a for a, b in zip(low, high, strict=True)) <= 0:
            said: dict[str, Any] = {"found": False, "why": "the place has no room inside it"}
        else:
            said = find(Part.read(args.file), low, high, AXES.index(args.across))
    except Exception as exc:  # noqa: BLE001 - whatever it is, it's said, and nothing is guessed
        said = {"found": False, "why": f"the file couldn't be read for an arm: {exc}"[:400]}
    print(MARK + json.dumps(said))
