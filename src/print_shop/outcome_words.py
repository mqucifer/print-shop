"""An outcome in words, the same wherever one is read (mqucifer/print-shop#189).

A module of its own, as the ledger imports the lessons and both say an outcome.
"""

from __future__ import annotations

__all__ = ["outcome_words"]


def outcome_words(result: str, symptom: str | None) -> str:
    """`success`, `success, with stringing` for a minor flaw on a usable part, or
    `failed, stringing`."""
    if not symptom or symptom == "none":
        return str(result)
    return f"{result}, with {symptom}" if result == "success" else f"{result}, {symptom}"
