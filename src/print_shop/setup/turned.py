"""An answer that's for a pose, set aside once the part is turned from it (mqucifer/print-shop#258).

An answer is given of the part as it lay when asked. "No support", said of a part on its side,
is about that pose: turned onto an overhang, it would hold the support guard off over air.
`answered_kept` keeps an option's setting as the person's decision whatever the pose, so the turn
has to set it aside before the settings call and the guard read the answers.

After a turn, what's for a pose isn't used: a question on a feature a turn moves, and any
setting that's for a pose, whichever question's option, or words read back and confirmed
(mqucifer/print-shop#213), set it. The guard decides again in the new pose, and the settings
call isn't told the old answer. Turned back, it holds again. What the part is for holds in any
pose. Which faces are seen is carried to the new pose (`reading.carried`, mqucifer/print-shop#248),
not set aside. The record keeps every answer as given: only what the settings call and the guard
read is held to the pose.

It's one rule for a single part and a plate (mqucifer/print-shop#234's `plate_job.still_holds`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from print_shop.setup.intake import Intake, Question
from print_shop.setup.proposal import Proposal, Source, pose_value
from print_shop.setup.reading import TURNED_SINCE

__all__ = ["ANY_POSE", "FOR_A_POSE", "Held", "after_turn", "as_posed", "held"]

#: The questions an answer to which holds however the part is turned.
ANY_POSE = frozenset({"purpose", "volume", "section"})
#: Which faces are seen: carried to the new pose, never set aside.
SEEN = "seen"
#: The settings that are for a pose, whichever question's option set them.
FOR_A_POSE = re.compile(
    r"support|bridge|overhang|ironing|seam|brim|raft|elefant|^top_|^bottom_|initial_layer"
)
#: The note's first sentence is all Look first, again and Moved show (`screen._short`): it names
#: the answer, and fits Look first, again's line of 100 columns (print-shop-ux, 2026-11-24).
SUMMARY_CHARS = 100 - len("  ")
#: The fewest of the answer's characters it keeps, its "\u2026" included: "No support" whole. It's
#: shortened at a word where it can be.
LABEL_CHARS = 10
#: How the guard's supports end their reason where nothing decided them (`proposal.guard`).
_UNDECIDED = ", and nothing decided otherwise"


@dataclass(frozen=True)
class Held:
    """The answers as a turned part's settings call and guard may read them, and each one set
    aside, in words."""

    intake: Intake
    aside: tuple[str, ...] = ()
    #: Each answer set aside, as it was said, in order.
    labels: tuple[str, ...] = ()
    #: Each setting set aside, with the answer that set it: a later one's wins, as it does
    #: where they're applied (`proposal._answers`).
    set_by: tuple[tuple[str, str], ...] = ()


def held(
    intake: Intake, *, seen_kept: bool = True, shown: Callable[[str], str] = lambda text: text,
) -> Held:
    """The answers that still hold once the part is turned from the pose they were given in.

    `seen_kept` False leaves out the question of faces too, for a caller that carries it by
    itself (a plate's, `plate_job.seen_of`). `shown` gives a question's or an option's words as
    they were shown, as a plate rewords them.
    """
    questions: list[Question] = []
    answers = []
    aside: list[str] = []
    labels: list[str] = []
    set_by: dict[str, str] = {}
    for question, answer in zip(intake.questions, intake.answers, strict=True):
        option = question.options[answer.choice] if answer.choice is not None else None
        # As it was shown: "as it prints" would read as the pose it has been turned to. Their own
        # words are quoted as typed.
        label = shown(option.label) if option is not None else answer.other
        if question.feature == SEEN:
            if seen_kept:
                questions.append(question)
                answers.append(answer)
            continue
        sets = option.settings if option is not None else answer.sets
        if question.feature not in ANY_POSE:
            aside.append(f"'{shown(question.text)}', you said '{label}'")
            labels.append(label)
            set_by.update(dict.fromkeys(sets, label))
            continue
        keeps = {k: v for k, v in sets.items() if not FOR_A_POSE.search(k)}
        if keeps != sets:
            lost = ", ".join(f"{k}={v}" for k, v in sets.items() if k not in keeps)
            aside.append(f"{lost}, from '{label}'")
            labels.append(label)
            set_by.update(dict.fromkeys((k for k in sets if k not in keeps), label))
            if option is not None and answer.choice is not None:
                options = list(question.options)
                options[answer.choice] = option.model_copy(update={"settings": keeps})
                question = question.model_copy(update={"options": tuple(options)})
            else:
                answer = answer.model_copy(update={"sets": keeps})
        questions.append(question)
        answers.append(answer)
    return Held(
        intake.model_copy(update={"questions": tuple(questions), "answers": tuple(answers)}),
        tuple(aside), tuple(dict.fromkeys(labels)), tuple(set_by.items()))


def as_posed(
    intake: Intake, asked_in: tuple[float, float, float] | None,
    now: tuple[float, float, float],
) -> tuple[Intake, str]:
    """One part's answers as its settings call and guard may read them in the pose `now`, and
    the review's note on those set aside, or "".

    `asked_in` is the pose the questions were asked in. None: not known, so nothing is set aside,
    as before. Not turned, or turned back, every answer is read as it was given.
    """
    if asked_in is None or tuple(asked_in) == tuple(now):
        return intake, ""
    kept = held(intake)
    if not kept.aside:
        return kept.intake, ""
    return kept.intake, (
        f"{_summary(kept, asked_in, now)}. These were said of the part as it lay when asked, so "
        f"they aren't used now: {'; '.join(kept.aside)}. Send it back with orientation, or say "
        "what you want with a correction"
    )


def _summary(kept: Held, asked_in: tuple[float, float, float],
             now: tuple[float, float, float]) -> str:
    """The note's first sentence: the first answer set aside, how many more, and what decides
    in its place. Within `SUMMARY_CHARS`, the answer's words shortened if they must be, down to
    `LABEL_CHARS`: with supports and more than one answer it can't fit, so it wraps."""
    more = len(kept.labels) - 1
    was = f" and {more} more were" if more else " was"
    then = ("the guard decides supports" if "enable_support" in dict(kept.set_by)
            else "they aren't used" if more else "it isn't used")

    def said(label: str) -> str:
        return (f"{TURNED_SINCE}: '{label}'{was} for {pose_value(asked_in)}, so at "
                f"{pose_value(now)} {then}")

    # A colon past the head, ". " or "; " would end the short form early (`screen._short`).
    label = re.sub(r"(?<=\D)\. |; ", ", ", kept.labels[0])
    # Never shorter than its first words: past the line, it wraps rather than lose them.
    room = max(SUMMARY_CHARS - len(said("")), LABEL_CHARS)
    if len(label) > room:
        cut = label[: room - 1]
        label = (cut.rsplit(" ", 1)[0] if " " in cut.strip() else cut).rstrip(" ,") + "\u2026"
    return said(label)


def after_turn(
    proposal: Proposal, intake: Intake, asked_in: tuple[float, float, float] | None,
    now: tuple[float, float, float],
) -> Proposal:
    """The proposal for a part turned since its questions: the note first, and the guard's
    supports saying whose answer was for the pose it was asked in, where one was. Not turned,
    it's unchanged."""
    _, note = as_posed(intake, asked_in, now)
    if not note:
        return proposal
    assert asked_in is not None
    by = dict(held(intake).set_by).get("enable_support")

    def reason(text: str) -> str:
        if by is None or not text.endswith(_UNDECIDED):
            return text
        return (f"{text.removesuffix(_UNDECIDED)}; your '{by}' was for the part at "
                f"{pose_value(asked_in)}")

    return proposal.model_copy(update={
        "notes": (note, *proposal.notes),
        "changes": tuple(
            c.model_copy(update={"reason": reason(c.reason)})
            if c.key == "enable_support" and c.source == Source.GUARD else c
            for c in proposal.changes),
    })
