"""`pieces flatten`: a dome's piece map cut into slices, each laid flat as its own piece map,
with the angle each of its lead lines bends through to give the dome back
(mqucifer/print-shop#154).

A real dome of stained glass is made of flat pieces, and its curve comes from slight angles
along the lead lines. So a dome's map is cut from top to hem along lead lines into slices, and
each slice is a stack of flat rows, one for each stretch between two lines that run all the way
round. A slice prints flat, is checked and built as a flat panel, and bends only along the
lines where its rows meet, never across a pane.

**The cuts** are `--slices` lines from the top to the hem, evenly round from the map's x = 0.
Each has to lie in lead its whole length: one that crosses a piece is refused, naming the
pieces.

**The folds** are the lead lines that run all the way round: each height no piece of the map
reaches across. They're read from the map, so a map that's been fixed is cut where its lines
are now. Between two folds a slice is one flat row.

**A row is flat:** its four corners are on the dome, where its two folds meet its two cuts,
and it's the flat piece between them, narrower at the top. A place on the dome goes to the
row's place as far between its folds, by the way down the dome, and as far between its cuts,
by the angle round. So a line down the dome stays a straight line, a line round it becomes a
straight line across, and nothing is stretched but by the little a flat row is shorter and
narrower than the dome it stands for.

**A slice's map** is drawn as seen from outside the dome, its hem at the bottom. Its panel's
edge is the glass's edge: half a lead inside the cut's middle on each side. So `pieces build
--border` of half the lead puts the frame's edge on the cut's middle, and two slices meet in
one line as wide as the lead. Its `surface` holds what the joining stage reads: the hem's
place, each fold's height on the map and its bend, each row's tilt, and each edge with the
slice it's shared with, as the cut's middle on the map and on the dome.

**Second method.** Each slice's map is read back from its file and bent by its own angles: a
chain of rows from the hem up, each turned by the bend at its fold, then turned round the
dome's axis to the slice's place. That's set against the dome itself, by the ellipse and the
angle round and nothing of the chain: each corner against its place on the dome, each point of
each piece against the flat through its row's three corners, and each edge against its
neighbour's. All within `--tolerance`. How far the flat rows stand from the curved dome between
their corners is a reading, and a limit only where `--max-off` gives one.

**Rows against bends.** A slice folds at every lead line that runs all the way round, unless
`--rows` asks for fewer: then it folds at the lines that leave its rows nearest the dome, and
is flat across the rest. The reply says, for every count the map allows, how far the rows
stand from the dome and the sharpest bend, so the choice is seen. More rows than the map has
lines for come from the bands file: another band, or another row of a grid.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from shapely.geometry.polygon import orient

from . import piecemap
from .limits import over
from .piecemap import Map, Piece
from .pieces_dome import PICTURE_PX, Dome, dome_of, flush, on_grid
from .report import NoAnswer, Report

#: The bed a slice is laid on, in mm, where none is given.
BED = (256.0, 256.0)
#: How far a slice bent back may be from the dome's own corners and flats, in mm.
TOLERANCE = 0.01
#: A piece's point this near the line half a lead from a cut, in mm, is on it.
ON_EDGE = 5e-4
#: A piece may reach this far past that line, in mm: the map's own rounding.
PAST_LINE = 2e-3
#: A flat outline loses each point this near the line through its neighbours, in mm.
TIDY = 1e-3
#: Two slices whose outlines are this near each other, in mm, are the same slice.
SAME = 5e-4
#: A gap between the tops and bottoms of the map's pieces under this, in mm, isn't a line.
LEAST_GAP = 0.05
#: The rows stood off the dome are read at this many places across and up each row.
ACROSS = 9


class Rows:
    """A dome cut into `slices`, each a stack of flat rows between the map's folds.

    Levels run from the hem up: level 0 is the hem, the last the opening's edge, and row `i`
    lies between levels `i` and `i + 1`.
    """

    def __init__(self, dome: Dome, slices: int, fold_s: list[float]) -> None:
        self.dome, self.slices = dome, slices
        self.half_turn = math.pi / slices
        self.s = np.array([dome.length, *sorted(fold_s, reverse=True), 0.0])
        self.radius = np.asarray(dome.radius(self.s), dtype=float)
        self.z = np.asarray(dome.z(self.s), dtype=float)
        #: Half the row's width at each level, and how far its middle is from the axis.
        self.half = self.radius * math.sin(self.half_turn)
        self.out = self.radius * math.cos(self.half_turn)
        run, rise = self.out[:-1] - self.out[1:], self.z[1:] - self.z[:-1]
        self.long = np.hypot(run, rise)
        self.up = np.concatenate([[0.0], np.cumsum(self.long)])
        self.tilt = np.degrees(np.arctan2(rise, run))
        self.bend = self.tilt[:-1] - self.tilt[1:]
        # The angle between two neighbours' rows, across the cut they share: each row's
        # outward normal against its neighbour's, turned a slice round the axis.
        lean = np.radians(self.tilt)
        self.join = np.degrees(
            np.arccos(
                np.clip(
                    np.sin(lean) ** 2 * math.cos(2.0 * self.half_turn) + np.cos(lean) ** 2,
                    -1.0,
                    1.0,
                )
            )
        )

    @property
    def count(self) -> int:
        return len(self.long)

    def middle(self, k: int) -> float:
        """Slice `k`'s middle, as an angle round the dome."""
        return (2 * k + 1) * self.half_turn

    def row_of(self, s: Any) -> Any:
        """The row a place lies in, by how far down the dome it is."""
        rising = self.s[::-1]
        found = np.searchsorted(rising, s, side="right") - 1
        return self.count - 1 - np.clip(found, 0, self.count - 1)

    def flat(self, k: int, places: np.ndarray) -> np.ndarray:
        """Places on the dome, in slice `k`, as that slice laid flat: across from its middle,
        and up from the hem."""
        row = self.row_of(places[:, 1])
        share = (self.s[row] - places[:, 1]) / (self.s[row] - self.s[row + 1])
        half = self.half[row] + share * (self.half[row + 1] - self.half[row])
        across = (places[:, 0] - self.middle(k)) / self.half_turn * half
        return np.column_stack([across, self.up[row] + share * self.long[row]])

    def corner(self, k: int, side: int, level: int) -> np.ndarray:
        """Where a fold meets a cut, on the dome: slice `k`'s left (-1) or right (1) cut at a
        level, by the ellipse and the angle round."""
        phi = self.middle(k) + side * self.half_turn
        return np.array(
            [
                self.radius[level] * math.cos(phi),
                self.radius[level] * math.sin(phi),
                self.z[level],
            ]
        )


