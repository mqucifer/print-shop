"""A plate's questions wait for no model (mqucifer/print-shop#109, mqucifer/print-shop#208).

One part's print loop chooses its pose after the questions. A plate has a call a part for its
poses, so those wait till after them too, or print-shop's two questions would wait minutes on
models that they don't need. The models, the person and the parts are all made up.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.core.contracts import Proposal as Chosen
from print_shop.expert.client import LLMConfig
from print_shop.expert.orientation import OrientationRequest
from print_shop.ingest.orientation import AXIS_ALIGNED
from print_shop.orchestrator import graph
from print_shop.runtime import describe_all
from print_shop.setup.intake import replay_asker
from print_shop.setup.questions import PLATE_WORDS, PURPOSE, SEEN, STOCK
from tests.plates import box
from tests.reviewing import flat, screen
from tests.test_plate_job import Rig, _caps
from tests.test_waiting import WAIT_S, Person

LLM = LLMConfig(api_key=SecretStr("sk-test"), base_url="http://models.invalid")


class Poses:
    """The orientation model: the pose `wants` picks, and when it was asked."""

    def __init__(self, wants: Callable[[Any], Any] = lambda c: not any(c.rotation_deg)) -> None:
        self.wants = wants
        self.asked_after: list[int] = []  # how many prompts the person had answered by then
        self.person: Person | None = None

    def __call__(self, request: OrientationRequest, llm: LLMConfig) -> Any:
        self.asked_after.append(len(self.person.prompts) if self.person is not None else 0)
        value = next(c for c in request.candidates if self.wants(c))
        return Chosen(value=value, source="model:made-up", rationale="the made-up model chose it",
                      confidence=0.9)


@pytest.fixture
def never() -> Iterator[Callable[..., Any]]:
    """A questions model that doesn't answer until the test is over."""
    over = threading.Event()

    def ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        over.wait(WAIT_S)
        raise RuntimeError("the fake never answers")

    yield ask
    over.set()


def _rig(
    tmp_path: Path, person: Person, poses: Poses, monkeypatch: pytest.MonkeyPatch,
    questions: Any, *typed: str, **waiting: Any,
) -> Rig:
    poses.person = person
    monkeypatch.setattr(graph, "propose_orientation", poses)
    waiting.setdefault("several", True)  # as print_job.py builds it for a plate
    return Rig(tmp_path, *typed, asking=person.asker(), waiting=person.waiting(**waiting),
               questions=questions, llm=LLM)


def test_a_plates_two_questions_wait_for_no_model_and_go_reaches_the_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    """mqucifer/print-shop#208's first Done when, for a plate, with no `--as-laid`."""
    person, poses = Person("3", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        assert rig.press("approve", job).printed
    assert poses.asked_after == [3, 3, 3], "no pose's call before the two answers and go"
    assert PLATE_WORDS[PURPOSE.text] in person.text and PLATE_WORDS[SEEN.text] in person.text
    assert "Type go and Enter to go on without waiting" in flat(person.text)
    said = " ".join(rig.shown[0].split())
    assert "note: you went on without the model's questions after" in said
    rows = rig.ledger.job_rows(job)
    assert [r.orientation_source for r in rows] == ["model:made-up"] * 3
    for row in rows:
        assert row.intake is not None
        assert [q.text for q in row.intake.questions] == [q.text for q in STOCK]
        assert row.intake.questions[1].pose == (0.0, 0.0, 0.0), "the pose it was asked in"
        assert row.intake.call["finish_reason"] == "went on"
    assert describe_all(rig.ledger.rows()).count("you went on without the model's") == 1


def test_as_laid_a_plate_waits_for_no_model_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    person, poses = Person("1", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=True)
        rig.press("reject", job)
    assert poses.asked_after == []
    assert "note: you went on without the model's questions" in " ".join(rig.shown[0].split())


def test_the_models_questions_are_added_after_the_two_on_a_plate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    option = {"settings": [], "impact": "made up", "recommended": False, "why": ""}

    def questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [{"feature": "section", "text": "How stiff are the made-up caps?",
                               "options": [
                                   {**option, "label": "as they come", "recommended": True},
                                   {**option, "label": "stiff", "settings": [
                                       {"key": "wall_loops", "value": "4"}]},
                               ]}]}

    person, poses = Person("1", "1", "2"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, questions, "ok")
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        assert rig.press("approve", job).printed
    assert "Added by the model" in person.text and "3. How stiff" in person.text
    assert "Orca: 15 min" in person.text, "priced as the whole plate"
    assert poses.asked_after == [3, 3, 3]
    row = rig.ledger.rows()[0]
    assert row.intake is not None and row.intake.without == ""
    assert [q.text for q in row.intake.questions][2] == "How stiff are the made-up caps?"
    assert "without the model's questions" not in rig.shown[0]


