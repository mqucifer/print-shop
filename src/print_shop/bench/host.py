"""Running the bench from the machine: the container's limits, and the disk they protect.

Docker's disk here is shared with LiteLLM's database, crew's workspaces and any running tick,
and it filled on 2026-11-01. So there's one image, of a stated size, and a build that would
take the disk under its floor refuses. Geometry code never has a network, a writable image or
more than two CPUs and 3 GiB. Standard library only: this runs outside the image.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

IMAGE = "print-shop/bench:0.1"
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
#: The exit code of a process the kernel killed, as out-of-memory does.
KILLED = 137
#: A command that runs longer than this has no answer, in seconds.
TIMEOUT = 600
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent.parent.parent
#: The container's own mount of this package: read-only, so nothing in it can change the bench.
MOUNT = "/opt/bench/bench"
ENTRY = "import sys; sys.path.insert(0, '/opt/bench'); from bench.cli import main; sys.exit(main())"
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


def container(work: Path, command: list[str], name: str, workdir: str = "/work") -> list[str]:
    """The `docker run` for a command over a working directory. It holds no secret."""
    user = f"{os.getuid()}:{os.getgid()}"
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
        "--user",
        user,
        "-e",
        "HOME=/tmp",
        "-e",
        f"BENCH_IMAGE={IMAGE}",
        "-e",
        f"BENCH_IMAGE_ID={image_id() or 'unknown'}",
        "-e",
        f"BENCH_CODE={code_version()}",
        "-v",
        f"{work.resolve()}:/work",
        "-v",
        f"{HERE}:{MOUNT}:ro",
        "-w",
        workdir,
        IMAGE,
        *command,
    ]


def bench_command(args: list[str]) -> list[str]:
    """A bench command as the container runs it. `-I` so nothing in /work can shadow an import."""
    return ["python", "-I", "-c", ENTRY, *args]


def run(
    args: list[str], work: Path, timeout: int = TIMEOUT, python: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a bench command, or a script with `python=True`, in a container over `work`."""
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    command = ["python", *args] if python else bench_command(args)
    argv = container(work, command, name)
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


def build() -> int:
    """Build the one image, unless the disk would fall under its floor. Prints what it found."""
    free = free_gb()
    print(
        f"free on Docker's disk: {free:.1f} GB; floor {FLOOR_GB} GB; "
        f"image limit {IMAGE_LIMIT_GB} GB"
    )
    if image_id() is None and free - IMAGE_LIMIT_GB < FLOOR_GB:
        print(f"refused: the image could leave {free - IMAGE_LIMIT_GB:.1f} GB. Nothing was built.")
        return 1
    done = subprocess.run(
        ["docker", "build", "--memory", MEMORY, "-t", IMAGE, str(HERE / "image")], check=False
    )
    if done.returncode:
        print(f"the build failed: {done.returncode}")
        return done.returncode
    return check()


def check() -> int:
    """The image and the disk against their limits."""
    found = image_id()
    if found is None:
        print(f"no image {IMAGE}: run `scripts/bench.py build`")
        return 1
    size, free = image_gb(), free_gb()
    print(f"image {IMAGE} {found[:19]}: {size:.2f} GB (limit {IMAGE_LIMIT_GB}); free {free:.1f} GB")
    if size > IMAGE_LIMIT_GB or free < FLOOR_GB:
        print(
            f"over the limit: the image may be {IMAGE_LIMIT_GB} GB, and the floor is {FLOOR_GB} GB"
        )
        return 1
    print("within the limits")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["build"]:
        return build()
    if argv[:1] == ["check"]:
        return check()
    if image_id() is None:
        print(f"no image {IMAGE}: run `scripts/bench.py build`")
        return 3
    work = Path.cwd()
    python = argv[:1] == ["python"]
    done = run(argv[1:] if python else argv, work, python=python)
    print(done.stdout, end="")
    if done.stderr:
        print(done.stderr, end="")
    return done.returncode
