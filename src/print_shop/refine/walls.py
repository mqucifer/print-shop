"""How wide a wall prints clean, and where infill starts, at a run's own profile.

mqucifer/print-shop#66.

On `eval-edges-1` Orca gap-filled 14.3 m inside 2 mm walls. Whole lines aren't the rule: swept from
1.50 to 2.60 mm through Orca 2.4.2 with that run's profile (mqucifer/print-shop#65,
`~/print-shop/proofs/m18-bench/walls/walls.json`), a wall printed clean to 1.66 mm, gap-filled in
every layer from 1.68 to 2.26, and took infill from 2.28. Its walls a side, an outer and an inner,
take twice their spacings, `2 × (outer + (loops - 1) × inner)`, each line's width less a layer's
`h × (1 - π/4)` as Orca spaces them: 1.568 mm, which is the sweep's gap line exactly (a 2.00 mm
wall's is 0.432). Where that leftover is too thin for a line, Orca drops it, and where it's wide
enough, infill takes it, but the two edges aren't a figure of the profile that one profile shows.
So the pass asks Orca: it slices a test wall at the run's own profile, a square tube, and bisects
each edge to `STEP`, from the wall the part has.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: How finely each edge is read, in mm: the sweep's own step.
STEP = 0.02
#: How far below and above the part's wall each edge is looked for, in mm, and in what strides.
REACH = 1.2
STRIDE = 0.1
#: The test wall: a square tube, as the sweep's, its outside and height in mm.
TUBE = (40.0, 30.0, 6.0)
#: Layers under this height are left out: the first layers are filled solid whatever the wall.
ABOVE_MM = 0.5

CLEAN, GAP, INFILL = "clean", "gap fill", "infill"


@dataclass(frozen=True)
class Edges:
    #: What the profile's walls a side take of a wall, by their spacings, in mm.
    walls_take_mm: float
    #: The widest wall Orca prints with no gap fill, or None if none was found in reach.
    clean_to_mm: float | None
    #: The narrowest wall Orca puts infill in, or None if none was found in reach.
    infill_from_mm: float | None
    #: Each width read, and what Orca laid in it.
    read: dict[float, str]


def spacing(width: float, layer: float) -> float:
    """A line's spacing as Orca lays lines side by side: its width less what a rounded line's
    sides leave between it and the next, `layer × (1 - π/4)`."""
    return width - layer * (1 - math.pi / 4)


def walls_take(outer: float, inner: float, loops: int, layer: float) -> float:
    """What a wall's lines a side take of it, both sides: the outer line and `loops - 1` inner."""
    return 2 * (spacing(outer, layer) + max(loops - 1, 0) * spacing(inner, layer))


def what_orca_lays(sliced: Any) -> str:
    """A test wall's slice read: infill, gap fill, or neither, above its first layers."""
    above = [x for x in sliced.layers if x.z > ABOVE_MM]
    if any(x.lengths.get("Sparse infill") or x.lengths.get("Internal solid infill") for x in above):
        return INFILL
    if any(x.lengths.get("Gap infill") for x in above):
        return GAP
    return CLEAN


def tube(wall: float, path: Path) -> Path:
    """The test wall as an STL: a square tube `wall` thick, as the sweep made them."""
    import trimesh

    x, y, z = TUBE
    outside = trimesh.creation.box(extents=(x, y, z))
    inside = trimesh.creation.box(extents=(x - 2 * wall, y - 2 * wall, z + 2))
    made = outside.difference(inside)
    made.apply_translation((x / 2, y / 2, z / 2))
    made.export(path)
    return path


def _grid(value: float) -> float:
    return round(round(value / STEP) * STEP, 4)


def edges(lays: Callable[[float], str], wall: float, taken: float) -> Edges:
    """Each edge of the gap-fill band round `wall`, a wall that gap-fills: the widest clean below
    and the narrowest with infill above, each the nearest to `wall`. From the wall outward in
    steps of `STRIDE` to the first width that's past the band, then bisected to `STEP` within that
    stride: Orca's widths aren't one band all the way down (a wall under 0.9 mm gap-fills again at
    the same profile), so each edge is looked for near the wall, not across the whole reach."""
    read: dict[float, str] = {}

    def at(width: float) -> str:
        width = _grid(width)
        if width not in read:
            read[width] = lays(width)
        return read[width]

    def nearest(way: int, past: str) -> float | None:
        """The first grid width from `wall`, going `way`, that's `past`, or None in reach."""
        inside = _grid(wall)
        step = 1
        while step * STRIDE <= REACH + 1e-9:
            probe = _grid(wall + way * step * STRIDE)
            if probe <= 0:
                return None
            if at(probe) == past:
                low, high = sorted((inside, probe))
                # Bisect between the last width in the band and the first past it.
                while _grid(high - low) > STEP:
                    middle = _grid((low + high) / 2)
                    if (at(middle) == past) == (way > 0):
                        high = middle
                    else:
                        low = middle
                return _grid(high if way > 0 else low)
            if at(probe) != GAP:
                return None
            inside = probe
            step += 1
        return None

    if at(wall) != GAP:
        return Edges(round(taken, 3), None, None, dict(sorted(read.items())))
    clean_to = nearest(-1, CLEAN)
    infill_from = nearest(+1, INFILL)
    return Edges(round(taken, 3), clean_to, infill_from, dict(sorted(read.items())))
