"""Where model-written code runs: the bench's hardened container, one per command.

`host.container` holds the limits: no network, 2 CPUs, 3 GiB, a read-only image, the invoking
user, no capabilities. `cad_eval` kept one container for a session, which let a process left by a
run act between tool calls, plant a `python` on the sandbox's PATH and forge what came next (its
README, "the race review of #41"). It closed that with a reap after each run, absolute binaries
and fresh containers for the judging. Here every command is its own container, so nothing a run
starts outlives it, and there's nothing to reap or stand in for: `/work` isn't on any PATH, and a
harness command runs with `python -I`.

Output is read as it comes and only its end is kept: a script that prints without end costs
neither the Mac's memory nor Docker's disk. The container holds no secret.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from print_shop.bench import host

#: A run's or a bench command's output kept, in bytes: the end of it.
KEEP = 256_000
#: The exit code docker itself gives when it couldn't start the container.
DOCKER_FAILED = 125


class Gone(RuntimeError):
    """Docker didn't answer: the infrastructure's failure, and nothing about the part."""


@dataclass
class Ran:
    code: int
    output: str
    seconds: float
    dropped: int = 0
    timed_out: bool = False


class Box:
    """Commands in containers over a directory."""

    def __init__(self, work: Path, timeout: int = host.TIMEOUT) -> None:
        self.work = work
        self.timeout = timeout
        self.ran = 0

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        """A script, as `run` runs it: it may import what's beside it."""
        return self._run(["python", script], where, timeout)

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        """A bench command. Only the bench's own: `build` and `check` aren't in the image."""
        return self._run(host.bench_command(args), where, timeout)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        """One of the bench package's modules that isn't a bench command."""
        return self._run(host.helper(module, args), where, timeout)

    def _run(self, command: list[str], where: Path | None, timeout: int | None) -> Ran:
        limit = self.timeout if timeout is None else timeout
        name = f"print-rounds-{uuid.uuid4().hex[:12]}"
        self.ran += 1
        began = time.monotonic()
        try:
            argv = host.container(where or self.work, command, name)
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
            code = 2 if command[:2] == ["python", "-I"] else 124
        elif code == host.KILLED:
            text += (
                f"\nverdict: NO ANSWER: it was killed, most likely at the {host.MEMORY} "
                "memory limit\n"
            )
            code = 2 if command[:2] == ["python", "-I"] else host.KILLED
        return Ran(code, text, seconds, size[1], timed_out)
