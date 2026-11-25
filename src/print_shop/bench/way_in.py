"""A dovetail's way in, read before `load` solves anything (mqucifer/print-shop#184 and
mqucifer/print-shop#185), with FreeCAD.

`load` read a dovetail by the way the assembly file said its tail slides in, and its latch as
holding from the catch's faces. Neither said whether the parts could be put together. On
eval-table-1 a leg's split read `OK` in a way its parts couldn't take, as the socket's mouth was
narrower than the tail's tip, and its latch, a solid stub, read as holding the lifted leg though
nothing could bend it past the socket's wall. So before anything is solved, each dovetail is
taken apart the way it went together:

- **The way out.** The tail's part is swept out of its socket against `slides`, from where it
  sits to where its box is past the socket's part's: each of its faces is drawn out along the
  way into a prism, and each prism is cut with the socket's part (OCCT's `common`). What a face
  passes through on its way out is what it has to pass on its way in. Faces that only run along
  the way draw nothing out. A piece of what the latch's tab passes, or of the tab itself where
  it's on the socket's part, is the latch's; anything else blocks the way, and the joint fails:
  the parts can't be put together that way. The same sweep along each of the six ways says
  which way they do come apart, if any.
- **The latch going on.** What the latch's tab passes, seen along the way, is how far it has
  to move to let the tail by: its least width across, as `fit-on` reads a place, and the way
  that's across. `bend` reads the tab, in the box and at the root the file gives it, pushed
  that far that way, away from what it passes. A tab `bend` reads over its material's limit
  across its layers, or that `bend` can't read pushed that way at all, as a stub whose push
  runs along it, can't be fitted: the joint fails, and the latch is credited with nothing.

**Second method:** rays along the way out, through a mesh of each part, as `fit-on`'s second
method reads a way on: one set of rays reads every step, each step `STEP` mm, and an overlap two
rays or more across outside the latch's box seen along the way blocks it. The sweep and the rays
disagreeing on whether the way out is blocked is `DISAGREE`; so is the tab's depth, by the sweep
and by rays `fit_on.FINE` apart over what it passes, more than twice their spacing and
`fit_on.DEPTH` apart. The tab's strain has `bend`'s own second method, its beam.

**What it leaves out:** a way in that isn't one straight line; friction, and how hard the tail
has to be pushed; and play: the parts are read where the file puts them, home.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from . import bend, fit_on, load_dovetail
from .assembly import AXES, Assembly, Joint, Latch
from .report import NoAnswer, Report

#: The six ways a part can be slid, as the assembly file names them.
WAYS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
#: A piece of overlap under this, in mm³, isn't one: two faces that touch share none.
VOLUME = fit_on.VOLUME
#: Past where the boxes are apart, the sweep goes this much further, in mm.
PAST = 1.0
#: The rays' readings along the way out, in mm apart.
STEP = 0.25
#: A direction is along an axis within this.
SQUARE = 1e-6


def _opposite(way: str) -> str:
    return ("-" if way[0] == "+" else "+") + way[1]


def _xyz(point: Any) -> str:
    return (
        "(" + ", ".join(f"{c + 0.0:g}" for c in np.round(np.asarray(point, dtype=float), 3)) + ")"
    )


def _box(shape: Any) -> tuple[np.ndarray, np.ndarray]:
    box = shape.BoundBox
    return np.array([box.XMin, box.YMin, box.ZMin]), np.array([box.XMax, box.YMax, box.ZMax])


def travel(moving: Any, other: Any, way: str) -> float:
    """How far `moving` slides along `way` before its box is past `other`'s, and `PAST` more."""
    axis, sign = AXES.index(way[1]), 1 if way[0] == "+" else -1
    (mine_low, mine_high), (low, high) = _box(moving), _box(other)
    apart = high[axis] - mine_low[axis] if sign > 0 else mine_high[axis] - low[axis]
    return max(0.0, float(apart)) + PAST


