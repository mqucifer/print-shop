"""A temperature or fan learned on another variety is offered unselected, saying why.

mqucifer/print-shop#219. A filament's lesson holds for its type (mqucifer/print-shop#117), but two
varieties of a type start from different presets, so one lesson's value is a different change on
each. It's set on the variety it was learned on; on another of its type it's offered unticked at
Use these, saying the variety, and one `use` takes it; once taken there in a print that didn't
fail, it's ticked there. Every print, spool and part here is made up, in a test's own ledger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.ingest.analyzer import analyze
from print_shop.inventory import Inventory, Material
from print_shop.ledger import Ledger, PrintRecord, Row
from print_shop.setup import Proposer, lessons, variety
from print_shop.setup.intake import Intake
from print_shop.setup.offer import Item, Offering, Standing, offering
from print_shop.setup.proposal import Change, GivenLesson, Proposal, Source
from print_shop.setup.review import ReviewRequest, parse_answer, render
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import ProfileRef, ProfilesUsed
from tests.fakes import CI_PROFILES
from tests.test_use_these import PROCESS, box, features_of, this

BASIC = "Made-up PETG Basic"
FAST = "Made-up PETG Fast"
REVIEWER = "reviewer: test"
PETG = {
    "name": "Made-up PETG", "filament_type": ["PETG"], "textured_plate_temp": ["60"],
    "nozzle_temperature": ["240"], "nozzle_temperature_range_low": ["220"],
    "nozzle_temperature_range_high": ["260"],
}
PLA = {**PETG, "name": "Made-up PLA", "filament_type": ["PLA"]}


def spool(kind_of: str, kind: str = "PETG") -> Material:
    return Material(printer_id="made-up-printer", printer_model="made up", nozzle_mm=0.4,
                    spool_id=1, manufacturer="Made-up", type=kind, variety=kind_of,
                    colour="grey")


def plate(value: str = "75", **other: Any) -> Change:
    return Change(key="textured_plate_temp", preset="60", value=value, reason="it lifted at 60",
                  source=Source.SPONSOR, **other)


def brim() -> Change:
    return Change(key="brim_type", preset="no_brim", value="outer_only",
                  reason="the ends lifted", source=Source.SPONSOR)


def on(
    ledger: Ledger, job: str, stl: Path, kind_of: str | None, *corrections: Change,
    given: tuple[GivenLesson, ...] = (), result: str | None = "success", kind: str = "PETG",
) -> Row:
    """A made-up print on a spool of `kind_of`, or on none the inventory gave (None), as the
    review left it and as it was answered."""
    ledger.record_start_requested(PrintRecord(
        job_id=job, stl_name=stl.name, stl_sha256=analyze(str(stl)).stl_sha256,
        rotation_deg=(0.0, 0.0, 0.0), orientation_source="geometric",
        orientation_rationale="made up", orientation_accepted=True, gcode_name=f"{job}.gcode",
        print_time_s=600, filament_cm3=3.0, layer_count=100,
        slicer_config={"filament_settings_id": kind_of or "Made-up PETG", "filament_type": kind,
                       "layer_height": "0.2"},
        printer_machine="made up", printer_firmware="0",
        proposal=Proposal(lessons=given), corrections=corrections, features=features_of(stl),
        material=spool(kind_of, kind) if kind_of else None,
    ))
    ledger.record_started(job)
    if result is not None:
        ledger.record_ended(job, 0)
        with ledger.writing() as db:
            db.execute("UPDATE prints SET result=?, symptom='none', answered_at=ended_at"
                       " WHERE job_id=?", (result, job))
    row = ledger.get(job)
    assert row is not None
    return row


@pytest.fixture
def ledger(tmp_path: Path) -> Any:
    path = tmp_path / "ledger.sqlite"
    with Inventory(path) as stock:  # what a print's spool row points at
        stock.add_printer("made-up-printer", "made up")
        stock.fit(0.4, "made-up-printer")
        stock.add_spool(manufacturer="Made-up", type="PETG", variety=BASIC, colour="grey")
    with Ledger(path) as opened:
        yield opened


@pytest.fixture
def rail(tmp_path: Path) -> Path:
    """Long and narrow: what the plate and brim lessons are keyed to."""
    return box(tmp_path, "rail.stl", 150, 20, 10)


def kept(ledger: Ledger) -> None:
    for row in ledger.query("SELECT id FROM print_lessons WHERE status='candidate'"):
        lessons.accept(ledger, int(row["id"]), REVIEWER)


def learned_on_basic(ledger: Ledger, rail: Path) -> None:
    on(ledger, "basic-1", rail, BASIC, plate(), brim())
    kept(ledger)


def given(ledger: Ledger, rail: Path, kind_of: str, kind: str = "PETG") -> dict[str, GivenLesson]:
    return {g.key: g for g in lessons.for_part(ledger, features_of(rail), kind, kind_of)}


def offered(ledger: Ledger, rail: Path, kind_of: str) -> Offering:
    return offering(ledger, this(rail, process=PROCESS, filament=PETG,
                                 filament_names=(kind_of,), material=spool(kind_of)))


def numbered(found: Offering) -> int:
    """The number the lesson not set for this variety is used by."""
    return next(n for n, i in enumerate(found.items(), 1) if i.not_set_here)


def the_lesson(found: Offering, proposal: Proposal | None = None) -> tuple[Item, Standing]:
    [item] = found.lessons
    return item, found.standing(item, proposal or Proposal(), "0,0,0")


def request(found: Offering, proposal: Proposal | None = None) -> ReviewRequest:
    return ReviewRequest(
        presets=("machine", "process", "filament"), proposal=proposal or Proposal(),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=100, max_z_mm=20,
                          filament_mm=900, filament_cm3=2.9, filament_g=3.6, print_time_s=654),
        orientation=AppliedOrientation(), profiles=CI_PROFILES,
        process=PROCESS, filament=PETG, offering=found,
    )


def taken(ledger: Ledger, rail: Path, job: str, result: str | None = "success") -> Row:
    """A print on the fast variety that took the basic one's plate lesson from Use these."""
    found = offered(ledger, rail, FAST)
    number = numbered(found)
    review = parse_answer(f"use {number}", request(found))
    assert not isinstance(review, str), review
    [used] = review.corrections
    return on(ledger, job, rail, FAST, used, given=tuple(given(ledger, rail, FAST).values()),
              result=result)


