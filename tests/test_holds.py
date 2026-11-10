"""A hold is read on the slide, never by eye, and a part that holds a bought part shows a first
build and its reading at the plan stop (mqucifer/print-shop#86, mqucifer/print-shop#87), as does a
part of the model's held by another (mqucifer/print-shop#90). A given part fixed in place is a fact
without a way in, and one fitted loose a fact held by nothing (mqucifer/print-shop#91). At intake,
a hold read `HELD` says what comes next (mqucifer/print-shop#92).

No Docker and no model: a scripted model drives each run, and a box answers each bench command
with the next verdict and runs nothing. The parts are made up for the test, a lid holding a fan, a
lid on a box, a lid on a base both printed, and a sleeve over a block; nothing here is from a run.
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
    parts = []
    if given:
        part = root / "given" / given
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_text(f"a {given}, made up")
        parts.append(part)
    run = Run.start(root / "runs", "r", request, parts, model, sleep=lambda _: None)
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
        "references/fan.step --slide -z`. If nothing is meant to hold it, as with a loose fit, "
        "say so with its `fact`, `loose: true` in place of `held_by`, and the plan goes as it "
        "is.\n"
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


# 5. A hold between two of the model's own parts (mqucifer/print-shop#90)

LID_REQUEST = """\
A lid and a base, both printed.

