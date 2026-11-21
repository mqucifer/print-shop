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
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import numpy as np

from . import assembly as assembly_module
from .report import Report


@dataclass(frozen=True)
class Placed:
    """One file's placement: the part entry it's from, its turns and where it's moved."""

    file: str
    part: str
    turn: tuple[tuple[str, float], ...]
    at: tuple[float, float, float]
    assembly: str

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
            "part": self.part,
            "assembly": self.assembly,
            "turn": [[a, d] for a, d in self.turn],
            "at": list(self.at),
        }


def _plain(path: str) -> str:
    return os.path.normpath(path.removeprefix("/work/"))


def read(path: str) -> dict[str, Placed]:
    """Each file the assembly file at `path` places, by its path from the working directory:
    each part's own file and the files in its `with`."""
    found = assembly_module.read(path)
    folder = PurePosixPath(path).parent
    placed: dict[str, Placed] = {}
    for part in found.parts:
        for file in (part.file, *part.others):
            name = _plain(str(folder / file))
            placed[name] = Placed(name, part.name, part.turn, part.at, path)
    return placed


def of(files: list[str], path: str | None) -> dict[str, Placed]:
    """The placement of each of `files` the assembly places, by the file as named, or none
    where no assembly was given."""
    if not path:
        return {}
    placed = read(path)
    return {f: placed[_plain(f)] for f in files if _plain(f) in placed}


def say_placed(
    report: Report, placed: dict[str, Placed], path: str | None, files: list[str]
) -> None:
    """Which files a reading placed, and how: a line, and `placed` in its numbers. Where
    `--placed` names an assembly that places none of them, that's said too, so the reading isn't
    taken for a placed one."""
    if placed:
        report.numbers["placed"] = {name: one.plain() for name, one in placed.items()}
        report.line(
            "placed: "
            + "; ".join(one.said for one in placed.values())
            + ". Each is read where it sits among the others, and its file stays as it's built"
        )
    elif path:
        report.numbers["placed"] = {}
        report.line(
            f"placed: nothing. {path} names none of {', '.join(files)} as a part or in a part's "
            "`with`, so each is read where it's built"
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
        return Part(part.name, shape=moved, notes=list(part.notes))
    mesh = part.fine().copy()
    mesh.apply_transform(one.matrix())
    return Part(part.name, mesh=mesh, notes=list(part.notes))


def load(path: str, placed: dict[str, Placed]) -> Any:
    """A file, as `shapes.load` reads it, placed where the assembly puts it."""
    from .shapes import load as load_shape

    part = load_shape(path)
    return place(part, placed[path]) if path in placed else part


def freecad_matrix(free_cad: Any, one: Placed) -> Any:
    """The placement as FreeCAD's matrix, for `fit-on` in the second image."""
    return free_cad.Matrix(*[float(v) for v in one.matrix().flatten()])
