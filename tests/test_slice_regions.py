"""Specification for `slicer/regions.py`, `slicer/joints.py` and the region reader in
`slicer/gcode.py` (mqucifer/print-shop#338).

A synthetic bar, 80 x 20 x 10 mm with a 6 mm hole through it at x = 20, is sliced by the real
Orca with regions of their own settings, and each is read back from the layers. The tests that
need Orca are marked `slicer`, and skip without it; the rest read made-up layers and projects,
and run in CI.
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.bench import assembly
from print_shop.bench.printed import Cylinder, Layers, Printed, Region, Settings, read
from print_shop.ingest.orientation import AXIS_ALIGNED, read_part
from print_shop.setup.context import Fit, PartContext
from print_shop.slicer import gcode
from print_shop.slicer.joints import hole_at, round_joints
from print_shop.slicer.regions import (
    Pose,
    Project,
    RegionsSlice,
    _add_regions,
    _item_shift,
    read_region,
    settings_of,
    slice_regions,
)
from tests.fakes import STORE_PROCESS, STORE_PROFILES

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
needs_orca = pytest.mark.skipif(not ORCA.exists(), reason="OrcaSlicer is not installed")

#: The hole's centre, in the bar's own axes.
HOLE = (20.0, 10.0)


def bar(folder: Path) -> Path:
    """The bar, 80 x 20 x 10 mm, with a 6 mm hole through it along z at `HOLE`."""
    body = trimesh.creation.box((80, 20, 10))
    body.apply_translation((40, 10, 5))
    hole = trimesh.creation.cylinder(radius=3, height=12, sections=48)
    hole.apply_translation((*HOLE, 5))
    part = body.difference(hole)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "bar.stl"
    part.export(path)
    return path


# --------------------------------------------------------------------------
# The part's own settings, from its profile
# --------------------------------------------------------------------------


def test_every_setting_load_needs_is_read_from_the_profile_it_slices_with() -> None:
    """The store's 0.20 mm process: 2 walls, 15% zig-zag at 45 degrees, 5 top and 3 bottom
    layers, each as thick as its shell thickness asks (1.0 and 0.6 mm), the walls 0.45 wide."""
    found = settings_of(STORE_PROCESS)
    assert found == Settings(walls=2, line_width=0.45, top=5, bottom=3, infill=15.0,
                             pattern="rectilinear", infill_angle=45.0, layer_height=0.2)
    assert found.missing() == []


def test_shells_are_as_many_as_orcas_shell_thickness_asks(tmp_path: Path) -> None:
    data = json.loads(STORE_PROCESS.read_text())
    data.update(top_shell_layers="3", top_shell_thickness="1.1", bottom_shell_layers="2",
                bottom_shell_thickness="0")
    path = tmp_path / "process.json"
    path.write_text(json.dumps(data))
    found = settings_of(path)
    assert (found.top, found.bottom) == (6, 2)


# --------------------------------------------------------------------------
# The pose, and the infill's angle in the part's own axes
# --------------------------------------------------------------------------


@pytest.mark.parametrize("turn", AXIS_ALIGNED)
def test_the_infill_angle_turns_onto_the_plate_and_back(turn: tuple[float, float, float]) -> None:
    pose = Pose.of(trimesh.creation.box((10, 20, 30)), turn)
    assert pose.layers() in ("x", "y", "z")
    for own in (0.0, 30.0, 90.0, 135.0):
        plate = pose.plate_angle(own)
        assert plate is not None
        assert pose.own_angle(plate) == pytest.approx(own % 360, abs=1e-6)


def test_laid_as_the_file_lies_the_angle_is_the_plates() -> None:
    pose = Pose.of(trimesh.creation.box((10, 20, 30)), AXIS_ALIGNED[0])
    assert pose.layers() == "z"
    assert pose.plate_angle(30.0) == pytest.approx(30.0)


def test_stood_on_its_side_the_layers_run_along_another_own_axis() -> None:
    """Turned 90 degrees about x, own +y is up: the layers are along y, and the angle runs from
    own +z toward +x. Own +z lies along the plate's -y."""
    pose = Pose.of(trimesh.creation.box((10, 20, 30)), (90.0, 0.0, 0.0))
    assert pose.layers() == "y"
    assert pose.plate_angle(0.0) == pytest.approx(270.0)
    assert pose.plate_angle(90.0) == pytest.approx(0.0)


