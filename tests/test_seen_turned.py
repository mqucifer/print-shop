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
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import AXIS_ALIGNED, LAID, _rotation_matrix
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup import offer as offer_module
from print_shop.setup.context import (
    SIDECAR,
    Fit,
    FitBand,
    PartContext,
    Placed,
    Seen,
    own_sides,
    pose_for,
    read_context,
    seen_from,
)
from print_shop.setup.features import measure
from print_shop.setup.intake import (
    SQUARE_ON,
    Answer,
    AnswersRefused,
    Intake,
    cli_asker,
    render,
    replay_asker,
)
from print_shop.setup.loop import PrintLoop
from print_shop.setup.model import prompt as settings_prompt
from print_shop.setup.offer import ThisPart, offering
from print_shop.setup.proposal import ORIENTATION, Change, Proposal, Source
from print_shop.setup.questions import STOCK, stock
from print_shop.setup.reading import (
    SeamReading,
    SliceReading,
    carried,
    flags,
    seen,
    seen_across,
    seen_said,
)
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer
from print_shop.setup.review import render as render_review
from print_shop.setup.skill import groups_for, load
from tests.fakes import CI_PROFILES, STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.reviewing import takes
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
            reviewer=takes(reviewer), loop=loop,
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
    assert ("note: Seen as it now prints: the face on the plate. The top wasn't asked about, as "
            "it was on the plate when you answered, so ironing isn't judged. You said the top, "
            "as the file lays it; it was turned 180,0,0 since") in job.last_review

    faces, placed = job.given[-1]
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    [ironing] = [f for f in flags(ironed, faces, PROCESS, FILAMENT, placed)
                 if f.id.startswith("ironing")]
    assert ironing.id == "ironing_unknown", "its top is no longer taken as seen, nor as hidden"


def test_a_flags_reason_follows_the_turn(tmp_path: Path) -> None:
    intake = Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=3)))
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    [hidden] = [f for f in flags(ironed, seen(intake, ON_ITS_SIDE), PROCESS, FILAMENT)
                if f.id == "ironing_hidden"]
    assert hidden.change is not None
    assert hidden.change.reason.startswith("you said the face on the plate, as the file lays it; "
                                           "as it prints that's part of the walls, so")


def test_a_quarter_turn_onto_a_side_is_carried_as_part_of_the_walls(
    tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(ON_ITS_SIDE), as_laid=True)
    assert three(job.read_as) == (True, False, False) and job.read_as.part_walls
    assert ("Seen as it now prints: part of the walls. One of the walls wasn't asked about, as "
            "it was on the plate when you answered, so it isn't judged hidden. You said the top"
            ) in job.last_review


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
    assert "Seen as it lies here, carried from what was said: the face on the plate." in after
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
    assert "Seen as it now prints: the face on the plate." in job.last_review
    weighed = model.shown[0][1]["content"]
    assert "seen there: the top\n" in weighed
    assert "seen there: part of the walls" in weighed
    assert "seen there: the face on the plate" in weighed
    assert "leave seam_position as it is there" in weighed
    assert "Seen as it lies here" not in weighed, "one pose's faces aren't given for all six"
    assert "go by the pose you propose" in weighed


