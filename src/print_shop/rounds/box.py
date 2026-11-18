"""Where model-written code runs: the bench's hardened container, one per command.

`host.container` holds the limits: no network, 2 CPUs, 3 GiB, a read-only image, the invoking
user, no capabilities. `cad_eval` kept one container for a session, which let a process left by a
run act between tool calls, plant a `python` on the sandbox's PATH and forge what came next (its
README, "the race review of #41"). It closed that with a reap after each run, absolute binaries
and fresh containers for the judging. Here every command is its own container, so nothing a run
starts outlives it, and there's nothing to reap or stand in for: `/work` isn't on any PATH, and a
harness command runs with `python -I`.

A script may call the bench's own commands (`/opt/bench`), as a piece map takes dozens of fixes.
Each one it runs writes a line where `python` tells it to, and `Ran.benched` is those, read after:
the harness records each as a bench call (mqucifer/print-shop#161). What's read is the script's
own folder, so it's what the script could have written itself: `rounds/run.py` holds a `FAIL`
from it, and lets no pass from it clear one.

Output is read as it comes and only its end is kept: a script that prints without end costs
neither the Mac's memory nor Docker's disk. The container holds no secret.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.bench.cli import CALLS
from print_shop.bench.report import EXIT
from print_shop.rounds import paths

#: A run's or a bench command's output kept, in bytes: the end of it.
KEEP = 256_000
#: The exit code docker itself gives when it couldn't start the container.
DOCKER_FAILED = 125
#: The bench commands of one script that are recorded, at most, and the most one's saved answer
#: may be, in bytes: a script in a loop mustn't fill the ledger.
BENCHED = 200
BENCHED_BYTES = 4_000_000


class Gone(RuntimeError):
    """Docker didn't answer: the infrastructure's failure, and nothing about the part."""


@dataclass
class Ran:
    code: int
    output: str
    seconds: float
    dropped: int = 0
    timed_out: bool = False
    #: The bench commands a script ran itself, in order: each its `argv`, its exit `code`, and
    #: its saved `record`, or None where that couldn't be read.
    benched: list[dict[str, Any]] = field(default_factory=list)


class Box:
    """Commands in containers over a directory."""

    def __init__(self, work: Path, timeout: int = host.TIMEOUT) -> None:
        self.work = work
        self.timeout = timeout
        self.ran = 0
        #: The images past the first that are known to be here.
        self.built: set[str] = set()

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        """A script, as `run` runs it: it may import what's beside it. Each bench command it
        ran itself is in `benched`."""
        journal = f"record/bench-calls-{uuid.uuid4().hex[:12]}.jsonl"
        ran = self._run(["python", script], where, timeout, env={CALLS: f"/work/{journal}"})
        ran.benched = benched(where or self.work, journal)
        return ran

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        """A bench command, in its own image: the second for `fit-on` and `bend`
        (mqucifer/print-shop#142). Only the bench's own: `build` and `check` aren't in an image."""
        image = host.image_for(args)
        if image != host.IMAGE and image not in self.built:
            if host.image_id(image) is None:
                # Said as a command that couldn't run, not as Docker gone: the run goes on with
                # the first image's commands.
                return Ran(3, f"usage error: {host.missing(image)}, on the machine\n", 0.0)
            self.built.add(image)
        return self._run(host.bench_command(args), where, timeout, image)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        """One of the bench package's modules that isn't a bench command, in its own image: the
        second for `arm` (mqucifer/print-shop#150), and said, not raised, where that isn't
        built."""
        image = host.helper_image(module)
        if image != host.IMAGE and image not in self.built:
            if host.image_id(image) is None:
                return Ran(3, f"usage error: {host.missing(image)}, on the machine\n", 0.0)
            self.built.add(image)
        return self._run(host.helper(module, args), where, timeout, image)

    def _run(
        self,
        command: list[str],
        where: Path | None,
        timeout: int | None,
        image: str = host.IMAGE,
        env: dict[str, str] | None = None,
    ) -> Ran:
        limit = self.timeout if timeout is None else timeout
        name = f"print-rounds-{uuid.uuid4().hex[:12]}"
        self.ran += 1
        began = time.monotonic()
        try:
            argv = host.container(where or self.work, command, name, image=image, env=env)
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise Gone(f"docker couldn't be started: {type(exc).__name__}") from exc
        kept: deque[bytes] = deque()
        size = [0, 0]

        def drain() -> None:
            assert process.stdout is not None
            descriptor = process.stdout.fileno()
            for chunk in iter(lambda: os.read(descriptor, 65536), b""):
                kept.append(chunk)
                size[0] += len(chunk)
                while size[0] - len(kept[0]) >= KEEP:
                    size[0] -= len(kept[0])
                    size[1] += len(kept.popleft())

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        try:
            code = process.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=60)
            try:
                code = process.wait(timeout=60)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                raise Gone(f"a container past its {limit} s limit wouldn't stop") from exc
        reader.join(timeout=30)
        text = b"".join(kept).decode("utf-8", errors="replace")
        seconds = round(time.monotonic() - began, 1)
        if code == DOCKER_FAILED and not timed_out:
            raise Gone(f"docker couldn't run the container: {text[-400:]}")
        if timed_out:
            text += f"\nverdict: NO ANSWER: it ran past the {limit} s limit and was stopped\n"
            code = 2 if command[1:2] == ["-I"] else 124
        elif code == host.KILLED:
            text += (
                f"\nverdict: NO ANSWER: it was killed, most likely at the {host.MEMORY} "
                "memory limit\n"
            )
            code = 2 if command[1:2] == ["-I"] else host.KILLED
        return Ran(code, text, seconds, size[1], timed_out)


def benched(directory: Path, journal: str) -> list[dict[str, Any]]:
    """The bench commands a script ran, from the list its container kept, which is then taken
    away. Each line names an answer's saved file under the folder, read through the walk and
    never through a link. A line or a file that can't be read is a command with no answer."""
    text = _small(directory, journal)
    kept = paths.inside(directory, journal)
    if kept is not None:
        kept.unlink(missing_ok=True)
    if text is None:
        return []
    lines = text.splitlines()
    found: list[dict[str, Any]] = []
    for line in lines[:BENCHED]:
        try:
            said = json.loads(line)
            argv = [str(word) for word in said["argv"]]
            saved = str(said["saved"])
        except (ValueError, KeyError, TypeError):
            continue
        record: dict[str, Any] | None = None
        if saved.startswith("/work/"):
            try:
                read = json.loads(_small(directory, saved[len("/work/") :]) or "")
                record = read if isinstance(read, dict) else None
            except ValueError:
                record = None
        verdict = record.get("verdict") if record else None
        seconds = record.get("seconds") if record else None
        found.append(
            {
                "argv": argv,
                "code": EXIT.get(str(verdict), 2),
                "record": record,
                "seconds": float(seconds) if isinstance(seconds, int | float) else 0.0,
            }
        )
    if len(lines) > BENCHED:
        found.append({"argv": [], "code": 3, "record": None, "seconds": 0.0, "more": len(lines)})
    return found


def _small(directory: Path, path: str) -> str | None:
    """A file's text, where it's inside the folder, a plain file, and no bigger than an answer
    is: None otherwise."""
    try:
        found = paths.inside(directory, path)
        if found is None or not found.is_file() or found.stat().st_size > BENCHED_BYTES:
            return None
        return paths.read_inside(directory, path)
    except (OSError, ValueError):
        return None
