"""A hold is read on the slide, never by eye, and a part that holds a bought part shows a first
build and its reading at the plan stop (mqucifer/print-shop#86, mqucifer/print-shop#87). A given
part fixed in place is a fact without a way in.

No Docker and no model: a scripted model drives each run, and a box answers each bench command
with the next verdict and runs nothing. The parts are made up for the test, a lid holding a fan
and a lid on a box, each given as a reference; nothing here is from a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.bench import usage as usage_module
from print_shop.rounds import facts, holds, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool

FAN_REQUEST = """\
A lid for the fan given, holding it under the lid by clips of its own.

## Requirements
- The fan is held under the lid by clips it snaps into.
- The lid sits flat over the fan.
"""
BOX_REQUEST = """\
A lid for the box given, which is already printed.

## Requirements
- The lid sits on the box's top edge, held on by its lip's fit.
"""
SLIDE = "clearance lid.step references/fan.step --slide -z"
CODES = {"OK": 0, "CLEAR": 0, "HELD": 0, "NOT HELD": 1}


class Answers:
    """Each bench command answered with the next verdict, OK once they run out, saved as the
    bench saves one. Scripts run and make nothing."""

    def __init__(self, work: Path, *verdicts: str) -> None:
        self.work = work
        self.verdicts = list(verdicts)
        self.asked: list[list[str]] = []

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(args)
        n = len(self.asked)
        verdict = self.verdicts.pop(0) if self.verdicts else "OK"
        why = f"{verdict.lower()} on {' '.join(args)}"
        if verdict == "NOT HELD":
            why = "nothing meets the fan on its way out along -z: it leaves after 7.500 mm"
        record = {"verdict": verdict, "why": why, "numbers": {}}
        out_dir = (where or self.work) / "bench-out"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{n}.json").write_text(json.dumps(record))
        said = f"verdict: {verdict}: {why}\nsaved: bench-out/{n}.txt and .json\n"
        return Ran(CODES.get(verdict, 2), said, 0.1)

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "usage":
            return Ran(0, usage_module.MARK + json.dumps(["" for _ in args]), 0.1)
        raise AssertionError(module)


def started(
    root: Path, request: str, given: str, *turns: Any, verdicts: tuple[str, ...] = ()
) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    part = root / "given" / given
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_text(f"a {given}, made up")
    run = Run.start(root / "runs", "r", request, [part], model, sleep=lambda _: None)
    box: Any = Answers(run.work, *verdicts)
    run.box = box
    run.tools.box = box
    return run


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


FAN_BREAKDOWN = tool(
    "breakdown",
    parts=[
        {"name": "lid", "what": "a plate over the fan, with clips hanging from it"},
        {"name": "fan", "what": "the bought fan, given, unchanged"},
    ],
    joins=[{"parts": ["lid", "fan"], "how": "the lid's clips catch the fan's bottom face"}],
    look=["a plain plate over the fan"],
    **{"from": "the request and references/fan.step"},
)
FAN_FACT = tool(
    "fact",
    name="references/fan.step",
    text="A 30 mm fan.",
    box="x -15-15, y -15-15, z -7.5-0",
    way_in="+z",
    held_by="the lid's clips, once they're built",
    **{"from": "call 1, bench info references/fan.step"},
)
FLAT = {
    "id": "flat",
    "what": "the lid sits flat over the fan",
    "source": "request",
    "quote": "The lid sits flat over the fan.",
    "covers": ["R2"],
    "check": {"by": "bench", "measures": "size", "command": "info lid.step", "verdicts": ["OK"]},
}
HOLD_QUOTE = "The fan is held under the lid by clips it snaps into."
BY_EYE = {
    "id": "clips-hold",
    "what": "the clips hold the fan under the lid",
    "source": "request",
    "quote": HOLD_QUOTE,
    "covers": ["R1"],
    "check": {
        "by": "eye",
        "look_for": "the four clips reach under the fan's bottom face",
        "no_bench": "no bench command measures it: holding is the Sponsor's judgement",
    },
}
BY_SLIDE = {
    **BY_EYE,
    "check": {"by": "bench", "measures": "fit", "command": SLIDE, "verdicts": ["HELD"]},
}
LOOKS = {
    "id": "plain",
    "what": "the lid reads plain",
    "source": "chosen",
    "check": {"by": "eye", "look_for": "whether the lid reads as one plain plate"},
}
PLAN = tool("ask", question="Is this the way?", stop="plan", plan="Four clips, feet under it.")


# 1. A hold is a bench need on the slide, never an eye need (mqucifer/print-shop#86)


def test_an_eye_need_on_a_fans_hold_is_refused_with_the_slide_filled_in(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        FAN_REQUEST,
        "fan.step",
        [FAN_BREAKDOWN],
        [FAN_FACT],
        [tool("needs", needs=[BY_EYE, FLAT, LOOKS])],
        [tool("needs", needs=[BY_SLIDE, FLAT, LOOKS])],
    )
    assert run.drive().name == "blocked"
    _, _, refused, taken = told(run)[:4]
    assert (refused["outcome"], refused["why"]) == ("failed", "eye_on_hold")
    assert refused["reply"] == (
        "The needs weren't taken:\n"
        "- the need 'clips-hold' asks the eye whether `references/fan.step` is held (\"hold\"), "
        "and its fact says it goes in along +z: held by the lid's clips, once they're built. A "
        "hold is read on the bench, never by eye: "
        "`clearance lid.step references/fan.step --slide -z` slides it out along its way out and "
        "says `HELD` with what it meets first, or `NOT HELD` and how far it moves. Send this "
        'need as a bench need, "check": {"by": "bench", "measures": "fit", "command": '
        '"clearance lid.step references/fan.step --slide -z", "verdicts": ["HELD"]}, with the '
        "same `source`, `quote` and `covers`. If the eye is to judge something else of it too, "
        "such as how it looks, keep an eye need for that alone, its `what` and `look_for` naming "
        "only that"
    )
    assert taken["outcome"] == "ok", "a bench need naming HELD on that command is taken"
    (kept,) = [n for n in run._needs() if n.id == "clips-hold"]
    assert (kept.by, kept.command, kept.verdicts) == ("bench", SLIDE, ["HELD"])


def test_an_eye_need_on_a_look_is_kept_beside_a_held_part() -> None:
    fan = facts.Fact("references/fan.step", "", "c", 1, "x", "+z", "the lid")
    held = holds.held([fan], None, {"references/fan.step"})
    neat = Need("neat", "the clips look neat", "chosen", "eye", look_for="the fan's clips are neat")
    assert holds.eye_on_hold([neat], held, {}) == [], "a clip is a thing, not a hold"
    rattle = Need("r", "the fan stays in place", "chosen", "eye", look_for="it doesn't rattle")
    assert holds.eye_on_hold([rattle], held, {}) != []
    covering = Need("c", "the lid", "chosen", "eye", look_for="it looks right", covers=["R1"])
    assert holds.eye_on_hold([covering], held, {"R1": HOLD_QUOTE}) != [], "its line says held"


def test_what_holds_a_bought_part_comes_from_its_fact_or_the_breakdown() -> None:
    fan = facts.Fact("references/fan.step", "", "c", 1, "x", "+z", "the lid")
    base = facts.Fact("references/base.step", "", "c", 1, "x 0-60", fixed=True)
    joined = {"joins": [{"parts": ["lid", "base"], "how": "the lip fits inside its walls"}]}
    given = {"references/fan.step", "references/base.step"}
    assert holds.held([fan, base], joined, given) == [
        holds.Held("references/fan.step", "-z", "fact", "the lid")
    ], "a fixed part is held by nothing, whatever the breakdown joins it to"
    assert holds.held([], joined, given) == [
        holds.Held("references/base.step", "", "breakdown", "lid")
    ], "without a fact, the breakdown's join says a part holds it, and no way out is known"
    assert holds.held([], {"joins": [{"parts": ["lid", "arm"], "how": "one body"}]}, given) == []


# 2. A fixed given part is a fact without a way in (mqucifer/print-shop#86)


def test_a_fixed_base_is_kept_and_a_fan_without_a_way_in_is_still_refused(tmp_path: Path) -> None:
    run = started(tmp_path, BOX_REQUEST, "base.step")
    base = {
        "name": "references/base.step",
        "text": "An open box, already printed.",
        "from": "call 2, bench info references/base.step",
        "box": "x 0-60, y 0-40, z 0-25",
    }
    kept = run.fact({**base, "fixed": True})
    assert kept.outcome == "ok", kept.reply
    (one,) = facts.load(run.state.facts)
    assert one.line() == (
        "- `references/base.step`: box x 0-60, y 0-40, z 0-25; fixed in place, never fitted. An "
        "open box, already printed. (from call 2, bench info references/base.step, kept at call 0)"
    )
    assert one.plain()["fixed"] is True
    mixed = run.fact({**base, "fixed": True, "way_in": "-z"})
    assert mixed.reply == (
        "Not kept: `fixed` says it's in place and never fitted, and `way_in` say how it's "
        "fitted. Drop them for a part fixed in place, or drop `fixed` if a part of yours holds it."
    )
    boxless = run.fact({**base, "box": "", "fixed": True})
    assert "A fixed part's fact gives its `box`" in boxless.reply
    fan = run.fact({**base, "way_in": "", "held_by": "the lid"})
    assert fan.outcome == "failed"
    assert "`way_in` missing" in fan.reply
    assert "give `fixed: true` in place of `way_in` and `held_by`" in fan.reply
    loose = facts.Fact("references/fan.step", "", "c", 1, "x", "+z", "the lid")
    assert "fixed" not in loose.plain(), "a fitted bought part's shape is as it was"


# 3. A held part is built and read before its plan (mqucifer/print-shop#87)


def test_a_plan_on_a_part_holding_a_fan_waits_for_a_first_try_and_its_reading(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        FAN_REQUEST,
        "fan.step",
        [FAN_BREAKDOWN],
        [FAN_FACT],
        [tool("needs", needs=[BY_SLIDE, FLAT, LOOKS])],
        [PLAN],
        [tool("write", path="lid.step", content="a first lid, made up")],
        [PLAN],
        [tool("bench", command=SLIDE)],
        [PLAN],
        [tool("send_ask", says="The plan, the breakdown and the needs, as text.")],
        verdicts=("NOT HELD",),
    )
    assert run.drive().name == "ask"
    _, _, _, nothing, _, unread, read, drawn, sent = told(run)
    assert (nothing["outcome"], nothing["why"]) == ("failed", "hold_unread")
    assert nothing["reply"] == (
        "The plan waits for a first try at each part you hold and the bench's reading of it "
        "(mqucifer/print-shop#87): the bench reads a hold in seconds, but only on a part.\n"
        "- `references/fan.step`: its fact says it goes in along +z: held by the lid's clips, "
        "once they're built. Missing: a part file of yours, and its reading. Build a rough "
        "first try with `write` and `run`, then read it with `bench clearance <your part> "
        "references/fan.step --slide -z`.\n"
        "The first try doesn't have to hold: its reading goes beside the plan, whatever the "
        "verdict, and the plan says what you'd change. Then `ask` the plan again."
    )
    assert unread["why"] == "hold_unread"
    assert (
        "Missing: its reading. You have `lid.step`: read the hold with `bench clearance lid.step "
        "references/fan.step --slide -z`, naming the file that holds it."
    ) in unread["reply"]
    assert read["outcome"] == "ok"
    assert drawn["outcome"] == "ok", drawn["reply"]
    assert (
        "With it goes each held part's reading, whatever its verdict:\n"
        "- `references/fan.step`: `clearance lid.step references/fan.step --slide -z`, call 7: "
        "NOT HELD: nothing meets the fan on its way out along -z"
    ) in drawn["reply"]
    assert sent["outcome"] == "ok"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert holds.AT_STOP in question
    assert (
        "- `references/fan.step`: `clearance lid.step references/fan.step --slide -z`, call 7: "
        "NOT HELD: nothing meets the fan on its way out along -z: it leaves after 7.500 mm"
    ) in question
    asked = json.loads((run.folder / "asks" / "1" / "asked.json").read_text())
    assert asked["holds"][0]["verdict"] == "NOT HELD"


def test_a_reading_on_a_part_file_changed_since_is_read_again(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        FAN_REQUEST,
        "fan.step",
        [FAN_BREAKDOWN],
        [FAN_FACT],
        [tool("needs", needs=[BY_SLIDE, FLAT, LOOKS])],
        [tool("write", path="lid.step", content="a first lid, made up")],
        [tool("bench", command=SLIDE)],
        [tool("write", path="lid.step", content="a second lid, made up")],
        [PLAN],
        verdicts=("HELD",),
    )
    assert run.drive().name == "blocked"
    stale = told(run)[6]
    assert stale["why"] == "hold_unread"
    assert (
        "Its reading is on a part file that has changed since: `lid.step` (read at call 5). Read "
        "it again as it stands: `bench clearance lid.step references/fan.step --slide -z`."
    ) in stale["reply"]


def test_a_plain_lid_on_a_fixed_box_goes_to_its_plan_with_no_part_file(tmp_path: Path) -> None:
    breakdown = tool(
        "breakdown",
        parts=[
            {"name": "lid", "what": "a plate with a lip"},
            {"name": "base", "what": "the given box, already printed"},
        ],
        joins=[{"parts": ["lid", "base"], "how": "the lip fits inside the box's walls"}],
        look=["flush with the box"],
        **{"from": "the request and references/base.step"},
    )
    fixed = tool(
        "fact",
        name="references/base.step",
        text="An open box, already printed.",
        box="x 0-60, y 0-40, z 0-25",
        fixed=True,
        **{"from": "call 2, bench info references/base.step"},
    )
    sits = {
        "id": "sits",
        "what": "the lid sits on the box's top edge",
        "source": "request",
        "quote": "The lid sits on the box's top edge",
        "covers": ["R1"],
        "check": {"by": "eye", "look_for": "it sits flush", "no_bench": "friction isn't modelled"},
    }
    run = started(
        tmp_path,
        BOX_REQUEST,
        "base.step",
        [breakdown],
        [fixed],
        [tool("needs", needs=[sits])],
        [tool("ask", question="This way?", stop="plan", plan="A lip, 0.2 mm of play.")],
        [tool("send_ask", says="The plan as text.")],
    )
    assert run.drive().name == "ask", [e["reply"] for e in told(run)]
    assert [e["outcome"] for e in told(run)] == ["ok"] * 5
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert holds.AT_STOP not in question


def test_without_its_fact_a_joined_box_asks_for_the_fact_or_a_first_try(tmp_path: Path) -> None:
    breakdown = tool(
        "breakdown",
        parts=[{"name": "lid", "what": "a plate"}, {"name": "base", "what": "the given box"}],
        joins=[{"parts": ["lid", "base"], "how": "the lip fits inside the box's walls"}],
        look=["flush"],
        **{"from": "the request"},
    )
    sits = {
        "id": "sits",
        "what": "the lid sits on the box",
        "source": "chosen",
        "covers": ["R1"],
        "check": {"by": "eye", "look_for": "it sits flush", "no_bench": "friction"},
    }
    run = started(
        tmp_path,
        BOX_REQUEST,
        "base.step",
        [breakdown],
        [tool("needs", needs=[sits])],
        [tool("ask", question="This way?", stop="plan", plan="A lip.")],
    )
    assert run.drive().name == "blocked"
    waits = told(run)[2]
    assert waits["why"] == "hold_unread"
    assert "your breakdown joins it to lid" in waits["reply"]
    assert (
        "If it sits fixed in place and nothing you make holds it, say so with its `fact`, "
        "`fixed: true` in place of `way_in` and `held_by`, and the plan goes as it is."
    ) in waits["reply"]


# 4. What the start says


def test_the_start_says_to_build_and_read_a_held_part_before_its_plan(tmp_path: Path) -> None:
    run = started(tmp_path, FAN_REQUEST, "fan.step")
    start = str(run.session.messages[1]["content"])
    assert holds.START in start
    assert len(holds.START.splitlines()) == 2
    assert "clip" not in holds.START.lower(), "a method, never a clip's shape"


def test_a_fixed_box_given_a_way_in_is_told_how_to_say_its_fixed(tmp_path: Path) -> None:
    """A made-up box whose fact says it's fixed in `held_by`, as the old fact shape forced."""
    run = started(tmp_path, BOX_REQUEST, "base.step")
    base = {
        "name": "references/base.step",
        "text": "An open box.",
        "from": "call 2",
        "box": "x 0-60, y 0-40, z 0-25",
        "held_by": "already printed, cannot change",
    }
    odd = run.fact({**base, "way_in": "+Z (on its floor)"})
    assert odd.reply.endswith(
        "If it's fixed in place and never fitted, such as a box already printed that your part "
        "sits on, give `fixed: true` in place of `way_in` and `held_by`, with its `box`."
    )
    assert run.fact({**base, "way_in": "-z"}).outcome == "ok"
    (one,) = holds.held(facts.load(run.state.facts), None, run._references())
    gap = holds.Missing(one, [], [])
    assert (
        "Its `held_by` says it's fixed. If it sits fixed in place and nothing you make holds it, "
        "say so with its `fact`, `fixed: true` in place of `way_in` and `held_by`, and the plan "
        "goes as it is."
    ) in holds.plan_waits([gap])
    fan = holds.Held("references/fan.step", "-z", "fact", "nothing yet; the lid's clips will")
    assert "fixed" not in holds.plan_waits([holds.Missing(fan, [], [])])
