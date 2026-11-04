"""The after-action review: one more turn on a run's own session, after a person's decision.

The model writes its run's lessons while it still has its context (the Sponsor, 2026-11-02), and
scores each lesson it was given at the start. It follows an approval, or a close: a run that ended
without approval gets one too once a person closes it (mqucifer/print-shop#32), as "It still has
learnings" (the Sponsor, 2026-11-03). Then it's asked what didn't work and why, what would have
changed it, and what it would do first next time. It's its own tool, `after_action`, apart from
`review.py`, which writes the folder a person prints from: the two are never the same word, in
code, in what the model's told, or in the ledger.

- It runs after the decision is recorded, so the run stays approved or closed whatever happens
  here. A failure is a row of `after_actions`, and the reviewer can enter lessons by hand
  (`lessons.load`).
- Only `read` and `after_action` work in it: its files stay as they are.
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
LESSON_KEYS = {"fix_shape", "kind", "routes", "text", "calls", "readings", "keys"}
LESSON_NEEDS = {"fix_shape", "kind", "routes", "text", "calls"}
#: The five areas and the four routes, as the model is told them.
AREAS_SAID = (
    '"job" (understanding the job: the request, the references, what the part must do), '
    '"verify" (verification: checking what was made, and reading what the checks say), '
    '"process" (process control: stages, stops, asking, and when to end), "memory" (memory '
    'within a run: keeping what was learned earlier in it), or "loop" (the learning loop: how '
    "lessons and reviews work)"
)
ROUTES_SAID = (
    '"model" (text the next run is given), "harness" (a change to the harness: a refusal, a '
    'check or a stop), "request" (a line in the request the people running this write), or '
    '"operator" (a line in the checklist of the people running this and anyone standing in)'
)
SCORE_KEYS = {"lesson", "score", "calls", "why"}
PEOPLE_KEYS = {"said", "lesson", "why"}


def spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NAME,
            "description": "Your after-action review, at the end of the run: what this run "
            "taught, for the next run, and whether you used each lesson you were given at the "
            "start. A reviewer checks each lesson against the ledger before any reaches another "
            "run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lessons": {
                        "type": "array",
                        "description": 'Each lesson: {"fix_shape": "the fix\'s shape, in a few '
                        f'words of your own", "kind": its area, {AREAS_SAID}, "routes": who '
                        f"should act on it, one or more of {ROUTES_SAID}, "
                        '"text": "what to tell whoever acts on it", "calls": [the call numbers '
                        'that show it, from the index], "readings": [{"round": 1, "need": "gap"}] '
                        '(optional), "keys": [{"command": "capture", "verdict": "FAIL"}], the '
                        "checks it's about (optional: the harness reads them from its calls)}. An "
                        "empty list if this run taught nothing.",
                        "items": {"type": "object"},
                    },
                    "people": {
                        "type": "array",
                        "description": "One for each thing a person said, marked [said N] in the "
                        'index: {"said": N, "lesson": the number of the lesson in your `lessons` '
                        'that it became, from 1, or null, "why": "in a line: why it should or '
                        "shouldn't be a lesson\"}. An empty list if no one said anything.",
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
                "required": ["lessons", "scores", "people"],
            },
        },
    }


def _quoted(text: str) -> list[str]:
    return [f"    > {line}" if line.strip() else "    >" for line in text.strip().splitlines()]


def _cut_said(said: str | None) -> str:
    """What a compaction cut, from its timeline row."""
    cut = json.loads(said) if said else {}
    if not cut:
        return "what it cut isn't recorded"
    parts = []
    shrunk = cut.get("replies_shrunk") or {}
    if shrunk:
        each = ", ".join(f"{tool} {n}" for tool, n in sorted(shrunk.items()))
        parts.append(f"{sum(shrunk.values())} tool replies shrunk to a line ({each})")
    if cut.get("reasoning_dropped"):
        parts.append(f"the reasoning of {cut['reasoning_dropped']} turns dropped")
    if cut.get("texts_noted"):
        parts.append(f"{cut['texts_noted']} `write` or `edit` texts made a note")
    return "; ".join(parts) or "nothing"


def index(ledger: DesignLedger, run: str, upto: int | None = None) -> list[str]:
    """The run as the ledger has it, a line a call: the tools it called and each bench answer,
    and each round's readings where the round ended. Before a call, the words of whatever a
    person sent, each marked `[said N]`, and each compaction, with what it cut, and each new
    conversation. After it, each refusal and failed edit, nudge, runaway, and a conversation
    that wouldn't fit (mqucifer/print-shop#40). It's what a lesson's calls are named from."""
    ended = {
        int(r["last_call"]): int(r["round"])
        for r in ledger.rows(
            "SELECT round, last_call FROM rounds WHERE run=? AND last_call IS NOT NULL", run
        )
    }
    before: dict[int, list[str]] = {}
    after: dict[int, list[str]] = {}
    for t in ledger.rows(
        "SELECT kind, name, at_call, said, estimated_before, prompt_after FROM timeline "
        "WHERE run=? AND kind IN ('compaction', 'restart', 'stop') ORDER BY seq",
        run,
    ):
        at = int(t["at_call"] or 0)
        if t["kind"] == "compaction":
            size = f"at about {t['estimated_before']:,} tokens" if t["estimated_before"] else ""
            then = f", {t['prompt_after']:,} after" if t["prompt_after"] else ""
            before.setdefault(at, []).append(
                f"(before call {at}: compaction {size}{then}: {_cut_said(t['said'])})"
            )
        elif t["kind"] == "restart":
            before.setdefault(at, []).append(
                f"(before call {at}: a new conversation from the record, as the last one "
                "wouldn't fit even compacted: its reasoning and tool replies are gone)"
            )
        elif t["name"] == "context_full":
            after.setdefault(at, []).append("   it wouldn't fit even compacted: context_full")
    for i in ledger.rows(
        "SELECT seq, kind, from_who, text, at_call FROM interventions WHERE run=? ORDER BY seq",
        run,
    ):
        before.setdefault(int(i["at_call"]), []).extend(
            [f"[said {i['seq']}] before call {i['at_call']}, {i['kind']} from {i['from_who']}:"]
            + _quoted(str(i["text"]))
        )
    for r in ledger.rows(
        "SELECT at_call, tool, outcome, why FROM refusals WHERE run=? ORDER BY seq", run
    ):
        what = "a failed edit" if r["tool"] == "edit" else f"`{r['tool']}` refused"
        after.setdefault(int(r["at_call"]), []).append(
            f"   {what}: {r['why'] or r['outcome']}"
        )
    for nudged in ledger.rows(
        "SELECT at_call, command, files, failures FROM nudges WHERE run=? ORDER BY seq", run
    ):
        files = ", ".join(json.loads(nudged["files"]))
        after.setdefault(int(nudged["at_call"]), []).append(
            f"   nudged: `{nudged['command']}`{' on ' + files if files else ''} had failed "
            f"{nudged['failures']} times without passing as a need checks it"
        )
    for r in ledger.rows(
        "SELECT n, why, completion_tokens, reasoning_tokens FROM runaways WHERE run=? ORDER BY n",
        run,
    ):
        after.setdefault(int(r["n"]), []).append(
            f"   runaway ({r['why']}): {r['completion_tokens']} tokens, "
            f"{r['reasoning_tokens']} of them reasoning"
        )
    lines: list[str] = []
    for call in ledger.rows("SELECT n, tools FROM calls WHERE run=? ORDER BY n", run):
        n = int(call["n"])
        if upto is not None and n > upto:
            break
        lines += before.get(n, [])
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
        lines += after.get(n, [])
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
    # What's sent after the last call shown, such as the decision the review follows.
    shown = {int(c["n"]) for c in ledger.rows("SELECT n FROM calls WHERE run=?", run)}
    for at in sorted(before):
        if at not in shown or (upto is not None and at > upto):
            lines += before[at]
    return lines


