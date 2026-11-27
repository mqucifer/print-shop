"""Specification for a settings study of a part that fits the bed only turned about its up axis
(mqucifer/print-shop#351).

A synthetic bar, 300 x 20 x 10 mm with a 6 mm hole through it at x = 20, is longer than the
store's 256 x 256 mm bed: it fits only turned about its up axis, by `fit-bed`'s own turn
(`bench.footprint`), 35.5 degrees. The pose turns it, centres it on the plate by translation
only, and carries the turn through its regions and its infill angle. A part that fits no pose
is refused before Orca runs. The tests that need Orca are marked `slicer`, and skip without it;
the rest run in CI.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import trimesh

from print_shop.bench.footprint import footprint, hull
from print_shop.bench.printed import Cylinder, Layers, Printed, Region, Settings
from print_shop.ingest.orientation import AXIS_ALIGNED, read_part
from print_shop.slicer import gcode
from print_shop.slicer.orca import DoesntFit
from print_shop.slicer.regions import (
    Bed,
    Pose,
    Project,
    _plate_angle_kept,
    _section_mask,
    degrees_said,
    fit_of,
    slice_regions,
    turned_to_fit,
    write_project,
)
from print_shop.study.candidate import Candidate, Shaper, study_poses, unfit
from print_shop.study.reading import OrcaSlicing, defaults
from tests.fakes import STORE_MACHINE, STORE_PROFILES
from tests.test_slice_regions import HOLE, _assembly, needs_orca

#: The store's bed, 256 x 256 x 256 mm.
BED = Bed((0.0, 0.0), (256.0, 256.0, 256.0))
#: fit-bed's own turn for the long bar: the least that fits its 300 x 20 mm on the bed.
FIT_TURN = 35.5


def long_bar(folder: Path, length: float = 300.0) -> Path:
    """A bar `length` x 20 x 10 mm, with a 6 mm hole through it along z at `HOLE`."""
    body = trimesh.creation.box((length, 20, 10))
    body.apply_translation((length / 2, 10, 5))
    hole = trimesh.creation.cylinder(radius=3, height=12, sections=48)
    hole.apply_translation((*HOLE, 5))
    part = body.difference(hole)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "bar.stl"
    part.export(path)
    return path


def _box(size: tuple[float, float, float]) -> trimesh.Trimesh:
    box = trimesh.creation.box(size)
    box.apply_translation(np.asarray(size) / 2)
    return cast(trimesh.Trimesh, box)


# --------------------------------------------------------------------------
# fit-bed's own turn, outside the bench's image
# --------------------------------------------------------------------------


def test_the_hull_is_the_outlines_corners_once_each() -> None:
    points = np.array([[0, 0], [2, 0], [2, 1], [0, 1], [1, 0], [1, 0.5], [1, 1], [2, 0]])
    ring = hull(points)
    assert (ring[0] == ring[-1]).all()
    assert sorted(map(tuple, ring[:-1].tolist())) == [(0, 0), (0, 1), (2, 0), (2, 1)]
    assert hull(np.array([[0.0, 0.0], [3.0, 0.0], [1.0, 0.0]])).tolist() == [
        [0, 0], [3, 0], [0, 0]]


def test_a_bar_longer_than_the_bed_fits_turned_by_the_least_turn_that_does() -> None:
    """300 x 20 mm on 256 x 256: 255.85 mm wide turned 35.5 degrees, and too wide at 35.25."""
    points = np.asarray(_box((300, 20, 10)).vertices)[:, :2]
    turn, width, depth = footprint(points, (256.0, 256.0))
    assert turn == FIT_TURN
    assert width == pytest.approx(300 * math.cos(math.radians(35.5)) + 20 * math.sin(
        math.radians(35.5)))
    assert width <= 256 < 300 * math.cos(math.radians(35.25)) + 20 * math.sin(
        math.radians(35.25))
    assert depth < 256
    assert footprint(np.asarray(_box((80, 20, 10)).vertices)[:, :2], (256.0, 256.0))[0] == 0


# --------------------------------------------------------------------------
# The pose, turned about its up axis by any angle, and centred on the plate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("turn", [(0.0, 0.0, 35.5), (180.0, 0.0, 35.5), (90.0, 0.0, 123.4),
                                  (0.0, 270.0, -17.25)])
def test_a_turn_about_the_up_axis_keeps_the_layers_and_turns_the_infill_angle(
    turn: tuple[float, float, float],
) -> None:
    box = _box((10, 20, 30))
    laid = Pose.of(box, (turn[0], turn[1], 0.0))
    pose = Pose.of(box, turn)
    assert pose.layers() == laid.layers() and pose.up() == laid.up()
    for own in (0.0, 30.0, 90.0, 135.0):
        plate, flat = pose.plate_angle(own), laid.plate_angle(own)
        assert plate is not None and flat is not None
        assert plate == pytest.approx((flat + turn[2]) % 360, abs=1e-6)
        assert pose.own_angle(plate) == pytest.approx(own % 360, abs=1e-6)


def test_laid_flat_the_plate_angle_is_the_own_angle_and_the_turn() -> None:
    pose = Pose.of(_box((300, 20, 10)), (0.0, 0.0, FIT_TURN))
    assert pose.plate_angle(0.0) == pytest.approx(35.5)
    assert pose.plate_angle(60.0) == pytest.approx(95.5)


def test_the_pose_centres_the_part_on_the_plate_by_translation_only() -> None:
    box = _box((300, 20, 10))
    pose = Pose.of(box, (0.0, 0.0, FIT_TURN), BED.centre)
    unplaced = Pose.of(box, (0.0, 0.0, FIT_TURN))
    assert np.allclose(pose.matrix[:3, :3], unplaced.matrix[:3, :3])
    posed = box.copy()
    posed.apply_transform(pose.matrix)
    assert posed.bounds.mean(axis=0)[:2] == pytest.approx((128.0, 128.0))
    assert posed.bounds[0][2] == pytest.approx(0.0)
    assert (posed.bounds[0][:2] >= 0).all() and (posed.bounds[1][:2] <= 256).all()
    assert pose.heights(Layers(2.0, 4.0)) == unplaced.heights(Layers(2.0, 4.0))


def test_the_bed_is_the_machine_profiles_printable_area() -> None:
    assert Bed.of(STORE_MACHINE) == BED
    assert BED.centre == (128.0, 128.0)


def test_a_turn_is_said_as_the_option_takes_it() -> None:
    assert degrees_said((0.0, 0.0, 35.5)) == "0,0,35.5"
    assert degrees_said((180.0, -0.0, 12.3456789)) == "180,0,12.345679"


# --------------------------------------------------------------------------
# A pose that doesn't fit, refused before Orca runs
# --------------------------------------------------------------------------


def test_the_long_bar_fits_only_turned_about_its_up_axis(tmp_path: Path) -> None:
    mesh = read_part(long_bar(tmp_path))
    assert not fit_of(mesh, (0.0, 0.0, 0.0), BED).fits
    fit = turned_to_fit(mesh, (0.0, 0.0), BED)
    assert fit.turn == (0.0, 0.0, FIT_TURN) and fit.fits
    assert fit.size[0] <= 256 and fit.size[2] == pytest.approx(10.0)


def test_a_pose_that_doesnt_fit_is_refused_before_orca_runs_naming_the_turn_that_does(
    tmp_path: Path,
) -> None:
    """No Orca is there: had it been run, it would be `FileNotFoundError`."""
    stl = long_bar(tmp_path)
    with pytest.raises(DoesntFit) as found:
        write_project(stl, Printed(Settings()), STORE_PROFILES, tmp_path / "out",
                      (0.0, 0.0, 0.0), binary=tmp_path / "no-orca")
    said = str(found.value)
    assert "turned 0,0,0 it's 300.0 x 20.0 x 10.0 mm, past the bed's 256 x 256 x 256 mm" in said
    assert "fit-bed's own turn it fits, turned 0,0,35.5 it's 255.8 x 190.5 x 10.0 mm" in said
    assert not (tmp_path / "out" / "exported" / "part.3mf").exists()


def test_a_part_that_fits_no_pose_is_refused_naming_its_least_footprint(tmp_path: Path) -> None:
    """400 x 30 x 30 mm: longer than the bed's diagonal laid any way, and taller stood up."""
    mesh = _box((400, 30, 30))
    said = unfit(mesh, (0.0, 0.0, 0.0), BED, "z")
    assert said.startswith("--turn 0,0,0: turned 0,0,0 it's 400.0 x 30.0 x 30.0 mm, past the "
                           "bed's 256 x 256 x 256 mm")
    assert "It fits the bed in no pose, turned any way about its up axis" in said
    assert said.endswith("laid so, its least footprint is turned 0,0,45 it's 304.1 x 304.1 x "
                         "30.0 mm")
    path = tmp_path / "long.stl"
    mesh.export(path)
    with pytest.raises(DoesntFit, match="no turn about its up axis fits it, the least "
                                        "footprint turned 0,0,45"):
        write_project(path, Printed(Settings()), STORE_PROFILES, tmp_path / "out",
                      (0.0, 0.0, 0.0), binary=tmp_path / "no-orca")


