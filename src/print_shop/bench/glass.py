"""What a person sees through a stained-glass piece: the light through each pane, and how thick
each pane is. The stained-glass toolset's readings (mqucifer/print-shop#37), not the core's.

**Light** (`light FRAME PANE...`): each pane's footprint, less the frame above and below it, read
straight along z, as the piece hangs flat to the light. In all three stained-glass runs light was
measured by hand, outside the bench, and on one a floor went under the panes to make the frame
one piece. The first method cuts the frame at every layer through its height, and at each level
face, and takes their union as the frame's shadow: a pane's open share is its footprint less that
shadow. The second never touches a section: rays straight up through the frame, from a grid of
points inside each footprint, and a point is open where no ray meets the frame. Where the open
pieces are, the narrowest band of frame between two of them is the leading as a person sees it,
and rays along the line across it read it again.

**Thickness** (`thickness PANE...`): each pane's thickness straight through. The first method is
the fine mesh's height; the second, rays straight up through the pane from a grid, each reading
the material it crosses. A pane thicker in one place than the rays find reads as a disagreement,
said why.

Neither has a default limit. A limit is the request's: `--min-open` or `--max`, or a need's own
number. With none, each is a reading and says so.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import shapely
import shapely.ops

from .limits import over, said, under
from .report import Report
from .shapes import FINE, NoAnswer, Part, flat_heights, load, slice_at

#: A height this close to a level face is moved off it, in mm, so a section reads one side.
LEVEL = 2e-3
#: The least two open shares may differ by, in percentage points, before it's a disagreement:
#: the mesh's chords along a pane's edge, on a pane as small as the smallest the runs drew.
FLOOR_PCT = 0.5
#: A straight edge between grid points misclassifies, on average, a quarter of the spacing along
#: its length, and at worst all one way: so a share by the grid can be off by that much area.
EDGE_SHARE = 0.25
#: The step of the rays across the narrowest band, in mm: a tenth of a nozzle's width.
BAND_STEP = 0.04


def _heights(part: Part, layer: float) -> list[float]:
    """Every layer's middle through the part, and just inside each level face, so a lip thinner
    than a layer is cut too."""
    mesh = part.fine()
    low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
    count = max(1, int(np.ceil((high - low) / layer)))
    found = [low + (k + 0.5) * (high - low) / count for k in range(count)]
    for flat in flat_heights(mesh, least=0.01):
        for h in (flat["z"] + LEVEL, flat["z"] - LEVEL):
            if low < h < high:
                found.append(h)
    return sorted(set(round(h, 6) for h in found))


def shadow(frames: list[Part], layer: float) -> tuple[Any, int]:
    """The frame's shadow along z: the union of its sections, and how many were cut."""
    pieces = []
    cut = 0
    for part in frames:
        built = part.manifold()
        for h in _heights(part, layer):
            pieces.append(slice_at(built, h))
            cut += 1
    return shapely.unary_union(pieces), cut


def _footprint(pane: Part) -> Any:
    """What a pane presents to the light: its sections near its bottom, middle and top, as one."""
    mesh = pane.fine()
    low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
    middle = (low + high) / 2
    built = pane.manifold()
    cuts = [slice_at(built, h) for h in (low + LEVEL, middle, high - LEVEL) if low < h < high]
    return shapely.unary_union(cuts)


def grid(region: Any, count: int) -> tuple[np.ndarray, float]:
    """Points inside a region, about `count` of them, on a square grid offset by half a step."""
    x0, y0, x1, y1 = region.bounds
    step = float(np.sqrt(region.area / max(1, count)))
    xs = np.arange(x0 + step / 2, x1, step)
    ys = np.arange(y0 + step / 2, y1, step)
    grid = np.array([(x, y) for y in ys for x in xs])
    if not len(grid):
        return np.empty((0, 2)), step
    inside = shapely.contains_xy(region, grid[:, 0], grid[:, 1])
    return grid[inside], step


def _blocked(built: list[Any], x: float, y: float, low: float, high: float) -> bool:
    return any(m.ray_cast((x, y, low), (x, y, high)) for m in built)


def visible(pane: Any, cover: Any) -> tuple[Any, float]:
    """What of a pane the frame covers on neither face, and its share in percent: the pane less
    the frame's shadow. `light` reads it for each pane file, and `capture` for each pane it reads
    from the frame's openings, so the two are one measurement."""
    region = pane.difference(cover)
    return region, 100.0 * float(region.area) / float(pane.area)


def visible_by_rays(
    pane: Any, region: Any, built: list[Any], low: float, high: float, count: int
) -> tuple[float, int, float]:
    """The same share a different way: rays straight up through the frame from a grid in the
    pane, open where none meets it. Its share, how many rays, and how far a grid that fine can be
    from the area, in percentage points."""
    points, step = grid(pane, count)
    if not len(points):
        raise NoAnswer("a pane is too small for a ray to land in")
    clear = sum(1 for x, y in points if not _blocked(built, float(x), float(y), low, high))
    edges = float(pane.boundary.length) + float(region.boundary.length)
    band = max(FLOOR_PCT, 100.0 * EDGE_SHARE * edges * step / float(pane.area))
    return 100.0 * clear / len(points), len(points), band


