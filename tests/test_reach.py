"""A filament setting changed at the review reaches the slice, or the review says it didn't.

mqucifer/print-shop#206. One rule says which profile a key is written to, for the review, the
presets a proposal reads and `apply`; a key it can't place is refused in words; and each change
is read back from the sliced G-code's config. The profiles and parts here are made up, or
print-shop's own stored profiles. The `slicer` tests slice with the real Orca, on the Mac.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from print_shop.desk import page
from print_shop.desk.again import again
from print_shop.desk.read import JobRecord
from print_shop.ledger import Ledger
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer, lessons
from print_shop.setup.bounds import MATERIAL, SETTINGS
from print_shop.setup.intake import Intake, replay_asker
from print_shop.setup.proposal import (
    Change,
    GivenLesson,
    NotPlaced,
    PartFacts,
    Proposal,
    Source,
    apply,
    from_lessons,
    placeable,
    read_back,
)
from print_shop.setup.reach import not_sliced, profile_of
from print_shop.setup.review import Review, ReviewRequest, parse_answer, render
from print_shop.slicer.orca import (
    BedType,
    Slicer,
    SliceResult,
    SlicerProfiles,
    parse_config_block,
    slice_model,
    with_bed_type,
)
from print_shop.slicer.store import Store
from tests.fakes import (
    CI_PROFILES,
    DATA,
    STORE,
    FramePrinter,
    MemoryBridge,
    RecordedSlicer,
)
from tests.fixtures import write_stl
from tests.test_graph import needs_orca
from tests.test_job_e2e import CFG
from tests.test_review import ANSWERS, Sponsor, _model, _questions, _request

#: The filament keys outside `bounds.SETTINGS` that went to the process, with made-up values:
#: preset, then asked.
OUTSIDE = {
    "overhang_fan_speed": ("80", "30"),
    "overhang_fan_threshold": ("50%", "25%"),
    "close_fan_the_first_x_layers": ("2", "4"),
    "full_fan_speed_layer": ("0", "6"),
    "complete_print_exhaust_fan_speed": ("60", "20"),
    "chamber_temperature": ("0", "35"),
    "nozzle_temperature_initial_layer": ("215", "225"),
    "hot_plate_temp": ("55", "62"),
    "hot_plate_temp_initial_layer": ("55", "63"),
    "eng_plate_temp": ("55", "64"),
    "eng_plate_temp_initial_layer": ("55", "65"),
    "supertack_plate_temp": ("40", "42"),
    "supertack_plate_temp_initial_layer": ("40", "43"),
    "activate_air_filtration": ("0", "1"),
}
PROCESS = {"name": "Made-up process", "wall_loops": "2", "brim_width": "5"}
FILAMENT = {"name": "Made-up filament", "filament_type": ["PLA"],
            **{key: [preset] for key, (preset, _) in OUTSIDE.items()}}

#: A stored filament profile's keys that are its record of itself: never corrected.
RECORDS = {
    "compatible_printers", "description", "filament_id", "filament_settings_id", "from",
    "inherits", "instantiation", "name", "renamed_from", "setting_id", "type",
}
#: A stored filament key the review's `key=value` doesn't read, for its capitals.
NOT_TYPED = {"required_nozzle_HRC"}
STORED = Store(STORE).all("filament")


def _profiles(
    folder: Path, process: dict[str, Any] = PROCESS, filament: dict[str, Any] | None = FILAMENT,
) -> SlicerProfiles:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "process.json").write_text(json.dumps(process))
    update: dict[str, Any] = {"process": folder / "process.json"}
    if filament is not None:
        (folder / "filament.json").write_text(json.dumps(filament))
        update["filament"] = folder / "filament.json"
    return CI_PROFILES.model_copy(update=update)


def _written(profiles: SlicerProfiles) -> tuple[dict[str, Any], dict[str, Any]]:
    filament = json.loads(profiles.filament.read_text()) if profiles.filament else {}
    return json.loads(profiles.process.read_text()), filament


def _corrected(
    answer: str, process: dict[str, Any] = PROCESS, filament: dict[str, Any] = FILAMENT,
) -> Review | str:
    return parse_answer(answer, replace(_request(), process=process, filament=filament))


def _change(key: str, value: str, source: Source = Source.SPONSOR, **more: Any) -> Change:
    return Change(key=key, preset="", value=value, reason="made up", source=source, **more)


def _lesson(key: str, value: str) -> GivenLesson:
    return GivenLesson(id=7, text="made up", key=key, value=value, job_id="job-0",
                       stl_name="rail.stl", traits=("overhangs",), reviewed_by="a reviewer")


def _facts(process: dict[str, Any] = PROCESS, filament: dict[str, Any] = FILAMENT) -> PartFacts:
    return PartFacts(intake=Intake(), features=(), machine={}, process=process,
                     filament=filament)


# --------------------------------------------------------------------------
# One rule for which profile a key is written to
# --------------------------------------------------------------------------


def test_every_key_material_adds_by_name_is_tried_here() -> None:
    assert {key for key in MATERIAL if key not in SETTINGS} <= set(OUTSIDE)


@pytest.mark.parametrize("key", OUTSIDE)
def test_a_correction_of_a_filament_key_is_written_to_the_filament(
    key: str, tmp_path: Path
) -> None:
    preset, value = OUTSIDE[key]
    review = _corrected(f"{key}={value}")
    assert isinstance(review, Review)
    assert review.corrections[0].preset == preset
    applied = apply(_profiles(tmp_path), Proposal(changes=review.corrections), tmp_path / "out")
    process, filament = _written(applied)
    assert filament[key] == [value], "a filament value is a one-item list"
    assert process == PROCESS, "and nothing of it is the process's"


@pytest.mark.parametrize("key", OUTSIDE)
def test_a_lessons_change_of_a_filament_key_is_written_to_the_filament(
    key: str, tmp_path: Path
) -> None:
    preset, value = OUTSIDE[key]
    [change], _, [given] = from_lessons(_facts(), (_lesson(key, value),))
    assert (change.preset, change.value, given.applied) == (preset, value, True), (
        "shown against the filament's own value, never '- -> new'"
    )
    applied = apply(_profiles(tmp_path), Proposal(changes=(change,)), tmp_path / "out")
    process, filament = _written(applied)
    assert (filament[key], process) == ([value], PROCESS)


def test_the_standards_value_is_the_filaments_too() -> None:
    facts = replace(_facts(), standard_filament={"overhang_fan_speed": ["100"]})
    assert (facts.standard("overhang_fan_speed"), facts.preset("overhang_fan_speed")) == (
        "100", "80"
    )


@pytest.mark.parametrize("profile", STORED, ids=lambda p: p.name)
def test_every_stored_filament_key_that_can_be_corrected_is_written_to_the_filament(
    profile: Any, tmp_path: Path
) -> None:
    process = Store(STORE).all("process")[0].composed
    filament = profile.composed
    keys = set(filament) - RECORDS
    assert keys, "the profile has settings"
    for key in sorted(keys - NOT_TYPED):
        review = _corrected(f"{key}=7", process, filament)
        assert isinstance(review, Review), (key, review)
    profiles = _profiles(tmp_path, process, filament)
    changes = tuple(_change(key, "7") for key in sorted(keys))
    wrote, written = _written(apply(profiles, Proposal(changes=changes), tmp_path / "out"))
    assert {key for key in keys if written[key] != ["7"]} == set()
    assert wrote == process, "none of them is written to the process"
    for key in sorted(set(filament) & RECORDS):
        assert isinstance(_corrected(f"{key}=7", process, filament), str), key


@pytest.mark.parametrize("stored", Store(STORE).all("process"), ids=lambda p: p.name)
def test_every_stored_process_key_is_still_written_to_the_process(
    stored: Any, tmp_path: Path
) -> None:
    filament = STORED[0].composed
    keys = sorted(set(stored.composed) - RECORDS - {
        "print_settings_id", "compatible_printers_condition", "compatible_prints",
        "compatible_prints_condition", "version", "is_custom_defined",
    })
    profiles = _profiles(tmp_path, stored.composed, filament)
    changes = tuple(_change(key, "7") for key in keys)
    process, written = _written(apply(profiles, Proposal(changes=changes), tmp_path / "out"))
    assert {key for key in keys if process[key] != "7"} == set(), "as a value, not a list"
    assert written == filament


#: A made-up filament shaped like Bambu PETG HF: it lists no `additional_cooling_fan_speed`,
#: nor any other key declared for the filament.
SPARSE = {"name": "Made-up PETG HF", "filament_type": ["PETG"], "fan_cooling_layer_time": ["30"]}


def test_a_declared_key_the_preset_doesnt_list_is_accepted_at_the_review(tmp_path: Path) -> None:
    """The stored Bambu PETG HF's case, made up: refused as "isn't a setting" at the review,
    though a lesson could set it and it reached the G-code."""
    key = "additional_cooling_fan_speed"
    assert key not in SPARSE
    review = _corrected(f"{key}=20", PROCESS, SPARSE)
    assert isinstance(review, Review), review
    [change] = review.corrections
    assert (change.key, change.preset, change.value) == (key, "", "20")

    asked = Proposal(changes=(change,))
    applied = apply(_profiles(tmp_path, PROCESS, SPARSE), asked, tmp_path / "out")
    assert _written(applied) == (PROCESS, {**SPARSE, key: ["20"]})
    shown = read_back(asked, PROCESS, SPARSE, {key: "20"})
    assert re.search(rf"{key} +- -> 20", render(_request(shown))), "its preset shows as '-'"


@pytest.mark.parametrize("key", sorted(MATERIAL))
def test_a_key_declared_the_filaments_is_placed_there_whether_or_not_its_listed(
    key: str, tmp_path: Path
) -> None:
    review = _corrected(f"{key}=20", PROCESS, SPARSE)
    assert isinstance(review, Review), review
    [learned], _, [given] = from_lessons(_facts(PROCESS, SPARSE), (_lesson(key, "20"),))
    assert (given.applied, learned.preset) == (True, "")
    for n, change in enumerate((review.corrections[0], learned)):
        folder = tmp_path / str(n)
        applied = apply(_profiles(folder, PROCESS, SPARSE), Proposal(changes=(change,)),
                        folder / "out")
        assert _written(applied) == (PROCESS, {**SPARSE, key: ["20"]})


def test_the_declared_profile_comes_before_the_one_that_has_the_key() -> None:
    stray = {**PROCESS, "overhang_fan_speed": "55", "fan_max_speed": "55"}
    assert profile_of("overhang_fan_speed", stray, FILAMENT) == "filament"
    assert profile_of("fan_max_speed", stray, SPARSE) == "filament"
    assert profile_of("wall_loops", {"name": "lists none"}, {"wall_loops": ["2"]}) == "process"


def test_a_declared_process_key_the_preset_doesnt_list_is_accepted_too() -> None:
    review = _corrected("elefant_foot_compensation=0.1")
    assert isinstance(review, Review), review
    assert review.corrections[0].preset == ""


def test_a_lone_setting_of_each_kind_is_placed() -> None:
    assert profile_of("brim_width", PROCESS, FILAMENT) == "process"
    assert profile_of("overhang_fan_speed", PROCESS, FILAMENT) == "filament"


# --------------------------------------------------------------------------
# A key that can't be placed is refused in words
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("key", "process", "filament", "why"), [
    ("no_such_key", PROCESS, FILAMENT,
     "'no_such_key' isn't a setting in the filament or the process preset"),
    ("name", PROCESS, FILAMENT, "'name' is a preset's record of itself, not a setting"),
    ("brim_width", PROCESS, {**FILAMENT, "brim_width": ["5"]},
     "'brim_width' is in both the filament and the process preset, so it can't be placed"),
    ("fan_max_speed", PROCESS, {},
     "'fan_max_speed' is a filament setting, and this print has no filament preset"),
    ("overhang_fan_speed", PROCESS, {},
     "'overhang_fan_speed' is a filament setting, and this print has no filament preset"),
])
def test_a_key_that_cant_be_placed_is_refused_and_never_the_process_by_default(
    key: str, process: dict[str, Any], filament: dict[str, Any], why: str, tmp_path: Path
) -> None:
    assert _corrected(f"{key}=1", process, filament) == why

    changes, kept, [given] = from_lessons(_facts(process, filament), (_lesson(key, "1"),))
    assert (changes, kept, given.applied) == ([], [], False)
    assert given.why == f"it set nothing: {why}"

    profiles = _profiles(tmp_path, process, filament or None)
    asked = Proposal(changes=(_change("wall_loops", "4"), _change(key, "1")))
    with pytest.raises(NotPlaced, match=re.escape(why)):
        apply(profiles, asked, tmp_path / "out")
    assert not (tmp_path / "out").exists(), "nothing was written, the change beside it neither"
    assert [c.key for c in placeable(asked, process, filament).changes] == ["wall_loops"]
    shown = read_back(asked, process, filament, {"wall_loops": "4"})
    assert [c.key for c in shown.changes] == ["wall_loops"]
    assert [c.not_applied for c in shown.not_applied] == [f"asked 1, not written: {why}"]


def test_an_estimate_is_priced_with_a_filament_key_and_never_without_one_it_cant_place(
    tmp_path: Path,
) -> None:
    sliced: list[dict[str, Any]] = []

    def slicer(
        stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str,
    ) -> SliceResult:
        sliced.append(_written(profiles)[1])
        return SliceResult(gcode_path=Path("x.gcode"), layer_count=1, max_z_mm=1, filament_mm=1,
                           filament_cm3=1, filament_g=1, print_time_s=60)

    profiles = _profiles(tmp_path)
    option = {"overhang_fan_speed": "30"}
    assert graph._estimate(slicer, "x.stl", profiles, option, tmp_path / "e") is not None
    assert sliced[-1]["overhang_fan_speed"] == ["30"]
    assert graph._estimate(slicer, "x.stl", profiles, {"no_such_key": "1"}, tmp_path / "n") is None

    proposal = Proposal(changes=(_change("no_such_key", "1"),))
    cost = graph._coster(slicer, "x.stl", profiles, proposal, tmp_path / "c")
    assert cost((_change("activate_air_filtration", "1"),)) is not None, (
        "a change that can't be placed doesn't stop the others being priced"
    )
    assert sliced[-1]["activate_air_filtration"] == ["1"]


# --------------------------------------------------------------------------
# Read back from the slice
# --------------------------------------------------------------------------

READ: dict[str, dict[str, Any]] = {
    "process": {"infill_wall_overlap": "15%", "wall_infill_order": "inner",
                "outer_wall_speed": "160"},
    "filament": {"overhang_fan_speed": ["80"], "overhang_fan_threshold": ["50%"],
                 "filament_z_hop": ["0.4"], "chamber_temperatures": ["0"]},
}


@pytest.mark.parametrize(("key", "asked", "config", "why"), [
    ("overhang_fan_speed", "30", {"overhang_fan_speed": "30"}, ""),
    ("overhang_fan_speed", "30.0", {"overhang_fan_speed": "30"}, ""),
    ("overhang_fan_speed", "30", {"overhang_fan_speed": "80"}, "asked 30, sliced 80"),
    ("overhang_fan_speed", "30.5", {"overhang_fan_speed": "30"}, "asked 30.5, sliced 30"),
    ("overhang_fan_threshold", "25", {"overhang_fan_threshold": "95%"}, "asked 25, sliced 95%"),
    ("infill_wall_overlap", "22", {"infill_wall_overlap": "22%"}, ""),
    ("outer_wall_speed", "50%", {"outer_wall_speed": "60"}, "asked 50%, sliced 60"),
    ("filament_z_hop", "nil", {}, ""),
    ("filament_z_hop", "0.6", {}, "asked 0.6, and the slice's settings don't have it"),
    ("chamber_temperatures", "35", {"chamber_temperature": "35"}, ""),
    ("chamber_temperatures", "35", {"chamber_temperature": "0"}, "asked 35, sliced 0"),
    ("wall_infill_order", "outer", {}, "asked outer, and the slice's settings don't have it"),
    ("overhang_fan_speed", "30", None, "asked 30, and the slice's settings couldn't be read"),
])
def test_a_value_is_read_back_as_orca_writes_it(
    key: str, asked: str, config: dict[str, str] | None, why: str
) -> None:
    assert not_sliced(key, asked, READ["process"], READ["filament"], config) == why


def test_of_two_corrections_of_one_setting_the_last_is_the_one_asked() -> None:
    proposal = Proposal().corrected([_change("wall_loops", "3"), _change("wall_loops", "4")])
    assert [c.value for c in proposal.changes] == ["4"]


def test_the_review_shows_a_change_that_didnt_reach_the_slice_as_a_mismatch() -> None:
    asked = Proposal(
        changes=(_change("overhang_fan_speed", "30", Source.LESSON, lesson=7),
                 _change("wall_loops", "4")),
        lessons=(_lesson("overhang_fan_speed", "30").model_copy(update={"applied": True}),),
    )
    config = {"overhang_fan_speed": "80", "wall_loops": "4"}
    request: ReviewRequest = replace(
        _request(read_back(asked, PROCESS, FILAMENT, config)), process=PROCESS, filament=FILAMENT
    )
    text = render(request)
    assert "NOT APPLIED overhang_fan_speed: asked 30, sliced 80" in text
    assert "made up [lesson 7]" in text
    assert not re.search(r"overhang_fan_speed +\S+ -> 30", text), "never as old -> new"
    assert re.search(r"wall_loops +\S+ -> 4", text), "one that reached it shows as before"
    assert "(it didn't reach the slice: asked 30, sliced 80)" in text
    assert "(set it)" not in text


def test_with_nothing_in_the_slice_the_review_doesnt_say_there_were_no_changes() -> None:
    shown = read_back(
        Proposal(changes=(_change("overhang_fan_speed", "30"),)), PROCESS, FILAMENT,
        {"overhang_fan_speed": "80"},
    )
    text = render(_request(shown))
    assert "No change reached the slice." in text
    assert "your presets as they are" not in text


# --------------------------------------------------------------------------
# Through the daemon: the review, the gate, the ledger and the lessons
# --------------------------------------------------------------------------


def deaf(stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str) -> SliceResult:
    """A slicer that takes no change at all: the recorded cube, whatever it's given."""
    return RecordedSlicer()(stl_path, CI_PROFILES, outdir)


