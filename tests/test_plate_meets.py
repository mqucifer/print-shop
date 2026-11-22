"""A plate of several parts where it meets the print flow's other rules.

Where mqucifer/print-shop#109 crosses the live lock (mqucifer/print-shop#207), what a pause height
means (mqucifer/print-shop#203), a success with a flaw (mqucifer/print-shop#189), each gate shown
(mqucifer/print-shop#200) and a spool corrected while a job waits (mqucifer/print-shop#220).
Everything is made up and runs on fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.desk import Desk
from print_shop.desk.again import again
from print_shop.inventory import Inventory
from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.orchestrator.live import AlreadyLive, Step, only_live
from print_shop.plate import row_id
from print_shop.setup import lessons as print_lessons
from print_shop.setup.context import Fit, FitBand, Placed
from print_shop.setup.loop import _past_plate
from print_shop.setup.reading import (
    PAUSE_PAST_FIT,
    LayerTime,
    PauseReading,
    SeamReading,
    SliceReading,
    flags,
)
from tests.plates import profiles
from tests.test_plate_job import (
    JOB,
    Rig,
    _caps,
    _plate_records,
    _printed_with,
    _script,
    _with_a_lesson,
)
from tests.test_runtime import settings  # noqa: F401  (the fixture)
from tests.test_sliced import _material


def _spooled(tmp_path: Path) -> Rig:
    from tests.fakes import STORE_PROFILES

    material = _material(tmp_path / "ledger.sqlite", type="PLA", variety="Elegoo PLA")
    assert STORE_PROFILES.used is not None
    used = STORE_PROFILES.used.model_copy(update={"material": material})
    return Rig(tmp_path, use=profiles(tmp_path / "profiles").model_copy(update={"used": used}))


def test_a_colour_corrected_while_a_plate_waits_is_on_every_row(tmp_path: Path) -> None:
    rig = _spooled(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        with Inventory(rig.path) as inventory:
            spool = inventory.material().spool_id
            inventory.edit(spool, colour="grey")
        assert rig.press("approve", job).printed
    rows = rig.ledger.job_rows(job)
    assert len(rows) == 3
    for row in rows:
        assert row.material is not None and row.material.colour == "grey"
        assert row.profiles is not None and row.profiles.material == row.material


def test_a_tuning_changed_while_a_plate_waits_prints_none_of_it(tmp_path: Path) -> None:
    rig = _spooled(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        with Inventory(rig.path) as inventory:
            inventory.edit(inventory.material().spool_id, tuning="Elegoo PLA @ECC - Copy")
        result = rig.press("approve", job)
    assert not result.printed and "tuning was changed" in (result.error or "")
    assert rig.printer.calls == [] and rig.ledger.rows() == []


def test_a_press_for_another_job_doesnt_start_a_plate(tmp_path: Path) -> None:
    from print_shop.core.mqtt import command_topic
    from tests.test_job_e2e import CFG

    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.bridge.deliver(command_topic(CFG), {"decision": "approve", "job_id": "another"})
        result = rig.press("reject", job)
    assert not result.printed and rig.printer.calls == []


def test_a_second_start_is_told_a_plate_waits_till_its_last_part_is_answered(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    ledger.record_start_requested(*_plate_records())
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)

    def told() -> str:
        with pytest.raises(AlreadyLive) as refused, only_live(tmp_path, path, "x.stl"):
            pass
        return str(refused.value)

    with only_live(tmp_path, path, "cap-1.stl, cap-2.stl, cap-3.stl") as live:
        live.at(JOB, "cap-1.stl, cap-2.stl, cap-3.stl", Step.SENDING)
        for n in (1, 2):
            ledger.record_answer(row_id(JOB, n),
                                 Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
            assert "is waiting for an outcome" in told(), f"{3 - n} still to answer"
        ledger.record_answer(row_id(JOB, 3), Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
        assert "is ending" in told()


def test_a_second_start_is_told_every_part_of_the_first(tmp_path: Path) -> None:
    script = _script()
    caps = _caps(tmp_path)
    given = type("Args", (), {"serve": False, "again": None, "sliced": None, "stl": caps})()
    assert script._part(given) == "cap-tight.stl, cap-mid.stl, cap-loose.stl"


def test_the_approval_gate_shows_a_plate_in_place_of_one_turn() -> None:
    lines = _script()._gate_lines({
        "gate": "gate.before_print", "print_time_s": 600, "filament_cm3": 3.0,
        "orientation_deg": None, "orientation_rationale": "printed by layer",
        "plate_lines": ["2 parts on one plate, printed BY LAYER", "  part 1"],
    }, turned=True)
    assert lines[0].startswith("At the approval gate")
    assert "  plate       2 parts on one plate, printed BY LAYER" in lines
    assert not any("turned" in line for line in lines)


def test_one_parts_bridge_isnt_read_as_anothers_pocket_closing() -> None:
    """One part's fit, and the first thing over air on the plate just under the pause: on one
    part that's its pocket closed, on a plate it can be the part beside it."""
    layers = tuple(LayerTime(n=n, z=round(0.2 * n, 1), time_s=20) for n in range(1, 31))
    reading = SliceReading(
        layers=layers, features=(), seam=SeamReading(seams=0, stacked=0.0),
        pauses=(PauseReading(before_layer=15, z=3.0, after_s=280, after_z=2.8),),
        over_air=((14, 40.0),),
    )
    placed = Placed(fits=(FitBand(fit=Fit(name="over the post"), z_from=0.0, z_to=2.6),))
    alone = [f.id for f in flags(reading, None, {}, {}, placed)]
    assert PAUSE_PAST_FIT in alone, "the note a part by itself gets"
    plate = [f.id for f in flags(reading, None, {}, {}, placed, tops=(25, 30))]
    assert PAUSE_PAST_FIT not in plate


def test_a_plates_pause_is_copied_as_its_g_code_paused_from_any_parts_page(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, "print_sequence=by layer; pause_at=3 because a swap", "ok")
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        rig.press("approve", job)
    assert "pause_at=3, read from the G-code" in rig.shown[-1], "where it landed, at the review"
    desk = Desk(rig.path, rig.work, tmp_path / "caps")
    lines = set()
    for n in (1, 2, 3):
        found: Any = desk.job(row_id(job, n))
        runs = again(found.row, found.part_path, 1, found.part_found, found.plate_parts,
                     found.plate_rows)
        lines.add(runs.corrections)
        assert not any("isn't copied" in note for note in runs.notes)
    (line,) = lines
    assert "print_sequence=by layer" in line and "pause_at=3" in line


def test_a_success_with_a_flaw_reads_the_same_on_a_plate(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(*_plate_records())
    ledger.record_started(JOB)
    ledger.record_ended(JOB, 9)
    ledger.record_answer(f"{JOB}-2", Answer(result=Result.SUCCESS, symptom=Symptom.STRINGING))
    assert "cap-2.stl (part 2 of 3) success, with stringing" in _past_plate(ledger.job_rows(JOB))


def test_a_flawed_part_doesnt_spoil_a_plate_toward_promoting(tmp_path: Path) -> None:
    ledger, lesson = _with_a_lesson(tmp_path)
    for k in range(print_lessons.USED_TO_PROMOTE):
        job = f"0a1b2c3d-0000-4000-8000-00000000002{k}"
        _printed_with(ledger, lesson, job, Result.SUCCESS, Result.SUCCESS, flaw=Symptom.STRINGING)
    [(_, good)] = print_lessons.to_promote(ledger)
    assert all("(success, with stringing)" in said for said in good), "a usable part, by result"
