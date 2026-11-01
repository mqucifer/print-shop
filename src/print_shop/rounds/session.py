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
# Characters a token, for what's come since the last measured prompt: measured on the served model
# by `cad_eval.probe` (2026-10-27: ~2,160 characters, ~800 tokens).
CHARS_PER_TOKEN = 2.7
COMPACTED = "(compacted: {what})"
# A picture's tokens in the next prompt, before a response reports them: `cad_eval`'s was 576.
PICTURE_TOKENS = 600
QUIET = "Call a tool to go on. If you can't, `blocked` says why."
SHOWN_BEFORE = (
    "(A picture was here. Only the latest turn's pictures stay in the conversation: `read` shows "
    "any of them again.)"
)
NOT_CARRIED_OUT = "Not carried out: an earlier call in this turn stopped the turn."
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
    # Each tool reply's facts, by its message's index, for compaction.
    replies: dict[int, dict[str, Any]] = field(default_factory=dict)

    def begin(self, *start: dict[str, Any]) -> None:
        """A new conversation. An earlier one in the same run stays in the records."""
        self.messages = list(start)
        self.replies = {}
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
            elif kind == "picture":
                self.views += 1

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
                kept["tool_calls"] = tool_calls
            self.messages.append(kept)
            if not tool_calls:
                quiet += 1
                if quiet >= self.limits.quiet:
                    return self._ended(Ending("no_tool_call", {"limit": self.limits.quiet}))
                self.say(QUIET)
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
          `write` (the call), or its last whole `read` (the reply), and every `edit` of it after
          that, call and reply.
        - The latest reply of `run`, of `bench` and of `done` stays whole, and so do the newest
          turn's. A run's reply through `write` or `edit` counts as `run`'s.
        - Every other `write` or `edit` call keeps its own keys, its text a note, and every other
          tool reply shrinks to one line.
        - What the Sponsor said is never touched: it's in user messages.
        """
        last = max((i for i, m in enumerate(self.messages) if m["role"] == "assistant"), default=-1)
        full: dict[str, set[tuple[str, int, int]]] = {}
        for i, m in enumerate(self.messages):
            for k, called in enumerate(m.get("tool_calls") or []):
                function = called.get("function") or {}
                path = str(_arguments(function.get("arguments")).get("path"))
                if function.get("name") == "write":
                    full[path] = {("call", i, k)}
                elif function.get("name") == "edit":
                    full.setdefault(path, set()).add(("call", i, k))
            info = self.replies.get(i)
            if info and info["ok"] and info["name"] == "read" and info["whole"]:
                full[str(info["path"])] = {("reply", i, -1)}
            elif info and info["ok"] and info["name"] == "edit":
                full.setdefault(str(info["path"]), set()).add(("reply", i, -1))
        kept = set().union(*full.values()) if full else set()
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
                        size = len(str(function.get("arguments")))
                        note = COMPACTED.format(what=f"{size} characters")
                        texts = ("content",) if called_name == "write" else ("old", "new")
                        short = {**args, **{t: note for t in texts if t in args}}
                        called = {
                            **called,
                            "function": {**function, "arguments": json.dumps(short)},
                        }
                    calls.append(called)
                if calls:
                    changed["tool_calls"] = calls
                if changed != m:
                    self.messages[i] = changed
                    self._record("rewrite", index=i, message=changed)
            elif (
                m["role"] == "tool" and i < last and i not in stay and ("reply", i, -1) not in kept
            ):
                content = str(m.get("content") or "")
                if not content.startswith("(compacted:"):
                    first = content.splitlines()[0][:120] if content else ""
                    tool = (self.replies.get(i) or {}).get("name", "tool")
                    self.rewrite(i, COMPACTED.format(what=f"{tool}: {first}"))
        self.compactions += 1

    def _call(self) -> tuple[dict[str, Any], Ending | None]:
        if self.last_prompt and self.estimate() >= self.limits.compact_at:
            before = self.estimate()
            self.compact()
            self._record("compacted", estimated=before, estimated_after=self.estimate())
            if self.estimate() >= self.limits.compact_at:
                full = {"estimated": self.estimate(), "limit": self.limits.compact_at}
                return {}, Ending("context_full", full)
        sent_chars = self._chars()
        body = {
            "model": self.alias,
            "messages": self.messages,
            "tools": self.tools.specs,
            "tool_choice": "auto",
            "max_tokens": self.limits.max_tokens,
        }
        self._request(body)
        n = self.requests
        at = datetime.now(UTC)
        began = time.monotonic()
        answer: dict[str, Any] = {}
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
            judged = self.tools.call(name, function.get("arguments"))
            self.outcomes[judged.outcome] += 1
            if isinstance(name, str):
                self.by_tool[name] += 1
            args = _arguments(function.get("arguments"))
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
        with self.events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, **detail}, default=str) + "\n")

    def snapshot(self) -> None:
        """What's come since the last request, so a resume goes on from this very message."""
        if self.logged < len(self.messages) or self.logged == 0:
            self._record(
                "snapshot",
                **{"from": self.logged, "append": copy.deepcopy(self.messages[self.logged :])},
            )
            self.logged = len(self.messages)

    def _ended(self, ending: Ending) -> Ending:
        self.snapshot()
        self._record("ended", name=ending.name, said=ending.said)
        return ending


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
