"""Whether bench commands can be read, without running them, inside the container.

The rounds (mqucifer/print-shop#17) ask this when a need names a check: a command that couldn't
be read is better said at intake than after a part is made. Each argument is a command as JSON.
It prints one line, `MARK` then a JSON list of what each got: "" or what's wrong.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys

MARK = "ROUNDS_USAGE "


def main(argv: list[str] | None = None) -> None:
    # Here, so the host can read `MARK` with no CAD library.
    from .cli import parser

    found: list[str] = []
    for text in sys.argv[1:] if argv is None else argv:
        said = io.StringIO()
        try:
            with contextlib.redirect_stderr(said), contextlib.redirect_stdout(said):
                parser().parse_args(json.loads(text))
            found.append("")
        except SystemExit as stopped:
            found.append(said.getvalue().strip()[:600] or f"it stopped with {stopped.code}")
    print(MARK + json.dumps(found))