## Requirements
- The lid snaps onto the base and stays on until pulled off.
- The lid sits flush with the base.
"""
LID_BREAKDOWN = tool(
    "breakdown",
    parts=[
        {"name": "base", "what": "an open box"},
        {"name": "lid", "what": "a plate with two tabs"},
    ],
    joins=[{"parts": ["base", "lid"], "how": "two tabs on the lid catch under the base's rim"}],
    look=["flush"],
    **{"from": "the request"},
)
LID_SLIDE = "clearance base.step lid.step --slide +z"
STAYS_QUOTE = "The lid snaps onto the base and stays on until pulled off."
STAYS_BY_EYE = {
    "id": "stays",
    "what": "the lid stays on the base until pulled off",
    "source": "request",
    "quote": STAYS_QUOTE,
    "covers": ["R1"],
    "check": {
        "by": "eye",
        "look_for": "the tabs bend and the lid stays on",
        "no_bench": "no bench command measures it",
    },
}
STAYS_BY_SLIDE = {
    **STAYS_BY_EYE,
    "check": {"by": "bench", "measures": "fit", "command": LID_SLIDE, "verdicts": ["HELD"]},
}
FLUSH = {
    "id": "flush",
    "what": "the lid sits flush with the base",
    "source": "request",
    "quote": "The lid sits flush with the base.",
    "covers": ["R2"],
    "check": {"by": "eye", "look_for": "no step at the seam", "no_bench": "a look"},
}
FIRM = {
    "id": "firm",
    "what": "the pull that takes the lid off feels firm",
    "source": "chosen",
    "check": {"by": "eye", "look_for": "a firm pull, not a light tug, lifts the lid"},
}
GAP = {
    "id": "gap",
    "what": "the lid's walls clear the base's",
    "source": "chosen",
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance base.step lid.step --min-gap 0.1",
        "verdicts": ["CLEAR"],
    },
}
WAY_OFF = "<its way off: the way lid moves to come off base, one of +x -x +y -y +z -z>"


def test_an_eye_need_saying_a_lid_stays_on_its_base_is_refused_naming_the_slide(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        LID_REQUEST,
        "",
        [LID_BREAKDOWN],
        [tool("needs", needs=[STAYS_BY_EYE, FLUSH, FIRM, GAP])],
        [tool("needs", needs=[STAYS_BY_SLIDE, FLUSH, FIRM, GAP])],
    )
    assert run.drive().name == "blocked"
    _, refused, taken = told(run)[:3]
    assert (refused["outcome"], refused["why"]) == ("failed", "eye_on_hold")
    assert refused["reply"] == (
        "The needs weren't taken:\n"
        "- the need 'stays' asks the eye whether lid is held by base (\"stays on\"), and your "
        "breakdown joins them. A hold is read on the bench, never by eye: "
        f"`clearance base.step lid.step --slide {WAY_OFF}` slides lid off along its way off and "
        "says `HELD` with what it meets first, or `NOT HELD` and how far it moves. Send this need "
        'as a bench need, "check": {"by": "bench", "measures": "fit", "command": '
        f'"clearance base.step lid.step --slide {WAY_OFF}", "verdicts": ["HELD"]}}, with the '
        "same `source`, `quote` and `covers`, and its way off named. The held part goes last, as "
        "it's the one slid: swap the two if it's the other way round. If the eye is to judge "
        "something else of it too, such as how it looks or how firm a pull feels, keep an eye "
        "need for that alone, its `what` and `look_for` naming only that"
    ), "the flush look and the firm pull are the eye's: only the hold is refused"
    assert taken["outcome"] == "ok", taken["reply"]
    (kept,) = [n for n in run._needs() if n.id == "stays"]
    assert (kept.by, kept.command, kept.verdicts) == ("bench", LID_SLIDE, ["HELD"])


def test_which_of_two_printed_parts_is_held_comes_from_the_words() -> None:
    def joined(how: str) -> dict[str, Any]:
        return {"joins": [{"parts": ["base", "lid"], "how": how}]}

    lid_on_base = [holds.Held("lid", "", holds.PRINTED, "base")]
    assert holds.held([], joined("the lid snaps onto the base"), set()) == lid_on_base
    assert holds.held([], joined("the base holds the lid by its rim"), set()) == lid_on_base
    assert holds.held([], joined("tabs snap over a rim"), set()) == [], "naming neither"
    assert holds.held([], joined("the lid stays on"), set()) == lid_on_base, "naming one"
    assert holds.held([], joined("the base holds a shelf"), set()) == [], (
        "naming one as the holder, of something else"
    )
    assert holds.held([], joined("one body"), set()) == [], "a join that says no hold"
    said = ["the lid stays on the base"]
    assert holds.held([], joined("tabs under a rim"), set(), said) == lid_on_base
    fan = ["the fan is held under the lid"]
    assert holds.held([], joined("tabs under a rim"), {"references/fan.step"}, fan) == [], (
        "the words are about the fan, not the lid on its base"
    )
    bought = {"joins": [{"parts": ["lid", "fan"], "how": "held"}]}
    assert holds.held([], bought, {"references/fan.step"}) == [
        holds.Held("references/fan.step", "", "breakdown", "lid")
    ], "a join with a bought part is the bought part's"


def test_a_slide_of_two_printed_parts_reads_either_way_round() -> None:
    lid = holds.Held("lid", "", holds.PRINTED, "base")
    geometry = {"base.step": ["a", 3], "lid.step": ["b", 4]}

    def row(command: str) -> dict[str, Any]:
        return {"at_call": 5, "argv": command.split(), "exit": 0, "verdict": "HELD", "why": ""}

    for command in (LID_SLIDE, "clearance lid.step base.step --slide -z"):
        found, missing = holds.first_tries([lid], [row(command)], geometry)
        assert (len(found), missing) == (1, []), command
    found, missing = holds.first_tries([lid], [row("clearance lid.step --slide +z")], geometry)
    assert (found, len(missing)) == ([], 1)


def test_a_plan_with_the_lid_held_by_the_base_waits_for_its_slide(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        LID_REQUEST,
        "",
        [LID_BREAKDOWN],
        [tool("needs", needs=[STAYS_BY_SLIDE, FLUSH, FIRM])],
        [PLAN],
        [tool("write", path="base.step", content="a first base, made up")],
        [tool("write", path="lid.step", content="a first lid, made up")],
        [PLAN],
        [tool("bench", command=LID_SLIDE)],
        [PLAN],
        [tool("send_ask", says="The plan, the breakdown and the needs, as text.")],
        verdicts=("HELD",),
    )
    assert run.drive().name == "ask"
    _, _, nothing, _, _, unread, read, drawn, sent = told(run)
    assert (nothing["outcome"], nothing["why"]) == ("failed", "hold_unread")
    assert (
        "- lid: your breakdown joins it to base, and a need or the join says it's held. Missing: "
        "a part file for each of base and lid, and its reading. Build a rough first try with "
        "`write` and `run`, then read it with `bench clearance <base's file> <lid's file> "
        f"--slide {WAY_OFF}`. The held part goes last, as it's the one slid: swap the two if "
        "it's the other way round, and name its way off."
    ) in nothing["reply"]
    assert "loose" not in nothing["reply"], "a part of the model's has no fact to say so"
    assert unread["why"] == "hold_unread"
    assert f"Missing: its reading: `bench clearance base.step lid.step --slide {WAY_OFF}`" in (
        unread["reply"]
    )
    assert read["outcome"] == "ok"
    assert drawn["outcome"] == "ok", drawn["reply"]
    assert f"- `lid`: `{LID_SLIDE}`, call 7: HELD" in drawn["reply"]
    assert sent["outcome"] == "ok"
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert holds.AT_STOP in question


# 6. A bought part fitted loose is held by nothing (mqucifer/print-shop#91)

SLEEVE_REQUEST = """\
A sleeve over the block given.

