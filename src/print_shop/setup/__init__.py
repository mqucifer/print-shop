"""Print-flow stage 5's first form: the intake, the proposed settings and the Sponsor's review.

mqucifer/print-shop#7. `Proposer` is what the graph's slice node calls: the
model reads the part through the print-settings skill and proposes inside the
bounds (`model.py`), then the guards hold (`proposal.guard`), over the presets
the print was given.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from print_shop.expert.client import LLMConfig
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.model import Ask, llm_ask, propose_by_model
from print_shop.setup.proposal import PartFacts, Proposal, guard
from print_shop.setup.skill import groups_for, load

if TYPE_CHECKING:  # the slicer imports the job state, which imports this
    from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Proposer"]


@dataclass(frozen=True)
class Proposer:
    """Proposes a print's settings. `llm` None asks no model, and says so."""

    llm: LLMConfig | None = None
    ask: Ask | None = None  # a test's recorded reply in place of the real call

    def __call__(
        self, intake: Intake, features: tuple[Feature, ...], profiles: SlicerProfiles
    ) -> Proposal:
        facts = PartFacts(
            intake=intake,
            features=features,
            machine=json.loads(profiles.machine.read_text()),
            process=json.loads(profiles.process.read_text()),
            filament=json.loads(profiles.filament.read_text()) if profiles.filament else {},
        )
        skill = load(groups_for(intake, features))
        if self.ask is not None:
            reasoned = propose_by_model(facts, skill, self.ask, "recorded")
        elif self.llm is not None:
            reasoned = propose_by_model(facts, skill, llm_ask(self.llm), self.llm.model)
        else:
            reasoned = Proposal(model="")
        return guard(facts, reasoned)
