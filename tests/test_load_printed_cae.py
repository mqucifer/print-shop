"""`load` reading a part as printed, in the bench's second image (mqucifer/print-shop#337): a
solid skin of walls and shells round an infill core, and regions of their own. Needs
`scripts/bench.py build cae`.

The beam of `test_cae.py`, 120 x 20 x 10, held at its root and loaded with 1 kg at its tip,
printed at 2 and 4 walls and at 20, 40 and 60% grid (`cae_cases/make_fixtures.py`, `printed`):
it reads stiffer and stronger as both rise, and moves as a sandwich beam's formula says, with
the skin and core's own section. Each element is read as its layers have it, all skin, all
core, or a blend of the two (`classify`, mqucifer/print-shop#363), its mesh fine at the part's
faces: a few minutes a read, so each is asked once a module (`cae`), and the whole grid of
walls and infill is a `proof`, by hand. Marked `printed`: CI runs them in a job of their own.
"""

from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import as_printed, host, infill
from print_shop.bench.assembly import MATERIALS
from print_shop.bench.printed import Settings

pytestmark = [pytest.mark.cae, pytest.mark.printed]
CASES = Path(__file__).parent / "cae_cases"
TIP = ("--weight", "1", "--at", "115,10,10")
#: The beam, as the formula reads it: its length to the load, its width and depth, and the
#: material's stiffness, in mm and MPa.
LENGTH, WIDTH, DEPTH, MODULUS, G = 115.0, 20.0, 10.0, 1500.0, 9.80665
#: How far the solve may be from the formula: the solid beam reads 6% under FL³/(3EI), as its
#: load is spread over a patch whose middle is nearer the root (`test_cae.py`), and the printed
#: ones the same, so they're held to it within 8%.
AGREE = 0.08
#: How far the beam's second, coarser mesh may read it from its first (mqucifer/print-shop#363):
#: its movement, and its skin and core, each at its own scale.
SECOND_MOVES = 0.03
SECOND_AGREE = 0.15


@dataclass
class Answer:
    code: int
    text: str
    record: dict[str, Any]

    @property
    def verdict(self) -> str:
        return str(self.record["verdict"])

    @property
    def numbers(self) -> dict[str, Any]:
        return dict(self.record["numbers"])


@pytest.fixture(scope="module")
def cae(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id(host.CAE_IMAGE) is None:
        pytest.fail(f"{host.missing(host.CAE_IMAGE)} first")
    work = tmp_path_factory.mktemp("printed")
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr

    asked: dict[tuple[str, ...], Answer] = {}

    def ask(*args: str) -> Answer:
        """`load`'s answer to `args`, read once a module: a read as printed takes minutes."""
        words = tuple(str(a) for a in args)
        if words not in asked:
            done = host.run(list(words), work)
            found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
            assert found, f"nothing saved: {done.stdout}{done.stderr}"
            record = json.loads((work / f"{found.group(1)}.json").read_text())
            asked[words] = Answer(done.returncode, done.stdout, record)
        return asked[words]

    ask.work = work  # type: ignore[attr-defined]
    return ask


def section(walls: int, density: float, pattern: str = "grid", angle: float = 0.0
            ) -> tuple[float, float, float]:
    """The beam's section as a sandwich: its EI, the skin's and the core's about their own
    neutral axis, the skin solid and the core its infill's stiffness along the beam
    (`infill.Core`), in N·mm²; that axis's height, and the core's top's, in mm. Its walls are
    Orca's beads' spacing at 0.45 mm lines and 0.2 mm layers (`as_printed.wall_thickness`), and
    its 5 top and 3 bottom layers 0.2 mm, as `make_fixtures.PRINT` has them."""
    found = infill.core(Settings(pattern=pattern, infill=density, infill_angle=angle),
                        MATERIALS["petg"])
    up, first, second = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]
    core = infill.pulled(found.stiffness(up, first, second), first)
    side, low, high = as_printed.wall_thickness(walls, 0.45, 0.2), 3 * 0.2, DEPTH - 5 * 0.2
    area, inner = WIDTH * DEPTH, (WIDTH - 2 * side) * (high - low)
    soft = MODULUS - core
    axial = MODULUS * area - soft * inner
    middle = (MODULUS * area * DEPTH / 2 - soft * inner * (low + high) / 2) / axial
    bending = MODULUS * (WIDTH * DEPTH**3 / 12 + area * (DEPTH / 2 - middle) ** 2) - soft * (
        (WIDTH - 2 * side) * (high - low) ** 3 / 12 + inner * ((low + high) / 2 - middle) ** 2
    )
    return float(bending), float(middle), float(high)


