"""One run, from a request to a named ending: intake, rounds, and the stops for the Sponsor.

`docs/rounds.md` says what a run is, and what each file in its folder holds. A run lives in
`~/print-shop/design-runs/<name>/`, off this public repository. Model-written code touches only its
`work/`. The records sit beside that, where no container can reach them.

Everything the model says is data here. It stops for the Sponsor at `questions` and `ask`, and
`blocked` ends it. The process exits on each of those: a person writes the answer into a file, and
`resume` goes on from the very message it stopped at.

`met` means every need passed its check, and doesn't mean approved. Every need passing stops the
run for approval, with a sheet of the part and the model's closing words: `done` can't end a run on
its own. A person approves it, or sends it back with a note, and the ledger records which and who.
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
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from print_shop.bench import colours as colours_module
from print_shop.bench import host
from print_shop.bench import overlap as overlap_module
from print_shop.bench import usage as usage_module
from print_shop.rounds import (
    after_action,
    judge,
    lessons,
    needs,
    paths,
    prompt,
    records,
    review,
    skills,
    transcript,
)
from print_shop.rounds.box import Box, Gone, Ran
from print_shop.rounds.judge import SPONSOR, STAND_IN
from print_shop.rounds.ledger import DesignLedger, now
from print_shop.rounds.needs import GEOMETRY, UNSURE, Need, Reading
from print_shop.rounds.send import ALIAS
from print_shop.rounds.session import Ending, Judged, Limits, Session
from print_shop.rounds.tools import WorkTools, recorded

DEFAULT_ROOT = Path("~/print-shop/design-runs").expanduser()
MET = "met"
APPROVED = "approved"
#: Stops, which wait for a person and aren't endings. `met` waits for approval.
WAITING = ("questions", "ask", MET)
#: Who a note is from, unless a person says.
NOTE_FROM = "the people running this"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
VARIANT = re.compile(r"[A-Za-z0-9_-]{1,24}")
#: A failing need's bench text shown in the reply.
FAILURE_CHARS = 4000
#: The default views on an `ask`'s sheet and on the sheet at approval, and each picture's side.
ASK_VIEWS = ("iso", "top", "front")
ASK_SIZE = "512"
#: The most rows of a sheet at approval the model is shown. The person always has it, and a very
#: tall picture could fail the call that carries it, which isn't worth a run.
SHOWN_ROWS = 4
#: A sheet's row name, from a file's name where nobody gave it one.
NOT_IN_A_NAME = re.compile(r"[^A-Za-z0-9_-]+")


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
    # Relayed notes: a source of their own, and kept for a conversation that starts from the record.
    notes: list[str] = field(default_factory=list)
    approvals: int = 0
    # Needs changed while their old check failed, with the model's reason: shown at the next stop.
    swaps: list[dict[str, Any]] = field(default_factory=list)
    # An ask drawn and shown to the model, which hasn't said what's in it yet, so it hasn't gone.
    draft: dict[str, Any] | None = None


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


def picked_by_words(by: str) -> str:
    """Who picked, as a sentence's subject. "The Sponsor" is said only of the Sponsor."""
    if by == SPONSOR:
        return "The Sponsor"
    return f"A stand-in for the Sponsor ({by[len(STAND_IN) :].strip()})"


def _who_decides(by: str, path: object) -> None:
    """A pick or a decision says who made it, so someone else's can't pass for the Sponsor's."""
    if by != SPONSOR and not (by.startswith(STAND_IN) and by[len(STAND_IN) :].strip()):
        raise Refused(
            f'{path}: `by` says who picked: "{SPONSOR}" only if they did, or '
            f'"{STAND_IN} <who>" for anyone standing in. Not {by!r}'
        )


def _sheet_line(line: str) -> bool:
    """A line of `render`'s answer that says what's on its sheet."""
    if line.startswith(("verdict:", "drawn:", "variant ")):
        return True
    return "not seen in any view" in line or "overlap by" in line


def _look_args(looks: dict[str, dict[str, str]]) -> list[str]:
    return [
        f"--look={colours_module.argument(name, look['colour'], look.get('label', ''))}"
        for name, look in looks.items()
    ]


def shared_files(groups: list[dict[str, Any]], part: list[str]) -> list[str]:
    """The files of `part` in no group, which print with every group."""
    owned = {f for g in groups for f in g["files"]}
    return [f for f in part if f not in owned]


def _clashes(groups: list[dict[str, Any]], part: list[str], overlap: dict[str, Any]) -> list[str]:
    """Each pair of files printed together that takes the same space, by both methods."""
    if overlap.get("error"):
        return [f"which files take the same space couldn't be read: {overlap['error']}"]
    shared = shared_files(groups, part)
    unread = overlap.get("unread") or {}
    found = [f"`{name}` couldn't be read: {why}" for name, why in unread.items()]
    for group in groups:
        together = set(group["files"]) | set(shared)
        for pair in overlap.get("pairs") or []:
            a, b = pair["files"]
            occt = pair.get("occt_mm3")
            agreed = occt is None or occt > overlap_module.SLIVER_MM3
            if a in together and b in together and agreed:
                shown = f"{pair['mesh_mm3']:g} mm³" + (f", {occt:g} by OCCT" if occt else "")
                found.append(
                    f"`{a}` and `{b}` share {shown}, and the group `{group['name']}` prints them "
                    "together: files printed together can't take the same space. Name `groups`, "
                    "each with its own files, for the ways it prints"
                )
    return found


