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

from print_shop.setup import own_words
from print_shop.setup.layout import WIDTH, right, terminal_width, wrap

__all__ = [
    "DIFFERS_PROMPT",
    "NOTES_PROMPT",
    "WORDS_PROMPT",
    "Added",
    "Answer",
    "AnswersRefused",
    "Asker",
    "Estimate",
    "Intake",
    "OfPlate",
    "Option",
    "Question",
    "cli_asker",
    "drop_typed",
    "option_lines",
    "replay_asker",
    "settings_line",
    "take_words",
]

logger = logging.getLogger(__name__)

#: The one prompt that confirms what was read back from the part's notes.
NOTES_PROMPT = "notes> "
#: The prompt after an answer that differs from the notes: not the one that took option numbers.
DIFFERS_PROMPT = "yours or notes> "
#: The prompt that confirms what an answer in words was read back as (mqucifer/print-shop#213):
#: its own, so it isn't taken for the question asked again.
WORDS_PROMPT = "yes or no> "
#: The questions call's finish reason where the notes gave both and it wasn't waited for.
NOT_WAITED = "not waited"
#: How an option of faces names the pose: changed only as it's shown (`Question.shown`).
AS_IT_PRINTS = "as it prints"
#: How an option names the plate's face: told afterwards as the one that was.
ON_THE_PLATE = "the face on the plate"
#: The poses faces can be named in: `ingest.orientation.AXIS_ALIGNED`, which imports this
#: module's importers, so a test holds the two the same.
SQUARE_ON = (
    (0.0, 0.0, 0.0), (180.0, 0.0, 0.0), (90.0, 0.0, 0.0), (270.0, 0.0, 0.0), (0.0, 90.0, 0.0),
    (0.0, 270.0, 0.0),
)
#: What an option that sets nothing costs: Orca prices the answers together, at the review.
NOTHING = "sets nothing itself; the review shows Orca's estimate"
#: Where an option's number is, and where what it does is wrapped beneath it
#: (mqucifer/print-shop#266).
_NUMBER = "     "
_BENEATH = " " * 10
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
        """In one line, as the print desk shows it: what it sets comes from what it applies,
        never its text.

        The operator, 2026-10-25: an option's settings once said 1 wall while
        its text said 3 perimeters. So the settings line is written here from
        `settings`, and the model's words are only the impact and the why.
        """
        rec = f"  [recommended: {self.why}]" if self.recommended else ""
        return f"{self.label} ({settings_line(self.settings)}): {self.impact}; {self.cost()}{rec}"

    def reason(self) -> str:
        """Why what it sets is set, as the review says it."""
        return f"you chose '{self.label}': {self.impact}"

    def cost(self) -> str:
        """What it costs: Orca's estimate, or why there's none."""
        if self.estimate is not None:
            return self.estimate.line()
        if self.settings:
            return "no estimate"
        return NOTHING


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


