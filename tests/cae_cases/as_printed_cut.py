"""The cut of a printed part's walls, on the bowed bars of `make_fixtures.py`
(mqucifer/print-shop#356), in the bench's second image: what main's common of 16 copies left,
and what `as_printed.cells` leaves now, by OCCT's offset and, with the offset refused, by the
copies mitred at concave edges; and that a cut no way holds is refused in words. Prints one
JSON object.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")

import FreeCAD  # noqa: E402, F401 - before Part, which stops the process loaded first
import Part  # noqa: E402

# The image's own mount of the bench, not this package: mypy can't see it.
as_printed: Any = importlib.import_module("bench.as_printed")
printed: Any = importlib.import_module("bench.printed")
NoAnswer: Any = importlib.import_module("bench.report").NoAnswer
Printed, Settings = printed.Printed, printed.Settings

#: As `make_fixtures.PRINT`, at 2 walls and 20%.
SETTINGS = Settings(walls=2, line_width=0.45, top=5, bottom=3, infill=20, pattern="grid",
                    infill_angle=0, layer_height=0.2, up="+z")  # fmt: skip
BEAD = 0.45


def read(cell: Any) -> dict[str, Any]:
    core = as_printed._one(list(cell.core))
    check = cell.check
    return {"cut": cell.cut, "planes": check.planes, "checked": check.read,
            "short": check.short, "over": check.over,
            "core_mm3": cell.core_mm3,
            "slivers": len(as_printed.slivers(core, BEAD)) if core is not None else 0}  # fmt: skip


def main() -> None:
    walls = as_printed.wall_thickness(2, 0.45, 0.2)
    found: dict[str, Any] = {}
    for name in ("bowed", "bowed-up", "bowed-notched"):
        part = Part.read(f"load/{name}.step").Solids[0]
        inside = as_printed.eroded_in_plane(part, 2, walls)
        shelled = as_printed.eroded_along(part, 2, 1.0, 0.6)
        core = inside.common(shelled) if inside is not None and shelled is not None else None
        mains = {"core_mm3": float(core.Volume) if core is not None else 0.0,
                 "slivers": len(as_printed.slivers(core, BEAD)) if core is not None else 0}
        now = read(as_printed.cells(part, Printed(settings=SETTINGS), 2, Path("."), name)[0])
        offset = as_printed.offset_in_plane

        def refused(*_: Any) -> Any:
            raise RuntimeError("as OCCT's offset of a real part was")

        as_printed.offset_in_plane = refused
        try:
            try:
                copies = read(as_printed.cells(part, Printed(settings=SETTINGS), 2, Path("."),
                                               name)[0])  # fmt: skip
            except NoAnswer as exc:  # noqa: B030 - a class from the image's bench
                copies = {"refused": str(exc)}
            agree = as_printed.AGREE
            as_printed.AGREE = -1.0
            try:
                as_printed.cells(part, Printed(settings=SETTINGS), 2, Path("."), name)
                said = ""
            except NoAnswer as exc:  # noqa: B030 - a class from the image's bench
                said = str(exc)
            finally:
                as_printed.AGREE = agree
        finally:
            as_printed.offset_in_plane = offset
        found[name] = {"main": mains, "now": now, "copies": copies, "refused": said}
    print(json.dumps(found))


if __name__ == "__main__":
    main()
