"""An infill core's stiffness and what strain it takes, by its pattern, density and direction
(mqucifer/print-shop#337).

`load` reads a printed part as a solid skin round a core (`as_printed.py`). The core is the
pattern's lines, so its stiffness is theirs: what each line carries along its own run, and, where
a pattern stacks its lines on the same lines each layer, the walls they build up through the
layers; with what the lattice gives besides by bending, which every pattern has. Which lines a
pattern lays, which way and how densely, is Orca 2.4.2's own code (`docs/bench.md` quotes it):

- **rectilinear, zigzag, line and crosshatch** lay one set of parallel lines in each layer, turned
  90° each layer (`FillBase.hpp`'s `_layer_angle`; crosshatch every few layers), so half the
  core's lines run along `infill_angle` and half square to it, and a line meets the next layer's
  only where they cross: no wall builds up through the layers;
- **alignedrectilinear** lays the same lines every layer, so they build walls along its angle;
- **grid** lays both directions in every layer, the density shared between them
  (`FillRectilinear.cpp`, `params.density /= double(sweep_params.size())`), the same each layer:
  two sets of walls square to each other;
- **triangles** lays three directions 60° apart in every layer, the same each layer: three sets of
  walls, a triangulated lattice in the layers' plane;
- **gyroid** turns its wave with the height and isn't lines: it's read as bending alone.

A line of the pattern, at density ρ, takes ρ of the area across its layer (`line_spacing =
spacing / density`), so a set holding a share s of the core's lines is stiff as s·ρ·E along its
run: Ashby's stretch-dominated struts (2006, eq. 5.1, with each set's own share, not his third
for struts every way). A wall is the same plastic in the plane of its lines and the layers,
s·ρ·E in that plane. Every pattern bends between its lines besides, as ρ²·E every way
(Ashby's eq. 3.3, bending-dominated, "the constant of proportionality ... close to unity"):
that's all a gyroid is read as here.

**Published tests set its figures** (`TESTS`, the main source): the cleanest open tests of an
infill core alone, Ben hadj Hassine et al. 2024 (*Polymers* 16(11):1562, PLA, "Top and bottom
solid layer 0 | Shell number 0", gyroid, grid and triangles at 40, 50 and 60%, each with its
cells at 0°, 45° and 90° to the pull). No open test has a PETG core with no walls and no
shells, so the tests' figures are taken as shares of their own plastic's, as Gibson and Ashby
take a lattice's (its stiffness over its solid's, by its density): their PLA's "tensile
strength of 20.9 MPa, tensile modulus of 1882 Mpa". Against them the lines' own figures above
read stiffer, by up to 4.6 times: a coupon with no walls has every line cut open at its sides.
So each pattern's core is held to the least share of its lines' stiffness any test of it
reached, at the same density and the same angle to its lines (`scale`), so it's never read
stiffer than a test of it; and it takes as much strain as the tests' own yield over their
modulus, as a share of their plastic's, at the least (`strain_share`): against PETG's own,
"Tensile Yield Strength [MPa] 47 ± 2" over its 1.5 GPa in the layers' plane, and its 1% across
them. The patterns no test here reads alone, rectilinear, zigzag, line, crosshatch and
alignedrectilinear, take grid's: the same straight lines, crossed. A second test of a core
alone, Alabdli et al. 2026 (*Polymers* 18:2299, PETG grid at 20 to 60%, its angle not said, so
read against grid's lines their stiffest way), reaches more of its lines, and sets nothing; it
checks grid from 20%. Tests of a skin round a core read the core too stiff, as their own 100%
has its solid infill at an angle, softer than walls along the pull: they're upper bounds only
(`BOUNDS`), and every core is read under each. Each core says which test set its figures and
from what density any test checks it (`set_by`, `checked_from`).

**The second method** is the lines' figures themselves, beside each test (`checks`) and each
bound (`bounds`), so every figure has its test and its lines' reading, with how far apart.

Plain Python and NumPy: the cae image reads it in `load`, and the tests in the first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from .printed import Settings

#: The patterns read here, each as Orca lays its lines: each set's angle off `infill_angle`, in
#: degrees in the layers' plane, its share of the core's lines, and whether it's laid on the same
#: lines each layer, so building a wall through the layers.
LINES: dict[str, tuple[tuple[float, float, bool], ...]] = {
    "rectilinear": ((0.0, 0.5, False), (90.0, 0.5, False)),
    "zigzag": ((0.0, 0.5, False), (90.0, 0.5, False)),
    "line": ((0.0, 0.5, False), (90.0, 0.5, False)),
    "crosshatch": ((0.0, 0.5, False), (90.0, 0.5, False)),
    "alignedrectilinear": ((0.0, 1.0, True),),
    "grid": ((0.0, 0.5, True), (90.0, 0.5, True)),
    "triangles": ((0.0, 1 / 3, True), (60.0, 1 / 3, True), (120.0, 1 / 3, True)),
    "gyroid": (),
}
#: The published tests the figures are taken from, each with its own words for its plastic, and
#: the figure each share is of: the study's own solid, as its own tests measured it.
@dataclass(frozen=True)
class Source:
    key: str
    said: str
    material: str
    #: The study's solid plastic's stiffness and strength, in MPa, that its shares are of.
    solid_mpa: float
    strength_mpa: float


SOURCES = {
    s.key: s
    for s in (
        Source(
            "ben-hadj-hassine-2024",
            "Ben hadj Hassine, Chatti, Louhichi and Seibi (2024), \"Experimental Study of the "
            "Tensile Behavior of Structures Obtained by FDM 3D Printing Process\", Polymers "
            "16(11):1562, doi:10.3390/polym16111562: PLA, \"Top and bottom solid layer 0 | Shell "
            "number 0\", with an extensometer, its plastic's \"tensile strength of 20.9 MPa, "
            "tensile modulus of 1882 Mpa\"",
            "pla", 1882.0, 20.9,
        ),
        Source(
            "alabdli-2026",
            "Alabdli, Jalal, Alqarni, Basalah and Damiati (2026), \"Influence of Infill Density "
            "on Bacterial Colonization and Mechanical Performance of 3D-Printed PETG "
            "Scaffolds\", Polymers 18:2299, doi:10.3390/polym18182299: PETG grid, its Table 1, "
            "strain \"from the crosshead displacement, corrected for machine compliance\", "
            "stress over \"the original gross cross-sectional area\", its walls and shells not "
            "said, its own 100% \"418.0 ± 41.8\" MPa and \"12.50 ± 0.75\" MPa",
            "petg", 418.0, 12.50,
        ),
    )
}
#: Each test of a core alone: its source, its pattern, its density in percent, the angle of the
#: pull to its lines in degrees, or None where the source doesn't say, and its strength and
#: modulus in MPa, as printed. Ben hadj Hassine's Table 3, "Yield Strength (MPa)" and "Elastic
#: Modulus (MPa)" by its Table 2's levels, "Honeycomb Gyroid Grid Triangles", "40 50 60" and
#: cell orientation "0 45 90"; and Alabdli's Table 1, "UTS (MPa)" and "Young's Modulus (MPa)".
TESTS: tuple[tuple[str, str, float, float | None, float, float], ...] = tuple(
    ("ben-hadj-hassine-2024", *row) for row in (
        ("grid", 40, 0, 1.84, 344.5), ("grid", 40, 45, 1.28, 78.0), ("grid", 40, 90, 1.84, 344.5),
        ("grid", 50, 0, 3.60, 605.0), ("grid", 50, 45, 2.72, 272.0), ("grid", 50, 90, 3.60, 605.0),
        ("grid", 60, 0, 4.49, 773.5), ("grid", 60, 45, 2.91, 403.0), ("grid", 60, 90, 4.49, 773.5),
        ("gyroid", 40, 0, 2.10, 227.5), ("gyroid", 40, 45, 1.34, 111.5),
        ("gyroid", 40, 90, 1.93, 210.0), ("gyroid", 50, 0, 3.35, 514.5),
        ("gyroid", 50, 45, 1.86, 238.5), ("gyroid", 50, 90, 2.67, 434.5),
        ("gyroid", 60, 0, 3.80, 488.0), ("gyroid", 60, 45, 3.66, 360.0),
        ("gyroid", 60, 90, 4.55, 685.0),
        ("triangles", 40, 0, 2.56, 411.0), ("triangles", 40, 45, 1.73, 483.0),
        ("triangles", 40, 90, 1.84, 325.0), ("triangles", 50, 0, 3.33, 339.5),
        ("triangles", 50, 45, 2.91, 446.5), ("triangles", 50, 90, 4.00, 616.5),
        ("triangles", 60, 0, 4.67, 624.0), ("triangles", 60, 45, 4.29, 646.0),
        ("triangles", 60, 90, 4.43, 696.5),
    )
) + tuple(
    ("alabdli-2026", "grid", density, None, strength, modulus)
    for density, strength, modulus in ((20, 1.27, 18.5), (40, 2.79, 47.9), (60, 5.72, 133.6))
)


def backed_out(apparent: float, solid: float, area: float, skin: float) -> float:
    """A core's stiffness as a share of the solid's, from a test of a skin round it: the
    specimen's apparent modulus over its gross area, less its skin's, solid, over its
    skin's area, over what's left. It reads the core stiffer than it is: a test's own 100%
    has its solid infill at its pattern's angle, softer than walls along the pull, so the
    walls are read too soft. It's an upper bound only (`BOUNDS`)."""
    return (apparent * area - solid * skin) / (area - skin) / solid