def sweep(
    moving: Any, other: Any, way: str, latch: Latch | None, latch_moves: bool
) -> list[dict[str, Any]]:
    """What `moving` passes through in `other` as it slides out along `way` until it's clear:
    each of its faces drawn out along the way, cut with `other`. Each piece has its box, its
    volume, the points of its outline and whether it's the latch's: drawn out from what of a
    face is inside the latch's box where the tab moves, or what of `other` is inside that box
    where the tab stays put."""
    import FreeCAD as App
    import Part

    vector = np.asarray(WAYS[way]) * travel(moving, other, way)
    room = None
    if latch is not None:
        low, high = (np.asarray(c, dtype=float) for c in latch.arm)
        room = Part.makeBox(*(high - low).tolist(), App.Vector(*low.tolist()))
        # Where its part's `turn` and `at` put it, given in its part's own axes.
        room = load_dovetail.placed(room, latch)
    # What's the latch's on each side: the tab's faces where it moves, each face cut at the
    # latch's box, as a face of the tab can run on into the part it's on; and where the tab
    # stays put, the other part cut there the same way.
    faces: list[tuple[Any, bool]] = []
    for face in moving.Faces:
        if type(face.Surface).__name__ == "Plane" and (
            abs(float(np.array(face.normalAt(0, 0)) @ np.asarray(WAYS[way]))) < SQUARE
        ):
            continue
        if room is None or not latch_moves or not face.BoundBox.intersect(room.BoundBox):
            faces.append((face, False))
            continue
        faces += [(f, True) for f in face.common(room).Faces if f.Area > 1e-9]
        faces += [(f, False) for f in face.cut(room).Faces if f.Area > 1e-9]
    others = [(other, False)]
    if room is not None and not latch_moves:
        others = [(other.common(room), True), (other.cut(room), False)]
    found: dict[tuple[float, ...], dict[str, Any]] = {}
    for face, tab_face in faces:
        try:
            drawn = face.extrude(App.Vector(*vector))
        except Exception as wrong:  # OCCT's own failure, said as what it is
            raise NoAnswer(
                f"a face of the tail's part, round {_xyz(face.CenterOfMass)}, couldn't be drawn "
                f"out along {way}: {str(wrong)[:200]}"
            ) from wrong
        for shape, tab_part in others:
            if not shape.Solids or not drawn.BoundBox.intersect(shape.BoundBox):
                continue
            for solid in drawn.common(shape).Solids:
                if solid.Volume < VOLUME:
                    continue
                low, high = _box(solid)
                mine = tab_face if latch_moves else tab_part
                key = tuple(np.round([*low, *high, solid.Volume], 4).tolist())
                if key in found:
                    found[key]["latch"] = found[key]["latch"] and mine
                    continue
                points = np.array([[p.x, p.y, p.z] for p in solid.tessellate(0.01)[0]])
                found[key] = {
                    "box": [low.tolist(), high.tolist()],
                    "volume_mm3": float(solid.Volume),
                    "latch": mine,
                    "points": points,
                }
    return sorted(found.values(), key=lambda p: -p["volume_mm3"])


class _Way:
    """The tail's part sliding out along a way, as `fit_on.Rays` reads one: from clear down to
    where it sits, every `STEP`."""

    def __init__(self, moving: Any, other: Any, names: tuple[str, str], way: str) -> None:
        self.moving, self.onto = moving, [other]
        self.name, self.names = names[0], [names[1]]
        self.axis = AXES.index(way[1])
        self.sign = 1 if way[0] == "+" else -1
        self.across = tuple(a for a in range(3) if a != self.axis)
        self.offsets = fit_on.offsets(travel(moving, other, way), STEP)


