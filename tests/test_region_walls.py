"""Specification for how many walls a region's depth holds, said before it's sliced, and each
candidate's Orca project linked from the study's table (mqucifer/print-shop#357).

A region with walls of its own is printed as an island, walled round its own outline, so its
walls come in from every side of its section in the part: it holds one wall for each two bead
spacings of its depth. On the first real study, 6 walls asked in a face mesh 1 mm deep were
built as 1. The bar of `tests/test_slice_regions.py`, with rings and face meshes of known depth,
is read before slicing; the tests that slice with Orca are marked `slicer`, and skip without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import trimesh

from print_shop.bench.printed import Mesh, Printed, Region, Settings, read
from print_shop.ingest.orientation import read_part
from print_shop.slicer.regions import _held, slice_regions, walls_depth, walls_held
from print_shop.study.candidate import Candidate, Shaper, faces_mesh
from print_shop.study.loop import StudyLoop, StudyPart, run
from print_shop.study.reading import Sliced
from print_shop.study.table import table
from tests.fakes import STORE_PROFILES
from tests.test_settings_study import (  # noqa: F401 - `part` is a fixture
    CASES,
    THREE_WALLS,
    Model,
    fake_loading,
    fake_slicing,
    part,
)
from tests.test_slice_regions import HOLE, _assembly, bar, needs_orca

#: A bead's spacing with the store's 0.45 mm lines at 0.2 mm (`as_printed.wall_thickness`).
SPACING = 0.45 - 0.2 * (1 - 3.141592653589793 / 4)
#: The latch's box at the bar's end, 2 mm round it, where its faces make a closed mesh.
LATCH_BOX = ((58.0, 0.0, -2.0), (72.0, 20.0, 12.0))


def _ring(folder: Path, depth: float) -> Region:
    """A ring round the bar's 6 mm hole, `depth` mm of the part deep, as a mesh beside it."""
    tube = trimesh.creation.cylinder(radius=3 + depth, height=12, sections=96).difference(
        trimesh.creation.cylinder(radius=2, height=14, sections=96))
    tube.apply_translation((*HOLE, 5))
    (folder / "faces").mkdir(parents=True, exist_ok=True)
    tube.export(folder / "faces" / f"ring-{depth:g}.stl")
    return read({"regions": [{"name": "ring", "mesh": f"faces/ring-{depth:g}.stl",
                              "walls": 1}]}).regions[0]


def _face_mesh(folder: Path, depth: float, walls: int) -> Region:
    """A mesh of the bar's own faces in the latch's box, `depth` mm deep (`faces_mesh`)."""
    solid = faces_mesh(read_part(bar(folder)), LATCH_BOX, depth)
    assert solid is not None
    folder.mkdir(parents=True, exist_ok=True)
    solid.export(folder / f"faces-{depth:g}.stl")
    return Region("latch-faces", Mesh(f"faces-{depth:g}.stl"), Settings(walls=walls))


# --------------------------------------------------------------------------
# The walls a depth holds
# --------------------------------------------------------------------------


@pytest.mark.parametrize("depth,walls", [
    # Orca 2.4.2's own, on the bar's rings with 10 walls asked (`regions._held`).
    (0.8, 1), (1.0, 1), (1.2, 1), (1.4, 1), (1.8, 2), (2.0, 2), (2.2, 2), (2.6, 3), (2.8, 3),
    (3.0, 3), (3.4, 4), (3.8, 4), (4.4, 5),
])
def test_a_depth_holds_one_wall_for_each_two_bead_spacings(depth: float, walls: int) -> None:
    assert _held(depth, SPACING, 0.45) == walls


def test_narrower_than_a_line_holds_none_and_the_least_depth_for_each_is_said() -> None:
    assert _held(0.4, SPACING, 0.45) == 0
    assert walls_depth(1, 0.45, 0.2) == 0.45
    assert walls_depth(2, 0.45, 0.2) == pytest.approx(1.63, abs=0.01)
    assert walls_depth(6, 0.45, 0.2) == pytest.approx(4.89, abs=0.01)