#: Tests of a skin round a core, as upper bounds a core is held under at its density and its
#: angle to the pull, each as its source's own figures give it:
#: - Racz and Dudescu (2022), "Numerical Investigation of the Infill Rate upon Mechanical
#:   Proprieties of 3D-Printed Materials", Polymers 14:2022, doi:10.3390/polym14102022: ABS,
#:   rectilinear "0° orientation of the pattern with respect to the tensile direction", "A
#:   uniaxial extensometer was used", "(4 mm thickness, 10 mm width; ... cross-section area of
#:   40 mm2)"; Table 4's EXP, "20% | 982.0", "40% | 1177.5", "100% | 1521.8"; and Table 2's
#:   material in the section, "20% | 20.50" and "40% | 23.85" mm², so the core's gross area is
#:   (23.85 - 20.50) / 0.2, 16.75 mm², and the skin's the rest.
#: - Rismalia et al. (2019), "Infill pattern and density effects on the tensile properties of
#:   3D printed PLA material", J. Phys.: Conf. Ser. 1402:044041, doi:10.1088/1742-6596/1402/
#:   4/044041: PLA, "Wall Thickness 1.2 mm", "Top/Bottom Thickness 0.6 mm", strain gauges,
#:   grid "45 degrees angle with respect to the axial axis", Table 2 "Grid 25 % 2.76 ± 0.38"
#:   GPa, and "grid pattern with 100% infill density ... 3.6 GPa"; ASTM D638 Type I, 13 mm
#:   wide, its thickness not said: 3.2 mm, which of the two it might be reads the lower bound.
BOUNDS: tuple[tuple[str, str, float, float, float], ...] = (
    ("racz-dudescu-2022", "rectilinear", 20, 0.0,
     backed_out(982.0, 1521.8, 40.0, 40.0 - (23.85 - 20.50) / 0.2)),
    ("racz-dudescu-2022", "rectilinear", 40, 0.0,
     backed_out(1177.5, 1521.8, 40.0, 40.0 - (23.85 - 20.50) / 0.2)),
    ("rismalia-2019", "grid", 25, 45.0,
     backed_out(2760.0, 3600.0, 13 * 3.2, 13 * 3.2 - (13 - 2.4) * (3.2 - 1.2))),
)
#: Which tested pattern stands for each read here: the line patterns take grid's, the same
#: straight lines, crossed; and which a bound of it is read against: the same lines.
TESTED_AS = {
    "rectilinear": "grid", "zigzag": "grid", "line": "grid", "crosshatch": "grid",
    "alignedrectilinear": "grid", "grid": "grid", "triangles": "triangles", "gyroid": "gyroid",
}
BOUNDED_AS = {
    "rectilinear": "rectilinear", "zigzag": "rectilinear", "line": "rectilinear",
    "crosshatch": "rectilinear", "grid": "grid",
}
#: Its Poisson's ratio isn't given: the lines' figures are read with PETG's, `bend`'s 0.38.
#: Prusament PETG's datasheet (version 1.1, 16-02-2022): "Tensile Yield Strength [MPa] 47 ± 2"
#: for a specimen printed flat, over its tensile modulus, 1.5 GPa: 3.13%, what the tests' shares
#: of their plastic's strength over its modulus are taken of, in the layers' plane.
YIELD_MPA = 47.0
#: The least and most density read, in percent. Past what a pattern's tests and bounds reach
#: (`checked_from`) the core is the lines' figures at the tests' share, which nothing checks.
LEAST, MOST = 10.0, 60.0


