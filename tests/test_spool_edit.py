"""A spool's colour or tuning corrected, and what that does to prints (mqucifer/print-shop#220).

Everything runs on a made-up settings file, stock and ledger in a temporary folder, with fakes
for the printer, the broker and the slicer.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from print_shop.inventory import Inventory, Material
from print_shop.ledger import Ledger
from print_shop.runtime import describe, profiles_for
from print_shop.slicer.store import Store, choose
from tests.fakes import STORE
from tests.sliced_fixtures import plate_file
from tests.test_job_e2e import Rig
from tests.test_ledger import record
from tests.test_runtime import settings  # noqa: F401  (the fixture)
from tests.test_sliced import Shop, _material
from tests.test_stock import _stock

RAPID = "Elegoo Rapid PETG"
COOLER = "Elegoo RAPID PETG 240Degree,MaxFanSpeed=50,AuxPartCoolingFan=70"
HOTTER = "Elegoo RAPID PETG 270Degree,MaxFanSpeed=50,AuxPartCoolingFan=100"
PLA_TUNING = "Elegoo PLA @ECC - Copy"


@pytest.fixture
def env(settings: Path, capsys: pytest.CaptureFixture[str]) -> Path:  # noqa: F811
    """Stock: #1 a loaded RAPID PETG with a tuning, #2 an untuned PLA on hand."""
    for line in (["printer", "add", "cc1", "Elegoo Centauri Carbon"], ["fit", "0.4"],
                 ["add", RAPID, "black", "--grams", "750", "--tuning", COOLER],
                 ["add", "Elegoo PLA", "white"], ["load", "1"]):
        assert _stock(settings, *line) == 0
    capsys.readouterr()
    return settings


@pytest.fixture
def path(env: Path, tmp_path: Path) -> Path:
    return tmp_path / "ledger.sqlite"


def _table(path: Path, name: str) -> list[tuple[object, ...]]:
    db = sqlite3.connect(path)
    try:
        return db.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
    finally:
        db.close()


# --- the command ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "changed"),
    [
        (["--colour", "grey"], {"colour": "grey"}),
        (["--tuning", HOTTER], {"tuning": HOTTER}),
        (["--colour", "grey", "--tuning", HOTTER], {"colour": "grey", "tuning": HOTTER}),
    ],
)
def test_an_edit_changes_what_it_names_and_nothing_else(
    env: Path, path: Path, capsys: pytest.CaptureFixture[str],
    args: list[str], changed: dict[str, str],
) -> None:
    with Inventory(path) as inventory:
        before, other = inventory.spool(1), inventory.spool(2)

    assert _stock(env, "edit", "1", *args) == 0

    with Inventory(path) as inventory:
        assert inventory.spool(1) == before.model_copy(update=changed)
        assert inventory.spool(2) == other
        assert len(inventory.spools(with_empty=True)) == 2, "no new spool"
    out = capsys.readouterr().out
    assert out.startswith("spool #1  ") and "(loaded on cc1)" in out
    assert all(value in out for value in changed.values())


def test_a_spool_on_hand_or_empty_stays_so(env: Path, path: Path) -> None:
    assert _stock(env, "edit", "2", "--colour", "cream") == 0
    with Inventory(path) as inventory:
        assert inventory.spool(2).state == "on_hand"
    assert _stock(env, "empty", "2") == 0
    emptied = _table(path, "spools")[1]
    assert _stock(env, "edit", "2", "--colour", "ivory") == 0
    edited = _table(path, "spools")[1]
    assert edited == tuple("ivory" if v == "cream" else v for v in emptied)


@pytest.mark.parametrize("none", ["none", "None"])
def test_tuning_none_is_the_varietys_default(env: Path, path: Path, none: str) -> None:
    assert _stock(env, "edit", "2", "--tuning", PLA_TUNING) == 0
    assert _stock(env, "edit", "2", "--tuning", none) == 0
    with Inventory(path) as inventory:
        spool = inventory.spool(2)
    assert spool.tuning is None
    choice = choose(Store(STORE), nozzle_mm=0.4, filament=spool.filament)
    assert choice.filament.name == PLA_TUNING, "the variety's one tuning, as `add` leaves it"


@pytest.mark.parametrize(
    ("spool", "args", "said"),
    [
        ("1", ["--tuning", PLA_TUNING], [f"isn't one of your tunings of {RAPID}", COOLER, HOTTER]),
        ("1", ["--colour", "grey", "--tuning", "no such"], ["isn't one of", COOLER, HOTTER]),
        ("1", ["--tuning", "none"], ["more than once", f'--tuning "{COOLER}"', HOTTER]),
        ("2", ["--tuning", HOTTER], ["isn't one of your tunings of Elegoo PLA", PLA_TUNING]),
        ("1", [], ["say what to correct"]),
        ("9", ["--colour", "grey"], ["there's no spool #9"]),
    ],
)
def test_a_wrong_edit_is_refused_and_changes_nothing(
    env: Path, path: Path, capsys: pytest.CaptureFixture[str],
    spool: str, args: list[str], said: list[str],
) -> None:
    before = _table(path, "spools")
    assert _stock(env, "edit", spool, *args) == 2
    err = capsys.readouterr().err
    assert all(words in err for words in said), err
    assert _table(path, "spools") == before


