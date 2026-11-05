"""A fixed need carries each figure of the non-negotiable it covers, at the request's own figure
and at both ends where the line gives a range or an exact figure (mqucifer/print-shop#52).

On `eval-thick-2` the line was "from 1.5 to 1.8 mm" and the need `thickness ... --max 1.8`, with
no `--min`; on `eval-choice-1` a rim of at least 1.2 mm was checked by an opening's area. Each
was said at the plan ("1.5 isn't in the check of a fixed need that covers it") and settled. Now
`needs` refuses it.

**Which figures.** A figure with its unit: `mm`, `cm`, `%`, degrees, `mm²` or `mm³`. A bare number
is a count ("2 screw holes"), which no limit carries, so it's still only said at the plan.

**Which ends.** The line's words decide, and only these:
- `A to B`, `A–B` or `between A and B`, each with its unit, is a range: A is its low end and B
  its high end;
- "at least", "no less than", "minimum", "more than" before a figure, or "or more" after it, is
  its low end alone; "at most", "no more than", "maximum", "up to", "less than" before it, or
  "or less" after it, is its high end alone;
- any other figure is exact, and is both ends.

**What carries an end.** Of the fixed bench needs that cover the line, together: a `number`'s
`at_least` (the low end) and `at_most` (the high), an option `--min` or `--min-...` (the low), an
option `--max` or `--max-...` (the high), and any other option, a setting the check reads the
part at, such as `--full-rim 1.2`, which carries both. Each at the figure itself. An exact figure
carried at one end by an option whose command has no option for the other end
(`reports.BOTH_ENDS`), such as `capture --min-visible`, is carried as far as a check can.

Where the Sponsor changed the figure by a note, the fixed need quotes the note, and its figures are
the note's. A line covered by an eye need alone isn't held to this: that need says in `no_bench`
why no bench command measures it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from print_shop.rounds import nudge, reports
from print_shop.rounds.needs import Need
from print_shop.rounds.request import Form, Line

UNIT = r"(?:mm²|mm2|mm³|mm3|mm|cm|%|°|deg\b|degrees?\b|percent\b)"
NUMBER = r"\d+(?:\.\d+)?"
RANGE = re.compile(
    rf"(?P<between>between\s+)?(?P<low>{NUMBER})\s*(?:{UNIT})?\s*(?P<join>to|–|—|-|and)\s*"
    rf"(?P<high>{NUMBER})\s*(?P<unit>{UNIT})",
    re.IGNORECASE,
)
ONE = re.compile(rf"(?<![\d.])(?P<n>{NUMBER})\s*(?P<unit>{UNIT})", re.IGNORECASE)
LOW_BEFORE = re.compile(
    r"\b(?:at least|no less than|not less than|a minimum of|minimum|min\.?|more than)\s*$"
    r"|≥\s*$|>=\s*$",
    re.IGNORECASE,
)
HIGH_BEFORE = re.compile(
    r"\b(?:at most|no more than|not more than|a maximum of|maximum|max\.?|up to|less than)\s*$"
    r"|≤\s*$|<=\s*$",
    re.IGNORECASE,
)
LOW_AFTER = re.compile(
    r"^\s*(?:[a-z]+\s+)?(?:or more|or over|or above|minimum|min)\b", re.IGNORECASE
)
HIGH_AFTER = re.compile(
    r"^\s*(?:[a-z]+\s+)?(?:or less|or under|or below|maximum|max)\b", re.IGNORECASE
)
#: What a clause starts after: the words before a figure are read back to here.
CLAUSE = re.compile(r"[,;:.()](?!\d)")

LOW, HIGH, BOTH = "low", "high", "both"
SHOWN = {LOW: "its low end", HIGH: "its high end", BOTH: "an exact figure, so both ends"}


@dataclass(frozen=True)
class Figure:
    value: float
    unit: str
    end: str
    #: As the line writes it.
    text: str
    #: Its range's other figure, where it's one end of one.
    of_range: str = ""


def _same(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def _plain_unit(unit: str) -> str:
    unit = unit.lower()
    return {"mm2": "mm²", "mm3": "mm³", "deg": "°", "degree": "°", "degrees": "°"}.get(
        unit, "%" if unit == "percent" else unit
    )


def read(text: str) -> list[Figure]:
    """Each figure of a line with its unit, and which of its ends a check carries."""
    found: list[Figure] = []
    taken: list[tuple[int, int]] = []
    for match in RANGE.finditer(text):
        low, high = float(match["low"]), float(match["high"])
        if match["join"].lower() == "and" and not match["between"]:
            continue  # "1.2 mm and 0.55 mm" is two figures, not a range
        if not low < high:
            continue
        unit = _plain_unit(match["unit"])
        shown = f"{match['low']} to {match['high']} {unit}"
        found.append(Figure(low, unit, LOW, f"{match['low']} {unit}", shown))
        found.append(Figure(high, unit, HIGH, f"{match['high']} {unit}", shown))
        taken.append(match.span())
    last_end = 0
    for match in ONE.finditer(text):
        start, end = match.span()
        if any(a <= start < b for a, b in taken):
            last_end = end
            continue
        before = text[last_end:start]
        clause = CLAUSE.split(before)[-1] if CLAUSE.search(before) else before
        after = text[end : end + 30]
        unit = _plain_unit(match["unit"])
        if LOW_BEFORE.search(clause) or LOW_AFTER.match(after):
            side = LOW
        elif HIGH_BEFORE.search(clause) or HIGH_AFTER.match(after):
            side = HIGH
        else:
            side = BOTH
        found.append(Figure(float(match["n"]), unit, side, f"{match['n']} {unit}"))
        last_end = end
    return found


@dataclass(frozen=True)
class Carried:
    value: float
    end: str
    #: Where it's carried: `--min 1.5`, or `number` `at_least 1.5`.
    how: str
    command: str
    #: The option or bound that would carry the other end, if its check has one.
    other: str | None


def carried(need: Need) -> list[Carried]:
    """Each figure a bench need's check carries, and at which end."""
    if need.by != "bench" or not need.command:
        return []
    words = need.argv()
    command = words[0] if words else ""
    found: list[Carried] = []
    for name, values in nudge.options(words).items():
        for value in values:
            try:
                number = float(value)
            except ValueError:
                continue
            if not math.isfinite(number):
                continue
            how = f"`{name} {value}`"
            if name == "--min" or name.startswith("--min-"):
                found.append(Carried(number, LOW, how, command, reports.other_end(command, name)))
            elif name == "--max" or name.startswith("--max-"):
                found.append(Carried(number, HIGH, how, command, reports.other_end(command, name)))
            else:
                found.append(Carried(number, BOTH, how, command, None))
    if need.number:
        if need.at_least is not None:
            how = f"`{need.number}` at least {need.at_least:g}"
            found.append(Carried(need.at_least, LOW, how, command, "at_most"))
        if need.at_most is not None:
            how = f"`{need.number}` at most {need.at_most:g}"
            found.append(Carried(need.at_most, HIGH, how, command, "at_least"))
    return found