def _outer(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.asarray(np.einsum("i,j->ij", a, b))


def isotropic(modulus: float, poisson: float) -> np.ndarray:
    """An isotropic material's stiffness, C_ijkl, in MPa."""
    lame = modulus * poisson / ((1 + poisson) * (1 - 2 * poisson))
    shear = modulus / (2 * (1 + poisson))
    eye = np.eye(3)
    return np.asarray(
        lame * np.einsum("ij,kl->ijkl", eye, eye)
        + shear * (np.einsum("ik,jl->ijkl", eye, eye) + np.einsum("il,jk->ijkl", eye, eye))
    )


def _strut(way: np.ndarray, stiff: float) -> np.ndarray:
    """A set of lines along `way`, stiff as `stiff` MPa along it and nothing else."""
    d = _outer(way, way)
    return np.asarray(stiff * np.einsum("ij,kl->ijkl", d, d))


def _wall(way: np.ndarray, up: np.ndarray, stiff: float, poisson: float) -> np.ndarray:
    """A wall in the plane of `way` and `up`, plastic as stiff as `stiff` MPa in plane stress."""
    found = np.zeros((3, 3, 3, 3))
    frame = (way, up)
    scale = stiff / (1 - poisson**2)
    plane = {(0, 0, 0, 0): 1.0, (1, 1, 1, 1): 1.0, (0, 0, 1, 1): poisson, (1, 1, 0, 0): poisson}
    for (i, j, k, m), value in plane.items():
        found += scale * value * np.einsum(
            "a,b,c,d->abcd", frame[i], frame[j], frame[k], frame[m]
        )
    shear = stiff / (2 * (1 + poisson))
    for i, j in ((0, 1), (1, 0)):
        for k, m in ((0, 1), (1, 0)):
            found += shear * np.einsum("a,b,c,d->abcd", frame[i], frame[j], frame[k], frame[m])
    return found


_VOIGT = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))


