"""The picture a piece map was traced from, read again where a command needs it
(mqucifer/print-shop#172).

`pieces trace` reads a picture once. Three things read it again, each over one place:
`pieces retrace` traces a box of it, `pieces paint` says how far a colour is from the
picture's own in a piece, and `pieces check` reads the colour's run across a piece a fix
has changed, so a merge can't hide a gradient.

The map says which picture: `source.image`, and `source.image_path` as the trace was given it.
The panel is found as the trace found it, inside the outer frame at the trace's own darkness
(`source.dark_under`), or the whole picture where it was traced `--whole`
(`source.whole_picture`), so a pixel here is the pixel the trace read. A picture whose panel
isn't the map's size isn't the one it came from, and has no answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import piecemap
from .piecemap import Map
from .report import NoAnswer

#: A pixel is lead where every channel is under this, of 255, unless `--dark` says.
DEFAULT_DARK = 40
#: A row or a column of the picture's edge is the outer frame where this share of it is dark,
#: and the frame starts within this share of the picture from its edge.
FRAME_SHARE = 0.6
FRAME_WITHIN = 0.25
#: A piece's colour is read from at most this many of its pixels, away from its edge.
COLOUR_PX = 20_000
#: A picture is the map's own where its panel is the map's size to this many pixels.
SAME_PX = 1.5


def _run(flags: np.ndarray) -> int:
    """How many of the first entries are set, in a row."""
    clear = np.flatnonzero(~flags)
    return int(clear[0]) if len(clear) else len(flags)


def _frame_side(dark_share: np.ndarray) -> tuple[int, int]:
    """Where the frame lies along one side, from the picture's edge inward: the first row or
    column, within the outer `FRAME_WITHIN` of the picture, that's dark over `FRAME_SHARE` of
    its length, and how many in a row from there are. A margin before it, with whatever specks
    it has, is outside the frame. (0, 0) where the side has no frame."""
    flags = dark_share > FRAME_SHARE
    outer = np.flatnonzero(flags[: max(1, int(len(flags) * FRAME_WITHIN))])
    if not len(outer):
        return 0, 0
    first = int(outer[0])
    return first, _run(flags[first:])


def panel_of(dark: np.ndarray) -> tuple[slice, slice, dict[str, int]]:
    """The panel: the picture inside its outer frame on all four sides, as a window of it, and
    the frame's thickness on each side in pixels. Whatever lies outside the frame, a margin and
    anything in it, isn't the panel. A side with no frame is the picture's own edge there, less
    any margin with nothing dark in it."""
    if not dark.any():
        raise NoAnswer("the picture has no dark lines: nothing in it is under --dark")
    height, width = dark.shape
    rows, cols = slice(0, height), slice(0, width)
    found: dict[str, tuple[int, int]] = {}
    # Twice: a side's share is read over the span the other two sides' frames leave, so a
    # margin beside the frame doesn't thin it.
    for _ in range(2):
        across = dark[:, cols].mean(axis=1)
        down = dark[rows, :].mean(axis=0)
        found = {
            "top": _frame_side(across),
            "bottom": _frame_side(across[::-1]),
            "left": _frame_side(down),
            "right": _frame_side(down[::-1]),
        }
        rows = slice(found["top"][0], height - found["bottom"][0])
        cols = slice(found["left"][0], width - found["right"][0])
    frame = {side: run for side, (_, run) in found.items()}
    if not any(frame.values()):
        # No frame at all: the whole picture is the panel, margins and all.
        return slice(0, height), slice(0, width), frame
    lit_rows = np.flatnonzero(dark.any(axis=1))
    lit_cols = np.flatnonzero(dark.any(axis=0))
    top = sum(found["top"]) if frame["top"] else int(lit_rows[0])
    bottom = height - sum(found["bottom"]) if frame["bottom"] else int(lit_rows[-1]) + 1
    left = sum(found["left"]) if frame["left"] else int(lit_cols[0])
    right = width - sum(found["right"]) if frame["right"] else int(lit_cols[-1]) + 1
    if bottom - top < 3 or right - left < 3:
        raise NoAnswer("the picture is dark all over: nothing in it is over --dark")
    return slice(top, bottom), slice(left, right), frame


def paint_of(rgb: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """The picture's colour over a patch, away from its edge: its mean, and how far it runs
    from one end of the patch to the other, read two ways."""
    from scipy import ndimage

    core = ndimage.binary_erosion(mask, iterations=2)
    if core.sum() < 12:
        core = mask
    rows, cols = np.nonzero(core)
    if len(rows) > COLOUR_PX:
        pick = np.linspace(0, len(rows) - 1, COLOUR_PX).astype(int)
        rows, cols = rows[pick], cols[pick]
    values = rgb[rows, cols].astype(float)
    mean = values.mean(axis=0)
    paint: dict[str, Any] = {
        "rgb": [int(round(v)) for v in mean],
        "spread": round(float(np.linalg.norm(values.std(axis=0))), 2),
        "run": 0.0,
        "run_fitted": 0.0,
    }
    if len(rows) < 24:
        return paint
    # A plane fitted to each channel: the way the colour changes fastest, and by how much from
    # one end of the patch to the other along it.
    at = np.stack([cols - cols.mean(), rows - rows.mean()], axis=1).astype(float)
    slopes, *_ = np.linalg.lstsq(np.column_stack([at, np.ones(len(at))]), values - mean, rcond=None)
    _, strength, ways = np.linalg.svd(slopes[:2].T)
    along = at @ ways[0]
    low, high = np.percentile(along, [10.0, 90.0])
    paint["run_fitted"] = round(float(strength[0] * (high - low)), 2)
    # The same two ends by their own colours: the tenth of the patch at each.
    ends = values[along <= low].mean(axis=0) - values[along >= high].mean(axis=0)
    paint["run"] = round(float(np.linalg.norm(ends)), 2)
    return paint


@dataclass
class Picture:
    """A picture's panel at its own scale: row 0 is the panel's top, and a pixel is `mm` wide."""

    rgb: np.ndarray
    mm: float
    name: str
    #: The darkness the map was traced at: a pixel under it on every channel is lead.
    under: int = DEFAULT_DARK

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[0]), int(self.rgb.shape[1])

    def cover(self, shape: Any) -> tuple[Any, np.ndarray]:
        """The pixels whose centres a shape covers, as a window of the panel and a mask in it."""
        height, _ = self.size
        return piecemap._cover(shape, 0.0, height * self.mm, self.mm, self.size)

    def paint(self, shape: Any, under: int | None = None) -> dict[str, Any] | None:
        """The picture's colour over a piece, as `pieces trace` reads it, or None where the
        piece covers no pixel's centre. Only its pixels that aren't lead in the picture, at
        the trace's darkness or `under`: a line a merge took out is still drawn under the
        piece's glass, and its dark isn't the glass's colour."""
        window, inside = self.cover(shape)
        rgb = self.rgb[window]
        inside = inside & ~(rgb.max(axis=2) < (self.under if under is None else under))
        if not inside.any():
            return None
        return paint_of(rgb, inside)


