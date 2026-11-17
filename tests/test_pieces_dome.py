"""`pieces dome` and `pieces flatten` on domes made from rules written here, in the bench's
image with no network (mqucifer/print-shop#154). Needs `scripts/bench.py build`.

The shade is the planned size: 200 mm across, 130 mm tall, 12 repeats. Right answers are
arithmetic: an ellipse's quarter, a chord, and a chain of rows bent by their angles, each worked
out here in plain Python and not by the bench's code.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import reports
from tests.test_bench import Answer

pytestmark = pytest.mark.bench
DIAMETER, HEIGHT, REPEATS, TOP, LEAD = 200.0, 130.0, 12, 40.0, 1.4
#: The issue's dome: three bands, the last taking what the first two leave.
THREE = [
    {"pattern": "grid", "height_mm": 56, "rows": 2, "columns": 2, "colours": ["amber", "green"]},
    {"pattern": "swag", "height_mm": 46, "depth_mm": 14, "colours": ["blue", "cream"]},
    {"pattern": "strip", "count": 2, "colours": ["red", "gold"]},
]
#: One band of each pattern, top to hem.
SIX = [
    {"pattern": "grid", "height_mm": 48, "rows": 2, "columns": 2, "colours": ["amber", "green"]},
    {"pattern": "swag", "height_mm": 38, "depth_mm": 12, "colours": ["blue", "cream"]},
    {"pattern": "jewels", "height_mm": 18, "diameter_mm": 8, "count": 2,
     "colours": ["red", "gold"]},
    {"pattern": "strip", "height_mm": 8, "colours": ["green"]},
    {"pattern": "triangles", "height_mm": 16, "count": 2, "colours": ["amber", "blue"]},
    {"pattern": "scallop", "depth_mm": 10, "colours": ["red"]},
]  # fmt: skip


def side_mm(steps: int = 20000) -> float:
    """The dome's side from its opening's edge to its hem: an ellipse's arc, by Simpson's rule."""
    a, b = DIAMETER / 2, HEIGHT
    start = math.asin(TOP / DIAMETER)
    width = (math.pi / 2 - start) / steps

    def speed(k: int) -> float:
        t = start + k * width
        return math.hypot(a * math.cos(t), b * math.sin(t))

    inner = sum((4 if k % 2 else 2) * speed(k) for k in range(1, steps))
    return (speed(0) + inner + speed(steps)) * width / 3


def bent_back(surface: dict[str, Any], x: float, y: float) -> tuple[float, float, float]:
    """A slice's flat point in space, by its file's own angles: a chain of rows from the hem
    up, each turned by the bend at its fold, then turned round the dome's axis."""
    hem = surface["hem"]
    folds = sorted(surface["folds"], key=lambda f: f["y_mm"])
    starts = [hem["at"][1], *[f["y_mm"] for f in folds]]
    tilt, out, z = math.radians(hem["tilt_deg"]), hem["out_mm"], hem["z_mm"]
    for i, start in enumerate(starts):
        last = i == len(starts) - 1
        along = (y if last or y <= starts[i + 1] else starts[i + 1]) - start
        out, z = out - along * math.cos(tilt), z + along * math.sin(tilt)
        if last or y <= starts[i + 1]:
            break
        tilt -= math.radians(folds[i]["bend_deg"])
    across, turn = x - hem["at"][0], math.radians(surface["turn_deg"])
    return (
        out * math.cos(turn) - across * math.sin(turn),
        out * math.sin(turn) + across * math.cos(turn),
        z,
    )


def off_ellipsoid(point: tuple[float, float, float] | list[float]) -> float:
    x, y, z = point
    return (math.hypot(x, y) / (DIAMETER / 2)) ** 2 + (z / HEIGHT) ** 2 - 1.0


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("dome")
    for name, bands in (("three", THREE), ("six", SIX)):
        (work / f"{name}.json").write_text(json.dumps({"lead_mm": LEAD, "bands": bands}))

    def ask(*args: Any) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


def dome(bench: Any, name: str, *more: Any) -> Answer:
    return bench(  # type: ignore[no-any-return]
        "pieces", "dome", "--diameter", DIAMETER, "--height", HEIGHT, "--repeats", REPEATS,
        "--bands", f"{name}.json", "--to", f"{name}.pieces.json", *more,
    )  # fmt: skip


