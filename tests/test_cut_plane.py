"""`render --cut` through any plane, with each pair's least gap on it (mqucifer/print-shop#352).

The parts are made here, in their own frame: two plates 30 x 20, the lower from z -4 to 0 and the
upper from z 0.2 to 4.2, so their seam is 0.2 mm wide, each with two holes 5.5 mm across at x -6
and x 6; and one pin, 5 mm across and 8.2 long, from z -4 to 4.2, which the assembly puts in each
hole, as the parts `pin1` (at x -6) and `pin2` (at x 6): a play of 0.25 mm all round. The assembly
turns everything 35° about x and then 20° about z, and moves it to (10, 5, 3), so no face is square
to an axis. The cut is square to the seam, through both pins' axes: through the plates' middle,
its normal the plates' own y, and up their own z, so on it the plates are bands 4 mm tall 0.2 apart
and each pin a band 5 mm wide 0.25 from its hole's walls. A cube from 0 to 10 checks that a plane
square to an axis reads as the axis form does. Nothing is any Sponsor's design.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from print_shop.bench import host
from print_shop.bench.placed import Placed

pytestmark = pytest.mark.bench

PARTS = """
import cadquery as cq


def plate(z0, z1):
    found = cq.Workplane("XY").box(30, 20, z1 - z0).translate((0, 0, (z0 + z1) / 2))
    for x in (-6, 6):
        found = found.cut(
            cq.Workplane("XY").center(x, 0).circle(2.75).extrude(z1 - z0).translate((0, 0, z0))
        )
    return found


cq.exporters.export(plate(-4, 0), "lower.step")
cq.exporters.export(plate(0.2, 4.2), "upper.step")
cq.exporters.export(cq.Workplane("XY").circle(2.5).extrude(8.2).translate((0, 0, -4)), "pin.step")
cq.exporters.export(cq.Workplane("XY").box(10, 10, 10).translate((5, 5, 5)), "cube.step")
"""

#: Each picture's pixels of one exact colour, and the colour at a pixel, in the image.
PIXELS = """
import json
import sys

import numpy as np
from PIL import Image

found = []
for word in sys.argv[1:]:
    name, _, what = word.rpartition(":")
    pixels = np.asarray(Image.open(name).convert("RGB")).astype(int)
    if what.startswith("rgb="):
        rgb = [int(v) for v in what[4:].split(",")]
        found.append(int(np.all(pixels == rgb, axis=2).sum()))
    else:
        x, y = (int(v) for v in what.split(","))
        found.append([int(v) for v in pixels[y, x]])
