"""What the pass found, as records: what it read, where, and what that means for the print.

Orca's own readings come first: its warnings, its labels for each layer's lines, their widths,
where it wrote the pause, and its time and filament for each plate. The bench's `pause` is the
second, and the pass's own readings of Orca's lines against the part come last: a stretch with no
line, wall lines laid over each other, and lines over a pane. Each says whose it is (`source`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from print_shop.slicer.gcode import Layer, Line, Sliced

#: Orca's labels for a line laid over air, as it reads the part.
OVER_AIR = ("Bridge", "Internal Bridge", "Overhang wall")
WALLS = ("Outer wall", "Inner wall", "Overhang wall")
GAP_FILL = "Gap infill"
#: Gap fill inside a wall over at least this many layers in a row is named by its cause.
MANY_LAYERS = 5
#: Layers whose walls at the gap fill differ by more than this, in mm, are read apart.
WALL_BAND = 0.1


@dataclass
class Finding:
    kind: str
    source: str  # orca, bench or pass
    read: str
    means: str
    needs_change: bool = False
    plate: str | None = None
    file: str | None = None
    layer_from: int | None = None
    layer_to: int | None = None
    z_from: float | None = None
    z_to: float | None = None
    numbers: dict[str, Any] = field(default_factory=dict)

    def plain(self) -> dict[str, Any]:
        return asdict(self)

    def where(self) -> str:
        bits = []
        if self.file:
            bits.append(f"`{self.file}`")
        if self.layer_from is not None:
            if self.layer_to not in (None, self.layer_from):
                bits.append(f"layers {self.layer_from}-{self.layer_to}")
            else:
                bits.append(f"layer {self.layer_from}")
        if self.z_from is not None:
            if self.z_to not in (None, self.z_from):
                bits.append(f"z {self.z_from:g}-{self.z_to:g}")
            else:
                bits.append(f"z {self.z_from:g}")
        if self.plate:
            bits.append(f"plate `{self.plate}`")
        return ", ".join(bits)


def runs_of(numbers: list[int]) -> list[tuple[int, int]]:
    """Consecutive runs, as (first, last)."""
    found: list[tuple[int, int]] = []
    for n in sorted(set(numbers)):
        if found and n == found[-1][1] + 1:
            found[-1] = (found[-1][0], n)
        else:
            found.append((n, n))
    return found


def inside(points: np.ndarray, rings: list[dict[str, Any]]) -> np.ndarray:
    """Which points fall in the outline's material: inside an outer ring and not in its holes."""
    found = np.zeros(len(points), dtype=bool)
    for polygon in rings:
        here = _in_ring(points, np.asarray(polygon["outer"], dtype=float))
        for hole in polygon.get("holes") or []:
            here &= ~_in_ring(points, np.asarray(hole, dtype=float))
        found |= here
    return found


