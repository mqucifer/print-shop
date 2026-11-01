"""A scripted model: each answer written out in advance, and no model call anywhere."""

from __future__ import annotations

import json
from typing import Any


def tool(name: str, /, **args: Any) -> dict[str, Any]:
    """One tool call, as a turn's script names it."""
    return {"name": name, "arguments": args}


def raw(name: str, arguments: str) -> dict[str, Any]:
    """A tool call whose arguments are sent exactly as given."""
    return {"name": name, "raw": arguments}


class Scripted:
    """Answers each call with the next turn. A turn is a list of tool calls, or text for a reply
    with none. It's a sender as a `Session` takes it, and it keeps each request it was sent."""

    def __init__(self, *turns: Any, prompt_tokens: int = 1000) -> None:
        self.turns = list(turns)
        self.bodies: list[dict[str, Any]] = []
        self.prompt_tokens = prompt_tokens
        self.served: dict[str, str] = {"x-litellm-model-group": "fake"}
        self.failures: list[Exception] = []

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.bodies.append(json.loads(json.dumps(body)))
        if self.failures:
            raise self.failures.pop(0)
        if not self.turns:
            raise AssertionError("the script ran out of turns")
        turn = self.turns.pop(0)
        n = len(self.bodies)
        message: dict[str, Any] = {"role": "assistant", "reasoning_content": f"thinking {n}"}
        if isinstance(turn, str):
            message["content"] = turn
        else:
            message["content"] = ""
            message["tool_calls"] = [
                {
                    "id": f"call-{n}-{i}",
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": call.get("raw", json.dumps(call.get("arguments", {}))),
                    },
                }
                for i, call in enumerate(turn)
            ]
        return {
            "id": f"response-{n}",
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": self.prompt_tokens, "completion_tokens": 10},
        }
