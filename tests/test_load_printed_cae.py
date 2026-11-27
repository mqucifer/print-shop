"""`load` reading a part as printed, in the bench's second image (mqucifer/print-shop#337): a
solid skin of walls and shells round an infill core, and regions of their own. Needs
`scripts/bench.py build cae`.

The beam of `test_cae.py`, 120 x 20 x 10, held at its root and loaded with 1 kg at its tip,
printed at 2 and 4 walls and at 20, 40 and 60% grid (`cae_cases/make_fixtures.py`, `printed`):
it reads stiffer and stronger as both rise, and moves as a sandwich beam's formula says, with
the skin and core's own section.
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

pytestmark = pytest.mark.cae
CASES = Path(__file__).parent / "cae_cases"
TIP = ("--weight", "1", "--at", "115,10,10")
#: The beam, as the formula reads it: its length to the load, its width and depth, and the
#: material's stiffness, in mm and MPa.
LENGTH, WIDTH, DEPTH, MODULUS, G = 115.0, 20.0, 10.0, 1500.0, 9.80665
#: How far the solve may be from the formula: the solid beam reads 6% under FL³/(3EI), as its
#: load is spread over a patch whose middle is nearer the root (`test_cae.py`), and the printed
#: ones the same, so they're held to it within 8%.
AGREE = 0.08


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

    def ask(*args: str) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


def sandwich(walls: int, density: float, pattern: str = "grid", angle: float = 0.0) -> float:
    """How far the beam's tip moves as a sandwich beam: FL³/(3·EI), with EI the skin's and the
    core's about their own neutral axis, the skin solid and the core its infill's stiffness
    along the beam (`infill.Core`). Its walls are Orca's beads' spacing at 0.45 mm lines and 0.2
    mm layers (`as_printed.wall_thickness`), and its 5 top and 3 bottom layers 0.2 mm, as
    `make_fixtures.PRINT` has them."""
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
    return float(G * LENGTH**3 / (3 * bending))


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


def test_a_printed_bar_reads_stiffer_and_stronger_as_its_walls_and_infill_rise(cae: Any) -> None:
    solid = cae("load", "load/one.json", *TIP)
    read = {(w, d): cae("load", f"load/printed-{w}-{d}.json", *TIP)
            for w in (2, 4) for d in (20, 40, 60)}
    for (walls, density), answer in read.items():
        assert answer.verdict == "OK" and answer.code == 0, answer.text
        beam = answer.numbers["parts"]["beam"]
        assert beam["printed"] and len(beam["cores"]) == 1
        assert beam["cores"][0]["pattern"] == "grid"
        assert beam["cores"][0]["density_pct"] == density
        moves = answer.numbers["moves_mm"]
        assert moves == pytest.approx(sandwich(walls, density), rel=AGREE), (walls, density)
        # Softer than the solid beam, which the skin and core can never pass.
        assert moves > solid.numbers["moves_mm"]
        printed = answer.numbers["printed"]["beam"][0]
        side = as_printed.wall_thickness(walls, 0.45, 0.2)
        assert printed["walls_mm"] == pytest.approx(side, abs=1e-3)
        assert printed["top_mm"] == pytest.approx(1.0)
        assert printed["bottom_mm"] == pytest.approx(0.6)
        assert printed["core_mm3"] == pytest.approx(
            (120 - 2 * side) * (20 - 2 * side) * (10 - 1.6), rel=0.01
        )
        # Printed own +z up: its 3 bottom layers on its z 0 face, its 5 top on its z 10.
        (_, _, low), (_, _, high) = printed["core_box"]
        assert (low, high) == (pytest.approx(0.6, abs=1e-3), pytest.approx(9.0, abs=1e-3))
    for walls in (2, 4):
        moves = [read[(walls, d)].numbers["moves_mm"] for d in (20, 40, 60)]
        shares = [worst(read[(walls, d)]) for d in (20, 40, 60)]
        assert moves == sorted(moves, reverse=True) and len(set(moves)) == 3
        assert shares == sorted(shares, reverse=True) and len(set(shares)) == 3
    for density in (20, 40, 60):
        assert read[(4, density)].numbers["moves_mm"] < read[(2, density)].numbers["moves_mm"]
        assert worst(read[(4, density)]) < worst(read[(2, density)])
    # The solid beam is read as before: one material, no cores.
    assert "printed" not in solid.numbers
    assert solid.numbers["moves_mm"] == pytest.approx(1.870, abs=0.002)


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
    read from the nodes' own balance with each element's own stiffness, skin or core, are the
    weight and its moment about the joint by statics, as for the solid halves, within 0.3% of
    the weight: read from CalculiX's seven figures of each movement, as the skin's thin flat
    elements made the .frd's six 3.9% out; and the halves, each walled where they meet, are
    stiffer than the one-piece printed beam."""
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
    """Own -z up, its 5 top layers are on its z 0 face and its 3 bottom on its z 10: the core
    runs from 1.0 to 9.4, where printed own +z up it runs from 0.6 to 9.0. Where a face pulled
    has the thinner shells under it, it reads weaker."""
    over = cae("load", "load/printed-over.json", *TIP)
    assert over.verdict == "OK", over.text
    (_, _, low), (_, _, high) = over.numbers["printed"]["beam"][0]["core_box"]
    assert (low, high) == (pytest.approx(1.0, abs=1e-3), pytest.approx(9.4, abs=1e-3))
    assert "printed own -z up" in over.text


