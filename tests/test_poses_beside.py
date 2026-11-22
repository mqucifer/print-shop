"""A kept pose's job doesn't wait for the five poses it won't print (mqucifer/print-shop#235).

The part is made up. The slicer, the models, the printer and the broker are fakes. The other
five are held back by a lock the test lets go of, so what waited for them is shown by what ran
before it was let go, and not by a clock.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from print_shop.expert.orientation import propose_geometric
from print_shop.ingest import measuring as measuring_module
from print_shop.ingest.measuring import Measuring
from print_shop.ingest.orientation import AXIS_ALIGNED, LAID, candidate_in, candidates, scored
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.intake import Answer, Question
from print_shop.setup.loop import PrintLoop
from print_shop.setup.proposal import ORIENTATION, pose_value
from print_shop.setup.review import Review, Reviewer, ReviewRequest, cli_reviewer, render
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.plate_235 import write_plate
from tests.test_job_e2e import CFG
from tests.test_review import _model, _questions
from tests.test_send_back import AnyPart
from tests.test_use_these import printed, yours

#: From the job's start to its questions: the issue's bar.
TO_QUESTIONS_S = 5.0
WAIT_S = 30.0
#: Longer than a job is waited for, so one that waits on held poses fails its test.
HELD_S = 4 * WAIT_S
FLIPPED = (180.0, 0.0, 0.0)
OTHERS = AXIS_ALIGNED[1:]


class Held:
    """The other poses' measuring, held until let go."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.let_go = threading.Event()
        self.began: list[tuple[float, float, float]] = []
        self.failing: set[tuple[float, float, float]] = set()
        self.first_go: set[tuple[float, float, float]] = set()
        def held(mesh: Any, rotation: tuple[float, float, float]) -> Any:
            self.began.append(rotation)
            if rotation not in self.first_go and not self.let_go.wait(HELD_S):
                raise AssertionError("never let go: something waited for the other poses")
            if rotation in self.failing:
                raise ValueError("a made-up bad pose")
            return scored(mesh, rotation)

        monkeypatch.setattr(measuring_module, "scored", held)


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> Iterator[Held]:
    found = Held(monkeypatch)
    yield found
    found.let_go.set()


@pytest.fixture
def one_pose_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the orient node measured all six")

    monkeypatch.setattr(graph, "candidates", never)


@pytest.fixture
def let_go_when_said(held: Held) -> Iterator[Callable[[str], list[str]]]:
    """Lets the held poses go when the job says it's waiting for them, and keeps what it said."""
    logger = logging.getLogger(graph.__name__)
    level = logger.level
    said: list[str] = []
    why: list[str] = []

    class Heard(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if why and why[0] in record.getMessage():
                said.append(record.getMessage())
                held.let_go.set()

    heard = Heard()
    logger.addHandler(heard)
    logger.setLevel(logging.INFO)

    def when(words: str) -> list[str]:
        why.append(words)
        return said

    yield when
    logger.removeHandler(heard)
    logger.setLevel(level)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "ledger.sqlite") as made:
        yield made


class Job:
    """A job through the daemon, and when its questions were asked."""

    def __init__(self, tmp_path: Path, ledger: Ledger) -> None:
        self.tmp_path = tmp_path
        self.ledger = ledger
        self.asked_after: float | None = None
        self.at_questions: Callable[[], None] = lambda: None
        self.error: str = ""

    def run(
        self, stl: Path, reviewer: Reviewer,
        loop: PrintLoop | None = None, **submit: Any,
    ) -> Row | None:
        began = 0.0

        def asker(questions: Sequence[Question]) -> tuple[Answer, ...]:
            if self.asked_after is None:
                self.asked_after = time.monotonic() - began
                self.at_questions()
            return tuple(Answer(choice=0) for _ in questions)

        (self.tmp_path / "work").mkdir(exist_ok=True)
        bridge = MemoryBridge()
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=self.tmp_path / "work",
            printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
            ledger=self.ledger, proposer=Proposer(ask=_model, ask_questions=_questions),
            asker=asker, reviewer=reviewer, loop=loop,
        )
        with daemon:
            began = time.monotonic()
            job_id = daemon.submit(stl, **submit)
            daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
            if daemon.pending_gate() is not None:
                bridge.press(CFG, "approve")
            self.error = daemon.wait_for_finish(job_id, timeout_s=WAIT_S).error or ""
        return self.ledger.get(job_id)


