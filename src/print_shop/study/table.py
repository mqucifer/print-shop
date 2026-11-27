"""A study's table and recommendation, for the Sponsor at the gate (mqucifer/print-shop#339).

Each candidate's time and grams against the profile's own, and its worst figure under each load
case against its limit, with the margin; the candidates that fail and why; the one recommended
and why, and how far past the defaults it saves or costs; what each figure rests on; and the
walls as built against what `load` reads. Nothing in it was printed or armed.
"""

from __future__ import annotations

import contextlib
import math
import os
from pathlib import Path
from typing import Any

from print_shop.study.loop import StudyResult, front, reading_said
from print_shop.study.reading import Reading, margin_said, unchecked

__all__ = ["as_plain", "table"]


def _cell_time(r: Reading, base: Reading | None) -> str:
    s = r.sliced
    if s is None or s.time_s is None:
        return "-"
    said = f"{s.time_s / 60:.1f} min"
    b = base.sliced if base is not None else None
    if b is not None and b.time_s is not None and r is not base:
        said += f" ({(s.time_s - b.time_s) / 60:+.1f})"
    return said


def _cell_grams(r: Reading, base: Reading | None) -> str:
    s = r.sliced
    if s is None or s.grams is None:
        return "-"
    said = f"{s.grams:.1f} g"
    b = base.sliced if base is not None else None
    if b is not None and b.grams is not None and r is not base:
        said += f" ({s.grams - b.grams:+.1f})"
    return said


def _reading(r: Reading) -> bool:
    """Whether it's queued or reading still: nothing landed, and nothing said against it."""
    return r.sliced is None and not r.error and not r.cases


def _cell_case(r: Reading, name: str) -> str:
    if _reading(r):
        return "reading"
    if r.error:
        return "not as sliced" if r.error.startswith("not as sliced") else "not sliced"
    case = next((c for c in r.cases if c.case == name), None)
    if case is None:
        return "-"
    if case.verdict not in ("OK", "FAIL"):
        return case.verdict
    worst = case.worst
    if worst is None:
        return case.verdict
    mark = "" if case.passes else " FAILS"
    # Only where no published test reaches its density (mqucifer/print-shop#342's `unchecked`):
    # every figure of a printed part rests on the infill model.
    rests = "*" if worst.unchecked else ""
    if not math.isfinite(worst.share):
        return f"past its limit{rests}{mark}"
    return f"{100 * worst.share:.0f}%{rests} ({margin_said(worst.margin)}){mark}"


