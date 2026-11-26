"""The side an assembly's part prints on, as the run's own files and readings say it
(mqucifer/print-shop#192).

A furniture run writes an assembly file (`bench/assembly.py`) whose parts each say `layers`, the
axis of the part's own file its layers are stacked along: the way up on the bed. `fit-bed` lays
each part on a flat side, square to its `layers` where it's given the assembly, and on whichever
side fits best where it's given the STEP files. Two things went wrong on a leg-joint eval:

- **The downward faces were read on the wrong side.** The reading at a stop laid every file on
  -z, so a leg piece printed on its side (`layers: x`, and `fit-bed` laid it on -x) was flagged
  for faces that are walls on its side, and its real overhang, a tail inset 3.2 to 5 mm on every
  side hanging over air along 12 mm, was missed (lesson runs19-joint:10). `downward.laid_on`
  now takes the side from here where nothing more direct names one.
- **`load` read strain against `layers` that disagreed with the side `fit-bed` laid the part
  on,** and nothing said so: the run read every strain against the wrong layers until a
  stand-in caught it (runs19-joint:16). Now a `load` reply leads with each part whose `layers`
  aren't the axis `fit-bed` laid its file on, and says which side.

Nothing here runs a file or a command: it reads the run's JSON files and the bench's records.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from print_shop.rounds import paths

AXES = ("x", "y", "z")
#: A request line that leaves the side a part prints on to the refinement pass after approval:
#: then `done` needn't name it, and the file prints as stored unless the pass says otherwise.
LEFT_TO_THE_PASS = re.compile(
    r"(?=.*\brefinement pass\b)(?=.*\b(?:sides?|orient\w*|bed|which way|lay|laid|lies|up|down)\b)",
    re.IGNORECASE,
)
#: The bench commands that read an assembly file.
READS_ASSEMBLY = ("fit-bed", "load")


@dataclass(frozen=True)
class Layered:
    """An assembly's part: its name, its file as the run names it, the axis its layers are
    stacked along, and the assembly file that says so."""

    name: str
    file: str
    layers: str
    assembly: str


@dataclass(frozen=True)
class Fitted:
    """The side `fit-bed` laid a file on, and the call it was read at."""

    file: str
    side: str
    at_call: int


def _plain(path: str | PurePosixPath) -> str:
    """A path as the run names it: relative to its working directory, with no `./`."""
    parts = [p for p in PurePosixPath(str(path)).parts if p not in (".", "")]
    return PurePosixPath(*parts).as_posix() if parts else ""


def parts_of(work: Path, assembly: str) -> list[Layered]:
    """The parts an assembly file names with their `layers`, each file as the run names it. A
    file that isn't an assembly, or a part with no `layers`, gives nothing: the bench says what's
    wrong with it when it's read."""
    try:
        given = json.loads((work / assembly).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    entries = given.get("parts") if isinstance(given, dict) else None
    if not isinstance(entries, list):
        return []
    folder = PurePosixPath(_plain(assembly)).parent
    found = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        layers, file = entry.get("layers"), entry.get("file")
        if layers in AXES and isinstance(file, str) and file:
            name = str(entry.get("name") or file)
            found.append(Layered(name, _plain(folder / file), str(layers), _plain(assembly)))
    return found


def assemblies(work: Path, named: list[str]) -> list[str]:
    """The run's assembly files, those its bench calls named first, latest first, then any other
    JSON file in its working directory that names parts with `layers`. One in `scratch/`, such as
    a compound placed or exploded to draw, isn't the run's (mqucifer/print-shop#315)."""
    found = list(
        dict.fromkeys(_plain(n) for n in reversed(named) if not paths.in_scratch(_plain(n)))
    )
    for path in paths.walk_plain(work, skip_made=True):
        plain = _plain(path.as_posix())
        if path.suffix.lower() != ".json" or plain.startswith(("record/", "bench-out/")):
            continue
        if plain not in found and parts_of(work, plain):
            found.append(plain)
    return [one for one in found if (work / one).is_file()]


def layered(work: Path, named: list[str]) -> dict[str, Layered]:
    """Each assembly part's `layers`, by its file: from the assembly a bench call named last
    where two say it."""
    found: dict[str, Layered] = {}
    for assembly in assemblies(work, named):
        for part in parts_of(work, assembly):
            found.setdefault(part.file, part)
    return found


def fitted(rows: Sequence[tuple[int, list[str], dict[str, Any] | None]]) -> dict[str, Fitted]:
    """The side the latest `fit-bed` laid each file on, from its record's `parts.NAME.file` and
    `side`: each bench call's call, its argv and its numbers, oldest first."""
    found: dict[str, Fitted] = {}
    for at_call, argv, numbers in rows:
        if not argv or argv[0] != "fit-bed" or not isinstance(numbers, dict):
            continue
        for row in (numbers.get("parts") or {}).values():
            if isinstance(row, dict) and row.get("side") and row.get("file"):
                file = _plain(str(row["file"]))
                found[file] = Fitted(file, str(row["side"]), at_call)
    return found


def named_assemblies(rows: Sequence[tuple[int, list[str], dict[str, Any] | None]]) -> list[str]:
    """The assembly files the run's bench calls read, oldest first."""
    return [
        arg
        for _, argv, _ in rows
        if argv and argv[0] in READS_ASSEMBLY
        for arg in argv[1:]
        if arg.lower().endswith(".json") and not arg.startswith("-")
    ]


def against(parts: list[Layered], sides: dict[str, Fitted]) -> list[dict[str, Any]]:
    """Each part whose `layers` aren't the axis `fit-bed` last laid its file on."""
    found = []
    for part in parts:
        fit = sides.get(part.file)
        if fit is not None and fit.side[1:] != part.layers:
            found.append(
                {
                    "part": part.name,
                    "file": part.file,
                    "layers": part.layers,
                    "assembly": part.assembly,
                    "fit_bed_side": fit.side,
                    "fit_bed_call": fit.at_call,
                }
            )
    return found


def against_said(found: list[dict[str, Any]]) -> str:
    """The line a `load` reply leads with, where a part's `layers` disagree with `fit-bed`."""
    lines = []
    for one in found:
        lines.append(
            f"`load` read the part {one['part']} with its layers stacked along {one['layers']}, "
            f"as `{one['assembly']}` says, but `fit-bed` laid `{one['file']}` on its "
            f"{one['fit_bed_side']} side at call {one['fit_bed_call']}, where they'd stack along "
            f"{one['fit_bed_side'][1]}: each strain across its layers here is read for "
            f"{one['layers']}. Print it on a side square to {one['layers']}, or give it "
            f'`"layers": "{one["fit_bed_side"][1]}"` and read `load` again.'
        )
    return "\n".join(lines)


def left_to_the_pass(request: str) -> str:
    """The request's line that leaves the print side to the refinement pass, or nothing."""
    for line in request.splitlines():
        if LEFT_TO_THE_PASS.search(line):
            return line.strip(" -*\t")
    return ""


def no_side_said(unnamed: list[str], laid: dict[str, tuple[str, str]]) -> str:
    """`done`'s refusal of printed files with no side in `on_bed` (mqucifer/print-shop#192). The
    refinement pass printed a lid skirt-down because `done` named no side (design-runs:42), and a
    run chose a lid's side without reading what its first layer bridges (runs9-lid:3)."""
    lines = [
        "Nothing was checked: `on_bed` names no side for "
        + ", ".join(f"`{name}`" for name in unnamed)
        + ". Each printed file's side is data: the refinement pass lays it so, and its downward "
        "faces are read lying on it."
    ]
    for name in unnamed:
        side, said = laid[name]
        if side != "-z" or "layers" in said:
            lines.append(f"- `{name}`: {side}, {said}.")
    lines.append(
        "Before you choose a side, read what its first layer lies on and what it bridges: "
        "`slice FILE --at AXIS=AT` a layer (0.2 mm) in from the face that goes down, as `info` "
        "gives its box, shows what's on the bed and what isn't. Then send `done` again with "
        '`on_bed` giving each file its side as your script stores it, `-z` too: {"lid.step": '
        '"+z"}.'
    )
    return "\n".join(lines)