def sliced_with(ledger: Ledger, job_id: str) -> dict[str, str]:
    """The config block the ledger kept of the G-code that was sent."""
    [row] = ledger.query("SELECT slicer_config FROM prints WHERE job_id=?", job_id)
    config: dict[str, str] = json.loads(row["slicer_config"])
    return config


Job = Callable[..., tuple[str, Sponsor]]


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[tuple[Ledger, Job]]:
    """The recorded cube's job, its presets saying what the recording was sliced with."""
    recorded = parse_config_block(DATA / "recorded" / "cube.gcode")
    process = {**json.loads(CI_PROFILES.process.read_text()), "wall_loops": "2"}
    filament = {"name": "Made-up filament", "filament_type": ["PLA"],
                **{key: [recorded[key]]
                   for key in ("overhang_fan_speed", "activate_air_filtration")}}
    profiles = _profiles(tmp_path / "presets", process, filament)
    (tmp_path / "work").mkdir()
    stl = write_stl("cube", tmp_path)
    runs = iter(range(1, 10))
    with Ledger(tmp_path / "ledger.sqlite") as ledger:

        def run(
            *answers: str, slicer: Slicer | None = None, given: tuple[GivenLesson, ...] = (),
        ) -> tuple[str, Sponsor]:
            sponsor, bridge = Sponsor(*answers), MemoryBridge()
            work = tmp_path / "work" / str(next(runs))
            work.mkdir()
            daemon = Daemon(
                cfg=CFG, profiles=profiles, workdir=work, printer=FramePrinter(),
                slicer=slicer or RecordedSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
                proposer=Proposer(ask=_model, ask_questions=_questions,
                                  lessons=lambda features, kind, variety: given),
                asker=replay_asker(ANSWERS), reviewer=sponsor,
            )
            with daemon:
                job_id = daemon.submit(stl)
                daemon.wait_for_gate(job_id, timeout_s=30)
                bridge.press(CFG, "approve")
                assert daemon.wait_for_finish(job_id, timeout_s=30).printed
            return job_id, sponsor

        yield ledger, run


