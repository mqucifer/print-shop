"""A render's check against pictures that are wrong, since the renderer can't be made to fail
on demand. Prints each one's finding. It imports the bench from where the container mounts it."""

import json
import sys
from typing import Any

import numpy as np

sys.path.insert(0, "/opt/bench")
from bench.render import outline_problem  # type: ignore[import-not-found]  # noqa: E402

SIZE = 256


def picture(width: int, height: int) -> Any:
    pixels = np.full((SIZE, SIZE, 3), 255, dtype=np.uint8)
    pixels[20 : 20 + height, 20 : 20 + width] = (90, 90, 120)
    return pixels


print(
    json.dumps(
        {
            "right": outline_problem(picture(200, 100), (100.0, 50.0), SIZE)[0],
            "blank": outline_problem(picture(0, 0), (100.0, 50.0), SIZE)[0],
            "wrong_shape": outline_problem(picture(200, 200), (100.0, 50.0), SIZE)[0],
            "clipped": outline_problem(
                np.full((SIZE, SIZE, 3), 60, dtype=np.uint8), (100.0, 50.0), SIZE
            )[0],
        }
    )
)