@pytest.fixture
def job(tmp_path: Path, ledger: Ledger) -> Job:
    return Job(tmp_path, ledger)


class Person:
    """Types from a script. Each line shown can let the held poses go."""

    def __init__(self, held: Held, *answers: str, go_at: str = "Waiting for") -> None:
        self.held = held
        self.answers = list(answers)
        self.go_at = go_at
        self.shown: list[str] = []

    def write(self, text: str) -> None:
        self.shown.append(text)
        if text.startswith(self.go_at):
            self.held.let_go.set()

    def read(self, prompt: str) -> str:
        assert self.answers, f"asked more than was scripted, at {prompt!r}"
        return self.answers.pop(0)

    @property
    def said(self) -> str:
        return "\n".join(self.shown)

    def reviewer(self) -> Reviewer:
        return cli_reviewer(self.read, self.write)


def approves(request: ReviewRequest) -> Review:
    return Review(approved=True)


def down(tmp_path: Path, part: str, side: str) -> Path:
    stl = write_stl(part, tmp_path)
    (tmp_path / f"{part}.part.json").write_text(json.dumps({"down": side}))
    return stl


# The questions don't wait
# --------------------------------------------------------------------------


def test_as_laid_reaches_the_questions_in_under_five_seconds_without_the_other_five(
    tmp_path: Path, job: Job, held: Held, one_pose_only: None
) -> None:
    row = job.run(write_plate(tmp_path), approves, as_laid=True)
    assert row is not None, job.error
    assert not held.let_go.is_set(), "nothing in the job waited for the other five"
    assert job.asked_after is not None
    assert job.asked_after < TO_QUESTIONS_S, f"{job.asked_after:.1f} s to the questions"
    assert (row.rotation_deg, row.orientation_source) == (LAID, "as_laid")


