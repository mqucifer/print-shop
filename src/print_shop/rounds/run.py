"""One run, from a request to a named ending: intake, rounds, and the stops for the Sponsor.

`docs/rounds.md` says what a run is, and what each file in its folder holds. A run lives in
`~/print-shop/design-runs/<name>/`, off this public repository. Model-written code touches only its
`work/`. The records sit beside that, where no container can reach them.

Everything the model says is data here. It stops for the Sponsor at `questions` and `ask`, and
`blocked` ends it. The process exits on each of those: a person writes the answer into a file, and
`resume` goes on from the very message it stopped at.
"""

from __future__ import annotations

import base64
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.bench import usage as usage_module
from print_shop.rounds import judge, needs, paths, prompt, skills, transcript
from print_shop.rounds.box import Box, Gone, Ran
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.needs import Need
from print_shop.rounds.send import ALIAS
from print_shop.rounds.session import Ending, Judged, Limits, Session
from print_shop.rounds.tools import WorkTools, recorded

DEFAULT_ROOT = Path("~/print-shop/design-runs").expanduser()
#: Stops, which wait for a person and aren't endings.
WAITING = ("questions", "ask")
MET = "met"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
VARIANT = re.compile(r"[A-Za-z0-9_-]{1,24}")
#: A failing need's bench text shown in the reply.
FAILURE_CHARS = 4000
#: The default views on an `ask`'s sheet, and each picture's side.
ASK_VIEWS = ("iso", "top", "front")
ASK_SIZE = "512"


class Refused(RuntimeError):
    """A start or a resume that can't go ahead, said in plain words. Nothing has changed."""


@dataclass
class State:
    """What a run has to remember across its stops, beside the conversation in its events."""

    name: str
    request_sha: str
    stage: str = "intake"
    round: int = 0
    needs_version: int = 0
    needs_open: bool = True
    needs: list[dict[str, Any]] = field(default_factory=list)
    picks: dict[str, dict[str, Any]] = field(default_factory=dict)
    asks: int = 0
    questions: int = 0
    stop: dict[str, Any] | None = None
    ending: dict[str, Any] | None = None
    restarts: int = 0
    unchanged: int = 0
    last_files: dict[str, str] = field(default_factory=dict)
    last_readings: dict[str, dict[str, Any]] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)
    harness: list[str] = field(default_factory=list)


