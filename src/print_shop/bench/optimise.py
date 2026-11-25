"""`optimise`: where a part's load goes, a reference for a design round (mqucifer/print-shop#286).

A part designed to pass `load` comes out solid wherever nothing told it otherwise, though most of
that material carries little of the weight. Making it lighter at the same strength is a design
round: ribs, pockets and channel sections, with `load` proving the part as before. What the
round lacks is a picture of where the load goes through the part, so it knows where material can
come out. This is that picture, and only that: **a reference, never a part.** Nothing in the
rounds takes its mesh as a design, and the part a run makes is read by `load` and the other
checks, none of which changes.

**The tool** is BESO (bi-directional evolutionary structural optimisation, fandaL/beso, LGPL-3.0)
on the bench's own CalculiX: it solves the part under its loads, takes away the material whose
strain energy is lowest, adds back where it's highest, and repeats down to the target volume.
Its five Python files are in the second image at one commit, by SHA-256 (`cae-image/lock-*.txt`),
and run unchanged: `beso_main.py` reads its settings from `beso_conf.py` beside itself, so a
copy of it and the settings are written to a folder of their own under /tmp.

**What it reads, the way `load` does.** The assembly file, of one part: its STEP file, where it
sits, its material, the way its layers are stacked, and `held`. `--weight` kg shared between the
places `--at` names, each spread over the surface within `--over` mm, pulling `--toward`. The
mesh, the held faces and the load are `load`'s own (`load._Model`), so a place off the part or a
held plane with no face in it is refused as `load` refuses it.

**What it keeps solid:** the elements under each load and on each held face, always, as a load
on material that's been taken away has nothing to push; each `--keep` box (an element whose
middle is in it); and each `--keep-face`, the part's faces within half a millimetre of a point
(each element with a corner on one), such as a bolt's boss, a nut's pocket or a face that shows.
`--volume` is the share of the whole part to keep, the solid regions in it.

**The print direction.** `--up`, the way up on the bed, along the part's `layers`. BESO's one
control for it is its casting filter, which keeps material only on material under it all the way
down: no overhang at all, where a print takes one to 45°. Tried on the planted beam, it cut away
the beam's whole right half with its support, and on the cantilever it cut the load off from the
wall, as BESO can't bring a column back once it's gone. So it isn't used. `--up` reads the kept
material's overhang instead: the area of its outside that faces down past 45° from up, above its
lowest level, which a print would have to support, and where it is. The stresses are read against
the layers either way.

**What comes back:** the volume kept; the peak stress before and after, its largest von Mises
stress against the material's tensile yield and its largest pull across its layers against what
holds one layer to the next, each with where; pictures of the kept material from three-quarters
above and below; and the kept material as an STL file. Material that the kept regions don't
reach, an island BESO left, is dropped and said.

**Second methods.** The peak after, solved with the void elements taken away altogether, against
BESO's own last solve, which keeps them at a millionth of the stiffness; the peak before, against
BESO's first solve of the same part; the held faces' reaction against the weight in both of
ours; and the volume kept, summed over the kept elements, against the STL file's own enclosed
volume, read back from the file.

**Same inputs, same result.** gmsh on one thread, as `load` meshes, and the solves on the image's
own two threads; BESO has no random step. The reply gives a digest of the mesh and of the kept
elements, so two runs can be compared by it.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from .assembly import AXES, read
from .cae import freecad
from .limits import apart, over, said
from .load import REACTION, G, _Curved, _Model, _placed, _quiet, _reaction, _xyz
from .report import NoAnswer, Report

#: BESO's files in the second image, as its Dockerfile puts them there.
BESO = Path("/opt/beso")
BESO_FILES = ("beso_main.py", "beso_lib.py", "beso_filters.py", "beso_plots.py", "beso_separate.py")
#: A void element's share of the material's stiffness and density: BESO's soft kill.
VOID = 1e-6
#: The mesh is no finer than leaves about this many second-order tetrahedra, unless `--mesh`
#: says: each of BESO's iterations solves the whole part, and there are tens of them.
ELEMENTS = 12_000
MESH_LEAST = 0.5
#: Each iteration takes away this share of what's left, and adds back this share (BESO's
#: `mass_removal_ratio` and `mass_addition_ratio`, relative).
REMOVE = 0.05
ADD = 0.02
#: BESO's filter's range, in elements of the mesh: a sensitivity is averaged over this far round,
#: which keeps the material from a checkerboard of single elements.
FILTER = 2.0
#: Two readings of one peak stress within this share of the larger are the same reading.
AGREE = 0.05
#: The kept volume, summed over the elements and read back from the STL file, within this share.
VOLUME_AGREE = 1e-4
#: The longest a BESO run may take, in seconds: inside the bench's own limit for `optimise`.
BESO_LIMIT = 3300
#: Where the files are written, under the working directory, unless `--optimised-out` says.
OUT = "optimised"


def _mesh_size(solid: Any, given: float | None) -> float:
    """The largest element: about `ELEMENTS` of them in the part, unless `--mesh` says."""
    if given is not None:
        return float(given)
    budget = (8.5 * solid.Volume / ELEMENTS) ** (1 / 3)
    return float(round(max(MESH_LEAST, budget), 3))


class _Solved(_Model):
    """`load`'s model of the part, meshed, held and loaded, which keeps CalculiX's input as
    FreeCAD wrote it and the load put in it: the input BESO and our own solves start from."""

    inp_text = ""

    def _add_to(self, path: str) -> None:
        super()._add_to(path)
        self.inp_text = Path(path).read_text()


# The input file


def _mesh(text: str) -> tuple[dict[int, np.ndarray], dict[int, list[int]]]:
    """The nodes and the second-order tetrahedra of a CalculiX input, by number."""
    nodes: dict[int, np.ndarray] = {}
    elements: dict[int, list[int]] = {}
    mode = None
    pending: list[int] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            key = line.upper().replace(" ", "")
            mode = None
            if key == "*NODE" or key.startswith("*NODE,"):
                mode = "node"
            elif key.startswith("*ELEMENT,") or key == "*ELEMENT":
                if "TYPE=C3D10" not in key:
                    raise NoAnswer(f"the mesh has elements BESO isn't given here: {line[:80]}")
                mode = "element"
            continue
        values = [v for v in line.split(",") if v.strip()]
        if mode == "node":
            nodes[int(values[0])] = np.array([float(v) for v in values[1:4]])
        elif mode == "element":
            pending += [int(v) for v in values]
            if len(pending) >= 11:
                elements[pending[0]] = pending[1:11]
                pending = []
    if not elements:
        raise NoAnswer("FreeCAD's CalculiX input has no second-order tetrahedra")
    return nodes, elements


def _with_sets(text: str, sets: dict[str, list[int]], prints: str = "") -> str:
    """The input with element sets put in before its step, and `prints` before its step's end."""
    block = ""
    for name, numbers in sets.items():
        rows = [numbers[i : i + 12] for i in range(0, len(numbers), 12)]
        block += f"*ELSET, ELSET={name}\n" + "".join(",".join(map(str, r)) + ",\n" for r in rows)
    start = text.find("\n*STEP")
    if start < 0:
        raise NoAnswer("FreeCAD's CalculiX input has no step")
    text = text[: start + 1] + block + text[start + 1 :]
    if prints:
        end = text.upper().rfind("*END STEP")
        text = text[:end] + prints + text[end:]
    return text


