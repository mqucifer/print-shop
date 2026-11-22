"""A job starts from the most similar past print's overrides, pre-selected, each saying why.

mqucifer/print-shop#211. Every part, profile and print here is made up, and each ledger is a
test's own, in its temp folder. Without a printer, Orca or a model: the slices are the recorded
cube's, and the models are replies in the test.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import trimesh

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.desk import again
from print_shop.desk.page import _change_rows
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import candidates
from print_shop.ledger import Ledger, PrintRecord, Row
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer, lessons, offer
from print_shop.setup.context import PartContext
from print_shop.setup.doubts import Doubting
from print_shop.setup.features import Feature, measure
from print_shop.setup.intake import Answer, Intake, Option, Question, replay_asker
from print_shop.setup.offer import Item, Offering, Standing, ThisPart, offering
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    Change,
    GivenLesson,
    Proposal,
    Source,
    Used,
    pose_value,
)
from print_shop.setup.reading import LayerTime, PauseReading, SeamReading, SliceReading
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer, parse_answer, render
from print_shop.setup.traits import traits
from print_shop.slicer.orca import SlicerProfiles
from tests.fakes import CI_PROFILES, STORE_PROFILES, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_colour_change import with_pause
from tests.test_job_e2e import CFG
from tests.test_review import ANSWERS, Sponsor, _model, _questions

PROCESS = {
    "name": "made-up process", "wall_loops": "2", "enable_support": "0", "brim_type": "no_brim",
    "bridge_speed": "50", "seam_position": "aligned", "layer_height": "0.2",
    "initial_layer_print_height": "0.2", "top_shell_layers": "4",
}
FILAMENT = {
    "name": "Made-up PLA", "filament_type": ["PLA"], "nozzle_temperature": ["210"],
    "fan_max_speed": ["100"],
}
PLATE = (256.0, 256.0, 256.0)
LAID = (0.0, 0.0, 0.0)


def box(tmp_path: Path, name: str, *extents: float) -> Path:
    """A block seated on the plate."""
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation((0, 0, extents[2] / 2))
    path = tmp_path / name
    mesh.export(path)
    return path


def stepped(tmp_path: Path, name: str, width: float = 40.0) -> Path:
    """A block with a step whose top is at 7.4 mm: a feature to pause at."""
    base = trimesh.creation.box(extents=(width, 30, 7.4))
    base.apply_translation((0, 0, 3.7))
    top = trimesh.creation.box(extents=(width / 2, 15, 7.0))
    top.apply_translation((0, 0, 10.9))
    path = tmp_path / name
    trimesh.boolean.union([base, top]).export(path)
    return path


def pocketed(tmp_path: Path, name: str, tops_out: float) -> Path:
    """A block with a pocket closed over, whose top is at `tops_out`."""
    block = trimesh.creation.box(extents=(40, 30, 14))
    block.apply_translation((0, 0, 7))
    pocket = trimesh.creation.box(extents=(20, 15, tops_out - 4))
    pocket.apply_translation((0, 0, (tops_out + 4) / 2))
    path = tmp_path / name
    trimesh.boolean.difference([block, pocket]).export(path)
    return path


def features_of(stl: Path) -> tuple[Feature, ...]:
    return measure(stl, analyze(str(stl)).bed_contact_area_mm2)


def this(stl: Path, **other: Any) -> ThisPart:
    report = analyze(str(stl))
    return ThisPart(**{
        "stl": stl, "stl_name": stl.name, "sha256": report.stl_sha256,
        "features": features_of(stl), "rotation_deg": LAID, "height_mm": report.bbox_mm[2],
        "machine": {"min_layer_height": "0.08", "max_layer_height": "0.28"},
        "process": PROCESS, "filament": FILAMENT, "filament_names": ("Made-up PLA",),
        "candidates": tuple(candidates(stl)), "build_volume_mm": PLATE, **other,
    })


def yours(key: str, value: str, reason: str = "it gets handled", **other: Any) -> Change:
    return Change(key=key, preset="", value=value, reason=reason, source=Source.SPONSOR, **other)


def read_pausing(z: float, older: bool = False) -> SliceReading:
    """A slice in 0.2 mm layers that pauses after the one topping out at `z`. An `older` row's
    reading doesn't say the layer it paused after (mqucifer/print-shop#228)."""
    layers = tuple(LayerTime(n=n, z=round(n * 0.2, 2), time_s=10.0) for n in range(1, 151))
    before = round(z / 0.2) + 1
    pause = PauseReading(before_layer=before, z=layers[before - 1].z, after_s=60.0,
                         after_z=None if older else z)
    return SliceReading(layers=layers, features=(), seam=SeamReading(seams=0, stacked=0.0),
                        pauses=(pause,))


def printed(
    ledger: Ledger, job: str, stl: Path, corrections: tuple[Change, ...] = (), *,
    changes: tuple[Change, ...] = (), kept: tuple[Change, ...] = (),
    given: tuple[GivenLesson, ...] = (), result: str | None = "success", symptom: str = "none",
    note: str = "", filament: str = "Made-up PLA", kind: str = "PLA",
    rotation: tuple[float, float, float] = LAID, source: str = "geometric",
    config: dict[str, str] | None = None, context_from: str = "",
    intake: Intake | None = None, features: tuple[Feature, ...] | None = None,
    older: bool = False, unread: bool = False,
) -> Row:
    """A made-up print in the ledger, as the review left it and as it was answered."""
    last = {c.key: c for c in corrections if c.key != ORIENTATION}
    made = Proposal(changes=(*(c for c in changes if c.key not in last), *last.values()),
                    kept=kept, lessons=given, context_from=context_from)
    paused = last.get(PAUSE)
    reading = None
    if paused is not None and paused.value != "none" and not unread:
        # An older row's height was the first layer after the pause.
        z = round(float(paused.value) - (0.2 if older else 0.0), 2)
        reading = read_pausing(z, older)
    ledger.record_start_requested(PrintRecord(
        job_id=job, stl_name=stl.name, stl_sha256=analyze(str(stl)).stl_sha256,
        rotation_deg=rotation, orientation_source=source, orientation_rationale="made up",
        orientation_accepted=True, gcode_name=f"{job}.gcode", print_time_s=600, filament_cm3=3.0,
        layer_count=100,
        slicer_config={"filament_settings_id": filament, "filament_type": kind,
                       "layer_height": "0.2", **(config or {})},
        printer_machine="made up", printer_firmware="0", intake=intake, proposal=made,
        corrections=corrections, reading=reading,
        features=features_of(stl) if features is None else features,
    ))
    ledger.record_started(job)
    if result is not None:
        ledger.record_ended(job, 0)
        with ledger.writing() as db:
            # Straight in: a success with a symptom is mqucifer/print-shop#189's to answer.
            db.execute(
                "UPDATE prints SET result=?, symptom=?, note=?, answered_at=ended_at"
                " WHERE job_id=?", (result, symptom, note, job),
            )
    row = ledger.get(job)
    assert row is not None
    return row


def standings(
    found: Offering, proposal: Proposal | None = None, pose: str = "0,0,0"
) -> dict[str, tuple[Item, Standing]]:
    assert found.past is not None
    return {i.key: (i, found.standing(i, proposal or Proposal(), pose)) for i in found.past.items}