def folds_of(found: Map) -> list[tuple[float, float]]:
    """The lead lines that run all the way round: each stretch of the map's height that no
    piece reaches across, as its bottom and top."""
    spans = sorted((p.shape.bounds[1], p.shape.bounds[3]) for p in found.pieces)
    gaps = []
    reached = spans[0][1]
    for low, high in spans[1:]:
        if low - reached > LEAST_GAP:
            gaps.append((reached, low))
        reached = max(reached, high)
    return gaps


def _crossed(found: Map, dome: Dome, slices: int) -> None:
    """Refuse a cut that crosses glass, naming the pieces."""
    round_mm = dome.round_mm
    for k in range(1, slices):
        x = round_mm * k / slices
        across = sorted(
            p.id
            for p in found.pieces
            if p.shape.bounds[0] < x - 1e-6
            and p.shape.bounds[2] > x + 1e-6
            and p.shape.intersects(shapely.LineString([(x, -1.0), (x, dome.length + 1.0)]))
        )
        if across:
            repeats = found.surface.get("repeats")
            fits = (
                f" The map repeats {repeats} times round, so a count that divides {repeats} "
                "cuts along its repeats' own lines."
                if isinstance(repeats, int) and repeats % slices
                else ""
            )
            raise NoAnswer(
                f"cut {k + 1} of {slices}, {360.0 * k / slices:g} degrees round the dome at x = "
                f"{x:.2f} on the map, crosses piece {piecemap.numbered(across)}: a dome is cut "
                f"along lead lines that run from its top to its hem.{fits}"
            )