# --------------------------------------------------------------------------
# Set on its variety, offered on another of its type, withheld on another type
# --------------------------------------------------------------------------


def test_a_bed_lesson_is_set_on_its_variety_offered_on_another_and_withheld_on_another_type(
    ledger: Ledger, rail: Path,
) -> None:
    learned_on_basic(ledger, rail)

    assert not given(ledger, rail, BASIC)["textured_plate_temp"].withheld, "set on its own"
    fast = given(ledger, rail, FAST)
    assert fast["textured_plate_temp"].withheld == f"learned on {BASIC}; this print is {FAST}"
    assert not fast["brim_type"].withheld, "a brim goes by shape, on any variety"
    pla = given(ledger, rail, "Made-up PLA", "PLA")
    assert pla["textured_plate_temp"].withheld == "learned on PETG; this print is PLA"

    item, standing = the_lesson(offered(ledger, rail, FAST))
    assert (item.key, item.value, item.used.lesson, item.used.by) == (
        "textured_plate_temp", "75", 1, Source.LESSON)
    assert standing.mark == " "
    assert standing.why == (f"this is {FAST}",)
    assert (item.source, item.notes) == (f"lesson 1, learned on {BASIC}", ())
    assert offered(ledger, rail, BASIC).lessons == (), "set on its own variety, not offered"
    pla_part = this(rail, process=PROCESS, filament=PLA, filament_names=("Made-up PLA",),
                    material=spool("Made-up PLA", "PLA"))
    assert offering(ledger, pla_part).lessons == (), "another type's is withheld, as before"


def test_the_proposal_sets_it_on_its_variety_and_the_review_says_why_not_on_another(
    ledger: Ledger, rail: Path, tmp_path: Path,
) -> None:
    learned_on_basic(ledger, rail)
    proposer = Proposer(
        ask=lambda system, user, schema: {"reasoning": "a rail", "changes": [],
                                          "design_notes": []},
        lessons=lambda features, kind, kind_of: lessons.for_part(ledger, features, kind, kind_of),
    )

    basic = proposer(Intake(), features_of(rail), profiles(tmp_path, BASIC))
    assert {(c.key, c.value, c.lesson) for c in basic.changes} >= {
        ("textured_plate_temp", "75", 1), ("brim_type", "outer_only", 2)}

    fast_profiles = profiles(tmp_path, FAST)
    assert variety(fast_profiles) == FAST
    fast = proposer(Intake(), features_of(rail), fast_profiles)
    assert fast.get("textured_plate_temp") is None, "another variety's isn't set"
    assert fast.get("brim_type") is not None
    shown = render(ReviewRequest(
        presets=("m", "p", FAST), proposal=fast,
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=1, max_z_mm=1,
                          filament_mm=1, filament_cm3=1, filament_g=1, print_time_s=60),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat"),
        profiles=fast_profiles, offering=offered(ledger, rail, FAST),
    ))
    text = " ".join(shown.split())
    assert f"not given: learned on {BASIC}; this print is {FAST}" in text
    assert "LESSONS NOT SET FOR THIS VARIETY" in text
    assert (f"your override on rail.stl, and lesson 1, learned on {BASIC} (in Lessons below); "
            f"not ticked: this is {FAST}") in text


