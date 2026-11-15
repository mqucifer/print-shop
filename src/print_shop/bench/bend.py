"""`bend`: the strain a flexing arm takes when it's pushed aside (mqucifer/print-shop#142).

A design run approved a snap-on lid that could never be fitted: each clip had to bend several
times what printed PETG takes across its layers, and nothing read it. `fit-on` reads how far an
arm has to move for a part to pass. This reads what that does to the arm.

**The reading.** `--arm` is a box round the arm, one side of it on the arm's root, where it joins
the rest of the part. The arm is the file's material in that box. It's held at its root, the
plane `--root` names, and one edge of it is pushed `--push` mm `--toward` a way: an edge at the
arm's extreme on the side the push comes from, as a hook's face has, the one furthest from the
root (`far`), the nearest (`near`), or each in turn (`both`, unless `--at` says). An edge, not a
face: what pushes a hook meets it along a line and leaves it free to tilt, and holding a whole
face square doubles the strain at the root. FreeCAD's FEM workbench meshes the arm with gmsh and
CalculiX solves it, linear and static, so the strain is in proportion to the push whatever the
material's stiffness.

**What's judged** is the strain across the layers, the stretch along `--layers` (z unless said),
which is what pulls one printed layer off the next: its peak anywhere on the arm but within half
a thickness of the pushed edge, where a push along a line has no finite strain in any mesh. The
verdict holds that peak to the material's limit across its layers, from `LIMITS`, or `--limit`.
It also says how far the arm's back moves, and how much room is behind it: an arm that meets a
wall before the push is done can't bend as this reads it, and that fails too.

**Second method.** A beam, by hand: the arm cut into 40 slabs from its root to the pushed edge,
each slab's area moment and outer fibre from OCCT's own volume properties, the tip's force from
the push by summing each slab's give, and the strain at a slab from its moment. On a plain strip
it's ε = 1.5·t·y/L², which is said too. It knows nothing of the mesh or the solver. The two are
held together half way along the arm, by the strain along it, where neither has the root's
corner or the push in it, within `AGREE`; past that it's `DISAGREE`, as it is for an arm too
stubby to be a beam.

**What it can't read:** friction, how hard the push is, a fit's hold, anything past the first
answer of a linear solve (an arm that yields, or one bent so far its shape changes the load), and
how a print's direction weakens it other than through the one limit.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from typing import Any

import numpy as np

from .cae import freecad
from .limits import over, said
from .report import NoAnswer, Report

AXES = ("x", "y", "z")
#: The most strain a printed material takes across its layers, in percent. `docs/bench.md` gives
#: each figure's source.
LIMITS = {"petg": 1.0}
#: How far the solve and the beam may differ half way along the arm, as a share of the beam's
#: figure, and still be one answer. A wide arm bends partly as a plate, up to 1/(1 - ν²) stiffer
#: where it can't curl across its width, 17% at ν 0.38, which moves strain along it, and a beam
#: has no shear: on the planted clips the two are 7 to 11% apart. A stubby arm, twice as long
#: as it's thick, is 24 to 34% apart, and that's no answer.
AGREE = 0.2
#: The slabs the beam cuts the arm into, from its root to the pushed edge.
SLABS = 40
#: A coordinate is on a plane within this, in mm.
ON = 1e-6
#: How far behind the arm room is looked for, in mm.
REACH = 50.0
#: The elements across the arm's thickness at its root, unless `--mesh` says: second-order
#: tetrahedra, so two carry a bend.
ACROSS = 2.0
#: The mesh is never coarser or finer than these unless `--mesh` says, in mm.
MESH = (0.2, 2.0)


def _span(box: Any, axis: int) -> tuple[float, float]:
    return (
        (box.XMin, box.XMax),
        (box.YMin, box.YMax),
        (box.ZMin, box.ZMax),
    )[axis]


def _flat(shape: Any, axis: int, at: float) -> bool:
    low, high = _span(shape.BoundBox, axis)
    return abs(low - at) < ON and abs(high - at) < ON


def _xyz(point: Any) -> str:
    return "(" + ", ".join(f"{c:g}" for c in np.round(np.asarray(point, dtype=float), 3)) + ")"


def _arm(shape: Any, args: Any) -> Any:
    """The file's material inside `--arm`, as one solid."""
    import FreeCAD as App
    import Part

    low, high = args.arm
    size = [b - a for a, b in zip(low, high, strict=True)]
    if min(size) <= 0:
        raise NoAnswer("--arm has no room inside it: give two corners apart on x, y and z")
    cut = shape.common(Part.makeBox(*size, App.Vector(*low)))
    solids = [s for s in cut.Solids if s.Volume > 1e-6]
    if not solids:
        box = shape.BoundBox
        raise NoAnswer(
            f"nothing of {args.file} is inside --arm: the part runs from "
            f"{_xyz((box.XMin, box.YMin, box.ZMin))} to {_xyz((box.XMax, box.YMax, box.ZMax))}"
        )
    if len(solids) > 1:
        raise NoAnswer(
            f"--arm holds {len(solids)} separate pieces of {args.file}: put the box round one "
            "arm, from its root to its tip, and clear of the wall beside it"
        )
    return solids[0]


