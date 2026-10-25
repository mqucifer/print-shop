"""print-shop's own Orca profiles: Orca's standards, and the Sponsor's tunings over them.

The Sponsor, 2026-10-25, on mqucifer/print-shop#6 and #8: print-shop keeps its
profiles in its repository, and starts "with the standards first":

- `profiles/standard/<kind>/`: Orca's own system presets, whole, for what
  `profiles/scope.json` covers (today the 0.4 mm machine, its 0.20 mm
  process, and PLA and PETG);
- `profiles/tuned/<kind>/`: each of the Sponsor's tuned presets as an
  **overlay**, only the keys it changes, naming its standard in `inherits`.

Both are written by `scripts/import_profiles.py`, and a change to either is a
reviewed PR. A print composes the profile it slices with (standard, then the
tuning over it) into whole files, so Orca resolves nothing and an Orca update
cannot change a print silently.

**The Sponsor's model:** the machine (per nozzle), then the filament by
manufacturer, type and variety, where "most of these settings will be based
on the variety", then the nozzle, which sets speed, flow and heat through the
machine and process. **The Sponsor says what's fitted:** the nozzle and the
loaded filament. For each of machine, process and filament, **their tuning
is used where there is one, and the standard only where they haven't
tuned.** Several tunings of one standard (RAPID PETG at 270 C and at 240 C)
refuse and list them, so the Sponsor picks by name.

**Orca's compatibility rule applies, strictly,** over the composed profiles:
- a machine fits when its `nozzle_diameter` is the fitted nozzle;
- a process or filament fits a machine when its `compatible_printers` names
  the machine or the machine's standard, as Orca's CLI checks it;
- a filament fits a process when its `compatible_prints`, if any, names it.

Tested facts about Orca's CLI (2.3, on the Mac, 2026-10-25), which the rule
keeps to:
- a whole tuned machine without `inherits` naming its standard exits 239,
  "process not compatible";
- a process whose `compatible_printers` is empty also exits 239, though Orca's
  UI reads an empty list as "every printer". Such a process is left out here.
- a filament whose `compatible_printers` is empty slices.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

__all__ = [
    "STORE_DIR",
    "Choice",
    "NoChoice",
    "ProfileRef",
    "ProfilesUsed",
    "Store",
    "StoredProfile",
    "choose",
    "describe",
]

#: `profiles/` at the top of print-shop's checkout.
STORE_DIR = Path(__file__).resolve().parents[3] / "profiles"

KINDS = ("machine", "process", "filament")


class NoChoice(ValueError):
    """The fitted nozzle and filament don't pick one set of profiles. Says why."""


@dataclass(frozen=True)
class StoredProfile:
    """A standard, or a tuning over one (`standard` set)."""

    kind: str
    path: Path
    data: dict[str, Any]
    standard: StoredProfile | None = None

    @property
    def name(self) -> str:
        return str(self.data.get("name", self.path.stem))

    @property
    def composed(self) -> dict[str, Any]:
        """The whole profile: the standard, and this tuning's keys over it."""
        if self.standard is None:
            return self.data
        # "from": "User" as Orca writes a user preset: with "system", Orca's CLI
        # checks compatibility by the tuning's own name, ignores `inherits`, and
        # exits 239 (tested 2026-10-25).
        return {**self.standard.data, **self.data, "inherits": self.standard.name,
                "from": "User"}

    @property
    def base_name(self) -> str:
        """The standard's name: this one's own, if it is one."""
        return self.standard.name if self.standard is not None else self.name

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def get(self, key: str) -> str:
        return _first(self.composed.get(key))

    @property
    def nozzle_mm(self) -> float | None:
        try:
            return float(self.get("nozzle_diameter"))
        except ValueError:
            return None

    @property
    def filament_type(self) -> str:
        return self.get("filament_type")

    @property
    def manufacturer(self) -> str:
        return self.get("filament_vendor")

    @property
    def variety(self) -> str:
        """The standard's name without Orca's printer suffix: "Elegoo Rapid PETG"."""
        return re.sub(r"\s+@\S+$", "", self.base_name)

    def names(self, key: str) -> list[str]:
        value = self.composed.get(key) or []
        return [value] if isinstance(value, str) else [str(v) for v in value]


