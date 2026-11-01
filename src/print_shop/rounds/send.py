"""The call to the model: LiteLLM on :4000, streamed, with print-shop's key.

Ported from mqucifer/model-performance `cad_eval/send.py` and `common/litellm.py` at 22ea8b7. A
call asked for whole waits for its last token, and on 2026-10-25 two calls thought towards the
65,536-token limit at about 20 tokens a second, and the client's one-hour timeout ended them with
nothing recorded. Streamed, a call's reads keep arriving, so it runs to its end. No thinking
budget is added (the Sponsor: a budget hides the real problem, the context and the tools).

The only cut is crew's: the content running on as whitespace past `RUNAWAY_CHARS`
(`crew_org.calls` at the commit `cad_eval` pins, 099879fe). A call that thinks to the token limit
ends as `length`, and the session goes on.

The key is read once, in this process, and never shown or recorded. Nothing probes SGLang.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import API_KEY, DEFAULT_URL

ALIAS = "crew-code-think"
RUNAWAY_CHARS = 2000
TIMEOUT_S = 3600.0
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
                    finish = choice.get("finish_reason") or finish
                if run >= RUNAWAY_CHARS:
                    finish = "runaway"
                    break
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
        }

    def close(self) -> None:
        self._client.close()


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
