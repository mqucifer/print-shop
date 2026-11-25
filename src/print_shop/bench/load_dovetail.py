"""`load`'s reading of a dovetail (mqucifer/print-shop#170), with FreeCAD.

A leg taller than the bed is split and joined by a tapered dovetail, with a printed click latch
at its seat. Glue channels in its flanks are the parts' own shape: nothing here counts glue.

**The whole assembly is solved as it always is,** each joint's faces tied all over, and that
gives what the joint carries: the force and the moment the socket's part puts on the tail's.
A dovetail's faces only push, so that's then put back on them as pushes alone (`dovetail.bed`),
and the plastic is read under exactly those.

**Before anything is solved:** no face of the tail may lean against the way it slides in
(`check`), or it could never have gone in that way; and a latch's catch is found from the two
parts' own faces (`catch`): a flat face of the tab, in its box, that faces the way the tail
would leave, with a flat face of the other part over it within `REACH`, and what the two share
seen along the slide.

**Its play** (`play`, mqucifer/print-shop#188): the flat faces of the two parts that don't touch
and face each other across a fit's play, `PLAY` mm or less, as small pieces, each with how far
it has to close. The latch's own faces, in its box, aren't the tail's.

**The latch's tab** (`tab`): the part inside the latch's box, held at its root, with what the
latch has to hold put on the catch, pulling the hook the way the other part would. **The tail's
neck and the socket's walls** (`around`): each part alone, cut off round the joint and held
where it's cut, with the faces' pushes on it. CalculiX solves each, linear and static, on
second-order tetrahedra, and the strains are read as any part's are.

**Whether it goes together** is `way_in.py`'s, read before all of this (mqucifer/print-shop#184
and mqucifer/print-shop#185): a latch is only credited here once `bend` has read it getting on.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Callable
from typing import Any

import numpy as np

from . import dovetail
from .assembly import AXES, Assembly, Joint, Latch, PartAt
from .cae import ccx_one_thread, tidy_mesh, tidy_solve
from .report import NoAnswer

#: A latch's ledge is over its hook within this, in mm, along the slide: the slide check's own
#: `--hold`, past which a part isn't held.
REACH = 1.0
#: Flat faces of the two parts that face each other this close, in mm, and no closer than `ON`,
#: are read as able to close on each other under the load (mqucifer/print-shop#188). It's a
#: judgement, not a sourced figure: twice `REACH`, the slide check's `--hold`, so a printed slip
#: fit's few tenths and a loose fit's mm are both read. Faces further apart aren't counted, so a
#: load only they could hold is left held by nothing, and the joint opens: `FAIL`, never `OK`.
PLAY = 2 * REACH
#: A face with play is read in pieces no wider than this, in mm, and no more than `PIECES` of
#: them a face, so where it closes along an edge is seen.
PIECE, PIECES = 0.5, 4000
#: Two directions are the same, or square, within this.
SQUARE = 1e-6
#: A point is on a face, or a face in a plane, within this, in mm.
ON = 1e-6
#: A part round a dovetail is cut off this many of the joint's widths across the slide from
#: the joint's own box, and read further than `1 - READ` of that from the cut.
CUT = 1.0
READ = 0.75
#: The cut is drawn in by this, as often as this, where the part is too small for it to cut.
SMALLER, TRIES = 0.75, 3
#: The cut is no nearer a latch's own box than this, in mm, so its tab and its ledge are in it.
ROUND_LATCH = 3.0
#: The mesh round a dovetail: this many elements across the least of the joint's box, no finer
#: than `LEAST` mm, and no finer than leaves about `ELEMENTS` of them.
ACROSS = 2.5
LEAST = 0.5
ELEMENTS = 15_000
#: A tab's mesh: this many elements through its root's thickness, between these, in mm.
TAB_ACROSS = 2.0
TAB_MESH = (0.2, 2.0)

Triangle = tuple[tuple[int, ...], float, np.ndarray, np.ndarray]


def _xyz(point: Any) -> str:
    return (
        "(" + ", ".join(f"{c + 0.0:g}" for c in np.round(np.asarray(point, dtype=float), 2)) + ")"
    )


def _span(box: Any) -> tuple[np.ndarray, np.ndarray]:
    return (
        np.array([box.XMin, box.YMin, box.ZMin]),
        np.array([box.XMax, box.YMax, box.ZMax]),
    )


def _flat(face: Any, axis: int, at: float) -> bool:
    low, high = _span(face.BoundBox)
    return bool(abs(low[axis] - at) < ON and abs(high[axis] - at) < ON)


def _matrix(latch: Latch) -> Any:
    """FreeCAD's matrix that takes a latch's own axes to the assembly's."""
    import FreeCAD as App

    turn = np.asarray(latch.turn)
    return App.Matrix(
        *[v for row in range(3) for v in (*turn[row].tolist(), float(latch.shift[row]))],
        0.0, 0.0, 0.0, 1.0,
    )  # fmt: skip


