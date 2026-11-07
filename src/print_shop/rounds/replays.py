"""One recorded call made again, with one thing changed (mqucifer/print-shop#67).

An eval takes one to four hours, so "did this skill text help?" would cost a run. mrCAD (arXiv
2504.20294) replays a recorded round with its history fixed; here it's one call. `transcript.
rebuild(events, n)` gives call `n`'s exact request, and a replay sends it again with one change,
a few times over, since the model varies. It sends nothing to the Sponsor, runs no tool the reply
asks for, and writes nothing in the run's folder: each replay is a folder of its own, under
`~/print-shop/replays/` by default.

**One change,** or none for a control, which shows how far the model varies on its own:
- `skill NAME`: a skill's text, wherever the request holds it: given whole in the start (between
  `<skill name="NAME">` and `</skill>`), and each `skill` reply that read it.
- `reply CALL.K`: the reply to the K-th tool call (from 1) the model made at call CALL.
- `skills-card`: the start's required skills, from "The skills this work needs throughout" to
  the last `</skill>`, as a short card of their rules, or whatever the file says. It tests the
  card as it would ship, with each skill's full text on demand: a `skill` read of a required
  skill in the history, which said the skill was in the start, has its full text as the start
  had it, and a try that reads one is answered with it and goes on to its next call, until it
  ends in anything else. A turn that reads one and calls anything more ends there, as no other
  tool is run.
- `lesson`: a lesson added to the start's lessons, under their heading, which is added if the
  start had none.
- `replace`: one exact text, everywhere the request holds it, for anything else.

A change that finds nothing to change is refused, so a replay never runs as a control by mistake.

**Each try records** the reasoning's length (characters, and tokens where the proxy says), the
call it ends in (`build`: `write`, `edit` or `run`; `bench`; `ask`: `ask`, `send_ask` or
`questions`; `other`, naming the tools; or `none`, with no tool call), its seconds, its finish,
whether it's long, and which required skills it read with `skill`. A long call reasoned past
`LONG_CHARS`, the line the runaways were read at
(`docs/bench.md`, `suncatcher-3`'s 35), or was cut or ended at its limit. The recorded call's own
figures come first, from its `call` event, to compare with.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds import transcript
from print_shop.rounds.lessons import ABOUT, HEADING
from print_shop.rounds.session import _sendable

DEFAULT_ROOT = Path("~/print-shop/replays")
#: Reasoning past this many characters is a long call: where the runaways were read from.
LONG_CHARS = 20_000
KINDS = ("none", "skill", "reply", "skills-card", "lesson", "replace")
BUILD = ("write", "edit", "run")
BENCH = ("bench",)
ASK = ("ask", "send_ask", "questions")
#: Where the start's required skills begin (`skills.start_skills`).
SKILLS_SAID = "The skills this work needs throughout are here whole:"
#: A required skill as the start gives it whole (`skills.GIVEN`).
GIVEN = re.compile(r'<skill name="([^"]+)">\n(.*?)\n</skill>', re.DOTALL)
#: What a `skill` read of a required skill answered: that it's in the start (`tools._skill`).
POINTS_TO_START = "in your first message"
#: Where a start with no lessons has its skills, for a lesson to go before.
SKILL_LINES = (SKILLS_SAID, "Skills for this work:")


class Refused(ValueError):
    """A replay that can't be made as asked, said plainly."""


@dataclass(frozen=True)
class Change:
    """One thing changed: its kind, what it names, and the new text. `old` is the text to find,
    for `replace`."""

    kind: str = "none"
    target: str = ""
    text: str = ""
    old: str = ""
    source: str = ""

    def plain(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "text_sha": _sha(self.text) if self.text else None,
            "old_sha": _sha(self.old) if self.old else None,
            "from": self.source,
        }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _body_sha(body: dict[str, Any]) -> str:
    return _sha(json.dumps(body, sort_keys=True))