def _by_rays(
    moving: Any, other: Any, names: tuple[str, str], way: str, latch: Latch | None
) -> dict[str, Any]:
    """The second method: rays along the way out at every step, and where they find an overlap
    two rays or more across that isn't the latch's, seen along the way."""
    slid = _Way(moving, other, names, way)
    rays = fit_on.Rays(slid)  # type: ignore[arg-type]
    across = list(slid.across)
    pad = rays.whole.spacing if rays.whole else 0.0
    blocked: list[dict[str, Any]] = []
    for met in rays.every_step():
        at = met["at"]
        # Outside the latch's box, seen along the way, on either axis across it.
        lone = np.ones(len(at), dtype=bool)
        if latch is not None and not latch.placed:
            inside = np.ones(len(at), dtype=bool)
            for column, axis in enumerate(across):
                inside &= (at[:, column] >= latch.arm[0][axis] - pad) & (
                    at[:, column] <= latch.arm[1][axis] + pad
                )
            lone = ~inside
        elif latch is not None:
            lone = ~_seen_in(latch, slid.axis, across, at, pad)
        if lone.any():
            where = at[lone]
            blocked.append(
                {"out_mm": met["out_mm"], "rays": int(lone.sum()),
                 "from": where.min(axis=0).tolist(), "to": where.max(axis=0).tolist()}
            )  # fmt: skip
    return {
        "rays": rays,
        "way": slid,
        "spacing_mm": rays.whole.spacing if rays.whole else None,
        "count": rays.whole.cells if rays.whole else 0,
        "readings": len(slid.offsets),
        "blocked": blocked,
    }


def _seen_in(latch: Latch, axis: int, across: list[int], at: np.ndarray, pad: float) -> Any:
    """Which points, each across `axis`, are inside a placed latch's box seen along that axis,
    within `pad` (mqucifer/print-shop#295): inside every side of the box's outline seen that
    way, a convex outline, each side moved out by `pad`."""
    outline = fit_on.hull(latch.corners()[:, across])
    inside = np.ones(len(at), dtype=bool)
    turning = 1.0 if _area(outline) > 0 else -1.0
    for k in range(len(outline)):
        side = outline[(k + 1) % len(outline)] - outline[k]
        length = float(np.linalg.norm(side))
        if length < 1e-9:
            continue
        # Out of the outline, square to this side.
        out = turning * np.array([side[1], -side[0]]) / length
        inside &= (at - outline[k]) @ out <= pad
    return inside


def _area(outline: np.ndarray) -> float:
    """An outline's area, more than 0 where it runs anticlockwise."""
    x, y = outline[:, 0], outline[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) / 2


def _rays_depth(rays: Any, slid: _Way, box: list[list[float]]) -> tuple[float | None, float]:
    """What the tab passes again, by rays `fit_on.FINE` apart over its box: the rays that run
    inside both parts at any step, and their least width, with a ray's spacing for their own."""
    across = list(slid.across)
    low = np.array(box[0])[across] - 10 * fit_on.FINE
    high = np.array(box[1])[across] + 10 * fit_on.FINE
    grid = fit_on.grid_over(slid.axis, low, high, fit_on.FINE, fit_on.PLACE)
    mine = rays._spans(rays.moving, grid)
    theirs = rays._spans(rays.onto[0], grid)
    both = np.flatnonzero(~np.isnan(mine[0][:, 0]) & ~np.isnan(theirs[0][:, 0]))
    if not len(both):
        return None, grid.spacing
    hit = np.zeros(len(both), dtype=bool)
    for out in slid.offsets:
        hit |= (
            fit_on.shared(
                (mine[0][both], mine[1][both]), (theirs[0][both], theirs[1][both]),
                slid.sign * out,
            )
            > 0
        )  # fmt: skip
    cells = both[hit]
    if not len(cells):
        return None, grid.spacing
    return fit_on.least_width(grid.centres(cells))[0] + grid.spacing, grid.spacing


def _layers(part: Any, latch: Latch | None = None) -> str | None:
    """The axis a part's layers are stacked along, in the assembly, or in a latch's own axes
    where it's given, where it's one."""
    way = part.layers_way() if latch is None else latch.way(part.layers_way())
    for axis, name in enumerate(AXES):
        if abs(abs(way[axis]) - 1) < SQUARE:
            return name
    return None


