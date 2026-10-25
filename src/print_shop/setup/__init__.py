"""Print-flow stage 5's first form: the intake, the proposed settings and the Sponsor's review.

mqucifer/print-shop#7. `Proposer` is what the graph's slice node calls: the
model reads the part through the print-settings skill and proposes inside the
bounds (`model.py`), then the guards hold (`proposal.guard`), over the presets
the print was given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from print_shop.expert.client import LLMConfig
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.model import Ask, llm_ask, propose_by_model
from print_shop.setup.proposal import PartFacts, Proposal, answered, guard
from print_shop.setup.questions import Asked, propose_questions
from print_shop.setup.skill import groups_for, load
from print_shop.slicer.store import STORE_DIR

if TYPE_CHECKING:  # the slicer imports the job state, which imports this
    from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Proposer"]


@dataclass(frozen=True)
class Proposer:
    """Proposes a print's settings. `llm` None asks no model, and says so."""

    llm: LLMConfig | None = None
    ask: Ask | None = None  # a test's recorded reply in place of the real call
    ask_questions: Ask | None = None  # the same, for the intake's questions

    def questions(self, features: tuple[Feature, ...], profiles: SlicerProfiles) -> Asked:
        """The intake's questions for this part, before any are answered."""
        facts = self._facts(Intake(), features, profiles)
        if self.ask_questions is not None:
            return propose_questions(facts, self.ask_questions)
        if self.llm is not None:
            return propose_questions(facts, llm_ask(self.llm))
        return Asked((), "no questions: no model was asked")

    def __call__(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles
    ) -> Proposal:
        facts = self._facts(intake, features, profiles)
        skill = load(groups_for(intake, features))
        if self.ask is not None:
            reasoned = propose_by_model(facts, skill, self.ask, "recorded")
        elif self.llm is not None:
            reasoned = propose_by_model(facts, skill, llm_ask(self.llm), self.llm.model)
        else:
            reasoned = Proposal(model="")
        # The Sponsor's answers first: the model may not change what they chose.
        reasoned = reasoned.model_copy(
            update={"changes": tuple(answered(facts)) + reasoned.changes}
        )
        return guard(facts, reasoned)

    def _facts(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles
    ) -> PartFacts:
        return PartFacts(
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
