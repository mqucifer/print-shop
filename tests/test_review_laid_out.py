"""The review laid out to be read, with every detail kept (mqucifer/print-shop#223).

Each Done when on a made-up review the size of 2026-11-21's (`tests/review_made_up.py`): every
figure and reason main said is still said; no line is wider than 100 columns, and the columns
line up; no fit and no lesson's words are said twice; and a second round lists what moved, at
the top and above the prompt. Then what came with it: one number space, the short forms above
the prompt, heights and times as a person reads them, and #249's pose waits said among the
screens as `orientation`'s is.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path

import pytest

from print_shop.bench.pausing import z_span
from print_shop.joblog import SAID, Terminal
from print_shop.orchestrator import graph
from print_shop.setup.proposal import Change, Source
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer, parse_answer, render
from print_shop.setup.screen import WIDTH, clock, sections, warnings, what_moved
from tests.review_made_up import (
    FIT_POCKETS,
    FIT_RAIL,
    LESSONS,
    READING,
    made_up,
)

#: Main's review of the same made-up request, before #223 (2662e23).
BEFORE = Path(__file__).parent / "data" / "review" / "before-223.txt"


def _flat(text: str) -> str:
    """The text with its wrapping undone: what's said, whatever the width."""
    return " ".join(text.split())


def _said_clock(text: str) -> str:
    """Main's "75 min 26 s" as the new layout says it, 1:15:26."""
    return re.sub(r"\b(\d+) min (\d+) s\b",
                  lambda m: clock(int(m.group(1)) * 60 + int(m.group(2))), text)


def _second_round(first: ReviewRequest) -> ReviewRequest:
    """enable_support=0, take 5 (ironing off), and the turn taken: Support and Ironing are gone
    and the time falls, the layers don't."""
    p = first.proposal
    changes = (*(c for c in p.changes if c.key not in ("enable_support", "ironing_type")),
               Change(key="ironing_type", preset="top", value="no ironing", source=Source.READING,
                      reason="the top isn't seen, so ironing it is time for nothing",
                      knowledge="ironing_hidden"))
    features = tuple(f for f in READING.features if f.kind not in ("Ironing", "Support"))
    return replace(
        first, proposal=p.model_copy(update={"changes": changes, "not_applied": ()}),
        slice=first.slice.model_copy(update={"print_time_s": 3226, "filament_g": 13.1,
                                             "filament_cm3": 10.2}),
        reading=READING.model_copy(update={"features": features}), flags=first.flags[2:],
        turn_taken=True,
    )


# --------------------------------------------------------------------------
# Every detail kept
# --------------------------------------------------------------------------


def test_every_figure_main_said_is_still_said() -> None:
    before = BEFORE.read_text(encoding="utf-8")
    # The prompt's own numbers were its numbering, which is one number space now (below).
    said = before.rsplit("\n\n", 1)[0]
    now = render(made_up())
    figures = set(re.findall(r"-?\d+(?:[.:]\d+)*%?", _said_clock(said)))
    assert figures - set(re.findall(r"-?\d+(?:[.:]\d+)*%?", now)) == set()


def test_every_word_main_said_is_still_said_but_those_said_another_way() -> None:
    """A word main used that the layout doesn't is one it says another way, each named here."""
    before = BEFORE.read_text(encoding="utf-8")
    now = render(made_up())
    words = {w for w in re.findall(r"[a-z_']+", before.lower())}
    gone = words - set(re.findall(r"[a-z_']+", now.lower()))
    assert gone <= {
        "learned",  # "learned on sample-tray-a.stl: ...": the lesson's own line, "from ..."
        "were",     # "the part's features were read in this pose": "features  read in ..."
        "says",     # "seen: bottom, as the part's context says": said once, as it prints
        "saying", "why", "correct",  # the long prompt's, said by the answers and examples
    }


def test_every_reason_note_and_reading_is_said_in_full() -> None:
    request = made_up()
    now = _flat(render(request))
    p = request.proposal
    reasons = [c.reason for c in (*p.changes, *p.kept, *p.not_applied) if c.lesson is None]
    found = request.suggested
    assert found is not None
    said = [*reasons, *p.notes, *p.design_notes, *p.refused, p.reasoning,
            *(r.change.reason for r in request.by_model if r.standing == "open"),
            *(g.text for g in LESSONS),
            *(f.reading for f in request.flags if FIT_POCKETS not in f.reading),
            *(c.not_applied for c in p.not_applied),
            request.orientation.rationale, *request.presets]
    for each in said:
        assert _flat(each) in now, each


