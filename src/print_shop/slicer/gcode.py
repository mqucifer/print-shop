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
from typing import Any, NamedTuple

import numpy as np

__all__ = [
    "Built",
    "Grid",
    "Layer",
    "Line",
    "Move",
    "Sliced",
    "Warning",
    "built",
    "loops",
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


class Move(NamedTuple):
    """One straight move of the nozzle, its ends in mm, on the layer it's made in (counted from 1;
    0 before the first). `width` is the line's where it lays one, else 0."""

    layer: int
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    width: float
    laying: bool


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
    #: Every move of the nozzle in order, the end G-code's too, where `read` was asked to keep
    #: them (`keep_moves`).
    head: list[Move] = field(default_factory=list)

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


def read(gcode: Path, keep_lines: bool = False, keep_moves: bool = False) -> Sliced:
    """A G-code as Orca wrote it: layers, labels, widths, pauses, filament changes, the footer.

    `keep_lines` keeps every extruded line, for reading where on the plate they fall, and
    `keep_moves` every move of the nozzle, for reading what the head passes. Nothing here assumes
    z only rises: printed by object, it starts again at each object (mqucifer/print-shop#109).
    """
    head: list[Move] = []
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
                if keep_moves:
                    head.append(Move(here + 1, px, py, pz, qx, qy, qz,
                                     width if laying else 0.0, laying))
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
    return Sliced(layers, pauses, changes, outlines, footer, head)


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


# --------------------------------------------------------------------------
# What Orca built inside a region (mqucifer/print-shop#338)
# --------------------------------------------------------------------------

#: Orca's labels for walls, for sparse infill, and for what fills a layer solid.
WALLS = ("Outer wall", "Inner wall")
SPARSE = "Sparse infill"
SOLID = ("Internal solid infill", "Top surface", "Bottom surface", "Internal Bridge", "Bridge",
         "Gap infill")
#: A raster's cell, in mm, unless the region is so big that more than `CELLS` would be needed.
CELL = 0.1
CELLS = 600_000
#: A layer is solid where its solid lines cover this much of what's inside its walls.
SOLID_SHARE = 0.8
#: Walls are counted along a ray from the outer wall inward, as far as this, in mm.
REACH = 8.0
#: Two walls are neighbours where the ray meets the next within this many line widths.
NEXT_WALL = 1.6
#: Samples are taken this far apart along each line, in mm.
STEP = 0.05


@dataclass(frozen=True)
class Grid:
    """A raster of the plate: its lower corner, its cell and its size in cells."""

    x0: float
    y0: float
    cell: float
    nx: int
    ny: int

    @classmethod
    def round(cls, x0: float, y0: float, x1: float, y1: float, margin: float = 1.0) -> Grid:
        """A grid covering a rectangle and `margin` round it, as fine as `CELL` allows."""
        width, depth = x1 - x0 + 2 * margin, y1 - y0 + 2 * margin
        cell = max(CELL, math.sqrt(max(width * depth, 0.0) / CELLS))
        return cls(x0 - margin, y0 - margin, cell, max(1, math.ceil(width / cell)),
                   max(1, math.ceil(depth / cell)))

    def empty(self) -> np.ndarray:
        return np.zeros((self.ny, self.nx), dtype=bool)

    def cells(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Each point's row and column, and whether it's on the grid."""
        col = np.floor((x - self.x0) / self.cell).astype(np.int64)
        row = np.floor((y - self.y0) / self.cell).astype(np.int64)
        on = (col >= 0) & (col < self.nx) & (row >= 0) & (row < self.ny)
        return row, col, on

    @property
    def cell_mm2(self) -> float:
        return self.cell * self.cell

    def fill(self, segments: np.ndarray) -> np.ndarray:
        """What closed loops of segments, (x0, y0, x1, y1) a row, hold, even-odd: a part's
        outline less its holes, or a shape's section."""
        found = self.empty()
        if len(segments) == 0:
            return found
        seg = np.asarray(segments, dtype=float)
        ys = self.y0 + (np.arange(self.ny) + 0.5) * self.cell
        xs = self.x0 + (np.arange(self.nx) + 0.5) * self.cell
        ya, yb = seg[:, 1], seg[:, 3]
        lo, hi = np.minimum(ya, yb), np.maximum(ya, yb)
        for r, y in enumerate(ys):
            crossing = (lo <= y) & (hi > y)
            if not crossing.any():
                continue
            s = seg[crossing]
            t = (y - s[:, 1]) / (s[:, 3] - s[:, 1])
            at = np.sort(s[:, 0] + t * (s[:, 2] - s[:, 0]))
            count = np.searchsorted(at, xs, side="right")
            found[r] = count % 2 == 1
        return found

    def stamp(self, samples: np.ndarray, half_width: float) -> np.ndarray:
        """The cells within `half_width` of any sample point, (x, y) a row."""
        found = self.empty()
        if len(samples) == 0:
            return found
        reach = max(0, math.ceil(half_width / self.cell))
        d = np.arange(-reach, reach + 1)
        dr, dc = np.meshgrid(d, d, indexing="ij")
        near = (dr * dr + dc * dc) * self.cell_mm2 <= half_width * half_width + 1e-12
        dr, dc = dr[near], dc[near]
        row, col, _ = self.cells(samples[:, 0], samples[:, 1])
        rows = (row[:, None] + dr[None, :]).ravel()
        cols = (col[:, None] + dc[None, :]).ravel()
        keep = (rows >= 0) & (rows < self.ny) & (cols >= 0) & (cols < self.nx)
        found[rows[keep], cols[keep]] = True
        return found


def _inner(mask: np.ndarray) -> np.ndarray:
    """The cells of `mask` whose four neighbours are in it too."""
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    inner[0, :] = inner[-1, :] = False
    inner[:, 0] = inner[:, -1] = False
    return inner


def _samples(lines: list[Line]) -> tuple[np.ndarray, np.ndarray]:
    """Points every `STEP` along lines, with each one's line: (x, y) and its index a row."""
    if not lines:
        return np.zeros((0, 2)), np.zeros(0, dtype=np.int64)
    ends = np.array([(ln.x0, ln.y0, ln.x1, ln.y1) for ln in lines], dtype=float)
    lengths = np.hypot(ends[:, 2] - ends[:, 0], ends[:, 3] - ends[:, 1])
    counts = np.maximum(1, np.ceil(lengths / STEP).astype(np.int64))
    which = np.repeat(np.arange(len(lines)), counts)
    first = np.repeat(np.cumsum(counts) - counts, counts)
    t = (np.arange(len(which)) - first + 0.5) / counts[which]
    x = ends[which, 0] + t * (ends[which, 2] - ends[which, 0])
    y = ends[which, 1] + t * (ends[which, 3] - ends[which, 1])
    return np.column_stack([x, y]), which


def loops(lines: list[Line], kind: str = OUTER_WALL) -> np.ndarray:
    """Each run of `kind` lines laid one after another, closed: the outer walls' loops round the
    outline and each hole, as segments, (x0, y0, x1, y1) a row."""
    found: list[tuple[float, float, float, float]] = []
    run: list[Line] = []

    def close() -> None:
        if run:
            found.extend((ln.x0, ln.y0, ln.x1, ln.y1) for ln in run)
            found.append((run[-1].x1, run[-1].y1, run[0].x0, run[0].y0))

    for line in lines:
        if line.kind != kind:
            continue
        if run and (abs(line.x0 - run[-1].x1) > 1e-6 or abs(line.y0 - run[-1].y1) > 1e-6):
            close()
            run = []
        run.append(line)
    close()
    return np.array(found, dtype=float).reshape(-1, 4)


@dataclass
class Built:
    """What Orca laid on one layer inside a region, read from its lines."""

    n: int
    z: float
    height: float
    #: The part's area inside the region on this layer, and of that, what's inside its walls.
    area_mm2: float
    core_mm2: float
    #: How much of what's inside the walls solid lines cover, 0 to 1.
    solid: float
    #: The sparse infill's plastic as a share of the space left for it, in percent: its lines'
    #: length times their section, over that area. None where no space is left.
    infill: float | None
    #: The walls round each outline in the region, as most samples along it count them, and the
    #: share of those samples whose walls run into another outline's: walls that fill the space.
    walls: int | None
    walls_meet: float
    #: The sparse infill's lines' length by the way they run, in 15 degree steps from +x.
    angles: dict[int, float]
    #: The plastic laid inside the region on this layer, mm3.
    volume_mm3: float


def _section(width: float, height: float) -> float:
    """A line's cross-section over its height: Orca's flow, a rectangle with round ends."""
    return max(width - height * (1 - math.pi / 4), 0.0)


def _walls_counted(
    samples: np.ndarray, normals: np.ndarray, others: np.ndarray, kinds: np.ndarray,
    width: float,
) -> tuple[list[int], list[bool]]:
    """Each sample's walls: itself and each inner wall a ray from it meets in turn, each within
    `NEXT_WALL` widths of the last, the way more walls are met. And whether the walls ran into
    another outline's outer wall."""
    counts: list[int] = []
    meets: list[bool] = []
    if len(samples) == 0 or len(others) == 0:
        return counts, meets
    ax, ay = others[:, 0], others[:, 1]
    ex, ey = others[:, 2] - ax, others[:, 3] - ay
    for (px, py), (nx_, ny_) in zip(samples, normals, strict=True):
        best = (1, False)
        for sign in (1.0, -1.0):
            dx, dy = sign * nx_, sign * ny_
            den = dx * ey - dy * ex
            ok = np.abs(den) > 1e-12
            qx, qy = ax - px, ay - py
            with np.errstate(divide="ignore", invalid="ignore"):
                t = np.where(ok, (qx * ey - qy * ex) / den, -1.0)
                u = np.where(ok, (qx * dy - qy * dx) / den, -1.0)
            hit = (t > width * 0.25) & (t <= REACH) & (u >= 0) & (u <= 1)
            order = np.argsort(t[hit])
            ts, ks = t[hit][order], kinds[hit][order]
            count, last, meet = 1, 0.0, False
            for tt, kk in zip(ts, ks, strict=True):
                if tt - last > NEXT_WALL * width:
                    break
                if kk == 1:  # an outer wall: another outline's
                    meet = True
                    break
                if kk == 2:  # infill or solid: the walls end here
                    break
                if tt - last > width * 0.25:
                    count += 1
                    last = tt
            if count > best[0]:
                best = (count, meet)
        counts.append(best[0])
        meets.append(best[1])
    return counts, meets


def built(layer: Layer, grid: Grid, region: np.ndarray) -> Built:
    """What Orca laid on `layer` inside `region`, a mask of `grid`: the part's area, its walls,
    how solid it is, its sparse infill's share and direction, and the plastic laid. `layer` is
    read with its lines kept (`read(keep_lines=True)`)."""
    lines = layer.lines
    width = max((ln.width for ln in lines if ln.kind in WALLS), default=0.45)
    half = width / 2
    by_kind: dict[str, list[Line]] = defaultdict(list)
    for line in lines:
        by_kind[line.kind].append(line)

    def stamped(kinds: tuple[str, ...]) -> np.ndarray:
        chosen = [ln for k in kinds for ln in by_kind.get(k, [])]
        points, _ = _samples(chosen)
        return grid.stamp(points, half)

    walls = stamped(WALLS)
    solid = stamped(SOLID)
    outline = grid.fill(loops(lines)) | stamped((OUTER_WALL,))
    inside = region & outline
    core = inside & ~walls
    space = core & ~solid
    cell = grid.cell_mm2
    # The infill's share is read a line's width clear of the walls, of solid fill and of the
    # region's own edge: Orca anchors its sparse lines along the walls (`infill_anchor`) and
    # joins them along the edge of the space they fill, and those runs aren't the pattern.
    wall_points, _ = _samples([ln for k in (*WALLS, *SOLID) for ln in by_kind.get(k, [])])
    edge = region & ~_inner(region)
    rows, cols = np.nonzero(edge)
    edge_points = np.column_stack([grid.x0 + (cols + 0.5) * grid.cell,
                                   grid.y0 + (rows + 0.5) * grid.cell])
    clear = (space & ~grid.stamp(wall_points, half + width)
             & ~grid.stamp(edge_points, width))

    sparse_points, which = _samples(by_kind.get(SPARSE, []))
    infill: float | None = None
    angles: dict[int, float] = {}
    if clear.sum() * cell >= 1.0:
        row, col, on = grid.cells(sparse_points[:, 0], sparse_points[:, 1])
        sparse_lines = by_kind.get(SPARSE, [])
        if on.any():
            keep = np.zeros(len(sparse_points), dtype=bool)
            keep[on] = clear[row[on], col[on]]
            laid = 0.0
            for k in np.flatnonzero(keep):
                ln = sparse_lines[int(which[k])]
                laid += STEP * _section(ln.width, layer.height)
                step = round(math.degrees(math.atan2(ln.y1 - ln.y0, ln.x1 - ln.x0)) % 180 / 15)
                angle = int(step * 15 % 180)
                angles[angle] = angles.get(angle, 0.0) + STEP
            infill = 100.0 * laid / (clear.sum() * cell)
        else:
            infill = 0.0

    volume = 0.0
    all_points, all_which = _samples(lines)
    if len(all_points):
        row, col, on = grid.cells(all_points[:, 0], all_points[:, 1])
        keep = np.zeros(len(all_points), dtype=bool)
        keep[on] = region[row[on], col[on]]
        sections = np.array([_section(ln.width, layer.height) for ln in lines])
        volume = float(STEP * sections[all_which[keep]].sum() * layer.height)

    outer = by_kind.get(OUTER_WALL, [])
    wall_count: int | None = None
    meet = 0.0
    if outer:
        points, which_outer = _samples(outer)
        row, col, on = grid.cells(points[:, 0], points[:, 1])
        keep = np.zeros(len(points), dtype=bool)
        keep[on] = region[row[on], col[on]]
        keep &= (np.arange(len(points)) % max(1, round(0.5 / STEP))) == 0
        if keep.any():
            ends = np.array([(ln.x0, ln.y0, ln.x1, ln.y1) for ln in outer])[which_outer[keep]]
            d = np.column_stack([ends[:, 2] - ends[:, 0], ends[:, 3] - ends[:, 1]])
            d /= np.maximum(np.hypot(d[:, 0], d[:, 1]), 1e-12)[:, None]
            normals = np.column_stack([-d[:, 1], d[:, 0]])
            near = [ln for ln in lines if ln.kind in WALLS or ln.kind in SOLID
                    or ln.kind == SPARSE]
            others = np.array([(ln.x0, ln.y0, ln.x1, ln.y1) for ln in near])
            kinds = np.array([0 if ln.kind == "Inner wall" else 1 if ln.kind == OUTER_WALL
                              else 2 for ln in near])
            counts, meets = _walls_counted(points[keep], normals, others, kinds, width)
            if counts:
                values, times = np.unique(counts, return_counts=True)
                wall_count = int(values[np.argmax(times)])
                meet = float(np.mean(meets))

    return Built(
        n=layer.n, z=layer.z, height=layer.height,
        area_mm2=round(float(inside.sum() * cell), 3),
        core_mm2=round(float(core.sum() * cell), 3),
        solid=round(float((core & solid).sum() / core.sum()), 3) if core.any() else 0.0,
        infill=None if infill is None else round(infill, 2),
        walls=wall_count, walls_meet=round(meet, 3),
        angles={k: round(v, 2) for k, v in sorted(angles.items())},
        volume_mm3=round(volume, 4),
    )
