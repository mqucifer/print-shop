"""Running the bench from the machine: the container's limits, and the disk they protect.

Docker's disk here is shared with LiteLLM's database, crew's workspaces and any running tick,
and it filled on 2026-11-01. So each image is of a stated size, and a build that would take
the disk under its floor refuses. Geometry code never has a network, a writable image or more
than two CPUs and 3 GiB. Standard library only: this runs outside the image.

There are two images (mqucifer/print-shop#142). `print-shop/bench` holds CadQuery and the
second-check libraries, and runs every command but two. `print-shop/cae` holds FreeCAD, gmsh and
CalculiX, and runs `fit-on`, `bend`, `load` (mqucifer/print-shop#156) and `optimise`, with BESO
(mqucifer/print-shop#286), under the same limits. The disk guard counts both: a build leaves room
for whichever of them isn't built yet.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
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
CAE_IMAGE = "print-shop/cae:0.2"
CAE_LIMIT_GB = 4.0
#: The commands that run in it. Every other runs in `IMAGE`.
CAE_COMMANDS = ("fit-on", "bend", "load", "optimise")
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
#: Longer for a command that solves the part tens of times over: `optimise`, whose BESO
#: iterations each solve the whole part (mqucifer/print-shop#286).
TIMEOUTS = {"optimise": 3600}
#: And for `load` where it reads in two passes (mqucifer/print-shop#325): the whole, then each
#: part on its own and every bolt round its hole. A made-up table of 24 parts and 56 bolts takes
#: about 1,750 s, 1,047 s of it the bolts' own readings, as in one pass. With `--parts` it's
#: asked for, so given from the start. Otherwise `load` tries one pass first, under `TIMEOUT`,
#: and is offered this (`OFFER`); it takes it up as it starts two passes, where the whole met
#: the container's memory, or, for an assembly of more than one part, where the one pass
#: wouldn't end inside `TIMEOUT` (mqucifer/print-shop#331), by one line on its stderr (`TAKEN`),
#: and only then does the host wait for it. Any other `load` stops at `TIMEOUT`, as before. A
#: one pass that could go on to two passes says so first (`ABLE`), so that where the host stops
#: it at `TIMEOUT`, past what its clock foresaw, the reply names `--parts` (`past`).
TWO_PASSES = 3600
#: And its memory, for `load` that asks for two passes with `--parts` (mqucifer/print-shop#336):
#: a design run's whole table, 37 parts with 36 through joints and 4 latched dovetails, its
#: leading thousands of small round faces, was killed at `MEMORY` meshing pass 1's whole. Read
#: under 6g it held 3.32 GiB at the most meshing that whole with its sides curved, `load`'s own
#: process at 1.0 GiB of it, and 3.66 GiB meshing it again straight-sided, before the curved
#: try's mesh was let go first. Docker here has about 12.5 GB, and at most two design runs read
#: at once: two of these are 8 of it. Every other command, and a `load` without `--parts`,
#: keeps `MEMORY`, so what each proves of the 3 GiB stands.
TWO_PASSES_MEMORY = "4g"
OFFER = "BENCH_TIMEOUT_TWO_PASSES"
TAKEN = "bench: two passes take the longer limit"
ABLE = "bench: two passes could read it"
#: What a command says on stderr as it goes (`progress.py`, mqucifer/print-shop#336): each step
#: as it begins, and the container's memory every few seconds. The host keeps the last of each,
#: and where the command is killed, says where it was and the most the container had held.
STEP_LINE = "bench: step"
MEMORY_LINE = "bench: memory"
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


def command_word(args: list[str]) -> str | None:
    """The command a bench call names, past `--out DIR`."""
    words = iter(args)
    for word in words:
        if word == "--out":
            next(words, None)
        elif not word.startswith("--out="):
            return word
    return None


def image_for(args: list[str]) -> str:
    """The image a bench command runs in: the second for `CAE_COMMANDS`, by the command's
    own word, which follows the bench's `--out DIR` where that's given."""
    return CAE_IMAGE if command_word(args) in CAE_COMMANDS else IMAGE


def container(
    work: Path,
    command: list[str],
    name: str,
    workdir: str = "/work",
    image: str = IMAGE,
    env: dict[str, str] | None = None,
    memory: str | None = None,
) -> list[str]:
    """The `docker run` for a command over a working directory, in either image under the same
    limits, but for the memory a command may be given (`memory_for`). It holds no secret. `env`
    is more of its environment, none of it a secret either."""
    memory = MEMORY if memory is None else memory
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
        memory,
        "--memory-swap",
        memory,
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


def timeout_for(args: list[str]) -> int:
    """How long a bench command may run: `TIMEOUT`, or its own in `TIMEOUTS`, or `TWO_PASSES`
    for a `load` that asks for two passes with `--parts`."""
    if asks_two_passes(args):
        return TWO_PASSES
    return TIMEOUTS.get(command_word(args) or "", TIMEOUT)


def asks_two_passes(args: list[str]) -> bool:
    """Whether a bench command is a `load` that asks for two passes with `--parts`."""
    word = command_word(args)
    return word == "load" and "--parts" in args[args.index(word) + 1 :]


def memory_for(args: list[str]) -> str:
    """The memory a bench command runs in: `TWO_PASSES_MEMORY` for a `load` that asks for two
    passes with `--parts`, and `MEMORY` for every other."""
    return TWO_PASSES_MEMORY if asks_two_passes(args) else MEMORY


def offered(args: list[str]) -> int | None:
    """The longer limit a bench command may take up as it runs, or None: `TWO_PASSES` for a
    `load` that may come to two passes, where it isn't already given it."""
    if command_word(args) == "load" and timeout_for(args) < TWO_PASSES:
        return TWO_PASSES
    return None


