"""A hold is read by the bench on the slide, never by eye, and a held part is built and read before
its plan (mqucifer/print-shop#86, mqucifer/print-shop#87), a part of the model's held by another
as a bought part is (mqucifer/print-shop#90).

From the hold test on 2026-11-09 (`~/print-shop/skill-evals/hold-test-2026-11-09/RESULTS.md`).
`clearance --slide` (mqucifer/print-shop#76) read every plan right, and nothing made the model read
it: each fan run made the hold an eye need, "no bench command measures it", and `eval-fan-3`
never ran the slide. Both fan-in-a-pocket runs spent over an hour at intake working out a clip in
the head before anything was built, and the bench can read a hold only on a part. The Sponsor,
2026-11-09: a hold in words is "confusing", so the bench reads it, never a picture.

**Which bought parts are held.** A file under `references/` that the model's part holds:
- its fact (mqucifer/print-shop#78) is a fitted bought part's, with `way_in` and `held_by`; or
- the latest breakdown joins it to a part, by its path, its file name or its stem as a part's name,
  and no fact says it's `fixed`.
A fact with `fixed` is a part already in place and never fitted, such as a box a lid sits on, and
one with `loose` a part fitted loose by design (mqucifer/print-shop#91): no part holds either. A
held part's way out is the opposite of its fact's way in, or unknown without one.

**How a breakdown's name is matched to a given file** (mqucifer/print-shop#97). The batch 9 gate
named given parts in words, "plug body" for `references/plug_body.step`, "box (reference)" for
`references/base.step` and "pane (given)" for `references/pane_1.step`, and each was taken for a
printed part, so the plan waited for a file that couldn't exist. A stem's underscores and hyphens
read as spaces, and a trailing digit (`_1`) may be left off where that names one file alone. Where
one given file is left unmatched by every name in the breakdown, a name still unmatched that says
it's given, "(reference)", "(given)" or "(bought)" in brackets or at its end, is that file. A name
still unmatched is a printed part. A `fixed` or `loose` fact ends the wait wherever the breakdown
names its part, on either branch.

**Which of the model's own parts are held** (mqucifer/print-shop#90). On a design run a lid's snap
onto a base was settled by eye, and the slide, run once, read the lid overlapping the base where it
lay. A part is held where the latest breakdown joins it to one other part of the model's, neither
under `references/`, and the join's `how` or a need's words name one of them and say it's held.
The held part is the one the words name first, unless a verb such as "holds" follows it: "the lid
stays on the base" holds the lid, and "the base holds the lid" too. Words naming one alone hold
that one, "the lid stays on", and name no bought part, so a bracket that "holds a shelf" holds
nothing of its own. The slide reads either way round, so a reading of the two in either order
counts, and every refusal says the held part goes last, to swap them if it's the other way. No
fact gives a printed part's way off, so each refusal says how to name it. A refusal naming a part
with no file names the given files, to name it by one if it's given, not to build it.

**A negated hold word isn't a hold** (mqucifer/print-shop#97). "read NOT HELD ... a passage, not a
grip" said nothing was held, and read as a hold. A hold word with "not", "no", "nothing", "never"
or a "-n't" up to two words before it, in the same clause, says no hold; words that say a part
comes off, such as "falls out", still say a hold when negated: "never falls out".

**Two rules, each said with what to do next:**
- `needs` refuses an eye need on a held part's hold: its words, or a request line it covers, name
  the part and say it's held. The refusal gives the bench need to send instead, its `clearance
  <part> <bought part> --slide <way out>` from the needs' own files, with `HELD`.
- At the plan stop, `ask` waits for each held part's first try and its reading: a part file of the
  model's, and a `clearance ... --slide <way out>` reading on it as it stands, whatever its
  verdict. The reading goes beside the plan, in `question.md`. Nothing says how to build a clip:
  shapes wait for the Sponsor's lid guidelines and mqucifer/print-shop#9.

**At intake, a hold read `HELD` says what comes next** (mqucifer/print-shop#92). `pocket-3` read its
hold `HELD` at intake, then rounded edges for about 20 calls and never sent `breakdown`. Each
`bench` and `run` reply at intake leads with a line saying the plan needs only the breakdown, the
needs and the first try with its reading, until `needs` is settled.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from print_shop.rounds import facts as facts_module
from print_shop.rounds.needs import GEOMETRY, Need, normal

REFERENCES = "references/"
WAYS = facts_module.WAYS
#: Words that say a part is held, or isn't, as a need or a request line puts it. A clip or a snap
#: is a thing, not a hold: "the clips look neat" judges a look, and "snaps on" says a hold. How
#: firm a pull feels is the eye's: "comes off by a firm pull" says nothing here.
HOLD = re.compile(
    r"\b(?:hold|holds|held|holding|retain(?:s|ed|ing)?|grip(?:s|ped|ping)?|secur(?:e|es|ed|ing)"
    r"|captive|in place|stays? (?:in|on|put|seated|shut)|snaps? (?:on|onto|in|into|over|shut)"
    r"|snapped (?:on|in|shut)|falls? out|comes? (?:out|loose)|loose)\b"
)
#: Hold words that say a part comes off: negated, they still say it's held, "never falls out".
COMES_OFF = re.compile(r"falls? out|comes? (?:out|loose)|loose")
#: Words that say a hold word after them isn't one: "not a grip", "NOT HELD", "nothing holds it".
NEGATION = re.compile(r"not|no|nothing|never|\w+n['’]t")
#: How many words before a hold word a negation reaches, in the same clause.
NEGATED_WITHIN = 3
#: Where a clause ends, for a negation: punctuation, a dash between words, or a joining word.
CLAUSE = re.compile(r"[,;:.!?()]|\s[-–—]+\s|\b(?:and|but|or|so|while|yet)\b")
#: A breakdown's name that says it's a given part: "box (reference)", "pane (given)", "fan bought".
GIVEN_SAID = re.compile(
    r"\(\s*(?:the\s+|a\s+)?(?:references?|given|bought)\b[^()]*\)"
    r"|\b(?:references?|given|bought)\s*$"
)
#: A verb after the first part named that makes it the holder: "the base holds the lid".
HOLDER_VERB = re.compile(r"\b(?:holds|grips|retains|secures|keeps|captures|clamps|locks|traps)\b")
#: Words of a part's name that don't name it.
NOT_A_NAME = frozenset(
    {"the", "and", "its", "part", "parts", "printed", "piece", "with", "for", "your", "one"}
)
#: What `Held.by` says of a part of the model's held by another (mqucifer/print-shop#90).
PRINTED = "printed"
#: How the slide's verdicts a need takes read.
VERDICT = "HELD"
WHY_CHARS = 300
#: What the start adds where something was given, and the plan stop's own words: two lines.
START = (
    "Where a part of yours holds a bought part or another of yours, build a rough first try and "
    "read its hold with `bench clearance <holder> <held part> --slide <its way out>` before the "
    "plan stop, not after: the bench reads a hold in seconds, but only on a part.\nThe first try "
    "doesn't have to hold. "
    "Its reading goes beside the plan, whatever it says, and a hold is a bench need on that "
    "command with `HELD`, never an eye need."
)
#: A `held_by` that says the part is fixed, not held: `eval-lid-3`'s base was given a way in, and a
#: `held_by` saying it was already printed, because the fact asked for both. It isn't taken as
#: `fixed`, which only the fact says, but the plan's refusal says how to say so.
SAYS_FIXED = re.compile(
    r"\bfixed\b|\balready (?:printed|in place|made)\b|can(?:not|'t) (?:change|move)"
)
#: The heading the plan's reading goes under, in `question.md`.
AT_STOP = "## What holds each held part, as the bench read it"
#: What leads each `bench` and `run` reply at intake once a hold reads `HELD`
#: (mqucifer/print-shop#92): one line.
READ_AT_INTAKE = (
    "The hold is read: {parts} {reads} `HELD`. Next come `breakdown`, `needs` and the plan stop: "
    "the plan needs only those and this first try with its reading. Finishing, such as edges and "
    "fillets, is round work after the plan is approved."
)


@dataclass(frozen=True)
class Held:
    """A part a part of the model's holds: a bought part's file under `references/`, or a part of
    the model's by its breakdown name (`by` is `PRINTED`); its way out, empty where no fact gives
    its way in; what said so; and what holds it, in words, or the holder's breakdown name."""

    part: str
    way_out: str
    by: str
    holder: str

    @property
    def printed(self) -> bool:
        return self.by == PRINTED


