"""The piece map: a stained-glass panel as closed pieces of glass, each with its colour, and the
lead lines between them (mqucifer/print-shop#152). `docs/pieces.md` is the format, written once.

A piece map is the handover between what draws a panel's pieces and what fixes and builds them.
Two things draw one: a traced picture (`pieces trace`), and rules for a repeated pattern
(mqucifer/print-shop#154). Every `pieces` command reads and writes it through this module, so
both ways in share everything after the pieces are drawn.

**What it holds.** JSON, in millimetres, x to the right and y up from the panel's bottom-left:

    {"piece_map": 1,
     "panel": {"width_mm": 220.0, "height_mm": 160.0},
     "lead_mm": 1.4,
     "colours": {"blue": {"rgb": [40, 110, 220]}},
     "pieces": [{"id": 1, "colour": "blue", "outline": [[x, y], ...], "holes": [[[x, y], ...]]}],
     "flags": [...], "source": {...}, "history": [...], "surface": {...}}

- **A piece is its glass:** the outline is where the glass meets the lead, so the lead is the
  panel less every piece, and no line is stored twice. Two pieces never overlap. A map from rules
  writes each cell inset by half its lead.
- **`lead_mm`** is the lead line's usual width: what a new line, from `pieces split` or `pieces
  extend`, is drawn at.
- **`flags`** are the rule breaks `pieces check` last read, and **`source`** says what drew the
  map and its glass share then, which every later reading is said against. Both are for a
  reader: no command trusts them, and `pieces check` reads the pieces again.
- **`surface`** is there only where the map isn't a flat panel's: a dome's chart, as `pieces
  dome` writes it, or one flat slice of a dome with its folds and the edges it shares with
  its neighbours, as `pieces flatten` does (mqucifer/print-shop#154). Every command keeps it.

This module is the geometry every command shares: the lead, the glass share, the lead's widest
point, and the map as pixels, which is each reading's second method.
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

from .report import NoAnswer

VERSION = 1
#: A map's own figures are kept to this many places: a tenth of a micron.
PLACES = 4
#: Two pieces that share more than this, in mm², overlap.
OVERLAP_MM2 = 1e-3
#: A piece may reach this far past the panel's edge, in mm: a traced outline ends on it.
PAST_EDGE = 0.01
#: The most pixels the map is drawn at for the second readings, and the finest it's ever drawn.
MOST_PX = 1_500_000
FINEST_PX_PER_MM = 12.0
FORMAT = (
    'a piece map is JSON: {"piece_map": 1, "panel": {"width_mm": W, "height_mm": H}, "lead_mm": '
    'L, "colours": {NAME: {"rgb": [r, g, b]}}, "pieces": [{"id": N, "colour": NAME, "outline": '
    '[[x, y], ...], "holes": [[[x, y], ...]]}]}, in mm, x right and y up from the panel\'s '
    "bottom-left. `pieces trace` writes one from a picture"
)


@dataclass
class Piece:
    """One piece of glass: its outline is where the glass meets the lead."""

    id: int
    colour: str
    shape: Any
    thickness_mm: float | None = None
    #: From a trace: the picture's own colour over the piece, and how far it runs across it.
    paint: dict[str, Any] | None = None


@dataclass
class Map:
    panel: Any
    lead_mm: float
    colours: dict[str, dict[str, Any]]
    pieces: list[Piece]
    source: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    #: What the map lies on, where that isn't a flat panel: a dome's chart, or one flat slice
    #: of a dome with its folds and edges (`pieces dome`, `pieces flatten`). Empty for a panel.
    surface: dict[str, Any] = field(default_factory=dict)

    def piece(self, number: int) -> Piece:
        for one in self.pieces:
            if one.id == number:
                return one
        raise NoAnswer(
            f"the map has no piece {number}: its pieces are {numbered([p.id for p in self.pieces])}"
        )

    def glass(self) -> Any:
        """Every piece, as one shape."""
        return shapely.unary_union([p.shape for p in self.pieces])

    def lead(self) -> Any:
        """The lead: the panel less every piece."""
        return self.panel.difference(self.glass())

    def next_id(self) -> int:
        return max((p.id for p in self.pieces), default=0) + 1


def numbered(ids: list[int]) -> str:
    """Piece numbers as a line says them: `1 to 58`, or each where they aren't a run."""
    ids = sorted(ids)
    if len(ids) > 6 and ids == list(range(ids[0], ids[-1] + 1)):
        return f"{ids[0]} to {ids[-1]}"
    shown = ", ".join(str(i) for i in ids[:40])
    return shown + (f", and {len(ids) - 40} more" if len(ids) > 40 else "")