def test_a_failed_questions_call_is_noted_at_a_plates_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("made-up outage")

    person, poses = Person("1", "1"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, down)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        rig.press("reject", job)
    assert "note: the model was unavailable (made-up outage)" in " ".join(rig.shown[0].split())


def test_a_pose_chosen_after_the_questions_is_the_one_sliced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    person, poses = Person("1", "1", "go"), Poses(lambda c: c.rotation_deg == (180.0, 0.0, 0.0))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        assert rig.press("approve", job).printed
    for row in rig.ledger.job_rows(job):
        assert row.rotation_deg == (180.0, 0.0, 0.0) and row.orientation_source == "model:made-up"
    assert "this print 180,0,0: the made-up model chose it" in flat(rig.shown[0])


def test_poses_chosen_that_dont_fit_keep_the_questions_poses_and_the_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    standing = [box(tmp_path / f"panel-{n}.stl", 240, 5, 130) for n in (1, 2)]
    person, poses = Person("2", "1", "go"), Poses(lambda c: c.height_mm < 6)
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(standing, as_laid=False)
        assert rig.press("approve", job).printed
    assert "the poses chosen don't fit one plate together" in flat(rig.shown[0])
    for row in rig.ledger.job_rows(job):
        assert (row.rotation_deg, row.orientation_source) == ((0.0, 0.0, 0.0), "plate:kept")
        assert row.intake is not None and row.intake.answers[0].choice == 1


def test_parts_that_fit_only_turned_are_still_asked_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    lying = [box(tmp_path / f"panel-{n}.stl", 240, 130, 5) for n in (1, 2)]
    person, poses = Person("1", "1", "go"), Poses(lambda c: not any(c.rotation_deg))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(lying, as_laid=False)
        assert rig.press("approve", job).printed
    assert poses.asked_after == [3, 3]
    for row in rig.ledger.job_rows(job):
        assert row.rotation_deg != (0.0, 0.0, 0.0), "the pose they fit in"


@pytest.mark.parametrize(("size", "count", "why"), [
    ((120.0, 120.0, 120.0), 5, "don't all fit on one plate"),
    ((20.0, 20.0, 300.0), 2, "no pose of part 1, part-1.stl, fits the build volume"),
])
def test_what_cant_fit_is_refused_before_any_question_or_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
    size: tuple[float, float, float], count: int, why: str,
) -> None:
    parts = [box(tmp_path / f"part-{n}.stl", *size) for n in range(1, count + 1)]
    person, poses = Person(), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.daemon.submit_several(parts)
        rig.daemon.wait_for_gate(job, timeout_s=WAIT_S)
        result = rig.daemon.wait_for_finish(job, timeout_s=WAIT_S)
    assert result.error is not None and why in result.error
    assert person.prompts == [] and poses.asked_after == []


def test_recorded_answers_answer_a_plate_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def none(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"questions": []}

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": [{"question": "purpose", "choice": 2},
                                               {"question": "seen", "choice": 1}]}))
    person, poses = Person(), Poses()
    poses.person = person
    monkeypatch.setattr(graph, "propose_orientation", poses)
    rig = Rig(tmp_path, asking=replay_asker(answers), waiting=person.waiting(replayed=True),
              questions=none, llm=LLM)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        assert rig.press("approve", job).printed
    row = rig.ledger.rows()[0]
    assert row.intake is not None and row.intake.answers[0].choice == 1
    assert len(poses.asked_after) == 3


# An answer is of the parts as they lay when it was asked (print-shop-ux's probe)
# --------------------------------------------------------------------------


