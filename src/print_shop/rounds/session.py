"""One run's conversation: a call at a time, until an ending or a stop.

Ported from mqucifer/model-performance `cad_eval/session.py` at 22ea8b7, its base arm's shape:
reasoning goes back as it came, compaction at crew's 125k, every request rebuilds from the
records (`transcript.py`), and each change to an earlier message is a recorded rewrite.

What's different here is that the conversation outlives its stops. A `done` the checks refuse
doesn't end it, so every tool call in a turn gets its reply even after one that stops the turn.
It can stop for the Sponsor and go on days later in another process: `restore` reads back from
the events all that compaction and the counts need.

Every ending is named (`Ending`), and `docs/rounds.md` lists them. Limits start high, are recorded
on the run, and are lowered from evidence. The model isn't told them, so a run says what a lower
one would have done. There's no budget on thinking anywhere (the Sponsor).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx

from print_shop.rounds.box import Gone
from print_shop.rounds.transcript import conversation, events_of

# Crew's own line (`crew_org/session/conversation.py`), in a 262,144-token window that keeps
# 65,536 for the answer.
COMPACT_AT = 125_000
# Characters a token, for what's come since the last measured prompt, counted as `_chars` counts
# them. Fitted on the 1,122 recorded prompts of 12 runs (mqucifer/print-shop#47): the median of the
# prompt's growth against the characters' growth between calls, so the estimate reads as often low
# as high. 2.7, from `cad_eval.probe`'s one prompt, read the growth about 21% high
# (`~/print-shop/proofs/m18-ask/fit_chars.json`).
CHARS_PER_TOKEN = 3.18
COMPACTED = "(compacted: {what})"
#: A line that is a compaction's note, which a file's text never is (mqucifer/print-shop#47).
NOTE_LINE = re.compile(r"^\s*\(compacted: .*\)\s*$", re.MULTILINE)
#: Where a reply carries a file's one current copy, after compaction made the rest notes.
COPY = "\n\n(compacted: `{path}` whole, as your last edit left it. Its earlier text is notes:)\n"
COPY_AT = re.compile(r"\n\n\(compacted: `(.+?)` whole, as your last edit left it\. [^\n]*\)\n")
NUMBERED = re.compile(r"^ *(\d+)\| ?(.*)$")
#: The most a shrunk reply keeps of each line it keeps.
GIST_CHARS = 300
#: A reply that says only how its command exited (mqucifer/print-shop#39).
EXIT_ONLY = re.compile(r"^exit -?\d+(?: after [\d.]+ ?s)?$")
#: A line that names an error, as Python prints its last: `TypeError: ...`, `cq.Error: ...`.
ERROR_LINE = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)\b")
#: Where a `write` or `edit` reply says what the run after it did.
RAN = "\n\nRan "
# A picture's tokens in the next prompt, before a response reports them: `cad_eval`'s was 576.
PICTURE_TOKENS = 600
#: The reply to a call with no tool call where there's no `ask` to call: the after-action review.
QUIET = "Call a tool to go on. If you can't, `blocked` says why."
#: The tools a call that ends without acting is pointed to. `send_ask` is `ask`'s second half: an
#: `ask` only draws the sheet, and nothing reaches the Sponsor until it's sent.
ASKING = ("ask", "send_ask", "blocked")
#: The reply to a call that ends without a tool call, where `ask` and `blocked` work.
ASK_WHAT = (
    "You reasoned for {reasoning} and ended without calling a tool.{cut} That usually means "
    "something is missing: a figure, a reading, what a check's reply means, or a choice that's "
    "the Sponsor's. Say what it is with `ask`, showing what you have, then `send_ask`. If it "
    "can't be done without it, end with `blocked`, saying what's missing."
)
CUT_OFF = " It reached the answer's limit, so that reasoning isn't sent back."
ONLY_ASKING = (
    " It's happened again, {calls} after the last time, so until you've asked or ended, only "
    "`ask`, `send_ask` and `blocked` work."
)
STILL_ONLY = " Only `ask`, `send_ask` and `blocked` work until you've asked or ended."
NOT_NOW = (
    "Not carried out: only `ask`, `send_ask` and `blocked` work until you've asked what's "
    "missing or ended."
)
NOTE_AS_TEXT = (
    "Not {done}: `{key}` holds a compaction note, `{note}`, not a file's text. Earlier calls "
    "show notes like it where their text was taken out of the conversation to save room. "
    "`{path}` is as it was, and `read` gives it whole."
)
SHOWN_BEFORE = (
    "(A picture was here. Only the latest turn's pictures stay in the conversation: `read` shows "
    "any of them again.)"
)
NOT_CARRIED_OUT = "Not carried out: an earlier call in this turn stopped the turn."
#: The calls whose latest arguments are what stands: each earlier one is replaced by it, and
#: compaction makes it a note (mqucifer/print-shop#78).
REPLACED_BY_LATEST = ("needs", "ask", "breakdown")
#: Where each of those keeps what stands.
LATEST_KEPT = {
    "needs": "`record/needs.json` holds the needs as settled",
    "ask": "the latest `ask` stays whole",
    "breakdown": "`record/breakdown.md` holds the latest",
}
#: An argument this short stays as it is in a replaced call, such as an ask's `stop`.
SHORT_ARGUMENT = 80
#: A one-off script's share of the main one's changes, under which it isn't a main script: the
#: context study's rule for the main script (`findings.md`, "Limits").
MAIN_SHARE = 0.35
#: What an earlier compaction's note becomes: only the latest stays.
EARLIER_NOTE = "(compacted: an earlier compaction's note. The latest one, further on, says it now.)"
#: The old note's opening, for a run from before notes were kept by their place.
OLD_NOTE = "Compacted. Still failing, from `record/CHECKS.md`:"
#: Said in the note when the newest turn's reasoning didn't fit and isn't sent back.
WITHHELD = (
    "Your last turn's reasoning, about {tokens:,} tokens, didn't fit in the conversation even "
    "compacted, so it isn't sent back: its tool calls and their replies are. If it worked out a "
    "figure or a plan you still need, keep it with `fact` or say it again in your next reasoning."
)
#: The reasoning-trim switch's settings, for an arm of the operator's evals: off unless a run
#: sets them (mqucifer/print-shop#78, the context study's option (e)).
TRIM_AT = 40_000
TRIM_KEEP = 3
RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)


@dataclass(frozen=True)
class Limits:
    """All high to start with (the Sponsor), recorded on the run, and lowered from evidence."""

    calls: int = 2000
    rounds: int = 200
    model_seconds: float = 14 * 24 * 3600
    # Rounds in a row refused with nothing in the working directory changed.
    stuck_rounds: int = 10
    # Replies in a row with no tool call.
    quiet: int = 10
    # A call with no tool call this many calls or fewer after the last one gets a request whose
    # only tools are `ask`, `send_ask` and `blocked`. On the 12 recorded runs, the repeats inside
    # the two loops (`suncatcher-2` 248-261, `eval-tip-1` 70-114) came 1 to 14 calls apart, and
    # the one gap outside them was 141 (`~/print-shop/proofs/m18-ask/quiet_calls.json`).
    ask_within: int = 15
    # New conversations from the record, each after one that wouldn't fit.
    restarts: int = 50
    # A failed call is tried this many times more before the run ends on it.
    call_retries: int = 8
    retry_wait_s: float = 15.0
    # A run's or a bench command's seconds: the container's own limit.
    command_seconds: int = 600
    work_gb: float = 40.0
    max_tokens: int = 65536
    compact_at: int = COMPACT_AT
    # A call whose reasoning repeats earlier text past this share gets a row for review. It decides
    # nothing, and nothing cuts a call for it.
    review_repeat_share: float = 0.5
    # Past reasoning, in tokens, over which every turn but the last `trim_keep` loses its
    # reasoning, in one step: 0 is off, as it is unless an eval's arm turns it on.
    trim_reasoning_at: int = 0
    trim_keep: int = TRIM_KEEP
    # The after-action review's calls, after the approval: it ends on `after_action`, and this is
    # only so a review that never calls it ends by name.
    after_action_calls: int = 30

    def recorded(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ending:
    """How a session stopped. `name` is one of `docs/rounds.md`'s."""

    name: str
    said: dict[str, Any] = field(default_factory=dict)


