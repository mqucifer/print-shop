"""A banded frame hung from a block two ways at once, a loop, for a lift read without its band
(the operator's review of mqucifer/print-shop#376), in the bench's second image: nothing here is
anyone's design.

`python band_loop.py OUT` writes, in the folder OUT, `block.step`, `frame.step`, `arm.step`,
`post.step`, `band.step` and `loop.json`. The block is 20 mm square and 80 long; the frame, a
plate 100 x 80 x 6 mm, meets its +x face along its edge; an arm lies on the block's top and
reaches out over the frame, and a post stands on the frame's far border under the arm's end. So
the frame hangs from the block directly, and through the post and the arm: every joint is in the
loop, and statics fixes none of them. The band is the plate's middle, inside a 12 mm border, as a
glass frame's panel is. Every joint is bonded, so the verdict is `UNREAD`: what's read is each
joint's force.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

#: The block's side and length, the frame's length, thickness and border, in mm.
SIDE, LONG, FRAME, THICK, BORDER = 20.0, 80.0, 100.0, 6.0, 12.0
#: The arm's width, round the middle, and its thickness, and the post's length along x.
WIDE, ARM, POST = 10.0, 6.0, 12.0


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(exist_ok=True)
    far = SIDE + FRAME
    low, high = LONG / 2 - WIDE / 2, LONG / 2 + WIDE / 2
    shapes = {
        "block": Part.makeBox(SIDE, LONG, SIDE),
        "frame": Part.makeBox(FRAME, LONG, THICK, App.Vector(SIDE, 0, 0)),
        "arm": Part.makeBox(far, WIDE, ARM, App.Vector(0, low, SIDE)),
        "post": Part.makeBox(POST, WIDE, SIDE - THICK, App.Vector(far - POST, low, THICK)),
        "band": Part.makeBox(
            FRAME - 2 * BORDER, LONG - 2 * BORDER, THICK + 2,
            App.Vector(SIDE + BORDER, BORDER, -1),
        ),
    }  # fmt: skip
    for name, shape in shapes.items():
        shape.exportStep(str(out / f"{name}.step"))
    loop = {
        "parts": [
            {"name": "block", "file": "block.step", "layers": "z"},
            {"name": "frame", "file": "frame.step", "layers": "z", "band": "band.step"},
            {"name": "arm", "file": "arm.step", "layers": "z"},
            {"name": "post", "file": "post.step", "layers": "z"},
        ],
        "joints": [
            {"between": ["frame", "block"], "kind": "bonded"},
            {"between": ["arm", "block"], "kind": "bonded"},
            {"between": ["post", "arm"], "kind": "bonded"},
            {"between": ["post", "frame"], "kind": "bonded"},
        ],
        "held": [{"plane": "x=0", "how": "fixed", "part": "block"}],
    }
    (out / "loop.json").write_text(json.dumps(loop))
    print("made", low, high)


if __name__ == "__main__":
    main()