def _without(text: str, gone: set[int]) -> str:
    """The input with the elements `gone` taken out: their lines, and their numbers in any
    element set that lists them."""
    found = []
    mode = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("*") and not line.startswith("**"):
            key = line.upper().replace(" ", "")
            mode = "element" if key.startswith("*ELEMENT,") else "elset" if key.startswith(
                "*ELSET,"
            ) else None
            found.append(raw)
            continue
        if mode == "element" and line and not line.startswith("**") and (
            int(line.split(",")[0]) in gone
        ):
            continue
        if mode == "elset" and line and not line.startswith("**"):
            words = [w.strip() for w in line.split(",") if w.strip()]
            kept = [w for w in words if not (w.isdigit() and int(w) in gone)]
            if words and not kept:
                continue
            raw = ",".join(kept) + ","
        found.append(raw)
    return "\n".join(found) + "\n"


# Our own solve


def _ccx(text: str, folder: Path, name: str) -> Path:
    """CalculiX on an input, on the image's threads. Its .dat file."""
    (folder / f"{name}.inp").write_text(text)
    ccx = shutil.which("ccx") or "ccx"
    done = subprocess.run(
        [ccx, name], cwd=folder, capture_output=True, text=True, timeout=BESO_LIMIT, check=False
    )
    dat = folder / f"{name}.dat"
    if done.returncode or "*ERROR" in done.stdout or not dat.exists():
        said_ = (done.stdout + done.stderr).strip().splitlines()
        wrong = [s for s in said_ if "ERROR" in s] or said_[-3:]
        raise NoAnswer(f"CalculiX solved nothing for the {name} part: {' '.join(wrong)[:300]}")
    return dat


def _stresses(dat: Path) -> dict[int, np.ndarray]:
    """Each element's stress at each integration point, as CalculiX printed it: rows of sxx,
    syy, szz, sxy, sxz, syz, in MPa."""
    text = dat.read_text()
    found = re.search(r"stresses \(elem, integ\.pnt\.,sxx,syy,szz,sxy,sxz,syz\).*?\n\s*\n(.*?)(?:\n\s*\n|\Z)", text, re.S)  # noqa: E501
    if not found:
        raise NoAnswer("CalculiX's table has no stresses")
    each: dict[int, list[list[float]]] = {}
    for line in found.group(1).splitlines():
        words = line.split()
        if len(words) == 8:
            each.setdefault(int(words[0]), []).append([float(v) for v in words[2:]])
    return {k: np.array(v, dtype=float) for k, v in each.items()}


def von_mises(rows: np.ndarray) -> np.ndarray:
    """Von Mises stress of rows of sxx, syy, szz, sxy, sxz, syz."""
    sx, sy, sz, xy, xz, yz = rows.T
    squared = 0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2) + 3 * (xy**2 + xz**2 + yz**2)
    return np.asarray(np.sqrt(squared), dtype=float)