def _ring(given: Any, what: str) -> list[tuple[float, float]]:
    try:
        ring = [(float(x), float(y)) for x, y in given]
    except (TypeError, ValueError) as exc:
        raise NoAnswer(f"{what} isn't a list of [x, y] points: {FORMAT}") from exc
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3 or not all(math.isfinite(v) for xy in ring for v in xy):
        raise NoAnswer(f"{what} needs three points or more, each a number")
    return ring


def _positive(given: Any, what: str) -> float:
    if isinstance(given, bool) or not isinstance(given, int | float) or not given > 0:
        raise NoAnswer(f"{what} is a number over 0, in mm: {FORMAT}")
    return float(given)


def read(data: Any, name: str = "the map") -> Map:
    """A map from its JSON, or what's wrong with it."""
    if not isinstance(data, dict) or data.get("piece_map") != VERSION:
        raise NoAnswer(f"{name} isn't a piece map: {FORMAT}")
    panel = data.get("panel")
    if not isinstance(panel, dict):
        raise NoAnswer(f"{name} has no `panel`: {FORMAT}")
    if "outline" in panel:
        outline = shapely.Polygon(_ring(panel["outline"], "the panel's outline"))
    else:
        width = _positive(panel.get("width_mm"), "the panel's `width_mm`")
        height = _positive(panel.get("height_mm"), "the panel's `height_mm`")
        outline = shapely.box(0.0, 0.0, width, height)
    if not outline.is_valid or outline.area <= 0:
        raise NoAnswer("the panel's outline crosses itself")
    colours = data.get("colours") or {}
    if not isinstance(colours, dict):
        raise NoAnswer(f"`colours` is each colour's name and its rgb: {FORMAT}")
    given = data.get("pieces")
    if not isinstance(given, list) or not given:
        raise NoAnswer(f"{name} has no `pieces`: {FORMAT}")
    pieces: list[Piece] = []
    for entry in given:
        if not isinstance(entry, dict) or isinstance(entry.get("id"), bool):
            raise NoAnswer(f"each piece is an object with an `id`: {FORMAT}")
        number = entry.get("id")
        if not isinstance(number, int) or number < 1:
            raise NoAnswer(f"a piece's `id` is a whole number from 1: {number!r} isn't")
        if any(p.id == number for p in pieces):
            raise NoAnswer(f"two pieces have the id {number}: each piece has its own")
        shape = shapely.Polygon(
            _ring(entry.get("outline"), f"piece {number}'s outline"),
            [_ring(h, f"a hole of piece {number}") for h in entry.get("holes") or []],
        )
        if not shape.is_valid:
            raise NoAnswer(
                f"piece {number}'s outline isn't a closed piece: {shapely.is_valid_reason(shape)}"
            )
        thickness = entry.get("thickness_mm")
        pieces.append(
            Piece(
                number,
                str(entry.get("colour") or ""),
                orient(shape, 1.0),
                None if thickness is None else _positive(thickness, f"piece {number}'s thickness"),
                entry.get("paint") if isinstance(entry.get("paint"), dict) else None,
            )
        )
    found = Map(
        orient(outline, 1.0),
        _positive(data.get("lead_mm"), "`lead_mm`, the lead line's usual width"),
        {str(k): dict(v) if isinstance(v, dict) else {} for k, v in colours.items()},
        pieces,
        dict(data.get("source") or {}),
        list(data.get("history") or []),
        list(data.get("flags") or []),
        dict(data.get("surface") or {}) if isinstance(data.get("surface"), dict) else {},
    )
    _held(found)
    return found


def _held(found: Map) -> None:
    """A map's own rules: every piece inside the panel, and no two overlapping."""
    reach = found.panel.buffer(PAST_EDGE)
    outside = [p.id for p in found.pieces if not reach.covers(p.shape)]
    if outside:
        raise NoAnswer(
            f"piece {numbered(outside)} reaches past the panel's edge: every piece lies inside it"
        )
    tree = shapely.STRtree([p.shape for p in found.pieces])
    for a, b in tree.query([p.shape for p in found.pieces], predicate="intersects").T:
        if a < b:
            shared = found.pieces[a].shape.intersection(found.pieces[b].shape).area
            if shared > OVERLAP_MM2:
                raise NoAnswer(
                    f"pieces {found.pieces[a].id} and {found.pieces[b].id} overlap by "
                    f"{shared:.3f} mm²: a piece is its glass, and the lead lies between two"
                )