def test_a_filament_correction_reaches_the_slice_and_is_kept_as_applied(
    shop: tuple[Ledger, Job],
) -> None:
    ledger, run = shop
    job_id, sponsor = run(
        "overhang_fan_speed=30 because made up; activate_air_filtration=1", "ok"
    )
    text = render(sponsor.shown[1])
    assert re.search(r"overhang_fan_speed +100 -> 30", text)
    assert "NOT APPLIED" not in text
    row = ledger.get(job_id)
    assert row is not None and row.proposal is not None
    config = sliced_with(ledger, job_id)
    assert (config["overhang_fan_speed"], config["activate_air_filtration"]) == ("30", "1")
    assert {"overhang_fan_speed", "activate_air_filtration"} <= {
        c.key for c in row.proposal.changes}
    assert row.proposal.not_applied == ()
    assert [c.not_applied for c in row.corrections or ()] == ["", ""]


def test_a_correction_that_didnt_reach_the_slice_is_never_recorded_as_applied(
    shop: tuple[Ledger, Job],
) -> None:
    ledger, run = shop
    job_id, sponsor = run("overhang_fan_speed=30 because made up", "ok", slicer=deaf)
    why = "asked 30, sliced 100"
    assert f"NOT APPLIED overhang_fan_speed: {why}" in render(sponsor.shown[1])

    row = ledger.get(job_id)
    assert row is not None and row.proposal is not None
    assert row.proposal.get("overhang_fan_speed") is None
    assert why in [c.not_applied for c in row.proposal.not_applied]
    assert [c.not_applied for c in row.corrections or ()] == [why]
    said = describe(row)
    assert f"corrected      overhang_fan_speed=30 (not applied: {why})" in said
    assert f"overhang_fan_speed: not applied, {why}" in said
    shown = page.job_page(JobRecord(job_id=job_id, row=row))
    assert f"not applied: {why}" in shown
    assert "<strong>30</strong>" not in shown
    assert f"(not applied: {why})" in lessons.listing(ledger), "the reviewer of it is told"


