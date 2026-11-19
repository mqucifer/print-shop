"""A piece map made here, run in the bench's image: two fans of straight leads that cross at
angles down to about 20 degrees, and three round ones through them, on a 150 x 100 mm panel
(mqucifer/print-shop#180). Its pieces have sharp tips, which `pieces build` cuts back and rounds
by half a nozzle, so many a straight side meets a small arc, and three colours share its 55
panes. Built eight times before mqucifer/print-shop#180, its frame and one pane file each came
out in two versions of their STEP data.

    python lattice_map.py lattice.json
"""

from __future__ import annotations

import json
import math
import sys

import shapely
import shapely.affinity
from shapely.ops import unary_union

WIDTH, HEIGHT, LEAD = 120.0, 80.0, 1.0
#: The drawing is made larger by this, the lead kept: so the bench's answer to meshing the frame
#: and its panes runs past what the harness keeps of a container's output.
SCALE = 1.25
COLOURS = ("amber", "teal", "rose")


def fan(hub: tuple[float, float], angles: range) -> list[shapely.LineString]:
    lines = []
    for a in angles:
        way = (math.cos(math.radians(a)), math.sin(math.radians(a)))
        lines.append(shapely.LineString([hub, (hub[0] + 300 * way[0], hub[1] + 300 * way[1])]))
    return lines


def lattice() -> dict[str, object]:
    lines = [*fan((-30.0, -10.0), range(8, 60, 9)), *fan((150.0, -10.0), range(120, 175, 9))]
    for x, y, r in ((60.0, 40.0, 18.0), (22.0, 52.0, 13.0), (98.0, 52.0, 13.0)):
        lines.append(shapely.Point(x, y).buffer(r, quad_segs=64).exterior)
    drawn = shapely.affinity.scale(unary_union(lines), SCALE, SCALE, origin=(0, 0))
    lead = drawn.buffer(LEAD / 2, quad_segs=16)
    glass = shapely.box(0, 0, WIDTH * SCALE, HEIGHT * SCALE).difference(lead)
    parts = sorted(
        (p for p in getattr(glass, "geoms", [glass]) if p.area > 25.0),
        key=lambda p: (round(p.centroid.x), round(p.centroid.y)),
    )
    pieces = [
        {
            "id": i + 1,
            "colour": COLOURS[i % len(COLOURS)],
            "outline": [[round(x, 4), round(y, 4)] for x, y in p.exterior.coords[:-1]],
        }
        for i, p in enumerate(parts)
    ]
    return {
        "piece_map": 1,
        "units": "mm",
        "panel": {"width_mm": WIDTH * SCALE, "height_mm": HEIGHT * SCALE},
        "lead_mm": LEAD,
        "colours": {c: {} for c in COLOURS},
        "pieces": pieces,
    }


if __name__ == "__main__":
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        json.dump(lattice(), fh)