def test_a_fit_and_a_lessons_words_are_said_once() -> None:
    now = _flat(render(made_up()))
    for fit in (FIT_POCKETS, FIT_RAIL):
        assert now.count(_flat(fit)) == 1, fit
    for lesson in LESSONS:
        assert now.count(_flat(lesson.text)) == 1, lesson.id
    # Two kept with the same words: the second says so by number.
    assert "the same words as lesson 15" in now
    # Named by number where they're used.
    assert "fit 1 (z 1.2 to 3.2 mm as it prints)" in now
    assert "lesson 11" in now


def test_the_sections_come_in_the_issues_order() -> None:
    titles = [s.title for s in sections(made_up())]
    assert titles == ["LOOK FIRST", "PRINT", "SETTINGS, over your presets",
                      "WHAT THE SLICE SHOWS", "THE PART", "POSE",
                      "LESSONS FROM EARLIER PRINTS, kept by a reviewer, for a part like this"]
    text = render(made_up())
    settings = text[text.index("SETTINGS"):text.index("WHAT THE SLICE SHOWS")]
    # Grouped by who set them, then Use these and the model's, each as its own group.
    order = ["YOURS", "LESSONS AND GUARDS", "THE MODEL", "KEPT AT YOUR PRESET", "USE THESE",
             "PROPOSED BY THE MODEL"]
    assert [settings.index(g) for g in order] == sorted(settings.index(g) for g in order)


# --------------------------------------------------------------------------
# Width and columns
# --------------------------------------------------------------------------


@pytest.mark.parametrize("width", [100, 80, 72])
def test_no_line_is_wider_than_the_width_save_one_word_that_cant_break(width: int) -> None:
    for text in (render(made_up(), width), render(_second_round(made_up()), width,
                                                 what_moved(made_up(), _second_round(made_up())),
                                                 first=False)):
        for line in text.splitlines():
            # A path or a setting's name is never broken: it runs past, on its own.
            assert len(line) <= width or len(line.split()) == 1, line


def test_wider_than_100_is_laid_out_to_100() -> None:
    assert render(made_up(), 160) == render(made_up(), 100)


def _column_starts(lines: list[str], cells: int) -> set[tuple[int, ...]]:
    """Where each row's cells start, for rows split on runs of two or more spaces."""
    starts = set()
    for line in lines:
        found = [m.start() for m in re.finditer(r"(?<=\s\s)\S", line)]
        if len(found) >= cells:
            starts.add(tuple(found[:cells]))
    return starts


def test_the_settings_slice_and_pose_columns_line_up() -> None:
    text = render(made_up())
    lines = text.splitlines()
    head = next(i for i, x in enumerate(lines) if x.strip().startswith("setting "))
    lessons = lines[lines.index("  LESSONS AND GUARDS") + 1:lines.index("  LESSONS AND GUARDS") + 8]
    assert len(_column_starts([lines[head], *lessons], 4)) == 1

    table = lines[lines.index("WHAT THE SLICE SHOWS") + 1:lines.index("WHAT THE SLICE SHOWS") + 11]
    # Times, areas and layers are right-aligned: each ends where its column does.
    ends = {(line.index(":") + 3, ) for line in table[1:]}
    assert len(ends) == 1
    assert len({len(line[:line.index("#")]) for line in table[1:]}) == 1

    at = lines.index("  The poses it weighed (overhang is what would need support; ! is this "
                     "print)")

    poses = lines[at + 1:at + 8]
    overhang = {line.index(".") for line in poses[1:] if "." in line.split()[1]}
    assert len(overhang) == 1
    assert poses[1].startswith("  ! 0,0,0")


def test_times_are_mss() -> None:
    assert [clock(s) for s in (0, 5, 9.6, 60, 754, 3600, 4526)] == [
        "0:00", "0:05", "0:09.6", "1:00", "12:34", "1:00:00", "1:15:26"]
    text = render(made_up())
    assert "time      1:15:26, Orca's estimate" in text
    assert re.search(r"Ironing\s+12:19\s+#+", text)


