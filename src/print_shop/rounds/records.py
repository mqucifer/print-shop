"""A run's events, into the ledger's tables: what was said to it, where it stopped, what it cost.

`Recorder.feed` takes one event at a time and writes what that event gives. A live run feeds it each
event as it's kept, and reading a run back feeds it the file, so what a table says of a run doesn't
depend on when it was read. A row's key comes from its place in the events, and a second reading
adds nothing, so a run killed part-way catches up when it's next loaded.

Events from before this was built hold less, and it's read back where it can be:
- a note or an answer is known by the words it began with, and its time is its first call's;
- who made a pick is the ledger's `picked_by`, never the message's words: a stand-in's pick was once
  told to the model as the Sponsor's, and its events are kept as they were sent;
- each stop is an `ended` event, and the process after it takes the next commit in `state.harness`;
- a compaction's prompts are the calls' either side of it.

**Runaways are evidence, not noise.** The Sponsor: "When we get these run-away calls or repeating
itself constantly its almost ALWAYS has been some issue with its context or tool or skill not
working well." So a call that hits the limit, or whose reasoning repeats earlier text past a stated
share, gets a row saying what it was doing and what it had and hadn't got, for review. Code can only
give the evidence for what it lacked, and `review_note` is for a reviewer. Nothing here cuts a call.

Repetition counts sentences of `REPEAT_SENTENCE` characters or more that match an earlier one word
for word, across the run. Paraphrase isn't counted, so it's a floor.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from print_shop.rounds import paths, skills, transcript
from print_shop.rounds.ledger import DesignLedger

#: A sentence shorter than this is too plain to count as repeating.
REPEAT_SENTENCE = 25
#: Reasoning shorter than this is too short for a share of it to mean anything.
REPEAT_LEAST_CHARS = 2000
#: The share past which a call's reasoning gets a row, unless a run's limits say otherwise.
REPEAT_SHARE = 0.5
#: Characters a token, for what's in the text: `session.CHARS_PER_TOKEN`, measured.
CHARS_PER_TOKEN = 2.7
SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
PATH_IN_ARGUMENTS = re.compile(r'"path"\s*:\s*"([^"]{1,200})"')
TAIL = 400
REFUSED_KEPT = 5
#: Who an answer is from, when nobody said: the harness has only ever called them the Sponsor's.
ANSWERS_FROM = "relayed as the Sponsor's"
#: Who a pick is from, where the ledger doesn't say: an empty `picked_by` says nothing of who.
UNKNOWN = "unknown"
#: How a relayed message reads, for events from before they were recorded as such.
NOTE_WORDS = "A note from the people running this:"
ANSWER_WORDS = "The Sponsor's answers:"
REOPEN_WORDS = "The needs may be settled again"
PICK_WORDS = re.compile(
    r"(?:The Sponsor|A stand-in for the Sponsor \(.+?\)) (?:picked|didn't pick)"
)
#: How a `try` read a need, in its reply, for events that hold only that.
TRIED_LINE = re.compile(r"- ([A-Za-z0-9._-]+), (would pass|WOULDN'T PASS): (.*)")
TABLES = ("calls", "interventions", "timeline", "runaways", "tries", "nudges", "refusals")


def repeated(text: str, seen: set[str]) -> tuple[int, int]:
    """Of this reasoning's sentences long enough to count, the characters that repeat an earlier
    one and all their characters. What's new joins `seen`."""
    again, total = 0, 0
    for sentence in SENTENCES.split(text):
        sentence = sentence.strip()
        if len(sentence) < REPEAT_SENTENCE:
            continue
        total += len(sentence)
        if sentence in seen:
            again += len(sentence)
        else:
            seen.add(sentence)
    return again, total


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class Recorder:
    """One run's events, as they come, into the tables."""

    def __init__(
        self,
        ledger: DesignLedger,
        run: str,
        harness: list[str],
        repeat_share: float = REPEAT_SHARE,
        skills_offered: tuple[str, ...] = skills.SETS,
    ) -> None:
        self.ledger = ledger
        self.skills_offered = skills_offered
        self.skill_reads = 0
        self.run = run
        # Read as it's used: a live run adds to it at each resume.
        self.harness = harness
        self.repeat_share = repeat_share
        self.requests = 0
        self.segment = 0
        self.phase = 0
        self.phase_call: int | None = None
        self.seen: set[str] = set()
        self.interventions = 0
        self.timeline = 0
        self.tries = 0
        self.nudges = 0
        self.after_stop = False
        self.last_stop: Any = None
        self.relayed: set[str] = set()
        self.compacted_at: int | None = None
        self.compactions = 0
        self.calls_since_compaction = 0
        self.last_prompt = 0
        self.skills_read: set[str] = set()
        self.refused: deque[list[str]] = deque(maxlen=REFUSED_KEPT)
        self.last_bench = ""
        self.refusals = 0
        # What's been rewritten since the last request: a compaction's cut, before its event.
        self.cut: dict[str, Any] = {}

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        if self.segment == 0:
            self._segment("start", event.get("t"), 1)
        handler = getattr(self, f"_on_{kind}", None)
        if handler is not None:
            handler(event)

    def feed_all(self, events: Iterable[dict[str, Any]]) -> None:
        for event in events:
            self.feed(event)

    # The timeline

    def _row(self, kind: str, **row: Any) -> None:
        self.timeline += 1
        self.ledger.add(
            "timeline",
            run=self.run,
            seq=self.timeline,
            kind=kind,
            segment=self.segment,
            **row,
        )

    def _segment(self, kind: str, at: str | None, at_call: int, commit: str | None = None) -> None:
        self.segment += 1
        i = self.segment - 1
        found = commit or (self.harness[i] if i < len(self.harness) else None)
        self._row(kind, at=at, at_call=at_call, harness_commit=found)
        self.after_stop = False

    def _on_resumed(self, event: dict[str, Any]) -> None:
        self._segment("resume", event.get("t"), self.requests + 1, event.get("commit"))

    def _on_after_action(self, event: dict[str, Any]) -> None:
        """The after-action review, after the approval: a process of its own."""
        self._segment("after_action", event.get("t"), self.requests + 1, event.get("commit"))

    def _on_ended(self, event: dict[str, Any]) -> None:
        self._row(
            "stop",
            name=event.get("name"),
            at=event.get("t"),
            at_call=self.requests,
            said=_json(event.get("said")),
        )
        self.after_stop = True
        self.last_stop = event.get("name")

    def _on_compacted(self, event: dict[str, Any]) -> None:
        self.compacted_at = self.timeline + 1
        self.compactions += 1
        self.calls_since_compaction = 0
        cut = _json(self.cut) if self.cut else None
        self._row(
            "compaction",
            at=event.get("t"),
            at_call=event.get("before_call", self.requests + 1),
            said=cut,
            estimated_before=event.get("estimated"),
            estimated_after=event.get("estimated_after"),
            prompt_before=event.get("prompt_before", self.last_prompt),
        )
        if cut is not None:
            # A run read back from before this has its compaction's row, and what it cut empty.
            self.ledger.change(
                "UPDATE timeline SET said=? WHERE run=? AND seq=? AND said IS NULL",
                cut,
                self.run,
                self.timeline,
            )
        self.cut = {}

    def _on_rewrite(self, event: dict[str, Any]) -> None:
        """A change to an earlier message: what compaction cuts, counted until its event."""
        message = event.get("message") or {}
        content = str(message.get("content") or "")
        if message.get("role") == "tool" and content.startswith("(compacted: "):
            tool = content.removeprefix("(compacted: ").split(":", 1)[0]
            shrunk = self.cut.setdefault("replies_shrunk", {})
            shrunk[tool] = shrunk.get(tool, 0) + 1
        elif message.get("role") == "assistant":
            if "reasoning_content" in message and not message["reasoning_content"]:
                self.cut["reasoning_dropped"] = self.cut.get("reasoning_dropped", 0) + 1
            noted = sum(
                1
                for call in message.get("tool_calls") or []
                if "(compacted: " in str((call.get("function") or {}).get("arguments"))
            )
            if noted:
                self.cut["texts_noted"] = self.cut.get("texts_noted", 0) + noted

    # What a person sent

    def _on_relayed(self, event: dict[str, Any]) -> None:
        self.relayed.add(str(event.get("message")))
        self._intervention(
            str(event["relay"]),
            str(event["from"]),
            str(event["text"]),
            int(event["at_call"]),
            event.get("t"),
            event.get("round"),
        )

    def _intervention(
        self, kind: str, who: str, text: str, at_call: int, at: str | None, round_n: int | None
    ) -> None:
        # What arrives together starts one phase: a pick and the note sent with it.
        if self.phase_call != at_call:
            self.phase += 1
            self.phase_call = at_call
        self.interventions += 1
        self.ledger.add(
            "interventions",
            run=self.run,
            seq=self.interventions,
            at=at,
            at_call=at_call,
            round=round_n if round_n is not None else self._round_of(at_call),
            phase=self.phase,
            kind=kind,
            from_who=who,
            text=text,
        )

    def _appended(self, messages: list[dict[str, Any]], at_call: int) -> None:
        """A message as it went to the model, in events from before each said who it was from."""
        for message in messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, str):
                continue
            if content in self.relayed:
                self.relayed.discard(content)
                continue
            found = _an_old_message(content)
            if found is not None:
                who = self._picked_by(at_call) if found[0] == "pick" else found[1]
                self._intervention(found[0], who, content, at_call, None, None)

    def _picked_by(self, at_call: int) -> str:
        """Who made the pick that was asked for last before this call, as the ledger has it."""
        found = self.ledger.rows(
            "SELECT picked_by FROM picks WHERE run=? AND at_call<? ORDER BY ask DESC LIMIT 1",
            self.run,
            at_call,
        )
        return str(found[0]["picked_by"]) if found and found[0]["picked_by"] else UNKNOWN

    def _on_snapshot(self, event: dict[str, Any]) -> None:
        self._appended(event.get("append") or [], self.requests + 1)

    def _on_request(self, event: dict[str, Any]) -> None:
        n = int(event["n"])
        self.requests = n
        self.cut = {}
        if self.after_stop:
            # A conversation that wouldn't fit starts again from the record in the same process.
            if self.last_stop == "context_full" and event.get("from") == 0:
                self.after_stop = False
            else:
                self._segment("resume", event.get("t"), n)
        self._appended(event.get("append") or [], n)
        if event.get("from") == 0 and n > 1:
            self._row("restart", at=event.get("t"), at_call=n)

    # What it cost

    def _round_of(self, n: int) -> int:
        found = self.ledger.rows(
            "SELECT round FROM rounds WHERE run=? AND first_call<=? ORDER BY round DESC LIMIT 1",
            self.run,
            n,
        )
        return int(found[0]["round"]) if found else 0

    def _on_call(self, event: dict[str, Any]) -> None:
        n = int(event["n"])
        used = event.get("usage") or {}
        message = event.get("message") or {}
        reasoning = str(message.get("reasoning_content") or "")
        again, counted = repeated(reasoning, self.seen)
        share = round(again / counted, 4) if counted else 0.0
        called = _called(message)
        round_n = self._round_of(n)
        at = event.get("at")
        args_chars = sum(c["chars"] for c in called)
        self.ledger.add(
            "calls",
            run=self.run,
            n=n,
            at=at,
            seconds=event.get("seconds"),
            round=round_n,
            phase=self.phase,
            segment=self.segment,
            finish=event.get("finish"),
            prompt_tokens=used.get("prompt_tokens"),
            completion_tokens=used.get("completion_tokens"),
            reasoning_tokens=used.get("reasoning_tokens"),
            cached_tokens=used.get("cached_tokens"),
            reasoning_chars=event.get("reasoning_chars"),
            content_chars=len(str(message.get("content") or "")),
            tool_args_chars=args_chars,
            tools=_json([c["name"] for c in called]),
            repeat_share=share,
            stream=_json(event["stream"]) if "stream" in event else None,
        )
        # Times a row couldn't say when it was written: its first call's, in older events.
        self.ledger.change(
            "UPDATE interventions SET at=? WHERE run=? AND at_call=? AND at IS NULL",
            at,
            self.run,
            n,
        )
        self.ledger.change(
            "UPDATE timeline SET at=? WHERE run=? AND at_call=? AND at IS NULL", at, self.run, n
        )
        if self.compacted_at is not None:
            self.ledger.change(
                "UPDATE timeline SET prompt_after=? WHERE run=? AND seq=?",
                used.get("prompt_tokens"),
                self.run,
                self.compacted_at,
            )
            self.compacted_at = None
        self._runaway(event, called, share, counted, round_n)
        self.last_prompt = int(used.get("prompt_tokens") or 0)
        self.calls_since_compaction += 1

    def _runaway(
        self,
        event: dict[str, Any],
        called: list[dict[str, Any]],
        share: float,
        counted: int,
        round_n: int,
    ) -> None:
        why = []
        finish = event.get("finish")
        if finish == "length":
            why.append("limit")
        elif finish == "runaway":
            why.append("cut")
        if counted >= REPEAT_LEAST_CHARS and share > self.repeat_share:
            why.append("repeats")
        if not why:
            return
        used = event.get("usage") or {}
        message = event.get("message") or {}
        completion = int(used.get("completion_tokens") or 0)
        reasoning_tokens = int(used.get("reasoning_tokens") or 0)
        content = str(message.get("content") or "")
        args_chars = sum(c["chars"] for c in called)
        unaccounted = (
            completion - reasoning_tokens - int((len(content) + args_chars) / CHARS_PER_TOKEN)
        )
        doing = {
            "tool_calls": called,
            "visible_text_ends": content[-TAIL:],
            "reasoning_ends": str(message.get("reasoning_content") or "")[-TAIL:],
            "seconds": event.get("seconds"),
            "stream": event.get("stream"),
        }
        lacked = {
            "skills_read": sorted(self.skills_read),
            "skills_not_read": sorted(set(skills.registry(self.skills_offered)) - self.skills_read),
            "compactions_so_far": self.compactions,
            "calls_since_compaction": self.calls_since_compaction,
            "prompt_tokens": used.get("prompt_tokens"),
            "last_bench_answer": self.last_bench,
            "last_tool_calls_refused": list(self.refused),
            "needs_failing": self._failing(round_n),
        }
        self.ledger.add(
            "runaways",
            run=self.run,
            n=int(event["n"]),
            why=",".join(why),
            at=event.get("at"),
            round=round_n,
            phase=self.phase,
            finish=finish,
            completion_tokens=completion,
            reasoning_tokens=reasoning_tokens,
            tokens_unaccounted=max(0, unaccounted),
            repeat_share=share,
            repeat_limit=self.repeat_share,
            doing=_json(doing),
            lacked=_json(lacked),
        )

    def _failing(self, round_n: int) -> list[str]:
        found = self.ledger.rows(
            "SELECT need FROM readings WHERE run=? AND round=? AND passed=0 ORDER BY need",
            self.run,
            round_n - 1,
        )
        return [str(r["need"]) for r in found]

    # What its tools said

    def _on_tool(self, event: dict[str, Any]) -> None:
        name = event.get("name")
        arguments = _arguments(event.get("arguments"))
        if name == "skill":
            self._skill_read(event, arguments)
        if event.get("outcome") != "ok":
            self.refused.append([str(name), str(event.get("why") or event.get("outcome"))])
            self.refusals += 1
            self.ledger.add(
                "refusals",
                run=self.run,
                seq=self.refusals,
                at_call=self.requests,
                round=self._round_of(self.requests),
                tool=str(name),
                outcome=str(event.get("outcome")),
                why=str(event.get("why") or "") or None,
            )
        elif name == "skill":
            self.skills_read.add(str(arguments.get("name")))
        elif name == "bench":
            lines = str(event.get("reply") or "").splitlines()
            self.last_bench = " ".join(lines[:2])[:TAIL]
        detail = event.get("detail") or {}
        if name == "needs" and detail.get("tried"):
            self._tried(event, detail)
        if name == "bench" and isinstance(detail, dict) and detail.get("nudge"):
            self._nudged(detail["nudge"])

    def _skill_read(self, event: dict[str, Any], arguments: dict[str, Any]) -> None:
        """Which skill a run read, at which call, and its file: how a run finds its skills."""
        self.skill_reads += 1
        detail = event.get("detail") or {}
        self.ledger.add(
            "skill_reads",
            run=self.run,
            seq=self.skill_reads,
            at_call=self.requests,
            round=self._round_of(self.requests),
            name=str(arguments.get("name")),
            file=detail.get("file") if isinstance(detail, dict) else None,
            found=int(event.get("outcome") == "ok"),
        )

    def _nudged(self, nudge: dict[str, Any]) -> None:
        """A check that kept failing, and the line its reply gained."""
        self.nudges += 1
        self.ledger.add(
            "nudges",
            run=self.run,
            seq=self.nudges,
            at_call=self.requests,
            round=self._round_of(self.requests),
            command=str(nudge["command"]),
            files=_json(nudge["files"]),
            failures=int(nudge["failures"]),
            after=int(nudge["after"]),
            calls=_json(nudge["calls"]) if nudge.get("calls") is not None else None,
        )

    def _tried(self, event: dict[str, Any], detail: dict[str, Any]) -> None:
        readings = detail.get("readings") or _tried_from_reply(str(event.get("reply") or ""))
        round_n = self._round_of(self.requests)
        for reading in readings:
            self.tries += 1
            self.ledger.add(
                "tries",
                run=self.run,
                seq=self.tries,
                at_call=self.requests,
                round=round_n,
                need=reading["need"],
                command=reading.get("command"),
                passed=int(bool(reading["passed"])),
                why=reading["why"],
                exit=reading.get("exit"),
                verdict=reading.get("verdict"),
                value=reading.get("value"),
                numbers=_json(reading["numbers"]) if reading.get("numbers") else None,
            )


