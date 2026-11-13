"""A part's traits: what a lesson from an earlier print is keyed to (mqucifer/print-shop#106).

Each is the part's shape in a printer person's words, from its measured features
(`features.py`) by a stated threshold. A lesson reaches a later part that has every trait it's
keyed to (`lessons.for_part`).
"""

from __future__ import annotations

from print_shop.setup.features import Feature

__all__ = ["TRAITS", "traits"]


#: Each trait, the feature it's read from, and its threshold in words. Not the material: the
#: Sponsor, 2026-11-13, keyed lessons to the part's features. The adhesion group's thresholds
#: (`skill.GROUPS`) are the same numbers, so a trait and the skill read a part alike.
TRAITS: dict[str, tuple[str, str]] = {
    "long_and_thin": ("aspect", "its longest side on the plate is 3 or more times its shortest"),
    "tall_and_narrow": ("slenderness", "more than 2.5 times as tall as its narrowest width"),
    "flat_and_wide": ("slenderness", "less than a quarter as tall as its narrowest width"),
    "small_bed_contact": ("bed_contact", "under 200 mm2 touches the plate"),
    "large_bed_contact": ("bed_contact", "5000 mm2 or more touches the plate"),
    "overhangs": ("overhang", "faces past 45 degrees from vertical, off the plate"),
    "bridges": ("bridge", "flat downward faces off the plate"),
    "thin_walls": ("section", "a typical section under 2 mm"),
}

_HAS = {
    "long_and_thin": lambda f: f["aspect"] >= 3,
    "tall_and_narrow": lambda f: f["slenderness"] > 2.5,
    "flat_and_wide": lambda f: 0 < f["slenderness"] < 0.25,
    "small_bed_contact": lambda f: 0 < f["bed_contact"] < 200,
    "large_bed_contact": lambda f: f["bed_contact"] >= 5000,
    "overhangs": lambda f: f["overhang"] > 0,
    "bridges": lambda f: f["bridge"] > 0,
    "thin_walls": lambda f: 0 < f["section"] < 2,
}


def traits(features: tuple[Feature, ...]) -> tuple[str, ...]:
    """The traits a part's features give it, in `TRAITS`' order. A feature not measured (a
    part from before it was) gives none of the traits it decides: nothing is guessed."""
    values = {f.id: f.value for f in features}
    return tuple(
        t for t, (feature, _) in TRAITS.items() if feature in values and _HAS[t](_zeros(values))
    )


def _zeros(values: dict[str, float]) -> dict[str, float]:
    return {k: values.get(k, 0.0) for k in ("aspect", "slenderness", "bed_contact", "overhang",
                                              "bridge", "section")}
