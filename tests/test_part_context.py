"""A part from a design run carries its fits, seen faces, side and holds to the print side.

mqucifer/print-shop#130. On 2026-11-14 the slice reading proposed a random seam on a part whose top
5 mm is a 0.10 mm fit, which would have scattered seam bumps all round it: it had no way to know
the fit was there. Here a part's context is read from its design run's folder, or from a sidecar
for a part made by hand, and the seam rule, elephant foot and the review read it, whichever path
proposes the settings.

Every run folder and sidecar is made here, in a temporary folder: a lid of our own with a skirt
that fits over a base box with 0.05 to 0.15 mm of play. Nothing under `~/print-shop/` is read,
and nothing reaches LiteLLM, Orca, the printer or the broker.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.core.mqtt import command_topic
from print_shop.ingest.orientation import candidates
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup.bounds import check
from print_shop.setup.context import (
    SIDECAR,
    PartContext,
    place,
    pose_for,
    read_context,
    refuses,
)
from print_shop.setup.intake import Intake, replay_asker
from print_shop.setup.loop import Part, PrintLoop, big
from print_shop.setup.proposal import Change, PartFacts, Proposal, Source, guard
from print_shop.setup.reading import flags
from print_shop.setup.review import Review, ReviewRequest, render
from print_shop.setup.skill import GROUPS, load
from tests.fakes import STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_print_loop import Model, Person
from tests.test_reading import ALL_SEEN, FILAMENT, PETG, PROCESS, _read
from tests.test_review import ANSWERS, _questions
from tests.test_send_back import AnyPart

#: The base box, already printed, as the run has it: 100 x 62 x 45 mm.
BASE = ((-50.0, -31.0, 0.0), (50.0, 31.0, 45.0))
#: The lid, seated on the base: a 5 mm plate on a 5 mm skirt whose inside stands 0.1 mm off the
#: base's walls all round.
LID_OUTER = ((-52.1, -33.1, 40.0), (52.1, 33.1, 50.0))
LID_INSIDE = ((-50.1, -31.1, 39.0), (50.1, 31.1, 45.0))
#: The run's need on the fit, as the CAD loop's intake writes one.
FIT_NEED: dict[str, Any] = {
    "id": "lid-fit", "what": "the lid's skirt slides over the base with a little play",
    "source": "request", "by": "bench",
    "command": "clearance references/base.step lid.step --lift 2 --min-gap 0.05 --max-gap 0.15",
    "number": "least_gap", "at_least": 0.05, "at_most": 0.15,
}
BED_NEED = {"id": "lid-down", "what": "the lid prints on its top", "source": "derived",
            "by": "bed", "file": "lid.step", "side": "+z"}
HOLD_NEED = {"id": "lid-stays", "what": "the lid stays on", "source": "request", "by": "bench",
             "command": "clearance references/base.step lid.step --slide +z",
             "verdicts": ["HELD"]}
#: A need on another part of the run, which this part's context leaves out.
OTHER_NEED = {"id": "tab-fit", "what": "the tab fits", "source": "derived", "by": "bench",
              "command": "clearance tab.step slot.step --max-gap 0.2"}


def _box(bounds: tuple[tuple[float, ...], tuple[float, ...]]) -> trimesh.Trimesh:
    box: trimesh.Trimesh = trimesh.creation.box(bounds=np.asarray(bounds, dtype=float))
    return box


def _lid() -> trimesh.Trimesh:
    lid: trimesh.Trimesh = _box(LID_OUTER).difference(_box(LID_INSIDE), engine="manifold")
    return lid


def _laid(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """The lid as the refinement pass lays it on its +z side: half a turn about x."""
    turned = mesh.copy()
    turned.apply_transform(np.diag([1.0, -1.0, -1.0, 1.0]))  # half a turn about x
    return turned


def _run(
    root: Path, needs: list[dict[str, Any]] | None = None, *, base_mesh: bool = True,
    findings: bool = True, laid: bool = False,
) -> Path:
    """A design run's folder of our own, with the lid meshed by its refinement: the lid's STL."""
    run = root / "eval-lid-9"
    meshing = run / "refinements" / "1" / "meshing"
    meshing.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "stage": "ended", "ending": {"name": "approved"},
        "facts": {
            "references/base.step": {
                "name": "references/base.step", "text": "the base box, already printed",
                "source": "bench info references/base.step", "at_call": 4,
                "box": "[[-50, -31, 0], [50, 31, 45]]", "fixed": True},
            "fan": {"name": "fan", "text": "the fan goes in from +y", "source": "notes",
                    "at_call": 9},
        }}))
    (run / "needs.json").write_text(json.dumps(
        needs if needs is not None else [FIT_NEED, BED_NEED, HOLD_NEED, OTHER_NEED]))
    if findings:
        (run / "refinements" / "1" / "findings.json").write_text(json.dumps([
            {"kind": "over_air", "source": "orca", "read": "Orca lays 2 layers of the plate "
             "over air", "means": "the plate bridges the skirt", "needs_change": False,
             "plate": "lid", "file": "print/lid.step", "z_from": 45.0, "z_to": 45.4},
            {"kind": "gap_fill", "source": "pass", "read": "gap fill in the tab",
             "means": "nothing", "file": "print/tab.step"},
        ]))
    if base_mesh:
        _box(BASE).export(meshing / "references__base.stl")
    _lid().export(meshing / "print__lid.stl")
    if laid:
        (run / "refinements" / "1" / "meshing-laid").mkdir()
        _laid(_lid()).export(run / "refinements" / "1" / "meshing-laid" / "print__lid.stl")
        return run / "refinements" / "1" / "meshing-laid" / "print__lid.stl"
    return meshing / "print__lid.stl"


# --------------------------------------------------------------------------
# Reading it from a design run
# --------------------------------------------------------------------------


def test_a_runs_part_reads_its_needs_facts_and_findings(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    context = read_context(stl)
    assert context.source == "the design run `eval-lid-9`, part `lid`"
    (fit,) = context.fits
    assert (fit.name, fit.mate, fit.play_min, fit.play_max) == (
        "lid-fit", "references/base.step", 0.05, 0.15)
    assert fit.derived.startswith("derived from where this part's box meets its mate's, from "
                                  "refinements/1/meshing/references__base.stl")
    assert fit.box is not None and fit.box[0][2] == 40.0 and fit.box[1][2] == pytest.approx(45.2)
    assert (context.down, context.down_from) == ("+z", "the run's need `lid-down`")
    (hold,) = context.holds
    assert (hold.name, hold.way) == ("lid-stays", "+z")
    # Its own facts and findings, not the run's every one.
    assert context.facts == ("`references/base.step`: box [[-50, -31, 0], [50, 31, 45]]; the "
                             "base box, already printed",)
    (finding,) = context.findings
    assert finding.startswith("over_air: Orca lays 2 layers of the plate over air (z 45-45.4")
    lines = context.lines()
    assert lines[0] == "from the design run `eval-lid-9`, part `lid`"
    assert any(line.endswith("4 settled needs") for line in lines)
    assert any("2 facts, 1 on this part or its mates" in line for line in lines)
    assert any(line.endswith("1 findings on this part") for line in lines)


def test_without_the_mates_mesh_the_band_is_derived_from_its_facts_box(tmp_path: Path) -> None:
    (fit,) = read_context(_run(tmp_path, base_mesh=False)).fits
    assert fit.derived == ("derived from where this part's box meets its mate's, from the fact "
                           "`references/base.step`'s box")
    assert fit.box is not None and fit.box[1][2] == pytest.approx(45.2)


def test_a_needs_own_place_is_the_band(tmp_path: Path) -> None:
    placed = {**FIT_NEED, "command": FIT_NEED["command"] + " --box skirt=-60,-40,41,60,40,44"}
    (fit,) = read_context(_run(tmp_path, [placed])).fits
    assert fit.derived == "derived from the need's place `skirt=-60,-40,41,60,40,44`"
    assert fit.box is not None and (fit.box[0][2], fit.box[1][2]) == (41.0, 44.0)


def test_a_fit_whose_place_cant_be_derived_holds_the_whole_wall(tmp_path: Path) -> None:
    stl = _run(tmp_path, base_mesh=False)
    state = json.loads((stl.parents[3] / "state.json").read_text())
    state["facts"] = {}
    (stl.parents[3] / "state.json").write_text(json.dumps(state))
    (fit,) = read_context(stl).fits
    assert fit.box is None
    assert "the whole wall is held as the fit" in fit.derived
    (band,) = place(read_context(stl), stl, (180.0, 0.0, 0.0)).fits
    assert (band.z_from, band.z_to) == (0.0, 10.0)


def test_room_to_keep_apart_is_no_fit(tmp_path: Path) -> None:
    loose = {**FIT_NEED, "command": "clearance references/base.step lid.step --min-gap 2",
             "number": "", "at_least": None, "at_most": None}
    assert read_context(_run(tmp_path, [loose])).fits == ()


def test_a_part_with_neither_a_run_nor_a_sidecar_says_so(tmp_path: Path) -> None:
    context = read_context(write_stl("cube", tmp_path))
    assert not context.found
    assert context.lines() == ["none: no design run above the file and no sidecar beside it, "
                               "so it's read as before"]
    assert place(context, write_stl("cube", tmp_path), (0.0, 0.0, 0.0)).fits == ()


def test_a_run_with_no_findings_or_no_need_on_the_part_says_so(tmp_path: Path) -> None:
    context = read_context(_run(tmp_path, [OTHER_NEED], findings=False))
    assert context.found and not context.fits
    assert "the run has no refinement findings" in context.notes
    assert "no need of the run names `lid`: its fits, holds and side aren't known" in (
        context.notes)


# --------------------------------------------------------------------------
# Placed in a pose
# --------------------------------------------------------------------------


def test_the_fit_is_placed_in_the_pose_it_prints_in(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    context = read_context(stl)
    # On its top, as the run lays it: the skirt's fit is the top 5 mm as it prints.
    (top,) = place(context, stl, (180.0, 0.0, 0.0)).fits
    assert (top.z_from, top.z_to) == (4.8, 10.0)
    # As the file lies, the skirt is at the plate.
    (bottom,) = place(context, stl, (0.0, 0.0, 0.0)).fits
    assert (bottom.z_from, bottom.z_to) == (0.0, 5.2)


def test_a_file_the_refinement_laid_on_its_side_reads_the_same_band(tmp_path: Path) -> None:
    stl = _run(tmp_path, laid=True)
    context = read_context(stl)
    assert context.down == "-z" and "already laid on it" in context.down_from
    (band,) = place(context, stl, (0.0, 0.0, 0.0)).fits
    assert (band.z_from, band.z_to) == (4.8, 10.0)
    assert context.holds[0].way == "-z"


def test_the_side_down_is_one_of_the_candidate_poses() -> None:
    for side in ("-z", "+z", "-x", "+x", "-y", "+y"):
        assert pose_for(side) is not None, side
    assert pose_for("-z") == (0.0, 0.0, 0.0)
    assert pose_for("+z") == (180.0, 0.0, 0.0)


def test_a_hold_that_slides_up_and_down_as_it_prints_is_across_the_layers(
    tmp_path: Path,
) -> None:
    stl = _run(tmp_path)
    context = read_context(stl)
    assert [h.name for h in place(context, stl, (180.0, 0.0, 0.0)).across] == ["lid-stays"]
    assert place(context, stl, (90.0, 0.0, 0.0)).across == ()


def test_a_pose_off_the_runs_side_is_said(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    context = read_context(stl)
    assert place(context, stl, (180.0, 0.0, 0.0)).off_side == ""
    assert place(context, stl, (0.0, 0.0, 0.0)).off_side == (
        "the pose turns it off its +z side, which the run's need `lid-down` lays it on")


# --------------------------------------------------------------------------
# A sidecar, for a part made by hand
# --------------------------------------------------------------------------


def _sidecar(tmp_path: Path, data: dict[str, Any]) -> Path:
    stl = tmp_path / "lid.stl"
    _laid(_lid()).export(stl)
    (tmp_path / f"lid{SIDECAR}").write_text(json.dumps(data))
    return stl


#: The runbook's sidecar, as it's copied: the lid as the file lies, its top down.
RUNBOOK = {
    "fits": [{"name": "skirt over the base", "mate": "the base box", "play": [0.05, 0.15],
              "z": [-45.2, -40]}],
    "seen": ["walls", "bottom"],
    "down": "-z",
    "holds": [{"name": "snaps on", "way": "-z"}],
}


def test_a_sidecar_beside_a_hand_made_part_gives_its_context(tmp_path: Path) -> None:
    stl = _sidecar(tmp_path, RUNBOOK)
    context = read_context(stl)
    assert context.source == "the sidecar lid.part.json"
    (fit,) = context.fits
    assert (fit.name, fit.mate, fit.play()) == ("skirt over the base", "the base box",
                                                "0.05 to 0.15 mm")
    assert fit.derived == "at z -45.2 to -40 mm as the file lies, as the sidecar gives it"
    assert context.seen is not None and (context.seen.walls, context.seen.top) == (True, False)
    assert context.down == "-z" and context.down_from == "the sidecar"
    placed = place(context, stl, (0.0, 0.0, 0.0))
    (band,) = placed.fits
    assert (band.z_from, band.z_to) == (4.8, 10.0)
    assert [h.name for h in placed.across] == ["snaps on"]


def test_the_sidecars_seen_faces_turn_with_the_pose(tmp_path: Path) -> None:
    stl = _sidecar(tmp_path, RUNBOOK)
    context = read_context(stl)
    over = place(context, stl, (180.0, 0.0, 0.0)).seen
    assert over is not None and (over.top, over.bottom) == (True, False)
    side = place(context, stl, (90.0, 0.0, 0.0)).seen
    assert side is not None and (side.walls, side.top, side.bottom) == (True, True, True)


def test_the_runbooks_sidecar_reads_as_it_says(tmp_path: Path) -> None:
    """The runbook's sidecar is copied as it stands, so it's read here as it stands."""
    runbook = (Path(__file__).parents[1] / "docs" / "runbook-first-print.md").read_text()
    section = runbook.split("**A part's context goes with it**", 1)[1]
    block = section.split("```json", 1)[1].split("```", 1)[0]
    context = read_context(_sidecar(tmp_path, json.loads(block)))
    assert context.found and not context.notes
    (fit,) = context.fits
    assert (fit.play(), fit.derived) == (
        "0.05 to 0.15 mm", "at z 5 to 10 mm as the file lies, as the sidecar gives it")
    assert context.seen is not None and context.seen.top and context.down == "-z"
    assert [(h.name, h.way) for h in context.holds] == [("snaps on", "+z")]


def test_a_sidecar_named_on_the_command_line_wins(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    named = tmp_path / "mine.json"
    named.write_text(json.dumps({"fits": [{"name": "by hand", "play": 0.1}]}))
    context = read_context(stl, named)
    assert context.source == "the sidecar mine.json"
    assert context.fits[0].derived == ("the sidecar gives no place, so the whole wall is held "
                                       "as the fit")


@pytest.mark.parametrize("data", [
    {"fits": [{"play": 0.1}], "colour": "red"},
    {"down": "sideways"},
    {"seen": ["inside"]},
    {"fits": [{"box": [1, 2, 3]}]},
])
def test_a_sidecar_that_cant_be_read_says_why_and_the_print_goes_on(
    tmp_path: Path, data: dict[str, Any],
) -> None:
    context = read_context(_sidecar(tmp_path, data))
    assert not context.found
    (note,) = context.notes
    assert "couldn't be read as a part's context" in note


# --------------------------------------------------------------------------
# The seam, elephant foot and holds, read against the fit
# --------------------------------------------------------------------------


def test_a_fit_band_near_the_top_gets_no_random_seam_and_the_reason_names_the_fit(
    tmp_path: Path,
) -> None:
    """The criteria's replay: on 2026-11-14 a seam at the back on seen walls was proposed random,
    across a lid's top 5 mm fit."""
    stl = _run(tmp_path)
    placed = place(read_context(stl), stl, (180.0, 0.0, 0.0))
    reading = _read(tmp_path, layers=50, config=PETG | {"seam_position": "back"})
    found = flags(reading, ALL_SEEN, PROCESS, FILAMENT, placed)
    assert not [f for f in found if f.change is not None and f.change.value == "random"]
    (seam,) = [f for f in found if f.id == "seam_fit"]
    assert seam.change is None
    assert "random isn't proposed: it would scatter seam bumps all round the fit" in seam.reading
    assert ("z 4.8 to 10 mm as it prints: a 0.05 to 0.15 mm fit with references/base.step "
            "(`lid-fit`)") in seam.reading
    # Without the context, the same slice proposed random.
    (before,) = [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT) if f.id == "seam_seen"]
    assert before.change is not None and before.change.value == "random"


