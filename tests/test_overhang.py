"""A pose's overhang is what would need support, and a pose that doesn't fit the plate is refused.

mqucifer/print-shop#128, the Sponsor's second try at the lid: the orientation model stood it on
its edge again, because every downward face past 45 degrees read the flat poses at 2580 and 6495
mm2 of overhang. That counted the lid's rounded top edge, lying at the plate, though Orca sliced
it flat with no supports. Now a candidate's overhang is the features' layer-aware reading, which
leaves out a round or chamfer rising from the plate, and the review shows each pose's figures.

And `candidates()` never filled a pose's extents, so the build-volume check passed any part. Now
it reads each pose's box as it lies.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import trimesh

from print_shop.core.contracts import Proposal as Chosen
from print_shop.core.contracts import Severity, propose_validate
from print_shop.core.state import AppliedOrientation, Orientation
from print_shop.expert.orientation import (
    OrientationRequest,
    propose_geometric,
    validate_orientation,
)
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import candidates
from print_shop.orchestrator import graph
from print_shop.setup import features
from print_shop.setup.features import FLARE_MM, measure
from print_shop.setup.review import ReviewRequest, render
from tests.fixtures import _profile, write_stl
from tests.test_features import _guarded
from tests.test_review import _request

#: The CC1's build volume.
PLATE = (256.0, 256.0, 256.0)


# The rounded lid
# --------------------------------------------------------------------------


def test_a_lid_lying_on_its_rounded_edge_needs_no_support(tmp_path: Path) -> None:
    stl = write_stl("rounded_lid", tmp_path)
    assert analyze(stl).overhang_area_mm2 == pytest.approx(951.9, abs=1), (
        "every downward face past 45 degrees: the reading that stood the lid on its edge"
    )
    poses = {c.rotation_deg: c for c in candidates(stl)}
    laid = poses[(0.0, 0.0, 0.0)]
    assert laid.overhang_area_mm2 == 0.0
    assert laid.flare_area_mm2 == pytest.approx(571.7, abs=1), "the round, left out and said"
    assert poses[(180.0, 0.0, 0.0)].flare_area_mm2 == 0.0, "its square-edged back: no round"
    assert all(c.overhang_area_mm2 == 0.0 for c in poses.values())


def test_lying_on_its_round_is_chosen_over_standing_on_its_edge(tmp_path: Path) -> None:
    """The Sponsor's choice between the two: top-down on its rounded edge, or on its edge. Every
    downward face read the flat pose as worse (about 950 mm2 against 185); what needs support
    is nothing in either, and flat puts ten times as much on the plate."""
    found = {c.rotation_deg: c for c in candidates(write_stl("rounded_lid", tmp_path))}
    two = (found[(0.0, 0.0, 0.0)], found[(0.0, 90.0, 0.0)])
    request = OrientationRequest(candidates=two, build_volume_mm=PLATE)
    outcome = propose_validate(lambda r: propose_geometric(r), validate_orientation, request)
    assert outcome.accepted
    assert outcome.proposal.value.rotation_deg == (0.0, 0.0, 0.0), "flat, not on its edge"


def test_the_rounded_lid_gets_no_supports_lying_on_its_round(tmp_path: Path) -> None:
    """The features read the same way, so the guard leaves supports off."""
    stl = write_stl("rounded_lid", tmp_path)
    features = {f.id: f.value for f in measure(stl, analyze(stl).bed_contact_area_mm2)}
    assert features["overhang"] == 0
    assert "enable_support" not in _guarded("rounded_lid", tmp_path)


# What still counts
# --------------------------------------------------------------------------


def test_a_ceiling_reached_through_a_chamfer_still_needs_support(tmp_path: Path) -> None:
    """A 1 mm chamfer at 60 degrees out from the plate, then a flat ceiling 10 mm wide at 0.6 mm
    up: the chamfer rises from the plate, the ceiling doesn't."""
    run = 0.6 * np.tan(np.radians(60))
    mesh = _profile([(0.0, 0.0), (5.0, 0.0), (5.0 + run, 0.6), (15.0, 0.6), (15.0, 4.0),
                     (0.0, 4.0)])
    path = tmp_path / "ledge.stl"
    mesh.export(path)
    [only] = [c for c in candidates(path) if c.rotation_deg == (0.0, 0.0, 0.0)]
    ceiling = (15.0 - 5.0 - run) * 20.0
    assert only.overhang_area_mm2 == pytest.approx(ceiling, rel=0.1)
    assert only.flare_area_mm2 > 0


