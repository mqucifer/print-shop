"""A risk that settings answer, read from why the orientation step turned a part.

mqucifer/print-shop#128. On 2026-11-14 the orientation model stood a flat lid on its edge "to
avoid warping": a risk the plate, a brim and the fans answer, at no cost to the pose. The
Sponsor: "it tried to solve the warping issue with orientation when it should have used the
elefant foot issue - knowing orcas settings". So when the person sends a pose back at the
review, the step names the concern it turned the part for and offers the settings that would
meet it instead, each priced by Orca (`review.send_back`).

Each concern is found by the words of the step's own reason, and each way is Orca's own keys,
inside their bounds (`bounds.check`). **Where a kept print lesson sets a way's key for this part,
in the pose chosen and on its filament, the way offers the lesson's value and names it**: a
fixed value would contradict what the shop has learned (the operator, 2026-11-14). The fixed
values are for keys no lesson sets. A concern with no settings here gets none offered, and the
person says why in their own words.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from print_shop.setup.bounds import BED_MAX_C, check
from print_shop.setup.proposal import GivenLesson

__all__ = ["CONCERNS", "Concern", "Way", "concern_in", "ways"]


@dataclass(frozen=True)
class Way:
    """One way to meet a concern by settings: what it is, in words, and Orca's keys, and the
    lessons whose values it offers."""

    label: str
    settings: dict[str, str]
    lessons: tuple[int, ...] = ()


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


def ways(
    concern: Concern, presets: dict[str, dict[str, Any]], lessons: Sequence[GivenLesson] = (),
) -> list[Way]:
    """The settings that would meet `concern` on these presets, each inside its bound. `lessons`
    are the kept lessons for this part in the pose chosen: a key one sets is offered at its
    value, naming it."""
    kept = {g.key: g for g in lessons if g.key and not g.withheld}
    found: list[Way] = []
    if concern.name == "warping":
        found.append(_way(kept, {"brim_type": "outer_only"},
                          "a brim round the outside", lambda v: f"a brim, {v}"))
        plate = str(_first(presets["process"].get("curr_bed_type", "Cool Plate")))
        keys = _PLATE_KEYS.get(plate)
        if keys is not None:
            now = _number(presets["filament"].get(keys[0]))
            if any(k in kept for k in keys) or (now is not None and now + 5 <= BED_MAX_C):
                warmer = f"{now + 5:g}" if now is not None else ""
                found.append(_way(kept, dict.fromkeys(keys, warmer),
                                  f"a warmer plate, {warmer} C", lambda v: f"the plate at {v} C"))
        found.append(_way(kept, {"during_print_exhaust_fan_speed": "0"},
                          "the chamber's exhaust off while it prints",
                          lambda v: f"the chamber's exhaust at {v}% while it prints"))
        found.append(_way(kept, {"additional_cooling_fan_speed": "0"}, "the side fan off",
                          lambda v: f"the side fan at {v}%"))
    return [w for w in found if w.settings and all(v for v in w.settings.values())
            and not any(check(k, v, presets) for k, v in w.settings.items())]


def _way(
    kept: dict[str, GivenLesson], fixed: dict[str, str], label: str,
    learned: Callable[[str], str],
) -> Way:
    """A way at its fixed values, or at the values the lessons for its keys set, named."""
    by = [kept[k] for k in fixed if k in kept]
    if not by:
        return Way(label, fixed)
    settings = {k: kept[k].value if k in kept else v for k, v in fixed.items()}
    ids = tuple(g.id for g in by)
    named = " and ".join(str(i) for i in ids)
    who = f"lessons {named} keep" if len(ids) > 1 else f"lesson {named} keeps"
    return Way(f"{learned(by[0].value)}, as {who} it", settings, ids)


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _number(value: Any) -> float | None:
    try:
        return float(_first(value))
    except (TypeError, ValueError):
        return None
