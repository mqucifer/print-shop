"""The refinement pass, with no Docker and no Orca (mqucifer/print-shop#51).

What Orca writes is read as Orca 2.4.2 writes it, from G-code made here in its form; the bench and
Orca are fakes (`refine_fakes.py`). `test_refine_orca.py` runs the real ones on the Mac.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from print_shop.refine import approved, proposals
from print_shop.refine.context import Context, hex_of, line_width_of
from print_shop.refine.findings import gap_in_wall, inside, runs_of
from print_shop.refine.refinement import Pass
from print_shop.rounds.box import Box
from print_shop.rounds.run import Refused
from print_shop.slicer import gcode
from print_shop.slicer.orca import PauseAt, pause_items, with_colour
from tests.refine_fakes import FakeBox, FakeOrca, pause_record
from tests.refine_fixtures import make_enclosure_run, make_run

CONTEXT = ["Elegoo PETG Translucent", "grey=Elegoo PLA"]


def _pass(root: Path, record: dict[str, Any], orca: FakeOrca | None = None, **kw: Any) -> Pass:
    folder, ledger = make_run(root, "fx", **kw)
    return _passing(folder, ledger, record, orca)


def _passing(folder: Path, ledger: Any, record: dict[str, Any], orca: FakeOrca | None) -> Pass:
    run = approved.read(folder, ledger)
    return Pass(
        run,
        ledger,
        Context.parse(0.4, CONTEXT),
        box=cast(Box, FakeBox(record)),
        slicer=orca or FakeOrca(),
        orca="OrcaSlicer-2.4.2",
        commit="abc123",
    )


# Orca's G-code and log, read


GCODE = """\
; HEADER_BLOCK_START
; HEADER_BLOCK_END
EXCLUDE_OBJECT_DEFINE NAME=frame.stl_id_0_copy_0 CENTER=20,20 POLYGON=[[0,0],[40,0],[40,40],[0,0]]
M83
;LAYER_CHANGE
;Z:0.2
;HEIGHT:0.2
EXCLUDE_OBJECT_START NAME=frame.stl_id_0_copy_0
;TYPE:Outer wall
;WIDTH:0.5
G1 X0 Y0
G1 X10 Y0 E1
EXCLUDE_OBJECT_END NAME=frame.stl_id_0_copy_0
;LAYER_CHANGE
;Z:0.4
;HEIGHT:0.2
;PAUSE_PRINT
M600
EXCLUDE_OBJECT_START NAME=frame.stl_id_0_copy_0
;TYPE:Bridge
;WIDTH:0.45
G1 X0 Y0
G1 X10 Y0 E1
;HEIGHT:0.4
;TYPE:Gap infill
;WIDTH:0.23
G2 X10 Y10 I0 J5 E0.5
G1 X0 Y10 E-0.8
EXCLUDE_OBJECT_END NAME=frame.stl_id_0_copy_0
T1
; filament used [mm] = 100.5, 20
; filament used [g] = 3.32, 0.5
; estimated printing time (normal mode) = 1h 2m 3s
; CONFIG_BLOCK_START
; layer_height = 0.2
; CONFIG_BLOCK_END
"""


def test_a_slice_is_read_as_orca_wrote_it(tmp_path: Path) -> None:
    path = tmp_path / "plate_1.gcode"
    path.write_text(GCODE)
    sliced = gcode.read(path, keep_lines=True)
    first, second = sliced.layers
    assert (first.n, first.z, first.height, first.bottom) == (1, 0.2, 0.2, 0.0)
    # A thick bridge's own `;HEIGHT:` inside the layer isn't the layer's.
    assert (second.z, second.height) == (0.4, 0.2)
    assert sliced.pauses == [{"before_layer": 2, "z": 0.4, "after_z": 0.2}]
    assert first.lengths == {"Outer wall": 10.0}
    assert second.lengths["Bridge"] == 10.0
    # A half-circle arc of radius 5, as chords; a retraction isn't a line.
    assert second.lengths["Gap infill"] == pytest.approx(np.pi * 5, rel=1e-2)
    assert second.narrowest == {"Bridge": 0.45, "Gap infill": 0.23}
    assert {ln.obj for ln in second.lines} == {"frame.stl_id_0_copy_0"}
    assert sliced.changes == [{"layer": 2, "z": 0.4, "to": 2}]
    assert sliced.outlines["frame.stl_id_0_copy_0"][1] == [40.0, 0.0]
    assert sliced.per_filament("filament used [g]") == [3.32, 0.5]
    assert sliced.time_s == 3723
    assert sliced.footer["layer_height"] == "0.2"


def test_orcas_warnings_are_read_from_its_log_once_each() -> None:
    log = "\n".join(
        [
            "plate 1: found NON_CRITICAL slicing warnings: It seems object tee.stl has floating"
            " cantilever.",
            "plate 1: found NON_CRITICAL slicing warnings: It seems object tee.stl has floating"
            " cantilever.",
            "got warnings: pane.stl is too close to others, and collisions may be caused.",
            "[warning] gcode path conflicts found between pane.stl and frame.stl",
            "[error] got error when validate: Plate 1: Cool Plate does not support filament 1",
        ]
    )
    found = gcode.warnings(log)
    assert [(w.plate, w.level) for w in found] == [
        (1, "non_critical"),
        (None, "warning"),
        (None, "warning"),
        (None, "error"),
    ]
    assert found[0].text.startswith("It seems object tee.stl")
    assert found[3].text == "Plate 1: Cool Plate does not support filament 1"


def test_a_pause_is_given_to_orca_as_its_own_custom_gcode_item() -> None:
    items = pause_items([PauseAt(print_z=2.8, message="set the panes in")])
    assert items == {
        "mode": "SingleExtruder",
        "gcodes": [
            {
                "type": "PausePrint",
                "print_z": 2.8,
                "color": "",
                "extruder": 1,
                "extra": "set the panes in",
            }
        ],
    }


def test_a_filament_gets_its_colour_as_orca_reads_it(tmp_path: Path) -> None:
    source = tmp_path / "f.json"
    source.write_text(json.dumps({"name": "PLA", "filament_type": ["PLA"]}))
    made = with_colour(source, hex_of("amber"), tmp_path / "out" / "f.json")
    assert json.loads(made.read_text())["filament_colour"] == ["#FFB000"]
    assert hex_of("") == hex_of("grey") == "#8C8C8C"
    assert hex_of("not a colour") == "#8C8C8C"


def test_a_filament_is_named_for_every_colour_or_one() -> None:
    tuned = "Elegoo RAPID PETG 240Degree,MaxFanSpeed=50,AuxPartCoolingFan=70"
    context = Context.parse(0.4, ["Elegoo PLA", f"amber={tuned}", tuned])
    assert context.filaments == {"": tuned, "amber": tuned}
    assert Context.parse(0.4, ["grey=Elegoo PLA"]).filaments == {"grey": "Elegoo PLA"}
    with pytest.raises(Exception, match="no filament for blue"):
        Context.parse(0.4, ["grey=Elegoo PLA"]).filament_for("blue")


def test_points_in_an_outline_with_a_hole() -> None:
    ring = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    points = np.array([[1, 1], [5, 5], [11, 5], [9.9, 9.9]])
    assert inside(points, [{"outer": ring, "holes": [hole]}]).tolist() == [True, False, False, True]
    assert runs_of([3, 1, 2, 7, 9, 8, 12]) == [(1, 3), (7, 9), (12, 12)]


# The change a pause that fails needs


@pytest.mark.parametrize(
    ("ceiling", "pane_top", "new", "rise", "room", "after"),
    [
        (2.45, 2.45, 2.6, 0.15, 0.15, 13),  # between layer lines: the next line over the pane
        (2.5, 2.5, 2.6, 0.1, 0.1, 13),  # hanging's reference: the pocket exactly the pane's
        (2.3, 2.4, 2.4, 0.1, 0.0, 12),  # a pane top on a layer line sits flush under the next
    ],
)
def test_the_ceiling_rises_to_the_first_layer_line_over_the_pane(
    ceiling: float, pane_top: float, new: float, rise: float, room: float, after: int
) -> None:
    row = {"pocket_ceiling_z": ceiling, "pane_top_z": pane_top, "pocket_floor_z": 1.5}
    change = proposals.ceiling_change(row, 0.2, 0.2, 4.0)
    assert (change["new_ceiling"], change["rise"], change["room"]) == (new, rise, room)
    assert change["after_layer"] == after
    assert (change["lip"], change["new_lip"]) == (round(4.0 - ceiling, 4), round(4.0 - new, 4))


def test_the_scripts_numbers_are_read_as_params_reads_them() -> None:
    source = "import x\nCEILING = 2.45\nN: int = 3\nTWICE = 1\nTWICE = 2\nNAME = 'a'\nFLAG = True\n"
    assert proposals.numbers_in(source) == {"CEILING": 2.45, "N": 3.0}


# The approved run


def test_only_an_approved_run_is_refined(tmp_path: Path) -> None:
    folder, ledger = make_run(tmp_path, "fx")
    state = json.loads((folder / "state.json").read_text())
    state["ending"] = None
    state["stop"] = {"kind": "met", "n": 1}
    (folder / "state.json").write_text(json.dumps(state))
    with pytest.raises(Refused, match="runs after approval only"):
        approved.read(folder, ledger)


def test_the_approved_folder_is_read_with_each_files_colour_from_done(tmp_path: Path) -> None:
    folder, ledger = make_run(tmp_path, "fx", panes=2)
    run = approved.read(folder, ledger)
    assert (run.approval, run.round, run.script) == (1, 1, "make_frame.py")
    (group,) = run.groups
    assert [(f.name, f.colour) for f in group.files] == [
        ("frame.stl", "grey"),
        ("pane_1.stl", "amber"),
        ("pane_2.stl", "blue"),
    ]
    assert group.about == "Print it, pause, drop the panes in."
    assert run.needs[0].id == "pane-thin"


def test_a_file_changed_since_approval_is_refused(tmp_path: Path) -> None:
    folder, ledger = make_run(tmp_path, "fx")
    target = folder / "approvals" / "1" / "review" / "A-with-pause" / "frame.stl"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(Refused, match="has changed since it was approved"):
        approved.read(folder, ledger)


def test_a_pause_call_gives_its_pocket_and_pane() -> None:
    call = {"argv": ["pause", "frame.step", "--pocket-z", "2.0", "--pane-thickness", "1.0"]}
    assert approved.pause_inputs(call) == {"pocket_z": 2.0, "pane_thickness": 1.0}
    assert approved.pause_inputs({"argv": ["pause", "frame.step"]}) is None


# The pass


def test_a_pause_on_a_layer_line_gives_the_passs_own_print_steps(tmp_path: Path) -> None:
    orca = FakeOrca(closes_at=14)
    p = _pass(tmp_path, pause_record(13, 0.1, 2.6), orca)
    done = p.run()
    kinds = [f.kind for f in done.findings]
    for kind in (
        "pause",
        "pause_placed",
        "over_pane",
        "over_air",
        "gap_fill",
        "orca_warning",
        "time_filament",
    ):
        assert kind in kinds, kind
    assert not any(f.needs_change for f in done.findings)
    # Orca was given the pause before layer 14, and the frame and the pane on plates of their own.
    frame_plate, pane_plate = orca.plates
    assert [p.print_z for p in frame_plate["pauses"]] == [2.8]
    assert [o.stl.name for o in pane_plate["objects"]] == ["A-with-pause__pane_1.stl"]
    placed = next(f for f in done.findings if f.kind == "pause_placed")
    assert placed.numbers["agree"] is True
    over = next(f for f in done.findings if f.kind == "over_pane")
    assert over.numbers["room_mm"] == pytest.approx(0.1)
    assert "Bridge" in over.numbers["labels_mm"]
    (steps,) = done.steps.values()
    assert "pauses after layer 13 (z 2.6)" in steps[-1]
    readme = (p.approved.folder / "review" / "README.md").read_text()
    assert readme.count("<!-- refinement: print steps -->") == 1
    assert "(the model's words)" in readme
    # The ledger has it, keyed to the run, the approval, the decision and the files.
    (row,) = p.ledger.rows("SELECT * FROM refinements")
    assert (row["run"], row["approval"], row["decision"], row["ended"]) == ("fx", 1, 1, "refined")
    files = p.ledger.rows("SELECT file, role, colour FROM refine_files ORDER BY file")
    assert [tuple(r) for r in files] == [
        ("frame.stl", "frame", "grey"),
        ("pane_1.stl", "pane", "amber"),
    ]
    assert len(p.ledger.rows("SELECT * FROM refine_findings")) == len(done.findings)
    assert len(p.ledger.rows("SELECT * FROM refine_slices")) == 2


def test_a_second_pass_replaces_its_steps_in_the_readme(tmp_path: Path) -> None:
    p = _pass(tmp_path, pause_record(13, 0.1, 2.6))
    p.run()
    again = Pass(p.approved, p.ledger, p.context, box=p.box, slicer=FakeOrca(), commit="abc")
    assert again.run().n == 2
    readme = (p.approved.folder / "review" / "README.md").read_text()
    assert readme.count("<!-- refinement: print steps -->") == 1
    assert "refinement pass (2)" in readme


def test_orca_closing_a_layer_later_than_the_bench_is_said_with_both(tmp_path: Path) -> None:
    done = _pass(tmp_path, pause_record(13, 0.1, 2.7), FakeOrca(closes_at=15)).run()
    over = next(f for f in done.findings if f.kind == "over_pane")
    assert over.numbers["room_mm"] == pytest.approx(0.3)
    assert over.numbers["bench_closes_at"] == 14
    assert "closes the pocket 1 layer(s) after the pause" in over.means


def test_a_pane_above_the_pause_is_a_change_and_a_proposal_and_no_steps(tmp_path: Path) -> None:
    done = _pass(
        tmp_path,
        pause_record(12, -0.05, 2.45, "FAIL"),
        FakeOrca(closes_at=13),
        ceiling=2.45,
        pane=0.95,
    ).run()
    pause = next(f for f in done.findings if f.kind == "pause")
    assert pause.needs_change
    over = next(f for f in done.findings if f.kind == "over_pane")
    assert over.needs_change and over.numbers["room_mm"] == pytest.approx(-0.05)
    (made,) = done.proposals
    assert "from z 2.45 to 2.6" in made["change"]
    assert "from 1.55 to 1.4 mm thick" in made["cost"]
    # The fake bench answers `params` with its `pause` record, FAIL, so the check didn't hold.
    assert made["checked"] == "failed"
    assert done.steps == {"A-with-pause": []}
    readme = (done.folder / "README.md").read_text()
    assert "None: a finding below needs the part to change" in readme
    assert "--approve|--reject" in readme


def test_a_plate_orca_refuses_is_a_finding_with_its_reason(tmp_path: Path) -> None:
    done = _pass(tmp_path, pause_record(13, 0.1, 2.6), FakeOrca(exit=195)).run()
    refused = [f for f in done.findings if f.kind == "orca_refused"]
    assert refused and "exit 195" in refused[0].read
    assert done.steps == {"A-with-pause": []}


def test_a_proposal_waits_for_a_signed_decision_once(tmp_path: Path) -> None:
    p = _pass(
        tmp_path,
        pause_record(12, -0.05, 2.45, "FAIL"),
        FakeOrca(closes_at=13),
        ceiling=2.45,
        pane=0.95,
    )
    (made,) = p.run().proposals
    with pytest.raises(Refused):
        proposals.decide(p.ledger, made["id"], "approved", "someone", "")
    with pytest.raises(Refused, match="approved or rejected"):
        proposals.decide(p.ledger, made["id"], "maybe", "sponsor", "")
    proposals.decide(p.ledger, made["id"], "approved", "sponsor", "yes")
    with pytest.raises(Refused, match="already approved by sponsor"):
        proposals.decide(p.ledger, made["id"], "rejected", "stand-in: x", "no")
    # Nothing in the approved folder changed.
    assert approved.read(p.approved.folder, p.ledger).groups[0].files[0].sha256 == (
        p.approved.groups[0].files[0].sha256
    )


# Laid as the run says, and read as its kind (mqucifer/print-shop#66)


def _enclosure(root: Path, on_bed: dict[str, str], *files: str) -> tuple[Pass, FakeOrca]:
    orca = FakeOrca()
    folder, ledger = make_enclosure_run(root, "case", on_bed, files or ("lid.stl",))
    return _passing(folder, ledger, {}, orca), orca


def test_a_lid_stored_lip_down_prints_outside_face_down_as_done_says(tmp_path: Path) -> None:
    """On `eval-lid-1` the pass printed the lid as stored, lip down, with 6.2 m of bridge at the
    plate's underside, where a lid prints with its outside on the bed."""
    p, orca = _enclosure(tmp_path, {"lid.stl": "+z"})
    assert p.approved.groups[0].files[0].on_bed == "+z"
    done = p.run()
    (plate,) = orca.plates
    (laid,) = [f for f in done.findings if f.kind == "on_bed"]
    assert laid.read.startswith("`lid.stl` is laid with its +z side on the bed, as `done` named")
    assert laid.numbers["bed_contact_mm2"] == pytest.approx(1200, abs=0.5), "the plate's top"
    assert laid.numbers["height_mm"] == pytest.approx(6)
    # What Orca was given is the lid turned over: its plate on the bed, its lip up.
    import trimesh

    given: Any = trimesh.load(plate["objects"][0].stl, force="mesh")
    low, high = given.bounds
    assert high[2] - low[2] == pytest.approx(6)
    down = given.face_normals[:, 2] < -0.9999
    at_bed = given.triangles_center[:, 2] < low[2] + 0.01
    assert given.area_faces[down & at_bed].sum() == pytest.approx(1200, abs=0.5)


