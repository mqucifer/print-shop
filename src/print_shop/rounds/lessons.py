"""Lessons: what a run taught, checked by a reviewer, and sent to whoever acts on it.

The first of M18's learn loop in code (mqucifer/print-shop#28, ADR 0010's decision 5 as changed on
2026-11-02). Before this, the operator wrote lessons in a run's analysis and copied them into the
next run's request by hand. On `cup-1` the two about how to build were followed, and the one about
checking held until a check got in the way, and then the check was changed. So:

- **The model writes its run's lessons** at the end, while it still has its context, in its
  after-action review (`after_action.py`). Each is a `candidate`, keyed to the ledger's calls and
  readings, and a person can write one too.
- **A lesson's kind is its area, one of the five** (mqucifer/print-shop#40, sorted with the Sponsor
  on 2026-11-03): what goes wrong. **Its routes say who acts on it:** the model, the harness, the
  request, or the operator and stand-in, each with its own delivery and score (`ROUTES`). The model
  proposes the routes, and the reviewer confirms or changes them.
- **A reviewer checks each against the ledger** (the operator, for now): `accept`, `edit` or
  `reject`, signed `reviewer: <who>`. A lesson for the harness is never given as a note: it's
  accepted only with what it `becomes` there, and built.
- **Lessons for the model come in layers.** `start` gives those for the request's kind of part,
  then the general ones, in the first message under `HEADING`, and `run_lessons` has each one
  given. One folded into a kind's skill is read there instead. Each is keyed to the checks it's
  about, by command and verdict (`lesson_keys`), for a look-up at the failure, which is the next
  batch. "We can't just dump everything in context" (the Sponsor, 2026-11-03).
- **The start's lessons are bounded** (mqucifer/print-shop#155). On 2026-11-17 every design run
  started with all 34 kept for the model, about 16,500 characters, as 34 of the 35 had no kind of
  part. So they're ordered (`to_give`): the kind's first, then general, and in each those scored
  `used` on most runs first and any that got in the way last. The start takes them in that order
  up to a budget in characters (`within`, `Limits.start_lessons_chars`), and says how many it left
  out. None is dropped: `run_lessons_left` has each left out, and the model's `lessons` tool
  finds any kept lesson by words or by kind (`look_up`), in a short reply, and gives one whole by
  its number, which the run is then given (`run_lessons`, `how` being `tool`) and its review
  scores. Folding a kind's lessons into its skill stays the main route: the listing names each
  kept lesson with no kind, for a reviewer to give one, and each kind's that its skill doesn't
  hold yet.
- **Each route is scored:** the model's by the run's own review, the others by the analysis after
  they're delivered (`promote`). A lesson's status follows each run's latest score on each route
  (`settle`): used is `held`, in the way `broken`, and a correction replaces that run's earlier
  score. Once its fix is built elsewhere and a run shows it holding, its text stops being given.

Every change to a lesson is a row of `lesson_steps`, with who and why, and an edit keeps the words
it replaced. Lessons from before the five are sorted once when a ledger opens (`migrate`).

**A lesson reaches another ledger by a copy** (mqucifer/print-shop#137). A run is given the
lessons of the ledger beside it, and each eval round has its own, so a lesson learned in an eval
reaches the Sponsor's design runs only if it's written into theirs. Written there, its calls were
checked against that ledger's run, which hasn't them: the only way through was to cite calls that
don't show it. So `copy` reads the lesson in the ledger it came from, read and never written,
checks its calls and readings there as `evidence` checks them, and writes it here as a
`candidate` citing that ledger's run, with where it came from in `lesson_copies`. A reviewer lets
it through on this ledger's terms, and it reaches this ledger's runs as any other lesson does. A
lesson is copied from a ledger once.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from print_shop.rounds import nudge, toolsets
from print_shop.rounds.judge import SPONSOR, STAND_IN
from print_shop.rounds.ledger import DesignLedger, now
from print_shop.rounds.session import START_LESSONS_CHARS

MODEL = "model"
HARNESS = "the harness"
#: Whoever checks lessons signs as this: the operator, for now. It's for lesson steps only, since a
#: reviewer isn't standing in for the Sponsor's taste, and picks and decisions don't take it.
REVIEWER = "reviewer:"
#: A lesson's kind: the area of what goes wrong, one of the five (mqucifer/print-shop#36 to #40).
KINDS = {
    "job": "understanding the job",
    "verify": "verification",
    "process": "process control",
    "memory": "memory within a run",
    "loop": "the learning loop",
}
#: The kinds from before the five, which `migrate` sorts each lesson from once.
OLD_KINDS = {"build": "how to build", "check": "what to check", "context": "context"}
#: Who acts on a lesson, and how each route delivers it and scores it (mqucifer/print-shop#40).
ROUTES = {
    "model": "the model: text the next run is given, scored by that run's own review",
    "harness": "the harness: an issue in the next batch, promoted when merged, scored by the "
    "next analysis",
    "request": "the request: a line in the request template the operator writes from, scored "
    "by the analysis",
    "operator": "the operator and stand-in: a line in the checklist in `docs/rounds.md`, scored "
    "by the analysis",
}
SCORES = {"used": "used", "not_used": "not used", "in_the_way": "got in the way"}
#: What a lesson for the harness becomes, instead of a note.
HARNESS_BECOMES = ("refusal", "bench_check", "toolset_check", "stop", "harness")
#: What a lesson becomes outside the model's text: a request change, or a change in the harness.
BECOMES = ("request", *HARNESS_BECOMES)
#: What a lesson became, once delivered on a route, and the route each is on.
BECAME = {
    "note": "model",
    "skill": "model",
    "request": "request",
    "checklist": "operator",
    **dict.fromkeys(HARNESS_BECOMES, "harness"),
}

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
#: Statuses a score moves: a lesson given or delivered somewhere.
SCORED = (GIVEN, HELD, BROKEN, PROMOTED)
#: How a run's score moves a lesson it was given. Not using it says nothing of whether it holds.
AFTER_SCORE = {"used": HELD, "in_the_way": BROKEN}
#: How a lesson reached a run: in its first message, by code.
AT_START = "start"
#: Or later, whole, by the `lessons` tool, for one its start left out.
AT_TOOL = "tool"
#: Given twice and not used, it's shown to the reviewer to retire.
UNUSED_TO_RETIRE = 2
#: A lesson's kind of part once a reviewer has said it's for every kind. With none, it's given as
#: a general one too, and listed for the review until it's given one (mqucifer/print-shop#155).
GENERAL = "general"
#: The characters of lessons a start holds, unless the run's limits say (`Limits`).
START_CHARS = START_LESSONS_CHARS
#: The most lessons one reply of the `lessons` tool lists, and the characters of each one's shape.
FOUND_MOST = 30
SHAPE_CHARS = 160

HEADING = "## Lessons from earlier runs"
ABOUT = (
    "Each of these was learned on an earlier run, written down after it, and kept by a reviewer. "
    "Use one where it fits your part, and leave it where it doesn't: at the end you'll be asked "
    "which you used."
)


class Refused(RuntimeError):
    """A reviewer's step, or a lesson, that can't be taken, said in plain words. Nothing changed."""


class Readable(Protocol):
    """A ledger that can be read: this one, or another a lesson is copied from."""

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]: ...


class Source:
    """Another ledger, read and never written: where a copied lesson came from
    (mqucifer/print-shop#137). It's opened read-only, so nothing here migrates or changes it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise Refused(f"there's no ledger at {self.path}")

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        db = sqlite3.connect(f"file:{quote(str(self.path))}?mode=ro", uri=True, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            return list(db.execute(sql, args))
        except sqlite3.DatabaseError as exc:
            raise Refused(f"{self.path} can't be read as a ledger: {exc}") from exc
        finally:
            db.close()


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
    ledger: Readable, run: str, calls: Any, readings: Any = None, *, at_least_one: bool = True
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


def evidence_lines(ledger: Readable, run: str, held: dict[str, Any]) -> list[str]:
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
    for finding in held.get("findings") or []:
        # A refinement pass's finding, by its row (mqucifer/print-shop#59).
        rows = ledger.rows(
            "SELECT f.kind, f.source, f.read, f.means, r.n FROM refine_findings AS f JOIN "
            "refinements AS r ON r.id = f.refinement WHERE f.id=?",
            finding,
        )
        if not rows:
            lines.append(f"refinement finding {finding}: not in the ledger")
            continue
        row = rows[0]
        lines.append(
            f"refinement {row['n']}, {row['kind']} by {row['source']}: {str(row['read'])[:200]}"
        )
    for check in held.get("end_checks") or []:
        # The harness's end checks, each command as it ran with what it read, to run again
        # (mqucifer/print-shop#150).
        lines.append(
            f"end check `{check.get('command')}`: {check.get('verdict') or 'no verdict'}, "
            f"{str(check.get('why') or '')[:300]}"
        )
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


# Routes and keys


def routes_of(ledger: Readable, lesson: int) -> dict[str, dict[str, Any]]:
    """A lesson's routes, each as `lesson_routes` holds it."""
    found = ledger.rows("SELECT * FROM lesson_routes WHERE lesson=? ORDER BY route", lesson)
    return {str(r["route"]): dict(r) for r in found}


def keys_of(ledger: Readable, lesson: int) -> list[dict[str, str]]:
    found = ledger.rows(
        "SELECT command, verdict FROM lesson_keys WHERE lesson=? ORDER BY command, verdict", lesson
    )
    return [{"command": str(r["command"]), "verdict": str(r["verdict"])} for r in found]


def check_routes(given: Any, lesson: int | str) -> tuple[str, ...]:
    if (
        not isinstance(given, list | tuple)
        or not given
        or not all(isinstance(r, str) and r in ROUTES for r in given)
    ):
        raise Refused(
            f"lesson {lesson}'s routes are one or more of {', '.join(ROUTES)}: who acts on it"
        )
    return tuple(sorted(set(given)))


def _set_routes(
    ledger: DesignLedger, lesson: int, routes: Iterable[str], by: str
) -> dict[str, Any] | None:
    """The lesson's routes, as given: a route it keeps keeps what it reached. What it had, if
    that changed."""
    had = routes_of(ledger, lesson)
    wanted = set(routes)
    if set(had) == wanted:
        return None
    for route in set(had) - wanted:
        ledger.change("DELETE FROM lesson_routes WHERE lesson=? AND route=?", lesson, route)
    for route in sorted(wanted - set(had)):
        ledger.insert("lesson_routes", lesson=lesson, route=route, by=by, at=now())
    return {"routes": sorted(had)}


def check_keys(given: Any, lesson: int | str) -> list[dict[str, str]]:
    if not isinstance(given, list) or not all(
        isinstance(k, dict)
        and set(k) <= {"command", "verdict"}
        and isinstance(k.get("command"), str)
        and k["command"].strip()
        and isinstance(k.get("verdict", ""), str)
        for k in given
    ):
        raise Refused(
            f'lesson {lesson}\'s keys are a list of {{"command": "capture", "verdict": "FAIL"}}: '
            "the checks it's about"
        )
    return [
        {"command": k["command"].strip().split()[0], "verdict": k.get("verdict", "").strip()}
        for k in given
    ]


def keys_from(ledger: DesignLedger, run: str, held: dict[str, Any]) -> list[dict[str, str]]:
    """The failing checks a lesson's evidence shows, by command and verdict, as a look-up at a
    failure would meet them: each bench answer at its calls that didn't pass (any exit but 0 or
    3), and each reading it names that failed, by its need's command. Each once."""
    found: list[dict[str, str]] = []
    for n in held.get("calls") or []:
        for b in ledger.rows(
            "SELECT argv, verdict FROM bench_calls WHERE run=? AND at_call=? AND exit NOT IN "
            "(0, ?) ORDER BY id",
            run,
            n,
            nudge.USAGE,
        ):
            command = nudge.command_of(nudge.check_of(json.loads(b["argv"])))
            if command:
                found.append({"command": command, "verdict": str(b["verdict"] or "")})
    for reading in held.get("readings") or []:
        rows = ledger.rows(
            "SELECT r.verdict, n.command FROM readings AS r JOIN rounds AS o "
            "ON o.run = r.run AND o.round = r.round JOIN needs AS n ON n.run = r.run "
            "AND n.version = o.needs_version AND n.need = r.need "
            "WHERE r.run=? AND r.round=? AND r.need=? AND r.passed=0",
            run,
            reading["round"],
            reading["need"],
        )
        for r in rows:
            if r["command"]:
                command = nudge.command_of(nudge.check_of(str(r["command"]).split()))
                found.append({"command": command, "verdict": str(r["verdict"] or "")})
    return [dict(k) for k in dict.fromkeys(tuple(sorted(k.items())) for k in found)]


def _set_keys(ledger: DesignLedger, lesson: int, keys: Iterable[dict[str, str]]) -> None:
    ledger.change("DELETE FROM lesson_keys WHERE lesson=?", lesson)
    for key in keys:
        ledger.add("lesson_keys", lesson=lesson, command=key["command"], verdict=key["verdict"])


def _becomes(routes: Iterable[str], becomes: str | None, lesson: int | str) -> str | None:
    """What a lesson becomes outside the model's text: for the harness, one of
    `HARNESS_BECOMES`, never a note; for the request alone, `request`; otherwise nothing."""
    routes = set(routes)
    if "harness" in routes:
        if becomes not in HARNESS_BECOMES:
            raise Refused(
                f"lesson {lesson} is for the harness, so it's never given as a note: say what it "
                f"becomes, {', '.join(HARNESS_BECOMES)} (`--becomes`), and build it"
            )
        return becomes
    if "request" in routes:
        if becomes not in (None, "request"):
            raise Refused(f"lesson {lesson} is for the request: it becomes `request`")
        return "request"
    if becomes is not None:
        raise Refused(
            f"only a lesson for the harness or the request becomes something: lesson {lesson} "
            f"is for {', '.join(sorted(routes))}"
        )
    return None


def part_kind_of(said: str | None) -> str | None:
    """A lesson's kind of part as it's kept: a kind by its toolset's key, as a request's `## Kind`
    is read, `general` for one a reviewer says is for every kind, or none."""
    said = (said or "").strip()
    if not said:
        return None
    return toolsets.key(said)


def is_general(found: dict[str, Any]) -> bool:
    """Given whatever the run's kind: said to be general, or with no kind yet."""
    return part_kind_of(found.get("part_kind")) in (None, GENERAL)


def _for_said(found: dict[str, Any]) -> str:
    kind = part_kind_of(found.get("part_kind"))
    if kind is None:
        return "with no kind of part yet"
    return GENERAL if kind == GENERAL else f"for {kind} parts"


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
    routes: Iterable[str],
    at_call: int | None = None,
    keys: list[dict[str, str]] | None = None,
    part_kind: str | None = None,
) -> int:
    """A candidate, with the routes its writer proposes: nothing reaches anyone until a reviewer
    has let it through. Its keys are the checks its evidence shows, unless it names them."""
    if kind not in KINDS:
        raise Refused(f"a lesson's kind is its area, {', '.join(KINDS)}: not {kind!r}")
    proposed = check_routes(list(routes), "new")
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
        part_kind=part_kind_of(part_kind),
    )
    _set_routes(ledger, lesson, proposed, written_by)
    _set_keys(ledger, lesson, keys if keys is not None else keys_from(ledger, run, held))
    _step(ledger, lesson, CANDIDATE, written_by, f"written, for {', '.join(proposed)}", run=run)
    return lesson