def _texts(message: dict[str, Any]) -> list[tuple[Any, Any]]:
    """Where a message holds text: its content as a string, or each text part of a list."""
    content = message.get("content")
    if isinstance(content, str):
        return [(message, "content")]
    if isinstance(content, list):
        return [
            (part, "text")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
    return []


def _first_user(messages: list[dict[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message
    raise Refused("the request has no first message of text from the user")


def _tool_calls(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each tool call the request holds, by its id."""
    return {
        call["id"]: call
        for message in messages
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if call.get("id")
    }


def _skill(messages: list[dict[str, Any]], change: Change) -> int:
    name = change.target
    body = change.text.strip()
    tag = re.compile(
        rf'(<skill name="{re.escape(name)}">\n)(.*?)(\n</skill>)', re.DOTALL
    )
    changed = 0
    for message in messages:
        for holder, key in _texts(message):
            text, n = tag.subn(lambda m: m.group(1) + body + m.group(3), holder[key])
            if n:
                holder[key] = text
                changed += n
    calls = _tool_calls(messages)
    for message in messages:
        if message.get("role") != "tool":
            continue
        call = calls.get(str(message.get("tool_call_id")))
        if call is None or call["function"].get("name") != "skill":
            continue
        try:
            named = json.loads(call["function"].get("arguments") or "{}").get("name")
        except (ValueError, AttributeError):
            continue
        content = message.get("content")
        # A required skill's reply only points to the start, which is changed above.
        if named == name and isinstance(content, str) and POINTS_TO_START not in content:
            message["content"] = body
            changed += 1
    return changed


def _reply(messages: list[dict[str, Any]], change: Change, events: Path) -> int:
    try:
        call_n, k = (int(x) for x in change.target.split("."))
    except ValueError as exc:
        raise Refused(
            f"a reply is CALL.K, the K-th tool call of call CALL: not {change.target!r}"
        ) from exc
    made = _call_event(events, call_n)
    asked = (made.get("message") or {}).get("tool_calls") or []
    if not 1 <= k <= len(asked):
        raise Refused(f"call {call_n} made {len(asked)} tool call(s): there's none at {k}")
    wanted = asked[k - 1].get("id")
    changed = 0
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id") == wanted:
            message["content"] = change.text
            changed += 1
    return changed


def required_given(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Each required skill's text, as the start gives it whole, by its name."""
    text = _first_user(messages)["content"]
    at = text.find(SKILLS_SAID)
    return {} if at < 0 else dict(GIVEN.findall(text[at:]))


def _skill_named(call: dict[str, Any]) -> str | None:
    function = call.get("function") or {}
    if function.get("name") != "skill":
        return None
    try:
        named = json.loads(function.get("arguments") or "{}").get("name")
    except (ValueError, AttributeError):
        return None
    return named if isinstance(named, str) else None


def _skills_card(messages: list[dict[str, Any]], change: Change) -> int:
    given = required_given(messages)
    start = _first_user(messages)
    text = start["content"]
    at = text.find(SKILLS_SAID)
    end = text.rfind("</skill>")
    if at < 0 or end < at:
        raise Refused("the start gives no required skills whole: there's nothing to make a card of")
    start["content"] = text[:at] + change.text.strip() + text[end + len("</skill>") :]
    # Each earlier read of a required skill had only a pointer to the start: with the card it
    # gives the skill's text, as the run would if the card shipped.
    calls = _tool_calls(messages)
    for message in messages:
        if message.get("role") != "tool":
            continue
        call = calls.get(str(message.get("tool_call_id")))
        named = _skill_named(call) if call is not None else None
        content = message.get("content")
        if named in given and isinstance(content, str) and POINTS_TO_START in content:
            message["content"] = given[named]
    return 1


def _lesson(messages: list[dict[str, Any]], change: Change) -> int:
    start = _first_user(messages)
    text = start["content"]
    line = change.text.strip()
    line = line if line.startswith("- ") else f"- {line}"
    if HEADING in text:
        about = text.find(ABOUT, text.find(HEADING))
        if about < 0:
            raise Refused("the start's lessons have no line saying what they are")
        end = text.find("\n\n", about + len(ABOUT) + 2)
        end = len(text) if end < 0 else end
        start["content"] = text[:end] + "\n" + line + text[end:]
        return 1
    for marker in SKILL_LINES:
        at = text.find(marker)
        if at >= 0:
            block = "\n".join([HEADING, "", ABOUT, "", line])
            start["content"] = text[:at] + block + "\n\n" + text[at:]
            return 1
    start["content"] = text.rstrip() + "\n\n" + "\n".join([HEADING, "", ABOUT, "", line])
    return 1


def _replace(messages: list[dict[str, Any]], change: Change) -> int:
    if not change.old:
        raise Refused("`replace` needs the text to find")
    changed = 0
    for message in messages:
        for holder, key in _texts(message):
            n = holder[key].count(change.old)
            if n:
                holder[key] = holder[key].replace(change.old, change.text)
                changed += n
    return changed


def changed(body: dict[str, Any], change: Change, events: Path) -> tuple[dict[str, Any], int]:
    """The request with the change made, and how many places it changed. Nothing changed is
    refused, but for the control."""
    if change.kind not in KINDS:
        raise Refused(f"a change is one of {', '.join(KINDS)}: not {change.kind!r}")
    made = copy.deepcopy(body)
    messages = made["messages"]
    if change.kind == "none":
        return made, 0
    if change.kind == "skill":
        n = _skill(messages, change)
    elif change.kind == "reply":
        n = _reply(messages, change, events)
    elif change.kind == "skills-card":
        n = _skills_card(messages, change)
    elif change.kind == "lesson":
        n = _lesson(messages, change)
    else:
        n = _replace(messages, change)
    if n == 0:
        raise Refused(
            f"the change ({change.kind} {change.target}) finds nothing to change in the request"
        )
    if made == body:
        raise Refused("the change leaves the request as it was")
    return made, n


def ends_in(message: dict[str, Any]) -> str:
    """The call a reply ends in, by its first tool call: `build`, `bench`, `ask`, `other` or
    `none`."""
    names = [c["function"].get("name") for c in message.get("tool_calls") or []]
    if not names:
        return "none"
    first = names[0]
    if first in BUILD:
        return "build"
    if first in BENCH:
        return "bench"
    if first in ASK:
        return "ask"
    return "other"


def figures(
    message: dict[str, Any],
    finish: Any,
    usage: dict[str, Any],
    seconds: Any,
    required: Any = (),
) -> dict[str, Any]:
    reasoning = str(message.get("reasoning_content") or "")
    tools = [c["function"].get("name") for c in message.get("tool_calls") or []]
    read = [n for c in message.get("tool_calls") or [] if (n := _skill_named(c)) in required]
    return {
        "reasoning_chars": len(reasoning),
        "reasoning_tokens": usage.get("reasoning_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "ends_in": ends_in(message),
        "tools": tools,
        "finish": finish,
        "seconds": seconds,
        "long": len(reasoning) > LONG_CHARS or finish in ("length", "runaway"),
        "read_required": read,
    }


def recorded(events: Path, n: int) -> dict[str, Any]:
    """The recorded call's own figures, from its `call` event."""
    made = _call_event(events, n)
    message = made.get("message") or {}
    required = required_given(transcript.rebuild(events, n)["messages"])
    found = figures(
        message, made.get("finish"), made.get("usage") or {}, made.get("seconds"), required
    )
    found["reasoning_chars"] = made.get("reasoning_chars", found["reasoning_chars"])
    long = int(found["reasoning_chars"] or 0) > LONG_CHARS
    found["long"] = long or made.get("finish") in ("length", "runaway")
    return found


def _call_event(events: Path, n: int) -> dict[str, Any]:
    for event in transcript.events_of(events):
        if event.get("kind") == "call" and event.get("n") == n:
            return event
    raise Refused(f"there's no call {n} in {events}")


@dataclass
class Replay:
    run: str
    call: int
    change: dict[str, Any]
    places_changed: int
    folder: str
    recorded: dict[str, Any]
    tries: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        done = [t for t in self.tries if "error" not in t]
        chars = [int(t["reasoning_chars"]) for t in done]
        return {
            "tries": len(self.tries),
            "answered": len(done),
            "long": sum(1 for t in done if t["long"]),
            "read_required": sum(1 for t in done if t["read_required"]),
            "ends_in": dict(sorted(Counter(t["ends_in"] for t in done).items())),
            "reasoning_chars": {
                "least": min(chars) if chars else None,
                "median": statistics.median(chars) if chars else None,
                "most": max(chars) if chars else None,
            },
            "recorded": {
                k: self.recorded[k] for k in ("reasoning_chars", "ends_in", "long", "seconds")
            },
        }

    def plain(self) -> dict[str, Any]:
        return {**asdict(self), "summary": self.summary()}


def _folder(root: Path, run: str, n: int, label: str) -> Path:
    base = root / run / f"call-{n:03}"
    for i in range(1, 1000):
        folder = base / (label if i == 1 else f"{label}-{i}")
        if not folder.exists():
            return folder
    raise Refused(f"{base} holds 999 replays labelled {label}")


def replay(
    run_folder: Path,
    n: int,
    change: Change,
    send: Callable[[dict[str, Any]], dict[str, Any]],
    times: int = 3,
    root: Path = DEFAULT_ROOT,
    label: str = "",
    commit: str = "",
    public: Path | None = None,
) -> Replay:
    """Call `n` of the run in `run_folder`, sent `times` times with `change` made, each answer
    kept in a folder of its own under `root`. Nothing in the run's folder is written."""
    run_folder = run_folder.resolve()
    events = run_folder / "events.jsonl"
    if not events.is_file():
        raise Refused(f"there's no events.jsonl in {run_folder}")
    if times < 1:
        raise Refused("a replay is sent at least once")
    root = root.expanduser().resolve()
    if root.is_relative_to(run_folder):
        raise Refused(f"{root} is inside the run's folder: a replay changes no run's records")
    if public is not None and root.is_relative_to(public.resolve()):
        raise Refused(f"{root} is inside this repository, which is public")
    label = label or change.kind
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", label):
        raise Refused(f"a label is letters, digits, `.`, `_` and `-`: not {label!r}")
    try:
        body = transcript.rebuild(events, n)
    except LookupError as exc:
        raise Refused(str(exc)) from exc
    made, places = changed(body, change, events)
    folder = _folder(root, run_folder.name, n, label)
    folder.mkdir(parents=True)
    if change.text:
        (folder / "change.txt").write_text(change.text, encoding="utf-8")
    if change.old:
        (folder / "change-old.txt").write_text(change.old, encoding="utf-8")
    (folder / "request.json").write_text(json.dumps(made), encoding="utf-8")
    done = Replay(
        run=run_folder.name,
        call=n,
        change={
            **change.plain(),
            "request_sha_recorded": _body_sha(body),
            "request_sha_sent": _body_sha(made),
            "commit": commit,
        },
        places_changed=places,
        folder=str(folder),
        recorded=recorded(events, n),
    )
    required = required_given(body["messages"])
    on_demand = required if change.kind == "skills-card" else {}
    for k in range(1, times + 1):
        done.tries.append(_one_try(folder, k, made, send, required, on_demand))
        _write(folder, done)
    return done


def _one_try(
    folder: Path,
    k: int,
    made: dict[str, Any],
    send: Callable[[dict[str, Any]], dict[str, Any]],
    required: dict[str, str],
    on_demand: dict[str, str],
) -> dict[str, Any]:
    """One try: the call, and where the card's skills are given on demand, each call after a read
    of one, answered with its text, until a call ends in anything else."""
    keep = getattr(send, "keep_stream", None)
    request = made
    calls: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    read: list[str] = []
    while True:
        part = f"{k}" if not calls else f"{k}-{len(calls) + 1}"
        if callable(keep):
            keep(folder / "streams" / f"{part}.jsonl")
        began = time.monotonic()
        try:
            answer = send(request)
        except Exception as exc:  # noqa: BLE001 - a failed try is recorded, and the rest go on
            # The kind of failure only, and a status: an error's words can name the proxy.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            failed = {"try": k, "error": type(exc).__name__, "status": status}
            return {**failed, "calls": calls} if calls else failed
        choice = (answer.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        seconds = round(time.monotonic() - began, 3)
        usage = answer.get("usage") or {}
        one = figures(message, choice.get("finish_reason"), usage, seconds, required)
        calls.append(one)
        answers.append({"message": message, "stream": answer.get("stream")})
        asked = message.get("tool_calls") or []
        names = [_skill_named(c) for c in asked]
        fresh = [n for n in names if n in on_demand and n not in read]
        read += [n for n in one["read_required"] if n not in read]
        # It goes on only where every call of the turn reads a required skill not read yet.
        if not asked or len(fresh) != len(asked):
            break
        kept: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("reasoning_content"):
            kept["reasoning_content"] = message["reasoning_content"]
        kept["tool_calls"] = _sendable(asked)
        replies = [
            {"role": "tool", "tool_call_id": c.get("id"), "content": on_demand[str(n)]}
            for c, n in zip(asked, names, strict=True)
        ]
        request = {**request, "messages": [*request["messages"], kept, *replies]}
    last = calls[-1]
    found = {
        "try": k,
        **last,
        "read_required": read,
        "long": any(c["long"] for c in calls),
        "reasoning_chars_total": sum(int(c["reasoning_chars"]) for c in calls),
    }
    if len(calls) > 1:
        found["calls"] = calls
    (folder / f"{k}.json").write_text(
        json.dumps({**found, "answers": answers}, indent=1), encoding="utf-8"
    )
    return found


def _write(folder: Path, done: Replay) -> None:
    (folder / "replay.json").write_text(json.dumps(done.plain(), indent=1) + "\n", encoding="utf-8")
    rows = [
        "| try | reasoning chars | tokens | ends in | tools | long | seconds | finish | read |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    r = done.recorded
    rows.append(
        f"| recorded | {r['reasoning_chars']} | {r['reasoning_tokens']} | {r['ends_in']} | "
        f"{', '.join(r['tools'])} | {r['long']} | {r['seconds']} | {r['finish']} | "
        f"{', '.join(r['read_required'])} |"
    )
    for t in done.tries:
        if "error" in t:
            rows.append(f"| {t['try']} | failed: {t['error']} {t['status'] or ''} | | | | | | | |")
            continue
        rows.append(
            f"| {t['try']} | {t['reasoning_chars']} | {t['reasoning_tokens']} | {t['ends_in']} | "
            f"{', '.join(t['tools'])} | {t['long']} | {t['seconds']} | {t['finish']} | "
            f"{', '.join(t['read_required'])} |"
        )
    change = done.change
    head = (
        f"# {done.run}, call {done.call}: {change['kind']} {change['target']}".rstrip()
        + f"\n\nChanged in {done.places_changed} place(s). From `{change['from']}`, at "
        f"{change['commit'] or 'an unrecorded commit'}. `replay.json` has every figure, and "
        "`request.json` what was sent.\n\n"
    )
    (folder / "README.md").write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
