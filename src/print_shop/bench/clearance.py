"""Interference and clearance between parts, by OCCT and by a mesh, side by side.

OCCT's booleans go wrong on near-touching faces and say nothing: in the trial 11 of 66 cases
in FreeCAD and 25 of 66 in CadQuery, often as zero overlap, with `common` and `cut` agreeing
with each other. So a STEP pair is measured three ways (OCCT common, OCCT cut, Manifold on
the mesh), and a pair where they differ gets no single answer. An STL has no exact surface,
so its second method is a stack of plane sections clipped by Shapely, and points on one
surface measured to the other.

**A gap range** (mqucifer/print-shop#60). `--min-gap` alone let a lid too loose to stay on pass.
`--max-gap G` fails a pair whose least gap is over G, `TOO LOOSE`, so parts meant to mate are held
from both ends. A gap drawn at G passes: the mesh's chords on both faces can read it up to
`2 * FINE` wider, as `wall --min-wall` holds a wall drawn at its limit. A pair that touches or
overlaps has a least gap of 0, which is never too loose: a lid resting on its seat touches it, and
the play round its lip is read with the lid lifted off its seat (the `designing-enclosures`
skill), which the reply says.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import shapely
import trimesh
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from .report import Report
from .shapes import FINE, VOLUME_FLOOR, VOLUME_SHARE, NoAnswer, Part, load, slice_at

#: Closer than this is touching, in mm.
TOUCHING = 1e-4
#: How many sections the mesh-only second check stacks, and points it samples.
SECTIONS = 96
SAMPLES = 6000
#: Worst first: the pairs' verdict is the worst of them.
ORDER = ["DISAGREE", "INTERFERE", "TOO CLOSE", "TOO LOOSE", "TOUCHING", "CLEAR"]


def _apart(a: Part, b: Part, margin: float = 1e-3) -> bool:
    low_a, high_a = a.bounds()
    low_b, high_b = b.bounds()
    return bool(np.any(high_a + margin < low_b) or np.any(high_b + margin < low_a))


def _occt(a: Part, b: Part) -> dict[str, Any]:
    """OCCT's overlap twice over and its distance. A boolean that raises is reported as such."""
    found: dict[str, Any] = {}
    apart = _apart(a, b)
    measures: dict[str, Callable[[], float]] = {
        "common": lambda: float(a.shape.intersect(b.shape).Volume()),
        "cut": lambda: float(a.shape.Volume() - a.shape.cut(b.shape).Volume()),
    }
    for name, measure in measures.items():
        try:
            found[name] = 0.0 if apart else measure()
        except Exception as exc:  # noqa: BLE001 - OCCT's failure is a finding, not a crash
            found[name] = None
            found[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:200]
    distance = BRepExtrema_DistShapeShape(a.shape.wrapped, b.shape.wrapped)
    found["distance"] = float(distance.Value()) if distance.IsDone() else None
    return found


def _sections_overlap(a: Part, b: Part) -> float:
    """The overlap's volume from plane sections, clipped in 2D and stacked."""
    low = max(a.bounds()[0][2], b.bounds()[0][2])
    high = min(a.bounds()[1][2], b.bounds()[1][2])
    if high <= low:
        return 0.0
    step = (high - low) / SECTIONS
    total = 0.0
    for k in range(SECTIONS):
        z = low + (k + 0.5) * step
        total += float(
            shapely.intersection(slice_at(a.manifold(), z), slice_at(b.manifold(), z)).area
        )
    return float(total * step)


def _sampled_gap(a: Part, b: Part) -> tuple[float, float]:
    """Points on a's surface measured to b's: (nearest, how far apart the points are)."""
    points, _ = trimesh.sample.sample_surface(a.fine(), SAMPLES, seed=7)
    _, distance, _ = trimesh.proximity.closest_point(b.fine(), points)  # type: ignore[no-untyped-call]
    return float(np.min(distance)), float(np.sqrt(a.fine().area / SAMPLES))


