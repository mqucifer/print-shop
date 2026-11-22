"""A pause height means one thing from the bench to the G-code (mqucifer/print-shop#203).

It's the top of the last layer printed before the pause. A made-up plate sliced with the bench's
height once had its `M600` a layer early, so the walls stood level with what was dropped in.
Every plate, job and profile change here is made up. Without a printer, and, but for the test
marked `slicer`, without Orca or the bench's image.
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
import trimesh

from print_shop.bench import host, pausing
from print_shop.core.mqtt import command_topic
from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup.context import Fit, FitBand, Placed
from print_shop.setup.proposal import PAUSE, Change, Proposal, Source, apply, pause_z
from print_shop.setup.reading import (
    PAUSE_PAST_FIT,
    LayerTime,
    PauseReading,
    SeamReading,
    SliceReading,
    flags,
)
from print_shop.setup.review import ReviewRequest, parse_answer, render
from print_shop.slicer.orca import _DEFAULT_BINARY
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge
from tests.test_job_e2e import CFG
from tests.test_review import _model, _questions
from tests.test_runtime import settings  # noqa: F401  (the fixture)

GRIDS = [(0.2, 0.2), (0.25, 0.2), (0.3, 0.2), (0.2, 0.16), (0.2, 0.12), (0.2, 0.28),
         (0.24, 0.24), (0.1, 0.0875)]


# The height Orca is given
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "height"), GRIDS)
def test_every_layers_top_as_its_said_gives_orca_the_next_layers(
    first: float, height: float
) -> None:
    """Up a 120 mm part, the top typed as the bench says it, to a thousandth."""
    process = {"initial_layer_print_height": str(first), "layer_height": str(height)}
    for n in range(int((120 - first) / height)):
        typed = float(pausing.mm(first + n * height))
        assert pause_z(process, typed) == pytest.approx(first + (n + 1) * height, abs=1e-3), n


def test_a_layer_height_changed_beside_a_pause_moves_the_layer_after_it(tmp_path: Path) -> None:
    """Whichever was typed first."""
    process = tmp_path / "process.json"
    process.write_text(json.dumps({
        **json.loads(CI_PROFILES.process.read_text()),
        "initial_layer_print_height": "0.2", "layer_height": "0.2",
    }))
    profiles = CI_PROFILES.model_copy(update={"process": process})
    pause = _change(PAUSE, "2.6")
    finer = _change("layer_height", "0.16")
    for n, changes in enumerate(((pause, finer), (finer, pause))):
        applied = apply(profiles, Proposal(changes=changes), tmp_path / str(n))
        assert [p.print_z for p in applied.pauses] == [2.76]


# At the review
# --------------------------------------------------------------------------


def _change(key: str, value: str) -> Change:
    return Change(key=key, preset="", value=value, reason="set the parts in",
                  source=Source.SPONSOR)


def _reading(*before: int) -> SliceReading:
    """30 layers of 0.2 mm, with a pause before each layer named."""
    layers = tuple(LayerTime(n=n, z=round(0.2 * n, 3), time_s=10.0) for n in range(1, 31))
    pauses = tuple(
        PauseReading(before_layer=n, z=layers[n - 1].z, after_s=10.0 * (n - 1),
                     after_z=layers[n - 2].z if n > 1 else None)
        for n in before
    )
    return SliceReading(layers=layers, features=(), seam=SeamReading(seams=0, stacked=0.0),
                        pauses=pauses)


def _request(asked: str | None, reading: SliceReading | None) -> ReviewRequest:
    return ReviewRequest(
        presets=("m", "p", "f"),
        proposal=Proposal(changes=(_change(PAUSE, asked),) if asked else ()),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=30, max_z_mm=6.0,
                          filament_mm=1, filament_cm3=1, filament_g=1, print_time_s=300),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat"),
        profiles=CI_PROFILES, process={"wall_loops": "2"}, reading=reading,
    )


def _pause_lines(request: ReviewRequest) -> list[str]:
    """The review's own line on where the pause landed."""
    return [line.strip() for line in render(request).splitlines()
            if re.match(r"  (PAUSE |pause_at=\S+, read from)", line)]


