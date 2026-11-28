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
neither the Mac's memory nor Docker's disk. The container holds no secret. So a helper whose answer
can be long writes it to a file in the folder it runs in, and its line says where (`answered`,
`bench/answers.py`): on a traced suncatcher, the `mesh` answer was one line of 1,459,288 bytes, its
mark dropped with its start, and each file read "can't tell" though the bench had meshed every one
(mqucifer/print-shop#181).

Each container is told its limit (`BENCH_TIMEOUT`), as `host.run` tells it. A `load` that reads in
two passes runs under an hour, as from `host.run` (mqucifer/print-shop#325): with `--parts` from
the start; otherwise it's offered the hour, and the harness waits for it only once its output
says it took it up, as two passes start (`host.TAKEN`): where the whole meets the container's
memory, or where an assembly's one pass wouldn't end inside the run's own limit
(mqucifer/print-shop#331). So a design round's `load` of a large assembly may take up to an hour,
and every other command, and a script, keeps the run's own `command_seconds`. A one pass stopped
at that limit, of an assembly two passes could read (`host.ABLE`), is told to ask for them with
`--parts` (`host.past`). And a command killed for memory says the step it was in and the most the
container had held, from what `load` says as it goes, as from `host.run` (`host.killed`,
mqucifer/print-shop#336). A `load --parts` runs in the larger memory `host.memory_for` gives it,
as from `host.run`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench import answers, host
from print_shop.bench.cli import CALLS
from print_shop.bench.report import EXIT
from print_shop.rounds import paths

#: A run's or a bench command's output kept, in bytes: the end of it.
KEEP = 256_000
#: The line, on a line of its own, that says a command took up the longer limit it was
#: offered; the one that says two passes could read it (mqucifer/print-shop#331); and how often
#: its container is looked at, in s.
TAKEN_LINE = f"\n{host.TAKEN}\n".encode()
ABLE_LINE = f"\n{host.ABLE}\n".encode()
POLL = 0.5
#: The exit code docker itself gives when it couldn't start the container.
DOCKER_FAILED = 125
#: The bench commands of one script that are recorded, at most, and the most one's saved answer
#: may be, in bytes: a script in a loop mustn't fill the ledger.
BENCHED = 200
BENCHED_BYTES = 4_000_000


#: What `load` says as it goes (`host.STEP_LINE`, `host.MEMORY_LINE`): set aside as it's read,
#: never counted against `KEEP`, and the longest such line held back to be told, in bytes.
PROGRESS = (f"{host.STEP_LINE} ".encode(), f"{host.MEMORY_LINE} ".encode())
PROGRESS_LONGEST = 4096


class _Progress:
    """What a command's output says as it goes, taken out of it as it's read
    (mqucifer/print-shop#336): a long `--parts` read says about 400 steps and its memory every
    5 s, some 210 KB, and counted against `KEEP` it would push the reply's start out, cut mid
    line. Each line that starts as one of `PROGRESS` is held until it ends and set aside, the
    last step and the last memory kept to say where a killed command was; every other line is
    passed on as it comes, a line of any length with it."""

    def __init__(self) -> None:
        self.line = b""
        #: Whether the line being read is passed on as it comes: it isn't one of `PROGRESS`.
        self.through = False
        self.last: dict[bytes, str] = {}

    def take(self, data: bytes) -> bytes:
        out = bytearray()
        while data:
            cut = data.find(b"\n")
            piece, data = (data, b"") if cut < 0 else (data[: cut + 1], data[cut + 1 :])
            ended = piece.endswith(b"\n")
            if self.through:
                out += piece
            else:
                line = self.line + piece
                self.line = b""
                if ended:
                    out += self._told(line)
                elif len(line) <= PROGRESS_LONGEST and any(
                    p.startswith(line) or line.startswith(p) for p in PROGRESS
                ):
                    self.line = line
                else:
                    out += line
                    self.through = True
            if ended:
                self.through = False
        return bytes(out)

    def _told(self, line: bytes) -> bytes:
        kind = next((p for p in PROGRESS if line.startswith(p)), None)
        if kind is None:
            return line
        self.last[kind] = line.rstrip(b"\n").decode("utf-8", errors="replace")
        return b""

    def end(self) -> bytes:
        """What's left at the output's end, a line with no end of its own."""
        line, self.line = self.line, b""
        return self._told(line)

    def said(self) -> list[str]:
        return [self.last[p] for p in PROGRESS if p in self.last]


class Gone(RuntimeError):
    """Docker didn't answer: the infrastructure's failure, and nothing about the part."""


class Unanswered(RuntimeError):
    """A helper gave no answer to read, and why, in words a run's records keep: its answer went
    missing on the way, or the bench exited with an error."""


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
        #: A bench command as the run has it run, from its words and the folder it runs over:
        #: a part built flat is placed where it's read against a given part
        #: (`placing.py`, mqucifer/print-shop#201). None runs each as it's given.
        self.placing: Callable[[list[str], Path], list[str]] | None = None

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
        if self.placing is not None:
            args = self.placing(list(args), where or self.work)
        image = host.image_for(args)
        if image != host.IMAGE and image not in self.built:
            if host.image_id(image) is None:
                # Said as a command that couldn't run, not as Docker gone: the run goes on with
                # the first image's commands.
                return Ran(3, f"usage error: {host.missing(image)}, on the machine\n", 0.0)
            self.built.add(image)
        longer = None
        if timeout is None:
            # By its folder, as `host.run` gives it: a read as printed of an assembly with a
            # `through` joint is given the hour from the start (mqucifer/print-shop#363).
            folder = where or self.work
            given = host.timeout_for(args, folder)
            timeout = given if given >= host.TWO_PASSES else None
            longer = host.offered(args, folder)
        return self._run(
            host.bench_command(args), where, timeout, image, longer=longer,
            memory=host.memory_for(args),
        )  # fmt: skip

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
        longer: int | None = None,
        memory: str | None = None,
    ) -> Ran:
        """`command` in a container, waited for `timeout`, or the run's own limit; or `longer`
        from when its output says it took that up (`host.TAKEN`)."""
        limit = self.timeout if timeout is None else timeout
        name = f"print-rounds-{uuid.uuid4().hex[:12]}"
        self.ran += 1
        began = time.monotonic()
        told = {**(env or {}), "BENCH_TIMEOUT": str(limit)}
        if longer is not None:
            told[host.OFFER] = str(longer)
        try:
            argv = host.container(
                where or self.work, command, name, image=image, env=told, memory=memory
            )
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise Gone(f"docker couldn't be started: {type(exc).__name__}") from exc
        kept: deque[bytes] = deque()
        size = [0, 0]
        taken = threading.Event()
        able = threading.Event()
        lines_seen = ((TAKEN_LINE, taken), (ABLE_LINE, able))
        reach = max(len(one) for one, _ in lines_seen)

        progress = _Progress()

        def keep(chunk: bytes) -> None:
            if not chunk:
                return
            kept.append(chunk)
            size[0] += len(chunk)
            while size[0] - len(kept[0]) >= KEEP:
                size[0] -= len(kept[0])
                size[1] += len(kept.popleft())

        def drain() -> None:
            assert process.stdout is not None
            descriptor = process.stdout.fileno()
            carry = b"\n"
            for chunk in iter(lambda: os.read(descriptor, 65536), b""):
                if longer is not None:
                    window = carry + chunk
                    for one, seen in lines_seen:
                        if one in window:
                            seen.set()
                    carry = window[-reach:]
                keep(progress.take(chunk))
            keep(progress.end())

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        could = False
        code: int | None = None
        while True:
            if longer is not None and taken.is_set():
                limit = longer
            left = limit - (time.monotonic() - began)
            if left <= 0:
                timed_out = True
                # Read as it's stopped, as `host._waited` does: a one pass under its own limit.
                could = able.is_set() and limit != longer
                break
            try:
                code = process.wait(timeout=min(left, POLL))
                break
            except subprocess.TimeoutExpired:
                continue
        if timed_out:
            subprocess.run(["docker", "kill", name], capture_output=True, check=False, timeout=60)
            try:
                code = process.wait(timeout=60)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                raise Gone(f"a container past its {limit} s limit wouldn't stop") from exc
        reader.join(timeout=30)
        assert code is not None, "waited for to its end, or stopped and waited for"
        text = b"".join(kept).decode("utf-8", errors="replace")
        if longer is not None:
            # The lines that took the longer limit up, or said two passes could, are the
            # harness's, not the answer's.
            lines = text.splitlines(keepends=True)
            text = "".join(one for one in lines if one.rstrip("\n") not in (host.TAKEN, host.ABLE))
        # What `load` says as it goes, its steps and the container's memory, is the harness's
        # too, set aside as it was read: kept only to say where it was, if it's killed
        # (mqucifer/print-shop#336).
        said = progress.said()
        seconds = round(time.monotonic() - began, 1)
        if code == DOCKER_FAILED and not timed_out:
            raise Gone(f"docker couldn't run the container: {text[-400:]}")
        if timed_out:
            text += "\n" + host.past(limit, could, said)
            code = 2 if command[1:2] == ["-I"] else 124
        elif code == host.KILLED:
            text += "\n" + host.killed(said, memory)
            code = 2 if command[1:2] == ["-I"] else host.KILLED
        return Ran(code, text, seconds, size[1], timed_out)


def answered(ran: Ran, mark: str, where: Path) -> dict[str, Any]:
    """A helper's answer: its line, `mark` then JSON, or the file that line names in `where`. A
    reply too long to keep is never taken for the bench failing: it's said as the answer gone
    missing, apart from an exit that isn't 0."""
    at = ran.output.find(mark)
    if at < 0:
        if ran.code != 0:
            last = ran.output.strip().splitlines()[-1:] or [""]
            raise Unanswered(f"the bench exited {ran.code}: {last[0][:300]}")
        if ran.dropped:
            raise Unanswered(
                f"the bench's answer went missing: its output was "
                f"{ran.dropped + len(ran.output.encode('utf-8')):,} bytes, of which the harness "
                f"keeps the last {KEEP:,} (exit 0)"
            )
        raise Unanswered("the bench's answer went missing: its output has no answer (exit 0)")
    line = ran.output[at + len(mark) :].split("\n", 1)[0]
    try:
        said = json.loads(line)
    except ValueError as exc:
        raise Unanswered(f"the bench's answer went missing: its line isn't JSON ({exc})") from exc
    if not isinstance(said, dict):
        raise Unanswered("the bench's answer went missing: its line isn't a JSON object")
    if set(said) != {answers.FILE, "bytes"}:
        return said
    name = str(said[answers.FILE])
    try:
        found = json.loads(paths.read_inside(where, name))
    except (OSError, ValueError) as exc:
        raise Unanswered(
            f"the bench's answer went missing: it wrote {name}, and that couldn't be read "
            f"({type(exc).__name__})"
        ) from exc
    if not isinstance(found, dict):
        raise Unanswered(f"the bench's answer went missing: {name} isn't a JSON object")
    return found


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