def measure_pair(
    a: Part, b: Part, min_gap: float | None, max_gap: float | None = None
) -> dict[str, Any]:
    """Every figure for a pair, whether the methods agree, and the pair's verdict."""
    fine, coarse = a.manifold(True) ^ b.manifold(True), a.manifold(False) ^ b.manifold(False)
    overlap, overlap_coarse = float(fine.volume()), float(coarse.volume())
    # The chords matter only where the overlap has surface, and at most where the parts curve.
    chords = min(FINE[0] * float(fine.surface_area()), a.chord_band() + b.chord_band())
    band = max(VOLUME_FLOOR, VOLUME_SHARE * overlap, chords, 2 * abs(overlap_coarse - overlap))
    gap_band = 2 * FINE[0] + TOUCHING
    pair: dict[str, Any] = {
        "a": a.name,
        "b": b.name,
        "overlap_mesh_mm3": round(overlap, 4),
        "overlap_band_mm3": round(band, 4),
    }
    differs: list[str] = []
    if a.is_brep and b.is_brep:
        occt = _occt(a, b)
        pair.update(
            overlap_occt_common_mm3=occt["common"],
            overlap_occt_cut_mm3=occt["cut"],
            gap_occt_mm=occt["distance"],
        )
        for name in ("common", "cut"):
            if occt[name] is None:
                differs.append(f"OCCT {name} failed: {occt[f'{name}_error']}")
            elif abs(occt[name] - overlap) > band:
                differs.append(f"OCCT {name} says {occt[name]:.3f} mm³, the mesh {overlap:.3f}")
        search = (occt["distance"] or 0.0) + 1.0
        gap = float(a.manifold().min_gap(b.manifold(), search))
        if occt["distance"] is None:
            differs.append("OCCT's distance didn't finish")
        elif abs(occt["distance"] - gap) > gap_band:
            differs.append(f"OCCT's gap is {occt['distance']:.4f} mm, the mesh's {gap:.4f}")
    else:
        stacked = _sections_overlap(a, b)
        # A stack's error is its steps' outlines: a share of the overlap, and a little at least.
        stack_band = max(0.02, 0.05 * overlap)
        pair["overlap_sections_mm3"] = round(stacked, 4)
        if abs(stacked - overlap) > stack_band:
            differs.append(f"sections say {stacked:.3f} mm³, the mesh {overlap:.3f}")
        sampled, spacing = _sampled_gap(a, b)
        low, high = a.bounds(), b.bounds()
        far = float(np.linalg.norm(np.maximum(low[0] - high[1], high[0] - low[1]))) + 1.0
        gap = float(a.manifold().min_gap(b.manifold(), far))
        pair["gap_sampled_mm"] = round(sampled, 4)
        # Sampled points only ever find a gap as large or larger, by about their spacing.
        if sampled < gap - gap_band or sampled > gap + 2 * spacing + gap_band:
            differs.append(f"sampled points say {sampled:.4f} mm, the mesh {gap:.4f}")
    pair["gap_mesh_mm"] = round(gap, 4)
    if differs:
        pair["verdict"] = "DISAGREE"
    elif overlap > band:
        pair["verdict"] = "INTERFERE"
    elif min_gap is not None and gap < min_gap:
        pair["verdict"] = "TOO CLOSE"
    elif max_gap is not None and gap > max_gap + 2 * FINE[0]:
        pair["verdict"] = "TOO LOOSE"
    elif gap <= TOUCHING:
        pair["verdict"] = "TOUCHING"
    else:
        pair["verdict"] = "CLEAR"
    pair["differs"] = differs
    return pair


def run(args: Any, report: Report) -> None:
    parts = [load(path) for path in args.files]
    for part in parts:
        report.notes += part.notes
    items = [piece for part in parts for piece in part.pieces()]
    if len(items) < 2:
        raise NoAnswer(f"needs two parts or a file of several solids: found {len(items)}")
    pairs = [(items[i], items[j]) for i in range(len(items)) for j in range(i + 1, len(items))]
    max_gap = args.max_gap
    results = [measure_pair(a, b, args.min_gap, max_gap) for a, b in pairs]
    report.numbers["pairs"] = results
    brep = all(a.is_brep and b.is_brep for a, b in pairs)
    for r in results:
        line = (
            f"{r['a']} | {r['b']}: {r['verdict']}, overlap {r['overlap_mesh_mm3']:.3f} mm³ "
            f"± {r['overlap_band_mm3']:.3f}, gap {r['gap_mesh_mm']:.4f} mm (mesh at {FINE[0]} mm)"
        )
        report.line(line)
        if brep:
            common, cut = r["overlap_occt_common_mm3"], r["overlap_occt_cut_mm3"]
            report.second(
                f"{r['a']} | {r['b']}: OCCT common {_shown(common)} mm³, cut {_shown(cut)} mm³, "
                f"distance {_shown(r['gap_occt_mm'], 4)} mm"
            )
        else:
            report.second(
                f"{r['a']} | {r['b']}: {SECTIONS} sections say {r['overlap_sections_mm3']:.3f} "
                f"mm³, {SAMPLES} sampled points say gap {r['gap_sampled_mm']:.4f} mm"
            )
        report.notes += r["differs"]
    if max_gap is not None:
        report.line(
            f"gap range: {_range(args.min_gap, max_gap)}, a gap drawn at {max_gap:g} mm "
            f"passing within the mesh's chords ({2 * FINE[0]:g} mm)"
        )
        seated = [r for r in results if r["verdict"] in ("TOUCHING", "INTERFERE")]
        if seated:
            report.note(
                f"{len(seated)} pair(s) touch or overlap, so their least gap is 0 and --max-gap "
                "can't read them: a part resting on its seat touches it. To read the play round "
                "a lid's lip or a part in its recess, lift it off its seat along the way it "
                f"closes by more than {max_gap:g} mm, in a file of its own, and run this on that: "
                "the least gap is then the play"
            )
    worst = min((r["verdict"] for r in results), key=ORDER.index)
    counts = {w: sum(r["verdict"] == w for r in results) for w in ORDER}
    said = ", ".join(f"{n} {w.lower()}" for w, n in counts.items() if n)
    report.verdict(worst, f"{len(results)} pair(s): {said}")


def _range(low: float | None, high: float) -> str:
    return f"{low:g} to {high:g} mm" if low is not None else f"at most {high:g} mm"


def _shown(value: float | None, digits: int = 3) -> str:
    return "failed" if value is None else f"{value:.{digits}f}"
