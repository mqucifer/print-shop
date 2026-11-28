"""Bench jobs on another host's Docker, over SSH (mqucifer/print-shop#373).

The Sponsor, 2026-11-27: "I just want to ensure we're actually maximizing our available
processing across many machines with different resources." A bench job (`job.BenchJob`) is its
command over a folder of its files, within its own limits, so any host with the same image can
run it. This runs one on a host named in the Mac's settings:

    BENCH_HOSTS=spark
    BENCH_HOST_SPARK_SSH=spark           # the SSH target, as ~/.ssh/config names it
    BENCH_HOST_SPARK_CORES=5-9,15-19     # the cores its jobs are pinned to (--cpuset-cpus)
    BENCH_HOST_SPARK_MEMORY=15g          # the memory its jobs may hold at once
    BENCH_HOST_SPARK_FLOOR=10g           # the MemFree never to go under

Every one of them is stated: nothing is guessed, and a host with one missing is refused.

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

import io
import os
import re
import subprocess
import tarfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import host
from .budget import megabytes
from .job import HERE, BenchJob, HostLost, JobResult, saved_files

__all__ = ["HOSTS", "NoRoom", "Remote", "RemoteHost", "cores_of", "cpuset", "send", "stated"]

#: The setting that names the remote hosts, and each one's own keys after
#: `BENCH_HOST_<NAME>_`.
HOSTS = "BENCH_HOSTS"
KEYS = ("SSH", "CORES", "MEMORY", "FLOOR")
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

    def docker(self) -> tuple[str, ...]:
        """Its Docker, over SSH."""
        return ("docker", "-H", f"ssh://{self.ssh}")

    def said(self) -> str:
        return (f"{self.name} ({self.ssh}): cores {cpuset(self.cores)}, "
                f"{self.memory_mb / 1024:.1f} GiB for jobs, MemFree kept over "
                f"{self.floor_mb / 1024:.1f} GiB")


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
        found.append(RemoteHost(name, target, cores, memory, floor))
    if len({h.name for h in found}) != len(found):
        raise ValueError(f"{HOSTS} names a host twice")
    return found


class NoRoom(RuntimeError):
    """A host's free memory, read now, holds no more of this job: its message says why."""


def _user() -> tuple[int, int]:
    return os.getuid(), os.getgid()


def _meminfo(text: str) -> dict[str, int]:
    """`/proc/meminfo`'s figures, in MiB."""
    return {k: int(v) // 1024 for k, v in re.findall(r"^(\w+):\s+(\d+) kB", text, re.MULTILINE)}


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

        folder("work", (uid, gid))
        made = {"work"}
        for name in job.files:
            relative = PurePosixPath(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"the job's file {name!r} isn't inside its folder")
            for parent in reversed(relative.parents[:-1]):
                if f"work/{parent}" not in made:
                    folder(f"work/{parent}", (uid, gid))
                    made.add(f"work/{parent}")
            put(job.folder / name, f"work/{relative}", (uid, gid))
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
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(stream.read())
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
        self._lock = threading.Lock()
        #: The containers this process has reserved or is running there, by name: each one's
        #: memory limit in MiB.
        self._held: dict[str, int] = {}
        #: Volumes it couldn't remove, where the host stopped answering: said, never left
        #: unsaid.
        self.left: list[str] = []

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
            raise HostLost(f"{self.name}: docker {' '.join(args[:2])} failed: "
                           f"{said or 'no reply'}")
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
        if self.image_id(host.IMAGE) is None:
            return None
        done = self._call("run", "--rm", "--name", self._new(), *HELPER, host.IMAGE,
                          "df", "-B1", "/")
        return int(done.stdout.decode().splitlines()[-1].split()[3]) / host.GB

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
        uid, gid = _user()
        done = self._call("run", "--rm", "--name", self._new(), *HELPER, "--user",
                          f"{uid}:{gid}", host.IMAGE, "cat", "/proc/meminfo")
        found = _meminfo(done.stdout.decode(errors="replace"))
        if "MemFree" not in found:
            raise HostLost(f"{self.name}'s /proc/meminfo didn't say MemFree")
        return found["MemFree"]

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
            free = self.memory_free_mb()
            pending = self._still_to_take(self._held)
            need = self.host.floor_mb + memory_mb + pending
            if free < need:
                raise NoRoom(f"{self.name}'s MemFree is {free / 1024:.1f} GiB, under its floor "
                             f"of {self.host.floor_mb / 1024:.1f} GiB plus the job's "
                             f"{memory_mb / 1024:.1f} GiB and {pending / 1024:.1f} GiB this "
                             "study's other jobs there may take yet")
            name = self._new()
            self._held[name] = memory_mb
            return name

    def release(self, name: str) -> None:
        with self._lock:
            self._held.pop(name, None)

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
            HostLost: If the host stopped answering before it gave the job's reply.
        """
        missing = [f for f in job.files if not (job.folder / f).exists()]
        if missing:
            raise FileNotFoundError(f"the job's files aren't in {job.folder}: "
                                    f"{', '.join(missing)}")
        name = name or self._new()
        volumes = (f"{name}-work", f"{name}-code")
        with self._lock:
            self._held.setdefault(name, megabytes(job.memory or host.MEMORY))
        try:
            for volume in volumes:
                self._call("volume", "create", volume)
            self._call("run", "--rm", "-i", "--name", f"{name}-in", *HELPER, "--user", "0:0",
                       "--cap-add", "CHOWN", "--cap-add", "FOWNER",
                       "-v", f"{volumes[0]}:/mnt/work", "-v", f"{volumes[1]}:/mnt/code",
                       host.IMAGE, "tar", "-x", "--numeric-owner", "-C", "/mnt", "-f", "-",
                       data=bundle(job), timeout=HELPER_TIMEOUT)
            done = host.run(list(job.args), job.folder, timeout=job.timeout or None, name=name,
                            docker=self.docker, mounts=volumes, cpuset=cpuset(cores),
                            more_env={"BENCH_HOST": self.name})
            saved = saved_files(done.stdout)
            if (not saved or done.returncode not in OWN_CODES) and not self.answers():
                raise HostLost(f"{self.name} stopped answering while it ran "
                               f"`{' '.join(job.args)}`")
            if saved:
                uid, gid = _user()
                out = self._call("run", "--rm", "--name", f"{name}-out", *HELPER, "--user",
                                 f"{uid}:{gid}", "-v", f"{volumes[0]}:/work:ro", host.IMAGE,
                                 "tar", "-c", "-C", "/work", "-f", "-", "--", *saved,
                                 timeout=HELPER_TIMEOUT)
                unpack(out.stdout, job.folder, saved)
            return JobResult(done.returncode, done.stdout, done.stderr, saved, self.name)
        finally:
            self._clear(name, volumes)

    def _clear(self, name: str, volumes: tuple[str, str]) -> None:
        """Remove a job's container, where it's still there, and its volumes: its own, by name."""
        try:
            self._call("rm", "-f", name, f"{name}-in", f"{name}-out", check=False, timeout=60)
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
        doesn't leave its reads running. Each job's own end removes its volumes."""
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
