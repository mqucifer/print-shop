"""Bench jobs on another host's Docker, over SSH (mqucifer/print-shop#373).

The Sponsor, 2026-11-27: "I just want to ensure we're actually maximizing our available
processing across many machines with different resources." A bench job (`job.BenchJob`) is its
command over a folder of its files, within its own limits, so any host with the same image can
run it. This runs one on a host named in the Mac's settings:

    BENCH_HOSTS=spark
    # the SSH target, as ~/.ssh/config names it
    BENCH_HOST_SPARK_SSH=spark
    # the cores its jobs are pinned to (--cpuset-cpus)
    BENCH_HOST_SPARK_CORES=5-9,15-19
    # the memory its jobs may hold at once
    BENCH_HOST_SPARK_MEMORY=15g
    # the MemFree never to go under
    BENCH_HOST_SPARK_FLOOR=10g

Each comment is a line of its own: the settings' reader takes all of a line after `=` as its
value. Every one of them is stated: nothing is guessed, and a host with one missing is refused. One
more may be (mqucifer/print-shop#380):

    # the larger memory a `load` is offered there
    BENCH_HOST_SPARK_LOAD_MEMORY=12g

A `load` whose read met its container's memory, here or on a remote, is offered a host that
states one larger than that container's: the same command, read there in this memory, held within
the host's budget and over its floor as any job is (`pool.Pool.offer`). A host that states none is
offered nothing; one that states it as anything but a size over 4g is refused. The budget,
`_MEMORY`, is this process's own, not shared with another process's jobs there; what keeps the
host safe across them is the MemFree floor, which counts every bench container there by its
label.

**What a job does there** is what it does here (`host.run`), by that host's own Docker
(`docker -H ssh://TARGET`): the same `docker run`, with the same limits, user and no network, its
cores pinned. Its files go into a Docker volume of its own, and the bench's code at this
checkout's state into another, mounted read-only where this one's is; nothing is written on the
host's own disk, and nothing is installed there. The reply comes back, and the files it says it
saved are brought back into the job's folder. Its record names the host (`BENCH_HOST`, read by
`report.how_it_ran`). Its volumes are removed as it ends, however it ends.

**It touches only its own:** every container and volume it makes is named `print-bench-` and a
new id, and it never asks Docker about anything else on the host. What a host's free memory
is, it reads from `/proc/meminfo` in a container of its own.

**Refusals:**
- A host whose image IDs differ from this host's, either image, or that has one missing, is
  refused (`Remote.refused`): the same figures need the same image.
- A job isn't started where the host's MemFree is under its floor plus the job's limit, plus
  what this process's other jobs there may still take up (`Remote.reserve`). MemFree, not
  MemAvailable: on a unified-memory GPU machine the GPU's memory is counted in MemFree.

**Images go over** by `docker save` piped into the host's `docker load`, and are checked by ID
(`send`, as `scripts/bench.py send HOST`), where its disk keeps `host.FLOOR_GB` free with the
image, as a build here does: read in the bench's image there, so the first one goes by hand.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import contextlib
import errno
import io
import os
import re
import stat
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import host
from .budget import megabytes
from .job import HERE, BenchJob, HostLost, JobResult, Met, Stopped, saved_files

__all__ = ["HOSTS", "NoRoom", "NotPlain", "RanPastThere", "Remote", "RemoteHost", "Stopped",
           "cores_of", "cpuset", "open_plain", "send", "stated"]
# `Stopped` is the job's own (`job.Stopped`), said here too, where a remote raises it.

#: The setting that names the remote hosts, and each one's own keys after
#: `BENCH_HOST_<NAME>_`.
HOSTS = "BENCH_HOSTS"
KEYS = ("SSH", "CORES", "MEMORY", "FLOOR")
#: The key that may be stated as well: the larger memory a `load` that met its container's is
#: offered there (mqucifer/print-shop#380).
LOAD_MEMORY = "LOAD_MEMORY"
#: What every container and volume this makes is named from: the bench's own prefix
#: (`budget.CONTAINERS`), and a new id.
PREFIX = "print-bench-"
#: The images a remote host must hold, as this one does: the bench's for its helpers and most
#: commands, the second for `load`.
IMAGES = (host.IMAGE, host.CAE_IMAGE)
#: The limits of the small containers that carry files in and out and read the host's memory.
HELPER = ("--network", "none", "--memory", "256m", "--pids-limit", "64", "--cap-drop", "ALL",
          "--security-opt", "no-new-privileges", "--read-only", "--log-driver", "none")
#: How long a helper may take, in s: a job's files are a few STEP files and meshes.
HELPER_TIMEOUT = 600
#: This host's own Docker, which an image is sent from.
LOCAL_DOCKER = ("docker",)
#: The exit codes a bench command gives of its own (`report.EXIT`, and 3 where it can't run).
OWN_CODES = (0, 1, 2, 3)
#: Docker's own exit codes, and SSH's: the host failed to run it, the part said nothing
#: (125 the daemon refused it, as for cores the host hasn't; 126 and 127 the command couldn't
#: start; 255 the SSH session dropped).
DOCKER_CODES = (125, 126, 127, 255)
#: The label each of its containers carries its memory limit by, in MiB: so any process counts
#: every bench container on the host by its name and label alone (`docker ps`), never
#: inspecting one. One with no label counts at the most a bench command takes.
MEMORY_LABEL = "print-shop.bench.memory"
UNLABELLED_MB = megabytes(host.TWO_PASSES_MEMORY)
#: The most a job's /work may come to, in GB: the most one file may be (`host.MAX_FILE`). A
#: job isn't started where the host's Docker disk wouldn't keep `host.FLOOR_GB` free with this
#: for each of this process's jobs there, and one whose /work came to more is said.
WORK_GB = int(host.MAX_FILE) / host.GB
#: How long past its longest limit a job there is killed inside its own container, in s: so
#: it can't outlive the study that started it, whatever became of this host.
DEADLINE_MARGIN = 300
#: How a command stopped at its limit replies (`host.past`).
RAN_PAST = host.RAN_PAST
#: The label each holds its disk bound by, in GB; one with none counts at `WORK_GB`.
DISK_LABEL = "print-shop.bench.disk"
#: The labels each of its volumes carries: whose, the job's container's name, and when it was
#: made, in s since the epoch.
OWNER_LABEL, JOB_LABEL, CREATED_LABEL = ("print-shop.bench.owner", "print-shop.bench.job",
                                         "print-shop.bench.created")
#: The longest a job's hold may last, in s: its longest limit and deadline, its files there and
#: back. A volume older than this whose job's container is gone was left by a process that
#: ended without removing it, and is removed before a study starts there (`clear_stale`).
STALE_S = host.TWO_PASSES + DEADLINE_MARGIN + 2 * HELPER_TIMEOUT
#: How often its Docker disk is read while its jobs run, in s: one that falls under the floor
#: has them killed (`Remote.run`).
DISK_LOOK = 30.0

_NAME = re.compile(r"[a-z][a-z0-9]{0,30}")
#: An SSH target as ~/.ssh/config or `user@host` names it: never an option, never a command.
_TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,120}")


def cores_of(text: str) -> tuple[int, ...]:
    """The cores `5-9,15-19` names, in order.

    Raises:
        ValueError: If it doesn't name cores as `--cpuset-cpus` takes them.
    """
    cores: set[int] = set()
    for part in text.replace(" ", "").split(","):
        found = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not found:
            raise ValueError(f"{text!r} isn't cores as --cpuset-cpus takes them, as 5-9,15-19")
        low, high = int(found.group(1)), int(found.group(2) or found.group(1))
        if high < low:
            raise ValueError(f"{part!r} runs backwards")
        cores.update(range(low, high + 1))
    return tuple(sorted(cores))


def cpuset(cores: tuple[int, ...] | list[int]) -> str:
    """Cores as `--cpuset-cpus` takes them, runs joined: 5-9,15-19."""
    runs: list[list[int]] = []
    for core in sorted(cores):
        if runs and core == runs[-1][1] + 1:
            runs[-1][1] = core
        else:
            runs.append([core, core])
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in runs)


@dataclass(frozen=True)
class RemoteHost:
    """A host the Mac's settings name, as they state it."""

    name: str
    ssh: str
    cores: tuple[int, ...]
    #: The memory its jobs may hold at once, and the MemFree never to go under, in MiB.
    memory_mb: int
    floor_mb: int
    #: The larger memory a `load` that met its container's is offered there, in MiB, or 0 where
    #: none is stated (mqucifer/print-shop#380).
    load_mb: int = 0

    def docker(self) -> tuple[str, ...]:
        """Its Docker, over SSH."""
        return ("docker", "-H", f"ssh://{self.ssh}")

    def said(self) -> str:
        return (f"{self.name} ({self.ssh}): cores {cpuset(self.cores)}, "
                f"{self.memory_mb / 1024:.1f} GiB for jobs, MemFree kept over "
                f"{self.floor_mb / 1024:.1f} GiB"
                + (f", a load that met its memory offered {self.load_mb / 1024:.1f} GiB"
                   if self.load_mb else ""))


