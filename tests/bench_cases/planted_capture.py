"""Capture's reading of a rim against rims that are wrong, since its two methods can't be made to
differ on demand. It imports the bench from where the container mounts it."""

import json
import sys
from typing import Any

import numpy as np

sys.path.insert(0, "/opt/bench")
from bench.capture import rim_differs, rim_reading  # type: ignore[import-not-found]  # noqa: E402

STEP = 0.25
COUNT = 160
ANGLES = np.linspace(0, 2 * np.pi, COUNT, endpoint=False)
POINTS = np.c_[np.cos(ANGLES), np.sin(ANGLES)] * COUNT * STEP / (2 * np.pi)


def read(lips: object) -> Any:
    return rim_reading(np.asarray(lips, dtype=float), POINTS, STEP, 0.1, 0.4)


even = read(np.full(COUNT, 1.9))
half = np.full(COUNT, 1.9)
half[: COUNT // 2] = 0.0
# As much of the edge free as `half`, in two stretches facing each other: the lip goes round.
facing = np.full(COUNT, 1.9)
facing[:40] = 0.0
facing[80:120] = 0.0
# Falls away at a window's corner: 1 mm walked for each 1 mm of lip.
ramp = np.full(COUNT, 1.9)
ramp[:40] = 0.0
for k in range(8):
    ramp[40 + k] = (k + 1) * STEP
    ramp[-1 - k] = (k + 1) * STEP
# A triangular pane's window meets its edge at a slant: it still rises faster than a taper.
slant = np.full(COUNT, 1.9)
slant[:40] = 0.0
for k in range(13):
    slant[40 + k] = (k + 1) * STEP * 0.58
    slant[-1 - k] = (k + 1) * STEP * 0.58
slanted = read(slant)
# Tapers gently down to nothing, over a long way.
taper = np.full(COUNT, 1.9)
taper[:20] = 0.0
taper[20:80] = np.linspace(0.05, 1.9, 60)
ramped, tapered = read(ramp), read(taper)
print(
    json.dumps(
        {
            "alike": rim_differs(even, read(np.full(COUNT, 1.9)), STEP),
            "rays_thinner": rim_differs(even, read(np.full(COUNT, 1.0)), STEP),
            "rays_more_open": rim_differs(even, read(ramp), STEP),
            "surrounds": rim_differs(read(facing), read(half), STEP),
            "rays_none": rim_differs(even, read(np.zeros(COUNT)), STEP),
            "rays_wider_is_natural": rim_differs(read(np.full(COUNT, 1.0)), even, STEP),
            "ramp": {"least": ramped["least_lip_mm"], "thin": ramped["thin_mm"]},
            "slant": {"least": slanted["least_lip_mm"], "thin": slanted["thin_mm"]},
            "taper": {"least": tapered["least_lip_mm"], "thin": tapered["thin_mm"]},
        }
    )
)
