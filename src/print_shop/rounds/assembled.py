"""An assembly run finishes with its assembly file as its part (mqucifer/print-shop#310).

A piece of furniture is designed one part per run, each approved by the Sponsor. An **assembly
run** is then given the approved parts, under `references/`, and puts them together: its result
is an assembly file placing them, each unchanged, with its readings. It makes no new part. `done`
took only files the run made, and refused a given file as "given to you, which isn't printed"
(mqucifer/print-shop#191), so an assembly run could only invent a part, name the references, or
never finish.

**`part` may name the run's assembly file:** one JSON file, as `bench/assembly.py` reads it and
`--placed` takes it. `done` takes it when it places at least one file, and every file it places,
each part's `file` and its `with`, is a given reference that's the same file, by its hash, as it
was given. A placed file the run made, a file the run wrote under `references/`, or a given file
that changed is refused, naming it. So nothing the run made passes as a reference.

**Each given file's hash.** The run keeps each reference's sha256 at its start. A person may state
one too, on a line of the request or of a note that names the file in backticks, `part.step` or
`references/part.step`, beside its hash: 64 hex digits, or at least the first 12, which is a
prefix of it. A reference may be replaced while the run goes on, after the Sponsor approved a
changed part: the note that says so states the new file's hash, and from then on that's the hash
the file is held to. The latest stated hash holds, and with none stated, the start's. A file that
differs from its start, with no hash stated for it since, is refused as changed. A run from
before the start's hashes, with no hash stated, holds none, and is refused.

**The run that approved each.** The line that states a file's hash may say `approved in <run>`,
and a fact named for the file may too. Where neither names a run, it's said: "approved run not
named". It's a record, and it decides nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from print_shop.rounds import paths, placing
from print_shop.rounds.needs import GEOMETRY

REFERENCES = "references/"
#: The fewest hex digits a stated hash has: 48 bits, plenty against an accident.
SHORTEST = 12
#: A file named in backticks, as a request or a note names a given part.
NAMED = re.compile(r"`(?P<name>[^`\s]+)`")
#: A stated hash: 12 to 64 hex digits standing alone.
HEX = re.compile(r"(?<![0-9A-Za-z])(?P<hex>[0-9a-fA-F]{12,64})(?![0-9A-Za-z])")
#: The run that approved it, as a line names it.
APPROVED_IN = re.compile(r"approved in (?:the run )?`?(?P<run>[A-Za-z0-9][A-Za-z0-9._-]*)`?")
NOT_NAMED = "approved run not named"
REQUEST = "request"
START = "start"


def sha(data: bytes) -> str:
    """A given file by its bytes: one byte changed is another file."""
    return hashlib.sha256(data).hexdigest()


def given_name(name: str) -> str:
    """A given file as the run names it, `references/<name>`."""
    plain = os.path.normpath(name.strip().removeprefix("/work/"))
    return plain if plain.startswith(REFERENCES) else f"{REFERENCES}{plain}"


def at_start(work: Path) -> dict[str, str]:
    """Each reference as it was given, by its hash: kept at the run's start."""
    folder = work / "references"
    return {
        f"{REFERENCES}{p.name}": sha(p.read_bytes())
        for p in sorted(folder.iterdir())
        if p.is_file() and not p.is_symlink()
    }


@dataclass(frozen=True)
class Stated:
    """A hash a person stated for a given file: where, and the line it was on."""

    file: str
    sha: str
    source: str
    line: str
    approved_in: str | None = None
    note: str | None = None


def stated_in(text: str, source: str, note: str | None = None) -> list[Stated]:
    """Each line of `text` that names one geometry file in backticks beside one hash. A line
    that names several files or several hashes states none: it can't be told which is whose."""
    found: list[Stated] = []
    for raw in text.splitlines():
        names = [
            m.group("name")
            for m in NAMED.finditer(raw)
            if m.group("name").lower().endswith(GEOMETRY)
        ]
        hexes = [m.group("hex") for m in HEX.finditer(NAMED.sub(" ", raw))]
        if len(set(names)) != 1 or len(hexes) != 1:
            continue
        approved = APPROVED_IN.search(raw)
        found.append(
            Stated(
                given_name(names[0]),
                hexes[0].lower(),
                source,
                raw.strip(),
                approved.group("run").rstrip(".") if approved else None,
                note,
            )
        )
    return found


