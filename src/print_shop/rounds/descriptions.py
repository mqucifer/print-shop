"""A description of the part says which version it describes (mqucifer/print-shop#53).

The model describes the part in its own words: the breakdown, each need's `what`, and the plan's
figures. In 4 of the 9 stained-glass evals on 430fc1a the breakdown still described the part as it
was before a change: on `eval-tip-2` the frame's pocket was reshaped, and the breakdown still gave
the original right-triangle pocket. A stand-in caught it each time, and nothing in the harness did.

So each description keeps the part's files as they stood when it was written, by what they hold
(`paths.content_hash`, so a STEP file written again the same isn't a change). When a part file
changes after it, the description is stale:
- the next stop shows each stale one under its own heading, with what it is, the call it was
  written at, and the files changed since;
- `done` refuses until each is sent again (`breakdown`, or `restate` with its new words) or said
  to still hold, with a reason (`restate`). A confirmation is shown at the next stop, as a
  set-aside is.

The part's files are its geometry. Once `done` or a sheet names them, only those and the given
references count, so a helper the model made to check against doesn't make a description stale.
Before that, every piece of geometry counts. A run from before this has no descriptions kept, and
nothing of it goes stale.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from print_shop.rounds import paths

BREAKDOWN = "breakdown"
PLAN = "plan"
NEED = "need "
#: How each was last kept: written by its own tool, sent again with new words, or said to hold.
WRITTEN, SENT_AGAIN, HOLDS = "written", "sent again", "holds"
HEADING = "## Descriptions written before the part changed"
SINCE_HEADING = "## Descriptions sent again or said to still hold since the last stop"
#: A plan describes the part where it gives a figure: "the plan's figures".
FIGURE = re.compile(r"\d")
SHAPE = (
    '`descriptions` is a list, each {"of": "breakdown", "holds": "why it still holds"}, '
    '{"of": "N1", "now": "the need\'s new `what`"} or {"of": "plan", "now": "the plan, again"}. '
    '`of` may name several, as ["N1", "N2"], where one reason covers them.'
)


def need_key(nid: str) -> str:
    return f"{NEED}{nid}"


def geometry(work: Path, tracked: dict[str, list[Any]] | None = None) -> dict[str, list[Any]]:
    """Each piece of geometry under `work`, outside what runs make: its hash, its size and its
    time, as `[hash, size, mtime_ns]`. A file `tracked` (`seen`) has at the same size and time
    keeps its hash, so a large STEP file isn't read again after every call."""
    tracked = tracked or {}
    found: dict[str, list[Any]] = {}
    for name in paths.walk_plain(work, skip_made=True):
        if name.suffix.lower() not in paths.HASHED:
            continue
        key = str(name)
        stat = os.lstat(work / name)
        was = tracked.get(key)
        if was and was[0] and list(was[2:4]) == [stat.st_size, stat.st_mtime_ns]:
            found[key] = [was[0], stat.st_size, stat.st_mtime_ns]
            continue
        data = paths.read_bytes_inside(work, key)
        found[key] = [paths.content_hash(key, data), stat.st_size, stat.st_mtime_ns]
    return found


def seen(
    tracked: dict[str, list[Any]], now: dict[str, list[Any]], at_call: int
) -> dict[str, list[Any]]:
    """Each file's hash and the call by which the harness saw it as it is, as `[hash, call, size,
    mtime_ns]`: a file gone is `[None, call]`."""
    found: dict[str, list[Any]] = {}
    for name, (sha, size, mtime) in now.items():
        was = tracked.get(name)
        call = was[1] if was and was[0] == sha else at_call
        found[name] = [sha, call, size, mtime]
    for name, was in tracked.items():
        if name not in now:
            found[name] = was if was[0] is None else [None, at_call]
    return found


def hashes(tracked: dict[str, list[Any]]) -> dict[str, str]:
    """The part's version as a description keeps it: each file there, by what it holds."""
    return {name: was[0] for name, was in tracked.items() if was[0] is not None}


@dataclass
class Stale:
    key: str
    what: str
    written_at: int
    held_at: int | None
    # Each file changed since: its name, `changed`, `new` or `gone`, and the call by which it was.
    changed: list[tuple[str, str, int | None]]

    def plain(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "what": self.what,
            "written_at": self.written_at,
            "held_at": self.held_at,
            "changed": [list(one) for one in self.changed],
        }

    @classmethod
    def load(cls, kept: dict[str, Any]) -> Stale:
        return cls(
            str(kept["key"]),
            str(kept["what"]),
            int(kept["written_at"]),
            kept.get("held_at"),
            _changed(kept.get("changed") or []),
        )


