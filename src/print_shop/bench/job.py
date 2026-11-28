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

import re
import subprocess
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import host

__all__ = ["BenchJob", "JobResult", "Runner", "run_here", "running_here", "saved_files",
           "stop_here"]

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

    @classmethod
    def of(cls, args: list[str] | tuple[str, ...], folder: Path,
           files: list[str] | tuple[str, ...]) -> BenchJob:
        """A job of `args` over `files` in `folder`, with the command's own limits."""
        words = list(args)
        return cls(tuple(words), folder, tuple(files), host.memory_for(words),
                   host.timeout_for(words))


@dataclass(frozen=True)
class JobResult:
    """What a job's command replied, and the files it saved, relative to its folder."""

    returncode: int
    stdout: str
    stderr: str
    saved: tuple[str, ...] = ()


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
    try:
        done: subprocess.CompletedProcess[str] = host.run(
            list(job.args), job.folder, timeout=job.timeout or None, name=name)
    finally:
        with _LOCK:
            _RUNNING.discard(name)
    return JobResult(done.returncode, done.stdout, done.stderr, saved_files(done.stdout))


def running_here() -> set[str]:
    """The containers this process's jobs are running in."""
    with _LOCK:
        return set(_RUNNING)


def stop_here() -> None:
    """Stop every container this process's jobs are running in: a study that's interrupted
    doesn't leave its reads running for hours."""
    for name in running_here():
        subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=60)
