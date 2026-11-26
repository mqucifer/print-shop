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
    # `--parts` as a word of another command isn't `load`'s.
    assert host.OFFER not in _ran_under(monkeypatch, tmp_path, ["clearance", "--parts"])
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
    path = tmp_path / "assembly.json"
    path.write_text(json.dumps(data))
    return str(path)


def _plain_load(monkeypatch: pytest.MonkeyPatch, limit: str = "600") -> None:
    """The container as the host starts a plain `load`: its limit, and the hour offered."""
    monkeypatch.setenv(load_parts.TIMEOUT, limit)
    monkeypatch.setenv(load_parts.OFFER, str(host.TWO_PASSES))
    monkeypatch.setattr(load, "_clock", {"now": None})


def _one_pass_reading_bolts(
    monkeypatch: pytest.MonkeyPatch, bolt: float, calls: list[dict[str, Any]]
) -> None:
    """`load._run` stood in for, as it reads the one pass: it starts the one pass's clock as
    `_run` does, reads the whole in 30 s, then a bolt that took `bolt` s, then asks before the
    next; and in two passes, says what it was given. No FreeCAD."""
    from print_shop.bench.assembly import read

    def run(
        args: Any, report: Report, without_bands: bool = False, two_pass: str | None = None,
        reserve: float = 0.0, tried: float = 0.0,
    ) -> None:  # fmt: skip
        calls.append({"two_pass": two_pass, "tried": tried, "limit": load_parts.Clock(0).limit})
        if two_pass is None:
            load._one_pass(read(args.assembly), report.started)
            load._timed("whole", 30.0)
            load._in_time("bolt", "the reading round p0+p1's first bolt")
            load._timed("bolt", bolt)
            load._in_time("bolt", "the reading round p0+p1's second bolt")
            report.verdict("OK", "read in one pass")
            return
        report.numbers["two_passes"] = {"why": two_pass}
        report.verdict("OK", "read in two passes")

    monkeypatch.setattr(load, "_run", run)


def _args(path: str, parts: bool = False) -> Any:
    import argparse

    return argparse.Namespace(assembly=path, parts=parts, lift=None)


