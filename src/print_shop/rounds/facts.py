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
- **A bought part fitted loose is held by nothing** (mqucifer/print-shop#91). On `eval-sleeve-3`
  and `eval-plug-3` the parts fitted loose by design, each fact named a part of the model's in
  `held_by`, as the fact asked, and the plan waited for a slide that could only say `NOT HELD`.
  Its fact gives `loose: true` in place of `held_by`, with its `box` and `way_in`.
- **A person's question is answered with a fact.** A note that asks something says so beneath
  it, naming what it asks, and the latest one's asks are in every compaction's note.
- **A person's word is read against the facts** (mqucifer/print-shop#314). On a design run a fact
  called a finer `--mesh` "coarser" and blamed the wrong part; a stand-in corrected both at a
  questions stop, the model agreed, and the fact stood: two compactions later the claim was back,
  and it was rewritten some 170 calls on. So when a person's answers, note or pick arrive, each
  fact is listed beside them (`beside`), and `needs` and `done` wait until `mark_facts` marks each
  kept or changed against what was said (`marked`). A changed fact is rewritten in that call, and
  `record/FACTS.md` keeps its old words and who changed it. A run with no facts lists nothing.
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
#: Beneath the facts in `record/FACTS.md`, once a fact has been changed against a person's word.
CHANGED_HEADING = "## Changed against a person's word"
CHANGED_ABOUT = (
    "Each fact rewritten with `mark_facts`, with its old words, which no longer hold. Only the "
    "facts above go into a new conversation or a compaction's note."
)
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
    # A bought part fitted loose, that nothing holds by design, in place of `held_by`.
    loose: bool = False

    def plain(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, False)}

    def line(self) -> str:
        """One fact as every message gives it."""
        said = f"- `{self.name}`: "
        if self.fixed:
            said += f"box {self.box}; fixed in place, never fitted."
            said += f" {self.text}" if self.text else ""
        elif self.loose:
            said += f"box {self.box}; goes in along {self.way_in}; a loose fit, held by nothing."
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


def record(found: list[Fact], changed: list[dict[str, Any]] | None = None) -> str:
    """`record/FACTS.md`: the facts, and each one changed against a person's word, if any was."""
    lines = [RECORD_HEADING, "", RECORD_ABOUT, ""]
    lines += [one.line() for one in found] or ["None yet."]
    if changed:
        lines += ["", CHANGED_HEADING, "", CHANGED_ABOUT, ""]
        lines += [changed_line(one) for one in changed]
    return "\n".join(lines) + "\n"


def changed_line(one: dict[str, Any]) -> str:
    """One fact changed against a person's word, as `record/FACTS.md` keeps it."""
    was = Fact(**one["was"]).line().removeprefix(f"- `{one['name']}`: ")
    now = "removed" if one.get("now") is None else "rewritten"
    return (
        f"- `{one['name']}`, {now} at call {one['at_call']} against {one['against']} from "
        f"{one['by']}: {one['why']} It was: {was}"
    )


#: What a fixed part's fact is, as a refusal says it.
FIXED = (
    "If it's fixed in place and never fitted, such as a box already printed that your part sits "
    "on, give `fixed: true` in place of `way_in` and `held_by`, with its `box`."
)


def bought_shape(name: str) -> str:
    """A bought part's fact to copy, named for its file (mqucifer/print-shop#191): on the runs
    its fields were found one refusal at a time, and `way_in` was sent unsigned."""
    return (
        f'{{"name": "{name}", "text": "...", "from": "call N, bench info {name}", "box": "x '
        '0-30, y 0-30, z 0-10", "way_in": "+z", "held_by": "what `clearance <your part> '
        f'{name} --slide <the way out>` answers"}}'
    )


#: What a loose part's fact is, as a refusal says it.
LOOSE = (
    "If it's a loose fit that nothing is meant to hold, give `loose: true` in place of `held_by`, "
    "with its `box` and `way_in`."
)


def _flag(value: Any, yes: tuple[str, ...]) -> bool | None:
    """A flag as sent: true, or a word that says so; false or absent; None where it's neither."""
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, str):
        word = value.strip().lower()
        if word in ("", "false", "no"):
            return False
        if word in ("true", "yes", *yes):
            return True
    return None


