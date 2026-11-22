"""An answer about which faces are seen follows the part when it's turned.

mqucifer/print-shop#248. Every part, note and answer is made up.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.desk.again import answers_file
from print_shop.ingest.orientation import AXIS_ALIGNED, LAID, _rotation_matrix
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.context import SIDECAR, Seen, own_sides, pose_for, read_context, seen_from
from print_shop.setup.features import measure
from print_shop.setup.intake import (
    Answer,
    AnswersRefused,
    Intake,
    cli_asker,
    render,
    replay_asker,
)
from print_shop.setup.loop import PrintLoop
from print_shop.setup.offer import ThisPart, offering
from print_shop.setup.proposal import ORIENTATION
from print_shop.setup.questions import STOCK, stock
from print_shop.setup.reading import carried, flags, seen, seen_across, seen_said
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer
from print_shop.setup.review import render as render_review
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_print_loop import Model
from tests.test_reading import FILAMENT, PETG, PROCESS, _read
from tests.test_send_back import AnyPart
from tests.test_use_these import printed as printed_before
from tests.test_use_these import yours

WAIT_S = 30.0
FLIPPED = (180.0, 0.0, 0.0)
ON_ITS_SIDE = (90.0, 0.0, 0.0)
EVERY = list(itertools.product([False, True], repeat=3))
#: Each side's own direction in the file.
NORMALS = {f"{sign}{axis}": (1 if sign == "+" else -1) * np.eye(3)["xyz".index(axis)]
           for sign in "+-" for axis in "xyz"}


def faces(walls: bool, top: bool, bottom: bool) -> Seen:
    return Seen(walls=walls, top=top, bottom=bottom, said="made up")


def lying_as(side: str, pose: tuple[float, float, float]) -> str:
    """What a side of the part is in that pose, by turning its normal as the part is turned."""
    z = float((_rotation_matrix(pose)[:3, :3] @ NORMALS[side])[2])
    return "top" if z > 0.99 else "bottom" if z < -0.99 else "walls"


@pytest.mark.parametrize("named", EVERY)
@pytest.mark.parametrize("answered_in", AXIS_ALIGNED)
@pytest.mark.parametrize("printed_in", AXIS_ALIGNED)
def test_every_answer_is_carried_through_every_turn(
    named: tuple[bool, bool, bool],
    answered_in: tuple[float, float, float],
    printed_in: tuple[float, float, float],
) -> None:
    said = faces(*named)
    sides = own_sides(said, answered_in)
    assert sides is not None
    meant = {side for side in NORMALS if getattr(said, lying_as(side, answered_in))}
    assert set(sides) == meant, "the part's own sides"
    now = seen_from(sides, printed_in, "made up")
    assert now is not None
    for face in ("walls", "top", "bottom"):
        wanted = any(lying_as(side, printed_in) == face for side in meant)
        assert getattr(now, face) == wanted, face
    walls = [side in meant for side in NORMALS if lying_as(side, printed_in) == "walls"]
    assert now.part_walls == (any(walls) and not all(walls))


def test_a_top_turned_over_is_the_plates_face() -> None:
    sides = own_sides(faces(False, True, False), LAID)
    assert sides == ("+z",)
    now = seen_from(sides, FLIPPED, "made up")
    assert now is not None and (now.walls, now.top, now.bottom) == (False, False, True)


def test_a_top_turned_onto_its_side_is_part_of_the_walls() -> None:
    now = seen_from(("+z",), ON_ITS_SIDE, "made up")
    assert now is not None and now.part_walls and now.in_words() == "part of the walls"


def test_a_pose_that_isnt_square_on_carries_none() -> None:
    assert own_sides(faces(False, True, False), (45.0, 0.0, 0.0)) is None
    assert seen_from(("+z",), (45.0, 0.0, 0.0), "made up") is None


# --------------------------------------------------------------------------
# Through a job, by each way a part is turned
# --------------------------------------------------------------------------


class Turned:
    """A job through the daemon on fakes, and what each slice's flags and each review had."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.ledger = Ledger(tmp_path / "ledger.sqlite")
        self.given: list[tuple[Seen | None, Any]] = []
        self.requests: list[ReviewRequest] = []
        self.settings_prompts: list[str] = []
        self.shown: list[str] = []

        def watched(reading: Any, faces: Any, *rest: Any) -> Any:
            self.given.append((faces, rest[2]))
            return flags(reading, faces, *rest)

        monkeypatch.setattr(graph, "flags", watched)

    def settings(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.settings_prompts.append(user)
        return {"reasoning": "", "changes": [], "design_notes": []}

    def run(
        self, stl: Path, answers: list[str], review: list[str],
        loop: PrintLoop | None = None, as_laid: bool = False,
    ) -> Row:
        typed, at_review = list(answers), list(review)
        person = cli_reviewer(lambda prompt: at_review.pop(0), self.shown.append)

        def reviewer(request: ReviewRequest) -> Review:
            self.requests.append(request)
            return person(request)

        (self.tmp_path / "work").mkdir(exist_ok=True)
        bridge = MemoryBridge()
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=self.tmp_path / "work",
            printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
            ledger=self.ledger,
            proposer=Proposer(ask=self.settings, ask_questions=lambda *_: {"questions": []}),
            asker=cli_asker(lambda prompt: typed.pop(0), self.shown.append, lambda: None),
            reviewer=reviewer, loop=loop,
        )
        with daemon:
            job_id = daemon.submit(stl, as_laid=as_laid)
            assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            bridge.press(CFG, "approve")
            finished = daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
            assert finished.printed, finished.error
        row = self.ledger.get(job_id)
        assert row is not None and row.intake is not None
        return row

    @property
    def read_as(self) -> Seen:
        """What the last slice's flags read as seen."""
        faces, placed = self.given[-1]
        found = faces or (placed.seen if placed is not None else None)
        assert found is not None
        return found

    @property
    def last_review(self) -> str:
        return render_review(self.requests[-1])


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Turned]:
    made = Turned(tmp_path, monkeypatch)
    yield made
    made.ledger.close()


