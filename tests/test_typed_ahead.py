"""What was typed before the questions are drawn answers none of them (mqucifer/print-shop#297).

The questions drop what was typed ahead once they're drawn, before the first prompt reads, as the
review's screens and the notes' read-back do (mqucifer/print-shop#254's rule: write, drop, then
read). Each test runs the terminal's own asker in a process of its own on a pseudo-terminal, as a
person's terminal is, with the phone's page off (`input`) and on (a lone job's racing read,
mqucifer/print-shop#288). Made-up questions; nothing touches a printer, broker or ledger.
"""

from __future__ import annotations

import json
import os
import pty
import select
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from print_shop.setup.intake import Answer, Option, Question, cli_asker, replay_asker

WAIT_S = 30.0

_CHILD = textwrap.dedent("""
    import sys, time
    from pathlib import Path

    from print_shop.setup.intake import Option, Question, cli_asker
    from print_shop.setup.questions import SEEN, STOCK, Asked
    from print_shop.setup.waiting import ask_meanwhile, cli_waiting

    folder, page, how = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    read = input
    if page == "on":
        from print_shop.phone.one_job import OneJob

        class Daemon:
            def active(self):
                return "job-0001-made-up"

        one = OneJob(part="bracket.stl", races=True)
        one.daemon = Daemon()
        read = one.read
    purpose = Question(feature="purpose", text="What's it for?", options=(
        Option(label="decoration", settings={}, impact="looks only"),
        Option(label="a light load", settings={}, impact="a little stronger")))
    support = Question(feature="overhang", text="Support the underside?", options=(
        Option(label="tree supports", settings={"enable_support": "1"}, impact="clean"),
        Option(label="no supports", settings={"enable_support": "0"}, impact="may sag")))
    asker = cli_asker(read=read)
    if page == "on":
        asker = one.asker(asker)
    while not (folder / "draw").exists():  # what's typed now is typed before it's drawn
        time.sleep(0.05)
    if how == "plain":
        got = asker((purpose, support))
        print("ANSWERS", [a.choice for a in got], flush=True)
    else:  # the model's wait, then its closer choices and an added question
        closer = Question(feature="purpose", text="A cover or a spacer?", options=(
            Option(label="a cover", settings={}, impact="i", recommended=True, why="w"),
            Option(label="a spacer", settings={}, impact="i")))

        def propose(stop):
            while not stop.wait(0.05):
                if (folder / "release").exists():
                    return Asked((closer, SEEN), "")
            return Asked(STOCK, "stopped")

        intake = ask_meanwhile(propose, asker, cli_waiting(read=read), lambda q: q)
        print("ANSWERS", [(q.feature, a.choice, a.other) for q, a in
                          zip(intake.questions, intake.answers, strict=True)], flush=True)
""")


class Child:
    """The asker in a process of its own, on a pseudo-terminal."""

    def __init__(self, folder: Path, page: str, how: str) -> None:
        self.folder = folder
        script = folder / "child.py"
        script.write_text(_CHILD)
        self.master, slave = pty.openpty()
        self.process = subprocess.Popen(
            [sys.executable, str(script), str(folder), page, how],
            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        )
        os.close(slave)
        self.seen = ""

    def until(self, text: str, after: int = 0) -> int:
        """Wait for `text` past `after` in what's shown; returns where it ends."""
        give_up = time.monotonic() + WAIT_S
        while text not in self.seen[after:]:
            assert time.monotonic() < give_up, f"{text!r} never showed in {self.seen[after:]!r}"
            if select.select([self.master], [], [], 0.2)[0]:
                try:
                    self.seen += os.read(self.master, 65536).decode(errors="replace")
                except OSError:
                    break
        return self.seen.index(text, after) + len(text)

    def type(self, text: str) -> None:
        os.write(self.master, text.encode())

    def touch(self, name: str) -> None:
        (self.folder / name).write_text("")

    def answers(self) -> object:
        at = self.until("ANSWERS ")
        end = self.until("\n", at)
        return eval(self.seen[at:end].strip())  # noqa: S307 - our own child's list of literals

    def close(self) -> None:
        self.process.kill()
        self.process.wait(WAIT_S)
        os.close(self.master)