class Said(Option):
    """An answer in the person's own words, read back and confirmed (mqucifer/print-shop#213):
    a decision as a chosen option is, so `Intake.chosen` gives it as one. Never in a question's
    options."""

    def reason(self) -> str:
        return f"you said '{self.label}', {self.impact}"


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
                f"{self._said_now(answer)}.")

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
        if answer.choice is not None:
            said = self.then(said)
        if answer.given_in is None:
            return said
        given = ",".join(f"{a:g}" for a in answer.given_in)
        return f"{said.replace('when asked', 'when answered')} (answered in the pose {given})"

    def label(self, answer: Answer) -> str:
        """An answer to it, in words. Faces confirmed from the notes are said as the file lays
        them: a label in this pose's words would name others once the part is turned."""
        if answer.choice is None:
            return answer.other
        if self.feature == "seen" and self.confirmed(answer) and self.beside:
            return self.beside
        return self.options[answer.choice].label

    def _said_now(self, answer: Answer) -> str:
        if answer.choice is None:
            return answer.other
        return self.as_answered(answer) if answer.given_in else self.shown(self.label(answer))

    def confirm(self) -> Answer:
        """What the notes gave, confirmed: what any asker returns for the person confirming it."""
        assert self.noted is not None
        return Answer(choice=self.noted, from_notes=True)

    def confirmed(self, answer: Answer) -> bool:
        """Whether the person confirmed it from the notes."""
        return self.noted is not None and answer.from_notes and answer.choice == self.noted

    def agrees(self, answer: Answer) -> bool:
        """Whether an answer is what the notes gave. One given in another pose never is by its
        words: there they name other faces."""
        return (self.noted is not None and answer.choice == self.noted
                and answer.given_in is None)

    def whose(self, answer: Answer) -> str:
        """Where the answer came from, where the notes gave one: "" is the person's answer to a
        question asked."""
        if self.noted is None:
            return ""
        if self.confirmed(answer):
            return "from the part's notes, confirmed"
        if self.agrees(answer):
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
    #: What their words were read back as, and the person confirmed (mqucifer/print-shop#213):
    #: a decision, as a chosen option's settings are. Empty: their words set nothing. Never
    #: inferred: only a confirmation, at the questions or recorded, sets it.
    sets: dict[str, str] = {}

    def in_words(self) -> str:
        """Their own words, with what they were confirmed to set."""
        if not self.sets:
            return self.other
        return f"{self.other} (read back as {settings_line(self.sets)}, and confirmed)"

    def said(self) -> Said | None:
        """Their words as the decision they were confirmed as, or None."""
        if self.choice is not None or not self.sets:
            return None
        return Said(label=self.other, settings=dict(self.sets),
                    impact=f"read back as {settings_line(self.sets)}, and confirmed")


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
        """Each question answered with an option, and that option. An answer in words that was
        read back and confirmed is one as well (`Said`, mqucifer/print-shop#213)."""
        chosen: list[tuple[Question, Option]] = []
        for q, a in zip(self.questions, self.answers, strict=True):
            if a.choice is not None:
                chosen.append((q, q.options[a.choice]))
            elif (said := a.said()) is not None:
                chosen.append((q, said))
        return chosen

    def could_set(self) -> frozenset[str]:
        """The settings this job's questions could set: every question shown, with the model's
        closer choices whose answer didn't stand (mqucifer/print-shop#208)."""
        return frozenset(k for q in (*self.questions, *self.offered)
                         for o in q.options for k in o.settings)

    def over_notes(self) -> list[tuple[Question, Answer]]:
        """The answers that replaced what the part's notes said (mqucifer/print-shop#236)."""
        return [(q, a) for q, a in zip(self.questions, self.answers, strict=False)
                if q.noted is not None and not q.agrees(a)]

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
            said = q.as_answered(a) if a.choice is not None else f"other: {a.in_words()}"
            whose = q.whose(a)
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


class OfPlate(tuple[Question, ...]):
    """Questions about a plate of several parts together (mqucifer/print-shop#109): shown as
    theirs, in the pose each lies in as it's asked. `words` are what's shown differently for
    them. The questions themselves, as they're recorded and replayed, are the same."""

    given: Sequence[Question]
    posed: str
    words: dict[str, str]

    def __new__(cls, questions: Sequence[Question], posed: str, words: dict[str, str]) -> OfPlate:
        made = super().__new__(cls, questions)
        made.given, made.posed, made.words = questions, posed, words
        return made


def _are_added(questions: Sequence[Question]) -> bool:
    given = questions.given if isinstance(questions, OfPlate) else questions
    return isinstance(given, Added)


def _numbered_from(questions: Sequence[Question]) -> int:
    given = questions.given if isinstance(questions, OfPlate) else questions
    return given.before + 1 if isinstance(given, Added) else 1