def accept(
    ledger: DesignLedger,
    lesson: int,
    by: str,
    becomes: str | None = None,
    routes: Iterable[str] | None = None,
    part_kind: str | None = None,
) -> None:
    """Let through, on the routes proposed or those the reviewer names. A lesson for the harness
    only as what it becomes there, never a note. `part_kind` gives it its kind of part, or
    `general`, as it's let through."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (CANDIDATE, REJECTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a candidate is accepted")
    proposed = list(routes) if routes is not None else list(routes_of(ledger, lesson))
    chosen = check_routes(proposed, lesson)
    becomes = _becomes(chosen, becomes, lesson)
    was = _set_routes(ledger, lesson, chosen, by)
    ledger.change("UPDATE lessons SET becomes=? WHERE id=?", becomes, lesson)
    said = f"for {', '.join(chosen)}" + (f", becomes {becomes}" if becomes else "")
    kind = part_kind_of(part_kind)
    if kind is not None and kind != found.get("part_kind"):
        ledger.change("UPDATE lessons SET part_kind=? WHERE id=?", kind, lesson)
        was = {**(was or {}), "part_kind": found.get("part_kind")}
        said += f", {_for_said({'part_kind': kind})}"
    _status(ledger, lesson, ACCEPTED, by)
    _step(ledger, lesson, ACCEPTED, by, said, was=_json(was) if was else None)


def edit(
    ledger: DesignLedger,
    lesson: int,
    by: str,
    *,
    text: str | None = None,
    kind: str | None = None,
    fix_shape: str | None = None,
    becomes: str | None = None,
    routes: Iterable[str] | None = None,
    keys: list[dict[str, str]] | None = None,
    part_kind: str | None = None,
    why: str = "",
) -> None:
    """The reviewer's own words, kept beside the ones they replace. A candidate edited is let
    through as edited. A lesson made one for the harness says what it becomes. `part_kind` is a
    kind of part, `general` for every kind, or "" to take its kind away, which lists it for the
    review again."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] in (REJECTED, RETIRED):
        raise Refused(f"lesson {lesson} is {found['status']}")
    if all(v is None for v in (text, kind, fix_shape, becomes, routes, keys, part_kind)):
        raise Refused(
            "an edit changes the text, the kind, the fix's shape, the routes, what it becomes, "
            "its keys or its kind of part"
        )
    if kind is not None and kind not in KINDS:
        raise Refused(f"a lesson's kind is its area, {', '.join(KINDS)}: not {kind!r}")
    had = routes_of(ledger, lesson)
    chosen = check_routes(list(routes), lesson) if routes is not None else tuple(sorted(had))
    if becomes is None and set(chosen) & {"harness", "request"}:
        becomes = found["becomes"]
    becomes = _becomes(chosen, becomes, lesson)
    new_keys = check_keys(keys, lesson) if keys is not None else None
    was: dict[str, Any] = {k: found[k] for k in ("text", "kind", "fix_shape", "becomes")}
    was["part_kind"] = found.get("part_kind")
    was["keys"] = keys_of(ledger, lesson)
    was["routes"] = sorted(had)
    ledger.change(
        "UPDATE lessons SET text=?, kind=?, fix_shape=?, becomes=?, part_kind=? WHERE id=?",
        (text or found["text"]).strip(),
        kind or found["kind"],
        (fix_shape or found["fix_shape"]).strip(),
        becomes,
        part_kind_of(part_kind) if part_kind is not None else found.get("part_kind"),
        lesson,
    )
    _set_routes(ledger, lesson, chosen, by)
    if new_keys is not None:
        _set_keys(ledger, lesson, new_keys)
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
    """Delivered on a route: what it became, and where that is (`owner/repo#n`, or a path). An
    issue merged for the harness, a line in the request template or in `docs/rounds.md`'s
    checklist, a skill or a note. For the harness, only what it was accepted as becoming. Only a
    score after this says whether it holds there."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (*KEPT, PROMOTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a kept lesson is promoted")
    if became not in BECAME:
        raise Refused(f"a lesson becomes {', '.join(BECAME)}: not {became!r}")
    route = BECAME[became]
    routes = routes_of(ledger, lesson)
    if route not in routes:
        raise Refused(
            f"lesson {lesson} isn't for {route}, which {became} is: it's for "
            f"{', '.join(routes) or 'no one'}. Change its routes first (`edit --routes`)"
        )
    if route == "harness" and became != found["becomes"]:
        raise Refused(f"lesson {lesson} was accepted as becoming {found['becomes']}, not {became}")
    if not where.strip():
        raise Refused("say where it is now: `owner/repo#n`, or a path")
    mark = ledger.rows("SELECT MAX(id) AS n FROM lesson_scores WHERE lesson=?", lesson)[0]["n"]
    ledger.change(
        "UPDATE lesson_routes SET became=?, became_where=?, delivered_at=?, after_score=? "
        "WHERE lesson=? AND route=?",
        became,
        where.strip(),
        now(),
        int(mark or 0),
        lesson,
        route,
    )
    ledger.change(
        "UPDATE lessons SET became=?, became_where=? WHERE id=?", became, where.strip(), lesson
    )
    _status(ledger, lesson, PROMOTED, by)
    _step(ledger, lesson, PROMOTED, by, f"became {became}, for {route}: {where.strip()}")


# A lesson from another ledger (mqucifer/print-shop#137)


def copy(ledger: DesignLedger, source: Path, lesson: int, by: str) -> int:
    """Lesson `lesson` of the ledger at `source`, written here as a candidate: its words, kind,
    routes, keys and kind of part as they stand there, and its evidence that ledger's run and
    calls, each checked there. Nothing is written if one isn't there, if it was rejected there,
    or if it's been copied here before, which names the copy."""
    by = signed(by)
    found = Source(source)
    if found.path == Path(ledger.path).expanduser().resolve():
        raise Refused(f"{found.path} is this ledger: a lesson is copied from another")
    rows = found.rows("SELECT * FROM lessons WHERE id=?", lesson)
    if not rows:
        raise Refused(f"{found.path} has no lesson {lesson}")
    row = dict(rows[0])
    copied = ledger.rows(
        "SELECT lesson, by, at FROM lesson_copies WHERE ledger=? AND source_lesson=?",
        str(found.path),
        lesson,
    )
    if copied:
        c = copied[0]
        raise Refused(
            f"lesson {lesson} of {found.path} is copied here already, as lesson {c['lesson']}, by "
            f"{c['by']} at {c['at']}: review that one (`lessons accept {c['lesson']}`)"
        )
    if row["status"] == REJECTED:
        raise Refused(f"lesson {lesson} was rejected in {found.path}: it isn't copied")
    run = str(row["run"])
    stated = json.loads(row["evidence"] or "{}")
    wrong: list[str] = []
    if not found.rows("SELECT 1 FROM runs WHERE run=?", run):
        wrong.append(f"it has no run `{run}`")
    held, bad = evidence(found, run, stated.get("calls"), stated.get("readings"))
    wrong += bad
    for finding in stated.get("findings") or []:
        if not found.rows("SELECT 1 FROM refine_findings WHERE id=?", finding):
            wrong.append(f"it has no refinement finding {finding}")
    if stated.get("findings"):
        held["findings"] = stated["findings"]
    if wrong:
        raise Refused(
            f"lesson {lesson} isn't copied: its evidence isn't all in {found.path}, the ledger it "
            "came from: " + "; ".join(wrong)
        )
    routes = list(routes_of(found, lesson))
    if not routes:
        raise Refused(f"lesson {lesson} has no routes in {found.path}: it isn't copied")
    said = evidence_lines(found, run, held)
    copy_id = ledger.insert(
        "lessons",
        run=run,
        written_by=row["written_by"],
        written_at=row["written_at"],
        at_call=row["at_call"],
        fix_shape=row["fix_shape"],
        kind=row["kind"],
        text=row["text"],
        evidence=_json(held),
        status=CANDIDATE,
        status_by=by,
        status_at=now(),
        part_kind=row.get("part_kind"),
    )
    try:
        ledger.insert(
            "lesson_copies",
            lesson=copy_id,
            ledger=str(found.path),
            source_lesson=lesson,
            source_run=run,
            source_status=row["status"],
            evidence_said=_json(said),
            by=by,
            at=now(),
        )
    except sqlite3.IntegrityError as exc:
        # Copied by another step since it was looked for: this one is taken back whole.
        ledger.change("DELETE FROM lessons WHERE id=?", copy_id)
        raise Refused(f"lesson {lesson} of {found.path} was copied here just now: {exc}") from exc
    _set_routes(ledger, copy_id, check_routes(routes, copy_id), by)
    _set_keys(ledger, copy_id, keys_of(found, lesson))
    _step(
        ledger,
        copy_id,
        CANDIDATE,
        by,
        f"copied from lesson {lesson} of {found.path}, {row['status']} there, for "
        f"{', '.join(sorted(routes))}: its evidence checked in that ledger's run `{run}`",
    )
    return copy_id


