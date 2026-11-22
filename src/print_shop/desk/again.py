"""Run a past job again: its command line, the review's correction line and an answers file.

The Sponsor, 2026-11-15: "since im often re-running with the same settings, to be be able to see
and re-copy those". Three things, each copied from the page and never run by it:
- **the command line,** with the part's file, found by the sha256 the ledger keeps
  (`read.PartFinder`: the log's path, the parts folder, the job's folder), the nozzle and
  filament the print used, its machine and process profiles by name, `--as-laid` if it was
  kept as laid, `--context` naming the sidecar or design run its context was read from
  (mqucifer/print-shop#130), and `--answers` naming the answers file where the browser saves it;
- **the correction line,** typed at the review as it was then (`key=value because …; …`, read
  by `review.parse_answer`): the Sponsor's own corrections, in order. Of its pauses only the
  last, which stood, and never copied raw where it could mean another layer now: one from before
  mqucifer/print-shop#203 is given at the height that pauses where it did (`_older_pause`), and
  one whose G-code doesn't say where it paused is left out, saying so (`_pause_again`);
- **the answers file** for `--answers` (`intake.replay_asker`): each question by its text, each
  answer by its option's label or the words given, and what was confirmed from the part's notes
  by what they gave (mqucifer/print-shop#236), which replays only while they still say it.

What these can't carry is said beside them, never dropped silently: a pose sent back at the
review is chosen at the review again (`orientation`), and a change taken from the slice's
reading is taken again (`take`) when the slice proposes it. Neither is put in the correction
line, which would record them as the Sponsor's own corrections, and each correction is drawn as
a lesson. For the same reason, what was used from a past print and was an answer's, a lesson's or
the slice's there (mqucifer/print-shop#211) is used again (`use`), not typed.

**A hand-sliced job runs again by its id** (mqucifer/print-shop#164): `--again JOB_ID` sends the
file it printed, kept in its folder, as a new job marked as a reprint of it. There's no part to
find, nothing to answer and nothing to correct, so that one line is all there is.

The line doesn't arm the printer. Arming stays on the command's own line, as the runbook says.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from print_shop.ledger import Row
from print_shop.orchestrator.graph import AS_LAID
from print_shop.setup.intake import Answer, Question
from print_shop.setup.proposal import ORIENTATION, PAUSE, Change, Source, pose_value

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
        if q.confirmed(a):
            answers.append({"question": q.text, "notes": q.noted_as})
        elif a.choice is not None:
            answers.append({"question": q.text, "choice": q.options[a.choice].label,
                            # Faces are named in a pose: in another it's refused, never carried
                            # by eye (mqucifer/print-shop#248).
                            **_given_in(row, q, a)})
        else:
            answers.append({"question": q.text, "other": a.other})
    return {"answers": answers}


def _given_in(row: Row, question: Question, answer: Answer) -> dict[str, str]:
    """The pose an answer about faces was given in, which a replay carries it from. A row from
    before poses were kept is read in the pose it printed in (mqucifer/print-shop#248)."""
    if question.feature != "seen" or question.confirmed(answer):
        return {}
    return {"pose": pose_value(question.answered_in(answer) or row.rotation_deg)}


def _used_as_anothers(change: Change) -> bool:
    """Used from a past print, and not the Sponsor's own correction there."""
    return change.used is not None and change.used.by is not Source.SPONSOR


def _older_pause(row: Row) -> tuple[Change, str] | None:
    """The pause that stood in a job from before mqucifer/print-shop#203, and the height that
    pauses where its own G-code did: the top of the layer before the pause. Such a row's
    reading has no `after_z`, and its typed height was the first layer after the pause."""
    stood = _standing_pause(row)
    if stood is None or stood.value == "none" or row.reading is None:
        return None
    if len(row.reading.pauses) != 1:
        return None
    pause = row.reading.pauses[0]
    if pause.after_z is not None or not 2 <= pause.before_layer <= len(row.reading.layers):
        return None
    return stood, f"{row.reading.layers[pause.before_layer - 2].z:g}"


def _standing_pause(row: Row) -> Change | None:
    """The last pause correction: the one that stood."""
    return next((c for c in reversed(row.corrections or ()) if c.key == PAUSE), None)


def _pause_again(row: Row, stood: Change) -> str | None:
    """The height to type for the pause that stood, or None where the job's G-code doesn't say
    where it paused, so neither which layer nor which meaning of a height it was is known."""
    if stood.value == "none":
        return stood.value
    older = _older_pause(row)
    if older is not None:
        return older[1]
    pauses = row.reading.pauses if row.reading is not None else ()
    return stood.value if len(pauses) == 1 and pauses[0].after_z is not None else None


def correction_line(row: Row) -> str:
    """The Sponsor's corrections at the review, as they're typed there."""
    parts = []
    stood = _standing_pause(row)
    for c in row.corrections or ():
        if c.key == ORIENTATION or _used_as_anothers(c):
            continue
        value: str | None = c.value
        if c.key == PAUSE:
            value = _pause_again(row, c) if c is stood else None
            if value is None:
                continue
        reason = " ".join(c.reason.replace(";", ",").split())
        said = f"{c.key}={value}"
        if reason and reason != _NO_REASON:
            said += f" because {reason}"
        parts.append(said)
    return "; ".join(parts)


def again(row: Row, part_path: Path | None, printers: int = 1, part_found: bool = False) -> Again:
    """How to run `row`'s job again. `part_path` is the part's file, and `part_found` says it
    has the bytes the job printed (by sha256); else it's the path the log names, if any.
    `printers` is how many the inventory has (`--printer` is needed with more than one)."""
    if row.sliced is not None:
        return _sliced_again(row, part_path, printers, part_found)
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
    # The part's context is named, as the part may be found elsewhere, away from its sidecar or
    # its design run (mqucifer/print-shop#130).
    context = row.proposal.context_from if row.proposal is not None else ""
    if context:
        args += ["--context", context]
        if not Path(context).exists():
            notes.append(f"Its context, {context}, is gone: without it the part prints with no "
                         "fits, side or holds known. Give it again, or leave --context out.")
            if row.intake is not None and any(
                q.confirmed(a) for q, a in zip(row.intake.questions, row.intake.answers,
                                               strict=False)
            ):
                notes.append("Its answers file confirms what those notes gave, so it's refused "
                             "without them: answer the questions as they're asked instead.")
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
    stood = _standing_pause(row)
    if stood is not None:
        copied = _pause_again(row, stood)
        if copied is None:
            notes.append(f"{PAUSE}={stood.value} isn't copied: this job's G-code doesn't say "
                         "where it paused. If it's to pause, type the top of the layer to pause "
                         "after.")
        elif copied != stood.value:
            notes.append(f"{PAUSE}={stood.value} is copied as {PAUSE}={copied}, to pause where "
                         "this job did: a pause height is now the top of the layer it comes "
                         "after.")
    taken = [c for c in ((*row.proposal.changes, *row.proposal.not_applied)
                         if row.proposal else ())
             if c.source is Source.READING]
    if taken:
        notes.append("Taken from the slice's reading, so take them again at the review (take) "
                     "if it proposes them: "
                     + "; ".join(f"{c.key}={c.value}" for c in taken) + ".")
    reused = [c for c in row.corrections or () if _used_as_anothers(c)]
    if reused:
        notes.append("Used from a past print, so use them again at the review (use): "
                     + "; ".join(f"{c.key}={c.value}" for c in reused) + ".")
    return Again(command=command, corrections=correction_line(row), answers=answers,
                 answers_name=name, notes=tuple(notes))


def _sliced_again(row: Row, kept: Path | None, printers: int, kept_found: bool) -> Again:
    """A hand-sliced job again: `--again` and its id. `kept` is the file it printed, in its
    folder, and `kept_found` says it still holds the bytes that were sent."""
    args = ["--again", row.job_id]
    if printers > 1 and row.material is not None:
        args += ["--printer", row.material.printer_id]
    notes = [
        "It sends the file this job printed, kept in its folder, as a new job marked as a "
        "reprint of this one. Nothing is asked, proposed or sliced again.",
        "It's refused if what's loaded and fitted isn't the filament and nozzle the file names; "
        "add --loaded-ok to print it with what's loaded.",
    ]
    if not kept_found:
        where = f" ({kept})" if kept is not None else ""
        notes.append(f"The file this job printed isn't in its folder any more, or has changed"
                     f"{where}, so --again will refuse it: send the file again with --sliced.")
    return Again(command="uv run python scripts/print_job.py " + shlex.join(args),
                 corrections="", answers=None, answers_name="", notes=tuple(notes))
