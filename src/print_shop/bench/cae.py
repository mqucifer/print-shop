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
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
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


#: The container's working folder, on disk, where its /tmp is memory (mqucifer/print-shop#376).
DISK = Path("/work")
#: A dot-folder there for the files gmsh and CalculiX read and write for `load`'s joined parts,
#: so a run's tools pass it by as not the run's own. Each mesh and solve has a folder of its own
#: in it, named by `SCRATCH_KINDS`, removed as it's read and at the command's end
#: (`clear_own`), and one a killed command left is removed at the next `load`'s start, once
#: it's older than `STALE` s (`clear_stale`): no command runs past the hour two passes are given
#: and its margin, so nothing that old is a running command's. Each file is held to the bench's
#: own `fsize` limit, as every file a command writes is.
SCRATCH = ".bench-scratch"
SCRATCH_KINDS = ("gmsh-", "load-")
STALE = 2 * 3600
#: The folders this command made there.
_own: list[str] = []


#: The container's own cgroup, where the kernel counts its memory.
CGROUP = Path("/sys/fs/cgroup")
#: What's kept back from the container's room, in bytes: the kernel's own, and the steps' small
#: allocations around the big ones.
ROOM_MARGIN = 64 * 2**20
#: What gmsh's mesh takes read back into `load`'s process, as a share of its file: 2.37 to
#: 2.40 times it on three second-order meshes of 8 to 207 MB in the image, curved and
#: straight-sided (a design run's assembly on mqucifer/print-shop#376). Held a tenth under the
#: least, so a mesh is refused only where even this doesn't fit.
READ_BACK = 2.3


def room(cgroup: Path | None = None) -> int | None:
    """What the container's memory has left, in bytes, by its own counts: its limit less what
    it holds, plus the file pages it holds that the kernel drops before it stops a process, less
    `ROOM_MARGIN`. None off the container, or with no limit. A failed allocation in FreeCAD's
    own code aborts the process, never a Python error (tried in the image: the mesh reader, the
    nodes on a face, the elements' nodes), so a big step's need is held against this before
    it's begun, not caught after."""
    cgroup = CGROUP if cgroup is None else cgroup
    try:
        most = (cgroup / "memory.max").read_text().strip()
        now = int((cgroup / "memory.current").read_text())
        stat = (cgroup / "memory.stat").read_text()
    except (OSError, ValueError):
        return None
    if not most.isdigit():
        return None
    found = re.search(r"^inactive_file (\d+)$", stat, re.M)
    return int(most) - now + (int(found.group(1)) if found else 0) - ROOM_MARGIN


def scratch(prefix: str) -> str:
    """A new folder for a mesh's or a solve's files, `prefix` one of `SCRATCH_KINDS`: in the
    scratch folder on disk, where /tmp is the container's memory, so what's written there never
    stops a process for memory (a design run's assembly on mqucifer/print-shop#376: the joined
    parts' shape, written for gmsh into /tmp, is no process's, and the kernel stopped `load`).
    Where there's no disk folder, as off the container, in the system's temporary folder."""
    assert prefix in SCRATCH_KINDS, prefix
    root = DISK / SCRATCH
    try:
        root.mkdir(exist_ok=True)
        made = tempfile.mkdtemp(prefix=prefix, dir=root)
    except OSError:
        return tempfile.mkdtemp(prefix=prefix)
    _own.append(made)
    return made


def clear_stale(now: float | None = None) -> None:
    """At a command's start: each folder in the scratch folder that its kinds name, older than
    `STALE`, removed, a killed command's; nothing else there is touched."""
    root = DISK / SCRATCH
    now = time.time() if now is None else now
    with contextlib.suppress(OSError):
        for folder in root.iterdir():
            if folder.is_dir() and folder.name.startswith(SCRATCH_KINDS):
                with contextlib.suppress(OSError):
                    if now - folder.stat().st_mtime > STALE:
                        shutil.rmtree(folder, ignore_errors=True)
    _drop_empty(root)


def clear_own() -> None:
    """At a command's end, whatever stopped it: every folder it made in the scratch folder, and
    the scratch folder itself where it's then empty."""
    while _own:
        shutil.rmtree(_own.pop(), ignore_errors=True)
    _drop_empty(DISK / SCRATCH)


def _drop_empty(root: Path) -> None:
    with contextlib.suppress(OSError):
        root.rmdir()


def gmsh(tools: Any, first: bool = False) -> Any:
    """gmsh's mesh, as FreeCAD's `GmshTools.create_mesh` makes it, with what this process has
    freed given back first (`give_back`). With `first`, gmsh is started as CalculiX is, the
    first the kernel stops where the container runs out of memory (`first_to_go`,
    mqucifer/print-shop#375): FreeCAD starts it by Qt, which can't be told so, so it's run here
    as FreeCAD's own run does, the same program, the same input and the same mesh read back.
    Where it ends with an error, or is stopped, no mesh is read and what it said is kept as
    `tools.said`, with its exit code as `tools.returncode`. Its files, the parts' shape it's
    given and the mesh it makes, are written on disk (`scratch`), and each part of it is a step
    of its own (`progress`), so a trace says which held what."""
    give_back()
    if not first:
        return tools.create_mesh()
    import FreeCAD

    from . import progress

    folder = scratch("gmsh-")
    found = tools.get_tmp_file_paths
    tools.get_tmp_file_paths = lambda *given: found(folder, True)
    with progress.step("writing gmsh's input, the joined parts' shape, on disk"):
        tools.prepare()
    level = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/Fem/Gmsh").GetString(
        "LogVerbosity", "3"
    )
    with progress.step("gmsh meshing the joined parts, first to go"):
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
    # FreeCAD's reader aborts the process where it meets the memory: held against the room
    # first, its need from the mesh file's size.
    give_back()
    try:
        size = os.path.getsize(tools.temp_file_mesh)
    except (OSError, AttributeError):
        size = 0  # no mesh file: FreeCAD's reader says so, as before
    need, left = READ_BACK * size, room()
    if size and left is not None and left < need:
        tools.memory = (need, left)
        tools.said += (
            f"\nError   : the mesh gmsh made takes about {need / 2**30:.2f} GiB read back, "
            f"{READ_BACK:g} times its {size / 2**20:.0f} MB file, and the container has "
            f"{max(left, 0) / 2**30:.2f} GiB left\n"
        )
        return False
    try:
        with progress.step("reading gmsh's mesh back"):
            tools.update_properties()
    except Exception as unread:  # noqa: BLE001 - as Qt's handler, which only printed it
        # FreeCAD's own run reads the mesh back in Qt's handler, where what's raised is
        # printed and the mesh left with no volumes, so its reader goes on as for no mesh
        # (the operator's review, mqucifer/print-shop#376). So it does here, saying why.
        tools.said += (
            f"\nError   : FreeCAD couldn't read gmsh's mesh back: {type(unread).__name__}: "
            f"{unread}\n"
        )
        return False
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