def _edges(arm: Any, root: int, at: float, away: int, push: int, sign: int) -> dict[str, Any]:
    """The edges a push can meet: those lying along the arm's extreme on the side the push comes
    from, and running across the arm. The furthest from the root and the nearest."""
    low, high = _span(arm.BoundBox, push)
    extreme = low if sign > 0 else high
    found: dict[float, list[str]] = {}
    for number, edge in enumerate(arm.Edges, 1):
        if not _flat(edge, push, extreme) or edge.Length < 1e-3:
            continue
        span = _span(edge.BoundBox, root)
        if span[1] - span[0] > ON or (span[0] - at) * away <= ON:
            continue
        found.setdefault(round(span[0], 6), []).append(f"Edge{number}")
    if not found:
        raise NoAnswer(
            f"no edge of the arm lies along its extreme at {AXES[push]}={extreme:g}, across the "
            "arm and clear of its root: the push needs a sharp edge to meet there, as a hook's "
            "flat face or its crest has. A rounded hook has none"
        )
    order = sorted(found, key=lambda c: (c - at) * away)
    return {
        "extreme": extreme,
        "far": (order[-1], found[order[-1]]),
        "near": (order[0], found[order[0]]),
    }


def _solve(
    arm: Any, fixed: list[str], pushed: list[str], move: tuple[int, float], size: float
) -> dict[str, Any]:
    """The arm held at `fixed` with `pushed` moved `move`, by gmsh and CalculiX through FreeCAD's
    FEM workbench: each node's place, strain tensor and movement."""
    import FreeCAD as App
    import ObjectsFem
    from femmesh.gmshtools import GmshTools
    from femtools import ccxtools

    # One thread, so the same arm is the same mesh, and the same figure, every time.
    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    doc = App.newDocument("Bend")
    # The workbench prints as it goes, and a reply's first line is its verdict.
    said_before = os.dup(1)
    sys.stdout.flush()
    with open(os.devnull, "w") as nowhere:
        os.dup2(nowhere.fileno(), 1)
    try:
        part = doc.addObject("Part::Feature", "Arm")
        part.Shape = arm
        analysis = ObjectsFem.makeAnalysis(doc, "Analysis")
        solver = ObjectsFem.makeSolverCalculiXCcxTools(doc, "Solver")
        solver.AnalysisType = "static"
        solver.GeometricalNonlinearity = "linear"
        analysis.addObject(solver)
        # The push is a distance, so the strain doesn't turn on the stiffness: only Poisson's
        # ratio is the material's here.
        material = ObjectsFem.makeMaterialSolid(doc, "Material")
        values = material.Material
        values.update(
            Name="Printed", YoungsModulus="2000 MPa", PoissonRatio="0.38", Density="1270 kg/m^3"
        )
        material.Material = values
        analysis.addObject(material)
        held = ObjectsFem.makeConstraintFixed(doc, "Root")
        held.References = [(part, tuple(fixed))]
        analysis.addObject(held)
        push = ObjectsFem.makeConstraintDisplacement(doc, "Push")
        push.References = [(part, tuple(pushed))]
        # Only along the push: the edge slides and tilts freely otherwise.
        for axis, name in enumerate(AXES):
            setattr(push, f"{name}Free", axis != move[0])
        setattr(push, f"{AXES[move[0]]}Displacement", move[1])
        analysis.addObject(push)
        mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
        mesh.Shape = part
        mesh.CharacteristicLengthMax = size
        mesh.ElementOrder = "2nd"
        analysis.addObject(mesh)
        doc.recompute()
        began = time.time()
        wrong = GmshTools(mesh).create_mesh()
        meshed = time.time() - began
        if wrong or not mesh.FemMesh.VolumeCount:
            raise NoAnswer(f"gmsh made no mesh of the arm: {str(wrong)[:300]}")
        fea = ccxtools.FemToolsCcx(analysis, solver)
        fea.update_objects()
        fea.setup_working_dir(tempfile.mkdtemp(prefix="bend-"))
        fea.setup_ccx()
        wrong = fea.check_prerequisites()
        if wrong:
            raise NoAnswer(f"the solve couldn't be set up: {wrong[:300]}")
        began = time.time()
        fea.write_inp_file()
        fea.ccx_run()
        fea.load_results()
        solved = time.time() - began
        results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
        if not results or not results[0].NodeStrainXX:
            raise NoAnswer(
                f"CalculiX gave no result for the arm's {mesh.FemMesh.VolumeCount} elements, at "
                f"a mesh of {size:g} mm: it may have run out of the container's memory. Give a "
                "coarser --mesh"
            )
        result = results[0]
        nodes = result.Mesh.FemMesh.Nodes
        tensor = np.zeros((len(result.NodeNumbers), 3, 3))
        for (i, j), name in {
            (0, 0): "XX", (1, 1): "YY", (2, 2): "ZZ", (0, 1): "XY", (0, 2): "XZ", (1, 2): "YZ"
        }.items():  # fmt: skip
            tensor[:, i, j] = tensor[:, j, i] = np.array(getattr(result, f"NodeStrain{name}"))
        return {
            "points": np.array([[nodes[n].x, nodes[n].y, nodes[n].z] for n in result.NodeNumbers]),
            "strain": tensor,
            "moved": np.array([[v.x, v.y, v.z] for v in result.DisplacementVectors]),
            "nodes": mesh.FemMesh.NodeCount,
            "elements": mesh.FemMesh.VolumeCount,
            "mesh_s": round(meshed, 2),
            "solve_s": round(solved, 2),
        }
    finally:
        sys.stdout.flush()
        os.dup2(said_before, 1)
        os.close(said_before)
        App.closeDocument(doc.Name)