def fixed_of(value: Any) -> bool | None:
    """`fixed` as sent: true, or a word that says so; false or absent; None where it's neither."""
    return _flag(value, ("fixed", "in place", "fixed in place"))


def loose_of(value: Any) -> bool | None:
    """`loose` as sent: true, or a word that says so; false or absent; None where it's neither."""
    return _flag(value, ("loose", "loose fit", "a loose fit"))


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
    loose = loose_of(args.get("loose"))
    if loose is None:
        said = args.get("loose")
        return (
            "`loose` is true, for a bought part fitted loose that nothing holds, or left out: "
            f"not {said!r}."
        )
    if loose:
        shape.append("loose")
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
    if fixed and loose:
        return (
            "`fixed` says it's in place and never fitted, and `loose` that it's fitted loose: "
            "give one of them."
        )
    if fixed:
        fitted = [f"`{k}`" for k in ("way_in", "held_by") if k in shape]
        if fitted:
            says = "says" if len(fitted) == 1 else "say"
            return (
                f"`fixed` says it's in place and never fitted, and {' and '.join(fitted)} {says} "
                "how it's fitted. Drop them for a part fixed in place, or drop `fixed` if a part "
                "of yours holds it."
            )
        if "box" not in shape:
            return "A fixed part's fact gives its `box`, its bounds from `bench info`, and `fixed`."
        return ""
    if loose and "held_by" in shape:
        return (
            "`loose` says nothing holds it, and `held_by` says what does. Drop `held_by` for a "
            "loose fit, or drop `loose` if a part of yours holds it."
        )
    wanted = ("box", "way_in") if loose else ("box", "way_in", "held_by")
    missing = [k for k in wanted if k not in shape]
    if missing:
        named = ", ".join(f"`{k}`" for k in missing)
        return (
            f"A bought part's fact gives its box, the way it goes in and what holds it: {named} "
            "missing. `box` is its bounds from `bench info`; `way_in` one of "
            f"{', '.join(WAYS)}; `held_by` the bench's `clearance --slide` answer, or what will "
            f"hold it until something does: {bought_shape(name.strip())}. {FIXED} {LOOSE}"
        )
    way = str(args.get("way_in")).strip()
    if way not in WAYS:
        signed = f"+{way.lower()}" if way.lower() in ("x", "y", "z") else "+z"
        return (
            f"`way_in` is one of {', '.join(WAYS)}, the way it goes in, signed, such as "
            f'`"way_in": "{signed}"`: not {args.get("way_in")!r}. {FIXED}'
        )
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


# A person's word, read against the facts (mqucifer/print-shop#314)

#: How a fact is marked against what a person said.
MARKS = ("kept", "changed")
#: What a person's word is, as the list beside it names it, by the kind it was relayed as.
WHENCE = {
    "answers": "the answers",
    "pick": "the pick",
    "note": "the note",
    "send_back": "the send-back note",
}
#: What leads each compaction's note while the facts listed beside a person's word aren't marked.
UNMARKED = (
    "Your facts were listed beside what a person said; mark each kept or changed against it with "
    "`mark_facts` before `needs` or `done`."
)
#: The fields a changed mark may give besides `text`: a bought part's, as `fact` takes them.
SHAPE = ("box", "way_in", "held_by", "fixed", "loose")


def whence(kinds: list[str]) -> str:
    """What a person said, as one phrase: "the answers", or "the pick and the note"."""
    said = list(dict.fromkeys(WHENCE.get(kind, "the message") for kind in kinds))
    return said[0] if len(said) == 1 else ", ".join(said[:-1]) + f" and {said[-1]}"


def beside(found: list[Fact], said: str) -> str:
    """What the model's told after a person's word, when it has facts: each fact beside it, and
    what to send. On a design run a stand-in corrected a fact and the model agreed, and the fact
    stood, to come back after compaction."""
    lines = [f"{said[0].upper()}{said[1:]} came while you hold these facts. Each, beside it:"]
    lines += [one.line() for one in found]
    example = ", ".join(f'"{one.name}": {{"is": "kept", "why": "..."}}' for one in found[:2])
    example += ", ..." if len(found) > 2 else ""
    lines += [
        "",
        f"Read each against {said}. Where they say different things, {said} is the newer word "
        "from a person: change the fact to fit it, or, if you think the fact still holds, say "
        "why. Send `mark_facts` with each of these marked kept or changed, with a line on why: "
        f"{{{example}}}. A changed one gives its new words in `text`, and is rewritten there; "
        "an empty `text` removes it. `needs` and `done` are refused until each is marked.",
    ]
    return "\n".join(lines)


