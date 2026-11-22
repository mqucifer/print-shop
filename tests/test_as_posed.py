"""A single part prints as it was posed: Orca never turns it on the plate (mqucifer/print-shop#257).

Orca turned one part as freely as several: an 18 x 235 mm bar by 45 degrees, small triangles by a
quarter. Told not to, it refuses what it has no room for as posed, so such a part is offered the
turn that fits at the review, and is turned only when the person takes it. Every part here is
made up. Without the `slicer` marker nothing here runs Orca: `Bed` refuses what has no room and
replays the cube's recording for the rest.
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.core.state import AppliedOrientation, Orientation, SliceResult, turn_words
from print_shop.desk.again import again
from print_shop.desk.page import _pose
from print_shop.ingest.measuring import Measuring
from print_shop.ingest.orientation import candidate_in, candidates
from print_shop.ledger import Ledger, PrintRecord, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon, JobResult
from print_shop.orchestrator.fitting import Fitting, turns, with_turns
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.intake import replay_asker
from print_shop.setup.loop import PrintLoop
from print_shop.setup.offer import ThisPart, offering
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.reading import SeamReading, SliceReading, read_slice
from print_shop.setup.review import (
    Review,
    ReviewRequest,
    cli_reviewer,
    parse_answer,
    render,
    send_back,
)
from print_shop.slicer.orca import (
    BedType,
    DoesntFit,
    SlicerProfiles,
    parse_gcode_header,
    slice_model,
    with_bed_type,
)
from tests.fakes import (
    CI_PROFILES,
    DATA,
    STORE_PROFILES,
    FramePrinter,
    MemoryBridge,
    echo_settings,
)
from tests.test_graph import needs_orca
from tests.test_job_e2e import CFG
from tests.test_print_loop import Model, Person
from tests.test_review import ANSWERS, _model, _questions

VOLUME = (256.0, 256.0, 256.0)
AS_POSED = (0.0, 0.0, 0.0)


def block(tmp_path: Path, wide: float, deep: float, high: float = 5.0) -> Path:
    """A block with a post at its +x +y corner, so which way it was turned can be read."""
    base = trimesh.creation.box(bounds=((-wide / 2, -deep / 2, 0), (wide / 2, deep / 2, high)))
    post = trimesh.creation.box(bounds=(
        (wide / 2 - 6, deep / 2 - 5, high), (wide / 2 - 1, deep / 2 - 1, high + 3)))
    path = tmp_path / f"block-{wide:g}x{deep:g}.stl"
    trimesh.boolean.union([base, post]).export(path)
    return path


def prism(tmp_path: Path, name: str, outline: Sequence[tuple[float, float]]) -> Path:
    """A convex outline, anticlockwise, 3 mm high."""
    n = len(outline)
    faces = [f for i in range(1, n - 1) for f in ((0, i + 1, i), (n, n + i, n + i + 1))]
    for i in range(n):
        j = (i + 1) % n
        faces += [(i, j, n + j), (i, n + j, n + i)]
    path = tmp_path / f"{name}.stl"
    trimesh.Trimesh([(x, y, z) for z in (0.0, 3.0) for x, y in outline], faces).export(path)
    return path


def _margins(x: float, y: float, z: float, process: dict[str, Any]) -> bool:
    """Orca's room on the store's plate, near enough: it keeps about a millimetre each side."""
    return x < 254 and y < 254 and z <= 256


class Bed:
    """A slicer with a plate's room: what `room` says doesn't fit is refused as Orca does."""

    def __init__(self, room: Callable[..., bool] = _margins) -> None:
        self.room = room
        #: The part's file as each slice that was taken had it.
        self.sliced: list[Path] = []

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        mesh = trimesh.load(Path(stl_path), force="mesh")
        assert isinstance(mesh, trimesh.Trimesh)
        if not self.room(*mesh.extents, json.loads(profiles.process.read_text())):
            raise DoesntFit("OrcaSlicer exited with code 206")
        self.sliced.append(Path(stl_path))
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DATA / "recorded" / "cube.gcode", out / "plate_1.gcode")
        echo_settings(out / "plate_1.gcode", profiles)
        return parse_gcode_header(out / "plate_1.gcode")


@dataclass
class Job:
    result: JobResult
    gate: dict[str, Any] | None
    row: Row | None
    printer: FramePrinter
    shown: str


def job(
    tmp_path: Path, stl: Path, *typed: str, slicer: Any = None, as_laid: bool = True,
    reviewer: Any = "cli", proposing: bool = True, loop: PrintLoop | None = None,
    profiles: SlicerProfiles = CI_PROFILES,
) -> Job:
    """The part through the daemon and the real command-line review, approved at the gate."""
    answers = list(typed)
    shown: list[str] = []
    if reviewer == "cli":
        reviewer = cli_reviewer(read=lambda prompt: answers.pop(0), write=shown.append)
    (tmp_path / "work").mkdir(exist_ok=True)
    bridge, printer = MemoryBridge(), FramePrinter()
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        daemon = Daemon(
            cfg=CFG, profiles=profiles, workdir=tmp_path / "work", printer=printer,
            slicer=slicer or Bed(), bridge=lambda cfg: bridge, ledger=ledger, loop=loop,
            proposer=Proposer(ask=_model, ask_questions=_questions) if proposing else None,
            asker=replay_asker(ANSWERS) if proposing else None, reviewer=reviewer,
        )
        with daemon:
            job_id = daemon.submit(stl, as_laid=as_laid)
            gate = daemon.wait_for_gate(job_id, timeout_s=240)
            if gate is not None:
                bridge.press(CFG, "approve")
            result = daemon.wait_for_finish(job_id, timeout_s=60)
        row = ledger.get(job_id)
    assert not answers, f"never typed: {answers}"
    return Job(result, gate, row, printer, "\n".join(shown))


# Orca is told
# --------------------------------------------------------------------------


def _fake_orca(tmp_path: Path, exits: int) -> tuple[Path, Path]:
    told = tmp_path / "told.txt"
    fake = tmp_path / "orca"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{told}"\n'
        'while [ "$#" -gt 0 ]; do case "$1" in --outputdir) out="$2";; esac; shift; done\n'
        f'cp "{DATA / "recorded" / "cube.gcode"}" "$out/plate_1.gcode"\n'
        f"exit {exits}\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake, told


def test_one_part_is_sliced_with_no_rotation(tmp_path: Path) -> None:
    fake, told = _fake_orca(tmp_path, 0)
    result = slice_model(block(tmp_path, 40, 20), CI_PROFILES, tmp_path / "out", binary=fake)
    args = told.read_text().split("\n")
    assert "--allow-rotations=0" in args
    assert args.index("--allow-rotations=0") < args.index("--slice")
    assert result.turn_deg == 0


@pytest.mark.parametrize(("exits", "no_room"), [(206, True), (156, False), (239, False)])
def test_only_orcas_no_room_is_a_part_that_doesnt_fit(
    tmp_path: Path, exits: int, no_room: bool
) -> None:
    fake, _ = _fake_orca(tmp_path, exits)
    with pytest.raises(RuntimeError, match=f"code {exits}") as refused:
        slice_model(block(tmp_path, 40, 20), CI_PROFILES, tmp_path / "out", binary=fake)
    assert isinstance(refused.value, DoesntFit) is no_room


# The turn that fits
# --------------------------------------------------------------------------


def test_a_part_that_fits_as_posed_is_sliced_from_its_own_file(tmp_path: Path) -> None:
    bed, stl = Bed(), block(tmp_path, 250, 20)
    assert Fitting(bed, VOLUME)(stl, CI_PROFILES, tmp_path / "out").turn_deg == 0
    assert bed.sliced == [stl]


@pytest.mark.parametrize("long", [255.0, 300.0])
def test_what_orca_refuses_is_turned_to_fit_and_says_so(tmp_path: Path, long: float) -> None:
    bed, stl = Bed(), block(tmp_path, long, 20)
    assert Fitting(bed, VOLUME)(stl, CI_PROFILES, tmp_path / "out").turn_deg == 45
    (turned,) = bed.sliced
    mesh = trimesh.load(turned, force="mesh")
    assert isinstance(mesh, trimesh.Trimesh)
    post = mesh.vertices[np.isclose(mesh.vertices[:, 2], 8.0, atol=1e-3)].mean(axis=0)[:2]
    was = np.array([long / 2 - 3.5, 7.0])
    c = np.sqrt(0.5)
    assert post == pytest.approx((c * (was[0] - was[1]), c * (was[0] + was[1])), abs=0.01), (
        "anticlockwise, seen from above"
    )


def test_a_quarter_is_tried_before_a_diagonal(tmp_path: Path) -> None:
    def oblong(x: float, y: float, z: float, process: dict[str, Any]) -> bool:
        return x <= 300 and y <= 200

    fitting = Fitting(Bed(oblong), (300.0, 200.0, 256.0))
    assert fitting(block(tmp_path, 20, 220), CI_PROFILES, tmp_path / "out").turn_deg == 90


def test_a_turn_between_the_plain_ones_is_found(tmp_path: Path) -> None:
    found = turns(block(tmp_path, 305, 20), (300.0, 100.0, 256.0))
    assert found, "no plain turn fits it, and one between does"
    assert all(d % 45 for d in found)
    assert found[1] == found[0] + 180, "and its opposite, for the excluded corner"


def test_no_turn_about_z_mends_a_part_too_tall_or_too_big(tmp_path: Path) -> None:
    assert turns(block(tmp_path, 20, 20, 260), VOLUME) == ()
    assert turns(block(tmp_path, 380, 30), VOLUME) == ()
    with pytest.raises(DoesntFit, match="no turn on the plate"):
        Fitting(Bed(), VOLUME)(block(tmp_path, 380, 30), CI_PROFILES, tmp_path / "out")


def test_a_failure_that_isnt_room_is_never_met_with_a_turn(tmp_path: Path) -> None:
    calls: list[Path | str] = []

    def broken(
        stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        calls.append(stl_path)
        raise RuntimeError("OrcaSlicer exited with code 156")

    with pytest.raises(RuntimeError, match="156"):
        Fitting(broken, VOLUME)(block(tmp_path, 40, 20), CI_PROFILES, tmp_path / "out")
    assert len(calls) == 1


def test_a_pose_that_fits_as_posed_is_never_passed_over_for_one_to_turn(tmp_path: Path) -> None:
    stl = block(tmp_path, 300, 20)
    found = tuple(candidates(stl))
    tall = (256.0, 256.0, 320.0)  # stood on its end, it fits as posed
    assert not any(c.fits_turned for c in with_turns(stl, found, tall, None))
    kept = with_turns(stl, found, tall, AS_POSED)
    assert [c.rotation_deg for c in kept if c.fits_turned] == [AS_POSED], "the pose it's kept in"
    none_fits = with_turns(stl, found, VOLUME, None)
    assert {c.rotation_deg for c in none_fits if c.fits_turned} == {
        c.rotation_deg for c in found if c.height_mm < 256
    }


@pytest.mark.parametrize(("deg", "said"), [
    (45, "45° anticlockwise, seen from above"), (315, "45° clockwise, seen from above"),
    (90, "90° anticlockwise, seen from above"), (180, "180°"),
])
def test_a_turn_is_said_as_a_person_makes_it(deg: float, said: str) -> None:
    assert turn_words(deg) == said


# Offered at the review, and taken or not
# --------------------------------------------------------------------------


@pytest.mark.parametrize("as_laid", [True, False])
@pytest.mark.parametrize(
    "long", [255.0, 300.0], ids=["inside the plate, outside Orca's margins", "over the plate"]
)
def test_a_part_that_fits_only_turned_says_so_and_the_turn_taken_is_recorded_and_shown(
    tmp_path: Path, long: float, as_laid: bool
) -> None:
    done = job(tmp_path, block(tmp_path, long, 20), "ok", "turn", "ok", as_laid=as_laid)
    assert done.result.printed, done.result.error

    offered = done.shown
    assert f"DOESN'T FIT AS POSED ({long:.1f} x 20.0 mm)" in offered
    assert "It fits turned 45° anticlockwise, seen from above, with the same side down." in offered
    assert ("What's shown here is already sliced that way: the time, x, y and the seam's \"back\" "
            "are the turned part's. Nothing prints that way unless you type turn. For another "
            "side down, type orientation.") in offered
    assert "Type turn to take it turned 45° anticlockwise" in offered
    assert "Type ok to go on" not in offered, "ok isn't offered before the turn is taken"
    assert ("it doesn't fit as posed: type turn to take it turned 45° anticlockwise, seen from "
            "above, orientation for another side down, or stop") in offered, "and is refused"
    assert "Taken: turned 45° anticlockwise" in offered

    assert done.gate is not None
    assert done.gate["orientation_deg"] == [0.0, 0.0, 45.0]
    assert done.gate["orientation_rationale"].endswith(
        "turned 45° anticlockwise, seen from above, on the plate to fit, taken by you at the "
        "review"
    )
    row = done.row
    assert row is not None
    assert row.rotation_deg == (0.0, 0.0, 45.0)
    assert row.orientation_rationale == done.gate["orientation_rationale"]
    assert row.reading is not None and row.reading.turn_deg == 45
    assert row.corrections == (), "a turn to fit isn't a pose corrected, so no lesson is drawn"
    assert "turned         45° anticlockwise, seen from above" in describe(row)
    assert "taken by you at the review" in _pose(row)
    notes = " ".join(again(row, None).notes)
    assert "It was turned 45° anticlockwise" in notes and "type turn at the review" in notes


def test_a_part_that_fits_as_posed_is_neither_turned_nor_offered_a_turn(tmp_path: Path) -> None:
    bed = Bed()
    stl = block(tmp_path, 250, 20)
    done = job(tmp_path, stl, "turn", "ok", slicer=bed)
    assert done.result.printed, done.result.error
    assert "DOESN'T FIT" not in done.shown and "TURNED ON THE PLATE" not in done.shown
    assert "it fits as posed, so there's no turn to take" in done.shown
    assert "Type ok to go on to the approval gate" in done.shown
    assert set(bed.sliced) == {stl}
    assert done.gate is not None and done.gate["orientation_deg"] == [0.0, 0.0, 0.0]
    assert done.row is not None and done.row.rotation_deg == AS_POSED
    assert done.row.reading is not None and done.row.reading.turn_deg == 0
    assert "turned " not in describe(done.row)


def test_stopped_at_the_offer_nothing_is_turned_or_sent(tmp_path: Path) -> None:
    done = job(tmp_path, block(tmp_path, 300, 20), "stop")
    assert "stopped at the settings review" in str(done.result.error)
    assert done.gate is None and done.row is None and done.printer.calls == []


def test_an_approval_without_the_turn_taken_never_reaches_the_gate(tmp_path: Path) -> None:
    done = job(tmp_path, block(tmp_path, 300, 20), reviewer=lambda r: Review(approved=True))
    assert "approved without taking the turn" in str(done.result.error)
    assert done.gate is None and done.row is None and done.printer.calls == []


@pytest.mark.parametrize("proposing", [True, False])
def test_with_no_review_to_offer_it_at_nothing_is_turned(tmp_path: Path, proposing: bool) -> None:
    done = job(tmp_path, block(tmp_path, 300, 20), reviewer=None, proposing=proposing)
    assert "which is taken at the review, and this job has none" in str(done.result.error)
    assert done.gate is None and done.printer.calls == []


def test_a_reviewer_that_returns_at_the_turn_is_shown_it_taken(tmp_path: Path) -> None:
    seen: list[ReviewRequest] = []
    answers = ["turn", "ok"]

    def review(request: ReviewRequest) -> Review:
        seen.append(request)
        said = parse_answer(answers.pop(0), request)
        assert isinstance(said, Review), said
        return said

    done = job(tmp_path, block(tmp_path, 300, 20), reviewer=review)
    assert done.result.printed, done.result.error
    assert [r.turn_waiting for r in seen] == [True, False]
    assert seen[0].slice.gcode_path == seen[1].slice.gcode_path, "nothing is sliced again for it"


def test_a_turn_taken_stands_through_a_correction_that_needs_the_same(tmp_path: Path) -> None:
    done = job(tmp_path, block(tmp_path, 300, 20), "turn", "wall_loops=4", "ok")
    assert done.result.printed, done.result.error
    assert "TURNED ON THE PLATE to fit, as you took it: turned 45°" in done.shown
    assert done.row is not None and done.row.rotation_deg == (0.0, 0.0, 45.0)


def test_a_correction_that_needs_another_turn_is_offered_that_one(tmp_path: Path) -> None:
    def room(x: float, y: float, z: float, process: dict[str, Any]) -> bool:
        if not _margins(x, y, z, process):
            return False
        # A setting that takes room: the 45 degree diagonal no longer fits.
        return process.get("wall_loops") != "7" or abs(x - y) > 1

    done = job(tmp_path, block(tmp_path, 255, 20), "turn", "wall_loops=7", "ok", "turn", "ok",
               slicer=Bed(room))
    assert done.result.printed, done.result.error
    assert done.shown.count("DOESN'T FIT AS POSED") == 2, "the new turn is its own offer"
    assert "TURNED ON THE PLATE" not in done.shown, "45 taken isn't this angle taken"
    assert "it doesn't fit as posed: type turn" in done.shown, "and ok waits again"
    assert done.row is not None
    assert done.row.rotation_deg[2] not in (0.0, 45.0)
    assert done.row.reading is not None
    assert done.row.reading.turn_deg == done.row.rotation_deg[2]


def test_a_correction_that_makes_room_turns_nothing(tmp_path: Path) -> None:
    def room(x: float, y: float, z: float, process: dict[str, Any]) -> bool:
        return process.get("wall_loops") == "7" or _margins(x, y, z, process)

    done = job(tmp_path, block(tmp_path, 255, 20), "turn", "wall_loops=7", "ok",
               slicer=Bed(room))
    assert done.result.printed, done.result.error
    assert done.row is not None and done.row.rotation_deg == AS_POSED
    assert "taken by you" not in done.row.orientation_rationale


def test_a_pose_sent_back_takes_no_turn_with_it(tmp_path: Path) -> None:
    done = job(tmp_path, block(tmp_path, 255, 20),
               "turn", "orientation=1 because the post is seen", "ok", "turn", "ok")
    assert done.result.printed, done.result.error
    assert done.shown.count("DOESN'T FIT AS POSED") == 2, "the other pose's turn is its own offer"
    assert "TURNED ON THE PLATE" not in done.shown, "the same angle, but another side down"
    assert "it doesn't fit as posed: type turn" in done.shown, "and ok waits again"
    assert done.row is not None and done.row.rotation_deg == (180.0, 0.0, 45.0)


def test_a_side_no_turn_fits_isnt_offered_to_send_it_back_to(tmp_path: Path) -> None:
    # The fifth other side stands it 300 mm tall: sliced, it ended the job with the answers lost.
    done = job(tmp_path, block(tmp_path, 300, 20),
               "orientation=5 because the top is seen", "back", "turn", "ok")
    assert done.result.printed, done.result.error
    asked = done.shown.split("Which pose should it print in?")[1].split("Back to the review")[0]
    listed, left_out = asked.split("Left out")
    assert "180,0,0" in listed and "90,0,0" in listed
    assert "0,90,0" not in listed and "0,270,0" not in listed
    assert "0,90,0; 0,270,0" in left_out


def test_with_no_other_side_that_fits_orientation_says_so_and_isnt_pointed_to() -> None:
    here = Orientation(overhang_area_mm2=0, bed_contact_area_mm2=1, height_mm=5, fits_turned=True)
    tall = here.model_copy(update={"rotation_deg": (0.0, 90.0, 0.0), "fits_turned": False,
                                   "fits_no_way": True})
    request = ReviewRequest(
        presets=("machine", "process", "filament"), proposal=Proposal(),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=25, max_z_mm=5, filament_mm=1,
                          filament_cm3=1, filament_g=1, print_time_s=60, turn_deg=45.0),
        orientation=AppliedOrientation(), profiles=CI_PROFILES, candidates=(here, tall),
    )
    assert "For another side down" not in render(request)
    assert "(doesn't fit, however it's turned on the plate)" in render(request), "once measured"
    assert "orientation for another side" not in str(parse_answer("ok", request))
    said: list[str] = []
    assert send_back(request, lambda prompt: pytest.fail("nothing to pick"), said.append) is None
    assert "No other side down fits the plate" in "\n".join(said)


@pytest.mark.parametrize(("wide", "deep"), [(300, 20), (20, 300), (40, 20)])
def test_a_pose_measured_beside_is_marked_as_it_is_among_all_six(
    tmp_path: Path, wide: float, deep: float
) -> None:
    stl = block(tmp_path, wide, deep)
    six = with_turns(stl, tuple(candidates(stl)), VOLUME, AS_POSED)
    beside = Measuring(stl, six[:1], lambda mesh, pose: with_turns(mesh, (pose,), VOLUME, None)[0])
    assert beside.settled() == six


def test_a_kept_pose_that_fits_only_turned_is_still_measured_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*args: Any) -> Any:
        raise AssertionError("the orient node measured all six")

    monkeypatch.setattr(graph, "candidates", never)
    done = job(tmp_path, block(tmp_path, 300, 20), "turn", "ok")
    assert done.result.printed, done.result.error


def test_orientation_is_pointed_to_while_the_others_are_still_being_measured(
    tmp_path: Path,
) -> None:
    stl = block(tmp_path, 300, 20)
    kept = with_turns(stl, (candidate_in(stl, AS_POSED),), VOLUME, AS_POSED)
    request = ReviewRequest(
        presets=("machine", "process", "filament"), proposal=Proposal(),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=25, max_z_mm=5, filament_mm=1,
                          filament_cm3=1, filament_g=1, print_time_s=60, turn_deg=45.0),
        orientation=AppliedOrientation(), profiles=CI_PROFILES, candidates=kept,
        measuring=Measuring(stl, kept),
    )
    assert "For another side down, type orientation." in render(request)
    assert "orientation for another side down" in str(parse_answer("ok", request))


def test_a_number_typed_ahead_never_picks_another_side_where_some_are_left_out(
    tmp_path: Path,
) -> None:
    # Standing on end is third and fourth here, so a 3 typed ahead would pick the one after.
    done = job(tmp_path, block(tmp_path, 20, 300),
               "orientation=3 because the top is seen", "back", "turn", "ok")
    assert done.result.printed, done.result.error
    assert "Which pose should it print in?" in done.shown, "asked, rather than picked"
    assert done.row is not None and done.row.rotation_deg[:2] == (0.0, 0.0)


def test_the_side_its_context_lays_it_on_is_kept_and_offered_the_turn(tmp_path: Path) -> None:
    stl = block(tmp_path, 300, 20)
    stl.with_name(f"{stl.stem}.part.json").write_text(json.dumps({"down": "+z"}))
    done = job(tmp_path, stl, "turn", "ok", as_laid=False)
    assert done.result.printed, done.result.error
    assert done.row is not None
    assert (done.row.rotation_deg, done.row.orientation_source) == ((180.0, 0.0, 45.0), "context")


def test_the_print_loop_is_told_of_the_turn_and_may_propose_the_pose(tmp_path: Path) -> None:
    pose = list(AS_POSED)
    model = Model(
        [("slice", {"rotation": pose, "settings": []})],
        [("propose", {"rotation": pose, "settings": [], "reasoning": "flat, as it lies"})],
    )
    done = job(tmp_path, block(tmp_path, 300, 20), "turn", "ok", as_laid=False,
               loop=PrintLoop(chat=model, person=Person()), profiles=STORE_PROFILES)
    assert done.result.printed, done.result.error
    said = [m["content"] for m in model.shown[1] if m["role"] == "tool"]
    assert "It doesn't fit as posed: this slice is turned 45 degrees about z" in said[0]
    assert done.row is not None and done.row.rotation_deg == (0.0, 0.0, 45.0)


def test_a_part_no_turn_fits_is_refused_before_anything_is_asked(tmp_path: Path) -> None:
    done = job(tmp_path, block(tmp_path, 380, 30))
    assert "laid as it is" in str(done.result.error)
    assert done.gate is None and done.shown == "" and done.printer.calls == []


def test_the_pose_is_kept_apart_from_the_turn_on_the_plate() -> None:
    applied = AppliedOrientation(rotation_deg=(90.0, 0.0, 0.0), turn_deg=315.0)
    assert applied.as_printed() == (90.0, 0.0, 315.0)
    assert applied.rotation_deg == (90.0, 0.0, 0.0), "what the features and faces are read in"


def test_a_candidate_fits_only_by_the_turn_it_was_found() -> None:
    long = Orientation(overhang_area_mm2=0, bed_contact_area_mm2=1, height_mm=5,
                       bbox_mm=(300.0, 20.0, 5.0))
    assert not long.fits_in(VOLUME)
    assert long.model_copy(update={"fits_turned": True}).fits_in(VOLUME)


# A row from before, and Use these
# --------------------------------------------------------------------------


def _row(
    ledger: Ledger, stl: Path, *, seam: str, turn: float | None,
    rotation: tuple[float, float, float] = AS_POSED,
) -> Row:
    back = Change(key="seam_position", preset="aligned", value=seam, reason="the front is seen",
                  source=Source.SPONSOR)
    ledger.record_start_requested(PrintRecord(
        job_id="before", stl_name=stl.name, stl_sha256="ab" * 32, rotation_deg=rotation,
        orientation_source="as_laid", orientation_rationale="kept as laid",
        orientation_accepted=True, gcode_name="before.gcode", print_time_s=600, filament_cm3=3.0,
        layer_count=100, slicer_config={"filament_type": "PLA"}, printer_machine="made up",
        printer_firmware="0", proposal=Proposal(changes=(back,)), corrections=(back,),
        features=measure(stl, 100.0),
        reading=SliceReading(layers=(), features=(), seam=SeamReading(seams=0, stacked=0.0),
                             settings={"seam_position": seam}, turn_deg=turn),
    ))
    ledger.record_started("before")
    row = ledger.get("before")
    assert row is not None
    return row


def _differs(ledger: Ledger, stl: Path) -> str:
    found = offering(ledger, ThisPart(
        stl=stl, stl_name=stl.name, sha256="ab" * 32, features=measure(stl, 100.0),
        rotation_deg=AS_POSED, height_mm=8.0,
        machine={"min_layer_height": "0.08", "max_layer_height": "0.28"},
        process={"seam_position": "aligned"}, filament={"filament_type": ["PLA"]},
        filament_names=(), candidates=tuple(candidates(stl)), build_volume_mm=VOLUME,
    ))
    return " ".join(found.options[0].differs)


def test_a_reading_from_before_doesnt_say_it_was_as_posed() -> None:
    kept = read_slice(DATA / "recorded" / "cube.gcode").model_dump(mode="json")
    del kept["turn_deg"]
    assert SliceReading.model_validate(kept).turn_deg is None


@pytest.mark.parametrize("seam", ["back", "aligned_back"])
def test_a_row_from_before_is_read_as_it_was_sliced(tmp_path: Path, seam: str) -> None:
    stl = block(tmp_path, 40, 20)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        row = _row(ledger, stl, seam=seam, turn=None)
        assert row.rotation_deg == AS_POSED and row.reading is not None
        assert row.reading.turn_deg is None, "never taken for a part sliced as posed"
        assert "turned " not in describe(row)
        assert "Orca may have turned it on the plate" in " ".join(again(row, None).notes)
        assert "its back may have been another side" in _differs(ledger, stl)


def test_a_row_from_before_with_no_seam_at_the_back_has_no_note(tmp_path: Path) -> None:
    stl = block(tmp_path, 40, 20)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        row = _row(ledger, stl, seam="aligned", turn=None)
        assert "turned" not in " ".join(again(row, None).notes)
        assert "turned" not in _differs(ledger, stl)


def test_a_row_sliced_as_posed_has_no_doubt_of_its_back(tmp_path: Path) -> None:
    stl = block(tmp_path, 40, 20)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        row = _row(ledger, stl, seam="back", turn=0.0)
        assert "turned" not in " ".join(again(row, None).notes)
        assert "turned" not in _differs(ledger, stl)


def test_a_turn_on_the_plate_is_no_other_pose_to_use_these(tmp_path: Path) -> None:
    stl = block(tmp_path, 40, 20)
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        _row(ledger, stl, seam="back", turn=45.0, rotation=(0.0, 0.0, 45.0))
        differs = _differs(ledger, stl)
    assert "printed in the pose" not in differs
    assert "it was turned 45° anticlockwise" in differs


# With real Orca
# --------------------------------------------------------------------------


@pytest.fixture
def pei(tmp_path: Path) -> SlicerProfiles:
    return with_bed_type(STORE_PROFILES, BedType.TEXTURED_PEI, tmp_path / "profiles")


def _rotations_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orca as `slice_model` ran it before."""
    popen = subprocess.Popen

    def before(cmd: list[str], **kwargs: Any) -> Any:
        return popen([c for c in cmd if c != "--allow-rotations=0"], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", before)


def _printed_box(gcode: Path) -> np.ndarray:
    """The size of the box round the second layer's outer wall."""
    points: list[tuple[float, float]] = []
    x = y = 0.0
    layer, kind = 0, ""
    for line in gcode.read_text().splitlines():
        layer += line.startswith(";LAYER_CHANGE")
        if line.startswith(";TYPE:"):
            kind = line[6:]
        elif re.match(r"G[0-3] ", line):
            to = {w[0]: w[1:] for w in line.split(";")[0].split()[1:]}
            was = (x, y)
            x, y = float(to.get("X", x)), float(to.get("Y", y))
            if layer == 2 and kind == "Outer wall" and float(to.get("E", 0)) > 0:
                points += [was, (x, y)]
    assert points, "no outer wall was read"
    size: np.ndarray = np.ptp(np.array(points), axis=0)
    return size


def _as_drawn(stl: Path, gcode: Path) -> bool:
    mesh = trimesh.load(stl, force="mesh")
    assert isinstance(mesh, trimesh.Trimesh)
    # A line's width, and what a sharp corner loses to it.
    return bool(np.allclose(_printed_box(gcode), mesh.extents[:2], rtol=0, atol=2.0))


#: The made-up parts real Orca turned, left to itself.
TURNED: dict[str, Callable[[Path], Path]] = {
    "18 x 235": lambda d: block(d, 18, 235),
    "triangle": lambda d: prism(d, "triangle", ((0, 0), (30, 0), (5, 18))),
    "right triangle": lambda d: prism(d, "right", ((0, 0), (22, 0), (0, 14))),
    "pin": lambda d: prism(d, "pin", ((0, 0), (22.5, 13), (19.5, 18.2), (-3, 5.2))),
}


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize("part", TURNED)
def test_the_parts_orca_turned_slice_as_posed(
    part: str, pei: SlicerProfiles, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stl = TURNED[part](tmp_path)
    assert _as_drawn(stl, slice_model(stl, pei, tmp_path / "now").gcode_path)
    _rotations_on(monkeypatch)
    assert not _as_drawn(stl, slice_model(stl, pei, tmp_path / "before").gcode_path), (
        "left to itself Orca turns it, or this proves nothing"
    )


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize(("wide", "deep"), [(40, 20), (235, 18), (200, 200)])
def test_a_part_that_fits_as_posed_and_wasnt_turned_slices_as_it_did(
    wide: float, deep: float, pei: SlicerProfiles, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stl = block(tmp_path, wide, deep)

    def gcode(out: str) -> list[str]:
        lines = slice_model(stl, pei, tmp_path / out).gcode_path.read_text().splitlines()
        return [line for line in lines if "generated by OrcaSlicer" not in line]

    now = gcode("now")
    _rotations_on(monkeypatch)
    assert now == gcode("before"), "every line but the time it was generated"


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize("long", [255.0, 300.0])
def test_a_part_orca_has_no_room_for_as_posed_is_offered_the_turn_it_takes(
    long: float, pei: SlicerProfiles, tmp_path: Path
) -> None:
    stl = block(tmp_path, long, 20)
    with pytest.raises(DoesntFit):
        slice_model(stl, pei, tmp_path / "posed")
    done = job(tmp_path, stl, "ok", "turn", "ok", slicer=slice_model, profiles=pei)
    assert done.result.printed, done.result.error
    assert "DOESN'T FIT AS POSED" in done.shown
    assert done.row is not None and done.row.rotation_deg == (0.0, 0.0, 45.0)
    (gcode,) = (tmp_path / "work" / done.row.job_id).glob("*.gcode")
    diagonal = (long + 20) * np.sqrt(0.5)
    assert _printed_box(gcode) == pytest.approx((diagonal, diagonal), abs=2.0)
