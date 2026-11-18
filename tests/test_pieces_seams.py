"""`pieces build --seam` and `pieces seam` on domes made from rules written here, in the bench's
image with no network (mqucifer/print-shop#166). Needs `scripts/bench.py build`.

A dome's slices join edge to edge: each long edge is bevelled by half the angle between the two
slices there, with pins on one and holes on the other, inside the lead line. Right answers are
arithmetic: the angle between two flat rows from their corners on the dome, by cross products
worked out here in plain Python and not by the bench's code.
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
#: A lead wide enough for a pin and its hole in each half of a seam's line.
WIDE = 2.6
#: The planned lamp's lead: each half of a seam's line is 0.7 mm.
NARROW = 1.4
BANDS = [
    {"pattern": "grid", "height_mm": 56, "rows": 2, "columns": 1, "colours": ["amber", "green"]},
    {"pattern": "swag", "height_mm": 46, "depth_mm": 14, "colours": ["blue", "cream"]},
    {"pattern": "strip", "count": 2, "colours": ["red", "gold"]},
]
THICK, PANE, PLAY = 3.0, 1.0, 0.25
#: The pin as the tests state it: along the edge, through the frame, and out of the face.
PIN, PIN_PLAY = (1.2, 1.6, 0.5), 0.1


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("seams")
    for name, lead in (("wide", WIDE), ("narrow", NARROW)):
        (work / f"{name}.json").write_text(json.dumps({"lead_mm": lead, "bands": BANDS}))

    def ask(*args: Any) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    for name in ("wide", "narrow"):
        made = ask(
            "pieces", "dome", "--diameter", DIAMETER, "--height", HEIGHT, "--repeats", REPEATS,
            "--bands", f"{name}.json", "--to", f"{name}.pieces.json",
        )  # fmt: skip
        assert made.verdict == "OK", made.text
        flat = ask("pieces", "flatten", f"{name}.pieces.json")
        assert flat.verdict == "OK", flat.text
    return ask


def build(bench: Any, name: str, number: int, seam: str, out: str, *more: Any) -> Answer:
    lead = WIDE if name.startswith("wide") else NARROW
    return bench(  # type: ignore[no-any-return]
        "pieces", "build", f"{name}.slice-{number:02d}.pieces.json", "--thickness", THICK,
        "--pane", PANE, "--lip", "1.2,0.8", "--play", PLAY, "--border", lead / 2,
        "--seam", seam, "--build-out", out, *more,
    )  # fmt: skip


def surface(bench: Any, name: str, number: int) -> dict[str, Any]:
    text = (bench.work / f"{name}.slice-{number:02d}.pieces.json").read_text()
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


@pytest.fixture(scope="module")
def pair(bench: Any) -> tuple[Answer, Answer]:
    """Two neighbours of the wide dome, each with holes on its left edge and pins on its
    right."""
    one = build(bench, "wide", 1, "holes,pins", "one")
    two = build(bench, "wide", 2, "holes,pins", "two")
    assert one.verdict == "OK" and one.code == 0, one.text
    assert two.verdict == "OK" and two.code == 0, two.text
    return one, two


def test_each_length_is_bevelled_by_half_the_angle_between_the_two_slices_there(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, _two = pair
    mine, next_to = row_normals(surface(bench, "wide", 1)), row_normals(surface(bench, "wide", 2))
    seam = one.numbers["seam"]
    assert seam["left"]["kind"] == "holes" and seam["right"]["kind"] == "pins"
    assert seam["right"]["slice"] == 2 and seam["left"]["slice"] == 12
    lengths = seam["right"]["lengths"]
    assert len(lengths) == len(mine) == 4, "two grid rows, the swag and the strip"
    for length, a, b in zip(lengths, mine, next_to, strict=True):
        assert length["bevel_deg"] == pytest.approx(angle_deg(a, b) / 2.0, abs=1e-4)
        assert length["pins"] == 1
    # Twelve slices stand 30 degrees apart where they're upright: the hem's row is the
    # nearest to that, and each row above it leans in more and stands less far from its
    # neighbour's.
    bevels = [length["bevel_deg"] for length in lengths]
    assert 14.0 < bevels[0] < 15.0 and bevels == sorted(bevels, reverse=True)
    assert one.numbers["pins_count"] == 4 and one.numbers["holes_count"] == 4
    assert one.numbers["frame_solids"] == 1
    assert "bevel" in one.text and "pin" in one.text


def test_two_neighbours_bent_and_placed_meet_within_the_gap_with_every_pin_in_its_hole(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, two = pair
    met = bench(
        "pieces", "seam", "wide.slice-01.pieces.json", "wide.slice-02.pieces.json",
        "--frames", one.numbers["glass"]["frame"], two.numbers["glass"]["frame"],
    )  # fmt: skip
    assert met.verdict == "OK" and met.code == 0, met.text
    assert met.numbers["seam_between"] == [1, 2]
    assert met.numbers["lengths_count"] == 4
    assert met.numbers["gap_limit_mm"] == pytest.approx(0.05)
    assert 0.0 <= met.numbers["gap_mm"] <= 0.01, "the faces are built on the cut's own plane"
    assert met.numbers["faces_apart_deg"] <= 0.01
    assert met.numbers["overlap_mm3"] <= 1e-3, "faces that touch share none"
    assert met.numbers["pins_count"] == 4 and met.numbers["pins_in_holes"] == 4
    assert met.numbers["pin_play_mm"] == pytest.approx(PIN_PLAY, abs=0.02)
    assert (bench.work / met.numbers["placed"]).exists(), "the two slices as placed, to look at"


def test_a_pin_with_no_hole_to_enter_is_found(bench: Any, pair: tuple[Answer, Answer]) -> None:
    """The control: slice 2 with two holes a length where slice 1 has one pin."""
    one, _two = pair
    other = build(bench, "wide", 2, "holes,pins", "two-pins", "--pins", 2)
    assert other.verdict == "OK", other.text
    met = bench(
        "pieces", "seam", "wide.slice-01.pieces.json", "wide.slice-02.pieces.json",
        "--frames", one.numbers["glass"]["frame"], other.numbers["glass"]["frame"],
    )  # fmt: skip
    assert met.verdict == "INTERFERE" and met.code == 1, met.text
    assert met.numbers["pins_in_holes"] == 0 and met.numbers["overlap_mm3"] > 0.1
    assert "pin" in met.record["why"]


def test_square_edges_dont_meet_face_to_face(bench: Any) -> None:
    """The control for the gap: two slices built with no seam are square-edged. Placed, their
    edges stand apart by the whole angle between the slices, open at the outer face."""
    frames = []
    for number in (1, 2):
        built = bench(
            "pieces", "build", f"wide.slice-{number:02d}.pieces.json", "--thickness", THICK,
            "--pane", PANE, "--lip", "1.2,0.8", "--play", PLAY, "--border", WIDE / 2,
            "--build-out", f"square-{number}",
        )  # fmt: skip
        assert built.verdict == "OK", built.text
        frames.append(built.numbers["glass"]["frame"])
    met = bench(
        "pieces", "seam", "wide.slice-01.pieces.json", "wide.slice-02.pieces.json",
        "--frames", *frames,
    )  # fmt: skip
    assert met.code == 1 and met.verdict in ("FAIL", "INTERFERE"), met.text
    mine, next_to = row_normals(surface(bench, "wide", 1)), row_normals(surface(bench, "wide", 2))
    assert met.numbers["faces_apart_deg"] == pytest.approx(angle_deg(mine[0], next_to[0]), abs=0.3)
    # One face's outer corner, half the frame's thickness out from the cut's middle, stands
    # off the other face by that half times the sine of the angle between them.
    open_at = THICK / 2.0 * math.sin(math.radians(angle_deg(mine[0], next_to[0])))
    assert met.numbers["gap_mm"] == pytest.approx(open_at, abs=0.1)
    assert met.numbers["pins_count"] == 0


def test_a_seam_that_would_cut_a_pocket_is_refused(bench: Any) -> None:
    """On the planned lead each half of a seam's line is 0.7 mm: a hole leaves no wall a
    nozzle prints between it and the pocket beside it."""
    refused = build(bench, "narrow", 1, "holes,pins", "narrow")
    assert refused.verdict == "FAIL" and refused.code == 1, refused.text
    assert "pocket" in refused.record["why"] and "hole" in refused.record["why"]
    assert not (bench.work / "narrow" / "frame.step").exists(), "nothing is built"
    assert refused.numbers["seam_refused"], "each place it would cut, with its figure"
    worst = refused.numbers["seam_refused"][0]
    assert worst["wall_mm"] < worst["least_mm"] == pytest.approx(0.4)
    assert refused.numbers["lead_needed_mm"] > NARROW
    # The bevels alone fit that lead, and so does a build.
    plain = build(bench, "narrow", 1, "plain,plain", "narrow-plain")
    assert plain.verdict == "OK", plain.text
    assert plain.numbers["pins_count"] == 0 and plain.numbers["holes_count"] == 0


def test_a_bevel_that_would_cut_a_pocket_is_refused(bench: Any) -> None:
    """Four slices stand 90 degrees apart at the hem: a bevel of 45 degrees on a frame 3 mm
    thick comes in 0.7 mm at the pocket's floor, all of the line's half."""
    flat = bench("pieces", "flatten", "narrow.pieces.json", "--slices", 4, "--to", "four")
    assert flat.verdict == "OK", flat.text
    refused = bench(
        "pieces", "build", "four.slice-01.pieces.json", "--thickness", THICK, "--pane", PANE,
        "--lip", "1.2,0.8", "--play", PLAY, "--border", NARROW / 2, "--seam", "plain,plain",
        "--build-out", "four",
    )  # fmt: skip
    assert refused.verdict == "FAIL", refused.text
    assert "bevel" in refused.record["why"] and "pocket" in refused.record["why"]
    assert not (bench.work / "four" / "frame.step").exists()


def test_a_slice_with_seams_still_passes_check_capture_pause_and_thin(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, _two = pair
    checked = bench("pieces", "check", "wide.slice-01.pieces.json")
    assert checked.verdict == "OK", checked.text
    glass = one.numbers["glass"]
    panes = one.numbers["panes_count"]
    held = bench(
        "capture", glass["frame"], "--pocket-z", glass["pocket_z"], "--clearance", PLAY,
        "--pane-thickness", PANE, "--panes", panes,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    paused = bench(
        "pause", glass["frame"], "--pocket-z", glass["pocket_z"], "--pane-thickness", PANE
    )
    assert paused.verdict == "OK", paused.text
    whole = bench("thin", glass["frame"], "--one-body")
    assert whole.verdict == "OK", whole.text
    # The pockets and openings are those of a build with no seam: only the frame's sides differ.
    square = bench(
        "pieces", "build", "wide.slice-01.pieces.json", "--thickness", THICK, "--pane", PANE,
        "--lip", "1.2,0.8", "--play", PLAY, "--border", WIDE / 2, "--build-out", "no-seam",
    )  # fmt: skip
    assert square.verdict == "OK", square.text
    assert one.numbers["lips"] == square.numbers["lips"]


@pytest.mark.parametrize(
    ("seam", "says"),
    [
        ("pins,pins", "one edge"),
        ("holes", "LEFT,RIGHT"),
        ("pegs,holes", "LEFT,RIGHT"),
    ],
)
def test_a_seam_that_cant_be_read_says_what_to_give(bench: Any, seam: str, says: str) -> None:
    answer = build(bench, "wide", 1, seam, "bad")
    assert answer.code == 2, answer.text
    assert says in answer.text


def test_a_flat_panel_has_no_seams(bench: Any) -> None:
    answer = bench(
        "pieces", "build", "wide.pieces.json", "--thickness", THICK, "--pane", PANE,
        "--lip", "1.2,0.8", "--play", PLAY, "--seam", "holes,pins", "--build-out", "flat",
    )  # fmt: skip
    assert answer.verdict == "NO ANSWER" and "pieces flatten" in answer.record["why"]


def test_two_slices_that_arent_neighbours_have_no_seam(
    bench: Any, pair: tuple[Answer, Answer]
) -> None:
    one, two = pair
    answer = bench(
        "pieces", "seam", "wide.slice-01.pieces.json", "wide.slice-03.pieces.json",
        "--frames", one.numbers["glass"]["frame"], two.numbers["glass"]["frame"],
    )  # fmt: skip
    assert answer.verdict == "NO ANSWER" and "neighbour" in answer.record["why"]
