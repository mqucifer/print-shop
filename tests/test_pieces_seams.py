"""`pieces build --seam`, `pieces came`, `pieces ring` and `pieces seam` on a dome made from
rules written here, in the bench's image with no network (mqucifer/print-shop#166). Needs
`scripts/bench.py build`.

A dome's slices join edge to edge: each long edge is bevelled by half the angle between the two
slices there, a came strip clips over both edges, and a ring at the hem and the top takes every
slice's edge. Right answers are arithmetic: the angle between two flat rows from their corners
on the dome, by cross products worked out here in plain Python and not by the bench's code.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

import pytest

from print_shop.bench import host
from tests.test_bench import Answer

pytestmark = pytest.mark.bench
DIAMETER, HEIGHT, REPEATS = 200.0, 130.0, 12
#: The planned lamp's lead: each half of a seam's line is 0.7 mm.
LEAD = 1.4
BANDS = [
    {"pattern": "grid", "height_mm": 56, "rows": 2, "columns": 1, "colours": ["amber", "green"]},
    {"pattern": "swag", "height_mm": 46, "depth_mm": 14, "colours": ["blue", "cream"]},
    {"pattern": "strip", "count": 2, "colours": ["red", "gold"]},
]
THICK, PANE, PLAY, LIP = 3.0, 1.0, 0.25, 1.2
#: The seam's gap, and a strip's play, grip and flange, as the tests state them.
GAP, CAME_PLAY, GRIP, FLANGE = 0.6, 0.1, 0.05, (1.5, 0.8)
RINGS = ("rings/hem-ring.step", "rings/top-seat.step", "rings/top-cap.step")


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("seams")
    (work / "lamp.json").write_text(json.dumps({"lead_mm": LEAD, "bands": BANDS}))

    def ask(*args: Any) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    made = ask(
        "pieces", "dome", "--diameter", DIAMETER, "--height", HEIGHT, "--repeats", REPEATS,
        "--bands", "lamp.json", "--to", "lamp.pieces.json",
    )  # fmt: skip
    assert made.verdict == "OK", made.text
    flat = ask("pieces", "flatten", "lamp.pieces.json")
    assert flat.verdict == "OK", flat.text
    return ask


def build(bench: Any, number: int, out: str, *more: Any, name: str = "lamp") -> Answer:
    return bench(  # type: ignore[no-any-return]
        "pieces", "build", f"{name}.slice-{number:02d}.pieces.json", "--thickness", THICK,
        "--pane", PANE, "--lip", f"{LIP},0.8", "--play", PLAY, "--border", LEAD / 2,
        "--build-out", out, *more,
    )  # fmt: skip


def seam(bench: Any, frames: list[str], *more: Any) -> Answer:
    return bench(  # type: ignore[no-any-return]
        "pieces", "seam", "lamp.slice-01.pieces.json", "lamp.slice-02.pieces.json",
        "--frames", *frames, *more,
    )  # fmt: skip


def surface(bench: Any, number: int) -> dict[str, Any]:
    text = (bench.work / f"lamp.slice-{number:02d}.pieces.json").read_text()
    return dict(json.loads(text)["surface"])


def minus(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b, strict=True)]


def cross(a: list[float], b: list[float]) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def angle_deg(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    size = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / size))))


def row_normals(found: dict[str, Any]) -> list[list[float]]:
    """Each flat row's normal, from three of its corners on the dome."""
    left, right = found["edges"]["left"]["on_dome"], found["edges"]["right"]["on_dome"]
    return [
        cross(minus(right[j], left[j]), minus(left[j + 1], left[j])) for j in range(len(left) - 1)
    ]


def joins(bench: Any) -> list[float]:
    """The angle between slice 1's rows and slice 2's, from the hem up."""
    mine, next_to = row_normals(surface(bench, 1)), row_normals(surface(bench, 2))
    return [angle_deg(a, b) for a, b in zip(mine, next_to, strict=True)]


@pytest.fixture(scope="module")
def pair(bench: Any) -> tuple[Answer, Answer]:
    """Two neighbours, each with its two long edges built as seams."""
    one, two = build(bench, 1, "one", "--seam"), build(bench, 2, "two", "--seam")
    assert one.verdict == "OK" and one.code == 0, one.text
    assert two.verdict == "OK" and two.code == 0, two.text
    return one, two


@pytest.fixture(scope="module")
def frames(pair: tuple[Answer, Answer]) -> list[str]:
    return [str(built.numbers["glass"]["frame"]) for built in pair]