def why_not(found: Offering, key: str, proposal: Proposal | None = None) -> str:
    _, standing = standings(found, proposal)[key]
    assert not standing.selected
    return "; ".join(standing.why)


def selected(found: Offering, key: str, proposal: Proposal | None = None) -> bool:
    return standings(found, proposal)[key][1].selected


def request_for(found: Offering, proposal: Proposal | None = None) -> ReviewRequest:
    return ReviewRequest(
        presets=("machine", "process", "filament"), proposal=proposal or Proposal(),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=100, max_z_mm=20,
                          filament_mm=900, filament_cm3=2.9, filament_g=3.6, print_time_s=654),
        orientation=AppliedOrientation(), profiles=STORE_PROFILES,
        process=PROCESS, filament=FILAMENT, offering=found,
    )


@pytest.fixture
def ledger(tmp_path: Path) -> Any:
    with Ledger(tmp_path / "ledger.sqlite") as opened:
        yield opened


# --------------------------------------------------------------------------
# Which past print
# --------------------------------------------------------------------------


def test_the_same_bytes_come_first(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    nearly = box(tmp_path, "plinth-b.stl", 60.2, 40, 12)
    printed(ledger, "same", here, (yours("wall_loops", "4"),), result="failed", symptom="warping")
    printed(ledger, "near", nearly, (yours("wall_loops", "5"),))
    found = offering(ledger, this(here))
    assert [p.job_id for p in found.options] == ["same", "near"]
    assert found.options[0].why == "the same file, byte for byte"
    assert found.options[0].came_out == "failed, warping"


def test_then_the_closest_by_its_measures_and_another_can_be_picked(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "far", box(tmp_path, "tower.stl", 15, 15, 90), (yours("wall_loops", "5"),))
    printed(ledger, "near", box(tmp_path, "block.stl", 62, 41, 12), (yours("wall_loops", "4"),))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))
    assert [p.job_id for p in found.options] == ["near", "far"]
    near = found.options[0]
    assert "(shape " in near.why
    assert ("length", 62.0, 60.0, "") in near.measures
    assert near.came_out == "success"
    other = found.pick(2)
    assert isinstance(other, Offering) and other.past is not None
    assert other.past.job_id == "far"
    assert "the part is unlike it" in why_not(other, "wall_loops")


def test_context_and_source_count_in_closeness(tmp_path: Path, ledger: Ledger) -> None:
    run = tmp_path / "run"
    printed(ledger, "run", box(tmp_path, "shelf.stl", 63, 41, 12), (yours("wall_loops", "4"),),
            context_from=str(run))
    printed(ledger, "other", box(tmp_path, "tile.stl", 62, 41, 12), (yours("wall_loops", "5"),))
    here = this(box(tmp_path, "plinth.stl", 60, 40, 12),
                context=PartContext(source="a made-up run", path=str(run)))
    assert [p.job_id for p in offering(ledger, here).options] == ["run", "other"]


def test_names_alike_arent_a_common_source(tmp_path: Path, ledger: Ledger) -> None:
    printed(ledger, "tower", box(tmp_path, "part-1.stl", 15, 15, 90), (yours("wall_loops", "5"),))
    found = offering(ledger, this(box(tmp_path, "part-2.stl", 60, 40, 12)))
    assert "the part is unlike it" in why_not(found, "wall_loops")


def test_of_the_same_file_one_that_didnt_fail_comes_first(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "good", here, (yours("wall_loops", "4"),))
    printed(ledger, "bad", here, (yours("wall_loops", "6"),), result="failed", symptom="warping")
    found = offering(ledger, this(here))
    assert [p.job_id for p in found.options] == ["good", "bad"]


def test_a_print_with_nothing_decided_isnt_offered(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "plain", here, changes=(Change(
        key="wall_loops", preset="2", value="3", reason="a model's", source=Source.MODEL),))
    found = offering(ledger, this(here))
    assert found.options == ()
    assert "USE THESE: no past print has settings to offer." in render(request_for(found))


# --------------------------------------------------------------------------
# What's offered, and why
# --------------------------------------------------------------------------


def test_all_that_decided_a_print_is_offered_each_with_whose_it_was_and_why(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    asked = Intake(
        questions=(
            Question(feature="purpose", text="What holds it?", options=(
                Option(label="screws", settings={"top_shell_layers": "9"}, impact="firmer"),
                Option(label="glue", settings={}, impact="none"),
            )),
            Question(feature="purpose", text="Where is it used?", options=(
                Option(label="indoors", settings={}, impact="none"),)),
        ),
        answers=(Answer(choice=0), Answer(choice=0)),
    )
    printed(
        ledger, "first", here, (yours("wall_loops", "4", "it gets handled"),),
        changes=(
            Change(key="top_shell_layers", preset="4", value="9", source=Source.ANSWER,
                   reason="you chose 'screws': firmer"),
            Change(key="seam_position", preset="aligned", value="back", source=Source.READING,
                   reason="the walls are seen", reading="a line up the wall"),
            Change(key="bridge_speed", preset="50", value="25", source=Source.MODEL,
                   reason="a model's"),
        ),
        kept=(Change(key="enable_support", preset="0", value="0", source=Source.ANSWER,
                     reason="you chose 'bridge it'"),),
        intake=asked,
    )
    found = offering(ledger, this(here))
    items = standings(found)
    assert list(items) == ["wall_loops", "seam_position", "enable_support", "top_shell_layers"]
    assert items["top_shell_layers"][0].reason == "you chose 'screws': firmer"
    assert items["enable_support"][1].mark == "=", "an answer that's the preset's is so already"
    assert all(s.selected for key, (_, s) in items.items() if key != "enable_support")
    text = " ".join(render(request_for(found)).split())
    assert "USE THESE: from plinth.stl," in text and "None is applied until you use it." in text
    assert "YOUR OVERRIDES now -> use [x] 1 wall_loops 2 -> 4 it gets handled" in text
    assert "seam_position aligned -> back the walls are seen; you took the slice's" in text
    assert "YOUR ANSWERS THEN (not asked for this job)" in text
    assert "you chose 'screws': firmer" in text
    assert "you answered then: Where is it used? -> indoors" in text
    assert "bridge_speed" not in items, "a model's choice isn't the person's"
    assert "use what decided that print (use, or use 1 3)," in text


def test_a_near_file_just_across_a_traits_threshold_still_gets_the_lessons_setting(
    tmp_path: Path, ledger: Ledger
) -> None:
    past = box(tmp_path, "slab.stl", 101, 50, 12)  # 5050 mm2 on the plate
    here = box(tmp_path, "slab-b.stl", 99, 50, 12)  # 4950
    printed(ledger, "seed", past)
    lesson = lessons.write(ledger, "seed", "reviewer: test", "a big face lifts",
                           ["large_bed_contact"], "brim_type=outer_only")
    lessons.accept(ledger, lesson, "reviewer: test")
    [given] = lessons.for_part(ledger, features_of(past), "PLA")
    printed(ledger, "first", past, changes=(Change(
        key="brim_type", preset="no_brim", value="outer_only", source=Source.LESSON,
        lesson=lesson, reason="learned on slab.stl: a big face lifts"),),
        given=(given.model_copy(update={"applied": True}),))
    assert lessons.for_part(ledger, features_of(here), "PLA") == (), "by traits alone it's lost"

    found = offering(ledger, this(here))
    item, standing = standings(found)["brim_type"]
    assert standing.selected
    assert item.notes == (
        "doesn't reach this part by itself: it isn't large_bed_contact (bed_contact 4950 here)",
    )
    assert item.fits == "about the same area on the plate"
    assert found.past is not None
    across = "not large_bed_contact now: 5000 mm2 or more touches the plate"
    assert ("bed_contact", 5050.0, 4950.0, across) in found.past.measures
    text = " ".join(render(request_for(found)).split())
    assert "area on the plate 5050 4950 mm2 not large_bed_contact now: 5000 mm2" in text
    assert f"lesson {lesson}; learned on slab.stl: a big face lifts" in text
    assert "doesn't reach this part by itself" in text


def test_a_lesson_far_from_its_trait_is_shown_unselected(tmp_path: Path, ledger: Ledger) -> None:
    past = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "seed", past)
    lesson = lessons.write(ledger, "seed", "reviewer: test", "a big face shows its corners",
                           ["large_bed_contact"], "seam_position=back")
    lessons.accept(ledger, lesson, "reviewer: test")
    printed(ledger, "first", past, changes=(Change(
        key="seam_position", preset="aligned", value="back", source=Source.LESSON, lesson=lesson,
        reason="learned on slab.stl: a big face shows its corners"),))
    found = offering(ledger, this(box(tmp_path, "slab.stl", 40, 50, 12)))
    assert "its area on the plate is too far from that part's" in why_not(found, "seam_position")


