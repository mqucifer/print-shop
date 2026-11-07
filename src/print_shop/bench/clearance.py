"""Interference and clearance between parts, by OCCT and by a mesh, side by side.

OCCT's booleans go wrong on near-touching faces and say nothing: in the trial 11 of 66 cases
in FreeCAD and 25 of 66 in CadQuery, often as zero overlap, with `common` and `cut` agreeing
with each other. So a STEP pair is measured three ways (OCCT common, OCCT cut, Manifold on
the mesh), and a pair where they differ gets no single answer. An STL has no exact surface,
so its second method is a stack of plane sections clipped by Shapely, and points on one
surface measured to the other.

**A gap range** (mqucifer/print-shop#60). `--min-gap` alone let a lid too loose to stay on pass.
`--max-gap G` fails a pair whose least gap is over G, `TOO LOOSE`, so parts meant to mate are held
from both ends. A pair that touches or overlaps has a least gap of 0, which is never too loose: a
lid resting on its seat touches it, which the reply says.

**The gap a verdict reads** (mqucifer/print-shop#65). On a STEP pair it's OCCT's exact distance,
and the mesh's is its second check, within `2 * FINE`; on an STL pair it's the mesh's, which is the
file's own surface. Both limits hold that figure as it is, to `ARITHMETIC`. The mesh can't be the
verdict on a STEP pair: its chords cut into a concave face, such as a pocket's rounded corner, so
it reads a gap drawn on a curve short. On `eval-fit-2` a pane 0.25 mm from its pocket all round,
by OCCT 0.2500, was `TOO CLOSE` under `--min-gap 0.25` on the mesh's figure. Allowing the chords
under `--min-gap` instead would pass a gap truly short by them; the exact distance passes neither.

**A lift** (mqucifer/print-shop#65). A seated lid touches its seat, so the play round its lip is
read with it lifted off: `--lift D` moves the last file named D mm along `--along` (z unless said)
before measuring, and the file itself stays where it is. The need is then on the printed file. On
`eval-lid-1` the play was read on a copy of the lid lifted 2 mm, as the docs said then, and `done`
refused a need on a file that isn't printed (mqucifer/print-shop#64). D must be more than either
limit, or a lift short of the play would read as the gap; a gap that reads the lift itself is said.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import cadquery as cq
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
#: How far a limit holds the gap a verdict reads past it, in mm: arithmetic's noise, not a band.
ARITHMETIC = 1e-6
#: The unit step along each axis `--along` names.
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
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
    # The verdict's gap: OCCT's exact distance on a STEP pair, the mesh's being only chords of it.
    exact = pair.get("gap_occt_mm")
    read = exact if exact is not None else gap
    pair["gap_mm"] = round(read, 4)
    pair["gap_by"] = "occt" if exact is not None else "mesh"
    if differs:
        pair["verdict"] = "DISAGREE"
    elif overlap > band:
        pair["verdict"] = "INTERFERE"
    elif min_gap is not None and read < min_gap - ARITHMETIC:
        pair["verdict"] = "TOO CLOSE"
    elif max_gap is not None and read > max_gap + ARITHMETIC:
        pair["verdict"] = "TOO LOOSE"
    elif read <= TOUCHING:
        pair["verdict"] = "TOUCHING"
    else:
        pair["verdict"] = "CLEAR"
    pair["differs"] = differs
    return pair


def lifted(part: Part, along: str, mm: float) -> Part:
    """The part moved `mm` along an axis, as a new part: its file is untouched."""
    step = np.asarray(AXES[along]) * mm
    if part.is_brep:
        return Part(part.name, shape=part.shape.translate(cq.Vector(*step)), notes=part.notes)
    assert part._mesh is not None
    return Part(part.name, mesh=part._mesh.copy().apply_translation(step), notes=part.notes)


def run(args: Any, report: Report) -> None:
    parts = [load(path) for path in args.files]
    lift = getattr(args, "lift", None)
    along = getattr(args, "along", "z")
    if lift is not None:
        moved = parts[-1].name
        parts[-1] = lifted(parts[-1], along, lift)
        way = {"z": ("up", "down"), "x": ("+x", "-x"), "y": ("+y", "-y")}[along][lift < 0]
        report.numbers["lift"] = {"file": moved, "along": along, "mm": lift}
        report.line(
            f"lifted: {moved}, moved {abs(lift):g} mm {way} along {along} before measuring, and "
            "only here: its file is where it was, and every figure below is with it moved"
        )
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
        by = "OCCT's exact distance" if r["gap_by"] == "occt" else "the mesh, the STL's own surface"
        line = (
            f"{r['a']} | {r['b']}: {r['verdict']}, overlap {r['overlap_mesh_mm3']:.3f} mm³ "
            f"± {r['overlap_band_mm3']:.3f}, gap {r['gap_mm']:.4f} mm by {by}"
        )
        if r["gap_by"] == "occt":
            line += f" (the mesh at {FINE[0]} mm reads {r['gap_mesh_mm']:.4f})"
        report.line(line)
        if brep:
            common, cut = r["overlap_occt_common_mm3"], r["overlap_occt_cut_mm3"]
            report.second(
                f"{r['a']} | {r['b']}: OCCT common {_shown(common)} mm³, cut {_shown(cut)} mm³; "
                f"the mesh's gap {r['gap_mesh_mm']:.4f} mm against OCCT's "
                f"{_shown(r['gap_occt_mm'], 4)}, within the chords on both ({2 * FINE[0]:g} mm)"
            )
        else:
            report.second(
                f"{r['a']} | {r['b']}: {SECTIONS} sections say {r['overlap_sections_mm3']:.3f} "
                f"mm³, {SAMPLES} sampled points say gap {r['gap_sampled_mm']:.4f} mm"
            )
        report.notes += r["differs"]
    if args.min_gap is not None or max_gap is not None:
        exact = "OCCT's exact distance" if brep else "the mesh's distance"
        report.line(
            f"gap range: {_range(args.min_gap, max_gap)}, held against {exact} as it is: a gap "
            "drawn at a limit passes"
        )
    if lift is not None:
        _lift_said(report, results, report.numbers["lift"])
    elif args.min_gap is not None or max_gap is not None:
        seated = [r for r in results if r["verdict"] == "INTERFERE" or r["gap_mm"] <= TOUCHING]
        if seated:
            most = max(g for g in (args.min_gap, max_gap) if g is not None)
            report.note(
                f"{len(seated)} pair(s) touch or overlap, so their least gap is 0 and the gap "
                "range can't read them: a part resting on its seat touches it. To read the play "
                "round a lid's lip or a part in its recess, run this again with --lift D: it "
                "moves the last file named D mm along the way it closes (--along, z unless said) "
                f"before measuring, D more than {most:g} mm, so the least gap is the play, and the "
                "need stays on the printed file"
            )
    worst = min((r["verdict"] for r in results), key=ORDER.index)
    counts = {w: sum(r["verdict"] == w for r in results) for w in ORDER}
    said = ", ".join(f"{n} {w.lower()}" for w, n in counts.items() if n)
    report.verdict(worst, f"{len(results)} pair(s): {said}")


def _lift_said(report: Report, results: list[dict[str, Any]], lift: dict[str, Any]) -> None:
    """What a lifted pair's gap is: the play, or the lift itself where a face that seats meets."""
    moved, mm = lift["file"], abs(lift["mm"])
    for r in results:
        if moved not in (r["a"].split("#")[0], r["b"].split("#")[0]):
            continue
        if r["verdict"] in ("TOUCHING", "INTERFERE"):
            report.note(
                f"{r['a']} | {r['b']} still touch or overlap with {moved} lifted {mm:g} mm: "
                "it isn't lifted off what it rests on. Lift it the way it comes off"
            )
        elif abs(r["gap_mm"] - mm) <= 2 * FINE[0] + TOUCHING:
            report.note(
                f"{r['a']} | {r['b']}: the least gap is the lift, {mm:g} mm, where the faces "
                f"that seat meet, so the play round the lip is at least {mm:g} mm and isn't read. "
                "Lift it more than the play to read it, while the lip stays in its recess"
            )


def _range(low: float | None, high: float | None) -> str:
    if low is not None and high is not None:
        return f"{low:g} to {high:g} mm"
    return f"at least {low:g} mm" if low is not None else f"at most {high:g} mm"


def _shown(value: float | None, digits: int = 3) -> str:
    return "failed" if value is None else f"{value:.{digits}f}"