#: The questions' answers: purpose, then "the top as it prints; the walls are hidden".
TOP_SEEN = ["2", "3"]
PLATE = (False, False, True)


def three(seen_as: Seen) -> tuple[bool, bool, bool]:
    return (seen_as.walls, seen_as.top, seen_as.bottom)


def sent_to(pose: tuple[float, float, float]) -> list[str]:
    """What's typed at the review to send the file's own pose to this one, and go on."""
    others = [each for each in AXIS_ALIGNED if each != LAID]
    return ["orientation", str(others.index(pose) + 1), "a made-up reason", "ok"]


def test_a_part_flipped_after_the_top_is_seen_reads_the_plates_face_at_the_review_and_flags(
    tmp_path: Path, job: Turned
) -> None:
    row = job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(FLIPPED), as_laid=True)
    assert row.rotation_deg == FLIPPED
    assert three(job.given[0][0] or job.read_as) == (False, True, False), "before the turn"
    assert three(job.read_as) == PLATE
    assert ("note: Seen as it now prints: the plate face. You said the top, as the file lays it; "
            "it was turned 180,0,0 since") in job.last_review

    faces, placed = job.given[-1]
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    [hidden] = [f for f in flags(ironed, faces, PROCESS, FILAMENT, placed)
                if f.id == "ironing_hidden"]
    assert hidden.change is not None
    assert hidden.change.reason.startswith("you said 'the top, as the file lays it'")


def test_a_quarter_turn_onto_a_side_is_carried_as_part_of_the_walls(
    tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(ON_ITS_SIDE), as_laid=True)
    assert three(job.read_as) == (True, False, False) and job.read_as.part_walls
    assert "Seen as it now prints: part of the walls. You said the top" in job.last_review


def test_walls_turned_onto_a_side_are_the_top_and_the_plates_face_too(
    tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), ["2", "2"], sent_to(ON_ITS_SIDE), as_laid=True)
    assert three(job.read_as) == (True, True, True) and job.read_as.part_walls


def test_the_settings_call_is_told_the_faces_as_they_now_lie(
    tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(FLIPPED), as_laid=True)
    first, after = job.settings_prompts
    assert "Seen as it lies here, carried from what was said: the top." in first
    assert "Seen as it lies here, carried from what was said: the plate face." in after
    assert "the top as it prints;" not in after, "nothing calls it the top as it now prints"
    assert "as the file lays it? -> the top as it lay when asked" in after


