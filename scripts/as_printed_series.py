#!/usr/bin/env python3
"""Why a printed part's mixed elements are read in series, on an assembly of one's own
(mqucifer/print-shop#363), in counts only.

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_series.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It copies the assembly and the files it names into a folder of its own and runs `load` there in
the bench's second image, under an hour's limit, watching each mixed element's blend being
chosen (`classify.band_normals`). For each read in series, every way, it notes: why (no way
parted its points, or a third face leans along the edge the two furthest apart share), how many
of the part's own faces its `classify.NEAREST` nearest boundary triangles lie on, each triangle
put on the face its middle is nearest, by its exact distance, those faces' kinds
together (a plane, a cylinder, a B-spline), the kind of the face nearest it, and its core share.
It says each as counts, and nothing of any part's name, shape or place.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from print_shop.bench import host

sys.path.insert(0, str(Path(__file__).parent))
from as_printed_profile import files_of  # noqa: E402

RUNNER = r'''
import json, math, sys
sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")
import FreeCAD as App  # noqa: F401 - before Part
import numpy as np
from bench import classify, load
from bench.cli import main

CONTEXT = {}
FOUND = []
original_bands, original_skin = classify.band_normals, load._Model._skin_layer


def skin_layer(self, k, layered, points, row, elements, to_own):
    CONTEXT.update(solid=self.solids[k], to_own=to_own)
    return original_skin(self, k, layered, points, row, elements, to_own)


def nearest_face(solid, where):
    """Each point's nearest of the solid's faces, by its exact distance, among the faces whose
    box, grown half a millimetre, holds it."""
    import Part

    boxes = []
    for face in solid.Faces:
        box = face.BoundBox
        boxes.append((box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax))
    boxes = np.array(boxes)
    out = np.empty(len(where), dtype=int)
    for i, p in enumerate(where):
        near = np.flatnonzero(np.all(p >= boxes[:, :3] - 0.5, axis=1)
                              & np.all(p <= boxes[:, 3:] + 0.5, axis=1))
        if not len(near):
            near = np.arange(len(solid.Faces))
        vertex = Part.Vertex(App.Vector(*p))
        out[i] = min(near, key=lambda f: solid.Faces[f].distToShape(vertex)[0])
    return out


def bands(read, mixed, centres, faces, normals, group_of=None):
    ways, kinds = original_bands(read, mixed, centres, faces, normals, group_of)
    solid, to_own = CONTEXT["solid"], CONTEXT["to_own"]
    base = to_own(np.zeros((1, 3)))[0]
    turn = to_own(np.eye(3)) - base
    back = np.linalg.inv(turn)
    series = np.flatnonzero(mixed & (kinds == classify.SERIES))
    record = {"mixed": int(mixed.sum()), "series": len(series), "rows": []}
    if len(series):
        count = min(classify.NEAREST, len(faces))
        kind_of = [type(f.Surface).__name__ for f in solid.Faces]
        for start in range(0, len(series), 256):
            chunk = series[start:start + 256]
            apart = ((centres[chunk][:, None, :] - faces[None]) ** 2).sum(axis=2)
            near = np.argpartition(apart, count - 1, axis=1)[:, :count]
            closest = near[np.arange(len(chunk)),
                           np.take_along_axis(apart, near, axis=1).argmin(axis=1)]
            wanted, back_to = np.unique(near.reshape(-1), return_inverse=True)
            labelled = nearest_face(solid, (faces[wanted] - base) @ back)
            on = labelled[back_to].reshape(len(chunk), count)
            nearest = labelled[np.searchsorted(wanted, closest)]
            for row, e in enumerate(chunk):
                distinct = sorted(set(on[row].tolist()))
                record["rows"].append({
                    "why": "no way" if not ways[e].any() else "a third face leaning",
                    "faces": len(distinct),
                    "kinds": "+".join(sorted(kind_of[f] for f in distinct)),
                    "nearest": kind_of[int(nearest[row])],
                    "share": float(read.share[e]),
                })
    FOUND.append(record)
    return ways, kinds


classify.band_normals, load._Model._skin_layer = bands, skin_layer
try:
    code = main(["load", *sys.argv[1:]])
finally:
    json.dump(FOUND, open("series.json", "w"))
sys.exit(code)
'''


def tally(values: list[str]) -> str:
    """Each value's count, the most first."""
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return ", ".join(f"{k} {n}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def bins(values: list[float], edges: list[float], unit: str = "") -> str:
    """How many of `values` in each bin between `edges`."""
    counts = [0] * (len(edges) + 1)
    for v in values:
        k = sum(v >= e for e in edges)
        counts[k] += 1
    labels = [f"<{edges[0]}{unit}"]
    labels += [f"{a}-{b}{unit}" for a, b in zip(edges, edges[1:], strict=False)]
    labels.append(f">={edges[-1]}{unit}")
    return ", ".join(f"{label}: {c}" for label, c in zip(labels, counts, strict=True) if c)


def main(argv: list[str]) -> int:
    if not argv or not argv[0].endswith(".json"):
        print("give the assembly and load's words: as_printed_series.py ASSEMBLY.json ...")
        return 2
    source = Path.cwd()
    work = Path(tempfile.mkdtemp(prefix="as-printed-series-"))
    try:
        folder = Path(argv[0]).parent
        for path in [Path(argv[0]), *(folder / f for f in files_of(source / argv[0]))]:
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source / path, work / path)
        (work / "series_load.py").write_text(RUNNER)
        done = host.run(["series_load.py", *argv], work, timeout=3600, python=True,
                        image=host.CAE_IMAGE)  # fmt: skip
        verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER|UNREAD)", done.stdout, re.M)
        print(f"verdict: {verdict.group(1) if verdict else 'none said'}")
        found_file = work / "series.json"
        if not found_file.exists():
            print("nothing watched")
            return 1
        found = json.loads(found_file.read_text())
        for mesh, record in zip(("the mesh", "the coarser mesh"), found, strict=False):
            rows = record["rows"]
            print(f"{mesh}: {record['series']} in series of {record['mixed']} mixed")
            if not rows:
                continue
            print("  why: " + tally([r["why"] for r in rows]))
            print("  how many of the part's faces their nearest triangles lie on: "
                  + tally([str(r["faces"]) for r in rows]))
            print("  those faces' kinds: " + tally([r["kinds"] for r in rows]))
            print("  their nearest face's kind: " + tally([r["nearest"] for r in rows]))
            shares = [r["share"] for r in rows]
            print("  their core share: " + bins(shares, [0.1, 0.3, 0.5, 0.7, 0.9]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
