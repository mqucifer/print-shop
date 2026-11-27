#!/usr/bin/env python3
"""Why gmsh refuses an assembly's joined parts, `segment-facet intersection`, on an assembly of
one's own (mqucifer/print-shop#346).

Run from the folder the assembly is in:

    uv run python <print-shop>/scripts/load_close_probe.py ASSEMBLY.json [--mesh MM] [--placed]

`--mesh` is the mesh the run gave `load`, or `load`'s own where it gave none. It runs in the
bench's second image under `load --parts`'s limits, joins the parts as `load` does, with each
bolt's bore squared as `load --parts`'s first pass meshes the whole, or as placed with
`--placed`, as one pass meshes it, and says:

- the joined parts' faces, by kind, and how many the fuse made that no part has, and how many
  of either are narrow, under a tenth of the mesh across;
- the pairs of faces that meet nowhere and lie closer than their mesh's sag, as `load` now finds
  them (`close_faces`), by their kinds and whether they're of one part or two, and how many are
  too close to mesh at all;
- gmsh's mesh of the joined parts, first-order (its error, if any, comes before the elements'
  order is raised), as main makes it and with `load`'s local sizes where faces are close: each
  meshed or refused, and where refused, the kinds of the faces nearest where gmsh says it is,
  and whether a close pair `load` finds is among them.

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

#: How long each of gmsh's meshes may take, in s: two fit the hour.
GMSH_LIMIT = 1500


def inner(assembly_file: str, mesh: float | None, placed_only: bool) -> None:  # pragma: no cover
    """Inside the container: each count and verdict as it's found."""
    import collections
    import re
    import subprocess
    import time
    import types
    from pathlib import Path

    sys.path.insert(0, "/opt/bench")
    sys.path.append("/opt/conda/lib")
    # The image's own mount of the bench, not this package: mypy can't see it.
    from bench import assembly, cae, close_faces, load, load_parts  # type: ignore[attr-defined]

    began = time.time()

    def said(what: str) -> None:
        print(f"{time.time() - began:8.1f} s  {what}", flush=True)

    App, Part = cae.freecad("probe")
    import ObjectsFem
    from femmesh.gmshtools import GmshTools

    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    made = assembly.read(assembly_file)
    with load._quiet():
        placed = load._placed(made, False)
    size = load._mesh_size(placed, mesh)
    said(f"{len(placed)} parts placed; the mesh is `load`'s for them, or --mesh's")
    if placed_only:
        parts, spread, cores = placed, True, load._cores(made)
        what = "as placed, as one pass meshes them"
    else:
        with load._quiet():
            parts, count, kept = load_parts.squared(made, placed)
        spread, cores = False, {}
        what = (f"with {count} bores squared and {kept} round faces on bolts' axes left round, "
                "as the first pass of --parts meshes the whole")  # fmt: skip
    args = types.SimpleNamespace(toward="-z", lift=None, mesh=mesh)
    model = load._Model(made, parts, args, size, spread=spread, cores=cores)
    doc = App.newDocument("Probe")
    with load._quiet():
        model.join(doc)
    shape = model.feature.Shape
    faces = shape.Faces
    said(f"joined, {what}: {len(faces)} faces, {len(shape.Edges)} edges, "
         f"{len(shape.Solids)} solids")  # fmt: skip
    numbers = {p.name: k for k, p in enumerate(made.parts, 1)}

    def owners(face: object) -> str:
        names = model._owners(face)
        return "+".join(f"part{numbers[n]}" if n in numbers else n for n in names)

    def kinds(found: list[object]) -> str:
        counted = collections.Counter(close_faces.kind_of(f) for f in found)
        return ", ".join(f"{n} {k}" for k, n in counted.most_common()) or "none"

    print(f"  by kind: {kinds(faces)}", flush=True)
    given = load.Faces([f for solid in parts for f in solid.Faces])
    made_by_fuse = [f for f in faces if not given.has(f)]

    def narrow(face: object) -> bool:
        length = sum(e.Length for e in face.Edges)  # type: ignore[attr-defined]
        return bool(length > 0 and 2 * face.Area / length < size / 10)  # type: ignore[attr-defined]

    thin = [f for f in faces if narrow(f)]
    print(f"  the fuse made {len(made_by_fuse)} faces no part has ({kinds(made_by_fuse)}), "
          f"{sum(narrow(f) for f in made_by_fuse)} of them narrow; narrow in all: {len(thin)} "
          f"({kinds(thin)})", flush=True)  # fmt: skip
    measured: dict[tuple[int, int], float] = {}
    searched = time.time()
    closes = close_faces.found(shape, size, known=measured)
    refused = close_faces.too_close(closes)
    said(f"{len(closes)} pair(s) of faces that meet nowhere lie closer than their mesh's sag; "
         f"{len(refused)} of them too close to mesh at all; {len(measured)} pairs measured, in "
         f"{time.time() - searched:.1f} s")  # fmt: skip
    pairs = collections.Counter()
    for c in closes:
        a, b = faces[c.first - 1], faces[c.second - 1]
        one = "one part" if owners(a) == owners(b) else "two parts"
        pairs[(" and ".join(sorted((close_faces.kind_of(a), close_faces.kind_of(b)))), one)] += 1
    for (kind, of), n in pairs.most_common():
        print(f"  {n} pair(s): {kind}, of {of}", flush=True)
    finer_faces = {n for c in closes for n in c.faces}
    finer_edges = {n for c in closes for n in c.edges}
    print(f"  meshed finer: {len(finer_faces)} face(s), {len(finer_edges)} edge(s)", flush=True)
    in_pairs = {n for c in closes for n in (c.first, c.second)}

    mesh_object = ObjectsFem.makeMeshGmsh(doc, "Mesh")
    mesh_object.Shape = model.feature
    mesh_object.CharacteristicLengthMax = size
    mesh_object.ElementOrder = "1st"
    doc.recompute()
    tools = GmshTools(mesh_object)
    for name in ("update_mesh_data", "get_tmp_file_paths", "get_gmsh_command", "get_group_data",
                 "get_region_data", "get_boundary_layer_data", "write_part_file", "write_geo"):
        with load._quiet():
            getattr(tools, name)()  # fmt: skip
    geo = Path(tools.temp_file_geo)
    text = geo.read_text().replace("General.NumThreads = 8;", "General.NumThreads = 1;")
    plain = text.replace(
        "// meshing", ("" if spread else "Mesh.MeshSizeExtendFromBoundary = 0;\n") + "// meshing"
    )
    # As `load` meshes it again: finer where faces are close, but not where they're too close
    # to mesh at all, which it names where they're near the crossing and leaves elsewhere.
    kept = [c for c in closes if c not in refused]
    sized = plain.replace("// meshing", close_faces.fields(kept, size) + "// meshing", 1)
    cae.give_back()
    verdicts = {}
    refused_near = False
    for label, given_text in (("as main meshes it", plain), ("with load's local sizes", sized)):
        if label != "as main meshes it" and given_text == plain:
            print(f"  {label}: the same as main's, nothing close enough to mesh finer", flush=True)
            verdicts[label] = verdicts["as main meshes it"]
            continue
        run = geo.with_name("probe.geo")
        run.write_text(given_text)
        try:
            done = subprocess.run(
                [tools.gmsh_bin, "-", str(run)], capture_output=True, text=True, cwd=geo.parent,
                check=False, timeout=GMSH_LIMIT,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            verdicts[label] = "no answer"
            said(f"{label}: gmsh gave no answer in {GMSH_LIMIT} s, and was stopped")
            continue
        out = done.stdout + done.stderr
        errors = [line for line in out.splitlines() if "Error" in line]
        tets = re.findall(r"(\d+) tetrahedra", out)
        if not errors and done.returncode == 0:
            verdicts[label] = "meshed"
            said(f"{label}: meshed, {tets[-1] if tets else 'some'} tetrahedra")
            continue
        kind = ("segment-facet intersection" if "segment-facet" in out
                else "two segments intersect" if "Two segments intersect" in out
                else "another error")  # fmt: skip
        verdicts[label] = f"refused: {kind}"
        said(f"{label}: refused, {kind}")
        point = re.search(r"(?:intersection|at point) \(([-\d.e+]+),([-\d.e+]+),([-\d.e+]+)\)", out)
        if point is None:
            print("  gmsh gives no point", flush=True)
            continue
        where = Part.Vertex(App.Vector(*(float(p) for p in point.groups())))
        near = []
        for number, face in enumerate(faces, 1):
            box = face.BoundBox
            box.enlarge(size)
            if not box.isInside(where.Point):
                continue
            near.append((where.distToShape(face)[0], number, face))
        near.sort(key=lambda row: row[0])
        nearest = near[:6]
        for rank, (_, number, face) in enumerate(nearest, 1):
            flags = [w for w, yes in (("made by the fuse", not given.has(face)),
                                      ("narrow", narrow(face)),
                                      ("in a close pair load finds", number in in_pairs)) if yes]
            print(f"  nearest {rank}: {close_faces.kind_of(face)}, of {owners(face)}"
                  + (f", {', '.join(flags)}" if flags else ""), flush=True)  # fmt: skip
        numbers_near = {n for _, n, _ in nearest}
        found_there = any(c.first in numbers_near and c.second in numbers_near for c in closes)
        refused_there = [c for c in refused if min(
            faces[n - 1].distToShape(where)[0] for n in (c.first, c.second)) < size]  # fmt: skip
        print(f"  a close pair load finds is among the nearest faces: "
              f"{'yes' if found_there else 'no'}; pairs too close to mesh within the mesh of "
              f"the crossing: {len(refused_there)}", flush=True)  # fmt: skip
        if label == "as main meshes it" and refused_there:
            refused_near = True
    App.closeDocument(doc.Name)
    main_said, sized_said = verdicts["as main meshes it"], verdicts["with load's local sizes"]
    if main_said == "meshed":
        verdict = "gmsh meshes it as main does: the refusal isn't in this whole at this mesh"
    elif refused_near:
        verdict = "faces too close to mesh at all, where gmsh's cross: load's reply names them"
    elif sized_said == "meshed":
        verdict = "faces that meet nowhere lie closer than the mesh's sag: load meshes it finer"
    else:
        verdict = "load's local sizes don't mesh it: the cause is elsewhere, in the kinds above"
    print(f"verdict: {verdict}", flush=True)


def main(argv: list[str]) -> int:
    words = list(argv)
    placed = "--placed" in words
    if placed:
        words.remove("--placed")
    mesh = None
    if "--mesh" in words:
        k = words.index("--mesh")
        mesh = float(words[k + 1])
        del words[k : k + 2]
    if len(words) != 1 or not words[0].endswith(".json"):
        print("give the assembly: load_close_probe.py ASSEMBLY.json [--mesh MM] [--placed]")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    code = f"import sys\nGMSH_LIMIT = {GMSH_LIMIT}\n" + inspect.getsource(inner)
    code += f"\ninner({words[0]!r}, {mesh!r}, {placed!r})\n"
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv_ = host.container(
        Path.cwd(), [host.CAE_PYTHON, "-I", "-c", code], name, image=host.CAE_IMAGE,
        memory=host.TWO_PASSES_MEMORY,
    )  # fmt: skip
    try:
        done = subprocess.run(
            argv_, capture_output=True, text=True, timeout=host.TWO_PASSES, check=False
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        print(f"stopped at {host.TWO_PASSES} s")
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
