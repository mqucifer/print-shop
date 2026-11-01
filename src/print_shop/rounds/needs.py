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

SOURCES = ("request", "answer", "reference", "derived", "chosen")
QUOTED = ("request", "answer")
COMMANDS = ("info", "clearance", "readback", "rays", "panes", "thin", "pause", "params", "render")
#: Verdicts that are no answer: they never pass a need, whatever it says.
UNSURE = ("DISAGREE", "NO ANSWER")
GOOD = ("OK", "CLEAR", "TOUCHING")
GEOMETRY = (".step", ".stp", ".stl", ".brep")


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
    # An eye need's.
    look_for: str = ""

    def argv(self) -> list[str]:
        return shlex.split(self.command)

    def files(self) -> list[str]:
        """The geometry a bench need's command names, which a clean run has to make or be given."""
        return [w for w in self.argv() if not w.startswith("-") and w.lower().endswith(GEOMETRY)]

    def plain(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", [], None)}

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


def read(raw: Any, said_by_sponsor: str) -> tuple[list[Need], list[str]]:
    """The needs, or what's wrong with them. `said_by_sponsor` is the request and the answers."""
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
    for entry in entries:
        if not isinstance(entry, dict):
            wrong.append("each need is an object")
            continue
        shown = f"the need {str(entry.get('id'))!r}"
        unknown = set(entry) - {"id", "what", "source", "quote", "check"}
        if unknown:
            wrong.append(f"{shown}: it has only id, what, source, quote, check: not {unknown}")
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
            elif normal(quote) not in theirs:
                wrong.append(
                    f'{shown} quotes "{quote}", which isn\'t in the request or the answers'
                )
        check = entry.get("check")
        if not isinstance(check, dict):
            wrong.append(f'{shown}: `check` is {{"by": "bench", ...}} or {{"by": "eye", ...}}')
            continue
        need = Need(need_id, what, str(source), str(check.get("by")), quote)
        if check.get("by") == "eye":
            need.look_for = str(check.get("look_for") or "").strip()
            if not need.look_for:
                wrong.append(f"{shown}: `look_for` says what the Sponsor's eye is judging")
        elif check.get("by") == "bench":
            _bench_check(need, check, wrong, shown)
        else:
            wrong.append(f"{shown}: `check.by` is bench or eye")
        found.append(need)
    return ([], wrong) if wrong else (found, [])


def _bench_check(need: Need, check: dict[str, Any], wrong: list[str], shown: str) -> None:
    need.command = str(check.get("command") or "").strip().removeprefix("bench ").strip()
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
    need.number = str(check.get("number") or "")
    need.at_least = _bound(check, "at_least", wrong, shown)
    need.at_most = _bound(check, "at_most", wrong, shown)
    if need.number and need.at_least is None and need.at_most is None:
        wrong.append(f"{shown}: `number` needs its limit: `at_least`, `at_most` or both")
    if not need.number and (need.at_least is not None or need.at_most is not None):
        wrong.append(f"{shown}: a limit needs the `number` it holds")


def load(entries: list[dict[str, Any]]) -> list[Need]:
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
