"""Lessons: what a run taught, checked by a reviewer, and given to the next run at its start.

The first of M18's learn loop in code (mqucifer/print-shop#28, ADR 0010's decision 5 as changed on
2026-11-02). Before this, the operator wrote lessons in a run's analysis and copied them into the
next run's request by hand. On `cup-1` the two about how to build were followed, and the one about
checking held until a check got in the way, and then the check was changed. So:

- **The model writes its run's lessons** at the end, while it still has its context, in its
  after-action review (`after_action.py`). Each is a `candidate`, keyed to the ledger's calls and
  readings, and a person can write one too.
- **A reviewer checks each against the ledger** (the operator, for now): `accept`, `edit` or
  `reject`, signed `reviewer: <who>`. A lesson about checking is never given as a note: it's
  accepted only as what it `becomes`, a refusal or a bench check, which is built in the harness.
- **`start` gives every kept lesson about how to build** to every run, in its first message, under
  `HEADING`, each saying where it came from, and `run_lessons` has each one given. Matching by part
  comes with more lessons.
- **Each run scores what it was given**: used, not used, or got in the way. A lesson used is
  `held`, one in the way `broken`. One given twice and not used, or that got in the way, is shown
  to the reviewer to retire.

Every change to a lesson is a row of `lesson_steps`, with who and why, and an edit keeps the words
it replaced. Looking a lesson up when a reading fails comes later.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from print_shop.rounds.judge import SPONSOR, STAND_IN
from print_shop.rounds.ledger import DesignLedger, now

MODEL = "model"
#: Whoever checks lessons signs as this: the operator, for now. It's for lesson steps only, since a
#: reviewer isn't standing in for the Sponsor's taste, and picks and decisions don't take it.
REVIEWER = "reviewer:"
KINDS = {"build": "how to build", "check": "what to check", "context": "context"}
SCORES = {"used": "used", "not_used": "not used", "in_the_way": "got in the way"}
#: What a lesson about checking becomes, instead of a note.
BECOMES = ("refusal", "bench_check")
#: What a lesson became, once promoted.
BECAME = ("note", "skill", "bench_check", "refusal")

CANDIDATE = "candidate"
ACCEPTED = "accepted"
EDITED = "edited"
REJECTED = "rejected"
GIVEN = "given"
HELD = "held"
BROKEN = "broken"
PROMOTED = "promoted"
RETIRED = "retired"
#: Let through by a reviewer, and still live: given since, perhaps, and scored.
KEPT = (ACCEPTED, EDITED, GIVEN, HELD, BROKEN)
#: How a run's score moves a lesson it was given. Not using it says nothing of whether it holds.
AFTER_SCORE = {"used": HELD, "in_the_way": BROKEN}
#: How a lesson reached a run: in its first message, by code.
AT_START = "start"
#: Given twice and not used, it's shown to the reviewer to retire.
UNUSED_TO_RETIRE = 2

HEADING = "## Lessons from earlier runs"
ABOUT = (
    "Each of these was learned on an earlier run, written down after it, and kept by a reviewer. "
    "They're about how to build. Use one where it fits your part, and leave it where it doesn't: "
    "at the end you'll be asked which you used."
)


class Refused(RuntimeError):
    """A reviewer's step, or a lesson, that can't be taken, said in plain words. Nothing changed."""


def signed(by: str, what: str = "--by") -> str:
    """Who did it: a reviewer, named, or as a pick is signed, the Sponsor or someone standing in
    for them, named."""
    by = by.strip()
    named = any(by.startswith(p) and by[len(p) :].strip() for p in (REVIEWER, STAND_IN))
    if by != SPONSOR and not named:
        raise Refused(
            f'{what} says who: "{REVIEWER} <who>" for whoever checks lessons, "{SPONSOR}" only '
            f'if it\'s them, or "{STAND_IN} <who>" for anyone standing in for them. Not {by!r}'
        )
    return by


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def one(ledger: DesignLedger, lesson: int) -> dict[str, Any]:
    found = ledger.rows("SELECT * FROM lessons WHERE id=?", lesson)
    if not found:
        raise Refused(f"there's no lesson {lesson}")
    return dict(found[0])


def _step(
    ledger: DesignLedger,
    lesson: int,
    status: str,
    by: str,
    why: str | None = None,
    was: str | None = None,
    run: str | None = None,
) -> None:
    ledger.insert(
        "lesson_steps",
        lesson=lesson,
        status=status,
        by=by,
        why=why,
        was=was,
        run=run,
        at=now(),
    )


def _status(ledger: DesignLedger, lesson: int, status: str, by: str) -> None:
    ledger.change(
        "UPDATE lessons SET status=?, status_by=?, status_at=? WHERE id=?",
        status,
        by,
        now(),
        lesson,
    )


# Evidence: the ledger's own keys, so a reviewer can read each one back


def evidence(
    ledger: DesignLedger, run: str, calls: Any, readings: Any = None, *, at_least_one: bool = True
) -> tuple[dict[str, Any], list[str]]:
    """Calls by number and readings by round and need, each held to what the ledger has of the
    run: what's wrong, said, or the evidence as it's kept."""
    wrong: list[str] = []
    if not isinstance(calls, list) or not all(
        isinstance(c, int) and not isinstance(c, bool) for c in calls
    ):
        return {}, ["`calls` is a list of call numbers"]
    readings = readings or []
    if not isinstance(readings, list) or not all(
        isinstance(r, dict)
        and set(r) == {"round", "need"}
        and isinstance(r["round"], int)
        and isinstance(r["need"], str)
        for r in readings
    ):
        return {}, ['`readings` is a list of {"round": N, "need": "id"}']
    if at_least_one and not calls:
        wrong.append("it needs the calls that show it")
    held = {int(r["n"]) for r in ledger.rows("SELECT n FROM calls WHERE run=?", run)}
    missing = sorted(set(calls) - held)
    if missing:
        wrong.append(
            f"the ledger has no call {', '.join(map(str, missing))} of `{run}`: it has calls 1 "
            f"to {max(held, default=0)}"
        )
    for reading in readings:
        if not ledger.rows(
            "SELECT 1 FROM readings WHERE run=? AND round=? AND need=?",
            run,
            reading["round"],
            reading["need"],
        ):
            wrong.append(
                f"the ledger has no reading of `{reading['need']}` in round {reading['round']}"
            )
    found = {"calls": sorted(set(calls)), "readings": readings}
    return found, wrong


def evidence_lines(ledger: DesignLedger, run: str, held: dict[str, Any]) -> list[str]:
    """What the ledger holds at each call and reading a lesson names, for the reviewer to judge it
    by: the tools called, each bench answer, what a person sent, and each round's readings."""
    lines: list[str] = []
    for n in held.get("calls") or []:
        call = ledger.rows("SELECT tools, round FROM calls WHERE run=? AND n=?", run, n)
        if not call:
            lines.append(f"call {n}: not in the ledger")
            continue
        tools = ", ".join(json.loads(call[0]["tools"] or "[]")) or "no tool"
        lines.append(f"call {n} (round {call[0]['round']}): {tools}")
        for b in ledger.rows(
            "SELECT argv, verdict, why FROM bench_calls WHERE run=? AND at_call=? ORDER BY id",
            run,
            n,
        ):
            argv = " ".join(json.loads(b["argv"]))
            why = (b["why"] or "")[:160]
            lines.append(f"  bench `{argv}`: {b['verdict'] or 'no verdict'}, {why}")
        for i in ledger.rows(
            "SELECT kind, from_who FROM interventions WHERE run=? AND at_call=? ORDER BY seq",
            run,
            n,
        ):
            lines.append(f"  before it: {i['kind']} from {i['from_who']}")
        for t in ledger.rows(
            "SELECT need, passed, why FROM tries WHERE run=? AND at_call=? ORDER BY seq", run, n
        ):
            mark = "would pass" if t["passed"] else "wouldn't pass"
            lines.append(f"  tried `{t['need']}`: {mark}, {t['why'][:160]}")
    for reading in held.get("readings") or []:
        found = ledger.rows(
            "SELECT passed, why FROM readings WHERE run=? AND round=? AND need=?",
            run,
            reading["round"],
            reading["need"],
        )
        if not found:
            lines.append(f"round {reading['round']}, `{reading['need']}`: not in the ledger")
            continue
        mark = "passed" if found[0]["passed"] else "FAILED"
        lines.append(
            f"round {reading['round']}, `{reading['need']}`: {mark}, {found[0]['why'][:200]}"
        )
    return lines


