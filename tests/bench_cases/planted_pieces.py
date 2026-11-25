"""Stained-glass pictures and piece maps for `tests/test_pieces.py`, drawn in code: nothing here
is a Sponsor's scene or anything from one (mqucifer/print-shop#152, mqucifer/print-shop#153).

- `clean.png`: five flat cells between straight lines, 120 x 80 mm at 5 pixels a mm, in a frame.
- `broken.png`: the same panel with one of each rule break `pieces check` reads.
- `hills.png`: curved bands, slanted uprights and a round sun, for curves built as curves.
- `thin_line.png`: curved bands and uprights at 5 pixels a mm, their lines 4 pixels wide but
  one band's, which is one pixel wide (mqucifer/print-shop#161).
- `speckled.png`: that picture with every line 4 pixels wide, and five dark specks two pixels
  across inside two of its curved pieces.
- `margin.png`: the clean panel with a white margin round its frame, specks in the margin, and
  the frame's top uneven.
- `specks.png`: the clean panel with clusters of dark specks inside its cells, and one real line
  that ends in glass.
- `deep.json`: the rules' map with a line that all but crosses one cell.
- `rules.json`: a map as rules would write one, four cells each inset by half the lead.
- `slit.json`: that map with a lead line ending in one cell's glass, and a line inside another.
- `slanted.json`: a neck whose narrowest line runs on a slant between two offset notches, which
  rays square to its edges read wider at the same place (mqucifer/print-shop#161).
- `spiked.json`: a notch that ends in a spike thinner than a pixel, with the limit between its
  depth by its outline and by its pixels.
- `thin_lines.json`: a panel big enough that a pixel is 0.18 mm, with a dead end in a narrow
  piece, its neighbours nearer than the pixels look round, and a line inside a piece closer to
  that piece's edge than a pixel.
- `quiet.json`: a spur under the neck limit and a spiked notch shallower than its mouth, neither
  of which any rule flags.
- `edge.json`: two pieces on the panel's right edge, the line between them slanting to it.
- `joined.json`: a dead end whose tip is three microns from a long line inside the same
  piece, so the pixels show the two as one stretch of lead.
- `stairs.json`: a piece whose slanted side is a pixel's steps, 0.228 mm each, as a traced edge
  that was never read as a line.
- `close.json`: the rules' map with two pieces 0.2 mm apart along 6 mm, lead under a nozzle's
  width, and one piece 0.25 mm from the panel's edge, which the frame's border backs.
- `near.json`: two curved pieces, the line between them 1.2 mm wide but 0.2 mm along 10 mm.
- `waist.json`: a piece 50 mm long and 14 mm wide with a waist 2 mm across at its middle.
- `slanted_waist.json`: a long piece on a slant, with a waist a quarter of the way along it.
- `bend.json`: a piece round a bend, narrow all the way round it.
- `curved_waist.json`: a piece with curved sides drawn in to a waist 2.45 mm across, a hair
  over the 2.4 mm a lip of 1.2 on each side closes over.
- `apart.json`: a small piece 1.6 mm from a large one across a patch of lead, with no line of
  their own between them, and a third piece beside it.
- `crescent.json`: a thin crescent, 5 mm across at its middle, whose full lip hides more than
  40% of its pane.
- `tailed.json`: a piece 20 by 5 mm with a tail 2.5 mm wide past a pinch, whose share-sized
  opening has a part under two nozzles wide in the tail.
- `shadow.png`: the clean panel with one dark patch in its green cell, 9 by 7 mm and darker
  than `--dark 40`, as a tree's shadow in a scene: read as lead, though it's no line
  (mqucifer/print-shop#172).
- `faint.png`: the clean panel with the top half of one upright line faint, under `--dark 40`
  and over `--dark 20`: at the lower darkness the line ends in glass.
- `meet.png`: four pieces between two curved lines that cross, 100 x 70 mm at 6 pixels a mm,
  with a dark patch of three lobes where the lines meet, read as lead at the default darkness:
  a patch among four pieces, each with a curved edge on it (mqucifer/print-shop#174)
- `ragged.png`: that picture with fourteen dark hairs round the patch's rim, each a few pixels
  wide and 2 mm long: scraps of lead beside the patch that are no part of it
- `stalk.json`: a band of lead under three pieces, one of them above the other two with a stalk
  0.7 mm wide down between them into the band, so its share of the band hangs off the stalk
- `band.json`: three pieces of a 60 x 40 mm panel round a band of lead 12 mm tall, one above it
  and two below with a line between them.
- `flush.json`: three pieces of a 60 x 40 mm panel, for `build --flush`: one square-cornered
  with a curved side, one a triangle with a corner of about 30 degrees, and one between them.
- `hairs.json`: two cuts two microns wide whose ends face each other, which the
  outline has and no pixel shows.
"""

from __future__ import annotations

import json
import math
from typing import Any

from PIL import Image, ImageDraw

S = 5  # pixels a mm
W, H = 120, 80  # mm, inside the frame
F = 10  # the frame, px
LINE = 7  # px: 1.4 mm
DARK = (10, 10, 10)


def canvas() -> tuple[Any, Any]:
    im = Image.new("RGB", (W * S + 2 * F, H * S + 2 * F), DARK)
    return im, ImageDraw.Draw(im)


def px(x: float, y: float) -> tuple[float, float]:
    return (F + x * S, F + (H - y) * S)


def fill(d: Any, pts: Any, colour: Any) -> None:
    d.polygon([px(*p) for p in pts], fill=colour)


def line(d: Any, pts: Any, width: int = LINE) -> None:
    d.line([px(*p) for p in pts], fill=DARK, width=width, joint="curve")


