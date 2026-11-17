"""`pieces trace`: a stained-glass picture read into a piece map, once, the same way every time
(mqucifer/print-shop#152).

The picture is flat colours between dark lines, inside a dark outer frame, as the image model
composes a scene. Its dark pixels are the lead, and each patch of colour between them is a piece:

1. **The panel** is the picture inside its outer frame: the dark rows and columns at its edges,
   past any margin. `--width` is the panel's width in mm, which sets the scale.
2. **The lead** is every pixel with each channel under `--dark`, closed by a square `HAIR_MM`
   across and at least 3 pixels: a hairline gap in a line doesn't join two pieces, and a hair
   of colour beside a line isn't glass. The reply says how much of the panel that was.
3. **A piece** is a patch of the rest, joined edge to edge. A patch under `SPECK_PX` pixels is a
   speck between lines, and is lead.
4. **Its outline** is its pixels' own edges, with each corner kept at its pixel's corner and
   the staircase between two corners read as the line or the curve it stands for
   (`_smooth_ring`). In mm, x right and y up from the panel's bottom-left.
5. **Its colour** is the nearest of `--palette` to the picture's own over the piece, or with no
   palette the nearest colour the bench has a name for. How far the picture's colour runs
   across the piece is kept in the map, for `pieces check`'s gradient.

The operator's prototype read the same pixels with OpenCV and scikit-image. This uses what the
bench's image already has: SciPy's labels and Shapely.

**Second method.** The map is drawn back as pixels at the picture's own scale and laid over the
patches it came from: the pixels that are glass in one and lead in the other can be no more
than a pixel's width along every outline. And every patch is one piece.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from . import colours, piecemap
from .piecemap import Map, Piece
from .pieces_check import counts_said, draw, fixes_said, limits_of, read_map, say
from .report import NoAnswer, Report

#: A patch of colour under this many pixels is a speck between lines: lead, not a piece.
SPECK_PX = 40
#: A gap between dark pixels narrower than this, in mm, and never under 3 pixels, is lead.
HAIR_MM = 0.5
#: A row or a column of the picture's edge is the outer frame where this share of it is dark.
FRAME_SHARE = 0.6
#: A corner is read over this many pixels each side, where the way along the outline turns by
#: more than this many degrees: a staircase's own turns over that reach are under 20.
CORNER_PX = 6
CORNER_TURN = 40.0
#: A smoothed outline is nowhere further than this from its pixels' edges, in pixels, or the
#: pixels' edges stand, less each point within `STAIR_PX` of the line through its neighbours.
OFF_PX = 1.5
STAIR_PX = 1.05
#: A point of the pixels' outline with a straight run this long on both sides is a corner as
#: drawn, whatever is near it.
SIDE_PX = 4
#: Between corners an outline's points are averaged over this many neighbours, an odd number.
SMOOTH_PX = 5
#: And lose each point within this of the line through its neighbours, in pixels: a shallow
#: slope's steps, a pixel tall and many apart, stand half a pixel either side of the line they
#: stand for, and averaging doesn't take them out.
SIMPLIFY_PX = 0.6
#: The fewest pixels across the panel a trace reads: under this a lead line is a pixel or two.
LEAST_PX = 200
#: A piece's colour is read from at most this many of its pixels, away from its edge.
COLOUR_PX = 20_000


def palette_of(text: str | None) -> dict[str, tuple[int, int, int]] | None:
    """`blue,red,gold=#f5c832`: each colour a piece may be."""
    if not text:
        return None
    found: dict[str, tuple[int, int, int]] = {}
    for word in text.split(","):
        name, sep, value = word.strip().partition("=")
        name = name.strip().lower()
        if sep:
            if not colours.HEX.fullmatch(value.strip()):
                raise NoAnswer(f"--palette: {word!r} isn't NAME=#rrggbb")
            code = value.strip().lstrip("#")
            found[name] = (int(code[0:2], 16), int(code[2:4], 16), int(code[4:6], 16))
        elif name in colours.NAMED:
            found[name] = colours.NAMED[name]
        else:
            raise NoAnswer(
                f"--palette: the bench has no colour named {name!r}: give it as {name}=#rrggbb, "
                f"or one of {', '.join(sorted(colours.NAMED))}"
            )
    if not found:
        raise NoAnswer("--palette names one colour or more")
    return found


def _run(flags: np.ndarray) -> int:
    """How many of the first entries are set, in a row."""
    clear = np.flatnonzero(~flags)
    return int(clear[0]) if len(clear) else len(flags)


def panel_of(dark: np.ndarray) -> tuple[slice, slice, dict[str, int]]:
    """The panel: the picture inside its outer frame, as a window of it, and the frame's
    thickness on each side in pixels. A side with no frame is the picture's own edge, less any
    margin with nothing dark in it."""
    rows = np.flatnonzero(dark.any(axis=1))
    cols = np.flatnonzero(dark.any(axis=0))
    if not len(rows):
        raise NoAnswer("the picture has no dark lines: nothing in it is under --dark")
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    inside = dark[r0:r1, c0:c1]
    across = inside.mean(axis=1) > FRAME_SHARE
    down = inside.mean(axis=0) > FRAME_SHARE
    frame = {
        "top": _run(across),
        "bottom": _run(across[::-1]),
        "left": _run(down),
        "right": _run(down[::-1]),
    }
    if not any(frame.values()):
        # No frame at all: the whole picture is the panel, margins and all.
        return slice(0, dark.shape[0]), slice(0, dark.shape[1]), frame
    top, bottom = r0 + frame["top"], r1 - frame["bottom"]
    left, right = c0 + frame["left"], c1 - frame["right"]
    if bottom - top < 3 or right - left < 3:
        raise NoAnswer("the picture is dark all over: nothing in it is over --dark")
    return slice(top, bottom), slice(left, right), frame


def _outline(mask: np.ndarray, row0: int, col0: int) -> Any:
    """One patch's outline as a polygon in pixels, x right and y down from the panel's
    top-left: its pixels' own edges, each run of pixels along a row a rectangle and the patch
    their union, so a corner is a pixel's corner and nothing is cut off it."""
    edges = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    rows, starts = np.nonzero(edges == 1)
    _, ends = np.nonzero(edges == -1)
    runs = shapely.box(col0 + starts, row0 + rows, col0 + ends, row0 + rows + 1)
    parts = piecemap.polygons(shapely.union_all(runs))
    return parts[0] if parts else None


