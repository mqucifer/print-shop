"""Choosing the profiles a print uses: standards first, the Sponsor's tuning over them.

mqucifer/print-shop#8, the Sponsor's answer of 2026-10-25: a variety uses
their tuning where there is one and the standard where there isn't; several
tunings of one are listed to choose from. The small store here is built per
test; the last tests read the repository's own `profiles/`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.slicer.store import NoChoice, Store, choose, describe
from tests.fakes import STORE

M04 = "CC 0.4 nozzle"
M08 = "CC 0.8 nozzle"


def _write(root: Path, where: str, kind: str, name: str, **data: Any) -> None:
    path = root / where / kind / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, **data}))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    root = tmp_path / "profiles"
    _write(root, "standard", "machine", M04, nozzle_diameter=["0.4"], inherits="")
    _write(root, "standard", "machine", M08, nozzle_diameter=["0.8"], inherits="")
    _write(root, "standard", "process", "0.20mm", compatible_printers=[M04])
    _write(root, "standard", "process", "0.40mm", compatible_printers=[M08])
    _write(root, "standard", "filament", "Maker PLA @CC", filament_type=["PLA"],
           filament_vendor=["Maker"], nozzle_temperature=["210"],
           compatible_printers=[M04, M08])
    _write(root, "standard", "filament", "Maker PLA Silk @CC", filament_type=["PLA"],
           filament_vendor=["Maker"], compatible_printers=[M04])
    _write(root, "standard", "filament", "Maker Rapid PETG @CC", filament_type=["PETG"],
           filament_vendor=["Maker"], compatible_printers=[M04])
    _write(root, "tuned", "machine", f"{M04} - Copy", inherits=M04, bed_mesh="on")
    _write(root, "tuned", "filament", "PLA mine", inherits="Maker PLA @CC",
           nozzle_temperature=["220"])
    _write(root, "tuned", "filament", "PETG 270", inherits="Maker Rapid PETG @CC")
    _write(root, "tuned", "filament", "PETG 240", inherits="Maker Rapid PETG @CC")
    return root


def test_a_variety_uses_the_sponsors_tuning_over_its_standard(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.4, filament="Maker PLA")
    assert choice.filament.name == "PLA mine"
    assert choice.filament.composed["nozzle_temperature"] == ["220"], "the tuning wins"
    assert choice.filament.composed["filament_type"] == ["PLA"], "the rest is the standard's"
    assert choice.filament.composed["inherits"] == "Maker PLA @CC"
    assert choice.machine.name == f"{M04} - Copy", "the tuned machine is the default too"
    assert choice.process.name == "0.20mm", "untuned: the standard"
    assert "process '0.20mm': the standard; you haven't tuned it" in choice.notes


def test_an_untuned_variety_uses_the_standard(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.4, filament="maker pla silk")
    assert choice.filament.name == "Maker PLA Silk @CC" and choice.filament.standard is None


def test_several_tunings_of_one_are_listed_to_choose_from(store: Path) -> None:
    with pytest.raises(NoChoice) as listed:
        choose(Store(store), nozzle_mm=0.4, filament="Maker Rapid PETG")
    assert '"PETG 240"' in str(listed.value) and '"PETG 270"' in str(listed.value)
    choice = choose(Store(store), nozzle_mm=0.4, filament="PETG 270")
    assert choice.filament.name == "PETG 270"


def test_a_type_lists_its_varieties_with_their_tunings(store: Path) -> None:
    with pytest.raises(NoChoice) as listed:
        choose(Store(store), nozzle_mm=0.4, filament="PLA")
    text = str(listed.value)
    assert '"Maker PLA"  (Maker / PLA)\n      tuned: "PLA mine"' in text
    assert '"Maker PLA Silk"  (Maker / PLA)' in text
    assert choose(Store(store), nozzle_mm=0.4, filament="petg 240").filament.name == "PETG 240"


def test_the_nozzle_picks_the_machine_and_its_process(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.8, filament="Maker PLA")
    assert (choice.machine.name, choice.process.name) == (M08, "0.40mm")


def test_a_filament_orcas_rule_forbids_is_refused(store: Path) -> None:
    with pytest.raises(NoChoice, match="its compatible_printers are CC 0.4 nozzle"):
        choose(Store(store), nozzle_mm=0.8, filament="Maker PLA Silk")


def test_no_machine_for_the_nozzle_says_what_there_is(store: Path) -> None:
    with pytest.raises(NoChoice, match="No standard machine fits") as refused:
        choose(Store(store), nozzle_mm=0.6, filament="Maker PLA")
    assert f'"{M08}"' in str(refused.value)


def test_a_tuning_of_a_standard_the_store_lacks_is_refused(store: Path) -> None:
    _write(store, "tuned", "process", "orphan", inherits="0.30mm")
    with pytest.raises(ValueError, match="tunes '0.30mm', which isn't a standard process"):
        Store(store)


def test_a_process_with_no_printers_is_left_out_as_orcas_cli_refuses_it(store: Path) -> None:
    _write(store, "standard", "process", "open", compatible_printers=[])
    with pytest.raises(NoChoice, match="doesn't fit"):
        choose(Store(store), nozzle_mm=0.4, filament="Maker PLA", process="open")


# --------------------------------------------------------------------------
# The repository's own store
# --------------------------------------------------------------------------


def test_the_stores_choices_are_the_sponsors() -> None:
    store = Store(STORE)
    pla = choose(store, nozzle_mm=0.4, filament="Elegoo PLA")
    assert (pla.machine.name, pla.process.name, pla.filament.name) == (
        "Elegoo Centauri Carbon 0.4 nozzle - Copy",
        "0.20mm Standard @Elegoo CC 0.4 nozzle - Copy",
        "Elegoo PLA @ECC - Copy",
    )
    assert choose(store, nozzle_mm=0.4, filament="Elegoo PETG").filament.name == "Elegoo PETG @ECC"
    with pytest.raises(NoChoice, match="tuned 'Elegoo Rapid PETG @ECC' more than once"):
        choose(store, nozzle_mm=0.4, filament="Elegoo Rapid PETG")


def test_the_store_is_scoped_to_the_0_4_machine_and_pla_and_petg() -> None:
    store = Store(STORE)
    assert [m.nozzle_mm for m in store.all("machine")] == [0.4, 0.4]
    assert {f.filament_type for f in store.all("filament")} == {"PLA", "PETG"}
    with pytest.raises(NoChoice):
        choose(store, nozzle_mm=0.8, filament="Elegoo PLA")
    assert "  \"Elegoo Rapid PETG\"  (Elegoo / PETG)" in describe(store)
