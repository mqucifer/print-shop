"""A piece map fixed where it's wrong, keeping the rest: `pieces retrace`, `fill`, `paint` and
`move-line`, on pictures and maps drawn in code, in the bench's image with no network
(mqucifer/print-shop#172). Needs `scripts/bench.py build`.

`bench_cases/planted_pieces.py` draws each: `shadow.png`, the clean panel with one dark patch
that's read as lead; `faint.png`, with half of one line faint; and `band.json`, three pieces
round a band of lead. Right answers are arithmetic. And for a fix's new edges
(mqucifer/print-shop#174): `meet.png` and `ragged.png`, a patch of lead among four curved
pieces, and `stalk.json`, a share that hangs off its piece by a stalk. Those are held by
building: `pieces build` takes the map a fix leaves wherever it took the map before.
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
#: A build, as the other pieces' tests give it.
BUILD = ("--thickness", 3, "--pane", 1, "--lip", "1.2,0.6", "--play", 0.25)
#: The most points a changed piece's outline may have, as a multiple of its points before, on
#: a traced map. A share's edge is its border's side, drawn to the detail the trace drew the
#: rest of the outline at, in place of the stretch of outline that lay along the patch: about
#: as many points as it had. Twice as many says the edge follows its steps again: on the eval
#: the four pieces round a filled patch had 2.9 to 4.3 times their points
#: (mqucifer/print-shop#174), and here they have 1.4 at the most.
MOST_POINTS = 2.0
#: A box round the patch in `meet.png`, which its four lines cross.
MEET_BOX = "38,24,66,46"


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


def traced_wide(bench: Any, picture: str, to: str) -> Answer:
    """`meet.png` and `ragged.png`, 100 mm wide, at the default darkness."""
    answer = bench("pieces", "trace", picture, "--width", 100, "--palette", PALETTE, "--to", to)
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
    x 30 runs on up to meet it. Where the three lines meet, the band is as near all three
    0.015 mm under y 20, 0.6² / 24: under the map's detail, so each border is drawn straight
    from the panel's edge to there (mqucifer/print-shop#174). By hand: piece 1 takes 60 by
    5.4 mm and two triangles 30 by 0.015, and pieces 2 and 3 each 29.4 by 5.4 mm less one."""
    answer = bench("pieces", "fill", "band.json", "--at", "10,20", "--to", "band1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    shares = {row["piece"]: row["area_mm2"] for row in numbers["patch_shares"]}
    dip = 0.6**2 / 24
    assert shares[1] == pytest.approx(60 * 5.4 + 30 * dip, abs=0.02)
    assert shares[2] == pytest.approx(29.4 * 5.4 - 29.4 * dip / 2, abs=0.02)
    assert shares[3] == pytest.approx(29.4 * 5.4 - 29.4 * dip / 2, abs=0.02)
    # The band, and the mouth of the line that leaves it: 60 by 12 mm and under half a mm².
    assert numbers["patch_mm2"] == pytest.approx(720.0, abs=0.5)
    assert numbers["patch_widest_mm"] == pytest.approx(12.0, abs=0.05)
    assert numbers["glass_added_mm2"] == pytest.approx(sum(shares.values()), abs=0.01)
    assert numbers["glass_taken_mm2"] == 0, "a fill turns lead into glass, never glass into lead"
    assert numbers["patch_left_as_lead_mm2"] == pytest.approx(720.0 - sum(shares.values()), abs=0.5)
    assert "100.00% are the piece nearest them in the map before" in answer.text
    assert numbers["lead_widest_after_mm"] < 1.8, "lines, where the three meet"
    after = {p["id"]: p["outline"] for p in saved(bench.work, "band1.json")["pieces"]}
    # Straight from each end at the panel's edge to where the three meet, and no point between.
    assert sorted((x, y) for x, y in after[1] if y < 25) == [
        pytest.approx((0, 20.6), abs=0.001),
        pytest.approx((30, 20.6 - dip), abs=0.001),
        pytest.approx((60, 20.6), abs=0.001),
    ]
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


def built(bench: Any, name: str, out: str) -> Answer:
    return bench("pieces", "build", name, *BUILD, "--build-out", out)  # type: ignore[no-any-return]


def short_sides(outline: list[list[float]], under: float = 0.3) -> int:
    return sum(
        math.dist(a, b) < under for a, b in zip(outline, outline[1:] + outline[:1], strict=True)
    )


@pytest.mark.parametrize(("picture", "scraps"), [("meet.png", False), ("ragged.png", True)])
def test_a_patch_among_four_traced_pieces_is_filled_and_the_map_still_builds(
    bench: Any, picture: str, scraps: bool
) -> None:
    """Two curved lines cross, and a dark patch lies where they meet: at the default darkness
    it's a patch of lead among four pieces. The map builds with the patch as lead. Filled, it
    builds too, and `pieces check` passes it: each share's edge is its border's side, not the
    steps the border was found from, and with hairs round the patch's rim, no scrap of lead
    is left in a piece as a line that ends there."""
    stem = picture.removesuffix(".png")
    before = traced_wide(bench, picture, f"{stem}0.json")
    assert flags_of(before) == {("wide-lead", None)}, before.text
    assert built(bench, f"{stem}0.json", f"{stem}-before").verdict == "OK"
    at = "{},{}".format(*before.numbers["lead_widest_at"])
    answer = bench("pieces", "fill", f"{stem}0.json", "--at", at, "--to", f"{stem}1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert not reports.unlike("pieces", numbers)
    was = {p["id"]: p for p in saved(bench.work, f"{stem}0.json")["pieces"]}
    now = {p["id"]: p for p in saved(bench.work, f"{stem}1.json")["pieces"]}
    assert sorted(row["piece"] for row in numbers["outlines"]) == sorted(was), "all four"
    for row in numbers["outlines"]:
        number = row["piece"]
        assert row["builds"] is True, answer.text
        assert row["points_before"] == len(was[number]["outline"])
        assert row["points_after"] == len(now[number]["outline"])
        assert row["points_after"] <= MOST_POINTS * row["points_before"], row
        assert f"piece {number}'s outline: {row['points_before']} point(s) before" in answer.text
        # A step where a border meets a line that leaves the patch, and no stairs.
        assert short_sides(now[number]["outline"]) <= 6, now[number]["outline"]
        assert not now[number].get("holes"), "no lead shut in a piece"
    assert numbers["dead_ends_at_patch_before"] == numbers["dead_ends_at_patch_after"] == 0
    assert (numbers["patch_scraps_count"] > 0) is scraps, answer.text
    assert numbers["glass_taken_mm2"] == 0
    assert ("too short to be a line, are glass too" in answer.text) is scraps
    again = bench("pieces", "check", f"{stem}1.json")
    assert again.verdict == "OK" and flags_of(again) == set(), again.text
    after = built(bench, f"{stem}1.json", f"{stem}-after")
    assert after.verdict == "OK" and after.code == 0, after.text
    assert after.numbers["panes_count"] == 4


def test_a_share_that_wont_build_is_a_fail_that_names_the_piece(bench: Any) -> None:
    """Piece 1 has a stalk 0.7 mm wide down between pieces 2 and 3 into a band of lead. The
    band round the stalk's end is nearer piece 1 than any other, so its share joins the rest
    of it by the stalk alone: two panes, where a build makes one. The map builds before the
    fill. The fill says FAIL and names the piece, `pieces check` flags it on the map the fill
    leaves, and `pieces build` refuses that map in the same words."""
    assert built(bench, "stalk.json", "stalk-before").verdict == "OK"
    answer = bench("pieces", "fill", "stalk.json", "--at", "30,6", "--to", "stalk1.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    why = "has no pane once 0.25 mm of play is taken all round: a narrow place near (10."
    assert f"piece 1 doesn't build as the fix leaves it: it {why}" in answer.record["why"]
    rows = {row["piece"]: row for row in answer.numbers["outlines"]}
    assert (rows[1]["builds"], rows[1]["built_before"]) == (False, True)
    assert rows[2]["builds"] is rows[3]["builds"] is True
    assert {(f["flag"], f["piece"]) for f in answer.numbers["flagged"]} >= {("no-pane", 1)}
    checked = bench("pieces", "check", "stalk1.json")
    assert checked.verdict == "FAIL" and ("no-pane", 1) in flags_of(checked), checked.text
    assert f"no-pane: piece 1 {why}" in checked.text, "in the build's words, with where"
    assert "`pieces split MAP 1 --line` across it" in checked.text
    assert "by its pixels, 12.0 a mm, it has 2 pane(s)" in checked.text
    refused = built(bench, "stalk1.json", "stalk-after")
    assert refused.verdict == "FAIL" and "piece 1, round (10." in refused.text, refused.text
    assert why in refused.text, "the build's words are the check's and the fill's"
    # As a step in a file of fixes it isn't made, and no map is written.
    (bench.work / "stalk-steps.json").write_text(json.dumps([{"fix": "fill", "at": [30, 6]}]))
    stepped = bench("pieces", "fix", "stalk.json", "stalk-steps.json", "--to", "stalk2.json")
    assert stepped.verdict == "NO ANSWER" and "piece 1 doesn't build" in stepped.record["why"]
    assert not (bench.work / "stalk2.json").exists()


def test_a_retraced_box_and_a_moved_line_leave_maps_that_build(bench: Any) -> None:
    """The patch in `meet.png` lies under the lines, so a box round it traced at `--dark 20`
    reads the four lines through it and no patch. Each piece keeps what it has outside the
    box and takes what's found inside, joined across the box's edge: with no step in its
    outline there, where the two sides of a line don't quite agree. Then the line between
    the two pieces on the right is bent 3 mm down. Each map builds."""
    traced_wide(bench, "meet.png", "box0.json")
    answer = bench(
        "pieces", "retrace", "box0.json", "--box", MEET_BOX, "--dark", 20, "--to", "box1.json"
    )
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    assert not reports.unlike("pieces", answer.numbers)
    assert answer.numbers["lines_across_box_edge"] == 4
    assert answer.numbers["lead_widest_after_mm"] < 2.5, "lines, and no patch"
    assert len(answer.numbers["outlines"]) == 4
    assert all(row["builds"] for row in answer.numbers["outlines"]), answer.text
    box = answer.numbers["box_mm"]
    for piece in saved(bench.work, "box1.json")["pieces"]:
        ring = piece["outline"]
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True):
            on_one_side = any(
                abs(a[axis] - side) < 1e-3 and abs(b[axis] - side) < 1e-3
                for axis, side in ((0, box[0]), (1, box[1]), (0, box[2]), (1, box[3]))
            )
            assert not on_one_side, f"piece {piece['id']} runs along the box's edge at {a}"
    first = built(bench, "box1.json", "box-built")
    assert first.verdict == "OK" and first.numbers["panes_count"] == 4, first.text
    gold, green = piece_at(bench.work, "box1.json", (80, 50)), piece_at(
        bench.work, "box1.json", (80, 15)
    )
    path = "48.5,33.4,60,28.5,70,27,80,29.5,90,34,100,38.8"
    moved = bench(
        "pieces", "move-line", "box1.json", gold, green, "--line", path, "--to", "box2.json"
    )
    assert moved.verdict == "OK" and moved.code == 0, moved.text
    assert not reports.unlike("pieces", moved.numbers)
    assert [(row["piece"], row["builds"]) for row in moved.numbers["outlines"]] == [
        (gold, True), (green, True)
    ]  # fmt: skip
    assert flags_of(bench("pieces", "check", "box2.json")) == set()
    second = built(bench, "box2.json", "line-built")
    assert second.verdict == "OK" and second.numbers["panes_count"] == 4, second.text


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
    # The verdict says so, and isn't OK: a rule that wasn't read hasn't passed.
    blind = bench("pieces", "check", "c5.json")
    assert blind.verdict == "PARTIAL" and blind.code == 1, blind.text
    assert flags_of(blind) == set() and blind.numbers["colour_unread"] == [white]
    why = blind.record["why"]
    assert f"the colour run of 1 piece(s) a fix has changed, {white}, wasn't read" in why
    assert "gone.png, isn't in the working directory: give --image FILE" in why
    # With another rule broken it's FAIL, and the verdict still names what wasn't read.
    both = bench("pieces", "check", "c5.json", "--min-area", 5000)
    assert both.verdict == "FAIL" and f", {white}, wasn't read" in both.record["why"]
    # A fix on such a map says it in its own verdict.
    gold = piece_at(bench.work, "c5.json", GOLD)
    painted = bench("pieces", "paint", "c5.json", gold, "red", "--to", "c6.json")
    assert painted.verdict == "OK" and painted.numbers["colour_unread"] == []
    green = piece_at(bench.work, "c5.json", GREEN)
    joined = bench("pieces", "merge", "c5.json", green, gold, "--to", "c7.json")
    assert joined.numbers["colour_unread"] == [gold]
    said = f"Not read: the colour run of 1 piece(s) a fix has changed, {gold}"
    assert joined.verdict == "OK" and said in joined.record["why"]
    told = bench("pieces", "check", "c5.json", "--image", "clean.png")
    assert flags_of(told) == {("gradient", white)} and told.numbers["colour_unread"] == []


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
    # 60 by 5.4 mm, and two triangles 30 by 0.015 down to where the three lines meet.
    took = answer.numbers["steps"][0]["patch_shares"][0]["area_mm2"]
    assert took == pytest.approx(324 + 30 * 0.6**2 / 24, abs=0.02)
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
