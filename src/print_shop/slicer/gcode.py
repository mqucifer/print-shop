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

**How long each layer takes** is read from the moves (mqucifer/print-shop#105): Orca writes only
the whole print's time, and its progress (`M73`) in whole percents. Each move runs at its `F`,
under the acceleration Orca set for it (`SET_VELOCITY_LIMIT ACCEL=`, `M204 S`), slowing at
corners as Klipper does (`SQUARE_CORNER_V`): the Centauri Carbon runs Klipper. A dwell (`G4`)
counts; a heater wait doesn't, since it can't be known from the file. The end G-code, after the
last line laid, is no layer's. The tests hold the layers to Orca's own progress, on slices Orca
wrote.
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
_ACCEL = re.compile(r"\bACCEL=(\d*\.?\d+)")
_M204 = re.compile(r"\b[SP](\d*\.?\d+)")
_DWELL = re.compile(r"\b([PS])(\d*\.?\d+)")
#: An arc is drawn as chords no longer than this, in mm, when its lines are kept.
CHORD = 0.5
#: Klipper's default `square_corner_velocity`, mm/s: how fast a 90 degree corner is taken.
SQUARE_CORNER_V = 5.0
#: The acceleration before the G-code sets one, mm/s2. Only the start G-code runs at it.
START_ACCEL = 1000.0
#: What a move that lays nothing is counted as, in a layer's times.
TRAVEL = "travel"
#: Orca's label for the outermost wall, where each loop's seam is.
OUTER_WALL = "Outer wall"


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
    #: Each feature's time, in s, by Orca's label; moves that lay nothing are `TRAVEL`.
    times: dict[str, float] = field(default_factory=dict)
    #: Each feature's area laid, in mm2: its lines' length times their width.
    areas: dict[str, float] = field(default_factory=dict)
    #: Where each outer wall loop starts on this layer, (x, y): its seam.
    seams: list[tuple[float, float]] = field(default_factory=list)

    @property
    def bottom(self) -> float:
        return round(self.z - self.height, 4)

    @property
    def time_s(self) -> float:
        """How long the layer takes, from its first move after `;LAYER_CHANGE` to the next."""
        return round(sum(self.times.values()), 3)


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


class _Moves:
    """Every move's length, speed and acceleration, to time them as the printer runs them.

    A move's speed at its ends is held by the corner it makes with its neighbours (Klipper's
    junction deviation) and by what it can reach accelerating from the one before and braking
    for the one after. A move that only extrudes or retracts stops the head.
    """

    def __init__(self) -> None:
        self.length: list[float] = []
        self.speed: list[float] = []
        self.accel: list[float] = []
        self.unit: list[tuple[float, float, float]] = []
        self.where: list[tuple[int, str]] = []  # the layer (from 0, -1 before the first), kind
        self.fixed: list[tuple[int, str, float]] = []  # dwells and moves of the filament only
        #: The moves the head starts from rest: after a dwell or a move of the filament alone.
        self.rests: set[int] = set()
        #: How many moves and still times there were when the last line was laid: the rest
        #: is the end G-code.
        self.laid = (0, 0)

    def move(self, dx: float, dy: float, dz: float, speed: float, accel: float,
             layer: int, kind: str) -> None:
        length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if length <= 0 or speed <= 0:
            return
        self.length.append(length)
        self.speed.append(speed)
        self.accel.append(max(accel, 1.0))
        self.unit.append((dx / length, dy / length, dz / length))
        self.where.append((layer, kind))

    def still(self, layer: int, kind: str, seconds: float) -> None:
        """Time with the head still: a dwell, or the filament moving alone."""
        if seconds > 0:
            self.fixed.append((layer, kind, seconds))
        self.rests.add(len(self.length))

    def times(self) -> list[float]:
        """Each move's time, in s."""
        n = len(self.length)
        rests = self.rests
        v2 = [v * v for v in self.speed]
        # The fastest each move may start at: its corner with the one before.
        start = [0.0] * (n + 1)
        for i in range(1, n):
            if i in rests:
                continue
            ax, ay, az = self.unit[i - 1]
            bx, by, bz = self.unit[i]
            cos_theta = -(ax * bx + ay * by + az * bz)
            if cos_theta > 0.999999:
                continue  # a reversal: stop
            limit = min(v2[i], v2[i - 1])
            if cos_theta < -0.999999:
                start[i] = limit  # straight on
                continue
            sin_half = math.sqrt(0.5 * (1.0 - cos_theta))
            r = sin_half / (1.0 - sin_half)
            corner = r * SQUARE_CORNER_V * SQUARE_CORNER_V * (math.sqrt(2.0) - 1.0)
            start[i] = min(limit, corner)
        # Braking for what comes next, then accelerating from what came before.
        for i in range(n - 1, -1, -1):
            start[i] = min(start[i], start[i + 1] + 2 * self.accel[i] * self.length[i])
        for i in range(n):
            start[i + 1] = min(start[i + 1], start[i] + 2 * self.accel[i] * self.length[i])
        found = []
        for i in range(n):
            a, length, v = self.accel[i], self.length[i], self.speed[i]
            s0, s1 = math.sqrt(start[i]), math.sqrt(start[i + 1])
            up = (v2[i] - start[i]) / (2 * a)
            down = (v2[i] - start[i + 1]) / (2 * a)
            if up + down <= length:
                found.append((v - s0) / a + (v - s1) / a + (length - up - down) / v)
            else:
                peak = math.sqrt((2 * a * length + start[i] + start[i + 1]) / 2)
                found.append((peak - s0) / a + (peak - s1) / a)
        return found


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
    x = y = z = 0.0
    feed = 0.0  # mm/s
    accel = START_ACCEL
    in_run = False  # laying a line that follows on from the last
    relative = False  # G91: X, Y and Z are relative
    moves = _Moves()
    pause_next = False
    in_config = False
    absolute_e = False
    last_e = 0.0
    lengths: defaultdict[str, float] = defaultdict(float)
    objects: defaultdict[str, float] = defaultdict(float)
    areas: defaultdict[str, float] = defaultdict(float)

    def close() -> None:
        if layer is not None:
            layer.lengths = {k: round(v, 3) for k, v in lengths.items()}
            layer.objects = {k: round(v, 3) for k, v in objects.items()}
            layer.areas = {k: round(v, 3) for k, v in areas.items()}

    with gcode.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            text = raw.rstrip("\n")
            if text.startswith(";"):
                if text.startswith(";LAYER_CHANGE"):
                    close()
                    lengths = defaultdict(float)
                    objects = defaultdict(float)
                    areas = defaultdict(float)
                    in_run = False
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
                    in_run = False
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
            elif word in ("G90",) or word.startswith("G90 "):
                relative = False
            elif word in ("G91",) or word.startswith("G91 "):
                relative = True
            elif word.startswith("SET_VELOCITY_LIMIT"):
                set_to = _ACCEL.search(word)
                if set_to:
                    accel = float(set_to.group(1))
            elif word.startswith("M204"):
                set_to = _M204.search(word)
                if set_to:
                    accel = float(set_to.group(1))
            elif re.match(r"G4\b", word):
                dwell = _DWELL.search(word)
                if dwell:
                    seconds = float(dwell.group(2)) / (1000 if dwell.group(1) == "P" else 1)
                    moves.still(len(layers) - 1, TRAVEL, seconds)
            if not word.startswith(("G0", "G1", "G2", "G3")):
                if word.startswith("G92"):
                    values = dict(_WORD.findall(word))
                    if "E" in values:
                        last_e = float(values["E"])
                continue
            command = word.split()[0]
            values = {k: float(v) for k, v in _WORD.findall(word)}
            if "F" in values:
                feed = values["F"] / 60
            if relative:
                nx, ny, nz = x + values.get("X", 0.0), y + values.get("Y", 0.0), z + values.get(
                    "Z", 0.0)
            else:
                nx, ny, nz = values.get("X", x), values.get("Y", y), values.get("Z", z)
            e = values.get("E")
            extruded = 0.0
            if e is not None:
                extruded = e - last_e if absolute_e else e
                if absolute_e:
                    last_e = e
            here = len(layers) - 1
            arc = command in ("G2", "G3") and ("I" in values or "J" in values)
            if nx == x and ny == y and nz == z and not arc:
                if extruded:
                    # The filament alone moves, the head still: a retraction or its undoing.
                    moves.still(here, TRAVEL, abs(extruded) / feed if feed else 0.0)
                continue
            laying = extruded > 0 and (nx != x or ny != y)
            what = kind if laying else TRAVEL
            points: list[tuple[float, float]]
            if arc:
                points = _arc(
                    x, y, nx, ny, values.get("I", 0.0), values.get("J", 0.0), command == "G2"
                )
            else:
                points = [(nx, ny)]
            if laying and kind == OUTER_WALL and not in_run and layer is not None:
                layer.seams.append((round(x, 3), round(y, 3)))
            in_run = laying
            px, py, pz = x, y, z
            for k, (qx, qy) in enumerate(points, 1):
                qz = z + (nz - z) * k / len(points)
                moves.move(qx - px, qy - py, qz - pz, feed, accel, here, what)
                if laying and layer is not None:
                    piece = Line(px, py, qx, qy, width, kind, obj)
                    lengths[kind] += piece.length
                    areas[kind] += piece.length * width
                    if obj:
                        objects[obj] += piece.length
                    if width > 0 and (kind not in layer.narrowest or width < layer.narrowest[kind]):
                        layer.narrowest[kind] = width
                    if keep_lines:
                        layer.lines.append(piece)
                px, py, pz = qx, qy, qz
            if laying:
                moves.laid = (len(moves.length), len(moves.fixed))
            x, y, z = nx, ny, nz
    close()
    _time(layers, moves)
    return Sliced(layers, pauses, changes, outlines, footer)


def _time(layers: list[Layer], moves: _Moves) -> None:
    """Each layer's time by feature, from its moves."""
    spent: list[defaultdict[str, float]] = [defaultdict(float) for _ in layers]
    moved, still = moves.laid
    for (n, what), seconds in zip(moves.where[:moved], moves.times()[:moved], strict=True):
        if n >= 0:
            spent[n][what] += seconds
    for n, what, seconds in moves.fixed[:still]:
        if n >= 0:
            spent[n][what] += seconds
    for layer, times in zip(layers, spent, strict=True):
        layer.times = {k: round(v, 3) for k, v in times.items()}


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