def _beam(
    arm: Any, root: int, at: float, away: int, push: int, load: float, moved: float
) -> dict[str, Any]:
    """The arm as a beam, by hand: slabs from the root to the pushed edge, each one's area moment
    about the axis it bends round and its outer fibre, from OCCT's volume properties. The tip's
    force, over the stiffness, is the push over the sum of each slab's give; the strain at a slab
    is its moment times its outer fibre over its area moment."""
    import FreeCAD as App
    import Part

    length = abs(load - at)
    thick = length / SLABS
    box = arm.BoundBox
    low = [box.XMin - 1, box.YMin - 1, box.ZMin - 1]
    size = [box.XLength + 2, box.YLength + 2, box.ZLength + 2]
    size[root] = thick
    turn = 3 - root - push
    slabs = []
    for number in range(SLABS):
        start = at + away * number * thick
        low[root] = min(start, start + away * thick)
        slab = arm.common(Part.makeBox(*size, App.Vector(*low)))
        if slab.Volume < 1e-9:
            raise NoAnswer(
                f"the arm has no material {number * thick:.3f} mm from its root, before the "
                "pushed edge: the beam can't be read through a gap"
            )
        # About the slab's own centre, round the axis it bends about, each solid of it carried to
        # that centre: the push's and the arm's directions both count, so the arm's, a slab's
        # own thickness, comes off.
        solids = [s for s in slab.Solids if s.Volume > 0]
        volume = sum(s.Volume for s in solids)
        centres = np.array([[s.CenterOfMass.x, s.CenterOfMass.y, s.CenterOfMass.z] for s in solids])
        volumes = np.array([s.Volume for s in solids])
        middle = (centres * volumes[:, None]).sum(axis=0) / volume
        round_turn = 0.0
        for solid, own in zip(solids, centres, strict=True):
            inertia = solid.MatrixOfInertia
            off = own - middle
            round_turn += (inertia.A11, inertia.A22, inertia.A33)[turn]
            round_turn += solid.Volume * (off[root] ** 2 + off[push] ** 2)
        area = volume / thick
        moment = (round_turn - volume * thick**2 / 12) / thick
        centre = float(middle[push])
        span = _span(slab.BoundBox, push)
        slabs.append(
            {
                "from_root": (number + 0.5) * thick,
                "area": area,
                "moment": moment,
                "fibre": max(span[1] - centre, centre - span[0]),
                "thick": span[1] - span[0],
            }
        )
    give = sum((length - s["from_root"]) ** 2 / s["moment"] * thick for s in slabs)
    force = moved / give

    def strain(from_root: float) -> float:
        near = min(slabs, key=lambda s: abs(s["from_root"] - from_root))
        return float(force * (length - from_root) * near["fibre"] / near["moment"])

    first = slabs[0]
    return {
        "length_mm": length,
        "thickness_mm": first["thick"],
        "root": strain(0.0),
        "peak": max(strain(0.0), *(strain(s["from_root"]) for s in slabs)),
        "strain": strain,
        # A plain strip of the root's thickness all the way: the usual formula.
        "strip": 1.5 * first["thick"] * moved / length**2,
    }