def test_a_core_pulled_across_its_layers_is_read_at_its_own_elements(cae: Any) -> None:
    """A block 40 x 40 x 10 held on its bed face and pulled up by 50 kg over its top: its core
    is read at its own elements' integration points and nodes, never less than CalculiX's
    mean at a node, which takes in the skin's where they meet; and on average no less than a
    column of shells and core beside its walls says: the walls and the column stretch alike,
    the column's shells and core in series."""
    pulled = cae("load", "load/printed-pulled.json", "--weight", "50", "--at", "20,20,10",
                 "--over", "40", "--toward", "+z")
    assert pulled.verdict == "OK", pulled.text
    core = pulled.numbers["parts"]["block"]["cores"][0]
    assert core["across_layers_pct"] >= core["averaged_across_layers_pct"] - 1e-9
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


def test_a_bowed_bar_s_walls_are_cut_with_no_sliver_and_never_read_solid_unsaid(cae: Any) -> None:
    """The cut on the bowed bars, in the second image (`cae_cases/as_printed_cut.py`): main's
    common of 16 copies left the notched leg's core 16 faces under a bead's width by its
    B-spline faces, steps at its concave edges, and the lofted bar bowed along its layers no
    core at all, read solid and said nothing. Now neither: the offset cuts each, and with the
    offset refused, as on a real leg, the copies mitred at the notch's concave edges leave no
    sliver, and the empty common is refused in words; each cut held to each layer's own
    outline offset in by its walls within `AGREE`."""
    work: Path = cae.work
    shutil.copy(CASES / "as_printed_cut.py", work / "as_printed_cut.py")
    done = host.run(["as_printed_cut.py"], work, python=True, image=host.CAE_IMAGE)
    assert done.returncode == 0, done.stdout + done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    for name in BOWED:
        now = found[name]["now"]
        assert now["slivers"] == 0 and now["checked"] >= max(5, 0.8 * now["planes"]), now
        assert now["short"] <= as_printed.SHORT and now["over"] <= as_printed.AGREE, now
        assert found[name]["refused"].startswith(
            f"the skin couldn't be cut round the part {name}'s curved faces: OCCT couldn't make "
            "its offset"
        ), found[name]["refused"]
    assert found["bowed-notched"]["main"]["slivers"] >= 8
    mitred = found["bowed-notched"]["copies"]
    assert "mitred at 2 concave edge(s)" in mitred["cut"] and mitred["slivers"] == 0
    assert mitred["short"] <= as_printed.SHORT and mitred["over"] <= as_printed.AGREE
    assert mitred["core_mm3"] == pytest.approx(found["bowed-notched"]["now"]["core_mm3"],
                                               rel=0.005)  # fmt: skip
    # Bowed along its layers its sections are the bar's, moved up and down: its shells, not
    # its walls, set its sloped faces, so its core is the straight bar's.
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    assert found["bowed-up"]["main"]["core_mm3"] == 0
    assert found["bowed-up"]["now"]["core_mm3"] == pytest.approx(
        (120 - 2 * side) * (20 - 2 * side) * 8.4, rel=0.01)
    assert "short of a layer's own outline" in found["bowed-up"]["copies"]["refused"]


@pytest.mark.parametrize("name", BOWED)
def test_a_bowed_bar_reads_as_printed_between_solid_and_its_core_alone(
    cae: Any, name: str
) -> None:
    """Each bowed bar, held at its root and loaded at x 110, meshes and solves as printed at
    the defaults with nothing turned inside out, moves more than it does solid and less than
    it would all core, the core's stiffness along it; and its solid read is as it was."""
    work: Path = cae.work
    at = ",".join(f"{v:g}" for v in json.loads((work / "load/bowed-at.json").read_text())[name])
    solid = cae("load", f"load/{name}.json", "--weight", "1", "--at", at)
    printed = cae("load", f"load/printed-{name}.json", "--weight", "1", "--at", at)
    assert solid.verdict != "NO ANSWER" and "printed" not in solid.numbers, solid.text
    assert printed.verdict != "NO ANSWER", printed.text
    assert "inside out" not in printed.text
    cell = printed.numbers["printed"]["bar"][0]
    assert cell["cut_planes_read"] >= max(5, 0.8 * cell["cut_planes"]), cell
    assert cell["core_mm3"] > 0, cell
    assert cell["cut_short_walls_pct"] <= 100 * as_printed.SHORT, cell
    assert cell["cut_over_walls_pct"] <= 100 * as_printed.AGREE, cell
    assert f"its walls cut by {cell['cut']}" in printed.text
    found = infill.core(Settings(pattern="grid", infill=20, infill_angle=0), MATERIALS["petg"])
    up, first, second = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]
    along = infill.pulled(found.stiffness(up, first, second), first)
    moves, alone = printed.numbers["moves_mm"], solid.numbers["moves_mm"]
    assert alone < moves < alone * MODULUS / along


