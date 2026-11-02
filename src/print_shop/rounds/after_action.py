"""The after-action review: one more turn on a run's own session, after a person's approval.

The model writes its run's lessons while it still has its context (the Sponsor, 2026-11-02), and
scores each lesson it was given at the start. It's its own tool, `after_action`, apart from
`review.py`, which writes the folder a person prints from: the two are never the same word, in
code, in what the model's told, or in the ledger.

- It runs after the approval is recorded, so the run is approved whatever happens here. A failure
  is a row of `after_actions`, and the reviewer can enter lessons by hand (`lessons.load`).
- Only `read` and `after_action` work in it: the part is approved, and its files stay as they are.
- Each lesson names the calls that show it, from an index of the run the ledger gives, and each is
  held to the ledger before it's kept. Each is a `candidate` until a reviewer checks it.
- Nothing cuts a call or limits thinking. The review's calls have a high cap of their own
  (`Limits.after_action_calls`), as a run's have, and it's recorded.
"""

from __future__ import annotations

import json
from typing import Any

from print_shop.rounds import lessons
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.session import Ending, Judged

NAME = "after_action"
REVIEWED = "reviewed"
#: The tools that work during it: reading what's there, and the review itself.
TOOLS = ("read", NAME)
LESSON_KEYS = {"fix_shape", "kind", "text", "calls", "readings"}
SCORE_KEYS = {"lesson", "score", "calls", "why"}


def spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NAME,
            "description": "Your after-action review, after the approval: what this run taught, "
            "for the next run, and whether you used each lesson you were given at the start. A "
            "reviewer checks each lesson against the ledger before any reaches another run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lessons": {
                        "type": "array",
                        "description": 'Each lesson: {"fix_shape": "the fix\'s shape, in a few '
                        'words of your own", "kind": "build" (how to build), "check" (what to '
                        'check) or "context" (about the job or the people), "text": "what to tell '
                        'the next run", "calls": [the call numbers that show it, from the index], '
                        '"readings": [{"round": 1, "need": "gap"}] (optional)}. An empty list if '
                        "this run taught nothing.",
                        "items": {"type": "object"},
                    },
                    "scores": {
                        "type": "array",
                        "description": "One for each lesson you were given at the start: "
                        '{"lesson": its number, "score": "used", "not_used" or "in_the_way", '
                        '"calls": [the calls that show it], "why": "in a line"}. An empty list if '
                        "you were given none.",
                        "items": {"type": "object"},
                    },
                },
                "required": ["lessons", "scores"],
            },
        },
    }


def index(ledger: DesignLedger, run: str, upto: int | None = None) -> list[str]:
    """The run as the ledger has it, a line a call: the tools it called, each bench answer, what
    a person sent before it, and each round's readings where the round ended. It's what a lesson's
    calls are named from."""
    ended = {
        int(r["last_call"]): int(r["round"])
        for r in ledger.rows(
            "SELECT round, last_call FROM rounds WHERE run=? AND last_call IS NOT NULL", run
        )
    }
    lines: list[str] = []
    for call in ledger.rows("SELECT n, tools FROM calls WHERE run=? ORDER BY n", run):
        n = int(call["n"])
        if upto is not None and n > upto:
            break
        said = [
            f"{i['kind']} from {i['from_who']}"
            for i in ledger.rows(
                "SELECT kind, from_who FROM interventions WHERE run=? AND at_call=? ORDER BY seq",
                run,
                n,
            )
        ]
        if said:
            lines.append(f"(before call {n}: {'; '.join(said)})")
        tools = json.loads(call["tools"] or "[]")
        benched = [
            f"`{' '.join(json.loads(b['argv']))}` {b['verdict'] or 'no verdict'}"
            for b in ledger.rows(
                "SELECT argv, verdict FROM bench_calls WHERE run=? AND at_call=? ORDER BY id",
                run,
                n,
            )
        ]
        named = [t for t in tools if t != "bench"]
        parts = [", ".join(named)] if named else []
        parts += [f"bench {b}" for b in benched]
        lines.append(f"{n}: {'; '.join(parts) or 'no tool'}")
        if n in ended:
            readings = ledger.rows(
                "SELECT need, passed FROM readings WHERE run=? AND round=? ORDER BY need",
                run,
                ended[n],
            )
            read = ", ".join(
                f"{r['need']} {'passed' if r['passed'] else 'FAILED'}" for r in readings
            )
            lines.append(f"   round {ended[n]}'s readings: {read or 'none'}")
    return lines