def pulled(tensor: np.ndarray, way: np.ndarray) -> float:
    """A stiffness's Young's modulus pulled along `way`, in MPa: the pull over the stretch
    along it, the material free every other way."""
    matrix = np.array([[tensor[i, j, k, m] for k, m in _VOIGT] for i, j in _VOIGT])
    pull = np.outer(way, way)
    stretch = np.linalg.solve(matrix, np.array([pull[i, j] for i, j in _VOIGT]))
    strain = np.zeros((3, 3))
    for n, (i, j) in enumerate(_VOIGT):
        strain[i, j] = strain[j, i] = stretch[n] if n < 3 else stretch[n] / 2
    return float(1.0 / (way @ strain @ way))


def lines_stiffness(
    pattern: str, density_pct: float, angle_deg: float, solid_mpa: float, poisson: float,
    up: np.ndarray, first: np.ndarray, second: np.ndarray,
) -> np.ndarray:
    """A core's stiffness by its lines alone, C_ijkl in MPa, before any test's share: each
    set's s·ρ·E along its run, or in its wall's plane, and ρ²·E every way as it bends."""
    rho = density_pct / 100
    found = isotropic(solid_mpa * rho**2, poisson)
    for offset, share, walls in LINES[pattern]:
        angle = math.radians(angle_deg + offset)
        way = math.cos(angle) * first + math.sin(angle) * second
        way = way / np.linalg.norm(way)
        stiff = solid_mpa * rho * share
        found += _wall(way, up, stiff, poisson) if walls else _strut(way, stiff)
    return found