def test_a_lean_out_from_the_plate_taller_than_a_round_still_needs_support(
    tmp_path: Path,
) -> None:
    """A wall leaning out at 60 degrees from the plate to 10 mm up, past `FLARE_MM`: no round or
    chamfer, so none of it is left out. Its face rises past the flare height, and is counted
    whole: 10 / cos 60 x 20 = 400 mm2, less the first layer on the plate."""
    top = 10.0
    run = top * np.tan(np.radians(60))
    mesh = _profile([(0.0, 0.0), (4.0, 0.0), (4.0 + run, top), (0.0, top)])
    path = tmp_path / "lean.stl"
    mesh.export(path)
    [only] = [c for c in candidates(path) if c.rotation_deg == (0.0, 0.0, 0.0)]
    assert top > FLARE_MM
    assert only.overhang_area_mm2 == pytest.approx(top / np.cos(np.radians(60)) * 20, rel=0.05)
    assert only.flare_area_mm2 == 0.0


# Fitting the plate
# --------------------------------------------------------------------------


def _bar(tmp_path: Path, length: float) -> Path:
    bar = trimesh.creation.box(extents=(length, 20, 5))
    bar.apply_translation((0, 0, 2.5))
    path = tmp_path / f"bar_{length:g}.stl"
    bar.export(path)
    return path


def test_each_pose_has_its_box_as_it_lies(tmp_path: Path) -> None:
    poses = {c.rotation_deg: c for c in candidates(_bar(tmp_path, 120))}
    assert poses[(0.0, 0.0, 0.0)].bbox_mm == pytest.approx((120, 20, 5))
    assert poses[(0.0, 90.0, 0.0)].bbox_mm == pytest.approx((5, 20, 120))


def test_a_part_too_big_for_the_plate_is_refused(tmp_path: Path) -> None:
    found = tuple(candidates(_bar(tmp_path, 300)))
    request = OrientationRequest(candidates=found, build_volume_mm=PLATE)
    verdict = validate_orientation(
        Chosen(value=found[0], source="model:fake", rationale="flat"), request
    )
    [refused] = [v for v in verdict.violations if v.rule == "orientation.build_volume"]
    assert refused.severity is Severity.BLOCKER
    outcome = propose_validate(lambda r: propose_geometric(r), validate_orientation, request)
    assert not outcome.accepted, "no pose fits: it goes to a person, never to the printer"
    with pytest.raises(RuntimeError, match="doesn't fit the build volume"):
        graph._as_laid(request)


def test_a_part_that_fits_is_still_accepted(tmp_path: Path) -> None:
    found = tuple(candidates(_bar(tmp_path, 250)))
    request = OrientationRequest(candidates=found, build_volume_mm=PLATE)
    assert graph._as_laid(request).accepted


# At the review
# --------------------------------------------------------------------------


def test_the_review_shows_each_poses_figures(tmp_path: Path) -> None:
    found = tuple(candidates(write_stl("rounded_lid", tmp_path)))
    base = _request()
    shown = render(ReviewRequest(
        presets=base.presets, proposal=base.proposal, slice=base.slice, profiles=base.profiles,
        orientation=AppliedOrientation(rotation_deg=(0.0, 90.0, 0.0), rationale="for warping"),
        candidates=found,
    ))
    assert "  The poses it weighed (overhang is what would need support; ! is this print)" in shown
    assert re.search(r"\n    pose +overhang mm2 +on the plate mm2 +tall mm +left out mm2\n", shown)
    # 571.7 before mqucifer/print-shop#239: 0.2 mm2 of the round read 45.0012 degrees past
    # vertical, 1.6e-5 mm past the limit's 0.4 mm of reach, which is a tie at it.
    assert re.search(r"\n    0,0,0 +0 +4968 +12 +571\.5\n", shown)
    assert re.search(r"\n  ! 0,90,0 +0 +496 +100 +108\.3\n", shown), "this print's, marked"


# Elephant foot, and standing a part up
# --------------------------------------------------------------------------


