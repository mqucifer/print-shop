"""`load`: joined parts under a weight (mqucifer/print-shop#156).

A side table is bigger than the bed, so it's printed as parts and joined, with bought bolts and
inserts where it takes load. `bend` reads one arm pushed aside. Nothing read several parts joined
together with a weight on them: whether each part takes it the way it's printed, what each joint
has to carry, and how far the top moves.

**The reading.** The assembly file (`assembly.py`) gives each part's file, where it is, its
material and the way its layers are stacked, the joints, and where it's held. The parts are put
in their places and must meet face to face: two that pass through each other, two that meet with
no joint named, and a joint whose parts don't meet are each said, and nothing is solved. The
parts are then one body whose faces that meet are shared, so gmsh meshes it with the same nodes
on both sides of each joint, and CalculiX solves it once, linear and static, through FreeCAD's
FEM workbench. `--weight` kg is shared equally between the places `--at` names, each spread over
the surface within `--over` mm of it, and pulls `--toward` -z unless said.

**Each part:** its worst strain pulled across its layers, which is what parts one printed layer
from the next, held to the material's limit across them; and its largest stretch any way, with
where it is and how far out of the layers' plane it points, held to the limit in their plane.
A part pushed together across its layers isn't pulled apart, so only a pull counts there.

**Each joint:** the force and the moment through the faces that meet, from the solve's own balance
at each node there: what the first part's elements carry, less what's put on it from outside
(`element_forces`). Every joint is solved as one piece, the faces tied all over, as a glued joint
is and as a bolted one is while its bolts hold. So a bolted joint is judged by what its bolts then
have to hold: the joint's pull shared between them, and its bending moment taken about the face's
edge on the pushed side, each bolt in proportion to its distance from that edge, against the least
of the bolt's proof load, the insert's pull-out where one is sourced, and the joint's own figure.
An insert pulls out of printed plastic long before a steel bolt gives, so
where a bolt has a pull to hold and neither its insert nor its joint has a figure, the answer is
`UNREAD`, never `OK` on the bolt alone. A bonded joint that carries anything is `UNREAD` too
(mqucifer/print-shop#170): glue is a second hold only, and no glue line has a figure here.

**A through joint** (mqucifer/print-shop#168, `load_through.py`) is a bolt right through both
parts, with a nut and washers, and no insert to give first. Its bolts' pulls are found the same
way, and it's judged by the worst of: the bolt against its proof load; the pressure under each
washer, or under the nut in its pocket, and where the shank leans on its hole's wall, against
what the plastic takes pressed that way to its layers; and the plastic round each hole, solved
again by itself under the bolt's pull and shear and read as strain, as any part is. The reply
names which is nearest its limit. It's never `OK` on the bolt alone.

**A dovetail joint** (mqucifer/print-shop#170, `dovetail.py` and `load_dovetail.py`) is a tail
slid into a socket: a leg split for the bed. Its flanks and its seat only push, so what the
tied solve says the joint carries is put back on its faces as pushes alone. It's judged by the
worst of: a load that drives the tail out of its seat, which only its latch holds, never
friction or glue; a load its faces can't balance by pushing, which opens it; the pressure on
each face; the latch's catch and its tab, solved by itself under what it holds; and the tail's
neck and the socket's walls, each solved again by itself under the faces' pushes. Before
anything is solved it's taken apart the way it went together (`way_in.py`, mqucifer/print-shop#184
and mqucifer/print-shop#185): its tail out of its socket against `slides` past nothing but its
latch, and its latch's tab read by `bend` pushed as far as it has to go to get on. A joint that
can't be put together fails whatever the load, and nothing is solved.

**Lifted** (`--lift PART`, mqucifer/print-shop#170), the assembly is held by that part's
highest level and every other part hangs by its own weight: its solid volume at its material's
density. That's the case that pulls a leg's lower piece the way its tail slides out.

A touching joint is held by nothing: it fails where it's pulled apart, or where its push falls
outside the faces that meet, which tips it open; the friction it needs to stay put is a figure,
held to `--friction` where that's given.

**How far it moves:** each loaded place, along the load, and the most anywhere.

**Second methods.** The whole solve again on a mesh half as coarse again: each figure that's
judged, on both. The held faces' reaction, which CalculiX prints, against the weight put on. And
for a joint with every held face on one side of it, statics: what crosses it is the load beyond
it, whatever the mesh. Two readings of one figure on opposite sides of its limit are `DISAGREE`.

**What it can't read:** a part's walls and infill (each is solid here, so a sparse part is less
stiff and less strong than this), a printed part's stiffness the other way (one modulus every
way), creep under a weight left on for months, a bolt's preload or a joint that opens (it's the
first, linear answer, with no contact), shear where an insert bears on its plastic, a nut's
thread, a bolt's length, a dovetail's glue and friction, the parts' own weight under a
`--weight`, buckling, and anything that isn't still: a knock, or someone sitting down.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from . import dovetail, load_dovetail, load_through, way_in
from .assembly import AXES, DIAMETER, Assembly, Held, Joint, PartAt, read
from .cae import freecad
from .limits import over, said
from .report import NoAnswer, Report

#: Standard gravity, in m/s²: a kilogram's weight in newtons.
G = 9.80665
#: The elements across the thinnest part, unless `--mesh` says: second-order tetrahedra.
ACROSS = 2.0
#: The mesh is no finer than this unless `--mesh` says, in mm, and no finer than leaves about
#: `ELEMENTS` of them, which the container's 3 GiB solves in a minute or two.
MESH_LEAST = 0.5
ELEMENTS = 40_000
#: The second solve's mesh, as a multiple of the first's.
COARSER = 1.5
#: A coordinate is on a plane within this, in mm.
ON = 1e-6
#: A place `--at` names is on the surface within this, in mm.
ON_SURFACE = 0.5
#: Two parts pass through each other past this, in mm³.
OVERLAP = 1e-4
#: A joint carries nothing under this share of the load.
NOTHING = 1e-3
#: A push's point is on the faces that meet within this, in mm.
ON_FACE = 0.01
#: The held faces' reaction is the weight within this share, or nothing is said: CalculiX's own
#: sum of the forces it solved for.
REACTION = 0.01


def _xyz(point: Any) -> str:
    return (
        "(" + ", ".join(f"{c + 0.0:g}" for c in np.round(np.asarray(point, dtype=float), 2)) + ")"
    )


def _list(values: Any, places: int = 4) -> list[float]:
    return [round(float(v), places) + 0.0 for v in values]


def bolt_pulls(
    force: np.ndarray,
    moment: np.ndarray,
    centre: np.ndarray,
    normal: np.ndarray,
    bolts: np.ndarray,
    outline: np.ndarray,
) -> list[dict[str, float]]:
    """What each bolt of a flat joint has to hold, in N. `force` and `moment` (about `centre`)
    are what the joint carries, a pull where the force is along `normal`. The pull is shared
    between the bolts. The bending moment, less what a push takes off it, is taken about the
    face's edge on the pushed side, the furthest of `outline` that way, each bolt in proportion
    to its distance from that edge. The shear is shared too."""
    count = len(bolts)
    along = float(force @ normal)
    shear = float(np.linalg.norm(force - along * normal)) / count
    pulls = np.full(count, max(along, 0.0) / count)
    bending = moment - (moment @ normal) * normal
    size = float(np.linalg.norm(bending))
    if size > 1e-9:
        # The side that's pulled is this way across the face from its centre.
        pulled = np.cross(normal, bending / size)
        edge = float(((outline - centre) @ pulled).min())
        levers = (bolts - centre) @ pulled - edge
        about_edge = size - max(-along, 0.0) * -edge
        if about_edge > 0 and float((levers**2).sum()) > 1e-12:
            pulls = pulls + about_edge * np.maximum(levers, 0.0) / float((levers**2).sum())
    return [{"pull_n": float(p), "shear_n": shear} for p in pulls]


#: A second-order tetrahedron's mid-side nodes, by the corners each lies between.
MIDS = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
#: Its four integration points, as CalculiX has them: three coordinates each, the first corner's
#: being what's left of 1.
_A, _B = 0.5854101966249685, 0.1381966011250105
POINTS = ((_B, _B, _B), (_A, _B, _B), (_B, _A, _B), (_B, _B, _A))


def element_forces(
    places: np.ndarray, moved: np.ndarray, modulus: float, poisson: float
) -> np.ndarray:
    """The force each second-order tetrahedron's stress puts on each of its ten nodes, in N:
    `places` and `moved` are each element's nodes, four corners and then `MIDS`, where they are
    and how far each moved, in mm. It's the element's own stiffness times its movement, the sum
    CalculiX balances at every node, so the forces on a part's nodes add up to what's put on
    it exactly, where the stress at a sharp inside corner, read off the surface, doesn't."""
    lame = modulus * poisson / ((1 + poisson) * (1 - 2 * poisson))
    shear = modulus / (2 * (1 + poisson))
    corners = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    forces = np.zeros_like(places, dtype=float)
    for point in POINTS:
        share = np.array([1 - sum(point), *point])
        slopes = np.zeros((10, 3))
        for i in range(4):
            slopes[i] = (4 * share[i] - 1) * corners[i]
        for k, (i, j) in enumerate(MIDS, 4):
            slopes[k] = 4 * (share[i] * corners[j] + share[j] * corners[i])
        # How the element's own coordinates map to space, and each node's slope in space.
        mapped = np.einsum("ak,nai->nki", slopes, places)
        in_space = np.einsum("nik,ak->nai", np.linalg.inv(mapped), slopes)
        grown = np.einsum("nai,naj->nij", moved, in_space)
        strain = (grown + grown.transpose(0, 2, 1)) / 2
        stress = 2 * shear * strain
        stress[:, range(3), range(3)] += lame * np.trace(strain, axis1=1, axis2=2)[:, None]
        weight = np.abs(np.linalg.det(mapped)) / 24
        forces += weight[:, None, None] * np.einsum("nij,naj->nai", stress, in_space)
    return forces


