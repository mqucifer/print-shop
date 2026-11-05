"""What Orca says of a slice, read from the G-code it wrote and the log it kept.

The refinement pass (mqucifer/print-shop#51) reads a slice from Orca's own words before anything
we compute: each layer's height and feature labels (`;TYPE:`), each line's width (`;WIDTH:`), the
pause and filament changes Orca wrote, the time and filament it gives for each, and the warnings
in its log. Nothing here writes G-code: it's read, never edited.

Tested facts about Orca 2.4.2's G-code, from the slices in `~/print-shop/proofs/m18-refine/
cli-probe/`:
- each layer starts `;LAYER_CHANGE`, then `;Z:` (its top) and `;HEIGHT:`;
- a pause is `;PAUSE_PRINT` and the machine's pause G-code, written at the start of the layer it
  comes before, ahead of that layer's lines;
- a filament change is `T<n>`, the extruder count from 0;
- extrusion is relative (`M83`), and with arc fitting on, lines can be `G2`/`G3` arcs;
- each object's lines sit between `EXCLUDE_OBJECT_START NAME=...` and `..._END`, and
  `EXCLUDE_OBJECT_DEFINE` gives its outline on the plate;
- the macOS CLI writes no `result.json` (Orca's code writes it on Linux only): warnings are in
  the log, `--debug 3`, as "plate N: found [NON_CRITICAL] slicing warnings: ...".
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "Layer",
    "Line",
    "Sliced",
    "Warning",
    "read",
    "warnings",
]

_WORD = re.compile(r"([XYZEIJF])(-?\d*\.?\d+)")
_DEFINE = re.compile(r"EXCLUDE_OBJECT_DEFINE NAME=(\S+).*?POLYGON=(\[\[.*\]\])")
_NAME = re.compile(r"EXCLUDE_OBJECT_(START|END) NAME=(\S+)")
#: An arc is drawn as chords no longer than this, in mm, when its lines are kept.
CHORD = 0.5


@dataclass(frozen=True)
class Line:
    """One extruded line, (x0, y0) to (x1, y1) on the plate: its width and what Orca calls it."""

    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    kind: str
    obj: str

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Layer:
    """A layer as Orca made it, counted from 1. `z` is its top, as `;Z:` says."""

    n: int
    z: float
    height: float
    #: Each feature's extruded length, in mm, by Orca's label.
    lengths: dict[str, float] = field(default_factory=dict)
    #: Each feature's narrowest line, in mm.
    narrowest: dict[str, float] = field(default_factory=dict)
    #: Each object's extruded length on this layer.
    objects: dict[str, float] = field(default_factory=dict)
    lines: list[Line] = field(default_factory=list)

    @property
    def bottom(self) -> float:
        return round(self.z - self.height, 4)


@dataclass
class Sliced:
    """One G-code, read."""

    layers: list[Layer]
    #: Each pause: the layer it comes before (from 1), and that layer's top.
    pauses: list[dict[str, Any]]
    #: Each filament change: the layer it's on and the extruder it changes to (from 1).
    changes: list[dict[str, Any]]
    #: Each object's outline on the plate, as Orca defined it.
    outlines: dict[str, list[list[float]]]
    #: What the footer says: per-filament use, time, and the settings Orca sliced with.
    footer: dict[str, str]

    def per_filament(self, key: str) -> list[float]:
        """A footer figure for each filament, as `filament used [g]`."""
        raw = self.footer.get(key, "")
        found = []
        for part in raw.split(","):
            try:
                found.append(float(part.strip()))
            except ValueError:
                continue
        return found

    @property
    def time_s(self) -> int | None:
        from print_shop.slicer.orca import parse_duration  # noqa: PLC0415 - a cycle otherwise

        raw = self.footer.get("estimated printing time (normal mode)")
        try:
            return parse_duration(raw) if raw else None
        except ValueError:
            return None

    def layer_at(self, n: int) -> Layer | None:
        return self.layers[n - 1] if 0 < n <= len(self.layers) else None


@dataclass(frozen=True)
class Warning:
    """One warning from Orca's log, in its words."""

    plate: int | None
    level: str
    text: str


def _arc(
    x0: float, y0: float, x1: float, y1: float, i: float, j: float, clockwise: bool
) -> list[tuple[float, float]]:
    """An arc's points, after its start, as chords no longer than `CHORD`."""
    cx, cy = x0 + i, y0 + j
    radius = math.hypot(i, j)
    start = math.atan2(y0 - cy, x0 - cx)
    end = math.atan2(y1 - cy, x1 - cx)
    sweep = end - start
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    elif not clockwise and sweep <= 0:
        sweep += 2 * math.pi
    steps = max(1, math.ceil(abs(sweep) * radius / CHORD))
    points = [
        (
            cx + radius * math.cos(start + sweep * k / steps),
            cy + radius * math.sin(start + sweep * k / steps),
        )
        for k in range(1, steps)
    ]
    return [*points, (x1, y1)]


