"""Run a past job again: its command line, the review's correction line and an answers file.

The Sponsor, 2026-11-15: "since im often re-running with the same settings, to be be able to see
and re-copy those". Three things, each copied from the page and never run by it:
- **the command line,** with the part's file, found by the sha256 the ledger keeps
  (`read.PartFinder`: the log's path, the parts folder, the job's folder), the nozzle and
  filament the print used, its machine and process profiles by name, `--as-laid` if it was
  kept as laid, and `--answers` naming the answers file where the browser saves it;
- **the correction line,** typed at the review as it was then (`key=value because …; …`, read
  by `review.parse_answer`): the Sponsor's own corrections, in order;
- **the answers file** for `--answers` (`intake.replay_asker`): each question by its text, each
  answer by its option's label or the words given.

What these can't carry is said beside them, never dropped silently: a pose sent back at the
review is chosen at the review again (`orientation`), and a change taken from the slice's
reading is taken again (`take`) when the slice proposes it. Neither is put in the correction
line, which would record them as the Sponsor's own corrections, and each correction is drawn as
a lesson.

The line doesn't arm the printer. Arming stays on the command's own line, as the runbook says.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from print_shop.ledger import Row
from print_shop.orchestrator.graph import AS_LAID
from print_shop.setup.proposal import ORIENTATION, Source

__all__ = ["DOWNLOADS", "Again", "again", "answers_file", "correction_line"]

#: Where a browser saves the answers file by default.
DOWNLOADS = "~/Downloads"

#: What `parse_answer` reads a correction with no reason as.
_NO_REASON = "your correction"


@dataclass(frozen=True)
class Again:
    """A past job, as it's run again."""

    command: str
    #: The review's correction line, or "" for a job with none.
    corrections: str
    #: The `--answers` file's content, or None for a job asked no questions.
    answers: dict[str, Any] | None
    answers_name: str
    #: What the lines can't carry, each a sentence.
    notes: tuple[str, ...]


def answers_file(row: Row) -> dict[str, Any] | None:
    """The job's answers as `--answers` replays them, or None if it was asked nothing."""
    intake = row.intake
    if intake is None or not intake.questions or len(intake.answers) != len(intake.questions):
        return None
    answers: list[dict[str, Any]] = []
    for q, a in zip(intake.questions, intake.answers, strict=True):
        if a.choice is not None:
            answers.append({"question": q.text, "choice": q.options[a.choice].label})
        else:
            answers.append({"question": q.text, "other": a.other})
    return {"answers": answers}


def correction_line(row: Row) -> str:
    """The Sponsor's corrections at the review, as they're typed there."""
    parts = []
    for c in row.corrections or ():
        if c.key == ORIENTATION:
            continue
        reason = " ".join(c.reason.replace(";", ",").split())
        said = f"{c.key}={c.value}"
        if reason and reason != _NO_REASON:
            said += f" because {reason}"
        parts.append(said)
    return "; ".join(parts)


def again(row: Row, part_path: Path | None, printers: int = 1, part_found: bool = False) -> Again:
    """How to run `row`'s job again. `part_path` is the part's file, and `part_found` says it
    has the bytes the job printed (by sha256); else it's the path the log names, if any.
    `printers` is how many the inventory has (`--printer` is needed with more than one)."""
    notes: list[str] = []
    part = str(part_path) if part_path is not None else row.stl_name
    if not part_found:
        where = ("nothing in the parts folder or the job's folder has the bytes it printed "
                 f"(sha256 {row.stl_sha256[:12]}…)")
        if part_path is not None and part_path.is_file():
            notes.append(f"{part_path} has changed since this job, and {where}: check it's "
                         "the part you mean.")
        else:
            notes.append(f"{row.stl_name} wasn't found: {where}. Give its path in its place.")
    args = [part]
    material = row.material
    if material is not None:
        args += ["--nozzle", f"{material.nozzle_mm:g}",
                 "--filament", material.tuning or material.variety]
        if printers > 1:
            args += ["--printer", material.printer_id]
    else:
        nozzle = row.slicer_settings.get("nozzle_diameter", "").split(",")[0]
        filament = row.profiles.filament.name if row.profiles else row.filament
        args += ["--nozzle", nozzle or "?", "--filament", filament]
        notes.append("This print didn't take its nozzle and spool from the inventory; they're "
                     "from its slice.")
    if row.profiles is not None:
        args += ["--machine", row.profiles.machine.name, "--process", row.profiles.process.name]
    if row.orientation_source == AS_LAID:
        args.append("--as-laid")
    answers = answers_file(row)
    name = f"answers-{row.job_id[:8]}.json"
    command = "uv run python scripts/print_job.py " + shlex.join(args)
    if answers is not None:
        command += f" --answers {DOWNLOADS}/{shlex.quote(name)}"
        notes.append(f"Save the answers file first: it goes to {DOWNLOADS}/{name}. If the "
                     "browser names it otherwise, change the path to match.")
    elif row.intake is not None and row.intake.questions:
        notes.append("The answers don't match the questions kept, so there's no answers file: "
                     "answer them as they're asked.")
    for c in row.corrections or ():
        if c.key == ORIENTATION:
            notes.append(f"The pose was sent back at the review, from {c.preset} to {c.value}, "
                         f"because {c.reason}. Type orientation at the review to send it back "
                         "again.")
    taken = [c for c in (row.proposal.changes if row.proposal else ())
             if c.source is Source.READING]
    if taken:
        notes.append("Taken from the slice's reading, so take them again at the review (take) "
                     "if it proposes them: "
                     + "; ".join(f"{c.key}={c.value}" for c in taken) + ".")
    return Again(command=command, corrections=correction_line(row), answers=answers,
                 answers_name=name, notes=tuple(notes))