def _escape(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _project(r: Reading, where: Path | None) -> str:
    """A link to the candidate's Orca project, `candidates/NAME/regions.3mf` (or `part.3mf`
    where it has no regions), from the folder `study.md` is in: the Sponsor opens it in Orca to
    see its modifiers and settings by eye (mqucifer/print-shop#357)."""
    project = r.sliced.project if r.sliced is not None else None
    if project is None:
        return "-"
    path = Path(project)
    if where is not None:
        # Another drive's path stays whole.
        with contextlib.suppress(ValueError):
            path = Path(os.path.relpath(path, where))
    return f"[{path.name}](<{path.as_posix()}>)"


def table(result: StudyResult, part: str, cases: list[str], where: Path | None = None) -> str:
    """The study as Markdown, for the gate, its links to each candidate's Orca project from
    `where`, the folder it's written in."""
    base = result.defaults
    rows = result.readings
    out = [f"# Settings study: {part}", "",
           f"{len(rows) - 1} candidates of the model's own read beside the profile's defaults, "
           f"under {len(cases)} load case{'s' if len(cases) != 1 else ''}, by {result.model}, "
           f"in {result.seconds / 60:.0f} min: {result.ended}. Nothing was printed or armed.",
           "", f"Read within the host's budget, {result.budget or 'one job at a time'}: at most "
           f"{result.most_at_once or 1} slices and reads at once, each single-threaded, so each "
           "figure is what it is read alone.",
           "", "Each case's cell is its worst figure as a share of its limit, and the margin "
           "left. Every figure of a printed part rests on the infill model's published tests, "
           "which no print has checked yet (mqucifer/print-shop#340); * is a figure whose core, "
           "or the core its skin rests on, is below the density any published test of its "
           "pattern reaches, so no test checks it.", "",
           "Each candidate's Orca project, its regions as Orca's own modifiers, is linked in its "
           "row: open it in Orca to see them and its settings by eye.", "",
           "| Candidate | Settings | Time | Grams | " + " | ".join(cases)
           + " | Passes | Orca project |",
           "|---|---|---|---|" + "---|" * len(cases) + "---|---|"]
    for r in rows:
        passes = "reading" if _reading(r) else "yes" if r.passes else "no"
        out.append(f"| {r.candidate.name} | {_escape(r.candidate.said())} | "
                   f"{_cell_time(r, base)} | {_cell_grams(r, base)} | "
                   + " | ".join(_cell_case(r, c) for c in cases)
                   + f" | {passes} | {_project(r, where)} |")
    out.append("")
    chosen = result.recommendation
    if chosen is not None:
        out.append(f"**Recommended: {chosen.candidate.name}**, {chosen.candidate.said()}.")
        out.append("")
        out.append(f"Its Orca project: {_project(chosen, where)}.")
        out.append("")
        out.append(f"Why: {result.reason}")
        out.append("")
        out.append(_past_defaults(chosen, base))
        out.append("")
        out.append(_rests_on(chosen))
    else:
        why = result.reason or result.ended
        out.append(f"**No recommendation**: {why}.")
    best = front(rows)
    if best:
        out += ["", "The trade-off: no other passing candidate beats "
                + ", ".join(r.candidate.name for r in best)
                + " on time, grams and margin at once."]
    failing = [r for r in rows if not r.passes]
    if failing:
        out += ["", "## Those that fail, and why", ""]
        out += [f"- {r.candidate.name}: {_escape(r.why_not())}" for r in failing]
    out += ["", "## Each candidate's figures", ""]
    for r in rows:
        why = f" ({r.candidate.why})" if r.candidate.why else ""
        lines = reading_said(r, base)
        out.append(f"- {lines[0]}{why}")
        out += [f"  {line.strip()}" for line in lines[1:]]
    out += ["", "## What the figures rest on", "",
            "- **The infill model:** each core's figures are published tests of PLA, ABS and "
            "PETG, taken as shares of their own plastic's (`bench/infill.py`, "
            "`docs/bench.md`), none yet checked by a print from this printer: that's the "
            "coupons, mqucifer/print-shop#340. A figure marked * rests on them, and the skin's "
            "rest on them too, as what the skin carries is what the core doesn't."]
    loose = [f"{r.candidate.name} ({'; '.join(found)})" for r in rows if r.sliced is not None
             and (found := unchecked(r.sliced.printed))]
    if loose:
        out.append(f"- **Below the densities the tests reach:** {', '.join(loose)}: their "
                   "core's figures are its lines' at the tests' share, which no test checks, "
                   "and so are its skin's, which rest on them.")
    notes = sorted({n for r in rows for c in r.cases for n in c.notes
                    if "read solid" in n or "solved again" in n})
    if notes:
        out.append("- **Round the joints, read solid:** " + "; ".join(notes[:3]) + ".")
    out.append("- **One pass only:** a printed part `load` would read in two passes has no "
               "answer, and says so.")
    bands = [f"- {r.candidate.name}: {line}" for r in rows if r.sliced is not None
             for line in r.sliced.band_said()]
    if bands:
        out += ["", "## The walls as built, a second reading", "",
                "The walls' band read from the layers, against load's thickness, n of Orca's "
                "bead spacings (`slicer.regions.band`):", "", *bands]
    if result.lessons:
        out += ["", "## What it found, as lessons for a reviewer", ""]
        out += [f"- {lesson['text']} (shown by {', '.join(lesson['shown_by']) or 'the study'})"
                for lesson in result.lessons]
    return "\n".join(out) + "\n"


def _rests_on(chosen: Reading) -> str:
    """What the recommended candidate's figures rest on, in its own paragraph."""
    said = ["Its figures rest on the infill model's published tests, which no print from this "
            "printer has checked yet (mqucifer/print-shop#340)"]
    if chosen.sliced is not None and (loose := unchecked(chosen.sliced.printed)):
        said.append("and some are checked by no test at all, below the densities they reach: "
                    + "; ".join(loose))
    else:
        said.append("and every core's density is within what the tests of its pattern reach")
    text = ", ".join(said) + "."
    if chosen.sliced is not None and chosen.sliced.unread:
        text += (" Not read from the layers, so load takes it as asked: "
                 + "; ".join(chosen.sliced.unread) + ".")
    return text


def _past_defaults(chosen: Reading, base: Reading | None) -> str:
    if base is None or chosen is base:
        return "It's the profile's own settings."
    said = []
    s, b = chosen.sliced, base.sliced
    if s is not None and b is not None:
        if s.time_s is not None and b.time_s is not None:
            said.append(f"{(s.time_s - b.time_s) / 60:+.1f} min")
        if s.grams is not None and b.grams is not None:
            said.append(f"{s.grams - b.grams:+.1f} g")
    if chosen.margin is not None:
        if base.margin is not None and base.answered:
            said.append(f"its worst figure at {margin_said(chosen.margin)}, where the "
                        f"defaults' is at {margin_said(base.margin)}"
                        + ("" if base.passes else ", so they fail"))
        else:
            said.append(f"its worst figure at {margin_said(chosen.margin)}, where the defaults "
                        "have no answer")
    return "Against the profile's defaults: " + ", ".join(said) + "."


def as_plain(result: StudyResult, part: str) -> dict[str, Any]:
    """The study as JSON."""
    return {
        "part": part, "ended": result.ended, "model": result.model,
        "model_calls": result.model_calls, "seconds": result.seconds,
        "recommended": result.recommended, "reason": result.reason,
        "budget": result.budget, "most_at_once": result.most_at_once,
        "lessons": result.lessons,
        "readings": [r.plain() for r in result.readings],
        "front": [r.candidate.name for r in front(result.readings)],
        "calls": [c.model_dump() for c in result.calls],
    }
