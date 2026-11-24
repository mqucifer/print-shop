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
  one whose G-code doesn't say where it paused is left out, saying so (`_pause_again`). It's
  typed with what it was for (mqucifer/print-shop#218): an insert, or a swap with the spools the
  print was planned with. One of them that can't be named now (used up, or the first's tuning
  edited) is left to be asked (`from ?`), so the line still reads back;
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

**A plate of several parts runs again whole** (mqucifer/print-shop#109): from any of its parts'
rows, the command names every file.

The line doesn't arm the printer. Arming stays on the command's own line, as the runbook says.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from print_shop.core.state import pose_of, turn_words
from print_shop.inventory import Material, Spool
from print_shop.ledger import Row
from print_shop.orchestrator.graph import AS_LAID
from print_shop.plate import plate_job
from print_shop.setup import pauses
from print_shop.setup.context import SIDECAR
from print_shop.setup.intake import Answer, Question
from print_shop.setup.proposal import ORIENTATION, PAUSE, Change, Source, pose_value
from print_shop.setup.reading import AT_THE_BACK, turned_on_plate

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
            # What they were read back as and confirmed, which a replay sets again only while
            # the words still read so (mqucifer/print-shop#213).
            answers.append({"question": q.text, "other": a.other,
                            **({"sets": dict(a.sets)} if a.sets else {})})
    return {"answers": answers}


def _given_in(row: Row, question: Question, answer: Answer) -> dict[str, str]:
    """The pose an answer about faces was given in, which a replay carries it from. A row from
    before poses were kept is read in the pose it printed in (mqucifer/print-shop#248), less any
    turn on the plate, which turns no face to another (mqucifer/print-shop#257)."""
    if question.feature != "seen" or question.confirmed(answer):
        return {}
    return {"pose": pose_value(question.answered_in(answer) or pose_of(row.rotation_deg))}


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


def _purpose_again(
    row: Row, stood: Change, on_hand: dict[int, Spool] | None
) -> pauses.Pause | None:
    """What the pause was for, as it can be typed again: a spool that's used up, or a first
    whose tuning has been edited, is left for the review to ask. The other is still named."""
    purpose = pauses.as_printed(stood.pause, row.spools)
    if purpose is None or purpose.start is None or on_hand is None:
        return purpose
    start: Material | None = purpose.start
    to = purpose.to
    if to is not None and to.spool_id not in on_hand:
        to = None
    if purpose.start.spool_id not in on_hand or (
        on_hand[purpose.start.spool_id].tuning != purpose.start.tuning
    ):
        start = None
    return purpose.model_copy(update={"start": start, "to": to, "ask_first": start is None})


def correction_line(row: Row, on_hand: dict[int, Spool] | None = None) -> str:
    """The Sponsor's corrections at the review, as they're typed there. `on_hand` is the spools
    that can be named in it, by number."""
    parts = []
    stood = _standing_pause(row)
    for c in row.corrections or ():
        if c.key == ORIENTATION or _used_as_anothers(c) or c.said_no_to:
            continue
        key, value, reason = c.key, c.value, c.reason
        if c.key == PAUSE:
            height = _pause_again(row, c) if c is stood else None
            if height is None:
                continue
            key, value = pauses.to_type(height, _purpose_again(row, c, on_hand))
            reason = pauses.carried_reason(c)
        reason = " ".join(reason.replace(";", ",").split())
        said = f"{key}={value}"
        if reason and reason != _NO_REASON:
            said += f" because {reason}"
        parts.append(said)
    return "; ".join(parts)


def again(
    row: Row, part_path: Path | None, printers: int = 1, part_found: bool = False,
    plate_parts: tuple[tuple[Path, bool], ...] = (),
    plate_rows: tuple[Row, ...] = (),
    on_hand: dict[int, Spool] | None = None,
) -> Again:
    """How to run `row`'s job again. `part_path` is the part's file, and `part_found` says it
    has the bytes the job printed (by sha256); else it's the path the log names, if any.
    `printers` is how many the inventory has (`--printer` is needed with more than one).
    `plate_parts` are each of a plate's files the same way, and `plate_rows` each of its rows, as
    the whole plate is what's run again, the same from any part's page
    (mqucifer/print-shop#109)."""
    if row.sliced is not None:
        return _sliced_again(row, part_path, printers, part_found)
    notes: list[str] = []
    if row.plate is not None:
        args = []
        files = plate_parts or tuple((Path(p.path), False) for p in row.plate.parts)
        for n, (given, (path, found)) in enumerate(zip(row.plate.parts, files, strict=True), 1):
            args.append(str(path))
            if not found:
                notes += _not_found(given.name, given.sha256, path)
            if given.context and not path.with_name(path.stem + SIDECAR).is_file() and (
                str(path) != given.path or not Path(given.context).exists()
            ):
                notes.append(f"Part {n}, {given.name}, was read with the context at "
                             f"{given.context}, and it won't be found beside {path}: its fits, "
                             "seen faces and side would be lost. Put it back, or put its sidecar "
                             f"({path.stem + SIDECAR}) beside the file.")
        notes.append("It's the whole plate again. Each part's context is read beside its file "
                     "as given here, with no --context.")
    else:
        args = [str(part_path) if part_path is not None else row.stl_name]
        if not part_found:
            notes += _not_found(row.stl_name, row.stl_sha256, part_path)
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
    if row.plate.as_laid if row.plate is not None else row.orientation_source == AS_LAID:
        args.append("--as-laid")
    # The part's context is named, as the part may be found elsewhere, away from its sidecar or
    # its design run (mqucifer/print-shop#130).
    context = row.proposal.context_from if row.proposal is not None and row.plate is None else ""
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
    for each in plate_rows or (row,):
        whose, which = "The pose", ""
        if each.plate is not None:
            whose = f"Part {each.plate.n}, {each.stl_name}: the pose"
            which = f", then {each.plate.n} for the part,"
        for c in each.corrections or ():
            if c.said_no_to:
                what = (f"turn to {c.said_no_to}" if c.key == ORIENTATION
                        else f"{c.key}={c.said_no_to}")
                notes.append(f"You turned down the model's {what}, because {c.reason}. Say no to "
                             "it again at the review if it's proposed.")
            elif c.key == ORIENTATION:
                notes.append(f"{whose} was sent back at the review, from {c.preset} to "
                             f"{c.value}, because {c.reason}. Type orientation at the review"
                             f"{which} to send it back again.")
    # A plate is sliced with no turn on it (mqucifer/print-shop#109), so this is one part's.
    turned = turned_on_plate(row.reading, row.rotation_deg) if not plate_rows else 0.0
    if turned:
        notes.append(f"It was turned {turn_words(turned)}, on the plate to fit. It's offered "
                     "again only if it still doesn't fit as posed: type turn at the review to "
                     "take it.")
    elif (turned is None and row.reading is not None
          and row.reading.settings.get("seam_position") in AT_THE_BACK):
        # A row from before, read as it was sliced: back was whichever side Orca turned there.
        notes.append("Its seam was at the back, and Orca may have turned it on the plate, unsaid "
                     "then. Again prints it as posed, so the back may be another side than this "
                     "job's.")
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
        if copied not in (None, "none"):
            purpose = _purpose_again(row, stood, on_hand)
            then = pauses.as_printed(stood.pause, row.spools)
            if then is not None and purpose is not None and then.start is not None:
                for used, kept in ((then.start, purpose.start), (then.to, purpose.to)):
                    if used is None or kept is not None:
                        continue
                    gone = on_hand is None or used.spool_id not in on_hand
                    why = "isn't on hand now" if gone else "has had its tuning edited since"
                    notes.append(f"It printed with {used.named()}, which {why}, so the review "
                                 "asks for that spool.")
            if purpose is None:
                notes.append("This job didn't say what its pause was for, so the review asks.")
            elif purpose.start is not None and purpose.to is not None:
                if row.ended_with is not None and row.ended_with.spool_id != purpose.to.spool_id:
                    notes.append(f"You said it ended with {row.ended_with.named()}: the line is "
                                 "as it was planned.")
                here = on_hand.values() if on_hand else ()
                notes.append(f"It starts on {purpose.start.as_now(here).named()}, and swaps to "
                             f"{purpose.to.as_now(here).named()}: have the first loaded, and "
                             "recorded "
                             f"(`stock.py load {purpose.start.spool_id}`), before you approve.")
    taken = [c for c in ((*row.proposal.changes, *row.proposal.not_applied)
                         if row.proposal else ())
             if c.source is Source.READING]
    if taken:
        notes.append("Taken from the slice's reading, so take them again at the review (take) "
                     "if it proposes them: "
                     + "; ".join(f"{c.key}={c.value}" for c in taken) + ".")
    turn = next((c for found in (row.proposal.suggested if row.proposal else ())
                 for c in found.proposed
                 if c.key == ORIENTATION and ORIENTATION in found.taken
                 and c.value == pose_value(row.rotation_deg)), None)
    if turn is not None:
        notes.append(f"The model's turn to {turn.value} was taken at the review: take it again "
                     "there if it's proposed, or type orientation to send the part to it.")
    by_model = [c for c in ((*row.proposal.changes, *row.proposal.not_applied)
                            if row.proposal else ())
                if c.taken]
    if by_model:
        notes.append("Taken from the model's proposals, so take them again at the review, by "
                     "number, if it proposes them, or type them: "
                     + "; ".join(f"{c.key}={c.value}" for c in by_model) + ".")
    reused = [c for c in row.corrections or () if _used_as_anothers(c)]
    if reused:
        notes.append("Used from a past print, so use them again at the review (use): "
                     + "; ".join(f"{c.key}={c.value}" for c in reused) + ".")
    if row.ended_at is not None:
        # The line above runs it all again, with today's lessons and the model's questions.
        notes.append("To print this run again as it was, or with a small change, with nothing "
                     "asked and no model: uv run python scripts/print_job.py --again "
                     f"{plate_job(row.job_id)} (mqucifer/print-shop#216).")
    return Again(command=command, corrections=correction_line(row, on_hand), answers=answers,
                 answers_name=name, notes=tuple(notes))


def _not_found(name: str, sha256: str, path: Path | None) -> list[str]:
    """The note for a part whose bytes weren't found."""
    where = ("nothing in the parts folder or the job's folder has the bytes it printed "
             f"(sha256 {sha256[:12]}…)")
    if path is not None and path.is_file():
        return [f"{path} has changed since this job, and {where}: check it's the part you mean."]
    return [f"{name} wasn't found: {where}. Give its path in its place."]


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
