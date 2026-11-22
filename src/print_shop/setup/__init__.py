"""Print-flow stage 5's first form: the intake, the proposed settings and the Sponsor's review.

mqucifer/print-shop#7. `Proposer` is what the graph's slice node calls: the
model reads the part through the print-settings skill and proposes inside the
bounds (`model.py`), then the guards hold (`proposal.guard`), over the presets
the print was given.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from print_shop.expert.client import LLMConfig
from print_shop.setup.context import PartContext
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.model import Ask, llm_ask, llm_ask_streamed, propose_by_model
from print_shop.setup.proposal import (
    GivenLesson,
    PartFacts,
    Proposal,
    answered,
    answered_kept,
    from_lessons,
    guard,
)
from print_shop.setup.questions import ANSWER_CHARS, Asked, propose_questions
from print_shop.setup.skill import groups_for, load
from print_shop.slicer.store import STORE_DIR

if TYPE_CHECKING:  # the slicer imports the job state, which imports this
    from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Lessons", "Proposer"]

#: The reviewed lessons for a part with these features in this filament type (`lessons.for_part`,
#: over the ledger). A lesson for a setting the filament decides is withheld for another filament.
Lessons = Callable[[tuple[Feature, ...], str], tuple[GivenLesson, ...]]


@dataclass(frozen=True)
class Proposer:
    """Proposes a print's settings. `llm` None asks no model, and says so."""

    llm: LLMConfig | None = None
    ask: Ask | None = None  # a test's recorded reply in place of the real call
    ask_questions: Ask | None = None  # the same, for the intake's questions
    #: Where kept lessons come from (mqucifer/print-shop#106). None gives none.
    lessons: Lessons | None = None
    ask_doubts: Ask | None = None  # a test's, for what differences put in doubt

    def doubter(self) -> Ask | None:
        """The model that reads what a past print's differences put in doubt
        (mqucifer/print-shop#211), or None: the offer doesn't need one."""
        if self.ask_doubts is not None:
            return self.ask_doubts
        return llm_ask(self.llm) if self.llm is not None else None

    @property
    def asks_model(self) -> bool:
        """Whether the questions come from a model, which can be waited for."""
        return self.ask_questions is not None or self.llm is not None

    def questions(
        self, features: tuple[Feature, ...], profiles: SlicerProfiles,
        stop: threading.Event | None = None,
    ) -> Asked:
        """The intake's questions for this part, before any are answered. `stop` ends the
        model's call (mqucifer/print-shop#208)."""
        facts = self._facts(Intake(), features, profiles)
        if self.ask_questions is not None:
            return propose_questions(facts, self.ask_questions)
        if self.llm is not None:
            return propose_questions(facts, llm_ask_streamed(self.llm, ANSWER_CHARS, stop))
        return Asked((), "no questions: no model was asked")

    def __call__(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles,
        context: PartContext | None = None,
    ) -> Proposal:
        facts = self._facts(intake, features, profiles, context)
        given = self.lessons(features, facts.filament_type) if self.lessons is not None else ()
        learned, learned_kept, marked = from_lessons(facts, given)
        facts = dataclasses.replace(facts, lessons=marked)
        skill = load(groups_for(intake, features))
        if self.ask is not None:
            reasoned = propose_by_model(facts, skill, self.ask, "recorded")
        elif self.llm is not None:
            reasoned = propose_by_model(facts, skill, llm_ask(self.llm), self.llm.model)
        else:
            reasoned = Proposal(model="")
        # The Sponsor's answers first, then the lessons: the model may not change what they set.
        reasoned = reasoned.model_copy(update={
            "changes": tuple(answered(facts)) + tuple(learned) + reasoned.changes,
            "kept": tuple(answered_kept(facts)) + tuple(learned_kept) + reasoned.kept,
            "lessons": marked,
        })
        return guard(facts, reasoned)

    def facts(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles,
        context: PartContext | None = None,
    ) -> PartFacts:
        """What a proposal reads: the intake, the features and the presets, with the kept lessons
        for this part, as the print loop is given them (mqucifer/print-shop#129), and the part's
        context (mqucifer/print-shop#130)."""
        facts = self._facts(intake, features, profiles, context)
        _, _, marked = from_lessons(facts, self.lessons_for(features, profiles))
        return dataclasses.replace(facts, lessons=marked)

    def lessons_for(
        self, features: tuple[Feature, ...], profiles: SlicerProfiles
    ) -> tuple[GivenLesson, ...]:
        """The kept lessons for a part with these features, on these presets' filament."""
        if self.lessons is None:
            return ()
        return self.lessons(features, self._facts(Intake(), features, profiles).filament_type)

    def _facts(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles,
        context: PartContext | None = None,
    ) -> PartFacts:
        return PartFacts(
            context=context,
            intake=intake,
            features=features,
            machine=json.loads(profiles.machine.read_text()),
            process=json.loads(profiles.process.read_text()),
            filament=json.loads(profiles.filament.read_text()) if profiles.filament else {},
            standard_process=_standard(profiles, "process"),
            standard_filament=_standard(profiles, "filament"),
        )



def _standard(profiles: SlicerProfiles, kind: str) -> dict[str, Any]:
    """The standard a tuned profile was composed over, from the store, or {}."""
    if profiles.used is None:
        return {}
    ref = getattr(profiles.used, kind)
    if not ref.standard_file:
        return {}
    path = STORE_DIR / ref.standard_file
    data: dict[str, Any] = json.loads(path.read_text()) if path.is_file() else {}
    return data
