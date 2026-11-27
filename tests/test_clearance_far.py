"""A pair farther apart than any gap a reading judges is read by its bounds
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
import os
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
        {"a": "s0", "b": "s2", "verdict": "CLEAR", "read_by": "bounds", "bound_gap_mm": 3.1},
    ]
    record = {"verdict": "TOUCHING", "why": "", "numbers": {"pairs": pairs}}
    found = needs.judge(need, 0, "verdict: TOUCHING: ok\n", record)
    assert not found.passed
    assert "`pairs.1` was read by its bounds" in found.why, found.why
    assert "at least 3.1 mm, so it has no `gap_mm`" in found.why
    assert "add `--exact`" in found.why


def test_a_command_stopped_at_its_limit_says_where_it_was() -> None:
    """A whole assembly's `clearance` stopped at 600 s said only that: now the host says the last
    step it said, how many pairs it reads exactly and how many it had read."""
    said = [
        "bench: step 3.0 clearance: loading 90 part(s)",
        "bench: memory 3.1 current=1 peak=2",
        "bench: step 40.2 clearance: 4005 pairs: 3000 by their boxes, 500 by their hulls, "
        "200 by their meshes, 305 to read exactly; reading the 305 exactly: 120 read",
    ]
    assert host.past(600, False, said) == (
        "verdict: NO ANSWER: it ran past the 600 s limit and was stopped. Where it was: "
        "clearance: 4005 pairs: 3000 by their boxes, 500 by their hulls, 200 by their meshes, "
        "305 to read exactly; reading the 305 exactly: 120 read, from 40.2 s in\n"
    )
    assert host.past(600, False) == host.past(600, False, []), "as before, where it said nothing"
    loading = "Where it was: clearance: loading 90 part(s), from 3.0 s in\n"
    assert host.past(600, False, said[:2]).endswith(loading)


def test_the_host_keeps_what_a_command_stopped_at_its_limit_said() -> None:
    """Both ways the host waits: to its limit, or able to take up a longer one."""
    import sys

    step = "bench: step 0.1 clearance: 10 pairs: reading the 4 exactly: 1 read"
    child = f"import sys, time; print({step!r}, file=sys.stderr, flush=True); time.sleep(30)"
    for longer in (None, 30):
        stopped, limit, _ = host._waited([sys.executable, "-c", child], 1, longer)
        assert stopped is None and limit == 1
        assert list(host.STOPPED_SAID) == [step], (longer, host.STOPPED_SAID)


def test_a_step_said_again_says_how_far_it_has_got(capsys: pytest.CaptureFixture[str]) -> None:
    from print_shop.bench import progress

    with progress.step("clearance: reading 3 exactly"):
        progress.again("clearance: reading 3 exactly: 2 read")
    lines = [ln for ln in capsys.readouterr().err.splitlines() if ln.startswith(progress.STEP_LINE)]
    assert lines[0].endswith("clearance: reading 3 exactly")
    assert lines[1].endswith("clearance: reading 3 exactly: 2 read")
    assert lines[2].endswith("between steps, after clearance: reading 3 exactly: 2 read")


def test_a_small_reading_keeps_its_limit_and_a_large_one_states_its_own(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Made-up figures: 95 s in, 10 of 410 pairs read exactly at 3 s each. Its limit is taken
    up from the pairs read so far, stated with room, and raised as they come slower, up to the
    hour offered; a reading that fits keeps 600 s."""
    from print_shop.bench import pace

    monkeypatch.setenv(pace.TIMEOUT, "600")
    small = pace.Pace(600, 3600)
    assert small.after(elapsed=30, done=10, spent=5, left=20) is None
    assert small.limit == 600 and small.took is None
    assert pace.TAKEN not in capsys.readouterr().err
    large = pace.Pace(600, 3600)
    assert large.after(elapsed=95, done=10, spent=30, left=400) == "take"
    projected = 95 + 400 * 3.0
    assert large.took == (projected, 10)
    assert large.limit == math.ceil(projected * pace.ROOM + pace.MARGIN)
    assert pace.TAKEN in capsys.readouterr().err
    assert os.environ[pace.TIMEOUT] == str(int(large.limit)), "the `ran:` line's limit"
    stated = large.limit
    assert large.after(elapsed=200, done=40, spent=120, left=370) is None, "as projected"
    assert large.after(elapsed=900, done=100, spent=800, left=310) == "raise"
    assert stated < large.limit <= 3600
    assert large.after(elapsed=1000, done=101, spent=900, left=309) is None
    assert pace.TAKEN not in capsys.readouterr().err, "taken once"
    capped = pace.Pace(600, 3600)
    assert capped.after(elapsed=100, done=10, spent=200, left=2000) == "take"
    assert capped.limit == 3600, "never past the offer"
    assert capped.after(elapsed=3585, done=1500, spent=3400, left=500) == "stop"


