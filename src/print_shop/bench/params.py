"""The parameter check: re-run a design's script with one value changed, and report what moved.

On a copy of a Fusion design, changing `BaseCircle` left the outer ring and motif in place while
Fusion reported everything healthy (mqucifer/print-shop#15). So: run the script as it stands,
run it again with nothing changed, run it with the one value changed, and read every file it
wrote. The figures say which dimensions, volumes and pieces moved. The second check is a
different method, a symmetric difference of the meshes, which also sees a feature that moved
with every figure staying the same. The run with nothing changed is the control: if a script's
output moves on its own, what moved can't be put down to the parameter.

A parameter is a number assigned once at the top level of the script (`BaseCircle = 20.0`).

`--keep DIR` copies what the changed run wrote into a new folder, by the same names: the refinement
pass (mqucifer/print-shop#51) checks a proposed change to a part on those files.

A parameter that leaves pieces behind reads `PARTIAL`, not `OK`: a need saying it moves every piece
passed with one piece moved of several. `--max-left` says how many may stay, for a parameter that's
meant to drive only some of them.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Cylinder

from .report import Report
from .shapes import VOLUME_FLOOR, NoAnswer, Part, flat_heights, load

GEOMETRY = {".step", ".stp", ".stl"}
#: A dimension moved if it changed by more than this, in mm.
MOVED_MM = 1e-3
#: A volume moved by more than this share, or VOLUME_FLOOR. The two meshes come from the same
#: code, so a part that didn't move meshes the same.
MOVED_SHARE = 1e-5
#: Inputs copied beside a run: larger files aren't, so a run never fills the container's /tmp.
LARGEST_INPUT = 64_000_000


def parameters(source: str) -> dict[str, list[ast.Assign | ast.AnnAssign]]:
    """Each top-level name assigned a number, with every top-level assignment to it."""
    found: dict[str, list[ast.Assign | ast.AnnAssign]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target: ast.expr = node.targets[0]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
        else:
            continue
        if isinstance(target, ast.Name):
            found.setdefault(target.id, []).append(node)
    return {
        name: nodes
        for name, nodes in found.items()
        if any(_number(n.value) is not None for n in nodes)
    }


def _number(node: ast.expr | None) -> float | None:
    try:
        value = ast.literal_eval(node) if node is not None else None
    except ValueError:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def changed_source(source: str, name: str, text: str) -> tuple[str, float, float]:
    """The script with one value replaced: (source, value it had, value it has now)."""
    found = parameters(source)
    if name not in found:
        names = ", ".join(sorted(found)) or "none"
        raise NoAnswer(f"no top-level number named {name} in the script. Its numbers: {names}")
    nodes = found[name]
    if len(nodes) > 1:
        where = ", ".join(str(n.lineno) for n in nodes)
        raise NoAnswer(
            f"{name} is assigned {len(nodes)} times, on lines {where}: which one counts?"
        )
    value = nodes[0].value
    old = _number(value)
    new = _number(ast.parse(text, mode="eval").body)
    if old is None or new is None or value is None:
        raise NoAnswer(f"{name} needs a number: it's {old}, and {text} was asked")
    if old == new:
        raise NoAnswer(f"{name} is already {text}: nothing would change")
    # The AST's columns count bytes, so the arithmetic's in bytes too.
    rows = source.splitlines(keepends=True)
    last_line = value.end_lineno or value.lineno
    last_column = value.end_col_offset or value.col_offset
    start = sum(len(x.encode()) for x in rows[: value.lineno - 1]) + value.col_offset
    end = sum(len(x.encode()) for x in rows[: last_line - 1]) + last_column
    encoded = source.encode()
    return (encoded[:start] + text.encode() + encoded[end:]).decode(), old, new


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_script(
    source: str, script: Path, timeout: int, keep: Path | None = None
) -> dict[str, list[Part]]:
    """One run, in a scratch copy of the script's folder: each file it wrote, as its pieces.

    `keep` is a folder to copy each file it wrote into, by the same name, before the scratch
    folder goes: the refinement pass checks a proposed change on what the changed run made.
    """
    with tempfile.TemporaryDirectory(dir="/tmp") as scratch:
        here = Path(scratch)
        inputs: dict[str, str] = {}
        for item in script.resolve().parent.iterdir():
            if item.is_file() and item.stat().st_size <= LARGEST_INPUT:
                shutil.copy(item, here / item.name)
                inputs[item.name] = _hash(item)
        (here / script.name).write_text(source)
        env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR="/tmp", NUMBA_CACHE_DIR="/tmp")
        try:
            done = subprocess.run(
                ["python", script.name],
                cwd=here,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NoAnswer(f"the script ran past {timeout} s") from exc
        if done.returncode:
            tail = done.stderr.strip().splitlines()[-1:] or ["no message"]
            raise ScriptFailed(f"exit {done.returncode}: {tail[0][:300]}")
        parts: dict[str, list[Part]] = {}
        for path in sorted(here.rglob("*")):
            name = str(path.relative_to(here))
            if (
                path.is_file()
                and path.suffix.lower() in GEOMETRY
                and (inputs.get(name) != _hash(path))
            ):
                part = load(str(path))
                part.name = name
                parts[name] = part.pieces()
                for piece in parts[name]:
                    piece.fine()  # read while the scratch folder is there
                if keep is not None:
                    (keep / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, keep / name)
        return parts


class ScriptFailed(NoAnswer):
    """The script itself raised."""


@dataclass
class Reading:
    """One piece's figures."""

    name: str
    manifold: Any
    size: list[float]
    centre: list[float]
    volume: float
    area: float
    radii: list[float] = field(default_factory=list)
    heights: list[float] = field(default_factory=list)