def _side(dome: Dome, at: float, way: int, half: float, own: np.ndarray) -> np.ndarray:
    """The glass's edge beside a cut, as places from the top to the hem: half a lead from the
    cut's middle, on the dome. It's the pieces' own points where they lie on it, so the panel's
    edge and theirs are the same points, and the line itself in between."""
    off = (own[:, 0] - at) * way * dome.radius(own[:, 1])

    def line(s: float) -> np.ndarray:
        return np.array([at + way * half / float(dome.radius(s)), s])

    kept = {round(float(p[1]), 9): p for p in own[np.abs(off - half) < ON_EDGE]}
    for end in (0.0, dome.length):
        if not any(abs(s - end) < 1e-6 for s in kept):
            kept[end] = line(end)
    order = sorted(kept)
    points = []
    for low, high in zip(order, order[1:], strict=False):
        points.append(kept[low])
        if high - low > 1.5:
            points.extend(
                line(float(s)) for s in np.linspace(low, high, math.ceil(high - low) + 1)[1:-1]
            )
    points.append(kept[order[-1]])
    return np.array(points)


def _tidy(ring: np.ndarray) -> Any:
    """A flat outline, less each point within `TIDY` of the line through its neighbours, on
    the grid a map's file keeps."""
    shape = shapely.Polygon(ring)
    tidy = shape.simplify(TIDY, preserve_topology=True)
    if tidy.is_valid and tidy.geom_type == "Polygon" and tidy.area > 0:
        shape = tidy
    return orient(on_grid(shape), 1.0)