_FLAT = (np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))


def _lines(pattern: str, density: float, angle: float | None, poisson: float = 0.38) -> float:
    """A core's stiffness by its lines alone, as a share of its solid's, pulled at `angle` to
    its lines in the layers' plane; at None, the way it's stiffest, so a test whose angle isn't
    said reads as the least share of its lines it could be."""
    up, first, second = _FLAT
    tensor = lines_stiffness(pattern, density, 0.0, 1.0, poisson, up, first, second)
    angles = [angle] if angle is not None else [float(a) for a in range(0, 91, 5)]
    return max(
        pulled(tensor, math.cos(math.radians(a)) * first + math.sin(math.radians(a)) * second)
        for a in angles
    )


def checks(tested: str, poisson: float = 0.38) -> list[dict[str, Any]]:
    """Each test of a pattern beside its lines' reading, at the test's density, pulled at the
    test's angle to its lines in the layers' plane, or the way they're stiffest where it isn't
    said: each as a share of its own source's solid, and the test's strength over its
    modulus as a share of its solid's."""
    found = []
    for key, pattern, density, angle, strength, modulus in TESTS:
        if pattern != tested:
            continue
        source = SOURCES[key]
        lines = _lines(pattern, density, angle, poisson)
        share = modulus / source.solid_mpa
        found.append({
            "source": key,
            "material": source.material,
            "density_pct": density,
            "angle_deg": angle,
            "test_share": share,
            "lines_share": lines,
            "scale": share / lines,
            "strain_share": (strength / modulus) / (source.strength_mpa / source.solid_mpa),
        })
    return found


def set_by(tested: str, figure: str = "scale") -> dict[str, Any]:
    """The test that sets a pattern's `scale` or `strain_share`: the least of all its tests."""
    return min(checks(tested), key=lambda c: float(c[figure]))


def scale(tested: str) -> float:
    """The least share of its lines' stiffness any test of a pattern reached, at most 1: what
    the core's lines' stiffness is held to, so it's read no stiffer than any test of it."""
    return min(1.0, float(set_by(tested)["scale"]))


def strain_share(tested: str) -> float:
    """The least of a pattern's tests' strength over their modulus, as a share of their
    plastic's, at most 1: the share of the solid's strain its core takes."""
    return min(1.0, float(set_by(tested, "strain_share")["strain_share"]))


def bounds(pattern: str) -> list[dict[str, Any]]:
    """Each upper bound on a pattern's core (`BOUNDS`), beside what's read for it there."""
    found = []
    for key, bounded, density, angle, share in BOUNDS:
        if BOUNDED_AS.get(pattern) != bounded:
            continue
        read = scale(TESTED_AS[pattern]) * _lines(bounded, density, angle)
        found.append({"source": key, "density_pct": density, "angle_deg": angle,
                      "bound_share": share, "read_share": read})
    return found