def test_a_lesson_retired_since_is_shown_and_not_selected(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "seed", here)
    lesson = lessons.write(ledger, "seed", "reviewer: test", "a big face lifts",
                           ["large_bed_contact"], "brim_type=outer_only")
    lessons.accept(ledger, lesson, "reviewer: test")
    printed(ledger, "first", here, changes=(Change(
        key="brim_type", preset="no_brim", value="outer_only", source=Source.LESSON,
        lesson=lesson, reason="learned on slab.stl: a big face lifts"),))
    lessons.retire(ledger, lesson, "reviewer: test", "a made-up reason")
    assert f"lesson {lesson} has been retired since" in why_not(
        offering(ledger, this(here)), "brim_type")


# --------------------------------------------------------------------------
# One that may not fit isn't pre-selected, and says why
# --------------------------------------------------------------------------


def test_another_filament_unselects_a_temperature_and_a_fan_only(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here,
            (yours("nozzle_temperature", "235"), yours("fan_max_speed", "40"),
             yours("wall_loops", "4")),
            filament="Made-up PETG", kind="PETG")
    found = offering(ledger, this(here))
    assert "another filament: that was PETG, this is PLA" in why_not(found, "nozzle_temperature")
    assert "another filament" in why_not(found, "fan_max_speed")
    assert selected(found, "wall_loops")


def test_another_variety_of_the_same_type_is_another_filament(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("nozzle_temperature", "215"),), filament="Made-up Silk")
    assert "that was Made-up Silk, this is Made-up PLA" in why_not(
        offering(ledger, this(here)), "nozzle_temperature")


def test_a_failed_print_unselects_what_its_symptom_bears_on(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here,
            (yours("brim_type", "outer_only"), yours("wall_loops", "4")),
            result="failed", symptom="warping")
    found = offering(ledger, this(here))
    assert why_not(found, "brim_type") == "that print failed with warping, which this bears on"
    assert selected(found, "wall_loops")


def test_a_failure_of_another_kind_could_be_any_settings(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),), result="failed",
            symptom="other", note="it rang when tapped")
    assert "which setting that bears on isn't known" in why_not(
        offering(ledger, this(here)), "wall_loops")


def test_a_success_with_a_symptom_is_no_failure(tmp_path: Path, ledger: Ledger) -> None:
    """mqucifer/print-shop#189: told by the result, never by a symptom being set."""
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("brim_type", "outer_only"),),
            result="success", symptom="warping")
    found = offering(ledger, this(here))
    assert selected(found, "brim_type")
    assert found.past is not None and found.past.came_out == "success, with warping"


