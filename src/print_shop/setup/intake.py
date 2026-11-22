"""The intake: a few questions about the part, each with options and what they cost.

The Sponsor, 2026-10-25, on mqucifer/print-shop#10: "I want intake to be
designed as a typical agent question to the sponsor. It takes in data and
starts to propose questions with limited options and impacts of those
decisions. Not just super open ended."

So the intake starts from the part's measured features and its presets
(`questions.py` proposes the questions), and asks only what they leave
undecided: at most four questions, each tied to the feature that makes it
matter (or to the part's purpose, which features often can't show), each with
two to four options. Every option says what it would set, its impact in one
line, and Orca's estimate of time and filament where a slice gave one. One
option is recommended, with why. A simple part may get no questions.

print-shop's own two, purpose and what's seen, need no model: the command asks them at once,
and the model's follow when they arrive (`waiting.py`, mqucifer/print-shop#208).

**What the part's notes say is read back, not asked** (mqucifer/print-shop#236). Such a question
carries the option the notes give (`Question.noted`), and one prompt confirms all of them: Enter,
or the numbers to change. An answer that differs names both before it's taken. The notes are
never a decision until confirmed, and only the confirming marks an answer as theirs
(`Answer.from_notes`): one that happens to say the same is the person's.

They're answered on the command line by number, with free text always
allowed as "other", or replayed from a recorded answers file. The questions,
every option offered with its impacts, and the answers go in the ledger.
"""

from __future__ import annotations

import json
import logging
import sys
import termios
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

__all__ = [
    "DIFFERS_PROMPT",
    "NOTES_PROMPT",
    "Added",
    "Answer",
    "AnswersRefused",
    "Asker",
    "Estimate",
    "Intake",
    "Option",
    "Question",
    "cli_asker",
    "drop_typed",
    "replay_asker",
    "settings_line",
]

logger = logging.getLogger(__name__)

#: The one prompt that confirms what was read back from the part's notes.
NOTES_PROMPT = "notes> "
#: The prompt after an answer that differs from the notes: not the one that took option numbers.
DIFFERS_PROMPT = "yours or notes> "
#: The questions call's finish reason where the notes gave both and it wasn't waited for.
NOT_WAITED = "not waited"
#: How an option of faces names the pose: changed only as it's shown (`Question.shown`).
AS_IT_PRINTS = "as it prints"
#: How an option names the plate's face: told afterwards as the one that was.
ON_THE_PLATE = "the face on the plate"
#: The most options a question is asked with. One past them is made from the notes.
ASKED_OPTIONS = 4


class Estimate(BaseModel):
    """Orca's estimate for one option, from a real slice."""

    model_config = {"frozen": True}

    print_time_s: int
    filament_g: float | None
    filament_cm3: float

    def line(self) -> str:
        grams = f"{self.filament_g:g} g" if self.filament_g else f"{self.filament_cm3:g} cm3"
        return f"Orca: {self.print_time_s // 60} min, {grams}"


class Option(BaseModel):
    """One answer to a question: what it sets, and what that costs."""

    model_config = {"frozen": True}

    label: str
    settings: dict[str, str]  # Orca keys and values, each inside its bound
    #: Its impact on strength, surface and risk, in the model's words. Never
    #: its values: those are shown from `settings`, by the code (`settings_line`).
    impact: str
    recommended: bool = False
    why: str = ""  # why it's recommended
    estimate: Estimate | None = None  # Orca's, or None: "no estimate"

    def line(self) -> str:
        """As the command shows it: what it sets comes from what it applies, never its text.

        The operator, 2026-10-25: an option's settings once said 1 wall while
        its text said 3 perimeters. So the settings line is written here from
        `settings`, and the model's words are only the impact and the why.
        """
        if self.estimate is not None:
            cost = self.estimate.line()
        elif self.settings:
            cost = "no estimate"
        else:
            cost = "sets nothing itself; the review shows Orca's estimate"
        rec = f"  [recommended: {self.why}]" if self.recommended else ""
        return f"{self.label} ({settings_line(self.settings)}): {self.impact}; {cost}{rec}"


