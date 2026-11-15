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

import difflib
import json
import re
from typing import Any

#: The most characters a part's name has.
NAME_LENGTH = 48
NAME = re.compile(rf"[A-Za-z0-9][A-Za-z0-9 ._()/-]{{0,{NAME_LENGTH - 1}}}")
SHAPE = (
    '{"parts": [{"name": "plate", "what": "what it is and does"}, ...], "joins": [{"parts": '
    '["plate", "arm"], "how": "what holds or connects them"}, ...], "look": ["what the look rests '
    'on", ...], "from": "what you read it from: the request, references/..."}'
)


#: What a join is, said when one is refused: on the runs, a join named one part, such as the frame
#: alone, and was refused twice before the model saw why (mqucifer/print-shop#54).
JOIN = (
    "A join is what holds one part to another, or connects them: the leading holding a pane by "
    'its lips is {"parts": ["frame", "pane_1"], "how": "pane_1 sits in its pocket in the frame, '
    'held by the lips on both faces"}, and a bracket\'s is {"parts": ["plate", "arm"], "how": '
    '"the arm is printed as one with the plate"}. What a part is on its own goes in its `what`, '
    "and with one part there's no join"
)


def _listed(raw: dict[str, Any], key: str, wrong: list[str], shape: str) -> list[Any]:
    """A field that's a list, or what's wrong with it: one sent as its JSON text is read as the
    list, as the tool reads it, and anything else is named (mqucifer/print-shop#145)."""
    given = raw.get(key)
    if isinstance(given, str) and given.strip()[:1] == "[":
        try:
            given = json.loads(given)
        except ValueError as exc:
            wrong.append(
                f"`{key}` is a string holding JSON that can't be read ({exc}): send the list "
                f"itself, `\"{key}\": [{shape}, ...]`, not its text in quotes"
            )
            return []
    if given is None:
        return []
    if not isinstance(given, list):
        wrong.append(f"`{key}` is a list, `\"{key}\": [{shape}, ...]`: not {_kind(given)}")
        return []
    return given


def _kind(value: Any) -> str:
    kinds = {dict: "an object", str: "a string", bool: "true or false"}
    return kinds.get(type(value), "a number")


def _not_in_a_name(name: str) -> str:
    """Why a part's name isn't one: what in it isn't a letter, a digit, a space or `. _ ( ) / -`,
    or that it's empty, starts with something else, or is too long."""
    if not name:
        return "it's empty"
    odd = sorted({c for c in name if not re.fullmatch(r"[A-Za-z0-9 ._()/-]", c)})
    if odd:
        named = ", ".join(f"`{c}`" for c in odd)
        return f"{named} isn't in a name, so say it in words, as `and` for `+`"
    if len(name) > NAME_LENGTH:
        return f"it's {len(name)} characters, over {NAME_LENGTH}: what it is goes in `what`"
    return "it starts with a letter or a digit"


def _meant(name: str, names: list[str]) -> str | None:
    """The part a join most likely means by a name `parts` doesn't have: one whose name holds
    it or is held by it, as `channel` for `channel (the M)`, or is all but it."""
    low = name.casefold()
    holding = [n for n in names if low and (low in n.casefold() or n.casefold() in low)]
    if len(holding) == 1:
        return holding[0]
    near = difflib.get_close_matches(name, names, n=1, cutoff=0.8)
    return near[0] if near and not holding else None


