"""One print loop with tools decides a part's pose and settings, and asks only before a big change.

mqucifer/print-shop#129. After the intake's questions, one conversation with `slice`,
`orca_setting`, `past_prints`, `ask` and `propose` decides the pose and the settings, in place of
the orientation call and the settings call. Code decides what's big; the review and the approval
gate are unchanged; every tool call is kept with the job; past its limit, the job falls back to
the single calls and says so. With a review the loop proposes and the person takes
(mqucifer/print-shop#242), as `takes` does here; with none to take them, it asks first as it
did. Every model here is a scripted fake, and every slice the cube's
recording: nothing reaches LiteLLM, Orca, the printer or the broker.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.state import Orientation
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import candidates
from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.features import measure
from print_shop.setup.intake import Intake, replay_asker
from print_shop.setup.loop import BIG_TIME_SHARE, Part, PrintLoop, big
from print_shop.setup.proposal import GivenLesson, PartFacts, Source
from print_shop.setup.review import ReviewRequest, render
from print_shop.setup.skill import GROUPS, load
from tests.fakes import STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.reviewing import takes
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_review import ANSWERS, _model, _questions
from tests.test_send_back import AnyPart

#: A tool call, as the script writes it: the tool and its arguments.
Step = tuple[str, dict[str, Any]]
#: A turn of the scripted model: its tool calls, or a function of the last result giving them.
Turn = Sequence[Step] | Callable[[str], Sequence[Step]]


class Model:
    """The print loop's model, from a script: each call takes the next turn, or `forever`'s.
    Keeps what it was shown."""

    def __init__(self, *turns: Turn, forever: Turn | None = None) -> None:
        self.turns = list(turns)
        self.forever = forever
        self.shown: list[list[dict[str, Any]]] = []

    def __call__(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        self.shown.append(list(messages))
        turn = self.turns.pop(0) if self.turns else self.forever
        assert turn is not None, "the script ran out"
        last = next((m["content"] for m in reversed(messages) if m["role"] == "tool"), "")
        steps = turn(last) if callable(turn) else turn
        return {"content": "", "tool_calls": [
            {"id": f"c{len(self.shown)}-{n}", "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
            for n, (name, args) in enumerate(steps)]}


class Person:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []

    def __call__(self, question: str) -> str:
        self.asked.append(question)
        return self.answers.pop(0)


def _results(proposal: Any) -> list[tuple[str, str]]:
    return [(c.tool, c.result.split("\n")[0]) for c in proposal.loop]


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Any]:
    """A daemon with the loop, the store's presets, and a ledger: `run(part, model, person)`
    prints the part through the gate, the review taking the model's proposals and then
    answering ok. `reviewed=False` is a job with no review, where the loop asks."""
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        (tmp_path / "work").mkdir()
        shown: list[ReviewRequest] = []

        def review(request: ReviewRequest) -> Any:
            shown.append(request)
            from print_shop.setup.review import Review  # noqa: PLC0415

            return Review(approved=True)

        def run(
            part: str, model: Model, person: Person, reviewed: bool = True, **limits: Any
        ) -> tuple[str, Any]:
            bridge = MemoryBridge()
            daemon = Daemon(
                cfg=CFG, profiles=STORE_PROFILES, workdir=tmp_path / "work",
                printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
                ledger=ledger,
                proposer=Proposer(ask=_model, ask_questions=_questions,
                                  lessons=lambda f, kind: print_lessons.for_part(ledger, f, kind)),
                asker=replay_asker(ANSWERS), reviewer=takes(review) if reviewed else None,
                loop=PrintLoop(chat=model, person=person, **limits),
            )
            with daemon:
                job_id = daemon.submit(write_stl(part, tmp_path))
                daemon.wait_for_gate(job_id, timeout_s=30)
                bridge.press(CFG, "approve")
                result = daemon.wait_for_finish(job_id, timeout_s=30)
                assert result.printed, result.error
            row = ledger.get(job_id)
            assert row is not None and row.proposal is not None
            return job_id, row

        run.shown = shown  # type: ignore[attr-defined]
        yield ledger, run


#: A brim and a plate 5 C warmer: what answers warping on a flat, wide part.
WARPING = [
    {"key": "brim_type", "value": "outer_only", "feature": "bed_contact",
     "knowledge": "adhesion", "reason": "6200 mm2 flat on the plate: a brim holds its edges"},
    {"key": "cool_plate_temp", "value": "40", "feature": "bed_contact",
     "knowledge": "adhesion", "reason": "a warmer plate keeps the first layers stuck down"},
]


# The criteria's replays
# --------------------------------------------------------------------------


def test_a_flat_part_with_a_warping_risk_keeps_its_pose_and_gets_settings_without_asking(
    shop: Any,
) -> None:
    ledger, run = shop
    model = Model(
        [("orca_setting", {}), ("past_prints", {})],
        [("slice", {"rotation": [0, 0, 0], "settings": WARPING[:1]})],
        [("propose", {"rotation": [0, 0, 0], "settings": WARPING,
                      "reasoning": "flat and wide: it lies flat, and warping is the plate's "
                                   "and a brim's to answer"})],
    )
    person = Person()
    job_id, row = run("lid", model, person)
    assert person.asked == [], "nothing big: nobody was asked"
    assert (row.rotation_deg, row.orientation_source) == ((0.0, 0.0, 0.0), "as_filed"), (
        "the loop proposed no turn, so it's the file's own pose")
    proposal = row.proposal
    assert proposal.model == "loop:fake"
    made = {c.key: (c.value, c.source) for c in proposal.changes}
    assert made.items() >= {
        "brim_type": ("outer_only", Source.MODEL), "cool_plate_temp": ("40", Source.MODEL)}.items()
    assert [c.tool for c in proposal.loop] == [
        "orca_setting", "past_prints", "slice", "propose"]
    assert proposal.loop[0].result.startswith("## Which setting answers which symptom")
    assert proposal.loop[2].result.startswith("Orca: ")
    assert proposal.loop[3].arguments["rotation"] == [0, 0, 0]
    assert all(c.seconds >= 0 for c in proposal.loop)
    assert proposal.loop_ending.startswith("proposed, after 3 calls in "), (
        "how it ended, with its calls and time")


def test_a_part_that_needs_a_turn_to_clear_an_overhang_asks_first_with_its_reason(
    shop: Any,
) -> None:
    """The tee stands on its pillar, its slab's underside over the air: turned over, nothing
    needs support. A pose other than the file's own is big, so the loop asks first."""
    ledger, run = shop
    model = Model(
        [("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "slab down"})],
        [("ask", {"question": "Turn it over, so the slab lies on the plate?",
                  "reason": "the slab's underside would need supports", "rotation": [180, 0, 0]})],
        [("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "slab down"})],
    )
    person = Person("yes")
    job_id, row = run("tee", model, person, reviewed=False)
    [asked] = person.asked
    assert "Turn it over, so the slab lies on the plate?" in asked
    assert "turn it to 180,0,0 from the file's own pose" in asked
    assert "because the slab's underside would need supports" in asked
    assert _results(row.proposal)[0] == (
        "propose", "ask first: turn it to 180,0,0 from the file's own pose. Ask the person with "
        "`ask`, then propose.")
    assert row.rotation_deg == (180.0, 0.0, 0.0)


def test_a_turn_the_person_says_no_to_is_their_correction_and_a_lesson(shop: Any) -> None:
    ledger, run = shop
    no = "no, the slab's top is the face that shows"
    model = Model(
        [("ask", {"question": "Turn it over?", "reason": "no supports that way",
                  "rotation": [180, 0, 0]})],
        [("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "slab down"})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "as it lies"})],
        [("ask", {"question": "Supports under the slab, then?", "reason": "it's over the air",
                  "rotation": [0, 0, 0]})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "as it lies"})],
    )
    person = Person(no, "yes")
    job_id, row = run("tee", model, person, reviewed=False)
    results = _results(row.proposal)
    assert results[1] == ("propose", f"refused: the person said no to pose 180,0,0: {no}")
    assert results[2][1].startswith("ask first: supports on, for ")
    assert row.rotation_deg == (0.0, 0.0, 0.0)
    assert [(c.key, c.preset, c.value, c.reason) for c in row.corrections or ()] == [
        ("orientation", "180,0,0", "0,0,0", no)]
    [lesson] = ledger.query("SELECT * FROM print_lessons WHERE setting='orientation'")
    assert (lesson["value"], lesson["text"]) == ("0,0,0", no)


def test_past_prints_says_a_success_with_its_flaw(shop: Any, tmp_path: Path) -> None:
    ledger, run = shop
    lid = write_stl("lid", tmp_path)
    ledger.record_start_requested(record("job-0").model_copy(update={
        "stl_name": "tray.stl", "features": measure(lid, analyze(lid).bed_contact_area_mm2)}))
    ledger.record_started("job-0")
    ledger.record_ended("job-0", 9)
    ledger.record_answer("job-0", Answer(result=Result.SUCCESS, symptom=Symptom.STRINGING))
    model = Model(
        [("past_prints", {})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "flat"})],
    )
    _, row = run("lid", model, Person())
    assert "- tray.stl, job job-0: success, with stringing; corrected nothing" \
           in row.proposal.loop[0].result


def test_a_part_like_an_earlier_one_gets_its_kept_lessons_and_the_review_says_which(
    shop: Any, tmp_path: Path
) -> None:
    ledger, run = shop
    lid = write_stl("lid", tmp_path)
    earlier = record("job-0").model_copy(update={
        "stl_name": "tray.stl", "features": measure(lid, analyze(lid).bed_contact_area_mm2)})
    ledger.record_start_requested(earlier)
    made = print_lessons.write(ledger, "job-0", "reviewer: test", "a big flat part lifts",
                               ["flat_and_wide", "large_bed_contact"], "brim_type=outer_only")
    print_lessons.accept(ledger, made, "reviewer: test")
    model = Model(
        [("past_prints", {})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "flat"})],
    )
    job_id, row = run("lid", model, Person())
    found = row.proposal.loop[0].result
    assert f"- Lesson {made}, from tray.stl, for a part that's flat_and_wide, large_bed_contact" \
           in found
    assert "- tray.stl, job job-0: no outcome yet; corrected nothing" in found
    [given] = [g for g in row.proposal.lessons if g.id == made]
    assert given.applied and given.looked_up
    assert row.proposal.get("brim_type") is not None
    shown = render(run.shown[-1])
    assert "(set it; the print loop looked it up with past_prints)" in shown