_BRIMS = {"no_brim": "no brim", "auto_brim": "auto brim", "outer_only": "outer brim",
           "brim_ears": "brim ears"}


def settings_line(settings: dict[str, str]) -> str:
    """Settings in a printer person's words: "3 walls, 20% gyroid infill, 0.16 mm layers"."""
    if not settings:
        return "your presets"
    s = dict(settings)
    parts = []
    if "wall_loops" in s:
        parts.append(f"{s.pop('wall_loops')} walls")
    for key, what in (("top_shell_layers", "top layers"), ("bottom_shell_layers", "bottom layers")):
        if key in s:
            parts.append(f"{s.pop(key)} {what}")
    density, pattern = s.pop("sparse_infill_density", ""), s.pop("sparse_infill_pattern", "")
    if density or pattern:
        parts.append(" ".join(x for x in (density, pattern, "infill") if x))
    if "enable_support" in s:
        parts.append("supports on" if s.pop("enable_support") == "1" else "no supports")
    if "support_type" in s:
        parts.append(f"{s.pop('support_type').replace('(auto)', '')} supports")
    if "support_threshold_angle" in s:
        parts.append(f"supports from {s.pop('support_threshold_angle')}°")
    if "brim_type" in s:
        brim = s.pop("brim_type")
        parts.append(_BRIMS.get(brim, brim))
    if "layer_height" in s:
        parts.append(f"{s.pop('layer_height')} mm layers")
    for key, what in (("outer_wall_speed", "outer wall"), ("inner_wall_speed", "inner wall"),
                      ("sparse_infill_speed", "infill")):
        if key in s:
            parts.append(f"{what} {s.pop(key)} mm/s")
    if "nozzle_temperature" in s:
        parts.append(f"nozzle {s.pop('nozzle_temperature')} °C")
    if "fan_max_speed" in s:
        parts.append(f"fan up to {s.pop('fan_max_speed')}%")
    if "fan_min_speed" in s:
        parts.append(f"fan from {s.pop('fan_min_speed')}%")
    parts += [f"{k} {v}" for k, v in s.items()]
    return ", ".join(parts)


