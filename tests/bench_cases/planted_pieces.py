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
for name, made in (("lone.json", lone_lines()), ("notched.json", notched())):
    with open(name, "w") as fh:
        json.dump(made, fh)
print("drawn")
