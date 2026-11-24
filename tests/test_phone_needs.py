"""What a job needs, answered on the phone: the questions, the review, a change, a look and the
mesh gate (mqucifer/print-shop#221's second PR).

The phone runs the terminal's own asker and reviewer, with what they read from its forms
(`phone/session.py`). So these tests compare the phone's answers with the terminal's for the same
words. The queue here is a fake one whose asks are the real `Asked`, answered once, over made-up
jobs, questions and reviews (`tests/review_made_up.py`); the last test takes a file from sent to
ready on the real queue's made-up shop. No test arms, and no request but Start's reaches the gate.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from print_shop.orchestrator.kept import WaitingJob, Waits
from print_shop.orchestrator.print_queue import Adding, Asked, Line, Need
from print_shop.phone.needs import REVIEW_WIDTH
from print_shop.phone.phones import Phones
from print_shop.phone.server import handler_for
from print_shop.phone.session import Session
from print_shop.phone.shop import Shop
from print_shop.setup.intake import Answer, Option, Question, cli_asker
from print_shop.setup.review import Review, parse_answer, render
from print_shop.setup.screen import sections
from tests.review_made_up import made_up
from tests.test_phone_start import Browser, FakeStarts, Reply, home, signed_in
from tests.test_queue import Shop as QueueShop
from tests.test_queue import wait_until

# --------------------------------------------------------------------------
# A made-up queue whose asks are real
# --------------------------------------------------------------------------

PURPOSE = Question(feature="purpose", text="What's it for?", options=(
    Option(label="Decoration", settings={}, impact="looks only"),
    Option(label="Carries a light load", settings={}, impact="a little stronger",
           recommended=True, why="it's a bracket"),
))
SUPPORT = Question(feature="overhang", text="The underside overhangs. Support it?", options=(
    Option(label="Tree supports", settings={"enable_support": "1"}, impact="a clean underside"),
    Option(label="No supports", settings={"enable_support": "0"}, impact="it may sag",
           recommended=True, why="the overhang is short"),
))


def job(job_id: str, part: str) -> WaitingJob:
    return WaitingJob(job_id=job_id, part=part, given={}, waits=Waits.QUESTIONS, spool_id=1)


@dataclass
class FakeQueue:
    """The queue's reads and `give`, over made-up jobs: each ask is the real `Asked`."""

    jobs: list[WaitingJob]
    asks: dict[str, Asked] = field(default_factory=dict)
    given: list[tuple[str, Any]] = field(default_factory=list)
    alike_given: list[tuple[list[str], tuple[Answer, ...]]] = field(default_factory=list)
    model_reading: float | None = None

    def lines(self) -> list[Line]:
        out = []
        for n, j in enumerate(self.jobs, 1):
            asked = self.asked(j.job_id)
            out.append(Line(number=n, job=j, step="needs you",
                            needs=asked.need if asked is not None else None, offered=False,
                            spool="", loaded=True))
        return out

    def sent(self) -> list[Any]:
        return []

    def find(self, named: str) -> WaitingJob | None:
        return next((j for j in self.jobs if j.job_id.startswith(named)), None)

    def asked(self, job_id: str) -> Asked | None:
        found = self.asks.get(job_id)
        return found if found is not None and found.open else None

    def give(self, job_id: str, answer: Any) -> str:
        found = self.asked(job_id)
        if found is None:
            return "it isn't waiting for you"
        if not found._give(answer):
            return "it was answered already"
        self.given.append((job_id, answer))
        return ""

    def give_alike(self, job_ids: Sequence[str], questions: Sequence[Question],
                   answers: Sequence[Answer]) -> list[str]:
        self.alike_given.append((list(job_ids), tuple(answers)))
        return [f"{j}: the same answers" for j in job_ids]

    def go(self, job_id: str) -> str:
        return ""

    def reading(self, job_id: str) -> float | None:
        return self.model_reading

    def hold(self, job_id: str, held: bool = True) -> str:
        return ""

    def first(self, job_id: str) -> str:
        return ""

    def remove(self, job_id: str) -> str:
        return ""

    def move(self, job_id: str, spool_id: int) -> list[str] | str:
        return []

    def add(self, adding: Sequence[Adding]) -> tuple[list[str], list[str]]:
        return [], []


@dataclass
class Rig:
    queue: FakeQueue
    shop: Shop
    handler: type
    phones: Phones
    starts: FakeStarts

    def phone(self) -> Browser:
        return signed_in(self.handler, self.phones)