def test_an_enclosure_is_read_as_its_kind_with_no_frame_panes_or_pause(tmp_path: Path) -> None:
    """On `eval-lid-1` every row spoke of a frame and panes: "no pane sits in the frame, and the
    run never ran `pause`"."""
    p, orca = _enclosure(tmp_path, {"lid.stl": "+z"})
    done = p.run()
    assert not [f for f in done.findings if f.kind in ("pause", "pause_placed", "over_pane")]
    assert not any("frame" in (f.plate or "") for f in done.findings)
    assert [o.stl.name for o in orca.plates[0]["objects"]] == ["A-case__lid.stl"]
    assert orca.plates[0]["pauses"] is None
    (row,) = p.ledger.rows("SELECT file, role FROM refine_files")
    assert tuple(row) == ("lid.stl", "part")
    (steps,) = done.steps.values()
    assert steps == [
        "Print `refinements/1/plates/A-case-blue/A-case-blue.gcode`: `lid.stl` with its +z side "
        "on the bed in blue, about 12 min and 3.3 g by Orca's estimate."
    ]


def test_a_plate_stored_on_its_edge_prints_flat_and_a_side_with_no_flat_is_said(
    tmp_path: Path,
) -> None:
    """On `eval-plug-1` a 2 mm plate stood on its edge, 99 layers, where the run meant it flat."""
    p, orca = _enclosure(tmp_path, {"plate.stl": "-y", "cone.stl": "+z"}, "plate.stl", "cone.stl")
    done = p.run()
    found = {str(f.file).split("/")[-1]: f for f in done.findings if f.kind == "on_bed"}
    assert found["plate.stl"].numbers["height_mm"] == pytest.approx(2)
    assert found["plate.stl"].numbers["bed_contact_mm2"] == pytest.approx(600, abs=0.5)
    cone = found["cone.stl"]
    assert cone.numbers["why"] == "not flat" and cone.numbers["laid"] == "-z"
    assert "touches the bed over 0 mm²" in cone.read
    assert "It prints as stored, its -z side down" in cone.means
    import trimesh

    tall = {
        o.stl.name: float(trimesh.load(o.stl, force="mesh").extents[2])
        for o in orca.plates[0]["objects"]
    }
    assert tall == {"A-case__plate.stl": pytest.approx(2), "A-case__cone.stl": pytest.approx(10)}


