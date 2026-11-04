"""A part file whose last `thin` failed is named in every later `bench` and `run` reply, until it
passes or leaves the part, and in the question `ask` sends (mqucifer/print-shop#37, lesson 21).
No Docker: a box answers each bench command with the next verdict and runs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import transcript, unprintable
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from print_shop.rounds.unprintable import Thin
from tests.rounds_fakes import Scripted, tool

SAID = "Files of the part whose last `thin` failed:"


class Verdicts:
    """Each bench command answered with the next verdict, saved as the bench saves one; each
    script run answered with exit 0."""

    def __init__(self, work: Path, *verdicts: str) -> None:
        self.work = work
        self.verdicts = list(verdicts)
        self.n = 0

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.n += 1
        verdict = self.verdicts.pop(0)
        code = {"OK": 0, "FAIL": 1}.get(verdict, 2)
        why = f"{verdict.lower()} on {' '.join(args)}"
        out = self.work / "bench-out"
        out.mkdir(exist_ok=True)
        (out / f"{self.n}.json").write_text(json.dumps({"verdict": verdict, "why": why}))
        said = f"verdict: {verdict}: {why}\nsaved: bench-out/{self.n}.txt and .json\n"
        return Ran(code, said, 0.1)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        return Ran(0, "ran\n", 0.1)


def driven(root: Path, *calls: dict[str, Any], verdicts: tuple[str, ...]) -> list[dict[str, Any]]:
    model = Scripted(*[[c] for c in calls], [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", "A frame.", [], model, sleep=lambda _: None)
    for name in ("pane_a.step", "pane_b.step", "frame.step", "make.py"):
        (run.work / name).write_text("x")
    box: Any = Verdicts(run.work, *verdicts)
    run.tools.box = box
    assert run.drive().name == "blocked"
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def test_a_failing_thin_is_said_in_every_later_bench_and_run_reply_until_it_passes(
    tmp_path: Path,
) -> None:
    told = driven(
        tmp_path,
        tool("bench", command="thin pane_a.step"),
        tool("bench", command="info frame.step"),
        tool("run", path="make.py"),
        tool("bench", command="thin pane_a.step --nozzle 0.2"),
        tool("bench", command="thin pane_a.step"),
        tool("bench", command="thin pane_a.step --one-body"),
        tool("bench", command="info frame.step"),
        verdicts=("FAIL", "OK", "OK", "DISAGREE", "OK", "OK"),
    )
    for i in (0, 1, 2, 3, 4):
        assert SAID in told[i]["reply"], i
        assert "- `pane_a.step`, call 1: FAIL: fail on thin pane_a.step" in told[i]["reply"], i
    assert "run `thin` on it again before you go on to another file" in told[1]["reply"]
    assert told[0]["detail"]["thin_failing"] == ["pane_a.step"]
    # A thinner nozzle is another check, no answer leaves it, and `--one-body` is the same check,
    # stricter: its pass clears the file.
    assert SAID not in told[5]["reply"] and SAID not in told[6]["reply"]


def test_a_file_that_leaves_the_part_isnt_said(tmp_path: Path) -> None:
    readings = [Thin(1, "pane_a.step", "FAIL"), Thin(2, "pane_b.step", "FAIL", by="done")]
    both = {"pane_a.step", "pane_b.step"}
    assert [t.file for t in unprintable.failing(readings, both, None, None)] == [
        "pane_a.step",
        "pane_b.step",
    ]
    assert unprintable.failing(readings, {"pane_b.step"}, None, None)[0].file == "pane_b.step"
    # A `done` after both named only pane_b as the part; one before them says nothing.
    assert [t.file for t in unprintable.failing(readings, both, ["pane_b.step"], 3)] == [
        "pane_b.step"
    ]
    assert len(unprintable.failing(readings, both, ["pane_b.step"], 1)) == 2
    assert "by the harness at `done`" in unprintable.said(readings[1:])
    # No answer to rely on leaves it as it was.
    later = [*readings, Thin(4, "pane_a.step", "NO ANSWER")]
    assert unprintable.failing(later, both, None, None)[0].file == "pane_a.step"


def test_only_a_thin_at_the_harnesss_settings_counts() -> None:
    assert unprintable.counted(["thin", "a.step"]) == "a.step"
    assert unprintable.counted(["bench", "thin", "--one-body", "a.step"]) == "a.step"
    assert unprintable.counted(["thin", "a.step", "--nozzle", "0.40"]) == "a.step"
    assert unprintable.counted(["thin", "a.step", "--nozzle", "0.6"]) is None
    assert unprintable.counted(["thin", "a.step", "--min-body=5"]) is None
    assert unprintable.counted(["info", "a.step"]) is None


def test_the_question_ask_sends_says_which_files_fail(tmp_path: Path) -> None:
    model = Scripted([tool("send_ask", says="One pane, blue, and the frame in grey.")])
    run = Run.start(tmp_path, "r", "A frame.", [], model, sleep=lambda _: None)
    (run.work / "pane_a.step").write_text("x")
    (run.work / ".pictures").mkdir()
    (run.work / ".pictures" / "sheet.png").write_bytes(b"png")
    run.state.draft = {
        "question": "Does it read right?",
        "need": None,
        "variants": [{"name": "A", "files": ["pane_a.step"], "about": "one pane"}],
        "test_pieces": [],
        "looks": {},
        "round": 0,
        "seen": {"variants": {"A": run._held(["pane_a.step"])}, "test_pieces": {}, "sheet": "x"},
        "sheet": ".pictures/sheet.png",
        "bench_said": [],
        "standing": [],
        "thin_failing": [{"file": "pane_a.step", "at_call": 4, "why": "a spot 0.2 mm wide"}],
        "at_call": 5,
    }
    assert run.drive().name == "ask"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert "## Part files whose last `thin` failed" in question
    assert "- `pane_a.step`, at call 4: a spot 0.2 mm wide" in question
