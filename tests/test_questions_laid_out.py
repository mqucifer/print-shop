"""The questions and a job's opening lines laid out to be read, with every detail kept
(mqucifer/print-shop#266).

On a made-up job (`tests/questions_made_up.py`): the notes read back and one changed, print-shop's
two, a question of the model's with what each option sets and costs, one added after, and the
model's closer choices. Every figure and word main said is still said, no line is wider than
100 columns or the terminal, what every option shares is said once, and the answers typed are
read as they were. Then THIS JOB, and what's kept off the terminal for the job's log.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from print_shop.opening import ThisJob
from print_shop.setup.intake import Answer, Question, cli_asker, option_lines, render
from print_shop.setup.waiting import QuestionsCall, Waiting, ask_meanwhile, cli_waiting
from tests.questions_made_up import ADDED, BRIDGE, CLOSER, FOR, QUESTIONS, SEEN_NOTED
from tests.reviewing import flat

#: Main's lines for the same made-up job (7c9ef1f), as `_drive` gets them.
BEFORE = Path(__file__).parent / "data" / "questions" / "before-266.txt"

#: What's typed: the read-back's 2 changed, to 1, kept as the notes; then 1> and 3>, whose words
#: set nothing and Enter keeps them so (mqucifer/print-shop#213); the added 4>; and the closer
#: choice.
TYPED = ("2", "1", "n", "2", "o it holds glass tiles", "", "2", "2")


def _drive(width: int = 100) -> tuple[str, list[Answer]]:
    """The made-up job's questions on the command line, every line said and every prompt."""
    said: list[str] = []
    typed = iter(TYPED)

    def read(prompt: str) -> str:
        said.append(prompt)
        return next(typed)

    ask = cli_asker(read=read, write=said.append, forget=lambda: None, width=lambda: width)
    answers = [*ask(QUESTIONS), *ask(ADDED)]
    waiting = cli_waiting(read=read, write=said.append, screen=lambda: None,
                          width=lambda: width)
    assert waiting.closer is not None
    closer = waiting.closer(CLOSER, Answer(choice=1), 431.0)
    assert closer is not None
    return "\n".join(said), [*answers, closer]


# --------------------------------------------------------------------------
# Every detail kept
# --------------------------------------------------------------------------


def test_every_figure_and_word_main_said_is_still_said() -> None:
    before = BEFORE.read_text(encoding="utf-8")
    now, _ = _drive()
    figures = re.compile(r"-?\d+(?:[.:]\d+)*%?")
    assert set(figures.findall(before)) - set(figures.findall(now)) == set()
    words = re.compile(r"[a-z_']+")
    assert set(words.findall(before.lower())) - set(words.findall(now.lower())) == set()


def test_every_label_impact_reason_and_estimate_is_said_in_full() -> None:
    now = flat(_drive()[0])
    for question in (*QUESTIONS, *ADDED, CLOSER):
        assert flat(question.asked()) in now or flat(question.text) in now, question.text
        for option in question.options:
            for said in (option.label, option.impact, option.why, option.cost()):
                assert flat(question.shown(said)) in now, said


def test_the_answers_are_read_as_they_were() -> None:
    _, answers = _drive()
    assert [(a.choice, a.other, a.from_notes) for a in answers] == [
        (1, "", False), (3, "", True), (None, "it holds glass tiles", False), (1, "", False),
        (1, "", False),
    ]


def test_the_prompts_and_what_enter_keeps_are_as_they_were() -> None:
    now, _ = _drive()
    prompts = [line for line in now.splitlines() if line.endswith("> ")]
    assert prompts == ["notes> ", "2> ", "yours or notes> ", "1> ", "3> ", "3> ", "4> ", "1> "]
    assert "Your words set nothing: none of them says " in now  # mqucifer/print-shop#213
    assert "Enter if that's right, or 2 to change it." in now
    assert "     Enter  keep what the notes say" in now
    assert "     Enter  keep your answer" in now
    assert "Enter takes your answer, or n keeps the notes." in now


# --------------------------------------------------------------------------
# Laid out
# --------------------------------------------------------------------------


