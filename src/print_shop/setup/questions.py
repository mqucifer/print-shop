"""The intake's questions, proposed from the part's features by one model call.

mqucifer/print-shop#10, the Sponsor's ask of 2026-10-25: questions with
limited options and their impacts, not open-ended ones. The model reads the
part's features, its presets, and the print-settings skill's practice and
index (SKILL.md), and proposes what those leave undecided:

- at most 4 questions, each tied to a feature id, or to "purpose" when the
  features can't show what the part is for. When none of the model's covers
  purpose, print-shop asks it first, with `PURPOSE` (the Sponsor's choice on
  mqucifer/print-shop#10), and keeps the model's first three;
- which of its faces will be seen, asked by print-shop with `SEEN`, last: the
  slice's reading judges ironing and the seam by it (mqucifer/print-shop#105). Which face
  is seen comes from the person, never from which face is on top;
- 2 to 4 options each. An option carries the settings it implies, each
  inside its bound (`bounds.check`): an option out of bounds is dropped, and
  a question left with fewer than 2 options is dropped. Exactly one option is
  recommended, with why;
- an option's words and its settings agree on supports (mqucifer/print-shop#124): one whose
  label says no support carries `enable_support=0`, added if the model left it out, so the
  answer is a decision the support guard keeps and the review shows where it came from. One
  that says no support and sets supports on is dropped;
- each option's impact on strength, surface and risk in one line, in the
  model's words, which never restate its values: the settings line shown
  beside it is written from the settings it applies (`settings_line`). Its
  time and filament are never the model's: `estimate` slices the part with
  that option's settings, and an option Orca couldn't slice says "no
  estimate".

**What the part's notes say isn't asked** (mqucifer/print-shop#236, the Sponsor's change to
their choice on mqucifer/print-shop#10). Purpose is always confirmed: asked when the notes don't
say, read back when they do, and so is what's seen (`stock`). The model is given the whole
context and asks only what it leaves open. Free text is shown beside the purpose question and
never guessed into one.

A part whose features leave nothing to decide gets only print-shop's two. A failed, cut-off
or timed-out call gives those two as well, saying which it was, and the proposal goes on. Those
two need no model, so the command asks them while the model works (`waiting.py`,
mqucifer/print-shop#208).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from print_shop.expert.client import CutOff, TimedOut
from print_shop.setup.bounds import SETTINGS, check
from print_shop.setup.context import (
    AS_THE_FILE_LIES,
    LOADS,
    PartContext,
    Seen,
    plain_line,
    pose_for,
    printed,
)
from print_shop.setup.intake import Estimate, Option, Question
from print_shop.setup.model import SHORT, TEXT, Ask, context_lines, setting_item
from print_shop.setup.proposal import PartFacts, pose_value
from print_shop.setup.skill import SKILL_DIR

__all__ = [
    "ANSWER_CHARS", "MAX_QUESTIONS", "SEEN", "SEEN_FACES", "STOCK", "Asked", "estimate",
    "only_stock", "propose_questions", "says_no_support", "schema", "stock",
]

MAX_QUESTIONS = 4
#: The most characters the model's answer may be: what four questions need, from the recorded
#: calls of 2026-11-15 to 22 (mqucifer/print-shop#208). The most an option took, its share of
#: the question and whitespace included, was 865 characters, leaving out one answer padded with
#: a run of 1,725 spaces; four questions of four options are 13,840. The largest answer that
#: stopped by itself was 4,221.
ANSWER_CHARS = 14_000
#: Slices for the options' estimates, at most, per print: a slice of a small
#: part takes a second or two, a large one longer.
MAX_SLICES = 12

_SYSTEM = (
    "You set up FDM prints in OrcaSlicer for a person who tunes every setting to the part's "
    "design and purpose. Before proposing settings, ask them the few questions this part's "
    "features leave undecided, the way a careful colleague would: at most 4, each tied to "
    "the feature that makes it matter, or to the part's purpose when its shape can't show "
    "what it's for (ask that first). Each question has 2 to 4 concrete options; each option "
    "lists the settings it implies, one line on its impact (strength, surface, risk) that "
    "never restates a setting or a number (the settings are shown beside it), and exactly "
    "one is recommended, with why, pointing at the part. An option that decides supports, "
    "either way, sets enable_support. Never ask what the features "
    "already answer, and never ask open-ended questions. A simple part may need none. Don't "
    "state times or filament: those come from the slicer. Don't ask which faces will be "
    "seen: print-shop asks that itself.\n\n"
)


#: What the part is for, asked by print-shop whenever the model's questions
#: don't (the Sponsor, 2026-10-25, on mqucifer/print-shop#10: "A load-bearing
#: part can't print with the presets' strength just because the model didn't
#: think to ask"). Its options set nothing: the proposal reads the answer and
#: decides, so it can't stand in for per-part reasoning.
PURPOSE = Question(
    feature="purpose",
    text="What is this part for? Its shape can't say.",
    options=(
        Option(label="decoration or display", settings={},
               impact="nothing rests on it: strength stays as your presets have it, and the "
                      "proposal looks at the surface"),
        Option(label="handled, clipped or lightly loaded", settings={}, recommended=True,
               why="when the shape can't say, a part that turns out decorative still prints "
                   "well this way, while a weak functional part costs a print",
               impact="the proposal strengthens its thin load paths a little; a little more "
                      "time and filament"),
        Option(label="carries a real load", settings={},
               impact="the proposal strengthens the load path; noticeably more time and "
                      "filament, and say the load in your own words with o"),
        Option(label="a prototype or test fit", settings={},
               impact="kept quick and cheap: your presets, unless a feature needs a change"),
    ),
)


assert tuple(o.label for o in PURPOSE.options) == LOADS

#: Which faces are seen, by option label: (walls, the top as it prints, the face on the plate).
SEEN_FACES: dict[str, tuple[bool, bool, bool]] = {
    "the walls and the top as it prints": (True, True, False),
    "the walls; the top as it prints is hidden": (True, False, False),
    "the top as it prints; the walls are hidden": (False, True, False),
    "only the face on the plate": (False, False, True),
}

#: Which faces will be seen, asked last by print-shop whenever the model's questions don't (the
#: Sponsor, 2026-11-13, on mqucifer/print-shop#105: which face is seen comes from the intake,
#: not from which face is on top). Its options set nothing: the slice's reading uses the answer
#: to judge ironing and the seam, and proposes at the review.
SEEN = Question(
    feature="seen",
    text="Which faces of this part will be seen once it's in use?",
    options=(
        Option(label="the walls and the top as it prints", settings={}, recommended=True,
               why="when it can't be said, judging every face as seen costs a little time, "
                   "while a hidden flaw on a face that shows costs the print",
               impact="the review keeps ironing for the top and points out a seam on the walls"),
        Option(label="the walls; the top as it prints is hidden", settings={},
               impact="the review points out ironing on the hidden top, and a seam on the walls"),
        Option(label="the top as it prints; the walls are hidden", settings={},
               impact="the review keeps ironing for the top and lets the seam be"),
        Option(label="only the face on the plate", settings={},
               impact="the review points out any ironing, which smooths only faces that "
                      "end up hidden"),
    ),
)
assert set(SEEN_FACES) == {o.label for o in SEEN.options}
#: The answers that leave the face on the plate unsaid. All but "only the face on the plate"
#: name two of the three, and none of them can say that face is seen as well. So it's never
#: judged hidden by one, wherever a turn puts it (mqucifer/print-shop#248).
PLATE_UNSAID = frozenset(label for label, (_, _, plate) in SEEN_FACES.items() if not plate)

#: The questions that need no model, shown at once (mqucifer/print-shop#208).
STOCK = (PURPOSE, SEEN)

#: print-shop's two as they're shown about a plate of several parts (mqucifer/print-shop#109).
#: A part can still be turned after they're asked, so "as it prints" is said as how it lies now.
PLATE_WORDS = {
    PURPOSE.text: "What are these parts for? Their shapes can't say.",
    SEEN.text: "Which faces of these parts will be seen once they're in use?",
    "as it prints": "as it lies now",
}


def stock(
    context: PartContext | None, rotation_deg: tuple[float, float, float],
) -> tuple[Question, Question]:
    """print-shop's two for this part in this pose: each read back where its notes say, asked
    where they don't (mqucifer/print-shop#236). With no notes they're `STOCK`'s. The question
    of faces always has the pose it's asked in, and names it (mqucifer/print-shop#248)."""
    posed = {"pose": tuple(rotation_deg), "posed": pose_words(context, rotation_deg)}
    if context is None or not context.found:
        return PURPOSE, SEEN.model_copy(update=posed)
    named = plain_line(Path(context.path).name) if context.path else ""
    faces = _seen_of(context.seen, rotation_deg, named)
    return _purpose_of(context, named), faces.model_copy(update=posed)