def box(arm: Any) -> Any:
    """An axis-aligned box, two corners, as a solid."""
    import FreeCAD as App
    import Part

    low, high = (np.array(c, dtype=float) for c in arm)
    return Part.makeBox(*(high - low), App.Vector(*low))


def in_own_axes(shape: Any, latch: Latch) -> Any:
    """A shape of the assembly in a latch's own axes: itself where the latch isn't placed."""
    if not latch.placed:
        return shape
    moved = shape.copy()
    moved.transformShape(_matrix(latch).inverse())
    return moved


def placed(shape: Any, latch: Latch) -> Any:
    """A shape in a latch's own axes, where the assembly has it."""
    if not latch.placed:
        return shape
    moved = shape.copy()
    moved.transformShape(_matrix(latch))
    return moved


def _facets(face: Any) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """A face as flat pieces: each one's centre, its normal as its corners wind, and its area."""
    points, triangles = face.tessellate(0.05)
    found = []
    for i, j, k in triangles:
        a, b, c = (np.array([points[n].x, points[n].y, points[n].z]) for n in (i, j, k))
        normal = np.cross(b - a, c - a)
        size = float(np.linalg.norm(normal))
        if size > 1e-12:
            found.append(((a + b + c) / 3, normal / size, size / 2))
    return found


def check(joint: Joint, faces: list[Any], tail: Any) -> None:
    """Refuse a dovetail one of whose faces leans against the way its tail slides in: the tail
    could never have gone in that way. The latch's own faces, in its box, aren't the tail's."""
    import FreeCAD as App

    slides = joint.slides_way()
    for face in faces:
        for centre, normal, _ in _facets(face):
            if joint.latch is not None and joint.latch.inside(centre):
                continue
            # Out of the tail's part.
            if not tail.isInside(App.Vector(*(centre - 1e-3 * normal)), 1e-6, False):
                normal = -normal
            if dovetail.kind(normal, slides) == "against":
                first, second = joint.between
                raise NoAnswer(
                    f"the dovetail joint {joint.name}: a face of {first} where it meets "
                    f"{second}, round {_xyz(centre)}, faces {_xyz(normal)}, against the way "
                    f"the file says the tail slides in, {joint.slides}: the tail could never "
                    f"have gone in that way. `slides` is the way the tail, on {first}, goes "
                    f"into its socket, in {second}, toward its seat; the tail's part is named "
                    "first. Where a seat at the tail's other end is meant to be clear, leave "
                    "it clear"
                )