@pytest.fixture(scope="module")
def three(bench: Any) -> Answer:
    answer = dome(bench, "three")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    return answer


@pytest.fixture(scope="module")
def three_flat(bench: Any, three: Answer) -> Answer:
    answer = bench("pieces", "flatten", "three.pieces.json", "--slices", 12)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    return answer  # type: ignore[no-any-return]


def saved(bench: Any, name: str) -> dict[str, Any]:
    return json.loads((bench.work / name).read_text())  # type: ignore[no-any-return]


# The dome's map


def test_a_dome_of_three_bands_is_its_pieces_on_the_dome(bench: Any, three: Answer) -> None:
    numbers = three.numbers
    side = side_mm()
    assert numbers["meridian_mm"] == pytest.approx(side, abs=1e-3)
    assert numbers["top_mm"] == TOP, "a fifth of the diameter, with no --top"
    assert numbers["pieces_count"] == REPEATS * (4 + 2 + 2)
    assert [b["pieces_a_repeat"] for b in numbers["bands"]] == [4, 2, 2]
    # The last band has no height: it takes what the first two leave.
    assert numbers["bands"][2]["from_mm"] == 102 and numbers["bands"][2]["to_mm"] == pytest.approx(
        side, abs=1e-3
    )
    chord = DIAMETER * math.sin(math.pi / REPEATS)
    assert numbers["slice_would_be_mm"] == [
        pytest.approx(chord, abs=1e-3),
        pytest.approx(side, abs=1e-3),
    ]
    assert numbers["glass_share_on_dome_pct"] == pytest.approx(
        numbers["glass_share_on_dome_by_pixels_pct"], abs=0.5
    )
    assert numbers["glass_patches"] == numbers["pieces_count"] and numbers["lead_bodies"] == 1
    assert len(three.record["second_check"]) == 2
    assert (bench.work / numbers["picture"]).is_file()
    assert "from the side and from 35° over it, one slice of 12 laid flat" in three.text
    assert not reports.unlike("pieces", numbers)
    made = saved(bench, "three.pieces.json")
    assert made["piece_map"] == 1 and made["source"]["by"] == "pieces dome"
    assert made["surface"]["kind"] == "dome" and made["surface"]["repeats"] == REPEATS
    assert made["panel"]["width_mm"] == pytest.approx(math.pi * DIAMETER, abs=1e-3)
    assert made["panel"]["height_mm"] == pytest.approx(side, abs=1e-3)


def test_the_lead_is_as_wide_at_the_domes_top_as_at_its_hem(bench: Any, three: Answer) -> None:
    """Half a lead on the dome is 0.7 mm round it. The map's x is the way round at the hem, so
    at the opening's edge, where the dome is 40 mm across, that's 0.7 * 100 / 20 on the map; at
    the hem, where the map is true to size, it's 0.7."""
    made = saved(bench, "three.pieces.json")
    side = side_mm()
    first = next(p for p in made["pieces"] if p["id"] == 1)["outline"]
    top = [x for x, y in first if abs(y - side) < 1e-3]
    column = math.pi * DIAMETER / REPEATS / 2
    assert min(top) == pytest.approx(0.7 * 100 / 20, abs=2e-3), "the glass's edge at the top"
    assert max(top) == pytest.approx(column - 0.7 * 100 / 20, abs=2e-3)
    last = next(p for p in made["pieces"] if p["id"] == 7)["outline"]
    hem = [x for x, y in last if abs(y) < 1e-3]
    assert min(hem) == pytest.approx(0.7, abs=2e-3) and max(hem) == pytest.approx(
        column - 0.7, abs=2e-3
    )
    # Up the dome a line is as wide on the map as on the dome: the first two bands' rows.
    below = next(p for p in made["pieces"] if p["id"] == 3)["outline"]
    gap = min(y for _x, y in first) - max(y for _x, y in below)
    assert gap == pytest.approx(LEAD, abs=2e-3)


