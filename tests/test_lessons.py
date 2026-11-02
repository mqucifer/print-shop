"""Lessons with no Docker: the after-action review, the reviewer's step, giving and scoring.

A run here reaches its stop for approval by having its state put there, with no clean run, so
everything after it is the harness's own code against a scripted model. `test_lessons_in_image.py`
goes the whole way in real containers.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from print_shop.rounds import after_action, lessons, transcript
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Refused, Run
from tests.rounds_fakes import Scripted, tool

SCRIPT = Path(__file__).parent.parent / "scripts" / "rounds.py"
BY = "stand-in: a reviewer in a test"


def lesson(
    text: str = "Lay it out in 2D first.", kind: str = "build", **more: Any
) -> dict[str, Any]:
    return {"fix_shape": "plan in 2D", "kind": kind, "text": text, "calls": [1], **more}


def reviewed(*found: dict[str, Any], scores: Any = ()) -> list[dict[str, Any]]:
    return [tool("after_action", lessons=list(found), scores=list(scores))]


def waiting(root: Path, name: str = "r", *turns: Any) -> Run:
    """A run of three calls, put at its stop for approval as if every need had passed."""
    model = Scripted(
        [tool("read", path="references/note.txt")],
        [tool("read", path="references/note.txt")],
        [tool("questions", questions="Anything else?")],
        *turns,
    )
    run = Run.start(root, name, "A block.", [], model, sleep=lambda _: None)
    (run.work / "references" / "note.txt").write_text("a note\n")
    assert run.drive().name == "questions"
    run.state.stop = {"kind": "met", "n": 1}
    run.state.approvals = 1
    run.ledger.approval_asked(
        name,
        1,
        round=1,
        at_call=3,
        sheet=None,
        sheet_sha=None,
        part_rows=[],
        part_hashes={},
        closing="Here it is.",
    )
    run.save()
    return run


def approved(root: Path, name: str = "r") -> Run:
    run = waiting(root, name)
    run.approve("sponsor", "Print it.")
    return run


def again(run: Run, *turns: Any) -> tuple[Run, Scripted]:
    model = Scripted(*turns)
    return Run.load(run.folder, model, sleep=lambda _: None), model


def rows(ledger: DesignLedger, sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in ledger.rows(sql, *args)]


# The after-action review


def test_the_model_writes_its_lessons_after_the_approval_each_held_to_the_ledger(
    tmp_path: Path,
) -> None:
    run = approved(tmp_path)
    run, model = again(
        run,
        reviewed(lesson(calls=[99]), lesson(kind="advice")),
        [tool("write", path="x.py", content="")],
        reviewed(lesson(readings=[{"round": 1, "need": "gap"}])),
        reviewed(lesson(), lesson("Check every pane.", kind="check", calls=[2, 3])),
    )
    ending = run.after_action_review()
    assert ending.name == after_action.REVIEWED
    told = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"][-4:]
    assert told[0]["outcome"] == "failed"
    assert "no call 99" in told[0]["reply"] and "build, check or context" in told[0]["reply"]
    assert "There's no tool `write`" in told[1]["reply"]
    assert "no reading of `gap` in round 1" in told[2]["reply"]
    assert told[3]["outcome"] == "ok"
    # Only reading and the review itself, and the run's index to name calls from.
    assert {t["function"]["name"] for t in model.bodies[0]["tools"]} == {"read", "after_action"}
    asked = model.bodies[0]["messages"][-1]["content"]
    assert "The part is approved" in asked and "1: read" in asked and "3: questions" in asked
    assert model.bodies[0]["messages"][-2]["content"] == (
        "The Sponsor approved the part. Their note:\n\nPrint it."
    )
    written = rows(run.ledger, "SELECT * FROM lessons ORDER BY id")
    assert [(w["kind"], w["status"], w["written_by"]) for w in written] == [
        ("build", "candidate", "model"),
        ("check", "candidate", "model"),
    ]
    assert written[0]["at_call"] == 7
    assert json.loads(written[1]["evidence"]) == {"calls": [2, 3], "readings": []}
    tried = rows(run.ledger, "SELECT * FROM after_actions")
    assert [
        (t["n"], t["approval"], t["ended"], t["first_call"], t["last_call"]) for t in tried
    ] == [(1, 1, "reviewed", 4, 7)]
    # The run stays approved, and the review is its own stretch of the timeline.
    assert rows(run.ledger, "SELECT ending, calls FROM runs")[0] == {
        "ending": "approved",
        "calls": 7,
    }
    assert json.loads((run.folder / "state.json").read_text())["ending"]["name"] == "approved"
    kinds = [r["kind"] for r in rows(run.ledger, "SELECT kind FROM timeline ORDER BY seq")]
    assert kinds[-2:] == ["after_action", "stop"]
    said = rows(run.ledger, "SELECT kind, from_who, text FROM interventions")
    assert said == [{"kind": "approved", "from_who": "sponsor", "text": "Print it."}]
    with pytest.raises(Refused, match="review is in"):
        again(run)[0].after_action_review()


def test_a_review_that_fails_leaves_the_run_approved_and_can_go_again(tmp_path: Path) -> None:
    run = approved(tmp_path)
    run, model = again(run)
    request = httpx.Request("POST", "http://proxy.invalid/v1")
    model.failures.append(
        httpx.HTTPStatusError("no", request=request, response=httpx.Response(401))
    )
    assert run.after_action_review().name == "call_failed"
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "approved"
    run, model = again(run, reviewed(lesson()))
    assert run.after_action_review().name == "reviewed"
    tried = rows(run.ledger, "SELECT n, ended FROM after_actions ORDER BY n")
    assert tried == [{"n": 1, "ended": "call_failed"}, {"n": 2, "ended": "reviewed"}]
    approvals = [m for m in model.bodies[0]["messages"] if "approved the part" in str(m["content"])]
    assert len(approvals) == 1, "the approval reached it once"


def test_a_review_that_runs_on_ends_by_name_and_stays_approved(tmp_path: Path) -> None:
    run = approved(tmp_path)
    run, _ = again(run, *(["just words"] * 10))
    assert run.after_action_review().name == "no_tool_call"
    run, _ = again(run, *([[tool("read", path="references/note.txt")]] * 30))
    assert run.after_action_review().name == "call_cap"
    assert run.session.limits.calls == 2000, "the run's own cap is back"
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "approved"


def test_a_review_that_cant_begin_is_said(tmp_path: Path) -> None:
    run = approved(tmp_path)
    assert run.after_action_not_begun("no key").name == "not_begun"
    tried = rows(run.ledger, "SELECT ended, ended_said FROM after_actions")[0]
    assert tried == {"ended": "not_begun", "ended_said": '{"why": "no key"}'}


def test_there_is_no_review_before_the_approval(tmp_path: Path) -> None:
    run = waiting(tmp_path)
    with pytest.raises(Refused, match="after a person's approval"):
        run.after_action_review()
    judged = run.after_action({"lessons": [], "scores": []})
    assert judged.outcome == "failed" and judged.why == "not_approved"
    assert "after_action" not in {s["function"]["name"] for s in run.tools.specs}


# The reviewer's step


def candidates(tmp_path: Path) -> tuple[DesignLedger, list[int]]:
    run = approved(tmp_path)
    run, _ = again(
        run,
        reviewed(
            lesson(),
            lesson("Check every pane with thin.", kind="check"),
            lesson("The Sponsor's picture comes late.", kind="context"),
        ),
    )
    run.after_action_review()
    return run.ledger, [int(r["id"]) for r in run.ledger.rows("SELECT id FROM lessons")]


def test_each_step_is_signed_and_kept_with_why(tmp_path: Path) -> None:
    ledger, (build, check, context) = candidates(tmp_path)
    with pytest.raises(lessons.Refused, match="says who"):
        lessons.accept(ledger, build, "the operator")
    with pytest.raises(lessons.Refused, match="never given as a note"):
        lessons.accept(ledger, check, BY)
    lessons.accept(ledger, check, BY, "refusal")
    lessons.edit(ledger, build, BY, text="Lay the cells out in 2D first.", why="plainer")
    with pytest.raises(lessons.Refused, match="says why"):
        lessons.reject(ledger, context, BY, " ")
    lessons.reject(ledger, context, BY, "for the people running it, not the model")
    found = {r["id"]: r for r in rows(ledger, "SELECT * FROM lessons")}
    assert [found[i]["status"] for i in (build, check, context)] == [
        "edited",
        "accepted",
        "rejected",
    ]
    assert found[build]["text"] == "Lay the cells out in 2D first."
    assert found[check]["becomes"] == "refusal"
    steps = rows(ledger, "SELECT * FROM lesson_steps WHERE lesson=? ORDER BY id", build)
    assert [(s["status"], s["by"]) for s in steps] == [("candidate", "model"), ("edited", BY)]
    assert json.loads(steps[1]["was"])["text"] == "Lay it out in 2D first."
    # Made into one about checking, it says what it becomes.
    with pytest.raises(lessons.Refused, match="never given as a note"):
        lessons.edit(ledger, build, BY, kind="check")
    with pytest.raises(lessons.Refused, match="it becomes refusal or bench_check"):
        lessons.promote(ledger, check, BY, "note", "nowhere")
    lessons.promote(ledger, check, BY, "refusal", "mqucifer/print-shop#26")
    assert lessons.one(ledger, check)["became_where"] == "mqucifer/print-shop#26"


def test_the_listing_shows_each_lesson_with_what_the_ledger_holds_where_it_points(
    tmp_path: Path,
) -> None:
    ledger, _ = candidates(tmp_path)
    shown = lessons.listing(ledger, "r")
    assert "After-action review 1 (approval 1): reviewed, from call 4 to 4" in shown
    assert "## Lesson 1: candidate" in shown and "Written by** model, at call 4" in shown
    assert "call 1 (round 0): read" in shown
    assert "It was given" not in shown
    assert "1. [candidate] how to build" in lessons.listing(ledger)
    with pytest.raises(lessons.Refused, match="isn't in this ledger"):
        lessons.listing(ledger, "nobody")


# Giving them at the start, and scoring them


def test_a_new_run_starts_with_the_kept_lessons_about_how_to_build_and_nothing_else(
    tmp_path: Path,
) -> None:
    ledger, (build, check, context) = candidates(tmp_path)
    lessons.accept(ledger, build, BY)
    lessons.accept(ledger, check, BY, "bench_check")
    lessons.accept(ledger, context, BY)
    model = Scripted([tool("blocked", reason="x")])
    run = Run.start(tmp_path, "next", "A frame.", [], model, sleep=lambda _: None)
    run.drive()
    first = model.bodies[0]["messages"][1]["content"]
    assert lessons.HEADING in first
    assert f"**Lesson {build}**, from `r`, written by the model: Lay it out in 2D first." in first
    assert "Check every pane" not in first and "comes late" not in first
    assert first.index("A frame.") < first.index(lessons.HEADING) < first.index("skill")
    given = rows(ledger, "SELECT * FROM run_lessons WHERE run='next'")
    assert [(g["lesson"], g["how"]) for g in given] == [(build, "start")]
    assert given[0]["text"] in first
    assert lessons.one(ledger, build)["status"] == "given"
    assert (run.folder / "lessons.md").read_text().startswith(lessons.HEADING)
    # A conversation started again from the record has them too.
    run._from_the_record()
    assert lessons.HEADING in run.session.messages[1]["content"]
    # A run with none kept has no heading.
    lessons.retire(ledger, build, BY, "a test")
    quiet = Scripted([tool("blocked", reason="x")])
    Run.start(tmp_path, "after", "A frame.", [], quiet, sleep=lambda _: None).drive()
    assert lessons.HEADING not in quiet.bodies[0]["messages"][1]["content"]


def scored_run(root: Path, name: str, score: str) -> Run:
    """A run given every kept lesson, approved, whose review scores lesson 1."""
    run = approved(root, name)
    run, _ = again(run, reviewed(scores=[{"lesson": 1, "score": score, "calls": [2]}]))
    assert run.after_action_review().name == "reviewed"
    return run


def test_every_lesson_given_is_scored_once_and_moves_by_its_score(tmp_path: Path) -> None:
    ledger, (build, _, _) = candidates(tmp_path)
    lessons.accept(ledger, build, BY)
    run = approved(tmp_path, "second")
    asked: list[Any] = [
        reviewed(),
        reviewed(scores=[{"lesson": 7, "score": "used", "calls": [1]}]),
        reviewed(scores=[{"lesson": build, "score": "liked", "calls": [1]}]),
        reviewed(scores=[{"lesson": build, "score": "used"}]),
        reviewed(scores=[{"lesson": build, "score": "used", "calls": [2], "why": "from call 2"}]),
    ]
    run, model = again(run, *asked)
    assert run.after_action_review().name == "reviewed"
    replies = [e["reply"] for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    assert f"lessons {build} were given and aren't scored" in replies[-5]
    assert f"you were given lessons {build}, not 7" in replies[-4]
    assert "used, not_used or in_the_way" in replies[-3]
    assert "it needs the calls that show it" in replies[-2]
    assert f"Lesson {build}, given start" in model.bodies[0]["messages"][-1]["content"]
    found = rows(ledger, "SELECT * FROM lesson_scores")
    assert [(f["run"], f["score"], f["by"], f["calls"]) for f in found] == [
        ("second", "used", "model", "[2]")
    ]
    assert lessons.one(ledger, build)["status"] == "held"
    assert not lessons.to_retire(ledger)


def test_a_lesson_in_the_way_or_unused_twice_is_shown_to_retire(tmp_path: Path) -> None:
    ledger, (build, _, _) = candidates(tmp_path)
    lessons.accept(ledger, build, BY)
    scored_run(tmp_path, "a", "not_used")
    assert lessons.one(ledger, build)["status"] == "given"
    assert not lessons.to_retire(ledger)
    scored_run(tmp_path, "b", "not_used")
    assert lessons.to_retire(ledger) == [(lessons.one(ledger, build), "given and not used on a, b")]
    scored_run(tmp_path, "c", "in_the_way")
    assert lessons.one(ledger, build)["status"] == "broken"
    retiring = lessons.to_retire(ledger)
    assert retiring[0][1] == "got in the way on c"
    assert "## To retire, for the reviewer" in lessons.listing(ledger, "c")
    # A reviewer scores again, and the latest is what counts.
    held, _ = lessons.evidence(ledger, "c", [3])
    lessons.score(ledger, "c", build, "used", held, BY, "it helped at call 3")
    assert lessons.one(ledger, build)["status"] == "held"
    assert lessons.to_retire(ledger)[0][1] == "given and not used on a, b"
    lessons.retire(ledger, build, BY, "not used twice")
    assert not lessons.to_retire(ledger) and not lessons.to_give(ledger)


# Lessons a person writes, and the command line


def steps_file(tmp_path: Path, steps: list[Any]) -> Path:
    path = tmp_path / "steps.json"
    path.write_text(json.dumps({"steps": steps}))
    return path


def test_a_reviewer_loads_lessons_from_a_file_checked_whole_first(tmp_path: Path) -> None:
    run = approved(tmp_path, "older")
    waiting(tmp_path, "newer")
    ledger = run.ledger
    steps: list[dict[str, Any]] = [
        {"do": "write", "key": "a", "run": "older", "fix_shape": "2D", "kind": "build"}
        | {"text": "Lay it out in 2D.", "calls": [1, 2]},
        {"do": "write", "key": "b", "run": "older", "fix_shape": "counts", "kind": "check"}
        | {"text": "Count the panes.", "calls": [3]},
        {"do": "accept", "lesson": "a"},
        {"do": "accept", "lesson": "b", "becomes": "bench_check"},
        {"do": "give", "lesson": "a", "run": "newer", "how": "by hand, in the request"}
        | {"text": "Lay it out in 2D, as given."},
        {"do": "give", "lesson": "b", "run": "newer", "how": "by hand, in the request"}
        | {"text": "Count them."},
        {"do": "score", "lesson": "a", "run": "newer", "score": "used", "calls": [1]},
        {"do": "score", "lesson": "b", "run": "newer", "score": "in_the_way", "why": "bent"},
        {"do": "promote", "lesson": "b", "as": "bench_check", "where": "mqucifer/print-shop#24"},
    ]
    bad = [
        *steps,
        {"do": "write", "key": "a", "run": "nobody", "fix_shape": "", "kind": "x"}
        | {"text": "", "calls": []},
        {"do": "write", "key": "c", "run": "older", "fix_shape": " ", "kind": "x"}
        | {"text": "x", "calls": [1]},
    ]
    with pytest.raises(lessons.Refused) as refused:
        lessons.load(ledger, steps_file(tmp_path, bad), BY)
    assert "two lessons are `a`" in str(refused.value)
    assert "`nobody` isn't in this ledger" in str(refused.value)
    assert "step 11: a kind is build, check, context" in str(refused.value)
    assert "step 11: it needs its fix's shape and its text" in str(refused.value)
    assert not ledger.rows("SELECT * FROM lessons"), "nothing was written"
    with pytest.raises(lessons.Refused, match="says who"):
        lessons.load(ledger, steps_file(tmp_path, steps), "model")
    ids = lessons.load(ledger, steps_file(tmp_path, steps), BY)
    a, b = lessons.one(ledger, ids["a"]), lessons.one(ledger, ids["b"])
    assert (a["status"], a["written_by"], b["status"], b["became"]) == (
        "held",
        BY,
        "promoted",
        "bench_check",
    )
    given = rows(ledger, "SELECT lesson, how, text FROM run_lessons ORDER BY lesson")
    assert given[0] == {"lesson": a["id"], "how": "by hand, in the request"} | {
        "text": "Lay it out in 2D, as given."
    }
    with pytest.raises(lessons.Refused, match="in the ledger already"):
        lessons.load(ledger, steps_file(tmp_path, steps), BY)
    # One about checking was given by hand once, as a record: code never gives one.
    assert [g["id"] for g in lessons.to_give(ledger)] == [a["id"]]


def test_a_ledger_from_before_lessons_gains_them_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE runs (run TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
        db.execute("INSERT INTO runs VALUES ('old', 'then')")
    ledger = DesignLedger(path)
    assert rows(ledger, "SELECT run FROM runs") == [{"run": "old"}]
    for table in ("lessons", "lesson_steps", "run_lessons", "lesson_scores", "after_actions"):
        assert ledger.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 0  # noqa: S608


def cli(root: Path, *words: str) -> subprocess.CompletedProcess[str]:
    """The command line, with a home of its own: no key, so nothing reaches LiteLLM."""
    env = {k: v for k, v in os.environ.items() if "KEY" not in k} | {"HOME": str(root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *words],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_the_command_line_approves_and_says_the_review_didnt_begin(tmp_path: Path) -> None:
    run = waiting(tmp_path)
    done = cli(tmp_path, "resume", "r", "--approve", "--by", "sponsor")
    assert done.returncode == 0, done.stderr
    assert "the after-action review didn't begin" in done.stdout
    assert "scripts/rounds.py after-action r" in done.stdout
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "approved"
    assert rows(run.ledger, "SELECT ended FROM after_actions") == [{"ended": "not_begun"}]
    again_ = cli(tmp_path, "after-action", "r")
    assert again_.returncode == 0 and "didn't begin" in again_.stdout
    assert len(rows(run.ledger, "SELECT * FROM after_actions")) == 2


def test_the_reviewer_steps_through_the_command_line_on_the_ledger_it_names(
    tmp_path: Path,
) -> None:
    ledger, (build, check, _) = candidates(tmp_path)
    copy = tmp_path / "copy.sqlite"
    copy.write_bytes((tmp_path / "ledger.sqlite").read_bytes())
    before = (tmp_path / "ledger.sqlite").read_bytes()
    listed = cli(tmp_path, "lessons", "r", "--ledger", str(copy))
    assert listed.returncode == 0 and "## Lesson 1: candidate" in listed.stdout
    unsigned = cli(tmp_path, "lessons", "accept", str(build), "--ledger", str(copy))
    assert unsigned.returncode == 3 and "says who" in unsigned.stderr
    done = cli(tmp_path, "lessons", "accept", str(build), "--by", BY, "--ledger", str(copy))
    assert done.returncode == 0 and f"lesson {build}: accepted, by {BY}" in done.stdout
    checked = cli(tmp_path, "lessons", "accept", str(check), "--by", BY, "--ledger", str(copy))
    assert checked.returncode == 3 and "never given as a note" in checked.stderr
    text = tmp_path / "new.md"
    text.write_text("New words.")
    cli(
        tmp_path,
        "lessons",
        "edit",
        str(build),
        "--text",
        str(text),
        "--by",
        BY,
        "--ledger",
        str(copy),
    )
    assert lessons.one(DesignLedger(copy), build)["text"] == "New words."
    assert (tmp_path / "ledger.sqlite").read_bytes() == before, "only the ledger it was told"
    assert lessons.one(ledger, build)["status"] == "candidate"