def catch(assembly: Assembly, joint: Joint, placed: list[Any]) -> dict[str, Any] | None:
    """A dovetail's latch as what it catches on, or why it catches nothing: the flat faces of
    its tab that face the way the tail would leave, each with the ledge over it, how far off
    that is, and what the two share seen along the slide."""
    import FreeCAD as App

    latch = joint.latch
    if latch is None:
        return None
    names = [p.name for p in assembly.parts]
    other = next(n for n in joint.between if n != latch.on)
    mine, theirs = placed[names.index(latch.on)], placed[names.index(other)]
    slides = joint.slides_way()
    # The tail leaves against the way it went in. A hook on the tail's part faces that way,
    # and one on the socket's part faces the tail as it comes.
    faces = -slides if latch.on == joint.between[0] else slides
    what = f"the dovetail joint {joint.name}'s latch"
    hooks = [
        f
        for f in mine.Faces
        if type(f.Surface).__name__ == "Plane"
        and float(np.array(f.normalAt(0, 0)) @ faces) > 1 - SQUARE
        and latch.inside(np.array(f.CenterOfMass))
    ]
    if not hooks:
        raise NoAnswer(
            f"{what}: no flat face of {latch.on} inside its `arm`, from "
            f"{_xyz(latch.arm[0])} to {_xyz(latch.arm[1])} {latch.where()}, faces "
            f"{_xyz(faces)}, the way a "
            "hook has to face to stop the tail leaving. The box goes round the tab, hook and all"
        )
    ledges = [
        f
        for f in theirs.Faces
        if type(f.Surface).__name__ == "Plane"
        and float(np.array(f.normalAt(0, 0)) @ faces) < -1 + SQUARE
    ]
    pieces = []
    for hook in hooks:
        level = float(np.array(hook.CenterOfMass) @ faces)
        for ledge in ledges:
            gap = float(np.array(ledge.CenterOfMass) @ faces) - level
            if not -ON <= gap <= REACH:
                continue
            moved = ledge.copy()
            moved.translate(App.Vector(*(-gap * faces)))
            shared = hook.common(moved)
            if shared.Area > 1e-6:
                centre = shared.CenterOfMass
                pieces.append(
                    {
                        "face": shared,
                        "gap": max(gap, 0.0),
                        "area": float(shared.Area),
                        "at": np.array([centre.x, centre.y, centre.z]),
                    }
                )
    if not pieces:
        raise NoAnswer(
            f"{what} catches on nothing: {len(hooks)} flat face(s) of its tab face "
            f"{_xyz(faces)}, and no flat face of {other} lies over one within {REACH:g} mm, "
            "facing it: a latch holds by a hook over a ledge"
        )
    area = sum(p["area"] for p in pieces)
    return {
        "on": latch.on,
        "other": other,
        "faces": faces,
        "area_mm2": area,
        "at": sum(p["area"] * p["at"] for p in pieces) / area,
        "gap_mm": max(p["gap"] for p in pieces),
        "pieces": pieces,
    }


def catch_patches(caught: dict[str, Any], slides: np.ndarray) -> tuple[Any, Any, Any, Any]:
    """A catch as pieces of face for `dovetail.bed`: on the tail's part it's a face that looks
    back along the way in, whichever part the tab is on, so the latch pushes the tail home. The
    hook stands clear of its ledge by the catch's own play, and bears once that's closed."""
    facets = [(f, piece["gap"]) for piece in caught["pieces"] for f in _facets(piece["face"])]
    centres = np.array([f[0] for f, _ in facets])
    return (
        centres,
        np.tile(-slides, (len(facets), 1)),
        np.array([f[2] for f, _ in facets]),
        np.array([gap for _, gap in facets]),
    )


def _small(face: Any, size: float) -> tuple[np.ndarray, np.ndarray]:
    """A flat face as small pieces: each one's centre and its area. Each of its own triangles
    is cut into as many smaller ones as leave none longer than `size`."""
    points, triangles = face.tessellate(0.05)
    centres: list[np.ndarray] = []
    areas: list[float] = []
    for i, j, k in triangles:
        a, b, c = (np.array([points[n].x, points[n].y, points[n].z]) for n in (i, j, k))
        area = float(np.linalg.norm(np.cross(b - a, c - a))) / 2
        if area <= 1e-12:
            continue
        longest = max(np.linalg.norm(b - a), np.linalg.norm(c - b), np.linalg.norm(a - c))
        cuts = max(1, math.ceil(longest / size))
        u, v = (b - a) / cuts, (c - a) / cuts
        for m in range(cuts):
            for n in range(cuts - m):
                centres.append(a + (m + 1 / 3) * u + (n + 1 / 3) * v)
                if m + n < cuts - 1:
                    centres.append(a + (m + 2 / 3) * u + (n + 2 / 3) * v)
        areas += [area / cuts**2] * (len(centres) - len(areas))
    return np.array(centres).reshape(-1, 3), np.array(areas)


