"""The call to the model: LiteLLM on :4000, streamed, with print-shop's key.

Ported from mqucifer/model-performance `cad_eval/send.py` and `common/litellm.py` at 22ea8b7. A
call asked for whole waits for its last token, and on 2026-10-25 two calls thought towards the
65,536-token limit at about 20 tokens a second, and the client's one-hour timeout ended them with
nothing recorded. Streamed, a call's reads keep arriving, so it runs to its end. No thinking
budget is added (the Sponsor: a budget hides the real problem, the context and the tools).

The only cut is crew's: the content running on as whitespace past `RUNAWAY_CHARS`
(`crew_org.calls` at the commit `cad_eval` pins, 099879fe). A call that thinks to the token limit
ends as `length`, and the session goes on.

A tool call's arguments are kept as they arrive, in a file of their own, so one cut off at the
limit can be read: `keep_stream` names the file before a call, and it stays unless they all came
whole, which the call's event then holds. On a real run a call lost most of its tokens as arguments
that never arrived, and the times of its chunks weren't kept either, so the answer also says when
chunks came and how far the arguments got. If a server holds arguments back until they're complete
there's nothing here to keep, and those figures are what show it. This is all recording: nothing
cuts a call for it.

The key is read once, in this process, and never shown or recorded. Nothing probes SGLang.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TextIO

import httpx

from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import API_KEY, DEFAULT_URL

ALIAS = "crew-code-think"
RUNAWAY_CHARS = 2000
TIMEOUT_S = 3600.0
#: What a run's record says when LiteLLM won't say what reasoning effort an alias sends.
UNKNOWN = "unknown"
#: Where in an alias's `litellm_params` its model info may hold the effort it sends: as a
#: parameter of its own, or in the body it adds to each call, as SGLang's chat template reads it.
EFFORT_AT = (
    ("reasoning_effort",),
    ("extra_body", "reasoning_effort"),
    ("extra_body", "chat_template_kwargs", "reasoning_effort"),
)
# LiteLLM's headers that say what served a call: never the key's, the spend's or the budget's,
# nor the address it reached. Rows and records hold no address (`cad_eval`'s second review of #41).
PRIVATE = ("key", "spend", "cost", "budget", "user", "team", "api-base")


class Sender:
    def __init__(
        self,
        key: str | None = None,
        base_url: str = DEFAULT_URL,
        timeout: float = TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        found = key if key is not None else load_env(SETTINGS_PATH).get(API_KEY)
        if not found:
            raise LookupError(f"{API_KEY} isn't in {SETTINGS_PATH}")
        self.served: dict[str, str] = {}
        self.spool: Path | None = None
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {found}"},
            timeout=timeout,
            transport=transport,
        )

    def lists(self, alias: str) -> bool:
        """Whether the proxy knows `alias` for this key: a check that costs no model call."""
        response = self._client.get("/models")
        if response.status_code != 200:
            raise httpx.HTTPStatusError(
                f"LiteLLM answered {response.status_code} for its models",
                request=response.request,
                response=response,
            )
        return alias in {model.get("id") for model in response.json().get("data", [])}

    def effort(self, alias: str) -> tuple[str, str]:
        """The reasoning effort `alias` sends, as LiteLLM's model info says it for this key, and
        where it was read; or `unknown`, and why (mqucifer/print-shop#40). `/model/info` is
        LiteLLM's own answer, from its config: it costs no model call and reaches no SGLang.
        Nothing else is kept from it, since it can name a server's address."""
        try:
            response = self._client.get("/model/info")
        except httpx.HTTPError as exc:
            return UNKNOWN, f"LiteLLM's model info didn't answer: {type(exc).__name__}"
        return effort_in(response.status_code, response.content, alias)

    def keep_stream(self, path: Path) -> None:
        """Where the next call's tool-call arguments go as they arrive."""
        self.spool = path

    def wire(self, body: dict[str, Any]) -> dict[str, Any]:
        """The body as it's sent: streamed, with the usage in its last chunk."""
        return {**body, "stream": True, "stream_options": {"include_usage": True}}

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        content: list[str] = []
        thinking: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish: str | None = None
        response_id = ""
        served = ""
        run = 0
        began = last = time.monotonic()
        stream: dict[str, Any] = {
            "chunks": 0,
            "first_chunk_s": None,
            "longest_gap_s": 0.0,
            "longest_gap_ended_s": None,
            "tool_call_started_s": None,
            "tool_args_chars": 0,
        }
        spool: TextIO | None = None
        with self._client.stream("POST", "/chat/completions", json=self.wire(body)) as response:
            self.served = served_headers(response.headers)
            if response.status_code >= 400:
                # The status only: a body can echo the request, and this text reaches the record.
                raise httpx.HTTPStatusError(
                    f"LiteLLM answered {response.status_code}",
                    request=response.request,
                    response=response,
                )
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                moment = time.monotonic()
                stream["chunks"] += 1
                if stream["first_chunk_s"] is None:
                    stream["first_chunk_s"] = round(moment - began, 3)
                if moment - last > stream["longest_gap_s"]:
                    stream["longest_gap_s"] = round(moment - last, 3)
                    stream["longest_gap_ended_s"] = round(moment - began, 3)
                last = moment
                response_id = chunk.get("id") or response_id
                served = chunk.get("model") or served
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    thinking.append(delta.get("reasoning_content") or "")
                    piece = delta.get("content") or ""
                    if piece:
                        content.append(piece)
                        kept = piece.rstrip()
                        run = run + len(piece) if not kept else len(piece) - len(kept)
                    for part in delta.get("tool_calls") or []:
                        assemble(calls, part)
                        if stream["tool_call_started_s"] is None:
                            stream["tool_call_started_s"] = round(moment - began, 3)
                        piece = (part.get("function") or {}).get("arguments") or ""
                        stream["tool_args_chars"] += len(piece)
                        spool = self._kept(spool, moment - began, part)
                    finish = choice.get("finish_reason") or finish
                if run >= RUNAWAY_CHARS:
                    finish = "runaway"
                    break
        stream["ended_s"] = round(time.monotonic() - began, 3)
        target = self.spool
        if spool is not None and target is not None:
            spool.close()
            if self._whole(calls) and finish not in ("length", "runaway"):
                target.unlink(missing_ok=True)
            else:
                stream["kept"] = f"{target.parent.name}/{target.name}"
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content),
            "reasoning_content": "".join(thinking),
        }
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return {
            "id": response_id,
            "model": served,
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": usage,
            "stream": stream,
        }

    def _kept(self, spool: TextIO | None, at: float, part: dict[str, Any]) -> TextIO | None:
        """One piece of a tool call, on the disk before the next arrives."""
        if self.spool is None:
            return None
        if spool is None:
            self.spool.parent.mkdir(parents=True, exist_ok=True)
            spool = self.spool.open("a", encoding="utf-8")
        function = part.get("function") or {}
        piece = {
            "at_s": round(at, 3),
            "index": part.get("index"),
            "name": function.get("name"),
            "arguments": function.get("arguments"),
        }
        spool.write(json.dumps(piece) + "\n")
        spool.flush()
        return spool

    @staticmethod
    def _whole(calls: dict[int, dict[str, Any]]) -> bool:
        for call in calls.values():
            try:
                json.loads(call["function"]["arguments"] or "{}")
            except ValueError:
                return False
        return True

    def close(self) -> None:
        self._client.close()


