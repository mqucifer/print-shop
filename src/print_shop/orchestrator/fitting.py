"""A part that fits the plate only turned on it (mqucifer/print-shop#257).

Orca is told never to turn a part (`--allow-rotations=0`), so what it can't take as posed it
refuses (`orca.DoesntFit`). It's the judge of that, not the bed's size: it keeps margins of its
own, the skirt's room and the bed's excluded corner. So a part refused is tried turned about z,
each turn sliced as posed, and the first that Orca takes is the turn the review offers. The
slice says it was turned (`SliceResult.turn_deg`), and nothing turned reaches the approval gate
until the person has taken that turn at the review.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from print_shop.core.state import Orientation, SliceResult
from print_shop.ingest.orientation import _rotation_matrix, write_rotated
from print_shop.slicer.orca import DoesntFit, Slicer, SlicerProfiles

__all__ = ["Fitting", "turns", "with_turns"]

#: The turns tried first, anticlockwise from above: the quarters, then the diagonals, the one
#: that keeps clear of the Centauri's excluded front right corner first.
_PLAIN = (90, 270, 180, 45, 315, 135, 225)
#: Then every 15 degrees, as the room can be less than the box: a skirt, the excluded corner.
_STEP = 15


def turns(
    stl: str | Path, build_volume: tuple[float, float, float],
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[float, ...]:
    """The turns about z that put the part, lying in `rotation`, inside the build volume's box,
    in the order they're tried: the plain ones, the one with most room to spare, to a degree,
    and its opposite, then every `_STEP`. () is that no turn can."""
    mesh = trimesh.load(Path(stl), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"Expected a Trimesh, got {type(mesh).__name__}")
    points = np.asarray(mesh.vertices) @ _rotation_matrix(rotation)[:3, :3].T
    if np.ptp(points[:, 2]) > build_volume[2]:
        return ()
    spare = []
    for angle in np.radians(np.arange(180)):  # a half turn has the same box
        c, s = np.cos(angle), np.sin(angle)
        across, along = np.ptp(points[:, :2] @ np.array([[c, s], [-s, c]]), axis=0)
        spare.append(min(build_volume[0] - across, build_volume[1] - along))
    most = int(np.argmax(spare[1:])) + 1
    tried = dict.fromkeys((*_PLAIN, most, most + 180, *range(_STEP, 360, _STEP)))
    return tuple(float(deg) for deg in tried if spare[deg % 180] >= 0)


def with_turns(
    stl: str | Path, found: tuple[Orientation, ...], build_volume: tuple[float, float, float],
    kept: tuple[float, float, float] | None,
) -> tuple[Orientation, ...]:
    """The poses, marked where one fits only turned on the plate, so that it's offered that turn
    rather than refused. Only `kept`, the pose the person or the part's context lays it in, or,
    where no pose fits as posed, any: a pose that fits as posed is never passed over for one
    that has to be turned. One that no turn fits is marked too, so it isn't sent back to."""
    none_fits = not any(c.fits_in(build_volume) for c in found)

    def marked(c: Orientation) -> Orientation:
        if c.fits_in(build_volume):
            return c
        if not turns(stl, build_volume, c.rotation_deg):
            return c.model_copy(update={"fits_no_way": True})
        if none_fits or c.rotation_deg == kept:
            return c.model_copy(update={"fits_turned": True})
        return c

    return tuple(marked(c) for c in found)


class Fitting:
    """A `Slicer` that slices as posed and, only where that doesn't fit, turned, saying so."""

    def __init__(self, slicer: Slicer, build_volume: tuple[float, float, float]) -> None:
        self._slicer = slicer
        self._build_volume = build_volume

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        """The slice, with the turn it took to fit, or 0.

        Raises:
            DoesntFit: If it fits neither as posed nor in any turn tried.
        """
        try:
            return self._slicer(stl_path, profiles, outdir)
        except DoesntFit as refused:
            for deg in turns(stl_path, self._build_volume):
                turned = write_rotated(
                    stl_path, (0.0, 0.0, deg), Path(outdir) / f"turned-{deg:g}.stl")
                try:
                    result = self._slicer(turned, profiles, outdir)
                except DoesntFit:
                    continue
                return result.model_copy(update={"turn_deg": deg})
            raise DoesntFit(
                "it doesn't fit the plate as posed, and no turn on the plate that was tried "
                "fits it either; nothing was sent to the printer"
            ) from refused
