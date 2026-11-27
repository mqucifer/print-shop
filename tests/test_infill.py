"""A printed part's infill core, outside the images (mqucifer/print-shop#337): its figures from
the published tests, by arithmetic, its stiffness by its lines, and an assembly file's `print`.
The solve itself is in `test_load_printed_cae.py`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import assembly, infill
from print_shop.bench.load import ANISO, _aniso, element_forces, element_stiffness
from print_shop.bench.printed import Settings
from print_shop.bench.report import NoAnswer

PETG = assembly.MATERIALS["petg"]
UP, FIRST, SECOND = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]


def core(pattern: str, density: float, angle: float = 0.0) -> infill.Core:
    return infill.core(Settings(pattern=pattern, infill=density, infill_angle=angle), PETG)


def along(tensor: np.ndarray, degrees: float) -> float:
    way = math.cos(math.radians(degrees)) * FIRST + math.sin(math.radians(degrees)) * SECOND
    return infill.pulled(tensor, way)


def test_each_pattern_s_share_of_its_lines_is_the_least_any_test_of_it_reached() -> None:
    """Ben hadj Hassine et al. 2024, Table 3: grid at 40% with its cells at 45° is "1.28 ± 0.04
    | 78.0 ± 2", the least of grid's against its lines' own reading there; gyroid's and
    triangles' least are theirs."""
    for pattern in ("grid", "gyroid", "triangles"):
        rows = infill.checks(pattern)
        mine = [r for r in rows if r["source"] == "ben-hadj-hassine-2024"]
        assert len(mine) == 9, "three densities, each at three angles"
        assert infill.scale(pattern) == pytest.approx(min(r["scale"] for r in rows))
        for row in rows:
            assert row["scale"] == pytest.approx(row["test_share"] / row["lines_share"])
            # Never stiffer than a test of it.
            assert infill.scale(pattern) * row["lines_share"] <= row["test_share"] + 1e-12
    worst = infill.set_by("grid")
    assert (worst["source"], worst["density_pct"], worst["angle_deg"]) == (
        "ben-hadj-hassine-2024", 40, 45)
    assert worst["test_share"] == pytest.approx(78.0 / 1882)
    assert infill.scale("grid") == pytest.approx(0.218, abs=0.001)
    assert infill.scale("gyroid") == pytest.approx(0.370, abs=0.001)
    assert infill.scale("triangles") == pytest.approx(0.396, abs=0.001)


def test_a_petg_test_whose_angle_isn_t_said_is_read_against_its_lines_stiffest_way() -> None:
    """Alabdli et al. 2026, Table 1: PETG grid at 20%, "18.5 ± 3.7" MPa against its own 100%,
    "418.0 ± 41.8": 0.044 of it, where grid's lines at their stiffest, along one set, are 0.163
    of the solid. So 0.27, above Ben hadj Hassine's least, which stands."""
    rows = {r["density_pct"]: r for r in infill.checks("grid") if r["source"] == "alabdli-2026"}
    assert set(rows) == {20, 40, 60}
    assert rows[20]["test_share"] == pytest.approx(18.5 / 418.0)
    assert rows[20]["lines_share"] == pytest.approx(
        max(infill._lines("grid", 20, a) for a in range(0, 91, 5)))
    assert rows[20]["scale"] == pytest.approx(0.271, abs=0.001)
    assert all(r["scale"] > infill.scale("grid") for r in rows.values())
    assert rows[20]["material"] == "petg"


def test_a_skin_round_a_core_is_an_upper_bound_and_every_core_is_read_under_it() -> None:
    """Racz and Dudescu 2022: ABS rectilinear at 20%, 982.0 MPa over its 40 mm², its core
    (23.85 - 20.50) / 0.2 = 16.75 mm² of it, the rest skin at its own 100%'s 1521.8:
    (982.0 · 40 - 1521.8 · 23.25) / 16.75 = 232.7 MPa, 0.153 of the solid."""
    assert infill.backed_out(982.0, 1521.8, 40.0, 23.25) == pytest.approx(232.7 / 1521.8,
                                                                          rel=1e-3)
    for pattern in ("rectilinear", "zigzag", "line", "crosshatch", "grid"):
        found = infill.bounds(pattern)
        assert found, pattern
        for bound in found:
            assert 0 < bound["read_share"] < bound["bound_share"], (pattern, bound)
    assert infill.bounds("gyroid") == [] and infill.bounds("triangles") == []
    # What's checked, and from what density: grid and the lines by PETG at 20% and the
    # bounds; gyroid and triangles only by the tests of a core alone, from 40%.
    assert infill.checked_from("grid") == infill.checked_from("rectilinear") == 20
    assert infill.checked_from("gyroid") == infill.checked_from("triangles") == 40


