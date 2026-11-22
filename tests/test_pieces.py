"""The `pieces` commands on pictures and maps drawn in code, in the bench's image with no network
(mqucifer/print-shop#152, mqucifer/print-shop#153). Needs `scripts/bench.py build`.

`bench_cases/planted_pieces.py` draws each: a clean panel, one with one of each rule break, a
curved scene, and two maps as rules would write them. Right answers are arithmetic.
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
from print_shop.rounds import downward, reports, standing
from print_shop.rounds.box import Box
from tests.test_bench import Answer

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"
PALETTE = "blue,red,gold,green,white,purple"
#: The broken panel's cells, by a point inside each: 120 x 80 mm, six cells of 40 x 40.
DEAD_END, SLIVER, BESIDE_SLIVER, GRADIENT = (10, 60), (41.7, 60), (60, 60), (100, 60)
NECK, NOTCH, SMALL, ROUND_SMALL = (8, 20), (50, 8), (100, 20), (110, 8)


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("pieces")
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


@pytest.fixture(scope="module")
def broken(bench: Any) -> Answer:
    answer = bench(
        "pieces", "trace", "broken.png", "--width", 120, "--palette", PALETTE, "--to", "b0.json"
    )
    assert answer.verdict == "OK", answer.text
    return answer  # type: ignore[no-any-return]


def piece_at(work: Path, name: str, at: tuple[float, float]) -> int:
    """The piece of a map that holds a point, by its outline: ray crossings, no library."""
    x, y = at
    for piece in json.loads((work / name).read_text())["pieces"]:
        ring = piece["outline"]
        inside = False
        for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True):
            if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
        if inside:
            return int(piece["id"])
    raise AssertionError(f"no piece of {name} holds {at}")


def flags_of(answer: Answer) -> set[tuple[str, int | None]]:
    return {(f["flag"], f["piece"]) for f in answer.numbers["flagged"]}


# The map


def test_a_map_from_rules_reads_clean_and_its_share_is_arithmetic(bench: Any) -> None:
    answer = bench("pieces", "check", "rules.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    # 60 x 40 less a cross of lead 1.2 wide: 60 * 1.2 + 40 * 1.2 - 1.44 of 2400.
    assert answer.numbers["glass_share_pct"] == pytest.approx(100 - 100 * 118.56 / 2400, abs=1e-6)
    assert answer.numbers["glass_share_by_pixels_pct"] == pytest.approx(95.06, abs=0.3)
    # Where the two lines cross, the largest circle is the square's diagonal: 1.2 * sqrt 2.
    assert answer.numbers["lead_widest_mm"] == pytest.approx(1.2 * 2**0.5, abs=0.02)
    assert answer.numbers["source_glass_share_pct"] == 95.06
    assert answer.numbers["flags"] == dict.fromkeys(answer.numbers["flags"], 0)
    assert len(answer.record["second_check"]) == 4
    assert (bench.work / answer.numbers["picture"]).is_file()
    assert not reports.unlike("pieces", answer.numbers)


def test_a_line_that_ends_in_glass_and_a_line_joined_to_no_other_are_dead_ends(bench: Any) -> None:
    answer = bench("pieces", "check", "slit.json")
    assert answer.verdict == "FAIL" and answer.code == 1
    assert flags_of(answer) == {("dead-end", 1), ("dead-end", 4)}
    slit, lone = sorted(answer.numbers["dead_ends"], key=lambda d: d["piece"])
    assert (slit["kind"], lone["kind"]) == ("slit", "island")
    assert slit["length_mm"] == pytest.approx(8.0, abs=0.01)
    assert slit["width_mm"] == pytest.approx(1.2, abs=0.01)
    assert slit["at"][1] == pytest.approx(11.4, abs=0.01), "its end in the glass"
    assert lone["length_mm"] == pytest.approx(10.0, abs=0.01)
    assert "2 of 2 dead end(s) closed in by their own piece's glass" in answer.text
    assert "pieces extend MAP --at X,Y" in answer.text and "pieces drop-line" in answer.text


def test_a_neck_is_the_nearest_facing_points_and_rays_elsewhere_settle_nothing(
    bench: Any, broken: Answer
) -> None:
    """Two notches whose ends lie 0.94 mm apart on a slant: the narrowest place, by the nearest
    points facing each other. No ray square to a side crosses it, and their narrowest is 10 mm
    off, at another place. That was `DISAGREE`; it's a neck, and the rays are a note."""
    answer = bench("pieces", "check", "notched.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert ("neck", 1) in flags_of(answer)
    (row,) = answer.numbers["pieces"]
    assert row["narrowest_mm"] == pytest.approx((0.5**2 + 0.8**2) ** 0.5, abs=0.01)
    assert row["narrowest_by_rays_mm"] is None, "no ray runs from one of its sides to the other"
    assert "Rays square to its edges read no width across that place" in answer.text
    assert "so they neither confirm it nor differ" in answer.text
    # Where rays do cross the place, they're its second check, and agree: the necked cell.
    necked = next(r for r in broken.numbers["pieces"] if "neck" in r["flags"])
    assert necked["narrowest_by_rays_mm"] == pytest.approx(necked["narrowest_mm"], abs=0.1)


def test_two_methods_both_under_the_limit_at_different_figures_are_a_neck(bench: Any) -> None:
    """0.854 mm by the nearest points and 1.1 mm by rays, at one place, both under 1.2: they
    agree it's a neck, so it's `FAIL` with `neck` at the nearest points' figure, and the rays'
    figure is said with the gap. It was `DISAGREE`."""
    answer = bench("pieces", "check", "stepped.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert ("neck", 1) in flags_of(answer)
    (row,) = answer.numbers["pieces"]
    assert row["narrowest_mm"] == pytest.approx((0.3**2 + 0.8**2) ** 0.5, abs=0.01)
    assert row["narrowest_by_rays_mm"] == pytest.approx(1.1, abs=0.01)
    assert row["narrowest_by_pixels_mm"] == pytest.approx(row["narrowest_mm"], abs=0.06)
    noted = [n for n in answer.record["notes"] if "Rays square to its edges" in n]
    assert "read 1.10 mm there, 0.25 mm more than the nearest points" in noted[0]
    # At 1.0 the rays' 1.1 mm is over the limit and the nearest points' 0.854 under it. The
    # rays run from the notch's flat end to the step, not between the two nearest points: they
    # measure something else, so that's a note (mqucifer/print-shop#161). It was `DISAGREE`.
    split = bench("pieces", "check", "stepped.json", "--min-neck", 1.0)
    assert split.verdict == "FAIL" and split.code == 1, split.text
    assert ("neck", 1) in flags_of(split)


def test_a_slanted_neck_reads_the_same_by_both_methods_and_rays_are_a_note(bench: Any) -> None:
    """mqucifer/print-shop#161: 1.022 mm by the nearest points facing each other, and 1.828 mm
    by rays square to its edges at the same place, which run the slant's long way. The second
    check is now twice the pixels' distance to the edge at the narrowest place, which measures
    across the same line. It was `DISAGREE`."""
    answer = bench("pieces", "check", "slanted.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert ("neck", 1) in flags_of(answer)
    (row,) = answer.numbers["pieces"]
    width = (0.3**2 + 0.977**2) ** 0.5
    assert row["narrowest_mm"] == pytest.approx(width, abs=0.01)
    assert row["narrowest_by_pixels_mm"] == pytest.approx(width, abs=row["narrowest_px_mm"])
    assert row["narrowest_px_mm"] <= 0.06, "its own resolution, finer than the map's pixels"
    assert row["narrowest_by_rays_mm"] == pytest.approx(1.828, abs=0.01)
    noted = [n for n in answer.record["notes"] if "Rays square to its edges" in n]
    assert noted and "1.83 mm" in noted[0] and "measure something else" in noted[0]


def test_a_neck_the_pixels_dont_have_on_the_other_side_of_the_limit_is_still_disagree(
    bench: Any,
) -> None:
    """The control: two cuts two microns wide, their ends 1 mm apart. By the
    outline that's a neck under 1.2 mm; no pixel shows either cut, so twice the pixels'
    distance to the edge there is the piece's own width, far over the limit and far past a
    pixel. The same place, opposite sides of the limit, each beyond the other's resolution."""
    answer = bench("pieces", "check", "hairs.json")
    assert answer.verdict == "DISAGREE" and answer.code == 2, answer.text
    assert "by the nearest points facing each other, under --min-neck 1.2" in answer.record["why"]
    assert "by its pixels' distance to the edge" in answer.record["why"]
    # With the limit under both, they agree there's no neck to flag, and the gap is a note.
    loose = bench("pieces", "check", "hairs.json", "--min-neck", 0.5)
    assert loose.verdict != "DISAGREE", loose.text


def test_a_dead_end_the_pixels_cant_show_is_a_note_not_a_disagree(bench: Any) -> None:
    """mqucifer/print-shop#161, from a trace at a low `--dark`: a line inside a piece, 8 mm
    long, with another piece 1.4 mm from all of it. The pixels look 3 mm round a line for one
    piece's glass alone, so they can't show this one: it's flagged by its outline, and the
    pixels' reading is a note. It was `DISAGREE`, "isn't lone lead by the pixels"."""
    answer = bench("pieces", "check", "thin_lines.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert flags_of(answer) == {("dead-end", 1), ("dead-end", 4)}
    (noted,) = [n for n in answer.record["notes"] if "isn't lone lead by the pixels" in n]
    assert "piece 4's dead end" in noted and "so they can't show it" in noted
    assert "Only its outline reads it" in noted


def test_a_dead_end_is_matched_to_the_pixels_by_where_it_lies(bench: Any) -> None:
    """mqucifer/print-shop#161, from a trace at a low `--dark`: a dead end 8 mm long and 1.2 mm
    wide, wide enough for the pixels and with nothing near it, whose tip all but meets a line
    40 mm long inside the same piece. The pixels show the two as one stretch of lead, whose
    middle is 24 mm from the dead end, and it was matched by that middle: `DISAGREE`, "isn't
    lone lead by the pixels". It's matched now by the lead pixels in the dead end's own shape."""
    answer = bench("pieces", "check", "joined.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert [f["flag"] for f in answer.numbers["flagged"]] == ["dead-end"] * 2
    assert {end["kind"] for end in answer.numbers["dead_ends"]} == {"slit", "island"}
    assert not [n for n in answer.record["notes"] if "lone lead" in n], "read, not excused"


def test_lead_under_a_nozzles_width_is_flagged_before_anything_is_built(bench: Any) -> None:
    """mqucifer/print-shop#161: a frame built from a traced scene failed `thin` at ten places,
    the thinnest 0.135 mm, and the map had 17 pairs of pieces under 0.4 mm apart: lead no
    nozzle prints. `pieces check` reads it on the map, each place with its two pieces, how
    wide and where, and its fix. Here two pieces are 0.2 mm apart. A third is 0.25 mm from the
    panel's edge, which isn't flagged: the frame's border stands outside it."""
    answer = bench("pieces", "check", "close.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert flags_of(answer) == {("thin-lead", 1)}
    (between,) = answer.numbers["flagged"]
    assert between["figure"] == pytest.approx(0.2, abs=1e-6) and between["limit"] == 0.4
    assert between["at"][0] == pytest.approx(30.5, abs=0.01) and 7 <= between["at"][1] <= 13
    assert "the lead between pieces 1 and 2 is 0.20 mm" in answer.text
    assert "`pieces widen MAP 1 2` moves each piece's edge back" in answer.text
    assert "`pieces merge MAP 1 2`" not in answer.text, "blue and gold: the line stays"
    assert answer.numbers["limits"]["min_lead_mm"] == 0.4
    assert "1 place(s) where the lead is under --min-lead 0.4 mm" in answer.text
    # A limit under it reads none; the merge takes the line out, and its flag with it.
    assert bench("pieces", "check", "close.json", "--min-lead", 0.15).verdict == "OK"
    merged = bench("pieces", "merge", "close.json", 1, 2, "--to", "close-1.json")
    assert merged.verdict == "OK", merged.text
    assert bench("pieces", "check", "close-1.json").verdict == "OK"


def test_a_line_under_a_nozzles_width_is_widened_where_its_thin_and_keeps_its_path(
    bench: Any,
) -> None:
    """mqucifer/print-shop#161: 16 of a traced scene's 17 thin lines lay between two pieces of
    one colour, a sun's rays and a river's inner lines, which a merge would take out. `pieces
    widen` keeps the line: each piece's edge moves back by half of what's short, only where
    it's short. Here a curved line 1.2 mm wide is 0.2 mm along 10 mm."""
    before = bench("pieces", "check", "near.json")
    assert flags_of(before) == {("thin-lead", 1)}, before.text
    # Both blue, so a merge is named too, after the widening, for a line that isn't meant.
    said = before.text.index("`pieces widen MAP 1 2`"), before.text.index("`pieces merge MAP 1 2`")
    assert said[0] < said[1] and "if the picture doesn't mean the line" in before.text
    answer = bench("pieces", "widen", "near.json", 1, 2, "--to", "near-1.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    # 0.2 mm up and down, a little less square to the line where it slants.
    assert numbers["widened_from_mm"] == pytest.approx(0.2, abs=0.01)
    assert 0.4 <= numbers["widened_to_mm"] <= 0.41
    assert 10.0 <= numbers["widened_along_mm"] <= 13.0, "the 10 mm, and its two tapers' ends"
    # 0.1 mm off each piece along 10 mm, and the tapers: about 2 mm² and a little.
    assert numbers["glass_added_mm2"] == 0 and 2.0 <= numbers["glass_taken_mm2"] <= 3.2
    assert numbers["glass_taken_by_pixels_mm2"] == pytest.approx(numbers["glass_taken_mm2"], abs=1)
    assert (numbers["pieces_before"], numbers["pieces_after"]) == (2, 2)
    assert "The line keeps its path" in answer.record["why"]
    # Only where it was thin: outside x 23 to 37 both outlines are as they were.
    was, now = (
        json.loads((bench.work / name).read_text())["pieces"]
        for name in ("near.json", "near-1.json")
    )
    for old, new in zip(was, now, strict=True):
        given = {(round(x, 3), round(y, 3)) for x, y in old["outline"]}
        outside = [p for p in new["outline"] if not 23 <= p[0] <= 37]
        # No point is moved or added there: a few on a straight run of the curve are dropped.
        assert all((round(x, 3), round(y, 3)) in given for x, y in outside), old["id"]
        assert len(outside) > 150
    # The line's middle is where it was: at x 30 the two edges are 0.2 mm either side of it.
    edges = [
        min((abs(x - 30), y) for x, y in piece["outline"] if abs(x - 30) < 0.3)[1] for piece in now
    ]
    middle = 20 + 2 * math.sin(30 / 8)
    assert edges[0] == pytest.approx(middle - 0.2, abs=0.01)
    assert edges[1] == pytest.approx(middle + 0.2, abs=0.01)
    again = bench("pieces", "check", "near-1.json")
    assert again.verdict == "OK" and not flags_of(again), again.text
    assert json.loads((bench.work / "near-1.json").read_text())["history"] == [
        {"fix": "widen", "a": 1, "b": 2, "lead_mm": 0.4}
    ]
    # Nothing to widen, and a width of its own.
    wide = bench("pieces", "widen", "near-1.json", 1, 2)
    assert wide.verdict == "NO ANSWER" and "there's nothing to widen" in wide.record["why"]
    more = bench("pieces", "widen", "near.json", 1, 2, "--lead", 0.8, "--to", "near-8.json")
    assert more.verdict == "OK" and 0.8 <= more.numbers["widened_to_mm"] <= 0.81, more.text
    # As a step of a file of fixes, it writes the same map.
    (bench.work / "widen.json").write_text(json.dumps([{"fix": "widen", "a": 1, "b": 2}]))
    step = bench("pieces", "fix", "near.json", "widen.json", "--to", "near-2.json")
    assert step.verdict == "OK", step.text
    assert step.numbers["steps"][0]["widened_to_mm"] == numbers["widened_to_mm"]
    assert rings(bench.work, "near-1.json") == rings(bench.work, "near-2.json")


def test_a_second_reading_is_said_only_where_a_rule_flags_the_place(bench: Any) -> None:
    """A spur under the neck limit and a notch shallower than its mouth, each read differently
    by its pixels, and neither flagged: the reply has no note of either. On a traced scene
    each check printed four to six such notes."""
    # The lead beside the spur is wide, which isn't what this reads.
    answer = bench("pieces", "check", "quiet.json", "--max-lead", 10)
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    (row,) = answer.numbers["pieces"]
    assert row["spurs"] == 1 and not row["neck"], "the 0.8 mm spur is looked past"
    assert row["narrowest_by_pixels_mm"] is None, "with no neck there's no rule to settle"
    gap = row["notch_depth_mm"] - row["notch_depth_by_pixels_mm"]
    assert gap > 0.2 and row["notch_depth_mm"] / row["notch_mouth_mm"] < 1.0
    assert answer.record["notes"] == []


def test_a_piece_on_the_panels_edge_is_inside_and_one_past_it_is_said_with_how_far(
    bench: Any,
) -> None:
    """Two pieces on the panel's edge merge, and what they make ends at the edge. A piece a
    hundredth of a millimetre past it is on it; one further is refused, saying which piece, how
    far and where."""
    answer = bench("pieces", "merge", "edge.json", 1, 2, "--to", "edge-1.json")
    assert answer.verdict == "OK", answer.text
    (whole,) = json.loads((bench.work / "edge-1.json").read_text())["pieces"]
    assert max(x for x, _ in whole["outline"]) == 60.0
    assert sorted(whole["outline"]) == [[0.0, 0.0], [0.0, 40.0], [60.0, 0.0], [60.0, 40.0]]
    data = json.loads((bench.work / "edge.json").read_text())
    for past, name in ((0.008, "on.json"), (0.5, "past.json")):
        data["pieces"][0]["outline"][1:3] = [[60 + past, 26], [60 + past, 40]]
        (bench.work / name).write_text(json.dumps(data))
    assert bench("pieces", "check", "on.json").verdict in ("OK", "FAIL")
    refused = bench("pieces", "check", "past.json")
    assert refused.verdict == "NO ANSWER"
    assert "piece 1 by 0.500 mm at (60.5, " in refused.record["why"]
    assert "more than the 0.01 mm a piece on the edge may" in refused.record["why"]


def test_a_notch_a_pixel_cant_show_the_end_of_reads_with_a_note(bench: Any) -> None:
    """mqucifer/print-shop#161: a notch 2.77 mm deep by its outline, 1.065 times its mouth,
    whose last millimetre is a spike thinner than a pixel. Its pixels read it about 2.5 mm
    deep, under 1 time its mouth, with the limit between. A pixel can't show the spike, so the
    two are closer than the pixels' resolution there: the outline's figure flags it, and the
    pixels' is a note. It was `DISAGREE`."""
    answer = bench("pieces", "check", "spiked.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert flags_of(answer) == {("notch", 1)}
    (row,) = answer.numbers["pieces"]
    assert row["notch_depth_mm"] == pytest.approx(2.77, abs=0.005)
    gap = row["notch_depth_mm"] - row["notch_depth_by_pixels_mm"]
    assert 0.2 < gap <= row["notch_px_mm"], "past the old 2.5 pixels, within what a pixel shows"
    assert row["notch_depth_by_pixels_mm"] / row["notch_mouth_mm"] < 1.0, "the limit is between"
    noted = [n for n in answer.record["notes"] if "notch" in n]
    assert noted and "closer than" in noted[0], answer.record["notes"]


def test_lines_joined_to_no_other_are_matched_by_place_not_by_count(bench: Any) -> None:
    """Three lines inside one piece: one too thin for a pixel to show, and two a hair apart
    that the pixels show as one body. Three by the outlines and one by the pixels was
    `DISAGREE`; each is now looked for where it lies."""
    answer = bench("pieces", "check", "lone.json")
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    assert [f["flag"] for f in answer.numbers["flagged"]] == ["dead-end"] * 3
    assert {end["kind"] for end in answer.numbers["dead_ends"]} == {"island"}


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda m: m.pop("piece_map"), "isn't a piece map"),
        (lambda m: m.update(lead_mm=0), "`lead_mm`"),
        (lambda m: m["pieces"][1].update(id=1), "two pieces have the id 1"),
        (lambda m: m["pieces"][0].update(outline=[[0, 0], [45, 0], [45, 19.4], [0, 19.4]]),
         "pieces 1 and 2 overlap"),
        (lambda m: m["pieces"][0].update(outline=[[-5, 0], [29.4, 0], [29.4, 19.4]]),
         "past the panel's edge"),
        (lambda m: m["pieces"][0].update(outline=[[0, 0], [29, 19], [29, 0], [0, 19]]),
         "isn't a closed piece"),
    ],
)  # fmt: skip
def test_a_map_that_isnt_one_is_said(bench: Any, change: Any, says: str) -> None:
    data = json.loads((bench.work / "rules.json").read_text())
    change(data)
    (bench.work / "wrong.json").write_text(json.dumps(data))
    answer = bench("pieces", "check", "wrong.json")
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.record["why"]


# A picture's pieces


def test_a_clean_picture_traces_to_its_five_cells_with_no_rule_break(bench: Any) -> None:
    answer = bench("pieces", "trace", "clean.png", "--width", 120)
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert numbers["pieces_count"] == 5 and not numbers["flagged"]
    assert numbers["frame_px"] == {"top": 10, "bottom": 9, "left": 10, "right": 9}
    assert numbers["panel_px"] == [601, 401]
    assert numbers["lead_mm"] == pytest.approx(1.4, abs=0.05), "7 pixels at 5 a mm"
    assert numbers["laid_over"]["agree_pct"] > 99.9
    assert numbers["laid_over"]["patches"] == numbers["laid_over"]["pieces"] == 5
    assert {c: v["pieces"] for c, v in numbers["colours"].items()} == dict.fromkeys(
        ("red", "white", "blue", "green", "yellow"), 1
    )
    saved = json.loads((bench.work / "clean.pieces.json").read_text())
    assert saved["piece_map"] == 1 and saved["source"]["by"] == "pieces trace"
    # Each cell is a rectangle, its corners the pixels' own: four points, on the panel's edge.
    first = next(p for p in saved["pieces"] if p["id"] == 1)
    assert len(first["outline"]) == 4
    assert [0.0, 80.0666] in first["outline"], "the panel's top-left corner"
    assert (bench.work / numbers["picture"]).is_file()
    assert not reports.unlike("pieces", numbers)


def test_each_rule_break_in_a_picture_is_read_on_its_own_piece(bench: Any, broken: Answer) -> None:
    def at(point: tuple[float, float]) -> int:
        return piece_at(bench.work, "b0.json", point)

    assert flags_of(broken) == {
        ("dead-end", at(DEAD_END)),
        ("sliver", at(SLIVER)),
        ("gradient", at(GRADIENT)),
        ("neck", at(NECK)),
        ("notch", at(NECK)),
        ("notch", at(NOTCH)),
        ("small", at(SMALL)),
        ("surrounds", at(ROUND_SMALL)),
        ("wide-lead", None),
    }
    rows = {row["id"]: row for row in broken.numbers["pieces"]}
    assert rows[at(SLIVER)]["widest_mm"] == pytest.approx(2.0, abs=0.25)
    assert rows[at(NECK)]["narrowest_mm"] == pytest.approx(1.0, abs=0.25)
    assert rows[at(GRADIENT)]["colour_run"] > 100 and rows[at(NOTCH)]["colour_run"] < 5
    assert rows[at(SMALL)]["area_mm2"] == pytest.approx(21.0, abs=2.0)
    (end,) = broken.numbers["dead_ends"]
    assert end["length_mm"] == pytest.approx(22.0 - 0.7, abs=0.6), "22 mm, less half the line"
    # A disc of lead 32 pixels across where four lines meet.
    assert broken.numbers["lead_widest_mm"] == pytest.approx(6.4, abs=0.3)
    assert rows[at(BESIDE_SLIVER)]["flags"] == [], "its neighbour breaks nothing"
    saved = json.loads((bench.work / "b0.json").read_text())
    assert len(saved["flags"]) == 9 and len(saved["colours"]) == 5


def test_outlines_that_cross_over_a_thin_line_are_parted_and_stay_smooth(bench: Any) -> None:
    """mqucifer/print-shop#161: 20 curved pieces whose lines are 4 pixels wide, but one that's
    a single pixel. The smoothed outlines either side of it cross. Each kept its pixels' own
    edges, a step at every pixel: two pieces of 262 and 230 points, 724 in the map, and a
    frame of 1,341 faces. They're parted now, each less what they share and half a pixel round
    it, and no outline is pixel steps."""
    answer = bench("pieces", "trace", "thin_line.png", "--width", 100, "--to", "thin.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["pieces_count"] == 20
    assert "2 piece(s) were parted from a neighbour" in answer.text
    saved = json.loads((bench.work / "thin.json").read_text())
    px = 1.0 / saved["source"]["px_per_mm"]
    counts = [len(p["outline"]) for p in saved["pieces"]]
    assert max(counts) < 40 and sum(counts) < 320, counts
    for piece in saved["pieces"]:
        ring = piece["outline"]
        sides = sorted(
            ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for a, b in zip(ring, ring[1:] + ring[:1], strict=True)
        )
        assert sides[len(sides) // 2] > 3 * px, f"piece {piece['id']} is pixel steps"
    # A pixel's line still lies between the two, and the glass is the trace's to 0.05 points.
    assert answer.numbers["glass_share_pct"] == pytest.approx(93.57, abs=0.05)
    built = bench(
        "pieces", "build", "thin.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "thin",
    )  # fmt: skip
    assert built.verdict == "OK", built.text
    assert built.numbers["frame_faces"] < 1000, "it was 1,341 with two outlines of pixel steps"


def test_a_piece_with_dark_specks_in_it_is_traced_as_smooth_as_any(bench: Any) -> None:
    """mqucifer/print-shop#161: the same 20 curved pieces, every line 4 pixels wide, with five
    dark specks two pixels across inside two of them. A speck's own ring is too short to read
    a corner on, and that left the whole piece as its pixels' edges: 314 and 282 points, 806
    in the map, and a frame of 1,686 faces. Each ring is read on its own now."""
    answer = bench("pieces", "trace", "speckled.png", "--width", 100, "--to", "speckled.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["specks_filled"] == 5 and answer.numbers["pieces_count"] == 20
    saved = json.loads((bench.work / "speckled.json").read_text())
    px = 1.0 / saved["source"]["px_per_mm"]
    counts = [len(p["outline"]) for p in saved["pieces"]]
    assert max(counts) < 40 and sum(counts) < 300, counts
    assert not any(p.get("holes") for p in saved["pieces"])
    for piece in saved["pieces"]:
        ring = piece["outline"]
        sides = sorted(
            ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
            for a, b in zip(ring, ring[1:] + ring[:1], strict=True)
        )
        assert sides[len(sides) // 2] > 3 * px, f"piece {piece['id']} is pixel steps"
    built = bench(
        "pieces", "build", "speckled.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "speckled",
    )  # fmt: skip
    assert built.verdict == "OK", built.text
    assert built.numbers["frame_faces"] < 900, "it was 1,686 with two outlines of pixel steps"
    # A lip is the lip asked for or up to --fit wider, never narrower: `capture`, at the rim
    # asked for, read 1.177 mm for 1.2 on one pane, where an opening fitted to its inset stood
    # up to --fit off between two points.
    held = bench(
        "capture", built.numbers["glass"]["frame"], "--pocket-z", 1.3, "--clearance", 0.25,
        "--pane-thickness", 1, "--full-rim", 1.2, "--least-rim", 0.6, "--min-visible", 1,
        "--panes", 20,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    assert held.numbers["least_lip_mm"] >= 0.94, "1.2 mm less the play, to capture's reading"


def test_a_picture_with_nothing_dark_or_too_small_has_no_answer(bench: Any) -> None:
    answer = bench("pieces", "trace", "clean.png", "--width", 120, "--dark", 3)
    assert answer.verdict == "NO ANSWER" and "no dark lines" in answer.record["why"]
    answer = bench("pieces", "trace", "clean.png", "--width", 120, "--palette", "mauve")
    assert answer.verdict == "NO ANSWER" and "mauve=#rrggbb" in answer.record["why"]


def test_the_panel_is_inside_the_frame_and_a_margin_outside_it_is_no_piece(bench: Any) -> None:
    """A white margin with specks in it, and a frame uneven at the top: the margin over the
    frame was traced as a piece, and the panel came out taller than the frame's inside."""
    answer = bench("pieces", "trace", "margin.png", "--width", 120, "--to", "margin.json")
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert numbers["pieces_count"] == 5 and not numbers["flagged"]
    assert numbers["panel_px"] == [601, 401], "as the same panel with no margin"
    assert numbers["panel_mm"] == [120.0, pytest.approx(80.067, abs=0.001)]
    # The frame's top is 10 pixels on its left half and 6 on its right: 6 all the way across.
    assert numbers["frame_px"] == {"top": 6, "bottom": 9, "left": 10, "right": 9}
    assert sorted(numbers["colours"]) == ["blue", "green", "red", "white", "yellow"]


def test_glass_on_a_dark_ground_is_never_cut_away_as_a_frame(bench: Any) -> None:
    """mqucifer/print-shop#240: a band on a dark ground lost 75, 74 and 73 pixels as frame,
    was drawn 1.24 times too big, and was `OK`. It has no answer, naming each side it would
    have cut and the glass there, with the scale it would have given, and writes no map."""
    answer = bench("pieces", "trace", "dark-ground.png", "--width", 100, "--to", "dark.json")
    assert answer.verdict == "NO ANSWER", answer.text
    why = answer.record["why"]
    assert why.startswith("it would have traced 323 x 151 of the picture's 400 x 300 pixels")
    assert "40.6% of it, at 0.3096 mm a pixel" in why
    cut = answer.numbers["frame_cut"]
    assert {side: c["px"] for side, c in cut.items()} == {
        "top": 75, "bottom": 74, "left": 4, "right": 73
    }  # fmt: skip
    assert cut["top"]["glass_px"] > 1000 and cut["bottom"]["glass_px"] > 1000
    assert f"top 75 px with {cut['top']['glass_px']} px of glass on it" in why
    assert f"bottom 74 px with {cut['bottom']['glass_px']} px of glass on it" in why
    assert "right 73 px" in why and "--whole" in why
    assert not (bench.work / "dark.json").exists(), "no map to work on"
    assert reports.unlike("pieces", answer.numbers) == []


def test_whole_traces_the_picture_as_its_panel_and_every_reader_after_it_does(bench: Any) -> None:
    answer = bench(
        "pieces", "trace", "dark-ground.png", "--width", 100, "--whole", "--to", "whole.json"
    )
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["panel_px"] == answer.numbers["picture_px"] == [400, 300]
    assert answer.numbers["px_per_mm"] == 4.0 and answer.numbers["panel_share_pct"] == 100.0
    assert not answer.record["why"].startswith("traced"), "the window is the whole picture"
    assert json.loads((bench.work / "whole.json").read_text())["source"]["whole_picture"] is True
    again = bench("pieces", "retrace", "whole.json", "--box", "30,25,55,55", "--to", "again.json")
    assert again.verdict == "OK", again.text


@pytest.mark.parametrize(
    ("name", "panel"),
    [("light-ground", [400, 300]), ("framed", [344, 244]), ("framed-specked", [344, 244]),
     ("framed-notched", None), ("framed-wavy", None)],
)  # fmt: skip
def test_a_real_frame_and_a_picture_with_none_trace_as_they_did(
    bench: Any, name: str, panel: list[int] | None
) -> None:
    """A frame with specks in it or its margin, one thinner where the margin comes in, and one
    whose inner edge wanders by a hair: none holds glass. The window is said first."""
    answer = bench("pieces", "trace", f"{name}.png", "--width", 100)
    assert answer.verdict == "OK", answer.text
    wide, tall = answer.numbers["panel_px"]
    assert panel is None or [wide, tall] == panel
    assert "frame_cut" not in answer.numbers
    if [wide, tall] == [400, 300]:
        assert not answer.record["why"].startswith("traced")
    else:
        assert answer.record["why"].startswith(
            f"traced {wide} x {tall} of the picture's 400 x 300 pixels, "
        )
        assert f"at {100 / wide:.4f} mm a pixel: " in answer.record["why"]


def test_specks_in_a_piece_are_filled_and_a_line_that_ends_in_glass_is_one_dead_end(
    bench: Any,
) -> None:
    answer = bench("pieces", "trace", "specks.png", "--width", 120, "--to", "specks.json")
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["specks_filled"] == 12, "two clusters of six"
    assert "specks_filled: 12 (dark specks inside one piece" in answer.text
    assert flags_of(answer) == {("dead-end", piece_at(bench.work, "specks.json", (10, 20)))}
    (end,) = answer.numbers["dead_ends"]
    assert end["kind"] == "slit" and end["length_mm"] == pytest.approx(12 - 0.7, abs=0.6)
    saved = json.loads((bench.work / "specks.json").read_text())
    assert not any(piece.get("holes") for piece in saved["pieces"])
    # A line inside a piece as long as a lead is wide is still a dead end: not every lone
    # line is a speck.
    lone = bench("pieces", "check", "slit.json")
    assert ("dead-end", 4) in flags_of(lone)


def test_a_dead_end_that_cuts_a_wide_pieces_opening_is_named_with_its_fix(bench: Any) -> None:
    """A piece 14 mm wide was said to have no pane, with its width and no cause."""
    answer = bench(
        "pieces", "build", "deep.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "nothing",
    )  # fmt: skip
    assert answer.verdict == "FAIL"
    cut, lone = answer.numbers["unbuildable"]
    assert cut.startswith("piece 1,") and "a dead end inside it, from (15.0, 19.4) to" in cut
    assert "cuts its opening in two" in cut and "though it's 14.40 mm at its widest" in cut
    assert "`pieces extend MAP --at 14.4,1`" in cut and "`pieces drop-line MAP --at 14.4,1`" in cut
    assert lone.startswith("piece 4,") and "a lead line inside it" in lone
    assert "`pieces drop-line MAP --at 45,30`" in lone
    # And the fix it names is one the bench takes.
    fixed = bench("pieces", "drop-line", "deep.json", "--at", "14.4,1", "--to", "undeep.json")
    assert fixed.verdict == "OK", fixed.text
    again = bench("pieces", "drop-line", "undeep.json", "--at", "45,30", "--to", "whole.json")
    assert again.verdict == "OK", again.text
    built = bench(
        "pieces", "build", "whole.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "whole",
    )  # fmt: skip
    assert built.verdict == "OK" and built.numbers["panes_count"] == 4, built.text


# The fixes


def test_a_merge_joins_a_sliver_to_its_neighbour_and_the_line_is_glass(
    bench: Any, broken: Answer
) -> None:
    sliver = piece_at(bench.work, "b0.json", SLIVER)
    beside = piece_at(bench.work, "b0.json", BESIDE_SLIVER)
    answer = bench("pieces", "merge", "b0.json", sliver, beside, "--to", "merged.json")
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert (numbers["pieces_before"], numbers["pieces_after"]) == (11, 10)
    assert numbers["glass_share_after_pct"] > numbers["glass_share_before_pct"]
    # The line between them: 1.4 mm wide and the cell's 39.3 mm tall.
    assert numbers["glass_added_mm2"] == pytest.approx(1.4 * 39.3, abs=1.5)
    assert numbers["glass_taken_mm2"] == pytest.approx(0.0, abs=1e-6)
    assert numbers["glass_added_by_pixels_mm2"] == pytest.approx(numbers["glass_added_mm2"], abs=2)
    assert "every other piece's outline is the same, point for point" in answer.text
    (row,) = numbers["changed"]
    assert row["id"] == beside and row["flags"] == []
    # Lines meet as lines: the two cells and the line between are one rectangle again.
    # the sliver's two outer corners stand where the cell's two were, and no point is added.
    before = json.loads((bench.work / "b0.json").read_text())
    was = next(p for p in before["pieces"] if p["id"] == beside)
    saved = json.loads((bench.work / "merged.json").read_text())
    joined = next(p for p in saved["pieces"] if p["id"] == beside)
    assert len(joined["outline"]) == len(was["outline"]), joined["outline"]
    left = min(x for x, _ in joined["outline"])
    assert left == pytest.approx(40.73, abs=0.01), "the sliver's own left side"
    assert sum(1 for x, _ in joined["outline"] if x == left) == 2, "one straight side"
    assert saved["history"] == [{"fix": "merge", "a": sliver, "b": beside}]
    again = bench("pieces", "check", "merged.json")
    assert not any(flag == "sliver" for flag, _ in flags_of(again))
    assert again.numbers["glass_share_change_pct"] > 0, "against the trace's"


def test_a_merge_of_pieces_that_dont_touch_is_refused(bench: Any, broken: Answer) -> None:
    far = piece_at(bench.work, "b0.json", GRADIENT)
    here = piece_at(bench.work, "b0.json", DEAD_END)
    answer = bench("pieces", "merge", "b0.json", here, far)
    assert answer.verdict == "NO ANSWER" and answer.code == 2
    assert f"pieces {here} and {far} don't touch" in answer.record["why"]
    assert "neighbours are" in answer.record["why"]
    answer = bench("pieces", "merge", "b0.json", here, 99)
    assert "the map has no piece 99" in answer.record["why"]


def test_a_dead_end_is_dropped_or_run_on_to_the_edge(bench: Any, broken: Answer) -> None:
    piece = piece_at(bench.work, "b0.json", DEAD_END)
    dropped = bench("pieces", "drop-line", "b0.json", "--at", "20,55", "--to", "dropped.json")
    assert dropped.verdict == "OK", dropped.text
    assert dropped.numbers["pieces_after"] == 11
    assert dropped.numbers["glass_added_mm2"] == pytest.approx(1.4 * 21.4, abs=1.5)
    assert dropped.numbers["changed"][0]["flags"] == []
    saved = json.loads((bench.work / "dropped.json").read_text())
    whole = next(p for p in saved["pieces"] if p["id"] == piece)
    assert len(whole["outline"]) == 4, "the cell is a rectangle again"
    assert not any(f == "dead-end" for f, _ in flags_of(bench("pieces", "check", "dropped.json")))

    ran = bench("pieces", "extend", "b0.json", "--at", "20,55", "--to", "ran.json")
    assert ran.verdict == "OK", ran.text
    assert ran.numbers["pieces_after"] == 12
    assert ran.numbers["ran_to"][1] == pytest.approx(80.07, abs=0.05), "the panel's top edge"
    assert "where it meets the panel's edge" in ran.record["why"]
    assert ran.numbers["glass_taken_mm2"] == pytest.approx(1.4 * 18.0, abs=2.0)
    assert {row["id"] for row in ran.numbers["changed"]} == {piece, 12}
    assert not any(f == "dead-end" for f, _ in flags_of(bench("pieces", "check", "ran.json")))

    nowhere = bench("pieces", "extend", "b0.json", "--at", "100,60")
    assert nowhere.verdict == "NO ANSWER" and "no dead-end line within" in nowhere.record["why"]
    assert f"piece {piece} at" in nowhere.record["why"], "it says where the map's are"


def test_a_line_between_two_pieces_dropped_is_their_merge(bench: Any, broken: Answer) -> None:
    small = piece_at(bench.work, "b0.json", SMALL)
    around = piece_at(bench.work, "b0.json", ROUND_SMALL)
    answer = bench("pieces", "drop-line", "b0.json", "--at", "100,23.3", "--to", "filled.json")
    assert answer.verdict == "OK", answer.text
    assert f"the smaller, {small}, joined {around}" in answer.record["why"]
    assert answer.numbers["pieces_after"] == 10
    saved = json.loads((bench.work / "filled.json").read_text())
    assert not next(p for p in saved["pieces"] if p["id"] == around).get("holes")


def test_a_split_cuts_a_piece_from_edge_to_edge_and_one_that_stops_is_refused(
    bench: Any, broken: Answer
) -> None:
    piece = piece_at(bench.work, "b0.json", NOTCH)
    answer = bench(
        "pieces", "split", "b0.json", piece, "--line", "40,15.3,80,15.3", "--to", "split.json"
    )
    assert answer.verdict == "OK", answer.text
    assert answer.numbers["pieces_after"] == 13, "the U is its base and two arms"
    assert len(answer.numbers["changed"]) == 3
    assert all("notch" not in row["flags"] for row in answer.numbers["changed"])
    assert answer.numbers["glass_taken_mm2"] > 30 and answer.numbers["glass_added_mm2"] == 0
    assert answer.numbers["glass_share_after_pct"] < answer.numbers["glass_share_before_pct"]
    short = bench("pieces", "split", "b0.json", piece, "--line", "45,5,70,5")
    assert short.verdict == "NO ANSWER"
    assert "doesn't cut" in short.record["why"] and "ends in its glass" in short.record["why"]


def test_a_piece_that_surrounds_another_is_opened_by_a_line_to_its_edge(
    bench: Any, broken: Answer
) -> None:
    around = piece_at(bench.work, "b0.json", ROUND_SMALL)
    answer = bench(
        "pieces", "split", "b0.json", around, "--line", "103.5,20,121,20", "--to", "opened.json"
    )
    assert answer.verdict == "OK", answer.text
    assert "it surrounds no piece now" in answer.record["why"]
    assert answer.numbers["pieces_after"] == 11
    assert "surrounds" not in answer.numbers["changed"][0]["flags"]
    again = bench("pieces", "check", "opened.json")
    assert not any(flag == "surrounds" for flag, _ in flags_of(again))
    # As it is, the frame would be two bodies, so nothing is built from it.
    built = bench(
        "pieces", "build", "dropped.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "nothing",
    )  # fmt: skip
    assert built.verdict in ("FAIL", "NO ANSWER")


def rings(work: Path, name: str) -> list[Any]:
    """A map's pieces with each ring begun at its lowest point: the same points in the same
    order round are the same outline, wherever the file starts it."""

    def begun(ring: list[list[float]]) -> list[list[float]]:
        k = ring.index(min(ring))
        return ring[k:] + ring[:k]

    return [
        (p["id"], p["colour"], begun(p["outline"]), sorted(begun(h) for h in p.get("holes", [])))
        for p in json.loads((work / name).read_text())["pieces"]
    ]


def five_steps(work: Path) -> list[dict[str, Any]]:
    """Five fixes to the broken panel, one of each kind and a second merge."""
    sliver, beside = piece_at(work, "b0.json", SLIVER), piece_at(work, "b0.json", BESIDE_SLIVER)
    notch, around = piece_at(work, "b0.json", NOTCH), piece_at(work, "b0.json", ROUND_SMALL)
    small = piece_at(work, "b0.json", SMALL)
    return [
        {"fix": "merge", "a": sliver, "b": beside},
        {"fix": "drop-line", "at": [20, 55]},
        {"fix": "split", "piece": notch, "line": [[40, 15.3], [80, 15.3]]},
        {"fix": "split", "piece": around, "line": [[103.5, 20], [121, 20]]},
        {"fix": "merge", "a": small, "b": around},
    ]


def test_a_file_of_five_fixes_runs_in_one_call_with_one_reply(bench: Any, broken: Answer) -> None:
    """mqucifer/print-shop#161: `pieces fix MAP STEPS` runs a file of steps in order, says each
    step's result, and the glass share and the lead's widest point before and after. The map it
    writes is the map the same five commands write one by one."""
    steps = five_steps(bench.work)
    (bench.work / "five.json").write_text(json.dumps({"steps": steps}))
    answer = bench("pieces", "fix", "b0.json", "five.json", "--to", "fixed.json")
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    numbers = answer.numbers
    assert (numbers["steps_given"], numbers["steps_made"]) == (5, 5)
    assert [s["fix"] for s in numbers["steps"]] == [s["fix"] for s in steps]
    assert all(s["made"] for s in numbers["steps"])
    assert [s["pieces_after"] for s in numbers["steps"]] == [10, 10, 12, 12, 11]
    for n in range(1, 6):
        assert f"step {n} of 5, " in answer.text
    assert (numbers["pieces_before"], numbers["pieces_after"]) == (11, 11)
    # A fix keeps the glass: three steps add it, and the two splits take a line's worth each.
    assert numbers["glass_added_mm2"] > 0 and numbers["glass_taken_mm2"] > 30
    # As the trace read it, to what writing the map to a tenth of a micron moves.
    assert numbers["glass_share_before_pct"] == pytest.approx(
        broken.numbers["glass_share_pct"], abs=1e-3
    )
    assert numbers["lead_widest_before_mm"] == pytest.approx(
        broken.numbers["lead_widest_mm"], abs=1e-3
    )
    assert "every other piece's outline is the same, point for point" in answer.text
    # One by one, the same five write the same map.
    name = "b0.json"
    for k, step in enumerate(steps):
        if step["fix"] == "merge":
            words = ["merge", name, step["a"], step["b"]]
        elif step["fix"] == "drop-line":
            words = ["drop-line", name, "--at", "{},{}".format(*step["at"])]
        else:
            line = ",".join(str(v) for point in step["line"] for v in point)
            words = ["split", name, step["piece"], "--line", line]
        one = bench("pieces", *words, "--to", f"one-{k}.json")
        assert one.verdict == "OK", one.text
        name = f"one-{k}.json"
    assert rings(bench.work, "fixed.json") == rings(bench.work, name), "point for point"
    together = json.loads((bench.work / "fixed.json").read_text())
    apart = json.loads((bench.work / name).read_text())
    assert together["history"] == apart["history"] and len(together["history"]) == 5
    # The reading after is the check's own.
    again = bench("pieces", "check", "fixed.json")
    assert numbers["glass_share_after_pct"] == pytest.approx(
        again.numbers["glass_share_pct"], abs=1e-6
    )
    assert numbers["lead_widest_after_mm"] == pytest.approx(
        again.numbers["lead_widest_mm"], abs=1e-6
    )
    left = flags_of(again)
    assert not {"sliver", "surrounds", "small"} & {flag for flag, _ in left}
    ended = piece_at(bench.work, "b0.json", DEAD_END)
    assert ("dead-end", ended) not in left and ("notch", steps[2]["piece"]) not in left
    # The reply's own reading of each changed piece is the check's: the opening line, with the
    # small piece joined across it, now ends in the piece's glass, and the reply says so.
    around = steps[4]["b"]
    changed = {row["id"]: row["flags"] for row in numbers["changed"]}
    assert changed[around] == ["dead-end"] and ("dead-end", around) in left
    assert sum(1 for flags in changed.values() if flags) == 1
    assert not reports.unlike("pieces", numbers)


def test_a_fix_file_stops_at_the_first_refused_step_and_writes_the_map_up_to_it(
    bench: Any, broken: Answer
) -> None:
    steps = five_steps(bench.work)
    far, here = piece_at(bench.work, "b0.json", GRADIENT), piece_at(bench.work, "b0.json", DEAD_END)
    steps[2] = {"fix": "merge", "a": here, "b": far}
    (bench.work / "stops.json").write_text(json.dumps(steps))
    answer = bench("pieces", "fix", "b0.json", "stops.json", "--to", "stopped.json")
    assert answer.verdict == "PARTIAL" and answer.code == 1, answer.text
    numbers = answer.numbers
    assert (numbers["steps_given"], numbers["steps_made"]) == (5, 2)
    refused = numbers["steps"][2]
    assert not refused["made"] and f"pieces {here} and {far} don't touch" in refused["said"]
    assert len(numbers["steps"]) == 3, "the steps after it weren't tried"
    assert "step 3 of 5, " in answer.record["why"] and "stopped.json" in answer.record["why"]
    saved = json.loads((bench.work / "stopped.json").read_text())
    assert [h["fix"] for h in saved["history"]] == ["merge", "drop-line"]
    assert numbers["pieces_after"] == 10
    # Refused at its first step, nothing is made and nothing is written.
    (bench.work / "first.json").write_text(json.dumps(steps[2:]))
    none = bench("pieces", "fix", "b0.json", "first.json", "--to", "none.json")
    assert none.verdict == "NO ANSWER" and none.code == 2
    assert "step 1 of 3, " in none.record["why"] and not (bench.work / "none.json").exists()


def test_a_maps_history_is_a_steps_file_that_makes_it_again(bench: Any, broken: Answer) -> None:
    """Each fix is written in the map's `history` as a step is given, so the fixes a map has had
    can be made again on the map they began from: a dead end run on, then the five."""
    steps = [{"fix": "extend", "at": [20, 55]}, *five_steps(bench.work)[2:]]
    (bench.work / "ran-on.json").write_text(json.dumps(steps))
    first = bench("pieces", "fix", "b0.json", "ran-on.json", "--to", "h1.json")
    assert first.verdict == "OK", first.text
    assert first.numbers["steps"][0]["ran_to"][1] == pytest.approx(80.07, abs=0.05)
    history = json.loads((bench.work / "h1.json").read_text())["history"]
    assert [h["fix"] for h in history] == ["extend", "split", "split", "merge"]
    (bench.work / "history.json").write_text(json.dumps(history))
    again = bench("pieces", "fix", "b0.json", "history.json", "--to", "h2.json")
    assert again.verdict == "OK", again.text
    assert rings(bench.work, "h1.json") == rings(bench.work, "h2.json")


@pytest.mark.parametrize(
    ("steps", "says"),
    [
        ('{"steps": []}', "no step"),
        ('[{"fix": "polish"}]', "`fix` is one of merge, split, extend, drop-line, widen"),
        ('[{"fix": "widen", "a": 1}]', "step 1, a widen, needs `b`"),
        ('[{"fix": "merge", "a": 1}]', "step 1, a merge, needs `b`"),
        ('[{"fix": "split", "piece": 1, "line": [[1, 2]]}]', "`line` is two points or more"),
        ("not json", "isn't JSON"),
    ],
)
def test_a_fix_file_that_cant_be_read_says_which_step_and_what_it_needs(
    bench: Any, broken: Answer, steps: str, says: str
) -> None:
    (bench.work / "odd.json").write_text(steps)
    answer = bench("pieces", "fix", "b0.json", "odd.json", "--to", "odd-out.json")
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text
    assert not (bench.work / "odd-out.json").exists()


def test_a_fix_names_its_new_map_by_the_next_number(bench: Any, broken: Answer) -> None:
    sliver = piece_at(bench.work, "b0.json", SLIVER)
    beside = piece_at(bench.work, "b0.json", BESIDE_SLIVER)
    first = bench("pieces", "merge", "b0.json", sliver, beside)
    assert first.numbers["map"] == "b0-1.json"
    small = piece_at(bench.work, "b0-1.json", SMALL)
    around = piece_at(bench.work, "b0-1.json", ROUND_SMALL)
    second = bench("pieces", "merge", "b0-1.json", small, around)
    assert second.numbers["map"] == "b0-2.json"
    before = json.loads((bench.work / "b0.json").read_text())
    assert len(before["pieces"]) == 11, "the map given is as it was"


# The build


@pytest.fixture(scope="module")
def built(bench: Any) -> Answer:
    traced = bench("pieces", "trace", "hills.png", "--width", 100, "--to", "hills.json")
    # The sun stands in the sky piece over it, which is a notch in that piece and nothing else.
    assert traced.verdict == "OK", traced.text
    assert [f["flag"] for f in traced.numbers["flagged"]] == ["notch"], traced.text
    answer = bench(
        "pieces", "build", "hills.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "hills",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    return answer  # type: ignore[no-any-return]


def test_a_build_is_a_frame_and_a_pane_file_for_each_colour(bench: Any, built: Answer) -> None:
    numbers = built.numbers
    assert numbers["panes_count"] == 10 and numbers["frame_solids"] == 1
    assert {c: v["panes"] for c, v in numbers["pane_files"].items()} == {
        "white": 3, "yellow": 1, "blue": 3, "green": 3,
    }  # fmt: skip
    # A 1 mm pane in a 3 mm frame, on 0.2 mm layers: 0.8 under it, 0.2 over it, 1 above that.
    assert (numbers["pocket_floor_z"], numbers["pane_top_z"], numbers["pause_z"]) == (0.8, 1.8, 2.0)
    assert (numbers["lower_lip_mm"], numbers["upper_lip_mm"]) == (0.8, 1.0)
    assert numbers["glass"] == {
        "frame": "hills/frame.step",
        "panes": [f"hills/pane_{c}.step" for c in ("white", "yellow", "blue", "green")],
        "pocket_z": 1.3,
        "clearance": 0.25,
        "pane_thickness": 1.0,
    }
    assert numbers["play_off_mm"] < 0.012
    assert numbers["least_lip_before_play_mm"] == 1.2
    assert "CYLINDER" in built.text.upper(), "its curves are curves"
    # The harness counts the panes by the bodies in the pane files (mqucifer/print-shop#153).
    assert standing.pane_bodies(bench.work, numbers["glass"]["panes"]) == 10
    assert standing.bodies_in(bench.work, "hills/frame.step") == 1


def test_the_existing_checks_read_a_build_as_any_frame(bench: Any, built: Answer) -> None:
    frame, panes = built.numbers["glass"]["frame"], built.numbers["glass"]["panes"]
    held = bench(
        "capture", frame, "--pocket-z", 1.3, "--clearance", 0.25, "--pane-thickness", 1,
        "--panes", 10,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    assert held.numbers["panes_read"] == 10
    # 1.2 mm less the play, 0.95: up to --fit wider where an opening is fitted, and read up to
    # 0.1 less at a corner, where `capture` reads the lip to the corner's point.
    assert 0.85 <= held.numbers["least_lip_mm"] <= 1.05
    by_files = bench(
        "capture", frame, "--pocket-z", 1.3, "--clearance", 0.25, "--pane-thickness", 1,
        "--panes", len(panes),
    )  # fmt: skip
    assert by_files.verdict == "FAIL", "four files aren't four panes"
    pause = bench("pause", frame, "--pocket-z", 1.3, "--pane-thickness", 1)
    said = "pause after layer 10, at z=2, before layer 11, at z=2.2:"
    assert pause.verdict == "OK" and pause.record["why"].startswith(said), pause.text
    assert pause.numbers["least_room_over_pane_mm"] == pytest.approx(0.2, abs=1e-6)
    assert bench("thin", frame, "--one-body").verdict == "OK"
    for pane in panes:
        assert bench("thin", pane).verdict == "OK", pane
    light = bench("light", frame, *panes)
    assert light.verdict == "OK" and light.numbers["least_open_pct"] > 50
    info = bench("info", frame)
    assert info.numbers["strip_faces"] == 0, "no curve is stored as strips"
    assert info.numbers["solids"] == 1


def test_a_built_frames_lips_pass_with_its_panes_in_place_and_fail_without(
    bench: Any, built: Answer
) -> None:
    """The harness's reading of a paused frame, on the bench's own meshes of a build: its top
    lips print over the panes. Without them, the control, each lip is a ceiling over air."""
    declared = built.numbers["glass"]
    files = [declared["frame"], *declared["panes"]]
    glass = standing.read([declared], files, bench.work)
    assert isinstance(glass, list) and glass[0].count() == 10, glass
    said = dict.fromkeys(files, 'the request says "It prints flat, with no supports."')
    box, kept = Box(bench.work), bench.work / ".kept"
    frame, *panes = downward.read(box, files, {}, bench.work, kept, said, glass)
    assert frame.passed and frame.verdict == "NONE", frame.why
    assert frame.numbers["panes_in_place"] == 10
    assert frame.numbers["without_panes_mm2"] > 300, "ten lips, each 1.2 mm wide"
    assert all(pane.passed for pane in panes), [pane.why for pane in panes]
    alone, *_ = downward.read(box, files, {}, bench.work, kept, said)
    assert not alone.passed and alone.verdict == "NEEDS SUPPORT"
    assert alone.numbers["to_support_mm2"] == pytest.approx(
        frame.numbers["without_panes_mm2"], abs=0.01
    )
    assert "panes_in_place" not in alone.numbers


def test_a_built_panes_play_is_read_at_its_edge(bench: Any, built: Answer) -> None:
    """The sun: a box round its right edge, between its faces, reads the play asked for."""
    edge = bench(
        "clearance", "hills/pane_yellow.step", "hills/frame.step", "--min-gap", 0.25,
        "--max-gap", 0.25, "--box", "edge=89,45,0.9,94,53,1.7",
    )  # fmt: skip
    place = edge.numbers["pairs"][0]["places"]["edge"]
    assert place["verdict"] == "CLEAR", edge.text
    assert place["gap_mm"] == pytest.approx(0.25, abs=1e-5)


def test_a_pane_is_held_to_its_play_square_to_its_pockets_sides(bench: Any) -> None:
    """mqucifer/print-shop#161: a piece whose slanted side is a traced edge's pixel steps, 240
    sides of 0.228 mm with a square corner between each. Its pane is those sides moved in by
    the play, and round each corner that points into the pocket the pane's own corner is
    rightly further off. The middle gap of every point read 0.285 mm for a play of 0.25, and
    the build was `DISAGREE`. Held square to each side, it's the play."""
    answer = bench(
        "pieces", "build", "stairs.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.8",
        "--play", 0.25, "--build-out", "stairs",
    )  # fmt: skip
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    assert answer.numbers["play_off_mm"] == pytest.approx(0.0, abs=0.001)
    # The control: a pane that really is 0.03 mm further in than its play, along its sides.
    shutil.copy(CASES / "planted_play.py", bench.work / "planted_play.py")
    done = host.run(["planted_play.py"], bench.work, python=True)
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert said["verdict"] == "DISAGREE" and said["code"] == 2, done.stdout + done.stderr
    assert "a pane's edge is 0.030 mm off the play, over the 0.012 mm" in said["why"]
    assert "pane is 0.280 mm from its pocket's wall at" in said["why"]
    assert "square to a side, and 0.280 mm at its nearest, for a play of 0.25" in said["why"]


def test_a_wall_between_two_pockets_is_never_thinner_than_the_maps_line(bench: Any) -> None:
    """mqucifer/print-shop#161: a line widened to 0.4 mm in the map was a wall 0.21 mm thick in
    the frame, as a pocket's curves were fitted to its outline and stood up to --fit past it
    on each side. They're fitted inside it now, so `thin` passes on what `check` passed."""
    widened = bench("pieces", "widen", "near.json", 1, 2, "--to", "near-wall.json")
    assert widened.verdict == "OK", widened.text
    built = bench(
        "pieces", "build", "near-wall.json", "--thickness", 3, "--pane", 1, "--lip", 1.2,
        "--play", 0.25, "--build-out", "wall",
    )  # fmt: skip
    assert built.verdict == "OK", built.text
    assert built.numbers["pocket_past_outline_mm"] == pytest.approx(0.0, abs=0.012)
    thin = bench("thin", built.numbers["glass"]["frame"], "--one-body")
    assert thin.verdict == "OK", thin.text


def test_a_waist_the_lip_closes_over_is_flagged_refused_and_split(bench: Any) -> None:
    """mqucifer/print-shop#161: a piece 14 mm wide with a waist 2 mm across. A lip of 1.2 mm on
    each side closes its opening over the waist. The build used to step that piece's lip down
    until its opening was one, 0.6 mm all round, and `capture` at the rim asked then failed
    it. Every piece takes the lip asked now: `check --lip` flags the piece as the build would
    refuse it, with the line to split it along; `build` refuses it the same way; and split
    there, it builds, and `capture` holds both halves at the rim asked."""
    plain = bench("pieces", "check", "waist.json", "--max-lead", 20)
    assert plain.verdict == "OK", "2 mm is over the 1.2 mm a neck is"
    told = bench("pieces", "check", "waist.json", "--max-lead", 20, "--lip", 1.2)
    assert told.verdict == "FAIL" and flags_of(told) == {("no-opening", 1)}, told.text
    assert "a waist 2.00 mm across at (30.0, 12.0)" in told.text
    assert "`pieces split MAP 1 --line 30,11,30,13`" in told.text, "the build's own words"
    assert len(told.record["second_check"]) == 5
    lips = ["--thickness", 3, "--pane", 1, "--lip", "1.2,0.6", "--play", 0.25]
    refused = bench("pieces", "build", "waist.json", *lips, "--build-out", "waist")
    assert refused.verdict == "FAIL" and refused.code == 1, refused.text
    why = refused.record["why"]
    assert "piece 1, round (30.0, 12.0), has no one opening at a lip of 1.2 mm" in why
    assert "a waist 2.00 mm across at (30.0, 12.0)" in why
    assert "`pieces split MAP 1 --line 30,11,30,13`" in why
    assert "isn't stepped down to" in refused.text
    assert not (bench.work / "waist" / "frame.step").exists()
    # The line it gave, as given.
    cut = bench("pieces", "split", "waist.json", 1, "--line", "30,11,30,13", "--to", "waist-1.json")
    assert cut.verdict == "OK" and cut.numbers["pieces_after"] == 2, cut.text
    built = bench("pieces", "build", "waist-1.json", *lips, "--build-out", "waist")
    assert built.verdict == "OK", built.text
    assert {row["lip_mm"] for row in built.numbers["lips"]} == {1.2}, "every piece, the lip asked"
    held = bench(
        "capture", built.numbers["glass"]["frame"], "--pocket-z", 1.3, "--clearance", 0.25,
        "--pane-thickness", 1, "--full-rim", 1.2, "--least-rim", 0.6, "--min-visible", 40,
        "--panes", 2,
    )  # fmt: skip
    assert held.verdict == "OK", held.text


@pytest.mark.parametrize(
    ("name", "says", "wide"),
    [
        # A band on a slant with its waist a quarter of the way along: the width is the
        # waist's, 2 mm, not the 18 mm a line along the band was.
        ("slanted_waist", "a waist 2.0", 2.0),
        # Round a bend, narrow for 17 mm: the middle between the opening's two halves is in
        # the bend's hollow, outside the piece, and no line was given.
        ("bend", "a narrow run, about 18 mm of the piece that leaves no opening", 2.0),
    ],
)
def test_every_waist_a_build_refuses_comes_with_a_line_that_split_takes(
    bench: Any, name: str, says: str, wide: float
) -> None:
    """mqucifer/print-shop#161: on a traced scene one refusal gave an 18 mm "waist", the line's
    own length along a slanted piece, and another, for a piece round a bend, gave no line and
    "the parts 49.84 mm apart". The narrow place is found in the piece itself, and its line
    crosses it square to the nearest edge."""
    lips = ["--thickness", 3, "--pane", 1, "--lip", 1.2, "--play", 0.25]
    refused = bench("pieces", "build", f"{name}.json", *lips, "--build-out", name)
    assert refused.verdict == "FAIL", refused.text
    why = refused.record["why"]
    assert says in why and "mm apart" not in why, why
    line = re.search(r"`pieces split MAP 1 --line ([-0-9.,]+)`", why)
    assert line, why
    x1, y1, x2, y2 = (float(v) for v in line.group(1).split(","))
    assert ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 == pytest.approx(wide, abs=0.05)
    cut = bench(
        "pieces", "split", f"{name}.json", 1, "--line", line.group(1), "--to", f"{name}-1.json"
    )
    assert cut.verdict == "OK" and cut.numbers["pieces_after"] == 2, cut.text
    built = bench("pieces", "build", f"{name}-1.json", *lips, "--build-out", name)
    assert built.verdict == "OK", built.text


def test_a_piece_is_judged_on_its_pocket_as_built_and_refused_the_same_way(bench: Any) -> None:
    """mqucifer/print-shop#161: a waist 2.45 mm across, between two curved sides. On the
    piece's outline a lip of 1.2 mm leaves its opening one piece, a hair wide at the waist. Its
    pocket as built is its fitted curves moved inside the outline, and there the opening is
    two. The plan passed the piece and the build then ended `NO ANSWER`, "leaves no one
    opening", with no place and no fix. The plan judges the pocket as it's built now."""
    lips = ["--thickness", 3, "--pane", 1, "--lip", 1.2, "--play", 0.25]
    refused = bench("pieces", "build", "curved_waist.json", *lips, "--build-out", "curved")
    assert refused.verdict == "FAIL" and refused.code == 1, refused.text
    why = refused.record["why"]
    assert "once its pocket's curves are fitted inside its outline" in why
    assert "a waist 2.45 mm across at (30.0, " in why
    line = re.search(r"`pieces split MAP 1 --line ([-0-9.,]+)`", why)
    assert line, why
    assert len(refused.record["second_check"]) >= 1, "a refusal with its second check"
    cut = bench(
        "pieces", "split", "curved_waist.json", 1, "--line", line.group(1), "--to", "curved-1.json"
    )
    assert cut.verdict == "OK" and cut.numbers["pieces_after"] == 2, cut.text
    built = bench("pieces", "build", "curved-1.json", *lips, "--build-out", "curved")
    assert built.verdict == "OK", built.text


def test_check_with_a_lip_flags_what_a_build_would_refuse_and_nothing_else(bench: Any) -> None:
    """mqucifer/print-shop#161: `pieces check --lip L` makes the build's own plan, so a piece
    the build would refuse for want of one opening is flagged before it: here a waist that's
    whole on its outline and closes on its pocket as built. A map the build takes has no such
    flag."""
    told = bench("pieces", "check", "curved_waist.json", "--max-lead", 20, "--lip", 1.2)
    assert told.verdict == "FAIL" and flags_of(told) == {("no-opening", 1)}, told.text
    assert "once its pocket's curves are fitted inside its outline" in told.text
    assert re.search(r"`pieces split MAP 1 --line [-0-9.,]+`", told.text)
    assert bench("pieces", "check", "curved_waist.json", "--max-lead", 20).verdict == "OK"
    fine = bench("pieces", "check", "rules.json", "--lip", 1.2)
    assert fine.verdict == "OK" and not flags_of(fine), fine.text
    # At a lip the waist takes, nothing is flagged.
    narrow = bench("pieces", "check", "curved_waist.json", "--max-lead", 20, "--lip", 0.8)
    assert narrow.verdict == "OK", narrow.text


def test_a_merge_across_a_patch_of_lead_takes_the_lead_between_and_builds_first_time(
    bench: Any,
) -> None:
    """mqucifer/print-shop#161: a small piece 1.6 mm from a large one, with no line of their
    own between them and one corner near. On a traced scene such a merge joined the two by a
    neck, with scraps of the old lead left as lines inside the piece, and a run fixed it one
    build at a time. The merge takes the lead between them now: the small piece's hull with
    what of the large one lies near it."""
    answer = bench("pieces", "merge", "apart.json", 2, 1, "--to", "apart-1.json")
    assert answer.verdict == "OK", answer.text
    assert "They were 1.60 mm apart at the nearest, wider than the map's usual 0.95 mm line" in (
        answer.record["why"]
    )
    (row,) = answer.numbers["changed"]
    assert row["flags"] == [] and answer.numbers["pieces_after"] == 2
    saved = json.loads((bench.work / "apart-1.json").read_text())
    whole = next(p for p in saved["pieces"] if p["id"] == 1)
    assert not whole.get("holes"), "no scrap of lead is left inside it"
    # The diamond's far corner is where it was, and the third piece keeps its lead.
    assert max(x for x, _ in whole["outline"]) == pytest.approx(49.4, abs=1e-6)
    assert "every other piece's outline is the same, point for point" in answer.text
    again = bench("pieces", "check", "apart-1.json", "--max-lead", 99, "--lip", 1.2)
    assert not flags_of(again), again.text
    built = bench(
        "pieces", "build", "apart-1.json", "--thickness", 3, "--pane", 1, "--lip", 1.2,
        "--play", 0.25, "--build-out", "apart",
    )  # fmt: skip
    assert built.verdict == "OK", built.text


def test_a_build_names_everything_that_stops_a_piece_together(bench: Any) -> None:
    """Three lines inside one piece, each joined to no other: one refusal names all three,
    each with its own place and fix, where it named one a build."""
    answer = bench(
        "pieces", "build", "lone.json", "--thickness", 3, "--pane", 1, "--lip", 1.2,
        "--play", 0.25, "--build-out", "lone",
    )  # fmt: skip
    assert answer.verdict == "FAIL", answer.text
    (line,) = answer.numbers["unbuildable"]
    assert line.startswith("piece 4, ") and line.count("has a lead line inside it") == 3
    assert line.count("`pieces drop-line MAP --at ") == 3


def test_a_lip_is_sized_to_the_panes_visible_share_as_capture_reads_it(bench: Any) -> None:
    """mqucifer/print-shop#161: a thin crescent, 5 mm across. At the full lip of 1.2 mm only
    52% of its pane is visible, and `capture`, asked for 60%, said to set its rim to 1.031 mm:
    the widest that keeps 60%. With `--min-visible` the build sizes each piece's lip by
    `capture`'s own function, so the two can't differ on a rim. A piece that can't keep the
    share at the least lip is refused, with its fix, and `check --lip` reads the same."""
    lips = ["--thickness", 3, "--pane", 1, "--play", 0.25, "--lip", "1.2,0.6"]
    rim = [
        "--pocket-z", 1.3, "--clearance", 0.25, "--pane-thickness", 1, "--full-rim", 1.2,
        "--least-rim", 0.6, "--min-visible", 60, "--panes", 1,
    ]  # fmt: skip
    plain = bench("pieces", "build", "crescent.json", *lips, "--build-out", "crescent-full")
    assert plain.verdict == "OK", plain.text
    short = bench("capture", plain.numbers["glass"]["frame"], *rim)
    assert short.verdict == "FAIL" and "set this pane's rim to 1.031 mm" in short.record["why"]
    built = bench(
        "pieces", "build", "crescent.json", *lips, "--min-visible", 60, "--build-out", "crescent"
    )
    assert built.verdict == "OK", built.text
    (row,) = built.numbers["lips"]
    assert row["lip_mm"] == pytest.approx(1.031, abs=0.002), "the rim `capture` asked for"
    assert "narrower, so each keeps 60% of its pane visible: piece 1 at 1.031" in built.text
    held = bench("capture", built.numbers["glass"]["frame"], *rim)
    assert held.verdict == "OK", held.text
    # At 90% even the least lip hides too much: refused by the build, and flagged by the check.
    refused = bench(
        "pieces", "build", "crescent.json", *lips, "--min-visible", 90, "--build-out", "crescent-90"
    )
    assert refused.verdict == "FAIL", refused.text
    assert "keeps 81.5% of its pane visible at even the least lip, 0.6 mm, where 90% is wanted" in (
        refused.record["why"]
    )
    assert "`pieces merge` it into a neighbour" in refused.record["why"]
    told = bench(
        "pieces", "check", "crescent.json", "--max-lead", 99, "--lip", "1.2,0.6",
        "--min-visible", 90,
    )  # fmt: skip
    assert flags_of(told) == {("no-opening", 1)} and "where 90% is wanted" in told.text
    fine = bench(
        "pieces", "check", "crescent.json", "--max-lead", 99, "--lip", "1.2,0.6",
        "--min-visible", 60,
    )  # fmt: skip
    assert fine.verdict == "OK", fine.text


def test_a_share_sized_opening_is_the_shape_capture_measures_narrow_parts_and_all(
    bench: Any,
) -> None:
    """mqucifer/print-shop#161: a piece with a tail 2.5 mm wide past a pinch. At the rim that
    keeps 60% of its pane visible, 0.903 mm, the tail still shows a strip 0.7 mm wide, apart
    from the body's opening. `capture` counts that strip; the build left it out as under two
    nozzles wide, and `capture` then read the rim as right and 55.8% visible: "the frame
    covers it elsewhere". It's cut now, and the reply says a slot that narrow may print
    closed."""
    built = bench(
        "pieces", "build", "tailed.json", "--thickness", 3, "--pane", 1, "--play", 0.25,
        "--lip", "1.2,0.6", "--min-visible", 60, "--build-out", "tailed",
    )  # fmt: skip
    assert built.verdict == "OK", built.text
    (row,) = built.numbers["lips"]
    assert row["lip_mm"] == pytest.approx(0.903, abs=0.002)
    assert "1 part(s) of an opening under 0.8 mm wide are cut" in built.text
    frame = built.numbers["glass"]["frame"]
    held = bench(
        "capture", frame, "--pocket-z", 1.3, "--clearance", 0.25, "--pane-thickness", 1,
        "--full-rim", 1.2, "--least-rim", 0.6, "--min-visible", 60, "--panes", 1,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    assert bench("thin", frame, "--one-body").verdict == "OK"
    assert bench("info", frame).numbers["solids"] == 1


def test_a_piece_too_thin_for_a_pane_is_named_and_nothing_is_built(
    bench: Any, broken: Answer
) -> None:
    sliver = piece_at(bench.work, "b0.json", SLIVER)
    answer = bench(
        "pieces", "build", "b0.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "nothing",
    )  # fmt: skip
    assert answer.verdict == "FAIL" and answer.code == 1
    said = answer.numbers["unbuildable"]
    thin = next(line for line in said if line.startswith(f"piece {sliver},"))
    assert "leaves it no opening 0.8 mm wide, as it's 2.00 mm at its widest" in thin
    assert "`pieces merge` it into a neighbour" in thin
    # The necked piece is wide, so its line says what cuts its opening in two, and where.
    necked = piece_at(bench.work, "b0.json", NECK)
    neck = next(line for line in said if line.startswith(f"piece {necked},"))
    assert "a waist 1.00 mm across at (" in neck or "a narrow run, about" in neck, neck
    assert re.search(rf"`pieces split MAP {necked} --line [0-9.]+,", neck), "with its line"
    around = piece_at(bench.work, "b0.json", ROUND_SMALL)
    assert any(f"piece {around} surrounds piece" in line for line in said)
    assert "so nothing was" in answer.record["why"] and "pieces merge" in answer.record["why"]
    # No line says a wide piece has no pane or opening without what cuts it.
    for line in said:
        named = ("surrounds", "leaves it no", "cuts its", "a waist ", "a narrow run")
        assert any(word in line for word in named), line
    assert not (bench.work / "nothing" / "frame.step").exists()
    thin = bench(
        "pieces", "build", "rules.json", "--thickness", 1.4, "--pane", 1, "--lip", "1.2",
        "--play", 0.25,
    )  # fmt: skip
    assert thin.verdict == "NO ANSWER" and "Give --thickness 2" in thin.record["why"]


def test_a_script_of_fixes_makes_the_part_again(bench: Any, broken: Answer) -> None:
    """As the skill says: a run's script calls the bench's own commands, so a clean run of it
    makes the part from the picture."""
    script = bench.work / "make.py"
    script.write_text(
        "import sys\n"
        "sys.path.insert(0, '/opt/bench')\n"
        "from bench.cli import main\n"
        "def bench(*words):\n"
        "    assert main([str(w) for w in words]) == 0, words\n"
        "bench('pieces', 'trace', 'clean.png', '--width', 120, '--to', 's0.json')\n"
        "bench('pieces', 'merge', 's0.json', 1, 2, '--to', 's1.json')\n"
        "bench('pieces', 'build', 's1.json', '--thickness', 3, '--pane', 1, '--lip', '1.2,0.8',"
        " '--play', 0.25, '--build-out', 'scripted')\n"
    )
    done = host.run(["make.py"], bench.work, python=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (bench.work / "scripted" / "frame.step").is_file()
    assert standing.pane_bodies(bench.work, ["scripted/pane_white.step"]) == 1
