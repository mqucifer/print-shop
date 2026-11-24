"""A slip at the review doesn't cost the rest of the line (mqucifer/print-shop#224).

One unknown name refused every correction typed with it, a `;` in a reason was read as the next
correction, and a pasted review was read as one answer a line. Now the good ones are held while
the bad one is corrected or dropped, a `;` starts an answer only before one, and a paste is one
answer whose `ok` approves nothing. Every setting and reason here is made up.
"""

from __future__ import annotations

from collections.abc import Callable

from print_shop.setup.review import (
    Left,
    Review,
    ReviewRequest,
    Slipped,
    cli_reviewer,
    parse_answer,
    read_answer,
)
from tests.test_review import _request

GOOD = ("wall_loops=3 because the faces show through", "layer_height=0.16",
        "nozzle_temperature=215 because it strings")
BAD = "walls=4 because it's thin"
#: A review someone wrote elsewhere and pasted: 20 lines, an ok among them, no correction.
PASTED = [
    "Looked at the tray's settings.",
    "  wall_loops     2   3   model",
    "      the flat faces show infill through 2 walls",
    "The seam is at the back, which is fine.",
    "ok",
    *(f"note {n}: nothing to change here" for n in range(15)),
]


class Person:
    """Types each answer in turn. A list of several lines is pasted: its lines after the first
    are waiting when the first is read."""

    def __init__(self, *answers: str | list[str]) -> None:
        self.lines: list[tuple[str, bool]] = []
        for answer in answers:
            lines = [answer] if isinstance(answer, str) else answer
            self.lines += [(line, n > 0) for n, line in enumerate(lines)]
        self.shown: list[str] = []
        self.prompts: list[str] = []

    def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.lines.pop(0)[0]

    def more(self) -> bool:
        return bool(self.lines) and self.lines[0][1]

    def reviewer(self) -> Callable[[ReviewRequest], Review]:
        return cli_reviewer(self.read, self.shown.append, forget=lambda: None,
                            screen=lambda: None, more=self.more)

    @property
    def said(self) -> str:
        return "\n".join(self.shown[1:])  # what came after the review's screen


def test_three_good_corrections_are_held_while_the_bad_name_is_asked() -> None:
    read = read_answer("; ".join([*GOOD[:2], BAD, GOOD[2]]), _request())
    assert isinstance(read, Slipped)
    assert read.held == ("wall_loops=3", "layer_height=0.16", "nozzle_temperature=215")
    assert [at for at, _ in read.slips] == [2]
    assert parse_answer(BAD, _request()) == read.slips[0][1], "said as a typed one is"


def test_the_bad_one_corrected_slices_all_four_once() -> None:
    person = Person("; ".join([*GOOD, BAD]), "wall_loops=4 because it's thin")
    review = person.reviewer()(_request())
    assert "Held, nothing sliced yet: wall_loops=3 · layer_height=0.16 · " in person.said
    assert "'walls' isn't a setting" in person.said
    assert person.prompts == ["> ", "fix> "], "one answer, one fix, then back to slice"
    # The fix names wall_loops too: typed later, it's the one that stands.
    assert [(c.key, c.value) for c in review.corrections] == [
        ("wall_loops", "3"), ("layer_height", "0.16"), ("nozzle_temperature", "215"),
        ("wall_loops", "4")]
    assert review.corrections[2].reason == "it strings"
    assert not review.approved


def test_the_bad_one_dropped_slices_the_three() -> None:
    person = Person("; ".join([*GOOD, BAD]), "drop")
    review = person.reviewer()(_request())
    assert [c.key for c in review.corrections] == [
        "wall_loops", "layer_height", "nozzle_temperature"]


def test_two_slips_are_asked_one_after_the_other() -> None:
    person = Person(f"{GOOD[0]}; {BAD}; fans=9", "drop", "drop")
    review = person.reviewer()(_request())
    assert person.prompts == ["> ", "fix> ", "fix> "]
    assert "'fans' isn't a setting" in person.said
    assert [c.key for c in review.corrections] == ["wall_loops"]


def test_back_takes_nothing_from_the_answer_and_says_so() -> None:
    person = Person("; ".join([*GOOD, BAD]), "back", "stop")
    review = person.reviewer()(_request())
    assert review == Review(stop=True)
    assert "nothing from that answer is taken" in person.said


def test_a_blank_fix_asks_again_and_drops_nothing() -> None:
    person = Person(f"{GOOD[0]}; {BAD}", "", "drop")
    review = person.reviewer()(_request())
    assert person.prompts == ["> ", "fix> ", "fix> "]
    assert [c.key for c in review.corrections] == ["wall_loops"]


def test_a_reason_with_a_semicolon_parses_whole() -> None:
    review = parse_answer("wall_loops=3 because it strings; the last two did too", _request())
    assert isinstance(review, Review)
    [walls] = review.corrections
    assert (walls.key, walls.value, walls.reason) == (
        "wall_loops", "3", "it strings; the last two did too")


