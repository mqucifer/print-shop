"""Pixels from a picture, inside the image, since the tests' side has no image library.

Each argument is `FILE:X,Y` for a pixel, or `FILE:row=Y` for how long the longest white run is
along that row. Prints a JSON list, one answer each.
"""

import json
import sys

import numpy as np
from PIL import Image


def answer(text: str) -> object:
    name, _, what = text.rpartition(":")
    pixels = np.asarray(Image.open(name).convert("RGB"))
    if what.startswith("row="):
        white = (pixels[int(what[4:])] >= 250).all(axis=1)
        best = run = 0
        for value in white:
            run = run + 1 if value else 0
            best = max(best, run)
        return best
    x, y = (int(v) for v in what.split(","))
    return [int(v) for v in pixels[y, x]]


print(json.dumps([answer(a) for a in sys.argv[1:]]))
