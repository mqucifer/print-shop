"""Build volume resolution, and what happens when the model is unreachable.

Both came out of a multi-pass review of the orientation wiring.

The build volume is a safety parameter: it is what stops the validator accepting
an orientation that will not fit the plate. The original implementation swallowed
every parse failure and returned a hardcoded 257 mm cube, so a real profile in
this Orca installation — `fdm_elegoo_common.json`, which declares
`printable_height: 280` and no `printable_area` — resolved to 257 with no
indication anything had gone wrong. On this printer the fallback happens to be
right, which is worse, not better: the correct answer and the guess are
indistinguishable, so nothing can ever detect the bug.

A build volume you had to guess is not a build volume. It raises now.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from print_shop.orchestrator.graph import build_volume_mm

DATADIR = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
_SYS = DATADIR / "system" / "Elegoo"
ECC = _SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json"

needs_profiles = pytest.mark.skipif(
    not ECC.exists(), reason="the Elegoo Centauri Carbon profiles are not installed"
)


def _profile(path: Path, **fields: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "machine", **fields}))
    return path


# --------------------------------------------------------------------------
# Reading a real profile
# --------------------------------------------------------------------------


@needs_profiles
def test_reads_the_real_elegoo_profile() -> None:
    assert build_volume_mm(ECC) == (257.0, 257.0, 257.0)


def test_reads_a_printer_that_is_not_257(tmp_path: Path) -> None:
    """Proves the parse actually parses.

    On the Centauri Carbon the correct answer equals the old hardcoded fallback,
    so a test against that printer alone cannot tell a working parse from a
    broken one.
    """
    p = _profile(
        tmp_path / "small.json",
        name="small",
        printable_area=["0x0", "180x0", "180x180", "0x180"],
        printable_height="180",
    )
    assert build_volume_mm(p) == (180.0, 180.0, 180.0)


def test_reads_a_non_square_bed(tmp_path: Path) -> None:
    p = _profile(
        tmp_path / "rect.json",
        name="rect",
        printable_area=["0x0", "300x0", "300x200", "0x200"],
        printable_height="400",
    )
    assert build_volume_mm(p) == (300.0, 200.0, 400.0)


# --------------------------------------------------------------------------
# Inheritance
# --------------------------------------------------------------------------


def test_resolves_a_profile_that_inherits_its_dimensions(tmp_path: Path) -> None:
    """User profiles are thin overlays.

    Every profile under `user/` in this installation is a few keys plus
    `inherits`. Requiring the dimensions to be present locally would reject
    every profile a person actually edits in the Orca UI.
    """
    _profile(
        tmp_path / "machine" / "base.json",
        name="base-printer",
        printable_area=["0x0", "220x0", "220x220", "0x220"],
        printable_height="250",
    )
    child = _profile(
        tmp_path / "machine" / "child.json", name="my-printer", inherits="base-printer"
    )
    assert build_volume_mm(child) == (220.0, 220.0, 250.0)


def test_a_child_overrides_its_parent(tmp_path: Path) -> None:
    _profile(
        tmp_path / "machine" / "base2.json",
        name="base2",
        printable_area=["0x0", "220x0", "220x220", "0x220"],
        printable_height="250",
    )
    child = _profile(
        tmp_path / "machine" / "tall.json",
        name="tall",
        inherits="base2",
        printable_height="400",
    )
    assert build_volume_mm(child) == (220.0, 220.0, 400.0)


# --------------------------------------------------------------------------
# Failing loudly
# --------------------------------------------------------------------------


def test_a_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises((FileNotFoundError, ValueError)):
        build_volume_mm(tmp_path / "nope.json")


def test_a_profile_with_no_dimensions_raises_and_names_itself(tmp_path: Path) -> None:
    """`fdm_elegoo_common.json` is exactly this shape: height, no area."""
    p = _profile(tmp_path / "partial.json", name="partial", printable_height="280")
    with pytest.raises(ValueError) as err:
        build_volume_mm(p)
    assert "partial.json" in str(err.value), "the error must say which profile"


def test_an_unresolvable_parent_raises(tmp_path: Path) -> None:
    child = _profile(
        tmp_path / "machine" / "orphan.json", name="orphan", inherits="does-not-exist"
    )
    with pytest.raises(ValueError):
        build_volume_mm(child)


def test_it_never_silently_returns_a_default(tmp_path: Path) -> None:
    """The guard against the original defect returning.

    A hardcoded 257 that appears when parsing fails is undetectable on a 257 mm
    printer, which is the only printer this project has.
    """
    for name, fields in [
        ("empty.json", {}),
        ("height-only.json", {"printable_height": "280"}),
        ("area-only.json", {"printable_area": ["0x0", "200x0", "200x200", "0x200"]}),
        ("junk-area.json", {"printable_area": ["banana"], "printable_height": "200"}),
    ]:
        p = _profile(tmp_path / name, name=name, **fields)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            build_volume_mm(p)


# --------------------------------------------------------------------------
# Cross-tree inheritance — user profiles inherit from the system tree
# --------------------------------------------------------------------------

USER_OVERLAY = DATADIR / "user" / "default" / "machine" / (
    "Elegoo Centauri Carbon 0.4 nozzle - Copy.json"
)


@pytest.mark.skipif(
    not USER_OVERLAY.exists(), reason="no edited user machine profile installed"
)
def test_resolves_a_real_user_profile_that_inherits_from_the_system_tree() -> None:
    """The profile a person actually edits in the Orca UI.

    It lives under `user/default/machine/` and inherits a profile that lives
    under `system/Elegoo/machine/ECC/`. Resolving `inherits` by searching only
    sibling files fails here — and this is the profile someone is most likely
    to point the pipeline at.
    """
    assert build_volume_mm(USER_OVERLAY, datadir=DATADIR) == (257.0, 257.0, 257.0)


def test_resolves_a_parent_in_a_different_subtree(tmp_path: Path) -> None:
    _profile(
        tmp_path / "system" / "Vendor" / "machine" / "XYZ" / "base.json",
        name="vendor-base",
        printable_area=["0x0", "210x0", "210x210", "0x210"],
        printable_height="210",
    )
    child = _profile(
        tmp_path / "user" / "default" / "machine" / "mine.json",
        name="mine",
        inherits="vendor-base",
    )
    assert build_volume_mm(child, datadir=tmp_path) == (210.0, 210.0, 210.0)


def test_an_unresolvable_parent_still_raises_with_a_datadir(tmp_path: Path) -> None:
    child = _profile(
        tmp_path / "user" / "machine" / "orphan2.json", name="o2", inherits="ghost"
    )
    with pytest.raises(ValueError):
        build_volume_mm(child, datadir=tmp_path)
