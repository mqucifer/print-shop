#!/usr/bin/env python3
"""The bench on what only this Mac holds, with every answer saved outside the repository.

    scripts/bench_local_run.py triforce  --out ~/print-shop/bench-runs/triforce-<date>
    scripts/bench_local_run.py reference --out ~/print-shop/bench-runs/reference-<date>

`triforce` runs each command on the Sponsor's exported frame. Its results are derived from their
design, so they stay on this machine: this repository is public, and nothing here holds them.
`reference` builds our own reference parts from mqucifer/model-performance's scripts, in the
image, and runs the commands over each, to find any answer that's wrong on a real part. The
frame's location may be overridden with $PRINT_BENCH_TRIFORCE, and the reference scripts' with
$PRINT_BENCH_REFERENCE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import print_shop.bench.host as host

TRIFORCE = Path(
    os.environ.get("PRINT_BENCH_TRIFORCE", "~/Documents/cad-context/TriForceStandGlass.step")
).expanduser()
REFERENCE = Path(
    os.environ.get(
        "PRINT_BENCH_REFERENCE", "~/Documents/git/model-performance/tests/cad_eval/reference"
    )
).expanduser()
HERE = Path(__file__).resolve().parent.parent
#: The frame's heights and play. They're derived from the Sponsor's design, so they stay off this
#: public repository, in a file on the Mac: {"pocket_z", "lip_z", "play", "pane_thickness"}.
SETTINGS = Path(os.environ.get("PRINT_BENCH_LOCAL", "~/print-shop/bench-local.json")).expanduser()


def ask(args: list[str], work: Path, log: list[str]) -> dict[str, object]:
    """One command, its whole text kept in the run's transcript."""
    done = host.run(args, work)
    log += [f"$ bench {' '.join(args)}", done.stdout.rstrip(), f"exit {done.returncode}", ""]
    found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    record = {"command": args, "exit": done.returncode, "verdict": "NOT SAVED", "why": done.stdout}
    if found:
        saved = json.loads((work / f"{found.group(1)}.json").read_text())
        record.update(verdict=saved["verdict"], why=saved["why"], saved=f"{found.group(1)}.json")
    print(f"{record['verdict']:10} {' '.join(args)[:90]}", flush=True)
    return record


