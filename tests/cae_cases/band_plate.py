"""A plain frame with a panel, for `load --parts` on a frame that names its band
(mqucifer/print-shop#375), in the bench's second image: nothing here is anyone's design.

`python band_plate.py WIDTH OUT` writes `OUT`, a STEP file: a border 12 mm wide and 6 mm thick
round a panel WIDTH x 0.625 WIDTH mm and 3 mm thick, at the border's middle, the panel from the
origin as a built glass frame's is. It has no leads: two passes read it whole, where a traced
frame's are refused by their own checks. `glass_joined.py` then bores it and lays it on a block,
its panel named as its band.
"""

from __future__ import annotations

import sys

sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

#: The border's width and thickness, and the panel's thickness, in mm.
BORDER, THICK, PANEL = 12.0, 6.0, 3.0


def main() -> None:
    width = float(sys.argv[1])
    height = 0.625 * width
    outer = Part.makeBox(width + 2 * BORDER, height + 2 * BORDER, THICK,
                         App.Vector(-BORDER, -BORDER, 0))  # fmt: skip
    hole = Part.makeBox(width, height, THICK + 2, App.Vector(0, 0, -1))
    panel = Part.makeBox(width, height, PANEL, App.Vector(0, 0, (THICK - PANEL) / 2))
    frame = outer.cut(hole).fuse(panel).removeSplitter()
    solids = [s for s in frame.Solids if s.Volume > 1e-6]
    assert len(solids) == 1, len(solids)
    solids[0].exportStep(sys.argv[2])
    print("made", round(solids[0].Volume, 1))


if __name__ == "__main__":
    main()