def play(assembly: Assembly, joint: Joint, placed: list[Any]) -> list[dict[str, Any]]:
    """The faces of a dovetail's two parts that don't touch where the file puts them and face
    each other across a fit's play (mqucifer/print-shop#188): a flat face of the tail's part
    and one of the socket's part square to the same way, facing each other, `ON` to `PLAY` mm
    apart, and what the two share seen along that way. Each is said as the tail's face, the
    socket's face over it, how far apart they are, the way out of the tail, and the tail's
    face in small pieces, less what's in the latch's box: the latch's own faces aren't the
    tail's."""
    import FreeCAD as App

    names = [p.name for p in assembly.parts]
    tail, socket = (placed[names.index(n)] for n in joint.between)

    def flats(solid: Any) -> list[tuple[Any, np.ndarray]]:
        found = []
        for face in solid.Faces:
            if type(face.Surface).__name__ != "Plane":
                continue
            # A point that's on the face, as a ring's middle isn't, and the way out of its part.
            pieces = _facets(face)
            if not pieces:
                continue
            centre, normal, _ = max(pieces, key=lambda f: f[2])
            if solid.isInside(App.Vector(*(centre + 1e-3 * normal)), 1e-6, False):
                normal = -normal
            found.append((face, normal))
        return found

    theirs = flats(socket)
    patches = []
    for mine, way in flats(tail):
        level = float(np.array(mine.CenterOfMass) @ way)
        for other, facing in theirs:
            if float(way @ facing) > -1 + SQUARE:
                continue
            gap = float(np.array(other.CenterOfMass) @ way) - level
            if not ON < gap <= PLAY:
                continue
            moved = other.copy()
            moved.translate(App.Vector(*(-gap * way)))
            for shared in mine.common(moved).Faces:
                if shared.Area <= 1e-6:
                    continue
                size = max(PIECE, math.sqrt(shared.Area / PIECES))
                centres, areas = _small(shared, size)
                if joint.latch is not None:
                    keep = np.array([not joint.latch.inside(c) for c in centres], bool)
                    centres, areas = centres[keep], areas[keep]
                if not len(areas):
                    continue
                across = shared.copy()
                across.translate(App.Vector(*(gap * way)))
                patches.append(
                    {
                        "tail_face": shared,
                        "socket_face": across,
                        "gap": gap,
                        "normal": way,
                        "centres": centres,
                        "areas": areas,
                    }
                )
    return patches