def test_the_strain_a_core_takes_is_the_tests_yield_over_their_modulus_at_the_least() -> None:
    """Grid at 40%, its cells at 0°: "1.84 ± 0.09 | 344.5 ± 7.5", 0.534%, against the tested
    PLA's "tensile strength of 20.9 MPa, tensile modulus of 1882 Mpa", 1.11%: 0.481 of it. In
    PETG that's 0.481 of its 47 MPa yield over its 1.5 GPa, 1.51%, and of its 1% across."""
    share = (1.84 / 344.5) / (20.9 / 1882)
    assert infill.strain_share("grid") == pytest.approx(share)
    grid = core("grid", 30)
    assert grid.along_pct == pytest.approx(100 * share * 47 / 1500)
    assert grid.across_pct == pytest.approx(share * PETG.across_pct)
    assert grid.along_pct < PETG.along_pct and grid.across_pct < PETG.across_pct
    # The line patterns take grid's, as the nearest tested: the same straight lines.
    for pattern in ("rectilinear", "zigzag", "line", "crosshatch", "alignedrectilinear"):
        assert core(pattern, 30).strain_share == infill.strain_share("grid")
        assert core(pattern, 30).scale == infill.scale("grid")


@pytest.mark.parametrize("pattern", sorted(infill.LINES))
@pytest.mark.parametrize("density", [10, 20, 40, 60])
def test_a_core_s_stiffness_is_symmetric_and_holds_every_way(pattern: str, density: int) -> None:
    tensor = core(pattern, density, 30).stiffness(UP, FIRST, SECOND)
    assert np.allclose(tensor, tensor.transpose(1, 0, 2, 3))
    assert np.allclose(tensor, tensor.transpose(2, 3, 0, 1))
    matrix = np.array([[tensor[i, j, k, m] for k, m in infill._VOIGT] for i, j in infill._VOIGT])
    assert np.linalg.eigvalsh(matrix).min() > 0
    # Never stiffer than the solid any way, and denser is stiffer.
    for way in (FIRST, SECOND, UP, (FIRST + SECOND) / math.sqrt(2)):
        assert infill.pulled(tensor, way) < PETG.modulus_mpa


def test_a_pattern_strengthens_most_along_its_lines() -> None:
    """Rectilinear's lines run at its angle and square to it: stiffest along them, and least at
    45° to both. Gyroid is the same every way. Grid's lines are laid on the same lines each
    layer, so they wall it through its layers, stiffer across them than rectilinear's, whose
    lines meet the next layer's only where they cross."""
    lines = core("rectilinear", 30, 0).stiffness(UP, FIRST, SECOND)
    assert along(lines, 0) == pytest.approx(along(lines, 90))
    assert along(lines, 0) > 2 * along(lines, 45)
    turned = core("rectilinear", 30, 45).stiffness(UP, FIRST, SECOND)
    assert along(turned, 45) == pytest.approx(along(lines, 0))
    gyroid = core("gyroid", 30).stiffness(UP, FIRST, SECOND)
    assert along(gyroid, 0) == pytest.approx(along(gyroid, 33))
    assert along(gyroid, 0) == pytest.approx(infill.pulled(gyroid, UP))
    grid = core("grid", 30).stiffness(UP, FIRST, SECOND)
    assert infill.pulled(grid, UP) > 3 * infill.pulled(lines, UP)
    for low, high in ((10, 20), (20, 40), (40, 60)):
        for pattern in ("rectilinear", "grid", "gyroid", "triangles"):
            soft = core(pattern, low).stiffness(UP, FIRST, SECOND)
            stiff = core(pattern, high).stiffness(UP, FIRST, SECOND)
            assert along(stiff, 0) > along(soft, 0)


