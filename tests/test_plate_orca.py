"""Several parts on one plate with the real Orca (mqucifer/print-shop#109), on the Mac.

Made-up boxes, print-shop's own profiles, a fake printer and broker. What Orca does that
print-shop can't trust is held here too: if one of these changes in a later Orca, it's seen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from print_shop.core.mqtt import gate_topic
from print_shop.plate import BY_LAYER, BY_OBJECT, SEQUENCE
from print_shop.slicer import gcode
from print_shop.slicer.arrange import DoesNotFit, ToPlace, object_file, slice_parts
from print_shop.slicer.clearance import Clearance, Limit, first_hit, runs
from print_shop.slicer.orca import (
    BedType,
    PauseAt,
    PlateObject,
    SlicerProfiles,
    slice_plate,
    with_bed_type,
)
from tests.fakes import STORE_PROFILES
from tests.plates import box
from tests.test_job_e2e import CFG
from tests.test_plate_job import CAP_NAMES, Rig, _until
from tests.test_slicer import needs_orca

pytestmark = [pytest.mark.slicer, needs_orca]


@pytest.fixture
def store(tmp_path: Path) -> SlicerProfiles:
    return with_bed_type(STORE_PROFILES, BedType.TEXTURED_PEI, tmp_path / "bed")


def _set(store: SlicerProfiles, tmp_path: Path, **settings: str) -> SlicerProfiles:
    process = tmp_path / "process.json"
    process.write_text(json.dumps({**json.loads(store.process.read_text()),
                                   SEQUENCE: BY_OBJECT, **settings}))
    return store.model_copy(update={"process": process})


def _parts(tmp_path: Path, *sizes: tuple[float, float]) -> tuple[ToPlace, ...]:
    return tuple(
        ToPlace(n, f"cap-{n}.stl", box(tmp_path / "parts" / object_file(n, f"cap-{n}.stl"),
                                       side, side, height), height)
        for n, (side, height) in enumerate(sizes, 1)
    )


CAPS = ((18.0, 4.0), (18.0, 6.0), (18.0, 8.0))


def test_three_small_parts_print_by_object_each_with_its_own_outcome(
    tmp_path: Path, store: SlicerProfiles
) -> None:
    """The issue's Done when, to the G-code a printer would be sent."""
    rig = Rig(tmp_path, use=store, orca=slice_plate)
    caps = [box(tmp_path / "caps" / name, 18, 18, 4.0 + 2 * n)
            for n, name in enumerate(CAP_NAMES)]
    with rig.daemon:
        job = rig.gated(caps)
        assert "printed BY OBJECT" in rig.bridge.retained_json(gate_topic(CFG))["plate"]
        result = rig.press("approve", job)
        assert (result.printed, result.error) == (True, None)
        (sent,) = [Path(p) for verb, p in rig.printer.calls if verb == "upload"]
        sliced = gcode.read(sent, keep_moves=True)
        order = runs(sliced)
        assert order is not None
        names = [re.match(r"part(\d)_", run.name).group(1) for run in order]  # type: ignore[union-attr]
        assert sorted(names) == ["1", "2", "3"]
        room = Clearance.of(json.loads(store.machine.read_text()))
        assert room == Clearance(68, 36, 90)
        assert first_hit(sliced, order, room) is None
        assert not list(sent.parent.glob("plate_*.gcode")), "nothing left on another plate"
        row = rig.ledger.job_rows(job)[0]
        assert row.plate is not None and row.reading is not None
        assert row.reading.starts_again() == [r.first for r in order[1:]]

        _until(lambda: all(r.ended_at for r in rig.ledger.rows()), "the end")
        for n, outcome in enumerate(("success", "failed", "success"), 1):
            waiting = job if n == 1 else f"{job}-{n}"
            _until(lambda: rig.card().get("job_id") == waiting, "the question")  # noqa: B023
            rig.answer(outcome, "stringing" if outcome == "failed" else "none")
    assert [r.result for r in rig.ledger.rows()] == ["success", "failed", "success"]