@dataclass(frozen=True)
class Read:
    """One held part's reading, as the plan's stop shows it. `bought` is the held part, a bought
    part's file or a part of the model's by name: `asked.json` keeps the key."""

    bought: str
    command: str
    at_call: int
    verdict: str
    why: str


def opposite(way: str) -> str:
    way = way.strip()
    if way not in WAYS:
        return ""
    return ("-" if way[0] == "+" else "+") + way[1:]


def _plain(name: str) -> str:
    return name.strip().removeprefix("/work/").removeprefix("./")


def _stem(bought: str) -> str:
    return PurePosixPath(bought).stem.lower()


def _spaced(text: str) -> str:
    """Words in lower case, one space apart: underscores and hyphens read as spaces."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _stems(bought: str, bare: bool) -> list[str]:
    """A file's stem as words, "plug body" of `plug_body.step`, and where `bare`, without a
    trailing digit, "pane" of `pane_1.step`, if what's left is a word of three letters or more."""
    words = re.findall(r"[a-z0-9]+", _stem(bought))
    forms = [" ".join(words)] if words else []
    if bare and len(words) > 1 and words[-1].isdigit() and len(" ".join(words[:-1])) >= 3:
        forms.append(" ".join(words[:-1]))
    return forms


def _names(name: str, bought: str, bare: bool = True) -> bool:
    """Whether a breakdown's part name is the file: its path, its file name, or its stem as words
    of the name, such as "fan" or "the fan (bought)" for `references/fan.step` and "plug body" for
    `references/plug_body.step`; where `bare`, "pane" for `references/pane_1.step` too."""
    said = _plain(name).lower()
    path = PurePosixPath(bought)
    if said in (bought.lower(), path.name.lower()):
        return True
    spaced = _spaced(said)
    return any(re.search(rf"\b{re.escape(f)}\b", spaced) for f in _stems(bought, bare))


