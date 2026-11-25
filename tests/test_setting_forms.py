"""A setting's value is refused before slicing when it isn't the form the setting takes
(mqucifer/print-shop#296).

At the review, `wall_loops=4 ok because …` set wall_loops to "4 ok", and only the read-back after
the slice said it didn't reach the file. The Sponsor, 2026-11-25: "That junk setting and number
types early is probably good to get fixed." Each setting's form is Orca's own (`orca_forms.json`,
from the installed Orca's `PrintConfig.cpp` by `scripts/orca_forms.py`); only the form is
checked, never the range. The profiles and slice are `test_setting_names`'s made-up ones, and the
phone's is `test_phone_needs`' rig; nothing reaches a printer, broker or ledger.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from print_shop.setup.names import ORCA_FORMS, form, not_a_form, typed
from print_shop.setup.review import Review, parse_answer, render
from print_shop.slicer.orca import sliced_by, version
from tests.test_setting_names import GCODE, Person, _request

ROOT = Path(__file__).resolve().parent.parent
FORMS = ROOT / "src" / "print_shop" / "setup" / "orca_forms.json"
ORCA_APP = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")


def taken(answer: str) -> list[tuple[str, str]]:
    review = parse_answer(answer, _request())
    assert isinstance(review, Review), review
    return [(c.key, c.value) for c in review.corrections]


# --------------------------------------------------------------------------
# Done when: wall_loops=4 ok is refused with the whole-number words and the line
# --------------------------------------------------------------------------


def test_four_ok_isnt_a_whole_number_and_the_line_is_given() -> None:
    said = parse_answer("wall_loops=4 ok because the walls carry it", _request())
    assert said == "'4 ok' isn't a whole number: wall_loops takes one, e.g. wall_loops=4"


def test_the_ok_after_a_wrong_form_asks_once() -> None:
    person = Person("wall_loops=4 ok because the walls carry it", "ok", "ok")
    assert person.review(_request()) == Review(approved=True), "nothing from it is set"
    assert ("  ok approves without your 'wall_loops=4 ok', which wasn't set. ok again to "
            "approve.") in person.shown


@pytest.mark.parametrize(("answer", "said"), [
    ("outer_wall_speed=fast", "'fast' isn't a number: outer_wall_speed takes one, e.g. "
                              "outer_wall_speed=60"),
    ("outer_wall_line_width=wide", "'wide' isn't mm or a percentage: outer_wall_line_width "
                                   "takes either, e.g. outer_wall_line_width=0"),
    ("layer_height=0.2 mm thick", "'0.2 mm thick' isn't a number: layer_height takes one, e.g. "
                                  "layer_height=0.2"),
])
def test_each_numbers_form_says_itself(answer: str, said: str) -> None:
    assert parse_answer(answer, _request()) == said


# --------------------------------------------------------------------------
# Done when: sparse_infill_density=15 and =15% are taken, =fifteen refused
# --------------------------------------------------------------------------


def test_a_percentage_is_taken_with_or_without_its_sign_as_orca_reads_it() -> None:
    assert taken("sparse_infill_density=15 because light") == [("sparse_infill_density", "15")]
    assert taken("sparse_infill_density=15% because light") == [("sparse_infill_density", "15%")]
    assert parse_answer("sparse_infill_density=fifteen because light", _request()) == (
        "'fifteen' isn't a percentage: sparse_infill_density takes one, e.g. "
        "sparse_infill_density=20%")


def test_mm_or_a_percentage_takes_either() -> None:
    assert taken("outer_wall_line_width=0.45 because x") == [("outer_wall_line_width", "0.45")]
    assert taken("outer_wall_line_width=105% because x") == [("outer_wall_line_width", "105%")]


# --------------------------------------------------------------------------
# Done when: an on/off setting takes on, off, 1 and 0, and refuses anything else
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "written"), [
    ("on", "1"), ("off", "0"), ("1", "1"), ("0", "0"), ("yes", "1"), ("no", "0"), ("ON", "1")])
def test_on_off_takes_on_off_one_and_zero(value: str, written: str) -> None:
    assert taken(f"enable_support={value} because x") == [("enable_support", written)]


@pytest.mark.parametrize("value", ["maybe", "2", "true-ish", "onn"])
def test_on_off_refuses_anything_else(value: str) -> None:
    assert parse_answer(f"enable_support={value} because x", _request()) == (
        f"'{value}' isn't on or off: enable_support takes on or off, e.g. enable_support=off")


def test_every_orca_bool_takes_on_and_off_not_only_the_named_switches() -> None:
    assert form("thick_bridges") == "bool"
    assert typed("thick_bridges", "on") == "1" and typed("thick_bridges", "off") == "0"


# --------------------------------------------------------------------------
# Done when: past the model's bounds, of the right form, is taken
# --------------------------------------------------------------------------


def test_past_the_models_bounds_of_the_right_form_is_taken() -> None:
    assert taken("wall_loops=12 because a solid part") == [("wall_loops", "12")]
    assert taken("top_shell_layers=40 because x") == [("top_shell_layers", "40")]
    assert taken("nozzle_temperature=300 because x") == [("nozzle_temperature", "300")]


# --------------------------------------------------------------------------
# The review's own units, and one extruder (print-shop-ux, on the design)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("answer", "written"), [
    ("fan_max_speed=80%", ("fan_max_speed", "80")),
    ("fan_max_speed=80 %", ("fan_max_speed", "80")),
    ("outer_wall_speed=200 mm/s", ("outer_wall_speed", "200")),
    ("nozzle_temperature=220 °C", ("nozzle_temperature", "220")),
    ("layer_height=0.2mm", ("layer_height", "0.2")),
])
def test_a_number_typed_with_the_unit_the_review_shows_is_taken_as_orca_takes_it(
    answer: str, written: tuple[str, str],
) -> None:
    assert taken(f"{answer} because x") == [written]


def test_another_unit_is_refused() -> None:
    assert parse_answer("outer_wall_speed=200 °C because x", _request()) == (
        "'200 °C' isn't a number: outer_wall_speed takes one, e.g. outer_wall_speed=200")


def test_a_percentage_of_mm_or_a_percentage_keeps_its_sign() -> None:
    assert typed("outer_wall_line_width", "100%") == "100%"
    assert typed("sparse_infill_density", "15%") == "15%"


def test_a_list_longer_than_the_extruders_is_refused() -> None:
    assert parse_answer("nozzle_temperature=220,230 because x", _request()) == (
        "this printer has one extruder: give one value, e.g. nozzle_temperature=220")
    assert parse_answer("filament_retraction_length=0,8 because x", _request()) == (
        "this printer has one extruder: give one value, e.g. filament_retraction_length=0.8")
    assert not_a_form("nozzle_temperature", "220,230", extruders=2) == ""
    assert not_a_form("nozzle_temperature", "220,230,240", extruders=2) == (
        "this printer has two extruders: give up to two values, e.g. nozzle_temperature=220")


# --------------------------------------------------------------------------
# Words, and a form that isn't known, read as before
# --------------------------------------------------------------------------


def test_words_read_as_279_made_them_and_unknown_forms_as_before() -> None:
    assert parse_answer("ironing_type=off because x", _request()) == (
        "'off' isn't one of ironing_type's choices: no ironing, top, topmost or solid. To turn "
        "it off: ironing_type=no ironing")
    assert taken("brim_type=inner_only because x") == [("brim_type", "inner_only")]
    assert form("filament_settings_id") == "strings"
    assert not_a_form("filament_settings_id", "anything at all") == ""
    assert not_a_form("not_a_key_orca_has", "anything") == ""


# --------------------------------------------------------------------------
# Done when: the same refusal comes on the phone's Change
# --------------------------------------------------------------------------


def test_the_phones_change_is_refused_in_the_same_words(tmp_path: Path) -> None:
    from print_shop.orchestrator.print_queue import Asked, Need  # noqa: PLC0415
    from tests.test_phone_needs import request, rig  # noqa: PLC0415
    from tests.test_phone_needs import since as since_  # noqa: PLC0415

    review = request()
    r = rig(tmp_path, {"j1": Asked(job_id="j1", need=Need.REVIEW, review=review)})
    phone = r.phone()
    done = phone.post("/job/j1/review", {"since": since_(r.queue.asks["j1"]), "do": "change",
                                         "key": "wall_loops", "value": "4 ok",
                                         "reason": "the walls carry the shelf"})
    assert done.status == 303, done.body
    said = parse_answer("wall_loops=4 ok because the walls carry the shelf", review)
    assert said == "'4 ok' isn't a whole number: wall_loops takes one, e.g. wall_loops=4"
    until = time.monotonic() + 10
    shown = phone.get("/job/j1/review")
    while "isn't a whole number" not in shown.body and time.monotonic() < until:
        time.sleep(0.05)
        shown = phone.get("/job/j1/review")
    assert said in " ".join(shown.body.replace("&#x27;", "'").split())
    assert r.queue.given == [], "nothing is set from it"


# --------------------------------------------------------------------------
# Which Orca: the table's, the installed one's, and a slice's by another
# --------------------------------------------------------------------------


def test_the_table_names_its_orca_and_every_setting_the_review_names() -> None:
    from print_shop.setup.names import PLAIN  # noqa: PLC0415

    table = json.loads(FORMS.read_text())
    assert table["orca"] == ORCA_FORMS
    assert "PrintConfig.cpp" in table["from"]
    assert [k for k in PLAIN if not form(k)] == [], "each named setting has Orca's form"


def test_a_slice_by_another_orca_says_whose_forms_are_read() -> None:
    assert sliced_by(GCODE) == "2.3.2", "the recorded slice is an older Orca's"
    text = render(_request(), 100)
    assert f"  orca      setting forms read from Orca {ORCA_FORMS}" in text


def test_a_slice_by_the_same_orca_says_nothing_of_it(tmp_path: Path) -> None:
    same = tmp_path / "same.gcode"
    same.write_text(GCODE.read_text().replace("OrcaSlicer 2.3.2", f"OrcaSlicer {ORCA_FORMS}", 1))
    from dataclasses import replace  # noqa: PLC0415

    request = _request()
    request = replace(request, slice=request.slice.model_copy(update={"gcode_path": same}))
    assert "setting forms read from" not in render(request, 100)


@pytest.mark.slicer
@pytest.mark.skipif(not ORCA_APP.exists(), reason="Orca isn't installed here")
def test_the_installed_orca_is_the_one_the_forms_are_read_from() -> None:
    # Fails once Orca is updated: run scripts/orca_forms.py on the new version's PrintConfig.cpp.
    assert version() == f"OrcaSlicer-{ORCA_FORMS}"