def test_the_loops_fallback_turn_is_carried(tmp_path: Path, job: Turned) -> None:
    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("no model here")

    loop = PrintLoop(chat=down, person=lambda question: "no")
    row = job.run(write_stl("tee", tmp_path), TOP_SEEN, ["ok"], loop)
    assert row.rotation_deg == FLIPPED, "the single call turned it"
    assert three(job.read_as) == PLATE
    assert "carried from what was said: the face on the plate." in job.settings_prompts[-1]


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
    assert ("note: Seen as it now prints: the face on the plate. The notes said the top, as the "
            "file lays it, and you confirmed it; it was turned 180,0,0 since") in job.last_review
    assert "seen: top, as the part's context says (as it prints: the face on the plate)" in (
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
    said = "as the file lays it? the top as it lay when asked; the walls are hidden"
    assert said in describe(row)
    assert "Seen as it now prints: the face on the plate." in describe(row)
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


def test_the_reviews_line_reads_each_part_from_the_pose_it_was_asked_in() -> None:
    intake = top_seen()
    unmoved = seen_said(intake, None, FLIPPED, asked_in=FLIPPED)
    assert unmoved == "Seen as it prints: the top"
    back = seen_said(intake, None, LAID, asked_in=FLIPPED)
    assert back.startswith("Seen as it now prints: the face on the plate. ")
    assert "You said the top, turned 180,0,0;" in back
    assert "turned back to how the file lays it since" in back


@pytest.mark.parametrize("pose", [LAID, FLIPPED, ON_ITS_SIDE])
def test_words_never_fall_through_to_a_parts_own_notes(
    pose: tuple[float, float, float],
) -> None:
    """A plate's one question needn't have the notes' option, as its parts' notes differ."""
    notes = PartContext(source="made up", seen=Seen(walls=True, top=False, bottom=False,
                                                    said="walls", noted=True))
    intake = Intake(questions=stock(None, LAID),
                    answers=(Answer(choice=1), Answer(other="only the logo")))
    assert carried(intake, notes, pose) is None


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


def test_a_recorded_answer_about_faces_is_carried_from_the_pose_it_was_given_in(
    tmp_path: Path, job: Turned
) -> None:
    row = job.run(write_stl("cube", tmp_path), TOP_SEEN, ["ok"], as_laid=True)
    content = answers_file(row)
    assert content is not None and content["answers"][1]["pose"] == "0,0,0"
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(content))
    assert replay_asker(path)(stock(None, LAID)) == (Answer(choice=1), Answer(choice=2))
    questions = stock(None, FLIPPED)
    intake = Intake(questions=questions, answers=replay_asker(path)(questions))
    assert intake.answers[1].given_in == LAID
    now = carried(intake, None, FLIPPED)
    assert now is not None and three(now) == PLATE, "never read as the top it's asked of now"
    assert "answered in the pose 0,0,0" in intake.summary()


def test_again_of_an_older_row_prints_seen_as_it_printed_then(
    tmp_path: Path, job: Turned
) -> None:
    """It was read in the pose it printed in, so that's the pose its file gives."""
    row = job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(FLIPPED), as_laid=True)
    older = row.model_copy(update={"intake": Intake(
        questions=STOCK, answers=(Answer(choice=1), Answer(choice=2)))})
    content = answers_file(older)
    assert content is not None and content["answers"][1]["pose"] == "180,0,0"
    assert "pose" not in content["answers"][0]
    path = tmp_path / "older.json"
    path.write_text(json.dumps(content))
    questions = stock(None, LAID)
    intake = Intake(questions=questions, answers=replay_asker(path)(questions))
    now = carried(intake, None, FLIPPED)
    assert now is not None and three(now) == (False, True, False), "as that row was read"


@pytest.mark.parametrize("written", ["180.0,0.0,0.0", "-180,0,-0", " 180, 0, 0"])
def test_a_recorded_pose_is_read_as_angles_and_not_as_text(
    written: str, tmp_path: Path
) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [
        {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 3, "pose": written}]}))
    assert replay_asker(path)(stock(None, FLIPPED))[1] == Answer(choice=2)


def test_a_recorded_answer_with_no_pose_says_which_its_read_in(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [
        {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 3}]}))
    said: list[str] = []
    replay_asker(path, write=said.append)(stock(None, LAID))
    assert "names no pose" in said[0] and "as the file lays it" in said[0]


def test_beside_the_notes_faces_an_answer_from_another_pose_is_carried_and_never_theirs(
    tmp_path: Path,
) -> None:
    """The desk writes such files, so one is never refused: its words name other faces here, so
    it's read by its sides and set beside the notes', both named."""
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"seen": ["top"]}))
    context = read_context(stl)
    questions = stock(context, FLIPPED)
    assert questions[1].noted == 3
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [
        {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 4, "pose": "0,0,0"}]}))
    said: list[str] = []
    intake = Intake(questions=questions, answers=replay_asker(path, write=said.append)(questions))
    question, answer = questions[1], intake.answers[1]
    assert not question.agrees(answer) and [q for q, _ in intake.over_notes()] == [question]
    now = carried(intake, context, FLIPPED)
    assert now is not None and three(now) == (False, True, False)
    assert "answered in the pose 0,0,0" in said[0]


