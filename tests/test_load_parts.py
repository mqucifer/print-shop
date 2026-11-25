"""`load` in two passes outside the images (mqucifer/print-shop#301): the arithmetic that
carries what the whole finds on each part over to the part on its own, how a part held by
nothing is pinned, the two meshes' agreement, the movement taken on to a finer mesh, a strain
held its band below its limit, and the container's clock. No Docker, and no model: the solves
themselves are in `test_cae.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import host, load, load_parts
from print_shop.bench.report import NoAnswer, Report


def test_a_place_moves_no_less_than_either_mesh_and_where_they_point() -> None:
    """A second-order mesh reads a part stiffer the coarser it is, so a place's movement taken
    on from two meshes is no less than either: here further than the finer, by the gap over
    1.5² - 1."""
    assert load_parts.finer(0.128, 0.127) == pytest.approx(0.128 + 0.001 / 1.25)
    # Where the coarser moves further, it's the coarser's.
    assert load_parts.finer(0.128, 0.130) == 0.130
    # Along the way it moves, whichever sign that is.
    assert load_parts.finer(-0.128, -0.127) == pytest.approx(-(0.128 + 0.001 / 1.25))


def _ring(count: int = 40) -> tuple[np.ndarray, np.ndarray]:
    turn = np.linspace(0, 2 * np.pi, count, endpoint=False)
    places = np.stack([10 * np.cos(turn), 5 * np.sin(turn), np.full(count, 3.0)], axis=1)
    return places, np.linspace(1.0, 2.0, count)


def test_what_a_part_is_given_adds_up_to_the_first_pass_s_force_and_moment_exactly() -> None:
    places, weights = _ring()
    rng = np.random.default_rng(301)
    pushes = rng.normal(size=places.shape)
    force, moment = np.array([1.0, -2.0, 30.0]), np.array([40.0, 5.0, -6.0])
    evened = load_parts.even_out(places, weights, pushes, force, moment)
    assert evened.sum(axis=0) == pytest.approx(force, abs=1e-9)
    assert np.cross(places, evened).sum(axis=0) == pytest.approx(moment, abs=1e-9)
    # What already adds up is left as it is.
    again = load_parts.even_out(
        places, weights, evened, evened.sum(axis=0), np.cross(places, evened).sum(axis=0)
    )
    assert again == pytest.approx(evened, abs=1e-9)


def test_a_part_held_by_nothing_is_pinned_against_moving_as_a_body_and_nothing_more() -> None:
    """Three nodes, six axes held between them: they stop each of a body's six ways of moving
    as a whole, and no more, so they carry nothing a balanced load doesn't need."""
    rng = np.random.default_rng(7)
    points = rng.uniform(-50, 50, size=(200, 3)) * np.array([1.0, 0.3, 0.1])
    held = load_parts.pins(points)
    assert len({row for row, _ in held}) == 3
    assert sum(len(axes) for _, axes in held) == 6
    # Each of the six rigid motions, at the held axes: none is left free.
    motions = []
    for k in range(3):
        move = np.zeros((len(points), 3))
        move[:, k] = 1
        motions.append(move)
        turn = np.zeros(3)
        turn[k] = 1
        motions.append(np.cross(turn, points - points.mean(axis=0)))
    seen = np.array([[m[row, a] for row, axes in held for a in axes] for m in motions])
    assert np.linalg.matrix_rank(seen) == 6


def test_two_meshes_of_the_whole_disagree_on_a_joint_only_past_the_share_they_re_held_to() -> None:
    fine = {"a+b": {"force": [100.0, 0, 0]}, "a+c": {"force": [0.0, 0.01, 0]}}
    near = {"a+b": {"force": [104.0, 0, 0]}, "a+c": {"force": [0.0, 0.02, 0]}}
    far = {"a+b": {"force": [94.0, 0, 0]}, "a+c": {"force": [0.0, 0.5, 0]}}
    # 4% apart, and a joint carrying next to nothing is held to a share of the load.
    assert load_parts.disagree(fine, near, 200.0) == []
    found = load_parts.disagree(fine, far, 200.0)
    assert [one[0] for one in found] == ["a+b", "a+c"]
    assert found[0][3] == pytest.approx(6.0)