BLUE, RED, GOLD, GREEN, WHITE, PURPLE = (
    (40, 110, 220),
    (210, 40, 40),
    (240, 200, 50),
    (40, 150, 60),
    (235, 235, 230),
    (130, 60, 170),
)


def clean() -> Any:
    im, d = canvas()
    fill(d, [(0, 0), (120, 0), (120, 80), (0, 80)], BLUE)
    fill(d, [(0, 0), (60, 0), (60, 40), (0, 40)], GREEN)
    fill(d, [(60, 0), (120, 0), (120, 40), (60, 40)], GOLD)
    fill(d, [(0, 40), (40, 40), (40, 80), (0, 80)], RED)
    fill(d, [(40, 40), (80, 40), (80, 80), (40, 80)], WHITE)
    uprights = ([(60, 0), (60, 40)], [(40, 40), (40, 80)], [(80, 40), (80, 80)])
    for seg in ([(0, 40), (120, 40)], *uprights):
        line(d, seg)
    return im


#: The shadow in `shadow.png`: its middle and its half-widths in mm, and its colour, under
#: `--dark 40` on every channel and over `--dark 20`.
SHADOW_AT, SHADOW_HALF, SHADOW = (30, 20), (4.5, 3.5), (30, 36, 28)


def shadow() -> Any:
    im = clean()
    d = ImageDraw.Draw(im)
    (x, y), (a, b) = SHADOW_AT, SHADOW_HALF
    d.ellipse([px(x - a, y + b), px(x + a, y - b)], fill=SHADOW)
    return im


def faint() -> Any:
    im = clean()
    d = ImageDraw.Draw(im)
    d.line([px(40, 60), px(40, 80)], fill=SHADOW, width=LINE)
    return im


#: The patch in `meet.png`, as lobes: each one's middle and half-widths, in mm.
MEET_LOBES = ((52, 34, 6, 4), (47, 38, 3.5, 3), (56, 31, 3, 3.5))


def meet(patch: bool = True, ragged: bool = False) -> Any:
    """100 x 70 mm at 6 pixels a mm: a curved line across and a curved line up, four pieces
    between them, and where the two cross a patch as dark as `shadow.png`'s."""
    from PIL import ImageChops

    s, w, h, f, wide = 6, 100, 70, 12, 8
    im = Image.new("RGB", (w * s + 2 * f, h * s + 2 * f), DARK)
    d = ImageDraw.Draw(im)

    def at(x: float, y: float) -> tuple[float, float]:
        return (f + x * s, f + (h - y) * s)

    across = [at(w * k / 300, 35 + 5 * math.sin(w * k / 300 / 14)) for k in range(301)]
    up = [at(50 + 4 * math.sin(h * k / 300 / 11 + 0.5), h * k / 300) for k in range(301)]
    above = Image.new("L", im.size, 0)
    ImageDraw.Draw(above).polygon([*across, at(w, h), at(0, h)], fill=255)
    right = Image.new("L", im.size, 0)
    ImageDraw.Draw(right).polygon([*up, at(w, h), at(w, 0)], fill=255)
    d.rectangle([at(0, h), at(w, 0)], fill=BLUE)
    im.paste(RED, mask=above)
    im.paste(GOLD, mask=ImageChops.multiply(right, above))
    im.paste(GREEN, mask=ImageChops.multiply(right, ImageChops.invert(above)))
    d = ImageDraw.Draw(im)
    # The patch lies under the lines, as a shadow a line was drawn over: darker than
    # `--dark 20`, the lines still cross it.
    for x, y, a, b in MEET_LOBES if patch else ():
        d.ellipse([at(x - a, y + b), at(x + a, y - b)], fill=SHADOW)
    for k in range(14 if ragged else 0):
        turn = k * 2 * math.pi / 14
        root = at(52 + 5 * math.cos(turn), 34 + 3.5 * math.sin(turn))
        tip = at(52 + 7.2 * math.cos(turn), 34 + 5.2 * math.sin(turn))
        d.line([root, tip], fill=SHADOW, width=3 + k % 3)
    d.line(across, fill=DARK, width=wide, joint="curve")
    d.line(up, fill=DARK, width=wide, joint="curve")
    return im


def stalk() -> dict[str, Any]:
    """A band of lead from y 0 to 12 across a 60 x 40 mm panel. Pieces 2 and 3 stand on it,
    from y 12 to 20. Piece 1 lies above them, with a stalk 0.7 mm wide down between the two,
    a 1.2 mm line each side of it, that ends in the band at y 6. The band round the stalk's
    end is nearer piece 1 than the two beside it, so piece 1's share is 18 mm wide and joins
    the rest of the piece by the stalk alone: too thin a place for one pane."""
    stem = [[10, 21.2], [10, 6], [10.7, 6], [10.7, 21.2]]
    top = [[60, 21.2], [60, 40], [0, 40]]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 1.2,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "blue", "outline": [[0, 21.2], *stem, *top]},
            {"id": 2, "colour": "gold", "outline": [[0, 12], [8.8, 12], [8.8, 20], [0, 20]]},
            {"id": 3, "colour": "green", "outline": [[11.9, 12], [60, 12], [60, 20], [11.9, 20]]},
        ],
    }


def band() -> dict[str, Any]:
    """Three pieces round a band of lead from y 14 to 26, right across a 60 x 40 mm panel:
    piece 1 above it, pieces 2 and 3 below, with a line 1.2 mm wide between those two at x 30.
    The band is as near piece 1 as the two below along y 20, and as near piece 2 as piece 3
    along x 30."""
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 1.2,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "blue", "outline": [[0, 26], [60, 26], [60, 40], [0, 40]]},
            {"id": 2, "colour": "green", "outline": [[0, 0], [29.4, 0], [29.4, 14], [0, 14]]},
            {"id": 3, "colour": "gold", "outline": [[30.6, 0], [60, 0], [60, 14], [30.6, 14]]},
        ],
    }


