"""A run that ends without approval, with no Docker (mqucifer/print-shop#32): a person closes it,
the model writes what it learned, and a check that keeps failing is nudged to `ask` or `blocked`.

Every run here is made up in the test, against a scripted model. A bench command is answered by a
box that runs nothing.
"""

from __future__ import annotations

import fcntl
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import after_action, lessons, nudge, prompt, records, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import CLOSED, Refused, Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, tool
from tests.test_lessons import again, approved, cli, lesson, reviewed, rows, waiting

NOTE = "It won't meet the brief as it stands. Stop here."


def ended(root: Path, how: str) -> Run:
    """A run that ended, or waits, in each way a close takes, with a call or two behind it."""
    reading = [tool("read", path="references/note.txt")]
    if how == "blocked":
        model = Scripted(reading, [tool("blocked", reason="No way to hold the pane.")])
        run = Run.start(root, "r", "A block.", [], model, sleep=lambda _: None)
    elif how == "call_cap":
        model = Scripted(reading, reading)
        run = Run.start(root, "r", "A block.", [], model, Limits(calls=2), sleep=lambda _: None)
    elif how == "model_time":
        model = Scripted()
        run = Run.start(
            root, "r", "A block.", [], model, Limits(model_seconds=0.0), sleep=lambda _: None
        )
    elif how in ("stopped", "questions"):
        model = Scripted(reading, [tool("questions", questions="Anything else?")])
        run = Run.start(root, "r", "A block.", [], model, sleep=lambda _: None)
    elif how == "met":
        return waiting(root)
    else:
        raise AssertionError(how)
    (run.work / "references" / "note.txt").write_text("a note\n")
    found = run.drive()
    if how == "stopped":
        (run.folder / "questions" / "1.answers.md").write_text("Nothing else.")
        run.resume()
        (run.folder / "stop").touch()
        found = run.drive()
    assert found.name == how
    return run


@pytest.mark.parametrize(
    ("how", "told"),
    [
        ("blocked", "It ended `blocked`, with your reason: No way to hold the pane."),
        ("call_cap", 'It ended `call_cap`: {"limit": 2}.'),
        ("model_time", 'It ended `model_time`: {"limit": 0.0}.'),
        ("stopped", 'It ended `stopped`: {"by": "stop file"}.'),
        ("questions", "It was waiting for the answers to your questions (1)."),
        ("met", "It was waiting at stop 1 for approval: every need had passed its check."),
    ],
)
def test_a_run_that_ended_without_approval_is_closed_and_the_model_says_what_didnt_work(
    tmp_path: Path, how: str, told: str
) -> None:
    run = ended(tmp_path, how)
    run, _ = again(run)
    closing = run.close("stand-in: the operator", NOTE)
    assert closing.name == CLOSED
    assert closing.said["was"]["name"] == how
    decided = rows(run.ledger, "SELECT * FROM decisions")
    assert [(d["decision"], d["by"], d["note"]) for d in decided] == [
        ("closed", "stand-in: the operator", NOTE)
    ]
    assert decided[0]["approval"] == (1 if how == "met" else None)
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "closed"
    assert run.state.stop is None

    run, model = again(run, reviewed(lesson("Ask before the third try.")))
    assert run.after_action_review().name == after_action.REVIEWED
    messages = model.bodies[0]["messages"]
    assert messages[-2]["content"] == (
        "A stand-in for the Sponsor (the operator) closed this run without approving it. "
        f"{told} Their note:\n\n{NOTE}"
    )
    asked = messages[-1]["content"]
    assert asked.startswith("This run is closed without approval.")
    for wanted in ("what didn't work, and why", "would have changed it", "do first next time"):
        assert wanted in asked
    assert "approved" not in asked and "approved" not in json.dumps(model.bodies[0]["tools"])
    assert {t["function"]["name"] for t in model.bodies[0]["tools"]} == {"read", "after_action"}
    tried = rows(run.ledger, "SELECT * FROM after_actions")
    assert [(t["decision"], t["approval"], t["ended"]) for t in tried] == [
        (decided[0]["id"], decided[0]["approval"], "reviewed")
    ]
    said = rows(run.ledger, "SELECT kind, from_who, text FROM interventions WHERE kind='closed'")
    assert said == [{"kind": "closed", "from_who": "stand-in: the operator", "text": NOTE}]
    written = rows(run.ledger, "SELECT status, written_by FROM lessons")
    assert written == [{"status": "candidate", "written_by": "model"}], "a reviewer checks it"
    listed = lessons.listing(run.ledger, "r")
    assert "After-action review 1 (after closed by stand-in: the operator): reviewed" in listed
    # The run stays closed, and nothing goes on from it.
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "closed"
    run, _ = again(run)
    for refused in (
        lambda: run.resume("more"),
        lambda: run.send_back("sponsor", "again"),
        lambda: run.approve("sponsor"),
        lambda: run.close("sponsor", NOTE),
    ):
        with pytest.raises(Refused, match="closed|isn't waiting"):
            refused()
    with pytest.raises(Refused, match="review is in"):
        run.after_action_review()