def given_names(breakdown: dict[str, Any] | None, references: set[str]) -> dict[str, set[str]]:
    """Each part name the breakdown gives, in its parts or its joins, with the given files it
    names (mqucifer/print-shop#97): by `_names`, a bare stem only where it names one file alone;
    then, where one given file is left unmatched by every name, each name still unmatched that
    says it's given, "box (reference)", is that file. A name not here is a part of the model's."""
    names: list[str] = []
    for part in (breakdown or {}).get("parts") or []:
        if isinstance(part, dict) and part.get("name"):
            names.append(str(part["name"]))
    for join in (breakdown or {}).get("joins") or []:
        if isinstance(join, dict):
            names += [str(p) for p in join.get("parts") or []]
    found: dict[str, set[str]] = {}
    for name in dict.fromkeys(names):
        hits = {r for r in references if _names(name, r, bare=False)}
        if not hits:
            bare = [r for r in references if _names(name, r)]
            hits = set(bare) if len(bare) == 1 else set()
        if hits:
            found[name] = hits
    named = set().union(*found.values()) if found else set()
    left = sorted(set(references) - named)
    if len(left) == 1:
        for name in dict.fromkeys(names):
            if name not in found and GIVEN_SAID.search(_plain(name).lower()):
                found[name] = {left[0]}
    return found