def test_what_was_said_seen_is_carried_through_a_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    """The probe: 'the top; the walls are hidden', of parts then turned 180. The top said is
    the face on the plate now (mqucifer/print-shop#248)."""
    person = Person("1", "3", "go")
    poses = Poses(lambda c: c.rotation_deg == (180.0, 0.0, 0.0))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path), as_laid=False)
        assert rig.press("approve", job).printed
    said = " ".join(rig.shown[0].split())
    assert ("part 1, cap-tight.stl: Seen as it now prints: the face on the plate." in said)
    assert "You said the top, as the file lays it; it was turned 180,0,0 since" in said
    assert "TURNED SINCE" not in said, "nothing else was set aside"
    [request] = rig.reviews
    assert not [f.id for f in request.flags if f.change is not None and f.change.feature == "seen"]
    row = rig.ledger.rows()[0]
    assert row.intake is not None
    assert [a.choice for a in row.intake.answers] == [0, 2], "the record is as given"
    assert row.intake.questions[1].pose == (0.0, 0.0, 0.0)
    assert row.proposal is not None and "Seen as it now prints" in row.proposal.notes[0]


def test_the_settings_call_isnt_told_what_no_longer_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    from print_shop.setup import model

    told: list[str] = []
    real = model.prompt

    def prompt(facts: Any, skill: Any) -> str:
        told.append(real(facts, skill))
        return told[-1]

    monkeypatch.setattr(model, "prompt", prompt)
    person = Person("1", "3", "go")
    poses = Poses(lambda c: c.rotation_deg == (180.0, 0.0, 0.0))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    assert "decoration or display" in told[-1], "what it's for holds in any pose"
    assert "the walls are hidden" not in told[-1], "the raw answer isn't given"
    assert "carried from what was said: the face on the plate" in told[-1]