# Writing one, and the reviewer's steps


def write(
    ledger: DesignLedger,
    run: str,
    *,
    written_by: str,
    fix_shape: str,
    kind: str,
    text: str,
    held: dict[str, Any],
    at_call: int | None = None,
) -> int:
    """A candidate: nothing reaches a run until a reviewer has let it through."""
    if kind not in KINDS:
        raise Refused(f"a lesson's kind is {', '.join(KINDS)}: not {kind!r}")
    if not fix_shape.strip() or not text.strip():
        raise Refused("a lesson needs its fix's shape and its text")
    if not ledger.rows("SELECT 1 FROM runs WHERE run=?", run):
        raise Refused(f"`{run}` isn't in this ledger")
    lesson = ledger.insert(
        "lessons",
        run=run,
        written_by=written_by,
        written_at=now(),
        at_call=at_call,
        fix_shape=fix_shape.strip(),
        kind=kind,
        text=text.strip(),
        evidence=_json(held),
        status=CANDIDATE,
        status_by=written_by,
        status_at=now(),
    )
    _step(ledger, lesson, CANDIDATE, written_by, "written", run=run)
    return lesson


def accept(ledger: DesignLedger, lesson: int, by: str, becomes: str | None = None) -> None:
    """Let through. A lesson about checking only as a refusal or a bench check, never a note."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (CANDIDATE, REJECTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a candidate is accepted")
    becomes = _becomes(found["kind"], becomes, lesson)
    ledger.change("UPDATE lessons SET becomes=? WHERE id=?", becomes, lesson)
    _status(ledger, lesson, ACCEPTED, by)
    _step(ledger, lesson, ACCEPTED, by, f"becomes {becomes}" if becomes else None)


def _becomes(kind: str, becomes: str | None, lesson: int) -> str | None:
    if kind == "check" and becomes not in BECOMES:
        raise Refused(
            f"lesson {lesson} is about checking, which is never given as a note: say what it "
            f"becomes, {' or '.join(BECOMES)} (`--becomes`), and build it in the harness"
        )
    if kind != "check" and becomes is not None:
        raise Refused(f"only a lesson about checking becomes a check: lesson {lesson} is {kind}")
    return becomes


def edit(
    ledger: DesignLedger,
    lesson: int,
    by: str,
    *,
    text: str | None = None,
    kind: str | None = None,
    fix_shape: str | None = None,
    becomes: str | None = None,
    why: str = "",
) -> None:
    """The reviewer's own words, kept beside the ones they replace. A candidate edited is let
    through as edited. A lesson made one about checking says what it becomes."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] in (REJECTED, RETIRED):
        raise Refused(f"lesson {lesson} is {found['status']}")
    if text is None and kind is None and fix_shape is None:
        raise Refused("an edit changes the text, the kind or the fix's shape")
    if kind is not None and kind not in KINDS:
        raise Refused(f"a lesson's kind is {', '.join(KINDS)}: not {kind!r}")
    new_kind = kind or found["kind"]
    if kind is None and becomes is None:
        becomes = found["becomes"]
    becomes = _becomes(new_kind, becomes, lesson)
    was = {k: found[k] for k in ("text", "kind", "fix_shape", "becomes")}
    ledger.change(
        "UPDATE lessons SET text=?, kind=?, fix_shape=?, becomes=? WHERE id=?",
        (text or found["text"]).strip(),
        new_kind,
        (fix_shape or found["fix_shape"]).strip(),
        becomes,
        lesson,
    )
    if found["status"] in (CANDIDATE, ACCEPTED, EDITED):
        _status(ledger, lesson, EDITED, by)
    _step(ledger, lesson, EDITED, by, why.strip() or None, was=_json(was))


