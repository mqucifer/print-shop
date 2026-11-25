"""The review's small wording points: plates, fix>, and the queue's settings in plain words
(mqucifer/print-shop#298).

Each was found while reading earlier PRs and parked so as not to hold them. Only words change: no
check, number or prompt does. The parts, profiles, slices and answers are made up; nothing
reaches a printer, broker or ledger.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from print_shop.core.state import AppliedOrientation, MeshReport, PlatePart
from print_shop.orchestrator import kept as kept_module
from print_shop.orchestrator.plate_job import still_holds
from print_shop.orchestrator.sending import Adding, _suggestions
from print_shop.setup.names import find, refused, setting_said
from print_shop.setup.proposal import Change, Source
from print_shop.setup.questions import PLATE_WORDS, SEEN
from print_shop.setup.reading import read_slice
from print_shop.setup.review import render
from print_shop.setup.screen import _layer, found
from tests.fakes import STORE_PROFILES
from tests.test_plate_job import Rig, _caps
from tests.test_setting_names import CONFIG, FILAMENT, GCODE, PROCESS, Person, _request
from tests.test_turned_answers import FLIPPED, no_support

WIDTH = 100


# --------------------------------------------------------------------------
# Plates
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("seconds", "said"), [
    (1.6, "1.6 s"), (10.4, "10.4 s"), (11.6, "11.6 s"), (12.4, "12.4 s"), (35.0, "35 s"),
    (59.9, "59.9 s"), (60.0, "1:00"), (193.0, "3:13"), (255.0, "4:15"), (2045.0, "34:05"),
])
def test_a_layer_under_a_minute_reads_in_seconds_and_from_a_minute_in_m_ss(
    seconds: float, said: str,
) -> None:
    assert _layer(seconds) == said


def test_the_layers_line_reads_each_figure_by_its_own_size() -> None:
    request = replace(_request(), reading=read_slice(GCODE))
    times = sorted(layer.time_s for layer in request.reading.layers)  # type: ignore[union-attr]
    line = " ".join(render(request, WIDTH).split())
    said = line.split(" layers 100, ")[1].split(" typical")[0]
    assert said == (f"{_layer(times[0])} to {_layer(times[-1])} each, "
                    f"{_layer(times[len(times) // 2])}")
    assert "0:0" not in said, "no figure under a minute is m:ss"


def _plate(tmp_path: Path) -> Rig:
    rig = Rig(tmp_path)
    with rig.daemon:
        job = rig.gated(_caps(tmp_path))
        rig.press("approve", job)
    return rig


def test_every_parts_faces_are_said_once_as_they_print(tmp_path: Path) -> None:
    rig = _plate(tmp_path)
    shown = rig.shown[0]
    assert "    every part: the walls and the top, as it prints" in shown
    assert "Seen as it prints:" not in shown
    part = shown.split("\nTHE PART", 1)[-1] if "\nTHE PART" in shown else shown
    assert "every part: the walls and the top, as it prints" in part, "under The part, as ever"


def test_a_plates_seam_reason_quotes_the_answer_as_its_question_showed_it(tmp_path: Path) -> None:
    rig = _plate(tmp_path)
    [seam] = [f for f in rig.reviews[-1].flags if f.id == "seam_seen"]
    assert seam.change is not None
    label = next(o.label for o in SEEN.options if o.recommended)
    shown = label
    for said, theirs in PLATE_WORDS.items():
        shown = shown.replace(said, theirs)
    assert shown != label, "a plate shows its seen question's labels in its own words"
    assert seam.change.reason.startswith(f"you said '{shown}', so the walls are seen")


def test_a_plates_turned_note_opens_as_one_parts_does_naming_the_answer() -> None:
    mesh = MeshReport(is_manifold=True, volume_mm3=1, surface_area_mm2=1, bbox_mm=(1, 1, 1),
                      overhang_area_mm2=0)
    part = PlatePart(n=1, name="cap.stl", given="cap.stl", stl="cap.stl", sha256="0", mesh=mesh,
                     applied=AppliedOrientation(rotation_deg=FLIPPED))
    _, note = still_holds(no_support(), (part,), ((0.0, 0.0, 0.0),))
    assert note.startswith("TURNED SINCE YOU ANSWERED: 'No support' was for 0,0,0, so at "
                           "180,0,0 the guard decides supports (part 1, cap.stl, to 180,0,0). "
                           "These were said of the parts as they lay when asked")


# --------------------------------------------------------------------------
# At fix>
# --------------------------------------------------------------------------


def test_a_fix_naming_a_held_key_says_which_stands() -> None:
    person = Person("wall_loops=3; ironing=0 because x", "wall_loops=4 because thicker")
    review = person.review(_request())
    # Words only: both are kept as the answer gave them, and the later stands, as before.
    assert [(c.key, c.value) for c in review.corrections] == [
        ("wall_loops", "3"), ("wall_loops", "4")]
    assert "  wall_loops was 3 in this answer: 4 stands" in person.shown


def test_ok_at_fix_says_it_isnt_a_fix() -> None:
    person = Person("wall_loops=3; ironing=0 because x", "ok", "drop")
    review = person.review(_request())
    assert [(c.key, c.value) for c in review.corrections] == [("wall_loops", "3")]
    assert "  ok isn't a fix here: type it corrected, drop, or back" in person.shown
    assert person.prompts == ["> ", "fix> ", "fix> "]


def test_a_refusal_with_nothing_held_wraps_at_the_prompt() -> None:
    # Already so since mqucifer/print-shop#278: held here at a narrow terminal.
    person = Person("ironing=0 because a test print", "stop")
    person.review(_request(), width=60)
    [said] = [s for s in person.shown[1:] if "isn't an Orca setting" in s]
    assert len(said.splitlines()) > 1 and all(len(line) <= 60 for line in said.splitlines())


# --------------------------------------------------------------------------
# The queue's and the orchestrator's lines say settings as the review does
# --------------------------------------------------------------------------


def test_a_setting_is_said_as_the_review_says_it() -> None:
    assert setting_said("wall_loops", "4") == "Walls (wall_loops) 4"
    assert setting_said("enable_support", "0") == "Supports (enable_support) off"
    assert setting_said("sparse_infill_density", "15%") == (
        "Infill density (sparse_infill_density) 15%")


def test_the_kept_answers_say_their_settings_in_words() -> None:
    change = Change(key="wall_loops", preset="2", value="4", reason="x", source=Source.SPONSOR)
    reviewed = kept_module._Reviewed(corrections=(change,))
    assert kept_module._said(reviewed) == "Walls (wall_loops) 4"


def test_a_suggestion_not_taken_is_said_in_words(tmp_path: Path) -> None:
    one = Adding(path=tmp_path / "part.stl", suggested=(("wall_loops", "99", "very strong"),))
    _, refused_ = _suggestions(one, STORE_PROFILES, "the sender")
    assert refused_ == ["not taken: the suggestion Walls (wall_loops) 99: outside 1-10"]


# --------------------------------------------------------------------------
# An override Orca leaves unset is the printer's
# --------------------------------------------------------------------------


def test_an_unset_override_says_the_printers_applies() -> None:
    assert FILAMENT["filament_retraction_length"] == ["nil"]
    said = refused("filament_retraction_lenght", PROCESS, FILAMENT, CONFIG)
    assert said == ("'filament_retraction_lenght' isn't an Orca setting. Nearest: "
                    "filament_retraction_length (Retraction length, not set here, so the "
                    "printer's 0.8 mm applies)")
    without = {k: v for k, v in CONFIG.items() if k != "retraction_length"}
    assert "(Retraction length, not set here, so the printer's applies)" in refused(
        "filament_retraction_lenght", PROCESS, FILAMENT, without)


def test_find_says_not_set_here_and_the_printers_beneath() -> None:
    lines = found("retraction length", find("retraction length", PROCESS, FILAMENT, CONFIG),
                  "Bambu PETG HF", WIDTH)
    at = next(n for n, line in enumerate(lines) if line.split()[-1:] == [
        "filament_retraction_length"])
    assert lines[at].split()[:4] == ["Retraction", "length", "not", "set"]
    assert lines[at + 1] == "      the printer's 0.8 mm applies"
    assert all("nil" not in line for line in lines)
    assert all(len(line) <= WIDTH for line in lines)


def test_every_line_here_is_within_100_columns(tmp_path: Path) -> None:
    rig = _plate(tmp_path)
    assert all(len(line) <= WIDTH for text in rig.shown for line in text.splitlines()
               if "/" not in line), "a path may run past it, alone"
