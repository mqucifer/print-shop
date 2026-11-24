"""The head's room, read from the moves, and a plate arranged by it (mqucifer/print-shop#109).

Made-up parts and a made-up head. Orca's exit 0 covers none of this, so each limit is held here
on moves written out by hand, and the arranging on a stand-in Orca that has the real one's gaps.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from print_shop.plate import BY_LAYER, BY_OBJECT, SEQUENCE
from print_shop.setup.reading import LayerTime, SeamReading, SliceReading, flags, read_slice
from print_shop.slicer import gcode
from print_shop.slicer.arrange import DoesNotFit, ToPlace, object_file, slice_parts
from print_shop.slicer.clearance import Clearance, Limit, Run, first_hit, runs
from print_shop.slicer.gcode import Layer, Move, Sliced
from print_shop.slicer.orca import PauseAt, SlicerProfiles
from tests.plates import CLEARANCE, PlateOrca, box, profiles

ROOM = Clearance(radius=68, rod=36, lid=90)


def _square(layer: int, x: float, y: float, z: float, side: float = 10) -> list[Move]:
    a, b = x - side / 2, x + side / 2
    c, d = y - side / 2, y + side / 2
    corners = [(a, c), (b, c), (b, d), (a, d), (a, c)]
    return [Move(layer, *p, z, *q, z, 0.4, True)
            for p, q in zip(corners, corners[1:], strict=False)]


def _two(
    first_height: float, at: tuple[float, float], z: float = 0.4,
) -> tuple[Sliced, tuple[Run, ...]]:
    """A part 10 mm square at 0, 0 and `first_height` tall, then one at `at`, laid at `z`."""
    moves = [*_square(1, 0, 0, 0.2), *_square(2, 0, 0, first_height),
             *_square(3, *at, z)]
    layers = [Layer(1, 0.2, 0.2), Layer(2, first_height, 0.2), Layer(3, z, 0.2)]
    return Sliced(layers, [], [], {}, {}, moves), (Run("first", 1, 2), Run("second", 3, 3))


def test_the_stricter_radius_is_the_one_held_to() -> None:
    assert Clearance.of(CLEARANCE) == ROOM
    assert Clearance.of({**CLEARANCE, "extruder_clearance_max_radius": "60"}) == Clearance(
        65, 36, 90)
    assert Clearance.of({k: [v] for k, v in CLEARANCE.items()}) == ROOM


@pytest.mark.parametrize("missing", [
    "extruder_clearance_radius", "extruder_clearance_height_to_rod",
    "extruder_clearance_height_to_lid",
])
def test_a_machine_that_doesnt_say_its_room_has_none(missing: str) -> None:
    assert Clearance.of({k: v for k, v in CLEARANCE.items() if k != missing}) is None


def test_outside_the_radius_is_clear_and_inside_it_is_a_hit() -> None:
    # 10 mm squares with 0.4 mm lines: centres 78.2 apart are 68 from line's middle to material.
    assert first_hit(*_two(8, (78.3, 0)), ROOM) is None
    hit = first_hit(*_two(8, (77.2, 0)), ROOM)
    assert hit is not None
    assert (hit.limit, hit.passed, hit.height) == (Limit.HEAD, "first", 8)
    assert hit.short == pytest.approx(1.0)


def test_the_radius_is_round_the_head_not_a_square() -> None:
    assert first_hit(*_two(8, (55, 55)), ROOM) is not None  # corner to corner 63.6 mm
    assert first_hit(*_two(8, (62, 62)), ROOM) is None, "52 on each axis is 73.5 corner to corner"


def test_past_the_rods_height_x_is_no_room_and_y_is() -> None:
    assert first_hit(*_two(36, (150, 0)), ROOM) is None, "36 mm is under the rod"
    beside = first_hit(*_two(40, (150, 0)), ROOM)
    assert beside is not None and beside.limit is Limit.ROD, "the rod spans the plate in x"
    assert first_hit(*_two(40, (0, 78.3)), ROOM) is None, "clear of it front to back"


def test_only_the_last_part_may_be_over_the_lid() -> None:
    hit = first_hit(*_two(95, (0, 200)), ROOM)
    assert hit is not None and hit.limit is Limit.LID
    sliced, order = _two(8, (0, 200), z=95)
    assert first_hit(sliced, order, ROOM) is None, "the last is never passed"


def test_above_a_finished_part_is_clear_and_across_it_lower_is_not() -> None:
    sliced, order = _two(8, (120, 0))
    over = Move(2, -50, 0, 8.4, 50, 0, 8.4, 0.0, False)
    sliced.head.insert(8, over)
    assert first_hit(sliced, order, ROOM) is None
    sliced.head[8] = over._replace(z0=7.0, z1=7.0)
    hit = first_hit(sliced, order, ROOM)
    assert hit is not None and hit.limit is Limit.HEAD
    assert hit.short == pytest.approx(68.2), "straight through it"


def test_a_brim_on_the_finished_part_is_no_body() -> None:
    sliced, order = _two(8, (78.3, 0))
    sliced.head[:4] = _square(1, 0, 0, 0.2, side=30)  # its first layer reaches 10 mm further
    assert first_hit(sliced, order, ROOM) is None


def test_what_cant_be_read_is_never_clear() -> None:
    sliced, order = _two(8, (120, 0))
    with pytest.raises(ValueError, match="weren't kept"):
        first_hit(Sliced(sliced.layers, [], [], {}, {}), order, ROOM)
    with pytest.raises(ValueError, match="lays nothing"):
        first_hit(sliced, (*order, Run("ghost", 9, 9)), ROOM)


def test_runs_are_only_parts_printed_one_after_another() -> None:
    def layers(*names: tuple[str, ...]) -> Sliced:
        return Sliced([Layer(n, 0.2, 0.2, objects=dict.fromkeys(here, 5.0))
                       for n, here in enumerate(names, 1)], [], [], {}, {})

    assert runs(layers(("a",), ("a",), ("b",), ("b",))) == (Run("a", 1, 2), Run("b", 3, 4))
    assert runs(layers(("a",), ("a", "b"))) is None, "two on one layer"
    assert runs(layers(("a",), ("b",), ("a",))) is None, "one returned to"
    assert runs(layers((), ())) is None, "none named"


# Arranged
# --------------------------------------------------------------------------


def _parts(folder: Path, *sizes: tuple[float, float]) -> tuple[ToPlace, ...]:
    return tuple(
        ToPlace(n, f"cap-{n}.stl", box(folder / "parts" / object_file(n, f"cap-{n}.stl"),
                                       side, side, height), height)
        for n, (side, height) in enumerate(sizes, 1)
    )


def _wanting(tmp_path: Path, **process: str) -> SlicerProfiles:
    return profiles(tmp_path / "p", {SEQUENCE: BY_OBJECT, **process})


CAPS = ((18.0, 6.0), (18.0, 8.0), (18.0, 10.0))


def test_three_small_parts_print_by_object_spaced_for_the_head(tmp_path: Path) -> None:
    orca = PlateOrca()
    result, plate = slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                                orca)
    assert plate.sequence == BY_OBJECT
    assert [(o.n, o.order, o.layers) for o in plate.objects] == [
        (1, 1, (1, 30)), (2, 2, (31, 70)), (3, 3, (71, 120))]
    assert result.layer_count == 120
    assert float(orca.calls[0]["machine"]["extruder_clearance_radius"]) == 70, "68 and the swing"
    assert "68 mm round it, 36 mm to the rod, 90 mm to the lid" in plate.why


def test_orcas_order_is_kept_apart_from_the_order_given(tmp_path: Path) -> None:
    _, plate = slice_parts(_parts(tmp_path, *CAPS[::-1]), _wanting(tmp_path), tmp_path / "out",
                           PlateOrca())
    assert [(o.n, o.order) for o in plate.objects] == [(1, 3), (2, 2), (3, 1)]


def test_a_brim_orca_doesnt_count_spaces_the_parts_wider(tmp_path: Path) -> None:
    orca = PlateOrca()
    wanted = _wanting(tmp_path, brim_type="outer_only")
    result, plate = slice_parts(_parts(tmp_path, *CAPS), wanted, tmp_path / "out", orca)
    assert plate.sequence == BY_OBJECT
    spacings = [float(c["machine"]["extruder_clearance_radius"]) for c in orca.calls]
    assert len(spacings) == 2 and spacings[1] > spacings[0] == 70
    sliced = gcode.read(result.gcode_path, keep_moves=True)
    order = runs(sliced)
    assert order is not None and first_hit(sliced, order, ROOM) is None


def test_parts_that_spill_to_a_second_plate_print_by_layer(tmp_path: Path) -> None:
    """Orca's exit is 0 and its first plate looks like a job: the parts on the second would be
    lost without a word."""
    parts = _parts(tmp_path, *[(50.0, 8.0)] * 5)
    result, plate = slice_parts(parts, _wanting(tmp_path), tmp_path / "out", PlateOrca())
    assert plate.sequence == BY_LAYER and "don't fit on one plate with the 70 mm" in plate.why
    assert len(plate.objects) == 5 and not (tmp_path / "out" / "plate_2.gcode").exists()
    assert len(gcode.read(result.gcode_path).outlines) == 5


def test_parts_that_dont_fit_by_layer_either_are_refused(tmp_path: Path) -> None:
    with pytest.raises(DoesNotFit, match="don't all fit on one plate, even printed by layer"):
        slice_parts(_parts(tmp_path, *[(120.0, 5.0)] * 5), _wanting(tmp_path), tmp_path / "out",
                    PlateOrca())


def test_a_part_missing_from_the_g_code_is_never_sent(tmp_path: Path) -> None:
    with pytest.raises(DoesNotFit):
        slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                    PlateOrca(leave_out=1))


def test_any_second_plate_is_refused_though_the_first_has_every_part(tmp_path: Path) -> None:
    with pytest.raises(DoesNotFit):
        slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                    PlateOrca(another_plate=True))


def test_a_slice_orca_refuses_both_ways_is_refused_with_both_reasons(tmp_path: Path) -> None:
    with pytest.raises(DoesNotFit, match="by layer either .*by object: Orca didn't slice"):
        slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                    PlateOrca(refuse=(BY_OBJECT, BY_LAYER)))


def test_by_object_refused_by_orca_prints_by_layer_saying_so(tmp_path: Path) -> None:
    _, plate = slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                           PlateOrca(refuse=(BY_OBJECT,)))
    assert plate.sequence == BY_LAYER and "exit 155" in plate.why


def test_a_machine_without_clearances_prints_by_layer(tmp_path: Path) -> None:
    wanted = profiles(tmp_path / "p", {SEQUENCE: BY_OBJECT}, machine={})
    orca = PlateOrca()
    _, plate = slice_parts(_parts(tmp_path, *CAPS), wanted, tmp_path / "out", orca)
    assert plate.sequence == BY_LAYER and "doesn't give the head's clearance" in plate.why
    assert [c["process"][SEQUENCE] for c in orca.calls] == [BY_LAYER], "never tried unchecked"


def test_two_parts_over_the_lid_print_by_layer(tmp_path: Path) -> None:
    _, plate = slice_parts(_parts(tmp_path, (20, 95), (20, 95)), _wanting(tmp_path),
                           tmp_path / "out", PlateOrca())
    assert plate.sequence == BY_LAYER and "over the 90 mm to the lid" in plate.why


def test_a_part_over_the_rod_with_one_beside_it_prints_by_layer(tmp_path: Path) -> None:
    _, plate = slice_parts(_parts(tmp_path, (20, 40), (20, 40)), _wanting(tmp_path),
                           tmp_path / "out", PlateOrca())
    assert plate.sequence == BY_LAYER and "over the 36 mm to the rod" in plate.why


def test_a_pause_prints_by_layer_as_orca_writes_none_by_object(tmp_path: Path) -> None:
    wanted = _wanting(tmp_path).model_copy(update={"pauses": (PauseAt(print_z=4.2),)})
    orca = PlateOrca()
    result, plate = slice_parts(_parts(tmp_path, *CAPS), wanted, tmp_path / "out", orca)
    assert plate.sequence == BY_LAYER and "writes no pause" in plate.why
    assert len(gcode.read(result.gcode_path).pauses) == 1


def test_by_layer_asked_is_by_layer(tmp_path: Path) -> None:
    orca = PlateOrca()
    _, plate = slice_parts(_parts(tmp_path, *CAPS), profiles(tmp_path / "p"), tmp_path / "out",
                           orca)
    assert plate.sequence == BY_LAYER and len(orca.calls) == 1
    assert plate.why == "print_sequence is by layer"


def test_the_objects_marks_are_always_written(tmp_path: Path) -> None:
    orca = PlateOrca()
    slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path, exclude_object="0"),
                tmp_path / "out", orca)
    assert orca.calls[0]["process"]["exclude_object"] == "1"


def test_two_files_of_one_name_are_told_apart() -> None:
    assert object_file(1, "cap v2.1_final.stl") == "part1_cap-v2-1-final.stl"
    assert object_file(2, "cap v2.1_final.stl") == "part2_cap-v2-1-final.stl"
    assert object_file(3, "___.stl") == "part3_part.stl"


# The reading, where z starts again
# --------------------------------------------------------------------------


def test_a_by_object_slice_reads_where_z_starts_again(tmp_path: Path) -> None:
    result, _ = slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                            PlateOrca())
    reading = read_slice(result.gcode_path)
    assert reading.starts_again() == [31, 71]
    assert "by object: z starts again at layers 31, 71" in "\n".join(reading.lines())
    walls = reading.feature("Outer wall")
    assert walls is not None and (walls.from_z, walls.to_z) == (0.2, 10.0)


def test_the_seam_is_compared_only_inside_an_object(tmp_path: Path) -> None:
    """Two parts one after the other: the second's first layer has the plate under it, not the
    first part's top."""
    out = tmp_path / "two.gcode"
    lines = ["M83"]
    for z in (0.2, 0.4, 0.2, 0.4):
        lines += [";LAYER_CHANGE", f";Z:{z}", ";HEIGHT:0.2", ";TYPE:Outer wall", ";WIDTH:0.4",
                  f"G1 X10 Y10 Z{z} F6000", "G1 X20 Y10 E1", f"G1 Z{z + 0.4:.1f}"]
    out.write_text("\n".join(lines) + "\n")
    seam = read_slice(out).seam
    assert (seam.seams, seam.stacked) == (4, 0.5), "of four loops, two have one below them"