def test_a_square_bore_s_sides_are_set_by_its_axis_line_alone() -> None:
    for axis in (np.array([0.0, 0, 1]), np.array([1.0, 2, 3]), np.array([1.0, 0, 0])):
        across, up = load_parts.square_axes(axis)
        assert across @ axis == pytest.approx(0) and up @ axis == pytest.approx(0)
        assert across @ up == pytest.approx(0)
        back = load_parts.square_axes(-axis)
        assert back[0] == pytest.approx(across) and back[1] == pytest.approx(up)


def test_each_triangle_takes_the_push_of_the_nearest_of_the_first_pass_s() -> None:
    centres = np.array([[0.0, 0, 0], [10, 0, 0], [0, 10, 0]])
    points = np.array([[1.0, 1, 0], [9, -1, 0], [2, 8, 0], [4, 6, 0]])
    assert load_parts.nearest(centres, points, rows=2).tolist() == [0, 1, 2, 2]


def test_two_passes_stop_before_a_step_that_wouldn_t_end_inside_the_container_s_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(load_parts.TIMEOUT, raising=False)
    clock = load_parts.Clock(0.0)
    clock.time("part", 1e9)
    clock.check("part", "anything")  # with no limit given, nothing stops
    monkeypatch.setenv(load_parts.TIMEOUT, "600")
    import time

    clock = load_parts.Clock(time.time() - 500)
    clock.done.append("the first pass read the whole in 60 s")
    clock.time("bolt", 2.0)
    clock.check("bolt", "a bolt")  # 80 s left, before the margin, and a bolt takes 2
    clock.time("part 10.459", 90.0)
    with pytest.raises(NoAnswer) as stopped:
        clock.check("part 10.459", "the second pass's part tile0 at 10.459 mm")
    said = str(stopped.value)
    assert said.startswith("two passes met the container's time, 600 s, after 500 s, before "
                           "the second pass's part tile0 at 10.459 mm")  # fmt: skip
    assert "the first pass read the whole in 60 s" in said


def test_a_correction_is_put_on_in_place_of_the_load() -> None:
    text = "*STEP\n*STATIC\n*CLOAD\n1,1,5\n2,3,-1\n*NODE PRINT, NSET=HELDALL\nRF\n*END STEP\n"
    left = np.array([[0.5, 0, 0], [0, 0, 0], [0, -0.25, 1e-3]])
    again = load._cload(text, left, {7: 0, 8: 1, 9: 2})
    assert again == (
        "*STEP\n*STATIC\n*CLOAD\n7,1,0.5\n9,2,-0.25\n9,3,0.001\n*NODE PRINT, NSET=HELDALL\nRF\n"
        "*END STEP\n"
    )


def _part(across: float, stretch: float = 0.1) -> dict[str, Any]:
    return {
        "material": "petg", "layers": [0.0, 0, 1], "across_layers_pct": across,
        "across_at": [0.0, 0, 0], "across_limit_pct": 1.0, "stretch_pct": stretch,
        "stretch_is": "pull", "stretch_at": [0.0, 0, 0], "stretch_from_layers_deg": 0.0,
        "stretch_limit_pct": 5.1, "nodes": 10,
    }  # fmt: skip


def test_a_strain_within_two_passes_band_of_its_limit_is_either_side_of_it() -> None:
    """Two passes read a part's worst strain within 2% of one pass on the stool, some above
    and some below: so in two passes a strain within `BAND` below its limit can't be called
    under it."""
    for band, across, apart_said in (
        (0.0, 0.97, False),
        (load_parts.BAND, 0.97, True),
        # Read exactly the band low, it's still either side (the operator's cold review).
        (load_parts.BAND, 0.95, True),
        (load_parts.BAND, 0.94, False),
    ):
        report = Report("load", [])
        fails: list[str] = []
        apart: list[str] = []
        load._parts(
            report, {"parts": {"top": _part(across)}}, {"parts": {"top": _part(across)}},
            fails, apart, band,
        )  # fmt: skip
        assert bool(apart) is apart_said, (band, across, apart)
        assert not fails
    assert "within the 5% two passes are held to of its 1% limit" in apart[0] if apart else True


