"""When the model is stuck, it's told plainly (mqucifer/print-shop#79).

Read across 40 long calls (`~/print-shop/skill-evals/long-calls-2026-11-08/SUMMARY.md`), going
round in circles was the main or second cause in 32, a contradiction drove 7, and the model never
reached for `ask`. On `pi5-case-1` (`pi5-s3-stretch-2026-11-08/REPORT.md` §§2 and 5) a stand-in's
note reopened the needs, the model's own settled need said the opposite, and nothing set the two
side by side: it sided with its need, at 217, and the note's three new needs were never sent. That
need expected an overlap with the fan, so no bench reply could point at the overlap as the fault.
After a 30-minute call the harness's nudge to `ask` came, and the next call re-ran the same check.

Three classes, each fixed where its trigger is:
- **A contradiction nobody points out.** A note that reopens the needs sets every settled need
  beside it, and `needs` is refused until each is marked kept or changed against the note
  (`beside`, `unmarked`). Until `needs` is taken, each `bench` and `run` reply and each
  compaction's note leads with `REOPENED`.
- **A check that passes the fault.** A need expecting `INTERFERE` against a given part is refused
  (`overlaps`): an overlap with a bought part can't hold it. It stands only once a person says yes
  at an `ask` that showed the overlap, as the harness read it.
- **Not asking.** After a call the recorder marks as a runaway, or a call of `CIRCLING_TOKENS` of
  reasoning or more that re-runs a check whose last try failed on files unchanged since, the next
  tool reply leads with `CIRCLED`. The call itself is never cut (the Sponsor, 2026-11-02).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from typing import Any

from print_shop.rounds import nudge
from print_shop.rounds.needs import Need

#: What leads each `bench` and `run` reply, and each compaction's note, while a note's reopening
#: of the needs hasn't been answered with `needs`.
REOPENED = (
    "A note reopened the needs; send `needs`, with each settled need marked kept or changed "
    "against it in `against_note`."
)
#: What leads the next tool reply after a call that went round in circles.
CIRCLED = (
    "Your last reasoning went over the same ground. Run one check that settles it, or `ask` with "
    "what you have."
)
#: Reasoning tokens from which a call that re-runs a failed check on unchanged files counts as one
#: that circled: the long-calls study's own line, 163 calls of 16,000 or more.
CIRCLING_TOKENS = 16_000
#: How a settled need is marked against the note.
MARKS = ("kept", "changed")
#: The verdict of an overlap.
OVERLAP = "INTERFERE"
REFERENCES = "references/"
#: How much of the harness's reading of an overlap the model and the person are shown.
OVERLAP_CHARS = 1500
SLIDE_ISSUE = "mqucifer/print-shop#76"
#: The ways a part can leave, as `clearance --slide` takes them.
WAYS = "+x -x +y -y +z -z"


def _plain(name: str) -> str:
    return name.removeprefix("/work/").removeprefix("./")


def _check(need: Need) -> str:
    if need.by == "bench":
        return f"checked by `{need.command}` ({need.limit()})"
    if need.by == "eye":
        return f"checked by the Sponsor's eye: {need.look_for}"
    return f"checked by `on_bed`: {need.limit()}"


def bought(need: Need) -> list[str]:
    """The given parts a need expects its files to overlap: `INTERFERE` among its verdicts, on a
    command that names a file under `references/`."""
    if need.by != "bench" or OVERLAP not in need.verdicts:
        return []
    try:
        files = need.files()
    except ValueError:
        return []
    return [_plain(f) for f in files if _plain(f).startswith(REFERENCES)]


def accepted(need: Need, overlaps: list[dict[str, Any]]) -> bool:
    """Whether a person said yes to this need's overlap at an ask: its id, and its command as
    asked."""
    try:
        argv = need.argv()
    except ValueError:
        return False
    for one in overlaps:
        if one.get("need") != need.id:
            continue
        try:
            if shlex.split(str(one.get("command") or "").removeprefix("bench ")) == argv:
                return True
        except ValueError:
            continue
    return False


def slide_said(need: Need, given: list[str]) -> str:
    """The `clearance --slide` command that finds what holds a given part, from the need's own
    files: the part's files first, the bought part last, as it's the one slid."""
    try:
        files = [_plain(f) for f in need.files()]
    except ValueError:
        files = []
    mine = [f for f in files if not f.startswith(REFERENCES)] or ["<your part>"]
    return f"clearance {' '.join(mine)} {given[0]} --slide <its way out: one of {WAYS}>"


def overlaps(found: list[Need], accepted_overlaps: list[dict[str, Any]]) -> list[str]:
    """Each need that expects an overlap with a given part, and no person has accepted: what's
    wrong, and what to do instead."""
    wrong = []
    for need in found:
        given = bought(need)
        if not given or accepted(need, accepted_overlaps):
            continue
        named = ", ".join(f"`{g}`" for g in given)
        wrong.append(
            f"the need {need.id!r} expects {OVERLAP} with {named}, a part you were given: it "
            "settles an overlap with a bought part as right, so no check could point at that "
            "overlap as the fault. An overlap with a bought part can't hold it: the part has to "
            "pass through that material to go in or come out. What holds it is material in its "
            f"way out, which `{slide_said(need, given)}` finds ({SLIDE_ISSUE}): `HELD` names what "
            "holds it, and what it meets on the way is what has to bend or be fitted after. "
            "Check the hold that way. If the overlap is meant, show it with `ask`, giving "
            f'`overlap` {{"need": "{need.id}", "command": "{need.command}"}}: the harness reads '
            "it and puts it beside your question, and only a person's yes lets this need stand"
        )
    return wrong


def _reading(reading: dict[str, Any] | None) -> str:
    if not reading:
        return "not read at `done` yet"
    said = "passed" if reading.get("passed") else "failed"
    verdict = f" ({reading['verdict']})" if reading.get("verdict") else ""
    why = " ".join(str(reading.get("why") or "").split())
    why = why if len(why) <= 200 else f"{why[:200]}…"
    return f"at the last `done` it {said}{verdict}" + (f": {why}" if why else "")


def beside(
    settled: list[Need],
    readings: dict[str, dict[str, Any]],
    whence: str,
    fixed_locked: bool,
    accepted_overlaps: list[dict[str, Any]],
) -> str:
    """What the model's told after a note that reopens the needs: each settled need beside the
    note, its words, its check and how it last read, and what to send. On `pi5-case-1` the
    model's own clip need said the opposite of the stand-in's note, and nothing set the two side
    by side."""
    lines = [
        f"{whence[0].upper()}{whence[1:]} reopened the needs. Each settled need, beside it:",
    ]
    for need in settled:
        line = f'- `{need.id}`: "{need.what}", {_check(need)}; {_reading(readings.get(need.id))}.'
        if need.fixed and fixed_locked:
            line += " It's fixed and stays as it is, so it's kept."
        given = bought(need)
        if given and not accepted(need, accepted_overlaps):
            line += (
                f" It expects {OVERLAP} with `{given[0]}`, a given part, which `needs` now "
                "refuses unless a person accepts that overlap at an `ask`."
            )
        lines.append(line)
    example = ", ".join(
        f'"{n.id}": {{"is": "kept", "why": "..."}}' for n in settled[:2]
    ) + (", ..." if len(settled) > 2 else "")
    lines += [
        "",
        f"Read each against {whence}. Where they say different things, the note is the newer word "
        "from a person: change the need to fit it, or, if you think the need is still right, say "
        "why. Then send `needs` with the whole list, and in `against_note` mark each of these "
        f"kept or changed, with a line on why, against the note: {{{example}}}. A need dropped is "
        "changed. `needs` is refused until each is marked, and until it's taken each `bench` and "
        "`run` reply leads with a line saying so. If the note leaves you unsure what it wants, "
        "`ask`.",
    ]
    return "\n".join(lines)


def mark_of(value: Any) -> tuple[str, str] | None:
    """A mark as sent: {"is": "kept", "why": "..."}, or "kept: ..." as text."""
    if isinstance(value, dict):
        said = str(value.get("is") or "").strip().lower()
        why = str(value.get("why") or "").strip()
    elif isinstance(value, str):
        word, _, rest = value.strip().partition(" ")
        said = word.strip(":,;.-—").lower()
        why = rest.strip(" :,;.-—")
    else:
        return None
    return (said, why) if said in MARKS else None


def unmarked(
    marks: Any,
    reopened: list[Need],
    sent: list[Need],
    same: Callable[[Need, Need], bool],
) -> list[str]:
    """Each settled need the note reopened that `against_note` doesn't mark, or marks against what
    the list sends: kept means sent as it was, and changed means it isn't."""
    given = marks if isinstance(marks, dict) else {}
    now = {n.id: n for n in sent}
    wrong = []
    for old in reopened:
        mark = mark_of(given.get(old.id))
        shape = f'{{"{old.id}": {{"is": "kept" or "changed", "why": "..."}}}}'
        if mark is None:
            wrong.append(
                f"`{old.id}` isn't marked: it was \"{old.what}\", {_check(old)}. Read it against "
                f"the note, and mark it in `against_note`, {shape}"
            )
            continue
        said, why = mark
        new = now.get(old.id)
        if not why:
            wrong.append(f"`{old.id}` is marked {said} with no reason: give a line on why, {shape}")
        elif said == "kept" and new is None:
            wrong.append(
                f"`{old.id}` is marked kept, and this list doesn't have it: send it as it was, or "
                "mark it changed if it's meant to go"
            )
        elif said == "kept" and not same(old, new):  # type: ignore[arg-type]
            wrong.append(
                f"`{old.id}` is marked kept, and this list changes it: mark it changed, with why, "
                "or send it as it was"
            )
        elif said == "changed" and new is not None and same(old, new):
            wrong.append(
                f"`{old.id}` is marked changed, and this list sends it as it was: change it, drop "
                "it, or mark it kept, with why"
            )
    return wrong