def sandwich(walls: int, density: float, pattern: str = "grid", angle: float = 0.0) -> float:
    """How far the beam's tip moves as a sandwich beam, 1 kg at its tip: FL³/(3·EI)."""
    return G * LENGTH**3 / (3 * section(walls, density, pattern, angle)[0])


def core_at_root(walls: int, density: float, kg: float = 1.0) -> float:
    """The core's stretch along the beam at its root's top, in %, by the sandwich's own
    section: M·c/EI, the root's moment `kg` at the tip, c the core's top over its neutral
    axis. The plane-sections arithmetic the cell's average must not read under."""
    bending, middle, high = section(walls, density)
    return 100 * kg * G * LENGTH * (high - middle) / bending


def worst(answer: Answer) -> float:
    """What bends the bar nearest its limit, as a share of the limit: the skin's pull across
    its layers and its stretch, and each core's stretch. A core's own pull across its layers
    is judged too, but not here: next to the clamped root, where its bottom shells are pushed
    along it, a stiffer core takes more of the clamp's hold (at 2 walls, 0.166%, 0.187% and
    0.192% at 20, 40 and 60%, all under its 0.48%), while what bends the bar falls."""
    beam = answer.numbers["parts"]["beam"]
    shares = [beam["across_layers_pct"] / beam["across_limit_pct"],
              beam["stretch_pct"] / beam["stretch_limit_pct"]]
    for core in beam["cores"]:
        shares.append(core["stretch_pct"] / core["stretch_limit_pct"])
        assert core["across_layers_pct"] < core["across_limit_pct"]
    return float(max(shares))


def held_to_its_layers(cell: dict[str, Any]) -> None:
    """A printed cell's mesh holds its core as Orca's layers do, band by band of its layers,
    its wedges' volume less (`classify.checked`): short of it by no more than
    `as_printed.SHORT`, the unsafe side, and over it by no more than `as_printed.AGREE`; every
    layer read; and every element told skin, core or one blend of them."""
    assert cell["layers_unread"] == 0, cell
    assert cell["band_short_pct"] <= 100 * as_printed.SHORT, cell
    assert cell["band_over_pct"] <= 100 * as_printed.AGREE, cell
    kinds = ("skin", "core", "laminate", "column", "series")
    assert sum(cell[f"{k}_elements"] for k in kinds) == cell["elements"], cell
    # Its skin as wedges on its faces: every triangle but those held, thin, leaving the part
    # or turned inside out (`wedges.layer`).
    assert cell["wedges"] > 0, cell
    left = ("held", "thin", "outside", "inverted")
    assert cell["part_wedges"] + sum(cell[f"faces_{k}"] for k in left) == cell["faces"], cell


def bar(answer: Answer, walls: int, density: int) -> None:
    """The printed beam at `walls` and `density`: read OK, as the sandwich formula says, its
    walls, shells and core as its layers have them."""
    assert answer.verdict == "OK" and answer.code == 0, answer.text
    beam = answer.numbers["parts"]["beam"]
    assert beam["printed"] and len(beam["cores"]) == 1
    assert beam["cores"][0]["pattern"] == "grid"
    assert beam["cores"][0]["density_pct"] == density
    assert answer.numbers["moves_mm"] == pytest.approx(sandwich(walls, density), rel=AGREE)
    # Its core averaged over its own cell, never under the section's own arithmetic at its root.
    core = beam["cores"][0]
    assert core["stretch_pct"] >= 0.9 * core_at_root(walls, density), core
    assert core["cell_mm"] == pytest.approx(infill.cell_mm(
        "grid", density, as_printed.wall_thickness(1, 0.45, 0.2)), abs=1e-3)
    printed = answer.numbers["printed"]["beam"][0]
    side = as_printed.wall_thickness(walls, 0.45, 0.2)
    assert printed["walls_mm"] == pytest.approx(side, abs=1e-3)
    assert printed["top_mm"] == pytest.approx(1.0)
    assert printed["bottom_mm"] == pytest.approx(0.6)
    assert printed["core_mm3"] == pytest.approx(
        (120 - 2 * side) * (20 - 2 * side) * (10 - 1.6), rel=0.01)
    held_to_its_layers(printed)


