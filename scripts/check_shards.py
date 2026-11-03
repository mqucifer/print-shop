#!/usr/bin/env python3
"""CI's parts of a test run are the whole run, each test in exactly one (mqucifer/print-shop#34).

scripts/check_shards.py N -m "bench"     collects with those arguments, then with each --shard
                                          I/N, and fails unless the parts' node IDs are the
                                          whole's, with none twice. `--save DIR` keeps the lists.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path


def collected(args: list[str]) -> list[str]:
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode not in (0, 5):
        raise SystemExit(f"collection failed: {done.stdout[-2000:]}{done.stderr[-2000:]}")
    return [line for line in done.stdout.splitlines() if "::" in line]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parts", type=int)
    parser.add_argument("--save", type=Path)
    known, rest = parser.parse_known_args(argv)
    whole = collected(rest)
    parts = [collected([*rest, "--shard", f"{i}/{known.parts}"]) for i in range(1, known.parts + 1)]
    if known.save:
        known.save.mkdir(parents=True, exist_ok=True)
        (known.save / "whole.txt").write_text("\n".join(whole) + "\n")
        for i, part in enumerate(parts, 1):
            (known.save / f"part-{i}-of-{known.parts}.txt").write_text("\n".join(part) + "\n")
    counts = Counter(node for part in parts for node in part)
    twice = sorted(n for n, c in counts.items() if c > 1)
    missing = sorted(set(whole) - set(counts))
    extra = sorted(set(counts) - set(whole))
    sizes = ", ".join(str(len(p)) for p in parts)
    print(f"{len(whole)} tests; {known.parts} parts of {sizes}")
    wrong = (("in two parts", twice), ("in no part", missing), ("not in the whole", extra))
    for label, found in wrong:
        for node in found:
            print(f"{label}: {node}")
    if twice or missing or extra or not whole:
        print("the parts aren't the whole")
        return 1
    print("each test is in exactly one part")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