def test_a_value_corrected_since_says_to_what(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"), yours("seam_position", "back")))
    later = box(tmp_path, "plinth-b.stl", 62, 41, 12)
    printed(ledger, "later", later, (yours("wall_loops", "6"),))
    found = offering(ledger, this(here))
    assert "corrected since, to 6, on plinth-b.stl (success)" in why_not(found, "wall_loops")
    assert selected(found, "seam_position")


def test_supports_decided_for_no_overhang_are_in_doubt_over_one(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", write_stl("cube", tmp_path), (yours("enable_support", "0"),))
    found = offering(ledger, this(write_stl("tee", tmp_path)))
    guarded = Proposal(changes=(Change(key="enable_support", preset="0", value="1",
                                       reason="it would print into the air", source=Source.GUARD),))
    assert why_not(found, "enable_support", guarded) == "its overhang is too far from that part's"


def test_a_pause_outside_this_part_cant_be_used(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", box(tmp_path, "plinth.stl", 60, 40, 12), (yours(PAUSE, "9"),))
    found = offering(ledger, this(box(tmp_path, "plinth-b.stl", 60, 40, 8)))
    item, standing = standings(found)[PAUSE]
    assert standing.mark == "-"
    assert item.blocked == "a pause at 9 mm is outside this part: it's 8 mm tall"
    refused = parse_answer("use 1", request_for(found))
    assert refused == f"{PAUSE} can't be used: {item.blocked}"


def test_a_pause_off_this_parts_features_isnt_selected(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", stepped(tmp_path, "step.stl"), (yours(PAUSE, "7.4"),))
    assert selected(offering(ledger, this(stepped(tmp_path, "step-b.stl", 42))), PAUSE)
    plain = offering(ledger, this(box(tmp_path, "block.stl", 40, 30, 14.4)))
    assert "it's off this part's features: nothing starts or ends at 7.4 mm" in why_not(
        plain, PAUSE)


def test_a_pause_a_layer_under_this_parts_pocket_isnt_ticked_and_names_its_height(
    tmp_path: Path, ledger: Ledger
) -> None:
    """Pre-selected, it'd be mqucifer/print-shop#203's fault again: a layer early."""
    printed(ledger, "first", pocketed(tmp_path, "tray.stl", 7.4), (yours(PAUSE, "7.4"),))
    found = offering(ledger, this(pocketed(tmp_path, "tray-b.stl", 7.6)))
    item, standing = standings(found)[PAUSE]
    assert not standing.selected
    assert standing.why == ("this part's face is at z 7.6, not 7.4: pause_at=7.6 pauses there",)
    assert item.value == "7.4", "never changed for them"
    assert item.fits == "", "and nothing reassuring is said of it"


def test_a_pause_a_layer_over_this_parts_pocket_isnt_ticked_either(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", pocketed(tmp_path, "tray.stl", 7.4), (yours(PAUSE, "7.4"),))
    found = offering(ledger, this(pocketed(tmp_path, "tray-b.stl", 7.2)))
    assert "face is at z 7.2, not 7.4: pause_at=7.2 pauses there" in why_not(found, PAUSE)


def test_a_face_between_two_layers_tops_isnt_given_as_a_height_to_type(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", pocketed(tmp_path, "tray.stl", 7.4), (yours(PAUSE, "7.4"),))
    found = offering(ledger, this(pocketed(tmp_path, "tray-b.stl", 7.5)))
    said = why_not(found, PAUSE)
    assert "no layer tops out at 7.5" in said and "pauses there" not in said


def test_a_pause_at_this_parts_own_face_is_ticked(
    tmp_path: Path, ledger: Ledger
) -> None:
    printed(ledger, "first", pocketed(tmp_path, "tray.stl", 7.4), (yours(PAUSE, "7.4"),))
    longer = stepped(tmp_path, "tray-b.stl", 42)
    item, standing = standings(offering(ledger, this(longer)))[PAUSE]
    assert standing.selected and item.fits == "a flat face is at z 7.4 here too"


def test_on_the_same_file_a_pause_needs_no_feature(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "block.stl", 40, 30, 14)
    printed(ledger, "first", here, (yours(PAUSE, "6.2"),))
    assert selected(offering(ledger, this(here)), PAUSE)


def test_an_older_rows_pause_is_converted_never_copied_raw(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "block.stl", 40, 30, 14)
    printed(ledger, "first", here, (yours(PAUSE, "6.2"),), older=True)
    found = offering(ledger, this(here))
    item, standing = standings(found)[PAUSE]
    assert standing.selected and item.value == "6"
    assert item.notes == ("6.2 mm then is 6 mm now, to pause where that print did: a pause's "
                          "height is now the top of the layer it comes after",)
    used = parse_answer("use", request_for(found))
    assert isinstance(used, Review)
    assert [(c.key, c.value) for c in used.corrections] == [(PAUSE, "6")]


def test_a_pause_whose_gcode_doesnt_say_where_isnt_carried(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "block.stl", 40, 30, 14)
    printed(ledger, "first", here, (yours(PAUSE, "6.2"), yours("wall_loops", "4")), unread=True)
    found = offering(ledger, this(here))
    item, standing = standings(found)[PAUSE]
    assert standing.mark == "-"
    assert "doesn't say where it paused" in item.blocked
    used = parse_answer("use", request_for(found))
    assert isinstance(used, Review)
    assert [c.key for c in used.corrections] == ["wall_loops"], "left out, with a note"


def test_one_that_didnt_reach_that_prints_slice_says_so(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    missed = yours("wall_loops", "4").model_copy(update={"not_applied": "asked 4, sliced 2"})
    printed(ledger, "first", here, (missed,))
    assert "it didn't reach that print's slice: asked 4, sliced 2" in why_not(
        offering(ledger, this(here)), "wall_loops")


def test_a_row_from_before_the_read_back_is_read_by_its_config(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("bridge_speed", "25"),), config={"bridge_speed": "50"})
    assert "asked 25, sliced 50" in why_not(offering(ledger, this(here)), "bridge_speed")


def test_a_key_this_prints_presets_dont_have_cant_be_used(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("made_up_key", "1"),))
    _, standing = standings(offering(ledger, this(here)))["made_up_key"]
    assert standing.mark == "-" and "isn't a setting" in standing.why[0]


def test_what_the_person_set_at_this_review_stands(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))
    found = offering(ledger, this(here))
    now = Proposal(changes=(yours("wall_loops", "3"),))
    assert "you set it to 3 at this review" in why_not(found, "wall_loops", now)
    so = Proposal(changes=(yours("wall_loops", "4"),))
    assert standings(found, so)["wall_loops"][1].mark == "="


def test_an_answer_corrected_then_is_still_offered_over_the_same_answer(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    question = Question(feature="purpose", text="What is it for?", options=(
        Option(label="a gift", settings={"wall_loops": "3"}, impact="sturdier"),))
    printed(ledger, "first", here, (yours("wall_loops", "4"),),
            intake=Intake(questions=(question,), answers=(Answer(choice=0),)))
    found = offering(ledger, this(here))
    same = Proposal(changes=(Change(key="wall_loops", preset="2", value="3",
                                    reason="a gift", source=Source.ANSWER),))
    assert selected(found, "wall_loops", same)
    another = Proposal(changes=(Change(key="wall_loops", preset="2", value="5",
                                       reason="a load", source=Source.ANSWER),))
    assert "your answer set it to 5 here" in why_not(found, "wall_loops", another)


def test_a_lesson_that_print_wasnt_given_puts_the_old_value_in_doubt(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))
    newer = Proposal(changes=(Change(key="wall_loops", preset="2", value="5", lesson=8,
                                     reason="learned since", source=Source.LESSON),))
    assert "lesson 8 sets it to 5 here" in why_not(
        offering(ledger, this(here)), "wall_loops", newer)


# --------------------------------------------------------------------------
# At the review: use, use 2 4, clear and like
# --------------------------------------------------------------------------


@pytest.fixture
def two(tmp_path: Path, ledger: Ledger) -> Offering:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (
        yours("wall_loops", "4", "it gets handled"),
        yours("seam_position", "back", "the front is seen"),
        yours("nozzle_temperature", "235", "layers split"),
    ), filament="Made-up PETG", kind="PETG")
    printed(ledger, "other", box(tmp_path, "block.stl", 63, 42, 12), (yours("bridge_speed", "25"),))
    return offering(ledger, this(here))


def test_nothing_is_applied_until_its_used(two: Offering) -> None:
    assert parse_answer("ok", request_for(two)) == Review(approved=True)


def test_use_takes_the_selected_ones_with_where_each_came_from(two: Offering) -> None:
    used = parse_answer("use", request_for(two))
    assert isinstance(used, Review)
    assert [(c.key, c.value, c.source, c.reason) for c in used.corrections] == [
        ("wall_loops", "4", Source.SPONSOR, "it gets handled"),
        ("seam_position", "back", Source.SPONSOR, "the front is seen"),
    ]
    assert used.corrections[0].used == Used(job_id="first", stl_name="plinth.stl",
                                            by=Source.SPONSOR)
    assert used.corrections[0].preset == "2"


def test_use_by_number_takes_those_doubtful_ones_too(two: Offering) -> None:
    used = parse_answer("use 2 3", request_for(two))
    assert isinstance(used, Review)
    assert [c.key for c in used.corrections] == ["seam_position", "nozzle_temperature"]


def test_any_can_be_cleared(two: Offering) -> None:
    cleared = parse_answer("clear 1", request_for(two))
    assert isinstance(cleared, Review) and cleared.browsing and cleared.offering is not None
    assert "you cleared it" in why_not(cleared.offering, "wall_loops")
    used = parse_answer("clear 1; use", request_for(two))
    assert isinstance(used, Review)
    assert [c.key for c in used.corrections] == ["seam_position"]
    every = parse_answer("clear", request_for(two))
    assert isinstance(every, Review) and every.offering is not None
    assert parse_answer("use", request_for(every.offering)) == (
        "nothing is ticked: name one by its number, such as use 1")


def test_another_print_is_looked_at_before_its_used(two: Offering) -> None:
    assert parse_answer("from 2; use", request_for(two)) == (
        "look at that print's settings first: from picks it, then use")
    picked = parse_answer("from 2", request_for(two))
    assert isinstance(picked, Review) and picked.browsing and picked.offering is not None
    used = parse_answer("use", request_for(picked.offering))
    assert isinstance(used, Review)
    assert [c.key for c in used.corrections] == ["bridge_speed"]


def test_typed_wins_over_used(two: Offering) -> None:
    both = parse_answer("use; wall_loops=6 because it carries a load", request_for(two))
    assert isinstance(both, Review)
    assert [(c.key, c.value, c.used is None) for c in both.corrections] == [
        ("seam_position", "back", False), ("wall_loops", "6", True)]


@pytest.mark.parametrize(("answer", "why"), [
    ("use 9", "a number from 1 to 3"),
    ("clear 0", "a number from 1 to 3"),
    ("from 7", "from picks a print from 1 to 2"),
    ("from", "from picks one print, such as from 2"),
])
def test_an_unreadable_use_says_why(two: Offering, answer: str, why: str) -> None:
    assert parse_answer(answer, request_for(two)) == why


def test_use_with_nothing_to_use_says_so() -> None:
    assert parse_answer("use", request_for(Offering())) == (
        "there's no past print's settings to use")


def test_what_was_used_shows_as_used_and_isnt_used_twice(two: Offering) -> None:
    first = parse_answer("use", request_for(two))
    assert isinstance(first, Review)
    after = request_for(two, Proposal().corrected(list(first.corrections)))
    assert standings(two, after.proposal)["wall_loops"][1] == Standing("=", ("used",))
    assert "[you, used from plinth.stl, job first]" in render(after)
    assert parse_answer("use 1", after) == "that's so here already"
    assert isinstance(parse_answer("use", after), str)


def test_on_the_command_picking_shows_only_the_offer_again(two: Offering) -> None:
    typed = iter(["from 2", "clear 1", "from 1", "use"])
    shown: list[str] = []
    review = cli_reviewer(lambda prompt: next(typed), shown.append)(request_for(two))
    assert [c.key for c in review.corrections] == ["wall_loops", "seam_position"]
    assert review.offering is not None
    assert ("other", "bridge_speed") in review.offering.cleared, "kept for the next review"
    whole = [text for text in shown if "Settings for this print" in text]
    assert len(whole) == 1 and len(shown) == 4


def test_the_offers_lines_fit_the_review(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    long = "the front is seen from the door and the side from the window, " * 3
    printed(ledger, "first", here, (yours("seam_position", "back", long.strip()),))
    printed(ledger, "other", box(tmp_path, "block.stl", 62, 41, 12), (yours("wall_loops", "4"),))
    lines = offer.render(offering(ledger, this(here)), Proposal(), "0,0,0")
    assert all(len(line) <= offer.WIDTH for line in lines)
    assert " ".join(long.split()) in " ".join(" ".join(lines).split())


# --------------------------------------------------------------------------
# The UX read's findings: answers, a plain ok, and laid out to be read
# --------------------------------------------------------------------------


def answered_print(ledger: Ledger, stl: Path) -> None:
    printed(ledger, "first", stl, (yours("wall_loops", "4"),), changes=(Change(
        key="top_shell_layers", preset="4", value="9", source=Source.ANSWER,
        reason="you chose 'screws': firmer"),))


def test_another_files_answer_is_never_ticked(tmp_path: Path, ledger: Ledger) -> None:
    answered_print(ledger, box(tmp_path, "block.stl", 62, 41, 12))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))
    assert why_not(found, "top_shell_layers") == "you're asked again for each job"
    assert selected(found, "wall_loops")
    text = render(request_for(found))
    assert text.count("you're asked again for each job") == 1
    assert "YOUR ANSWERS THEN (you're asked again for each job)" in text


def test_the_same_files_answer_is_ticked_only_where_this_job_didnt_ask(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    answered_print(ledger, here)
    assert selected(offering(ledger, this(here)), "top_shell_layers")
    asked = offering(ledger, this(here, asked=frozenset({"top_shell_layers"})))
    assert not selected(asked, "top_shell_layers")
    same = Proposal(changes=(Change(key="top_shell_layers", preset="4", value="9",
                                    reason="screws", source=Source.ANSWER),))
    assert standings(asked, same)["top_shell_layers"][1].mark == "="


def test_an_ok_with_ticked_ones_unused_says_so_once(two: Offering) -> None:
    typed = iter(["ok", "ok"])
    shown: list[str] = []
    review = cli_reviewer(lambda prompt: next(typed), shown.append)(request_for(two))
    assert review == Review(approved=True), "and the second carries nothing"
    assert shown[1:] == [
        "  2 ticked aren't used yet: use to take them, or ok again to go on without"]


def test_an_ok_then_use_takes_them(two: Offering) -> None:
    typed = iter(["ok", "use"])
    review = cli_reviewer(lambda prompt: next(typed), lambda text: None)(request_for(two))
    assert not review.approved
    assert [c.key for c in review.corrections] == ["wall_loops", "seam_position"]


def test_an_ok_with_none_ticked_goes_on(two: Offering) -> None:
    cleared = two.clear([])
    assert isinstance(cleared, Offering)
    review = cli_reviewer(lambda prompt: "ok", lambda text: None)(request_for(cleared))
    assert review == Review(approved=True)


def test_what_several_items_share_is_said_once(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    keys = ["wall_loops", "seam_position", "brim_type", "bridge_speed", "top_shell_layers",
            "enable_support", "nozzle_temperature", "fan_max_speed"]
    values = ["4", "back", "outer_only", "25", "9", "1", "215", "40"]
    reason = "it stands in the hall and is knocked by coats"
    note = "a corner came away when it was lifted by that corner alone, after a day on the shelf"
    printed(ledger, "first", here,
            tuple(yours(k, v, reason) for k, v in zip(keys, values, strict=True)),
            result="failed", symptom="other", note=note)
    lines = offer.render(offering(ledger, this(here)), Proposal(), "0,0,0")
    text = " ".join(" ".join(lines).split())
    assert "Came out FAILED, other" in text
    assert text.count("a corner came away") == 1
    assert text.count("which setting that bears on isn't known") == 1
    assert "None is ticked: that print failed (other)" in text
    assert text.count(reason) == 1 and f"1-8: {reason}" in text
    assert len(lines) <= 22 and all(len(line) <= offer.WIDTH for line in lines)
    assert "use 1 (none is ticked: by number)" in text


def test_differences_read_then_now_in_columns(tmp_path: Path, ledger: Ledger) -> None:
    printed(ledger, "first", box(tmp_path, "block.stl", 80, 41, 12), (yours("wall_loops", "4"),))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))
    lines = offer.render(found, Proposal(), "0,0,0")
    head = next(line for line in lines if line.startswith("  Differs"))
    assert head.split() == ["Differs", "then", "now"]
    length = next(line for line in lines if line.split()[:1] == ["length"])
    assert length.split() == ["length", "80", "60", "mm"]
    assert length.index("80") + 2 == head.index("then") + 4, "right under its heading"
    assert found.past is not None
    shares = [abs(a - b) / max(a, b) for _, a, b, _ in found.past.measures]
    assert shares[0] >= shares[-1], "the largest first"


def test_the_groups_share_their_columns_and_lessons_their_words(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "seed", here)
    learned = []
    for key, value in (("brim_type", "outer_only"), ("top_shell_layers", "8")):
        n = lessons.write(ledger, "seed", "reviewer: test", "a big face lifts",
                          ["large_bed_contact"], f"{key}={value}")
        lessons.accept(ledger, n, "reviewer: test")
        learned.append(Change(key=key, preset="", value=value, source=Source.LESSON, lesson=n,
                              reason="learned on slab.stl: a big face lifts"))
    printed(ledger, "first", here, (yours("wall_loops", "4"),), changes=tuple(learned))
    lines = offer.render(offering(ledger, this(here)), Proposal(), "0,0,0")
    assert len({line.index(" -> ") for line in lines if " -> " in line}) == 1
    assert sum("now -> use" in line for line in lines) == 1
    assert " ".join(lines).count("learned on slab.stl: a big face lifts") == 1


def test_an_unticked_value_thats_this_prints_already_is_set_here(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "2", "two is plenty"),),
            result="failed", symptom="other", note="it rang when tapped")
    found = offering(ledger, this(here))
    assert standings(found)["wall_loops"][1].mark == "="
    text = " ".join(" ".join(offer.render(found, Proposal(), "0,0,0")).split())
    assert "ALREADY SET HERE 1 wall_loops 2 (your preset) 1: two is plenty" in text
    assert "2 -> 2" not in text