def _room(shape: Any, arm: Any, args: Any, push: int, sign: int, root: int, at: float) -> Any:
    """How far behind the arm the rest of the part is, in mm: the arm's back faces swept along
    the push into what the box left, and the nearest of what they meet. None where there's
    nothing within `REACH`."""
    import FreeCAD as App
    import Part

    low, high = args.arm
    size = [b - a for a, b in zip(low, high, strict=True)]
    rest = shape.cut(Part.makeBox(*size, App.Vector(*low)))
    span = _span(arm.BoundBox, push)
    back = span[1] if sign > 0 else span[0]
    way = [0.0, 0.0, 0.0]
    way[push] = sign * REACH
    nearest = None
    for face in arm.Faces:
        if not _flat(face, push, back):
            continue
        met = face.extrude(App.Vector(*way)).common(rest)
        for solid in met.Solids:
            if solid.Volume < 1e-9:
                continue
            reach = _span(solid.BoundBox, push)
            gap = (reach[0] - back) if sign > 0 else (back - reach[1])
            nearest = gap if nearest is None else min(nearest, gap)
    return None if nearest is None else max(0.0, nearest)


def _read(
    solved: dict[str, Any],
    args: Any,
    root: int,
    at: float,
    away: int,
    push: int,
    edge: tuple[float, float],
    thickness: float,
) -> dict[str, Any]:
    """What the solve says of the strain across the layers: on the root, within a thickness above
    it, and its peak and where; and of the strain along the arm half way along it, for the beam.
    The peak leaves out what's within half a thickness of the pushed edge: a push along a line
    has no finite strain under it, in any mesh, and what bends the arm is the same without it."""
    layers = AXES.index(args.layers)
    points = solved["points"]
    across = np.abs(solved["strain"][:, layers, layers])
    from_root = (points[:, root] - at) * away
    length = abs(edge[0] - at)
    clear = np.hypot(points[:, root] - edge[0], points[:, push] - edge[1]) > thickness / 2
    on_root = from_root < ON
    above = (from_root >= ON) & (from_root <= thickness) & clear
    width = args.mesh_used / 2
    half = (np.abs(from_root - length / 2) <= width) & clear
    if not (on_root.any() and above.any() and half.any() and clear.any()):
        raise NoAnswer(
            "the mesh has no node where the arm is read: on its root, within a thickness above "
            "it, and half way along it. Give a finer --mesh"
        )
    peak = int(np.argmax(np.where(clear, across, -1.0)))
    stretch = np.abs(np.linalg.eigvalsh(solved["strain"])).max(axis=1)
    most = int(np.argmax(np.where(clear, stretch, -1.0)))
    return {
        "root": float(across[on_root].max()),
        "above": float(across[above].max()),
        # Along the arm, whichever way the layers lie: that's the strain a beam has.
        "half": float(np.abs(solved["strain"][:, root, root])[half].max()),
        # The band's end nearer the root, where a strain that falls off along the arm is most.
        "half_from": max(0.0, length / 2 - width),
        "peak": float(across[peak]),
        "peak_at": [round(float(c), 3) for c in points[peak]],
        "stretch": float(stretch[most]),
        "stretch_at": [round(float(c), 3) for c in points[most]],
    }


