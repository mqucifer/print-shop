"""`load`'s reading of a through-bolt (mqucifer/print-shop#168), with FreeCAD.

A through-bolt passes right through two parts, a washer under its head and a nut on the far
side. It doesn't depend on an insert, so what gives first is the bolt, the plastic crushing
under a washer or the nut, or the plastic round the hole tearing out toward an edge.

**The whole assembly is solved as it always is,** each joint's faces tied all over, as a bolted
joint is while its bolts hold, and that gives what each bolt has to hold: its pull and the
shear across it (`load.bolt_pulls`). The plastic is then read under exactly that.

**The way through** (`ways`), before anything is solved, from each part's own faces (`through.py`):
the round or peaked hole in each part along the bolt's axis, no narrower than the bolt, the two
meeting end to end where the parts meet; and each hole's far end, its seat, with the flat faces the
washer or the nut bears on there. What it bears on is the ring between the hole and the washer's
outside, or the nut's bearing face, cut to the part's own faces, so a washer that overhangs an edge
bears on less. At a peaked bore (mqucifer/print-shop#237) its whole seat is that ring less the
peak's cut, and nothing else.

**Round each bolt** (`round_bolt`), in each of the two parts: the part alone, cut off two diameters
of what bears on it, the washer or the nut's face, from the bolt's axis and held where it's cut,
with the bolt's pull spread over what the washer or the nut bears on, toward the other part, and its
shear put on the half of the hole's wall the shank leans on, most where it leans square: at a peaked
bore, on what of that half is still round. CalculiX solves it, linear and static, on second-order
tetrahedra, and the strains are read as any part's are, within three quarters of the cut: the hold
itself strains what's beside it.
"""

from __future__ import annotations

import math
import tempfile
from typing import Any

import numpy as np

from . import through
from .assembly import DIAMETER, Assembly, Joint, PartAt
from .report import NoAnswer

#: A part round a bolt is cut off this many diameters of what bears on it there, the washer or
#: the nut's face, from the bolt's axis, and read within `READ` of that.
CUT = 2.0
READ = 0.75
#: The cut is drawn in by this, as often as this, where the part is too small for it to cut.
SMALLER, TRIES = 0.75, 3
#: The mesh round a bolt: this many elements round the hole's width and through its length, no
#: finer than `LEAST` mm, and no finer than leaves about `ELEMENTS` of them.
ACROSS_HOLE, THROUGH = 3.5, 4.0
LEAST = 0.5
ELEMENTS = 6_000
#: A washer or a nut sits wholly on the part where this share of its seat does: its ring, less
#: a peaked bore's cut.
SEATED = 0.999
#: A curved edge is this many straight runs in a flat face's outline.
EDGE_RUNS = 32


def _xyz(point: Any) -> str:
    return (
        "(" + ", ".join(f"{c + 0.0:g}" for c in np.round(np.asarray(point, dtype=float), 2)) + ")"
    )


