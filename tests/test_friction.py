"""The friction the runs wrote down as lessons, taken away (mqucifer/print-shop#54), with no Docker:
a wrong argument names the tool that takes it, a refused join says what a join is, `questions`
carries pictures, `looks` may name a given file, and a need may quote a pick's reason. Every
request here is made up for the test, and a bench command is answered by a box that runs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import breakdown, needs, tools, toolsets, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_intake import (
    FREE,
    PLAN_ASK,
    SENT,
    Box,
    again,
    begin,
    covered,
    pick,
    picture,
    to_the_plan,
    told,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"a sheet"


class Drawing(Box):
    """As `Box`, and `render` leaves a sheet where it says it does, as the bench records one."""

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        ran = super().bench(args, where, timeout)
        if args[0] == "render":
            out = args[args.index("--renders-out") + 1]
            (self.work / out).mkdir(parents=True, exist_ok=True)
            (self.work / out / "given-sheet.png").write_bytes(PNG)
            record = (where or self.work) / "bench-out" / f"{len(self.asked)}.json"
            said = json.loads(record.read_text())
            record.write_text(json.dumps({**said, "files": [f"{out}/given-sheet.png"]}))
        return ran


def given(root: Path, *turns: Any, files: tuple[str, ...] = ("frame.step",)) -> Run:
    """A run of the free-form request, given made-up files, with a box that draws."""
    refs = []
    for name in files:
        (root / name).write_text("ISO-10303-21; made up for the test\n")
        refs.append(root / name)
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    run = Run.start(root / "runs", "r", FREE, refs, model, sleep=lambda _: None)
    box: Any = Drawing(run.work)
    run.box = box
    run.tools.box = box
    return run


def commands(run: Run) -> list[list[str]]:
    """The bench commands the fake box was asked for."""
    box: Any = run.box
    return list(box.asked)


# A refusal names the right tool


def test_an_argument_another_tool_takes_is_named_with_that_tool(tmp_path: Path) -> None:
    """On the runs, `says` went to `ask`: it's `send_ask`'s."""
    with_says = tool(
        "ask", question="Plan?", stop="plan", plan="As said.", says="it shows the profile"
    )
    run = to_the_plan(tmp_path, [with_says], [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    wrong = told(run)[2]
    assert wrong["why"] == "unknown"
    assert "`ask` wasn't run: it doesn't take `says`." in wrong["reply"]
    said = "`says` is `send_ask`'s, which sends the sheet once `ask` has drawn it."
    assert said in wrong["reply"]


def test_an_argument_no_tool_takes_is_refused_as_before(tmp_path: Path) -> None:
    run = begin(tmp_path, FREE, [tool("questions", questions="Which?", colour="red")])
    run.drive()
    wrong = told(run)[0]
    assert wrong["why"] == "unknown"
    assert wrong["reply"].endswith("It takes `questions`, `pictures`."), "no other tool takes it"


def test_a_join_refused_says_what_a_join_is_with_an_example() -> None:
    _, wrong = breakdown.read(
        {
            "parts": [{"name": "frame", "what": "leading"}, {"name": "pane_1", "what": "glass"}],
            "joins": [{"parts": ["frame"], "how": "the pane sits in its pocket"}],
            "look": ["light through the panes"],
        }
    )
    said = wrong[0]
    assert said.startswith("a join names the two or more parts it holds together, in `parts`")
    assert "this one names 1" in said
    assert '{"parts": ["frame", "pane_1"], "how": ' in said
    assert "with one part there's no join" in said
    _, wrong = breakdown.read(
        {
            "parts": [{"name": "frame (leading)", "what": "x"}, {"name": "panes", "what": "y"}],
            "joins": [{"parts": ["frame", "panes"], "how": "held by its lips"}],
            "look": ["a"],
        }
    )
    assert wrong[0] == (
        "a join names `frame`, which isn't one of `parts`: a join names each part exactly as "
        "`parts` does (`frame (leading)`, `panes`)"
    )


# Pictures at `questions`


def test_questions_carry_the_models_pictures(tmp_path: Path) -> None:
    run = begin(tmp_path, FREE, [tool("questions", questions="How wide?", pictures=["cell.png"])])
    picture(run, "cell.png")
    assert run.drive().name == "questions"
    asked = told(run)[0]
    assert asked["outcome"] == "ok" and asked["detail"] == {"pictures": 1}
    assert "Your pictures went with them, as they are." in asked["reply"]
    kept = run.folder / "questions" / "1" / "cell.png"
    assert kept.read_bytes() == (run.work / "cell.png").read_bytes()
    assert f"- {kept}" in (run.folder / "questions" / "1.md").read_text()
    assert commands(run) == [], "no sheet is drawn where the model gave pictures"


def test_questions_with_no_picture_carry_a_sheet_of_the_given_files(tmp_path: Path) -> None:
    """On `eval-choice-1` a question about a cell's size went with no picture."""
    run = given(tmp_path, [tool("questions", questions="How wide should the small cell be?")])
    assert run.drive().name == "questions"
    drew = commands(run)[0]
    assert drew[:3] == ["render", "--variant", "given=references/frame.step"]
    sheet = run.folder / "questions" / "1" / "given-sheet.png"
    assert sheet.read_bytes() == PNG
    text = (run.folder / "questions" / "1.md").read_text()
    assert text.startswith("How wide should the small cell be?\n\n## Pictures\n")
    asked = told(run)[0]
    assert "the harness's sheet of the given files, above: `references/frame.step`" in (
        asked["reply"]
    )
    shown = [e for e in transcript.events_of(run.session.events) if e["kind"] == "request"]
    assert shown, "the sheet went to the model too"