def _peaks(
    stresses: dict[int, np.ndarray], middles: dict[int, np.ndarray], axis: int
) -> dict[str, Any]:
    """The largest von Mises stress, and the largest pull across the layers (the stress along
    `axis`, a pull only), each with the middle of its element."""
    numbers = sorted(stresses)
    rows = [stresses[n] for n in numbers]
    mises = np.array([float(von_mises(r).max()) for r in rows])
    across = np.array([max(float(r[:, axis].max()), 0.0) for r in rows])
    worst, pulled = int(mises.argmax()), int(across.argmax())
    return {
        "mises_mpa": float(mises[worst]),
        "mises_at": [round(float(v), 3) for v in middles[numbers[worst]]],
        "across_mpa": float(across[pulled]),
        "across_at": [round(float(v), 3) for v in middles[numbers[pulled]]],
    }


# What BESO left


def _outward(
    nodes: dict[int, np.ndarray], elements: dict[int, list[int]], kept: list[int]
) -> np.ndarray:
    """The outside triangles as an array of corner points, each turned to face out."""
    faces: dict[tuple[int, ...], Any] = {}
    for number in kept:
        corners = elements[number][:4]
        for skip in range(4):
            tri = [corners[i] for i in range(4) if i != skip]
            key = tuple(sorted(tri))
            if key in faces:
                faces[key] = None
            else:
                faces[key] = (tri, corners[skip])
    found = []
    for value in faces.values():
        if value is None:
            continue
        tri, inner = value
        a, b, c = (nodes[n] for n in tri)
        if float(np.cross(b - a, c - a) @ (nodes[inner] - a)) > 0:
            b, c = c, b
        found.append((a, b, c))
    return np.array(found)


def _connected(elements: dict[int, list[int]], kept: list[int]) -> list[list[int]]:
    """The kept elements in pieces: two elements are in one piece where they share a face."""
    beside: dict[tuple[int, ...], list[int]] = {}
    for number in kept:
        corners = elements[number][:4]
        for skip in range(4):
            beside.setdefault(tuple(sorted(corners[:skip] + corners[skip + 1 :])), []).append(
                number
            )
    near: dict[int, list[int]] = {n: [] for n in kept}
    for pair in beside.values():
        if len(pair) == 2:
            near[pair[0]].append(pair[1])
            near[pair[1]].append(pair[0])
    pieces, seen = [], set()
    for start in kept:
        if start in seen:
            continue
        piece, todo = [start], [start]
        seen.add(start)
        while todo:
            for other in near[todo.pop()]:
                if other not in seen:
                    seen.add(other)
                    piece.append(other)
                    todo.append(other)
        pieces.append(sorted(piece))
    return pieces


def write_stl(path: Path, triangles: np.ndarray, title: str) -> None:
    head = title.encode()[:80].ljust(80, b" ")
    with open(path, "wb") as fh:
        fh.write(head + struct.pack("<I", len(triangles)))
        for a, b, c in triangles:
            normal = np.cross(b - a, c - a)
            size = float(np.linalg.norm(normal))
            normal = normal / size if size > 0 else normal
            fh.write(struct.pack("<12fH", *normal, *a, *b, *c, 0))


