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
`UNREAD`, never `OK` on the bolt alone.

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
first, linear answer, with no contact), shear where an insert bears on its plastic, the parts' own
weight, buckling, and anything that isn't still: a knock, or someone sitting down.
"""

from __future__ import annotations

import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

from .assembly import AXES, Assembly, Held, Joint, read
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
        self.between = [f for faces in self.met.values() for f in faces]

    def _face_name(self, face: Any) -> str:
        for number, other in enumerate(self.shape.Faces, 1):
            if face.isSame(other):
                return f"Face{number}"
        raise NoAnswer("a face of the joined parts couldn't be found again")

    def hold(self) -> None:
        """The faces each held plane names: fixed, or standing on it."""
        held = list(self.assembly.held)
        if not held:
            low = self.shape.BoundBox.ZMin
            held = [Held("z", low, "standing")]
        names = [p.name for p in self.assembly.parts]
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
        owned = self._owned(wired)
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

    def _owned(self, wired: np.ndarray) -> np.ndarray:
        """Which part each element is in, as a row of flags for each part. Elements that share
        a face are in one part, but for a face that's one of a joint's triangles: so each part
        is what's reached from one of its elements without crossing a joint, and which part
        that is, is asked of the solid once, at one element's middle."""
        import FreeCAD as App

        joined = {
            tuple(sorted(self.row[n] for n in nodes[:3]))
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
            middle = App.Vector(*self.points[wired[start, :4]].mean(axis=0))
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

    def _add_to(self, path: str) -> None:
        """The loads, the pins that stop a standing assembly sliding, and the held faces'
        reaction asked for, written into the input FreeCAD made."""
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
        for face in self.joint_faces[joint.name]:
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
        }

    def loads_read(self) -> list[dict[str, Any]]:
        found = []
        for load in self.loads:
            moved = self.moved[[self.row[n] for n in load["nodes"] if n in self.row]].mean(axis=0)
            found.append(
                {
                    "at": _list(load["at"], 3),
                    "part": load["part"],
                    "force_n": _list(load["force"]),
                    "patch_mm2": load["patch_mm2"],
                    "middle": _list(load["middle"], 3),
                    "moved": _list(moved),
                    "moves_mm": float(moved @ self.toward),
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


def _solved(assembly: Assembly, placed: list[Any], args: Any, size: float) -> dict[str, Any]:
    """One solve at one mesh, and everything read from it."""
    import FreeCAD as App

    # One thread, so the same parts are the same mesh, and the same figures, every time.
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    doc = App.newDocument("Load")
    model = _Model(assembly, placed, args, size)
    # The workbench prints as it goes, and a reply's first line is its verdict.
    said_before = os.dup(1)
    sys.stdout.flush()
    with open(os.devnull, "w") as nowhere:
        os.dup2(nowhere.fileno(), 1)
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
        sys.stdout.flush()
        os.dup2(said_before, 1)
        os.close(said_before)
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
    if args.weight <= 0:
        raise NoAnswer("--weight is the load in kg, more than 0")
    if args.over <= 0:
        raise NoAnswer("--over is how far round each place the weight is spread, more than 0 mm")
    assembly = read(args.assembly)
    stiff = {(p.material.modulus_mpa, p.material.poisson) for p in assembly.parts}
    if len(stiff) > 1:
        raise NoAnswer("parts of different stiffness aren't solved together yet: one material")
    placed = _placed(assembly)
    size = _mesh_size(placed, args.mesh)
    fine = _solved(assembly, placed, args, size)
    coarse = _solved(assembly, placed, args, round(size * COARSER, 3))
    force = args.weight * G
    report.line(
        f"the assembly: {len(assembly.parts)} part(s) and {len(assembly.joints)} joint(s) from "
        f"{args.assembly}, {'; '.join(fine['held'])}; {args.weight:g} kg, {force:.2f} N, "
        f"--toward {args.toward}, shared between {len(args.at)} place(s), each over the surface "
        f"within {args.over:g} mm of it; a mesh of at most {size:g} mm ({fine['nodes']} nodes, "
        f"{fine['elements']} elements, meshed in {fine['mesh_s']} s and solved in "
        f"{fine['solve_s']} s)"
    )
    off = float(np.linalg.norm(fine["reaction"] + force * fine["toward"]))
    if off > REACTION * force:
        raise NoAnswer(
            f"the held faces' reaction, {_xyz(fine['reaction'])} N as CalculiX sums it, isn't "
            f"the {force:.2f} N put on, by {off:.3f} N: the load didn't go on as asked"
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
    _joints(assembly, args, report, fine, coarse, fails, apart)
    unread = [n for n, j in report.numbers["joints"].items() if j.get("insert_unread")]
    report.numbers["loads"] = fine["loads"]
    for one, other in zip(fine["loads"], coarse["loads"], strict=True):
        one["coarse_moves_mm"] = other["moves_mm"]
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
        f"{force:.2f} N put on --toward {args.toward}, {off:.4f} N apart"
    )
    sources = sorted({p.material.source for p in assembly.parts})
    report.line("the limits are from " + "; ".join(sources))
    report.note(
        "each part is read as solid, with one stiffness every way, and every joint as one piece "
        "while it holds: walls and infill, creep, a joint that opens and the parts' own weight "
        "aren't in it"
    )
    if apart:
        report.verdict(
            "DISAGREE",
            "; and ".join(apart) + ": read both, and don't pick one. A sharp inside corner has "
            "no one figure in any mesh: round it, or give a finer --mesh",
        )
    elif fails:
        report.verdict("FAIL", "; and ".join(fails))
    elif unread:
        inserts = sorted(
            {j.insert.name for j in assembly.joints if j.name in unread and j.insert is not None}
        )
        report.verdict(
            "UNREAD",
            f"the bolted joint(s) {', '.join(unread)} have a pull to hold, "
            f"{max(report.numbers['joints'][n]['bolt_pull_n'] for n in unread):.1f} N a bolt at "
            f"most, and the insert {', '.join(inserts)} has no pull-out figure: in printed "
            "plastic the insert gives long before the bolt, so the bolt's own figure doesn't say "
            "it holds. Give each such joint its `pull_n`, what each bolt holds in N, and its "
            "`source`, from the request or a pull test. Everything else read is within its limit",
        )
    else:
        worst = max(fine["parts"].items(), key=lambda p: p[1]["across_layers_pct"])
        report.verdict(
            "OK",
            f"under {args.weight:g} kg every part is within its material's limits and every "
            f"joint holds: the most across any part's layers is "
            f"{worst[1]['across_layers_pct']:.3f}%, in {worst[0]}, and the loaded place moves "
            f"{moves_most:.3f} mm",
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
) -> None:
    """Each joint's force, and whether what holds it can."""
    force = args.weight * G
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
        elif joint.kind == "touching":
            _touching(joint, args, report, one, read, force, fails)
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
