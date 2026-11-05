"""The refinement pass with the real Orca and the bench's image (mqucifer/print-shop#51).

The issue's four fixtures, each a made-up approved run (`refine_fixtures.py`): a frame whose
pocket's ceiling is on a layer line, one whose ceiling falls between layers, two panes of
different colours, and a frame with a narrow bar. Each slices in Orca with a throwaway data
directory, as `slice_model` does. Under `slicer`, so CI, which has no Orca, leaves them out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.refine import approved
from print_shop.refine.context import Context
from print_shop.refine.refinement import Outcome, Pass
from print_shop.slicer import gcode
from print_shop.slicer.orca import _DEFAULT_BINARY
from tests.refine_fixtures import make_run

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.skipif(not _DEFAULT_BINARY.exists(), reason="no OrcaSlicer on this machine"),
]


@pytest.fixture(scope="module", autouse=True)
def _image() -> None:
    if host.image_id() is None:
        pytest.skip(f"no bench image {host.IMAGE}")


def _refine(root: Path, **kw: Any) -> Outcome:
    folder, ledger = make_run(root, "fx", **kw)
    context = Context.parse(0.4, ["Elegoo PETG Translucent", "grey=Elegoo PLA"])
    return Pass(approved.read(folder, ledger), ledger, context, commit="test").run()


def _one(done: Outcome, kind: str) -> list[dict[str, object]]:
    return [f.plain() for f in done.findings if f.kind == kind]


def test_a_ceiling_on_a_layer_line_pauses_where_the_bench_says(tmp_path: Path) -> None:
    done = _refine(tmp_path, ceiling=2.6)
    (pause,) = _one(done, "pause")
    assert "OK: pause after layer 13" in str(pause["read"])
    (placed,) = _one(done, "pause_placed")
    assert placed["numbers"] == {
        "orca_after_layers": [13],
        "bench_after_layers": [13],
        "agree": True,
    }
    (over,) = _one(done, "over_pane")
    numbers = over["numbers"]
    assert isinstance(numbers, dict) and numbers["room_mm"] == pytest.approx(0.1)
    assert not any(f.needs_change for f in done.findings)
    frame = next(done.folder.glob("plates/*-frame/*.gcode"))
    # Orca wrote the pause itself, from the machine profile: no G-code was edited.
    text = frame.read_text()
    assert text.count(";PAUSE_PRINT") == 1 and "\nM600\n" in text
    assert gcode.read(frame).pauses[0]["before_layer"] == 14
    assert "pauses after layer 13 (z 2.6)" in done.steps["A-with-pause"][-1]


def test_a_ceiling_between_layers_fails_and_its_change_is_checked(tmp_path: Path) -> None:
    done = _refine(tmp_path, ceiling=2.45, pane=0.95)
    (pause,) = _one(done, "pause")
    assert "FAIL" in str(pause["read"]) and pause["needs_change"]
    (over,) = _one(done, "over_pane")
    numbers = over["numbers"]
    assert isinstance(numbers, dict) and numbers["room_mm"] == pytest.approx(-0.05)
    (made,) = done.proposals
    assert "from z 2.45 to 2.6" in made["change"]
    assert made["checked"] == "checked", made["checked_said"]
    assert "CEILING 2.45 → 2.6" in made["checked_said"]
    assert done.steps == {"A-with-pause": []}


def test_two_panes_of_two_colours_each_get_orcas_colour(tmp_path: Path) -> None:
    done = _refine(tmp_path, panes=2)
    plates = {p.parent.name: p for p in done.folder.glob("plates/*/*.gcode")}
    assert set(plates) == {"A-with-pause-frame", "A-with-pause-amber", "A-with-pause-blue"}
    amber = gcode.read(plates["A-with-pause-amber"]).footer["filament_colour"]
    blue = gcode.read(plates["A-with-pause-blue"]).footer["filament_colour"]
    assert (amber, blue) == ("#FFB000", "#285ADC")
    assert len(_one(done, "time_filament")) == 3


def test_a_narrow_bar_is_gap_filled_and_a_narrower_one_is_bare_and_laid_twice(
    tmp_path: Path,
) -> None:
    filled = _refine(tmp_path / "one", bar=1.0)
    gaps = [f for f in _one(filled, "gap_fill") if f["layer_from"] == 2]
    numbers = gaps[0]["numbers"]
    assert isinstance(numbers, dict) and numbers["narrowest_mm"] == pytest.approx(0.231, abs=0.01)
    assert not _one(filled, "bare") and not _one(filled, "walls_overlap")
    narrow = _refine(tmp_path / "half", bar=0.5)
    (bare,) = _one(narrow, "bare")
    assert bare["layer_from"] == bare["layer_to"] == 1
    (twice,) = _one(narrow, "walls_overlap")
    numbers = twice["numbers"]
    assert twice["layer_from"] == 2
    assert isinstance(numbers, dict) and numbers["overlap_mm"] == pytest.approx(0.34, abs=0.02)
