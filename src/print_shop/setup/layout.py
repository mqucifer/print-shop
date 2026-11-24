"""How the command lays out what it says: to a width, wrapped under its own start
(mqucifer/print-shop#223, mqucifer/print-shop#266).

The review, the questions and a job's opening lines share it, so they read alike: at most
`WIDTH` columns, or the terminal's where it's narrower, never a word broken, and without colour,
so the job's log holds the same.
"""

from __future__ import annotations

import shutil
import textwrap

__all__ = ["WIDTH", "right", "terminal_width", "wrap"]

#: The widest a line is.
WIDTH = 100


def terminal_width() -> int:
    """The terminal's width, up to `WIDTH`; `WIDTH` where there's no terminal."""
    return min(shutil.get_terminal_size((WIDTH, 24)).columns, WIDTH)


def wrap(lead: str, text: str, width: int, rest: str | None = None) -> list[str]:
    """`text` after `lead`, wrapped under its own start (or `rest`) to `width`. A word is never
    broken: a path or a setting's name longer than the line runs past it, on its own."""
    hang = " " * len(lead) if rest is None else rest
    return textwrap.wrap(text, width, initial_indent=lead, subsequent_indent=hang,
                         break_long_words=False, break_on_hyphens=False) or [lead.rstrip()]


def right(lines: list[str], tail: str, width: int) -> list[str]:
    """`lines` with `tail` at the right of the last, where it fits, else on a line of its own,
    at the right."""
    if not tail:
        return lines
    last = lines[-1] if lines else ""
    if len(last) + 2 + len(tail) <= width:
        return [*lines[:-1], last + tail.rjust(width - len(last))]
    return [*lines, tail.rjust(width)]
