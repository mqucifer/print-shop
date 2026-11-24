"""An answer in the person's own words is read back as a decision (mqucifer/print-shop#213).

"o no support" at the questions used to set nothing, so the guard turned tree supports on,
"nothing decided otherwise". Now words to a question whose options set something are read with
no model, read back ("So supports off, and nothing turns them back on?") and, once confirmed,
are a decision as a chosen option is: the guard and the lessons leave it, and the review says
it's the person's. Words that set nothing, or aren't confirmed, say so, and the guard holds.

Every part, question and lesson here is made up.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pty
import re
import select
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from print_shop.setup.intake import (
    WORDS_PROMPT,
    Answer,
    AnswersRefused,
    Intake,
    Option,
    Question,
    cli_asker,
    replay_asker,
    take_words,
)
from print_shop.setup.own_words import read
from print_shop.setup.proposal import GivenLesson, Source, from_lessons
from print_shop.setup.questions import PURPOSE
from print_shop.setup.review import render
from tests.test_proposal import _facts, _no_model, _proposal, _snap_answering
from tests.test_review import _request

#: A made-up overhang question, as the model asks one: its options set supports.
CEILING = Question(feature="overhang", text="The made-up ceiling overhangs. Support it?", options=(
    Option(label="Bridge it", settings={"enable_support": "0"}, impact="i", recommended=True,
           why="w"),
    Option(label="Tree supports", settings={"enable_support": "1"}, impact="i"),
))


class Terminal:
    """A person at the terminal: `typed` answers each prompt in turn, and `ahead` is typed while
    what contains `at` is drawn, which `forget` drops."""

    def __init__(self, *typed: str, at: str = "", ahead: str = "") -> None:
        self.typed = list(typed)
        self.at, self.ahead = at, ahead
        self.buffered: list[str] = []
        self.shown: list[str] = []
        self.prompts: list[str] = []

    def write(self, text: str) -> None:
        self.shown.append(text)
        if self.at and self.at in text:
            self.buffered.append(self.ahead)

    def forget(self) -> None:
        self.buffered.clear()

    def read(self, prompt: str) -> str:
        assert self.buffered or self.typed, f"asked more than was scripted, at {prompt!r}"
        self.prompts.append(prompt)
        return self.buffered.pop(0) if self.buffered else self.typed.pop(0)

    def said(self) -> str:
        return " ".join(" ".join(self.shown).split())

    def ask(self, *questions: Question) -> tuple[Answer, ...]:
        return cli_asker(self.read, self.write, self.forget, width=lambda: 100)(questions)


# --------------------------------------------------------------------------
# Words are read with no model, only as what the question decides
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("words", "sets"), [
    ("no support", {"enable_support": "0"}),
    ("no support, it bridges fine", {"enable_support": "0"}),
    ("without supports", {"enable_support": "0"}),
    ("supports off", {"enable_support": "0"}),
    ("don't use supports", {"enable_support": "0"}),
    ("skip the supports", {"enable_support": "0"}),
    ("use tree supports", {"enable_support": "1"}),
    ("supports on please", {"enable_support": "1"}),
    ("4 walls and no supports", {"enable_support": "0", "wall_loops": "4"}),
    ("three walls", {"wall_loops": "3"}),
])
def test_words_are_read_as_the_settings_they_say(words: str, sets: dict[str, str]) -> None:
    reading = read(words, {"enable_support", "wall_loops"})
    assert reading.sets == sets and not reading.nothing


@pytest.mark.parametrize(("words", "why"), [
    ("it hangs a towel", "none of them says supports"),
    ("no suport", "none of them says supports"),  # misspelt: nothing is guessed
    ("no support marks on the face", "none of them says supports"),  # the finish, not supports
    ("no support on the hook, tree supports under the lip", "they say supports two ways"),
    ("4 walls", "this question doesn't decide walls"),
])
def test_words_that_dont_map_set_nothing_and_say_why(words: str, why: str) -> None:
    reading = read(words, {"enable_support"})
    assert reading.sets == {} and any(why in n for n in reading.nothing), reading.nothing


def test_words_that_cant_be_set_set_nothing() -> None:
    reading = read("a brim", {"brim_type"})  # which brim isn't said
    assert reading.sets == {} and "can't be set" in reading.nothing[0]
    assert read("brim ears", {"brim_type"}).sets == {"brim_type": "brim_ears"}
    assert read("40 walls", {"wall_loops"}).sets == {}, "out of its bound"


# --------------------------------------------------------------------------
# Read back at the questions, and confirmed or corrected
# --------------------------------------------------------------------------


def test_words_are_read_back_and_confirmed_as_a_decision() -> None:
    """The approved Questions screen (print-shop-ux): "So supports off, and nothing turns them
    back on?" """
    person = Terminal("o no support, it bridges fine", "")
    [answer] = person.ask(CEILING)
    assert answer == Answer(other="no support, it bridges fine", sets={"enable_support": "0"})
    assert person.prompts == ["1> ", WORDS_PROMPT]
    assert ("So supports off, and nothing turns them back on? From your words, 'no support, it "
            "bridges fine'. Enter or y: yes, supports off, your decision. n: your words set "
            "nothing.") in person.said()


