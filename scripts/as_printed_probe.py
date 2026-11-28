#!/usr/bin/env python3
"""What reading a part as printed costs, on an assembly of one's own (mqucifer/print-shop#363).

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_probe.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It runs `load` as the bench does, in its second image under `load`'s own limits
(`host.memory_for`, `host.timeout_for`), and says of it only counts, verdicts and seconds:

- the mesh's elements and nodes, the seconds it meshed in and solved in, the seconds in all,
  and the most memory the container held;
- the verdict's word: OK, FAIL, DISAGREE or NO ANSWER, and for NO ANSWER the kind of its reason:
  the container's memory, an element turned inside out, two passes, the time, or else its
  first words, each part's name put as its number and every figure as #;
- the step `load` was in when it stopped, by a name of its own (`STEPS`), never a part's, and
  the mesh's elements and nodes as soon as it said them;
- for each printed part, by its number, and each of its cells, by its number: how many of its
  elements are all skin, all core, a laminate along a face, a column along an edge and in
  series where three faces meet, and how far its mesh's core is from Orca's layers' in its
  bands, short and over, as a share of a band; its skin's wedges, its faces with none and why,
  and how many were made shallower; each core's figures over its cell, and at its elements.

Nothing of any part's name, shape or place is printed. `load`'s own reply stays in the folder's
`bench-out/`, as any `load`'s does.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from print_shop.bench import host

#: `load`'s steps, by the words each begins with, and what this says of each: none names a part.
STEPS = (
    ("placing the part", "placing a part by its turn"),
    ("reading the part", "reading a part"),
    ("joining the parts", "joining the parts"),
    ("finding the faces the assembly is held by", "finding the held faces"),
    ("finding each through joint", "finding the bolts' ways"),
    ("reading the dovetail", "reading a dovetail"),
    ("taking the dovetail", "taking a dovetail apart"),
    ("meshing and solving the joined parts", "meshing and solving"),
    ("meshing again straight-sided", "meshing again straight-sided"),
    ("gmsh's mesh of the joined parts", "gmsh's mesh"),
    ("finding faces closer", "finding close faces"),
    ("reading each element of the printed parts", "reading each element by its layers"),
    ("writing CalculiX's input", "writing CalculiX's input"),
    ("CalculiX's iterative solve", "CalculiX's iterative solve"),
    ("CalculiX's solve", "CalculiX's direct solve"),
    ("reading the joint", "reading a joint"),
    ("reading the loaded places", "reading the loaded places"),
)
MESHED = re.compile(r"a mesh of (\d+) elements and (\d+) nodes")


def step_of(text: str) -> str:
    """A step's own name for `load`'s words for it, or "another step"."""
    if text.startswith("between steps, after "):
        return "between steps, after " + step_of(text.removeprefix("between steps, after "))
    return next((name for words, name in STEPS if text.startswith(words)), "another step")


def scrubbed(reason: str, names: list[str], words: int = 24) -> str:
    """A refusal's first `words`, each part's name its number and every figure #."""
    ordered = sorted(enumerate(names, 1), key=lambda n: -len(n[1]))
    for k, name in ordered:
        reason = reason.replace(name, f"\0{chr(0x100 + k)}\0")
    reason = re.sub(r"\d+(\.\d+)?", "#", reason)
    for k, _ in ordered:
        reason = reason.replace(f"\0{chr(0x100 + k)}\0", f"part {k}")
    return " ".join(reason.split()[:words])


#: The kinds a refusal is told by, in order: the first whose words it has.
KINDS = (
    ("the container's memory", ("container's memory", "memory")),
    ("an element turned inside out", ("inside out",)),
    ("two passes", ("two passes", "two (")),
    ("the time", ("timed out", "no answer in", "time")),
)


def kind_of(reason: str) -> str:
    """A refusal's kind, from its words, never the words themselves."""
    low = reason.lower()
    return next((kind for kind, words in KINDS if any(w in low for w in words)), "other")