def among(names: list[str] | None) -> set[str] | None:
    """The files that count as the part: those named, with the given references. None is every
    piece of geometry, before anything names the part."""
    if not names:
        return None
    return {_plain(name) for name in names}


def stale(
    described: dict[str, dict[str, Any]],
    tracked: dict[str, list[Any]],
    part: set[str] | None,
) -> list[Stale]:
    """Each description written about files that have changed since, among the part's files."""
    now = hashes(tracked)
    found = []
    for key, one in described.items():
        then: dict[str, str] = one.get("files") or {}
        changed = []
        for name in sorted(set(then) | set(now)):
            if part is not None and name not in part and not name.startswith("references/"):
                continue
            if then.get(name) == now.get(name):
                continue
            how = "new" if name not in then else ("gone" if name not in now else "changed")
            by = tracked.get(name)
            changed.append((name, how, by[1] if by else None))
        if changed:
            held = one.get("held_at")
            found.append(Stale(key, one["what"], int(one["written_at"]), held, changed))
    return found


def changed_said(changed: list[tuple[str, str, int | None]]) -> str:
    words = {"changed": "changed", "new": "is new", "gone": "is gone"}
    said = []
    for name, how, call in changed:
        by = f" by call {call}" if call is not None else ""
        said.append(f"`{name}` {words[how]}{by}")
    return ", ".join(said)


def line(one: Stale) -> str:
    held = f", said to hold at call {one.held_at}" if one.held_at is not None else ""
    return (
        f"- {one.what}, written at call {one.written_at}{held}: since then "
        f"{changed_said(one.changed)}"
    )


def lines(found: list[Stale]) -> list[str]:
    return [line(one) for one in found]


def refused(found: list[Stale]) -> str:
    """`done`'s refusal: what it is, when it was written, and what changed since."""
    return "\n".join(
        [
            "Nothing was checked: these describe the part as it was before a part file changed, "
            "and a person reads them beside the result:",
            *lines(found),
            "Read each against the part as it stands. If it's wrong, send it again: the "
            "breakdown with `breakdown`, a need's `what` or the plan with `restate` and its new "
            "words in `now`. If it still holds, say why with `restate` and `holds`: the person at "
            "the next stop reads it. Then `done` again.",
        ]
    )


def at_stop(found: list[Stale]) -> list[str]:
    """The heading at a stop, for the person there."""
    if not found:
        return []
    return [
        HEADING,
        "",
        "Each was written about the part as it stood then. A part file has changed since, so it "
        "may not describe the part as it is.",
        "",
        *lines(found),
        "",
    ]


def since_said(kept: list[dict[str, Any]]) -> list[str]:
    """Each description sent again or said to hold since the last stop."""
    shown = [one for one in kept if not one.get("shown_at")]
    if not shown:
        return []
    out = [SINCE_HEADING, ""]
    for one in shown:
        over = f", over {changed_said(_changed(one['changed']))}" if one.get("changed") else ""
        if one["how"] == HOLDS:
            out.append(
                f"- {one['what']}, said to still hold at call {one['at_call']}{over}: {one['why']}"
            )
        else:
            out.append(f"- {one['what']}, sent again at call {one['at_call']}{over}:")
            out.append(f"  - was: {one['was']}")
            out.append(f"  - now: {one['now']}")
    return [*out, ""]


def _changed(raw: list[Any]) -> list[tuple[str, str, int | None]]:
    return [(str(a), str(b), c) for a, b, c in raw]


def read(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """`restate`'s list, each entry naming what it's about and either `holds` or `now`."""
    if not isinstance(raw, list) or not raw:
        return [], [SHAPE]
    found: list[dict[str, Any]] = []
    wrong: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - {"of", "holds", "now"} or "of" not in entry:
            wrong.append(SHAPE)
            continue
        named = entry["of"] if isinstance(entry["of"], list) else [entry["of"]]
        named = [str(one).strip() for one in named if str(one).strip()]
        holds = str(entry.get("holds") or "").strip()
        now = str(entry.get("now") or "").strip()
        if not named:
            wrong.append(f"`of` names what it's about. {SHAPE}")
        elif bool(holds) == bool(now):
            wrong.append(
                f"{', '.join(named)}: give `holds`, why it still holds, or `now`, its new words, "
                "and not both"
            )
        elif now and len(named) > 1:
            wrong.append(f"{', '.join(named)}: new words in `now` are for one at a time")
        for one in named:
            found.append({"of": one, "holds": holds, "now": now})
    return found, wrong


def _plain(name: str) -> str:
    return str(Path(name.removeprefix("/work/")).as_posix()).removeprefix("./")
