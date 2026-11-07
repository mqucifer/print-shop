"""A figure against its limit, one rule everywhere (mqucifer/print-shop#72): the bench's `limits`,
and a need's number held by it."""

from __future__ import annotations

from typing import Any

from print_shop.bench import limits
from print_shop.rounds import needs
from print_shop.rounds.needs import Need


def test_a_figure_at_its_limit_to_arithmetics_noise_passes_and_past_it_doesnt() -> None:
    """`eval-fit-3`'s pause: 0.4 mm of room was 0.40000000000000036 by the floats."""
    room = 0.2 + 0.2 * 14 - (1.0 + 1.6)
    assert room != 0.4
    assert not limits.over(room, 0.4) and not limits.under(room, 0.4)
    assert limits.over(0.4001, 0.4) and limits.under(0.3999, 0.4)
    assert not limits.over(0.4 + limits.ARITHMETIC / 2, 0.4)
    assert limits.over(0.4 + 2 * limits.ARITHMETIC, 0.4), "a millionth, and no band past it"


def test_a_figure_said_beside_its_limit_is_never_said_as_the_limit() -> None:
    """A reply said "0.400 mm ... over 0.4 mm"."""
    assert limits.said(0.40000000000000036, 0.4) == "0.400", "at the limit, as it is"
    assert limits.said(0.4001, 0.4) == "0.4001"
    assert limits.said(0.40002, 0.4) == "0.40002"
    assert limits.said(0.3999, 0.4) == "0.3999"
    assert limits.said(0.9, 1.2, None) == "0.9"
    assert limits.said(100.000002, 100, None) == "100.000002"


def judged(need: Need, value: float) -> Any:
    record = {"verdict": "CLEAR", "why": "", "numbers": {"pairs": [{"gap_mm": value}]}}
    return needs.judge(need, 0, "verdict: CLEAR: ok\n", record)


def test_a_needs_number_is_held_by_the_benchs_own_rule() -> None:
    """`needs` compared a figure as the reply rounded it; now it holds the figure as the bench
    judged it, by the same rule."""
    need = Need(
        "play", "x", "chosen", "bench", command="clearance a.step b.step",
        number="pairs.0.gap_mm", at_least=0.25, at_most=0.4,
    )  # fmt: skip
    assert judged(need, 0.24999999999999853).passed, "OCCT's 0.25 round a curve"
    assert judged(need, 0.40000000000000036).passed
    under = judged(need, 0.24996)
    assert not under.passed and "is 0.24996, under 0.25" in under.why
    over = judged(need, 0.4001)
    assert not over.passed and "is 0.4001, over 0.4" in over.why
