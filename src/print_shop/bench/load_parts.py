"""`load` in two passes (mqucifer/print-shop#301), with FreeCAD.

A table of 24 parts and 56 bolts doesn't fit the container whole at the mesh its parts are read
at: gmsh sizes each bolt's bore by its curvature, 12 elements a turn, and the bores make most of
the mesh. So where the whole meets the container's memory, or where `--parts` asks, it's read in
two passes, inside the container's limits as they are.

**The first pass: the whole.** Every part, joint and bolt, solved together as the one pass
solves them, each joint's faces tied all over, with each bolt's bore stood in by a square one
(`squared`): the same axis and length, its sides the bore's width and a tenth of a mm more, so
it holds the round one and is no stiffer. A square bore's flat sides are meshed at `--mesh`,
not by their curvature. No bolt is in the bore in either pass: the one pass ties the faces and
leaves the bore empty, and a connector stiffer than what it stands for would move the loaded
places less. It gives each joint's force and moment, which the bolts' readings and the joints'
own readings take as the one pass's, each loaded place's movement, and what each part's
neighbours put on it, over each face it meets them by (`_Model.tractions`). It's solved at two
meshes, and each joint's force on the coarser one is to agree with the finer within `AGREE`.

**The second pass: each part on its own** (`_Alone`), at the one pass's mesh, its bores meshed
round by their curvature. Its loads are its own: the places `--at` names on it, or its own
weight when lifted. What its neighbours put on it is put on the faces it meets them by: on each
triangle, the push per mm² the first pass found on the nearest of its own there, its total then
made the first pass's exactly (`even_out`). It's held by what holds it in the assembly; a part
held by nothing but its joints is pinned at three nodes against moving as a body (`pins`), and
nothing else. CalculiX's reaction at what holds it is to equal what's put on it, within
`load.REACTION`. It gives each part's worst pull across its layers and its largest stretch,
against its material's limits, at that mesh and on the coarser one.

**Memory.** Where CalculiX's direct solver meets the container's memory in either pass, the
same mesh is solved by its iterative solver and corrected (`load._Model._iterated`), and the
reply says so. Every other solve is the direct solver's, as in one pass.

**Time.** The container's limit is read from `BENCH_TIMEOUT`, which the host sets. Two passes
stop before a solve that wouldn't end inside it, and say which pass met it and what's read.
"""

from __future__ import annotations

import dataclasses
import math
import os
import signal
import time
from typing import Any

import numpy as np

from . import load
from .assembly import AXES, DIAMETER, Assembly, Held, PartAt
from .report import NoAnswer

#: A square bore's half width over the round one's radius, in mm: so it holds the round one
#: with room to spare, and no face of the round one is left.
SQUARE = 0.1
#: A cylinder is a bolt's bore where its axis passes this close to the bolt's point, in mm,
#: it's a hole, and it's no wider across than this many times the bolt's thread: an M5's 5.5 mm
#: clearance bore and an insert's hole are, a washer's counterbore isn't.
ON_AXIS = 1e-3
BORE = 1.5
#: How far toward its axis a round face is probed to tell a hole from a boss, in mm.
INWARD = 0.05
#: Each joint's force on the coarser of the first pass's meshes agrees with the finer within
#: this share of the larger, or of `load.NOTHING` of the load where that's more: CalculiX's own
#: spread from run to run is 2e-4 of a figure (mqucifer/print-shop#302), 250 times less.
AGREE = 0.05
#: A part's strain read in the second pass is held this far below its limit, or it's either
#: side of it: on the small bolted stool (`cae_cases/make_fixtures.py`) two passes read each
#: part's worst strain within 2% of one pass, some above and some below, where one pass's own
#: two meshes are 1% to 24% apart on the same parts.
BAND = 0.05
#: How much of what's put on a part from its neighbours `even_out` may move to make it add up
#: to the first pass's, as a share of all of it: it moved 8.4% at the most on the stool, 13.4%
#: on its control and 7.2% on the 24-part table, and past this nothing of the part is read.
EVENED = 0.20
#: Each joint's nearest figure to its limit (`nearest_share`, or a bolted joint's worst bolt
#: over what it holds), read from the first pass's forces, is held this far below it, or it's
#: either side of it: on the stool each joint's force in two passes is within 0.40% of one
#: pass's. This, like `BAND`, rests on the stool alone.
JOINT_BAND = 0.05
#: What's said of the movement in two passes.
FINER_SAID = (
    "pass 1, the whole: how far each place moves is taken on from its two meshes to a finer "
    "one, as a second-order mesh reads a part stiffer the coarser it is: the larger of the "
    "two, and of where they point to (Richardson's, at a mesh's error in proportion to its "
    "size squared); so it's no less than one pass, whose bores' small elements make it finer "
    "round them. loads.*.mesh_moves_mm is the mesh's own"
)
#: The container's own clock, as the host sets it, in s, and how long before it a reply is
#: written.
TIMEOUT = "BENCH_TIMEOUT"
MARGIN = 20.0