@pytest.mark.parametrize("depth,walls", [(1.0, 1), (2.0, 2), (3.0, 3)])
def test_a_rings_depth_in_the_part_is_read_from_its_sections(
    tmp_path: Path, depth: float, walls: int,
) -> None:
    """The ring reaches inside the hole: only what of the part it holds is its depth."""
    found = walls_held(read_part(bar(tmp_path)), _ring(tmp_path, depth), "z", tmp_path, 0.45, 0.2)
    assert found is not None
    assert found.depth_mm == pytest.approx(depth, abs=0.03)
    assert found.holds == walls


def test_a_face_meshs_depth_is_its_strip_not_the_faces_across_its_layers(tmp_path: Path) -> None:
    """At the bar's top and bottom the latch's face mesh lies in the faces, whole: on most of
    its layers it's a strip round the faces at its sides and end, 1 mm deep."""
    found = walls_held(read_part(bar(tmp_path)), _face_mesh(tmp_path, 1.0, 6), "z", tmp_path,
                       0.45, 0.2)
    assert found is not None
    assert found.depth_mm == pytest.approx(1.0, abs=0.1)
    assert found.holds == 1
    assert found.said().endswith("on most of its layers, which holds at most 1 wall of 0.41 mm")


def test_a_band_of_layers_is_as_deep_as_the_parts_own_section(tmp_path: Path) -> None:
    mesh = read_part(bar(tmp_path))
    band = read({"regions": [{"name": "band", "layers": [3, 5], "walls": 3}]}).regions[0]
    found = walls_held(mesh, band, "z", None, 0.45, 0.2)
    assert found is not None and found.holds >= 10


# --------------------------------------------------------------------------
# Refused before it's sliced, or said beside its reading
# --------------------------------------------------------------------------


def test_a_face_mesh_1_mm_deep_asked_for_6_walls_is_refused_naming_1(tmp_path: Path) -> None:
    """The first real study's: 6 walls in a face mesh 1 mm deep, which Orca built as 1."""
    shaper = Shaper(read_part(bar(tmp_path)), tmp_path, None, _assembly(tmp_path), "bar")
    with pytest.raises(ValueError) as found:
        shaper._depths([_face_mesh(tmp_path, 1.0, 6)], (0.0, 0.0, 0.0))
    said = str(found.value)
    assert said.startswith("region latch-faces asks 6 walls, but it is 0.")
    assert "which holds at most 1 wall of 0.41 mm: ask 1 or fewer, or make it at least 4.89 mm " \
           "deep in the part" in said
    assert "so it isn't sliced" in said
    (one,) = shaper._depths([_face_mesh(tmp_path, 1.0, 1)], (0.0, 0.0, 0.0))
    assert one.startswith("latch-faces is ") and one.endswith("holds at most 1 wall of 0.41 mm")


def test_the_models_regions_round_the_joints_are_held_to_their_depth(tmp_path: Path) -> None:
    shaper = Shaper(read_part(bar(tmp_path)), tmp_path / "meshes", None, _assembly(tmp_path),
                    "bar")
    with pytest.raises(ValueError, match="region bar\\+post-bolt-1 asks 6 walls, but it is "
                                         "2.00 mm deep in the part on most of its layers, which "
                                         "holds at most 2 walls of 0.41 mm: ask 2 or fewer"):
        shaper.candidate("c1", {"regions": [{"joints": ["bar+post-bolt-1"], "margin": 2,
                                             "walls": 6}]})
    fits = shaper.candidate("c1", {"regions": [{"joints": "all", "margin": 2, "faces": True,
                                                "walls": 2}]})
    assert fits.notes == (
        "bar+post-bolt-1 is 2.00 mm deep in the part on most of its layers, which holds at most "
        "2 walls of 0.41 mm",
        "bar+lid-latch is 1.90 mm deep in the part on most of its layers, which holds at most 2 "
        "walls of 0.41 mm")
    assert fits.plain()["notes"] == list(fits.notes)


