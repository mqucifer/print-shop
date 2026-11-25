"""`pieces build --flush`: each pane with a raised face level with the lead, on a panel drawn in
code, in the bench's image with no network (mqucifer/print-shop#287). Needs `scripts/bench.py
build`.

`bench_cases/planted_pieces.py` draws `flush.json`: three pieces of a 60 x 40 mm panel, one with
square corners and a curved side, one a triangle with a corner of 29.7 degrees, and one between.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import standing
from tests.test_bench import Answer

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"
BUILD = ["--thickness", 3, "--pane", 1, "--lip", 1.2, "--play", 0.25]
COLOURS = ("blue", "gold", "green")
#: The STEP data `pieces build` wrote without `--flush` on main, dcf3391, before it was added:
#: the hash of each file's data, past its header, which names the second it was written, as
#: `paths.content_hash` reads it. By the bench image's architecture, as each writes other
#: last figures, from the same image's Dockerfile: arm64 in `print-shop/bench:0.15` built on
#: the Mac, amd64 in the one CI built for mqucifer/print-shop#291, run on the Mac under
#: emulation, twice the same. A new image whose OpenCascade writes other data is a new
#: reference, made again from main's code. Saved, with how they were made:
#: `~/print-shop/proofs/ux-287/`.
BEFORE = {
    "arm64": {
        "flat/frame.step": "d3cfe5c89b449d879b8823272fd1f09f0b55633ddf0e849312c42ef3aaa53feb",
        "flat/pane_blue.step": "a3f293759d56115dba42a8c794a67339cd5804eafead48127217a3fc975a16d7",
        "flat/pane_gold.step": "188eb47809383514eba220bb41cfe9ac05482188a9ec9dda617dc8777dfe3fa5",
        "flat/pane_green.step": "33f6c3a554e0b393fe0c685c2243ce80bfe84cacafd6c3c9c29a35219dd1993a",
        "hills/frame.step": "a0f58681ed0bdeb39dd0b5e0a9cd289fae26524df1eb39607c8f95265a005c0d",
        "hills/pane_blue.step": "e4b4ac04fde9b88d9de9d6d28352d714e78bb190c831bbb1965ebdf74be59e53",
        "hills/pane_green.step": "f5daa7d0f0d4224a137d7f14a53721154d64558714be369de836ab9381f55da2",
        "hills/pane_white.step": "5e2519f58da5bd78721c8748b35b02884742bf49bcd429fa1b0ae5123a8217bb",
        "hills/pane_yellow.step": (
            "0cc027861a2d14550549c98d995647d7b24b458f9cbaf6f04e43d89735487d54"
        ),
    },
    "amd64": {
        "flat/frame.step": "f9f70a060c602c1353723106bb5ab09368e420fb7da6ce64f95fbb76da99ce51",
        "flat/pane_blue.step": "f6b1aab7f3ea0612a66b8b7123d813940ade8476ab8c02cafd911bd7dabad570",
        "flat/pane_gold.step": "188eb47809383514eba220bb41cfe9ac05482188a9ec9dda617dc8777dfe3fa5",
        "flat/pane_green.step": "70de21fc78c01747e8c926d27d25a7d0ff8653042732e0ec344f68bc1714b3bf",
        "hills/frame.step": "0b3da0921ba99eddc8d500276bb09236ee765bbe7189d7398b091d0ef03ccad3",
        "hills/pane_blue.step": "bab0fac845bd1324d7d36e94444c0c73de7092732223d1edd86b8cd98e130695",
        "hills/pane_green.step": "00b74a88f0d47298bc5178ff4e720db8b3a70575018c66ce17462e1b8726a2d2",
        "hills/pane_white.step": "4bb34fb6cf7f8c04da5b1c5e1155284c601d4b7dece5eb4adba0635c4a5aba2e",
        "hills/pane_yellow.step": (
            "a02574957cd5a07886bb419128eab11e488d1567cd9c98e9608881c6ee5d5f8e"
        ),
    },
}


@pytest.fixture(scope="module")
def bench(tmp_path_factory: pytest.TempPathFactory) -> Any:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("flush")
    shutil.copy(CASES / "planted_pieces.py", work / "planted_pieces.py")
    made = host.run(["planted_pieces.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr

    def ask(*args: Any) -> Answer:
        done = host.run([str(a) for a in args], work)
        found = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert found, f"nothing saved: {done.stdout}{done.stderr}"
        record = json.loads((work / f"{found.group(1)}.json").read_text())
        return Answer(done.returncode, done.stdout, record)

    ask.work = work  # type: ignore[attr-defined]
    return ask


@pytest.fixture(scope="module")
def flush(bench: Any) -> Answer:
    answer = bench("pieces", "build", "flush.json", *BUILD, "--flush", "--build-out", "fl")
    assert answer.verdict == "OK", answer.text
    return answer  # type: ignore[no-any-return]


def test_a_flush_panel_is_level_with_its_lead_and_each_face_clear_by_the_play(
    bench: Any, flush: Answer
) -> None:
    """Each pane's raised face ends on the bed, as the lead does, and stands the play from its
    opening all round, read from the solids: the build's own reading of its files' meshes, and
    `clearance` and `info` on the files, each its own command."""
    numbers = flush.numbers
    assert numbers["flush"] is True and numbers["panes_count"] == 3
    # A 1 mm body on 0.8 mm of lip, under 1 mm: the face is the lip's height.
    assert (numbers["face_mm"], numbers["body_mm"], numbers["face_play_mm"]) == (0.8, 1.0, 0.25)
    assert (numbers["pocket_floor_z"], numbers["pane_top_z"], numbers["pause_z"]) == (0.8, 1.8, 2.0)
    assert numbers["pause_after_layer"] == 10
    read = numbers["flush_read"]
    assert read["level_mm"] == pytest.approx(0.0, abs=1e-3)
    assert read["faces"] == read["bodies"] == 3
    assert read["face_in_lead_mm2"] == pytest.approx(0.0, abs=1e-4)
    # The play all round, at points round each face, to the mesh's chords: nowhere less.
    assert 0.25 - 0.004 <= read["face_play_least_mm"] <= 0.25 + 0.007
    # Under the lip by the lip less the play, 0.95, or up to --fit more where it was refitted.
    assert 0.95 - 0.016 <= read["lip_over_body_least_mm"] <= 1.05
    assert read["laid_in_air_mm2"] == pytest.approx(0.0, abs=0.01)
    # As far as a face reaches past its opening, pushed with its body: at the play, no further.
    assert numbers["face_reach_mm"] <= 0.012
    # The triangle's 29.7 degree corner: its face stops short where a nozzle can't print it.
    tips = numbers["face_tips"]
    assert [t["piece"] for t in tips] == [2] and tips[0]["mm"] > 0.3, tips
    frame = numbers["glass"]["frame"]
    files = {c: f"fl/pane_{c}.step" for c in COLOURS}
    assert numbers["glass"]["panes"] == list(files.values())
    assert numbers["glass"]["pane_thickness"] == 1.0
    # Each face's play by OCCT's exact distance, in a box from the bed to the lip's top.
    boxes = {"blue": "1,1,0.05,18,39,0.75", "gold": "27,0,0.05,60,20,0.75",
             "green": "21,2,0.05,59,39,0.75"}  # fmt: skip
    for colour, box in boxes.items():
        gap = bench("clearance", files[colour], frame, "--min-gap", 0.25, "--box", f"face={box}")
        place = gap.numbers["pairs"][0]["places"]["face"]
        assert place["verdict"] == "CLEAR", gap.text
        # Never nearer than the play; a face fitted again is up to 0.006 mm further off.
        assert 0.25 - 1e-6 <= place["gap_mm"] <= 0.25 + 0.007, colour
    # The pane's face and the frame's on the bed, each from its own file.
    frame_info = bench("info", frame)
    assert frame_info.numbers["bounds_mm"][0][2] == pytest.approx(0.0, abs=1e-6)
    for colour in COLOURS:
        pane = bench("info", files[colour])
        assert pane.numbers["bounds_mm"][0][2] == pytest.approx(0.0, abs=1e-6), colour
        assert pane.numbers["bounds_mm"][1][2] == pytest.approx(1.8, abs=1e-6), colour
        laid = bench("info", f"fl/pane_{colour}-laid.step")
        # Turned over: on its body, its raised face up, everything facing down on the bed.
        heights = laid.numbers["flat_heights"]
        assert [h["z"] for h in heights if h["down"] > 0] == [0.0], (colour, heights)
        assert laid.numbers["bounds_mm"][1][2] == pytest.approx(1.8, abs=1e-6)
    assert standing.pane_bodies(bench.work, list(files.values())) == 3
    assert (bench.work / "fl" / "section.png").is_file()


def test_the_existing_checks_read_a_flush_build(bench: Any, flush: Answer) -> None:
    """`pause` pauses at the body's top, as the reply says; `capture` holds each pane by its
    body; `thin` passes every file; and `light` counts each raised face as the glass that
    shows, by both its methods, not the ring of play round it."""
    frame, panes = flush.numbers["glass"]["frame"], flush.numbers["glass"]["panes"]
    pause = bench("pause", frame, "--pocket-z", 1.3, "--pane-thickness", 1)
    said = "pause after layer 10, at z=2, before layer 11, at z=2.2:"
    assert pause.verdict == "OK" and pause.record["why"].startswith(said), pause.text
    held = bench(
        "capture", frame, "--pocket-z", 1.3, "--clearance", 0.25, "--pane-thickness", 1,
        "--panes", 3,
    )  # fmt: skip
    assert held.verdict == "OK", held.text
    for name in [frame, *panes, *(f"fl/pane_{c}-laid.step" for c in COLOURS)]:
        assert bench("thin", name).verdict == "OK", name
    light = bench("light", frame, *panes)
    assert light.verdict == "OK", light.text
    assert light.numbers["stepped"] == panes
    for row in light.numbers["panes"]:
        # The face is the opening less the play all round: under what the opening shows.
        assert row["open_pct"] == pytest.approx(row["open_pct_rays"], abs=row["band_pct"])
    blue = next(r for r in light.numbers["panes"] if r["file"].endswith("blue.step"))
    flat = bench("pieces", "build", "flush.json", *BUILD, "--build-out", "flat-light")
    assert flat.verdict == "OK", flat.text
    plain = bench("light", "flat-light/frame.step", "flat-light/pane_blue.step")
    assert "stepped" not in plain.numbers
    opening = plain.numbers["panes"][0]
    assert blue["footprint_mm2"] == pytest.approx(opening["footprint_mm2"], abs=0.01)
    # The ring round the face, 0.25 mm wide round an opening about 100 mm round, isn't counted.
    assert 15.0 < opening["open_mm2"] - blue["open_mm2"] < 35.0


def test_a_face_that_would_touch_the_lead_at_a_corner_is_refused_naming_the_place(
    bench: Any,
) -> None:
    """A raised face with less play than its body: pushed into a square corner of its pocket,
    the body moves the play over the sine of 45 degrees, and the face, with less room, meets
    the lead first, at the corner. Refused, nothing built, naming the corner and the play that
    keeps it off; and `pieces check --flush` flags the same, in the same words."""
    answer = bench(
        "pieces", "build", "flush.json", *BUILD, "--flush", "--face-play", 0.1,
        "--build-out", "touch",
    )  # fmt: skip
    assert answer.verdict == "FAIL" and answer.code == 1, answer.text
    said = answer.numbers["unbuildable"]
    square = next(line for line in said if line.startswith("piece 1,"))
    # The panel's bottom-left corner, the opening's corner 1.2 mm of lip in from the piece's.
    assert (
        "the pane pushed to one side in its pocket would put its raised face on the lead, "
        "here at (1.3, 1.5). --face-play 0.25 keeps it off."
    ) in square, square
    assert "Its body moves 0.354 mm that way in its pocket" in square
    assert "the face has 0.141 mm to its opening's wall there" in square
    assert not (bench.work / "touch" / "frame.step").exists()
    checked = bench("pieces", "check", "flush.json", "--lip", 1.2, "--flush", "--face-play", 0.1)
    flagged = {f["piece"]: f for f in checked.numbers["flagged"] if f["flag"] == "no-face"}
    assert sorted(flagged) == [1, 2, 3], checked.text
    assert square in checked.text, "in the build's own words"
    clean = bench("pieces", "check", "flush.json", "--lip", 1.2, "--flush")
    assert clean.verdict == "OK", clean.text
    again = bench(
        "pieces", "build", "flush.json", *BUILD, "--flush", "--face-play", 0.25,
        "--build-out", "kept-off",
    )  # fmt: skip
    assert again.verdict == "OK", again.text


@pytest.mark.parametrize(
    ("given", "says"),
    [
        (["--lip-thick", 0.2], "the lip under the panes, 0.2 mm, is under two layers, 0.4 mm"),
        (["--body", 0.2], "--body 0.2 is under two layers, 0.4 mm"),
        (["--lip-thick", 0.5], "--lip-thick 0.5 isn't on a layer line of 0.2 mm"),
        (["--body", 0.4, "--lip-thick", 2.2], "the lip over it would be 0.2 mm"),
    ],
)
def test_a_body_or_lip_under_two_layers_is_refused(bench: Any, given: list[Any], says: str) -> None:
    answer = bench(
        "pieces", "build", "flush.json", *BUILD, "--flush", *given, "--build-out", "under",
    )  # fmt: skip
    assert answer.verdict == "NO ANSWER" and says in answer.record["why"], answer.text
    assert not (bench.work / "under" / "frame.step").exists()


def test_a_thinner_lip_and_body_move_the_pause_to_the_bodys_top(bench: Any) -> None:
    answer = bench(
        "pieces", "build", "flush.json", "--thickness", 2, "--pane", 1, "--lip", 1.2,
        "--play", 0.25, "--flush", "--lip-thick", 0.4, "--body", 0.6, "--build-out", "thin",
    )  # fmt: skip
    assert answer.verdict == "OK", answer.text
    numbers = answer.numbers
    assert (numbers["face_mm"], numbers["body_mm"], numbers["pause_z"]) == (0.4, 0.6, 1.2)
    assert numbers["pause_after_layer"] == 6 and numbers["upper_lip_mm"] == 0.8
    assert "layer 6" in answer.record["why"]
    pause = bench("pause", "thin/frame.step", "--pocket-z", 0.7, "--pane-thickness", 0.6)
    assert pause.record["why"].startswith("pause after layer 6, at z=1.2"), pause.text
    refused = bench("pieces", "build", "flush.json", *BUILD, "--body", 0.6, "--build-out", "x")
    assert refused.verdict == "NO ANSWER" and "there's no --flush" in refused.record["why"]


def test_a_panel_built_without_flush_is_unchanged_byte_for_byte(bench: Any) -> None:
    """The test panel, and the traced test scene as `test_pieces.py` builds it, without
    `--flush`: each file's STEP data is byte for byte what main wrote before `--flush` was
    added, in an image of the same architecture."""
    traced = bench("pieces", "trace", "hills.png", "--width", 100, "--to", "hills.json")
    assert traced.verdict == "OK", traced.text
    hills = bench(
        "pieces", "build", "hills.json", "--thickness", 3, "--pane", 1, "--lip", "1.2,0.6",
        "--play", 0.25, "--build-out", "hills",
    )  # fmt: skip
    flat = bench("pieces", "build", "flush.json", *BUILD, "--build-out", "flat")
    assert hills.verdict == flat.verdict == "OK", hills.text + flat.text
    assert "flush" not in flat.numbers and not list((bench.work / "flat").glob("*-laid.step"))
    found = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Architecture}}", host.IMAGE],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    arch = found.stdout.strip()
    assert arch in BEFORE, f"no reference made from main for an {arch} image: make one"
    differ = []
    for name, wanted in BEFORE[arch].items():
        made = (bench.work / name).read_bytes()
        if hashlib.sha256(made[made.find(b"\nDATA;") :]).hexdigest() != wanted:
            differ.append(name)
    assert not differ, f"other STEP data than main's, on {arch}: {differ}"
    assert sorted(p.name for p in (bench.work / "flat").iterdir()) == [
        "frame.step", "pane_blue.step", "pane_gold.step", "pane_green.step",
    ]  # fmt: skip