def _read(
    *layers: tuple[float, float], ironed: tuple[float, ...] = (), **settings: str,
) -> SliceReading:
    return SliceReading(
        layers=tuple(LayerTime(n=n, z=z, time_s=s) for n, (z, s) in enumerate(layers, 1)),
        features=(), seam=SeamReading(seams=0, stacked=0.0),
        ironed=tuple(LayerTime(n=n, z=z, time_s=1) for n, z in enumerate(ironed, 1)),
        settings=settings,
    )


def test_short_layers_are_said_from_the_lowest_to_the_highest() -> None:
    reading = _read((0.2, 2), (8.0, 2), (0.2, 2), (4.0, 2), slow_down_layer_time="8",
                    slow_down_min_speed="10", slow_down_for_layer_cooling="1")
    [flag] = flags(reading, None, {}, {})
    assert "short layers from z 0.2 to 8 mm" in flag.reading


def test_each_parts_own_top_isnt_a_top_inside_a_part() -> None:
    from print_shop.setup.context import Seen
    from print_shop.setup.reading import FeatureReading

    ironing = FeatureReading(kind="Ironing", time_s=9, area_mm2=90, layers=3, from_z=6, to_z=10)
    reading = _read((0.2, 20), ironed=(6.0, 8.0, 10.0), ironing_type="top").model_copy(
        update={"features": (ironing,)})
    seen = Seen(walls=False, top=True, bottom=False, said="the top")
    assert [f.id for f in flags(reading, seen, {}, {})] == ["ironing_inside"], "one part of 10 mm"
    assert flags(reading, seen, {}, {}, tops=(1, 2, 3)) == (), "three parts' own tops, by layer"


