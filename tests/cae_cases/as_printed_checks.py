"""The layers' own 2D reading that a part read as printed is classified by and checked against
(`as_printed.layer_core`, mqucifer/print-shop#356 and mqucifer/print-shop#363), in the bench's
second image. Prints one JSON object.

- `fin`: a block 10 x 10 x 40 and a fin 1.0 thick beside it, joined by a base 2 tall, at 2
  walls: its layer at z 20 is the block's core alone, as Orca slices it, the fin thinner than
  its walls, though OCCT's 2D offset of the two together fails.
- `axes`: the tile of `make_fixtures.tile`, its top's outer edges chamfered, its layers along
  each of its own axes, either way up: its middle layer's core is the same in each. OCCT's 2D
  offset goes by a face's own way round, and a section across a part's own y came the other
  way, so its walls were offset out, not in, and every element read far over its layers.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402 - before Part, which stops the process loaded first
import Part  # noqa: E402

# The image's own mount of the bench, not this package: mypy can't see it.
as_printed: Any = importlib.import_module("bench.as_printed")
WALLS = as_printed.wall_thickness(2, 0.45, 0.2)


def box(low: tuple[float, ...], high: tuple[float, ...]) -> Any:
    return Part.makeBox(*(b - a for a, b in zip(low, high, strict=True)), App.Vector(*low))


def area(layer: Any) -> float:
    """A layer's core's area in mm², 0 where it has none."""
    if layer is None or layer[0] is None:
        return 0.0
    return float(layer[0] if isinstance(layer[0], float) else layer[0].Area)


def main() -> None:
    found: dict[str, Any] = {}
    fin = box((0, 0, 0), (20, 10, 2)).fuse([box((0, 0, 2), (10, 10, 40)),
                                            box((15, 0, 2), (16, 10, 40))]).removeSplitter()
    found["fin"] = {"layer_mm2": area(as_printed.layer_core(fin, 2, 20.0, WALLS, 1.0, 0.6, 1.0)),
                    "true_mm2": (10 - 2 * WALLS) ** 2}
    # The tile of `make_fixtures.tile`, its top's four outer edges chamfered 1 mm.
    plate = Part.read("load/tile.step").Solids[0]

    def on_rim(edge: Any) -> bool:
        box_ = edge.BoundBox
        return bool(type(edge.Curve).__name__ == "Line" and box_.ZMin > 8 - 1e-6
                    and (min(box_.XMax, box_.YMax) < 1e-6 or max(box_.XMin, box_.YMin) > 60 - 1e-6))

    plate = plate.makeChamfer(1.0, [e for e in plate.Edges if on_rim(e)])
    axes = {}
    poses = ((2, None), (0, (App.Vector(0, 1, 0), 90)), (1, (App.Vector(1, 0, 0), -90)))
    for layers, turn in poses:
        posed = plate.copy()
        if turn is not None:
            posed.rotate(App.Vector(0, 0, 0), *turn)
        span = posed.BoundBox
        middle = ((span.XMin, span.YMin, span.ZMin)[layers]
                  + (span.XMax, span.YMax, span.ZMax)[layers]) / 2
        whole = posed.common(box(*[tuple(
            (middle - 0.05 if i == layers else -500.0) for i in range(3)), tuple(
            (middle + 0.05 if i == layers else 500.0) for i in range(3))])).Volume / 0.1
        for up in (1.0, -1.0):
            core = as_printed.layer_core(posed, layers, middle, WALLS, 1.0, 0.6, up)
            axes[f"{'xyz'[layers]}{'+' if up > 0 else '-'}"] = {
                "core_mm2": area(core), "section_mm2": whole}
    found["axes"] = axes
    print(json.dumps(found))


if __name__ == "__main__":
    main()
