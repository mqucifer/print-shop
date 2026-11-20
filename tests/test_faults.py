"""A stop's fix-first holds the fault, and its suggested way stays a suggestion
(mqucifer/print-shop#175). No Docker: a scripted model drives each run, and a box answers each
bench command and runs nothing. The run, its piece map and its picks are made up here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import overlap as overlap_module
from print_shop.rounds import faults, toolsets
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Refused, Run
from tests.rounds_fakes import Scripted, flat, tool
from tests.test_holding import Answers, replies, settled, started
from tests.test_needs_part import DONE, GLASS, THICK

CHECK = "pieces check map.json"
NOTCH = {
    "fault": "Piece 28 has a notch deeper than it's wide.",
    "at": "piece 28, by (41.5, 60)",
    "rule": f"`{CHECK}` flags `notch`",
    "suggest": "Cleave it from the notch's end to its far edge.",
}
SHADE = {
    "fault": "The hill's shade reads as lead, not glass",
    "at": "the patch at (30, 20)",
    "rule": "request line 3: the hills are glass",
    "suggest": "Pour glass into the patch.",
}
BY = "stand-in: a builder in a test"
WHO = "a stand-in for the Sponsor (a builder in a test)"
ASK = tool("ask", question="Right?", variants=[{"name": "A", "files": ["pane.step"]}])
SENT = tool("send_ask", says="One pane.")
PANE = {"part": ["pane.step"], "glass": GLASS, "on_bed": flat("pane.step"), **DONE}
MERGED = "Merged piece 28 into piece 27, which takes the notch away"


class Flagging(Answers):
    """As `Answers`, and the piece map's check fails its first `fails` readings."""

    def __init__(self, work: Path, fails: int = 0) -> None:
        super().__init__(work)
        self.fails = fails

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        if "pieces" in args and self.fails:
            self.fails -= 1
            self.answers.insert(0, "FAIL")
        return super().bench(args, where, timeout)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        ((where or self.work) / "pane.step").write_text("x")
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "overlap":
            said = {"files": {name: {} for name in args}, "pairs": [], "unread": {}}
            return Ran(0, overlap_module.MARK + json.dumps(said), 0.1)
        return super().helper(module, args, timeout, where)


def boxed(run: Run, fails: int = 0) -> Run:
    box: Any = Flagging(run.work, fails)
    run.box = run.tools.box = box
    return run


def asked(tmp_path: Path) -> Run:
    """A run in its first round, waiting at an ask for its pick."""
    run = started(tmp_path, [ASK], [SENT])
    settled(run, THICK)
    run.state.stage = "rounds"
    run._start_round()
    (run.work / "map.json").write_text("{}")
    run.save()
    assert boxed(run).drive().name == "ask"
    return run


def picked(run: Run, fix_first: Any, *turns: Any, n: int = 1, fails: int = 0) -> Run:
    """The pick written with its fix-first, and the run loaded to go on from it."""
    found = {"pick": "A", "reason": "It reads.", "by": BY, "fix_first": fix_first}
    (run.folder / "asks" / str(n) / "pick.json").write_text(json.dumps(found))
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    return boxed(Run.load(run.folder, model, sleep=lambda _: None), fails)


# A fault is a list entry: what, where and its rule, with a way suggested or not


def test_a_rule_names_a_bench_check_only_in_backticks_and_only_one_the_run_has() -> None:
    glass = toolsets.STAINED_GLASS
    assert faults.check_named(f"`{CHECK}` flags `notch`", glass) == CHECK
    assert faults.check_named("thin walls are under the nozzle's width", glass) == ""
    assert faults.check_named("pieces check map.json", glass) == "", "said in words, not named"
    assert faults.check_named("need `thick`, as settled", glass) == ""
    assert faults.check_named(f"`{CHECK}`", toolsets.CORE) == "", "not a command of this run"


def test_a_fault_with_no_rule_or_a_key_that_isnt_one_is_refused_at_the_pick(
    tmp_path: Path,
) -> None:
    run = asked(tmp_path)
    with pytest.raises(Refused, match="fault 1 has no rule"):
        picked(run, [{"fault": "A notch.", "at": "piece 28"}]).resume()
    with pytest.raises(Refused, match="fault 1 has how"):
        picked(run, [{**NOTCH, "how": "x"}]).resume()
    with pytest.raises(Refused, match="words, or a list of faults"):
        picked(run, {"fault": "A notch."}).resume()


