"""A made-up flat plate of 5,600 triangles, built by hand so every platform gets the same one
(mqucifer/print-shop#235).

A ring 176 x 176 x 4 mm, both its edges waved. Standing on edge, nearly every one of its points
is at a height of its own, which is what the old reading was slow on. Nobody's part.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

__all__ = ["wavy_plate", "write_plate"]

#: The points round each of its two edges: each gives 4 triangles.
_AROUND = 700
_THICK_MM = 4.0


def wavy_plate() -> trimesh.Trimesh:
    turn = np.linspace(0, 2 * np.pi, _AROUND, endpoint=False)
    outer = 80 + 8 * np.sin(23 * turn)
    inner = 30 + 5 * np.sin(17 * turn + 0.4)
    rings = [
        np.column_stack([radius * np.cos(turn), radius * np.sin(turn), np.full(_AROUND, z)])
        for z in (0.0, _THICK_MM)
        for radius in (outer, inner)
    ]
    at = np.arange(_AROUND)
    on = (at + 1) % _AROUND
    out_low, in_low, out_high, in_high = (n * _AROUND for n in range(4))

    def strip(a: int, b: int) -> list[np.ndarray]:
        """The band from ring `a` to ring `b`, facing out of the part."""
        return [np.column_stack([a + at, a + on, b + on]),
                np.column_stack([a + at, b + on, b + at])]

    faces = np.concatenate([
        *strip(out_low, out_high),  # its outside
        *strip(in_high, in_low),  # the hole's wall
        *strip(out_high, in_high),  # its top
        *strip(in_low, out_low),  # its underside
    ])
    return trimesh.Trimesh(vertices=np.concatenate(rings), faces=faces, process=False)


def write_plate(directory: Path) -> Path:
    path = directory / "wavy_plate.stl"
    wavy_plate().export(path)
    return path