def finish(out: Path, log: list[str], rows: list[dict[str, object]]) -> int:
    (out / "transcript.txt").write_text("\n".join(log) + "\n")
    (out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n")
    counts = Counter(str(r["verdict"]) for r in rows)
    lines = [f"{n} {word}" for word, n in counts.most_common()]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print(f"{len(rows)} commands: {', '.join(lines)}. Saved in {out}")
    return 0


def triforce(out: Path) -> int:
    if not TRIFORCE.is_file() or not SETTINGS.is_file():
        print(f"needs {TRIFORCE} and {SETTINGS}: nothing to run")
        return 3
    local = json.loads(SETTINGS.read_text())
    pocket, lip = str(local["pocket_z"]), str(local["lip_z"])
    play, pane = str(local["play"]), str(local["pane_thickness"])
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(TRIFORCE, out / "frame.step")
    log: list[str] = []
    panes_args = ["panes", "frame.step", "--pocket-z", pocket, "--lip-z", lip]
    rows = [
        ask(args, out, log)
        for args in (
            ["info", "frame.step"],
            [*panes_args, "--clearance", play, "--pane-thickness", pane],
            ["thin", "frame.step", "--samples", "8000"],
            ["pause", "frame.step", "--pocket-z", pocket, "--pane-thickness", pane],
            ["capture", "frame.step", "--pocket-z", pocket, "--clearance", play,
             "--pane-thickness", pane],
            ["render", "frame.step", "--section", "1", pocket, lip],
        )
    ]
    # Turned over, lip on the plate, as a person would likely print it.
    (out / "turn_over.py").write_text(_TURN_OVER)
    done = host.run(["turn_over.py"], out, python=True)
    log += ["$ python turn_over.py", done.stdout + done.stderr, f"exit {done.returncode}", ""]
    height = float(done.stdout.strip().splitlines()[-1])
    turned = ["pause", "frame-turned.step", "--pocket-z", f"{height - float(pocket):g}"]
    rows.append(ask([*turned, "--pane-thickness", pane], out, log))
    # The frame against its own panes, read back from their files.
    panes = sorted((out / "panes" / "frame").glob("pane-*.stl"))
    if panes:
        rows.append(ask(["clearance", "frame.step", str(panes[0].relative_to(out))], out, log))
    # How far OCCT's volume moves with its own accuracy setting, against sections.
    (out / "volume_figures.py").write_text(_VOLUME_FIGURES)
    done = host.run(["-u", "volume_figures.py"], out, python=True)
    (out / "volume-figures.txt").write_text(done.stdout + done.stderr)
    log += ["$ python volume_figures.py", done.stdout + done.stderr, f"exit {done.returncode}", ""]
    return finish(out, log, rows)


_TURN_OVER = """
import cadquery as cq

s = cq.importers.importStep("frame.step").val()
height = s.BoundingBox().zmax
t = s.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, height))
cq.exporters.export(cq.Workplane(obj=t), "frame-turned.step")
print(round(height, 6))
"""

_VOLUME_FIGURES = """
import cadquery as cq
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

s = cq.importers.importStep("frame.step").val()
print("OCCT Volume():", s.Volume())
for eps in (1e-2, 1e-4, 1e-6):
    g = GProp_GProps()
    err = BRepGProp.VolumeProperties_s(s.wrapped, g, eps, False, False)
    print(f"OCCT at eps {eps}: {g.Mass()}, its own error estimate {err}")
level = sorted(
    {
        round(f.Center().z, 4)
        for f in s.Faces()
        if f.geomType() == "PLANE" and abs(f.normalAt().z) > 0.999
    }
)
total = 0.0
for low, high in zip(level, level[1:]):
    middle = (low + high) / 2
    face = cq.Face.makePlane(400, 400, cq.Vector(0, 0, middle), cq.Vector(0, 0, 1))
    area = sum(f.Area() for f in s.intersect(face).Faces())
    print(f"section at {middle}: {area} mm2, over {high - low} mm")
    total += area * (high - low)
print("sections stacked:", total)
"""


def reference(out: Path) -> int:
    scripts = sorted(REFERENCE.glob("*.py"))
    if not scripts:
        print(f"no reference scripts at {REFERENCE}: nothing to run")
        return 3
    out.mkdir(parents=True, exist_ok=True)
    (out / "near_touching_sweep.py").write_text(
        (HERE / "tests/bench_cases/near_touching_sweep.py").read_text()
    )
    (out / "make_fixtures.py").write_text((HERE / "tests/bench_cases/make_fixtures.py").read_text())
    sweep = host.run(["near_touching_sweep.py"], out, python=True)
    (out / "near-touching-66.json").write_text(sweep.stdout + sweep.stderr)
    log: list[str] = []
    rows: list[dict[str, object]] = []
    for script in scripts:
        work = out / script.stem
        work.mkdir(exist_ok=True)
        shutil.copy(script, work / script.name)
        built = host.run([script.name], work, python=True)
        log += [f"$ python {script.name}", (built.stdout + built.stderr)[-400:], ""]
        files = sorted(p.name for p in work.iterdir() if p.suffix.lower() in (".step", ".stl"))
        if built.returncode or not files:
            rows.append({"command": ["build", script.name], "exit": built.returncode,
                         "verdict": "NOT BUILT", "why": built.stderr[-200:]})  # fmt: skip
            print(f"NOT BUILT  {script.name}", flush=True)
            continue
        solids = 0
        for name in files:
            read = ask(["info", name], work, log)
            saved = (
                json.loads((work / str(read.get("saved"))).read_text()) if "saved" in read else {}
            )
            solids += int(saved.get("numbers", {}).get("solids", 1))
            rows.append(read)
            rows.append(ask(["thin", name], work, log))
        rows.append(ask(["render", *files, "--views", "iso"], work, log))
        if solids > 1:
            rows.append(ask(["clearance", *files], work, log))
    return finish(out, log, rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=("triforce", "reference"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if host.image_id() is None:
        print(f"no image {host.IMAGE}: run scripts/bench.py build")
        return 3
    out = args.out.expanduser().resolve()
    if HERE in out.parents or out == HERE:
        print(f"refused: {out} is inside this public repository")
        return 3
    return triforce(out) if args.what == "triforce" else reference(out)


if __name__ == "__main__":
    sys.exit(main())