def _smooth_ring(ring: np.ndarray) -> np.ndarray | None:
    """A ring of pixel edges as an outline: its corners kept where they are, and the staircase
    between them read as the line or the curve it stands for.

    The ring's points are a pixel apart, at the middle of each pixel's edge. A corner is a
    point of the ring with a straight run of `SIDE_PX` pixels on both sides, or where the way
    along the ring, read over `CORNER_PX` pixels before and after, turns more than
    `CORNER_TURN` degrees, at the pixel's corner that stands furthest out there. Between two
    corners the points are averaged over `SMOOTH_PX` neighbours, which takes the stairs out,
    and lose every point within `SIMPLIFY_PX` of the line through its neighbours."""
    steps, lengths = [], []
    for k in range(len(ring)):
        a, b = ring[k], ring[(k + 1) % len(ring)]
        n = max(1, int(round(float(np.abs(b - a).sum()))))
        lengths.append(n)
        steps.append(a + (np.arange(n)[:, None] + 0.5) / n * (b - a))
    dense = np.concatenate(steps)
    count = len(dense)
    if count < 4 * CORNER_PX:
        return None
    # A point of the ring with a straight run of `SIDE_PX` pixels or more on both sides is a
    # corner as drawn: a stair's riser is shorter.
    first = np.concatenate([[0], np.cumsum(lengths)[:-1]])
    drawn = {
        int(first[k]): ring[k]
        for k in range(len(ring))
        if lengths[k] >= SIDE_PX and lengths[k - 1] >= SIDE_PX
    }
    before = dense - np.roll(dense, CORNER_PX, axis=0)
    after = np.roll(dense, -CORNER_PX, axis=0) - dense
    turn = np.abs(
        np.degrees(
            np.arctan2(
                before[:, 0] * after[:, 1] - before[:, 1] * after[:, 0],
                (before * after).sum(axis=1),
            )
        )
    )
    window = np.stack([np.roll(turn, shift) for shift in range(-CORNER_PX, CORNER_PX + 1)])
    peaks = np.flatnonzero((turn > CORNER_TURN) & (turn >= window.max(axis=0)))
    # One corner where two points share a peak.
    corners = [int(k) for n, k in enumerate(peaks) if n == 0 or k - peaks[n - 1] > CORNER_PX]
    if len(corners) > 1 and corners[0] + count - corners[-1] <= CORNER_PX:
        corners.pop()
    spots = {}
    for k in corners:
        chord_a, chord_b = dense[k - CORNER_PX], dense[(k + CORNER_PX) % count]
        way = chord_b - chord_a
        # Among the ring's own points within reach of it along the ring: across a neck a
        # few pixels wide, the other side's are as near, and aren't this corner.
        along = np.minimum((first - k) % count, (k - first) % count)
        near = ring[along <= CORNER_PX]
        if not len(near):
            continue
        off = np.abs(way[0] * (near[:, 1] - chord_a[1]) - way[1] * (near[:, 0] - chord_a[0]))
        spots[k] = near[int(np.argmax(off))]
    # A corner as drawn stands; one read from the turn within reach of it is the same corner.
    for k in list(spots):
        if any(min((k - d) % count, (d - k) % count) <= CORNER_PX for d in drawn):
            del spots[k]
    spots.update(drawn)
    corners = sorted(spots)
    kernel = np.ones(SMOOTH_PX) / SMOOTH_PX
    half = SMOOTH_PX // 2
    if not corners:
        wrapped = np.vstack([dense[-half:], dense, dense[:half]])
        smooth = np.column_stack(
            [np.convolve(wrapped[:, axis], kernel, mode="valid") for axis in (0, 1)]
        )
        line = shapely.LinearRing(smooth).simplify(SIMPLIFY_PX)
        return np.asarray(line.coords)[:-1]
    found = []
    for n, start in enumerate(corners):
        stop = corners[(n + 1) % len(corners)]
        stop = stop if stop > start else stop + count
        run = dense[np.arange(start + 1, stop) % count]
        if len(run) >= SMOOTH_PX:
            padded = np.vstack(
                [np.repeat(run[:1], half, axis=0), run, np.repeat(run[-1:], half, axis=0)]
            )
            run = np.column_stack(
                [np.convolve(padded[:, axis], kernel, mode="valid") for axis in (0, 1)]
            )
        points = np.vstack([spots[start], run[half:-half] if len(run) > 2 * half else run[:0],
                            spots[stop % count]])  # fmt: skip
        line = shapely.LineString(points).simplify(SIMPLIFY_PX)
        found.append(np.asarray(line.coords)[:-1])
    return np.concatenate(found)


