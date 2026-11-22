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
from print_shop.orchestrator import graph
from print_shop.runtime import describe_all
from print_shop.setup.intake import replay_asker
from print_shop.setup.questions import PURPOSE, SEEN, STOCK
from tests.plates import box
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
    assert PURPOSE.text in person.text and SEEN.text in person.text
    assert "Type go and Enter to go on without waiting" in person.text
    said = " ".join(rig.shown[0].split())
    assert "note: you went on without the model's questions after" in said
    rows = rig.ledger.job_rows(job)
    assert [r.orientation_source for r in rows] == ["model:made-up"] * 3
    for row in rows:
        assert row.intake is not None and row.intake.questions == STOCK
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
    assert "(180.0, 0.0, 0.0), the made-up model chose it" in rig.shown[0]


def test_poses_chosen_that_dont_fit_keep_the_questions_poses_and_the_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, never: Callable[..., Any],
) -> None:
    standing = [box(tmp_path / f"panel-{n}.stl", 240, 5, 130) for n in (1, 2)]
    person, poses = Person("2", "1", "go"), Poses(lambda c: c.height_mm < 6)
    rig = _rig(tmp_path, person, poses, monkeypatch, never)
    with rig.daemon:
        job = rig.gated(standing, as_laid=False)
        assert rig.press("approve", job).printed
    assert "the poses chosen don't fit one plate together" in rig.shown[0]
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
