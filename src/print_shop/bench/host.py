"""Running the bench from the machine: the container's limits, and the disk they protect.

Docker's disk here is shared with LiteLLM's database, crew's workspaces and any running tick,
and it filled on 2026-11-01. So each image is of a stated size, and a build that would take
the disk under its floor refuses. Geometry code never has a network, a writable image or more
than two CPUs and 3 GiB. Standard library only: this runs outside the image.

There are two images (mqucifer/print-shop#142). `print-shop/bench` holds CadQuery and the
second-check libraries, and runs every command but two. `print-shop/cae` holds FreeCAD, gmsh and
CalculiX, and runs `fit-on`, `bend` and `load` (mqucifer/print-shop#156), under the same
limits. The disk guard counts both: a
build leaves room for whichever of them isn't built yet.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

IMAGE = "print-shop/bench:0.15"
BASE = (
    "python:3.12-slim-bookworm@sha256:"
    "6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70"
)
CPUS = "2"
MEMORY = "3g"
PIDS = "512"
TMP_SIZE = "1g"
#: The disk limit, in GB: the image at its largest, and the free space never to go under.
IMAGE_LIMIT_GB = 4.0
FLOOR_GB = 5.0
#: The second image: FreeCAD 1.1.3, gmsh and CalculiX from conda-forge, each package by its
#: name, version, build and MD5 in `cae-image/lock-*.txt`. To make the lists again, in any
#: micromamba container with a network: `micromamba create -p /tmp/x --platform linux-aarch64 -c
#: conda-forge --override-channels freecad=1.1.3 calculix=2.23 gmsh=4.15.2 --dry-run --json`,
#: and from each `actions.LINK` entry in order, its `subdir`, `name`, `v` and its `version`,
#: `build`, the file type its `fn` ends in, and `md5`; `linux-64` for CI.
CAE_IMAGE = "print-shop/cae:0.1"
CAE_LIMIT_GB = 4.0
#: The commands that run in it. Every other runs in `IMAGE`.
CAE_COMMANDS = ("fit-on", "bend", "load")
#: Each image by the name `build` takes: its tag, its limit and its Dockerfile's folder.
IMAGES = {
    "bench": (IMAGE, IMAGE_LIMIT_GB, "image"),
    "cae": (CAE_IMAGE, CAE_LIMIT_GB, "cae-image"),
}
#: The exit code of a process the kernel killed, as out-of-memory does.
KILLED = 137
#: The most one file may be, in bytes. /work is a folder on the Mac, with no quota of its own.
MAX_FILE = str(2 * 1024**3)
#: A command that runs longer than this has no answer, in seconds.
TIMEOUT = 600
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent.parent.parent
#: The container's own mount of this package: read-only, so nothing in it can change the bench.
MOUNT = "/opt/bench/bench"
ENTRY = "import sys; sys.path.insert(0, '/opt/bench'); from bench.cli import main; sys.exit(main())"
#: The same in the second image, where FreeCAD's modules are beside its libraries.
CAE_PYTHON = "/opt/conda/bin/python"
CAE_ENTRY = "import sys; sys.path.append('/opt/conda/lib'); " + ENTRY
GB = 1_000_000_000


def _text(argv: list[str], timeout: int = 120) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode:
        raise RuntimeError(f"{argv[0]} {argv[1]} failed: {done.stderr.strip()[:400]}")
    return done.stdout


def image_id(image: str = IMAGE) -> str | None:
    try:
        return _text(["docker", "image", "inspect", "--format", "{{.Id}}", image]).strip()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None


def image_gb(image: str = IMAGE) -> float:
    return int(_text(["docker", "image", "inspect", "--format", "{{.Size}}", image])) / GB


def free_gb() -> float:
    """Free space on Docker's disk, as a container sees it."""
    out = _text(["docker", "run", "--rm", "--network", "none", BASE, "df", "-B1", "/"])
    return int(out.splitlines()[-1].split()[3]) / GB


