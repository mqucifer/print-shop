"""A piece map fixed where it's wrong, keeping the rest: `pieces retrace`, `fill`, `paint` and
`move-line`, on pictures and maps drawn in code, in the bench's image with no network
(mqucifer/print-shop#172). Needs `scripts/bench.py build`.

`bench_cases/planted_pieces.py` draws each: `shadow.png`, the clean panel with one dark patch
that's read as lead; `faint.png`, with half of one line faint; and `band.json`, three pieces
round a band of lead. Right answers are arithmetic.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import reports, skills
from tests.test_bench import Answer
from tests.test_pieces import flags_of, piece_at, rings

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"
BENCH = Path(host.__file__).parent
PALETTE = "blue,red,gold,green,white,purple"
#: The clean panel's cells, by a point inside each: 120 x 80 mm.
RED, WHITE, BLUE, GREEN, GOLD = (20, 60), (60, 60), (100, 60), (30, 5), (90, 20)
#: The shadow in `shadow.png`: an ellipse 9 by 7 mm round (30, 20), in the green cell.
SHADOW_MM2 = math.pi * 4.5 * 3.5
#: A box round it that the line between the green cell and the gold one crosses, at x 60.
BOX = "20,10,70,30"


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("pieces-local")
    shutil.copy(CASES / "planted_pieces.py", work / "planted_pieces.py")
    made = host.run(["planted_pieces.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr

    def ask(*args: Any) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


def traced(bench: Any, picture: str, to: str, *more: Any) -> Answer:
    answer = bench(
        "pieces", "trace", picture, "--width", 120, "--palette", PALETTE, "--to", to, *more
    )
    assert answer.verdict == "OK", answer.text
    return answer  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def shadowed(bench: Any) -> Answer:
    return traced(bench, "shadow.png", "s0.json")


@pytest.fixture(scope="module")
def clean(bench: Any) -> Answer:
    return traced(bench, "clean.png", "c0.json")


def saved(work: Path, name: str) -> dict[str, Any]:
    return json.loads((work / name).read_text())  # type: ignore[no-any-return]


def points_outside(found: dict[str, Any], box: list[float]) -> dict[int, list[str]]:
    """Each piece's outline points that lie outside a box, as the file writes them, in the
    order they go round, begun at the lowest."""
    x0, y0, x1, y1 = box
    out: dict[int, list[str]] = {}
    for piece in found["pieces"]:
        for ring in (piece["outline"], *piece.get("holes", [])):
            mine = [p for p in ring if not (x0 <= p[0] <= x1 and y0 <= p[1] <= y1)]
            if mine:
                k = mine.index(min(mine))
                out.setdefault(piece["id"], []).extend(json.dumps(p) for p in mine[k:] + mine[:k])
    return out


# A box traced again


def test_a_dark_patch_read_as_lead_is_cleared_by_a_retrace_of_a_box_round_it(
    bench: Any, shadowed: Answer
) -> None:
    """The default trace reads the shadow as a patch of lead inside the green cell. A retrace
    of a box round it at `--dark 20` reads it as that cell's glass, and nothing else moves."""
    assert flags_of(shadowed) >= {("wide-lead", None)}, shadowed.text
    assert shadowed.numbers["lead_widest_mm"] == pytest.approx(7.0, abs=0.3), "the shadow's 7 mm"
    green, gold = (piece_at(bench.work, "s0.json", at) for at in (GREEN, GOLD))
    answer = bench("pieces", "retrace", "s0.json", "--box", BOX, "--dark", 20, "--to", "s1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    # The box as read lies inside the box given, on the picture's pixels: 5 a mm.
    x0, y0, x1, y1 = numbers["box_mm"]
    assert 20 <= x0 < 20.21 and 10 <= y0 < 10.21 and 69.79 < x1 <= 70 and 29.79 < y1 <= 30
    assert (numbers["pieces_in_box_before"], numbers["pieces_in_box_after"]) == (2, 2)
    assert (numbers["lines_in_box_before"], numbers["lines_in_box_after"]) == (1, 1)
    assert (numbers["dead_ends_in_box_before"], numbers["dead_ends_in_box_after"]) == (1, 0)
    # The line between the two cells crosses the box's top and its bottom, and none ends there.
    assert numbers["lines_across_box_edge"] == 2
    assert numbers["lines_ending_at_box_edge"] == []
    assert numbers["glass_added_mm2"] == pytest.approx(SHADOW_MM2, abs=1.5)
    assert numbers["glass_taken_mm2"] == pytest.approx(0.0, abs=0.05)
    assert numbers["glass_added_by_pixels_mm2"] == pytest.approx(SHADOW_MM2, abs=1.5)
    assert (numbers["pieces_before"], numbers["pieces_after"]) == (5, 5)
    assert numbers["lead_widest_after_mm"] < 2.0 < numbers["lead_widest_before_mm"]
    assert "the two maps' pixels outside the box: 0 differ" in answer.text
    assert "every other piece's outline is the same, point for point" in answer.text
    assert {row["id"] for row in numbers["changed"]} == {green, gold}
    assert all(row["flags"] == [] for row in numbers["changed"])

    before, after = saved(bench.work, "s0.json"), saved(bench.work, "s1.json")
    # Every line outside the box, byte for byte: each piece that doesn't reach into it is
    # the same entry, and each that does has the same points outside it, in the same order.
    untouched = [row for row in rings(bench.work, "s0.json") if row[0] not in (green, gold)]
    assert len(untouched) == 3
    assert untouched == [row for row in rings(bench.work, "s1.json") if row[0] not in (green, gold)]
    assert json.dumps(untouched) == json.dumps(
        [row for row in rings(bench.work, "s1.json") if row[0] not in (green, gold)]
    )
    assert points_outside(before, numbers["box_mm"]) == points_outside(after, numbers["box_mm"])
    assert sum(len(v) for v in points_outside(after, numbers["box_mm"]).values()) == 20
    # The green cell is whole again, in its own colour, and the line beside it is as wide.
    now = {p["id"]: p for p in after["pieces"]}
    assert not now[green].get("holes") and now[green]["colour"] == "green"
    right = max(x for x, y in now[green]["outline"] if y0 <= y <= y1)
    left = min(x for x, y in now[gold]["outline"] if y0 <= y <= y1)
    assert left - right == pytest.approx(1.4, abs=0.25), "the line across the box"
    assert after["history"] == [
        {"fix": "retrace", "box": numbers["box_mm"], "dark": 20, "palette": None}
    ]
    again = bench("pieces", "check", "s1.json")
    assert again.verdict == "OK" and flags_of(again) == set(), again.text


def test_a_retrace_at_the_maps_own_darkness_reads_the_same_pieces(
    bench: Any, shadowed: Answer
) -> None:
    """Nothing to fix in the box: the same pieces, the same numbers, and the shadow still lead."""
    answer = bench("pieces", "retrace", "s0.json", "--box", BOX, "--to", "same.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["dark_under"] == 40
    assert answer.numbers["glass_added_mm2"] == pytest.approx(0.0, abs=0.5)
    assert answer.numbers["glass_taken_mm2"] == pytest.approx(0.0, abs=0.5)
    assert answer.numbers["lead_widest_after_mm"] == pytest.approx(7.0, abs=0.3)
    before, after = saved(bench.work, "s0.json"), saved(bench.work, "same.json")
    assert [(p["id"], p["colour"]) for p in before["pieces"]] == [
        (p["id"], p["colour"]) for p in after["pieces"]
    ]


def test_a_box_across_curved_lines_keeps_every_line_and_every_number(bench: Any) -> None:
    """`hills.png`: curved bands, slanted uprights and a sun. A box over its middle holds a
    piece wholly inside it and is crossed by ten lines. Traced again at the same darkness,
    each line joins the line found inside, and every piece keeps its number and colour."""
    first = traced(bench, "hills.png", "h0.json")
    answer = bench("pieces", "retrace", "h0.json", "--box", "25,15,95,62", "--to", "h1.json")
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert (numbers["pieces_in_box_before"], numbers["pieces_in_box_after"]) == (10, 10)
    assert (numbers["lines_in_box_before"], numbers["lines_in_box_after"]) == (17, 17)
    assert numbers["lines_across_box_edge"] == 10 and numbers["lines_ending_at_box_edge"] == []
    assert numbers["box_edge_step_mm"] <= 0.4, "two of the picture's pixels, at 5 a mm"
    assert "the two maps' pixels outside the box: 0 differ" in answer.text
    before, after = saved(bench.work, "h0.json"), saved(bench.work, "h1.json")
    assert [(p["id"], p["colour"]) for p in after["pieces"]] == [
        (p["id"], p["colour"]) for p in before["pieces"]
    ]
    assert points_outside(before, numbers["box_mm"]) == points_outside(after, numbers["box_mm"])
    again = bench("pieces", "check", "h1.json")
    assert flags_of(again) == flags_of(first), "the same rule breaks as the trace's"
    share = first.numbers["glass_share_pct"]
    assert again.numbers["glass_share_pct"] == pytest.approx(share, abs=0.05)


def test_a_line_that_ends_at_the_boxs_edge_is_said_with_where(bench: Any, shadowed: Answer) -> None:
    """A box that stops half way across the shadow: inside it the shadow is glass at
    `--dark 20`, and outside it's lead as it was, so that lead ends at the box's edge."""
    answer = bench(
        "pieces", "retrace", "s0.json", "--box", "20,10,30,30", "--dark", 20, "--to", "half.json"
    )
    assert answer.verdict == "OK", answer.text
    (end,) = answer.numbers["lines_ending_at_box_edge"]
    assert end["of"] == "outside"
    assert end["at"] == pytest.approx([30.0, 20.0], abs=0.3)
    assert end["width_mm"] == pytest.approx(7.0, abs=0.4), "the shadow's height at its middle"
    assert "a line ends at the box's edge at (" in answer.text
    assert "it runs to the box from outside, and inside is glass" in answer.text
    assert "1 end(s) at it: make the box larger" in answer.record["why"]
    assert answer.numbers["glass_added_mm2"] == pytest.approx(SHADOW_MM2 / 2, abs=1.5)
    assert "the two maps' pixels outside the box: 0 differ" in answer.text


@pytest.mark.parametrize(
    ("words", "says"),
    [
        (["--box", "20,10,21,30"], "a retrace needs 24 each way"),
        (["--box", BOX, "--dark", 0], "--dark is a whole number, 1 to 255"),
        (["--box", BOX, "--image", "gone.png"], "gone.png, isn't in the working directory"),
        (["--box", BOX, "--image", "hills.png"], "isn't the picture this map was traced from"),
        (["--box", BOX, "--palette", "mauve"], "the bench has no colour named 'mauve'"),
    ],
)
def test_a_retrace_that_cant_be_made_says_why(
    bench: Any, shadowed: Answer, words: list[Any], says: str
) -> None:
    answer = bench("pieces", "retrace", "s0.json", *words)
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert says in answer.record["why"]


def test_a_map_from_rules_has_no_picture_to_trace_again(bench: Any) -> None:
    answer = bench("pieces", "retrace", "rules.json", "--box", "5,5,25,15")
    assert answer.verdict == "NO ANSWER"
    assert "doesn't say which picture it was traced from" in answer.record["why"]


# A patch of lead


def test_a_patch_between_three_pieces_is_shared_by_which_is_nearest(bench: Any) -> None:
    """A band of lead from y 14 to 26 across a 60 mm panel, piece 1 above it and pieces 2 and
    3 below, with a 1.2 mm line between those two at x 30. It's as near piece 1 as the two
    below along y 20, so a 1.2 mm line is left there from y 19.4 to 20.6, and the line at
    x 30 runs on up to meet it. By hand: piece 1 takes 60 by 5.4 mm, and pieces 2 and 3 each
    29.4 by 5.4 mm."""
    answer = bench("pieces", "fill", "band.json", "--at", "10,20", "--to", "band1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    shares = {row["piece"]: row["area_mm2"] for row in numbers["patch_shares"]}
    assert shares[1] == pytest.approx(60 * 5.4, abs=0.1)
    assert shares[2] == pytest.approx(29.4 * 5.4, abs=0.1)
    assert shares[3] == pytest.approx(29.4 * 5.4, abs=0.1)
    # The band, and the mouth of the line that leaves it: 60 by 12 mm and under half a mm².
    assert numbers["patch_mm2"] == pytest.approx(720.0, abs=0.5)
    assert numbers["patch_widest_mm"] == pytest.approx(12.0, abs=0.05)
    assert numbers["glass_added_mm2"] == pytest.approx(sum(shares.values()), abs=0.01)
    assert numbers["glass_taken_mm2"] == 0, "a fill turns lead into glass, never glass into lead"
    assert numbers["patch_left_as_lead_mm2"] == pytest.approx(720.0 - sum(shares.values()), abs=0.5)
    assert "100.00% are the piece nearest them in the map before" in answer.text
    assert numbers["lead_widest_after_mm"] < 1.8, "lines, where the three meet"
    after = {p["id"]: p["outline"] for p in saved(bench.work, "band1.json")["pieces"]}
    # Away from where the three lines meet: there the border dips 0.015 mm toward the two.
    assert min(y for x, y in after[1] if not 25 < x < 35) == pytest.approx(20.6, abs=0.001)
    assert min(y for _, y in after[1]) == pytest.approx(20.6 - 0.6**2 / 24, abs=0.005)
    assert max(y for _, y in after[2]) == pytest.approx(19.4, abs=0.001)
    # The line between pieces 2 and 3 keeps its path: its two sides are where they were.
    assert max(x for x, _ in after[2]) == 29.4 and min(x for x, _ in after[3]) == 30.6
    assert saved(bench.work, "band1.json")["history"] == [
        {"fix": "fill", "at": [10.0, 20.0], "lead_mm": None}
    ]
    again = bench("pieces", "check", "band1.json")
    assert again.verdict == "OK" and flags_of(again) == set(), again.text


def test_a_patch_inside_one_piece_is_all_that_pieces_glass(bench: Any, shadowed: Answer) -> None:
    green = piece_at(bench.work, "s0.json", GREEN)
    at = "{},{}".format(*shadowed.numbers["lead_widest_at"])
    assert f"`pieces fill MAP --at {at}`" in shadowed.text, "the flag names its fix, with where"
    answer = bench("pieces", "fill", "s0.json", "--at", at, "--to", "filled.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["patch_shares"] == [
        {"piece": green, "area_mm2": pytest.approx(SHADOW_MM2, abs=1.5)}
    ]
    assert answer.numbers["patch_left_as_lead_mm2"] == pytest.approx(0.0, abs=0.01)
    assert not next(
        p for p in saved(bench.work, "filled.json")["pieces"] if p["id"] == green
    ).get("holes")
    assert flags_of(bench("pieces", "check", "filled.json")) == set()


def test_a_line_is_no_patch_and_isnt_filled(bench: Any) -> None:
    answer = bench("pieces", "fill", "rules.json", "--at", "30,10")
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    why = answer.record["why"]
    assert "no patch of lead within 3.6 mm of (30.0, 10.0)" in why
    assert "a patch is lead wider than 1.80 mm" in why and "`pieces drop-line MAP --at X,Y`" in why
    inside = bench("pieces", "fill", "rules.json", "--at", "10,10")
    assert "which is in piece 1's glass" in inside.record["why"]


# A piece's colour


def test_a_piece_painted_says_how_far_its_colour_is_from_the_pictures(
    bench: Any, clean: Answer
) -> None:
    white = piece_at(bench.work, "c0.json", WHITE)
    answer = bench("pieces", "paint", "c0.json", white, "teal=#008080", "--to", "c1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    # The picture's cell is (235, 235, 230), as it was drawn.
    assert numbers["colour_in_picture"] == [235, 235, 230]
    far = math.dist((0, 128, 128), (235, 235, 230))
    assert numbers["colour_off"] == pytest.approx(far, abs=0.01)
    assert numbers["colour_was_off"] < 30 < numbers["colour_off"], "white was the nearer"
    assert numbers["colour_nearest"] == "white"
    assert (numbers["colour"], numbers["colour_rgb"]) == ("teal", [0, 128, 128])
    assert numbers["glass_added_mm2"] == numbers["glass_taken_mm2"] == 0
    assert "read now over its outline: rgb [235, 235, 230]" in answer.text
    before, after = saved(bench.work, "c0.json"), saved(bench.work, "c1.json")
    assert after["colours"]["teal"] == {"rgb": [0, 128, 128], "pieces": 1}
    assert after["colours"]["white"]["pieces"] == 0
    was = {p["id"]: p for p in before["pieces"]}
    for piece in after["pieces"]:
        assert piece["colour"] == ("teal" if piece["id"] == white else was[piece["id"]]["colour"])
    # Nothing moves: the same points in the same order round, whatever the colour.
    assert [row[2:] for row in rings(bench.work, "c1.json")] == [
        row[2:] for row in rings(bench.work, "c0.json")
    ]
    assert after["history"] == [
        {"fix": "paint", "piece": white, "colour": "teal", "rgb": [0, 128, 128], "was": "white"}
    ]


def test_a_piece_takes_another_of_the_maps_colours_by_name(bench: Any, clean: Answer) -> None:
    white = piece_at(bench.work, "c0.json", WHITE)
    answer = bench("pieces", "paint", "c0.json", white, "red", "--to", "c2.json")
    assert answer.verdict == "OK", answer.text
    red = saved(bench.work, "c0.json")["colours"]["red"]["rgb"]
    assert answer.numbers["colour_rgb"] == red
    assert answer.numbers["colour_off"] == pytest.approx(math.dist(red, (235, 235, 230)), abs=0.01)
    assert saved(bench.work, "c2.json")["colours"]["red"]["pieces"] == 2


@pytest.mark.parametrize(
    ("colour", "says"),
    [
        ("mauve", "the map has no colour 'mauve', and the bench has no colour of that name"),
        ("white", "is white already"),
        ("red=#000000", "the map's red is rgb"),
        ("teal=#12", "isn't NAME=#rrggbb"),
    ],
)
def test_a_colour_that_cant_be_given_says_why(
    bench: Any, clean: Answer, colour: str, says: str
) -> None:
    white = piece_at(bench.work, "c0.json", WHITE)
    answer = bench("pieces", "paint", "c0.json", white, colour)
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert says in answer.record["why"]


# A line between two neighbours


def test_a_line_is_bent_and_the_glass_on_each_side_follows_it(bench: Any) -> None:
    """The rules' map: four cells of a 60 x 40 mm panel, a 1.2 mm cross of lead between them.
    The line between pieces 1 and 2, straight up x 30, is bent to (26, 10): piece 1 loses
    the triangle between the two paths, 20 by 4 mm over 2, and piece 2 gains it."""
    answer = bench(
        "pieces", "move-line", "rules.json", 1, 2, "--line", "30,0,26,10,30,20", "--to", "r1.json"
    )
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    assert numbers["line_width_mm"] == pytest.approx(1.2, abs=0.001), "as wide as it was"
    assert numbers["line_moved_mm"] == pytest.approx(4.0, abs=0.2)
    assert numbers["line_length_mm"] == pytest.approx(2 * math.hypot(4, 10), abs=0.001)
    rows = {row["id"]: row for row in numbers["changed"]}
    # The bent line is 1.54 mm longer than the straight one, and that's lead: 0.9 mm² a side.
    assert rows[1]["area_mm2"] == pytest.approx(29.4 * 19.4 - 40, abs=1.5)
    assert rows[2]["area_mm2"] == pytest.approx(29.4 * 19.4 + 40, abs=1.5)
    assert rows[1]["flags"] == rows[2]["flags"] == []
    before, after = saved(bench.work, "rules.json"), saved(bench.work, "r1.json")
    was, now = ({p["id"]: p for p in found["pieces"]} for found in (before, after))
    assert rings(bench.work, "r1.json")[2:] == [
        (p["id"], p["colour"], [[float(v) for v in xy] for xy in p["outline"]], [])
        for p in before["pieces"][2:]
    ], "pieces 3 and 4 are as they were"
    assert (now[1]["colour"], now[2]["colour"]) == (was[1]["colour"], was[2]["colour"])
    # At the bend the line is 1.2 mm wide, square to itself: 1.2 / cos of its slant across.
    slant = 1.2 * math.hypot(4, 10) / 10
    assert max(x for x, _ in now[1]["outline"]) == pytest.approx(30 - slant / 2, abs=0.01)
    bend = min(x for x, y in now[2]["outline"] if 5 < y < 15)
    assert bend == pytest.approx(26 + slant / 2, abs=0.05)
    assert after["history"] == [
        {"fix": "move-line", "a": 1, "b": 2, "line": [[30.0, 0.0], [26.0, 10.0], [30.0, 20.0]]}
    ]
    # The lines it met stay joined: no line ends in glass, and no lead is under a nozzle.
    again = bench("pieces", "check", "r1.json")
    assert again.verdict == "OK" and flags_of(again) == set(), again.text


@pytest.mark.parametrize(
    ("pieces", "line", "says"),
    [
        (
            (1, 2), "30,0,26,10,26,25",
            "the new path crosses another line: the one between pieces 1 and 3, into piece 3's",
        ),
        ((1, 2), "2,0,2,20", "leaves piece 1 under the map's rules: piece 1 is a sliver: 1.40 mm"),
        ((1, 2), "30,0,28,10", "cuts pieces 1 and 2 into 1, not two"),
        ((1, 4), "30,0,28,10", "pieces 1 and 4 have no line of their own between them"),
    ],
)  # fmt: skip
def test_a_path_that_crosses_another_line_or_leaves_a_piece_too_small_is_refused(
    bench: Any, pieces: tuple[int, int], line: str, says: str
) -> None:
    answer = bench("pieces", "move-line", "rules.json", *pieces, "--line", line, "--to", "no.json")
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert says in answer.record["why"]
    assert not (bench.work / "no.json").exists(), "nothing is written"


# A merged piece's colours


def test_a_merged_pieces_gradient_is_read_from_the_picture_again(bench: Any, clean: Answer) -> None:
    """The red cell joins the white one. The piece keeps white, and the picture across it
    runs from red to white: the fix's own reply flags it, and so does a check of its map."""
    red, white = piece_at(bench.work, "c0.json", RED), piece_at(bench.work, "c0.json", WHITE)
    run = math.dist((210, 40, 40), (235, 235, 230))
    merged = bench("pieces", "merge", "c0.json", red, white, "--to", "c3.json")
    assert merged.verdict == "OK", merged.text
    (row,) = merged.numbers["changed"]
    assert row["flags"] == ["gradient"] and row["colour_run"] == pytest.approx(run, abs=2.0)
    assert row["colour_run_fitted"] > 40, "and by a plane fitted to its colour"
    again = bench("pieces", "check", "c3.json")
    assert again.verdict == "FAIL" and flags_of(again) == {("gradient", white)}, again.text
    # A map that kept no run for the piece, as every merge wrote one before: read again.
    found = saved(bench.work, "c3.json")
    kept = next(p for p in found["pieces"] if p["id"] == white)
    kept["paint"] = {"rgb": kept["paint"]["rgb"]}
    (bench.work / "c4.json").write_text(json.dumps(found))
    assert flags_of(bench("pieces", "check", "c4.json")) == {("gradient", white)}
    # And where the picture isn't there, that's said, and --image says where it is.
    found["source"]["image"] = found["source"]["image_path"] = "gone.png"
    (bench.work / "c5.json").write_text(json.dumps(found))
    blind = bench("pieces", "check", "c5.json")
    assert flags_of(blind) == set()
    assert f"1 piece(s) a fix has changed, {white}, have no colour run read" in blind.text
    told = bench("pieces", "check", "c5.json", "--image", "clean.png")
    assert flags_of(told) == {("gradient", white)}


# A file of fixes


def test_each_local_fix_is_a_step_and_a_maps_history_makes_it_again(
    bench: Any, shadowed: Answer
) -> None:
    red, white = piece_at(bench.work, "s0.json", RED), piece_at(bench.work, "s0.json", WHITE)
    steps = [
        {"fix": "retrace", "box": [20, 10, 70, 30], "dark": 20},
        {"fix": "paint", "piece": white, "colour": "teal", "rgb": [0, 128, 128]},
        {"fix": "move-line", "a": red, "b": white, "line": [[40, 40], [36, 60], [40, 80.1]]},
    ]
    (bench.work / "local.json").write_text(json.dumps(steps))
    answer = bench("pieces", "fix", "s0.json", "local.json", "--to", "all.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    assert not reports.unlike("pieces", answer.numbers)
    assert (answer.numbers["steps_given"], answer.numbers["steps_made"]) == (3, 3)
    assert [row["fix"] for row in answer.numbers["steps"]] == ["retrace", "paint", "move-line"]
    assert answer.numbers["steps"][0]["lines_across_box_edge"] == 2
    assert answer.numbers["steps"][1]["colour_off"] == pytest.approx(277.63, abs=0.01)
    assert answer.numbers["steps"][2]["line_width_mm"] == pytest.approx(1.4, abs=0.15)
    # The same three commands, one by one, write the same map.
    bench("pieces", "retrace", "s0.json", "--box", BOX, "--dark", 20, "--to", "one-1.json")
    bench("pieces", "paint", "one-1.json", white, "teal=#008080", "--to", "one-2.json")
    last = bench(
        "pieces", "move-line", "one-2.json", red, white, "--line", "40,40,36,60,40,80.1",
        "--to", "one-3.json",
    )  # fmt: skip
    assert last.verdict == "OK", last.text
    assert rings(bench.work, "all.json") == rings(bench.work, "one-3.json")
    # And the map's history, given back as steps on the map it began from.
    found = saved(bench.work, "all.json")
    assert [h["fix"] for h in found["history"]] == ["retrace", "paint", "move-line"]
    (bench.work / "history.json").write_text(json.dumps(found["history"]))
    again = bench("pieces", "fix", "s0.json", "history.json", "--to", "again.json")
    assert again.verdict == "OK", again.text
    assert rings(bench.work, "again.json") == rings(bench.work, "all.json")
    assert saved(bench.work, "again.json")["colours"] == found["colours"]


def test_a_fill_is_a_step_and_a_step_that_cant_be_read_changes_nothing(bench: Any) -> None:
    (bench.work / "fill.json").write_text(json.dumps([{"fix": "fill", "at": [10, 20]}]))
    answer = bench("pieces", "fix", "band.json", "fill.json", "--to", "band2.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["steps"][0]["patch_shares"][0]["area_mm2"] == pytest.approx(324, abs=0.1)
    for step, says in (
        ({"fix": "retrace"}, "step 1, a retrace, needs `box`"),
        ({"fix": "retrace", "box": [0, 0, 9, 9], "dark": 300}, "`dark` is a whole number"),
        ({"fix": "fill"}, "step 1, a fill, needs `at`"),
        ({"fix": "paint", "piece": 1}, "step 1, a paint, needs `colour`"),
        ({"fix": "paint", "piece": 1, "colour": "red", "rgb": [1, 2]}, "`rgb` is [r, g, b]"),
        ({"fix": "move-line", "a": 1, "b": 2, "line": [[0, 0]]}, "`line` is the new path"),
    ):
        (bench.work / "unread.json").write_text(json.dumps([step]))
        refused = bench("pieces", "fix", "band.json", "unread.json", "--to", "never.json")
        assert refused.verdict == "NO ANSWER" and says in refused.record["why"], refused.text
    assert not (bench.work / "never.json").exists()


# What a darkness costs, and what the advice names


def test_a_trace_at_another_darkness_says_what_it_cost_against_the_default(bench: Any) -> None:
    """`faint.png`: the top half of the line between the red cell and the white one is faint.
    At `--dark 20` it isn't lead, so the two cells are one piece and the line's dark half
    ends in its glass: a piece and two lines fewer, and a dead end more, than the default."""
    usual = traced(bench, "faint.png", "f0.json")
    assert usual.numbers["pieces_count"] == 5 and flags_of(usual) == set()
    assert "pieces_at_default_dark" not in usual.numbers, "the default is set against nothing"
    answer = traced(bench, "faint.png", "f20.json", "--dark", 20)
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    assert (numbers["pieces_count"], numbers["pieces_at_default_dark"]) == (4, 5)
    assert (numbers["lines_count"], numbers["lines_at_default_dark"]) == (5, 7)
    assert (numbers["dead_ends_count"], numbers["dead_ends_at_default_dark"]) == (1, 0)
    assert (
        "--dark 20 against the default 40 on this picture: 4 piece(s) against 5, 5 line(s) "
        "against 7, 1 dead end(s) against 0"
    ) in answer.text
    assert "`pieces retrace MAP --box X0,Y0,X1,Y1 --dark 20` reads only that place" in answer.text


def named_fixes(text: str) -> set[str]:
    """Each `pieces` command a text names in backticks."""
    return set(re.findall(r"`pieces ([a-z][a-z-]*)", text))


def test_the_advice_names_only_commands_that_exist(bench: Any, shadowed: Answer) -> None:
    """Every fix `pieces check` and `pieces trace` name, for every flag there is, answers
    `--help`. `pieces check` said to redraw a patch of lead as glass, and no command did."""
    (bench.work / "advice.py").write_text(
        "import sys\n"
        "sys.path.insert(0, '/opt/bench')\n"
        "from bench.pieces_check import FLAGS, fixes_said\n"
        "print(fixes_said([{'flag': name} for name in FLAGS]))\n"
    )
    said = host.run(["advice.py"], bench.work, python=True)
    assert said.returncode == 0, said.stdout + said.stderr
    assert "redraw" not in said.stdout
    checked = bench("pieces", "check", "s0.json")
    low = traced(bench, "faint.png", "advice.json", "--dark", 20)
    # And every command the bench's own code and the skill name anywhere, as a reply may.
    written = "".join(path.read_text() for path in sorted(BENCH.glob("pieces*.py")))
    written += "".join(
        path.read_text()
        for path in sorted((skills.SKILLS / "designing-stained-glass").rglob("*.md"))
    )
    advice = named_fixes(said.stdout) | named_fixes(shadowed.text) | named_fixes(checked.text)
    advice |= named_fixes(low.text)
    assert {"fill", "retrace", "merge", "split", "extend", "drop-line", "widen"} <= advice
    for name in sorted(advice | named_fixes(written)):
        helped = host.run(["pieces", name, "--help"], bench.work)
        assert helped.returncode == 0 and f"pieces {name}" in helped.stdout, (name, helped.stdout)
    unknown = host.run(["pieces", "redraw", "--help"], bench.work)
    assert unknown.returncode != 0, "a command that doesn't exist is refused"
