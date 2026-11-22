"""Minimal OpenAI-compatible chat client, through LiteLLM :4000.

Every model call goes through the LiteLLM proxy on the Mac; nothing calls the
Spark's SGLang directly (mqucifer/project-print `CLAUDE.md`). The prototype's
default pointed at SGLang by hostname; that default is gone, and so is any
default that reaches for a model without being asked: `LLMConfig.from_settings`
is the one way to build a configured client, and it refuses without a key.

The alias, not this client, owns sampling and thinking (mqucifer/infra
`compose/mac/litellm/config.yaml`). Orientation uses `crew-code`, whose
thinking is off: with it on, this model spent its whole budget reasoning and
returned empty content (the prototype's AGENTS.md), and a 512-token answer
leaves no room for reasoning anyway.

Only what the expert nodes need: one chat completion call with structured JSON
output enforced server-side, the same streamed (`stream_json_call`), and one with tools for
the print loop, streamed too (`chat_with_tools`, mqucifer/print-shop#129). No retries — a failed
call is a failed call and the caller decides what to do with it.

**The calls a person could be waiting on are streamed** (the intake's questions,
mqucifer/print-shop#208; the settings and the print loop's, mqucifer/print-shop#242), so each
can be ended: when its answer runs on, when its time is up, or when the person goes on without
it. A call asked for whole keeps generating on the server after the client has left, and keeps
its share of the host from the calls after it.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from print_shop.env import SETTINGS_PATH, load_env

__all__ = [
    "RUNAWAY_CHARS", "CallInfo", "CutOff", "LLMConfig", "Stopped", "TimedOut", "chat_with_tools",
    "complete_json", "complete_json_call", "stream_json_call",
]

#: A streamed answer is cut off once it's this many characters of whitespace in a row: crew's
#: cut, which the rounds' sender makes too. The server's JSON grammar allows whitespace without
#: end. On the questions calls of 2026-11-15 to 22 every cut-off had a run of at least 8,349,
#: and every answer that stopped by itself one of at most 1,725 (mqucifer/print-shop#208).
RUNAWAY_CHARS = 2000

#: LiteLLM on the Mac. Loopback, so it is not an address the check would flag,
#: and not a host anyone outside this machine could reach.
DEFAULT_URL = "http://127.0.0.1:4000/v1"

#: The alias with thinking off (see the module docstring).
DEFAULT_MODEL = "crew-code"

#: Settings keys. The key is the crew's name for a LiteLLM virtual key.
URL_KEY = "PRINT_SHOP_LLM_URL"
MODEL_KEY = "PRINT_SHOP_LLM_MODEL"
API_KEY = "CREW_LLM_API_KEY"


class LLMConfig(BaseModel):
    """Where the model is, and the key that reaches it."""

    model_config = {"frozen": True}

    api_key: SecretStr
    base_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    max_tokens: int = 512
    timeout_s: float = 120.0

    @classmethod
    def from_settings(cls, path: Path | None = None) -> LLMConfig:
        """Read the Mac's settings with `load_env`.

        Raises:
            RuntimeError: If there is no key. Without one every call would fail
                and orientation would quietly fall back to geometry, so it is
                refused here, where someone is watching.
        """
        target = path or SETTINGS_PATH
        env = load_env(target)
        key = env.get(API_KEY, "")
        if not key:
            raise RuntimeError(
                f"{API_KEY} is not set in {target}; the orientation model is "
                f"reached through LiteLLM :4000 with a key"
            )
        return cls(
            api_key=SecretStr(key),
            base_url=env.get(URL_KEY) or DEFAULT_URL,
            model=env.get(MODEL_KEY) or DEFAULT_MODEL,
        )


class CallInfo(BaseModel):
    """What one call cost and how it ended, without its text."""

    model_config = {"frozen": True}

    model: str
    #: "stop", or "length" when max_tokens cut it off. A streamed call this client cut off
    #: says "runaway" or "too long", and its tokens are 0: the server counts them in the last
    #: chunk, which never came.
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    #: Tokens spent thinking, when the alias thinks and the server reports it.
    reasoning_tokens: int
    max_tokens: int
    seconds: float
    #: A streamed answer's characters, and its longest run of whitespace: never its text.
    answer_chars: int = 0
    whitespace_run: int = 0


class CutOff(RuntimeError):
    """The reply ran to a limit and was cut off: `max_tokens`, or a streamed call's own.
    Carries what the call cost."""

    def __init__(self, message: str, info: CallInfo) -> None:
        super().__init__(message)
        self.info = info


class TimedOut(RuntimeError):
    """The call had no whole answer in its time."""

    def __init__(self, seconds: float, limit_s: float) -> None:
        super().__init__(f"timed out after {seconds:g} s (its limit is {limit_s:g} s)")
        self.seconds = seconds
        self.limit_s = limit_s


class Stopped(RuntimeError):
    """The caller ended the call before it answered."""


def complete_json(
    config: LLMConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """The parsed JSON object in one chat completion's reply (`complete_json_call`)."""
    return complete_json_call(config, system, user, schema)[0]