# The limit, and the fallback
# --------------------------------------------------------------------------


def test_past_its_limit_the_job_falls_back_to_the_single_calls_and_says_so(shop: Any) -> None:
    ledger, run = shop
    model = Model(forever=[("orca_setting", {})])
    job_id, row = run("lid", model, Person(), max_calls=3)
    assert len(model.shown) == 3
    proposal = row.proposal
    assert proposal.model == "recorded", "the single settings call made it"
    assert row.orientation_source == "as_filed", (
        "and the single orientation call's pose was the file's own, so there was no turn")
    assert any(n.startswith("the print loop stopped, past its limit of 3 calls, after 3 calls")
               for n in proposal.notes)
    assert [c.tool for c in proposal.loop] == ["orca_setting"] * 3, "what it did is kept"


def test_a_model_that_cant_be_reached_falls_back_too(shop: Any) -> None:
    ledger, run = shop

    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("LiteLLM is down")

    job_id, row = run("lid", down, Person())
    assert any("the model couldn't be reached: RuntimeError: LiteLLM is down" in n
               for n in row.proposal.notes)


def test_a_proposal_out_of_bounds_goes_back_to_the_model(shop: Any) -> None:
    ledger, run = shop
    model = Model(
        [("propose", {"rotation": [0, 0, 0], "reasoning": "x", "settings": [
            {"key": "cool_plate_temp", "value": "300"}]})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "x"})],
    )
    job_id, row = run("lid", model, Person())
    assert _results(row.proposal)[0] == (
        "propose", "refused, outside their bounds: cool_plate_temp=300: outside 0-110")