def hold_word(text: str) -> re.Match[str] | None:
    """The first hold word in words already `normal`, or None: one with a negation up to two words
    before it in its clause isn't one, "not a grip", unless it says a part comes off, "never falls
    out", which is a hold either way (mqucifer/print-shop#97)."""
    for word in HOLD.finditer(text):
        clause = CLAUSE.split(text[: word.start()])[-1]
        before = re.findall(r"[\w'’]+", clause)[-NEGATED_WITHIN:]
        negated = any(NEGATION.fullmatch(w) for w in before)
        if not negated or COMES_OFF.fullmatch(word.group(0)):
            return word
    return None


def mentions(text: str, bought: str) -> bool:
    """Whether words name the bought part, by its path or its stem as words."""
    said = normal(text)
    if bought.lower() in said or PurePosixPath(bought).name.lower() in said:
        return True
    if normal(bought) in said or normal(PurePosixPath(bought).name) in said:
        return True
    spaced = _spaced(said)
    return any(re.search(rf"\b{re.escape(f)}s?\b", spaced) for f in _stems(bought, bare=False))


def _name_words(name: str, other: str) -> list[str]:
    """The words of a part's name that name it and not the other part: "lid" of "snap lid"."""
    theirs = set(re.findall(r"[a-z0-9]+", other.lower()))
    return [
        w
        for w in re.findall(r"[a-z0-9]+", _plain(name).lower())
        if len(w) >= 3 and w not in NOT_A_NAME and w not in theirs
    ]


def _at(text: str, name: str, other: str) -> int:
    """Where words first name a part of the model's, by a word of its name, or -1."""
    found = [
        m.start()
        for w in _name_words(name, other)
        for m in [re.search(rf"\b{re.escape(w)}s?\b", text)]
        if m is not None
    ]
    return min(found) if found else -1


def _which(text: str, a: str, b: str) -> tuple[str, str] | None:
    """Which of two parts of the model's the words say is held, and which holds it, or None
    where they name neither: the one named first, unless a holder's verb follows it."""
    at_a, at_b = _at(text, a, b), _at(text, b, a)
    if at_a < 0 and at_b < 0:
        return None
    if at_b < 0 or 0 <= at_a < at_b:
        first, other, at, then = a, b, at_a, at_b
    else:
        first, other, at, then = b, a, at_b, at_a
    between = text[at : then if then > at else len(text)]
    return (other, first) if HOLDER_VERB.search(between) else (first, other)


def _says(text: str, a: str, b: str, references: Sequence[str]) -> tuple[str, str] | None:
    """Where words say one of two parts of the model's is held: which, and which holds it. Words
    naming one of them alone say it's the one held, as "the lid stays on" does, and name no
    bought part: "what the plate holds" holds nothing of the join's, and "the fan is held by the
    lid" isn't the lid held by its base."""
    said = normal(text)
    if not said or hold_word(said) is None:
        return None
    which = _which(said, a, b)
    named = [p for p, q in ((a, b), (b, a)) if _at(said, p, q) >= 0]
    if which is None or len(named) == 2:
        return which
    if which[0] != named[0] or any(mentions(said, r) for r in references):
        return None
    return which


def _pair(join: dict[str, Any], given: dict[str, set[str]]) -> tuple[str, str] | None:
    """A join's two parts where both are the model's, in its order, or None. `given` is each name
    the breakdown gives a given file, as `given_names` reads it."""
    between = [str(p) for p in join.get("parts") or []]
    mine = [p for p in between if not _plain(p).startswith(REFERENCES) and not given.get(p)]
    if len(between) != 2 or len(mine) != 2 or _plain(mine[0]) == _plain(mine[1]):
        return None
    return mine[0], mine[1]


def _printed(pair: tuple[str, str], texts: Sequence[str], references: Sequence[str]) -> Held | None:
    """A part of the model's held by the other in a join, where words say one is held: the
    needs' words first, then the join's `how`."""
    a, b = pair
    for text in texts:
        which = _says(text, a, b, references)
        if which is not None:
            return Held(which[0], "", PRINTED, which[1])
    return None


