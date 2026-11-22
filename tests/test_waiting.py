"""The questions at once: nobody waits on a model call to get the two that need no model.

mqucifer/print-shop#208. The model is a fake that never answers, answers late, or fails; the
person is a script; the parts and every question are made up. Nothing here reaches a model, a
broker, Orca or a printer. The terminal's own wait is run on a real pseudo-terminal.
"""

from __future__ import annotations

import io
import json
import os
import pty
import select
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from print_shop.expert.client import CallInfo, CutOff, Stopped, TimedOut
from print_shop.ingest.analyzer import analyze
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.intake import (
    Added,
    Answer,
    AnswersRefused,
    Asker,
    Intake,
    Option,
    Question,
    cli_asker,
    render,
    replay_asker,
)
from print_shop.setup.proposal import Proposal
from print_shop.setup.questions import MAX_QUESTIONS, MAX_SLICES, PURPOSE, STOCK, Asked, estimate
from print_shop.setup.review import Review, ReviewRequest
from print_shop.setup.review import render as render_review
from print_shop.setup.skill import groups_for
from print_shop.setup.waiting import Prompted, Waiting, ask_meanwhile, cli_waiting, took
from tests.fakes import CI_PROFILES, STORE_PROFILES, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import _model

WAIT_S = 20.0  # for anything that should take milliseconds


def option(label: str, recommended: bool = False, **settings: str) -> Option:
    return Option(label=label, settings=settings, impact=f"{label} impact",
                  recommended=recommended, why="because" if recommended else "")


CLOSER = Question(feature="purpose", text="Is this made-up block a cover or a spacer?", options=(
    option("a cover", True, wall_loops="3"), option("a spacer", sparse_infill_density="40%")))
TOP = Question(feature="flat_top", text="How smooth is its made-up top?", options=(
    option("as it comes", True), option("ironed", top_shell_layers="6")))
EDGE = Question(feature="section", text="How stiff is its made-up edge?", options=(
    option("as it comes", True), option("stiff", wall_loops="4")))
THIRD = Question(feature="footprint", text="A third made-up question?", options=(
    option("one", True), option("two")))


def answered(*questions: Question) -> Asked:
    return Asked(questions, "")


def same(questions: tuple[Question, ...]) -> tuple[Question, ...]:
    return questions


class Person:
    """Types from a script, and keeps what they were shown and at which prompt they typed."""

    def __init__(self, *lines: str, before: Callable[[int], None] | None = None) -> None:
        self.lines = list(lines)
        self.before = before
        self.shown: list[str] = []
        self.prompts: list[tuple[str, int]] = []  # each prompt, and how much had been shown

    def write(self, text: str) -> None:
        self.shown.append(text)

    def read(self, prompt: str) -> str:
        self.prompts.append((prompt, len(self.shown)))
        if self.before is not None:
            self.before(len(self.prompts))
        assert self.lines, f"asked more than was scripted, at {prompt!r}"
        return self.lines.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.shown)

    def asker(self) -> Asker:
        return cli_asker(read=self.read, write=self.write)

    def waiting(self, *, terminal: bool = True, replayed: bool = False) -> Waiting:
        screen = io.StringIO() if terminal else None
        return cli_waiting(self.read, self.write, replayed=replayed, screen=lambda: screen,
                           typed=self._typed, forget=lambda: None)

    def _typed(self, timeout_s: float) -> bool:
        # A script that goes on types its lines into the wait. Any other line is an answer, and
        # waits for the question it answers.
        if "go" in self.lines:
            return True
        time.sleep(min(timeout_s, 0.01))
        return False


def ask(person: Person, propose: Callable[[threading.Event], Asked], **how: Any) -> Intake:
    return ask_meanwhile(propose, person.asker(), person.waiting(**how), same)


@pytest.fixture
def forever() -> Iterator[Callable[[threading.Event], Asked]]:
    """A model that never answers, until it's stopped or the test ends."""
    over = threading.Event()

    def propose(stop: threading.Event) -> Asked:
        while not stop.wait(0.01) and not over.is_set():
            pass
        raise Stopped("stopped")

    yield propose
    over.set()