def test_an_answer_given_in_another_pose_says_so_wherever_its_told(tmp_path: Path) -> None:
    intake = Intake(questions=stock(None, LAID),
                    answers=(Answer(choice=1), Answer(choice=2, given_in=FLIPPED)))
    said = "the top as it lay when answered; the walls are hidden (answered in the pose 180,0,0)"
    assert intake.questions[1].as_answered(intake.answers[1]) == said
    assert said in intake.summary()
    stl = write_stl("cube", tmp_path)
    facts = Proposer().facts(intake, measure(stl, 400.0), STORE_PROFILES, pose=LAID)
    assert said in settings_prompt(facts, load(groups_for(intake, facts.features)))


def test_use_these_keeps_the_notes_difference_where_an_answer_isnt_known(
    tmp_path: Path,
) -> None:
    """As before: notes here and none there differ, so a past print without them doesn't rise."""
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"seen": ["top"]}))
    other = tmp_path / "plinth.stl"
    other.write_bytes(write_stl("tee", tmp_path).read_bytes())
    context = read_context(stl)
    with Ledger(tmp_path / "made-up.sqlite") as ledger:
        printed_before(ledger, "past-job", other, (yours("brim_type", "auto_brim"),))
        part = ThisPart(stl=stl, stl_name=stl.name, sha256="made-up",
                        features=measure(stl, 400.0), rotation_deg=LAID, height_mm=20.0,
                        machine={}, process={"brim_type": "no_brim"}, filament={},
                        context=context, seen_in=lambda pose: carried(None, context, pose))
        past = offering(ledger, part).past
        assert past is not None
        assert "seen faces weren't these" in " ".join(past.differs)
        assert "context 0" in past.why


def test_a_face_not_spoken_of_is_no_difference() -> None:
    flipped = seen(Intake(questions=stock(None, LAID),
                          answers=(Answer(choice=1), Answer(choice=0))), FLIPPED)
    assert flipped is not None
    assert flipped.same_as(faces(True, True, True)) and flipped.same_as(faces(True, False, True))
    assert not flipped.same_as(faces(False, True, True))


def test_a_wall_thats_seen_differs_from_walls_said_hidden_though_another_wasnt_spoken_of(
) -> None:
    here = seen(Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=0))),
                (0.0, 90.0, 0.0))
    then = seen(top_seen(), LAID)
    assert here is not None and then is not None
    assert here.walls and "walls" in here.unsaid and not then.walls
    assert not here.same_as(then) and not then.same_as(here)
    assert not here.same_as(faces(False, True, True))


@pytest.mark.parametrize("option", [0, 2, 3])
def test_part_of_the_walls_differs_from_all_round_unless_the_rest_wasnt_asked_about(
    option: int,
) -> None:
    now = seen(Intake(questions=stock(None, LAID),
                      answers=(Answer(choice=1), Answer(choice=option))), ON_ITS_SIDE)
    assert now is not None and now.part_walls
    all_round = faces(True, now.top, now.bottom)
    assert now.same_as(all_round) is (option == 0) and all_round.same_as(now) is (option == 0)


