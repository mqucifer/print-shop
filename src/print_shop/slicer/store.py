"""print-shop's own Orca profiles, in `profiles/`, and the choice of one set per print.

The Sponsor, 2026-10-25, on mqucifer/print-shop#6: print-shop keeps its
profiles in its repository, in Orca's own JSON, one file per machine, process
and filament. Each is fully resolved and keeps `inherits` naming its system
parent. They are seeded from the Sponsor's tuned Orca presets by
`scripts/import_profiles.py`, and a change to one is a reviewed PR. Every print
slices from these files, so an Orca update cannot change a print silently.

**The Sponsor says what's fitted:** the nozzle and the loaded filament preset
(a material type works as a shortcut when only one preset has it). Nothing is
assumed. `profiles/pairings.json` says which machine and process go with each
filament on each nozzle: the Sponsor's knowledge, written once and reviewed by
PR (mqucifer/print-shop#6, 2026-10-25). A filament with no pairing for the
nozzle falls back to a search, and a tie there refuses.

**Orca's own compatibility rule applies over a pairing, strictly:**

- a machine fits when its `nozzle_diameter` is the fitted nozzle;
- a process or filament fits a machine when its `compatible_printers` names
  the machine or the machine's system parent (`inherits`), as Orca's CLI
  checks it;
- a filament fits a process when its `compatible_prints`, if any, names it.

A pairing the rule forbids is refused, saying why, never allowed silently.
On no match or a tie, the refusal lists every candidate with why each was
left out, so the Sponsor picks by name, and a mis-marked preset shows there.

Tested facts about Orca's CLI (2.3, on the Mac, 2026-10-25), which the rule
keeps to:
- a flattened machine without `inherits` exits 239, "process not compatible";
- a process whose `compatible_printers` is empty also exits 239, though Orca's
  UI reads an empty list as "every printer". Such a process is left out here.
- a filament whose `compatible_printers` is empty slices.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

__all__ = [
    "STORE_DIR",
    "Choice",
    "NoChoice",
    "Pairing",
    "ProfileRef",
    "ProfilesUsed",
    "Store",
    "StoredProfile",
    "check_pairings",
    "choose",
    "unpaired",
]

#: `profiles/` at the top of print-shop's checkout.
STORE_DIR = Path(__file__).resolve().parents[3] / "profiles"

#: The reviewed file saying which machine and process go with each filament.
PAIRINGS = "pairings.json"


class NoChoice(ValueError):
    """The fitted nozzle and filament don't pick one set of profiles. Says why."""


@dataclass(frozen=True)
class StoredProfile:
    """One profile file in the store."""

    kind: str
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.path.stem))

    @property
    def parent(self) -> str:
        return str(self.data.get("inherits") or "")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    @property
    def nozzle_mm(self) -> float | None:
        return _first_float(self.data.get("nozzle_diameter"))

    @property
    def filament_type(self) -> str:
        return _first(self.data.get("filament_type"))

    def _names(self, key: str) -> list[str]:
        value = self.data.get(key) or []
        return [value] if isinstance(value, str) else [str(v) for v in value]

    def condition(self, key: str) -> str:
        return str(self.data.get(key) or "").strip()


@dataclass
class Store:
    """Every profile under `root/{machine,process,filament}/`."""

    root: Path = STORE_DIR
    machines: list[StoredProfile] = field(default_factory=list, init=False)
    processes: list[StoredProfile] = field(default_factory=list, init=False)
    filaments: list[StoredProfile] = field(default_factory=list, init=False)
    pairings: list[Pairing] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not self.root.is_dir():
            raise FileNotFoundError(f"no profile store at {self.root}")
        self.machines = self._load("machine")
        self.processes = self._load("process")
        self.filaments = self._load("filament")
        path = self.root / PAIRINGS
        if path.is_file():
            try:
                rows = json.loads(path.read_text())["pairings"]
                self.pairings = [Pairing(**row) for row in rows]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValueError(f"{path.name} cannot be read: {exc}") from exc

    def pairing(self, filament: str, nozzle_mm: float) -> Pairing | None:
        found = [
            p for p in self.pairings
            if p.filament == filament and abs(p.nozzle_mm - nozzle_mm) < 1e-6
        ]
        if len(found) > 1:
            raise NoChoice(f"{PAIRINGS} pairs {found[0].label()} more than once")
        return found[0] if found else None

    def _load(self, kind: str) -> list[StoredProfile]:
        found = []
        for path in sorted((self.root / kind).glob("*.json")):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"profile {path.name} cannot be read: {exc}") from exc
            found.append(StoredProfile(kind=kind, path=path, data=data))
        return found


