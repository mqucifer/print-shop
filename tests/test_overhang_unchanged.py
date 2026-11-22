"""The overhang reading is fast, and it reads what it read (mqucifer/print-shop#235).

It cuts a pose's heights together and reads each point against only the lines near it. Every
point it finds, with its area and slope, is held to the old reading's (`overhang_as_it_was.py`)
to the last bit, on whatever platform runs this. Where the old reading is too slow to run here,
the vase and the plate, its figures at 3a22495 are written down.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import (
    AXIS_ALIGNED,
    _rotation_matrix,
    _seated,
    candidate_in,
    candidates,
)
from print_shop.setup import features, outline
from tests import overhang_as_it_was as as_it_was
from tests.fixtures import _BUILDERS, write_stl
from tests.plate_235 import write_plate

#: Every fixture but the vase, which the old reading takes a minute and a half over.
PARTS = sorted(set(_BUILDERS) - {"vase"})

#: At 3a22495: overhang, flare and area on the plate in mm2, and height in mm, pose by pose.
PLATE_THEN = (
    (0.0, 0.0, 17338.1, 4.0), (0.0, 0.0, 17338.1, 4.0),
    (1276.1, 6.8, 0.0, 175.2), (1236.6, 23.1, 0.0, 175.2),
    (1284.1, 9.6, 1.6, 175.53), (1268.5, 9.6, 1.6, 175.53),
)
VASE_THEN = (
    (0.0, 0.0, 2825.4, 120.0), (2384.9, 0.0, 364.2, 120.0),
    (9950.8, 0.0, 0.0, 60.0), (9950.8, 0.0, 0.0, 60.0),
    (9950.8, 0.0, 0.0, 60.0), (9950.8, 0.0, 0.0, 60.0),
)
#: Six poses of the plate: the issue's bar.
SIX_S = 10.0


def _posed(mesh: trimesh.Trimesh, rotation: tuple[float, float, float]) -> trimesh.Trimesh:
    posed = mesh.copy()
    posed.apply_transform(_rotation_matrix(rotation))
    return _seated(posed)


def _read_as_before(mesh: trimesh.Trimesh, beneath: trimesh.Trimesh | None = None) -> None:
    """Every number both readings give, compared with `==`: no tolerance."""
    was = as_it_was.overhanging(mesh, beneath=beneath)
    now = features._overhanging(mesh, beneath)
    assert (was is None) == (now is None)
    if was is not None and now is not None:
        for what, then, today in zip(("at", "area", "slope", "flat"), was, now, strict=True):
            assert np.array_equal(then, today), what
    assert features.overhang_to_support(mesh, beneath) == as_it_was.overhang_to_support(
        mesh, beneath
    )
    if beneath is None:
        assert features._overhang(mesh) == as_it_was.overhang(mesh)


@pytest.mark.parametrize("part", PARTS)
def test_each_fixture_reads_as_it_did_in_each_pose(part: str) -> None:
    mesh = _BUILDERS[part]()
    for rotation in AXIS_ALIGNED:
        _read_as_before(_posed(mesh, rotation))


def test_a_face_with_something_beneath_reads_as_it_did() -> None:
    tee = _posed(_BUILDERS["tee"](), (0.0, 0.0, 0.0))
    under = trimesh.creation.box(extents=(8, 18, 9.5))
    under.apply_translation((-5, 0, 4.75))
    _read_as_before(tee, under)


@pytest.mark.parametrize("seed", range(3))
def test_a_tilted_part_reads_as_it_did(seed: int) -> None:
    """Off the axes, so cuts meet corners, edges and slopes at no round figure."""
    x, y, z = np.random.default_rng(seed).uniform(0, 360, 3).tolist()
    mesh = _BUILDERS["snap"]()
    mesh.apply_transform(_rotation_matrix((x, y, z)))
    _read_as_before(_seated(mesh))


@pytest.mark.parametrize("part", ["tee", "snap"])
def test_how_many_are_read_at_once_changes_no_figure(
    part: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An area is a sum, which turns on its order in the last bit: whatever the batches, it's
    summed in the order it was."""
    monkeypatch.setattr(features, "_POINTS_AT_ONCE", 61)
    monkeypatch.setattr(outline, "AT_ONCE", 257)
    mesh = _BUILDERS[part]()
    for rotation in AXIS_ALIGNED:
        _read_as_before(_posed(mesh, rotation))


def test_what_is_held_at_once_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ceiling's points were all held together, and each against every line: up to 4 GB."""
    monkeypatch.setattr(features, "_POINTS_AT_ONCE", 2000)
    monkeypatch.setattr(outline, "AT_ONCE", 3000)
    read: list[int] = []
    paired: list[int] = []
    reach, ranges = outline.Outlines.reach, outline._ranges

    def reached(self: Any, points: np.ndarray, heights: np.ndarray) -> np.ndarray:
        read.append(len(points))
        return reach(self, points, heights)

    def ranged(first: np.ndarray, after: np.ndarray) -> Any:
        for which, place in ranges(first, after):
            paired.append(len(place))
            yield which, place

    monkeypatch.setattr(outline.Outlines, "reach", reached)
    monkeypatch.setattr(outline, "_ranges", ranged)
    features.overhang_to_support(_posed(_BUILDERS["bracket"](), (180.0, 0.0, 0.0)))
    assert sum(read) > 5000, "a ceiling of more points than are read at once"
    assert max(read) < 3 * 2000
    assert max(paired) <= 3000


def test_the_plates_six_poses_take_under_ten_seconds_with_the_old_figures(tmp_path: Path) -> None:
    stl = write_plate(tmp_path)
    assert analyze(stl).is_manifold
    assert len(trimesh.load_mesh(str(stl)).faces) >= 5000
    began = time.monotonic()
    found = candidates(stl)
    took = time.monotonic() - began
    assert _figures(found) == PLATE_THEN
    assert took < SIX_S, f"the six took {took:.1f} s"


def test_the_vase_reads_as_it_did(tmp_path: Path) -> None:
    assert _figures(candidates(write_stl("vase", tmp_path))) == VASE_THEN


def test_one_pose_alone_is_the_candidate_it_is_among_six(tmp_path: Path) -> None:
    stl = write_stl("rounded_lid", tmp_path)
    assert [candidate_in(stl, rotation) for rotation in AXIS_ALIGNED] == candidates(stl)


def _figures(found: list[Any]) -> tuple[tuple[float, float, float, float], ...]:
    return tuple((c.overhang_area_mm2, c.flare_area_mm2, c.bed_contact_area_mm2, c.height_mm)
                 for c in found)
