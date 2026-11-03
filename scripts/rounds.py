#!/usr/bin/env python3
"""A run of the local model in rounds on the bench. `docs/rounds.md` says what it does.

scripts/rounds.py start NAME --request FILE [--reference FILE]...   intake, then rounds
scripts/rounds.py resume NAME [--note FILE] [--from WHO] [--reopen-needs]   go on after a stop
scripts/rounds.py resume NAME --approve --by WHO [--note FILE]      a person's yes, then the
                                                                  model's after-action review
scripts/rounds.py resume NAME --send-back --by WHO --note FILE [--reopen-needs]
                                                                  a person's no, as a new round
scripts/rounds.py resume NAME --close --by WHO --note FILE         ended without approval, then
                                                                  the model's after-action review
scripts/rounds.py status NAME                                       where it stands
scripts/rounds.py stop NAME                                         stop before its next call
scripts/rounds.py summary NAME                                      its figures, in summary.json
scripts/rounds.py read-back NAME [--ledger FILE]                    its records, through the ledger
scripts/rounds.py after-action NAME                                 the review again, if it failed

scripts/rounds.py lessons [RUN] [--ledger FILE]                     what a run wrote, its evidence
scripts/rounds.py lessons accept ID --by WHO [--becomes refusal|bench_check]
scripts/rounds.py lessons edit ID --by WHO [--text FILE] [--kind K] [--shape S] [--why W]
scripts/rounds.py lessons reject ID --by WHO --why W
scripts/rounds.py lessons score RUN ID --score used|not_used|in_the_way --by WHO [--calls 1,2]
scripts/rounds.py lessons retire ID --by WHO --why W
scripts/rounds.py lessons promote ID --as note|skill|bench_check|refusal --where WHERE --by WHO
scripts/rounds.py lessons load FILE --by WHO                        a reviewer's steps, in order

Exit: 0 it's approved, 10 it's waiting for a person, approval included, 20 another named ending,
closed included, 3 refused. An approval is 0, and a close 20, whether its after-action review
finished or not: it's said. A close is refused while a session runs under the root, as a resume is.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from types import FrameType

import httpx

from print_shop.rounds import after_action, lessons, records
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import (
    APPROVED,
    DEFAULT_ROOT,
    NOTE_FROM,
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


def _no_model(_: dict[str, object]) -> dict[str, object]:
    raise AssertionError("a person's approval makes no model call")


def _ready(send: Sender) -> None:
    """Refuse before any model call: code that isn't committed, or a proxy without the alias."""
    commit, dirty = repository_version()
    if dirty:
        raise Refused(
            f"this checkout at {commit[:12]} has uncommitted changes: a run names its code"
        )
    if not send.lists(ALIAS):
        raise Refused(f"LiteLLM doesn't list {ALIAS} for this key")


def _after_action(folder: Path) -> str:
    """The model's after-action review on an approved or closed run, and what came of it. The run
    stays as the person left it whatever this says: a review that can't begin or doesn't finish
    is a row of `after_actions`, and the reviewer can enter lessons by hand."""
    name = folder.name
    again = f"scripts/rounds.py after-action {name}"
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    stays = f"The run stays {(state.get('ending') or {}).get('name')}."
    try:
        send = Sender()
        _ready(send)
    except (Refused, LookupError, httpx.HTTPError) as why:
        # An HTTP error's own words can hold the proxy's address, which no record holds.
        said = str(why)
        if isinstance(why, httpx.HTTPStatusError):
            said = f"LiteLLM answered {why.response.status_code}"
        elif isinstance(why, httpx.HTTPError):
            said = f"LiteLLM couldn't be reached ({type(why).__name__})"
        Run.load(folder, _no_model).after_action_not_begun(said)
        return (
            f"the after-action review didn't begin: {said}. {stays} Then: {again}, or lessons by "
            f"hand with scripts/rounds.py lessons load FILE --by WHO"
        )
    run = Run.load(folder, send)
    (folder / "stop").unlink(missing_ok=True)
    ending = run.after_action_review()
    if ending.name == after_action.REVIEWED:
        return (
            f"the after-action review is in: {ending.said}. For the reviewer: "
            f"scripts/rounds.py lessons {name}"
        )
    return (
        f"the after-action review ended {ending.name} {json.dumps(ending.said)}. {stays} Then: "
        f"{again}, or lessons by hand with scripts/rounds.py lessons load FILE --by WHO"
    )


LESSON_ACTIONS = ("accept", "edit", "reject", "score", "retire", "promote", "load")


