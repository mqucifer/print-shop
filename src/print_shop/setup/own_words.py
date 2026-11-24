"""An answer in the person's own words, read as the settings it decides (mqucifer/print-shop#213).

At the questions, "o no support" used to set nothing, so the guard took the overhang as
undecided and turned tree supports on, "nothing decided otherwise". mqucifer/print-shop#124 made
a chosen option a decision; this makes words one too, once they're read back and confirmed.

The reading needs no model. A few plain phrasings, each of one setting, are read: supports on or
off, a named brim, a wall count and an infill percentage. Only a setting the question's options
set is read, as that's what the question decides; words about another say so and set nothing.
Words that say two things of one setting, or nothing this reads, set nothing either, and the
reading says why. Nothing is guessed: a reading is only a proposal, which the asker reads back
("So no supports?") and the person confirms or corrects (`intake.cli_asker`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from print_shop.setup.bounds import check

__all__ = ["NAMES", "Reading", "either", "read"]

#: Words that say the part prints without supports: what `questions.says_no_support` reads in an
#: option's label ("no support", "without supports", "unsupported", "support-free"), and what a
#: person also types: "supports off", "don't use supports", "skip supports". Not "no support
#: marks", which is about the finish.
_NO_SUPPORT = re.compile(
    r"\b(?:no|without)\s+supports?\b(?!\s*(?:marks?|scars?|contact|interface|removal))"
    r"|\bunsupported\b|\bsupport[- ]free\b|\bsupports?\s+off\b(?!\s+the)"
    r"|\b(?:don'?t|do\s+not|never)\s+(?:use\s+|add\s+|need\s+|want\s+)?(?:any\s+)?supports?\b"
    r"|\bskip\s+(?:the\s+)?supports?\b",
    re.IGNORECASE,
)

#: Words that say it prints with supports: "supports on", "with supports", "use tree supports",
#: "support it". Read after what says no support is taken out, so "don't use supports" isn't both.
_SUPPORT = re.compile(
    r"\bsupports?\s+on\b|\bsupport\s+it\b"
    r"|\b(?:with|use|add|needs?|wants?)\s+(?:some\s+|tree\s+)?supports?\b"
    r"|\btree\s+supports?\b",
    re.IGNORECASE,
)

_BRIMS = (
    (re.compile(r"\b(?:no|without)\s+(?:a\s+)?brim\b|\bbrim\s+off\b", re.IGNORECASE), "no_brim"),
    (re.compile(r"\bbrim\s+ears\b|\bears\b", re.IGNORECASE), "brim_ears"),
    (re.compile(r"\bouter(?:\s+only)?\s+brim\b|\bbrim\s+outside\s+only\b", re.IGNORECASE),
     "outer_only"),
    (re.compile(r"\bauto(?:matic)?\s+brim\b", re.IGNORECASE), "auto_brim"),
)
_ANY_BRIM = re.compile(r"\bbrim\b", re.IGNORECASE)

_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
           "eight": 8, "nine": 9, "ten": 10}
_WALLS = re.compile(
    r"\b(\d+|" + "|".join(_COUNTS) + r")\s+(?:walls?|perimeters?|wall\s+loops?)\b",
    re.IGNORECASE,
)
_INFILL = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*%\s*(?:[a-z]+\s+)?infill\b|\binfill\s+(?:of\s+|at\s+)?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)

#: Each setting read, in a person's words, as "says nothing of" names it.
NAMES = {"enable_support": "supports", "brim_type": "a brim", "wall_loops": "walls",
         "sparse_infill_density": "infill"}


@dataclass(frozen=True)
class Reading:
    """What some words decide: the settings they'd set, and why any part of them sets nothing."""

    sets: dict[str, str] = field(default_factory=dict)
    #: Each reason a part of the words sets nothing, said to the person.
    nothing: tuple[str, ...] = ()


def read(words: str, settings: Iterable[str]) -> Reading:
    """Words read as what they'd set among `settings`, the keys a question's options set."""
    settings_order = list(settings)
    deciding = set(settings_order)
    found: dict[str, set[str]] = {}
    for key, value in _readings(words):
        found.setdefault(key, set()).add(value)
    sets: dict[str, str] = {}
    nothing: list[str] = []
    for key, values in found.items():
        name = NAMES.get(key, key)
        if key not in deciding:
            nothing.append(f"this question doesn't decide {name}, so what they say of it "
                           "sets nothing here; the review can")
        elif len(values) > 1:
            nothing.append(f"they say {name} two ways, so they set neither")
        elif why := check(key, next(iter(values)), {}):
            nothing.append(f"what they say of {name} can't be set: {why}")
        else:
            sets[key] = next(iter(values))
    if not found:
        asked = list(dict.fromkeys(NAMES.get(k, k) for k in settings_order))
        nothing.append("none of them says " + either(asked) if asked else
                       "this question's options set nothing")
    return Reading(sets, tuple(nothing))


def _readings(words: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    rest = _NO_SUPPORT.sub(" ", words)
    if rest != words:
        found.append(("enable_support", "0"))
    if _SUPPORT.search(rest):
        found.append(("enable_support", "1"))
    brims = [value for pattern, value in _BRIMS if pattern.search(words)]
    if brims:
        found += [("brim_type", value) for value in brims]
    elif _ANY_BRIM.search(words):
        found.append(("brim_type", "which brim"))  # which one isn't said: refused by `check`
    for count in _WALLS.findall(words):
        found.append(("wall_loops", str(_COUNTS.get(count.lower(), count))))
    for before, after in _INFILL.findall(words):
        found.append(("sparse_infill_density", f"{float(before or after):g}%"))
    return found


def either(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} or {names[-1]}"