def test_a_file_with_no_side_named_prints_as_stored_and_says_so(tmp_path: Path) -> None:
    p, _ = _enclosure(tmp_path, {})
    done = p.run()
    (said,) = [f for f in done.findings if f.kind == "on_bed"]
    assert said.read == "`done` named no side for the bed for `lid.stl`"
    assert said.numbers["laid"] == "-z"


def test_a_given_pane_is_context_for_the_pause_and_never_printed(tmp_path: Path) -> None:
    """On `eval-fit-2` the given pane wasn't in the approved folder, so the pass set no pause."""
    orca = FakeOrca(closes_at=14)
    p = _pass(tmp_path, pause_record(13, 0.1, 2.6), orca, given_pane=True, glass=True)
    done = p.run()
    asked = next(c for c in p.box.calls if c[2:3] == ["pause"])  # type: ignore[attr-defined]
    assert asked[asked.index("--pocket-z") + 1] == "2"
    assert asked[asked.index("--pane-thickness") + 1] == "1"
    pause = next(f for f in done.findings if f.kind == "pause")
    assert "`references/pane_1.stl`, given, as it sits: context only, never printed" in pause.read
    (frame_plate,) = orca.plates
    assert [o.stl.name for o in frame_plate["objects"]] == ["A-with-pause__frame.stl"]
    assert [x.print_z for x in frame_plate["pauses"]] == [2.8]
    over = next(f for f in done.findings if f.kind == "over_pane")
    assert "`pane_1.stl`" in over.read
    rows = p.ledger.rows("SELECT file, role FROM refine_files ORDER BY role")
    assert [tuple(r) for r in rows] == [
        ("references/pane_1.stl", "context"),
        ("frame.stl", "frame"),
    ]
    (steps,) = done.steps.values()
    assert "set the given `references/pane_1.stl` in their pockets" in steps[0]