def _radii(part: Part) -> list[float]:
    if not part.is_brep:
        return []
    found = set()
    for face in part.shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            found.add(round(float(surface.Cylinder().Radius()), 4))
    return sorted(found)


def read_piece(piece: Part) -> Reading:
    mesh = piece.fine()
    built = piece.manifold()
    low, high = np.asarray(mesh.bounds)
    return Reading(
        name=piece.name,
        manifold=built,
        size=(high - low).round(4).tolist(),
        centre=((high + low) / 2).round(4).tolist(),
        volume=float(built.volume()),
        area=float(built.surface_area()),
        radii=_radii(piece),
        heights=[h["z"] for h in flat_heights(mesh)],
    )


def differences(a: Reading, b: Reading) -> list[str]:
    """What moved between two readings of a piece, in words and numbers."""
    moved = []
    for axis, was, now in zip("xyz", a.size, b.size, strict=True):
        if abs(was - now) > MOVED_MM:
            moved.append(f"size {axis} {was} to {now}")
    if max(abs(x - y) for x, y in zip(a.centre, b.centre, strict=True)) > MOVED_MM:
        moved.append(f"centre {a.centre} to {b.centre}")
    if abs(a.volume - b.volume) > max(VOLUME_FLOOR, MOVED_SHARE * abs(a.volume)):
        moved.append(f"volume {a.volume:.3f} to {b.volume:.3f}")
    if a.radii != b.radii:
        moved.append(f"radii {a.radii} to {b.radii}")
    if a.heights != b.heights:
        moved.append(f"flat heights {a.heights} to {b.heights}")
    return moved


def _symmetric(a: Reading, b: Reading) -> float:
    return float((a.manifold - b.manifold).volume() + (b.manifold - a.manifold).volume())


def _still(a: Reading, moved: float) -> bool:
    return moved <= max(VOLUME_FLOOR, MOVED_SHARE * abs(a.volume))


def compare_files(a: list[Reading], b: list[Reading]) -> list[dict[str, Any]]:
    """Pair up two runs' pieces, those that didn't move first, and say what each did."""
    rows: list[dict[str, Any]] = []
    left = list(a)
    right = list(b)
    for piece in list(left):
        for other in right:
            if differences(piece, other):
                continue
            moved = _symmetric(piece, other)
            if _still(piece, moved):
                rows.append({"piece": piece.name, "moved": [], "symmetric_mm3": round(moved, 4)})
                left.remove(piece)
                right.remove(other)
                break
    for piece in sorted(left, key=lambda p: -p.volume):
        if not right:
            rows.append({"piece": piece.name, "vanished": True, "moved": ["it vanished"]})
            continue
        other = min(
            right,
            key=lambda o: (
                float(np.linalg.norm(np.subtract(o.centre, piece.centre)))
                + abs(o.volume - piece.volume) ** (1 / 3)
            ),
        )
        right.remove(other)
        moved = _symmetric(piece, other)
        words = differences(piece, other)
        row: dict[str, Any] = {
            "piece": piece.name,
            "moved": words,
            "symmetric_mm3": round(moved, 4),
        }
        if _still(piece, moved):
            row["figures_only"] = bool(words)
        else:
            row["meshes_differ"] = True
            if not words:
                row["moved"] = [f"it moved with no figure changing: {moved:.3f} mm³ differs"]
        rows.append(row)
    rows += [{"piece": o.name, "appeared": True, "moved": ["it appeared"]} for o in right]
    return rows