def test_the_print_loops_turn_is_carried_and_each_pose_says_whats_seen_there(
    tmp_path: Path, job: Turned
) -> None:
    model = Model(
        [("ask", {"question": "Turn it over?", "reason": "no supports that way",
                  "rotation": [180, 0, 0]})],
        [("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "slab down"})],
    )
    loop = PrintLoop(chat=model, person=lambda question: "yes")
    row = job.run(write_stl("tee", tmp_path), TOP_SEEN, ["ok"], loop)
    assert row.rotation_deg == FLIPPED and row.orientation_source.startswith("loop:")
    assert three(job.read_as) == PLATE
    assert "Seen as it now prints: the plate face." in job.last_review
    weighed = model.shown[0][1]["content"]
    assert "seen there: the top\n" in weighed and "seen there: the plate face" in weighed


def test_the_loops_fallback_turn_is_carried(tmp_path: Path, job: Turned) -> None:
    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("no model here")

    loop = PrintLoop(chat=down, person=lambda question: "no")
    row = job.run(write_stl("tee", tmp_path), TOP_SEEN, ["ok"], loop)
    assert row.rotation_deg == FLIPPED, "the single call turned it"
    assert three(job.read_as) == PLATE
    assert "carried from what was said: the plate face." in job.settings_prompts[-1]


def test_a_pose_used_from_a_past_print_carries_it(tmp_path: Path, job: Turned) -> None:
    stl = write_stl("cube", tmp_path)
    printed_before(job.ledger, "past-job", stl,
                   (yours(ORIENTATION, "180,0,0", "it prints cleaner so"),), rotation=FLIPPED)
    row = job.run(stl, TOP_SEEN, ["use 1", "ok", "ok"])
    assert row.rotation_deg == FLIPPED
    assert any(c.key == ORIENTATION and c.used is not None for c in row.corrections or ())
    assert three(job.read_as) == PLATE


def test_seen_from_the_notes_follows_a_turn_as_an_answer_does(
    tmp_path: Path, job: Turned
) -> None:
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"seen": ["top"]}))
    job.run(stl, ["", "2"], sent_to(FLIPPED), as_laid=True)
    assert three(job.read_as) == PLATE
    assert ("note: Seen as it now prints: the plate face. The notes said the top, as the file "
            "lays it, and you confirmed it; it was turned 180,0,0 since") in job.last_review
    assert "seen: top, as the part's context says (as it prints: the plate face)" in (
        job.last_review)


def test_an_answer_given_on_the_notes_side_is_carried_back_to_the_files_pose(
    tmp_path: Path, job: Turned
) -> None:
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"down": "+z"}))
    others = [each for each in AXIS_ALIGNED if each != FLIPPED]
    back = ["orientation", str(others.index(LAID) + 1), "a made-up reason", "ok"]
    job.run(stl, TOP_SEEN, back)
    assert "once it's in use, laid top-down, as its notes say?" in "\n".join(job.shown)
    assert three(job.read_as) == PLATE, "what was up on the notes' side is on the plate now"
    assert "it was turned back to how the file lays it since" in job.last_review


def test_turned_back_the_answer_is_as_it_was_given(tmp_path: Path, job: Turned) -> None:
    others = [each for each in AXIS_ALIGNED if each != FLIPPED]
    again = ["orientation", str(others.index(LAID) + 1), "back again", "ok"]
    job.run(write_stl("cube", tmp_path), TOP_SEEN, [*sent_to(FLIPPED)[:-1], *again],
            as_laid=True)
    assert three(job.read_as) == (False, True, False)
    assert "Seen as it prints: the top" in job.last_review


def test_words_turned_cant_be_carried_so_none_is_judged_and_it_says_so(
    tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), ["2", "o only the curved bit"], sent_to(FLIPPED),
            as_laid=True)
    faces, placed = job.given[-1]
    assert faces is None and (placed is None or placed.seen is None)
    assert ("TURNED SINCE YOU ANSWERED: you said 'only the curved bit', as the file lays it. "
            "That can't be carried to 180,0,0, so no face is judged seen") in job.last_review
    assert "judge none as seen or hidden" in job.settings_prompts[-1]


def test_a_turn_off_square_judges_none_and_says_so() -> None:
    questions = stock(None, LAID)
    intake = Intake(questions=questions, answers=(Answer(choice=1), Answer(choice=2)))
    off = (45.0, 0.0, 0.0)
    assert seen(intake, off) is None and carried(intake, None, off) is None
    assert seen_said(intake, None, off).startswith("TURNED SINCE YOU ANSWERED")


def test_the_record_keeps_the_answer_as_given_and_its_pose(
    tmp_path: Path, job: Turned
) -> None:
    row = job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(FLIPPED), as_laid=True)
    assert row.intake is not None and row.proposal is not None
    question, answer = row.intake.questions[1], row.intake.answers[1]
    assert question.options[2].label == "the top as it prints; the walls are hidden"
    assert answer == Answer(choice=2) and tuple(question.pose or ()) == LAID
    assert "as the file lays it? the top as it prints; the walls are hidden" in describe(row)
    assert any(note.startswith("Seen as it now prints") for note in row.proposal.notes)


# --------------------------------------------------------------------------
# A plate, an older row, the question, a replay and "Use these"
# --------------------------------------------------------------------------


def top_seen(pose: tuple[float, float, float] = LAID) -> Intake:
    return Intake(questions=stock(None, pose), answers=(Answer(choice=1), Answer(choice=2)))