def _solve(
    shape: Any,
    held: Callable[[Any], bool],
    part: PartAt,
    size: float,
    loads: Callable[[Any, Callable[[Any], list[Triangle]]], dict[int, np.ndarray]],
    what: str,
) -> dict[str, Any]:
    """A piece of a part, held on the faces `held` picks, under the force `loads` puts on each
    node of its mesh, by gmsh and CalculiX through FreeCAD's FEM workbench: each node's place
    and strain."""
    import FreeCAD as App
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    doc = App.newDocument("Dovetail")
    try:
        feature = doc.addObject("Part::Feature", "Piece")
        feature.Shape = shape
        shape = feature.Shape
        fixed_faces = [f"Face{n}" for n, face in enumerate(shape.Faces, 1) if held(face)]
        if not fixed_faces:
            raise NoAnswer(f"{what} has no face to be held by")
        analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
        solver.AnalysisType = "static"
        solver.GeometricalNonlinearity = "linear"
        analysis.addObject(solver)
        material = ObjectsFem.makeMaterialSolid(doc, "Material")
        values = material.Material
        values.update(
            Name="Printed",
            YoungsModulus=f"{part.material.modulus_mpa:g} MPa",
            PoissonRatio=f"{part.material.poisson:g}",
            Density=f"{1000 * part.material.density_g_cm3:g} kg/m^3",
        )
        material.Material = values
        analysis.addObject(material)
        fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        fixed.References = [(feature, tuple(fixed_faces))]
        analysis.addObject(fixed)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = feature
        mesh.CharacteristicLengthMax = size
        mesh.ElementOrder = "2nd"
        mesh.HighOrderOptimize = "Optimization"
        analysis.addObject(mesh)
        doc.recompute()
        tools = GmshTools(mesh)
        wrong = tools.create_mesh()
        tidy_mesh(tools)
        fem = mesh.FemMesh
        if wrong or not fem.VolumeCount:
            raise NoAnswer(f"gmsh made no mesh of {what}: {str(wrong)[:300]}")
        nodes = {n: np.array([p.x, p.y, p.z]) for n, p in fem.Nodes.items()}

        def triangles(face: Any) -> list[Triangle]:
            found = []
            for element in fem.getFacesByFace(face):
                wired = fem.getElementNodes(element)
                a, b, c = (nodes[n] for n in wired[:3])
                normal = np.cross(b - a, c - a)
                length = float(np.linalg.norm(normal))
                if length > 1e-12:
                    found.append((wired, length / 2, (a + b + c) / 3, normal / length))
            return found

        pushes = loads(shape, triangles)
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir(tempfile.mkdtemp(prefix="dovetail-"))
        fea.setup_ccx()
        wrong = fea.check_prerequisites()
        if wrong:
            raise NoAnswer(f"the solve of {what} couldn't be set up: {wrong[:300]}")
        fea.write_inp_file()
        with open(fea.inp_file_name) as file:
            text = file.read()
        outputs = text.find("** Outputs")
        if outputs < 0:
            raise NoAnswer("FreeCAD's CalculiX input has no step to put the joint's load in")
        outputs = text.rfind("\n***", 0, outputs) + 1
        step = "*CLOAD\n" + "".join(
            f"{node},{k + 1},{push[k]:.9g}\n"
            for node, push in sorted(pushes.items())
            for k in range(3)
            if abs(push[k]) > 0
        )
        with open(fea.inp_file_name, "w") as file:
            file.write(text[:outputs] + step + text[outputs:])
        ccx_one_thread(fea)
        fea.load_results()
        tidy_solve(fea)
        results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
        if not results or not results[0].NodeStrainXX:
            raise NoAnswer(f"CalculiX gave no result for {what}, at a mesh of {size:g} mm")
        result = results[0]
        strain = np.zeros((len(result.NodeNumbers), 3, 3))
        for (i, j), name in {
            (0, 0): "XX", (1, 1): "YY", (2, 2): "ZZ", (0, 1): "XY", (0, 2): "XZ", (1, 2): "YZ"
        }.items():  # fmt: skip
            strain[:, i, j] = strain[:, j, i] = getattr(result, f"NodeStrain{name}")
        return {
            "points": np.array([nodes[n] for n in result.NodeNumbers]),
            "strain": strain,
            "elements": fem.VolumeCount,
        }
    finally:
        App.closeDocument(doc.Name)


def _spread(pushes: dict[int, np.ndarray], weighted: list[tuple[Any, float]], force: Any) -> None:
    """A force shared between triangles by their weights: a uniform push on a second-order
    triangle is a third on each mid-side node."""
    total = sum(w for _, w in weighted)
    for wired, weight in weighted:
        for node in wired[3:]:
            pushes[node] = pushes.get(node, 0) + np.asarray(force) * weight / total / 3


def _strains(solved: dict[str, Any], keep: np.ndarray, part: PartAt, what: str) -> dict[str, Any]:
    """The worst pull across the layers and the largest stretch any way, among the nodes kept."""
    if not keep.any():
        raise NoAnswer(f"the mesh of {what} has no node where it's read: give a finer --mesh")
    strain, points = solved["strain"][keep], solved["points"][keep]
    layers = part.layers_way()
    over_layers = np.einsum("i,nij,j->n", layers, strain, layers)
    each = np.linalg.eigvalsh(strain)
    stretch = np.abs(each).max(axis=1)
    worst, most = int(over_layers.argmax()), int(stretch.argmax())
    return {
        "across_layers_pct": 100 * max(float(over_layers[worst]), 0.0),
        "across_at": [round(float(v), 3) for v in points[worst]],
        "stretch_pct": 100 * float(stretch[most]),
        "stretch_is": "pull" if each[most][np.abs(each[most]).argmax()] > 0 else "push",
        "stretch_at": [round(float(v), 3) for v in points[most]],
        "elements": solved["elements"],
    }