def _bend(
    assembly: Assembly, joint: Joint, solid: Any, push: float, toward: str
) -> dict[str, Any]:
    """`bend` on the latch's tab, as anyone could call it: the tab's part where the assembly
    puts it, the latch's box and root, pushed `push` mm `toward` a way. Its verdict and why,
    its strain across the layers against the limit, its second check, and its words where it
    has no answer. A latch given in its part's own axes is read there, on the part as its own
    file has it, with `toward` in those axes (mqucifer/print-shop#295)."""
    assert joint.latch is not None
    part = assembly.part(joint.latch.on)
    layers = _layers(part, joint.latch)
    root = joint.latch.root
    solid = load_dovetail.in_own_axes(solid, joint.latch)
    command = (
        f"bend {part.file if joint.latch.own else part.name + '.step'} --arm "
        + ",".join(f"{c:g}" for c in (*joint.latch.arm[0], *joint.latch.arm[1]))
        + f" --root {root[0]}={root[1]:g} --push {push:.3f} --toward {toward}"
        + (f" --layers {layers}" if layers else "")
    )
    said: dict[str, Any] = {"command": command, "push_mm": push, "toward": toward}
    if layers is None:
        return {
            **said, "verdict": "NO ANSWER",
            "why": f"{part.name}'s layers aren't stacked along x, y or z in the assembly, and "
                   "`bend` reads them along one",
        }  # fmt: skip
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / f"{part.name}.step"
        solid.exportStep(str(path))
        args = SimpleNamespace(
            file=str(path),
            arm=(list(joint.latch.arm[0]), list(joint.latch.arm[1])),
            root=root,
            push=push,
            toward=toward,
            at="both",
            layers=layers,
            material=part.material.name,
            limit=None,
            mesh=None,
        )
        report = Report("bend", command.split())
        try:
            bend.run(args, report)
        except NoAnswer as refused:
            return {**said, "verdict": "NO ANSWER", "why": str(refused)}
    return {
        **said,
        "verdict": report.word,
        "why": report.why,
        "across_layers_pct": report.numbers.get("across_layers_pct"),
        "limit_pct": report.numbers.get("limit_pct"),
        "second": list(report.second_check),
    }


