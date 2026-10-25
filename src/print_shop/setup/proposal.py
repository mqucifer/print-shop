"""A print's settings, proposed over the chosen presets from what the part is and is for.

Print-flow stage 5's first recorded form (mqucifer/print-shop#7). The Sponsor
tunes presets; this proposes, per print, what to change from them. The
Sponsor, 2026-10-25: "It's all very dependent on the actual part ... This
goes back to my want for this to understand what it's doing." So:

- **the reasoning is per part,** from its measured features (`features.py`)
  and the intake, and each change names the feature, or the purpose, behind
  it ("the 2 mm tab carries the load, so 4 walls");
- **a skill, not defaults:** what each setting does and when to change it
  is the print-settings skill's (`skills/print-settings/`, loaded in layers
  by `skill.py`); code holds only the bounds every value must fall in
  (`bounds.py`);
- **the model reasons** (`model.py`), inside those bounds. A value outside
  them is refused and recorded as refused;
- **two guards** hold whatever the model says: a part with an overhang and
  no decision about supports gets them, and supports are always tree (the
  Sponsor's practice). An overhang also gets a note that it could be
  designed out, for the CAD side;
- **the Sponsor's correction** at the review replaces any of it.

The changes are applied as whole copies of the process and filament
profiles, the way the plate is (`with_bed_type`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from print_shop.setup.bounds import SETTINGS
from print_shop.setup.features import Feature
from print_shop.setup.intake import Environment, Intake

if TYPE_CHECKING:  # the slicer imports the job state, which imports this
    from print_shop.slicer.orca import SlicerProfiles

__all__ = ["Change", "PartFacts", "Proposal", "Source", "apply", "guard"]


class Source(StrEnum):
    MODEL = "model"
    GUARD = "guard"  # a rule that holds whatever the model says
    SPONSOR = "sponsor"


class Change(BaseModel):
    """One setting changed from the preset, and why."""

    model_config = {"frozen": True}

    key: str
    preset: str  # the preset's own value, as Orca writes it
    #: Where the preset is the Sponsor's tuning of a standard (mqucifer/print-shop#8),
    #: the standard's value, so the review shows standard -> tuning -> this print.
    standard: str = ""
    value: str
    reason: str
    source: Source
    feature: str = ""  # the feature id behind it, or "purpose"
    knowledge: str = ""  # the skill's reference group it cites


class Proposal(BaseModel):
    """The changes for one print, the reasoning behind them, and what wasn't done."""

    model_config = {"frozen": True}

    changes: tuple[Change, ...] = ()
    #: Settings the model weighed and left at the preset's value, with why.
    kept: tuple[Change, ...] = ()
    #: How the part was read, in the model's words: what it is, what matters.
    reasoning: str = ""
    #: Where a change to the design would serve better than a setting.
    design_notes: tuple[str, ...] = ()
    #: Things the Sponsor should know that no setting answers ("PLA outdoors").
    notes: tuple[str, ...] = ()
    #: Model suggestions refused, each with why.
    refused: tuple[str, ...] = ()
    #: Which model reasoned: an alias, "" for none, or why it couldn't.
    model: str = ""
    #: The call's finish reason and tokens, reasoning tokens included (`CallInfo`).
    call: dict[str, Any] = {}
    #: The skill's reference groups the model read for this part, and the hash
    #: of what it read (SKILL.md and those references), so a proposal can be
    #: traced to the skill as it stood.
    skill_groups: tuple[str, ...] = ()
    skill_sha256: str = ""

    def get(self, key: str) -> Change | None:
        return next((c for c in self.changes if c.key == key), None)

    def corrected(self, corrections: list[Change]) -> Proposal:
        """This proposal with the Sponsor's corrections in place of what they correct."""
        keys = {c.key for c in corrections}
        kept = tuple(c for c in self.changes if c.key not in keys)
        return self.model_copy(update={"changes": kept + tuple(corrections)})