@pytest.fixture(scope="module")
def came(bench: Any) -> Answer:
    made = bench("pieces", "came", "lamp.slice-01.pieces.json", "--thickness", THICK, "--lip", LIP)
    assert made.verdict == "OK" and made.code == 0, made.text
    return made  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def rings(bench: Any) -> Answer:
    made = bench("pieces", "ring", "lamp.slice-01.pieces.json", "--thickness", THICK)
    assert made.verdict == "OK" and made.code == 0, made.text
    return made  # type: ignore[no-any-return]


def test_each_length_is_bevelled_by_half_the_angle_between_the_two_slices_there(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, _two = pair
    made = one.numbers["seam"]
    assert made["right"]["slice"] == 2 and made["left"]["slice"] == 12
    lengths = made["right"]["lengths"]
    assert len(lengths) == 4, "two grid rows, the swag and the strip"
    for length, join in zip(lengths, joins(bench), strict=True):
        assert length["bevel_deg"] == pytest.approx(join / 2.0, abs=1e-4)
    # Twelve slices stand 30 degrees apart where they're upright: the hem's row is the
    # nearest to that, and each row above it leans in more and stands less far from its
    # neighbour's.
    bevels = [length["bevel_deg"] for length in lengths]
    assert 14.0 < bevels[0] < 15.0 and bevels == sorted(bevels, reverse=True)
    assert one.numbers["seam_gap_mm"] == pytest.approx(GAP)
    # Half the gap back from the cut's middle, which is 0.7 mm from the glass, less what the
    # face leans out by the pocket's floor: a nozzle's width of lead is left.
    assert 0.4 <= one.numbers["seam_wall_mm"] < LEAD / 2
    assert one.numbers["frame_solids"] == 1 and "bevel" in one.text


def test_two_neighbours_bent_and_placed_stand_face_to_face_a_web_apart(
    bench: Any, frames: list[str]
) -> None:
    met = seam(bench, frames)
    assert met.verdict == "OK" and met.code == 0, met.text
    assert met.numbers["seam_between"] == [1, 2] and met.numbers["lengths_count"] == 4
    assert met.numbers["gap_mm"] == pytest.approx(GAP, abs=0.005)
    assert met.numbers["gap_least_mm"] == pytest.approx(GAP, abs=0.005)
    assert met.numbers["faces_apart_deg"] <= 0.01
    assert met.numbers["overlap_mm3"] <= 1e-3, "faces a web apart share none"
    assert (bench.work / met.numbers["placed"]).exists(), "the two slices as placed, to look at"


def test_square_edges_dont_meet_face_to_face(bench: Any) -> None:
    """The control for the faces: two slices built with no seam are square-edged. Placed, their
    edges stand apart by the whole angle between the slices, and one passes into the other."""
    square = []
    for number in (1, 2):
        built = build(bench, number, f"square-{number}")
        assert built.verdict == "OK", built.text
        square.append(built.numbers["glass"]["frame"])
    met = seam(bench, square)
    assert met.code == 1 and met.verdict in ("FAIL", "INTERFERE"), met.text
    assert met.numbers["faces_apart_deg"] == pytest.approx(joins(bench)[0], abs=0.3)
    assert met.numbers["gap_least_mm"] < 0 < met.numbers["gap_mm"]


def test_slices_built_for_another_gap_are_found(bench: Any, frames: list[str]) -> None:
    """The control for the gap: slice 2 built with no gap stands 0.3 mm nearer than the web
    needs."""
    tight = build(bench, 2, "tight", "--seam", "--seam-gap", 0)
    assert tight.verdict == "OK", tight.text
    met = seam(bench, [frames[0], tight.numbers["glass"]["frame"]])
    assert met.verdict == "FAIL" and "gap" in met.record["why"], met.text
    assert met.numbers["gap_mm"] == pytest.approx(GAP / 2.0, abs=0.005)


def test_a_seam_that_would_cut_a_pocket_is_refused(bench: Any) -> None:
    """A gap of 1.6 mm sets each face 0.8 mm back, past the glass 0.7 mm from the cut."""
    refused = build(bench, 1, "wide-gap", "--seam", "--seam-gap", 1.6)
    assert refused.verdict == "FAIL" and refused.code == 1, refused.text
    assert "pocket" in refused.record["why"]
    assert not (bench.work / "wide-gap" / "frame.step").exists(), "nothing is built"
    worst = refused.numbers["seam_refused"][0]
    assert worst["wall_mm"] < worst["least_mm"] == pytest.approx(0.4)
    assert refused.numbers["lead_needed_mm"] > LEAD


def test_a_slice_with_seams_still_passes_check_capture_pause_and_thin(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, _two = pair
    checked = bench("pieces", "check", "lamp.slice-01.pieces.json")
    assert checked.verdict == "OK", checked.text
    glass = one.numbers["glass"]
    held = bench(
        "capture", glass["frame"], "--pocket-z", glass["pocket_z"], "--clearance", PLAY,
        "--pane-thickness", PANE, "--panes", one.numbers["panes_count"],
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    paused = bench(
        "pause", glass["frame"], "--pocket-z", glass["pocket_z"], "--pane-thickness", PANE
    )
    assert paused.verdict == "OK", paused.text
    whole = bench("thin", glass["frame"], "--one-body")
    assert whole.verdict == "OK", whole.text
    # The pockets and openings are those of a build with no seam: only the frame's sides differ.
    square = build(bench, 1, "no-seam")
    assert square.verdict == "OK", square.text
    assert one.numbers["lips"] == square.numbers["lips"]


def test_a_came_strip_for_each_length_has_its_channels_at_the_slices_angle(
    bench: Any, came: Answer
) -> None:
    strips = came.numbers["strips"]
    assert came.numbers["strips_count"] == len(strips) == 4
    for strip, join in zip(strips, joins(bench), strict=True):
        assert strip["channels_deg"] == pytest.approx(join, abs=1e-4)
        assert strip["channels_read_deg"] == pytest.approx(join, abs=0.01), "from its own faces"
        assert strip["slot_read_mm"] == pytest.approx(THICK + CAME_PLAY, abs=0.005)
        assert (bench.work / strip["file"]).exists()
        # Laid on the bed: on two flanges' ends, its web a bridge a slot wide, nothing in the air.
        prints = strip["prints"]
        assert prints["fits_bed"] and not prints["supports"] and not prints["unsupported"]
        assert prints["bridges"] == 1 and THICK < prints["bridge_mm"] < THICK + 0.5
        assert prints["on_bed_mm2"] > 0
    assert came.numbers["slot_mm"] == pytest.approx(THICK + CAME_PLAY)
    assert came.numbers["mouth_mm"] == pytest.approx(THICK - 2 * GRIP)
    assert came.numbers["web_mm"] == pytest.approx(GAP - 2 * CAME_PLAY)
    assert came.numbers["flange_wide_mm"] > LEAD, "wider than the lead line"


def test_a_strip_on_two_placed_slices_grips_both_and_covers_no_opening(
    bench: Any, frames: list[str], came: Answer
) -> None:
    met = seam(bench, frames, "--came", came.numbers["placed"])
    assert met.verdict == "OK" and met.code == 0, met.text
    assert met.numbers["strips_count"] == met.numbers["strips_gripping"] == 4
    assert met.numbers["press_mm"] == pytest.approx(GRIP, abs=0.01)
    assert met.numbers["flanges_over_openings"] == 0


def test_a_flange_that_would_lie_over_a_panes_opening_is_refused_and_found(
    bench: Any, frames: list[str]
) -> None:
    wide = ("pieces", "came", "lamp.slice-01.pieces.json", "--thickness", THICK,
            "--flange", "3.5,0.8", "--came-out", "wide")  # fmt: skip
    refused = bench(*wide, "--lip", LIP)
    assert refused.verdict == "FAIL" and "opening" in refused.record["why"], refused.text
    # Told the lip is wider than it is, `came` makes them, and they're found on the slices.
    made = bench(*wide, "--lip", 5)
    assert made.verdict == "OK", made.text
    met = seam(bench, frames, "--came", made.numbers["placed"])
    assert met.verdict == "FAIL" and "opening" in met.record["why"], met.text
    assert met.numbers["flanges_over_openings"] > 0


def test_a_strip_made_for_a_thinner_frame_is_found(bench: Any, frames: list[str]) -> None:
    thin = bench(
        "pieces", "came", "lamp.slice-01.pieces.json", "--thickness", 2.4, "--lip", LIP,
        "--came-out", "thin",
    )  # fmt: skip
    assert thin.verdict == "OK", thin.text
    met = seam(bench, frames, "--came", thin.numbers["placed"])
    assert met.verdict == "INTERFERE" and "strip" in met.record["why"], met.text
    assert met.numbers["press_mm"] > 0.2


def test_the_rings_take_every_slices_edge_and_print_with_no_support(
    bench: Any, rings: Answer
) -> None:
    parts = {part["part"]: part for part in rings.numbers["rings"]}
    assert set(parts) == {"hem-ring", "top-seat", "top-cap"}
    for part in parts.values():
        assert part["solids"] == 1 and (bench.work / part["laid"]).exists()
        prints = part["prints"]
        assert prints["fits_bed"] and not prints["supports"] and not prints["unsupported"]
        assert prints["on_bed_mm2"] > 0
    assert rings.numbers["edges_asked"] == {"hem": 3 * REPEATS, "top": 3 * REPEATS}
    assert rings.numbers["edges_held"] == rings.numbers["edges_asked"]
    assert rings.numbers["groove_mm"] == pytest.approx(THICK + 0.2)
    # The hem ring is a hoop round the dome's hem, and the top's parts cover its opening.
    assert parts["hem-ring"]["prints"]["size_mm"][0] > DIAMETER
    assert parts["top-cap"]["prints"]["size_mm"][0] > DIAMETER / 5


def test_two_placed_slices_edges_are_in_the_rings_grooves(
    bench: Any, frames: list[str], rings: Answer
) -> None:
    assert rings.verdict == "OK"
    met = seam(bench, frames, "--rings", *RINGS)
    assert met.verdict == "OK" and met.code == 0, met.text
    assert met.numbers["edges_in_rings"] == {"hem": [3, 3], "top": [3, 3]}
    assert met.numbers["rings"]["shared_mm3"] <= 1e-3


def test_rings_made_for_a_thinner_frame_are_found(bench: Any, frames: list[str]) -> None:
    thin = bench(
        "pieces", "ring", "lamp.slice-01.pieces.json", "--thickness", 2, "--rings-out", "thin-rings"
    )
    assert thin.verdict == "OK", thin.text
    met = seam(bench, frames, "--rings", *[r.replace("rings/", "thin-rings/") for r in RINGS])
    assert met.verdict == "INTERFERE" and "ring" in met.record["why"], met.text


def test_a_scalloped_hem_has_no_hem_ring(bench: Any) -> None:
    bands = [*BANDS[:2], {"pattern": "scallop", "depth_mm": 10, "colours": ["red"]}]
    (bench.work / "scallop.json").write_text(json.dumps({"lead_mm": LEAD, "bands": bands}))
    made = bench(
        "pieces", "dome", "--diameter", DIAMETER, "--height", HEIGHT, "--repeats", REPEATS,
        "--bands", "scallop.json", "--to", "scallop.pieces.json",
    )  # fmt: skip
    assert made.verdict == "OK", made.text
    assert bench("pieces", "flatten", "scallop.pieces.json").verdict == "OK"
    built = build(bench, 1, "scallop", "--seam", name="scallop")
    assert built.verdict == "OK", built.text
    hoops = bench(
        "pieces", "ring", "scallop.slice-01.pieces.json", "--thickness", THICK,
        "--rings-out", "scallop-rings",
    )  # fmt: skip
    assert hoops.verdict == "OK", hoops.text
    assert [part["part"] for part in hoops.numbers["rings"]] == ["top-seat", "top-cap"]
    assert "no hem ring" in hoops.text


def test_a_flat_panel_has_no_seams(bench: Any) -> None:
    answer = bench(
        "pieces", "build", "lamp.pieces.json", "--thickness", THICK, "--pane", PANE,
        "--lip", "1.2,0.8", "--play", PLAY, "--seam", "--build-out", "flat",
    )  # fmt: skip
    assert answer.verdict == "NO ANSWER" and "pieces flatten" in answer.record["why"]
    for command in ("came", "ring"):
        more = ("--lip", LIP) if command == "came" else ()
        answer = bench("pieces", command, "lamp.pieces.json", "--thickness", THICK, *more)
        assert answer.verdict == "NO ANSWER" and "pieces flatten" in answer.record["why"]


def test_two_slices_that_arent_neighbours_have_no_seam(bench: Any, frames: list[str]) -> None:
    answer = bench(
        "pieces", "seam", "lamp.slice-01.pieces.json", "lamp.slice-03.pieces.json",
        "--frames", *frames,
    )  # fmt: skip
    assert answer.verdict == "NO ANSWER" and "neighbour" in answer.record["why"]
