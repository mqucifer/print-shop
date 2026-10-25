"""Specification for orientation candidates and the fitness function that scores them.

`AGENTS.md` rule 2: the fitness function comes before the judgment node. This
file is that fitness function. No model chooses anything yet.

The tee is the case that makes it checkable by hand. As authored it rests on a
10x10 pillar with a 20x20 slab overhanging it; flipped 180 degrees about X the
slab is on the plate and nothing overhangs:

    rotation        overhang    bed contact
    (0, 0, 0)        300 mm2      100 mm2
    (180, 0, 0)        0 mm2      400 mm2      <- correct answer
    (90, 0, 0)       100 mm2      200 mm2

**The scoring slice must enable supports.** With the stock Elegoo profile,
support generation is off, so Orca extrudes the overhang into mid-air and both
orientations come out at 850 s / 2.39 cm3 — the fitness function reports nothing
and the whole exercise is blind. With supports on the signal is unmissable:
1555 s / 3.130 cm3 against 918 s / 2.390 cm3.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from print_shop.core.state import Orientation
from print_shop.ingest.orientation import AXIS_ALIGNED, candidates, write_rotated
from print_shop.orchestrator.fitness import scoring_profiles, slice_candidate
from print_shop.slicer.orca import SlicerProfiles
from tests.fixtures import write_stl

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
DATADIR = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
_SYS = DATADIR / "system" / "Elegoo"
MACHINE = _SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json"
PROCESS = _SYS / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json"
FILAMENT = _SYS / "filament" / "ECC" / "Elegoo PLA @ECC.json"
TOL = 1e-3

needs_orca = pytest.mark.skipif(
    not (ORCA.exists() and MACHINE.exists() and PROCESS.exists()),
    reason="OrcaSlicer or the Elegoo Centauri Carbon profiles are not installed",
)


@pytest.fixture
def profiles() -> SlicerProfiles:
    return SlicerProfiles(
        machine=MACHINE,
        process=PROCESS,
        filament=FILAMENT if FILAMENT.exists() else None,
        datadir=DATADIR,
    )


# --------------------------------------------------------------------------
# Candidate enumeration and deterministic scoring
# --------------------------------------------------------------------------


def test_enumerates_the_six_axis_aligned_orientations(tmp_path: Path) -> None:
    got = candidates(write_stl("tee", tmp_path))
    assert len(got) == len(AXIS_ALIGNED) == 6
    assert all(isinstance(c, Orientation) for c in got)
    assert {c.rotation_deg for c in got} == set(AXIS_ALIGNED)


def test_scores_the_tee_exactly(tmp_path: Path) -> None:
    """Every number here is derived on paper. See the module docstring."""
    by_rot = {c.rotation_deg: c for c in candidates(write_stl("tee", tmp_path))}

    assert by_rot[(0.0, 0.0, 0.0)].overhang_area_mm2 == pytest.approx(300.0, abs=TOL)
    assert by_rot[(0.0, 0.0, 0.0)].bed_contact_area_mm2 == pytest.approx(100.0, abs=TOL)

    assert by_rot[(180.0, 0.0, 0.0)].overhang_area_mm2 == pytest.approx(0.0, abs=TOL)
    assert by_rot[(180.0, 0.0, 0.0)].bed_contact_area_mm2 == pytest.approx(400.0, abs=TOL)

    assert by_rot[(90.0, 0.0, 0.0)].overhang_area_mm2 == pytest.approx(100.0, abs=TOL)
    assert by_rot[(90.0, 0.0, 0.0)].bed_contact_area_mm2 == pytest.approx(200.0, abs=TOL)


def test_the_flipped_tee_is_the_unique_best_on_overhang(tmp_path: Path) -> None:
    got = candidates(write_stl("tee", tmp_path))
    best = min(got, key=lambda c: c.overhang_area_mm2)
    assert best.rotation_deg == (180.0, 0.0, 0.0)
    assert sum(c.overhang_area_mm2 == 0.0 for c in got) == 1


def test_a_cube_has_no_preferred_orientation(tmp_path: Path) -> None:
    """The degenerate case. Six identical candidates and no geometric reason to
    pick any of them — exactly where a model's judgment has to come from
    something other than the numbers."""
    got = candidates(write_stl("cube", tmp_path))
    assert {c.overhang_area_mm2 for c in got} == {0.0}
    assert {c.bed_contact_area_mm2 for c in got} == {400.0}


def test_every_candidate_reports_its_own_height(tmp_path: Path) -> None:
    for c in candidates(write_stl("tee", tmp_path)):
        assert c.height_mm > 0


def test_write_rotated_produces_a_seated_mesh(tmp_path: Path) -> None:
    """Rotated STLs are written sitting on the plate, ready to slice."""
    import trimesh

    src = write_stl("tee", tmp_path)
    out = write_rotated(src, (180.0, 0.0, 0.0), tmp_path / "flipped.stl")
    assert out.exists()
    m = trimesh.load(out, force="mesh")
    assert isinstance(m, trimesh.Trimesh)
    assert m.bounds[0][2] == pytest.approx(0.0, abs=TOL)
    assert m.volume == pytest.approx(5000.0, abs=1.0)


# --------------------------------------------------------------------------
# The fitness function
# --------------------------------------------------------------------------


@pytest.mark.slicer
@needs_orca
def test_scoring_profiles_enable_support_generation(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """Without this the fitness function cannot see an overhang at all."""
    scoring = scoring_profiles(profiles, tmp_path)
    import json

    cfg = json.loads(Path(scoring.process).read_text())
    assert cfg["enable_support"] in ("1", 1, True)
    assert cfg.get("compatible_printers"), "Orca rejects a process with no compatible_printers"


@pytest.mark.slicer
@needs_orca
def test_slicing_rewards_the_better_orientation(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """The whole point. The flipped tee needs no support and must measure cheaper.

    Numbers observed on this machine: 1555 s / 3.130 cm3 as authored against
    918 s / 2.390 cm3 flipped. Asserted as a margin, not equality, so a slicer
    or profile update does not produce a spurious failure.
    """
    stl = write_stl("tee", tmp_path)
    scoring = scoring_profiles(profiles, tmp_path)

    authored = slice_candidate(stl, (0.0, 0.0, 0.0), scoring, tmp_path / "a")
    flipped = slice_candidate(stl, (180.0, 0.0, 0.0), scoring, tmp_path / "b")

    assert flipped.filament_cm3 < authored.filament_cm3 * 0.95
    assert flipped.print_time_s < authored.print_time_s * 0.9


@pytest.mark.slicer
@needs_orca
def test_the_fitness_function_agrees_with_the_geometry(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """Lowest overhang by geometry must also be cheapest by slicing.

    If these two ever disagree, the geometric score is not a proxy for cost and
    the orientation agent would be optimising the wrong thing.
    """
    stl = write_stl("tee", tmp_path)
    scoring = scoring_profiles(profiles, tmp_path)
    cands = candidates(stl)

    best_by_geometry = min(cands, key=lambda c: c.overhang_area_mm2)
    sliced = {
        c.rotation_deg: slice_candidate(stl, c.rotation_deg, scoring, tmp_path / str(i))
        for i, c in enumerate(cands)
    }
    best_by_slicing = min(sliced, key=lambda r: sliced[r].filament_cm3)

    assert best_by_geometry.rotation_deg == best_by_slicing