def test_the_review_says_where_it_landed_beside_the_height_asked_for() -> None:
    assert _pause_lines(_request("2.6", _reading(14))) == [
        "pause_at=2.6, read from the G-code: "
        "pause after layer 13, at z=2.6, before layer 14, at z=2.8"
    ]


@pytest.mark.parametrize(("asked", "reading", "said"), [
    ("2.6", _reading(13), "PAUSE MISMATCH pause_at=2.6: layer 13 tops out at z=2.6, and the "
     "G-code has: pause after layer 12, at z=2.4, before layer 13, at z=2.6"),
    ("2.5", _reading(13), "PAUSE MISMATCH pause_at=2.5: no layer tops out at z=2.5, and the "
     "G-code has: pause after layer 12, at z=2.4, before layer 13, at z=2.6: "
     "type pause_at=2.4 or pause_at=2.6"),
    ("5.9", _reading(30), "PAUSE MISMATCH pause_at=5.9: no layer tops out at z=5.9, and the "
     "G-code has: pause after layer 29, at z=5.8, before layer 30, at z=6: type pause_at=5.8"),
    ("2.6", _reading(), "PAUSE MISMATCH pause_at=2.6: layer 13 tops out at z=2.6, and the "
     "G-code has: no pause"),
    ("2.6", _reading(14, 20), "PAUSE MISMATCH pause_at=2.6: layer 13 tops out at z=2.6, and the "
     "G-code has: pause after layer 13, at z=2.6, before layer 14, at z=2.8; "
     "pause after layer 19, at z=3.8, before layer 20, at z=4"),
    ("0.1", _reading(1), "PAUSE MISMATCH pause_at=0.1: no layer tops out at z=0.1, and the "
     "G-code has: pause before layer 1, at z=0.2: type pause_at=0.2"),
    ("none", _reading(14), "PAUSE MISMATCH: none was asked for, and the G-code has: "
     "pause after layer 13, at z=2.6, before layer 14, at z=2.8"),
    ("2.6", None, "PAUSE NOT READ pause_at=2.6: the G-code couldn't be read, so where it pauses "
     "isn't known"),
])
def test_a_mismatch_is_said_with_what_the_gcode_has(
    asked: str, reading: SliceReading | None, said: str
) -> None:
    assert _pause_lines(_request(asked, reading)) == [said]


def test_on_a_raft_a_height_in_the_gcode_is_never_called_the_parts() -> None:
    """Real Orca put this pause at z 2.6 on a two-layer raft: lower than that up the part."""
    raft = _reading(14).model_copy(update={"settings": {"raft_layers": "2"}})
    assert _pause_lines(_request("2.6", raft)) == [
        "PAUSE MISMATCH pause_at=2.6: the part stands on a raft, so z in the G-code counts the "
        "raft too, and the G-code has: pause after layer 13, at z=2.6, before layer 14, at z=2.8"
    ]
    flat = _reading(14).model_copy(update={"settings": {"raft_layers": "0"}})
    assert "PAUSE MISMATCH" not in render(_request("2.6", flat))


@pytest.mark.parametrize("asked", [None, "none"])
def test_no_pause_asked_for_and_none_written_says_nothing(asked: str | None) -> None:
    assert _pause_lines(_request(asked, _reading())) == []


def test_a_height_under_the_first_layers_top_has_no_layer_to_pause_after() -> None:
    said = parse_answer("pause_at=0.1", _request(None, _reading()))
    assert said == "there's no layer to pause after at 0.1 mm: the first tops out at 0.2 mm"
    assert not isinstance(parse_answer("pause_at=0.2", _request(None, _reading())), str)


# A height typed a layer up, as it had to be before
# --------------------------------------------------------------------------


