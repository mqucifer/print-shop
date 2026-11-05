"""What the part must do, each need saying how it's checked, and the check against a reading.

Intake's result. A need is checked by a named bench command and its limit, or by the Sponsor's
eye. The harness runs these itself at `done`, so the model's word and a tool's success don't
count. Like `cad_eval`'s `plan.py`, a need says where it came from, and a quote from the Sponsor is
held to their words, with the miss quoting the model's own entry. Unlike it, nothing gates work on
this: writing and running are open throughout intake.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
import shlex
from dataclasses import asdict, dataclass, field
from typing import Any

from print_shop.rounds import reports, toolsets
from print_shop.rounds.toolsets import Toolset

#: `note` is a person's note relayed to the model, so a Sponsor's words aren't marked `derived`.
SOURCES = ("request", "answer", "note", "pick", "reference", "derived", "chosen")
#: Sources quoted in a person's own words: a pick's reason is one (mqucifer/print-shop#54), as on
#: `eval-unnamed-1` a need quoting the stand-in's pick was refused and cost a round of questions.
QUOTED = ("request", "answer", "note", "pick")
COMMANDS = (
    "info",
    "clearance",
    "readback",
    "rays",
    "panes",
    "capture",
    "thin",
    "pause",
    "wall",
    "params",
    "render",
    "light",
    "thickness",
)
#: Verdicts that are no answer: they never pass a need, whatever it says.
UNSURE = ("DISAGREE", "NO ANSWER")
#: The bench saying that what it checks is wrong. On `cup-1` a need passed on `capture`'s FAIL:
#: a need can't ask for the check to fail.
WRONG = ("FAIL",)
GOOD = ("OK", "CLEAR", "TOUCHING")
GEOMETRY = (".step", ".stp", ".stl", ".brep")
#: A need's keys that tie it to the request's lines (`request.py`).
MARKS = ("covers", "fixed", "figure")


def normal(text: str) -> str:
    """As `cad_eval`'s `plan.loose`: a quote isn't judged on markup, layout or how a unit reads."""
    text = re.sub(r"[*`_]", "", text).replace("−", "-").replace("×", "x")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    text = re.sub(r"(\d)\s*mm\b", r"\1 mm", text)
    text = re.sub(r"(\d)\s*(?:°|degrees?\b|deg\b)", r"\1 deg", text)
    return re.sub(r"(\d(?:\s*mm)?)\s*(?:x|by)\s*(?=-?\.?\d)", r"\1 x ", text)


@dataclass
class Need:
    id: str
    what: str
    source: str
    by: str  # bench or eye
    quote: str = ""
    # A bench need's.
    command: str = ""
    verdicts: list[str] = field(default_factory=list)
    number: str = ""
    at_least: float | None = None
    at_most: float | None = None
    # An eye need's, and why no bench command measures a line of the request it covers.
    look_for: str = ""
    no_bench: str = ""
    # The request's lines it covers (`request.py`), whether it's a non-negotiable's, and how a
    # non-negotiable's figure is read, such as a lip before or after play.
    covers: list[str] = field(default_factory=list)
    fixed: bool = False
    figure: str = ""
    # What a bench need is about, from its toolset's short list (`toolsets.py`).
    measures: str = ""

    def argv(self) -> list[str]:
        return shlex.split(self.command)

    def files(self) -> list[str]:
        """The geometry a bench need's command names, which a clean run has to make or be given."""
        return [w for w in self.argv() if not w.startswith("-") and w.lower().endswith(GEOMETRY)]

    def plain(self) -> dict[str, Any]:
        """What it says, without what it leaves out: a need that isn't fixed doesn't say so."""
        found = {k: v for k, v in asdict(self).items() if v not in ("", [], None)}
        if not self.fixed:
            found.pop("fixed", None)
        return found

    def limit(self) -> str:
        """The limit as a reading's line says it."""
        wanted = f"verdict {' or '.join(self.verdicts)}" if self.verdicts else "exit 0"
        if self.number:
            low = f" at least {self.at_least}" if self.at_least is not None else ""
            high = f" at most {self.at_most}" if self.at_most is not None else ""
            return f"{wanted}, `{self.number}`{low}{high}"
        return wanted


def _bound(entry: dict[str, Any], key: str, wrong: list[str], shown: str) -> float | None:
    value = entry.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            value = float(value)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        wrong.append(f"{shown}: `{key}` is one number")
        return None
    return float(value)


def _unquoted(source: str, quote: str, theirs: str, relayed: str, picked: str) -> str:
    """Why a quote isn't from its source, naming the source it is from, or nothing if it is."""
    where = {
        "note": (relayed, "which isn't in a note you were sent"),
        "pick": (picked, "which isn't in a pick's reason you were sent"),
    }
    text, said = where.get(source, (theirs, "which isn't in the request or the answers"))
    if quote in text:
        return ""
    others = (
        ("note", relayed, "a note", "`note`"),
        ("pick", picked, "a pick's reason", "`pick`"),
        ("answer", theirs, "the request or an answer", "`request` or `answer`"),
    )
    for other, found, kind, named in others:
        if other != source and not (other == "answer" and source == "request") and quote in found:
            return f"{said}, but is in {kind}: its `source` is {named}"
    return said


