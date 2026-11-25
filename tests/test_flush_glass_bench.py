"""A flush frame through the rounds harness's own readings, on the real bench, in its image
(mqucifer/print-shop#293). Needs `scripts/bench.py build`.

The test panel of `bench_cases/planted_pieces.py`, `flush.json`, built `--flush` and declared as
its reply's `glass` gives it: every glass reading and every print reading passes. Its laid files
turned raised face down fail support, naming the pane; declared flat, with no laid files, it
fails `light`; and the same panel built flat reads as main's harness reads it, compared reading
for reading with main's code, built here from this commit's merge base with origin's main.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import downward, judge, standing
from print_shop.rounds.box import Box
from tests.test_pieces_flush import _git, reference_commit

pytestmark = pytest.mark.bench
CASES = Path(__file__).parent / "bench_cases"
BUILD = ["--thickness", "3", "--pane", "1", "--lip", "1.2", "--play", "0.25"]
COLOURS = ("blue", "gold", "green")
SAID = 'the request says "It prints flat, with no supports."'
#: The readings a flat frame gets, as a script: run with main's harness and with this one's, over
#: the same files, it prints what each read.
READINGS = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from print_shop.bench import host
from print_shop.rounds import downward, judge, standing
from print_shop.rounds.box import Box
work, kept = Path(sys.argv[2]), Path(sys.argv[3])
entry, part = json.loads(sys.argv[4]), json.loads(sys.argv[5])
glass = standing.read([entry], part, work)
box = Box(work)
said = {name: sys.argv[6] for name in part}
read = [
    *standing.check(box, glass, work, kept / "standing"),
    *downward.read(box, part, {}, work, kept / "downward", said, glass),
    *judge.printable(box, part, work, kept / "thin"),
]
print("READINGS " + json.dumps(
    {"code": standing.__file__, "bench": host.HERE.as_posix(),
     "glass": [one.plain() for one in glass],
     "read": [{**one.plain(), "numbers": one.numbers} for one in read]}, sort_keys=True))
"""


