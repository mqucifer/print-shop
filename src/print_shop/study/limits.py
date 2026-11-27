"""The Sponsor's limits on a settings study, given up front and binding on it
(mqucifer/print-shop#359).

The Sponsor, 2026-11-27, of studies run by themselves: "Everything with a load case to start
with the option to include flagged designs." A study queued on its own (`study.orders`) keeps
to the limits given with it:

- **`most_minutes`** and **`most_grams`**: a candidate Orca slices past either is ruled out, and
  can't be recommended. Only a slice says them, so it's sliced first.
- **`patterns`**: the infill patterns a candidate may ask for; **`wall_sequences`**: the wall
  orders it may ask for, for finish. A candidate asking another is refused before it's sliced.
- **`least_infill`**, **`most_infill`** and **`most_walls`**: within `study.candidate`'s own
  bounds, narrower where given; a candidate past them is refused before it's sliced.
- **`nozzle`** and **`filament`**: what it slices with, where the run doesn't say: a variety or a
  tuning's name, as `slicer.store.choose` takes it.

The table says which candidates a limit ruled out, and by which (`study.table`). JSON, one object
of these keys; any other is refused, never ignored.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from print_shop.bench.printed import Settings
from print_shop.study.candidate import PATTERNS, WALL_SEQUENCES, Candidate

__all__ = ["Limits", "OutOfLimits"]


class OutOfLimits(ValueError):  # noqa: N818 - a refusal, said as what it is
    """A candidate the Sponsor's limits rule out before it's sliced."""


@dataclass(frozen=True)
class Limits:
    """What binds a study, each None or empty where the Sponsor gave none."""

    most_minutes: float | None = None
    most_grams: float | None = None
    patterns: tuple[str, ...] = ()
    wall_sequences: tuple[str, ...] = ()
    least_infill: float | None = None
    most_infill: float | None = None
    most_walls: int | None = None
    nozzle: float | None = None
    filament: str | None = None

    @classmethod
    def of(cls, raw: dict[str, Any] | None) -> Limits:
        """Limits from their JSON object.

        Raises:
            ValueError: Naming a key that isn't one, or a value that isn't what it takes.
        """
        raw = dict(raw or {})
        names = {f.name for f in fields(cls)}
        if extra := sorted(set(raw) - names):
            raise ValueError(f"the study's limits are {', '.join(sorted(names))}: not "
                             f"{', '.join(extra)}")
        got: dict[str, Any] = {}
        for key in ("most_minutes", "most_grams", "least_infill", "most_infill", "nozzle"):
            if raw.get(key) is not None:
                value = raw[key]
                if isinstance(value, bool) or not isinstance(value, int | float) or not (
                        math.isfinite(value) and value > 0):
                    raise ValueError(f"the limit {key} is a number above 0, not {value!r}")
                got[key] = float(value)
        if raw.get("most_walls") is not None:
            walls = raw["most_walls"]
            if isinstance(walls, bool) or not isinstance(walls, int) or walls < 1:
                raise ValueError(f"the limit most_walls is a whole number from 1, not {walls!r}")
            got["most_walls"] = walls
        for key, allowed in (("patterns", PATTERNS), ("wall_sequences", tuple(WALL_SEQUENCES))):
            given = raw.get(key) or []
            if not isinstance(given, list) or not all(isinstance(g, str) for g in given):
                raise ValueError(f"the limit {key} is a list of names")
            if wrong := [g for g in given if g not in allowed]:
                raise ValueError(f"the limit {key} takes {', '.join(allowed)}: not "
                                 f"{', '.join(wrong)}")
            got[key] = tuple(given)
        if raw.get("filament") is not None:
            if not isinstance(raw["filament"], str) or not raw["filament"].strip():
                raise ValueError("the limit filament is a variety or a tuning's name")
            got["filament"] = raw["filament"].strip()
        if (got.get("least_infill") is not None and got.get("most_infill") is not None
                and got["least_infill"] > got["most_infill"]):
            raise ValueError("the limit least_infill is above most_infill")
        return cls(**got)

    @classmethod
    def read(cls, path: Path) -> Limits:
        """Limits from a JSON file.

        Raises:
            ValueError: If it isn't one object of limits.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path} isn't a JSON object of the study's limits: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{path} isn't a JSON object of the study's limits")
        return cls.of(raw)

    def plain(self) -> dict[str, Any]:
        """Only what's given, as its JSON."""
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(self).items()
                if v not in (None, ())}

    def given(self) -> bool:
        return bool(self.plain())

    def said(self) -> str:
        """The limits in words, or "" where none is given."""
        said = []
        if self.most_minutes is not None:
            said.append(f"at most {self.most_minutes:g} min")
        if self.most_grams is not None:
            said.append(f"at most {self.most_grams:g} g")
        if self.patterns:
            said.append("infill " + " or ".join(self.patterns))
        if self.least_infill is not None or self.most_infill is not None:
            said.append(f"infill from {self.least_infill or 0:g} to "
                        f"{self.most_infill if self.most_infill is not None else 100:g}%")
        if self.most_walls is not None:
            said.append(f"at most {self.most_walls} walls")
        if self.wall_sequences:
            said.append("walls laid " + " or ".join(self.wall_sequences))
        return "; ".join(said)

    def check(self, candidate: Candidate) -> None:
        """Refuse a candidate past what's known before it's sliced: its pattern, wall order,
        infill and walls, the part's and each region's.

        Raises:
            OutOfLimits: Saying which limit and what was asked.
        """
        if (candidate.wall_sequence and self.wall_sequences
                and candidate.wall_sequence not in self.wall_sequences):
            raise OutOfLimits(f"walls laid {candidate.wall_sequence} are past the Sponsor's "
                              f"limit: {' or '.join(self.wall_sequences)} only")
        for where, settings in (("the part", candidate.settings),
                                *((f"region {r.name}", r.settings) for r in candidate.regions)):
            self._settings(where, settings)

    def _settings(self, where: str, settings: Settings) -> None:
        if settings.pattern and self.patterns and settings.pattern not in self.patterns:
            raise OutOfLimits(f"{where}'s {settings.pattern} infill is past the Sponsor's limit: "
                              f"{' or '.join(self.patterns)} only")
        if settings.infill is not None:
            if self.least_infill is not None and settings.infill < self.least_infill:
                raise OutOfLimits(f"{where}'s {settings.infill:g}% infill is under the Sponsor's "
                                  f"least, {self.least_infill:g}%")
            if self.most_infill is not None and settings.infill > self.most_infill:
                raise OutOfLimits(f"{where}'s {settings.infill:g}% infill is past the Sponsor's "
                                  f"most, {self.most_infill:g}%")
        if (settings.walls is not None and self.most_walls is not None
                and settings.walls > self.most_walls):
            raise OutOfLimits(f"{where}'s {settings.walls} walls are past the Sponsor's most, "
                              f"{self.most_walls}")

    def sliced_past(self, time_s: int | None, grams: float | None) -> str:
        """What a slice is past of the time and grams allowed, in words; "" where nothing."""
        past = []
        if self.most_minutes is not None and time_s is not None and (
                time_s > self.most_minutes * 60):
            past.append(f"{time_s / 60:.1f} min, past the Sponsor's most of "
                        f"{self.most_minutes:g} min")
        if self.most_grams is not None and grams is not None and grams > self.most_grams:
            past.append(f"{grams:g} g, past the Sponsor's most of {self.most_grams:g} g")
        return "; ".join(past)
