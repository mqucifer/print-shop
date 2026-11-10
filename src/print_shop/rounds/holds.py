"""A hold is read by the bench on the slide, never by eye, and a held part is built and read before
its plan (mqucifer/print-shop#86, mqucifer/print-shop#87).

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
A fact with `fixed` is a part already in place and never fitted, such as a box a lid sits on: no
part holds it. A held part's way out is the opposite of its fact's way in, or unknown without one.

**Two rules, each said with what to do next:**
- `needs` refuses an eye need on a held part's hold: its words, or a request line it covers, name
  the part and say it's held. The refusal gives the bench need to send instead, its `clearance
  <part> <bought part> --slide <way out>` from the needs' own files, with `HELD`.
- At the plan stop, `ask` waits for each held part's first try and its reading: a part file of the
  model's, and a `clearance ... --slide <way out>` reading on it as it stands, whatever its
  verdict. The reading goes beside the plan, in `question.md`. Nothing says how to build a clip:
  shapes wait for the Sponsor's lid guidelines and mqucifer/print-shop#9.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from print_shop.rounds import facts as facts_module
from print_shop.rounds.needs import GEOMETRY, Need, normal

REFERENCES = "references/"
WAYS = facts_module.WAYS
#: Words that say a part is held, or isn't, as a need or a request line puts it. A clip or a snap
#: is a thing, not a hold: "the clips look neat" judges a look.
HOLD = re.compile(
    r"\b(?:hold|holds|held|holding|retain(?:s|ed|ing)?|grip(?:s|ped|ping)?|secur(?:e|es|ed|ing)"
    r"|captive|in place|stays? (?:in|put|seated)|falls? out|comes? (?:out|loose)|loose)\b"
)
#: How the slide's verdicts a need takes read.
VERDICT = "HELD"
WHY_CHARS = 300
#: What the start adds where something was given, and the plan stop's own words: two lines.
START = (
    "Where a part of yours holds a bought part, build a rough first try and read its hold with "
    "`bench clearance <part> <bought part> --slide <its way out>` before the plan stop, not after: "
    "the bench reads a hold in seconds, but only on a part.\nThe first try doesn't have to hold. "
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
AT_STOP = "## What holds each bought part, as the bench read it"


@dataclass(frozen=True)
class Held:
    """A bought part a part of the model's holds: its file, its way out (empty where no fact
    gives its way in), what said so, and what holds it, in words."""

    bought: str
    way_out: str
    by: str
    holder: str


@dataclass(frozen=True)
class Read:
    """One held part's reading, as the plan's stop shows it."""

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


def _names(name: str, bought: str) -> bool:
    """Whether a breakdown's part name is the bought part: its path, its file name, or its stem as
    a word of the name, such as "fan" or "the fan (bought)" for `references/fan.step`."""
    said = _plain(name).lower()
    path = PurePosixPath(bought)
    if said in (bought.lower(), path.name.lower()):
        return True
    return re.search(rf"\b{re.escape(_stem(bought))}\b", said) is not None


def mentions(text: str, bought: str) -> bool:
    """Whether words name the bought part, by its path or its stem as a word."""
    said = normal(text)
    if bought.lower() in said or PurePosixPath(bought).name.lower() in said:
        return True
    return re.search(rf"\b{re.escape(_stem(bought))}s?\b", said) is not None


def held(
    kept: list[facts_module.Fact], breakdown: dict[str, Any] | None, references: set[str]
) -> list[Held]:
    """Each bought part a part of the model's holds, by its fact, or by the latest breakdown where
    no fact says it's fixed."""
    found: dict[str, Held] = {}
    fixed = {one.name for one in kept if one.fixed}
    for one in kept:
        if facts_module.bought(one.name) and not one.fixed and one.way_in:
            found[one.name] = Held(one.name, opposite(one.way_in), "fact", one.held_by)
    for join in (breakdown or {}).get("joins") or []:
        between = [str(p) for p in join.get("parts") or []]
        for bought in sorted(references):
            if bought in found or bought in fixed:
                continue
            others = [p for p in between if not _names(p, bought)]
            if others and len(others) < len(between):
                found[bought] = Held(bought, "", "breakdown", ", ".join(others))
    return [found[k] for k in sorted(found)]