def read(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """The breakdown as it's kept, or everything that's wrong with it, each fault said once
    (mqucifer/print-shop#145): its names, its joins, and its parts in no join. In an eval a
    breakdown was sent three times for a part named with a `+`, a join that named `channel` where
    `parts` had `channel (the M)`, and a part left out of every join. So a name says which
    character isn't one; a join that names a part otherwise says which part it most likely means,
    once, and that part isn't then also said to be in no join; and a field of the wrong kind is
    named, with the rest still read."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}, [f"the breakdown is an object: {SHAPE}"]
    if not isinstance(raw, dict):
        return {}, [f"the breakdown is an object: {SHAPE}"]
    wrong: list[str] = []
    unknown = sorted(set(raw) - {"parts", "joins", "look", "from"}, key=str)
    part_keys = [k for k in unknown if k in ("name", "what")]
    join_keys = [k for k in unknown if k == "how"]
    if part_keys:
        named = " and ".join(f"`{k}`" for k in part_keys)
        wrong.append(f'{named} go in each part, in `parts`: {{"name": "...", "what": "..."}}')
    if join_keys:
        wrong.append('`how` goes in each join, in `joins`: {"parts": ["a", "b"], "how": "..."}')
    rest = [k for k in unknown if k not in part_keys and k not in join_keys]
    if rest:
        wrong.append(f"it has parts, joins, look and from: not {rest}")
    parts: list[dict[str, str]] = []
    for entry in _listed(raw, "parts", wrong, '{"name": "...", "what": "..."}'):
        if not isinstance(entry, dict) or set(entry) - {"name", "what"}:
            shown = json.dumps(entry)[:60] if not isinstance(entry, dict) else (
                f"{entry.get('name')!r}, which also has "
                f"{sorted(set(entry) - {'name', 'what'}, key=str)}"
            )
            wrong.append(
                f'each part is {{"name": "...", "what": "..."}} and nothing else: not {shown}'
            )
            continue
        name, what = str(entry.get("name") or "").strip(), str(entry.get("what") or "").strip()
        if not NAME.fullmatch(name):
            wrong.append(
                f"a part's `name` is short, of letters, digits and spaces: not {name!r}, as "
                f"{_not_in_a_name(name)}"
            )
        elif name in [p["name"] for p in parts]:
            wrong.append(f"two parts are named `{name}`")
        if not what:
            wrong.append(f"the part `{name}` says `what` it is and does")
        parts.append({"name": name, "what": what})
    if not parts:
        wrong.append("`parts` names at least one part, each with what it is and does")
    names = [p["name"] for p in parts]
    joins: list[dict[str, Any]] = []
    #: Each part a join named otherwise, by the name the join gave it.
    otherwise: dict[str, str | None] = {}
    for entry in _listed(raw, "joins", wrong, '{"parts": ["a", "b"], "how": "..."}'):
        if not isinstance(entry, dict) or set(entry) - {"parts", "how"}:
            wrong.append('each join is {"parts": ["a", "b"], "how": "..."} and nothing else')
            continue
        between = entry.get("parts")
        how = str(entry.get("how") or "").strip()
        if not isinstance(between, list) or len(between) < 2:
            wrong.append(
                f"a join names the two or more parts it holds together, in `parts`, and this one "
                f"names {len(between) if isinstance(between, list) else 0}. {JOIN}"
            )
            continue
        between = [str(b).strip() for b in between]
        for name in between:
            if name not in names and name not in otherwise:
                otherwise[name] = _meant(name, names)
        if not how:
            wrong.append(f"the join of {', '.join(between)} says `how` they're held or connected")
        joins.append({"parts": between, "how": how})
    listed = ", ".join(f"`{n}`" for n in sorted(names))
    for name, meant in otherwise.items():
        count = sum(name in join["parts"] for join in joins)
        times = f", in {count} joins" if count > 1 else ""
        if meant is not None:
            wrong.append(
                f"a join names `{name}`{times}, and `parts` names it `{meant}`: a join names "
                f"each part exactly as `parts` does, so name it `{meant}` in the join, or "
                f"`{name}` in `parts`"
            )
        else:
            wrong.append(
                f"a join names `{name}`{times}, which isn't one of `parts`: a join names each "
                f"part exactly as `parts` does ({listed})"
            )
    if len(parts) > 1:
        joined = {name for join in joins for name in join["parts"]}
        joined |= {meant for meant in otherwise.values() if meant is not None}
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
    wrong = list(dict.fromkeys(wrong))
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