def code_version() -> str:
    """The commit the bench code is at, and whether it's been changed since."""
    try:
        commit = _text(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"]).strip()
        changed = _text(["git", "-C", str(HERE), "status", "--porcelain", "--", "."]).strip()
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return "unknown"
    return f"{commit}+changes" if changed else commit


def image_for(args: list[str]) -> str:
    """The image a bench command runs in: the second for `CAE_COMMANDS`, by the command's
    own word, which follows the bench's `--out DIR` where that's given."""
    words = iter(args)
    for word in words:
        if word == "--out":
            next(words, None)
        elif not word.startswith("--out="):
            return CAE_IMAGE if word in CAE_COMMANDS else IMAGE
    return IMAGE


def container(
    work: Path,
    command: list[str],
    name: str,
    workdir: str = "/work",
    image: str = IMAGE,
    env: dict[str, str] | None = None,
) -> list[str]:
    """The `docker run` for a command over a working directory, in either image under the same
    limits. It holds no secret. `env` is more of its environment, none of it a secret either."""
    user = f"{os.getuid()}:{os.getgid()}"
    more = [word for key, value in (env or {}).items() for word in ("-e", f"{key}={value}")]
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none",
        "--cpus",
        CPUS,
        "--memory",
        MEMORY,
        "--memory-swap",
        MEMORY,
        "--pids-limit",
        PIDS,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,size={TMP_SIZE},mode=1777",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        # A script that prints without end mustn't fill Docker's disk through its log: what's
        # attached still reads the output.
        "--log-driver",
        "none",
        "--ulimit",
        f"fsize={MAX_FILE}",
        "--user",
        user,
        "-e",
        "HOME=/tmp",
        # What a script printed before it was stopped is what says where it had got to.
        "-e",
        "PYTHONUNBUFFERED=1",
        "-e",
        f"BENCH_IMAGE={image}",
        "-e",
        f"BENCH_IMAGE_ID={image_id(image) or 'unknown'}",
        "-e",
        f"BENCH_CODE={code_version()}",
        *more,
        "-v",
        f"{work.resolve()}:/work",
        "-v",
        f"{HERE}:{MOUNT}:ro",
        "-w",
        workdir,
        image,
        *command,
    ]


def bench_command(args: list[str]) -> list[str]:
    """A bench command as its container runs it (`image_for` says which image that is). `-I` so
    nothing in /work can shadow an import."""
    if image_for(args) == CAE_IMAGE:
        return [CAE_PYTHON, "-I", "-c", CAE_ENTRY, *args]
    return ["python", "-I", "-c", ENTRY, *args]


def missing(image: str) -> str:
    """What to say when an image isn't built."""
    name = next(n for n, (tag, _, _) in IMAGES.items() if tag == image)
    return f"no image {image}: run `scripts/bench.py build{'' if name == 'bench' else ' ' + name}`"


#: The helpers that run in the second image: `arm` reads a part's shape with FreeCAD
#: (mqucifer/print-shop#150).
CAE_HELPERS = ("arm",)


def helper_image(module: str) -> str:
    """The image one of the bench's helpers runs in."""
    return CAE_IMAGE if module in CAE_HELPERS else IMAGE


def helper(module: str, args: list[str]) -> list[str]:
    """One of the bench package's own modules as the container runs it, `-I` too, so nothing a
    run left in /work can stand in for it."""
    entry = f"import sys; sys.path.insert(0, '/opt/bench'); from bench.{module} import main; main()"
    if module in CAE_HELPERS:
        # FreeCAD's modules are beside its libraries there, as for `CAE_ENTRY`.
        entry = entry.replace("import sys; ", "import sys; sys.path.append('/opt/conda/lib'); ", 1)
        return [CAE_PYTHON, "-I", "-c", entry, *args]
    return ["python", "-I", "-c", entry, *args]