def test_a_lesson_that_didnt_reach_the_slice_isnt_applied_or_scored_as_used(
    shop: tuple[Ledger, Job],
) -> None:
    ledger, run = shop
    earlier, _ = run("ok")
    made = lessons.write(ledger, earlier, "reviewer: test", "made up", ["overhangs"],
                         "overhang_fan_speed=30")
    lessons.accept(ledger, made, "reviewer: test")
    given = _lesson("overhang_fan_speed", "30").model_copy(update={"id": made})

    job_id, sponsor = run("ok", slicer=deaf, given=(given,))
    why = "it didn't reach the slice: asked 30, sliced 100"
    assert f"({why})" in render(sponsor.shown[0])
    [kept] = ledger.query("SELECT * FROM job_lessons WHERE job_id=?", job_id)
    assert (kept["applied"], kept["why"]) == (0, why)
    [scored] = ledger.query("SELECT * FROM print_lesson_scores WHERE job_id=?", job_id)
    assert (scored["score"], scored["why"]) == ("not_used", why)


def test_a_lesson_of_a_filament_key_reaches_the_slice_and_is_scored_as_used(
    shop: tuple[Ledger, Job],
) -> None:
    ledger, run = shop
    earlier, _ = run("ok")
    made = lessons.write(ledger, earlier, "reviewer: test", "made up", ["overhangs"],
                         "overhang_fan_speed=30")
    lessons.accept(ledger, made, "reviewer: test")
    given = _lesson("overhang_fan_speed", "30").model_copy(update={"id": made})

    job_id, sponsor = run("ok", given=(given,))
    assert re.search(r"overhang_fan_speed +100 -> 30", render(sponsor.shown[0]))
    assert sliced_with(ledger, job_id)["overhang_fan_speed"] == "30"
    [scored] = ledger.query("SELECT * FROM print_lesson_scores WHERE job_id=?", job_id)
    assert scored["score"] == "used"