def test_with_no_longer_limit_offered_a_reading_stops_itself_before_its_own() -> None:
    from print_shop.bench import pace

    alone = pace.Pace(600, None)
    assert alone.after(elapsed=500, done=100, spent=440, left=300) is None
    assert alone.limit == 600 and alone.took is None
    assert alone.after(elapsed=581, done=120, spent=520, left=280) == "stop", "20 s to write"
    assert pace.Pace(None, None).after(elapsed=5000, done=1, spent=1, left=1) is None


def test_the_pairs_read_exactly_are_read_in_an_order_spread_through_them() -> None:
    from print_shop.bench import pace

    for count in (0, 1, 2, 3, 10, 400, 1000):
        order = pace.spread(count)
        assert sorted(order) == list(range(count)), count
    first = pace.spread(400)[:3]
    assert max(first) - min(first) > 400 / 3, first


def test_the_host_offers_clearance_the_hour_as_it_offers_load() -> None:
    assert host.offered(["clearance", "a.step", "b.step"]) == host.TWO_PASSES
    assert host.offered(["load", "a.json"]) == host.TWO_PASSES
    assert host.offered(["info", "a.step"]) is None


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
    """Each pair read by its bounds is CLEAR and its box gap no more than its exact gap; every other
    pair's figures are the exact reading's own. Returns the pairs read by their bounds."""
    assert boxed["verdict"] == exact["verdict"]
    one, two = boxed["numbers"]["pairs"], exact["numbers"]["pairs"]
    assert [(p["a"], p["b"]) for p in one] == [(p["a"], p["b"]) for p in two]
    far_off = []
    for mine, theirs in zip(one, two, strict=True):
        assert "read_by" not in theirs
        if mine.get("read_by") == "bounds":
            far_off.append(mine)
            assert mine["verdict"] == theirs["verdict"] == "CLEAR"
            assert "gap_mm" not in mine
            assert 0 < mine["bound_gap_mm"] <= theirs["gap_mm"]
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
    read = [p for p in pairs if p.get("read_by") != "bounds"]
    # Only the neighbours are read exactly, each touching, as the reading of every pair says.
    assert all(neighbours(p) for p in read), [(p["a"], p["b"]) for p in read]
    assert len(read) == SLICES and len(far_off) == len(pairs) - SLICES
    assert all(p["verdict"] == "TOUCHING" for p in read)
    assert record["verdict"] == "TOUCHING", text
    by = record["numbers"]["by_bounds"]
    least = min(p["bound_gap_mm"] for p in far_off)
    assert by["pairs"] == len(far_off) and by["least_bound_gap_mm"] == least
    assert pairs[by["least"]]["bound_gap_mm"] == least
    assert least <= NEXT
    exact_least = min(p["gap_mm"] for p in exact["numbers"]["pairs"] if not neighbours(p))
    assert exact_least == pytest.approx(NEXT, abs=1e-3)
    said = f"{len(far_off)} pair(s) farther apart than any gap judged"
    assert said in text and f"least bound gap at least {least:.4f} mm" in text, text
    assert f"{len(pairs)} pair(s): {SLICES} touching, {len(far_off)} clear by their bounds" in text
    assert "read by their bounds" not in exact_text and "by_bounds" not in exact["numbers"]
    assert reports.unlike("clearance", record["numbers"]) == []
    # The time falls with the far pairs: 252 of 276 are read by their bounds alone.
    assert record["numbers"]["pairs_s"] < 0.5 * exact["numbers"]["pairs_s"], (
        record["numbers"]["pairs_s"],
        exact["numbers"]["pairs_s"],
    )


