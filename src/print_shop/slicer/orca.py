"""OrcaSlicer subprocess wrapper.

Orca is a process boundary (CONTRACTS.md §1): we invoke the CLI binary,
read the G-code it writes to local disk, and parse the header comments.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from print_shop.core.state import SliceResult

__all__ = [
    "SliceResult",
    "Slicer",
    "SlicerProfiles",
    "parse_config_block",
    "parse_duration",
    "parse_gcode_header",
    "slice_model",
]

_DEFAULT_BINARY = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")

_DURATION_RE = re.compile(r"(\d+)\s*(d|h|m|s)")


def parse_duration(text: str) -> int:
    """Convert Orca's duration string (e.g. ``1h 2m 3s``) to total seconds.

    Raises:
        ValueError: If no recognisable unit (d, h, m, s) is found.
    """
    matches = _DURATION_RE.findall(text)
    if not matches:
        raise ValueError(f"Unrecognised duration format: {text!r}")

    total = 0
    for value, unit in matches:
        n = int(value)
        if unit == "d":
            total += n * 86400
        elif unit == "h":
            total += n * 3600
        elif unit == "m":
            total += n * 60
        elif unit == "s":
            total += n
    return total


class SlicerProfiles(BaseModel):
    """Paths to OrcaSlicer profile files."""

    machine: Path
    process: Path
    filament: Path | None = None
    datadir: Path | None = None


class Slicer(Protocol):
    """What the slice node calls. `slice_model` is the real one.

    A seam so CI can replay a G-code the real Orca wrote once, through the
    real header parser, on a runner that has no Orca.
    """

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult: ...




def _first_value(raw: str) -> str:
    """Take the first entry of an Orca settings value, unquoted.

    Per-extruder and per-filament settings are written as a delimited list
    (``nozzle_diameter = 0.4,0.4``, ``filament_type = "PLA";"PETG"``). The CC1
    is single-extruder, so the first entry is the one that describes the file;
    a multi-material job is a backlog item and will need more than a filename.
    """
    return re.split(r"[;,]", raw.strip())[0].strip().strip('"')


def parse_gcode_header(gcode_path: Path) -> SliceResult:
    """Extract the metrics and settings Orca records in the G-code it writes.

    The metrics live in the header block at the top; the settings Orca used
    live in a footer at the bottom. Both are ``;`` comments and this scans the
    whole file once for either.

    Raises:
        RuntimeError: If any metric the pipeline depends on is absent. The
            settings fields are optional — they name the file, they do not
            gate the job.
    """
    text = gcode_path.read_text()

    layer_count: int | None = None
    max_z: float | None = None
    filament_mm: float | None = None
    filament_cm3: float | None = None
    filament_g: float | None = None
    print_time_s: int | None = None
    nozzle_mm: float | None = None
    material: str | None = None
    layer_height_mm: float | None = None

    for line in text.splitlines():
        if not line.startswith(";"):
            continue
        if line.startswith("; total layer number:"):
            layer_count = int(line.split(":", 1)[1].strip())
        elif line.startswith("; max_z_height:"):
            max_z = float(line.split(":", 1)[1].strip())
        elif line.startswith("; filament used [mm] ="):
            filament_mm = float(line.split("=", 1)[1].strip())
        elif line.startswith("; filament used [cm3] ="):
            filament_cm3 = float(line.split("=", 1)[1].strip())
        elif line.startswith("; total filament used [g] ="):
            filament_g = float(line.split("=", 1)[1].strip())
        elif line.startswith("; estimated printing time (normal mode) ="):
            raw = line.split("=", 1)[1].strip()
            print_time_s = parse_duration(raw)
        elif line.startswith("; nozzle_diameter ="):
            with contextlib.suppress(ValueError):
                nozzle_mm = float(_first_value(line.split("=", 1)[1]))
        elif line.startswith("; layer_height ="):
            with contextlib.suppress(ValueError):
                layer_height_mm = float(_first_value(line.split("=", 1)[1]))
        elif line.startswith("; filament_type ="):
            material = _first_value(line.split("=", 1)[1]) or None

    missing = []
    if layer_count is None:
        missing.append("total layer number")
    if max_z is None:
        missing.append("max_z_height")
    if filament_mm is None:
        missing.append("filament used [mm]")
    if filament_cm3 is None:
        missing.append("filament used [cm3]")
    if print_time_s is None:
        missing.append("estimated printing time")

    if missing:
        raise RuntimeError(
            f"OrcaSlicer G-code header missing expected fields: {', '.join(missing)}"
        )

    # Zero grams means density was unknown — treat as missing, not a measurement.
    if filament_g is not None and filament_g == 0.0:
        filament_g = None

    assert layer_count is not None
    assert max_z is not None
    assert filament_mm is not None
    assert filament_cm3 is not None
    assert print_time_s is not None
    return SliceResult(
        gcode_path=gcode_path,
        layer_count=layer_count,
        max_z_mm=max_z,
        filament_mm=filament_mm,
        filament_cm3=filament_cm3,
        filament_g=filament_g,
        print_time_s=print_time_s,
        nozzle_mm=nozzle_mm,
        material=material,
        layer_height_mm=layer_height_mm,
    )


def parse_config_block(gcode_path: Path) -> dict[str, str]:
    """Every setting Orca sliced with, from the G-code's config footer.

    Orca writes them between ``; CONFIG_BLOCK_START`` and ``; CONFIG_BLOCK_END``
    as ``; key = value``. Values are kept as Orca wrote them, unquoted; the
    ledger stores the whole block, so a setting nobody thought to name today
    is still there when a symptom needs explaining.

    Raises:
        RuntimeError: If the file has no config block. A slice without one
            cannot be recorded, and recording it without settings would be a
            ledger row that cannot answer the question it exists for.
    """
    settings: dict[str, str] = {}
    inside = False
    for line in gcode_path.read_text().splitlines():
        if line == "; CONFIG_BLOCK_START":
            inside = True
        elif line == "; CONFIG_BLOCK_END":
            if not settings:
                break
            return settings
        elif inside and line.startswith("; ") and " = " in line:
            key, _, value = line[2:].partition(" = ")
            settings[key.strip()] = value.strip().strip('"')
    raise RuntimeError(f"{gcode_path.name} has no Orca config block")


def slice_model(
    stl_path: Path | str,
    profiles: SlicerProfiles,
    outdir: Path | str,
    binary: Path | str | None = None,
    timeout_s: float = 600.0,
) -> SliceResult:
    """Slice an STL model using OrcaSlicer and return parsed results.

    Args:
        stl_path: Path to the input STL file.
        profiles: Slicer profile paths (machine, process, optional filament, datadir).
        outdir: Directory where OrcaSlicer will write the G-code.
        binary: Path to the OrcaSlicer binary. Defaults to the macOS app path.
        timeout_s: Seconds to wait for the slicer before giving up.

    Returns:
        A SliceResult with parsed G-code header metrics.

    Raises:
        FileNotFoundError: If the OrcaSlicer binary does not exist.
        RuntimeError: If OrcaSlicer exits non-zero, times out, or does not
            produce a G-code file.
    """
    binary_path = Path(binary) if binary is not None else _DEFAULT_BINARY
    if not binary_path.exists():
        raise FileNotFoundError(f"OrcaSlicer binary not found: {binary_path}")

    stl = Path(stl_path)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)

    # A slicer that exits 0 without writing must not leave a previous run's
    # G-code in place to be mistaken for a fresh slice.
    stale = out / "plate_1.gcode"
    if stale.exists():
        stale.unlink()

    cmd: list[str] = [str(binary_path)]

    if profiles.datadir is not None:
        cmd += ["--datadir", str(profiles.datadir)]

    settings = f"{profiles.machine};{profiles.process}"
    cmd += ["--load-settings", settings]

    if profiles.filament is not None:
        cmd += ["--load-filaments", str(profiles.filament)]

    cmd += ["--slice", "0", "--outputdir", str(out), str(stl)]

    # Run in its own process group so a timeout can kill the whole tree,
    # not just the process we started (Orca may spawn helpers).
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()  # reap so the child does not become a zombie
        raise RuntimeError(f"OrcaSlicer timeout after {timeout_s}s") from exc
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"OrcaSlicer exited with code {result.returncode}:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    gcode_path = out / "plate_1.gcode"
    if not gcode_path.exists():
        raise RuntimeError(
            f"OrcaSlicer exited 0 but did not write {gcode_path}. "
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    return parse_gcode_header(gcode_path)