def test_a_random_seam_on_a_part_with_a_fit_is_proposed_back_to_a_line(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    placed = place(read_context(stl), stl, (180.0, 0.0, 0.0))
    reading = _read(tmp_path, layers=50, seam_stays=False,
                    config=PETG | {"seam_position": "random"})
    (seam,) = [f for f in flags(reading, ALL_SEEN, PROCESS, FILAMENT, placed)
               if f.id == "seam_fit"]
    assert seam.change is not None
    assert (seam.change.key, seam.change.value) == ("seam_position", "back")
    assert seam.change.reason.startswith("a random seam scatters seam bumps all round the fit, "
                                         "z 4.8 to 10 mm as it prints")
    hidden = [f for f in flags(reading, None, PROCESS, FILAMENT, placed) if f.id == "seam_fit"]
    assert hidden[0].change is not None and hidden[0].change.value == "aligned"


def test_an_aligned_seam_on_seen_walls_with_a_fit_goes_back_not_random(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    placed = place(read_context(stl), stl, (180.0, 0.0, 0.0))
    (seam,) = [f for f in flags(_read(tmp_path, layers=50), ALL_SEEN, PROCESS, FILAMENT, placed)
               if f.id == "seam_fit"]
    assert seam.change is not None and seam.change.value == "back"
    assert "Not random: it would scatter seam bumps all round the fit" in seam.change.reason


def test_a_fit_at_the_plate_proposes_elephant_foot_compensation_not_applied(
    tmp_path: Path,
) -> None:
    stl = _run(tmp_path)
    context = read_context(stl)
    reading = _read(tmp_path, layers=50, config=PETG | {"elefant_foot_compensation": "0"})
    at_plate = flags(reading, ALL_SEEN, PROCESS, FILAMENT, place(context, stl, (0.0, 0.0, 0.0)))
    (foot,) = [f for f in at_plate if f.id == "fit_elephant_foot"]
    assert foot.change is not None
    assert (foot.change.key, foot.change.value, foot.change.source) == (
        "elefant_foot_compensation", "0.1", Source.READING)
    assert "Proposed, not applied" in foot.change.reason
    presets: dict[str, dict[str, Any]] = {"process": {"initial_layer_line_width": "0.5"},
                                          "machine": {}, "filament": {}}
    assert check(foot.change.key, foot.change.value, presets) == ""
    # At the top, or compensated already, nothing is proposed.
    at_top = flags(reading, ALL_SEEN, PROCESS, FILAMENT, place(context, stl, (180.0, 0.0, 0.0)))
    assert not [f for f in at_top if f.id == "fit_elephant_foot"]
    done = _read(tmp_path, layers=50, config=PETG | {"elefant_foot_compensation": "0.15"})
    assert not [f for f in flags(done, ALL_SEEN, PROCESS, FILAMENT,
                                 place(context, stl, (0.0, 0.0, 0.0)))
                if f.id == "fit_elephant_foot"]


def test_elephant_foot_is_held_to_a_narrower_first_layer(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    reading = _read(tmp_path, layers=50, config=PETG | {"elefant_foot_compensation": "0"})
    (foot,) = [f for f in flags(reading, ALL_SEEN, {**PROCESS, "initial_layer_line_width": "0.08"},
                                FILAMENT, place(read_context(stl), stl, (0.0, 0.0, 0.0)))
               if f.id == "fit_elephant_foot"]
    assert foot.change is not None and foot.change.value == "0.08"


def test_a_hold_across_the_layers_is_said_with_no_change(tmp_path: Path) -> None:
    stl = _run(tmp_path)
    found = flags(_read(tmp_path, layers=50), ALL_SEEN, PROCESS, FILAMENT,
                  place(read_context(stl), stl, (180.0, 0.0, 0.0)))
    (hold,) = [f for f in found if f.id == "hold_layers"]
    assert hold.change is None
    assert hold.reading.startswith("a hold with references/base.step (`lid-stays`), sliding "
                                   "along +z: it goes on and off along z as this prints")


def test_the_sidecars_seen_faces_stand_where_the_intake_didnt_say(tmp_path: Path) -> None:
    stl = _sidecar(tmp_path, {"seen": ["bottom"]})
    placed = place(read_context(stl), stl, (0.0, 0.0, 0.0))
    reading = _read(tmp_path, ironed=(20,))
    (ironing,) = [f for f in flags(reading, None, PROCESS, FILAMENT, placed)
                  if f.id.startswith("ironing")]
    assert ironing.id == "ironing_hidden"
    assert ironing.change is not None and "as the part's context says" in ironing.change.reason


# --------------------------------------------------------------------------
# Both paths: the guard, and the print loop's bounds
# --------------------------------------------------------------------------


def _facts(context: PartContext | None) -> PartFacts:
    return PartFacts(intake=Intake(), features=(), machine={}, process={"seam_position": "back"},
                     filament={}, context=context)


def test_a_models_random_seam_on_a_part_with_a_fit_is_refused_naming_the_fit(
    tmp_path: Path,
) -> None:
    context = read_context(_run(tmp_path))
    random = Change(key="seam_position", preset="back", value="random", reason="scatter it",
                    source=Source.MODEL)
    guarded = guard(_facts(context), Proposal(changes=(random,)))
    assert guarded.get("seam_position") is None
    (refused,) = guarded.refused
    assert refused.startswith("seam_position=random: the part has a fit, and a random seam "
                              "scatters seam bumps all round it: a 0.05 to 0.15 mm fit with "
                              "references/base.step (`lid-fit`)")
    # Without a fit it stands; the person's own choice always stands.
    assert guard(_facts(None), Proposal(changes=(random,))).get("seam_position") is not None
    theirs = random.model_copy(update={"source": Source.SPONSOR})
    assert guard(_facts(context), Proposal(changes=(theirs,))).get("seam_position") is not None


def _part(tmp_path: Path, stl: Path, context: PartContext) -> Part:
    return Part(stl=stl, facts=_facts(context), candidates=tuple(candidates(stl)),
                build_volume_mm=(256.0, 256.0, 256.0), skill=load(GROUPS[:1]),
                profiles=STORE_PROFILES, slicer=AnyPart(), workdir=tmp_path / "loop",
                kept=(180.0, 0.0, 0.0), kept_from="the run's need `lid-down`")


def test_the_loops_bounds_refuse_a_random_seam_on_a_fit(tmp_path: Path) -> None:
    from print_shop.setup.loop import _bounded  # noqa: PLC0415 - the loop's own check

    stl = _run(tmp_path)
    part = _part(tmp_path, stl, read_context(stl))
    (why,) = _bounded(part, {"seam_position": "random"})
    assert "a random seam scatters seam bumps all round it" in why
    assert _bounded(part, {"seam_position": "back"}) == []
    assert refuses(None, "seam_position", "random") == ""


def test_the_side_its_context_lays_it_on_isnt_a_big_change_but_the_files_own_is(
    tmp_path: Path,
) -> None:
    stl = _run(tmp_path)
    part = _part(tmp_path, stl, read_context(stl))
    by = {c.rotation_deg: c for c in part.candidates}
    assert big(part, by[(180.0, 0.0, 0.0)], {}, None, None) == []
    found = dict(big(part, by[(0.0, 0.0, 0.0)], {}, None, None))
    assert found["pose 0,0,0"] == ("turn it to 0,0,0 from the pose the run's need `lid-down` "
                                   "lays it in (180,0,0)")
    assert "supports" in found  # skirt down, its plate needs them too


# --------------------------------------------------------------------------
# A job: the single calls and the print loop
# --------------------------------------------------------------------------


def _random_seam(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The settings model as it was on 2026-11-14: a random seam, for the look of the walls."""
    return {"reasoning": "a lid whose walls are seen", "design_notes": [], "changes": [
        {"key": "seam_position", "value": "random", "feature": "purpose",
         "knowledge": "surface", "reason": "scatter the seam over the seen walls"}]}


@pytest.fixture
def job(tmp_path: Path) -> Iterator[Any]:
    """`run(stl, loop=None)` prints a part through the gate, the review answering ok: the
    ledger's row and the review it was shown."""
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        (tmp_path / "work").mkdir()

        def run(stl: Path, loop: PrintLoop | None = None, **submit: Any) -> tuple[Any, Any]:
            shown: list[ReviewRequest] = []

            def review(request: ReviewRequest) -> Review:
                shown.append(request)
                return Review(approved=True)

            bridge = MemoryBridge()
            daemon = Daemon(
                cfg=CFG, profiles=STORE_PROFILES, workdir=tmp_path / "work",
                printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
                ledger=ledger, proposer=Proposer(ask=_random_seam, ask_questions=_questions),
                asker=replay_asker(ANSWERS), reviewer=review, loop=loop,
            )
            with daemon:
                job_id = daemon.submit(stl, **submit)
                daemon.wait_for_gate(job_id, timeout_s=60)
                bridge.deliver(command_topic(CFG), {"decision": "approve"})
                result = daemon.wait_for_finish(job_id, timeout_s=60)
                assert result.printed, result.error
            row = ledger.get(job_id)
            assert row is not None and row.proposal is not None
            return row, shown[-1]

        yield run


def test_a_runs_part_prints_with_its_context_by_the_single_calls(job: Any, tmp_path: Path) -> None:
    row, review = job(_run(tmp_path))
    # The run's side is kept, with no orientation model asked.
    assert row.rotation_deg == (180.0, 0.0, 0.0)
    assert row.orientation_source == "context"
    assert "as the run's need `lid-down` lays it" in row.orientation_rationale
    # The model's random seam is refused, naming the fit.
    assert row.proposal.get("seam_position") is None
    assert any("random seam scatters seam bumps all round it" in r and "`lid-fit`" in r
               for r in row.proposal.refused)
    # The review says what was read, and the band is derived, from which need.
    text = render(review)
    assert "The part's context (its design run, or a sidecar beside it):" in text
    assert "  from the design run `eval-lid-9`, part `lid`" in text
    assert ("fit at z 4.8 to 10 mm as it prints: a 0.05 to 0.15 mm fit with references/base.step "
            "(`lid-fit`), derived from where this part's box meets its mate's") in text
    # The ledger keeps what was read.
    assert row.proposal.context[0] == "from the design run `eval-lid-9`, part `lid`"


def test_a_runs_part_prints_with_its_context_by_the_print_loop(job: Any, tmp_path: Path) -> None:
    model = Model(
        [("propose", {"rotation": [180, 0, 0], "reasoning": "the lid as its run lays it",
                      "settings": [{"key": "seam_position", "value": "random"}]})],
        [("propose", {"rotation": [180, 0, 0], "reasoning": "the lid as its run lays it",
                      "settings": [{"key": "seam_position", "value": "back"}]})],
    )
    person = Person()
    row, review = job(_run(tmp_path), loop=PrintLoop(chat=model, person=person))
    assert person.asked == []  # the run's own side isn't a big change
    assert row.rotation_deg == (180.0, 0.0, 0.0)
    first, second = row.proposal.loop
    assert first.result.startswith("refused, outside their bounds: seam_position=random: the "
                                   "part has a fit")
    assert second.result.startswith("proposed: 180,0,0")
    assert row.proposal.get("seam_position").value == "back"
    # The model was told the context, and the pose kept unless it asks.
    told = model.shown[0][1]["content"]
    assert "The part's context, as the file lies" in told
    assert "The run's need `lid-down` lays it in 180,0,0, which is kept unless you ask" in told
    assert "  from the design run `eval-lid-9`, part `lid`" in render(review)


def test_a_part_with_no_context_says_so_and_prints_as_before(job: Any, tmp_path: Path) -> None:
    row, review = job(write_stl("cube", tmp_path))
    assert row.proposal.context == ("none: no design run above the file and no sidecar beside "
                                    "it, so it's read as before",)
    assert "  none: no design run above the file" in render(review)
    # With no fit, the model's seam stands as it did.
    assert row.proposal.get("seam_position").value == "random"


def test_a_sidecar_named_at_submit_is_read(job: Any, tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    sidecar = tmp_path / "cube-context.json"
    sidecar.write_text(json.dumps({"fits": [{"name": "cube in a pocket", "play": 0.1,
                                             "z": [0, 3]}]}))
    row, review = job(stl, context=sidecar)
    assert row.proposal.context[0] == "from the sidecar cube-context.json"
    assert row.proposal.get("seam_position") is None
    assert any(f.id == "fit_elephant_foot" for f in review.flags)