def tab(
    solid: Any, part: PartAt, joint: Joint, caught: dict[str, Any], pull: float, coarser: float
) -> dict[str, Any]:
    """A latch's tab under what it has to hold: the part inside the latch's box, held at its
    root, with `pull`, in N, put on its catch the way the other part's ledge pushes it."""
    import FreeCAD as App
    import Part

    latch = joint.latch
    assert latch is not None
    what = f"the dovetail joint {joint.name}'s latch"
    # Read in the box's own axes, where `arm` and `root` are given (mqucifer/print-shop#295):
    # the assembly's, unless the latch is given in its part's own.
    arms = [s for s in in_own_axes(solid, latch).common(box(latch.arm)).Solids
            if s.Volume > 1e-6]  # fmt: skip
    if len(arms) != 1:
        raise NoAnswer(
            f"{what}: its `arm` holds {len(arms)} separate piece(s) of {part.name}: put the "
            "box round the one tab, from its root to its tip, hook and all, and clear of the "
            "rest of the part"
        )
    axis, at = AXES.index(latch.root[0]), latch.root[1]
    roots = [f for f in arms[0].Faces if _flat(f, axis, at)]
    if not roots:
        span = _span(arms[0].BoundBox)
        raise NoAnswer(
            f"{what}: the tab in its `arm` has no end on its `root`, {latch.root[0]}={at:g} "
            f"{latch.where()}: it runs from {span[0][axis]:g} to {span[1][axis]:g} along "
            f"{latch.root[0]}. One side of the box is on the tab's root, so the box cuts it there"
        )
    sides = [
        float(extent)
        for face in roots
        for k, extent in enumerate(_span(face.BoundBox)[1] - _span(face.BoundBox)[0])
        if k != axis
    ]
    arm = placed(arms[0], latch)

    def rooted(face: Any) -> bool:
        return _flat(in_own_axes(face, latch), axis, at)

    thick = min(sides)
    size = min(TAB_MESH[1], max(TAB_MESH[0], thick / TAB_ACROSS)) * coarser
    faces = caught["faces"]
    # The ledge pushes the hook's face back the way that face looks.
    force = -faces * pull

    def loads(shape: Any, triangles: Callable[[Any], list[Triangle]]) -> dict[int, np.ndarray]:
        on_hook = [
            t
            for face in shape.Faces
            if type(face.Surface).__name__ == "Plane"
            and float(np.array(face.normalAt(0, 0)) @ faces) > 1 - SQUARE
            for t in triangles(face)
        ]
        under = [
            (t[0], t[1])
            for t in on_hook
            if any(
                p["face"].distToShape(Part.Vertex(App.Vector(*t[2])))[0] <= ON
                for p in caught["pieces"]
            )
        ]
        if not under and on_hook:
            # A catch narrower than the mesh: the triangle nearest its middle.
            near = min(on_hook, key=lambda t: float(np.linalg.norm(t[2] - caught["at"])))
            under = [(near[0], near[1])]
        if not under:
            raise NoAnswer(f"the mesh of {what}'s tab has no triangle on its catch")
        pushes: dict[int, np.ndarray] = {}
        _spread(pushes, under, force)
        return pushes

    solved = _solve(arm, rooted, part, size, loads, f"{what}'s tab")
    keep = np.ones(len(solved["points"]), dtype=bool)
    found = _strains(solved, keep, part, f"{what}'s tab")
    found.update(
        mesh_mm=round(size, 3), pull_n=pull, thickness_mm=thick, arm_mm3=float(arm.Volume)
    )
    return found


def _square(part: PartAt) -> np.ndarray | None:
    """A part's turn where it leaves its own axes off the assembly's, as one rotation: none
    where it turns them onto them, as no turn or a quarter turn does."""
    turn = part.matrix()
    if np.all(np.isclose(np.abs(turn), np.round(np.abs(turn)), atol=1e-9)):
        return None
    return turn


def _turned(shape: Any, turn: np.ndarray | None) -> Any:
    """A shape turned by a rotation about the origin, as a copy: itself with none."""
    import FreeCAD as App

    if turn is None:
        return shape
    moved = shape.copy()
    rows = [v for row in range(3) for v in (*turn[row].tolist(), 0.0)]
    moved.transformShape(App.Matrix(*rows, 0.0, 0.0, 0.0, 1.0))
    return moved


