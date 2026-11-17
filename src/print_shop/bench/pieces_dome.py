"""`pieces dome`: a dome shade's piece map from rules (mqucifer/print-shop#154).

A lamp's shade is a dome of stained glass: one motif repeated round it, in bands from its top
to its hem. Its pieces aren't traced from a picture: they follow the dome's own lines, down and
around, so they're made here from a few figures, and then checked, fixed and built with the same
`pieces` commands as a traced map.

**The surface** is half an ellipsoid, open at its top: its side is a quarter of an ellipse, half
`--diameter` across and `--height` tall, turned round the dome's axis. At `--height` half of
`--diameter` it's a hemisphere. The top is open `--top` across, for the lamp's fitting, and
because slices that met at a point there would have no width: a fifth of the diameter unless
given. A place on it is how far round, as an angle, and how far down from the opening's edge,
along the dome.

**The bands file** lists the bands from the top to the hem (`docs/pieces.md` has the format):
each its height down the dome and its pattern, with the pattern's own figures and its cells'
colours. A band is laid once in a repeat, `--repeats` times round. Every figure is to the middle
of a lead line, but a jewel's diameter, which is its glass.

**Lines meet as lines, by construction.** A band's top and bottom are lines all the way round,
each repeat's sides are lines from the top to the hem, and every other line runs from one of
those to another. No line ends in glass, and no piece lies inside another.

**A piece is its glass:** its cell less half the lead along each of its lines, as far on the dome
itself, not on the map: a place's distance round is its angle times the dome's radius there. So
the lead is as wide near the top as at the hem. The dome's top and hem are the glass's own
edges, with no lead inside them, as a panel's edge is.

**The map** is the dome's chart, a piece map like any other with `surface` saying what it lies
on. x is the way round, as it is at the hem, from 0 to the hem's whole length; y is up the dome
from the hem, along it. So it's true to size up and down, and round the hem; nearer the top
everything is drawn wider than it is, by the hem's radius over the dome's radius there. `pieces
check` and `pieces build` read a flat panel: `pieces flatten` cuts this map into slices, each
flat, and those are what they read.

**Second method.** The glass's share of the dome by each outline's own area on it, summed along
the outline, against the map's pixels each weighed by the dome's radius at its row. And the
pixels' separate patches of glass against the pieces, with the lead between them one body.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry.polygon import orient

from . import colours as named_colours
from . import piecemap
from .piecemap import Map, Piece
from .report import NoAnswer, Report

#: The patterns a band may have, and each one's own figures with what it is where not given.
PATTERNS: dict[str, dict[str, Any]] = {
    "grid": {"rows": 1, "columns": 1},
    "swag": {"depth_mm": None, "shoulder_mm": "half of what the depth leaves"},
    "jewels": {"diameter_mm": None, "count": 1},
    "strip": {"count": 1},
    "triangles": {"count": 1},
    "scallop": {"depth_mm": None, "count": 1},
}
#: The lead line's width where the bands file gives none, in mm.
LEAD = 1.4
#: The furthest apart two points of an outline are, in mm on the dome.
STEP = 1.0
#: Points along the dome's side, from the opening's edge to the hem.
TABLE = 20001
#: A turn sharper than this, in degrees, is a corner: two sides meet there, each with its strip.
CORNER = 30.0
#: A side's strip of lead runs on this many half leads past each of its corners, and starts
#: `HAIR` mm outside the cell, so no sliver of the cell's own edge is left.
RUN_ON = 4.0
HAIR = 1e-3
#: A piece's point this near the panel's edge, in mm, is on it.
FLUSH = 2e-3
#: Bands' heights that add up to within this of the dome's side, in mm, are its side.
SUM_OFF = 0.05
#: How far the outlines' glass share and the pixels' may differ, as `pieces check` allows it:
#: half a pixel's width along every outline, and this many points.
SHARE_SLACK = 0.05
PICTURE_PX = 1500
FORMAT = (
    'a bands file is JSON: {"lead_mm": 1.4, "colours": {NAME: {"rgb": [r, g, b]}}, "bands": '
    '[{"pattern": "grid", "height_mm": 40, "rows": 2, "columns": 2, "colours": ["amber", '
    '"green"]}, ...]}, the bands from the top to the hem. A pattern is '
    + ", ".join(
        f"`{name}`" + (f" ({', '.join(f'`{k}`' for k in figures)})" if figures else "")
        for name, figures in PATTERNS.items()
    )
    + ". One band may leave `height_mm` out, and takes what the others leave"
)


class Dome:
    """Half an ellipsoid, open at its top: where each place on it is.

    A place is `phi`, its angle round the axis in radians, and `s`, its distance down from the
    opening's edge along the dome, in mm.
    """

    def __init__(self, diameter: float, height: float, top: float) -> None:
        if not (diameter > 0 and height > 0):
            raise NoAnswer("--diameter and --height are the dome's, in mm, each over 0")
        if not 0 < top < diameter:
            raise NoAnswer(
                f"--top is the opening at the dome's top, in mm across, over 0 and under the "
                f"dome's {diameter:g}: its slices would meet at a point with none"
            )
        self.diameter, self.height, self.top = float(diameter), float(height), float(top)
        self.a, self.b = self.diameter / 2.0, self.height
        angle = np.linspace(math.asin(self.top / self.diameter), math.pi / 2.0, TABLE)
        speed = np.hypot(self.a * np.cos(angle), self.b * np.sin(angle))
        gone = np.concatenate([[0.0], np.cumsum(0.5 * (speed[1:] + speed[:-1]) * np.diff(angle))])
        ring = self.a * np.sin(angle) * speed
        swept = np.concatenate([[0.0], np.cumsum(0.5 * (ring[1:] + ring[:-1]) * np.diff(angle))])
        self._angle, self._gone, self._swept = angle, gone, swept
        #: From the opening's edge to the hem, along the dome.
        self.length = float(gone[-1])

    def _at(self, s: Any) -> Any:
        return np.interp(s, self._gone, self._angle)

    def radius(self, s: Any) -> Any:
        """The dome's radius, `s` down it."""
        return self.a * np.sin(self._at(s))

    def z(self, s: Any) -> Any:
        """The height over the hem, `s` down the dome."""
        return self.b * np.cos(self._at(s))

    def swept(self, s: Any) -> Any:
        """The radius summed down the dome from its top to `s`: an area's own measure."""
        return np.interp(s, self._gone, self._swept)

    def point(self, phi: Any, s: Any) -> np.ndarray:
        """A place on the dome, in space: x, y and z, the axis along z and the hem at z = 0."""
        r = self.radius(s)
        return np.stack([r * np.cos(phi), r * np.sin(phi), self.z(s) + 0.0 * r], axis=-1)

    @property
    def round_mm(self) -> float:
        """The hem's whole length round: the chart's width."""
        return 2.0 * math.pi * self.a

    def chart(self, places: np.ndarray) -> np.ndarray:
        """Places, `phi` and `s`, as the map's x and y."""
        return np.column_stack([self.a * places[:, 0], self.length - places[:, 1]])

    def places(self, xy: np.ndarray) -> np.ndarray:
        """The map's x and y as places on the dome."""
        return np.column_stack([xy[:, 0] / self.a, self.length - xy[:, 1]])

    def area(self, ring: np.ndarray) -> float:
        """A closed outline's area on the dome, from its places: the radius summed down the
        dome, taken along the outline, by Simpson's rule on each side."""
        after = np.roll(ring, -1, axis=0)
        middle = 0.5 * (ring[:, 1] + after[:, 1])
        mean = (self.swept(ring[:, 1]) + 4.0 * self.swept(middle) + self.swept(after[:, 1])) / 6.0
        return abs(float(np.sum(mean * (after[:, 0] - ring[:, 0]))))

    def plain(self) -> dict[str, Any]:
        return {
            "profile": "half an ellipsoid",
            "diameter_mm": self.diameter,
            "height_mm": self.height,
            "top_mm": self.top,
            "meridian_mm": round(self.length, 4),
        }


