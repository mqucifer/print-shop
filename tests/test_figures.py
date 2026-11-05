"""A fixed need carries each figure of its non-negotiable, at the request's own figure and at
both ends where the line gives a range or an exact figure (mqucifer/print-shop#52, item 3). No
Docker: the needs are read as `needs` reads them. The lines are made up for these tests.
"""

from __future__ import annotations

from typing import Any

import pytest

from print_shop.rounds import figures
from print_shop.rounds.needs import Need
from print_shop.rounds.request import Form, Line


def form(text: str) -> Form:
    return Form(non_negotiables=[Line("N1", "non_negotiable", text)])


def fixed(command: str, nid: str = "n1", **more: Any) -> Need:
    return Need(
        nid,
        "x",
        more.pop("source", "request"),
        "bench",
        command=command,
        covers=["N1"],
        fixed=True,
        **more,
    )


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("Every pane between 2.0 and 2.4 mm thick.", [(2.0, "low"), (2.4, "high")]),
        ("Panes 2.0 to 2.4 mm thick.", [(2.0, "low"), (2.4, "high")]),
        ("Panes 2.0–2.4 mm thick.", [(2.0, "low"), (2.4, "high")]),
        ("Each pane is 1.4 mm thick.", [(1.4, "both")]),
        ("A lip of no less than 0.9 mm on each face.", [(0.9, "low")]),
        ("Bars 3 mm wide or less.", [(3.0, "high")]),
        ("Up to 40 mm tall, and 70% of it open.", [(40.0, "high"), (70.0, "both")]),
        # Two figures joined by "and" are two, unless "between" makes them a range.
        ("A rim of 1.1 mm and a least of 0.5 mm.", [(1.1, "both"), (0.5, "both")]),
        # A count has no unit, and isn't read.
        ("Four panes, each at least 9 mm across.", [(9.0, "low")]),
    ],
)
def test_a_lines_figures_are_read_with_the_ends_its_words_give(
    text: str, read: list[tuple[float, str]]
) -> None:
    assert [(f.value, f.end) for f in figures.read(text)] == read


def test_a_range_is_carried_at_both_ends() -> None:
    line = form("Every pane between 2.0 and 2.4 mm thick.")
    # As on one of the evals: only the top of the range.
    wrong = figures.wrong([fixed("thickness p1.step p2.step --max 2.4")], line)
    assert len(wrong) == 1
    assert "its 2.0 mm, the low end of 2.0 to 2.4 mm, isn't carried as a lower limit" in wrong[0]
    assert "Their checks carry `--max 2.4`" in wrong[0]
    assert not figures.wrong([fixed("thickness p1.step p2.step --min 2.0 --max 2.4")], line)
    # The other way round is no better.
    assert figures.wrong([fixed("thickness p1.step --min 2.4 --max 2.0")], line)


def test_an_exact_figure_is_carried_at_both_ends_where_its_check_can() -> None:
    line = form("Each pane is 1.4 mm thick.")
    wrong = figures.wrong([fixed("thickness p.step --max 1.4")], line)
    assert len(wrong) == 1
    assert (
        "its 1.4 mm, an exact figure, so both ends, isn't carried as a lower limit too"
        in (wrong[0])
    )
    assert "(`--min`)" in wrong[0]
    assert not figures.wrong([fixed("thickness p.step --min 1.4 --max 1.4")], line)
    # Two fixed needs on one line carry it together.
    both = [fixed("thickness p.step --min 1.4"), fixed("thickness p.step --max 1.4", "n2")]
    assert not figures.wrong(both, line)
    # A number's limit has the other bound beside it.
    one = fixed("thickness p.step", number="thickest_mm", at_most=1.4)
    assert "as a lower limit too (`at_least`)" in figures.wrong([one], line)[0]


def test_an_exact_figure_one_option_carries_with_no_partner_is_as_far_as_a_check_can() -> None:
    line = form("A full rim of 1.1 mm, a least of 0.5 mm, and 55% of each pane seen.")
    rims = "capture f.step --pocket-z 2 --clearance 0.2 --pane-thickness 1.4"
    sized = fixed(f"{rims} --full-rim 1.1 --least-rim 0.5 --min-visible 55")
    assert not figures.wrong([sized], line), "a setting carries both ends, and so does 55%"


def test_a_check_by_another_measure_doesnt_carry_the_figure() -> None:
    line = form("A lip of no less than 0.9 mm on each face.")
    area = fixed("panes f.step --pocket-z 1 --clearance 0.2", number="openings", at_most=21.5)
    wrong = figures.wrong([area], line)
    assert "its 0.9 mm, its low end, isn't carried as a lower limit" in wrong[0]
    assert "such as an area for a rim, doesn't carry it" in wrong[0]
    assert not figures.wrong([fixed("capture f.step --pocket-z 1 --min-lip 0.9")], line)
    assert figures.wrong([fixed("thickness p.step --max 0.9")], line), "the wrong end"


def test_a_figure_the_sponsor_changed_by_a_note_is_the_notes() -> None:
    line = form("Each pane is 1.4 mm thick.")
    noted = fixed(
        "thickness p.step --min 1.2 --max 1.2", source="note", quote="make them 1.2 mm thick"
    )
    assert not figures.wrong([noted], line)
    assert figures.wrong([fixed("thickness p.step --min 1.2 --max 1.2")], line)


def test_a_line_only_an_eye_need_covers_isnt_held_to_it() -> None:
    line = form("A border 6 mm wide that reads as lead.")
    eye = Need("n1", "x", "request", "eye", covers=["N1"], fixed=True, look_for="it reads as lead")
    assert not figures.wrong([eye], line)
