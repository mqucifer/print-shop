#!/usr/bin/env python3
"""What reading a part as printed costs, on an assembly of one's own (mqucifer/print-shop#363).

Run from the folder the assembly is in, with the words `load` would be given:

    uv run python <print-shop>/scripts/as_printed_probe.py ASSEMBLY.json --weight KG --at X,Y,Z
        [any other of load's words]

It runs `load` as the bench does, in its second image under the limits a read as printed has
(`host.PRINTED_MEMORY`, `host.PRINTED`), and says of it only counts, verdicts and seconds:

- the mesh's elements and nodes, the seconds it meshed in and solved in, the seconds in all,
  and the most memory the container held;
- the verdict's word: OK, FAIL, DISAGREE or NO ANSWER, and for NO ANSWER the kind of its reason
  only: the container's memory, an element turned inside out, two passes, the time, or other;
- for each printed part, by its number, and each of its cells, by its number: how many of its
  elements are all skin, all core, a laminate along a face, a column along an edge and in
  series where three faces meet, and how far its mesh's core is from Orca's layers' in its
  bands, short and over, as a share of a band.

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
          f"{host.memory_for(args, printed)} and {host.timeout_for(args, printed)} s", flush=True)
    began = time.time()
    done = host.run(args, work)
    seconds = time.time() - began
    text = done.stdout
    peak = re.search(r"memory\.peak (\d+)", text)
    verdict = re.search(r"^verdict: (OK|FAIL|DISAGREE|NO ANSWER)", text, re.M)
    print(f"seconds in all: {seconds:.0f}; the most memory held: "
          + (f"{int(peak.group(1)) / 2**30:.2f} GiB" if peak else "not said"), flush=True)
    if verdict is None:
        print(f"verdict: none said (exit {done.returncode})")
        return 1
    word = verdict.group(1)
    if word == "NO ANSWER":
        line = text[verdict.start() : text.find("\n", verdict.start())]
        print(f"verdict: NO ANSWER, its reason's kind: {kind_of(line)}")
    else:
        print(f"verdict: {word}")
    meshed = re.search(r"\((\d+) nodes, (\d+) elements, meshed in ([\d.]+) s and solved in "
                       r"([\d.]+) s\)", text)  # fmt: skip
    if meshed:
        print(f"mesh: {meshed.group(2)} elements, {meshed.group(1)} nodes; meshed in "
              f"{meshed.group(3)} s, solved in {meshed.group(4)} s", flush=True)
    saved = re.search(r"saved: (\S+)\.txt and \.json", text)
    if saved is None:
        return 0
    record = json.loads((work / f"{saved.group(1)}.json").read_text())
    numbers = record.get("numbers", {})
    names = [p.get("name") for p in json.loads((work / argv[0]).read_text()).get("parts", [])]
    for name, cells in numbers.get("printed", {}).items():
        k = names.index(name) + 1 if name in names else 0
        for number, cell in enumerate(cells, 1):
            print(f"part {k}, cell {number} of {len(cells)}: elements "
                  f"{cell.get('elements')}: all skin {cell.get('skin_elements')}, all core "
                  f"{cell.get('core_elements')}, laminate {cell.get('laminate_elements')}, column "
                  f"{cell.get('column_elements')}, in series {cell.get('series_elements')}; its "
                  f"core against Orca's layers in {cell.get('bands')} bands: short "
                  f"{cell.get('band_short_pct')}%, over {cell.get('band_over_pct')}%; layers "
                  f"unread {cell.get('layers_unread')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