class Question(BaseModel):
    """A question a feature (or the part's purpose) makes matter."""

    model_config = {"frozen": True}

    feature: str  # a feature id, or "purpose"
    text: str
    options: tuple[Option, ...]
    #: What the part's notes say on it, in their words, shown beside it (mqucifer/print-shop#236).
    beside: str = ""
    #: The option the notes give: read back to be confirmed, where it would have been asked.
    noted: int | None = None
    #: What the notes gave, whatever the pose: a recorded confirmation names it (`replay_asker`).
    noted_as: str = ""
    #: The notes it was read from, by name, as the read-back heads it.
    noted_from: str = ""
    #: The pose a question of faces was asked, or read back, in, as a rotation, and in words
    #: (mqucifer/print-shop#248). None: from before, read in the pose it printed in.
    pose: tuple[float, float, float] | None = None
    posed: str = ""

    @property
    def recommended(self) -> int:
        return next(i for i, o in enumerate(self.options) if o.recommended)

    @property
    def topic(self) -> str:
        """What it's about, as a read-back names it."""
        return {"purpose": "For", "seen": "Seen"}.get(self.feature, self.text)

    def notes_say(self) -> str:
        """What the notes gave on it: what it's for in their words, with how it's loaded, and
        what's seen as the faces they name."""
        assert self.noted is not None
        label = self.options[self.noted].label
        if self.feature != "purpose":
            return self.beside or label
        words = f"{self.beside.rstrip('.')}. " if self.beside else ""
        return f"{words}{label[:1].upper()}{label[1:]}"

    def read_back(self) -> str:
        """The same, as it's read back to be confirmed: a purpose with the reading it has."""
        assert self.noted is not None
        if self.feature != "purpose":
            said = self.notes_say()
            if not self.pose or not any(self.pose):
                return said
            label = self.options[self.noted].label
            now = f", so {self.shown(label)}" if label != said else ""
            return f"{said} ({self.posed}{now})"
        return f"{self.notes_say()}: {self.options[self.noted].impact.split(';')[0]}"

    def differs(self, answer: Answer) -> str:
        """An answer and what the notes gave, both named (mqucifer/print-shop#236)."""
        assert self.noted is not None
        return (f"That differs from the notes. They say {self.topic.lower()}: "
                f"{self.shown(self.options[self.noted].label)}. You said: "
                f"{self.shown(self.label(answer)) if answer.choice is not None else answer.other}.")

    def answered_in(self, answer: Answer) -> tuple[float, float, float] | None:
        """The pose an answer about faces was given in. None: from before poses were kept, so
        it's read in the pose it printed in."""
        found = answer.given_in or self.pose
        return (found[0], found[1], found[2]) if found is not None else None

    def asked(self) -> str:
        """The question as it's shown: a question of faces names the pose it's asked in."""
        if not self.posed:
            return self.text
        return f"{self.text.rstrip('?')}, {self.posed}?"

    def shown(self, words: str) -> str:
        """An option's words as a question of faces shows them: the part can still be turned
        after it's asked, so "as it prints" is said as how it lies now. The label is unchanged."""
        return words.replace(AS_IT_PRINTS, "as it lies now") if self.pose is not None else words

    def then(self, words: str) -> str:
        """The same words told afterwards, when the part may have been turned since."""
        if self.pose is None:
            return words
        return words.replace(AS_IT_PRINTS, "as it lay when asked").replace(
            ON_THE_PLATE, f"{ON_THE_PLATE} when asked")

    def as_answered(self, answer: Answer) -> str:
        """An answer as it's told afterwards."""
        said = self.label(answer)
        return self.then(said) if answer.choice is not None else said

    def label(self, answer: Answer) -> str:
        """An answer to it, in words. Faces confirmed from the notes are said as the file lays
        them: a label in this pose's words would name others once the part is turned."""
        if answer.choice is None:
            return answer.other
        if self.feature == "seen" and self.confirmed(answer) and self.beside:
            return self.beside
        return self.options[answer.choice].label

    def confirm(self) -> Answer:
        """What the notes gave, confirmed: what any asker returns for the person confirming it."""
        assert self.noted is not None
        return Answer(choice=self.noted, from_notes=True)

    def confirmed(self, answer: Answer) -> bool:
        """Whether the person confirmed it from the notes."""
        return self.noted is not None and answer.from_notes and answer.choice == self.noted

    def whose(self, answer: Answer) -> str:
        """Where the answer came from, where the notes gave one: "" is the person's answer to a
        question asked."""
        if self.noted is None:
            return ""
        if self.confirmed(answer):
            return "from the part's notes, confirmed"
        if answer.choice == self.noted:
            return "your answer, as the notes say too"
        return f"your answer, in place of the notes' '{self.then(self.options[self.noted].label)}'"


class Answer(BaseModel):
    """The Sponsor's answer: an option by number, or their own words."""

    model_config = {"frozen": True}

    choice: int | None = None  # index into the question's options
    other: str = ""
    #: The person confirmed what the part's notes gave (`Question.confirm`). Never inferred.
    from_notes: bool = False
    #: The pose faces were named in, where that isn't the question's: a recorded answer's, which
    #: is carried from there (mqucifer/print-shop#248).
    given_in: tuple[float, float, float] | None = None


