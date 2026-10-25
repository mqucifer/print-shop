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
from pathlib import Path

import pytest

from print_shop.orchestrator.graph import build_volume_mm
from tests.fakes import STORE_MACHINE


def _profile(path: Path, **fields: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "machine", **fields}))
    return path


# --------------------------------------------------------------------------
# Reading a real profile
# --------------------------------------------------------------------------


def test_reads_the_stores_machine_profile() -> None:
    """The Sponsor's 0.4 nozzle, as imported: Orca's Elegoo profile says 256 mm.

    It was 257 in the Orca this project was ported on; Orca 2.3's Elegoo
    bundle says 256. The store keeps what was imported, so a print no longer
    changes with an Orca update.
    """
    assert build_volume_mm(STORE_MACHINE) == (256.0, 256.0, 256.0)


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
# A whole profile names a parent that is not beside it
# --------------------------------------------------------------------------


def test_a_whole_profile_is_read_without_walking_to_its_parent(tmp_path: Path) -> None:
    """print-shop's profiles keep `inherits` for Orca's compatibility check.

    The system parent it names is not in the store, so the dimensions must be
    read from the file itself, not looked for up the chain. Resolving Orca's
    own tree is the catalog's job, at import (`tests/test_catalog.py`).
    """
    whole = _profile(
        tmp_path / "machine" / "whole.json",
        name="whole",
        inherits="Elegoo Centauri Carbon 0.4 nozzle",
        printable_area=["0x0", "210x0", "210x210", "0x210"],
        printable_height="210",
    )
    assert build_volume_mm(whole) == (210.0, 210.0, 210.0)