def test_a_close_says_who_and_why_and_isnt_for_an_approved_run(tmp_path: Path) -> None:
    run = ended(tmp_path, "blocked")
    for by in ("", "the operator", "reviewer: the operator"):
        with pytest.raises(Refused, match="says who"):
            run.close(by, NOTE)
    with pytest.raises(Refused, match="needs its note"):
        run.close("sponsor", " ")
    assert rows(run.ledger, "SELECT * FROM decisions") == []
    other = approved(tmp_path / "other")
    with pytest.raises(Refused, match="approved"):
        other.close("sponsor", NOTE)


def test_a_review_that_fails_after_a_close_leaves_it_closed_and_can_go_again(
    tmp_path: Path,
) -> None:
    run = ended(tmp_path, "blocked")
    run.close("sponsor", NOTE)
    assert run.after_action_not_begun("no key").name == "not_begun"
    run, model = again(run, *(["just words"] * 10))
    assert run.after_action_review().name == "no_tool_call"
    assert rows(run.ledger, "SELECT ending FROM runs")[0]["ending"] == "closed"
    run, model = again(run, reviewed())
    assert run.after_action_review().name == "reviewed"
    closes = [m for m in model.bodies[0]["messages"] if "closed this run" in str(m["content"])]
    assert len(closes) == 1, "the close reached it once"
    tried = rows(run.ledger, "SELECT n, ended FROM after_actions ORDER BY n")
    assert [t["ended"] for t in tried] == ["not_begun", "no_tool_call", "reviewed"]