class Intake(BaseModel):
    """The questions asked and the Sponsor's answers, as the job and ledger keep them."""

    model_config = {"frozen": True}

    questions: tuple[Question, ...] = ()
    answers: tuple[Answer, ...] = ()
    #: Why there are no questions, or which options were dropped out of bounds.
    note: str = ""
    #: The questions call's finish reason and tokens, reasoning included. One with no answer
    #: says why: cut off, "timed out", "unavailable", or "went on" for the person going on
    #: without it.
    call: dict[str, Any] = {}
    #: A question that was shown and whose answer isn't the one that stands: the model's closer
    #: choices on purpose that the person kept their answer over, or print-shop's purpose
    #: question where they picked one of those (mqucifer/print-shop#208).
    offered: tuple[Question, ...] = ()
    #: Why the model's own questions aren't among these, as the review says it, or "".
    without: str = ""

    def chosen(self) -> list[tuple[Question, Option]]:
        """Each question answered with an option, and that option."""
        return [
            (q, q.options[a.choice])
            for q, a in zip(self.questions, self.answers, strict=True)
            if a.choice is not None
        ]

    def could_set(self) -> frozenset[str]:
        """The settings this job's questions could set: every question shown, with the model's
        closer choices whose answer didn't stand (mqucifer/print-shop#208)."""
        return frozenset(k for q in (*self.questions, *self.offered)
                         for o in q.options for k in o.settings)

    def over_notes(self) -> list[tuple[Question, Answer]]:
        """The answers that replaced what the part's notes said (mqucifer/print-shop#236)."""
        return [(q, a) for q, a in zip(self.questions, self.answers, strict=False)
                if q.noted is not None and a.choice != q.noted]

    @property
    def unwaited(self) -> bool:
        """The notes gave both, so the model's questions were closed unasked: nothing it could
        have asked counts as not asked (`offer`)."""
        return self.call.get("finish_reason") == NOT_WAITED

    def set_aside(self) -> dict[str, str]:
        """The person's answer, by what of the notes it stands in place of."""
        return {q.feature: q.as_answered(a) for q, a in self.over_notes()}

    def summary(self) -> str:
        if not self.questions:
            return "no questions: the part's features left nothing to ask"
        parts = []
        for q, a in zip(self.questions, self.answers, strict=True):
            said = q.as_answered(a) if a.choice is not None else f"other: {a.other}"
            whose = q.whose(a)
            if a.given_in is not None:
                said += f" (given in the pose {','.join(f'{v:g}' for v in a.given_in)})"
            parts.append(f"{q.asked()} {said}" + (f" ({whose})" if whose else ""))
        return "; ".join(parts)


#: How the questions are answered: on the command line, or from a recording.
Asker = Callable[[Sequence[Question]], tuple[Answer, ...]]


class Added(tuple[Question, ...]):
    """Questions added after `before` others were answered (mqucifer/print-shop#208): shown as
    added, and numbered on from those."""

    before: int

    def __new__(cls, questions: Sequence[Question], before: int) -> Added:
        made = super().__new__(cls, questions)
        made.before = before
        return made


def _numbered_from(questions: Sequence[Question]) -> int:
    return questions.before + 1 if isinstance(questions, Added) else 1


def render(questions: Sequence[Question], shown: Sequence[int] | None = None) -> str:
    """The questions to ask: all, or those at the places in `shown`, numbered as among all."""
    first = _numbered_from(questions)
    places = range(len(questions)) if shown is None else shown
    if isinstance(questions, Added):
        lines = ["Added by the model, from the part's shape:"]
    elif all(questions[i].feature in ("purpose", "seen") for i in places):
        lines = ["A few questions about this part:"]
    else:
        lines = ["A few questions about this part, from its shape:"]
    for i in places:
        lines += _listed(first + i, questions[i])
    return "\n".join(lines)


