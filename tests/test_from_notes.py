"""The questions start from the part's notes, and ask only what they don't say.

mqucifer/print-shop#236. Every part, note, purpose and question is made up. The model, the
slicer, the printer and the broker are fakes, and the person is a script; what's typed ahead is
tried on a real pseudo-terminal.
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
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.checkpoint import make_serializer
from print_shop.desk.again import again, answers_file
from print_shop.desk.page import _questions as desk_questions
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.context import LOADS, SIDECAR, PartContext, place, read_context, refuses
from print_shop.setup.features import measure
from print_shop.setup.intake import (
    DIFFERS_PROMPT,
    NOTES_PROMPT,
    Added,
    Answer,
    AnswersRefused,
    Asker,
    Intake,
    Question,
    cli_asker,
    render,
    replay_asker,
)
from print_shop.setup.model import prompt as settings_prompt
from print_shop.setup.proposal import Change, Source
from print_shop.setup.questions import PURPOSE, SEEN, STOCK, Asked, stock
from print_shop.setup.reading import flags, seen
from print_shop.setup.review import Review, ReviewRequest, parse_answer
from print_shop.setup.review import render as render_review
from print_shop.setup.similar import likeness
from print_shop.setup.skill import groups_for, load
from print_shop.setup.waiting import Waiting, ask_meanwhile, cli_waiting
from tests.fakes import CI_PROFILES, STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_reading import FILAMENT, PETG, PROCESS, _read
from tests.test_send_back import AnyPart
from tests.test_waiting import EDGE, TOP, Person, same

WAIT_S = 20.0
LAID = (0.0, 0.0, 0.0)
FLIPPED = (180.0, 0.0, 0.0)
ON_ITS_SIDE = (90.0, 0.0, 0.0)
LOADED = "carries a real load"
FOR = {"for": "a made-up bracket for a shelf", "loaded": LOADED}
BOTH: dict[str, Any] = {"purpose": FOR, "seen": ["top", "bottom"]}


def noted(tmp_path: Path, data: dict[str, Any] | None = None) -> Path:
    """A made-up cube with these notes beside it, or none."""
    stl = write_stl("cube", tmp_path)
    if data is not None:
        stl.with_name(stl.stem + SIDECAR).write_text(json.dumps(data))
    return stl


def _answers_then(row: Row) -> list[str]:
    """What the offer lists as that print's answers."""
    from print_shop.setup.offer import ThisPart, offering

    class One:
        def rows(self) -> list[Row]:
            return [row.model_copy(update={"corrections": (Change(
                key="brim_type", preset="no_brim", value="auto_brim", reason="made up",
                source=Source.SPONSOR),)})]

        def query(self, sql: str, *args: Any) -> list[dict[str, Any]]:
            return []

    part = ThisPart(stl=Path(row.stl_name), stl_name=row.stl_name, sha256=row.stl_sha256,
                    features=tuple(row.features or ()), rotation_deg=LAID, height_mm=20.0,
                    machine={}, process={"brim_type": "no_brim"}, filament={})
    past = offering(One(), part).past  # type: ignore[arg-type]
    assert past is not None
    return list(past.answers)


def two(
    tmp_path: Path, data: dict[str, Any] | None, pose: tuple[float, float, float] = LAID
) -> tuple[Question, Question]:
    return stock(read_context(noted(tmp_path, data)), pose)


def nothing(stop: threading.Event) -> Asked:
    return Asked(STOCK, "")


@pytest.fixture
def forever() -> Iterator[Callable[[threading.Event], Asked]]:
    """A questions call that never answers, until it's stopped or the test ends."""
    over = threading.Event()

    def propose(stop: threading.Event) -> Asked:
        while not stop.wait(0.01) and not over.is_set():
            pass
        return Asked(STOCK, "")

    yield propose
    over.set()


def arrives(propose: Callable[[threading.Event], Asked]) -> tuple[Any, Callable[[int], None]]:
    """A call that answers while the first prompt is open, and what lets it."""
    release = threading.Event()

    def late(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        return propose(stop)

    def at_prompt(n: int) -> None:
        if n == 1:
            release.set()
            time.sleep(0.2)

    return late, at_prompt


class Reviews:
    def __init__(self) -> None:
        self.requests: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> Review:
        self.requests.append(request)
        return Review(approved=True)


class Job:
    """One job through the daemon, its gate and a made-up ledger."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.reviews = Reviews()
        self.settings_prompts: list[str] = []
        self.ledger = Ledger(tmp_path / "ledger.sqlite")

    def settings(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Strengthens what's said to carry a load, as the answer is read."""
        self.settings_prompts.append(user)
        changes = [{"key": "wall_loops", "value": "4", "feature": "purpose",
                    "knowledge": "walls-shells", "reason": "it carries a load"}
                   ] if f"-> {LOADED} (no setting)" in user else []
        return {"reasoning": "", "changes": changes, "design_notes": []}

    def run(
        self, stl: Path, questions: Any, asker: Asker, waiting: Waiting | None = None,
    ) -> Row:
        (self.tmp_path / "work").mkdir(exist_ok=True)
        bridge = MemoryBridge()
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=self.tmp_path / "work",
            printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
            ledger=self.ledger, proposer=Proposer(ask=self.settings, ask_questions=questions),
            asker=asker, waiting=waiting, reviewer=self.reviews,
        )
        with daemon:
            job_id = daemon.submit(stl, as_laid=True)
            assert daemon.wait_for_gate(job_id, timeout_s=WAIT_S) is not None
            bridge.press(CFG, "approve")
            assert daemon.wait_for_finish(job_id, timeout_s=WAIT_S).printed
        row = self.ledger.get(job_id)
        assert row is not None and row.intake is not None
        return row