def way_said(one: Held) -> str:
    ways = " ".join(WAYS)
    return one.way_out or f"<its way out: the opposite of the way it goes in, one of {ways}>"


def command(one: Held, mine: list[str]) -> str:
    """The slide that reads this part's hold: the part's files first, the bought part last, as it's
    the one slid."""
    files = " ".join(mine) if mine else "<your part>"
    return f"clearance {files} {one.bought} --slide {way_said(one)}"


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


def _bench_shape(one: Held, mine: list[str]) -> str:
    check = {
        "by": "bench",
        "measures": "fit",
        "command": command(one, mine),
        "verdicts": [VERDICT],
    }
    return json.dumps(check)


def _why_held(one: Held) -> str:
    if one.by == "fact":
        holder = f": held by {one.holder}" if one.holder else ""
        return f"its fact says it goes in along {opposite(one.way_out)}{holder}"
    return f"your breakdown joins it to {one.holder}"


def eye_on_hold(found: list[Need], holds: list[Held], lines: dict[str, str]) -> list[str]:
    """Each eye need that judges a held part's hold: its `what` or `look_for`, or a request line
    it covers, names the part and says it's held. What to send instead, from the needs' own
    files."""
    wrong: list[str] = []
    mine = own_files(found)
    for need in found:
        if need.by != "eye":
            continue
        texts = [need.what, need.look_for, *(lines.get(c, "") for c in need.covers)]
        for one in holds:
            said = next(
                (
                    HOLD.search(normal(t))
                    for t in texts
                    if t and HOLD.search(normal(t)) and mentions(t, one.bought)
                ),
                None,
            )
            if said is None:
                continue
            wrong.append(
                f"the need {need.id!r} asks the eye whether `{one.bought}` is held "
                f'("{said.group(0)}"), and {_why_held(one)}. A hold is read on the bench, never '
                f"by eye: `{command(one, mine)}` slides it out along its way out and says "
                f"`{VERDICT}` with what it meets first, or `NOT HELD` and how far it moves. Send "
                f'this need as a bench need, "check": {_bench_shape(one, mine)}, with the same '
                "`source`, `quote` and `covers`"
                + (
                    "; the way out is the opposite of the way it goes in, which its `fact` "
                    "keeps"
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
            if not files or files[-1] != one.bought or (one.way_out and way != one.way_out):
                continue
            parts = [f for f in files[:-1] if not f.startswith(REFERENCES)]
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
            latest = Read(one.bought, shlex.join(argv), at, str(row["verdict"]), why)
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


def plan_waits(missing: list[Missing]) -> str:
    """The plan stop's refusal: for each held part, what's missing, and what to do next."""
    lines = [
        "The plan waits for a first try at each part you hold and the bench's reading of it "
        "(mqucifer/print-shop#87): the bench reads a hold in seconds, but only on a part."
    ]
    for gap in missing:
        one = gap.one
        said = f"- `{one.bought}`: {_why_held(one)}."
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
        lines.append(said)
    lines.append(
        "The first try doesn't have to hold: its reading goes beside the plan, whatever the "
        "verdict, and the plan says what you'd change. Then `ask` the plan again."
    )
    return "\n".join(lines)


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
        "A part of the model's holds each of these bought parts, so the plan waited for a first "
        "try and its reading. The first try doesn't have to hold: this is what the bench read, "
        "whatever the verdict.",
        "",
        *said([Read(**one) for one in found]),
        "",
    ]


def plain(found: list[Read]) -> list[dict[str, Any]]:
    return [asdict(one) for one in found]
