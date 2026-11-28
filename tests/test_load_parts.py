"""`load` in two passes outside the images (mqucifer/print-shop#301): the arithmetic that
carries what the whole finds on each part over to the part on its own, how a part held by
nothing is pinned, the two meshes' agreement, the movement taken on to a finer mesh, a strain
held its band below its limit, and the container's clock. No Docker, and no model: the solves
themselves are in `test_cae.py`.
"""

from __future__ import annotations

import re
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


def _ran_under(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str], **more: Any
) -> dict[str, str]:
    """The environment `host.run` gives a bench command's container, read off its `docker run`,
    and the limit it was waited for under; nothing is run."""
    waited: list[tuple[int, int | None]] = []

    def wait(argv: list[str], timeout: int, longer: int | None) -> tuple[Any, int, bool]:
        waited.append((timeout, longer))
        return subprocess.CompletedProcess(argv, 0, "", ""), timeout, False

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(host, "_waited", wait)
    done = host.run(args, tmp_path, **more)
    env = dict(w.split("=", 1) for a, w in zip(done.args, done.args[1:], strict=False) if a == "-e")
    (timeout, longer), = waited
    assert env["BENCH_TIMEOUT"] == str(timeout), "the container is told the limit it's waited for"
    assert env.get(host.OFFER) == (None if longer is None else str(longer))
    return env


def test_a_load_in_two_passes_is_given_the_hour_and_one_pass_keeps_its_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mqucifer/print-shop#325, the Sponsor's "Yes an hour": `--parts` is given 3600 s from the
    start. A plain `load` runs under 600 s, offered the hour for two passes, which it takes up
    only where the whole meets the container's memory. Every other command, a script, and a
    limit the caller gives keep theirs, with nothing offered."""
    assert host.TWO_PASSES == 3600 and host.TIMEOUT == 600
    parts = _ran_under(monkeypatch, tmp_path, ["load", "a.json", "--weight", "1", "--parts"])
    assert parts["BENCH_TIMEOUT"] == "3600" and host.OFFER not in parts
    out = _ran_under(monkeypatch, tmp_path, ["--out", "o", "load", "a.json", "--parts"])
    assert out["BENCH_TIMEOUT"] == "3600"
    one = _ran_under(monkeypatch, tmp_path, ["load", "a.json", "--weight", "1"])
    assert one["BENCH_TIMEOUT"] == "600" and one[host.OFFER] == "3600"
    for args in (["optimise", "p.step"], ["bend", "c.step"], ["info", "parts.step"]):
        other = _ran_under(monkeypatch, tmp_path, args)
        assert other["BENCH_TIMEOUT"] == str(host.TIMEOUTS.get(args[0], host.TIMEOUT)), args
        assert host.OFFER not in other, args
    # `--parts` as a word of another command isn't `load`'s: `clearance --parts` isn't given the
    # hour from the start. It's offered it, as a whole assembly's may take it up
    # (mqucifer/print-shop#354).
    other = _ran_under(monkeypatch, tmp_path, ["clearance", "--parts"])
    assert other["BENCH_TIMEOUT"] == "600" and other[host.OFFER] == "3600"
    script = _ran_under(monkeypatch, tmp_path, ["load", "a.json"], python=True)
    assert script["BENCH_TIMEOUT"] == "600" and host.OFFER not in script
    given = _ran_under(monkeypatch, tmp_path, ["load", "a.json"], timeout=600)
    assert given["BENCH_TIMEOUT"] == "600" and host.OFFER not in given


def _child(code: str) -> list[str]:
    import sys

    return [sys.executable, "-c", code]


def test_the_host_waits_the_hour_only_once_two_passes_take_it_up() -> None:
    """A command offered a longer limit is stopped at its own unless it says it took it up, on
    its stderr; then it's waited for under the longer one, and that line isn't passed on. Real
    processes, with limits of seconds, not the bench's."""
    taken = f"import sys, time; print({load_parts.TAKEN!r}, file=sys.stderr, flush=True); "
    late = "time.sleep(3); print('read'); print('said', file=sys.stderr)"
    done, limit, could = host._waited(_child(taken + late), 1, 30)
    assert done is not None and limit == 30 and not could
    assert done.returncode == 0 and done.stdout == "read\n" and done.stderr == "said\n"
    stopped, limit, could = host._waited(_child("import time; " + late), 1, 30)
    assert stopped is None and limit == 1, "not taken up, it's stopped at its own"
    assert not could, "it didn't say two passes could read it"
    stopped, limit, could = host._waited(_child(taken + "time.sleep(30)"), 1, 2)
    assert stopped is None and limit == 2, "taken up, it's stopped at the longer"
    assert not could


def test_a_command_past_the_limit_it_took_up_says_that_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    killed: list[list[str]] = []
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(host, "_waited", lambda argv, timeout, longer: (None, longer, False))
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: killed.append(argv))
    done = host.run(["load", "a.json"], tmp_path)
    assert done.stdout == "verdict: NO ANSWER: it ran past the 3600 s limit and was stopped\n"
    assert killed[0][:2] == ["docker", "kill"]


def _box(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, child: str, timeout: int = 600
) -> tuple[Any, list[dict[str, str]]]:
    """A design round's `Box`, its `docker run` stood in by `child`, a real Python process on
    this machine, so its own wait, its reading of the output and its stopping are what run; and
    each container's environment as `host.container` was given it."""
    import sys

    from print_shop.rounds import box

    told: list[dict[str, str]] = []

    def container(work: Path, command: list[str], name: str, **more: Any) -> list[str]:
        told.append(dict(more.get("env") or {}))
        return [sys.executable, "-c", child]

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "container", container)
    # `docker kill`: the child ends of itself soon after its limit.
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: None)
    return box.Box(tmp_path, timeout), told


def test_a_design_round_s_load_in_two_passes_is_given_the_hour_as_host_run_gives_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mqucifer/print-shop#325, the operator: a design round's bench calls go through
    `rounds/box.py`, and the side table's load cases are read there. Each container is told its
    limit; `load --parts` is given the hour, and a plain `load` the run's own limit and the
    hour offered; every other command, a script, and a limit the caller gives, nothing more."""
    box, told = _box(monkeypatch, tmp_path, "pass", timeout=300)
    box.bench(["load", "a.json", "--weight", "1", "--parts"])
    box.bench(["load", "a.json", "--weight", "1"])
    box.bench(["optimise", "a.json"])
    box.bench(["info", "a.step"])
    box.python("make.py")
    box.bench(["load", "a.json"], timeout=600)
    limits = [(one["BENCH_TIMEOUT"], one.get(host.OFFER)) for one in told]
    assert limits == [
        ("3600", None), ("300", "3600"), ("300", None), ("300", None), ("300", None),
        ("600", None),
    ]  # fmt: skip


def test_a_design_round_waits_the_hour_only_once_two_passes_take_it_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness's own wait for the call: past the run's limit only where the command's
    output says two passes took the hour up, a line the model isn't shown; and stopped at the
    hour where it runs past that. Limits of seconds here, not the bench's."""
    taken = f"import sys, time; print('a'); print({host.TAKEN!r}, flush=True); "
    box, _ = _box(monkeypatch, tmp_path, taken + "time.sleep(3); print('read')", timeout=1)
    done = box.bench(["load", "a.json"])
    assert not done.timed_out and done.code == 0, done.output
    assert done.output == "a\nread\n", "the line that took it up isn't the answer's"
    box, _ = _box(monkeypatch, tmp_path, "import time; time.sleep(3)", timeout=1)
    stopped = box.bench(["load", "a.json"])
    assert stopped.timed_out and "ran past the 1 s limit" in stopped.output
    # A script, which isn't offered the hour, can't take it up by saying so.
    box, _ = _box(monkeypatch, tmp_path, taken + "time.sleep(3)", timeout=1)
    assert box.python("make.py").timed_out
    monkeypatch.setattr(host, "offered", lambda args: 2)
    box, _ = _box(monkeypatch, tmp_path, taken + "time.sleep(5)", timeout=1)
    late = box.bench(["load", "a.json"])
    assert late.timed_out and "ran past the 2 s limit" in late.output


def test_two_passes_take_up_the_hour_offered_and_the_clock_and_the_ran_line_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """In the container: two passes take up the host's offer, saying so on stderr, and from
    there the container's limit is the hour, for the clock (so the glass band's reserve comes
    out of the hour) and for the reply's `ran:` line. Not offered, nothing changes."""
    mirrored = (load_parts.OFFER, load_parts.TAKEN, load_parts.ABLE)
    assert mirrored == (host.OFFER, host.TAKEN, host.ABLE)
    monkeypatch.setenv(load_parts.TIMEOUT, "600")
    monkeypatch.delenv(load_parts.OFFER, raising=False)
    load_parts.take_longer()
    assert capsys.readouterr().err == ""
    assert load_parts.Clock(0.0).limit == 600
    monkeypatch.setenv(load_parts.OFFER, "3600")
    load_parts.take_longer()
    assert capsys.readouterr().err == host.TAKEN + "\n"
    assert load_parts.OFFER not in __import__("os").environ, "taken up once"
    import time

    clock = load_parts.Clock(time.time(), reserve=100.0)
    assert clock.limit == 3600
    left = clock.left()
    assert left is not None and left == pytest.approx(3600 - load_parts.MARGIN - 100.0, abs=1.0)
    report = Report("load", ["a.json"])
    report.verdict("NO ANSWER", "made up")
    report.save(tmp_path)
    assert "\nran: " in report.text and " s of its 3600 s limit, image " in report.text
    monkeypatch.delenv(load_parts.TIMEOUT)
    report.save(tmp_path)
    assert " limit" not in report.text.split("\nran: ")[1].split(", image")[0]


# mqucifer/print-shop#331: a one pass that fits the memory but not its time