def rig(tmp_path: Path, asks: dict[str, Asked], parts: dict[str, str] | None = None) -> Rig:
    names = parts or {job_id: f"{job_id}.stl" for job_id in asks}
    queue = FakeQueue(jobs=[job(j, p) for j, p in names.items()], asks=dict(asks))
    shop = Shop(tmp_path / "ledger.sqlite", queue=queue)
    phones = Phones(tmp_path / "phones.sqlite")
    starts = FakeStarts()
    return Rig(queue, shop, handler_for(home=home(), phones=phones, starts=starts, shop=shop),
               phones, starts)


def ticked(raw: str) -> list[str]:
    """The names of what's pre-selected on a page."""
    return re.findall(r'<input type="(?:radio|checkbox)" name="([^"]+)"[^>]*\bchecked\b', raw)


def since(asked: Asked) -> str:
    return repr(asked.since)


def follow(phone: Browser, path: str) -> Reply:
    reply = phone.get(path)
    return reply


def version(reply: Reply) -> str:
    found = re.search(r'name="version" value="(\d+)"', reply.raw)
    assert found is not None, reply.body
    return found.group(1)


def terminal(questions: Sequence[Question], typed: Sequence[str]) -> tuple[Answer, ...]:
    """The terminal's answers to `questions` for what's typed at its prompts, in turn."""
    lines = iter(typed)
    return cli_asker(read=lambda prompt: next(lines), write=lambda text: None,
                     forget=lambda: None, width=lambda: 60)(questions)


# --------------------------------------------------------------------------
# The questions
# --------------------------------------------------------------------------


def test_the_questions_preselect_nothing_as_enter_takes_nothing_there(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE, SUPPORT))
    r = rig(tmp_path, {"j1": asked})
    shown = r.phone().get("/job/j1/questions")
    assert shown.status == 200
    assert "What's it for?" in shown.body and "The underside overhangs. Support it?" in shown.body
    assert "recommended" in shown.body and "it's a bracket" in shown.body
    assert ticked(shown.raw) == [], "Enter takes nothing at a question: nothing is picked"
    assert "Answer" in r.phone().get("/").body
    assert 'href="/job/j1/questions"' in r.phone().get("/").raw


def test_a_form_gives_what_the_same_numbers_give_at_the_terminal(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE, SUPPORT))
    r = rig(tmp_path, {"j1": asked})
    done = r.phone().post("/job/j1/questions", {"since": since(asked), "q0": "2", "q1": "1"})
    assert done.status == 303, done.body
    wait_until(lambda: bool(r.queue.given))
    (job_id, answers), = r.queue.given
    assert job_id == "j1" and answers == terminal((PURPOSE, SUPPORT), ["2", "1"])


def test_a_question_left_unanswered_is_refused_and_nothing_given(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE, SUPPORT))
    r = rig(tmp_path, {"j1": asked})
    reply = r.phone().post("/job/j1/questions", {"since": since(asked), "q0": "2"})
    assert reply.status == 400 and "Support it?" in reply.body
    assert r.queue.given == [] and asked.open


def test_own_words_are_read_back_and_confirmed_once_from_the_form(tmp_path: Path) -> None:
    """mqucifer/print-shop#213's read-back on the phone: its yes is a form's, once."""
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE, SUPPORT))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    done = phone.post("/job/j1/questions", {"since": since(asked), "q0": "1", "q1": "o",
                                             "w1": "no supports please"})
    assert done.status == 303
    shown = phone.get(done.header("Location"))
    assert "So supports off" in shown.body and "no supports please" in shown.body
    assert "Yes, that's my decision" in shown.body and r.queue.given == []
    v = version(shown)
    assert phone.post("/job/j1/reply", {"version": str(int(v) + 1), "typed": "y"}).status == 409
    took = phone.post("/job/j1/reply", {"version": v, "typed": "y"})
    assert took.status == 303
    wait_until(lambda: bool(r.queue.given))
    answers = r.queue.given[0][1]
    assert answers == terminal((PURPOSE, SUPPORT), ["1", "o no supports please", "y"])
    assert answers[1].sets == {"enable_support": "0"}
    again = phone.post("/job/j1/reply", {"version": v, "typed": "y"})
    assert again.status == 409 and len(r.queue.given) == 1, "a form sent again confirms nothing"