def _of_plate(
    questions: OfPlate, places: Sequence[int] | None = None, width: int = WIDTH,
) -> str:
    """A plate's questions, all or those at `places`, in its words: they're reworded before a
    question adds the pose it's asked in, as each part's is said in the heading."""
    def shown(text: str) -> str:
        for said, theirs in questions.words.items():
            text = text.replace(said, theirs)
        return text

    first = _numbered_from(questions)
    asked = ("Added by the model, from these parts' shapes" if _are_added(questions)
             else "A few questions about these parts")
    lines = wrap("", f"{asked}, {questions.posed}:", width)
    for i in range(len(questions)) if places is None else places:
        q = questions[i]
        posed = q.model_copy(update={"text": shown(q.text)})
        lines += wrap(f"  {first + i}. ", posed.asked(), width)
        lines += option_lines(q, width, shown=shown)
    return "\n".join(lines)


def render(
    questions: Sequence[Question], shown: Sequence[int] | None = None, width: int = WIDTH,
) -> str:
    """The questions to ask: all, or those at the places in `shown`, numbered as among all,
    laid out to `width` (mqucifer/print-shop#266)."""
    if isinstance(questions, OfPlate):
        return _of_plate(questions, shown, width)
    first = _numbered_from(questions)
    places = range(len(questions)) if shown is None else shown
    if isinstance(questions, Added):
        lines = ["Added by the model, from the part's shape:"]
    elif all(questions[i].feature in ("purpose", "seen") for i in places):
        lines = ["A few questions about this part:"]
    else:
        lines = ["A few questions about this part, from its shape:"]
    for i in places:
        lines += _listed(first + i, questions[i], width)
    return "\n".join(lines)


def _listed(n: int, question: Question, width: int = WIDTH, keep: str = "") -> list[str]:
    """A question with its options, and above it what the part's notes say on it. `keep` is
    what Enter keeps, as a row after the options, where Enter answers."""
    if question.noted is not None:
        lines = wrap("  ", f"The notes say {question.topic.lower()}: {question.notes_say()}",
                     width, "    ")
    elif question.beside:
        # Their free words, so in quotes.
        lines = wrap("  ", f'Its notes say: "{question.beside}"', width, "    ")
    else:
        lines = []
    lines += wrap(f"  {n}. ", question.asked(), width)
    return [*lines, *option_lines(question, width, keep=keep)]


def option_lines(
    question: Question, width: int = WIDTH, *, keep: str = "", keep_first: bool = False,
    shown: Callable[[str], str] | None = None,
) -> list[str]:
    """A question's options laid out to be read (mqucifer/print-shop#266): each its number and
    label on a line, with what it does wrapped beneath; the recommended one marked, its reason
    beneath; what every option shares said once, after them. `keep` is what Enter keeps, as a
    row of its own, first or after the options. `shown` rewords the text, as a plate's do.

    What each sets is written from its settings, never from its words (`Option.line`)."""
    say = shown or question.shown
    options = question.options
    sets = [settings_line(o.settings) for o in options]
    costs = [o.cost() for o in options]
    one_set, one_cost = len(set(sets)) == 1, len(set(costs)) == 1
    enter = wrap(f"{_NUMBER}Enter  ", keep, width) if keep else []
    lines = list(enter) if keep_first else []
    for n, (o, does, cost) in enumerate(zip(options, sets, costs, strict=True), 1):
        mark = "* " if o.recommended else "  "
        label = say(o.label) + ("" if one_set else f": {does}")
        tags = [t for t in ("recommended" if o.recommended else "",
                            "the notes" if n - 1 == question.noted else "",
                            "" if one_cost else cost) if t]
        lines += right(wrap(f"   {mark}{n}  ", label, width, " " * 8), " \u00b7 ".join(tags),
                       width)
        lines += wrap(_BENEATH, say(o.impact), width)
        if o.recommended and o.why:
            lines += wrap(f"{_BENEATH}recommended: ", say(o.why), width, _BENEATH)
    lines += wrap(f"{_NUMBER}o  ", "other: say it in your own words", width)
    shared = ([("keeps your presets" if sets[0] == "your presets" else f"sets {sets[0]}")]
              if one_set and sets else [])
    priced = ""
    if one_cost and costs:
        if costs[0] == NOTHING:
            shared.append(NOTHING)
        else:
            priced = _priced(costs[0])
    said = " ".join(x for x in (f"Each {' and '.join(shared)}." if shared else "", priced) if x)
    if said:
        lines += wrap(_NUMBER, said, width)
    if keep and not keep_first:
        lines += enter
    return lines


