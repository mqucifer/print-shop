"""A setting is found by plain words, and a wrong name suggests the right one.

mqucifer/print-shop#222. The review took Orca's keys only, and refused
`enable_overhang_bridge_fan`, a real Orca setting, as if it weren't one. Now `find <words>`
lists the settings this print's filament and process take, a key it can't use says which case
it is, and a setting is shown by its plain name, its key beside or beneath. The profiles are
print-shop's own stored ones, composed for Bambu PETG HF, and the slice is the recorded cube's
(`tests/data/recorded/cube.gcode`), whose config block has `enable_overhang_bridge_fan`.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.setup.bounds import MATERIAL, SETTINGS
from print_shop.setup.names import PLAIN, STANDS_IN, find, named, plain, refused, with_unit
from print_shop.setup.proposal import Change, Proposal, Source, read_back
from print_shop.setup.review import Review, ReviewRequest, cli_reviewer, parse_answer, render
from print_shop.slicer.orca import parse_config_block
from print_shop.slicer.store import Store, choose
from tests.fakes import DATA, STORE, STORE_PROFILES

_PETG = choose(Store(STORE), nozzle_mm=0.4, filament="Bambu PETG HF").compose(
    Path(tempfile.mkdtemp(prefix="print-shop-names-")))
PROCESS: dict[str, Any] = json.loads(_PETG["process"].read_text())
FILAMENT: dict[str, Any] = json.loads(_PETG["filament"].read_text())
GCODE = DATA / "recorded" / "cube.gcode"
CONFIG = parse_config_block(GCODE)


def _request(gcode: Path = GCODE) -> ReviewRequest:
    return ReviewRequest(
        presets=("Elegoo Centauri Carbon 0.4 nozzle", "0.20mm Standard @Elegoo CC 0.4 nozzle",
                 "Bambu PETG HF @System"),
        proposal=Proposal(
            reasoning="A made-up cube.", model="test",
            changes=(Change(key="wall_loops", preset="2", value="3", source=Source.MODEL,
                            reason="the flat faces show infill through 2 walls"),),
            kept=(Change(key="enable_support", preset="0", value="0", source=Source.MODEL,
                         reason="nothing overhangs"),),
        ),
        slice=SliceResult(gcode_path=gcode, layer_count=100, max_z_mm=20, filament_mm=900,
                          filament_cm3=2.9, filament_g=3.6, print_time_s=654),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat face down"),
        profiles=STORE_PROFILES,
        process=PROCESS,
        filament=FILAMENT,
    )


class Person:
    """Types each answer in turn at the review, and keeps what the review said."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.shown: list[str] = []

    def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)

    def review(self, request: ReviewRequest, width: int = 100) -> Review:
        reviewer = cli_reviewer(read=self.read, write=self.shown.append, forget=lambda: None,
                                asks=lambda: False, screen=lambda: None, width=lambda: width,
                                more=lambda: False)
        return reviewer(request)

    @property
    def said(self) -> str:
        return "\n".join(self.shown[1:])  # what came after the review's screen


# --------------------------------------------------------------------------
# Done when: "bridge fan" and enable_overhang_bridge_fan both lead to overhang_fan_speed
# --------------------------------------------------------------------------


def test_the_stored_petg_hf_lists_the_fan_on_bridges_and_not_the_switch() -> None:
    """The case the Sponsor met: the config block has the switch, the filament doesn't."""
    assert "enable_overhang_bridge_fan" in CONFIG
    assert "enable_overhang_bridge_fan" not in FILAMENT
    assert "enable_overhang_bridge_fan" not in PROCESS
    assert "overhang_fan_speed" in FILAMENT


def test_bridge_fan_finds_overhang_fan_speed_first_with_its_name_and_value() -> None:
    found = find("bridge fan", PROCESS, FILAMENT, CONFIG)
    first = found[0]
    assert (first.key, first.plain, first.now) == (
        "overhang_fan_speed", "Fan on bridges and overhangs", CONFIG["overhang_fan_speed"])
    assert first.stands_in == "bridges", "it does the job of the switch this filament lacks"
    assert all(f.key != "enable_overhang_bridge_fan" for f in found), "not one it takes"
    assert len(found) <= 5


def test_find_at_the_review_lists_best_first_and_changes_nothing() -> None:
    person = Person("find bridge fan", "stop")
    assert person.review(_request()) == Review(stop=True), "finding takes nothing"
    assert person.prompts == ["> ", "> "]
    lines = person.said.splitlines()
    assert lines[0] == "  Settings that fit 'bridge fan', best first:"
    assert lines[1].split() == ["Fan", "on", "bridges", "and", "overhangs", "now", "100%",
                                "overhang_fan_speed"]
    assert lines[2] == "      Bambu PETG HF uses this one for bridges."
    assert "Bridge speed" in person.said and "bridge_speed" in person.said
    assert "e.g. overhang_fan_speed=<value> because <why>" in " ".join(person.said.split())
    assert all(len(line) <= 100 for line in lines)