def _piece(
    solid: Any, part: PartAt, joint: Joint, met: Any
) -> tuple[Any, list[tuple[int, float]], float, np.ndarray | None]:
    """The part round a dovetail: what's within the cut of the joint's own box, with its
    latch's, the planes it was cut on, each an axis and where, and how far off the cut is.
    A part turned off the assembly's axes, as a leg at a table's corner can be, is cut in
    its own file's axes, turned back about the origin (mqucifer/print-shop#295): a box of the
    assembly's axes round its joint takes in all of it, and cuts it nowhere. The last is that
    turn, or none where the cut is in the assembly's axes."""
    turn = _square(part)
    back = None if turn is None else turn.T
    piece, planes, reach = _cut(_turned(solid, back), part, joint, _turned(met, back), turn)
    return _turned(piece, turn), planes, reach, turn


def _cut(
    solid: Any, part: PartAt, joint: Joint, met: Any, turn: np.ndarray | None
) -> tuple[Any, list[tuple[int, float]], float]:
    import FreeCAD as App
    import Part

    low, high = _span(met.BoundBox)
    slides = np.abs(joint.slides_way() if turn is None else joint.slides_way() @ turn)
    across = float(((high - low) * (1 - slides)).max())
    reach = CUT * across
    middle = Part.Vertex(App.Vector(*((low + high) / 2)))
    for _ in range(TRIES + 1):
        corner, far = low - reach, high + reach
        if joint.latch is not None:
            least, most = joint.latch.bounds()
            if turn is not None:
                corners = joint.latch.corners() @ turn
                least, most = corners.min(axis=0), corners.max(axis=0)
            corner = np.minimum(corner, least - ROUND_LATCH)
            far = np.maximum(far, most + ROUND_LATCH)
        size = far - corner
        cut = solid.common(Part.makeBox(*size, App.Vector(*corner)))
        solids = [s for s in cut.Solids if s.Volume > 1e-6]
        if solids:
            piece = min(solids, key=lambda s: s.distToShape(middle)[0])
            planes = []
            for face in piece.Faces:
                for axis in range(3):
                    for at in (corner[axis], corner[axis] + size[axis]):
                        centre = face.CenterOfMass
                        # A face on the cut's plane that's inside the part is where it was cut.
                        if _flat(face, axis, float(at)) and solid.isInside(centre, 1e-6, False):
                            planes.append((axis, float(at)))
            if planes:
                return piece, sorted(set(planes)), reach
        reach *= SMALLER
    raise NoAnswer(
        f"the part {part.name} is too small round the dovetail joint {joint.name} to be read "
        f"there: cut off {reach / SMALLER:.1f} mm from the joint, none of it is left beyond the "
        "cut to hold it by"
    )