def _priced(cost: str) -> str:
    """One cost every option shares, as a sentence of its own: "Each" can't take "no estimate"
    or "Orca: 11 min, 3.1 g" (mqucifer/print-shop#266's shared line)."""
    if cost == "no estimate":
        return "None of these has an Orca estimate yet."
    return f"Orca's estimate for each: {cost.removeprefix('Orca: ')}."


def render_read_back(numbered: Sequence[tuple[int, Question]], width: int = WIDTH) -> str:
    """What the part's notes gave, on one card, to confirm at one prompt."""
    named = next((q.noted_from for _, q in numbered if q.noted_from), "")
    lines = wrap("", f"From the part's notes{f' ({named})' if named else ''}:", width)
    for n, q in numbered:
        lines += wrap(f"  {n}. ", f"{q.topic}: {q.read_back()}", width)
    return "\n".join([*lines, *wrap("", _how_to_confirm([n for n, _ in numbered]), width)])


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
    """Forget what was typed before something was shown: it can't have been an answer to it.

    Called once it has been written, so that what's typed while it's drawn goes too: what's
    still buffered is put on the screen first.
    """
    try:
        sys.stdout.flush()
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (OSError, ValueError, termios.error):
        logger.debug("couldn't drop what was typed ahead", exc_info=True)


def cli_asker(
    read: Callable[[str], str] = input, write: Callable[[str], None] = print,
    forget: Callable[[], None] = drop_typed,
    width: Callable[[], int] = terminal_width,
) -> Asker:
    """Ask on the command line, again until each answer is readable, laid out to `width`, the
    terminal's (mqucifer/print-shop#266).

    What the notes give is read back first, at one prompt, and only what they leave is asked
    (mqucifer/print-shop#236). What was typed before a read-back, or a question after one or
    added, was shown is forgotten, as each is written and before it's read: Enter confirms
    there, and mustn't confirm what nobody saw.
    """

    def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
        if not questions:
            return ()
        answers: dict[int, Answer] = {}
        first = _numbered_from(questions)

        def asked(places: list[int]) -> None:
            write(render(questions, places, width()))
            if noted or _are_added(questions):
                forget()  # shown only now, so what was typed while it drew is no answer to it
            for i in places:
                typed = read(f"{first + i}> ")
                while True:
                    parsed = parse(typed, questions[i])
                    if isinstance(parsed, Answer):
                        took = take_words(questions[i], parsed, f"{first + i}> ", read, write,
                                          forget, width())
                        if isinstance(took, str):
                            typed = took  # answered again
                            continue
                        answers[i] = took
                        break
                    if typed.strip().lower() == "go" and not _are_added(questions):
                        parsed += "; go works once these are answered"  # mqucifer/print-shop#208
                    write(f"  {parsed}")
                    typed = read(f"{first + i}> ")

        noted = [i for i, q in enumerate(questions) if q.noted is not None]
        rest = [i for i in range(len(questions)) if i not in noted]
        if noted:
            answers.update(_confirm(questions, noted, read, write, forget, width()))
        if rest:
            asked(rest)
        return tuple(answers[i] for i in range(len(questions)))

    return ask


def _confirm(
    questions: Sequence[Question], noted: list[int],
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
    width: int = WIDTH,
) -> dict[int, Answer]:
    """The one prompt over what was read back: Enter, or the numbers to change. Those changed
    are asked, and what isn't named stands confirmed, so nothing is asked twice."""
    first = _numbered_from(questions)
    by_key = {str(first + i): i for i in noted}
    write(render_read_back([(first + i, questions[i]) for i in noted], width))
    forget()
    while True:
        typed = read(NOTES_PROMPT).strip()
        named = typed.replace(",", " ").split()
        if not typed or (named and all(key in by_key for key in named)):
            break
        write("\n".join(wrap("  ", _how_to_confirm([first + i for i in noted]), width)))
    answers = {i: questions[i].confirm() for i in noted}
    for i in sorted({by_key[key] for key in named}):
        answers[i] = _changed(first + i, questions[i], read, write, forget, width)
    return answers