#: How a run that was closed had ended, said to the model: a stop waits for a person, and isn't an
#: ending of its own.
STOPS = {
    "questions": "It was waiting for the answers to your questions ({n}).",
    "ask": "It was waiting at ask {n}, for a pick.",
    "met": "It was waiting at stop {n} for approval: every need had passed its check.",
}
#: What each part of a lesson is, and what's said of the scores, for either kind of review.
SHAPE = (
    "Each has the fix's shape in a few words of your own; its kind, the area of what went wrong: "
    f"{AREAS_SAID}; its routes, who should act on it: {ROUTES_SAID}; its text; and the calls that "
    "show it, by their numbers below, with any readings by round and need. A fault the harness "
    "hid from you, or a stop it should have made, is a lesson for the harness: it doesn't reach "
    "the next run as a note, and a reviewer may build it as a check or a refusal instead."
)
SCORES = (
    "- **Scores:** for each lesson you were given, whether you used it, didn't, or it got in the "
    "way, with the calls that show it."
)
PEOPLE = (
    "- **What people said:** each note, answer, pick and its reason, fix-first, send-back and "
    "decision is in the index in their words, marked [said N]. For each one, say whether it "
    "should be a lesson, and if it is, which of your lessons it became (`people`). A person's "
    "finding that never became a lesson is a lesson missed."
)
HARNESS_SAID = (
    "The index shows the harness's part too: each compaction and what it cut from what you'd "
    "seen, each new conversation after one that wouldn't fit, and each refusal, failed edit, "
    "nudge and runaway, at its call. Where the harness hid something from you, or a stop it "
    "should have made wasn't made, that's a lesson for the harness."
)