def read(gcode: Path, keep_lines: bool = False) -> Sliced:
    """A G-code as Orca wrote it: layers, labels, widths, pauses, filament changes, the footer.

    `keep_lines` keeps every extruded line, for reading where on the plate they fall.
    """
    layers: list[Layer] = []
    pauses: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    outlines: dict[str, list[list[float]]] = {}
    footer: dict[str, str] = {}
    layer: Layer | None = None
    kind = ""
    width = 0.0
    obj = ""
    x = y = 0.0
    pause_next = False
    in_config = False
    absolute_e = False
    last_e = 0.0
    lengths: defaultdict[str, float] = defaultdict(float)
    objects: defaultdict[str, float] = defaultdict(float)

    def close() -> None:
        if layer is not None:
            layer.lengths = {k: round(v, 3) for k, v in lengths.items()}
            layer.objects = {k: round(v, 3) for k, v in objects.items()}

    with gcode.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            text = raw.rstrip("\n")
            if text.startswith(";"):
                if text.startswith(";LAYER_CHANGE"):
                    close()
                    lengths = defaultdict(float)
                    objects = defaultdict(float)
                    layer = Layer(len(layers) + 1, 0.0, 0.0)
                    layers.append(layer)
                elif text.startswith(";Z:") and layer is not None:
                    layer.z = float(text[3:])
                elif text.startswith(";HEIGHT:") and layer is not None and not layer.height:
                    # The first after `;Z:` is the layer's. Orca says it again inside a layer for
                    # a line laid at another height, as a thick bridge is.
                    layer.height = float(text[8:])
                elif text.startswith(";TYPE:"):
                    kind = text[6:].strip()
                elif text.startswith(";WIDTH:"):
                    width = float(text[7:])
                elif text.startswith(";PAUSE_PRINT"):
                    pause_next = True
                elif text == "; CONFIG_BLOCK_START":
                    in_config = True
                elif text == "; CONFIG_BLOCK_END":
                    in_config = False
                elif text.startswith("; ") and " = " in text:
                    key, _, value = text[2:].partition(" = ")
                    if in_config:
                        footer.setdefault(key.strip(), value.strip().strip('"'))
                    else:
                        footer[key.strip()] = value.strip()
                continue
            if pause_next and layer is not None:
                pauses.append({"before_layer": layer.n, "z": layer.z, "after_z": layer.bottom})
                pause_next = False
            word = text.split(";", 1)[0].strip()
            if not word:
                continue
            if word.startswith("EXCLUDE_OBJECT_DEFINE"):
                found = _DEFINE.search(word)
                if found:
                    coords = [float(v) for v in re.findall(r"-?\d+\.?\d*", found.group(2))]
                    outlines[found.group(1)] = [
                        [coords[k], coords[k + 1]] for k in range(0, len(coords) - 1, 2)
                    ]
                continue
            named = _NAME.match(word)
            if named:
                obj = named.group(2) if named.group(1) == "START" else ""
                continue
            if word in ("M82",):
                absolute_e = True
            elif word in ("M83",):
                absolute_e = False
            elif re.fullmatch(r"T\d+", word) and layer is not None:
                changes.append({"layer": layer.n, "z": layer.z, "to": int(word[1:]) + 1})
            if not word.startswith(("G0", "G1", "G2", "G3")):
                if word.startswith("G92"):
                    values = dict(_WORD.findall(word))
                    if "E" in values:
                        last_e = float(values["E"])
                continue
            command = word.split()[0]
            values = {k: float(v) for k, v in _WORD.findall(word)}
            nx, ny = values.get("X", x), values.get("Y", y)
            e = values.get("E")
            extruded = 0.0
            if e is not None:
                extruded = e - last_e if absolute_e else e
                if absolute_e:
                    last_e = e
            if extruded > 0 and layer is not None and (nx != x or ny != y):
                points: list[tuple[float, float]]
                if command in ("G2", "G3") and ("I" in values or "J" in values):
                    points = _arc(
                        x, y, nx, ny, values.get("I", 0.0), values.get("J", 0.0), command == "G2"
                    )
                else:
                    points = [(nx, ny)]
                px, py = x, y
                for qx, qy in points:
                    piece = Line(px, py, qx, qy, width, kind, obj)
                    lengths[kind] += piece.length
                    if obj:
                        objects[obj] += piece.length
                    if width > 0 and (kind not in layer.narrowest or width < layer.narrowest[kind]):
                        layer.narrowest[kind] = width
                    if keep_lines:
                        layer.lines.append(piece)
                    px, py = qx, qy
            x, y = nx, ny
    close()
    return Sliced(layers, pauses, changes, outlines, footer)


_WARNED = re.compile(r"plate (\d+): found (NON_CRITICAL )?slicing warnings: (.*)")
_OTHER = (
    re.compile(r"got warnings: (.*)"),
    re.compile(r"(gcode path conflicts found between .*)"),
    re.compile(r"(found slicing result conflict)"),
    re.compile(r"got error when validate: (.*)"),
)


def warnings(log: str) -> list[Warning]:
    """Orca's warnings from its log, each once, in its own words."""
    found: list[Warning] = []
    seen: set[str] = set()
    for line in log.splitlines():
        hit = _WARNED.search(line)
        if hit:
            text = hit.group(3).strip()
            level = "non_critical" if hit.group(2) else "warning"
            item = Warning(int(hit.group(1)), level, text)
        else:
            item = None
            for pattern in _OTHER:
                other = pattern.search(line)
                if other:
                    level = "error" if "error" in pattern.pattern else "warning"
                    item = Warning(None, level, other.group(1).strip())
                    break
        if item is not None and item.text not in seen:
            seen.add(item.text)
            found.append(item)
    return found