def past_seam(
    tmp_path: Path, value: str = "back", *,
    source: Source = Source.SPONSOR, another_file: bool = False,
    pose: tuple[float, float, float] = ON_ITS_SIDE, answered: Intake | None = None,
    result: str = "success", symptom: str = "none", here: Intake | None = None,
) -> tuple[list[str], list[str]]:
    """What's ticked of a past print's seam_position, and its doubts and reason, for a cube on
    its side whose seen top is now part of the walls."""
    stl = write_stl("cube", tmp_path)
    past = stl
    if another_file:
        past = tmp_path / "plinth.stl"
        past.write_bytes(write_stl("tee", tmp_path).read_bytes())
    change = Change(key="seam_position", preset="aligned", value=value, reason="a made-up reason",
                    source=source)
    typed = source is Source.SPONSOR
    with Ledger(tmp_path / f"made-up-{value}.sqlite") as ledger:
        printed_before(ledger, "past-job", past, (change,) if typed else (),
                       changes=() if typed else (change,), rotation=pose,
                       intake=answered or top_seen(), result=result, symptom=symptom)
        now = here or top_seen()
        part = ThisPart(stl=stl, stl_name=stl.name, sha256=analyze(str(stl)).stl_sha256,
                        features=measure(stl, 400.0), rotation_deg=ON_ITS_SIDE, height_mm=20.0,
                        machine={}, process={"seam_position": "nearest"}, filament={},
                        seen_in=lambda at: carried(now, None, at))
        found = offering(ledger, part)
    assert found.past is not None
    [item] = found.past.items
    return [i.key for i in found.ticked(Proposal(), "90,0,0")], [*item.doubts, item.fits]


@pytest.mark.parametrize("value", ["back", "aligned_back", "random", "aligned"])
def test_their_own_seam_on_the_same_file_pose_and_faces_is_ticked_saying_why(
    value: str, tmp_path: Path
) -> None:
    ticked, why = past_seam(tmp_path, value)
    assert ticked == ["seam_position"]
    assert why == ["you set it on this file, in this pose, with the same faces seen"]


def test_a_seam_from_another_file_isnt_ticked(tmp_path: Path) -> None:
    ticked, why = past_seam(tmp_path, another_file=True)
    assert ticked == [] and "part of the walls is seen here" in " ".join(why)


def test_a_seam_from_another_pose_isnt_ticked(tmp_path: Path) -> None:
    ticked, why = past_seam(tmp_path, pose=(270.0, 0.0, 0.0))
    assert ticked == [] and "part of the walls is seen here" in " ".join(why)


def test_a_seam_set_for_other_faces_isnt_ticked(tmp_path: Path) -> None:
    only_plate = Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=1)))
    ticked, why = past_seam(tmp_path, answered=only_plate)
    assert ticked == [] and "part of the walls is seen here" in " ".join(why)


def test_a_seam_set_for_another_of_the_walls_isnt_ticked(tmp_path: Path) -> None:
    """The same file and pose, part of the walls both times, and not the same wall."""
    other_wall = top_seen((0.0, 90.0, 0.0))
    then, now = seen(other_wall, ON_ITS_SIDE), seen(top_seen(), ON_ITS_SIDE)
    assert then is not None and now is not None and now.same_as(then)
    ticked, why = past_seam(tmp_path, answered=other_wall)
    assert ticked == [] and "part of the walls is seen here" in " ".join(why)


def test_a_seam_taken_from_the_slices_proposal_isnt_ticked(tmp_path: Path) -> None:
    ticked, why = past_seam(tmp_path, source=Source.READING)
    assert ticked == [] and "part of the walls is seen here" in " ".join(why)


def test_a_failed_prints_doubts_still_untick_their_own_seam(tmp_path: Path) -> None:
    ticked, why = past_seam(tmp_path, result="failed", symptom="other")
    assert ticked == [] and "that print failed" in " ".join(why)