def test_a_pose_that_doesnt_fit_says_the_turn_to_give(tmp_path: Path) -> None:
    mesh = read_part(long_bar(tmp_path))
    said = unfit(mesh, (0.0, 0.0, 0.0), BED, "z")
    assert said.endswith("give --turn 0,0,35.5, or 0,0,fit")
    assert unfit(mesh, (0.0, 0.0, FIT_TURN), BED, "z") == ""


def test_a_part_that_fits_only_laid_another_way_says_which(tmp_path: Path) -> None:
    """300 x 200 x 20 mm doesn't fit flat any way, but stood on its edge, 200 mm tall, its
    300 x 20 does, turned."""
    said = unfit(_box((300, 200, 20)), (0.0, 0.0, 0.0), BED, "z")
    assert "no turn about its up axis fits it" in said
    assert "It fits only laid otherwise: turned 90,0,35.5 it's" in said
    assert "its layers along its own y" in said and said.endswith("its layers are along z")


# --------------------------------------------------------------------------
# The study's poses
# --------------------------------------------------------------------------


def test_the_study_offers_each_pose_that_fits_turned_where_it_must(tmp_path: Path) -> None:
    """Flat either way up the long bar fits only turned; stood on its side, its 300 x 10 mm
    fits turned too, and stood on its end it's taller than the bed."""
    mesh = read_part(long_bar(tmp_path))
    poses = study_poses(mesh, (0.0, 0.0, FIT_TURN), BED)
    assert poses[0] == (0.0, 0.0, FIT_TURN)
    assert (180.0, 0.0, FIT_TURN) in poses
    assert all(fit_of(mesh, p, BED).fits for p in poses)
    assert not any(p[:2] in ((0.0, 90.0), (0.0, 270.0)) for p in poses), "300 mm tall"
    small = study_poses(_box((80, 20, 10)), AXIS_ALIGNED[0], BED)
    assert small == AXIS_ALIGNED, "a part that fits as laid is offered the six as before"