def test_unturned_the_answer_is_read_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    person, poses = Person("1", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    assert "TURNED SINCE" not in rig.shown[0]
    [request] = rig.reviews
    assert "seam_seen" in [f.id for f in request.flags], "the walls were said seen"


def test_no_supports_isnt_carried_onto_a_new_overhang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Answered of a part lying on its slab. Turned onto its stalk, the guard decides again."""
    from print_shop.ingest.orientation import write_rotated
    from tests.fixtures import write_stl
    from tests.plates import profiles

    slab = write_rotated(write_stl("tee", tmp_path), (180.0, 0.0, 0.0), tmp_path / "slab.stl")
    option = {"impact": "made up", "why": ""}

    def questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [{"feature": "overhang", "text": "Support the made-up ledge?",
                               "options": [
                                   {**option, "label": "no supports", "recommended": True,
                                    "settings": [{"key": "enable_support", "value": "0"}]},
                                   {**option, "label": "trees", "recommended": False,
                                    "settings": [{"key": "enable_support", "value": "1"}]},
                               ]}]}

    person = Person("1", "1", "1")
    monkeypatch.setattr(graph, "propose_orientation", Poses())
    rig = Rig(tmp_path, "orientation", "1", "1", "the slab's top is the face", "ok",
              asking=person.asker(), waiting=person.waiting(), questions=questions, llm=LLM,
              use=profiles(tmp_path / "profiles", {"enable_support": "0",
                                                   "support_type": "normal(auto)"}))
    with rig.daemon:
        job = rig.gated([slab, _caps(tmp_path)[0]], as_laid=True)
        assert rig.press("approve", job).printed
    first, second = rig.reviews
    assert "enable_support" in {c.key for c in first.proposal.kept}, "answered, so it stood"
    supports = second.proposal.get("enable_support")
    assert supports is not None
    assert (supports.value, supports.source) == ("1", "guard"), "the new overhang decides again"
    assert "'Support the made-up ledge?', you said 'no supports'" in " ".join(
        screen(rig.shown).split())


def test_a_purpose_choices_setting_for_a_pose_isnt_carried_either(tmp_path: Path) -> None:
    from print_shop.core.state import AppliedOrientation, MeshReport, PlatePart
    from print_shop.orchestrator.plate_job import still_holds
    from print_shop.setup.intake import Answer, Intake, Option, Question

    closer = Question(feature="purpose", text="A cover or a spacer?", options=(
        Option(label="a cover", impact="made up",
               settings={"enable_support": "0", "wall_loops": "3"}),
        Option(label="a spacer", impact="made up", settings={}),
    ))
    intake = Intake(questions=(closer,), answers=(Answer(choice=0),))
    mesh = MeshReport(is_manifold=True, volume_mm3=1, surface_area_mm2=1, bbox_mm=(1, 1, 1),
                      overhang_area_mm2=0)
    part = PlatePart(n=1, name="cap.stl", given="cap.stl", stl="cap.stl", sha256="0", mesh=mesh,
                     applied=AppliedOrientation(rotation_deg=(180.0, 0.0, 0.0)))
    judged, note = still_holds(intake, (part,), ((0.0, 0.0, 0.0),))
    assert judged.questions[0].options[0].settings == {"wall_loops": "3"}
    assert "enable_support=0, from 'a cover'" in note
    assert intake.questions[0].options[0].settings["enable_support"] == "0", "the record is whole"
    assert still_holds(intake, (part,), ((180.0, 0.0, 0.0),)) == (intake, ""), "not turned since"


def test_sent_back_to_the_pose_asked_of_the_answer_holds_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    person, poses = Person("1", "1", "go"), Poses(
        lambda c: c.rotation_deg == ((180.0, 0.0, 0.0) if c.height_mm < 5 else (0.0, 0.0, 0.0)))
    rig = _rig(tmp_path, person, poses, monkeypatch, never,
               "orientation", "1", "1", "as it was", "ok")
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    assert "Seen as it now prints" in rig.shown[0]
    assert "Seen as it now prints" not in screen(rig.shown)
    assert "every part: Seen as it prints: the walls and the top" in screen(rig.shown)
    assert "seam_seen" in [f.id for f in rig.reviews[-1].flags]


def test_a_plates_questions_are_worded_for_its_parts_and_say_how_they_lie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    person, poses = Person("1", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never, several=True)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    assert ("A few questions about these parts, top and bottom as each part lies in its file:"
            in person.text)
    assert "What are these parts for? Their shapes can't say." in person.text
    assert "Which faces of these parts will be seen once they're in use?" in person.text
    assert "the top as it lies now" in person.text and "as it prints" not in person.text
    assert "this part" not in person.text and "reading the part" not in person.text
    row = rig.ledger.rows()
    assert row == [], "rejected"


def test_the_questions_say_a_pose_that_isnt_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    lying = [box(tmp_path / f"panel-{n}.stl", 240, 130, 5) for n in (1, 2)]
    person, poses = Person("1", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(lying, as_laid=False))
    assert "top and bottom as each lies now (part 1 turned " in person.text


def test_the_persons_own_words_are_quoted_as_typed() -> None:
    from print_shop.core.state import AppliedOrientation, MeshReport, PlatePart
    from print_shop.orchestrator.plate_job import seen_of
    from print_shop.setup.intake import Answer, Intake
    from print_shop.setup.questions import stock

    mesh = MeshReport(is_manifold=True, volume_mm3=1, surface_area_mm2=1, bbox_mm=(1, 1, 1),
                      overhang_area_mm2=0)
    part = PlatePart(n=1, name="cap.stl", given="cap.stl", stl="cap.stl", sha256="0", mesh=mesh,
                     applied=AppliedOrientation(rotation_deg=(180.0, 0.0, 0.0)))
    seen_q = stock(None, (0.0, 0.0, 0.0))[1]
    intake = Intake(questions=(seen_q,), answers=(Answer(other="the side as it prints"),))
    faces, lines = seen_of(intake, (part,), ((0.0, 0.0, 0.0),))
    assert faces is None, "words name no face, so none is judged"
    assert "TURNED SINCE YOU ANSWERED: you said 'the side as it prints'" in lines[0]


def test_walls_turned_onto_a_side_are_part_of_the_walls_and_the_seam_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    """'The walls and the top', of cubes then turned onto a side: the top said is a wall now, so
    only part of the walls is seen, and no seam change is proposed (mqucifer/print-shop#248)."""
    cubes = [box(tmp_path / "cubes" / f"cube-{n}.stl", 20, 20, 20) for n in (1, 2)]
    person = Person("1", "1", "go")
    poses = Poses(lambda c: c.rotation_deg == (0.0, 90.0, 0.0))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(cubes, as_laid=False))
    [request] = rig.reviews
    assert [p.orientation.rotation_deg for p in request.parts] == [(0.0, 90.0, 0.0)] * 2
    said = " ".join(rig.shown[0].split())
    assert "Seen as it now prints: part of the walls" in said
    seams = [f for f in request.flags if f.id == "seam_seen"]
    assert all(f.change is None for f in seams), "no seam change on partly seen walls"


@pytest.mark.parametrize(
    ("as_laid", "on_a_side"),
    [(True, 0), (False, 3), (False, 1), (False, 0)],
    ids=["as laid", "each on its context's side", "one on its context's side", "chosen after"],
)
def test_every_part_of_a_plate_has_all_six_poses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
    as_laid: bool, on_a_side: int,
) -> None:
    """A kept pose is measured alone for one part (mqucifer/print-shop#235). A plate's part
    needs all six however its pose is kept: its send-back, its fit and print-again read them."""
    caps = _caps(tmp_path)
    for cap in caps[:on_a_side]:
        (cap.parent / f"{cap.stem}.part.json").write_text('{"down": "+z"}')
    person, poses = Person("1", "1", "go"), Poses()
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(caps, as_laid=as_laid))
    parts = rig.reviews[0].parts
    assert len(parts) == len(caps)
    for n, part in enumerate(parts):
        assert [c.rotation_deg for c in part.candidates] == list(AXIS_ALIGNED), part
        if n < on_a_side:
            assert part.orientation.rotation_deg == (180.0, 0.0, 0.0), "its own context was read"