def words(found: list[Need], lines: dict[str, str]) -> list[str]:
    """What the needs say, each need's `what` and `look_for` and the request lines it covers:
    where a need may say a part of the model's is held."""
    said: list[str] = []
    for need in found:
        said += [need.what, need.look_for, *(lines.get(c, "") for c in need.covers)]
    return [one for one in said if one]


def held(
    kept: list[facts_module.Fact],
    breakdown: dict[str, Any] | None,
    references: set[str],
    said: Sequence[str] = (),
) -> list[Held]:
    """Each part a part of the model's holds: a bought part by its fact, or by the latest
    breakdown where no fact says it's fixed or loose; and a part of the model's joined to another
    in the latest breakdown, where `said`, the needs' words, or the join's `how` say it's held. A
    `fixed` or `loose` fact ends the wait wherever the breakdown names its part, on either branch
    (mqucifer/print-shop#97)."""
    found: dict[str, Held] = {}
    not_held = {one.name for one in kept if one.fixed or one.loose}
    for one in kept:
        if facts_module.bought(one.name) and one.name not in not_held and one.way_in:
            found[one.name] = Held(one.name, opposite(one.way_in), "fact", one.held_by)
    joins = [j for j in (breakdown or {}).get("joins") or [] if isinstance(j, dict)]
    given = given_names(breakdown, references)
    for join in joins:
        between = [str(p) for p in join.get("parts") or []]
        for bought in sorted(references):
            if bought in found or bought in not_held:
                continue
            others = [p for p in between if bought not in given.get(p, set())]
            if others and len(others) < len(between):
                found[bought] = Held(bought, "", "breakdown", ", ".join(others))
    printed: list[Held] = []
    for join in joins:
        pair = _pair(join, given)
        if pair is None or any(_names(p, f) for p in pair for f in not_held):
            continue
        how = str(join.get("how") or "")
        part = _printed(pair, [*said, how], sorted(references))
        if part is not None and part not in printed:
            printed.append(part)
    return [found[k] for k in sorted(found)] + printed


def way_said(one: Held) -> str:
    ways = " ".join(WAYS)
    if one.printed:
        return f"<its way off: the way {one.part} moves to come off {one.holder}, one of {ways}>"
    return one.way_out or f"<its way out: the opposite of the way it goes in, one of {ways}>"


def file_of(name: str, files: Sequence[str]) -> str:
    """A part of the model's file, by its name: the first whose stem is a word of the name."""
    return next((f for f in files if _names(name, f)), f"<{_plain(name)}'s file>")


def command(one: Held, mine: Sequence[str]) -> str:
    """The slide that reads this part's hold: the holder's files first, the held part last, as
    it's the one slid."""
    if one.printed:
        return (
            f"clearance {file_of(one.holder, mine)} {file_of(one.part, mine)} "
            f"--slide {way_said(one)}"
        )
    files = " ".join(mine) if mine else "<your part>"
    return f"clearance {files} {one.part} --slide {way_said(one)}"


def own_files(found: list[Need]) -> list[str]:
    """The model's own geometry the needs name, in order: what the slide reads the hold on."""
    named: list[str] = []
    for need in found:
        if need.by != "bench":
            continue
        try:
            files = [_plain(f) for f in need.files()]
        except ValueError:
            continue
        named += [f for f in files if not f.startswith(REFERENCES)]
    return list(dict.fromkeys(named))


def _bench_shape(one: Held, mine: Sequence[str]) -> str:
    check = {
        "by": "bench",
        "measures": "fit",
        "command": command(one, mine),
        "verdicts": [VERDICT],
    }
    return json.dumps(check)


def _why_held(one: Held) -> str:
    if one.printed:
        return f"your breakdown joins it to {one.holder}, and a need or the join says it's held"
    if one.by == "fact":
        holder = f": held by {one.holder}" if one.holder else ""
        return f"its fact says it goes in along {opposite(one.way_out)}{holder}"
    return f"your breakdown joins it to {one.holder}"


#: Said of a printed part's slide, which the words may have the wrong way round.
SWAP = "The held part goes last, as it's the one slid: swap the two if it's the other way round"