def test_a_candidate_takes_a_turned_pose_as_it_was_said(tmp_path: Path) -> None:
    stl = long_bar(tmp_path)
    mesh = read_part(stl)
    poses = study_poses(mesh, (0.0, 0.0, 35.4999999), BED)
    shaper = Shaper(mesh, tmp_path / "meshes", None, _assembly(tmp_path), "bar", poses)
    found = shaper.candidate("turned", {"turn": [180, 0, 35.5]})
    assert found.turn == (180.0, 0.0, FIT_TURN)
    assert "turned 180,0,35.5" in found.said()
    with pytest.raises(ValueError, match="isn't one of the poses: 0,0,35.5; 180,0,35.5"):
        shaper.candidate("off", {"turn": [0, 0, 36]})


# --------------------------------------------------------------------------
# The infill angle as Orca keeps it in its project
# --------------------------------------------------------------------------


def test_the_plate_angle_is_read_back_from_orcas_project() -> None:
    kept = json.dumps({"infill_direction": "80.5"})
    _plate_angle_kept(kept, 80.5)
    _plate_angle_kept(kept, 260.5)
    with pytest.raises(RuntimeError, match="keeps the infill on the plate at 80.5, not 45"):
        _plate_angle_kept(kept, 45.0)


# --------------------------------------------------------------------------
# The script
# --------------------------------------------------------------------------


