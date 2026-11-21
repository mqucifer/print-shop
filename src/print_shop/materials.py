"""A filament's material, by one name wherever print-shop reads it.

Orca's filament library spells Bambu Lab's PETG presets `PET` (Bambu PETG HF and
Bambu PETG Translucent, `filament_type: "PET"`), where every other PETG print-shop
holds says `PETG`. Read as given, a Bambu PETG print would key its lessons apart from
every PETG lesson, and the store would hold a third material beside PLA and PETG. So
each place that reads a filament type reads it through `material_name`.
"""

from __future__ import annotations

#: Another spelling of a material, as a preset or a slice gives it, to print-shop's name.
SAME_AS: dict[str, str] = {"PET": "PETG"}


def material_name(raw: str) -> str:
    """The material's name in capitals, with another spelling of it read as print-shop's own."""
    found = raw.strip().upper()
    return SAME_AS.get(found, found)