@pytest.mark.bench
def test_pairs_within_any_gap_judged_are_read_exactly(work: Path) -> None:
    """With `--min-gap` past the next slice but one, those pairs are read exactly, `TOO CLOSE` as
    the reading of every pair says, and only pairs farther apart than it are read by their bounds.
    `--max-gap` holds each pair's least gap, so a far pair is too loose: every pair is exact."""
    text, record = ask(work, "--min-gap", "6")
    _, exact = ask(work, "--min-gap", "6", "--exact")
    far_off = same_as_exact(record, exact)
    assert record["verdict"] == "TOO CLOSE", text
    assert record["numbers"]["by_bounds"]["beyond_mm"] == 6
    assert all(p["bound_gap_mm"] > 6 for p in far_off)
    # The neighbours touch, and the slices next but one are NEXT apart: both are too close.
    close = [p for p in record["numbers"]["pairs"] if p["verdict"] == "TOO CLOSE"]
    apart = [p for p in close if not neighbours(p)]
    assert len(close) == 2 * SLICES and len(apart) == SLICES
    assert all(p["gap_mm"] == pytest.approx(NEXT, abs=1e-3) for p in apart)
    assert len(far_off) == len(record["numbers"]["pairs"]) - 2 * SLICES
    text, loose = ask(work, "--max-gap", "1", "--part", *(f"s{k}" for k in range(5)))
    assert loose["verdict"] == "TOO LOOSE", text
    assert not any(p.get("read_by") for p in loose["numbers"]["pairs"])
    assert "by_bounds" not in loose["numbers"]


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


def test_a_need_on_a_pair_s_figure_reads_every_pair_exactly() -> None:
    """The harness runs a `clearance` need on a figure under `pairs.` with `--exact`, so its
    figure is the exact gap, never a bound; a need on the verdict alone runs as written."""
    command = "clearance slice.step --placed ring.json --min-gap 0.2"
    on_gap = Need("gap", "x", "chosen", "bench", command=command, number="pairs.3.gap_mm")
    assert needs.read_argv(on_gap)[-1] == "--exact"
    assert needs.read_argv(on_gap)[:-1] == on_gap.argv()
    exact = f"{command} --exact"
    given = Need("gap", "x", "chosen", "bench", command=exact, number="pairs.0.gap_mm")
    assert needs.read_argv(given).count("--exact") == 1
    verdict = Need("fit", "x", "chosen", "bench", command=command, verdicts=["CLEAR"])
    assert needs.read_argv(verdict) == verdict.argv()
    other = Need("wall", "x", "chosen", "bench", command="wall a.step", number="pairs.0.gap_mm")
    assert needs.read_argv(other) == other.argv()



# Where boxes and hulls can't tell a pair apart and the meshes can (mqucifer/print-shop#354): a
# rod through a ring's hole with 1.5 mm of play all round, as a pin goes through a slice's hole;
# a pin wholly inside a block, an overlap with no surfaces meeting; and a plate far from both.

THREADED = """
import cadquery as cq

cq.exporters.export(cq.Workplane("XY").circle(6).circle(3).extrude(2), "ring.step")
cq.exporters.export(cq.Workplane("XY").circle(1.5).extrude(20).translate((0, 0, -9)), "rod.step")
cq.exporters.export(cq.Workplane("XY").box(10, 10, 10).translate((40, 0, 0)), "block.step")
cq.exporters.export(cq.Workplane("XY").box(1, 1, 1).translate((40, 0, 0)), "pin.step")
cq.exporters.export(cq.Workplane("XY").box(10, 10, 2).translate((0, 60, 0)), "plate.step")
"""
THREADED_FILES = ("ring.step", "rod.step", "block.step", "pin.step", "plate.step")