def _assembly(tmp_path: Path, parts: int = 2) -> str:
    """A made-up assembly file of `parts` blocks, bolted where there are two."""
    import json

    data: dict[str, Any] = {
        "parts": [
            {"name": f"p{k}", "file": f"p{k}.step", "at": [40 * k, 0, 0], "layers": "z"}
            for k in range(parts)
        ],
        "joints": [
            {"between": ["p0", "p1"], "kind": "bolted", "bolt": "m4-8.8",
             "insert": "m4-heat-set", "at": [[40, 10, 5]]}
        ] if parts > 1 else [],
        "held": [{"plane": "x=0", "how": "fixed"}],
    }  # fmt: skip
    for k in range(parts):
        (tmp_path / f"p{k}.step").write_text("")
    path = tmp_path / f"assembly-{parts}.json"
    path.write_text(json.dumps(data))
    return str(path)


def _plain_load(monkeypatch: pytest.MonkeyPatch, limit: str = "600") -> None:
    """The container as the host starts a plain `load`: its limit, and the hour offered."""
    monkeypatch.setenv(load_parts.TIMEOUT, limit)
    monkeypatch.setenv(load_parts.OFFER, str(host.TWO_PASSES))
    monkeypatch.setattr(load, "_clock", {"now": None})


def _args(path: str, parts: bool = False) -> Any:
    import argparse

    return argparse.Namespace(assembly=path, parts=parts, lift=None)


def _ccx_binary(tmp_path: Path, seconds: float) -> Any:
    """What `cae.ccx_one_thread` runs CalculiX from, a real process that takes `seconds`."""
    ccx = tmp_path / "ccx"
    ccx.write_text(f"#!/bin/sh\nsleep {seconds}\n")
    ccx.chmod(0o755)
    folder = tmp_path / "solve"
    folder.mkdir(exist_ok=True)

    class Fea:
        ccx_binary = str(ccx)
        inp_file_name = str(folder / "solve.inp")
        working_dir = str(folder)

        def setup_ccx(self) -> None:
            pass

    return Fea()


def _stood_in(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    one_pass: Any,
) -> None:
    """`load._run` stood in for where it needs FreeCAD: it starts the one pass's clock by the real
    gate (`load._one_pass`, given what `_run` is given), then `one_pass(clock)` does the one
    pass's steps through the real clock; in two passes, it says what it was given."""
    from print_shop.bench.assembly import read

    def run(
        args: Any, report: Report, without_bands: bool = False, two_pass: str | None = None,
        reserve: float = 0.0, tried: float = 0.0,
    ) -> None:  # fmt: skip
        limit = load_parts.Clock(0).limit
        calls.append({"two_pass": two_pass, "tried": tried, "limit": limit})
        load._one_pass(read(args.assembly), report.started, two_pass, without_bands)
        if two_pass is None:
            one_pass(load._on_one_pass())
            report.verdict("OK", "read in one pass")
            return
        assert load._on_one_pass() is None, "two passes don't run on the one pass's clock"
        report.numbers["two_passes"] = {"why": two_pass}
        report.verdict("OK", "read in two passes")

    monkeypatch.setattr(load, "_run", run)


def _bolts(bolt: float) -> Any:
    """The one pass's whole read in 30 s, then a bolt's reading that took `bolt` s, then the
    next asked for, as `_through` asks."""

    def steps(clock: Any) -> None:
        assert clock is not None
        clock.whole_began = clock.ccx_began = clock.ccx_ended = __import__("time").time() - 30
        clock.whole_read()
        load._in_time("bolt", "the reading round p0+p1's first bolt")
        load._timed("bolt", bolt)
        load._in_time("bolt", "the reading round p0+p1's second bolt")

    return steps


def test_a_one_pass_that_fits_the_memory_but_not_its_time_goes_to_two_passes_under_the_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The furniture assembly whose one pass ran past 600 s, round after round, without meeting
    the memory. Here its next bolt wouldn't end inside the 600 s, so the one pass stops itself
    before it and goes on to two passes, without being asked: they take up the hour, counted
    from the command's start, and the reply says why."""
    import time

    _plain_load(monkeypatch)
    calls: list[dict[str, Any]] = []
    _stood_in(monkeypatch, calls, _bolts(1e4))
    report = Report("load", ["assembly.json"])
    report.started = time.time() - 100
    load.run(_args(_assembly(tmp_path)), report)
    assert [c["two_pass"] is None for c in calls] == [True, False]
    why = calls[1]["two_pass"]
    assert why.startswith("the one pass wouldn't end inside the container's 600 s: it had run ")
    assert why.endswith(" s, before the reading round p0+p1's second bolt, which would end past it")
    assert calls[1]["limit"] == host.TWO_PASSES, "the hour, taken up"
    assert calls[1]["tried"] == pytest.approx(30.0, abs=1), "what the one pass's whole took"
    assert capsys.readouterr().err == f"{host.ABLE}\n{host.TAKEN}\n", "each said once"
    assert report.numbers["two_passes"]["one_pass_late"] == why
    assert report.started == pytest.approx(time.time() - 100, abs=5), "the hour counts the try"
    assert load._clock["now"] is None


def test_a_one_pass_stopped_in_calculix_s_solve_goes_to_two_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Where the one pass's solve of the whole runs to the container's time, less its margin,
    `load._ccx` stops CalculiX there, a real process standing in for it, and `_read` goes on to
    two passes. The hour is taken up before the solve's files are tidied, and two passes' clock
    is given what the whole had taken when it was stopped."""
    import os
    import time

    from print_shop.bench import cae

    _plain_load(monkeypatch, limit=f"{load.ONE_PASS_MARGIN + 1:g}")
    tidied: list[bool] = []

    def tidy(fea: Any) -> None:
        tidied.append(load_parts.OFFER not in os.environ)
        cae.tidy_solve(fea)

    monkeypatch.setattr(load, "tidy_solve", tidy)
    fea = _ccx_binary(tmp_path, 30)

    def solve(clock: Any) -> None:
        assert clock is not None and clock.left() <= 1.0
        clock.whole_began = time.time()
        load._ccx(fea, 5.662)
        pytest.fail("CalculiX wasn't stopped")

    calls: list[dict[str, Any]] = []
    _stood_in(monkeypatch, calls, solve)
    report = Report("load", ["assembly.json"])
    began = time.time()
    load.run(_args(_assembly(tmp_path)), report)
    assert time.time() - began < 10, "stopped at what's left, not left to run"
    assert [c["two_pass"] is None for c in calls] == [True, False]
    why = calls[1]["two_pass"]
    assert re.fullmatch(
        rf"the one pass wouldn't end inside the container's {load.ONE_PASS_MARGIN + 1:g} s: it "
        r"had run \d+ s, in CalculiX's solve of the whole at 5\.662 mm",
        why,
    ), why
    assert calls[1]["limit"] == host.TWO_PASSES
    assert tidied == [True], "the hour taken up first, then the solve's files tidied"
    assert not Path(fea.working_dir).exists()
    assert 0 <= calls[1]["tried"] < 10, "what the whole had taken when it was stopped"
    assert capsys.readouterr().err == f"{host.ABLE}\n{host.TAKEN}\n"


def test_a_solve_off_the_one_pass_s_clock_has_no_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two passes' own clock, or none, gives CalculiX no timeout, as on main; on the one pass's
    clock it's given what's left less what the whole's reading after its solve took."""
    import time

    from print_shop.bench import cae

    given: list[float | None] = []
    monkeypatch.setattr(load, "ccx_one_thread", lambda fea, timeout=None: given.append(timeout))
    monkeypatch.setattr(load, "_clock", {"now": None})
    load._ccx(object(), 4.0)
    load._clock["now"] = load_parts.Clock(time.time())
    load._ccx(object(), 4.0)
    clock = load._OnePass(time.time(), 600.0)
    clock.after = 25.0
    load._clock["now"] = clock
    load._ccx(object(), 4.0)
    assert given[:2] == [None, None]
    assert given[2] == pytest.approx(600 - load.ONE_PASS_MARGIN - 25.0, abs=1)
    assert clock.ccx_began is not None and clock.ccx_ended is not None
    # And `ccx_one_thread` holds to what it's given, a real process standing in for CalculiX.
    began = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        cae.ccx_one_thread(_ccx_binary(tmp_path, 30), timeout=0.5)
    assert time.time() - began < 10


def test_the_one_pass_s_coarser_mesh_is_asked_for_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's review: gmsh's meshing isn't stopped, so before the whole's coarser mesh
    the one pass asks whether what the mesh's whole took before its solve and after it, which the
    coarser's is no longer than, would end inside what's left. Its solve is then given what's
    left less the reading after it. A fine whole of 560 s, 20 s of it outside its solve: 13 s
    left after it stops the one pass; 38 s goes on to the coarser mesh."""
    import time

    monkeypatch.setattr(load, "_clock", {"now": None})
    now = time.time()
    for spent, stops in ((585.0, True), (560.0, False)):
        clock = load._OnePass(now - spent, 600.0)
        clock.whole_began, clock.ccx_began, clock.ccx_ended = now - 560, now - 550, now - 10
        clock.whole_read()
        assert (clock.before, clock.after) == pytest.approx((10.0, 10.0), abs=0.5)
        assert clock.longest["whole"] == pytest.approx(560.0, abs=0.5)
        left = 600 - load.ONE_PASS_MARGIN - spent
        if stops:
            with pytest.raises(load._Slow) as late:
                clock.check_coarser(8.493)
            assert str(late.value).endswith(
                "s, before the whole's coarser mesh, at 8.493 mm, whose meshing and reading "
                "would end past it, as the mesh's took 20 s"
            ), str(late.value)
            assert late.value.whole == pytest.approx(560.0, abs=0.5)
        else:
            clock.check_coarser(8.493)  # 38 s left, against 20
            assert left > clock.before + clock.after


def test_a_one_pass_near_its_limit_reads_in_one_pass_to_within_the_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's review: the margin is the hand-off's, measured, and no more
    (`load.ONE_PASS_MARGIN`). A next bolt that ends a second inside it is read in one pass, as on
    main; one that would end past it goes to two passes. So a one pass that ended at 590 s on
    main still does."""
    import time

    assert load.ONE_PASS_MARGIN == 2.0
    monkeypatch.setattr(load, "_clock", {"now": None})
    now = time.time()
    clock = load._OnePass(now - 580, 600.0)
    clock.time("bolt", 10.0)
    left = clock.left()
    assert left == pytest.approx(600 - 2 - 580, abs=0.5)
    clock.check("bolt", "a bolt that ends at 590 s")  # 18 s left, and a bolt takes 10
    clock.time("bolt", left + 1)
    with pytest.raises(load._Slow):
        clock.check("bolt", "a bolt that would end past 598 s")