def _missing(figure: Figure, by: list[Carried]) -> str | None:
    """Which end of the figure no check carries, said as what would carry it, or None."""
    here = [c for c in by if _same(c.value, figure.value)]
    if any(c.end == BOTH for c in here):
        return None
    lows = [c for c in here if c.end == LOW]
    highs = [c for c in here if c.end == HIGH]
    if figure.end == LOW:
        return None if lows else "as a lower limit"
    if figure.end == HIGH:
        return None if highs else "as an upper limit"
    if lows and highs:
        return None
    one = lows or highs
    if not one:
        return "at all"
    if all(c.other is None for c in one):
        return None  # its command can't carry the other end
    other = "an upper" if lows else "a lower"
    ways = sorted({c.other for c in one if c.other})
    return f"as {other} limit too ({', '.join(f'`{w}`' for w in ways)})"


def wrong(found: list[Need], form: Form) -> list[str]:
    """Each figure of a non-negotiable that its fixed bench needs don't carry, at each end its
    line gives."""
    said: list[str] = []
    for line in form.non_negotiables:
        mine = [n for n in found if n.fixed and n.by == "bench" and line.id in n.covers]
        if not mine:
            continue
        by = [c for need in mine for c in carried(need)]
        # A figure the Sponsor changed by a note is the note's: its need quotes it (`request.py`).
        noted = [n.quote for n in mine if n.source == "note"]
        for figure in read(" ".join(noted) if noted else line.text):
            missing = _missing(figure, by)
            if missing is None:
                continue
            said.append(_said(line, figure, mine, by, missing))
    return said


def _said(line: Line, figure: Figure, mine: list[Need], by: list[Carried], missing: str) -> str:
    named = ", ".join(f"{n.id!r}" for n in mine)
    whose = f"the need {named} covers" if len(mine) == 1 else f"the needs {named} cover"
    what = (
        f"{figure.text}, the {'low' if figure.end == LOW else 'high'} end of {figure.of_range}"
        if figure.of_range
        else f"{figure.text}, {SHOWN[figure.end]}"
    )
    has = "; ".join(sorted({c.how for c in by})) or "no figure"
    return (
        f'{whose} `{line.id}`, "{line.text}", and its {what}, isn\'t carried {missing}. '
        f"Their checks carry {has}. A non-negotiable is checked at the request's own figure, at "
        "both ends where the line gives a range or an exact figure: as the check's own limit, "
        "such as `--min` and `--max`, or a `number` with `at_least` and `at_most`. A check by "
        "another measure, such as an area for a rim, doesn't carry it. If the line can't be "
        "read that way, ask with `questions`"
    )