def stated(env: dict[str, str]) -> list[RemoteHost]:
    """The remote hosts the settings name, each with every one of its keys.

    Raises:
        ValueError: If a host is named and one of its keys is missing or isn't what it says.
    """
    names = [n for n in re.split(r"[,\s]+", env.get(HOSTS, "").strip()) if n]
    found = []
    for name in names:
        if not _NAME.fullmatch(name) or name == HERE:
            raise ValueError(f"{HOSTS} names {name!r}: a host's name is lower-case letters and "
                             f"digits, and not {HERE!r}")
        keys = {k: f"BENCH_HOST_{name.upper()}_{k}" for k in KEYS}
        missing = [key for key in keys.values() if not env.get(key, "").strip()]
        if missing:
            raise ValueError(f"{HOSTS} names {name}, but the settings don't state "
                             f"{', '.join(missing)}: nothing about a host is guessed")
        target = env[keys["SSH"]].strip()
        if not _TARGET.fullmatch(target):
            raise ValueError(f"{keys['SSH']} is {target!r}: an SSH target is a name as "
                             "~/.ssh/config gives it, or user@host")
        cores = cores_of(env[keys["CORES"]])
        memory, floor = (megabytes(env[keys[k]]) for k in ("MEMORY", "FLOOR"))
        if len(cores) < int(host.CPUS) or memory < megabytes(host.MEMORY):
            raise ValueError(f"{name} holds no bench command: one takes {host.CPUS} cores and "
                             f"{host.MEMORY}")
        load_key = f"BENCH_HOST_{name.upper()}_{LOAD_MEMORY}"
        given = env.get(load_key, "").strip()
        load = 0
        if given:
            # Stated, it's a size over a load's own, never read as none (`12` is 12 bytes).
            try:
                load = megabytes(given) if given[-1:].lower() in ("m", "g", "t") else 0
            except ValueError:
                load = 0
        if given and not megabytes(host.TWO_PASSES_MEMORY) < load <= memory:
            raise ValueError(f"{load_key} is {given!r}: the larger memory a load is offered "
                             f"there is a size, as 12g, more than a load's own "
                             f"{host.TWO_PASSES_MEMORY}, and within its jobs' "
                             f"{env[keys['MEMORY']].strip()}; to offer none, leave it out")
        found.append(RemoteHost(name, target, cores, memory, floor, load))
    if len({h.name for h in found}) != len(found):
        raise ValueError(f"{HOSTS} names a host twice")
    return found


