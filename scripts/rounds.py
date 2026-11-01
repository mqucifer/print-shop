#!/usr/bin/env python3
"""A run of the local model in rounds on the bench. `docs/rounds.md` says what it does.

scripts/rounds.py start NAME --request FILE [--reference FILE]...   intake, then rounds
scripts/rounds.py resume NAME [--note FILE] [--reopen-needs]        go on after a stop
scripts/rounds.py status NAME                                       where it stands
scripts/rounds.py stop NAME                                         stop before its next call
scripts/rounds.py summary NAME                                      its figures, in summary.json

Exit: 0 its needs are met, 10 it's waiting for the Sponsor, 20 another named ending, 3 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from types import FrameType

from print_shop.rounds.run import (
    DEFAULT_ROOT,
    MET,
    WAITING,
    Refused,
    Run,
    only_session,
    repository_version,
    summarise,
    what_next,
)
from print_shop.rounds.send import ALIAS, Sender


def _interrupted(_signal: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def _ready(send: Sender) -> None:
    """Refuse before any model call: code that isn't committed, or a proxy without the alias."""
    commit, dirty = repository_version()
    if dirty:
        raise Refused(
            f"this checkout at {commit[:12]} has uncommitted changes: a run names its code"
        )
    if not send.lists(ALIAS):
        raise Refused(f"LiteLLM doesn't list {ALIAS} for this key")


def main() -> int:
    top = argparse.ArgumentParser(description=__doc__)
    top.add_argument(
        "--root", type=Path, default=Path(os.environ.get("PRINT_DESIGN_RUNS", DEFAULT_ROOT))
    )
    sub = top.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("name")
    start.add_argument("--request", type=Path, required=True)
    start.add_argument("--reference", type=Path, action="append", default=[])
    resume = sub.add_parser("resume")
    resume.add_argument("name")
    resume.add_argument("--note", type=Path)
    resume.add_argument("--reopen-needs", action="store_true")
    sub.add_parser("status").add_argument("name")
    sub.add_parser("stop").add_argument("name")
    sub.add_parser("summary").add_argument("name")
    args = top.parse_args()
    root = args.root.expanduser()
    folder = root / args.name
    signal.signal(signal.SIGTERM, _interrupted)
    try:
        if args.command == "status":
            state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            keys = ("stage", "round", "stop", "ending", "restarts")
            print(json.dumps({k: state[k] for k in keys}, indent=1))
            return 0
        if args.command == "summary":
            print(json.dumps(summarise(folder), indent=1))
            return 0
        if args.command == "stop":
            (folder / "stop").touch()
            print(f"it stops before its next call: {folder / 'stop'}")
            return 0
        with only_session(root):
            send = Sender()
            _ready(send)
            if args.command == "start":
                request = args.request.read_text(encoding="utf-8")
                run = Run.start(root, args.name, request, args.reference, send)
            else:
                run = Run.load(folder, send)
                (folder / "stop").unlink(missing_ok=True)
                note = args.note.read_text(encoding="utf-8") if args.note else ""
                run.resume(note, args.reopen_needs)
            ending = run.drive()
            print(what_next(run, ending))
    except (Refused, FileNotFoundError) as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 3
    if ending.name == MET:
        return 0
    return 10 if ending.name in WAITING else 20


if __name__ == "__main__":
    sys.exit(main())