def beyond(
    assembly: Assembly,
    joint: Joint,
    loads: list[dict[str, Any]],
    held: set[str],
    centre: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """What crosses a joint by statics, as the force and moment the second part puts on the
    first: the load on the side of it that nothing holds. None where both sides are held, or
    the parts are joined round it another way."""
    first, second = joint.between
    reached = {first}
    grown = True
    while grown:
        grown = False
        for other in assembly.joints:
            if other is joint:
                continue
            a, b = other.between
            for near, far in ((a, b), (b, a)):
                if near in reached and far not in reached:
                    reached.add(far)
                    grown = True
    if second in reached:
        return None
    near_held, far_held = bool(held & reached), bool(held - reached)
    if near_held == far_held:
        return None
    # The side nothing holds is in balance under its loads and the joint alone.
    free_is_far = near_held
    force, moment = np.zeros(3), np.zeros(3)
    for load in loads:
        if (load["part"] in reached) != free_is_far:
            push = np.asarray(load["force"])
            force += push
            moment += np.cross(np.asarray(load["middle"]) - centre, push)
    return (force, moment) if free_is_far else (-force, -moment)


def _placed(assembly: Assembly) -> list[Any]:
    """Each part's one solid, where the assembly puts it."""
    import FreeCAD as App
    import Part

    folder = Path(assembly.path).parent
    found = []
    for part in assembly.parts:
        solids = [s for s in Part.read(str(folder / part.file)).Solids if s.Volume > 1e-6]
        if len(solids) != 1:
            raise NoAnswer(
                f"the part {part.name}'s file, {part.file}, holds {len(solids)} solids: a part is "
                "one solid, and each other piece is a part of its own"
            )
        solid = solids[0].copy()
        for axis, degrees in part.turn:
            way = [0.0, 0.0, 0.0]
            way[AXES.index(axis)] = 1.0
            solid.rotate(App.Vector(0, 0, 0), App.Vector(*way), degrees)
        solid.translate(App.Vector(*part.at))
        found.append(solid)
    for i, first in enumerate(found):
        for j in range(i + 1, len(found)):
            if not first.BoundBox.intersect(found[j].BoundBox):
                continue
            shared = first.common(found[j])
            if shared.Volume > OVERLAP:
                box = shared.BoundBox
                names = f"{assembly.parts[i].name} and {assembly.parts[j].name}"
                raise NoAnswer(
                    f"{names} pass through each other where the assembly puts them, by "
                    f"{shared.Volume:.3f} mm³, from {_xyz((box.XMin, box.YMin, box.ZMin))} to "
                    f"{_xyz((box.XMax, box.YMax, box.ZMax))}: parts meet face to face. Move one "
                    "or cut it back, and `clearance` reads the pair"
                )
    return found


def _span(box: Any, axis: int) -> tuple[float, float]:
    return ((box.XMin, box.XMax), (box.YMin, box.YMax), (box.ZMin, box.ZMax))[axis]


def _flat(shape: Any, axis: int, at: float) -> bool:
    low, high = _span(shape.BoundBox, axis)
    return abs(low - at) < ON and abs(high - at) < ON


class _Model:
    """The joined parts in one FreeCAD document, meshed and solved once."""

    def __init__(self, assembly: Assembly, placed: list[Any], args: Any, size: float) -> None:
        self.assembly, self.placed, self.args, self.size = assembly, placed, args, size
        self.toward = np.zeros(3)
        self.toward[AXES.index(args.toward[1])] = 1.0 if args.toward[0] == "+" else -1.0

    # The body

    def join(self, doc: Any) -> None:
        """The parts as one body with the faces that meet shared, and each joint's faces."""
        placed = self.placed
        whole = placed[0] if len(placed) == 1 else placed[0].generalFuse(placed[1:])[0]
        self.feature = doc.addObject("Part::Feature", "Parts")
        self.feature.Shape = whole
        self.shape = shape = self.feature.Shape
        if len(shape.Solids) != len(placed):
            raise NoAnswer(
                f"joined, the {len(placed)} parts are {len(shape.Solids)} solids: two of them "
                "pass through each other, or one is in pieces"
            )
        self.solids = []
        for part, solid in zip(self.assembly.parts, placed, strict=True):
            centre = solid.CenterOfMass
            near = min(shape.Solids, key=lambda s: s.CenterOfMass.distanceToPoint(centre))
            if abs(near.Volume - solid.Volume) > 1e-3 * solid.Volume + 1e-3:
                raise NoAnswer(f"joined, the part {part.name} isn't the solid it was")
            self.solids.append(near)
        names = [p.name for p in self.assembly.parts]
        self.met: dict[frozenset[str], list[Any]] = {}
        for i, first in enumerate(self.solids):
            for j in range(i + 1, len(self.solids)):
                theirs = self.solids[j].Faces
                shared = [f for f in first.Faces if any(f.isSame(g) for g in theirs)]
                if shared:
                    self.met[frozenset((names[i], names[j]))] = shared
        named = {frozenset(j.between): j for j in self.assembly.joints}
        for pair, faces in self.met.items():
            if pair not in named:
                a, b = sorted(pair)
                centre = faces[0].CenterOfMass
                raise NoAnswer(
                    f"{a} and {b} meet over {sum(f.Area for f in faces):.1f} mm², round "
                    f"{_xyz((centre.x, centre.y, centre.z))}, and the assembly names no joint "
                    "between them: say whether it's bonded, bolted or touching"
                )
        for pair, joint in named.items():
            if pair not in self.met:
                a, b = (placed[names.index(n)] for n in joint.between)
                raise NoAnswer(
                    f"the joint {joint.name} is between parts that don't meet: they're "
                    f"{a.distToShape(b)[0]:.3f} mm apart where the assembly puts them. Parts "
                    "meet face to face"
                )
        self.joint_faces = {j.name: self.met[frozenset(j.between)] for j in self.assembly.joints}
        for joint in self.assembly.joints:
            if joint.kind == "dovetail":
                tail = self.solids[names.index(joint.between[0])]
                load_dovetail.check(joint, self.joint_faces[joint.name], tail)
        self.between = [f for faces in self.met.values() for f in faces]

    def _face_name(self, face: Any) -> str:
        for number, other in enumerate(self.shape.Faces, 1):
            if face.isSame(other):
                return f"Face{number}"
        raise NoAnswer("a face of the joined parts couldn't be found again")

    def hold(self) -> None:
        """The faces each held plane names: fixed, or standing on it."""
        held = list(self.assembly.held)
        names = [p.name for p in self.assembly.parts]
        if self.args.lift is not None:
            # Lifted by one part: held by that part's highest level, and by nothing else.
            axis = int(np.abs(self.toward).argmax())
            span = _span(self.solids[names.index(self.args.lift)].BoundBox, axis)
            top = span[0 if self.toward[axis] > 0 else 1]
            held = [Held(AXES[axis], top, "fixed", self.args.lift)]
        elif not held:
            low = self.shape.BoundBox.ZMin
            held = [Held("z", low, "standing")]
        self.fixed: list[Any] = []
        self.standing: dict[int, list[Any]] = {}
        self.held_parts: set[str] = set()
        self.held_said = []
        for one in held:
            axis = AXES.index(one.axis)
            faces = []
            for name, solid in zip(names, self.solids, strict=True):
                if one.part is not None and name != one.part:
                    continue
                for face in solid.Faces:
                    if _flat(face, axis, one.at) and not any(face.isSame(b) for b in self.between):
                        faces.append(face)
                        self.held_parts.add(name)
            if not faces and self.args.lift is not None:
                raise NoAnswer(
                    f"--lift {one.part}: {one.part} has no flat face at its highest level, "
                    f"{one.axis}={one.at:g}, to be lifted by"
                )
            if not faces:
                box = self.shape.BoundBox
                raise NoAnswer(
                    f"no flat face of {one.part or 'the assembly'} lies in the held plane "
                    f"{one.axis}={one.at:g}: it runs from {_span(box, axis)[0]:g} to "
                    f"{_span(box, axis)[1]:g} along {one.axis}"
                )
            if one.how == "fixed":
                self.fixed += faces
            else:
                self.standing.setdefault(axis, []).extend(faces)
            self.held_said.append(
                f"{one.how} {'to' if one.how == 'fixed' else 'on'} {one.axis}={one.at:g}, "
                f"{len(faces)} face(s), {sum(f.Area for f in faces):.1f} mm²"
            )
        if not self.fixed:
            across = [a for a in range(3) if a not in self.standing and abs(self.toward[a]) > ON]
            if across:
                raise NoAnswer(
                    f"the load is --toward {self.args.toward}, along what the assembly stands on: "
                    "standing holds it square to its plane only, and it would slide. Hold a "
                    "plane `fixed` in the assembly's `held`"
                )

    # The solve

    def solve(self, doc: Any) -> None:
        import ObjectsFem
        from femmesh.gmshtools import GmshTools
        from femtools import ccxtools

        analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
        solver.AnalysisType = "static"
        solver.GeometricalNonlinearity = "linear"
        analysis.addObject(solver)
        # One material for the body: every part's so far. The assembly file is refused before
        # this if two parts' differ in stiffness.
        material = self.assembly.parts[0].material
        self.modulus = material.modulus_mpa
        solid = ObjectsFem.makeMaterialSolid(doc, "Material")
        values = solid.Material
        values.update(
            Name="Printed",
            YoungsModulus=f"{material.modulus_mpa:g} MPa",
            PoissonRatio=f"{material.poisson:g}",
            Density="1270 kg/m^3",
        )
        solid.Material = values
        analysis.addObject(solid)
        if self.fixed:
            fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
            fixed.References = [(self.feature, tuple(self._face_name(f) for f in self.fixed))]
            analysis.addObject(fixed)
        for axis, faces in self.standing.items():
            stand = ObjectsFem.makeConstraintDisplacement(doc, f"Standing{AXES[axis]}")
            stand.References = [(self.feature, tuple(self._face_name(f) for f in faces))]
            for other, name in enumerate(AXES):
                setattr(stand, f"{name}Free", other != axis)
            setattr(stand, f"{AXES[axis]}Displacement", 0.0)
            analysis.addObject(stand)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = self.feature
        mesh.CharacteristicLengthMax = self.size
        mesh.ElementOrder = "2nd"
        # A second-order element's curved side, on a bolt's hole far smaller than the mesh,
        # can turn it inside out, and CalculiX then solves nothing: gmsh straightens those
        # (mqucifer/print-shop#168). A part with flat sides only is meshed as it was.
        mesh.HighOrderOptimize = "Optimization"
        analysis.addObject(mesh)
        doc.recompute()
        began = time.time()
        wrong = GmshTools(mesh).create_mesh()
        self.mesh_s = round(time.time() - began, 2)
        self.fem = fem = mesh.FemMesh
        if wrong or not fem.VolumeCount:
            raise NoAnswer(f"gmsh made no mesh of the joined parts: {str(wrong)[:300]}")
        self.nodes = {n: np.array([p.x, p.y, p.z]) for n, p in fem.Nodes.items()}
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir(tempfile.mkdtemp(prefix="load-"))
        fea.setup_ccx()
        wrong = fea.check_prerequisites()
        if wrong:
            raise NoAnswer(f"the solve couldn't be set up: {wrong[:300]}")
        began = time.time()
        fea.write_inp_file()
        self._add_to(fea.inp_file_name)
        fea.ccx_run()
        fea.load_results()
        self.solve_s = round(time.time() - began, 2)
        results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
        if "nonpositive jacobian" in str(getattr(fea, "ccx_stdout", "")):
            raise NoAnswer(
                f"CalculiX solved nothing: an element of the mesh, at most {self.size:g} mm, is "
                "turned inside out, as one with a curved side on a small hole or a tight round "
                "can be. Give a finer --mesh"
            )
        if not results or not results[0].NodeStrainXX:
            raise NoAnswer(
                f"CalculiX gave no result for the {fem.VolumeCount} elements, at a mesh of "
                f"{self.size:g} mm: it may have run out of the container's memory. Give a "
                "coarser --mesh"
            )
        result = results[0]
        self.row = {n: i for i, n in enumerate(result.NodeNumbers)}
        count = len(result.NodeNumbers)
        self.strain, self.stress = np.zeros((count, 3, 3)), np.zeros((count, 3, 3))
        for (i, j), name in {
            (0, 0): "XX", (1, 1): "YY", (2, 2): "ZZ", (0, 1): "XY", (0, 2): "XZ", (1, 2): "YZ"
        }.items():  # fmt: skip
            self.strain[:, i, j] = self.strain[:, j, i] = getattr(result, f"NodeStrain{name}")
            self.stress[:, i, j] = self.stress[:, j, i] = getattr(result, f"NodeStress{name}")
        self.moved = np.array([[v.x, v.y, v.z] for v in result.DisplacementVectors])
        self.points = np.array([self.nodes[n] for n in result.NodeNumbers])
        self.reaction, self.held_force = _reaction(os.path.splitext(fea.inp_file_name)[0] + ".dat")
        self._balance()

    def _balance(self) -> None:
        """The force each part's elements put on each node, and what's put on each part's
        nodes from outside: its loads and its held faces' reaction."""
        names = [p.name for p in self.assembly.parts]
        ids = self.fem.Volumes
        wired = np.array([[self.row[n] for n in self.fem.getElementNodes(e)] for e in ids])
        places = self.points[wired]
        # The mid-side nodes in `MIDS`' order, each found by where it lies.
        between = np.stack([(places[:, i] + places[:, j]) / 2 for i, j in MIDS], axis=1)
        apart = np.linalg.norm(between[:, :, None, :] - places[:, None, 4:, :], axis=3)
        order = apart.argmin(axis=2)
        wired = np.concatenate([wired[:, :4], np.take_along_axis(wired[:, 4:], order, axis=1)], 1)
        material = self.assembly.parts[0].material
        forces = element_forces(
            self.points[wired], self.moved[wired], material.modulus_mpa, material.poisson
        )
        owned = self._owned(wired, self.row, self.points)
        self.part_rows = {name: np.unique(wired[owned[k]]) for k, name in enumerate(names)}
        self.within = {}
        for k, name in enumerate(names):
            total = np.zeros((len(self.points), 3))
            np.add.at(total, wired[owned[k]], forces[owned[k]])
            self.within[name] = total
        # What's put on each part from outside, node by node.
        self.outside = {name: np.zeros((len(self.points), 3)) for name in names}
        for load in self.loads:
            for node, push in load["pushes"].items():
                self.outside[load["part"]][self.row[node]] += push
        for name, solid in zip(names, self.solids, strict=True):
            mine = [f for f in [*self.fixed, *sum(self.standing.values(), [])]
                    if any(f.isSame(g) for g in solid.Faces)]  # fmt: skip
            for node in {n for f in mine for n in self.fem.getNodesByFace(f)}:
                if node in self.held_force:
                    self.outside[name][self.row[node]] += self.held_force.pop(node)
        # A pin's reaction, on no held face of its own: it's nothing where the load is square.

    def _owned(self, wired: np.ndarray, row: dict[int, int], points: np.ndarray) -> np.ndarray:
        """Which part each element is in, as a row of flags for each part: `wired` is each
        element's nodes as rows of `points`, and `row` each node's. Elements that share
        a face are in one part, but for a face that's one of a joint's triangles: so each part
        is what's reached from one of its elements without crossing a joint, and which part
        that is, is asked of the solid once, at one element's middle."""
        import FreeCAD as App

        joined = {
            tuple(sorted(row[n] for n in nodes[:3]))
            for face in self.between
            for nodes, _, _ in self._triangles(face)
        }
        beside: dict[tuple[int, ...], list[int]] = {}
        for element, corners in enumerate(wired[:, :4].tolist()):
            for skip in range(4):
                face = tuple(sorted(corners[:skip] + corners[skip + 1 :]))
                if face not in joined:
                    beside.setdefault(face, []).append(element)
        next_to: list[list[int]] = [[] for _ in range(len(wired))]
        for pair in beside.values():
            if len(pair) == 2:
                next_to[pair[0]].append(pair[1])
                next_to[pair[1]].append(pair[0])
        owned = np.zeros((len(self.solids), len(wired)), dtype=bool)
        seen = np.zeros(len(wired), dtype=bool)
        for start in range(len(wired)):
            if seen[start]:
                continue
            reached, todo = [start], [start]
            seen[start] = True
            while todo:
                for other in next_to[todo.pop()]:
                    if not seen[other]:
                        seen[other] = True
                        reached.append(other)
                        todo.append(other)
            middle = App.Vector(*points[wired[start, :4]].mean(axis=0))
            part = next(
                (k for k, s in enumerate(self.solids) if s.isInside(middle, 1e-6, False)), None
            )
            if part is None:
                raise NoAnswer("an element of the mesh is in none of the parts")
            owned[part, reached] = True
        return owned

    def _triangles(self, face: Any) -> list[tuple[tuple[int, ...], float, np.ndarray]]:
        """The mesh's triangles on a face: each one's nodes, corners first, its area and its
        centre."""
        found = []
        for element in self.fem.getFacesByFace(face):
            nodes = self.fem.getElementNodes(element)
            a, b, c = (self.nodes[n] for n in nodes[:3])
            area = 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
            found.append((nodes, area, (a + b + c) / 3))
        return found

    def _patches(self) -> None:
        """Each place `--at` names as the triangles of the surface within `--over` of it, and
        the force on each of their nodes."""
        import FreeCAD as App
        import Part

        names = [p.name for p in self.assembly.parts]
        outer = [f for f in self.shape.Faces if not any(f.isSame(b) for b in self.between)]
        each = self.args.weight * G / len(self.args.at)
        self.loads: list[dict[str, Any]] = []
        self.pushed: dict[int, np.ndarray] = {}
        for place in self.args.at:
            point = np.asarray(place, dtype=float)
            vertex = Part.Vertex(App.Vector(*place))
            near = [(f.distToShape(vertex)[0], f) for f in outer]
            least = min(d for d, _ in near)
            if least > ON_SURFACE:
                box = self.shape.BoundBox
                raise NoAnswer(
                    f"--at {_xyz(point)} is {least:.3f} mm from the assembly's surface, which "
                    f"runs from {_xyz((box.XMin, box.YMin, box.ZMin))} to "
                    f"{_xyz((box.XMax, box.YMax, box.ZMax))}: a weight sits on a surface"
                )
            faces = [f for d, f in near if d <= ON_SURFACE]
            triangles = [t for f in faces for t in self._triangles(f)]
            within = [t for t in triangles if np.linalg.norm(t[2] - point) <= self.args.over]
            if not within:
                within = [min(triangles, key=lambda t: float(np.linalg.norm(t[2] - point)))]
            area = sum(t[1] for t in within)
            patch: set[int] = set()
            pushes: dict[int, np.ndarray] = {}
            for nodes, size, _ in within:
                # A uniform push on a second-order triangle is a third on each mid-side node.
                for node in nodes[3:]:
                    pushes[node] = pushes.get(node, 0) + each * size / area / 3 * self.toward
                patch.update(nodes)
            for node, push in pushes.items():
                self.pushed[node] = self.pushed.get(node, 0) + push
            part = next(
                n
                for n, s in zip(names, self.solids, strict=True)
                if any(faces[0].isSame(f) for f in s.Faces)
            )
            self.loads.append(
                {
                    "at": point,
                    "part": part,
                    "force": each * self.toward,
                    "patch_mm2": area,
                    "nodes": sorted(patch),
                    "pushes": pushes,
                    # Where the spread weight acts: a patch cut off by an edge isn't round.
                    "middle": sum(self.nodes[n] * (p @ self.toward) for n, p in pushes.items())
                    / each,
                }
            )

    def _weights(self) -> None:
        """Each part's own weight but the lifted one's (mqucifer/print-shop#170): its solid
        volume at its material's density, pulling `--toward`, put on every node of its own
        elements. A uniform pull on a second-order tetrahedron is a fifth of it on each
        mid-side node, less a twentieth on each corner."""
        order = sorted(self.nodes)
        row = {n: i for i, n in enumerate(order)}
        points = np.array([self.nodes[n] for n in order])
        wired = np.array([[row[n] for n in self.fem.getElementNodes(e)] for e in self.fem.Volumes])
        owned = self._owned(wired, row, points)
        corners = points[wired[:, :4]]
        volume = np.abs(np.linalg.det(corners[:, 1:] - corners[:, :1])) / 6
        self.loads = []
        self.pushed = {}
        for k, (part, solid) in enumerate(zip(self.assembly.parts, self.placed, strict=True)):
            if part.name == self.args.lift:
                continue
            grams = solid.Volume * part.material.density_g_cm3 / 1000
            newtons = grams / 1000 * G
            mine = owned[k]
            # The mesh's own volume differs from the solid's by its chords on a curved face.
            each = volume[mine] * newtons / float(volume[mine].sum())
            shared = np.zeros(len(points))
            np.add.at(shared, wired[mine, :4], -each[:, None] / 20)
            np.add.at(shared, wired[mine, 4:], each[:, None] / 5)
            rows = np.unique(wired[mine])
            pushes = {order[r]: shared[r] * self.toward for r in rows if shared[r] != 0}
            for node, push in pushes.items():
                self.pushed[node] = self.pushed.get(node, 0) + push
            centre = solid.CenterOfMass
            middle = np.array([centre.x, centre.y, centre.z])
            self.loads.append(
                {
                    "at": middle,
                    "part": part.name,
                    "force": newtons * self.toward,
                    "patch_mm2": None,
                    "nodes": [order[r] for r in rows],
                    "pushes": pushes,
                    "middle": middle,
                    "volume_mm3": float(solid.Volume),
                    "grams": grams,
                }
            )

    def _add_to(self, path: str) -> None:
        """The loads, the pins that stop a standing assembly sliding, and the held faces'
        reaction asked for, written into the input FreeCAD made."""
        if self.args.lift is not None:
            self._weights()
        else:
            self._patches()
        held = sorted(
            {
                n
                for f in [*self.fixed, *sum(self.standing.values(), [])]
                for n in self.fem.getNodesByFace(f)
            }
        )
        step = "*CLOAD\n" + "".join(
            f"{node},{axis + 1},{push[axis]:.9g}\n"
            for node, push in sorted(self.pushed.items())
            for axis in range(3)
            if abs(push[axis]) > 0
        )
        if not self.fixed:
            # Standing holds it square to each plane only. Two nodes stop the rest: one held
            # along the other axes, and the furthest from it held from turning about it. With
            # a load square to the plane they carry nothing.
            free = [a for a in range(3) if a not in self.standing]
            first = held[0]
            last = max(held, key=lambda n: float(np.linalg.norm(self.nodes[n] - self.nodes[first])))
            step += "*BOUNDARY\n" + "".join(f"{first},{a + 1},{a + 1}\n" for a in free)
            if len(free) == 2:
                apart = np.abs(self.nodes[last] - self.nodes[first])
                steer = min(free, key=lambda a: float(apart[a]))
                step += f"{last},{steer + 1},{steer + 1}\n"
        step += "*NODE PRINT, NSET=HELDALL, TOTALS=YES\nRF\n"
        text = Path(path).read_text()
        start, outputs = text.find("\n*STEP"), text.find("** Outputs")
        if start < 0 or outputs < 0:
            raise NoAnswer("FreeCAD's CalculiX input has no step to put the load in")
        outputs = text.rfind("\n***", 0, outputs) + 1
        rows = [held[i : i + 12] for i in range(0, len(held), 12)]
        sets = "*NSET, NSET=HELDALL\n" + "".join(",".join(map(str, r)) + ",\n" for r in rows)
        text = text[: start + 1] + sets + text[start + 1 : outputs] + step + text[outputs:]
        Path(path).write_text(text)

    # The readings

    def parts_read(self) -> dict[str, Any]:
        found = {}
        for part in self.assembly.parts:
            rows = self.part_rows[part.name]
            way = part.layers_way()
            strain = self.strain[rows]
            across = np.einsum("i,nij,j->n", way, strain, way)
            values, vectors = np.linalg.eigh(strain)
            pick = np.abs(values).argmax(axis=1)
            stretch = np.abs(values).max(axis=1)
            worst, most = int(across.argmax()), int(stretch.argmax())
            along = vectors[most][:, pick[most]]
            found[part.name] = {
                "material": part.material.name,
                "layers": _list(way),
                "across_layers_pct": 100 * max(float(across[worst]), 0.0),
                "across_at": _list(self.points[rows[worst]], 3),
                "across_limit_pct": part.material.across_pct,
                "stretch_pct": 100 * float(stretch[most]),
                "stretch_is": "pull" if values[most][pick[most]] > 0 else "push",
                "stretch_at": _list(self.points[rows[most]], 3),
                "stretch_from_layers_deg": math.degrees(
                    math.asin(min(1.0, abs(float(along @ way))))
                ),
                "stretch_limit_pct": part.material.along_pct,
                "nodes": len(rows),
            }
        return found

    def joint_read(self, joint: Joint) -> dict[str, Any]:
        """What a joint carries, as the force and the moment the second part puts on the first:
        the nodes' own balance on the faces that meet (`element_forces`)."""
        import FreeCAD as App

        names = [p.name for p in self.assembly.parts]
        first = self.solids[names.index(joint.between[0])]
        pieces = []
        of_face = []
        for index, face in enumerate(self.joint_faces[joint.name]):
            triangles = self._triangles(face)
            if not triangles:
                continue
            for nodes, area, centre in triangles:
                a, b, c = (self.nodes[n] for n in nodes[:3])
                normal = np.cross(b - a, c - a)
                normal /= np.linalg.norm(normal)
                # Out of the first part, into the second.
                inside = centre - 1e-3 * normal
                if not first.isInside(App.Vector(*inside), 1e-6, False):
                    normal = -normal
                pieces.append((nodes, area, centre, normal))
                of_face.append(index)
        if not pieces:
            raise NoAnswer(
                f"the mesh has no triangle on the joint {joint.name}: give a finer --mesh"
            )
        total = sum(p[1] for p in pieces)
        centre = np.sum([p[1] * p[2] for p in pieces], axis=0) / total
        mean = np.sum([p[1] * p[3] for p in pieces], axis=0)
        flat = float(np.linalg.norm(mean)) > (1 - 1e-6) * total
        mean = mean / np.linalg.norm(mean) if np.linalg.norm(mean) > 1e-9 else np.zeros(3)
        # By the surface's stress, roughly: how much of the faces is pulled, and how much pushed.
        pull, push = 0.0, 0.0
        for nodes, area, _, normal in pieces:
            for node in nodes[3:]:
                square = float(normal @ self.stress[self.row[node]] @ normal) * area / 3
                pull, push = pull + max(square, 0.0), push + max(-square, 0.0)
        # What the second part puts on the first: at each node of the faces that meet, the
        # force the first part's own elements carry, less what's put on it there from outside.
        rows = sorted(
            {self.row[n] for f in self.joint_faces[joint.name] for n in self.fem.getNodesByFace(f)}
        )
        name = joint.between[0]
        carried = self.within[name][rows] - self.outside[name][rows]
        force = carried.sum(axis=0)
        moment = np.cross(self.points[rows] - centre, carried).sum(axis=0)
        outline = np.array(
            [
                self.nodes[n]
                for f in self.joint_faces[joint.name]
                for n in self.fem.getNodesByFace(f)
            ]
        )
        return {
            "kind": joint.kind,
            "at": centre,
            "normal": mean,
            "flat": flat,
            "area_mm2": total,
            "force": force,
            "moment": moment,
            "pulled_n": pull,
            "pushed_n": push,
            "outline": outline,
            "faces": list(self.joint_faces[joint.name]),
            # Each triangle of the faces: its centre, its way out of the first part, its area
            # and which face it's on.
            "pieces": (
                np.array([p[2] for p in pieces]),
                np.array([p[3] for p in pieces]),
                np.array([p[1] for p in pieces]),
                np.array(of_face),
            ),
        }

    def loads_read(self) -> list[dict[str, Any]]:
        found = []
        for load in self.loads:
            moved = self.moved[[self.row[n] for n in load["nodes"] if n in self.row]].mean(axis=0)
            # A part's own weight says its volume and its mass too.
            own: dict[str, Any] = {k: load[k] for k in ("volume_mm3", "grams") if k in load}
            found.append(
                {
                    "at": _list(load["at"], 3),
                    "part": load["part"],
                    "force_n": _list(load["force"]),
                    "patch_mm2": load["patch_mm2"],
                    "middle": _list(load["middle"], 3),
                    "moved": _list(moved),
                    "moves_mm": float(moved @ self.toward),
                    **own,
                }
            )
        return found


def _reaction(path: str) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """The held nodes' force as CalculiX printed it: the total, and each node's."""
    try:
        text = Path(path).read_text()
    except OSError as exc:
        raise NoAnswer("CalculiX wrote no table of the held faces' reaction") from exc
    total = re.search(r"total force.*?HELDALL.*?\n\s*\n\s*(\S+)\s+(\S+)\s+(\S+)", text, re.S)
    each = re.search(r"\n\s*forces \(fx,fy,fz\).*?HELDALL.*?\n\s*\n(.*?)\n\s*\n", text, re.S)
    if not total or not each:
        raise NoAnswer("CalculiX's table has no reaction for the held faces")
    nodes = {}
    for line in each.group(1).splitlines():
        words = line.split()
        nodes[int(words[0])] = np.array([float(v) for v in words[1:4]])
    return np.array([float(v) for v in total.groups()]), nodes


def _mesh_size(placed: list[Any], given: float | None) -> float:
    if given is not None:
        return float(given)
    thin = min(min(s.BoundBox.XLength, s.BoundBox.YLength, s.BoundBox.ZLength) for s in placed)
    volume = sum(s.Volume for s in placed)
    # About 8.5 tetrahedra fill a cube of their own edge.
    budget = (8.5 * volume / ELEMENTS) ** (1 / 3)
    return float(round(max(MESH_LEAST, budget, thin / ACROSS), 3))


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """The workbench prints as it goes, and a reply's first line is its verdict."""
    said_before = os.dup(1)
    sys.stdout.flush()
    with open(os.devnull, "w") as nowhere:
        os.dup2(nowhere.fileno(), 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        os.dup2(said_before, 1)
        os.close(said_before)


def _solved(assembly: Assembly, placed: list[Any], args: Any, size: float) -> dict[str, Any]:
    """One solve at one mesh, and everything read from it."""
    import FreeCAD as App

    # One thread, so the same parts are the same mesh, and the same figures, every time.
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    doc = App.newDocument("Load")
    model = _Model(assembly, placed, args, size)
    with _quiet():
        return _read_solved(assembly, model, doc, size)


def _read_solved(assembly: Assembly, model: _Model, doc: Any, size: float) -> dict[str, Any]:
    import FreeCAD as App

    try:
        model.join(doc)
        model.hold()
        model.solve(doc)
        joints = {j.name: model.joint_read(j) for j in assembly.joints}
        far = int(np.linalg.norm(model.moved, axis=1).argmax())
        return {
            "mesh_mm": size,
            "parts": model.parts_read(),
            "joints": joints,
            "loads": model.loads_read(),
            "raw_loads": model.loads,
            "held_parts": model.held_parts,
            "held": model.held_said,
            "reaction": model.reaction,
            "toward": model.toward,
            "most_moves_mm": float(np.linalg.norm(model.moved[far])),
            "most_moves_at": _list(model.points[far], 3),
            "nodes": model.fem.NodeCount,
            "elements": model.fem.VolumeCount,
            "mesh_s": model.mesh_s,
            "solve_s": model.solve_s,
        }
    finally:
        App.closeDocument(doc.Name)


def _bolts(joint: Joint, read: dict[str, Any], force: np.ndarray, moment: np.ndarray) -> list[Any]:
    normal = read["normal"]
    bolts = np.array(joint.at, dtype=float)
    # Each bolt where its axis crosses the faces' plane.
    bolts = bolts - np.outer((bolts - read["at"]) @ normal, normal)
    pulls = bolt_pulls(force, moment, read["at"], normal, bolts, read["outline"])
    return [{"at": _list(b, 3), **p} for b, p in zip(bolts, pulls, strict=True)]


def run(args: Any, report: Report) -> None:
    freecad("load")
    args.lift = getattr(args, "lift", None)
    if args.lift is not None and (args.weight is not None or args.at):
        raise NoAnswer(
            "--lift hangs every other part from the one named, by its own weight: it takes no "
            "--weight and no --at. A weight on it is a load case of its own"
        )
    if args.lift is None and (args.weight is None or not args.at):
        raise NoAnswer(
            "load takes --weight KG and --at X,Y,Z, the weight and a place it sits on; or "
            "--lift PART, the assembly lifted by that part with every other part's own weight "
            "hanging from it"
        )
    if args.lift is None and args.weight <= 0:
        raise NoAnswer("--weight is the load in kg, more than 0")
    if args.over <= 0:
        raise NoAnswer("--over is how far round each place the weight is spread, more than 0 mm")
    assembly = read(args.assembly)
    if args.lift is not None and args.lift not in [p.name for p in assembly.parts]:
        raise NoAnswer(
            f"--lift {args.lift}: the parts are {', '.join(p.name for p in assembly.parts)}"
        )
    stiff = {(p.material.modulus_mpa, p.material.poisson) for p in assembly.parts}
    if len(stiff) > 1:
        raise NoAnswer("parts of different stiffness aren't solved together yet: one material")
    placed = _placed(assembly)
    # Each through-bolt's way through both parts, before anything is solved.
    ways = load_through.ways(assembly, placed)
    # And each dovetail's latch as what it catches on.
    catches = {
        j.name: load_dovetail.catch(assembly, j, placed)
        for j in assembly.joints
        if j.kind == "dovetail"
    }
    # And each dovetail taken apart the way it went together: its tail out of its socket past
    # nothing but its latch, and its latch's tab bent as far as it has to be to let it in
    # (mqucifer/print-shop#184 and mqucifer/print-shop#185). A joint that can't be put together
    # fails whatever the load, and nothing is solved.
    # The parts are joined first, as the solve joins them, so what the join refuses, as a face
    # that leans against the way the file says a tail slides in, is said before this.
    if any(j.kind == "dovetail" for j in assembly.joints):
        import FreeCAD as App

        doc = App.newDocument("Joined")
        try:
            with _quiet():
                _Model(assembly, placed, args, 1.0).join(doc)
        finally:
            App.closeDocument(doc.Name)
    ways_in = {
        j.name: way_in.read(assembly, j, placed)
        for j in assembly.joints
        if j.kind == "dovetail"
    }
    if ways_in:
        report.numbers["ways_in"] = {
            name: {k: v for k, v in found.items() if k not in ("fails", "apart")}
            for name, found in ways_in.items()
        }
        for name, found in ways_in.items():
            for text in way_in.said(found, name):
                report.line(text)
            for text in way_in.second(found, name):
                report.second(text)
        apart_in = [t for found in ways_in.values() for t in found["apart"]]
        fails_in = [t for found in ways_in.values() for t in found["fails"]]
        if apart_in:
            report.verdict(
                "DISAGREE", "; and ".join(apart_in) + ": read both, and don't pick one. Nothing "
                "is solved until the way in has one answer"
            )
            return
        if fails_in:
            report.verdict(
                "FAIL", "; and ".join(fails_in) + ". Nothing is solved: a joint that can't be "
                "put together carries nothing"
            )
            return
    size = _mesh_size(placed, args.mesh)
    fine = _solved(assembly, placed, args, size)
    coarse = _solved(assembly, placed, args, round(size * COARSER, 3))
    force = float(sum(np.linalg.norm(one["force"]) for one in fine["raw_loads"]))
    meshed = (
        f"a mesh of at most {size:g} mm ({fine['nodes']} nodes, {fine['elements']} elements, "
        f"meshed in {fine['mesh_s']} s and solved in {fine['solve_s']} s)"
    )
    start = (
        f"the assembly: {len(assembly.parts)} part(s) and {len(assembly.joints)} joint(s) from "
        f"{args.assembly}, "
    )
    if args.lift is not None:
        args.weight = force / G
        dense = sorted(
            {
                f"{p.material.name} at {p.material.density_g_cm3:g} g/cm³, from "
                f"{p.material.density_source}"
                for p in assembly.parts
                if p.name != args.lift
            }
        )
        report.line(
            f"{start}lifted by {args.lift}: {'; '.join(fine['held'])}, and held by nothing "
            f"else, the assembly's own `held` set aside. Every other part hangs by its own "
            f"weight, --toward {args.toward}: its solid volume, {'; '.join(dense)}, "
            f"{1000 * args.weight:.2f} g and {force:.4f} N in all; {meshed}"
        )
        report.numbers["lift"] = args.lift
    else:
        report.line(
            f"{start}{'; '.join(fine['held'])}; {args.weight:g} kg, {force:.2f} N, "
            f"--toward {args.toward}, shared between {len(args.at)} place(s), each over the "
            f"surface within {args.over:g} mm of it; {meshed}"
        )
    off = float(np.linalg.norm(fine["reaction"] + force * fine["toward"]))
    if off > REACTION * force:
        raise NoAnswer(
            f"the held faces' reaction, {_xyz(fine['reaction'])} N as CalculiX sums it, isn't "
            f"the {force:.4g} N put on, by {off:.3f} N: the load didn't go on as asked"
        )
    report.numbers["weight_kg"] = args.weight
    report.numbers["force_n"] = force
    report.numbers["toward"] = args.toward
    report.numbers["mesh_mm"] = size
    report.numbers["coarse_mesh_mm"] = coarse["mesh_mm"]
    report.numbers["nodes"] = fine["nodes"]
    report.numbers["elements"] = fine["elements"]
    report.numbers["mesh_s"] = fine["mesh_s"]
    report.numbers["solve_s"] = fine["solve_s"]
    report.numbers["reaction_n"] = _list(fine["reaction"])
    report.numbers["held"] = fine["held"]
    fails: list[str] = []
    apart: list[str] = []
    _parts(report, fine, coarse, fails, apart)
    _joints(assembly, args, report, fine, coarse, fails, apart, (ways, placed, catches))
    unread = [n for n, j in report.numbers["joints"].items() if j.get("insert_unread")]
    glued = [n for n, j in report.numbers["joints"].items() if j.get("glue_unread")]
    report.numbers["loads"] = fine["loads"]
    for one, other in zip(fine["loads"], coarse["loads"], strict=True):
        one["coarse_moves_mm"] = other["moves_mm"]
        if one["patch_mm2"] is None:
            report.line(
                f"the part {one['part']} hangs by its own weight: {one['volume_mm3']:.1f} mm³ "
                f"solid is {one['grams']:.2f} g, {np.linalg.norm(one['force_n']):.4f} N, at its "
                f"middle, {_xyz(one['middle'])}; it moves {one['moves_mm']:.4f} mm {args.toward}"
            )
            continue
        report.line(
            f"the place at {_xyz(one['at'])}, on {one['part']}: it moves {one['moves_mm']:.3f} mm "
            f"{args.toward} under its {np.linalg.norm(one['force_n']):.2f} N, spread over "
            f"{one['patch_mm2']:.1f} mm² whose middle is {_xyz(one['middle'])}"
        )
    report.numbers["moves_mm"] = moves_most = max(one["moves_mm"] for one in fine["loads"])
    report.numbers["most_moves_mm"] = fine["most_moves_mm"]
    report.numbers["most_moves_at"] = fine["most_moves_at"]
    report.line(
        f"the most any point moves is {fine['most_moves_mm']:.3f} mm, at "
        f"{_xyz(fine['most_moves_at'])}, as most_moves_mm; the loaded place that moves most, "
        f"{moves_most:.3f} mm, is moves_mm. Both are in proportion to one over the "
        f"material's stiffness, {assembly.parts[0].material.modulus_mpa:g} MPa, a solid part's"
    )
    moves = ", ".join(
        f"{o['moves_mm']:.3f} mm at {_xyz(o['at'])} ({_share(o['moves_mm'], f['moves_mm'])} apart)"
        for f, o in zip(fine["loads"], coarse["loads"], strict=True)
    )
    report.second(
        f"the same solve on a mesh of at most {coarse['mesh_mm']:g} mm ({coarse['nodes']} nodes): "
        f"the loaded places move {moves}"
    )
    report.second(
        f"the held faces' reaction, as CalculiX sums it: {_xyz(fine['reaction'])} N, against the "
        f"{force:.4g} N put on --toward {args.toward}, {off:.4f} N apart"
    )
    sources = sorted({p.material.source for p in assembly.parts})
    report.line("the limits are from " + "; ".join(sources))
    if args.lift is not None:
        report.note(
            "each part is read as solid, with one stiffness every way, and hangs at its solid "
            "weight, which is more than a printed part's with its walls and infill: creep, a "
            f"knock, and the weight of anything bought that hangs with them aren't in it, and "
            f"nor is {args.lift}'s own weight, which is in the hand that lifts it"
        )
    else:
        report.note(
            "each part is read as solid, with one stiffness every way, and every joint as one "
            "piece while it holds: walls and infill, creep, a joint that opens and the parts' "
            "own weight aren't in it"
        )
    if apart:
        report.verdict(
            "DISAGREE",
            "; and ".join(apart) + ": read both, and don't pick one. A sharp inside corner has "
            "no one figure in any mesh: round it, or give a finer --mesh",
        )
    elif fails:
        report.verdict("FAIL", "; and ".join(fails))
    elif unread or glued:
        why = []
        if unread:
            inserts = sorted(
                {j.insert.name for j in assembly.joints if j.name in unread and j.insert}
            )
            why.append(
                f"the bolted joint(s) {', '.join(unread)} have a pull to hold, "
                f"{max(report.numbers['joints'][n]['bolt_pull_n'] for n in unread):.1f} N a bolt "
                f"at most, and the insert {', '.join(inserts)} has no pull-out figure: in "
                "printed plastic the insert gives long before the bolt, so the bolt's own "
                "figure doesn't say it holds. Give each such joint its `pull_n`, what each "
                "bolt holds in N, and its `source`, from the request or a pull test"
            )
        if glued:
            most = max(np.linalg.norm(report.numbers["joints"][n]["force_n"]) for n in glued)
            why.append(
                f"the bonded joint(s) {', '.join(glued)} carry load, {most:.2f} N at most, and "
                "glue is a second hold only: no glue line in printed plastic has a figure "
                "here, so nothing says it holds. Join the parts by something that's read, a "
                "`through` bolt or a `dovetail`, and keep glue for its channels"
            )
        report.verdict(
            "UNREAD", "; and ".join(why) + ". Everything else read is within its limit"
        )
    else:
        worst = max(fine["parts"].items(), key=lambda p: p[1]["across_layers_pct"])
        under = (
            f"lifted by {args.lift}, with {1000 * args.weight:.1f} g hanging from it,"
            if args.lift is not None
            else f"under {args.weight:g} kg"
        )
        report.verdict(
            "OK",
            f"{under} every part is within its material's limits and every "
            f"joint holds: the most across any part's layers is "
            f"{worst[1]['across_layers_pct']:.3f}%, in {worst[0]}, and "
            + ("what hangs" if args.lift is not None else "the loaded place")
            + f" moves {moves_most:.3f} mm",
        )


def _share(value: float, against: float) -> str:
    return f"{100 * abs(value - against) / abs(against):.1f}%" if abs(against) > 1e-12 else "n/a"


def _parts(
    report: Report, fine: dict[str, Any], coarse: dict[str, Any], fails: list[str], apart: list[str]
) -> None:
    """Each part against its material: pulled across its layers, and stretched any way."""
    report.numbers["parts"] = fine["parts"]
    for name, one in fine["parts"].items():
        other = coarse["parts"][name]
        one["coarse_across_layers_pct"] = other["across_layers_pct"]
        one["coarse_stretch_pct"] = other["stretch_pct"]
        way = "(" + ", ".join(f"{v:g}" for v in np.round(one["layers"], 3)) + ")"
        lies = one["stretch_from_layers_deg"]
        report.line(
            f"the part {name} ({one['material']}, its layers stacked along {way}): pulled "
            f"across its layers by {one['across_layers_pct']:.3f}% at most, at "
            f"{_xyz(one['across_at'])}, as parts.{name}.across_layers_pct, against "
            f"{one['across_limit_pct']:g}%; its largest stretch any way is a {one['stretch_is']} "
            f"of {one['stretch_pct']:.3f}%, at {_xyz(one['stretch_at'])}, {lies:.0f}° out of "
            f"its layers' plane ({'across them' if lies > 45 else 'along them'}), against "
            f"{one['stretch_limit_pct']:g}%"
        )
        report.second(
            f"the part {name} on the coarser mesh: {other['across_layers_pct']:.3f}% across its "
            f"layers ({_share(other['across_layers_pct'], one['across_layers_pct'])} apart) and "
            f"{other['stretch_pct']:.3f}% any way "
            f"({_share(other['stretch_pct'], one['stretch_pct'])} apart)"
        )
        for key, limit_key, what in (
            ("across_layers_pct", "across_limit_pct", "pulled across its layers"),
            ("stretch_pct", "stretch_limit_pct", "stretched"),
        ):
            value, limit = one[key], one[limit_key]
            where = one["across_at" if key == "across_layers_pct" else "stretch_at"]
            if over(value, limit) != over(other[key], limit):
                apart.append(
                    f"the part {name} is {what} by {said(value, limit)}% on the mesh and "
                    f"{said(other[key], limit)}% on the coarser one, either side of its "
                    f"{limit:g}% limit"
                )
            elif over(value, limit):
                fails.append(
                    f"the part {name} is {what} by {said(value, limit, 2)}% at {_xyz(where)}, "
                    f"over its material's {limit:g}% "
                    + (
                        "across its layers: lay it on the bed so its layers run along this pull, "
                        "or make it thicker there"
                        if key == "across_layers_pct"
                        else "in its layers' plane"
                    )
                )


def _joints(
    assembly: Assembly,
    args: Any,
    report: Report,
    fine: dict[str, Any],
    coarse: dict[str, Any],
    fails: list[str],
    apart: list[str],
    through: tuple[dict[str, list[dict[str, Any]]], list[Any], dict[str, Any]],
) -> None:
    """Each joint's force, and whether what holds it can."""
    force = report.numbers["force_n"]
    saved: dict[str, Any] = {}
    for joint in assembly.joints:
        read, other = fine["joints"][joint.name], coarse["joints"][joint.name]
        normal = read["normal"]
        along = float(read["force"] @ normal)
        shear = float(np.linalg.norm(read["force"] - along * normal))
        bending = read["moment"] - (read["moment"] @ normal) * normal
        one: dict[str, Any] = {
            "kind": joint.kind,
            "at": _list(read["at"], 3),
            "area_mm2": read["area_mm2"],
            "force_n": _list(read["force"]),
            "moment_nmm": _list(read["moment"]),
            "pull_n": along,
            "shear_n": shear,
            "bending_nmm": float(np.linalg.norm(bending)),
            "twist_nmm": float(read["moment"] @ normal),
            "pulled_part_n": read["pulled_n"],
            "pushed_part_n": read["pushed_n"],
            "coarse_force_n": _list(other["force"]),
            "coarse_moment_nmm": _list(other["moment"]),
        }
        saved[joint.name] = one
        report.line(
            f"the joint {joint.name}, {joint.kind}, over {read['area_mm2']:.1f} mm² round "
            f"{_xyz(read['at'])}: it carries {np.linalg.norm(read['force']):.2f} N, "
            f"{abs(along):.2f} N of it a {'pull' if along > 0 else 'push'} square to the faces "
            f"and {shear:.2f} N along them, with a bending moment of "
            f"{one['bending_nmm']:.1f} N·mm and a twist of {abs(one['twist_nmm']):.1f} N·mm, as "
            f"joints.{joint.name}"
        )
        statics = beyond(assembly, joint, fine["raw_loads"], fine["held_parts"], read["at"])
        if statics is not None:
            one["statics_force_n"], one["statics_moment_nmm"] = _list(statics[0]), _list(statics[1])
            gap = float(np.linalg.norm(read["force"] - statics[0]))
            turn = float(np.linalg.norm(read["moment"] - statics[1]))
            report.second(
                f"the joint {joint.name} by statics, the load beyond it: "
                f"{np.linalg.norm(statics[0]):.2f} N and {np.linalg.norm(statics[1]):.1f} N·mm, "
                f"{gap:.2f} N and {turn:.1f} N·mm from the solve's; on the coarser mesh the "
                f"solve gives {np.linalg.norm(other['force']):.2f} N and "
                f"{np.linalg.norm(other['moment']):.1f} N·mm"
            )
        else:
            report.second(
                f"the joint {joint.name} on the coarser mesh: "
                f"{np.linalg.norm(other['force']):.2f} N and "
                f"{np.linalg.norm(other['moment']):.1f} N·mm (statics alone doesn't give it: the "
                "parts are held, or joined, on both sides of it)"
            )
        if joint.kind == "bolted":
            _bolted(joint, report, one, read, other, statics, fails, apart)
        elif joint.kind in ("through", "dovetail"):
            ways, placed, catches = through
            solids = [placed[[p.name for p in assembly.parts].index(n)] for n in joint.between]
            if joint.kind == "dovetail":
                _dovetail(
                    assembly, joint, report, one, read, other, statics, fails, apart,
                    catches[joint.name], solids,
                )  # fmt: skip
                continue
            _through(
                assembly, joint, report, one, read, other, statics, fails, apart,
                ways[joint.name], solids,
            )  # fmt: skip
        elif joint.kind == "touching":
            _touching(joint, args, report, one, read, force, fails)
        elif joint.kind == "bonded":
            # Glue is a second hold only, and no glue line in printed plastic has a sourced
            # figure: a glued joint that carries anything isn't read (the operator's ruling on
            # mqucifer/print-shop#171), as an insert with no figure isn't.
            size = float(np.ptp(read["outline"], axis=0).max()) or 1.0
            carries = max(np.linalg.norm(read["force"]), np.linalg.norm(read["moment"]) / size)
            one["glue_unread"] = bool(carries > NOTHING * force)
            report.line(
                "  it's bonded, and nothing here says what a glue line in printed plastic "
                "holds: "
                + (
                    "what it carries isn't read against anything, as joints."
                    f"{joint.name}.glue_unread"
                    if one["glue_unread"]
                    else "it carries nothing to speak of under this load"
                )
            )
    report.numbers["joints"] = saved


def _bolted(
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
) -> None:
    if not read["flat"]:
        raise NoAnswer(
            f"the bolted joint {joint.name}'s faces aren't in one plane: a bolt's pull is read "
            "square to one flat face. Give each flat face's joint to its own pair of parts"
        )
    holds = joint.holds()
    assert holds is not None and joint.bolt is not None and joint.insert is not None
    limit, whose = holds
    bolts = _bolts(joint, read, read["force"], read["moment"])
    worst = max(b["pull_n"] for b in bolts)
    one.update(bolts=bolts, bolt_pull_n=worst, holds_n=limit, holds_from=whose)
    report.line(
        f"its {len(bolts)} bolt(s), {joint.bolt.name} into {joint.insert.name}: the most one has "
        f"to hold is a pull of {worst:.1f} N, with {bolts[0]['shear_n']:.1f} N across it, as "
        f"joints.{joint.name}.bolt_pull_n, against {limit:.0f} N ({whose})"
    )
    # In printed plastic the insert pulls out long before a steel bolt gives, so a bolt that
    # has anything to hold, with no figure for its insert, isn't read (the operator's ruling on
    # mqucifer/print-shop#160).
    one["insert_unread"] = (
        joint.insert.pull_n is None
        and joint.pull_n is None
        and worst > NOTHING * report.numbers["force_n"]
    )
    ways = {"on the coarser mesh": _bolts(joint, other, other["force"], other["moment"])}
    if statics is not None:
        ways["by statics"] = _bolts(joint, read, *statics)
    for how, theirs in ways.items():
        again = max(b["pull_n"] for b in theirs)
        one[f"bolt_pull_{how.split()[-1]}_n"] = again
        report.second(f"the joint {joint.name}'s worst bolt {how}: a pull of {again:.1f} N")
        if over(again, limit) != over(worst, limit):
            apart.append(
                f"the joint {joint.name}'s worst bolt holds {said(worst, limit, 1)} N by the "
                f"solve and {said(again, limit, 1)} N {how}, either side of its {limit:.0f} N"
            )
    if over(worst, limit):
        fails.append(
            f"the joint {joint.name}'s worst bolt has to hold {said(worst, limit, 1)} N, over "
            f"its {limit:.0f} N ({whose}): more bolts, further from the edge it turns about, "
            "or a joint where the load is lower"
        )


def _through(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
    ways: list[dict[str, Any]],
    solids: list[Any],
) -> None:
    """A bolt right through both parts (mqucifer/print-shop#168): the bolt against its proof
    load, the pressure under each washer or the nut and on each hole's wall, and the plastic
    round each hole, solved again by itself. The joint's answer is the worst of them, named."""
    assert joint.bolt is not None and joint.nut is not None and joint.washer is not None
    name = joint.name
    if not read["flat"]:
        raise NoAnswer(
            f"the through joint {name}'s faces aren't in one plane: a bolt's pull is read "
            "square to one flat face. Give each flat face's joint to its own pair of parts"
        )
    normal = read["normal"]
    for way in ways:
        if abs(abs(float(way["axis"] @ normal)) - 1) > 1e-6:
            raise NoAnswer(
                f"the through joint {name}'s bolt at {_xyz(way['sides'][0]['seat'])} isn't "
                "square to the faces that meet: its pull is read square to them"
            )
    limit = joint.bolt.pull_n
    whose = f"the bolt {joint.bolt.name}: {joint.bolt.source}"
    bolts = _bolts(joint, read, read["force"], read["moment"])
    count = len(bolts)
    # What the second part's shank puts on the first part's hole, and the other way on its own.
    across = (read["force"] - float(read["force"] @ normal) * normal) / count
    shank = DIAMETER[joint.bolt.thread]
    parts = [assembly.part(n) for n in joint.between]
    force = report.numbers["force_n"]
    #: Every reading as its share of its limit, to name the nearest.
    shares: list[tuple[float, str]] = []
    worst = max(b["pull_n"] for b in bolts)
    shares.append((worst / limit, f"the bolt, at {worst:.1f} N of its {limit:.0f} N"))
    one.update(bolts=bolts, bolt_pull_n=worst, holds_n=limit, holds_from=whose)
    seat = "in a pocket, at a play of " + f"{joint.pocket:g} mm" if joint.pocket is not None else ""
    report.line(
        f"its {count} through-bolt(s), {joint.bolt.name} with the nut {joint.nut.name}"
        + (f" {seat}" if seat else "")
        + f" and the washer {joint.washer.name}: the most one has to hold is a pull of "
        f"{worst:.1f} N, with {bolts[0]['shear_n']:.1f} N across it, as "
        f"joints.{name}.bolt_pull_n, against {limit:.0f} N ({whose})"
    )
    if over(worst, limit):
        fails.append(
            f"the joint {name}'s worst bolt has to hold {said(worst, limit, 1)} N, over its "
            f"{limit:.0f} N ({whose}): more bolts, further from the edge it turns about, or a "
            "larger bolt"
        )
    again = {"on the coarser mesh": _bolts(joint, other, other["force"], other["moment"])}
    if statics is not None:
        again["by statics"] = _bolts(joint, read, *statics)
    for how, theirs in again.items():
        pull = max(b["pull_n"] for b in theirs)
        one[f"bolt_pull_{how.split()[-1]}_n"] = pull
        report.second(f"the joint {name}'s worst bolt {how}: a pull of {pull:.1f} N")
        if over(pull, limit) != over(worst, limit):
            apart.append(
                f"the joint {name}'s worst bolt holds {said(worst, limit, 1)} N by the solve "
                f"and {said(pull, limit, 1)} N {how}, either side of its {limit:.0f} N"
            )
    press_source = ""
    for index, (bolt, way) in enumerate(zip(bolts, ways, strict=True)):
        ends = []
        for k, (side, part, solid) in enumerate(zip(way["sides"], parts, solids, strict=True)):
            layers = part.layers_way()
            where = f"the joint {name}'s bolt at {_xyz(side['seat'])}, in {part.name}"
            press_source = part.material.press_source
            # Under the washer, or the nut.
            most, pressed = part.material.press(way["axis"], layers)
            pressure = bolt["pull_n"] / side["bears_mm2"]
            end: dict[str, Any] = {
                "part": part.name,
                "under": "nut" if side["on_nut"] else "washer",
                "seat": _list(side["seat"], 3),
                "bears_mm2": side["bears_mm2"],
                "pressure_mpa": pressure,
                "pressure_limit_mpa": most,
                "pressed": pressed,
                "hole_mm": side["hole_mm"],
                "hole_length_mm": side["hole_length_mm"],
            }
            shares.append(
                (pressure / most, f"the pressure under {side['under']} on {part.name}, at "
                 f"{pressure:.2f} MPa of {most:g} MPa")
            )  # fmt: skip
            report.line(
                f"  {side['under']} bears on {side['bears_mm2']:.2f} mm² of {part.name} round a "
                f"hole {side['hole_mm']:.2f} mm across and {side['hole_length_mm']:.2f} mm long: "
                f"{pressure:.3f} MPa under the bolt's {bolt['pull_n']:.1f} N, pressed {pressed}, "
                f"against {most:g} MPa, as joints.{name}.bolts.{index}.ends.{k}.pressure_mpa"
            )
            if not side["seated"]:
                fails.append(
                    f"{where}: {side['under']} sits on {side['bears_mm2']:.1f} mm² of the part, "
                    f"of its ring's {side['ring_mm2']:.1f} mm²: it overhangs an edge or a "
                    "step. Move the bolt in, or make its seat flat and wide enough"
                )
            if over(pressure, most):
                fails.append(
                    f"{where}: the plastic under {side['under']} is pressed by "
                    f"{said(pressure, most, 2)} MPa, {pressed}, over its {most:g} MPa: a "
                    "wider washer, more bolts, or a larger bolt"
                )
            for how, theirs in again.items():
                second = theirs[index]["pull_n"] / side["bears_mm2"]
                if over(second, most) != over(pressure, most):
                    apart.append(
                        f"{where}: the pressure under {side['under']} is "
                        f"{said(pressure, most, 2)} MPa by the solve and "
                        f"{said(second, most, 2)} MPa {how}, either side of its {most:g} MPa"
                    )
            # Where the shank leans on the hole's wall.
            push = across if k == 0 else -across
            leans = float(np.linalg.norm(push))
            if leans > NOTHING * force:
                wall_most, wall_pressed = part.material.press(push / leans, layers)
                wall = leans / (shank * side["hole_length_mm"])
                end.update(
                    wall_pressure_mpa=wall, wall_limit_mpa=wall_most, wall_pressed=wall_pressed
                )
                shares.append(
                    (wall / wall_most, f"the shank on the hole's wall in {part.name}, at "
                     f"{wall:.2f} MPa of {wall_most:g} MPa")
                )  # fmt: skip
                report.line(
                    f"  the shank leans on {part.name}'s hole with {leans:.1f} N over its "
                    f"{shank:g} mm by {side['hole_length_mm']:.2f} mm: {wall:.3f} MPa, pressed "
                    f"{wall_pressed}, against {wall_most:g} MPa"
                )
                if over(wall, wall_most):
                    fails.append(
                        f"{where}: the shank presses the hole's wall by "
                        f"{said(wall, wall_most, 2)} MPa, {wall_pressed}, over its "
                        f"{wall_most:g} MPa: a thicker part there, more bolts, or a larger bolt"
                    )
            # The plastic round the hole, solved by itself under this bolt.
            if bolt["pull_n"] > NOTHING * force or leans > NOTHING * force:
                with _quiet():
                    solves = [
                        load_through.round_bolt(
                            solid, part, side, way["axis"], bolt["pull_n"], push, coarser
                        )
                        for coarser in (1.0, COARSER)
                    ]  # fmt: skip
                fine, rough = solves
                end["round"] = {**fine, "coarse": rough}
                report.line(
                    f"  the plastic round it in {part.name}, cut off {fine['held_mm']:.1f} mm "
                    f"from the bolt's axis and held there, under the bolt's pull and shear "
                    f"({fine['elements']} elements of at most {fine['mesh_mm']:g} mm): pulled "
                    f"across its layers by {fine['across_layers_pct']:.3f}% at most, at "
                    f"{_xyz(fine['across_at'])}, against {part.material.across_pct:g}%; its "
                    f"largest stretch any way is a {fine['stretch_is']} of "
                    f"{fine['stretch_pct']:.3f}%, at {_xyz(fine['stretch_at'])}, against "
                    f"{part.material.along_pct:g}%"
                )
                report.second(
                    f"the plastic round {where} on a mesh of at most {rough['mesh_mm']:g} mm: "
                    f"{rough['across_layers_pct']:.3f}% across its layers "
                    f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) "
                    f"and {rough['stretch_pct']:.3f}% any way "
                    f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
                )
                for key, most_pct, what, at in (
                    ("across_layers_pct", part.material.across_pct, "pulled across its layers",
                     "across_at"),
                    ("stretch_pct", part.material.along_pct, "stretched", "stretch_at"),
                ):  # fmt: skip
                    value = fine[key]
                    shares.append(
                        (value / most_pct, f"the plastic round the hole in {part.name}, {what} "
                         f"by {value:.3f}% of {most_pct:g}%")
                    )  # fmt: skip
                    if over(value, most_pct) != over(rough[key], most_pct):
                        apart.append(
                            f"{where}: the plastic round the hole is {what} by "
                            f"{said(value, most_pct)}% on the mesh and "
                            f"{said(rough[key], most_pct)}% on the coarser one, either side of "
                            f"its {most_pct:g}% limit"
                        )
                    elif over(value, most_pct):
                        fails.append(
                            f"{where}: the plastic round the hole is {what} by "
                            f"{said(value, most_pct, 2)}% at {_xyz(fine[at])}, over its "
                            f"material's {most_pct:g}%: it tears out there. Move the bolt "
                            "further from the edge, make the part thicker round it, or lay it "
                            "so its layers run along this pull"
                        )
            ends.append(end)
        bolt["ends"] = ends
    share, nearest = max(shares)
    one.update(gives_first=nearest, nearest_share=share)
    report.line(
        f"  of the bolt, the washers, the nut and the plastic, the nearest its limit is "
        f"{nearest}: {100 * share:.1f}% of it, as joints.{name}.nearest_share"
    )
    if press_source:
        report.line(f"  what the plastic takes pressed is from {press_source}")
    report.note(
        f"the through joint {name} is read with its bolts snug and no more: a bolt tightened "
        "hard presses its washers on the plastic by its own preload, which isn't in this, and "
        "PETG creeps under it. The pressure is taken as even under each washer"
    )


#: A dovetail's faces said one by one, the most pushed first: the rest are counted.
FACES_SAID = 6


def _pressed(parts: list[PartAt], way: np.ndarray) -> tuple[float, str, str]:
    """The least pressure two parts that meet take pushed along `way`: the figure, which way
    that is to that part's layers, and the part."""
    found = [(*part.material.press(way, part.layers_way()), part.name) for part in parts]
    return min(found)


def _dovetail(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    other: dict[str, Any],
    statics: tuple[np.ndarray, np.ndarray] | None,
    fails: list[str],
    apart: list[str],
    caught: dict[str, Any] | None,
    solids: list[Any],
) -> None:
    """A tapered dovetail (mqucifer/print-shop#170): which way the load drives its tail, its
    flanks and its seat as pushes alone, its latch against what's left to hold, and the tail's
    neck and the socket's walls solved again by themselves. The joint's answer is the worst of
    them, named. Glue is counted nowhere."""
    name = joint.name
    tail, socket = joint.between
    parts = [assembly.part(n) for n in joint.between]
    slides = joint.slides_way()
    total = report.numbers["force_n"]
    assert joint.slides is not None
    out = ("-" if joint.slides[0] == "+" else "+") + joint.slides[1]

    def driven(force: np.ndarray) -> float:
        """How hard the load drives the tail along its way in: what the socket puts on the
        tail is the other way."""
        return -float(force @ slides)

    def bedded(pieces: Any, force: np.ndarray, moment: np.ndarray) -> tuple[Any, bool, Any]:
        """The faces' pushes that balance what the joint carries: by its flanks and its seat
        alone, and with its latch where those leave something over."""
        centres, normals, areas, _ = pieces
        plain = dovetail.bed(centres, normals, areas, force, moment, read["at"])
        if plain["holds"] or caught is None:
            return plain, False, areas
        more = load_dovetail.catch_patches(caught, slides)
        every = (
            np.vstack([centres, more[0]]),
            np.vstack([normals, more[1]]),
            np.concatenate([areas, more[2]]),
        )
        return dovetail.bed(*every, force, moment, read["at"]), True, every[2]

    arms = np.linalg.norm(read["pieces"][0] - read["at"], axis=1).max()
    if max(np.linalg.norm(read["force"]), np.linalg.norm(read["moment"]) / arms) <= (
        NOTHING * total
    ):
        one.update(slides=joint.slides, drive_n=driven(read["force"]), opens=False, latched=False)
        report.line(f"the dovetail joint {name} carries nothing to speak of under this load")
        return
    drive = driven(read["force"])
    leaving = drive < -NOTHING * total
    one.update(slides=joint.slides, drive_n=drive)
    if leaving:
        way = f"out of its seat, {out}, the way it would leave, by {-drive:.2f} N"
    elif drive > NOTHING * total:
        way = f"into its seat by {drive:.2f} N"
    else:
        way = "neither in nor out, to speak of"
    report.line(
        f"its tail, on {tail}, slides {joint.slides} into its socket, in {socket}, toward its "
        f"seat: this load drives it {way}, as joints.{name}.drive_n (more than 0 is into its "
        "seat)"
    )
    seconds = {"on the coarser mesh": (other["pieces"], other["force"], other["moment"])}
    if statics is not None:
        seconds["by statics"] = (read["pieces"], *statics)
    for how, (_, force, _) in seconds.items():
        again = driven(force)
        if (again < -NOTHING * total) != leaving:
            apart.append(
                f"the dovetail joint {name}'s tail is driven {drive:.2f} N toward its seat by "
                f"the solve and {again:.2f} N {how}, either side of leaving it"
            )
    # Its faces, pushing only.
    result, latched, areas = bedded(read["pieces"], read["force"], read["moment"])
    centres, normals, _, of_face = read["pieces"]
    count = len(of_face)
    push = result["pressure"] * areas
    faces: list[dict[str, Any]] = []
    for index in sorted(set(of_face.tolist())):
        mine = of_face == index
        normal = (normals[mine] * areas[:count][mine, None]).sum(axis=0)
        normal = normal / np.linalg.norm(normal)
        limit, pressed, weaker = _pressed(parts, normal)
        faces.append(
            {
                "kind": dovetail.kind(normal, slides),
                "at": _list((centres[mine] * areas[:count][mine, None]).sum(axis=0)
                            / areas[:count][mine].sum(), 3),
                "normal": _list(normal),
                "area_mm2": float(areas[:count][mine].sum()),
                "push_n": float(push[:count][mine].sum()),
                "bears_mm2": float(areas[:count][mine][push[:count][mine] > 0].sum()),
                "pressure_mpa": float(result["pressure"][:count][mine].max()),
                "pressure_limit_mpa": limit,
                "pressed": pressed,
                "pressed_part": weaker,
            }
        )  # fmt: skip
    faces.sort(key=lambda f: -f["push_n"])
    latch_n = float(push[count:].sum()) if latched else 0.0
    one.update(
        faces=faces,
        opens=not result["holds"],
        latched=latched,
        left_n=_list(result["left_n"]),
        left_nmm=_list(result["left_nmm"]),
    )
    pushing = [f for f in faces if f["push_n"] > NOTHING * total]
    report.line(
        f"  its {len(faces)} face(s) only push: {sum(f['kind'] == 'seat' for f in faces)} "
        f"seat(s), square to the slide, and {sum(f['kind'] == 'flank' for f in faces)} "
        f"flank(s), along it. Under this load {len(pushing)} push"
        + (", and the rest have come away or carry nothing" if len(pushing) < len(faces) else "")
        + f", as joints.{name}.faces, the most pushed first"
    )
    for face in pushing[:FACES_SAID]:
        report.line(
            f"    a {face['kind']} at {_xyz(face['at'])}, facing {_xyz(face['normal'])} out of "
            f"{tail}: pushed by {face['push_n']:.2f} N over {face['bears_mm2']:.1f} of its "
            f"{face['area_mm2']:.1f} mm², {face['pressure_mpa']:.3f} MPa at most, against "
            f"{face['pressure_limit_mpa']:g} MPa pressed {face['pressed']} in "
            f"{face['pressed_part']}"
        )
    if len(pushing) > FACES_SAID:
        report.line(f"    and {len(pushing) - FACES_SAID} more, pushed less")
    for how, (pieces, force, moment) in seconds.items():
        again, again_latched, again_areas = bedded(pieces, force, moment)
        held = float((again["pressure"] * again_areas)[len(pieces[3]) :].sum())
        report.second(
            f"the dovetail joint {name}'s faces {how}: "
            + ("they balance what it carries" if again["holds"] else "they leave "
               f"{np.linalg.norm(again['left_n']):.2f} N of it held by nothing")
            + (f", its latch holding {held:.2f} N" if again_latched else "")
        )  # fmt: skip
        if again["holds"] != result["holds"]:
            apart.append(
                f"the dovetail joint {name}'s faces balance what it carries "
                + ("by the solve and not " if result["holds"] else "not by the solve and do ")
                + how
            )
    if not result["holds"]:
        left, turn = np.linalg.norm(result["left_n"]), np.linalg.norm(result["left_nmm"])
        along = float(result["left_n"] @ slides)
        one.update(gives_first="nothing holds what its faces can't push back on")
        if caught is None and leaving:
            fails.append(
                f"the dovetail joint {name}: this load drives the tail out of its seat by "
                f"{-drive:.2f} N, {out}, and it has no latch: only friction or glue would "
                "hold it, and neither is counted. Give it a latch at its seat, or point the "
                "taper so that this load seats it"
            )
        else:
            fails.append(
                f"the dovetail joint {name} opens: its flanks and its seat can only push, and "
                f"pushing they leave {left:.2f} N and {turn:.1f} N·mm of what it carries held "
                "by nothing"
                + (", its latch counted" if caught is not None else "")
                + (
                    f"; {along:.2f} N of that is along the way out, {out}, where a taper's "
                    "flanks squeeze the tail back out as they're pushed, and only a latch "
                    "would hold it: friction and glue aren't counted"
                    if along > NOTHING * total and caught is None
                    else ": a face there would have to pull"
                )
            )
        report.line(
            f"  pushing alone, its faces leave {left:.2f} N, {_xyz(result['left_n'])}, and "
            f"{turn:.1f} N·mm of what the joint carries held by nothing, as "
            f"joints.{name}.left_n: the tail's neck and the socket's walls aren't read under "
            "a load the joint doesn't hold"
        )
        return
    #: Every reading as its share of its limit, to name the nearest.
    shares: list[tuple[float, str]] = []
    for face in pushing:
        most = face["pressure_limit_mpa"]
        where = f"the {face['kind']} at {_xyz(face['at'])}"
        shares.append(
            (face["pressure_mpa"] / most,
             f"the pressure on {where}, at {face['pressure_mpa']:.2f} MPa of {most:g} MPa")
        )  # fmt: skip
        if over(face["pressure_mpa"], most):
            fails.append(
                f"the dovetail joint {name}: {where} is pressed by "
                f"{said(face['pressure_mpa'], most, 2)} MPa, {face['pressed']} in "
                f"{face['pressed_part']}, over its {most:g} MPa: a longer or deeper tail, or a "
                "seat square to the slide to take the weight off the taper"
            )
    press_source = parts[0].material.press_source
    if caught is not None:
        _latch(assembly, joint, report, one, caught, latch_n, solids, shares, fails, apart)
    carried = (read["force"], read["moment"], read["at"])
    for part, solid, role in zip(parts, solids, ("the tail's neck", "the socket's walls"),
                                 strict=True):  # fmt: skip
        with _quiet():
            fine, rough = (
                load_dovetail.around(
                    solid, part, joint, read["faces"], carried, caught if latched else None, by
                )
                for by in (1.0, COARSER)
            )
        key = "neck" if part.name == tail else "walls"
        one[key] = {**fine, "coarse": rough, "part": part.name}
        where = f"{role}, in {part.name}"
        report.line(
            f"  {where}, cut off {fine['held_mm']:.1f} mm from the joint and held there "
            f"({', '.join(fine['cut_on'])}), under the faces' pushes ({fine['elements']} "
            f"elements of at most {fine['mesh_mm']:g} mm): pulled across its layers by "
            f"{fine['across_layers_pct']:.3f}% at most, at {_xyz(fine['across_at'])}, against "
            f"{part.material.across_pct:g}%; its largest stretch any way is a "
            f"{fine['stretch_is']} of {fine['stretch_pct']:.3f}%, at "
            f"{_xyz(fine['stretch_at'])}, against {part.material.along_pct:g}%, as "
            f"joints.{name}.{key}"
        )
        report.second(
            f"the dovetail joint {name}: {where} on a mesh of at most {rough['mesh_mm']:g} mm: "
            f"{rough['across_layers_pct']:.3f}% across its layers "
            f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) and "
            f"{rough['stretch_pct']:.3f}% any way "
            f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
        )
        _strained(f"the dovetail joint {name}: {where}", where, fine, rough, part, shares, fails,
                  apart, "make it wider or deeper there, round its inside corners, or lay the "
                  "part so its layers run along this pull")  # fmt: skip
    share, nearest = max(shares) if shares else (0.0, "nothing: it carries nothing to speak of")
    one.update(gives_first=nearest, nearest_share=share)
    report.line(
        f"  of its faces, its latch, the tail's neck and the socket's walls, the nearest its "
        f"limit is {nearest}: {100 * share:.1f}% of it, as joints.{name}.nearest_share"
    )
    if press_source:
        report.line(f"  what the plastic takes pressed is from {press_source}")
    report.note(
        f"the dovetail joint {name} is read with no glue and no friction: each face pushes "
        "square to itself, in proportion to how far the tail is pressed into it, the tail "
        "taken as stiff, which is a reading of its balance and not a contact solve. It's "
        "read where the file puts it, seated, with no play: a tail drawn loose in its socket "
        "meets it on fewer faces than a printed one does. Its way in, and its latch's tab "
        "bent to let the tail by, are read before anything is solved, as ways_in"
    )