def _readings(parts: dict[str, list[Part]]) -> dict[str, list[Reading]]:
    return {name: [read_piece(p) for p in pieces] for name, pieces in parts.items()}


def _compare_runs(
    a: dict[str, list[Reading]], b: dict[str, list[Reading]]
) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(set(a) | set(b)):
        found[name] = compare_files(a.get(name, []), b.get(name, []))
    return found


def run(args: Any, report: Report) -> None:
    script = Path(args.script)
    if not script.is_file():
        raise NoAnswer(f"no script {args.script} in the working directory")
    name, sep, text = args.change.partition("=")
    if not sep:
        raise NoAnswer(f"--set takes NAME=VALUE, not {args.change}")
    source = script.read_text()
    changed, old, new = changed_source(source, name.strip(), text.strip())
    report.fact("parameter", f"{name.strip()} {old:g} to {new:g}")

    base = _readings(run_script(source, script, args.timeout))
    if not base:
        raise NoAnswer("the script wrote no STEP or STL file, so there's nothing to compare")
    control = _readings(run_script(source, script, args.timeout))
    keep = None
    if getattr(args, "keep", None):
        keep = Path(args.keep)
        if keep.resolve() == script.resolve().parent or keep.exists():
            raise NoAnswer(f"--keep {args.keep} has to be a new folder")
        keep.mkdir(parents=True)
    try:
        after = _readings(run_script(changed, script, args.timeout, keep))
    except ScriptFailed as exc:
        report.second("the changed run wrote nothing to compare")
        report.verdict("NO ANSWER", f"at {name.strip()}={new:g} the script failed: {exc}")
        return

    drift = _compare_runs(base, control)
    drifted = [r["piece"] for rows in drift.values() for r in rows if r["moved"]]
    report.second(f"the run with nothing changed: {len(drifted)} piece(s) moved on their own")
    if drifted:
        report.numbers["moved_on_their_own"] = drifted[:20]
        report.verdict(
            "DISAGREE",
            f"the script's output moves with nothing changed ({', '.join(drifted[:3])}), so what "
            "moved can't be put down to the parameter",
        )
        return

    rows = _compare_runs(base, after)
    report.numbers["files"] = rows
    moved: list[str] = []
    still: list[str] = []
    differs: list[str] = []
    for file_name, pieces in rows.items():
        for row in pieces:
            (moved if row["moved"] else still).append(row["piece"])
            state = "; ".join(row["moved"]) if row["moved"] else "didn't move"
            report.line(f"{file_name}: {row['piece']}: {state}")
            if row.get("figures_only"):
                differs.append(f"{row['piece']}'s figures moved, and its mesh didn't")
    total = sum(r.get("symmetric_mm3", 0.0) for pieces in rows.values() for r in pieces)
    report.fact("pieces_moved", len(moved))
    report.fact("pieces_not_moved", len(still))
    report.numbers["pieces_left"] = still[:50]
    report.second(f"the meshes' symmetric difference over all pieces: {total:.3f} mm³")
    label = f"{name.strip()} {old:g} to {new:g}"
    if differs:
        report.verdict("DISAGREE", "; ".join(differs[:3]))
    elif not moved:
        report.verdict("NO CHANGE", f"{label} moved nothing: this parameter drives nothing")
    elif still:
        said = (
            f"{label} moved {len(moved)} piece(s), and {len(still)} didn't: {', '.join(still[:8])}"
        )
        if len(still) > args.max_left:
            limit = f"{args.max_left} may" if args.max_left else "none may"
            report.verdict("PARTIAL", f"{said}. Pieces are left behind, and {limit} stay")
        else:
            report.verdict("OK", f"{said}, within --max-left {args.max_left}")
    else:
        report.verdict("OK", f"{label} moved all {len(moved)} piece(s)")
