#!/usr/bin/env python3
"""Where a part read as printed leaves slivers in its skin and core, and whether they turn its
mesh's elements inside out, on an assembly of one's own (mqucifer/print-shop#356).

Run from the folder the assembly is in:

    uv run python <print-shop>/scripts/as_printed_probe.py ASSEMBLY.json [--mesh MM]

`--mesh` is the mesh the run gave `load`, or `load`'s own where it gave none. It runs in the
bench's second image under `load`'s limits. For each part the assembly gives a `print`, in its
own axes as `load` cuts it (`bench/as_printed.py`), it says the part's faces, by kind, how many
are narrow (under a tenth of the mesh across, as `load` names a face narrow, or under a bead's
width), and its edges where two faces meet concave, how many of those touch a curved face, and
how many are smooth, the faces tangent there. Then for each of two cuts of its core:

- main's before mqucifer/print-shop#356: the part's common with 16 copies of itself, each moved
  its walls' thickness round, and with its shells;
- this branch's (`as_printed.cut_core`): OCCT's offset of the part stretched along its layers,
  or else the 16 copies mitred at its concave edges, each held to each layer's own outline
  offset in by its walls, and its slivers merged, or refused;

it says what that cut leaves: each cell's core, how it was cut and checked, its faces by kind,
how many narrow and of which kind, how many of those touch a curved face, and its slivers;
and with every printed part's cores fused in, as `load` joins them, gmsh's mesh as `load` makes
it, its elements' sides curved and straight, and how many of its elements are turned inside out
at any of CalculiX's four integration points, as CalculiX would find them, with the kinds of the
faces nearest the first and whether each is narrow and is where a skin meets its core.

It prints only counts, kinds of face, verdicts and seconds: each part by its number, never its
name, and no figure of any shape or place. Nothing is written but gmsh's own files in the
container's /tmp.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import uuid
from pathlib import Path

from print_shop.bench import host

#: How long the probe may take in all, in s.
WHOLE_LIMIT = 2 * host.TWO_PASSES


def inner(assembly_file: str, mesh: float | None) -> None:  # pragma: no cover
    """Inside the container: each stage said as it starts, then its counts and verdicts."""
    import collections
    import dataclasses
    import time
    import types
    from pathlib import Path

    import numpy as np

    sys.path.insert(0, "/opt/bench")
    sys.path.append("/opt/conda/lib")
    # The image's own mount of the bench, not this package: mypy can't see it.
    from bench import as_printed, assembly, cae, close_faces, load  # type: ignore[attr-defined]

    began = time.time()

    def said(what: str) -> None:
        print(f"{time.time() - began:8.1f} s  {what}", flush=True)

    def stage(what: str) -> None:
        said(f"-- {what}")

    def stopped(exc: Exception) -> str:
        # Only the error's kind and its words' first part: the rest may name a part.
        return f"{type(exc).__name__}: {str(exc).split(':', 1)[0][:120]}"

    stage("loading FreeCAD")
    App, Part = cae.freecad("probe")
    import ObjectsFem
    from femmesh.gmshtools import GmshTools

    branch_cut = as_printed.cut_core

    def mains_cut(part: object, layers: int, walls: float, top: float, bottom: float,
                  up: float, bead: float, name: str) -> object:
        """Main's cut before mqucifer/print-shop#356: the common with 16 copies, unchecked."""
        inside = as_printed.eroded_in_plane(part, layers, walls)
        shelled = as_printed.eroded_along(part, layers, top, bottom, up)
        core = None
        if inside is not None and shelled is not None:
            core = as_printed._one(as_printed._solids(inside.common(shelled)))
        return as_printed.Cut(core, "main's 16 copies", 0, 0.0)

    cuts = {"main's": mains_cut, "this branch's": branch_cut}

    stage("placing the parts")
    made = assembly.read(assembly_file)
    folder = Path(made.path).parent
    with load._quiet():
        placed = load._placed(made, False)
    size = load._mesh_size(placed, mesh)
    said(f"{len(placed)} parts placed; the mesh is `load`'s for them, or --mesh's")
    curved = {"B-spline", "Bézier", "cylindrical", "conical", "spherical", "toroidal",
              "revolved", "extruded"}  # fmt: skip

    def width(face: object) -> float:
        length = sum(e.Length for e in face.Edges)  # type: ignore[attr-defined]
        return 2 * face.Area / length if length > 0 else 0.0  # type: ignore[attr-defined]

    def kinds(found: list[object]) -> str:
        counted = collections.Counter(close_faces.kind_of(f) for f in found)
        return ", ".join(f"{n} {k}" for k, n in counted.most_common()) or "none"

    def touches_curved(shape: object, face: object) -> bool:
        for edge in face.Edges:  # type: ignore[attr-defined]
            for other in shape.ancestorsOfType(edge, Part.Face):  # type: ignore[attr-defined]
                if not other.isSame(face) and close_faces.kind_of(other) in curved:
                    return True
        return False

    def faces_said(label: str, shape: object, bead: float) -> None:
        if shape is None:
            print(f"  {label}: nothing left", flush=True)
            return
        faces = shape.Faces  # type: ignore[attr-defined]
        thin = [f for f in faces if width(f) < size / 10]
        beads = [f for f in faces if width(f) < bead]
        by_curve = [f for f in thin if touches_curved(shape, f)]
        print(f"  {label}: {len(shape.Solids)} solid(s), {len(faces)} faces ({kinds(faces)}), "  # type: ignore[attr-defined]
              f"valid {shape.isValid()}; narrow, under a tenth of the mesh: {len(thin)} "  # type: ignore[attr-defined]
              f"({kinds(thin)}), {len(by_curve)} of them touching a curved face; under a "
              f"bead's width: {len(beads)} ({kinds(beads)})", flush=True)  # fmt: skip

    def edges_said(shape: object) -> None:
        concave = smooth = by_curve = 0
        for edge in shape.Edges:  # type: ignore[attr-defined]
            faces = shape.ancestorsOfType(edge, Part.Face)  # type: ignore[attr-defined]
            if len(faces) != 2:
                continue
            middle = edge.valueAt(sum(edge.ParameterRange) / 2)
            normals = []
            for face in faces:
                u, v = face.Surface.parameter(middle)
                normals.append(face.normalAt(u, v))
            if normals[0].getAngle(normals[1]) < 0.02:
                smooth += 1
                continue
            # Concave where a step out of one face and a half step into the other is inside.
            step = 0.01
            if any(shape.isInside(middle + a * step - b * (step / 2), 1e-6, False)  # type: ignore[attr-defined]
                   for a, b in (normals, normals[::-1])):  # fmt: skip
                concave += 1
                if any(close_faces.kind_of(f) in curved for f in faces):
                    by_curve += 1
        print(f"  edges: {len(shape.Edges)}, concave {concave}, {by_curve} of them by a "  # type: ignore[attr-defined]
              f"curved face; smooth, its faces tangent: {smooth}", flush=True)

    def inverted(fem: object) -> tuple[int, int, list[int]]:
        """How many elements, and how many with a Jacobian not over naught at any of
        CalculiX's integration points, and those elements' numbers."""
        nodes = fem.Nodes  # type: ignore[attr-defined]
        numbers = list(fem.Volumes)  # type: ignore[attr-defined]
        given = [fem.getElementNodes(e) for e in numbers]  # type: ignore[attr-defined]
        if any(len(g) != 10 for g in given):
            return len(numbers), -1, []
        places = np.array([[[nodes[n].x, nodes[n].y, nodes[n].z] for n in g] for g in given])
        corners = np.array([[-1.0, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
        dets = []
        for point in load.POINTS:
            share = np.array([1 - sum(point), *point])
            slopes = np.zeros((10, 3))
            for i in range(4):
                slopes[i] = (4 * share[i] - 1) * corners[i]
            for k, (i, j) in enumerate(load.MIDS, 4):
                slopes[k] = 4 * (share[i] * corners[j] + share[j] * corners[i])
            mapped = np.einsum("ak,nai->nki", slopes, places)
            dets.append(np.linalg.det(mapped))
        # FreeCAD's order turns each element the other way from CalculiX's, which its input
        # turns back: inside out is the other way from the mesh's own, at any point.
        signed = np.array(dets).T * np.sign(np.median(dets))
        bad = [numbers[k] for k in np.flatnonzero((signed <= 0).any(axis=1))]
        return len(numbers), len(bad), bad

    def nearest_said(model: object, fem: object, number: int, cores: list[object]) -> str:
        nodes = fem.Nodes  # type: ignore[attr-defined]
        corners = [nodes[n] for n in fem.getElementNodes(number)[:4]]  # type: ignore[attr-defined]
        middle = Part.Vertex(sum(corners, App.Vector()) * 0.25)
        core_faces = load.Faces([f for c in cores for f in c.Faces])  # type: ignore[attr-defined]
        near = []
        for face in model.whole.Faces:  # type: ignore[attr-defined]
            box = face.BoundBox
            box.enlarge(size)
            if box.isInside(middle.Point):
                near.append((float(face.distToShape(middle)[0]), face))
        near.sort(key=lambda row: row[0])
        words = []
        for _, face in near[: load.NEAREST]:
            flags = [w for w, yes in (("narrow", width(face) < size / 10),
                                      ("skin meets core", core_faces.has(face)))
                     if yes]  # fmt: skip
            words.append(close_faces.kind_of(face) + (f" ({', '.join(flags)})" if flags else ""))
        return ", ".join(words) or "none near"

    printed = [(k, part) for k, part in enumerate(made.parts, 1) if part.printed is not None]
    if not printed:
        print("verdict: no part has a `print`: load reads them all solid", flush=True)
        return
    owns = {}
    for k, part in printed:
        stage(f"part {k}, in its own axes")
        own = load._one_placed(dataclasses.replace(part, turn=(), at=(0.0, 0.0, 0.0)), folder,
                               False)  # fmt: skip
        owns[k] = own
        settings = part.printed.settings
        bead = float(settings.line_width or 0)
        faces_said("the part", own, bead)
        edges_said(own)
    verdicts = {}
    for name, cut in cuts.items():
        stage(f"{name} cut")
        as_printed.cut_core = cut
        refused = False
        for k, part in printed:
            settings = part.printed.settings
            if settings.missing():
                print(f"  part {k}: its settings leave some out: load refuses it", flush=True)
                refused = True
                continue
            layers = load.AXES.index(part.layers)
            bead = float(settings.line_width or 0)
            said(f"part {k}: its cells' cores")
            try:
                cells = as_printed.cells(owns[k], part.printed, layers, folder, f"part{k}")
            except Exception as exc:  # noqa: BLE001
                # The bench's own refusal names the part by the number given it: said whole.
                words = str(exc) if type(exc).__name__ == "NoAnswer" else stopped(exc)
                print(f"  refused: {words}", flush=True)
                refused = True
                continue
            for number, cell in enumerate(cells, 1):
                core = as_printed._one(list(cell.core))
                print(f"  cell {number} of {len(cells)}: "
                      + (f"{len(cell.core)} core solid(s)" if cell.core else "no core")
                      + f"; cut by {cell.cut}"
                      + (f", checked at {cell.checked} planes, {100 * cell.off:.1f}% of its "
                         "walls off at most" if cell.checked else ""), flush=True)  # fmt: skip
                if core is not None:
                    faces_said(f"cell {number}'s core", core, bead)
                    print(f"  slivers, under a bead by a curved face: "
                          f"{len(as_printed.slivers(core, bead))}", flush=True)
        if refused:
            verdicts[name] = "refused before it's meshed"
            continue
        said("joining the parts with their cores, as load does")
        try:
            with load._quiet():
                cores = load._cores(made)
            args = types.SimpleNamespace(toward="-z", lift=None, mesh=mesh)
            model = load._Model(made, placed, args, size, cores=cores)
            doc = App.newDocument(f"Probe{name}")
            with load._quiet():
                model.join(doc)
        except Exception as exc:  # noqa: BLE001
            print(f"  refused: {stopped(exc)}", flush=True)
            verdicts[name] = "refused as it's joined"
            continue
        shape = model.feature.Shape
        thin = [f for f in shape.Faces if width(f) < size / 10]
        core_solids = [shape.Solids[i] for i, *_ in model.core_solids]
        said(f"joined: {len(shape.Faces)} faces ({kinds(shape.Faces)}), {len(shape.Solids)} "
             f"solids, {len(core_solids)} of them cores; narrow: {len(thin)} ({kinds(thin)})")
        found = []
        for straight in (False, True):
            how = "straight" if straight else "curved"
            said(f"gmsh's mesh, its sides {how}, as load makes it")
            mesh_object = ObjectsFem.makeMeshGmsh(doc, f"Mesh{how}")
            mesh_object.Shape = model.feature
            mesh_object.CharacteristicLengthMax = size
            mesh_object.ElementOrder = "2nd"
            if straight:
                mesh_object.SecondOrderLinear = True
                mesh_object.HighOrderOptimize = "None"
            else:
                mesh_object.HighOrderOptimize = "Optimization"
            doc.recompute()
            cae.give_back()
            try:
                with load._quiet():
                    tools = model._gmsh(GmshTools, mesh_object, "")
            except Exception as exc:  # noqa: BLE001
                print(f"  gmsh stopped: {stopped(exc)}", flush=True)
                found.append(f"{how}: gmsh stopped")
                continue
            fem = mesh_object.FemMesh
            if not fem.VolumeCount:
                words = load._gmsh_said(tools)
                kind = ("triangles that cross" if load.CROSSED.search(words or "")
                        else "another error")  # fmt: skip
                print(f"  gmsh refused it: {kind}", flush=True)
                found.append(f"{how}: gmsh refused it")
                continue
            count, bad, numbers = inverted(fem)
            if bad < 0:
                print(f"  {count} elements, not all of ten nodes: not read", flush=True)
                found.append(f"{how}: not read")
                continue
            print(f"  {count} elements, {bad} turned inside out", flush=True)
            if numbers:
                print(f"  the faces nearest the first: "
                      f"{nearest_said(model, fem, numbers[0], core_solids)}", flush=True)
            found.append(f"{how}: {bad} of {count} inside out")
        verdicts[name] = "; ".join(found)
        App.closeDocument(doc.Name)
    for name, verdict in verdicts.items():
        print(f"verdict, {name} cut: {verdict}", flush=True)


def main(argv: list[str]) -> int:
    words = list(argv)
    mesh = None
    if "--mesh" in words:
        k = words.index("--mesh")
        mesh = float(words[k + 1])
        del words[k : k + 2]
    if len(words) != 1 or not words[0].endswith(".json"):
        print("give the assembly: as_printed_probe.py ASSEMBLY.json [--mesh MM]")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    code = "import sys\nfrom typing import Any\n" + inspect.getsource(inner)
    code += f"\ninner({words[0]!r}, {mesh!r})\n"
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv_ = host.container(
        Path.cwd(), [host.CAE_PYTHON, "-I", "-c", code], name, image=host.CAE_IMAGE,
    )  # fmt: skip
    try:
        done = subprocess.run(
            argv_, capture_output=True, text=True, timeout=WHOLE_LIMIT, check=False
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        print(f"stopped at {WHOLE_LIMIT} s")
        return 2
    print(done.stdout, end="")
    if done.returncode:
        # Only the error's kind: its words may name a part or give a figure.
        last = (done.stderr.strip().splitlines() or ["none"])[-1].split(":", 1)[0]
        print(f"exit {done.returncode}" + (" (killed)" if done.returncode == host.KILLED else "")
              + f", the error's kind: {last}")  # fmt: skip
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