def _changed(
    n: int, question: Question,
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
    width: int = WIDTH,
) -> Answer:
    """One the person changes: asked alone, and an answer that differs names both before it's
    taken, when Enter takes it and `n` keeps the notes'."""
    kept = question.confirm()
    write("\n".join(_listed(n, question, width, keep="keep what the notes say")))
    forget()
    said = read(f"{n}> ")
    while True:
        if not said.strip():
            return kept
        parsed = parse(said, question)
        if isinstance(parsed, Answer):
            took = take_words(question, parsed, f"{n}> ", read, write, forget, width)
            if not isinstance(took, str):
                parsed = took
                break
            said = took  # answered again
            continue
        write(f"  {parsed}, or Enter to keep what the notes say")
        said = read(f"{n}> ")
    if parsed.choice == question.noted:
        return kept  # the option marked as the notes', picked here
    write("\n".join([*wrap("", question.differs(parsed), width),
                      "Enter takes your answer, or n keeps the notes."]))
    forget()
    while True:
        said = read(DIFFERS_PROMPT).strip().lower()
        if said in ("", "n"):
            return kept if said else parsed
        write("  Enter takes your answer, or n keeps the notes.")


def take_words(
    question: Question, answer: Answer, prompt: str,
    read: Callable[[str], str], write: Callable[[str], None], forget: Callable[[], None],
    width: int = WIDTH, beside: str = "",
) -> Answer | str:
    """An answer in words, read back as the decision it makes and confirmed or corrected
    (mqucifer/print-shop#213); any other answer as it is.

    Where the question's options set something, the words are read with no model
    (`own_words.read`). What they'd set is read back, "So supports off, and nothing turns them
    back on?", drawn before what was typed ahead is dropped, so an Enter typed with the words
    never confirms them. Enter or y makes it a decision, as a chosen option is. Words that set
    nothing, or a no, say so, and the person keeps them so with Enter or answers again at
    `prompt`, the question's own: the typed line is returned, to be read as an answer. Where
    the options set nothing, that's said, and the words stand.

    Words on what the part is for are a purpose, whatever its options set (print-shop-ux, on
    the model's closer choices): where they set nothing they're kept as what it's for, beside
    the purpose already given (`beside`), with no prompt. The proposal decides what the options
    would have set, and nothing is lost.
    """
    if answer.choice is not None:
        return answer
    keys = list(dict.fromkeys(k for o in question.options for k in o.settings))
    if not keys:
        kept = "what it's for" if question.feature == "purpose" else "your answer"
        write("\n".join(wrap("  ", f"Your words set nothing: they're kept as {kept}.", width)))
        return answer
    reading = own_words.read(answer.other, keys)
    aside = [f"Some of them set nothing: {why}." for why in reading.nothing if reading.sets]
    if reading.sets:
        yes = (f"Enter or y: yes, {_short(reading.sets)}, your decision. "
               "n: your words set nothing.")
        lines = [*wrap("  ", f"So {_read_as(reading.sets)}? From your words, "
                             f"'{answer.other}'.", width)]
        for line in (*aside, yes):
            lines += wrap("  ", line, width)
        write("\n".join(lines))
        forget()  # an Enter typed with the words never confirms what they were read as
        while True:
            said = read(WORDS_PROMPT).strip().lower()
            if said in ("", "y", "yes"):
                return answer.model_copy(update={"sets": dict(reading.sets)})
            if said in ("n", "no"):
                break
            write("\n".join(wrap("  ", yes, width)))
        why = "you said no to what they were read as"
    else:
        why = "; ".join(reading.nothing)
    decides = own_words.either(list(dict.fromkeys(own_words.NAMES.get(k, k) for k in keys)))
    if question.feature == "purpose":
        given = f", beside '{beside}'" if beside else ""
        write("\n".join(wrap("  ", f"Your words are kept as what it's for{given}. They set no "
                                  f"{decides}, so the proposal decides those.", width)))
        return answer
    write("\n".join([
        *wrap("  ", f"Your words set nothing: {why}. The proposal decides {decides}, as if this "
                    "weren't answered.", width),
        *wrap("  ", "Enter keeps them so, or answer again: a number, or o and your words.",
              width),
    ]))
    forget()
    again = read(prompt)
    return again if again.strip() else answer