#: The side down, as a question names a part laid on it.
_SIDE_DOWN = {"+z": "top", "-z": "bottom", "-x": "left side", "+x": "right side", "-y": "front",
              "+y": "back"}


def pose_words(context: PartContext | None, rotation_deg: tuple[float, float, float]) -> str:
    """A pose, as a question of faces names it."""
    if tuple(rotation_deg) == AS_THE_FILE_LIES:
        return "as the file lays it"
    if context is not None and context.down and pose_for(context.down) == tuple(rotation_deg):
        says = "its notes say" if context.down_from == "the sidecar" else "its design run says"
        return f"laid {_SIDE_DOWN[context.down]}-down, as {says}"
    return f"turned {pose_value(rotation_deg)}"


def _purpose_of(context: PartContext, named: str) -> Question:
    if context.loaded:
        return PURPOSE.model_copy(update={
            "beside": context.purpose, "noted": LOADS.index(context.loaded),
            # Its words as well: a confirmation is of both.
            "noted_as": ": ".join(w for w in (context.purpose, context.loaded) if w),
            "noted_from": named,
        })
    # Words alone, and `about`, stand beside the question: nothing is picked from them.
    words = "; ".join(w for w in (context.purpose, context.about) if w)
    return PURPOSE.model_copy(update={"beside": words}) if words else PURPOSE