@pytest.fixture(scope="module")
def threaded(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("threaded")
    (found / "parts.py").write_text(THREADED, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    return found


def read(work: Path, *args: str) -> tuple[str, dict[str, Any]]:
    done = host.run(["clearance", *THREADED_FILES, *args], work)
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text())


def pair_of(record: dict[str, Any], one: str, two: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [
        p for p in record["numbers"]["pairs"] if {p["a"], p["b"]} == {one, two}
    ]
    assert len(found) == 1, found
    return found[0]


@pytest.mark.bench
def test_a_rod_through_a_ring_is_read_by_their_meshes_and_a_buried_pin_exactly(
    threaded: Path,
) -> None:
    text, record = read(threaded)
    exact_text, exact = read(threaded, "--exact")
    far_off = same_as_exact(record, exact)
    assert record["verdict"] == "INTERFERE", text
    # The rod's box and hull lie through the ring's; its mesh is 1.5 mm from the ring's.
    threaded_pair = pair_of(record, "ring.step", "rod.step")
    assert threaded_pair["read_by"] == "bounds" and threaded_pair["bound"] == "mesh"
    # Searched for only 0.5 mm past the gap judged, touching: a floor of half a millimetre, said.
    assert threaded_pair["bound_gap_mm"] == pytest.approx(0.5, abs=1e-3)
    assert threaded_pair["bound_at_search"] is True
    assert "least bound gap at least" in text and "as far as its meshes were searched" in text
    assert pair_of(exact, "ring.step", "rod.step")["gap_mm"] == pytest.approx(1.5, abs=1e-4)
    # The pin's surface is nowhere near the block's, and it's wholly inside it: read exactly.
    buried = pair_of(record, "block.step", "pin.step")
    assert "read_by" not in buried and buried["verdict"] == "INTERFERE"
    assert buried["overlap_mesh_mm3"] == pytest.approx(1.0, abs=0.01)
    assert len(far_off) == 9 and record["numbers"]["by_bounds"]["by_mesh"] == 1
    assert "1 by meshes" in text, text
    # Read exactly, the rod and ring are apart by OCCT's distance and the mesh's, with no overlap
    # by the mesh, so OCCT's common and cut aren't run; the buried pin's distance is 0, and they
    # are, and find it.
    rod = pair_of(exact, "ring.step", "rod.step")
    assert rod["occt_booleans"] and rod["overlap_occt_common_mm3"] == 0.0
    assert rod["seconds"]["occt_common"] < 0.01
    assert "for 1 of them OCCT's common and cut weren't run" in exact_text, exact_text
    pin = pair_of(exact, "block.step", "pin.step")
    assert "occt_booleans" not in pin and pin["overlap_occt_common_mm3"] == pytest.approx(1.0)
    assert set(exact["numbers"]["exact_s"]) >= {"occt_distance", "occt_common", "gap_mesh"}


@pytest.mark.bench
def test_a_plan_says_how_the_pairs_would_be_read_and_judges_nothing(threaded: Path) -> None:
    text, record = read(threaded, "--plan")
    _, read_all = read(threaded)
    assert record["verdict"] == "NO ANSWER", text
    assert "a plan, which judges nothing: 10 pairs: " in record["why"], record["why"]
    plan = record["numbers"]["plan"]
    by = read_all["numbers"]["by_bounds"]
    assert plan["pairs"] == 10 and plan["exact"] == 1
    assert [plan[f"by_{w}"] for w in ("box", "hull", "mesh")] == [
        by[f"by_{w}"] for w in ("box", "hull", "mesh")
    ]
    assert plan["sampled"] == 1 and plan["sample_s"] > 0 and plan["exact_estimate_s"] >= 0
    assert all(plan[k] >= 0 for k in ("load_s", "meshes_s", "bounds_s"))
    # Where the sampled read's time went, step by step, and the limit it would be under.
    assert {"occt_distance", "gap_mesh", "occt_common", "occt_cut"} <= set(plan["sample_steps_s"])
    assert plan["limit_s"] == 600 and "under a limit of 600 s" in text
    assert "Their steps: " in text
    assert "pairs" not in record["numbers"], "nothing judged"
    assert reports.unlike("clearance", record["numbers"]) == []


def under_limit(
    work: Path, limit: int, offer: int | None, *args: str
) -> tuple[str, dict[str, Any], int]:
    """`clearance` on the ring with the container told its limit is `limit` s, and offered
    `offer`, as the host tells it, while the host waits 600 s, or the offer once it's taken: so
    a reading of a few seconds meets its limit as a whole assembly's does. The host's limit at
    the end, too."""
    import uuid

    env = {"BENCH_TIMEOUT": str(limit), **({host.OFFER: str(offer)} if offer else {})}
    command = host.bench_command(["clearance", "slice.step", "--placed", "ring.json", *args])
    name = f"print-bench-{uuid.uuid4().hex[:12]}"
    argv = host.container(work, command, name, env=env)
    done, waited, _ = host._waited(argv, 600, offer)
    assert done is not None, "stopped by the host"
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text()), waited


@pytest.mark.bench
def test_a_reading_that_projects_past_its_limit_takes_up_the_offer_and_states_its_own(
    work: Path,
) -> None:
    text, record, waited = under_limit(work, 1, 3600)
    assert waited == 3600, "the host waits for the longer limit once it's taken"
    assert record["verdict"] == "TOUCHING", text
    limit = record["numbers"]["limit"]
    assert limit["limit_s"] == 1 and limit["offered_s"] == 3600
    assert 1 <= limit["taken_after"] < 24 and 1 < limit["stated_s"] < 3600
    assert "so it took up the longer limit offered, 3600 s, and stated" in text, text
    assert record["ran"]["limit_s"] == int(limit["stated_s"]), "the `ran:` line's limit"
    assert host.TAKEN not in text


@pytest.mark.bench
def test_a_reading_with_no_longer_limit_offered_stops_itself_before_its_own(work: Path) -> None:
    text, record, waited = under_limit(work, 1, None)
    assert waited == 600
    assert record["verdict"] == "NO ANSWER", text
    # How many it reads first is the machine's speed: fewer than all, and said.
    said = r"it stopped itself before its 1 s limit, having read (\d+) of the 24 pairs"
    read = re.search(said, text)
    assert read and 1 <= int(read.group(1)) < 24, text
    assert "--plan says the split and its time" in text


# A flat washer seated on a plate of B-spline faces, as a CAD export gives them: OCCT's default
# volumes, off by about 1e-5 of the plate, read its cut under nothing (mqucifer/print-shop#354).

SEATED = """
import cadquery as cq
from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert


def nurbs(shape):
    return cq.Shape.cast(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())


plate = (
    cq.Workplane("XY").box(120, 120, 6, centered=(True, True, False))
    .faces(">Z").workplane().pushPoints([(10, 0)]).hole(3.2).val()
)
washer = cq.Workplane("XY").circle(3.5).circle(1.6).extrude(0.5).translate((10, 0, 6)).val()
cube = cq.Workplane("XY").box(1, 1, 1).translate((-20, 0, 6)).val()
cq.exporters.export(nurbs(plate), "plate.step")
cq.exporters.export(washer, "washer.step")
cq.exporters.export(cube, "cube.step")
"""


def seated_entry(name: str, file: str) -> dict[str, Any]:
    return {"name": name, "file": file, "layers": "z", "turn": [["z", 37.3]],
            "at": [12.345678, -7.654321, 3.0]}  # fmt: skip


@pytest.fixture(scope="module")
def seated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("seated")
    (found / "parts.py").write_text(SEATED, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    entries = [seated_entry(n, f"{n}.step") for n in ("plate", "washer", "cube")]
    (found / "seated.json").write_text(json.dumps({"parts": entries}), encoding="utf-8")
    return found


@pytest.mark.bench
def test_a_washer_seated_on_a_b_spline_plate_touches_and_a_buried_cube_still_interferes(
    seated: Path,
) -> None:
    def ask_seated(*files: str) -> tuple[str, dict[str, Any]]:
        done = host.run(["clearance", *files, "--placed", "seated.json"], seated)
        saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert saved, f"nothing saved: {done.stdout}{done.stderr}"
        return done.stdout, json.loads((seated / f"{saved.group(1)}.json").read_text())

    text, record = ask_seated("plate.step", "washer.step")
    assert record["verdict"] == "TOUCHING", text
    [pair] = record["numbers"]["pairs"]
    assert pair["overlap_occt_cut_mm3"] == pytest.approx(0.0, abs=1e-6), pair
    assert pair["overlap_occt_common_mm3"] == pytest.approx(0.0, abs=1e-6)
    assert pair["gap_occt_mm"] == pytest.approx(0.0, abs=1e-9)
    assert abs(pair["overlap_mesh_mm3"]) <= pair["overlap_band_mm3"]
    # Half of a 1 mm cube in the plate: every method finds the 0.5 mm³, the cut with the rest.
    text, record = ask_seated("plate.step", "cube.step")
    assert record["verdict"] == "INTERFERE", text
    [pair] = record["numbers"]["pairs"]
    assert pair["overlap_occt_cut_mm3"] == pytest.approx(0.5, abs=1e-5)
    assert pair["overlap_occt_common_mm3"] == pytest.approx(pair["overlap_occt_cut_mm3"], abs=1e-6)


def test_a_reading_with_nothing_left_to_read_is_never_stopped() -> None:
    """A whole reading done inside its last 20 s was thrown away for its margin."""
    from print_shop.bench import pace

    assert pace.Pace(600, None).after(elapsed=585, done=24, spent=500, left=0) is None
    assert pace.Pace(600, None).after(elapsed=585, done=23, spent=480, left=1) == "stop"


# Swept splines, whose meshes BRepMesh lays farther off the surface than it's asked
# (mqucifer/print-shop#354). Made here: a tube 4 mm round swept along a spline in xz, and a copy
# moved along y, so the two are as near as the move less 8 mm, along their length.

SWEPT = """
import cadquery as cq

path = cq.Workplane("XZ").spline([(0, 0), (10, 15), (-5, 30), (8, 45)], includeCurrent=False)
tube = cq.Workplane("XY").circle(4).sweep(path)
cq.exporters.export(tube, "sweep.step")
cq.exporters.export(tube.translate((0, 9, 0)), "beside.step")
cq.exporters.export(cq.Workplane("XY").box(5, 5, 5).translate((80, 0, 0)), "far1.step")
cq.exporters.export(cq.Workplane("XY").box(5, 5, 5).translate((-80, 0, 0)), "far2.step")
"""

#: What a script in the image reads of the bench's own code: which mesh the mesh bound reads,
#: the pad it gives a body, what that mesh's triangulation says it's off the surface, and the
#: threads OCCT's distance runs on after a reading, against the container's CPUs.
SWEPT_READ = """
import json, sys
sys.path.insert(0, "/opt/bench")
from OCP.BRep import BRep_Tool
from OCP.OSD import OSD_ThreadPool
from OCP.TopLoc import TopLoc_Location
from bench import clearance, shapes
from bench.cli import main

a, b = shapes.load("sweep.step"), shapes.load("beside.step")
asked = []
plain = shapes.Part.manifold
def spy(self, fine=True):
    asked.append(fine)
    return plain(self, fine)
shapes.Part.manifold = spy
clearance.by_meshes(a, b, 0.1, 0.5)
shapes.Part.manifold = plain
pad = clearance.bounds_of(a).pad
shapes._tessellated(a.shape, shapes.FINE if asked[0] else shapes.COARSE)
recorded = max(
    BRep_Tool.Triangulation_s(f.wrapped, TopLoc_Location()).Deflection() for f in a.shape.Faces()
)
try:
    main(["--out", "probe", "clearance", "sweep.step", "beside.step"])
except SystemExit:
    pass
quota, period = open("/sys/fs/cgroup/cpu.max").read().split()[:2]
print("READ " + json.dumps({
    "fine": asked[0], "pad": pad, "recorded": recorded,
    "threads": OSD_ThreadPool.DefaultPool_s().NbThreads(), "cpus": -(-int(quota) // int(period)),
}))
"""


@pytest.fixture(scope="module")
def swept(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("swept")
    (found / "parts.py").write_text(SWEPT, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    return found


@pytest.mark.bench
def test_a_bodys_pad_covers_what_the_mesh_its_bound_reads_says_it_is_off(swept: Path) -> None:
    """The mesh bound read the coarse mesh, padded 0.05 mm, where a swept spline's coarse
    triangulation says it's 0.05 mm off: twice that is the pad it needs. And OCCT's pool ran 8
    threads in a 2-CPU container."""
    (swept / "read.py").write_text(SWEPT_READ, encoding="utf-8")
    done = host.run(["read.py"], swept, python=True)
    line = next((x for x in done.stdout.splitlines() if x.startswith("READ ")), None)
    assert line, done.stdout + done.stderr
    read = json.loads(line[len("READ "):])
    assert read["pad"] >= 2 * read["recorded"], read
    assert read["fine"], "the fine mesh, which the exact reads use too"
    assert read["threads"] == read["cpus"], read


@pytest.mark.bench
def test_two_swept_splines_just_inside_a_gap_judged_are_read_exactly(swept: Path) -> None:
    files = ("sweep.step", "beside.step", "far1.step", "far2.step")

    def ask_swept(*args: str) -> tuple[str, dict[str, Any]]:
        done = host.run(["clearance", *files, *args], swept)
        saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert saved, f"nothing saved: {done.stdout}{done.stderr}"
        return done.stdout, json.loads((swept / f"{saved.group(1)}.json").read_text())

    _, exact = ask_swept("--exact")
    gap = pair_of(exact, "sweep.step", "beside.step")["gap_mm"]
    assert 0.9 < gap < 1.1, gap
    # Judged a hair past the true gap, less than the coarse mesh is off: too close, read exactly.
    text, record = ask_swept("--min-gap", f"{gap + 0.005:.4f}")
    near = pair_of(record, "sweep.step", "beside.step")
    assert near["verdict"] == "TOO CLOSE" and "read_by" not in near, text
    assert record["verdict"] == "TOO CLOSE"
    # Judged a hair short of it: clear, and never by a bound past the true gap.
    text, record = ask_swept("--min-gap", f"{gap - 0.005:.4f}")
    near = pair_of(record, "sweep.step", "beside.step")
    assert near["verdict"] == "CLEAR", text
    assert near.get("bound_gap_mm", 0.0) <= gap


@pytest.mark.bench
def test_a_plans_timed_reads_are_held_to_its_limit(work: Path) -> None:
    text, record, waited = under_limit(work, 1, None, "--plan")
    assert record["verdict"] == "NO ANSWER", text
    sampled = record["numbers"]["plan"]["sampled"]
    assert 1 <= sampled < 3, record["numbers"]["plan"]
    assert f"the plan stopped timing after {sampled} exact read(s), before its 1 s limit" in text


def test_a_readings_clock_is_its_process_age_as_the_kernel_says(tmp_path: Path) -> None:
    """The host's limit runs from the container's start; a reading's own clock started only once
    its modules were imported, seconds later. A made-up /proc: started 1,000 ticks after boot,
    up 25 s."""
    from print_shop.bench import progress

    ticks = os.sysconf("SC_CLK_TCK")
    fields = ["S", *["0"] * 18, str(10 * ticks)]
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "stat").write_text(f"42 (python (bench)) {' '.join(fields)} 0 0\n")
    (tmp_path / "uptime").write_text("25.00 40.00\n")
    assert progress.since(tmp_path) == pytest.approx(15.0)
    assert progress.since(tmp_path / "none") >= 0.0, "since the module was imported, where not"