def test_a_plate_with_one_part_turned_and_one_not_reads_each_from_its_own_pose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    """mqucifer/print-shop#248's Done when, for a plate: 'the top; the walls are hidden', and only
    the flattest part flipped after. Its top is on the plate now, the others' still up."""
    person = Person("1", "3", "go")
    poses = Poses(lambda c: c.rotation_deg == ((180.0, 0.0, 0.0) if c.height_mm < 5
                                               else (0.0, 0.0, 0.0)))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    [request] = rig.reviews
    assert [p.orientation.rotation_deg for p in request.parts] == [
        (180.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]
    said = " ".join(rig.shown[0].split())
    assert "part 1, cap-tight.stl: Seen as it now prints: the face on the plate." in said
    assert "part 2, cap-mid.stl: Seen as it prints: the top" in said
    assert "part 3, cap-loose.stl: Seen as it prints: the top" in said
    assert "ironing_hidden" not in [f.id for f in request.flags], "a top is seen on the plate"


def test_the_plates_flags_read_the_faces_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    """'The walls; the top is hidden', of caps flipped after: the walls are still seen all
    round, so the seam is read as on them, from the faces carried and not the answer set aside."""
    person = Person("1", "2", "go")
    poses = Poses(lambda c: c.rotation_deg == (180.0, 0.0, 0.0))
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        rig.press("reject", rig.gated(_caps(tmp_path), as_laid=False))
    [request] = rig.reviews
    assert [f.change.value for f in request.flags if f.id == "seam_seen" and f.change] == ["back"]


def test_a_plates_added_questions_drop_what_was_typed_ahead_and_take_no_go() -> None:
    from print_shop.setup.intake import Added, OfPlate, cli_asker

    forgot: list[str] = []
    written: list[str] = []
    typed = iter(["go", "1"])
    asked = OfPlate(Added((SEEN,), 1), "top and bottom as each part lies in its file", PLATE_WORDS)
    ask = cli_asker(read=lambda _: next(typed), write=written.append,
                    forget=lambda: forgot.append("dropped"))
    ask(asked)
    assert forgot, "what was typed while it drew is no answer to it"
    assert not any("go works once these are answered" in w for w in written)


def test_a_plates_questions_show_only_those_asked_numbered_as_among_all() -> None:
    from print_shop.setup.intake import OfPlate, render

    shown = render(OfPlate(STOCK, "top and bottom as each part lies in its file", PLATE_WORDS), [1])
    assert "2. Which faces of these parts" in shown
    assert "What are these parts for?" not in shown