class WorkFull(RuntimeError):
    """The working directory went over its limit."""


@dataclass
class Judged:
    """One tool call's outcome, and the text the model gets back."""

    name: str
    outcome: str  # ok, malformed, ill_typed or failed
    reply: str
    why: str = ""
    # Pictures to show the model, as (a name, base64 PNG).
    images: list[tuple[str, str]] = field(default_factory=list)
    # What the run's events keep beside the call.
    detail: dict[str, Any] = field(default_factory=dict)
    # A tool that stops the turn (`done` that met the needs, `ask`, `questions`, `blocked`).
    ending: Ending | None = None


class Tools(Protocol):
    specs: list[dict[str, Any]]

    def call(self, name: Any, raw: Any) -> Judged: ...


@dataclass
class Session:
    tools: Tools
    send: Callable[[dict[str, Any]], dict[str, Any]]
    alias: str
    events: Path
    limits: Limits = field(default_factory=Limits)
    stop_file: Path | None = None
    sleep: Callable[[float], None] = time.sleep
    messages: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0
    requests: int = 0
    views: int = 0
    compactions: int = 0
    model_s: float = 0.0
    reasoning_chars: int = 0
    last_prompt: int = 0
    last_chars: int = 0
    logged: int = 0
    logged_tools: str = ""
    logged_params: dict[str, Any] = field(default_factory=dict)
    usage: Counter[str] = field(default_factory=Counter)
    outcomes: Counter[str] = field(default_factory=Counter)
    by_tool: Counter[str] = field(default_factory=Counter)
    runaways: list[dict[str, int]] = field(default_factory=list)
    # The last call that ended without a tool call, and while it holds, the only tools offered.
    quiet_at: int = 0
    only: tuple[str, ...] = ()
    last_seconds: float = 0.0
    # Each tool reply's facts, by its message's index, for compaction.
    replies: dict[int, dict[str, Any]] = field(default_factory=dict)
    # Told each event as it's kept, for the ledger.
    heard: Callable[[dict[str, Any]], None] | None = None
    # Asked right after a compaction for what to say before the next call: the run's facts, open
    # needs, what a person last asked and its failing checks (mqucifer/print-shop#45, #78). Empty
    # text says nothing.
    after_compaction: Callable[[], str] | None = None
    # Where this conversation's compaction notes are, so only the latest stays whole.
    notes_at: list[int] = field(default_factory=list)

    def begin(self, *start: dict[str, Any]) -> None:
        """A new conversation. An earlier one in the same run stays in the records."""
        self.messages = list(start)
        self.replies = {}
        self.notes_at = []
        self.logged = 0
        self.last_prompt = 0
        self.last_chars = 0

    def say(self, text: str) -> None:
        """A message to the model from outside its tools: an answer, a pick, a note."""
        self.messages.append({"role": "user", "content": text})

    def restore(self) -> None:
        """Back from the events, to go on after a stop or a crash. A crash loses at most the last
        call's answer, and that call is made again: what its tools did is on the disk."""
        self.messages = conversation(self.events)
        self.logged = len(self.messages)
        # A run from before `_sendable` may hold a call the server refuses to read back.
        for i, m in enumerate(self.messages):
            calls = m.get("tool_calls")
            if calls and _sendable(calls) != calls:
                self.messages[i] = {**m, "tool_calls": _sendable(calls)}
                self._record("rewrite", index=i, message=self.messages[i])
        for event in events_of(self.events):
            kind = event.get("kind")
            if kind == "request":
                self.requests = event["n"]
                if "tools" in event:
                    self.logged_tools = json.dumps(event["tools"], sort_keys=True)
                if "params" in event:
                    self.logged_params = event["params"]
                if event["from"] == 0:
                    self.replies = {}
                    self.notes_at = []
            elif kind == "call":
                self.calls += 1
                self.model_s += float(event.get("seconds") or 0)
                self.reasoning_chars += int(event.get("reasoning_chars") or 0)
                self.usage.update(event.get("usage") or {})
                self.last_prompt = int((event.get("usage") or {}).get("prompt_tokens") or 0)
                self.last_chars = int(event.get("sent_chars") or 0)
                if event.get("finish") in ("length", "runaway"):
                    self.runaways.append(event.get("runaway") or {})
            elif kind == "tool":
                self.outcomes[event["outcome"]] += 1
                if isinstance(event.get("name"), str):
                    self.by_tool[event["name"]] += 1
                self.replies[int(event["index"])] = event["info"]
            elif kind == "compacted":
                self.compactions += 1
            elif kind == "compaction_note":
                self.notes_at.append(int(event["index"]))
            elif kind == "picture":
                self.views += 1
            elif kind == "quiet_reply":
                self.quiet_at = int(event["n"])
                self.only = tuple(event.get("only") or ())
            elif kind == "ended":
                # Each stop ends what a quiet call asked for: the model asked, or ended.
                self.only = ()

    def go(self) -> Ending:
        """Calls until something stops it."""
        quiet = 0
        while True:
            if self.stop_file is not None and self.stop_file.exists():
                return self._ended(Ending("stopped", {"by": "stop file"}))
            if self.calls >= self.limits.calls:
                return self._ended(Ending("call_cap", {"limit": self.limits.calls}))
            if self.model_s >= self.limits.model_seconds:
                return self._ended(Ending("model_time", {"limit": self.limits.model_seconds}))
            answer, ending = self._call()
            if ending is not None:
                return self._ended(ending)
            message = answer["choices"][0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            kept: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("reasoning_content"):
                # Each past turn's reasoning goes back as it came: `cad_eval.probe` found the
                # served template keeps it, across a tool reply and an inserted user message.
                kept["reasoning_content"] = message["reasoning_content"]
            if tool_calls:
                kept["tool_calls"] = _sendable(tool_calls)
            self.messages.append(kept)
            finish = answer["choices"][0].get("finish_reason")
            if finish == "length" and kept.get("reasoning_content"):
                self._withhold_reasoning(len(self.messages) - 1)
            if not tool_calls:
                quiet += 1
                if quiet >= self.limits.quiet:
                    return self._ended(Ending("no_tool_call", {"limit": self.limits.quiet}))
                self.say(self._quiet_reply(answer, message, finish))
                continue
            quiet = 0
            try:
                ending = self._tools(tool_calls)
            except Gone as gone:
                return self._ended(Ending("container_failed", {"error": str(gone)[:400]}))
            except WorkFull as full:
                return self._ended(Ending("work_full", {"error": str(full)}))
            if ending is not None:
                return self._ended(ending)

    def _withhold_reasoning(self, index: int, why: str = "cut_off") -> None:
        """A call cut off at `max_tokens` goes back without its reasoning, its text and calls as
        they came. On `suncatcher-2` each such call's 65k tokens were resent whole, as the newest
        turn's, and calls 258-262 compacted once a call. The `call` event keeps all of it, and
        the change is a rewrite, kept after a snapshot so the records can replay it. A newest
        turn too big to fit once compacted goes back the same way (`oversize`)."""
        self.snapshot()
        self.messages[index] = {**self.messages[index], "reasoning_content": ""}
        self._record("rewrite", index=index, message=self.messages[index], why=why)

    def _withhold_newest(self) -> int:
        """The newest turn's reasoning, withheld when the conversation won't fit even compacted,
        and its tokens, about: 0 when it had none (mqucifer/print-shop#78). Two of the three
        `context_full` endings in the context study came from one long newest turn, which
        compaction keeps whole: pi5's call 147 reasoned for 30k tokens, and 149k came down only
        to 141k. A restart lost all its reasoning instead, and the run's plan with it."""
        last = max((i for i, m in enumerate(self.messages) if m["role"] == "assistant"), default=-1)
        if last < 0 or not self.messages[last].get("reasoning_content"):
            return 0
        tokens = int(len(str(self.messages[last]["reasoning_content"])) / CHARS_PER_TOKEN)
        self._withhold_reasoning(last, "oversize")
        return tokens

    def _trim_reasoning(self) -> None:
        """The reasoning-trim switch, off unless `trim_reasoning_at` is set: once past turns'
        reasoning passes it, every turn but the last `trim_keep` loses its reasoning, all in one
        step, so the prefix cache misses once and not on every call (the context study's option
        (e)). It's for an arm of the operator's evals, and nothing turns it on by default."""
        at = self.limits.trim_reasoning_at
        if not at:
            return
        turns = [i for i, m in enumerate(self.messages) if m["role"] == "assistant"]
        thought = sum(len(str(self.messages[i].get("reasoning_content") or "")) for i in turns)
        if thought / CHARS_PER_TOKEN <= at:
            return
        keep = max(self.limits.trim_keep, 0)
        cut = [i for i in turns[: len(turns) - keep] if self.messages[i].get("reasoning_content")]
        if not cut:
            return
        before = self.estimate()
        self.snapshot()
        for i in cut:
            self.messages[i] = {**self.messages[i], "reasoning_content": ""}
            self._record("rewrite", index=i, message=self.messages[i], why="trimmed")
        left = sum(len(str(self.messages[i].get("reasoning_content") or "")) for i in turns)
        self._record(
            "reasoning_trimmed",
            before_call=self.requests + 1,
            turns=len(cut),
            kept=keep,
            reasoning_before=int(thought / CHARS_PER_TOKEN),
            reasoning_after=int(left / CHARS_PER_TOKEN),
            estimated=before,
            estimated_after=self.estimate(),
            limit=at,
        )

    def _asking(self) -> tuple[str, ...]:
        """The asking tools this session offers: none in the after-action review."""
        offered = {s["function"]["name"] for s in self.tools.specs}
        return tuple(name for name in ASKING if name in offered)

    def _offered(self) -> list[dict[str, Any]]:
        if not self.only:
            return self.tools.specs
        return [s for s in self.tools.specs if s["function"]["name"] in self.only]

    def _quiet_reply(self, answer: dict[str, Any], message: dict[str, Any], finish: Any) -> str:
        """What a call that ended without a tool call is told: what's missing is asked for, not
        action (mqucifer/print-shop#47). On the records, 12 of the 15 such calls followed 16k
        tokens or more of reasoning, and 10 sat in the two loops. A second one soon after gets a
        request whose only tools are the asking ones. Nothing cuts a call or limits thinking."""
        asking = self._asking()
        if not {"ask", "blocked"} <= set(asking):
            return QUIET
        n = self.requests
        used = usage(answer)
        tokens = used.get("reasoning_tokens")
        if tokens is None:
            tokens = int(len(str(message.get("reasoning_content") or "")) / CHARS_PER_TOKEN)
        minutes = self.last_seconds / 60
        took = f"{minutes:.0f} minutes" if minutes >= 2 else f"{self.last_seconds:.0f} seconds"
        reasoning = f"{tokens:,} tokens ({took})"
        again = bool(self.quiet_at) and n - self.quiet_at <= self.limits.ask_within
        reply = ASK_WHAT.format(reasoning=reasoning, cut=CUT_OFF if finish == "length" else "")
        if again:
            self.only = asking
            gap = n - self.quiet_at
            reply += ONLY_ASKING.format(calls=f"{gap} call{'' if gap == 1 else 's'}")
        elif self.only:
            reply += STILL_ONLY
        self._record(
            "quiet_reply",
            n=n,
            reasoning_tokens=tokens,
            seconds=round(self.last_seconds, 1),
            finish=finish,
            after=n - self.quiet_at if self.quiet_at else None,
            only=list(self.only) or None,
            reply=reply,
        )
        self.quiet_at = n
        return reply

    def rewrite(self, index: int, content: Any) -> None:
        """Change an earlier message's content, and keep the change in the records."""
        self.messages[index] = {**self.messages[index], "content": content}
        self._record("rewrite", index=index, message=self.messages[index])

    def _request(self, body: dict[str, Any]) -> None:
        """What's new in this call's request, as `transcript.rebuild` replays it."""
        wire = getattr(self.send, "wire", None)
        sent = wire(body) if callable(wire) else body
        params = {k: v for k, v in sent.items() if k not in ("messages", "tools")}
        tools = json.dumps(sent["tools"], sort_keys=True)
        self.requests += 1
        event: dict[str, Any] = {
            "n": self.requests,
            "from": self.logged,
            "append": copy.deepcopy(self.messages[self.logged :]),
        }
        if tools != self.logged_tools:
            event["tools"] = sent["tools"]
            event["tools_sha"] = hashlib.sha256(tools.encode()).hexdigest()
            self.logged_tools = tools
        if params != self.logged_params:
            event["params"] = params
            self.logged_params = params
        self._record("request", **event)
        self.logged = len(self.messages)

    def _chars(self) -> int:
        """The messages' size as text, a picture counted as its tokens, not its base64."""
        pictures = 0
        shown = []
        for m in self.messages:
            if isinstance(m.get("content"), list):
                pictures += sum(1 for p in m["content"] if p.get("type") == "image_url")
                m = {**m, "content": [p for p in m["content"] if p.get("type") != "image_url"]}
            shown.append(m)
        return len(json.dumps(shown)) + int(pictures * PICTURE_TOKENS * CHARS_PER_TOKEN)

    def estimate(self) -> int:
        """The next prompt's tokens: the last one's, measured, plus what's come since."""
        if not self.last_prompt:
            return int((self._chars() + len(json.dumps(self.tools.specs))) / CHARS_PER_TOKEN)
        return self.last_prompt + int((self._chars() - self.last_chars) / CHARS_PER_TOKEN)

    def compact(self) -> None:
        """crew's compaction on this conversation, each change a recorded rewrite.

        - Every assistant turn before the newest loses its reasoning.
        - **Each file's latest full text stays whole** (`cad_eval`'s review of #41): its last
          `write` (the call), or its last whole `read` (the reply).
        - **Once it's been edited, it's one current copy** (mqucifer/print-shop#47): the reply to
          its last edit carries the file as the edits left it, numbered as `read` shows it, and
          its `write` and edits become notes. Kept as a text and every edit since, it was 22.0k
          tokens on `suncatcher-2` against 11.7k as one copy, and four of the seven refused edits
          after its compactions were on one file kept as a write and 25 edits. Where the copy
          can't be rebuilt from the conversation, or an edit is in the newest turn, the text and
          every edit since stay whole, call and reply, as before.
        - The latest reply of `run`, of `bench` and of `done` stays whole, and so do the newest
          turn's. A run's reply through `write` or `edit` counts as `run`'s.
        - Every other `write` or `edit` call keeps its own keys, its text a note, and every other
          tool reply shrinks to what failed and why (`gist`): a bench answer's verdict and its
          reason, or a run's last error, never only its exit code (mqucifer/print-shop#39).
        - What the Sponsor said is never touched: it's in user messages. So is the start, with
          the required skills it gives whole (mqucifer/print-shop#61): on `suncatcher-3` a skill
          read with `skill` was lost to the next of 31 compactions, and read again.

        **What a later call replaced goes** (mqucifer/print-shop#78). On pi5 at call 138, 29% of
        what compaction kept was replaced content: a `needs` list sent 7 times stayed 7 times,
        26.8k tokens with 22.1k replaced, beside 11 one-off scripts and 18 restated lists. The
        context study's estimate is that this takes that floor from 109k tokens to about 70k.
        - Only the latest `needs`, `ask` and `breakdown` call keeps its arguments whole. Each
          earlier one keeps its short arguments, and the rest becomes a note.
        - A one-off script, written once, run once and left since, becomes a note with its run's
          gist. The main scripts stay: any `done` names, and any changed at least 35% as often as
          the most changed one.
        - Only the latest compaction's note stays: each earlier one becomes a note.
        """
        last = max((i for i, m in enumerate(self.messages) if m["role"] == "assistant"), default=-1)
        kept, copies = self._files(last)
        one_offs = self._one_offs(last, kept)
        kept -= {("call", i, k) for i, k in one_offs}
        replaced = self._replaced(last)
        latest: dict[str, int] = {}
        for i, info in self.replies.items():
            for name in ("run", "bench", "done"):
                if info["name"] == name or (name == "run" and info["ran"]):
                    latest[name] = max(latest.get(name, -1), i)
        stay = set(latest.values())
        for i, m in enumerate(self.messages):
            if m["role"] == "assistant" and i != last:
                changed = dict(m)
                if m.get("reasoning_content"):
                    changed["reasoning_content"] = ""
                calls = []
                for k, called in enumerate(m.get("tool_calls") or []):
                    function = called.get("function") or {}
                    called_name = function.get("name")
                    if called_name in ("write", "edit") and ("call", i, k) not in kept:
                        args = _arguments(function.get("arguments"))
                        texts = ("content",) if called_name == "write" else ("old", "new")
                        noted = all(NOTE_LINE.fullmatch(str(args[t])) for t in texts if t in args)
                        if not noted:
                            size = len(str(function.get("arguments")))
                            note = one_offs.get((i, k)) or COMPACTED.format(
                                what=f"{size} characters"
                            )
                            short = {**args, **{t: note for t in texts if t in args}}
                            called = {
                                **called,
                                "function": {**function, "arguments": json.dumps(short)},
                            }
                    elif (i, k) in replaced:
                        called = _noted_call(called, replaced[(i, k)])
                    calls.append(called)
                if calls:
                    changed["tool_calls"] = calls
                if changed != m:
                    self.messages[i] = changed
                    self._record("rewrite", index=i, message=changed)
            elif m["role"] == "tool" and i < last and ("reply", i, -1) not in kept:
                content = str(m.get("content") or "")
                found = COPY_AT.search(content)
                body = content[: found.start()] if found else content
                if i not in stay and not body.startswith("(compacted:"):
                    tool = (self.replies.get(i) or {}).get("name", "tool")
                    body = COMPACTED.format(what=f"{tool}: {gist(body)}")
                if i in copies:
                    path, text = copies[i]
                    body += COPY.format(path=path) + _numbered(text)
                if body != content:
                    self.rewrite(i, body)
        for i in self.notes_at:
            if i < len(self.messages) and self.messages[i].get("content") != EARLIER_NOTE:
                self.rewrite(i, EARLIER_NOTE)
        for i, m in enumerate(self.messages):
            # A run from before notes were kept by their place: its notes, by how they open.
            if m["role"] == "user" and str(m.get("content") or "").startswith(OLD_NOTE):
                self.rewrite(i, EARLIER_NOTE)
        self.compactions += 1

    def _replaced(self, last: int) -> dict[tuple[int, int], str]:
        """Each `needs`, `ask` and `breakdown` call a later one of its name replaced, by (message,
        call), and what it says in place of its arguments."""
        found: dict[str, list[tuple[int, int]]] = {}
        for i, m in enumerate(self.messages[:last]):
            if m["role"] != "assistant":
                continue
            for k, called in enumerate(m.get("tool_calls") or []):
                name = (called.get("function") or {}).get("name")
                if name in REPLACED_BY_LATEST:
                    found.setdefault(name, []).append((i, k))
        for m in self.messages[last:]:
            for called in m.get("tool_calls") or []:
                name = (called.get("function") or {}).get("name")
                if name in REPLACED_BY_LATEST:
                    # The newest turn's is the latest, and every earlier one is replaced.
                    found.setdefault(name, []).append((-1, -1))
        replaced: dict[tuple[int, int], str] = {}
        for name, at in found.items():
            for i, k in at[:-1]:
                replaced[(i, k)] = f"a later `{name}` replaced this: {LATEST_KEPT[name]}"
        return replaced

    def _one_offs(self, last: int, kept: set[tuple[str, int, int]]) -> dict[tuple[int, int], str]:
        """Each one-off script's `write`, by (message, call), and the note it becomes: a `.py`
        file written once, never edited, run once and left since, that isn't a main script.
        A main script is one a `done` names, or one changed at least 35% as often as the most
        changed one, so while no script has been changed three times, none is a one-off. One
        whose run is the latest `run` reply, or whose text stays whole as a `read`, isn't
        either."""
        writes: dict[str, list[tuple[int, int]]] = {}
        changes: Counter[str] = Counter()
        runs: dict[str, list[int]] = {}
        main: set[str] = set()
        for i, m in enumerate(self.messages):
            for k, called in enumerate(m.get("tool_calls") or []):
                function = called.get("function") or {}
                name = function.get("name")
                args = _arguments(function.get("arguments"))
                path = str(args.get("path"))
                if name == "done" and isinstance(args.get("script"), str):
                    main.add(args["script"])
                if name not in ("write", "edit", "run"):
                    continue
                reply = self._reply_index(i, k)
                ran = (self.replies.get(reply) or {}).get("ran")
                if name in ("write", "edit"):
                    changes[path] += 1
                    if name == "write":
                        writes.setdefault(path, []).append((i, k))
                    script = args.get("run_script")
                    if ran and isinstance(script, str):
                        runs.setdefault(script, []).append(reply)
                        continue
                if ran:
                    runs.setdefault(path, []).append(reply)
        most = max(changes.values(), default=0)
        main |= {p for p, n in changes.items() if n >= MAIN_SHARE * most}
        latest_run = max(
            (i for i, info in self.replies.items() if info["name"] == "run" or info["ran"]),
            default=-1,
        )
        found: dict[tuple[int, int], str] = {}
        for path, at in writes.items():
            ran_at = runs.get(path, [])
            if (
                not path.endswith(".py")
                or path in main
                or len(at) != 1
                or changes[path] != 1
                or len(ran_at) != 1
                or ran_at[0] >= last
                or ran_at[0] == latest_run
                or ("call", *at[0]) not in kept
            ):
                continue
            said = gist(str(self.messages[ran_at[0]].get("content") or ""))
            what = f"`{path}`, a one-off script, run once and left since; it said: {said}"
            found[at[0]] = COMPACTED.format(what=f"{what}. `read` gives it whole")
        return found

    def _files(self, last: int) -> tuple[set[tuple[str, int, int]], dict[int, tuple[str, str]]]:
        """Each file's latest full text, what stays whole of it as (kind, message, call), and the
        files that are one current copy instead: by the reply that carries it, its path and its
        text, rebuilt from its text in the conversation and each edit since."""
        full: dict[str, set[tuple[str, int, int]]] = {}
        base: dict[str, tuple[str, int, int]] = {}
        text: dict[str, str | None] = {}
        asked: dict[int, tuple[str, dict[str, Any]]] = {}
        for i, m in enumerate(self.messages):
            for k, called in enumerate(m.get("tool_calls") or []):
                function = called.get("function") or {}
                args = _arguments(function.get("arguments"))
                path = str(args.get("path"))
                if function.get("name") == "write":
                    full[path] = {("call", i, k)}
                    base[path] = ("call", i, k)
                    content = args.get("content")
                    known = isinstance(content, str) and not NOTE_LINE.search(content)
                    text[path] = content if known else None
                    asked[self._reply_index(i, k)] = ("write", args)
                elif function.get("name") == "edit":
                    full.setdefault(path, set()).add(("call", i, k))
                    asked[self._reply_index(i, k)] = ("edit", args)
            if m["role"] != "tool":
                continue
            info = self.replies.get(i) or {}
            content = str(m.get("content") or "")
            what, args = asked.pop(i, ("", {}))
            if what == "write" and not info.get("ok"):
                text[str(args.get("path"))] = None
            if info.get("ok") and info["name"] == "read" and info["whole"]:
                path = str(info["path"])
                full[path] = {("reply", i, -1)}
                base[path] = ("reply", i, -1)
                text[path] = _from_read(content)
            elif info.get("ok") and info["name"] == "edit":
                path = str(info["path"])
                full.setdefault(path, set()).add(("reply", i, -1))
                text[path] = _edited(text.get(path), args) if what == "edit" else None
                found = COPY_AT.search(content)
                if found is not None and found.group(1) == path:
                    # An earlier compaction's copy: the file as it stood after this edit.
                    full[path] = {("reply", i, -1)}
                    base[path] = ("reply", i, -1)
                    text[path] = _from_numbered(content[found.end() :])
        kept: set[tuple[str, int, int]] = set()
        copies: dict[int, tuple[str, str]] = {}
        for path, members in full.items():
            edited = [
                i
                for kind, i, _ in members
                if kind == "reply"
                and (kind, i, -1) != base.get(path)
                and (self.replies.get(i) or {}).get("name") == "edit"
            ]
            current = text.get(path)
            if current is None or not edited or any(i >= last for _, i, _ in members):
                kept |= members
            else:
                copies[max(edited)] = (path, current)
        return kept, copies

    def _reply_index(self, i: int, k: int) -> int:
        """Where the reply to message `i`'s `k`th call is: by its id, or else by its place."""
        call_id = (self.messages[i].get("tool_calls") or [{}])[k].get("id")
        j = i + 1
        while j < len(self.messages) and self.messages[j]["role"] == "tool":
            if call_id is not None and self.messages[j].get("tool_call_id") == call_id:
                return j
            j += 1
        return i + 1 + k

    def _call(self) -> tuple[dict[str, Any], Ending | None]:
        self._trim_reasoning()
        if self.last_prompt and self.estimate() >= self.limits.compact_at:
            before = self.estimate()
            self.compact()
            compacted = self.estimate()
            withheld = 0
            if compacted >= self.limits.compact_at:
                withheld = self._withhold_newest()
            self._record(
                "compacted",
                estimated=before,
                estimated_after=self.estimate(),
                prompt_before=self.last_prompt,
                before_call=self.requests + 1,
                **({"withheld_reasoning": withheld, "compacted_to": compacted} if withheld else {}),
            )
            restated = self.after_compaction() if self.after_compaction is not None else ""
            if withheld:
                restated = "\n\n".join(
                    one for one in (restated, WITHHELD.format(tokens=withheld)) if one
                )
            if restated:
                self.say(restated)
                self.notes_at.append(len(self.messages) - 1)
                self._record("compaction_note", index=len(self.messages) - 1)
            if self.estimate() >= self.limits.compact_at:
                full = {"estimated": self.estimate(), "limit": self.limits.compact_at}
                return {}, Ending("context_full", full)
        sent_chars = self._chars()
        body = {
            "model": self.alias,
            "messages": self.messages,
            "tools": self._offered(),
            "tool_choice": "auto",
            "max_tokens": self.limits.max_tokens,
        }
        self._request(body)
        n = self.requests
        at = datetime.now(UTC)
        began = time.monotonic()
        answer: dict[str, Any] = {}
        keep = getattr(self.send, "keep_stream", None)
        if callable(keep):
            keep(self.events.parent / "streams" / f"{n}.jsonl")
        for attempt in range(self.limits.call_retries + 1):
            try:
                answer = self.send(body)
                break
            except Exception as exc:  # noqa: BLE001 - any failed call has a named ending
                said: dict[str, Any] = {"error": type(exc).__name__, "attempts": attempt + 1}
                status = None
                if isinstance(exc, httpx.HTTPStatusError):
                    # The status only: the body can echo the request.
                    status = said["status"] = exc.response.status_code
                    if status == 400 and self.estimate() >= self.limits.compact_at:
                        said.update(estimated=self.estimate(), limit=self.limits.compact_at)
                        return {}, Ending("context_full", said)
                passing = status in RETRY_STATUS or isinstance(
                    exc, httpx.TransportError | json.JSONDecodeError
                )
                if not passing or attempt >= self.limits.call_retries:
                    self.model_s += time.monotonic() - began
                    return {}, Ending("call_failed", said)
                self._record("call_retry", n=n, **said)
                self.sleep(self.limits.retry_wait_s * (attempt + 1))
        took = time.monotonic() - began
        self.last_seconds = took
        self.model_s += took
        self.calls += 1
        choices = answer.get("choices") if isinstance(answer, dict) else None
        message = (choices or [{}])[0].get("message") or {}
        thought = len(str(message.get("reasoning_content") or ""))
        self.reasoning_chars += thought
        used = usage(answer)
        self.last_prompt = int(used.get("prompt_tokens") or 0)
        self.last_chars = sent_chars
        self.usage.update(used)
        finish = (choices or [{}])[0].get("finish_reason")
        runaway = None
        if finish in ("length", "runaway"):
            reasoning = used.get("reasoning_tokens", 0)
            runaway = {
                "reasoning": reasoning,
                "content": used.get("completion_tokens", 0) - reasoning,
            }
            self.runaways.append(runaway)
        self._record(
            "call",
            n=n,
            response_id=answer.get("id") if isinstance(answer, dict) else None,
            at=at.isoformat(timespec="seconds"),
            seconds=round(took, 1),
            sent_chars=sent_chars,
            reasoning_chars=thought,
            served=dict(getattr(self.send, "served", None) or {}),
            finish=finish,
            message=message,
            usage=used,
            **({"runaway": runaway} if runaway else {}),
            **(
                {"stream": answer["stream"]}
                if isinstance(answer, dict) and "stream" in answer
                else {}
            ),
        )
        if not choices:
            return {}, Ending("call_failed", {"error": "no choices"})
        return answer, None

    def _tools(self, tool_calls: list[Any]) -> Ending | None:
        pictures: list[tuple[str, str]] = []
        ending: Ending | None = None
        for call in tool_calls:
            function = (call.get("function") if isinstance(call, dict) else None) or {}
            name = function.get("name")
            call_id = call.get("id") if isinstance(call, dict) else None
            if ending is not None:
                # Every call needs its reply: this conversation goes on after a stop.
                self.messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": NOT_CARRIED_OUT}
                )
                continue
            args = _arguments(function.get("arguments"))
            judged = self._refused(name, args) or self.tools.call(name, function.get("arguments"))
            self.outcomes[judged.outcome] += 1
            if isinstance(name, str):
                self.by_tool[name] += 1
            info = {
                "name": name,
                "path": args.get("path"),
                "ok": judged.outcome == "ok",
                "ran": judged.outcome == "ok"
                and (name == "run" or "\n\nRan " in str(judged.reply or "")),
                # A read of the whole file, not part of it (`cad_eval`'s second review of #41).
                "whole": bool(judged.detail.get("whole")) if name == "read" else False,
            }
            index = len(self.messages)
            self.replies[index] = info
            self._record(
                "tool",
                index=index,
                info=info,
                name=name,
                outcome=judged.outcome,
                why=judged.why,
                arguments=function.get("arguments"),
                reply=judged.reply,
                **({"detail": judged.detail} if judged.detail else {}),
            )
            self.messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": judged.reply or "ok"}
            )
            for picture in judged.images:
                pictures.append(picture)
                self._keep_picture(picture)
            ending = judged.ending
        if pictures:
            self._show(pictures)
        return ending

    def _refused(self, name: Any, args: dict[str, Any]) -> Judged | None:
        """What the session refuses before a tool sees it: any tool but the asking ones while only
        they're offered, and a `write` or `edit` whose text holds a compaction's note. On
        `eval-tip-1` at call 109 the model wrote a file whose whole text was a note, as earlier
        `write` calls looked after compaction (mqucifer/print-shop#47)."""
        if self.only and name not in self.only:
            return Judged(str(name), "failed", NOT_NOW, "ask_only")
        if name not in ("write", "edit"):
            return None
        for key in ("content", "new", "old"):
            said = args.get(key)
            found = NOTE_LINE.search(said) if isinstance(said, str) else None
            if found is not None:
                done = "written" if name == "write" else "changed"
                note = _cut(found.group(0).strip())
                reply = NOTE_AS_TEXT.format(done=done, key=key, note=note, path=args.get("path"))
                return Judged(str(name), "failed", reply, "compaction_note")
        return None

    def _keep_picture(self, picture: tuple[str, str]) -> None:
        """A picture the model was shown, kept in the run's records beside its events."""
        views = self.events.parent / "views"
        views.mkdir(exist_ok=True)
        self.views += 1
        (views / f"{self.views}.png").write_bytes(base64.b64decode(picture[1]))
        self._record("picture", n=self.views, name=picture[0])

    def _show(self, pictures: list[tuple[str, str]]) -> None:
        """A turn's pictures, to the model as a message of their own: a tool's reply can't carry
        an image. Earlier ones are replaced by a note, through a rewrite the records keep."""
        for index, message in enumerate(self.messages):
            if message.get("role") == "user" and isinstance(message.get("content"), list):
                self.rewrite(index, SHOWN_BEFORE)
        names = ", ".join(f"`{name}`" for name, _ in pictures)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": f"What you asked to see: {names}"}
        ]
        for _, image in pictures:
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}}
            )
        self.messages.append({"role": "user", "content": content})

    def _record(self, kind: str, **detail: Any) -> None:
        event = {"kind": kind, "t": datetime.now(UTC).isoformat(timespec="seconds"), **detail}
        with self.events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        if self.heard is not None:
            self.heard(event)

    def snapshot(self) -> None:
        """What's come since the last request, so a resume goes on from this very message."""
        if self.logged < len(self.messages) or self.logged == 0:
            self._record(
                "snapshot",
                **{"from": self.logged, "append": copy.deepcopy(self.messages[self.logged :])},
            )
            self.logged = len(self.messages)

    def _ended(self, ending: Ending) -> Ending:
        self.only = ()
        self.snapshot()
        self._record("ended", name=ending.name, said=ending.said)
        return ending


