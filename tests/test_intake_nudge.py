"""A run building parts at intake is told to write its breakdown and needs, and to show the plan
(mqucifer/print-shop#146).

In an eval the run was building from call 8, read its own part on the bench at call 35, and kept
its first breakdown at call 98, its needs at 113 and its plan stop at 121, with the design it was
later approved with. No Docker: a scripted model drives each run, and a box answers each bench
command and runs nothing. The request is the made-up shelf bracket of `test_intake.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from print_shop.rounds import nudge
from tests.rounds_fakes import tool
from tests.test_intake import BRACKET, BROKEN_DOWN, LAMP, begin, covered, told

FIRST = (
    "You have a part file, `bracket.step`, read on the bench since this call, and no breakdown or "
    "needs yet. Write your `breakdown` and settle `needs` now, then show the plan with `ask`: "
    "they need only what you know of how it's built and this first try with its readings, not a "
    "finished part. You can go on building meanwhile, and what the plan's answer changes is "
    "cheaper to change now."
)
AGAIN = FIRST.replace("since this call", "since call 2")
BENCH = [tool("bench", command="info bracket.step")]


def wrote(n: int) -> list[Any]:
    return [[tool("write", path=f"try_{k}.py", content=f"# try {k}")] for k in range(n)]


def led(event: dict[str, Any]) -> bool:
    return "intake_nudge" in (event.get("detail") or {})


def test_a_part_read_on_the_bench_at_intake_is_told_what_comes_next_and_again_each_8_calls(
    tmp_path: Path,
) -> None:
    turns = [[tool("write", path="bracket.step", content="a first bracket")], BENCH, *wrote(9)]
    run = begin(tmp_path, BRACKET, *turns)
    assert run.drive().name == "blocked"
    first, read, *after = told(run)[:11]
    assert not led(first), "a part file alone, not yet read on the bench"
    assert read["outcome"] == "ok" and read["reply"].startswith(FIRST + "\n\n"), read["reply"]
    assert read["detail"]["intake_nudge"] == {
        "file": "bracket.step",
        "benched_at": 2,
        "breakdown": False,
        "every": 8,
    }
    # Calls 3 to 9 say nothing more; call 10, 8 after it was said, says it again.
    assert [led(e) for e in after] == [False] * 7 + [True, False]
    assert after[7]["outcome"] == "ok" and after[7]["reply"].startswith(AGAIN + "\n\n")
    assert nudge.INTAKE_EVERY == 8


def test_it_refuses_nothing_and_stops_once_the_breakdown_arrives_then_asks_for_the_needs(
    tmp_path: Path,
) -> None:
    turns = [
        [tool("write", path="bracket.step", content="a first bracket")],
        BENCH,
        [BROKEN_DOWN],
        *wrote(7),
        BENCH,
        [tool("write", path="late.py", content="# still building")],
        [tool("needs", needs=covered())],
        BENCH,
    ]
    run = begin(tmp_path, BRACKET, *turns)
    assert run.drive().name == "blocked"
    events = told(run)[:14]
    assert all(e["outcome"] == "ok" for e in events), [e["reply"] for e in events]
    _, read, kept, *building, benched, late, settled, after = events
    assert led(read)
    assert not led(kept) and kept["reply"].startswith("Kept, as breakdown 1")
    # Calls 4 to 10, after the breakdown at call 3: nothing is said.
    assert [led(e) for e in building] == [False] * 7
    # Call 11, 8 after the breakdown, with no needs: it asks for them, and the plan.
    assert benched["reply"].startswith(
        "You're still at intake, 9 calls after `bracket.step` was first read on the bench, and "
        "your breakdown is kept: settle `needs` next, then show the plan with `ask`."
    ), benched["reply"]
    assert benched["detail"]["intake_nudge"]["breakdown"] is True
    assert not led(late)
    assert settled["outcome"] == "ok" and not led(settled)
    assert not led(after), "once the needs are settled it's gone"


def test_a_breakdown_before_the_part_starts_the_count_from_the_part(tmp_path: Path) -> None:
    """`eval-pocket-3` kept its breakdown at call 6, read its lid on the bench at call 8, and
    went 49 calls more with no needs."""
    turns = [
        [BROKEN_DOWN],
        [tool("write", path="bracket.step", content="a first bracket")],
        BENCH,
        *wrote(8),
    ]
    run = begin(tmp_path, BRACKET, *turns)
    assert run.drive().name == "blocked"
    events = told(run)[:11]
    # Read at call 3; said at call 11, 8 calls on.
    assert [led(e) for e in events] == [False] * 10 + [True]
    assert events[10]["reply"].startswith("You're still at intake, 8 calls after `bracket.step`")


def test_a_given_file_read_on_the_bench_isnt_a_part_and_a_request_with_no_plan_isnt_asked_for_one(
    tmp_path: Path,
) -> None:
    given = begin(tmp_path / "a", BRACKET, [tool("bench", command="info references/fan.step")])
    assert given.drive().name == "blocked"
    assert not led(told(given)[0])
    turns = [[tool("write", path="bracket.step", content="a first bracket")], BENCH]
    lamp = begin(tmp_path / "b", LAMP, *turns)
    assert lamp.drive().name == "blocked"
    read = told(lamp)[1]
    assert led(read) and "settle `needs` now: they need only" in read["reply"], read["reply"]


def test_the_first_bench_check_on_the_models_own_part_is_found() -> None:
    tries = [
        nudge.Try(4, ["info", "references/fan.step"], 0),
        nudge.Try(6, ["info", "lid.step", "--nope"], nudge.USAGE),
        nudge.Try(8, ["clearance", "lid.step", "references/fan.step", "--slide", "-z"], 1),
        nudge.Try(9, ["thin", "lid.step"], 0),
    ]
    found = nudge.built(tries, ["lid.step", "render.png"])
    assert found is not None and (found[0].at_call, found[1]) == (8, "lid.step")
    assert nudge.built(tries[:2], ["lid.step"]) is None
    # Said at once with no breakdown; then 8 calls after it was said, or after the breakdown.
    assert nudge.intake_due(8, 8, 0, 0) and not nudge.intake_due(7, 8, 0, 0)
    assert not nudge.intake_due(15, 8, 8, 0) and nudge.intake_due(16, 8, 8, 0)
    assert not nudge.intake_due(20, 8, 16, 14) and nudge.intake_due(24, 8, 16, 14)
    assert not nudge.intake_due(15, 8, 0, 6) and nudge.intake_due(16, 8, 0, 6)