def _same_check(a: Need, b: Need) -> bool:
    return (a.by, a.command, a.verdicts, a.number, a.at_least, a.at_most, a.look_for) == (
        b.by,
        b.command,
        b.verdicts,
        b.number,
        b.at_least,
        b.at_most,
        b.look_for,
    )


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
        self.recorder: records.Recorder | None = None
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
        run._give_lessons()
        run.save()
        run._listen()
        run.session.begin(
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": run._start_text()},
        )
        return run

    def _give_lessons(self) -> None:
        """Every lesson about how to build a reviewer kept, into the first message, and a row of
        `run_lessons` for each. `lessons.md` holds them as given, so a conversation started again
        from the record has the same."""
        kept = lessons.to_give(self.ledger)
        if not kept:
            return
        (self.folder / "lessons.md").write_text(lessons.block(kept) + "\n", encoding="utf-8")
        lessons.give(self.ledger, self.state.name, kept)

    def _listen(self) -> None:
        """Each event from here on goes to the ledger as it's kept."""
        self.recorder = records.Recorder(
            self.ledger, self.state.name, self.state.harness, self.limits.review_repeat_share
        )
        self.recorder.feed_all(transcript.events_of(self.session.events))
        self.session.heard = self.recorder.feed

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
        # What the ledger hasn't got yet: an older run's, or what a killed process didn't reach.
        run._listen()
        return run

    def resume(
        self, note: str = "", reopen_needs: bool = False, note_from: str = NOTE_FROM
    ) -> None:
        """Take in what a person wrote, ready to go on. Refused until it's there."""
        ending = (self.state.ending or {}).get("name")
        if ending == MET:
            raise Refused("this run ended at `met`, from before approval was a stop")
        if ending == APPROVED:
            raise Refused("this run is approved: only a send-back goes on from here")
        stop = self.state.stop
        if stop and stop["kind"] == MET:
            raise Refused(
                f"this run is waiting for approval: {self.folder / 'approvals' / str(stop['n'])}. "
                "It takes `--approve` or `--send-back`, with `--by`"
            )
        if stop and stop["kind"] == "questions":
            self._take_answers(
                int(stop["n"]), note_from if note_from != NOTE_FROM else records.ANSWERS_FROM
            )
        elif stop and stop["kind"] == "ask":
            self._take_pick(int(stop["n"]))
        self._go_on()
        if reopen_needs:
            self.state.needs_open = True
            self._relay(
                "reopen_needs",
                note_from,
                "The needs may be settled again: send `needs` with the whole list if they should "
                "change.",
            )
        if note.strip():
            self._note(note.strip(), note_from)
        self.save()

    def _go_on(self) -> None:
        """A new process's part of the run, under the code it's running on."""
        commit, dirty = repository_version()
        self.state.harness.append(f"{commit}{'+changes' if dirty else ''}")
        self.state.stop = None
        self.state.ending = None
        self.session._record("resumed", commit=self.state.harness[-1])
        self.ledger.run_resumed(self.state.name)

    def _relay(self, kind: str, who: str, text: str, message: str | None = None) -> None:
        """Something a person sent, to the model and into the ledger: it's the largest cause of
        change in a run. `text` is theirs, and `message` how it's put to the model."""
        said = message if message is not None else text
        self.session._record(
            "relayed",
            relay=kind,
            **{"from": who},
            text=text,
            message=said,
            at_call=self.session.requests + 1,
            round=self.state.round,
        )
        self.session.say(said)

    def _note(self, note: str, who: str) -> None:
        self.state.notes.append(note)
        self._relay("note", who, note, f"A note from the people running this:\n\n{note}")

    def approve(self, by: str, note: str = "") -> Ending:
        """A person's yes. It ends the run: the refinement pass and the delivery come after this."""
        stop = self.state.stop
        if not stop or stop["kind"] != MET:
            raise Refused("this run isn't waiting for approval")
        _who_decides(by, "--by")
        self.ledger.decided(self.state.name, int(stop["n"]), "approved", by, note.strip())
        self.state.stop = None
        said = {"approval": stop["n"], "by": by, "note": note.strip()}
        return self._close(self.session._ended(Ending(APPROVED, said)))

    # The after-action review, after the approval

    def after_action_review(self) -> Ending:
        """One more turn on this session, after a person's approval: the model writes its run's
        lessons and scores those it was given (`after_action.py`). The run is approved whatever
        happens here. How it ended is a row of `after_actions`, and so is a review that couldn't
        begin."""
        approval = self._reviewable()
        n, first = self._after_action_begun(approval)
        decided = self.ledger.rows(
            "SELECT by, note FROM decisions WHERE run=? AND approval=? AND decision='approved' "
            "ORDER BY id DESC LIMIT 1",
            self.state.name,
            approval,
        )
        if first and decided:
            by, note = str(decided[0]["by"]), str(decided[0]["note"] or "")
            said = f"{picked_by_words(by)} approved the part."
            if note:
                said += f" Their note:\n\n{note}"
            self._relay("approved", by, note, said)
        self.tools.only(after_action.TOOLS, after_action.spec())
        self.session.say(after_action.ask(self.ledger, self.state.name, self.session.requests))
        limits = self.session.limits
        self.session.limits = replace(
            limits, calls=self.session.calls + self.limits.after_action_calls
        )
        try:
            ending = self.session.go()
        except KeyboardInterrupt:
            ending = self.session._ended(Ending("stopped", {"by": "signal"}))
        except Gone as gone:
            ending = self.session._ended(Ending("container_failed", {"error": str(gone)[:400]}))
        except Exception as exc:  # noqa: BLE001 - it ends named, and the run stays approved
            fault = self.folder / f"after-action-fault-{n}.txt"
            fault.write_text(traceback.format_exc(), encoding="utf-8")
            fell = {"error": type(exc).__name__, "trace": fault.name}
            ending = self.session._ended(Ending("harness_fault", fell))
        finally:
            self.session.limits = limits
        self._after_action_ended(n, ending)
        return ending

    def after_action_not_begun(self, why: str) -> Ending:
        """A review that couldn't begin, such as with no key: said, and the run stays approved."""
        approval = self._reviewable()
        n, _ = self._after_action_begun(approval)
        ending = Ending("not_begun", {"why": why[:400]})
        self._after_action_ended(n, ending)
        return ending

    def _reviewable(self) -> int:
        if (self.state.ending or {}).get("name") != APPROVED:
            raise Refused("the after-action review comes after a person's approval")
        approval = int((self.state.ending or {}).get("said", {}).get("approval") or 0)
        if self.ledger.rows(
            "SELECT 1 FROM after_actions WHERE run=? AND approval=? AND ended=?",
            self.state.name,
            approval,
            after_action.REVIEWED,
        ):
            raise Refused(
                f"approval {approval}'s after-action review is in: scripts/rounds.py lessons "
                f"{self.state.name}"
            )
        return approval

    def _after_action_begun(self, approval: int) -> tuple[int, bool]:
        """Its row, and whether it's this approval's first try."""
        tried = self.ledger.rows(
            "SELECT n, approval FROM after_actions WHERE run=? ORDER BY n", self.state.name
        )
        n = len(tried) + 1
        commit, dirty = repository_version()
        self.state.harness.append(f"{commit}{'+changes' if dirty else ''}")
        self.ledger.add(
            "after_actions",
            run=self.state.name,
            n=n,
            approval=approval,
            begun_at=now(),
            first_call=self.session.requests + 1,
            harness_commit=self.state.harness[-1],
        )
        self.session._record("after_action", n=n, approval=approval, commit=self.state.harness[-1])
        self.save()
        return n, not any(int(t["approval"]) == approval for t in tried)

    def _after_action_ended(self, n: int, ending: Ending) -> None:
        self.ledger.change(
            "UPDATE after_actions SET ended=?, ended_said=?, ended_at=?, last_call=? "
            "WHERE run=? AND n=?",
            ending.name,
            json.dumps(ending.said, sort_keys=True),
            now(),
            self.session.requests,
            self.state.name,
            n,
        )
        # Its calls count, and the run's ending stays the person's approval.
        self.ledger.change(
            "UPDATE runs SET calls=?, usage=?, outcomes=? WHERE run=?",
            self.session.calls,
            json.dumps(dict(self.session.usage), sort_keys=True),
            json.dumps(dict(self.session.outcomes), sort_keys=True),
            self.state.name,
        )
        self.save()

    def after_action(self, args: dict[str, Any]) -> Judged:
        """The tool: refused before the approval, and held to the ledger after it."""
        if (self.state.ending or {}).get("name") != APPROVED:
            reply = "The after-action review comes after a person's approval."
            return Judged(after_action.NAME, "failed", reply, "not_approved")
        return after_action.take(self.ledger, self.state.name, args, self.session.requests)

    def send_back(self, by: str, note: str, reopen_needs: bool = False) -> None:
        """A person's no, with what's wrong, at the stop or by hand after approving it. It goes on
        as a new round. A pick on files that then change is asked again, as ever. With
        `reopen_needs`, the needs may be settled again: on `cup-1` a send-back couldn't add the
        check it found missing, and the model was refused when it tried."""
        stop = self.state.stop
        ending = (self.state.ending or {}).get("name")
        if not (stop and stop["kind"] == MET) and ending != APPROVED:
            raise Refused("this run isn't waiting for approval, and isn't approved")
        _who_decides(by, "--by")
        if not note.strip():
            raise Refused("a send-back needs its note: it's what the model goes on from")
        n = int(stop["n"]) if stop else self.state.approvals
        self.ledger.decided(self.state.name, n, "sent_back", by, note.strip())
        self._go_on()
        self.state.notes.append(note.strip())
        message = (
            f"{picked_by_words(by)} looked at the final part and sent it back, with this note:\n\n"
            f"{note.strip()}"
        )
        self._relay("send_back", by, note.strip(), message)
        if reopen_needs:
            self.state.needs_open = True
            self._relay(
                "reopen_needs",
                by,
                "The needs may be settled again: send `needs` with the whole list if they should "
                "change.",
            )
        self._start_round()
        self.save()

    def _take_answers(self, n: int, who: str) -> None:
        path = self.folder / "questions" / f"{n}.answers.md"
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise Refused(f"the answers aren't written yet: {path}")
        answers = path.read_text(encoding="utf-8").strip()
        said = f"The Sponsor's answers:\n\n{answers}"
        self.state.said.append(said)
        self.ledger.answered(self.state.name, n, answers)
        self._relay("answers", who, answers, said)

    def _take_pick(self, n: int) -> None:
        folder = self.folder / "asks" / str(n)
        path = folder / "pick.json"
        if not path.is_file():
            raise Refused(
                f'the pick isn\'t written yet: {path}, {{"pick": ..., "reason": ..., "by": ...}}'
            )
        asked = json.loads((folder / "asked.json").read_text(encoding="utf-8"))
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise Refused(f"{path} isn't JSON: {exc}") from exc
        pick, reason = str(found.get("pick") or ""), str(found.get("reason") or "").strip()
        by = str(found.get("by") or "").strip()
        fix_first = found.get("fix_first")
        if fix_first is not None and not isinstance(fix_first, str):
            raise Refused(f"{path}: `fix_first` is what to fix first, in words")
        fix_first = (fix_first or "").strip() or None
        unknown = set(found) - {"pick", "reason", "by", "fix_first"}
        if unknown:
            raise Refused(f"{path}: a pick holds pick, reason, by and fix_first: not {unknown}")
        names = [v["name"] for v in asked["variants"]]
        if pick not in [*names, "none"]:
            raise Refused(f"{path}: the pick is one of {', '.join(names)}, or none: not {pick!r}")
        if not reason:
            raise Refused(f"{path}: a pick needs its reason, which is what the ledger learns from")
        _who_decides(by, path)
        self.ledger.picked(self.state.name, n, pick, reason, by, fix_first)
        need = asked.get("need")
        if need:
            # A "none" takes back any pick before it: the person has just said it isn't this.
            self.state.picks.pop(need, None)
        if pick != "none" and need:
            seen = asked.get("seen") or records.seen_by_kept_files(folder)
            files = (seen.get("variants") or {}).get(pick) or {}
            self.state.picks[need] = {
                "pick": pick,
                "reason": reason,
                "ask": n,
                "by": by,
                "files": files,
            }
        who = picked_by_words(by)
        if pick == "none":
            said = f"{who} didn't pick any of them. Their reason: {reason}"
        else:
            said = f"{who} picked {pick}. Their reason: {reason}"
        self.state.said.append(f'On your question "{asked["question"]}": {said}')
        self._relay("pick", by, f"{pick}: {reason}", said)
        if fix_first:
            # Apart from the pick, which is about the look: the Sponsor was asked to answer
            # `none` for a build fault on `cup-1`, which mixed two answers.
            first = (
                f"Apart from the look, {who[0].lower()}{who[1:]} says to fix this first:\n\n"
                f"{fix_first}"
            )
            self.state.said.append(first)
            self._relay("fix_first", by, fix_first, first)

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
        # `met` is what the run rests in until a person decides, so the ledger says so.
        resting = ending.name not in WAITING or ending.name == MET
        self.ledger.run_ended(
            self.state.name,
            ending.name if resting else None,
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
        relayed = [f"A note from the people running this:\n\n{n}" for n in self.state.notes]
        text = prompt.again(self._start_text(), [*self.state.said, *relayed], needs_json, state)
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
        kept = self.folder / "lessons.md"
        given_lessons = kept.read_text(encoding="utf-8") if kept.is_file() else ""
        return prompt.start(request, given, skills.start_line(), given_lessons)

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
        found, wrong = needs.read(
            args["needs"], "\n".join([request, *self.state.said]), "\n".join(self.state.notes)
        )
        if not wrong:
            wrong = self._usable(found)
        if wrong:
            said = "\n".join(f"- {w}" for w in wrong)
            return Judged("needs", "failed", f"The needs weren't taken:\n{said}", "misses")
        if args.get("try") is True:
            return self._try_needs(found)
        why_changed = args.get("why_changed") or {}
        swaps, refused = self._swaps(self._needs(), found, why_changed)
        if refused:
            said = "\n".join(f"- {w}" for w in refused)
            reply = (
                f"The needs weren't taken:\n{said}\nA need is what the work is checked against, "
                "so one whose check fails can't be swapped for one that passes without a reason a "
                "person will read: give it in `why_changed`, by the old need's id. The swap is "
                "shown at the next stop, beside both checks."
            )
            return Judged("needs", "failed", reply, "swapped_failing")
        self.state.needs = needs.dump(found)
        self.state.needs_open = False
        self.state.needs_version += 1
        version = self.state.needs_version
        for swap in swaps:
            self.ledger.swapped(self.state.name, version, self.session.requests, swap)
        self.state.swaps += [{**swap, "version": version} for swap in swaps]
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
        reply = f"The needs are settled. Round {self.state.round} starts now."
        if swaps:
            named = ", ".join(f"`{swap['need']}`" for swap in swaps)
            reply += (
                f" {named} changed while the old check failed: the person at the next stop sees "
                "both checks and your reason."
            )
        return Judged("needs", "ok", reply)

    def _read_now(self, found: list[Need]) -> list[Reading]:
        """Each bench need's check against the files as they stand, on a copy, with no clean run
        first: what a check says of what's there now."""
        scratch = self.folder / f".now-{uuid.uuid4().hex[:12]}"
        try:
            paths.copy_plain(self.work, scratch, skip_made=True)
            return [judge.read_need(self.box, n, scratch, scratch / ".kept") for n in found]
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _swaps(
        self, old: list[Need], new: list[Need], why_changed: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Each bench need changed or dropped while its check fails on the files as they stand,
        where what takes its place passes or there's nothing: on `cup-1` `capture` was swapped
        for `clearance`, and `pause` pointed at a file with no pockets, so both passed. Each
        needs its reason, and what's refused says which."""
        kept = {n.id: n for n in new}
        changed = [
            n for n in old if n.by == "bench" and not (n.id in kept and _same_check(n, kept[n.id]))
        ]
        if not changed:
            return [], []
        old_readings = self._read_now(changed)
        failing = [(n, r) for n, r in zip(changed, old_readings, strict=True) if not r.passed]
        replacing = [kept[n.id] for n, _ in failing if n.id in kept and kept[n.id].by == "bench"]
        new_readings = {r.need: r for r in self._read_now(replacing)}
        swaps: list[dict[str, Any]] = []
        refused: list[str] = []
        for need, reading in failing:
            instead = kept.get(need.id)
            after = new_readings.get(need.id)
            if after is not None and not after.passed:
                # What takes its place fails too, so nothing was weakened to pass.
                continue
            reason = str(why_changed.get(need.id) or "").strip()
            if instead is None:
                what = "drops it"
            elif instead.by == "eye":
                what = "gives it to the Sponsor's eye"
            else:
                what = f"changes its check to `{instead.command}` ({instead.limit()}), which passes"
            if not reason:
                refused.append(
                    f"`{need.id}`'s check, `{need.command}` ({need.limit()}), fails on your files "
                    f"as they stand: {reading.why}. This set {what}"
                )
                continue
            swaps.append(
                {
                    "need": need.id,
                    "old": need.plain(),
                    "old_reading": reading.why,
                    "new": instead.plain() if instead else None,
                    "new_reading": after.why if after is not None else None,
                    "reason": reason,
                    "what": what,
                }
            )
        return swaps, refused

    def _swaps_said(self) -> list[str]:
        """The needs swapped while their check failed, for the person at a stop."""
        if not self.state.swaps:
            return []
        lines = ["## Needs changed while their old check failed", ""]
        for swap in self.state.swaps:
            old = swap["old"]
            new = swap.get("new")
            lines.append(f"- **{swap['need']}** (needs version {swap['version']}):")
            lines.append(f"  - was `{old.get('command')}`, which read: {swap['old_reading']}")
            if new:
                check = new.get("command") or f"the Sponsor's eye: {new.get('look_for')}"
                then = f", which read: {swap['new_reading']}" if swap.get("new_reading") else ""
                lines.append(f"  - is now `{check}`{then}")
            else:
                lines.append("  - is now gone")
            lines.append(f"  - the model's reason: {swap['reason']}")
        return [*lines, ""]

    def _try_needs(self, found: list[Need]) -> Judged:
        """Each bench check against the files as they stand, settling nothing. The first real run
        guessed a number's name, since a check that's wrong once settled would stop the run for a
        person. It runs on a copy, with no clean run first, so it says only that a check works."""
        lines = ["Nothing is settled. Each check, against your files as they stand:"]
        readings: list[dict[str, Any]] = []
        read = iter(self._read_now([n for n in found if n.by == "bench"]))
        for need in found:
            if need.by == "eye":
                lines.append(f"- {need.id}: for the Sponsor's eye, so there's nothing to read.")
                continue
            reading = next(read)
            mark = "would pass" if reading.passed else "WOULDN'T PASS"
            lines.append(f"- {need.id}, {mark}: {reading.why}. Limit: {need.limit()}.")
            readings.append(
                {
                    "need": need.id,
                    "command": need.command,
                    "passed": reading.passed,
                    "why": reading.why,
                    "exit": reading.exit,
                    "verdict": reading.verdict,
                    "value": reading.value,
                    "numbers": (reading.record or {}).get("numbers"),
                }
            )
        detail = {"tried": True, "readings": readings}
        return Judged("needs", "ok", "\n".join(lines), detail=detail)

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
        rows = self._part(args["part"])
        if isinstance(rows, Judged):
            return rows
        part = list(dict.fromkeys(name for row in rows for name in row["files"]))
        looks = self._looks(args.get("looks"), part, "done")
        if isinstance(looks, Judged):
            return looks
        groups = self._groups(args.get("groups"), part)
        if isinstance(groups, Judged):
            return groups
        fix_shape, closing = str(args["fix_shape"]).strip(), str(args["closing"]).strip()
        if not fix_shape or not closing:
            reply = "`fix_shape` and `closing` each take your own words, and one is empty."
            return Judged("done", "failed", reply, "empty")
        script = str(args["script"])
        n = self.state.round
        folder = self.folder / "rounds" / f"{n:03}"
        shutil.rmtree(folder / "judged", ignore_errors=True)
        wanted = self._needs()
        judgement = judge.check(self.box, self.work, script, wanted, self.state.picks, folder, part)
        standing: list[judge.Standing] = []
        overlap: dict[str, Any] = {}
        clashes: list[str] = []
        if judgement.met:
            # Whatever the needs say: every file to be printed, and what's printed together.
            judged = folder / "judged"
            standing = judge.printable(self.box, part, judged, folder / "standing")
            for one in standing:
                self._stood(one, "done", judged)
            overlap = self._overlap(judged, part)
            clashes = _clashes(groups, part, overlap)
            (folder / "standing.json").write_text(
                json.dumps(
                    {
                        "printable": [one.plain() for one in standing],
                        "overlap": overlap,
                        "clashes": clashes,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        met = judgement.met and not clashes and not any(one.failed for one in standing)
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
            met,
            fix_shape,
            part,
            rows,
            closing,
            judgement.left,
        )
        reply = self._said_of(judgement, wanted, met, standing, clashes)
        self._remember(judgement)
        unchanged = files == self.state.last_files
        self.state.unchanged = self.state.unchanged + 1 if unchanged else 0
        self.state.last_files = files
        self._write_record()
        detail = {"round": n, "met": met, "code": judgement.code}
        if met:
            delivered = {"groups": groups, "looks": looks, "standing": standing, "overlap": overlap}
            return self._stop_for_approval(
                rows, folder, judgement, wanted, closing, reply, detail, delivered
            )
        return self._next_round(reply, detail)

    def _stood(self, one: judge.Standing, stop: str, where: Path, ask: int | None = None) -> None:
        sha = None
        if paths.inside(where, one.file) is not None and (where / one.file).is_file():
            sha = paths.content_hash(one.file, paths.read_bytes_inside(where, one.file))
        self.ledger.stood(
            self.state.name,
            round=self.state.round,
            at_call=self.session.requests,
            stop=stop,
            ask=ask,
            check=one.check,
            file=one.file,
            command=one.command,
            passed=one.passed,
            unsure=one.unsure,
            verdict=one.verdict,
            why=one.why,
            numbers=one.numbers,
            file_sha=sha,
        )

    def _overlap(self, where: Path, files: list[str]) -> dict[str, Any]:
        """Each file's box, and each pair that takes the same space, read in a fresh container."""
        done = self.box.helper("overlap", files, timeout=self.limits.command_seconds, where=where)
        for line in reversed(done.output.splitlines()):
            if line.startswith(overlap_module.MARK):
                found: dict[str, Any] = json.loads(line[len(overlap_module.MARK) :])
                return found
        return {"error": f"exit {done.code}: {done.output[-400:]}", "files": {}, "pairs": []}

    def _looks(
        self, given: Any, allowed: list[str], tool: str
    ) -> dict[str, dict[str, str]] | Judged:
        """Each file's colour and short label, as the model gives them: what sheets draw, and
        what the delivered folder names each file by."""
        shape = (
            '`looks` gives files their colour and a short label: {"pane_1.step": {"colour": '
            '"blue", "label": "cup"}}.'
        )
        if not given:
            return {}
        if not isinstance(given, dict):
            return Judged(tool, "failed", shape, "not_a_look")
        found: dict[str, dict[str, str]] = {}
        for name, look in given.items():
            if name not in allowed:
                reply = f"`looks` names `{name}`, which isn't one of the files here. {shape}"
                return Judged(tool, "failed", reply, "not_a_look")
            if not isinstance(look, dict) or not set(look) <= {"colour", "label"}:
                return Judged(tool, "failed", shape, "not_a_look")
            colour = str(look.get("colour") or "").strip()
            label = str(look.get("label") or "").strip()
            try:
                colours_module.parse(colours_module.argument(name, colour, label))
            except colours_module.NotAColour as exc:
                return Judged(tool, "failed", f"`{name}`: {exc}. {shape}", "not_a_look")
            found[name] = {"colour": colour, "label": label}
        return found

    def _groups(self, given: Any, part: list[str]) -> list[dict[str, Any]] | Judged:
        """The ways the part is printed, each with its own files. A file of `part` in no group
        is in every group, as the panes are."""
        shape = (
            '`groups` are the ways to print it, each with its own files: [{"name": "two-parts", '
            '"files": ["frame_base.step", "cap.step"], "about": "how it prints"}, {"name": '
            '"one-frame", "files": ["frame.step"], "about": "with a pause at 4.2 mm"}]. Files of '
            "`part` in no group are printed with every group."
        )
        if not given:
            return [{"name": "part", "about": "", "files": list(part)}]
        if not isinstance(given, list):
            return Judged("done", "failed", shape, "not_a_group")
        groups: list[dict[str, Any]] = []
        for entry in given:
            if not isinstance(entry, dict) or not {"name", "files"} <= set(entry) <= {
                "name",
                "files",
                "about",
            }:
                return Judged("done", "failed", shape, "not_a_group")
            name, files = str(entry["name"]), entry["files"]
            if not VARIANT.fullmatch(name) or name in [g["name"] for g in groups]:
                reply = f"`{name}` won't do as a group's name. {shape}"
                return Judged("done", "failed", reply, "not_a_group")
            if not (isinstance(files, list) and files and all(isinstance(f, str) for f in files)):
                return Judged("done", "failed", shape, "not_a_group")
            outside = [f for f in files if f not in part]
            if outside:
                reply = f"The group `{name}` names `{outside[0]}`, which isn't in `part`. {shape}"
                return Judged("done", "failed", reply, "not_a_group")
            groups.append({"name": name, "about": str(entry.get("about") or ""), "files": files})
        return groups

    def _part(self, given: list[Any]) -> list[dict[str, Any]] | Judged:
        """The files that are the part, as `done` names them, each entry a row of the sheet. It's
        the model's word, and it's what the people's sheet draws: a test piece a script writes is
        left off it, and two parts in one `part` aren't drawn on top of each other."""
        shape = (
            '`part` names the files that are the part: `["stand.step"]`, with files seen together '
            'as {"name": "wide", "files": ["base.step", "lid.step"]}.'
        )
        if not given:
            return Judged("done", "failed", shape, "no_part")
        rows: list[dict[str, Any]] = []
        for entry in given:
            if isinstance(entry, str):
                stem = NOT_IN_A_NAME.sub("_", Path(entry).stem)[:24].strip("_") or "part"
                row: dict[str, Any] = {"name": stem, "files": [entry]}
            elif isinstance(entry, dict) and set(entry) == {"name", "files"}:
                row = {"name": str(entry["name"]), "files": entry["files"]}
                if not VARIANT.fullmatch(str(row["name"])):
                    return Judged("done", "failed", f"`{row['name']}` won't do. {shape}", "no_part")
            else:
                return Judged("done", "failed", shape, "no_part")
            files = row["files"]
            if not (isinstance(files, list) and files and all(isinstance(f, str) for f in files)):
                return Judged("done", "failed", shape, "no_part")
            for name in files:
                if not name.lower().endswith(GEOMETRY):
                    reply = f"`part` names STEP or STL files, and `{name}` isn't one."
                    return Judged("done", "failed", reply, "not_geometry")
                if paths.inside(self.work, name) is None or "," in name:
                    reply = "Only files inside your directory, and never through a link."
                    return Judged("done", "failed", reply, "outside")
            taken = [r["name"] for r in rows]
            row["name"] = next(
                candidate
                for k in range(1, len(rows) + 2)
                if (candidate := row["name"] if k == 1 else f"{row['name']}-{k}") not in taken
            )
            rows.append(row)
        return rows

    def _stop_for_approval(
        self,
        rows: list[dict[str, Any]],
        folder: Path,
        judgement: judge.Judgement,
        wanted: list[Need],
        closing: str,
        reply: str,
        detail: dict[str, Any],
        delivered: dict[str, Any],
    ) -> Judged:
        """Every need passed, and every file to be printed passed `thin`, so a person looks: the
        sheet of the part, the model's words, and `review/`, the folder to print from."""
        self.state.approvals += 1
        n = self.state.approvals
        stop = self.folder / "approvals" / str(n)
        stop.mkdir(parents=True)
        looks = delivered["looks"]
        image, why_not = self._final(rows, folder, looks)
        part = list(dict.fromkeys(name for row in rows for name in row["files"]))
        made = review.write(
            stop / "review",
            run=self.state.name,
            round_n=self.state.round,
            approval=n,
            judged=folder / "judged",
            part=part,
            groups=delivered["groups"],
            looks=looks,
            wanted=wanted,
            readings=judgement.readings,
            standing=delivered["standing"],
            overlap=delivered["overlap"],
            swaps=self.state.swaps,
            closing=closing,
        )
        # The latest stop's folder is the run's, for the person who prints it.
        shutil.rmtree(self.folder / "review", ignore_errors=True)
        shutil.copytree(stop / "review", self.folder / "review")
        sheet: Path | None = None
        if image is not None:
            sheet = stop / "sheet.png"
            sheet.write_bytes(image)
        (stop / "closing.md").write_text(closing + "\n", encoding="utf-8")
        lines = [
            f"# Round {self.state.round}: every need passed its check. It isn't approved yet.",
            "",
            f"The sheet: {sheet}" if sheet else f"The sheet couldn't be made: {why_not}",
            "",
            "## What the model said",
            "",
            closing,
            "",
            "## Each need",
            "",
        ]
        for need, reading in zip(wanted, judgement.readings, strict=True):
            limit = f" Limit: {need.limit()}." if need.by == "bench" else ""
            lines.append(f"- **{need.id}** ({need.what}): {reading.why}.{limit}")
        lines += [
            "",
            "## Every file to be printed, by `thin`, whatever the needs say",
            "",
        ]
        for one in delivered["standing"]:
            mark = "can't tell" if one.unsure else "ok"
            lines.append(f"- `{one.file}`, {mark}: {one.why}")
        lines += ["", *self._swaps_said()]
        groups = ", ".join(f"`{g['folder']}`" for g in made["groups"])
        lines += [
            "## The folder to print from",
            "",
            f"{self.folder / 'review'}: {groups}, with its README. Open one folder only.",
        ]
        (stop / "stop.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self.state.swaps:
            self.ledger.swaps_shown(self.state.name, f"approval {n}")
            self.state.swaps = []
        part_hashes = {name: judgement.left.get(name) for row in rows for name in row["files"]}
        self.ledger.approval_asked(
            self.state.name,
            n,
            round=self.state.round,
            at_call=self.session.requests,
            sheet=str(sheet) if sheet else None,
            sheet_sha=hashlib.sha256(image).hexdigest() if image else None,
            part_rows=rows,
            part_hashes=part_hashes,
            closing=closing,
            review={"folder": str(self.folder / "review"), **made},
        )
        self.state.stop = {"kind": MET, "n": n}
        self.save()
        reply += (
            "\n\nThe run stops here for approval: a person sees the sheet and your closing words. "
            "It isn't approved yet, and if it's sent back, their note comes in your next message."
        )
        said = {"round": self.state.round, "script": judgement.script, "approval": n}
        images = []
        if image and len(rows) <= SHOWN_ROWS:
            images = [(f"approvals/{n}/sheet.png", base64.b64encode(image).decode())]
        return Judged("done", "ok", reply, images=images, detail=detail, ending=Ending(MET, said))

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

    def _said_of(
        self,
        judgement: judge.Judgement,
        wanted: list[Need],
        met: bool | None = None,
        standing: list[judge.Standing] | None = None,
        clashes: list[str] | None = None,
    ) -> str:
        n = self.state.round
        met = judgement.met if met is None else met
        if met:
            opening = f"Round {n}: every need is met."
        elif judgement.met:
            opening = (
                f"Round {n}: not met yet. Every need passed, and the harness's own checks on the "
                "files to be printed didn't."
            )
        else:
            opening = f"Round {n}: not met yet."
        lines = [
            opening,
            f"`{judgement.script}` ran again from a clean start, in a fresh container: exit "
            f"{judgement.code} after {judgement.seconds} s.",
        ]
        if judgement.code != 0:
            lines.append(f"Its output ended:\n{judgement.tail[-FAILURE_CHARS:]}")
        elif judgement.part_missing:
            named = ", ".join(f"`{name}`" for name in judgement.part_missing)
            lines.append(f"`part` names {named}, which a clean run didn't leave.")
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
        if standing:
            lines.append(
                "Every file to be printed, by `thin` at the nozzle's width, whatever the needs say:"
            )
            for one in standing:
                mark = "ok" if one.passed else "can't tell" if one.unsure else "TOO THIN"
                lines.append(f"- {one.file}, {mark}: {one.why}")
        for clash in clashes or []:
            lines.append(f"- {clash}")
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
                f"Asked in round {row['round']}: {row['question']} "
                f"{picked_by_words(row['picked_by'] or SPONSOR)} picked: {row['pick']}. "
                f"Reason: {row['reason']}"
            )
        paths.write_inside(self.work, "record/ROUNDS.md", "\n".join(lines) + "\n")
        n = self.state.round
        diff = (self.folder / "rounds" / f"{n:03}" / "diff.patch").read_text(encoding="utf-8")
        paths.write_inside(self.work, f"record/round-{n}.diff", diff)

    def _final(
        self, rows: list[dict[str, Any]], folder: Path, looks: dict[str, dict[str, str]]
    ) -> tuple[bytes | None, str]:
        """A sheet of what passed, for the people who see the result: one part a row, not all of
        `part` in one scene. It decides nothing. It draws the part alone: what else a need names is
        a test piece, which isn't what passed. It gives the picture, or why there's none."""
        judged = folder / "judged"
        args = ["render", "--renders-out", "final-render", "--size", ASK_SIZE]
        for row in rows:
            args += ["--variant", f"{row['name']}={','.join(row['files'])}"]
        args += _look_args(looks)
        args += ["--views", *ASK_VIEWS]
        done = self.box.bench(args, where=judged)
        record = recorded(done.output, judged) or {}
        sheets = [str(f) for f in record.get("files") or [] if str(f).endswith("-sheet.png")]
        if not sheets or paths.inside(judged, sheets[-1]) is None:
            return None, done.output[-FAILURE_CHARS:]
        image = paths.read_bytes_inside(judged, sheets[-1])
        (self.folder / "final-sheet.png").write_bytes(image)
        return image, ""

    def blocked(self, args: dict[str, Any]) -> Judged:
        reason = args["reason"]
        return Judged("blocked", "ok", "", ending=Ending("blocked", {"reason": reason}))

    def ask(self, args: dict[str, Any]) -> Judged:
        """The sheet is drawn and shown to the model, and nothing goes yet: `send_ask` sends it,
        with what the model says it shows. On both real runs the model described the design it
        meant to make, not what a bench picture showed."""
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
        pieces = [str(p) for p in args.get("test_pieces") or []]
        for path in pieces:
            missing = self.tools._existing("ask", {"path": path})
            if missing is not None:
                return missing
            command += ["--test-piece", path]
        drawn = list(dict.fromkeys(str(f) for v in named for f in v["files"]))
        looks = self._looks(args.get("looks"), drawn, "ask")
        if isinstance(looks, Judged):
            return looks
        command += _look_args(looks)
        command += [f"--close={close}" for close in str(args.get("close") or "").split()]
        command += [f"--cut={cut}" for cut in str(args.get("cuts") or "").split()]
        n = self.state.asks + 1
        views = str(args.get("views") or " ".join(ASK_VIEWS)).split()
        out = f".pictures/ask-{n}"
        command += ["--views", *views, "--size", ASK_SIZE, "--renders-out", out]
        done = self.box.bench(command)
        record = recorded(done.output, self.work)
        sheets = [str(f) for f in (record or {}).get("files") or [] if str(f).endswith(".png")]
        if done.code != 0 or not sheets:
            reply = f"The sheet couldn't be made, so nothing went:\n{done.output}"
            return Judged("ask", "failed", reply[:FAILURE_CHARS], "no_sheet")
        image = paths.read_bytes_inside(self.work, sheets[-1])
        standing = self._standing_at_ask(drawn)
        self.state.draft = {
            "question": question,
            "need": need,
            "variants": named,
            "test_pieces": pieces,
            "looks": looks,
            "round": self.state.round,
            "seen": {
                "variants": {v["name"]: self._held(v["files"]) for v in named},
                "test_pieces": self._held(pieces),
                "sheet": hashlib.sha256(image).hexdigest(),
            },
            "sheet": sheets[-1],
            "bench_said": [line for line in done.output.splitlines() if _sheet_line(line)],
            "standing": [one.plain() for one in standing],
            "at_call": self.session.requests,
        }
        self.save()
        lines = [
            "The sheet above is drawn, and it hasn't gone to the Sponsor. Look at it, and send it "
            "with `send_ask`, saying in `says` what the sheet shows: each picture as drawn, not "
            "the design you meant. It reaches them with the question, in your words.",
            "What the bench says it drew:",
            *[f"- {line}" for line in self.state.draft["bench_said"]],
        ]
        failing = [one for one in standing if not one.passed]
        if failing:
            lines.append(
                "The harness's own checks on these files, which go beside the question whatever "
                "you say:"
            )
            lines += [f"- {one.command}: {one.why}" for one in failing]
        lines.append("A new `ask` draws a new sheet in place of this one.")
        encoded = base64.b64encode(image).decode()
        return Judged(
            "ask",
            "ok",
            "\n".join(lines),
            images=[(f"{out}/sheet.png", encoded)],
            detail={"drafted": n, "standing_failing": len(failing)},
        )

    def _standing_at_ask(self, files: list[str]) -> list[judge.Standing]:
        """What an ask shows, read as it stands: each settled need's own check, and `thin` on each
        file. On `cup-1` a question said a pane was split in two where `capture` read one, and no
        need ran `capture` any more."""
        found: list[judge.Standing] = []
        benched = [n for n in self._needs() if n.by == "bench"]
        for need, reading in zip(benched, self._read_now(benched), strict=True):
            unsure = reading.verdict in UNSURE or not reading.verdict
            found.append(
                judge.Standing(
                    f"need {need.id}",
                    ", ".join(need.files()),
                    need.command,
                    reading.passed,
                    unsure and not reading.passed,
                    reading.verdict,
                    reading.why,
                    dict((reading.record or {}).get("numbers") or {}),
                )
            )
        scratch = self.folder / f".now-{uuid.uuid4().hex[:12]}"
        try:
            paths.copy_plain(self.work, scratch, skip_made=True)
            found += judge.printable(self.box, files, scratch, scratch / ".kept")
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return found

    def send_ask(self, args: dict[str, Any]) -> Judged:
        """The drawn sheet goes to the Sponsor, with what the model says is on it."""
        draft = self.state.draft
        if draft is None:
            reply = "There's no sheet drawn to send: `ask` draws one, and shows it to you first."
            return Judged("send_ask", "failed", reply, "no_draft")
        says = str(args["says"]).strip()
        if not says:
            reply = "`says` is what the sheet shows, in your words, and it's empty."
            return Judged("send_ask", "failed", reply, "empty")
        named = draft["variants"]
        for variant in [*named, {"files": draft["test_pieces"]}]:
            for path in variant["files"]:
                if self.tools._existing("send_ask", {"path": str(path)}) is not None:
                    reply = f"`{path}` is gone since the sheet was drawn: `ask` again."
                    return Judged("send_ask", "failed", reply, "changed")
        now = {
            "variants": {v["name"]: self._held(v["files"]) for v in named},
            "test_pieces": self._held(draft["test_pieces"]),
        }
        seen = draft["seen"]
        if now["variants"] != seen["variants"] or now["test_pieces"] != seen["test_pieces"]:
            reply = (
                "Files on the sheet changed since it was drawn, so it isn't what they'd see: "
                "`ask` again."
            )
            return Judged("send_ask", "failed", reply, "changed")
        n = self.state.asks + 1
        self.state.asks = n
        folder = self.folder / "asks" / str(n)
        (folder / "variants").mkdir(parents=True)
        image = paths.read_bytes_inside(self.work, draft["sheet"])
        (folder / "sheet.png").write_bytes(image)
        for variant in named:
            kept = folder / "variants" / variant["name"]
            kept.mkdir()
            for path in variant["files"]:
                (kept / Path(path).name).write_bytes(paths.read_bytes_inside(self.work, path))
        if draft["test_pieces"]:
            (folder / "test-pieces").mkdir()
        for path in draft["test_pieces"]:
            kept = folder / "test-pieces" / Path(path).name
            kept.write_bytes(paths.read_bytes_inside(self.work, path))
        asked = {
            "question": draft["question"],
            "need": draft["need"],
            "variants": named,
            "test_pieces": draft["test_pieces"],
            "looks": draft["looks"],
            "round": self.state.round,
            "seen": seen,
            "says_model_said": says,
            "standing": draft["standing"],
        }
        (folder / "asked.json").write_text(json.dumps(asked, indent=1), encoding="utf-8")
        about = "\n".join(f"- **{v['name']}**: {v['about']}" for v in named)
        lines = [
            draft["question"],
            "",
            about,
            "",
            f"The sheet: {folder / 'sheet.png'}",
            "",
            "## What the model says the sheet shows",
            "",
            says,
            "",
            "## What the bench says it drew",
            "",
            *[f"- {line}" for line in draft["bench_said"]],
            "",
        ]
        failing = [one for one in draft["standing"] if not one["passed"]]
        if failing:
            lines += [
                "## Checks that don't pass on these files",
                "",
                "The harness ran these itself, whatever the needs and the question say.",
                "",
                *[f"- `{one['command']}`: {one['why']}" for one in failing],
                "",
            ]
        lines += self._swaps_said()
        lines += [
            "## Answering",
            "",
            "The pick is about the look. A fault to fix first, such as a check above, goes in "
            '`"fix_first"` in pick.json, apart from the pick.',
            "",
        ]
        (folder / "question.md").write_text("\n".join(lines), encoding="utf-8")
        self.ledger.asked(
            self.state.name,
            n,
            self.state.round,
            draft["need"],
            draft["question"],
            named,
            str(folder / "sheet.png"),
            self.session.requests,
            seen,
        )
        self.ledger.sheet_said(self.state.name, n, says)
        for one in draft["standing"]:
            self._stood(judge.Standing(**one), "ask", self.work, ask=n)
        if self.state.swaps:
            self.ledger.swaps_shown(self.state.name, f"ask {n}")
            self.state.swaps = []
        self.state.draft = None
        self.state.stop = {"kind": "ask", "n": n}
        self.save()
        reply = (
            "The sheet has gone to the Sponsor, with what you said of it. Their pick and reason "
            "come in your next message."
        )
        return Judged("send_ask", "ok", reply, ending=Ending("ask", {"n": n}))

    def _held(self, files: list[str]) -> dict[str, str]:
        """Each file as it stands now, by what it holds: what a sheet of them saw."""
        return {
            str(name): paths.content_hash(str(name), paths.read_bytes_inside(self.work, str(name)))
            for name in files
        }

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
        "picked_by": {need: pick.get("by") for need, pick in state["picks"].items()},
        "approvals": state.get("approvals", 0),
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
            f'{{"pick": "<a variant or none>", "reason": "...", "by": "{SPONSOR}"}}, or "by": '
            f'"{STAND_IN} <who>" if someone stood in for them. The pick is about the look: a '
            f'fault to fix first goes in "fix_first", apart from it. Then: scripts/rounds.py '
            f"resume {name}"
        )
    if ending.name == MET:
        # What the person is asked for, and the model's closing words: nowhere else reaches them.
        folder = run.folder / "approvals" / str(ending.said["approval"])
        closing = (folder / "closing.md").read_text(encoding="utf-8").strip()
        return (
            f"waiting for approval: every need passed its check, which doesn't mean it's approved. "
            f"{folder / 'stop.md'} and its sheet. The model's closing words:\n\n{closing}\n\n"
            f'Then: scripts/rounds.py resume {name} --approve --by "{SPONSOR}", or --send-back '
            f'--by "{SPONSOR}" --note FILE. Someone else is --by "{STAND_IN} <who>".'
        )
    said = json.dumps(ending.said)
    return f"ended: {ending.name} {said}. The ledger and records are in {run.folder}"