def test_a_one_pass_that_reads_inside_its_time_reads_as_on_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its bolts each well inside what's left: read in one pass, never two, under 600 s, and
    the hour isn't taken up. Only the line that tells the host two passes could read it is
    said, and the host doesn't pass that on."""
    _plain_load(monkeypatch)
    calls: list[dict[str, Any]] = []
    _stood_in(monkeypatch, calls, _bolts(2.0))
    report = Report("load", ["assembly.json"])
    load.run(_args(_assembly(tmp_path)), report)
    assert [c["two_pass"] for c in calls] == [None]
    assert report.word == "OK" and "two_passes" not in report.numbers
    assert capsys.readouterr().err == f"{host.ABLE}\n"
    assert load_parts.Clock(0).limit == 600 and load_parts.OFFER in __import__("os").environ


@pytest.mark.parametrize(
    ("parts", "offered", "limit", "two_pass", "without_bands"),
    [
        (1, True, "600", None, False),
        (2, False, "600", None, False),
        (2, True, None, None, False),
        (2, True, "600", "asked for with --parts", False),
        (2, True, "600", None, True),
    ],
    ids=["a single part", "not offered the hour", "no limit given", "two passes",
         "a glass band's read"],
)  # fmt: skip
def test_a_load_that_isn_t_a_one_pass_that_could_go_to_two_has_no_clock_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    parts: int,
    offered: bool,
    limit: str | None,
    two_pass: str | None,
    without_bands: bool,
) -> None:
    """`load._one_pass`, the gate `_run` calls with what it's given: a single part, a script or
    a limit the caller gave (nothing offered), and a command with no limit keep theirs, never
    stopped early; two passes and a glass band's read keep their own clocks. None is told to the
    host as one two passes could read."""
    from print_shop.bench.assembly import read

    _plain_load(monkeypatch)
    if not offered:
        monkeypatch.delenv(load_parts.OFFER)
    if limit is None:
        monkeypatch.delenv(load_parts.TIMEOUT)
    load._one_pass(read(_assembly(tmp_path, parts)), 0.0, two_pass, without_bands)
    assert load._clock["now"] is None and capsys.readouterr().err == ""
    load._one_pass(read(_assembly(tmp_path, 2)), 0.0, None, False)
    assert (load._on_one_pass() is not None) == (offered and limit is not None)