def slice_of(found: Map, dome: Dome, rows: Rows, k: int, name: str) -> tuple[Map, list[np.ndarray]]:
    """Slice `k` of the dome's map, laid flat as its own map."""
    half = found.lead_mm / 2.0
    width = dome.round_mm / rows.slices
    left, right = 2.0 * k * rows.half_turn, 2.0 * (k + 1) * rows.half_turn
    mine = [
        p
        for p in found.pieces
        if int(shapely.get_coordinates(p.shape.representative_point())[0][0] // width) == k
    ]
    if not mine:
        raise NoAnswer(f"slice {k + 1} of {rows.slices} has no piece in it")
    outlines = [dome.places(shapely.get_coordinates(p.shape.exterior)[:-1]) for p in mine]
    every = np.vstack(outlines)
    for way, at in ((1, left), (-1, right)):
        short = (every[:, 0] - at) * way * dome.radius(every[:, 1]) - half
        if float(short.min()) < -PAST_LINE:
            worst = every[int(np.argmin(short))]
            raise NoAnswer(
                f"the line at the cut {math.degrees(at):g} degrees round the dome is "
                f"{2.0 * (half + float(short.min())):.2f} mm wide {worst[1]:.1f} mm down from "
                f"the top, narrower than the map's lead, {found.lead_mm:g} mm: a slice's edge "
                "is half a lead from its cut's middle"
            )
    west = _side(dome, left, 1, half, every)
    east = _side(dome, right, -1, half, every)
    region = shapely.Polygon(dome.chart(np.vstack([west, east[::-1]])))
    panel = piecemap.polygons(region.intersection(found.panel))
    if len(panel) != 1:
        raise NoAnswer(f"slice {k + 1}'s outline is in {len(panel)} parts, not one")
    panel_uv = rows.flat(k, dome.places(shapely.get_coordinates(panel[0].exterior)[:-1]))
    x0, y0 = float(panel_uv[:, 0].min()), float(panel_uv[:, 1].min())
    shift = np.array([x0, y0])
    pieces = []
    laid = [panel_uv - shift]
    for number, (piece, outline) in enumerate(zip(mine, outlines, strict=True), start=1):
        laid.append(rows.flat(k, outline) - shift)
        pieces.append(Piece(number, piece.colour, _tidy(laid[-1])))
    made = Map(
        flush(on_grid(shapely.Polygon(panel_uv - shift)), [p.shape for p in pieces]),
        found.lead_mm,
        {tint: dict(c) for tint, c in found.colours.items() if any(p.colour == tint for p in mine)},
        pieces,
    )
    hem = [round(-x0, 6), round(-y0, 6)]

    def edge(side: int, other: int) -> dict[str, Any]:
        return {
            "slice": other % rows.slices + 1,
            "half_line_mm": round(half, 4),
            "flat": [
                [
                    round(hem[0] + side * float(rows.half[j]), 6),
                    round(hem[1] + float(rows.up[j]), 6),
                ]
                for j in range(rows.count + 1)
            ],
            "on_dome": [
                [round(float(v), 6) for v in rows.corner(k, side, j)] for j in range(rows.count + 1)
            ],
            "join_deg": [round(float(v), 6) for v in rows.join],
        }

    made.surface = {
        "kind": "dome-slice",
        "of": name,
        "dome": {
            key: found.surface[key]
            for key in ("profile", "diameter_mm", "height_mm", "top_mm", "meridian_mm", "repeats")
            if key in found.surface
        },
        "slice": k + 1,
        "slices": rows.slices,
        "seen": "from outside the dome, the hem at the bottom",
        "turn_deg": round(math.degrees(rows.middle(k)), 6),
        "hem": {
            "at": hem,
            "out_mm": round(float(rows.out[0]), 6),
            "z_mm": round(float(rows.z[0]), 6),
            "tilt_deg": round(float(rows.tilt[0]), 6),
        },
        "rows": [
            {
                "row": i + 1,
                "y_mm": [
                    round(hem[1] + float(rows.up[i]), 6),
                    round(hem[1] + float(rows.up[i + 1]), 6),
                ],
                "tilt_deg": round(float(rows.tilt[i]), 6),
                "join_deg": round(float(rows.join[i]), 6),
            }
            for i in range(rows.count)
        ],
        "folds": [
            {
                "fold": i + 1,
                "y_mm": round(hem[1] + float(rows.up[i + 1]), 6),
                "bend_deg": round(float(rows.bend[i]), 6),
            }
            for i in range(rows.count - 1)
        ],
        "edges": {"left": edge(-1, k - 1), "right": edge(1, k + 1)},
        "dome_pieces": [p.id for p in mine],
    }
    made.source = {
        "by": "pieces flatten",
        "of": name,
        "slice": k + 1,
        "pieces": len(pieces),
        "glass_share_pct": round(piecemap.share(made), 4),
    }
    return made, laid


# A slice bent back, from what its file says


def bent_back(surface: dict[str, Any], xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A slice's flat points bent by its own angles and set round the dome: each point in
    space, and the row it's in. It reads the slice's `surface` and nothing else: the hem's
    place and tilt, then each fold's height and bend, a chain from the hem up."""
    hem = surface["hem"]
    folds = sorted(surface["folds"], key=lambda f: f["y_mm"])
    starts = np.array([hem["at"][1], *[f["y_mm"] for f in folds]])
    tilt = np.radians(
        hem["tilt_deg"] - np.concatenate([[0.0], np.cumsum([f["bend_deg"] for f in folds])])
    )
    out, z = [float(hem["out_mm"])], [float(hem["z_mm"])]
    for i in range(len(folds)):
        long = starts[i + 1] - starts[i]
        out.append(out[-1] - long * math.cos(tilt[i]))
        z.append(z[-1] + long * math.sin(tilt[i]))
    row = np.clip(np.searchsorted(starts, xy[:, 1], side="right") - 1, 0, len(starts) - 1)
    along = xy[:, 1] - starts[row]
    away = np.array(out)[row] - along * np.cos(tilt[row])
    high = np.array(z)[row] + along * np.sin(tilt[row])
    across = xy[:, 0] - hem["at"][0]
    turn = math.radians(surface["turn_deg"])
    return (
        np.column_stack(
            [
                away * math.cos(turn) - across * math.sin(turn),
                away * math.sin(turn) + across * math.cos(turn),
                high,
            ]
        ),
        row,
    )


def read_back(path: str, rows: Rows) -> dict[str, Any]:
    """One slice's file bent back, against the dome: how far its corners are from their places
    on the dome, its pieces' points from the flat through their row's corners, and where its
    edges come to."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    surface = data["surface"]
    k = int(surface["slice"]) - 1
    corners_off = 0.0
    edges = {}
    for side, name in ((-1, "left"), (1, "right")):
        back, _ = bent_back(surface, np.array(surface["edges"][name]["flat"], dtype=float))
        edges[name] = back
        for level in range(rows.count + 1):
            corners_off = max(
                corners_off, float(np.linalg.norm(back[level] - rows.corner(k, side, level)))
            )
    flats_off = 0.0
    for piece in data["pieces"]:
        back, row = bent_back(surface, np.array(piece["outline"], dtype=float))
        for i in np.unique(row):
            a, b, c = rows.corner(k, -1, i), rows.corner(k, 1, i), rows.corner(k, -1, i + 1)
            normal = np.cross(b - a, c - a)
            normal /= np.linalg.norm(normal)
            flats_off = max(flats_off, float(np.abs((back[row == i] - a) @ normal).max()))
    return {"corners_off_mm": corners_off, "flats_off_mm": flats_off, "edges": edges}


def span_off(dome: Dome, slices: int, low_s: float, high_s: float) -> float:
    """How far one flat row stands from the dome it stands for, at its furthest: the row
    between two heights down the dome, at each place across and up it, on the row and on the
    dome."""
    half_turn = math.pi / slices
    across, share = np.meshgrid(np.linspace(-1.0, 1.0, ACROSS), np.linspace(0.0, 1.0, ACROSS))
    ends = []
    for s in (low_s, high_s):
        radius, z = float(dome.radius(s)), float(dome.z(s))
        ends.append(
            np.stack(
                [
                    radius * math.cos(half_turn) + 0.0 * across,
                    across * radius * math.sin(half_turn),
                    z + 0.0 * across,
                ],
                axis=-1,
            )
        )
    on_row = (1.0 - share)[..., None] * ends[0] + share[..., None] * ends[1]
    on_dome = dome.point(across * half_turn, low_s - share * (low_s - high_s))
    return float(np.linalg.norm(on_row - on_dome, axis=-1).max())


def off_dome(rows: Rows) -> dict[str, Any]:
    """The furthest any of a slice's rows stands from the dome, and which row."""
    each = [
        span_off(rows.dome, rows.slices, float(rows.s[i]), float(rows.s[i + 1]))
        for i in range(rows.count)
    ]
    worst = int(np.argmax(each))
    return {"mm": each[worst], "row": worst + 1}


def choices(dome: Dome, slices: int, lines: list[float]) -> dict[int, dict[str, Any]]:
    """For each count of rows the map's lines allow, the lines to fold at that leave the rows
    nearest the dome: a slice folds at some or all of the lead lines that run all the way
    round, and is flat across the rest. Every way of choosing is tried, a row at a time."""
    levels = [dome.length, *sorted(lines, reverse=True), 0.0]
    most = len(levels) - 1
    off = {
        (i, j): span_off(dome, slices, levels[i], levels[j])
        for i in range(most)
        for j in range(i + 1, most + 1)
    }
    best: dict[tuple[int, int], tuple[float, list[int]]] = {(0, 0): (0.0, [])}
    for count in range(1, most + 1):
        for j in range(count, most + 1):
            ways = [
                (max(best[(count - 1, i)][0], off[(i, j)]), [*best[(count - 1, i)][1], i])
                for i in range(count - 1, j)
                if (count - 1, i) in best
            ]
            best[(count, j)] = min(ways)
    found = {}
    for count in range(1, most + 1):
        mm, starts = best[(count, most)]
        folds = [levels[i] for i in starts[1:]]
        rows = Rows(dome, slices, folds)
        found[count] = {
            "rows": count,
            "folds_s": folds,
            "off_dome_mm": mm,
            "most_bend_deg": float(rows.bend.max()) if count > 1 else 0.0,
        }
    return found


def _fits(size: list[float], bed: tuple[float, float]) -> bool:
    w, h = size
    return (not over(w, bed[0]) and not over(h, bed[1])) or (
        not over(w, bed[1]) and not over(h, bed[0])
    )


def _near(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether two outlines are the same, to within `SAME` all the way round."""
    if a.shape == b.shape and float(np.abs(a - b).max()) < SAME:
        return True
    return bool(shapely.hausdorff_distance(shapely.LinearRing(a), shapely.LinearRing(b)) < SAME)


def _alike(maps: list[Map], laid: list[list[np.ndarray]]) -> list[list[int]]:
    """Slices that are the same, point for point within `SAME`, as they were laid flat: one
    check and one build stand for each group. Each takes its group's first slice's outlines,
    so the files are the same too."""
    groups: list[list[int]] = []
    for number, one in enumerate(maps, start=1):
        for group in groups:
            first = maps[group[0] - 1]
            mine, theirs = laid[number - 1], laid[group[0] - 1]
            if (
                len(mine) == len(theirs)
                and [p.colour for p in one.pieces] == [p.colour for p in first.pieces]
                and all(_near(a, b) for a, b in zip(mine, theirs, strict=True))
            ):
                one.panel = first.panel
                for piece, other in zip(one.pieces, first.pieces, strict=True):
                    piece.shape = other.shape
                group.append(number)
                break
        else:
            groups.append([number])
    return groups


def paint(draw_on: Any, one: Map, left: float, base: float, scale: float, font: Any) -> None:
    """One flat slice on a picture, its bottom-left at `left`, `base`: each piece in its
    colour and numbered, and each fold as a line across it with its bend."""

    def px(points: Any) -> list[tuple[float, float]]:
        return [(left + x * scale, base - y * scale) for x, y in points]

    draw_on.polygon(px(one.panel.exterior.coords), fill=(45, 45, 45))
    for piece in one.pieces:
        rgb = (one.colours.get(piece.colour) or {}).get("rgb") or (200, 200, 200)
        draw_on.polygon(px(piece.shape.exterior.coords), fill=tuple(int(v) for v in rgb))
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
    right = one.panel.bounds[2]
    for fold in one.surface["folds"]:
        (xa, ya), (xb, _yb) = px([(0.0, fold["y_mm"]), (right, fold["y_mm"])])
        draw_on.line([(xa - 4, ya), (xb + 4, ya)], fill=(255, 0, 255), width=1)
        draw_on.text(
            (xb + 6, ya), f"{fold['bend_deg']:.1f}°", fill=(160, 0, 160), font=font, anchor="lm"
        )


def draw(maps: list[tuple[int, Map]], out: Path, name: str) -> str:
    """Each different slice laid flat, each piece in its colour and numbered, its folds as
    lines across it with the bend at each."""
    from PIL import Image, ImageDraw, ImageFont

    margin, gap, key_px = 16, 60, 22
    tall = max(m.panel.bounds[3] for _n, m in maps)
    wide = sum(m.panel.bounds[2] for _n, m in maps)
    scale = min(6.0, (PICTURE_PX - 2 * margin - gap * len(maps)) / wide, 1100.0 / tall)
    width = max(640, int(wide * scale) + 2 * margin + gap * len(maps))
    height = int(tall * scale) + 2 * margin + key_px + 16
    picture = Image.new("RGB", (width, height), "white")
    draw_on = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=12)
    left = float(margin)
    for number, one in maps:
        paint(draw_on, one, left, height - key_px - margin, scale, font)
        middle = left + one.panel.bounds[2] * scale / 2.0
        draw_on.text(
            (middle, margin / 2 + 2), f"slice {number}", fill=(0, 0, 0), font=font, anchor="mm"
        )
        left += one.panel.bounds[2] * scale + gap
    key = "each slice laid flat, seen from outside, hem down. Magenta: a fold, and its bend"
    draw_on.text((margin, height - key_px + 3), key, fill=(0, 0, 0), font=font)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-slices.png"
    picture.save(target)
    return str(target)


def run_flatten(args: Any, report: Report) -> None:
    found = piecemap.load(args.map)
    dome, surface = dome_of(found, args.map)
    slices = args.slices if args.slices is not None else surface.get("repeats")
    if not isinstance(slices, int) or slices < 3:
        raise NoAnswer("--slices is how many slices the dome is cut into, 3 or more")
    tolerance = args.tolerance
    if not tolerance > 0:
        raise NoAnswer(
            "--tolerance is how far a slice bent back may be off the dome, in mm, over 0"
        )
    _crossed(found, dome, slices)
    gaps = folds_of(found)
    lines = [dome.length - (low + high) / 2.0 for low, high in gaps]
    choice = choices(dome, slices, lines)
    wanted = args.rows if args.rows is not None else len(choice)
    if wanted not in choice:
        raise NoAnswer(
            f"--rows {wanted}: the map has {len(lines)} lead line(s) that run all the way "
            f"round, so a slice is 1 to {len(choice)} flat row(s). Another band, or another "
            "row of a grid, in the bands file adds a line"
        )
    rows = Rows(dome, slices, choice[wanted]["folds_s"])
    stem = args.to or Path(args.map).name.removesuffix(".json").removesuffix(".pieces")
    name = Path(args.map).name
    cut = [slice_of(found, dome, rows, k, name) for k in range(slices)]
    maps = [made for made, _laid in cut]
    alike = _alike(maps, [laid for _made, laid in cut])
    files = []
    for number, one in enumerate(maps, start=1):
        one.source["glass_share_pct"] = round(piecemap.share(one), 4)
        target = f"{stem}.slice-{number:02d}.pieces.json"
        piecemap.save(one, target)
        report.wrote(target)
        files.append(target)
    same = {number: group[0] for group in alike for number in group}
    report.fact("slices_count", slices, how="cut along lead lines from the top to the hem")
    report.numbers["maps"] = files
    report.line(
        f"maps: {files[0]} to {files[-1]}, each a flat piece map, seen from outside the dome "
        "with its hem at the bottom"
    )
    stood = off_dome(rows)
    report.fact(
        "rows_count",
        rows.count,
        how=f"flat rows in each slice, of the {len(choice)} the map's {len(lines)} lead "
        "line(s) all the way round allow: --rows, the most unless given"
        + (
            f". It folds at {rows.count - 1} of those lines, the ones that leave its rows "
            "nearest the dome, and is flat across the rest"
            if rows.count < len(choice)
            else ". It folds at every one"
        ),
    )
    report.fact(
        "rows_off_dome_mm",
        stood["mm"],
        "mm",
        f"the furthest a flat row stands from the curved dome it's cut from, in row "
        f"{stood['row']}: its corners are on the dome, and between them it's flat"
        + (f"; --max-off {args.max_off:g}" if args.max_off is not None else ""),
    )
    report.numbers["rows_off_dome_at"] = {"row": stood["row"]}
    report.numbers["rows_choice"] = [
        {k: v for k, v in one.items() if k != "folds_s"} for one in choice.values()
    ]
    report.line(
        "rows against bends, each count's furthest off the dome and its sharpest bend: "
        + "; ".join(
            f"{one['rows']} row(s) {one['off_dome_mm']:.2f} mm"
            + (f", {one['most_bend_deg']:.1f}°" if one["rows"] > 1 else ", no fold")
            for one in choice.values()
        )
        + ". More slices, or more lines round the dome in the bands file, bring it nearer"
    )
    sizes = [
        [
            round(m.panel.bounds[2] - m.panel.bounds[0], 3),
            round(m.panel.bounds[3] - m.panel.bounds[1], 3),
        ]
        for m in maps
    ]
    bed = args.bed or BED
    largest = max(sizes, key=lambda s: s[0] * s[1])
    fit = [_fits(size, bed) for size in sizes]
    report.fact(
        "slice_mm",
        largest,
        how="the largest slice's flat outline, across and from hem to top: its glass's edge, "
        "before a build's border",
    )
    report.numbers["bed_mm"] = list(bed)
    report.fact(
        "fits_bed",
        all(fit),
        how=f"each slice's outline on a bed {bed[0]:g} by {bed[1]:g} mm, either way round"
        + (
            ""
            if all(fit)
            else f": slice {piecemap.numbered([k + 1 for k, f in enumerate(fit) if not f])} doesn't"
        ),
    )
    report.numbers["slices"] = [
        {
            "slice": number,
            "map": files[number - 1],
            "pieces_count": len(one.pieces),
            "size_mm": sizes[number - 1],
            "fits_bed": fit[number - 1],
            "glass_share_pct": one.source["glass_share_pct"],
            "turn_deg": one.surface["turn_deg"],
            "same_as": same[number],
        }
        for number, one in enumerate(maps, start=1)
    ]
    report.numbers["alike"] = alike
    if len(alike) == 1:
        report.line(
            f"alike: all {slices} slices are the same map, piece for piece: one check and one "
            f"build of {files[0]} stand for every slice"
        )
    else:
        report.line(
            "alike: "
            + "; ".join(f"slice {piecemap.numbered(g)}" for g in alike[:8])
            + (f"; and {len(alike) - 8} more group(s)" if len(alike) > 8 else "")
            + ": the slices of a group are the same map, piece for piece"
        )
    first = maps[0].surface
    report.numbers["rows"] = first["rows"]
    report.numbers["folds"] = first["folds"]
    report.numbers["join_deg"] = [row["join_deg"] for row in first["rows"]]
    report.line(
        "rows, from the hem up, each its height on the map, its tilt from level, and the angle "
        "between it and a neighbour's across a cut: "
        + "; ".join(
            f"row {r['row']} y {r['y_mm'][0]:.2f} to {r['y_mm'][1]:.2f}, {r['tilt_deg']:.1f}°, "
            f"{r['join_deg']:.1f}°"
            for r in first["rows"]
        )
    )
    if first["folds"]:
        report.line(
            "folds, each a lead line across the slice and the angle it bends through, toward "
            "the dome's axis: "
            + "; ".join(
                f"fold {f['fold']} at y = {f['y_mm']:.2f} mm, {f['bend_deg']:.2f}°"
                for f in first["folds"]
            )
        )
    else:
        report.line(
            "folds: none. No lead line runs all the way round, so each slice is one flat row"
        )
    report.fact(
        "lead_flat_mm",
        piecemap.usual_lead(maps[0]),
        "mm",
        f"the lead's usual width on slice 1 laid flat, its area over its lines' length, where "
        f"the dome's is {found.lead_mm:g}: a flat row is a little narrower than the dome it "
        "stands for",
    )
    report.line(
        f"edges: a slice's panel ends at its glass, {found.lead_mm / 2.0:g} mm inside each "
        "cut's middle. Each map's `surface.edges` has the slice each side is shared with, and "
        "the cut's middle on the map and on the dome, a point at each fold"
    )
    report.line(
        f"next: `pieces check` and `pieces build ... --border {found.lead_mm / 2.0:g}` on each "
        "different slice: a border of half the lead puts the frame's edge on the cut's middle, "
        "so two slices meet in one line as wide as the lead"
    )
    # The second method: each file bent back by its own angles, against the dome.
    backs = [read_back(target, rows) for target in files]
    corners_off = max(b["corners_off_mm"] for b in backs)
    flats_off = max(b["flats_off_mm"] for b in backs)
    edges_off = max(
        float(
            np.linalg.norm(
                backs[k]["edges"]["right"] - backs[(k + 1) % slices]["edges"]["left"], axis=1
            ).max()
        )
        for k in range(slices)
    )
    report.numbers["tolerance_mm"] = tolerance
    report.numbers["corners_off_mm"] = corners_off
    report.numbers["flats_off_mm"] = flats_off
    report.numbers["edges_off_mm"] = edges_off
    report.second(
        f"each slice's file bent back by its own angles, a chain of rows from the hem up, and "
        f"set round the dome: its corners, where a fold meets a cut, are within "
        f"{corners_off:.5f} mm of their places on the dome by the ellipse and the angle round; "
        f"each piece's points within {flats_off:.5f} mm of the flat through their row's "
        f"corners; tolerance {tolerance:g} mm"
    )
    report.second(
        f"each slice's right edge bent back lies on its neighbour's left to within "
        f"{edges_off:.5f} mm, at every fold"
    )
    shown = draw([(group[0], maps[group[0] - 1]) for group in alike[:6]], Path(args.out), stem)
    report.wrote(shown)
    report.numbers["picture"] = shown
    differs = []
    if over(max(corners_off, flats_off), tolerance):
        differs.append(
            f"bent back by its angles a slice is {max(corners_off, flats_off):.4f} mm off the "
            f"dome's own corners and flats, over the tolerance, {tolerance:g} mm"
        )
    if over(edges_off, tolerance):
        differs.append(
            f"two neighbours' shared edges are {edges_off:.4f} mm apart bent back, over the "
            f"tolerance, {tolerance:g} mm"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
        return
    wrong = []
    if not all(fit):
        wrong.append(
            f"slice {piecemap.numbered([k + 1 for k, f in enumerate(fit) if not f])} is "
            f"{largest[0]:g} by {largest[1]:g} mm laid flat, and the bed is {bed[0]:g} by "
            f"{bed[1]:g}: more slices are narrower"
        )
    if args.max_off is not None and over(stood["mm"], args.max_off):
        wrong.append(
            f"a flat row stands {stood['mm']:.2f} mm from the dome, over --max-off "
            f"{args.max_off:g}: more slices, or more lines round the dome, bring it nearer"
        )
    if wrong:
        report.verdict("FAIL", "; ".join(wrong))
        return
    report.verdict(
        "OK",
        f"{slices} slice(s) of {rows.count} flat row(s) of {len(choice)}, each {largest[0]:g} "
        f"by {largest[1]:g} mm, in {files[0]} to {files[-1]}; bent back by their angles they "
        "give the dome's "
        f"corners back within {max(corners_off, flats_off, edges_off):.5f} mm, and stand "
        f"{stood['mm']:.2f} mm from its curve at the furthest",
    )
