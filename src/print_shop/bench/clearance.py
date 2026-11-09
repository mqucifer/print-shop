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

**Places** (mqucifer/print-shop#72). A pair's least gap is its tightest face. On `eval-fit-3` a pane
had 0.25 mm of play round its edge and 0.15 mm on its faces, and the edge's play couldn't be read on
the printed part; on `eval-fan-1` a clip arm's side play couldn't be read apart from the seated
plate's contact. `--box NAME=...` reads each pair's least gap between their surfaces inside a box:
the parts' material in it, whose least distance, as a box is convex, is between their surfaces in
it, never a section or a copy. `--facing NAME=DIR` reads it between faces facing DIR and faces
facing back, on a STEP pair. Each place is read by the pair's own two methods, and with a place
named the gap range holds each place, not the pair's least. Every limit is `limits`' rule.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import cadquery as cq
import manifold3d as m3
import numpy as np
import shapely
import trimesh
from OCP.BRepExtrema import BRepExtrema_DistShapeShape

from .limits import ARITHMETIC, over, said, under
from .report import Report
from .shapes import (
    FINE,
    VOLUME_FLOOR,
    VOLUME_SHARE,
    NoAnswer,
    Part,
    _tessellated,
    load,
    slice_at,
)
from .slide import summary, what_holds

#: Closer than this is touching, in mm.
TOUCHING = 1e-4
#: How many sections the mesh-only second check stacks, and points it samples.
SECTIONS = 96
SAMPLES = 6000
#: The unit step along each axis `--along` names.
AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
#: Worst first: the pairs' verdict is the worst of them.
ORDER = ["DISAGREE", "INTERFERE", "TOO CLOSE", "TOO LOOSE", "TOUCHING", "CLEAR"]
#: With `--slide`, worst first: the hold, then an overlap of parts that don't move. A pair's own
#: touch or gap is a reading beside it (mqucifer/print-shop#76).
SLIDE_ORDER = ["DISAGREE", "NOT HELD", "INTERFERE", "HELD", "TOO CLOSE", "TOO LOOSE", "TOUCHING",
               "CLEAR"]  # fmt: skip
#: A place that catches no surface of one of a pair: it gives no figure, and judges nothing.
NOT_CAUGHT = "NOT CAUGHT"
#: A face is `--facing` a way where its outward normal stays within this of it, in degrees,
#: everywhere it's read: `edges`' figure, so a rounded corner turning through 90° is neither way.
FACING_DEG = 60.0
#: Over a curved face, its normal is read on a grid of so many points each way.
FACING_GRID = 5
#: The directions `--facing` takes, as `edges` takes them.
DIRECTIONS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}


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
    # The figure the limits judge, as it is: a need on it reads the same (`limits`).
    pair["gap_mm"] = read
    pair["gap_by"] = "occt" if exact is not None else "mesh"
    if differs:
        pair["verdict"] = "DISAGREE"
    elif overlap > band:
        pair["verdict"] = "INTERFERE"
    elif min_gap is not None and under(read, min_gap):
        pair["verdict"] = "TOO CLOSE"
    elif max_gap is not None and over(read, max_gap):
        pair["verdict"] = "TOO LOOSE"
    elif read <= TOUCHING:
        pair["verdict"] = "TOUCHING"
    else:
        pair["verdict"] = "CLEAR"
    pair["differs"] = differs
    return pair


@dataclass
class Place:
    """A place a gap is read at: a box, or the faces facing a way (mqucifer/print-shop#72)."""

    name: str
    low: np.ndarray | None = None
    high: np.ndarray | None = None
    way: str | None = None

    def words(self) -> str:
        if self.way is not None:
            return f"between faces facing {self.way} and faces facing back, within {FACING_DEG:g}°"
        assert self.low is not None and self.high is not None
        return f"in the box from {_point(self.low)} to {_point(self.high)}"

    def plain(self) -> dict[str, Any]:
        if self.way is not None:
            return {"facing": self.way}
        assert self.low is not None and self.high is not None
        return {"box": [_at(self.low), _at(self.high)]}