def test_with_no_pane_file_to_hand_the_pause_is_read_at_glasss_own_figures(
    tmp_path: Path,
) -> None:
    folder, ledger = make_run(tmp_path, "fx", given_pane=True, glass=True)
    shutil.rmtree(folder / "work")
    p = _passing(folder, ledger, pause_record(13, 0.1, 2.6), FakeOrca())
    done = p.run()
    pause = next(f for f in done.findings if f.kind == "pause")
    assert "the frame's `glass` as the run declared it" in pause.read
    assert "the pocket at z 2 and a 1 mm pane" in pause.read


def test_a_frame_paused_on_prints_as_stored_whatever_side_is_named(tmp_path: Path) -> None:
    folder, ledger = make_run(tmp_path, "fx", glass=True)
    review = json.loads(ledger.rows("SELECT review FROM approvals WHERE run='fx'")[0]["review"])
    review["on_bed"] = {"frame.stl": "+z"}
    ledger.change("UPDATE approvals SET review=? WHERE run='fx'", json.dumps(review))
    done = _passing(folder, ledger, pause_record(13, 0.1, 2.6), FakeOrca()).run()
    (said,) = [f for f in done.findings if f.kind == "on_bed" and "frame" in str(f.file)]
    assert said.numbers["why"] == "a frame paused on"
    assert "it prints as stored, its -z side down" in said.means


