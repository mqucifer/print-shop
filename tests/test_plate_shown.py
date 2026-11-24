"""A plate as it's shown: the map, and the end said once (mqucifer/print-shop#109).

Made-up plates. Nothing is written on the parts, so the map and the numbers are how each outcome
finds its part on the bed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.plate import BY_LAYER, OnPlate, PlateLayout, row_id
from print_shop.runtime import describe_all
from tests.test_ledger import record
from tests.test_plate_job import JOB, _plate_records


def _plate(*at: tuple[float, float]) -> PlateLayout:
    return PlateLayout(sequence=BY_LAYER, why="made up", bed=(256, 256), objects=tuple(
        OnPlate(n=n, name=f"cap-{n}.stl", x=x, y=y, where="-", height_mm=8, top_layer=40)
        for n, (x, y) in enumerate(at, 1)
    ))


def _where(plate: PlateLayout) -> dict[int, tuple[int, int]]:
    found = {}
    for row, line in enumerate(plate.drawn()):
        if line.startswith("|"):
            for col in range(1, len(line) - 1, 3):
                if line[col:col + 3].strip():
                    found[int(line[col:col + 3])] = (row, col)
    return found


def test_the_map_has_the_back_at_the_top_and_the_left_on_the_left() -> None:
    plate = _plate((205, 166), (185, 78), (51, 165))
    drawn = plate.drawn()
    assert (drawn[0], drawn[-1]) == ("back", "front")
    assert len({len(line) for line in drawn[1:-1]}) == 1, "square edged"
    at = _where(plate)
    assert at[2][0] > at[1][0], "part 2 is nearer the front"
    assert at[3][1] < at[2][1] < at[1][1], "left to right, by x"


def test_parts_close_together_are_drawn_closer_up_saying_so() -> None:
    plate = _plate((119, 135), (137, 119), (138, 136))
    assert plate.drawn()[0] == "back (closer up, not to the plate's edges)"
    at = _where(plate)
    assert len(at) == 3
    assert at[2][0] > at[1][0] and at[1][1] < at[3][1]


def test_a_full_plate_has_every_number() -> None:
    plate = _plate(*[(30.0 + 48 * col, 30.0 + 48 * row) for row in range(5) for col in range(5)])
    assert sorted(_where(plate)) == list(range(1, 26))


def test_no_map_rather_than_a_wrong_one() -> None:
    assert _plate((100, 100), (100, 100)).drawn() == []


@pytest.mark.parametrize("count", [3, 9])
def test_the_end_says_what_the_parts_share_once(tmp_path: Path, count: int) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(record())
    ledger.record_start_requested(*_plate_records(count=count))
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)
    ledger.record_answer(row_id(JOB, 2), Answer(result=Result.FAILED, symptom=Symptom.WARPING))
    text = describe_all(ledger.rows())
    assert text.count("g-code         ") == 2, "the lone print's, and the plate's once"
    assert text.count("ended          ") == 2
    assert text.count("outcome        ") == count + 1, "each part's own"
    assert "outcome        failed, warping" in text
    for n in range(1, count + 1):
        assert f"part {n} of {count}" in text and f"its row        {row_id(JOB, n)}\n" in text
        assert f"cap-{n}.stl  (sha256 {str(n) * 12}" in text, "no detail lost"
    plate = text[text.index(f"job            {JOB}"):]
    assert len(plate.splitlines()) < 30 + 7 * count
