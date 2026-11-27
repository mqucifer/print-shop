#!/usr/bin/env python3
"""Where a part read as printed leaves slivers in its skin and core, and whether they turn its
mesh's elements inside out, on an assembly of one's own (mqucifer/print-shop#356).

Run from the folder the assembly is in:

    uv run python <print-shop>/scripts/as_printed_probe.py ASSEMBLY.json [--mesh MM]
        [--thin | --offsets]

`--mesh` is the mesh the run gave `load`, or `load`'s own where it gave none. It runs in the
bench's second image under `load`'s limits. For each part the assembly gives a `print`, in its
own axes as `load` cuts it (`bench/as_printed.py`), it says the part's faces, by kind, how many
are narrow (under a tenth of the mesh across, as `load` names a face narrow, or under a bead's
width), and its edges where two faces meet concave, how many of those touch a curved face, and
how many are smooth, the faces tangent there. Then for each of two cuts of its core:

- main's before mqucifer/print-shop#356: the part's common with 16 copies of itself, each moved
  its walls' thickness round, and with its shells;
- this branch's (`as_printed.cut_core`), opened where its layers are too thin for infill
  (mqucifer/print-shop#363): OCCT's offset of the part stretched along its layers,
  or else the 16 copies mitred at its concave edges, each held to each layer's own outline
  offset in by its walls, and its slivers merged, or refused;

it says what that cut leaves: each cell's core, how it was cut and checked, its faces by kind,
how many narrow and of which kind, how many of those touch a curved face, and its slivers;
and with every printed part's cores fused in, as `load` joins them, gmsh's mesh as `load` makes
it, its elements' sides curved and straight, and how many of its elements are turned inside out
at any of CalculiX's four integration points, as CalculiX would find them, with the kinds of the
faces nearest the first and whether each is narrow and is where a skin meets its core.

With `--thin` (mqucifer/print-shop#363), it says instead, for each printed part, where its core
is too thin for Orca's sparse infill: the core as it's cut now, or the copies mitred where that's
refused, and its slivers; how many of its layers, every 0.5 mm, have a gap where the area inside
its walls, opened as Orca opens it (`PerimeterGenerator.cpp:1658-1663`) or by a bead's width,
leaves no infill, how many regions, and whether they stay the same from layer to layer; and for
each sliver, whether it lies in such a gap or in an island of infill under 15 mm², which Orca's
`minimum_sparse_infill_area` fills solid.

With `--offsets` (mqucifer/print-shop#363), it says instead whether OCCT's offset can cut each
printed part's walls at all: how many of its faces are upright to its layers or flat in their
plane, and OCCT's offset of it stretched 10, 3 and 1 times along its layers, at two tolerances,
with arcs at its concave edges or mitres, on the part and on it simplified; unstretched, its
flat faces moved back along its layers by copies, exact on a part whose faces are all upright or
flat; and for each made, its check against its layers and its slivers.

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

#: How long the probe may take in all, in s.
WHOLE_LIMIT = 2 * host.TWO_PASSES


def inner(  # pragma: no cover
    assembly_file: str, mesh: float | None, mode: str = ""
) -> None:
    """Inside the container: each stage said as it starts, then its counts and verdicts."""
    import collections
    import dataclasses
    import math
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
                  up: float, bead: float, name: str, open_by: float = 0.0) -> object:
        """Main's cut before mqucifer/print-shop#356: the common with 16 copies, unchecked."""
        inside = as_printed.eroded_in_plane(part, layers, walls)
        shelled = as_printed.eroded_along(part, layers, top, bottom, up)
        core = None
        if inside is not None and shelled is not None:
            core = as_printed._one(as_printed._solids(inside.common(shelled)))
        return as_printed.Cut(core, "main's 16 copies", None)

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

    def dissect(k: int, own: object, layers: int, settings: object) -> None:
        """This branch's ways, one at a time, on the part and on it simplified, and where any
        slivers each leaves lie."""
        bead = float(settings.line_width or 0)  # type: ignore[attr-defined]
        height = float(settings.layer_height or 0)  # type: ignore[attr-defined]
        walls = as_printed.wall_thickness(int(settings.walls or 0), bead, height)  # type: ignore[attr-defined]
        top = float(settings.top or 0) * height  # type: ignore[attr-defined]
        bottom = float(settings.bottom or 0) * height  # type: ignore[attr-defined]
        up = -1.0 if (settings.up or "+")[0] == "-" else 1.0  # type: ignore[attr-defined]
        own_narrow = [f for f in own.Faces if width(f) < bead]  # type: ignore[attr-defined]
        smooth: list[object] = []
        mitred: list[object] = []
        unmitred: list[object] = []
        for edge in own.Edges:  # type: ignore[attr-defined]
            faces = own.ancestorsOfType(edge, Part.Face)  # type: ignore[attr-defined]
            if len(faces) != 2:
                continue
            middle = edge.valueAt(sum(edge.ParameterRange) / 2)
            normals = [f.normalAt(*f.Surface.parameter(middle)) for f in faces]
            if normals[0].getAngle(normals[1]) < 0.02:
                smooth.append(edge)
        for edge, faces in as_printed._concave(own):
            flat = all(type(f.Surface).__name__ == "Plane" for f in faces)
            (mitred if flat and type(edge.Curve).__name__ == "Line" else unmitred).append(edge)
        print(f"  part {k}: concave edges {len(mitred) + len(unmitred)}, {len(unmitred)} not "
              f"mitred (a curved face or edge); smooth edges {len(smooth)}; its own faces "
              f"under a bead {len(own_narrow)} ({kinds(own_narrow)})", flush=True)

        def near(face: object, shapes: list[object], within: float) -> bool:
            return any(face.distToShape(other)[0] < within for other in shapes)  # type: ignore[attr-defined]

        def where(core: object) -> None:
            found = as_printed.slivers(core, bead)
            counted: collections.Counter[str] = collections.Counter()
            for face in found:
                round_ = sorted({close_faces.kind_of(o) for e in face.Edges
                                 for o in core.ancestorsOfType(e, Part.Face)  # type: ignore[attr-defined]
                                 if not o.isSame(face)})  # fmt: skip
                flags = [w for w, yes in (
                    ("by the part's own narrow face", near(face, own_narrow, walls + bead)),
                    ("by a smooth edge", near(face, smooth, 2 * walls)),
                    ("by a mitred concave edge", near(face, mitred, 2 * walls)),
                    ("by an unmitred concave edge", near(face, unmitred, 2 * walls)),
                ) if yes] or ["by none of those"]  # fmt: skip
                bucket = ("under 0.05 mm" if width(face) < 0.05 else "under 0.15 mm"
                          if width(face) < 0.15 else "under a bead's width")  # fmt: skip
                counted[f"{close_faces.kind_of(face)}, {bucket}, touching "
                    f"{'/'.join(round_)}; {', '.join(flags)}"] += 1
            for words, n in counted.most_common():
                print(f"    {n} sliver(s): {words}", flush=True)

        open_by = as_printed.open_radius(bead, height)
        thin = as_printed.thin_runs(own, layers, walls, open_by, bead)
        print(f"  part {k}: {len(thin)} run(s) of layers too thin for infill, to open",
              flush=True)  # fmt: skip
        sources = [("the part", own)]
        simple = as_printed.simplified(own, bead)
        print(f"  part {k} simplified, its faces under a bead merged: "
              + (f"{simple[1]} merged, {len(simple[0].Faces)} faces left" if simple
                 else "none to merge, or OCCT couldn't"), flush=True)  # fmt: skip
        if simple:
            sources.append(("it simplified", simple[0]))
        for label, source in sources:
            shelled = as_printed.eroded_along(source, layers, top, bottom, up)
            for how in ("offset", "copies", "copies, mitred"):
                said(f"part {k}, {label}: {how}")
                try:
                    if how == "offset":
                        inside = as_printed.offset_in_plane(source, layers, -walls)
                    else:
                        inside = as_printed.eroded_in_plane(source, layers, walls)
                        if how.endswith("mitred") and inside is not None:
                            prisms, _ = as_printed.mitres(source, layers, walls)
                            if prisms:
                                inside = as_printed._one(as_printed._solids(
                                    inside.cut(Part.makeCompound(prisms))))
                            if inside is not None:
                                inside = inside.removeSplitter()
                except Exception as exc:  # noqa: BLE001
                    print(f"    stopped: {stopped(exc)}", flush=True)
                    continue
                core = None
                if inside is not None and shelled is not None:
                    core = as_printed._one(as_printed._solids(inside.common(shelled)))
                if core is not None and thin:
                    core = as_printed._one(as_printed._solids(core.cut(Part.makeCompound(thin))))
                check = as_printed.checked(own, core, layers, walls, top, bottom, up, open_by)
                found = as_printed.slivers(core, bead) if core is not None else []
                print(f"    checked at {check.read} of {check.planes} planes, short by "
                      f"{100 * check.short:.1f}% of its walls and over by "
                      f"{100 * check.over:.1f}% at most: "
                      f"{check.fails() or 'holds'}; slivers {len(found)}", flush=True)
                if found:
                    where(core)
                    merged = as_printed.healed(core, bead)
                    print("    merged: " + ("none left" if merged is not None else
                                            "some left"), flush=True)  # fmt: skip

    def offsets(k: int, own: Any, layers: int, settings: Any) -> None:
        """Whether OCCT's offset can cut the part's walls at all, and how (mqucifer/print-shop
        #363): stretched along its layers or not, at two tolerances, with arcs or mitres at its
        concave edges, on the part and on it simplified; and for each that's made, how it
        holds to its layers and its slivers."""
        bead = float(settings.line_width or 0)
        height = float(settings.layer_height or 0)
        walls = as_printed.wall_thickness(int(settings.walls or 0), bead, height)
        top = float(settings.top or 0) * height
        bottom = float(settings.bottom or 0) * height
        up = -1.0 if (settings.up or "+")[0] == "-" else 1.0
        open_by = as_printed.open_radius(bead, height)
        way = as_printed._vector(layers, 1.0)
        upright = flat = slanted = 0
        slanted_kinds: collections.Counter[str] = collections.Counter()
        for face in own.Faces:
            u0, u1, v0, v1 = face.ParameterRange
            along = [abs(face.normalAt(u0 + (u1 - u0) * i / 4, v0 + (v1 - v0) * j / 4).dot(way))
                     for i in range(5) for j in range(5)]  # fmt: skip
            if all(a < 1e-6 for a in along):
                upright += 1
            elif close_faces.kind_of(face) == "flat" and all(abs(a - 1) < 1e-6 for a in along):
                flat += 1
            else:
                slanted += 1
                slanted_kinds[close_faces.kind_of(face)] += 1
        stack = as_printed._levels(own, layers) is not None
        others = ", ".join(f"{n} {w}" for w, n in slanted_kinds.items()) or "none"
        print(f"  part {k}: faces upright to its layers {upright}, flat in their plane {flat}, "
              f"any other {slanted} ({others}); a stack of prisms along its layers, cut by "
              f"slabs: {'yes' if stack else 'no'}", flush=True)
        shells = as_printed.eroded_along(own, layers, top, bottom, up)
        simple = as_printed.simplified(own, bead)
        sources = [("the part", own)] + ([("it simplified", simple[0])] if simple else [])
        for label, source in sources:
            for stretch, tolerance, join in ((10.0, 1e-4, 0), (10.0, 1e-2, 0), (3.0, 1e-4, 0),
                                             (1.0, 1e-4, 0), (1.0, 1e-2, 0), (1.0, 1e-4, 2)):
                words = (f"{label}, stretched {stretch:g}x, tolerance {tolerance:g}, "
                         f"{'arcs' if join == 0 else 'mitres'}")
                began_at = time.time()
                try:
                    scale = [1.0, 1.0, 1.0]
                    scale[layers] = stretch
                    there, back = App.Matrix(), App.Matrix()
                    there.scale(*scale)
                    back.scale(*(1 / v for v in scale))
                    given = source.transformGeometry(there) if stretch != 1 else source
                    made = given.makeOffsetShape(-walls, tolerance, False, False, 0, join, False)
                    if made.isNull() or not made.Solids:
                        print(f"    {words}: OCCT made nothing", flush=True)
                        continue
                    inside = made.transformGeometry(back) if stretch != 1 else made
                    if stretch == 1:
                        # Flat faces moved its walls along the layers too: moved back, copies
                        # along the layers, exact on a part whose faces are upright or flat.
                        step = as_printed._vector(layers, walls)
                        inside = inside.fuse([inside.translated(step),
                                              inside.translated(step * -1)]).removeSplitter()
                    core = as_printed._one(as_printed._solids(inside.common(shells)))
                except Exception as exc:  # noqa: BLE001
                    print(f"    {words}: failed, {stopped(exc)} "
                          f"({time.time() - began_at:.0f} s)", flush=True)
                    continue
                check = as_printed.checked(own, core, layers, walls, top, bottom, up, open_by)
                found = as_printed.slivers(core, bead) if core is not None else []
                print(f"    {words}: made in {time.time() - began_at:.0f} s; checked at "
                      f"{check.read} of {check.planes} planes, short {100 * check.short:.1f}% "
                      f"and over {100 * check.over:.1f}% of its walls: "
                      f"{check.fails() or 'holds'}; slivers {len(found)}", flush=True)

    def thinness(k: int, own: Any, layers: int, settings: Any) -> None:
        """Where the core is too thin for Orca's sparse infill, layer by layer, and whether the
        slivers the cut leaves lie there (mqucifer/print-shop#363)."""
        bead = float(settings.line_width or 0)
        height = float(settings.layer_height or 0)
        walls = as_printed.wall_thickness(int(settings.walls or 0), bead, height)
        top = float(settings.top or 0) * height
        bottom = float(settings.bottom or 0) * height
        up = -1.0 if (settings.up or "+")[0] == "-" else 1.0
        spacing = bead - height * (1 - math.pi / 4)
        # Orca's PerimeterGenerator.cpp:1658-1663: the area inside the last wall opened by half
        # of min_perimeter_infill_spacing, 0.6 of the solid infill's spacing; and a bead's.
        radii = {"Orca's": 0.6 * spacing / 2, "a bead's": bead / 2}
        stage(f"part {k}: its core as cut now, or the copies mitred where that's refused")
        try:
            core = as_printed.cut_core(own, layers, walls, top, bottom, up, bead, f"part{k}").core
            how = "the cut holds"
        except Exception as exc:  # noqa: BLE001
            how = f"refused ({type(exc).__name__}); the copies mitred instead"
            inside = as_printed.eroded_in_plane(own, layers, walls)
            prisms, _ = as_printed.mitres(own, layers, walls)
            if prisms and inside is not None:
                inside = as_printed._one(as_printed._solids(inside.cut(Part.makeCompound(prisms))))
            shelled = as_printed.eroded_along(own, layers, top, bottom, up)
            core = None
            if inside is not None and shelled is not None:
                core = as_printed._one(as_printed._solids(
                    inside.removeSplitter().common(shelled)))
        found = as_printed.slivers(core, bead) if core is not None else []
        print(f"  {how}; slivers {len(found)}", flush=True)

        def layer(at: float) -> tuple[list[Any], list[Any]]:
            """The area inside the walls at `at`, and the part of it its shells leave."""
            section = as_printed._section(own, layers, at)
            if section is None:
                return [], []
            # Each face offset on its own, a face OCCT can't offset that isn't thinner than
            # the walls raising, never read as gone.
            inner, _ = as_printed._inside_walls(own, layers, at, walls)
            sparse = list(inner)
            for along in (top * up, -bottom * up):
                other = as_printed._section(own, layers, at + along)
                if other is None:
                    return inner, []
                moved = as_printed._moved_to(other, layers, at)
                sparse = [f for g in sparse for f in g.common(moved).Faces if f.Area > 1e-9]
            return inner, sparse

        def opened(faces: list[Any], radius: float) -> list[Any]:
            return list(as_printed.opened(faces, radius, layers))

        stage(f"part {k}: each layer's core opened as Orca opens it, every 0.5 mm")
        low, high = as_printed._span(own, layers)
        grid = [low + 0.25 + 0.5 * i for i in range(int((high - low) / 0.5))]
        for name, radius in radii.items():
            with_gaps, regions, stable, moving, unread = 0, 0, 0, 0, 0
            last = None
            for at in grid:
                try:
                    inner, _ = layer(at)
                    kept = opened(inner, radius)
                except Exception:  # noqa: BLE001 - a layer OCCT can't offset isn't read
                    unread += 1
                    last = None
                    continue
                gap = []
                for face in inner:
                    rest = face.cut(Part.makeCompound(kept)) if kept else face
                    # Not the corners any opening rounds, a fifth of its radius squared each.
                    gap += [f for f in rest.Faces if f.Area > radius**2]
                if gap:
                    with_gaps += 1
                    regions += len(gap)
                    here = Part.makeCompound(gap)
                    if last is not None:
                        before = as_printed._moved_to(last, layers, at)
                        apart = here.cut(before).Area + before.cut(here).Area
                        if apart < 0.1 * max(here.Area, 1e-9):
                            stable += 1
                        else:
                            moving += 1
                    last = here
                else:
                    last = None
            print(f"  {name} opening: {with_gaps} of {len(grid)} layers have a gap, "
                  f"{regions} gap region(s) in all; from layer to layer {stable} the same and "
                  f"{moving} moved; {unread} layer(s) OCCT couldn't open, not read", flush=True)

        stage(f"part {k}: where its slivers lie")
        counted: collections.Counter[str] = collections.Counter()
        for face in found:
            centre = face.CenterOfMass
            normal = face.normalAt(*face.Surface.parameter(centre))
            point = centre - normal * 0.02
            at = (point.x, point.y, point.z)[layers]
            here = Part.Vertex(point)
            try:
                inner, sparse = layer(at)
                for radius in radii.values():
                    opened(sparse, radius)
            except Exception:  # noqa: BLE001
                counted["in a layer OCCT couldn't open, not read"] += 1
                continue

            def holds(faces: list[Any], here: Any = here) -> bool:
                return any(f.distToShape(here)[0] < 1e-3 for f in faces)

            verdict = []
            if not holds(inner):
                verdict.append("outside the walls' own")
            for name, radius in radii.items():
                if holds(sparse) and not holds(opened(sparse, radius)):
                    verdict.append(f"in a gap by {name} opening")
            islands = [f for f in opened(sparse, radii["Orca's"]) if f.distToShape(here)[0] < 1e-3]
            if islands and all(f.Area < 15.0 for f in islands):
                verdict.append("in an island under 15 mm²")
            counted["; ".join(verdict) or "in sparse infill, by any of those"] += 1
        for words, n in counted.most_common():
            print(f"    {n} sliver(s): {words}", flush=True)

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
    if mode:
        for k, part in printed:
            if not part.printed.settings.missing():
                (thinness if mode == "thin" else offsets)(
                    k, owns[k], load.AXES.index(part.layers), part.printed.settings)
        return
    for k, part in printed:
        settings = part.printed.settings
        if settings.missing():
            continue
        dissect(k, owns[k], load.AXES.index(part.layers), settings)
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
                      + (f", checked at {cell.check.read} of {cell.check.planes} planes, short "
                         f"by {100 * cell.check.short:.1f}% of its walls and over by "
                         f"{100 * cell.check.over:.1f}% at most"
                         if cell.check is not None else ""), flush=True)  # fmt: skip
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
    mode = ""
    for flag in ("--thin", "--offsets"):
        if flag in words:
            words.remove(flag)
            mode = flag[2:]
    mesh = None
    if "--mesh" in words:
        k = words.index("--mesh")
        mesh = float(words[k + 1])
        del words[k : k + 2]
    if len(words) != 1 or not words[0].endswith(".json"):
        print("give the assembly: as_printed_probe.py ASSEMBLY.json [--mesh MM] "
              "[--thin | --offsets]")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    code = "import sys\nfrom typing import Any\n" + inspect.getsource(inner)
    code += f"\ninner({words[0]!r}, {mesh!r}, {mode!r})\n"
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