def copied_from(ledger: Readable, lesson: int) -> dict[str, Any] | None:
    """Where a copied lesson came from, or None for one written here."""
    found = ledger.rows("SELECT * FROM lesson_copies WHERE lesson=?", lesson)
    return dict(found[0]) if found else None


def _copy_evidence(row: dict[str, Any], held: dict[str, Any]) -> list[str]:
    """A copied lesson's evidence, as the ledger it came from holds it now, or as it held it when
    it was copied, said so, if that ledger can't be read."""
    try:
        return evidence_lines(Source(Path(row["ledger"])), row["source_run"], held)
    except (Refused, sqlite3.Error):
        said = json.loads(row["evidence_said"])
        return [f"{row['ledger']} can't be read now: as it held it when copied, at {row['at']}"] + [
            f"  {e}" for e in said
        ]


# Giving them to a run, and scoring them


def kept_for_model(ledger: DesignLedger) -> list[dict[str, Any]]:
    """Each kept lesson for the model whose text is still given, of every kind, oldest first.
    One folded into a skill is read there, and one for the harness alone never reaches the model."""
    found = ledger.rows(
        "SELECT lessons.* FROM lessons JOIN lesson_routes AS r ON r.lesson = lessons.id "
        f"AND r.route = 'model' AND r.ended_at IS NULL WHERE lessons.status IN "  # noqa: S608
        f"({','.join('?' * len(KEPT))}, ?) AND COALESCE(r.became, '') != 'skill' "
        "ORDER BY lessons.id",
        *KEPT,
        PROMOTED,
    )
    return [dict(r) for r in found]


