"""The print-settings skill, loaded in layers: SKILL.md, then only the references a part touches.

The Sponsor, 2026-10-25, on mqucifer/print-shop#7: the proposal's knowledge
lives in a skill in print-shop (`skills/print-settings/`), not in code.
Orca's wiki is big and detailed, so the skill is layered: a short SKILL.md
(how to read a part, the Sponsor's practice, an index of setting groups),
and one reference per group, distilled from the wiki and linking each setting
to its page. A proposal loads SKILL.md and the groups the part's features
touch, and records which it read and the hash of what it read.

The groups' "read when" is SKILL.md's index table, decided here from the
features and the intake; `tests/test_skill.py` keeps the two in step.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from print_shop.setup.features import Feature
from print_shop.setup.intake import Environment, Fit, Intake, Load, Purpose

__all__ = ["GROUPS", "SKILL_DIR", "Group", "Skill", "groups_for", "load"]

#: `skills/print-settings/` at the top of print-shop's checkout.
SKILL_DIR = Path(__file__).resolve().parents[3] / "skills" / "print-settings"


@dataclass(frozen=True)
class Group:
    """A setting group: its reference file, its settings, and when a part needs it."""

    id: str
    settings: tuple[str, ...]
    needed: Callable[[Intake, dict[str, float]], bool]

    @property
    def path(self) -> Path:
        return SKILL_DIR / "references" / f"{self.id}.md"


def _always(intake: Intake, f: dict[str, float]) -> bool:
    return True


GROUPS: tuple[Group, ...] = (
    Group("walls-shells", ("wall_loops", "top_shell_layers", "bottom_shell_layers"), _always),
    Group("infill", ("sparse_infill_density", "sparse_infill_pattern"), _always),
    Group("supports", ("enable_support", "support_type", "support_threshold_angle"),
          lambda i, f: f["overhang"] > 0 or f["bridge"] > 0),
    Group("adhesion", ("brim_type",),
          lambda i, f: f["bed_contact"] < 200 or f["slenderness"] > 2.5),
    Group("layer-height", ("layer_height",),
          lambda i, f: i.purpose is Purpose.COSMETIC or i.fit is not Fit.NONE),
    Group("speed-cooling", ("outer_wall_speed", "inner_wall_speed", "sparse_infill_speed",
                            "fan_max_speed", "fan_min_speed"),
          lambda i, f: i.purpose is Purpose.COSMETIC or i.load is not Load.NONE
          or f["overhang"] > 0 or f["bridge"] > 0),
    Group("temperature", ("nozzle_temperature",),
          lambda i, f: i.load is not Load.NONE or i.fit is not Fit.NONE
          or i.environment is not Environment.INDOOR),
)


@dataclass(frozen=True)
class Skill:
    """What a proposal read: SKILL.md and its groups' references, and their hash."""

    groups: tuple[Group, ...]
    text: str

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(g.id for g in self.groups)

    @property
    def settings(self) -> tuple[str, ...]:
        return tuple(s for g in self.groups for s in g.settings)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def groups_for(intake: Intake, features: tuple[Feature, ...]) -> tuple[Group, ...]:
    """The groups this part's features and purpose touch, in the index's order."""
    values = {f.id: f.value for f in features}
    return tuple(g for g in GROUPS if g.needed(intake, values))


def load(groups: tuple[Group, ...], root: Path = SKILL_DIR) -> Skill:
    """SKILL.md, then each group's reference, as one text.

    Raises:
        FileNotFoundError: If the skill or a reference is missing, before any model call.
    """
    parts = [(root / "SKILL.md").read_text()]
    for group in groups:
        reference = root / "references" / f"{group.id}.md"
        parts.append(f"<!-- references/{group.id}.md -->\n{reference.read_text()}")
    return Skill(groups=groups, text="\n\n".join(parts))
