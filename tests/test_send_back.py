"""The orientation sent back at the review, and the lesson the orientation step learns from it.

mqucifer/print-shop#128. On 2026-11-14 the orientation model stood a flat lid on its edge "to
avoid warping". The Sponsor: "i would like to just send it back for a reason - that should
provide the live lesson that just because warping is a concern we dont re-orient the whole
print", and "should ask for a reason to send back - this should be a conversation".

So at the review the pose is sent back in a short conversation: the step says why it turned the
part, the person names the pose and says why the turn was wrong, or meets the step's concern by
settings, each priced by Orca. The reason is the correction, drawn as a print lesson like any
(`setup/lessons.py`); once a reviewer keeps it, a later part with the same traits, read in its
file's own pose, has it given to the orientation step with the poses. Every model, slicer,
broker and printer here is a fake.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.core.contracts import Proposal as Chosen
from print_shop.core.state import AppliedOrientation, Orientation, SliceResult
from print_shop.expert.client import LLMConfig
from print_shop.expert.orientation import OrientationRequest
from print_shop.ledger import Ledger
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.intake import Estimate, replay_asker
from print_shop.setup.proposal import ORIENTATION, Change, GivenLesson, Source
from print_shop.setup.review import ReviewRequest, cli_reviewer, render, send_back
from print_shop.slicer.orca import SlicerProfiles, parse_gcode_header
from tests.fakes import (
    CI_PROFILES,
    DATA,
    FramePrinter,
    MemoryBridge,
    RecordedSlicer,
    echo_settings,
)
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_review import ANSWERS, _model, _questions, _request

#: Why the model stood the lid on its edge, near enough its words of 2026-11-14.
WARPING = ("standing it on its edge avoids warping or poor adhesion from the large surface "
           "area of the flat orientation")
REASON = "just because warping is a concern we don't re-orient the whole print"

LAID = Orientation(rotation_deg=(0.0, 0.0, 0.0), overhang_area_mm2=0.0,
                   bed_contact_area_mm2=6200.0, height_mm=6.0)
EDGE = Orientation(rotation_deg=(0.0, 90.0, 0.0), overhang_area_mm2=0.0,
                   bed_contact_area_mm2=372.0, height_mm=100.0)
FLIPPED = Orientation(rotation_deg=(180.0, 0.0, 0.0), overhang_area_mm2=0.0,
                      bed_contact_area_mm2=6200.0, height_mm=6.0)


def _on_edge(rationale: str = WARPING, cost: Any = None) -> ReviewRequest:
    """The lid's review, stood on its edge by the step for `rationale`."""
    base = _request()
    return ReviewRequest(
        presets=base.presets, proposal=base.proposal, slice=base.slice, profiles=base.profiles,
        orientation=AppliedOrientation(rotation_deg=EDGE.rotation_deg, rationale=rationale,
                                       height_mm=100.0, bed_contact_area_mm2=372.0),
        process={"curr_bed_type": "Textured PEI Plate", "wall_loops": "2"},
        filament={"textured_plate_temp": ["70"], "textured_plate_temp_initial_layer": ["70"]},
        candidates=(LAID, FLIPPED, EDGE),
        laid_traits=("flat_and_wide", "large_bed_contact"),
        cost=cost,
    )


