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
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import re
import shlex
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
    breakdown,
    descriptions,
    figures,
    holding,
    judge,
    lessons,
    needs,
    nudge,
    paths,
    prompt,
    records,
    review,
    scores,
    skills,
    toolsets,
    transcript,
    unprintable,
)
from print_shop.rounds import request as forms
from print_shop.rounds import standing as glass_checks
from print_shop.rounds.box import Box, Gone, Ran
from print_shop.rounds.judge import SPONSOR, STAND_IN
from print_shop.rounds.ledger import DesignLedger, now
from print_shop.rounds.needs import GEOMETRY, UNSURE, Need, Reading
from print_shop.rounds.send import ALIAS, UNKNOWN
from print_shop.rounds.session import Ending, Judged, Limits, Session
from print_shop.rounds.tools import WorkTools, recorded

DEFAULT_ROOT = Path("~/print-shop/design-runs").expanduser()
MET = "met"
APPROVED = "approved"
#: A person ended it without approving it: it says the run ended without one.
CLOSED = "closed"
#: The endings an after-action review follows, each a person's decision.
DECIDED = (APPROVED, CLOSED)
#: Stops, which wait for a person and aren't endings. `met` waits for approval.
WAITING = ("questions", "ask", MET)
#: Who a note is from, unless a person says.
NOTE_FROM = "the people running this"
#: Each check's latest tries a conversation started again from the record is given: the rest
#: are in `record/CHECKS.md`.
AGAIN_TRIES = 6
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
#: Said where a settled need's files or count followed the part (mqucifer/print-shop#59).
FOLLOWED = (
    "Needs whose files or pane count follow the part, as `glass` declares it, every figure as "
    "settled:"
)
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
    # The kind the request names, whose toolset the run has: None for the core only. `toolsets`
    # is false for a run from before them, which keeps every command it had.
    kind: str | None = None
    toolsets: bool = False
    # The files the latest `done` named as the part, and the call it was at: a file it left out
    # has left the part, and a failing `thin` on it isn't said any more.
    part: list[str] | None = None
    part_at: int | None = None
    # The request's set form (`request.py`), read at the start. None for a run started before it,
    # which carries on as it began: no lines held, no stops, and no breakdown asked for.
    form: dict[str, Any] | None = None
    breakdowns: int = 0
    # Each stop passed: its ask, the pick, who made it and at which call.
    stops: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Who opened the fixed needs, with a note, so they may change at the next `needs`.
    fixed_open: dict[str, Any] | None = None
    # Each failing check the model said doesn't apply, and why: held out of the list of failing
    # checks, and shown at the next stop (`holding.py`). One with `unread_file` is a part file the
    # model says no need has to read (mqucifer/print-shop#52).
    asides: list[dict[str, Any]] = field(default_factory=list)
    # The latest frames and panes `done` or `ask` declared, for the kind's standing checks
    # (`standing.py`).
    glass: list[dict[str, Any]] | None = None
    # Each `needs` refused once they were settled, shown beside the next question.
    needs_refused: list[dict[str, Any]] = field(default_factory=list)
    # Who each of `notes` is from, in order: a run from before this has none, and its notes were
    # all from the people running this.
    notes_from: list[str] = field(default_factory=list)
    # Each description of the part the model wrote, by key: what it is, the call it was written
    # at, and the part's files then, by what they hold (`descriptions.py`). A run from before this
    # has none kept, and nothing of it goes stale.
    described: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Each piece of geometry as the harness last saw it, and the call by which it was so.
    geometry: dict[str, list[Any]] = field(default_factory=dict)
    # Each description sent again or said to still hold, shown at the next stop.
    restated: list[dict[str, Any]] = field(default_factory=list)


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


def from_words(who: str) -> str:
    """Who sent something, as the model's told it: "the Sponsor" only of the Sponsor, a stand-in
    by name, and anyone else as `--from` gave them (mqucifer/print-shop#53)."""
    if who == SPONSOR:
        return "the Sponsor"
    if who.startswith(STAND_IN) and who[len(STAND_IN) :].strip():
        return f"a stand-in for the Sponsor ({who[len(STAND_IN) :].strip()})"
    return who.strip() or NOTE_FROM


def _decides(by: str) -> bool:
    """The Sponsor, or someone who says they stand in for them and who."""
    return by == SPONSOR or (by.startswith(STAND_IN) and bool(by[len(STAND_IN) :].strip()))


def _who_decides(by: str, path: object) -> None:
    """A pick or a decision says who made it, so someone else's can't pass for the Sponsor's."""
    if not _decides(by):
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