def main(argv: list[str]) -> int:
    if not argv or not argv[0].endswith(".json"):
        print("give the assembly and load's words: as_printed_probe.py ASSEMBLY.json --weight KG "
              "--at X,Y,Z [...]")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    work = Path.cwd()
    args = ["load", *argv]
    printed = host.reads_printed(args, work)
    print(f"read as printed: {'yes' if printed else 'no'}; limits "
          f"{host.memory_for(args)} and {host.timeout_for(args)} s", flush=True)
    names = [str(p.get("name")) for p in json.loads((work / argv[0]).read_text()).get("parts", [])]
    began = time.time()
    trace: list[str] = []
    done = host.run(args, work, trace=trace)
    seconds = time.time() - began
    text = done.stdout
    peak = re.search(r"memory\.peak (\d+)", text)
    peaks = [int(m.group(1)) for line in trace for m in [re.search(r"peak=(\d+)", line)] if m]
    held = int(peak.group(1)) if peak else max(peaks, default=0)
    print(f"seconds in all: {seconds:.0f}; the most memory held: "
          + (f"{held / 2**30:.2f} GiB" if held else "not said"), flush=True)
    steps = [line.split(" ", 3) for line in trace if line.startswith(host.STEP_LINE + " ")]
    steps = [s for s in steps if len(s) == 4]
    for line in trace:
        found = MESHED.search(line)
        if found:
            print(f"mesh: {found.group(1)} elements, {found.group(2)} nodes", flush=True)
            break
    if steps:
        _, _, at, what = steps[-1]
        print(f"its last step: {step_of(what)}, from {float(at):.0f} s in", flush=True)
    verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER)", text, re.M)
    if verdict is None:
        print(f"verdict: none said (exit {done.returncode})")
        return 1
    word = verdict.group(1)
    if word == "NO ANSWER":
        line = text[verdict.start() : text.find("\n", verdict.start())]
        kind = kind_of(line)
        if kind == "other":
            kind += f": {scrubbed(line.removeprefix('verdict: NO ANSWER:'), names)}"
        print(f"verdict: NO ANSWER, its reason's kind: {kind}")
    else:
        print(f"verdict: {word}")
    meshed = re.search(r"\((\d+) nodes, (\d+) elements, meshed in ([\d.]+) s and solved in "
                       r"([\d.]+) s\)", text)  # fmt: skip
    if meshed:
        print(f"meshed in {meshed.group(3)} s, solved in {meshed.group(4)} s", flush=True)
    saved = re.search(r"saved: (\S+)\.txt and \.json", text)
    if saved is None:
        return 0
    record = json.loads((work / f"{saved.group(1)}.json").read_text())
    numbers = record.get("numbers", {})
    for name, part in numbers.get("parts", {}).items():
        k = names.index(name) + 1 if name in names else 0
        for number, core in enumerate(part.get("cores", []), 1):
            print(f"part {k}, core {number}: across its layers "
                  f"{core.get('across_layers_pct', 0):.3f}% of {core.get('across_limit_pct')}%, "
                  f"stretch {core.get('stretch_pct', 0):.3f}% of "
                  f"{core.get('stretch_limit_pct', 0):.3g}%, its cell "
                  f"{core.get('across_cell_fill')} and {core.get('stretch_cell_fill')} core; "
                  f"at its elements {core.get('at_elements_across_layers_pct', 0):.3f}% and "
                  f"{core.get('at_elements_stretch_pct', 0):.3f}%", flush=True)
    for name, cells in numbers.get("printed", {}).items():
        k = names.index(name) + 1 if name in names else 0
        for number, cell in enumerate(cells, 1):
            print(f"part {k}, cell {number} of {len(cells)}: elements "
                  f"{cell.get('elements')}: all skin {cell.get('skin_elements')}, all core "
                  f"{cell.get('core_elements')}, laminate {cell.get('laminate_elements')}, column "
                  f"{cell.get('column_elements')}, in series {cell.get('series_elements')}; its "
                  f"core against Orca's layers in {cell.get('bands')} bands: short "
                  f"{cell.get('band_short_pct')}%, over {cell.get('band_over_pct')}%; layers "
                  f"unread {cell.get('layers_unread')}; its skin {cell.get('wedges')} wedges of "
                  f"the part's {cell.get('part_wedges')} on {cell.get('faces')} faces, none on "
                  f"{cell.get('faces_held')} held, {cell.get('faces_thin')} thin, "
                  f"{cell.get('faces_outside')} leaving the part, {cell.get('faces_inverted')} "
                  f"turned over; {cell.get('wedges_thinned')} made shallower; its held faces "
                  f"meshed at {cell.get('held_mesh_mm') or 'the mesh own'} mm, "
                  f"{cell.get('held_zone_mm') or 0} mm deep, its finest cell met "
                  f"{cell.get('held_finest_met')}; its straight elements "
                  f"{cell.get('straight_short_pct')}% under the curved mesh; "
                  f"{cell.get('layers_read')} of its {cell.get('layers')} layers read, every "
                  f"{cell.get('layers_stride_mm')} mm", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
