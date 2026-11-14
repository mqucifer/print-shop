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

from pathlib import Path

import numpy as np
import pytest
import trimesh

from print_shop.core.contracts import Proposal as Chosen
from print_shop.core.contracts import Severity, propose_validate
from print_shop.core.state import AppliedOrientation
from print_shop.expert.orientation import (
    OrientationRequest,
    propose_geometric,
    validate_orientation,
)
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import candidates
from print_shop.orchestrator import graph
from print_shop.setup.features import FLARE_MM, measure
from print_shop.setup.review import ReviewRequest, render
from tests.fixtures import _profile, write_stl
from tests.test_features import _guarded
from tests.test_review import _request

#: The CC1's build volume.
PLATE = (256.0, 256.0, 256.0)


def _by_rotation(stl: Path) -> dict[tuple[float, float, float], object]:
    return {c.rotation_deg: c for c in candidates(stl)}


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
    assert "  the poses it weighed (overhang is what would need support):" in shown
    assert ("        0,0,0  0 mm2 overhang, 571.7 mm2 rising from the plate left out, 4968 mm2 "
            "on the plate, 12 mm tall") in shown
    assert "       0,90,0  0 mm2 overhang, 108.3 mm2 rising from the plate left out, 496 mm2 on " \
           "the plate, 100 mm tall  <- this print" in shown