def broken() -> Any:
    im, d = canvas()
    # Six cells: 3 across, 2 down, 40 x 40 each.
    fill(d, [(0, 40), (40, 40), (40, 80), (0, 80)], BLUE)  # top-left: a dead end runs into it
    fill(d, [(40, 40), (80, 40), (80, 80), (40, 80)], WHITE)  # top-middle: a sliver beside it
    fill(d, [(80, 40), (120, 40), (120, 80), (80, 80)], RED)  # top-right: a gradient
    fill(d, [(0, 0), (40, 0), (40, 40), (0, 40)], GREEN)  # bottom-left: a neck
    fill(d, [(40, 0), (80, 0), (80, 40), (40, 40)], GOLD)  # bottom-middle: a notch
    fill(d, [(80, 0), (120, 0), (120, 40), (80, 40)], PURPLE)  # bottom-right: a small piece in it
    # The gradient: red to gold across the top-right cell.
    for i in range(40 * S):
        t = i / (40 * S - 1)
        c = tuple(int(RED[k] + (GOLD[k] - RED[k]) * t) for k in range(3))
        x = F + 80 * S + i
        d.line([(x, F), (x, F + 40 * S)], fill=c)
    for seg in ([(0, 40), (120, 40)], [(40, 0), (40, 80)], [(80, 0), (80, 80)]):
        line(d, seg)
    # A dead end: a line from the top-left cell's bottom edge up into its glass.
    line(d, [(20, 40), (20, 62)])
    # A sliver: a line 2 mm from the top-middle cell's left edge.
    line(d, [(43.4, 40), (43.4, 80)])
    # A neck: the bottom-left cell is two halves joined by 1 mm of glass, with a white
    # piece above the join and a purple one below.
    fill(d, [(17, 21), (23, 21), (23, 40), (17, 40)], WHITE)
    fill(d, [(17, 0), (23, 0), (23, 19), (17, 19)], PURPLE)
    line(d, [(17, 40), (17, 21.2), (23, 21.2), (23, 40)])
    line(d, [(17, 0), (17, 18.8), (23, 18.8), (23, 0)])
    # A notch: a blue tongue into the bottom-middle cell from its top, 6 wide and 24 deep.
    fill(d, [(57, 40), (63, 40), (63, 16), (57, 16)], BLUE)
    line(d, [(57, 40), (57, 16), (63, 16), (63, 40)])
    # A small piece: a square in the bottom-right cell, 4.6 mm of glass across.
    fill(d, [(97, 17), (103, 17), (103, 23), (97, 23)], WHITE)
    line(d, [(97, 17), (103, 17), (103, 23), (97, 23), (97, 17)])
    # A patch of lead where four lines meet.
    cx, cy = px(80, 40)
    d.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=DARK)
    return im


def hills() -> Any:
    """100 x 70 mm at 6 pixels a mm: three curved bands, two uprights in each, and a sun."""
    s, w, h, f, line = 6, 100, 70, 12, 9
    im = Image.new("RGB", (w * s + 2 * f, h * s + 2 * f), DARK)
    d = ImageDraw.Draw(im)

    def at(x: float, y: float) -> tuple[float, float]:
        return (f + x * s, f + (h - y) * s)

    def along(curve: Any, n: int = 300) -> list[tuple[float, float]]:
        return [(w * k / n, curve(w * k / n)) for k in range(n + 1)]

    bands: list[Any] = [
        lambda x: 22 + 5 * math.sin(x / 16),
        lambda x: 46 + 6 * math.sin(x / 21 + 1.3),
    ]
    d.rectangle([at(0, h), at(w, 0)], fill=GREEN)
    for curve, colour in zip(bands, (BLUE, WHITE), strict=True):
        d.polygon([at(*p) for p in along(curve)] + [at(w, h), at(0, h)], fill=colour)
    for curve in bands:
        d.line([at(*p) for p in along(curve)], fill=DARK, width=line, joint="curve")
    edges: list[Any] = [lambda x: -3.0, *bands, lambda x: h + 3.0]
    for k, (low, high) in enumerate(zip(edges, edges[1:], strict=False)):
        for x, lean in ((33 + 4 * k, 5.0), (68 - 3 * k, -6.0)):
            points = [
                (x + lean * t + 2 * math.sin(3 * t), low(x) + (high(x + lean) - low(x)) * t)
                for t in [j / 40 for j in range(41)]
            ]
            d.line([at(*p) for p in points], fill=DARK, width=line, joint="curve")
    # The sun stands on the upper band's line, so the line round it joins the others.
    cx, cy = at(84, 49)
    d.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), fill=GOLD, outline=DARK, width=line)
    return im