def _fits(*tops: float) -> Placed:
    return Placed(fits=tuple(
        FitBand(fit=Fit(name=f"pocket {n}"), z_from=1.0, z_to=top)
        for n, top in enumerate(tops, 1)
    ))


def test_a_pause_a_layer_past_a_fits_top_is_said_beside_where_it_landed() -> None:
    """A pocket topping out at 2.6, and 2.8 typed, as it once had to be: the layer over the
    pocket has printed by the pause. It's a note, never a refusal."""
    reading = _reading(15)
    found = flags(reading, None, {}, {}, _fits(2.6, 2.6))
    [note] = [f for f in found if f.id == PAUSE_PAST_FIT]
    assert note.change is None
    said = ("the fit `pocket 1` tops out at z=2.6, and this pauses a layer later, once the "
            "layer over it has printed: pause_at=2.6 pauses before that layer")
    assert note.reading == said

    request = replace(_request("2.8", reading), flags=found)
    lines = render(request).splitlines()
    at = lines.index(f"  note: {said}")
    assert lines[at - 1].startswith("  pause_at=2.8, read from the G-code: ")
    assert lines.count(f"  note: {said}") == 1
    assert not isinstance(parse_answer("ok", request), str), "it can still be approved"


@pytest.mark.parametrize(("before", "top", "said"), [
    (15, 2.6, True),    # a layer past a top on a layer line
    (15, 2.65, True),   # under the middle of the layer to 2.8, which closes over it
    (14, 2.6, False),   # at the top
    (15, 2.75, False),  # over that layer's middle: it doesn't close over it
    (14, 2.8, False),   # before the top
    (20, 2.6, False),   # well above it: a colour change, or another pause
])
def test_only_a_pause_a_layer_past_the_top_is_said(before: int, top: float, said: bool) -> None:
    found = flags(_reading(before), None, {}, {}, _fits(top))
    assert any(f.id == PAUSE_PAST_FIT for f in found) is said


def test_no_fit_and_no_height_to_compare_say_nothing() -> None:
    raft = _reading(15).model_copy(update={"settings": {"raft_layers": "2"}})
    for reading, placed in ((_reading(15), None), (_reading(15), Placed()), (raft, _fits(2.6))):
        assert not any(f.id == PAUSE_PAST_FIT for f in flags(reading, None, {}, {}, placed))


# From the bench, through print_job.py, to the G-code
# --------------------------------------------------------------------------