@pytest.mark.parametrize("width", [100, 80, 60])
def test_no_line_is_wider_than_the_width(width: int) -> None:
    now, _ = _drive(width)
    for line in now.splitlines():
        assert len(line) <= width or len(line.split()) == 1, line


def test_an_option_is_a_short_line_with_what_it_does_beneath() -> None:
    lines = option_lines(FOR, 100)
    assert lines[:3] == [
        "     1  decoration or display",
        "          nothing rests on it: strength stays as your presets have it, and the proposal "
        "looks at the",
        "          surface",
    ]
    assert lines[3].startswith("   * 2  handled, clipped or lightly loaded")
    assert lines[3].endswith("recommended") and len(lines[3]) == 100
    assert lines[5].startswith("          recommended: when the shape can't say")


def test_what_every_option_shares_is_said_once() -> None:
    text = "\n".join(option_lines(FOR, 100))
    # The shared line, and two options' own words ("as your presets have it", "kept quick and
    # cheap: your presets").
    assert text.count("your presets") == 3
    assert text.count("sets nothing itself") == 1
    assert text.endswith("     Each keeps your presets and sets nothing itself; the review shows "
                         "Orca's estimate.")
    # Where they differ, each says its own: what it sets after its label, its estimate right.
    bridge = "\n".join(option_lines(BRIDGE, 100))
    assert "Each " not in bridge
    assert re.search(r"     1  Bridge it: no supports +Orca: 75 min, 15\.88 g\n", bridge)
    assert re.search(r"   \* 2  Support it: supports on, tree supports +recommended · "
                     r"Orca: 99 min, 19\.2 g\n", bridge)


def test_options_with_no_estimate_say_so_once_in_a_sentence() -> None:
    """"Each no estimate." was a broken line: the shared cost is its own sentence."""
    unpriced = BRIDGE.model_copy(update={"options": tuple(
        o.model_copy(update={"estimate": None}) for o in BRIDGE.options)})
    text = "\n".join(option_lines(unpriced, 100))
    assert text.endswith("     None of these has an Orca estimate yet.")
    assert "Each no estimate" not in text and text.count("no estimate") == 0
    same = BRIDGE.model_copy(update={"options": tuple(
        o.model_copy(update={"settings": {}, "estimate": BRIDGE.options[0].estimate})
        for o in BRIDGE.options)})
    assert "\n".join(option_lines(same, 100)).endswith(
        "     Each keeps your presets. Orca's estimate for each: 75 min, 15.88 g.")


def test_the_notes_option_is_marked_as_the_notes() -> None:
    text = "\n".join(option_lines(SEEN_NOTED, 100))
    assert re.search(r"     4  only the face on the plate +the notes\n", text)


def test_a_question_wraps_under_itself() -> None:
    text = render([BRIDGE], width=80)
    lines = text.splitlines()
    assert lines[1].startswith("  1. The 1,204 mm² ceiling")
    assert lines[2].startswith("     ")


def test_one_question_with_no_options_shared_says_none() -> None:
    one = Question(feature="x", text="One?", options=BRIDGE.options[:1])
    assert option_lines(one, 100)[-1] == (
        "     Each sets no supports. Orca's estimate for each: 75 min, 15.88 g.")


# --------------------------------------------------------------------------
# THIS JOB
# --------------------------------------------------------------------------


def _this() -> ThisJob:
    return ThisJob(
        part="/made-up/parts/sample-tray.stl",
        inventory="cc1 with a 0.4 mm nozzle; spool #3, Made-up PETG, grey",
        fitted="a 0.4 mm nozzle and Made-up PETG",
        presets=[("machine", "Made-up Printer 0.4 - Copy"), ("process", "0.20mm Made-up - Copy"),
                 ("filament", "Made-up PETG @System")],
        notes=("machine 'Made-up Printer 0.4 - Copy': your tuning of 'Made-up Printer 0.4'",
               "process '0.20mm Made-up - Copy': your tuning of '0.20mm Made-up'",
               "filament 'Made-up PETG @System': the standard; you haven't tuned it",
               "a note naming no preset"),
        armed="Not armed (PRINT_AGENT_ALLOW_HARDWARE is not 1): an approval will NOT start a "
              "print.",
        context="from the sidecar sample-tray.part.json; read /made-up/parts/sample-tray.part.json",
    )