# --------------------------------------------------------------------------
# A model that never answers: the stock questions at once, and on to the review
# --------------------------------------------------------------------------


class Reviews:
    def __init__(self) -> None:
        self.seen: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> Review:
        self.seen.append(request)
        return Review(approved=True)


def test_with_a_model_that_never_answers_the_job_reaches_the_review_and_prints(
    tmp_path: Path,
) -> None:
    """The issue's first Done when, through the daemon, the graph, the gate and the ledger."""
    released = threading.Event()

    def never(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        released.wait(WAIT_S)
        raise RuntimeError("the fake never answers")

    first_asked: list[float] = []
    person = Person("3", "1", "go", before=lambda n: first_asked.append(time.monotonic()))
    reviews = Reviews()
    (tmp_path / "work").mkdir()
    ledger = Ledger(tmp_path / "ledger.sqlite")
    bridge = MemoryBridge()
    daemon = Daemon(
        cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work", printer=FramePrinter(),
        slicer=RecordedSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
        proposer=Proposer(ask=_model, ask_questions=never),
        asker=person.asker(), waiting=person.waiting(), reviewer=reviews,
    )
    try:
        with daemon:
            submitted = time.monotonic()
            job_id = daemon.submit(write_stl("cube", tmp_path))
            assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            assert not released.is_set(), "the model still hasn't answered"
            assert first_asked[0] - submitted < 10, "the stock questions show within seconds"
            bridge.press(CFG, "approve")
            assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
    finally:
        released.set()

    assert PURPOSE.text in person.text and "seen once it's in use" in person.text
    assert "The model is still reading the part (" in person.text
    assert "Type go and Enter to go on without waiting" in person.text
    [review] = reviews.seen
    assert "note: you went on without the model's questions after" in render_review(review)

    row = ledger.get(job_id)
    assert row is not None and row.intake is not None
    assert row.intake.questions == STOCK
    assert row.intake.answers == (Answer(choice=2), Answer(choice=0))
    assert row.intake.call["finish_reason"] == "went on" and "seconds" in row.intake.call
    assert "you went on without the model's questions" in describe(row)


def test_only_go_goes_on_so_a_second_enter_doesnt(
    forever: Callable[[threading.Event], Asked],
) -> None:
    person = Person("1", "1", "", "yes", "go")
    intake = ask(person, forever)
    assert person.text.count("Type go and Enter to go on without waiting.") == 3
    assert intake.call["finish_reason"] == "went on"


def test_going_on_ends_the_models_call(forever: Callable[[threading.Event], Asked]) -> None:
    stops: list[threading.Event] = []

    def propose(stop: threading.Event) -> Asked:
        stops.append(stop)
        return forever(stop)

    ask(Person("1", "1", "go"), propose)
    assert stops[0].is_set()


def test_a_reply_after_going_on_changes_nothing() -> None:
    release, returned = threading.Event(), threading.Event()

    def late(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        returned.set()
        return answered(CLOSER, TOP, STOCK[1])

    person = Person("2", "1", "go")
    intake = ask(person, late)
    before = person.text
    release.set()
    assert returned.wait(WAIT_S)
    time.sleep(0.05)
    assert (intake.questions, intake.offered) == (STOCK, ())
    assert "its call was closed and none of them is used" in intake.without
    assert person.text == before, "nothing more is said or asked"


def test_when_the_wait_is_run_without_a_terminal_it_runs_to_the_calls_end() -> None:
    def slow(stop: threading.Event) -> Asked:
        time.sleep(0.2)
        return answered(*STOCK)

    person = Person("1", "1")
    intake = ask(person, slow, terminal=False)
    assert "Type go" not in person.text
    assert "has no other question" in person.text
    assert intake.without == ""


def test_the_wait_says_how_long_it_has_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("print_shop.setup.waiting.SAID_EVERY_S", 0.1)

    def slow(stop: threading.Event) -> Asked:
        time.sleep(1.2)
        return answered(*STOCK)

    person = Person("1", "1")
    ask(person, slow, terminal=False)
    assert "still waiting for the model: " in person.text
    assert (took(42.9), took(361.0)) == ("42 s", "6 min 1 s")


# --------------------------------------------------------------------------
# A slow model that does answer: added, and nothing already answered is lost
# --------------------------------------------------------------------------


def test_questions_that_come_late_are_added_without_losing_an_answer() -> None:
    release = threading.Event()

    def slow(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        return answered(PURPOSE, TOP, EDGE, STOCK[1])

    def arrives(prompt: int) -> None:
        if prompt == 1:  # while "1>" is open
            release.set()
            time.sleep(0.2)

    person = Person("2", "3", "2", "o it bends", before=arrives)
    intake = ask(person, slow)
    assert intake.questions == (*STOCK, TOP, EDGE)
    assert intake.answers == (
        Answer(choice=1), Answer(choice=2), Answer(choice=1), Answer(other="it bends"))
    assert [p for p, _ in person.prompts] == ["1> ", "2> ", "3> ", "4> "]


def test_a_list_never_changes_under_an_open_prompt() -> None:
    release = threading.Event()

    def slow(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        return answered(CLOSER, TOP, STOCK[1])

    def arrives(prompt: int) -> None:
        if prompt == 1:
            release.set()
            time.sleep(0.2)

    person = Person("2", "1", "", "1", before=arrives)
    intake = ask(person, slow)
    at_second = "\n".join(person.shown[: person.prompts[1][1]])
    assert "The model's questions came after" in at_second, "said while they answer"
    assert CLOSER.text not in at_second and TOP.text not in at_second, "shown after they have"
    assert intake.answers[0] == Answer(choice=1), "the 2 was read against what was on screen"


def test_the_models_purpose_question_is_closer_choices_that_enter_keeps() -> None:
    person = Person("2", "1", "")
    intake = ask(person, lambda stop: answered(CLOSER, STOCK[1]))
    assert f"1. {PURPOSE.text}  you: handled, clipped or lightly loaded" in person.text
    assert "closer choices" in person.text and "Enter) keep your answer" in person.text
    assert "1) a cover (3 walls)" in person.text
    assert intake.questions == STOCK and intake.answers == (Answer(choice=1), Answer(choice=0))
    assert intake.offered == (CLOSER,), "what was offered is on record"
    assert "kept your purpose answer" in intake.note
    assert intake.chosen()[0][1].settings == {}, "nothing of the model's is applied"


def test_picking_a_closer_choice_replaces_the_answer_and_keeps_both_on_record() -> None:
    person = Person("2", "1", "9", "2")
    intake = ask(person, lambda stop: answered(CLOSER, STOCK[1]))
    assert "or Enter to keep your answer" in person.text
    assert intake.questions == (CLOSER, STOCK[1])
    assert intake.answers == (Answer(choice=1), Answer(choice=0))
    assert intake.chosen()[0][1].settings == {"sparse_infill_density": "40%"}
    assert intake.offered == (PURPOSE,)
    assert "in place of your first answer, 'handled, clipped or lightly loaded'" in intake.note


def test_a_kept_answers_choices_still_open_their_skill_groups() -> None:
    """The skill read for the part is what it was when those choices replaced purpose."""
    surface = CLOSER.model_copy(update={"options": (
        option("a face that shows", True, layer_height="0.16"), option("hidden"))})
    kept = ask(Person("1", "1", ""), lambda stop: answered(surface, STOCK[1]))
    then = Intake(questions=(surface, STOCK[1]), answers=(Answer(choice=1), Answer(choice=0)))
    assert groups_for(kept, ()) == groups_for(then, ())


def test_choices_kept_over_are_still_asked_again_to_the_reviews_offer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With mqucifer/print-shop#211: a past answer to a setting this job asked about isn't
    pre-selected, and closer choices the person kept their answer over were asked."""
    from print_shop.setup.offer import offering

    offers: list[Any] = []

    def watched(ledger: Any, part: Any) -> Any:
        offers.append(part)
        return offering(ledger, part)

    monkeypatch.setattr("print_shop.orchestrator.graph.offer_past_prints", watched)

    def closer(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [{"feature": "purpose", "text": CLOSER.text, "options": [
            {"label": "a cover", "settings": [{"key": "top_shell_layers", "value": "6"}],
             "impact": "i", "recommended": True, "why": "w"},
            {"label": "a spacer", "settings": [], "impact": "i", "recommended": False, "why": ""},
        ]}]}

    person = Person("2", "1", "")
    (tmp_path / "work").mkdir()
    bridge = MemoryBridge()
    daemon = Daemon(
        cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work", slicer=RecordedSlicer(),
        bridge=lambda cfg: bridge, ledger=Ledger(tmp_path / "ledger.sqlite"),
        proposer=Proposer(ask=_model, ask_questions=closer),
        asker=person.asker(), waiting=person.waiting(), reviewer=Reviews(),
    )
    with daemon:
        job_id = daemon.submit(write_stl("cube", tmp_path))
        assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
        bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=WAIT_S)
    assert offers and "top_shell_layers" in offers[0].asked


def test_four_questions_is_still_the_most_and_one_left_out_is_said() -> None:
    person = Person("1", "1", "1", "1")
    intake = ask(person, lambda stop: answered(TOP, EDGE, THIRD, STOCK[1]))
    assert len(intake.questions) == MAX_QUESTIONS
    assert f"left out '{THIRD.text}'" in intake.note


def test_the_options_share_one_limit_on_slices() -> None:
    many = tuple(option(f"choice {n}", n == 0, wall_loops=str(n + 1)) for n in range(6))
    big = [q.model_copy(update={"options": many}) for q in (CLOSER, TOP, EDGE)]
    sliced: list[dict[str, str]] = []

    def price(questions: tuple[Question, ...]) -> tuple[Question, ...]:
        return estimate(questions, lambda settings: sliced.append(settings))

    person = Person("1", "1", "", "1", "1")
    ask_meanwhile(lambda stop: answered(*big, STOCK[1]), person.asker(), person.waiting(), price)
    assert len(sliced) == MAX_SLICES


def test_added_questions_are_numbered_on_and_say_they_were_added() -> None:
    shown = render(Added((TOP, EDGE), before=2))
    assert shown.startswith("Added by the model, from the part's shape:\n3. ")
    assert f"4. {EDGE.text}" in shown
    assert render((TOP, EDGE)).startswith("A few questions about this part, from its shape:\n1. ")


# --------------------------------------------------------------------------
# A cut-off, a timeout and a dead model are each said and recorded as what they are
# --------------------------------------------------------------------------


def _cut(finish: str) -> CutOff:
    return CutOff(f"cut off, {finish}", CallInfo(
        model="fake", finish_reason=finish, prompt_tokens=0, completion_tokens=0,
        reasoning_tokens=0, max_tokens=16000, seconds=361.0, whitespace_run=2000))


@pytest.mark.parametrize(
    ("raised", "finish", "says"),
    [
        (_cut("runaway"), "runaway", "was cut off as its answer ran on as blank space"),
        (_cut("too long"), "too long", "was cut off as its answer ran past what four questions"),
        (_cut("length"), "length", "was cut off at its token limit"),
        (TimedOut(800.0, 800.0), "timed out", "timed out"),
        (RuntimeError("the proxy is down"), "unavailable", "was unavailable (the proxy is down)"),
    ],
)
def test_a_failed_call_says_so_when_it_fails_and_is_recorded_as_what_it_was(
    raised: Exception, finish: str, says: str, tmp_path: Path
) -> None:
    release = threading.Event()
    features = measure(write_stl("cube", tmp_path), 400.0)

    def fails(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        release.wait(WAIT_S)
        raise raised

    proposer = Proposer(ask_questions=fails)

    def at_prompt(prompt: int) -> None:
        if prompt == 1:
            release.set()
            time.sleep(0.2)

    person = Person("2", "1", before=at_prompt)
    intake = ask(person, lambda stop: proposer.questions(features, STORE_PROFILES, stop))

    while_answering = "\n".join(person.shown[: person.prompts[1][1]])
    assert f"The model {says}" in while_answering, "said as it fails, not only afterwards"
    assert " after " in while_answering
    assert intake.questions == STOCK and intake.answers == (Answer(choice=1), Answer(choice=0))
    assert intake.call["finish_reason"] == finish and "seconds" in intake.call
    assert says in intake.without
    assert intake.note.startswith("only purpose and what's seen asked")


def test_what_lands_under_an_open_prompt_shows_the_prompt_again() -> None:
    release = threading.Event()
    screen = io.StringIO()

    def fails(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        raise RuntimeError("down")

    def at_prompt(n: int) -> None:
        if n == 1:
            release.set()
            time.sleep(0.2)

    person = Person("2", "1", before=at_prompt)
    answering = Prompted(person.read)
    waiting = cli_waiting(person.read, person.write, prompt=answering.prompt,
                          screen=lambda: screen, forget=lambda: None)
    ask_meanwhile(fails, cli_asker(read=answering, write=person.write), waiting, same)
    said = next(text for text in person.shown if "was unavailable" in text)
    assert said.endswith("\nKeep answering 1 (what you've typed is kept):")
    assert screen.getvalue() == "1> ", "the cursor is back at the question's prompt"
    assert answering.prompt() == ""


def test_go_at_a_question_says_when_it_works() -> None:
    person = Person("go", "2", "1")
    ask(person, lambda stop: answered(*STOCK), terminal=False)
    assert "or o and your words; go works once these are answered" in person.text


def test_an_error_nobody_expected_is_a_failed_call_and_not_a_hung_job() -> None:
    def broken(stop: threading.Event) -> Asked:
        raise ValueError("a made-up bug")

    intake = ask(Person("1", "1"), broken)
    assert intake.call["finish_reason"] == "unavailable"
    assert "a made-up bug" in intake.without


def test_going_on_holds_the_guards_as_a_failed_call_does(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    stl = write_stl("snap", tmp_path)
    features = measure(stl, analyze(stl).bed_contact_area_mm2)

    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("down")

    proposer = Proposer(ask=down, ask_questions=down)
    went_on = ask(Person("2", "1", "go"), forever)
    failed = ask(Person("2", "1"), lambda stop: proposer.questions(features, STORE_PROFILES))

    def proposed(intake: Intake) -> Proposal:
        return proposer(intake, features, STORE_PROFILES)

    assert proposed(went_on).changes == proposed(failed).changes
    assert proposed(went_on).get("enable_support") is not None, "the support guard holds"


# --------------------------------------------------------------------------
# Recorded answers
# --------------------------------------------------------------------------


def _file(tmp_path: Path, *rows: dict[str, Any]) -> Asker:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": list(rows)}))
    return replay_asker(path)


def _replayed(asker: Asker, *questions: Question) -> tuple[Intake, list[Sequence[Question]]]:
    asked: list[Sequence[Question]] = []

    def watched(given: Sequence[Question]) -> tuple[Answer, ...]:
        asked.append(given)
        return asker(given)

    person = Person()
    return ask_meanwhile(lambda stop: answered(*questions), watched,
                         person.waiting(replayed=True), same), asked


def test_recorded_answers_keep_theirs_over_closer_choices(tmp_path: Path) -> None:
    file = _file(tmp_path, {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 1})
    intake, asked = _replayed(file, CLOSER, STOCK[1])
    assert asked == [STOCK], "asked once, as one set, and never stopped on the refinement"
    assert intake.questions == STOCK and intake.offered == (CLOSER,)


def test_recorded_answers_that_picked_a_closer_choice_replay_it(tmp_path: Path) -> None:
    file = _file(tmp_path, {"question": CLOSER.text, "choice": "a spacer"},
                 {"question": "seen", "choice": 1})
    intake, _ = _replayed(file, CLOSER, STOCK[1])
    assert intake.questions == (CLOSER, STOCK[1]) and intake.offered == (PURPOSE,)
    assert intake.answers[0] == Answer(choice=1)


def test_recorded_answers_for_other_questions_are_still_refused(tmp_path: Path) -> None:
    file = _file(tmp_path, {"question": "bridge", "choice": 1})
    with pytest.raises(AnswersRefused, match="none of it is used"):
        _replayed(file, CLOSER, TOP, STOCK[1])


def test_with_recorded_answers_go_still_goes_on(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    file = _file(tmp_path, {"question": "purpose", "choice": 2}, {"question": "seen", "choice": 1})
    intake = ask_meanwhile(forever, file, Person("go").waiting(replayed=True), same)
    assert intake.questions == STOCK and intake.call["finish_reason"] == "went on"


# --------------------------------------------------------------------------
# The real terminal
# --------------------------------------------------------------------------

_TERMINAL = textwrap.dedent("""
    import sys, time
    from pathlib import Path

    from print_shop.joblog import JobLog
    from print_shop.setup.intake import Option, Question, cli_asker
    from print_shop.setup.questions import SEEN, STOCK, Asked
    from print_shop.setup.waiting import ask_meanwhile, cli_waiting

    folder, how = Path(sys.argv[1]), sys.argv[2]
    closer = Question(feature="purpose", text="A cover or a spacer?", options=(
        Option(label="a cover", settings={}, impact="i", recommended=True, why="w"),
        Option(label="a spacer", settings={}, impact="i")))

    def propose(stop):
        while not stop.wait(0.05):
            if how == "answers" and (folder / "release").exists():
                return Asked((closer, SEEN), "")
        return Asked(STOCK, "stopped")

    log = JobLog([])
    log.open(folder / "job.log")
    with log.capturing():
        read = log.reader(input)
        intake = ask_meanwhile(propose, cli_asker(read=read), cli_waiting(read=read), lambda q: q)
        print("FIRST", intake.questions[0].feature, intake.answers[0].choice,
              intake.call.get("finish_reason", "answered"), len(intake.offered))
    log.close()
""")


class Terminal:
    """`waiting.py` in a process of its own, on a pseudo-terminal."""

    def __init__(self, tmp_path: Path, how: str) -> None:
        self.folder = tmp_path
        script = tmp_path / "terminal.py"
        script.write_text(_TERMINAL)
        self.master, slave = pty.openpty()
        self.process = subprocess.Popen(
            [sys.executable, str(script), str(tmp_path), how],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        os.close(slave)
        self.seen = ""

    def until(self, text: str) -> None:
        give_up = time.monotonic() + WAIT_S
        while text not in self.seen:
            assert time.monotonic() < give_up, f"{text!r} never showed in {self.seen!r}"
            if select.select([self.master], [], [], 0.2)[0]:
                try:
                    self.seen += os.read(self.master, 65536).decode(errors="replace")
                except OSError:
                    break
        assert text in self.seen

    def type(self, text: str) -> None:
        os.write(self.master, text.encode())

    def close(self) -> None:
        self.process.kill()
        self.process.wait(WAIT_S)
        os.close(self.master)


@pytest.fixture
def terminal(tmp_path: Path) -> Iterator[Callable[[str], Terminal]]:
    made: list[Terminal] = []

    def start(how: str) -> Terminal:
        made.append(Terminal(tmp_path, how))
        return made[-1]

    yield start
    for each in made:
        each.close()


def test_on_a_terminal_the_time_runs_and_go_goes_on(
    terminal: Callable[[str], Terminal], tmp_path: Path
) -> None:
    run = terminal("never")
    run.until("1> ")
    run.type("2\n")
    run.until("2> ")
    run.type("1\n")
    run.until("waiting for the model: 0 s")
    run.until("waiting for the model: 1 s")
    run.type("go\n")
    run.until("FIRST purpose 1 went on 0")
    log = (tmp_path / "job.log").read_text()
    assert "\ngo\n" in log, "what they typed is in the job's log"
    assert "waiting for the model:" not in log, "the timer isn't, a line for each second"


def test_on_a_terminal_whats_typed_ahead_never_answers_a_question_unseen(
    terminal: Callable[[str], Terminal], tmp_path: Path
) -> None:
    run = terminal("answers")
    run.until("1> ")
    run.type("2\n")
    run.until("2> ")
    run.type("1\n")
    run.until("waiting for the model: ")
    run.type("1")  # half a line, typed before the closer choices were shown
    time.sleep(0.2)
    (tmp_path / "release").write_text("")
    run.until("Enter) keep your answer")
    run.until("o) other")
    time.sleep(0.2)
    run.type("\n")
    run.until("FIRST purpose 1 answered 1")