def thin_line(thin: int = 1, specks: bool = False) -> Any:
    """100 x 70 mm at 5 pixels a mm: four curved bands and three uprights in each strip, 20
    pieces. Every line is 4 pixels wide but the second band's, which is `thin`, one: the two
    smoothed outlines either side of it cross, as a pixel is all that lies between them. With
    `specks`, five dark specks two pixels across lie inside two of the pieces."""
    s, w, h, f = 5, 100, 70, 8
    im = Image.new("RGB", (w * s + 2 * f, h * s + 2 * f), DARK)
    d = ImageDraw.Draw(im)

    def at(x: float, y: float) -> tuple[float, float]:
        return (f + x * s, f + (h - y) * s)

    def along(curve: Any, n: int = 400) -> list[tuple[float, float]]:
        return [(w * k / n, curve(w * k / n)) for k in range(n + 1)]

    bands: list[Any] = [
        lambda x: 14 + 4 * math.sin(x / 13),
        lambda x: 30 + 5 * math.sin(x / 17 + 1),
        lambda x: 47 + 6 * math.sin(x / 21 + 2),
        lambda x: 60 + 3 * math.sin(x / 9),
    ]
    d.rectangle([at(0, h), at(w, 0)], fill=BLUE)
    for curve, colour in zip(bands, (WHITE, GREEN, GOLD, RED), strict=True):
        d.polygon([at(*p) for p in along(curve)] + [at(w, h), at(0, h)], fill=colour)
    for n, curve in enumerate(bands):
        wide = thin if n == 1 else 4
        d.line([at(*p) for p in along(curve)], fill=DARK, width=wide, joint="curve")
    edges: list[Any] = [lambda x: -3.0, *bands, lambda x: h + 3.0]
    for k, (low, high) in enumerate(zip(edges, edges[1:], strict=False)):
        for x, lean in ((22 + 7 * k, 6.0), (55 - 4 * k, -7.0), (80 + 2 * k, 3.0)):
            points = [
                (x + lean * t + 2 * math.sin(3 * t), low(x) + (high(x + lean) - low(x)) * t)
                for t in [j / 60 for j in range(61)]
            ]
            d.line([at(*p) for p in points], fill=DARK, width=4, joint="curve")
    if specks:
        for x, y in ((10, 22), (14, 25), (9, 27), (45, 40), (48, 37)):
            cx, cy = at(x, y)
            d.rectangle([cx, cy, cx + 1, cy + 1], fill=DARK)
    return im