def _contained(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    """`host.run`'s `docker run` stood in by a real Python process on this machine that runs
    `load._one_pass` on the assembly at `path`, in the environment the host gives the container,
    then a one pass that runs past any limit. So what's said, the host's wait and its reply are
    the real ones."""
    import json
    import sys

    def container(work: Path, command: list[str], name: str, **more: Any) -> list[str]:
        code = (
            "import os, sys, time\n"
            f"os.environ.update({json.dumps(more.get('env') or {})})\n"
            "from print_shop.bench import load\n"
            "from print_shop.bench.assembly import read\n"
            f"load._one_pass(read({path!r}), time.time(), None, False)\n"
            "time.sleep(60)\n"
        )
        return [sys.executable, "-c", code]

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(host, "container", container)
    real = subprocess.run

    def run(argv: list[str], **more: Any) -> Any:
        # `docker kill`: the host has stopped the process itself.
        return None if argv[:2] == ["docker", "kill"] else real(argv, **more)

    monkeypatch.setattr(subprocess, "run", run)


def test_a_single_part_past_its_limit_is_stopped_there_its_reply_as_now(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A single part can't go to two passes: its one pass has no clock, says nothing, and the
    host stops it at its limit with the reply it has always given. Seconds here, not 600."""
    _contained(monkeypatch, _assembly(tmp_path, 1))
    monkeypatch.setattr(host, "TIMEOUT", 8)
    done = host.run(["load", "a.json", "--weight", "1"], tmp_path)
    assert done.stdout == "verdict: NO ANSWER: it ran past the 8 s limit and was stopped\n"
    assert done.returncode == 2


def test_a_one_pass_the_host_stops_names_parts_where_two_passes_could_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Past what the one pass's clock foresees, as its first mesh running past the limit, the
    host stops it there. An assembly's one pass said first that two passes could read it, so
    the reply names `--parts` and the hour, and doesn't promise they will."""
    _contained(monkeypatch, _assembly(tmp_path, 2))
    monkeypatch.setattr(host, "TIMEOUT", 8)
    done = host.run(["load", "a.json", "--weight", "1"], tmp_path)
    assert done.stdout == (
        "verdict: NO ANSWER: it ran past the 8 s limit and was stopped, in one pass. Two passes "
        "may read this assembly, and are given the hour, 3600 s: run it again with --parts, "
        "which asks for them from the start\n"
    )
    assert host.ABLE not in done.stdout + done.stderr


def _late_taken(child: str, out: str = "sys.stderr", then: str = "None") -> str:
    """`child`, which says TAKEN on `out` when it's signalled, then does `then`: as two passes
    starting at the moment the host stops the one pass would."""
    return (
        "import signal, sys, time\n"
        f"signal.signal(signal.SIGUSR1, lambda *_: (print({host.TAKEN!r}, file={out}, "
        f"flush=True), {then}))\n" + child
    )


def test_a_taken_line_that_lands_as_the_host_stops_the_one_pass_doesn_t_change_its_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's review: the host reads whether it was a one pass that two passes could
    read at the moment it stops it, under its own limit. TAKEN, landing while it's being
    stopped, leaves the reply naming `--parts`. Real processes, with limits of seconds."""
    import os
    import signal
    import time

    popen = subprocess.Popen

    class Late(popen):  # type: ignore[misc, valid-type]
        def kill(self) -> None:
            os.kill(self.pid, signal.SIGUSR1)
            time.sleep(0.5)  # the line read by the host as it stops it
            super().kill()

    monkeypatch.setattr(subprocess, "Popen", Late)
    able = f"print({host.ABLE!r}, file=sys.stderr, flush=True); time.sleep(30)"
    stopped, limit, could = host._waited(_child(_late_taken(able)), 1, 30)
    assert stopped is None and limit == 1 and could


def test_a_design_round_s_one_pass_stopped_names_parts_where_two_passes_could_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness's own wait, where the issue was met: the model read the timeout as the
    bench's limit and waited for the hour. Now the reply it's shown names `--parts`, the line
    that said two passes could read it isn't in it, and TAKEN landing during `docker kill`
    doesn't change that."""
    import os
    import signal

    able = f"import time; print('a'); print({host.ABLE!r}, flush=True); "
    box, _ = _box(monkeypatch, tmp_path, able + "time.sleep(3)", timeout=1)
    stopped = box.bench(["load", "a.json"])
    assert stopped.timed_out
    assert stopped.output == "a\n\n" + host.past(1, True), stopped.output
    assert "run it again with --parts" in stopped.output
    box, _ = _box(monkeypatch, tmp_path, able + "print('read')", timeout=5)
    done = box.bench(["load", "a.json"])
    assert done.output == "a\nread\n", "the line is the harness's"
    taken = f"print({host.TAKEN!r}, flush=True); "
    box, _ = _box(monkeypatch, tmp_path, able + taken + "time.sleep(5)", timeout=1)
    monkeypatch.setattr(host, "offered", lambda args: 2)
    late = box.bench(["load", "a.json"])
    assert late.timed_out and late.output.endswith(host.past(2, False)), "as on main"
    monkeypatch.setattr(host, "offered", lambda args: 30)
    box, _ = _box(monkeypatch, tmp_path, "import time; time.sleep(3)", timeout=1)
    assert box.bench(["load", "a.json"]).output.endswith(host.past(1, False))
    # TAKEN as `docker kill` runs, which here signals the child to say it, then end.
    child = _late_taken(able.replace("import time; ", "") + "time.sleep(30)", "sys.stdout",
                        "sys.exit(0)")
    box, _ = _box(monkeypatch, tmp_path, child, timeout=1)
    pids: list[int] = []
    real_popen = subprocess.Popen

    def popen(*a: Any, **k: Any) -> Any:
        process = real_popen(*a, **k)
        pids.append(process.pid)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: os.kill(pids[-1], signal.SIGUSR1)
    )
    raced = box.bench(["load", "a.json"])
    assert raced.timed_out and raced.output.endswith(host.past(1, True)), raced.output


def test_time_kept_for_a_glass_band_is_left_out_of_two_passes_and_its_read_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where a part names its band, two passes are given the container's time less what's kept
    for the band's read, and stop where a step would run into it; the band's read has its own
    clock, and says it's the band's where that runs out (the operator's ruling)."""
    import time

    monkeypatch.setenv(load_parts.TIMEOUT, "600")
    now = time.time()
    plain, kept = load_parts.Clock(now - 300), load_parts.Clock(now - 300, reserve=200.0)
    whole, less = plain.left(), kept.left()
    assert whole is not None and less is not None
    assert whole - less == pytest.approx(200.0, abs=1.0)
    kept.time("whole", 90.0)
    with pytest.raises(load_parts.OutOfTime) as stopped:
        kept.check("whole", "the first pass's coarser mesh")
    assert "600 s, less the 200 s kept for a glass band's lower bound" in str(stopped.value)
    band = load_parts.Clock(now - 500, label="the glass band's lower bound")
    band.time("band", 170.0)
    with pytest.raises(load_parts.OutOfTime) as late:
        band.check("band", "its read")
    assert str(late.value).startswith("the glass band's lower bound met the container's time")

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



def test_one_load_joins_each_set_of_parts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """mqucifer/print-shop#336: two passes join the parts as placed twice and with their bores
    squared twice, each join up to 1,200 s on a design run's table. The same shapes are joined
    once a read, and let go once nothing more is read from them."""
    joins: list[int] = []

    class Shape:
        def generalFuse(self, others: list[Any]) -> tuple[str, list[Any]]:  # noqa: N802
            joins.append(len(others))
            return f"joined {len(joins)}", []

    monkeypatch.setattr(load, "_joined", {})
    monkeypatch.setattr(load, "_kept", [])
    placed = [Shape(), Shape(), Shape()]
    assert load.fused(placed) == load.fused(placed) == "joined 1"
    square = [Shape(), Shape(), Shape()]
    assert load.fused(square) == "joined 2" and load.fused(placed) == "joined 1"
    load.unjoined(square)
    assert load.fused(square) == "joined 3"
    assert load.fused(placed[:1]) is placed[0], "one part is itself, unjoined"
    assert joins == [2, 2, 2]


class _Face:
    """A face as OCC's hash and `isSame` see it: its own shape, `which`, and a hash that more
    than one shape may share."""

    def __init__(self, which: int, hashed: int) -> None:
        self.which, self.hashed = which, hashed

    def hashCode(self) -> int:  # noqa: N802
        return self.hashed

    def isSame(self, other: _Face) -> bool:  # noqa: N802
        return self.which == other.which


def test_faces_by_their_hash_say_what_asking_each_in_turn_says() -> None:
    """mqucifer/print-shop#336: whether a face is one of a part's is asked of those with its hash
    alone, and `isSame` still says. Shapes that share a hash aren't taken for each other."""
    mine = [_Face(k, k % 3) for k in range(10)]
    theirs = [_Face(k, k % 3) for k in (2, 5, 11, 14)]
    index = load.Faces(theirs)
    assert [f.which for f in mine if index.has(f)] == [
        f.which for f in mine if any(f.isSame(g) for g in theirs)
    ] == [2, 5]


def test_what_freecad_caches_of_the_kept_shapes_faces_is_let_go_as_each_step_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared: list[str] = []

    class Kept:
        def __init__(self, name: str) -> None:
            self.name = name

        def clearCache(self) -> None:  # noqa: N802
            cleared.append(self.name)

    monkeypatch.setattr(load, "_kept", [])
    monkeypatch.setattr(load, "give_back", lambda: cleared.append("given back"))
    load.keep([Kept("a"), Kept("b"), "no cache of its own"])
    with load.step("reading the faces"):
        pass
    assert cleared == ["a", "b", "given back"]


def test_a_part_the_squaring_left_as_it_was_stays_kept_once_the_squared_join_goes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mqucifer/print-shop#336, the cold review: letting the squared parts' join go let go of a
    part `squared` returned as it was too, a part as placed, whose faces' cache was then never
    let go again."""

    class Shape:
        def generalFuse(self, others: list[Any]) -> tuple[Any, list[Any]]:  # noqa: N802
            return Shape(), []

    monkeypatch.setattr(load, "_joined", {})
    monkeypatch.setattr(load, "_kept", [])
    monkeypatch.setattr(load, "_placed_now", [])
    placed = [Shape(), Shape()]
    load.keep(placed)
    load._placed_now[:] = placed
    square = [placed[0], Shape()]
    load.keep(square)
    joined = load.fused(square)
    assert any(s is joined for s in load._kept)
    load.unjoined(square)
    assert [s for s in load._kept if s is placed[0]], "a part as placed stays kept"
    assert not [s for s in load._kept if s is square[1] or s is joined]


# `--parts` on glass bands (mqucifer/print-shop#375)


def _banded(tmp_path: Path, banded: bool = True) -> str:
    """A made-up glass frame bolted to a block, the frame naming its glass band where `banded`:
    files only, for the stand-ins below."""
    import json

    frame: dict[str, Any] = {"name": "frame", "file": "frame.step", "layers": "z"}
    if banded:
        frame["band"] = "band.step"
        (tmp_path / "band.step").write_text("")
    data = {
        "parts": [frame, {"name": "block", "file": "block.step", "layers": "z", "at": [0, 0, -20]}],
        "joints": [
            {"between": ["frame", "block"], "kind": "through", "bolt": "m5-8.8", "nut": "m5-nut",
             "washer": "m5-washer", "at": [[6, 10, 6]]}
        ],
        "held": [{"plane": "x=40", "how": "fixed", "part": "block"}],
    }  # fmt: skip
    for name in ("frame", "block"):
        (tmp_path / f"{name}.step").write_text("")
    path = tmp_path / f"glass-{'banded' if banded else 'plain'}.json"
    path.write_text(json.dumps(data))
    return str(path)


def _two_passes_then(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]], stopped: Exception | None
) -> None:
    """`load._run` stood in for where it needs FreeCAD: in two passes it raises `stopped`, or
    reads where that's None; read with the bands left out, it reads, its loads as a lift's with
    the band's weight kept where it's lifted."""

    def run(
        args: Any, report: Report, without_bands: bool = False, two_pass: str | None = None,
        reserve: float = 0.0, tried: float = 0.0,
    ) -> None:  # fmt: skip
        calls.append({"two_pass": two_pass, "without_bands": without_bands, "reserve": reserve,
                      "clock": load._clock["now"]})  # fmt: skip
        report.line(f"read {'without bands' if without_bands else two_pass}")
        if two_pass is not None and stopped is not None:
            raise stopped
        if without_bands:
            load._solvers[:] = ["iterative, corrected", "direct"]
        if without_bands and args.lift is not None:
            report.numbers["loads"] = [
                {"part": "frame", "grams": 30.0, "band_grams": 12.5, "band_mm2": 400.0}
            ]
        report.verdict("OK", "every part is within its material's limits")

    monkeypatch.setattr(load, "_run", run)
    monkeypatch.setattr(load, "_clock", {"now": None})


def _parts_args(path: str, lift: str | None = None) -> Any:
    args = _args(path, parts=True)
    args.lift = lift
    return args


def test_parts_on_a_glass_band_that_meets_the_time_reads_its_lower_bound_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`load --parts` on a frame that names its band: two passes go first, with a one pass's
    limit, `RESERVE` times over, held back from their time for the band's read; where they meet
    the container's time, the frame is read with its band left out, a lower bound, and the reply
    leads with it and says why, as the one pass's two passes do."""
    import time

    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    late = load_parts.OutOfTime("two passes met the container's time, 3600 s, after 2900 s")
    _two_passes_then(monkeypatch, calls, late)
    report = Report("load", ["glass.json"])
    report.started = time.time()
    load.run(_parts_args(_banded(tmp_path)), report)
    assert [(c["two_pass"], c["without_bands"]) for c in calls] == [
        ("asked for with --parts", False), (None, True)
    ]  # fmt: skip
    assert calls[0]["reserve"] == load_parts.RESERVE * load_parts.ONE_PASS
    band = calls[1]["clock"]
    assert band.label == "the glass band's lower bound" and band.reserve == 0
    assert band.done == ["two passes, asked for with --parts, met the container's time"]
    lower = report.numbers["lower_bound"]
    assert lower["parts"] == ["frame"]
    assert lower["why"] == f"read in two passes, as --parts asks: {late}"
    assert lower["solvers"] == {"mesh": "iterative, corrected", "coarser": "direct"}
    assert "weight_kept_g" not in lower
    assert report.why.startswith(
        "read as a lower bound, frame with its glass band left out, the band unread: "
    )
    assert report.facts[0].startswith("read as a lower bound: frame read with its glass band left")
    assert load.BOUND in report.facts[0]
    assert report.facts[1:] == ["read without bands"], "what two passes said is gone"


def test_parts_on_a_glass_band_whose_whole_meets_the_memory_reads_its_lower_bound(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Where two passes meet the container's memory, meshing or solving, they're answered as
    unread (`load._Unread`), and the band's read follows, saying what they met, without what to
    do about it."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    met = load._Unread(
        "nothing of the assembly is read: give a coarser --mesh. gmsh met it",
        "gmsh met the container's memory, 4 GiB",
        memory=True,
    )
    _two_passes_then(monkeypatch, calls, met)
    report = Report("load", ["glass.json"])
    load.run(_parts_args(_banded(tmp_path)), report)
    assert [c["without_bands"] for c in calls] == [False, True]
    done = calls[1]["clock"].done
    assert done == ["two passes, asked for with --parts, met the container's memory"]
    lower = report.numbers["lower_bound"]
    assert lower["why"] == (
        "read in two passes, as --parts asks: gmsh met the container's memory, 4 GiB"
    )


@pytest.mark.parametrize("banded", [True, False])
def test_parts_reads_a_whole_that_reads_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, banded: bool
) -> None:
    """A read that can be had whole is still read whole, band or none: no lower bound, and with
    no band, nothing is held back from two passes."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    _two_passes_then(monkeypatch, calls, None)
    report = Report("load", ["glass.json"])
    load.run(_parts_args(_banded(tmp_path, banded)), report)
    assert [c["without_bands"] for c in calls] == [False]
    assert calls[0]["reserve"] == (load_parts.RESERVE * load_parts.ONE_PASS if banded else 0.0)
    assert "lower_bound" not in report.numbers
    assert report.why == "every part is within its material's limits"


@pytest.mark.parametrize(
    "stopped",
    [
        NoAnswer("pass 2, frame on its own, on the mesh: what it's given doesn't balance"),
        load._Unread("met", "met", memory=True),
        load_parts.OutOfTime("two passes met the container's time"),
    ],
)
def test_parts_says_what_two_passes_say_where_there_s_no_band_or_it_isn_t_time_or_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stopped: Exception
) -> None:
    """No check is loosened: the band's read follows two passes only where they can't read the
    whole and a part names its band. A refusal of their own, as a part whose loads don't
    balance, is said as it is, band or none; and with no band, so is the time or the memory."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    for banded in (True, False):
        if banded and type(stopped) is not NoAnswer:
            continue
        calls: list[dict[str, Any]] = []
        _two_passes_then(monkeypatch, calls, stopped)
        with pytest.raises(NoAnswer) as said:
            load.run(_parts_args(_banded(tmp_path, banded)), Report("load", ["glass.json"]))
        assert said.value is stopped
        assert [c["without_bands"] for c in calls] == [False]


def test_parts_band_s_read_says_where_what_s_left_is_less_than_a_one_pass_s_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The band's read has the container's time left, and is taken to take a one pass's limit
    at the most: where less is left, it says so as the band's, and nothing more is read."""
    import time

    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    _two_passes_then(monkeypatch, calls, load_parts.OutOfTime("two passes met it"))
    report = Report("load", ["glass.json"])
    report.started = time.time() - (host.TWO_PASSES - load_parts.ONE_PASS)
    with pytest.raises(load_parts.OutOfTime) as late:
        load.run(_parts_args(_banded(tmp_path)), report)
    assert str(late.value).startswith("the glass band's lower bound met the container's time")
    assert "before its read, which a one pass's limit is given for" in str(late.value)
    assert [c["without_bands"] for c in calls] == [False]


@pytest.mark.parametrize("parts", [True, False])
def test_a_lift_on_a_glass_band_that_can_t_be_read_whole_keeps_the_band_s_weight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parts: bool
) -> None:
    """mqucifer/print-shop#375: a lift on a frame whose whole can't be read was refused, as read
    without its band the frame would hang by less than its weight. Now the band's weight is
    kept, hung where it met the frame, with its stiffness left out: the reply leads with that,
    says the movement taken together is an upper bound under the full weight, never a part's
    strain, that the band is unread, and gives the weight kept. In two passes
    asked for, and after the one pass's."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    met = load._Unread("met", "the whole met the container's memory", memory=True)
    _two_passes_then(monkeypatch, calls, met)
    if not parts:
        two = load._run

        def one_pass_first(args: Any, report: Report, without_bands: bool = False,
                           **given: Any) -> None:  # fmt: skip
            if not without_bands and given.get("two_pass") is None:
                raise met
            two(args, report, without_bands, **given)

        monkeypatch.setattr(load, "_run", one_pass_first)
    args = _parts_args(_banded(tmp_path), lift="block")
    args.parts = parts
    report = Report("load", ["glass.json"])
    load.run(args, report)
    assert calls[-1]["without_bands"] is True
    lower = report.numbers["lower_bound"]
    assert lower["parts"] == ["frame"] and lower["weight_kept_g"] == {"frame": 12.5}
    assert report.why.startswith(
        "read as a bound, frame with its glass band's stiffness left out and its weight kept, "
        "the band unread: "
    )
    fact = report.facts[0]
    assert fact.startswith("read as a bound: frame read with its glass band's stiffness left out "
                           "and its weight kept")  # fmt: skip
    assert load.LIFT_BOUND in fact and load.BOUND not in fact
    assert "is an upper bound under the full weight" in load.LIFT_BOUND
    assert "Each part's strain isn't one" in load.LIFT_BOUND


def test_a_one_pass_s_limit_for_the_band_is_the_host_s() -> None:
    """`ONE_PASS` runs in the image, `host.TIMEOUT` outside it: the same figure."""
    assert load_parts.ONE_PASS == host.TIMEOUT


def test_a_mesh_that_met_the_container_s_memory_is_unread_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Where gmsh made no mesh and the kernel stopped a process for memory while it ran, the
    whole is unread for memory, as where CalculiX meets it, so two passes or the band's read can
    follow (mqucifer/print-shop#375). A mesh that's there, or no count risen, is left to say
    what it says."""
    import types

    def model(volumes: int) -> Any:
        return types.SimpleNamespace(size=3.0, straight=True), types.SimpleNamespace(
            FemMesh=types.SimpleNamespace(VolumeCount=volumes)
        )

    monkeypatch.setattr(load, "memory_met", lambda before: 4 * 2**30 if before == 7 else None)
    self, mesh = model(0)
    with pytest.raises(load._Unread) as met:
        load._Model._mesh_met(self, mesh, 7)
    assert met.value.memory
    assert met.value.evidence == (
        "gmsh made no mesh of the joined parts at a mesh of at most 3 mm, its elements "
        "straight-sided: it met the container's memory, 4 GiB, meshing them, and the kernel "
        "stopped it there, as the container's own count of what it stopped for memory says"
    )
    assert str(met.value).startswith("nothing of the assembly is read, as its mesh is past what")
    load._Model._mesh_met(self, mesh, 8)
    self, mesh = model(10)
    load._Model._mesh_met(self, mesh, 7)