def test_a_printed_bar_reads_stiffer_and_stronger_as_its_walls_and_infill_rise(cae: Any) -> None:
    """At 2 walls and 20%, and 4 walls and 60%, the beam bends as the sandwich formula says
    within `AGREE`, less than all core and more than solid; the stiffer stronger."""
    solid = cae("load", "load/one.json", *TIP)
    low = cae("load", "load/printed-2-20.json", *TIP)
    high = cae("load", "load/printed-4-60.json", *TIP)
    bar(low, 2, 20)
    bar(high, 4, 60)
    assert solid.numbers["moves_mm"] < high.numbers["moves_mm"] < low.numbers["moves_mm"]
    assert worst(high) < worst(low)
    # Each core read at its own cell's scale at the clamped root, the same on both meshes: the
    # 60% core's cell, 1.36 mm, finer than the mesh, its held face meshed at twice it.
    for answer in (low, high):
        core = answer.numbers["parts"]["beam"]["cores"][0]
        assert core["coarse_stretch_pct"] == pytest.approx(core["stretch_pct"], rel=0.05), core
    cell = high.numbers["printed"]["beam"][0]
    assert cell["held_mesh_mm"] == pytest.approx(2 * cell["cell_mm"], rel=1e-3)
    assert low.numbers["printed"]["beam"][0]["held_mesh_mm"] is None
    # The solid beam is read as before: one material, no cores.
    assert "printed" not in solid.numbers
    assert solid.numbers["moves_mm"] == pytest.approx(1.870, abs=0.002)


@pytest.mark.proof
def test_the_whole_grid_of_walls_and_infill_reads_in_order(cae: Any) -> None:
    """Each of 2 and 4 walls at 20, 40 and 60%: each as the formula says, and each step up in
    walls or infill stiffer and stronger. Six reads as printed, past CI's job: by hand."""
    read = {(w, d): cae("load", f"load/printed-{w}-{d}.json", *TIP)
            for w in (2, 4) for d in (20, 40, 60)}
    for (walls, density), answer in read.items():
        bar(answer, walls, density)
    for walls in (2, 4):
        moves = [read[(walls, d)].numbers["moves_mm"] for d in (20, 40, 60)]
        shares = [worst(read[(walls, d)]) for d in (20, 40, 60)]
        assert moves == sorted(moves, reverse=True) and len(set(moves)) == 3
        assert shares == sorted(shares, reverse=True) and len(set(shares)) == 3
    for density in (20, 40, 60):
        assert read[(4, density)].numbers["moves_mm"] < read[(2, density)].numbers["moves_mm"]
        assert worst(read[(4, density)]) < worst(read[(2, density)])


def forced(cae: Any, mode: str, *args: str) -> Answer:
    """`load` with one step forced (`cae_cases/load_forced.py`), read once a module."""
    work: Path = cae.work
    asked: dict[tuple[str, tuple[str, ...]], Answer] = cae.__dict__.setdefault("forced", {})
    if (mode, args) not in asked:
        shutil.copy(CASES / "load_forced.py", work / "load_forced.py")
        done = host.run(["load_forced.py", mode, *args], work, python=True, image=host.CAE_IMAGE)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, done.stdout + done.stderr
        asked[(mode, args)] = Answer(done.returncode, done.stdout,
                                     json.loads((work / f"{found.group(1)}.json").read_text()))
    return asked[(mode, args)]