def test_a_plate_with_one_part_turned_and_one_not_reads_both(tmp_path: Path) -> None:
    """At the function: a plate's job is mqucifer/print-shop#234's, which calls it."""
    intake = top_seen()
    turned = carried(intake, None, FLIPPED, asked_in=LAID)
    still = carried(intake, None, LAID, asked_in=LAID)
    assert turned is not None and three(turned) == PLATE
    assert still is not None and three(still) == (False, True, False)
    plate = seen_across([turned, still])
    assert plate is not None and three(plate) == (False, True, True)


def test_each_part_of_a_plate_is_read_from_the_pose_it_was_asked_in() -> None:
    intake = top_seen()
    laid_over = carried(intake, None, FLIPPED, asked_in=FLIPPED)
    assert laid_over is not None and three(laid_over) == (False, True, False)


def test_a_plate_where_one_parts_cant_be_carried_judges_none() -> None:
    intake = top_seen()
    lost = carried(intake, None, (45.0, 0.0, 0.0))
    assert seen_across([carried(intake, None, LAID), lost]) is None
    assert seen_across([]) is None


def test_carried_is_for_the_seen_faces_alone() -> None:
    assert carried.__doc__ is not None and "nothing else" in carried.__doc__
    intake = top_seen()
    before = intake.model_dump()
    carried(intake, None, FLIPPED)
    assert intake.model_dump() == before, "no other answer, nor any option's setting, is touched"


def test_an_older_row_reads_back_as_it_was() -> None:
    old = Intake(questions=STOCK, answers=(Answer(choice=1), Answer(choice=2)))
    for pose in AXIS_ALIGNED:
        read = seen(old, pose)
        assert read is not None and three(read) == (False, True, False)
        assert read.said == "the top as it prints; the walls are hidden" and not read.part_walls
    assert "as the file lays it" not in old.summary() and "when asked" not in old.summary()
    assert old.questions[1].as_answered(old.answers[1]) == (
        "the top as it prints; the walls are hidden")
    assert seen_said(old, None, FLIPPED) == "Seen as it prints: the top"


@pytest.mark.parametrize(("pose", "named"), [
    (LAID, "as the file lays it"), (FLIPPED, "turned 180,0,0"), (ON_ITS_SIDE, "turned 90,0,0"),
])
def test_the_question_names_the_pose_its_asked_in(
    pose: tuple[float, float, float], named: str
) -> None:
    assert f"once it's in use, {named}?" in render(stock(None, pose))


def test_a_read_back_on_a_side_names_the_pose_and_the_faces_there(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"seen": ["top"], "down": "-y"}))
    pose = pose_for("-y")
    assert pose is not None
    _, question = stock(read_context(stl), pose)
    assert question.read_back() == (
        "the top, as the file lays it (laid front-down, as its notes say, so part of the walls)")


def test_a_recorded_answer_about_faces_is_refused_in_another_pose(
    tmp_path: Path, job: Turned
) -> None:
    row = job.run(write_stl("cube", tmp_path), TOP_SEEN, ["ok"], as_laid=True)
    content = answers_file(row)
    assert content is not None and content["answers"][1]["pose"] == "0,0,0"
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(content))
    assert replay_asker(path)(stock(None, LAID)) == (Answer(choice=1), Answer(choice=2))
    with pytest.raises(AnswersRefused, match="those words name other faces"):
        replay_asker(path)(stock(None, FLIPPED))


def test_use_these_compares_whats_seen_as_each_printed(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    other = tmp_path / "plinth.stl"
    other.write_bytes(write_stl("tee", tmp_path).read_bytes())
    features = measure(stl, 400.0)
    flipped_top = top_seen()  # the top as the file lies, printed on the plate
    with Ledger(tmp_path / "made-up.sqlite") as ledger:
        printed_before(ledger, "past-job", other, (yours("brim_type", "auto_brim"),),
                       rotation=FLIPPED, intake=flipped_top)

        def why(faces: Seen) -> str:
            part = ThisPart(stl=stl, stl_name=stl.name, sha256="made-up", features=features,
                            rotation_deg=FLIPPED, height_mm=20.0, machine={},
                            process={"brim_type": "no_brim"}, filament={}, seen=faces)
            past = offering(ledger, part).past
            assert past is not None
            return " ".join(past.differs)

        assert "seen faces weren't these" not in why(faces(*PLATE))
        assert "seen faces weren't these" in why(faces(False, True, False))


def test_an_unreadable_purpose_quotes_each_load_so_one_can_be_copied(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"purpose": {"loaded": "lots"}}))
    [note] = read_context(stl).notes
    assert "'handled, clipped or lightly loaded'; 'carries a real load'" in note
