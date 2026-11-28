"""A bench job: one command, its files and its limits, a unit any host with the same image can run
(mqucifer/print-shop#364).

A settings study's `load` reads are jobs: each a command over a folder of the files it reads,
run in the bench's image within its own limits (`host.container`), returning its reply and the
files it saved. Nothing in a job names the host: `folder` is where its files are on the host that
made it, and a runner on another machine ships them, runs the command in the same image and
writes back what it saved, into the same folder. Here they run on this host (`run_here`); a
runner for another is ROADMAP M12's job runner (mqucifer/project-print), and nothing here assumes
there's only one.

Standard library only: this runs outside the image.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import host

__all__ = ["HERE", "OFFERED", "READS", "BenchJob", "HostLost", "JobResult", "Met", "Runner",
           "Stopped", "load_files", "reads", "run_here", "running_here", "saved_files",
           "stop_here", "verdict_of"]

#: What a job's record calls this host (`report.how_it_ran`'s `host`): a remote is called by its
#: name in the settings (`remote.RemoteHost`).
HERE = "here"


class Stopped(RuntimeError):
    """The study was stopped before the job started: cut off, never the part's answer."""


class HostLost(RuntimeError):
    """The host a job ran on stopped answering before it gave the job's reply: no answer of the
    job's own, so it may run again on another host (mqucifer/print-shop#373). `kind` is how it
    failed, in words with no name or figure of the job's: the step and Docker's or SSH's part,
    as `run: docker's exit 125` (mqucifer/print-shop#380)."""

    kind: str = ""
    #: Where a call there failed, its exit code and its words alone (`remote.words_only`).
    how: str = ""

#: This host's Docker, which its jobs run by.
DOCKER = ("docker",)

#: The containers this process's jobs are running in, by name: its own, which the host's budget
#: doesn't count twice (`budget.others`), and which `stop_here` stops.
_RUNNING: set[str] = set()
_LOCK = threading.Lock()

#: How a bench command says what it saved, as its reply's last lines do: "saved:
#: bench-out/load-1.txt and .json", the same file by each of its endings.
_SAVED = re.compile(r"^saved: (\S+?)(\.\w+)((?: and \.\w+)*)\s*$", re.MULTILINE)


def saved_files(stdout: str) -> tuple[str, ...]:
    """Each file a command's reply says it saved, relative to its folder."""
    found = []
    for match in _SAVED.finditer(stdout):
        stem, first, more = match.groups()
        found += [stem + first, *(stem + ext for ext in re.findall(r"\.\w+", more))]
    return tuple(found)


#: The setting a job offered a host with more memory is told, in its container, what it met
#: where it ran first (`Met.told`), for its record to say (`report.how_it_ran`,
#: mqucifer/print-shop#380).
OFFERED = "BENCH_OFFERED"


def _gib(count: int | None) -> str:
    return "unknown" if count is None else f"{count / 2**30:.1f} GiB"


@dataclass(frozen=True)
class Met:
    """A read that met its container's memory on the way to an answer that isn't the whole
    read, as its command said as it ended (`host.MET_LINE`), or as the host said of one the
    kernel killed (mqucifer/print-shop#380): its container's limit, the most the container held,
    and, where a check of a step's need against its room said them, the need and the room; each
    in bytes, None where unsaid. `at` is the step it was in, and `host` where it ran."""

    limit: int | None
    peak: int | None
    need: int | None
    room: int | None
    at: str
    killed: bool = False
    host: str = HERE

    @classmethod
    def of(cls, said: list[str], host_name: str = HERE) -> Met | None:
        """The last `host.MET_LINE` of what a command said, or None."""
        lines = [line for line in said if line.startswith(host.MET_LINE + " ")]
        if not lines:
            return None
        first, _, rest = lines[-1][len(host.MET_LINE) + 1 :].partition(" ")
        fields, _, at = rest.partition(" at ")
        found = dict(f.split("=", 1) for f in fields.split() if "=" in f)

        def number(key: str) -> int | None:
            value = found.get(key, "-")
            return int(value) if value.isdigit() else None

        return cls(number("limit"), number("peak"), number("need"), number("room"),
                   at.strip() or "where it last said", first == host.MET_KILLED, host_name)

    def said(self) -> str:
        """What it met, in words: `on here, meshing ...: need 5.2 GiB, room 3.0 GiB of its
        container's 4.0 GiB, at most 4.0 GiB held`."""
        how = ("the kernel killed it, most likely at the memory" if self.killed
               else "need " + _gib(self.need) + ", room " + _gib(self.room)
               if self.need is not None or self.room is not None
               else "the kernel stopped a process of it for memory, by the container's own count")
        return (f"on {self.host}, {self.at}: {how}, of its container's {_gib(self.limit)}, "
                f"at most {_gib(self.peak)} held")

    def told(self) -> str:
        """It as `OFFERED` carries it: `host=here limit=... peak=... need=... room=... killed=0
        at STEP`."""
        counts = {"limit": self.limit, "peak": self.peak, "need": self.need, "room": self.room}
        fields = " ".join(f"{k}={'-' if v is None else v}" for k, v in counts.items())
        return f"host={self.host} {fields} killed={int(self.killed)} at {self.at}"


def reads(folder: Path, saved: tuple[str, ...] | list[str]) -> int:
    """How much a saved reply read (mqucifer/print-shop#380): 2 an answer of the whole, any
    verdict but NO ANSWER; 1 a glass band's lower bound; 0 NO ANSWER, or no reply saved."""
    found = next((f for f in saved if f.endswith(".json")), None)
    if found is None:
        return 0
    try:
        record = json.loads((folder / found).read_text())
    except (OSError, ValueError):
        return 0
    if not isinstance(record, dict) or record.get("verdict") in (None, "NO ANSWER"):
        return 0
    return 1 if "lower_bound" in (record.get("numbers") or {}) else 2


