"""A plate of several parts where it meets the print flow's other rules.

Where mqucifer/print-shop#109 crosses the live lock (mqucifer/print-shop#207), what a pause height
means (mqucifer/print-shop#203), a success with a flaw (mqucifer/print-shop#189), each gate shown
(mqucifer/print-shop#200), a spool corrected while a job waits (mqucifer/print-shop#220) and
what repeats a lesson (mqucifer/print-shop#212).
Everything is made up and runs on fakes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import command_topic, gate_topic
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
from tests.reviewing import flat
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_plate_job import (
    JOB,
    Rig,
    _caps,
    _plate_records,
    _printed_with,
    _script,
    _until,
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
        # An Approve with it changed starts nothing and holds the gate (mqucifer/print-shop#218).
        rig.bridge.press(CFG, "approve")
        _until(lambda: bool(rig.bridge.retained_json(gate_topic(CFG)).get("held")), "the hold")
        held = str(rig.bridge.retained_json(gate_topic(CFG))["held"])
        result = rig.press("reject", job)
    assert "tuning was changed" in held, held
    # Start read the printer (mqucifer/print-shop#209), and nothing was sent.
    assert not result.printed and rig.printer.verbs == ["snapshot"] and rig.ledger.rows() == []


def test_a_press_for_another_job_doesnt_start_a_plate(tmp_path: Path) -> None:
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
    given = type("Args", (), {"serve": False, "again": None, "sliced": None, "stl": caps,
                            "resume": None})()
    assert script._part(given) == "cap-tight.stl, cap-mid.stl, cap-loose.stl"


def test_the_approval_gate_shows_a_plate_in_place_of_one_turn() -> None:
    lines = _script()._gate_lines({
        "gate": "gate.before_print", "print_time_s": 600, "filament_cm3": 3.0,
        "orientation_deg": None, "orientation_rationale": "printed by layer",
        "plate_lines": ["2 parts on one plate, printed BY LAYER", "  part 1"],
    }, turned=True)
    assert lines[0].startswith("Ready to print.")  # mqucifer/print-shop#209
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
    rig = Rig(tmp_path, "print_sequence=by layer; colour_change=3 because a swap", "ok")
    caps = _caps(tmp_path)
    with rig.daemon:
        job = rig.gated(caps)
        rig.press("approve", job)
    assert re.search(r"PAUSE pause_at=3: pauses .*, as asked \(read from the G-code\)",
                     flat(rig.shown[-1])), "where it landed, at the review"
    desk = Desk(rig.path, rig.work, tmp_path / "caps")
    lines = set()
    for n in (1, 2, 3):
        found: Any = desk.job(row_id(job, n))
        runs = again(found.row, found.part_path, 1, found.part_found, found.plate_parts,
                     found.plate_rows)
        lines.add(runs.corrections)
        assert not any("isn't copied" in note for note in runs.notes)
    (line,) = lines
    assert "print_sequence=by layer" in line and "colour_change=3" in line


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


def test_a_plates_correction_typed_again_is_evidence_and_drawing_again_adds_nothing(
    tmp_path: Path,
) -> None:
    """With mqucifer/print-shop#212's rule: a repeat where the lesson reaches the part it's keyed
    by, the same plate again included."""
    from tests.plates import box

    pin = box(tmp_path / "pin.stl", 8, 8, 30)
    lid = box(tmp_path / "lid.stl", 80, 80, 4)
    rig = Rig(tmp_path, "wall_loops=3 because it's enough", "ok",
              "wall_loops=3 because it's enough", "ok")
    with rig.daemon:
        first = rig.gated([pin, lid])
        rig.press("approve", first)
        rig.printer._started = None  # the bed is clear again
        second = rig.gated([pin, lid])
        rig.press("approve", second)
    assert len(rig.ledger.query("SELECT id FROM print_lessons")) == 1
    evidence = rig.ledger.query("SELECT job_id FROM print_lesson_evidence")
    assert sorted(e["job_id"] for e in evidence) == sorted([first, second]), "once for each plate"

    for row in rig.ledger.rows():
        print_lessons.after_print(rig.ledger, row.job_id)
    assert len(rig.ledger.query("SELECT id FROM print_lessons")) == 1
    assert rig.ledger.query("SELECT job_id FROM print_lesson_evidence") == evidence


def test_a_plates_correction_isnt_absorbed_by_a_lesson_for_another_shape(tmp_path: Path) -> None:
    from print_shop.setup.features import measure
    from print_shop.setup.proposal import Change, Source
    from print_shop.setup.traits import traits
    from tests.plates import box

    lid = measure(box(tmp_path / "lid.stl", 80, 80, 4), 6400)
    pin = measure(box(tmp_path / "pin.stl", 8, 8, 30), 64)
    fixed = Change(key="wall_loops", preset="2", value="3", reason="enough",
                   source=Source.SPONSOR)
    ledger = Ledger(tmp_path / "l.sqlite")
    ledger.record_start_requested(
        record().model_copy(update={"features": lid, "corrections": (fixed,)}))
    one, two = _plate_records(count=2, corrections=(fixed,))
    ledger.record_start_requested(one.model_copy(update={"features": pin}),
                                  two.model_copy(update={"features": lid}))
    old, new = ledger.query("SELECT job_id, traits FROM print_lessons ORDER BY id")
    assert (old["job_id"], new["job_id"]) == ("job-1", JOB)
    assert set(traits(pin)) == set(json.loads(new["traits"])), (
        "keyed to the part with the most traits, which the lid's lesson doesn't reach"
    )
    assert "linked to lesson 1" in print_lessons.listing(ledger)


def test_a_plates_swap_is_marked_once_and_its_first_card_takes_the_pick(tmp_path: Path) -> None:
    """mqucifer/print-shop#218 on a plate: one print, so one mark of what's loaded not being
    known, by its first row. Each row has the spools, and what it ended with is every row's."""
    from print_shop.core.mqtt import outcome_spool_topic
    from print_shop.orchestrator import spools as said_spools

    rig = _spooled(tmp_path)
    with Inventory(rig.path) as stock:
        white = stock.material().spool_id
        black = stock.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA",
                                colour="black").id
    rig.typed = [f"print_sequence=by layer; colour_change=3 from #{white} to #{black} because "
                 "white to black", "ok"]
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        assert rig.press("approve", job).printed
        _until(lambda: rig.card().get("job_id") == job, "the first card")
        marks = rig.ledger.query("SELECT job_id FROM swap_not_known")
        swaps = rig.ledger.query("SELECT job_id FROM print_swaps")
        assert marks == swaps == [{"job_id": job}], "once, by the first row"
        rows = rig.ledger.job_rows(job)
        assert [[s.material.spool_id for s in r.spools] for r in rows] == [[white, black]] * 3
        picks = said_spools.choices(rig.ledger, rows[0])
        assert picks and not said_spools.choices(rig.ledger, rows[1]), "the first card's alone"
        on_black = next(words for words, pick in picks.items() if pick.spool_id == black)
        rig.bridge.deliver(outcome_spool_topic(CFG), on_black)
        rig.answer("success")
        _until(lambda: rig.card().get("job_id") == row_id(job, 2), "the second card")
        said = rig.card()["spools"]
    assert "not this card's" not in said, said
    assert rig.ledger.query("SELECT job_id FROM swap_not_known") == []
    assert [r.ended_with and r.ended_with.spool_id for r in rig.ledger.job_rows(job)] == [black] * 3


def test_a_plates_later_card_points_to_its_first_while_its_not_said(tmp_path: Path) -> None:
    from print_shop.orchestrator import spools as said_spools

    rig = _spooled(tmp_path)
    with Inventory(rig.path) as stock:
        white = stock.material().spool_id
        black = stock.add_spool(manufacturer="Elegoo", type="PLA", variety="Elegoo PLA",
                                colour="black").id
    rig.typed = [f"print_sequence=by layer; colour_change=3 from #{white} to #{black} because "
                 "white to black", "ok"]
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        assert rig.press("approve", job).printed
        _until(lambda: rig.card().get("job_id") == job, "the first card")
        second = rig.ledger.job_rows(job)[1]
        said = said_spools.said(rig.ledger, second)
    assert "on the card for cap-tight.stl, this plate's first" in said, said
    assert "another print" not in said