def test_the_iterative_solver_reads_a_printed_part_as_the_direct_one_does(cae: Any) -> None:
    """The beam at 2 walls and 20%, its skin a layer of wedges on its faces, by CalculiX's
    direct solver and by its iterative one from the start, balanced by `load`'s corrections
    (`cae_cases/load_forced.py`, `iterative`): the same movement, verdict, skin and core, to
    the balance's own tolerance, a part in a thousand."""
    direct = cae("load", "load/printed-2-20.json", *TIP)
    iterated = forced(cae, "iterative", "load", "load/printed-2-20.json", *TIP)
    assert direct.verdict == iterated.verdict != "NO ANSWER", direct.text + iterated.text
    assert "iterative" not in direct.text and "iterative" in iterated.text
    assert iterated.numbers["moves_mm"] == pytest.approx(direct.numbers["moves_mm"], rel=1e-3)
    for key in ("across_layers_pct", "stretch_pct"):
        assert iterated.numbers["parts"]["beam"][key] == pytest.approx(
            direct.numbers["parts"]["beam"][key], rel=1e-3)
        assert iterated.numbers["parts"]["beam"]["cores"][0][key] == pytest.approx(
            direct.numbers["parts"]["beam"]["cores"][0][key], rel=1e-3)


def test_the_second_check_reads_the_wedges_meshed_again_with_the_solid(cae: Any) -> None:
    """mqucifer/print-shop#363: the beam's skin is laid as wedges on each mesh's own faces, so
    its coarser mesh is a second reading of it: it moves within `SECOND_MOVES` of the first,
    and its skin and core, each read at its own scale, within `SECOND_AGREE`; and its core's
    peak at its elements is left unjudged beside its cell's."""
    fine = cae("load", "load/printed-2-20.json", *TIP)
    place = fine.numbers["loads"][0]
    first = place.get("mesh_moves_mm", place["moves_mm"])
    assert place["coarse_moves_mm"] == pytest.approx(first, rel=SECOND_MOVES)
    beam = fine.numbers["parts"]["beam"]
    for one in (beam, beam["cores"][0]):
        for key in ("across_layers_pct", "stretch_pct"):
            assert one[f"coarse_{key}"] == pytest.approx(one[key], rel=SECOND_AGREE), (key, one)
    core = beam["cores"][0]
    assert core["at_elements_stretch_pct"] > core["stretch_pct"]
    assert "averaged over its own cell, 4.07 mm each way" in fine.text
    assert "its skin laid on its faces as" in fine.text
    assert "its skin's strain read at its wedges' and its elements' own points" in fine.text


def test_a_core_truly_overloaded_at_its_root_fails_there(cae: Any) -> None:
    """Twice the weight at which the section's arithmetic stretches the beam's core at its
    root to its limit: read over its own cell, the core is over its limit, at its root, and
    no less stretched than that arithmetic says. An overload spans many cells, so its cell's
    average doesn't hide it."""
    limit = infill.core(Settings(pattern="grid", infill=20, infill_angle=0),
                        MATERIALS["petg"]).along_pct  # fmt: skip
    kg = math.ceil(2 * limit / core_at_root(2, 20))
    heavy = cae("load", "load/printed-2-20.json", "--weight", str(kg), "--at", "115,10,10")
    assert heavy.verdict == "FAIL", heavy.text
    core = heavy.numbers["parts"]["beam"]["cores"][0]
    assert core["stretch_pct"] > core["stretch_limit_pct"], core
    assert core["stretch_pct"] >= 0.9 * core_at_root(2, 20, kg), core
    assert core["stretch_at"][0] < 10, core
    assert "the part beam's core in the part is stretched" in heavy.text


def test_the_reply_says_what_it_assumed_and_that_no_print_has_checked_it(cae: Any) -> None:
    answer = cae("load", "load/printed-2-20.json", *TIP)
    assert "the part beam as printed, the part: 2 walls" in answer.text
    assert "walls 0.81 mm thick, as many of Orca's beads' spacing" in answer.text
    assert "1.00 mm of shells under each top face" in answer.text
    assert "Ben hadj Hassine et al. 2024, Polymers 16:1562" in answer.text
    assert "mqucifer/print-shop#340" in answer.text
    assert "Alabdli et al. 2026" in answer.text and "grid from 20%" in answer.text
    core = answer.numbers["parts"]["beam"]["cores"][0]
    assert not core["unchecked"] and core["checked_from_pct"] == 20
    assert core["tested_as"] == "grid"
    assert core["scale_set_by"]["source"] == "ben-hadj-hassine-2024"
    assert core["stretch_limit_pct"] == pytest.approx(round(infill.core(
        Settings(pattern="grid", infill=20, infill_angle=0), MATERIALS["petg"]).along_pct, 4))
    assert "on the coarser mesh" in answer.text and "core in the part" in answer.text


