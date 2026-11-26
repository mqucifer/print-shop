"""What the second image's commands share (mqucifer/print-shop#142): FreeCAD, loaded in order.

`fit-on`, `bend`, `load` (mqucifer/print-shop#156) and `optimise` (mqucifer/print-shop#286) run
in `print-shop/cae`, where FreeCAD's modules are beside its libraries (`host.CAE_ENTRY` puts them
on the path). The first image has no FreeCAD, and a script there that
calls one of them is told so, not left with an import's traceback.
"""

from __future__ import annotations

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