@pytest.fixture
def child(tmp_path: Path) -> Iterator[Callable[[str, str], Child]]:
    made: list[Child] = []

    def start(page: str, how: str = "plain") -> Child:
        made.append(Child(tmp_path, page, how))
        return made[-1]

    yield start
    for each in made:
        each.close()


@pytest.mark.parametrize("page", ["off", "on"])
def test_a_number_and_enter_typed_before_the_questions_answer_nothing(
    child: Callable[[str, str], Child], page: str,
) -> None:
    """Done when: "1" and Enter typed before the questions are drawn answer nothing, and a "2"
    typed after answers 2, with the phone's page on and off."""
    run = child(page, "plain")
    time.sleep(0.3)
    run.type("1\n")  # before anything is drawn
    time.sleep(0.2)
    run.touch("draw")
    at = run.until("1> ")
    time.sleep(0.2)
    run.type("2\n")
    run.until("2> ", at)
    run.type("1\n")
    assert run.answers() == [1, 0], "the early 1 answered nothing; the 2 typed after did"


@pytest.mark.parametrize("page", ["off", "on"])
def test_answers_typed_at_each_prompt_in_turn_read_as_now(
    child: Callable[[str, str], Child], page: str,
) -> None:
    """The typing cases: each answered at its own prompt, a line typed ahead for the next one
    at the prompt before is kept, as it always was."""
    run = child(page, "plain")
    run.touch("draw")
    run.until("1> ")
    run.type("1\n2\n")  # the second typed ahead, at question 1's prompt, for question 2
    assert run.answers() == [0, 1]


@pytest.mark.parametrize("page", ["off", "on"])
def test_a_line_typed_at_the_models_wait_as_its_questions_arrive_answers_none(
    child: Callable[[str, str], Child], page: str,
) -> None:
    """print-shop-ux: a "1" typed at the model's wait prompt just as its questions arrive is
    dropped, not taken as the closer's answer: the likeliest real case."""
    run = child(page, "wait")
    run.touch("draw")
    at = run.until("1> ")  # print-shop's own two first, while the model reads
    run.type("2\n")
    at = run.until("2> ", at)
    run.type("1\n")
    at = run.until("waiting for the model", at)
    run.touch("release")
    run.type("1\n")  # typed at the wait as its questions arrive
    at = run.until("Enter  keep your answer", at)
    at = run.until("1> ", at)
    time.sleep(0.3)
    run.type("2\n")  # the closer's second choice, typed after it's shown
    answers = run.answers()
    assert isinstance(answers, list)
    purpose = next(a for a in answers if a[0] == "purpose")
    assert purpose[1] == 1, "the closer took the 2 typed after it was shown, not the early 1"


def test_replayed_answers_read_as_now(tmp_path: Path) -> None:
    """Done when: replayed answers (`--answers`) read as now: they drop nothing and ask
    nothing."""
    questions = (Question(feature="purpose", text="What's it for?", options=(
        Option(label="decoration", settings={}, impact="looks only"),
        Option(label="a light load", settings={}, impact="a little stronger"))),)
    path = tmp_path / "answers.json"
    # A recorded choice counts from 1, as it's typed.
    path.write_text(json.dumps({"answers": [{"question": "purpose", "choice": 2}]}))
    said: list[str] = []
    assert replay_asker(path, write=said.append)(questions) == (Answer(choice=1),)


def test_the_questions_drop_once_each_time_theyre_drawn() -> None:
    """Every set drops what was typed ahead once it's drawn, the first too: written, dropped,
    then read; and nothing more between its prompts."""
    did: list[str] = []
    questions = (Question(feature="purpose", text="What's it for?", options=(
        Option(label="decoration", settings={}, impact="looks only"),
        Option(label="a light load", settings={}, impact="a little stronger"))),
        Question(feature="seen", text="Which faces are seen?", options=(
            Option(label="the walls", settings={}, impact="i"),
            Option(label="the top", settings={}, impact="i"))))

    def read(prompt: str) -> str:
        did.append(prompt)
        return "1"

    cli_asker(read=read, write=lambda text: did.append("written"),
              forget=lambda: did.append("dropped"))(questions)
    assert did == ["written", "dropped", "1> ", "2> "]