def test_an_enter_typed_with_the_words_never_confirms_them() -> None:
    """Drawn, then what was typed ahead dropped, then read: only an Enter typed after it's shown
    confirms it."""
    person = Terminal("o no support", "n", "", at="So supports off", ahead="")
    [answer] = person.ask(CEILING)
    assert answer == Answer(other="no support"), "the Enter typed ahead was dropped"
    assert person.prompts == ["1> ", WORDS_PROMPT, "1> "]


def test_a_no_sets_nothing_says_so_and_may_answer_again() -> None:
    person = Terminal("o no support", "n", "2")
    [answer] = person.ask(CEILING)
    assert answer == Answer(choice=1)
    assert ("Your words set nothing: you said no to what they were read as. The proposal decides "
            "supports, as if this weren't answered.") in person.said()


@pytest.mark.parametrize("words", ["no suport", "no support here, tree supports there"])
def test_unclear_words_set_nothing_say_so_and_enter_keeps_them(words: str) -> None:
    person = Terminal(f"o {words}", "")
    [answer] = person.ask(CEILING)
    assert answer == Answer(other=words) and not answer.sets
    assert person.prompts == ["1> ", "1> "], "never read back: there's nothing to confirm"
    assert "Your words set nothing: " in person.said()
    assert "Enter keeps them so, or answer again" in person.said()


def test_something_other_than_yes_or_no_asks_again() -> None:
    person = Terminal("o no support", "maybe", "y")
    [answer] = person.ask(CEILING)
    assert answer.sets == {"enable_support": "0"}
    assert person.prompts == ["1> ", WORDS_PROMPT, WORDS_PROMPT]


def test_words_to_a_question_whose_options_set_nothing_are_kept_with_no_prompt() -> None:
    """What it's for sets nothing itself: the words stand, said so, and nothing more is asked."""
    person = Terminal("o it hangs a towel")
    [answer] = person.ask(PURPOSE)
    assert answer == Answer(other="it hangs a towel")
    assert person.prompts == ["1> "]
    assert "Your words set nothing: they're kept as what it's for." in person.said()


def test_words_on_a_purpose_whose_options_set_something_are_kept_as_a_purpose() -> None:
    """A purpose question whose options set walls: words that set none are what it's for, said
    so, with no prompt; words that do are still read back."""
    purpose = Question(feature="purpose", text="A made-up cover or spacer?", options=(
        Option(label="a cover", settings={"wall_loops": "3"}, impact="i", recommended=True,
               why="w"),
        Option(label="a spacer", settings={"sparse_infill_density": "40%"}, impact="i")))
    person = Terminal("o a lid for a box")
    assert person.ask(purpose) == (Answer(other="a lid for a box"),)
    assert person.prompts == ["1> "]
    assert ("Your words are kept as what it's for. They set no walls or infill, so the proposal "
            "decides those.") in person.said()
    person = Terminal("o 4 walls", "y")
    assert person.ask(purpose) == (Answer(other="4 walls", sets={"wall_loops": "4"}),)


def test_a_chosen_option_is_never_read_back() -> None:
    person = Terminal()
    answer = Answer(choice=0)
    assert take_words(CEILING, answer, "1> ", person.read, person.write, person.forget) is answer
    assert not person.shown


# --------------------------------------------------------------------------
# A confirmed reading is a decision: the guard, the lessons and the review
# --------------------------------------------------------------------------


def test_confirmed_words_keep_supports_off_through_the_guard(tmp_path: Path) -> None:
    """Done when: "no support", confirmed, keeps supports off through the guard, and the review
    says whose decision it is. With no model: the proposal's is a canned reply."""
    words = Answer(other="no support", sets={"enable_support": "0"})
    proposal = _proposal("snap", tmp_path, _no_model(), _snap_answering(tmp_path, words))
    assert proposal.get("enable_support") is None and proposal.get("support_type") is None
    assert [(c.key, c.value, c.source, c.feature) for c in proposal.kept] == [
        ("enable_support", "0", Source.ANSWER, "overhang")
    ]
    assert proposal.kept[0].reason == (
        "you said 'no support', read back as no supports, and confirmed")
    assert not any(c.source is Source.GUARD for c in proposal.changes)
    shown = render(_request(proposal))
    assert re.search(r"KEPT AT YOUR PRESET\n  Supports +enable_support +off +(off +)?off +"
                     r"your answer: overhang\n      you said 'no support', read back as no "
                     r"supports, and confirmed", shown), shown
    assert "[guard" not in shown


def test_unconfirmed_words_set_nothing_and_the_guard_holds(tmp_path: Path) -> None:
    """Done when: unconfirmed or unclear words set nothing, and the guard holds."""
    words = Answer(other="no support")
    proposal = _proposal("snap", tmp_path, _no_model(), _snap_answering(tmp_path, words))
    assert [(c.key, c.value, c.source) for c in proposal.changes
            if c.key in ("enable_support", "support_type")] == [
        ("enable_support", "1", Source.GUARD),
        ("support_type", "tree(auto)", Source.GUARD),
    ]