print(json.dumps(found))
"""

TURN = (("x", 35.0), ("z", 20.0))
AT = (10.0, 5.0, 3.0)
PINS = {"pin1": -6.0, "pin2": 6.0}
SIZE = 512
#: The play round each pin, and the seam between the plates.
PLAY = 0.25
SEAM = 0.2
#: How far a figure on the cut may be from `clearance`'s: the fine mesh's chords on both files.
CHORDS = 0.0041


def turned() -> np.ndarray:
    return Placed("", "", TURN, AT, "").matrix()


def placed_at(local: tuple[float, float, float]) -> list[float]:
    matrix = turned()
    return [round(float(v), 6) for v in matrix[:3, :3] @ np.array(local) + matrix[:3, 3]]


def numbers(values: Any) -> str:
    return ",".join(f"{float(v):.9g}" for v in values)


def seam_cut(window: str = ",u=-12:12,v=-5:5") -> str:
    """Square to the seam through both pins' axes: the normal the plates' own y, up their z."""
    matrix = turned()
    normal, up = matrix[:3, :3] @ [0, 1, 0], matrix[:3, :3] @ [0, 0, 1]
    return f"at={numbers(AT)},normal={numbers(normal)},up={numbers(up)}{window}"


def assembly() -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {"name": "lower", "file": "lower.step", "layers": "z", "turn": TURN, "at": AT},
        {"name": "upper", "file": "upper.step", "layers": "z", "turn": TURN, "at": AT},
    ]
    for name, x in PINS.items():
        entry = {"name": name, "file": "pin.step", "layers": "z", "turn": TURN}
        parts.append({**entry, "at": placed_at((x, 0.0, 0.0))})
    return {"parts": parts}


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    found = tmp_path_factory.mktemp("cut-plane")
    (found / "parts.py").write_text(PARTS, encoding="utf-8")
    (found / "pixels.py").write_text(PIXELS, encoding="utf-8")
    made = host.run(["parts.py"], found, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    (found / "plates.json").write_text(json.dumps(assembly()), encoding="utf-8")
    return found


def ask(work: Path, *args: str) -> tuple[str, dict[str, Any]]:
    done = host.run(list(args), work)
    saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
    assert saved, f"nothing saved: {done.stdout}{done.stderr}"
    return done.stdout, json.loads((work / f"{saved.group(1)}.json").read_text())


def read_pixels(work: Path, *where: str) -> list[Any]:
    done = host.run(["pixels.py", *where], work, python=True)
    assert done.returncode == 0, done.stderr
    return list(json.loads(done.stdout))


FILES = ("lower.step", "upper.step", "pin.step")
PIN1, PIN2 = "pin.step as pin1", "pin.step as pin2"


def the_cut(record: dict[str, Any]) -> dict[str, Any]:
    (cut,) = record["numbers"]["cuts"].values()
    return dict(cut)


def test_a_cut_square_to_a_turned_seam_draws_each_outline_where_it_lies(work: Path) -> None:
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--views", "--cut", seam_cut(),
        "--look", "lower.step=blue:lower", "--size", str(SIZE), "--renders-out", "seam",
    )  # fmt: skip
    assert record["verdict"] == "OK", text
    cut = the_cut(record)
    matrix = turned()
    # The plane as it's said: through the point, square to the plates' y, up their z, and so
    # right their -x, since u x v is the normal.
    plane = cut["plane"]
    assert plane["point"] == pytest.approx(list(AT), abs=1e-6)
    assert plane["normal"] == pytest.approx(list(matrix[:3, :3] @ [0, 1, 0]), abs=1e-6)
    assert plane["v"] == pytest.approx(list(matrix[:3, :3] @ [0, 0, 1]), abs=1e-6)
    assert plane["u"] == pytest.approx(list(matrix[:3, :3] @ [-1, 0, 0]), abs=1e-6)
    assert (cut["right"], cut["up"]) == ("u", "v")
    assert "the plane through 10,5,3, normal" in text, text
    files = cut["files"]
    assert set(files) == {"lower.step", "upper.step", PIN1, PIN2}, files
    # Each outline where it should be, in the plane's own mm: the plates clipped to u -12 to 12,
    # each in three pieces between the holes, and each pin a band 5 wide, pin1 at u 6.
    boxes: dict[str, list[list[float]]] = {
        "lower.step": [[-12, -4], [12, 0]],
        "upper.step": [[-12, SEAM], [12, 4 + SEAM]],
        PIN1: [[3.5, -4], [8.5, 4.2]],
        PIN2: [[-8.5, -4], [-3.5, 4.2]],
    }
    for name, box in boxes.items():
        flat = [v for corner in files[name]["box_mm"] for v in corner]
        assert flat == pytest.approx([v for corner in box for v in corner], abs=0.005), name
    for plate in ("lower.step", "upper.step"):
        # Drawn in three pieces between the holes; its area the whole plane's, 30 mm less two
        # holes of 5.5, 4 tall.
        assert files[plate]["pieces"] == 3
        assert files[plate]["area_mm2"] == pytest.approx((30 - 2 * 5.5) * 4, abs=0.05)
        assert files[plate]["area_occt_mm2"] == pytest.approx((30 - 2 * 5.5) * 4, abs=0.01)
    for pin in (PIN1, PIN2):
        assert files[pin]["area_mm2"] == pytest.approx(5 * 8.2, abs=0.05)
    assert cut["overlaps"] == []
    # Drawn to scale: 24 mm across the picture with room for the axes on its sides.
    scale = cut["px_per_mm"]
    assert scale == pytest.approx(SIZE / (1.3 * 24), rel=1e-3)
    # The lower plate in the look's blue, its left piece away from its number; the upper not.
    margin = (SIZE - np.array([24, 10]) * scale) / 2
    (picture,) = [f for f in record["files"] if "-cut-at-" in str(f)]

    def pixel(u: float, v: float) -> str:
        x = (u + 12) * scale + margin[0]
        y = SIZE - ((v + 5) * scale + margin[1])
        return f"{picture}:{round(x)},{round(y)}"

    lower, upper, count = read_pixels(
        work, pixel(-10.4, -3), pixel(-10.4, 3), f"{picture}:rgb=40,90,220"
    )
    assert lower == [40, 90, 220], lower
    assert upper != [40, 90, 220], upper
    # What's drawn of it, 24 mm less the holes, is painted in it, less its outlines and number.
    painted = count / scale**2
    assert 0.85 * 52 < painted <= 52.5, painted


def test_the_gaps_on_the_cut_are_clearances_and_lie_where_it_says(work: Path) -> None:
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--views", "--cut", seam_cut(),
        "--gaps", "--size", str(SIZE), "--renders-out", "gaps",
    )  # fmt: skip
    assert record["verdict"] == "OK", text
    gaps = {frozenset(g["files"]): g for g in the_cut(record)["gaps"]}
    names = ["lower.step", "upper.step", PIN1, PIN2]
    assert set(gaps) == {frozenset((a, b)) for a in names for b in names if a < b}, gaps
    _, measured = ask(work, "clearance", *FILES, "--placed", "plates.json")
    pairs = {frozenset((p["a"], p["b"])): p for p in measured["numbers"]["pairs"]}
    assert set(pairs) == set(gaps)
    expected = {
        frozenset(("lower.step", "upper.step")): SEAM,
        frozenset((PIN1, PIN2)): 12 - 5,
        **{frozenset((p, plate)): PLAY for p in (PIN1, PIN2) for plate in names[:2]},
    }
    for both, gap in gaps.items():
        # The figure is OCCT's on these STEP files, as `clearance`'s is, and the outlines agree.
        assert gap["gap_by"] == "occt", gap
        assert gap["gap_mm"] == pytest.approx(pairs[both]["gap_mm"], abs=1e-4), (gap, both)
        assert gap["gap_mm"] == pytest.approx(expected[both], abs=1e-4), gap
        assert gap["gap_outline_mm"] == pytest.approx(pairs[both]["gap_mm"], abs=CHORDS), gap
    # Where a pin's gap lies, back in the plates' own frame: on the plane (their y 0), one end on
    # the plate's hole and the other on the pin, each that far from the pin's axis.
    inverse = np.linalg.inv(turned())
    for pin, x in ((PIN1, PINS["pin1"]), (PIN2, PINS["pin2"])):
        for plate, low, high in (("lower.step", -4, 0), ("upper.step", SEAM, 4 + SEAM)):
            gap = gaps[frozenset((pin, plate))]
            ends = dict(zip(gap["files"], gap["at"], strict=True))
            for name, radius in ((plate, 2.75), (pin, 2.5)):
                local = (inverse @ [*ends[name], 1.0])[:3]
                assert local[1] == pytest.approx(0, abs=1e-3), (name, local)
                assert abs(local[0] - x) == pytest.approx(radius, abs=CHORDS), (name, local)
                assert low - 1e-3 <= local[2] <= high + 1e-3, (name, local)
    assert "least gap lower.step | upper.step 0.200 mm by OCCT, from u,v" in text, text
    assert f"least gap lower.step | {PIN1} 0.250 mm by OCCT" in text, text


def test_one_copy_of_a_repeated_part_is_cut_alone_with_its_gaps(work: Path) -> None:
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--part", "pin1", "--views",
        "--cut", seam_cut(), "--gaps", "--size", "256", "--renders-out", "one-pin",
    )  # fmt: skip
    assert record["verdict"] == "OK", text
    cut = the_cut(record)
    assert set(cut["files"]) == {"lower.step", "upper.step", PIN1}, cut["files"]
    assert len(cut["gaps"]) == 3


def test_a_stretch_of_the_plane_reads_the_gaps_inside_it(work: Path) -> None:
    """Only pin2 and the plates' stretch round it: pin1 isn't drawn, and has no gap."""
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--views", "--gaps",
        "--cut", seam_cut(",u=-10:-2,v=-1:1"), "--size", "256", "--renders-out", "stretch",
    )  # fmt: skip
    assert record["verdict"] == "OK", text
    cut = the_cut(record)
    assert set(cut["files"][PIN1]) == {"area_mm2", "pieces", "area_occt_mm2"}, cut["files"]
    assert cut["files"][PIN1]["pieces"] == 0
    found = {frozenset(g["files"]): g["gap_mm"] for g in cut["gaps"]}
    assert found == pytest.approx(
        {
            frozenset(("lower.step", "upper.step")): SEAM,
            frozenset(("lower.step", PIN2)): PLAY,
            frozenset(("upper.step", PIN2)): PLAY,
        },
        abs=1e-4,
    )


@pytest.mark.parametrize(
    ("plane", "axis"),
    [
        ("at=5,5,3,normal=0,0,1", "z=3"),
        ("at=7,5,5,normal=1,0,0", "x=7"),
        ("at=5,4,5,normal=0,-1,0", "y=4"),
    ],
)
def test_a_plane_square_to_an_axis_reads_as_the_axis_form(
    work: Path, plane: str, axis: str
) -> None:
    """With no up given, +z laid on the plane, or +y on a level one: the axis form's own right
    and up, so the same cut is drawn the same way round."""
    text, record = ask(work, "render", "cube.step", "--views", "--cut", plane)
    assert record["verdict"] == "OK", text
    _, plain = ask(work, "render", "cube.step", "--views", "--cut", axis)
    mine, theirs = the_cut(record), the_cut(plain)
    sides = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}
    assert mine["plane"]["u"] == pytest.approx(sides[theirs["right"]], abs=1e-9)
    assert mine["plane"]["v"] == pytest.approx(sides[theirs["up"]], abs=1e-9)
    assert mine["files"]["cube.step"]["area_mm2"] == pytest.approx(100, abs=1e-6)
    assert theirs["files"]["cube.step"]["area_mm2"] == pytest.approx(100, abs=1e-6)
    assert "box_mm" not in theirs["files"]["cube.step"], "the axis form's numbers are as they were"


def test_a_plane_on_a_face_says_so_with_one_to_copy_beside_it(work: Path) -> None:
    """The lower plate's top face, turned off the axes: degenerate, and the plane 0.05 mm along its
    normal, inside the pin, is drawn."""
    matrix = turned()
    normal = matrix[:3, :3] @ [0, 0, 1]
    face = f"at={numbers(AT)},normal={numbers(normal)}"
    text, record = ask(work, "render", *FILES, "--placed", "plates.json", "--views", "--cut", face)
    assert record["verdict"] == "NO ANSWER", text
    why = record["why"]
    assert "lies exactly on a face of lower.step" in why, why
    nearby = why.split("such as --cut ")[1].split(" or ")[0]
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--views", "--cut", nearby
    )
    assert record["verdict"] == "OK", text


@pytest.mark.parametrize(
    ("cut", "says"),
    [
        ("at=1,2,normal=0,0,1", "a plane's at is three numbers"),
        ("at=1,2,3", "a plane's cut is at=X,Y,Z,normal=NX,NY,NZ"),
        ("at=1,2,3,normal=0,0,0", "a plane's normal is a direction"),
        ("at=1,2,3,normal=0,0,1,up=0,0,2", "a plane's up lies in it"),
        ("at=1,2,3,normal=0,0,1,w=0:1", "a plane's cut is at="),
        ("at=1,2,3,normal=0,0,1,u=4:2", "low to high"),
        ("w=3", "starts with x, y or z"),
    ],
)
def test_a_plane_that_cant_be_read_says_so(work: Path, cut: str, says: str) -> None:
    text, record = ask(work, "render", "cube.step", "--views", "--cut", cut)
    assert record["verdict"] == "NO ANSWER" and says in record["why"], text


def test_gaps_with_no_cut_says_so(work: Path) -> None:
    text, record = ask(work, "render", "cube.step", "--views", "top", "--gaps", "--size", "128")
    assert record["verdict"] == "NO ANSWER" and "give a --cut" in record["why"], text


def test_the_sheet_of_a_plane_cut_in_its_look_has_its_legend(work: Path) -> None:
    """With views, a plane's cut is a picture on the sheet beside them, as an axis cut is."""
    text, record = ask(
        work, "render", *FILES, "--placed", "plates.json", "--views", "iso", "--cut",
        seam_cut(), "--gaps", "--look", "pin.step=amber", "--size", "256", "--renders-out", "sheet",
    )  # fmt: skip
    assert record["verdict"] == "OK", text
    drawn = {f["file"]: f for f in record["numbers"]["files"]}
    assert drawn[PIN1]["rgb"] == drawn[PIN2]["rgb"] == [255, 176, 0], drawn
    assert any(str(f).endswith("-sheet.png") for f in record["files"]), record["files"]