def test_a_semicolon_before_key_or_take_starts_the_next() -> None:
    review = parse_answer("wall_loops=3 because a; b; layer_height=0.16; nozzle_temperature=210 "
                          "- hot; enough", _request())
    assert isinstance(review, Review)
    assert [(c.key, c.value, c.reason) for c in review.corrections] == [
        ("wall_loops", "3", "a; b"), ("layer_height", "0.16", "your correction"),
        ("nozzle_temperature", "210", "hot; enough")]
    took = read_answer("wall_loops=3 because a; take", _request())
    assert isinstance(took, Slipped), "take is an answer of its own"
    assert took.held == ("wall_loops=3",) and "nothing to take" in took.slips[0][1]


def test_a_semicolon_in_a_value_is_refused_not_sliced() -> None:
    """Without a reason, what follows a `;` that starts no answer would be the value."""
    said = parse_answer("wall_loops=3; ok", _request())
    assert said == ("'3; ok' isn't a value of wall_loops: a ';' goes before the next key=value "
                    "or take, or in a reason after because")


def test_a_pasted_review_gets_one_sentence_and_its_ok_approves_nothing() -> None:
    assert len(PASTED) == 20
    person = Person(PASTED, "stop")
    review = person.reviewer()(_request())
    assert review == Review(stop=True), "the ok in it approved nothing"
    after = person.shown[1:]
    assert after == ["  that paste of 20 lines is one answer with no correction in it, so "
                     "nothing's taken from it, and the ok in it approves and ends nothing: type "
                     "a correction as key=value because …, or ok on its own"]
    assert person.prompts.count("> ") == 2


def test_a_paste_with_corrections_asks_once_about_the_rest() -> None:
    pasted = ["wall_loops=3 because the faces show through", "looks good otherwise", "ok"]
    read = read_answer("\n".join(pasted), _request())
    assert isinstance(read, Slipped)
    assert read.held == ("wall_loops=3",)
    left = read.parts[-1]
    assert isinstance(left, Left) and left.lines == tuple(pasted[1:])
    assert read.slips[0][1] == ("'looks good otherwise' and 1 more lines of the paste aren't "
                                "answers (ok in a paste approves and ends nothing: type it on "
                                "its own)")
    person = Person(pasted, "drop")
    review = person.reviewer()(_request())
    assert not review.approved and [c.key for c in review.corrections] == ["wall_loops"]


def test_a_pasted_screen_offers_none_of_its_examples() -> None:
    """The review's own lines are indented, its examples among them."""
    screen = ["Answers: ok (approval gate) · key=value because … · stop",
              "  e.g. wall_loops=3 because it's decorative (several separated by ';');",
              "       colour_change=12.4 because white to black (a swap)"]
    said = read_answer("\n".join(screen), _request())
    assert isinstance(said, str) and said.startswith("that paste of 3 lines")


def test_a_slip_with_nothing_held_is_typed_again_at_the_review() -> None:
    """There's nothing to hold, so no fix to ask for: a stop typed next ends the job."""
    person = Person("walls=4; fans=9", "stop")
    assert person.reviewer()(_request()) == Review(stop=True)
    assert person.prompts == ["> ", "> "]
    assert person.said == "  'walls' isn't a setting in the filament or the process preset"


def test_typed_answers_one_at_a_time_are_still_each_an_answer() -> None:
    """Lines typed after one another, each at its prompt, aren't a paste."""
    person = Person("walls=4", "wall_loops=3", "ok")
    reviewer = person.reviewer()
    review = reviewer(_request())
    assert [c.key for c in review.corrections] == ["wall_loops"]
    assert person.prompts == ["> ", "> "]
    assert reviewer(_request()) == Review(approved=True)


def test_a_paste_at_the_fix_is_one_fix_and_its_ok_approves_nothing() -> None:
    person = Person(f"{GOOD[0]}; {BAD}", ["wall_loops=4 because it's thin", "ok"], "drop")
    review = person.reviewer()(_request())
    assert person.prompts == ["> ", "fix> ", "", "fix> "], "the ok is asked about, not left"
    assert not review.approved
    assert [(c.key, c.value) for c in review.corrections] == [
        ("wall_loops", "3"), ("wall_loops", "4")]


def test_lines_typed_ahead_of_the_prompt_are_each_an_answer() -> None:
    """Waiting before the prompt opened, they were typed ahead as the slice ran, not pasted."""
    lines = ["wall_loops=3", "ok"]
    asked: list[str] = []

    def read(prompt: str) -> str:
        asked.append(prompt)
        return lines.pop(0)

    reviewer = cli_reviewer(read, lambda text: None, forget=lambda: None, screen=lambda: None,
                            more=lambda: bool(lines))
    assert [c.key for c in reviewer(_request()).corrections] == ["wall_loops"]
    assert reviewer(_request()) == Review(approved=True)
    assert asked == ["> ", "> "]