def _strained(
    where: str,
    short: str,
    fine: dict[str, Any],
    rough: dict[str, Any],
    part: PartAt,
    shares: list[tuple[float, str]],
    fails: list[str],
    apart: list[str],
    change: str,
) -> None:
    """A piece of a part solved by itself on two meshes, against its material's limits."""
    for key, most, what, at in (
        ("across_layers_pct", part.material.across_pct, "pulled across its layers", "across_at"),
        ("stretch_pct", part.material.along_pct, "stretched", "stretch_at"),
    ):
        value = fine[key]
        shares.append((value / most, f"{short}, {what} by {value:.3f}% of {most:g}%"))
        if over(value, most) != over(rough[key], most):
            apart.append(
                f"{where} is {what} by {said(value, most)}% on the mesh and "
                f"{said(rough[key], most)}% on the coarser one, either side of its {most:g}% "
                "limit"
            )
        elif over(value, most):
            fails.append(
                f"{where} is {what} by {said(value, most, 2)}% at {_xyz(fine[at])}, over its "
                f"material's {most:g}%: {change}"
            )


def _latch(
    assembly: Assembly,
    joint: Joint,
    report: Report,
    one: dict[str, Any],
    caught: dict[str, Any],
    holds: float,
    solids: list[Any],
    shares: list[tuple[float, str]],
    fails: list[str],
    apart: list[str],
) -> None:
    """A dovetail's latch against what it has to hold: the pressure on its catch, and its tab,
    solved by itself with that pull on its hook."""
    name = joint.name
    parts = [assembly.part(n) for n in joint.between]
    on = assembly.part(caught["on"])
    solid = solids[joint.between.index(caught["on"])]
    saved: dict[str, Any] = {
        "on": caught["on"],
        "catch_mm2": caught["area_mm2"],
        "catch_at": _list(caught["at"], 3),
        "play_mm": caught["gap_mm"],
        "holds_n": holds,
    }
    one["latch"] = saved
    report.line(
        f"  its latch, a tab on {caught['on']}, catches {caught['other']} over "
        f"{caught['area_mm2']:.2f} mm² round {_xyz(caught['at'])}, {caught['gap_mm']:.3f} mm "
        "clear of it along the slide, read from the two parts' own faces"
    )
    if holds <= NOTHING * report.numbers["force_n"]:
        report.line("  under this load its flanks and its seat hold it, and the latch nothing")
        return
    most, pressed, weaker = _pressed(parts, joint.slides_way())
    pressure = holds / caught["area_mm2"]
    saved.update(pressure_mpa=pressure, pressure_limit_mpa=most)
    shares.append(
        (pressure / most, f"the pressure on the latch's catch, at {pressure:.2f} MPa of "
         f"{most:g} MPa")
    )  # fmt: skip
    report.line(
        f"  the latch has {holds:.2f} N to hold, as joints.{name}.latch.holds_n: what draws "
        f"the tail out, and what its flanks add as they're pushed. On its catch that's "
        f"{pressure:.3f} MPa, against {most:g} MPa pressed {pressed} in {weaker}"
    )
    if over(pressure, most):
        fails.append(
            f"the dovetail joint {name}'s latch: its catch is pressed by "
            f"{said(pressure, most, 2)} MPa, {pressed} in {weaker}, over its {most:g} MPa: a "
            "deeper or wider hook"
        )
    with _quiet():
        fine, rough = (load_dovetail.tab(solid, on, joint, caught, holds, by)
                       for by in (1.0, COARSER))  # fmt: skip
    saved["tab"] = {**fine, "coarse": rough}
    report.line(
        f"  its tab, {fine['thickness_mm']:.2f} mm thick at its root, held there with that "
        f"pull on its hook ({fine['elements']} elements of at most {fine['mesh_mm']:g} mm): "
        f"pulled across its layers by {fine['across_layers_pct']:.3f}% at most, at "
        f"{_xyz(fine['across_at'])}, against {on.material.across_pct:g}%; its largest stretch "
        f"any way is a {fine['stretch_is']} of {fine['stretch_pct']:.3f}%, at "
        f"{_xyz(fine['stretch_at'])}, against {on.material.along_pct:g}%, as "
        f"joints.{name}.latch.tab"
    )
    report.second(
        f"the dovetail joint {name}'s tab on a mesh of at most {rough['mesh_mm']:g} mm: "
        f"{rough['across_layers_pct']:.3f}% across its layers "
        f"({_share(rough['across_layers_pct'], fine['across_layers_pct'])} apart) and "
        f"{rough['stretch_pct']:.3f}% any way "
        f"({_share(rough['stretch_pct'], fine['stretch_pct'])} apart)"
    )
    _strained(f"the dovetail joint {name}'s tab", "the latch's tab", fine, rough, on, shares,
              fails, apart, "a thicker or wider tab, a hook nearer its line, or the part laid "
              "so the tab's length runs along its layers")  # fmt: skip