@pytest.mark.parametrize("code", [0, 1])
def test_gmsh_first_to_go_runs_freecad_s_own_command_and_reads_its_mesh_only_where_it_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
) -> None:
    """`cae.gmsh(first=True)` runs gmsh as FreeCAD's own run does, `-v LEVEL - GEO`, in a
    process of its own the kernel stops first for memory (`first_to_go`), and keeps what it
    said: the mesh is read back only where it ended well."""
    import sys
    import types

    from print_shop.bench import cae

    seen = tmp_path / "args"
    binary = tmp_path / "gmsh"
    binary.write_text(f'#!/bin/sh\necho "$@" > {seen}\necho "Error   : stopped" >&2\nexit {code}\n')
    binary.chmod(0o755)
    params = types.SimpleNamespace(GetString=lambda key, default: "3")
    monkeypatch.setitem(
        sys.modules, "FreeCAD", types.SimpleNamespace(ParamGet=lambda path: params)
    )
    read: list[bool] = []
    tools = types.SimpleNamespace(
        prepare=lambda: None, gmsh_bin=str(binary), temp_file_geo="shape.geo",
        update_properties=lambda: read.append(True), get_tmp_file_paths=lambda *given: None,
    )  # fmt: skip
    assert cae.gmsh(tools, first=True) is (code == 0)
    assert seen.read_text().split() == ["-v", "3", "-", "shape.geo"]
    assert read == ([True] if code == 0 else [])
    assert tools.returncode == code and "Error   : stopped" in tools.said
    assert load._gmsh_said(tools) == "stopped"


def _bands_probe() -> Any:
    import importlib.util
    import sys

    folder = Path(__file__).parent.parent / "scripts"
    sys.path.insert(0, str(folder))
    try:
        path = folder / "load_bands_probe.py"
        spec = importlib.util.spec_from_file_location("load_bands_probe", path)
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
    finally:
        sys.path.remove(str(folder))
    return probe


def test_the_bands_probe_says_how_it_was_read_and_what_stopped_the_whole_by_kinds_alone() -> None:
    """`scripts/load_bands_probe.py` is run on a design's own assembly: it says how the reply
    was read and what stopped the whole as kinds and counts, never a figure or a name. Made-up
    replies only."""
    probe = _bands_probe()
    lifted = {
        "numbers": {
            "lower_bound": {
                "parts": ["frame-a", "frame-b"],
                "why": "read in two passes, as --parts asks: gmsh made no mesh of the joined "
                "parts at a mesh of at most 7.25 mm: it met the container's memory, 4 GiB",
                "tried_s": 412.0, "band_s": 96.5, "weight_kept_g": {"frame-a": 31.7},
            },
            "loads": [{"part": "frame-a", "band_mm2": 210.4}, {"part": "block", "grams": 9.0}],
        }
    }  # fmt: skip
    said = probe.summary(lifted)
    assert said == [
        "read: each band left out, lifted, each band's weight kept",
        "what stopped the whole: memory meshing",
        "parts read without their band: 2",
        "of them, with the band's weight hung where it met them: 1",
        "the try took 412.0 s, the band's read 96.5 s",
    ]
    assert not any(n in " ".join(said) for n in ("frame-a", "7.25", "31.7", "210.4"))
    assert probe.kinds(
        "it met the container's memory, 3 GiB; and in two passes, two passes met the container's "
        "time, 3600 s"
    ) == ["memory solving", "time"]
    assert probe.kinds("the mesh gives block no element of its own") == ["unparted"]
    assert probe.kinds(
        "read in two passes, as --parts asks: gmsh meshed the joined parts at a mesh of at most "
        "7 mm, and its mesh takes about 2.10 GiB read back, where the container's memory has "
        "0.40 GiB left, by its own count: so it isn't read back"
    ) == ["memory reading the mesh back"]
    assert probe.kinds(
        "the mesh of the joined parts at a mesh of at most 7 mm, 9 elements and 9 nodes, takes "
        "about 0.70 GiB to set up in load's own process, and the container's memory has 0.10 GiB"
    ) == ["memory setting the mesh up"]
    assert probe.kinds(
        "load's own process met the container's memory, reading the joint a+b"
    ) == ["memory in load's own process"]
    assert probe.kinds(
        "the mesh of the joined parts at a mesh of at most 7 mm, 9 elements and 9 nodes, takes "
        "about 3.50 GiB to write CalculiX's input with its loads in load's own process"
    ) == ["memory writing CalculiX's input"]
    assert probe.kinds(
        "the mesh ..., takes about 3.50 GiB to read CalculiX's results back in load's own"
    ) == ["memory reading CalculiX's results"]
    assert probe.kinds("gmsh made no mesh of the joined parts: it gave no reason") == ["no mesh"]
    assert probe.summary({"numbers": {"parts": {"a": {}}, "two_passes": {}}}) == [
        "read: the whole, in two passes"
    ]


