"""A pair farther apart than any gap a reading judges is read by its boxes
(mqucifer/print-shop#354).

`clearance` read every pair by its exact distance, so a whole assembly of alike parts placed by
`--placed` overran the bench's limit. The part is made here: a slice of a flat ring, 20 to 40 mm
from its axis, 15° wide and 5 mm thick, with a round hole through its middle, built centred in its
own frame. 24 of them make the ring, each turned about z to its place and moved back out, so each
touches its two neighbours and the 252 other pairs are 2 x 20 x sin 7.5° = 5.2208 mm apart or more.
Each reading is checked against the same reading of every pair by its exact distance, `--exact`.
Nothing is any Sponsor's design.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import far, host
from print_shop.rounds import needs, reports
from print_shop.rounds.needs import Need

PARTS = """
import math

import cadquery as cq

ring = cq.Workplane("XY").circle(40).circle(20).extrude(5)
half, far = math.radians(7.5), 80
wedge = (
    cq.Workplane("XY")
    .polyline([(0, 0), (far * math.cos(half), -far * math.sin(half)),
               (far * math.cos(half), far * math.sin(half))])
    .close()
    .extrude(5)
)
hole = cq.Workplane("XY").center(30, 0).circle(1.5).extrude(5)
cq.exporters.export(ring.intersect(wedge).cut(hole).translate((-30, 0, 0)), "slice.step")
"""

SLICES = 24
OUT = 30.0
INNER = 20.0
#: The nearest two slices that aren't neighbours come: 30° apart at their inner corners.
NEXT = 2 * INNER * math.sin(math.radians(360 / SLICES) / 2)


def entry(k: int) -> dict[str, Any]:
    degrees = 360 * k / SLICES
    turned = math.radians(degrees)
    return {
        "name": f"s{k}",
        "file": "slice.step",
        "layers": "z",
        "turn": [["z", degrees]],
        "at": [round(OUT * math.cos(turned), 6), round(OUT * math.sin(turned), 6), 0],
    }


# The bounds, with no CAD.


def box_of(points: np.ndarray, pad: float = 0.0) -> far.Bounds:
    return far.bounds_from(points.min(axis=0), points.max(axis=0), points, pad)


def cube(at: tuple[float, float, float], degrees: float = 0.0) -> np.ndarray:
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    turn = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return np.asarray(corners @ turn.T + np.asarray(at), dtype=float)


def test_two_cubes_side_by_side_are_their_gap_apart_by_both_bounds() -> None:
    by_box, by_hull = far.least_apart(box_of(cube((0, 0, 0))), box_of(cube((5, 0, 0))))
    assert by_box == pytest.approx(3.0) and by_hull == pytest.approx(3.0)


def test_a_pad_comes_off_each_body_s_bound() -> None:
    by_box, by_hull = far.least_apart(box_of(cube((0, 0, 0)), 0.05), box_of(cube((5, 0, 0)), 0.05))
    assert by_box == pytest.approx(2.9) and by_hull == pytest.approx(2.9)


def test_two_turned_bars_whose_boxes_meet_are_told_apart_by_their_hulls() -> None:
    """Two bars along the diagonal, side by side: their boxes overlap, so the boxes can't tell
    them apart, and their hulls are their true gap apart across the diagonal."""
    one = np.array([[0, 0, 0], [10, 10, 0], [0, 0, 1], [10, 10, 1]], float)
    two = one + np.array([3.0, -3.0, 0.0])
    by_box, by_hull = far.least_apart(box_of(one), box_of(two))
    assert by_box <= 0.0
    assert by_hull == pytest.approx(math.hypot(3, 3))


def test_a_bound_is_never_more_than_the_true_gap_between_turned_cubes() -> None:
    """Cubes turned every way about z, along x: the true least gap is between a corner or face
    of each, read here between their faces' points on a fine grid, which is never less than it."""
    grid = np.linspace(-1, 1, 41)
    face = np.array([[1.0, y, z] for y in grid for z in grid])
    faces = np.vstack([face @ turn.T for turn in _turns()])
    for degrees in range(0, 91, 15):
        for apart in (2.5, 3.0, 4.0, 6.0):
            one, two = cube((0, 0, 0), degrees), cube((apart, 0, 0), -degrees)
            on_one = faces @ _about_z(degrees).T
            on_two = faces @ _about_z(-degrees).T + np.array([apart, 0, 0])
            sampled = min(
                float(np.min(np.linalg.norm(on_two - p, axis=1))) for p in on_one[::7]
            )
            bound = max(far.least_apart(box_of(one), box_of(two)))
            assert bound <= sampled + 1e-9, (degrees, apart, bound, sampled)


