"""Temporary probe for mqucifer/print-shop#348, removed before the PR is ready: the stool's load
run many times on CI's runner, every mesh's and solve's files kept, and the first stage whose
files differ between runs named. Usage: probe.py WORK RUNS (RUNS loads, two at a time)."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from print_shop.bench import host

HERE = Path(__file__).resolve().parent
CASES = HERE.parent.parent / "tests" / "cae_cases"
PUSH = ["--weight", "5", "--at", "0,100,154", "--toward", "+x"]
STAMPS = re.compile(rb"^(\s*1UTIME|\s*1UDATE|\*\*\s+written on|// /opt/conda/bin/gmsh).*$", re.M)


def digest(path: Path) -> str:
    return hashlib.md5(STAMPS.sub(b"", path.read_bytes())).hexdigest()[:10]


def one(work: Path, tag: str) -> dict[str, object]:
    forced = ["forced", "guess"] if tag.startswith("guess") else []
    args = ["keep_load.py", tag, *forced, "load", "large/stool.json", *PUSH]
    done = host.run(args, work, python=True, image=host.CAE_IMAGE)
    found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    if not found:
        return {"tag": tag, "error": (done.stdout + done.stderr)[-3000:]}
    record = json.loads((work / f"{found.group(1)}.json").read_text())
    kept = work / "keep" / tag
    files = {
        f"{d.name}/{f.name}": digest(f)
        for d in sorted(kept.iterdir())
        for f in sorted(d.iterdir())
    }
    numbers = {k: v for k, v in record["numbers"].items() if k not in ("mesh_s", "solve_s")}
    return {"tag": tag, "files": files, "numbers": numbers}


def main() -> None:
    work, runs = Path(sys.argv[1]), int(sys.argv[2])
    if shutil.which("lscpu"):
        print(subprocess.run(["lscpu"], capture_output=True, text=True).stdout)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr
    shutil.copy(CASES / "load_forced.py", work / "load_forced.py")
    shutil.copy(HERE / "keep_load.py", work / "keep_load.py")
    # In twos, as CI's -n 2 runs them, now and the old guess alternately.
    tags = [f"{'now' if k % 2 == 0 else 'guess'}-{k}" for k in range(runs)]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda t: one(work, t), tags))
    for r in results:
        if "error" in r:
            print(r["tag"], "ERROR", r["error"])
    good = [r for r in results if "error" not in r]
    first = good[0]
    for r in good:
        files: dict[str, str] = r["files"]  # type: ignore[assignment]
        base: dict[str, str] = first["files"]  # type: ignore[assignment]
        differ = [k for k in base if files.get(k) != base[k]]
        same = r["numbers"] == first["numbers"]
        print(r["tag"], "numbers", "SAME" if same else "DIFF", "files differing:", differ[:12])
        for name in differ[:3]:
            a = (work / "keep" / str(first["tag"]) / name).read_text(errors="replace").splitlines()
            b = (work / "keep" / str(r["tag"]) / name).read_text(errors="replace").splitlines()
            lines = [(i, x, y) for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y]
            print(f"   {name}: {len(a)} vs {len(b)} lines, {len(lines)} differ")
            for i, x, y in lines[:6]:
                print(f"     {i}: {x[:100]!r}")
                print(f"     {i}: {y[:100]!r}")
        if not same:
            top, top0 = r["numbers"]["parts"]["top"], first["numbers"]["parts"]["top"]  # type: ignore[index]
            print("   top stretch", top["stretch_pct"], top0["stretch_pct"])
            j, j0 = (n["joints"]["top+leg0"] for n in (r["numbers"], first["numbers"]))  # type: ignore[index]
            print("   top+leg0 moment", j["moment_nmm"], j0["moment_nmm"])


if __name__ == "__main__":
    main()