def checked_from(pattern: str) -> float:
    """The least density any test or bound of a pattern reaches, in percent."""
    tested = [c["density_pct"] for c in checks(TESTED_AS[pattern])]
    return float(min(tested + [b["density_pct"] for b in bounds(pattern)]))


@dataclass(frozen=True)
class Core:
    """A cell's core as `load` reads it: its pattern, density and angle, and the material's own
    figures it's made of."""

    pattern: str
    density_pct: float
    angle_deg: float
    #: The solid plastic's stiffness, Poisson's ratio and strain across its layers, in MPa and
    #: percent.
    solid_mpa: float
    poisson: float
    solid_across_pct: float

    @property
    def tested(self) -> str:
        """The tested pattern its figures are taken from."""
        return TESTED_AS[self.pattern]

    @property
    def scale(self) -> float:
        return scale(self.tested)

    @property
    def strain_share(self) -> float:
        return strain_share(self.tested)

    @property
    def along_pct(self) -> float:
        """The most it's stretched any way, in percent: the tests' share of the solid's yield
        over its stiffness."""
        return 100 * self.strain_share * YIELD_MPA / self.solid_mpa

    @property
    def across_pct(self) -> float:
        """The most it's pulled across its layers, in percent: the same share of the solid's."""
        return self.strain_share * self.solid_across_pct

    def stiffness(self, up: np.ndarray, first: np.ndarray, second: np.ndarray) -> np.ndarray:
        """The core's stiffness, C_ijkl in MPa, in the axes `up`, `first` and `second` are
        given in: its lines' stiffness at the tests' share."""
        return self.scale * lines_stiffness(
            self.pattern, self.density_pct, self.angle_deg, self.solid_mpa, self.poisson,
            up, first, second,
        )

    @property
    def modulus_mpa(self) -> float:
        """A modulus for FreeCAD's material, which `load` writes over with `stiffness`: the
        lattice's bending alone."""
        return self.scale * self.solid_mpa * (self.density_pct / 100) ** 2

    def said(self) -> dict[str, Any]:
        lines = LINES[self.pattern]
        how = (
            "read as bending alone, the same every way"
            if not lines
            else ", ".join(
                f"{100 * share:.0f}% of its lines at {self.angle_deg + offset:g}°"
                + (" walled through its layers" if walls else "")
                for offset, share, walls in lines
            )
        )
        up, first, second = _FLAT
        tensor = self.stiffness(up, first, second)
        along = math.radians(self.angle_deg)
        stiff_along = pulled(tensor, math.cos(along) * first + math.sin(along) * second)
        stiff_across = pulled(tensor, up)
        start = checked_from(self.pattern)
        unchecked = self.density_pct < start
        setter = set_by(self.tested)
        angle = ("its stiffest way" if setter["angle_deg"] is None
                 else f"{setter['angle_deg']:g}° to its lines")
        return {
            "pattern": self.pattern,
            "density_pct": self.density_pct,
            "angle_deg": self.angle_deg,
            "tested_as": self.tested,
            "scale": round(self.scale, 4),
            "strain_share": round(self.strain_share, 4),
            "modulus_along_mpa": round(stiff_along, 2),
            "modulus_across_mpa": round(stiff_across, 2),
            "unchecked": unchecked,
            "checked_from_pct": start,
            "scale_set_by": {k: setter[k] for k in ("source", "material", "density_pct",
                                                     "angle_deg")},
            "said": (
                f"{self.pattern} at {self.density_pct:g}%, {how}; {stiff_along:.0f} MPa along "
                f"{self.angle_deg:g}° and {stiff_across:.0f} MPa across its layers, "
                f"{100 * self.scale:.0f}% of its lines' own, the least any test of "
                f"{self.tested} reached: {setter['source']}'s {setter['material'].upper()} at "
                f"{setter['density_pct']:g}%, pulled {angle}"
                + (f"; below the {start:g}% the published tests reach, unchecked" if unchecked
                   else "")
            ),
        }

    def read(
        self, strain: np.ndarray, up: np.ndarray, first: np.ndarray, second: np.ndarray
    ) -> dict[str, Any]:
        """The core's nodes' `strain`, tensors as rows, against its limits: pulled across its
        layers, and its largest stretch any way, with how far that points out of the layers'
        plane and off its lines. `across_at` and `stretch_at` are rows of `strain`."""
        across = np.einsum("i,nij,j->n", up, strain, up)
        worst = int(across.argmax())
        values, vectors = np.linalg.eigh(strain)
        pick = np.abs(values).argmax(axis=1)
        stretch = np.abs(values).max(axis=1)
        node = int(stretch.argmax())
        towards = vectors[node][:, pick[node]]
        in_plane = towards - (towards @ up) * up
        off_lines = 90.0
        ways = []
        for offset, _, _ in LINES[self.pattern]:
            angle = math.radians(self.angle_deg + offset)
            ways.append(math.cos(angle) * first + math.sin(angle) * second)
        if ways and np.linalg.norm(in_plane) > 1e-9:
            flat = in_plane / np.linalg.norm(in_plane)
            off_lines = min(
                math.degrees(math.acos(min(1.0, abs(float(flat @ w) / np.linalg.norm(w)))))
                for w in ways
            )
        return {
            "across_layers_pct": 100 * max(float(across[worst]), 0.0),
            "across_at": worst,
            "across_limit_pct": round(self.across_pct, 4),
            "stretch_pct": 100 * float(stretch[node]),
            "stretch_is": "pull" if values[node][pick[node]] > 0 else "push",
            "stretch_at": node,
            "stretch_from_layers_deg": math.degrees(math.asin(min(1.0, abs(float(towards @ up))))),
            "stretch_from_lines_deg": off_lines,
            "stretch_limit_pct": round(self.along_pct, 4),
        }