def test_a_floor_at_another_parts_height_is_still_a_floor_inside_its_part() -> None:
    """An 8 mm part's ironed pocket floor at 4 mm, beside a part 4 mm tall: one z, two layers."""
    from print_shop.setup.context import Seen
    from print_shop.setup.reading import FeatureReading

    ironing = FeatureReading(kind="Ironing", time_s=9, area_mm2=90, layers=3, from_z=4, to_z=8)
    reading = _read((0.2, 20), ironed=(4.0, 4.0, 8.0), ironing_type="top").model_copy(
        update={"features": (ironing,)})
    seen = Seen(walls=False, top=True, bottom=False, said="the top")
    [flag] = flags(reading, seen, {}, {}, tops=(1, 3))
    assert flag.id == "ironing_inside" and "z 4 mm" in (flag.change.reason if flag.change else "")


def test_a_part_that_lays_nothing_is_named_not_called_too_many(tmp_path: Path) -> None:
    with pytest.raises(DoesNotFit, match="lays nothing for part 3, cap-3.stl: too thin"):
        slice_parts(_parts(tmp_path, *CAPS), profiles(tmp_path / "p"), tmp_path / "out",
                    PlateOrca(leave_out=1))


def test_each_parts_top_layer_is_the_slices(tmp_path: Path) -> None:
    _, plate = slice_parts(_parts(tmp_path, *CAPS), _wanting(tmp_path), tmp_path / "out",
                           PlateOrca())
    assert [o.top_layer for o in plate.objects] == [30, 70, 120]
    _, plate = slice_parts(_parts(tmp_path, *CAPS), profiles(tmp_path / "p"), tmp_path / "layer",
                           PlateOrca())
    assert [o.top_layer for o in plate.objects] == [30, 40, 50]


def test_a_process_file_is_left_as_it_was(tmp_path: Path) -> None:
    wanted = _wanting(tmp_path)
    before = (wanted.process.read_text(), wanted.machine.read_text())
    slice_parts(_parts(tmp_path, *CAPS), wanted, tmp_path / "out", PlateOrca())
    assert (wanted.process.read_text(), wanted.machine.read_text()) == before
    assert json.loads(before[1])["extruder_clearance_radius"] == "65"