def test_several_set_here_already_share_their_reason(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    why = "the presets do well here"
    printed(ledger, "first", here,
            (yours("wall_loops", "2", why), yours("bridge_speed", "50", why)),
            result="failed", symptom="other", note="it rang when tapped")
    lines = offer.render(offering(ledger, this(here)), Proposal(), "0,0,0")
    text = " ".join(" ".join(lines).split())
    assert "1 wall_loops 2 (your preset) 2 bridge_speed 50 (your preset)" in text
    assert text.count(why) == 1 and f"1, 2: {why}" in text


def test_a_lesson_set_here_already_is_a_short_row_with_its_words_beneath_once(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "seed", here)
    words = "a big face lifts at its corners when the room is cold and the door is left open"
    learned, given = [], []
    for key, value in (("wall_loops", "2"), ("bridge_speed", "50")):
        n = lessons.write(ledger, "seed", "reviewer: test", words, ["large_bed_contact"],
                          f"{key}={value}")
        lessons.accept(ledger, n, "reviewer: test")
        learned.append(Change(key=key, preset="", value=value, source=Source.LESSON, lesson=n,
                              reason=f"learned on slab.stl: {words}"))
        given.append(n)
    printed(ledger, "first", here, changes=tuple(learned))
    found = offering(ledger, this(here))
    lines = offer.render(found, Proposal(), "0,0,0")
    assert f"  1 wall_loops 2 (your preset; lesson {given[0]})" in lines
    assert f"  2 bridge_speed 50 (your preset; lesson {given[1]})" in lines
    assert " ".join(" ".join(lines).split()).count(words) == 1
    listed = Proposal(lessons=lessons.for_part(ledger, features_of(here), "PLA"))
    assert "a big face lifts" not in " ".join(offer.render(found, listed, "0,0,0")), (
        "in the Lessons section, so not said twice")


def test_the_persons_ticked_override_stays_theirs_at_the_presets_value(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "2"),))
    assert selected(offering(ledger, this(here)), "wall_loops")