def refused(marks_wrong: list[str], overlaps_wrong: list[str]) -> tuple[str, str]:
    """The refusal of a `needs` call, and its reason's code: each need left unmarked, and each
    expecting an overlap with a given part."""
    lines = ["The needs weren't taken:"]
    lines += [f"- {w}" for w in [*marks_wrong, *overlaps_wrong]]
    if marks_wrong:
        lines.append(
            "Mark every settled need the note reopened, each kept or changed with a line on why "
            "against the note, and send the whole list again."
        )
    return "\n".join(lines), "not_marked" if marks_wrong else "overlap_with_bought"


def rerun(earlier: list[nudge.Try], argv: list[str], geometry: dict[str, list[Any]]) -> Any:
    """The earlier try this bench call repeats, if it's a check whose last try failed, run on
    files unchanged since: `geometry` is each file's hash and the call by which the harness saw
    it as it is (`descriptions.seen`). `earlier` leaves out this call's own tries. None if not."""
    check = nudge.check_of(argv)
    if not check[0] or not check[1]:
        return None
    before = [t for t in earlier if t.exit != nudge.USAGE and nudge.check_of(t.argv) == check]
    if not before or before[-1].exit == 0:
        return None
    last = before[-1]
    for name in check[1]:
        seen = geometry.get(_plain(name))
        if not seen or seen[0] is None or int(seen[1]) > last.at_call:
            return None
    return last


