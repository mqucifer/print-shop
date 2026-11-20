"""The ask-only lock never strands a run (mqucifer/print-shop#192).

A leg-joint eval hit the answer's limit twice at intake. The lock then allowed only `ask`,
`send_ask`, `blocked`, one build and one bench. Told to `read` its cut-reasoning note, the read
was refused as ask-only; its `ask` was refused for no breakdown and then for nothing shown;
`breakdown` was refused as ask-only; and it ended `blocked` with a design it said was complete. A
pocket eval ended the same way. These are scripted the way both went, on the made-up bracket of
`test_intake.py`, with no Docker and no model call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import transcript
from print_shop.rounds.after_action import how_it_ended
from print_shop.rounds.run import Run
from tests.rounds_fakes import Spoken, tool
from tests.test_intake import BRACKET, BROKEN_DOWN, PLAN_ASK, SENT, begin, covered, picture, told

DESIGN = (
    "The design is settled: an L of 5 mm plate, the arm 120 mm from the wall face to its tip, "
    "a gusset under it, and two 4.5 mm holes 60 mm apart in the plate.\n\n"
    + "Is the gusset's angle right? Let me check it against the shelf again. " * 40
)


def cut() -> Spoken:
    """A call cut at the answer's limit, its reasoning holding a design and no script."""
    return Spoken("", reasoning=DESIGN, finish="length")


def offered(body: dict[str, Any]) -> list[str]:
    return [s["function"]["name"] for s in body["tools"]]


def refused(run: Run) -> list[tuple[str, str]]:
    return [(e["name"], e["why"]) for e in told(run) if e["outcome"] != "ok"]


def ended(run: Run) -> dict[str, Any]:
    return next(e for e in transcript.events_of(run.session.events) if e["kind"] == "ended")


BUILT = [tool("write", path="bracket.step", content="a first bracket")]
NOTE = "record/cut-reasoning-3.md"


def test_a_run_locked_at_intake_reaches_its_plan_stop(tmp_path: Path) -> None:
    """The leg-joint eval's calls, with what's open now: the note read, the one build had, the
    `ask` refused for no breakdown and then no needs, each written, and the plan shown."""
    run = begin(
        tmp_path,
        BRACKET,
        BUILT,
        cut(),
        cut(),
        [tool("read", path=NOTE)],
        [tool("write", path="bracket.step", content="the bracket as the note says")],
        [tool("edit", path="bracket.step", old="the bracket", new="a bracket")],
        [PLAN_ASK],
        [BROKEN_DOWN],
        [PLAN_ASK],
        [tool("needs", needs=covered())],
        [PLAN_ASK],
        SENT,
    )
    picture(run)
    assert run.drive().name == "ask", refused(run)
    model: Any = run.session.send
    locked = str(model.bodies[3]["messages"][-1]["content"])
    assert "It's happened again, 1 call after the last time" in locked
    assert (
        "At intake, `questions`, `breakdown` and `needs` stay open too, as the way to a stop "
        "there." in locked
    ), locked
    assert locked.endswith(
        "`read` still opens your cut-reasoning notes: `record/cut-reasoning-2.md` and "
        f"`{NOTE}`."
    )
    assert offered(model.bodies[3]) == [
        "write", "edit", "read", "run", "bench",
        "questions", "breakdown", "needs", "ask", "send_ask", "blocked",
    ]  # fmt: skip
    replies = told(run)
    read = replies[1]
    assert read["outcome"] == "ok" and "The last design it stated" in read["reply"]
    assert "the arm 120 mm from the wall face to its tip" in read["reply"]
    assert refused(run) == [
        ("edit", "ask_only_spent"),
        ("ask", "no_breakdown"),
        ("ask", "no_needs"),
    ]
    no_breakdown = replies[4]["reply"]
    assert no_breakdown.startswith("The plan shows your breakdown: write it with `breakdown`")
    assert no_breakdown.endswith(
        "(Under the lock: `questions`, `breakdown` and `needs` stay open at intake; you may still "
        f"`run` it and read one check with `bench`; `read` opens `record/cut-reasoning-2.md` and "
        f"`{NOTE}`. If the `ask` can't go through, end with `blocked`, saying what it lacks.)"
    ), no_breakdown
    # The ask is a stop, and the stop names the lock it came through.
    lock = ended(run)["said"]["lock"]
    assert lock["set_at"] == 3
    assert lock["refused"] == [
        {"call": 6, "tool": "edit", "why": "ask_only_spent"},
        {"call": 7, "tool": "ask", "why": "no_breakdown"},
        {"call": 9, "tool": "ask", "why": "no_needs"},
    ]
    assert run.session.only == (), "the stop ends the lock"
    stop = run.ledger.rows(
        "SELECT said FROM timeline WHERE run=? AND kind='stop'", run.state.name
    )
    assert json.loads(stop[-1]["said"])["lock"]["refused"][1]["why"] == "no_breakdown"


def test_a_run_that_cant_ask_under_the_lock_ends_blocked_naming_what_was_missing(
    tmp_path: Path,
) -> None:
    """The pocket eval: locked, it tried to `ask` with nothing to show, had its one bench, and
    ended. The ending names the locks it hit and what the `ask` lacked."""
    run = begin(
        tmp_path,
        BRACKET,
        BUILT,
        cut(),
        cut(),
        [tool("ask", question="Is the bracket right?")],
        [tool("bench", command="info bracket.step")],
        [tool("bench", command="thin bracket.step --min 4")],
        [tool("run", path="make.py")],
        [tool("blocked", reason="The design is settled, but I can't show it.")],
    )
    assert run.drive().name == "blocked"
    assert refused(run)[0] == ("ask", "nothing_shown")
    assert ("bench", "ask_only_spent") in refused(run)
    said = ended(run)["said"]
    assert said["reason"] == "The design is settled, but I can't show it."
    assert said["lock"]["missing"].startswith("Show something: `variants` of your files")
    assert [(h["tool"], h["why"]) for h in said["lock"]["refused"]] == refused(run)
    assert said["lock"]["open"] == [
        "ask", "send_ask", "blocked", "questions", "breakdown", "needs", "read",
    ]  # fmt: skip
    told_after = how_it_ended({"name": "blocked", "said": said})
    assert "under the ask-only lock set at call 3" in told_after
    assert "the last `ask` lacked: Show something" in told_after


def test_a_lock_in_the_rounds_leaves_done_open(tmp_path: Path) -> None:
    """In the rounds, `done` is the stage's way to a stop: it isn't refused as ask-only."""
    run = begin(
        tmp_path,
        BRACKET.replace("- the 2D profile of the arm\n", "").replace(
            "- one bracket printed as a test, before the pair\n", "- no plan\n"
        ),
        [BROKEN_DOWN],
        [tool("needs", needs=covered())],
        BUILT,
        cut(),
        cut(),
        [tool("done", script="make.py", part=["bracket.step"], fix_shape="x", closing="Here.")],
    )
    assert run.drive().name == "blocked"
    model: Any = run.session.send
    assert "done" in offered(model.bodies[5])
    assert "In the rounds, `done` stays open too, as the way to a stop there." in str(
        model.bodies[5]["messages"][-1]["content"]
    )
    done = told(run)[3]
    assert done["name"] == "done" and done["why"] != "ask_only", done