def stated(request: str, notes: list[str]) -> dict[str, list[Stated]]:
    """Each given file's stated hashes, oldest first: the request's, then each note's."""
    found: dict[str, list[Stated]] = {}
    every = stated_in(request, REQUEST)
    for k, note in enumerate(notes, 1):
        every += stated_in(note, f"note {k}", note)
    for one in every:
        found.setdefault(one.file, []).append(one)
    return found


@dataclass
class Given:
    """One file the assembly places, as `done` read it."""

    file: str
    sha: str | None
    expected: str | None
    source: str
    approved_in: str | None
    changes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return bool(self.sha and self.expected and self.sha.startswith(self.expected))

    def approved_said(self) -> str:
        return f"approved in `{self.approved_in}`" if self.approved_in else NOT_NAMED

    def plain(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Checked:
    """An assembly file as `done` read it: what it places, and what's wrong, each named."""

    assembly: str
    assembly_sha: str | None = None
    given: list[Given] = field(default_factory=list)
    empty: bool = False
    unread: str = ""
    not_given: list[str] = field(default_factory=list)
    changed: list[Given] = field(default_factory=list)

    @property
    def wrong(self) -> bool:
        return bool(self.empty or self.unread or self.not_given or self.changed)

    def files(self) -> list[str]:
        return [one.file for one in self.given]

    def plain(self) -> dict[str, Any]:
        return {
            "assembly": self.assembly,
            "assembly_sha": self.assembly_sha,
            "given": [one.plain() for one in self.given],
        }

    def refused(self) -> str:
        """Why `done` won't take it, each file named."""
        if self.unread:
            return f"`{self.assembly}` isn't an assembly file `done` can read: {self.unread}."
        if self.empty:
            return (
                f"`{self.assembly}` places nothing: an assembly file's `parts` each place a given "
                'file, {"name": "top", "file": "references/top.step", "layers": "z", "turn": '
                '[], "at": [0, 0, 0]}.'
            )
        lines = [f"`{self.assembly}` places files that aren't the given parts as they were given:"]
        for name in self.not_given:
            lines.append(
                f"- `{name}` isn't given to you: an assembly in `part` places only the given "
                "references, unchanged. A file you made is printed, and is named in `part` "
                "itself."
            )
        for one in self.changed:
            if one.sha is None:
                lines.append(f"- `{one.file}` isn't there.")
            elif one.expected is None:
                lines.append(
                    f"- `{one.file}` has no hash held for it: neither the run's start nor a "
                    "person stated one, so it can't be told from a file you wrote."
                )
            else:
                lines.append(
                    f"- `{one.file}` has changed: it's {one.sha[:SHORTEST]}, and as given "
                    f"({one.source}) it's {one.expected[:SHORTEST]}. A given file is placed as it "
                    "was given."
                )
        return "\n".join(lines)


def _expected(
    file: str, start: dict[str, str] | None, said: list[Stated]
) -> tuple[str | None, str, list[dict[str, Any]]]:
    """The hash a given file is held to and where it's from: the latest stated, or the start's.
    And each hash stated for it, with its line and its note, oldest first."""
    expected = (start or {}).get(file)
    source = START if expected else ""
    changes: list[dict[str, Any]] = []
    for one in said:
        changes.append({"sha": one.sha, "source": one.source, "line": one.line, "note": one.note})
        expected, source = one.sha, one.source
    return expected, source, changes


def _approved_in(file: str, said: list[Stated], facts: dict[str, dict[str, Any]]) -> str | None:
    for one in reversed(said):
        if one.approved_in:
            return one.approved_in
    fact = facts.get(file) or facts.get(PurePosixPath(file).name) or {}
    found = APPROVED_IN.search(str(fact.get("text") or ""))
    return found.group("run").rstrip(".") if found else None


def check(
    where: Path,
    assembly: str,
    start: dict[str, str] | None,
    said: dict[str, list[Stated]],
    facts: dict[str, dict[str, Any]],
) -> Checked:
    """The assembly file in `where`, read for what it places: every file a given reference, each
    the same, by its hash, as it was given."""
    checked = Checked(assembly)
    try:
        raw = paths.read_bytes_inside(where, assembly)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        checked.unread = str(exc)[:200] or type(exc).__name__
        return checked
    checked.assembly_sha = sha(raw)
    entries = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        checked.unread = 'it has no `parts` list: {"parts": [{"name": ..., "file": ...}, ...]}'
        return checked
    placed = sorted(placing.placed_by(where, assembly))
    if not placed:
        checked.empty = True
        return checked
    for name in placed:
        held = name in (start or {}) or name in said
        if not name.startswith(REFERENCES) or (start is not None and not held):
            # A file the run made, or wrote under `references/`: never given to it.
            checked.not_given.append(name)
            continue
        expected, source, changes = _expected(name, start, said.get(name, []))
        given = Given(
            name,
            _sha_of(where, name),
            expected,
            source,
            _approved_in(name, said.get(name, []), facts),
            changes,
        )
        checked.given.append(given)
        if not given.same:
            checked.changed.append(given)
    return checked


def _sha_of(where: Path, name: str) -> str | None:
    found = paths.inside(where, name)
    if found is None or found.is_symlink() or not found.is_file():
        return None
    return sha(paths.read_bytes_inside(where, name))


def is_assembly(name: str) -> bool:
    return name.strip().lower().endswith(".json")


# What's said


def not_read_on_given(checked: Checked) -> str:
    """What the reply says of the printed-file readings, which aren't run on a given file."""
    named = "; ".join(f"`{one.file}`, {one.approved_said()}" for one in checked.given)
    return (
        "The printed-file readings, `thin`, the side on the bed, the downward faces and the "
        "glass checks, aren't run on the given parts: each was read in its own approved run "
        f"({named})."
    )


def said(checked: Checked) -> list[str]:
    """The result, said: the assembly file and each given part, with its hash and the run that
    approved it, and each hash a note stated for it, with the note's line."""
    return _placing(checked.assembly, checked.assembly_sha, [g.plain() for g in checked.given])


def recorded(assembly: str, assembly_sha: str | None, given: str) -> list[str]:
    """`record/ROUNDS.md`'s lines for a round's assembly, from its ledger row."""
    return _placing(assembly, assembly_sha, json.loads(given or "[]"))


def _placing(assembly: str, assembly_sha: str | None, files: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"The result is an assembly of given parts: `{assembly}`, sha256 {assembly_sha}. "
        "It places:"
    ]
    for one in files:
        run = f"approved in `{one['approved_in']}`" if one.get("approved_in") else NOT_NAMED
        lines.append(f"- `{one['file']}`, sha256 {one['sha']}, as given ({one['source']}), {run}")
        for change in one.get("changes") or []:
            if change.get("note"):
                lines.append(
                    f"  - its hash, {change['sha']}, stated in {change['source']}: "
                    f"\"{change['line']}\""
                )
    return lines


#: The colours a given part is drawn in where the run gave it none: each its own.
COLOURS = ("blue", "orange", "green", "purple", "red", "teal", "gold", "pink", "brown", "navy")
NOT_IN_A_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def row_name(checked: Checked, taken: list[str]) -> str:
    """The sheet's row for the placed whole: the assembly file's name."""
    name = NOT_IN_A_NAME.sub("_", PurePosixPath(checked.assembly).stem)[:24].strip("_")
    name = name or "assembly"
    return name if name not in taken else f"{name}-assembly"


def looks(checked: Checked, given: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Each given part's look on the sheet: the run's colour for it, or one no other has, and a
    label with its name and its hash."""
    used = {str(look.get("colour")) for look in given.values()}
    spare = iter([c for c in COLOURS if c not in used])
    found: dict[str, dict[str, str]] = {}
    for one in checked.given:
        mine = given.get(one.file) or {}
        colour = mine.get("colour") or next(spare, "grey")
        label = (mine.get("label") or PurePosixPath(one.file).stem)[:18].strip()
        label = re.sub(r"[,=:\n]", " ", label)
        found[one.file] = {"colour": colour, "label": f"{label} {str(one.sha)[:SHORTEST]}"}
    return found


HEADING = "The result: an assembly of given parts"
PLACED_HEADING = "The given parts as placed, by the harness"
CHECK = "placed"


def placed_reading(numbers: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """From `clearance`'s pairs: the pairs passing into each other, the pairs that can't be told,
    and the contacts."""
    into: list[str] = []
    unsure: list[str] = []
    contacts: list[str] = []
    for pair in numbers.get("pairs") or []:
        both = f"`{pair.get('a')}` and `{pair.get('b')}`"
        verdict = str(pair.get("verdict") or "")
        if verdict == "INTERFERE":
            into.append(f"{both} pass into each other")
        elif verdict in ("DISAGREE", "NO ANSWER"):
            unsure.append(f"{both}: {verdict}")
        elif verdict == "TOUCHING":
            contacts.append(f"{both} touch")
    return into, unsure, contacts
