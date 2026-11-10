"""What the model established, kept by name and carried into every new conversation
(mqucifer/print-shop#78).

On a design run the model worked out which way a fan goes in. The context filled, the run
started again from the record, and that was lost: it worked from the opposite picture for four
hours. On another, after a restart, it measured the same parts again, 42 minutes in all. The
record kept bench tries, needs and rounds, and nothing the model found with its own scripts
(`~/print-shop/skill-evals/context-study-2026-11-08/findings.md`, finding 5).

So the model keeps what it establishes with `fact`: a name, a short text with its figures, and the
call it came from. Setting a name again replaces it. The harness writes them to `record/FACTS.md`,
and each goes whole into the restart message, every compaction's note and every resume.

- **A bought part's fact has a fixed shape.** Named for its file under `references/`, it gives the
  part's box, the way it goes in, and what holds it: the bench's `clearance --slide` answer
  (mqucifer/print-shop#76), or what will, until something does. A name holds one fact, so each
  bought part has one.
- **A given part fixed in place has no way in** (mqucifer/print-shop#86). On `eval-lid-3` the
  base box a lid sits on, already printed, had two facts refused until it was given a way in. Its
  fact gives its box and `fixed: true` in place of `way_in` and `held_by`: it's in place, never
  fitted, and nothing of the model's holds it. A fitted bought part's shape is unchanged.
- **A person's question is answered with a fact.** A note that asks something says so beneath
  it, naming what it asks, and the latest one's asks are in every compaction's note.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

#: The ways a part can go in, as `clearance --slide` names them.
WAYS = ("+x", "-x", "+y", "-y", "+z", "-z")
NAME_CHARS = 80
#: A fact is short: its figures and a line on them. Longer is notes, which a script's output
#: or a file holds better. Every compaction's note carries each one whole.
TEXT_CHARS = 600
SOURCE_CHARS = 200
#: A bought part's file, under `references/`.
BOUGHT = re.compile(r"references/[^/]+\.(?:step|stp|stl|brep|3mf|obj)", re.IGNORECASE)
HEADING = "Facts you set with `fact`, as `record/FACTS.md` holds them:"
NONE_YET = (
    "No facts set yet. When you establish a figure or a finding, such as a bought part's box, the "
    "way it goes in or what holds it, keep it with `fact`: it's carried into every new "
    "conversation."
)
RECORD = "record/FACTS.md"
RECORD_HEADING = "# Facts"
RECORD_ABOUT = (
    "What the model established, each set with `fact` and replaced by setting its name again. "
    "Written by the harness: each goes whole into every new conversation and every compaction's "
    "note."
)
#: What the start says a fact is for, in two lines.
START = (
    "Keep each fact you establish with `fact`: a figure or a finding you measured, read or were "
    "told, with the call it came from, such as a bought part's box, the way it goes in and what "
    "holds it.\nFacts go whole into every new conversation and every compaction's note, so what "
    "you worked out once is never worked out again; when a person asks you something, answer "
    "with a fact."
)
#: Beneath a note that asks something.
ANSWER = (
    "This note asks you {asks}. Answer each with `fact`, named for what it asks, so the answer is "
    "carried into every later conversation, then go on."
)
#: Words that open a sentence asking for an answer, as a note puts it: "Say which side the plug
#: faces."
ASKING_VERBS = ("say", "tell", "explain", "describe", "state", "name", "confirm")
SENTENCE = re.compile(r"[^.?!\n]+[.?!]?")
ASK_CHARS = 200
ASKS_SHOWN = 4


@dataclass(frozen=True)
class Fact:
    name: str
    text: str
    source: str
    at_call: int
    # A bought part's.
    box: str = ""
    way_in: str = ""
    held_by: str = ""
    # A given part already in place and never fitted, in place of `way_in` and `held_by`.
    fixed: bool = False

    def plain(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, False)}

    def line(self) -> str:
        """One fact as every message gives it."""
        said = f"- `{self.name}`: "
        if self.fixed:
            said += f"box {self.box}; fixed in place, never fitted."
            said += f" {self.text}" if self.text else ""
        elif self.box or self.way_in or self.held_by:
            said += f"box {self.box}; goes in along {self.way_in}; held by {self.held_by}."
            said += f" {self.text}" if self.text else ""
        else:
            said += self.text
        return f"{said} (from {self.source}, kept at call {self.at_call})"


def load(kept: dict[str, dict[str, Any]]) -> list[Fact]:
    return [Fact(**one) for one in kept.values()]


def bought(name: str) -> bool:
    return BOUGHT.fullmatch(name) is not None


def block(found: list[Fact], empty: str = "") -> str:
    """The facts as a message gives them, whole, or `empty` when there are none."""
    if not found:
        return empty
    return "\n".join([HEADING, *(one.line() for one in found)])


def record(found: list[Fact]) -> str:
    """`record/FACTS.md`."""
    lines = [RECORD_HEADING, "", RECORD_ABOUT, ""]
    lines += [one.line() for one in found] or ["None yet."]
    return "\n".join(lines) + "\n"


#: What a fixed part's fact is, as a refusal says it.
FIXED = (
    "If it's fixed in place and never fitted, such as a box already printed that your part sits "
    "on, give `fixed: true` in place of `way_in` and `held_by`, with its `box`."
)


def fixed_of(value: Any) -> bool | None:
    """`fixed` as sent: true, or a word that says so; false or absent; None where it's neither."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("", "false", "no"):
            return False
        if word in ("true", "yes", "fixed", "in place", "fixed in place"):
            return True
    return None