def test_a_later_read_back_isnt_confirmed_by_an_earlier_form(tmp_path: Path) -> None:
    words = Question(feature="brim", text="Its edge lifts. A brim?", options=(
        Option(label="A brim", settings={"brim_type": "outer_only"}, impact="it holds"),
        Option(label="No brim", settings={"brim_type": "no_brim"}, impact="clean edge")))
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(SUPPORT, words))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    phone.post("/job/j1/questions", {"since": since(asked), "q0": "o", "w0": "no supports",
                                     "q1": "o", "w1": "no brim"})
    first = phone.get("/job/j1/questions")
    v1 = version(first)
    phone.post("/job/j1/reply", {"version": v1, "typed": "y"})
    second = phone.get("/job/j1/questions")
    assert "So no brim" in second.body or "brim" in second.body
    replayed = phone.post("/job/j1/reply", {"version": v1, "typed": "y"})
    assert replayed.status == 409, "the first read-back's yes isn't the second's"
    assert r.queue.given == []
    phone.post("/job/j1/reply", {"version": version(second), "typed": "n"})
    later = phone.get("/job/j1/questions")
    assert "set nothing" in later.body


def test_what_the_notes_give_is_read_back_first_and_thats_right_confirms_it(
    tmp_path: Path,
) -> None:
    noted = SUPPORT.model_copy(update={"noted": 1, "noted_from": "bracket.md",
                                       "noted_as": "no supports"})
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(noted,))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    shown = phone.get("/job/j1/questions")
    assert "From bracket.md" in shown.body and "No supports" in shown.body
    assert "That's right" in shown.body
    phone.post("/job/j1/questions", {"since": since(asked)})
    wait_until(lambda: bool(r.queue.given))
    assert r.queue.given[0][1] == (noted.confirm(),) == terminal((noted,), [""])


def test_a_note_changed_is_that_question_alone(tmp_path: Path) -> None:
    noted = SUPPORT.model_copy(update={"noted": 1, "noted_from": "bracket.md"})
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE, noted))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    changing = phone.get("/job/j1/questions?change=1")
    assert "The underside overhangs" in changing.body and ticked(changing.raw) == []
    phone.post("/job/j1/questions", {"since": since(asked), "change": "1", "q0": "1",
                                     "q1": "1"})
    shown = phone.get("/job/j1/questions")
    if r.queue.given == []:
        # The terminal names both before it takes an answer that differs from the notes.
        assert "Take my answer" in shown.body
        phone.post("/job/j1/reply", {"version": version(shown), "typed": ""})
    wait_until(lambda: bool(r.queue.given))
    # At the Mac: 2 at notes> changes it, 1 answers it, Enter takes it over the notes', then
    # 1 answers the question the notes left.
    assert r.queue.given[0][1] == terminal((PURPOSE, noted), ["2", "1", "", "1"])


def test_the_same_answers_go_to_the_files_alike_as_answer_n_m_gives_them(tmp_path: Path) -> None:
    asks = {j: Asked(job_id=j, need=Need.QUESTIONS, questions=(PURPOSE,))
            for j in ("j1", "j2", "j3")}
    asks["j4"] = Asked(job_id="j4", need=Need.QUESTIONS, questions=(SUPPORT,))
    r = rig(tmp_path, asks, {"j1": "bracket.stl", "j2": "hook.stl", "j3": "clip.stl",
                             "j4": "lid.stl"})
    phone = r.phone()
    shown = phone.get("/job/j1/questions")
    assert "Same answers for hook.stl and clip.stl" in shown.body and "lid.stl" not in shown.body
    assert ticked(shown.raw) == ["alike"], "ticked, as print-shop-ux's screen has it"
    phone.post("/job/j1/questions", {"since": since(asks["j1"]), "q0": "1",
                                     "alike": "j2 j3 j4 made-up"})
    wait_until(lambda: bool(r.queue.alike_given))
    assert r.queue.alike_given[0][0] == ["j2", "j3"], "only the jobs offered"


def test_a_stale_form_changes_nothing(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE,))
    r = rig(tmp_path, {"j1": asked})
    reply = r.phone().post("/job/j1/questions", {"since": "1.0", "q0": "1"})
    assert reply.status == 409 and "out of date" in reply.body
    assert r.queue.given == [] and asked.open