def test_the_bands_probe_counts_elements_turned_inside_out_and_those_in_no_band_s_leading() -> None:
    """mqucifer/print-shop#382: the probe says a band's bound read for elements turned inside out
    in its leading by its kind, and where they leave the reply no answer, how many there are and
    how many are in no glass band's leading, by counts alone. Made-up replies only."""
    probe = _bands_probe()
    assert probe.kinds(
        "CalculiX solved nothing: an element of the mesh, at most 7 mm, is turned inside out, and "
        "each one it found is in a glass band's leading, frame-a's, the faces nearest it within "
        "the band: 3 element(s), the first in frame-a"
    ) == ["inverted in a band's leading"]
    stopped = {
        "verdict": "NO ANSWER",
        "why": "CalculiX solved nothing: an element of the mesh, at most 7 mm, is turned inside "
        "out: 5 element(s), the first in leg-2, round (1, 2, 3), its longest side 0.0400 mm; 2 of "
        "the 5 in no glass band's leading, so no band is left out for them. One with a curved "
        "side on a small hole or a tight round can be: give a finer --mesh",
    }
    said = probe.inverted(stopped)
    assert said == ["turned inside out: 5 element(s)", "in no glass band's leading: 2 of 5"]
    assert not any(n in " ".join(said) for n in ("leg-2", "0.0400", "7 mm"))
    unfound = dict(stopped, why=stopped["why"].split("; 2 of")[0] + "; whether they're in a glass "
                   "band's leading couldn't be found: load stopped ...")  # fmt: skip
    assert probe.inverted(unfound)[1:] == ["in a glass band's leading: couldn't be found"]
    assert probe.inverted({"verdict": "OK", "why": stopped["why"]}) == []
    assert probe.inverted({"verdict": "NO ANSWER", "why": "gmsh made no mesh"}) == []


# The operator's cold review of mqucifer/print-shop#376


def test_a_mesh_gmsh_made_that_can_t_be_read_back_is_no_mesh_as_on_qt_s_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """gmsh ended well, but FreeCAD couldn't read its mesh back: on Qt's way that was raised in
    Qt's own handler, and the mesh was left with no volumes, so the straight-sided retry
    followed. Run first to go, it's the same: no mesh, and what FreeCAD said kept with gmsh's."""
    import sys
    import types

    from print_shop.bench import cae

    binary = tmp_path / "gmsh"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    params = types.SimpleNamespace(GetString=lambda key, default: "3")
    monkeypatch.setitem(
        sys.modules, "FreeCAD", types.SimpleNamespace(ParamGet=lambda path: params)
    )

    def unreadable() -> None:
        raise RuntimeError("unv: no volumes in file")

    tools = types.SimpleNamespace(
        prepare=lambda: None, gmsh_bin=str(binary), temp_file_geo="shape.geo",
        update_properties=unreadable, get_tmp_file_paths=lambda *given: None,
    )  # fmt: skip
    assert cae.gmsh(tools, first=True) is False
    assert tools.returncode == 0
    assert "Error   : FreeCAD couldn't read gmsh's mesh back: RuntimeError: unv: no volumes" in (
        tools.said
    )


def _unparted_model(banded: bool, turned: bool = False) -> Any:
    import types

    from print_shop.bench.assembly import AXES

    assert "z" in AXES
    parts = [
        types.SimpleNamespace(name="frame", band="band.step" if banded else None, turn=()),
        types.SimpleNamespace(name="block", band=None, turn=(("z", 45.0),) if turned else ()),
        types.SimpleNamespace(name="leg", band=None, turn=()),
    ]
    return types.SimpleNamespace(
        assembly=types.SimpleNamespace(parts=parts),
        met={frozenset(("frame", "block")): [], frozenset(("block", "leg")): []},
    )


@pytest.mark.parametrize(
    ("banded", "turned", "empty", "unread"),
    [
        (True, False, ["block"], True),
        (True, False, ["frame"], True),
        (False, False, ["block"], False),
        (True, True, ["block"], False),
        (True, False, ["leg"], False),
    ],
)
def test_two_passes_take_a_mesh_that_doesn_t_part_the_parts_as_unread_only_by_a_band(
    banded: bool, turned: bool, empty: list[str], unread: bool
) -> None:
    """In two passes, a mesh that gives a part no element of its own is the whole unread, for
    `--parts` to read each band left out, only where it's the leads' doing: each such part is
    banded or meets a banded part, and none is turned. A turned part's placement
    (mqucifer/print-shop#334), or faces not found between parts with no band
    (mqucifer/print-shop#319), stay `NO ANSWER`, as one pass says them."""
    said = load._Unparted("the mesh gives block no element of its own", empty)
    found = load._Model._unparted_read(_unparted_model(banded, turned), said)
    if unread:
        assert isinstance(found, load._Unread) and not found.memory
        assert str(found) == str(said)
    else:
        assert found is said


def test_a_lift_s_band_bound_says_which_joints_statics_fixes_and_no_other_share_is_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The cold review: a joint's and its bolts' share is the full weight's only where statics
    fixes it, everything beyond it hanging from it alone; in a loop of two ways, taking a band's
    stiffness out moves load between them, and a joint can carry less. So the reply names the
    joints statics fixes, and says every other share can be more or less, and isn't a bound."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    met = load._Unread("met", "the whole met the container's memory", memory=True)
    _two_passes_then(monkeypatch, calls, met)
    read = load._run

    def with_joints(args: Any, report: Report, without_bands: bool = False, **given: Any) -> None:
        read(args, report, without_bands, **given)
        report.numbers["joints"] = {
            "frame+block": {"force_n": [0, 0, 1.0], "statics_force_n": [0, 0, 1.0]},
            "frame+arm": {"force_n": [0, 0, 0.4]},
            "arm+block": {"force_n": [0, 0, 0.4]},
        }

    monkeypatch.setattr(load, "_run", with_joints)
    report = Report("load", ["glass.json"])
    load.run(_parts_args(_banded(tmp_path), lift="block"), report)
    lower = report.numbers["lower_bound"]
    assert lower["statics_fixed"] == ["frame+block"]
    assert lower["shares_not_bound"] == ["arm+block", "frame+arm"]
    fact = report.facts[0]
    assert "Statics fixes frame+block: each carries the load beyond it" in fact
    assert "arm+block and frame+arm can carry more or less than with the band in" in fact
    assert "each joint's and bolt's share of the load, are upper bounds" not in load.LIFT_BOUND


def test_gmsh_s_files_for_the_joined_parts_are_written_on_disk_not_in_the_container_s_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A design run's assembly on mqucifer/print-shop#376: two passes were killed at the
    straight-sided mesh's start, the container's /tmp, which is its memory, holding gmsh's
    input. A file there is no process's, so the kernel stopped `load`. Now gmsh's files are
    written in a folder of their own on /work's disk (`cae.scratch`), and FreeCAD is told it as
    gmsh's own folder; off the container, where there's no /work, in the system's temporary
    folder as before. And each part of the mesh is a step of its own, so a trace says which held
    what."""
    import sys
    import types

    from print_shop.bench import cae, progress

    disk = tmp_path / "work"
    disk.mkdir()
    monkeypatch.setattr(cae, "DISK", disk)
    folder = Path(cae.scratch("gmsh-"))
    assert folder.parent == disk / cae.SCRATCH and folder.name.startswith("gmsh-")
    monkeypatch.setattr(cae, "DISK", tmp_path / "none")
    assert Path(cae.scratch("load-")).parent == Path(__import__("tempfile").gettempdir())
    monkeypatch.setattr(cae, "DISK", disk)
    binary = tmp_path / "gmsh"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    params = types.SimpleNamespace(GetString=lambda key, default: "3")
    monkeypatch.setitem(
        sys.modules, "FreeCAD", types.SimpleNamespace(ParamGet=lambda path: params)
    )
    asked: list[tuple[Any, ...]] = []
    steps: list[str] = []
    monkeypatch.setattr(
        progress, "_say", lambda kind, text: steps.append(text) if kind == host.STEP_LINE else None
    )
    tools = types.SimpleNamespace(
        gmsh_bin=str(binary), temp_file_geo="shape.geo", update_properties=lambda: None,
        get_tmp_file_paths=lambda *given: asked.append(given),
    )  # fmt: skip
    tools.prepare = lambda: tools.get_tmp_file_paths()
    assert cae.gmsh(tools, first=True) is True
    assert len(asked) == 1 and Path(asked[0][0]).parent == disk / cae.SCRATCH
    assert asked[0][1] is True, "FreeCAD makes it where it isn't"
    assert [t for t in steps if not t.startswith("between")][:3] == [
        "writing gmsh's input, the joined parts' shape, on disk",
        "gmsh meshing the joined parts, first to go",
        "reading gmsh's mesh back",
    ]


def test_a_killed_command_s_scratch_folders_go_at_the_next_load_s_start_and_only_they(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scratch folder is in a run's own work folder (the operator's guards): a dot-folder,
    each command's own folders removed at its end, and at a load's start only folders its own
    kinds name and older than `STALE`, a killed command's. A younger one may be a running
    command's, and anything else there isn't the bench's."""
    import os

    from print_shop.bench import cae

    monkeypatch.setattr(cae, "DISK", tmp_path)
    monkeypatch.setattr(cae, "_own", [])
    assert cae.SCRATCH.startswith(".")
    root = tmp_path / cae.SCRATCH
    root.mkdir()
    now = 1e9
    for name, age in (("gmsh-old", cae.STALE + 60), ("load-old", cae.STALE + 60),
                      ("gmsh-young", 60), ("theirs", cae.STALE + 60)):  # fmt: skip
        (root / name).mkdir()
        (root / name / "f").write_text("x")
        os.utime(root / name, (now - age, now - age))
    cae.clear_stale(now)
    assert sorted(p.name for p in root.iterdir()) == ["gmsh-young", "theirs"]
    mine = Path(cae.scratch("gmsh-"))
    (mine / "shape.brep").write_text("x")
    cae.clear_own()
    assert not mine.exists() and root.exists(), "the others' are left, and so is the folder"
    for name in ("gmsh-young", "theirs"):
        __import__("shutil").rmtree(root / name)
    cae.scratch("load-")
    cae.clear_own()
    assert not root.exists(), "an empty scratch folder goes too"