def margin() -> Any:
    """The clean panel on white, 40 pixels of margin all round, with the frame's top 4 pixels
    thinner over its right half and three specks of dark in the margin above it."""
    inner = clean()
    im = Image.new("RGB", (inner.width + 80, inner.height + 80), (250, 250, 250))
    im.paste(inner, (40, 40))
    d = ImageDraw.Draw(im)
    d.rectangle((40 + inner.width // 2, 40, 40 + inner.width, 43), fill=(250, 250, 250))
    for x, y in ((120, 12), (300, 20), (520, 9)):
        d.ellipse((x, y, x + 3, y + 3), fill=DARK)
    return im


def specks() -> Any:
    """The clean panel with two clusters of six dark specks, 2 to 4 pixels across, inside two
    cells, and a lead line 12 mm long that ends in a third cell's glass."""
    im = clean()
    d = ImageDraw.Draw(im)
    for cx, cy in ((20, 60), (100, 20)):
        for k in range(6):
            x, y = px(cx + 3 * (k % 3) - 3, cy + 4 * (k // 3) - 2)
            size = 2 + k % 3
            d.ellipse((x, y, x + size, y + size), fill=DARK)
    line(d, [(30, 40), (30, 28)])
    return im


def on_ground(ground: tuple[int, int, int]) -> Any:
    """A slanting band of glass on a ground, its corners reaching near every edge
    (mqucifer/print-shop#240): on a dark ground most rows that hold it are mostly dark."""
    im = Image.new("RGB", (400, 300), ground)
    d = ImageDraw.Draw(im)
    edge, lead = [(0, 120), (330, 20), (330, 180), (0, 280)], (10, 10, 10)
    d.polygon(edge, fill=(200, 150, 70), outline=lead, width=4)
    lower = [(0, 200), (330, 100), (330, 180), (0, 280)]
    d.polygon(lower, fill=(80, 140, 70), outline=lead, width=4)
    for x in (110, 220):
        d.line([(x, 120 - x * 100 / 330), (x, 280 - x * 100 / 330)], fill=lead, width=4)
    return im


def framed(kind: str = "") -> Any:
    """A real frame 8 pixels thick, 20 in from the picture's edge. `specked`: specks of dark
    in its margin and of colour in it. `notched`: thinner from outside over 30% of its top,
    where the margin comes in. `wavy`: its inner edge wanders 2 pixels."""
    im = Image.new("RGB", (400, 300), (240, 240, 240))
    d = ImageDraw.Draw(im)
    glass, lead = (200, 150, 70), (10, 10, 10)
    d.rectangle((20, 20, 379, 279), fill=glass, outline=lead, width=8)
    d.line([(200, 20), (200, 279)], fill=lead, width=4)
    if kind == "specked":
        for x, y in ((60, 6), (150, 12), (300, 290)):
            d.ellipse((x, y, x + 3, y + 3), fill=lead)
        for x, y in ((80, 22), (260, 23), (22, 140), (374, 90)):
            d.rectangle((x, y, x + 3, y + 3), fill=(120, 90, 200))
    elif kind == "notched":
        d.rectangle((250, 20, 357, 23), fill=(240, 240, 240))
    elif kind == "wavy":
        for x in range(30, 370, 60):
            d.rectangle((x, 26, x + 24, 27), fill=glass)
    return im


def cells(lead: float) -> list[list[list[float]]]:
    """Four cells of a 60 x 40 mm panel, each inset by half the lead: a map from rules."""
    half = lead / 2
    boxes = [(0, 0, 30, 20), (30, 0, 60, 20), (0, 20, 30, 40), (30, 20, 60, 40)]
    found = []
    for x0, y0, x1, y1 in boxes:
        a = x0 if x0 == 0 else x0 + half
        b = y0 if y0 == 0 else y0 + half
        c = x1 if x1 == 60 else x1 - half
        e = y1 if y1 == 40 else y1 - half
        found.append([[a, b], [c, b], [c, e], [a, e]])
    return found


def lone_lines() -> dict[str, Any]:
    """The rules' map with three lines inside piece 4, each joined to no other: one 0.4 mm
    wide, too thin for a pixel to show as a line, and two 1.2 mm wide lying 0.05 mm apart,
    which the map's pixels show as one body of lead."""
    found = rules(False)
    found["pieces"][3]["holes"] = [
        [[34, 24], [44, 24], [44, 24.4], [34, 24.4]],
        [[40, 29.4], [50, 29.4], [50, 30.6], [40, 30.6]],
        [[40, 30.65], [50, 30.65], [50, 31.85], [40, 31.85]],
    ]
    return found


def notched() -> dict[str, Any]:
    """One piece 40 x 20 mm in a panel, with a notch from its top at x 12 and one from its
    bottom at x 13, each 0.5 mm wide and 9.6 mm deep: between their ends, the glass is 0.94 mm
    across, a neck, straight across nowhere a ray square to a side goes."""
    outline = [
        [1, 1], [12.75, 1], [12.75, 10.6], [13.25, 10.6], [13.25, 1], [41, 1], [41, 21],
        [12.25, 21], [12.25, 11.4], [11.75, 11.4], [11.75, 21], [1, 21],
    ]  # fmt: skip
    return {
        "piece_map": 1,
        "panel": {"width_mm": 42, "height_mm": 22},
        "lead_mm": 1.0,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [{"id": 1, "colour": "blue", "outline": outline}],
    }


def stepped() -> dict[str, Any]:
    """The notched piece with its notches' ends closer and the upper one stepped: the nearest
    two points are corners 0.3 mm apart along and 0.8 across, 0.854 mm, and the only rays
    square to a side between those two ends run from the lower notch's flat end to the upper
    one's step, 1.1 mm. Both are under 1.2 mm."""
    found = notched()
    found["pieces"][0]["outline"] = [
        [1, 1], [12.3, 1], [12.3, 10.6], [13.0, 10.6], [13.0, 1], [41, 1], [41, 21],
        [12.6, 21], [12.6, 11.7], [12.0, 11.7], [12.0, 11.4], [11.75, 11.4], [11.75, 21],
        [1, 21],
    ]  # fmt: skip
    return found


def slanted() -> dict[str, Any]:
    """The stepped piece with its step higher, as a traced scene read one: the nearest two
    points are corners 0.3 mm apart along and 0.977 across, 1.022 mm, under 1.2. The only rays
    square to a side between those two ends run from the lower notch's flat end up to the
    step, 1.828 mm: along the slant's long way, over 1.2."""
    found = notched()
    found["pieces"][0]["outline"] = [
        [1, 1], [12.3, 1], [12.3, 10.6], [13.0, 10.6], [13.0, 1], [41, 1], [41, 21],
        [12.6, 21], [12.6, 12.428], [12.0, 12.428], [12.0, 11.577], [11.75, 11.577],
        [11.75, 21], [1, 21],
    ]  # fmt: skip
    return found


def spiked() -> dict[str, Any]:
    """One piece 40 x 20 mm with a notch in its top: a mouth 2.6 mm wide, sloping in to 0.06 mm
    wide at 1.7 mm deep, then a spike on to a point 2.77 mm deep. By its outline it's 2.77 mm
    deep, 1.065 times its mouth, over 1. The spike is thinner than a pixel, so the furthest
    pixel's centre in the notch is about 2.5 mm from its mouth, under 1 time its mouth."""
    found = notched()
    found["pieces"][0]["outline"] = [
        [1, 1], [41, 1], [41, 21], [21.3, 21], [20.03, 19.3], [20.0, 18.23], [19.97, 19.3],
        [18.7, 21], [1, 21],
    ]  # fmt: skip
    return found


def hairs() -> dict[str, Any]:
    """The notched piece with each notch 0.002 mm wide and their ends 1 mm apart, 0.02 mm
    to each side of one line: a neck by the outline, 1 mm under 1.2, and no pixel has either
    cut, so by the pixels the glass there is as wide as the piece."""
    found = notched()
    found["pieces"][0]["outline"] = [
        [1, 1], [12.509, 1], [12.509, 10.5], [12.511, 10.5], [12.511, 1], [41, 1], [41, 21],
        [12.491, 21], [12.491, 11.5], [12.489, 11.5], [12.489, 21], [1, 21],
    ]  # fmt: skip
    return found


def thin_lines() -> dict[str, Any]:
    """A 250 x 200 mm panel whose map says its usual lead is 1.4 mm, with lines 0.4 mm wide
    round a narrow piece: as a trace at a low `--dark` leaves some lines a pixel or two thin
    (mqucifer/print-shop#161). Piece 1 is a strip 3.4 mm wide with a dead end 0.5 mm wide and
    8 mm long down from its top: the pieces each side of the strip are 1.85 mm from it, nearer
    than the lead and a quarter the pixels look round for a line with one piece's glass. Piece
    4 has a line inside it 1 mm wide, joined to no other, 0.005 mm from its bottom edge: no
    pixel shows that gap, so by the pixels the line joins the lead."""
    strip = [
        [100, 0], [103.4, 0], [103.4, 100], [101.95, 100], [101.95, 92], [101.45, 92],
        [101.45, 100], [100, 100],
    ]  # fmt: skip
    pieces: list[dict[str, Any]] = [
        {"id": 1, "colour": "blue", "outline": strip},
        {"id": 2, "colour": "gold", "outline": [[0, 0], [99.6, 0], [99.6, 100], [0, 100]]},
        {"id": 3, "colour": "green", "outline": [[103.8, 0], [250, 0], [250, 100], [103.8, 100]]},
        {
            "id": 4,
            "colour": "red",
            "outline": [[0, 100.4], [250, 100.4], [250, 200], [0, 200]],
            "holes": [[[150, 100.405], [158, 100.405], [158, 101.405], [150, 101.405]]],
        },
    ]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 250, "height_mm": 200},
        "lead_mm": 1.4,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": pieces,
    }


def quiet() -> dict[str, Any]:
    """One piece 40 x 20 mm with nothing a rule flags, and two places a second reading would
    differ on: a spur 0.8 mm wide and 4 mm long off its right side, under the 1.2 mm neck
    limit with wide glass on one side of it only; and the spiked notch with its mouth 4.4 mm
    wide, 0.63 times as deep as that, whose pixels read it 0.3 mm shallower."""
    found = notched()
    found["panel"] = {"width_mm": 47, "height_mm": 22}
    found["pieces"][0]["outline"] = [
        [1, 1], [41, 1], [41, 10.6], [45, 10.6], [45, 11.4], [41, 11.4], [41, 21],
        [22.2, 21], [20.03, 19.3], [20.0, 18.23], [19.97, 19.3], [17.8, 21], [1, 21],
    ]  # fmt: skip
    return found


def edge() -> dict[str, Any]:
    """Two pieces of a 60 x 40 mm panel, both on its right edge, with a slanted line 1.4 mm
    wide between them that runs to the edge."""
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 1.4,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "blue", "outline": [[0, 22], [60, 26], [60, 40], [0, 40]]},
            {"id": 2, "colour": "gold", "outline": [[0, 0], [60, 0], [60, 24.6], [0, 20.6]]},
        ],
    }