def test_a_part_orca_turned_on_the_plate_isnt_read_against_its_turn() -> None:
    assert _item_shift('<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 88 118 0"/>'
                       "</build>") == (88.0, 118.0)
    with pytest.raises(RuntimeError, match="Orca turned the part on the plate"):
        _item_shift('<item objectid="2" transform="0 1 0 -1 0 0 0 0 1 88 118 0"/>')


# --------------------------------------------------------------------------
# The project: Orca's own, with the regions added as Orca stores them
# --------------------------------------------------------------------------


def _exported(path: Path) -> Path:
    """A project as Orca 2.4.2's `--export-3mf` writes one part: its files cut to what's read."""
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter"><resources>'
             '<object id="2" type="model"><components><component p:path="/3D/Objects/bar.model" '
             'objectid="1" transform="1 0 0 0 1 0 0 0 1 40 10 5"/></components></object>'
             '</resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 88 118 0" '
             'printable="1"/></build></model>')
    settings = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2">\n'
                '    <part id="1" subtype="normal_part"></part>\n  </object>\n</config>\n')
    part = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="core" '
            'xmlns:p="production" requiredextensions="p"><resources><object id="1" '
            'type="model"><mesh/></object></resources></model>')
    rels = ('<Relationships xmlns="relationships"><Relationship Target="/3D/Objects/bar.model" '
            'Id="rel-1" Type="model"/></Relationships>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", model)
        z.writestr("3D/Objects/bar.model", part)
        z.writestr("Metadata/model_settings.config", settings)
        z.writestr("Metadata/project_settings.config", "{}")
        z.writestr("3D/_rels/3dmodel.model.rels", rels)
    return path


def test_each_region_is_added_to_orcas_project_as_orca_stores_a_modifier(tmp_path: Path) -> None:
    box = trimesh.creation.box((4, 4, 4))
    box.apply_translation((10, 10, 2))
    dense = Region("dense", ((8, 8, 0), (12, 12, 4)), Settings(infill=60.0))
    band = Region("band", Layers(3.0, 5.0), Settings(walls=4))
    out = tmp_path / "regions.3mf"
    _add_regions(_exported(tmp_path / "part.3mf"), out,
                 [(dense, box, {"sparse_infill_density": "60%"})],
                 [(band, {"extruder": "0", "layer_height": "0.2", "wall_loops": "4"})])
    with zipfile.ZipFile(out) as z:
        model = z.read("3D/3dmodel.model").decode()
        settings = z.read("Metadata/model_settings.config").decode()
        meshes = z.read("3D/Objects/regions.model").decode()
        ranges = z.read("Metadata/layer_config_ranges.xml").decode()
        rels = z.read("3D/_rels/3dmodel.model.rels").decode()
        kept = z.read("Metadata/project_settings.config").decode()
    # A component of the part's object, its mesh centred in its own model file, moved to place.
    assert ('<component p:path="/3D/Objects/regions.model" objectid="3" '
            'transform="1 0 0 0 1 0 0 0 1 10 10 2"/></components>') in model
    # Written as Orca wrote the part's: its header and its relationship's type.
    assert meshes.startswith('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" '
                             'xmlns="core" xmlns:p="production" requiredextensions="p">'
                             '<resources><object id="3" type="model"><mesh>')
    assert ('<Relationship Target="/3D/Objects/regions.model" Id="rel-regions" Type="model"/>'
            "</Relationships>") in rels
    # The modifier's part, with its settings as Orca's own keys.
    assert ('<part id="3" subtype="modifier_part"><metadata key="name" value="dense"/>'
            '<metadata key="matrix" value="1 0 0 10 0 1 0 10 0 0 1 2 0 0 0 1"/>'
            '<metadata key="sparse_infill_density" value="60%"/></part></object>') in settings
    # The height range, keyed by the object's index from 1, with its own layer height.
    assert '<object id="1">' in ranges
    assert ('<range min_z="3" max_z="5"><option opt_key="extruder">0</option><option '
            'opt_key="layer_height">0.2</option><option opt_key="wall_loops">4</option>'
            "</range>") in ranges
    assert kept == "{}", "nothing Orca wrote is rewritten"


def test_a_project_of_more_than_one_object_isnt_added_to(tmp_path: Path) -> None:
    path = tmp_path / "two.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", "<components></components><components></components>")
        z.writestr("Metadata/model_settings.config", "<object></object>")
    with pytest.raises(RuntimeError, match="doesn't hold one object"):
        _add_regions(path, tmp_path / "out.3mf", [], [])