def test_enable_overhang_bridge_fan_is_a_real_setting_this_filament_doesnt_list() -> None:
    said = parse_answer("enable_overhang_bridge_fan=60 because loose inserts blow out",
                        _request())
    assert said == (
        "'enable_overhang_bridge_fan' is a real Orca setting, but Bambu PETG HF doesn't list "
        "it, so it can't be set here. Here overhang_fan_speed (Fan on bridges and overhangs, "
        "now 100%) does that job, for bridges")


def test_the_one_that_does_the_job_is_typed_at_fix_and_the_rest_of_the_line_held() -> None:
    """Through mqucifer/print-shop#268's fix>: the wall change waits while the key is found."""
    person = Person("wall_loops=4 because stiffer; enable_overhang_bridge_fan=60 because "
                    "loose inserts blow out", "find bridge fan",
                    "overhang_fan_speed=60 because loose inserts blow out")
    review = person.review(_request())
    assert person.prompts == ["> ", "fix> ", "fix> "]
    assert "Held, nothing sliced yet: wall_loops=4" in person.said
    assert [(c.key, c.preset, c.value, c.reason) for c in review.corrections] == [
        ("wall_loops", "2", "4", "stiffer"),
        ("overhang_fan_speed", "100", "60", "loose inserts blow out")]


def test_a_setting_named_in_words_before_the_equals_finds_its_key() -> None:
    said = parse_answer("bridge fan=60", _request())
    assert said == ("'bridge fan=60' isn't setting=value: a setting's key goes before the =. "
                    "'bridge fan' finds Fan on bridges and overhangs (overhang_fan_speed): type "
                    "overhang_fan_speed=60, or find bridge fan for others")


# --------------------------------------------------------------------------
# Done when: a misspelt key suggests the right one, and words that match nothing say so
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "right"), [
    ("overhang_fan_sped", "overhang_fan_speed"),
    ("nozzle_tempurature", "nozzle_temperature"),
    ("wall_loop", "wall_loops"),
    ("textured_plate_temp_inital_layer", "textured_plate_temp_initial_layer"),
])
def test_a_misspelt_key_isnt_an_orca_setting_and_suggests_the_right_one(
    typed: str, right: str
) -> None:
    said = parse_answer(f"{typed}=1", _request())
    assert isinstance(said, str)
    assert said.startswith(f"'{typed}' isn't an Orca setting. Nearest: {right} (")


def test_words_that_match_nothing_say_so() -> None:
    assert find("frobnicate widget", PROCESS, FILAMENT, CONFIG) == []
    person = Person("find frobnicate widget", "stop")
    person.review(_request())
    assert "Nothing this filament or process has fits 'frobnicate widget'" in person.said
    said = parse_answer("frobnicate=1", _request())
    assert said == ("'frobnicate' isn't an Orca setting, and nothing this print takes is near "
                    "it: find <words> looks one up")


def test_a_real_setting_neither_preset_lists_says_so_without_a_stand_in() -> None:
    """The machine's, here: Orca's, in the config block, and nothing a correction sets."""
    assert "printable_height" in CONFIG
    said = refused("printable_height", PROCESS, FILAMENT, CONFIG, preset="Bambu PETG HF")
    assert said.startswith("'printable_height' is a real Orca setting, but neither Bambu PETG "
                           "HF nor the process lists it, so it can't be set here")


def test_without_the_slices_config_a_named_setting_is_still_known_as_orcas() -> None:
    """A slice whose config block can't be read: `PLAIN`'s keys are still Orca's own."""
    said = parse_answer("enable_overhang_bridge_fan=1", _request(Path("missing.gcode")))
    assert isinstance(said, str) and "is a real Orca setting" in said
    assert "overhang_fan_speed" in said


def test_the_other_cases_are_still_said_as_ever() -> None:
    assert refused("name", PROCESS, FILAMENT, CONFIG) == (
        "'name' is a preset's record of itself, not a setting")
    assert refused("overhang_fan_speed", PROCESS, {}, CONFIG) == (
        "'overhang_fan_speed' is a filament setting, and this print has no filament preset")
    assert refused("overhang_fan_speed", PROCESS, FILAMENT, CONFIG) == "", "it can be set"


# --------------------------------------------------------------------------
# Done when: every key in bounds.SETTINGS and bounds.MATERIAL has a plain name
# --------------------------------------------------------------------------


def test_every_bounded_and_material_key_has_a_plain_name() -> None:
    missing = sorted(k for k in (*SETTINGS, *MATERIAL) if k not in PLAIN)
    assert missing == []
    names = [PLAIN[k][0] for k in (*SETTINGS, *MATERIAL)]
    assert all(name and name[0].isupper() and "_" not in name for name in names)


def test_each_plain_name_is_one_settings_save_orcas_two_names_for_the_chamber() -> None:
    by_name: dict[str, list[str]] = {}
    for key, (name, _) in PLAIN.items():
        by_name.setdefault(name, []).append(key)
    assert {n: ks for n, ks in by_name.items() if len(ks) > 1} == {
        "Chamber temperature": ["chamber_temperature", "chamber_temperatures"]}