def _about_z(degrees: float) -> np.ndarray:
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _turns() -> list[np.ndarray]:
    """The six turns that take the +x face to each face of a cube."""
    x, y = np.eye(3), np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], float)
    z = np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float)
    return [x, y, y @ y, y.T, z, z.T]


def test_a_need_on_a_pair_read_by_its_boxes_says_so_and_to_read_it_exactly() -> None:
    need = Need(
        "apart", "x", "chosen", "bench", command="clearance slice.step --placed ring.json",
        number="pairs.1.gap_mm", at_most=12.0,
    )  # fmt: skip
    pairs = [
        {"a": "s0", "b": "s1", "verdict": "TOUCHING", "gap_mm": 0.0},
        {"a": "s0", "b": "s2", "verdict": "CLEAR", "read_by": "boxes", "box_gap_mm": 3.1},
    ]
    record = {"verdict": "TOUCHING", "why": "", "numbers": {"pairs": pairs}}
    found = needs.judge(need, 0, "verdict: TOUCHING: ok\n", record)
    assert not found.passed
    assert "`pairs.1` was read by its boxes" in found.why, found.why
    assert "at least 3.1 mm, so it has no `gap_mm`" in found.why
    assert "add `--exact`" in found.why


# The bench, in its image.


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("ring-far")
    (found / "parts.py").write_text(PARTS, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    ring = {"parts": [entry(k) for k in range(SLICES)]}
    (found / "ring.json").write_text(json.dumps(ring), encoding="utf-8")
    return found


def ask(work: Path, *args: str) -> tuple[str, dict[str, Any]]:
    done = host.run(["clearance", "slice.step", "--placed", "ring.json", *args], work)
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text())


def neighbours(pair: dict[str, Any]) -> bool:
    one, two = (int(pair[k].rsplit("s", 1)[1]) for k in ("a", "b"))
    return (one - two) % SLICES in (1, SLICES - 1)


#: Every figure a pair read exactly has, but its time.
FIGURES = (
    "verdict", "gap_mm", "gap_by", "gap_mesh_mm", "gap_occt_mm", "overlap_mesh_mm3",
    "overlap_band_mm3", "overlap_occt_common_mm3", "overlap_occt_cut_mm3", "differs", "untrusted",
)  # fmt: skip


def same_as_exact(boxed: dict[str, Any], exact: dict[str, Any]) -> list[dict[str, Any]]:
    """Each pair read by its boxes is CLEAR and its box gap no more than its exact gap; every other
    pair's figures are the exact reading's own. Returns the pairs read by their boxes."""
    assert boxed["verdict"] == exact["verdict"]
    one, two = boxed["numbers"]["pairs"], exact["numbers"]["pairs"]
    assert [(p["a"], p["b"]) for p in one] == [(p["a"], p["b"]) for p in two]
    far_off = []
    for mine, theirs in zip(one, two, strict=True):
        assert "read_by" not in theirs
        if mine.get("read_by") == "boxes":
            far_off.append(mine)
            assert mine["verdict"] == theirs["verdict"] == "CLEAR"
            assert "gap_mm" not in mine
            assert 0 < mine["box_gap_mm"] <= theirs["gap_mm"]
            for name, place in (mine.get("places") or {}).items():
                assert place["verdict"] == theirs["places"][name]["verdict"] == "NOT CAUGHT"
            continue
        assert {k: mine.get(k) for k in FIGURES} == {k: theirs.get(k) for k in FIGURES}
        for name, place in (mine.get("places") or {}).items():
            assert place == theirs["places"][name], name
    return far_off