class Person:
    """Types the answers given, in order, and keeps everything shown."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.shown: list[str] = []
        self.asked: list[str] = []

    def read(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self.answers.pop(0)

    def write(self, text: str) -> None:
        self.shown.append(text)

    @property
    def said(self) -> str:
        return "\n".join(self.shown)


# The conversation
# --------------------------------------------------------------------------


def test_sending_the_pose_back_asks_why_and_a_reason_is_required() -> None:
    person = Person("1", "", REASON)
    review = send_back(_on_edge(), person.read, person.write)
    assert review is not None
    assert person.shown[0] == f"The orientation step printed it at 0,90,0: {WARPING}"
    assert ("  1) 0,0,0  as the file lays it: 0 mm2 of overhang, 6200 mm2 on the plate, 6 mm tall"
            in person.said)
    assert "0,90,0:" not in person.said.split("Which pose")[1].split("Why")[0], (
        "the pose it has isn't offered"
    )
    assert "Why is 0,90,0 wrong for this part? It turned it for warping" in person.said
    assert "A reason is needed: it's what the orientation step learns from next time." in (
        person.said
    )
    assert person.asked == ["pose> ", "why> ", "why> "]
    [turned] = review.corrections
    assert (turned.key, turned.preset, turned.value, turned.reason, turned.source) == (
        ORIENTATION, "0,90,0", "0,0,0", REASON, Source.SPONSOR
    )
    assert turned.feature == "flat_and_wide, large_bed_contact", "the file's own pose's traits"
    assert turned.reading == (
        "the step's 0,90,0: 0 mm2 of overhang, 372 mm2 on the plate, 100 mm tall; yours 0,0,0  "
        "as the file lays it: 0 mm2 of overhang, 6200 mm2 on the plate, 6 mm tall"
    )


def test_its_concern_can_be_met_by_settings_each_priced_by_orca() -> None:
    priced: list[tuple[Change, ...]] = []

    def cost(changes: tuple[Change, ...]) -> Estimate:
        priced.append(changes)
        return Estimate(print_time_s=654 + 120 * len(priced), filament_g=3.6, filament_cm3=2.9)

    person = Person("1", "1")
    review = send_back(_on_edge(cost=cost), person.read, person.write)
    assert review is not None
    lines = [s for s in person.shown if s.startswith("  ") and ")" in s[:6]][-4:]
    assert lines == [
        "  1) a brim round the outside (brim_type=outer_only): Orca: 12 min, 3.6 g, +2 min",
        "  2) a warmer plate, 75 C (textured_plate_temp=75, textured_plate_temp_initial_layer=75):"
        " Orca: 14 min, 3.6 g, +4 min",
        "  3) the chamber's exhaust off while it prints (during_print_exhaust_fan_speed=0): "
        "Orca: 16 min, 3.6 g, +6 min",
        "  4) the side fan off (additional_cooling_fan_speed=0): Orca: 18 min, 3.6 g, +8 min",
    ]
    turned, brim = review.corrections
    assert turned.reason == (
        "warping is met by settings, not by turning the part: a brim round the outside"
    )
    assert (brim.key, brim.value, brim.source) == ("brim_type", "outer_only", Source.SPONSOR)


def _kept(id: int, key: str, value: str, withheld: str = "") -> GivenLesson:
    return GivenLesson(id=id, text="learned on a PETG tray", key=key, value=value, job_id="job-0",
                       stl_name="tray.stl", traits=("flat_and_wide", "large_bed_contact"),
                       reviewed_by="reviewer: test", material="PETG", withheld=withheld)


def _offers(person: Person) -> list[str]:
    start = next(i for i, s in enumerate(person.shown) if s.startswith("Why is"))
    return [s for s in person.shown[start + 1:] if s.startswith("  ")]


def test_a_way_offers_a_kept_lessons_value_and_names_it() -> None:
    """The Sponsor's ledger keeps PETG large-footprint lessons for the plate at 75, the exhaust
    at 10 and the side fan at 20: offering 0 would contradict what the shop has learned."""
    asked: list[Orientation] = []
    lessons = (_kept(3, "textured_plate_temp", "75"),
               _kept(4, "textured_plate_temp_initial_layer", "75"),
               _kept(5, "during_print_exhaust_fan_speed", "10"),
               _kept(6, "additional_cooling_fan_speed", "20"))

    def lessons_for(pose: Orientation) -> tuple[GivenLesson, ...]:
        asked.append(pose)
        return lessons

    request = replace(_on_edge(), lessons_for=lessons_for)
    person = Person("1", "3")
    review = send_back(request, person.read, person.write)
    assert asked == [LAID], "the lessons for the pose the person chose"
    assert _offers(person) == [
        "  1) a brim round the outside (brim_type=outer_only)",
        "  2) the plate at 75 C, as lessons 3 and 4 keep it "
        "(textured_plate_temp=75, textured_plate_temp_initial_layer=75)",
        "  3) the chamber's exhaust at 10% while it prints, as lesson 5 keeps it "
        "(during_print_exhaust_fan_speed=10)",
        "  4) the side fan at 20%, as lesson 6 keeps it (additional_cooling_fan_speed=20)",
    ]
    assert review is not None
    turned, exhaust = review.corrections
    assert (exhaust.key, exhaust.value) == ("during_print_exhaust_fan_speed", "10")
    assert turned.reason.endswith("the chamber's exhaust at 10% while it prints, as lesson 5 "
                                  "keeps it")


def test_a_lesson_withheld_for_its_filament_leaves_the_fixed_value() -> None:
    withheld = (_kept(6, "additional_cooling_fan_speed", "20",
                      withheld="learned on PETG; this print is PLA"),)
    request = replace(_on_edge(), lessons_for=lambda pose: withheld)
    person = Person("1", REASON)
    send_back(request, person.read, person.write)
    assert "  4) the side fan off (additional_cooling_fan_speed=0)" in _offers(person)


def test_with_no_lesson_for_a_key_its_fixed_value_is_offered() -> None:
    one = (_kept(6, "additional_cooling_fan_speed", "20"),)
    request = replace(_on_edge(), lessons_for=lambda pose: one)
    person = Person("1", REASON)
    send_back(request, person.read, person.write)
    assert _offers(person) == [
        "  1) a brim round the outside (brim_type=outer_only)",
        "  2) a warmer plate, 75 C (textured_plate_temp=75, textured_plate_temp_initial_layer=75)",
        "  3) the chamber's exhaust off while it prints (during_print_exhaust_fan_speed=0)",
        "  4) the side fan at 20%, as lesson 6 keeps it (additional_cooling_fan_speed=20)",
    ]


def test_a_reason_with_nothing_settings_answer_offers_no_settings() -> None:
    person = Person("2", "it prints better flat")
    review = send_back(_on_edge("it has the least overhang"), person.read, person.write)
    assert review is not None
    assert "which settings can meet instead" not in person.said
    assert review.corrections[0].value == "180,0,0"


def test_the_pose_and_reason_can_be_given_at_once() -> None:
    person = Person()
    review = send_back(_on_edge(), person.read, person.write, "1", REASON)
    assert review is not None and person.asked == []
    assert (review.corrections[0].value, review.corrections[0].reason) == ("0,0,0", REASON)


def test_back_returns_to_the_review_having_sent_nothing() -> None:
    person = Person("orientation", "back", "ok")
    review = cli_reviewer(person.read, person.write)(_on_edge())
    assert review.approved and review.corrections == ()
    assert "  Back to the review: ok, a correction, orientation, or stop." in person.shown


def test_the_review_offers_to_send_the_pose_back_and_names_the_pose_read_in() -> None:
    shown = render(_on_edge())
    assert "send the orientation back (orientation)," in shown
    assert ("  the part's features were read in this pose: rotation (0.0, 90.0, 0.0), 100 mm "
            "tall, 372 mm2 on the plate") in shown


# Through the daemon: job 1 sends the pose back, job 2 is given the lesson
# --------------------------------------------------------------------------


class AnyPart(RecordedSlicer):
    """The cube's recorded slice for any part: what's tested is the pose, not the G-code."""

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        self.calls.append(Path(stl_path))
        plate = json.loads(Path(profiles.process).read_text()).get("curr_bed_type", "Cool Plate")
        recorded = DATA / "recorded" / (
            "cube_pei.gcode" if plate == "Textured PEI Plate" else "cube.gcode"
        )
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(recorded, out / "plate_1.gcode")
        echo_settings(out / "plate_1.gcode", profiles)
        return parse_gcode_header(out / "plate_1.gcode")


class StandsItUp:
    """The orientation model as it was on 2026-11-14: the lid on its edge, for warping. Keeps
    each request, with the lessons it was given. The validator now corrects that pose before
    the review (`orientation.stood_up`), so `pose` lets a test turn it a way the rule allows and
    the person still sends back: top-down, for the same reason."""

    def __init__(self, pose: tuple[float, float, float] = (0.0, 90.0, 0.0)) -> None:
        self.pose = pose
        self.requests: list[OrientationRequest] = []

    def __call__(self, request: OrientationRequest, config: LLMConfig) -> Chosen[Orientation]:
        self.requests.append(request)
        chosen = next(c for c in request.candidates if c.rotation_deg == self.pose)
        return Chosen(value=chosen, source="model:fake", rationale=WARPING, confidence=0.7)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> StandsItUp:
    """Turns the lid top-down for warping: a pose the person sends back."""
    fake = StandsItUp((180.0, 0.0, 0.0))
    monkeypatch.setattr(graph, "propose_orientation", fake)
    return fake


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[tuple[Ledger, Any]]:
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        (tmp_path / "work").mkdir()
        process = json.loads(CI_PROFILES.process.read_text())
        (tmp_path / "process.json").write_text(json.dumps({**process, "wall_loops": "2"}))
        profiles = CI_PROFILES.model_copy(update={"process": tmp_path / "process.json"})

        def run(*answers: str) -> tuple[str, Person]:
            person = Person(*answers)
            bridge = MemoryBridge()
            daemon = Daemon(
                cfg=CFG, profiles=profiles, workdir=tmp_path / "work", printer=FramePrinter(),
                slicer=AnyPart(), bridge=lambda cfg: bridge, ledger=ledger,
                llm=LLMConfig(api_key=SecretStr("not-a-key"), model="fake"),
                proposer=Proposer(ask=_model, ask_questions=_questions),
                asker=replay_asker(ANSWERS), reviewer=cli_reviewer(person.read, person.write),
            )
            with daemon:
                job_id = daemon.submit(write_stl("lid", tmp_path))
                daemon.wait_for_gate(job_id, timeout_s=30)
                bridge.press(CFG, "approve")
                result = daemon.wait_for_finish(job_id, timeout_s=30)
                assert result.printed, result.error
            return job_id, person

        yield ledger, run


def test_a_pose_sent_back_turns_the_part_and_is_recorded_as_the_correction(
    shop: tuple[Ledger, Any], model: StandsItUp
) -> None:
    ledger, run = shop
    job_id, person = run("wall_loops=4 because I handle it", "orientation", "1", REASON, "ok")
    assert person.said.count("Settings for this print") == 3, (
        "the review, a correction, then again once turned"
    )
    assert "Orientation: (0.0, 0.0, 0.0), sent back at the review, by you: " + REASON in (
        person.said
    )
    row = ledger.get(job_id)
    assert row is not None
    assert (row.rotation_deg, row.orientation_source) == ((0.0, 0.0, 0.0), "sponsor")
    assert [(c.key, c.value) for c in row.corrections or ()] == [
        ("wall_loops", "4"), (ORIENTATION, "0,0,0")
    ], "both kept with the job"
    assert row.proposal is not None and row.proposal.get("wall_loops") is not None, (
        "a correction made before the turn stands after it"
    )
    assert {f.id: f.value for f in row.features or ()}["bed_contact"] == pytest.approx(6200), (
        "the features are read in the pose printed"
    )


def test_the_correction_is_a_lesson_candidate_keyed_to_the_file_pose(
    shop: tuple[Ledger, Any], model: StandsItUp
) -> None:
    ledger, run = shop
    job_id, _ = run("orientation", "1", REASON, "ok")
    [lesson] = ledger.query("SELECT * FROM print_lessons")
    assert (lesson["setting"], lesson["value"], lesson["text"], lesson["status"]) == (
        ORIENTATION, "0,0,0", REASON, "candidate"
    )
    assert json.loads(lesson["traits"]) == ["flat_and_wide", "large_bed_contact"]
    [evidence] = ledger.query("SELECT * FROM print_lesson_evidence")
    detail = json.loads(evidence["detail"])
    assert (evidence["job_id"], detail["preset"], detail["value"]) == (job_id, "180,0,0", "0,0,0")
    assert detail["reading"].startswith("the step's 180,0,0: 0 mm2 of overhang, 6200 mm2")
    assert "corrected orientation 180,0,0 -> 0,0,0: " + REASON in print_lessons.listing(ledger)


def test_a_kept_orientation_lesson_reaches_the_next_parts_orientation_by_itself(
    shop: tuple[Ledger, Any], model: StandsItUp
) -> None:
    ledger, run = shop
    run("orientation", "1", REASON, "ok")
    assert model.requests[0].lessons == (), "nothing kept yet"
    [lesson] = ledger.query("SELECT id FROM print_lessons")
    print_lessons.accept(ledger, lesson["id"], "reviewer: test")

    # The model turns it again, so the first print's pose is offered, ticked
    # (mqucifer/print-shop#211): a plain ok says it isn't used, and the next goes on without it.
    job_2, person = run("ok", "ok")
    assert "1 ticked isn't used yet: use to take it, or ok again to go on without" in person.said
    [line] = model.requests[1].lessons
    assert line.startswith(f"Lesson {lesson['id']}, from lid.stl, for a part that's flat_and_wide"
                           ", large_bed_contact")
    assert line.endswith(f"orientation=0,0,0: {REASON}")
    assert f"orientation=0,0,0: {REASON} (given to the orientation step with its poses)" in (
        person.said
    ), "the review says which it was given"
    [given] = ledger.query("SELECT * FROM job_lessons WHERE job_id=?", job_2)
    assert given["lesson"] == lesson["id"]
    [scored] = ledger.query("SELECT score, why FROM print_lesson_scores")
    assert (scored["score"], scored["why"]) == ("used", "the pose wasn't sent back at the review")


def test_the_ways_offered_take_the_lessons_for_the_pose_chosen(
    shop: tuple[Ledger, Any], model: StandsItUp
) -> None:
    """Through the daemon: lessons kept for a flat, wide part reach the offers once the person
    lays the lid flat, though the pose on its edge has neither trait."""
    ledger, run = shop
    ledger.record_start_requested(record("job-0"))
    for key, value in (("during_print_exhaust_fan_speed", "10"),
                       ("additional_cooling_fan_speed", "20")):
        made = print_lessons.write(ledger, "job-0", "reviewer: test", "PETG lifts at the corners",
                                   ["flat_and_wide", "large_bed_contact"], f"{key}={value}")
        print_lessons.edit(ledger, made, "reviewer: test", material="any")
    job_id, person = run("orientation", "1", "2", "ok")
    exhaust = next(i for i, s in enumerate(person.shown) if "exhaust at 10%" in s)
    assert person.shown[exhaust].startswith("  2) the chamber's exhaust at 10% while it prints, "
                                            "as lesson 1 keeps it")
    assert any(s.startswith("  3) the side fan at 20%, as lesson 2 keeps it")
               for s in person.shown)
    row = ledger.get(job_id)
    assert row is not None and ("during_print_exhaust_fan_speed", "10") in [
        (c.key, c.value) for c in row.corrections or ()
    ]


def test_an_orientation_lesson_never_sets_a_setting(
    shop: tuple[Ledger, Any], model: StandsItUp, tmp_path: Path
) -> None:
    """It's the orientation step's: the settings proposal is never given it."""
    ledger, run = shop
    run("orientation", "1", REASON, "ok")
    [lesson] = ledger.query("SELECT id FROM print_lessons")
    print_lessons.accept(ledger, lesson["id"], "reviewer: test")
    from print_shop.setup.features import measure  # noqa: PLC0415

    features = measure(write_stl("lid", tmp_path), 6200.0)
    assert print_lessons.for_part(ledger, features) == ()
    assert [g.key for g in print_lessons.for_orienting(ledger, features)] == [ORIENTATION]


def test_a_lesson_isnt_given_to_a_part_without_its_traits(
    shop: tuple[Ledger, Any], model: StandsItUp, tmp_path: Path
) -> None:
    ledger, run = shop
    run("orientation", "1", REASON, "ok")
    [lesson] = ledger.query("SELECT id FROM print_lessons")
    print_lessons.accept(ledger, lesson["id"], "reviewer: test")
    from print_shop.setup.features import measure  # noqa: PLC0415

    cube = measure(write_stl("cube", tmp_path), 400.0)
    assert print_lessons.for_orienting(ledger, cube) == ()


def test_standing_the_lid_up_for_warping_is_corrected_before_the_review(
    shop: tuple[Ledger, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-14's pose: on its edge, 100 mm tall, for warping. The validator lays it flat
    again (`orientation.stood_up`), and the review shows the flat pose, before anyone asks."""
    fake = StandsItUp()
    monkeypatch.setattr(graph, "propose_orientation", fake)
    ledger, run = shop
    job_id, person = run("ok")
    row = ledger.get(job_id)
    assert row is not None and row.rotation_deg in ((0.0, 0.0, 0.0), (180.0, 0.0, 0.0))
    assert ("the part's features were read in this pose: rotation (0.0, 0.0, 0.0), 6 mm tall, "
            "6200 mm2 on the plate") in person.said, "the flat pose's own figures"
    assert "Orientation: (0.0, 0.0, 0.0), moved by the validator: (0.0, 0.0, 0.0) puts 6200 mm2 " \
           "on the plate against 372" in person.said
    assert f"The model had said: {WARPING}" in person.said
    assert {f.id: f.value for f in row.features or ()}["bed_contact"] == pytest.approx(6200)