def run(args: Any, report: Report) -> None:
    _, Part = freecad("bend")

    if not args.file.lower().endswith((".step", ".stp")):
        raise NoAnswer("bend reads a STEP file: an STL has no edges to hold or push")
    if args.push <= 0:
        raise NoAnswer("--push is a distance, more than 0: --toward says which way")
    shape = Part.read(args.file)
    arm = _arm(shape, args)
    root, at = AXES.index(args.root[0]), args.root[1]
    push, sign = AXES.index(args.toward[1]), 1 if args.toward[0] == "+" else -1
    if push == root:
        raise NoAnswer(
            f"--toward {args.toward} is along the arm, square to its root at {args.root[0]}="
            f"{at:g}: a push bends the arm sideways, so name a way across it"
        )
    low, high = _span(arm.BoundBox, root)
    fixed = [f"Face{n}" for n, face in enumerate(arm.Faces, 1) if _flat(face, root, at)]
    if not fixed or (low < at - ON and high > at + ON):
        raise NoAnswer(
            f"the arm in --arm has no end on --root {args.root[0]}={at:g}: it runs from "
            f"{low:g} to {high:g} along {args.root[0]}. Put one side of --arm on the root, where "
            "the arm joins the rest of the part, so the box cuts it there"
        )
    away = 1 if high > at + ON else -1
    edges = _edges(arm, root, at, away, push, sign)
    names = ("far", "near") if args.at == "both" else (args.at,)
    if edges["far"][0] == edges["near"][0]:
        names = names[:1]
    limit = args.limit if args.limit is not None else LIMITS[args.material]
    base = _beam(arm, root, at, away, push, edges["far"][0], args.push)
    thickness = base["thickness_mm"]
    args.mesh_used = args.mesh or min(MESH[1], max(MESH[0], thickness / ACROSS))
    move = (push, sign * args.push)
    span = _span(arm.BoundBox, push)
    back = span[1] if sign > 0 else span[0]
    room = _room(shape, arm, args, push, sign, root, at)
    if room is not None and room < ON:
        raise NoAnswer(
            f"--arm cuts through {args.file} along the arm's back, at {AXES[push]}={back:g}: "
            "there's material right behind the cut. The box holds the whole arm, clear of the "
            "wall beside it, and only its root is cut"
        )
    report.line(
        f"the arm: {arm.Volume:.2f} mm³ of {args.file} in --arm, {thickness:.3f} mm thick at its "
        f"root along {AXES[push]}, held at {args.root[0]}={at:g}, pushed {args.push:g} mm "
        f"{args.toward}, with a mesh of at most {args.mesh_used:g} mm"
    )
    pushes: dict[str, Any] = {}
    for name in names:
        where, pushed = edges[name]
        solved = _solve(arm, fixed, pushed, move, args.mesh_used)
        beam = _beam(arm, root, at, away, push, where, args.push)
        read = _read(solved, args, root, at, away, push, (where, edges["extreme"]), thickness)
        on_back = np.abs(solved["points"][:, push] - back) < ON
        back_moves = float((solved["moved"][on_back, push] * sign).max()) if on_back.any() else 0.0
        pushes[name] = {
            "edge_at": {AXES[root]: where, AXES[push]: edges["extreme"]},
            "length_mm": beam["length_mm"],
            "peak_pct": 100 * read["peak"],
            "peak_at": read["peak_at"],
            "root_pct": 100 * read["root"],
            "above_root_pct": 100 * read["above"],
            "half_way_pct": 100 * read["half"],
            "stretch_pct": 100 * read["stretch"],
            "stretch_at": read["stretch_at"],
            "beam_peak_pct": 100 * beam["peak"],
            "beam_root_pct": 100 * beam["root"],
            "beam_half_way_pct": 100 * beam["strain"](read["half_from"]),
            "strip_pct": 100 * beam["strip"],
            "back_moves_mm": back_moves,
            "nodes": solved["nodes"],
            "elements": solved["elements"],
            "mesh_s": solved["mesh_s"],
            "solve_s": solved["solve_s"],
        }
    report.numbers["pushes"] = pushes
    report.numbers["limit_pct"] = limit
    report.numbers["limit_from"] = "--limit" if args.limit is not None else args.material
    report.numbers["layers"] = args.layers
    report.numbers["thickness_mm"] = thickness
    report.numbers["mesh_mm"] = args.mesh_used
    report.numbers["room_behind_mm"] = room
    report.numbers["arm_mm3"] = arm.Volume
    _judge(args, report, pushes, limit, room)


def would_pass(
    push: float, thickness: float, length: float, peak: float, limit: float
) -> dict[str, float]:
    """What keeps the strain at the limit, from the reading's own peak: a strip's strain is
    ε = 1.5·t·y/L², in proportion to its thickness and its push and to one over its length
    squared, so each is scaled by how far the peak is from the limit, the other two as they
    are. The most push at this arm, the most thickness at this push, and the least length at
    this push, in mm."""
    share = limit / peak
    return {
        "push_mm": push * share,
        "thickness_mm": thickness * share,
        "length_mm": length / share**0.5,
    }