def _lessons(args: argparse.Namespace, ledger: DesignLedger) -> str:
    """The reviewer's step, or the listing."""
    words = list(args.words)
    if not words or words[0] not in LESSON_ACTIONS:
        if len(words) > 1:
            raise Refused(f"`lessons` lists one run, or every lesson: not {' '.join(words)}")
        return lessons.listing(ledger, words[0] if words else None)
    action, rest = words[0], words[1:]
    wanted = {"score": 2, "load": 1}.get(action, 1)
    if len(rest) != wanted:
        raise Refused(f"`lessons {action}` takes {'RUN ID' if action == 'score' else 'one'}")
    if action == "load":
        ids = lessons.load(ledger, Path(rest[0]), args.by)
        return f"loaded: {json.dumps(ids)}"
    try:
        lesson = int(rest[-1])
    except ValueError as exc:
        raise Refused(f"a lesson is its number: not {rest[-1]!r}") from exc
    if action == "accept":
        lessons.accept(ledger, lesson, args.by, args.becomes)
    elif action == "edit":
        text = args.text.read_text(encoding="utf-8") if args.text else None
        lessons.edit(
            ledger,
            lesson,
            args.by,
            text=text,
            kind=args.kind,
            fix_shape=args.shape,
            becomes=args.becomes,
            why=args.why,
        )
    elif action == "reject":
        lessons.reject(ledger, lesson, args.by, args.why)
    elif action == "retire":
        lessons.retire(ledger, lesson, args.by, args.why)
    elif action == "promote":
        lessons.promote(ledger, lesson, args.by, args.became or "", args.where)
    elif action == "score":
        by = lessons.signed(args.by)
        calls = [int(c) for c in args.calls.split(",") if c.strip()] if args.calls else []
        held, wrong = lessons.evidence(ledger, rest[0], calls, at_least_one=False)
        if wrong:
            raise Refused("; ".join(wrong))
        lessons.score(ledger, rest[0], lesson, args.score or "", held, by, args.why)
    found = lessons.one(ledger, lesson)
    return f"lesson {lesson}: {found['status']}, by {found['status_by']}"


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
    resume.add_argument("--from", dest="note_from", default=NOTE_FROM, help="who the note is from")
    resume.add_argument("--reopen-needs", action="store_true")
    decision = resume.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--send-back", action="store_true")
    decision.add_argument("--close", action="store_true", help="end it without approving it")
    resume.add_argument("--by", default="", help="who decided: sponsor, or stand-in: <who>")
    sub.add_parser("status").add_argument("name")
    sub.add_parser("stop").add_argument("name")
    sub.add_parser("summary").add_argument("name")
    reading = sub.add_parser("read-back")
    reading.add_argument("name")
    reading.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    sub.add_parser("after-action").add_argument("name")
    taught = sub.add_parser("lessons")
    taught.add_argument("words", nargs="*", help="RUN, or a step: " + ", ".join(LESSON_ACTIONS))
    taught.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    taught.add_argument("--by", default="", help="who: sponsor, or stand-in: <who>")
    taught.add_argument("--why", default="")
    taught.add_argument("--text", type=Path, help="a file of the lesson's new words")
    taught.add_argument("--kind", choices=tuple(lessons.KINDS))
    taught.add_argument("--shape", help="the fix's shape, in new words")
    taught.add_argument("--becomes", choices=lessons.BECOMES)
    taught.add_argument("--as", dest="became", choices=lessons.BECAME)
    taught.add_argument("--where", default="", help="where it is now: owner/repo#n, or a path")
    taught.add_argument("--score", choices=tuple(lessons.SCORES))
    taught.add_argument("--calls", help="the calls that show it, by commas")
    args = top.parse_args()
    root = args.root.expanduser()
    folder = root / str(getattr(args, "name", ""))
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
        if args.command == "read-back":
            ledger = DesignLedger(args.ledger or root / "ledger.sqlite")
            print(json.dumps(records.read_back(folder, ledger), indent=1))
            return 0
        if args.command == "lessons":
            print(_lessons(args, DesignLedger(args.ledger or root / "ledger.sqlite")))
            return 0
        with only_session(root):
            if args.command == "resume" and args.approve:
                note = args.note.read_text(encoding="utf-8") if args.note else ""
                ending = Run.load(folder, _no_model).approve(args.by, note)
                print(f"approved, by {args.by}. The ledger holds it, in `decisions`.")
                if ending.name != APPROVED:
                    return 20
                print(_after_action(folder))
                return 0
            if args.command == "resume" and args.close:
                if args.reopen_needs:
                    raise Refused("a closed run doesn't go on, so its needs aren't reopened")
                if not args.note:
                    raise Refused("a close needs --note FILE: the model's lessons start from it")
                note = args.note.read_text(encoding="utf-8")
                ending = Run.load(folder, _no_model).close(args.by, note)
                was = ending.said["was"]["name"] or "not recorded as ended"
                print(
                    f"closed without approval, by {args.by}: it was {was}. The ledger holds it, "
                    "in `decisions`."
                )
                print(_after_action(folder))
                return 20
            if args.command == "after-action":
                print(_after_action(folder))
                return 0
            send = Sender()
            _ready(send)
            if args.command == "start":
                request = args.request.read_text(encoding="utf-8")
                run = Run.start(root, args.name, request, args.reference, send)
            else:
                run = Run.load(folder, send)
                (folder / "stop").unlink(missing_ok=True)
                note = args.note.read_text(encoding="utf-8") if args.note else ""
                if args.send_back:
                    run.send_back(args.by, note, args.reopen_needs)
                else:
                    run.resume(note, args.reopen_needs, args.note_from)
            ending = run.drive()
            print(what_next(run, ending))
    except (Refused, lessons.Refused, FileNotFoundError, LookupError) as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 3
    return 10 if ending.name in WAITING else 20


if __name__ == "__main__":
    sys.exit(main())