def test_the_command_line_closes_only_once_the_runs_process_is_gone(tmp_path: Path) -> None:
    run = ended(tmp_path, "blocked")
    note = tmp_path / "note.md"
    note.write_text(NOTE)
    words = ("resume", "r", "--close", "--by", "sponsor", "--note", str(note))
    with (tmp_path / ".session.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        busy = cli(tmp_path, *words)
    assert busy.returncode == 3 and "another session is running" in busy.stderr
    assert rows(run.ledger, "SELECT * FROM decisions") == []
    assert cli(tmp_path, "resume", "r", "--close", "--by", "sponsor").returncode == 3
    done = cli(tmp_path, *words)
    assert done.returncode == 20, done.stderr
    assert "closed without approval, by sponsor: it was blocked" in done.stdout
    assert "the after-action review didn't begin" in done.stdout
    assert "The run stays closed." in done.stdout
    assert rows(run.ledger, "SELECT ended FROM after_actions") == [{"ended": "not_begun"}]
    assert cli(tmp_path, "after-action", "r").returncode == 0
    assert len(rows(run.ledger, "SELECT * FROM after_actions")) == 2
    assert cli(tmp_path, *words).returncode == 3, "it's closed already"
    assert cli(tmp_path, "lessons", "r").returncode == 0


# The nudge


def test_a_check_is_its_command_and_its_files_whatever_its_other_arguments() -> None:
    lower = ["capture", "frame.step", "--pocket-z", "2", "--min-lip", "0.35"]
    assert nudge.check_of(lower) == ("capture", ("frame.step",))
    assert nudge.check_of(["bench", *lower[:2], "--min-lip", "1.2"]) == nudge.check_of(lower)
    assert nudge.check_of(["clearance", "b.stl", "a.step", "--min-gap", "0.2"]) == (
        "clearance",
        ("a.step", "b.stl"),
    )
    assert nudge.check_of(["render", "a.step", "--cut=x=50,y=20:40"]) == ("render", ("a.step",))
    capture, thin = (["capture", "a.step"], ["thin", "a.step"])
    calls = [(capture, 1), (capture, 0), (capture, 1), (thin, 1), (capture, 3), (capture, 2)]
    assert nudge.failing(calls, nudge.check_of(capture)) == 2, "a pass starts it again"
    assert nudge.failing(calls, nudge.check_of(thin)) == 1


class Answers:
    """A box that answers each bench command with the next exit, and runs nothing."""

    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.asked: list[list[str]] = []

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(args)
        code = self.codes.pop(0)
        return Ran(code, f"verdict: {'OK' if code == 0 else 'FAIL'}: as scripted\n", 0.1)


def benched(root: Path, *commands: str, codes: tuple[int, ...]) -> tuple[Run, list[dict[str, Any]]]:
    model = Scripted(
        *[[tool("bench", command=c)] for c in commands],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(root, "r", "A block.", [], model, sleep=lambda _: None)
    box: Any = Answers(*codes)
    run.tools.box = box
    assert run.drive().name == "blocked"
    told = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    return run, told[: len(commands)]


def test_a_check_that_keeps_failing_is_nudged_to_ask_or_end_and_nothing_is_cut(
    tmp_path: Path,
) -> None:
    lip = "capture frame.step --pocket-z 2 --clearance 0.4 --pane-thickness 2 --min-lip"
    commands = (
        f"{lip} 1.2",
        f"{lip} 0.8",
        "thin frame.step",
        f"{lip} 0.45",
        "capture frame.step --pocket-z 2 --clearance 0.4",  # the command was wrong
        f"{lip} 0.35",
        f"{lip} 0.3",
        f"{lip} 1.2",
    )
    run, told = benched(tmp_path, *commands, codes=(1, 1, 1, 2, 3, 1, 1, 0))
    nudged = [i for i, e in enumerate(told) if prompt.STUCK in e["reply"]]
    assert nudged == [5, 6], "from the 4th failure of capture on frame.step, its arguments aside"
    assert told[5]["reply"].endswith(
        "`capture` on `frame.step` has failed 4 times in a row, whatever its other arguments. "
        f"{prompt.STUCK}"
    )
    assert told[6]["detail"]["nudge"] == {
        "command": "capture",
        "files": ["frame.step"],
        "failures": 5,
        "after": nudge.AFTER,
    }
    assert told[7]["outcome"] == "ok" and "nudge" not in told[7]["detail"]
    kept = rows(run.ledger, "SELECT seq, at_call, command, files, failures, after FROM nudges")
    assert kept == [
        {"seq": 1, "at_call": 6, "command": "capture", "files": '["frame.step"]', "failures": 4,
         "after": 4},
        {"seq": 2, "at_call": 7, "command": "capture", "files": '["frame.step"]', "failures": 5,
         "after": 4},
    ]
    records.read_back(run.folder, run.ledger)
    assert len(rows(run.ledger, "SELECT * FROM nudges")) == 2, "a second reading adds nothing"


def test_a_run_resumed_on_this_code_counts_the_failures_it_had_before(tmp_path: Path) -> None:
    """As `suncatcher-2` will be: five failures of one check from older code, then one more."""
    model = Scripted([tool("questions", questions="Anything?")])
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.drive().name == "questions"
    for lip in ("1.2", "0.35", "0.45", "0.3", "0.25"):
        argv = ["capture", "frame.step", "--min-lip", lip]
        run.ledger.bench_call("r", 1, 1, argv, 1, None, {}, 1.0)
    (run.folder / "questions" / "1.answers.md").write_text("No.")
    run, _ = again(
        run,
        [tool("bench", command="capture frame.step --min-lip 0.2")],
        [tool("blocked", reason="It can't hold.")],
    )
    run.resume()
    box: Any = Answers(1)
    run.tools.box = box
    assert run.drive().name == "blocked"
    told = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    assert "has failed 6 times in a row" in told[-2]["reply"]


def test_the_prompt_and_the_refusals_that_point_to_blocked_say_the_same() -> None:
    assert prompt.STUCK[0].lower() + prompt.STUCK[1:] in prompt.SYSTEM
    assert "truly can't be done" not in prompt.SYSTEM
    assert "failed, complete run is a valid end" in prompt.STUCK
    assert "show what you have and ask what you need" in prompt.STUCK.lower()


def test_the_settled_needs_refusals_say_the_same(tmp_path: Path) -> None:
    run = ended(tmp_path, "questions")
    run.state.needs_open = False
    for name in ("questions", "needs"):
        judged = getattr(run, name)({"questions": "?", "needs": []})
        assert judged.why == "needs_settled" and prompt.STUCK in judged.reply


# The ledger


OLD = """
CREATE TABLE runs (run TEXT PRIMARY KEY, started_at TEXT NOT NULL);
CREATE TABLE approvals (run TEXT NOT NULL REFERENCES runs(run), n INTEGER NOT NULL,
    round INTEGER NOT NULL, asked_at TEXT NOT NULL, at_call INTEGER NOT NULL, sheet TEXT,
    sheet_sha TEXT, part_rows TEXT NOT NULL, part_hashes TEXT NOT NULL, closing_model_said TEXT,
    PRIMARY KEY (run, n));
CREATE TABLE decisions (id INTEGER PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(run),
    approval INTEGER NOT NULL, decision TEXT NOT NULL, by TEXT NOT NULL, note TEXT,
    at TEXT NOT NULL, FOREIGN KEY (run, approval) REFERENCES approvals(run, n));
CREATE TABLE after_actions (run TEXT NOT NULL REFERENCES runs(run), n INTEGER NOT NULL,
    approval INTEGER NOT NULL, begun_at TEXT NOT NULL, first_call INTEGER NOT NULL,
    harness_commit TEXT NOT NULL, ended TEXT, ended_said TEXT, ended_at TEXT, last_call INTEGER,
    PRIMARY KEY (run, n));
INSERT INTO runs VALUES ('old', 't');
INSERT INTO approvals VALUES ('old', 1, 1, 't', 3, NULL, NULL, '[]', '{}', 'Here.');
INSERT INTO approvals VALUES ('old', 2, 2, 't', 9, NULL, NULL, '[]', '{}', 'Here again.');
INSERT INTO decisions VALUES (1, 'old', 1, 'sent_back', 'sponsor', 'Too thin.', '2026-11-02T10');
INSERT INTO decisions VALUES (2, 'old', 2, 'approved', 'sponsor', NULL, '2026-11-02T12');
INSERT INTO after_actions VALUES ('old', 1, 2, '2026-11-02T13', 10, 'c', 'not_begun', '{}',
    '2026-11-02T13', 9);
"""


def test_a_ledger_from_before_keeps_every_row_and_says_which_decision_each_review_follows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript(OLD)
    ledger = DesignLedger(path)
    kept = rows(ledger, "SELECT id, approval, decision FROM decisions")
    assert [tuple(d.values()) for d in kept] == [(1, 1, "sent_back"), (2, 2, "approved")]
    assert rows(ledger, "SELECT n, approval, decision, ended FROM after_actions") == [
        {"n": 1, "approval": 2, "decision": 2, "ended": "not_begun"}
    ]
    # A close of a run that never stopped for approval has none, and older code still inserts.
    assert ledger.decided("old", None, "closed", "sponsor", "Stop.") == 3
    ledger.add(
        "after_actions", run="old", n=2, approval=2, begun_at="t", first_call=1, harness_commit="c"
    )
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT sql FROM sqlite_master WHERE name='decisions'").fetchone()
    assert rows(DesignLedger(path), "SELECT decision FROM after_actions WHERE n=2") == [
        {"decision": 2}
    ], "a review older code wrote names its decision when the ledger is next opened"
    with sqlite3.connect(path) as db:
        after = db.execute("SELECT sql FROM sqlite_master WHERE name='decisions'").fetchone()
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert before == after, "made again once"
    assert len(rows(DesignLedger(path), "SELECT * FROM decisions")) == 3


def test_a_decision_reaches_the_model_on_the_first_review_that_begins(tmp_path: Path) -> None:
    """With no key the first try never begins, so the approval is told at the next one."""
    run = approved(tmp_path)
    run.after_action_not_begun("no key")
    run, model = again(run, reviewed())
    assert run.after_action_review().name == "reviewed"
    told = [m for m in model.bodies[0]["messages"] if "approved the part" in str(m["content"])]
    assert len(told) == 1