def places(args: Any) -> list[Place]:
    """Each `--box` and `--facing`, by its name, as `cli` parsed them."""
    found = [
        Place(name, low=np.asarray(low, dtype=float), high=np.asarray(high, dtype=float))
        for name, (low, high) in getattr(args, "boxes", None) or []
    ]
    found += [Place(name, way=way) for name, way in getattr(args, "facings", None) or []]
    return found


def _at(p: Any) -> list[float]:
    return [round(float(v), 4) for v in p]


def _point(p: Any) -> str:
    return "(" + ", ".join(f"{float(v):g}" for v in p) + ")"


def _box_shape(place: Place) -> Any:
    assert place.low is not None and place.high is not None
    size = place.high - place.low
    return cq.Solid.makeBox(*map(float, size), pnt=cq.Vector(*map(float, place.low)))


def _box_manifold(place: Place) -> Any:
    assert place.low is not None and place.high is not None
    size = tuple(map(float, place.high - place.low))
    return m3.Manifold.cube(size).translate(tuple(map(float, place.low)))


def _caught(volume: float, whole: float) -> str:
    """Where a part's surface is against a box: in it ("caught"), where some of the part's
    material is in it and not all of the box; else "outside" it, or the box all "inside" it."""
    if volume <= 0.0:
        return "outside"
    return "inside" if volume >= whole * (1 - VOLUME_SHARE) else "caught"


def _as_mesh(built: Any) -> trimesh.Trimesh:
    mesh = built.to_mesh()
    return trimesh.Trimesh(
        np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=False
    )


def _nearest(a: trimesh.Trimesh, b: trimesh.Trimesh) -> tuple[float, np.ndarray, np.ndarray, float]:
    """Points on each surface measured to the other: (nearest, from, to, how far apart the points
    are). Sampled points find a gap as large as the true one or larger, by about their spacing."""
    best: tuple[float, np.ndarray, np.ndarray] = (math.inf, np.zeros(3), np.zeros(3))
    spacing = 0.0
    for one, other in ((a, b), (b, a)):
        sampled, _ = trimesh.sample.sample_surface(one, SAMPLES, seed=7)
        points = np.vstack([sampled, np.asarray(one.vertices)])
        closest, distance, _ = trimesh.proximity.closest_point(other, points)  # type: ignore[no-untyped-call]
        k = int(np.argmin(distance))
        if float(distance[k]) < best[0]:
            ends = (points[k], closest[k]) if one is a else (closest[k], points[k])
            best = (float(distance[k]), *ends)
        spacing = max(spacing, float(np.sqrt(one.area / SAMPLES)))
    return best[0], best[1], best[2], spacing


def _facing_faces(part: Part, toward: np.ndarray) -> list[Any]:
    """The part's faces whose outward normal stays within `FACING_DEG` of `toward`."""
    from .edges import Face  # the exact surface's normals, as `edges` reads them

    least = math.cos(math.radians(FACING_DEG))
    chosen = []
    for face in part.shape.Faces():
        read = Face(face.wrapped)
        a = read.adaptor
        us = np.linspace(a.FirstUParameter(), a.LastUParameter(), FACING_GRID)
        vs = np.linspace(a.FirstVParameter(), a.LastVParameter(), FACING_GRID)
        grid = [(u, v) for u in us for v in vs if read.contains(u, v)]
        if not grid:
            grid = [(float(np.mean(us)), float(np.mean(vs)))]
        normals = [read.props(u, v)[1] for u, v in grid]
        if all(float(n @ toward) >= least for n in normals if np.any(n)):
            chosen.append(face)
    return chosen