def test_every_pattern_makes_a_dome_whose_slice_checks_and_builds(bench: Any) -> None:
    made = dome(bench, "six")
    assert made.verdict == "OK", made.text
    assert [b["pattern"] for b in made.numbers["bands"]] == [b["pattern"] for b in SIX]
    assert [b["pieces_a_repeat"] for b in made.numbers["bands"]] == [4, 2, 6, 1, 5, 1]
    flat = bench("pieces", "flatten", "six.pieces.json")
    assert flat.verdict == "OK", flat.text
    assert flat.numbers["slices_count"] == REPEATS, "the map's repeats, with no --slices"
    assert flat.numbers["rows_count"] == 7 and len(flat.numbers["folds"]) == 6
    assert flat.numbers["alike"] == [list(range(1, 13))]
    # A scalloped hem: the slice's outline isn't a rectangle, and rises at its sides.
    one = saved(bench, "six.slice-01.pieces.json")
    assert "outline" in one["panel"] and min(y for _x, y in one["panel"]["outline"]) == 0
    checked = bench("pieces", "check", "six.slice-01.pieces.json")
    assert checked.verdict == "OK" and checked.code == 0, checked.text
    assert checked.numbers["pieces_count"] == 19 and not checked.numbers["flagged"]
    assert checked.numbers["glass_share_change_pct"] == pytest.approx(0, abs=1e-3)
    built = bench(
        "pieces", "build", "six.slice-01.pieces.json", "--thickness", 3, "--pane", 1,
        "--lip", "1.2,0.8", "--play", 0.25, "--border", LEAD / 2, "--build-out", "six",
    )  # fmt: skip
    assert built.verdict == "OK" and built.code == 0, built.text
    assert built.numbers["panes_count"] == 19 and built.numbers["frame_solids"] == 1
    glass = built.numbers["glass"]
    held = bench(
        "capture", glass["frame"], "--pocket-z", glass["pocket_z"], "--clearance", 0.25,
        "--pane-thickness", 1, "--panes", 19,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    paused = bench("pause", glass["frame"], "--pocket-z", glass["pocket_z"], "--pane-thickness", 1)
    assert paused.verdict == "OK", paused.text
    whole = bench("thin", glass["frame"], "--one-body")
    assert whole.verdict == "OK", whole.text


# Its slices


def test_the_flattened_slices_bent_by_their_angles_give_the_dome_back(
    bench: Any, three_flat: Answer
) -> None:
    """The issue's test: N = 12 and three bands. Within the stated tolerance, 0.01 mm: every
    corner, where a fold meets a cut, is on the dome; every point of every piece is on the flat
    through its row's corners; and each edge lies on its neighbour's. Between its corners a
    flat row stands off the curved dome by the chord across it and more."""
    numbers = three_flat.numbers
    assert numbers["tolerance_mm"] == 0.01
    assert numbers["corners_off_mm"] < 1e-3 and numbers["flats_off_mm"] < 1e-3
    assert numbers["edges_off_mm"] < 1e-3
    # Two rows of grid, the swag and the strip: four rows, three folds.
    assert numbers["rows_count"] == 4 and len(numbers["folds"]) == 3
    sag = DIAMETER / 2 * (1 - math.cos(math.pi / REPEATS))
    assert sag - 0.01 < numbers["rows_off_dome_mm"] < 2.5 * sag
    assert not reports.unlike("pieces", numbers)
    maps = [saved(bench, name) for name in numbers["maps"]]
    assert len(maps) == 12
    side = side_mm()
    for number, one in enumerate(maps, start=1):
        surface = one["surface"]
        assert surface["kind"] == "dome-slice" and surface["slice"] == number
        assert surface["turn_deg"] == pytest.approx(30 * number - 15)
        tilts = [row["tilt_deg"] for row in surface["rows"]]
        bends = [fold["bend_deg"] for fold in surface["folds"]]
        assert all(b > 0 for b in bends), "a dome bends one way, toward its axis"
        assert sum(bends) == pytest.approx(tilts[0] - tilts[-1], abs=1e-4)
        for name, cut in (("left", 30 * (number - 1)), ("right", 30 * number)):
            edge = surface["edges"][name]
            assert edge["half_line_mm"] == LEAD / 2
            for flat, on_dome in zip(edge["flat"], edge["on_dome"], strict=True):
                back = bent_back(surface, *flat)
                # On the dome: on the ellipsoid, at the cut's angle round it.
                assert off_ellipsoid(back) == pytest.approx(0, abs=1e-6)
                round_deg = math.degrees(math.atan2(back[1], back[0]))
                assert (round_deg - cut + 180) % 360 - 180 == pytest.approx(0, abs=1e-5)
                assert math.dist(back, on_dome) < 1e-4
            hem, top = bent_back(surface, *edge["flat"][0]), bent_back(surface, *edge["flat"][-1])
            assert math.hypot(hem[0], hem[1]) == pytest.approx(DIAMETER / 2, abs=1e-4)
            assert hem[2] == pytest.approx(0, abs=1e-4)
            assert math.hypot(top[0], top[1]) == pytest.approx(TOP / 2, abs=1e-4)
        # A piece's points bent back lie inside the dome, between its corners, and never
        # further in than the reply's figure.
        for piece in one["pieces"]:
            for x, y in piece["outline"]:
                assert -0.2 < off_ellipsoid(bent_back(surface, x, y)) < 1e-6
        after = maps[number % 12]["surface"]
        assert surface["edges"]["right"]["slice"] == number % 12 + 1
        assert after["edges"]["left"]["slice"] == number
        for mine, theirs in zip(
            surface["edges"]["right"]["on_dome"], after["edges"]["left"]["on_dome"], strict=True
        ):
            assert math.dist(mine, theirs) < 1e-5
    # Flat, a slice is as long as its rows are, a little under the dome's side, and a chord
    # across less the half lead at each cut.
    width, length = numbers["slice_mm"]
    chord = DIAMETER * math.sin(math.pi / REPEATS)
    assert chord - LEAD - 0.05 < width < chord - LEAD + 0.05
    assert side - 4 < length < side
    assert numbers["fits_bed"] is True and numbers["bed_mm"] == [256, 256]


def test_fewer_rows_fold_at_the_lines_that_leave_them_nearest_the_dome(bench: Any) -> None:
    """The six-band dome has six lines all the way round, so seven rows at most. The reply
    says what each count costs, and `--rows` takes one of them."""
    dome(bench, "six")
    most = bench("pieces", "flatten", "six.pieces.json", "--to", "most")
    table = most.numbers["rows_choice"]
    assert [one["rows"] for one in table] == [1, 2, 3, 4, 5, 6, 7]
    assert most.numbers["rows_count"] == 7, "every line, with no --rows"
    assert table[6]["off_dome_mm"] == pytest.approx(most.numbers["rows_off_dome_mm"])
    assert "rows against bends" in most.text and "7 flat row(s) of 7" in most.record["why"]
    # A row more is never further off, and one flat row has no fold.
    off = [one["off_dome_mm"] for one in table]
    assert off == sorted(off, reverse=True) and table[0]["most_bend_deg"] == 0
    sag = DIAMETER / 2 * (1 - math.cos(math.pi / REPEATS))
    assert off[-1] > sag - 0.01 and off[0] > 5 * sag
    four = bench("pieces", "flatten", "six.pieces.json", "--rows", 4, "--to", "four")
    assert four.verdict == "OK", four.text
    assert four.numbers["rows_count"] == 4 and len(four.numbers["folds"]) == 3
    assert four.numbers["rows_off_dome_mm"] == pytest.approx(table[3]["off_dome_mm"])
    assert max(f["bend_deg"] for f in four.numbers["folds"]) == pytest.approx(
        table[3]["most_bend_deg"], abs=1e-4
    )
    assert four.numbers["corners_off_mm"] < 1e-3 and four.numbers["flats_off_mm"] < 1e-3
    checked = bench("pieces", "check", "four.slice-01.pieces.json")
    assert checked.verdict == "OK", checked.text
    none = bench("pieces", "flatten", "six.pieces.json", "--rows", 8, "--to", "no")
    assert none.verdict == "NO ANSWER" and "1 to 7 flat row(s)" in none.record["why"]


def test_every_slice_passes_pieces_check(bench: Any, three_flat: Answer) -> None:
    """All twelve are the same map, piece for piece, so one reading is every slice's: the files
    say so point for point, and each is read."""
    assert three_flat.numbers["alike"] == [list(range(1, 13))]
    first = saved(bench, "three.slice-01.pieces.json")
    for name in three_flat.numbers["maps"]:
        one = saved(bench, name)
        assert one["pieces"] == first["pieces"] and one["panel"] == first["panel"], name
    for name in ("three.slice-01.pieces.json", "three.slice-08.pieces.json"):
        checked = bench("pieces", "check", name)
        assert checked.verdict == "OK" and checked.code == 0, checked.text
        assert checked.numbers["pieces_count"] == 8 and not checked.numbers["flagged"]
        assert checked.numbers["flags"] == dict.fromkeys(checked.numbers["flags"], 0)
    # Flat, the lead is the dome's, less the little a flat row is narrower than the dome.
    assert three_flat.numbers["lead_flat_mm"] == pytest.approx(LEAD, abs=0.1)


def test_fewer_slices_are_wider_and_stand_further_off_the_dome(bench: Any, three: Answer) -> None:
    six = bench("pieces", "flatten", "three.pieces.json", "--slices", 6, "--to", "wide")
    assert six.verdict == "OK", six.text
    assert six.numbers["maps"][0] == "wide.slice-01.pieces.json"
    assert six.numbers["slices"][0]["pieces_count"] == 16, "two repeats a slice"
    sag = DIAMETER / 2 * (1 - math.cos(math.pi / 6))
    assert six.numbers["rows_off_dome_mm"] > sag - 0.01
    limited = bench(
        "pieces", "flatten", "three.pieces.json", "--slices", 6, "--to", "wide", "--max-off", 5
    )
    assert limited.verdict == "FAIL" and limited.code == 1
    assert "over --max-off 5" in limited.record["why"]
    small = bench("pieces", "flatten", "three.pieces.json", "--to", "small", "--bed", "100,150")
    assert small.verdict == "FAIL" and small.numbers["fits_bed"] is False
    assert "the bed is 100 by 150" in small.record["why"]


@pytest.mark.parametrize(
    ("slices", "says"),
    [
        (5, "crosses piece"),
        (24, "crosses piece"),
        (2, "3 or more"),
    ],
)
def test_a_cut_that_isnt_along_lead_is_refused(
    bench: Any, three: Answer, slices: int, says: str
) -> None:
    answer = bench("pieces", "flatten", "three.pieces.json", "--slices", slices, "--to", "no")
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    assert says in answer.record["why"], answer.record["why"]


def test_a_flat_map_isnt_a_domes_and_a_domes_chart_says_so_to_check(
    bench: Any, three_flat: Answer
) -> None:
    flat = bench("pieces", "flatten", "three.slice-01.pieces.json")
    assert flat.verdict == "NO ANSWER" and "isn't a dome's map" in flat.record["why"]
    chart = bench("pieces", "check", "three.pieces.json")
    assert any("a dome's chart" in note for note in chart.record["notes"]), chart.text


def test_a_fix_keeps_a_slices_folds_and_edges(bench: Any, three_flat: Answer) -> None:
    before = saved(bench, "three.slice-01.pieces.json")
    merged = bench("pieces", "merge", "three.slice-01.pieces.json", 1, 2, "--to", "joined.json")
    assert merged.verdict == "OK", merged.text
    after = saved(bench, "joined.json")
    assert after["surface"] == before["surface"] and len(after["pieces"]) == 7


# The bands file


@pytest.mark.parametrize(
    ("bands", "says"),
    [
        ([{**THREE[0], "pattern": "plaid"}], "one of grid, swag, jewels, strip"),
        ([{**THREE[0], "height_mm": 50}, {**THREE[1], "height_mm": 50}], "come to 100.00 mm"),
        ([THREE[0], THREE[2], {**THREE[2]}], "have no `height_mm`"),
        ([{**THREE[0], "depth_mm": 4}, THREE[2]], "which that pattern doesn't take"),
        ([{"pattern": "scallop", "depth_mm": 8, "height_mm": 30, "colours": ["red"]}, THREE[2]],
         "it's the last band"),
        ([{**THREE[1], "depth_mm": 45}, THREE[2]], "no room for a swag"),
        ([{**THREE[0], "colours": ["heliotrope"]}, THREE[2]], "isn't one the bench has a name for"),
        ([{**THREE[0], "columns": 40}, THREE[2]], "no one piece of glass left"),
        ([{"pattern": "jewels", "height_mm": 12, "diameter_mm": 9, "colours": ["red"]}, THREE[2]],
         "no glass over and under a jewel"),
    ],
)  # fmt: skip
def test_a_bands_file_that_cant_be_laid_is_said(bench: Any, bands: Any, says: str) -> None:
    (bench.work / "wrong.json").write_text(json.dumps({"bands": bands}))
    answer = dome(bench, "wrong")
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    assert says in answer.record["why"], answer.record["why"]


def test_a_dome_with_no_opening_is_refused(bench: Any) -> None:
    answer = dome(bench, "three", "--top", 0)
    assert answer.verdict == "NO ANSWER" and "--top is the opening" in answer.record["why"]
    assert Path(bench.work / "three.json").is_file()
