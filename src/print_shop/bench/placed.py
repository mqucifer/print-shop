"""A part built in its own frame, read where it sits among the given parts
(mqucifer/print-shop#201).

A table's apron has a stained-glass panel on a face square to y in the table's frame. The glass
tools read a pane's pocket along z only, and the joints are read against the neighbouring parts,
which are given in the table's frame. So whichever frame the apron was built in, one set of
checks couldn't read it. The Sponsor, 2026-11-21: "The glass itself gets designed as a regular
flat panel. The orientation only matters when it meets other pieces."

So a part is built flat, in its own frame, and every glass reading reads it there. Where it's read
against other parts, `--placed ASSEMBLY` places it first, by its part entry in an assembly file
(`assembly.py`): turned by each of its `turn`, in order, about the origin's axes, then moved by
`at`, as `load` places it. The files in its `with`, such as its panes, go with it. Every other
file is read where it is. The part's file is never changed, and no turned copy is written: the
placement is one fact, in the assembly file, read each time.

`clearance`, `fit-on` and `render` take it. A reading says which files it placed and how.

**A file in several parts** (mqucifer/print-shop#343). An assembly often names one file in several
parts: alike slices of a dome, each turned to its place, or four alike legs. Keyed by its file,
each was read once, at the last part's place, so no pair between two of them was ever read. Now
such a file is read once for each part that names it, each copy at its own `turn` and `at`, and
named by its part, `slice.step as s2`; a file the assembly names once keeps its own name. A pair
between two copies is read like any other, and so is a file in several parts' `with`. Given once
on the command line, it's read as each of its parts, and the reply says so; `--part NAME` reads it
as only the parts named, where any of its parts is named, and as all of them where none is.
"""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

import numpy as np

from . import assembly as assembly_module
from .report import NoAnswer, Report


@dataclass(frozen=True)
class Placed:
    """One file's placement: the part entry it's from, its turns and where it's moved."""

    file: str
    part: str
    turn: tuple[tuple[str, float], ...]
    at: tuple[float, float, float]
    assembly: str
    #: What it's read by: the file, as named, where the assembly names it in one part, or
    #: `FILE as PART` where it names it in several (mqucifer/print-shop#343).
    name: str = ""

    @property
    def read_as(self) -> str:
        return self.name or self.file

    def matrix(self) -> np.ndarray:
        """Its turns and its move as one 4 x 4 transform, a point as a column."""
        total = np.eye(4)
        turned = np.eye(3)
        for axis, degrees in self.turn:
            turned = assembly_module._about(
                assembly_module.AXES.index(axis), math.radians(degrees)
            ) @ turned
        total[:3, :3] = turned
        total[:3, 3] = self.at
        return total

    @property
    def said(self) -> str:
        turns = ", then ".join(f"{d:g}° about {a}" for a, d in self.turn) or "not turned"
        at = ", ".join(f"{v:g}" for v in self.at)
        return (
            f"{self.file}, as the part {self.part} in {self.assembly}: {turns}, then moved to "
            f"[{at}]"
        )

    def plain(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "part": self.part,
            "assembly": self.assembly,
            "turn": [[a, d] for a, d in self.turn],
            "at": list(self.at),
        }


#: What `fit-on` and `clearance --slide`/`--lift` answer where the file they move is named in
#: several parts and no `--as` says which: the end checks never take it for a way they can't tell
#: (mqucifer/print-shop#343).
SEVERAL_MOVING = "moves one of them: name which with --as PART"


def _plain(path: str) -> str:
    return os.path.normpath(path.removeprefix("/work/"))


def read(path: str) -> dict[str, list[Placed]]:
    """Each file the assembly file at `path` places, by its path from the working directory:
    each part's own file and the files in its `with`, with each place a part puts it, in the
    assembly's order."""
    found = assembly_module.read(path)
    folder = PurePosixPath(path).parent
    placed: dict[str, list[Placed]] = {}
    for part in found.parts:
        for file in dict.fromkeys((part.file, *part.others)):
            name = _plain(str(folder / file))
            placed.setdefault(name, []).append(Placed(name, part.name, part.turn, part.at, path))
    return placed


def as_named(file: str, every: Sequence[Placed]) -> list[Placed]:
    """Each placement of a file named `file` on the command line, named as it's read: `file`
    where the assembly names it once, else `file as PART`."""
    if len(every) == 1:
        return [replace(every[0], name=file)]
    return [replace(one, name=f"{file} as {one.part}") for one in every]


def of(
    files: Sequence[str], path: str | None, parts: Sequence[str] = ()
) -> dict[str, list[Placed]]:
    """The placements of each of `files` the assembly places, by the file as named, or none
    where no assembly was given: each file once for each part that names it, and with `parts`
    named, only as those, where any of its own parts is one."""
    parts = list(dict.fromkeys(parts))
    if not path:
        if parts:
            raise NoAnswer(
                f"--part names {', '.join(parts)}, a part of an assembly: give the assembly "
                "with --placed"
            )
        return {}
    placed = read(path)
    found: dict[str, list[Placed]] = {}
    for file in dict.fromkeys(files):
        every = as_named(file, placed.get(_plain(file), []))
        if every:
            found[file] = [one for one in every if one.part in parts] or every
    named = {one.part for file in files for one in placed.get(_plain(file), [])}
    unknown = [n for n in parts if n not in named]
    if unknown:
        raise NoAnswer(
            f"--part names {', '.join(unknown)}, and no part of that name in {path} places any "
            f"of {', '.join(dict.fromkeys(files))}"
            + (f": the parts that do are {', '.join(sorted(named))}" if named else "")
        )
    return found