def complete_json_call(
    config: LLMConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> tuple[dict[str, Any], CallInfo]:
    """Send one chat completion and return the parsed JSON object in the reply, and its cost.

    The schema is enforced server-side via `response_format` with
    `strict: True`, so the reply is always valid JSON against it.

    Args:
        config: Endpoint configuration.
        system: System prompt.
        user: User prompt.
        schema: JSON Schema with a `name` and `schema` key.

    Returns:
        The parsed JSON object from the model's reply, and what the call cost.

    Raises:
        CutOff: When `max_tokens` cut the reply off, with the call's cost.
        RuntimeError: On a non-2xx response, an empty answer, or a body that
            will not parse. The key is never part of the message.
    """
    body = _json_body(config, system, user, schema)
    headers = {"Authorization": f"Bearer {config.api_key.get_secret_value()}"}

    started = time.monotonic()
    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.post(
                f"{config.base_url}/chat/completions", json=body, headers=headers
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM endpoint {config.base_url} unreachable: {exc}") from exc

    if resp.status_code // 100 != 2:
        raise RuntimeError(f"LLM endpoint returned {resp.status_code}: {resp.text[:500]}")

    try:
        payload = resp.json()
        choice = payload["choices"][0]
        text = choice["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM endpoint returned an unexpected body: {exc}") from exc
    usage = payload.get("usage") or {}
    info = CallInfo(
        model=str(payload.get("model", config.model)),
        finish_reason=str(choice.get("finish_reason", "")),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        ),
        max_tokens=config.max_tokens,
        seconds=round(time.monotonic() - started, 1),
    )
    if info.finish_reason == "length":
        raise CutOff(
            f"cut off at max_tokens={config.max_tokens} after {info.completion_tokens} tokens"
            f" ({info.reasoning_tokens} reasoning) and {info.seconds} s",
            info,
        )
    return _parsed(text), info


def stream_json_call(
    config: LLMConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    answer_chars: int,
    stop: threading.Event | None = None,
) -> tuple[dict[str, Any], CallInfo]:
    """`complete_json_call`, streamed, so that it can be ended (mqucifer/print-shop#208).

    `config.timeout_s` is the whole call's time. A call that's ended, by that, by `stop` or by a
    limit on its answer, has its connection closed, so the server can let it go. One that
    hasn't sent a byte yet is closed as its first one arrives.

    Args:
        answer_chars: The answer's most characters. Its thinking isn't counted.
        stop: Set by the caller to end the call.

    Raises:
        CutOff: At `max_tokens`, at `answer_chars`, or at `RUNAWAY_CHARS` of whitespace in a row.
        TimedOut: When `config.timeout_s` is up.
        Stopped: When `stop` is set.
        RuntimeError: On a non-2xx response, an empty answer, or a body that will not parse.
            The key is never part of the message.
    """
    message, info = _streamed(config, _json_body(config, system, user, schema), answer_chars, stop)
    return _parsed(message["content"]), info


def _streamed(
    config: LLMConfig, body: dict[str, Any], answer_chars: int, stop: threading.Event | None
) -> tuple[dict[str, Any], CallInfo]:
    """One chat completion, streamed and ended by its time, by `stop` or by a limit on its
    answer: the assistant's message, assembled, and its cost."""
    sent = {**body, "stream": True, "stream_options": {"include_usage": True}}
    halt = threading.Event()
    ended: Queue[tuple[dict[str, Any], CallInfo] | Exception] = Queue()

    def read() -> None:
        try:
            ended.put(_read_stream(config, sent, answer_chars, halt))
        except Exception as exc:  # noqa: BLE001 - raised again below, in the caller's thread
            ended.put(exc)

    started = time.monotonic()
    threading.Thread(target=read, daemon=True, name="llm-stream").start()
    try:
        while True:
            try:
                got = ended.get(timeout=0.05)
            except Empty:
                waited = time.monotonic() - started
                if stop is not None and stop.is_set():
                    raise Stopped(f"stopped after {waited:.1f} s") from None
                if waited >= config.timeout_s:
                    raise TimedOut(round(waited, 1), config.timeout_s) from None
                continue
            if isinstance(got, Exception):
                raise got
            return got
    finally:
        halt.set()


def _read_stream(
    config: LLMConfig, body: dict[str, Any], answer_chars: int, halt: threading.Event
) -> tuple[dict[str, Any], CallInfo]:
    """The stream read to its end or to one of its limits, on `_streamed`'s thread. A tool
    call's arguments are the answer as much as its content is: both are counted and bounded."""
    headers = {"Authorization": f"Bearer {config.api_key.get_secret_value()}"}
    answer: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    chars = run = longest = 0
    usage: dict[str, Any] = {}
    finish = ""
    model = config.model
    started = time.monotonic()
    try:
        with (
            httpx.Client(timeout=config.timeout_s) as client,
            client.stream(
                "POST", f"{config.base_url}/chat/completions", json=body, headers=headers
            ) as resp,
        ):
            if resp.status_code // 100 != 2:
                resp.read()
                raise RuntimeError(
                    f"LLM endpoint returned {resp.status_code}: {resp.text[:500]}"
                )
            for line in resp.iter_lines():
                if halt.is_set():
                    raise Stopped("stopped")  # leaving the block closes the connection
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise RuntimeError(f"LLM endpoint sent an error: {str(chunk['error'])[:500]}")
                model = str(chunk.get("model") or model)
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or ():
                    delta = choice.get("delta") or {}
                    piece = delta.get("content") or ""
                    answer.append(piece)
                    for part in delta.get("tool_calls") or ():
                        piece += _assemble(calls, part)
                    chars += len(piece)
                    for char in piece:
                        run = run + 1 if char.isspace() else 0
                        longest = max(longest, run)
                    finish = str(choice.get("finish_reason") or finish)
                if longest >= RUNAWAY_CHARS:
                    finish = "runaway"
                    break
                if chars > answer_chars:
                    finish = "too long"
                    break
    except httpx.TimeoutException as exc:
        raise TimedOut(round(time.monotonic() - started, 1), config.timeout_s) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM endpoint {config.base_url} unreachable: {exc}") from exc
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise RuntimeError(f"LLM endpoint returned an unexpected body: {exc}") from exc

    info = CallInfo(
        model=model,
        finish_reason=finish,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int(
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        ),
        max_tokens=config.max_tokens,
        seconds=round(time.monotonic() - started, 1),
        answer_chars=chars,
        whitespace_run=longest,
    )
    why = {
        "runaway": f"as its answer ran on as whitespace, {longest:,} characters in a row",
        "too long": f"as its answer passed {answer_chars:,} characters",
        "length": (f"at max_tokens={config.max_tokens} after {info.completion_tokens} tokens "
                   f"({info.reasoning_tokens} reasoning)"),
    }.get(finish)
    if why:
        raise CutOff(f"cut off {why}, after {info.seconds} s", info)
    message: dict[str, Any] = {"role": "assistant", "content": "".join(answer)}
    if calls:
        message["tool_calls"] = [calls[n] for n in sorted(calls)]
    return message, info


def _assemble(calls: dict[int, dict[str, Any]], part: dict[str, Any]) -> str:
    """One streamed piece of a tool call put into the call at its index, and the piece of its
    arguments: the id and the name come once, the arguments in pieces."""
    call = calls.setdefault(int(part.get("index") or 0), {
        "id": "", "type": "function", "function": {"name": "", "arguments": ""},
    })
    call["id"] = part.get("id") or call["id"]
    function = part.get("function") or {}
    piece = function.get("arguments") or ""
    call["function"]["name"] += function.get("name") or ""
    call["function"]["arguments"] += piece
    return str(piece)


def _json_body(
    config: LLMConfig, system: str, user: str, schema: dict[str, Any]
) -> dict[str, Any]:
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": config.max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.get("name", "response"),
                "schema": schema.get("schema", schema),
                "strict": True,
            },
        },
    }