def overlap_said(need: str, command: str, verdict: str, why: str) -> str:
    """The harness's reading of an overlap an ask shows, for the model."""
    return (
        f"The overlap goes beside the question: `{command}` reads {verdict} on your files as they "
        f"stand: {why}. Only a pick other than none accepts `{need}` expecting it; a none "
        "leaves it refused."
    )


def overlap_at_stop(overlap: dict[str, Any]) -> list[str]:
    """The overlap an ask shows, as the person reads it in `question.md`."""
    return [
        "## An overlap with a given part, for you to accept or not",
        "",
        f"The model wants its need `{overlap['need']}` to expect {OVERLAP} between its part and a "
        f"part it was given, read by `{overlap['command']}`. An overlap with a bought part can't "
        "hold it: the part would have to pass through that material. So the harness refuses this "
        "need unless you accept the overlap as meant, such as a press fit you asked for.",
        "",
        "Any pick but `none` accepts it. `none` doesn't, and the model is told to find what holds "
        "the part another way.",
        "",
        "The harness's reading at the ask:",
        "",
        "```",
        str(overlap.get("text") or overlap.get("why") or "").strip(),
        "```",
        "",
    ]


def overlap_picked(overlap: dict[str, Any], who: str, accepted_it: bool) -> str:
    """What the model's told of the overlap an ask showed, with the pick."""
    command = overlap["command"]
    if accepted_it:
        return (
            f"\n\n{who} accepted the overlap `{overlap['need']}` expects, read by `{command}`: "
            f"send `{overlap['need']}` with that command and {OVERLAP} among its verdicts, and "
            "`needs` takes it."
        )
    given = [w for w in command.split() if _plain(w).startswith(REFERENCES)] or ["the part"]
    return (
        f"\n\n{who} didn't accept the overlap `{overlap['need']}` expects, read by `{command}`: "
        "an overlap with a bought part doesn't hold it. Find what does with `clearance <your "
        f"part> {_plain(given[0])} --slide <its way out>` ({SLIDE_ISSUE})."
    )
