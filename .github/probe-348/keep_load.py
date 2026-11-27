"""Temporary probe for mqucifer/print-shop#348: `load` (or `load_forced.py MODE ...` after the
word `forced`) in the cae image, each mesh's and solve's files kept under /work/keep/TAG/NN-WHAT/
before the bench tidies them."""

import importlib
import os
import runpy
import shutil
import sys
from typing import Any

sys.path.insert(0, "/opt/bench")
sys.path.append("/opt/conda/lib")
import FreeCAD  # noqa: E402,F401

tag, rest = sys.argv[1], sys.argv[2:]
keep = f"/work/keep/{tag}"
os.makedirs(keep, exist_ok=True)
count = [0]


def save(folder: Any, what: str) -> None:
    if folder and os.path.isdir(folder):
        count[0] += 1
        shutil.copytree(folder, f"{keep}/{count[0]:02d}-{what}")


cae = importlib.import_module("bench.cae")
tm, ts = cae.tidy_mesh, cae.tidy_solve


def tidy_mesh(tools: Any) -> None:
    path = getattr(tools, "temp_file_mesh", None) or getattr(tools, "temp_file_geo", None)
    if path:
        save(os.path.dirname(path), "mesh")
    tm(tools)


def tidy_solve(fea: Any) -> None:
    save(getattr(fea, "working_dir", None), "solve")
    ts(fea)


for name in ("load", "load_through", "load_dovetail", "load_parts", "bend"):
    module = importlib.import_module(f"bench.{name}")
    if hasattr(module, "tidy_mesh"):
        module.tidy_mesh = tidy_mesh
    if hasattr(module, "tidy_solve"):
        module.tidy_solve = tidy_solve
if rest[0] == "forced":
    sys.argv = ["/work/load_forced.py", *rest[1:]]
    runpy.run_path("/work/load_forced.py", run_name="__main__")
else:
    sys.exit(importlib.import_module("bench.cli").main(rest))