# The relay keeps the two apart


def test_a_list_of_faults_is_relayed_as_to_fix_and_suggested_with_the_rules_line(
    tmp_path: Path,
) -> None:
    run = picked(asked(tmp_path), [NOTCH, SHADE])
    (told,) = run.resume()
    said = run.session.messages[-1]["content"]
    assert said.startswith(f"Apart from the look, {WHO} says to fix this first.\n\nTo fix:\n")
    to_fix, suggested = said.split("\n\nSuggested, yours to take or leave:\n")
    assert (
        f"- `F1.1`, at piece 28, by (41.5, 60): Piece 28 has a notch deeper than it's wide. Rule: "
        f"`{CHECK}` flags `notch`. `done` reads `{CHECK}` again, and this stands until it passes."
    ) in to_fix
    assert (
        "- `F1.2`, at the patch at (30, 20): The hill's shade reads as lead, not glass Rule: "
        "request line 3: the hills are glass. `done` waits for the way you took"
    ) in to_fix
    assert "Cleave" not in to_fix and "Pour" not in to_fix
    assert "- `F1.1`: Cleave it from the notch's end to its far edge." in suggested
    assert "- `F1.2`: Pour glass into the patch." in suggested
    assert (
        "Any fix that meets every rule will do. If a suggestion would break a rule, don't use "
        "it. Say which way you chose."
    ) in suggested
    # Whoever resumed is told how each is held.
    assert f"`F1.1` is read again at `done` by `{CHECK}`" in told
    assert "`F1.2` names no bench check of this run in backticks in its `rule`" in told
    # The ledger keeps the pick as written, and the intervention is the faults.
    kept = run.ledger.rows("SELECT fix_first FROM picks WHERE run=?", "r")[0]["fix_first"]
    assert json.loads(kept) == [NOTCH, SHADE]


def test_a_plain_string_is_relayed_as_a_fault_with_the_line_about_suggestions(
    tmp_path: Path,
) -> None:
    run = picked(asked(tmp_path), "Split pane 9.")
    assert run.resume() == [], "a fix-first in words says nothing more to whoever resumed"
    said = run.session.messages[-1]["content"]
    assert said.startswith(
        f"Apart from the look, {WHO} says to fix this first.\n\nTo fix:\n- `F1.1`: Split pane 9."
    )
    assert "Suggested" not in said
    assert faults.ANY_FIX in said
    assert run.ledger.rows("SELECT fix_first FROM picks WHERE run=?", "r")[0]["fix_first"] == (
        "Split pane 9."
    )
    assert run._faults()[0].command == "", "words name no check"


# A suggestion is said once, and kept nowhere


def test_after_a_compaction_the_note_quotes_the_faults_and_not_the_suggestions(
    tmp_path: Path,
) -> None:
    run = picked(asked(tmp_path), [NOTCH, SHADE])
    run.resume()
    note = run.compaction_note()
    assert "Faults named to fix first, still standing" in note
    assert "- `F1.1`, at piece 28, by (41.5, 60): Piece 28 has a notch deeper than" in note
    assert "- `F1.2`, at the patch at (30, 20): The hill's shade reads as lead" in note
    assert "Any fix that meets every rule will do." in note
    for kept in (
        note,
        json.dumps(run.state.faults),
        "\n".join(run.state.said),
        json.dumps(run.state.last_said),
        run._picks_said(),
    ):
        assert "Cleave" not in kept and "Pour glass" not in kept, kept
    assert "Piece 28 has a notch" in run._picks_said(), "a need may quote the fault"
    assert "The last fix-first" not in note, "said once, as the faults that stand"


# A fault is held, a suggestion never is