@pytest.mark.parametrize("past_is_a_plate", [False, True])
def test_a_plates_combined_faces_never_make_a_seam_theirs(
    past_is_a_plate: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ux-109's pair: the neighbour turned another way, and both plates read the same."""

    def plate(neighbour: tuple[float, float, float]) -> Seen:
        found = seen_across([carried(top_seen(), None, ON_ITS_SIDE),
                             carried(top_seen(), None, neighbour)])
        assert found is not None and found.part_walls
        return found

    here, then = plate((0.0, 90.0, 0.0)), plate(ON_ITS_SIDE)
    assert here.same_as(then) and here.wall_sides == then.wall_sides, "nothing tells them apart"
    if not past_is_a_plate:
        then = then.model_copy(update={"of_several": False})
    monkeypatch.setattr(offer_module, "_seen_then", lambda row: then)
    stl = write_stl("cube", tmp_path)
    with Ledger(tmp_path / "made-up.sqlite") as ledger:
        printed_before(ledger, "past-job", stl, (yours("seam_position", "back"),),
                       rotation=ON_ITS_SIDE)
        part = ThisPart(stl=stl, stl_name=stl.name, sha256=analyze(str(stl)).stl_sha256,
                        features=measure(stl, 400.0), rotation_deg=ON_ITS_SIDE, height_mm=20.0,
                        machine={}, process={"seam_position": "nearest"}, filament={},
                        seen_in=lambda pose: here)
        assert offering(ledger, part).ticked(Proposal(), "90,0,0") == []


def test_where_the_walls_arent_partly_seen_a_past_seam_is_offered_as_before(
    tmp_path: Path,
) -> None:
    as_asked = top_seen(ON_ITS_SIDE)
    ticked, why = past_seam(tmp_path, answered=as_asked, here=as_asked)
    assert ticked == ["seam_position"] and why == [""]


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
                            process={"brim_type": "no_brim"}, filament={},
                            seen_in=lambda pose: faces)
            past = offering(ledger, part).past
            assert past is not None
            return " ".join(past.differs)

        assert "seen faces weren't these" not in why(faces(*PLATE))
        assert "seen faces weren't these" in why(faces(False, True, False))


def line_up_the_wall(tmp_path: Path, position: str = "aligned") -> SliceReading:
    """A square's slice with its seam a line."""
    return _read(tmp_path, layers=30, config=PETG | {"seam_position": position})


SIDEWAYS = [pose for pose in AXIS_ALIGNED if pose not in (LAID, (180.0, 0.0, 0.0))]


@pytest.mark.parametrize("option", [0, 1, 2, 3])
@pytest.mark.parametrize("pose", SIDEWAYS)
@pytest.mark.parametrize("position", ["aligned", "back", "aligned_back", "nearest", "random"])
def test_no_seam_change_is_proposed_where_only_part_of_the_walls_is_seen(
    option: int, pose: tuple[float, float, float], position: str, tmp_path: Path
) -> None:
    """It can't be told where the line is against that part, nor that scattering is better."""
    intake = Intake(questions=stock(None, LAID),
                    answers=(Answer(choice=1), Answer(choice=option)))
    faces_now = seen(intake, pose)
    assert faces_now is not None and faces_now.part_walls
    found = flags(line_up_the_wall(tmp_path, position), faces_now, PROCESS, FILAMENT)
    [seam] = [f for f in found if f.id == "seam_seen"]
    assert seam.change is None
    assert seam.reading.startswith(f"seam ({position}): a line up the wall at x ")
    assert "can't be told whether the line is on that part" in seam.reading
    assert ("seam_position=random to scatter it" in seam.reading) is (position != "random")


def test_a_line_in_a_us_inside_corner_is_left_where_it_is(tmp_path: Path) -> None:
    """The re-review's U: the line was on a hidden inside corner, and back put it on the seen
    wall's outer edge. Random would put it all over that wall."""
    u_line = line_up_the_wall(tmp_path).model_copy(update={
        "seam": SeamReading(seams=100, stacked=0.99, at=(4.8, 4.8), at_layers=100)})
    found = flags(u_line, seen(top_seen(), (0.0, 90.0, 0.0)), PROCESS, FILAMENT)
    [seam] = [f for f in found if f.id == "seam_seen"]
    assert seam.change is None and "at x 4.8 y 4.8" in seam.reading


@pytest.mark.parametrize("pose", [ON_ITS_SIDE, (270.0, 0.0, 0.0)])
def test_both_quarter_turns_show_the_line_at_the_review(
    pose: tuple[float, float, float], tmp_path: Path, job: Turned
) -> None:
    job.run(write_stl("cube", tmp_path), TOP_SEEN, sent_to(pose), as_laid=True)
    assert "note: seam (aligned): a line up the wall at x 138 y 138" in job.last_review
    assert "seam_position  aligned ->" not in job.last_review


