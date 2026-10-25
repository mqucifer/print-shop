"""The choice of profiles per print: the fitted nozzle, the loaded filament, the reviewed pairing.

mqucifer/print-shop#6. The small store here is built per test; the last tests
read the repository's own `profiles/`, as the Sponsor's prints will.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.slicer.store import NoChoice, Store, check_pairings, choose, unpaired
from tests.fakes import STORE

M04 = "CC 0.4 - Copy"
M04_TPU = "CC 0.4 - TPU"
M08 = "CC 0.8 - Copy"
P04 = "0.20mm - Copy"
P_TPU = "0.30mm - TPU"
P08 = "0.40mm - Copy"


def _write(root: Path, kind: str, name: str, **data: Any) -> None:
    path = root / kind / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, **data}))


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """Two 0.4 machines and a 0.8, as the Sponsor's: copies of Orca's system machines."""
    root = tmp_path / "profiles"
    _write(root, "machine", M04, inherits="CC 0.4", nozzle_diameter=["0.4"])
    _write(root, "machine", M04_TPU, inherits="CC 0.4", nozzle_diameter=["0.4"])
    _write(root, "machine", M08, inherits="CC 0.8", nozzle_diameter=["0.8"])
    _write(root, "process", P04, inherits="0.20mm", compatible_printers=["CC 0.4"])
    _write(root, "process", P_TPU, inherits="0.20mm", compatible_printers=["CC 0.4"])
    _write(root, "process", P08, inherits="0.40mm", compatible_printers=["CC 0.8"])
    _write(root, "filament", "PLA tuned", filament_type=["PLA"],
           compatible_printers=["CC 0.4", "CC 0.8"])
    _write(root, "filament", "PLA silk", filament_type=["PLA"])
    _write(root, "filament", "TPU only on the copy", filament_type=["TPU"],
           compatible_printers=[M04])
    _write(root, "filament", "PETG", filament_type=["PETG"])
    pairings = [
        {"filament": "PLA tuned", "nozzle_mm": 0.4, "machine": M04, "process": P04,
         "drafted": False, "why": "the tuned copies"},
        {"filament": "PLA tuned", "nozzle_mm": 0.8, "machine": M08, "process": P08,
         "drafted": True, "why": "the 0.8 copies"},
        {"filament": "TPU only on the copy", "nozzle_mm": 0.4, "machine": M04_TPU,
         "process": P_TPU, "drafted": True, "why": "TPU goes with TPU"},
    ]
    (root / "pairings.json").write_text(json.dumps({"pairings": pairings}))
    return root


def test_the_pairing_names_the_machine_and_process(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.4, filament="PLA tuned")
    assert (choice.machine.name, choice.process.name) == (M04, P04)
    assert choice.notes == ("pairing for 'PLA tuned' on a 0.4 mm nozzle: the tuned copies",)


def test_the_same_filament_on_another_nozzle_has_its_own_pairing(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.8, filament="PLA tuned")
    assert (choice.machine.name, choice.process.name) == (M08, P08)
    assert "DRAFTED" in choice.notes[0], "a drafted pairing says so at the command"


def test_a_material_type_is_a_shortcut_only_when_one_preset_has_it(store: Path) -> None:
    with pytest.raises(NoChoice) as tie:
        choose(Store(store), nozzle_mm=0.4, filament="pla")
    assert '"PLA tuned"' in str(tie.value) and '"PLA silk"' in str(tie.value)
    choice = choose(
        Store(store), nozzle_mm=0.4, filament="petg", machine=M04, process=P04
    )
    assert choice.filament.name == "PETG"


def test_an_unknown_filament_lists_what_the_store_has(store: Path) -> None:
    with pytest.raises(NoChoice, match=r'"PLA silk"  \(PLA\)'):
        choose(Store(store), nozzle_mm=0.4, filament="ASA")


def test_a_pairing_orcas_rule_forbids_is_refused_saying_why(store: Path) -> None:
    """The filament is marked for the copy machine only; the pairing names the TPU one."""
    with pytest.raises(NoChoice) as refused:
        choose(Store(store), nozzle_mm=0.4, filament="TPU only on the copy")
    text = str(refused.value)
    assert text.startswith("Orca's compatibility rule forbids the pairing")
    assert f"its compatible_printers are {M04}" in text


def test_without_a_pairing_a_tie_lists_the_candidates(store: Path) -> None:
    with pytest.raises(NoChoice) as tie:
        choose(Store(store), nozzle_mm=0.4, filament="PLA silk")
    text = str(tie.value)
    assert "no pairing" in text
    assert f'"{M04}"' in text and f'"{M04_TPU}"' in text
    assert f"machine '{M08}': a 0.8 mm nozzle" in text, "why each was left out"


def test_without_a_pairing_a_single_fit_is_chosen(store: Path) -> None:
    choice = choose(Store(store), nozzle_mm=0.8, filament="PLA silk")
    assert (choice.machine.name, choice.process.name, choice.notes) == (M08, P08, ())


def test_the_sponsor_can_name_the_machine_and_process(store: Path) -> None:
    choice = choose(
        Store(store), nozzle_mm=0.4, filament="PLA tuned", machine=M04_TPU, process=P_TPU
    )
    assert (choice.machine.name, choice.process.name) == (M04_TPU, P_TPU)


def test_a_named_machine_for_another_nozzle_is_refused(store: Path) -> None:
    with pytest.raises(NoChoice, match="a 0.8 mm nozzle"):
        choose(Store(store), nozzle_mm=0.4, filament="PLA tuned", machine=M08)


def test_a_process_with_no_printers_is_left_out_as_orcas_cli_refuses_it(store: Path) -> None:
    _write(store, "process", "open", compatible_printers=[])
    with pytest.raises(NoChoice, match=r"empty, which Orca's CLI refuses \(exit 239\)"):
        choose(Store(store), nozzle_mm=0.4, filament="PLA tuned", machine=M04, process="open")


def test_a_compatibility_condition_is_not_guessed(store: Path) -> None:
    _write(store, "process", "conditional", compatible_printers=["CC 0.4"],
           compatible_printers_condition="nozzle_diameter[0]==0.4")
    with pytest.raises(NoChoice, match="does not evaluate"):
        choose(Store(store), nozzle_mm=0.4, filament="PLA tuned", process="conditional")


# --------------------------------------------------------------------------
# The repository's own store
# --------------------------------------------------------------------------


def test_every_filament_in_the_store_has_a_pairing() -> None:
    assert unpaired(Store(STORE)) == []


def test_the_stores_forbidden_pairings_are_the_ones_waiting_on_the_sponsor() -> None:
    """Drafted 2026-10-25 and listed on the PR for the Sponsor's fix.

    Both INLAND TPU presets are marked for the "0.4 nozzle - Copy" machine
    only, while TPU goes with the TPU machines. Fixing the preset (or the
    pairing) in a PR removes them from this list.
    """
    problems = [p.split(":")[0] for p in check_pairings(Store(STORE))]
    assert problems == [
        "'INLAND TPU Basic @Elegoo Centauri Carbon 0.4 nozzle - Copy' on a 0.4 mm nozzle",
        "'INLAND TPU Basic @Elegoo Centauri Carbon 0.4 nozzle - Copy' on a 0.6 mm nozzle",
        "'INLAND TPU Basic correct' on a 0.4 mm nozzle",
        "'INLAND TPU Basic correct' on a 0.6 mm nozzle",
    ]
