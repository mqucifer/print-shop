"""The `pieces` commands on pictures and maps drawn in code, in the bench's image with no network
(mqucifer/print-shop#152, mqucifer/print-shop#153). Needs `scripts/bench.py build`.

`bench_cases/planted_pieces.py` draws each: a clean panel, one with one of each rule break, a
curved scene, and two maps as rules would write them. Right answers are arithmetic.
"""

from __future__ import annotations

import json
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
    assert len(answer.record["second_check"]) == 3
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
    assert pause.verdict == "OK" and "z=2.0" in pause.record["why"], pause.text
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
    assert "nothing of it is left, as it's 2.00 mm at its widest" in thin
    assert "`pieces merge` it into a neighbour" in thin
    # The necked piece is wide, so its line says what cuts its opening in two, and where.
    necked = piece_at(bench.work, "b0.json", NECK)
    neck = next(line for line in said if line.startswith(f"piece {necked},"))
    assert "a narrow place near (20" in neck and f"`pieces split MAP {necked} --line`" in neck
    around = piece_at(bench.work, "b0.json", ROUND_SMALL)
    assert any(f"piece {around} surrounds piece" in line for line in said)
    assert "so nothing was" in answer.record["why"] and "pieces merge" in answer.record["why"]
    # No line says a wide piece has no pane or opening without what cuts it.
    for line in said:
        assert "surrounds" in line or "nothing of it is left" in line or "cuts its" in line, line
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