def test_the_container_is_told_its_own_time_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[list[str]] = []

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(subprocess, "run", run)
    host.run(["load", "a.json"], tmp_path)
    host.run(["load", "a.json"], tmp_path, timeout=3600)
    docker = [argv for argv in ran if argv[0] == "docker"]
    assert f"BENCH_TIMEOUT={host.TIMEOUT}" in docker[0]
    assert "BENCH_TIMEOUT=3600" in docker[1]


def test_a_step_longer_than_any_yet_is_stopped_at_the_limit_saying_where(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first of a kind has no longest to go by: the clock rings at the limit less its
    margin, in whatever step it's in, and the reply says which."""
    import time

    monkeypatch.setenv(load_parts.TIMEOUT, "600")
    clock = load_parts.Clock(time.time() - (600 - load_parts.MARGIN - 1))
    clock.now = "the second pass's part tile0 at 10.459 mm"
    clock.start()
    try:
        with pytest.raises(load_parts.OutOfTime) as stopped:
            time.sleep(5)
    finally:
        clock.stop()
    assert "during the second pass's part tile0 at 10.459 mm" in str(stopped.value)


def test_a_reading_exactly_the_band_below_its_limit_is_near_it() -> None:
    """Read `band` low, a figure would be at its limit: so it's near it, and one a hair further
    down isn't. `value * (1 + band)` missed it by up to `band` squared."""
    assert load_parts.near(0.95, 1.0, 0.05)
    assert load_parts.near(0.999, 1.0, 0.05)
    assert not load_parts.near(0.9499, 1.0, 0.05)
    assert not load_parts.near(1.01, 1.0, 0.05), "over is a failure, not near"


def test_a_joint_s_nearest_figure_is_its_share_of_its_limit() -> None:
    assert load_parts.joint_share({"nearest_share": 0.97}) == 0.97
    assert load_parts.joint_share({"bolt_pull_n": 95.0, "holds_n": 100.0}) == 0.95
    assert load_parts.joint_share({"kind": "touching"}) is None


def test_only_a_hole_about_a_bolt_s_width_is_stood_in_by_a_square() -> None:
    """An M5's 5.5 mm clearance bore and a 7 mm insert's hole are; a boss or collar round the
    bolt (its outside away from the axis) isn't, nor a 10 mm counterbore, nor a hole narrower
    than the bolt: they're left round, as one pass meshes them."""
    assert load_parts.stood_in(2.75, True, 5.0)
    assert load_parts.stood_in(3.5, True, 5.0)
    assert not load_parts.stood_in(2.75, False, 5.0), "a boss is the part, not a hole"
    assert not load_parts.stood_in(5.0, True, 5.0), "a counterbore wider than the bore"
    assert not load_parts.stood_in(2.0, True, 5.0)


def test_what_s_moved_to_add_up_is_a_share_of_what_was_put_on() -> None:
    before = np.array([[1.0, 0, 0], [0, 2.0, 0]])
    assert load_parts.moved_share(before, before) == 0.0
    assert load_parts.moved_share(before, before + [[0.3, 0, 0], [0, 0, 0]]) == pytest.approx(0.1)
    assert load_parts.moved_share(np.zeros((2, 3)), before) == float("inf")
    assert load_parts.EVENED == 0.20


def test_a_part_that_meets_others_but_was_given_nothing_by_them_isn_t_read() -> None:
    """Its held faces' reaction would balance whatever was put on it, so the balance can't
    catch a part whose neighbours' forces are missing: it's refused (the operator's cold
    review)."""
    import argparse

    from print_shop.bench.assembly import Assembly, Material, PartAt

    petg = Material("petg", 1500.0, 0.38, 1.0, 5.1, "made up")
    part = PartAt("leg", "leg.step", (0.0, 0.0, 0.0), (), petg, "z")
    args = argparse.Namespace(toward="-z", lift=None)
    alone = load_parts._Alone(
        Assembly("a.json", (part,)), part, None, [object()], [], None, [], args, 5.0
    )
    with pytest.raises(NoAnswer, match="the first pass gave it nothing they put on it"):
        alone._joined()