def test_a_one_pass_that_fits_the_memory_but_not_its_time_goes_to_two_passes_under_the_hour(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """mqucifer/print-shop#331: the furniture assembly whose one pass ran past 600 s, round
    after round, without meeting the memory. Here its next bolt wouldn't end inside the 600 s,
    so the one pass stops itself before it and goes on to two passes, without being asked:
    they take up the hour, counted from the command's start, and the reply says why."""
    import time

    _plain_load(monkeypatch)
    calls: list[dict[str, Any]] = []
    _one_pass_reading_bolts(monkeypatch, 1e4, calls)
    report = Report("load", ["assembly.json"])
    report.started = time.time() - 100
    load.run(_args(_assembly(tmp_path)), report)
    assert [c["two_pass"] is None for c in calls] == [True, False]
    why = calls[1]["two_pass"]
    assert why.startswith("the one pass wouldn't end inside the container's 600 s: it had run ")
    assert why.endswith(" s, before the reading round p0+p1's second bolt, which would end past it")
    assert calls[1]["limit"] == host.TWO_PASSES, "the hour, taken up"
    assert calls[1]["tried"] == 30.0, "a whole takes at most what the one pass's whole took"
    assert capsys.readouterr().err == f"{host.ABLE}\n{host.TAKEN}\n"
    assert report.numbers["two_passes"]["one_pass_late"] == why
    assert report.started == pytest.approx(time.time() - 100, abs=5), "the hour counts the try"
    assert load._clock["now"] is None


def test_a_one_pass_stopped_in_its_solve_goes_to_two_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Where the one pass's solve of the whole runs to the container's time, less its margin,
    CalculiX is stopped there (`cae.ccx_one_thread`'s timeout), and that's said the same way.
    A real process stands in for CalculiX; FreeCAD's part of it is in the proof run."""
    import time

    from print_shop.bench import cae

    _plain_load(monkeypatch, limit="21.5")
    clock = load._OnePass(time.time(), 21.5)
    load._clock["now"] = clock
    left = load._ccx_left()
    assert left is not None and 0.5 < left <= 1.5, "the limit less the margin"
    ccx = tmp_path / "ccx"
    ccx.write_text("#!/bin/sh\nsleep 30\n")
    ccx.chmod(0o755)

    class Fea:
        ccx_binary = str(ccx)
        inp_file_name = str(tmp_path / "solve.inp")

        def setup_ccx(self) -> None:
            pass

    began = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        cae.ccx_one_thread(Fea(), timeout=left)
    assert time.time() - began < 10, "stopped at what's left, not left to run"
    late = clock.late("in CalculiX's solve of the whole at 4 mm")
    assert isinstance(late, load._Unread) and not late.memory
    said = r"21\.5 s: it had run \d+ s, in CalculiX's solve of the whole at 4 mm$"
    assert re.search(said, str(late))
    # Two passes' own clock, or no clock, gives a solve no timeout: as on main.
    load._clock["now"] = load_parts.Clock(time.time())
    assert load._ccx_left() is None
    load._clock["now"] = None
    assert load._ccx_left() is None


def test_a_one_pass_that_reads_inside_its_time_reads_as_on_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Its bolts each well inside what's left: read in one pass, never two, under 600 s, and
    the hour isn't taken up. Only the line that tells the host two passes could read it is
    said, and the host doesn't pass that on."""
    _plain_load(monkeypatch)
    calls: list[dict[str, Any]] = []
    _one_pass_reading_bolts(monkeypatch, 2.0, calls)
    report = Report("load", ["assembly.json"])
    load.run(_args(_assembly(tmp_path)), report)
    assert [c["two_pass"] for c in calls] == [None]
    assert report.word == "OK" and "two_passes" not in report.numbers
    assert capsys.readouterr().err == f"{host.ABLE}\n"
    assert load_parts.Clock(0).limit == 600 and load_parts.OFFER in __import__("os").environ


@pytest.mark.parametrize(
    ("parts", "offered", "limit"),
    [(1, True, "600"), (2, False, "600"), (2, True, None)],
    ids=["a single part", "not offered the hour", "no limit given"],
)
def test_a_load_that_can_t_go_to_two_passes_keeps_its_limit_and_says_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    parts: int,
    offered: bool,
    limit: str | None,
) -> None:
    """A single part, a script or a limit the caller gave (nothing offered), and a command with
    no limit: no clock in the one pass, so it's never stopped early, and the host isn't told two
    passes could read it. A single part past 600 s is stopped there, its reply as now."""
    _plain_load(monkeypatch)
    if not offered:
        monkeypatch.delenv(load_parts.OFFER)
    if limit is None:
        monkeypatch.delenv(load_parts.TIMEOUT)
    calls: list[dict[str, Any]] = []
    _one_pass_reading_bolts(monkeypatch, 1e4, calls)
    report = Report("load", ["assembly.json"])
    load.run(_args(_assembly(tmp_path, parts)), report)
    assert [c["two_pass"] for c in calls] == [None] and report.word == "OK"
    assert capsys.readouterr().err == ""
    assert host.past(600, False) == (
        "verdict: NO ANSWER: it ran past the 600 s limit and was stopped\n"
    )


def test_a_one_pass_the_host_stops_names_parts_where_two_passes_could_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Past what the one pass's clock foresees, as a mesh that runs past the limit, the host
    stops it at 600 s. Where it said two passes could read it, and they weren't started, the
    reply names `--parts` and the hour; a single part's reply, and two passes' past the hour,
    are as on main. Real processes, with limits of seconds."""
    able = f"import sys, time; print({host.ABLE!r}, file=sys.stderr, flush=True); "
    taken = f"print({host.TAKEN!r}, file=sys.stderr, flush=True); "
    stopped, limit, could = host._waited(_child(able + "time.sleep(30)"), 1, 30)
    assert stopped is None and limit == 1 and could
    stopped, limit, could = host._waited(_child(able + taken + "time.sleep(30)"), 1, 2)
    assert stopped is None and limit == 2 and not could, "two passes ran out of the hour"
    done, _, _ = host._waited(_child(able + "print('read')"), 5, 30)
    assert done is not None and done.stdout == "read\n" and done.stderr == "", "not passed on"
    killed: list[list[str]] = []
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: killed.append(argv))
    monkeypatch.setattr(host, "_waited", lambda argv, timeout, longer: (None, timeout, True))
    said = host.run(["load", "a.json"], tmp_path).stdout
    assert said == (
        "verdict: NO ANSWER: it ran past the 600 s limit and was stopped, in one pass. Two "
        "passes can read this assembly, and are given the hour, 3600 s: run it again with "
        "--parts, which asks for them from the start\n"
    )
    monkeypatch.setattr(host, "_waited", lambda argv, timeout, longer: (None, timeout, False))
    assert host.run(["load", "a.json"], tmp_path).stdout == (
        "verdict: NO ANSWER: it ran past the 600 s limit and was stopped\n"
    )


def test_a_design_round_s_one_pass_stopped_names_parts_where_two_passes_could_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The harness's own wait, where the issue was met: the model read the timeout as the
    bench's limit and waited for the hour. Now the reply it's shown names `--parts`, and the
    line that said two passes could read it isn't in it."""
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
    box, _ = _box(monkeypatch, tmp_path, "import time; time.sleep(3)", timeout=1)
    assert box.bench(["load", "a.json"]).output.endswith(host.past(1, False))


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