def test_a_picture_that_isnt_one_is_refused_and_nothing_stops(tmp_path: Path) -> None:
    run = begin(tmp_path, FREE, [tool("questions", questions="How?", pictures=["make.py"])])
    assert run.drive().name == "blocked"
    assert told(run)[0]["why"] == "not_a_picture"
    assert run.state.questions == 0


# `looks` on a given file


def test_looks_may_name_a_given_file_which_the_sheet_draws_only_if_its_on_it(
    tmp_path: Path,
) -> None:
    """On `eval-through-1` a plan's `looks` naming the given frame was refused."""
    looks = {"references/frame.step": {"colour": "grey", "label": "frame"}}
    run = given(
        tmp_path,
        [tool("ask", question="Like this?", plan="As drawn.", pictures=["p.png"], looks=looks)],
        [tool("ask", question="Like this?", pictures=["p.png"], looks={"elsewhere.step": {}})],
        [tool("blocked", reason="done")],
    )
    picture(run, "p.png")
    run.drive()
    drawn, wrong = told(run)[:2]
    assert drawn["outcome"] == "ok", drawn["reply"]
    assert "`looks` names `references/frame.step`, given but not on this sheet" in drawn["reply"]
    assert wrong["why"] == "not_a_look"
    assert "or a given file under `references/`" in wrong["reply"]


# A need may quote a pick's reason


def test_a_need_may_quote_a_picks_reason_with_its_source_naming_it() -> None:
    need = {
        "id": "light",
        "what": "each pane lets light through",
        "source": "pick",
        "quote": "keep at least 75% of each pane open",
        "check": {"by": "eye", "look_for": "light"},
    }
    reason = "B, and keep at least 75% of each pane open."
    found, wrong = needs.read([need], "the request", picks=reason)
    assert wrong == [] and found[0].source == "pick"
    _, wrong = needs.read([{**need, "source": "note"}], "the request", picks=reason)
    assert wrong == [
        "the need 'light' quotes \"keep at least 75% of each pane open\", which isn't in a note "
        "you were sent, but is in a pick's reason: its `source` is `pick`"
    ]
    _, wrong = needs.read([need], "the request", "keep at least 75% of each pane open")
    assert wrong[0].endswith(
        "which isn't in a pick's reason you were sent, but is in a note: its `source` is `note`"
    )


def test_a_run_holds_a_picks_quote_to_what_was_picked(tmp_path: Path) -> None:
    """On `eval-unnamed-1`, a need quoting the stand-in's pick was refused."""
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    pick(run, 1, "none", by="stand-in: a builder in a test")
    quoted = covered(
        holds={"source": "pick", "quote": "As I'd hoped", "check": {"by": "eye", "look_for": "x"}}
    )
    run = again(run, [tool("needs", needs=quoted, **{"try": True})])
    run.resume()
    run.drive()
    tried = told(run)[-2]
    assert tried["outcome"] == "ok", tried["reply"]


def test_the_needs_tool_names_the_pick_as_a_source() -> None:
    # Its sources are in the needs' schema since mqucifer/print-shop#93, beside the enum.
    source = tools.need_item(toolsets.CORE)["properties"]["source"]
    assert "`note` or `pick`, a pick's reason" in source["description"]
    assert "pick" in source["enum"]