def _build(work: Path, out: str, *more: str) -> dict[str, Any]:
    done = host.run(["pieces", "build", "flush.json", *BUILD, *more, "--build-out", out], work)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr
    record = sorted((work / "bench-out").glob("pieces-*.json"))[-1]
    numbers: dict[str, Any] = json.loads(record.read_text())["numbers"]
    return numbers


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    here = tmp_path_factory.mktemp("flush-glass")
    shutil.copy(CASES / "planted_pieces.py", here / "planted_pieces.py")
    made = host.run(["planted_pieces.py"], here, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    return here


@pytest.fixture(scope="module")
def flush(work: Path) -> dict[str, Any]:
    """The flush build's `glass`, as its reply gives it for `done`."""
    glass: dict[str, Any] = _build(work, "fl", "--flush")["glass"]
    return glass


def _part(entry: dict[str, Any]) -> list[str]:
    return [entry["frame"], *entry["panes"], *(entry.get("laid") or {}).values()]


def _declared(work: Path, entry: dict[str, Any]) -> list[standing.Glass]:
    found = standing.read([entry], _part(entry), work)
    assert isinstance(found, list), found
    return found


def test_a_flush_frame_declared_with_its_laid_files_passes_every_reading(
    work: Path, flush: dict[str, Any], tmp_path: Path
) -> None:
    assert flush["laid"] == {f"fl/pane_{c}.step": f"fl/pane_{c}-laid.step" for c in COLOURS}
    assert (flush["pane_thickness"], flush["face_thickness"], flush["face_clearance"]) == (
        1.0, 0.8, 0.25,
    )  # fmt: skip
    glass = _declared(work, flush)
    box = Box(work)
    steps = standing.steps(box, glass, work)
    assert isinstance(steps, dict) and standing.borne_out(glass, steps) == "", steps
    read = standing.check(box, glass, work, tmp_path / "standing")
    assert [(r.check, r.passed) for r in read] == [
        (standing.LIGHT, True), (standing.HOLD, True), (standing.INSERT, True),
    ], [r.why for r in read]  # fmt: skip
    light, hold, pause = read
    assert light.numbers["stepped"] == flush["panes"], "the raised faces are the glass that shows"
    assert "--raised-faces" in hold.command and hold.numbers["faces"] == 3
    assert hold.numbers["face_play_least_mm"] >= 0.25 - 0.004
    assert pause.why.startswith("OK: pause after layer 10, at z=2"), pause.why
    part = _part(flush)
    said = {name: SAID for name in part}
    faces = downward.read(box, part, {}, work, tmp_path / "downward", said, glass)
    assert all(f.passed for f in faces), [f.why for f in faces if not f.passed]
    by_file = {f.file: f for f in faces}
    assert by_file["fl/frame.step"].numbers["panes_in_place"] == 3
    for pane, laid in flush["laid"].items():
        assert by_file[pane].numbers["prints_from"] == laid
        assert by_file[laid].verdict == "NONE", by_file[laid].why
    thin = judge.printable(box, part, work, tmp_path / "thin")
    assert all(t.passed for t in thin), [t.why for t in thin if not t.passed]


def test_its_laid_files_turned_raised_face_down_fail_support_naming_each_pane(
    work: Path, flush: dict[str, Any], tmp_path: Path
) -> None:
    """The control: each laid file laid on its other side, its raised face down, needs support
    under its body, and the pane's own reading fails with it, naming the pane."""
    glass = _declared(work, flush)
    part = _part(flush)
    said = {name: SAID for name in part}
    turned = {laid: "+z" for laid in flush["laid"].values()}
    faces = downward.read(Box(work), part, turned, work, tmp_path / "downward", said, glass)
    by_file = {f.file: f for f in faces}
    for pane, laid in flush["laid"].items():
        assert not by_file[laid].passed and by_file[laid].verdict == "NEEDS SUPPORT", laid
        assert not by_file[pane].passed, pane
        assert f"from its laid file `{laid}`" in by_file[pane].why
        assert "would need support" in by_file[pane].why
    assert by_file["fl/frame.step"].passed


def test_a_flush_pane_declared_flat_is_refused_not_passed(
    work: Path, flush: dict[str, Any], tmp_path: Path
) -> None:
    """With no laid files: `light` reads its panes as stepped and the standing reading fails,
    naming each, and each pane read alone, as a flat frame's pane is, needs support. A flush
    declaration with a pane's laid file missing, or a face it doesn't bear out, is refused."""
    flat = {k: v for k, v in flush.items() if k not in standing.FLUSH_KEYS}
    glass = _declared(work, flat)
    box = Box(work)
    light, hold, pause = standing.check(box, glass, work, tmp_path / "standing")
    assert not light.passed and not light.unsure and light.verdict == "OK", light.why
    for pane in flush["panes"]:
        assert f"`{pane}`" in light.why
    assert "are stepped, each a body over a raised face, as a flush pane is" in light.why
    assert "with no file for them to print from: declare them flush" in light.why
    said = {name: SAID for name in _part(flat)}
    faces = downward.read(box, _part(flat), {}, work, tmp_path / "downward", said, glass)
    panes = [f for f in faces if f.file in flush["panes"]]
    assert panes and all(not f.passed and f.verdict == "NEEDS SUPPORT" for f in panes)
    missing = {**flush, "laid": dict(list(flush["laid"].items())[:2])}
    refused = standing.read([missing], _part(flush), work)
    assert isinstance(refused, str), refused
    assert "`laid` names no file to print `fl/pane_green.step` from" in refused
    wrong = _declared(work, {**flush, "face_thickness": 0.6})
    steps = standing.steps(box, wrong, work)
    assert isinstance(steps, dict)
    said_wrong = standing.borne_out(wrong, steps)
    assert "`fl/pane_blue.step`, where it sits in the frame, is a body 1.000 mm thick and a " in (
        said_wrong
    )  # fmt: skip
    assert "raised face 0.800 mm, where `glass` says `pane_thickness` 1" in said_wrong


def _readings(src: Path, work: Path, kept: Path, entry: dict[str, Any]) -> dict[str, Any]:
    kept.mkdir(parents=True)
    script = kept.parent / "readings.py"
    script.write_text(READINGS)
    done = subprocess.run(
        [sys.executable, str(script), str(src), str(work), str(kept), json.dumps(entry),
         json.dumps(_part(entry)), SAID],
        capture_output=True, text=True, timeout=1500, check=False,
    )  # fmt: skip
    line = next((ln for ln in done.stdout.splitlines() if ln.startswith("READINGS ")), None)
    assert line is not None, done.stdout[-2000:] + done.stderr[-3000:]
    found: dict[str, Any] = json.loads(line[len("READINGS ") :])
    return found


def test_a_flat_panel_reads_as_main_reads_it(work: Path, tmp_path: Path) -> None:
    """The panel built flat and declared as today: every glass, print and `thin` reading is the
    one main's harness gives, field for field, run here with main's code over the same files."""
    entry = _build(work, "flat")["glass"]
    assert set(entry) == {"frame", "panes", "pocket_z", "clearance", "pane_thickness"}
    commit = reference_commit()
    package = _git("archive", "--format=tar", commit, "src")
    assert package.returncode == 0, package.stderr.decode()
    main_src = tmp_path / "main"
    with tarfile.open(fileobj=io.BytesIO(package.stdout)) as tar:
        tar.extractall(main_src, filter="data")
    mine = Path(__file__).resolve().parents[1] / "src"
    theirs = _readings(main_src / "src", work, tmp_path / "main-kept" / "k", entry)
    ours = _readings(mine, work, tmp_path / "our-kept" / "k", entry)
    # Each ran its own harness, and its own bench package in the container.
    for found, src in ((theirs, main_src / "src"), (ours, mine)):
        assert found.pop("code").startswith(src.resolve().as_posix()), found
        assert found.pop("bench").startswith(src.resolve().as_posix())
    assert len(ours["read"]) == 3 + 4 + 4, "three glass readings, and four files each"
    assert all(r["passed"] for r in ours["read"]), [r["why"] for r in ours["read"]]
    assert ours == theirs, f"main {commit} read otherwise"