# What's big, decided in code
# --------------------------------------------------------------------------


def _part(tmp_path: Path, stl: str = "lid", lessons: tuple[GivenLesson, ...] = (),
          intake: Intake | None = None) -> Part:
    path = write_stl(stl, tmp_path)
    process = json.loads(STORE_PROFILES.process.read_text())
    filament = json.loads(STORE_PROFILES.filament.read_text()) if STORE_PROFILES.filament else {}
    machine = json.loads(STORE_PROFILES.machine.read_text())
    facts = PartFacts(intake=intake or Intake(),
                      features=measure(path, analyze(path).bed_contact_area_mm2),
                      machine=machine, process=process, filament=filament)
    return Part(stl=path, facts=facts, candidates=tuple(candidates(path)),
                build_volume_mm=(256.0, 256.0, 256.0), skill=load(GROUPS),
                profiles=STORE_PROFILES, slicer=AnyPart(), workdir=tmp_path / "loop",
                lessons=lessons)


def _pose(part: Part, rotation: tuple[float, float, float]) -> Orientation:
    return next(c for c in part.candidates if c.rotation_deg == rotation)


def _kept(key: str, value: str) -> GivenLesson:
    return GivenLesson(id=7, text="learned", key=key, value=value, job_id="job-0",
                       stl_name="tray.stl", traits=("large_bed_contact",),
                       reviewed_by="reviewer: test")


