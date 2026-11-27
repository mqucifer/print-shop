"""Temporary probe for mqucifer/print-shop#348, in the cae image: gmsh alone on the stool's own
FreeCAD input, run N times as FreeCAD runs it (`gmsh - file.geo`), each mesh hashed. Usage:
gmsh_loop.py MESH_DIR VARIANT N, MESH_DIR holding `shape2mesh.geo` and its .brep; VARIANT one of
`as-is`, `one-thread` (OMP_NUM_THREADS and OPENBLAS_NUM_THREADS 1), `no-ho` (no high-order
optimisation), `no-opt` (no tetrahedral optimisation)."""

import collections
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

source, variant, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(os.path.join(source, "shape2mesh.geo")) as given:
    geo = given.read()
brep = next(f for f in os.listdir(source) if f.endswith(".brep"))
env = dict(os.environ)
if variant == "one-thread":
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
elif variant == "no-ho":
    geo = geo.replace("Mesh.HighOrderOptimize = 1;", "Mesh.HighOrderOptimize = 0;")
elif variant == "no-opt":
    geo = geo.replace("Mesh.Optimize = 1;", "Mesh.Optimize = 0;")
elif variant != "as-is":
    raise SystemExit(f"no such variant: {variant}")
seen: dict[str, int] = collections.Counter()
logs: dict[str, str] = {}
kept = f"/work/gmsh-{variant}"
os.makedirs(kept, exist_ok=True)
for _ in range(count):
    here = tempfile.mkdtemp()
    shutil.copy(os.path.join(source, brep), os.path.join(here, brep))
    text = re.sub(r'Merge "[^"]*\.brep";', f'Merge "{here}/{brep}";', geo)
    text = re.sub(r'Save "[^"]*\.unv";', f'Save "{here}/mesh.unv";', text)
    with open(os.path.join(here, "shape2mesh.geo"), "w") as out:
        out.write(text)
    done = subprocess.run(["/opt/conda/bin/gmsh", "-", os.path.join(here, "shape2mesh.geo")],
                          capture_output=True, text=True, env=env, check=False)
    with open(os.path.join(here, "mesh.unv"), "rb") as mesh:
        digest = hashlib.md5(mesh.read()).hexdigest()[:10]
    if digest not in seen:
        shutil.copy(os.path.join(here, "mesh.unv"), f"{kept}/{digest}.unv")
        logs[digest] = done.stdout + done.stderr
    seen[digest] += 1
    shutil.rmtree(here)
print(variant, dict(seen), flush=True)
for digest, log in logs.items():
    wanted = r"finalized|Generated|succeeded|failed|Warning|Error"
    lines = [line for line in log.splitlines() if re.search(wanted, line)]
    print(f"  {digest}:", *lines[:40], sep="\n    ")
