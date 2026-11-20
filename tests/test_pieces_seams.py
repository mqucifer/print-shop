"""`pieces build --seam`, `pieces came`, `pieces ring` and `pieces seam` on a dome made from
rules written here, in the bench's image with no network (mqucifer/print-shop#166,
mqucifer/print-shop#190). Needs `scripts/bench.py build`.

A dome's slices join edge to edge: each long edge is bevelled by half the angle between the two
slices there, a came strip inside the dome holds both edges by a rib in a groove in each
slice's inner face, and a ring at the hem and the top takes every slice's edge. Right answers
are arithmetic: the angle between two flat rows from their corners on the dome, by cross
products worked out here in plain Python and not by the bench's code. The controls: today's
strip, with a flange over each slice's outer face, and strips and grooves that don't fit.
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
#: The seam's gap; the Sponsor's PETG snap, a side; a groove in a slice's inner face, wide and
#: deep; and two lines of a 0.4 mm nozzle, the least a strip may be anywhere.
GAP, SNAP, GROOVE, LINES = 0.6, 0.10, (0.6, 0.4), 0.8
#: Today's strip (mqucifer/print-shop#166): its play, its beads' grip and its flange.
H_PLAY, H_GRIP, FLANGE = 0.1, 0.05, (1.5, 0.8)
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
def h_came(bench: Any) -> Answer:
    """Today's strip, an H with a flange over each slice's outer face: made, and named."""
    return bench(  # type: ignore[no-any-return]
        "pieces", "came", "lamp.slice-01.pieces.json", "--thickness", THICK, "--lip", LIP,
        "--hold", "h", "--came-out", "h-came",
    )  # fmt: skip


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
    # A groove along each length of both edges, in the inner face, with a nozzle's width of
    # lead between it and the seam's face and between it and the nearest pane's opening.
    grooves = one.numbers["grooves"]
    assert len(grooves) == 8 and {g["edge"] for g in grooves} == {"left", "right"}
    assert one.numbers["groove_mm"] == pytest.approx(list(GROOVE))
    for groove, join in zip(grooves[4:], joins(bench), strict=True):
        face = GAP / 2.0 / math.cos(math.radians(join / 2.0))
        assert groove["near_mm"] == pytest.approx(face + 0.4, abs=1e-4)
        assert groove["far_mm"] - groove["near_mm"] == pytest.approx(GROOVE[0], abs=1e-4)
    assert one.numbers["groove_wall_mm"] >= 0.4
    # The pocket's floor is at 0.8: a groove 0.4 deep leaves two layers of lip over it.
    assert one.numbers["lip_over_groove_mm"] == pytest.approx(0.4)
    right, asked = one.numbers["grooves_read"]
    assert right == asked == 4 * 8, "each groove read back from the frame"


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


def test_a_hold_that_would_cut_the_lead_beside_an_opening_is_refused_with_the_lead_it_needs(
    bench: Any,
) -> None:
    """A lip of 0.9 brings each opening 0.3 mm nearer the seam: a groove 1.0 to 1.6 mm from the
    cut's middle would leave about 0.25 mm of lead beside the opening, under a nozzle."""
    refused = bench(
        "pieces", "build", "lamp.slice-01.pieces.json", "--thickness", THICK, "--pane", PANE,
        "--lip", "0.9,0.8", "--play", PLAY, "--border", LEAD / 2, "--seam",
        "--build-out", "narrow-lip",
    )  # fmt: skip
    assert refused.verdict == "FAIL" and refused.code == 1, refused.text
    assert "opening" in refused.record["why"] and "groove" in refused.record["why"]
    assert not (bench.work / "narrow-lip" / "frame.step").exists(), "nothing is built"
    rows = [r for r in refused.numbers["seam_refused"] if r["what"] == "opening"]
    assert rows and all(r["wall_mm"] < r["least_mm"] == pytest.approx(0.4) for r in rows)
    worst = min(r["wall_mm"] for r in rows)
    # Each mm the lead is wider takes each opening half a mm further from the cut's middle.
    assert refused.numbers["lead_needed_mm"] >= LEAD + 2 * (0.4 - worst) - 1e-9
    assert refused.numbers["lead_needed_mm"] < LEAD + 1


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


def test_a_came_strip_for_each_length_has_its_ribs_at_the_slices_angle(
    bench: Any, came: Answer
) -> None:
    strips = came.numbers["strips"]
    assert came.numbers["strips_count"] == len(strips) == 4
    for strip, join in zip(strips, joins(bench), strict=True):
        assert strip["join_deg"] == pytest.approx(join, abs=1e-4)
        assert strip["read_deg"] == pytest.approx(join, abs=0.01), "from its own faces"
        assert strip["read_mm"] == pytest.approx(GROOVE[0] + 2 * SNAP, abs=0.005)
        assert (bench.work / strip["file"]).exists()
        # Nothing over the outer face: the rib stands 0.3 mm into the slice, the groove's 0.4
        # less the room over it.
        assert strip["over_outer_face_mm"] == 0
        assert strip["into_slice_mm"] == pytest.approx(GROOVE[1] - 0.1, abs=1e-4)
    assert came.numbers["rib_mm"] == pytest.approx(GROOVE[0] + 2 * SNAP)
    assert came.numbers["grip_mm"] == pytest.approx(SNAP)
    assert came.numbers["over_outer_face_mm"] == 0