def read(assembly: Assembly, joint: Joint, placed: list[Any]) -> dict[str, Any]:
    """A dovetail taken apart the way it went together: whether its tail comes out against
    `slides` past nothing but its latch, by the sweep and by rays; where it doesn't, which ways
    it does; and its latch's tab, how far it has to move on the way in, and `bend`'s reading
    of that. `fails` says why it can't go together, and `apart` where the methods differ."""
    names = [p.name for p in assembly.parts]
    first, second = joint.between
    tail, socket = placed[names.index(first)], placed[names.index(second)]
    assert joint.slides is not None
    out = _opposite(joint.slides)
    latch = joint.latch
    moves = joint.latch is not None and joint.latch.on == first
    pieces = sweep(tail, socket, out, latch, moves)
    blocking = [p for p in pieces if not p["latch"]]
    tab = [p for p in pieces if p["latch"]]
    found: dict[str, Any] = {
        "slides": joint.slides,
        "out": out,
        "blocked": bool(blocking),
        "blocking": [{k: p[k] for k in ("box", "volume_mm3")} for p in blocking],
        "fails": [],
        "apart": [],
        "latched": joint.latch is not None,
    }
    what = f"the dovetail joint {joint.name}"
    by_rays = _by_rays(tail, socket, (first, second), out, latch)
    found["rays"] = {k: by_rays[k] for k in ("spacing_mm", "count", "readings", "blocked")}
    if bool(blocking) != bool(by_rays["blocked"]):
        found["apart"].append(
            f"{what}'s tail, slid out of its socket {out}, "
            + (
                "passes through the socket's part by OCCT's sweep, and no ray finds that"
                if blocking
                else "passes nothing but its latch by OCCT's sweep, and "
                f"{by_rays['blocked'][0]['rays']} rays find it inside {second} "
                f"{by_rays['blocked'][0]['out_mm']:g} mm out"
            )
        )
    if blocking:
        clear = []
        for other_way in WAYS:
            if other_way == out:
                continue
            if not any(not p["latch"] for p in sweep(tail, socket, other_way, latch, moves)):
                clear.append(other_way)
        found["comes_out"] = clear
        found["goes_in"] = [_opposite(w) for w in clear]
        worst = blocking[0]
        found["fails"].append(
            f"{what} can't be put together the way the file says its tail slides in, "
            f"{joint.slides}: slid out of its socket {out}, the tail's part, {first}, passes "
            f"through {second}" + (" other than at its latch" if latch is not None else "")
            + f", {worst['volume_mm3']:.1f} mm³ from "
            f"{_xyz(worst['box'][0])} to {_xyz(worst['box'][1])}"
            + (f", and {len(blocking) - 1} more piece(s)" if len(blocking) > 1 else "")
            + ". "
            + (
                "Of the six ways, the parts come apart past nothing"
                + (" but the latch" if latch is not None else "")
                + " only "
                f"{' and '.join(clear)}, so the tail goes in {' or '.join(found['goes_in'])}: "
                "give that as `slides`, or change the parts so the tail goes in the way named"
                if clear
                else "In none of the six ways do the parts come apart past nothing"
                + (" but the latch" if latch is not None else "")
                + ": the tail can't be put into its socket in a straight line"
            )
        )
        return found
    if joint.latch is None:
        return found
    latch_said = f"{what}'s latch, a tab on {joint.latch.on}"
    if not tab:
        found["latch"] = {"passes": False, "depth_mm": 0.0}
        return found
    axis = AXES.index(out[1])
    across_axes = [a for a in range(3) if a != axis]
    best = None
    for piece in tab:
        depth, square, along = fit_on.least_width(piece["points"][:, across_axes])
        if best is None or depth > best[0]:
            best = (depth, square, along, piece)
    assert best is not None
    depth, square, along, piece = best
    way = np.zeros(3)
    way[across_axes] = square
    centre = (np.array(piece["box"][0]) + np.array(piece["box"][1])) / 2
    middle = joint.latch.corners().mean(axis=0)
    if float((middle - centre) @ way) < 0:
        way = -way
    again, spacing = _rays_depth(by_rays["rays"], by_rays["way"], piece["box"])
    found["latch"] = {
        "passes": True,
        "depth_mm": depth,
        "across": [round(float(c), 6) + 0.0 for c in way],
        "along_mm": along,
        "box": piece["box"],
        "depth_rays_mm": again,
        "rays_mm": spacing,
    }
    if again is None or abs(again - depth) > 2 * spacing + fit_on.DEPTH:
        found["apart"].append(
            f"{latch_said}, passes {depth:.3f} mm deep by OCCT's sweep on its way in, and "
            + (
                "no ray finds it"
                if again is None
                else f"{again:.3f} by rays {spacing:.3g} mm apart"
            )
        )
        return found
    # `bend` reads the tab in the latch's own axes, which are its part's own file's where
    # the latch is given in them (mqucifer/print-shop#295).
    own = joint.latch.way(way)
    named = [n for a, n in enumerate(AXES) if abs(abs(own[a]) - 1) < SQUARE]
    if not named:
        found["latch"]["bend"] = None
        found["fails"].append(
            f"{latch_said}, has to move {depth:.3f} mm across {_xyz(way)} to let the tail in, "
            f"which isn't along x, y or z {joint.latch.where()}, so `bend` can't read it: the "
            "latch isn't shown to go together, and is credited with nothing"
        )
        return found
    toward = ("+" if own[AXES.index(named[0])] > 0 else "-") + named[0]
    tab_part = placed[names.index(joint.latch.on)]
    read_bend = _bend(assembly, joint, tab_part, depth, toward)
    found["latch"]["bend"] = read_bend
    words = (
        f"{latch_said}, has to move {depth:.3f} mm {toward}"
        + (f" {joint.latch.where()}" if joint.latch.own else "")
        + " to let the tail in, where it "
        + (f"passes {second}" if moves else f"is passed by {first}")
        + f" from {_xyz(piece['box'][0])} to {_xyz(piece['box'][1])}"
    )
    if read_bend["verdict"] == "OK":
        return found
    if read_bend["verdict"] == "DISAGREE":
        found["apart"].append(f"{words}, and `bend` has no one answer: {read_bend['why']}")
    elif read_bend["verdict"] == "FAIL":
        found["fails"].append(
            f"{words}, and pushed that far it can't go together: {read_bend['why']}. The latch "
            "is credited with nothing"
        )
    else:
        found["fails"].append(
            f"{words}, and the tab as the file names it can't bend that way: {read_bend['why']}. "
            "The latch is credited with nothing"
        )
    return found


