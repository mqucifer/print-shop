"""The cut's own checks held to what they promise (mqucifer/print-shop#356's review), in the
bench's second image: each case a cut made wrong on purpose, by `as_printed`'s ways replaced,
and whether `as_printed.cells` takes it or refuses it. Prints one JSON object.

- `fin`: a block 10 x 10 x 40 and a fin 1.0 thick beside it, joined by a base 2 tall under its
  layers' lowest checked plane, at 2 walls: each layer's core is the block's alone, as the fin
  is thinner than its walls. Its layer at z 20, as Orca slices it; with the cut empty, as an
  offset that left nothing would be; with only the 16 copies, whose core meets the base's top
  where the block stands on it; and as it's cut, by its slabs.
- `shifted`: a bar's core moved 0.5 mm across, its area each layer the same.
- `short` and `over`: a bar's core cut 4% of its walls further in, and 4% less far.
- `ledge`: a bar with a ledge at z 6, its core missing a slab 0.8 tall round the ledge's height,
  between the 7 planes spread over its height.
- `unread`: a bar whose layers' cores can be read at one plane only.
- `outside`: a bar 40 tall's core cut from the bar with a lump on it, as a simpler shape made
  bigger, the lump's own core between two of the 7 planes spread over its height.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD as App  # noqa: E402 - before Part, which stops the process loaded first
import Part  # noqa: E402

# The image's own mount of the bench, not this package: mypy can't see it.
as_printed: Any = importlib.import_module("bench.as_printed")
printed: Any = importlib.import_module("bench.printed")
NoAnswer: Any = importlib.import_module("bench.report").NoAnswer
Printed, Settings = printed.Printed, printed.Settings

SETTINGS = Settings(walls=2, line_width=0.45, top=5, bottom=3, infill=20, pattern="grid",
                    infill_angle=0, layer_height=0.2, up="+z")  # fmt: skip
WALLS = as_printed.wall_thickness(2, 0.45, 0.2)


def box(low: tuple[float, ...], high: tuple[float, ...]) -> Any:
    return Part.makeBox(*(b - a for a, b in zip(low, high, strict=True)), App.Vector(*low))


def read(part: Any, **replaced: Any) -> dict[str, Any]:
    """`cells` on `part` with `as_printed`'s functions `replaced`: its core, or its refusal."""
    kept = {name: getattr(as_printed, name) for name in replaced}
    for name, given in replaced.items():
        setattr(as_printed, name, given)
    try:
        cell = as_printed.cells(part, Printed(settings=SETTINGS), 2, Path("."), "test")[0]
        return {"core_mm3": cell.core_mm3, "cut": cell.cut}
    except NoAnswer as exc:  # noqa: B030 - a class from the image's bench
        return {"refused": str(exc)}
    finally:
        for name, given in kept.items():
            setattr(as_printed, name, given)


def failed(*_: Any) -> Any:
    raise RuntimeError("as OCCT's offset of a real part was")


def nothing(*_: Any) -> Any:
    return None


def main() -> None:
    found: dict[str, Any] = {}
    fin = box((0, 0, 0), (20, 10, 2)).fuse([box((0, 0, 2), (10, 10, 40)),
                                            box((15, 0, 2), (16, 10, 40))]).removeSplitter()
    layer = as_printed.layer_core(fin, 2, 20.0, WALLS, 1.0, 0.6, 1.0)
    area = layer[0] if isinstance(layer[0], float) else layer[0].Area
    found["fin"] = {
        "layer_mm2": area,
        "true_mm2": (10 - 2 * WALLS) ** 2,
        "empty": read(fin, slab_core=nothing, offset_in_plane=nothing, eroded_in_plane=nothing),
        "copies": read(fin, slab_core=failed, offset_in_plane=failed),
        "read": read(fin),
    }
    bar = box((0, 0, 0), (60, 20, 14))
    right = as_printed.offset_in_plane

    def moved(shape: Any, layers: int, by: float) -> Any:
        return right(shape, layers, by).translated(App.Vector(0, 0.5, 0))

    def further(share: float) -> Any:
        def cut(shape: Any, layers: int, by: float) -> Any:
            return right(shape, layers, by * share)
        return cut

    slabs = as_printed.slab_core

    def slabs_moved(part: Any, layers: int, walls: float, *rest: Any) -> Any:
        return slabs(part, layers, walls, *rest).translated(App.Vector(0, 0.5, 0))

    def slabs_further(share: float) -> Any:
        def cut(part: Any, layers: int, walls: float, top: float, bottom: float, up: float,
                margin: float) -> Any:
            return slabs(part, layers, walls * share, top, bottom, up, margin * share)
        return cut

    # Each wrong cut given as the first way a bar's tried, its slabs (mqucifer/print-shop#363),
    # and as the offset after it.
    found["shifted"] = read(bar, slab_core=slabs_moved, offset_in_plane=moved)
    found["short"] = read(bar, slab_core=slabs_further(1.04), offset_in_plane=further(1.04))
    found["over"] = read(bar, slab_core=slabs_further(0.96), offset_in_plane=further(0.96))
    ledge = box((0, 0, 0), (60, 20, 6)).fuse(box((0, 0, 0), (30, 20, 14))).removeSplitter()
    slab = box((-1, -1, 5.6), (61, 21, 6.4))

    def slab_missing(shape: Any, layers: int, by: float) -> Any:
        return right(shape, layers, by).cut(slab)

    def slabs_missing(*given: Any) -> Any:
        return slabs(*given).cut(slab)

    found["ledge"] = read(ledge, slab_core=slabs_missing, offset_in_plane=slab_missing)
    calls = [0]
    layer_core = as_printed.layer_core

    def once(*given: Any) -> Any:
        calls[0] += 1
        if calls[0] > 1:
            raise RuntimeError("as a plane OCCT can't section")
        return layer_core(*given)

    found["unread"] = read(bar, layer_core=once)
    tall = box((0, 0, 0), (60, 20, 40))
    lump = tall.fuse(box((20, 20, 10), (40, 23, 14))).removeSplitter()
    said: list[str] = []
    cut = as_printed._cut_from(tall, lump, "", 2, WALLS, 1.0, 0.6, 1.0, 0.45, said)
    shells = as_printed.eroded_along(tall, 2, 1.0, 0.6, 1.0)
    found["outside"] = {
        "outside_mm3": cut.core.cut(shells).Volume if cut is not None and cut.core else 0.0,
        "cut": None if cut is None else cut.how,
        "said": said,
    }
    print(json.dumps(found))


if __name__ == "__main__":
    main()