def _eye_on_printed(
    need: Need, one: Held, texts: list[str], mine: list[str], references: Sequence[str]
) -> str:
    """What an eye need on a part of the model's held by another is told, or nothing."""
    for text in texts:
        which = _says(text, one.part, one.holder, references)
        if which is None:
            continue
        word = hold_word(normal(text))
        this = Held(which[0], "", PRINTED, which[1])
        given = _given_said(_unfiled(this, mine), references)
        return (
            f"the need {need.id!r} asks the eye whether {this.part} is held by {this.holder} "
            f'("{word.group(0) if word else ""}"), and your breakdown joins them. A hold is read '
            f"on the bench, never by eye: `{command(this, mine)}` slides {this.part} off along "
            f"its way off and says `{VERDICT}` with what it meets first, or `NOT HELD` and how far "
            f'it moves. Send this need as a bench need, "check": {_bench_shape(this, mine)}, with '
            f"the same `source`, `quote` and `covers`, and its way off named. {SWAP}. "
            + (f"{given}. " if given else "")
            + "If the eye is to judge something else of it too, such as how it looks or how firm "
            "a pull feels, keep an eye need for that alone, its `what` and `look_for` naming only "
            "that"
        )
    return ""


def _unfiled(one: Held, mine: Sequence[str]) -> list[str]:
    """The two parts of a printed part's hold that no file of the model's is named for."""
    return [name for name in (one.holder, one.part) if not any(_names(name, f) for f in mine)]


def _given_said(names: Sequence[str], references: Sequence[str]) -> str:
    """Where a refusal names a part with no file and something was given: to name it by a given
    file if it's given, not to build it (mqucifer/print-shop#97), or nothing."""
    if not names or not references:
        return ""
    files = ", ".join(f"`{r}`" for r in sorted(references))
    which = files if len(references) == 1 else f"one of {files}"
    return (
        f"If {' or '.join(names)} is a given part, not one you print, name it in `breakdown` by "
        f"its file, {which}, and don't build it"
    )


def eye_on_hold(
    found: list[Need],
    holds: list[Held],
    lines: dict[str, str],
    files: Sequence[str] = (),
    references: Sequence[str] = (),
) -> list[str]:
    """Each eye need that judges a held part's hold: its `what` or `look_for`, or a request line
    it covers, names the part and says it's held. What to send instead, from the needs' own
    files, and for a part of the model's, `files`, the model's geometry, as well. `references`
    are the given files."""
    wrong: list[str] = []
    mine = own_files(found)
    theirs = list(dict.fromkeys([*mine, *files]))
    for need in found:
        if need.by != "eye":
            continue
        texts = [need.what, need.look_for, *(lines.get(c, "") for c in need.covers)]
        for one in holds:
            if one.printed:
                printed = _eye_on_printed(need, one, texts, theirs, references)
                if printed:
                    wrong.append(printed)
                    break
                continue
            said = next(
                (
                    hold_word(normal(t))
                    for t in texts
                    if t and hold_word(normal(t)) and mentions(t, one.part)
                ),
                None,
            )
            if said is None:
                continue
            wrong.append(
                f"the need {need.id!r} asks the eye whether `{one.part}` is held "
                f'("{said.group(0)}"), and {_why_held(one)}. A hold is read on the bench, never '
                f"by eye: `{command(one, mine)}` slides it out along its way out and says "
                f"`{VERDICT}` with what it meets first, or `NOT HELD` and how far it moves. Send "
                f'this need as a bench need, "check": {_bench_shape(one, mine)}, with the same '
                "`source`, `quote` and `covers`"
                + (
                    "; the way out is the opposite of the way it goes in, which its `fact` keeps"
                    if not one.way_out
                    else ""
                )
                + ". If the eye is to judge something else of it too, such as how it looks, keep "
                "an eye need for that alone, its `what` and `look_for` naming only that"
            )
            break
    return wrong