def reject(ledger: DesignLedger, lesson: int, by: str, why: str) -> None:
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (CANDIDATE, ACCEPTED, EDITED):
        raise Refused(f"lesson {lesson} is {found['status']}: it's retired, not rejected, now")
    if not why.strip():
        raise Refused("a rejection says why: it's what the next review learns from")
    _status(ledger, lesson, REJECTED, by)
    _step(ledger, lesson, REJECTED, by, why.strip())


def retire(ledger: DesignLedger, lesson: int, by: str, why: str) -> None:
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (*KEPT, PROMOTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a kept lesson is retired")
    if not why.strip():
        raise Refused("retiring one says why")
    _status(ledger, lesson, RETIRED, by)
    _step(ledger, lesson, RETIRED, by, why.strip())


def promote(ledger: DesignLedger, lesson: int, by: str, became: str, where: str) -> None:
    """What it became, and where that is (`owner/repo#n`, or a path). A lesson about checking
    becomes only what it was accepted as becoming, a refusal or a bench check."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in KEPT:
        raise Refused(f"lesson {lesson} is {found['status']}: only a kept lesson is promoted")
    if became not in BECAME:
        raise Refused(f"a lesson becomes {', '.join(BECAME)}: not {became!r}")
    if found["kind"] == "check" and became not in BECOMES:
        raise Refused(f"lesson {lesson} is about checking: it becomes {' or '.join(BECOMES)}")
    if not where.strip():
        raise Refused("say where it is now: `owner/repo#n`, or a path")
    ledger.change(
        "UPDATE lessons SET became=?, became_where=? WHERE id=?", became, where.strip(), lesson
    )
    _status(ledger, lesson, PROMOTED, by)
    _step(ledger, lesson, PROMOTED, by, f"became {became}: {where.strip()}")


# Giving them to a run, and scoring them


def to_give(ledger: DesignLedger) -> list[dict[str, Any]]:
    """Every kept lesson about how to build, oldest first, and one promoted to a note. A lesson
    about checking is never among them, whatever its status."""
    found = ledger.rows(
        f"SELECT * FROM lessons WHERE kind='build' AND (status IN ({','.join('?' * len(KEPT))}) "  # noqa: S608
        "OR (status=? AND became='note')) ORDER BY id",
        *KEPT,
        PROMOTED,
    )
    return [dict(r) for r in found]


def writer(written_by: str) -> str:
    """Who wrote a lesson, as a sentence names them."""
    if written_by == MODEL:
        return "the model"
    if written_by == SPONSOR:
        return "the Sponsor"
    return written_by.removeprefix(REVIEWER).removeprefix(STAND_IN).strip()


def line(found: dict[str, Any]) -> str:
    """One lesson as a run is given it, with where it came from."""
    who = writer(found["written_by"])
    return f"- **Lesson {found['id']}**, from `{found['run']}`, written by {who}: {found['text']}"


def block(given: list[dict[str, Any]]) -> str:
    """The lessons under their fixed heading, for the first message."""
    if not given:
        return ""
    return "\n".join([HEADING, "", ABOUT, "", *(line(g) for g in given)])


def give(
    ledger: DesignLedger,
    run: str,
    given: Iterable[dict[str, Any]],
    how: str = AT_START,
    by: str = "the harness",
    texts: dict[int, str] | None = None,
) -> None:
    """A row of `run_lessons` for each, in the words it was given in, and each the first time
    it's given is `given`. Only `start` gives one by code, and never one about checking: `how`
    otherwise records one given by hand before there was code for it."""
    for found in given:
        if how == AT_START and found["kind"] != "build":
            raise Refused(f"lesson {found['id']} is {found['kind']}: only how to build is given")
        text = (texts or {}).get(found["id"]) or line(found)
        ledger.add("run_lessons", run=run, lesson=found["id"], given_at=now(), how=how, text=text)
        if found["status"] in (ACCEPTED, EDITED):
            _status(ledger, found["id"], GIVEN, by)
        _step(ledger, found["id"], GIVEN, by, how, run=run)


def given_to(ledger: DesignLedger, run: str) -> list[dict[str, Any]]:
    found = ledger.rows(
        "SELECT run_lessons.*, lessons.kind, lessons.status FROM run_lessons JOIN lessons "
        "ON lessons.id = run_lessons.lesson WHERE run_lessons.run=? ORDER BY lesson",
        run,
    )
    return [dict(r) for r in found]


def score(
    ledger: DesignLedger,
    run: str,
    lesson: int,
    scored: str,
    held: dict[str, Any],
    by: str,
    why: str | None = None,
    at_call: int | None = None,
) -> None:
    """Whether a run used a lesson it was given. The latest score moves a kept lesson: used is
    held, in the way is broken."""
    if scored not in SCORES:
        raise Refused(f"a score is {', '.join(SCORES)}: not {scored!r}")
    if not ledger.rows("SELECT 1 FROM run_lessons WHERE run=? AND lesson=?", run, lesson):
        raise Refused(f"`{run}` wasn't given lesson {lesson}")
    ledger.insert(
        "lesson_scores",
        run=run,
        lesson=lesson,
        score=scored,
        calls=_json(held.get("calls") or []),
        why=(why or "").strip() or None,
        by=by,
        at_call=at_call,
        at=now(),
    )
    found = one(ledger, lesson)
    moved = AFTER_SCORE.get(scored)
    if moved and found["status"] in (GIVEN, HELD, BROKEN):
        _status(ledger, lesson, moved, by)
        _step(ledger, lesson, moved, by, f"scored {scored}", run=run)


def latest_scores(ledger: DesignLedger, lesson: int) -> dict[str, dict[str, Any]]:
    """Each run's latest score of a lesson, by run."""
    found: dict[str, dict[str, Any]] = {}
    for row in ledger.rows("SELECT * FROM lesson_scores WHERE lesson=? ORDER BY id", lesson):
        found[str(row["run"])] = dict(row)
    return found


def to_retire(ledger: DesignLedger) -> list[tuple[dict[str, Any], str]]:
    """Each live lesson given twice and not used, or that got in the way, and why."""
    shown: list[tuple[dict[str, Any], str]] = []
    for row in ledger.rows(
        f"SELECT * FROM lessons WHERE status IN ({','.join('?' * len(KEPT))}) "  # noqa: S608
        "OR (status=? AND became='note') ORDER BY id",
        *KEPT,
        PROMOTED,
    ):
        scores = latest_scores(ledger, int(row["id"]))
        in_the_way = sorted(r for r, s in scores.items() if s["score"] == "in_the_way")
        unused = sorted(r for r, s in scores.items() if s["score"] == "not_used")
        if in_the_way:
            shown.append((dict(row), f"got in the way on {', '.join(in_the_way)}"))
        elif len(unused) >= UNUSED_TO_RETIRE:
            shown.append((dict(row), f"given and not used on {', '.join(unused)}"))
    return shown


# What the reviewer reads


def listing(ledger: DesignLedger, run: str | None = None) -> str:
    """With a run: the lessons it wrote, each with its evidence as the ledger has it, and the
    lessons it was given with their scores. With none: every lesson in a line. Either way, any
    to retire."""
    lines: list[str] = []
    if run is not None:
        if not ledger.rows("SELECT 1 FROM runs WHERE run=?", run):
            raise Refused(f"`{run}` isn't in this ledger")
        lines += _run_listing(ledger, run)
    else:
        lines += ["# Every lesson", ""]
        for row in ledger.rows("SELECT * FROM lessons ORDER BY id"):
            lines.append(
                f"- {row['id']}. [{row['status']}] {KINDS[row['kind']]}, from `{row['run']}`: "
                f"{row['text'][:160]}"
            )
        if len(lines) == 2:
            lines.append("None yet.")
        lines.append("")
    retiring = to_retire(ledger)
    if retiring:
        lines += ["## To retire, for the reviewer", ""]
        lines += [f"- Lesson {row['id']}: {why}. `retire {row['id']}`" for row, why in retiring]
        lines.append("")
    return "\n".join(lines)


def _run_listing(ledger: DesignLedger, run: str) -> list[str]:
    lines = [f"# Lessons from `{run}`", ""]
    reviews = ledger.rows(
        "SELECT a.*, d.decision AS followed, d.by AS decided_by FROM after_actions AS a "
        "LEFT JOIN decisions AS d ON d.id = a.decision WHERE a.run=? ORDER BY a.n",
        run,
    )
    for r in reviews:
        ended = r["ended"] or "unfinished"
        if r["followed"]:
            after = f"after {r['followed']} by {r['decided_by']}"
        else:
            after = f"after approval {r['approval']}"
        lines.append(
            f"After-action review {r['n']} ({after}): {ended}, from call "
            f"{r['first_call']} to {r['last_call'] or '?'}, {r['ended_said'] or ''}".rstrip(", ")
        )
    if reviews:
        lines.append("")
    written = ledger.rows("SELECT * FROM lessons WHERE run=? ORDER BY id", run)
    if not written:
        lines += ["It has written none.", ""]
    for row in written:
        becomes = f", becomes {row['becomes']}" if row["becomes"] else ""
        lines += [
            f"## Lesson {row['id']}: {row['status']}",
            "",
            f"- **Kind:** {KINDS[row['kind']]}{becomes}",
            f"- **The fix's shape, in its writer's words:** {row['fix_shape']}",
            f"- **Written by** {row['written_by']}"
            + (f", at call {row['at_call']}" if row["at_call"] else ""),
            "",
            row["text"],
            "",
            "What the ledger holds where it points:",
            "",
        ]
        held = json.loads(row["evidence"])
        lines += [f"    {e}" for e in evidence_lines(ledger, run, held)] or ["    nothing"]
        lines.append("")
    given = given_to(ledger, run)
    if given:
        lines += ["## What it was given", ""]
        for g in given:
            scores = ledger.rows(
                "SELECT * FROM lesson_scores WHERE run=? AND lesson=? ORDER BY id", run, g["lesson"]
            )
            said = "; ".join(_score_said(dict(s)) for s in scores)
            lines.append(
                f"- Lesson {g['lesson']} ({g['how']}, now {g['status']}): {said or 'not scored'}"
            )
        lines.append("")
    return lines


def _score_said(found: dict[str, Any]) -> str:
    calls = json.loads(found["calls"])
    shown = f" (calls {', '.join(map(str, calls))})" if calls else ""
    why = f": {found['why']}" if found["why"] else ""
    return f"{SCORES[found['score']]} by {found['by']}{shown}{why}"


# Lessons a person writes, from a file


def load(ledger: DesignLedger, path: Path, by: str) -> dict[str, int]:
    """A reviewer's steps from a JSON file, in order, by the same rules as at the command line:
    `{"steps": [{"do": "write", "key": "a", "run": ..., "fix_shape": ..., "kind": ..., "text":
    ..., "calls": [...], "readings": [...]}, {"do": "accept", "lesson": "a", "becomes": ...},
    {"do": "give", "lesson": "a", "run": ..., "how": ..., "text": ...}, {"do": "score", ...},
    {"do": "edit" | "reject" | "retire" | "promote", ...}]}`. A lesson is named by its `key` in
    the file. It's all checked before anything is written, and refused whole if a lesson's text
    is already in the ledger for its run, so it can't be loaded twice."""
    by = signed(by)
    try:
        steps = json.loads(path.read_text(encoding="utf-8"))["steps"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Refused(f'{path}: a file of steps, {{"steps": [...]}}: {exc}') from exc
    wrong = _check_file(ledger, steps)
    if wrong:
        raise Refused(f"{path}:\n" + "\n".join(f"- {w}" for w in wrong))
    ids: dict[str, int] = {}
    for step in steps:
        do = step["do"]
        if do == "write":
            held, _ = evidence(ledger, step["run"], step.get("calls") or [], step.get("readings"))
            ids[step["key"]] = write(
                ledger,
                step["run"],
                written_by=by,
                fix_shape=step["fix_shape"],
                kind=step["kind"],
                text=step["text"],
                held=held,
            )
            continue
        lesson = ids[step["lesson"]]
        if do == "accept":
            accept(ledger, lesson, by, step.get("becomes"))
        elif do == "edit":
            edit(
                ledger,
                lesson,
                by,
                text=step.get("text"),
                kind=step.get("kind"),
                fix_shape=step.get("fix_shape"),
                becomes=step.get("becomes"),
                why=step.get("why", ""),
            )
        elif do == "reject":
            reject(ledger, lesson, by, step["why"])
        elif do == "give":
            found = one(ledger, lesson)
            give(ledger, step["run"], [found], step["how"], by, {lesson: step["text"]})
        elif do == "score":
            held, _ = evidence(ledger, step["run"], step.get("calls") or [], at_least_one=False)
            score(ledger, step["run"], lesson, step["score"], held, by, step.get("why"))
        elif do == "retire":
            retire(ledger, lesson, by, step["why"])
        elif do == "promote":
            promote(ledger, lesson, by, step["as"], step["where"])
    return ids


STEP_KEYS = {
    "write": ({"key", "run", "fix_shape", "kind", "text", "calls"}, {"readings"}),
    "accept": ({"lesson"}, {"becomes"}),
    "edit": ({"lesson"}, {"text", "kind", "fix_shape", "becomes", "why"}),
    "reject": ({"lesson", "why"}, set()),
    "give": ({"lesson", "run", "how", "text"}, set()),
    "score": ({"lesson", "run", "score"}, {"calls", "why"}),
    "retire": ({"lesson", "why"}, set()),
    "promote": ({"lesson", "as", "where"}, set()),
}


def _check_file(ledger: DesignLedger, steps: Any) -> list[str]:
    """What's wrong with a file of steps, before any is taken."""
    if not isinstance(steps, list) or not steps:
        return ["`steps` is a list, with at least one"]
    wrong: list[str] = []
    keys: set[str] = set()
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) or step.get("do") not in STEP_KEYS:
            wrong.append(f"step {i}: `do` is one of {', '.join(STEP_KEYS)}")
            continue
        needed, optional = STEP_KEYS[step["do"]]
        given = set(step) - {"do"}
        if needed - given or given - needed - optional:
            wrong.append(
                f"step {i} ({step['do']}): it takes {', '.join(sorted(needed))}"
                + (f", and perhaps {', '.join(sorted(optional))}" if optional else "")
                + f": not {', '.join(sorted(given))}"
            )
            continue
        if step["do"] == "write":
            if step["key"] in keys:
                wrong.append(f"step {i}: two lessons are `{step['key']}`")
            keys.add(step["key"])
            if not ledger.rows("SELECT 1 FROM runs WHERE run=?", step["run"]):
                wrong.append(f"step {i}: `{step['run']}` isn't in this ledger")
                continue
            if step["kind"] not in KINDS:
                wrong.append(f"step {i}: a kind is {', '.join(KINDS)}")
            if not str(step["fix_shape"]).strip() or not str(step["text"]).strip():
                wrong.append(f"step {i}: it needs its fix's shape and its text")
            _, bad = evidence(ledger, step["run"], step["calls"], step.get("readings"))
            wrong += [f"step {i}: {w}" for w in bad]
            if ledger.rows(
                "SELECT id FROM lessons WHERE run=? AND text=?", step["run"], step["text"].strip()
            ):
                wrong.append(f"step {i}: `{step['key']}` is in the ledger already")
        elif step["lesson"] not in keys:
            wrong.append(f"step {i}: `{step['lesson']}` isn't a lesson written above it")
        elif "run" in step:
            if not ledger.rows("SELECT 1 FROM runs WHERE run=?", step["run"]):
                wrong.append(f"step {i}: `{step['run']}` isn't in this ledger")
            elif step["do"] == "score":
                _, bad = evidence(ledger, step["run"], step.get("calls") or [], at_least_one=False)
                wrong += [f"step {i}: {w}" for w in bad]
    return wrong