def each(files: Sequence[str], placed: dict[str, list[Placed]]) -> list[tuple[str, Placed | None]]:
    """Each copy a reading reads, in the order the files are named: a file the assembly places,
    once for each of its placements, where it's first named; any other file as it's named."""
    found: list[tuple[str, Placed | None]] = []
    seen: set[str] = set()
    for file in files:
        if file not in placed:
            found.append((file, None))
        elif file not in seen:
            seen.add(file)
            found += [(file, one) for one in placed[file]]
    return found


def by_name(placed: dict[str, list[Placed]]) -> dict[str, Placed]:
    """Every placement, by the name it's read by."""
    return {one.read_as: one for every in placed.values() for one in every}


def one_moving(
    file: str, every: Sequence[Placed], moving_as: str | None, given: str | None, moves: str
) -> Placed | None:
    """Where the file a reading moves is read: as the part `--as` names, where the assembly
    names it in several parts; or unplaced, where it places none of it. `moves` is what moves
    it, `fit-on`, `--slide` or `--lift`: with several parts and no `--as` it moves none, as
    copies would go unread (mqucifer/print-shop#343)."""
    if moving_as:
        if not given:
            raise NoAnswer(f"--as names {moving_as}, a part of an assembly: give it with --placed")
        chosen = [one for one in every if one.part == moving_as]
        if not chosen:
            parts = ", ".join(one.part for one in every) or "none"
            raise NoAnswer(
                f"--as names {moving_as}, and {given} names {file} in no part of that name: its "
                f"parts are {parts}"
            )
        return chosen[0]
    if len(every) > 1:
        raise NoAnswer(
            f"{given} names {file} in {len(every)} parts, {', '.join(o.part for o in every)}, and "
            f"{moves} {SEVERAL_MOVING}. Its other parts are read where they sit, as ones it "
            "could meet"
        )
    return every[0] if every else None


def say_placed(
    report: Report,
    placed: dict[str, list[Placed]],
    path: str | None,
    files: Sequence[str],
    parts: Sequence[str] = (),
    moved: Placed | None = None,
) -> None:
    """Which files a reading placed, and how: a line, and `placed` in its numbers, each
    placement by the name it's read by. A file the assembly names in several parts is said, with
    how many of them are read and how to read fewer, or, for the file a reading moves, `moved`,
    which part moves. Where `--placed` names an assembly that places none of them, that's said
    too, so the reading isn't taken for a placed one."""
    if not placed:
        if path:
            report.numbers["placed"] = {}
            report.line(
                f"placed: nothing. {path} names none of {', '.join(dict.fromkeys(files))} as a "
                "part or in a part's `with`, so each is read where it's built"
            )
        return
    every = by_name(placed)
    report.numbers["placed"] = {name: one.plain() for name, one in every.items()}
    report.line(
        "placed: "
        + "; ".join(one.said for one in every.values())
        + ". Each is read where it sits among the others, and its file stays as it's built"
    )
    whole = read(path) if path else {}
    for file, copies in placed.items():
        total = len(whole.get(_plain(file), copies))
        if total < 2:
            continue
        in_all = f"{file} is named in {total} parts of {path}"
        if moved is not None and any(one.file == moved.file for one in copies):
            others = [one.part for one in copies if one.part != moved.part]
            report.line(
                f"{in_all}: it's moved as {moved.part}, the part --as names, `{file} as "
                f"{moved.part}`"
                + (
                    f", and read where it sits as {', '.join(others)}, each by `{file} as PART`"
                    if others
                    else ", and none of its other parts is read here"
                )
            )
            continue
        names = ", ".join(one.part for one in copies)
        if parts and any(one.part in parts for one in copies):
            report.line(
                f"{in_all}, and it's read as the {len(copies)} that --part names: {names}, each "
                f"by `{file} as PART`"
            )
            continue
        twice = sum(1 for f in files if f == file)
        given = "named " + (f"{twice} times" if twice > 1 else "once") + " here"
        report.line(
            f"{file}, {given}, is named in {total} parts of {path}, so it's read {len(copies)} "
            f"times, once as each: {names}, each by `{file} as PART`, and each pair between "
            "them is read like any other. To read it as only some of them, name each with "
            "--part"
        )


def place(part: Any, one: Placed) -> Any:
    """A `shapes.Part` where it sits: its STEP shape or its mesh turned and moved."""
    from .shapes import Part

    if part.is_brep:
        import cadquery as cq

        # Each turn about its axis through the origin, then the move, as OCCT's own rigid
        # transforms: a matrix of floats isn't quite orthogonal, and OCCT refuses it.
        moved = part.shape
        for axis, degrees in one.turn:
            way = [0.0, 0.0, 0.0]
            way[assembly_module.AXES.index(axis)] = 1.0
            moved = moved.rotate(cq.Vector(0, 0, 0), cq.Vector(*way), degrees)
        moved = moved.translate(cq.Vector(*one.at))
        return Part(one.name or part.name, shape=moved, notes=list(part.notes))
    mesh = part.fine().copy()
    mesh.apply_transform(one.matrix())
    return Part(one.name or part.name, mesh=mesh, notes=list(part.notes))


def load(path: str, one: Placed | None) -> Any:
    """A file, as `shapes.load` reads it, placed where the assembly puts it, by the name it's
    read by."""
    from .shapes import load as load_shape

    part = load_shape(path)
    return place(part, one) if one is not None else part


def freecad_matrix(free_cad: Any, one: Placed) -> Any:
    """The placement as FreeCAD's matrix, for `fit-on` in the second image."""
    return free_cad.Matrix(*[float(v) for v in one.matrix().flatten()])
