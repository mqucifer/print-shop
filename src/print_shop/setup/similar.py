"""How like this part a past print's part is, measured and never asked of a model.

mqucifer/print-shop#211. A lesson reaches a part by eight yes/no traits (`traits.py`), so a part
with 4950 mm2 on the plate got none of what one with 5050 mm2 gets. Here the part's features are
read as numbers, and each is near or far by how much it differs. The ranking has no threshold:
the same bytes come first, then closeness on three counts (`SHARES`):

- **shape:** size, height and the features, each number's share of difference, averaged;
- **context:** the same fits, holds and seen faces, or none on both (`context.py`);
- **source:** the same design run or sidecar, the same file name, or words of the name in common.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from print_shop.core.state import Orientation
from print_shop.setup.features import Feature

__all__ = ["MEASURES", "Likeness", "Numbers", "apart", "differences", "figure", "likeness",
           "measured", "numbers", "posed"]

#: A part's shape as numbers, by `MEASURES`' names.
Numbers = dict[str, float]


@dataclass(frozen=True)
class Measure:
    """A number two parts are compared by: how it's said, and the difference that's nothing."""

    says: str
    #: Its name in the offer's table, and in a sentence of it, and its unit.
    label: str
    unit: str
    #: Added to the larger of two values, so 0 against a sliver isn't wholly different.
    floor: float


MEASURES: dict[str, Measure] = {
    "height": Measure("mm tall", "height", "mm", 2.0),
    "length": Measure("mm long", "length", "mm", 2.0),
    "width": Measure("mm across", "width", "mm", 2.0),
    "bed_contact": Measure("mm2 on the plate", "area on the plate", "mm2", 50.0),
    "volume": Measure("mm3 of part", "volume", "mm3", 500.0),
    "section": Measure("mm typical wall", "typical wall", "mm", 0.2),
    "overhang": Measure("mm2 of overhang", "overhang", "mm2", 50.0),
    "bridge": Measure("mm2 of bridge", "bridge", "mm2", 50.0),
    "flat_top": Measure("mm2 of flat top", "flat top", "mm2", 50.0),
}

#: What closeness is made of: mostly the part, then its context and where it came from.
SHARES = {"shape": 0.8, "context": 0.1, "source": 0.1}
#: Names with every word in common are this near a common source, and never are one: "part-1" and
#: "part-2" can be any two parts.
NAMED_ALIKE = 0.75
#: A difference smaller than this share isn't listed: one just across a trait's threshold is.
SAID = 0.005

#: A context's fit or hold is named in backticks (`context.Fit.line`).
_NAMED = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class Likeness:
    """How like this part one past print's part is, each count from 0 to 1."""

    shape: float
    context: float
    source: float

    @property
    def score(self) -> float:
        return (SHARES["shape"] * self.shape + SHARES["context"] * self.context
                + SHARES["source"] * self.source)


def numbers(features: Iterable[Feature]) -> Numbers:
    """The measured features as numbers. The footprint's width is its length over its aspect."""
    found = {f.id: f.value for f in features}
    shape = {name: found[name] for name in MEASURES if name in found}
    if "length" in found and found.get("footprint_aspect"):
        shape["width"] = round(found["length"] / found["footprint_aspect"], 1)
    return shape


def posed(shape: Numbers, pose: Orientation) -> Numbers:
    """The part's numbers in another of its poses, from that pose's own figures: what a turn
    doesn't move is kept, and what it does and isn't scored there is left out."""
    x, y, _ = pose.bbox_mm
    turned: Numbers = {name: value for name, value in shape.items()
                       if name in ("volume", "section")}
    turned |= {"height": pose.height_mm, "bed_contact": pose.bed_contact_area_mm2,
               "overhang": pose.overhang_area_mm2}
    if x and y:
        turned |= {"length": max(x, y), "width": min(x, y)}
    return turned


def apart(name: str, a: float, b: float) -> float:
    """How far apart two values of a measure are, as a share of the larger, from 0 to 1."""
    return abs(a - b) / (max(abs(a), abs(b)) + MEASURES[name].floor)


def likeness(
    here: Numbers, then: Numbers,
    here_context: tuple[str, ...], then_context: tuple[str, ...],
    here_source: tuple[str, str], then_source: tuple[str, str],
) -> Likeness:
    """How like `here` the past print is. A context is its lines (`PartContext.lines`); a source
    is the path it was read from and the part's file name."""
    both = [apart(n, here[n], then[n]) for n in MEASURES if n in here and n in then]
    return Likeness(
        shape=1 - sum(both) / len(both) if both else 0.0,
        context=_context(here_context, then_context),
        source=_source(here_source, then_source),
    )


def _context(here: tuple[str, ...], then: tuple[str, ...]) -> float:
    a, b = _said(here), _said(then)
    return len(a & b) / len(a | b) if a | b else 1.0


def _said(lines: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        kind, _, rest = line.partition(": ")
        if kind in ("fit", "hold"):
            found |= {f"{kind} {name}" for name in _NAMED.findall(rest)[:1]}
        elif kind == "seen" and not rest.startswith("not said"):
            found.add(line)
    return found


def _source(here: tuple[str, str], then: tuple[str, str]) -> float:
    (path, name), (past_path, past_name) = here, then
    if (path and path == past_path) or name == past_name:
        return 1.0
    a, b = _words(name), _words(past_name)
    return NAMED_ALIKE * len(a & b) / len(a | b) if a | b else 0.0


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", Path(name).stem.lower()))


def measured(name: str, value: float) -> str:
    """A measure in words: "4950 mm2 on the plate"."""
    return f"{figure(value)} {MEASURES[name].says}"


def figure(value: float) -> str:
    return f"{round(value, 2):.10g}"


def differences(here: Numbers, then: Numbers) -> list[str]:
    """The measures that differ, by name, the most different first."""
    found = sorted(
        ((apart(n, here[n], then[n]), n) for n in MEASURES if n in here and n in then),
        reverse=True,
    )
    return [n for far, n in found if far > SAID]