def test_one_answerer_at_a_time_the_terminal_first(tmp_path: Path) -> None:
    """The operator: an answer from one can't land on a prompt the other moved past."""
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(SUPPORT,))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    phone.post("/job/j1/questions", {"since": since(asked), "q0": "o", "w0": "no supports"})
    v = version(phone.get("/job/j1/questions"))
    at_the_mac = terminal((SUPPORT,), ["1"])
    assert r.queue.give("j1", at_the_mac) == ""
    late = phone.post("/job/j1/reply", {"version": v, "typed": "y"})
    assert late.status in (303, 409)
    time.sleep(0.2)
    assert [a for _, a in r.queue.given] == [at_the_mac], "the Mac's answer alone stands"
    session = r.shop.sessions.get("j1")
    if session is not None:
        wait_until(lambda: session.ended, 5.0)
        assert session.said == "it was answered already" or "put by" in session.said


def test_a_phone_that_walks_away_holds_nothing(tmp_path: Path) -> None:
    """The operator: no answer from the phone hangs nothing, and the Mac still answers."""
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(SUPPORT,))
    r = rig(tmp_path, {"j1": asked})
    queue = r.queue
    session = Session("j1", asked.since, lambda read, write: cli_asker(
        read=read, write=write, forget=lambda: None)((SUPPORT,)),
        lambda answers: queue.give("j1", answers),
        alive=lambda: queue.asked("j1") is asked, idle_s=0.5)
    session.start({})
    session.settle(5.0)
    assert session.waiting
    wait_until(lambda: session.ended, 5.0)
    assert "put by" in session.said and queue.given == [] and asked.open
    assert queue.give("j1", terminal((SUPPORT,), ["2"])) == "", "the Mac answers it"
    wait_until(lambda: not session._thread.is_alive(), 5.0)


def test_the_need_moving_on_ends_the_phones_wait(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(SUPPORT,))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    phone.post("/job/j1/questions", {"since": since(asked), "q0": "o", "w0": "no supports"})
    session = r.shop.sessions["j1"]
    assert session.waiting
    asked._cancel()  # put by at the Mac: the queue's own end of an ask
    wait_until(lambda: session.ended, 5.0)
    assert "put by" in session.said


def test_the_models_wait_is_said_as_of_the_page_and_go_is_the_queues(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.QUESTIONS, questions=(PURPOSE,))
    r = rig(tmp_path, {"j1": asked})
    r.queue.model_reading = 125.0
    shown = r.phone().get("/job/j1/questions")
    assert "The model is still reading the part (reading since" in shown.body
    assert "Check again" in shown.body and "Done, go on without waiting" in shown.body
    assert "http-equiv" not in shown.raw, "no refresh on a form page"
    assert r.phone().post("/job/j1/go", {"go": "1"}).status == 303


def test_the_models_closer_choices_keep_your_answer_as_enter_does(tmp_path: Path) -> None:
    kept = Answer(other="it holds the shelf")
    asked = Asked(job_id="j1", need=Need.CLOSER, questions=(PURPOSE,), kept=kept, seconds=30.0)
    r = rig(tmp_path, {"j1": asked})
    shown = r.phone().get("/job/j1/questions")
    assert "Keep your answer" in shown.body and ticked(shown.raw) == ["closer"]
    r.phone().post("/job/j1/questions", {"since": since(asked), "closer": ""})
    wait_until(lambda: bool(r.queue.given))
    assert r.queue.given[0][1] is None, "Enter keeps the answer given: None"


# --------------------------------------------------------------------------
# The review
# --------------------------------------------------------------------------


def _words(text: str) -> list[str]:
    """The words and figures of a text, as facts: case and the punctuation after them aside."""
    return [w.lower().rstrip(".") for w in
            re.findall(r"[A-Za-z0-9][A-Za-z0-9.%°³²/'_-]*", html.unescape(text))]


def request() -> Any:
    """The made-up review, fitting as posed: no turn waits for `turn` before ok."""
    found = made_up()
    return replace(found, slice=found.slice.model_copy(update={"turn_deg": 0.0}))


def test_every_fact_in_the_terminals_review_is_on_the_phone(tmp_path: Path) -> None:
    """Done when: every fact in the terminal's review can be read on the phone."""
    review = made_up()
    asked = Asked(job_id="j1", need=Need.REVIEW, review=review)
    r = rig(tmp_path, {"j1": asked})
    shown = r.phone().get("/job/j1/review")
    assert shown.status == 200
    page = " ".join(shown.body.split())
    for section in sections(review, REVIEW_WIDTH):
        for line in section.lines:
            assert " ".join(html.escape(line).split()) in " ".join(shown.raw.split()) or \
                " ".join(line.split()) in page, line
    on_page = set(_words(shown.body))
    # Less what a terminal leaves above its prompt: Look first again, in short, and the answers.
    full = render(review).split("LOOK FIRST, AGAIN")[0]
    missing = sorted({w for w in _words(full) if w not in on_page})
    assert missing == [], f"in the terminal's review and not on the phone: {missing[:20]}"