class ProfileRef(BaseModel):
    """One profile a print used: what the ledger keeps of it."""

    model_config = {"frozen": True}

    name: str
    file: str  # relative to the store, e.g. "machine/Elegoo ... - Copy.json"
    sha256: str  # of the file's bytes


class ProfilesUsed(BaseModel):
    """The three profiles a print sliced with, and the print-shop commit they're at.

    `store_dirty` says the store had uncommitted changes, so the commit alone
    would not reproduce the files; the hashes still do.
    """

    model_config = {"frozen": True}

    machine: ProfileRef
    process: ProfileRef
    filament: ProfileRef
    commit: str
    store_dirty: bool


@dataclass(frozen=True)
class Pairing:
    """One line of `profiles/pairings.json`: what goes with a filament on a nozzle."""

    filament: str
    nozzle_mm: float
    machine: str
    process: str
    drafted: bool
    why: str

    def label(self) -> str:
        return f"'{self.filament}' on a {self.nozzle_mm:g} mm nozzle"


@dataclass(frozen=True)
class Choice:
    machine: StoredProfile
    process: StoredProfile
    filament: StoredProfile
    #: How the choice was made: the pairing used, and whether it is drafted.
    notes: tuple[str, ...] = ()

    def used(self, root: Path) -> ProfilesUsed:
        commit, dirty = _git_state(root)

        def ref(p: StoredProfile) -> ProfileRef:
            return ProfileRef(
                name=p.name, file=p.path.relative_to(root).as_posix(), sha256=p.sha256
            )

        return ProfilesUsed(
            machine=ref(self.machine),
            process=ref(self.process),
            filament=ref(self.filament),
            commit=commit,
            store_dirty=dirty,
        )


def choose(
    store: Store,
    *,
    nozzle_mm: float,
    filament: str,
    machine: str | None = None,
    process: str | None = None,
) -> Choice:
    """The machine, process and filament for the fitted nozzle and loaded filament.

    `filament` is a filament preset's exact name, or a material type (`PLA`)
    that only one preset has. The machine and process come from the pairing
    for that filament and nozzle; `machine` and `process`, exact names, take
    their place when the Sponsor picks.

    Raises:
        NoChoice: On an unknown or ambiguous filament, a pairing Orca's rule
            forbids, or no single match without a pairing; each says why.
    """
    loaded = _the_filament(store, filament)
    pairing = store.pairing(loaded.name, nozzle_mm)
    machine = machine or (pairing.machine if pairing else None)
    process = process or (pairing.process if pairing else None)
    notes: tuple[str, ...] = ()
    if pairing is not None:
        notes = (
            f"pairing for {pairing.label()}"
            + (", DRAFTED, not yet confirmed by the Sponsor" if pairing.drafted else "")
            + f": {pairing.why}",
        )

    out: list[str] = []  # why each profile was left out
    named = _named(store.machines, machine, "machine", out)
    machines = [m for m in named if _nozzle_fits(m, nozzle_mm, out)]
    processes = _named(store.processes, process, "process", out)
    sets: list[tuple[StoredProfile, StoredProfile]] = []
    for m in machines:
        for p in processes:
            why = _process_misfit(p, m) or _filament_misfit(loaded, m, p)
            if why:
                out.append(f"'{m.name}' with '{p.name}': {why}")
            else:
                sets.append((m, p))

    asked = f"a {nozzle_mm:g} mm nozzle and '{loaded.name}'"
    if not sets:
        head = f"No profiles fit {asked}."
        if pairing is not None:
            head = (
                f"Orca's compatibility rule forbids the pairing for {pairing.label()} "
                f"in profiles/pairings.json. Fix the pairing or the preset in a PR."
            )
        raise NoChoice(_refusal(head, [], out))
    if len(sets) > 1:
        lines = _ties(sets)
        head = (
            f"Several profile sets fit {asked}, and profiles/pairings.json has no pairing "
            f"for it; name one, or add the pairing in a PR."
        )
        raise NoChoice(_refusal(head, lines, out))

    m, p = sets[0]
    return Choice(machine=m, process=p, filament=loaded, notes=notes)


