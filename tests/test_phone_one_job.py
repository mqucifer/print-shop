"""A single job on the phone, as a queue of one (mqucifer/print-shop#288).

The lone job's asker, closer and reviewer are the terminal's own, at the Mac, and each ask is
also a real `Asked` the phone answers through the queue's screens. These tests race the two on
fakes: the Mac's typing is a fake stdin (or a real pty, for what's half-typed), and the phone
is the page's real handler through a fake socket (`tests/test_phone_start.py`). No test arms.
"""

from __future__ import annotations

import os
import pty
import sys
import termios
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from print_shop.orchestrator.print_queue import Asked, Need
from print_shop.phone.one_job import QUEUES_ALONE, OneJob
from print_shop.phone.phones import Phones
from print_shop.phone.server import handler_for
from print_shop.phone.shop import Shop
from print_shop.setup.intake import Answer, cli_asker
from print_shop.setup.review import Review, cli_reviewer, parse_answer
from tests.test_phone_needs import PURPOSE, SUPPORT, request
from tests.test_phone_start import Browser, FakeStarts, home, signed_in
from tests.test_queue import wait_until

JOB = "job-0001-made-up"


@dataclass
class Daemonish:
    """The run's daemon as the queue of one reads it: its active job and its gate."""

    job_id: str = JOB
    gate: dict[str, Any] | None = None

    def active(self) -> str | None:
        return self.job_id

    def pending_gate(self) -> dict[str, Any] | None:
        return self.gate

    def prepared(self, job_id: str) -> dict[str, Any]:
        return {}

    def outcome_choices(self, job_id: str) -> dict[str, Any]:
        return {}

    def record_outcome(self, job_id: str, **given: Any) -> str:
        return ""


@dataclass
class Mac:
    """What's typed at the Mac: lines, each ready when it's let go."""

    lines: list[str] = field(default_factory=list)
    ready: threading.Event = field(default_factory=threading.Event)
    dropped: int = 0

    def keys_ready(self, timeout_s: float) -> bool:
        return self.ready.wait(timeout_s) and bool(self.lines)

    def read_line(self, prompt: str) -> str:
        line = self.lines.pop(0)
        if not self.lines:
            self.ready.clear()
        return line

    def type(self, *lines: str) -> None:
        self.lines += lines
        self.ready.set()

    def forget(self) -> None:
        self.dropped += 1


@dataclass
class Rig:
    one: OneJob
    mac: Mac
    said: list[str]
    shop: Shop
    handler: type
    phones: Phones

    def phone(self) -> Browser:
        return signed_in(self.handler, self.phones)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    mac = Mac()
    said: list[str] = []
    one = OneJob(part="bracket.stl", write=said.append, races=True, keys_ready=mac.keys_ready,
                 read_line=mac.read_line, forget=mac.forget)
    one.daemon = Daemonish()
    shop = Shop(tmp_path / "ledger.sqlite", queue=one, daemon=one.daemon)
    one.who = lambda job_id: shop.by.get(job_id, "")
    phones = Phones(tmp_path / "phones.sqlite")
    handler = handler_for(home=home(), phones=phones, starts=FakeStarts(), shop=shop)
    return Rig(one, mac, said, shop, handler, phones)