@pytest.mark.parametrize("position", ["aligned", "back", "aligned_back"])
def test_with_a_fit_the_line_is_said_and_random_is_never_offered(
    position: str, tmp_path: Path
) -> None:
    band = FitBand(fit=Fit(name="made-up fit", play_min=0.1, play_max=0.1), z_from=2, z_to=4)
    found = flags(line_up_the_wall(tmp_path, position), seen(top_seen(), ON_ITS_SIDE), PROCESS,
                  FILAMENT, Placed(fits=(band,)))
    [seam] = [f for f in found if f.id == "seam_fit"]
    assert seam.change is None and "then place it by hand" in seam.reading
    assert "seam_position=random" not in seam.reading


def test_a_random_seam_on_a_fit_isnt_sent_back_where_part_of_the_walls_is_seen(
    tmp_path: Path,
) -> None:
    band = FitBand(fit=Fit(name="made-up fit", play_min=0.1, play_max=0.1), z_from=2, z_to=4)
    scattered = _read(tmp_path, layers=30, seam_stays=False,
                      config=PETG | {"seam_position": "random"})
    found = flags(scattered, seen(top_seen(), (270.0, 0.0, 0.0)), PROCESS, FILAMENT,
                  Placed(fits=(band,)))
    [seam] = [f for f in found if f.id == "seam_fit"]
    assert seam.change is not None and seam.change.value == "aligned"
    assert "placing by hand" in seam.change.reason


def test_walls_seen_all_round_still_go_back_as_before(tmp_path: Path) -> None:
    intake = Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=1)))
    found = flags(line_up_the_wall(tmp_path), seen(intake, FLIPPED), PROCESS, FILAMENT)
    [seam] = [f for f in found if f.id == "seam_seen"]
    assert seam.change is not None and seam.change.value == "back"


@pytest.mark.parametrize("option", [0, 1])
@pytest.mark.parametrize("turn", SIDEWAYS)
def test_a_plate_with_a_part_seen_all_round_has_no_seam_change_proposed(
    option: int, turn: tuple[float, float, float], tmp_path: Path
) -> None:
    intake = Intake(questions=stock(None, LAID),
                    answers=(Answer(choice=1), Answer(choice=option)))
    all_round, turned = carried(intake, None, LAID), carried(intake, None, turn)
    for pair in ([all_round, turned], [turned, all_round]):
        plate = seen_across(pair)
        assert plate is not None and plate.part_walls
        for position in ("aligned", "back"):
            found = flags(line_up_the_wall(tmp_path, position), plate, PROCESS, FILAMENT)
            [seam] = [f for f in found if f.id == "seam_seen"]
            assert seam.change is None


@pytest.mark.parametrize("pose", SIDEWAYS)
def test_the_settings_call_is_told_to_leave_the_seam(
    pose: tuple[float, float, float], tmp_path: Path
) -> None:
    stl = write_stl("cube", tmp_path)
    facts = Proposer().facts(top_seen(), measure(stl, 400.0), STORE_PROFILES, pose=pose)
    told = settings_prompt(facts, load(groups_for(top_seen(), facts.features)))
    assert "leave seam_position as it is there" in told
    assert "One of the walls wasn't asked about: don't judge that face hidden" in told


@pytest.mark.parametrize("option", [0, 1, 2])
@pytest.mark.parametrize("pose", AXIS_ALIGNED)
def test_three_answers_never_call_the_plates_face_hidden(
    option: int, pose: tuple[float, float, float], tmp_path: Path
) -> None:
    """None of them can say it's seen: only "only the face on the plate" speaks of it."""
    intake = Intake(questions=stock(None, LAID),
                    answers=(Answer(choice=1), Answer(choice=option)))
    now = seen(intake, pose)
    assert now is not None and lying_as("-z", pose) in now.unsaid
    if pose == FLIPPED:
        ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
        assert "ironing_hidden" not in {f.id for f in flags(ironed, now, PROCESS, FILAMENT)}


def test_only_the_plates_face_is_the_whole_list() -> None:
    intake = Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=3)))
    for pose in AXIS_ALIGNED:
        now = seen(intake, pose)
        assert now is not None and not now.unsaid


