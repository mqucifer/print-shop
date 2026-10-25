"""Minimal OpenAI-compatible chat client, through LiteLLM :4000.

Every model call goes through the LiteLLM proxy on the Mac; nothing calls the
Spark's SGLang directly (mqucifer/project-print `CLAUDE.md`). The prototype's
default pointed at SGLang by hostname; that default is gone.
Only what the expert nodes need: one chat completion call with structured JSON
output enforced server-side. No streaming, no retries — a failed call is a
failed call and the caller decides what to do with it.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel

__all__ = ["LLMConfig", "complete_json"]

_DEFAULT_URL = "http://127.0.0.1:4000/v1"
_DEFAULT_MODEL = "qwen3.8-27b-sglang"


class LLMConfig(BaseModel):
    """Endpoint configuration, overridable from the environment."""

    model_config = {"frozen": True}

    base_url: str = os.environ.get("PRINT_AGENT_LLM_URL", _DEFAULT_URL)
    model: str = os.environ.get("PRINT_AGENT_LLM_MODEL", _DEFAULT_MODEL)
    temperature: float = 0.3
    max_tokens: int = 512
    timeout_s: float = 120.0


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
        RuntimeError: On a non-2xx response or a body that will not parse.
    """
    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema.get("name", "response"),
                "schema": schema.get("schema", schema),
                "strict": True,
            },
        },
        "chat_template_kwargs": {"enable_thinking": False},
    }

    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.post(f"{config.base_url}/chat/completions", json=body)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM endpoint {config.base_url} unreachable: {exc}") from exc

    if resp.status_code // 100 != 2:
        raise RuntimeError(f"LLM endpoint returned {resp.status_code}: {resp.text[:500]}")

    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM endpoint returned non-JSON response: {exc}") from exc

    text = payload["choices"][0]["message"]["content"]
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM reply is not a JSON object: {text[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(f"LLM reply is not a JSON object: {text[:200]!r}")
    return parsed