def test_this_job_is_one_block_its_rows_aligned() -> None:
    lines = [*_this().lines(100),
             *ThisJob.started("0f3c2a1b", "/made-up/jobs/0f3c2a1b/job.log",
                              "kept as laid, then a few questions about the part, here", 100)]
    assert lines[0] == "THIS JOB"
    names = [line[2:12].strip() for line in lines[1:] if line[2:3] != " "]
    assert names == ["part", "inventory", "fitted", "machine", "process", "filament",
                     "arming", "context", "job", "log"]
    assert "  fitted    a 0.4 mm nozzle and Made-up PETG\n" in "\n".join(lines) + "\n"
    assert all(line.startswith(" " * 12) or line[2:3] != " " for line in lines[1:])
    text = "\n".join(lines)
    assert ("  machine   Made-up Printer 0.4 - Copy\n"
            "              your tuning of Made-up Printer 0.4") in text
    assert "  filament  Made-up PETG @System\n              the standard; you haven't tuned it" \
        in text
    assert "  (a note naming no preset)" in text, "a note it can't place is said as it is"
    assert "  job       0f3c2a1b: kept as laid, then a few questions about the part, here" in text


@pytest.mark.parametrize("width", [100, 80])
def test_this_job_fits_the_width(width: int) -> None:
    for line in _this().lines(width):
        assert len(line) <= width or len(line.split()) == 1, line


def test_a_plates_parts_are_rows_of_the_block() -> None:
    plate = ThisJob(part="3 parts, one job, on one plate",
                    parts=[("cap-a.stl", "none: no design run above the file and no sidecar "
                                         "beside it, so it's read as before")])
    lines = plate.lines(100)
    assert lines[1] == "  part      3 parts, one job, on one plate"
    assert lines[2].startswith("  part 1    cap-a.stl. Its context: none: no design run")
    assert lines[3].startswith(" " * 12)


# --------------------------------------------------------------------------
# Off the terminal, kept in the job's log
# --------------------------------------------------------------------------


def _on_screen() -> Callable[[logging.LogRecord], bool]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "print_job", Path(__file__).parent.parent / "scripts" / "print_job.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    found: Callable[[logging.LogRecord], bool] = script.on_screen
    return found


@pytest.mark.parametrize(("name", "level", "msg", "shown"), [
    ("httpx", logging.INFO, 'HTTP Request: POST http://made-up/v1/chat/completions "200 OK"',
     False),
    ("httpx", logging.WARNING, "a retry", True),
    ("print_shop.core.mqtt", logging.WARNING, "availability read offline while this run is "
     "online (another session's will); saying online again", False),
    ("print_shop.core.mqtt", logging.WARNING, "the broker refused the connection", True),
    ("print_shop.orchestrator.graph", logging.INFO, "waiting for the pose", True),
])
def test_bookkeeping_is_kept_off_the_terminal(
    name: str, level: int, msg: str, shown: bool,
) -> None:
    record = logging.LogRecord(name, level, __file__, 1, msg, None, None)
    assert _on_screen()(record) is shown


# --------------------------------------------------------------------------
# The wait's start is said before what it comes to
# --------------------------------------------------------------------------


def test_a_model_that_answers_at_once_is_said_to_start_first() -> None:
    from print_shop.setup.questions import STOCK, Asked

    said: list[str] = []

    def started(call: QuestionsCall) -> None:
        said.append("started")

    def ended(call: QuestionsCall, asked: Asked) -> None:
        said.append("ended")

    waiting = Waiting(started=started, ended=ended, wait=lambda call: True,
                      went_on=lambda call: None, closer=None)
    ask_meanwhile(lambda stop: Asked(()), lambda questions: tuple(
        Answer(choice=q.recommended) for q in questions), waiting, lambda q: q, STOCK)
    assert said[0] == "started"