def test_a_filament_without_a_profile_takes_a_colour_and_no_tuning(
    env: Path, path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _stock(env, "add", "Overture TPU", "red", "--type", "tpu",
                  "--manufacturer", "Overture") == 0
    assert _stock(env, "edit", "3", "--tuning", HOTTER) == 2
    assert "has no profile in profiles/, so it has no tunings" in capsys.readouterr().err
    assert _stock(env, "edit", "3", "--colour", "orange", "--tuning", "none") == 0
    with Inventory(path) as inventory:
        assert (inventory.spool(3).colour, inventory.spool(3).tuning) == ("orange", None)


def test_a_past_prints_record_is_unchanged_by_an_edit(
    env: Path, path: Path, tmp_path: Path
) -> None:
    with Inventory(path) as inventory:
        material = inventory.material()
    choice = choose(Store(STORE), nozzle_mm=0.4, filament=COOLER)
    used = profiles_for(choice, STORE, tmp_path / "composed", material).used
    with Ledger(path) as ledger:
        ledger.record_start_requested(record().model_copy(update={"profiles": used}))
        row = ledger.get("job-1")
    assert row is not None
    kept = {name: _table(path, name) for name in ("prints", "print_material")}

    assert _stock(env, "edit", "1", "--colour", "grey", "--tuning", HOTTER) == 0

    assert {name: _table(path, name) for name in kept} == kept
    with Ledger(path) as ledger:
        after = ledger.get("job-1")
    assert after == row
    assert f"spool #1, {RAPID}, black" in describe(after)
    with Inventory(path) as inventory:
        now = inventory.for_print(None, None)
    assert (now.colour, now.tuning) == ("grey", HOTTER), "the next print takes the correction"


# --- a job prepared before the edit --------------------------------------------------------------


@pytest.fixture
def prepared(env: Path, path: Path, tmp_path: Path) -> Iterator[tuple[Rig, str, Material]]:
    """A part sliced with the loaded spool's tuning, waiting at the approval gate."""
    with Inventory(path) as inventory:
        material = inventory.material()
    choice = choose(Store(STORE), nozzle_mm=0.4, filament=material.tuning or material.variety)
    (tmp_path / "work").mkdir()
    rig = Rig(tmp_path, profiles_for(choice, STORE, tmp_path / "composed", material))
    with rig.daemon:
        job_id = rig.daemon.submit(rig.stl)
        rig.daemon.wait_for_gate(job_id, timeout_s=30)
        yield rig, job_id, material
    rig.ledger.close()


def test_a_job_prepared_with_the_old_tuning_says_so_at_start_and_prints_nothing(
    env: Path, prepared: tuple[Rig, str, Material]
) -> None:
    rig, job_id, _ = prepared
    assert _stock(env, "edit", "1", "--tuning", HOTTER) == 0

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert result.printed is False and result.error is not None
    assert f'spool #1\'s tuning was changed from "{COOLER}" to "{HOTTER}"' in result.error
    assert "Run the job again" in result.error
    assert rig.printer.calls == [], "refused before the printer was reached"
    assert rig.ledger.rows() == []


def test_a_tuning_put_back_before_start_still_prints(
    env: Path, prepared: tuple[Rig, str, Material]
) -> None:
    rig, job_id, material = prepared
    assert _stock(env, "edit", "1", "--tuning", HOTTER) == 0
    assert _stock(env, "edit", "1", "--tuning", COOLER) == 0

    rig.press("approve")
    assert rig.daemon.wait_for_finish(job_id, timeout_s=30).printed is True
    (row,) = rig.ledger.rows()
    assert row.material == material


def test_a_colour_corrected_before_start_is_the_colour_recorded(
    env: Path, prepared: tuple[Rig, str, Material]
) -> None:
    rig, job_id, material = prepared
    assert _stock(env, "edit", "1", "--colour", "grey") == 0

    rig.press("approve")
    result = rig.daemon.wait_for_finish(job_id, timeout_s=30)

    assert (result.printed, result.error) == (True, None)
    (row,) = rig.ledger.rows()
    grey = material.model_copy(update={"colour": "grey"})
    assert row.material == grey
    assert row.profiles is not None and row.profiles.material == grey, "both copies agree"


@pytest.mark.parametrize(
    ("args", "printed"), [(["--tuning", PLA_TUNING], False), (["--colour", "grey"], True)]
)
def test_the_terminal_says_so_for_a_hand_sliced_job_too(
    settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    capsys: pytest.CaptureFixture[str], args: list[str], printed: bool,
) -> None:
    shop = Shop(tmp_path, settings, monkeypatch)
    _material(shop.ledger_path, type="PLA", variety="Elegoo PLA")
    monkeypatch.setattr(shop.script, "_watch", lambda run, job_id: 0)  # not until its outcome
    press = shop.bridge.deliver

    def edit_then_press(*given: object, **kwargs: object) -> None:
        assert _stock(settings, "edit", "1", *args) == 0
        press(*given, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shop.bridge, "deliver", edit_then_press)
    # The file is PETG's, so it's sent with --loaded-ok; what matters here is the spool's edit.
    code = shop.run("--sliced", str(plate_file(tmp_path)), "--loaded-ok", press="approve")

    out = capsys.readouterr().out
    with Ledger(shop.ledger_path) as ledger:
        rows = ledger.rows()
    if printed:
        assert code == 0
        (row,) = rows
        assert row.material is not None and row.material.colour == "grey"
    else:
        assert code == 1 and rows == [] and shop.printer.calls == []
        assert "The job ended without printing: spool #1's tuning was changed from the " \
               f"variety's default to \"{PLA_TUNING}\"" in out
        (log,) = shop.jobs.glob("*/job.log")
        assert "tuning was changed" in log.read_text(), "kept in the job's log"
