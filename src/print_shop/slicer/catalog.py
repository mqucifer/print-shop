"""Orca's own preset catalog, read and resolved, for the import into `profiles/`.

print-shop slices only from its own files (the Sponsor, 2026-10-25, on
mqucifer/print-shop#6). They are seeded from the Sponsor's tuned Orca presets
by `scripts/import_profiles.py`, which is the only reader of Orca's folders.
Nothing here writes to them.

Orca keeps a user preset as a thin overlay: a few keys and `inherits`, the
name of its parent. The parent may be another user preset or a system one, and
system presets inherit too, down to `fdm_machine_common`. Resolving a preset
walks that chain, the child's keys winning.

Two things about the real tree, both found on the Sponsor's Mac:

- **A name can appear in more than one system folder.** `system/Elegoo/` and
  `system/OrcaFilamentLibrary/` both carry `Elegoo PLA @base` and the
  `fdm_filament_*` bases, and `system/Custom/` carries `fdm_machine_common`.
  The printer's vendor folder is searched first, then the filament library,
  then the rest, so the parent is the one Orca's vendor bundle means.
- **An `inherits` can differ from its parent's name in case only.** Five of
  the Sponsor's RAPID PETG presets inherit `Elegoo RAPID PETG @ECC`, and the
  system preset is `Elegoo Rapid PETG @ECC`. Such a name resolves, with a
  warning naming both spellings (the operator's call on mqucifer/print-shop#6).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_ORCA_DIR",
    "KINDS",
    "Catalog",
    "CatalogError",
    "Preset",
    "ImportResult",
    "Scope",
    "flatten",
    "import_presets",
    "overlay",
]

#: Orca's data directory on macOS. The import reads it; slicing never does.
DEFAULT_ORCA_DIR = Path("~/Library/Application Support/OrcaSlicer").expanduser()

#: The three tiers, as Orca names a preset's `type` and its folder.
KINDS = ("machine", "process", "filament")

#: Machine keys that hold the printer's address or credentials. A preset in
#: the store is in a public repository, so these never leave the Mac.
_PRIVATE_PREFIXES = ("print_host", "printhost")

#: Metadata, not settings: the Orca version that saved the preset. Its
#: four-part form (2.3.1.x) reads as an address to the repository's
#: address check, and it would change the store with every Orca update.
_METADATA = ("version",)

#: The vendor folders searched first, in order, for a system parent.
_FIRST_FOLDERS = ("Elegoo", "OrcaFilamentLibrary")


class CatalogError(ValueError):
    """A preset that cannot be resolved: an unreadable file or a missing parent."""


@dataclass(frozen=True)
class Preset:
    """One preset file in Orca's catalog, as it is on disk."""

    kind: str
    name: str
    path: Path
    data: dict[str, Any]
    system: bool

    @property
    def inherits(self) -> str:
        value = self.data.get("inherits")
        return value if isinstance(value, str) else ""


@dataclass
class Catalog:
    """Orca's presets: the user's in `user/default/`, then the system's.

    `warnings` collects what resolution let through but a person should see,
    such as an `inherits` matched only by ignoring case.
    """

    orca_dir: Path
    warnings: list[str] = field(default_factory=list)
    _by_name: dict[str, Preset] = field(default_factory=dict, init=False)
    _by_folded: dict[str, Preset] = field(default_factory=dict, init=False)
    _user: list[Preset] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        system = self.orca_dir / "system"
        for path in _system_files(system):
            preset = _read(path, system=True)
            if preset is not None and preset.name not in self._by_name:
                self._index(preset)
        # A user preset shadows a system one of the same name, as in Orca.
        for path in sorted((self.orca_dir / "user" / "default").rglob("*.json")):
            preset = _read(path, system=False)
            if preset is not None:
                self._user.append(preset)
                self._index(preset)

    def _index(self, preset: Preset) -> None:
        self._by_name[preset.name] = preset
        self._by_folded[preset.name.casefold()] = preset

    def user_presets(self, kind: str | None = None) -> list[Preset]:
        """The Sponsor's own presets, of one kind or all."""
        return [p for p in self._user if kind is None or p.kind == kind]

    def get(self, name: str, *, child: str = "") -> Preset:
        """The preset called `name`, matched exactly, else ignoring case.

        Raises:
            CatalogError: If no preset has that name in any case.
        """
        found = self._by_name.get(name)
        if found is not None:
            return found
        found = self._by_folded.get(name.casefold())
        if found is None:
            where = f" (inherited by '{child}')" if child else ""
            raise CatalogError(f"no Orca preset is called '{name}'{where}")
        warning = (
            f"'{child or name}' inherits '{name}', which Orca spells '{found.name}': "
            "matched ignoring case"
        )
        if warning not in self.warnings:
            self.warnings.append(warning)
        return found

    def chain(self, preset: Preset) -> Iterator[Preset]:
        """The preset, then each parent in turn, to the root.

        Raises:
            CatalogError: On a missing parent or a loop.
        """
        seen: set[str] = set()
        current: Preset | None = preset
        while current is not None:
            if current.name in seen:
                raise CatalogError(f"'{preset.name}' inherits in a loop through '{current.name}'")
            seen.add(current.name)
            yield current
            parent = current.inherits
            current = self.get(parent, child=current.name) if parent else None

    def resolve(self, preset: Preset) -> dict[str, Any]:
        """Every setting the preset has, its own and inherited; no `inherits`."""
        merged: dict[str, Any] = {}
        for link in reversed(list(self.chain(preset))):
            merged.update(link.data)
        merged.pop("inherits", None)
        return merged

    def system_parent(self, preset: Preset) -> str:
        """The nearest system preset a person could select, in the chain.

        That is what the flattened copy keeps as its `inherits`: Orca's CLI
        checks a process's `compatible_printers` against the machine's system
        parent, and exits 239 when a flattened machine names none.
        """
        for link in self.chain(preset):
            if link.system and link.data.get("instantiation") == "true":
                return link.name
        return ""


