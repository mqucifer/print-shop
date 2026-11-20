"""A scripted model: each answer written out in advance, and no model call anywhere."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import trimesh

from print_shop.bench.refine import MARK as REFINE_MARK
from print_shop.rounds.box import Ran


def tool(name: str, /, **args: Any) -> dict[str, Any]:
    """One tool call, as a turn's script names it."""
    return {"name": name, "arguments": args}


def flat(*files: str) -> dict[str, str]:
    """`done`'s `on_bed` laying each file as stored: every printed file's side is named
    (mqucifer/print-shop#192)."""
    return dict.fromkeys(files, "-z")


def laid(args: list[str], where: Path | None, meshes: dict[str, Any] | None = None) -> Ran:
    """The bench's `refine` helper answering a `mesh` request, as a fake box gives it: each file
    laid on the bed as an STL, a plain 10 mm cube unless `meshes` gives the file's own by its
    name. A stop reads each printed file's downward faces from these (mqucifer/print-shop#147)."""
    request = json.loads(args[0])
    assert request["request"] == "mesh" and where is not None, request
    out = where / request["out"]
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in request["files"]:
        plain = name.split("__", 1)[-1]
        mesh = (meshes or {}).get(plain) or trimesh.creation.box(extents=(10, 10, 10))
        target = out / (Path(name).stem + ".stl")
        mesh.export(str(target))
        side = (request.get("on_bed") or {}).get(name, "-z")
        files[name] = {"on_bed": side, "stl": f"{request['out']}/{target.name}"}
    return Ran(0, REFINE_MARK + json.dumps({"files": files}), 0.1)


def raw(name: str, arguments: str) -> dict[str, Any]:
    """A tool call whose arguments are sent exactly as given."""
    return {"name": name, "raw": arguments}


@dataclass
class Spoken:
    """A turn with its reasoning, its finish or its usage written out as well."""

    calls: Any = ""
    reasoning: str = ""
    finish: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)


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
        finish = "stop"
        used = {"prompt_tokens": self.prompt_tokens, "completion_tokens": 10}
        if isinstance(turn, Spoken):
            message["reasoning_content"] = turn.reasoning or message["reasoning_content"]
            finish = turn.finish
            used = {**used, **turn.usage}
            turn = turn.calls
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
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": used,
        }


def stream_of(*chunks: dict[str, Any]) -> bytes:
    """A streamed answer, as LiteLLM sends one."""
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return "".join([*lines, "data: [DONE]\n\n"]).encode()


#: How `done` begins when it's held for descriptions written before the part changed.
STALE_REFUSAL = "Nothing was checked: these describe the part as it was"
STALE_LINE = re.compile(r"^- (breakdown \d+|the need `([^`]+)`'s `what`|the plan),", re.M)


class Restating(Scripted):
    """A scripted model that does what `done` asks when it's held for stale descriptions
    (mqucifer/print-shop#53): it says each one named still holds, then sends the same `done`
    again. For runs that aren't about descriptions, so their scripts stay as they were."""

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        messages = body.get("messages") or []
        last = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        held = [
            m
            for m in messages[last + 1 :]
            if m.get("role") == "tool" and STALE_REFUSAL in str(m.get("content"))
        ]
        if held and not self.failures:
            named = []
            for found in STALE_LINE.finditer(str(held[-1]["content"])):
                if found.group(1).startswith("breakdown"):
                    named.append("breakdown")
                elif found.group(2):
                    named.append(found.group(2))
                else:
                    named.append("plan")
            sent = [
                c["function"]
                for c in messages[last].get("tool_calls") or []
                if c["function"]["name"] == "done"
            ]
            again = {"name": "done", "raw": sent[-1]["arguments"]}
            said = {"of": named, "holds": "Read against the part as it stands: it holds."}
            self.turns[:0] = [[tool("restate", descriptions=[said])], [again]]
        return super().__call__(body)
