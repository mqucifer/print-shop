"""Two blocks bolted through one M5 bore, the upper with a boss round the bolt on its top, made up
in code (mqucifer/print-shop#301, the operator's cold review): what the first of two passes does to
each round face on the bolt's axis. Run in the second image; prints one line of JSON."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, "/opt/bench")

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

# The bench as the container mounts it, `bench`, not as this repository installs it.
load: Any = importlib.import_module("bench.load")
load_parts: Any = importlib.import_module("bench.load_parts")
read: Any = importlib.import_module("bench.assembly").read

#: The bore's radius, the boss's, and where the bolt is.
BORE, BOSS, AXIS = 2.75, 6.0, (20.0, 20.0)


def main() -> None:
    out = Path("boss")
    out.mkdir(exist_ok=True)
    bore = Part.makeCylinder(BORE, 30, App.Vector(*AXIS, -12), App.Vector(0, 0, 1))
    lower = Part.makeBox(40, 40, 10, App.Vector(0, 0, -10)).cut(bore)
    boss = Part.makeCylinder(BOSS, 3, App.Vector(*AXIS, 10), App.Vector(0, 0, 1))
    upper = Part.makeBox(40, 40, 10).fuse(boss).removeSplitter().cut(bore)
    lower.exportStep(str(out / "lower.step"))
    upper.exportStep(str(out / "upper.step"))
    assembly = {
        "parts": [{"name": "upper", "file": "upper.step", "layers": "z"},
                  {"name": "lower", "file": "lower.step", "layers": "z"}],
        "joints": [{"between": ["upper", "lower"], "kind": "through", "bolt": "m5-8.8",
                    "nut": "m5-nut", "washer": "m5-washer", "at": [[*AXIS, 0]]}],
        "held": [{"plane": "z=-10", "how": "fixed"}],
    }  # fmt: skip
    (out / "boss.json").write_text(json.dumps(assembly))
    found = read(str(out / "boss.json"))
    placed = load._placed(found)
    square, count, kept = load_parts.squared(found, placed)
    axis = Part.makeLine(App.Vector(*AXIS, -10), App.Vector(*AXIS, 13))
    print(json.dumps({
        "squared": count,
        "kept": kept,
        "radii": {p.name: sorted(round(f.Surface.Radius, 3) for f in s.Faces
                                 if type(f.Surface).__name__ == "Cylinder")
                  for p, s in zip(found.parts, square, strict=True)},
        "from_axis": {p.name: s.distToShape(axis)[0]
                      for p, s in zip(found.parts, square, strict=True)},
        "volumes": {p.name: [a.Volume, b.Volume]
                    for p, a, b in zip(found.parts, placed, square, strict=True)},
    }))  # fmt: skip


if __name__ == "__main__":
    main()