def flatten(catalog: Catalog, preset: Preset) -> dict[str, Any]:
    """The preset as print-shop stores it: whole, with its system parent named.

    Keys holding the printer's address or credentials are left out, and so is
    Orca's version stamp.
    """
    resolved = catalog.resolve(preset)
    for key in [k for k in resolved if k.startswith(_PRIVATE_PREFIXES) or k in _METADATA]:
        del resolved[key]
    resolved["inherits"] = catalog.system_parent(preset)
    return resolved


@dataclass(frozen=True)
class Scope:
    """`profiles/scope.json`: the standards imported whole, and the tunings left out."""

    standard: dict[str, tuple[str, ...]]
    left_out: dict[str, str]

    @classmethod
    def read(cls, path: Path) -> Scope:
        data = json.loads(path.read_text())
        return cls(
            standard={kind: tuple(data["standard"].get(kind, ())) for kind in KINDS},
            left_out=dict(data.get("left_out", {})),
        )


@dataclass
class ImportResult:
    """What an import changed in the store, by file relative to it."""

    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    #: In the store and no longer imported. Kept, and listed: removing a
    #: profile is a decision for the PR, not the import.
    only_in_store: list[str] = field(default_factory=list)
    #: The Sponsor's presets not imported, each with why.
    not_imported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def store_text(data: dict[str, Any]) -> str:
    """A profile as the store writes it: sorted keys, so a diff shows only changes."""
    return json.dumps(data, indent=4, sort_keys=True, ensure_ascii=False) + "\n"


#: Keys an overlay never carries: its identity is its name and its standard.
_IDENTITY = ("name", "inherits", "from", "instantiation", "setting_id", "is_custom_defined",
             "printer_settings_id", "print_settings_id", "filament_settings_id")


def overlay(catalog: Catalog, preset: Preset, standard: Preset) -> dict[str, Any]:
    """The Sponsor's tuning as only the keys that differ from its standard."""
    tuned = flatten(catalog, preset)
    base = flatten(catalog, standard)
    changed = {
        k: v for k, v in tuned.items() if k not in _IDENTITY and base.get(k) != v
    }
    return {"name": preset.name, "inherits": standard.name, **changed}


def import_presets(
    catalog: Catalog, store: Path, scope: Scope, *, write: bool = True
) -> ImportResult:
    """Orca's standards in scope, whole, and the Sponsor's tunings of them, as overlays.

    `store/standard/<kind>/<name>.json` holds each standard, and
    `store/tuned/<kind>/<name>.json` each tuning of one: only the keys it
    changes, naming its standard in `inherits` (the Sponsor, 2026-10-25,
    on mqucifer/print-shop#8: "start with the standards first").

    Raises:
        CatalogError: If a preset cannot be resolved; nothing is written then.
    """
    planned: dict[Path, str] = {}
    result = ImportResult()
    in_scope: dict[str, Preset] = {}
    for kind in KINDS:
        for name in scope.standard[kind]:
            standard = catalog.get(name)
            in_scope[standard.name] = standard
            whole = {**flatten(catalog, standard), "inherits": ""}
            planned[store / "standard" / kind / f"{_file_name(name)}.json"] = store_text(whole)
    for preset in catalog.user_presets():
        if preset.name in scope.left_out:
            result.not_imported.append(f"{preset.name}: {scope.left_out[preset.name]}")
            continue
        # Its own system parent, not any ancestor: Orca's 0.8 nozzle machine
        # itself inherits the 0.4, and the Sponsor's 0.8 copy isn't a 0.4 tuning.
        parent = catalog.system_parent(preset)
        if parent not in in_scope:
            result.not_imported.append(
                f"{preset.name}: its standard, '{parent or 'none'}', is not in scope.json"
            )
            continue
        planned[store / "tuned" / preset.kind / f"{_file_name(preset.name)}.json"] = store_text(
            overlay(catalog, preset, in_scope[parent])
        )

    result.warnings = list(catalog.warnings)
    for path, text in sorted(planned.items()):
        rel = path.relative_to(store).as_posix()
        if path.is_file() and path.read_text() == text:
            result.unchanged.append(rel)
            continue
        result.written.append(rel)
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    for path in sorted(store.glob("*/*/*.json")):
        if path not in planned:
            result.only_in_store.append(path.relative_to(store).as_posix())
    return result


def _file_name(name: str) -> str:
    if "/" in name or name.startswith("."):
        raise CatalogError(f"preset name '{name}' cannot be a file name")
    return name


def _system_files(system: Path) -> list[Path]:
    """Every system preset file, the vendor's own folders first."""
    first = [system / name for name in _FIRST_FOLDERS]
    files = [p for folder in first for p in sorted(folder.rglob("*.json"))]
    rest = sorted(
        p for p in system.rglob("*.json") if not any(p.is_relative_to(f) for f in first)
    )
    return files + rest


def _read(path: Path, *, system: bool) -> Preset | None:
    """A preset file, or None for anything else under the tree (vendor indexes)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    kind = data.get("type") or _kind_from_path(path)
    if not isinstance(name, str) or kind not in KINDS:
        return None
    return Preset(kind=kind, name=name, path=path, data=data, system=system)


def _kind_from_path(path: Path) -> str:
    """User presets often carry no `type`; their folder says it."""
    for part in reversed(path.parts):
        if part in KINDS:
            return part
    return ""