def span(frames: list[Part]) -> tuple[float, float]:
    """Heights below and above every frame part, for a ray straight up through them all."""
    lows, highs = zip(*(np.asarray(p.fine().bounds)[:, 2] for p in frames), strict=True)
    return float(min(lows)) - 1.0, float(max(highs)) + 1.0


def _pieces(region: Any) -> list[Any]:
    if region.is_empty:
        return []
    return list(getattr(region, "geoms", [region]))


def run_light(args: Any, report: Report) -> None:
    frames = [load(name) for name in [args.frame, *args.with_files]]
    for part in frames:
        report.notes += part.notes
    panes = [(name, load(name)) for name in args.panes]
    cover, cut = shadow(frames, args.layer)
    built = [p.manifold() for p in frames]
    low, high = span(frames)
    report.line(
        f"the frame ({', '.join(p.name for p in frames)}) cut at {cut} heights, every layer of "
        f"{args.layer} mm and just inside each level face; light read straight along z"
    )
    rows: list[dict[str, Any]] = []
    differs: list[str] = []
    opened: list[tuple[str, Any]] = []
    for name, pane in panes:
        foot = _footprint(pane)
        if foot.is_empty or foot.area <= 0:
            raise NoAnswer(f"{name} has no section to read: is it a solid?")
        open_region, pct = visible(foot, cover)
        pct_rays, rays, band = visible_by_rays(foot, open_region, built, low, high, args.rays)
        x0, y0, x1, y1 = foot.bounds
        row = {
            "file": name,
            "centre": [round((x0 + x1) / 2, 2), round((y0 + y1) / 2, 2)],
            "footprint_mm2": round(float(foot.area), 3),
            "open_mm2": round(float(open_region.area), 3),
            # The figure `--min-open` judges, as it is: a need reads the same (`limits`).
            "open_pct": pct,
            "open_pct_rays": round(pct_rays, 2),
            "rays": rays,
            "band_pct": round(band, 2),
        }
        rows.append(row)
        report.line(
            f"{name}: {pct:.1f}% open straight through ({row['open_mm2']} of "
            f"{row['footprint_mm2']} mm²), at {row['centre'][0]},{row['centre'][1]}"
        )
        if abs(pct - pct_rays) > band:
            differs.append(
                f"{name} is {pct:.1f}% open by the sections and {pct_rays:.1f}% by rays, "
                f"more than the {band:.1f} points a grid of {rays} can be off by"
            )
        opened += [(name, piece) for piece in _pieces(open_region)]
    report.numbers["panes"] = rows
    least = min(rows, key=lambda r: r["open_pct"])
    report.fact(
        "least_open_pct",
        least["open_pct"],
        how=f"{least['file']}: its footprint less the frame's shadow",
    )
    report.numbers["least_open_file"] = least["file"]
    report.second(
        "rays straight up through the frame from a grid in each pane's footprint: "
        + "; ".join(f"{r['file']} {r['open_pct_rays']:.1f}% of {r['rays']}" for r in rows)
    )
    _narrowest_band(opened, built, low, high, report, differs)
    problems = []
    if args.min_open is not None:
        short = [r for r in rows if under(r["open_pct"], args.min_open)]
        if short:
            named = ", ".join(
                f"{r['file']} {said(r['open_pct'], args.min_open, 1)}%" for r in short
            )
            problems.append(
                f"{len(short)} pane(s) under the {args.min_open:g}% open asked for: {named}"
            )
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems))
    elif args.min_open is not None:
        report.verdict(
            "OK",
            f"every pane is at least {args.min_open:g}% open: the least is {least['file']}, "
            f"{least['open_pct']:.1f}%",
        )
    else:
        report.verdict(
            "OK",
            f"a reading, with no limit given: the least open is {least['file']}, "
            f"{least['open_pct']:.1f}%",
        )


def _narrowest_band(
    opened: list[tuple[str, Any]],
    built: list[Any],
    low: float,
    high: float,
    report: Report,
    differs: list[str],
) -> None:
    """The narrowest frame between two open pieces, as a person sees the leading."""
    if len(opened) < 2:
        report.note("fewer than two open pieces, so there's no band of frame between them")
        report.numbers["narrowest_band_mm"] = None
        return
    best: tuple[float, int, int] | None = None
    for i in range(len(opened)):
        for j in range(i + 1, len(opened)):
            gap = float(opened[i][1].distance(opened[j][1]))
            if best is None or gap < best[0]:
                best = (gap, i, j)
    assert best is not None
    gap, i, j = best
    near, far = shapely.ops.nearest_points(opened[i][1], opened[j][1])
    report.fact(
        "narrowest_band_mm",
        round(gap, 4),
        how=f"the least distance between two open pieces, of {opened[i][0]} and {opened[j][0]}",
    )
    report.numbers["narrowest_band_from"] = [round(near.x, 3), round(near.y, 3)]
    report.numbers["narrowest_band_to"] = [round(far.x, 3), round(far.y, 3)]
    # Rays straight up along the line across it: the length of it the frame covers.
    steps = max(2, int(np.ceil(gap / BAND_STEP)) + 1)
    line = np.linspace([near.x, near.y], [far.x, far.y], steps)
    covered = sum(1 for x, y in line[1:-1] if _blocked(built, float(x), float(y), low, high))
    across = gap * covered / max(1, steps - 1)
    report.second(
        f"rays straight up along the line across the narrowest band, every {gap / (steps - 1):.3f}"
        f" mm: the frame covers {across:.3f} mm of it"
    )
    if abs(across - gap) > 2 * gap / max(1, steps - 1) + 2 * FINE[0]:
        differs.append(
            f"the narrowest band is {gap:.3f} mm between open pieces, and rays across it find "
            f"{across:.3f} mm of frame"
        )