def _script(monkeypatch: pytest.MonkeyPatch) -> Any:
    import importlib.util  # noqa: PLC0415

    path = Path(__file__).resolve().parents[1] / "scripts" / "settings_study.py"
    spec = importlib.util.spec_from_file_location("settings_study_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # No settings file is read: the plate is the default, textured PEI.
    monkeypatch.setattr(module, "load_env", lambda _path=None: {})
    return module


def _study(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stl: Path, turn: str) -> int:
    _assembly(tmp_path)
    code: int = _script(monkeypatch).main([
        "run", str(stl), "--assembly", str(tmp_path / "assembly.json"), "--part", "bar",
        "--nozzle", "0.4", "--filament", "Elegoo PLA", "--turn", turn,
        "--case", "standing=--weight 30 --at 10,10,700", "--out", str(tmp_path / "out"),
        "--ledger", str(tmp_path / "ledger.sqlite")])
    return code


def test_the_script_refuses_a_pose_that_doesnt_fit_saying_the_turn_that_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    stl = long_bar(tmp_path / "part")
    assert _study(tmp_path, monkeypatch, stl, "0,0,0") == 2
    err = capsys.readouterr().err
    assert err.startswith("--turn 0,0,0: turned 0,0,0 it's 300.0 x 20.0 x 10.0 mm")
    assert "give --turn 0,0,35.5, or 0,0,fit" in err
    assert not (tmp_path / "ledger.sqlite").exists()


def test_the_script_refuses_a_part_that_fits_no_pose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    stl = long_bar(tmp_path / "part", length=400.0)
    assert _study(tmp_path, monkeypatch, stl, "0,0,fit") == 2
    err = capsys.readouterr().err
    assert "turned 0,0,45 it's 297" in err.split("\n")[0], err
    assert "It fits the bed in no pose" in err
    assert "laid so, its least footprint is turned 0,0,45 it's 297.0 x 297.0 x 10.0 mm" in err


def test_the_script_takes_fit_beds_turn_and_still_holds_the_layers_axis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    stl = long_bar(tmp_path / "part")
    assert _study(tmp_path, monkeypatch, stl, "90,0,fit") == 2
    # On its side its footprint is 300 x 10 mm, which fits turned 33.5 degrees.
    assert "--turn 90,0,33.5 stands the part's own y up, but assembly.json gives its layers " \
           "along z" in capsys.readouterr().err


@pytest.mark.parametrize("text,turn", [("0,0,35.5", (0.0, 0.0, 35.5)),
                                       ("180, 0, fit", (180.0, 0.0, None))])
def test_the_turn_is_three_angles_or_two_and_fit(
    monkeypatch: pytest.MonkeyPatch, text: str, turn: tuple[float, float, float | None],
) -> None:
    assert _script(monkeypatch)._turn(text) == turn


@pytest.mark.parametrize("text", ["0,0", "0,fit,0", "0,0,nan", "a,b,c"])
def test_a_turn_that_isnt_one_is_refused(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    import argparse  # noqa: PLC0415

    with pytest.raises(argparse.ArgumentTypeError, match="RX,RY,fit"):
        _script(monkeypatch)._turn(text)


# --------------------------------------------------------------------------
# Sliced by the real Orca
# --------------------------------------------------------------------------

#: The regions asked of the long bar, in its own axes: a dense box round the hole, its lines
#: along own 60 degrees; a ring of walls round own x = 200; and a band of layers.
LONG_REGIONS = Printed(Settings(infill_angle=0.0), (
    Region("dense", ((12.0, -1.0, -1.0), (28.0, 21.0, 11.0)),
           Settings(infill=50.0, pattern="rectilinear", infill_angle=60.0)),
    Region("ring", Cylinder((200.0, 10.0, -1.0), (0.0, 0.0, 1.0), 6.0, 12.0), Settings(walls=5)),
    Region("band", Layers(3.0, 5.0), Settings(infill=40.0)),
))


@pytest.fixture(scope="module")
def turned(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    folder = tmp_path_factory.mktemp("turned")
    stl = long_bar(folder)
    return folder, slice_regions(stl, LONG_REGIONS, STORE_PROFILES, folder / "out",
                                 (0.0, 0.0, FIT_TURN), layers="z")


def _lines_in(path: Path, project: Project, box: tuple[tuple[float, float, float],
              tuple[float, float, float]]) -> dict[int, float]:
    """The sparse lines' length by their way on the plate, inside a box of the part's own
    axes, posed as the project poses it, over every layer."""
    shape = trimesh.creation.box(np.asarray(box[1]) - np.asarray(box[0]))
    shape.apply_translation((np.asarray(box[0]) + np.asarray(box[1])) / 2)
    shape.apply_transform(project.pose.matrix)
    sx, sy = project.pose.shift
    low, high = shape.bounds
    grid = gcode.Grid.round(low[0] + sx, low[1] + sy, high[0] + sx, high[1] + sy)
    total: dict[int, float] = {}
    for layer in gcode.read(path, keep_lines=True).layers:
        mask = _section_mask(shape, layer.z - layer.height / 2, grid, project.pose.shift)
        if mask.any():
            for k, v in gcode.built(layer, grid, mask).angles.items():
                total[k] = total.get(k, 0.0) + v
    return total


@pytest.mark.slicer
@needs_orca
def test_the_long_bar_slices_turned_and_centred_by_translation_only(
    turned: tuple[Path, Any],
) -> None:
    folder, result = turned
    with zipfile.ZipFile(result.project) as z:
        model = z.read("3D/3dmodel.model").decode()
        kept = json.loads(z.read("Metadata/project_settings.config"))
    assert 'transform="1 0 0 0 1 0 0 0 1 0 0 0"' in model, "Orca neither moved nor turned it"
    # The part's own infill angle, 0 (along the bar), on the plate: the turn.
    assert float(kept["infill_direction"]) == pytest.approx(FIT_TURN)
    outline = np.asarray(next(iter(gcode.read(result.gcode).outlines.values())))
    posed = read_part(folder / "bar.stl")
    posed.apply_transform(Pose.of(posed, (0.0, 0.0, FIT_TURN), BED.centre).matrix)
    assert ((outline.min(axis=0) + outline.max(axis=0)) / 2) == pytest.approx((128, 128),
                                                                              abs=0.1)
    assert np.ptp(outline, axis=0) == pytest.approx(np.ptp(posed.vertices[:, :2], axis=0),
                                                    abs=0.1)
    assert result.up == "+z"


@pytest.mark.slicer
@needs_orca
def test_the_turned_bars_regions_land_where_they_are_asked(turned: tuple[Path, Any]) -> None:
    _, result = turned
    found = {r.region.name: {s.name: s for s in r.said} for r in result.regions}
    lines = [line for r in result.regions for line in r.lines()]
    assert found["dense"]["infill"].verdict == "as asked", lines
    assert found["dense"]["infill_angle"].verdict == "as asked", lines
    assert "on the plate 95.5, and 185.5" in found["dense"]["infill_angle"].asked
    assert found["ring"]["walls"].verdict == "as asked", lines
    assert found["band"]["infill"].verdict == "as asked", lines
    assert result.settings.infill_angle == pytest.approx(0.0)
    assert result.for_load()["infill_angle"] == pytest.approx(0.0)


@pytest.mark.slicer
@needs_orca
def test_the_turned_bars_infill_reads_back_as_asked_in_its_own_axes(
    turned: tuple[Path, Any],
) -> None:
    """Along the bar, away from its regions, the part's sparse lines run at own 0 and 90: on
    the plate 35.5 and 125.5, read to the whole degree."""
    folder, result = turned
    project = Project(result.project, Pose(
        (0.0, 0.0, FIT_TURN), Pose.of(read_part(folder / "bar.stl"), (0.0, 0.0, FIT_TURN),
                                      BED.centre).matrix), {}, result.settings, STORE_PROFILES,
        read_part(folder / "bar.stl"))
    angles = _lines_in(result.base_gcode, project, ((60.0, 2.0, 0.0), (180.0, 18.0, 10.0)))
    main = max(angles, key=lambda a: angles[a])
    own = project.pose.own_angle(float(main))
    assert own is not None
    assert min(own % 90, 90 - own % 90) <= 1.0, (main, own, angles)
    near = sum(v for a, v in angles.items()
               if min(abs(a - 35.5) % 90, 90 - abs(a - 35.5) % 90) <= 1.0)
    assert near / sum(angles.values()) >= 0.8, angles


@pytest.mark.slicer
@needs_orca
def test_a_study_slices_the_long_bar_in_fit_beds_turn(tmp_path: Path) -> None:
    """The study's own path: its defaults' angle in the part's own axes, and a candidate with a
    region round the bolt's hole, sliced in the turn the study offers it."""
    stl = long_bar(tmp_path)
    mesh = read_part(stl)
    turn = turned_to_fit(mesh, (0.0, 0.0), Bed.of(STORE_MACHINE)).turn
    shaper = Shaper(mesh, tmp_path / "meshes", None, _assembly(tmp_path), "bar",
                    study_poses(mesh, turn, BED))
    # The profile's 45 degrees on the plate is own 9.5, as the bar is turned 35.5.
    assert defaults(STORE_PROFILES, stl, turn).infill_angle == pytest.approx(9.5)
    slicing = OrcaSlicing(stl, STORE_PROFILES, tmp_path / "meshes")
    base = slicing(Candidate("defaults", Settings(), turn=turn), tmp_path / "defaults")
    assert base.printed["infill_angle"] == pytest.approx(9.5) and base.printed["up"] == "+z"
    assert base.time_s and base.grams
    roomy = slicing(shaper.candidate("roomy", {"settings": {"infill_angle": 0}, "regions": [
        {"joints": ["bar+post-bolt-1"], "margin": 6, "walls": 4, "infill": 50}]}),
        tmp_path / "roomy")
    said = "\n".join(roomy.regions)
    assert "bar+post-bolt-1: built as asked" in said, said
    assert roomy.unbuilt == [] and roomy.unread == []
    assert roomy.printed["infill_angle"] == pytest.approx(0.0)