def joined() -> dict[str, Any]:
    """The rules' map, twice the size, with a dead end 1.2 mm wide and 8 mm long down from
    piece 3's top edge, and a line inside piece 3, 1.2 mm wide and 40 mm long, that starts
    0.003 mm under the dead end's tip and runs on down. By the outline they're two: a dead end
    and a line joined to no other. No pixel shows the 0.003 mm between them, so the pixels'
    lead alone with piece 3 is one stretch 48 mm long, whose middle is 24 mm from the dead
    end's tip."""
    lead, half = 1.2, 0.6
    boxes = [(0, 0, 60, 60), (60, 0, 120, 60), (0, 60, 60, 120), (60, 60, 120, 120)]
    outlines = []
    for x0, y0, x1, y1 in boxes:
        a = x0 if x0 == 0 else x0 + half
        b = y0 if y0 == 0 else y0 + half
        c = x1 if x1 == 120 else x1 - half
        e = y1 if y1 == 120 else y1 - half
        outlines.append([[a, b], [c, b], [c, e], [a, e]])
    pieces: list[dict[str, Any]] = [
        {"id": k + 1, "colour": colour, "outline": outline}
        for k, (outline, colour) in enumerate(
            zip(outlines, ("blue", "gold", "green", "red"), strict=True)
        )
    ]
    # Piece 3 is the top-left cell: the dead end runs up into it from its bottom edge.
    low, top, right = 60.6, 120.0, 59.4
    pieces[2]["outline"] = [
        [0, low], [29.4, low], [29.4, low + 8], [30.6, low + 8], [30.6, low], [right, low],
        [right, top], [0, top],
    ]  # fmt: skip
    pieces[2]["holes"] = [[[29.4, 68.603], [30.6, 68.603], [30.6, 108.603], [29.4, 108.603]]]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 120, "height_mm": 120},
        "lead_mm": lead,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": pieces,
    }


def stairs() -> dict[str, Any]:
    """One piece in a 60 x 60 mm panel: a square 40 mm across with one corner cut off along a
    slant, the slant drawn as 120 steps of 0.228 mm, across then up. Every step is a square
    corner, so the build keeps each, and the pocket has 240 sides shorter than the play."""
    step, count = 0.228, 120
    run = round(step * count, 3)
    slant: list[list[float]] = []
    for k in range(count):
        slant += [[10 + k * step, 50 - run + k * step], [10 + (k + 1) * step, 50 - run + k * step]]
    outline: list[list[float]] = [[10, 10], [50, 10], [50, 50], [10 + run, 50], *slant[::-1]]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 60},
        "lead_mm": 1.0,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "blue", "outline": [[round(x, 4), round(y, 4)] for x, y in outline]}
        ],
    }


def close() -> dict[str, Any]:
    """The rules' map with a tab on piece 1's right side, 6 mm tall, that leaves 0.2 mm of lead
    between it and piece 2, where the line is 1.2 mm; and piece 3's left side 0.25 mm in from
    the panel's edge, where the frame's border stands outside it."""
    found = rules(False)
    found["pieces"][0]["outline"] = [
        [0, 0], [29.4, 0], [29.4, 7], [30.4, 7], [30.4, 13], [29.4, 13], [29.4, 19.4], [0, 19.4],
    ]  # fmt: skip
    found["pieces"][2]["outline"] = [[0.25, 20.6], [29.4, 20.6], [29.4, 40], [0.25, 40]]
    del found["source"]
    return found


def near() -> dict[str, Any]:
    """Two pieces of a 60 x 40 mm panel, one colour, with a curved line between them: 1.2 mm
    wide, but 0.2 mm from x 25 to 35, narrowing over a millimetre at each end. A line the
    picture means, too thin for a nozzle along 10 mm."""

    def middle(x: float) -> float:
        return 20 + 2 * math.sin(x / 8)

    def width(x: float) -> float:
        inside = min(x - 24, 36 - x)
        return 1.2 if inside <= 0 else 0.2 if inside >= 1 else 1.2 - inside

    xs = [k / 4 for k in range(241)]
    low = [[x, round(middle(x) - width(x) / 2, 4)] for x in xs]
    high = [[x, round(middle(x) + width(x) / 2, 4)] for x in xs]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 1.2,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "blue", "outline": [[0, 0], [60, 0], *low[::-1]]},
            {"id": 2, "colour": "blue", "outline": [*high, [60, 40], [0, 40]]},
        ],
    }