def load(path: str) -> Map:
    found = Path(path)
    if not found.is_file():
        raise NoAnswer(f"no file {path} in the working directory")
    try:
        data = json.loads(found.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise NoAnswer(f"{path} isn't JSON: {exc}") from exc
    return read(data, path)


def _points(ring: Any) -> list[list[float]]:
    return [[round(float(x), PLACES), round(float(y), PLACES)] for x, y in ring.coords[:-1]]


def plain(found: Map) -> dict[str, Any]:
    """The map as its JSON."""
    x0, y0, x1, y1 = found.panel.bounds
    box = shapely.box(x0, y0, x1, y1)
    rectangle = x0 == 0 and y0 == 0 and found.panel.symmetric_difference(box).area < 1e-9
    panel: dict[str, Any] = {
        "width_mm": round(x1 - x0, PLACES),
        "height_mm": round(y1 - y0, PLACES),
    }
    if not rectangle:
        panel["outline"] = _points(found.panel.exterior)
    pieces = []
    for one in sorted(found.pieces, key=lambda p: p.id):
        entry: dict[str, Any] = {
            "id": one.id,
            "colour": one.colour,
            "outline": _points(one.shape.exterior),
        }
        if one.shape.interiors:
            entry["holes"] = [_points(ring) for ring in one.shape.interiors]
        if one.thickness_mm is not None:
            entry["thickness_mm"] = one.thickness_mm
        if one.paint:
            entry["paint"] = one.paint
        pieces.append(entry)
    data = {
        "piece_map": VERSION,
        "units": "mm",
        "panel": panel,
        "lead_mm": round(found.lead_mm, PLACES),
        "colours": found.colours,
        "pieces": pieces,
        "flags": found.flags,
        "source": found.source,
        "history": found.history,
    }
    if found.surface:
        data["surface"] = found.surface
    return data


def save(found: Map, path: str) -> None:
    """Write the map, read back first: a command never writes a map the next can't read. Each
    outline is put on the grid the file keeps it to, so rounding can't make one cross itself."""
    for piece in found.pieces:
        kept = polygons(shapely.set_precision(piece.shape, 10.0**-PLACES))
        if kept:
            piece.shape = kept[0]
    data = plain(found)
    read(json.loads(json.dumps(data)), path)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def polygons(shape: Any) -> list[Any]:
    """Every polygon with area in a shape, largest first."""
    found: list[Any] = []
    todo = [shape]
    while todo:
        one = todo.pop()
        if one.geom_type == "Polygon":
            if one.area > 0:
                found.append(orient(one, 1.0))
        elif hasattr(one, "geoms"):
            todo.extend(one.geoms)
    return sorted(found, key=lambda p: -p.area)


def xy(at: Any) -> list[float]:
    return [round(float(at[0]), 3), round(float(at[1]), 3)]


def said_at(at: Any) -> str:
    return f"({float(at[0]):.1f}, {float(at[1]):.1f})"


# The readings every command gives


def share(found: Map) -> float:
    """The glass share: the pieces' area over the panel's, in percent."""
    return float(100.0 * sum(p.shape.area for p in found.pieces) / found.panel.area)


def widest(shape: Any, tolerance: float = 0.01) -> tuple[float, list[float]]:
    """The widest point of a shape: the largest circle that fits in it, as its diameter and
    centre."""
    best, at = 0.0, [0.0, 0.0]
    for poly in polygons(shape):
        # A circle wider than this one's box can't be in it.
        x0, y0, x1, y1 = poly.bounds
        if min(x1 - x0, y1 - y0) <= best:
            continue
        line = shapely.maximum_inscribed_circle(poly, tolerance=tolerance)
        if line.is_empty:
            continue
        across = 2.0 * float(line.length)
        if across > best:
            best, at = across, xy(shapely.get_coordinates(line)[0])
    return best, at


@dataclass
class Pixels:
    """The map as pixels, each piece's id at each pixel's centre it covers: 0 is lead, and -1
    outside the panel."""

    ids: np.ndarray
    px_per_mm: float
    x0: float
    y1: float

    @property
    def mm(self) -> float:
        return 1.0 / self.px_per_mm

    def at(self, row: float, col: float) -> list[float]:
        """A pixel's centre, in the map's millimetres."""
        return [
            round(self.x0 + (col + 0.5) * self.mm, 3),
            round(self.y1 - (row + 0.5) * self.mm, 3),
        ]


def _cover(shape: Any, x0: float, y1: float, mm: float, size: tuple[int, int]) -> tuple[Any, Any]:
    """The pixels whose centres a shape covers, as a window of the picture and a mask in it."""
    height, width = size
    bx0, by0, bx1, by1 = shape.bounds
    c0 = max(0, int(math.floor((bx0 - x0) / mm)))
    c1 = min(width, int(math.ceil((bx1 - x0) / mm)) + 1)
    r0 = max(0, int(math.floor((y1 - by1) / mm)))
    r1 = min(height, int(math.ceil((y1 - by0) / mm)) + 1)
    if c1 <= c0 or r1 <= r0:
        return (slice(0, 0), slice(0, 0)), np.zeros((0, 0), dtype=bool)
    xs = x0 + (np.arange(c0, c1) + 0.5) * mm
    ys = y1 - (np.arange(r0, r1) + 0.5) * mm
    grid_x, grid_y = np.meshgrid(xs, ys)
    shapely.prepare(shape)
    return (slice(r0, r1), slice(c0, c1)), shapely.contains_xy(shape, grid_x, grid_y)


def pixels(found: Map, px_per_mm: float | None = None) -> Pixels:
    """The map drawn as pixels, by which pixel centres each piece covers: no drawing library's
    edges, so a piece's pixels are its area to a pixel's width round its outline."""
    x0, y0, x1, y1 = found.panel.bounds
    if px_per_mm is None:
        px_per_mm = min(FINEST_PX_PER_MM, math.sqrt(MOST_PX / ((x1 - x0) * (y1 - y0))))
    mm = 1.0 / px_per_mm
    size = (max(1, round((y1 - y0) * px_per_mm)), max(1, round((x1 - x0) * px_per_mm)))
    ids = np.zeros(size, dtype=np.int32)
    window, inside = _cover(found.panel, x0, y1, mm, size)
    ids[:] = -1
    ids[window][inside] = 0
    for one in found.pieces:
        window, inside = _cover(one.shape, x0, y1, mm, size)
        ids[window][inside] = one.id
    return Pixels(ids, px_per_mm, x0, y1)


def share_by_pixels(drawn: Pixels) -> float:
    panel = int((drawn.ids >= 0).sum())
    return 100.0 * int((drawn.ids > 0).sum()) / max(panel, 1)


def lead_widest_by_pixels(drawn: Pixels) -> tuple[float, list[float]]:
    """The lead's widest point by its pixels: each lead pixel's distance to the nearest that
    isn't lead, a piece or the panel's edge."""
    from scipy import ndimage

    lead = np.pad(drawn.ids == 0, 1)
    if not lead.any():
        return 0.0, [0.0, 0.0]
    far = ndimage.distance_transform_edt(lead)[1:-1, 1:-1]
    row, col = np.unravel_index(int(np.argmax(far)), far.shape)
    # A pixel's distance is to the nearest other pixel's centre: half a pixel more to its edge
    # on each side is the disc's diameter less one pixel.
    across = max(0.0, 2.0 * float(far[row, col]) - 1.0) * drawn.mm
    return across, drawn.at(float(row), float(col))


def usual_lead(found: Map) -> float:
    """The lead line's usual width: the lead's area over its lines' length, each line having a
    piece's edge on both sides, or one where it runs along the panel's edge."""
    lead = found.lead()
    if lead.is_empty:
        return 0.0
    edge = found.panel.exterior.buffer(PAST_EDGE)
    inner = sum(p.shape.boundary.difference(edge).length for p in found.pieces)
    along_edge = lead.boundary.intersection(edge).length
    length = (inner + along_edge) / 2.0
    return float(lead.area / length) if length > 0 else 0.0


def closed(shape: Any, reach: float) -> Any:
    """A shape with every gap in it narrower than twice `reach` filled, its corners kept: out
    and back in by `reach` with mitred joins, so a straight side stays straight to its corner
    and two sides that run on to each other meet as lines, not as a round."""
    out = shape.buffer(reach, join_style="mitre", mitre_limit=20.0)
    return out.buffer(-reach, join_style="mitre", mitre_limit=20.0)
