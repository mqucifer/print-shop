#!/usr/bin/env python3
"""Why a printed part's faces have no wedge as leaving the part, on an assembly of one's own
(mqucifer/print-shop#363), in counts only.

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_leaving.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It copies the assembly and the files it names into a folder of its own and runs `load` there in
the bench's second image, under an hour's limit, watching the skin's wedges being laid
(`wedges.layer`). For each triangle of a printed part's faces that has none because a wedge's
inner node would leave the part, it notes: the kind of the part's face it's on (its surface's
type, a plane, a cylinder, a B-spline), how far it leans toward the layers, whether it borders a
held face, whether its node is out of its layer's section or past the part's end layers, how
far out, and where along the part's height. And round the place its core is read worst, the
faces within a cell of it, by what each has: a wedge, none as leaving, held, or thin. It says
each as counts in bins, and nothing of any part's name, shape or place.
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
import json, sys
from collections import Counter
sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")
import FreeCAD as App  # noqa: F401 - before Part
import Part
import numpy as np
from bench import load, wedges
from bench.cli import main

CONTEXT = {}
FOUND = []
original_layer, original_skin = wedges.layer, load._Model._skin_layer


def skin_layer(self, k, layered, points, row, elements, to_own):
    CONTEXT.update(solid=self.solids[k], to_own=to_own, layered=layered)
    return original_skin(self, k, layered, points, row, elements, to_own)


def layer(points, rows, normals, depth, cells, over_core, inside, planes=None):
    built = original_layer(points, rows, normals, depth, cells, over_core, inside, planes)
    import shapely
    layered, to_own, solid = CONTEXT["layered"], CONTEXT["to_own"], CONTEXT["solid"]
    held = np.zeros(len(rows), dtype=bool) if planes is None else planes
    middles = points[rows[:, :3]].mean(axis=1)
    kept = {tuple(np.round(m, 6)) for m in built.middles}
    has = np.array([tuple(np.round(m, 6)) in kept for m in middles])
    keep = over_core & ~held
    both = np.flatnonzero(keep | held)
    offsets, _ = wedges.inner_nodes(points, rows[both], normals[both],
                                    np.where(held[both], 0.0, depth[both]))
    span = layered.high - layered.low
    dropped = np.flatnonzero(keep & ~has)
    held_nodes = {n for r in rows[held].tolist() for n in r}
    status = np.where(~over_core, "thin", "leaving")
    status = np.where(held, "held", status)
    status = np.where(has, "wedge", status)
    record = {"status": status.tolist(), "middles": middles.tolist(), "rows": [],
              "only_shallower": 0}
    for t in dropped:
        out = []
        for n in rows[t]:
            if n not in offsets:
                continue
            own = to_own(np.asarray(offsets[n], dtype=float)[None])[0]
            if not inside(np.asarray(offsets[n], dtype=float)[None])[0]:
                out.append(own)
        if not out:
            # Its nodes at its full depth are in the part: it left once made shallower.
            record["only_shallower"] += 1
            continue
        own = out[0]
        along = float(own[layered.layers])
        if along < layered.low or along > layered.high:
            how = "past the end layers"
            far = min(abs(along - layered.low), abs(along - layered.high))
        else:
            first, second = (a for a in range(3) if a != layered.layers)
            k = int(layered.layer_of(np.array([along]))[0])
            section = layered.mesh().section(layered.low + (k + 0.5) * layered.height)
            how = "out of its layer's section"
            point = shapely.Point(own[first], own[second])
            far = float(section.distance(point)) if not section.is_empty else float("nan")
        vertex = Part.Vertex(App.Vector(*middles[t]))
        nearest = min(solid.Faces, key=lambda f: f.distToShape(vertex)[0])
        own_normal = to_own(normals[t][None])[0] - to_own(np.zeros((1, 3)))[0]
        record["rows"].append({
            "kind": type(nearest.Surface).__name__,
            "lean": float(abs(own_normal[layered.layers])),
            "borders_held": bool(set(rows[t].tolist()) & held_nodes),
            "how": how, "far_mm": far,
            "height": (float(to_own(middles[t][None])[0][layered.layers]) - layered.low) / span
                      if span else 0.0,
        })
    FOUND.append(record)
    return built


wedges.layer, load._Model._skin_layer = layer, skin_layer
try:
    code = main(["load", *sys.argv[1:]])
finally:
    json.dump(FOUND, open("leaving.json", "w"))
sys.exit(code)
'''


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
        print("give the assembly and load's words: as_printed_leaving.py ASSEMBLY.json ...")
        return 2
    source = Path.cwd()
    work = Path(tempfile.mkdtemp(prefix="as-printed-leaving-"))
    try:
        folder = Path(argv[0]).parent
        for path in [Path(argv[0]), *(folder / f for f in files_of(source / argv[0]))]:
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source / path, work / path)
        (work / "leaving_load.py").write_text(RUNNER)
        done = host.run(["leaving_load.py", *argv], work, timeout=3600, python=True,
                        image=host.CAE_IMAGE)  # fmt: skip
        verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER|UNREAD)", done.stdout, re.M)
        print(f"verdict: {verdict.group(1) if verdict else 'none said'}")
        found_file = work / "leaving.json"
        if not found_file.exists():
            print("nothing watched")
            return 1
        found = json.loads(found_file.read_text())
        saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        numbers = (json.loads((work / f"{saved.group(1)}.json").read_text()).get("numbers", {})
                   if saved else {})  # fmt: skip
        for mesh, record in zip(("the mesh", "the coarser mesh"), found, strict=False):
            rows = record["rows"]
            print(f"{mesh}: {len(rows)} faces leaving the part at their full depth, "
                  f"{record.get('only_shallower', 0)} only once made shallower")
            if not rows:
                continue
            kinds: dict[str, int] = {}
            for r in rows:
                kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            print("  by their face's kind: "
                  + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))
            print("  by their lean to the layers (sine): "
                  + bins([r["lean"] for r in rows], [0.05, 0.2, 0.5, 0.9]))
            held = sum(r["borders_held"] for r in rows)
            print(f"  bordering a held face: {held}, not: {len(rows) - held}")
            hows: dict[str, int] = {}
            for r in rows:
                hows[r["how"]] = hows.get(r["how"], 0) + 1
            print("  their node: " + ", ".join(f"{k} {n}" for k, n in hows.items()))
            far = [r["far_mm"] for r in rows if r["far_mm"] == r["far_mm"]]
            print("  how far out, mm: " + bins(far, [0.005, 0.02, 0.1, 0.5]))
            print("  along the part's height, as a share: "
                  + bins([r["height"] for r in rows], [0.1, 0.25, 0.5, 0.75, 0.9]))
        # Round the core's worst place: the faces within a cell of it, by what each has.
        for part in numbers.get("parts", {}).values():
            for core in part.get("cores", []):
                at, cell = core.get("stretch_at"), core.get("cell_mm") or 0
                if not at or not found:
                    continue
                import numpy as np

                record = found[0]
                middles = np.array(record["middles"])
                near = np.linalg.norm(middles - np.array(at), axis=1) <= 2 * cell
                status: dict[str, int] = {}
                for s in np.array(record["status"])[near]:
                    status[str(s)] = status.get(str(s), 0) + 1
                print("faces within two cells of the core's worst stretch: "
                      + ", ".join(f"{k} {n}" for k, n in sorted(status.items())))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
