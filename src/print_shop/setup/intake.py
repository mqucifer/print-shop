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

They're answered on the command line by number, with free text always
allowed as "other", or replayed from a recorded answers file. The questions,
every option offered with its impacts, and the answers go in the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

__all__ = [
    "Answer",
    "Asker",
    "Estimate",
    "Intake",
    "Option",
    "Question",
    "cli_asker",
    "replay_asker",
]


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
    impact: str  # strength, surface, risk, in one line: the model's words
    recommended: bool = False
    why: str = ""  # why it's recommended
    estimate: Estimate | None = None  # Orca's, or None: "no estimate"

    def line(self) -> str:
        cost = self.estimate.line() if self.estimate else "no estimate"
        rec = f"  [recommended: {self.why}]" if self.recommended else ""
        return f"{self.label}: {self.impact}; {cost}{rec}"


class Question(BaseModel):
    """A question a feature (or the part's purpose) makes matter."""

    model_config = {"frozen": True}

    feature: str  # a feature id, or "purpose"
    text: str
    options: tuple[Option, ...]

    @property
    def recommended(self) -> int:
        return next(i for i, o in enumerate(self.options) if o.recommended)


class Answer(BaseModel):
    """The Sponsor's answer: an option by number, or their own words."""

    model_config = {"frozen": True}

    choice: int | None = None  # index into the question's options
    other: str = ""


class Intake(BaseModel):
    """The questions asked and the Sponsor's answers, as the job and ledger keep them."""

    model_config = {"frozen": True}

    questions: tuple[Question, ...] = ()
    answers: tuple[Answer, ...] = ()
    #: Why there are no questions, or which options were dropped out of bounds.
    note: str = ""
    #: The questions call's finish reason and tokens, reasoning included.
    call: dict[str, Any] = {}

    def chosen(self) -> list[tuple[Question, Option]]:
        """Each question answered with an option, and that option."""
        return [
            (q, q.options[a.choice])
            for q, a in zip(self.questions, self.answers, strict=True)
            if a.choice is not None
        ]

    def summary(self) -> str:
        if not self.questions:
            return "no questions: the part's features left nothing to ask"
        parts = []
        for q, a in zip(self.questions, self.answers, strict=True):
            said = q.options[a.choice].label if a.choice is not None else f"other: {a.other}"
            parts.append(f"{q.text} {said}")
        return "; ".join(parts)


#: How the questions are answered: on the command line, or from a recording.
Asker = Callable[[Sequence[Question]], tuple[Answer, ...]]


def render(questions: Sequence[Question]) -> str:
    lines = ["A few questions about this part, from its shape:"]
    for n, q in enumerate(questions, 1):
        lines.append(f"{n}. {q.text}")
        lines += [f"   {i}) {o.line()}" for i, o in enumerate(q.options, 1)]
        lines.append("   o) other: say it in your own words")
    return "\n".join(lines)


def parse(answer: str, question: Question) -> Answer | str:
    """An answer typed for `question`: a number, or "o" and some words."""
    text = answer.strip()
    if text.lower().startswith("o"):
        words = text[1:].strip(" :")
        return Answer(other=words) if words else "say it after the o, e.g. o it hangs a towel"
    if text.isdigit() and 1 <= int(text) <= len(question.options):
        return Answer(choice=int(text) - 1)
    return f"a number from 1 to {len(question.options)}, or o and your words"


def cli_asker(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print
) -> Asker:
    """Ask on the command line, again until each answer is readable."""

    def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
        if not questions:
            return ()
        write(render(questions))
        answers = []
        for n, q in enumerate(questions, 1):
            while True:
                parsed = parse(read(f"{n}> "), q)
                if isinstance(parsed, Answer):
                    answers.append(parsed)
                    break
                write(f"  {parsed}")
        return tuple(answers)

    return ask


def replay_asker(path: Path) -> Asker:
    """Answers recorded as JSON, `{"answers": [{"choice": 2}, {"other": "..."}]}`.

    Numbered from 1, as typed. A question beyond the recording takes the
    recommended option, and says so in the answer.

    Raises:
        ValueError: If the file isn't a list of answers.
    """
    try:
        rows = json.loads(path.read_text())["answers"]
        recorded = [
            Answer(choice=r["choice"] - 1) if "choice" in r else Answer(other=str(r["other"]))
            for r in rows
        ]
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"{path} is not a list of answers: {exc}") from exc

    def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
        answers = []
        for n, q in enumerate(questions):
            given = recorded[n] if n < len(recorded) else None
            if given is not None and (given.choice is None or given.choice < len(q.options)):
                answers.append(given)
            else:
                answers.append(Answer(choice=q.recommended,
                                      other="(not in the recording: the recommended option)"))
        return tuple(answers)

    return ask