@dataclass
class Store:
    """`root/standard/<kind>/` and `root/tuned/<kind>/`, each tuning tied to its standard."""

    root: Path = STORE_DIR
    standards: dict[str, list[StoredProfile]] = field(default_factory=dict, init=False)
    tunings: dict[str, list[StoredProfile]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if not (self.root / "standard").is_dir():
            raise FileNotFoundError(f"no profile store at {self.root}")
        for kind in KINDS:
            self.standards[kind] = [
                StoredProfile(kind, path, _read(path))
                for path in sorted((self.root / "standard" / kind).glob("*.json"))
            ]
            by_name = {s.name: s for s in self.standards[kind]}
            self.tunings[kind] = []
            for path in sorted((self.root / "tuned" / kind).glob("*.json")):
                data = _read(path)
                standard = by_name.get(str(data.get("inherits", "")))
                if standard is None:
                    raise ValueError(
                        f"{path.name} tunes '{data.get('inherits')}', which isn't a standard "
                        f"{kind} in the store"
                    )
                self.tunings[kind].append(StoredProfile(kind, path, data, standard))

    def of(self, standard: StoredProfile) -> list[StoredProfile]:
        """The Sponsor's tunings of `standard`."""
        return [t for t in self.tunings[standard.kind] if t.standard is standard]

    def all(self, kind: str) -> list[StoredProfile]:
        return self.standards[kind] + self.tunings[kind]


class ProfileRef(BaseModel):
    """One profile a print used: what the ledger keeps of it."""

    model_config = {"frozen": True}

    name: str
    file: str  # relative to the store, e.g. "tuned/filament/Elegoo PLA @ECC - Copy.json"
    sha256: str  # of the file's bytes
    #: For a tuning, the standard it was composed over.
    standard_file: str = ""
    standard_sha256: str = ""


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
class Choice:
    machine: StoredProfile
    process: StoredProfile
    filament: StoredProfile
    #: How each was chosen: the Sponsor's tuning, or the standard for want of one.
    notes: tuple[str, ...] = ()

    def compose(self, workdir: Path) -> dict[str, Path]:
        """Each profile written whole into `workdir`, as Orca slices with it."""
        workdir.mkdir(parents=True, exist_ok=True)
        out = {}
        for kind, profile in (("machine", self.machine), ("process", self.process),
                              ("filament", self.filament)):
            path = workdir / f"{kind}.json"
            path.write_text(json.dumps(profile.composed, indent=2))
            out[kind] = path
        return out

    def used(self, root: Path) -> ProfilesUsed:
        commit, dirty = _git_state(root)

        def ref(p: StoredProfile) -> ProfileRef:
            standard = p.standard
            return ProfileRef(
                name=p.name,
                file=p.path.relative_to(root).as_posix(),
                sha256=p.sha256,
                standard_file=standard.path.relative_to(root).as_posix() if standard else "",
                standard_sha256=standard.sha256 if standard else "",
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
    """The machine, process and filament for the fitted nozzle and the loaded filament.

    `filament` is a variety ("Elegoo Rapid PETG"), a standard's name, or one of
    the Sponsor's tunings by name; a material type ("PLA") lists the varieties.
    `machine` and `process` name a standard or a tuning, for when the Sponsor
    picks.

    Raises:
        NoChoice: On no match, several tunings of one standard, or a
            combination Orca's rule forbids; each lists what to choose from.
    """
    notes: list[str] = []
    m = _pick(store, "machine", machine, notes, lambda s: _nozzle_fits(s, nozzle_mm))
    p = _pick(store, "process", process, notes, lambda s: not _process_misfit(s, m))
    f = _the_filament(store, filament, notes)
    why = _process_misfit(p, m) or _filament_misfit(f, m, p)
    if why:
        raise NoChoice(
            f"'{f.name}' can't print with '{m.name}' and '{p.name}' by Orca's rule: {why}"
        )
    return Choice(machine=m, process=p, filament=f, notes=tuple(notes))


def _pick(
    store: Store,
    kind: str,
    named: str | None,
    notes: list[str],
    fits: Callable[[StoredProfile], bool],
) -> StoredProfile:
    """The one standard that fits, then the Sponsor's tuning of it if there's one."""
    if named is not None:
        found = [s for s in store.all(kind) if s.name == named]
        if not found:
            have = "\n".join(f'  "{s.name}"' for s in store.all(kind)) or "  (none)"
            raise NoChoice(f"No {kind} profile is called '{named}'. The store has:\n{have}")
        if not fits(found[0]):
            raise NoChoice(f"The {kind} '{named}' doesn't fit what's fitted")
        return found[0]
    standards = [s for s in store.standards[kind] if fits(s)]
    if len(standards) != 1:
        have = "\n".join(f'  "{s.name}"' for s in store.standards[kind]) or "  (none)"
        what = "No" if not standards else "More than one"
        raise NoChoice(f"{what} standard {kind} fits. The store's standards:\n{have}")
    return _tuning_of(store, standards[0], f"--{kind}", notes)


def _tuning_of(
    store: Store, standard: StoredProfile, flag: str, notes: list[str]
) -> StoredProfile:
    tunings = store.of(standard)
    if not tunings:
        notes.append(f"{standard.kind} '{standard.name}': the standard; you haven't tuned it")
        return standard
    if len(tunings) > 1:
        names = "\n".join(f'  "{t.name}"' for t in tunings)
        raise NoChoice(
            f"You've tuned '{standard.name}' more than once; name the one to use "
            f"({flag}):\n{names}"
        )
    notes.append(f"{standard.kind} '{tunings[0].name}': your tuning of '{standard.name}'")
    return tunings[0]


def _the_filament(store: Store, filament: str, notes: list[str]) -> StoredProfile:
    """By tuning name, standard name or variety; a type lists the varieties."""
    want = filament.casefold()
    for t in store.tunings["filament"]:
        if t.name.casefold() == want:
            notes.append(f"filament '{t.name}': your tuning of '{t.base_name}'")
            return t
    for s in store.standards["filament"]:
        if want in (s.name.casefold(), s.variety.casefold()):
            return _tuning_of(store, s, "--filament", notes)
    of_type = [s for s in store.standards["filament"] if s.filament_type.casefold() == want]
    if len(of_type) == 1:
        return _tuning_of(store, of_type[0], "--filament", notes)
    listing = _varieties(store, of_type or store.standards["filament"])
    if of_type:
        raise NoChoice(f"Several {filament} varieties are in the store; name the one loaded "
                       f"(--filament):\n{listing}")
    raise NoChoice(f"No filament in the store is called or typed '{filament}'. "
                   f"The store has:\n{listing}")


def _varieties(store: Store, standards: list[StoredProfile]) -> str:
    """Manufacturer / type / variety, with the Sponsor's tunings under each."""
    lines = []
    for s in sorted(standards, key=lambda s: (s.manufacturer, s.filament_type, s.variety)):
        lines.append(f'  "{s.variety}"  ({s.manufacturer} / {s.filament_type})')
        lines += [f'      tuned: "{t.name}"' for t in store.of(s)]
    return "\n".join(lines)


def describe(store: Store) -> str:
    """The store as `print_job.py --filaments` shows it."""
    lines = []
    for kind in ("machine", "process"):
        for s in store.standards[kind]:
            lines.append(f"{kind}: {s.name}")
            lines += [f'    tuned: "{t.name}"' for t in store.of(s)]
    lines += ["filament (manufacturer / type), by variety:",
              _varieties(store, store.standards["filament"])]
    return "\n".join(lines)


def _nozzle_fits(machine: StoredProfile, nozzle_mm: float) -> bool:
    return machine.nozzle_mm is not None and abs(machine.nozzle_mm - nozzle_mm) < 1e-6


def _fits_printer(profile: StoredProfile, machine: StoredProfile) -> str:
    """Why `profile` doesn't fit `machine` by `compatible_printers`, or ''."""
    if _first(profile.composed.get("compatible_printers_condition")).strip():
        return "it has a compatible_printers_condition, which print-shop does not evaluate"
    names = profile.names("compatible_printers")
    if names and not {machine.name, machine.base_name} & set(names):
        return f"its compatible_printers are {', '.join(names)}"
    return ""


def _process_misfit(process: StoredProfile, machine: StoredProfile) -> str:
    if not process.names("compatible_printers"):
        return "its compatible_printers is empty, which Orca's CLI refuses (exit 239)"
    return _fits_printer(process, machine)


def _filament_misfit(
    filament: StoredProfile, machine: StoredProfile, process: StoredProfile
) -> str:
    why = _fits_printer(filament, machine)
    if why:
        return why
    if _first(filament.composed.get("compatible_prints_condition")).strip():
        return "it has a compatible_prints_condition, which print-shop does not evaluate"
    prints = filament.names("compatible_prints")
    if prints and not {process.name, process.base_name} & set(prints):
        return f"its compatible_prints are {', '.join(prints)}, not '{process.name}'"
    return ""


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


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"profile {path.name} cannot be read: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"profile {path.name} is not a JSON object")
    return data


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")