def test_a_long_reason_goes_beneath_at_full_width(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    long = "the front is seen from the door and the side from the window when it is open"
    printed(ledger, "first", here, (yours("seam_position", "back", long),))
    lines = offer.render(offering(ledger, this(here)), Proposal(), "0,0,0")
    assert f"      {long}" in lines


def test_a_key_that_only_has_infill_in_its_name_rests_on_no_wall() -> None:
    assert offer._rests_on("sparse_infill_density") == ("section",)
    assert offer._rests_on("infill_direction") == ()


def test_after_a_use_it_says_all_used(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))
    found = offering(ledger, this(here))
    used = parse_answer("use", request_for(found))
    assert isinstance(used, Review)
    lines = offer.render(found, Proposal().corrected(list(used.corrections)), "0,0,0")
    assert "  USED: 1 wall_loops 4" in lines and lines[-1] == "  All used. ok to go on."
    assert not any("use (the" in line for line in lines)


def test_already_set_and_cant_use_are_said_in_words(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("made_up_key", "1", "a try"), yours("wall_loops", "4")))
    now = Proposal(changes=(Change(key="wall_loops", preset="2", value="4", lesson=3,
                                   reason="learned", source=Source.LESSON),))
    text = "\n".join(offer.render(offering(ledger, this(here)), now, "0,0,0"))
    assert "ALREADY SET HERE\n  2 wall_loops 4 (lesson 3)" in text
    assert "CAN'T USE 1: 'made_up_key' isn't a setting" in text
    assert "1 made_up_key 1: a try" in text
    assert "[=]" not in text and "[-]" not in text


def test_another_print_says_what_it_would_give(two: Offering) -> None:
    text = "\n".join(offer.render(two, Proposal(), "0,0,0"))
    assert "from 2  block.stl," in text and "bridge_speed 25; success" in text
    assert "from 2 (another print)" in text


# --------------------------------------------------------------------------
# The model only adds to it
# --------------------------------------------------------------------------


def test_the_models_doubts_only_ever_unselect(tmp_path: Path, ledger: Ledger) -> None:
    printed(ledger, "first", box(tmp_path, "block.stl", 62, 41, 12),
            (yours("wall_loops", "4"), yours("seam_position", "back")))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))
    reply = {"doubts": [{"key": "wall_loops", "why": "it's narrower"},
                        {"key": "layer_height", "why": "never offered"}]}
    doubting = Doubting.start(found, lambda system, user, shape: reply)
    assert doubting is not None and doubting.wait(10)
    added = doubting.added_to(found)
    assert why_not(added, "wall_loops") == "the model doubts it: it's narrower"
    assert selected(added, "seam_position")
    assert set(standings(added)) == {"wall_loops", "seam_position"}, "it adds no setting"
    assert doubting.added_to(added) == added, "each doubt once"


def test_the_offer_doesnt_wait_for_the_model(tmp_path: Path, ledger: Ledger) -> None:
    printed(ledger, "first", box(tmp_path, "block.stl", 62, 41, 12), (yours("wall_loops", "4"),))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))
    released = threading.Event()

    def slow(system: str, user: str, shape: dict[str, Any]) -> dict[str, Any]:
        released.wait(10)
        return {"doubts": []}

    doubting = Doubting.start(found, slow)
    assert doubting is not None
    meanwhile = doubting.added_to(found)
    assert selected(meanwhile, "wall_loops") and "still reading" in meanwhile.model
    assert "still reading" in render(request_for(meanwhile))
    released.set()
    assert doubting.wait(10) and doubting.added_to(meanwhile).model == ""