# --------------------------------------------------------------------------
# The reader: a made-up layer, read inside a region
# --------------------------------------------------------------------------


def _square_layer(walls: int, density: float, width: float = 0.45, height: float = 0.2,
                  side: float = 20.0, angle: float = 0.0) -> gcode.Layer:
    """A layer of a square `side` mm across: `walls` loops, the outer one first, and sparse
    lines `angle` degrees from +x spaced as Orca spaces them for `density` percent."""
    lines: list[gcode.Line] = []
    spacing = width - height * (1 - math.pi / 4)
    for k in range(walls):
        inset = width / 2 + k * spacing
        lo, hi = inset, side - inset
        kind = "Outer wall" if k == 0 else "Inner wall"
        corners = [(lo, lo), (hi, lo), (hi, hi), (lo, hi), (lo, lo)]
        lines += [gcode.Line(x0, y0, x1, y1, width, kind, "")
                  for (x0, y0), (x1, y1) in zip(corners, corners[1:], strict=False)]
    inner = width / 2 + walls * spacing - spacing / 2
    gap = spacing / (density / 100)
    if angle == 0.0:
        y = inner + gap / 2
        while y < side - inner:
            lines.append(gcode.Line(inner, y, side - inner, y, width, "Sparse infill", ""))
            y += gap
    else:
        x = inner + gap / 2
        while x < side - inner:
            lines.append(gcode.Line(x, inner, x, side - inner, width, "Sparse infill", ""))
            x += gap
    return gcode.Layer(n=5, z=1.0, height=height, lines=lines)


def _read_square(layer: gcode.Layer, box: tuple[float, float, float, float]) -> gcode.Built:
    grid = gcode.Grid.round(*box)
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.cell
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.cell
    mask = ((xs[None, :] >= box[0]) & (xs[None, :] <= box[2])
            & (ys[:, None] >= box[1]) & (ys[:, None] <= box[3]))
    return gcode.built(layer, grid, mask)


@pytest.mark.parametrize("walls,density", [(2, 15.0), (3, 40.0), (5, 60.0)])
def test_a_layers_walls_and_infill_are_read_from_its_lines(walls: int, density: float) -> None:
    found = _read_square(_square_layer(walls, density), (0, 0, 20, 20))
    assert found.walls == walls
    assert found.infill == pytest.approx(density, abs=3.0)
    assert found.solid == 0.0
    assert found.area_mm2 == pytest.approx(400, rel=0.03)
    assert max(found.angles, key=lambda a: found.angles[a]) == 0


def test_only_what_lies_inside_the_region_is_read() -> None:
    """A box over the square's right half meets its walls on three sides only, and its infill."""
    layer = _square_layer(3, 40.0, angle=90.0)
    half = _read_square(layer, (10, -1, 21, 21))
    assert half.area_mm2 == pytest.approx(200, rel=0.03)
    assert half.walls == 3
    assert max(half.angles, key=lambda a: half.angles[a]) == 90
    whole = _read_square(layer, (-1, -1, 21, 21))
    assert half.volume_mm3 == pytest.approx(whole.volume_mm3 / 2, rel=0.08)


def test_a_layer_filled_solid_reads_solid() -> None:
    layer = _square_layer(2, 40.0)
    solid = [gcode.Line(ln.x0, ln.y0, ln.x1, ln.y1, ln.width, "Internal solid infill", "")
             for ln in _square_layer(2, 100.0).lines if ln.kind == "Sparse infill"]
    layer.lines = [ln for ln in layer.lines if ln.kind != "Sparse infill"] + solid
    found = _read_square(layer, (0, 0, 20, 20))
    assert found.solid >= gcode.SOLID_SHARE
    assert found.infill is None


def test_outer_walls_close_into_loops() -> None:
    lines = _square_layer(1, 10.0).lines
    loops = gcode.loops(lines)
    assert len(loops) == 5  # four sides and the segment that closes the loop, of no length


# --------------------------------------------------------------------------
# Regions round a part's joints
# --------------------------------------------------------------------------