def _in_ring(points: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Even-odd: a ray to +x crosses the ring an odd number of times."""
    if len(points) == 0 or len(ring) < 3:
        return np.zeros(len(points), dtype=bool)
    x, y = points[:, 0:1], points[:, 1:2]
    x0, y0 = ring[:-1, 0], ring[:-1, 1]
    x1, y1 = ring[1:, 0], ring[1:, 1]
    crosses = (y0 > y) != (y1 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return np.asarray(np.sum(crosses & (x < at), axis=1) % 2 == 1)


def midpoints(lines: list[Line]) -> np.ndarray:
    if not lines:
        return np.zeros((0, 2))
    return np.array([[(ln.x0 + ln.x1) / 2, (ln.y0 + ln.y1) / 2] for ln in lines])


def shifted(rings: list[dict[str, Any]], dx: float, dy: float) -> list[dict[str, Any]]:
    return [
        {
            "outer": [[x + dx, y + dy] for x, y in r["outer"]],
            "holes": [[[x + dx, y + dy] for x, y in h] for h in r.get("holes") or []],
        }
        for r in rings
    ]


def over_air(sliced: Sliced, plate: str, file: str) -> list[Finding]:
    """Each run of layers Orca lays over air (its bridge and overhang labels), first layer first."""
    found = []
    layers = [layer for layer in sliced.layers if any(layer.lengths.get(k) for k in OVER_AIR)]
    for first, last in runs_of([layer.n for layer in layers]):
        top = sliced.layers[first - 1]
        labels = {k: top.lengths[k] for k in OVER_AIR if top.lengths.get(k)}
        said = ", ".join(f"{k} {v:.0f} mm" for k, v in labels.items())
        found.append(
            Finding(
                "over_air",
                "orca",
                f"Orca lays layer {first} (z {top.z:g}) over air: {said}"
                + (f", and over air again to layer {last}" if last > first else ""),
                "Orca prints these lines as bridges or overhangs: at bridge speed, flow and fan, "
                "with nothing under them but what's set in at a pause, if anything",
                plate=plate,
                file=file,
                layer_from=first,
                layer_to=last,
                z_from=top.z,
                z_to=sliced.layers[last - 1].z,
                numbers={"labels_mm": labels},
            )
        )
    return found


def narrow_lines(sliced: Sliced, plate: str, file: str, nozzle: float) -> list[Finding]:
    """Gap fill, and any line under half the nozzle's width, by runs of layers."""
    found = []
    gap = [layer for layer in sliced.layers if layer.lengths.get("Gap infill")]
    for first, last in runs_of([layer.n for layer in gap]):
        span = sliced.layers[first - 1 : last]
        widths = [x.narrowest["Gap infill"] for x in span if "Gap infill" in x.narrowest]
        length = sum(x.lengths.get("Gap infill", 0.0) for x in span)
        least = min(widths) if widths else None
        found.append(
            Finding(
                "gap_fill",
                "orca",
                f"Orca gap-fills {length:.0f} mm over layers {first}-{last}, the narrowest line "
                f"{least:.3f} mm"
                if least is not None
                else f"Orca gap-fills {length:.0f} mm",
                (
                    f"a line {least:.2f} mm wide from a {nozzle:g} mm nozzle: Orca found the "
                    "space between walls too narrow for a wall, and fills it thin. Thin lines "
                    "bond and fill less well, and show where the light comes through"
                )
                if least is not None and least < nozzle
                else "Orca fills between walls",
                plate=plate,
                file=file,
                layer_from=first,
                layer_to=last,
                z_from=span[0].z,
                z_to=span[-1].z,
                numbers={"narrowest_mm": least, "length_mm": round(length, 2)},
            )
        )
    narrowest: tuple[float, str, Layer] | None = None
    for layer in sliced.layers:
        for kind, width in layer.narrowest.items():
            if narrowest is None or width < narrowest[0]:
                narrowest = (width, kind, layer)
    if narrowest is not None:
        width, kind, layer = narrowest
        found.append(
            Finding(
                "narrowest",
                "orca",
                f"the narrowest line Orca lays is {width:.3f} mm, {kind}, on layer {layer.n}",
                "the least width anywhere in the print, for the record"
                if width >= nozzle / 2
                else f"under half the {nozzle:g} mm nozzle: a line that thin barely extrudes",
                plate=plate,
                file=file,
                layer_from=layer.n,
                z_from=layer.z,
                numbers={"width_mm": width, "kind": kind},
            )
        )
    return found


def figures(sliced: Sliced, plate: str, colours: list[str], filaments: list[str]) -> Finding:
    grams = sliced.per_filament("filament used [g]")
    mm = sliced.per_filament("filament used [mm]")
    time_s = sliced.time_s
    said = []
    for k, name in enumerate(filaments):
        g = f"{grams[k]:g} g" if k < len(grams) else "? g"
        m = f"{mm[k]:g} mm" if k < len(mm) else "? mm"
        said.append(f"{name} ({colours[k] or 'no colour'}): {g}, {m}")
    hours = f"{time_s // 3600}h {time_s % 3600 // 60}m {time_s % 60}s" if time_s else "not given"
    changes = sliced.footer.get("total filament change", "0")
    return Finding(
        "time_filament",
        "orca",
        f"Orca's estimate: {hours}; " + "; ".join(said) + f"; filament changes: {changes}",
        "what this plate takes, by Orca's own estimate: one material a plate, so it's each "
        "material's time",
        plate=plate,
        numbers={
            "seconds": time_s,
            "grams": grams,
            "mm": mm,
            "filament_changes": changes,
            "layers": len(sliced.layers),
        },
    )


def bare(answer: dict[str, Any], plate: str, file: str) -> list[Finding]:
    """The pass's own: stretches of the part with no line, grouped by where they are."""
    return _grouped(
        answer.get("bare") or [],
        plate,
        file,
        "bare",
        lambda rows, w: (
            f"no line of Orca's on a stretch of the part about {w:.2f} mm wide",
            "this stretch isn't printed: on these layers the part has a gap there. Orca gave no "
            "warning for it",
        ),
        "width_mm",
    )


def overlapping(answer: dict[str, Any], plate: str, file: str) -> list[Finding]:
    return _grouped(
        answer.get("overlaps") or [],
        plate,
        file,
        "walls_overlap",
        lambda rows, w: (
            f"Orca lays two wall lines over each other, overlapping {w:.2f} mm across",
            "a feature narrower than two walls, printed as both: the second line pushes into "
            "the first, over-filling it, and the feature comes out wider or rougher than drawn",
        ),
        "overlap_mm",
    )


def _grouped(
    rows: list[dict[str, Any]], plate: str, file: str, kind: str, words: Any, figure: str
) -> list[Finding]:
    """Rows of one place over consecutive layers, as one finding each."""
    places: dict[tuple[float, ...], list[dict[str, Any]]] = {}
    for row in rows:
        (x0, y0), (x1, y1) = row["box"]
        key = (round(x0, 0), round(y0, 0), round(x1, 0), round(y1, 0))
        places.setdefault(key, []).append(row)
    found = []
    for spots in places.values():
        for first, last in runs_of([r["layer"] for r in spots]):
            span = [r for r in spots if first <= r["layer"] <= last]
            value = max(float(r[figure]) for r in span)
            read, means = words(span, value)
            (x0, y0), (x1, y1) = span[0]["box"]
            found.append(
                Finding(
                    kind,
                    "pass",
                    f"{read}, at x {x0:g}-{x1:g}, y {y0:g}-{y1:g} on the plate",
                    means,
                    plate=plate,
                    file=file,
                    layer_from=first,
                    layer_to=last,
                    z_from=span[0]["z"],
                    z_to=span[-1]["z"],
                    numbers={figure: value, "box": span[0]["box"], "layers": len(span)},
                )
            )
    return found


def gap_in_wall(
    answer: dict[str, Any],
    plate: str,
    file: str,
    lines: dict[str, Any],
    edges: Callable[[float], Any] | None = None,
) -> list[Finding]:
    """Gap fill inside a wall over many layers, named by its cause (mqucifer/print-shop#66): what
    the profile's walls a side take of the wall, by their line widths and spacings, and the widths
    either side that print with no gap fill and with infill, by Orca's own slices of a test wall
    at the profile (`walls.py`). On `eval-edges-1` 2 mm walls left 14.3 m of gap fill, down to
    0.096 mm, inside every wall from layer 10 to 100. A gap line is inside a wall where the part
    there is no wider than its walls a side, both sides, and a line between: wider, it's
    infill's."""
    outer, inner, loops = float(lines["outer"]), float(lines["inner"]), int(lines["loops"])
    widest = 2 * (outer + (loops - 1) * inner) + inner
    rows = [r for r in answer.get("gap_walls") or [] if float(r["wall_mm"]) <= widest]
    runs: list[list[dict[str, Any]]] = []
    for row in rows:
        last = runs[-1] if runs else None
        if (
            last is not None
            and int(row["layer"]) == int(last[-1]["layer"]) + 1
            and abs(float(row["wall_mm"]) - float(last[0]["wall_mm"])) <= WALL_BAND
        ):
            last.append(row)
        else:
            runs.append([row])
    found = []
    for run in runs:
        if len(run) < MANY_LAYERS:
            continue
        widths = sorted(float(r["wall_mm"]) for r in run)
        wall = widths[len(widths) // 2]
        length = sum(float(r["length_mm"]) for r in run)
        taken = 2 * (
            outer - float(lines["layer"]) * (1 - math.pi / 4)
            + (loops - 1) * (inner - float(lines["layer"]) * (1 - math.pi / 4))
        )
        band = edges(wall) if edges is not None else None
        said = (
            f"{wall:.2f} mm wall: its {loops} walls a side, {outer:g} mm outer and {inner:g} mm "
            f"inner lines ({lines['outer_from']}, {lines['inner_from']}), take {taken:.2f} mm "
            f"of it as Orca spaces them, and leave {wall - taken:.2f} mm between, too narrow "
            "for infill, which Orca gap-fills"
        )
        if band is not None and band.clean_to_mm is not None and band.infill_from_mm is not None:
            said += (
                f". By Orca's own slices of a test wall at this profile, a wall prints with no "
                f"gap fill up to {band.clean_to_mm:.2f} mm, and takes infill from "
                f"{band.infill_from_mm:.2f} mm: size it to one of those"
            )
        elif band is not None:
            said += ". Orca's slices of a test wall didn't find both edges of the band in reach"
        numbers: dict[str, Any] = {
            "wall_mm": wall,
            "outer_mm": outer,
            "inner_mm": inner,
            "wall_loops": loops,
            "layer_mm": float(lines["layer"]),
            "walls_take_mm": round(taken, 3),
            "length_mm": round(length, 2),
        }
        if band is not None:
            numbers.update(
                clean_to_mm=band.clean_to_mm,
                infill_from_mm=band.infill_from_mm,
                test_walls={f"{k:.2f}": v for k, v in band.read.items()},
            )
        found.append(
            Finding(
                "gap_in_wall",
                "pass",
                f"Orca's gap fill on layers {run[0]['layer']}-{run[-1]['layer']} lies inside a "
                f"wall {wall:.2f} mm wide, by the part's section at each gap line: "
                f"{length:.0f} mm of it",
                said,
                plate=plate,
                file=file,
                layer_from=int(run[0]["layer"]),
                layer_to=int(run[-1]["layer"]),
                z_from=float(run[0]["z"]),
                z_to=float(run[-1]["z"]),
                numbers=numbers,
            )
        )
    return found
