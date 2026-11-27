#!/usr/bin/env python3
"""Why gmsh refuses an assembly's joined parts, `segment-facet intersection`, on an assembly of
one's own (mqucifer/print-shop#346).

Run from the folder the assembly is in:

    uv run python <print-shop>/scripts/load_close_probe.py ASSEMBLY.json [--mesh MM] [--placed]

`--mesh` is the mesh the run gave `load`, or `load`'s own where it gave none. It runs in the
bench's second image under `load --parts`'s limits, joins the parts as `load` does, with each
bolt's bore squared as `load --parts`'s first pass meshes the whole, or as placed with
`--placed`, as one pass meshes it, and says:

- the joined parts' faces, by kind, how many are one part's and how many two parts' share,
  how many the fuse made that no part has, and how many are narrow, under a tenth of the mesh
  across;
- gmsh's first-order mesh of the joined parts as main makes it (its error, if any, comes before
  the elements' order is raised), and where it's refused for triangles that cross, the kinds
  of the faces nearest where it says they cross;
- then as `load` does now: the pairs of faces that meet nowhere and lie closer than their
  mesh's sag (`close_faces`), every two parts' pair and any near there, by their kinds and
  whether they're of one part or two, how many are too close to mesh at all, how many pairs
  were measured and in how long; and gmsh's mesh again, finer on them, up to `load.RETRIES`
  times, each where gmsh says its triangles cross again.

Each stage is said, `--`, as it starts, so a probe that's stopped says where.

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
from typing import Any

from print_shop.bench import host

#: How long each of gmsh's meshes may take, in s, and the probe in all.
GMSH_LIMIT = 1200
WHOLE_LIMIT = 2 * host.TWO_PASSES


def inner(assembly_file: str, mesh: float | None, placed_only: bool) -> None:  # pragma: no cover
    """Inside the container: each stage said as it starts, then its counts and verdicts."""
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

    def stage(what: str) -> None:
        said(f"-- {what}")

    stage("loading FreeCAD")
    App, Part = cae.freecad("probe")
    import ObjectsFem
    from femmesh.gmshtools import GmshTools

    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    stage("placing the parts")
    made = assembly.read(assembly_file)
    with load._quiet():
        placed = load._placed(made, False)
    size = load._mesh_size(placed, mesh)
    said(f"{len(placed)} parts placed; the mesh is `load`'s for them, or --mesh's")
    if placed_only:
        stage("reading the parts' cores, where any is printed")
        parts, spread, cores = placed, True, load._cores(made)
        what = "as placed, as one pass meshes them"
    else:
        stage("squaring the bolts' bores")
        with load._quiet():
            parts, count, kept = load_parts.squared(made, placed)
        spread, cores = False, {}
        what = (f"with {count} bores squared and {kept} round faces on bolts' axes left round, "
                "as the first pass of --parts meshes the whole")  # fmt: skip
    args = types.SimpleNamespace(toward="-z", lift=None, mesh=mesh)
    model = load._Model(made, parts, args, size, spread=spread, cores=cores)
    doc = App.newDocument("Probe")
    stage("joining the parts")
    with load._quiet():
        model.join(doc)
    shape = model.feature.Shape
    faces = shape.Faces
    said(f"joined, {what}: {len(faces)} faces, {len(shape.Edges)} edges, "
         f"{len(shape.Solids)} solids")  # fmt: skip
    close_faces._let_go(shape)
    numbers = {p.name: k for k, p in enumerate(made.parts, 1)}

    def owners(face: object) -> str:
        names = model._owners(face)
        return "+".join(f"part{numbers[n]}" if n in numbers else n for n in names)

    def kinds(found: list[object]) -> str:
        counted = collections.Counter(close_faces.kind_of(f) for f in found)
        return ", ".join(f"{n} {k}" for k, n in counted.most_common()) or "none"

    print(f"  by kind: {kinds(faces)}", flush=True)
    stage("finding each face's parts")
    owned = model._face_owners()
    print(f"  faces of one part: {sum(len(o) == 1 for o in owned)}, shared by two: "
          f"{sum(len(o) == 2 for o in owned)}, of none (a core's): "
          f"{sum(not o for o in owned)}", flush=True)  # fmt: skip
    stage("finding the faces the fuse made, and the narrow ones")
    given = load.Faces([f for solid in parts for f in solid.Faces])

    def narrow(face: object) -> bool:
        length = sum(e.Length for e in face.Edges)  # type: ignore[attr-defined]
        thin = length > 0 and 2 * face.Area / length < size / 10  # type: ignore[attr-defined]
        close_faces._let_go(face)
        return bool(thin)

    made_by_fuse = {n for n, f in enumerate(faces, 1) if not given.has(f)}
    thin = {n for n, f in enumerate(faces, 1) if narrow(f)}
    for solid in parts:
        close_faces._let_go(solid)
    print(f"  the fuse made {len(made_by_fuse)} faces no part has "
          f"({kinds([faces[n - 1] for n in made_by_fuse])}); narrow of them: "
          f"{len(made_by_fuse & thin)} ({kinds([faces[n - 1] for n in made_by_fuse & thin])}); "
          f"narrow in all: {len(thin)}", flush=True)  # fmt: skip

    stage("writing gmsh's input as load does")
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
    measured: dict[tuple[int, int], float] = {}
    closes: list[Any] = []

    def meshed(label: str, given_text: str) -> str | None:
        """gmsh's first-order mesh of `given_text`: none where it meshes, or its error lines."""
        stage(f"gmsh's mesh, {label}")
        cae.give_back()
        run = geo.with_name("probe.geo")
        run.write_text(given_text)
        try:
            done = subprocess.run(
                [tools.gmsh_bin, "-", str(run)], capture_output=True, text=True,
                cwd=geo.parent, check=False, timeout=GMSH_LIMIT,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            said(f"{label}: gmsh gave no answer in {GMSH_LIMIT} s, and was stopped")
            return "no answer"
        out = done.stdout + done.stderr
        errors = [line for line in out.splitlines() if "Error" in line]
        if not errors and done.returncode == 0:
            tets = re.findall(r"(\d+) tetrahedra", out)
            said(f"{label}: meshed, {tets[-1] if tets else 'some'} tetrahedra")
            return None
        kind = ("segment-facet intersection" if "segment-facet" in out
                else "two segments intersect" if "Two segments intersect" in out
                else "another error")  # fmt: skip
        said(f"{label}: refused, {kind}")
        return "; ".join(errors)

    def nearest(words: str) -> Any:
        """The kinds of the faces nearest where gmsh says its triangles cross, and that place."""
        where = load._crossed_at(words)
        if where is None:
            print("  gmsh gives no point", flush=True)
            return None
        stage("finding the faces nearest where gmsh refused the mesh")
        near = []
        for number, face in enumerate(faces, 1):
            box = face.BoundBox
            box.enlarge(size)
            if box.isInside(where.Point):
                near.append((where.distToShape(face)[0], number, face))
            close_faces._let_go(face)
        near.sort(key=lambda row: row[0])
        in_pairs = {n for c in closes for n in (c.first, c.second)}
        for rank, (_, number, face) in enumerate(near[:6], 1):
            flags = [w for w, yes in (("made by the fuse", number in made_by_fuse),
                                      ("narrow", number in thin),
                                      ("in a close pair found", number in in_pairs)) if yes]
            print(f"  nearest {rank}: {close_faces.kind_of(face)}, of {owners(face)}"
                  + (f", {', '.join(flags)}" if flags else ""), flush=True)  # fmt: skip
        return where

    def search(where: Any) -> tuple[list[Any], list[Any]]:
        """The close pairs as load finds them: every two parts' pair, and any near `where`."""
        stage("finding the close pairs"
              + (", and near where gmsh refused the mesh" if where else ""))  # fmt: skip
        before = len(measured)
        searched = time.time()
        found = close_faces.found(shape, size, known=measured, owners=owned, near=where)
        refused = close_faces.too_close(found)
        pairs: collections.Counter[tuple[str, str]] = collections.Counter()
        for c in found:
            a, b = faces[c.first - 1], faces[c.second - 1]
            one = "one part" if owned[c.first - 1] & owned[c.second - 1] else "two parts"
            both = " and ".join(sorted((close_faces.kind_of(a), close_faces.kind_of(b))))
            pairs[(both, one)] += 1
        said(f"{len(found)} close pair(s), {len(refused)} of them too close to mesh at all; "
             f"{len(measured) - before} more pairs measured, in {time.time() - searched:.1f} s")
        for (kind, of), n in pairs.most_common():
            print(f"  {n} pair(s): {kind}, of {of}", flush=True)
        return found, refused

    words = meshed("as main meshes it", plain)
    verdict = "gmsh meshes it as main does: the refusal isn't in this whole at this mesh"
    tries = 0
    while words is not None:
        if words == "no answer" or not re.search("intersect", words, re.I):
            verdict = "gmsh refuses it for something else than triangles that cross"
            break
        if tries == load.RETRIES:
            verdict = f"still refused after {tries} meshes finer: the faces above, where it is"
            nearest(words)
            break
        where = nearest(words)
        found, refused = search(where)
        here = [c for c in refused if where is not None and min(
            faces[n - 1].distToShape(where)[0] for n in (c.first, c.second)) < size]  # fmt: skip
        if here:
            verdict = (f"{len(here)} pair(s) too close to mesh at all, where gmsh's cross: load "
                       "names them")  # fmt: skip
            break
        new = [c for c in found if c not in refused and c not in closes]
        if not new:
            verdict = "no close pair where gmsh's triangles cross: the cause is in the faces above"
            break
        closes.extend(new)
        tries += 1
        finer = {n for c in closes for n in c.faces}
        said(f"meshing again finer on {len(finer)} face(s), {len(closes)} pair(s) in all")
        sized = plain.replace("// meshing", close_faces.fields(closes, size) + "// meshing", 1)
        words = meshed(f"finer where faces are close, try {tries}", sized)
        if words is None:
            verdict = (f"faces that meet nowhere lie closer than the mesh's sag: load meshes it "
                       f"finer, after {tries} tr{'y' if tries == 1 else 'ies'}")  # fmt: skip
    App.closeDocument(doc.Name)
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
    code = f"import sys\nfrom typing import Any\nGMSH_LIMIT = {GMSH_LIMIT}\n"
    code += inspect.getsource(inner)
    code += f"\ninner({words[0]!r}, {mesh!r}, {placed!r})\n"
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv_ = host.container(
        Path.cwd(), [host.CAE_PYTHON, "-I", "-c", code], name, image=host.CAE_IMAGE,
        memory=host.TWO_PASSES_MEMORY,
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