def test_a_height_range_of_one_value_reads_at_z() -> None:
    assert z_span(3.2, 3.2) == "at z 3.2 mm"
    assert z_span(3.2, 3.2, "from ") == "at z 3.2 mm"
    assert z_span(1.2, 4.2, "from ") == "from z 1.2 to 4.2 mm"
    text = _flat(render(made_up()))
    assert "(short layers at z 3.2 mm)" in text
    assert "3.2 to 3.2" not in text
    lines = READING.lines()
    assert "Bridge: 4 min 24 s, 2402 mm2 on 1 layer, at z 3.4 mm" in lines


def test_it_reads_without_colour() -> None:
    assert "\033" not in render(made_up())


# --------------------------------------------------------------------------
# Above the prompt
# --------------------------------------------------------------------------


def test_look_first_is_said_again_above_the_prompt_each_line_whole() -> None:
    text = render(made_up())
    end = text[text.index("LOOK FIRST, AGAIN"):]
    shorts = [w.short for w in warnings(made_up())]
    assert shorts == [
        "PAUSE at 3.2 mm for an insert, about 29:23 in",
        "NOT APPLIED top_surface_pattern: asked concentric, sliced monotonicline",
        "DOESN'T FIT AS POSED: it fits turned 90° anticlockwise, seen from above; type turn",
        "WARPING RISK: 5,400 mm2 on the plate",
        "USE THESE: 1 ticked from sample-tray-old.stl (use)",
        "THE SLICE PROPOSES 2: take 5 6",
        "THE MODEL PROPOSES 2: take 7 8",
    ]
    for short in shorts:
        assert short in end
    assert "..." not in end


def test_the_answers_are_one_list_and_the_examples_come_on_the_first_round_only() -> None:
    first = _flat(render(made_up()))
    assert ("Answers: turn (ok waits for it) · ok (approval gate) · take (the slice's) · "
            "take 7 8 (the model's) · no 7 because … · use · clear 2 · orientation (send it "
            "back) · key=value because … · stop") in first
    assert "e.g. wall_loops=3 because it's decorative (several separated by ';')" in first
    later = render(made_up(), first=False)
    assert "e.g." not in later


# --------------------------------------------------------------------------
# What moved
# --------------------------------------------------------------------------


def test_a_second_round_lists_what_moved_at_the_top_and_above_the_prompt() -> None:
    first = made_up()
    second = _second_round(first)
    since = what_moved(first, second)
    assert since.lines == (
        "enable_support back to your preset (was 1)",
        "time 1:15:26 -> 53:46 (-21:40)",
        "plastic 15.88 g -> 13.1 g",
        "Support gone, -8:51",
        "Ironing gone, -12:19",
        "new: TURNED ON THE PLATE to fit: turned 90° anticlockwise, seen from above",
        "gone: NOT APPLIED top_surface_pattern: asked concentric, sliced monotonicline",
        "gone: DOESN'T FIT AS POSED: it fits turned 90° anticlockwise, seen from above; type turn",
    )
    text = render(second, since=since, first=False)
    top, end = text[:text.index("LOOK FIRST\n")], text[text.index("LOOK FIRST, AGAIN"):]
    for line in since.lines:
        assert line in top and line in end


def test_only_what_moved_is_marked_where_its_shown() -> None:
    first = made_up()
    second = _second_round(first)
    text = render(second, since=what_moved(first, second), first=False)
    assert "* time      53:46" in text
    assert "* plastic   10.2 cm3, 13.1 g" in text
    assert "  layers    21" in text  # the layers didn't move
    assert "* TURNED ON THE PLATE to fit" in text
    # The same value set by another is no move: ironing_type stays no ironing.
    assert not any(x.startswith("* ironing_type") for x in text.splitlines())


def test_the_terminal_marks_what_moved_from_the_second_round_of_a_job() -> None:
    first, second = made_up(), _second_round(made_up())
    said: list[str] = []
    answers = iter(["enable_support=0 because the pockets are filled at the pause", "stop"])
    reviewer = cli_reviewer(read=lambda _: next(answers), write=said.append,
                            forget=lambda: None, screen=lambda: None, width=lambda: 100)
    reviewer(replace(first, suggested=None))
    reviewer(replace(second, suggested=None))
    assert "MOVED SINCE YOUR LAST ANSWER" not in said[0]
    assert said[1].startswith("MOVED SINCE YOUR LAST ANSWER\n  * enable_support back")