def _parsed(text: str | None) -> dict[str, Any]:
    if not text:
        # The model can think and then answer nothing (mqucifer/infra
        # `runbooks/empty-model-answers.md`). Say so rather than fail to parse.
        raise RuntimeError("LLM returned an empty answer")

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM reply is not a JSON object: {text[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"LLM reply is not a JSON object: {text[:200]!r}")
    return parsed


def chat_with_tools(
    config: LLMConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    answer_chars: int,
    stop: threading.Event | None = None,
) -> tuple[dict[str, Any], CallInfo]:
    """One chat completion that may call tools, streamed and bounded as `stream_json_call` is
    (mqucifer/print-shop#242): the assistant's message (its `content` and `tool_calls`, each
    call's `arguments` a JSON string), assembled, and its cost.

    Args:
        answer_chars: The most characters of content and tool calls' arguments together.
        stop: Set by the caller to end the call.

    Raises:
        CutOff: At `max_tokens`, at `answer_chars`, or at `RUNAWAY_CHARS` of whitespace in a row.
        TimedOut: When `config.timeout_s` is up.
        Stopped: When `stop` is set.
        RuntimeError: On a non-2xx response or a body that will not parse. The key is never
            part of the message.
    """
    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": config.max_tokens,
    }
    return _streamed(config, body, answer_chars, stop)