def said(found: dict[str, Any], name: str) -> list[str]:
    """A way in's reading as lines of the reply."""
    lines = [
        f"the dovetail joint {name}'s way in: its tail slides {found['slides']}, so it's slid "
        f"out {found['out']} until it's clear, each face drawn out that way and cut with the "
        "socket's part: "
        + (
            "it passes through it"
            if found["blocked"]
            else "it passes nothing"
            + (" but its latch" if found.get("latch", {}).get("passes") else "")
        )
        + f", as ways_in.{name}.blocked"
    ]
    if "comes_out" in found:
        lines.append(
            "  of the six ways, it comes out past nothing"
            + (" but its latch" if found["latched"] else "")
            + ": "
            + (", ".join(found["comes_out"]) or "none")
            + f", as ways_in.{name}.comes_out"
        )
    latch = found.get("latch")
    if latch and latch.get("passes"):
        lines.append(
            f"  its latch's tab passes {latch['depth_mm']:.3f} mm deep across "
            f"{fit_on._way(latch['across'])} and {latch['along_mm']:.3f} mm along, from "
            f"{_xyz(latch['box'][0])} to {_xyz(latch['box'][1])}, as ways_in.{name}.latch"
        )
        read_bend = latch.get("bend")
        if read_bend:
            lines.append(
                f"  `{read_bend['command']}`: {read_bend['verdict']}: {read_bend['why']}"
            )
    elif latch is not None:
        lines.append("  its latch's tab passes nothing on the way in, so nothing has to bend")
    return lines


def second(found: dict[str, Any], name: str) -> list[str]:
    """A way in's second checks as lines of the reply."""
    rays = found["rays"]
    lines = [
        f"the dovetail joint {name}'s way out, {found['out']}, by {rays['count']} rays "
        + (f"{rays['spacing_mm']:.3g} mm apart" if rays["spacing_mm"] else "")
        + f" at each of {rays['readings']} readings {STEP:g} mm apart: "
        + (
            f"an overlap outside the latch's box at {len(rays['blocked'])} of them"
            if rays["blocked"]
            else "no overlap outside the latch's box"
        )
    ]
    latch = found.get("latch")
    if latch and latch.get("passes"):
        lines.append(
            f"the dovetail joint {name}'s latch's tab, what it passes again by rays "
            f"{latch['rays_mm']:.3g} mm apart: "
            + ("none" if latch["depth_rays_mm"] is None else f"{latch['depth_rays_mm']:.3f} mm")
            + f", where the sweep reads {latch['depth_mm']:.3f}"
        )
        read_bend = latch.get("bend")
        if read_bend:
            lines += [
                f"the dovetail joint {name}'s latch's `bend`: {s}"
                for s in read_bend.get("second", [])
            ]
    return lines
