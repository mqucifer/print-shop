"""OrcaSlicer subprocess wrapper.

Orca is a process boundary (CONTRACTS.md §1): we invoke the CLI binary,
read the G-code it writes to local disk, and parse the header comments.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from print_shop.core.state import SliceResult
from print_shop.slicer.store import ProfilesUsed

__all__ = [
    "PauseAt",
    "PlateObject",
    "PlateSlice",
    "pause_items",
    "slice_plate",
    "version",
    "with_colour",
    "BedType",
    "SliceResult",
    "Slicer",
    "SlicerProfiles",
    "parse_config_block",
    "parse_duration",
    "parse_gcode_header",
    "slice_model",
    "with_bed_type",
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
    """Paths to OrcaSlicer profile files, each whole: Orca resolves nothing.

    `used` is what the ledger records of them: the store's files they came
    from, by name and hash, and print-shop's commit. A derived copy
    (`with_bed_type`, `scoring_profiles`) carries it unchanged.
    """

    machine: Path
    process: Path
    filament: Path | None = None
    used: ProfilesUsed | None = None


class BedType(StrEnum):
    """The build plate, as Orca names it in `curr_bed_type`.

    The plate sets the bed temperature: Orca takes it from the filament
    profile's per-plate value (`cool_plate_temp`, `textured_plate_temp`, ...).
    Only the plates whose slices have been read back are listed. The Elegoo
    process profile defaults to the Cool Plate, 35 C for Elegoo PLA; this
    printer has a textured PEI plate (the Sponsor, 2026-10-25, written on
    mqucifer/print-shop#3), which Elegoo's PLA heats to 60 C. Orca refuses
    PETG on the Cool Plate outright (exit 195).
    """

    COOL = "Cool Plate"
    TEXTURED_PEI = "Textured PEI Plate"


def with_bed_type(profiles: SlicerProfiles, bed: BedType, workdir: Path) -> SlicerProfiles:
    """`profiles` with the process profile's plate set to `bed`.

    Orca's command line has no bed-type option, so the process profile is
    copied whole with `curr_bed_type` set, keeping its name and `inherits`.
    A whole copy rather than an overlay, because an overlay names its parent
    by `inherits`, and Orca slices with an empty data directory that has no
    parents in it (`slice_model`).
    """
    base = json.loads(Path(profiles.process).read_text())
    derived = {**base, "curr_bed_type": bed.value}
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"process_{bed.name.lower()}.json"
    out.write_text(json.dumps(derived, indent=2))
    return profiles.model_copy(update={"process": out})


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
        profiles: Slicer profile paths (machine, process, optional filament),
            each a whole profile, as print-shop's store keeps them.
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

    # A throwaway data directory: Orca slices from exactly the files it is
    # given, and never reads or writes the Sponsor's own Orca folders
    # (mqucifer/print-shop#6). It is also the working directory, where Orca
    # leaves a log when an STL won't load.
    scratch = tempfile.TemporaryDirectory(prefix="orca-data-")
    cmd: list[str] = [str(binary_path), "--datadir", scratch.name]

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
        cwd=scratch.name,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()  # reap so the child does not become a zombie
        raise RuntimeError(f"OrcaSlicer timeout after {timeout_s}s") from exc
    finally:
        scratch.cleanup()
    result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    if result.returncode != 0:
        hint = (
            " (239: Orca found the process not compatible with the machine; a whole"
            " machine profile must keep `inherits` naming its system parent)"
            if result.returncode == 239
            else ""
        )
        raise RuntimeError(
            f"OrcaSlicer exited with code {result.returncode}{hint}:\n"
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


#: Orca's CLI exit codes the refinement pass has met, in Orca's words (`cli_errors` in
#: `src/OrcaSlicer.cpp` at v2.4.2), or what the run showed.
EXITS = {
    -11: "Orca crashed (SIGSEGV)",
    155: "G-code paths conflict: two objects take the same space",
    195: "the filament doesn't fit the build plate",
    239: "the process isn't compatible with the machine",
    250: "Orca can't read the file's format",
}


class PauseAt(BaseModel):
    """A pause Orca writes itself, from the machine profile's pause G-code.

    `print_z` is the top of the first layer printed after it. Orca puts a custom G-code item on
    the nearest layer (a tie goes to the lower) and runs it at the start of that layer, before
    the layer prints (`ToolOrdering::assign_custom_gcodes`, v2.4.2; tested at 2.6, 2.7 and 2.8 mm
    in `~/print-shop/proofs/m18-refine/cli-probe/`). So a pause after layer 13 of 0.2 mm layers
    on a 0.2 mm first layer is `print_z` 2.8.
    """

    print_z: float
    message: str = ""


def pause_items(pauses: list[PauseAt]) -> dict[str, Any]:
    """`--load-custom-gcodes`' file: Orca's `CustomGCode::Info`, as its `from_json` reads it."""
    return {
        "mode": "SingleExtruder",
        "gcodes": [
            {"type": "PausePrint", "print_z": p.print_z, "color": "", "extruder": 1,
             "extra": p.message}
            for p in sorted(pauses, key=lambda p: p.print_z)
        ],
    }


class PlateObject(BaseModel):
    """One file on a plate, and which of the plate's filaments it prints in (from 1)."""

    stl: Path
    filament: int = 1


class PlateSlice(BaseModel):
    """What Orca made of one plate: its G-code and log, or why there's none."""

    command: list[str]
    exit: int
    gcode: Path | None
    log: Path
    stdout: str
    stderr: str

    @property
    def why(self) -> str:
        known = EXITS.get(self.exit, "")
        said = (self.stderr.strip() or self.stdout.strip())[-400:]
        return "; ".join(x for x in (f"exit {self.exit}", known, said) if x)


def with_colour(filament: Path, colour: str, out: Path) -> Path:
    """A whole filament profile with its colour set, as Orca reads it (`filament_colour`).

    The store's composed profiles carry none. With two or more filaments and no colour, Orca
    2.4.2 counts one filament, sizes its flush matrix by it, and crashes (SIGSEGV in
    `calc_filament_change_info_by_toolorder`, cli-probe runs o4, o8). The colour is a setting
    Orca reads, so it writes each object's colour itself.
    """
    data = json.loads(filament.read_text())
    data["filament_colour"] = [colour]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    return out


def slice_plate(
    objects: list[PlateObject],
    machine: Path,
    process: Path,
    filaments: list[Path],
    outdir: Path,
    pauses: list[PauseAt] | None = None,
    binary: Path | str | None = None,
    timeout_s: float = 600.0,
) -> PlateSlice:
    """One plate through Orca's CLI, with a filament for each object and Orca's own pauses.

    As `slice_model`: a throwaway data directory, never the Sponsor's own Orca folders. Orca
    arranges the plate itself, and `EXCLUDE_OBJECT_DEFINE` says where each object went. Its log
    (`--debug 3`) is kept, as the macOS CLI writes its warnings nowhere else. A plate that
    doesn't slice isn't raised: what Orca said is the finding.
    """
    binary_path = Path(binary) if binary is not None else _DEFAULT_BINARY
    if not binary_path.exists():
        raise FileNotFoundError(f"OrcaSlicer binary not found: {binary_path}")
    outdir.mkdir(parents=True, exist_ok=True)
    for stale in outdir.glob("plate_*.gcode"):
        stale.unlink()
    log = outdir / "orca.log"
    log.unlink(missing_ok=True)
    scratch = tempfile.TemporaryDirectory(prefix="orca-data-")
    cmd: list[str] = [
        str(binary_path), "--datadir", scratch.name, "--debug", "3", "--logfile", str(log),
        "--load-settings", f"{machine};{process}",
        "--load-filaments", ";".join(str(f) for f in filaments),
        "--load-filament-ids", ",".join(str(o.filament) for o in objects),
    ]
    if pauses:
        items = outdir / "pauses.json"
        items.write_text(json.dumps(pause_items(pauses), indent=1))
        cmd += ["--load-custom-gcodes", str(items)]
    cmd += ["--slice", "0", "--outputdir", str(outdir), *(str(o.stl) for o in objects)]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True, cwd=scratch.name,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
        raise RuntimeError(f"OrcaSlicer timeout after {timeout_s}s") from exc
    finally:
        scratch.cleanup()
    gcode = outdir / "plate_1.gcode"
    shown = [c if c != scratch.name else "<throwaway>" for c in cmd]
    return PlateSlice(
        command=shown,
        exit=proc.returncode,
        gcode=gcode if proc.returncode == 0 and gcode.exists() else None,
        log=log,
        stdout=stdout[-4000:],
        stderr=stderr[-4000:],
    )


def version(binary: Path | str | None = None) -> str:
    """Orca's version, from the first line of its `--help`, which it writes without slicing."""
    binary_path = Path(binary) if binary is not None else _DEFAULT_BINARY
    with tempfile.TemporaryDirectory(prefix="orca-data-") as scratch:
        done = subprocess.run(
            [str(binary_path), "--datadir", scratch, "--help"],
            capture_output=True, text=True, timeout=60, check=False, cwd=scratch,
        )
    first = (done.stdout or done.stderr).strip().splitlines()[:1]
    return first[0].rstrip(":") if first else "unknown"