@dataclass(frozen=True)
class PartFacts:
    """What the proposal reads: the intake, the features, and the presets."""

    intake: Intake
    features: tuple[Feature, ...]
    machine: dict[str, Any]
    process: dict[str, Any]
    filament: dict[str, Any]
    #: The standards the process and filament tune, when they are tunings.
    standard_process: dict[str, Any] = field(default_factory=dict)
    standard_filament: dict[str, Any] = field(default_factory=dict)

    def feature(self, id: str) -> float:
        return next((f.value for f in self.features if f.id == id), 0.0)

    def preset(self, key: str) -> str:
        setting = SETTINGS.get(key)
        data = self.filament if setting is not None and setting.filament else self.process
        return _first(data.get(key, ""))

    def standard(self, key: str) -> str:
        """The standard's value for `key`, or '' when the preset is the standard itself."""
        setting = SETTINGS.get(key)
        data = (
            self.standard_filament if setting is not None and setting.filament
            else self.standard_process
        )
        return _first(data.get(key, "")) if data else ""

    @property
    def presets(self) -> dict[str, dict[str, Any]]:
        return {"machine": self.machine, "process": self.process, "filament": self.filament}

    @property
    def filament_type(self) -> str:
        return _first(self.filament.get("filament_type", ""))


def guard(facts: PartFacts, proposal: Proposal) -> Proposal:
    """The rules that hold whatever the model proposed, and the notes for the Sponsor."""
    changes = list(proposal.changes)
    overhang = facts.feature("overhang")

    def value(key: str) -> str:
        change = next((c for c in changes if c.key == key), None)
        return change.value if change else facts.preset(key)

    decided = {c.key for c in changes} | {c.key for c in proposal.kept}
    if overhang > 0 and value("enable_support") != "1" and "enable_support" not in decided:
        changes.append(Change(
            key="enable_support", preset=facts.preset("enable_support"), value="1",
            reason=f"the {overhang:g} mm2 overhang would print into the air, and nothing "
                   "decided otherwise",
            source=Source.GUARD, feature="overhang", knowledge="supports"))
    if value("enable_support") == "1" and value("support_type") != "tree(auto)":
        changes = [c for c in changes if c.key != "support_type"]
        changes.append(Change(
            key="support_type", preset=facts.preset("support_type"), value="tree(auto)",
            reason="tree supports, as you use them", source=Source.GUARD,
            feature="overhang", knowledge="supports"))

    design = list(proposal.design_notes)
    if overhang > 0:
        design.insert(0, (
            f"{overhang:g} mm2 overhangs, up to {facts.feature('steepest_overhang'):g} degrees "
            "past vertical: a chamfer to 45 degrees or a support designed into the part "
            "would print without Orca's supports. That's a design change, for the CAD side."
        ))
    def with_standard(c: Change) -> Change:
        return c.model_copy(update={"standard": facts.standard(c.key)})

    return proposal.model_copy(update={
        "changes": tuple(with_standard(c) for c in changes),
        "kept": tuple(with_standard(c) for c in proposal.kept),
        "design_notes": tuple(design),
        "notes": proposal.notes + tuple(_notes(facts)),
    })


def _notes(f: PartFacts) -> list[str]:
    notes = []
    if f.filament_type == "PLA" and f.intake.environment in (Environment.OUTDOOR, Environment.HEAT):
        notes.append(
            f"PLA {f.intake.environment.value}: it softens near 60 C. That's the filament's "
            "choice, which no setting changes"
        )
    low = _first(f.filament.get("nozzle_temperature_range_low", ""))
    high = _first(f.filament.get("nozzle_temperature_range_high", ""))
    temp = f.preset("nozzle_temperature")
    try:
        if not float(low) <= float(temp) <= float(high):
            notes.append(
                f"the filament preset's own nozzle temperature, {temp} C, is outside its "
                f"maker's range of {low}-{high} C"
            )
    except ValueError:
        pass
    return notes


def apply(profiles: SlicerProfiles, proposal: Proposal, workdir: Path) -> SlicerProfiles:
    """`profiles` with the changes written into whole copies of the process and filament.

    A filament setting is a per-extruder list in Orca's JSON; the CC1 has one.
    """
    process = json.loads(profiles.process.read_text())
    filament = json.loads(profiles.filament.read_text()) if profiles.filament else {}
    for change in proposal.changes:
        setting = SETTINGS.get(change.key)
        if setting is not None and setting.filament:
            filament[change.key] = [change.value]
        else:
            process[change.key] = change.value
    workdir.mkdir(parents=True, exist_ok=True)
    out_process = workdir / "process_proposed.json"
    out_process.write_text(json.dumps(process, indent=2))
    update: dict[str, Path] = {"process": out_process}
    if profiles.filament is not None:
        out_filament = workdir / "filament_proposed.json"
        out_filament.write_text(json.dumps(filament, indent=2))
        update["filament"] = out_filament
    return profiles.model_copy(update=update)


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)