def read(
    raw: Any,
    said_by_sponsor: str,
    notes: str = "",
    toolset: Toolset | None = None,
    picks: str = "",
) -> tuple[list[Need], list[str]]:
    """The needs, or what's wrong with them. `said_by_sponsor` is the request and the answers,
    `notes` what was relayed, and `picks` each pick's reason and fix-first: each is its own source,
    so a need says which it came from. With a `toolset`, each bench need's check is in it and
    measures what the need says it's about."""
    wrong: list[str] = []
    try:
        entries = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as exc:
        return [], [f"`needs` isn't JSON: {exc}"]
    if not isinstance(entries, list) or not entries:
        return [], ["`needs` is a list with at least one need"]
    found: list[Need] = []
    seen: set[str] = set()
    theirs = normal(said_by_sponsor)
    relayed = normal(notes)
    picked = normal(picks)
    for entry in entries:
        if not isinstance(entry, dict):
            wrong.append("each need is an object")
            continue
        shown = f"the need {str(entry.get('id'))!r}"
        unknown = set(entry) - {"id", "what", "source", "quote", "check", *MARKS}
        if unknown:
            wrong.append(
                f"{shown}: it has only id, what, source, quote, check, covers, fixed and figure: "
                f"not {unknown}"
            )
        need_id = entry.get("id")
        if not isinstance(need_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", need_id):
            wrong.append(f"{shown}: `id` is a short name, of letters, digits, `.`, `_` or `-`")
            need_id = str(need_id)
        elif need_id in seen:
            wrong.append(f"{shown}: two needs have this id")
        seen.add(need_id)
        what = str(entry.get("what") or "").strip()
        if not what:
            wrong.append(f"{shown}: `what` says what it must do")
        source = entry.get("source")
        if source not in SOURCES:
            wrong.append(f"{shown}: `source` is one of {', '.join(SOURCES)}")
        quote = str(entry.get("quote") or "")
        if source in QUOTED:
            if not quote.strip():
                wrong.append(f"{shown} comes from the {source}: give its `quote`, their own words")
            else:
                said = _unquoted(str(source), normal(quote), theirs, relayed, picked)
                if said:
                    wrong.append(f'{shown} quotes "{quote}", {said}')
        check = entry.get("check")
        if not isinstance(check, dict):
            wrong.append(f'{shown}: `check` is {{"by": "bench", ...}} or {{"by": "eye", ...}}')
            continue
        need = Need(need_id, what, str(source), str(check.get("by")), quote)
        _marks(need, entry, wrong, shown)
        if check.get("by") == "eye":
            need.look_for = str(check.get("look_for") or "").strip()
            need.no_bench = str(check.get("no_bench") or "").strip()
            if not need.look_for:
                wrong.append(f"{shown}: `look_for` says what the Sponsor's eye is judging")
        elif check.get("by") == "bench":
            before = len(wrong)
            _bench_check(need, check, wrong, shown)
            if toolset is not None and len(wrong) == before:
                wrong += toolsets.wrong_with(need, toolset)
        else:
            wrong.append(f"{shown}: `check.by` is bench or eye")
        found.append(need)
    return ([], wrong) if wrong else (found, [])


def _marks(need: Need, entry: dict[str, Any], wrong: list[str], shown: str) -> None:
    """What ties a need to the request's lines. Whether they're the right lines is `request.held`'s
    to say, against the request."""
    covers = entry.get("covers") or []
    if isinstance(covers, str):
        covers = [covers]
    if not isinstance(covers, list) or not all(isinstance(c, str) for c in covers):
        wrong.append(
            f'{shown}: `covers` names the request\'s lines it covers, such as ["R1", "N2"]'
        )
        covers = []
    need.covers = list(dict.fromkeys(c.strip() for c in covers))
    fixed = entry.get("fixed", False)
    if isinstance(fixed, str) and fixed.strip().lower() in ("true", "false"):
        fixed = fixed.strip().lower() == "true"
    if not isinstance(fixed, bool):
        wrong.append(f"{shown}: `fixed` is true or false")
        fixed = False
    need.fixed = fixed
    figure = entry.get("figure") or ""
    if not isinstance(figure, str):
        wrong.append(f"{shown}: `figure` says in words how its line's figure is read")
        figure = ""
    need.figure = figure.strip()


def _bench_check(need: Need, check: dict[str, Any], wrong: list[str], shown: str) -> None:
    need.command = str(check.get("command") or "").strip().removeprefix("bench ").strip()
    need.measures = str(check.get("measures") or "").strip()
    try:
        words = need.argv()
    except ValueError as exc:
        wrong.append(f"{shown}: `command` can't be read: {exc}")
        return
    if not words or words[0] not in COMMANDS:
        wrong.append(f"{shown}: `command` is a bench command: {', '.join(COMMANDS)}")
    verdicts = check.get("verdicts") or []
    if isinstance(verdicts, str):
        verdicts = [verdicts]
    need.verdicts = [str(v) for v in verdicts]
    for verdict in need.verdicts:
        if verdict in UNSURE:
            wrong.append(f"{shown}: {verdict} is no answer, so it can't pass a need")
        elif verdict in WRONG:
            wrong.append(
                f"{shown}: {verdict} is the bench saying what `{need.command.split(' ')[0]}` "
                "checks is wrong, so it can't pass a need. If the check reads the part wrongly, "
                "give one that reads it, or say so in `blocked`"
            )
    need.number = str(check.get("number") or "")
    need.at_least = _bound(check, "at_least", wrong, shown)
    need.at_most = _bound(check, "at_most", wrong, shown)
    if need.number and need.at_least is None and need.at_most is None:
        wrong.append(f"{shown}: `number` needs its limit: `at_least`, `at_most` or both")
    if not need.number and (need.at_least is not None or need.at_most is not None):
        wrong.append(f"{shown}: a limit needs the `number` it holds")


def numbers_wrong(found: list[Need]) -> list[str]:
    """Each bench need whose `number` its command never reports (`reports.py`). On `eval-rings-1`
    `wall` was settled with `min_wall_mm`, where it reports `least_wall_mm`. A `try` isn't held to
    this: its reading says which numbers the answer has."""
    wrong = []
    for need in found:
        if need.by != "bench" or not need.number:
            continue
        words = need.argv()
        said = reports.wrong_number(words[0], need.number) if words else None
        if said:
            wrong.append(f"the need {need.id!r}: {said}")
    return wrong


def verdicts_wrong(found: list[Need]) -> list[str]:
    """Each bench need asking for a verdict its command never gives (`reports.py`). On
    `eval-through-1` a fit need on `clearance` asked for OK, where `clearance` says CLEAR: three
    `done`s failed on it before the model asked."""
    wrong = []
    for need in found:
        if need.by != "bench" or not need.verdicts:
            continue
        words = need.argv()
        for verdict in need.verdicts:
            if verdict in UNSURE or verdict in WRONG:
                continue  # `read` refuses these already
            said = reports.wrong_verdict(words[0], verdict) if words else None
            if said:
                wrong.append(f"the need {need.id!r}: {said}")
    return wrong


def load(entries: list[dict[str, Any]]) -> list[Need]:
    """Needs as kept, a need from before `covers` and `fixed` with neither."""
    return [Need(**entry) for entry in entries]


def dump(needs: list[Need]) -> list[dict[str, Any]]:
    return [asdict(n) for n in needs]


def number_at(numbers: Any, path: str) -> Any:
    """`pairs.0.gap_mesh_mm` in a report's numbers. Raises LookupError where there's none."""
    here = numbers
    for part in path.split("."):
        if isinstance(here, dict) and part in here:
            here = here[part]
        elif isinstance(here, list) and part.isdigit() and int(part) < len(here):
            here = here[int(part)]
        else:
            raise LookupError(path)
    return here


@dataclass
class Reading:
    """One need's check, as the harness ran it."""

    need: str
    by: str
    passed: bool
    why: str
    exit: int | None = None
    verdict: str = ""
    value: float | None = None
    text: str = ""
    record: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0


def judge(need: Need, code: int, text: str, record: dict[str, Any] | None) -> Reading:
    """One bench answer against one need. Only the bench's verdict and numbers count."""
    match = re.match(r"\s*verdict: ([A-Z ]+?):", text)
    verdict = str((record or {}).get("verdict") or (match.group(1) if match else ""))
    reading = Reading(need.id, "bench", False, "", code, verdict, None, text, record or {})
    if code == 3:
        reading.why = f"the check's own command is wrong: {text.strip()[:300]}"
        return reading
    if not verdict:
        reading.why = f"no verdict came back (exit {code})"
        return reading
    if verdict in UNSURE:
        reading.why = f"{verdict}: there's no answer to rely on"
        return reading
    if need.verdicts:
        if verdict not in need.verdicts:
            reading.why = f"{verdict}, and it must be {' or '.join(need.verdicts)}"
            return reading
    elif code != 0 or verdict not in GOOD:
        reading.why = f"{verdict}: {str((record or {}).get('why') or '')[:300]}"
        return reading
    if need.number:
        return _judge_number(need, reading, record or {})
    reading.passed = True
    reading.why = verdict
    return reading


def _judge_number(need: Need, reading: Reading, record: dict[str, Any]) -> Reading:
    numbers = record.get("numbers") or {}
    try:
        raw = number_at(numbers, need.number)
    except LookupError:
        names = ", ".join(sorted(numbers)) or "none"
        reading.why = f"this answer has no number `{need.number}`: it has {names}"
        return reading
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        reading.why = f"`{need.number}` is {raw!r}, not a number"
        return reading
    value = float(raw)
    reading.value = value
    if need.at_least is not None and value < need.at_least:
        reading.why = f"`{need.number}` is {value:g}, under {need.at_least:g}"
    elif need.at_most is not None and value > need.at_most:
        reading.why = f"`{need.number}` is {value:g}, over {need.at_most:g}"
    else:
        reading.passed = True
        reading.why = f"{reading.verdict}, `{need.number}` is {value:g}"
    return reading
