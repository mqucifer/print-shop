#!/usr/bin/env python3
"""Where a read as printed spends its time, on an assembly of one's own (mqucifer/print-shop#363).

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_profile.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It copies the assembly file and the files it names into a folder of its own, so nothing is
written beside them, and runs `load` there in the bench's second image under Python's profiler,
with an hour's limit. It says only seconds and counts:

- the seconds in all, and in each kind of work: OCCT's slicing, its offsets, its booleans, the
  layers' rings, the exact clipping of each element and wedge, the skin's wedges, the core's
  average over its cell, CalculiX's solves, gmsh's mesh, and the rest;
- each step's seconds, by the probe's own names for them (`as_printed_probe.STEPS`);
- each printed cell's layers and how many were read, its elements and wedges;
- the verdict's word, and the eight functions most of its time is in, by the code's names.

Nothing of any part's name, shape or place is printed.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from print_shop.bench import host

sys.path.insert(0, str(Path(__file__).parent))
from as_printed_probe import step_of  # noqa: E402

#: Each kind of work, by the functions its time is counted in (their own time, `tottime`).
KINDS = (
    ("OCCT's slicing", ("slice",)),
    ("OCCT's offsets", ("makeOffset2D",)),
    ("OCCT's booleans", ("common", "cut", "fuse", "removeSplitter")),
    ("OCCT's faces", ("makeFace", "Face", "discretize")),
    ("the exact clipping", ("inside_rings", "clip", "sections", "area", "overlapping")),
    ("the skin's wedges", ("layer", "inner_nodes", "within", "covered", "lowest_jacobian",
                           "faces_of", "strains", "forces", "stiffness", "_in_space")),
    ("the core's cell average", ("element_averaged",)),
    ("the layers' points", ("inside", "core", "cell_of", "_in_region")),
    ("CalculiX and gmsh, waited on", ("communicate", "wait", "poll", "select", "read",
                                       "_wait_for_tstate_lock", "acquire")),
)
#: The profiled run, as the image runs it: `load` under cProfile, its own seconds by function
#: written beside its reply.
RUNNER = '''
import cProfile, json, pstats, sys
sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")
import FreeCAD  # noqa: F401 - before Part
from bench.cli import main
profile = cProfile.Profile()
profile.enable()
try:
    code = main(["load", *sys.argv[1:]])
finally:
    profile.disable()
    stats = pstats.Stats(profile)
    own = {}
    for (_, _, name), (_, calls, tottime, _, _) in stats.stats.items():
        held = own.setdefault(name, [0, 0.0])
        held[0] += calls
        held[1] += tottime
    json.dump(own, open("profile.json", "w"))
sys.exit(code)
'''


def files_of(assembly: Path) -> list[str]:
    """The files an assembly names, relative to its folder: each part's, each region's."""
    data = json.loads(assembly.read_text())
    named = []
    for part in data.get("parts", []):
        if part.get("file"):
            named.append(part["file"])
        for region in (part.get("print") or {}).get("regions", []):
            if isinstance(region.get("mesh"), str):
                named.append(region["mesh"])
    return named


def main(argv: list[str]) -> int:
    if not argv or not argv[0].endswith(".json"):
        print("give the assembly and load's words: as_printed_profile.py ASSEMBLY.json ...")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    source = Path.cwd()
    assembly = source / argv[0]
    work = Path(tempfile.mkdtemp(prefix="as-printed-profile-"))
    try:
        folder = Path(argv[0]).parent
        for path in [Path(argv[0]), *(folder / f for f in files_of(assembly))]:
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(source / path, work / path)
        (work / "profile_load.py").write_text(RUNNER)
        trace: list[str] = []
        began = time.time()
        done = host.run(["profile_load.py", *argv], work, timeout=3600, python=True,
                        image=host.CAE_IMAGE, trace=trace)  # fmt: skip
        print(f"seconds in all: {time.time() - began:.0f}", flush=True)
        verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER|UNREAD)", done.stdout, re.M)
        print(f"verdict: {verdict.group(1) if verdict else 'none said'}")
        steps = [line.split(" ", 3) for line in trace if line.startswith(host.STEP_LINE + " ")]
        steps = [s for s in steps if len(s) == 4]
        spent: dict[str, float] = {}
        for (_, _, at, what), (_, _, then, _) in zip(steps, steps[1:], strict=False):
            name = step_of(what)
            spent[name] = spent.get(name, 0.0) + float(then) - float(at)
        for said, seconds in sorted(spent.items(), key=lambda kv: -kv[1]):
            if seconds >= 1:
                print(f"step {said}: {seconds:.0f} s")
        own_file = work / "profile.json"
        if own_file.exists():
            own = json.loads(own_file.read_text())
            counted: set[str] = set()
            for kind, names in KINDS:
                # A built-in method is named as `<method 'slice' of 'Part.Shape' objects>`.
                mine = [k for k in own if k not in counted
                        and any(k == n or f"'{n}'" in k for n in names)]  # fmt: skip
                calls = sum(own[k][0] for k in mine)
                seconds = sum(own[k][1] for k in mine)
                counted.update(mine)
                print(f"{kind}: {seconds:.1f} s over {calls} calls")
            rest = sum(v[1] for k, v in own.items() if k not in counted)
            print(f"the rest: {rest:.1f} s")
            # The functions most of the time is in, by the code's own names, never the part's.
            for name, (calls, seconds) in sorted(own.items(), key=lambda kv: -kv[1][1])[:8]:
                print(f"  in {name}: {seconds:.1f} s over {calls} calls")
        saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        if saved:
            numbers = json.loads((work / f"{saved.group(1)}.json").read_text()).get("numbers", {})
            for number, cells in enumerate(numbers.get("printed", {}).values(), 1):
                for k, cell in enumerate(cells, 1):
                    print(f"part {number}, cell {k}: {cell.get('layers_read')} of "
                          f"{cell.get('layers')} layers read, every "
                          f"{cell.get('layers_stride_mm')} mm; {cell.get('elements')} elements, "
                          f"{cell.get('wedges')} wedges")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