# `load`'s own process held to the container's room (a design run's assembly on
# mqucifer/print-shop#376)


def _cgroup(tmp_path: Path, most: str, now: int, inactive: int) -> Path:
    folder = tmp_path / "cgroup"
    folder.mkdir(exist_ok=True)
    (folder / "memory.max").write_text(most + "\n")
    (folder / "memory.current").write_text(f"{now}\n")
    (folder / "memory.stat").write_text(f"anon 1\nactive_file 999999\ninactive_file {inactive}\n")
    return folder


def test_the_room_load_has_is_the_container_s_own_count_of_what_s_left(tmp_path: Path) -> None:
    """What `load`'s own process may still take: the container's limit, less what it holds,
    plus the file pages it holds that the kernel would drop first, less `ROOM_MARGIN`. None off
    the container, or with no limit."""
    from print_shop.bench import cae

    gib = 2**30
    folder = _cgroup(tmp_path, str(4 * gib), 3 * gib, gib // 4)
    assert cae.room(folder) == 4 * gib - 3 * gib + gib // 4 - cae.ROOM_MARGIN
    # Held high, where a lower bound can follow, the file pages aren't counted on.
    assert cae.room(folder, files=False) == 4 * gib - 3 * gib - cae.ROOM_MARGIN
    assert cae.room(_cgroup(tmp_path, "max", gib, 0)) is None
    assert cae.room(tmp_path / "none") is None


def test_a_mesh_that_won_t_fit_read_back_isn_t_read_and_is_said_for_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed allocation in FreeCAD's own mesh reader aborts the process, never a Python
    error (tried in the image): so before gmsh's mesh is read back, what it takes, `READ_BACK`
    times its file (2.37 to 2.40 measured), is held against the container's room. Where it
    won't fit, it isn't read, and it's said for memory, so two passes or the band's read can
    follow. Where it fits, it's read as before."""
    import sys
    import types

    from print_shop.bench import cae

    binary = tmp_path / "gmsh"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    unv = tmp_path / "mesh.unv"
    unv.write_bytes(b"x" * 1000)
    params = types.SimpleNamespace(GetString=lambda key, default: "3")
    monkeypatch.setitem(
        sys.modules, "FreeCAD", types.SimpleNamespace(ParamGet=lambda path: params)
    )
    monkeypatch.setattr(cae, "DISK", tmp_path / "none")
    asked: list[bool] = []

    def asking(left: float) -> Any:
        def room(files: bool = True) -> float:
            asked.append(files)
            return left

        return room

    for left, reads in ((cae.READ_BACK * 1000 - 1, False), (cae.READ_BACK * 1000 + 1, True)):
        monkeypatch.setattr(cae, "room", asking(left))
        read: list[bool] = []
        tools = types.SimpleNamespace(
            prepare=lambda: None, gmsh_bin=str(binary), temp_file_geo="shape.geo",
            temp_file_mesh=str(unv), update_properties=lambda read=read: read.append(True),
            get_tmp_file_paths=lambda *given: None,
        )  # fmt: skip
        assert cae.gmsh(tools, first=True) is reads
        assert read == ([True] if reads else [])
        if not reads:
            assert tools.memory == (cae.READ_BACK * 1000, left, False)
            assert "Error   : the mesh gmsh made takes about " in tools.said
    assert asked == [True, True]
    # With a lower bound to fall to, held high: above the most measured, a half again.
    monkeypatch.setattr(cae, "room", asking(3000))
    tools = types.SimpleNamespace(
        prepare=lambda: None, gmsh_bin=str(binary), temp_file_geo="shape.geo",
        temp_file_mesh=str(unv), update_properties=lambda: None,
        get_tmp_file_paths=lambda *given: None,
    )  # fmt: skip
    assert cae.READ_BACK_HIGH * 1000 > 3000 > cae.READ_BACK * 1000
    assert cae.gmsh(tools, first=True, high=True) is False
    assert tools.memory == (cae.READ_BACK_HIGH * 1000, 3000, True) and asked[-1] is False
    assert "may not fit" in tools.said


def test_a_set_up_that_won_t_fit_is_the_whole_unread_for_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the mesh is read back, `load` sets it up in its own process: each node's place
    and, in two passes, each element's faces. Its need, by the mesh's own counts at the least
    measured per node and per element, is held against the container's room: where it won't
    fit, the whole is unread for memory, before anything is allocated. A mesh gmsh's read-back
    was refused for is unread too."""
    import types

    from print_shop.bench import cae

    fem = types.SimpleNamespace(NodeCount=1000, VolumeCount=500)
    need = 1000 * load.SET_UP_NODE + 500 * load.SET_UP_FACES
    high = 1000 * load.SET_UP_NODE_HIGH + 500 * load.SET_UP_FACES_HIGH
    cases = [
        (True, need - 1, False, False), (True, need, True, False),
        (False, 1000 * load.SET_UP_NODE, True, False),
        (True, high - 1, False, True), (True, high, True, True),
    ]  # fmt: skip
    for two_pass, left, fits, falls in cases:
        monkeypatch.setattr(cae, "room", lambda files=True, left=left: left)
        monkeypatch.setattr(load._Model, "_falls_back", lambda self, falls=falls: falls)
        model: Any = types.SimpleNamespace(
            size=3.0, straight=False, two_pass=two_pass, solids=[1, 2],
            _falls_back=lambda falls=falls: falls,
        )  # fmt: skip
        if fits:
            load._Model._set_up_fits(model, fem)
            continue
        with pytest.raises(load._Unread) as met:
            load._Model._set_up_fits(model, fem)
        assert met.value.memory
        assert met.value.evidence.startswith(
            "the mesh of the joined parts at a mesh of at most 3 mm"
        )
        assert ("may not fit" in met.value.evidence) is falls
    monkeypatch.setattr(cae, "room", lambda files=True: None)
    unasked: Any = types.SimpleNamespace(two_pass=True, solids=[1, 2], _falls_back=lambda: True)
    load._Model._set_up_fits(unasked, fem)
    model = types.SimpleNamespace(size=3.0, straight=False)
    mesh = types.SimpleNamespace(FemMesh=types.SimpleNamespace(VolumeCount=0))
    tools = types.SimpleNamespace(memory=(3 * 2**30, 2**30, False))
    with pytest.raises(load._Unread) as refused:
        load._Model._mesh_met(model, mesh, None, tools)
    assert refused.value.memory and "takes about 3.00 GiB read back" in refused.value.evidence


@pytest.mark.parametrize(
    ("said", "left", "memory"),
    [
        ("Unable to allocate 1.50 GiB for an array with shape (9,) and data type float64",
         2**29, True),
        ("Unable to allocate 7.28 TiB for an array with shape (9,) and data type float64",
         2**30, False),
        ("Unable to allocate 1.50 GiB for an array with shape (9,) and data type float64",
         2**31, False),
        ("", 0, True),
        ("", 2**31, False),
        ("", None, False),
    ],
)  # fmt: skip
def test_a_step_that_meets_python_s_memory_is_unread_for_it_only_where_the_room_is_short(
    monkeypatch: pytest.MonkeyPatch, said: str, left: int | None, memory: bool
) -> None:
    """Where `load`'s own Python meets the memory in a step, the whole is unread for it, saying
    the step, so two passes or the band's read can follow; but only where the container's room
    is short of it (the operator's review of mqucifer/print-shop#376): what numpy asked for, where
    it says, is within the container's limit and past what's left, or where it doesn't say,
    nothing's left. Anything else, as an array of terabytes or off the container, is a plain
    error, said as one, never taken for the memory."""
    from print_shop.bench import cae

    monkeypatch.setattr(cae, "room", lambda files=True: left)
    monkeypatch.setattr(cae, "memory_max", lambda: 4 * 2**30 if left is not None else None)
    with pytest.raises(NoAnswer) as stopped, load.step("meshing and solving the joined parts"):
        raise MemoryError(said) if said else MemoryError()
    assert isinstance(stopped.value, load._Unread) is memory
    if isinstance(stopped.value, load._Unread):
        assert stopped.value.memory
        assert stopped.value.evidence.startswith(
            "load's own process met the container's memory, meshing and solving the joined parts"
        ), stopped.value.evidence
    else:
        assert str(stopped.value).startswith(
            "load stopped meshing and solving the joined parts: MemoryError"
        ), str(stopped.value)
    with pytest.raises(NoAnswer) as other, load.step("reading"):
        raise RuntimeError("something else")
    assert not isinstance(other.value, load._Unread)


def test_optimise_clears_its_scratch_at_its_end_and_a_killed_command_s_at_its_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`optimise` solves with `load`'s model, so its mesh and solve files go in the same scratch
    folder (the operator's review): it clears its own at its end, whatever stopped it, and a
    killed command's at its start, as `load` does."""
    import os

    from print_shop.bench import cae, optimise

    monkeypatch.setattr(cae, "DISK", tmp_path)
    monkeypatch.setattr(cae, "_own", [])
    root = tmp_path / cae.SCRATCH
    root.mkdir()
    (root / "load-old").mkdir()
    os.utime(root / "load-old", (1.0, 1.0))
    made: list[Path] = []

    def failing(args: Any, report: Report) -> None:
        assert not (root / "load-old").exists(), "a killed command's, cleared first"
        made.append(Path(cae.scratch("load-")))
        raise NoAnswer("stopped")

    monkeypatch.setattr(optimise, "_optimise", failing)
    with pytest.raises(NoAnswer):
        optimise.run(object(), Report("optimise", []))
    assert made and not made[0].exists() and not root.exists()
def test_where_a_lower_bound_can_follow_a_mesh_s_need_is_held_high(tmp_path: Path) -> None:
    """The operator's ruling on the design run's assembly: a step let through that doesn't fit
    costs the container, and a step refused that would just have fitted costs only a lower
    bound, said. So where a part names its band and this isn't the band's own read, each step's
    need is held a half above the most measured; anywhere else, a tenth under the least."""
    import types

    def model(banded: bool) -> Any:
        return types.SimpleNamespace(assembly=types.SimpleNamespace(parts=[
            types.SimpleNamespace(band="band.step" if banded else None),
            types.SimpleNamespace(band=None),
        ]))  # fmt: skip

    load._bands.clear()
    assert load._Model._falls_back(model(True)) is True
    assert load._Model._falls_back(model(False)) is False
    load._bands["frame"] = load.Band([], 1.0, 2.0, np.zeros(3))
    try:
        assert load._Model._falls_back(model(True)) is False, "the band's read has nothing after"
    finally:
        load._bands.clear()
    assert load.SET_UP_NODE_HIGH >= 1.5 * 430 and load.SET_UP_FACES_HIGH >= 1.5 * 735
    assert load.SET_UP_NODE <= 0.9 * 374 and load.SET_UP_FACES <= 0.9 * 735


def test_the_bands_probe_gives_each_mesh_s_counts_as_it_was_set_up() -> None:
    """So a set-up that's killed can be held against its need: the probe gives each mesh's
    elements and nodes as `load` set it up, and the container's memory at its start, from the
    step `load` says, and no other figure."""
    probe = _bands_probe()
    trace = [
        "bench: step 940.0 meshing and solving the joined parts, at a mesh of at most 7.25 mm",
        "bench: memory 941.0 current=3000000000 peak=3500000000",
        "bench: step 948.1 setting the mesh up in load's own process: 812345 elements, "
        "1234567 nodes",
        "bench: memory 948.1 current=3736000000 peak=3900000000",
    ]
    trace += [
        "bench: step 950.0 writing CalculiX's input: 812345 elements, 1234567 nodes",
        "bench: memory 950.0 current=3800000000 peak=3900000000",
    ]
    assert probe.set_ups(trace) == [
        "setting the mesh up in load's own process at 948.1 s: 812345 elements, 1234567 nodes; "
        "the container held 3.48 GiB at its start",
        "writing CalculiX's input at 950.0 s: 812345 elements, 1234567 nodes; the container held "
        "3.54 GiB at its start",
    ]


def _counted(two_pass: bool, falls: bool) -> Any:
    import types

    return types.SimpleNamespace(
        size=3.0, straight=True, two_pass=two_pass, solids=[1, 2], _falls_back=lambda: falls
    )


@pytest.mark.parametrize("falls", [False, True])
def test_writing_calculix_s_input_and_reading_its_results_are_held_to_the_room_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, falls: bool
) -> None:
    """The design run's assembly on mqucifer/print-shop#376, its set-up checked, was killed
    writing CalculiX's input in `load`'s own process: FreeCAD's writer took 3.10 to 3.13 KB a
    node (4.36 an element) on two meshes that solved, and its reader of the results 3.39 to
    3.52 (4.72 to 4.94). Each is held to the room before it's begun, by the mesh's counts: low,
    a tenth under the least, where the try is all there is; and where a lower bound can follow,
    a half above the most. Where it won't fit, the whole is unread for memory."""
    import types

    from print_shop.bench import cae

    fem = types.SimpleNamespace(NodeCount=1000, VolumeCount=700)
    for need, what in ((load._Model._write_need, "to write CalculiX's input with its loads"),
                       (load._Model._read_need, "to read CalculiX's results back")):  # fmt: skip
        want = need(_counted(True, falls), fem)
        if falls:
            assert want == pytest.approx(1.5 * max(
                1000 * (load.WRITE_NODE_HIGH if "write" in what else load.READ_NODE_HIGH),
                700 * (load.WRITE_ELEMENT_HIGH if "write" in what else load.READ_ELEMENT_HIGH),
            ))  # fmt: skip
        else:
            assert want == pytest.approx(0.9 * min(
                1000 * (load.WRITE_NODE if "write" in what else load.READ_NODE),
                700 * (load.WRITE_ELEMENT if "write" in what else load.READ_ELEMENT),
            ))  # fmt: skip
        monkeypatch.setattr(cae, "room", lambda files=True, want=want: want - 1)
        with pytest.raises(load._Unread) as met:
            load._Model._fits(_counted(True, falls), fem, want, what)
        assert met.value.memory and what in met.value.evidence
        assert ("may not fit" in met.value.evidence) is falls
        monkeypatch.setattr(cae, "room", lambda files=True, want=want: want)
        load._Model._fits(_counted(True, falls), fem, want, what)


def test_calculix_s_results_are_read_back_only_where_they_fit_and_as_a_step(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Where CalculiX left no results file, as where it was stopped, FreeCAD's reader is asked
    as before and finds none. Where it left one, its need is held to the room first, and it's
    read as a step of its own, giving the mesh's counts."""
    import types

    from print_shop.bench import cae, progress

    steps: list[str] = []
    monkeypatch.setattr(
        progress, "_say", lambda kind, text: steps.append(text) if kind == host.STEP_LINE else None
    )
    read: list[bool] = []
    fea = types.SimpleNamespace(
        inp_file_name=str(tmp_path / "solve.inp"), load_results=lambda: read.append(True)
    )
    model: Any = _counted(False, False)
    model.fem = types.SimpleNamespace(NodeCount=1000, VolumeCount=700)
    model._read_need = lambda fem: 10**9
    model._fits = lambda fem, need, what: load._Model._fits(model, fem, need, what)
    monkeypatch.setattr(cae, "room", lambda files=True: 0)
    load._Model._results_back(model, fea)
    assert read == [True] and steps == [], "no file: asked as before"
    (tmp_path / "solve.frd").write_text("x")
    with pytest.raises(load._Unread):
        load._Model._results_back(model, fea)
    assert read == [True]
    monkeypatch.setattr(cae, "room", lambda files=True: 10**10)
    load._Model._results_back(model, fea)
    assert read == [True, True]
    assert steps[0] == "reading CalculiX's results back: 700 elements, 1000 nodes"


def test_a_solve_past_the_memory_says_how_long_each_of_calculix_s_tries_ran() -> None:
    """The operator: on a large assembly the direct solver and then the iterative one may each
    be tried and stopped before the band's read, so the reply gives each try's seconds."""
    import types

    model: Any = types.SimpleNamespace(
        fem=types.SimpleNamespace(VolumeCount=500_000, NodeCount=900_000),
        shape=types.SimpleNamespace(Volume=1e6), size=5.0,
        tries=[("direct", 412.4), ("iterative", 1290.6)],
    )  # fmt: skip
    said = load._Model._too_big(model, 4 * 2**30)
    assert said.memory
    ran = "CalculiX's direct solver ran 412 s and its iterative one 1291 s, each stopped there"
    assert ran in said.evidence, said.evidence


def _too_big_model(banded: bool) -> Any:
    import types

    return types.SimpleNamespace(
        fem=types.SimpleNamespace(VolumeCount=470_000, NodeCount=827_000),
        shape=types.SimpleNamespace(Volume=1e6), size=5.0, tries=[("direct", 26.5)],
        assembly=types.SimpleNamespace(parts=[
            types.SimpleNamespace(band="band.step" if banded else None),
            types.SimpleNamespace(band=None),
        ]),
    )  # fmt: skip


def test_a_read_past_the_memory_never_says_to_name_bands_that_are_named() -> None:
    """The design run's assembly on mqucifer/print-shop#376: its bands named and left out, the
    band's own read met the memory, and the reply said to name each glass frame's band. Where
    they're named, it says so, and, left out already, that the bound is past the memory too."""
    plain = load._Model._too_big(_too_big_model(False), 4 * 2**30)
    assert "name each glass frame's `band`" in str(plain)
    load._bands.clear()
    named = load._Model._too_big(_too_big_model(True), 4 * 2**30)
    assert "name each glass frame's `band`" not in str(named)
    assert "each glass frame's band is named, and it's read next with its band left out" in (
        str(named)
    )
    load._bands["frame"] = load.Band([], 1.0, 2.0, np.zeros(3))
    try:
        left_out = load._Model._too_big(_too_big_model(True), 4 * 2**30)
    finally:
        load._bands.clear()
    assert "name each glass frame's `band`" not in str(left_out)
    assert "with each glass band left out already, it's past the container's memory still" in (
        str(left_out)
    )
    assert "name each glass frame's `band`" not in str(_memory_said("x", True))
    assert "name each glass frame's `band`" in str(_memory_said("x", False))


def _memory_said(evidence: str, banded: bool) -> Any:
    with pytest.raises(load._Unread) as said:
        load._memory_unread(evidence, banded)
    return said.value


def test_a_band_s_read_that_can_t_be_read_says_the_bound_itself_wasn_t(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Where the band's own read can't be read, the reply says the lower bound couldn't be read
    either, why the whole couldn't, and what the band's read met: never as if the whole had."""
    monkeypatch.setenv(load_parts.TIMEOUT, str(host.TWO_PASSES))
    calls: list[dict[str, Any]] = []
    met = load._Unread("met", "the whole met the container's memory", memory=True)
    _two_passes_then(monkeypatch, calls, met)
    read = load._run

    def band_too_big(args: Any, report: Report, without_bands: bool = False, **given: Any) -> None:
        read(args, report, without_bands, **given)
        if without_bands:
            raise load._Unread("nothing of the assembly is read: CalculiX met it", "it met it")

    monkeypatch.setattr(load, "_run", band_too_big)
    with pytest.raises(NoAnswer) as said:
        load.run(_parts_args(_banded(tmp_path)), Report("load", ["glass.json"]))
    text = str(said.value)
    assert text.startswith(
        "the glass band's lower bound couldn't be read either: the whole couldn't be read "
        "(read in two passes, as --parts asks: the whole met the container's memory), and with "
        "frame's glass band left out, nothing of the assembly is read: CalculiX met it"
    ), text