def _print_job() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plate(folder: Path, ceiling: float, base: float) -> Path:
    """40 mm square and 6 tall: a 26 mm window, a 30 mm pocket round it from 1 to `ceiling`,
    and its underside at `base` in the file."""

    def box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> Any:
        made = trimesh.creation.box(extents=(x1 - x0, y1 - y0, z1 - z0))
        made.apply_translation(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        return made

    plate = box(0, 0, 0, 40, 40, 6).difference(box(7, 7, -1, 33, 33, 7))
    plate = plate.difference(box(5, 5, 1, 35, 35, ceiling))
    plate.apply_translation((0, 0, base))
    path = folder / "plate.stl"
    plate.export(path)
    return path


def _m600(gcode: str) -> list[tuple[int, float, int, float, bool]]:
    """Each M600, from the G-code itself and not print-shop's reader: the layer it comes after
    and its top, the layer it comes before and its top, and whether that one had laid any yet."""
    tops: list[float] = []
    found = []
    printing = laid = False
    for line in gcode.splitlines():
        if line.startswith(";Z:"):
            tops.append(float(line[3:]))
            printing = laid = False
        elif line.startswith(";TYPE:"):
            printing = line != ";TYPE:Custom"
        elif line.strip() == "M600":
            found.append((len(tops) - 1, tops[-2], len(tops), tops[-1], laid))
        elif printing and re.match(r"G1 .*[XY].* E\.?\d", line):
            laid = True
    return found


@pytest.mark.slicer
@pytest.mark.skipif(not _DEFAULT_BINARY.exists(), reason="no OrcaSlicer on this machine")
@pytest.mark.parametrize(("first", "height", "ceiling", "base", "layer"), [
    (0.2, 0.2, 2.6, 0.0, 13),
    (0.25, 0.16, 2.65, 5.0, 16),  # a thicker first layer, and the file's underside at z 5
])
def test_the_benchs_pause_height_through_print_job_puts_the_m600_after_that_layer(
    first: float, height: float, ceiling: float, base: float, layer: int, tmp_path: Path,
    settings: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The real bench and Orca, and `print_job.py` itself with its run built on fakes: the
    height the bench says, typed at the review, and the gate rejected."""
    if host.image_id() is None:
        pytest.skip(f"no bench image {host.IMAGE}")
    part = _plate(tmp_path, ceiling, base)
    done = host.run(["pause", part.name, "--pocket-z", f"{base + (1 + ceiling) / 2:g}",
                     "--pane-thickness", f"{ceiling - 1.1:g}", "--layer", f"{height:g}",
                     "--first-layer", f"{first:g}"], tmp_path)
    benchs = re.search(r"verdict: OK: (pause after layer (\d+), at z=([\d.]+), "
                       r"before layer \d+, at z=[\d.]*\d)", done.stdout)
    assert benchs, done.stdout + done.stderr
    said, at, typed = benchs.groups()
    assert (int(at), float(typed)) == (layer, ceiling), "above the underside, as it prints"

    script = _print_job()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    bridge = MemoryBridge()
    sliced: list[Path] = []
    finished = threading.Event()

    def build_run(**run: Any) -> SimpleNamespace:
        assert run["printer"] is None, "never armed"
        ledger = Ledger(tmp_path / "ledger.sqlite")
        daemon = Daemon(cfg=CFG, profiles=run["profiles"], workdir=jobs, printer=FramePrinter(),
                        bridge=lambda cfg: bridge, ledger=ledger,
                        proposer=Proposer(ask=_model, ask_questions=_questions),
                        asker=run["asker"], reviewer=run["reviewer"])

        def reject() -> None:
            while not finished.is_set():
                gate = daemon.pending_gate()
                if gate:
                    sliced.append(Path(str(gate["gcode_path"])))
                    # A press names its job (mqucifer/print-shop#207).
                    bridge.deliver(command_topic(CFG),
                                   {"decision": "reject", "job_id": gate["job_id"]})
                    return
                time.sleep(0.05)

        threading.Thread(target=reject, daemon=True).start()
        return SimpleNamespace(daemon=daemon, ledger=ledger)

    typed_lines = iter((
        f"initial_layer_print_height={first:g}; layer_height={height:g}; enable_support=0; "
        f"pause_at={typed} because set the parts in",
        "ok",
    ))
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
    monkeypatch.setattr(script, "WORK_DIR", jobs)
    monkeypatch.setattr(script, "build_run", build_run)
    # The review's prompt takes the lines above; each question takes its first option.
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": next(typed_lines) if prompt == "> " else "1")
    try:
        script.main([str(part), "--nozzle", "0.4", "--filament", "Elegoo PLA", "--as-laid",
                     "--settings", str(settings)])
    finally:
        finished.set()

    [gcode] = sliced
    [(after, after_z, before, before_z, laid)] = _m600(gcode.read_text())
    assert (after, before, laid) == (layer, layer + 1, False)
    assert (after_z, before_z) == pytest.approx((ceiling, ceiling + height), abs=1e-3)

    # The bench's words, the review's, and the gate's are one.
    shown = capsys.readouterr().out
    assert f"pause_at={typed}, read from the G-code: {said}" in shown
    assert "PAUSE MISMATCH" not in shown
    gate = re.search(r"SWAP +(this print pauses for a filament swap .*)", shown)
    assert gate and said.removeprefix("pause ") in gate.group(1)
