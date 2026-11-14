"""A risk that settings answer, read from why the orientation step turned a part.

mqucifer/print-shop#128. On 2026-11-14 the orientation model stood a flat lid on its edge "to
avoid warping": a risk the plate, a brim and the fans answer, at no cost to the pose. The
Sponsor: "it tried to solve the warping issue with orientation when it should have used the
elefant foot issue - knowing orcas settings". So when the person sends a pose back at the
review, the step names the concern it turned the part for and offers the settings that would
meet it instead, each priced by Orca (`review.send_back`).

Each concern is found by the words of the step's own reason, and each way is Orca's own keys,
inside their bounds (`bounds.check`). A concern with no settings here gets none offered, and
the person says why in their own words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from print_shop.setup.bounds import BED_MAX_C, check

__all__ = ["CONCERNS", "Concern", "Way", "concern_in", "ways"]


@dataclass(frozen=True)
class Way:
    """One way to meet a concern by settings: what it is, in words, and Orca's keys."""

    label: str
    settings: dict[str, str]


@dataclass(frozen=True)
class Concern:
    name: str
    words: re.Pattern[str]


#: The concerns an orientation's reason may give that settings answer.
CONCERNS = (
    Concern("warping", re.compile(
        r"\b(warp\w*|lift\w*|curl\w*|peel\w*|adhesion|adhere\w*|stick\w*)\b", re.I)),
)

#: The plate's temperature keys, by the plate Orca slices for.
_PLATE_KEYS = {
    "Textured PEI Plate": ("textured_plate_temp", "textured_plate_temp_initial_layer"),
    "Cool Plate": ("cool_plate_temp", "cool_plate_temp_initial_layer"),
}


def concern_in(reason: str) -> Concern | None:
    """The concern a step's reason names, if settings answer it."""
    return next((c for c in CONCERNS if c.words.search(reason)), None)


def ways(concern: Concern, presets: dict[str, dict[str, Any]]) -> list[Way]:
    """The settings that would meet `concern` on these presets, each inside its bound."""
    found: list[Way] = []
    if concern.name == "warping":
        found.append(Way("a brim round the outside", {"brim_type": "outer_only"}))
        plate = str(_first(presets["process"].get("curr_bed_type", "Cool Plate")))
        keys = _PLATE_KEYS.get(plate)
        if keys is not None:
            now = _number(presets["filament"].get(keys[0]))
            if now is not None and now + 5 <= BED_MAX_C:
                warmer = f"{now + 5:g}"
                found.append(Way(f"a warmer plate, {warmer} C", dict.fromkeys(keys, warmer)))
        found.append(Way("the chamber's exhaust off while it prints",
                         {"during_print_exhaust_fan_speed": "0"}))
        found.append(Way("the side fan off", {"additional_cooling_fan_speed": "0"}))
    return [w for w in found if not any(check(k, v, presets) for k, v in w.settings.items())]


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _number(value: Any) -> float | None:
    try:
        return float(_first(value))
    except (TypeError, ValueError):
        return None