def test_use_these_preselects_what_the_terminal_does_and_says_why_not(tmp_path: Path) -> None:
    request = made_up()
    asked = Asked(job_id="j1", need=Need.REVIEW, review=request)
    r = rig(tmp_path, {"j1": asked})
    shown = r.phone().get("/job/j1/review")
    assert "Learned from your sample-tray-old.stl, 21 Nov" in shown.body
    assert "Not selected: this tray's slot is 2 mm wider" in shown.body
    use = re.findall(r'name="use" value="(\d+)"( checked)?', shown.raw)
    assert offered_ticks(request) == [n for n, on in use if on]
    assert "Pick a different print" in shown.body and "sample-coaster.stl" in shown.body


def offered_ticks(request: Any) -> list[str]:
    from print_shop.setup.proposal import pose_value  # noqa: PLC0415

    offering = request.offering
    pose = pose_value(request.orientation.rotation_deg)
    return [str(n) for n, item in enumerate(offering.items(), 1)
            if offering.standing(item, request.proposal, pose).selected]


def _given_review(r: Rig, phone: Browser, form: dict[str, str]) -> Review:
    asked = r.queue.asks["j1"]
    done = phone.post("/job/j1/review", {"since": since(asked), **form})
    assert done.status == 303, done.body
    for _ in range(4):
        if r.queue.given:
            break
        shown = phone.get("/job/j1/review")
        if 'name="version"' not in shown.raw:
            time.sleep(0.05)
            continue
        # What the terminal says before it takes it (what's ticked and unused, the model's):
        # answered as at the Mac, ok again.
        phone.post("/job/j1/reply", {"version": version(shown), "typed": form.get("do", "ok")
                                     if form.get("do") == "ok" else "ok"})
    wait_until(lambda: bool(r.queue.given))
    found = r.queue.given[0][1]
    assert isinstance(found, Review)
    return found


def test_use_these_is_the_terminals_use(tmp_path: Path) -> None:
    review = request()
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    given = _given_review(r, r.phone(), {"do": "use", "use": "2"})
    expected = parse_answer("use 2", review)
    assert isinstance(expected, Review)
    assert given.corrections == expected.corrections


def test_take_and_turn_down_are_the_terminals(tmp_path: Path) -> None:
    from print_shop.setup.suggest import by_number  # noqa: PLC0415

    review = request()
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    models = sorted(by_number(review.by_model))
    take, down = models[0], models[1]
    given = _given_review(r, r.phone(), {"do": "take", "take": str(take),
                                         f"no{down}": "the slot is fine as it is"})
    expected = parse_answer(f"take {take}; no {down} because the slot is fine as it is", review)
    assert isinstance(expected, Review)
    assert (given.taken, given.declined, given.corrections) == (
        expected.taken, expected.declined, expected.corrections)


def test_looks_good_makes_it_ready_as_ok_does(tmp_path: Path) -> None:
    review = request()
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    phone = r.phone()
    asked = r.queue.asks["j1"]
    phone.post("/job/j1/review", {"since": since(asked), "do": "ok"})
    shown = phone.get("/job/j1/review")
    assert "1 ticked isn't used yet: use to take it, or ok again" in shown.body, \
        "the terminal's own words"
    assert phone.post("/job/j1/reply", {"version": version(shown), "typed": "ok"}).status == 303
    wait_until(lambda: bool(r.queue.given))
    given = r.queue.given[0][1]
    assert isinstance(given, Review) and given.approved
    assert r.starts.calls == [], "ready is not started: Start is still the person's"


def test_change_finds_a_setting_by_words_and_corrects_it_as_typed(tmp_path: Path) -> None:
    review = request()
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    phone = r.phone()
    found = phone.get("/job/j1/review/change?words=wall+loops")
    assert found.status == 200 and "Orca calls it wall_loops." in found.body
    assert "Change and slice again" in found.body
    given = _given_review(r, phone, {"do": "change", "key": "wall_loops", "value": "4",
                                     "reason": "the walls carry the shelf"})
    expected = parse_answer("wall_loops=4 because the walls carry the shelf", review)
    assert isinstance(expected, Review) and given.corrections == expected.corrections


