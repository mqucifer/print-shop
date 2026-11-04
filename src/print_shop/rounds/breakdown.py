"""How the reference and the request are built, written before the needs (mqucifer/print-shop#36).

In all three stained-glass runs the model read the reference for what's in it, not how it's built:
on `suncatcher-2` it drew each ring as one loop where short bars cut them, and the frame came apart.
So before `needs`, it names the parts, what holds or connects each to the rest, and what the look
rests on. It's general to any part: a bracket's plate, arm and screw holes, a lamp's shade, socket
and base, or a panel's frame and panes.

What's held is its shape, not its words: every part is named once, every join names parts that
are there, and with more than one part every part is in a join, since a part held by nothing is
what came apart. The first stop shows it to a person, who judges the rest.
"""

from __future__ import annotations

import json
import re
from typing import Any

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._()/-]{0,47}")
SHAPE = (
    '{"parts": [{"name": "plate", "what": "what it is and does"}, ...], "joins": [{"parts": '
    '["plate", "arm"], "how": "what holds or connects them"}, ...], "look": ["what the look rests '
    'on", ...], "from": "what you read it from: the request, references/..."}'
)


def read(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """The breakdown as it's kept, or what's wrong with it."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}, [f"the breakdown is an object: {SHAPE}"]
    if not isinstance(raw, dict):
        return {}, [f"the breakdown is an object: {SHAPE}"]
    wrong: list[str] = []
    unknown = set(raw) - {"parts", "joins", "look", "from"}
    if unknown:
        wrong.append(f"it has parts, joins, look and from: not {sorted(unknown)}")
    parts: list[dict[str, str]] = []
    for entry in raw.get("parts") or []:
        if not isinstance(entry, dict) or set(entry) - {"name", "what"}:
            wrong.append('each part is {"name": "...", "what": "..."}')
            continue
        name, what = str(entry.get("name") or "").strip(), str(entry.get("what") or "").strip()
        if not NAME.fullmatch(name):
            wrong.append(f"a part's `name` is short, of letters, digits and spaces: not {name!r}")
        elif name in [p["name"] for p in parts]:
            wrong.append(f"two parts are named `{name}`")
        if not what:
            wrong.append(f"the part `{name}` says `what` it is and does")
        parts.append({"name": name, "what": what})
    if not parts:
        wrong.append("`parts` names at least one part, each with what it is and does")
    names = {p["name"] for p in parts}
    joins: list[dict[str, Any]] = []
    for entry in raw.get("joins") or []:
        if not isinstance(entry, dict) or set(entry) - {"parts", "how"}:
            wrong.append('each join is {"parts": ["a", "b"], "how": "..."}')
            continue
        between = entry.get("parts")
        how = str(entry.get("how") or "").strip()
        if not isinstance(between, list) or len(between) < 2:
            wrong.append("a join names the two or more parts it holds together, in `parts`")
            continue
        between = [str(b).strip() for b in between]
        for name in between:
            if name not in names:
                wrong.append(f"a join names `{name}`, which isn't one of `parts`")
        if not how:
            wrong.append(f"the join of {', '.join(between)} says `how` they're held or connected")
        joins.append({"parts": between, "how": how})
    if len(parts) > 1:
        joined = {name for join in joins for name in join["parts"]}
        for part in parts:
            if part["name"] not in joined:
                wrong.append(
                    f"`{part['name']}` is in no join: say what holds it to the rest, or, if "
                    "nothing does, that's for the design to answer before the needs"
                )
    look = raw.get("look") or []
    if isinstance(look, str):
        look = [look]
    if not isinstance(look, list) or not [str(one).strip() for one in look if str(one).strip()]:
        wrong.append("`look` says what the look rests on, a line each")
        look = []
    found = {
        "parts": parts,
        "joins": joins,
        "look": [str(one).strip() for one in look if str(one).strip()],
        "from": str(raw.get("from") or "").strip(),
    }
    return ({}, wrong) if wrong else (found, [])


def text(found: dict[str, Any]) -> str:
    """The breakdown as a person and the model read it."""
    lines = ["# How it's built", ""]
    if found.get("from"):
        lines += [f"Read from: {found['from']}", ""]
    lines += ["## Parts", ""]
    lines += [f"- **{p['name']}**: {p['what']}" for p in found["parts"]]
    if found["joins"]:
        lines += ["", "## What holds or connects what", ""]
        lines += [f"- {', '.join(j['parts'])}: {j['how']}" for j in found["joins"]]
    lines += ["", "## What the look rests on", ""]
    lines += [f"- {one}" for one in found["look"]]
    return "\n".join(lines) + "\n"