def test_confirmed_words_are_left_by_a_lesson(tmp_path: Path) -> None:
    words = Answer(other="no support", sets={"enable_support": "0"})
    facts = dataclasses.replace(_facts("snap", tmp_path),
                                intake=_snap_answering(tmp_path, words))
    lesson = GivenLesson(id=5, text="a made-up overhang drooped", key="enable_support",
                         value="1", job_id="job-0005", stl_name="made-up.stl",
                         traits=("overhang",), reviewed_by="operator test-op")
    changes, kept, [marked] = from_lessons(facts, (lesson,))
    assert not changes and not kept and marked.why == "your intake answer set it"


def test_the_intake_keeps_what_words_were_confirmed_as() -> None:
    intake = Intake(questions=(CEILING,),
                    answers=(Answer(other="no support", sets={"enable_support": "0"}),))
    assert Intake.model_validate_json(intake.model_dump_json()) == intake
    [(_, said)] = intake.chosen()
    assert said.settings == {"enable_support": "0"}
    assert "read back as no supports, and confirmed" in intake.summary()
    unconfirmed = Intake(questions=(CEILING,), answers=(Answer(other="no support"),))
    assert unconfirmed.chosen() == []


# --------------------------------------------------------------------------
# Replayed from a recorded answers file
# --------------------------------------------------------------------------


def _recorded(tmp_path: Path, row: dict[str, Any]) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [{"question": "overhang", **row}]}))
    return path


def test_recorded_words_with_what_they_were_confirmed_as_replay_as_a_decision(
    tmp_path: Path,
) -> None:
    path = _recorded(tmp_path, {"other": "no support", "sets": {"enable_support": "0"}})
    said: list[str] = []
    assert replay_asker(path, said.append)((CEILING,)) == (
        Answer(other="no support", sets={"enable_support": "0"}),)
    assert said == []


def test_recorded_words_with_nothing_confirmed_set_nothing_and_say_so(tmp_path: Path) -> None:
    said: list[str] = []
    answers = replay_asker(_recorded(tmp_path, {"other": "no support"}), said.append)((CEILING,))
    assert answers == (Answer(other="no support"),)
    assert said == ["answers.json answers 'The made-up ceiling overhangs. Support it?' in words, "
                    "'no support', which read as no supports, and records no \"sets\" confirming "
                    "it, so they set nothing and the proposal decides."]


@pytest.mark.parametrize(("row", "says"), [
    ({"other": "it bridges", "sets": {"enable_support": "0"}},
     "its words 'it bridges' read as nothing now, not the no supports it records"),
    ({"choice": 1, "sets": {"enable_support": "0"}}, "it has no words"),
])
def test_a_recorded_confirmation_the_words_dont_bear_out_is_refused(
    tmp_path: Path, row: dict[str, Any], says: str,
) -> None:
    with pytest.raises(AnswersRefused, match=re.escape(says)):
        replay_asker(_recorded(tmp_path, row))((CEILING,))


# --------------------------------------------------------------------------
# The real terminal: an Enter typed with the words confirms nothing
# --------------------------------------------------------------------------

_TERMINAL = textwrap.dedent("""
    from print_shop.setup.intake import Option, Question, cli_asker

    ceiling = Question(feature="overhang", text="The made-up ceiling overhangs. Support it?",
                       options=(Option(label="Bridge it", settings={"enable_support": "0"},
                                       impact="i", recommended=True, why="w"),
                                Option(label="Tree supports", settings={"enable_support": "1"},
                                       impact="i")))
    [answer] = cli_asker()((ceiling,))
    print("ANSWER", answer.other, answer.sets, flush=True)
""")

#: How long the terminal is given to show something.
WAIT_S = 30.0


def test_on_a_terminal_an_enter_typed_with_the_words_never_confirms_them(tmp_path: Path) -> None:
    """The words and an Enter typed together: the Enter is dropped once the read-back is drawn,
    nothing is answered until another is typed at "yes or no>", and that one confirms."""
    script = tmp_path / "terminal.py"
    script.write_text(_TERMINAL)
    master, slave = pty.openpty()
    child = subprocess.Popen([sys.executable, str(script)],
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
        until("1> ")
        os.write(master, b"o no support\n\n")  # the Enter typed ahead, with the words
        until(WORDS_PROMPT)
        time.sleep(0.5)
        if select.select([master], [], [], 0.3)[0]:
            shown += os.read(master, 65536).decode(errors="replace")
        assert "ANSWER" not in shown, "the Enter typed ahead confirmed the reading"
        os.write(master, b"\n")  # an Enter typed after the read-back drew
        until("ANSWER no support {'enable_support': '0'}")
    finally:
        child.kill()
        child.wait(WAIT_S)
        os.close(master)
