"""Specification for `src/slicer/orca.py`.

Two halves. `parse_duration` is pure and always runs. The slicing tests shell
out to a real OrcaSlicer binary and are skipped when it is not installed —
marked `slicer` so they can be deselected with `-m "not slicer"`.

There is no fake slicer and no recorded fixture on purpose. A stub that returns
plausible G-code is exactly what `PROJECT.md` learning 2 is about: it would pass
forever without ever telling us the real invocation had broken.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from print_shop.slicer.orca import SliceResult, SlicerProfiles, parse_duration, slice_model
from tests.fixtures import write_stl

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
DATADIR = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
_SYS = DATADIR / "system" / "Elegoo"
MACHINE = _SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json"
PROCESS = _SYS / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json"
FILAMENT = _SYS / "filament" / "ECC" / "Elegoo PLA @ECC.json"

needs_orca = pytest.mark.skipif(
    not (ORCA.exists() and MACHINE.exists() and PROCESS.exists()),
    reason="OrcaSlicer or the Elegoo Centauri Carbon profiles are not installed",
)


# --------------------------------------------------------------------------
# Duration parsing — pure, always runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("45s", 45),
        ("22m 12s", 1332),
        ("14m 10s", 850),
        ("1h 2m 3s", 3723),
        ("2h", 7200),
        ("1d 3h 4m 5s", 97445),
        ("3m", 180),
    ],
)
def test_parses_orca_durations(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "soon", "12"])
def test_rejects_unparseable_durations(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


# --------------------------------------------------------------------------
# Real slicing
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profiles() -> SlicerProfiles:
    return SlicerProfiles(
        machine=MACHINE,
        process=PROCESS,
        filament=FILAMENT if FILAMENT.exists() else None,
        datadir=DATADIR,
    )


@pytest.fixture(scope="module")
def sliced(profiles: SlicerProfiles, tmp_path_factory: pytest.TempPathFactory) -> SliceResult:
    d = tmp_path_factory.mktemp("slice")
    return slice_model(write_stl("tee", d), profiles, d / "out")


@pytest.mark.slicer
@needs_orca
def test_produces_a_gcode_file(sliced: SliceResult) -> None:
    assert sliced.gcode_path.exists()
    assert sliced.gcode_path.suffix == ".gcode"
    assert sliced.gcode_path.stat().st_size > 10_000


@pytest.mark.slicer
@needs_orca
def test_the_gcode_actually_commands_the_printer(sliced: SliceResult) -> None:
    """The previous slicer validator checked for M104/M109 and it was right to.

    An empty or failed slice still writes a file.
    """
    text = sliced.gcode_path.read_text()
    assert any(cmd in text for cmd in ("M104", "M109")), "no nozzle temperature command"
    assert any(cmd in text for cmd in ("M140", "M190")), "no bed temperature command"
    assert "G1" in text, "no movement commands"


@pytest.mark.slicer
@needs_orca
def test_reports_geometry_matching_the_model(sliced: SliceResult) -> None:
    """The tee is 20 mm tall at 0.2 mm layers: 100 layers."""
    assert sliced.layer_count == 100
    assert sliced.max_z_mm == pytest.approx(20.0, abs=0.05)


@pytest.mark.slicer
@needs_orca
def test_reports_the_numbers_orientation_will_be_scored_on(sliced: SliceResult) -> None:
    """Print time and filament volume are the orientation fitness function."""
    assert 0 < sliced.print_time_s < 3600
    assert sliced.filament_cm3 == pytest.approx(2.39, abs=0.2)
    assert sliced.filament_mm > 0


@pytest.mark.slicer
@needs_orca
def test_grams_are_none_when_density_is_unknown(sliced: SliceResult) -> None:
    """Orca reports `total filament used [g] = 0.00` when filament_density is 0.

    Zero grams of filament is not a measurement, it is a missing one. Report
    None rather than a number nothing downstream can tell is wrong — the same
    call made for `MeshReport.volume_mm3`.
    """
    assert sliced.filament_g is None or sliced.filament_g > 0


@pytest.mark.slicer
@needs_orca
def test_a_failed_slice_raises_rather_than_returning_empty(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    bad = tmp_path / "not-a-mesh.stl"
    bad.write_text("this is not an STL")
    with pytest.raises(RuntimeError):
        slice_model(bad, profiles, tmp_path / "out")


def test_missing_binary_raises_clearly(profiles: SlicerProfiles, tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    with pytest.raises(FileNotFoundError):
        slice_model(stl, profiles, tmp_path / "out", binary=tmp_path / "no-such-slicer")


def test_orca_is_findable_if_installed() -> None:
    """Documents where we look, so a missing binary is diagnosable."""
    assert ORCA.exists() or shutil.which("orca-slicer") is None


# --------------------------------------------------------------------------
# Robustness — found in review, not in the original spec
# --------------------------------------------------------------------------


def _fake_slicer(tmp_path: Path, script: str) -> Path:
    """A stand-in binary, so failure modes can be tested without hanging Orca."""
    p = tmp_path / "fake-slicer"
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(0o755)
    return p


def test_a_hung_slicer_times_out(profiles: SlicerProfiles, tmp_path: Path) -> None:
    """Without a timeout a wedged slicer blocks the pipeline forever.

    This orchestrates prints that run for hours; "wait indefinitely" is not an
    acceptable failure mode for a subprocess.
    """
    stl = write_stl("cube", tmp_path)
    slow = _fake_slicer(tmp_path, "sleep 30\n")
    with pytest.raises(RuntimeError, match="(?i)time"):
        slice_model(stl, profiles, tmp_path / "out", binary=slow, timeout_s=1.0)


def test_stale_gcode_is_not_mistaken_for_a_fresh_slice(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """A slicer that exits 0 without writing must not return the previous run."""
    stl = write_stl("cube", tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    (out / "plate_1.gcode").write_text("; stale output from an earlier run\n")
    noop = _fake_slicer(tmp_path, "exit 0\n")
    with pytest.raises(RuntimeError):
        slice_model(stl, profiles, out, binary=noop)


def test_a_timeout_does_not_orphan_the_slicers_children(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """Killing the child is not enough; its children must die too.

    `subprocess.run` kills only the process it started. OrcaSlicer is an app
    bundle that may spawn helpers, so a timed-out slice can leave work running
    on a machine that is orchestrating multi-hour prints. Observed before the
    fix: every timeout left a live grandchild behind.
    """
    import os
    import signal
    import time

    stl = write_stl("cube", tmp_path)
    pidfile = tmp_path / "grandchild.pid"
    spawner = _fake_slicer(
        tmp_path,
        f"sleep 300 &\necho $! > {pidfile}\nwait\n",
    )

    with pytest.raises(RuntimeError, match="(?i)time"):
        slice_model(stl, profiles, tmp_path / "out", binary=spawner, timeout_s=1.0)

    deadline = time.time() + 5
    pid = int(pidfile.read_text().strip())
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return  # gone, as it should be
        time.sleep(0.1)

    os.kill(pid, signal.SIGKILL)  # don't leak it from the test either
    pytest.fail(f"grandchild {pid} survived the timeout")
