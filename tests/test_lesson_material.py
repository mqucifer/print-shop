"""A print lesson that sets a temperature or a fan reaches only parts of the same filament.

mqucifer/print-shop#117 (the Sponsor, 2026-11-14). Shape still decides where a lesson applies; a
lesson for a setting the filament decides (`bounds.MATERIAL`) also holds only for the filament
type of the print it was learned on. At the review, one that matched by shape but not by
filament is listed as not given, with why. Every print here is a fixture, with the fake model.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.ledger import Ledger, PrintRecord
from print_shop.rounds.lessons import Refused
from print_shop.setup import Proposer, lessons
from print_shop.setup.bounds import MATERIAL, SETTINGS
from print_shop.setup.features import Feature
from print_shop.setup.intake import Intake
from print_shop.setup.proposal import Change, Source
from print_shop.setup.review import ReviewRequest, render
from print_shop.slicer.orca import SlicerProfiles, parse_config_block
from tests.fakes import CI_PROFILES, DATA

REVIEWER = "reviewer: operator"
CONFIG = parse_config_block(DATA / "recorded" / "cube.gcode")
RAIL = tuple(
    Feature(id=k, value=v, unit="", says=k)
    for k, v in {"height": 20.0, "footprint": 150.0, "length": 150.0, "footprint_aspect": 7.5,
                 "bed_contact": 3000.0, "slenderness": 1.0, "overhang": 0.0, "bridge": 0.0,
                 "section": 6.0}.items()
)


def _record(job_id: str, filament: str | None, *corrections: Change) -> PrintRecord:
    config = {k: v for k, v in CONFIG.items() if k != "filament_type"}
    if filament is not None:
        config["filament_type"] = filament
    return PrintRecord(
        job_id=job_id, stl_name=f"{job_id}.stl", stl_sha256="ab" * 32,
        rotation_deg=(0.0, 0.0, 0.0), orientation_source="geometric",
        orientation_rationale="flat", orientation_accepted=True, gcode_name=f"{job_id}.gcode",
        print_time_s=600, filament_cm3=3.0, layer_count=100, slicer_config=config,
        printer_machine="Centauri Carbon", printer_firmware="V1.1.46",
        corrections=corrections, features=RAIL,
    )


def _plate(value: str = "75") -> Change:
    return Change(key="textured_plate_temp", preset="60", value=value, reason="it lifted at 60",
                  source=Source.SPONSOR)


def _brim() -> Change:
    return Change(key="brim_type", preset="no_brim", value="outer_only",
                  reason="the ends lifted", source=Source.SPONSOR)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "ledger.sqlite") as opened:
        yield opened


def _kept(ledger: Ledger) -> None:
    for row in ledger.query("SELECT id FROM print_lessons WHERE status='candidate'"):
        lessons.accept(ledger, int(row["id"]), REVIEWER)


# Which settings the filament decides
# --------------------------------------------------------------------------


def test_the_filaments_temperatures_and_fans_are_material_and_the_shapes_settings_arent() -> None:
    assert {k for k, s in SETTINGS.items() if s.filament} <= MATERIAL
    for key in ("textured_plate_temp", "cool_plate_temp_initial_layer", "nozzle_temperature",
                "fan_max_speed", "additional_cooling_fan_speed",
                "during_print_exhaust_fan_speed", "hot_plate_temp"):
        assert key in MATERIAL, key
    for key in ("brim_type", "enable_support", "support_type", "wall_loops", "seam_position"):
        assert key not in MATERIAL, key


# Drawn with the filament of the print it was learned on
# --------------------------------------------------------------------------


def test_a_plate_lesson_holds_the_prints_filament_and_a_brim_lesson_none(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
    found = {r["setting"]: r["material"] for r in ledger.query("SELECT * FROM print_lessons")}
    assert found == {"textured_plate_temp": "PETG", "brim_type": None}
    assert "textured_plate_temp=75 in PETG" in lessons.listing(ledger)


def test_the_same_plate_correction_on_another_filament_is_a_lesson_of_its_own(
    ledger: Ledger,
) -> None:
    ledger.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
    ledger.record_start_requested(_record("pla-1", "PLA", _plate(), _brim()))
    rows = ledger.query("SELECT setting, material FROM print_lessons ORDER BY id")
    assert [(r["setting"], r["material"]) for r in rows] == [
        ("textured_plate_temp", "PETG"), ("brim_type", None), ("textured_plate_temp", "PLA")
    ], "the brim is the same lesson again; PLA's plate is its own"


# Given only to its own filament
# --------------------------------------------------------------------------


def test_a_petg_plate_lesson_reaches_petg_and_not_pla_and_a_brim_lesson_reaches_both(
    ledger: Ledger,
) -> None:
    ledger.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
    _kept(ledger)
    petg = {g.key: g for g in lessons.for_part(ledger, RAIL, "PETG")}
    assert (petg["textured_plate_temp"].withheld, petg["textured_plate_temp"].material) == (
        "", "PETG"
    )
    assert petg["brim_type"].withheld == ""
    pla = {g.key: g for g in lessons.for_part(ledger, RAIL, "pla")}
    assert pla["textured_plate_temp"].withheld == "learned on PETG; this print is PLA"
    assert pla["brim_type"].withheld == "", "shape alone decides a brim"
    unknown = {g.key: g for g in lessons.for_part(ledger, RAIL, "")}
    assert unknown["textured_plate_temp"].withheld.startswith("learned on PETG; this print is")


def test_widened_to_any_filament_it_reaches_every_one(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
    _kept(ledger)
    with pytest.raises(Refused, match="goes by the part's shape alone"):
        lessons.edit(ledger, 2, REVIEWER, material="any")
    lessons.edit(ledger, 1, REVIEWER, material="any", why="the plate lifts whatever the filament")
    pla = {g.key: g for g in lessons.for_part(ledger, RAIL, "PLA")}
    assert pla["textured_plate_temp"].withheld == ""
    [step] = ledger.query("SELECT was FROM print_lesson_steps WHERE lesson=1 AND status='edited'"
                          " AND was IS NOT NULL")
    assert json.loads(step["was"])["material"] == "PETG", "the edit keeps what it replaced"


# Through the proposal and the review, with the fake model
# --------------------------------------------------------------------------


def _model(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"reasoning": "a long bar", "changes": [], "design_notes": []}


def _profiles(tmp_path: Path, filament: str) -> SlicerProfiles:
    path = tmp_path / f"{filament}.json"
    path.write_text(json.dumps({
        "name": f"Generic {filament}", "filament_type": [filament],
        "textured_plate_temp": ["60"], "nozzle_temperature": ["240"],
        "nozzle_temperature_range_low": ["220"], "nozzle_temperature_range_high": ["260"],
    }))
    process = tmp_path / "process.json"
    process.write_text(json.dumps(
        {**json.loads(CI_PROFILES.process.read_text()), "brim_type": "no_brim"}
    ))
    return CI_PROFILES.model_copy(update={"filament": path, "process": process})


def test_the_review_says_a_lesson_for_another_filament_wasnt_given(
    ledger: Ledger, tmp_path: Path
) -> None:
    ledger.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
    _kept(ledger)
    proposer = Proposer(
        ask=_model, lessons=lambda features, filament: lessons.for_part(ledger, features, filament)
    )

    petg = proposer(Intake(), RAIL, _profiles(tmp_path, "PETG"))
    assert {(c.key, c.value, c.lesson) for c in petg.changes} >= {
        ("textured_plate_temp", "75", 1), ("brim_type", "outer_only", 2)
    }

    pla_profiles = _profiles(tmp_path, "PLA")
    pla = proposer(Intake(), RAIL, pla_profiles)
    assert pla.get("textured_plate_temp") is None, "PETG's plate isn't set on PLA"
    brim = pla.get("brim_type")
    assert brim is not None and brim.lesson == 2, "the brim goes by shape, on any filament"
    shown = render(ReviewRequest(
        presets=("m", "p", "Generic PLA"), proposal=pla,
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=1, max_z_mm=1,
                          filament_mm=1, filament_cm3=1, filament_g=1, print_time_s=60),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat"),
        profiles=pla_profiles,
    ))
    assert "(not given: learned on PETG; this print is PLA)" in shown

    # The ledger records it as never given, and never scores it.
    ledger.record_start_requested(_record("pla-2", "PLA").model_copy(update={"proposal": pla}))
    given = ledger.query("SELECT lesson FROM job_lessons WHERE job_id='pla-2'")
    assert [g["lesson"] for g in given] == [2]
    assert ledger.query("SELECT * FROM print_lesson_scores WHERE lesson=1") == []


# A ledger from before: the material is read from each print's own record
# --------------------------------------------------------------------------


def test_lessons_from_before_take_their_prints_filament_and_nothing_is_guessed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "old.sqlite"
    with Ledger(path) as made:
        made.record_start_requested(_record("petg-1", "PETG", _plate(), _brim()))
        made.record_start_requested(_record("unsaid-1", None, _plate("80")))
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE print_lessons DROP COLUMN material")  # as the table was
    db.commit()
    db.close()

    with Ledger(path) as ledger:
        found = {
            (r["setting"], r["value"]): r["material"]
            for r in ledger.query("SELECT * FROM print_lessons")
        }
        assert found == {
            ("textured_plate_temp", "75"): "PETG",
            ("brim_type", "outer_only"): None,
            ("textured_plate_temp", "80"): None,
        }
        steps = ledger.query("SELECT lesson, why FROM print_lesson_steps WHERE why LIKE"
                             " 'its material%'")
        assert [(s["lesson"], s["why"]) for s in steps] == [
            (1, "its material, PETG, read from the print it was learned on")
        ]
        _kept(ledger)
        unsaid = next(g for g in lessons.for_part(ledger, RAIL, "PETG") if g.value == "80")
        assert unsaid.withheld.startswith("its filament isn't known")
        assert "in a filament NOT KNOWN" in lessons.listing(ledger)
    with Ledger(path) as again:
        assert len(again.query("SELECT * FROM print_lesson_steps WHERE why LIKE"
                               " 'its material%'")) == 1, "a second open changes nothing"
