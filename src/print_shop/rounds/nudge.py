"""A bench check that keeps failing gets a line in its reply: show what you have and ask what you
need, or end with `blocked`, as a failed, complete run is a valid end (the Sponsor, 2026-11-03).

A check is its command and the files it names, not its other arguments: on `suncatcher-2` the model
passed `capture` a lower `--min-lip` on each try, and each try was the same check failing. Exit 3 is
the command itself being wrong, so it's neither. Any other exit but 0 doesn't pass: 1, both methods
agree something's wrong, 2, there's no answer to rely on, or the container's own.

**A pass counts only as a need checks it.** `suncatcher-2` passed `capture` at `--min-lip 0.25`
after failing at 1.2, 0.35, 0.45 and 0.3, where its need runs it at 1.2. So a pass starts the count
again only if its arguments are a settled need's, or if no need runs that check. And a call of a
need's check with other arguments says so in its reply, pass or fail: `done` checks the need's.

**The nudge says why it fired,** a line a try: its call, the arguments that differ from the first
try, and the start of what the bench said. It's the newest bench reply, so compaction keeps it
whole, where older replies are cut to their first line (`exit 1`) and can't be compared.

`AFTER` is from the design runs' records (`~/print-shop/proofs/m18-close/streaks.json`): of 41 runs
of failures of one check in a row, 35 were 3 or fewer, and the 6 of 4 or more include both of
`suncatcher-2`'s loops. It's a line in a reply, never a limit: nothing is cut.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from print_shop.rounds import prompt

#: Failures of one check, without a pass that counts, before its reply says so, and each one after.
AFTER = 4
#: The command was wrong: not an answer either way.
USAGE = 3
#: A word that names a file: it ends in a suffix that starts with a letter, so `0.35` isn't one.
SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]*$")
#: The bench's options that take no value, and those that take every word up to the next option.
FLAGS = frozenset({"--one-body"})
MANY = frozenset({"--views", "--section"})
#: How much of what the bench said each try's line keeps.
WHY_CHARS = 140

Check = tuple[str, tuple[str, ...]]
Options = dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Try:
    """One bench call, as the ledger keeps it."""

    at_call: int
    argv: list[str]
    exit: int
    verdict: str | None = None
    why: str | None = None


@dataclass(frozen=True)
class Wanted:
    """A settled need that runs a bench check, and its command."""

    need: str
    command: str
    argv: list[str]
    #: A non-negotiable's need, and its words: its check counts only at its own settings.
    fixed: bool = False
    quote: str = ""


def _words(argv: list[str]) -> list[str]:
    return argv[1:] if argv[:1] == ["bench"] else argv


def check_of(argv: list[str]) -> Check:
    """A bench command's check: its command and the files it names, whatever else it's given."""
    words = _words(argv)
    if not words:
        return "", ()
    files = {
        w
        for w in words[1:]
        if not w.startswith("-") and "=" not in w and SUFFIX.search(PurePosixPath(w).name)
    }
    return words[0], tuple(sorted(files))


def options(argv: list[str]) -> Options:
    """Each option a command is given, and its values, by name."""
    words = _words(argv)
    found: dict[str, list[str]] = {}
    i = 1
    while i < len(words):
        word = words[i]
        i += 1
        if not word.startswith("--"):
            continue
        name, equals, value = word.partition("=")
        if equals:
            found.setdefault(name, []).append(value)
        elif name in FLAGS:
            found.setdefault(name, [])
        elif name in MANY:
            values = found.setdefault(name, [])
            while i < len(words) and not words[i].startswith("--"):
                values.append(words[i])
                i += 1
        elif i < len(words):
            found.setdefault(name, []).append(words[i])
            i += 1
        else:
            found.setdefault(name, [])
    return {name: tuple(values) for name, values in found.items()}


def _same(a: tuple[str, ...] | None, b: tuple[str, ...] | None) -> bool:
    if a is None or b is None:
        return a is b
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        try:
            if float(x) != float(y):
                return False
        except ValueError:
            if x != y:
                return False
    return True


def _shown(values: tuple[str, ...] | None) -> str | None:
    return None if values is None else " ".join(values)


def differences(argv: list[str], other: list[str]) -> list[tuple[str, str | None, str | None]]:
    """Each option given differently: its name, as `argv` gives it, and as `other` does."""
    mine, theirs = options(argv), options(other)
    return [
        (name, _shown(mine.get(name)), _shown(theirs.get(name)))
        for name in sorted(set(mine) | set(theirs))
        if not _same(mine.get(name), theirs.get(name))
    ]


def _given(name: str, value: str | None) -> str:
    return f"`{name} {value}`" if value else f"`{name}`"


def wanted_for(check: Check, wanted: list[Wanted]) -> list[Wanted]:
    """The settled needs that run this check."""
    return [w for w in wanted if check_of(w.argv) == check]


def as_a_need(argv: list[str], wanted: list[Wanted]) -> bool:
    """Whether this call is a settled need's command, or its check is no need's."""
    mine = wanted_for(check_of(argv), wanted)
    return not mine or any(not differences(argv, w.argv) for w in mine)


def off_need(argv: list[str], wanted: list[Wanted]) -> list[dict[str, Any]]:
    """Each need whose check this is, run here with other arguments: what differs, by need. None
    if it's a need's command as it stands."""
    mine = wanted_for(check_of(argv), wanted)
    if as_a_need(argv, mine):
        return []
    return [
        {
            "need": w.need,
            "command": w.command,
            "differs": differences(argv, w.argv),
            **({"fixed": True, "quote": w.quote} if w.fixed else {}),
        }
        for w in mine
    ]


def _differs_said(one: dict[str, Any]) -> str:
    parts = []
    for name, got, wants in one["differs"]:
        if wants is None:
            parts.append(f"{_given(name, got)}, the need doesn't give it")
        elif got is None:
            parts.append(f"no `{name}`, the need says {_given(name, wants)}")
        else:
            parts.append(f"`{name} {got}`, the need says `{wants}`")
    return "; ".join(parts)


def not_the_requirement(found: list[dict[str, Any]]) -> str:
    """What the model's told, before the bench's answer, of a fixed need's check run at other
    settings: on `suncatcher-2` `capture` passed at `--min-lip 0.25` where the request said 1.2.
    It's said on the first try, pass or fail, and the reading can't pass the need."""
    lines = []
    for one in found:
        if not one.get("fixed"):
            continue
        words = f', "{one["quote"]}"' if one.get("quote") else ""
        lines.append(
            f"NOT THE REQUIREMENT: `{one['need']}` is fixed, a non-negotiable of the request"
            f"{words}, and its check is `{one['command']}`. This try has {_differs_said(one)}. "
            "Whatever it reads below, it isn't the requirement and can't pass it: `done` runs the "
            "requirement's own command. A fixed need changes only by the Sponsor."
        )
    return "\n".join(lines)


def off_need_said(found: list[dict[str, Any]]) -> str:
    """What the model's told of a need's check run with other arguments."""
    lines = []
    for one in found:
        if one.get("fixed"):
            continue  # said first, by `not_the_requirement`
        lines.append(
            f"This is `{one['need']}`'s check with other arguments: {_differs_said(one)}. `done` "
            f"checks the need's command, `{one['command']}`, so this reading isn't the need's."
        )
    return "\n".join(lines)


def streak(earlier: list[Try], check: Check, wanted: list[Wanted]) -> list[Try]:
    """The tries of this check since it last passed as a need checks it, oldest first: `earlier`
    is every bench call, oldest first, this one included. A wrong command is left out."""
    found: list[Try] = []
    for one in reversed(earlier):
        if check_of(one.argv) != check or one.exit == USAGE:
            continue
        if one.exit == 0 and as_a_need(one.argv, wanted):
            break
        found.append(one)
    return list(reversed(found))


def failures(tries: list[Try]) -> int:
    return sum(1 for one in tries if one.exit != 0)


def said(check: Check, tries: list[Try], wanted: list[Wanted]) -> str:
    """The nudge, with why it fired: each try since the check last passed as a need checks it."""
    command, files = check
    on = f" on {', '.join(f'`{f}`' for f in files)}" if files else ""
    since = "without passing as a need checks it" if wanted_for(check, wanted) else "in a row"
    first = tries[0]
    lines = [
        f"`{command}`{on} has failed {failures(tries)} times {since}, whatever its other arguments:"
    ]
    for one in tries:
        if one is first:
            given = " ".join(
                f"{name} {' '.join(values)}".strip() for name, values in options(one.argv).items()
            )
            how = f"`{given}`" if given else "no options"
            how = f"{how}, the first try" if len(tries) > 1 else how
        else:
            changed = [
                f"{_given(name, got) if got is not None else f'no `{name}`'}"
                f" (first try: {_given(name, was) if was is not None else 'none'})"
                for name, got, was in differences(one.argv, first.argv)
            ]
            how = ", ".join(changed) or "the same arguments"
        outcome = one.verdict or ("passed" if one.exit == 0 else f"exit {one.exit}")
        if one.exit == 0:
            outcome += ", but not as a need checks it"
        why = (one.why or "").strip().replace("\n", " ")
        why = why if len(why) <= WHY_CHARS else f"{why[:WHY_CHARS]}…"
        lines.append(f"- call {one.at_call}, {how}: {outcome}" + (f": {why}" if why else ""))
    lines.append(prompt.STUCK)
    return "\n".join(lines)
