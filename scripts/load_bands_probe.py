#!/usr/bin/env python3
"""Whether a `load` on an assembly with glass bands read its whole or a band's bound, and what
stopped the whole, by counts alone (mqucifer/print-shop#375).

Run from the folder the assembly is in, as `scripts/bench.py load ...` is:

    uv run python <print-shop>/scripts/load_bands_probe.py load ASSEMBLY.json ARGS... --parts

It runs the `load` as the bench does, in the second image, under the bench's own limits for the
command, and prints only counts, kinds, seconds and memory: the exit code and the verdict's word,
never its reasons; whether the container was killed; whether the whole was read, in one pass or
two, or each band left out, under a weight or lifted with its weight kept; what stopped the
whole, as a kind (`time`, `memory meshing`, `memory solving`, `no mesh`, `unparted`, `other`),
never its words; how many parts were read without their band and how many of those had their
band's weight hung from where it met them; the try's and the band's seconds; and each step as
`load_trace.py` says it. No figure of a part, joint, load or mesh size is printed, and the reply
`load` saves stays where `load` saves it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_trace import names, report  # noqa: E402

from print_shop.bench import host  # noqa: E402

#: What stopped a whole, by its words, as a kind: the first that matches each part of a why.
KINDS = (
    ("time", re.compile(r"met the container's time|wouldn't end inside the container's")),
    ("memory meshing", re.compile(r"gmsh made no mesh.*met the container's memory")),
    ("memory solving", re.compile(r"met the container's memory")),
    ("unparted", re.compile(r"no element of its own|in none of the parts")),
    ("no mesh", re.compile(r"gmsh made no mesh")),
)


def kinds(why: str) -> list[str]:
    """What stopped the whole, each try in turn: the one pass's, then its two passes', or two
    passes' asked for with `--parts`, each as a kind."""
    found = []
    for part in why.split("; and in two passes, "):
        found.append(next((k for k, words in KINDS if words.search(part)), "other"))
    return found


def summary(record: dict[str, Any]) -> list[str]:
    """The saved reply's counts: how it was read, what stopped the whole, the bands."""
    numbers = record.get("numbers", {})
    lower = numbers.get("lower_bound")
    lines = []
    if lower is None:
        passes = "two passes" if "two_passes" in numbers else "one pass"
        lines.append(f"read: the whole, in {passes}" if numbers.get("parts") else "read: nothing")
        return lines
    lifted = "weight_kept_g" in lower
    lines.append(
        "read: each band left out, "
        + ("lifted, each band's weight kept" if lifted else "a lower bound under the weight")
    )
    lines.append(f"what stopped the whole: {', then '.join(kinds(str(lower.get('why', ''))))}")
    lines.append(f"parts read without their band: {len(lower.get('parts', []))}")
    if lifted:
        hung = [one for one in numbers.get("loads", []) if one.get("band_mm2", 0) > 0]
        lines.append(f"of them, with the band's weight hung where it met them: {len(hung)}")
    lines.append(f"the try took {lower.get('tried_s')} s, the band's read {lower.get('band_s')} s")
    return lines


def main(argv: list[str]) -> int:
    if argv[:1] != ["load"] or len(argv) < 2:
        print("give a load command: load ASSEMBLY.json ARGS...")
        return 2
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    trace: list[str] = []
    done = host.run(argv, Path.cwd(), trace=trace)
    print(f"load under {host.memory_for(argv)}: exit {done.returncode}")
    found = re.search(r"^verdict: ([A-Z ]+?)(:|$)", done.stdout, re.M)
    print(f"verdict: {found.group(1) if found else 'none'}")
    if done.returncode == 2 and "it was killed" in done.stdout:
        print("killed by the kernel")
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    if saved:
        for line in summary(json.loads((Path.cwd() / f"{saved.group(1)}.json").read_text())):
            print(line)
    else:
        print("nothing saved")
    for line in report(trace, names(Path(argv[1]))):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