def test_the_cut_s_check_holds_it_to_each_layer_and_to_the_part(cae: Any) -> None:
    """mqucifer/print-shop#356's review, each case a cut made wrong on purpose
    (`cae_cases/as_printed_checks.py`):

    - a block and a fin thinner than its walls: each layer's core is the block's, though OCCT's
      2D offset of the two together fails for both. An empty cut is refused, never read solid,
      and the 16 copies' cut, the block's alone, holds;
    - a bar's core moved 0.5 mm across, the same area each layer, isn't the cut; nor is one cut
      4% of its walls too far in, the unsafe side, while 4% too little is within `AGREE`;
    - a bar with a ledge, its core missing a slab between the spread planes: read at planes
      just off the ledge, the cut missing it isn't taken;
    - a bar whose layers can be read at one plane only is refused, not passed on one;
    - a core cut from the bar with a lump on it is kept within the bar's own shells."""
    work: Path = cae.work
    shutil.copy(CASES / "as_printed_checks.py", work / "as_printed_checks.py")
    done = host.run(["as_printed_checks.py"], work, python=True, image=host.CAE_IMAGE)
    assert done.returncode == 0, done.stdout + done.stderr
    found = json.loads(done.stdout.strip().splitlines()[-1])
    fin = found["fin"]
    assert fin["layer_mm2"] == pytest.approx(fin["true_mm2"], rel=1e-6)
    assert "the skin couldn't be cut round the part test's faces" in fin["empty"]["refused"]
    assert "short of a layer's own outline" in fin["empty"]["refused"]
    side = as_printed.wall_thickness(2, 0.45, 0.2)
    assert fin["copies"]["cut"] == "its common with 16 copies of itself"
    # The block's core over its 40 mm, less its shells, and a strip of the base's under it.
    assert fin["copies"]["core_mm3"] > (10 - 2 * side) ** 2 * (40 - 1.0 - 2.6)
    for name in ("shifted", "short", "ledge"):
        assert found[name]["cut"] == "its common with 16 copies of itself", (name, found[name])
    assert found["over"]["cut"] == ("its section between each two of its flat faces offset in "
                                    "by its walls")
    assert found["over"]["core_mm3"] > found["shifted"]["core_mm3"]
    assert "only 1 of" in found["unread"]["refused"]
    assert "planes could be read to check it" in found["unread"]["refused"]
    assert found["outside"]["cut"] is not None and found["outside"]["outside_mm3"] == 0.0


def test_a_web_too_thin_for_infill_reads_solid_and_its_bosses_skin_and_core(cae: Any) -> None:
    """mqucifer/print-shop#363: two bosses joined by a web 1.75 mm thick, at 2 walls, 0.81 mm
    each, leave 0.12 mm inside them, under what Orca's opening of the area inside its walls
    lays infill in. The web reads solid skin, opened at one run of its layers, and the core is
    the bosses' alone, as Orca prints it, each held to its layers as Orca opens them. At 2.0
    mm, 0.37 mm inside, Orca lays infill, and the core runs through the web."""
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
        assert cell["cut_planes_read"] >= max(5, 0.8 * cell["cut_planes"]), cell
        assert cell["cut_short_walls_pct"] <= 100 * as_printed.SHORT, cell
        assert cell["cut_over_walls_pct"] <= 100 * as_printed.AGREE, cell
        read[name] = cell
    assert "opened where its layers are too thin for infill, at 1 run(s)" in read["web"]["cut"]
    assert read["web"]["core_mm3"] == pytest.approx(2 * boss, rel=0.01)
    assert "opened" not in read["web-wide"]["cut"]
    # The wide web's 0.37 mm of core between the bosses' own, through its height less shells.
    strip = (2.0 - 2 * side) * (40.0 - 2 * (6.0 - side)) * (20.0 - 1.6)
    assert read["web-wide"]["core_mm3"] > 2 * boss + 0.9 * strip