def around(
    solid: Any,
    part: PartAt,
    joint: Joint,
    faces: list[Any],
    carried: tuple[np.ndarray, np.ndarray, np.ndarray],
    caught: dict[str, Any] | None,
    coarser: float = 1.0,
    played: list[dict[str, Any]] | None = None,
    stiff: float | None = None,
) -> dict[str, Any]:
    """The plastic of one part round a dovetail: the tail's neck, or the socket's walls. The
    part alone is cut off round the joint and held where it's cut. `carried` is what the joint
    carries, the force and the moment, about a point, the socket's part puts on the tail's:
    it's put on this part's own faces of the joint as pushes alone, with what the latch holds
    on its catch where `caught` is given. `played` are the faces with play (`play`), each on
    this part's own side of it, which push once the tail has moved that far, by the faces'
    stiffness `stiff` (mqucifer/print-shop#188)."""
    import FreeCAD as App
    import Part

    is_tail = part.name == joint.between[0]
    what = f"the part {part.name} round the dovetail joint {joint.name}"
    played = played or []
    own = [p["tail_face" if is_tail else "socket_face"] for p in played]
    met = Part.Compound(list(faces) + own)
    piece, planes, reach, turn = _piece(solid, part, joint, met)
    low, high = _span(met.BoundBox)
    least = float(min(e for e in high - low if e > 1e-6))
    size = max(LEAST, least / ACROSS, (8.5 * piece.Volume / ELEMENTS) ** (1 / 3)) * coarser
    slides = joint.slides_way()
    force, moment, about = carried
    said: dict[str, Any] = {}

    def loads(shape: Any, triangles: Callable[[Any], list[Triangle]]) -> dict[int, np.ndarray]:
        on_joint: list[Triangle] = []
        gaps: list[float] = []
        for face in shape.Faces:
            if face.distToShape(met)[0] > ON:
                continue
            for wired, area, centre, normal in triangles(face):
                point = Part.Vertex(App.Vector(*centre))
                if met.distToShape(point)[0] > ON:
                    continue
                if joint.latch is not None and joint.latch.inside(centre):
                    continue
                # Out of this piece.
                if not shape.isInside(App.Vector(*(centre - 1e-3 * normal)), 1e-6, False):
                    normal = -normal
                on_joint.append((wired, area, centre, normal))
                # Its play: none on a face that touches.
                gap = next((p["gap"] for p, f in zip(played, own, strict=True)
                            if f.distToShape(point)[0] <= ON), 0.0)  # fmt: skip
                gaps.append(gap)
        if not on_joint:
            raise NoAnswer(f"the mesh of {what} has no triangle on the joint's faces")
        centres = np.array([t[2] for t in on_joint])
        outward = np.array([t[3] for t in on_joint])
        areas = np.array([t[1] for t in on_joint])
        # `dovetail.bed` takes each face's way out of the tail.
        normals = outward if is_tail else -outward
        count = len(on_joint)
        clear = np.array(gaps)
        if caught is not None:
            more = catch_patches(caught, slides)
            centres = np.vstack([centres, more[0]])
            normals = np.vstack([normals, more[1]])
            areas = np.concatenate([areas, more[2]])
            clear = np.concatenate([clear, more[3]])
        bedded = dovetail.bed(centres, normals, areas, force, moment, about, clear, stiff)
        push = bedded["pressure"] * areas
        pushes: dict[int, np.ndarray] = {}
        for (wired, _, _, _), out, newtons in zip(on_joint, outward, push[:count], strict=True):
            if newtons > 0:
                _spread(pushes, [(wired, 1.0)], -newtons * out)
        said.update(holds=bedded["holds"], latch_n=float(push[count:].sum()))
        if caught is not None and said["latch_n"] > 0:
            # The latch pushes the tail home, and the socket's part the other way.
            held_by = (slides if is_tail else -slides) * said["latch_n"]
            mine = part.name == caught["on"]
            on_catch = []
            for face in shape.Faces:
                if type(face.Surface).__name__ != "Plane":
                    continue
                if abs(abs(float(np.array(face.normalAt(0, 0)) @ slides)) - 1) > SQUARE:
                    continue
                for wired, area, centre, _ in triangles(face):
                    for one in caught["pieces"]:
                        # The hook's face, or the ledge over it.
                        at = centre if mine else centre - one["gap"] * caught["faces"]
                        if one["face"].distToShape(Part.Vertex(App.Vector(*at)))[0] <= ON:
                            on_catch.append((wired, area))
                            break
            if not on_catch:
                raise NoAnswer(f"the mesh of {what} has no triangle on the latch's catch")
            _spread(pushes, on_catch, held_by)
        return pushes

    def held(face: Any) -> bool:
        back = _turned(face, None if turn is None else turn.T)
        return any(_flat(back, axis, at) for axis, at in planes)

    solved = _solve(piece, held, part, size, loads, what)
    points = solved["points"]
    # Where it was cut, in the axes it was cut in.
    cut = points if turn is None else points @ turn
    keep = np.ones(len(points), dtype=bool)
    for axis, at in planes:
        keep &= np.abs(cut[:, axis] - at) > (1 - READ) * reach
    if joint.latch is not None:
        latch = joint.latch
        keep &= ~np.array([latch.inside(p, 1e-3) for p in points])
    found = _strains(solved, keep, part, what)
    found.update(
        mesh_mm=round(size, 3),
        held_mm=reach,
        read_mm=READ * reach,
        cut_on=[f"{AXES[axis]}={at:g}" + ("" if turn is None else f" in {part.name}'s own axes")
                for axis, at in planes],  # fmt: skip
        holds=said["holds"],
        latch_n=said["latch_n"],
    )
    return found