def dome_of(found: Map, name: str) -> tuple[Dome, dict[str, Any]]:
    """The dome a map lies on, or that it isn't a dome's map."""
    surface = found.surface
    if surface.get("kind") != "dome":
        raise NoAnswer(
            f"{name} isn't a dome's map: `pieces dome --diameter D --height H --repeats N "
            "--bands FILE` writes one"
        )
    try:
        dome = Dome(
            float(surface["diameter_mm"]), float(surface["height_mm"]), float(surface["top_mm"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise NoAnswer(f"{name}'s `surface` doesn't say its dome's size: {exc}") from exc
    return dome, surface


def shape_area(dome: Dome, shape: Any) -> float:
    """A map's shape as its area on the dome."""
    total = 0.0
    for poly in piecemap.polygons(shape):
        total += dome.area(dome.places(shapely.get_coordinates(poly.exterior)[:-1]))
        for hole in poly.interiors:
            total -= dome.area(dome.places(shapely.get_coordinates(hole)[:-1]))
    return total


# The bands file


@dataclass
class Band:
    number: int
    pattern: str
    figures: dict[str, Any]
    colours: list[str]
    height: float | None
    top: float = 0.0
    bottom: float = 0.0
    cells: int = 0

    @property
    def said(self) -> str:
        figures = self.figures
        if self.pattern == "grid":
            return f"a grid of {figures['rows']} row(s) by {figures['columns']} column(s)"
        if self.pattern == "swag":
            return (
                f"a swag {figures['depth_mm']:g} mm deep, hung {figures['shoulder_mm']:g} mm "
                "under the band's top"
            )
        if self.pattern == "jewels":
            return (
                f"{figures['count']} jewel(s) of {figures['diameter_mm']:g} mm, each in its ground"
            )
        if self.pattern == "strip":
            return f"a strip of {figures['count']} piece(s)"
        if self.pattern == "triangles":
            return f"{figures['count']} triangle(s) point down, and as many up between them"
        return f"a hem of {figures['count']} scallop(s), {figures['depth_mm']:g} mm deep"


def _whole(given: Any, what: str) -> int:
    if isinstance(given, bool) or not isinstance(given, int) or given < 1:
        raise NoAnswer(f"{what} is a whole number from 1: {given!r} isn't")
    return given


def _length(given: Any, what: str) -> float:
    if isinstance(given, bool) or not isinstance(given, int | float) or not given > 0:
        raise NoAnswer(f"{what} is a number over 0, in mm: {given!r} isn't")
    return float(given)


@dataclass
class Rules:
    bands: list[Band]
    lead: float
    colours: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_bands(path: str, dome: Dome) -> Rules:
    """The bands file, each band placed down the dome, or what's wrong with it."""
    source = Path(path)
    if not source.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise NoAnswer(f"{path} isn't JSON: {exc}") from exc
    if isinstance(data, list):
        data = {"bands": data}
    given = data.get("bands") if isinstance(data, dict) else None
    if not isinstance(given, list) or not given:
        raise NoAnswer(f"{path} has no `bands`: {FORMAT}")
    lead = _length(data.get("lead_mm", LEAD), "`lead_mm`, the lead line's width")
    bands: list[Band] = []
    for number, entry in enumerate(given, start=1):
        what = f"band {number}"
        if not isinstance(entry, dict) or entry.get("pattern") not in PATTERNS:
            raise NoAnswer(
                f"{what} needs a `pattern`, one of {', '.join(PATTERNS)}: "
                f"{entry.get('pattern') if isinstance(entry, dict) else entry!r} isn't"
            )
        pattern = str(entry["pattern"])
        allowed = {"pattern", "height_mm", "colours", *PATTERNS[pattern]}
        odd = sorted(set(entry) - allowed)
        if odd:
            raise NoAnswer(
                f"{what} ({pattern}) has `{odd[0]}`, which that pattern doesn't take: its "
                f"figures are {', '.join(f'`{k}`' for k in sorted(allowed - {'pattern'}))}"
            )
        figures: dict[str, Any] = {}
        for key, usual in PATTERNS[pattern].items():
            if key.endswith("_mm"):
                if key in entry:
                    figures[key] = _length(entry[key], f"{what}'s `{key}`")
                elif usual is None:
                    raise NoAnswer(f"{what} ({pattern}) needs `{key}`, in mm")
            else:
                figures[key] = _whole(entry.get(key, usual), f"{what}'s `{key}`")
        tints = entry.get("colours")
        if (
            not isinstance(tints, list)
            or not tints
            or not all(isinstance(c, str) and c for c in tints)
        ):
            raise NoAnswer(
                f'{what} needs `colours`, its cells\' colours by name: ["amber", "green"]'
            )
        height = entry.get("height_mm")
        bands.append(
            Band(
                number,
                pattern,
                figures,
                list(tints),
                None if height is None else _length(height, f"{what}'s `height_mm`"),
            )
        )
        if pattern == "scallop" and number != len(given):
            raise NoAnswer(
                f"{what} is a scallop, the hem's edge: it's the last band, not band {number}"
            )
    _place(bands, dome)
    for band in bands:
        _settle(band, lead)
    known = data.get("colours") or {}
    if not isinstance(known, dict):
        raise NoAnswer(f"`colours` is each colour's name and its rgb: {FORMAT}")
    table: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(c for band in bands for c in band.colours):
        entry = known.get(name)
        if isinstance(entry, dict) and isinstance(entry.get("rgb"), list | tuple):
            rgb = [int(v) for v in entry["rgb"]]
        elif name in named_colours.NAMED:
            rgb = list(named_colours.NAMED[name])
        else:
            raise NoAnswer(
                f"the colour `{name}` isn't one the bench has a name for: give it in the "
                f'bands file, "colours": {{"{name}": {{"rgb": [r, g, b]}}}}'
            )
        if len(rgb) != 3 or not all(0 <= v <= 255 for v in rgb):
            raise NoAnswer(f"the colour `{name}`'s rgb is three numbers from 0 to 255")
        table[name] = {"rgb": rgb}
    return Rules(bands, lead, table)


def _place(bands: list[Band], dome: Dome) -> None:
    """Each band's top and bottom, down the dome: the heights are its side, and a band with
    none takes what the others leave."""
    given = sum(b.height for b in bands if b.height is not None)
    open_ones = [b for b in bands if b.height is None]
    if len(open_ones) > 1:
        raise NoAnswer(
            f"bands {', '.join(str(b.number) for b in open_ones)} have no `height_mm`: one "
            "band may leave it out, and takes what the others leave"
        )
    left = dome.length - given
    if open_ones:
        if not left > 0:
            raise NoAnswer(
                f"the bands' heights come to {given:.2f} mm, and the dome's side is "
                f"{dome.length:.2f} mm from its opening's edge to its hem: nothing is left for "
                f"band {open_ones[0].number}"
            )
        open_ones[0].height = left
    elif abs(left) > SUM_OFF:
        raise NoAnswer(
            f"the bands' heights come to {given:.2f} mm, and the dome's side is "
            f"{dome.length:.2f} mm from its opening's edge to its hem, along it: leave one "
            "band's `height_mm` out and it takes what the others leave"
        )
    at = 0.0
    for band in bands:
        assert band.height is not None
        band.top, at = at, at + band.height
        band.bottom = at
    bands[-1].bottom = dome.length
    bands[-1].height = dome.length - bands[-1].top


def _settle(band: Band, lead: float) -> None:
    """A band's figures against its height: what a pattern can't be made with is said."""
    assert band.height is not None
    what = f"band {band.number} ({band.pattern}, {band.height:.2f} mm tall)"
    figures = band.figures
    if band.pattern == "swag":
        depth = figures["depth_mm"]
        if not depth < band.height - 2.0 * lead:
            raise NoAnswer(f"{what} has no room for a swag {depth:g} mm deep and its lead")
        figures.setdefault("shoulder_mm", round((band.height - depth) / 2.0, 4))
        under = band.height - depth - figures["shoulder_mm"]
        if not under > lead:
            raise NoAnswer(
                f"{what} leaves {under:.2f} mm under its swag, hung {figures['shoulder_mm']:g} "
                f"mm down and {depth:g} deep: no glass is left there between two lines"
            )
    if band.pattern == "scallop" and not figures["depth_mm"] < band.height - lead:
        raise NoAnswer(f"{what} has no room for a scallop {figures['depth_mm']:g} mm deep")
    if band.pattern == "jewels":
        over = band.height / 2.0 - figures["diameter_mm"] / 2.0 - 1.5 * lead
        if not over > 0:
            raise NoAnswer(
                f"{what} has no glass over and under a jewel of {figures['diameter_mm']:g} mm "
                f"and its lead: a jewel's band is taller than the jewel by over {3 * lead:g} mm"
            )


# One repeat's cells


@dataclass
class Cell:
    """One cell of a repeat: its corners as places, in order with the dome's top on the left
    of the first side, and whether each side, from its corner to the next, is a lead line. A
    side on the dome's top or its hem isn't: the glass ends there."""

    at: list[tuple[float, float]]
    lead: list[bool]
    colour: str


def _box(phi0: float, phi1: float, s0: float, s1: float, band: Band, dome: Dome) -> Cell:
    return Cell(
        [(phi0, s0), (phi1, s0), (phi1, s1), (phi0, s1)],
        [s0 > 0.0, True, s1 < dome.length, True],
        "",
    )


def cells_of(band: Band, dome: Dome, turn: float, lead: float) -> list[Cell]:
    """A band's cells in the first repeat, which is `turn` radians round."""
    top, bottom, figures = band.top, band.bottom, band.figures
    inside_top, inside_hem = top > 0.0, bottom < dome.length
    tint = band.colours
    cells: list[Cell] = []
    if band.pattern in ("grid", "strip"):
        rows = figures.get("rows", 1)
        columns = figures.get("columns", figures.get("count", 1))
        for row in range(rows):
            for column in range(columns):
                cell = _box(
                    turn * column / columns,
                    turn * (column + 1) / columns,
                    top + (bottom - top) * row / rows,
                    top + (bottom - top) * (row + 1) / rows,
                    band,
                    dome,
                )
                cell.colour = tint[(row + column) % len(tint)]
                cells.append(cell)
    elif band.pattern == "swag":
        hung = top + figures["shoulder_mm"]
        along = np.linspace(0.0, 1.0, 65)
        curve = [(turn * t, hung + figures["depth_mm"] * 4.0 * t * (1.0 - t)) for t in along]
        back = curve[::-1]
        cells.append(
            Cell(
                [(0.0, top), (turn, top), *back],
                [inside_top, True, *[True] * len(back)],
                tint[0],
            )
        )
        cells.append(
            Cell(
                [*curve, (turn, bottom), (0.0, bottom)],
                [*[True] * len(curve), inside_hem, True],
                tint[1 % len(tint)],
            )
        )
    elif band.pattern == "jewels":
        count = figures["count"]
        middle = (top + bottom) / 2.0
        reach = figures["diameter_mm"] / 2.0 + lead / 2.0
        narrowest = float(np.min(dome.radius(np.linspace(top, bottom, 33))))
        beside = narrowest * turn / count / 2.0 - reach - lead
        if not beside > 0:
            raise NoAnswer(
                f"band {band.number}'s jewels, {figures['diameter_mm']:g} mm with their lead, "
                f"don't fit {count} to a repeat, which is {narrowest * turn:.1f} mm round at "
                "the band's narrowest: fewer, or smaller"
            )

        def arc(centre: float, start: float, stop: float) -> list[tuple[float, float]]:
            found = []
            for angle in np.radians(np.linspace(start, stop, 49)):
                s = middle + reach * math.sin(angle)
                found.append((centre + reach * math.cos(angle) / float(dome.radius(s)), s))
            return found

        for k in range(count):
            left, right = turn * k / count, turn * (k + 1) / count
            centre = (left + right) / 2.0
            ring = arc(centre, 0.0, 360.0)[:-1]
            cells.append(Cell(ring, [True] * len(ring), tint[0]))
            west = arc(centre, 270.0, 90.0)
            east = arc(centre, 90.0, -90.0)
            ground = tint[(1 + k % max(1, len(tint) - 1)) % len(tint)]
            cells.append(
                Cell(
                    [(left, top), (centre, top), *west, (centre, bottom), (left, bottom)],
                    [inside_top, *[True] * (len(west) + 1), inside_hem, True],
                    ground,
                )
            )
            cells.append(
                Cell(
                    [(centre, top), (right, top), (right, bottom), (centre, bottom), *east],
                    [inside_top, True, inside_hem, *[True] * (len(east) + 1)],
                    ground,
                )
            )
    elif band.pattern == "triangles":
        count = figures["count"]
        step = turn / count
        up = tint[1 % len(tint)]
        cells.append(
            Cell([(0.0, top), (step / 2.0, bottom), (0.0, bottom)], [True, inside_hem, True], up)
        )
        for k in range(count):
            cells.append(
                Cell(
                    [(step * k, top), (step * (k + 1), top), (step * (k + 0.5), bottom)],
                    [inside_top, True, True],
                    tint[0],
                )
            )
            if k:
                cells.append(
                    Cell(
                        [(step * k, top), (step * (k + 0.5), bottom), (step * (k - 0.5), bottom)],
                        [True, inside_hem, True],
                        up,
                    )
                )
        cells.append(
            Cell(
                [(turn, top), (turn, bottom), (turn - step / 2.0, bottom)],
                [True, inside_hem, True],
                up,
            )
        )
    else:  # a scalloped hem
        count = figures["count"]
        edge = bottom - figures["depth_mm"]
        along = np.linspace(1.0, 0.0, 49)
        for k in range(count):
            left, right = turn * k / count, turn * (k + 1) / count
            curve = [
                (left + (right - left) * t, edge + figures["depth_mm"] * 4.0 * t * (1.0 - t))
                for t in along
            ]
            cells.append(
                Cell(
                    [(left, top), (right, top), *curve],
                    [inside_top, True, *[False] * (len(curve) - 1), True],
                    tint[k % len(tint)],
                )
            )
    band.cells = len(cells)
    return cells


def hem_line(band: Band, dome: Dome, turn: float, repeats: int) -> list[tuple[float, float]]:
    """The hem's edge as places, from the first repeat's left all the way round: a scallop's
    curve, or the hem itself."""
    if band.pattern != "scallop":
        return [(0.0, dome.length), (turn * repeats, dome.length)]
    count, depth = band.figures["count"], band.figures["depth_mm"]
    found: list[tuple[float, float]] = []
    step = turn / count
    for k in range(count * repeats):
        for t in np.linspace(0.0, 1.0, 49)[: None if k == count * repeats - 1 else -1]:
            found.append((step * (k + t), band.bottom - depth + depth * 4.0 * t * (1.0 - t)))
    return found


def _fine(at: np.ndarray, lead: np.ndarray, dome: Dome) -> tuple[np.ndarray, np.ndarray]:
    """An outline with points no further apart than `STEP` on the dome, each new one on its
    side and a lead line where its side is."""
    points: list[np.ndarray] = []
    flags: list[np.ndarray] = []
    after = np.roll(at, -1, axis=0)
    radius = dome.radius(0.5 * (at[:, 1] + after[:, 1]))
    long = np.hypot(radius * (after[:, 0] - at[:, 0]), after[:, 1] - at[:, 1])
    for k in range(len(at)):
        parts = max(1, math.ceil(float(long[k]) / STEP))
        share = np.arange(parts)[:, None] / parts
        points.append(at[k] + share * (after[k] - at[k]))
        flags.append(np.full(parts, lead[k]))
    return np.concatenate(points), np.concatenate(flags)


class NoGlass(Exception):
    """A cell that its lead leaves no one piece of glass in."""


def glass_of(at: np.ndarray, lead: np.ndarray, dome: Dome, half: float) -> Any:
    """A cell's glass, in the map's x and y: the cell less a strip `half` wide along each side
    that's a lead line. A strip's width is taken on the dome: a distance round it is the angle
    times the dome's radius at that point, so the lead is as wide wherever it is.

    Along a side each point moves square to it, into the cell. At a corner each side's strip
    runs on past it, so two strips meet as two lines do, and what they leave is the glass.
    Every pattern's corners turn into its cell; one that turned out would want its lead's
    corner rounded, which this doesn't do."""
    count = len(at)
    scale = np.column_stack([dome.radius(at[:, 1]), np.ones(count)])
    before = (at - np.roll(at, 1, axis=0)) * scale
    after = (np.roll(at, -1, axis=0) - at) * scale
    before /= np.linalg.norm(before, axis=1)[:, None]
    after /= np.linalg.norm(after, axis=1)[:, None]
    # The outline goes round with the dome's top on the left of its first side, so the cell is
    # on the left of every side: a side's direction turned a quarter turn.
    n1 = np.column_stack([-before[:, 1], before[:, 0]])
    n2 = np.column_stack([-after[:, 1], after[:, 0]])
    cos = np.clip(np.sum(n1 * n2, axis=1), -1.0, 1.0)
    corner = (cos < math.cos(math.radians(CORNER))) | (lead != np.roll(lead, 1))
    moved = at + (half / (1.0 + cos))[:, None] * (n1 + n2) / scale
    cell = shapely.Polygon(dome.chart(at))
    if not corner.any():
        return shapely.Polygon(dome.chart(moved)) if lead.all() else cell
    strips = []
    corners = np.flatnonzero(corner)
    for k, start in enumerate(corners):
        stop = int(corners[(k + 1) % len(corners)])
        if not lead[start]:
            continue
        run = [(start + step) % count for step in range((stop - start - 1) % count + 2)]
        out0, out1 = n2[start] / scale[start], n1[stop] / scale[stop]
        back = at[start] - RUN_ON * half * after[start] / scale[start]
        on = at[stop] + RUN_ON * half * before[stop] / scale[stop]
        near = [back - HAIR * out0, *[at[i] - HAIR * n2[i] / scale[i] for i in run[:-1]]]
        near += [at[stop] - HAIR * out1, on - HAIR * out1]
        far = [back + half * out0, at[start] + half * out0, *[moved[i] for i in run[1:-1]]]
        far += [at[stop] + half * out1, on + half * out1]
        strips.append(shapely.Polygon(dome.chart(np.array([*near, *far[::-1]]))))
    left = piecemap.polygons(cell.difference(shapely.unary_union(strips)))
    if len(left) != 1:
        raise NoGlass
    return left[0]


def on_grid(shape: Any) -> Any:
    """A shape with each point on the grid a map's file keeps it to."""
    return shapely.transform(shape, lambda xy: np.round(xy, piecemap.PLACES))


def flush(panel: Any, shapes: list[Any]) -> Any:
    """A panel whose edge is its pieces' own points wherever a piece lies along it, so the
    glass ends on the panel's edge with no hair of lead between: each point of a piece within
    `FLUSH` of the edge becomes a point of it, and a point of the edge beside a piece's side
    that isn't one of the piece's goes."""
    points = shapely.get_coordinates(shapes)
    rim = points[shapely.distance(shapely.points(points), panel.exterior) < FLUSH]
    if not len(rim):
        return panel
    snapped = shapely.snap(panel, shapely.MultiPoint(rim), FLUSH)
    ring = shapely.get_coordinates(snapped.exterior)[:-1]
    own = {(float(x), float(y)) for x, y in rim}
    sides = shapely.unary_union([shape.exterior for shape in shapes])
    beside = shapely.distance(shapely.points(ring), sides) < FLUSH
    kept = [xy for xy, near in zip(ring, beside, strict=True) if not near or tuple(xy) in own]
    made = shapely.Polygon(kept) if len(kept) >= 3 else panel
    return orient(made, 1.0) if made.is_valid and made.area > 0 else panel


def pieces_of(rules: Rules, dome: Dome, repeats: int) -> tuple[list[Piece], Any]:
    """Every piece of the dome, in the map's x and y, and the map's panel."""
    turn = 2.0 * math.pi / repeats
    first: list[tuple[Any, str]] = []
    for band in rules.bands:
        for number, cell in enumerate(cells_of(band, dome, turn, rules.lead), start=1):
            at, lead = _fine(np.array(cell.at, dtype=float), np.array(cell.lead), dome)
            try:
                glass = glass_of(at, lead, dome, rules.lead / 2.0)
            except NoGlass:
                glass = None
            if glass is None or not glass.is_valid or not glass.area > 0:
                raise NoAnswer(
                    f"band {band.number} ({band.pattern}) has a cell with no one piece of "
                    f"glass left once half the lead, {rules.lead / 2.0:g} mm, is taken along "
                    f"each of its lines (cell {number} of its repeat): a taller band, fewer "
                    "cells across it, or a narrower lead"
                ) from None
            first.append((glass, cell.colour))
    pieces: list[Piece] = []
    for repeat in range(repeats):
        for glass, colour in first:
            moved = shapely.transform(glass, lambda xy, by=dome.a * turn * repeat: xy + [by, 0.0])
            pieces.append(Piece(len(pieces) + 1, colour, orient(on_grid(moved))))
    hem = np.array(hem_line(rules.bands[-1], dome, turn, repeats), dtype=float)
    hem[-1, 0] = 2.0 * math.pi
    outline = np.vstack([[[0.0, 0.0], [2.0 * math.pi, 0.0]], hem[::-1]])
    panel = on_grid(shapely.Polygon(dome.chart(outline)))
    return pieces, orient(flush(panel, [p.shape for p in pieces]))


# The readings


def share_on_dome(found: Map, dome: Dome) -> float:
    """The glass share on the dome itself: each piece's area there over the dome's."""
    glass = sum(shape_area(dome, p.shape) for p in found.pieces)
    return 100.0 * glass / shape_area(dome, found.panel)


def by_pixels(found: Map, dome: Dome) -> dict[str, Any]:
    """The map's pixels read as the dome: the glass share with each pixel weighed by the dome's
    radius at its row, the separate patches of glass, and the lead's separate bodies."""
    from scipy import ndimage

    drawn = piecemap.pixels(found)
    rows = drawn.ids.shape[0]
    y = drawn.y1 - (np.arange(rows) + 0.5) * drawn.mm
    weight = dome.radius(dome.length - y)[:, None] * np.ones((1, drawn.ids.shape[1]))
    on_dome = float(np.sum(weight[drawn.ids >= 0]))
    glass = float(np.sum(weight[drawn.ids > 0]))
    return {
        "share_pct": 100.0 * glass / max(on_dome, 1e-9),
        "patches": int(ndimage.label(drawn.ids > 0)[1]),
        "lead_bodies": int(ndimage.label(drawn.ids == 0)[1]),
        "mm": drawn.mm,
    }


def draw(found: Map, dome: Dome, out: Path, name: str) -> str:
    """The dome as a picture: seen from the side, and under it its map, each piece in its
    colour."""
    from PIL import Image, ImageDraw, ImageFont

    margin, gap, key_px = 16, 24, 22
    x0, y0, x1, y1 = found.panel.bounds
    scale = (PICTURE_PX - 2 * margin) / (x1 - x0)
    side = min(4.0, (PICTURE_PX - 2 * margin) / (2.2 * dome.a))
    side_tall = int(dome.b * side) + 2 * margin
    chart_tall = int((y1 - y0) * scale)
    height = side_tall + gap + chart_tall + margin + key_px
    picture = Image.new("RGB", (PICTURE_PX, height), "white")
    draw_on = ImageDraw.Draw(picture)

    def tint(piece: Piece) -> tuple[int, ...]:
        rgb = (found.colours.get(piece.colour) or {}).get("rgb")
        return tuple(int(v) for v in rgb) if rgb else (200, 200, 200)

    # From the side: the half of the dome that faces the eye, each piece where it stands.
    middle = PICTURE_PX / 2.0

    def seen(points: np.ndarray) -> list[tuple[float, float]]:
        return [(middle + x * side, margin + (dome.b - z) * side) for x, _y, z in points]

    down = np.linspace(0.0, dome.length, 200)
    rim = np.concatenate([dome.point(0.0, down), dome.point(math.pi, down[::-1])])
    draw_on.polygon(seen(rim), fill=(45, 45, 45))
    for piece in found.pieces:
        places = dome.places(shapely.get_coordinates(piece.shape.exterior)[:-1])
        if math.sin(float(np.mean(places[:, 0]))) < -0.02:
            draw_on.polygon(seen(dome.point(places[:, 0], places[:, 1])), fill=tint(piece))

    # Its map.
    top = side_tall + gap

    def px(points: Any) -> list[tuple[float, float]]:
        return [(margin + (x - x0) * scale, top + (y1 - y) * scale) for x, y in points]

    draw_on.polygon(px(found.panel.exterior.coords), fill=(45, 45, 45))
    font = ImageFont.load_default(size=11)
    for piece in found.pieces:
        draw_on.polygon(px(piece.shape.exterior.coords), fill=tint(piece))
    if len(found.pieces) <= 400:
        for piece in found.pieces:
            (x, y) = px(shapely.get_coordinates(piece.shape.representative_point()))[0]
            draw_on.text(
                (x, y),
                str(piece.id),
                fill=(0, 0, 0),
                font=font,
                anchor="mm",
                stroke_width=2,
                stroke_fill=(255, 255, 255),
            )
    key = (
        "above: the dome from the side. Below: its map, the way round by the way up from the "
        "hem; near the top a piece is drawn wider than it is"
    )
    draw_on.text((margin, height - key_px + 3), key, fill=(0, 0, 0), font=font)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-dome.png"
    picture.save(target)
    return str(target)


def run_dome(args: Any, report: Report) -> None:
    repeats = args.repeats
    if repeats < 3:
        raise NoAnswer("--repeats is how many times the motif goes round the dome, 3 or more")
    top = args.top if args.top is not None else args.diameter / 5.0
    dome = Dome(args.diameter, args.height, top)
    rules = read_bands(args.bands, dome)
    pieces, panel = pieces_of(rules, dome, repeats)
    surface = {
        "kind": "dome",
        **dome.plain(),
        "repeats": repeats,
        "x": "the way round, in mm at the hem: the dome's angle round its axis times the hem's "
        "radius",
        "y": "up the dome from the hem, in mm along it",
        "bands": [
            {
                "band": band.number,
                "pattern": band.pattern,
                **band.figures,
                "from_mm": round(band.top, 4),
                "to_mm": round(band.bottom, 4),
            }
            for band in rules.bands
        ],
    }
    found = Map(panel, rules.lead, rules.colours, pieces, surface=surface)
    on_dome = share_on_dome(found, dome)
    surface["glass_share_on_dome_pct"] = round(on_dome, 4)
    found.source = {
        "by": "pieces dome",
        "bands": Path(args.bands).name,
        "pieces": len(pieces),
        "glass_share_pct": round(piecemap.share(found), 4),
    }
    target = args.to or "dome.pieces.json"
    piecemap.save(found, target)
    report.wrote(target)
    found = piecemap.load(target)
    report.fact("map", target, how="the dome's piece map, for `pieces flatten`")
    report.numbers["profile"] = surface["profile"]
    report.fact(
        "dome_mm",
        [dome.diameter, dome.height],
        how=f"across at its hem, and tall: half an ellipsoid, its side a quarter of an ellipse "
        f"{dome.a:g} mm out and {dome.b:g} mm up, turned round the dome's axis",
    )
    report.fact(
        "top_mm",
        dome.top,
        "mm",
        "the opening at its top, across, for the fitting"
        + ("" if args.top else ": a fifth of the diameter, as --top wasn't given"),
    )
    report.fact(
        "meridian_mm",
        dome.length,
        "mm",
        "from the opening's edge to the hem, along the dome: what the bands' heights add up to",
    )
    report.fact("repeats", repeats, how="times round")
    per = sum(band.cells for band in rules.bands)
    report.fact(
        "pieces_count", len(found.pieces), how=f"{per} in a repeat, in {len(rules.bands)} band(s)"
    )
    rows = []
    for band in rules.bands:
        across = [2.0 * float(dome.radius(s)) for s in (band.top, band.bottom)]
        rows.append(
            {
                "band": band.number,
                "pattern": band.pattern,
                "from_mm": band.top,
                "to_mm": band.bottom,
                "pieces_a_repeat": band.cells,
                "dome_across_mm": across,
            }
        )
        report.line(
            f"  band {band.number}, {band.said}: {band.top:.2f} to {band.bottom:.2f} mm down "
            f"the dome, where it's {across[0]:.1f} to {across[1]:.1f} mm across; {band.cells} "
            "piece(s) a repeat"
        )
    report.numbers["bands"] = rows
    report.fact("lead_mm", found.lead_mm, "mm", "each lead line's width, on the dome")
    on_dome = share_on_dome(found, dome)
    report.fact(
        "glass_share_on_dome_pct",
        on_dome,
        "%",
        "the pieces' area on the dome over the dome's, each by its outline",
    )
    chord = 2.0 * dome.a * math.sin(math.pi / repeats)
    report.fact(
        "slice_would_be_mm",
        [round(chord, 3), round(dome.length, 3)],
        how=f"about, for one of {repeats} slices laid flat: across at the hem, and from top to "
        "hem. `pieces flatten` gives each slice's own",
    )
    pixels = by_pixels(found, dome)
    report.numbers["glass_share_on_dome_by_pixels_pct"] = pixels["share_pct"]
    report.numbers["glass_patches"] = pixels["patches"]
    report.numbers["lead_bodies"] = pixels["lead_bodies"]
    rims = sum(p.shape.length for p in found.pieces)
    dome_mm2 = shape_area(dome, found.panel)
    allowed = 50.0 * rims * pixels["mm"] / dome_mm2 + SHARE_SLACK
    report.second(
        f"the glass share on the dome is {on_dome:.2f}% by the outlines and "
        f"{pixels['share_pct']:.2f}% by the map's pixels, each weighed by the dome's radius at "
        f"its row, where a pixel's width along every outline allows {allowed:.2f} points"
    )
    report.second(
        f"the pixels show {pixels['patches']} separate patch(es) of glass for the map's "
        f"{len(found.pieces)} piece(s), and the lead between them as {pixels['lead_bodies']} "
        "body: every line meets another"
    )
    shown = draw(found, dome, Path(args.out), Path(target).name.split(".")[0])
    report.wrote(shown)
    report.numbers["picture"] = shown
    report.note(
        "the map is the dome's chart: true to size up and down and round the hem, and drawn "
        "wider than the dome nearer its top. `pieces check` and `pieces build` read a flat "
        f"panel, so cut it first: `pieces flatten {target} --slices {repeats}`, then check and "
        "build each slice"
    )
    differs = []
    if abs(on_dome - pixels["share_pct"]) > allowed:
        differs.append(
            f"the glass share on the dome is {on_dome:.2f}% by the outlines and "
            f"{pixels['share_pct']:.2f}% by the pixels, more than the {allowed:.2f} points a "
            "pixel's width allows"
        )
    if pixels["patches"] != len(found.pieces):
        differs.append(
            f"the pixels show {pixels['patches']} separate patch(es) of glass, and the map has "
            f"{len(found.pieces)} piece(s)"
        )
    if pixels["lead_bodies"] != 1:
        differs.append(
            f"the lead is {pixels['lead_bodies']} separate bodies in the pixels, not one"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    report.verdict(
        "OK",
        f"{len(found.pieces)} piece(s) in {target}: {repeats} repeats of {per}, in "
        f"{len(rules.bands)} band(s), on a dome {dome.diameter:g} mm across and "
        f"{dome.height:g} mm tall; glass share {on_dome:.2f}%",
    )