def test_a_refused_region_isnt_sliced_and_its_reason_reaches_the_model(part: StudyPart) -> None:  # noqa: F811
    seen: list[Candidate] = []
    part.slicing, part.loading = fake_slicing(seen), fake_loading([])
    six = ("read", {"why": "a thick ring round the hole", "regions": [
        {"joints": ["bar+post-bolt-1"], "margin": 2, "walls": 6}]})
    two = ("read", {"why": "the walls its depth holds", "regions": [
        {"joints": ["bar+post-bolt-1"], "margin": 2, "walls": 2}]})
    model = Model([six], [two], [THREE_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    reads = [c.result for c in result.calls if c.tool == "read"]
    assert reads[0].startswith("refused: region bar+post-bolt-1 asks 6 walls")
    assert [c.name for c in seen] == ["defaults", "c1", "c2"], "no slice spent on it"
    assert "  before slicing: bar+post-bolt-1 is 2.00 mm deep in the part on most of its " \
           "layers, which holds at most 2 walls of 0.41 mm" in reads[1]


def test_the_read_tool_and_the_part_say_what_a_depth_holds(part: StudyPart) -> None:  # noqa: F811
    from print_shop.study.loop import TOOLS, _user  # noqa: PLC0415
    from print_shop.study.reading import Reading  # noqa: PLC0415

    described = next(t for t in TOOLS if t["function"]["name"] == "read")["function"]
    assert "one wall for each two bead spacings of its depth" in described["description"]
    said = _user(part, StudyLoop(chat=Model()), Reading(Candidate("defaults", Settings())))
    assert "A bead's spacing is 0.41 mm: a region holds one wall of its own for each two of " \
           "them across its depth in the part." in said


# --------------------------------------------------------------------------
# Each candidate's Orca project, linked from the table
# --------------------------------------------------------------------------


def test_each_candidates_orca_project_is_linked_from_its_row_and_the_recommendation(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    out = tmp_path / "my study"
    slicing = fake_slicing([])

    def projects(candidate: Candidate, folder: Path) -> Sliced:
        sliced = slicing(candidate, folder)
        sliced.project = folder / ("regions.3mf" if candidate.regions else "part.3mf")
        return sliced

    part.slicing, part.loading = projects, fake_loading([])
    part.workdir = out / "candidates"
    model = Model([THREE_WALLS], [("recommend", {"candidate": "c1", "reason": "r"})],
                  [("read", {"why": "w", "settings": {"walls": 4}})],
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    result = run(StudyLoop(chat=model), part)
    text = table(result, "bar", [c.name for c in CASES], where=out)
    assert "| Passes | Orca project |" in text
    assert "| [part.3mf](<candidates/defaults/part.3mf>) |" in text
    assert "| [part.3mf](<candidates/c1/part.3mf>) |" in text
    assert "Its Orca project: [part.3mf](<candidates/c1/part.3mf>)." in text
    assert "open it in Orca to see them and its settings by eye" in text


def test_without_its_folder_the_link_is_the_projects_whole_path(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    slicing = fake_slicing([])

    def projects(candidate: Candidate, folder: Path) -> Sliced:
        sliced = slicing(candidate, folder)
        sliced.project = folder / "part.3mf"
        return sliced

    part.slicing, part.loading = projects, fake_loading([])
    model = Model([THREE_WALLS], [("recommend", {"candidate": "none", "reason": "r"})],
                  [("read", {"why": "w", "settings": {"walls": 4}})],
                  [("recommend", {"candidate": "none", "reason": "r"})])
    text = table(run(StudyLoop(chat=model), part), "bar", [c.name for c in CASES])
    assert f"[part.3mf](<{(part.workdir / 'defaults' / 'part.3mf').as_posix()}>)" in text


# --------------------------------------------------------------------------
# Sliced by the real Orca
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_the_walls_a_face_mesh_1_mm_deep_holds_are_built_as_asked(tmp_path: Path) -> None:
    region = _face_mesh(tmp_path, 1.0, 1)
    result = slice_regions(bar(tmp_path), Printed(Settings(walls=3), (region,)), STORE_PROFILES,
                           tmp_path / "out", base=tmp_path)
    walls = next(s for s in result.regions[0].said if s.name == "walls")
    assert (walls.asked, walls.read, walls.verdict) == ("1", "1", "as asked"), \
        result.regions[0].lines()