def _closed(name: str) -> str:
    return (
        "this run is closed: a person ended it without approving it. Nothing goes on from here "
        f"but its after-action review: scripts/rounds.py after-action {name}"
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
        # After a compaction, each check whose latest try fails is said again (`holding.py`).
        self.session.after_compaction = self.failing_said
        self.tools.at_call = lambda: self.session.requests

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
        try:
            form = forms.read(request)
        except forms.NotAForm as exc:
            raise Refused(str(exc)) from exc
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
        run.state = State(
            name,
            _sha(request),
            harness=[f"{commit}{'+changes' if dirty else ''}"],
            form=form.plain(),
            kind=toolsets.key(form.kind) if form.kind else None,
            toolsets=True,
        )
        run.tools.use(run.toolset)
        read = getattr(send, "effort", None)
        effort, effort_from = (
            read(alias) if callable(read) else (UNKNOWN, "its sender doesn't read LiteLLM")
        )
        run.session._record("effort", alias=alias, effort=effort, said=effort_from)
        run.ledger.run_started(
            name,
            request_sha=run.state.request_sha,
            events=str(run.session.events),
            harness_commit=run.state.harness[0],
            skills_sha=skills.sha(run.toolset.skills),
            image=host.IMAGE,
            image_id=host.image_id(),
            alias=alias,
            limits=run.limits.recorded(),
            toolset={
                "kind": run.toolset.kind,
                "commands": list(run.toolset.commands),
                "skills": list(run.toolset.skills),
            },
            reasoning_effort=effort,
            reasoning_effort_from=effort_from,
        )
        run.ledger.request_read(name, form)
        run._give_lessons()
        run.save()
        run._listen()
        run.session.begin(
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": run._start_text()},
        )
        run._skills_given()
        return run

    def _give_lessons(self) -> None:
        """Every lesson about how to build a reviewer kept, into the first message, and a row of
        `run_lessons` for each. `lessons.md` holds them as given, so a conversation started again
        from the record has the same."""
        form = self._form()
        kept = lessons.to_give(self.ledger, form.kind if form is not None else None)
        if not kept:
            return
        (self.folder / "lessons.md").write_text(lessons.block(kept) + "\n", encoding="utf-8")
        lessons.give(self.ledger, self.state.name, kept)

    def _listen(self) -> None:
        """Each event from here on goes to the ledger as it's kept."""
        self.recorder = records.Recorder(
            self.ledger,
            self.state.name,
            self.state.harness,
            self.limits.review_repeat_share,
            self.toolset.skills,
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
        run.tools.use(run.toolset)
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
        if ending == CLOSED:
            raise Refused(_closed(self.state.name))
        stop = self.state.stop
        if stop and stop["kind"] == MET:
            raise Refused(
                f"this run is waiting for approval: {self.folder / 'approvals' / str(stop['n'])}. "
                "It takes `--approve` or `--send-back`, with `--by`"
            )
        if stop and stop["kind"] == "questions":
            self._take_answers(int(stop["n"]), note_from)
        elif stop and stop["kind"] == "ask":
            self._take_pick(int(stop["n"]), reopening=reopen_needs)
        self._go_on()
        if reopen_needs:
            self._reopen(note_from, bool(note.strip()))
        if note.strip():
            self._note(note.strip(), note_from)
        self.save()

    def _reopen(self, who: str, noted: bool) -> None:
        """The needs may be settled again. A fixed need, a non-negotiable's, changes only by the
        Sponsor, or someone standing in who says who, with a note: anyone else's reopening leaves
        the fixed needs as they are (mqucifer/print-shop#37)."""
        self.state.needs_open = True
        fixed = [n.id for n in self._needs() if n.fixed]
        said = (
            "The needs may be settled again: send `needs` with the whole list if they should "
            "change."
        )
        if noted and _decides(who):
            self.state.fixed_open = {"by": who}
            if fixed:
                said += " The fixed needs may change too, as the note says."
        elif fixed:
            named = ", ".join(f"`{f}`" for f in fixed)
            said += (
                f" The fixed needs, {named}, stay as they are: a non-negotiable's need changes "
                "only by the Sponsor, with a note."
            )
        self._relay("reopen_needs", who, said)

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
        self.state.notes_from.append(who)
        self._relay("note", who, note, f"A note from {from_words(who)}:\n\n{note}")

    def approve(self, by: str, note: str = "") -> Ending:
        """A person's yes. It ends the run: the refinement pass and the delivery come after this."""
        stop = self.state.stop
        if not stop or stop["kind"] != MET:
            raise Refused("this run isn't waiting for approval")
        _who_decides(by, "--by")
        decision = self.ledger.decided(self.state.name, int(stop["n"]), APPROVED, by, note.strip())
        self.state.stop = None
        said = {"approval": stop["n"], "by": by, "note": note.strip(), "decision": decision}
        return self._close(self.session._ended(Ending(APPROVED, said)))

    def close(self, by: str, note: str) -> Ending:
        """A person ends the run without approving it, so it still gets its after-action review: a
        run that didn't succeed has the most to teach (mqucifer/print-shop#32). It works on any
        run that isn't approved, closed or running: stopped, `blocked`, cut off by a limit, or
        waiting at a stop. That it isn't running is the caller's to hold, as `scripts/rounds.py`
        does with the session's lock. A close isn't an approval, and nothing goes on from it."""
        ending = (self.state.ending or {}).get("name")
        if ending == APPROVED:
            raise Refused("this run is approved: a close is for a run that ended without that")
        if ending == CLOSED:
            raise Refused(_closed(self.state.name))
        _who_decides(by, "--by")
        if not note.strip():
            raise Refused("a close needs its note: it's what the model's lessons start from")
        stop = self.state.stop
        if stop:
            was: dict[str, Any] = {"name": stop["kind"], "said": {"n": stop["n"]}}
        elif self.state.ending:
            was = {"name": self.state.ending["name"], "said": self.state.ending.get("said") or {}}
        else:
            was = {"name": None, "said": {}}
        approval = int(stop["n"]) if stop and stop["kind"] == MET else None
        decision = self.ledger.decided(self.state.name, approval, CLOSED, by, note.strip())
        self.state.stop = None
        self.state.draft = None
        # A `stop` file that ended it has done its work, and would stop the review before its call.
        (self.folder / "stop").unlink(missing_ok=True)
        said = {"by": by, "note": note.strip(), "decision": decision, "was": was}
        if approval is not None:
            said["approval"] = approval
        return self._close(self.session._ended(Ending(CLOSED, said)))

    # The after-action review, after a person's approval or close

    def after_action_review(self) -> Ending:
        """One more turn on this session, after a person's approval or close: the model writes
        its run's lessons and scores those it was given (`after_action.py`). The run stays as the
        person left it whatever happens here. How it ended is a row of `after_actions`, and so is
        a review that couldn't begin."""
        decision, approval = self._reviewable()
        n, first = self._after_action_begun(decision, approval)
        decided = self.ledger.rows("SELECT * FROM decisions WHERE id=?", decision)
        closed = (self.state.ending or {}).get("name") == CLOSED
        # What this review says to the model, for a new conversation if this one won't fit.
        opening: list[str] = []
        if first and decided:
            by, note = str(decided[0]["by"]), str(decided[0]["note"] or "")
            if closed:
                was = (self.state.ending or {}).get("said", {}).get("was") or {}
                said = (
                    f"{picked_by_words(by)} closed this run without approving it. "
                    f"{after_action.how_it_ended(was)} Their note:\n\n{note}"
                )
                self._relay(CLOSED, by, note, said)
            else:
                said = f"{picked_by_words(by)} approved the part."
                if note:
                    said += f" Their note:\n\n{note}"
                self._relay(APPROVED, by, note, said)
            opening.append(said)
        self.tools.only(after_action.TOOLS, after_action.spec())
        asked = after_action.ask(
            self.ledger, self.state.name, self.session.requests, closed=closed
        )
        self.session.say(asked)
        opening.append(asked)
        limits = self.session.limits
        # Its own cap on calls, and none on time: a run cut off by `model_time` is reviewed too.
        self.session.limits = replace(
            limits,
            calls=self.session.calls + self.limits.after_action_calls,
            model_seconds=float("inf"),
        )
        try:
            ending = self.session.go()
            # Near its limit, as `suncatcher-2`'s was after many compactions, the review goes on
            # in a new conversation from the record, as a run does: the index is from the
            # ledger, so it has the whole run (mqucifer/print-shop#40).
            while ending.name == "context_full" and self.state.restarts < self.limits.restarts:
                self.state.restarts += 1
                self._from_the_record(opening)
                ending = self.session.go()
        except KeyboardInterrupt:
            ending = self.session._ended(Ending("stopped", {"by": "signal"}))
        except Gone as gone:
            ending = self.session._ended(Ending("container_failed", {"error": str(gone)[:400]}))
        except Exception as exc:  # noqa: BLE001 - it ends named, and the decision stands
            fault = self.folder / f"after-action-fault-{n}.txt"
            fault.write_text(traceback.format_exc(), encoding="utf-8")
            fell = {"error": type(exc).__name__, "trace": fault.name}
            ending = self.session._ended(Ending("harness_fault", fell))
        finally:
            self.session.limits = limits
        self._after_action_ended(n, ending)
        return ending

    def after_action_not_begun(self, why: str) -> Ending:
        """A review that couldn't begin, such as with no key: said, and the decision stands."""
        decision, approval = self._reviewable()
        n, _ = self._after_action_begun(decision, approval)
        ending = Ending("not_begun", {"why": why[:400]})
        self._after_action_ended(n, ending)
        return ending

    def _reviewable(self) -> tuple[int, int | None]:
        """The decision the review follows, by its id in `decisions`, and its approval if it has
        one. An approval from before a decision's id was kept is found by its approval."""
        ending = self.state.ending or {}
        if ending.get("name") not in DECIDED:
            raise Refused("the after-action review comes after a person's approval or close")
        said = ending.get("said") or {}
        approval = int(said["approval"]) if said.get("approval") is not None else None
        decision = said.get("decision")
        if decision is None:
            found = self.ledger.rows(
                "SELECT id FROM decisions WHERE run=? AND approval IS ? AND decision=? "
                "ORDER BY id DESC LIMIT 1",
                self.state.name,
                approval,
                ending["name"],
            )
            if not found:
                raise Refused(f"the ledger has no decision for this run's {ending['name']} ending")
            decision = found[0]["id"]
        if self.ledger.rows(
            "SELECT 1 FROM after_actions WHERE run=? AND decision=? AND ended=?",
            self.state.name,
            int(decision),
            after_action.REVIEWED,
        ):
            raise Refused(
                f"this run was {ending['name']}, and its after-action review is in: "
                f"scripts/rounds.py lessons {self.state.name}"
            )
        return int(decision), approval

    def _after_action_begun(self, decision: int, approval: int | None) -> tuple[int, bool]:
        """Its row, and whether the decision has yet to reach the model: a try that didn't begin,
        with no key, never told it."""
        tried = self.ledger.rows(
            "SELECT n, decision, ended FROM after_actions WHERE run=? ORDER BY n", self.state.name
        )
        n = len(tried) + 1
        commit, dirty = repository_version()
        self.state.harness.append(f"{commit}{'+changes' if dirty else ''}")
        self.ledger.add(
            "after_actions",
            run=self.state.name,
            n=n,
            approval=approval,
            decision=decision,
            begun_at=now(),
            first_call=self.session.requests + 1,
            harness_commit=self.state.harness[-1],
        )
        self.session._record(
            "after_action",
            n=n,
            approval=approval,
            decision=decision,
            commit=self.state.harness[-1],
        )
        self.save()
        told = [t for t in tried if t["decision"] == decision and t["ended"] != "not_begun"]
        return n, not told

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
        # Its calls count, and the run's ending stays the person's decision.
        self.ledger.change(
            "UPDATE runs SET calls=?, usage=?, outcomes=? WHERE run=?",
            self.session.calls,
            json.dumps(dict(self.session.usage), sort_keys=True),
            json.dumps(dict(self.session.outcomes), sort_keys=True),
            self.state.name,
        )
        self.save()

    def after_action(self, args: dict[str, Any]) -> Judged:
        """The tool: refused before a person's approval or close, and held to the ledger after."""
        if (self.state.ending or {}).get("name") not in DECIDED:
            reply = "The after-action review comes after a person approves or closes the run."
            return Judged(after_action.NAME, "failed", reply, "not_decided")
        return after_action.take(self.ledger, self.state.name, args, self.session.requests)

    def send_back(self, by: str, note: str, reopen_needs: bool = False) -> None:
        """A person's no, with what's wrong, at the stop or by hand after approving it. It goes on
        as a new round. A pick on files that then change is asked again, as ever. With
        `reopen_needs`, the needs may be settled again: on `cup-1` a send-back couldn't add the
        check it found missing, and the model was refused when it tried."""
        stop = self.state.stop
        ending = (self.state.ending or {}).get("name")
        if ending == CLOSED:
            raise Refused(_closed(self.state.name))
        if not (stop and stop["kind"] == MET) and ending != APPROVED:
            raise Refused("this run isn't waiting for approval, and isn't approved")
        _who_decides(by, "--by")
        if not note.strip():
            raise Refused("a send-back needs its note: it's what the model goes on from")
        n = int(stop["n"]) if stop else self.state.approvals
        self.ledger.decided(self.state.name, n, "sent_back", by, note.strip())
        self._go_on()
        self.state.notes.append(note.strip())
        self.state.notes_from.append(by)
        message = (
            f"{picked_by_words(by)} looked at the final part and sent it back, with this note:\n\n"
            f"{note.strip()}"
        )
        self._relay("send_back", by, note.strip(), message)
        if reopen_needs:
            self._reopen(by, True)
        self._start_round()
        self.save()

    def _take_answers(self, n: int, who: str) -> None:
        """The answers to the model's questions, said as whoever gave them: on `eval-choice-1` a
        stand-in's reached the model as the Sponsor's, and its set-aside reasons then said "the
        Sponsor confirmed" (mqucifer/print-shop#53). So they say who, as a pick does."""
        path = self.folder / "questions" / f"{n}.answers.md"
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            raise Refused(f"the answers aren't written yet: {path}")
        if not _decides(who):
            raise Refused(
                f'the answers say who gave them: --from "{SPONSOR}" only if they did, or --from '
                f'"{STAND_IN} <who>" for anyone standing in. Not {who!r}'
            )
        answers = path.read_text(encoding="utf-8").strip()
        if who == SPONSOR:
            said = f"The Sponsor's answers:\n\n{answers}"
        else:
            said = f"Answers from {from_words(who)}:\n\n{answers}"
        self.state.said.append(said)
        self.ledger.answered(self.state.name, n, answers, who)
        self._relay("answers", who, answers, said)

    def _take_pick(self, n: int, reopening: bool = False) -> None:
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
        names = [v["name"] for v in asked["variants"]] or ["yes"]
        if pick not in [*names, "none"]:
            raise Refused(f"{path}: the pick is one of {', '.join(names)}, or none: not {pick!r}")
        if not reason:
            raise Refused(f"{path}: a pick needs its reason, which is what the ledger learns from")
        _who_decides(by, path)
        self.ledger.picked(self.state.name, n, pick, reason, by, fix_first)
        # Every eye need the ask named: one stop answers them all (mqucifer/print-shop#59).
        eyes = asked.get("needs") or ([asked["need"]] if asked.get("need") else [])
        for need in eyes:
            # A "none" takes back any pick before it: the person has just said it isn't this.
            self.state.picks.pop(need, None)
        if pick != "none" and eyes:
            seen = asked.get("seen") or records.seen_by_kept_files(folder)
            files = (seen.get("variants") or {}).get(pick) or {}
            for need in eyes:
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
        elif not asked["variants"]:
            said = f"{who} said yes to it. Their reason: {reason}"
        else:
            said = f"{who} picked {pick}. Their reason: {reason}"
        said += self._stop_picked(asked.get("stop"), n, pick, by, reopening)
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

    def _stop_picked(
        self, stop: str | None, ask: int, pick: str, by: str, reopening: bool = False
    ) -> str:
        """A pick passes the stop its ask showed. `none` leaves it open: what was shown wasn't
        right, so it's shown again before anything's handed over. A `none` at the plan reopens
        the needs, as the plan shows them (mqucifer/print-shop#45): on `eval-tip-1` the reason
        named a need's check, and the model was refused when it moved it. With `reopening`, the
        person reopened them by hand too, which says so itself."""
        form = self._form()
        line = form.stop(stop) if form is not None and stop else None
        if line is None:
            return ""
        if pick == "none":
            said = (
                f"\n\nThe stop `{line.id}`, {line.text}, is still open: show it again with `ask` "
                "when it's ready. `done` waits for it."
            )
            if line.id == forms.PLAN and not self.state.needs_open and not reopening:
                self.state.needs_open = True
                self.session._record("needs_reopened", by=by, at_ask=ask, why="plan_none")
                fixed = [f"`{n.id}`" for n in self._needs() if n.fixed]
                said += (
                    " The plan shows the needs, so they're open again: if the reason is about one, "
                    "send `needs` with the whole list, changed. If none should change, there's "
                    "nothing to send."
                )
                if fixed:
                    said += (
                        f" The fixed needs, {', '.join(fixed)}, stay as they are: a "
                        "non-negotiable's need changes only by the Sponsor, with a note."
                    )
            return said
        self.state.stops[line.id] = {
            "ask": ask,
            "pick": pick,
            "by": by,
            "at_call": self.session.requests,
        }
        after = self._open_stop()
        then = f" The next is `{after.id}`, {after.text}." if after is not None else ""
        return f"\n\nThe stop `{line.id}`, {line.text}, is passed.{then}"

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

    def _from_the_record(self, after: list[str] | None = None) -> None:
        """A conversation that wouldn't fit even compacted: a new one, from the record, and then
        `after`, what was said to it since, as the after-action review's decision and ask."""
        state = f"This is round {self.state.round}." if self.state.round else "This is intake."
        state = f"{state} {self._stops_said()}".strip()
        needs_json = json.dumps([n.plain() for n in self._needs()], indent=1)
        # A run from before this kept no sender, and those notes come first.
        older = [NOTE_FROM] * (len(self.state.notes) - len(self.state.notes_from))
        relayed = [
            f"A note from {from_words(who)}:\n\n{note}"
            for note, who in zip(self.state.notes, [*older, *self.state.notes_from], strict=True)
        ]
        checks = nudge.record(self._bench_tries(), self._wanted(), last=AGAIN_TRIES)
        text = prompt.again(
            self._start_text(), [*self.state.said, *relayed], needs_json, state, checks
        )
        self.session.begin(
            {"role": "system", "content": prompt.SYSTEM},
            {"role": "user", "content": text},
        )
        self._skills_given()
        for said in after or []:
            self.session.say(said)

    def _start_text(self) -> str:
        given = sorted(
            (p.name, p.stat().st_size)
            for p in (self.work / "references").iterdir()
            if p.is_file() and not p.is_symlink()
        )
        request = (self.folder / "request.md").read_text(encoding="utf-8")
        kept = self.folder / "lessons.md"
        given_lessons = kept.read_text(encoding="utf-8") if kept.is_file() else ""
        form = self._form()
        lines = forms.said(form) if form is not None else ""
        toolset = self.toolset
        if toolset.legacy:
            line = skills.start_line()
        else:
            asked = form.kind if form is not None else None
            line = f"{toolset.said(asked)} {skills.start_skills(toolset.skills, toolset.required)}"
        return prompt.start(request, given, line, given_lessons, lines)

    def _skills_given(self) -> None:
        """The required skills a conversation's start gives whole, for the ledger's `skill_reads`
        as given, not read (mqucifer/print-shop#61): at the run's start, and again at each new
        conversation from the record, which gives them again."""
        toolset = self.toolset
        if toolset.legacy or not toolset.required:
            return
        given = skills.given(toolset.skills, toolset.required)
        self.session._record(
            "skills_given",
            at_call=self.session.requests + 1,
            skills=[{"name": name, "file": file} for name, file in given],
        )

    @property
    def toolset(self) -> toolsets.Toolset:
        """The run's toolset: its kind's, the core with no kind, or every command from before."""
        return toolsets.of_run(self.state.kind, self.state.toolsets)

    def save(self) -> None:
        _write_atomic(self.folder / "state.json", json.dumps(asdict(self.state), indent=1))

    def _needs(self) -> list[Need]:
        """The needs as settled."""
        return needs.load(self.state.needs)

    def _needs_now(self, glass: list[glass_checks.Glass] | None = None) -> list[Need]:
        """The needs as they read the part now: a settled need's frame, panes and count follow
        the part as `glass` declares it, the latest unless given, and its figures stay as settled
        (mqucifer/print-shop#59)."""
        declared = glass if glass is not None else glass_checks.load(self.state.glass)
        return [needs.on_part(n, declared)[0] for n in self._needs()]

    def _followed_said(self, glass: list[glass_checks.Glass]) -> list[str]:
        """Each need whose files or count followed the part, in words, for a reply or a stop."""
        found = []
        for need in self._needs():
            _, said = needs.on_part(need, glass)
            if said:
                found.append(f"- `{need.id}`: {said}")
        return found

    def _form(self) -> forms.Form | None:
        return forms.load(self.state.form)

    def _open_stop(self) -> forms.Line | None:
        """The first of the request's stops whose ask hasn't had a pick: `done` and the next stop
        wait for it."""
        form = self._form()
        if form is None:
            return None
        return next((one for one in form.stops if one.id not in self.state.stops), None)

    def _stops_said(self) -> str:
        form = self._form()
        if form is None or not form.stops:
            return ""
        passed = [f"`{one.id}`" for one in form.stops if one.id in self.state.stops]
        open_stop = self._open_stop()
        said = f"Stops passed: {', '.join(passed) or 'none yet'}."
        if open_stop is not None:
            said += f" Open: `{open_stop.id}`, {open_stop.text}."
        return said

    # The tools that belong to the run

    def bench_said(
        self, argv: list[str], ran: Ran, record: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Kept in the ledger, and what its reply gains, if anything (`nudge.py`): that it's a
        need's check run with other arguments, and a nudge once the check has failed
        `nudge.AFTER` times without passing as a need checks it. It's counted from the ledger, so
        a run resumed on newer code counts what it did before."""
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
        self._seen()
        earlier = self._bench_tries()
        wanted = self._wanted()
        paths.write_inside(self.work, "record/CHECKS.md", nudge.record(earlier, wanted))
        if ran.code == nudge.USAGE:
            return None
        lines: list[str] = []
        detail: dict[str, Any] = {}
        off = nudge.off_need(argv, wanted)
        lead = nudge.not_the_requirement(off)
        if off:
            said_off = nudge.off_need_said(off)
            if said_off:
                lines.append(said_off)
            detail["off_need"] = off
        if lead:
            detail["not_the_requirement"] = [one["need"] for one in off if one.get("fixed")]
        check = nudge.check_of(argv)
        tries = nudge.streak(earlier, check, wanted)
        failed = nudge.failures(tries)
        failing = self._thin_failing()
        if failing:
            lines.append(unprintable.said(failing))
            detail["thin_failing"] = [one.file for one in failing]
        held = self._holding()
        if held:
            lines.append(holding.said(held))
            detail["held"] = holding.plain(held)
        if tries and failed >= nudge.AFTER:
            lines.append(nudge.said(check, tries, wanted))
            detail["nudge"] = {
                "command": check[0],
                "files": list(check[1]),
                "failures": failed,
                "after": nudge.AFTER,
                "calls": [one.at_call for one in tries],
            }
        if not lines and not lead:
            return None
        return {"said": "\n\n".join(lines), "detail": detail, "lead": lead}

    def breakdown(self, args: dict[str, Any]) -> Judged:
        """How the reference and the request are built, kept before the needs and shown at the
        first stop (mqucifer/print-shop#36)."""
        given = {k: args[k] for k in ("parts", "joins", "look", "from") if k in args}
        found, wrong = breakdown.read(given)
        if wrong:
            said = "\n".join(f"- {w}" for w in wrong)
            return Judged("breakdown", "failed", f"The breakdown wasn't kept:\n{said}", "misses")
        self.state.breakdowns += 1
        n = self.state.breakdowns
        (self.folder / "breakdowns").mkdir(exist_ok=True)
        (self.folder / "breakdowns" / f"{n}.json").write_text(
            json.dumps(found, indent=1) + "\n", encoding="utf-8"
        )
        text = breakdown.text(found)
        _write_atomic(self.folder / "breakdown.md", text)
        paths.write_inside(self.work, "record/breakdown.md", text)
        self.ledger.broken_down(self.state.name, n, self.session.requests, found)
        self._describe(descriptions.BREAKDOWN, f"breakdown {n}", text)
        self.save()
        reply = (
            f"Kept, as breakdown {n}: `record/breakdown.md` holds it, and the first stop shows it. "
            "Send it again if you find it's wrong."
        )
        return Judged("breakdown", "ok", reply, detail={"breakdown": n})

    # The model's descriptions of the part, each held to the version it was written about

    def _seen(self) -> dict[str, list[Any]]:
        """Each piece of geometry as it is now, and the call by which it was so: kept after every
        run and bench call, so a change is named by the call that made it."""
        now = descriptions.geometry(self.work, self.state.geometry)
        self.state.geometry = descriptions.seen(self.state.geometry, now, self.session.requests)
        at = self.session.requests
        for key in descriptions.stamp(self.state.described, self.state.geometry, at):
            one = self.state.described[key]
            self.ledger.described(
                self.state.name,
                {
                    "key": key,
                    "what": one["what"],
                    "how": descriptions.FIRST_VERSION,
                    "at_call": at,
                    "round": self.state.round,
                    "files": json.dumps(one["files"], sort_keys=True),
                },
            )
        return self.state.geometry

    def _describe(self, key: str, what: str, text: str, how: str = descriptions.WRITTEN) -> None:
        """A description written, with the part's files as they stand (mqucifer/print-shop#53)."""
        files = descriptions.hashes(self._seen())
        self.state.described[key] = {
            "what": what,
            "written_at": self.session.requests,
            "held_at": None,
            "files": files,
            "text": text,
        }
        self.ledger.described(
            self.state.name,
            {
                "key": key,
                "what": what,
                "how": how,
                "at_call": self.session.requests,
                "round": self.state.round,
                "files": json.dumps(files, sort_keys=True),
                "text": text,
            },
        )

    def _needs_described(self, found: list[Need]) -> None:
        """Each need's `what`, as it was settled: a need dropped isn't a description any more."""
        for key in [k for k in self.state.described if k.startswith(descriptions.NEED)]:
            del self.state.described[key]
        for need in found:
            self._describe(descriptions.need_key(need.id), _need_what(need.id), need.what)

    def _plan_described(self, plan: str, ask: int) -> None:
        """The plan, where it gives a figure: "the plan's figures". One without any isn't kept."""
        if descriptions.FIGURE.search(plan):
            self._describe(descriptions.PLAN, f"the plan, sent at ask {ask}", plan)
        else:
            self.state.described.pop(descriptions.PLAN, None)

    def _stale(self, part: list[str] | None) -> list[descriptions.Stale]:
        """Each description written before a file of the part changed: the files named, with the
        given references, or every piece of geometry where nothing names the part yet."""
        return descriptions.stale(
            self.state.described, self._seen(), descriptions.among(part)
        )

    def restate(self, args: dict[str, Any]) -> Judged:
        """A description sent again with new words, or said to still hold with a reason: what
        `done` asks of each stale one. It's shown to the person at the next stop."""
        entries, wrong = descriptions.read(args.get("descriptions"))
        kept = self.state.described
        if not kept and not wrong:
            wrong = [
                "No description is kept yet: `breakdown`, `needs` and the plan's `ask` keep them."
            ]
        tracked = self._seen()
        everything = {one.key: one for one in descriptions.stale(kept, tracked, None)}
        named = ", ".join(_described_name(k) for k in kept)
        plan: list[tuple[str, dict[str, Any]]] = []
        unchanged: list[str] = []
        for entry in entries:
            key = _description_key(entry["of"])
            if key not in kept:
                wrong.append(f"`{entry['of']}` isn't a description kept. They are: {named}.")
                continue
            if key == descriptions.BREAKDOWN and entry["now"]:
                wrong.append("The breakdown is sent again with `breakdown`, whole.")
                continue
            if entry["holds"] and key not in everything:
                # Nothing to say holds: kept as it was, and said, rather than refusing the rest.
                one = kept[key]
                since = one["held_at"] or one.get("stamped_at") or one["written_at"]
                unchanged.append(
                    f"{one['what']}: no part file has changed since call {since}, so it's kept "
                    "as it was"
                )
                continue
            plan.append((key, entry))
        if wrong:
            said = "\n".join(f"- {w}" for w in dict.fromkeys(wrong))
            return Judged("restate", "failed", f"Nothing was kept:\n{said}", "misses")
        files = descriptions.hashes(tracked)
        done: list[str] = []
        for key, entry in plan:
            one = kept[key]
            stale = everything.get(key)
            changed = [list(c) for c in stale.changed] if stale else []
            row: dict[str, Any] = {
                "key": key,
                "what": one["what"],
                "at_call": self.session.requests,
                "round": self.state.round,
                "changed": changed,
                "shown_at": None,
            }
            if entry["holds"]:
                one["held_at"], one["files"] = self.session.requests, files
                row.update(how=descriptions.HOLDS, why=entry["holds"])
                done.append(f"{one['what']}: said to still hold")
            else:
                was = str(one.get("text") or "")
                if key == descriptions.PLAN:
                    what = f"the plan, sent again at call {self.session.requests}"
                else:
                    self._need_what_now(key.removeprefix(descriptions.NEED), entry["now"])
                    what = one["what"]
                one.update(
                    what=what,
                    written_at=self.session.requests,
                    held_at=None,
                    files=files,
                    text=entry["now"],
                )
                row.update(how=descriptions.SENT_AGAIN, was=was, now=entry["now"])
                done.append(f"{what}: sent again")
            self.state.restated.append(row)
            self.ledger.described(
                self.state.name,
                {
                    "key": key,
                    "what": row["what"],
                    "how": row["how"],
                    "at_call": row["at_call"],
                    "round": row["round"],
                    "files": json.dumps(files, sort_keys=True),
                    "text": row.get("now"),
                    "was": row.get("was"),
                    "why": row.get("why"),
                    "changed": json.dumps(changed),
                },
            )
        self.save()
        left = self._stale(self.state.part)
        reply = (
            "Kept, and the person at the next stop reads each:\n"
            + "\n".join(f"- {one}" for one in done)
            if done
            else "Nothing was kept."
        )
        if unchanged:
            reply += "\n" + "\n".join(f"- {one}" for one in unchanged)
        if left:
            reply += "\n\nStill written before the part changed:\n" + "\n".join(
                descriptions.lines(left)
            )
        return Judged("restate", "ok", reply, detail={"restated": [k for k, _ in plan]})

    def _need_what_now(self, nid: str, what: str) -> None:
        """A need's `what` in new words. Its check, its quote and its marks stay as settled: only
        the model's description of it changes, and the needs' files say it as it is now."""
        for entry in self.state.needs:
            if entry["id"] == nid:
                entry["what"] = what
        text = json.dumps([n.plain() for n in self._needs()], indent=1) + "\n"
        _write_atomic(self.folder / "needs.json", text)
        paths.write_inside(self.work, "record/needs.json", text)

    def _restated_shown(self, where: str) -> None:
        for one in self.state.restated:
            if not one.get("shown_at"):
                one["shown_at"] = where
        self.ledger.change(
            "UPDATE descriptions SET shown_at=? WHERE run=? AND shown_at IS NULL AND how IN (?, ?)",
            where,
            self.state.name,
            descriptions.HOLDS,
            descriptions.SENT_AGAIN,
        )

    def _wanted(self) -> list[nudge.Wanted]:
        """The settled needs that run a bench check."""
        return [
            nudge.Wanted(n.id, n.command, n.argv(), n.fixed, n.quote)
            for n in self._needs_now()
            if n.by == "bench" and n.command
        ]

    def _bench_tries(self) -> list[nudge.Try]:
        """Every bench call of this run, oldest first, as the ledger keeps it."""
        return [
            nudge.Try(
                int(r["at_call"]), json.loads(r["argv"]), int(r["exit"]), r["verdict"], r["why"]
            )
            for r in self.ledger.rows(
                "SELECT at_call, argv, exit, verdict, why FROM bench_calls WHERE run=? ORDER BY id",
                self.state.name,
            )
        ]

    def _thin_failing(self) -> list[unprintable.Thin]:
        """Each part file whose last `thin` failed, the model's or the harness's, and hasn't left
        the part (`unprintable.py`)."""
        readings: list[tuple[int, int, unprintable.Thin]] = []
        for row in self.ledger.rows(
            "SELECT id, at_call, argv, verdict, why FROM bench_calls WHERE run=? ORDER BY id",
            self.state.name,
        ):
            name = unprintable.counted(json.loads(row["argv"]))
            if name is not None:
                thin = unprintable.Thin(
                    int(row["at_call"]), name, str(row["verdict"] or ""), str(row["why"] or "")
                )
                readings.append((thin.at_call, 0, thin))
        for row in self.ledger.rows(
            "SELECT id, at_call, stop, file, verdict, why FROM standing WHERE run=? "
            "AND check_name=? ORDER BY id",
            self.state.name,
            judge.PRINTABLE,
        ):
            thin = unprintable.Thin(
                int(row["at_call"]),
                str(row["file"]),
                str(row["verdict"] or ""),
                str(row["why"] or ""),
                str(row["stop"]),
            )
            readings.append((thin.at_call, 1, thin))
        readings.sort(key=lambda r: (r[0], r[1]))
        present = {str(p) for p in paths.walk_plain(self.work, skip_made=True)}
        return unprintable.failing(
            [r[2] for r in readings], present, self.state.part, self.state.part_at
        )

    def standing_said(self) -> str:
        """What a `run` reply gains: each part file whose last `thin` failed, and each check whose
        latest try failed (`holding.py`). The geometry a run made is seen here, by its call."""
        self._seen()
        said = [unprintable.said(self._thin_failing()), holding.said(self._holding())]
        return "\n\n".join(one for one in said if one)

    def failing_said(self) -> str:
        """The message after a compaction: each check whose latest try fails, one or two lines
        each, from the tries `record/CHECKS.md` keeps (mqucifer/print-shop#45). Empty when none
        does. Session calls it through `after_compaction`."""
        return holding.after_compaction(self._holding(), self._thin_failing())

    def _held_tries(self) -> list[holding.Tried]:
        """Every reading of a check, oldest first: the model's bench calls, each need's own
        command read against its limit, and the harness's readings of the needs at `done` and at
        an ask. `thin` at the harness's settings is `unprintable.py`'s."""
        found: list[tuple[int, int, holding.Tried]] = []
        wanted = self._needs_now()
        for row in self.ledger.rows(
            "SELECT at_call, argv, exit, verdict, why, numbers FROM bench_calls WHERE run=? "
            "ORDER BY id",
            self.state.name,
        ):
            argv = json.loads(row["argv"])
            code = int(row["exit"])
            if code == nudge.USAGE or unprintable.counted(argv) is not None:
                continue
            verdict, why = str(row["verdict"] or ""), str(row["why"] or "")
            passed: bool | None = {0: True, 1: False}.get(code)
            check = nudge.check_of(argv)
            for need in wanted:
                if (
                    need.by == "bench"
                    and need.command
                    and nudge.check_of(need.argv()) == check
                    and not nudge.differences(argv, need.argv())
                ):
                    record = {
                        "verdict": verdict,
                        "why": why,
                        "numbers": json.loads(row["numbers"]) if row["numbers"] else {},
                    }
                    reading = needs.judge(need, code, f"verdict: {verdict}: {why}", record)
                    unsure = not verdict or verdict in UNSURE
                    passed = reading.passed or (None if unsure else False)
                    if not reading.passed and not unsure:
                        why = reading.why
                    break
            tried = holding.Tried(int(row["at_call"]), argv, passed, verdict, why)
            found.append((tried.at_call, 0, tried))
        for row in self.ledger.rows(
            "SELECT at_call, command, passed, unsure, verdict, why FROM standing WHERE run=? "
            "AND stop='ask' AND check_name LIKE 'need %' ORDER BY id",
            self.state.name,
        ):
            passed = None if row["unsure"] else bool(row["passed"])
            tried = holding.Tried(
                int(row["at_call"]),
                shlex.split(str(row["command"])),
                passed,
                str(row["verdict"] or ""),
                str(row["why"] or ""),
                "ask",
            )
            found.append((tried.at_call, 1, tried))
        for row in self.ledger.rows(
            "SELECT at_call, stop, command, passed, unsure, verdict, why FROM standing WHERE run=? "
            "AND check_name IN (?, ?) ORDER BY id",
            self.state.name,
            glass_checks.LIGHT,
            glass_checks.HOLD,
        ):
            # The kind's own, at `done` and each stop (`standing.py`): held as the model's are.
            tried = holding.Tried(
                int(row["at_call"]),
                shlex.split(str(row["command"])),
                None if row["unsure"] else bool(row["passed"]),
                str(row["verdict"] or ""),
                str(row["why"] or ""),
                str(row["stop"]),
            )
            found.append((tried.at_call, 1, tried))
        for row in self.ledger.rows(
            "SELECT r.need, r.round, r.passed, r.verdict, r.why, o.last_call, o.needs_version FROM "
            "readings "
            "AS r JOIN rounds AS o ON o.run = r.run AND o.round = r.round WHERE r.run=? AND "
            "r.by='bench' AND o.last_call IS NOT NULL ORDER BY o.round",
            self.state.name,
        ):
            command = self._need_command(
                int(row["needs_version"]), str(row["need"]), int(row["round"])
            )
            if not command:
                continue
            unsure = not row["passed"] and (not row["verdict"] or row["verdict"] in UNSURE)
            tried = holding.Tried(
                int(row["last_call"]),
                shlex.split(command),
                None if unsure else bool(row["passed"]),
                str(row["verdict"] or ""),
                str(row["why"] or ""),
                "done",
            )
            found.append((tried.at_call, 1, tried))
        found.sort(key=lambda r: (r[0], r[1]))
        return [r[2] for r in found]

    def _need_command(self, version: int, need: str, round_n: int | None = None) -> str:
        """A need's command as `done` ran it in that round, its files following the part
        (mqucifer/print-shop#59), or as a version of the needs had it."""
        ran = self.folder / "rounds" / f"{round_n or 0:03}" / "followed.json"
        if round_n is not None and ran.is_file():
            with contextlib.suppress(ValueError, OSError):
                found = json.loads(ran.read_text(encoding="utf-8")).get(need)
                if found:
                    return str(found)
        kept = self.folder / "needs" / f"{version}.json"
        if not kept.is_file():
            return ""
        for entry in json.loads(kept.read_text(encoding="utf-8")):
            if entry.get("id") == need:
                return str((entry.get("check") or {}).get("command") or entry.get("command") or "")
        return ""

    def _holding(self) -> list[holding.Held]:
        """Each check whose failure is still open (`holding.py`)."""
        present = {str(p) for p in paths.walk_plain(self.work, skip_made=True)}
        asides = [
            holding.Aside(int(a["at_call"]), (str(a["command"]), tuple(a["files"])), str(a["why"]))
            for a in self.state.asides
        ]
        return holding.failing(
            self._held_tries(),
            self._wanted(),
            present,
            self.state.part,
            self.state.part_at,
            asides,
        )

    def set_aside(self, args: dict[str, Any]) -> Judged:
        """A failing check the model says doesn't apply, with its reason: out of the list until it
        fails again, and shown to the person at the next stop. A check a settled need runs is
        `done`'s to decide, so it can't be set aside."""
        why = str(args.get("why") or "").strip()
        if args.get("curves") is not None:
            return self._set_aside_curves(str(args["curves"]).strip(), why, args)
        if args.get("file") is not None:
            return self._set_aside_file(str(args["file"]).strip(), why, args.get("check"))
        try:
            argv = shlex.split(str(args.get("check") or ""))
        except ValueError as exc:
            return Judged("set_aside", "failed", f"`check` can't be read: {exc}", "not_a_command")
        check = nudge.check_of(argv)
        held = self._holding()
        mine = [h for h in held if h.check == check]
        if not why:
            reply = "`why` says why the check doesn't apply, for the person at the next stop."
            return Judged("set_aside", "failed", reply, "empty")
        if not mine:
            named = "; ".join(
                f"`{h.check[0]}{''.join(' ' + f for f in h.check[1])}`" for h in held
            )
            reply = (
                "That isn't a check whose latest try fails, by its command and files. "
                + (f"The checks that fail: {named}." if named else "No check fails now.")
            )
            return Judged("set_aside", "failed", reply, "not_failing")
        if mine[0].needs:
            named = ", ".join(f"`{w.need}`" for w in mine[0].needs)
            reply = (
                f"{named} runs this check, so `done` decides it, and it can't be set aside. If the "
                f"need's check is wrong: {prompt.STUCK}"
            )
            return Judged("set_aside", "failed", reply, "a_needs_check")
        own = ", ".join(f"`{c}`" for c in self.toolset.standing)
        # A given file is the "before" state only where the piece doesn't print it: a frame given
        # and printed as it is, named in `glass`, is the part.
        printed = {
            _plain_name(f) for one in glass_checks.load(self.state.glass) for f in one.files()
        }
        if check[0] in self.toolset.standing and not all(
            _plain_name(f).startswith("references/") and _plain_name(f) not in printed
            for f in check[1]
        ):
            reply = (
                f"`{check[0]}` is one of {self.toolset.title}'s own checks, which every piece of "
                f"this kind passes whatever the request's lines say: {own}. "
                "The harness runs them on every pane at `done` and at each stop, so they can't be "
                'set aside. On a given reference the piece doesn\'t print, as the "before" state, '
                f"one can. If the check reads the part wrongly: {prompt.STUCK}"
            )
            return Judged("set_aside", "failed", reply, "standing")
        aside = {
            "at_call": self.session.requests,
            "round": self.state.round,
            "command": check[0],
            "files": list(check[1]),
            "tries": [one.at_call for one in mine[0].tries],
            "why": why,
            "shown_at": None,
        }
        self.state.asides.append(aside)
        self.ledger.set_aside(self.state.name, len(self.state.asides), aside)
        self.save()
        reply = (
            "Set aside: it isn't named in your replies until it fails again. The person at the "
            "next stop reads it, with your reason."
        )
        return Judged("set_aside", "ok", reply, detail={"set_aside": aside})

    def _set_aside_curves(self, name: str, why: str, args: dict[str, Any]) -> Judged:
        """A part file whose small flat faces aren't curves stored as strips, by the model's
        reason: `done` and the stops then pass it, and the next stop shows why
        (mqucifer/print-shop#59)."""
        if args.get("check") or args.get("file"):
            reply = "`set_aside` takes one of `check`, `file` and `curves`."
            return Judged("set_aside", "failed", reply, "both")
        if not why:
            reply = "`why` says why the file's small flat faces aren't curves, for the next stop."
            return Judged("set_aside", "failed", reply, "empty")
        plain = _plain_name(name)
        latest = self.ledger.rows(
            "SELECT passed, why FROM standing WHERE run=? AND check_name=? AND file=? "
            "ORDER BY id DESC LIMIT 1",
            self.state.name,
            glass_checks.CURVES,
            plain,
        )
        if not latest or latest[0]["passed"]:
            reply = (
                f"`{name}` hasn't failed the harness's reading of curves stored as strips, at "
                "`done` or a stop, so there's nothing to set aside."
            )
            return Judged("set_aside", "failed", reply, "not_failing")
        aside = {
            "at_call": self.session.requests,
            "round": self.state.round,
            "command": "info",
            "files": [plain],
            "tries": [],
            "why": why,
            "strips_file": plain,
            "shown_at": None,
        }
        self.state.asides = [a for a in self.state.asides if a.get("strips_file") != plain]
        self.state.asides.append(aside)
        self.ledger.set_aside(self.state.name, len(self.state.asides), aside)
        self.save()
        reply = (
            f"Set aside: `done` and the stops pass `{plain}`'s small flat faces with your reason, "
            "which the person at the next stop reads."
        )
        return Judged("set_aside", "ok", reply, detail={"set_aside": aside})

    def _set_aside_file(self, name: str, why: str, check: Any) -> Judged:
        """A part file the model says no need has to read, and why (mqucifer/print-shop#52): out
        of `done`'s refusal, and shown at the next stop."""
        if check:
            reply = "`set_aside` takes a `check` or a `file`, not both."
            return Judged("set_aside", "failed", reply, "both")
        if not why:
            reply = "`why` says why no need reads the file, for the person at the next stop."
            return Judged("set_aside", "failed", reply, "empty")
        plain = _plain_name(name)
        if plain not in {_plain_name(f) for f in self.state.part or []}:
            named = ", ".join(f"`{f}`" for f in self.state.part or []) or "none yet"
            reply = (
                f"`{name}` isn't a file of the part `done` last named ({named}): a `file` set "
                "aside is a part file no need reads."
            )
            return Judged("set_aside", "failed", reply, "not_in_part")
        if plain not in self._unread(self.state.part or []):
            reply = f"A need already reads `{name}`, so there's nothing to set aside."
            return Judged("set_aside", "failed", reply, "read")
        aside = {
            "at_call": self.session.requests,
            "round": self.state.round,
            "command": "",
            "files": [plain],
            "tries": [],
            "why": why,
            "unread_file": plain,
            "shown_at": None,
        }
        self.state.asides.append(aside)
        self.ledger.set_aside(self.state.name, len(self.state.asides), aside)
        self.save()
        reply = (
            f"Set aside: `done` no longer asks for a need to read `{plain}`. The person at the "
            "next stop reads your reason."
        )
        return Judged("set_aside", "ok", reply, detail={"set_aside": aside})

    def _asides_said(self) -> list[str]:
        """The checks set aside since the last stop, and the part files no need reads, for the
        person there."""
        shown = [a for a in self.state.asides if not a.get("shown_at")]
        if not shown:
            return []
        files = [a for a in shown if a.get("unread_file")]
        strips = [a for a in shown if a.get("strips_file")]
        shown = [a for a in shown if not a.get("unread_file") and not a.get("strips_file")]
        lines: list[str] = []
        if strips:
            lines += [
                "## Small flat faces the model says aren't curves stored as strips",
                "",
                f"Each file has {glass_checks.STRIPS_AT} or more flat faces under 1 mm across, "
                "which is how a curve built from a polygon is stored. The model says why these "
                "aren't:",
                "",
                *[
                    f"- `{a['strips_file']}`, set aside at call {a['at_call']}: {a['why']}"
                    for a in strips
                ],
                "",
            ]
        if files:
            lines += [
                "## Part files no need reads, set aside by the model",
                "",
                "`thin` reads every file of the part; no need's check reads these.",
                "",
            ]
            lines += [
                f"- `{a['unread_file']}`, set aside at call {a['at_call']}: {a['why']}"
                for a in files
            ]
            lines.append("")
        if not shown:
            return lines
        lines += ["## Failing checks the model set aside", ""]
        for aside in shown:
            on = " ".join(aside["files"])
            calls = ", ".join(str(c) for c in aside["tries"])
            lines.append(
                f"- `{aside['command']} {on}`, failed at call {calls}, set aside at call "
                f"{aside['at_call']}: {aside['why']}"
            )
        return [*lines, ""]

    def _on_replaced(self, printed: list[str]) -> list[tuple[str, str, str, str]]:
        """Each bench need checked on a given reference that the part replaces, by its name: on
        `eval-through-1` the fit need ran on `references/frame.step` while the part was the
        model's changed `frame.step`. Each is its need, the reference, the part's file and its
        command. A check on the reference, as the "before" state, can still run: it isn't a
        need."""
        mine: dict[str, str] = {}
        for name in printed:
            plain = _plain_name(name)
            if not plain.startswith("references/"):
                mine.setdefault(Path(plain).name, plain)
        found = []
        for need in self._needs_now():
            if need.by != "bench":
                continue
            for name in need.files():
                plain = _plain_name(name)
                if plain.startswith("references/") and Path(plain).name in mine:
                    found.append((need.id, name, mine[Path(plain).name], need.command))
        return found

    def _unread(self, printed: list[str]) -> list[str]:
        """Each file of the part no need's check reads, beyond `thin`'s, and not set aside: on
        `eval-tip-2` the pane's fit need was `panes` on the frame, which builds its own pane and
        never opens `pane.step` (mqucifer/print-shop#52). A run from before toolsets carries on
        as it began."""
        if self.toolset.legacy:
            return []
        # `thin` reads every file whatever the needs say, so a need that runs it reads nothing more,
        # unless it asks for one body: on `eval-tip-2` `pane.step` was read by `thin` alone.
        read = {
            _plain_name(f)
            for n in self._needs_now()
            if n.by == "bench" and (n.argv()[:1] != ["thin"] or "--one-body" in n.argv())
            for f in n.files()
        }
        aside = {str(a["unread_file"]) for a in self.state.asides if a.get("unread_file")}
        found = []
        for name in printed:
            plain = _plain_name(name)
            if plain not in read and plain not in aside and plain not in found:
                found.append(plain)
        return found

    def _part_reading_said(self, draft: dict[str, Any]) -> list[str]:
        """Beside a question: needs on a reference the part replaces, and part files no need
        reads, as `done` would refuse them."""
        lines: list[str] = []
        replaced = draft.get("on_replaced") or []
        if replaced:
            lines += [
                "## Needs checked on a reference the part replaces",
                "",
                "A need is checked on the part. Where the part has its own copy of a given file, "
                "a check on the given one is the \"before\" state, not a need: `done` refuses "
                "these until the needs change, which takes `--reopen-needs`.",
                "",
                *[
                    f"- `{o['need']}`: `{o['file']}`, in `{o['command']}`, where the part has "
                    f"`{o['part_file']}`"
                    for o in replaced
                ],
                "",
            ]
        unread = draft.get("unread") or []
        if unread:
            lines += [
                "## Files no need reads",
                "",
                "`done` asks for each file of the part to be read by a need's check, beyond "
                "`thin`. These, on the sheet or in the part `done` last named, aren't: a need is "
                "added through `--reopen-needs`, or the model sets the file aside with its reason.",
                "",
                *[f"- `{name}`" for name in unread],
                "",
            ]
        return lines

    def _off_part(self, printed: list[str]) -> list[tuple[str, str, str]]:
        """Each bench need whose check names a file that's neither printed nor given: on
        `eval-tip-1` the fit need ran `clearance` on a pocket the model modelled, and passed while
        the frame had almost no lip at the tip. Each is its need, the file and its command."""
        kept = {_plain_name(name) for name in printed}
        found = []
        for need in self._needs_now():
            if need.by != "bench":
                continue
            for name in need.files():
                plain = _plain_name(name)
                if plain not in kept and not plain.startswith("references/"):
                    found.append((need.id, name, need.command))
        return found

    def _needs_refused_said(self) -> list[str]:
        """Each `needs` call refused since the last stop, for the person there: on `eval-tip-1`
        the model tried to move a need onto the frame, was refused, and its ask didn't say so."""
        if not self.state.needs_refused:
            return []
        lines = ["## `needs` calls refused since the last stop", ""]
        for one in self.state.needs_refused:
            lines.append(f"- call {one['at_call']}, refused ({one['why']}): {one['said']}")
            lines += [f"  - {change}" for change in one["changes"]]
        lines += [
            "",
            "If one of these should go ahead, `resume --reopen-needs` with the pick opens them.",
            "",
        ]
        return lines

    def _refused_needs(self, args: dict[str, Any], judged: Judged) -> Judged:
        """A `needs` call refused after the needs were first settled, kept for the next question:
        what it would have changed, need by need."""
        if not self.state.needs_version or args.get("try") is True:
            return judged
        settled = {n.id: n for n in self._needs()}
        sent = args.get("needs")
        if isinstance(sent, str):
            try:
                sent = json.loads(sent)
            except ValueError:
                sent = None
        changes: list[str] = []
        named: set[str] = set()
        for entry in sent if isinstance(sent, list) else []:
            if not isinstance(entry, dict):
                continue
            nid = str(entry.get("id"))
            named.add(nid)
            given = entry.get("check")
            check: dict[str, Any] = given if isinstance(given, dict) else {}
            command = str(check.get("command") or "").strip().removeprefix("bench ").strip()
            old = settled.get(nid)
            if old is None:
                changes.append(f"adds `{nid}`" + (f", checked by `{command}`" if command else ""))
            elif command and command != old.command:
                changes.append(f"`{nid}` from `{old.command}` to `{command}`")
        changes += [f"drops `{nid}`" for nid in settled if sent and nid not in named]
        first = judged.reply.splitlines()[0] if judged.reply else ""
        self.state.needs_refused.append(
            {
                "at_call": self.session.requests,
                "why": judged.why,
                "said": first[:300],
                "changes": changes or ["no check changed"],
            }
        )
        self.save()
        return judged

    def _asides_shown(self, where: str) -> None:
        for aside in self.state.asides:
            if not aside.get("shown_at"):
                aside["shown_at"] = where
        self.ledger.change(
            "UPDATE set_asides SET shown_at=? WHERE run=? AND shown_at IS NULL",
            where,
            self.state.name,
        )

    def questions(self, args: dict[str, Any]) -> Judged:
        if not self.state.needs_open:
            reply = (
                "The needs are settled, so this doesn't stop for the Sponsor. Form goes through "
                f"`ask`, and the rest is yours to decide. If you're stuck: {prompt.STUCK}"
            )
            return Judged("questions", "failed", reply, "needs_settled")
        text = args["questions"].strip()
        if not text:
            return Judged("questions", "failed", "`questions` is empty.", "empty")
        pictures = [str(p) for p in args.get("pictures") or []]
        shown: list[tuple[str, str]] = []
        for path in pictures:
            if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg"):
                reply = f"`pictures` are PNG or JPEG files you made, and `{path}` isn't one."
                return Judged("questions", "failed", reply, "not_a_picture")
            seen_picture = self.tools.call("read", {"path": path})
            if seen_picture.outcome != "ok":
                return Judged("questions", "failed", seen_picture.reply, seen_picture.why)
            shown += seen_picture.images
        self.state.questions += 1
        n = self.state.questions
        folder = self.folder / "questions"
        folder.mkdir(exist_ok=True)
        kept, said = self._question_pictures(n, pictures)
        lines = [text, ""]
        if kept:
            lines += ["## Pictures", "", *[f"- {path}" for path in kept], ""]
        (folder / f"{n}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        self.ledger.question(self.state.name, n, text, self.session.requests)
        self.state.stop = {"kind": "questions", "n": n}
        self.state.said.append(f"You asked the Sponsor:\n\n{text}")
        self.save()
        # Who answers isn't known until they do: a stand-in's answers say so, and the reply
        # doesn't name the Sponsor before then (mqucifer/print-shop#59).
        reply = (
            "Your questions have gone out, to the Sponsor or someone standing in for them. The "
            "answers come in your next message, saying who gave them."
        )
        if said:
            reply += f" {said}"
        images = shown or [(f"questions/{n}/{Path(p).name}", self._b64(p)) for p in kept]
        return Judged(
            "questions",
            "ok",
            reply,
            images=images,
            detail={"pictures": len(kept)},
            ending=Ending("questions", {"n": n}),
        )

    def _b64(self, path: Path) -> str:
        return base64.b64encode(path.read_bytes()).decode()

    def _question_pictures(self, n: int, pictures: list[str]) -> tuple[list[Path], str]:
        """What goes with a question (mqucifer/print-shop#54): on `eval-choice-1` a question about
        a cell's size went with no picture, as the request gave only STEP files. The model's own
        pictures go as they are. With none, the harness draws a sheet of the given geometry, one
        row of every STEP or STL under `references/`, so the person sees what's asked about. It's
        what they see, and the model is shown it too."""
        folder = self.folder / "questions" / str(n)
        if pictures:
            folder.mkdir(parents=True, exist_ok=True)
            kept = []
            for path in pictures:
                target = folder / Path(path).name
                target.write_bytes(paths.read_bytes_inside(self.work, path))
                kept.append(target)
            return kept, "Your pictures went with them, as they are."
        given = sorted(
            str(found.relative_to(self.work))
            for found in (self.work / "references").glob("*")
            if found.is_file() and not found.is_symlink() and found.name.lower().endswith(GEOMETRY)
        )
        if not given:
            return [], ""
        out = f".pictures/questions-{n}"
        command = ["render", "--variant", f"given={','.join(given)}"]
        command += ["--views", *ASK_VIEWS, "--size", ASK_SIZE, "--renders-out", out]
        done = self.box.bench(command)
        record = recorded(done.output, self.work)
        sheets = [str(f) for f in (record or {}).get("files") or [] if str(f).endswith(".png")]
        if done.code != 0 or not sheets:
            return [], "The harness couldn't draw the given files, so they went with no picture."
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "given-sheet.png"
        target.write_bytes(paths.read_bytes_inside(self.work, sheets[-1]))
        named = ", ".join(f"`{name}`" for name in given)
        return [target], f"With them went the harness's sheet of the given files, above: {named}."

    def needs(self, args: dict[str, Any]) -> Judged:
        """The needs, settled or tried. One refused once they were first settled goes beside the
        next question, so a person sees what the model tried to change."""
        judged = self._settle(args)
        if judged.outcome == "failed":
            return self._refused_needs(args, judged)
        return judged

    def _settle(self, args: dict[str, Any]) -> Judged:
        if not self.state.needs_open:
            reply = (
                "The needs are settled: they're what your work is checked against, so they don't "
                f"change from here. If one can't be met or its check is wrong: {prompt.STUCK} "
                "This refusal, with what it would have changed, goes beside your next question, "
                "so the person there sees it."
            )
            return Judged("needs", "failed", reply, "needs_settled")
        trying = args.get("try") is True
        form = self._form()
        if form is not None and not trying and not self.state.breakdowns:
            reply = (
                "The needs weren't taken: write down how it's built with `breakdown` first, the "
                "parts, what holds or connects what, and what the look rests on. The needs come "
                "from that."
            )
            return Judged("needs", "failed", reply, "no_breakdown")
        request = (self.folder / "request.md").read_text(encoding="utf-8")
        found, wrong = needs.read(
            args["needs"],
            "\n".join([request, *self.state.said]),
            "\n".join(self.state.notes),
            self.toolset,
            picks=self._picks_said(),
        )
        if not wrong and not trying:
            wrong = needs.numbers_wrong(found) + needs.verdicts_wrong(found)
            wrong += needs.echoes_wrong(found)
        if not wrong and form is not None and not trying:
            wrong = forms.held(found, form) + figures.wrong(found, form)
            wrong += figures.echoed(found, form)
            wrong += self._fixed_kept(found)
        if not wrong:
            wrong = self._usable(found)
        if wrong:
            said = "\n".join(f"- {w}" for w in wrong)
            return Judged("needs", "failed", f"The needs weren't taken:\n{said}", "misses")
        if trying:
            return self._try_needs(found)
        why_changed = args.get("why_changed") or {}
        declared = glass_checks.load(self.state.glass)
        swaps, refused = self._swaps(
            self._needs_now(declared), [needs.on_part(n, declared)[0] for n in found], why_changed
        )
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
        self.ledger.needs_marked(self.state.name, version, found)
        self._needs_described(found)
        self.state.fixed_open = None
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

    def _picks_said(self) -> str:
        """Each pick's reason and fix-first, as written in its `pick.json`: what a need with
        `source` `pick` quotes (mqucifer/print-shop#54)."""
        said = []
        for path in sorted((self.folder / "asks").glob("*/pick.json")):
            with contextlib.suppress(ValueError, OSError):
                found = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(found, dict):
                    said += [str(found.get(k) or "") for k in ("reason", "fix_first")]
        return "\n".join(one for one in said if one.strip())

    def _fixed_kept(self, found: list[Need]) -> list[str]:
        """Each fixed need settled before, kept as it was: its check, its words and its mark. Only
        the Sponsor changes one, with a note (`_reopen`)."""
        if self.state.fixed_open:
            return []
        # Its files and pane count follow the part, so a list of the part's own files in their
        # roles is the same check (mqucifer/print-shop#59); its figures aren't.
        declared = glass_checks.load(self.state.glass)
        kept = {n.id: needs.on_part(n, declared)[0] for n in found}
        wrong = []
        for old in self._needs_now(declared):
            new = kept.get(old.id)
            if not old.fixed or (
                new is not None
                and new.fixed
                and _same_check(old, new)
                and new.quote == old.quote
                and new.covers == old.covers
                and new.figure == old.figure
            ):
                continue
            check = old.command or f"the Sponsor's eye: {old.look_for}"
            wrong.append(
                f"`{old.id}` is fixed, a non-negotiable of the request, and changes only by the "
                f"Sponsor, through `resume --reopen-needs` with their note. Send it as it was: "
                f"`{check}`" + (f" ({old.limit()})" if old.by == "bench" else "")
            )
        return wrong

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
        open_stop = self._open_stop()
        if open_stop is not None:
            reply = (
                f"`done` waits for the stop `{open_stop.id}`, {open_stop.text}: until its `ask` "
                f'has a pick, nothing is handed over. Show it with `ask`, `"stop": '
                f'"{open_stop.id}"`. Writing, running and the bench are open meanwhile.'
            )
            return Judged("done", "failed", reply, "stop_open")
        missing = self.tools._existing("done", args, "script")
        if missing is not None:
            return missing
        rows = self._part(args["part"])
        if isinstance(rows, Judged):
            return rows
        part = list(dict.fromkeys(name for row in rows for name in row["files"]))
        self.state.part, self.state.part_at = part, self.session.requests
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
        # The frames and panes first: a settled need's files and counts follow them
        # (mqucifer/print-shop#59), so what's off the part is read against the part as it is.
        declared = self._glass_declared(args.get("glass"), part, "done")
        off = self._off_part(part)
        if off:
            return Judged("done", "failed", _off_part_refused(off), "need_off_part")
        replaced = self._on_replaced(part)
        if replaced:
            return Judged("done", "failed", _on_replaced_refused(replaced), "need_on_replaced")
        unread = self._unread(part)
        if unread:
            return Judged("done", "failed", _unread_refused(unread), "part_unread")
        if isinstance(declared, Judged):
            return declared
        glass = declared
        stale = self._stale(part)
        if stale:
            # A description of the part as it was: sent again or said to hold before anything is
            # checked (mqucifer/print-shop#53).
            self.ledger.stale_found(self.state.name, self.session.requests, "done", stale)
            self.save()
            said = {"stale": [one.plain() for one in stale]}
            return Judged(
                "done", "failed", descriptions.refused(stale), "descriptions_stale", detail=said
            )
        script = str(args["script"])
        n = self.state.round
        folder = self.folder / "rounds" / f"{n:03}"
        shutil.rmtree(folder / "judged", ignore_errors=True)
        wanted = self._needs_now(glass)
        followed = self._followed_said(glass)
        (folder / "followed.json").write_text(
            json.dumps({n.id: n.command for n in wanted if n.by == "bench"}, indent=1),
            encoding="utf-8",
        )
        judgement = judge.check(self.box, self.work, script, wanted, self.state.picks, folder, part)
        standing: list[judge.Standing] = []
        panes_read: list[judge.Standing] = []
        strips: list[judge.Standing] = []
        overlap: dict[str, Any] = {}
        clashes: list[str] = []
        if judgement.met:
            # Whatever the needs say: every file to be printed, and what's printed together.
            judged = folder / "judged"
            standing = judge.printable(self.box, part, judged, folder / "standing")
            for one in standing:
                self._stood(one, "done", judged)
            # The kind's own: `light` and `capture` on every pane (`standing.py`).
            ran = list(zip(wanted, judgement.readings, strict=True))
            panes_read = glass_checks.check(self.box, glass, judged, folder / "standing", ran)
            for one in panes_read:
                self._stood(one, "done", judged)
            # Curves stored as flat strips, beside `thin` (mqucifer/print-shop#59).
            strips = self._curves(part, judged, folder / "standing")
            for one in strips:
                self._stood(one, "done", judged)
            overlap = self._overlap(judged, part)
            clashes = _clashes(groups, part, overlap)
            (folder / "standing.json").write_text(
                json.dumps(
                    {
                        "printable": [one.plain() for one in standing],
                        "glass": [one.plain() for one in panes_read],
                        "curves": [one.plain() for one in strips],
                        "overlap": overlap,
                        "clashes": clashes,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
        met = (
            judgement.met
            and not clashes
            and not any(one.failed for one in standing)
            and not any(one.failed for one in panes_read)
            and not any(one.failed for one in strips)
        )
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
        # Against the scenario's right answer, if it was started with one: mqucifer/print-shop#67.
        scores.after_round(self.ledger, self.folder)
        reply = self._said_of(judgement, wanted, met, standing, clashes)
        if followed:
            reply += "\n" + "\n".join([FOLLOWED, *followed])
        if panes_read:
            reply += "\n" + "\n".join([glass_checks.HEADING, *glass_checks.said(panes_read)])
        if any(not one.passed for one in strips):
            reply += "\n" + "\n".join([glass_checks.CURVES_HEADING, *glass_checks.said(strips)])
        held = self._holding()
        if held:
            # Whatever the needs read: a check that failed and hasn't passed since (`holding.py`).
            reply += f"\n\n{holding.said(held)}"
        self._remember(judgement)
        unchanged = files == self.state.last_files
        self.state.unchanged = self.state.unchanged + 1 if unchanged else 0
        self.state.last_files = files
        self._write_record()
        detail: dict[str, Any] = {"round": n, "met": met, "code": judgement.code}
        if held:
            detail["held"] = holding.plain(held)
        if met:
            delivered = {
                "groups": groups,
                "looks": looks,
                "standing": standing,
                "glass": panes_read,
                "curves": strips,
                "overlap": overlap,
                "held": held,
            }
            return self._stop_for_approval(
                rows, folder, judgement, wanted, closing, reply, detail, delivered
            )
        return self._next_round(reply, detail)

    def _curves(self, files: list[str], where: Path, kept: Path) -> list[judge.Standing]:
        """Each file's curves, if it has any, built as curves: `info`'s small flat faces as a
        standing check, unless the model set the file aside with its reason
        (mqucifer/print-shop#59). A run from before toolsets carries on as it began."""
        if self.toolset.legacy:
            return []
        asides = {
            str(a["strips_file"]): str(a["why"]) for a in self.state.asides if a.get("strips_file")
        }
        return glass_checks.curves(self.box, files, where, kept, asides)

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
            if name not in allowed and not self._given(name):
                named = ", ".join(f"`{f}`" for f in allowed) or "none"
                reply = (
                    f"`looks` names `{name}`, which isn't one of the files here ({named}), or a "
                    f"given file under `references/`. {shape}"
                )
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

    def _given(self, name: str) -> bool:
        """A file the run was given, under `references/`: `looks` may name one (on
        `eval-through-1` a plan's `looks` naming the given frame was refused, and on
        `eval-light-3` a `done`'s naming the given panes, mqucifer/print-shop#54)."""
        found = paths.inside(self.work, name)
        return name.startswith("references/") and found is not None and found.is_file()

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
        lines.append("")
        if delivered.get("glass"):
            lines += [f"## {glass_checks.HEADING}", "", *glass_checks.said(delivered["glass"]), ""]
        if delivered.get("curves"):
            lines += [
                f"## {glass_checks.CURVES_HEADING}",
                "",
                *glass_checks.said(delivered["curves"]),
                "",
            ]
        if delivered.get("held"):
            lines += [
                "## Checks whose latest try failed, which no need settles",
                "",
                "The model's own bench checks, or a need's at other settings: each failed and "
                "hasn't passed since.",
                "",
                *holding.lines(delivered["held"]),
                "",
            ]
        lines += [
            *self._asides_said(),
            *self._swaps_said(),
            *descriptions.since_said(self.state.restated),
        ]
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
        self._asides_shown(f"approval {n}")
        self._restated_shown(f"approval {n}")
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
        drawn = {name for row in rows for name in row["files"]}
        args += _look_args({name: look for name, look in looks.items() if name in drawn})
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
        """The sheet is drawn, or the pictures the model made are shown, to the model, and nothing
        goes yet: `send_ask` sends it, with what the model says it shows. On both real runs the
        model described the design it meant to make, not what a bench picture showed. It works at
        any point, with the files as they stand. A stop of the request is shown here, in order,
        and the plan, the first, needs no part file: its text, the breakdown, the needs, and any
        picture the model drew (mqucifer/print-shop#38)."""
        question = args["question"].strip()
        variants = args.get("variants") or []
        eyes = _eyes_named(args.get("need"))
        need = eyes[0] if eyes else None
        later = args.get("later") or {}
        stop = str(args.get("stop") or "").strip() or None
        plan = str(args.get("plan") or "").strip()
        pictures = [str(p) for p in args.get("pictures") or []]
        bad = self._ask_problem(question, variants, eyes)
        if bad is None:
            bad = self._stop_problem(stop, plan)
        if bad is None:
            bad = self._eyes_problem(eyes, later, variants)
        if bad is None and not (variants or pictures or plan):
            bad = (
                "nothing_shown",
                "Show something: `variants` of your files, `pictures` you made, or at the plan "
                "stop, the `plan`.",
            )
        if bad is not None:
            return Judged("ask", "failed", bad[1], bad[0])
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
        pieces = [str(p) for p in args.get("test_pieces") or []]
        for path in pieces:
            missing = self.tools._existing("ask", {"path": path})
            if missing is not None:
                return missing
        shown: list[tuple[str, str]] = []
        for path in pictures:
            if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg"):
                reply = f"`pictures` are PNG or JPEG files you made, and `{path}` isn't one."
                return Judged("ask", "failed", reply, "not_a_picture")
            seen_picture = self.tools.call("read", {"path": path})
            if seen_picture.outcome != "ok":
                return Judged("ask", "failed", seen_picture.reply, seen_picture.why)
            shown += seen_picture.images
        seen_now = {
            "variants": {v["name"]: self._held(v["files"]) for v in named},
            "pictures": self._held(pictures),
        }
        repeated = self._already_picked(stop, eyes, seen_now, plan)
        if repeated is not None:
            return Judged("ask", "failed", repeated, "already_picked")
        drawn = list(dict.fromkeys(str(f) for v in named for f in v["files"]))
        looks = self._looks(args.get("looks"), drawn, "ask")
        if isinstance(looks, Judged):
            return looks
        n = self.state.asks + 1
        out = f".pictures/ask-{n}"
        sheet: str | None = None
        bench_said: list[str] = []
        images: list[tuple[str, str]] = []
        image = b""
        if named:
            command = ["render"]
            for one in named:
                command += ["--variant", f"{one['name']}={','.join(one['files'])}"]
            for path in pieces:
                command += ["--test-piece", path]
            command += _look_args({name: look for name, look in looks.items() if name in drawn})
            command += [f"--close={close}" for close in str(args.get("close") or "").split()]
            command += [f"--cut={cut}" for cut in str(args.get("cuts") or "").split()]
            views = str(args.get("views") or " ".join(ASK_VIEWS)).split()
            command += ["--views", *views, "--size", ASK_SIZE, "--renders-out", out]
            done = self.box.bench(command)
            record = recorded(done.output, self.work)
            sheets = [str(f) for f in (record or {}).get("files") or [] if str(f).endswith(".png")]
            if done.code != 0 or not sheets:
                reply = f"The sheet couldn't be made, so nothing went:\n{done.output}"
                return Judged("ask", "failed", reply[:FAILURE_CHARS], "no_sheet")
            sheet = sheets[-1]
            image = paths.read_bytes_inside(self.work, sheet)
            bench_said = [line for line in done.output.splitlines() if _sheet_line(line)]
            images.append((f"{out}/sheet.png", base64.b64encode(image).decode()))
        declared = self._glass_declared(args.get("glass"), drawn, "ask")
        if isinstance(declared, Judged):
            return declared
        standing = self._standing_at_ask(drawn, declared) if drawn else []
        self.state.draft = {
            "question": question,
            "need": need,
            "needs": eyes,
            "later": {k: str(v) for k, v in later.items()} if eyes else {},
            "stop": stop,
            "plan": plan,
            "variants": named,
            "test_pieces": pieces,
            "pictures": pictures,
            "looks": looks,
            "round": self.state.round,
            "seen": {
                "variants": {v["name"]: self._held(v["files"]) for v in named},
                "test_pieces": self._held(pieces),
                "pictures": self._held(pictures),
                "sheet": hashlib.sha256(image).hexdigest() if sheet else None,
            },
            "sheet": sheet,
            "bench_said": bench_said,
            "standing": [one.plain() for one in standing],
            "at_call": self.session.requests,
        }
        self.save()
        what = "The sheet above is drawn" if sheet else "What's above is ready"
        lines = [
            f"{what}, and nothing has gone out yet. Look at it, and send it with `send_ask`, "
            "saying in `says` what it shows: each picture as drawn, not the design you meant. It "
            "reaches whoever answers, the Sponsor or someone standing in for them, with the "
            "question, in your words."
        ]
        if bench_said:
            lines += ["What the bench says it drew:", *[f"- {line}" for line in bench_said]]
        if pictures:
            lines.append(f"Your pictures go as they are: {', '.join(f'`{p}`' for p in pictures)}.")
        undrawn = [name for name in looks if name not in drawn]
        if undrawn:
            lines.append(
                f"`looks` names {', '.join(f'`{n}`' for n in undrawn)}, given but not on this "
                "sheet, so it isn't drawn: name it in a variant's `files` to draw it."
            )
        if stop == forms.PLAN:
            lines.append(
                "With your plan go your latest breakdown and the needs as settled, with the "
                "request's lines each covers."
            )
        failing = [one for one in standing if not one.passed]
        if failing:
            lines.append(
                "The harness's own checks on these files, which go beside the question whatever "
                "you say:"
            )
            lines += [f"- {one.command}: {one.why}" for one in failing]
        unprinted = self._thin_failing()
        if unprinted:
            lines.append(
                "Part files whose last `thin` failed, which go beside the question too: "
                + ", ".join(f"`{one.file}` (call {one.at_call})" for one in unprinted)
            )
        self.state.draft["thin_failing"] = [
            {"file": one.file, "at_call": one.at_call, "why": one.why} for one in unprinted
        ]
        held = self._holding()
        if held:
            lines.append(
                "Checks whose latest try failed, which go beside the question too:\n"
                + "\n".join(holding.lines(held))
            )
        self.state.draft["held"] = holding.lines(held)
        # What's printed is what the sheet shows and the part `done` last named. With neither,
        # as at the plan, each need checked on a file the model made is listed: `done` holds
        # each to the part.
        printed = [*drawn, *(self.state.part or [])]
        off = self._off_part(printed)
        if off:
            what = (
                "on a file that isn't on the sheet, in the part or given"
                if printed
                else "on a file you made, which `done` will hold to the part"
            )
            lines.append(
                f"Needs checked {what}, which go beside the question too:\n"
                + "\n".join(f"- `{need}`: `{file}`, in `{command}`" for need, file, command in off)
            )
        self.state.draft["off_part"] = [
            {"need": need, "file": file, "command": command} for need, file, command in off
        ]
        self.state.draft["off_part_known"] = bool(printed)
        replaced = self._on_replaced(printed)
        unread = self._unread(printed)
        if replaced or unread:
            said = [
                f"- `{need}` reads `{file}`, and the part has `{own}`"
                for need, file, own, _ in replaced
            ] + [f"- no need reads `{name}`" for name in unread]
            lines.append(
                "What `done` would refuse in the needs, which goes beside the question too:\n"
                + "\n".join(said)
            )
        self.state.draft["on_replaced"] = [
            {"need": need, "file": file, "part_file": own, "command": command}
            for need, file, own, command in replaced
        ]
        self.state.draft["unread"] = unread
        followed = self._followed_said(declared or glass_checks.load(self.state.glass))
        if followed:
            lines.append(FOLLOWED + "\n" + "\n".join(followed))
        self.state.draft["followed"] = followed
        stale = self._stale(printed)
        if stale:
            lines.append(
                "Descriptions written before the part changed, which go beside the question too:\n"
                + "\n".join(descriptions.lines(stale))
            )
        self.state.draft["stale"] = [one.plain() for one in stale]
        if self.state.needs_refused:
            lines.append(
                f"Your {len(self.state.needs_refused)} refused `needs` call(s) since the last stop "
                "go beside the question, so the person sees what you tried to change."
            )
        self.save()
        open_stop = self._open_stop()
        if open_stop is not None and stop != open_stop.id:
            lines.append(
                f"The stop `{open_stop.id}`, {open_stop.text}, is still open: `done` waits for it."
            )
        lines.append("A new `ask` draws a new sheet in place of this one.")
        return Judged(
            "ask",
            "ok",
            "\n".join(lines),
            images=images + shown,
            detail={"drafted": n, "standing_failing": len(failing), "stop": stop},
        )

    def _stop_problem(self, stop: str | None, plan: str) -> tuple[str, str] | None:
        """A stop is shown in its turn: the next stop's ask waits for the one before it to have a
        pick, and the plan shows what it rests on."""
        if stop is None:
            return None
        form = self._form()
        if form is None or form.stop(stop) is None:
            named = ", ".join(f"`{one.id}`" for one in (form.stops if form else [])) or "none"
            return "no_such_stop", f"`stop` names one of the request's stops: {named}."
        open_stop = self._open_stop()
        if stop not in self.state.stops and open_stop is not None and open_stop.id != stop:
            return "stop_open", (
                f"The stop `{open_stop.id}`, {open_stop.text}, is open: until its `ask` has a "
                f'pick, `{stop}` waits. Show it first, with `"stop": "{open_stop.id}"`.'
            )
        if stop == forms.PLAN:
            if not self.state.breakdowns:
                return "no_breakdown", (
                    "The plan shows your breakdown: write it with `breakdown` first."
                )
            if not self.state.needs_version:
                return "no_needs", (
                    "The plan shows the needs it'll be checked against: settle them with `needs` "
                    "first."
                )
            if not plan:
                return "no_plan", "At the plan stop, `plan` says how you'll go about it."
        return None

    def _glass_declared(
        self, given: Any, part: list[str], tool: str
    ) -> list[glass_checks.Glass] | Judged:
        """The frames and panes the kind's standing checks read (`standing.py`): at `done` given,
        with every file of `part`; at `ask`, the latest given."""
        if not self.toolset.standing:
            return []
        if given is None:
            if tool == "done":
                reply = (
                    f"Nothing was checked: `done` names the frames and panes in `glass`, as "
                    f"{self.toolset.title}'s own checks read every pane. {glass_checks.SHAPE}"
                )
                return Judged("done", "failed", reply, "no_glass")
            return glass_checks.load(self.state.glass)
        found = glass_checks.read(given, part, self.work, whole=tool == "done")
        if isinstance(found, str):
            return Judged(tool, "failed", found, "not_glass")
        self.state.glass = [one.plain() for one in found]
        self.save()
        return found

    def _standing_at_ask(
        self, files: list[str], glass: list[glass_checks.Glass] | None = None
    ) -> list[judge.Standing]:
        """What an ask shows, read as it stands: each settled need's own check, and `thin` on each
        file. On `cup-1` a question said a pane was split in two where `capture` read one, and no
        need ran `capture` any more."""
        found: list[judge.Standing] = []
        benched = [n for n in self._needs_now(glass) if n.by == "bench"]
        ran = list(zip(benched, self._read_now(benched), strict=True))
        for need, reading in ran:
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
            # The kind's own, on each frame whose files are all on the sheet or given.
            shown = set(files)
            on_sheet = [
                one
                for one in glass or []
                if all(f in shown or f.startswith("references/") for f in one.files())
                and any(f in shown for f in one.files())
            ]
            found += glass_checks.check(self.box, on_sheet, scratch, scratch / ".kept", ran)
            found += self._curves(files, scratch, scratch / ".kept")
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
        pictures = draft.get("pictures") or []
        for variant in [*named, {"files": draft["test_pieces"]}, {"files": pictures}]:
            for path in variant["files"]:
                if self.tools._existing("send_ask", {"path": str(path)}) is not None:
                    reply = f"`{path}` is gone since the sheet was drawn: `ask` again."
                    return Judged("send_ask", "failed", reply, "changed")
        now = {
            "variants": {v["name"]: self._held(v["files"]) for v in named},
            "test_pieces": self._held(draft["test_pieces"]),
            "pictures": self._held(pictures),
        }
        seen = draft["seen"]
        if (
            now["variants"] != seen["variants"]
            or now["test_pieces"] != seen["test_pieces"]
            or now["pictures"] != (seen.get("pictures") or {})
        ):
            reply = (
                "Files on the sheet changed since it was drawn, so it isn't what they'd see: "
                "`ask` again."
            )
            return Judged("send_ask", "failed", reply, "changed")
        n = self.state.asks + 1
        self.state.asks = n
        folder = self.folder / "asks" / str(n)
        (folder / "variants").mkdir(parents=True)
        sheet = folder / "sheet.png" if draft.get("sheet") else None
        if sheet is not None:
            sheet.write_bytes(paths.read_bytes_inside(self.work, draft["sheet"]))
        if pictures:
            (folder / "pictures").mkdir()
        for path in pictures:
            kept = folder / "pictures" / Path(path).name
            kept.write_bytes(paths.read_bytes_inside(self.work, path))
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
        stop = draft.get("stop")
        asked = {
            "question": draft["question"],
            "need": draft["need"],
            "needs": draft.get("needs") or ([draft["need"]] if draft["need"] else []),
            "later": draft.get("later") or {},
            "stop": stop,
            "plan": draft.get("plan") or "",
            "pictures": pictures,
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
        form = self._form()
        shown_stop = form.stop(stop) if form is not None and stop else None
        lines = []
        if shown_stop is not None:
            lines += [
                f"# Stop `{shown_stop.id}`: {shown_stop.text}",
                "",
                "The run hands nothing over until this has a pick: `done` and the next stop wait "
                "for it. Writing and running go on meanwhile.",
                "",
            ]
        lines += [draft["question"], ""]
        lines += self._eyes_said(draft)
        if about:
            lines += [about, ""]
        if sheet is not None:
            lines += [f"The sheet: {sheet}", ""]
        if pictures:
            lines += ["Pictures the model made:", ""]
            lines += [f"- {folder / 'pictures' / Path(p).name}" for p in pictures]
            lines.append("")
        if not named:
            lines += [
                'With nothing to choose between, the pick is "yes" to go on from this, or '
                '"none", with the reason, if it isn\'t right.',
                "",
            ]
        what = "the sheet shows" if sheet is not None else "it shows"
        lines += [f"## What the model says {what}", "", says, ""]
        if draft["bench_said"]:
            lines += [
                "## What the bench says it drew",
                "",
                *[f"- {line}" for line in draft["bench_said"]],
                "",
            ]
        if stop == forms.PLAN:
            lines += self._plan_said(draft.get("plan") or "")
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
        unprinted = draft.get("thin_failing") or []
        if unprinted:
            lines += [
                "## Part files whose last `thin` failed",
                "",
                "Each can't be printed as it stands, whatever the other files do.",
                "",
                *[
                    f"- `{one['file']}`, at call {one['at_call']}: {one['why']}"
                    for one in unprinted
                ],
                "",
            ]
        held = draft.get("held") or []
        if held:
            lines += [
                "## Checks whose latest try failed",
                "",
                "The model's own bench checks, or a need's at other settings: each failed and "
                "hasn't passed since, whatever the question says.",
                "",
                *held,
                "",
            ]
        off = draft.get("off_part") or []
        if off and draft.get("off_part_known", True):
            lines += [
                "## Needs checked on a file that isn't printed or given",
                "",
                "A need is checked on the files that will be printed, or on the given references. "
                "`done` refuses these until the needs change, which takes `--reopen-needs`.",
                "",
            ]
        elif off:
            lines += [
                "## Needs checked on a file the model made",
                "",
                "No part is named yet. A need is checked on the files that will be printed, or on "
                "the given references: at `done`, each of these files has to be one of the part, "
                "or the need is refused. A helper the model made to check against, such as a "
                "pocket it modelled itself, isn't the part; `--reopen-needs` with the pick lets "
                "the need move.",
                "",
            ]
        if off:
            lines += [
                *[f"- `{o['need']}`: `{o['file']}`, in `{o['command']}`" for o in off],
                "",
            ]
        lines += self._part_reading_said(draft)
        if draft.get("followed"):
            lines += [
                "## Needs that follow the part",
                "",
                "A settled need's frame, panes and pane count are the part's, as the model's "
                "latest `glass` names them. Its figures stay as settled: changing one takes "
                "`--reopen-needs` with the Sponsor's note.",
                "",
                *draft["followed"],
                "",
            ]
        lines += self._needs_refused_said()
        lines += self._asides_said()
        lines += self._swaps_said()
        stale = [descriptions.Stale.load(one) for one in draft.get("stale") or []]
        lines += descriptions.at_stop(stale)
        lines += descriptions.since_said(self.state.restated)
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
            ", ".join(draft.get("needs") or []) or draft["need"],
            draft["question"],
            named,
            str(sheet) if sheet is not None else "",
            self.session.requests,
            seen,
        )
        self.ledger.sheet_said(self.state.name, n, says)
        if stop:
            self.ledger.stop_asked(self.state.name, n, stop)
        for one in draft["standing"]:
            self._stood(judge.Standing(**one), "ask", self.work, ask=n)
        if self.state.swaps:
            self.ledger.swaps_shown(self.state.name, f"ask {n}")
            self.state.swaps = []
        self._asides_shown(f"ask {n}")
        self._restated_shown(f"ask {n}")
        if stale:
            self.ledger.stale_found(self.state.name, self.session.requests, f"ask {n}", stale)
        if stop == forms.PLAN:
            self._plan_described(draft.get("plan") or "", n)
        self.state.needs_refused = []
        self.state.draft = None
        self.state.stop = {"kind": "ask", "n": n}
        self.save()
        reply = (
            "The sheet has gone out with what you said of it, to the Sponsor or someone standing "
            "in for them. The pick and its reason come in your next message, saying who made it."
        )
        return Judged("send_ask", "ok", reply, ending=Ending("ask", {"n": n}))

    def _plan_said(self, plan: str) -> list[str]:
        """The plan, as the person at its stop reads it: the model's words, how it says the part
        is built, and the needs it'll be checked against, each by the request's lines."""
        lines = ["## The plan, in the model's words", "", plan, ""]
        kept = self.folder / "breakdown.md"
        if kept.is_file():
            body = kept.read_text(encoding="utf-8").strip().splitlines()
            lines += ["## How the model says it's built", ""]
            lines += [("#" + line if line.startswith("## ") else line) for line in body[2:]]
            lines.append("")
        lines += ["## The needs", ""]
        for need in self._needs():
            if need.by == "bench":
                check = f"`{need.command}`, {need.limit()}"
            else:
                check = f"by eye: {need.look_for}"
                if need.no_bench:
                    check += f" (no bench command measures it: {need.no_bench})"
            mark = "**fixed** " if need.fixed else ""
            covers = f" covers {', '.join(need.covers)}." if need.covers else ""
            lines.append(f"- {mark}**{need.id}** ({need.what}):{covers} Checked {check}")
            if need.figure:
                lines.append(f"  - its figure is read as: {need.figure}")
        lines.append("")
        form = self._form()
        if form is not None and form.lines():
            lines += ["## The request's lines, and the needs that cover each", ""]
            for line in form.lines():
                by = [n.id for n in self._needs() if line.id in n.covers]
                lines.append(f"- `{line.id}`, covered by {', '.join(by) or 'none'}: {line.text}")
            lines.append("")
            lines += self._figures_left(form)
        return lines

    def _figures_left(self, form: forms.Form) -> list[str]:
        """Each figure of a non-negotiable that no fixed need's check carries, for the person to
        look at: it's said, never refused, as a figure may be one no bench command reads."""
        found = []
        for line in form.non_negotiables:
            figures = re.findall(r"\d+(?:\.\d+)?", line.text)
            carried: set[float] = set()
            for need in self._needs():
                if need.fixed and line.id in need.covers:
                    for word in re.findall(r"-?\d+(?:\.\d+)?", need.command):
                        carried.add(float(word))
                    carried |= {b for b in (need.at_least, need.at_most) if b is not None}
            left = [f for f in figures if float(f) not in carried]
            if left:
                found.append(
                    f"- `{line.id}`: {', '.join(left)} isn't in the check of a fixed need that "
                    "covers it"
                )
        if not found:
            return []
        return ["Figures of the non-negotiables no fixed need's check carries:", "", *found, ""]

    def _held(self, files: list[str]) -> dict[str, str]:
        """Each file as it stands now, by what it holds: what a sheet of them saw."""
        return {
            str(name): paths.content_hash(str(name), paths.read_bytes_inside(self.work, str(name)))
            for name in files
        }

    def _ask_problem(self, question: str, variants: Any, eyes: list[str]) -> tuple[str, str] | None:
        if not question:
            return "empty", "`question` says what they're judging."
        if not isinstance(variants, list):
            return "not_a_variant", 'Each variant is {"name": "A", "files": ["a.step"], ...}.'
        seen: set[str] = set()
        for variant in variants:
            shape = 'Each variant is {"name": "A", "files": ["a.step"], "about": "..."}.'
            if not isinstance(variant, dict) or "name" not in variant or "files" not in variant:
                return "not_a_variant", shape
            if not VARIANT.fullmatch(str(variant["name"])) or variant["name"] in ("none", "yes"):
                return "not_a_variant", f"`{variant['name']}` won't do as a short name. {shape}"
            if variant["name"] in seen:
                return "not_a_variant", f"Two variants are named {variant['name']}."
            seen.add(variant["name"])
        known = [n.id for n in self._needs() if n.by == "eye"]
        for need in eyes:
            if need not in known:
                named = ", ".join(known) or "none yet"
                return "no_such_need", f"`need` names eye needs. They are: {named}."
        return None

    def _eyes_open(self) -> list[Need]:
        """Each eye need with no pick that holds for the files as they stand."""
        now = paths.geometry_hashes(self.work)
        return [
            n
            for n in self._needs()
            if n.by == "eye"
            and (
                n.id not in self.state.picks
                or judge.not_what_it_saw(self.state.picks[n.id], now, None) is not None
            )
        ]

    def _eyes_problem(self, eyes: list[str], later: Any, variants: Any) -> tuple[str, str] | None:
        """Eye needs judged on one sheet are asked together, so one stop answers them: on
        `suncatcher-3` two were confirmed one stop each on the same layout, and the third set
        went in one only after the stand-in asked (mqucifer/print-shop#59). And an eye need is
        asked on the part's own files, which is all its pick can pass."""
        if not eyes:
            return None
        if not variants:
            return "eye_on_picture", (
                "An eye need passes on a pick of the part's own files, which `done` holds it to, "
                "so `need` goes with `variants`: a picture you drew isn't the part, and a pick on "
                "it can't pass the need. Show the files as variants, with your pictures beside "
                "them if they help."
            )
        if not isinstance(later, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in later.items()
        ):
            return "eyes_apart", EYES_LATER
        left = [
            n.id for n in self._eyes_open() if n.id not in eyes and not later.get(n.id, "").strip()
        ]
        if left:
            named = ", ".join(f"`{one}`" for one in left)
            return "eyes_apart", (
                f"Eye needs judged on one sheet go in one ask, so one stop answers them all: "
                f"{named} {'is' if len(left) == 1 else 'are'} open too. Name each this sheet "
                f"answers in `need`, or {EYES_LATER}"
            )
        return None

    def _eyes_said(self, draft: dict[str, Any]) -> list[str]:
        """What the eye needs asked are, for the person: one pick answers every one."""
        eyes = draft.get("needs") or ([draft["need"]] if draft.get("need") else [])
        if not eyes:
            return []
        found = {n.id: n for n in self._needs()}
        lines = ["One pick answers each of these, judged by eye on the sheet:", ""]
        lines += [f"- **{e}**: {found[e].look_for if e in found else ''}" for e in eyes]
        later = draft.get("later") or {}
        if later:
            lines += ["", "Asked at a later stop, as the model says:", ""]
            lines += [f"- **{k}**: {v}" for k, v in later.items()]
        return [*lines, ""]

    def _already_picked(
        self, stop: str | None, eyes: list[str], seen: dict[str, Any], plan: str
    ) -> str | None:
        """A stop, or eye needs, asked again on what a pick already answered, unchanged since by
        hash: on `suncatcher-3` the layout stop was raised again with the picked picture, byte
        for byte, only to say the fix was understood (mqucifer/print-shop#59)."""
        if not stop and not eyes:
            return None
        mine = _shown(seen, plan if stop == forms.PLAN else "")
        if mine == _shown({}, ""):
            return None
        asks = sorted(
            (int(p.name) for p in (self.folder / "asks").glob("*") if p.name.isdigit()),
            reverse=True,
        )
        for n in asks:
            folder = self.folder / "asks" / str(n)
            try:
                asked = json.loads((folder / "asked.json").read_text(encoding="utf-8"))
                picked = json.loads((folder / "pick.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if picked.get("pick") in (None, "none"):
                continue
            theirs = asked.get("needs") or ([asked["need"]] if asked.get("need") else [])
            same_stop = bool(stop) and asked.get("stop") == stop and stop in self.state.stops
            same_eyes = bool(eyes) and set(eyes) <= set(theirs)
            if not (same_stop or same_eyes):
                continue
            kept = asked.get("plan") or "" if asked.get("stop") == forms.PLAN else ""
            if _shown(asked.get("seen") or {}, kept) != mine:
                continue
            what = f"the stop `{stop}`" if same_stop else ", ".join(f"`{e}`" for e in eyes)
            who = picked_by_words(str(picked.get("by")))
            said = (
                f"Already picked; go on. Ask {n} showed {what} on the same files and pictures, "
                f"unchanged since by their hash, and {who[0].lower()}{who[1:]} picked "
                f"{picked['pick']}: {picked.get('reason') or ''}"
            )
            if picked.get("fix_first"):
                said += f" Fix first: {picked['fix_first']}"
            return (
                said + "\nAsking again tells them nothing new. Go on with the work, and show it "
                "at the next stop once it's changed."
            )
        return None


EYES_LATER = (
    "say in `later` why it needs another sheet, by its id: "
    '{"hang-hole": "a close-up of the hole, once it\'s cut"}. The person reads it.'
)


def _eyes_named(given: Any) -> list[str]:
    """The eye needs an ask names: one, or a list."""
    if isinstance(given, str):
        given = [given]
    if not isinstance(given, list):
        return []
    return list(dict.fromkeys(str(one).strip() for one in given if str(one).strip()))


def _shown(seen: dict[str, Any], plan: str) -> tuple[Any, ...]:
    """What an ask showed, by hash: each variant's files, whatever its name, each picture, and
    the plan's text at the plan stop."""
    variants = sorted(tuple(sorted(v.values())) for v in (seen.get("variants") or {}).values())
    pictures = sorted((seen.get("pictures") or {}).values())
    return (tuple(variants), tuple(pictures), plan)


def _need_what(nid: str) -> str:
    return f"the need `{nid}`'s `what`"


def _described_name(key: str) -> str:
    """How `restate` names a description in `of`."""
    return f"`{key.removeprefix(descriptions.NEED)}`"


def _description_key(of: str) -> str:
    """`breakdown`, `plan`, or a need by its id, as `restate` names it."""
    of = of.strip().strip("`")
    if of in (descriptions.BREAKDOWN, descriptions.PLAN):
        return of
    return descriptions.need_key(of.removeprefix(descriptions.NEED).strip())


def _plain_name(name: str) -> str:
    return str(Path(name.removeprefix("/work/")).as_posix()).removeprefix("./")


def _off_part_refused(off: list[tuple[str, str, str]]) -> str:
    """`done`'s refusal of a need checked on a file that's neither printed nor given."""
    lines = [
        "Nothing was checked: a need is checked on the files that will be printed, or on the given "
        "references, and these aren't:"
    ]
    lines += [
        f"- `{need}` reads `{file}`, in `{command}`, which isn't in `part` or `references/`."
        for need, file, command in off
    ]
    lines.append(
        "If the file is printed, name it in `part`. Otherwise the need's check has to change, "
        "and the needs are settled: show what you have with `ask`, and the need goes beside your "
        "question, so a person can reopen them."
    )
    return "\n".join(lines)


def _on_replaced_refused(found: list[tuple[str, str, str, str]]) -> str:
    """`done`'s refusal of a need checked on a given file the part has its own copy of."""
    lines = [
        "Nothing was checked: a need is checked on the part, and these are checked on a given "
        "reference the part replaces:"
    ]
    lines += [
        f"- `{need}` reads `{file}`, in `{command}`, and the part has its own `{own}`."
        for need, file, own, command in found
    ]
    lines.append(
        'A check on the given file, as the "before" state, can still run on the bench: it just '
        "isn't a need. The needs are settled, so show what you have with `ask`: the need goes "
        "beside your question, so a person can reopen them."
    )
    return "\n".join(lines)


def _unread_refused(unread: list[str]) -> str:
    """`done`'s refusal of a part whose files aren't all read by a need's check."""
    lines = [
        "Nothing was checked: each file of the part is read by a need's check, beyond `thin`, "
        "and no need's command reads these:"
    ]
    lines += [f"- `{name}`" for name in unread]
    lines.append(
        "A need that names another file, such as `panes` on the frame, builds its own and never "
        "opens these. If a need should read one, the needs are settled: show it with `ask`, and "
        "it goes beside your question, so a person can reopen them. If none has to, say why with "
        "`set_aside`, giving `file` and `why`: the person at the next stop reads it."
    )
    return "\n".join(lines)


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
        "stops": state.get("stops") or {},
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
        "reasoning_effort": next(
            (
                {"effort": e["effort"], "from": e["said"]}
                for e in transcript.events_of(events)
                if e["kind"] == "effort"
            ),
            {"effort": UNKNOWN, "from": "not recorded: a run from before mqucifer/print-shop#40"},
        ),
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
            f"{folder / f'{n}.answers.md'}, then: scripts/rounds.py resume {name} --from "
            f'"{SPONSOR}", or --from "{STAND_IN} <who>" if someone stood in for them'
        )
    if ending.name == "ask":
        folder = run.folder / "asks" / str(ending.said["n"])
        asked = json.loads((folder / "asked.json").read_text(encoding="utf-8"))
        choice = "<a variant or none>" if asked.get("variants") else "yes or none"
        stop = f"the stop `{asked['stop']}`: " if asked.get("stop") else ""
        return (
            f"waiting at {stop}{folder / 'question.md'}. Write {folder / 'pick.json'}, "
            f'{{"pick": "{choice}", "reason": "...", "by": "{SPONSOR}"}}, or "by": '
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