def test_the_offer_stands_when_the_model_fails(tmp_path: Path, ledger: Ledger) -> None:
    printed(ledger, "first", box(tmp_path, "block.stl", 62, 41, 12), (yours("wall_loops", "4"),))
    found = offering(ledger, this(box(tmp_path, "plinth.stl", 60, 40, 12)))

    def down(system: str, user: str, shape: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("no model")

    doubting = Doubting.start(found, down)
    assert doubting is not None and doubting.wait(10)
    stood = doubting.added_to(found)
    assert selected(stood, "wall_loops") and "the offer stands without it" in stood.model


def test_the_same_file_asks_no_model(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))

    def never(system: str, user: str, shape: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("no model is asked about the same file")

    assert Doubting.start(offering(ledger, this(here)), never) is None


# --------------------------------------------------------------------------
# What was used, in the ledger, the desk and the lessons
# --------------------------------------------------------------------------


def test_a_print_used_from_another_says_so_where_its_read(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    own = yours("wall_loops", "4", used=Used(job_id="first", stl_name="plinth.stl",
                                              by=Source.SPONSOR))
    answered = yours("top_shell_layers", "9", used=Used(job_id="first", stl_name="plinth.stl",
                                                         by=Source.ANSWER))
    row = printed(ledger, "second", here, (own, answered), intake=Intake())
    assert row.corrections is not None and row.corrections[0].used == own.used
    assert "wall_loops=4 (used from plinth.stl, job first)" in describe(row)
    assert "used from plinth.stl, job first" in _change_rows(row.corrections)
    copied = again.again(row, here, part_found=True)
    assert copied.corrections == "wall_loops=4 because it gets handled"
    assert any("use them again at the review (use): top_shell_layers=9" in n for n in copied.notes)


def test_carrying_an_override_isnt_a_lesson_accepted(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))
    [drawn] = ledger.query("SELECT id, status FROM print_lessons")
    carried = (
        yours("wall_loops", "4", used=Used(job_id="first", stl_name="plinth.stl",
                                           by=Source.SPONSOR)),
        yours("top_shell_layers", "9", used=Used(job_id="first", stl_name="plinth.stl",
                                                 by=Source.ANSWER)),
    )
    printed(ledger, "second", here, carried)
    assert ledger.query("SELECT id, status FROM print_lessons") == [drawn]
    assert drawn["status"] == "candidate"
    evidence = ledger.query("SELECT job_id FROM print_lesson_evidence ORDER BY job_id")
    assert [e["job_id"] for e in evidence] == ["first", "second"]


def test_used_on_a_shape_no_lesson_reaches_its_a_candidate_for_that_shape(
    tmp_path: Path, ledger: Ledger
) -> None:
    """As one typed there is (mqucifer/print-shop#212): using instead of typing loses no shape."""
    printed(ledger, "first", box(tmp_path, "slab.stl", 101, 50, 12), (yours("wall_loops", "4"),))
    [first] = ledger.query("SELECT id FROM print_lessons")
    here = box(tmp_path, "slab-b.stl", 99, 50, 12)
    used = yours("wall_loops", "4", used=Used(job_id="first", stl_name="slab.stl",
                                              by=Source.SPONSOR))
    printed(ledger, "second", here, (used,))
    printed(ledger, "third", here, (used,))
    found = ledger.query("SELECT id, status, traits FROM print_lessons ORDER BY id")
    assert [f["status"] for f in found] == ["candidate", "candidate"], "none is accepted"
    assert found[0]["id"] == first["id"]
    assert json.loads(found[1]["traits"]) == list(traits(features_of(here)))
    evidence = ledger.query(
        "SELECT job_id FROM print_lesson_evidence WHERE lesson=? ORDER BY job_id", found[1]["id"])
    assert [e["job_id"] for e in evidence] == ["second", "third"], "the next use is evidence for it"
    assert "(used from slab.stl, job first)" in lessons.listing(ledger)


def test_using_again_what_a_reviewer_turned_down_draws_nothing(
    tmp_path: Path, ledger: Ledger
) -> None:
    here = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "first", here, (yours("wall_loops", "4"),))
    [first] = ledger.query("SELECT id FROM print_lessons")
    lessons.reject(ledger, first["id"], "reviewer: test", "only that part's")
    printed(ledger, "second", here, (yours("wall_loops", "4", used=Used(
        job_id="first", stl_name="slab.stl", by=Source.SPONSOR)),))
    assert [f["id"] for f in ledger.query("SELECT id FROM print_lessons")] == [first["id"]]


def test_used_at_a_lessons_own_value_the_lesson_stood(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "slab.stl", 101, 50, 12)
    printed(ledger, "seed", here)
    lesson = lessons.write(ledger, "seed", "reviewer: test", "a big face lifts",
                           ["large_bed_contact"], "brim_type=outer_only")
    lessons.accept(ledger, lesson, "reviewer: test")
    [given] = lessons.for_part(ledger, features_of(here), "PLA")
    printed(ledger, "second", here,
            (yours("brim_type", "outer_only", used=Used(job_id="seed", stl_name="slab.stl",
                                                        by=Source.LESSON, lesson=lesson)),),
            given=(given.model_copy(update={"applied": True}),))
    assert lessons.latest_scores(ledger, lesson)["second"]["score"] == "used"


def test_an_override_used_again_says_where_it_began(tmp_path: Path, ledger: Ledger) -> None:
    here = box(tmp_path, "plinth.stl", 60, 40, 12)
    printed(ledger, "second", here, (yours("wall_loops", "4", used=Used(
        job_id="first", stl_name="plinth-a.stl", by=Source.SPONSOR)),))
    found = offering(ledger, this(here))
    item, _ = standings(found)["wall_loops"]
    assert (item.used.job_id, item.used.first_job_id) == ("second", "first")
    assert "first on plinth-a.stl, job first" in render(request_for(found))


# --------------------------------------------------------------------------
# Through the daemon
# --------------------------------------------------------------------------


class AnyPartSlicer:
    """The recorded cube's slice for any part, with each pause put in where Orca puts it."""

    def __init__(self, tmp_path: Path) -> None:
        self.recorded = RecordedSlicer()
        folder = tmp_path / "recorded"
        folder.mkdir(exist_ok=True)
        self.cube = write_stl("cube", folder)
        self.calls = 0

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        self.calls += 1
        result = self.recorded(self.cube, profiles, outdir)
        for pause in profiles.pauses:
            with_pause(Path(result.gcode_path), pause.print_z)
        return result


@pytest.fixture
def profiles(tmp_path: Path) -> SlicerProfiles:
    process = {**json.loads(CI_PROFILES.process.read_text()), "wall_loops": "2",
               "initial_layer_print_height": "0.2", "layer_height": "0.2",
               "seam_position": "aligned"}
    (tmp_path / "process.json").write_text(json.dumps(process))
    filament = {**FILAMENT, "filament_settings_id": ["Made-up PLA"]}
    (tmp_path / "filament.json").write_text(json.dumps(filament))
    return CI_PROFILES.model_copy(update={"process": tmp_path / "process.json",
                                          "filament": tmp_path / "filament.json"})


Run = Callable[..., tuple[Row | None, Sponsor, AnyPartSlicer]]


@pytest.fixture
def run(tmp_path: Path, ledger: Ledger, profiles: SlicerProfiles) -> Run:
    def job(
        stl: Path, *answers: str, proposer: Proposer | None = None, as_laid: bool = True,
        replayed: Path | None = None,
    ) -> tuple[Row | None, Sponsor, AnyPartSlicer]:
        (tmp_path / "work").mkdir(exist_ok=True)
        bridge, sponsor, slicer = MemoryBridge(), Sponsor(*answers), AnyPartSlicer(tmp_path)
        daemon = Daemon(
            cfg=CFG, profiles=profiles, workdir=tmp_path / "work", printer=FramePrinter(),
            slicer=slicer, bridge=lambda cfg: bridge, ledger=ledger,
            proposer=proposer or Proposer(),
            asker=replay_asker(replayed) if replayed else lambda questions: (),
            reviewer=sponsor,
        )
        with daemon:
            job_id = daemon.submit(stl, as_laid=as_laid)
            daemon.wait_for_gate(job_id, timeout_s=30)
            if daemon.pending_gate() is not None:
                bridge.press(CFG, "approve")  # a press names its job
            daemon.wait_for_finish(job_id, timeout_s=30)
        return ledger.get(job_id), sponsor, slicer

    return job