def _touching(
    joint: Joint,
    args: Any,
    report: Report,
    one: dict[str, Any],
    read: dict[str, Any],
    force: float,
    fails: list[str],
) -> None:
    import FreeCAD as App
    import Part

    if not read["flat"]:
        report.note(
            f"the touching joint {joint.name}'s faces aren't in one plane, so it isn't judged: "
            f"the solve pulls on {read['pulled_n']:.2f} N of it and pushes on "
            f"{read['pushed_n']:.2f} N, and faces that only touch can't pull"
        )
        return
    along, shear = one["pull_n"], one["shear_n"]
    if abs(along) <= NOTHING * force and shear <= NOTHING * force:
        report.line(f"the joint {joint.name} carries nothing to speak of")
        return
    if along > NOTHING * force:
        fails.append(
            f"the touching joint {joint.name} is pulled apart by {along:.2f} N, and nothing "
            "holds it: bond it, bolt it, or put it where the load pushes it together"
        )
        return
    if along >= -NOTHING * force:
        fails.append(
            f"the touching joint {joint.name} carries {shear:.2f} N along its faces and "
            f"{one['bending_nmm']:.1f} N·mm of bending with no push to hold it: it slides or "
            "tips, as nothing holds it"
        )
        return
    normal = read["normal"]
    bending = read["moment"] - (read["moment"] @ normal) * normal
    point = read["at"] + np.cross(normal, bending) / along
    gap = min(f.distToShape(Part.Vertex(App.Vector(*point)))[0] for f in read["faces"])
    one["push_at"], one["push_off_faces_mm"] = _list(point, 3), gap
    needs = shear / -along
    one["friction_needed"] = needs
    report.line(
        f"it's pushed together by {-along:.2f} N, through {_xyz(point)}, "
        + ("on the faces that meet" if gap <= ON_FACE else f"{gap:.2f} mm off the faces that meet")
        + f"; to stay put along them it needs a friction of {needs:.3f}, as "
        f"joints.{joint.name}.friction_needed"
        + ("" if args.friction is not None else ", which has no limit unless --friction gives one")
    )
    if gap > ON_FACE:
        fails.append(
            f"the touching joint {joint.name}'s push passes {gap:.2f} mm outside the faces that "
            "meet, so it tips open: nothing there pulls it shut"
        )
    if args.friction is not None and over(needs, args.friction):
        fails.append(
            f"the touching joint {joint.name} needs a friction of {said(needs, args.friction)} to "
            f"stay put, over the {args.friction:g} given: it slides"
        )