def test_the_five_begin_only_once_what_the_questions_need_is_measured(
    tmp_path: Path, job: Job, held: Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured: list[int] = []

    def counted(*args: Any) -> Any:
        measured.append(len(held.began))
        return measure(*args)

    monkeypatch.setattr(graph, "measure", counted)
    # Kept off the file's own pose, so it's measured in both before the questions.
    assert job.run(down(tmp_path, "tee", "+z"), approves) is not None, job.error
    assert measured[:2] == [0, 0], "they never slow the measuring the questions wait for"


def test_a_part_in_the_files_own_pose_is_measured_once(
    tmp_path: Path, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: list[Any] = []

    def counted(*args: Any) -> Any:
        read.append(args)
        return measure(*args)

    monkeypatch.setattr(graph, "measure", counted)
    stl = write_stl("rounded_lid", tmp_path)
    row = job.run(stl, approves, as_laid=True)
    assert row is not None, job.error
    assert len(read) == 1
    laid = candidate_in(stl, LAID)
    assert row.features == measure(stl, laid.bed_contact_area_mm2), "and reads as it did"


def test_a_pose_kept_from_the_context_doesnt_wait_either(
    tmp_path: Path, job: Job, held: Held, one_pose_only: None
) -> None:
    row = job.run(down(tmp_path, "tee", "+z"), approves)
    assert row is not None, job.error
    assert not held.let_go.is_set()
    assert (row.rotation_deg, row.orientation_source) == (FLIPPED, "context")


def test_a_kept_pose_under_the_print_loop_asks_first_and_the_loop_has_all_six(
    tmp_path: Path, job: Job, held: Held, one_pose_only: None,
    let_go_when_said: Callable[[str], list[str]],
) -> None:
    said = let_go_when_said("the print loop chooses among all six")
    job.at_questions = lambda: said.append("asked")
    given: list[str] = []

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        given.append(str(messages))
        raise RuntimeError("no model here")

    loop = PrintLoop(chat=chat, person=lambda question: "yes", model="fake")
    row = job.run(down(tmp_path, "tee", "+z"), approves, loop)
    assert row is not None, job.error
    assert said[0] == "asked", "the questions came before anything waited"
    assert said[1].startswith("waiting for the 5 poses still being"), "and the loop's wait says so"
    assert all(f"- {pose_value(r)}: " in given[0] for r in AXIS_ALIGNED)
    assert row.rotation_deg == FLIPPED


def test_a_side_that_doesnt_fit_the_plate_weighs_all_six_as_before(
    tmp_path: Path, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    weighed: list[int] = []
    def geometric(request: Any, *args: Any) -> Any:
        weighed.append(len(request.candidates))
        return propose_geometric(request, *args)

    monkeypatch.setattr(graph, "propose_geometric", geometric)
    monkeypatch.setattr(graph, "build_volume_mm", lambda machine: (256.0, 256.0, 50.0))
    row = job.run(down(tmp_path, "lid", "+x"), approves)  # 100 mm tall so
    assert row is not None, job.error
    assert weighed == [6]


# The five still arrive
# --------------------------------------------------------------------------


def test_the_review_says_which_arent_ready_and_orientation_waits_saying_so(
    tmp_path: Path, job: Job, held: Held
) -> None:
    stl = write_stl("tee", tmp_path)
    six = candidates(stl)
    person = Person(held, "orientation", "1", "its top face is seen", "ok")
    row = job.run(stl, person.reviewer(), as_laid=True)
    assert row is not None, job.error
    first = person.shown[0]
    for rotation in OTHERS:
        assert (f"{pose_value(rotation):>9}  still being measured: orientation waits for it"
                in first)
    waits = ("Waiting for the 5 poses still being measured: "
             f"{'; '.join(map(pose_value, OTHERS))}.")
    assert person.shown[1] == waits
    for n, pose in enumerate(six[1:], 1):
        assert (f"  {n}) {pose_value(pose.rotation_deg)}: {pose.overhang_area_mm2:g} mm2 of "
                f"overhang, {pose.bed_contact_area_mm2:g} mm2 on the plate") in person.said
    again = person.shown[-1]  # the review once turned
    assert "still being measured" not in again
    for pose in six:
        assert (f"{pose_value(pose.rotation_deg):>9}  {pose.overhang_area_mm2:g} mm2 overhang"
                in again)
    assert "<- this print" in again
    assert row.rotation_deg == FLIPPED
    assert [(c.key, c.value) for c in row.corrections or ()] == [(ORIENTATION, "180,0,0")]


def test_a_pose_typed_ahead_is_the_one_it_numbers(tmp_path: Path, job: Job, held: Held) -> None:
    person = Person(held, "orientation=3 because it's stronger so", "ok")
    row = job.run(write_stl("tee", tmp_path), person.reviewer(), as_laid=True)
    assert row is not None, job.error
    assert row.rotation_deg == OTHERS[2]


def test_the_review_shows_all_six_as_today_once_theyre_in(tmp_path: Path, job: Job) -> None:
    stl = write_stl("rounded_lid", tmp_path)
    shown: list[tuple[str, str]] = []

    def review(request: ReviewRequest) -> Review:
        assert request.measuring is not None
        six = request.measuring.settled()
        today = render(replace(request, measuring=None, candidates=six))
        shown.append((render(request), today))
        return Review(approved=True)

    assert job.run(stl, review, as_laid=True) is not None, job.error
    [(now, today)] = shown
    assert now == today
    assert now.count(" mm tall") >= 6


def test_the_past_prints_offer_waits_only_for_the_pose_one_was_in(
    tmp_path: Path, job: Job, held: Held, ledger: Ledger
) -> None:
    stl = write_stl("tee", tmp_path)
    printed(ledger, "first", stl,
            (yours(ORIENTATION, "180,0,0", "its top face is seen"), yours("wall_loops", "4")),
            rotation=FLIPPED, source="sponsor")
    held.first_go = {FLIPPED}  # that one's measured, and the other four are held
    seen: list[ReviewRequest] = []

    def review(request: ReviewRequest) -> Review:
        seen.append(request)
        return Review(approved=True)

    assert job.run(stl, review, as_laid=True) is not None, job.error
    assert not held.let_go.is_set()
    offering = seen[0].offering
    assert offering is not None and offering.past is not None
    assert {item.key: item.value for item in offering.past.items}[ORIENTATION] == "180,0,0"


def test_the_offer_says_when_it_waits(
    tmp_path: Path, job: Job, held: Held, ledger: Ledger,
    let_go_when_said: Callable[[str], list[str]],
) -> None:
    stl = write_stl("tee", tmp_path)
    printed(ledger, "first", stl, (yours("wall_loops", "4"),), rotation=FLIPPED)
    said = let_go_when_said("a past print was in it")
    assert job.run(stl, approves, as_laid=True) is not None, job.error
    assert said[0] == ("waiting for the pose still being measured (180,0,0): a past print was "
                       "in it, and its offer compares them in it")


def test_a_pose_that_cant_be_measured_is_said_and_never_offered(
    tmp_path: Path, job: Job, held: Held
) -> None:
    held.failing = {FLIPPED}
    # A number typed ahead counted the failed one, so the list is shown and asked.
    person = Person(held, "orientation=2 because it's stronger so", "back", "ok")
    row = job.run(write_stl("tee", tmp_path), person.reviewer(), as_laid=True)
    assert row is not None, job.error
    assert ("180,0,0 couldn't be measured, so it isn't offered: ValueError: a made-up bad pose"
            in person.said)
    assert "Which pose should it print in?" in person.said
    assert "  1) 90,0,0:" in person.said
    assert row.rotation_deg == LAID


def test_the_loop_never_chooses_among_fewer_than_six(
    tmp_path: Path, held: Held
) -> None:
    held.failing = {FLIPPED}
    held.let_go.set()
    stl = write_stl("tee", tmp_path)
    measuring = Measuring(stl, [candidate_in(stl, LAID)])
    with pytest.raises(RuntimeError, match="none is chosen: 180,0,0: ValueError"):
        measuring.all_six()


def test_stopping_lets_go_whoever_waits_and_begins_no_other(tmp_path: Path, held: Held) -> None:
    stl = write_stl("tee", tmp_path)
    measuring = Measuring(stl, [candidate_in(stl, LAID)])
    measuring.start()
    timer = threading.Timer(0.1, measuring.stop)
    timer.start()
    assert [c.rotation_deg for c in measuring.settled()] == [LAID]
    held.let_go.set()
    assert measuring._thread is not None
    measuring._thread.join(WAIT_S)
    assert held.began == [FLIPPED]


def test_the_five_are_never_read_from_a_file_that_changed(
    tmp_path: Path, job: Job, held: Held, monkeypatch: pytest.MonkeyPatch
) -> None:
    stl = write_stl("tee", tmp_path)
    (tmp_path / "other").mkdir()
    other = write_stl("cube", tmp_path / "other").read_bytes()
    def then_swapped(*args: Any) -> Any:
        found = candidate_in(*args)
        stl.write_bytes(other)
        return found

    monkeypatch.setattr(graph, "candidate_in", then_swapped)
    assert job.run(stl, approves, as_laid=True) is None
    assert "changed since analysis" in job.error
    assert held.began == []