def _gap_of(a: Any, b: Any) -> tuple[float, np.ndarray, np.ndarray] | None:
    """OCCT's exact least distance between two shapes, and the points it's between."""
    distance = BRepExtrema_DistShapeShape(a.wrapped, b.wrapped)
    if not distance.IsDone() or distance.NbSolution() < 1:
        return None
    ends = [distance.PointOnShape1(1), distance.PointOnShape2(1)]
    one, two = (np.array([p.X(), p.Y(), p.Z()]) for p in ends)
    return float(distance.Value()), one, two


def measure_place(
    a: Part,
    b: Part,
    place: Place,
    band: float,
    min_gap: float | None,
    max_gap: float | None,
) -> dict[str, Any]:
    """The least gap between a pair's surfaces at one place, by two methods, and its verdict
    against the gap range. A place reads the parts' own surfaces where they lie, never a section
    or a copy: inside a box, the least distance between the parts' material in it, which is
    between their surfaces in it, as a box is convex; facing a way, between the faces that face
    each other along it."""
    found: dict[str, Any] = {
        "gap_mm": None,
        "gap_by": None,
        "gap_second_mm": None,
        "from": None,
        "to": None,
        "verdict": NOT_CAUGHT,
        "why": "",
    }
    differs: list[str] = []
    gap_band = 2 * FINE[0] + TOUCHING
    overlap = 0.0
    if place.way is not None:
        toward = np.asarray(DIRECTIONS[place.way])
        sides = [
            (_facing_faces(one, toward), _facing_faces(other, -toward))
            for one, other in ((a, b), (b, a))
        ]
        sides = [(f, g) for f, g in sides if f and g]
        if not sides:
            found["why"] = (
                f"no face of {a.name} or {b.name} faces {place.way} with a face of the other "
                "facing back. Name a way one faces the other across the gap, or use --box"
            )
            return found
        exact: tuple[float, np.ndarray, np.ndarray] | None = None
        second: tuple[float, np.ndarray, np.ndarray, float] | None = None
        for one, other in sides:
            here = _gap_of(cq.Compound.makeCompound(one), cq.Compound.makeCompound(other))
            if here is None:
                differs.append("OCCT's distance didn't finish")
                continue
            if exact is None or here[0] < exact[0]:
                exact = here
            meshed = (
                _tessellated(cq.Compound.makeCompound(one), FINE),
                _tessellated(cq.Compound.makeCompound(other), FINE),
            )
            sampled = _nearest(*meshed)
            if second is None or sampled[0] < second[0]:
                second = sampled
        if exact is None or second is None:
            found.update(verdict="DISAGREE", why="; ".join(differs))
            return found
        read, start, end = exact
        other_read, spacing = second[0], second[3]
        found["gap_by"] = "occt"
        if other_read < read - gap_band or other_read > read + 2 * spacing + gap_band:
            differs.append(
                f"points sampled on those faces say {other_read:.4f} mm, OCCT {read:.4f}"
            )
    else:
        box = _box_shape(place)
        whole = float(box.Volume())
        clipped = [one.manifold() ^ _box_manifold(place) for one in (a, b)]
        by_mesh = [_caught(float(c.volume()), whole) for c in clipped]
        if a.is_brep and b.is_brep:
            try:
                cut = [one.shape.intersect(box) for one in (a, b)]
                by_occt = [_caught(float(c.Volume()), whole) for c in cut]
            except Exception as exc:  # noqa: BLE001 - OCCT's failure is a finding
                found.update(verdict="DISAGREE", why=f"OCCT couldn't cut the box: {exc}"[:200])
                return found
            if by_occt != by_mesh:
                found.update(
                    verdict="DISAGREE",
                    why=f"OCCT and the mesh differ on which of the pair the box catches: "
                    f"{by_occt} against {by_mesh}",
                )
                return found
        where = {"outside": "lies all outside the box", "inside": "fills the box with material"}
        missed = [
            f"{one.name} {where[hit]}"
            for one, hit in zip((a, b), by_mesh, strict=True)
            if hit != "caught"
        ]
        if missed:
            found["why"] = (
                f"no surface of both is in the box: {' and '.join(missed)}. Move the box so it "
                "holds the faces on both sides of the gap you're reading"
            )
            return found
        overlap = float((clipped[0] ^ clipped[1]).volume())
        search = float(np.linalg.norm(place.high - place.low)) + 1.0  # type: ignore[operator]
        by_manifold = float(clipped[0].min_gap(clipped[1], search))
        if a.is_brep and b.is_brep:
            exact_box = _gap_of(cut[0], cut[1])
            if exact_box is None:
                found.update(verdict="DISAGREE", why="OCCT's distance didn't finish")
                return found
            read, start, end = exact_box
            other_read = by_manifold
            found["gap_by"] = "occt"
            if abs(read - other_read) > gap_band:
                differs.append(f"OCCT's gap is {read:.4f} mm, the mesh's {other_read:.4f}")
        else:
            read = by_manifold
            other_read, start, end, spacing = _nearest(*(_as_mesh(c) for c in clipped))
            found["gap_by"] = "mesh"
            if other_read < read - gap_band or other_read > read + 2 * spacing + gap_band:
                differs.append(f"sampled points say {other_read:.4f} mm, the mesh {read:.4f}")
    # The figure the limits judge, as it is: a need on it reads the same (`limits`).
    found.update(
        gap_mm=read,
        gap_second_mm=round(other_read, 4),
        **{"from": _at(start), "to": _at(end)},
    )
    if differs:
        found.update(verdict="DISAGREE", why="; ".join(differs))
    elif overlap > band:
        found.update(verdict="INTERFERE", why=f"{overlap:.3f} mm³ of overlap in the box")
    elif min_gap is not None and under(read, min_gap):
        found["verdict"] = "TOO CLOSE"
    elif max_gap is not None and over(read, max_gap):
        found["verdict"] = "TOO LOOSE"
    elif read <= TOUCHING:
        found["verdict"] = "TOUCHING"
    else:
        found["verdict"] = "CLEAR"
    return found


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
    named = places(args)
    # With --slide a box names where a contact lies, and reads no gap (mqucifer/print-shop#76).
    way_out: str | None = getattr(args, "slide", None)
    boxes, named = (named, []) if way_out else ([], named)
    if any(place.way for place in named) and not all(one.is_brep for one in items):
        raise NoAnswer(
            "--facing reads the faces of a STEP file, and an STL has none of its own: read an "
            "STL's gap at a place with --box"
        )
    # With a place named, the range holds each place's gap, not the pair's least: a range is the
    # play at one place, and a mated pair has several (mqucifer/print-shop#72).
    low, high = (None, None) if named else (args.min_gap, max_gap)
    results = [measure_pair(a, b, low, high) for a, b in pairs]
    for (a, b), r in zip(pairs, results, strict=True):
        if named:
            band = r["overlap_band_mm3"]
            r["places"] = {
                p.name: measure_place(a, b, p, band, args.min_gap, max_gap) for p in named
            }
    report.numbers["pairs"] = results
    if named:
        report.numbers["places"] = {p.name: p.plain() for p in named}
    slid = None
    if way_out:
        others = [piece for part in parts[:-1] for piece in part.pieces()]
        slid = what_holds(report, parts[-1], others, way_out, args.hold, boxes)
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
        for name, place in r.get("places", {}).items():
            _place_said(report, r, name, place, args)
    if args.min_gap is not None or max_gap is not None:
        exact = "OCCT's exact distance" if brep else "the mesh's distance"
        held = "each place's least gap" if named else "each pair's least gap"
        report.line(
            f"gap range: {_range(args.min_gap, max_gap)}, holding {held}, by {exact} as it is: a "
            f"gap at a limit, to {ARITHMETIC:g} mm, passes"
            + (
                ". The pairs' own least gaps are read and not held to it: with places named, "
                "the range is the play at those places"
                if named
                else ""
            )
        )
    if named:
        _places_checked(report, results, named)
    if lift is not None:
        _lift_said(report, results, report.numbers["lift"])
    elif not named and (args.min_gap is not None or max_gap is not None):
        seated = [r for r in results if r["verdict"] == "INTERFERE" or r["gap_mm"] <= TOUCHING]
        if seated:
            most = max(g for g in (args.min_gap, max_gap) if g is not None)
            report.note(
                f"{len(seated)} pair(s) touch or overlap, so their least gap is 0 and the gap "
                "range can't read them: a part resting on its seat touches it. To read the play "
                "round a lid's lip or a part in its recess, run this again with --lift D: it "
                "moves the last file named D mm along the way it closes (--along, z unless said) "
                f"before measuring, D more than {most:g} mm, so the least gap is the play, and the "
                "need stays on the printed file. Or name a place round the faces whose play it "
                "is, with --box, and read its gap with the parts seated"
            )
    verdicts = [r["verdict"] for r in results]
    read_at = [p for r in results for p in r.get("places", {}).values()]
    verdicts += [p["verdict"] for p in read_at if p["verdict"] != NOT_CAUGHT]
    worst = min(verdicts, key=ORDER.index)
    counts = {w: sum(r["verdict"] == w for r in results) for w in ORDER}
    tally = ", ".join(f"{n} {w.lower()}" for w, n in counts.items() if n)
    if read_at:
        at = {w: sum(p["verdict"] == w for p in read_at) for w in [*ORDER, NOT_CAUGHT]}
        tally += "; at the places: " + ", ".join(f"{n} {w.lower()}" for w, n in at.items() if n)
    if slid is not None:
        # The slide's verdict is the answer's, unless a reading has no answer to rely on, or two
        # parts that don't move overlap: the hold's figures stand beside the pairs'.
        worst = min([*verdicts, slid["verdict"]], key=SLIDE_ORDER.index)
        report.verdict(worst, f"{summary(slid)}; {len(results)} pair(s): {tally}")
        return
    report.verdict(worst, f"{len(results)} pair(s): {tally}")


