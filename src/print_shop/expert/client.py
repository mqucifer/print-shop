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
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from print_shop.env import SETTINGS_PATH, load_env

__all__ = ["LLMConfig", "complete_json"]

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


def complete_json(
    config: LLMConfig,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Send one chat completion and return the parsed JSON object in the reply.

    The schema is enforced server-side via `response_format` with
    `strict: True`, so the reply is always valid JSON against it.

    Args:
        config: Endpoint configuration.
        system: System prompt.
        user: User prompt.
        schema: JSON Schema with a `name` and `schema` key.

    Returns:
        The parsed JSON object from the model's reply.

    Raises:
        RuntimeError: On a non-2xx response, an empty answer, or a body that
            will not parse. The key is never part of the message.
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
        text = payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM endpoint returned an unexpected body: {exc}") from exc
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