@pytest.mark.parametrize("written", [
    "nan,0,0", "1e999,0,0", "inf,0,0", "45,0,0", "0,0,90", "180,0", "flat",
])
def test_a_pose_no_part_prints_in_is_refused_before_anything_is_recorded(
    written: str, tmp_path: Path
) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [
        {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 3, "pose": written}]}))
    with pytest.raises(AnswersRefused, match="its pose"):
        replay_asker(path)(stock(None, LAID))


def test_the_poses_an_answer_takes_are_the_candidates() -> None:
    assert SQUARE_ON == AXIS_ALIGNED


def test_the_options_say_as_it_lies_now_and_the_label_is_unchanged(tmp_path: Path) -> None:
    questions = stock(None, LAID)
    shown = render(questions)
    assert "the top as it lies now; the walls are hidden" in shown
    assert "as it prints" not in shown
    assert questions[1].options[2].label == "the top as it prints; the walls are hidden"
    path = tmp_path / "as-shown.json"
    path.write_text(json.dumps({"answers": [
        {"question": "purpose", "choice": 2},
        {"question": "seen", "choice": "the top as it lies now; the walls are hidden"}]}))
    assert replay_asker(path)(questions)[1] == Answer(choice=2)


def test_an_answer_not_known_on_one_side_is_no_difference(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    other = tmp_path / "plinth.stl"
    other.write_bytes(write_stl("tee", tmp_path).read_bytes())
    with Ledger(tmp_path / "made-up.sqlite") as ledger:
        printed_before(ledger, "past-job", other, (yours("brim_type", "auto_brim"),))
        part = ThisPart(stl=stl, stl_name=stl.name, sha256="made-up",
                        features=measure(stl, 400.0), rotation_deg=LAID, height_mm=20.0,
                        machine={}, process={"brim_type": "no_brim"}, filament={},
                        seen_in=lambda pose: carried(top_seen(), None, pose))
        past = offering(ledger, part).past
        assert past is not None
        assert "seen faces weren't these" not in " ".join(past.differs)
        assert "context 100" in past.why


def test_the_recommended_answer_flipped_never_calls_the_top_hidden(
    tmp_path: Path, job: Turned
) -> None:
    """It's the answer for when it can't be said, and never named the face on the plate."""
    job.run(write_stl("cube", tmp_path), ["2", "1"], sent_to(FLIPPED), as_laid=True)
    assert job.read_as.top_unsaid and job.read_as.bottom
    assert ("note: Seen as it now prints: the walls and the face on the plate. The top wasn't "
            "asked about, as it was on the plate when you answered, so ironing isn't judged. "
            "You said the walls and the top, as the file lays it; it was turned 180,0,0 since"
            ) in job.last_review
    assert "don't judge that face hidden" in job.settings_prompts[-1]
    faces, placed = job.given[-1]
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    found = {f.id: f for f in flags(ironed, faces, PROCESS, FILAMENT, placed)}
    assert "ironing_hidden" not in found and found["ironing_unknown"].change is None


def test_an_answer_that_called_the_top_hidden_still_does_unturned(tmp_path: Path) -> None:
    intake = Intake(questions=stock(None, LAID), answers=(Answer(choice=1), Answer(choice=1)))
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    assert "ironing_hidden" in {f.id for f in flags(ironed, seen(intake, LAID), PROCESS, FILAMENT)}


def test_the_plates_face_is_told_as_the_one_it_was_when_asked() -> None:
    questions = stock(None, LAID)
    assert questions[1].as_answered(Answer(choice=3)) == "only the face on the plate when asked"


def test_several_parts_reasons_say_whose_each_is(tmp_path: Path) -> None:
    notes = PartContext(source="made up", seen=Seen(walls=False, top=True, bottom=False,
                                                    said="top, as the part's context says",
                                                    noted=True))
    both = seen_across([carried(top_seen(), None, LAID), carried(None, notes, LAID)])
    assert both is not None
    assert both.because() == ("you said 'the top as it prints; the walls are hidden'; the part's "
                              "notes say the top, as the file lays it")


def test_an_unreadable_purpose_quotes_each_load_so_one_can_be_copied(tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps({"purpose": {"loaded": "lots"}}))
    [note] = read_context(stl).notes
    assert "'handled, clipped or lightly loaded'; 'carries a real load'" in note