def profiles(tmp_path: Path, kind_of: str) -> SlicerProfiles:
    path = tmp_path / f"{kind_of}.json"
    path.write_text(json.dumps(PETG))
    process = tmp_path / "process.json"
    process.write_text(json.dumps(
        {**json.loads(CI_PROFILES.process.read_text()), "brim_type": "no_brim"}))
    ref = ProfileRef(name=kind_of, file=f"{kind_of}.json", sha256="0" * 64)
    used = ProfilesUsed(machine=ref, process=ref, filament=ref, commit="0", store_dirty=False,
                        material=spool(kind_of))
    return CI_PROFILES.model_copy(update={"filament": path, "process": process, "used": used})


def test_with_no_variety_from_the_inventory_its_not_set(ledger: Ledger, rail: Path) -> None:
    learned_on_basic(ledger, rail)
    assert given(ledger, rail, "")["textured_plate_temp"].withheld == (
        f"learned on {BASIC}; this print's variety isn't known")


def test_a_lesson_widened_to_any_stays_widened(ledger: Ledger, rail: Path) -> None:
    learned_on_basic(ledger, rail)
    lessons.edit(ledger, 1, REVIEWER, material="any", why="the plate lifts on every PETG")
    assert not given(ledger, rail, FAST)["textured_plate_temp"].withheld
    assert not given(ledger, rail, "Made-up PLA", "PLA")["textured_plate_temp"].withheld
    assert offered(ledger, rail, FAST).lessons == ()


def test_a_past_prints_own_override_on_another_variety_says_the_variety(
    ledger: Ledger, rail: Path,
) -> None:
    learned_on_basic(ledger, rail)
    found = offered(ledger, rail, FAST)
    assert found.past is not None
    [mine] = [i for i in found.past.items if i.key == "textured_plate_temp"]
    standing = found.standing(mine, Proposal(), "0,0,0")
    assert standing.mark == " "
    assert f"learned on another variety, {BASIC}; this is {FAST}" in standing.why


def test_one_setting_at_one_value_has_one_number(ledger: Ledger, rail: Path) -> None:
    """The print it was learned on set the same, at the same value: it's said once, in the
    lessons' group, with both sources, so it can't be ticked twice or used twice."""
    learned_on_basic(ledger, rail)
    found = offered(ledger, rail, FAST)
    assert [i.key for i in found.items()] == ["brim_type", "textured_plate_temp"]
    [lesson] = [i for i in found.items() if i.not_set_here]
    assert lesson.source == f"your override on rail.stl, and lesson 1, learned on {BASIC}"
    other = on(ledger, "basic-2", rail, BASIC, plate("80"), brim())
    assert other.job_id == "basic-2"
    later = offered(ledger, rail, FAST)
    assert later.past is not None and later.past.job_id == "basic-2"
    assert [(i.key, i.value) for i in later.items() if i.key == "textured_plate_temp"] == [
        ("textured_plate_temp", "80"), ("textured_plate_temp", "75")], (
        "another value is another choice, and each says why")
    assert later.span() == 3 == len(later.items()), (
        "Use these holds the most any pick has, so picking moves no number after it")


# --------------------------------------------------------------------------
# Taken on the other variety, in a print that didn't fail, it's ticked there
# --------------------------------------------------------------------------