def _slid(argv: list[str]) -> tuple[list[str], str] | None:
    """A `clearance --slide` command's files and its way, or None."""
    if not argv or argv[0] != "clearance" or "--slide" not in " ".join(argv):
        return None
    files: list[str] = []
    way = ""
    k = 1
    while k < len(argv):
        word = argv[k]
        if word.startswith("--slide="):
            way = word.split("=", 1)[1]
        elif word == "--slide":
            way = argv[k + 1] if k + 1 < len(argv) else ""
            k += 1
        elif word.startswith("--"):
            k += 0 if "=" in word else 1
        elif word.lower().endswith(GEOMETRY):
            files.append(_plain(word))
        k += 1
    return files, way.strip()


@dataclass(frozen=True)
class Missing:
    """What a held part lacks at the plan stop."""

    one: Held
    mine: list[str]
    stale: list[str]


def _read_on(one: Held, files: list[str], way: str) -> list[str]:
    """The model's part files a slide read this part's hold on, or none where it isn't this
    part's slide. A bought part is the one slid, along its way out where its fact gives one; two
    parts of the model's count either way round, along any way."""
    if not files:
        return []
    if not one.printed:
        if files[-1] != one.part or (one.way_out and way != one.way_out):
            return []
        return [f for f in files[:-1] if not f.startswith(REFERENCES)]
    slid, rest = files[-1], files[:-1]
    for held_, holder in ((one.part, one.holder), (one.holder, one.part)):
        if _names(held_, slid) and any(_names(holder, f) for f in rest):
            return [f for f in files if not f.startswith(REFERENCES)]
    return []


def first_tries(
    holds: list[Held],
    bench_calls: list[dict[str, Any]],
    geometry: dict[str, list[Any]],
) -> tuple[list[Read], list[Missing]]:
    """Each held part's latest reading on a part file of the model's as it stands, and each held
    part without one. `bench_calls` are the ledger's rows, oldest first; `geometry` is each file's
    `[hash, call]` as the harness last saw it (`descriptions.seen`)."""
    mine = sorted(
        name
        for name, seen in geometry.items()
        if seen and seen[0] is not None and not name.startswith(REFERENCES)
    )
    found: list[Read] = []
    missing: list[Missing] = []
    for one in holds:
        latest: Read | None = None
        stale: list[str] = []
        for row in bench_calls:
            try:
                argv = json.loads(row["argv"]) if isinstance(row["argv"], str) else row["argv"]
            except ValueError:
                continue
            slid = _slid([str(w) for w in argv])
            if slid is None or int(row["exit"]) == 3 or not row.get("verdict"):
                continue
            files, way = slid
            parts = _read_on(one, files, way)
            if not parts:
                continue
            at = int(row["at_call"])
            changed = [
                f
                for f in parts
                if not geometry.get(f) or geometry[f][0] is None or int(geometry[f][1]) > at
            ]
            if changed:
                stale = [f"`{f}` (read at call {at})" for f in changed]
                continue
            why = " ".join(str(row.get("why") or "").split())
            why = why if len(why) <= WHY_CHARS else f"{why[:WHY_CHARS]}…"
            latest = Read(one.part, shlex.join(argv), at, str(row["verdict"]), why)
            stale = []
        if latest is not None:
            found.append(latest)
        else:
            missing.append(Missing(one, mine, stale))
    return found, missing


FIXED_SAID = (
    "If it sits fixed in place and nothing you make holds it, say so with its `fact`, `fixed: "
    "true` in place of `way_in` and `held_by`, and the plan goes as it is."
)
#: Said of every bought part the plan waits for (mqucifer/print-shop#91).
LOOSE_SAID = (
    "If nothing is meant to hold it, as with a loose fit, say so with its `fact`, `loose: true` "
    "in place of `held_by`, and the plan goes as it is."
)