def test_each_strip_prints_as_laid_on_its_bar_with_nothing_under_two_lines(came: Answer) -> None:
    """Today's strip stood on two flanges' ends, 0.8 mm each, and its web was a bridge 0.4 mm
    thick. A strip now lies on its bar's flat underside, and nothing in it is under two
    lines."""
    for strip in came.numbers["strips"]:
        prints = strip["prints"]
        assert prints["fits_bed"] and not prints["supports"] and not prints["unsupported"]
        assert prints["bridges"] == 0
        # On more than two lines along its length: its bar, across, by its length.
        assert prints["on_bed_mm2"] > 2 * LINES * strip["length_mm"]
        assert prints["on_bed_mm2"] == pytest.approx(
            strip["underside_mm"] * strip["length_mm"], rel=1e-3
        )
        assert strip["least_feature_mm"] >= LINES - 1e-6
    assert came.numbers["least_feature_mm"] >= LINES - 1e-6
    assert came.numbers["on_bed_mm2"] > 0


def test_two_slices_with_their_strips_read_ok_with_nothing_over_either_outer_face(
    bench: Any, frames: list[str], came: Answer
) -> None:
    met = seam(bench, frames, "--came", came.numbers["placed"])
    assert met.verdict == "OK" and met.code == 0, met.text
    assert met.numbers["strips_count"] == met.numbers["strips_holding"] == 4
    assert met.numbers["over_outer_face"] == 0 and met.numbers["over_outer_face_mm"] == 0
    for strip in met.numbers["strips"]:
        assert strip["holds"] == ["a rib in its groove", "a rib in its groove"]
        assert not strip["in_gap"], "nothing in the gap"
        for side in strip["sides"]:
            assert side["outer_probed"] == 0, "and nothing past the outer face by points"
            assert side["rib"]["in_groove"]
    # Each rib is 0.10 mm a side wider than its groove.
    assert met.numbers["press_mm"] == pytest.approx(SNAP, abs=0.005)
    assert met.numbers["over_inner_face_mm"] >= LINES
    assert met.numbers["strips_over_openings"] == 0


def test_todays_outer_flange_is_found_over_the_outer_face(
    bench: Any, frames: list[str], h_came: Answer
) -> None:
    """The control: mqucifer/print-shop#166's strip, an H with a flange 0.8 mm thick over each
    slice's outer face, 0.05 mm of play under it. `came` names it, and `seam` finds it there."""
    assert h_came.verdict == "FAIL" and "outer face" in h_came.record["why"], h_came.text
    assert h_came.numbers["over_outer_face_mm"] == pytest.approx(H_PLAY / 2 + FLANGE[1], abs=1e-3)
    # Its web, the gap less the play each side, is one line, and its beads are less.
    assert h_came.numbers["least_feature_mm"] <= GAP - 2 * H_PLAY + 1e-6
    met = seam(bench, frames, "--came", h_came.numbers["placed"])
    assert met.verdict == "FAIL" and "outer face" in met.record["why"], met.text
    assert met.numbers["over_outer_face"] == 4
    assert met.numbers["over_outer_face_mm"] == pytest.approx(H_PLAY / 2 + FLANGE[1], abs=0.01)
    for strip in met.numbers["strips"]:
        assert strip["holds"] == ["a channel round its edge", "a channel round its edge"]
        assert strip["over_outer_probed"], "found past the outer face by points too"


def test_a_strip_that_would_lie_over_a_panes_opening_is_refused_and_found(
    bench: Any, frames: list[str]
) -> None:
    wide = ("pieces", "came", "lamp.slice-01.pieces.json", "--thickness", THICK,
            "--bar", "3.5,0.8", "--came-out", "wide")  # fmt: skip
    refused = bench(*wide, "--lip", LIP)
    assert refused.verdict == "FAIL" and "opening" in refused.record["why"], refused.text
    # Told the lip is wider than it is, `came` makes them, and they're found on the slices.
    made = bench(*wide, "--lip", 5)
    assert made.verdict == "OK", made.text
    met = seam(bench, frames, "--came", made.numbers["placed"])
    assert met.verdict == "FAIL" and "opening" in met.record["why"], met.text
    assert met.numbers["strips_over_openings"] > 0


def test_a_strip_made_for_another_gap_is_found(bench: Any, frames: list[str]) -> None:
    """Made for a gap of 1.0, each rib stands 0.2 mm further from the cut's middle than its
    groove: it goes 0.3 mm into the groove's far wall. Its bar is held to the reach the
    openings leave, so it's the rib that's found."""
    other = bench(
        "pieces", "came", "lamp.slice-01.pieces.json", "--thickness", THICK, "--lip", LIP,
        "--gap", 1.0, "--bar", "1.8,0.8", "--came-out", "other-gap",
    )  # fmt: skip
    assert other.verdict == "OK", other.text
    met = seam(bench, frames, "--came", other.numbers["placed"])
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