def test_a_pause_the_bench_fails_is_given_with_its_reason_beside_it(tmp_path: Path) -> None:
    """Read on `eval-fit-2`'s given pane, `pause` failed on 0.5 mm of air over it: the steps give
    Orca's pause, and say so, where they'd said nothing."""
    record = pause_record(13, 0.5, 3.0, verdict="FAIL")
    record["why"] = "opening 1: 0.500 mm above the pane at the pause, over 0.4 mm"
    done = _pass(tmp_path, record, FakeOrca(closes_at=14)).run()
    pause = next(f for f in done.findings if f.kind == "pause")
    assert pause.means.startswith("the bench fails this pause")
    (steps,) = done.steps.values()
    assert steps[-1].endswith(
        "The bench's `pause` fails it: opening 1: 0.500 mm above the pane at the pause, over "
        "0.4 mm."
    )


def test_gap_fill_inside_a_wall_over_many_layers_names_the_walls_width_in_lines() -> None:
    """mqucifer/print-shop#66: on `eval-edges-1` 2 mm walls left 14.3 m of gap fill inside every
    wall, layers 10 to 100: a 2 mm wall isn't a whole number of the profile's lines."""
    rows = [{"layer": n, "z": 0.2 * n, "length_mm": 150.0, "wall_mm": 2.0} for n in range(10, 101)]
    rows += [{"layer": n, "z": 0.2 * n, "length_mm": 9.0, "wall_mm": 2.0} for n in (2, 3, 4)]
    rows += [{"layer": n, "z": 0.2 * n, "length_mm": 9.0, "wall_mm": 6.0} for n in range(110, 130)]
    (found,) = gap_in_wall(
        {"gap_walls": sorted(rows, key=lambda r: r["layer"])},
        "A-box",
        "A/box.step",
        0.45,
        2,
        "the profile's `inner_wall_line_width`",
    )
    assert found.kind == "gap_in_wall" and (found.layer_from, found.layer_to) == (10, 100)
    assert found.means.startswith(
        "2.00 mm wall = 4.4 lines of 0.45 mm (the profile's `inner_wall_line_width`)"
    )
    assert "4 lines are 1.80 mm and 5 are 2.25 mm" in found.means
    assert found.numbers["length_mm"] == pytest.approx(13650)


def test_the_line_a_wall_is_counted_in_is_the_profiles(tmp_path: Path) -> None:
    profile = tmp_path / "process.json"
    profile.write_text(json.dumps({"inner_wall_line_width": "0.45", "wall_loops": "3"}))
    assert line_width_of(profile, 0.4) == (0.45, 3, "the profile's `inner_wall_line_width`")
    profile.write_text(json.dumps({"inner_wall_line_width": "110%"}))
    share = line_width_of(profile, 0.4)
    assert share is not None and share[0] == pytest.approx(0.44) and share[1] == 2
    profile.write_text(json.dumps({"inner_wall_line_width": "0", "line_width": "0.42"}))
    assert line_width_of(profile, 0.4) == (0.42, 2, "the profile's `line_width`")
    profile.write_text(json.dumps({}))
    assert line_width_of(profile, 0.4) is None