def test_a_flat_part_with_a_warping_risk_keeps_its_pose_and_the_settings_answer_it(
    shop: tuple[Ledger, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Sponsor's criterion: the model stands the lid up for warping; it keeps its flat pose,
    and the proposal names the plate's temperature and a brim, by Orca's keys."""
    monkeypatch.setattr(graph, "propose_orientation", StandsItUp())
    ledger, run = shop
    job_id, person = run("ok")
    row = ledger.get(job_id)
    assert row is not None and row.rotation_deg in ((0.0, 0.0, 0.0), (180.0, 0.0, 0.0))
    assert row.proposal is not None
    [risk] = [n for n in row.proposal.notes if n.startswith("warping risk")]
    assert risk == (
        "warping risk: 6200 mm2 on the plate. The plate's temperature (cool_plate_temp), a brim "
        "(brim_type), the chamber's exhaust (during_print_exhaust_fan_speed) and the side fan "
        "(additional_cooling_fan_speed) answer it, not turning the part"
    )
    assert f"  note: {risk}" in person.said, "the review shows it"


def test_the_orientation_model_reads_the_skills_symptom_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from print_shop.expert import orientation as expert  # noqa: PLC0415

    seen: list[str] = []

    def reply(config: Any, system: str, user: str, schema: Any) -> dict[str, Any]:
        seen.append(system)
        return {"rotation_deg": [0, 0, 0], "rationale": "flat", "confidence": 0.9}

    monkeypatch.setattr(expert, "complete_json", reply)
    request = OrientationRequest(candidates=(LAID, EDGE), build_volume_mm=(256.0, 256.0, 256.0))
    expert.propose_orientation(request, LLMConfig(api_key=SecretStr("x"), model="fake"))
    [system] = seen
    assert "never a reason to turn the part" in system
    assert "## Which setting answers which symptom" in system
    assert "`elefant_foot_compensation`" in system