def run_thickness(args: Any, report: Report) -> None:
    rows: list[dict[str, Any]] = []
    differs: list[str] = []
    # A pane drawn at the limit reads a hair over it on the mesh's chords.
    hair = 2 * FINE[0]
    for name in args.panes:
        pane = load(name)
        report.notes += pane.notes
        mesh = pane.fine()
        low, high = (float(v) for v in np.asarray(mesh.bounds)[:, 2])
        height = high - low
        foot = _footprint(pane)
        points, _ = grid(foot, args.rays)
        if not len(points):
            raise NoAnswer(f"{name} is too small for a ray to land in")
        built = pane.manifold()
        runs = []
        for x, y in points:
            met = built.ray_cast((float(x), float(y), low - 1.0), (float(x), float(y), high + 1.0))
            zs = sorted(float(h.position[2]) for h in met)
            # In and out in pairs: the material a ray crosses is the sum of each pair's length.
            runs.append(sum(zs[k + 1] - zs[k] for k in range(0, len(zs) - 1, 2)))
        found = [r for r in runs if r > 0]
        if not found:
            raise NoAnswer(f"no ray through {name} met any material")
        row = {
            "file": name,
            # The figures `--max` and `--min` judge, as they are.
            "thickest_mm": height,
            "thickest_rays_mm": round(max(found), 4),
            "thinnest_rays_mm": min(found),
            "z_from": round(low, 4),
            "z_to": round(high, 4),
            "rays": len(points),
        }
        rows.append(row)
        report.line(
            f"{name}: {height:.3f} mm thick, from z {low:.3f} to {high:.3f}; rays read "
            f"{row['thinnest_rays_mm']:.3f} to {row['thickest_rays_mm']:.3f}"
        )
        if abs(max(found) - height) > hair + 0.01 * height:
            differs.append(
                f"{name} is {height:.3f} mm from its lowest to its highest point, and the "
                f"thickest any ray crossed is {max(found):.3f} mm: it isn't flat, or it's "
                "thickest where no ray passed"
            )
    report.numbers["files"] = rows
    thickest = max(rows, key=lambda r: r["thickest_mm"])
    thinnest = min(rows, key=lambda r: r["thinnest_rays_mm"])
    report.fact("thickest_mm", thickest["thickest_mm"], how=f"{thickest['file']}, by its height")
    report.numbers["thickest_file"] = thickest["file"]
    report.fact(
        "thinnest_mm",
        thinnest["thinnest_rays_mm"],
        how=f"{thinnest['file']}, the least any ray crossed",
    )
    report.numbers["thinnest_file"] = thinnest["file"]
    report.second(
        "rays straight up through each pane from a grid: "
        + "; ".join(f"{r['file']} at most {r['thickest_rays_mm']:.3f} mm" for r in rows)
    )
    problems = []
    if args.max is not None:
        thick = [r for r in rows if over(r["thickest_mm"], args.max + hair)]
        if thick:
            named = ", ".join(f"{r['file']} {said(r['thickest_mm'], args.max)} mm" for r in thick)
            problems.append(f"{len(thick)} pane(s) thicker than the {args.max:g} mm asked: {named}")
    if args.min is not None:
        thin = [r for r in rows if under(r["thinnest_rays_mm"], args.min - hair)]
        if thin:
            named = ", ".join(
                f"{r['file']} {said(r['thinnest_rays_mm'], args.min)} mm" for r in thin
            )
            problems.append(f"{len(thin)} pane(s) thinner than the {args.min:g} mm asked: {named}")
    if differs:
        report.verdict("DISAGREE", "; ".join(differs))
    elif problems:
        report.verdict("FAIL", "; ".join(problems))
    else:
        limits = [
            f"at most {args.max:g} mm" if args.max is not None else "",
            f"at least {args.min:g} mm" if args.min is not None else "",
        ]
        asked = " and ".join(x for x in limits if x)
        held = f", {asked} as asked" if asked else ", a reading with no limit given"
        chords = (args.max is not None and over(thickest["thickest_mm"], args.max)) or (
            args.min is not None and under(thinnest["thinnest_rays_mm"], args.min)
        )
        if chords:
            held += f", within the mesh's {hair:g} mm of chords"
        report.verdict(
            "OK", f"the thickest pane is {thickest['file']}, {thickest['thickest_mm']:.3f} mm{held}"
        )