def test_the_same_file_twice_one_use_applies_every_override_pause_included(
    tmp_path: Path, run: Run, ledger: Ledger
) -> None:
    cube = write_stl("cube", tmp_path)
    first, _, _ = run(
        cube,
        f"take; wall_loops=4 because I handle it; {PAUSE}=10.2 because a band of colour; "
        "nozzle_temperature=215 because the layers split",
        "ok",
        proposer=Proposer(ask=_model, ask_questions=_questions), replayed=ANSWERS,
    )
    assert first is not None and first.proposal is not None and first.corrections is not None

    second, sponsor, slicer = run(cube, "use", "ok")  # kept as laid, and no model
    shown = sponsor.shown[0]
    assert shown.proposal.model == "" and shown.proposal.changes == (), "nothing applied unseen"
    assert shown.offering is not None and shown.offering.past is not None
    assert shown.offering.past.job_id == first.job_id
    offered = standings(shown.offering, shown.proposal)
    assert set(offered) == {PAUSE, "wall_loops", "nozzle_temperature", "seam_position"}
    assert all(standing.selected for _, standing in offered.values())
    assert slicer.calls == 2, "one use, one slice again"

    assert second is not None and second.proposal is not None and second.corrections is not None
    assert {(c.key, c.value) for c in second.proposal.changes} == {
        (PAUSE, "10.2"), ("wall_loops", "4"), ("nozzle_temperature", "215"),
        ("seam_position", "back"),
    }
    assert second.reading is not None and first.reading is not None
    assert second.reading.pauses == first.reading.pauses, "it pauses where the first did"
    assert {c.used.job_id for c in second.corrections if c.used} == {first.job_id}
    assert all(c.used is not None for c in second.corrections)
    assert "I handle it" in render(shown)


def test_an_older_rows_pause_replayed_pauses_before_the_same_layer(
    tmp_path: Path, run: Run, ledger: Ledger
) -> None:
    """Copied raw it'd replay a layer late (mqucifer/print-shop#228)."""
    cube = write_stl("cube", tmp_path)
    older = printed(ledger, "older", cube, (yours(PAUSE, "10.2", "a band of colour"),), older=True)
    second, _, _ = run(cube, "use", "ok")
    assert second is not None and second.reading is not None and older.reading is not None
    [now], [then] = second.reading.pauses, older.reading.pauses
    assert (now.before_layer, now.z) == (then.before_layer, then.z)
    assert second.proposal is not None
    pause = second.proposal.get(PAUSE)
    assert pause is not None and pause.value == "10"


def test_approved_without_use_nothing_is_carried(tmp_path: Path, run: Run, ledger: Ledger) -> None:
    cube = write_stl("cube", tmp_path)
    run(cube, "wall_loops=4 because I handle it", "ok")
    second, sponsor, _ = run(cube, "ok")
    assert sponsor.shown[0].offering is not None and sponsor.shown[0].offering.options
    assert second is not None and second.proposal is not None
    assert second.corrections == () and second.proposal.changes == ()


def test_picking_and_clearing_slice_nothing(tmp_path: Path, run: Run) -> None:
    cube = write_stl("cube", tmp_path)
    run(cube, "wall_loops=4 because I handle it; seam_position=back because it's seen", "ok")
    second, sponsor, slicer = run(cube, "clear 1", "use", "ok")
    assert len(sponsor.shown) == 3 and slicer.calls == 2
    assert second is not None and second.corrections is not None
    assert [c.key for c in second.corrections] == ["seam_position"]
    last = sponsor.shown[-1]
    assert last.offering is not None
    assert "you cleared it" in why_not(last.offering, "wall_loops", last.proposal)


def test_a_pose_sent_back_then_is_used_with_the_settings_that_went_with_it(
    tmp_path: Path, run: Run, ledger: Ledger
) -> None:
    tee = write_stl("tee", tmp_path)
    found = tuple(candidates(tee))
    laid = next(c for c in found if c.rotation_deg == LAID)
    printed(ledger, "first", tee, (
        yours(ORIENTATION, pose_value(LAID), "its top face is seen",
              feature=", ".join(traits(features_of(tee)))),
        yours("wall_loops", "4"),
    ), rotation=LAID, source="sponsor")
    second, sponsor, _ = run(tee, "use", "ok", as_laid=False)  # the geometric pose is another
    shown = sponsor.shown[0]
    assert shown.orientation.rotation_deg != laid.rotation_deg
    assert shown.offering is not None
    pose = pose_value(shown.orientation.rotation_deg)
    assert all(s.selected for _, s in standings(shown.offering, shown.proposal, pose).values())
    assert second is not None and second.corrections is not None
    assert second.rotation_deg == LAID
    assert {(c.key, c.value) for c in second.corrections} == {
        (ORIENTATION, "0,0,0"), ("wall_loops", "4")}
    assert second.orientation_rationale == "sent back at the review, by you: its top face is seen"


def test_without_the_turn_what_held_in_that_pose_isnt_selected(
    tmp_path: Path, ledger: Ledger
) -> None:
    tee = write_stl("tee", tmp_path)
    other = next(c for c in candidates(tee) if c.rotation_deg != LAID)
    printed(ledger, "first", tee,
            (yours(ORIENTATION, pose_value(LAID), "its top face is seen"),
             yours("wall_loops", "4")),
            rotation=LAID, source="sponsor")
    here = replace(this(tee), rotation_deg=other.rotation_deg, height_mm=other.height_mm)
    found = offering(ledger, here)
    cleared = found.clear([1])
    assert isinstance(cleared, Offering)
    _, standing = standings(cleared, pose=here.pose)["wall_loops"]
    assert not standing.selected
    assert "the turn to it isn't selected" in "; ".join(standing.why)


def test_past_prints_that_cant_be_read_never_stop_a_print(
    tmp_path: Path, run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    from print_shop.orchestrator import graph

    def broken(*args: Any) -> Offering:
        raise ValueError("a made-up bad row")

    monkeypatch.setattr(graph, "offer_past_prints", broken)
    row, sponsor, _ = run(write_stl("cube", tmp_path), "ok")
    assert row is not None
    assert "the past prints couldn't be read, so none is offered: ValueError" in " ".join(
        render(sponsor.shown[0]).split())


def test_a_hundred_past_prints_are_read_within_seconds(tmp_path: Path, ledger: Ledger) -> None:
    past = box(tmp_path, "block.stl", 62, 41, 12)
    measured = features_of(past)
    for n in range(100):
        printed(ledger, f"job{n:03}", past, (yours("wall_loops", "4"),), features=measured,
                result=None)
    here = this(box(tmp_path, "plinth.stl", 60, 40, 12))
    began = time.monotonic()
    assert len(offering(ledger, here).options) == 5
    assert time.monotonic() - began < 10