def _listed(n: int, question: Question) -> list[str]:
    """A question with its options, and above it what the part's notes say on it."""
    if question.noted is not None:
        lines = [f"The notes say {question.topic.lower()}: {question.notes_say()}"]
    elif question.beside:
        lines = [f'Its notes say: "{question.beside}"']  # their free words, so in quotes
    else:
        lines = []
    lines.append(f"{n}. {question.asked()}")
    for i, option in enumerate(question.options):
        theirs = "  <- the notes" if i == question.noted else ""
        lines.append(f"   {i + 1}) {question.shown(option.line())}{theirs}")
    lines.append("   o) other: say it in your own words")
    return lines


def render_read_back(numbered: Sequence[tuple[int, Question]]) -> str:
    """What the part's notes gave, on one card, to confirm at one prompt."""
    named = next((q.noted_from for _, q in numbered if q.noted_from), "")
    lines = [f"From the part's notes{f' ({named})' if named else ''}:"]
    lines += [f"  {n}. {q.topic}: {q.read_back()}" for n, q in numbered]
    return "\n".join([*lines, _how_to_confirm([n for n, _ in numbered])])


def _how_to_confirm(numbers: Sequence[int]) -> str:
    keys = [str(n) for n in numbers]
    both = f" ({' '.join(keys)} for both)" if len(keys) == 2 else ""
    return f"Enter if that's right, or {' or '.join(keys)} to change it{both}."


def parse(answer: str, question: Question) -> Answer | str:
    """An answer typed for `question`: a number, or "o" and some words."""
    text = answer.strip()
    if text.lower().startswith("o"):
        words = text[1:].strip(" :")
        return Answer(other=words) if words else "say it after the o, e.g. o it hangs a towel"
    if text.isdigit() and 1 <= int(text) <= len(question.options):
        return Answer(choice=int(text) - 1)
    return f"a number from 1 to {len(question.options)}, or o and your words"


def drop_typed() -> None:
    """Forget what was typed before something was shown: it can't have been an answer to it."""
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (OSError, ValueError, termios.error):
        logger.debug("couldn't drop what was typed ahead", exc_info=True)


def cli_asker(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print,
    forget: Callable[[], None] = drop_typed,
) -> Asker:
    """Ask on the command line, again until each answer is readable.

    What the notes give is read back first, at one prompt, and only what they leave is asked
    (mqucifer/print-shop#236). What was typed before a read-back, or a question after one, was
    shown is forgotten: Enter confirms there, and mustn't confirm what nobody saw.
    """

    def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
        if not questions:
            return ()
        answers: dict[int, Answer] = {}
        first = _numbered_from(questions)

        def asked(places: list[int]) -> None:
            write(render(questions, places))
            for i in places:
                while True:
                    typed = read(f"{first + i}> ")
                    parsed = parse(typed, questions[i])
                    if isinstance(parsed, Answer):
                        answers[i] = parsed
                        break
                    if typed.strip().lower() == "go" and not isinstance(questions, Added):
                        parsed += "; go works once these are answered"  # mqucifer/print-shop#208
                    write(f"  {parsed}")

        noted = [i for i, q in enumerate(questions) if q.noted is not None]
        rest = [i for i in range(len(questions)) if i not in noted]
        if noted:
            answers.update(_confirm(questions, noted, read, write, forget))
            if rest:
                forget()
        if rest:
            asked(rest)
        return tuple(answers[i] for i in range(len(questions)))

    return ask


def _confirm(
    questions: Sequence[Question], noted: list[int],
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
) -> dict[int, Answer]:
    """The one prompt over what was read back: Enter, or the numbers to change. Those changed
    are asked, and what isn't named stands confirmed, so nothing is asked twice."""
    first = _numbered_from(questions)
    by_key = {str(first + i): i for i in noted}
    forget()
    write(render_read_back([(first + i, questions[i]) for i in noted]))
    while True:
        typed = read(NOTES_PROMPT).strip()
        named = typed.replace(",", " ").split()
        if not typed or (named and all(key in by_key for key in named)):
            break
        write(f"  {_how_to_confirm([first + i for i in noted])}")
    answers = {i: questions[i].confirm() for i in noted}
    for i in sorted({by_key[key] for key in named}):
        answers[i] = _changed(first + i, questions[i], read, write, forget)
    return answers