#: What each of `reads`'s counts is, in words.
READS = ("NO ANSWER", "a glass band's lower bound", "the whole")


def verdict_of(folder: Path, saved: tuple[str, ...] | list[str]) -> str:
    """A saved reply's verdict word, or `none saved`."""
    found = next((f for f in saved if f.endswith(".json")), None)
    try:
        record = json.loads((folder / found).read_text()) if found else {}
    except (OSError, ValueError):
        record = {}
    return str(record.get("verdict") or "none saved") if isinstance(record, dict) else "none saved"


def load_files(folder: Path, args: list[str] | tuple[str, ...]) -> list[str]:
    """The files a `load` reads, relative to `folder`: its assembly file and every file it names
    beside it, each part's own, its `with`s, its `band` and its regions' meshes. Those that
    aren't there are left for the job to say (`run_here`, `remote.Remote.run`)."""
    word = host.command_word(list(args))
    words = list(args)
    if word != "load" or words.index(word) + 1 >= len(words):
        raise ValueError(f"not a load of an assembly file: {' '.join(words)}")
    assembly = words[words.index(word) + 1]
    found = [assembly]
    try:
        raw = json.loads((folder / assembly).read_text())
    except (OSError, ValueError):
        return found
    beside = PurePosixPath(assembly).parent
    for part in raw.get("parts") or () if isinstance(raw, dict) else ():
        if not isinstance(part, dict):
            continue
        named = [part.get("file"), *(part.get("with") or ()), part.get("band")]
        printed = part.get("print")
        if isinstance(printed, dict):
            named += [r.get("mesh") for r in printed.get("regions") or () if isinstance(r, dict)]
        for name in named:
            if isinstance(name, str) and name:
                path = (beside / name).as_posix()
                if path not in found:
                    found.append(path)
    return found


@dataclass(frozen=True)
class BenchJob:
    """A bench command over its folder of files, with its limits."""

    #: The command and its words, as `scripts/bench.py` takes them: `load study.json --lift top`.
    args: tuple[str, ...]
    #: Where its files are on the host that made it, and which they are, relative to it: what a
    #: runner on another host ships.
    folder: Path
    files: tuple[str, ...]
    #: Its limits, as its container runs it (`host.memory_for`, `host.timeout_for`): the time
    #: is the command's own, and nothing longer is offered, so a `load` reads in the passes its
    #: words ask for (one, or two with `--parts`), never as its clock decides
    #: (mqucifer/print-shop#331), and what it reads doesn't hang on how busy the host is.
    memory: str = field(default="")
    timeout: int = 0
    #: The longer limit it's offered as it runs, where `timeout` is given, in s, or 0: a design
    #: round's `load` under the run's own limit, offered the hour (`host.offered`).
    longer: int = 0
    #: What it met where it ran first, where it's offered a host with more memory
    #: (mqucifer/print-shop#380): told to its container (`OFFERED`).
    offered: Met | None = None

    @classmethod
    def of(cls, args: list[str] | tuple[str, ...], folder: Path,
           files: list[str] | tuple[str, ...]) -> BenchJob:
        """A job of `args` over `files` in `folder`, with the command's own limits."""
        words = list(args)
        return cls(tuple(words), folder, tuple(files), host.memory_for(words),
                   host.timeout_for(words))

    def told(self) -> dict[str, str]:
        """What its container is told of what it met where it ran first, where it's offered."""
        return {} if self.offered is None else {OFFERED: self.offered.told()}


@dataclass(frozen=True)
class JobResult:
    """What a job's command replied, and the files it saved, relative to its folder."""

    returncode: int
    stdout: str
    stderr: str
    saved: tuple[str, ...] = ()
    #: The host that ran it: `HERE`, or a remote's name.
    host: str = HERE
    #: Where its read met its container's memory on the way to an answer that isn't the whole
    #: read, as it said, or None (mqucifer/print-shop#380).
    met: Met | None = None


#: Runs a job and returns its result, with what it saved in the job's folder.
Runner = Callable[[BenchJob], JobResult]


def run_here(job: BenchJob) -> JobResult:
    """Run a job on this host, in a container over its folder, as the bench runs any command
    (`host.run`) but within its own limit: its files are where they are, and what it saves lands
    beside them."""
    missing = [f for f in job.files if not (job.folder / f).exists()]
    if missing:
        raise FileNotFoundError(f"the job's files aren't in {job.folder}: {', '.join(missing)}")
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    with _LOCK:
        _RUNNING.add(name)
    said: list[str] = []
    try:
        done: subprocess.CompletedProcess[str] = host.run(
            list(job.args), job.folder, timeout=job.timeout or None, name=name,
            more_env={"BENCH_HOST": HERE, **job.told()}, memory=job.memory or None, trace=said,
            docker=DOCKER, longer=job.longer or None)
    finally:
        with _LOCK:
            _RUNNING.discard(name)
    return JobResult(done.returncode, done.stdout, done.stderr, saved_files(done.stdout),
                     met=Met.of(said))


def running_here() -> set[str]:
    """The containers this process's jobs are running in."""
    with _LOCK:
        return set(_RUNNING)


def stop_here() -> None:
    """Stop every container this process's jobs are running in: a study that's interrupted
    doesn't leave its reads running for hours."""
    for name in running_here():
        subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=60)