## Requirements
- The sleeve slides over the block with play all round.
"""
SLEEVE_BREAKDOWN = tool(
    "breakdown",
    parts=[{"name": "sleeve", "what": "a tube"}, {"name": "block", "what": "the given block"}],
    joins=[{"parts": ["sleeve", "block"], "how": "the sleeve slides over the block"}],
    look=["a plain tube"],
    **{"from": "the request and references/block.step"},
)
BLOCK = {
    "name": "references/block.step",
    "text": "A block.",
    "from": "call 2, bench info references/block.step",
    "box": "x 0-20, y 0-20, z 0-40",
    "way_in": "+z",
}
PLAY = {
    "id": "play",
    "what": "the sleeve slides over the block with play",
    "source": "request",
    "quote": "The sleeve slides over the block with play all round.",
    "covers": ["R1"],
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance sleeve.step references/block.step --min-gap 0.2",
        "verdicts": ["CLEAR"],
    },
}
SLEEVE_PLAN = tool("ask", question="This way?", stop="plan", plan="A tube, 0.2 mm of play.")


def test_a_block_fitted_loose_lets_the_plan_go_with_no_slide(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        SLEEVE_REQUEST,
        "block.step",
        [SLEEVE_BREAKDOWN],
        [tool("fact", **BLOCK, loose=True)],
        [tool("needs", needs=[PLAY])],
        [SLEEVE_PLAN],
        [tool("send_ask", says="The plan as text.")],
    )
    assert run.drive().name == "ask", [e["reply"] for e in told(run)]
    assert [e["outcome"] for e in told(run)] == ["ok"] * 5
    (one,) = facts.load(run.state.facts)
    assert one.line() == (
        "- `references/block.step`: box x 0-20, y 0-20, z 0-40; goes in along +z; a loose fit, "
        "held by nothing. A block. (from call 2, bench info references/block.step, kept at call 2)"
    )
    question = (run.folder / "asks" / "1" / "question.md").read_text()
    assert holds.AT_STOP not in question


def test_a_block_held_by_the_sleeve_still_waits_and_names_the_loose_way_out(
    tmp_path: Path,
) -> None:
    run = started(
        tmp_path,
        SLEEVE_REQUEST,
        "block.step",
        [SLEEVE_BREAKDOWN],
        [tool("fact", **BLOCK, held_by="the sleeve")],
        [tool("needs", needs=[PLAY])],
        [SLEEVE_PLAN],
    )
    assert run.drive().name == "blocked"
    waits = told(run)[3]
    assert waits["why"] == "hold_unread"
    assert (
        "If nothing is meant to hold it, as with a loose fit, say so with its `fact`, `loose: "
        "true` in place of `held_by`, and the plan goes as it is."
    ) in waits["reply"]


def test_a_loose_fact_gives_no_held_by_and_isnt_fixed_too(tmp_path: Path) -> None:
    run = started(tmp_path, SLEEVE_REQUEST, "block.step")
    both = run.fact({**BLOCK, "loose": True, "held_by": "the sleeve"})
    assert both.reply == (
        "Not kept: `loose` says nothing holds it, and `held_by` says what does. Drop `held_by` "
        "for a loose fit, or drop `loose` if a part of yours holds it."
    )
    fixed = run.fact({**BLOCK, "way_in": "", "loose": True, "fixed": True})
    assert "give one of them" in fixed.reply
    wayless = run.fact({**BLOCK, "way_in": "", "loose": True})
    assert "`way_in` missing" in wayless.reply
    odd = run.fact({**BLOCK, "loose": "a bit"})
    assert odd.reply.startswith("Not kept: `loose` is true")
    unsaid = run.fact({**BLOCK})
    assert "give `loose: true` in place of `held_by`" in unsaid.reply
    assert run.fact({**BLOCK, "loose": True}).outcome == "ok"
    assert holds.held(facts.load(run.state.facts), None, run._references()) == []
    joined = {"joins": [{"parts": ["sleeve", "block"], "how": "over it"}]}
    assert holds.held(facts.load(run.state.facts), joined, run._references()) == [], (
        "nor does the breakdown's join make it held"
    )


# 7. A hold read HELD at intake says what comes next (mqucifer/print-shop#92)


def test_a_hold_read_held_at_intake_leads_each_reply_until_the_needs(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        FAN_REQUEST,
        "fan.step",
        [FAN_FACT],
        [tool("write", path="lid.step", content="a first lid, made up")],
        [tool("bench", command=SLIDE)],
        [tool("write", path="a.py", content="print('round the edges')")],
        [tool("run", path="a.py")],
        [FAN_BREAKDOWN],
        [tool("needs", needs=[BY_SLIDE, FLAT, LOOKS])],
        [tool("bench", command=SLIDE)],
        verdicts=("HELD", "HELD"),
    )
    assert run.drive().name == "blocked"
    _, _, read, wrote, ran, _, settled, after = told(run)[:8]
    line = (
        "The hold is read: `references/fan.step` reads `HELD`. Next come `breakdown`, `needs` "
        "and the plan stop: the plan needs only those and this first try with its reading. "
        "Finishing, such as edges and fillets, is round work after the plan is approved."
    )
    assert read["reply"].startswith(line + "\n\n")
    assert not wrote["reply"].startswith(line), "a write that runs nothing isn't led"
    assert ran["reply"].startswith(line + "\n\n"), "a later run at intake leads with it"
    assert settled["outcome"] == "ok"
    assert line not in after["reply"], "once the needs are settled it's gone"


def test_a_hold_read_not_held_at_intake_says_nothing_more(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        FAN_REQUEST,
        "fan.step",
        [FAN_FACT],
        [tool("write", path="lid.step", content="a first lid, made up")],
        [tool("bench", command=SLIDE)],
        verdicts=("NOT HELD",),
    )
    assert run.drive().name == "blocked"
    read = told(run)[2]
    assert "The hold is read" not in read["reply"]