def test_a_box_round_the_root_stiffens_it_and_its_own_walls_more(cae: Any) -> None:
    """60% in the first 30 mm, where the beam is bent most, reads stiffer than 20% throughout;
    with 4 walls of its own besides, an island walled at its face where it cuts the beam,
    stiffer still."""
    plain = cae("load", "load/printed-2-20.json", *TIP).numbers
    dense = cae("load", "load/printed-dense.json", *TIP)
    walled = cae("load", "load/printed-walled.json", *TIP)
    for answer, name in ((dense, "root"), (walled, "walled")):
        assert answer.verdict == "OK", answer.text
        cells = {c["cell"]: c for c in answer.numbers["parts"]["beam"]["cores"]}
        assert set(cells) == {name, "the part"}
        assert cells[name]["density_pct"] == 60 and cells["the part"]["density_pct"] == 20
        for cell in answer.numbers["printed"]["beam"]:
            held_to_its_layers(cell)
    assert walled.numbers["moves_mm"] < dense.numbers["moves_mm"] < plain["moves_mm"]
    # The island's ring each side of its face at x 30 takes from both cores.
    island = {c["cell"]: c for c in walled.numbers["printed"]["beam"]}
    box = {c["cell"]: c for c in dense.numbers["printed"]["beam"]}
    assert island["walled"]["core_mm3"] < box["root"]["core_mm3"]
    assert island["the part"]["core_mm3"] < box["the part"]["core_mm3"]
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    whole = (120 - 2 * side) * (20 - 2 * side) * 8.4
    assert box["root"]["core_mm3"] + box["the part"]["core_mm3"] == pytest.approx(whole, rel=0.01)


def test_a_band_of_layers_is_read_in_the_part_s_own_axes_and_a_box_wins_over_it(
    cae: Any,
) -> None:
    """Its lower 5 mm at 40%, as a band of layers from 0 to 5 in its own z, and the box at its
    root at 60%, given before the band: the box wins where they meet, as Orca's modifier does
    over a height range, so the cells are the band's, the box's, and the part's own above."""
    answer = cae("load", "load/printed-band.json", *TIP)
    assert answer.verdict == "OK", answer.text
    cells = {c["cell"]: c for c in answer.numbers["printed"]["beam"]}
    assert set(cells) == {"band", "band and root", "root", "the part"}
    assert cells["band and root"]["settings"]["infill"] == 60
    assert cells["band"]["settings"]["infill"] == 40
    assert cells["the part"]["settings"]["infill"] == 20
    # The band holds the beam's lower 5 mm beyond the box, its bottom shells and walls left out.
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    assert cells["band"]["core_mm3"] == pytest.approx(
        (120 - side - 30) * (20 - 2 * side) * (5 - 0.6), rel=0.01)


def test_a_printed_part_s_joint_carries_what_statics_says(cae: Any) -> None:
    """The beam as two halves bolted at its middle, each printed: the joint's force and moment,
    read from the nodes' own balance with each element's own stiffness, skin, core or blend,
    are the weight and its moment about the joint by statics, as for the solid halves, within
    0.3% of the weight: read from CalculiX's seven figures of each movement, as the skin's thin
    flat elements made the .frd's six 3.9% out; and the halves, each walled where they meet,
    are stiffer than the one-piece printed beam."""
    two = cae("load", "load/printed-two.json", *TIP)
    assert two.verdict == "OK", two.text
    joint = two.numbers["joints"]["root+tip"]
    assert joint["statics_force_n"] == pytest.approx([0, 0, -G], abs=1e-4)
    assert joint["force_n"] == pytest.approx(joint["statics_force_n"], abs=0.003 * G)
    assert joint["moment_nmm"] == pytest.approx(joint["statics_moment_nmm"], abs=1.0)
    assert joint["bolt_pull_n"] == pytest.approx(joint["bolt_pull_statics_n"], rel=2e-3)
    for name in ("root", "tip"):
        assert two.numbers["parts"][name]["printed"], name
    one = cae("load", "load/printed-2-20.json", *TIP)
    assert two.numbers["moves_mm"] < one.numbers["moves_mm"]