def check_pairings(store: Store) -> list[str]:
    """Every pairing that names no preset or that Orca's rule forbids, and why."""
    problems = []
    for pairing in store.pairings:
        try:
            choose(store, nozzle_mm=pairing.nozzle_mm, filament=pairing.filament)
        except NoChoice as exc:
            problems.append(f"{pairing.label()}: {exc}")
    return problems


def unpaired(store: Store) -> list[str]:
    """Filament presets no pairing names."""
    paired = {p.filament for p in store.pairings}
    return [f.name for f in store.filaments if f.name not in paired]


def _the_filament(store: Store, filament: str) -> StoredProfile:
    """The filament preset by exact name, else the only one of that material type."""
    for f in store.filaments:
        if f.name == filament:
            return f
    of_type = [f for f in store.filaments if f.filament_type.casefold() == filament.casefold()]
    if len(of_type) == 1:
        return of_type[0]
    if of_type:
        names = "\n".join(f'  "{f.name}"' for f in of_type)
        raise NoChoice(
            f"Several {filament} presets are in the store; name the one loaded "
            f"(--filament):\n{names}"
        )
    have = "\n".join(f'  "{f.name}"  ({f.filament_type or "no type"})' for f in store.filaments)
    raise NoChoice(f"No filament preset is called or typed '{filament}'. The store has:\n{have}")


def _nozzle_fits(machine: StoredProfile, nozzle_mm: float, out: list[str]) -> bool:
    if machine.nozzle_mm is None:
        out.append(f"machine '{machine.name}': names no nozzle_diameter")
        return False
    if abs(machine.nozzle_mm - nozzle_mm) > 1e-6:
        out.append(f"machine '{machine.name}': a {machine.nozzle_mm:g} mm nozzle")
        return False
    return True


def _named(
    profiles: Iterable[StoredProfile], name: str | None, kind: str, out: list[str]
) -> list[StoredProfile]:
    """All of them, or the one called `name`. A name nobody has is refused."""
    profiles = list(profiles)
    if name is None:
        return profiles
    found = [p for p in profiles if p.name == name]
    if not found:
        have = "\n".join(f"  {p.name}" for p in profiles) or "  (none)"
        raise NoChoice(f"No {kind} profile is called '{name}'. The store has:\n{have}")
    return found


def _fits_printer(profile: StoredProfile, machine: StoredProfile) -> str:
    """Why `profile` doesn't fit `machine` by `compatible_printers`, or ''."""
    if profile.condition("compatible_printers_condition"):
        return "it has a compatible_printers_condition, which print-shop does not evaluate"
    names = profile._names("compatible_printers")
    if names and not {machine.name, machine.parent} & set(names):
        return f"its compatible_printers are {', '.join(names)}"
    return ""


def _process_misfit(process: StoredProfile, machine: StoredProfile) -> str:
    if not process._names("compatible_printers"):
        return "its compatible_printers is empty, which Orca's CLI refuses (exit 239)"
    return _fits_printer(process, machine)


def _filament_misfit(
    filament: StoredProfile, machine: StoredProfile, process: StoredProfile
) -> str:
    why = _fits_printer(filament, machine)
    if why:
        return why
    if filament.condition("compatible_prints_condition"):
        return "it has a compatible_prints_condition, which print-shop does not evaluate"
    prints = filament._names("compatible_prints")
    if prints and not {process.name, process.parent} & set(prints):
        return f"its compatible_prints are {', '.join(prints)}, not '{process.name}'"
    return ""


def _ties(sets: list[tuple[StoredProfile, StoredProfile]]) -> list[str]:
    lines = []
    for kind, flag, i in (("machine", "--machine", 0), ("process", "--process", 1)):
        names = sorted({s[i].name for s in sets})
        if len(names) > 1:
            lines.append(f"{kind} ({flag}):")
            lines += [f'  "{n}"' for n in names]
    return lines


def _refusal(head: str, choices: list[str], out: list[str]) -> str:
    parts = [head]
    if choices:
        parts += ["The candidates:", *choices]
    if out:
        parts += ["Left out:", *(f"  {line}" for line in dict.fromkeys(out))]
    return "\n".join(parts)


def _git_state(root: Path) -> tuple[str, bool]:
    """print-shop's commit and whether the store differs from it; ('', True) outside git."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "."],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "", True
    return commit, bool(status)


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


def _first_float(value: Any) -> float | None:
    try:
        return float(_first(value))
    except ValueError:
        return None