def rows(solid: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A solid's round faces and flat faces, as `through.py` reads them. Each flat row keeps
    its `face` and its `corners`."""
    cylinders, planes = [], []
    for face in solid.Faces:
        kind = type(face.Surface).__name__
        if kind not in ("Cylinder", "Plane"):
            continue
        u0, u1, v0, v1 = face.ParameterRange
        middle = ((u0 + u1) / 2, (v0 + v1) / 2)
        normal = np.array(face.normalAt(*middle))
        corners = np.array([[v.X, v.Y, v.Z] for v in face.Vertexes])
        if kind == "Plane":
            centre = face.CenterOfMass
            planes.append(
                {
                    "normal": normal,
                    "at": np.array([centre.x, centre.y, centre.z]),
                    "corners": corners,
                    "outline": _outline(face),
                    "face": face,
                }
            )
            continue
        axis, origin = np.array(face.Surface.Axis), np.array(face.Surface.Center)
        point = np.array(face.valueAt(*middle))
        radial = point - origin - ((point - origin) @ axis) * axis
        base = float(origin @ axis)
        reach = corners @ axis if len(corners) else np.array([base + v0, base + v1])
        cylinders.append(
            {
                "axis": axis,
                "origin": origin,
                "radius": float(face.Surface.Radius),
                "hole": bool(normal @ radial < 0),
                "low": float(reach.min()),
                "high": float(reach.max()),
                "turns": float(u1 - u0),
                "middle": radial / np.linalg.norm(radial),
                "face": face,
            }
        )
    return cylinders, planes


def _outline(face: Any) -> np.ndarray:
    """A flat face's edges as straight runs, each its two ends: a curved edge as several."""
    runs = []
    for edge in face.Edges:
        if edge.Degenerated:
            continue
        straight = type(edge.Curve).__name__.startswith("Line")
        ends = np.array([[p.x, p.y, p.z] for p in edge.discretize(2 if straight else EDGE_RUNS)])
        runs.append(np.stack([ends[:-1], ends[1:]], axis=1))
    return np.concatenate(runs) if runs else np.zeros((0, 2, 3))


def _ring(centre: np.ndarray, axis: np.ndarray, inner: float, outer: float) -> Any:
    import FreeCAD as App
    import Part

    at, way = App.Vector(*centre), App.Vector(*axis)
    disc = Part.Face(Part.Wire(Part.makeCircle(outer, at, way)))
    return disc.cut(Part.Face(Part.Wire(Part.makeCircle(inner, at, way))))


def _side(
    joint: Joint, part: PartAt, solid: Any, hole: dict[str, Any], way: dict[str, Any], k: int
) -> dict[str, Any]:
    """One end of a through-bolt: the part it's in, its hole, and what bears on its seat."""
    assert joint.bolt is not None and joint.nut is not None and joint.washer is not None
    axis = way["axis"]
    out = -axis if k == 0 else axis
    seat = way["seats"][k]
    origin = np.asarray(hole["origin"], dtype=float)
    centre = origin + (seat - float(origin @ axis)) * axis
    shank = DIAMETER[joint.bolt.thread]
    on_nut = k == 1 and joint.pocket is not None
    if k == 0:
        under = f"the washer {joint.washer.name} under the bolt's head"
    elif on_nut:
        under = f"the nut {joint.nut.name} in its pocket"
    else:
        under = f"the washer {joint.washer.name} under the nut"
    what = f"the through joint {joint.name}'s bolt at {_xyz(centre)}"
    radius = hole["radius"]
    if radius < shank / 2 - through.SAME:
        raise NoAnswer(
            f"{what}: the hole in {part.name} is {2 * radius:.3f} mm across, narrower than the "
            f"bolt {joint.bolt.name}'s {shank:g} mm"
        )
    outer = (joint.nut.bears_mm if on_nut else joint.washer.outer_mm) / 2
    inner = max(radius, (shank if on_nut else joint.washer.hole_mm) / 2)
    if inner >= outer - through.SAME:
        raise NoAnswer(
            f"{what}: the hole in {part.name} is {2 * radius:.3f} mm across, and {under} is "
            f"{2 * outer:g} mm across where it bears: it has nothing to bear on"
        )
    _, planes = rows(solid)
    flats = [
        p["face"]
        for p in planes
        if float(p["normal"] @ out) > 1 - through.SQUARE
        and abs(float(p["at"] @ axis) - seat) <= 1e-3
    ]
    ring = _ring(centre, axis, inner, outer)
    bears = float(sum(ring.common(f).Area for f in flats)) if flats else 0.0
    whole = math.pi * (outer**2 - inner**2)
    expected = whole - through.peak_cut(hole, inner, outer)
    if bears <= 1e-6:
        raise NoAnswer(
            f"{what}: {part.name} has no flat face round its hole's end, at {_xyz(centre)}, "
            f"square to the bolt, for {under} to bear on"
        )
    return {
        "part": part.name,
        "under": under,
        "on_nut": on_nut,
        "seat": centre,
        "out": out,
        "hole_mm": 2 * radius,
        "hole_length_mm": way["lengths"][k],
        "inner": inner,
        "outer": outer,
        "bears_mm2": bears,
        "ring_mm2": whole,
        "seat_mm2": expected,
        "seated": bears >= SEATED * expected,
        "hole": hole,
    }


def ways(assembly: Assembly, placed: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """Each through joint's bolts as their way through both parts, or why one has none."""
    names = [p.name for p in assembly.parts]
    found: dict[str, list[dict[str, Any]]] = {}
    for joint in assembly.joints:
        if joint.kind != "through":
            continue
        parts = [assembly.part(n) for n in joint.between]
        solids = [placed[names.index(n)] for n in joint.between]
        faces = [rows(s) for s in solids]
        bolts = []
        for point in joint.at:
            what = f"the through joint {joint.name}'s bolt at {_xyz(point)}"
            holes = []
            for part, (cylinders, planes) in zip(parts, faces, strict=True):
                hole = through.bore(cylinders, point, planes)
                if isinstance(hole, str):
                    raise NoAnswer(
                        f"{what}: in {part.name}, {hole}. A through-bolt passes right through "
                        "both parts, in a round or peaked hole in each"
                    )
                holes.append(hole)
            way = through.meet(holes[0], holes[1])
            if isinstance(way, str):
                raise NoAnswer(f"{what}: {way}")
            sides = [_side(joint, parts[k], solids[k], holes[k], way, k) for k in range(2)]
            bolts.append({"axis": way["axis"], "meet": way["meet"], "sides": sides})
        found[joint.name] = bolts
    return found


def _piece(solid: Any, side: dict[str, Any]) -> tuple[Any, float]:
    """The part round one bolt: what's within the cut of the bolt's axis, and the cut's radius."""
    import FreeCAD as App
    import Part

    reach = CUT * 2 * side["outer"]
    seat = side["seat"]
    far = seat - side["hole_length_mm"] * side["out"]
    for _ in range(TRIES + 1):
        start = seat + reach * side["out"]
        length = float(np.linalg.norm(far - seat)) + 2 * reach
        cutter = Part.makeCylinder(reach, length, App.Vector(*start), App.Vector(*-side["out"]))
        piece = solid.common(cutter)
        if piece.Volume < solid.Volume * (1 - 1e-9) and piece.Solids:
            at = Part.Vertex(App.Vector(*seat))
            return min(piece.Solids, key=lambda s: s.distToShape(at)[0]), reach
        reach *= SMALLER
    raise NoAnswer(
        f"the part {side['part']} is too small round its bolt at {_xyz(seat)} to be read "
        f"there: nothing of it is {reach / SMALLER:.1f} mm from the bolt's axis to hold it by"
    )


def round_bolt(
    solid: Any,
    part: PartAt,
    side: dict[str, Any],
    axis: np.ndarray,
    pull: float,
    shear: np.ndarray,
    coarser: float = 1.0,
) -> dict[str, Any]:
    """The plastic round one end of a through-bolt, under the bolt's `pull`, in N, on its seat
    and its `shear`, the force the shank puts on this part's hole."""
    import FreeCAD as App
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    piece, reach = _piece(solid, side)
    doc = App.newDocument("Round")
    try:
        feature = doc.addObject("Part::Feature", "Piece")
        feature.Shape = piece
        shape = feature.Shape
        hole = side["hole"]
        origin, radius = np.asarray(hole["origin"], dtype=float), hole["radius"]

        def off_axis(point: np.ndarray) -> np.ndarray:
            away = point - origin
            return np.asarray(away - (away @ axis) * axis)

        held, wall, seat = [], [], []
        seat_at = float(side["seat"] @ axis)
        ends = sorted((seat_at, seat_at - side["hole_length_mm"] * float(side["out"] @ axis)))
        caps = (ends[0] - reach, ends[1] + reach)
        for number, face in enumerate(shape.Faces, 1):
            kind = type(face.Surface).__name__
            centre = np.array(face.CenterOfMass)
            if kind == "Cylinder":
                coaxial = (
                    abs(abs(float(np.array(face.Surface.Axis) @ axis)) - 1) <= through.SQUARE
                    and float(np.linalg.norm(off_axis(np.array(face.Surface.Center)))) <= 1e-6
                )
                if coaxial and abs(face.Surface.Radius - reach) <= 1e-6:
                    held.append(f"Face{number}")
                elif coaxial and abs(face.Surface.Radius - radius) <= through.SAME:
                    wall.append(face)
            elif kind == "Plane":
                level = float(centre @ axis)
                normal = np.array(face.normalAt(0, 0))
                if abs(abs(float(normal @ axis)) - 1) > through.SQUARE:
                    continue
                if min(abs(level - caps[0]), abs(level - caps[1])) <= 1e-6:
                    held.append(f"Face{number}")
                elif abs(level - seat_at) <= 1e-3 and float(normal @ side["out"]) > 0:
                    seat.append(face)
        if not held:
            raise NoAnswer(
                f"the part {part.name}, cut off {reach:.1f} mm from its bolt at "
                f"{_xyz(side['seat'])}, has no cut face to be held by"
            )
        size = max(LEAST, min(2 * radius / ACROSS_HOLE, side["hole_length_mm"] / THROUGH))
        size = max(size, (8.5 * piece.Volume / ELEMENTS) ** (1 / 3)) * coarser
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
            Density="1270 kg/m^3",
        )
        material.Material = values
        analysis.addObject(material)
        fixed = ObjectsFem.makeConstraintFixed(doc, "Fixed")
        fixed.References = [(feature, tuple(held))]
        analysis.addObject(fixed)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = feature
        mesh.CharacteristicLengthMax = size
        mesh.ElementOrder = "2nd"
        mesh.HighOrderOptimize = "Optimization"
        analysis.addObject(mesh)
        doc.recompute()
        wrong = GmshTools(mesh).create_mesh()
        fem = mesh.FemMesh
        if wrong or not fem.VolumeCount:
            raise NoAnswer(f"gmsh made no mesh of {part.name} round its bolt: {str(wrong)[:300]}")
        nodes = {n: np.array([p.x, p.y, p.z]) for n, p in fem.Nodes.items()}

        def triangles(face: Any) -> list[tuple[tuple[int, ...], float, np.ndarray]]:
            found = []
            for element in fem.getFacesByFace(face):
                wired = fem.getElementNodes(element)
                a, b, c = (nodes[n] for n in wired[:3])
                area = 0.5 * float(np.linalg.norm(np.cross(b - a, c - a)))
                found.append((wired, area, (a + b + c) / 3))
            return found

        pushes: dict[int, np.ndarray] = {}

        def put(weighted: list[tuple[tuple[int, ...], float]], force: np.ndarray, on: str) -> None:
            total = sum(w for _, w in weighted)
            if total <= 0:
                raise NoAnswer(
                    f"the mesh of {part.name} round its bolt at {_xyz(side['seat'])} has no "
                    f"triangle on {on}, at {size:g} mm"
                )
            # A uniform push on a second-order triangle is a third on each mid-side node.
            for wired, weight in weighted:
                for node in wired[3:]:
                    pushes[node] = pushes.get(node, 0) + force * weight / total / 3

        if pull > 0:
            under = [
                (wired, area)
                for face in seat
                for wired, area, centre in triangles(face)
                if side["inner"] <= float(np.linalg.norm(off_axis(centre))) <= side["outer"]
            ]
            put(under, -pull * side["out"], "what the washer or the nut bears on")
        across = float(np.linalg.norm(shear))
        by_mesh = None
        if across > 0:
            leans = shear / across
            leant, in_stretch = [], 0.0
            for face in wall:
                for wired, area, centre in triangles(face):
                    away = off_axis(centre)
                    square = float(away @ leans) / float(np.linalg.norm(away))
                    if square > 0:
                        leant.append((wired, area * square))
                        if ends[0] <= float(centre @ axis) <= ends[1]:
                            in_stretch += area * square
            put(leant, shear, "the hole's wall")
            # The second reading of `through.lean_share`: the mesh's triangles on the wall of
            # the bore's own stretch, whose length is the one the pressure is read over.
            by_mesh = in_stretch / (2 * radius * side["hole_length_mm"])
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir(tempfile.mkdtemp(prefix="round-"))
        fea.setup_ccx()
        wrong = fea.check_prerequisites()
        if wrong:
            raise NoAnswer(f"the solve round a bolt couldn't be set up: {wrong[:300]}")
        fea.write_inp_file()
        with open(fea.inp_file_name) as file:
            text = file.read()
        outputs = text.find("** Outputs")
        if outputs < 0:
            raise NoAnswer("FreeCAD's CalculiX input has no step to put the bolt's load in")
        outputs = text.rfind("\n***", 0, outputs) + 1
        step = "*CLOAD\n" + "".join(
            f"{node},{k + 1},{push[k]:.9g}\n"
            for node, push in sorted(pushes.items())
            for k in range(3)
            if abs(push[k]) > 0
        )
        with open(fea.inp_file_name, "w") as file:
            file.write(text[:outputs] + step + text[outputs:])
        fea.ccx_run()
        fea.load_results()
        results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
        if not results or not results[0].NodeStrainXX:
            raise NoAnswer(
                f"CalculiX gave no result for {part.name} round its bolt at {_xyz(side['seat'])}"
            )
        result = results[0]
        count = len(result.NodeNumbers)
        strain = np.zeros((count, 3, 3))
        for (i, j), name in {
            (0, 0): "XX", (1, 1): "YY", (2, 2): "ZZ", (0, 1): "XY", (0, 2): "XZ", (1, 2): "YZ"
        }.items():  # fmt: skip
            strain[:, i, j] = strain[:, j, i] = getattr(result, f"NodeStrain{name}")
        points = np.array([nodes[n] for n in result.NodeNumbers])
        away = points - origin
        near = np.linalg.norm(away - np.outer(away @ axis, axis), axis=1) <= READ * reach
        near &= (points @ axis >= caps[0] + (1 - READ) * reach) & (
            points @ axis <= caps[1] - (1 - READ) * reach
        )
        strain, points = strain[near], points[near]
        layers = part.layers_way()
        over_layers = np.einsum("i,nij,j->n", layers, strain, layers)
        each = np.linalg.eigvalsh(strain)
        stretch = np.abs(each).max(axis=1)
        worst, most = int(over_layers.argmax()), int(stretch.argmax())
        found = {
            "across_layers_pct": 100 * max(float(over_layers[worst]), 0.0),
            "across_at": [round(float(v), 3) for v in points[worst]],
            "stretch_pct": 100 * float(stretch[most]),
            "stretch_is": "pull" if each[most][np.abs(each[most]).argmax()] > 0 else "push",
            "stretch_at": [round(float(v), 3) for v in points[most]],
            "mesh_mm": round(size, 3),
            "elements": fem.VolumeCount,
            "held_mm": reach,
            "read_mm": READ * reach,
        }
        if hole["peak"] is not None and by_mesh is not None:
            found["wall_share"] = by_mesh
        return found
    finally:
        App.closeDocument(doc.Name)
