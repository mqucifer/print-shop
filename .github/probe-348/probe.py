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


def unv_sections(path: Path) -> dict[str, list[str]]:
    """A UNV file's lines by dataset: 2411 the nodes, 2412 the elements."""
    found: dict[str, list[str]] = {}
    lines = path.read_text().splitlines()
    k = 0
    while k < len(lines):
        if lines[k].strip() == "-1" and k + 1 < len(lines):
            name, end = lines[k + 1].strip(), k + 2
            while end < len(lines) and lines[end].strip() != "-1":
                end += 1
            found.setdefault(name, []).extend(lines[k + 2 : end])
            k = end + 1
        else:
            k += 1
    return found


def compare_meshes(folder: Path) -> None:
    meshes = sorted(folder.glob("*.unv"))
    if len(meshes) < 2:
        return
    first = unv_sections(meshes[0])
    for other in meshes[1:]:
        second = unv_sections(other)
        for name in first:
            a, b = first[name], second.get(name, [])
            differ = sum(x != y for x, y in zip(a, b, strict=False))
            print(f"   {meshes[0].stem} v {other.stem}: dataset {name}, {len(a)} v {len(b)} "
                  f"lines, {differ} differ")


def main() -> None:
    work, count = Path(sys.argv[1]), int(sys.argv[2])
    if shutil.which("lscpu"):
        print(subprocess.run(["lscpu"], capture_output=True, text=True).stdout)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr
    shutil.copy(CASES / "load_forced.py", work / "load_forced.py")
    shutil.copy(HERE / "keep_load.py", work / "keep_load.py")
    shutil.copy(HERE / "gmsh_loop.py", work / "gmsh_loop.py")
    print(one(work, "now-0").get("error", "kept now-0"), flush=True)

    def loop(variant: str) -> str:
        done = host.run(["gmsh_loop.py", "/work/keep/now-0/01-mesh", variant, str(count)], work,
                        python=True, image=host.CAE_IMAGE, timeout=2400)  # fmt: skip
        return done.stdout + done.stderr[-2000:]

    with ThreadPoolExecutor(2) as pool:
        for said in pool.map(loop, ["as-is", "one-thread", "no-ho", "no-opt"]):
            print(said, flush=True)
    for variant in ["as-is", "one-thread", "no-ho", "no-opt"]:
        compare_meshes(work / f"gmsh-{variant}")


if __name__ == "__main__":
    main()