def _standing(ledger: DesignLedger, lesson: int) -> tuple[int, int]:
    """Where a lesson's scores put it among its like, lowest first: whether any run's latest
    score of its text says it got in the way, then the runs whose latest says it was used, most
    first."""
    scores = [
        s for (_, route), s in latest_scores(ledger, lesson).items() if route == "model"
    ]
    in_the_way = any(s["score"] == "in_the_way" for s in scores)
    return int(in_the_way), -sum(s["score"] == "used" for s in scores)


def _ordered(ledger: DesignLedger, found: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(found, key=lambda one: (*_standing(ledger, int(one["id"])), int(one["id"])))


def to_give(ledger: DesignLedger, part_kind: str | None = None) -> list[dict[str, Any]]:
    """What a run's start draws from, in the order it takes them: each kept lesson for the model
    whose text is still given, those for this kind of part first, then the general ones, and in
    each those scored `used` on most runs first, any that got in the way last, and the oldest
    first among equals. `within` holds them to the start's budget."""
    kind = part_kind_of(part_kind)
    kept = kept_for_model(ledger)
    mine = [k for k in kept if not is_general(k) and part_kind_of(k["part_kind"]) == kind]
    general = [k for k in kept if is_general(k)]
    return _ordered(ledger, mine) + _ordered(ledger, general)


def within(
    found: list[dict[str, Any]], budget: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The lessons a start gives and those it leaves out, under a budget in characters: they're
    taken in order while each one's line, as given, fits in what's left, and the first that
    doesn't ends them, so none is given ahead of a better one. The one exception is a lesson
    whose line is longer than the whole budget: no start can give it, so it's left out, marked
    `too_long`, and the filling goes on past it, as one such lesson mustn't empty a start. One
    left out is still kept, and the `lessons` tool finds it."""
    given: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    used, full = 0, False
    for one in found:
        size = len(line(one))
        if 0 < budget < size:
            left.append({**one, "too_long": True})
        elif full or used + size > budget:
            full = True
            left.append(one)
        else:
            used += size
            given.append(one)
    return given, left


def writer(written_by: str) -> str:
    """Who wrote a lesson, as a sentence names them."""
    if written_by == MODEL:
        return "the model"
    if written_by == SPONSOR:
        return "the Sponsor"
    return written_by.removeprefix(REVIEWER).removeprefix(STAND_IN).strip()


def area(kind: str) -> str:
    """A lesson's area in words, or its kind from before the five."""
    return KINDS.get(kind) or OLD_KINDS.get(kind) or kind


def line(found: dict[str, Any]) -> str:
    """One lesson as a run is given it, with its area and where it came from."""
    who = writer(found["written_by"])
    return (
        f"- **Lesson {found['id']}** ({area(found['kind'])}), from `{found['run']}`, written by "
        f"{who}: {found['text']}"
    )


def left_said(left: list[dict[str, Any]], part_kind: str | None = None) -> str:
    """What a start says of the lessons it left out: how many, and how to find them."""
    kind = part_kind_of(part_kind)
    general = sum(is_general(one) for one in left)
    mine = len(left) - general
    counts = [f"{mine} for {kind} parts"] if mine else []
    counts += [f"{general} general"] if general else []
    one = len(left) == 1
    yours = f"`{kind}`, or `general`" if kind not in (None, GENERAL) else "`general`, or a kind's"
    long = [str(found["id"]) for found in left if found.get("too_long")]
    too_long = ""
    if long:
        many = len(long) > 1
        too_long = (
            f" Lesson{'s' if many else ''} {', '.join(long)} {'are' if many else 'is'} too long "
            f"for the start: read {'each' if many else 'it'} with its `id`."
        )
    return (
        f"{len(left)} more kept lesson{' isn' if one else 's aren'}'t here, to keep the start "
        f"short: {' and '.join(counts)}.{too_long} The `lessons` tool finds "
        f"{'it' if one else 'them'}: by `words`, by `kind` ({yours}), and one whole by its `id`. "
        "One you read whole, you'll be asked about at the end, as you will these."
    )


def block(
    given: list[dict[str, Any]],
    left: list[dict[str, Any]] | None = None,
    part_kind: str | None = None,
) -> str:
    """The lessons under their fixed heading, for the first message, and after them how many the
    start's budget left out, and how to find them."""
    if not given and not left:
        return ""
    lines = [HEADING, "", ABOUT, "", *(line(g) for g in given)]
    if left:
        lines += [*([""] if given else []), left_said(left, part_kind)]
    return "\n".join(lines)


def give(
    ledger: DesignLedger,
    run: str,
    given: Iterable[dict[str, Any]],
    how: str = AT_START,
    by: str = HARNESS,
    texts: dict[int, str] | None = None,
) -> None:
    """A row of `run_lessons` for each, in the words it was given in, and each the first time
    it's given is `given`. Code gives one at `start`, or whole by the `lessons` tool, and only one
    for the model: `how` otherwise records one given by hand before there was code for it."""
    for found in given:
        if how in (AT_START, AT_TOOL) and "model" not in routes_of(ledger, found["id"]):
            raise Refused(f"lesson {found['id']} isn't for the model: it isn't given as text")
        text = (texts or {}).get(found["id"]) or line(found)
        ledger.add("run_lessons", run=run, lesson=found["id"], given_at=now(), how=how, text=text)
        if found["status"] in (ACCEPTED, EDITED):
            _status(ledger, found["id"], GIVEN, by)
        _step(ledger, found["id"], GIVEN, by, how, run=run)


def leave_out(ledger: DesignLedger, run: str, left: Iterable[dict[str, Any]], budget: int) -> None:
    """A row of `run_lessons_left` for each lesson a run's start left out, and why. Nothing of
    the lesson changes: it isn't given, and it isn't dropped."""
    over = f"over the start's budget of {budget} characters (`Limits.start_lessons_chars`)"
    for found in left:
        why = over
        if found.get("too_long"):
            why = (
                f"too long for the start: its line is {len(line(found))} characters, and the "
                f"start's whole budget is {budget} (`Limits.start_lessons_chars`)"
            )
        ledger.add("run_lessons_left", run=run, lesson=found["id"], at=now(), why=why)


def left_out_of(ledger: DesignLedger, run: str) -> list[dict[str, Any]]:
    """Each lesson a run's start left out, in the order the start would have taken them."""
    found = ledger.rows("SELECT * FROM run_lessons_left WHERE run=? ORDER BY rowid", run)
    return [dict(r) for r in found]


# The model's `lessons` tool: a kept lesson found by words or by kind, and one whole by its id


def _found_line(found: dict[str, Any], had: dict[int, str]) -> str:
    """One lesson as a search names it: its number, area, kind of part and fix's shape."""
    how = had.get(int(found["id"]))
    seen = ", read already" if how == AT_TOOL else ", in your start" if how else ""
    kind = GENERAL if is_general(found) else _for_said(found)
    shape = " ".join(str(found["fix_shape"]).split())
    if len(shape) > SHAPE_CHARS:
        shape = shape[:SHAPE_CHARS].rstrip() + "..."
    return f"- Lesson {found['id']} ({area(found['kind'])}, {kind}{seen}): {shape}"


def _matches(found: dict[str, Any], words: list[str]) -> int:
    """How many of the words a lesson holds: in its text, its fix's shape, its area or its kind
    of part."""
    held = " ".join(
        str(found.get(k) or "") for k in ("text", "fix_shape", "kind", "part_kind")
    )
    held = f"{held} {area(found['kind'])}".casefold()
    return sum(word in held for word in words)


def _kinds_said(kept: list[dict[str, Any]]) -> str:
    kinds = sorted({str(part_kind_of(k["part_kind"])) for k in kept if not is_general(k)})
    return ", ".join(f"`{k}`" for k in [*kinds, GENERAL])


def look_up(
    ledger: DesignLedger, run: str, part_kind: str | None, args: dict[str, Any]
) -> tuple[bool, str, dict[str, Any]]:
    """The `lessons` tool's answer: whether it's one, its words, and what the run's events keep.
    With `id`, that kept lesson whole, as a start gives one, and the run is given it
    (`run_lessons`, as `tool`), so its review scores it. Otherwise each kept lesson for the model
    that holds every one of `words`, or failing that any of them, and is for `kind`, in a line:
    its number, area, kind of part and fix's shape. With neither, the run's own: its kind's and
    the general ones, as the start orders them. It reads, and changes no lesson's words."""
    kept = kept_for_model(ledger)
    had = {int(g["lesson"]): str(g["how"]) for g in given_to(ledger, run)}
    if args.get("id") is not None:
        wanted = int(args["id"])
        found = next((k for k in kept if int(k["id"]) == wanted), None)
        if found is None:
            said = (
                f"There's no kept lesson {wanted} for you. `lessons` with `words` or `kind` "
                "names each by its number."
            )
            return False, said, {"read": wanted}
        if wanted not in had:
            give(ledger, run, [found], AT_TOOL)
        how = had.get(wanted, AT_TOOL)
        said = f"{line(found)}\n\nIts fix's shape: {found['fix_shape']}"
        if how == AT_TOOL:
            said += (
                "\n\nUse it where it fits your part, and leave it where it doesn't: at the "
                "end you'll be asked whether you used it."
            )
        else:
            said += "\n\nIt's in your first message too, under `" + HEADING + "`."
        return True, said, {"read": wanted, "given": how}
    words = re.findall(r"[\w'.-]+", str(args.get("words") or "").casefold())
    asked = part_kind_of(str(args.get("kind") or ""))
    if asked is None and not words:
        found_all = to_give(ledger, part_kind)
        of = " for your part's kind, and general"
    elif asked is None:
        found_all, of = _ordered(ledger, kept), ""
    elif asked == GENERAL:
        found_all, of = _ordered(ledger, [k for k in kept if is_general(k)]), " that are general"
    else:
        mine = [k for k in kept if not is_general(k) and part_kind_of(k["part_kind"]) == asked]
        found_all, of = _ordered(ledger, mine), f" for {asked} parts"
    some = ""
    if words:
        counted = [(k, _matches(k, words)) for k in found_all]
        found_all = [k for k, n in counted if n == len(words)]
        if not found_all:
            # None holds every word: those that hold any, most first.
            counted.sort(key=lambda pair: -pair[1])
            found_all = [k for k, n in counted if n]
            some = " None has every word, so these have some, most first."
        of += f" with {' '.join(words)!r}"
    detail: dict[str, Any] = {"found": [int(k["id"]) for k in found_all]}
    if not kept:
        return True, "No lessons are kept for you yet.", detail
    if not found_all:
        said = f"There's no kept lesson{of}."
        said += " Try other words, or fewer." if words else ""
        return True, f"{said} The kinds that have lessons: {_kinds_said(kept)}.", detail
    lines = [_found_line(k, had) for k in found_all[:FOUND_MOST]]
    more = len(found_all) - len(lines)
    if more:
        lines.append(f"- and {more} more: narrow it with `words` or `kind`.")
    head = (
        f"{len(found_all)} kept lesson{'' if len(found_all) == 1 else 's'}{of}, each by its "
        f"number, area, kind of part and fix's shape.{some} `lessons` with `id` gives one whole."
    )
    return True, "\n".join([head, "", *lines]), detail


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
    route: str = "model",
) -> None:
    """Whether a lesson held on one of its routes in a run: the model's by the run's own review,
    of a lesson it was given; any other once it was delivered there, by the analysis. The status
    then follows each run's latest score (`settle`)."""
    if scored not in SCORES:
        raise Refused(f"a score is {', '.join(SCORES)}: not {scored!r}")
    routes = routes_of(ledger, lesson)
    if route == "model":
        # A lesson given as text is scored as such, whatever its routes are now.
        if not ledger.rows("SELECT 1 FROM run_lessons WHERE run=? AND lesson=?", run, lesson):
            raise Refused(f"`{run}` wasn't given lesson {lesson}")
    elif route not in routes:
        raise Refused(f"lesson {lesson} isn't for {route}: it's for {', '.join(routes) or 'none'}")
    elif not routes[route]["delivered_at"]:
        raise Refused(
            f"lesson {lesson} hasn't reached {route} yet: `promote` it there first, then score it"
        )
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
        route=route,
    )
    settle(ledger, lesson, by)