def _changed(
    n: int, question: Question,
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
) -> Answer:
    """One the person changes: asked alone, and an answer that differs names both before it's
    taken, when Enter takes it and `n` keeps the notes'."""
    kept = question.confirm()
    forget()
    write("\n".join([*_listed(n, question), "   Enter) keep what the notes say"]))
    while True:
        said = read(f"{n}> ")
        if not said.strip():
            return kept
        parsed = parse(said, question)
        if isinstance(parsed, Answer):
            break
        write(f"  {parsed}, or Enter to keep what the notes say")
    if parsed.choice == question.noted:
        return kept  # the option marked as the notes', picked here
    forget()
    write(f"{question.differs(parsed)}\nEnter takes your answer, or n keeps the notes.")
    while True:
        said = read(DIFFERS_PROMPT).strip().lower()
        if said in ("", "n"):
            return kept if said else parsed
        write("  Enter takes your answer, or n keeps the notes.")


class AnswersRefused(ValueError):
    """A recorded answers file that doesn't answer the questions asked.

    `only_more`: its one fault is answering questions not among these, which the model's may be.
    """

    def __init__(self, said: str, only_more: bool = False) -> None:
        super().__init__(said)
        self.only_more = only_more


def replay_asker(path: Path, write: Callable[[str], None] = print) -> Asker:
    """Answers recorded as JSON, each naming the question it answers.

    ``{"answers": [{"question": "purpose", "choice": 2},
    {"question": "<a question's text>", "choice": "<an option's label>"},
    {"question": "overhang", "other": "..."}]}``

    A question is named by its text, or by its feature id ("purpose",
    "overhang") when no other question asked has that feature. A choice is an
    option's number, from 1 as typed, or its label. The file must answer
    every question asked and nothing else: a file written for other questions
    is refused, saying which, never replayed by position (mqucifer/print-shop#107:
    one written for one set of questions answered another wrongly, silently).

    An answer about faces may name the pose it was given in (``"pose": "180,0,0"``), and is
    carried from it into whatever pose it's asked in now (mqucifer/print-shop#248). Beside faces
    the notes give, it's refused in another, as which it agrees with can't be told by its words.
    Without a pose it's read in the pose it's asked in, and `write` is told which.

    What the notes gave replays as ``{"question": "seen", "notes": "top, bottom"}``: confirmed,
    if the notes still say it (mqucifer/print-shop#236). Nothing of theirs is confirmed that the
    file doesn't name. An answer in their place replays as any answer does, and `write` is told
    both before it's used.

    Raises:
        ValueError: If the file isn't a list of answers, each naming its question.
            The asker itself raises `AnswersRefused` when they don't match.
    """
    try:
        rows = json.loads(path.read_text())["answers"]
        recorded = [_Recorded.model_validate(r) for r in rows]
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(
            f"{path} is not a list of answers, each naming its question "
            f'({{"answers": [{{"question": "purpose", "choice": 2}}]}}): {exc}'
        ) from exc

    def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
        answers: list[Answer | None] = [None] * len(questions)
        problems = []
        unknown = 0
        for r in recorded:
            matched = r.matches(questions)
            if len(matched) != 1:
                unknown += not matched
                problems.append(f"'{r.question}' names {'no' if not matched else 'more than one'} "
                                "question asked")
                continue
            n = matched[0]
            if answers[n] is not None:
                problems.append(f"question {n + 1} is answered twice")
                continue
            given = r.answer(questions[n])
            if isinstance(given, str):
                problems.append(f"question {n + 1}: {given}")
                continue
            answers[n] = given
        problems += [f"question {n + 1} has no answer in the file"
                     for n, a in enumerate(answers) if a is None and questions]
        if problems:
            raise AnswersRefused(
                f"{path} doesn't answer these questions, so none of it is used: "
                + "; ".join(problems) + ".\nThe questions asked, each with its feature:\n"
                + "\n".join(f"{n}. [{q.feature}] {q.asked()}\n"
                             + "\n".join(f"   {i}) {q.shown(o.label)}"
                                         for i, o in enumerate(q.options, 1))
                             + (f'\n   the notes give it: "notes": "{q.noted_as}" confirms that'
                                if q.noted is not None else "")
                             for n, q in enumerate(questions, 1)),
                only_more=unknown == len(problems),
            )
        found = tuple(a for a in answers if a is not None)
        for r in recorded:
            for n in r.matches(questions):
                q, a = questions[n], found[n]
                if q.pose is not None and not r.pose and not r.notes and a.choice is not None:
                    write(f"{path.name} names no pose for which faces are seen, so it's read "
                          f"{q.posed}, as it's asked now.")
        for q, a in zip(questions, found, strict=False):
            if q.noted is not None and a.choice != q.noted:
                write(f"{q.differs(a)} That's from {path.name}, and it's used in their place.")
        return found

    return ask