class NoRoom(RuntimeError):
    """A host's free memory or disk, read now, holds no more of this job: its message says
    why."""


class RanPastThere(HostLost):
    """A job stopped at its limit on a remote host: on time alone, which a slower host can
    take where this one answers, so it's read again on another and never taken as the part's
    NO ANSWER. The host stays in use."""




def _exit_said(code: int) -> str:
    """Docker's or SSH's exit code, said with whose it is."""
    return f"ssh's exit {code}" if code == 255 else f"docker's exit {code}"


def words_only(text: str) -> str:
    """What Docker or a helper said, its words alone: every token with a path, a name, a number
    or a quote in it left out as `…`, so it can be said where the job's names and figures can't
    (mqucifer/print-shop#380). `tar: work/leg.step: Cannot open: No space left on device` is
    `tar: … Cannot open: No space left on device`."""
    kept: list[str] = []
    for token in text.split():
        bare = token.strip(",;()[]")
        word = bare if re.fullmatch(r"[A-Za-z]{1,24}:?", bare) else "…"
        if not (word == "…" and kept and kept[-1] == "…"):
            kept.append(word)
    return " ".join(kept)[:300]


def _failed_said(message: str) -> str:
    """How a call there failed, as a kind with no name or figure: SSH or Docker not answering,
    an image Docker hasn't, a file not sent back, or Docker's refusal."""
    if "didn't answer" in message:
        return "ssh or docker didn't answer"
    if re.search(r"No such image|Unable to find image|pull access denied", message):
        return "an image it hasn't"
    if "didn't send back" in message:
        return "a file it saved wasn't sent back"
    if re.search(r"ssh:|Connection (refused|reset)|connection reset", message):
        return "ssh failed"
    return "docker refused it"


def _user() -> tuple[int, int]:
    return os.getuid(), os.getgid()


def _meminfo(text: str) -> dict[str, int]:
    """`/proc/meminfo`'s figures, in MiB."""
    return {k: int(v) // 1024 for k, v in re.findall(r"^(\w+):\s+(\d+) kB", text, re.MULTILINE)}


class NotPlain(ValueError):
    """A job's file that isn't a plain file inside its folder: a link, at any step of its path,
    or not a regular file. Never sent, never written through (mqucifer/print-shop#380): a link
    would send a file from outside the folder to the host, which a container here, with only
    the folder mounted, could never read."""


def open_plain(folder: Path, name: str, flags: int = os.O_RDONLY,
               make_dirs: bool = False) -> int:
    """A file descriptor for `name` in `folder`, opened one step at a time, never through a link
    and only to a regular file, as `rounds.paths.open_inside` opens one; its folders made where
    `make_dirs`.

    Raises:
        NotPlain: If a step of it is a link, or it isn't a regular file, or it leads out.
        FileNotFoundError: If it isn't there.
    """
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or ".." in parts:
        raise NotPlain(f"the job's file {name!r} isn't inside its folder")
    here = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=here)
            except FileNotFoundError:
                if not make_dirs:
                    raise
                os.mkdir(part, 0o755, dir_fd=here)
                nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=here)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise NotPlain(f"the job's file {name!r} is under a link, or under what "
                                   "isn't a folder") from exc
                raise
            os.close(here)
            here = nested
        try:
            found = os.open(parts[-1], flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644, dir_fd=here)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise NotPlain(f"the job's file {name!r} is a link") from exc
            raise
        if not stat.S_ISREG(os.fstat(found).st_mode):
            os.close(found)
            raise NotPlain(f"the job's file {name!r} isn't a regular file")
        return found
    finally:
        os.close(here)