def _read_as(sets: dict[str, str]) -> str:
    """What words were read as, as it's read back: supports off is said with what that means."""
    rest = {k: v for k, v in sets.items() if (k, v) != ("enable_support", "0")}
    said = [settings_line(rest)] if rest else []
    if sets.get("enable_support") == "0":
        said.append("supports off, and nothing turns them back on")
    return " and ".join(said)


def _short(sets: dict[str, str]) -> str:
    rest = {k: v for k, v in sets.items() if (k, v) != ("enable_support", "0")}
    return ", ".join([settings_line(rest)] * bool(rest)
                     + ["supports off"] * (sets.get("enable_support") == "0"))


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
    carried from it into whatever pose it's asked in now (mqucifer/print-shop#248). One given in
    another pose is never taken for the notes' own by its words, which name other faces there.
    Without a pose it's read in the pose it's asked in, and `write` is told which.

    An answer in words replays as words, setting nothing, unless it records what they were read
    back as and confirmed: ``{"question": "overhang", "other": "no support", "sets":
    {"enable_support": "0"}}`` (mqucifer/print-shop#213). That's a decision, used only while
    the words still read so; words that would set something and record nothing are told to
    `write`, and set nothing.

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
            unconfirmed = _unconfirmed(q, a)
            if unconfirmed:
                write(f"{path.name} answers '{q.text}' in words, '{a.other}', which read as "
                      f"{unconfirmed}, and records no \"sets\" confirming it, so they set "
                      "nothing and the proposal decides.")
            if q.noted is not None and not q.agrees(a):
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
    #: What its words were read back as and confirmed (mqucifer/print-shop#213).
    sets: dict[str, str] = {}

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
            if (x, y, z) not in SQUARE_ON:
                poses = "; ".join(",".join(f"{a:g}" for a in pose) for pose in SQUARE_ON)
                return f"its pose '{self.pose}' isn't one a part prints in: {poses}"
            if (x, y, z) != tuple(question.pose):
                given = (x, y, z)
        found = self._picked(question)
        if isinstance(found, Answer) and given is not None:
            return found.model_copy(update={"given_in": given})
        return found

    def _picked(self, question: Question) -> Answer | str:
        if self.sets and (self.choice is not None or not self.other.strip()):
            return "\"sets\" records what words were read as, and it has no words"
        if self.choice is None:
            if not self.other.strip():
                return "neither a choice nor words"
            if not self.sets:
                return Answer(other=self.other)
            keys = {k for o in question.options for k in o.settings}
            now = own_words.read(self.other, keys).sets
            if now != self.sets:
                reads = settings_line(now) if now else "nothing"
                return (f"its words '{self.other}' read as {reads} now, not the "
                        f"{settings_line(self.sets)} it records as confirmed")
            return Answer(other=self.other, sets=dict(self.sets))
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


def _unconfirmed(question: Question, answer: Answer) -> str:
    """What words with nothing confirmed would set, in words, or "" for none."""
    if answer.choice is not None or answer.sets:
        return ""
    keys = {k for o in question.options for k in o.settings}
    reading = own_words.read(answer.other, keys) if keys else None
    return settings_line(reading.sets) if reading is not None and reading.sets else ""


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()