def _seen_of(
    seen: Seen | None, rotation_deg: tuple[float, float, float], named: str,
) -> Question:
    """What's seen, with the notes' faces as an option: one of `SEEN`'s where they're the same,
    else one of their own, so that any combination can be read back."""
    if seen is None:
        return SEEN
    beside = f"{seen.in_words()}, as the file lays it"
    now = printed(seen, rotation_deg)
    label = _faces_label(now) if now is not None else beside
    options = SEEN.options
    labels = [o.label for o in options]
    if label not in labels:
        options += (Option(label=label, settings={}, impact="as the part's notes say"),)
        labels.append(label)
    return SEEN.model_copy(update={
        "options": options, "beside": beside,
        "noted": labels.index(label), "noted_as": seen.faces(), "noted_from": named,
    })


def _faces_label(seen: Seen) -> str:
    """Faces as they print, in the question's own words where an option says them."""
    faces = (seen.walls, seen.top, seen.bottom)
    said = next((label for label, each in SEEN_FACES.items() if each == faces), "")
    if said and not seen.part_walls:
        return said
    names = ("part of the walls" if seen.part_walls else "the walls",
             "the top as it prints", "the face on the plate")
    *most, last = [n for n, shown in zip(names, faces, strict=True) if shown] or [
        "none of its faces"]
    return f"{', '.join(most)} and {last}" if most else last


def only_stock(from_notes: bool) -> str:
    """What's asked without the model's questions, as the intake's note opens."""
    return ("none of the model's questions asked" if from_notes
            else "only purpose and what's seen asked")


class Asked:
    """What the questions step produced: the questions, and why there are none, if none."""

    def __init__(self, questions: tuple[Question, ...], note: str = "",
                 call: dict[str, Any] | None = None, failed: str = "") -> None:
        self.questions = questions
        self.note = note
        self.call = call or {}
        #: How the model's call failed, in words that a time follows ("timed out"), or "" if it
        #: answered.
        self.failed = failed

    @property
    def closer(self) -> Question | None:
        """The model's own purpose question: closer choices, once purpose has been answered."""
        return next((q for q in self.questions if q.feature == "purpose" and q != PURPOSE), None)

    @property
    def added(self) -> tuple[Question, ...]:
        """The model's other questions, added after print-shop's two."""
        return self._others()[: MAX_QUESTIONS - len(STOCK)]

    @property
    def over(self) -> tuple[Question, ...]:
        """The model's questions there's no room for, with four at most."""
        return self._others()[MAX_QUESTIONS - len(STOCK):]

    def _others(self) -> tuple[Question, ...]:
        closer = self.closer
        return tuple(q for q in self.questions if q not in STOCK and q is not closer)