# The first pass


def squared(assembly: Assembly, placed: list[Any]) -> tuple[list[Any], int, int]:
    """Each part with each bolt's bore stood in by a square one: every cylindrical face whose
    axis passes through a bolt's point of a joint of the part, that is a hole (the part's
    outside is toward its axis) and no wider across than `BORE` times the bolt's thread, cut
    away by a square prism on the same axis and over the same length, `SQUARE` wider than its
    radius each side, so it holds the round one. The square's sides are set by the axis alone,
    so where a bore passes from one part into the next, the two squares are one. Any other round
    face on a bolt's axis, a boss or a collar round it or a counterbore wider than the bore, is
    left round, as one pass meshes it: cut away it would leave less of the part than there is.
    And how many bores were squared, and how many round faces on bolts' axes were left round."""
    import FreeCAD as App
    import Part

    points: dict[str, list[tuple[np.ndarray, float]]] = {}
    for joint in assembly.joints:
        if joint.kind in ("bolted", "through") and joint.bolt is not None:
            thread = DIAMETER[joint.bolt.thread]
            for name in joint.between:
                points.setdefault(name, []).extend(
                    (np.array(p, dtype=float), thread) for p in joint.at
                )
    found, count, kept = [], 0, 0
    for part, solid in zip(assembly.parts, placed, strict=True):
        prisms = []
        for face in solid.Faces:
            surface = face.Surface
            if type(surface).__name__ != "Cylinder":
                continue
            centre, axis = np.array(surface.Center), np.array(surface.Axis)
            axis = axis / np.linalg.norm(axis)
            threads = [
                thread
                for p, thread in points.get(part.name, [])
                if np.linalg.norm(np.cross(p - centre, axis)) < ON_AXIS
            ]
            if not threads:
                continue
            along = [float((np.array(v.Point) - centre) @ axis) for v in face.Vertexes]
            low, high = min(along), max(along)
            if high - low < load.ON:
                continue
            if not stood_in(surface.Radius, _inward(solid, face, centre, axis), max(threads)):
                kept += 1
                continue
            half = surface.Radius + SQUARE
            across, up = square_axes(axis)
            corner = centre + low * axis - half * across - half * up
            prism = Part.makeBox(2 * half, 2 * half, high - low)
            prism = prism.transformGeometry(
                App.Matrix(
                    across[0], up[0], axis[0], corner[0],
                    across[1], up[1], axis[1], corner[1],
                    across[2], up[2], axis[2], corner[2],
                    0, 0, 0, 1,
                )
            )  # fmt: skip
            prisms.append(prism)
        if not prisms:
            found.append(solid)
            continue
        left = [s for s in solid.cut(Part.makeCompound(prisms)).Solids if s.Volume > 1e-6]
        if len(left) != 1:
            raise NoAnswer(
                f"the part {part.name} with its bolts' bores squared is {len(left)} solids: "
                "a bore is too near an edge to be stood in for"
            )
        found.append(left[0])
        count += len(prisms)
    return found, count, kept


def stood_in(radius: float, inward: bool, thread: float) -> bool:
    """Whether a round face on a bolt's axis is its bore, to be stood in by a square: a hole,
    from the bolt's own width to `BORE` times it across."""
    return inward and thread / 2 - load.ON <= radius <= BORE * thread / 2