def _place_said(
    report: Report, pair: dict[str, Any], name: str, place: dict[str, Any], args: Any
) -> None:
    """One place's line, for one pair: its gap, where, and by which method, or why it has none."""
    head = f"  at {name}, {pair['a']} | {pair['b']}:"
    if place["gap_mm"] is None:
        report.line(f"{head} {place['verdict']}, no figure: {place['why']}")
        return
    limit = next((g for g in (args.min_gap, args.max_gap) if g is not None), place["gap_mm"])
    by = "OCCT's exact distance" if place["gap_by"] == "occt" else "the mesh"
    line = (
        f"{head} {place['verdict']}, gap {said(place['gap_mm'], limit, 4)} mm by {by}, from "
        f"{_point(place['from'])} to {_point(place['to'])}, as pairs.N.places.{name}.gap_mm"
    )
    if place["why"]:
        line += f": {place['why']}"
    report.line(line)


def _places_checked(report: Report, results: list[dict[str, Any]], named: list[Place]) -> None:
    """What each place is, its second check, and a place no pair has surfaces in."""
    empty: str | None = None
    for place in named:
        report.line(f"place {place.name}: {place.words()}")
        read = [r["places"][place.name] for r in results]
        seconds = [p for p in read if p["gap_second_mm"] is not None]
        if seconds:
            way = "points sampled on those faces" if place.way else "the mesh clipped by the box"
            if not place.way and not all(p["gap_by"] == "occt" for p in seconds):
                way = "points sampled on the mesh clipped by the box"
            report.second(
                f"{place.name}: {way} read "
                + ", ".join(f"{p['gap_second_mm']:.4f}" for p in seconds)
                + " mm, against "
                + ", ".join(f"{p['gap_mm']:.4f}" for p in seconds)
            )
        if all(p["verdict"] == NOT_CAUGHT for p in read) and empty is None:
            empty = (
                f"the place {place.name}, {place.words()}, holds no pair's surfaces on both sides "
                f"of a gap, so it reads nothing: {read[0]['why']}"
            )
    if empty is not None:
        raise NoAnswer(empty)


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