def test_lines_along_the_bar_hold_it_stiffer_than_lines_across_it(cae: Any) -> None:
    """Rectilinear lays its lines at its angle and square to it, turned each layer: at 0° half
    run along the beam, and at 45° none do."""
    along = cae("load", "load/printed-lines-0.json", *TIP)
    across = cae("load", "load/printed-lines-45.json", *TIP)
    assert along.verdict == "OK" and across.verdict == "OK", along.text + across.text
    assert along.numbers["moves_mm"] < across.numbers["moves_mm"]
    for answer, angle in ((along, 0), (across, 45)):
        assert answer.numbers["moves_mm"] == pytest.approx(
            sandwich(2, 20, "rectilinear", angle), rel=AGREE
        )


def test_a_part_printed_turned_over_has_its_top_shells_on_its_other_face(cae: Any) -> None:
    """Own -z up, its 5 top layers are on its z 0 face and its 3 bottom on its z 10, the face
    the tip's weight pulls: its core runs 0.6 mm from it, not 1.0, so it's stretched more
    there than printed own +z up."""
    over = cae("load", "load/printed-over.json", *TIP)
    up = cae("load", "load/printed-2-20.json", *TIP)
    assert over.verdict == "OK", over.text
    assert "printed own -z up" in over.text
    held_to_its_layers(over.numbers["printed"]["beam"][0])
    stretched = [a.numbers["parts"]["beam"]["cores"][0]["stretch_pct"] for a in (over, up)]
    assert stretched[0] > stretched[1]


def test_a_core_pulled_across_its_layers_is_read_at_its_own_elements(cae: Any) -> None:
    """A block 40 x 40 x 10 held on its bed face and pulled up by 50 kg over its top: its core,
    read over its own cell, is no less than a column of shells and core beside its walls says
    on average: the walls and the column stretch alike, the column's shells and core in
    series."""
    pulled = cae("load", "load/printed-pulled.json", "--weight", "50", "--at", "20,20,10",
                 "--over", "40", "--toward", "+z")
    assert pulled.verdict == "OK", pulled.text
    core = pulled.numbers["parts"]["block"]["cores"][0]
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    found = infill.core(Settings(pattern="grid", infill=20, infill_angle=0), MATERIALS["petg"])
    up, first, second = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]
    across = infill.pulled(found.stiffness(up, first, second), up)
    inner = (40 - 2 * side) ** 2
    column = 1.6 / MODULUS + 8.4 / across
    stretch = 50 * G / (inner / column + MODULUS * (40**2 - inner) / 10)
    assert core["across_layers_pct"] > 100 * stretch / column / across


@pytest.mark.parametrize(
    ("changed", "says"),
    [
        ({"up": None}, "doesn't say which way it stands"),
        ({"walls": 0}, "given walls 0"),
        ({"top": 0}, "given top 0"),
        ({"up": "+x"}, "its layers are along its own z"),
        ({"pattern": None}, "without pattern"),
        ({"infill": 90}, "read solid"),
        ({"pattern": "honeycomb"}, "isn't read as printed yet"),
    ],
)
def test_a_print_load_can_t_read_says_why_and_nothing_is_solved(
    cae: Any, changed: dict[str, Any], says: str
) -> None:
    work: Path = cae.work
    data = json.loads((work / "load/printed-2-20.json").read_text())
    for key, value in changed.items():
        if value is None:
            data["parts"][0]["print"].pop(key)
        else:
            data["parts"][0]["print"][key] = value
    name = f"load/printed-wrong-{abs(hash(json.dumps(changed))) % 10**6}.json"
    (work / name).write_text(json.dumps(data))
    answer = cae("load", name, *TIP)
    assert answer.verdict == "NO ANSWER" and answer.code == 2, answer.text
    assert says in answer.text
    assert math.isfinite(answer.record["seconds"])