def test_a_change_that_missed_is_still_asked_at_the_next_slice(
    shop: tuple[Ledger, Job],
) -> None:
    """Read back each time: one that missed the first slice and is in the next is applied."""
    ledger, run = shop
    replay = RecordedSlicer()
    calls: list[int] = []

    def late(stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str) -> SliceResult:
        calls.append(1)
        # The option's price, the first slice, then deaf for the first correction only.
        return (deaf if len(calls) == 3 else replay)(stl_path, profiles, outdir)

    job_id, sponsor = run("overhang_fan_speed=30", "wall_loops=4", "ok", slicer=late)
    assert [c.key for c in sponsor.shown[1].proposal.not_applied] == [
        "wall_loops", "overhang_fan_speed"
    ]
    assert sponsor.shown[2].proposal.not_applied == ()
    row = ledger.get(job_id)
    assert row is not None and row.proposal is not None
    assert row.proposal.get("overhang_fan_speed") is not None
    assert [c.not_applied for c in row.corrections or ()] == ["", ""]


def test_a_reading_taken_that_missed_is_still_said_to_take_again(
    shop: tuple[Ledger, Job],
) -> None:
    ledger, run = shop
    job_id, _ = run("take", "ok", slicer=deaf)
    row = ledger.get(job_id)
    assert row is not None and row.proposal is not None
    assert row.proposal.get("seam_position") is None
    assert any("seam_position=back" in note for note in again(row, None).notes)