def _smooth(shape: Any) -> Any:
    """A patch's pixel outline with its stairs read as lines and curves, where that's still
    one valid piece within a pixel's width of the pixels; else as it is."""
    rings = []
    for ring in (shape.exterior, *shape.interiors):
        smooth = _smooth_ring(np.asarray(ring.coords)[:-1])
        if smooth is None or len(smooth) < 3:
            return shape
        rings.append(smooth)
    made = shapely.Polygon(rings[0], rings[1:])
    if not made.is_valid or made.geom_type != "Polygon":
        return shape
    near = made.boundary.hausdorff_distance(shape.boundary) <= OFF_PX
    if near and made.symmetric_difference(shape).area <= 1.0 * shape.length:
        return made
    # Smoothing lost the pixels somewhere, as at a neck a few pixels wide: the pixels' own
    # edges then, less each point within a stair's height of the line through its neighbours.
    plain = shape.simplify(STAIR_PX, preserve_topology=True)
    return plain if plain.is_valid and plain.geom_type == "Polygon" else shape


def _paint(rgb: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
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


def _nearest(rgb: Any, palette: dict[str, tuple[int, int, int]]) -> str:
    return min(palette, key=lambda name: math.dist(rgb, palette[name]))


def trace(picture: Any, width_mm: float, dark_under: int, palette: Any) -> dict[str, Any]:
    """The picture's pieces as a map, with the patches it came from and what was left out."""
    from scipy import ndimage

    rgb = np.asarray(picture.convert("RGB"))
    dark = rgb.max(axis=2) < dark_under
    rows, cols, frame = panel_of(dark)
    rgb, dark = rgb[rows, cols], dark[rows, cols]
    height, width = dark.shape
    if width < LEAST_PX:
        raise NoAnswer(
            f"the panel is {width} pixels across: a trace needs {LEAST_PX} or more, so a lead "
            "line is more than a pixel or two"
        )
    mm = width_mm / width
    # A gap in the lead under `HAIR_MM` wide, a hairline break in a line or a hair of colour
    # beside one, is lead: no nozzle prints glass that thin.
    side = max(3, 2 * int(HAIR_MM / mm / 2.0) + 1)
    # Past the panel's edge is the frame, so a hair of colour along the edge is closed too.
    lines = ndimage.binary_closing(
        np.pad(dark, side, constant_values=True), structure=np.ones((side, side))
    )
    lines = lines[side:-side, side:-side]
    labels, count = ndimage.label(~lines)
    sizes = np.atleast_1d(ndimage.sum(~lines, labels, np.arange(1, count + 1)))
    specks = [k for k in range(1, count + 1) if sizes[k - 1] < SPECK_PX]
    panel = shapely.box(0.0, 0.0, width * mm, height * mm)
    pieces: list[Piece] = []
    patches: list[int] = []
    raw: list[Any] = []
    lost: list[int] = []
    for k, window in enumerate(ndimage.find_objects(labels), start=1):
        if k in specks or window is None:
            continue
        mask = labels[window] == k
        shape = _outline(mask, window[0].start, window[1].start)
        if shape is None:
            lost.append(k)
            continue
        pixels = shape
        shape = _smooth(shape)
        fine = shapely.transform(shape, lambda p: np.column_stack([p[:, 0], height - p[:, 1]]) * mm)
        fine = piecemap.polygons(fine.intersection(panel))
        if not fine:
            lost.append(k)
            continue
        pieces.append(Piece(0, "", fine[0], paint=_paint(rgb[window], mask)))
        raw.append(
            piecemap.polygons(
                shapely.transform(
                    pixels, lambda p: np.column_stack([p[:, 0], height - p[:, 1]]) * mm
                ).intersection(panel)
            )[0]
        )
        patches.append(k)
    if not pieces:
        raise NoAnswer("no patch of colour between the dark lines is large enough to be a piece")
    for k, piece in enumerate(pieces):
        # As the map will keep it, to a tenth of a micron: a sliver's outline can cross itself
        # once its points are rounded.
        kept = shapely.set_precision(piece.shape, 10.0**-piecemap.PLACES)
        if kept.geom_type != "Polygon" or not kept.is_valid or kept.is_empty:
            piece.shape = raw[k]
    tree = shapely.STRtree([p.shape for p in pieces])
    crossed: set[int] = set()
    for a, b in tree.query([p.shape for p in pieces], predicate="intersects").T:
        if a < b and pieces[a].shape.intersection(pieces[b].shape).area > piecemap.OVERLAP_MM2:
            crossed |= {int(a), int(b)}
    for k in crossed:
        # Two smoothed outlines cross over a line a pixel or two wide: each keeps its pixels'.
        pieces[k].shape = raw[k]
    # Numbered from the top-left, a row at a time, as a person reads the picture.
    band = max(height * mm / 12.0, 1e-6)

    def reading_order(k: int) -> tuple[float, float]:
        x, y = shapely.get_coordinates(pieces[k].shape.representative_point())[0]
        return (-math.floor(y / band), x)

    order = sorted(range(len(pieces)), key=reading_order)
    pieces = [pieces[k] for k in order]
    patches = [patches[k] for k in order]
    named = palette or colours.NAMED
    table: dict[str, dict[str, Any]] = {}
    for number, piece in enumerate(pieces, start=1):
        piece.id = number
        assert piece.paint is not None
        piece.colour = _nearest(piece.paint["rgb"], named)
        table.setdefault(piece.colour, {"rgb": [], "pieces": 0})
        table[piece.colour]["rgb"].append(piece.paint["rgb"])
        table[piece.colour]["pieces"] += 1
    for name, entry in table.items():
        own = [int(round(v)) for v in np.mean(entry["rgb"], axis=0)]
        entry["rgb"] = list(palette[name]) if palette else own
        entry["in_picture"] = own
    found = Map(panel, 1.0, table, pieces)
    found.lead_mm = max(piecemap.usual_lead(found), mm)
    return {
        "map": found,
        "labels": labels,
        "patches": patches,
        "specks": len(specks),
        "hairs_pct": 100.0 * float((lines & ~dark).mean()),
        "hair_px": side,
        "speck_mm2": SPECK_PX * mm * mm,
        "lost": lost,
        "frame": frame,
        "mm": mm,
        "size_px": [width, height],
        "kept_fine": len(crossed),
        "picture": picture.crop((cols.start, rows.start, cols.stop, rows.stop)),
    }


def laid_over(found: Map, traced: dict[str, Any]) -> dict[str, Any]:
    """The map drawn back at the picture's own scale, over the patches it came from: the share
    of the panel's pixels that are glass in both or lead in both, the share a pixel's width
    along every outline allows to differ, and each patch that isn't one piece."""
    drawn = piecemap.pixels(found, 1.0 / traced["mm"])
    labels = traced["labels"]
    height = min(labels.shape[0], drawn.ids.shape[0])
    width = min(labels.shape[1], drawn.ids.shape[1])
    ids, labels = drawn.ids[:height, :width], labels[:height, :width]
    patch_of = dict(zip(traced["patches"], [p.id for p in found.pieces], strict=True))
    meant = np.zeros(int(labels.max()) + 1, dtype=np.int32)
    for patch, number in patch_of.items():
        meant[patch] = number
    wanted = meant[labels]
    same = float((ids == wanted).mean()) * 100.0
    rims = sum(p.shape.length for p in found.pieces) / traced["mm"]
    return {
        "agree_pct": same,
        "allowed_pct": 100.0 * rims / (height * width),
        "pieces": len(found.pieces),
        "patches": len(traced["patches"]),
    }


def run_trace(args: Any, report: Report) -> None:
    from PIL import Image

    source = Path(args.image)
    if not source.is_file():
        raise NoAnswer(f"no file {args.image} in the working directory")
    if not args.width > 0:
        raise NoAnswer("--width is the panel's width in mm, over 0")
    try:
        picture = Image.open(source)
        picture.load()
    except Exception as exc:  # noqa: BLE001 - whatever it is, it isn't a picture
        raise NoAnswer(f"{args.image} doesn't read as a picture: {exc}") from exc
    palette = palette_of(args.palette)
    traced = trace(picture, args.width, args.dark, palette)
    found: Map = traced["map"]
    limits = limits_of(args)
    reading = read_map(found, limits)
    found.flags = [
        {k: v for k, v in f.items() if k != "said"} | {"figure": round(float(f["figure"]), 4)}
        for f in reading.flags
    ]
    found.source = {
        "by": "pieces trace",
        "image": source.name,
        "px_per_mm": round(1.0 / traced["mm"], 4),
        "dark_under": args.dark,
        "pieces": len(found.pieces),
        "glass_share_pct": round(reading.numbers["glass_share_pct"], 4),
        "lead_widest_mm": round(reading.numbers["lead_widest_mm"], 4),
    }
    target = args.to or f"{source.stem}.pieces.json"
    piecemap.save(found, target)
    report.wrote(target)
    report.fact("map", target, how="the piece map, for every other `pieces` command")
    frame = traced["frame"]
    width, height = traced["size_px"]
    report.fact(
        "panel_px", [width, height],
        how="inside the outer frame: "
        + (
            ", ".join(f"{side} {px} px" for side, px in frame.items())
            if any(frame.values())
            else "the picture has none, so the whole picture is the panel"
        ),
    )  # fmt: skip
    report.fact("px_per_mm", 1.0 / traced["mm"], how="from --width")
    report.fact(
        "specks", traced["specks"],
        how=f"patches under {SPECK_PX} pixels, {traced['speck_mm2']:.2f} mm², read as lead",
    )  # fmt: skip
    report.fact(
        "hairs_pct", traced["hairs_pct"], "%",
        f"of the panel: colour in gaps under {traced['hair_px']} pixels wide, read as lead",
    )  # fmt: skip
    report.numbers["frame_px"] = frame
    report.numbers["colours"] = {
        name: {"pieces": entry["pieces"], "rgb": entry["rgb"]}
        for name, entry in found.colours.items()
    }
    report.line(
        "colours: "
        + ", ".join(f"{entry['pieces']} {name}" for name, entry in found.colours.items())
        + (" (the nearest of --palette)" if palette else " (the nearest named colour to each)")
    )
    say(found, reading, report, limits)
    over = laid_over(found, traced)
    report.numbers["laid_over"] = over
    report.second(
        f"the map drawn back over the picture's patches: {over['agree_pct']:.2f}% of the "
        f"panel's pixels are the same piece or lead in both, where a pixel's width along every "
        f"outline is {over['allowed_pct']:.2f}%; {over['patches']} patch(es), "
        f"{over['pieces']} piece(s)"
    )
    shown = draw(found, reading, Path(args.out), source.stem, beside=traced["picture"])
    report.wrote(shown)
    report.numbers["picture"] = shown
    if traced["kept_fine"]:
        report.note(
            f"{traced['kept_fine']} piece(s) keep their pixels' own edges: smoothed, two of "
            "them would overlap across a line a pixel or two wide"
        )
    differs = list(reading.differs)
    if traced["lost"]:
        differs.append(f"{len(traced['lost'])} patch(es) of the picture have no outline")
    if 100.0 - over["agree_pct"] > over["allowed_pct"] + 0.05:
        differs.append(
            f"the map drawn back differs from the picture's patches in "
            f"{100.0 - over['agree_pct']:.2f}% of the panel's pixels, more than the "
            f"{over['allowed_pct']:.2f}% a pixel's width along every outline allows"
        )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:6]))
        return
    if reading.flags:
        report.note(
            f"the map breaks glass-cutting rules as traced. To fix {fixes_said(reading.flags)}. "
            f"`pieces check {target}` reads it again after each fix"
        )
    report.verdict(
        "OK",
        f"{len(found.pieces)} piece(s) traced into {target}, {args.width:g} by "
        f"{height * traced['mm']:.1f} mm; glass share {reading.numbers['glass_share_pct']:.2f}%; "
        f"rule breaks: {counts_said(reading.flags)}",
    )