def test_a_hole_is_found_round_a_bolts_point_in_the_parts_own_mesh(tmp_path: Path) -> None:
    mesh = read_part(bar(tmp_path))
    axis, radius = hole_at(mesh, (*HOLE, 5)) or (None, None)
    assert axis == 2 and radius == pytest.approx(3.0, abs=0.05)
    assert hole_at(mesh, (50, 10, 5)) is None, "in the part, not in a hole"
    assert hole_at(mesh, (100, 10, 5)) is None, "off the part"


def _assembly(tmp_path: Path) -> assembly.Assembly:
    for name in ("bar.step", "post.step", "lid.step"):
        (tmp_path / name).write_text("")
    data: dict[str, Any] = {
        "parts": [
            {"name": "bar", "file": "bar.step", "at": [100, 0, 0], "turn": [["z", 90]],
             "layers": "z"},
            {"name": "post", "file": "post.step", "at": [0, 0, -50], "layers": "z"},
            {"name": "lid", "file": "lid.step", "at": [0, 0, 50], "layers": "z"},
        ],
        "joints": [
            # The bolt through the bar's hole, in the assembly's axes: own (20, 10) turned 90
            # degrees about z is (-10, 20), moved by (100, 0, 0).
            {"between": ["bar", "post"], "kind": "bolted", "bolt": "m4-8.8",
             "insert": "m4-heat-set", "at": [[90, 20, 5], [100, 70, 5]]},
            {"between": ["bar", "lid"], "kind": "dovetail", "slides": "-z",
             "latch": {"on": "bar", "arm": [[60, 2, 0], [70, 18, 10]], "root": "x=60",
                       "frame": "part"}},
        ],
        "held": [{"plane": "z=-50", "how": "fixed"}],
    }
    path = tmp_path / "assembly.json"
    path.write_text(json.dumps(data))
    return assembly.read(str(path))


def test_regions_are_put_round_each_joint_of_the_part_by_the_margin(tmp_path: Path) -> None:
    mesh = read_part(bar(tmp_path))
    context = PartContext(source="a sidecar", fits=(
        Fit(name="end", mate="post", play_max=0.2, box=((0, 0, 0), (5, 20, 10)),
            derived="from the need's box"),
        Fit(name="lost", mate="post", play_max=0.2),
    ))
    found = round_joints(Settings(infill=60.0, walls=4), 2.0, mesh, context,
                         _assembly(tmp_path), "bar")
    names = [r.name for r in found.regions]
    assert names == ["fit-end", "bar+post-bolt-1", "bar+lid-latch"]
    fit, bolt, latch = found.regions
    assert fit.kind == "box" and fit.shape == ((-2.0, -2.0, -2.0), (7.0, 22.0, 12.0))
    cylinder = bolt.shape
    assert bolt.kind == "cylinder" and isinstance(cylinder, Cylinder)
    assert cylinder.at[:2] == pytest.approx(HOLE, abs=1e-6)
    assert cylinder.axis == (0.0, 0.0, 1.0)
    assert cylinder.radius == pytest.approx(5.0, abs=0.05)
    assert cylinder.length == pytest.approx(14.0)
    assert latch.kind == "box" and latch.shape == ((58.0, 0.0, -2.0), (72.0, 20.0, 12.0))
    assert all(r.settings == Settings(infill=60.0, walls=4) for r in found.regions)
    assert any("fit-lost: its place wasn't derived" in n for n in found.notes)
    assert any("bar+post-bolt-2: no hole round its point" in n for n in found.notes)


# --------------------------------------------------------------------------
# The real Orca: the bar sliced with regions, and each read back
# --------------------------------------------------------------------------


BAR_REGIONS = {"regions": [
    {"name": "dense", "box": [[12, -1, -1], [28, 21, 11]], "infill": 60, "pattern": "grid",
     "infill_angle": 20, "why": "round the hole, 5 mm past it"},
    {"name": "ring", "cylinder": {"at": [60, 10, -1], "axis": "z", "radius": 6, "length": 12},
     "walls": 5},
    {"name": "lid", "box": [[40, -1, 5], [56, 21, 11]], "top": 9},
    {"name": "band", "layers": [3, 5], "infill": 40, "layer_height": 0.1},
]}