def _inward(solid: Any, face: Any, centre: np.ndarray, axis: np.ndarray) -> bool:
    """Whether the part's outside is toward the axis at a round face: a hole, not a boss."""
    import FreeCAD as App

    u0, u1, v0, v1 = face.ParameterRange
    point = np.array(face.valueAt((u0 + u1) / 2, (v0 + v1) / 2))
    on_axis = centre + ((point - centre) @ axis) * axis
    toward = (on_axis - point) / np.linalg.norm(on_axis - point)
    probe = point + INWARD * toward
    return not solid.isInside(App.Vector(*probe), 1e-6, False)


def square_axes(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two ways square to `axis` and to each other, set by the line of the axis alone, so the
    same whichever way along it the axis points."""
    axis = axis / np.linalg.norm(axis)
    if axis[int(np.abs(axis).argmax())] < 0:
        axis = -axis
    other = np.array([1.0, 0, 0]) if abs(axis[0]) < 0.9 else np.array([0, 1.0, 0])
    across = np.cross(axis, other)
    across /= np.linalg.norm(across)
    return across, np.cross(axis, across)


def disagree(
    fine: dict[str, dict[str, Any]], coarse: dict[str, dict[str, Any]], force: float
) -> list[tuple[str, float, float, float]]:
    """The joints whose force on the coarser mesh is further from the finer's than `AGREE` of
    the larger, or of `load.NOTHING` of the load where that's more: each one's name, both
    forces' sizes and how far apart they are, in N."""
    found = []
    for name, one in fine.items():
        first, second = np.asarray(one["force"]), np.asarray(coarse[name]["force"])
        apart = float(np.linalg.norm(first - second))
        allowed = AGREE * max(
            float(np.linalg.norm(first)), float(np.linalg.norm(second)), load.NOTHING * force
        )
        if apart > allowed:
            found.append((name, float(np.linalg.norm(first)), float(np.linalg.norm(second)), apart))
    return found


def near(value: float, limit: float, band: float) -> bool:
    """Whether `value` is under `limit` but within `band` of it, as a share of the limit: read
    `band` low, it would be over."""
    return load._near(value, limit, band)


def joint_share(joint: dict[str, Any]) -> float | None:
    """A joint's nearest figure to its limit, as a share of it, where it has one."""
    if "nearest_share" in joint:
        return float(joint["nearest_share"])
    if joint.get("holds_n"):
        return float(joint["bolt_pull_n"]) / float(joint["holds_n"])
    return None


def finer(fine: float, coarse: float) -> float:
    """How far a place moves on a finer mesh than either of two, `load.COARSER` apart in
    size: the larger, along the way it moves, of the two and of where they point to, at an
    error in proportion to the mesh's size squared."""
    way = 1.0 if fine >= 0 else -1.0
    a, b = way * fine, way * coarse
    return way * max(a, b, a + (a - b) / (load.COARSER**2 - 1))


# The second pass


def nearest(centres: np.ndarray, points: np.ndarray, rows: int = 2048) -> np.ndarray:
    """For each of `points`, which of `centres` is nearest."""
    found = np.empty(len(points), dtype=int)
    for start in range(0, len(points), rows):
        chunk = points[start : start + rows]
        apart = ((chunk[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        found[start : start + rows] = apart.argmin(axis=1)
    return found


def even_out(
    places: np.ndarray,
    weights: np.ndarray,
    pushes: np.ndarray,
    force: np.ndarray,
    moment: np.ndarray,
) -> np.ndarray:
    """`pushes` on the nodes at `places`, each node's share of the faces `weights`, made to
    add up to `force` and to `moment` about the origin exactly: what's missing is spread over
    the same nodes as a rigid body spreads a load, evenly for the force and in proportion to
    each node's distance from their middle for the moment."""
    total = weights.sum()
    middle = (weights[:, None] * places).sum(axis=0) / total
    off = places - middle
    short = force - pushes.sum(axis=0)
    turn = moment - np.cross(places, pushes).sum(axis=0) - np.cross(middle, short)
    spread = (weights[:, None, None] * (
        (off**2).sum(axis=1)[:, None, None] * np.eye(3) - off[:, :, None] * off[:, None, :]
    )).sum(axis=0)  # fmt: skip
    about = np.linalg.lstsq(spread, turn, rcond=None)[0]
    evened: np.ndarray = pushes + weights[:, None] * (short / total + np.cross(about, off))
    return evened


def moved_share(before: np.ndarray, after: np.ndarray) -> float:
    """How much of the pushes `before` were moved to make `after`, summed node by node, as a
    share of them all: where there was nothing to move, any push is all of it moved."""
    given = float(np.linalg.norm(before, axis=1).sum())
    moved = float(np.linalg.norm(after - before, axis=1).sum())
    if given > 0:
        return moved / given
    return 0.0 if moved == 0 else math.inf


def pins(points: np.ndarray) -> list[tuple[int, list[int]]]:
    """Three nodes, as rows of `points`, and the axes each is held along, that stop a body
    moving as a whole and hold nothing else: the first held every way, the furthest from it
    held across the line between them, and the furthest from that line held from turning
    about it."""
    middle = points.mean(axis=0)
    first = int(np.linalg.norm(points - middle, axis=1).argmax())
    second = int(np.linalg.norm(points - points[first], axis=1).argmax())
    line = points[second] - points[first]
    line /= np.linalg.norm(line)
    off = points - points[first]
    out = off - np.outer(off @ line, line)
    third = int(np.linalg.norm(out, axis=1).argmax())
    turned = np.cross(line, out[third])
    along = int(np.abs(line).argmax())
    return [
        (first, [0, 1, 2]),
        (second, [a for a in range(3) if a != along]),
        (third, [int(np.abs(turned).argmax())]),
    ]


class _Alone(load._Model):
    """One part on its own, for the second pass: its loads, and what its neighbours put on it
    in the first pass, on the faces it meets them by."""

    def __init__(
        self,
        assembly: Assembly,
        part: PartAt,
        solid: Any,
        met: list[Any],
        planes: list[Held],
        given: dict[str, np.ndarray] | None,
        places: list[tuple[tuple[float, ...], float]],
        args: Any,
        size: float,
        straight: bool = False,
    ) -> None:
        alone = dataclasses.replace(assembly, parts=(part,), joints=(), held=())
        super().__init__(alone, [solid], args, size, straight, two_pass=True)
        self.part, self.solid, self.met_faces = part, solid, met
        self.planes_given, self.given, self.places = planes, given, places
        self.free = False

    def join(self, doc: Any) -> None:
        self.feature = doc.addObject("Part::Feature", "Parts")
        self.feature.Shape = self.solid
        self.shape = self.feature.Shape
        self.solids = [self.shape]
        self.between = [f for f in self.shape.Faces if any(f.isSame(g) for g in self.met_faces)]
        if len(self.between) != len(self.met_faces):
            raise NoAnswer(
                f"the part {self.part.name} on its own doesn't have the {len(self.met_faces)} "
                "faces it meets its neighbours by"
            )
        self.met, self.joint_faces = {}, {}

    def hold(self) -> None:
        """By the planes that hold it in the first pass, where it has a face in one."""
        self.fixed, self.standing = [], {}
        self.held_parts, self.held_said = set(), []
        self.planes = self.planes_given
        for one in self.planes_given:
            if one.part is not None and one.part != self.part.name:
                continue
            axis = AXES.index(one.axis)
            faces = [
                f
                for f in self.shape.Faces
                if load._flat(f, axis, one.at) and not any(f.isSame(b) for b in self.between)
            ]
            if not faces:
                continue
            if one.how == "fixed":
                self.fixed += faces
            else:
                self.standing.setdefault(axis, []).extend(faces)
            self.held_parts.add(self.part.name)
            self.held_said.append(
                f"{one.how} {'to' if one.how == 'fixed' else 'on'} {one.axis}={one.at:g}, "
                f"{len(faces)} face(s), {sum(f.Area for f in faces):.1f} mm²"
            )
        self.free = not self.fixed and not self.standing
        if self.free:
            self.held_said.append("held by nothing in the assembly: pinned at three nodes")

    def _pins(self, held: list[int]) -> tuple[str, list[int]]:
        if not self.free:
            return super()._pins(held)
        order = sorted(self.nodes)
        chosen = pins(np.array([self.nodes[n] for n in order]))
        lines = "*BOUNDARY\n"
        for row, axes in chosen:
            node = order[row]
            lines += "".join(f"{node},{a + 1},{a + 1}\n" for a in axes)
            self.constrained.setdefault(node, set()).update(axes)
        return lines, [order[row] for row, _ in chosen]

    def _prerequisites(self, wrong: str) -> str:
        """A part held by nothing in the assembly has no held face for FreeCAD to see: its
        three pins are written into CalculiX's input by `_pins`."""
        if not self.free:
            return wrong
        return "\n".join(
            line for line in wrong.splitlines() if "No mechanical boundary conditions" not in line
        ).strip()

    def _add_to(self, path: str) -> None:
        if self.args.lift is not None:
            self._weights()
        elif self.places:
            self._put(self.places)
        else:
            self.loads, self.pushed = [], {}
        self._joined()
        self._write(path)

    def _joined(self) -> None:
        """What its neighbours put on it in the first pass, on the faces it meets them by."""
        self.joined_said: dict[str, Any] = {}
        if self.given is None:
            if self.met_faces:
                raise NoAnswer(
                    f"pass 2, {self.part.name} on its own: it meets other parts over "
                    f"{len(self.met_faces)} face(s), and the first pass gave it nothing they "
                    "put on it, so nothing of it is read"
                )
            return
        triangles = [t for f in self.between for t in self._triangles(f)]
        if not triangles:
            raise NoAnswer(f"the mesh of {self.part.name} has no triangle where it meets another")
        which = nearest(self.given["centres"], np.array([t[2] for t in triangles]))
        shares: dict[int, float] = {}
        pushes: dict[int, np.ndarray] = {}
        for (nodes, area, _), k in zip(triangles, which, strict=True):
            # A uniform push on a second-order triangle is a third on each mid-side node.
            for node in nodes[3:]:
                shares[node] = shares.get(node, 0.0) + area / 3
                pushes[node] = pushes.get(node, 0) + self.given["pushes"][k] * area / 3
        order = sorted(pushes)
        places = np.array([self.nodes[n] for n in order])
        before = np.array([pushes[n] for n in order])
        after = even_out(
            places,
            np.array([shares[n] for n in order]),
            before,
            np.asarray(self.given["force"]),
            np.asarray(self.given["moment"]),
        )
        for node, push in zip(order, after, strict=True):
            self.pushed[node] = self.pushed.get(node, 0) + push
        mapped = before.sum(axis=0)
        given = float(np.linalg.norm(before, axis=1).sum())
        moved = float(np.linalg.norm(after - before, axis=1).sum())
        share = moved_share(before, after)
        if share > EVENED:
            raise NoAnswer(
                f"pass 2, {self.part.name} on its own: what its neighbours put on it in the "
                f"first pass, carried over to its own mesh, had {100 * share:.1f}% of it moved "
                f"to add up to the first pass's force and moment, more than the "
                f"{100 * EVENED:g}% it's held to, so nothing of it is read"
            )
        self.joined_said = {
            "force_n": load._list(self.given["force"]),
            "moment_nmm": load._list(self.given["moment"]),
            "mapped_force_n": load._list(mapped),
            "evened_n": moved,
            "given_n": given,
        }

    def _balance(self) -> None:
        self.part_rows = {self.part.name: np.arange(len(self.points))}


def _alone_solved(make: Any) -> dict[str, Any]:
    """One part on its own at one mesh, read; meshed again straight-sided where its curved
    mesh fails, as the whole is."""
    import FreeCAD as App

    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    try:
        doc = App.newDocument("Alone")
        with load._quiet():
            return _alone_read(make(False), doc)
    except load._Curved as curved:
        doc = App.newDocument("Alone")
        with load._quiet():
            found = _alone_read(make(True), doc)
        found["straight"] = str(curved)
        return found


def _alone_read(model: _Alone, doc: Any) -> dict[str, Any]:
    import FreeCAD as App

    try:
        model.join(doc)
        model.hold()
        model.solve(doc)
        given = sum(model.pushed.values(), np.zeros(3))
        scale = float(sum(np.linalg.norm(p) for p in model.pushed.values()))
        middle = np.array(model.points).mean(axis=0)
        turns = np.zeros(3)
        for node, push in model.pushed.items():
            turns += np.cross(model.nodes[node] - middle, push)
        held_turns = np.zeros(3)
        for node, push in model.held_force.items():
            held_turns += np.cross(model.nodes[node] - middle, push)
        reach = float(np.linalg.norm(np.ptp(model.points, axis=0)))
        return {
            "part": model.parts_read()[model.part.name],
            "mesh_mm": model.size,
            "nodes": model.fem.NodeCount,
            "elements": model.fem.VolumeCount,
            "mesh_s": model.mesh_s,
            "solve_s": model.solve_s,
            "held": model.held_said,
            "free": model.free,
            "given_n": load._list(given),
            "reaction_n": load._list(model.reaction),
            "off_n": float(np.linalg.norm(model.reaction + given)),
            "off_nmm": float(np.linalg.norm(held_turns + turns)),
            "scale_n": scale,
            "reach_mm": reach,
            "joined": model.joined_said,
            "iterated": model.iterated,
            "straight": None,
        }
    finally:
        App.closeDocument(doc.Name)


# The clock and the container's memory


def peak() -> int | None:
    """The most the container has held so far, in bytes, where the kernel keeps it."""
    try:
        return int((load.CGROUP / "memory.peak").read_text().strip())
    except (OSError, ValueError):
        return None


class Clock:
    """The container's time, as the host gives it: each kind of step's longest so far, and
    whether the next would end inside the limit. Started, it also rings at the limit less
    `MARGIN`, in whatever step it's in, so even a first step longer than any yet stops in time
    to say where it was."""

    def __init__(self, started: float) -> None:
        given = os.environ.get(TIMEOUT, "")
        self.limit = float(given) if given.replace(".", "", 1).isdigit() else None
        self.started = started
        self.longest: dict[str, float] = {}
        self.done: list[str] = []
        self.now = "the first pass"

    def start(self) -> None:
        left = self.left()
        if left is not None:
            signal.signal(signal.SIGALRM, self._ring)
            signal.alarm(max(1, int(left)))

    def stop(self) -> None:
        if self.limit is not None:
            signal.alarm(0)

    def _ring(self, *_: Any) -> None:
        raise OutOfTime(self._said(f"during {self.now}"))

    def time(self, kind: str, seconds: float) -> None:
        self.longest[kind] = max(self.longest.get(kind, 0.0), seconds)

    def left(self) -> float | None:
        if self.limit is None:
            return None
        return self.limit - MARGIN - (time.time() - self.started)

    def check(self, kind: str, what: str) -> None:
        """Stop before `what`, a step of `kind`, where the longest of its kind so far
        wouldn't end inside the container's limit."""
        self.now = what
        left = self.left()
        if left is not None and left < self.longest.get(kind, 0.0):
            raise OutOfTime(self._said(f"before {what}, which would end past it"))

    def _said(self, where: str) -> str:
        spent = time.time() - self.started
        return (
            f"two passes met the container's time, {self.limit:g} s, after {spent:.0f} s, "
            f"{where}: "
            + "; ".join(self.done or ["nothing was read yet"])
            + ". Nothing is judged on part of an assembly"
        )


class OutOfTime(NoAnswer):
    """Two passes stopped for the container's time, saying where."""


def _gib(value: int | None) -> str:
    return "unknown" if value is None else f"{value / 2**30:.2f} GiB"


def first(
    assembly: Assembly, placed: list[Any], args: Any, size: float, clock: Clock
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The whole, its bolts' bores squared, at the mesh and the coarser one: each solve as
    `load._solved` reads it, and what's said of the pass."""
    began = time.time()
    clock.now = "the first pass, the whole's bores squared"
    with load._quiet():
        square, count, kept = squared(assembly, placed)
    clock.now = f"the first pass, the whole at {size:g} mm"
    fine = load._solved(
        assembly, square, args, size, two_pass=True, weighed=placed, spread=False
    )
    clock.time("whole", time.time() - began)
    clock.check("whole", "the first pass's coarser mesh")
    coarse = load._solved(
        assembly, square, args, round(size * load.COARSER, 3), two_pass=True, weighed=placed,
        spread=False,
    )
    said = {
        "squared_bores": count,
        "round_kept": kept,
        "mesh_mm": size,
        "elements": fine["elements"],
        "nodes": fine["nodes"],
        "coarse_mesh_mm": coarse["mesh_mm"],
        "coarse_elements": coarse["elements"],
        "coarse_nodes": coarse["nodes"],
        "seconds": round(time.time() - began, 1),
        "memory_peak": peak(),
        "iterated": fine["iterated"] or coarse["iterated"],
    }
    clock.done.append(
        f"the first pass read the whole in {said['seconds']} s ({fine['elements']} elements)"
    )
    return fine, coarse, said


def second(
    assembly: Assembly,
    placed: list[Any],
    args: Any,
    size: float,
    fine: dict[str, Any],
    clock: Clock,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """Each part on its own, at the mesh and the coarser one, under what the first pass found
    on it: each read, and what's said of the pass."""
    import FreeCAD as App

    began = time.time()
    clock.now = "the second pass, the parts joined to find the faces each meets another by"
    # The parts as they are, joined so the faces each meets another by are faces of its own.
    doc = App.newDocument("Joined")
    try:
        with load._quiet():
            joined = load._Model(assembly, placed, args, size)
            joined.join(doc)
            solids = joined.solids
            met = {
                part.name: [
                    g
                    for g in solids[k].Faces
                    if any(
                        g.isSame(f)
                        for pair, faces in joined.met.items()
                        if part.name in pair
                        for f in faces
                    )
                ]
                for k, part in enumerate(assembly.parts)
            }
    finally:
        App.closeDocument(doc.Name)
    places: dict[str, list[tuple[tuple[float, ...], float]]] = {}
    if args.lift is None:
        for one in fine["raw_loads"]:
            places.setdefault(one["part"], []).append(
                (tuple(float(c) for c in one["at"]), float(np.linalg.norm(one["force"])))
            )
    reads: dict[str, dict[str, Any]] = {}
    rough: dict[str, dict[str, Any]] = {}
    for k, part in enumerate(assembly.parts):
        for mesh, into in ((size, reads), (round(size * load.COARSER, 3), rough)):
            kind = f"part {mesh}"
            clock.check(kind, f"the second pass's part {part.name} at {mesh:g} mm")
            started = time.time()

            def make(straight: bool, k: int = k, part: PartAt = part, mesh: float = mesh) -> Any:
                return _Alone(
                    assembly, part, solids[k], met[part.name], fine["planes"],
                    fine["tractions"].get(part.name), places.get(part.name, []), args, mesh,
                    straight,
                )  # fmt: skip

            into[part.name] = _alone_solved(make)
            into[part.name]["seconds"] = round(time.time() - started, 1)
            clock.time(kind, time.time() - started)
        clock.done.append(f"the second pass read {part.name} on its own")
    said = {
        "parts": len(reads),
        "mesh_mm": size,
        "coarse_mesh_mm": round(size * load.COARSER, 3),
        "elements": sum(r["elements"] for r in reads.values()),
        "most_elements": max(r["elements"] for r in reads.values()),
        "most_nodes": max(r["nodes"] for r in reads.values()),
        "seconds": round(time.time() - began, 1),
        "memory_peak": peak(),
        "iterated": sorted(n for n, r in {**reads, **rough}.items() if r["iterated"]),
    }
    return reads, rough, said


def pass_lines(report: Any, firsts: dict[str, Any], seconds: dict[str, Any], why: str) -> None:
    """What the reply says of the two passes, first: why, and each one's mesh, elements,
    nodes, time and the container's memory by its end."""
    report.line(
        f"read in two passes ({why}): the first reads the whole, each of its "
        f"{firsts['squared_bores']} bolts' bores stood in by a square one as wide"
        + (
            f" ({firsts['round_kept']} other round face(s) on bolts' axes, bosses, collars or "
            "counterbores, left round as one pass meshes them)"
            if firsts["round_kept"]
            else ""
        )
        + ", to give each joint's force and moment and how far each loaded place moves; the "
        "second reads "
        "each part on its own, its bores meshed round, under what the first found on it, to "
        "give each part's strain. Each figure below says which pass it's from"
    )
    report.line(
        f"pass 1, the whole: a mesh of at most {firsts['mesh_mm']:g} mm, "
        f"{firsts['elements']} elements and {firsts['nodes']} nodes, and at most "
        f"{firsts['coarse_mesh_mm']:g} mm, {firsts['coarse_elements']} elements and "
        f"{firsts['coarse_nodes']} nodes; {firsts['seconds']} s, the container's memory at "
        f"most {_gib(firsts['memory_peak'])} by its end"
        + (f"; {firsts['iterated']}" if firsts["iterated"] else "")
    )
    report.line(
        f"pass 2, each part on its own: {seconds['parts']} parts at a mesh of at most "
        f"{seconds['mesh_mm']:g} mm, {seconds['elements']} elements in all, the most "
        f"{seconds['most_elements']} elements and {seconds['most_nodes']} nodes in one part, "
        f"and again at {seconds['coarse_mesh_mm']:g} mm; {seconds['seconds']} s, the "
        f"container's memory at most {_gib(seconds['memory_peak'])} by its end"
        + (
            f"; {', '.join(seconds['iterated'])} solved by CalculiX's iterative solver, "
            "corrected, where its direct one met the container's memory"
            if seconds["iterated"]
            else ""
        )
    )


def checks(
    report: Any,
    fine: dict[str, Any],
    coarse: dict[str, Any],
    reads: dict[str, dict[str, Any]],
    rough: dict[str, dict[str, Any]],
    force: float,
    apart: list[str],
) -> None:
    """The two passes' own second checks: each joint's force on both of the first pass's
    meshes, and each part's balance in the second."""
    for name, one, other, gap in disagree(fine["joints"], coarse["joints"], force):
        apart.append(
            f"pass 1 reads the joint {name}'s force as {one:.2f} N on the mesh and {other:.2f} N "
            f"on the coarser one, {gap:.2f} N apart, more than the {100 * AGREE:g}% they're "
            "held to"
        )
    worst = max(
        (
            float(np.linalg.norm(np.asarray(fine["joints"][n]["force"])
                                 - np.asarray(coarse["joints"][n]["force"])))
            / max(float(np.linalg.norm(fine["joints"][n]["force"])), load.NOTHING * force),
            n,
        )
        for n in fine["joints"]
    ) if fine["joints"] else (0.0, "")  # fmt: skip
    report.second(
        f"pass 1, each joint's force on both meshes: the furthest apart is {worst[1] or 'none'}, "
        f"{100 * worst[0]:.2f}% apart, against the {100 * AGREE:g}% they're held to"
    )
    for name, joint in report.numbers.get("joints", {}).items():
        share = joint_share(joint)
        if share is not None and near(share, 1.0, JOINT_BAND):
            apart.append(
                f"pass 1 reads the joint {name}'s nearest figure at {100 * share:.1f}% of its "
                f"limit, within the {100 * JOINT_BAND:g}% two passes are held to: read it in one "
                "pass, or give it more margin"
            )
    for name, alone in reads.items():
        for mesh, read in (("mesh", alone), ("coarser mesh", rough[name])):
            off = read["off_n"] > load.REACTION * read["scale_n"] or read["off_nmm"] > (
                load.REACTION * read["scale_n"] * read["reach_mm"]
            )
            what = (
                f"its three pins carry {read['off_n']:.4f} N and {read['off_nmm']:.2f} N·mm"
                if read["free"]
                else f"what holds it carries {load._xyz(read['reaction_n'])} N, against the "
                f"{load._xyz(read['given_n'])} N put on it, {read['off_n']:.4f} N apart"
            )
            if mesh == "mesh":
                report.second(
                    f"pass 2, {name} on its own: {what}, of the {read['scale_n']:.2f} N put on "
                    "its nodes; what its neighbours put on it moved "
                    f"{read['joined'].get('evened_n', 0.0):.3f} N of "
                    f"{read['joined'].get('given_n', 0.0):.2f} N to add up to the first pass's"
                )
            if off:
                raise NoAnswer(
                    f"pass 2, {name} on its own, on the {mesh}: {what}, more than "
                    f"{100 * load.REACTION:g}% of the {read['scale_n']:.2f} N put on it, so what "
                    "it's given doesn't balance and nothing is read"
                )

