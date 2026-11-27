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

from print_shop.bench import host, infill
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
    along the beam (`infill.Core`). Its walls are 0.45 mm each, and its 5 top and 3 bottom
    layers 0.2 mm, as `make_fixtures.PRINT` has them."""
    found = infill.core(Settings(pattern=pattern, infill=density, infill_angle=angle),
                        MATERIALS["petg"])
    up, first, second = np.eye(3)[2], np.eye(3)[0], np.eye(3)[1]
    core = infill.pulled(found.stiffness(up, first, second), first)
    side, low, high = walls * 0.45, 3 * 0.2, DEPTH - 5 * 0.2
    area, inner = WIDTH * DEPTH, (WIDTH - 2 * side) * (high - low)
    soft = MODULUS - core
    axial = MODULUS * area - soft * inner
    middle = (MODULUS * area * DEPTH / 2 - soft * inner * (low + high) / 2) / axial
    bending = MODULUS * (WIDTH * DEPTH**3 / 12 + area * (DEPTH / 2 - middle) ** 2) - soft * (
        (WIDTH - 2 * side) * (high - low) ** 3 / 12 + inner * ((low + high) / 2 - middle) ** 2
    )
    return float(G * LENGTH**3 / (3 * bending))


def worst(answer: Answer) -> float:
    """The read nearest its limit, of the skin's and each core's, as a share of the limit."""
    beam = answer.numbers["parts"]["beam"]
    shares = [beam["across_layers_pct"] / beam["across_limit_pct"],
              beam["stretch_pct"] / beam["stretch_limit_pct"]]
    for core in beam["cores"]:
        shares += [core["across_layers_pct"] / core["across_limit_pct"],
                   core["stretch_pct"] / core["stretch_limit_pct"]]
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
        assert printed["walls_mm"] == pytest.approx(walls * 0.45)
        assert printed["top_mm"] == pytest.approx(1.0)
        assert printed["bottom_mm"] == pytest.approx(0.6)
        assert printed["core_mm3"] == pytest.approx(
            (120 - 2 * walls * 0.45) * (20 - 2 * walls * 0.45) * (10 - 1.6), rel=0.01
        )
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
    assert "walls 0.90 mm thick, 1.00 mm of shells under each top face" in answer.text
    assert "Ben hadj Hassine et al. 2024, Polymers 16:1562" in answer.text
    assert "mqucifer/print-shop#340" in answer.text
    assert "below the tests' 40% to 60%" in answer.text, "20% is below what the tests checked"
    core = answer.numbers["parts"]["beam"]["cores"][0]
    assert core["unchecked"] and core["tested_as"] == "grid"
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
    whole = (120 - 1.8) * (20 - 1.8) * 8.4
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
    assert cells["band"]["core_mm3"] == pytest.approx((120 - 30) * (20 - 1.8) * (5 - 0.6), rel=0.02)


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


@pytest.mark.parametrize(
    ("changed", "says"),
    [
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