def _an_old_message(content: str) -> tuple[str, str] | None:
    if content.startswith(NOTE_WORDS):
        return "note", "the people running this"
    if content.startswith(ANSWER_WORDS):
        return "answers", ANSWERS_FROM
    if content.startswith(REOPEN_WORDS):
        return "reopen_needs", "the people running this"
    return ("pick", UNKNOWN) if PICK_WORDS.match(content) else None


def _tried_from_reply(reply: str) -> list[dict[str, Any]]:
    found = []
    for line in reply.splitlines():
        match = TRIED_LINE.match(line)
        if match is not None:
            passed = match.group(2) == "would pass"
            found.append({"need": match.group(1), "passed": passed, "why": match.group(3)})
    return found


def _arguments(raw: Any) -> dict[str, Any]:
    try:
        found = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _called(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Each tool call in a reply, whole or cut off: its name, how much came, and any path in it."""
    called = []
    for call in message.get("tool_calls") or []:
        function = (call.get("function") if isinstance(call, dict) else None) or {}
        raw = str(function.get("arguments") or "")
        path = PATH_IN_ARGUMENTS.search(raw)
        called.append(
            {
                "name": str(function.get("name")),
                "chars": len(raw),
                "path": path.group(1) if path else None,
                "arguments_begin": raw[:TAIL],
                "arguments_end": raw[-TAIL:] if len(raw) > TAIL else "",
            }
        )
    return called


def seen_by_kept_files(ask: Path) -> dict[str, Any]:
    """What an ask's sheet saw, from the copies its folder keeps. Each file by what it holds."""

    def held(folder: Path) -> dict[str, str]:
        if not folder.is_dir():
            return {}
        return {
            p.name: paths.content_hash(p.name, p.read_bytes())
            for p in sorted(folder.iterdir())
            if p.is_file()
        }

    variants = ask / "variants"
    names = sorted(p.name for p in variants.iterdir() if p.is_dir()) if variants.is_dir() else []
    sheet = ask / "sheet.png"
    return {
        "variants": {name: held(variants / name) for name in names},
        "test_pieces": held(ask / "test-pieces"),
        "sheet": paths.content_hash("sheet.png", sheet.read_bytes()) if sheet.is_file() else None,
    }


def read_back(
    folder: Path, ledger: DesignLedger, repeat_share: float = REPEAT_SHARE
) -> dict[str, int]:
    """A run's records, through the tables, from what its folder holds. It only reads the folder:
    no row of the ledger is changed but for what was empty, and it can be done again."""
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    name = state["name"]
    if not ledger.rows("SELECT run FROM runs WHERE run=?", name):
        raise LookupError(f"{name} isn't in this ledger")
    recorder = Recorder(ledger, name, list(state.get("harness") or []), repeat_share)
    recorder.feed_all(transcript.events_of(folder / "events.jsonl"))
    for row in ledger.rows("SELECT ask FROM picks WHERE run=? AND seen IS NULL", name):
        ask = folder / "asks" / str(row["ask"])
        if ask.is_dir():
            ledger.change(
                "UPDATE picks SET seen=? WHERE run=? AND ask=? AND seen IS NULL",
                _json(seen_by_kept_files(ask)),
                name,
                row["ask"],
            )
    for row in ledger.rows("SELECT round FROM rounds WHERE run=? AND judged_files IS NULL", name):
        judged = folder / "rounds" / f"{int(row['round']):03}" / "judged"
        if judged.is_dir():
            ledger.change(
                "UPDATE rounds SET judged_files=? WHERE run=? AND round=? AND judged_files IS NULL",
                _json(paths.geometry_hashes(judged)),
                name,
                row["round"],
            )
    return {
        table: int(ledger.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE run=?", name)[0]["n"])  # noqa: S608
        for table in TABLES
    }