@pytest.mark.parametrize(("changes", "lessons", "seconds", "whats"), [
    ({}, (), None, []),
    ({"brim_type": "outer_only"}, (), None, []),
    ({"cool_plate_temp": "40"}, (), None, []),
    ({"cool_plate_temp": "45"}, (), None, ["setting cool_plate_temp"]),
    ({"cool_plate_temp": "45"}, (_kept("cool_plate_temp", "45"),), None, []),
    ({"cool_plate_temp": "52"}, (_kept("cool_plate_temp", "45"),), None,
     ["setting cool_plate_temp"]),
    ({"additional_cooling_fan_speed": "15"}, (), None, []),
    ({"additional_cooling_fan_speed": "30"}, (), None, ["setting additional_cooling_fan_speed"]),
    ({"enable_support": "1"}, (), None, ["supports"]),
    ({}, (), int(1000 * (1 + BIG_TIME_SHARE)) + 1, ["time"]),
    ({}, (), int(1000 * (1 + BIG_TIME_SHARE)), []),
])
def test_what_is_big_is_decided_in_code(
    tmp_path: Path, changes: dict[str, str], lessons: tuple[GivenLesson, ...],
    seconds: int | None, whats: list[str],
) -> None:
    part = _part(tmp_path, lessons=lessons)
    found = big(part, _pose(part, (0.0, 0.0, 0.0)), changes, seconds, 1000)
    assert [w for w, _ in found] == whats


def test_a_pose_other_than_the_files_own_is_big(tmp_path: Path) -> None:
    part = _part(tmp_path)
    assert [w for w, _ in big(part, _pose(part, (180.0, 0.0, 0.0)), {}, None, None)] == [
        "pose 180,0,0"]


def test_supports_the_guard_would_switch_on_are_big(tmp_path: Path) -> None:
    """The tee as it stands: nothing proposes supports, but the guard would for its overhang."""
    part = _part(tmp_path, "tee")
    [(what, said)] = big(part, _pose(part, (0.0, 0.0, 0.0)), {}, None, None)
    assert what == "supports" and said.startswith("supports on, for ")