def plan_waits(missing: list[Missing], references: Sequence[str] = ()) -> str:
    """The plan stop's refusal: for each held part, what's missing, and what to do next.
    `references` are the given files."""
    lines = [
        "The plan waits for a first try at each part you hold and the bench's reading of it "
        "(mqucifer/print-shop#87): the bench reads a hold in seconds, but only on a part."
    ]
    for gap in missing:
        one = gap.one
        if one.printed:
            lines.append(_printed_waits(gap, references))
            continue
        said = f"- `{one.part}`: {_why_held(one)}."
        if not gap.mine:
            said += (
                " Missing: a part file of yours, and its reading. Build a rough first try with "
                f"`write` and `run`, then read it with `bench {command(one, [])}`."
            )
        elif gap.stale:
            said += (
                f" Its reading is on a part file that has changed since: {', '.join(gap.stale)}. "
                f"Read it again as it stands: `bench {command(one, gap.mine[:1])}`."
            )
        else:
            said += (
                f" Missing: its reading. You have {', '.join(f'`{f}`' for f in gap.mine)}: read "
                f"the hold with `bench {command(one, gap.mine[:1])}`, naming the file that "
                "holds it."
            )
        if not one.way_out:
            said += (
                " Its way out is the opposite of the way it goes in: keep that with its `fact`, "
                f"`way_in` and `held_by`. {FIXED_SAID}"
            )
        elif SAYS_FIXED.search(one.holder.lower()):
            said += f" Its `held_by` says it's fixed. {FIXED_SAID}"
        said += f" {LOOSE_SAID}"
        lines.append(said)
    lines.append(
        "The first try doesn't have to hold: its reading goes beside the plan, whatever the "
        "verdict, and the plan says what you'd change. Then `ask` the plan again."
    )
    return "\n".join(lines)


def _printed_waits(gap: Missing, references: Sequence[str] = ()) -> str:
    """The plan's refusal for a part of the model's held by another (mqucifer/print-shop#90). A
    part with no file may be a given part named in words: it's told how to name it
    (mqucifer/print-shop#97)."""
    one = gap.one
    said = f"- {one.part}: {_why_held(one)}."
    unfiled = _unfiled(one, gap.mine)
    if unfiled:
        said += (
            f" Missing: a part file for each of {one.holder} and {one.part}, and its reading. "
            "Build a rough first try with `write` and `run`, then read it with "
            f"`bench {command(one, gap.mine)}`."
        )
        given = _given_said(unfiled, references)
        said += f" {given}." if given else ""
    elif gap.stale:
        said += (
            f" Its reading is on a part file that has changed since: {', '.join(gap.stale)}. "
            f"Read it again as it stands: `bench {command(one, gap.mine)}`."
        )
    else:
        said += f" Missing: its reading: `bench {command(one, gap.mine)}`."
    return f"{said} {SWAP}, and name its way off."


def said(found: list[Read]) -> list[str]:
    """Each reading, as the ask's reply and the plan's stop give it."""
    return [
        f"- `{one.bought}`: `{one.command}`, call {one.at_call}: {one.verdict}"
        + (f": {one.why}" if one.why else "")
        for one in found
    ]


def at_stop(found: list[dict[str, Any]]) -> list[str]:
    """The readings under their own heading in `question.md`."""
    if not found:
        return []
    return [
        AT_STOP,
        "",
        "A part of the model's holds each of these parts, so the plan waited for a first "
        "try and its reading. The first try doesn't have to hold: this is what the bench read, "
        "whatever the verdict.",
        "",
        *said([Read(**one) for one in found]),
        "",
    ]


def plain(found: list[Read]) -> list[dict[str, Any]]:
    return [asdict(one) for one in found]


def read_at_intake(found: list[Read]) -> str:
    """The line that leads each `bench` and `run` reply at intake once a hold reads `HELD`, or
    nothing (mqucifer/print-shop#92)."""
    parts = list(dict.fromkeys(one.bought for one in found if one.verdict == VERDICT))
    if not parts:
        return ""
    named = ", ".join(f"`{p}`" for p in parts[:-1])
    named = f"{named} and `{parts[-1]}`" if named else f"`{parts[-1]}`"
    return READ_AT_INTAKE.format(parts=named, reads="read" if len(parts) > 1 else "reads")