def repository_version() -> tuple[str, bool]:
    """This checkout's commit, and whether it has uncommitted changes to tracked files."""
    where = str(host.REPOSITORY)
    try:
        commit = subprocess.run(
            ["git", "-C", where, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "-C", where, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    return commit, bool(changed)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


class Run:
    """One run's folder, its state, and the tools that belong to it."""

    def __init__(
        self,
        folder: Path,
        send: Callable[[dict[str, Any]], dict[str, Any]],
        limits: Limits | None = None,
        alias: str = ALIAS,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.folder = folder
        self.work = folder / "work"
        self.limits = limits or Limits()
        self.state: State
        self.ledger = DesignLedger(folder.parent / "ledger.sqlite")
        self.box = Box(self.work, self.limits.command_seconds)
        self.tools = WorkTools(self.work, self.box, self, int(self.limits.work_gb * 10**9))
        self.session = Session(
            tools=self.tools,
            send=send,
            alias=alias,
            events=folder / "events.jsonl",
            limits=self.limits,
            stop_file=folder / "stop",
        )
        if sleep is not None:
            self.session.sleep = sleep

    # Starting and going on

    @classmethod
    def start(
        cls,
        root: Path,
        name: str,
        request: str,
        references: list[Path],
        send: Callable[[dict[str, Any]], dict[str, Any]],
        limits: Limits | None = None,
        alias: str = ALIAS,
        sleep: Callable[[float], None] | None = None,
    ) -> Run:
        """A new run's folder, made once, before any call."""
        if not NAME.fullmatch(name):
            raise Refused(f"a run's name is letters, digits, `.`, `_` and `-`: not {name!r}")
        resolved = root.expanduser().resolve()
        if resolved.is_relative_to(host.REPOSITORY.resolve()):
            raise Refused(f"{resolved} is inside this repository, which is public")
        if not request.strip():
            raise Refused("the request is empty")
        for reference in references:
            if not reference.is_file():
                raise Refused(f"there's no reference file {reference}")
        if len({r.name for r in references}) != len(references):
            raise Refused("two reference files have the same name")
        folder = resolved / name
        if folder.exists():
            raise Refused(f"{folder} exists: `resume` goes on with it")
        folder.mkdir(parents=True)
        (folder / "work" / "references").mkdir(parents=True)
        (folder / "work" / "record").mkdir()
        (folder / "request.md").write_text(request, encoding="utf-8")
        for reference in references:
            shutil.copyfile(reference, folder / "work" / "references" / reference.name)
        run = cls(folder, send, limits, alias, sleep)
        commit, dirty = repository_version()
        run.state = State(name, _sha(request), harness=[f"{commit}{'+changes' if dirty else ''}"])
        run.ledger.run_started(
            name,
            request_sha=run.state.request_sha,
            events=str(run.session.events),
            harness_commit=run.state.harness[0],
            skills_sha=skills.sha(),
            image=host.IMAGE,
            image_id=host.image_id(),
            alias=alias,
            limits=run.limits.recorded(),
        )
        run.save()
        run.session.begin(
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": run._start_text()},
        )
        return run

    @classmethod
    def load(
        cls,
        folder: Path,
        send: Callable[[dict[str, Any]], dict[str, Any]],
        limits: Limits | None = None,
        alias: str = ALIAS,
        sleep: Callable[[float], None] | None = None,
    ) -> Run:
        if not (folder / "state.json").is_file():
            raise Refused(f"{folder} isn't a run")
        run = cls(folder, send, limits, alias, sleep)
        run.state = State(**json.loads((folder / "state.json").read_text(encoding="utf-8")))
        run.session.restore()
        return run

    def resume(self, note: str = "", reopen_needs: bool = False) -> None:
        """Take in what a person wrote, ready to go on. Refused until it's there."""
        if self.state.ending and self.state.ending["name"] == MET:
            raise Refused("this run met its needs and ended")
        stop = self.state.stop
        if stop and stop["kind"] == "questions":
            self._take_answers(int(stop["n"]))
        elif stop and stop["kind"] == "ask":
            self._take_pick(int(stop["n"]))
        if reopen_needs:
            self.state.needs_open = True
            self.session.say(
                "The needs may be settled again: send `needs` with the whole list if they should "
                "change."
            )
        if note.strip():
            self.session.say(f"A note from the people running this:\n\n{note.strip()}")
        commit, dirty = repository_version()
        self.state.harness.append(f"{commit}{'+changes' if dirty else ''}")
        self.state.stop = None
        self.state.ending = None
        self.save()

    def _take_answers(self, n: int) -> None:
        path = self.folder / "questions" / f"{n}.answers.md"
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise Refused(f"the answers aren't written yet: {path}")
        answers = path.read_text(encoding="utf-8").strip()
        said = f"The Sponsor's answers:\n\n{answers}"
        self.state.said.append(said)
        self.ledger.answered(self.state.name, n, answers)
        self.session.say(said)

    def _take_pick(self, n: int) -> None:
        folder = self.folder / "asks" / str(n)
        path = folder / "pick.json"
        if not path.is_file():
            raise Refused(f'the pick isn\'t written yet: {path}, {{"pick": ..., "reason": ...}}')
        asked = json.loads((folder / "asked.json").read_text(encoding="utf-8"))
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Refused(f"{path} isn't JSON: {exc}") from exc
        pick, reason = str(found.get("pick") or ""), str(found.get("reason") or "").strip()
        names = [v["name"] for v in asked["variants"]]
        if pick not in [*names, "none"]:
            raise Refused(f"{path}: the pick is one of {', '.join(names)}, or none: not {pick!r}")
        if not reason:
            raise Refused(f"{path}: a pick needs its reason, which is what the ledger learns from")
        self.ledger.picked(self.state.name, n, pick, reason)
        need = asked.get("need")
        if pick != "none" and need:
            self.state.picks[need] = {"pick": pick, "reason": reason, "ask": n}
        if pick == "none":
            said = f"The Sponsor didn't pick any of them. Their reason: {reason}"
        else:
            said = f"The Sponsor picked {pick}. Their reason: {reason}"
        self.state.said.append(f'On your question "{asked["question"]}": {said}')
        self.session.say(said)

    def drive(self) -> Ending:
        """Calls until a stop or an ending, every failure a named one."""
        try:
            ending = self._disk_floor()
            if ending is None:
                ending = self.session.go()
                while ending.name == "context_full" and self.state.restarts < self.limits.restarts:
                    self.state.restarts += 1
                    self._from_the_record()
                    ending = self.session.go()
        except KeyboardInterrupt:
            ending = self.session._ended(Ending("stopped", {"by": "signal"}))
        except Gone as gone:
            ending = self.session._ended(Ending("container_failed", {"error": str(gone)[:400]}))
        except Exception as exc:  # noqa: BLE001 - whatever it is, it ends named, with its trace kept
            (self.folder / "harness-fault.txt").write_text(traceback.format_exc(), encoding="utf-8")
            said = {"error": type(exc).__name__, "trace": "harness-fault.txt"}
            ending = self.session._ended(Ending("harness_fault", said))
        return self._close(ending)

    def _close(self, ending: Ending) -> Ending:
        if ending.name not in WAITING:
            self.state.ending = asdict(ending)
        self.save()
        self.ledger.run_ended(
            self.state.name,
            None if ending.name in WAITING else ending.name,
            ending.said,
            calls=self.session.calls,
            usage=dict(self.session.usage),
            outcomes=dict(self.session.outcomes),
        )
        return ending

    def _disk_floor(self) -> Ending | None:
        """Docker's disk is shared, and it filled on 2026-11-01."""
        ending = self._disk_floor_ending()
        return self.session._ended(ending) if ending is not None else None

    def _from_the_record(self) -> None:
        """A conversation that wouldn't fit even compacted: a new one, from the record."""
        state = f"This is round {self.state.round}." if self.state.round else "This is intake."
        needs_json = json.dumps([n.plain() for n in self._needs()], indent=1)
        text = prompt.again(self._start_text(), self.state.said, needs_json, state)
        self.session.begin(
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": text},
        )

    def _start_text(self) -> str:
        given = sorted(
            (p.name, p.stat().st_size)
            for p in (self.work / "references").iterdir()
            if p.is_file() and not p.is_symlink()
        )
        request = (self.folder / "request.md").read_text(encoding="utf-8")
        return prompt.start(request, given, skills.start_line())

    def save(self) -> None:
        _write_atomic(self.folder / "state.json", json.dumps(asdict(self.state), indent=1))

    def _needs(self) -> list[Need]:
        return needs.load(self.state.needs)

    # The tools that belong to the run

    def bench_said(self, argv: list[str], ran: Ran, record: dict[str, Any] | None) -> None:
        self.ledger.bench_call(
            self.state.name,
            self.state.round,
            self.session.requests,
            argv,
            ran.code,
            record,
            paths.snapshot(self.work),
            ran.seconds,
        )

    def questions(self, args: dict[str, Any]) -> Judged:
        if not self.state.needs_open:
            reply = (
                "The needs are settled, so this doesn't stop for the Sponsor. Form goes through "
                "`ask`, and the rest is yours to decide. If it can't be done, `blocked` says why."
            )
            return Judged("questions", "failed", reply, "needs_settled")
        text = args["questions"].strip()
        if not text:
            return Judged("questions", "failed", "`questions` is empty.", "empty")
        self.state.questions += 1
        n = self.state.questions
        folder = self.folder / "questions"
        folder.mkdir(exist_ok=True)
        (folder / f"{n}.md").write_text(text + "\n", encoding="utf-8")
        self.ledger.question(self.state.name, n, text, self.session.requests)
        self.state.stop = {"kind": "questions", "n": n}
        self.state.said.append(f"You asked the Sponsor:\n\n{text}")
        self.save()
        reply = "Your questions have gone to the Sponsor. Their answers come in your next message."
        return Judged("questions", "ok", reply, ending=Ending("questions", {"n": n}))

    def needs(self, args: dict[str, Any]) -> Judged:
        if not self.state.needs_open:
            reply = (
                "The needs are settled: they're what your work is checked against, so they don't "
                "change from here. If one can't be met or its check is wrong, `blocked` says so."
            )
            return Judged("needs", "failed", reply, "needs_settled")
        request = (self.folder / "request.md").read_text(encoding="utf-8")
        found, wrong = needs.read(args["needs"], "\n".join([request, *self.state.said]))
        if not wrong:
            wrong = self._usable(found)
        if wrong:
            said = "\n".join(f"- {w}" for w in wrong)
            return Judged("needs", "failed", f"The needs weren't taken:\n{said}", "misses")
        if args.get("try") is True:
            return self._try_needs(found)
        self.state.needs = needs.dump(found)
        self.state.needs_open = False
        self.state.needs_version += 1
        version = self.state.needs_version
        text = json.dumps([n.plain() for n in found], indent=1) + "\n"
        (self.folder / "needs").mkdir(exist_ok=True)
        (self.folder / "needs" / f"{version}.json").write_text(text, encoding="utf-8")
        _write_atomic(self.folder / "needs.json", text)
        paths.write_inside(self.work, "record/needs.json", text)
        self.ledger.needs_accepted(self.state.name, version, found, self.session.requests)
        if self.state.stage == "intake":
            self.state.stage = "rounds"
            self._start_round()
        self.save()
        return Judged("needs", "ok", f"The needs are settled. Round {self.state.round} starts now.")

    def _try_needs(self, found: list[Need]) -> Judged:
        """Each bench check against the files as they stand, settling nothing. The first real run
        guessed a number's name, since a check that's wrong once settled would stop the run for a
        person. It runs on a copy, with no clean run first, so it says only that a check works."""
        scratch = self.folder / f".try-{uuid.uuid4().hex[:12]}"
        lines = ["Nothing is settled. Each check, against your files as they stand:"]
        try:
            paths.copy_plain(self.work, scratch, skip_made=True)
            for need in found:
                if need.by == "eye":
                    lines.append(f"- {need.id}: for the Sponsor's eye, so there's nothing to read.")
                    continue
                reading = judge.read_need(self.box, need, scratch, scratch / ".kept")
                mark = "would pass" if reading.passed else "WOULDN'T PASS"
                lines.append(f"- {need.id}, {mark}: {reading.why}. Limit: {need.limit()}.")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return Judged("needs", "ok", "\n".join(lines), detail={"tried": True})

    def _usable(self, found: list[Need]) -> list[str]:
        """Whether each bench need's command can be read at all, by the bench's own parser."""
        benched = [n for n in found if n.by == "bench"]
        if not benched:
            return []
        args = [json.dumps(n.argv()) for n in benched]
        done = self.box.helper("usage", args)
        for line in reversed(done.output.splitlines()):
            if line.startswith(usage_module.MARK):
                said = json.loads(line[len(usage_module.MARK) :])
                return [
                    f"the need {n.id!r}: the bench can't read `{n.command}`: {w}"
                    for n, w in zip(benched, said, strict=True)
                    if w
                ]
        return [f"the commands couldn't be read: exit {done.code}: {done.output[-400:]}"]

    def _start_round(self) -> None:
        self.state.round += 1
        n = self.state.round
        folder = self.folder / "rounds" / f"{n:03}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "start.json").write_text(json.dumps(paths.texts(self.work)), encoding="utf-8")
        failing = [k for k, v in self.state.last_readings.items() if not v["passed"]]
        self.ledger.round_started(
            self.state.name,
            n,
            self.session.requests + 1,
            self.state.needs_version,
            paths.snapshot(self.work),
            failing,
        )

    def done(self, args: dict[str, Any]) -> Judged:
        if not self.state.needs:
            reply = "There are no needs yet, so there's nothing to check it against: `needs` first."
            return Judged("done", "failed", reply, "no_needs")
        missing = self.tools._existing("done", args, "script")
        if missing is not None:
            return missing
        script = str(args["script"])
        n = self.state.round
        folder = self.folder / "rounds" / f"{n:03}"
        shutil.rmtree(folder / "judged", ignore_errors=True)
        wanted = self._needs()
        judgement = judge.check(self.box, self.work, script, wanted, self.state.picks, folder)
        files = paths.snapshot(self.work)
        diff = self._diff(folder)
        (folder / "diff.patch").write_text(diff, encoding="utf-8")
        for need, reading in zip(wanted, judgement.readings, strict=True):
            self.ledger.reading(self.state.name, n, need, reading)
        self.ledger.round_ended(
            self.state.name,
            n,
            self.session.requests,
            script,
            diff,
            files,
            judgement.code,
            judgement.seconds,
            judgement.met,
            str(args.get("fix_shape") or "") or None,
        )
        reply = self._said_of(judgement, wanted)
        self._remember(judgement)
        unchanged = files == self.state.last_files
        self.state.unchanged = self.state.unchanged + 1 if unchanged else 0
        self.state.last_files = files
        self._write_record()
        detail = {"round": n, "met": judgement.met, "code": judgement.code}
        if judgement.met:
            self._final(wanted, folder)
            self.save()
            said = {"round": n, "script": script}
            return Judged("done", "ok", reply, detail=detail, ending=Ending(MET, said))
        return self._next_round(reply, detail)

    def _next_round(self, reply: str, detail: dict[str, Any]) -> Judged:
        n = self.state.round
        ending: Ending | None = None
        if self.state.unchanged >= self.limits.stuck_rounds:
            said = {"rounds": self.state.unchanged, "limit": self.limits.stuck_rounds}
            ending = Ending("stuck", said)
        elif n >= self.limits.rounds:
            ending = Ending("round_cap", {"limit": self.limits.rounds})
        else:
            ending = self._disk_floor_ending()
        if ending is None:
            self._start_round()
            reply += (
                f"\n\nRound {self.state.round} starts now, from what's there: your script and part "
                "are as you left them, and `record/ROUNDS.md` holds every round so far."
            )
        self.save()
        return Judged("done", "ok", reply, detail=detail, ending=ending)

    def _disk_floor_ending(self) -> Ending | None:
        try:
            free = host.free_gb()
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise Gone(f"Docker's disk couldn't be read: {type(exc).__name__}") from exc
        if free < host.FLOOR_GB:
            return Ending("disk_floor", {"free_gb": round(free, 1), "floor_gb": host.FLOOR_GB})
        return None

    def _diff(self, folder: Path) -> str:
        before = json.loads((folder / "start.json").read_text(encoding="utf-8"))
        after = paths.texts(self.work)
        lines: list[str] = []
        for name in sorted(set(before) | set(after)):
            lines += difflib.unified_diff(
                before.get(name, "").splitlines(keepends=True),
                after.get(name, "").splitlines(keepends=True),
                f"a/{name}",
                f"b/{name}",
            )
        return "".join(lines)

    def _said_of(self, judgement: judge.Judgement, wanted: list[Need]) -> str:
        n = self.state.round
        opening = f"Round {n}: every need is met." if judgement.met else f"Round {n}: not met yet."
        lines = [
            opening,
            f"`{judgement.script}` ran again from a clean start, in a fresh container: exit "
            f"{judgement.code} after {judgement.seconds} s.",
        ]
        if judgement.code != 0:
            lines.append(f"Its output ended:\n{judgement.tail[-FAILURE_CHARS:]}")
        for need, reading in zip(wanted, judgement.readings, strict=True):
            before = self.state.last_readings.get(need.id)
            was = ""
            if before is not None:
                value = before.get("value")
                last = value if value is not None else before.get("verdict") or before["why"][:60]
                was = f" (last round: {last})"
            mark = "met" if reading.passed else "NOT MET"
            limit = f" Limit: {need.limit()}." if need.by == "bench" else ""
            lines.append(f"- {need.id}, {mark}: {reading.why}.{limit}{was}")
            if not reading.passed and reading.text:
                lines.append(reading.text[:FAILURE_CHARS].rstrip())
        return "\n".join(lines)

    def _remember(self, judgement: judge.Judgement) -> None:
        for reading in judgement.readings:
            self.state.last_readings[reading.need] = {
                "passed": reading.passed,
                "value": reading.value,
                "verdict": reading.verdict,
                "why": reading.why,
            }

    def _write_record(self) -> None:
        """`record/ROUNDS.md`, written by code from the ledger, for the model to go on from. Its
        copy in `work/` is the model's to read: nothing is ever read back from it."""
        run = self.state.name
        lines = ["# The rounds so far, as the harness read them", ""]
        for row in self.ledger.rows("SELECT * FROM rounds WHERE run=? ORDER BY round", run):
            if row["ended_at"] is None:
                continue
            n = row["round"]
            lines.append(
                f"## Round {n}: {'met' if row['met'] else 'not met'}. `{row['script']}` from a "
                f"clean start: exit {row['clean_run_exit']} after {row['clean_run_seconds']} s"
            )
            changed = sum(
                1
                for line in str(row["diff"] or "").splitlines()
                if line[:1] in "+-" and line[:3] not in ("+++", "---")
            )
            lines.append(f"Lines changed in the round: {changed}. See `record/round-{n}.diff`.")
            found = self.ledger.rows(
                "SELECT * FROM readings WHERE run=? AND round=? ORDER BY need", run, n
            )
            for reading in found:
                mark = "met" if reading["passed"] else "NOT MET"
                lines.append(f"- {reading['need']}, {mark}: {reading['why']}")
            lines.append("")
        for row in self.ledger.rows("SELECT * FROM picks WHERE run=? ORDER BY ask", run):
            lines.append(
                f"Asked in round {row['round']}: {row['question']} Picked: {row['pick']}. "
                f"Reason: {row['reason']}"
            )
        paths.write_inside(self.work, "record/ROUNDS.md", "\n".join(lines) + "\n")
        n = self.state.round
        diff = (self.folder / "rounds" / f"{n:03}" / "diff.patch").read_text(encoding="utf-8")
        paths.write_inside(self.work, f"record/round-{n}.diff", diff)

    def _final(self, wanted: list[Need], folder: Path) -> None:
        """A sheet of what passed, for the people who see the result. It decides nothing."""
        files = list(dict.fromkeys(f for n in wanted for f in n.files()))
        if not files:
            return
        judged = folder / "judged"
        args = ["render", *files, "--renders-out", "final-render"]
        done = self.box.bench(args, where=judged)
        record = recorded(done.output, judged) or {}
        sheets = [str(f) for f in record.get("files") or [] if str(f).endswith("-sheet.png")]
        if sheets and paths.inside(judged, sheets[-1]) is not None:
            target = self.folder / "final-sheet.png"
            target.write_bytes(paths.read_bytes_inside(judged, sheets[-1]))

    def blocked(self, args: dict[str, Any]) -> Judged:
        reason = args["reason"]
        return Judged("blocked", "ok", "", ending=Ending("blocked", {"reason": reason}))

    def ask(self, args: dict[str, Any]) -> Judged:
        question = args["question"].strip()
        variants = args["variants"]
        need = args.get("need") or None
        bad = self._ask_problem(question, variants, need)
        if bad is not None:
            return Judged("ask", "failed", bad[1], bad[0])
        command = ["render"]
        named: list[dict[str, Any]] = []
        for variant in variants:
            files = variant["files"] if isinstance(variant["files"], list) else [variant["files"]]
            for path in files:
                missing = self.tools._existing("ask", {"path": str(path)})
                if missing is not None:
                    return missing
            named.append(
                {"name": variant["name"], "files": files, "about": str(variant.get("about") or "")}
            )
            command += ["--variant", f"{variant['name']}={','.join(files)}"]
        n = self.state.asks + 1
        views = str(args.get("views") or " ".join(ASK_VIEWS)).split()
        out = f".pictures/ask-{n}"
        command += ["--views", *views, "--size", ASK_SIZE, "--renders-out", out]
        done = self.box.bench(command)
        record = recorded(done.output, self.work)
        sheets = [str(f) for f in (record or {}).get("files") or [] if str(f).endswith(".png")]
        if done.code != 0 or not sheets:
            reply = (
                f"The sheet couldn't be made, so this hasn't gone to the Sponsor:\n{done.output}"
            )
            return Judged("ask", "failed", reply[:FAILURE_CHARS], "no_sheet")
        self.state.asks = n
        folder = self.folder / "asks" / str(n)
        (folder / "variants").mkdir(parents=True)
        image = paths.read_bytes_inside(self.work, sheets[-1])
        (folder / "sheet.png").write_bytes(image)
        for variant in named:
            kept = folder / "variants" / variant["name"]
            kept.mkdir()
            for path in variant["files"]:
                (kept / Path(path).name).write_bytes(paths.read_bytes_inside(self.work, path))
        asked = {"question": question, "need": need, "variants": named, "round": self.state.round}
        (folder / "asked.json").write_text(json.dumps(asked, indent=1), encoding="utf-8")
        about = "\n".join(f"- **{v['name']}**: {v['about']}" for v in named)
        (folder / "question.md").write_text(
            f"{question}\n\n{about}\n\nThe sheet: {folder / 'sheet.png'}\n", encoding="utf-8"
        )
        self.ledger.asked(
            self.state.name,
            n,
            self.state.round,
            need,
            question,
            named,
            str(folder / "sheet.png"),
            self.session.requests,
        )
        self.state.stop = {"kind": "ask", "n": n}
        self.save()
        reply = (
            "The sheet has gone to the Sponsor. Their pick and reason come in your next message."
        )
        encoded = base64.b64encode(image).decode()
        return Judged(
            "ask",
            "ok",
            reply,
            images=[(f"asks/{n}/sheet.png", encoded)],
            ending=Ending("ask", {"n": n}),
        )

    def _ask_problem(
        self, question: str, variants: list[Any], need: str | None
    ) -> tuple[str, str] | None:
        if not question:
            return "empty", "`question` says what they're judging."
        if not variants:
            return "no_variants", "Name at least one variant: the Sponsor picks from a sheet."
        seen: set[str] = set()
        for variant in variants:
            shape = 'Each variant is {"name": "A", "files": ["a.step"], "about": "..."}.'
            if not isinstance(variant, dict) or "name" not in variant or "files" not in variant:
                return "not_a_variant", shape
            if not VARIANT.fullmatch(str(variant["name"])) or variant["name"] == "none":
                return "not_a_variant", f"`{variant['name']}` won't do as a short name. {shape}"
            if variant["name"] in seen:
                return "not_a_variant", f"Two variants are named {variant['name']}."
            seen.add(variant["name"])
        if need is not None and need not in [n.id for n in self._needs() if n.by == "eye"]:
            eyes = ", ".join(n.id for n in self._needs() if n.by == "eye") or "none yet"
            return "no_such_need", f"`need` names an eye need. They are: {eyes}."
        return None


def summarise(folder: Path) -> dict[str, Any]:
    """A run's figures, from its events and nothing else, saved beside them: `summary.json`."""
    events = folder / "events.jsonl"
    calls = [e for e in transcript.events_of(events) if e["kind"] == "call"]
    tools = [e for e in transcript.events_of(events) if e["kind"] == "tool"]
    requests = [e["n"] for e in transcript.events_of(events) if e["kind"] == "request"]
    used: Counter[str] = Counter()
    for call in calls:
        used.update(call.get("usage") or {})
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    tally = Counter(f"{e['name']} {e['outcome']}" for e in tools)
    found = {
        "how_it_ran": "read from events.jsonl and state.json by `scripts/rounds.py summary`",
        "ending": state["ending"],
        "stop": state["stop"],
        "rounds": state["round"],
        "asks": state["asks"],
        "questions": state["questions"],
        "restarts": state["restarts"],
        "harness": state["harness"],
        "model_calls": len(calls),
        "requests_recorded": len(requests),
        "requests_rebuilt": sum(1 for n in requests if transcript.rebuild(events, n)["messages"]),
        "model_seconds": round(sum(float(c.get("seconds") or 0) for c in calls), 1),
        "longest_call_seconds": max((float(c.get("seconds") or 0) for c in calls), default=0.0),
        "reasoning_chars": sum(int(c.get("reasoning_chars") or 0) for c in calls),
        "completion_tokens": used["completion_tokens"],
        "reasoning_tokens": used["reasoning_tokens"],
        "largest_prompt_tokens": max(
            (int((c.get("usage") or {}).get("prompt_tokens") or 0) for c in calls), default=0
        ),
        "runaways": sum(1 for c in calls if c.get("finish") in ("length", "runaway")),
        "tool_calls": len(tools),
        "by_tool_and_outcome": dict(sorted(tally.items())),
        "compactions": sum(1 for e in transcript.events_of(events) if e["kind"] == "compacted"),
        "call_retries": sum(1 for e in transcript.events_of(events) if e["kind"] == "call_retry"),
    }
    (folder / "summary.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    return found


@contextmanager
def only_session(root: Path) -> Iterator[None]:
    """One live session under a root: the Spark's rule for this work, and a resume's safety."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".session.lock").open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise Refused(f"another session is running under {root}") from exc
        yield


def what_next(run: Run, ending: Ending) -> str:
    """What a person does now, in a line."""
    name = run.state.name
    if ending.name == "questions":
        n = ending.said["n"]
        folder = run.folder / "questions"
        return (
            f"waiting: questions in {folder / f'{n}.md'}. Write the answers to "
            f"{folder / f'{n}.answers.md'}, then: scripts/rounds.py resume {name}"
        )
    if ending.name == "ask":
        folder = run.folder / "asks" / str(ending.said["n"])
        return (
            f"waiting: {folder / 'question.md'} and its sheet. Write {folder / 'pick.json'}, "
            f'{{"pick": "<a variant or none>", "reason": "..."}}, then: scripts/rounds.py resume '
            f"{name}"
        )
    said = json.dumps(ending.said)
    return f"ended: {ending.name} {said}. The ledger and records are in {run.folder}"
