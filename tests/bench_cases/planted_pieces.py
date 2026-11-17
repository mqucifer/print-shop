"""Stained-glass pictures and piece maps for `tests/test_pieces.py`, drawn in code: nothing here
is a Sponsor's scene or anything from one (mqucifer/print-shop#152, mqucifer/print-shop#153).

- `clean.png`: five flat cells between straight lines, 120 x 80 mm at 5 pixels a mm, in a frame.
- `broken.png`: the same panel with one of each rule break `pieces check` reads.
- `hills.png`: curved bands, slanted uprights and a round sun, for curves built as curves.
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


clean().save("clean.png")
broken().save("broken.png")
hills().save("hills.png")
margin().save("margin.png")
specks().save("specks.png")
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
    ("edge.json", edge()),
):
    with open(name, "w") as fh:
        json.dump(made, fh)
print("drawn")