class _Recorded(BaseModel):
    """One recorded answer: the question it answers, and an option or the Sponsor's words."""

    model_config = {"frozen": True, "extra": "forbid"}

    question: str
    choice: int | str | None = None
    other: str = ""
    notes: str = ""  # what it confirms the notes gave
    pose: str = ""  # the pose an answer about faces was given in

    def matches(self, questions: Sequence[Question]) -> list[int]:
        said = _plain(self.question)
        by_text = [n for n, q in enumerate(questions)
                   if said in (_plain(q.text), _plain(q.asked()))]
        if by_text:
            return by_text
        return [n for n, q in enumerate(questions) if q.feature == self.question.strip()]

    def answer(self, question: Question) -> Answer | str:
        if self.notes.strip():
            if self.choice is not None or self.other.strip():
                return "it's either what the notes gave or an answer, not both"
            if question.noted is None:
                return f"it confirms the notes' '{self.notes}', and the notes don't give one now"
            if _plain(self.notes) != _plain(question.noted_as):
                return (f"it confirms the notes' '{self.notes}', and they say "
                        f"'{question.noted_as}' now")
            return question.confirm()
        given: tuple[float, float, float] | None = None
        if self.pose and question.pose is not None:
            try:
                x, y, z = (float(part) % 360 for part in self.pose.split(","))
            except ValueError:
                return f"its pose '{self.pose}' isn't three angles, such as 180,0,0"
            if (x, y, z) != tuple(question.pose):
                given = (x, y, z)
                if question.noted is not None:
                    here = ",".join(f"{a:g}" for a in question.pose)
                    return (f"it was answered in the pose {self.pose}, and it's read back in "
                            f"{here} now, where those words name other faces")
        found = self._picked(question)
        if isinstance(found, Answer) and given is not None:
            return found.model_copy(update={"given_in": given})
        return found

    def _picked(self, question: Question) -> Answer | str:
        if self.choice is None:
            return Answer(other=self.other) if self.other.strip() else "neither a choice nor words"
        if isinstance(self.choice, int):
            if self.choice > ASKED_OPTIONS and self.choice - 1 == question.noted:
                return (f"choice {self.choice} is whatever the notes say now, so a number can't "
                        f'name it: "notes": "{question.noted_as}" confirms it')
            if 1 <= self.choice <= len(question.options):
                return Answer(choice=self.choice - 1)
            return f"choice {self.choice} isn't one of its {len(question.options)} options"
        wanted = _plain(self.choice)
        for at, option in enumerate(question.options):
            if wanted in (_plain(option.label), _plain(question.shown(option.label))):
                return Answer(choice=at)
        return f"'{self.choice}' isn't one of its options"


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()