def schema(feature_ids: list[str], purpose: bool = True) -> dict[str, Any]:
    """The reply's shape, enforced by the server. `purpose` False: the notes give it."""
    option = {
        "type": "object", "additionalProperties": False,
        "required": ["label", "settings", "impact", "recommended", "why"],
        "properties": {
            "label": SHORT,
            "settings": {"type": "array", "maxItems": 6, "items": setting_item(sorted(SETTINGS))},
            "impact": TEXT,
            "recommended": {"type": "boolean"},
            "why": TEXT,
        },
    }
    question = {
        "type": "object", "additionalProperties": False,
        "required": ["feature", "text", "options"],
        "properties": {
            "feature": {"type": "string",
                        "enum": [*feature_ids, *(["purpose"] if purpose else [])]},
            "text": TEXT,
            "options": {"type": "array", "minItems": 2, "maxItems": 4, "items": option},
        },
    }
    return {
        "name": "intake_questions",
        "schema": {
            "type": "object", "additionalProperties": False, "required": ["questions"],
            "properties": {"questions": {"type": "array", "maxItems": MAX_QUESTIONS,
                                         "items": question}},
        },
    }


def propose_questions(facts: PartFacts, ask: Ask) -> Asked:
    """The questions this part's features leave open, each option inside its bounds."""
    context = facts.context if facts.context and facts.context.found else None
    purpose_given = context is not None and bool(context.loaded)
    seen_given = context is not None and context.seen is not None
    only = only_stock(purpose_given or seen_given)
    try:
        reply = ask(_SYSTEM + (SKILL_DIR / "SKILL.md").read_text(), _prompt(facts),
                    schema([f.id for f in facts.features], purpose=not purpose_given))
        raw = list(reply["questions"])
    except CutOff as exc:
        return Asked(STOCK,
                     f"{only}: the model was cut off ({exc})",
                     exc.info.model_dump(), failed=_cut_off(exc))
    except TimedOut as exc:
        return Asked(STOCK,
                     f"{only}: the model {exc}",
                     {"finish_reason": "timed out", "seconds": exc.seconds,
                      "timeout_s": exc.limit_s}, failed="timed out")
    except (RuntimeError, OSError, KeyError, TypeError) as exc:
        return Asked(STOCK,
                     f"{only}: the model was unavailable "
                     f"({exc})"[:600], {"finish_reason": "unavailable"},
                     failed=f"was unavailable ({exc})"[:300])

    questions = []
    dropped = []
    for q in raw[:MAX_QUESTIONS]:
        if purpose_given and str(q.get("feature")) == "purpose":
            dropped.append(f"'{q.get('text')}': the part's notes say what it's for")
            continue
        options = []
        for o in q.get("options", ()):
            settings = {str(s["key"]): str(s["value"]) for s in o.get("settings", ())}
            if says_no_support(str(o.get("label", ""))):
                if settings.get("enable_support", "0") != "0":
                    dropped.append(f"'{o.get('label')}' in '{q.get('text')}': says no support "
                                   f"and sets enable_support={settings['enable_support']}")
                    continue
                settings["enable_support"] = "0"
            refused = [f"{k}={v}: {why}" for k, v in settings.items()
                       if (why := check(k, v, facts.presets))]
            if refused:
                dropped.append(f"'{o.get('label')}' in '{q.get('text')}': {'; '.join(refused)}")
                continue
            options.append(Option(label=str(o["label"]), settings=settings,
                                  impact=str(o.get("impact", "")),
                                  recommended=bool(o.get("recommended")),
                                  why=str(o.get("why", ""))))
        if len(options) < 2:
            dropped.append(f"'{q.get('text')}': fewer than 2 options in bounds")
            continue
        questions.append(Question(feature=str(q["feature"]), text=str(q["text"]),
                                  options=tuple(_one_recommended(options))))
    note = "; ".join(f"dropped {d}" for d in dropped)
    if not any(q.feature == "purpose" for q in questions):
        # The Sponsor's choice on mqucifer/print-shop#10: purpose is always asked.
        questions = [PURPOSE, *questions[: MAX_QUESTIONS - 1]]
        said = ("purpose read back from the part's notes" if purpose_given
                else "purpose asked by print-shop: the model didn't")
        note = "; ".join(x for x in (note, said) if x)
    if not any(q.feature == SEEN.feature for q in questions):
        # Last, after what the part's shape raised; in place of the model's last question
        # when four are already asked (print-shop#105).
        said = ("what's seen read back from the part's notes" if seen_given
                else "what's seen asked by print-shop")
        if len(questions) >= MAX_QUESTIONS:
            said += f", in place of '{questions[MAX_QUESTIONS - 1].text}'"
        questions = [*questions[: MAX_QUESTIONS - 1], SEEN]
        note = "; ".join(x for x in (note, said) if x)
    return Asked(tuple(questions), note, dict(reply.get("_call") or {}))