def how_it_ended(was: dict[str, Any]) -> str:
    """A closed run's ending before the close, in a line: its name and what it said."""
    name = was.get("name")
    said = was.get("said") or {}
    if name is None:
        return "Its process ended with no ending recorded."
    if name in STOPS:
        # A run from before approval was a stop ended at `met`, saying its approval.
        return STOPS[name].format(n=said.get("n", said.get("approval", "?")))
    if name == "blocked":
        return f"It ended `blocked`, with your reason: {said.get('reason', '')}"
    shown = f": {json.dumps(said, sort_keys=True)}" if said else ""
    return f"It ended `{name}`{shown}."


def ask(ledger: DesignLedger, run: str, upto: int, closed: bool = False) -> str:
    """What the model is asked, with the lessons it was given and the run's index. After a close
    it isn't told the part is approved: it's asked what didn't work and why, what would have
    changed it, and what it would do first next time."""
    given = lessons.given_to(ledger, run)
    if given:
        lines = [f"- Lesson {g['lesson']}, given {g['how']}: {g['text']}" for g in given]
        was_given = "The lessons you were given at the start, to score:\n" + "\n".join(lines)
    else:
        was_given = "You were given no lessons at the start, so `scores` is an empty list."
    calls = "\n".join(index(ledger, run, upto))
    if closed:
        opening = (
            "This run is closed without approval. Before it ends, write your after-action review "
            "with `after_action`, while the whole run is still in your mind. A run that didn't "
            "succeed has the most to teach, and a clear account of it is a good result. It's for "
            "the next run, which starts with only what a reviewer keeps.\n\n"
            "- **Lessons:** what didn't work, and why; what would have changed it: a tool's "
            "reply, a check, a lesson, the request, or a stage; and what you'd do first next "
            f"time. {SHAPE}\n"
        )
        now = "the run is closed"
    else:
        opening = (
            "The part is approved. Before this run ends, write your after-action review with "
            "`after_action`, while the whole run is still in your mind. It's for the next run, "
            "which starts with only what a reviewer keeps.\n\n"
            "- **Lessons:** what you'd tell a designer starting a part like this, from what "
            "happened here: what cost you calls, what worked, and what you'd do first next time. "
            f"{SHAPE}\n"
        )
        now = "the part is approved"
    return (
        f"{opening}{SCORES}\n{PEOPLE}\n\n{HARNESS_SAID}\n\n"
        f"A reviewer checks each against the ledger. Only `read` and `after_action` work now: "
        f"{now}, and its files stay as they are.\n\n"
        f"{was_given}\n\nThis run, call by call, as the ledger has it:\n{calls}"
    )


