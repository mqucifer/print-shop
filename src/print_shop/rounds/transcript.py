"""Each call's exact request, and the conversation to resume, rebuilt from a run's events.

Ported from mqucifer/model-performance `cad_eval/transcript.py` at 22ea8b7. A request is the whole
conversation so far, so keeping each whole would repeat its prefix on every call. The session
keeps a `request` event with only what's new, the tools and parameters when they change, and a
`rewrite` event when it changes an earlier message. Replaying them gives each request as it was
sent, and a test holds the session to that. This is what could later train an adapter (M14).

New here: a `snapshot` event, what's come since the last request, kept whenever a session ends or
stops, so a run can pick up from the very message it stopped at.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def events_of(events: Path) -> Iterator[dict[str, Any]]:
    if not events.exists():
        return
    with events.open(encoding="utf-8") as fh:
        for line in fh:
            # A line cut short by a crash is the last, and holds nothing to resume from.
            try:
                yield json.loads(line)
            except ValueError:
                continue


def rebuild(events: Path, n: int) -> dict[str, Any]:
    """The body of call `n` (from 1), as it went to LiteLLM."""
    messages: list[dict[str, Any]] = []
    tools: Any = None
    params: dict[str, Any] = {}
    for event in events_of(events):
        kind = event.get("kind")
        if kind == "rewrite":
            messages[event["index"]] = event["message"]
        elif kind == "snapshot":
            messages = messages[: event["from"]] + event["append"]
        elif kind == "request":
            messages = messages[: event["from"]] + event["append"]
            tools = event.get("tools", tools)
            params = event.get("params", params)
            if event["n"] == n:
                return {**params, "messages": copy.deepcopy(messages), "tools": tools}
    raise LookupError(f"no request {n} in {events}")


def conversation(events: Path) -> list[dict[str, Any]]:
    """The conversation as the records last hold it, to go on from."""
    messages: list[dict[str, Any]] = []
    for event in events_of(events):
        kind = event.get("kind")
        if kind == "rewrite":
            messages[event["index"]] = event["message"]
        elif kind in ("request", "snapshot"):
            messages = messages[: event["from"]] + event["append"]
    return messages
