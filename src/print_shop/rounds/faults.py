"""A pick's fix-first: the faults a person named, held apart from the ways they suggested
(mqucifer/print-shop#175).

On `eval-organic-3` a stand-in's fix-first named three faults and suggested a way for each. It
reached the model as one text, "<who> says to fix this first", and was quoted again at each
compaction, so the model read each suggestion as an order, down to its verb: "they explicitly
said 'split'". About 75 calls went on working round the wording, though a merge or a moved line
met every rule.

So a fix-first is a list of faults. Each says what must change (`fault`), where (`at`) and the
rule that reads it (`rule`: a line of the request, a need, or a bench check), and may suggest one
way (`suggest`). The fault is held: where its rule names a bench check, `done` reads that check
again and the fault stands until the reading passes; where it names none, `done` waits for the
way the model took, and the next stop shows it beside the fault. A suggestion is said once, when
it's given, and nothing keeps it or reads whether it was followed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from print_shop.rounds import needs
from print_shop.rounds.needs import Need, Reading
from print_shop.rounds.toolsets import Toolset

#: A fault's keys in `pick.json`. `suggest` alone may be left out.
KEYS = ("fault", "at", "rule", "suggest")
HELD = ("fault", "at", "rule")
TO_FIX = "To fix"
SUGGESTED = "Suggested, yours to take or leave"
#: Said with every fix-first, whether or not it suggests anything.
ANY_FIX = (
    "Any fix that meets every rule will do. If a suggestion would break a rule, don't use it. "
    "Say which way you chose."
)
HEADING = "Faults named to fix first"
SPAN = re.compile(r"`([^`]+)`")
#: How much of a failing reading a line says.
WHY_CHARS = 300


@dataclass
class Fault:
    """One fault a person named, as the run holds it. What they suggested isn't here."""

    id: str
    fault: str
    ask: int
    by: str
    at: str = ""
    rule: str = ""
    #: The bench check its rule names, if it names one: what `done` reads again.
    command: str = ""
    #: Whether a way was suggested with it: said at a stop, so a person knows one was.
    suggested: bool = False
    #: The way the model said it took, in its words, and the call it said so at.
    way: str = ""
    way_at: int | None = None
    #: The latest reading of its check: where (`done` or `ask`), whether it passed and why.
    read: dict[str, Any] | None = None
    #: The round whose `done` read its check as passing.
    passed_round: int | None = None
    #: The stop that showed it settled: its check passed, or its way was said.
    shown: str = ""

    def plain(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def passed(self) -> bool:
        return self.passed_round is not None

    @property
    def stands(self) -> bool:
        """A fault with a check stands until `done` reads it as passing. One with none stands
        until the way taken has been shown to a person at a stop."""
        return not self.passed if self.command else not self.shown

    def need(self) -> Need:
        """Its check as `done` reads it: the bench's own verdict, with no limit of its own."""
        return Need(self.id, self.fault, "pick", "bench", command=self.command)


def load(kept: list[dict[str, Any]]) -> list[Fault]:
    return [Fault(**one) for one in kept]


def dump(found: list[Fault]) -> list[dict[str, Any]]:
    return [one.plain() for one in found]


def check_named(rule: str, toolset: Toolset | None) -> str:
    """The bench check a rule names: a command of this run's bench, in backticks. A rule in
    words alone names none, whatever word it starts with: "thin walls" isn't `thin`."""
    for span in SPAN.findall(rule):
        command = " ".join(span.split())
        if needs.readable(Need("f", "f", "pick", "bench", command=command), toolset):
            return command
    return ""


def read(
    given: Any, ask: int, by: str, toolset: Toolset | None
) -> tuple[list[Fault], dict[str, str]]:
    """A pick's `fix_first` as faults, and each one's suggestion by its id. A plain string is one
    fault in the person's words, with nothing suggested. Raises `ValueError`, saying what a
    fault holds."""
    if given is None:
        return [], {}
    if isinstance(given, str):
        text = given.strip()
        return ([Fault(f"F{ask}.1", text, ask, by)] if text else []), {}
    shape = (
        "`fix_first` is what to fix first: words, or a list of faults, each "
        '{"fault": what must change, "at": where, "rule": the request line, need or bench check '
        'that reads it}, with "suggest", one way to fix it, if there is one'
    )
    if not isinstance(given, list):
        raise ValueError(shape)
    found: list[Fault] = []
    suggested: dict[str, str] = {}
    for k, entry in enumerate(given, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"{shape}: not {entry!r}")
        unknown = sorted(set(entry) - set(KEYS))
        if unknown:
            raise ValueError(f"{shape}: fault {k} has {', '.join(unknown)}")
        words = {key: " ".join(str(entry.get(key) or "").split()) for key in KEYS}
        missing = [key for key in HELD if not words[key]]
        if missing:
            raise ValueError(f"{shape}: fault {k} has no {', '.join(missing)}")
        one = Fault(
            f"F{ask}.{k}",
            words["fault"],
            ask,
            by,
            at=words["at"],
            rule=words["rule"],
            command=check_named(words["rule"], toolset),
            suggested=bool(words["suggest"]),
        )
        found.append(one)
        if words["suggest"]:
            suggested[one.id] = words["suggest"]
    return found, suggested


def words(one: Fault) -> str:
    """A fault as the person wrote it: what, where and its rule. Never what they suggested."""
    where = f", at {one.at}" if one.at else ""
    rule = f" Rule: {one.rule}" if one.rule else ""
    return f"`{one.id}`{where}: {one.fault}{rule}"


def sentence(one: Fault) -> str:
    """Its words, ended as a sentence, whether or not the person ended theirs."""
    return words(one).rstrip(". ") + "."


def quoted(given: Any) -> str:
    """What a need may quote of a `fix_first` as written in `pick.json`: each fault's words, and
    no suggestion, so a suggested way can't be settled as a need."""
    if isinstance(given, str):
        return given
    if not isinstance(given, list):
        return ""
    said = []
    for entry in given:
        if isinstance(entry, dict):
            said += [str(entry.get(key) or "") for key in HELD]
    return "\n".join(one for one in said if one.strip())


def brief(given: Any) -> str:
    """A `fix_first` as written in `pick.json`, in a line: each fault and where."""
    if isinstance(given, str):
        return given
    if not isinstance(given, list):
        return ""
    found = [e for e in given if isinstance(e, dict)]
    return " ".join(f"{str(e.get('fault')).rstrip('. ')} (at {e.get('at')})." for e in found)


def told(found: list[Fault]) -> str:
    """Said to whoever resumed: how the run holds each fault of the list they wrote."""
    out = []
    for one in found:
        if one.command:
            out.append(
                f"`{one.id}` is read again at `done` by `{one.command}`, and stands until it "
                "passes."
            )
        else:
            out.append(
                f"`{one.id}` names no bench check of this run in backticks in its `rule`, so "
                "nothing reads it: `done` waits for the way the model took, and the next stop "
                "shows it to you."
            )
    return "The pick's fix-first, as the run holds it: " + " ".join(out)


def said(found: list[Fault]) -> str:
    """The faults as the record of what the person said keeps them."""
    return "\n".join(f"- {words(one)}" for one in found)


def _held_how(one: Fault) -> str:
    if one.command:
        return f" `done` reads `{one.command}` again, and this stands until it passes."
    return " `done` waits for the way you took, and the next stop shows it beside this."


def relay(who: str, found: list[Fault], suggested: dict[str, str]) -> str:
    """What the model is told, once: the faults under "To fix", the suggestions under their own
    heading, and that any fix meeting every rule will do."""
    lines = [
        f"Apart from the look, {who[0].lower()}{who[1:]} says to fix this first.",
        "",
        f"{TO_FIX}:",
        *[f"- {sentence(one)}{_held_how(one)}" for one in found],
        "",
    ]
    if suggested:
        lines += [
            f"{SUGGESTED}:",
            *[f"- `{fid}`: {way}" for fid, way in suggested.items()],
            "",
        ]
    named = found[0].id
    lines.append(
        f"{ANY_FIX} It goes in `ways`, at your next `ask` or `done`, by the fault's id: "
        f'{{"{named}": "what you did"}}.'
    )
    return "\n".join(lines)


def record(found: list[Fault]) -> str:
    """What's kept of a fix-first for a conversation started again from the record: the faults,
    with no suggestion."""
    return said(found)


def took(found: list[Fault], given: Any, at_call: int) -> list[str]:
    """Each way the model says it took, kept with its fault. What comes back is what's wrong:
    a way for a fault that isn't one, or one with no words."""
    if given is None:
        return []
    if not isinstance(given, dict):
        return ['`ways` is each fault\'s id with the way you took: {"F2.1": "what you did"}.']
    by_id = {one.id: one for one in found}
    wrong = []
    for fid, way in given.items():
        one = by_id.get(str(fid).strip().strip("`"))
        text = " ".join(str(way or "").split())
        if one is None:
            known = ", ".join(f"`{f.id}`" for f in found) or "none"
            wrong.append(f"`ways` names `{fid}`, which isn't a fault named to fix first: {known}.")
        elif not text:
            wrong.append(f"`ways` gives `{fid}` no words: say which way you took.")
        elif text != one.way:
            one.way, one.way_at = text, at_call
    return wrong


def unsaid(found: list[Fault]) -> list[Fault]:
    """Each fault that stands with no way said for it yet."""
    return [one for one in found if one.stands and not one.way]


def unsaid_refused(found: list[Fault]) -> str:
    named = "\n".join(f"- {words(one)}" for one in found)
    example = f'{{"{found[0].id}": "what you did"}}'
    return (
        "`done` waits for the way you took for each fault named to fix first:\n"
        f"{named}\n"
        f"Say it in `ways`, by the fault's id: {example}. Any fix that meets every rule will do; "
        "the person at the next stop reads your words beside the fault."
    )


def took_reading(
    one: Fault, reading: Reading, where: str, round_n: int | None, ask: int | None = None
) -> None:
    """A reading of a fault's check, kept. Only one at `done` settles it."""
    why = reading.why
    if not reading.passed and reading.record.get("why"):
        why = f"{reading.verdict}: {reading.record['why']}" if reading.verdict else reading.why
    one.read = {
        "where": where,
        "round": round_n,
        "ask": ask,
        "passed": reading.passed,
        "verdict": reading.verdict,
        "why": " ".join(str(why).split())[:WHY_CHARS],
    }
    if reading.passed and where == "done":
        one.passed_round = round_n


def unread(one: Fault, why: str, round_n: int | None) -> None:
    """Its check couldn't be read at `done`, so it stands."""
    one.read = {
        "where": "done",
        "round": round_n,
        "ask": None,
        "passed": False,
        "verdict": "",
        "why": why,
    }


def _reading_said(one: Fault) -> str:
    read = one.read
    if not one.command:
        return "its rule names no bench check, so a person reads it"
    if read is None:
        return f"`{one.command}` isn't read yet: `done` reads it"
    where = f"`done` in round {read['round']}" if read["where"] == "done" else f"ask {read['ask']}"
    if read["passed"] and read["where"] == "done":
        return f"`{one.command}` passed at {where}: it's fixed"
    if read["passed"]:
        return f"`{one.command}` passes on the files as of {where}; `done` reads it again"
    return f"`{one.command}` still fails, at {where}: {read['why']}"


def at_done(found: list[Fault]) -> list[str]:
    """What `done`'s reply says of each fault it read."""
    lines = [f"{HEADING}, read again whichever way each was fixed:"]
    for one in found:
        mark = "fixed" if one.passed else "STILL STANDS"
        lines.append(f"- {sentence(one)} {mark}: {_reading_said(one)}.")
    if any(not one.passed for one in found):
        lines.append(
            "A fault stands until its check passes. Any fix that meets every rule will do: it "
            "needn't be the way that was suggested."
        )
    return lines


def lines(found: list[Fault]) -> list[str]:
    """Each fault as a stop shows it: its words, its reading, and the way the model took."""
    out = []
    for one in found:
        way = (
            f"The model says it took this way: {one.way.rstrip('. ')}."
            if one.way
            else "The model hasn't said which way it took."
        )
        was = " A way was suggested with it, which the model was free to leave." * one.suggested
        out.append(f"- {sentence(one)} Now: {_reading_said(one)}. {way}{was}")
    return out


def at_stop(found: list[Fault], pick: bool = True) -> list[str]:
    """The section a question or a stop for approval gains, where there are faults to show. At
    a question, `pick`, the answer may name faults again."""
    if not found:
        return []
    again = (
        " A `fix_first` in this pick takes the place of these: name again whatever still isn't "
        "right."
        if pick
        else ""
    )
    return [
        f"## {HEADING}",
        "",
        "Each fault an earlier pick's `fix_first` named, with the way the model says it took. A "
        "fault whose rule names a bench check is read again at `done`, and stands until that "
        f"passes; any other is yours to read here.{again}",
        "",
        *lines(found),
        "",
    ]


def to_show(found: list[Fault]) -> list[Fault]:
    """The faults a stop shows: each that stands, and each settled since the last stop."""
    return [one for one in found if not one.shown]


def shown(found: list[Fault], stop: str) -> None:
    """A stop showed these. One with no check is settled by its way being shown, and one with a
    check once it has passed: neither is shown again."""
    for one in found:
        if one.shown:
            continue
        if (one.command and one.passed) or (not one.command and one.way):
            one.shown = stop


def standing_said(found: list[Fault]) -> str:
    """After a compaction: each fault that stands, in the person's words, with its latest
    reading and the way the model said it took. No suggestion is among them."""
    open_ = [one for one in found if one.stands]
    if not open_:
        return ""
    who = open_[0].by
    out = [f"{HEADING}, still standing ({who}, at ask {open_[0].ask}):"]
    for one in open_:
        line = f"- {sentence(one)}"
        if one.command:
            line += f" Its check: {_reading_said(one)}."
        if one.way:
            line += f" You said you took this way: {one.way.rstrip('. ')}."
        out.append(line)
    out.append("Any fix that meets every rule will do.")
    return "\n".join(out)


def dropped_said(found: list[Fault], who: str) -> str:
    """Said to whoever resumed, and to the model: faults that stood, which a later fix-first
    took the place of."""
    named = ", ".join(f"`{one.id}`" for one in found)
    failing = [one for one in found if one.command and one.read and not one.read["passed"]]
    still = (
        f" ({', '.join(f'`{one.id}`' for one in failing)} failed its check when last read)"
        if failing
        else ""
    )
    return (
        f"This fix-first takes the place of the one before: {named} no longer "
        f"{'stands' if len(found) == 1 else 'stand'}, by {who}{still}."
    )