def _judge(
    args: Any, report: Report, pushes: dict[str, Any], limit: float, room: float | None
) -> None:
    """The verdict: the worst push's strain across the layers against the limit, the arm's back
    against the room behind it, and the solve against the beam."""
    apart = []
    for name, one in pushes.items():
        report.line(
            f"pushed at its {name} edge, {one['length_mm']:.3f} mm from the root: the strain "
            f"across the layers (along {args.layers}) peaks at {one['peak_pct']:.3f}%, at "
            f"{_xyz(one['peak_at'])}, as pushes.{name}.peak_pct; it's {one['root_pct']:.3f}% on "
            f"the root, {one['above_root_pct']:.3f}% within a thickness above it and "
            f"{one['half_way_pct']:.3f}% half way along the arm; the largest stretch any way is "
            f"{one['stretch_pct']:.3f}% at {_xyz(one['stretch_at'])}; the arm's back moves "
            f"{one['back_moves_mm']:.3f} mm {args.toward} ({one['nodes']} nodes, "
            f"{one['elements']} elements, meshed in {one['mesh_s']} s and solved in "
            f"{one['solve_s']} s)"
        )
        differ = abs(one["half_way_pct"] - one["beam_half_way_pct"]) / one["beam_half_way_pct"]
        one["differ_share"] = differ
        report.second(
            f"pushed at its {name} edge, the arm as a beam, {SLABS} slabs from the root, each "
            f"with its own section: {one['beam_half_way_pct']:.3f}% half way along the arm, "
            f"where the solve reads {one['half_way_pct']:.3f}%, {100 * differ:.1f}% apart; its "
            f"most is {one['beam_peak_pct']:.3f}%, and {one['beam_root_pct']:.3f}% at the root, "
            f"where the solve's peak is {one['peak_pct'] / one['beam_root_pct']:.2f} times it; "
            f"a plain strip of the root's thickness, 1.5·t·y/L², {one['strip_pct']:.3f}%"
        )
        if differ > AGREE:
            apart.append(name)
    worst = max(pushes, key=lambda n: pushes[n]["peak_pct"])
    one = pushes[worst]
    report.numbers["worst"] = worst
    report.numbers["across_layers_pct"] = one["peak_pct"]
    report.numbers["back_moves_mm"] = max(p["back_moves_mm"] for p in pushes.values())
    source = "--limit" if args.limit is not None else f"{args.material.upper()} across its layers"
    behind = (
        f"nothing is behind it within {REACH:g} mm"
        if room is None
        else f"there's {room:.3f} mm behind it, as room_behind_mm"
    )
    report.line(f"behind the arm: {behind}")
    if apart:
        report.verdict(
            "DISAGREE",
            f"pushed at its {' and '.join(apart)} edge, the solve and the beam read the strain "
            f"half way along the arm more than {100 * AGREE:g}% apart, as they do for an arm too "
            "short for its thickness to bend as a beam: read both, and don't pick one",
        )
        return
    strain_over = over(one["peak_pct"], limit)
    met = room is not None and over(report.numbers["back_moves_mm"], room)
    passing = would_pass(
        args.push, report.numbers["thickness_mm"], one["length_mm"], one["peak_pct"], limit
    )
    report.numbers["would_pass"] = passing
    report.line(
        f"what would pass, from this reading by the strip's ε = 1.5·t·y/L²: at this push, an arm "
        f"at most {passing['thickness_mm']:.3f} mm thick at its root (it's "
        f"{report.numbers['thickness_mm']:.3f}), or at least {passing['length_mm']:.3f} mm from "
        f"its root to the pushed edge (it's {one['length_mm']:.3f}); at this arm, a push of at "
        f"most {passing['push_mm']:.3f} mm (it's {args.push:g}), each as would_pass"
        + (
            f"; and at least {report.numbers['back_moves_mm']:.3f} mm of room behind it"
            if met
            else ""
        )
    )
    figure = (
        f"pushed {args.push:g} mm {args.toward} at its {worst} edge, the arm takes "
        f"{said(one['peak_pct'], limit, 2)}% across its layers, at {_xyz(one['peak_at'])}"
    )
    if strain_over or met:
        why = []
        if strain_over:
            why.append(f"{figure}, over the {limit:g}% limit ({source})")
        if met:
            why.append(
                f"the arm's back moves {report.numbers['back_moves_mm']:.3f} mm and there's "
                f"{room:.3f} mm behind it, so it meets what's behind it before the push is done"
            )
        report.verdict("FAIL", "; and ".join(why))
    else:
        report.verdict("OK", f"{figure}, within the {limit:g}% limit ({source}); {behind}")
