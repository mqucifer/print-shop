"""The inventory: what's on hand, kept up a line at a time, and what a print takes from it.

mqucifer/print-shop#11. Everything runs against a temporary ledger file; no
printer is touched.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from print_shop.inventory import Inventory, InventoryError, SpoolState
from print_shop.ledger import Ledger
from print_shop.runtime import describe, profiles_for
from print_shop.slicer.store import Store, choose
from tests.fakes import STORE
from tests.test_ledger import record


@pytest.fixture
def inventory(tmp_path: Path) -> Inventory:
    inv = Inventory(tmp_path / "ledger.sqlite")
    inv.add_printer("cc1", "Elegoo Centauri Carbon")
    return inv


def _spool(inv: Inventory, variety: str = "Elegoo PLA", colour: str = "white") -> int:
    return inv.add_spool(manufacturer="Elegoo", type="PLA", variety=variety, colour=colour).id


def test_a_print_takes_the_fitted_nozzle_and_the_loaded_spool(inventory: Inventory) -> None:
    inventory.fit(0.4)
    inventory.load(_spool(inventory))
    material = inventory.for_print(None, None)
    assert (material.printer_id, material.nozzle_mm, material.variety, material.colour) == (
        "cc1", 0.4, "Elegoo PLA", "white"
    )
    assert material.line() == "cc1 with a 0.4 mm nozzle; spool #1, Elegoo PLA, white"


def test_naming_what_is_fitted_and_loaded_is_fine(inventory: Inventory) -> None:
    inventory.fit(0.4)
    inventory.load(_spool(inventory))
    assert inventory.for_print(0.4, "elegoo pla").spool_id == 1
    assert inventory.for_print(0.4, "#1").spool_id == 1


def test_a_nozzle_that_isnt_fitted_is_refused(inventory: Inventory) -> None:
    inventory.fit(0.4)
    inventory.load(_spool(inventory))
    with pytest.raises(InventoryError, match="the fitted nozzle on cc1 is 0.4 mm, not 0.6"):
        inventory.for_print(0.6, None)


def test_a_filament_that_isnt_loaded_is_refused_listing_whats_on_hand(
    inventory: Inventory,
) -> None:
    inventory.fit(0.4)
    inventory.load(_spool(inventory))
    _spool(inventory, "Elegoo PETG", "clear")
    with pytest.raises(InventoryError) as refused:
        inventory.for_print(None, "Elegoo PETG")
    text = str(refused.value)
    assert "loaded on cc1 is spool #1, Elegoo PLA, white, not Elegoo PETG" in text
    assert "spool #2  Elegoo PETG  clear  (on hand)" in text


def test_nothing_fitted_or_loaded_says_how_to_fix_it(inventory: Inventory) -> None:
    with pytest.raises(InventoryError, match="no nozzle is fitted on cc1: `stock fit 0.4`"):
        inventory.material()
    inventory.fit(0.4)
    with pytest.raises(InventoryError, match="no spool is loaded on cc1"):
        inventory.material()


def test_one_nozzle_fitted_and_one_spool_loaded_per_printer(inventory: Inventory) -> None:
    inventory.fit(0.4)
    inventory.fit(0.6, note="hardened")
    assert [(n.diameter_mm, n.printer_id) for n in inventory.nozzles()] == [
        (0.4, None), (0.6, "cc1")
    ], "the 0.4 comes off and stays owned"
    inventory.fit(0.4)
    assert len(inventory.nozzles()) == 2, "the owned 0.4 is fitted again, not a new one"

    first, second = _spool(inventory), _spool(inventory, colour="black")
    inventory.load(first)
    inventory.load(second)
    assert inventory.spool(first).state is SpoolState.ON_HAND
    assert inventory.loaded("cc1").id == second  # type: ignore[union-attr]


def test_an_empty_spool_leaves_the_listing_and_cant_be_loaded(inventory: Inventory) -> None:
    spool = _spool(inventory)
    inventory.load(spool)
    inventory.empty(spool)
    assert inventory.loaded("cc1") is None
    assert "spool #1" not in inventory.listing()
    assert "spool #1  Elegoo PLA  white  (empty)" in inventory.listing(with_empty=True)
    with pytest.raises(InventoryError, match="spool #1 is empty"):
        inventory.load(spool)


def test_more_than_one_printer_needs_naming(inventory: Inventory) -> None:
    inventory.add_printer("cc2", "Elegoo Centauri Carbon")
    with pytest.raises(InventoryError, match="name the printer .--printer.: cc1, cc2"):
        inventory.fit(0.4)
    inventory.fit(0.4, "cc2")
    assert inventory.fitted("cc2") is not None and inventory.fitted("cc1") is None


def test_a_print_row_points_at_its_spool(inventory: Inventory, tmp_path: Path) -> None:
    inventory.fit(0.4)
    inventory.load(_spool(inventory))
    material = inventory.for_print(None, None)
    choice = choose(Store(STORE), nozzle_mm=material.nozzle_mm, filament=material.variety)
    used = profiles_for(choice, STORE, tmp_path / "composed", material).used
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        ledger.record_start_requested(record().model_copy(update={"profiles": used}))
        row = ledger.get("job-1")
    assert row is not None and row.material == material
    assert "material       cc1 with a 0.4 mm nozzle; spool #1, Elegoo PLA, white" in describe(row)
    db = sqlite3.connect(tmp_path / "ledger.sqlite")
    assert db.execute("SELECT spool_id FROM print_material").fetchall() == [(1,)]


def test_the_inventory_shares_an_existing_ledger_and_its_version(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as ledger:
        ledger.record_start_requested(record())
    version = sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0]
    with Inventory(path) as inv:
        inv.add_printer("cc1", "Elegoo Centauri Carbon")
    with Ledger(path) as again:
        assert again.get("job-1") is not None
        assert again.get("job-1").material is None  # type: ignore[union-attr]
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == version
