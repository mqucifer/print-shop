#!/usr/bin/env python3
"""A run of the local model in rounds on the bench. `docs/rounds.md` says what it does.

scripts/rounds.py start NAME --request FILE [--reference FILE]... [--right FILE]
                                                                  intake, then rounds; a right
                                                                  answer is the scenario's ranges
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
scripts/rounds.py refine NAME --nozzle MM --filament [COLOUR=]NAME...   after approval: the
                                  approved folder sliced by Orca, its findings and proposals
scripts/rounds.py refined NAME                                      the latest pass's findings
scripts/rounds.py proposal NAME ID --approve|--reject --by WHO [--note FILE]
scripts/rounds.py scores NAME [--right FILE] [--ledger FILE] [--keep]   each round against the
                                  scenario's right answer: its distance, improvement and kind
scripts/rounds.py replay NAME N [--times K] [--label L] [--out DIR] [CHANGE]   call N sent again
                                  with one change: --skill NAME=FILE, --reply CALL.K=FILE,
                                  --skills-card FILE, --lesson FILE or --replace OLD NEW; none is
                                  the control. Into ~/print-shop/replays/, never the run's folder

scripts/rounds.py lessons [RUN] [--ledger FILE]                     what a run wrote, its evidence
scripts/rounds.py lessons accept ID --by WHO [--routes R,R] [--becomes B]
scripts/rounds.py lessons edit ID --by WHO [--text FILE] [--kind K] [--shape S] [--routes R,R]
                                  [--becomes B] [--keys CMD:VERDICT,...] [--part-kind K] [--why W]
scripts/rounds.py lessons reject ID --by WHO --why W
scripts/rounds.py lessons score RUN ID --score used|not_used|in_the_way --by WHO [--calls 1,2]
                                  [--route model|harness|request|operator]
scripts/rounds.py lessons retire ID --by WHO --why W
scripts/rounds.py lessons promote ID --as B --where WHERE --by WHO    delivered on its route
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

from print_shop.rounds import after_action, lessons, records, replays, scores
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
from print_shop.slicer.store import NoChoice


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


def _listed(words: str | None) -> list[str] | None:
    return [w.strip() for w in words.split(",") if w.strip()] if words is not None else None


def _keys(words: str | None) -> list[dict[str, str]] | None:
    """`capture:FAIL,thin` as keys: a command, and a verdict or none for any."""
    if words is None:
        return None
    found = []
    for word in _listed(words) or []:
        command, _, verdict = word.partition(":")
        found.append({"command": command, "verdict": verdict})
    return found


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
        lessons.accept(ledger, lesson, args.by, args.becomes, _listed(args.routes))
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
            routes=_listed(args.routes),
            keys=_keys(args.keys),
            part_kind=args.part_kind,
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
        lessons.score(
            ledger, rest[0], lesson, args.score or "", held, by, args.why, route=args.route
        )
    found = lessons.one(ledger, lesson)
    return f"lesson {lesson}: {found['status']}, by {found['status_by']}"


def _refine(args: argparse.Namespace, folder: Path, ledger: DesignLedger) -> int:
    """The refinement pass, its findings, and a decision on a proposal it made."""
    from print_shop.refine import approved, proposals
    from print_shop.refine.context import Context
    from print_shop.refine.refinement import Pass
    from print_shop.slicer.orca import version

    if args.command == "proposal":
        note = args.note.read_text(encoding="utf-8") if args.note else ""
        decision = "approved" if args.approve else "rejected"
        n = proposals.decide(ledger, args.id, decision, args.by, note)
        print(
            f"proposal {args.id} {decision} by {args.by} (decision {n}). Nothing in the approved "
            "folder changes: an approved change goes to the design, as a new run or a send-back."
        )
        return 0
    if args.command == "refined":
        rows = ledger.rows(
            "SELECT * FROM refinements WHERE run=? ORDER BY n DESC LIMIT 1", folder.name
        )
        if not rows:
            raise Refused(f"{folder.name} hasn't been refined")
        found = ledger.rows(
            "SELECT seq, kind, source, file, layer_from, z_from, read, means, needs_change "
            "FROM refine_findings WHERE refinement=? ORDER BY seq", rows[0]["id"]
        )
        made = ledger.rows(
            "SELECT p.*, d.decision, d.by FROM refine_proposals AS p LEFT JOIN "
            "proposal_decisions AS d ON d.proposal = p.id WHERE p.refinement=?", rows[0]["id"]
        )
        print(json.dumps({"refinement": dict(rows[0]), "findings": [dict(r) for r in found],
                          "proposals": [dict(r) for r in made]}, indent=1))
        return 0
    commit, dirty = repository_version()
    if dirty:
        raise Refused(
            f"this checkout at {commit[:12]} has uncommitted changes: a pass names its code"
        )
    run = approved.read(folder, ledger)
    context = Context.parse(args.nozzle, args.filament, machine=args.machine, process=args.process)
    done = Pass(run, ledger, context, orca=version(), commit=commit).run()
    changes = [f for f in done.findings if f.needs_change]
    print(f"refinement {done.n} of {run.run}: {len(done.findings)} findings, "
          f"{len(changes)} needing the part to change, {len(done.proposals)} proposals.")
    print(f"{done.folder / 'README.md'}")
    for folder_name, steps in done.steps.items():
        print(f"{folder_name}: " + ("print steps written" if steps else "no print steps"))
    return 0


def _scores(args: argparse.Namespace, folder: Path, root: Path) -> int:
    """Each round's score against a right answer, from a ledger it only reads unless `--keep`."""
    right = args.right or folder / scores.RIGHT_FILE
    if not right.is_file():
        raise Refused(f"there's no right answer: {right} isn't there, and --right names none")
    try:
        scenario, ranges, sha = scores.load_right(right)
    except scores.NotRight as exc:
        raise Refused(str(exc)) from exc
    path = args.ledger or root / "ledger.sqlite"
    found = scores.score(scores.read_only(path), folder.name, ranges)
    if args.keep:
        scores.keep(DesignLedger(path), found, sha)
    print(json.dumps({
        "how_it_ran": f"scripts/rounds.py scores, from {path.name}"
        + (", kept in round_scores" if args.keep else ", read only"),
        "run": folder.name,
        "scenario": scenario,
        "right_sha": sha,
        "rounds": [one.plain() for one in found],
        "phases": scores.phases(found),
    }, indent=1))
    return 0