def ask(ledger: DesignLedger, run: str, upto: int) -> str:
    """What the model is asked, with the lessons it was given and the run's index."""
    given = lessons.given_to(ledger, run)
    if given:
        lines = [f"- Lesson {g['lesson']}, given {g['how']}: {g['text']}" for g in given]
        was_given = "The lessons you were given at the start, to score:\n" + "\n".join(lines)
    else:
        was_given = "You were given no lessons at the start, so `scores` is an empty list."
    calls = "\n".join(index(ledger, run, upto))
    return (
        "The part is approved. Before this run ends, write your after-action review with "
        "`after_action`, while the whole run is still in your mind. It's for the next run, which "
        "starts with only what a reviewer keeps.\n\n"
        "- **Lessons:** what you'd tell a designer starting a part like this, from what happened "
        "here: what cost you calls, what worked, and what you'd do first next time. Each has the "
        "fix's shape in a few words of your own, its kind (`build`: how to build; `check`: what "
        "to check; `context`: about the job or the people), its text, and the calls that show "
        "it, by their numbers below, with any readings by round and need. A lesson about "
        "checking doesn't reach the next run as a note: a reviewer may make it a check in the "
        "harness instead.\n"
        "- **Scores:** for each lesson you were given, whether you used it, didn't, or it got in "
        "the way, with the calls that show it.\n\n"
        "A reviewer checks each against the ledger. Only `read` and `after_action` work now: the "
        "part is approved, and its files stay as they are.\n\n"
        f"{was_given}\n\nThis run, call by call, as the ledger has it:\n{calls}"
    )


def take(ledger: DesignLedger, run: str, args: dict[str, Any], at_call: int) -> Judged:
    """The review, held to the ledger: every lesson's calls and readings are the run's, and every
    lesson given is scored once. All of it is kept, or none, with what's wrong said."""
    given = {int(g["lesson"]) for g in lessons.given_to(ledger, run)}
    wrong: list[str] = []
    kept: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for i, lesson in enumerate(args["lessons"], 1):
        if not isinstance(lesson, dict):
            wrong.append(f"lesson {i} isn't an object")
            continue
        unknown = set(lesson) - LESSON_KEYS
        missing = {"fix_shape", "kind", "text", "calls"} - set(lesson)
        if unknown or missing:
            wrong.append(
                f"lesson {i} takes fix_shape, kind, text, calls and perhaps readings"
                + (f": it lacks {', '.join(sorted(missing))}" if missing else "")
                + (f": not {', '.join(sorted(unknown))}" if unknown else "")
            )
            continue
        if lesson["kind"] not in lessons.KINDS:
            wrong.append(f"lesson {i}'s kind is build, check or context: not {lesson['kind']!r}")
        for key in ("fix_shape", "text"):
            if not isinstance(lesson[key], str) or not lesson[key].strip():
                wrong.append(f"lesson {i} needs its {key.replace('_', ' ')}, in words")
        held, bad = lessons.evidence(ledger, run, lesson["calls"], lesson.get("readings"))
        wrong += [f"lesson {i}: {w}" for w in bad]
        kept.append((lesson, held))
    scored: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[int] = set()
    for i, one in enumerate(args["scores"], 1):
        if not isinstance(one, dict) or not {"lesson", "score"} <= set(one) <= SCORE_KEYS:
            wrong.append(f"score {i} takes lesson, score, calls and why")
            continue
        number = one["lesson"]
        if not isinstance(number, int) or number not in given:
            named = ", ".join(map(str, sorted(given))) or "none"
            wrong.append(f"score {i}: you were given lessons {named}, not {number!r}")
            continue
        if number in seen:
            wrong.append(f"lesson {number} is scored twice")
        seen.add(number)
        if one["score"] not in lessons.SCORES:
            wrong.append(f"score {i} is used, not_used or in_the_way: not {one['score']!r}")
        needs_calls = one["score"] != "not_used"
        held, bad = lessons.evidence(ledger, run, one.get("calls") or [], at_least_one=needs_calls)
        wrong += [f"score {i}: {w}" for w in bad]
        if one.get("why") is not None and not isinstance(one["why"], str):
            wrong.append(f"score {i}'s `why` is words")
        scored.append((one, held))
    unscored = sorted(given - seen)
    if unscored:
        wrong.append(f"lessons {', '.join(map(str, unscored))} were given and aren't scored")
    if wrong:
        said = "\n".join(f"- {w}" for w in wrong)
        return Judged(NAME, "failed", f"Nothing was kept:\n{said}", "misses")
    ids = [
        lessons.write(
            ledger,
            run,
            written_by=lessons.MODEL,
            fix_shape=lesson["fix_shape"],
            kind=lesson["kind"],
            text=lesson["text"],
            held=held,
            at_call=at_call,
        )
        for lesson, held in kept
    ]
    for one, held in scored:
        lessons.score(
            ledger, run, one["lesson"], one["score"], held, lessons.MODEL, one.get("why"), at_call
        )
    named = ", ".join(map(str, ids)) or "none"
    reply = (
        f"Kept for the reviewer: lessons {named}, and {len(scored)} scores. Nothing reaches "
        "another run until a reviewer accepts it. That's the end of this run."
    )
    outcome: dict[str, Any] = {"lessons": ids, "scores": len(scored)}
    return Judged(NAME, "ok", reply, detail=outcome, ending=Ending(REVIEWED, outcome))