# --------------------------------------------------------------------------
# The real Orca (`slicer`)
# --------------------------------------------------------------------------


def _orca(name: str, folder: Path) -> SlicerProfiles:
    store = Store(STORE)
    composed = {"process": store.all("process")[-1].composed,
                "machine": store.all("machine")[-1].composed,
                "filament": next(p for p in STORED if p.name == name).composed}
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}
    for kind, data in composed.items():
        paths[kind] = folder / f"{kind}.json"
        paths[kind].write_text(json.dumps(data))
    given = SlicerProfiles(machine=paths["machine"], process=paths["process"],
                           filament=paths["filament"])
    return with_bed_type(given, BedType.TEXTURED_PEI, folder / "bed")


@pytest.mark.slicer
@needs_orca
def test_orca_slices_a_changed_overhang_fan_and_air_filtration(tmp_path: Path) -> None:
    """In the config and in the fan commands: the T's overhang at 37 % of 255, and the exhaust
    fan on at 73 %."""
    profiles = _orca("Elegoo PETG @ECC", tmp_path / "presets")
    presets = _written(profiles)
    asked = {"overhang_fan_speed": "37", "activate_air_filtration": "1",
             "during_print_exhaust_fan_speed": "73"}
    proposal = Proposal(changes=tuple(_change(k, v) for k, v in asked.items()))
    stl = write_stl("tee", tmp_path)

    before = slice_model(stl, profiles, tmp_path / "before").gcode_path
    after = slice_model(stl, apply(profiles, proposal, tmp_path / "p"), tmp_path / "o").gcode_path

    config = parse_config_block(after)
    assert {key: config[key] for key in asked} == asked
    assert read_back(proposal, *presets, config).not_applied == ()

    def fans(gcode: Path) -> tuple[set[int], list[str]]:
        code = gcode.read_text().split("; CONFIG_BLOCK_START")[0]
        part = {round(float(s)) for s in re.findall(r"^M106 S([\d.]+)", code, re.M)}
        return part, re.findall(r"^M106 P3 S([\d.]+)", code, re.M)

    (part_before, exhaust_before), (part, exhaust) = fans(before), fans(after)
    overhang = {94, 95}  # 37 % of 255 is 94.35
    assert not overhang & part_before and overhang & part, "the overhang's fan, as asked"
    assert exhaust_before == ["0"], "the exhaust fan is never turned on"
    assert "186" in exhaust, "it's on while it prints, at 73 % of 255"