def test_elephant_foot_is_bounded_from_zero_to_the_first_layers_line() -> None:
    from print_shop.setup.bounds import check  # noqa: PLC0415

    presets: dict[str, dict[str, Any]] = {
        "machine": {"nozzle_diameter": ["0.4"]},
        "process": {"initial_layer_line_width": "0.5"},
        "filament": {},
    }
    assert check("elefant_foot_compensation", "0.1", presets) == ""
    assert check("elefant_foot_compensation", "0.5", presets) == ""
    assert check("elefant_foot_compensation", "0.6", presets) == "outside 0-0.5"
    assert check("elefant_foot_compensation", "-0.1", presets) == "outside 0-0.5", (
        "Orca refuses a negative itself"
    )
    no_width: dict[str, dict[str, Any]] = {**presets, "process": {}}
    assert check("elefant_foot_compensation", "0.45", no_width) == "outside 0-0.4", (
        "the nozzle's width where the process doesn't say"
    )


def _pose(rotation: tuple[float, float, float], overhang: float, contact: float,
          height: float) -> Orientation:
    return Orientation(rotation_deg=rotation, overhang_area_mm2=overhang,
                       bed_contact_area_mm2=contact, height_mm=height, bbox_mm=(10, 10, height))


@pytest.mark.parametrize(("flat", "edge", "moved"), [
    ((0.0, 6200.0, 6.0), (0.0, 372.0, 100.0), True),     # the lid: stood up for nothing
    ((20.0, 6200.0, 6.0), (0.0, 372.0, 100.0), True),    # within the tolerance
    ((80.0, 6200.0, 6.0), (0.0, 372.0, 100.0), False),   # flat needs more support: the model's call
    ((0.0, 600.0, 6.0), (0.0, 372.0, 100.0), False),     # not twice as much on the plate
    ((0.0, 6200.0, 100.0), (0.0, 372.0, 100.0), False),  # no lower: not stood up
])
def test_a_pose_stood_up_is_moved_only_where_a_lower_one_beats_it(
    flat: tuple[float, float, float], edge: tuple[float, float, float], moved: bool
) -> None:
    lying = _pose((0.0, 0.0, 0.0), *flat)
    standing = _pose((0.0, 90.0, 0.0), *edge)
    request = OrientationRequest(candidates=(lying, standing), build_volume_mm=PLATE)
    verdict = validate_orientation(
        Chosen(value=standing, source="model:fake", rationale="warping"), request
    )
    rules = [v.rule for v in verdict.blockers]
    assert ("orientation.stood_up" in rules) is moved
    if moved:
        assert verdict.blockers[0].suggested == (0.0, 0.0, 0.0)


# On the plate, a few microns off zero
# --------------------------------------------------------------------------


def _jittered(tmp_path: Path) -> Path:
    """A flat lid, 100 x 62 x 6 mm, its plate face in a 2 mm grid whose vertices are jittered by
    up to +-0.005 mm: a file after Manifold's simplify (mqucifer/print-shop#128)."""
    lid = trimesh.creation.box(extents=(100, 62, 6))
    lid = lid.subdivide_to_size(2.0)
    lid.apply_translation((0, 0, 3))
    rng = np.random.default_rng(128)
    bottom = lid.vertices[:, 2] < 1e-9
    vertices = lid.vertices.copy()
    vertices[bottom, 2] += rng.uniform(-0.005, 0.005, bottom.sum())
    jittered = trimesh.Trimesh(vertices=vertices, faces=lid.faces, process=False)
    path = tmp_path / "jittered_lid.stl"
    jittered.export(path)
    return path


def test_a_plate_face_a_few_microns_off_zero_keeps_its_contact(tmp_path: Path) -> None:
    stl = _jittered(tmp_path)
    assert analyze(stl).bed_contact_area_mm2 == pytest.approx(6200, rel=0.001)
    laid = next(c for c in candidates(stl) if c.rotation_deg == (0.0, 0.0, 0.0))
    assert laid.bed_contact_area_mm2 == pytest.approx(6200, rel=0.001)
    assert laid.overhang_area_mm2 == 0.0, "the jittered face is on the plate, not over the air"


def test_its_contact_reaches_the_lessons_keyed_to_a_large_footprint(tmp_path: Path) -> None:
    from print_shop.setup.traits import traits  # noqa: PLC0415

    stl = _jittered(tmp_path)
    assert "large_bed_contact" in traits(measure(stl, analyze(stl).bed_contact_area_mm2))