#: The bowed bars of `make_fixtures.bowed` (mqucifer/print-shop#356).
BOWED = ("bowed", "bowed-up", "bowed-notched")
#: The bar bowed along its layers' core by its layers, in mm³: 24 million points 0.1 mm apart
#: over its box, each read in its own layer (`classify.Layered.core`), inside the part by its
#: section there (mqucifer/print-shop#363).
BOWED_UP_CORE = 17567.3


@pytest.mark.parametrize("name", BOWED)
def test_a_bowed_bar_reads_as_printed_between_solid_and_its_core_alone(
    cae: Any, name: str
) -> None:
    """Each bowed bar, held at its root and loaded at x 110, meshes and solves as printed at
    the defaults with nothing turned inside out, its core held to its layers, and moves more
    than it does solid and less than it would all core, the core's stiffness along it; and its
    solid read is as it was. Bowed along its layers, its sections are the straight bar's moved
    up and down, so its core is the straight bar's: main's common of 16 copies read it solid."""
    work: Path = cae.work
    at = ",".join(f"{v:g}" for v in json.loads((work / "load/bowed-at.json").read_text())[name])
    solid = cae("load", f"load/{name}.json", "--weight", "1", "--at", at)
    printed = cae("load", f"load/printed-{name}.json", "--weight", "1", "--at", at)
    assert solid.verdict != "NO ANSWER" and "printed" not in solid.numbers, solid.text
    assert printed.verdict != "NO ANSWER", printed.text
    assert "inside out" not in printed.text
    cell = printed.numbers["printed"]["bar"][0]
    assert cell["core_mm3"] > 0, cell
    held_to_its_layers(cell)
    if name == "bowed-up":
        # Its layers' own core, read on a 0.1 mm grid of points each in its own layer: less
        # than the straight bar's, 18,267 mm³, as its sloped faces' shells are grown into it.
        assert cell["core_mm3"] == pytest.approx(BOWED_UP_CORE, rel=0.01)
    found = infill.core(Settings(pattern="grid", infill=20, infill_angle=0), MATERIALS["petg"])
    up, first, second = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]
    along = infill.pulled(found.stiffness(up, first, second), first)
    moves, alone = printed.numbers["moves_mm"], solid.numbers["moves_mm"]
    assert alone < moves < alone * MODULUS / along


def test_a_layer_s_core_is_read_as_orca_slices_it_along_each_axis(cae: Any) -> None:
    """The layers' own 2D reading each element is classified by and checked against
    (`cae_cases/as_printed_checks.py`): a block's layer beside a fin thinner than its walls is
    the block's core alone, though OCCT's 2D offset of the two together fails; and the tile,
    chamfered, its layers along each of its own axes, reads the same middle layer each way,
    offset in, not out: OCCT's 2D offset goes by a face's own way round, and a section across
    its own y once offset out (mqucifer/print-shop#363's real tile)."""
    work: Path = cae.work
    shutil.copy(CASES / "as_printed_checks.py", work / "as_printed_checks.py")
    done = host.run(["as_printed_checks.py"], work, python=True, image=host.CAE_IMAGE)
    assert done.returncode == 0, done.stdout + done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    assert found["fin"]["layer_mm2"] == pytest.approx(found["fin"]["true_mm2"], rel=1e-6)
    for up in "+-":
        read = [found["axes"][f"{axis}{up}"] for axis in "xyz"]
        for layer in read:
            assert 0.8 * layer["section_mm2"] < layer["core_mm2"] < layer["section_mm2"], read
            assert layer["core_mm2"] == pytest.approx(read[2]["core_mm2"], rel=1e-4), read