@pytest.mark.parametrize("settings", [
    {"brim_type": "outer_only", "brim_width": "5"},
    {"raft_layers": "2"},
    {"skirt_loops": "2", "skirt_distance": "3"},
])
def test_what_orca_doesnt_space_for_is_spaced_for(
    tmp_path: Path, store: SlicerProfiles, settings: dict[str, str]
) -> None:
    result, plate = slice_parts(_parts(tmp_path, *CAPS), _set(store, tmp_path, **settings),
                                tmp_path / "out", slice_plate)
    assert plate.sequence == BY_OBJECT
    sliced = gcode.read(result.gcode_path, keep_moves=True)
    order = runs(sliced)
    assert order is not None and first_hit(sliced, order, Clearance(68, 36, 90)) is None


def test_orcas_own_spacing_lets_a_brim_inside_the_radius(
    tmp_path: Path, store: SlicerProfiles
) -> None:
    profiles = _set(store, tmp_path, brim_type="outer_only", brim_width="5")
    assert profiles.filament is not None
    done = slice_plate([PlateObject(stl=p.stl) for p in _parts(tmp_path, *CAPS)],
                       profiles.machine, profiles.process, [profiles.filament], tmp_path / "out")
    assert done.gcode is not None and done.exit == 0, "Orca says nothing"
    sliced = gcode.read(done.gcode, keep_moves=True)
    order = runs(sliced)
    assert order is not None
    hit = first_hit(sliced, order, Clearance(65, 36, 90))
    assert hit is not None and hit.limit is Limit.HEAD


def test_orca_writes_no_pause_by_object(tmp_path: Path, store: SlicerProfiles) -> None:
    profiles = _set(store, tmp_path)
    assert profiles.filament is not None
    done = slice_plate([PlateObject(stl=p.stl) for p in _parts(tmp_path, *CAPS)],
                       profiles.machine, profiles.process, [profiles.filament], tmp_path / "out",
                       [PauseAt(print_z=3.0)])
    assert done.gcode is not None and done.exit == 0, "Orca says nothing"
    body = done.gcode.read_text().split("; CONFIG_BLOCK_START")[0]
    assert "M600" not in body and ";PAUSE_PRINT" not in body


def test_a_pause_prints_by_layer_and_pauses(tmp_path: Path, store: SlicerProfiles) -> None:
    profiles = _set(store, tmp_path).model_copy(update={"pauses": (PauseAt(print_z=3.0),)})
    result, plate = slice_parts(_parts(tmp_path, *CAPS), profiles, tmp_path / "out", slice_plate)
    assert plate.sequence == BY_LAYER
    assert len(gcode.read(result.gcode_path).pauses) == 1


@pytest.mark.parametrize(("sizes", "why"), [
    (((20.0, 40.0),) * 3, "over the 36 mm to the rod"),
    (((20.0, 95.0),) * 2, "over the 90 mm to the lid"),
    (((18.0, 6.0),) * 9, "don't fit on one plate with the 70 mm"),
])
def test_where_the_head_has_no_room_it_prints_by_layer(
    tmp_path: Path, store: SlicerProfiles, sizes: tuple[tuple[float, float], ...], why: str
) -> None:
    result, plate = slice_parts(_parts(tmp_path, *sizes), _set(store, tmp_path),
                                tmp_path / "out", slice_plate)
    assert plate.sequence == BY_LAYER and why in plate.why
    assert len(gcode.read(result.gcode_path).outlines) == len(sizes), "every part, on plate 1"


def test_what_doesnt_fit_one_plate_is_refused(tmp_path: Path, store: SlicerProfiles) -> None:
    with pytest.raises(DoesNotFit):
        slice_parts(_parts(tmp_path, *((120.0, 4.0),) * 5), _set(store, tmp_path),
                    tmp_path / "out", slice_plate)