def at_the_mac(rig: Rig, run: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    """The lone job's step, run as the daemon runs it: in a thread of its own."""
    got: list[Any] = []
    thread = threading.Thread(target=lambda: got.append(run()), daemon=True)
    thread.start()
    return thread, got


def ask(rig: Rig) -> Callable[[], tuple[Answer, ...]]:
    asker = rig.one.asker(cli_asker(read=rig.one.read, write=rig.said.append,
                                    forget=lambda: None, width=lambda: 60))
    return lambda: asker((PURPOSE, SUPPORT))


def since(asked: Asked) -> str:
    return repr(asked.since)


# --------------------------------------------------------------------------
# Done when: Main on a single job shows the job and its need
# --------------------------------------------------------------------------


def test_main_on_a_lone_job_shows_the_job_and_its_need(rig: Rig) -> None:
    thread, _ = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    phone = rig.phone()
    shown = phone.get("/")
    assert "The queue isn't running" not in shown.body
    assert "This job" in shown.body and "bracket.stl" in shown.body
    assert f'href="/job/{JOB}/questions"' in shown.raw and "Answer" in shown.body
    assert QUEUES_ALONE[1:] in shown.body
    page = phone.get(f"/job/{JOB}")
    assert "Hold" not in page.body and "Remove from the queue" not in page.body
    rig.mac.type("1", "1")
    thread.join(5)


def test_a_lone_job_offered_at_the_gate_links_to_start(rig: Rig) -> None:
    rig.one.daemon.gate = {"job_id": JOB, "gate": "before_print"}
    shown = rig.phone().get("/")
    assert '<a class="act" href="/start">Start</a>' in shown.raw


def test_the_queues_own_moves_arent_a_lone_jobs(rig: Rig) -> None:
    rig.one.daemon.gate = {"job_id": JOB}
    phone = rig.phone()
    for verb in ("hold", "first"):
        reply = phone.post(f"/job/{JOB}/{verb}", {"go": "1"})
        assert reply.status == 409 and "the queue" in reply.body


# --------------------------------------------------------------------------
# Done when: a single job's questions are answered on the phone, and it goes on with them
# --------------------------------------------------------------------------


def test_the_phone_answers_first_and_the_job_goes_on_with_its_answers(rig: Rig) -> None:
    thread, got = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    asked = rig.one.asked(JOB)
    assert asked is not None
    phone = rig.phone()
    done = phone.post(f"/job/{JOB}/questions", {"since": since(asked), "q0": "2", "q1": "2"})
    assert done.status == 303, done.body
    thread.join(5)
    assert not thread.is_alive()
    typed = iter(["2", "2"])
    expected = tuple(cli_asker(read=lambda prompt: next(typed), write=lambda t: None,
                               forget=lambda: None)((PURPOSE, SUPPORT)))
    assert got == [expected], "the job goes on with the phone's answers"
    said = "\n".join(rig.said)
    assert ("Answered on your phone (Made-up phone): Carries a light load; No supports. "
            "It goes on with those.") in said
    assert rig.mac.dropped == 1, "what was typed at the Mac meanwhile is dropped"


def test_the_mac_answers_first_and_the_phones_page_is_put_by(rig: Rig) -> None:
    thread, got = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    asked = rig.one.asked(JOB)
    assert asked is not None
    phone = rig.phone()
    phone.post(f"/job/{JOB}/questions", {"since": since(asked), "q0": "o",
                                         "w0": "it holds a lamp", "q1": "o",
                                         "w1": "no supports"})
    rig.mac.type("1", "1")
    thread.join(5)
    assert got and got[0][1].choice == 0, "the Mac's answers stand"
    late = phone.get(f"/job/{JOB}/questions")
    assert "Answered on your phone" not in "\n".join(rig.said)
    session = rig.shop.sessions.get(JOB)
    if session is not None:
        wait_until(lambda: session.ended, 5)
    again = phone.post(f"/job/{JOB}/questions", {"since": since(asked), "q0": "2", "q1": "2"})
    assert again.status in (409, 200) and ("Not taken" in again.body or "Nothing waits"
                                           in again.body or "out of date" in again.body)
    assert late.status in (200, 409)


def test_the_macs_answers_to_some_arent_used_once_the_phones_stand(rig: Rig) -> None:
    thread, got = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    rig.mac.type("1")  # question 1 answered at the Mac
    wait_until(lambda: rig.one._typed_here == ["1"])
    asked = rig.one.asked(JOB)
    assert asked is not None
    rig.phone().post(f"/job/{JOB}/questions", {"since": since(asked), "q0": "2", "q1": "2"})
    thread.join(5)
    assert got[0][0].choice == 1, "the phone's answers stand"
    assert "Your answers here to 1 aren't used: the phone's stand." in "\n".join(rig.said)


# --------------------------------------------------------------------------
# Done when: a single job's review is approved, used and changed on the phone
# --------------------------------------------------------------------------


def review_at_the_mac(rig: Rig) -> tuple[threading.Thread, list[Any]]:
    reviewer = rig.one.reviewer(cli_reviewer(read=rig.one.read, write=rig.said.append,
                                             forget=lambda: None, asks=lambda: False,
                                             screen=lambda: None, more=lambda: False))
    review = request()
    thread, got = at_the_mac(rig, lambda: reviewer(review))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    return thread, got


def answered_on_phone(rig: Rig, form: dict[str, str]) -> Review:
    asked = rig.one.asked(JOB)
    assert asked is not None
    phone = rig.phone()
    assert phone.post(f"/job/{JOB}/review", {"since": since(asked), **form}).status == 303
    for _ in range(3):
        shown = phone.get(f"/job/{JOB}/review")
        if 'name="version"' not in shown.raw:
            break
        stamp = shown.raw.split('name="version" value="')[1].split('"')[0]
        phone.post(f"/job/{JOB}/reply", {"version": stamp, "typed": "ok"})
    wait_until(lambda: not asked.open)
    return asked._answer  # type: ignore[no-any-return]


@pytest.mark.parametrize(("form", "typed", "said"), [
    ({"do": "ok"}, "ok", "Approved on your phone (Made-up phone)."),
    ({"do": "use", "use": "2"}, "use 2",
     "Changed on your phone (Made-up phone): enable_support=0; slicing again."),
    ({"do": "change", "key": "wall_loops", "value": "4", "reason": "the walls carry it"},
     "wall_loops=4 because the walls carry it",
     "Changed on your phone (Made-up phone): wall_loops=4; slicing again."),
])
def test_a_lone_jobs_review_is_answered_on_the_phone(
    rig: Rig, form: dict[str, str], typed: str, said: str,
) -> None:
    thread, got = review_at_the_mac(rig)
    given = answered_on_phone(rig, form)
    thread.join(5)
    assert got == [given]
    expected = parse_answer(typed, request())
    assert isinstance(expected, Review) and given.corrections == expected.corrections
    assert said in "\n".join(rig.said)


# --------------------------------------------------------------------------
# The model's wait takes the phone's go
# --------------------------------------------------------------------------


class Call:
    seconds = 42.0
    stopped = False

    def __init__(self) -> None:
        self.done = threading.Event()

    def ended(self, timeout: float = 0) -> bool:
        return self.done.is_set()


def test_the_lone_jobs_wait_takes_the_phones_go(rig: Rig) -> None:
    from print_shop.setup.waiting import GO_ON, Waiting  # noqa: PLC0415

    waiting = rig.one.waiting(Waiting(started=lambda c: None, ended=lambda c, a: None,
                                      wait=lambda c: True, closer=None))
    call = Call()
    waiting.started(call)  # type: ignore[arg-type]
    assert rig.one.reading(JOB) == 42.0
    assert rig.one.go(JOB) == ""
    assert rig.one.keys_or_go(1.0) and rig.one.wait_read("") == GO_ON
    call.done.set()
    assert rig.one.go(JOB) != "", "no wait, no go"


# --------------------------------------------------------------------------
# What's half typed at the Mac answers nothing once the phone has answered (a real pty)
# --------------------------------------------------------------------------


@pytest.fixture
def tty(monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """A pty as the Mac's terminal, in its own canonical mode: stdin is its other end."""
    main, other = pty.openpty()
    stdin = os.fdopen(other, "r", buffering=1)
    monkeypatch.setattr(sys, "stdin", stdin)
    yield main
    stdin.close()
    os.close(main)


def test_what_was_half_typed_answers_nothing_after_the_phone(tty: int, tmp_path: Path) -> None:
    """print-shop-ux: half a line typed, the phone answers, and Enter at the next prompt
    answers nothing typed before."""
    said: list[str] = []
    one = OneJob(part="bracket.stl", write=said.append, races=True)
    one.daemon = Daemonish()
    asked_first = one.asker(cli_asker(read=one.read, write=said.append, forget=lambda: None))
    got: list[Any] = []
    thread = threading.Thread(target=lambda: got.append(asked_first((SUPPORT,))), daemon=True)
    thread.start()
    wait_until(lambda: one.asked(JOB) is not None)
    os.write(tty, b"2")  # half a line, no Enter
    time.sleep(0.3)
    assert one.give(JOB, (Answer(choice=0),)) == ""
    thread.join(5)
    assert got == [(Answer(choice=0),)]
    # The next prompt, at the Mac: Enter alone.
    nxt: list[str] = []
    reading = threading.Thread(target=lambda: nxt.append(one.read("next> ")), daemon=True)
    reading.start()
    time.sleep(0.2)
    os.write(tty, b"\n")
    reading.join(5)
    assert nxt == [""], "the half-typed 2 was dropped, never the next prompt's answer"
    assert termios.tcgetattr(sys.stdin.fileno())[3] & termios.ICANON


def test_with_the_page_off_the_lone_job_reads_as_input_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    typed = iter(["1", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    one = OneJob(part="bracket.stl")
    assert not one.races
    asker = one.asker(cli_asker(read=one.read, write=lambda t: None, forget=lambda: None))
    assert asker((PURPOSE, SUPPORT)) == (Answer(choice=0), Answer(choice=1))



# --------------------------------------------------------------------------
# The cold security review of mqucifer/print-shop#290, and what came of it
# --------------------------------------------------------------------------


def test_with_answers_replayed_or_no_terminal_the_phone_answers_nothing(rig: Rig) -> None:
    """Finding 1: not racing, the job's asks aren't the phone's at all."""
    rig.one.races = False
    seen: list[Any] = []

    def terminal(questions: Any) -> tuple[Answer, ...]:
        seen.append(rig.one.asked(JOB))
        assert rig.one.give(JOB, (Answer(choice=1), Answer(choice=1))) != ""
        return (Answer(choice=0), Answer(choice=0))

    assert rig.one.asker(terminal)((PURPOSE, SUPPORT)) == (Answer(choice=0), Answer(choice=0))
    assert seen == [None], "no ask is the phone's"


def test_what_the_mac_is_told_has_no_control_codes(rig: Rig) -> None:
    """Finding 2: the phone's words reach the Mac as words, never the terminal's own codes."""
    thread, got = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    words = Answer(other="x\x1b[1A\x1b[2K at the Mac\x1b]0;made-up\x07")
    assert rig.one.give(JOB, (words, Answer(choice=1))) == ""
    thread.join(5)
    told = "\n".join(rig.said)
    assert "\x1b" not in told and "\x07" not in told and "?[1A" in told


def test_the_page_drops_control_codes_from_what_it_sends_on(rig: Rig) -> None:
    thread, got = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    asked = rig.one.asked(JOB)
    assert asked is not None
    rig.phone().post(f"/job/{JOB}/questions", {"since": since(asked), "q0": "o",
                                               "w0": "holds\x1b[2K a lamp\x07", "q1": "2"})
    thread.join(5)
    assert got and got[0][0].other == "holds [2K a lamp"


def test_a_form_never_begins_on_an_ask_after_the_one_it_was_checked_against(rig: Rig) -> None:
    """Finding 3."""
    thread, _ = at_the_mac(rig, ask(rig))
    wait_until(lambda: rig.one.asked(JOB) is not None)
    shown = rig.one.asked(JOB)
    other = Asked(job_id=JOB, need=Need.QUESTIONS, questions=(SUPPORT,))
    made = rig.shop.begin(JOB, {}, for_ask=other)
    assert isinstance(made, str) and "moved on" in made
    assert shown is not None and shown.open
    rig.mac.type("1", "1")
    thread.join(5)


def test_a_go_the_wait_never_used_answers_no_other_prompt(rig: Rig) -> None:
    """Finding 4: a go left over from a wait that ended by itself never reaches the closer."""
    from print_shop.setup.waiting import Waiting  # noqa: PLC0415

    waiting = rig.one.waiting(Waiting(started=lambda c: None, ended=lambda c, a: None,
                                      wait=lambda c: True, closer=None))
    call = Call()
    waiting.started(call)  # type: ignore[arg-type]
    assert rig.one.go(JOB) == ""
    call.done.set()
    waiting.ended(call, None)  # type: ignore[arg-type]
    assert not rig.one.keys_or_go(0.3)
    rig.mac.type("1")
    assert rig.one.wait_read("1> ") == "1", "the closer reads what's typed, not the phone's go"