def _change(args: argparse.Namespace) -> replays.Change:
    """The one change a replay makes, from its flags: none is the control."""
    given = [
        name
        for name in ("skill", "reply", "skills_card", "lesson", "replace")
        if getattr(args, name) is not None
    ]
    if len(given) > 1:
        raise Refused(f"a replay changes one thing: not {', '.join(given)}")
    if not given:
        return replays.Change()
    kind = given[0]
    if kind in ("skill", "reply"):
        target, sep, file = getattr(args, kind).partition("=")
        if not sep or not target or not file:
            raise Refused(f"--{kind} is {'NAME' if kind == 'skill' else 'CALL.K'}=FILE")
        return replays.Change(kind, target, Path(file).read_text(encoding="utf-8"), source=file)
    if kind == "replace":
        old, new = (Path(f) for f in args.replace)
        return replays.Change(
            "replace", old.name, new.read_text(encoding="utf-8"),
            old=old.read_text(encoding="utf-8"), source=f"{old} -> {new}",
        )
    file = getattr(args, kind)
    return replays.Change(
        kind.replace("_", "-"), "", Path(file).read_text(encoding="utf-8"), source=file
    )


def _replay(args: argparse.Namespace, folder: Path) -> int:
    change = _change(args)
    send = Sender()
    _ready(send)
    commit, _ = repository_version()
    from print_shop.bench import host  # noqa: PLC0415 - only for where the repository is

    done = replays.replay(
        folder, args.n, change, send, times=args.times, root=args.out, label=args.label,
        commit=commit, public=host.REPOSITORY,
    )
    print(json.dumps({"folder": done.folder, **done.summary()}, indent=1))
    return 0


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
    start.add_argument(
        "--right", type=Path, help="the scenario's right answer, as ranges: kept from the model"
    )
    resume = sub.add_parser("resume")
    resume.add_argument("name")
    resume.add_argument("--note", type=Path)
    resume.add_argument(
        "--from",
        dest="note_from",
        default=NOTE_FROM,
        help="who the note is from; at `questions`, who answered: sponsor, or stand-in: <who>",
    )
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
    refining = sub.add_parser("refine", help="after approval: the folder sliced through Orca")
    refining.add_argument("name")
    refining.add_argument("--nozzle", type=float, required=True, help="the fitted nozzle, mm")
    refining.add_argument(
        "--filament", action="append", required=True,
        help="a filament for every colour, by variety or tuning, or COLOUR=NAME for one",
    )
    refining.add_argument("--machine", help="a machine profile's exact name, to pick one")
    refining.add_argument("--process", help="a process profile's exact name, to pick one")
    refining.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    refined = sub.add_parser("refined", help="the latest refinement's findings and proposals")
    refined.add_argument("name")
    refined.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    proposed = sub.add_parser("proposal", help="a person's yes or no to a proposed part change")
    proposed.add_argument("name")
    proposed.add_argument("id", type=int)
    verdict = proposed.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--approve", action="store_true")
    verdict.add_argument("--reject", action="store_true")
    proposed.add_argument("--by", required=True, help="who decided: sponsor, or stand-in: <who>")
    proposed.add_argument("--note", type=Path)
    proposed.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    scored = sub.add_parser("scores", help="each round against the scenario's right answer")
    scored.add_argument("name")
    scored.add_argument("--right", type=Path, help="this right answer, not the run's right.json")
    scored.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    scored.add_argument("--keep", action="store_true", help="write them into round_scores")
    again = sub.add_parser("replay", help="one recorded call sent again, with one change")
    again.add_argument("name")
    again.add_argument("n", type=int, help="the call, from 1")
    again.add_argument("--times", type=int, default=3)
    again.add_argument("--label", default="", help="the replay's folder name; its change's kind")
    again.add_argument(
        "--out", type=Path, default=replays.DEFAULT_ROOT, help="where replays go, by run and call"
    )
    again.add_argument("--skill", help="NAME=FILE: the skill's text, in the start and its reads")
    again.add_argument("--reply", help="CALL.K=FILE: the reply to call CALL's K-th tool call")
    again.add_argument("--skills-card", help="FILE: the start's required skills, as a card")
    again.add_argument("--lesson", help="FILE: a lesson added to the start's lessons")
    again.add_argument("--replace", nargs=2, metavar=("OLD", "NEW"), help="exact text, as files")
    taught = sub.add_parser("lessons")
    taught.add_argument("words", nargs="*", help="RUN, or a step: " + ", ".join(LESSON_ACTIONS))
    taught.add_argument("--ledger", type=Path, help="this ledger, not the one beside the runs")
    taught.add_argument("--by", default="", help="who: sponsor, or stand-in: <who>")
    taught.add_argument("--why", default="")
    taught.add_argument("--text", type=Path, help="a file of the lesson's new words")
    taught.add_argument("--kind", choices=tuple(lessons.KINDS))
    taught.add_argument("--shape", help="the fix's shape, in new words")
    taught.add_argument("--becomes", choices=lessons.BECOMES)
    taught.add_argument("--routes", help="who acts on it, by commas: " + ", ".join(lessons.ROUTES))
    taught.add_argument("--route", default="model", choices=tuple(lessons.ROUTES))
    taught.add_argument("--keys", help="the checks it's about: capture:FAIL,thin (any verdict)")
    taught.add_argument("--part-kind", help="the kind of part it's for; empty makes it general")
    taught.add_argument("--as", dest="became", choices=tuple(lessons.BECAME))
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
            keys = ("stage", "round", "stop", "ending", "restarts", "stops")
            print(json.dumps({k: state.get(k) for k in keys}, indent=1))
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
        if args.command in ("refine", "refined", "proposal"):
            return _refine(args, folder, DesignLedger(args.ledger or root / "ledger.sqlite"))
        if args.command == "scores":
            return _scores(args, folder, root)
        if args.command == "replay":
            with only_session(args.out.expanduser()):
                return _replay(args, folder)
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
                if args.right:
                    try:
                        scores.load_right(args.right)
                    except scores.NotRight as exc:
                        raise Refused(str(exc)) from exc
                run = Run.start(root, args.name, request, args.reference, send)
                if args.right:
                    scores.keep_right(run.folder, args.right, run.ledger)
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
    except (
        Refused, lessons.Refused, replays.Refused, NoChoice, FileNotFoundError, LookupError
    ) as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 3
    return 10 if ending.name in WAITING else 20


if __name__ == "__main__":
    sys.exit(main())