def test_a_web_too_thin_for_infill_reads_solid_and_its_bosses_skin_and_core(cae: Any) -> None:
    """mqucifer/print-shop#363: two bosses joined by a web 1.75 mm thick, at 2 walls, 0.81 mm
    each, leave 0.12 mm inside them, under what Orca's opening of the area inside its walls
    lays infill in. The web reads solid skin and the core is the bosses' alone, as Orca prints
    it. At 2.0 mm, 0.37 mm inside, Orca lays infill, and the core runs through the web."""
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    boss = math.pi * (6.0 - side) ** 2 * (20.0 - 1.0 - 0.6)
    read = {}
    for name in ("web", "web-wide"):
        solid = cae("load", f"load/{name}.json", "--weight", "1", "--at", "20,0,20",
                    "--toward", "-y")  # fmt: skip
        printed = cae("load", f"load/printed-{name}.json", "--weight", "1", "--at", "20,0,20",
                      "--toward", "-y")  # fmt: skip
        assert solid.verdict != "NO ANSWER" and "printed" not in solid.numbers, solid.text
        assert printed.verdict != "NO ANSWER", printed.text
        assert "inside out" not in printed.text
        cell = printed.numbers["printed"]["webbed"][0]
        held_to_its_layers(cell)
        read[name] = cell
    assert read["web"]["core_mm3"] == pytest.approx(2 * boss, rel=0.02)
    # The wide web's 0.37 mm of core between the bosses' own, through its height less shells.
    strip = (2.0 - 2 * side) * (40.0 - 2 * (6.0 - side)) * (20.0 - 1.6)
    assert read["web-wide"]["core_mm3"] > 2 * boss + 0.9 * strip


def test_a_tile_of_planes_and_cylinders_reads_as_printed(cae: Any) -> None:
    """mqucifer/print-shop#363: a tile of counterbored holes and a boss in a pocket, every face
    upright to its layers or flat in their plane, where the cut's ways each failed: read
    element by element, it holds to its layers, meshes with nothing turned inside out and
    reads softer than solid; its solid read is as it was."""
    solid = cae("load", "load/tile.json", "--weight", "1", "--at", "55,55,8")
    printed = cae("load", "load/printed-tile.json", "--weight", "1", "--at", "55,55,8")
    assert solid.verdict != "NO ANSWER" and "printed" not in solid.numbers, solid.text
    assert printed.verdict != "NO ANSWER", printed.text
    assert "inside out" not in printed.text
    held_to_its_layers(printed.numbers["printed"]["tile"][0])
    assert printed.numbers["moves_mm"] > solid.numbers["moves_mm"]


def test_a_chamfered_tile_s_sloped_faces_read_as_printed(cae: Any) -> None:
    """The tile with its top's outer edges chamfered 1 mm at 45°, its skin's depth there its
    walls' or its shells' along their own way, whichever is deeper (`wedges.depths`): none of
    its wedges turned inside out, its core held to its layers, and it moves more than the plain
    tile, less stiff by its chamfer."""
    plain = cae("load", "load/printed-tile.json", "--weight", "1", "--at", "55,55,8")
    sloped = cae("load", "load/printed-tile-chamfered.json", "--weight", "1", "--at", "55,55,8")
    assert sloped.verdict != "NO ANSWER", sloped.text
    assert "inside out" not in sloped.text and "nonpositive jacobian" not in sloped.text
    cell = sloped.numbers["printed"]["tile"][0]
    held_to_its_layers(cell)
    # Where a wedge would turn over at the chamfer's narrow strip, there's none, and it's said.
    assert f"{cell['faces_inverted']} whose wedge would be too sharp or turned over" in sloped.text
    assert sloped.numbers["moves_mm"] > plain.numbers["moves_mm"]


def test_parts_beside_a_printed_one_keep_elements_of_their_own(cae: Any) -> None:
    """The printed tile with a solid pin bonded in each hole: each pin keeps elements of its own
    and is read, as a real tile's eight neighbours weren't when its faces were meshed at its
    walls (mqucifer/print-shop#363); its skin's wedges lie on the faces it meets them by too."""
    pinned = cae("load", "load/printed-tile-pinned.json", "--weight", "1", "--at", "55,55,8")
    assert pinned.verdict != "NO ANSWER", pinned.text
    for k in range(1, 5):
        assert pinned.numbers["parts"][f"pin{k}"]["nodes"] > 0, pinned.numbers["parts"]
        assert not pinned.numbers["parts"][f"pin{k}"].get("printed")
    held_to_its_layers(pinned.numbers["printed"]["tile"][0])