@pytest.fixture(scope="module")
def sliced(tmp_path_factory: pytest.TempPathFactory) -> RegionsSlice:
    folder = tmp_path_factory.mktemp("regions")
    return slice_regions(bar(folder), read(BAR_REGIONS), STORE_PROFILES, folder / "out")


def _plate_box(result: RegionsSlice, which: Path, low: tuple[float, float],
               high: tuple[float, float]) -> list[gcode.Built]:
    """The layers of one slice read inside a box of the bar's own x and y, through it all."""
    outline = np.asarray(next(iter(gcode.read(which).outlines.values())))
    sx, sy = outline.min(axis=0)
    box = (low[0] + sx, low[1] + sy, high[0] + sx, high[1] + sy)
    layers = gcode.read(which, keep_lines=True).layers
    return [_read_square(layer, box) for layer in layers]


@pytest.mark.slicer
@needs_orca
def test_a_dense_box_round_the_hole_costs_time_and_grams_and_is_denser_only_inside(
    sliced: RegionsSlice,
) -> None:
    assert sliced.orca == "2.4.2"
    assert sliced.time_added_s is not None and sliced.time_added_s > 0
    assert sliced.grams_added is not None and sliced.grams_added > 0
    dense = sliced.regions[0]
    assert dense.built, dense.lines()
    infill = next(s for s in dense.said if s.name == "infill")
    assert infill.verdict == "as asked"
    assert float(infill.read) == pytest.approx(60, abs=3)
    assert dense.grams_added is not None and dense.grams_added > 0
    # Outside every region, the part's own 15%, in the slice with them as without: below and
    # above the band of layers, which holds the whole layer.
    for which in (sliced.gcode, sliced.base_gcode):
        far = [b.infill for b in _plate_box(sliced, which, (66, 1), (78, 19))
               if b.infill is not None and b.solid < 0.5 and not 3 < b.z <= 5]
        assert np.mean(far) == pytest.approx(15, abs=1)
    # Inside the box, without it: the part's own.
    base_inside = [b.infill for b in dense.base_layers if b.infill is not None and b.solid < 0.5]
    assert np.mean(base_inside) == pytest.approx(15, abs=1)


@pytest.mark.slicer
@needs_orca
def test_each_shape_reads_as_orca_built_it(sliced: RegionsSlice) -> None:
    said = {(r.region.name, s.name): s for r in sliced.regions for s in r.said}
    assert said[("dense", "infill_angle")].verdict == "as asked"
    assert said[("dense", "pattern")].verdict == "not read"
    assert (said[("ring", "walls")].verdict, said[("ring", "walls")].read) == ("as asked", "5")
    assert (said[("lid", "top")].verdict, said[("lid", "top")].read) == ("as asked", "9")
    assert said[("band", "infill")].verdict == "as asked"
    assert (said[("band", "layer_height")].verdict,
            said[("band", "layer_height")].read) == ("as asked", "0.1")
    # Orca's height range holds whole layers: 2 mm of 0.1 mm layers.
    band = sliced.regions[3]
    assert len(band.layers) == 20
    assert all(r.built for r in sliced.regions)


@pytest.mark.slicer
@needs_orca
def test_a_region_reaching_past_the_part_is_said_to(sliced: RegionsSlice) -> None:
    dense = sliced.regions[0]
    assert any("reaches past the part on -y, -z, +y, +z" in n for n in dense.notes)


@pytest.mark.slicer
@needs_orca
def test_a_region_orca_ignored_is_said_never_assumed(
    sliced: RegionsSlice, tmp_path: Path
) -> None:
    """Read against the slice without its regions, as if Orca had dropped them: each region
    reads as the part round it, and says so."""
    base = gcode.read(sliced.base_gcode, keep_lines=True)
    project = _project_of(sliced, tmp_path)
    dense = read_region(base, base, project, project, 0, 1.27)
    assert dense.ignored and not dense.built
    infill = next(s for s in dense.said if s.name == "infill")
    assert infill.verdict == "as the part round it"
    assert "IGNORED by Orca" in dense.lines()[0]


