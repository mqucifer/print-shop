"""A part built flat is placed wherever it's read against a given part (mqucifer/print-shop#201).

A design run made a table's apron, whose stained-glass panel sits on a face square to y in the
table's frame. The glass tools and the glass reading at `done` read a pane's pocket along z only,
and the joints are read against the neighbouring parts, given in the table's frame. So whichever
frame the apron was built in, one set of checks couldn't read it: the run blocked twice and was
closed to run again. The Sponsor, 2026-11-21: "The glass itself gets designed as a regular flat
panel. The orientation only matters when it meets other pieces."

**Where the placement is kept.** In the assembly file, the one fact a furniture run already
writes for where a part sits (`bench/assembly.py`): the part's entry, its `turn` and `at`, and
in its `with` the files that go with it, such as its panes. Any run may write one: a part
entry needs `name`, `file` and `layers`, and the given parts need no entry, as they're given where
they sit. The run's assembly files are the ones its `load`, `fit-bed` and `--placed` readings
named, latest first, and then any other JSON file in its working directory that names parts
(`print_sides.assemblies`).

**Where it's used.** Every bench reading the harness runs that names a given file, under
`references/`, beside a file an assembly places, `clearance`, `fit-on` or `render`, is given
`--placed` with that assembly: the model's own `bench` calls, each bench need's reading at `done`
and `needs`' tries, the end checks' way on, and the stop sheets. The bench reads the placed files
turned and moved, and says so in the reply. The model's file stays as it's built, flat, so the
glass readings read it there, and nobody keeps a second, turned copy that could drift. A reading
that names no given file is the model's own, and is run as it's written.

**A part with no placement** reads as it does today. Nothing is guessed: where a frame `glass`
declares is read against a given file and no assembly places it, the stop says so, as it can't
tell a part built where it sits from one built flat.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from print_shop.rounds import print_sides
from print_shop.rounds.needs import GEOMETRY

REFERENCES = "references/"
#: The bench commands that take `--placed`.
PLACES = ("clearance", "fit-on", "render")
FLAG = "--placed"
#: Options whose value names a file that's never placed: drawn beside the part, and not it.
NOT_PLACED = ("--test-piece",)


def _plain(path: str) -> str:
    return os.path.normpath(path.strip().removeprefix("/work/"))


def given(name: str) -> bool:
    return _plain(name).startswith(REFERENCES)


def placed_by(where: Path, assembly: str) -> set[str]:
    """The files an assembly file places, each part's file and its `with`, as the run names
    them. Read loosely, as the bench reads it strictly and says what's wrong."""
    try:
        data = json.loads((where / assembly).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    entries = data.get("parts") if isinstance(data, dict) else None
    folder = PurePosixPath(_plain(assembly)).parent
    found: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        others = entry.get("with")
        for file in [entry.get("file"), *(others if isinstance(others, list) else [])]:
            if isinstance(file, str) and file:
                found.add(_plain(str(folder / file)))
    return found


def named(argv: Sequence[str]) -> list[str]:
    """The geometry a bench command names, `--variant`'s and `--onto`'s too, and not a test
    piece's."""
    found: list[str] = []
    skip = False
    for word in argv[1:]:
        if skip:
            skip = False
            continue
        if word in NOT_PLACED:
            skip = True
            continue
        if word.startswith("--"):
            if "=" not in word or word.split("=", 1)[0] in NOT_PLACED:
                continue
            word = word.split("=", 1)[1]
        # A variant's files follow its name: `NAME=a.step,b.step`.
        for one in word.split("=", 1)[-1].split(","):
            if one.lower().endswith(GEOMETRY):
                found.append(_plain(one))
    return list(dict.fromkeys(found))


def wanted(argv: Sequence[str]) -> bool:
    """Whether a command could be placed: one that takes `--placed`, hasn't one, and reads a
    given file."""
    if not argv or argv[0] not in PLACES:
        return False
    if any(w == FLAG or w.startswith(f"{FLAG}=") for w in argv):
        return False
    return any(given(f) for f in named(argv))


@dataclass(frozen=True)
class Placing:
    """The run's assembly files, latest first: where `--placed` comes from."""

    assemblies: tuple[str, ...]

    def which(self, argv: Sequence[str], where: Path) -> str:
        """The assembly that places a file of the command's, where it also reads a given file
        and takes `--placed`, and doesn't name one already; or nothing."""
        if not wanted(argv):
            return ""
        own = {f for f in named(argv) if not given(f)}
        for assembly in self.assemblies:
            if (where / assembly).is_file() and own & placed_by(where, assembly):
                return assembly
        return ""

    def argv(self, argv: list[str], where: Path) -> list[str]:
        """The command as the harness runs it: with `--placed` where `which` names one."""
        assembly = self.which(argv, where)
        return [*argv, FLAG, assembly] if assembly else argv


def named_assemblies(rows: Sequence[tuple[int, list[str], dict[str, Any] | None]]) -> list[str]:
    """The assembly files the run's bench calls read, oldest first: `load`'s and `fit-bed`'s,
    and each `--placed`."""
    found = print_sides.named_assemblies(rows)
    for _, argv, _ in rows:
        for k, word in enumerate(argv):
            if word == FLAG and k + 1 < len(argv):
                found.append(argv[k + 1])
            elif word.startswith(f"{FLAG}="):
                found.append(word.split("=", 1)[1])
    return found


def of_run(work: Path, rows: Sequence[tuple[int, list[str], dict[str, Any] | None]]) -> Placing:
    """The run's assembly files, latest first, from its bench calls and its working directory."""
    return Placing(tuple(print_sides.assemblies(work, named_assemblies(rows))))


def placed(work: Path, placing: Placing) -> set[str]:
    """Every file the run's assembly files place."""
    found: set[str] = set()
    for assembly in placing.assemblies:
        found |= placed_by(work, assembly)
    return found


def unplaced(
    frames: Sequence[tuple[str, Sequence[str]]],
    commands: Sequence[str | Sequence[str]],
    work: Path,
    placing: Placing,
) -> list[str]:
    """What the stop says of each glass frame read against a given file where no assembly
    places it, or places its frame and not each of its other files. `frames` are each frame's
    file and its other files, its panes and `with`, as `glass` declares them; `commands` are the
    readings to look at, each a command or its words."""
    every = placed(work, placing)
    lines: list[str] = []
    for frame, others in frames:
        mine = [_plain(frame), *(_plain(o) for o in others)]
        read: list[str] = []
        for command in commands:
            words = _words(command)
            files = named(words)
            if words and words[0] in PLACES and any(given(f) for f in files):
                read += [g for g in files if given(g)] if set(mine) & set(files) else []
        read = list(dict.fromkeys(read))
        if not read:
            continue
        against = ", ".join(f"`{f}`" for f in read)
        if mine[0] not in every:
            lines.append(
                f"- `{mine[0]}` is a frame the glass checks read flat, along z, and it's read "
                f"against {against} where it's built, as no assembly file places it. If it's "
                "built flat and sits otherwise among them, give it a part entry in an assembly "
                'file, `{"name": ..., "file": "' + mine[0] + '", "layers": "z", "turn": '
                '[["x", 90]], "at": [x, y, z], "with": [its panes]}`, and every reading against '
                "a given part reads it placed. If it's built where it sits, nothing changes."
            )
            continue
        loose = [o for o in mine[1:] if o not in every]
        if loose:
            lines.append(
                f"- `{mine[0]}` is placed, and {', '.join(f'`{o}`' for o in loose)}, which "
                "`glass` gives it, isn't: name each in its part entry's `with`, or it's read "
                "where it's built, apart from its frame."
            )
    return lines


HEADING = "Frames read against a given part, and how each is placed"
AT_STOP = (
    "Each frame `glass` declares is read flat by the glass checks, and placed by its assembly "
    "file's `turn` and `at` wherever it's read against a given part (mqucifer/print-shop#201). "
    "These go beside the question:"
)


def _words(command: str | Sequence[str]) -> list[str]:
    if not isinstance(command, str):
        return [str(w) for w in command]
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()
