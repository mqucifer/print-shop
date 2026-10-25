"""Naming the sliced G-code so two jobs cannot collide on the printer.

The printer's internal storage is one flat namespace — it already holds 164
files — and ``start_print`` takes a bare filename with no path. Every job here
sliced to ``plate_1.gcode``, so the second job to upload would silently
overwrite the first and then print it.

Orca's own uploads read ``ECC_0.4_bracket_PLA0.2_1h19m.gcode``: nozzle, model,
material, layer height, duration. That convention is mirrored because it is
what makes the printer's own file list readable to someone standing at the
machine. Ours appends the job id, which is the only thing tying a file on the
printer back to a gate decision and a checkpoint.

Every field comes from the G-code the slicer actually wrote, never from the
profiles we asked it to use. Same reasoning as `AppliedOrientation`: what was
decided and what happened are not guaranteed to be the same thing, and the
name should describe the file.
"""

from __future__ import annotations

import re
from pathlib import Path

from print_shop.core.state import SliceResult

__all__ = [
    "MAX_PART_CHARS",
    "duration_tag",
    "gcode_filename",
    "job_id_from_filename",
    "name_gcode",
    "sanitize_part",
]

#: One printer, one household (PROJECT.md non-goals). Orca writes this same
#: prefix on its own Centauri Carbon uploads, so our files sort next to theirs.
_PREFIX = "ECC"

#: Long enough to recognise a part on the printer's screen, short enough that
#: the 36-character job id still fits in a sane filename.
MAX_PART_CHARS = 24

_DISALLOWED = re.compile(r"[^A-Za-z0-9-]+")

#: The job id is the last underscore-delimited field before the extension.
#: Anchored at the end, so no amount of underscores earlier in the name can
#: be mistaken for it.
_JOB_ID_RE = re.compile(
    r"_(?P<job_id>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.gcode$"
)


def sanitize_part(name: str, *, limit: int = MAX_PART_CHARS) -> str:
    """Reduce a model name to characters that are safe in a filename.

    Underscore is the field separator and dot introduces the extension, so
    neither may survive into this field — a part called ``v2.1_final`` would
    otherwise make the name ambiguous to read and to split.

    Returns ``"part"`` when nothing usable is left, because a job with an
    unnameable model still needs a file.
    """
    cleaned = _DISALLOWED.sub("-", name).strip("-")
    # Collapse the runs the substitution can create ("a__b" -> "a--b" -> "a-b").
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:limit].strip("-")
    return cleaned or "part"


def duration_tag(seconds: int) -> str:
    """Format a print duration the way Orca does: ``1h19m``, ``14m``, ``45s``.

    Truncates rather than rounds, so the tag is a deterministic function of the
    seconds and two slices reporting the same duration always name the same.
    Negative input is treated as zero; the value comes from a parsed header and
    a nonsense number should not raise inside a naming function.
    """
    seconds = max(0, seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def gcode_filename(
    *,
    job_id: str,
    part: str,
    print_time_s: int,
    nozzle_mm: float | None = None,
    material: str | None = None,
    layer_height_mm: float | None = None,
) -> str:
    """Build the name a sliced G-code file carries onto the printer.

    Shape: ``ECC_<nozzle>_<part>_<material><layer>_<duration>_<job_id>.gcode``

    The three slicer-derived fields are optional. Orca writes all of them, but
    a profile that omits one must not fail a job that has already been sliced —
    the segment is dropped instead. The job id is always last, which is what
    makes `job_id_from_filename` able to recover it regardless.
    """
    fields = [_PREFIX]
    if nozzle_mm is not None:
        fields.append(f"{nozzle_mm:g}")
    fields.append(sanitize_part(part))

    if material is not None and layer_height_mm is not None:
        fields.append(f"{sanitize_part(material, limit=12)}{layer_height_mm:g}")
    elif material is not None:
        fields.append(sanitize_part(material, limit=12))
    elif layer_height_mm is not None:
        fields.append(f"{layer_height_mm:g}")

    fields.append(duration_tag(print_time_s))
    fields.append(job_id)
    return "_".join(fields) + ".gcode"


def job_id_from_filename(filename: str | Path) -> str | None:
    """Recover the job id from a name produced by `gcode_filename`.

    Accepts a bare name or a path — the printer reports ``/local/<name>`` in
    some frames and a bare name in others. Returns None for any file this
    project did not name, which includes the 164 files already on the printer.
    """
    return match.group("job_id") if (match := _JOB_ID_RE.search(Path(filename).name)) else None


def name_gcode(result: SliceResult, *, job_id: str, part: str) -> SliceResult:
    """Rename a freshly sliced G-code on disk and return the updated result.

    The file keeps the same name locally and on the printer, so a file on the
    machine's screen can be traced back to a job directory without a lookup
    table.

    Raises:
        FileNotFoundError: If the sliced G-code is not where the result says.
    """
    source = result.gcode_path
    if not source.exists():
        raise FileNotFoundError(f"cannot rename: no G-code at {source}")

    target = source.parent / gcode_filename(
        job_id=job_id,
        part=part,
        print_time_s=result.print_time_s,
        nozzle_mm=result.nozzle_mm,
        material=result.material,
        layer_height_mm=result.layer_height_mm,
    )
    if target != source:
        # replace(), not rename(): re-slicing a job that was sent back from the
        # review gate must land on its own previous output rather than raise.
        source.replace(target)
    return result.model_copy(update={"gcode_path": target})
