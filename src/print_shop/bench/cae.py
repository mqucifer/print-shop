"""What the second image's commands share (mqucifer/print-shop#142): FreeCAD, loaded in order.

`fit-on`, `bend`, `load` (mqucifer/print-shop#156) and `optimise` (mqucifer/print-shop#286) run
in `print-shop/cae`, where FreeCAD's modules are beside its libraries (`host.CAE_ENTRY` puts them
on the path). The first image has no FreeCAD, and a script there that
calls one of them is told so, not left with an import's traceback.
"""

from __future__ import annotations

import contextlib
import ctypes
import gc
import os
import shutil
import subprocess
from typing import Any

from .report import NoAnswer


def freecad(command: str) -> tuple[Any, Any]:
    """FreeCAD and its Part module. FreeCAD first: Part stops the process if it's loaded before
    it."""
    try:
        import FreeCAD
        import Part
    except ImportError as exc:
        raise NoAnswer(
            f"`{command}` runs in the bench's second image, which has FreeCAD, and this isn't it "
            f"({exc.name} isn't here): ask for it as a bench command, not from a script"
        ) from exc
    return FreeCAD, Part


def give_back() -> None:
    """What the process has freed, given back to the kernel (mqucifer/print-shop#336). FreeCAD's
    shapes and meshes are freed once their document's closed, but the C library keeps the freed
    memory for the process unless told to return it: joining a table's parts with their bores
    squared freed 0.8 GiB that the process went on holding. gmsh and CalculiX are processes of
    their own, held against the same container's memory, and can't use what this one holds, so
    it's given back before each of them starts, and as each of `load`'s steps ends. Off Linux,
    nothing changes."""
    gc.collect()
    with contextlib.suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def gmsh(tools: Any, first: bool = False) -> Any:
    """gmsh's mesh, as FreeCAD's `GmshTools.create_mesh` makes it, with what this process has
    freed given back first (`give_back`). With `first`, gmsh is started as CalculiX is, the
    first the kernel stops where the container runs out of memory (`first_to_go`,
    mqucifer/print-shop#375): FreeCAD starts it by Qt, which can't be told so, so it's run here
    as FreeCAD's own run does, the same program, the same input and the same mesh read back.
    Where it ends with an error, or is stopped, no mesh is read and what it said is kept as
    `tools.said`, with its exit code as `tools.returncode`."""
    give_back()
    if not first:
        return tools.create_mesh()
    import FreeCAD

    tools.prepare()
    level = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").GetString(
        "LogVerbosity", "3"
    )
    done = subprocess.run(
        [tools.gmsh_bin, "-v", level, "-", tools.temp_file_geo],
        capture_output=True,
        check=False,
        preexec_fn=first_to_go,
    )
    tools.said = (done.stdout + done.stderr).decode(errors="replace")
    tools.returncode = done.returncode
    if done.returncode != 0:
        return False
    tools.update_properties()
    return True


def tidy_mesh(tools: Any) -> None:
    """Delete gmsh's own files once FreeCAD has read its mesh, or found none
    (mqucifer/print-shop#295). The container's /tmp is memory, held against the same limit as
    the solve, and a traced frame's mesh file alone is tens of MB: left there, each mesh's files
    take memory from every solve after it."""
    for path in (getattr(tools, "temp_file_mesh", None), getattr(tools, "temp_file_geo", None)):
        if path:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def tidy_solve(fea: Any) -> None:
    """Delete CalculiX's input and its results once they've been read, for the same reason."""
    folder = getattr(fea, "working_dir", None)
    if folder:
        shutil.rmtree(folder, ignore_errors=True)


def first_to_go() -> None:
    """In a solver's own process, before it starts: it's the first the kernel stops where the
    container runs out of memory (mqucifer/print-shop#301), not the command that started it,
    which can then say so, and read the same mesh another way. Any process may raise its own
    `oom_score_adj`; where there's none, as off Linux, nothing changes."""
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write("1000")
    except OSError:
        pass


def ccx_one_thread(fea: Any, timeout: float | None = None) -> None:
    """CalculiX on one thread, where FreeCAD's own run starts it on as many as the host has
    CPUs, not the container's 2 (mqucifer/print-shop#295): its solver's memory grows with
    them, and a traced glass frame that solves in 2.8 GB on one ran out of the container's
    3 GiB on more. One thread, as gmsh is, also gives the same figures on any host. It runs
    as FreeCAD's does, in the input's folder on the input's name, and keeps what it says
    where FreeCAD's `load_results` and the readings look for it. Given a `timeout`, in s, it's
    stopped there, and `subprocess.TimeoutExpired` is raised (`load`'s one pass, where two
    passes could go on under the hour, mqucifer/print-shop#331)."""
    fea.setup_ccx()
    folder, name = os.path.split(fea.inp_file_name)
    give_back()
    done = subprocess.run(
        [fea.ccx_binary, "-i", os.path.splitext(name)[0]],
        cwd=folder,
        capture_output=True,
        env={**os.environ, "OMP_NUM_THREADS": "1"},
        check=False,
        preexec_fn=first_to_go,
        timeout=None if timeout is None else max(timeout, 0.0),
    )
    fea.ccx_stdout = done.stdout.decode(errors="replace")
    fea.ccx_stderr = done.stderr.decode(errors="replace")
