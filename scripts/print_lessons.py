"""The reviewer's tool for print lessons (mqucifer/print-shop#106), as `rounds.py lessons` for runs.

    uv run python scripts/print_lessons.py [JOB] [--ledger FILE]       every lesson, or a print's
    uv run python scripts/print_lessons.py draw                         candidates from every print
    uv run python scripts/print_lessons.py traits                       what a lesson is keyed to
    uv run python scripts/print_lessons.py accept ID --by WHO
    uv run python scripts/print_lessons.py edit ID --by WHO [--text FILE] [--setting K=V|none]
                                           [--traits T,T] [--why W]
    uv run python scripts/print_lessons.py reject ID --by WHO --why W
    uv run python scripts/print_lessons.py retire ID --by WHO --why W
    uv run python scripts/print_lessons.py promote ID --as skill --where owner/repo#n --by WHO
    uv run python scripts/print_lessons.py score JOB ID --score used|not_used|in_the_way --by WHO
    uv run python scripts/print_lessons.py write JOB --by WHO --text FILE --traits T,T
                                           [--setting K=V]

Each correction at the review and each outcome with something to say is drawn as a candidate when
the ledger records it; `draw` does it again for every print, and for prints from before. A
reviewer signs `--by "reviewer: <who>"` (or `sponsor`, or `stand-in: <who>`), as for CAD lessons.
Nothing reaches a proposal until it's accepted or edited. `docs/print-lessons.md` says the rest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from print_shop.env import SETTINGS_PATH
from print_shop.ledger import Ledger, ledger_path
from print_shop.rounds.lessons import Refused
from print_shop.setup import lessons
from print_shop.setup.traits import TRAITS

STEPS = ("draw", "traits", "accept", "edit", "reject", "retire", "promote", "score", "write")


def _listed(value: str | None) -> list[str] | None:
    return None if value is None else [v for v in value.split(",") if v.strip()]


def _text(path: Path | None) -> str | None:
    return path.read_text() if path is not None else None


def _id(words: list[str], step: str) -> int:
    if len(words) != 1 or not words[0].isdigit():
        raise Refused(f"`{step}` takes one lesson's number")
    return int(words[0])


def run(args: argparse.Namespace, ledger: Ledger) -> str:
    words = list(args.words)
    step = words.pop(0) if words and words[0] in STEPS else None
    if step is None:
        if len(words) > 1:
            raise Refused("list one print's lessons, or every lesson: give at most one JOB")
        return lessons.listing(ledger, words[0] if words else None)
    if step == "traits":
        return "\n".join(f"- {t}: {what} (from `{f}`)" for t, (f, what) in TRAITS.items())
    if step == "draw":
        for row in ledger.rows():
            lessons.after_print(ledger, row.job_id)
        return lessons.listing(ledger)
    if step == "score":
        if len(words) != 2 or not words[1].isdigit():
            raise Refused("`score` takes JOB ID")
        lessons.score(ledger, words[0], int(words[1]), args.score or "", args.by, args.why)
        return lessons.listing(ledger, words[0])
    if step == "write":
        if len(words) != 1:
            raise Refused("`write` takes the JOB it's about")
        made = lessons.write(
            ledger, words[0], args.by, _text(args.text) or "", _listed(args.traits) or [],
            args.setting,
        )
        return f"lesson {made}, a candidate\n\n" + lessons.listing(ledger, words[0])
    lesson = _id(words, step)
    if step == "accept":
        lessons.accept(ledger, lesson, args.by)
    elif step == "edit":
        lessons.edit(
            ledger, lesson, args.by, text=_text(args.text), setting=args.setting,
            keyed=_listed(args.traits), why=args.why,
        )
    elif step == "reject":
        lessons.reject(ledger, lesson, args.by, args.why)
    elif step == "retire":
        lessons.retire(ledger, lesson, args.by, args.why)
    elif step == "promote":
        lessons.promote(ledger, lesson, args.by, args.became or "", args.where or "")
    found = lessons.one(ledger, lesson)
    return f"lesson {lesson} is {found['status']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("words", nargs="*", help="JOB, or a step: " + ", ".join(STEPS))
    parser.add_argument("--ledger", type=Path, help="this ledger, not the settings' (a copy)")
    parser.add_argument("--settings", type=Path, default=SETTINGS_PATH)
    parser.add_argument("--by", default="", help='who: "reviewer: <who>", sponsor, or stand-in')
    parser.add_argument("--why", default="")
    parser.add_argument("--text", type=Path, help="FILE: the lesson's words")
    parser.add_argument("--setting", help="key=value it sets, or none for one only read")
    parser.add_argument("--traits", help="T,T: what it's keyed to (`traits` lists them)")
    parser.add_argument("--as", dest="became", help="what it became: skill")
    parser.add_argument("--where", help="where it is now: owner/repo#n")
    parser.add_argument("--score", choices=list(lessons.SCORES))
    args = parser.parse_args(argv)
    with Ledger(args.ledger or ledger_path(args.settings)) as ledger:
        try:
            print(run(args, ledger))
        except Refused as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