def waist() -> dict[str, Any]:
    """One piece of a 60 x 24 mm panel: 50 mm long and 14 mm wide, drawn in to a waist 2 mm
    across at its middle, from 8 mm either side. Over the 1.2 mm a neck is, and under the
    2.4 mm a lip of 1.2 on each side closes over."""
    found = notched()
    found["panel"] = {"width_mm": 60, "height_mm": 24}
    found["pieces"][0]["outline"] = [
        [5, 5], [22, 5], [30, 11], [38, 5], [55, 5], [55, 19], [38, 19], [30, 13], [22, 19],
        [5, 19],
    ]  # fmt: skip
    return found


def slanted_waist() -> dict[str, Any]:
    """One piece of a 70 x 50 mm panel: a band 56 mm long and 10 mm wide, lying at 31 degrees,
    drawn in to a waist 2 mm across 14 mm from its lower end. A line square to the way from
    one half of its opening to the other runs along the band, not across it."""
    turn = math.atan2(30, 50)
    along, across = (math.cos(turn), math.sin(turn)), (-math.sin(turn), math.cos(turn))
    half = [(0, 5), (8, 5), (14, 1), (20, 5), (56, 5)]
    ring = [(u, -v) for u, v in half] + [(u, v) for u, v in reversed(half)]
    found = notched()
    found["panel"] = {"width_mm": 70, "height_mm": 50}
    found["pieces"][0]["outline"] = [
        [round(10 + u * along[0] + v * across[0], 4), round(12 + u * along[1] + v * across[1], 4)]
        for u, v in ring
    ]
    return found


def bend() -> dict[str, Any]:
    """One piece of a 60 x 40 mm panel, round a bend: a band along an arc 20 mm in radius, 10
    mm wide at its two ends and 2 mm wide from 50 to 100 degrees round, 17 mm of narrow run.
    The middle between the two halves of its opening lies in the bend's hollow, outside the
    piece."""

    def half(deg: float) -> float:
        return 1.0 if 50 <= deg <= 100 else 5.0 if deg <= 40 or deg >= 110 else (
            1.0 + 4.0 * (50 - deg) / 10 if deg < 50 else 1.0 + 4.0 * (deg - 100) / 10
        )

    def at(deg: float, radius: float) -> list[float]:
        turn = math.radians(deg)
        return [round(30 + radius * math.cos(turn), 4), round(6 + radius * math.sin(turn), 4)]

    degrees = [10 + 2 * k for k in range(66)]
    outer = [at(d, 20 + half(d)) for d in degrees]
    inner = [at(d, 20 - half(d)) for d in reversed(degrees)]
    found = notched()
    found["panel"] = {"width_mm": 60, "height_mm": 40}
    found["pieces"][0]["outline"] = outer + inner
    return found


def curved_waist() -> dict[str, Any]:
    """One piece of a 60 x 24 mm panel, 50 mm long, its two long sides curves that come
    within 2.45 mm of each other at its middle and open to 14 mm. On its outline a lip of 1.2
    mm leaves an opening 0.05 mm wide through the waist, one piece. Its pocket, as built, is
    its fitted curves moved inside the outline, and the opening there is two."""

    def half(x: float) -> float:
        return min(7.0, 1.225 + 0.02 * (x - 30) ** 2)

    xs = [5 + k * 0.5 for k in range(101)]
    low = [[x, round(12 - half(x), 4)] for x in xs]
    high = [[x, round(12 + half(x), 4)] for x in xs]
    found = notched()
    found["panel"] = {"width_mm": 60, "height_mm": 24}
    found["pieces"][0]["outline"] = low + high[::-1]
    return found


def apart() -> dict[str, Any]:
    """Three pieces of a 60 x 40 mm panel whose usual lead is 0.95 mm. Piece 1 is large, the
    panel's left 40 mm. Piece 2 is small, a diamond 7.8 by 7 mm, one corner 1.6 mm off piece
    1's right side, in a patch of lead: further than a line is wide, so they share no line,
    and only that corner is near. Piece 3 is the panel's right side, 1.2 mm beyond piece 2."""
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 0.95,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": [
            {"id": 1, "colour": "green", "outline": [[0, 0], [40, 0], [40, 40], [0, 40]]},
            {
                "id": 2,
                "colour": "green",
                "outline": [[41.6, 18], [45.5, 14.5], [49.4, 18], [45.5, 21.5]],
            },
            {"id": 3, "colour": "blue", "outline": [[50.6, 0], [60, 0], [60, 40], [50.6, 40]]},
        ],
    }


def crescent() -> dict[str, Any]:
    """One piece of a 64 x 26 mm panel: the part of a circle 30 mm in radius that lies above
    the same circle 5 mm lower, from 60 to 120 degrees round. A crescent 5 mm across at its
    middle and 4.4 at its ends, 31 mm long. A lip of 1.2 mm on each side leaves about half of
    its pane visible; one of about 1 mm leaves 60%."""

    def at(deg: float, lower: float) -> list[float]:
        turn = math.radians(deg)
        return [round(32 + 30 * math.cos(turn), 4), round(30 * math.sin(turn) - lower - 8, 4)]

    degrees = [60 + k for k in range(61)]
    outer = [at(d, 0.0) for d in degrees]
    inner = [at(d, 5.0) for d in reversed(degrees)]
    found = notched()
    found["panel"] = {"width_mm": 64, "height_mm": 26}
    found["pieces"][0]["outline"] = inner + outer
    return found


