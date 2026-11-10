"""A failing check stays in front of the model until it passes, and a need is checked on the real
part (mqucifer/print-shop#45). No Docker: a scripted model drives each run, and a box answers each
bench command with the next answer and runs nothing. The parts are made up: a frame given as a
reference, a pane the model makes, and a helper file it shouldn't check a need on.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from print_shop.bench import picture as picture_module
from print_shop.bench import usage as usage_module
from print_shop.rounds import holding, needs, nudge, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.holding import Aside, Tried
from print_shop.rounds.needs import Need
from print_shop.rounds.nudge import Wanted
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_intake import PLAN_ASK, SENT, again, covered, pick, to_the_plan

HELD = holding.HEADING
REQUEST = """\
A pane for the frame given.

## Requirements
- The pane sits in the frame.

## Kind
stained-glass

## Stops
- no plan
"""
CAPTURE = "capture references/frame.step pane.step"


class Answers:
    """Each bench command answered with the next answer, `(verdict, numbers)` or a verdict, and
    OK once they run out, saved as the bench saves one. `render` draws a sheet; scripts run."""

    def __init__(self, work: Path, *answers: Any) -> None:
        self.work = work
        self.answers = list(answers)
        self.asked: list[list[str]] = []

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(args)
        n = len(self.asked)
        here = where or self.work
        words = [a for a in args if a != "--out"]
        record: dict[str, Any] = {}
        if "render" in words:
            out = args[args.index("--renders-out") + 1]
            (here / out).mkdir(parents=True, exist_ok=True)
            (here / out / "sheet.png").write_bytes(b"png")
            verdict, record["files"] = "OK", [f"{out}/sheet.png"]
        else:
            answer = self.answers.pop(0) if self.answers else "OK"
            verdict, numbers = answer if isinstance(answer, tuple) else (answer, {})
            record["numbers"] = numbers
        code = {"OK": 0, "HELD": 0, "FAIL": 1, "NOT HELD": 1}.get(verdict, 2)
        why = f"{verdict.lower()} on {' '.join(args)}"
        record.update(verdict=verdict, why=why)
        out_dir = here / "bench-out"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{n}.json").write_text(json.dumps(record))
        return Ran(code, f"verdict: {verdict}: {why}\nsaved: bench-out/{n}.txt and .json\n", 0.1)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "usage":
            return Ran(0, usage_module.MARK + json.dumps(["" for _ in args]), 0.1)
        if module == "picture":
            source, target = args
            (self.work / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.work / source, self.work / target)
            said = {"size": [8, 8], "shown": [8, 8], "out": target}
            return Ran(0, picture_module.MARK + json.dumps(said), 0.1)
        raise AssertionError(module)


def started(root: Path, *turns: Any, answers: tuple[Any, ...] = (), limits: Any = None) -> Run:
    model = Scripted(
        *[t if isinstance(t, list) else [t] for t in turns],
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(root, "r", REQUEST, [], model, limits, sleep=lambda _: None)
    (run.work / "references").mkdir(exist_ok=True)
    for name in ("references/frame.step", "pane.step", "pocket_cavity.step", "make.py"):
        (run.work / name).write_text("x")
    box: Any = Answers(run.work, *answers)
    run.box = box
    run.tools.box = box
    return run


def settled(run: Run, *found: Need) -> None:
    run.state.needs = needs.dump(list(found))
    run.state.needs_open = False
    run.state.needs_version = 1
    (run.folder / "needs").mkdir(exist_ok=True)
    (run.folder / "needs" / "1.json").write_text(json.dumps([n.plain() for n in found]))
    run.save()


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


# Item 1: a failed check stays named until it passes


def test_a_failed_check_no_need_runs_is_named_until_it_passes_at_the_settings_it_failed_at(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        tool("bench", command=f"{CAPTURE} --min-lip 0.4"),
        tool("bench", command="info pane.step"),
        tool("run", path="make.py"),
        tool("bench", command=f"{CAPTURE} --min-lip 0.1"),
        tool("bench", command=f"{CAPTURE} --min-lip 0.40"),
        tool("bench", command="info pane.step"),
        answers=("FAIL", "OK", "OK", "OK", "OK"),
    )
    assert run.drive().name == "blocked"
    told = replies(run)
    line = f"- `{CAPTURE} --min-lip 0.4`, call 1: FAIL: fail on {CAPTURE} --min-lip 0.4"
    for i in (0, 1, 2, 3):
        assert HELD in told[i]["reply"] and line in told[i]["reply"], i
    assert "A pass at other settings doesn't clear it" in told[1]["reply"]
    assert told[0]["detail"]["held"][0]["command"] == "capture"
    # A pass at 0.1 says nothing of 0.4; a pass at 0.4, however it's written, clears it.
    assert HELD in told[3]["reply"]
    assert HELD not in told[4]["reply"] and HELD not in told[5]["reply"]


def test_a_pass_at_the_first_settings_clears_probes_and_a_looser_pass_clears_nothing_else() -> None:
    def tried(n: int, lip: str, passed: bool) -> Tried:
        return Tried(n, [*CAPTURE.split(), "--min-lip", lip], passed, "OK" if passed else "FAIL")

    present = {"references/frame.step", "pane.step"}
    back = [tried(1, "0.4", False), tried(2, "0.1", False), tried(3, "0.4", True)]
    assert not holding.failing(back, [], present, None, None)
    sliding = [tried(1, "1.2", False), tried(2, "0.3", False), tried(3, "0.3", True)]
    kept = holding.failing(sliding, [], present, None, None)
    assert [t.at_call for t in kept[0].tries] == [1]


def test_a_check_set_aside_with_a_reason_goes_until_it_fails_again(tmp_path: Path) -> None:
    # Not `capture`: on the model's files, stained glass's own checks can't be set aside.
    wall = "wall pane.step references/frame.step --hole 1,2,3"
    run = started(
        tmp_path,
        tool("bench", command=wall),
        tool("set_aside", check="wall pane.step", why="Not that one."),
        tool("set_aside", check=f"bench {wall} --min-wall 2", why="I rebuilt the frame."),
        tool("bench", command="info pane.step"),
        tool("bench", command=wall),
        answers=("FAIL", "OK", "FAIL"),
    )
    assert run.drive().name == "blocked"
    told = replies(run)
    assert told[1]["why"] == "not_failing"
    assert "`wall pane.step references/frame.step`" in told[1]["reply"]
    assert told[2]["outcome"] == "ok"
    assert HELD not in told[3]["reply"]
    assert HELD in told[4]["reply"] and "call 5: FAIL" in told[4]["reply"]
    kept = [dict(r) for r in run.ledger.rows("SELECT * FROM set_asides")]
    assert kept[0]["command"] == "wall" and kept[0]["why"] == "I rebuilt the frame."
    assert json.loads(kept[0]["tries"]) == [1] and kept[0]["shown_at"] is None


def test_a_needs_check_is_held_until_it_passes_as_the_need_checks_it(tmp_path: Path) -> None:
    hold = Need(
        "hold",
        "the pane is held",
        "chosen",
        "bench",
        command=f"{CAPTURE} --min-lip 1.2",
        number="least_lip_mm",
        at_least=1.2,
        measures="pane-hold",
    )
    run = started(
        tmp_path,
        tool("bench", command=f"{CAPTURE} --min-lip 0.4"),
        tool("bench", command=f"{CAPTURE} --min-lip 0.4"),
        tool("bench", command=f"{CAPTURE} --min-lip 1.2"),
        tool("set_aside", check=CAPTURE, why="It reads the frame wrong."),
        tool("bench", command=f"{CAPTURE} --min-lip 1.2"),
        answers=(
            "FAIL",
            ("OK", {"least_lip_mm": 0.5}),
            ("OK", {"least_lip_mm": 0.9}),
            ("OK", {"least_lip_mm": 1.3}),
        ),
    )
    settled(run, hold)
    assert run.drive().name == "blocked"
    told = replies(run)
    assert f"`hold` runs it as `{CAPTURE} --min-lip 1.2`, which `done` checks." in told[0]["reply"]
    assert "call 1: FAIL" in told[1]["reply"], "a pass at other settings doesn't clear it"
    assert "call 3: OK: `least_lip_mm` is 0.9, under 1.2" in told[2]["reply"]
    assert told[3]["why"] == "a_needs_check"
    assert HELD not in told[4]["reply"]


def test_the_harness_reading_at_done_or_an_ask_counts_and_no_answer_leaves_it() -> None:
    wanted = [Wanted("hold", f"{CAPTURE} --min-lip 1.2", [*CAPTURE.split(), "--min-lip", "1.2"])]
    argv = [*CAPTURE.split(), "--min-lip", "1.2"]
    present = {"references/frame.step", "pane.step"}
    tries = [
        Tried(2, argv, False, "FAIL", "no lip", "done"),
        Tried(3, argv, None, "NO ANSWER"),
    ]
    found = holding.failing(tries, wanted, present, None, None)
    assert [t.by for t in found[0].tries] == ["done"]
    assert "by the harness at `done`" in holding.said(found)
    assert not holding.failing(
        [*tries, Tried(4, argv, True, by="ask")], wanted, present, None, None
    )


def test_a_check_leaves_when_its_files_go_or_a_later_done_leaves_them_out_of_the_part() -> None:
    tried = [Tried(1, ["info", "pocket_cavity.step"], False, "FAIL")]
    present = {"pocket_cavity.step", "pane.step"}
    assert holding.failing(tried, [], present, None, None)
    assert not holding.failing(tried, [], {"pane.step"}, None, None)
    assert not holding.failing(tried, [], present, ["pane.step"], 2)
    assert holding.failing(tried, [], present, ["pane.step"], 1), "a done before it says nothing"
    aside = Aside(2, ("info", ("pocket_cavity.step",)), "a helper")
    assert not holding.failing(tried, [], present, None, None, [aside])


def test_the_list_is_said_again_after_a_compaction(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("bench", command=f"{CAPTURE} --min-lip 0.4"),
        tool("bench", command="info pane.step"),
        answers=("FAIL", "OK"),
    )
    # Over the line once, before the second call, and under it once compacted.
    compacted: list[bool] = []
    real = run.session.compact

    def compact() -> None:
        real()
        compacted.append(True)

    run.session.compact = compact  # type: ignore[method-assign]
    run.session.estimate = lambda: 0 if compacted else 10**9  # type: ignore[method-assign]
    assert run.drive().name == "blocked"
    assert len(compacted) == 1
    body = transcript.rebuild(run.session.events, 2)
    last = body["messages"][-1]
    assert last["role"] == "user" and last["content"].startswith(holding.AFTER_COMPACTION)
    assert f"- `{CAPTURE} --min-lip 0.4`, call 1: FAIL" in last["content"]
    assert len(last["content"]) < 376 * 3, "under the 376 tokens the records allowed"


def test_nothing_is_said_after_a_compaction_when_nothing_fails(tmp_path: Path) -> None:
    run = started(tmp_path, tool("bench", command="info pane.step"), answers=("OK",))
    assert run.failing_said() == ""


# Item 2: a need is checked on files that will be printed, or the given references


def test_done_refuses_a_need_checked_on_a_file_neither_printed_nor_given(tmp_path: Path) -> None:
    fit = Need(
        "fit",
        "the pane fits",
        "chosen",
        "bench",
        command="clearance pocket_cavity.step pane.step",
        measures="fit",
    )
    run = started(
        tmp_path,
        tool("done", script="make.py", part=["pane.step"], fix_shape="first", closing="Here."),
    )
    settled(run, fit)
    assert run.drive().name == "blocked"
    refused = replies(run)[0]
    assert refused["why"] == "need_off_part"
    assert (
        "- `fit` reads `pocket_cavity.step`, in `clearance pocket_cavity.step pane.step`, which "
        "isn't in `part` or `references/`."
    ) in refused["reply"]
    assert not (run.folder / "rounds" / "001" / "judged").exists(), "nothing was checked"
    on_the_frame = Need("fit", "x", "chosen", "bench", command="panes references/frame.step")
    settled(run, on_the_frame)
    assert run._off_part(["pane.step"]) == []


# Items 1, 2 and 4 beside the question


def test_the_question_names_failing_checks_needs_off_the_part_refused_needs_and_asides(
    tmp_path: Path,
) -> None:
    fit = Need("fit", "fits", "chosen", "bench", command="clearance pocket_cavity.step pane.step")
    run = started(
        tmp_path,
        tool("bench", command=f"{CAPTURE} --min-lip 0.4"),
        tool("bench", command="info pocket_cavity.step"),
        tool("set_aside", check="info pocket_cavity.step", why="A helper, not printed."),
        tool(
            "needs",
            needs=[
                {
                    "id": "fit",
                    "what": "fits",
                    "source": "chosen",
                    "check": {
                        "by": "bench",
                        "command": "clearance references/frame.step pane.step",
                    },
                }
            ],
        ),
        tool(
            "ask",
            question="Does the pane read right?",
            variants=[{"name": "A", "files": ["pane.step"]}],
        ),
        tool("send_ask", says="One pane."),
        answers=("FAIL", "FAIL"),
    )
    settled(run, fit)
    assert run.drive().name == "ask"
    told = replies(run)
    assert "goes beside your next question" in told[3]["reply"]
    drawn = told[4]["reply"]
    assert "Checks whose latest try failed, which go beside the question too" in drawn
    assert "- `fit`: `pocket_cavity.step`, in `clearance pocket_cavity.step pane.step`" in drawn
    assert "refused `needs` call(s) since the last stop" in drawn
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## Checks whose latest try failed" in question
    assert f"- `{CAPTURE} --min-lip 0.4`, call 1: FAIL" in question
    assert "## Needs checked on a file that isn't printed or given" in question
    assert "## `needs` calls refused since the last stop" in question
    assert (
        "  - `fit` from `clearance pocket_cavity.step pane.step` to "
        "`clearance references/frame.step pane.step`"
    ) in question
    assert "## Failing checks the model set aside" in question
    assert (
        "- `info pocket_cavity.step`, failed at call 2, set aside at call 3: A helper" in question
    )
    assert run.state.needs_refused == []
    assert run.state.asides[0]["shown_at"] == "ask 1"


# Item 4: a "none" at the plan reopens the needs


def test_a_none_at_the_plan_reopens_the_needs_and_later_stops_dont(tmp_path: Path) -> None:
    run = to_the_plan(tmp_path, [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    # Nothing on the sheet and no part yet: each need on a file the model made is listed.
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## Needs checked on a file the model made" in question
    assert "- `holds`: `bracket.step`, in `info bracket.step`" in question
    pick(run, 1, "none")
    moved = covered(
        holes={
            "check": {"by": "bench", "measures": "wall", "command": "wall bracket.step --hole-x 12"}
        }
    )
    run = again(run, [tool("needs", needs=moved)], [PLAN_ASK], SENT)
    run.resume()
    assert run.drive().name == "ask"
    relayed = [e for e in transcript.events_of(run.session.events) if e.get("relay") == "pick"]
    assert "The plan shows the needs, so they're open again" in relayed[-1]["message"]
    assert "The fixed needs, `arm`, `plate`, stay as they are" in relayed[-1]["message"]
    assert replies(run)[-3]["outcome"] == "ok", "the moved need was taken"
    assert "--hole-x 12" in run.state.needs[1]["command"]
    pick(run, 2, "yes")
    run = again(
        run,
        [tool("ask", question="Is the profile right?", stop="S1", pictures=["profile.png"])],
        SENT,
    )
    run.resume()
    assert run.drive().name == "ask"
    pick(run, 3, "none")
    run = again(run, [tool("needs", needs=covered())])
    run.resume()
    run.drive()
    assert replies(run)[-2]["why"] == "needs_settled", "a none at a later stop opens nothing"
    assert run.state.needs_refused, "and the refusal waits for the next question"


def test_after_a_compaction_a_failing_thin_is_in_the_same_short_list() -> None:
    from print_shop.rounds.unprintable import Thin

    said = holding.after_compaction([], [Thin(4, "pane.step", "FAIL", "a spot 0.2 mm wide")])
    assert said.startswith(holding.AFTER_COMPACTION)
    assert "- `thin pane.step`, call 4: FAIL: a spot 0.2 mm wide" in said
    assert holding.after_compaction([], []) == ""


# A slide is its own check (mqucifer/print-shop#94)

FIT = "clearance pane.step references/frame.step"
SLIDE = f"{FIT} --slide -z"


def gap_need() -> Need:
    return Need(
        "gap",
        "the pane sits loose in the frame",
        "chosen",
        "bench",
        command=f"{FIT} --min-gap 0.4 --max-gap 0.8",
        measures="fit",
    )


def test_a_slide_and_its_way_are_part_of_a_checks_key() -> None:
    gap = nudge.check_of(f"{FIT} --min-gap 0.4 --max-gap 0.8".split())
    down = nudge.check_of(SLIDE.split())
    assert gap == ("clearance", ("pane.step", "references/frame.step"))
    assert down == ("clearance --slide -z", ("pane.step", "references/frame.step"))
    assert nudge.check_of(["bench", *FIT.split(), "--slide=-z", "--hold", "2"]) == down
    assert nudge.check_of([*FIT.split(), "--slide", "+y"]) != down
    assert nudge.check_of(FIT.split()) == gap, "a gap at any settings is one check"
    assert nudge.command_of(down) == "clearance"


def test_slides_two_ways_on_the_same_files_are_two_checks() -> None:
    present = {"references/frame.step", "pane.step"}
    tries = [
        Tried(1, SLIDE.split(), False, "NOT HELD"),
        Tried(2, [*FIT.split(), "--slide", "+y"], False, "NOT HELD"),
        Tried(3, SLIDE.split(), True, "HELD"),
    ]
    found = holding.failing(tries, [], present, None, None)
    assert [(h.check[0], [t.at_call for t in h.tries]) for h in found] == [
        ("clearance --slide +y", [2])
    ], "a pass on -z says nothing of +y"


def test_a_slide_no_need_runs_is_apart_from_a_gap_need_on_its_files_and_can_be_set_aside(
    tmp_path: Path,
) -> None:
    """On `eval-sleeve-3` the block slid out of a loose sleeve, `NOT HELD` as meant, and the
    harness held it under the gap need and refused `set_aside`."""
    run = started(
        tmp_path,
        tool("bench", command=SLIDE),
        tool("set_aside", check=SLIDE, why="Nothing holds the pane: it lifts out, as asked."),
        tool("bench", command="info pane.step"),
        answers=("NOT HELD",),
    )
    settled(run, gap_need())
    assert run.drive().name == "blocked"
    told = replies(run)
    assert HELD in told[0]["reply"] and f"- `{SLIDE}`, call 1: NOT HELD" in told[0]["reply"]
    assert "`gap` runs it" not in told[0]["reply"], "the slide isn't the gap need's check"
    assert "This is `gap`'s check" not in told[0]["reply"]
    assert told[0]["detail"]["held"] == [
        {
            "command": "clearance --slide -z",
            "files": ["pane.step", "references/frame.step"],
            "needs": [],
            "tries": [
                {
                    "at_call": 1,
                    "argv": SLIDE.split(),
                    "verdict": "NOT HELD",
                    "why": f"not held on {SLIDE}",
                    "by": "bench",
                }
            ],
        }
    ]
    assert told[1]["outcome"] == "ok", told[1]["reply"]
    assert HELD not in told[2]["reply"]
    kept = [dict(r) for r in run.ledger.rows("SELECT * FROM set_asides")]
    assert kept[0]["command"] == "clearance --slide -z"


def test_a_settled_slide_needs_own_failing_reading_cant_be_set_aside(tmp_path: Path) -> None:
    held = Need(
        "held",
        "the pane is held in the frame",
        "chosen",
        "bench",
        command=SLIDE,
        verdicts=["HELD"],
        measures="fit",
    )
    run = started(
        tmp_path,
        tool("bench", command=SLIDE),
        tool("set_aside", check=SLIDE, why="It's fine."),
        tool("bench", command=f"{FIT} --slide +y"),
        tool("set_aside", check=f"{FIT} --slide +y", why="It goes in from above."),
        answers=("NOT HELD", "NOT HELD"),
    )
    settled(run, held, gap_need())
    assert run.drive().name == "blocked"
    told = replies(run)
    assert f"`held` runs it as `{SLIDE}`, which `done` checks." in told[0]["reply"]
    assert told[1]["why"] == "a_needs_check" and "`held`" in told[1]["reply"]
    assert told[3]["outcome"] == "ok", "the other way is a check no need runs"