def bundle(job: BenchJob) -> bytes:
    """A job's files under `work/`, its owner the user its container runs as, and the bench's
    code as this checkout has it (what `host.container` mounts here) under `code/`: one tar."""
    uid, gid = _user()
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.GNU_FORMAT) as tar:

        def folder(name: str, owner: tuple[int, int]) -> None:
            info = tarfile.TarInfo(name)
            info.type, info.mode = tarfile.DIRTYPE, 0o755
            info.uid, info.gid = owner
            tar.addfile(info)

        def put(path: Path, name: str, owner: tuple[int, int]) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mode = path.stat().st_size, (path.stat().st_mode & 0o777) | 0o444
            info.uid, info.gid = owner
            with path.open("rb") as stream:
                tar.addfile(info, stream)

        def put_plain(given: str, name: str, owner: tuple[int, int]) -> None:
            # Read through no link, from what was opened: never a file outside the folder.
            with os.fdopen(open_plain(job.folder, given), "rb") as stream:
                found = os.fstat(stream.fileno())
                info = tarfile.TarInfo(name)
                info.size, info.mode = found.st_size, (found.st_mode & 0o777) | 0o444
                info.uid, info.gid = owner
                tar.addfile(info, stream)

        folder("work", (uid, gid))
        made = {"work"}
        # In order, so each folder's files are together: the helper's tar, as root with no
        # power over another's files, restores a folder's owner, the job's user, as it moves
        # past it, and couldn't write into it again (mqucifer/print-shop#380).
        for name in sorted(set(job.files), key=lambda n: PurePosixPath(n).as_posix()):
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"the job's file {name!r} isn't inside its folder")
            for parent in reversed(relative.parents[:-1]):
                if f"work/{parent}" not in made:
                    folder(f"work/{parent}", (uid, gid))
                    made.add(f"work/{parent}")
            put_plain(name, f"work/{relative}", (uid, gid))
        folder("code", (0, 0))
        for path in sorted(host.HERE.rglob("*")):
            inside = path.relative_to(host.HERE)
            if "__pycache__" in inside.parts or path.suffix == ".pyc" or path.is_symlink():
                continue
            if path.is_dir():
                folder(f"code/{inside.as_posix()}", (0, 0))
            elif path.is_file():
                put(path, f"code/{inside.as_posix()}", (0, 0))
    return out.getvalue()


def unpack(data: bytes, folder: Path, saved: tuple[str, ...]) -> None:
    """The files a job saved, from the tar its host sent back, into its folder: only those it
    said it saved, and only as plain files inside it.

    Raises:
        HostLost: If one it said it saved isn't there.
    """
    wanted = {PurePosixPath(n).as_posix(): n for n in saved}
    got: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for member in tar:
            name = PurePosixPath(member.name).as_posix().removeprefix("./")
            if name not in wanted or not member.isfile():
                continue
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                continue
            stream = tar.extractfile(member)
            if stream is None:
                continue
            # Written through no link, as a folder of its own or one the job's files left
            # there could be (mqucifer/print-shop#380): never to a file outside the folder.
            with os.fdopen(open_plain(folder, name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                                      make_dirs=True), "wb") as written:
                written.write(stream.read())
            got.add(name)
    lost = sorted(set(wanted) - got)
    if lost:
        raise HostLost(f"the host didn't send back {', '.join(lost)}")


