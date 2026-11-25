"""A made-up stained-glass picture for `load`'s glass frame (mqucifer/print-shop#295), drawn in
code, run in the bench's first image: nothing here is anyone's design.

Six smooth lines wander across a panel, crossing each other at shallow angles, so the pieces
between them have sharp V corners, and some are long and thin, as a painted picture traced has.
Each region is flooded with a colour. `pieces trace` reads it as a map, and `pieces build` makes
the frame, with two lips sized by `--min-visible`, as a run builds one.

`python glass_panel.py WIDTH` writes `panel.png`, WIDTH x 0.625 WIDTH mm at 5 pixels a mm.
"""

from __future__ import annotations

import math
import sys

from PIL import Image, ImageDraw

S, LINE = 5, 7  # pixels a mm, and the lines' width in pixels
DARK, GROUND = (10, 10, 10), (40, 90, 220)
COLOURS = [(220, 60, 50), (230, 190, 40), (60, 170, 80), (240, 240, 230), (140, 70, 170)]
#: Each line: where it starts and ends across the panel, as shares of its height, how far it
#: wanders, as a share, how many times, and where in its wave it starts.
LINES = [
    (0.10, 0.90, 0.12, 1.3, 0.2), (0.80, 0.15, 0.10, 1.7, 1.1), (0.30, 0.40, 0.18, 2.1, 2.0),
    (0.60, 0.55, 0.15, 1.1, 0.5), (0.05, 0.70, 0.08, 2.6, 2.7), (0.95, 0.35, 0.09, 0.9, 1.9),
]  # fmt: skip


def main() -> None:
    width = float(sys.argv[1]) if len(sys.argv) > 1 else 160.0
    height = 0.625 * width
    image = Image.new("RGB", (round(width * S), round(height * S)), DARK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((LINE, LINE, image.width - LINE, image.height - LINE), fill=GROUND)
    for start, end, wander, waves, phase in LINES:
        points = []
        for k in range(201):
            t = k / 200
            y = start + (end - start) * t + wander * math.sin(2 * math.pi * waves * t + phase)
            points.append((t * width * S, y * height * S))
        draw.line(points, fill=DARK, width=LINE, joint="curve")
    draw.rectangle((0, 0, image.width - 1, image.height - 1), outline=DARK, width=LINE)
    count = 0
    for gy in range(6, int(height), 9):
        for gx in range(6, int(width), 11):
            point = (gx * S, gy * S)
            if image.getpixel(point) == GROUND:
                ImageDraw.floodfill(image, point, COLOURS[count % len(COLOURS)])
                count += 1
    image.save("panel.png")
    print("drawn", count)


if __name__ == "__main__":
    main()