def problem(args: dict[str, Any], references: set[str]) -> str:
    """What's wrong with a `fact` call, in words, or nothing. `references` are the given files'
    paths, `references/<name>`."""
    name, text, source = args.get("name"), args.get("text"), args.get("from")
    if not isinstance(name, str) or not name.strip() or "\n" in name:
        return "`name` is a short name on one line, such as `fan-way-in`."
    if len(name) > NAME_CHARS:
        return f"`name` is {len(name)} characters: at most {NAME_CHARS}."
    if not isinstance(text, str):
        return "`text` is the fact, in words."
    if len(text) > TEXT_CHARS:
        return (
            f"`text` is {len(text)} characters: a fact is at most {TEXT_CHARS}, its figures and a "
            "line on them. Split it into facts by name, each one thing."
        )
    if not isinstance(source, str) or not source.strip():
        return "`from` says where it came from: the call and what it was."
    if len(source) > SOURCE_CHARS:
        return f"`from` is {len(source)} characters: at most {SOURCE_CHARS}."
    shape = [k for k in ("box", "way_in", "held_by") if str(args.get(k) or "").strip()]
    fixed = fixed_of(args.get("fixed"))
    if fixed is None:
        said = args.get("fixed")
        return f"`fixed` is true, for a given part fixed in place, or left out: not {said!r}."
    if fixed:
        shape.append("fixed")
    if not bought(name.strip()):
        if shape:
            named = ", ".join(f"`{k}`" for k in shape)
            return (
                f"{named} belong to a bought part's fact, named for its file under "
                "`references/`, such as `references/fan.step`. For anything else, put it in "
                "`text`."
            )
        return ""
    if name.strip() not in references:
        given = ", ".join(f"`{r}`" for r in sorted(references)) or "none"
        return f"There's no `{name.strip()}`: the given files are {given}."
    if not text.strip() and not shape:
        return ""
    if fixed:
        fitted = [f"`{k}`" for k in ("way_in", "held_by") if k in shape]
        if fitted:
            return (
                f"`fixed` says it's in place and never fitted, and {' and '.join(fitted)} say "
                "how it's fitted. Drop them for a part fixed in place, or drop `fixed` if a part "
                "of yours holds it."
            )
        if "box" not in shape:
            return "A fixed part's fact gives its `box`, its bounds from `bench info`, and `fixed`."
        return ""
    missing = [k for k in ("box", "way_in", "held_by") if k not in shape]
    if missing:
        named = ", ".join(f"`{k}`" for k in missing)
        return (
            f"A bought part's fact gives its box, the way it goes in and what holds it: {named} "
            "missing. `box` is its bounds from `bench info`; `way_in` one of "
            f"{', '.join(WAYS)}; `held_by` the bench's `clearance --slide` answer, or what will "
            f"hold it until something does. {FIXED}"
        )
    if str(args.get("way_in")).strip() not in WAYS:
        return f"`way_in` is one of {', '.join(WAYS)}: not {args.get('way_in')!r}. {FIXED}"
    return ""


def asks(text: str) -> list[str]:
    """Each sentence of a person's text that asks for an answer: a question, or one that opens
    with saying, telling or explaining."""
    found = []
    for line in text.splitlines():
        line = line.strip().lstrip("-*>#0123456789. )").strip()
        for sentence in SENTENCE.findall(line):
            one = sentence.strip()
            words = one.split(maxsplit=1)
            if not words:
                continue
            first = words[0].lower().strip(",:;")
            if one.endswith("?") or first in ASKING_VERBS:
                found.append(one if len(one) <= ASK_CHARS else f"{one[:ASK_CHARS]}…")
    return found


def asked_said(found: list[str]) -> str:
    """What a list of asks reads as, in a sentence: the first few, quoted."""
    shown = [f'"{one}"' for one in found[:ASKS_SHOWN]]
    more = len(found) - len(shown)
    said = shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + f" and {shown[-1]}"
    return said + (f", and {more} more" if more > 0 else "")


def answer_line(text: str) -> str:
    """What goes beneath a person's note: nothing, unless it asks something."""
    found = asks(text)
    return ANSWER.format(asks=asked_said(found)) if found else ""