def test_the_terminal_lays_it_out_to_its_width() -> None:
    said: list[str] = []
    reviewer = cli_reviewer(read=lambda _: "stop", write=said.append, forget=lambda: None,
                            screen=lambda: None, width=lambda: 72)
    assert reviewer(replace(made_up(), suggested=None)) == Review(stop=True)
    assert all(len(x) <= 72 or len(x.split()) == 1 for x in said[0].splitlines())


# --------------------------------------------------------------------------
# One number space
# --------------------------------------------------------------------------


def test_use_these_the_slices_and_the_models_share_one_number_space() -> None:
    request = made_up()
    assert request.offered == 4
    assert [n for n, _ in request.numbered] == [5, 6]
    assert [r.number for r in request.by_model if r.standing == "open"] == [7, 8]
    text = render(request)
    assert "  5) ironing_type  top -> no ironing" in text
    assert "  7) outer_wall_speed  200 -> 120" in text


@pytest.mark.parametrize(("answer", "said"), [
    ("take 3", "take 3 is one of Use these: use 3"),
    ("use 5", "5 is the slice's: take 5"),
    ("clear 7", "7 is the model's: take 7"),
    ("take 9", "there's no 9: Use these 1-4, the slice's 5-6, the model's 7-8"),
])
def test_a_number_typed_to_the_wrong_command_says_whose_it_is(answer: str, said: str) -> None:
    assert parse_answer(answer, made_up()) == said


def test_take_takes_by_the_numbers_on_screen() -> None:
    request = made_up()
    took = parse_answer("take 5 7", request)
    assert isinstance(took, Review)
    assert [(c.key, c.value, c.taken) for c in took.taken] == [
        ("ironing_type", "no ironing", False), ("outer_wall_speed", "120", True)]
    bare = parse_answer("take", request)
    assert isinstance(bare, Review)
    assert [c.key for c in bare.taken] == ["ironing_type", "elefant_foot_compensation"]
    used = parse_answer("use 2", request)
    assert isinstance(used, Review)
    assert [(c.key, c.value) for c in used.corrections] == [("enable_support", "0")]


def test_another_print_picked_moves_no_other_number() -> None:
    request = made_up()
    offering = request.offering
    assert offering is not None
    picked = offering.pick(2)
    assert not isinstance(picked, str)
    other = replace(request, offering=picked)
    assert [n for n, _ in other.numbered] == [5, 6]


# --------------------------------------------------------------------------
# #249's waits, said among the screens as orientation's is
# --------------------------------------------------------------------------


def test_a_pose_wait_is_said_on_the_terminal_as_orientations_and_the_log_keeps_its_line() -> None:
    records: list[logging.LogRecord] = []

    class Kept(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(graph.__name__)
    kept, level = Kept(), logger.level
    logger.addHandler(kept)
    logger.setLevel(logging.INFO)
    try:
        graph._say_waiting([(180.0, 0.0, 0.0)], "a past print was in it, and its offer "
                                               "compares them in it")
    finally:
        logger.removeHandler(kept)
        logger.setLevel(level)
    record, = records
    on_screen = Terminal("%(asctime)s %(levelname)s %(message)s").format(record)
    assert on_screen == ("Waiting for the pose still being measured (180,0,0): a past print was "
                         "in it, and its offer compares them in it.")
    # The job's log keeps it with its time and level, as its own handler formats it.
    in_log = logging.Formatter("%(asctime)s %(levelname)s %(message)s").format(record)
    assert in_log.endswith(" INFO waiting for the pose still being measured (180,0,0): a past "
                           "print was in it, and its offer compares them in it")
    assert getattr(record, SAID).startswith("Waiting for the pose")


def test_any_other_log_line_keeps_its_time_and_level_on_the_terminal() -> None:
    record = logging.LogRecord("x", logging.WARNING, __file__, 1, "something", None, None)
    assert " WARNING something" in Terminal("%(asctime)s %(levelname)s %(message)s").format(record)


def test_width_is_100() -> None:
    assert WIDTH == 100
