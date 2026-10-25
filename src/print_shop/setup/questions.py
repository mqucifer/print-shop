"""The intake's questions, proposed from the part's features by one model call.

mqucifer/print-shop#10, the Sponsor's ask of 2026-10-25: questions with
limited options and their impacts, not open-ended ones. The model reads the
part's features, its presets, and the print-settings skill's practice and
index (SKILL.md), and proposes what those leave undecided:

- at most 4 questions, each tied to a feature id, or to "purpose" when the
  features can't show what the part is for. When none of the model's covers
  purpose, print-shop asks it first, with `PURPOSE` (the Sponsor's choice on
  mqucifer/print-shop#10), and keeps the model's first three;
- 2 to 4 options each. An option carries the settings it implies, each
  inside its bound (`bounds.check`): an option out of bounds is dropped, and
  a question left with fewer than 2 options is dropped. Exactly one option is
  recommended, with why;
- each option's impact on strength, surface and risk in one line, in the
  model's words, which never restate its values: the settings line shown
  beside it is written from the settings it applies (`settings_line`). Its
  time and filament are never the model's: `estimate` slices the part with
  that option's settings, and an option Orca couldn't slice says "no
  estimate".

A part whose features leave nothing to decide gets no questions. A failed or
cut-off call gives no questions, saying so, and the proposal goes on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from print_shop.expert.client import CutOff
from print_shop.setup.bounds import SETTINGS, check
from print_shop.setup.intake import Estimate, Option, Question
from print_shop.setup.model import SHORT, TEXT, Ask, setting_item
from print_shop.setup.proposal import PartFacts
from print_shop.setup.skill import SKILL_DIR

__all__ = ["MAX_QUESTIONS", "Asked", "estimate", "propose_questions", "schema"]

MAX_QUESTIONS = 4
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
    "one is recommended, with why, pointing at the part. Never ask what the features "
    "already answer, and never ask open-ended questions. A simple part may need none. Don't "
    "state times or filament: those come from the slicer.\n\n"
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


class Asked:
    """What the questions step produced: the questions, and why there are none, if none."""

    def __init__(self, questions: tuple[Question, ...], note: str = "",
                 call: dict[str, Any] | None = None) -> None:
        self.questions = questions
        self.note = note
        self.call = call or {}


def schema(feature_ids: list[str]) -> dict[str, Any]:
    """The reply's shape, enforced by the server."""
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
            "feature": {"type": "string", "enum": [*feature_ids, "purpose"]},
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
    try:
        reply = ask(_SYSTEM + (SKILL_DIR / "SKILL.md").read_text(), _prompt(facts),
                    schema([f.id for f in facts.features]))
        raw = list(reply["questions"])
    except CutOff as exc:
        return Asked((PURPOSE,), f"only purpose asked: the model was cut off ({exc})",
                     exc.info.model_dump())
    except (RuntimeError, OSError, KeyError, TypeError) as exc:
        return Asked((PURPOSE,), f"only purpose asked: the model was unavailable ({exc})"[:600])

    questions = []
    dropped = []
    for q in raw[:MAX_QUESTIONS]:
        options = []
        for o in q.get("options", ()):
            settings = {str(s["key"]): str(s["value"]) for s in o.get("settings", ())}
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
        note = "; ".join(x for x in (note, "purpose asked by print-shop: the model didn't") if x)
    return Asked(tuple(questions), note, dict(reply.get("_call") or {}))


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
        "Settings an option may set, the preset's value, and the bound a value must stay in:",
    ]
    for key, setting in SETTINGS.items():
        allowed = (", ".join(setting.choices) if setting.choices
                   else "{:g} to {:g}".format(*setting.bound(facts.presets)))  # type: ignore[misc]
        lines.append(f"- {key}: preset {facts.preset(key) or '(unset)'}; allowed {allowed}")
    return "\n".join(lines)