def named(found: Map, given: str | None = None) -> str | None:
    """The picture's file: as given, or as the map's source says, the first of those there."""
    if given:
        return given
    said = [found.source.get("image_path"), found.source.get("image")]
    names = [name for name in said if isinstance(name, str) and name]
    return next((name for name in names if Path(name).is_file()), names[0] if names else None)


def picture_of(found: Map, given: str | None = None) -> Picture:
    """The panel of the picture a map was traced from, or why it can't be read."""
    from PIL import Image

    name = named(found, given)
    if name is None:
        raise NoAnswer(
            "the map doesn't say which picture it was traced from: give --image FILE, the "
            "picture `pieces trace` read"
        )
    if not Path(name).is_file():
        raise NoAnswer(
            f"the picture the map was traced from, {name}, isn't in the working directory: "
            "give --image FILE"
        )
    try:
        opened = Image.open(name)
        opened.load()
    except Exception as exc:  # noqa: BLE001 - whatever it is, it isn't a picture
        raise NoAnswer(f"{name} doesn't read as a picture: {exc}") from exc
    rgb = np.asarray(opened.convert("RGB"))
    under = found.source.get("dark_under")
    under = under if isinstance(under, int) and not isinstance(under, bool) else DEFAULT_DARK
    whole_picture = found.source.get("whole_picture") is True
    if not whole_picture:
        rows, cols, _ = panel_of(rgb.max(axis=2) < under)
        rgb = rgb[rows, cols]
    x0, y0, x1, y1 = found.panel.bounds
    height, width = rgb.shape[:2]
    mm = (x1 - x0) / width
    whole = found.panel.symmetric_difference(shapely.box(0.0, 0.0, x1, y1)).area < 1e-9
    if (x0, y0) != (0.0, 0.0) or not whole or abs((y1 - y0) / mm - height) > SAME_PX:
        raise NoAnswer(
            f"{name} isn't the picture this map was traced from: "
            + ("the whole picture is " if whole_picture else "inside its frame it's ")
            + f"{width} by {height} pixels, and the map's panel is {x1 - x0:g} by {y1 - y0:g} mm, "
            f"which at {1.0 / mm:.3f} pixels a mm is {(y1 - y0) / mm:.1f} pixels tall"
        )
    return Picture(rgb, mm, name, under)