def test_done_waits_for_the_way_taken_and_a_fault_stands_until_its_check_passes(
    tmp_path: Path,
) -> None:
    """The fault's check fails at the first `done` and passes at the second, by a merge where
    a split was suggested: nothing reads the suggestion."""
    run = picked(
        asked(tmp_path),
        [NOTCH],
        [tool("done", **PANE)],
        [tool("done", ways={"F9.9": "x"}, **PANE)],
        [tool("done", ways={"F1.1": MERGED}, **PANE)],
        [tool("done", **PANE)],
        fails=1,
    )
    run.resume()
    assert run.drive().name == "met", "the fault's check passed, by another way than suggested"
    unsaid, unknown, standing, fixed = replies(run)[-4:]
    assert (unsaid["outcome"], unsaid["why"]) == ("failed", "fault_way_unsaid")
    assert "- `F1.1`, at piece 28, by (41.5, 60): Piece 28 has a notch" in unsaid["reply"]
    assert unknown["why"] == "ways_wrong"
    assert "`F9.9`, which isn't a fault named to fix first: `F1.1`" in unknown["reply"]
    # Refused, naming the fault: every need passed, and it isn't met.
    assert standing["detail"]["met"] is False
    assert standing["detail"]["faults_standing"] == ["F1.1"]
    assert "Every need passed, and a fault named to fix first still stands." in standing["reply"]
    assert f"STILL STANDS: `{CHECK}` still fails, at `done` in round 1" in standing["reply"]
    assert "it needn't be the way that was suggested" in standing["reply"]
    assert fixed["detail"]["round"] == 2
    assert fixed["detail"]["met"] is True
    assert f"fixed: `{CHECK}` passed at `done` in round 2: it's fixed" in fixed["reply"]
    read = json.loads((run.folder / "rounds" / "001" / "faults.json").read_text())
    assert (read[0]["id"], read[0]["read"]["passed"]) == ("F1.1", False)
    # The stop for approval shows the way taken beside the fault, and no suggestion.
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "## Faults named to fix first" in stop
    assert f"The model says it took this way: {MERGED}." in stop
    assert "A way was suggested with it, which the model was free to leave." in stop
    assert "Cleave" not in stop
    assert run._faults()[0].shown == "approval 1"
    assert run.compaction_note().count("F1.1") == 0, "a fixed fault isn't said again"


def test_a_fault_whose_check_couldnt_be_read_stands(tmp_path: Path) -> None:
    run = picked(asked(tmp_path), [NOTCH], [tool("done", ways={"F1.1": MERGED}, **PANE)])
    run.box.python = lambda *a, **k: Ran(1, "Traceback\n", 0.1)  # type: ignore[method-assign]
    run.resume()
    run.drive()
    done = replies(run)[-2]
    assert done["detail"]["faults_standing"] == ["F1.1"]
    assert "the script didn't finish from a clean start" in done["reply"]


# The next stop shows the way beside the fault, and a later fix-first takes its place


def test_the_next_question_shows_each_fault_its_reading_and_the_way_taken(
    tmp_path: Path,
) -> None:
    (tmp_path / "x").mkdir()
    again = tool("ask", question="Better?", variants=ASK["arguments"]["variants"])
    run = picked(
        asked(tmp_path / "x"),
        [NOTCH, SHADE],
        [tool("write", path="pane.step", content="changed")],
        [{**again, "arguments": {**again["arguments"], "ways": {"F1.2": "Traced the box again"}}}],
        [SENT],
        fails=1,
    )
    run.resume()
    assert run.drive().name == "ask"
    drawn = replies(run)[-2]
    assert "The faults named to fix first go beside the question" in drawn["reply"]
    question = (run.folder / "asks" / "2" / "question.md").read_text()
    assert "## Faults named to fix first" in question
    assert f"Now: `{CHECK}` still fails, at ask 2: FAIL: fail on" in question
    assert "The model hasn't said which way it took." in question
    assert "Now: its rule names no bench check, so a person reads it." in question
    assert "The model says it took this way: Traced the box again." in question
    assert "Cleave" not in question and "Pour glass" not in question
    assert 'each `{"fault": what must change, "at": where, "rule":' in question
    notch, shade = run._faults()
    assert notch.stands and not notch.shown, "a reading at an ask settles nothing"
    assert not shade.stands and shade.shown == "ask 2", "its way was shown to a person"
    # A later fix-first is the faults that stand from then on: an empty list drops them.
    run = picked(run, [], n=2)
    (told,) = run.resume()
    assert told == (
        f"This fix-first takes the place of the one before: `F1.1` no longer stands, by {WHO} "
        "(`F1.1` failed its check when last read)."
    )
    assert run.session.messages[-1]["content"] == told
    assert [one.id for one in run._faults()] == ["F1.2"]
    assert "F1.1" not in run.compaction_note()