def run(
    args: list[str],
    work: Path,
    timeout: int = TIMEOUT,
    python: bool = False,
    image: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bench command, or a script with `python=True`, in a container over `work`. A command
    runs in its own image; a script in the first, or in `image`, where `CAE_IMAGE`'s Python has
    FreeCAD's modules on its path."""
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    if python and image == CAE_IMAGE:
        start = "import sys, runpy; sys.path.append('/opt/conda/lib'); del sys.argv[0]; "
        start += "runpy.run_path(sys.argv[0], run_name='__main__')"
        command = [CAE_PYTHON, "-c", start, *args]
    else:
        command = ["python", *args] if python else bench_command(args)
    argv = container(work, command, name, image=image or (IMAGE if python else image_for(args)))
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        message = f"verdict: NO ANSWER: it ran past the {timeout} s limit and was stopped\n"
        return subprocess.CompletedProcess(argv, 2, message, "")
    if done.returncode == KILLED:
        # A process that's killed takes its buffered output with it, so this may be all there is.
        message = f"verdict: NO ANSWER: it was killed, most likely at the {MEMORY} memory limit\n"
        return subprocess.CompletedProcess(argv, 2, done.stdout + message, done.stderr)
    return done


def others(image: str) -> list[str]:
    """Other tags of an image's repository on this machine: each is disk a build left behind."""
    name = image.rpartition(":")[0]
    try:
        out = _text(["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", name])
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return []
    return sorted(t for t in out.split() if t != image and not t.endswith(":<none>"))


def build(which: str = "bench") -> int:
    """Build one image, unless the disk would fall under its floor with it and with whichever
    other image isn't built yet. Prints what it found."""
    if which not in IMAGES:
        print(f"no image called {which}: they're {', '.join(IMAGES)}")
        return 3
    image, limit, folder = IMAGES[which]
    free = free_gb()
    print(f"free on Docker's disk: {free:.1f} GB; floor {FLOOR_GB} GB; {image}'s limit {limit} GB")
    # Room for every image that isn't here yet: this one, and the other if it's still to build.
    absent = {tag: most for tag, most, _ in IMAGES.values() if image_id(tag) is None}
    if image in absent and free - sum(absent.values()) < FLOOR_GB:
        room = " and ".join(f"{tag} ({most} GB)" for tag, most in absent.items())
        print(
            f"refused: {room} could leave {free - sum(absent.values()):.1f} GB. "
            "Nothing was built."
        )
        return 1
    before = image_id(image)
    done = subprocess.run(
        ["docker", "build", "--memory", MEMORY, "-t", image, str(HERE / folder)], check=False
    )
    if done.returncode:
        print(f"the build failed: {done.returncode}")
        return done.returncode
    if before is not None and image_id(image) != before:
        # The image this build replaced has no tag now, and still holds its disk: a rebuild of
        # the second image left 3.2 GB behind that way. Docker refuses if another tag has it.
        subprocess.run(["docker", "image", "rm", before], capture_output=True, check=False)
        print(f"removed the {image} this replaced, {before[:19]}")
    return check()


def check() -> int:
    """Each image that's built, and the disk, against their limits."""
    built = {tag: (image_id(tag), most) for tag, most, _ in IMAGES.values()}
    if built[IMAGE][0] is None:
        print(missing(IMAGE))
        return 1
    free = free_gb()
    over = free < FLOOR_GB
    for tag, (found, most) in built.items():
        if found is None:
            print(f"{missing(tag)}, for {' and '.join(CAE_COMMANDS)}")
            continue
        size = image_gb(tag)
        print(f"image {tag} {found[:19]}: {size:.2f} GB (limit {most})")
        over = over or size > most
        left = others(tag) if tag == CAE_IMAGE else []
        if left:
            print(f"  other tags of it are here: {', '.join(left)}. `docker image rm` each")
    print(f"free {free:.1f} GB (floor {FLOOR_GB})")
    if over:
        print("over the limit: an image is over its limit, or the disk is under its floor")
        return 1
    print("within the limits")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["build"]:
        return build(*argv[1:2])
    if argv[:1] == ["check"]:
        return check()
    work = Path.cwd()
    # `python script.py` in the first image, `freecad script.py` in the second.
    python = argv[:1] in (["python"], ["freecad"])
    image = CAE_IMAGE if argv[:1] == ["freecad"] else IMAGE if python else image_for(argv)
    if image_id(image) is None:
        print(missing(image))
        return 3
    done = run(argv[1:] if python else argv, work, python=python, image=image)
    print(done.stdout, end="")
    if done.stderr:
        print(done.stderr, end="")
    return done.returncode
