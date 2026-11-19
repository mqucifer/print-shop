"""A build writes the same files, a pick holds by geometry where only bytes differ, and the
overhang reading gets the bench's whole answer (mqucifer/print-shop#180, mqucifer/print-shop#181).

On eval-organic-3, a traced suncatcher, building one map three times gave two versions of the
STEP data in three of its 13 files, so `done` refused a pick that should have held. And the
overhang reading said "can't tell" for every file at every stop: the bench's answer was one line
of 1,459,288 bytes, and the harness keeps the last 256,000 of a container's output.

The map here is made here (`bench_cases/lattice_map.py`): sharp-tipped pieces in three colours,
whose tips `pieces build` rounds, so many a straight side meets a small arc, as on the scene.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import downward, judge, standing
from print_shop.rounds.box import KEEP, Box
from print_shop.rounds.needs import Need
from print_shop.rounds.paths import content_hash
from print_shop.rounds.standing import Glass

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"
BUILD = ["--thickness", "3", "--pane", "1", "--lip", "1.2,0.6", "--play", "0.25"]
FILES = ["frame.step", "pane_amber.step", "pane_teal.step", "pane_rose.step"]


def build(work: Path, out: str, options: list[str], seed: str = "0") -> dict[str, Any]:
    """`pieces build` of the lattice in a fresh container, with its own `PYTHONHASHSEED`."""
    argv = host.container(
        work,
        host.bench_command(["pieces", "build", "lattice.json", *options, "--build-out", out]),
        f"print-stable-{uuid.uuid4().hex[:12]}",
        env={"PYTHONHASHSEED": seed},
    )
    done = subprocess.run(argv, capture_output=True, text=True, timeout=900, check=False)
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    return {name: (work / out / name).read_bytes() for name in FILES}


@pytest.fixture(scope="module")
def lattice(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("lattice")
    shutil.copy(CASES / "lattice_map.py", work / "lattice_map.py")
    made = host.run(["lattice_map.py", "lattice.json"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    return work


@pytest.fixture(scope="module")
def built(lattice: Path) -> dict[str, bytes]:
    return build(lattice, "first", BUILD, "1")


def test_one_map_built_three_times_in_fresh_processes_writes_the_same_step_data(
    lattice: Path, built: dict[str, bytes]
) -> None:
    """Each build in its own container, with its own hash seed: every file has the same
    `content_hash` each time. Built eight times before mqucifer/print-shop#180, this map's frame
    and one pane file each came out two ways, five builds to three: three builds all agreed on
    both about one time in eleven."""
    again = [build(lattice, f"again-{seed}", BUILD, seed) for seed in ("17", "4242")]
    for name in FILES:
        hashes = {content_hash(name, one[name]) for one in [built, *again]}
        assert len(hashes) == 1, f"{name} came out {len(hashes)} ways"
    # A pane file holds many panes: the colours are shared among the 55.
    assert all(built[name].count(b"MANIFOLD_SOLID_BREP") > 10 for name in FILES[1:])


def _pick(work: Path, picked: Path) -> dict[str, Any]:
    """A pick of variant A at ask 1 on the lattice's frame, its copy kept as `send_ask` keeps it."""
    kept = work / "asks" / "1" / "variants" / "A"
    kept.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(picked, kept / "frame.step")
    sha = content_hash("frame.step", picked.read_bytes())
    return {"pick": "A", "reason": "the colours sit right", "ask": 1, "by": "sponsor",
            "files": {"lattice/frame.step": sha}}  # fmt: skip


def _judged(work: Path, name: str, data: bytes) -> tuple[Path, dict[str, str]]:
    judged = work / name / "judged"
    (judged / "lattice").mkdir(parents=True, exist_ok=True)
    (judged / "lattice" / "frame.step").write_bytes(data)
    return judged, {"lattice/frame.step": content_hash("frame.step", data)}


def test_a_pick_holds_by_geometry_where_only_the_bytes_differ_and_not_where_the_geometry_does(
    lattice: Path, built: dict[str, bytes]
) -> None:
    """The frame written again without its faces' 2D curves is other STEP data for the same
    solid: the pick holds, and says it held by geometry. The frame built with 0.05 mm more play
    is other geometry: the pick doesn't hold, and says what differs."""
    work = lattice / "held"
    work.mkdir()
    picked = work / "picked.step"
    picked.write_bytes(built["frame.step"])
    pick = _pick(work, picked)
    # Read and written again by OpenCascade, without pcurves: the same solid, other bytes.
    (work / "rewrite.py").write_text(
        "import cadquery as cq\n"
        "shape = cq.importers.importStep('picked.step').val()\n"
        "shape.exportStep('rewritten.step', write_pcurves=False)\n",
        encoding="utf-8",
    )
    rewrote = host.run(["rewrite.py"], work, python=True)
    assert rewrote.returncode == 0, rewrote.stdout + rewrote.stderr
    rewritten = (work / "rewritten.step").read_bytes()
    assert content_hash("frame.step", rewritten) != pick["files"]["lattice/frame.step"]
    box = Box(work)
    need = Need("look", "the colours sit right", "request", "eye", look_for="the colours")
    part = ["lattice/frame.step"]

    judged, left = _judged(work, "same", rewritten)
    held = judge.by_geometry(box, pick, left, judged, work / "asks")
    assert held["lattice/frame.step"].same, held
    reading = judge.eye_reading(need, {"look": pick}, left, part, held)
    assert reading.passed, reading.why
    assert "It holds by geometry: lattice/frame.step's bytes aren't what was picked" in reading.why
    assert not list(judged.parent.glob("held-*")), "its scratch folder is taken away"

    wider = build(lattice, "wider", [*BUILD[:-1], "0.3"], "5")
    judged, left = _judged(work, "other", wider["frame.step"])
    differs = judge.by_geometry(box, pick, left, judged, work / "asks")
    assert not differs["lattice/frame.step"].same
    reading = judge.eye_reading(need, {"look": pick}, left, part, differs)
    assert not reading.passed
    assert "lattice/frame.step's geometry isn't what was picked: its solid's" in reading.why, (
        reading.why
    )

    # And `done` itself reads it so, from the ask's kept copy.
    judged, left = _judged(work, "done", rewritten)
    assert judge.not_what_it_saw(pick, left, part) is not None, "by bytes alone it doesn't hold"


def test_the_overhang_reading_gets_the_whole_answer_for_a_frame_and_all_its_panes(
    lattice: Path, built: dict[str, bytes]
) -> None:
    """The frame and its three pane files, 55 panes, laid and meshed by the bench: the answer
    is longer than what the harness keeps of a container's output, and each file is read."""
    files = [f"first/{name}" for name in FILES]
    bodies = standing.pane_bodies(lattice, files[1:])
    assert bodies == 55
    glass = Glass(files[0], tuple(files[1:]), 1.3, 0.25, 1.0, bodies=bodies)
    kept = lattice / "downward-kept"
    found = downward.read(Box(lattice), files, {}, lattice, kept, {}, [glass])
    assert [one.file for one in found] == files
    for one in found:
        assert not one.unsure, one.why
    (answer,) = kept.glob("downward-*/laid/answer.json")
    assert answer.stat().st_size > KEEP, answer.stat().st_size
    meshed = json.loads(answer.read_text())["files"]
    assert len(meshed) == len(files) + len(files) - 1, "each file, and each pane file in place"
    assert found[0].numbers["panes_in_place"] == 55
