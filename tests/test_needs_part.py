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
#: The frame given, and the model's pane in it, for the harness's own `light` and `capture`.
GLASS = [
    {
        "frame": "references/frame.step",
        "panes": ["pane.step"],
        "pocket_z": 2,
        "clearance": 0.2,
        "pane_thickness": 1.5,
    }
]
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
#: A need on the pane that passes on the box's OK.
THICK = Need(
    "thick",
    "the pane is 1.5 mm thick",
    "chosen",
    "bench",
    command="thickness pane.step --min 1.5 --max 1.5",
    measures="pane-thickness",
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
    printable = Need("ok", "x", "chosen", "bench", command="thin pane.step", measures="printable")
    settled(run, FROM_THE_FRAME, printable)
    assert run._unread(["pane.step"]) == ["pane.step"], "`thin` reads every file anyway"
    one_body = Need("one", "x", "chosen", "bench", command="thin pane.step --one-body")
    settled(run, FROM_THE_FRAME, one_body)
    assert run._unread(["pane.step"]) == [], "one body is a check beyond `thin`'s"
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
        tool("done", part=["pane.step"], glass=GLASS, **DONE),
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


# Item 5: stained glass's own checks, `light` and `capture`, on every pane


LIGHT = "light references/frame.step pane.step"
HOLD = "capture references/frame.step --pocket-z 2 --clearance 0.2 --pane-thickness 1.5 --panes 1"


def test_a_stained_glass_done_names_its_frames_and_panes(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("done", part=["pane.step"], **DONE),
        tool("done", part=["pane.step"], glass=[{**GLASS[0], "panes": []}], **DONE),
        tool("done", part=["pane.step"], glass=[{**GLASS[0], "panes": ["frame.step"]}], **DONE),
        tool("done", part=["pane.step"], glass=[{**GLASS[0], "clearance": 0}], **DONE),
    )
    in_rounds(run, THICK)
    run.drive()
    told = replies(run)
    assert told[0]["why"] == "no_glass"
    assert "`done` names the frames and panes in `glass`" in told[0]["reply"]
    assert [t["why"] for t in told[1:4]] == ["not_glass"] * 3
    assert "isn't a file of `part` or a given reference" in told[2]["reply"]
    assert "`clearance` is a number in mm" in told[3]["reply"]
    assert not (run.folder / "rounds" / "001" / "judged").exists(), "nothing was checked"


def test_done_runs_light_and_capture_on_every_pane_and_isnt_met_while_one_fails(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        tool("done", part=["pane.step"], glass=GLASS, **DONE),
        tool("set_aside", check="capture references/frame.step", why="It's the given frame."),
        tool("bench", command="info pane.step"),
        answers=(
            "OK",  # the need
            "OK",  # `thin` on the pane
            ("OK", {"least_open_pct": 61.5, "least_open_file": "pane.step"}),
            "FAIL",  # `capture`
        ),
    )
    in_rounds(run, THICK)
    assert run.drive().name == "blocked"
    asked = [" ".join(a[2:]) for a in run.box.asked]  # type: ignore[attr-defined]
    assert asked[2:4] == [LIGHT, HOLD], "after the needs and `thin`, each in its own container"
    done, aside, later = replies(run)[:3]
    assert done["detail"]["met"] is False
    assert "Every need passed, and the harness's own checks" in done["reply"]
    assert "Every pane, by `light` and `capture`, whatever the needs say:" in done["reply"]
    assert f"- `{LIGHT}`, ok: OK: ok on" in done["reply"]
    assert f"- `{HOLD}`, FAIL: FAIL: fail on" in done["reply"]
    rows = run.ledger.rows(
        "SELECT check_name, stop, passed FROM standing WHERE run=? AND check_name != 'curves'",
        "r",
    )
    assert [(r["check_name"], r["stop"], r["passed"]) for r in rows][-2:] == [
        ("light", "done", 1),
        ("pane-hold", "done", 0),
    ]
    # It can't be set aside, on the model's files, and it's held until it passes.
    assert aside["why"] == "standing"
    assert "stained glass's own checks" in aside["reply"]
    assert (
        'On a given reference the piece doesn\'t print, as the "before" state, one can'
        in (aside["reply"])
    )
    assert f"`{HOLD}`, call 1, by the harness at `done`: FAIL" in later["reply"]


def test_light_fails_a_pane_with_nothing_open_whatever_its_verdict(tmp_path: Path) -> None:
    """With no limit given, `light` is a reading and says OK: the kind's rule is that light
    passes through every pane."""
    run = started(
        tmp_path,
        tool("done", part=["pane.step"], glass=GLASS, **DONE),
        answers=("OK", "OK", ("OK", {"least_open_pct": 0.0, "least_open_file": "pane.step"})),
    )
    in_rounds(run, THICK)
    run.drive()
    done = replies(run)[0]
    assert done["detail"]["met"] is False
    assert (
        f"- `{LIGHT}`, FAIL: no light passes through pane.step: nothing of it is open straight "
        "through the frame"
    ) in done["reply"]


def test_at_the_stop_for_approval_each_reading_is_shown(tmp_path: Path) -> None:
    run = started(tmp_path, tool("done", part=["pane.step"], glass=GLASS, **DONE))
    in_rounds(run, THICK)
    assert run.drive().name == "met"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "## Every pane, by `light` and `capture`, whatever the needs say:" in stop
    assert f"- `{HOLD}`, ok: OK" in stop
    assert run.state.glass == [{**GLASS[0], "with": []}]


def test_an_ask_runs_them_on_the_sheets_frame_and_panes(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool(
            "ask",
            question="Does it read right?",
            variants=[{"name": "A", "files": ["pane.step"]}],
            glass=GLASS,
        ),
        tool("send_ask", says="One pane."),
        answers=("OK", "OK", ("OK", {"least_open_pct": 50}), "FAIL"),
    )
    settled(run, THICK)
    assert run.drive().name == "ask"
    drawn = replies(run)[0]["reply"]
    assert f"- {HOLD}: FAIL: fail on" in drawn
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert f"- `{HOLD}`: FAIL: fail on" in question
    rows = run.ledger.rows("SELECT check_name, stop FROM standing WHERE run=?", "r")
    assert ("pane-hold", "ask") in [(r["check_name"], r["stop"]) for r in rows]


def test_a_failing_capture_on_a_given_reference_can_still_be_set_aside(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("bench", command="capture references/frame.step --pocket-z 2 --clearance 0.2"),
        tool("set_aside", check="capture references/frame.step", why="The before state."),
        answers=("FAIL",),
    )
    run.drive()
    assert replies(run)[1]["outcome"] == "ok"


def test_a_kind_with_no_standing_checks_takes_no_glass() -> None:
    from print_shop.rounds import toolsets
    from print_shop.rounds.tools import specs

    def done(toolset: toolsets.Toolset) -> dict[str, object]:
        spec = next(s for s in specs(toolset) if s["function"]["name"] == "done")
        found: dict[str, object] = spec["function"]["parameters"]["properties"]
        return found

    assert "glass" in done(toolsets.STAINED_GLASS)
    assert "glass" not in done(toolsets.CORE)


def test_a_need_that_runs_the_check_on_that_frame_is_its_reading_at_the_requests_figures(
    tmp_path: Path,
) -> None:
    """A rim the request sizes by share can be under the bench's 0.4 mm: where a need runs
    `capture` on the frame, its reading is the standing check's, and no second one runs."""
    rims = (
        "capture references/frame.step --pocket-z 2 --clearance 0.2 --pane-thickness 1.5 "
        "--full-rim 1.1 --least-rim 0.5 --min-visible 55 --panes 1"
    )
    held = Need("held", "held on both faces", "chosen", "bench", command=rims, measures="pane-hold")
    run = started(tmp_path, tool("done", part=["pane.step"], glass=GLASS, **DONE))
    in_rounds(run, THICK, held)
    assert run.drive().name == "met"
    asked = [" ".join(a[2:]) for a in run.box.asked]  # type: ignore[attr-defined]
    assert HOLD not in asked and asked.count(rims) == 1, "once, as the need"
    assert LIGHT in asked
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"- `{rims}`, ok: as the need `held` reads it: OK" in stop