@pytest.mark.slicer
@needs_orca
def test_orca_taking_another_value_is_read_back_as_a_mismatch(tmp_path: Path) -> None:
    profiles = _orca("Elegoo PETG @ECC", tmp_path / "presets")
    proposal = Proposal(changes=(_change("overhang_fan_threshold", "25"),))
    sliced = slice_model(
        write_stl("cube", tmp_path), apply(profiles, proposal, tmp_path / "p"), tmp_path / "o"
    )
    shown = read_back(proposal, *_written(profiles), parse_config_block(sliced.gcode_path))
    assert [c.not_applied for c in shown.not_applied] == ["asked 25, sliced 95%"]


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize(("key", "value", "why"), [
    ("additional_cooling_fan_speed", "20", ""),
    ("supertack_plate_temp", "41", ""),
    ("supertack_plate_temp_initial_layer", "42", ""),
    # The stored profiles' `chamber_temperatures` wins over it: said, not shown as applied.
    ("chamber_temperature", "35", "asked 35, sliced 0"),
])
def test_orca_on_a_declared_key_the_stored_preset_doesnt_list(
    key: str, value: str, why: str, tmp_path: Path
) -> None:
    profiles = _orca("Bambu PETG HF @System", tmp_path / "presets")
    process, filament = _written(profiles)
    assert key not in filament
    review = _corrected(f"{key}={value}", process, filament)
    assert isinstance(review, Review), review
    proposal = Proposal(changes=review.corrections)
    sliced = slice_model(
        write_stl("cube", tmp_path), apply(profiles, proposal, tmp_path / "p"), tmp_path / "o"
    )
    shown = read_back(proposal, process, filament, parse_config_block(sliced.gcode_path))
    assert [c.not_applied for c in shown.not_applied] == ([why] if why else [])


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize("profile", STORED, ids=lambda p: p.name)
def test_orca_reads_back_every_stored_filament_key_as_reached(
    profile: Any, tmp_path: Path
) -> None:
    """Each key written as a change at its own value: none is called a mismatch by mistake."""
    profiles = _orca(profile.name, tmp_path / "presets")
    process, filament = _written(profiles)
    proposal = Proposal(changes=tuple(
        _change(key, str(value[0])) for key, value in filament.items()
        if key not in RECORDS and value
    ))
    sliced = slice_model(
        write_stl("cube", tmp_path), apply(profiles, proposal, tmp_path / "p"), tmp_path / "o"
    )
    shown = read_back(proposal, process, filament, parse_config_block(sliced.gcode_path))
    assert [(c.key, c.not_applied) for c in shown.not_applied] == []