def stl_volume(path: Path) -> float:
    """An STL file's enclosed volume, read back from the file: the divergence theorem over its
    triangles, as written in single precision."""
    data = path.read_bytes()
    count = struct.unpack("<I", data[80:84])[0]
    rows = np.frombuffer(data[84 : 84 + 50 * count], dtype=np.dtype(
        [("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]
    ))
    corners = rows["v"].astype(float)
    a, b, c = corners[:, 0], corners[:, 1], corners[:, 2]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def _picture(
    path: Path,
    triangles: np.ndarray,
    box: tuple[np.ndarray, np.ndarray],
    places: list[np.ndarray],
    elev: float,
    title: str,
) -> None:
    """The kept material from three-quarters above (`elev` over 0) or below, shaded by a light
    over the shoulder, in the part's box, each load's place marked."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    figure = plt.figure(figsize=(9, 6), dpi=110)
    axes = figure.add_axes((0.0, 0.0, 1.0, 0.94), projection="3d")
    axes.view_init(elev=elev, azim=-60)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1), 1e-12)[:, None]
    e, a = math.radians(elev), math.radians(-60)
    eye = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    light = np.clip(normals @ eye, 0, 1) * 0.75 + 0.25
    colour = np.array([0.25, 0.45, 0.75])
    axes.add_collection3d(
        Poly3DCollection(list(triangles), facecolors=light[:, None] * colour, edgecolors="none")
    )
    low, high = box
    edges = []
    for i in range(3):
        for j in (0, 1):
            for k in (0, 1):
                start, end = low.copy(), low.copy()
                other = [x for x in range(3) if x != i]
                for axis, pick in zip(other, (j, k), strict=True):
                    start[axis] = end[axis] = (low, high)[pick][axis]
                start[i], end[i] = low[i], high[i]
                edges.append([start, end])
    axes.add_collection3d(Line3DCollection(edges, colors="0.6", linewidths=0.6))
    for place in places:
        axes.scatter(*place, color="red", s=30, depthshade=False)
    axes.set_xlim(low[0], high[0])
    axes.set_ylim(low[1], high[1])
    axes.set_zlim(low[2], high[2])
    axes.set_box_aspect(tuple(float(v) for v in np.maximum(high - low, 1e-6)), zoom=1.25)
    axes.locator_params(nbins=5)
    axes.set_xlabel("x")
    axes.set_ylabel("y")
    axes.set_zlabel("z")
    figure.suptitle(title, fontsize=9)
    figure.savefig(path)
    plt.close(figure)


# BESO


def _settings(
    folder: Path,
    goal: float,
    material: Any,
    yield_mpa: float,
    size: float,
    count: int,
    threads: int,
) -> str:
    """`beso_conf.py`: Python that BESO runs as its settings."""
    void = f"*ELASTIC\n{material.modulus_mpa * VOID:.9g}, {material.poisson:g}"
    solid = f"*ELASTIC\n{material.modulus_mpa:.9g}, {material.poisson:g}"
    reach = round(FILTER * size, 6)
    filters: list[list[Any]] = [["simple", reach]]
    lines = [
        f"path = {str(folder)!r}",
        f"path_calculix = {shutil.which('ccx') or 'ccx'!r}",
        "file_name = 'part.inp'",
    ]
    for name, optimised in (("Optimised", True), ("Kept", False)):
        lines += [
            f"domain_optimized[{name!r}] = {optimised}",
            f"domain_density[{name!r}] = [{VOID!r}, 1.0]",
            f"domain_thickness[{name!r}] = [1.0, 1.0]",
            f"domain_offset[{name!r}] = 0.0",
            f"domain_orientation[{name!r}] = []",
            # Each element's stress over the material's yield: what BESO's own solve says the
            # peak is. A void element's is over a million times it, so it's never the peak.
            f"domain_FI[{name!r}] = [[('stress_von_Mises', {yield_mpa / VOID!r})], "
            f"[('stress_von_Mises', {yield_mpa!r})]]",
            f"domain_material[{name!r}] = [{void!r}, {solid!r}]",
            f"domain_same_state[{name!r}] = False",
        ]
    lines += [
        f"mass_goal_ratio = {goal!r}",
        "continue_from = ''",
        f"filter_list = {filters!r}",
        "optimization_base = 'stiffness'",
        f"cpu_cores = {threads}",
        # The target volume is the input's: no count of elements over their stress stops it.
        f"FI_violated_tolerance = {count + 1}",
        "decay_coefficient = -0.2",
        "shells_as_composite = False",
        "reference_points = 'integration points'",
        "reference_value = 'max'",
        "sensitivity_averaging = False",
        f"mass_addition_ratio = {ADD!r}",
        f"mass_removal_ratio = {REMOVE!r}",
        "ratio_type = 'relative'",
        "compensate_state_filter = False",
        "steps_superposition = []",
        "iterations_limit = 'auto'",
        "tolerance = 1e-3",
        "displacement_graph = []",
        "save_iteration_results = 0",
        "save_solver_files = ''",
        "save_resulting_format = 'inp'",
    ]
    return "\n".join(lines) + "\n"


def _beso(folder: Path, threads: int) -> tuple[str, str]:
    """BESO's own `beso_main.py`, a copy beside its settings, with its other files from the
    image. What it printed, and its log."""
    missing = [f for f in BESO_FILES if not (BESO / f).exists()]
    if missing:
        raise NoAnswer(
            f"BESO isn't in this image ({', '.join(missing)} not in {BESO}): build it again with "
            "`scripts/bench.py build cae`"
        )
    shutil.copy(BESO / "beso_main.py", folder / "beso_main.py")
    entry = (
        f"import sys, runpy; sys.path.insert(0, {str(BESO)!r}); sys.argv = ['beso_main.py']; "
        "runpy.run_path('beso_main.py', run_name='__main__')"
    )
    env = {
        **os.environ,
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(folder),
        "XDG_CACHE_HOME": str(folder),
        "OMP_NUM_THREADS": str(threads),
    }
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-c", entry],
            cwd=folder,
            env=env,
            capture_output=True,
            text=True,
            timeout=BESO_LIMIT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NoAnswer(
            f"BESO ran past {BESO_LIMIT} s and was stopped: give a coarser --mesh"
        ) from exc
    log = folder / "part.log"
    logged = log.read_text() if log.exists() else ""
    if done.returncode:
        tail = (done.stderr.strip().splitlines() or done.stdout.strip().splitlines() or [""])[-1]
        raise NoAnswer(f"BESO stopped with an error: {tail[:300]}")
    # A filter that found no neighbour carries on unfiltered, and says so only in its log
    # (fandaL/beso#58): a result with no filter is a checkerboard, not an answer.
    wrong = [line for line in logged.splitlines() if line.startswith("ERROR")]
    if wrong:
        raise NoAnswer(f"BESO's log has an error: {wrong[0][:300]}")
    return done.stdout, logged


def _rows(logged: str) -> list[list[float]]:
    """BESO's table, an iteration a row: its number, the optimised mass, the mean strain energy
    density, the count over their stress in each set and in all, the mean stress over the yield
    with and without the void, and the most in each set and in all."""
    rows = []
    started = False
    for line in logged.splitlines():
        if line.strip().startswith("i ") and "mass" in line:
            started = True
            continue
        if not started:
            continue
        words = line.split()
        if len(words) == 11 and words[0].isdigit():
            rows.append([float(w) for w in words])
    if not rows:
        raise NoAnswer("BESO's log has no table of its iterations")
    return rows


def _states(folder: Path) -> set[int]:
    """The elements BESO left solid: its last mesh of the solid state."""
    found = sorted(folder.glob("file*_state1.inp"))
    if not found:
        raise NoAnswer("BESO wrote no mesh of what it kept")
    _, elements = _mesh(found[-1].read_text())
    return set(elements)


def _digest(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode())
    return digest.hexdigest()[:16]


def _stopped(logged: str, iterations: int) -> str:
    """Why BESO stopped, from its log."""
    if "OSCILLATION" in logged:
        return "it turned back to an earlier iteration's material, and stopped"
    found = re.search(r"iterations_limit\s*=\s*(\d+)", logged)
    if found and iterations > int(found.group(1)):
        return f"it reached its limit of {found.group(1)} iterations"
    return "its mean strain energy settled, within a thousandth over five iterations"


#: A face facing down further than this from the way up hangs over air: past 45°, as a print
#: takes an overhang.
HANGS = math.cos(math.radians(45))


def overhang(triangles: np.ndarray, up: np.ndarray, near: float) -> dict[str, Any]:
    """The kept material's outside that faces down past 45° from `up`, above its lowest level by
    more than `near`: what a print would have to support. Its area, and the box round it."""
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    areas = np.linalg.norm(normals, axis=1) / 2
    ways = normals @ up / np.maximum(2 * areas, 1e-12)
    heights = triangles.mean(axis=1) @ up
    hangs = (ways < -HANGS) & (heights > float((triangles.reshape(-1, 3) @ up).min()) + near)
    if not hangs.any():
        return {"area_mm2": 0.0, "low": None, "high": None}
    points = triangles[hangs].reshape(-1, 3)
    return {
        "area_mm2": float(areas[hangs].sum()),
        "low": [round(float(v), 2) for v in points.min(axis=0)],
        "high": [round(float(v), 2) for v in points.max(axis=0)],
    }


# The command


def run(args: Any, report: Report) -> None:
    freecad("optimise")
    if args.weight is None or args.weight <= 0 or not args.at:
        raise NoAnswer(
            "optimise takes --weight KG, more than 0, and --at X,Y,Z, the places it sits on, as "
            "`load` takes them"
        )
    if args.over <= 0:
        raise NoAnswer("--over is how far round each place the weight is spread, more than 0 mm")
    assembly = read(args.assembly)
    if len(assembly.parts) != 1 or assembly.joints:
        raise NoAnswer(
            f"optimise reads one part, and {args.assembly} has {len(assembly.parts)} and "
            f"{len(assembly.joints)} joint(s): give an assembly file of the part alone, held "
            "where the others hold it"
        )
    part = assembly.parts[0]
    layers = part.layers_way()
    axis = int(np.abs(layers).argmax())
    up = None
    if args.up is not None:
        up = np.zeros(3)
        up[AXES.index(args.up[1])] = 1.0 if args.up[0] == "+" else -1.0
        if abs(float(up @ layers)) < 1 - 1e-6:
            raise NoAnswer(
                f"--up {args.up} isn't along the part's layers, which stack along "
                f"{AXES[axis]} where the assembly puts it: the way up on the bed is +{AXES[axis]} "
                f"or -{AXES[axis]}"
            )
    material = part.material
    # The two figures `assembly.PRESS` takes from the same sheet, here read as what they are: a
    # pull in the layers' plane against the tensile yield printed flat, and a pull across the
    # layers against the interlayer adhesion, each at its low end.
    yield_mpa, layers_mpa = material.press_across_mpa, material.press_along_mpa
    placed = _placed(assembly)
    size = _mesh_size(placed[0], args.mesh)
    load_args = SimpleNamespace(
        weight=args.weight, at=args.at, toward=args.toward, over=args.over, lift=None
    )
    import FreeCAD as App
    import Part

    App.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").SetInt("NumOfThreads", 1)
    # As `load` meshes (mqucifer/print-shop#295): with each element's sides curved onto the
    # part's faces, and where gmsh can't do that without turning one inside out, again with them
    # straight, said in the reply.
    straight = None
    doc = App.newDocument("Optimise")
    model = _Solved(assembly, placed, load_args, size)
    try:
        with _quiet():
            model.join(doc)
            model.hold()
            try:
                model.solve(doc)
            except _Curved as curved:
                straight = str(curved)
                App.closeDocument(doc.Name)
                doc = App.newDocument("Optimise")
                model = _Solved(assembly, placed, load_args, size, True)
                model.join(doc)
                model.hold()
                model.solve(doc)
            mesh_s = model.mesh_s
        held_faces = [
            (f.CenterOfMass, set(model.fem.getNodesByFace(f)))
            for f in [*model.fixed, *sum(model.standing.values(), [])]
        ]
        held_nodes = set().union(*(nodes_ for _, nodes_ in held_faces))
        patch_nodes = {n for one in model.loads for n in one["nodes"]}
        faces_kept: list[tuple[list[float], list[Any]]] = []
        for point in args.keep_face:
            vertex = Part.Vertex(App.Vector(*point))
            near = [(f.distToShape(vertex)[0], f) for f in model.shape.Faces]
            least = min(d for d, _ in near)
            if least > 0.5:
                raise NoAnswer(
                    f"--keep-face {_xyz(point)} is {least:.3f} mm from the part's surface: it "
                    "names a face by a point on it"
                )
            faces_kept.append((point, [f for d, f in near if d <= 0.5]))
        face_nodes = [
            {n for f in faces for n in model.fem.getNodesByFace(f)} for _, faces in faces_kept
        ]
    finally:
        App.closeDocument(doc.Name)
    force = args.weight * G
    nodes, elements = _mesh(model.inp_text)
    numbers = sorted(elements)
    corners = {n: np.array([nodes[k] for k in elements[n][:4]]) for n in numbers}
    volume = {n: abs(float(np.linalg.det(c[1:] - c[0]))) / 6 for n, c in corners.items()}
    middles = {n: c.mean(axis=0) for n, c in corners.items()}
    whole = sum(volume.values())
    # What's kept solid, and why.
    why: dict[str, set[int]] = {"loads": set(), "held": set()}
    for n in numbers:
        mine = set(elements[n])
        if mine & patch_nodes:
            why["loads"].add(n)
        if mine & held_nodes:
            why["held"].add(n)
    for k, (low, high) in enumerate(args.keep):
        lo, hi = np.array(low), np.array(high)
        why[f"box {k + 1}"] = {
            n for n in numbers if bool(np.all(middles[n] >= lo) and np.all(middles[n] <= hi))
        }
        if not why[f"box {k + 1}"]:
            raise NoAnswer(
                f"--keep {','.join(f'{v:g}' for v in [*low, *high])} holds no element's middle: "
                "the box is off the part, or thinner than the mesh"
            )
    for k, found in enumerate(face_nodes):
        why[f"face {k + 1}"] = {n for n in numbers if set(elements[n][:4]) & found}
    kept_solid = sorted(set().union(*why.values()))
    optimised = [n for n in numbers if n not in set(kept_solid)]
    solid_share = sum(volume[n] for n in kept_solid) / whole
    if not optimised:
        raise NoAnswer("everything is kept solid: nothing is left for BESO to take away")
    if args.volume <= solid_share:
        raise NoAnswer(
            f"what's kept solid is already {solid_share:.3f} of the part, so --volume "
            f"{args.volume:g} leaves nothing to choose: give more than {solid_share:.3f}, or "
            "keep less"
        )
    goal = (args.volume * whole - solid_share * whole) / sum(volume[n] for n in optimised)
    threads = int(os.environ.get("OMP_NUM_THREADS", "2") or 2)
    work = Path(tempfile.mkdtemp(prefix="optimise-"))
    try:
        prints = "*EL PRINT, ELSET=Solved\nS\n"
        before_dat = _ccx(_with_sets(model.inp_text, {"Solved": numbers}, prints), work, "before")
        before = _peaks(_stresses(before_dat), middles, axis)
        before_reaction, _ = _reaction(str(before_dat))
        (work / "part.inp").write_text(
            _with_sets(model.inp_text, {"Optimised": optimised, "Kept": kept_solid})
        )
        (work / "beso_conf.py").write_text(
            _settings(work, goal, material, yield_mpa, size, len(numbers), threads)
        )
        began = time.time()
        printed, logged = _beso(work, threads)
        beso_s = round(time.time() - began, 1)
        rows = _rows(logged)
        stopped = _stopped(logged, len(rows))
        solid = _states(work)
        pieces = _connected(elements, sorted(solid))
        # What carries the load: each piece with a loaded element in it. Anything else is an
        # island, or a held face's own piece with nothing on it, and carries nothing.
        loaded = set(why["loads"])
        carried = [p for p in pieces if loaded & set(p)]
        kept = sorted(n for p in carried for n in p)
        dropped = sorted(solid - set(kept))
        kept_nodes = {k for n in kept for k in elements[n]}
        for piece in carried:
            if not {k for n in piece for k in elements[n]} & held_nodes:
                raise NoAnswer(
                    "what BESO kept doesn't carry the load to a held face: the material under a "
                    f"load is cut off from them, round {_xyz(middles[piece[0]])}. Give a larger "
                    "--volume, or --keep the material between"
                )
        for centre, found in held_faces:
            if not found & kept_nodes:
                raise NoAnswer(
                    "what BESO kept no longer reaches the held face round "
                    f"{_xyz((centre.x, centre.y, centre.z))}, so it isn't held as the part is: "
                    "give a larger --volume, or --keep the material between"
                )
        gone = set(numbers) - set(kept)
        after_dat = _ccx(
            _with_sets(_without(model.inp_text, gone), {"Solved": kept}, prints), work, "after"
        )
        after = _peaks(_stresses(after_dat), middles, axis)
        after_reaction, _ = _reaction(str(after_dat))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for name, reaction in (("before", before_reaction), ("after", after_reaction)):
        off = float(np.linalg.norm(reaction + force * model.toward))
        if off > REACTION * force:
            raise NoAnswer(
                f"the held faces' reaction {name}, {_xyz(reaction)} N, isn't the {force:.4g} N "
                f"put on, by {off:.3f} N: the load didn't go on as asked"
            )
    kept_volume = sum(volume[n] for n in kept)
    out = Path(args.out_optimised) / f"{part.name}-v{args.volume:g}"
    out.mkdir(parents=True, exist_ok=True)
    stl = out / "kept.stl"
    triangles = _outward(nodes, elements, kept)
    write_stl(stl, triangles, f"optimise: {part.name} at {args.volume:g}, a reference, not a part")
    read_back = stl_volume(stl)
    box = placed[0].BoundBox
    low = np.array([box.XMin, box.YMin, box.ZMin])
    high = np.array([box.XMax, box.YMax, box.ZMax])
    places = [np.array(p, dtype=float) for p in args.at]
    pictures = []
    for name, elev in (("above", 30.0), ("below", -30.0)):
        path = out / f"{name}.png"
        _picture(
            path,
            triangles,
            (low, high),
            places,
            elev,
            f"{part.name}: {kept_volume / whole:.3f} of it kept, from three-quarters {name}. "
            "A reference, not a part",
        )
        pictures.append(path)
    # BESO's own readings: its first and last solves.
    soft_before = rows[0][-1] * yield_mpa
    soft_after = rows[-1][-1] * yield_mpa
    report.numbers["part"] = part.name
    report.numbers["weight_kg"] = args.weight
    report.numbers["force_n"] = force
    report.numbers["toward"] = args.toward
    report.numbers["volume_target"] = args.volume
    report.numbers["volume_kept_mm3"] = kept_volume
    report.numbers["volume_part_mm3"] = whole
    report.numbers["volume_kept_solid"] = solid_share
    report.numbers["kept_solid"] = {k: len(v) for k, v in why.items()}
    # Each region kept solid, as the share of its elements in what's kept at the end:
    # 1 unless BESO's piece of it reached no held face and was dropped.
    report.numbers["kept_solid_kept"] = {
        k: (len(v & set(kept)) / len(v) if v else 1.0) for k, v in why.items()
    }
    report.numbers["mesh_mm"] = size
    report.numbers["nodes"] = len(nodes)
    report.numbers["elements"] = len(numbers)
    report.numbers["elements_kept"] = len(kept)
    report.numbers["islands_dropped"] = len(pieces) - len(carried)
    report.numbers["elements_dropped"] = len(dropped)
    report.numbers["iterations"] = len(rows)
    report.numbers["beso_s"] = beso_s
    report.numbers["mesh_s"] = mesh_s
    report.numbers["up"] = args.up
    report.numbers["layers"] = [round(float(v), 4) + 0.0 for v in layers]
    report.numbers["before"] = before
    report.numbers["after"] = after
    report.numbers["beso_mises_before_mpa"] = soft_before
    report.numbers["beso_mises_after_mpa"] = soft_after
    report.numbers["stl_volume_mm3"] = read_back
    report.numbers["yield_mpa"] = yield_mpa
    report.numbers["across_limit_mpa"] = layers_mpa
    report.numbers["mesh_digest"] = _digest(
        sorted((k, tuple(np.round(v, 9))) for k, v in nodes.items())
    )
    report.numbers["kept_digest"] = _digest(kept)
    report.numbers["reference_only"] = True

    report.line(
        "a reference for a design round, never a part: it shows where the load goes, so a round "
        "knows where material can come out. Nothing takes this mesh as a design; the part a run "
        "makes is read by `load` and the other checks as before"
    )
    report.line(
        f"the part {part.name} from {args.assembly}, {whole:.0f} mm³ as meshed: "
        f"{'; '.join(model.held_said)}; {args.weight:g} kg, {force:.2f} N, --toward "
        f"{args.toward}, shared between {len(args.at)} place(s), each over the surface within "
        f"{args.over:g} mm of it; a mesh of at most {size:g} mm ({len(nodes)} nodes, "
        f"{len(numbers)} second-order tetrahedra)"
    )
    if straight is not None:
        report.numbers["straight"] = straight
        report.line(
            "its elements are second-order with straight sides, mid-side nodes halfway along "
            f"them, as `load` meshes where curving them fails: {straight}"
        )
    kinds = ", ".join(f"{k} {len(v)}" for k, v in why.items() if v)
    report.line(
        f"kept solid: {len(kept_solid)} elements, {solid_share:.3f} of the part ({kinds}): under "
        "each load and on each held face always, and each --keep and --keep-face"
    )
    report.fact(
        "volume_kept",
        kept_volume / whole,
        how=f"{kept_volume:.0f} of {whole:.0f} mm³, against --volume {args.volume:g}; BESO, "
        f"fandaL/beso, by strain energy, in {len(rows)} iterations and {beso_s} s: {stopped}",
    )
    if dropped:
        report.line(
            f"{len(pieces) - len(carried)} piece(s) of {len(dropped)} element(s) that BESO kept "
            "have no load on them and touch the loaded material nowhere along a face, and are "
            "dropped: they carry nothing"
        )
    mises_limit = said(after["mises_mpa"], yield_mpa)
    report.line(
        f"peak stress before, solid: {before['mises_mpa']:.2f} MPa von Mises at "
        f"{_xyz(before['mises_at'])}, and {before['across_mpa']:.2f} MPa pulled across the layers "
        f"at {_xyz(before['across_at'])}"
    )
    report.line(
        f"peak stress after, the kept material alone: {mises_limit} MPa von Mises at "
        f"{_xyz(after['mises_at'])}, against the tensile yield's {yield_mpa:g}; and "
        f"{said(after['across_mpa'], layers_mpa)} MPa pulled across the layers at "
        f"{_xyz(after['across_at'])}, against the interlayer adhesion's {layers_mpa:g}"
    )
    report.line(
        f"the limits are {material.name}'s from {material.press_source.split(',')[0]}: "
        f"{material.source}. In the layers' plane, the low end of the tensile yield printed "
        f"flat; across them, the low end of the interlayer adhesion"
    )
    if up is None:
        report.line(
            "the way up on the bed wasn't given (--up), so where the kept material hangs over "
            "air isn't read"
        )
    else:
        hang = overhang(triangles, up, size)
        report.numbers["overhang"] = hang
        where = (
            f", from {_xyz(hang['low'])} to {_xyz(hang['high'])}" if hang["low"] is not None else ""
        )
        report.line(
            f"printed with {args.up} up, {hang['area_mm2']:.0f} mm² of the kept material's "
            f"outside faces down past 45° above its lowest level, and would need support{where}. "
            "BESO's casting filter, its one control for the way up, forbids any overhang and cut "
            "the load path on the planted parts, so it isn't used: this is read, not steered"
        )
    report.line(
        f"same inputs, same result: mesh {report.numbers['mesh_digest']}, kept "
        f"{report.numbers['kept_digest']} (gmsh on one thread; CalculiX on {threads}; BESO has "
        "no random step)"
    )
    report.wrote(str(stl))
    for path in pictures:
        report.wrote(str(path))
    # Second methods.
    soft = apart(after["mises_mpa"], soft_after, AGREE * max(after["mises_mpa"], soft_after),
                 yield_mpa, least=False)  # fmt: skip
    report.second(
        f"the peak after by BESO's own last solve, the taken-away material left in at a "
        f"millionth of the stiffness: {soft_after:.2f} MPa von Mises, against "
        f"{after['mises_mpa']:.2f} with it taken out ({soft})"
    )
    same_before = apart(before["mises_mpa"], soft_before, AGREE * max(before["mises_mpa"],
                        soft_before), yield_mpa, least=False)  # fmt: skip
    report.second(
        f"the peak before by BESO's first solve: {soft_before:.2f} MPa von Mises, against "
        f"{before['mises_mpa']:.2f} ({same_before})"
    )
    report.second(
        f"the held faces' reaction, as CalculiX sums it: {_xyz(before_reaction)} N before and "
        f"{_xyz(after_reaction)} N after, against the {force:.4g} N put on --toward {args.toward}"
    )
    volume_off = abs(read_back - kept_volume) / kept_volume
    report.second(
        f"the kept volume read back from {stl.name}, by its triangles: {read_back:.1f} mm³, "
        f"against {kept_volume:.1f} summed over the kept elements ({volume_off:.2e} apart)"
    )
    report.note(
        "the part is read as solid, with one stiffness every way, under the one load case given: "
        "walls and infill, creep, and any other load aren't in it. The kept material is BESO's "
        "answer for that case on this mesh, jagged at the elements' size"
    )
    split = [s for s in (soft, same_before) if s == "split"]
    if volume_off > VOLUME_AGREE or split:
        report.verdict(
            "DISAGREE",
            "the two readings of "
            + ("the volume kept" if volume_off > VOLUME_AGREE else "the peak stress")
            + " differ: read both, and don't pick one",
        )
        return
    over_by = [
        f"its von Mises stress, {mises_limit} MPa, is over the tensile yield's {yield_mpa:g}"
        for _ in [0]
        if over(after["mises_mpa"], yield_mpa)
    ] + [
        f"its pull across the layers, {said(after['across_mpa'], layers_mpa)} MPa, is over the "
        f"interlayer adhesion's {layers_mpa:g}"
        for _ in [0]
        if over(after["across_mpa"], layers_mpa)
    ]
    if over_by:
        report.verdict(
            "FAIL",
            f"kept to {kept_volume / whole:.3f} of the part, {' and '.join(over_by)}: give a "
            "larger --volume. Still a reference, never a part",
        )
        return
    report.verdict(
        "OK",
        f"a reference, never a part: {kept_volume / whole:.3f} of the part kept carries "
        f"{args.weight:g} kg at {after['mises_mpa']:.2f} MPa von Mises at most, against "
        f"{before['mises_mpa']:.2f} solid and the yield's {yield_mpa:g}",
    )