def tailed() -> dict[str, Any]:
    """One piece of a 40 x 21 mm panel: a body 20 by 5 mm, then a pinch 1.5 mm wide and 1 mm
    long, then a tail 2.5 mm wide and 8 mm long. A lip of 1.2 mm hides too much of it, so its
    rim is sized to its share, about 0.9 mm. At that rim the tail still shows a strip 0.7 mm
    wide, apart from the body's opening and under the two nozzles an opening is."""
    found = notched()
    found["panel"] = {"width_mm": 40, "height_mm": 21}
    found["pieces"][0]["outline"] = [
        [5, 8], [25, 8], [25, 9.75], [26, 9.75], [26, 9.25], [34, 9.25], [34, 11.75],
        [26, 11.75], [26, 11.25], [25, 11.25], [25, 13], [5, 13],
    ]  # fmt: skip
    return found


def rules(slit: bool, depth: float = 8.0) -> dict[str, Any]:
    lead = 1.2
    outlines = cells(lead)
    pieces = [
        {"id": k + 1, "colour": colour, "outline": outline}
        for k, (outline, colour) in enumerate(
            zip(outlines, ("blue", "gold", "green", "red"), strict=True)
        )
    ]
    if slit:
        # A lead line 1.2 mm wide from piece 1's top edge, `depth` mm down into its glass.
        x0, y0, x1, y1 = 0.0, 0.0, 29.4, 19.4
        pieces[0]["outline"] = [
            [x0, y0],
            [x1, y0],
            [x1, y1],
            [15.6, y1],
            [15.6, y1 - depth],
            [14.4, y1 - depth],
            [14.4, y1],
            [x0, y1],
        ]
        # And a line inside piece 4, joined to no other: a hole 10 x 1.2 mm.
        pieces[3]["holes"] = [[[40, 29.4], [50, 29.4], [50, 30.6], [40, 30.6]]]
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": lead,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": pieces,
        "source": {"by": "rules, in a test", "glass_share_pct": 95.06},
    }


def flush() -> dict[str, Any]:
    """Three pieces of a 60 x 40 mm panel, its lines 1.2 mm wide: a curved line down it, from
    x = 20 at its bottom and top bowing 2 mm right at its middle, and a straight one from
    (25, 0) to (60, 20), so the piece under it is a triangle with a corner of 29.7 degrees."""
    import shapely

    half = 0.6
    curve = shapely.LineString(
        [(20 + 2 * math.sin(math.pi * y / 40), y) for y in [-2 + 0.25 * k for k in range(177)]]
    )
    way = (35 / math.hypot(35, 20), 20 / math.hypot(35, 20))
    slant = shapely.LineString([(25 - 3 * way[0], -3 * way[1]), (60 + 3 * way[0], 20 + 3 * way[1])])
    lead = shapely.union_all([line.buffer(half, cap_style="flat") for line in (curve, slant)])
    glass = shapely.box(0, 0, 60, 40).difference(lead)
    left, *right = sorted(shapely.get_parts(glass), key=lambda p: p.centroid.x)
    under, over = sorted(right, key=lambda p: p.centroid.y)
    pieces = []
    for number, (shape, colour) in enumerate(
        ((left, "blue"), (under, "gold"), (over, "green")), start=1
    ):
        ring = [[round(x, 4), round(y, 4)] for x, y in shape.exterior.coords[:-1]]
        pieces.append({"id": number, "colour": colour, "outline": ring})
    return {
        "piece_map": 1,
        "panel": {"width_mm": 60, "height_mm": 40},
        "lead_mm": 2 * half,
        "colours": {"blue": {"rgb": [40, 90, 220]}},
        "pieces": pieces,
        "source": {"by": "rules, in a test"},
    }


clean().save("clean.png")
broken().save("broken.png")
hills().save("hills.png")
thin_line().save("thin_line.png")
thin_line(4, specks=True).save("speckled.png")
margin().save("margin.png")
specks().save("specks.png")
shadow().save("shadow.png")
faint().save("faint.png")
meet().save("meet.png")
meet(ragged=True).save("ragged.png")
on_ground((10, 10, 10)).save("dark-ground.png")
on_ground((60, 110, 220)).save("light-ground.png")
for kind in ("", "specked", "notched", "wavy"):
    framed(kind).save(f"framed-{kind}.png" if kind else "framed.png")
for name, slit, depth in (
    ("rules.json", False, 0),
    ("slit.json", True, 8),
    ("deep.json", True, 18.4),
):
    with open(name, "w") as fh:
        json.dump(rules(slit, depth), fh)
for name, made in (
    ("lone.json", lone_lines()),
    ("notched.json", notched()),
    ("stepped.json", stepped()),
    ("slanted.json", slanted()),
    ("spiked.json", spiked()),
    ("hairs.json", hairs()),
    ("thin_lines.json", thin_lines()),
    ("quiet.json", quiet()),
    ("joined.json", joined()),
    ("close.json", close()),
    ("near.json", near()),
    ("waist.json", waist()),
    ("tailed.json", tailed()),
    ("crescent.json", crescent()),
    ("apart.json", apart()),
    ("curved_waist.json", curved_waist()),
    ("slanted_waist.json", slanted_waist()),
    ("bend.json", bend()),
    ("stairs.json", stairs()),
    ("edge.json", edge()),
    ("band.json", band()),
    ("stalk.json", stalk()),
    ("flush.json", flush()),
):
    with open(name, "w") as fh:
        json.dump(made, fh)
print("drawn")