def cell_mm(pattern: str, density_pct: float, spacing: float) -> float:
    """The core's own cell, in mm (mqucifer/print-shop#363): how far apart its lines lie in a
    layer, `spacing` over the density each layer's sparsest set holds. Grid shares each layer
    between its two sets, so at 20% its lines each way are 5 spacings apart; rectilinear lays
    one set a layer at the whole density. Its stiffness and strain are its cell's mean, so it's
    read no finer than that (`classify.cell_averaged`). Gyroid, not lines, by its density."""
    sets = LINES.get(pattern, ())
    alternate = [s for s in sets if not s[2]]
    shares = [s[1] if s[2] else s[1] * len(alternate) for s in sets] or [1.0]
    return float(spacing / (density_pct / 100 * min(shares)))


def core(settings: Settings, material: Any) -> Core:
    """A cell's core from its settings and the part's material.

    Raises:
        NoAnswer: For a pattern or a density no published test here reads.
    """
    from .report import NoAnswer

    pattern, density = settings.pattern, settings.infill
    if pattern is None or density is None or settings.infill_angle is None:
        raise NoAnswer("a printed cell needs its pattern, its infill and its infill_angle")
    if pattern not in LINES:
        raise NoAnswer(
            f"the pattern {pattern} isn't read as printed yet: {', '.join(sorted(LINES))} are, "
            "each as Orca lays its lines and set by a published test of it or of grid"
        )
    if not LEAST <= density <= MOST:
        raise NoAnswer(
            f"infill at {density:g}% isn't read as printed: it's read from {LEAST:g}% to "
            f"{MOST:g}%, the published tests reaching {MOST:g}%. Over that, leave out the "
            "part's `print`, and it's read solid"
        )
    return Core(pattern, float(density), float(settings.infill_angle), material.modulus_mpa,
                material.poisson, material.across_pct)