@pytest.mark.bench
def test_a_ring_s_far_pairs_are_read_by_their_boxes_and_its_neighbours_exactly(work: Path) -> None:
    text, record = ask(work)
    exact_text, exact = ask(work, "--exact")
    far_off = same_as_exact(record, exact)
    pairs = record["numbers"]["pairs"]
    assert len(pairs) == SLICES * (SLICES - 1) // 2
    read = [p for p in pairs if p.get("read_by") != "boxes"]
    # Only the neighbours are read exactly, each touching, as the reading of every pair says.
    assert all(neighbours(p) for p in read), [(p["a"], p["b"]) for p in read]
    assert len(read) == SLICES and len(far_off) == len(pairs) - SLICES
    assert all(p["verdict"] == "TOUCHING" for p in read)
    assert record["verdict"] == "TOUCHING", text
    by = record["numbers"]["by_boxes"]
    least = min(p["box_gap_mm"] for p in far_off)
    assert by["pairs"] == len(far_off) and by["least_box_gap_mm"] == least
    assert pairs[by["least"]]["box_gap_mm"] == least
    assert least <= NEXT
    exact_least = min(p["gap_mm"] for p in exact["numbers"]["pairs"] if not neighbours(p))
    assert exact_least == pytest.approx(NEXT, abs=1e-3)
    said = f"{len(far_off)} pair(s) farther apart than any gap judged"
    assert said in text and f"least box gap {least:.4f} mm" in text, text
    assert f"{len(pairs)} pair(s): {SLICES} touching, {len(far_off)} clear by their boxes" in text
    assert "read by their boxes" not in exact_text and "by_boxes" not in exact["numbers"]
    assert reports.unlike("clearance", record["numbers"]) == []
    # The time falls with the far pairs: 252 of 276 are read by their bounds alone.
    assert record["numbers"]["pairs_s"] < 0.5 * exact["numbers"]["pairs_s"], (
        record["numbers"]["pairs_s"],
        exact["numbers"]["pairs_s"],
    )


@pytest.mark.bench
def test_pairs_within_any_gap_judged_are_read_exactly(work: Path) -> None:
    """With `--min-gap` past the next slice but one, those pairs are read exactly, `TOO CLOSE` as
    the reading of every pair says, and only pairs farther apart than it are read by their boxes.
    `--max-gap` holds each pair's least gap, so a far pair is too loose: every pair is exact."""
    text, record = ask(work, "--min-gap", "6")
    _, exact = ask(work, "--min-gap", "6", "--exact")
    far_off = same_as_exact(record, exact)
    assert record["verdict"] == "TOO CLOSE", text
    assert record["numbers"]["by_boxes"]["beyond_mm"] == 6
    assert all(p["box_gap_mm"] > 6 for p in far_off)
    # The neighbours touch, and the slices next but one are NEXT apart: both are too close.
    close = [p for p in record["numbers"]["pairs"] if p["verdict"] == "TOO CLOSE"]
    apart = [p for p in close if not neighbours(p)]
    assert len(close) == 2 * SLICES and len(apart) == SLICES
    assert all(p["gap_mm"] == pytest.approx(NEXT, abs=1e-3) for p in apart)
    assert len(far_off) == len(record["numbers"]["pairs"]) - 2 * SLICES
    text, loose = ask(work, "--max-gap", "1", "--part", *(f"s{k}" for k in range(5)))
    assert loose["verdict"] == "TOO LOOSE", text
    assert not any(p.get("read_by") for p in loose["numbers"]["pairs"])
    assert "by_boxes" not in loose["numbers"]


@pytest.mark.bench
def test_a_pair_a_place_may_catch_is_read_exactly(work: Path) -> None:
    """A box round the seam between s0 and s1: every pair with both slices' boxes reaching it is
    read exactly, its place read as every pair's reading reads it, and the rest by their boxes,
    the place catching neither."""
    seam = "--box=seam=24,-1,-1,36,9,6"
    text, record = ask(work, "--min-gap", "0.1", seam)
    _, exact = ask(work, "--min-gap", "0.1", seam, "--exact")
    far_off = same_as_exact(record, exact)
    assert record["verdict"] == "TOO CLOSE", text
    pairs = record["numbers"]["pairs"]
    [caught] = [p for p in pairs if p["places"]["seam"]["verdict"] != "NOT CAUGHT"]
    assert {caught["a"], caught["b"]} == {"slice.step as s0", "slice.step as s1"}
    assert caught["places"]["seam"]["verdict"] == "TOO CLOSE"
    assert far_off and all(p["places"]["seam"]["verdict"] == "NOT CAUGHT" for p in far_off)