def test_one_use_takes_it_and_a_print_that_didnt_fail_ticks_it_there_from_then_on(
    ledger: Ledger, rail: Path,
) -> None:
    learned_on_basic(ledger, rail)
    row = taken(ledger, rail, "fast-1")
    [used] = [c for c in row.corrections or () if c.key == "textured_plate_temp"]
    assert used.used is not None and (used.used.by, used.used.lesson) == (Source.LESSON, 1)

    jobs = ledger.query("SELECT applied, why FROM job_lessons WHERE job_id='fast-1' AND lesson=1")
    assert [(j["applied"], j["why"]) for j in jobs] == [(1, lessons.TAKEN)]
    assert lessons.latest_scores(ledger, 1)["fast-1"]["score"] == "used"

    fast = given(ledger, rail, FAST)["textured_plate_temp"]
    assert fast.withheld.endswith("which you took it on before, in a print that didn't fail"), (
        "still not set by itself: a use takes it")
    item, standing = the_lesson(offered(ledger, rail, FAST))
    assert standing.selected
    day = row.start_requested_at
    assert any(n.startswith(f"taken on {FAST} on ") and n.endswith(
        "on rail.stl, which didn't fail") for n in item.notes), (item.notes, day)
    assert item.source == f"lesson 1, learned on {BASIC}"
    assert "used on 1 print that didn't fail" in item.notes
    found = offered(ledger, rail, FAST)
    assert found.past is not None and found.past.job_id == "fast-1", "the print it was taken on"
    assert [i.used.lesson for i in found.items() if i.used.by is Source.LESSON] == [1], (
        "said once, in its own group, though that print had it too")
    assert f"taken on {FAST} on " in " ".join(render(request(found)).split())
    assert offered(ledger, rail, "Made-up PETG Matte").lessons[0].doubts, (
        "ticked on the variety it was taken on, not on a third")


@pytest.mark.parametrize("result", ["failed", None])
def test_taken_in_a_print_that_failed_or_isnt_answered_it_stays_unticked(
    ledger: Ledger, rail: Path, result: str | None,
) -> None:
    learned_on_basic(ledger, rail)
    taken(ledger, rail, "fast-1", result)
    _, standing = the_lesson(offered(ledger, rail, FAST))
    assert not standing.selected
    assert standing.why == (f"this is {FAST}",)


def test_used_it_reads_as_used_and_cleared_it_says_so(ledger: Ledger, rail: Path) -> None:
    learned_on_basic(ledger, rail)
    found = offered(ledger, rail, FAST)
    number = numbered(found)
    review = parse_answer(f"use {number}", request(found))
    assert not isinstance(review, str)
    _, standing = the_lesson(found, Proposal(changes=review.corrections))
    assert standing.why == ("used",)

    cleared = found.clear([number])
    assert isinstance(cleared, Offering)
    _, standing = the_lesson(cleared)
    assert standing.why[0] == "you cleared it"


# --------------------------------------------------------------------------
# Left unticked, it's neither scored nor given
# --------------------------------------------------------------------------


def test_a_lesson_left_unticked_for_its_variety_isnt_given_or_scored(
    ledger: Ledger, rail: Path,
) -> None:
    learned_on_basic(ledger, rail)
    on(ledger, "fast-1", rail, FAST, given=tuple(given(ledger, rail, FAST).values()))
    assert ledger.query("SELECT * FROM job_lessons WHERE job_id='fast-1' AND lesson=1") == []
    assert "fast-1" not in lessons.latest_scores(ledger, 1)
    assert [j["lesson"] for j in ledger.query(
        "SELECT lesson FROM job_lessons WHERE job_id='fast-1'")] == [2], "the brim was given"


# --------------------------------------------------------------------------
# An older lesson's variety is read from its own print, or said to be unknown
# --------------------------------------------------------------------------


def test_an_older_lessons_variety_is_read_from_its_print_or_said_to_be_unknown(
    ledger: Ledger, rail: Path,
) -> None:
    on(ledger, "before-1", rail, None, plate(), brim())  # before the inventory gave a spool
    kept(ledger)
    unknown = "the variety it was learned on isn't known: its print doesn't say"
    for kind_of in (BASIC, FAST):
        assert given(ledger, rail, kind_of)["textured_plate_temp"].withheld == unknown
    item, standing = the_lesson(offered(ledger, rail, BASIC))
    assert standing.why == (unknown,)
    assert (item.source, item.notes) == (
        "lesson 1, learned on a variety its print doesn't say", ())


def test_with_no_past_print_to_offer_the_lessons_are_still_offered(
    ledger: Ledger, rail: Path,
) -> None:
    learned_on_basic(ledger, rail)
    found = Offering(lessons=offered(ledger, rail, FAST).lessons)
    text = " ".join(render(request(found)).split())
    assert "USE THESE: no past print has settings to offer, and lessons of this filament" in text
    assert "[ ] 1 textured_plate_temp 60 -> 75" in text
    assert "Answers: ok (approval gate) · use 1 ·" in text, "use is offered, by its number"
    review = parse_answer("use 1", request(found))
    assert not isinstance(review, str)
    assert [c.key for c in review.corrections] == ["textured_plate_temp"]