#: An option's label that says it prints without supports: "Bridge it — no support", "without
#: supports", "unsupported", "support-free". Not "no support marks", which is about the finish.
_NO_SUPPORT = re.compile(
    r"\b(?:no|without)\s+supports?\b(?!\s*(?:marks?|scars?|contact|interface|removal))"
    r"|\bunsupported\b|\bsupport[- ]free\b",
    re.IGNORECASE,
)


def says_no_support(label: str) -> bool:
    """Whether an option's label says the part prints without supports."""
    return _NO_SUPPORT.search(label) is not None


def _left_open(context: PartContext | None) -> list[str]:
    """What the model is told of the notes: to ask only what they leave open."""
    if context is None or not context.found:
        return []
    said = "Never ask what that context already says: ask only what it leaves open."
    if context.loaded:
        said += " It gives what the part is for, which the person confirms: don't ask that."
    return [said, ""]


def estimate(
    questions: tuple[Question, ...], slice_with: Callable[[dict[str, str]], Estimate | None]
) -> tuple[Question, ...]:
    """Each option with Orca's estimate for its settings, up to `MAX_SLICES` slices."""
    budget = MAX_SLICES
    out = []
    for q in questions:
        options = []
        for o in q.options:
            found = None
            if o.settings and budget > 0:
                budget -= 1
                found = slice_with(o.settings)
            options.append(o.model_copy(update={"estimate": found}))
        out.append(q.model_copy(update={"options": tuple(options)}))
    return tuple(out)


def _cut_off(exc: CutOff) -> str:
    """A cut-off in a person's words, with no figure: its time follows, and the note has them."""
    how = {
        "runaway": " as its answer ran on as blank space",
        "too long": " as its answer ran past what four questions need",
        "length": " at its token limit",
    }.get(exc.info.finish_reason, "")
    return f"was cut off{how}"


def _one_recommended(options: list[Option]) -> list[Option]:
    """Exactly one recommended: the first marked, else the first option."""
    first = next((i for i, o in enumerate(options) if o.recommended), 0)
    return [
        o.model_copy(update={
            "recommended": i == first,
            "why": o.why or ("the first option; none was marked" if i == first else ""),
        })
        for i, o in enumerate(options)
    ]


def _prompt(facts: PartFacts) -> str:
    lines = [
        f"Filament: {facts.filament.get('name')} ({facts.filament_type}). "
        f"Process: {facts.process.get('name')}.",
        "",
        "The part's features, as it will sit on the plate:",
        *(f"- {f.line()}" for f in facts.features),
        "",
        *context_lines(facts),
        *_left_open(facts.context),
        "Settings an option may set, the preset's value, and the bound a value must stay in:",
    ]
    for key, setting in SETTINGS.items():
        allowed = (", ".join(setting.choices) if setting.choices
                   else "{:g} to {:g}".format(*setting.bound(facts.presets)))  # type: ignore[misc]
        lines.append(f"- {key}: preset {facts.preset(key) or '(unset)'}; allowed {allowed}")
    return "\n".join(lines)