def test_a_core_s_largest_stretch_is_said_against_its_lines_and_layers() -> None:
    grid = core("grid", 40, 0)
    strain = np.zeros((3, 3, 3))
    strain[0, 0, 0] = 0.001  # along its first lines
    strain[1] = 0.0004 * np.outer(FIRST + SECOND, FIRST + SECOND) / 2  # at 45° to both
    strain[2, 2, 2] = 0.0005  # across its layers
    read = grid.read(strain, UP, FIRST, SECOND)
    assert read["stretch_at"] == 0 and read["stretch_pct"] == pytest.approx(0.1)
    assert read["stretch_from_lines_deg"] == pytest.approx(0, abs=1e-6)
    assert read["across_at"] == 2 and read["across_layers_pct"] == pytest.approx(0.05)
    assert read["stretch_limit_pct"] == pytest.approx(round(grid.along_pct, 4))
    strain[0, 0, 0] = 0.0
    read = grid.read(strain, UP, FIRST, SECOND)
    assert read["stretch_at"] == 2 and read["stretch_from_layers_deg"] == pytest.approx(90)
    strain[2, 2, 2] = 0.0
    read = grid.read(strain, UP, FIRST, SECOND)
    assert read["stretch_from_lines_deg"] == pytest.approx(45)


@pytest.mark.parametrize(
    ("settings", "says"),
    [
        (Settings(pattern="honeycomb", infill=30, infill_angle=0), "isn't read as printed yet"),
        (Settings(pattern="grid", infill=80, infill_angle=0), "read solid"),
        (Settings(pattern="grid", infill=5, infill_angle=0), "from 10% to 60%"),
    ],
)
def test_a_pattern_or_density_no_test_reads_is_refused(settings: Settings, says: str) -> None:
    with pytest.raises(NoAnswer, match=says):
        infill.core(settings, PETG)


def test_an_element_s_own_stiffness_reads_as_the_material_s_where_it_s_the_same() -> None:
    """The balance at each node (`element_forces`) with each element's stiffness given as
    C_ijkl is the one with the material's modulus and Poisson's ratio, where that's isotropic."""
    rng = np.random.default_rng(0)
    corners = np.array([[0.0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]])
    places = np.concatenate([corners, [(corners[i] + corners[j]) / 2 for i, j in
                                       ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))]])
    places = np.stack([places, places * 1.5 + 1])
    moved = rng.normal(scale=1e-3, size=places.shape)
    tensor = np.stack([infill.isotropic(1500.0, 0.38)] * 2)
    assert np.allclose(element_forces(places, moved, 1500.0, 0.38),
                       element_forces(places, moved, 0.0, 0.0, tensor))
    assert np.allclose(element_stiffness(places, 1500.0, 0.38),
                       element_stiffness(places, 0.0, 0.0, tensor))


def test_a_core_s_stiffness_is_written_as_calculix_s_21_anisotropic_constants() -> None:
    tensor = core("grid", 40, 30).stiffness(UP, FIRST, SECOND)
    written = _aniso(tensor).splitlines()
    assert written[0] == "*ELASTIC, TYPE=ANISO"
    values = [float(v) for line in written[1:] for v in line.split(",")]
    assert [len(line.split(",")) for line in written[1:]] == [8, 8, 5]
    for key, value in zip(ANISO, values, strict=True):
        assert value == pytest.approx(tensor[tuple(int(c) - 1 for c in key)], rel=1e-8)


def test_an_assembly_part_reads_its_print_and_a_part_without_one_reads_solid(
    tmp_path: Path,
) -> None:
    (tmp_path / "bar.step").write_text("")
    given = {"walls": 2, "line_width": 0.45, "top": 5, "bottom": 3, "infill": 20,
             "pattern": "grid", "infill_angle": 0, "layer_height": 0.2,
             "regions": [{"name": "root", "box": [[0, 0, 0], [30, 20, 10]], "infill": 60}]}
    parts: list[dict[str, Any]] = [
        {"name": "bar", "file": "bar.step", "layers": "z", "print": given},
        {"name": "plain", "file": "bar.step", "at": [0, 30, 0], "layers": "z"},
    ]
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"parts": parts}))
    read = assembly.read(str(path))
    printed = read.part("bar").printed
    assert printed is not None and printed.settings.infill == 20
    assert printed.regions[0].name == "root" and printed.regions[0].settings.infill == 60
    assert read.part("plain").printed is None
    parts[0]["print"] = {**given, "infill": 120}
    path.write_text(json.dumps({"parts": parts}))
    with pytest.raises(NoAnswer, match="the part bar's print's infill"):
        assembly.read(str(path))
