"""A section drawing's check against one drawn the old way, which painted each hole over any island
inside it (`cup-1`). Prints which pieces each picture leaves out. It imports the bench from where
the container mounts it, and reads a fixture `make_fixtures.py` wrote."""

import json
import sys
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, "/opt/bench")
from bench.render import _pieces_missing, _section_picture  # type: ignore[import-not-found]  # noqa: E402
from bench.shapes import load, slice_at  # type: ignore[import-not-found]  # noqa: E402

SIZE = 256
part = load("island/frame-island.step")
section = slice_at(part.manifold(), 2.0)


def old_way() -> Any:
    """Smallest piece first, each hole filled white over whatever was drawn before it."""
    low, high = np.asarray(section.bounds).reshape(2, 2)
    scale = 0.9 * SIZE / float(max(high - low))
    margin = (SIZE - (high - low) * scale) / 2

    def pixel(ring: Any) -> list[tuple[float, float]]:
        xy = (np.asarray(ring.coords) - low) * scale + margin
        return [(float(x), float(SIZE - y)) for x, y in xy]

    picture = Image.new("RGB", (SIZE, SIZE), "white")
    draw = ImageDraw.Draw(picture)
    for poly in section.geoms:
        draw.polygon(pixel(poly.exterior), fill=(70, 70, 80))
        for hole in poly.interiors:
            draw.polygon(pixel(hole), fill=(255, 255, 255))
    return np.asarray(picture)


print(
    json.dumps(
        {
            "pieces": len(section.geoms),
            "now": _pieces_missing(section, _section_picture(part, 2.0, SIZE), SIZE),
            "old_way": _pieces_missing(section, old_way(), SIZE),
        }
    )
)