def _noted_call(called: dict[str, Any], why: str) -> dict[str, Any]:
    """A replaced call with its short arguments as they came and each long one a note."""
    function = called.get("function") or {}
    args = _arguments(function.get("arguments"))
    short: dict[str, Any] = {}
    for key, value in args.items():
        said = json.dumps(value)
        if len(said) <= SHORT_ARGUMENT or (
            isinstance(value, str) and NOTE_LINE.fullmatch(value)
        ):
            short[key] = value
        else:
            short[key] = COMPACTED.format(what=f"{len(said)} characters; {why}")
    if short == args:
        return called
    return {**called, "function": {**function, "arguments": json.dumps(short)}}


def _cut(line: str) -> str:
    return line if len(line) <= GIST_CHARS else f"{line[:GIST_CHARS]}…"


def _ran_gist(text: str) -> str:
    """A run's reply, `exit N after S s` and its output, as its exit and its last error: the last
    line that names one, or the last line it printed."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    head, rest = lines[0], lines[1:]
    errors = [line for line in rest if ERROR_LINE.match(line)]
    last = errors[-1] if errors else (rest[-1] if rest else "it printed nothing")
    return f"{head}; {_cut(last)}"


def gist(content: str) -> str:
    """What a tool reply keeps when compaction shrinks it: what failed and why. On `suncatcher-2`
    139 of the shrunk replies said only `exit 1`, and after a summary the model couldn't compare
    its failures (mqucifer/print-shop#39).

    - A bench answer keeps its exit and its verdict line, with the reason, or its usage error.
    - A run keeps its exit and its last error, or the last line it printed.
    - A `write` or `edit` that ran a script keeps its first line and what the run did.
    - Anything else keeps its first line, as before, and an exit alone gains the last line said."""
    text = content.strip()
    if not text:
        return ""
    body, ran, after = text.partition(RAN)
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    first = lines[0]
    if EXIT_ONLY.match(first):
        said = next(
            (line for line in lines if line.startswith(("verdict:", "usage error:"))), None
        )
        if said is not None:
            kept = f"{first}, {_cut(said)}"
        elif " after " in first:
            kept = _ran_gist(body)
        else:
            kept = f"{first}; {_cut(lines[-1]) if len(lines) > 1 else 'nothing else was said'}"
    else:
        kept = _cut(first)
    if ran:
        script, _, output = after.partition(": ")
        kept += f" Ran {script}: {_ran_gist(output)}"
    return kept


def _numbered(text: str) -> str:
    """A file's lines with their numbers, as `read` shows them."""
    return "\n".join(f"{n:5}| {line}" for n, line in enumerate(text.splitlines(), 1))


def _from_numbered(block: str) -> str | None:
    """Numbered lines back to the text, or None where they aren't 1, 2, 3... in order."""
    lines = []
    for n, line in enumerate(block.split("\n") if block else [], 1):
        found = NUMBERED.match(line)
        if found is None or int(found.group(1)) != n:
            return None
        lines.append(found.group(2))
    return "\n".join(lines)


def _from_read(reply: str) -> str | None:
    """The text a whole `read` showed: its head says `lines 1-N of N`."""
    head, _, rest = reply.partition("\n")
    found = re.search(r"lines 1-(\d+) of (\d+):$", head)
    if found is None or found.group(1) != found.group(2):
        return None
    text = _from_numbered(rest)
    return text if text is not None and len(text.splitlines()) == int(found.group(2)) else None


def _edited(text: str | None, args: dict[str, Any]) -> str | None:
    """An `edit` that was carried out, applied as `tools.WorkTools._edit` applies it, or None
    where it can't be: the text isn't known, or its lines aren't there once."""
    old, new = args.get("old"), args.get("new")
    if text is None or not isinstance(old, str) or not isinstance(new, str):
        return None
    old, new = _trim(old), _trim(new)
    lines, want = text.splitlines(), old.split("\n")
    at = [i for i in range(len(lines) - len(want) + 1) if lines[i : i + len(want)] == want]
    if len(at) != 1:
        return None
    i = at[0]
    changed = lines[:i] + (new.split("\n") if new else []) + lines[i + len(want) :]
    return "\n".join(changed) + ("\n" if text.endswith("\n") else "")


def _trim(text: str) -> str:
    """As `tools._trim`: one leading and one trailing newline off."""
    text = text[1:] if text.startswith("\n") else text
    return text[:-1] if text.endswith("\n") else text


def _sendable(tool_calls: list[Any]) -> list[Any]:
    """The calls as they can go back to the server, which refuses a past turn whose arguments
    aren't JSON, and ends the run. A reply cut off at the token limit leaves them empty or partial.
    The call was told back as malformed, and its event keeps what came."""
    fixed: list[Any] = []
    for call in tool_calls:
        function = call.get("function") or {} if isinstance(call, dict) else {}
        raw = function.get("arguments")
        try:
            json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            call = {**call, "function": {**function, "arguments": "{}"}}
        fixed.append(call)
    return fixed


def _arguments(raw: Any) -> dict[str, Any]:
    try:
        found = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def usage(answer: Any) -> dict[str, int]:
    """Token counts off a response as LiteLLM reports them; a shape nobody expected is none."""
    raw = answer.get("usage") if isinstance(answer, dict) else None
    if not isinstance(raw, dict):
        return {}
    found = {
        k: raw[k] for k in ("prompt_tokens", "completion_tokens") if isinstance(raw.get(k), int)
    }
    for key, detail in (
        ("reasoning_tokens", "completion_tokens_details"),
        ("cached_tokens", "prompt_tokens_details"),
        ("image_tokens", "prompt_tokens_details"),
    ):
        value = (raw.get(detail) or {}).get(key)
        if isinstance(value, int):
            found[key] = value
    return found
