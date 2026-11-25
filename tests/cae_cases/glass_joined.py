"""A glass frame `pieces build` made, joined to a block (mqucifer/print-shop#295), in the bench's
second image: nothing here is anyone's design.

`python glass_joined.py FRAME OUT` reads the frame, its border at least 12 mm wide on its +x side,
and lays that border on a block 40 mm wide and 20 deep, the block from 17 mm inside the
frame's +x edge to 23 past it and as long as the frame, bolted down through the border by three
M5 through-bolts 6 mm in from the frame's edge. It writes, in the folder OUT,
`frame-bored.step`, `block.step`, `band.step` and two assembly files: `alone.json`, the bare
frame held by its -x edge, and `joined.json`, the bored frame on the block, held by the block's
far end. The band is the panel inside the border, the leads and lips `pieces build` made, which
`load` leaves out to read the frame as a lower bound where the whole can't be read: the panel
is from the origin, so the border is as wide as the frame reaches below it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

#: The block, the bolts' place in from the frame's edge, and an M5 bolt's hole.
WIDE, DEEP, IN, PAST, BORE = 40.0, 20.0, 6.0, 23.0, 2.75


def main() -> None:
    frame_file = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(exist_ok=True)
    frame = next(s for s in Part.read(str(frame_file)).Solids if s.Volume > 1e-6)
    box = frame.BoundBox
    edge, low, high = box.XMax, box.YMin, box.YMax
    block = Part.makeBox(WIDE, high - low, DEEP, App.Vector(edge + PAST - WIDE, low, -DEEP))
    span = high - low
    bolts = [(edge - IN, low + span * share) for share in (0.15, 0.5, 0.85)]
    bored = frame
    for x, y in bolts:
        bore = Part.makeCylinder(BORE, DEEP + 20, App.Vector(x, y, -DEEP - 5), App.Vector(0, 0, 1))
        bored, block = bored.cut(bore), block.cut(bore)
    bored.exportStep(str(out / "frame-bored.step"))
    border = -box.XMin
    band = Part.makeBox(
        edge - border, high - border, box.ZLength + 2, App.Vector(0, 0, box.ZMin - 1)
    )
    band.exportStep(str(out / "band.step"))
    block.exportStep(str(out / "block.step"))
    alone = {
        "parts": [
            {"name": "frame", "file": f"../{frame_file}", "layers": "z", "band": "band.step"}
        ],
        "held": [{"plane": f"x={box.XMin}", "how": "fixed"}],
    }
    (out / "alone.json").write_text(json.dumps(alone))
    joined = {
        "parts": [
            {"name": "frame", "file": "frame-bored.step", "layers": "z", "band": "band.step"},
            {"name": "block", "file": "block.step", "layers": "z"},
        ],
        "joints": [
            {"between": ["frame", "block"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
             "washer": "m5-washer", "at": [[x, y, box.ZMax] for x, y in bolts]}
        ],
        "held": [{"plane": f"x={edge + PAST}", "how": "fixed", "part": "block"}],
    }  # fmt: skip
    (out / "joined.json").write_text(json.dumps(joined))
    print("made", round(box.XMin, 4), round(edge, 4), round(low, 4), round(high, 4))


if __name__ == "__main__":
    main()
