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
output enforced server-side. No streaming, no retries — a failed call is a
failed call and the caller decides what to do with it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from print_shop.env import SETTINGS_PATH, load_env

__all__ = ["CallInfo", "LLMConfig", "complete_json", "complete_json_call"]

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
    finish_reason: str  # "stop", or "length" when max_tokens cut it off
    prompt_tokens: int
    completion_tokens: int
    #: Tokens spent thinking, when the alias thinks and the server reports it.
    reasoning_tokens: int
    max_tokens: int
    seconds: float


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
        RuntimeError: On a non-2xx response, an empty answer, or a body that
            will not parse, saying when `max_tokens` cut it off. The key is
            never part of the message.
    """
    body: dict[str, Any] = {
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
    cut = (
        f" (cut off at max_tokens={config.max_tokens})" if info.finish_reason == "length" else ""
    )
    if not text:
        # The model can think and then answer nothing (mqucifer/infra
        # `runbooks/empty-model-answers.md`). Say so rather than fail to parse.
        raise RuntimeError(f"LLM returned an empty answer{cut}")

    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        tail = f" ... {text[-200:]!r} ({len(text)} chars)" if cut else ""
        raise RuntimeError(
            f"LLM reply is not a JSON object{cut}: {text[:200]!r}{tail}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"LLM reply is not a JSON object: {text[:200]!r}")
    return parsed, info