def test_every_named_key_is_orcas_own() -> None:
    """In the slice's config block, or in the stored presets where Orca writes it otherwise."""
    stored = {k for kind in ("process", "filament") for f in (STORE / "standard" / kind).glob(
        "*.json") for k in json.loads(f.read_text())}
    assert sorted(k for k in (*PLAIN, *STANDS_IN) if k not in CONFIG and k not in stored) == []
    assert all(stand in PLAIN for stand, _ in STANDS_IN.values())


# --------------------------------------------------------------------------
# The plain names wherever a setting is shown, with the key beside or behind
# --------------------------------------------------------------------------


def test_the_settings_table_has_the_plain_name_first_and_the_key_after() -> None:
    request = _request()
    long = Change(key="textured_plate_temp_initial_layer", preset="70", value="75",
                  source=Source.LESSON, reason="a wide base lifted at 70")
    request = replace(request, proposal=request.proposal.model_copy(
        update={"changes": (*request.proposal.changes, long)}))
    text = render(request, 100)
    lines = text.splitlines()
    walls = next(line for line in lines if "wall_loops" in line)
    assert walls.split()[:2] == ["Walls", "wall_loops"]
    bed = lines.index(next(line for line in lines if line.startswith("  Bed, textured plate")))
    assert "textured_plate_temp_initial_layer" in lines[bed] or (
        lines[bed + 1].strip() == "textured_plate_temp_initial_layer"), "beside, or beneath"
    assert all(len(line) <= 100 for line in lines)


def test_a_narrow_terminal_puts_a_long_key_beneath_its_name() -> None:
    request = _request()
    long = Change(key="textured_plate_temp_initial_layer", preset="70", value="75",
                  source=Source.LESSON, reason="a wide base lifted at 70")
    request = replace(request, proposal=request.proposal.model_copy(
        update={"changes": (long,)}))
    lines = render(request, 60).splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith("  Bed, textured plate"))
    # Beneath its row, which may wrap its who first.
    assert "    textured_plate_temp_initial_layer" in lines[at + 1:at + 3]
    assert all(len(line) <= 60 for line in lines[at:at + 3])


def test_a_setting_without_a_plain_name_is_said_as_its_keys_words() -> None:
    assert plain("bridge_angle") == "Bridge angle"
    assert named("wall_loops") == "Walls (wall_loops)"
    assert plain("pause_at") == "Pause"


@pytest.mark.parametrize(("key", "value", "shown"), [
    ("overhang_fan_speed", "100", "100%"),
    ("sparse_infill_density", "15%", "15%"),
    ("nozzle_temperature", "240,240", "240 °C"),
    ("bridge_speed", "25", "25 mm/s"),
    ("sparse_infill_pattern", "gyroid", "gyroid"),
    ("wall_loops", "", "-"),
])
def test_a_value_is_shown_with_its_unit(key: str, value: str, shown: str) -> None:
    assert with_unit(key, value) == shown


def test_a_switch_is_named_for_what_it_sets_and_shown_off_or_on() -> None:
    """print-shop-ux, on mqucifer/print-shop#275: "Supports on  0  0" read as a contradiction."""
    assert plain("enable_support") == "Supports"
    lines = render(_request(), 100).splitlines()
    kept = next(line for line in lines if "enable_support" in line)
    assert kept.split()[:4] == ["Supports", "enable_support", "off", "off"]
    assert with_unit("enable_overhang_bridge_fan", "1") == "on"


def test_a_switch_may_be_typed_on_or_off() -> None:
    review = parse_answer("enable_support=on because the arch sags", _request())
    assert isinstance(review, Review)
    assert [(c.key, c.value) for c in review.corrections] == [("enable_support", "1")]
    review = parse_answer("enable_support=1 because the arch sags", _request())
    assert isinstance(review, Review) and review.corrections[0].value == "1", "as ever"
    review = parse_answer("wall_loops=on", _request())
    assert isinstance(review, Review) and review.corrections[0].value == "on", (
        "only a switch's on is read as 1: the rest is checked as typed")


def test_a_refusal_at_the_prompt_is_wrapped_to_the_width() -> None:
    person = Person("enable_overhang_bridge_fan=60", "stop")
    person.review(_request(), width=80)
    assert len(person.shown) == 2
    said = person.shown[1].splitlines()
    assert len(said) > 1 and all(len(line) <= 80 for line in said)
    assert said[0].startswith("  'enable_overhang_bridge_fan' is a real Orca setting")


def test_a_switch_typed_on_is_read_back_from_the_slice_as_1() -> None:
    """Typed `on`, it's 1 from the start, so the slice's config is read against 1 as ever."""
    review = parse_answer("enable_support=on because the arch sags", _request())
    assert isinstance(review, Review)
    asked = Proposal(changes=review.corrections)
    sliced = read_back(asked, PROCESS, FILAMENT, {**CONFIG, "enable_support": "1"})
    assert [(c.key, c.value) for c in sliced.changes] == [("enable_support", "1")]
    missed = read_back(asked, PROCESS, FILAMENT, {**CONFIG, "enable_support": "0"})
    assert [c.not_applied for c in missed.not_applied] == ["asked 1, sliced 0"]