def _people(
    ledger: DesignLedger, run: str, given: Any, written: int
) -> tuple[list[tuple[int, int | None, str]], list[str]]:
    """Each thing a person said, weighed once: whether it became one of the review's lessons,
    and why. What's wrong, said, if any is missing or doesn't fit."""
    said = {int(r["seq"]) for r in ledger.rows("SELECT seq FROM interventions WHERE run=?", run)}
    wrong: list[str] = []
    found: list[tuple[int, int | None, str]] = []
    if given is None:
        given = []
    if not isinstance(given, list):
        return [], ["`people` is a list, one for each [said N]"]
    seen: set[int] = set()
    for i, one in enumerate(given, 1):
        if not isinstance(one, dict) or not {"said", "why"} <= set(one) <= PEOPLE_KEYS:
            wrong.append(f"people {i} takes said, lesson and why")
            continue
        n, became, why = one["said"], one.get("lesson"), one["why"]
        if not isinstance(n, int) or n not in said:
            wrong.append(f"people {i}: there's no [said {n}]")
            continue
        if n in seen:
            wrong.append(f"[said {n}] is weighed twice")
        seen.add(n)
        if became is not None and (
            not isinstance(became, int) or isinstance(became, bool) or not 1 <= became <= written
        ):
            wrong.append(
                f"people {i}'s lesson is the number of one of your {written} lessons, or null"
            )
            continue
        if not isinstance(why, str) or not why.strip():
            wrong.append(f"people {i} says why, in words")
            continue
        found.append((n, became, why.strip()))
    missing = sorted(said - seen)
    if missing:
        wrong.append(
            f"{', '.join(f'[said {n}]' for n in missing)} weren't weighed: say of each whether "
            "it should be a lesson"
        )
    return found, wrong


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
        missing = LESSON_NEEDS - set(lesson)
        if unknown or missing:
            wrong.append(
                f"lesson {i} takes fix_shape, kind, routes, text, calls, and perhaps readings and "
                "keys"
                + (f": it lacks {', '.join(sorted(missing))}" if missing else "")
                + (f": not {', '.join(sorted(unknown))}" if unknown else "")
            )
            continue
        if lesson["kind"] not in lessons.KINDS:
            wrong.append(
                f"lesson {i}'s kind is its area, {', '.join(lessons.KINDS)}: not "
                f"{lesson['kind']!r}"
            )
        try:
            lessons.check_routes(lesson["routes"], i)
            if "keys" in lesson:
                lessons.check_keys(lesson["keys"], i)
        except lessons.Refused as refused:
            wrong.append(str(refused))
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
    weighed, bad = _people(ledger, run, args.get("people"), len(args["lessons"]))
    wrong += bad
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
            routes=lesson["routes"],
            at_call=at_call,
            keys=lessons.check_keys(lesson["keys"], 0) if "keys" in lesson else None,
        )
        for lesson, held in kept
    ]
    for one, held in scored:
        lessons.score(
            ledger, run, one["lesson"], one["score"], held, lessons.MODEL, one.get("why"), at_call
        )
    for number, became, why in weighed:
        ledger.add(
            "said_weighed",
            run=run,
            said=number,
            at_call=at_call,
            lesson=ids[became - 1] if became is not None else None,
            why=why,
            by=lessons.MODEL,
        )
    named = ", ".join(map(str, ids)) or "none"
    reply = (
        f"Kept for the reviewer: lessons {named}, and {len(scored)} scores. Nothing reaches "
        "another run until a reviewer accepts it. That's the end of this run."
    )
    outcome: dict[str, Any] = {"lessons": ids, "scores": len(scored), "people": len(weighed)}
    return Judged(NAME, "ok", reply, detail=outcome, ending=Ending(REVIEWED, outcome))
