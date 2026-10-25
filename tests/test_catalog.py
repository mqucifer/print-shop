"""Reading Orca's catalog for the import: `inherits` resolved as Orca's UI resolves it.

Each case is a shape found in the Sponsor's real Orca tree on 2026-10-25, built
here in a temporary directory so CI needs no Orca.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.slicer.catalog import Catalog, CatalogError, flatten, import_presets


def _write(path: Path, **data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def orca(tmp_path: Path) -> Path:
    """A small Orca tree: Elegoo's bundle, the filament library, and two user presets."""
    root = tmp_path / "OrcaSlicer"
    system = root / "system"
    # The same base name in two system folders, as in the real tree. Elegoo's
    # own folder is what its bundle means, and it says 60 C.
    _write(system / "Elegoo/filament/fdm_filament_pla.json", type="filament",
           name="fdm_filament_pla", filament_type=["PLA"], textured_plate_temp=["60"])
    _write(system / "OrcaFilamentLibrary/filament/base/fdm_filament_pla.json",
           type="filament", name="fdm_filament_pla", textured_plate_temp=["55"])
    _write(system / "Elegoo/filament/ECC/Elegoo Rapid PETG @ECC.json", type="filament",
           name="Elegoo Rapid PETG @ECC", inherits="fdm_filament_pla", instantiation="true",
           filament_type=["PETG"])
    _write(system / "Elegoo/machine/fdm_machine_common.json", type="machine",
           name="fdm_machine_common", printable_height="256")
    _write(system / "Custom/machine/fdm_machine_common.json", type="machine",
           name="fdm_machine_common", printable_height="999")
    _write(system / "Elegoo/machine/ECC/Elegoo Centauri Carbon 0.4 nozzle.json",
           type="machine", name="Elegoo Centauri Carbon 0.4 nozzle",
           inherits="fdm_machine_common", instantiation="true", nozzle_diameter=["0.4"])
    _write(system / "Elegoo.json", name="Elegoo", version="1")  # a vendor index
    user = root / "user" / "default"
    # Orca writes user presets without `type`; the folder says it.
    _write(user / "machine/Mine - Copy.json", name="Mine - Copy", from_="User",
           inherits="Elegoo Centauri Carbon 0.4 nozzle", print_host="printer.invalid",
           printhost_apikey="secret", version="2.3." + "1.10")
    _write(user / "filament/PETG 240.json", name="PETG 240",
           inherits="Elegoo RAPID PETG @ECC", nozzle_temperature=["240"])
    return root


def test_a_parent_is_found_in_the_vendors_own_folder_first(orca: Path) -> None:
    catalog = Catalog(orca)
    machine = catalog.resolve(catalog.get("Mine - Copy"))
    assert machine["printable_height"] == "256", "Elegoo's base, not Custom's"
    petg = catalog.resolve(catalog.get("PETG 240"))
    assert petg["textured_plate_temp"] == ["60"], "Elegoo's base, not the library's"
    assert petg["nozzle_temperature"] == ["240"], "the child's own value wins"


def test_an_inherits_differing_in_case_resolves_with_a_warning(orca: Path) -> None:
    catalog = Catalog(orca)
    catalog.resolve(catalog.get("PETG 240"))
    catalog.resolve(catalog.get("PETG 240"))
    assert catalog.warnings == [
        "'PETG 240' inherits 'Elegoo RAPID PETG @ECC', which Orca spells "
        "'Elegoo Rapid PETG @ECC': matched ignoring case"
    ], "once, naming both spellings"


def test_a_missing_parent_is_refused(orca: Path) -> None:
    _write(orca / "user/default/process/Orphan.json", name="Orphan", inherits="Gone")
    catalog = Catalog(orca)
    with pytest.raises(CatalogError, match="'Gone'"):
        catalog.resolve(catalog.get("Orphan"))


def test_flattened_keeps_the_system_parent_and_drops_the_address(orca: Path) -> None:
    catalog = Catalog(orca)
    flat = flatten(catalog, catalog.get("Mine - Copy"))
    assert flat["inherits"] == "Elegoo Centauri Carbon 0.4 nozzle", "Orca's CLI needs it"
    assert flat["nozzle_diameter"] == ["0.4"]
    assert not [k for k in flat if k.startswith(("print_host", "printhost"))]
    assert "version" not in flat, "Orca's stamp reads as an address, and isn't a setting"


def test_the_import_writes_only_the_users_presets_and_reports_changes(
    orca: Path, tmp_path: Path
) -> None:
    store = tmp_path / "profiles"
    first = import_presets(Catalog(orca), store)
    assert first.written == ["filament/PETG 240.json", "machine/Mine - Copy.json"]

    _write(store / "process/Kept.json", name="Kept")
    again = import_presets(Catalog(orca), store)
    assert again.written == []
    assert again.unchanged == first.written
    assert again.only_in_store == ["process/Kept.json"], "listed, never removed"
    assert (store / "process/Kept.json").exists()


def test_a_dry_import_writes_nothing(orca: Path, tmp_path: Path) -> None:
    store = tmp_path / "profiles"
    result = import_presets(Catalog(orca), store, write=False)
    assert result.written and not store.exists()


def test_the_import_never_writes_to_orcas_folders(orca: Path, tmp_path: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in orca.rglob("*")}
    import_presets(Catalog(orca), tmp_path / "profiles")
    assert {p: p.stat().st_mtime_ns for p in orca.rglob("*")} == before
