#!/usr/bin/env python3
"""What a printed part's core is read from where its strain across its layers is worst, on an
assembly of one's own (mqucifer/print-shop#363), in counts and shares only.

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_across.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It copies the assembly and the files it names into a folder of its own and runs `load` there in
the bench's second image, under an hour's limit, watching each core's strain being averaged
over its cell (`classify.element_averaged`). For each mesh and each printed part's core, at its
worst cell across its layers and at its worst stretch, each as `load` picked it, it says: where
that cell is along the part's height, as a share of it; how far it is from a held face, in
cells; the kind of the part's face nearest it, how far off, and how far that face leans toward
the layers; and what the cell's average is made of, by each blend its elements' cores are read
from (all core, a laminate across a face, a column along an edge, in series), each one's share
of the cell's weight and its mean strain across the layers or its stretch, and for a laminate
how far its face leans. And how far apart the two meshes' worst cells are, in cells. Nothing
of any part's name, shape or place is printed.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

from print_shop.bench import host

sys.path.insert(0, str(Path(__file__).parent))
from as_printed_profile import files_of  # noqa: E402

RUNNER = r'''
import inspect, json, sys, textwrap
sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")
import FreeCAD as App  # noqa: F401 - before Part
import Part
import numpy as np
from bench import classify, load
from bench.cli import main

KINDS = {classify.LAMINATE: "a laminate", classify.COLUMN: "a column",
         classify.SERIES: "in series"}
GROUPS = []
TAGS = []
CAUGHT = []
FOUND = []
READS = []
original_strain, original_averaged = classify.core_strain, classify.element_averaged
original_classify = load._Model._classify


def core_strain(kind, skin, core, share, given=None):
    GROUPS.append((int(kind), float(share), None if given is None else np.asarray(given)))
    return original_strain(kind, skin, core, share, given)


def classify_wrapped(self, *args, **kwargs):
    GROUPS.clear()
    return original_classify(self, *args, **kwargs)


def averaged_wrapped(middles, strains, weights, cell):
    found = original_averaged(middles, strains, weights, cell)
    CAUGHT.append((middles, strains, weights, cell, found))
    return found


# `_core_read` as it is, each entry tagged with its group's number.
source = textwrap.dedent(inspect.getsource(load._Model._core_read))
marker = "means.append("
assert source.count(marker) == 1
source = source.replace(marker, "TAGS.append(np.full(int(live.sum()), number)); " + marker, 1)
space = dict(vars(load))
space["TAGS"] = TAGS
exec(compile(source, "core_read", "exec"), space)
original_core_read = space["_core_read"]


def core_read(self, part, cell, figures, numbers, way, first, second):
    TAGS.clear()
    CAUGHT.clear()
    read = original_core_read(self, part, cell, figures, numbers, way, first, second)
    if not CAUGHT or not TAGS:
        return read
    middles, strains, weights, size, averaged = CAUGHT[-1]
    tags = np.concatenate(TAGS)
    held = np.flatnonzero(weights > 0)
    where, strain, weight, tag = middles[held], strains[held], weights[held], tags[held]
    turn = part.matrix()
    own_way = way @ turn
    k = [p.name for p in self.assembly.parts].index(part.name)
    layered = self.cores[part.name]
    span = layered.high - layered.low
    faces = list(self.solids[k].Faces)
    held_faces = [*self.fixed, *sum(self.standing.values(), [])]
    READS.append(len(READS) + 1)
    for what, value_key, at_key, fill_key in (
            ("across its layers", "across_layers_pct", "across_at", "across_cell_fill"),
            ("stretch", "stretch_pct", "stretch_at", "stretch_cell_fill")):
        at = np.asarray(read[at_key], dtype=float)
        # Its cell as `load` picked it, in the part's own axes, as the entries are.
        centre = (at - np.array(part.at, dtype=float)) @ turn
        window = np.all((where - centre >= -size / 2) & (where - centre < size / 2), axis=1)
        by = {}
        for t, w, one in zip(tag[window], weight[window], strain[window]):
            kind, share, given = GROUPS[int(t)]
            name = "all core" if share >= 1 else KINDS[kind]
            if name == "a laminate" and given is not None:
                lean = abs(float(np.asarray(given) @ way))
                name += " across a face " + ("flat to the layers" if lean > 0.9 else
                                             "leaning to them" if lean > 0.3 else "upright")
            held_by = by.setdefault(name, [0.0, np.zeros((3, 3)), 0])
            held_by[0] += float(w)
            held_by[1] = held_by[1] + float(w) * one
            held_by[2] += 1
        total = sum(v[0] for v in by.values()) or 1.0

        def figure(tensor):
            if what == "stretch":
                return 100 * float(np.abs(np.linalg.eigvalsh(tensor)).max())
            return 100 * float(own_way @ tensor @ own_way)

        own = centre
        height = (float(own[layered.layers]) - layered.low) / span if span else 0.0
        vertex = Part.Vertex(App.Vector(*at))
        nearest = min(faces, key=lambda f: f.distToShape(vertex)[0])
        off, pairs, _ = nearest.distToShape(vertex)
        u, v = nearest.Surface.parameter(pairs[0][0])
        normal = nearest.normalAt(u, v)
        lean = abs(float(np.array([normal.x, normal.y, normal.z]) @ way))
        from_held = (min(f.distToShape(vertex)[0] for f in held_faces) / size
                     if held_faces else float("inf"))
        FOUND.append({
            "read": READS[-1], "what": what, "pct": float(read[value_key]),
            "fill": read.get(fill_key), "cell_mm": float(size), "height": height,
            "from_held_cells": float(from_held), "nearest": type(nearest.Surface).__name__,
            "nearest_lean": lean, "to_face_cells": float(off / size), "at": at.tolist(),
            "entries": int(window.sum()),
            "by": {name: [w / total, figure(t / w) if w else 0.0, n]
                   for name, (w, t, n) in by.items()},
        })
    return read


classify.core_strain, classify.element_averaged = core_strain, averaged_wrapped
load._Model._classify, load._Model._core_read = classify_wrapped, core_read
try:
    code = main(["load", *sys.argv[1:]])
finally:
    json.dump(FOUND, open("across.json", "w"))
sys.exit(code)
'''


def main(argv: list[str]) -> int:
    if not argv or not argv[0].endswith(".json"):
        print("give the assembly and load's words: as_printed_across.py ASSEMBLY.json ...")
        return 2
    source = Path.cwd()
    work = Path(tempfile.mkdtemp(prefix="as-printed-across-"))
    try:
        folder = Path(argv[0]).parent
        for path in [Path(argv[0]), *(folder / f for f in files_of(source / argv[0]))]:
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source / path, work / path)
        (work / "across_load.py").write_text(RUNNER)
        done = host.run(["across_load.py", *argv], work, timeout=3600, python=True,
                        image=host.CAE_IMAGE)  # fmt: skip
        verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER|UNREAD)", done.stdout, re.M)
        print(f"verdict: {verdict.group(1) if verdict else 'none said'}")
        found_file = work / "across.json"
        if not found_file.exists():
            print("nothing watched")
            return 1
        found = json.loads(found_file.read_text())
        reads = sorted({one["read"] for one in found})
        meshes = ("the mesh", "the coarser mesh")
        for one in found:
            mesh = (meshes[reads.index(one["read"])] if len(reads) == 2
                    else f"core read {one['read']}")
            print(f"{mesh}: the core's worst cell, {one['what']}, {one['pct']:.3f}%, its cell "
                  f"{one['fill']} core, of {one['entries']} entries")
            print(f"  along the part's height {one['height']:.2f} of it; "
                  f"{one['from_held_cells']:.1f} cells from a held face; its nearest face a "
                  f"{one['nearest']}, {one['to_face_cells']:.2f} cells off, leaning "
                  f"{one['nearest_lean']:.2f} toward the layers (sine)")
            ordered = sorted(one["by"].items(), key=lambda kv: -kv[1][0])
            for name, (share, mean, count) in ordered:
                print(f"  {name}: {100 * share:.0f}% of its weight, {count} entries, its mean "
                      f"{one['what']} {mean:.3f}%")
        if len(reads) == 2:
            for what in ("across its layers", "stretch"):
                two = [one for one in found if one["what"] == what]
                if len(two) == 2:
                    apart = np.linalg.norm(np.array(two[0]["at"]) - np.array(two[1]["at"]))
                    print(f"the two meshes' worst cells, {what}, "
                          f"{apart / two[0]['cell_mm']:.1f} cells apart")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