def test_a_face_a_tenth_of_a_layer_up_is_not_on_the_plate(tmp_path: Path) -> None:
    """The tolerance is well under a layer: a step 0.02 mm up is the part's, not the plate's."""
    from print_shop.ingest.analyzer import PLATE_MM, on_the_plate  # noqa: PLC0415

    low = trimesh.creation.box(extents=(10, 10, 2))
    low.apply_translation((0, 0, 1))
    high = trimesh.creation.box(extents=(10, 10, 2))
    high.apply_translation((20, 0, 1.02))
    both = trimesh.util.concatenate([low, high])
    assert isinstance(both, trimesh.Trimesh)
    on = on_the_plate(both)
    assert PLATE_MM < 0.02
    assert float(both.area_faces[on].sum()) == pytest.approx(100)


# A face at exactly the limit (mqucifer/print-shop#239)


def _leant_bar(
    path: Path, degrees: float, turn: float = 0.0, at: float = 0.0, height: float = 20.0
) -> trimesh.Trimesh:
    """A bar 60 mm long that widens upward at `degrees` from vertical each side, turned about
    the vertical and moved, through an STL and back: the float32 points a run's mesh carries."""
    out = height * math.tan(math.radians(degrees))
    end = [(-10, 0), (10, 0), (10 + out, height), (-10 - out, height)]
    points = np.array([(x, y, z) for y in (-20.0, 40.0) for x, z in end])
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7)]
    for a in range(4):
        b = (a + 1) % 4
        faces += [(a, b, b + 4), (a, b + 4, a + 4)]
    found = np.array(faces)
    corners = points[found]
    away = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    outward = (away * (corners.mean(axis=1) - points.mean(axis=0))).sum(axis=1) > 0
    found = np.where(outward[:, None], found, found[:, ::-1])
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    points = points @ np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]]) + [at, 2 * at / 3, 0]
    trimesh.Trimesh(points, found, process=False).export(path)
    mesh = trimesh.load_mesh(str(path), force="mesh")
    assert isinstance(mesh, trimesh.Trimesh)
    return mesh


@pytest.mark.parametrize(
    ("turn", "at", "height"),
    [(0, 0, 20), (17, 183.3, 20), (30, 250.1, 20), (45, 254.56, 20), (71, 250.1, 20),
     (135, 183.3, 20), (30, 250.1, 5.5), (71, 440.2, 5.5)],
)  # fmt: skip
def test_a_face_at_exactly_45_is_at_the_limit_however_the_part_is_turned(
    tmp_path: Path, turn: float, at: float, height: float
) -> None:
    """Turned and moved, the same bar read 0, 340, 698 or 1057 mm2 to support, its faces off
    45 degrees by the float32 points' rounding. It's at the limit, and at it needs none."""
    mesh = _leant_bar(tmp_path / "bar.stl", 45.0, turn, at, height)
    assert features.overhang_to_support(mesh)[0] == 0.0
    assert features.overhang_places(mesh) == ()


@pytest.mark.parametrize(("degrees", "area"), [(44.99, 0.0), (45.01, 1369.4), (46.0, 3370.6)])
def test_a_face_past_45_counts_as_it_did(tmp_path: Path, degrees: float, area: float) -> None:
    mesh = _leant_bar(tmp_path / "bar.stl", degrees)
    assert features.overhang_to_support(mesh)[0] == pytest.approx(area, abs=0.2)


@pytest.mark.parametrize(("turn", "at"), [(17, 183.3), (30, 250.1), (45, 254.56), (71, 440.2)])
def test_45_01_still_counts_however_its_turned(tmp_path: Path, turn: float, at: float) -> None:
    assert features.overhang_to_support(_leant_bar(tmp_path / "bar.stl", 45.01, turn, at))[0] > 1000


def test_the_allowance_is_a_float32_meshs_rounding_and_under_a_hundredth_of_a_degree() -> None:
    half_step = float(np.spacing(np.float32(511.0))) / 2
    moved = 2 * math.sqrt(3) * half_step
    turned = math.degrees(moved / (features._ENVELOPE_MM * math.sqrt(2)))
    assert turned <= features._AT_LIMIT_DEG < turned + 1e-4
    assert features._AT_LIMIT_DEG < 0.01
    assert features._OVERHANG_DEG == 45.0
