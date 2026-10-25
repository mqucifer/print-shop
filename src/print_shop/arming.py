"""Whether anything in this process may arm the printer.

Off unless `PRINT_AGENT_ALLOW_HARDWARE=1` is in the process environment. It is
deliberately never read from `.env`: a `.env` value is ambient configuration,
set once and in force for every run after, and that is the shape of what went
wrong on 2026-09-16, when the prototype's test suite started two real prints.
Arming has to be an act, made again on each invocation.

The name is the prototype's, kept so the Sponsor's command is the same one.
"""

from __future__ import annotations

import os

__all__ = ["ALLOW_HARDWARE_ENV", "hardware_allowed"]

ALLOW_HARDWARE_ENV = "PRINT_AGENT_ALLOW_HARDWARE"


def hardware_allowed() -> bool:
    """True only when the invocation's environment opted in, with exactly "1"."""
    return os.environ.get(ALLOW_HARDWARE_ENV) == "1"