class Remote:
    """A remote host's Docker, and the jobs this process runs there."""

    def __init__(self, stated: RemoteHost, docker: tuple[str, ...] | None = None) -> None:
        self.host = stated
        #: Its `docker`: over SSH, or as a test gives it.
        self.docker = docker or stated.docker()
        self._lock = threading.RLock()
        #: The containers this process has reserved or is running there, by name: each one's
        #: memory limit in MiB.
        self._held: dict[str, int] = {}
        #: Volumes it couldn't remove, where the host stopped answering: said, never left
        #: unsaid. And jobs whose /work came to more than `WORK_GB`.
        self.left: list[str] = []
        self.over: list[str] = []
        self._stopped = threading.Event()
        #: Its jobs whose containers are running now, and those killed as its disk fell under
        #: its floor, each with why; and the thread that watches its disk while they run.
        self._running: set[str] = set()
        self._killed: dict[str, str] = {}
        self._watching: threading.Thread | None = None
        #: The jobs whose holds are still up (`_hold`).
        self._holds: set[str] = set()

    @property
    def name(self) -> str:
        return self.host.name

    def _call(self, *args: str, data: bytes | None = None, timeout: float = 120,
              check: bool = True) -> subprocess.CompletedProcess[bytes]:
        try:
            done = subprocess.run([*self.docker, *args], input=data, capture_output=True,
                                  timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HostLost(f"{self.name}: docker {args[0]} didn't answer: {exc}") from exc
        if check and done.returncode != 0:
            said = done.stderr.decode(errors="replace").strip()[-300:]
            lost = HostLost(f"{self.name}: docker {' '.join(args[:2])} failed: "
                            f"{said or 'no reply'}")
            # How, with no name or figure: its exit code, and its words alone.
            lost.how = f"exit {done.returncode}: {words_only(said) or 'no reply'}"
            raise lost
        return done

    def answers(self) -> bool:
        """Whether its Docker answers now."""
        try:
            self._call("version", "--format", "{{.Server.Version}}", timeout=60)
        except HostLost:
            return False
        return True

    def image_id(self, image: str) -> str | None:
        done = self._call("image", "inspect", "--format", "{{.Id}}", image, check=False)
        if done.returncode != 0:
            return None
        return done.stdout.decode().strip() or None

    def free_gb(self) -> float | None:
        """Free space on its Docker's disk, as a container of the bench's image sees it, or None
        where that image isn't there to ask."""
        theirs = self.image_id(host.IMAGE)
        if theirs is None:
            return None
        done = self._run("--rm", "--name", self._new(), *self._helper(), theirs,
                          "df", "-B1", "/")
        return int(done.stdout.decode().splitlines()[-1].split()[3]) / host.GB

    def _run(self, *args: str, data: bytes | None = None, timeout: float = 120,
             check: bool = True) -> subprocess.CompletedProcess[bytes]:
        """`docker run` there, never pulling an image it hasn't."""
        return self._call("run", "--pull", "never", *args, data=data, timeout=timeout,
                          check=check)

    def _helper(self, memory_mb: int = 256) -> tuple[str, ...]:
        """A small container's limits, and what any process counts it at: a job's own helpers
        at nothing, its hold holding its memory and disk for it."""
        return (*HELPER, "--label", f"{MEMORY_LABEL}={memory_mb}", "--label", f"{DISK_LABEL}=0")

    def _bench_image(self) -> str:
        """The bench's image by the ID this host has, which the host's was checked against."""
        found = host.image_id(host.IMAGE)
        if found is None:
            raise RuntimeError(host.missing(host.IMAGE))
        return found

    def refused(self) -> str | None:
        """Why the host can't take jobs, or None: its Docker doesn't answer, or an image there
        isn't this host's, by ID."""
        if not self.answers():
            return f"{self.name}'s Docker doesn't answer over SSH ({self.host.ssh})"
        for image in IMAGES:
            mine, theirs = host.image_id(image), self.image_id(image)
            if mine is None:
                return f"{image} isn't built here: {host.missing(image)}"
            if theirs != mine:
                said = theirs[:19] if theirs else "none"
                return (f"{self.name}'s {image} is {said}, not this host's {mine[:19]}: "
                        f"`scripts/bench.py send {self.name}`")
        return None

    def _new(self) -> str:
        return f"{PREFIX}{uuid.uuid4().hex[:12]}"

    def memory_free_mb(self) -> int:
        """The host's MemFree now, in MiB, read in a container of its own."""
        return self.probe()[0]

    def probe(self) -> tuple[int, float]:
        """The host's MemFree now, in MiB, and its Docker disk's free space, in GB: read in one
        container of its own."""
        uid, gid = _user()
        done = self._run("--rm", "--name", self._new(), *self._helper(), "--user",
                          f"{uid}:{gid}", self._bench_image(), "sh", "-c",
                          "cat /proc/meminfo && df -B1 /")
        text = done.stdout.decode(errors="replace")
        found = _meminfo(text)
        if "MemFree" not in found:
            raise HostLost(f"{self.name}'s /proc/meminfo didn't say MemFree")
        try:
            disk = int(text.strip().splitlines()[-1].split()[3]) / host.GB
        except (IndexError, ValueError) as exc:
            raise HostLost(f"{self.name}'s df didn't say its Docker disk's free space") from exc
        return found["MemFree"], disk

    def others_mb(self, held: dict[str, int]) -> int:
        """The memory limits, in MiB, of the bench containers on the host that aren't this
        process's: another study's, or a run by hand (`others`)."""
        return self.others(held)[0]

    def others(self, held: dict[str, int]) -> tuple[int, float]:
        """What the bench containers on the host that aren't this process's hold: their memory
        limits in MiB, and their disk bounds in GB. By their names and labels alone (`docker
        ps`, `MEMORY_LABEL`, `DISK_LABEL`), the bench's prefix only; one with no label counts at
        `UNLABELLED_MB` and `WORK_GB`."""
        done = self._call("ps", "--filter", f"name={PREFIX}", "--format",
                          f'{{{{.Names}}}} {{{{.Label "{MEMORY_LABEL}"}}}} '
                          f'{{{{.Label "{DISK_LABEL}"}}}}')
        memory, disk = 0, 0.0
        for line in done.stdout.decode(errors="replace").splitlines():
            name, *labels = line.split()
            if not name.startswith(PREFIX) or any(name.startswith(h) for h in held):
                continue
            mb = labels[0] if labels else ""
            gb = labels[1] if len(labels) > 1 else ""
            memory += int(mb) if mb.isdigit() else UNLABELLED_MB
            try:
                disk += float(gb)
            except ValueError:
                disk += WORK_GB
        return memory, disk

    def _still_to_take(self, held: dict[str, int]) -> int:
        """What this process's jobs there may take yet, in MiB: each one's limit less what it
        holds now, all of it for one not yet started."""
        if not held:
            return 0
        done = self._call("stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}",
                          *sorted(held), check=False)
        using: dict[str, int] = {}
        for line in done.stdout.decode(errors="replace").splitlines():
            name, _, usage = line.partition(" ")
            if name in held and usage:
                try:
                    using[name] = megabytes(usage.split("/")[0].strip())
                except ValueError:
                    continue
        return sum(max(0, limit - using.get(name, 0)) for name, limit in held.items())

    def reserve(self, memory_mb: int) -> str:
        """A name for a job of `memory_mb` to start under, as the host's free memory holds it:
        read now, one reservation at a time, its MemFree over its floor plus the job's limit,
        plus what this process's other jobs there may take yet. `release` it as the job ends.

        Raises:
            NoRoom: If its free memory doesn't hold it now, saying what it read.
            HostLost: If the host doesn't answer.
        """
        with self._lock:
            free, disk = self.probe()
            pending = self._still_to_take(self._held)
            self._room(free, disk, memory_mb, pending, self.others(self._held), len(self._held) + 1)
            name = self._new()
            # Held from now to its end by a container of its own, labelled with its memory and
            # disk: so another process counts it whole while its files go over, before its own
            # container starts. Then counted again, now another's hold made at the same moment
            # shows, and let go where the two together don't fit.
            self._held[name] = memory_mb
            try:
                self._hold(name, memory_mb)
                self._room(free, disk, memory_mb, pending, self.others(self._held), len(self._held))
            except BaseException:
                # NoRoom, or SSH failing as the hold was made or the count read again: the
                # reservation is let go, and its hold removed by its own name where it can be,
                # though `docker run -d` may have made it before it failed to say so.
                self._held.pop(name, None)
                self._holds.discard(name)
                with contextlib.suppress(HostLost):
                    self._call("rm", "-f", f"{name}-hold", check=False, timeout=60)
                raise
            return name

    def _room(self, free: int, disk: float, memory_mb: int, pending: int,
              others: tuple[int, float], jobs: int) -> None:
        """Raises NoRoom where the host's MemFree or its Docker disk, as read, doesn't hold one
        more job of `memory_mb` over their floors."""
        need = self.host.floor_mb + memory_mb + pending + others[0]
        if free < need:
            raise NoRoom(f"{self.name}'s MemFree is {free / 1024:.1f} GiB, under its floor "
                         f"of {self.host.floor_mb / 1024:.1f} GiB plus the job's "
                         f"{memory_mb / 1024:.1f} GiB, {pending / 1024:.1f} GiB this "
                         f"study's other jobs there may take yet and {others[0] / 1024:.1f} "
                         "GiB other bench containers there hold")
        room = host.FLOOR_GB + WORK_GB * jobs + others[1]
        if disk < room:
            raise NoRoom(f"{self.name}'s Docker disk has {disk:.1f} GB free, under its "
                         f"floor of {host.FLOOR_GB} GB plus {WORK_GB:.1f} GB for each of "
                         f"this study's jobs there and {others[1]:.1f} GB other bench jobs "
                         "there may write")

    def _hold(self, name: str, memory_mb: int) -> None:
        """A container that holds a job's memory and disk on the host, by its labels, until the
        job ends (`_clear`) or `STALE_S` passes."""
        uid, gid = _user()
        self._run("-d", "--rm", "--name", f"{name}-hold", *HELPER,
                  "--label", f"{MEMORY_LABEL}={memory_mb}", "--label", f"{DISK_LABEL}={WORK_GB}",
                  "--user", f"{uid}:{gid}", self._bench_image(), "sleep", str(STALE_S))
        with self._lock:
            self._holds.add(name)

    def release(self, name: str) -> None:
        """Let a reservation go: its hold too, where its job never ran to remove it."""
        with self._lock:
            self._held.pop(name, None)
            holding = name in self._holds
            self._holds.discard(name)
        if holding:
            self._call("rm", "-f", f"{name}-hold", check=False, timeout=60)

    def held(self) -> set[str]:
        with self._lock:
            return set(self._held)

    def run(self, job: BenchJob, cores: tuple[int, ...] | list[int],
            name: str | None = None) -> JobResult:
        """Run a job there, under `name` (`reserve`'s), pinned to `cores`: its files and the
        bench's code sent into volumes of its own, the same `docker run` as here, and what it
        saved brought back into its folder. Its volumes are removed as it ends.

        Raises:
            FileNotFoundError: If its files aren't in its folder.
            NotPlain: If one is a link, or under one, or isn't a regular file.
            HostLost: If the host failed it, Docker's own or SSH's failure, or gave no reply:
                never the part's answer. `RanPastThere` where it stopped at its limit.
            Stopped: If the study was stopped before it started.
        """
        if self._stopped.is_set():
            raise Stopped(f"the study was stopped before `{' '.join(job.args)}` started")
        missing = []
        for given in job.files:
            # Each a plain file in its folder, through no link, before anything goes over.
            try:
                os.close(open_plain(job.folder, given))
            except FileNotFoundError:
                missing.append(given)
        if missing:
            raise FileNotFoundError(f"the job's files aren't in {job.folder}: "
                                    f"{', '.join(missing)}")
        name = name or self._new()
        volumes = (f"{name}-work", f"{name}-code")
        memory_mb = megabytes(job.memory or host.MEMORY)
        said = f"`{' '.join(job.args)}`"
        with self._lock:
            reserved = name in self._held
            self._held.setdefault(name, memory_mb)
        #: The step it's at, which a failure is said by (`HostLost.kind`).
        stage = ["holding its memory"]
        try:
            if not reserved:
                self._hold(name, memory_mb)
            stage[0] = "making its volumes"
            for volume in volumes:
                self._call("volume", "create", "--label", f"{OWNER_LABEL}=print-shop",
                           "--label", f"{JOB_LABEL}={name}",
                           "--label", f"{CREATED_LABEL}={int(time.time())}", volume)
            stage[0] = "sending its files"
            self._run("--rm", "-i", "--name", f"{name}-in", *self._helper(0), "--user",
                       "0:0", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
                       "-v", f"{volumes[0]}:/mnt/work", "-v", f"{volumes[1]}:/mnt/code",
                       self._bench_image(), "tar", "-x", "--numeric-owner",
                       # Each folder's owner and mode only once all of it is in: the helper
                       # can't write into one owned by the job's user (mqucifer/print-shop#380).
                       "--delay-directory-restore", "-C", "/mnt",
                       "-f", "-", data=bundle(job), timeout=HELPER_TIMEOUT)
            if self._stopped.is_set():
                # Stopped while its files went over: it never starts.
                raise Stopped(f"the study was stopped before {said} started")
            self._watch(name)
            stage[0] = "running it"
            said_as_it_went: list[str] = []
            try:
                done = host.run(list(job.args), job.folder, timeout=job.timeout or None,
                                name=name, docker=self.docker, mounts=volumes,
                                cpuset=cpuset(cores),
                                more_env={"BENCH_HOST": self.name, **job.told()},
                                labels={MEMORY_LABEL: "0", DISK_LABEL: "0"}, by_id=True,
                                margin=DEADLINE_MARGIN, pull=False, memory=job.memory or None,
                                trace=said_as_it_went, longer=job.longer or None)
            finally:
                with self._lock:
                    self._running.discard(name)
                    killed = self._killed.pop(name, None)
            if killed is not None:
                lost = HostLost(f"{self.name} killed {said}: {killed}")
                lost.kind = "running it: killed as its Docker disk fell under its floor"
                raise lost
            if done.returncode in DOCKER_CODES or not done.stdout.strip():
                why = (done.stderr or "").strip().splitlines()[-1:] or ["no reply"]
                lost = HostLost(f"{self.name} failed {said} (exit {done.returncode}): "
                                f"{why[0][:300]}")
                lost.kind = ("running it: no reply" if done.returncode not in DOCKER_CODES
                             else f"running it: {_exit_said(done.returncode)}")
                raise lost
            if done.stdout.startswith(RAN_PAST):
                late = RanPastThere(f"{self.name} stopped {said} at its {job.timeout} s limit")
                late.kind = "running it: past its time limit"
                raise late
            saved = saved_files(done.stdout)
            stage[0] = "bringing back what it saved"
            if not saved and not self.answers():
                raise HostLost(f"{self.name} stopped answering while it ran {said}")
            if saved:
                uid, gid = _user()
                out = self._run("--rm", "--name", f"{name}-out", *self._helper(0),
                                 "--user", f"{uid}:{gid}", "-v", f"{volumes[0]}:/work:ro",
                                 self._bench_image(), "sh", "-c",
                                 'du -sk /work >&2 && exec tar -c -C /work -f - -- "$@"', "sh",
                                 *saved, timeout=HELPER_TIMEOUT)
                self._sized(out.stderr, said)
                stage[0] = "unpacking what it saved"
                unpack(out.stdout, job.folder, saved)
            return JobResult(done.returncode, done.stdout, done.stderr, saved, self.name,
                             Met.of(said_as_it_went, self.name))
        except HostLost as exc:
            if not exc.kind:
                how = getattr(exc, "how", "")
                exc.kind = f"{stage[0]}: {_failed_said(str(exc))}" + (f" ({how})" if how else "")
            raise
        finally:
            self._clear(name, volumes)

    def _watch(self, name: str) -> None:
        """Count `name` as running, and watch the host's Docker disk while any job of this
        process's runs there: where it falls under `host.FLOOR_GB`, each is killed, and said."""
        with self._lock:
            self._running.add(name)
            if self._watching is not None and self._watching.is_alive():
                return
            self._watching = threading.Thread(target=self._watch_disk, daemon=True,
                                              name=f"disk-{self.name}")
            self._watching.start()

    def _watch_disk(self) -> None:
        while True:
            time.sleep(DISK_LOOK)
            with self._lock:
                if not self._running:
                    self._watching = None
                    return
            try:
                _, disk = self.probe()
            except HostLost:
                continue
            if disk >= host.FLOOR_GB:
                continue
            with self._lock:
                names = sorted(self._running)
                for name in names:
                    self._killed[name] = (f"its Docker disk fell to {disk:.1f} GB, under its "
                                          f"floor of {host.FLOOR_GB} GB, as it ran")
            for name in names:
                self._call("kill", name, check=False, timeout=60)

    def clear_stale(self) -> tuple[list[str], list[str]]:
        """Remove print-shop's volumes on the host that a process left (its label's), older
        than `STALE_S`, whose job's container is gone. Nothing else. Which it removed, and which
        it tried to and couldn't, by name: Docker says each one it removed."""
        listed = self._call("volume", "ls", "--filter", f"label={JOB_LABEL}", "--format",
                            f'{{{{.Name}}}} {{{{.Label "{JOB_LABEL}"}}}} '
                            f'{{{{.Label "{CREATED_LABEL}"}}}}')
        alive = set(self._call("ps", "-a", "--filter", f"name={PREFIX}", "--format",
                               "{{.Names}}").stdout.decode(errors="replace").split())
        now, mine = time.time(), self.held()
        stale = []
        for line in listed.stdout.decode(errors="replace").splitlines():
            words = line.split()
            if len(words) != 3 or not words[0].startswith(PREFIX) or not words[2].isdigit():
                continue
            volume, job, made = words
            if (now - int(made) > STALE_S and job not in mine
                    and not any(a.startswith(job) for a in alive)):
                stale.append(volume)
        if not stale:
            return [], []
        gone = set(self._call("volume", "rm", "-f", *stale, check=False,
                              timeout=120).stdout.decode(errors="replace").split())
        return [v for v in stale if v in gone], [v for v in stale if v not in gone]

    def _sized(self, stderr: bytes, said: str) -> None:
        """Say a job whose /work came to more than `WORK_GB`, as `du -sk` gave it."""
        words = stderr.decode(errors="replace").split()
        if words and words[0].isdigit() and int(words[0]) * 1024 > WORK_GB * host.GB:
            with self._lock:
                self.over.append(f"{said}'s /work came to {int(words[0]) * 1024 / host.GB:.1f} "
                                 f"GB, past its {WORK_GB:.1f} GB")

    def _clear(self, name: str, volumes: tuple[str, str]) -> None:
        """Remove a job's container, where it's still there, and its volumes: its own, by name."""
        try:
            self._call("rm", "-f", name, f"{name}-in", f"{name}-out", f"{name}-hold",
                       check=False, timeout=60)
            with self._lock:
                self._holds.discard(name)
            gone = self._call("volume", "rm", "-f", *volumes, check=False, timeout=60)
            if gone.returncode != 0:
                raise HostLost(gone.stderr.decode(errors="replace").strip()[-200:])
        except HostLost:
            with self._lock:
                self.left.extend(volumes)
        finally:
            self.release(name)

    def stop(self) -> None:
        """Stop every container this process is running there: a study that's interrupted
        doesn't leave its reads running, and none it's sending files for starts. Each job's own
        end removes its volumes."""
        self._stopped.set()
        for name in self.held():
            self._call("kill", name, check=False, timeout=60)

    def ours_left(self) -> list[str]:
        """The volumes of this process's jobs still on the host: none, once they've ended."""
        done = self._call("volume", "ls", "--format", "{{.Name}}", check=False)
        mine = {v for name in self.held() for v in (f"{name}-work", f"{name}-code")}
        mine |= set(self.left)
        return sorted(v for v in done.stdout.decode().split() if v in mine)


def send(remote: Remote, images: tuple[str, ...] = IMAGES) -> int:
    """Send each image to the host, `docker save` piped into its `docker load`, where its ID
    there isn't this host's; and check every one's ID. Prints what it found; 0 where they all
    agree."""
    failed = 0
    for image in images:
        mine = host.image_id(image)
        if mine is None:
            print(host.missing(image))
            failed += 1
            continue
        if remote.image_id(image) == mine:
            print(f"{remote.name} has {image} {mine[:19]}, as here")
            continue
        free = remote.free_gb()
        size = host.image_gb(image)
        if free is None or free - size < host.FLOOR_GB:
            said = "unknown, with no bench image there to read it" if free is None else (
                f"{free:.1f} GB")
            print(f"refused: {remote.name}'s Docker disk has {said} free; {image} is "
                  f"{size:.1f} GB, and the floor {host.FLOOR_GB} GB. Nothing was sent.")
            failed += 1
            continue
        print(f"sending {image} {mine[:19]} to {remote.name}", flush=True)
        save = subprocess.Popen([*LOCAL_DOCKER, "save", image], stdout=subprocess.PIPE)
        try:
            load = subprocess.run([*remote.docker, "load"], stdin=save.stdout,
                                  capture_output=True, check=False)
        finally:
            if save.stdout is not None:
                save.stdout.close()
            save.wait()
        theirs = remote.image_id(image)
        if save.returncode or load.returncode or theirs != mine:
            said = load.stderr.decode(errors="replace").strip()[-200:]
            print(f"  {remote.name}'s {image} is {(theirs or 'none')[:19]}, not {mine[:19]}"
                  f"{': ' + said if said else ''}")
            failed += 1
        else:
            print(f"  {remote.name} has {image} {theirs[:19]}, as here")
    return 1 if failed else 0


def _settings() -> dict[str, str]:
    # The Mac's settings, read here, where they're needed: print_shop's own reader, outside the
    # image as all of this is.
    from print_shop.env import SETTINGS_PATH, load_env

    return load_env(SETTINGS_PATH)


def main(argv: list[str], env: dict[str, str] | None = None) -> int:
    """`hosts`: each remote host the settings name, whether its Docker answers, its images'
    IDs against this host's, and its MemFree against its floor. `send NAME [bench|cae]`: the
    images, or one, sent there and checked. Neither starts a job."""
    try:
        found = {h.name: h for h in stated(_settings() if env is None else env)}
    except ValueError as exc:
        print(exc)
        return 2
    if argv[:1] == ["hosts"]:
        if not found:
            print(f"no remote hosts: {HOSTS} in the settings names none")
            return 0
        worst = 0
        for stated_host in found.values():
            print(stated_host.said())
            remote = Remote(stated_host)
            why = remote.refused()
            if why:
                print(f"  refused: {why}")
                worst = 1
                continue
            free = remote.memory_free_mb()
            room = (free - stated_host.floor_mb) // megabytes(host.MEMORY)
            print(f"  its images are this host's, by ID; MemFree {free / 1024:.1f} GiB, "
                  f"{max(0, room)} jobs of {host.MEMORY} over its floor now")
        return worst
    if argv[:1] == ["send"] and len(argv) in (2, 3):
        if argv[1] not in found:
            print(f"no host {argv[1]!r} in {HOSTS}: {', '.join(found) or 'none'}")
            return 2
        which: tuple[str, ...] = IMAGES
        if len(argv) == 3:
            if argv[2] not in host.IMAGES:
                print(f"no image called {argv[2]}: they're {', '.join(host.IMAGES)}")
                return 2
            which = (host.IMAGES[argv[2]][0],)
        return send(Remote(found[argv[1]]), which)
    print("scripts/bench.py hosts | send NAME [bench|cae]")
    return 2