@pytest.fixture
def job(tmp_path: Path) -> Iterator[Job]:
    made = Job(tmp_path)
    yield made
    made.ledger.close()


def no_question(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"questions": []}


# --------------------------------------------------------------------------
# Purpose and seen in the notes: one Enter, and no wait
# --------------------------------------------------------------------------


def test_with_both_in_the_notes_one_enter_reaches_the_review_with_no_call_waited_on(
    tmp_path: Path, job: Job
) -> None:
    released = threading.Event()

    def never(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        released.wait(4 * WAIT_S)
        raise RuntimeError("the fake never answers")

    person = Person("")
    try:
        row = job.run(noted(tmp_path, BOTH), never, person.asker(), person.waiting())
        assert not released.is_set(), "the questions call still hasn't answered"
    finally:
        released.set()

    assert [prompt for prompt, _ in person.prompts] == [NOTES_PROMPT], "one Enter, and no go"
    assert "still reading" not in person.text, "it never waited"
    assert "Going on without waiting for the model's questions" in person.text
    [request] = job.reviews.requests
    assert "note: the notes gave what it's for and what's seen" in render_review(request)

    intake = row.intake
    assert intake is not None
    assert intake.call["finish_reason"] == "not waited" and "seconds" in intake.call
    assert [q.confirmed(a) for q, a in zip(intake.questions, intake.answers, strict=True)] == [
        True, True]
    assert describe(row).count("(from the part's notes, confirmed)") == 2
    assert desk_questions(row).count("← from the part&#x27;s notes, confirmed") == 2


def test_the_read_back_says_the_notes_words_with_their_reading(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    person = Person("")
    ask_meanwhile(forever, person.asker(), person.waiting(), same, two(tmp_path, BOTH))
    card = next(text for text in person.shown if text.startswith("From the part's notes"))
    assert card.splitlines() == [
        "From the part's notes (cube.part.json):",
        "  1. For: a made-up bracket for a shelf. Carries a real load: "
        "the proposal strengthens the load path",
        "  2. Seen: the top and the bottom, as the file lays it",
        "Enter if that's right, or 1 or 2 to change it (1 2 for both).",
    ]
    assert PURPOSE.text not in person.text and SEEN.text not in person.text, "neither is asked"


def test_a_question_that_arrives_in_time_is_added(tmp_path: Path) -> None:
    propose, at_prompt = arrives(lambda stop: Asked((PURPOSE, TOP, SEEN), ""))
    person = Person("", "2", before=at_prompt)
    intake = ask_meanwhile(propose, person.asker(), person.waiting(), same, two(tmp_path, BOTH))
    assert [prompt for prompt, _ in person.prompts] == [NOTES_PROMPT, "3> "]
    assert "Added by the model" in person.text
    assert intake.questions[2] == TOP and intake.answers[2] == Answer(choice=1)
    assert intake.without == ""


def test_a_question_that_arrives_after_the_confirmation_changes_nothing(tmp_path: Path) -> None:
    release, returned = threading.Event(), threading.Event()

    def late(stop: threading.Event) -> Asked:
        release.wait(WAIT_S)
        returned.set()
        return Asked((PURPOSE, TOP, SEEN), "")

    person = Person("")
    intake = ask_meanwhile(late, person.asker(), person.waiting(), same, two(tmp_path, BOTH))
    before = person.text
    release.set()
    assert returned.wait(WAIT_S)
    time.sleep(0.05)
    assert len(intake.questions) == 2 and person.text == before
    assert "weren't waited for" in intake.without


def test_a_failed_call_says_it_adds_nothing_and_not_that_the_two_are_asked(
    tmp_path: Path,
) -> None:
    def fails(stop: threading.Event) -> Asked:
        raise RuntimeError("down")

    propose, at_prompt = arrives(fails)
    person = Person("", before=at_prompt)
    intake = ask_meanwhile(propose, person.asker(), person.waiting(), same, two(tmp_path, BOTH))
    assert "It adds no question." in person.text
    assert "Only purpose and what's seen are asked" not in person.text
    assert intake.without.endswith("so it added no question")
    assert intake.call["finish_reason"] == "unavailable"


def test_only_enter_confirms(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    person = Person("yes", "go", "3", "")
    intake = ask_meanwhile(forever, person.asker(), person.waiting(), same, two(tmp_path, BOTH))
    assert person.text.count("Enter if that's right") == 4
    assert len(person.prompts) == 4 and not intake.over_notes()


# --------------------------------------------------------------------------
# One given: that's read back, and only the other is asked
# --------------------------------------------------------------------------


def test_seen_alone_is_read_back_and_purpose_is_asked_and_the_call_waited_for(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    person = Person("", "2", "go")
    questions = two(tmp_path, {"seen": ["walls", "top"]})
    intake = ask_meanwhile(forever, person.asker(), person.waiting(), same, questions)
    assert [prompt for prompt, _ in person.prompts][:2] == [NOTES_PROMPT, "1> "]
    assert "Enter if that's right, or 2 to change it." in person.text
    assert PURPOSE.text in person.text and SEEN.text not in person.text
    assert "still reading" in person.text, "it's waited for, as today"
    assert intake.answers == (Answer(choice=1), questions[1].confirm())
    assert intake.questions[1].confirmed(intake.answers[1])
    assert not intake.questions[0].whose(intake.answers[0]), "purpose is the person's answer"


def test_purpose_alone_is_read_back_and_gets_no_closer_choices(tmp_path: Path) -> None:
    closer = PURPOSE.model_copy(update={"text": "A made-up cover or a spacer?"})
    person = Person("", "1")
    intake = ask_meanwhile(lambda stop: Asked((closer, SEEN), ""), person.asker(),
                           person.waiting(), same, two(tmp_path, {"purpose": FOR}))
    assert [prompt for prompt, _ in person.prompts] == [NOTES_PROMPT, "2> "]
    assert closer.text not in person.text, "nothing is asked twice"
    assert intake.offered == () and intake.questions[0].confirmed(intake.answers[0])


def test_notes_with_about_alone_ask_purpose_with_that_text_beside_it(tmp_path: Path) -> None:
    about = "A bracket. It carries a real load, 5 kg of books."
    context = read_context(noted(tmp_path, {"about": about}))
    purpose, seen_faces = stock(context, LAID)
    assert purpose.noted is None and context.loaded == "", "nothing is inferred from its words"
    assert seen_faces == SEEN

    person = Person("2", "1")
    intake = ask_meanwhile(nothing, person.asker(), person.waiting(), same,
                           (purpose, seen_faces))
    assert f'Its notes say: "{about}"\n1. {PURPOSE.text}' in person.text
    assert "From the part's notes" not in person.text
    assert intake.answers[0] == Answer(choice=1), "the answer given, whatever the words said"
    assert intake.questions[0].beside == about and not intake.over_notes()


def test_a_purpose_in_words_alone_is_asked(tmp_path: Path) -> None:
    purpose, _ = two(tmp_path, {"purpose": {"for": "holds a heavy made-up shelf"}})
    assert purpose.noted is None and purpose.beside == "holds a heavy made-up shelf"
    plain, _ = two(tmp_path, {"purpose": "holds a heavy made-up shelf"})
    assert plain == purpose


# --------------------------------------------------------------------------
# An answer that contradicts the notes
# --------------------------------------------------------------------------


def test_a_contradicting_answer_names_both_before_its_taken_and_the_record_has_both(
    tmp_path: Path, job: Job
) -> None:
    person = Person("2", "4", "")
    row = job.run(noted(tmp_path, BOTH), no_question, person.asker(), person.waiting())

    prompt, shown = person.prompts[2]
    before_taken = "\n".join(person.shown[:shown])
    theirs, yours = "the top as it prints and the face on the plate", SEEN.options[3].label
    assert prompt == DIFFERS_PROMPT
    assert f"They say seen: {theirs}. You said: {yours}." in before_taken
    assert "Enter takes your answer, or n keeps the notes." in before_taken

    assert row.intake is not None
    question, answer = row.intake.questions[1], row.intake.answers[1]
    assert question.noted is not None and question.options[question.noted].label == theirs
    assert question.label(answer) == yours and question.noted_as == "top, bottom"
    assert f"your answer, in place of the notes' '{theirs}'" in describe(row)
    assert "the notes" in desk_questions(row) and yours in desk_questions(row)
    [request] = job.reviews.requests
    aside = f"seen: top, bottom, as the part's context says (set aside: you said '{yours}')"
    assert aside in request.context and f"  {aside}" in render_review(request)
    assert not [line for line in request.context if line.endswith("context says")], (
        "none reads as what's being judged")
    assert aside in job.settings_prompts[0]
    assert f"[your answer, in place of the notes' '{theirs}']" in job.settings_prompts[0]


def test_n_keeps_the_notes_and_enter_at_the_question_does_too(tmp_path: Path) -> None:
    for lines in (("2", "4", "n"), ("2", "")):
        person = Person(*lines)
        answers = cli_asker(person.read, person.write)(two(tmp_path, BOTH))
        assert answers == tuple(q.confirm() for q in two(tmp_path, BOTH))


def test_what_isnt_named_to_change_isnt_asked_again(tmp_path: Path) -> None:
    person = Person("1", "1", "")
    answers = cli_asker(person.read, person.write)(two(tmp_path, BOTH))
    assert answers == (Answer(choice=0), two(tmp_path, BOTH)[1].confirm())
    assert [prompt for prompt, _ in person.prompts] == [NOTES_PROMPT, "1> ", DIFFERS_PROMPT]
    assert SEEN.text not in person.text


def test_a_line_set_aside_compares_to_past_prints_as_the_notes_said_it(tmp_path: Path) -> None:
    context = read_context(noted(tmp_path, BOTH))
    changed = tuple(context.lines({"seen": "only the face on the plate"}))
    plain = tuple(context.lines())
    assert changed != plain
    assert likeness({}, {}, plain, changed, ("", "a.stl"), ("", "b.stl")).context == 1.0


def test_changing_a_line_says_what_the_notes_say_on_it_with_quotes_for_about_alone(
    tmp_path: Path,
) -> None:
    person = Person("1 2", "", "")
    cli_asker(person.read, person.write)(two(tmp_path, BOTH))
    assert "The notes say for: a made-up bracket for a shelf. Carries a real load\n" in person.text
    assert "The notes say seen: the top and the bottom, as the file lays it\n" in person.text
    assert "Its notes say" not in person.text and "Enter) keep what the notes say" in person.text


def test_both_can_be_changed_at_the_one_prompt(tmp_path: Path) -> None:
    person = Person("1 2", "4", "", "o only the hook", "")
    answers = cli_asker(person.read, person.write)(two(tmp_path, BOTH))
    assert answers == (Answer(choice=3), Answer(other="only the hook"))
    assert person.text.count("That differs from the notes.") == 2


def test_words_in_place_of_the_notes_faces_judge_none_and_not_theirs(
    tmp_path: Path, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    given: list[tuple[Any, Any]] = []

    def watched(reading: Any, faces: Any, *rest: Any) -> Any:
        given.append((faces, rest[2]))
        return flags(reading, faces, *rest)

    monkeypatch.setattr(graph, "flags", watched)
    person = Person("2", "o only the hook", "")
    job.run(noted(tmp_path, BOTH), no_question, person.asker(), person.waiting())
    faces, placed = given[0]
    assert faces is None and placed.seen is None


def test_the_answer_wins_over_the_notes_at_the_reading(tmp_path: Path) -> None:
    questions = two(tmp_path, BOTH)
    faces = seen(Intake(questions=questions, answers=(Answer(choice=2), Answer(choice=3))))
    assert faces is not None and (faces.top, faces.bottom) == (False, True)


# --------------------------------------------------------------------------
# Seen from the notes: any combination
# --------------------------------------------------------------------------


def test_top_and_bottom_both_seen_survive_to_the_reviews_reading(
    tmp_path: Path, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    given: list[tuple[Any, Any]] = []

    def watched(reading: Any, faces: Any, *rest: Any) -> Any:
        given.append((faces, rest[2]))
        return flags(reading, faces, *rest)

    monkeypatch.setattr(graph, "flags", watched)
    person = Person("")
    job.run(noted(tmp_path, BOTH), no_question, person.asker(), person.waiting())

    faces, placed = given[0]
    read = faces or placed.seen
    assert (read.walls, read.top, read.bottom) == (False, True, True)
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    found = {f.id for f in flags(ironed, faces, PROCESS, FILAMENT, placed)}
    assert "ironing_hidden" not in found and "seam_seen" not in found


def test_the_notes_reason_is_the_notes_and_not_what_you_said(tmp_path: Path) -> None:
    stl = noted(tmp_path, {"seen": ["walls"]})
    ironed = _read(tmp_path, layers=20, ironed=(20,), config=PETG)
    found = flags(ironed, None, PROCESS, FILAMENT, place(read_context(stl), stl, LAID))
    [hidden] = [f for f in found if f.id == "ironing_hidden"]
    assert hidden.change is not None
    assert hidden.change.reason.startswith("the part's notes say 'walls")


@pytest.mark.parametrize("faces", [
    [], ["walls"], ["top"], ["bottom"], ["walls", "top"], ["walls", "bottom"],
    ["top", "bottom"], ["walls", "top", "bottom"],
])
@pytest.mark.parametrize("pose", [LAID, FLIPPED, ON_ITS_SIDE])
def test_every_combination_is_read_back_and_read_as_the_notes_have_it(
    faces: list[str], pose: tuple[float, float, float], tmp_path: Path
) -> None:
    stl = noted(tmp_path, {"seen": faces})
    context = read_context(stl)
    question = stock(context, pose)[1]
    assert question.noted is not None, "read back, never asked"
    assert question.noted_as == (", ".join(faces) or "nothing")
    assert "as the file lays it" in question.read_back()
    intake = Intake(questions=(question,), answers=(question.confirm(),))
    assert seen(intake) is None, "read as the notes place it, not by the label in this pose"
    placed = place(context, stl, pose).seen
    assert placed is not None
    if pose == LAID:
        assert (placed.walls, placed.top, placed.bottom) == (
            "walls" in faces, "top" in faces, "bottom" in faces)


def test_turned_over_the_read_back_says_where_those_faces_are_as_it_prints(
    tmp_path: Path,
) -> None:
    _, question = two(tmp_path, {"seen": ["top"]}, FLIPPED)
    assert question.read_back() == (
        "the top, as the file lays it (turned 180,0,0, so only the face on the plate)")
    assert question.noted == 3


# --------------------------------------------------------------------------
# What the model is given
# --------------------------------------------------------------------------

EVERYTHING: dict[str, Any] = {
    "fits": [{"name": "skirt over the base", "play": 0.1, "z": [0, 5]}],
    "seen": ["top", "bottom"],
    "down": "+z",
    "holds": [{"name": "snaps on", "way": "+z"}],
    "about": "printed twice before, made up",
    "purpose": FOR,
}


def asked_of_model(
    stl: Path, context: PartContext | None, reply: dict[str, Any] | None = None
) -> tuple[str, dict[str, Any], Asked]:
    sent: list[tuple[str, dict[str, Any]]] = []

    def ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        sent.append((user, schema))
        return reply or {"questions": []}

    asked = Proposer(ask_questions=ask).questions(
        measure(stl, 400.0), STORE_PROFILES, context=context)
    [(user, schema)] = sent
    return user, schema, asked


def subjects(schema: dict[str, Any]) -> list[str]:
    item = schema["schema"]["properties"]["questions"]["items"]
    found: list[str] = item["properties"]["feature"]["enum"]
    return found


@pytest.mark.parametrize("part", [
    "fit: a 0.1 mm fit (`skirt over the base`)",
    "seen: top, bottom",
    "laid on its +z side",
    "hold: a hold (`snaps on`), sliding along +z",
    "about: printed twice before, made up",
    "purpose: a made-up bracket for a shelf; loaded as 'carries a real load'",
    "ask only what it leaves open",
])
def test_the_questions_prompt_holds_each_part_of_the_context(part: str, tmp_path: Path) -> None:
    stl = noted(tmp_path, EVERYTHING)
    user, _, _ = asked_of_model(stl, read_context(stl))
    assert part in user


@pytest.mark.parametrize("meanwhile", [True, False])
def test_the_job_gives_its_questions_call_the_context(
    meanwhile: bool, tmp_path: Path, job: Job
) -> None:
    sent: list[str] = []
    told = threading.Event()

    def questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        sent.append(user)
        told.set()
        return {"questions": []}

    def confirms(asked: Sequence[Question]) -> tuple[Answer, ...]:
        assert told.wait(WAIT_S), "the call is made before anything is confirmed"
        return tuple(q.confirm() for q in asked)

    job.run(noted(tmp_path, EVERYTHING), questions, confirms,
            Person().waiting() if meanwhile else None)
    assert "about: printed twice before, made up" in sent[0]


def test_with_purpose_in_the_notes_the_model_cant_ask_it(tmp_path: Path) -> None:
    stl = noted(tmp_path, EVERYTHING)
    reply = {"questions": [{"feature": "purpose", "text": "A made-up cover?", "options": [
        {"label": "a", "settings": [], "impact": "i", "recommended": True, "why": "w"},
        {"label": "b", "settings": [], "impact": "i", "recommended": False, "why": ""}]}]}
    _, schema, asked = asked_of_model(stl, read_context(stl), reply)
    assert "purpose" not in subjects(schema)
    assert asked.closer is None and "the part's notes say what it's for" in asked.note


# --------------------------------------------------------------------------
# A load in the notes, and no notes at all
# --------------------------------------------------------------------------


def test_a_load_in_the_notes_is_strengthened_as_the_same_answer_is(tmp_path: Path) -> None:
    def changes(folder: str, data: dict[str, Any] | None, *typed: str) -> list[tuple[str, str]]:
        home = tmp_path / folder
        home.mkdir()
        job, person = Job(home), Person(*typed)
        row = job.run(noted(home, data), no_question, person.asker(), person.waiting())
        job.ledger.close()
        assert row.proposal is not None
        return sorted((c.key, c.value) for c in row.proposal.changes)

    from_notes = changes("notes", BOTH, "")
    answered = changes("answered", None, "3", "1")
    assert ("wall_loops", "4") in from_notes
    assert from_notes == answered


def test_with_no_notes_the_questions_are_todays(tmp_path: Path) -> None:
    stl = noted(tmp_path)
    context = read_context(stl)
    assert stock(context, FLIPPED) == STOCK and stock(None, LAID) == STOCK
    user, schema, asked = asked_of_model(stl, context)
    assert (user, schema) == asked_of_model(stl, None)[:2]
    assert "context" not in user and "purpose" in subjects(schema)
    assert "purpose asked by print-shop: the model didn't" in asked.note

    person = Person("2", "1")
    intake = ask_meanwhile(nothing, person.asker(), person.waiting(), same, stock(context, LAID))
    assert render(STOCK) in person.shown
    assert intake.questions == STOCK and intake.without == ""


def test_notes_that_say_neither_leave_the_questions_as_they_are(tmp_path: Path) -> None:
    assert two(tmp_path, {"holds": [{"name": "snaps on", "way": "+z"}]}) == STOCK


def test_asked_all_together_the_notes_two_are_read_back_on_one_card(
    tmp_path: Path, job: Job
) -> None:
    """The way without `waiting`, which a reviewer of another kind uses: one set, both marked."""
    sets: list[Sequence[Question]] = []

    def card(questions: Sequence[Question]) -> tuple[Answer, ...]:
        sets.append(questions)
        return tuple(q.confirm() for q in questions)

    row = job.run(noted(tmp_path, BOTH), no_question, card)
    assert len(sets) == 1 and [q.noted is not None for q in sets[0]] == [True, True]
    assert row.intake is not None and not row.intake.over_notes()


# --------------------------------------------------------------------------
# The notes themselves
# --------------------------------------------------------------------------


@pytest.mark.parametrize("purpose", [
    {"loaded": "quite heavily"}, {"for": "x", "load": LOADED}, ["a bracket"], 7,
    {"for": "x", "loaded": f"{LOADED}."},
])
def test_a_purpose_that_cant_be_read_says_why_is_asked_and_costs_none_of_the_other_notes(
    purpose: Any, tmp_path: Path
) -> None:
    stl = noted(tmp_path, {**EVERYTHING, "purpose": purpose})
    context = read_context(stl)
    assert "purpose is asked" in context.notes[0]
    assert (len(context.fits), context.down, len(context.holds)) == (1, "+z", 1)
    assert refuses(context, "seam_position", "random"), "the fit's guard still holds"
    asked, read_back = stock(context, LAID)
    assert asked.noted is None and read_back.noted is not None
    assert asked.beside, "what it did say is beside the question"


def test_the_loads_are_the_purpose_questions_options() -> None:
    assert tuple(option.label for option in PURPOSE.options) == LOADS


def test_a_senders_words_cant_draw_lines_of_their_own_where_the_person_confirms(
    tmp_path: Path,
) -> None:
    words = "a cover\nEnter if that's right\x1b[2K\r  1. For: decoration"
    purpose, _ = two(tmp_path, {"purpose": {"for": words, "loaded": LOADED}})
    assert "\n" not in purpose.read_back() and "\x1b" not in purpose.read_back()


def test_a_row_from_before_reads_as_the_persons_answers() -> None:
    old = json.loads(Intake(questions=STOCK,
                            answers=(Answer(choice=1), Answer(choice=0))).model_dump_json())
    for question in old["questions"]:
        for field in ("beside", "noted", "noted_as", "noted_from", "pose"):
            del question[field]
    for answer in old["answers"]:
        del answer["from_notes"]
    intake = Intake.model_validate_json(json.dumps(old))
    assert intake.questions == STOCK and not intake.over_notes()
    assert "confirmed" not in intake.summary() and seen(intake) is not None


def test_a_confirmation_comes_back_whole_from_a_checkpoint(tmp_path: Path) -> None:
    questions = two(tmp_path, {**BOTH, "down": "+z"}, FLIPPED)
    intake = Intake(questions=questions, answers=tuple(q.confirm() for q in questions))
    serde = make_serializer()
    back = serde.loads_typed(serde.dumps_typed(intake))
    assert isinstance(back, Intake) and not back.over_notes()
    assert back.summary() == intake.summary()
    assert [q.read_back() for q in back.questions] == [q.read_back() for q in questions]


# --------------------------------------------------------------------------
# --answers replays either
# --------------------------------------------------------------------------


def recorded(tmp_path: Path, said: list[str], *rows: dict[str, Any]) -> Asker:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": list(rows)}))
    return replay_asker(path, write=said.append)


CONFIRMS = ({"question": "purpose", "notes": f"{FOR['for']}: {LOADED}"},
            {"question": "seen", "notes": "top, bottom"})


def test_a_recorded_confirmation_replays_without_waiting_for_the_model(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    said: list[str] = []
    person = Person()
    intake = ask_meanwhile(forever, recorded(tmp_path, said, *CONFIRMS),
                           person.waiting(replayed=True), same, two(tmp_path, BOTH))
    assert not person.prompts and not said
    assert not intake.over_notes() and len(intake.answers) == 2
    assert intake.call["finish_reason"] == "not waited"


def test_a_recorded_answer_over_the_notes_replays_and_names_both(
    tmp_path: Path, forever: Callable[[threading.Event], Asked]
) -> None:
    said: list[str] = []
    file = recorded(tmp_path, said, CONFIRMS[0], {"question": "seen", "choice": 4})
    intake = ask_meanwhile(forever, file, Person().waiting(replayed=True), same,
                           two(tmp_path, BOTH))
    [line] = said
    assert "the top as it prints and the face on the plate" in line
    assert "You said: only the face on the plate" in line
    assert [q.feature for q, _ in intake.over_notes()] == ["seen"]


@pytest.mark.parametrize(("notes", "says"), [
    ({"purpose": FOR, "seen": ["walls"]}, "they say 'walls' now"),
    ({"purpose": FOR}, "the notes don't give one now"),
    ({"purpose": {"for": "x", "loaded": LOADS[0]}, "seen": ["top", "bottom"]},
     "they say 'x: decoration or display' now"),
    ({"purpose": {**FOR, "for": "a made-up hook a child hangs from"}, "seen": ["top", "bottom"]},
     "they say 'a made-up hook a child hangs from: carries a real load' now"),
])
def test_a_confirmation_never_confirms_notes_that_changed(
    notes: dict[str, Any], says: str, tmp_path: Path
) -> None:
    with pytest.raises(AnswersRefused, match=says):
        recorded(tmp_path, [], *CONFIRMS)(two(tmp_path, notes))


def test_a_file_never_confirms_what_it_doesnt_name(tmp_path: Path) -> None:
    with pytest.raises(AnswersRefused, match="question 2 has no answer"):
        recorded(tmp_path, [], CONFIRMS[0])(two(tmp_path, BOTH))


def test_a_file_that_answers_the_models_question_too_waits_for_it(tmp_path: Path) -> None:
    def slow(stop: threading.Event) -> Asked:
        time.sleep(0.2)
        return Asked((PURPOSE, EDGE, SEEN), "")

    file = recorded(tmp_path, [], *CONFIRMS, {"question": EDGE.text, "choice": "stiff"})
    intake = ask_meanwhile(slow, file, Person().waiting(replayed=True), same,
                           two(tmp_path, BOTH))
    assert intake.questions[2] == EDGE and intake.answers[2] == Answer(choice=1)


@pytest.mark.parametrize("typed", [("",), ("2", "4", "")])
def test_the_desks_answers_file_replays_what_was_confirmed_or_answered(
    typed: tuple[str, ...], tmp_path: Path, job: Job
) -> None:
    stl = noted(tmp_path, BOTH)
    person = Person(*typed)
    row = job.run(stl, no_question, person.asker(), person.waiting())
    content = answers_file(row)
    assert content is not None and row.intake is not None
    assert content["answers"][0] == {"question": PURPOSE.text, "notes": CONFIRMS[0]["notes"]}
    path = tmp_path / "again.json"
    path.write_text(json.dumps(content))
    replayed = replay_asker(path, write=lambda line: None)(stock(read_context(stl), LAID))
    assert replayed == row.intake.answers[:2]

    stl.with_name(stl.stem + SIDECAR).unlink()
    assert any("refused without them" in note for note in again(row, stl, 1, True).notes)


# --------------------------------------------------------------------------
# Not waiting never weakens what waiting held
# --------------------------------------------------------------------------


def test_an_unwaited_job_doesnt_pre_select_a_past_answer_over_the_guard(
    tmp_path: Path,
) -> None:
    """A past "no support" is asked again by each job. One that didn't wait to ask it doesn't
    tick it, so `use` can't turn the guard's supports off unasked."""

    def supports(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [{"feature": "overhang", "text": "The made-up ceiling?", "options": [
            {"label": "no support", "settings": [], "impact": "i", "recommended": True, "why": "w"},
            {"label": "tree supports", "settings": [{"key": "enable_support", "value": "1"}],
             "impact": "i", "recommended": False, "why": ""}]}]}

    stl = write_stl("snap", tmp_path)
    first = Job(tmp_path)
    row = first.run(stl, supports, lambda asked: tuple(Answer(choice=0) for _ in asked))
    assert row.proposal is not None and any(
        c.key == "enable_support" and c.source is Source.ANSWER
        for c in (*row.proposal.changes, *row.proposal.kept))
    first.ledger.close()

    stl.with_name(stl.stem + SIDECAR).write_text(json.dumps(BOTH))
    released = threading.Event()

    def never(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        released.wait(4 * WAIT_S)
        raise RuntimeError("the fake never answers")

    second, person = Job(tmp_path), Person("")
    try:
        second.run(stl, never, person.asker(), person.waiting())
    finally:
        released.set()
        second.ledger.close()
    [request] = second.reviews.requests
    guarded = request.proposal.get("enable_support")
    assert guarded is not None and (guarded.value, guarded.source) == ("1", Source.GUARD)
    assert request.offering is not None
    assert not request.offering.ticked(request.proposal, "0,0,0")
    assert parse_answer("use", request) == (
        "nothing is ticked: name one by its number, such as use 1")
    assert "this job didn't wait for the questions" in " ".join(render_review(request).split())


def test_an_answer_that_says_the_same_is_the_persons_and_not_a_confirmation(
    tmp_path: Path,
) -> None:
    questions = two(tmp_path, {"purpose": FOR, "seen": ["walls", "top"]})
    file = recorded(tmp_path, [], {"question": "purpose", "choice": LOADED},
                    {"question": "seen", "choice": 1})
    intake = Intake(questions=questions, answers=file(questions))
    assert not any(q.confirmed(a) for q, a in zip(questions, intake.answers, strict=True))
    assert not intake.over_notes(), "and it replaced nothing"
    assert "confirmed" not in intake.summary() and "as the notes say too" in intake.summary()
    faces = seen(intake)
    assert faces is not None and not faces.noted, "their own answer is what's read"


@pytest.mark.parametrize("faces", [["top", "bottom"], [], ["walls", "top", "bottom"]])
def test_a_number_never_names_whatever_option_the_notes_make_now(
    faces: list[str], tmp_path: Path
) -> None:
    file = recorded(tmp_path, [], {"question": "purpose", "choice": 3},
                    {"question": "seen", "choice": 5})
    with pytest.raises(AnswersRefused, match="a number can't name it"):
        file(two(tmp_path, {"purpose": FOR, "seen": faces}))


def test_confirmed_faces_are_never_told_in_a_pose_the_part_may_leave(tmp_path: Path) -> None:
    """The reading follows a turn, so the record and the settings call mustn't say "the top as
    it prints" of what's the plate's face by then."""
    stl = noted(tmp_path, {"seen": ["top"]})
    context = read_context(stl)
    questions = stock(context, LAID)
    intake = Intake(questions=questions, answers=(Answer(choice=0), questions[1].confirm()))
    facts = Proposer().facts(intake, measure(stl, 400.0), STORE_PROFILES, context)
    told = settings_prompt(facts, load(groups_for(intake, facts.features)))
    assert "-> the top, as the file lays it (no setting)" in told
    assert "the top as it prints; the walls are hidden" not in told
    assert "the top, as the file lays it" in intake.summary()


def test_a_past_confirmation_isnt_told_as_an_answer_given_then(
    tmp_path: Path, job: Job
) -> None:
    stl = noted(tmp_path, {**BOTH, "about": "made up"})
    person = Person("")
    row = job.run(stl, no_question, person.asker(), person.waiting())
    assert row.intake is not None
    found = _answers_then(row)
    assert found and all("from the part's notes, confirmed" in line for line in found)


def test_questions_already_here_as_they_confirm_are_added_though_still_being_said(
    tmp_path: Path,
) -> None:
    saying = threading.Event()

    def as_its_said(n: int) -> None:
        if n == 1:
            saying.wait(WAIT_S)

    person = Person("", "2", before=as_its_said)
    waiting = person.waiting()

    def slow_to_say(call: Any, asked: Asked) -> None:
        saying.set()
        time.sleep(0.4)

    intake = ask_meanwhile(lambda stop: Asked((PURPOSE, TOP, SEEN), ""), person.asker(),
                           replace(waiting, ended=slow_to_say), same, two(tmp_path, BOTH))
    assert intake.questions[2] == TOP and intake.without == ""


def test_a_replay_keeps_the_record_of_a_call_that_had_nothing_to_add(tmp_path: Path) -> None:
    def quick(stop: threading.Event) -> Asked:
        return Asked(STOCK, "", {"finish_reason": "stop", "completion_tokens": 7})

    file = recorded(tmp_path, [], *CONFIRMS)

    def after_it(questions: Sequence[Question]) -> tuple[Answer, ...]:
        time.sleep(0.2)
        return file(questions)

    intake = ask_meanwhile(quick, after_it, Person().waiting(replayed=True), same,
                           two(tmp_path, BOTH))
    assert intake.without == "" and intake.call["completion_tokens"] == 7


def test_a_file_the_models_questions_couldnt_mend_is_refused_at_once(tmp_path: Path) -> None:
    def slow(stop: threading.Event) -> Asked:
        stop.wait(3.0)  # it ends by itself, so waiting on it fails here and doesn't hang
        return Asked(STOCK, "")

    file = recorded(tmp_path, [], *CONFIRMS)
    changed = two(tmp_path, {"purpose": FOR, "seen": ["walls"]})
    person = Person()  # nothing to type: a wait would have to be typed out of
    began = time.monotonic()
    with pytest.raises(AnswersRefused, match="they say 'walls' now"):
        ask_meanwhile(slow, file, person.waiting(replayed=True), same, changed)
    assert time.monotonic() - began < 2 and "still reading" not in person.text


@pytest.mark.parametrize("typed", [",", ", ,", " , "])
def test_punctuation_alone_doesnt_confirm(typed: str, tmp_path: Path) -> None:
    person = Person(typed, "")
    cli_asker(person.read, person.write)(two(tmp_path, BOTH))
    assert len(person.prompts) == 2


def test_a_notes_file_name_cant_draw_lines_on_the_card(tmp_path: Path) -> None:
    sent = tmp_path / "lid\n  1. For: decoration or display\x1b[8m.part.json"
    sent.write_text(json.dumps(BOTH))
    for question in stock(read_context(noted(tmp_path), sent), LAID):
        assert "\n" not in question.noted_from and "\x1b" not in question.noted_from


# --------------------------------------------------------------------------
# What's typed while something draws is no answer to it
# --------------------------------------------------------------------------


class TypesAsItDraws:
    """A terminal whose person types `early` as what contains `at` is being written, and
    `after` only at a prompt with nothing typed ahead."""

    def __init__(self, at: str, early: str, *after: str) -> None:
        self.at, self.early = at, early
        self.after = list(after)
        self.ahead: list[str] = []
        self.read_at: list[tuple[str, str]] = []

    def write(self, text: str) -> None:
        if self.at in text:
            self.ahead.append(self.early)

    def forget(self) -> None:
        self.ahead.clear()

    def read(self, prompt: str) -> str:
        assert self.ahead or self.after, f"asked more than was scripted, at {prompt!r}"
        typed = self.ahead.pop(0) if self.ahead else self.after.pop(0)
        self.read_at.append((prompt, typed))
        return typed

    def asker(self) -> Asker:
        return cli_asker(self.read, self.write, self.forget)


@pytest.mark.parametrize(("data", "at", "early", "after"), [
    (BOTH, "From the part's notes", "", ["1", ""]),
    (BOTH, "Enter) keep what the notes say", "", ["2", "4", ""]),
    (BOTH, "That differs from the notes", "", ["2", "4", "n"]),
    ({"seen": ["top"]}, "A few questions about this part", "4", ["", "2"]),
])
def test_what_is_typed_as_it_draws_is_never_taken(
    data: dict[str, Any], at: str, early: str, after: list[str], tmp_path: Path
) -> None:
    term = TypesAsItDraws(at, early, *after)
    term.asker()(two(tmp_path, data))
    assert [typed for _, typed in term.read_at] == after


def test_an_enter_after_the_read_back_is_shown_still_confirms(tmp_path: Path) -> None:
    term = TypesAsItDraws("nothing that's written", "", "")
    questions = two(tmp_path, BOTH)
    assert term.asker()(questions) == tuple(question.confirm() for question in questions)


def test_what_is_typed_as_an_added_question_draws_doesnt_answer_it() -> None:
    term = TypesAsItDraws("Added by the model", "1", "2")
    assert term.asker()(Added((TOP,), before=2)) == (Answer(choice=1),)


def test_an_enter_typed_as_the_closer_choices_draw_doesnt_keep_the_answer() -> None:
    term = TypesAsItDraws("closer choices", "", "2")
    waiting = cli_waiting(term.read, term.write, screen=io.StringIO, forget=term.forget)
    assert waiting.closer is not None
    assert waiting.closer(PURPOSE, Answer(choice=0), 1.0) == Answer(choice=1)


# --------------------------------------------------------------------------
# The real terminal: a spare Enter confirms nothing
# --------------------------------------------------------------------------

_TERMINAL = textwrap.dedent("""
    import sys, time
    from pathlib import Path

    from print_shop.setup.context import read_context
    from print_shop.setup.intake import cli_asker
    from print_shop.setup.questions import stock

    folder = Path(sys.argv[1])
    questions = stock(read_context(folder / "cube.stl"), (0.0, 0.0, 0.0))
    print("READY", flush=True)
    while not (folder / "release").exists():
        time.sleep(0.02)
    print("ANSWERS", [a.choice for a in cli_asker()(questions)], flush=True)
""")


def test_on_a_terminal_an_enter_typed_ahead_never_confirms_notes_unseen(tmp_path: Path) -> None:
    noted(tmp_path, BOTH)
    script = tmp_path / "terminal.py"
    script.write_text(_TERMINAL)
    master, slave = pty.openpty()
    child = subprocess.Popen([sys.executable, str(script), str(tmp_path)],
                             stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    shown = ""

    def until(text: str) -> None:
        nonlocal shown
        give_up = time.monotonic() + WAIT_S
        while text not in shown:
            assert time.monotonic() < give_up, f"{text!r} never showed in {shown!r}"
            if select.select([master], [], [], 0.2)[0]:
                shown += os.read(master, 65536).decode(errors="replace")

    try:
        until("READY")
        os.write(master, b"\n")
        time.sleep(0.2)
        (tmp_path / "release").write_text("")
        until(NOTES_PROMPT)
        time.sleep(0.5)
        assert not select.select([master], [], [], 0.3)[0] and "ANSWERS" not in shown
        os.write(master, b"\n")
        until("ANSWERS [2, 4]")
    finally:
        child.kill()
        child.wait(WAIT_S)
        os.close(master)