def test_a_reviews_stale_form_changes_nothing(tmp_path: Path) -> None:
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=made_up())})
    reply = r.phone().post("/job/j1/review", {"since": "2.0", "do": "ok"})
    assert reply.status == 409 and r.queue.given == []


def test_the_pose_is_sent_back_at_the_mac(tmp_path: Path) -> None:
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=made_up())})
    assert "To send the pose back, at the Mac" in r.phone().get("/job/j1/review").body


# --------------------------------------------------------------------------
# A look, and the mesh gate
# --------------------------------------------------------------------------


def test_a_look_shows_all_of_it_then_ok_or_reject(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.LOOK, shown=("Orca  38 min, 24 g", "pauses at 3.2"))
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    shown = phone.get("/job/j1/look")
    assert "Orca  38 min, 24 g" in shown.body and "pauses at 3.2" in shown.body
    assert 'href="/job/j1/look"' in phone.get("/").raw
    assert phone.post("/job/j1/look", {"since": "3.0", "ok": "yes"}).status == 409
    assert phone.post("/job/j1/look", {"since": since(asked), "ok": "yes"}).status == 303
    assert r.queue.given == [("j1", True)]


def test_the_mesh_gate_approves_or_rejects(tmp_path: Path) -> None:
    asked = Asked(job_id="j1", need=Need.MESH, gate={"reason": "it has holes",
                                                      "is_manifold": False})
    r = rig(tmp_path, {"j1": asked})
    phone = r.phone()
    assert "it has holes" in phone.get("/job/j1/mesh").body
    phone.post("/job/j1/mesh", {"since": since(asked), "approve": "no"})
    assert r.queue.given == [("j1", (False, "on the phone"))]


# --------------------------------------------------------------------------
# Done when: a file from sent to ready on the phone, on the real queue
# --------------------------------------------------------------------------


@pytest.fixture
def made(tmp_path: Path) -> Iterator[QueueShop]:
    found = QueueShop(tmp_path)
    yield found
    found.ledger.close()


def test_a_file_goes_from_sent_to_ready_on_the_phone(made: QueueShop, tmp_path: Path) -> None:
    phones = Phones(tmp_path / "phones.sqlite")
    starts = FakeStarts()
    with ExitStack() as stack:
        daemon = stack.enter_context(made.daemon())
        queue = stack.enter_context(made.queue(daemon))
        shop = Shop(made.ledger_path, queue=queue, daemon=daemon)
        phone = signed_in(handler_for(home=home(), phones=phones, starts=starts, shop=shop),
                          phones)
        job_id = queue.add([Adding(made.part("bracket"))])[0][0]
        for _ in range(12):
            wait_until(lambda: queue.asked(job_id) is not None
                       or made.waits(job_id) is Waits.READY)
            asked = queue.asked(job_id)
            if asked is None:
                break
            if asked.need is Need.QUESTIONS:
                form = {"since": repr(asked.since)}
                form.update({f"q{i}": "1" for i in range(len(asked.questions))})
                assert phone.post(f"/job/{job_id}/questions", form).status == 303
            elif asked.need is Need.REVIEW:
                assert phone.post(f"/job/{job_id}/review",
                                  {"since": repr(asked.since), "do": "ok"}).status == 303
                shown = phone.get(f"/job/{job_id}/review")
                if 'name="version"' in shown.raw:
                    phone.post(f"/job/{job_id}/reply", {"version": version(shown),
                                                        "typed": "ok"})
            wait_until(lambda a=asked: queue.asked(job_id) is not a)  # type: ignore[misc]
        wait_until(lambda: made.waits(job_id) is Waits.READY)
        assert made.printer.verbs.count("start") == 0 and starts.calls == []
        assert "Ready. They wait until you start them." in phone.get("/").body


def test_a_turn_that_waits_is_said_in_the_terminals_words(tmp_path: Path) -> None:
    review = made_up()  # it doesn't fit as posed: ok waits for the turn
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    phone = r.phone()
    phone.post("/job/j1/review", {"since": since(r.queue.asks["j1"]), "do": "ok"})
    shown = phone.get("/job/j1/review")
    assert "it doesn't fit as posed: type turn" in shown.body and r.queue.given == []
