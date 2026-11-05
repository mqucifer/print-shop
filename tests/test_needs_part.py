"""A need checks the part (mqucifer/print-shop#52): every file of the part is read by a need, and
a need on a given reference the part replaces is refused. No Docker: a scripted model drives each
run, and a box answers each bench command and runs nothing. The parts are made up: a frame given
as a reference, the model's own copy of it, and a pane.
"""

from __future__ import annotations

import json
from pathlib import Path

from print_shop.bench import overlap as overlap_module
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests.rounds_fakes import tool
from tests.test_holding import replies, settled, started

DONE = {"script": "make.py", "fix_shape": "first", "closing": "Here."}
#: The pane's fit read from the frame alone: `panes` builds its own pane, and never opens the
#: model's `pane.step`.
FROM_THE_FRAME = Need(
    "fit",
    "the pane fills its pocket with its play",
    "chosen",
    "bench",
    command="panes references/frame.step --pocket-z 2 --clearance 0.2",
    measures="pane-fit",
)
ON_THE_PANE = Need(
    "fit",
    "the pane sits in its pocket",
    "chosen",
    "bench",
    command="clearance references/frame.step pane.step",
    verdicts=["CLEAR", "TOUCHING"],
    measures="fit",
)


# Item 1: every file of the part is read by a need


def test_done_names_each_part_file_no_need_reads_and_checks_nothing(tmp_path: Path) -> None:
    run = started(tmp_path, tool("done", part=["pane.step"], **DONE))
    settled(run, FROM_THE_FRAME)
    assert run.drive().name == "blocked"
    refused = replies(run)[0]
    assert refused["why"] == "part_unread"
    assert "no need's command reads these:\n- `pane.step`" in refused["reply"]
    assert "`set_aside`, giving `file` and `why`" in refused["reply"]
    assert not (run.folder / "rounds" / "001" / "judged").exists(), "nothing was checked"
    assert run._unread(["pane.step"]) == ["pane.step"]
    settled(run, ON_THE_PANE)
    assert run._unread(["pane.step"]) == [], "a need that names it reads it"


def in_rounds(run: Run, *found: Need, makes: tuple[str, ...] = ("pane.step",)) -> None:
    """Settled, and the first round begun, as `needs` leaves a run; the script, run clean, writes
    `makes`."""
    settled(run, *found)
    run.state.stage = "rounds"
    run._start_round()
    run.save()

    def python(script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        for name in makes:
            ((where or run.work) / name).write_text("x")
        return Ran(0, "ran\n", 0.1)

    given = run.box.helper

    def helper(module: str, args: list[str], timeout: int = 120, where: Path | None = None) -> Ran:
        if module == "overlap":
            said = {"files": {name: {} for name in args}, "pairs": [], "unread": {}}
            return Ran(0, overlap_module.MARK + json.dumps(said), 0.1)
        return given(module, args, timeout, where)

    run.box.python = python  # type: ignore[method-assign]
    run.box.helper = helper  # type: ignore[method-assign]


def test_a_part_file_set_aside_with_its_reason_lets_done_on_and_is_shown_at_the_next_stop(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        tool("done", part=["pane.step"], **DONE),
        tool("set_aside", file="frame.step", why="Not mine."),
        tool("set_aside", file="pane.step", why=""),
        tool(
            "set_aside", file="pane.step", why="Cut from the frame's own pocket, which `fit` reads."
        ),
        tool("done", part=["pane.step"], **DONE),
    )
    in_rounds(run, FROM_THE_FRAME)
    assert run.drive().name == "met", "set aside, it goes on to the checks, and passes"
    told = replies(run)
    assert told[0]["why"] == "part_unread"
    assert told[1]["why"] == "not_in_part"
    assert told[2]["why"] == "empty"
    assert told[3]["outcome"] == "ok"
    assert "`done` no longer asks for a need to read `pane.step`" in told[3]["reply"]
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "## Part files no need reads, set aside by the model" in stop
    assert "- `pane.step`, set aside at call 4: Cut from the frame's own pocket" in stop
    row = run.ledger.rows("SELECT command, unread_file, why FROM set_asides WHERE run=?", "r")[0]
    assert (row["command"], row["unread_file"]) == ("", "pane.step")
    assert run.state.asides[0]["shown_at"] == "approval 1"


def test_the_question_names_part_files_no_need_reads(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool(
            "ask", question="Does it read right?", variants=[{"name": "A", "files": ["pane.step"]}]
        ),
        tool("send_ask", says="One pane."),
    )
    settled(run, FROM_THE_FRAME)
    assert run.drive().name == "ask"
    assert "- no need reads `pane.step`" in replies(run)[0]["reply"]
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## Files no need reads\n" in question
    assert "- `pane.step`\n" in question


# Item 2: a need on a reference the part replaces


def test_done_refuses_a_need_on_a_reference_the_part_replaces(tmp_path: Path) -> None:
    run = started(tmp_path, tool("done", part=["frame.step", "pane.step"], **DONE))
    (run.work / "frame.step").write_text("x")
    settled(run, ON_THE_PANE)
    assert run.drive().name == "blocked"
    refused = replies(run)[0]
    assert refused["why"] == "need_on_replaced"
    assert (
        "- `fit` reads `references/frame.step`, in `clearance references/frame.step pane.step`, "
        "and the part has its own `frame.step`."
    ) in refused["reply"]
    assert 'as the "before" state, can still run on the bench' in refused["reply"]
    assert not (run.folder / "rounds" / "001" / "judged").exists(), "nothing was checked"
    # Where the part doesn't replace it, the given frame is checked as it was.
    assert run._on_replaced(["pane.step"]) == []


def test_the_question_names_a_need_on_a_reference_the_part_replaces(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool(
            "ask",
            question="Does it read right?",
            variants=[{"name": "A", "files": ["frame.step", "pane.step"]}],
        ),
        tool("send_ask", says="The frame and its pane."),
    )
    (run.work / "frame.step").write_text("x")
    settled(run, ON_THE_PANE)
    assert run.drive().name == "ask"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## Needs checked on a reference the part replaces" in question
    assert (
        "- `fit`: `references/frame.step`, in `clearance references/frame.step pane.step`, where "
        "the part has `frame.step`"
    ) in question
    assert "- `frame.step`\n" in question, "and no need reads the model's frame"