def test_supports_the_intake_answered_are_the_persons_already(tmp_path: Path) -> None:
    from print_shop.setup.intake import Answer, Option, Question  # noqa: PLC0415

    on = Option(label="tree supports", settings={"enable_support": "1"}, impact="",
                recommended=True, why="")
    asked = Intake(questions=(Question(feature="overhang", text="?", options=(on, on)),),
                   answers=(Answer(choice=0),))
    part = _part(tmp_path, "tee", intake=asked)
    assert big(part, _pose(part, (0.0, 0.0, 0.0)), {"enable_support": "1"}, None, None) == []


def test_asking_about_a_small_change_asks_nobody(shop: Any) -> None:
    ledger, run = shop
    model = Model(
        [("ask", {"question": "A brim?", "reason": "edges", "rotation": [0, 0, 0],
                  "settings": WARPING[:1]})],
        [("propose", {"rotation": [0, 0, 0], "settings": WARPING[:1], "reasoning": "brim"})],
    )
    person = Person()
    job_id, row = run("lid", model, person)
    assert person.asked == []
    assert _results(row.proposal)[0] == (
        "ask", "not a big change, so nobody was asked: propose it, and the person reviews it "
        "with the rest")


def test_a_call_cut_off_at_its_limit_is_a_turn_not_an_ending(shop: Any) -> None:
    """The Sponsor's lid, 2026-11-14: the settings call was cut off at 16,000 tokens after 499 s,
    and none of it was used. In the loop the cut-off is said, recorded and counted."""
    from print_shop.expert.client import CallInfo, CutOff  # noqa: PLC0415

    script = Model(
        [("propose", {"rotation": [0, 0, 0], "settings": WARPING[:1], "reasoning": "brim"})])
    first = True

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        nonlocal first
        if first:
            first = False
            raise CutOff("cut off at max_tokens=16000 after 16000 tokens (3895 reasoning) and "
                         "499.0 s", CallInfo(model="fake", finish_reason="length",
                                             prompt_tokens=9000, completion_tokens=16000,
                                             reasoning_tokens=3895, max_tokens=16000,
                                             seconds=499.0))
        assert "Your last answer was cut off at its output limit" in messages[-1]["content"]
        return script(messages, tools)

    ledger, run = shop
    job_id, row = run("lid", chat, Person())
    assert [(c.tool, c.seconds) for c in row.proposal.loop][0] == ("(model)", 499.0)
    assert row.proposal.loop[0].result.startswith("cut off: cut off at max_tokens=16000")
    assert row.proposal.loop_ending.startswith("proposed, after 2 calls")
    assert row.proposal.get("brim_type") is not None, "its proposal is used"


def test_each_tools_free_text_is_capped() -> None:
    from print_shop.setup.loop import TOOLS  # noqa: PLC0415

    def strings(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, dict):
            if node.get("type") == "string":
                yield node
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, list):
            for value in node:
                yield from strings(value)

    for tool in TOOLS:
        params = tool["function"]["parameters"]
        assert all("maxLength" in s for s in strings(params)), tool["function"]["name"]


def test_past_prints_sets_aside_words_that_arent_traits(shop: Any, tmp_path: Path) -> None:
    """The one real call, 2026-11-14: crew-code-think asked `past_prints` for "flat", "wide",
    "lid", "PLA" and "large bed contact". Now the schema names the traits, and a word that isn't
    one is said and set aside, leaving the part's own."""
    from print_shop.setup.loop import TOOLS  # noqa: PLC0415
    from print_shop.setup.traits import TRAITS  # noqa: PLC0415

    [tool] = [t for t in TOOLS if t["function"]["name"] == "past_prints"]
    assert tool["function"]["parameters"]["properties"]["traits"]["items"]["enum"] == list(TRAITS)
    ledger, run = shop
    model = Model(
        [("past_prints", {"traits": ["flat", "lid", "PLA"]})],
        [("propose", {"rotation": [0, 0, 0], "settings": [], "reasoning": "flat"})],
    )
    job_id, row = run("lid", model, Person())
    said = row.proposal.loop[0].result
    assert said.startswith("Not traits, so set aside: flat, lid, PLA. The traits are: ")
    assert "Kept lessons for this part (flat_and_wide, large_bed_contact):" in said