def _project_of(sliced: RegionsSlice, tmp_path: Path) -> Project:
    """The project the bar was sliced from, read again, its shapes placed as they were."""
    stl = bar(tmp_path)
    mesh = read_part(stl)
    pose = Pose.of(mesh, AXIS_ALIGNED[0])
    outline = np.asarray(next(iter(gcode.read(sliced.gcode).outlines.values())))
    pose = Pose(pose.rotation_deg, pose.matrix, (float(outline.min(axis=0)[0]),
                                                 float(outline.min(axis=0)[1])))
    printed = read(BAR_REGIONS)
    shapes = {}
    for region in printed.regions:
        if isinstance(region.shape, tuple):
            low, high = np.asarray(region.shape[0]), np.asarray(region.shape[1])
            box = trimesh.creation.box(extents=high - low)
            box.apply_translation((low + high) / 2)
            shapes[region.name] = box
        elif isinstance(region.shape, Cylinder):
            cyl = trimesh.creation.cylinder(radius=region.shape.radius,
                                            height=region.shape.length, sections=72)
            cyl.apply_translation(np.asarray(region.shape.at) + [0, 0, region.shape.length / 2])
            shapes[region.name] = cyl
    return Project(sliced.project, pose, shapes, sliced.settings, STORE_PROFILES, mesh,
                   printed.regions)


@pytest.mark.slicer
@needs_orca
def test_the_part_goes_to_load_whole(sliced: RegionsSlice) -> None:
    whole = read(sliced.for_load())
    assert whole.settings.missing() == []
    assert whole.settings == Settings(walls=2, line_width=0.45, top=5, bottom=3, infill=15.0,
                                      pattern="rectilinear", infill_angle=45.0,
                                      layer_height=0.2)
    assert [r.name for r in whole.regions] == ["dense", "ring", "lid", "band"]


@pytest.mark.slicer
@needs_orca
def test_a_region_turns_with_the_part_and_so_does_its_infill_angle(tmp_path: Path) -> None:
    """Stood on its side (90 degrees about x), the bar's layers run along its own y. A box round
    the hole asks lines along the bar's own x (own angle 90, from +z toward +x): on the plate
    that's +x too, and Orca lays them there; the box turns with the part."""
    printed = Printed(Settings(), (Region("dense", ((12, -1, -1), (28, 21, 11)),
                                          Settings(infill=50.0, pattern="rectilinear",
                                                   infill_angle=90.0)),))
    result = slice_regions(bar(tmp_path), printed, STORE_PROFILES, tmp_path / "out",
                           (90.0, 0.0, 0.0))
    dense = result.regions[0]
    said = {s.name: s for s in dense.said}
    assert said["infill"].verdict == "as asked", dense.lines()
    assert said["infill_angle"].verdict == "as asked", dense.lines()
    assert "on the plate 0, and 90" in said["infill_angle"].asked
    assert result.settings.infill_angle is not None
    assert Pose.of(read_part(bar(tmp_path)), (90.0, 0.0, 0.0)).plate_angle(
        result.settings.infill_angle) == pytest.approx(45.0)


@pytest.mark.slicer
@needs_orca
@pytest.mark.parametrize("outer,walls_read,verdict", [
    (7.0, "4", "as asked"),
    # 2 mm round a 6 mm hole: Orca fits the walls it can, and says nothing.
    (5.0, "2", "not as asked"),
])
def test_a_mesh_region_round_the_hole_rings_it_with_walls(
    tmp_path: Path, outer: float, walls_read: str, verdict: str
) -> None:
    """A tube round the hole, as a mesh beside the job: its walls ring the hole, as the
    cylinder's do. Where 4 don't fit in its width, the reader says how many Orca fitted."""
    tube = trimesh.creation.cylinder(radius=outer, height=12, sections=72).difference(
        trimesh.creation.cylinder(radius=2, height=14, sections=72))
    tube.apply_translation((*HOLE, 5))
    (tmp_path / "faces").mkdir()
    tube.export(tmp_path / "faces" / "tube.stl")
    printed = read({"regions": [{"name": "tube", "mesh": "faces/tube.stl", "walls": 4}]})
    result = slice_regions(bar(tmp_path), printed, STORE_PROFILES, tmp_path / "out",
                           base=tmp_path)
    walls = result.regions[0].said[0]
    assert (walls.name, walls.read, walls.verdict) == ("walls", walls_read, verdict)
    if verdict != "as asked":
        assert "the walls fill it: only 2 fit in its width" in walls.why
    assert result.time_added_s is not None and result.time_added_s > 0