def unmarked_refused(tool: str, names: list[str], said: str) -> str:
    """The refusal of `needs` or `done` while the facts beside a person's word aren't marked."""
    named = ", ".join(f"`{n}`" for n in names)
    return (
        f"`{tool}` waits: your facts were listed beside {said}, and none is marked yet. Send "
        f"`mark_facts` with each of {named} marked kept or changed against it, with a line on "
        'why: {"name": {"is": "kept", "why": "..."}}, or {"is": "changed", "why": "...", "text": '
        '"the fact in new words"}. Then send this again.'
    )


@dataclass(frozen=True)
class Mark:
    """One fact's mark against a person's word: kept, or changed to `args`, a `fact` call's
    arguments, an empty `text` removing it."""

    name: str
    said: str
    why: str
    args: dict[str, Any] | None = None


def marked(
    marks: Any, listed: list[Fact], source: str, references: set[str]
) -> tuple[list[Mark], list[str]]:
    """Each listed fact's mark, read, and what's wrong with them, in words: one left unmarked,
    marked with no reason, kept with new words, changed to its own words, or changed to a fact
    `fact` would refuse. `source` is the `from` a changed fact is given."""
    given = marks if isinstance(marks, dict) else {}
    found, wrong = [], []
    names = {one.name for one in listed}
    shape = '{"is": "kept" or "changed", "why": "..."}'
    for one in listed:
        mark = given.get(one.name)
        said = str(mark.get("is") or "").strip().lower() if isinstance(mark, dict) else ""
        if said not in MARKS:
            wrong.append(
                f"`{one.name}` isn't marked: it says {one.line().removeprefix('- ')}. Read it "
                f'against what was said, and mark it, {{"{one.name}": {shape}}}'
            )
            continue
        assert isinstance(mark, dict)
        why = str(mark.get("why") or "").strip()
        if not why:
            wrong.append(f"`{one.name}` is marked {said} with no reason: give a line on why")
            continue
        if said == "kept":
            text = mark.get("text")
            if isinstance(text, str) and " ".join(text.split()) != " ".join(one.text.split()):
                wrong.append(
                    f"`{one.name}` is marked kept, and gives new words: mark it changed, or "
                    "leave `text` out"
                )
            else:
                found.append(Mark(one.name, said, why))
            continue
        if "text" not in mark:
            wrong.append(
                f"`{one.name}` is marked changed, with no `text`: give the fact in new words, or "
                "an empty `text` to remove it"
            )
            continue
        old = one.plain()
        if not str(mark["text"] or "").strip() and not any(k in mark for k in SHAPE):
            # An empty `text` removes it, as `fact` does.
            found.append(Mark(one.name, said, why, {"name": one.name, "text": "", "from": source}))
            continue
        args = {
            # A bought part's fields it doesn't give stay as they were.
            **{k: old[k] for k in SHAPE if k in old},
            **{k: mark[k] for k in SHAPE if k in mark},
            "name": one.name,
            "text": mark["text"],
            "from": source,
        }
        problem_said = problem(args, references)
        if problem_said:
            wrong.append(f"`{one.name}` can't be changed so: {problem_said}")
            continue
        text = str(args["text"]).strip()
        now = {k: args.get(k) for k in SHAPE}
        was = {k: old.get(k) for k in SHAPE}
        if " ".join(text.split()) == " ".join(one.text.split()) and _same_shape(now, was):
            wrong.append(
                f"`{one.name}` is marked changed, and its words are as they were: change them, "
                "or mark it kept, with why"
            )
            continue
        found.append(Mark(one.name, said, why, args))
    for name in given:
        if name not in names:
            wrong.append(
                f"`{name}` isn't one of the facts listed beside what was said: a new fact is set "
                "with `fact`"
            )
    return found, wrong


def _same_shape(now: dict[str, Any], was: dict[str, Any]) -> bool:
    def plain(value: Any) -> str:
        return str(value or "").strip().lower()

    flags = {"fixed": fixed_of, "loose": loose_of}
    return all(
        bool(flags[k](now[k])) == bool(was[k]) if k in flags else plain(now[k]) == plain(was[k])
        for k in SHAPE
    )
