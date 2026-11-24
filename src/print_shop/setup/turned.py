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
from print_shop.setup.proposal import pose_value
from print_shop.setup.reading import TURNED_SINCE

__all__ = ["ANY_POSE", "FOR_A_POSE", "Held", "as_posed", "held"]

#: The questions an answer to which holds however the part is turned.
ANY_POSE = frozenset({"purpose", "volume", "section"})
#: Which faces are seen: carried to the new pose, never set aside.
SEEN = "seen"
#: The settings that are for a pose, whichever question's option set them.
FOR_A_POSE = re.compile(
    r"support|bridge|overhang|ironing|seam|brim|raft|elefant|^top_|^bottom_|initial_layer"
)


@dataclass(frozen=True)
class Held:
    """The answers as a turned part's settings call and guard may read them, and each one set
    aside, in words."""

    intake: Intake
    aside: tuple[str, ...] = ()


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
        if question.feature not in ANY_POSE:
            aside.append(f"'{shown(question.text)}', you said '{label}'")
            continue
        sets = option.settings if option is not None else answer.sets
        keeps = {k: v for k, v in sets.items() if not FOR_A_POSE.search(k)}
        if keeps != sets:
            lost = ", ".join(f"{k}={v}" for k, v in sets.items() if k not in keeps)
            aside.append(f"{lost}, from '{label}'")
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
        tuple(aside))


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
        f"{TURNED_SINCE}: turned to {pose_value(now)} from {pose_value(asked_in)}, where you "
        "answered. These were said of the part as it lay when asked, so they aren't used now: "
        f"{'; '.join(kept.aside)}. Send it back with orientation, or say what you want with a "
        "correction"
    )