def latest_scores(ledger: DesignLedger, lesson: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Each run's latest score of a lesson on each route, by run and route, oldest first. A
    score from before routes is the model's."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for row in ledger.rows("SELECT * FROM lesson_scores WHERE lesson=? ORDER BY id", lesson):
        key = (str(row["run"]), str(row["route"] or "model"))
        found.pop(key, None)
        found[key] = dict(row)
    return found


def _since(routes: dict[str, dict[str, Any]]) -> int:
    """The last score from before a lesson's latest delivery, by id: none counts after it."""
    marks = [int(r["after_score"] or 0) for r in routes.values() if r["delivered_at"]]
    return max(marks, default=0)


def settle(ledger: DesignLedger, lesson: int, by: str, why: str | None = None) -> str:
    """A scored lesson's status from each run's latest score on each route: the newest that says
    anything, used as `held` and in the way as `broken`. A correction replaces that run's score,
    so a lesson whose only score became `not_used` is back to `given`, or `promoted`. A score from
    before its latest delivery says nothing of the fix delivered. Once a lesson built elsewhere
    holds, its text stops being given to the model. Each change is a step."""
    found = one(ledger, lesson)
    if found["status"] not in SCORED:
        return str(found["status"])
    routes = routes_of(ledger, lesson)
    since = _since(routes)
    base = PROMOTED if found["became"] else GIVEN
    counted = [row for row in latest_scores(ledger, lesson).values() if int(row["id"]) > since]
    if not counted:
        return str(found["status"])
    moved, said, deciding = base, "no score since says whether it holds", None
    for row in counted:
        if row["score"] in AFTER_SCORE:
            moved, deciding = AFTER_SCORE[row["score"]], row
            said = f"scored {row['score']} on {row['route'] or 'model'}, on {row['run']}"
    if moved != found["status"]:
        _status(ledger, lesson, moved, by)
        _step(ledger, lesson, moved, by, why or f"each run's latest score: {said}")
    model = routes.get("model")
    built = {
        str(r["became"])
        for r in routes.values()
        if r["delivered_at"] and r["became"] in (*HARNESS_BECOMES, "skill")
    }
    if moved == HELD and deciding is not None and model and not model["ended_at"] and built:
        ended = f"its fix is built ({', '.join(sorted(built))}) and {said}"
        ledger.change(
            "UPDATE lesson_routes SET ended_at=?, ended_why=? WHERE lesson=? AND route='model'",
            now(),
            ended,
            lesson,
        )
        _step(ledger, lesson, moved, by, f"its text stops being given: {ended}")
    return moved


def to_retire(ledger: DesignLedger) -> list[tuple[dict[str, Any], str]]:
    """Each live lesson given twice and not used, or that got in the way, and why: by each run's
    latest score on each route since its latest delivery."""
    shown: list[tuple[dict[str, Any], str]] = []
    for row in ledger.rows(
        f"SELECT * FROM lessons WHERE status IN ({','.join('?' * len(SCORED))}) "  # noqa: S608
        "OR status IN (?, ?) ORDER BY id",
        *SCORED,
        ACCEPTED,
        EDITED,
    ):
        since = _since(routes_of(ledger, int(row["id"])))
        scores = [s for s in latest_scores(ledger, int(row["id"])).values() if s["id"] > since]
        in_the_way = sorted({str(s["run"]) for s in scores if s["score"] == "in_the_way"})
        unused = sorted({str(s["run"]) for s in scores if s["score"] == "not_used"})
        if in_the_way:
            shown.append((dict(row), f"got in the way on {', '.join(in_the_way)}"))
        elif len(unused) >= UNUSED_TO_RETIRE:
            shown.append((dict(row), f"given and not used on {', '.join(unused)}"))
    return shown


# What the reviewer reads


def _routes_said(ledger: DesignLedger, lesson: int) -> str:
    said = []
    for route, r in routes_of(ledger, lesson).items():
        if r["ended_at"]:
            said.append(f"{route} (its text stopped: {r['ended_why']})")
        elif r["delivered_at"]:
            said.append(f"{route} ({r['became']}: {r['became_where']})")
        else:
            said.append(route)
    return ", ".join(said) or "none"


def _keys_said(ledger: DesignLedger, lesson: int) -> str:
    keys = keys_of(ledger, lesson)
    return ", ".join(f"`{k['command']}` {k['verdict'] or 'any'}" for k in keys) or "none"


def waiting(ledger: DesignLedger) -> list[tuple[dict[str, Any], str]]:
    """Each kept lesson for a route that hasn't reached it: the harness's issue to file, the
    request template's line or the checklist's to write. The model's are given at `start`."""
    found: list[tuple[dict[str, Any], str]] = []
    for row in ledger.rows(
        f"SELECT * FROM lessons WHERE status IN ({','.join('?' * len(KEPT))}) "  # noqa: S608
        "OR status=? ORDER BY id",
        *KEPT,
        PROMOTED,
    ):
        for route, r in routes_of(ledger, int(row["id"])).items():
            if route != "model" and not r["delivered_at"]:
                found.append((dict(row), route))
    return found


def listing(ledger: DesignLedger, run: str | None = None) -> str:
    """With a run: the lessons it wrote, each with its evidence as the ledger has it, and the
    lessons it was given with their scores. With none: every lesson in a line. Either way, what
    waits to reach its route, and any to retire."""
    lines: list[str] = []
    if run is not None:
        if ledger.rows("SELECT 1 FROM runs WHERE run=?", run):
            lines += _run_listing(ledger, run)
        elif ledger.rows("SELECT 1 FROM lesson_copies WHERE source_run=?", run):
            lines += _copies_listing(ledger, run)
        else:
            raise Refused(f"`{run}` isn't in this ledger, and no lesson here was copied from it")
    else:
        lines += ["# Every lesson", ""]
        for row in ledger.rows("SELECT * FROM lessons ORDER BY id"):
            copy = copied_from(ledger, int(row["id"]))
            source = (
                f", copied from lesson {copy['source_lesson']} of {copy['ledger']}" if copy else ""
            )
            lines.append(
                f"- {row['id']}. [{row['status']}] {area(row['kind'])}, for "
                f"{_routes_said(ledger, int(row['id']))}, from `{row['run']}`{source}: "
                f"{row['text'][:160]}"
            )
        if len(lines) == 2:
            lines.append("None yet.")
        lines.append("")
        if ledger.rows("SELECT 1 FROM lesson_copies"):
            lines += _copies_listing(ledger)
    pending = waiting(ledger)
    if pending:
        lines += ["## Waiting to reach their route, for the operator", ""]
        lines += [
            f"- Lesson {row['id']}, for {route}: {ROUTES[route].split(': ', 1)[1]}. Once it's "
            f"there, `promote {row['id']} --as {_as_for(route, row)} --where ...`"
            for row, route in pending
        ]
        lines.append("")
    retiring = to_retire(ledger)
    if retiring:
        lines += ["## To retire, for the reviewer", ""]
        lines += [f"- Lesson {row['id']}: {why}. `retire {row['id']}`" for row, why in retiring]
        lines.append("")
    kept = kept_for_model(ledger)
    unsorted = [k for k in kept if part_kind_of(k["part_kind"]) is None]
    if unsorted:
        lines += [
            "## Kept for the model with no kind of part, for the reviewer",
            "",
            "Each starts every run, of any kind, until it has one. Give each its kind with "
            "`edit ID --part-kind K`, or `--part-kind general` if it's for every kind.",
            "",
        ]
        lines += [
            f"- Lesson {k['id']} ({area(k['kind'])}): {_sentence(k['fix_shape'])} "
            f"{str(k['text'])[:SHAPE_CHARS]}"
            for k in unsorted
        ]
        lines.append("")
    long = [(k, len(line(k))) for k in kept if len(line(k)) > START_CHARS]
    if long:
        lines += [
            "## Too long for a start, for the reviewer",
            "",
            f"Each one's line is over a start's whole budget, {START_CHARS} characters unless a "
            "run sets it (`Limits.start_lessons_chars`), so no start gives it, and only the "
            "`lessons` tool finds it. `edit ID --text FILE` shortens it.",
            "",
        ]
        lines += [f"- Lesson {k['id']}: {size} characters." for k, size in long]
        lines.append("")
    folding = to_fold(ledger)
    if folding:
        lines += [
            "## To fold into a kind's skill, for the reviewer",
            "",
            "Kept for a kind whose skill doesn't hold them yet. A kind's lessons belong in its "
            "skill: once one is there, `promote ID --as skill --where PATH`, and it leaves the "
            "start.",
            "",
        ]
        for kind, found in folding.items():
            own = toolsets.KINDS.get(kind)
            skill = f"the `{own.skills[-1]}` skill" if own else "which has no skill of its own yet"
            named = ", ".join(str(k["id"]) for k in found)
            lines.append(f"- `{kind}`, {skill}: lesson{'' if len(found) == 1 else 's'} {named}.")
        lines.append("")
    return "\n".join(lines)


def _sentence(words: str) -> str:
    words = " ".join(str(words).split())
    return words if words.endswith((".", "!", "?")) else f"{words}."


def to_fold(ledger: DesignLedger) -> dict[str, list[dict[str, Any]]]:
    """Each kind's kept lessons for the model that its skill doesn't hold yet, by kind: the
    candidates to fold in, which stays the main route (mqucifer/print-shop#155). One promoted as
    `skill` is read there, and isn't here."""
    found: dict[str, list[dict[str, Any]]] = {}
    for one in kept_for_model(ledger):
        if not is_general(one):
            found.setdefault(str(part_kind_of(one["part_kind"])), []).append(one)
    return dict(sorted(found.items()))


def _copies_listing(ledger: DesignLedger, run: str | None = None) -> list[str]:
    """Each lesson copied here from another ledger, or from one run there, with its evidence as
    the ledger it came from holds it (mqucifer/print-shop#137)."""
    copies = ledger.rows(
        "SELECT c.*, l.status, l.text, l.kind, l.evidence FROM lesson_copies AS c JOIN lessons "
        "AS l ON l.id = c.lesson WHERE ? IS NULL OR c.source_run = ? ORDER BY c.lesson",
        run,
        run,
    )
    title = f"Lessons copied from `{run}`" if run else "Copied from other ledgers"
    lines = [f"{'#' if run else '##'} {title}, with their evidence where each came from", ""]
    for c in copies:
        n = int(c["lesson"])
        lines += [
            f"{'##' if run else '###'} Lesson {n}: {c['status']}",
            "",
            f"- **Copied from** lesson {c['source_lesson']} of {c['ledger']}, `{c['source_run']}`"
            f", {c['source_status']} there, by {c['by']} at {c['at']}",
            f"- **Kind:** {area(c['kind'])}",
            f"- **For:** {_routes_said(ledger, n)}",
            f"- **Keyed to:** {_keys_said(ledger, n)}",
            "",
            str(c["text"]),
            "",
            f"What {c['ledger']} holds where it points:",
            "",
        ]
        held = json.loads(c["evidence"])
        lines += [f"    {e}" for e in _copy_evidence(dict(c), held)] or ["    nothing"]
        lines.append("")
    return lines


def _as_for(route: str, row: dict[str, Any]) -> str:
    if route == "harness":
        return str(row["becomes"] or "|".join(HARNESS_BECOMES))
    return {"request": "request", "operator": "checklist"}.get(route, "note|skill")


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
    written = ledger.rows(
        "SELECT * FROM lessons WHERE run=? AND id NOT IN (SELECT lesson FROM lesson_copies) "
        "ORDER BY id",
        run,
    )
    if not written:
        lines += ["It has written none.", ""]
    for row in written:
        n = int(row["id"])
        becomes = f", becomes {row['becomes']}" if row["becomes"] else ""
        part = f", {_for_said(dict(row))}"
        lines += [
            f"## Lesson {n}: {row['status']}",
            "",
            f"- **Kind:** {area(row['kind'])}{part}{becomes}",
            f"- **For:** {_routes_said(ledger, n)}",
            f"- **Keyed to:** {_keys_said(ledger, n)}",
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
    left = left_out_of(ledger, run)
    if left:
        read = {int(g["lesson"]) for g in given if g["how"] == AT_TOOL}
        lines += ["## Left out of its start", ""]
        for one in left:
            later = ", and read whole with the `lessons` tool" if int(one["lesson"]) in read else ""
            lines.append(f"- Lesson {one['lesson']}: {one['why']}{later}")
        lines.append("")
    scored = ledger.rows(
        "SELECT * FROM lesson_scores WHERE run=? AND COALESCE(route, 'model') != 'model' "
        "ORDER BY id",
        run,
    )
    if scored:
        lines += ["## Scored by the analysis", ""]
        for r in scored:
            lines.append(f"- Lesson {r['lesson']}, on {r['route']}: {_score_said(dict(r))}")
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
    `{"steps": [{"do": "write", "key": "a", "run": ..., "fix_shape": ..., "kind": ..., "routes":
    [...], "text": ..., "calls": [...], "readings": [...], "keys": [...], "part_kind": ...},
    {"do": "accept", "lesson": "a", "becomes": ..., "routes": [...], "part_kind": ...}, {"do":
    "give", "lesson": "a", "run": ..., "how": ..., "text": ...}, {"do": "score", ..., "route":
    ...}, {"do": "edit" | "reject" | "retire" | "promote", ...}]}`. A lesson is named by its
    `key` in the file, or by its number if it's in the ledger already. It's all checked before
    anything is written, and refused whole if a lesson's text is already in the ledger for its
    run, so it can't be loaded twice."""
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
                routes=step["routes"],
                keys=check_keys(step["keys"], step["key"]) if "keys" in step else None,
                part_kind=step.get("part_kind"),
            )
            continue
        named = step["lesson"]
        lesson = named if isinstance(named, int) else ids[named]
        if do == "accept":
            accept(
                ledger, lesson, by, step.get("becomes"), step.get("routes"), step.get("part_kind")
            )
        elif do == "edit":
            edit(
                ledger,
                lesson,
                by,
                text=step.get("text"),
                kind=step.get("kind"),
                fix_shape=step.get("fix_shape"),
                becomes=step.get("becomes"),
                routes=step.get("routes"),
                keys=step.get("keys"),
                part_kind=step.get("part_kind"),
                why=step.get("why", ""),
            )
        elif do == "reject":
            reject(ledger, lesson, by, step["why"])
        elif do == "give":
            found = one(ledger, lesson)
            give(ledger, step["run"], [found], step["how"], by, {lesson: step["text"]})
        elif do == "score":
            held, _ = evidence(ledger, step["run"], step.get("calls") or [], at_least_one=False)
            score(
                ledger,
                step["run"],
                lesson,
                step["score"],
                held,
                by,
                step.get("why"),
                route=step.get("route", "model"),
            )
        elif do == "retire":
            retire(ledger, lesson, by, step["why"])
        elif do == "promote":
            promote(ledger, lesson, by, step["as"], step["where"])
    return ids


STEP_KEYS = {
    "write": (
        {"key", "run", "fix_shape", "kind", "routes", "text", "calls"},
        {"readings", "keys", "part_kind"},
    ),
    "accept": ({"lesson"}, {"becomes", "routes", "part_kind"}),
    "edit": (
        {"lesson"},
        {"text", "kind", "fix_shape", "becomes", "routes", "keys", "part_kind", "why"},
    ),
    "reject": ({"lesson", "why"}, set()),
    "give": ({"lesson", "run", "how", "text"}, set()),
    "score": ({"lesson", "run", "score"}, {"calls", "why", "route"}),
    "retire": ({"lesson", "why"}, set()),
    "promote": ({"lesson", "as", "where"}, set()),
}


def _named(ledger: DesignLedger, lesson: Any, keys: set[str]) -> bool:
    if isinstance(lesson, int) and not isinstance(lesson, bool):
        return bool(ledger.rows("SELECT 1 FROM lessons WHERE id=?", lesson))
    return lesson in keys


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
            try:
                check_routes(step["routes"], step["key"])
                check_keys(step.get("keys", []), step["key"])
            except Refused as refused:
                wrong.append(f"step {i}: {refused}")
            if not str(step["fix_shape"]).strip() or not str(step["text"]).strip():
                wrong.append(f"step {i}: it needs its fix's shape and its text")
            _, bad = evidence(ledger, step["run"], step["calls"], step.get("readings"))
            wrong += [f"step {i}: {w}" for w in bad]
            if ledger.rows(
                "SELECT id FROM lessons WHERE run=? AND text=?", step["run"], step["text"].strip()
            ):
                wrong.append(f"step {i}: `{step['key']}` is in the ledger already")
        elif not _named(ledger, step["lesson"], keys):
            wrong.append(
                f"step {i}: `{step['lesson']}` isn't a lesson written above it, or one in the "
                "ledger by its number"
            )
        elif "run" in step:
            if not ledger.rows("SELECT 1 FROM runs WHERE run=?", step["run"]):
                wrong.append(f"step {i}: `{step['run']}` isn't in this ledger")
            elif step["do"] == "score":
                _, bad = evidence(ledger, step["run"], step.get("calls") or [], at_least_one=False)
                wrong += [f"step {i}: {w}" for w in bad]
    return wrong


# Lessons from before the five areas, sorted once


#: The real ledger's 24 lessons from before the five areas, by their run, number and old kind:
#: each one's area, the builder's sort for the reviewer to check, and its routes as settled with
#: the Sponsor on 2026-11-03 (mqucifer/print-shop#40): build lessons to the model, check lessons to
#: the harness, 10 to the request and the operator, 13 to the operator and the model, and 14 and
#: 19 to the model. A lesson that isn't one of these is sorted by its old kind (`BY_OLD_KIND`).
SORTED: dict[tuple[str, int, str], tuple[str, tuple[str, ...]]] = {
    ("stainedglass-1", 1, "build"): ("process", ("model",)),
    ("stainedglass-1", 2, "build"): ("memory", ("model",)),
    ("stainedglass-1", 3, "check"): ("verify", ("harness",)),
    ("stainedglass-1", 4, "check"): ("verify", ("harness",)),
    ("stainedglass-1", 5, "check"): ("verify", ("harness",)),
    ("stainedglass-1", 6, "check"): ("verify", ("harness",)),
    ("stainedglass-1", 7, "build"): ("process", ("model",)),
    ("stainedglass-1", 8, "check"): ("verify", ("harness",)),
    ("stainedglass-1", 9, "build"): ("job", ("model",)),
    ("stainedglass-1", 10, "context"): ("job", ("operator", "request")),
    ("stainedglass-1", 11, "build"): ("verify", ("model",)),
    ("cup-1", 12, "check"): ("verify", ("harness",)),
    ("cup-1", 13, "context"): ("verify", ("model", "operator")),
    ("suncatcher-2", 14, "context"): ("job", ("model",)),
    ("suncatcher-2", 15, "check"): ("process", ("harness",)),
    ("suncatcher-2", 16, "check"): ("verify", ("harness",)),
    ("suncatcher-2", 17, "build"): ("job", ("model",)),
    ("suncatcher-2", 18, "check"): ("verify", ("harness",)),
    ("suncatcher-2", 19, "context"): ("process", ("model",)),
    ("suncatcher-2", 20, "build"): ("job", ("model",)),
    ("suncatcher-2", 21, "check"): ("process", ("harness",)),
    ("suncatcher-2", 22, "check"): ("verify", ("harness",)),
    ("suncatcher-2", 23, "build"): ("memory", ("model",)),
    ("suncatcher-2", 24, "check"): ("verify", ("harness",)),
}
#: Any other lesson from before: its area and routes by its old kind, as the Sponsor settled the
#: routes, and a context lesson to the operator, who reads every one.
BY_OLD_KIND: dict[str, tuple[str, tuple[str, ...]]] = {
    "build": ("job", ("model",)),
    "check": ("verify", ("harness",)),
    "context": ("job", ("operator",)),
}


def migrate(ledger: DesignLedger) -> list[int]:
    """Each lesson from before the five areas, sorted once, in place, with a step saying what it
    was: its area, its routes, what it becomes, its keys from its evidence, and a lesson already
    promoted as delivered on its route. Then, the first time, every lesson's status from each
    run's latest score, each change a step (lesson 7's corrected score left it `broken`). Every
    row is kept, and it's done again only for a lesson older code writes later."""
    old = ledger.rows(
        f"SELECT * FROM lessons WHERE kind IN ({','.join('?' * len(OLD_KINDS))}) "  # noqa: S608
        "ORDER BY id",
        *OLD_KINDS,
    )
    first = not ledger.rows("SELECT 1 FROM lesson_routes LIMIT 1")
    for row in old:
        lesson, kind = int(row["id"]), str(row["kind"])
        area_, routes = SORTED.get((str(row["run"]), lesson, kind)) or BY_OLD_KIND[kind]
        becomes = row["becomes"]
        if "harness" not in routes:
            becomes = "request" if "request" in routes else None
        ledger.change("UPDATE lessons SET kind=?, becomes=? WHERE id=?", area_, becomes, lesson)
        _set_routes(ledger, lesson, routes, HARNESS)
        if not keys_of(ledger, lesson):
            held = json.loads(row["evidence"])
            _set_keys(ledger, lesson, keys_from(ledger, str(row["run"]), held))
        if row["became"] and row["became"] in BECAME and BECAME[row["became"]] in routes:
            mark = ledger.rows(
                "SELECT MAX(id) AS n FROM lesson_scores WHERE lesson=?", lesson
            )[0]["n"]
            ledger.change(
                "UPDATE lesson_routes SET became=?, became_where=?, delivered_at=?, "
                "after_score=? WHERE lesson=? AND route=?",
                row["became"],
                row["became_where"],
                row["status_at"] or now(),
                int(mark or 0),
                lesson,
                BECAME[row["became"]],
            )
        was = {"kind": kind, "becomes": row["becomes"]}
        _step(
            ledger,
            lesson,
            str(row["status"]),
            HARNESS,
            f"sorted into the five areas (mqucifer/print-shop#40): {area_}, for "
            f"{', '.join(routes)}" + (f", becomes {becomes}" if becomes else ""),
            was=_json(was),
        )
    if old and first:
        for row in ledger.rows("SELECT id FROM lessons ORDER BY id"):
            settle(
                ledger,
                int(row["id"]),
                HARNESS,
                "put right once (mqucifer/print-shop#40): its status follows each run's latest "
                "score",
            )
    return [int(r["id"]) for r in old]
