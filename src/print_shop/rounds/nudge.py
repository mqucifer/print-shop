"""A bench check that keeps failing gets a line in its reply: show what you have and ask what you
need, or end with `blocked`, as a failed, complete run is a valid end (the Sponsor, 2026-11-03).

A check is its command and the files it names, not its other arguments: on `suncatcher-2` the model
passed `capture` a lower `--min-lip` on each try, and each try was the same check failing. Exit 0
passes, and starts the count again. Exit 3 is the command itself being wrong, so it's neither. Any
other exit doesn't pass: 1, both methods agree something's wrong, 2, there's no answer to rely on,
or the container's own.

`AFTER` is from the design runs' records (`~/print-shop/proofs/m18-close/streaks.json`): of 41 runs
of failures of one check in a row, 35 were 3 or fewer, and the 6 of 4 or more include both loops
`suncatcher-2` was stopped in. It's a line in a reply, never a limit: nothing is cut.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from print_shop.rounds import prompt

#: Failures in a row of one check before its reply says so, and each one after.
AFTER = 4
#: The command was wrong: not an answer either way.
USAGE = 3
#: A word that names a file: it ends in a suffix that starts with a letter, so `0.35` isn't one.
SUFFIX = re.compile(r"\.[A-Za-z][A-Za-z0-9]*$")


def check_of(argv: list[str]) -> tuple[str, tuple[str, ...]]:
    """A bench command's check: its command and the files it names, whatever else it's given."""
    words = argv[1:] if argv[:1] == ["bench"] else argv
    if not words:
        return "", ()
    files = {
        w
        for w in words[1:]
        if not w.startswith("-") and "=" not in w and SUFFIX.search(PurePosixPath(w).name)
    }
    return words[0], tuple(sorted(files))


def failing(earlier: list[tuple[list[str], int]], check: tuple[str, tuple[str, ...]]) -> int:
    """Its failures in a row, from the newest back: `earlier` is each bench call's argv and exit,
    oldest first, this one included."""
    n = 0
    for argv, code in reversed(earlier):
        if check_of(argv) != check or code == USAGE:
            continue
        if code == 0:
            break
        n += 1
    return n


def said(check: tuple[str, tuple[str, ...]], failures: int) -> str:
    command, files = check
    on = f" on {', '.join(f'`{f}`' for f in files)}" if files else ""
    return (
        f"`{command}`{on} has failed {failures} times in a row, whatever its other arguments. "
        f"{prompt.STUCK}"
    )