def effort_in(status: int, body: bytes, alias: str) -> tuple[str, str]:
    """The effort an alias sends, from LiteLLM's `/model/info` answer: `{"data": [{"model_name":
    ..., "litellm_params": {...}, "model_info": {...}}]}`, one entry for each deployment."""
    if status != 200:
        return UNKNOWN, f"LiteLLM answered {status} for its model info"
    try:
        data = json.loads(body).get("data") or []
    except (ValueError, AttributeError):
        return UNKNOWN, "LiteLLM's model info wasn't the JSON it documents"
    entries = [d for d in data if isinstance(d, dict) and d.get("model_name") == alias]
    if not entries:
        return UNKNOWN, f"LiteLLM's model info has no `{alias}` for this key"
    found: dict[str, str] = {}
    for entry in entries:
        for path in EFFORT_AT:
            value: Any = entry.get("litellm_params")
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if isinstance(value, str) and value.strip():
                found[value.strip()] = "litellm_params." + ".".join(path)
    if len(found) == 1:
        (value, where), = found.items()
        return value, f"LiteLLM's /model/info for `{alias}`, {where}"
    if found:
        return UNKNOWN, f"`{alias}`'s deployments send {', '.join(sorted(found))}"
    return UNKNOWN, f"LiteLLM's model info for `{alias}` doesn't name an effort"


def served_headers(headers: Any) -> dict[str, str]:
    """The `x-litellm-*` response headers that name what served a call, and no address."""
    return {
        k.lower(): str(v)
        for k, v in headers.items()
        if k.lower().startswith("x-litellm-") and not any(p in k.lower() for p in PRIVATE)
    }


def assemble(calls: dict[int, dict[str, Any]], part: dict[str, Any]) -> None:
    """One streamed piece of a tool call into the call at its index: the id and name come once,
    the arguments in pieces."""
    index = int(part.get("index") or 0)
    call = calls.setdefault(
        index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
    )
    call["id"] = part.get("id") or call["id"]
    function = part.get("function") or {}
    call["function"]["name"] += function.get("name") or ""
    call["function"]["arguments"] += function.get("arguments") or ""
