#!/usr/bin/env python3
"""Where a `load` spends the container's memory, step by step (mqucifer/print-shop#336).

Run from the folder the assembly is in, as `scripts/bench.py load ...` is:

    uv run python <print-shop>/scripts/load_trace.py [--memory 3g] load ASSEMBLY.json ARGS...

It runs the `load` as the bench does, in the second image, under the bench's limits, but for
the memory, which `--memory` may set higher than the bench's own, so a case that's killed at the
bench's can be followed past it. It prints only steps, counts, seconds and memory: each part and
joint by a number and each joint's kind, never its name; any figure in a step's words, as a
mesh's size, as `#`; and the verdict's word, never its reasons. Nothing else is printed, and
the reply `load` saves stays where `load` saves it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from print_shop.bench import host

GIB = 2**30


def names(assembly: Path) -> list[tuple[str, str]]:
    """Each part's and joint's name, and what it's said as: longest first, so a joint's name,
    which holds its parts', goes before them."""
    data = json.loads(assembly.read_text())
    said: list[tuple[str, str]] = []
    for k, part in enumerate(data.get("parts", []), 1):
        said.append((str(part["name"]), f"part{k}"))
    for k, joint in enumerate(data.get("joints", []), 1):
        name = str(joint.get("name") or "+".join(joint["between"]))
        said.append((name, f"joint{k} ({joint.get('kind', 'bonded')})"))
    return sorted(said, key=lambda n: -len(n[0]))


def _token(k: int) -> str:
    """A name's stand-in while the figures are taken out: no letter or digit, so no name or
    figure is found in it. A word that is also a name is said as the name's number."""
    return chr(0xE000 + k)


def plain(what: str, said: list[tuple[str, str]]) -> str:
    """A step's words with each name said by its number, and every other figure as `#`."""
    for k, (name, _) in enumerate(said):
        what = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", _token(k), what)
    what = re.sub(r"\d+(\.\d+)?", "#", what)
    # The names' own numbers aren't figures: put in after.
    for k, (_, instead) in enumerate(said):
        what = what.replace(_token(k), instead)
    return what


def _gib(value: str | None) -> str:
    return f"{int(value) / GIB:.2f}" if value and value.isdigit() else "?"


def _count(counts: dict[str, str], key: str) -> int:
    value = counts.get(key, "")
    return int(value) if value.isdigit() else 0


def report(trace: list[str], said: list[tuple[str, str]], most: int = 12) -> list[str]:
    """One line a step, as it was said, in order: when it began and how long it ran, in s; the
    container's peak at its start and at its end, the most it held while it ran, of that what
    was files in /tmp, and what `load`'s own process had in use and had freed but kept then, in
    GiB. Then the `most` steps the peak rose most in."""
    rows: list[tuple[float, str, list[dict[str, str]]]] = []
    for line in trace:
        if line.startswith(host.STEP_LINE + " "):
            seconds, _, what = line[len(host.STEP_LINE) + 1 :].partition(" ")
            rows.append((float(seconds), plain(what, said), []))
        elif line.startswith(host.MEMORY_LINE + " ") and rows:
            _, *fields = line[len(host.MEMORY_LINE) + 1 :].split()
            rows[-1][2].append(dict(f.split("=", 1) for f in fields if "=" in f))
    lines = [
        "began s, ran s, peak GiB at its start and its end, most held GiB, of it /tmp GiB; and "
        "load's own process then, in use GiB and freed but kept GiB"
    ]
    rises: list[tuple[int, str]] = []
    for k, (at, what, counts) in enumerate(rows):
        if not counts:
            continue  # said again as an inner step ended, and ended before the memory was read
        took = (rows[k + 1][0] if k + 1 < len(rows) else at) - at
        held = max(counts, key=lambda c: _count(c, "current"))
        first, last = _count(counts[0], "peak"), _count(counts[-1], "peak")
        line = (
            f"{at:8.1f} {took:8.1f}  {_gib(counts[0].get('peak'))} {_gib(counts[-1].get('peak'))}"
            f"  {_gib(held.get('current'))} {_gib(held.get('shmem'))}"
            f"  {_gib(held.get('used'))} {_gib(held.get('kept'))}  {what}"
        )
        lines.append(line)
        rises.append((last - first, line))
    lines.append(f"the {most} steps the peak rose most in:")
    lines += [line for _, line in sorted(rises, key=lambda r: -r[0])[:most]]
    return lines


def main(argv: list[str]) -> int:
    top = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    top.add_argument("--memory", default=host.MEMORY, help="the container's memory, as docker's")
    top.add_argument("command", nargs=argparse.REMAINDER)
    args = top.parse_args(argv)
    if args.command[:1] != ["load"] or len(args.command) < 2:
        top.error("give a load command: load ASSEMBLY.json ARGS...")
    if host.image_id(host.CAE_IMAGE) is None:
        print(host.missing(host.CAE_IMAGE))
        return 3
    host.MEMORY = args.memory
    trace: list[str] = []
    done = host.run(args.command, Path.cwd(), trace=trace)
    said = names(Path(args.command[1]))
    print(f"load under {args.memory}: exit {done.returncode}")
    found = re.search(r"^verdict: ([A-Z ]+?)(:|$)", done.stdout, re.M)
    print(f"verdict: {found.group(1) if found else 'none'}")
    if done.returncode == 2 and "it was killed" in done.stdout:
        print("killed by the kernel")
    for line in report(trace, said):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
