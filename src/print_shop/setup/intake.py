"""What the part is for: the short intake each print starts with.

Print-flow stage 1 (mqucifer/project-print `docs/print-flow.md`), in its first
form, mqucifer/print-shop#7: a few questions the command asks, whose answers
the setting proposal reads and the ledger keeps. Nothing here is guessed: an
answer the Sponsor didn't give is refused, not defaulted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ValidationError

__all__ = ["Environment", "Fit", "Intake", "Load", "Purpose", "ask", "read_intake"]


class Purpose(StrEnum):
    COSMETIC = "cosmetic"
    FUNCTIONAL = "functional"


class Load(StrEnum):
    NONE = "none"
    LIGHT = "light"
    HEAVY = "heavy"


class Fit(StrEnum):
    NONE = "none"
    PRESS = "press"
    SNAP = "snap"
    SLIDING = "sliding"


class Environment(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    HEAT = "heat"


class Intake(BaseModel):
    """The answers, as the job and the ledger keep them."""

    model_config = {"frozen": True}

    purpose: Purpose
    load: Load
    load_note: str = ""  # what it carries, in the Sponsor's words
    fit: Fit
    fit_note: str = ""  # what it fits, and the clearance
    environment: Environment
    note: str = ""

    def summary(self) -> str:
        parts = [self.purpose.value, f"load {self.load.value}"]
        if self.load_note:
            parts[-1] += f" ({self.load_note})"
        parts.append(f"fit {self.fit.value}" + (f" ({self.fit_note})" if self.fit_note else ""))
        parts.append(self.environment.value)
        return ", ".join(parts) + (f"; {self.note}" if self.note else "")


def read_intake(path: Path) -> Intake:
    """An intake written as JSON, for a print run without questions.

    Raises:
        ValueError: If the file isn't a complete intake.
    """
    try:
        return Intake.model_validate(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"{path} is not an intake: {exc}") from exc


_QUESTIONS: list[tuple[str, str, type[StrEnum] | None]] = [
    ("purpose", "What is it for? cosmetic or functional", Purpose),
    ("load", "Does it carry a load? none, light or heavy", Load),
    ("load_note", "  What load, in a few words (Enter to skip)", None),
    ("fit", "Must it fit something? none, press, snap or sliding", Fit),
    ("fit_note", "  What it fits, and the clearance (Enter to skip)", None),
    ("environment", "Where will it live? indoor, outdoor or heat", Environment),
    ("note", "Anything else about the part (Enter to skip)", None),
]


def ask(read: Callable[[str], str] = input, write: Callable[[str], None] = print) -> Intake:
    """Ask the intake on the command line, again until each answer is one of the choices."""
    answers: dict[str, str] = {}
    for key, question, choices in _QUESTIONS:
        if key == "load_note" and answers.get("load") == Load.NONE.value:
            continue
        if key == "fit_note" and answers.get("fit") == Fit.NONE.value:
            continue
        while True:
            answer = read(f"{question}: ").strip()
            if choices is None:
                answers[key] = answer
                break
            if answer.lower() in {c.value for c in choices}:
                answers[key] = answer.lower()
                break
            write(f"  one of: {', '.join(c.value for c in choices)}")
    return Intake.model_validate(answers)