def run(
    args: list[str],
    work: Path,
    timeout: int | None = None,
    python: bool = False,
    image: str | None = None,
    trace: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a bench command, or a script with `python=True`, in a container over `work`. A command
    runs in its own image; a script in the first, or in `image`, where `CAE_IMAGE`'s Python has
    FreeCAD's modules on its path. A `timeout` given is the limit, and nothing is offered past
    it; otherwise a command has its own (`timeout_for`), and `load` may take up a longer one
    (`offered`). What it says as it goes (`STEP_LINE`, `MEMORY_LINE`) isn't passed on, but put
    in `trace` where one's given."""
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    longer = None
    if timeout is None:
        timeout = TIMEOUT if python else timeout_for(args)
        longer = None if python else offered(args)
    if python and image == CAE_IMAGE:
        start = "import sys, runpy; sys.path.append('/opt/conda/lib'); del sys.argv[0]; "
        start += "runpy.run_path(sys.argv[0], run_name='__main__')"
        command = [CAE_PYTHON, "-c", start, *args]
    else:
        command = ["python", *args] if python else bench_command(args)
    # The command's own limit, so a command that can see it coming stops before it and says
    # where it got to (`load` in two passes, mqucifer/print-shop#301); and the longer one it may
    # take up (mqucifer/print-shop#325).
    env = {"BENCH_TIMEOUT": str(timeout)}
    if longer is not None:
        env[OFFER] = str(longer)
    memory = MEMORY if python else memory_for(args)
    argv = container(
        work,
        command,
        name,
        image=image or (IMAGE if python else image_for(args)),
        env=env,
        memory=memory,
    )
    done, limit, could = _waited(argv, timeout, longer)
    if done is None:
        subprocess.run(["docker", "kill", name], capture_output=True, check=False)
        return subprocess.CompletedProcess(argv, 2, past(limit, could), "")
    err, said = progress_lines(done.stderr)
    if trace is not None:
        trace.extend(said)
    if done.returncode == KILLED:
        # A process that's killed takes its buffered output with it, so this may be all there is.
        return subprocess.CompletedProcess(argv, 2, done.stdout + killed(said, memory), err)
    return subprocess.CompletedProcess(argv, done.returncode, done.stdout, err)


def progress_lines(stderr: str) -> tuple[str, list[str]]:
    """A command's stderr, less what it said as it went (`STEP_LINE`, `MEMORY_LINE`); and those
    lines."""
    kept, said = [], []
    for line in stderr.splitlines(keepends=True):
        if line.startswith((STEP_LINE + " ", MEMORY_LINE + " ")):
            said.append(line.rstrip("\n"))
        else:
            kept.append(line)
    return "".join(kept), said


def _gib(count: str) -> str:
    return f"{int(count) / 2**30:.2f} GiB" if count.isdigit() else "unknown"


def killed(said: list[str], memory: str | None = None) -> str:
    """The verdict of a command the kernel killed: where it had said it was, the step it was in
    and when, and the most the container had held when it last said (mqucifer/print-shop#336).
    A command that says nothing as it goes is said to be killed, as before. `memory` is the
    limit it ran under, `MEMORY` unless said."""
    memory = MEMORY if memory is None else memory
    message = f"verdict: NO ANSWER: it was killed, most likely at the {memory} memory limit"
    steps = [
        line[len(STEP_LINE) + 1 :].split(" ", 1) for line in said if line.startswith(STEP_LINE)
    ]
    counts = [
        line[len(MEMORY_LINE) + 1 :].split() for line in said if line.startswith(MEMORY_LINE)
    ]
    if steps and len(steps[-1]) == 2:
        seconds, what = steps[-1]
        message += f". Where it was: {what}, from {seconds} s in"
    if counts:
        seconds, *fields = counts[-1]
        found = dict(f.split("=", 1) for f in fields if "=" in f)
        message += (
            f". The container's memory: at most {_gib(found.get('peak', '-'))} of its "
            f"{memory} by {seconds} s in, when it last said, {_gib(found.get('current', '-'))} "
            f"then, {_gib(found.get('shmem', '-'))} of it files in /tmp"
        )
    return message + "\n"


def past(limit: int, could: bool) -> str:
    """The verdict of a command stopped at its limit; and where it was a `load`'s one pass, of
    an assembly two passes could read (`ABLE`), and they weren't started, what would read it
    (mqucifer/print-shop#331)."""
    said = f"verdict: NO ANSWER: it ran past the {limit} s limit and was stopped"
    if could:
        said += (
            ", in one pass. Two passes may read this assembly, and are given the hour, "
            f"{TWO_PASSES} s: run it again with --parts, which asks for them from the start"
        )
    return said + "\n"


#: How often the host looks whether a command has ended, or taken up its longer limit, in s.
POLL = 0.5


def _waited(
    argv: list[str], timeout: int, longer: int | None
) -> tuple[subprocess.CompletedProcess[str] | None, int, bool]:
    """Run `argv` to its end, or None where it ran past its limit; the limit it ran under:
    `timeout`, or `longer` from when the command's stderr says it took it up (`TAKEN`); and,
    where it was stopped, whether that was in a one pass that said two passes could read it
    (`ABLE`): under `timeout`, as the host read it at the moment it stopped it, whatever the
    command says while it's being stopped. Those lines are the host's, and aren't passed on."""
    if longer is None:
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return None, timeout, False
        return done, timeout, False
    began = time.monotonic()
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out: list[str] = []
    err: list[str] = []
    taken = threading.Event()
    able = threading.Event()

    def read_out() -> None:
        assert process.stdout is not None
        out.extend(process.stdout)

    def read_err() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if line.rstrip("\n") == TAKEN:
                taken.set()
            elif line.rstrip("\n") == ABLE:
                able.set()
            else:
                err.append(line)

    readers = [threading.Thread(target=f, daemon=True) for f in (read_out, read_err)]
    for reader in readers:
        reader.start()
    code: int | None = None
    could = False
    while code is None:
        limit = longer if taken.is_set() else timeout
        left = limit - (time.monotonic() - began)
        if left <= 0:
            # Read as it's stopped: a line that lands while it's being stopped changes nothing.
            could = able.is_set() and limit == timeout
            process.kill()
            process.wait()
            break
        try:
            code = process.wait(timeout=min(left, POLL))
        except subprocess.TimeoutExpired:
            continue
    for reader in readers:
        reader.join(timeout=30)
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            pipe.close()
    if code is None:
        return None, limit, could
    return subprocess.CompletedProcess(argv, code, "".join(out), "".join(err)), limit, False


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
