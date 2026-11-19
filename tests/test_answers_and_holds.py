"""A helper's answer read whole and a "can't tell" that says why (mqucifer/print-shop#181), and
a pick held by geometry where only its bytes differ (mqucifer/print-shop#180), with no image:
the bench's answers are written here as it writes them. `test_build_stable.py` runs both in it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import trimesh

from print_shop.bench import answers
from print_shop.bench.refine import MARK
from print_shop.bench.solids import MARK as SOLIDS_MARK
from print_shop.rounds import downward, judge
from print_shop.rounds.box import KEEP, Ran, Unanswered, answered
from print_shop.rounds.needs import Need
from print_shop.rounds.paths import content_hash

# A helper's answer, whatever its length


def test_an_answer_on_its_line_or_in_the_file_it_names_is_read_whole(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    long = {"files": {"a.step": {"outline": [[0.123456, 0.654321]] * 40_000}}}
    answers.say(MARK, long, {"answer": str(tmp_path / "answer.json")})
    line = capsys.readouterr().out
    assert len(line) < 200 and line.startswith(MARK), "the line says only where"
    assert (tmp_path / "answer.json").stat().st_size > KEEP
    # As the container sees it: the file named from the folder the helper ran in.
    said = json.loads(line[len(MARK) :])
    said[answers.FILE] = "answer.json"
    ran = Ran(0, "warming up\n" + MARK + json.dumps(said) + "\n", 1.0)
    assert answered(ran, MARK, tmp_path) == long
    answers.say(MARK, {"files": {}}, {})
    assert answered(Ran(0, capsys.readouterr().out, 1.0), MARK, tmp_path) == {"files": {}}


@pytest.mark.parametrize(
    ("ran", "says"),
    [
        (
            Ran(0, '0.1, 0.2], [0.3, 0.4]]}}}\n', 3.0, dropped=1_203_288),
            "the bench's answer went missing: its output was 1,203,314 bytes, of which the "
            "harness keeps the last 256,000 (exit 0)",
        ),
        (Ran(0, "nothing to say\n", 3.0), "the bench's answer went missing: its output has no"),
        (Ran(2, "Traceback ...\nValueError: no shape\n", 3.0), "the bench exited 2: ValueError"),
        (
            Ran(0, MARK + json.dumps({answers.FILE: "gone.json", "bytes": 9}) + "\n", 3.0),
            "the bench's answer went missing: it wrote gone.json, and that couldn't be read",
        ),
    ],
)
def test_no_answer_says_why_and_a_reply_too_long_is_never_the_bench_failing(
    tmp_path: Path, ran: Ran, says: str
) -> None:
    with pytest.raises(Unanswered) as raised:
        answered(ran, MARK, tmp_path)
    assert str(raised.value).startswith(says), str(raised.value)


# "can't tell" names why


class Benched:
    """A box whose `refine` helper gives a set `Ran`, and, with `write`, the answer file the
    request names: as the bench writes it, each file meshed as a 10 mm cube."""

    def __init__(self, ran: Ran, write: dict[str, Any] | None = None) -> None:
        self.ran, self.write = ran, write

    def helper(self, module: str, args: list[str], timeout: int = 120, where: Any = None) -> Ran:
        request = json.loads(args[0])
        if self.write is not None:
            files = {}
            for name in request["files"]:
                stl = f"{request['out']}/{Path(name).stem}.stl"
                (where / request["out"]).mkdir(parents=True, exist_ok=True)
                trimesh.creation.box(extents=(10, 10, 10)).export(str(where / stl))
                files[name] = {"stl": stl, **self.write.get(name.split("__", 1)[-1], {})}
            (where / request["answer"]).write_text(json.dumps({"files": files}))
        return self.ran


def _work(tmp_path: Path) -> Path:
    for name in ("frame.step", "pane.step"):
        (tmp_path / name).write_text("ISO-10303-21;")
    return tmp_path


def test_each_file_is_read_from_an_answer_written_to_a_file(tmp_path: Path) -> None:
    pointer = MARK + json.dumps({answers.FILE: downward.ANSWER, "bytes": 1_459_288})
    box = Benched(Ran(0, pointer + "\n", 9.0), {})
    files = ["frame.step", "pane.step"]
    found = downward.read(box, files, {}, _work(tmp_path), tmp_path / "k")  # type: ignore[arg-type]
    assert [one.verdict for one in found] == ["NONE", "NONE"], [one.why for one in found]


@pytest.mark.parametrize(
    ("ran", "write", "says"),
    [
        (
            Ran(0, "0.5, 0.25]]}}}\n", 9.0, dropped=1_203_288),
            None,
            "the bench's answer went missing: its output was",
        ),
        (Ran(137, "verdict: NO ANSWER: it was killed\n", 9.0), None, "the bench exited 137"),
        (
            Ran(0, MARK + json.dumps({answers.FILE: downward.ANSWER, "bytes": 1}) + "\n", 9.0),
            {"pane.step": {"unread": "pane.step read as nothing"}},
            "the bench couldn't lay it on the bed: pane.step read as nothing",
        ),
    ],
)
def test_cant_tell_names_why(
    tmp_path: Path, ran: Ran, write: dict[str, Any] | None, says: str
) -> None:
    """A harness fault, the answer gone missing, apart from the bench's own: it exited with an
    error, or said it couldn't lay a file. Each is neither a pass nor a fail."""
    box: Any = Benched(ran, write)
    found = downward.read(box, ["frame.step", "pane.step"], {}, _work(tmp_path), tmp_path / "k")
    pane = found[-1]
    assert pane.unsure and not pane.failed and not pane.passed
    assert pane.why.startswith(f"it couldn't be read: {says}"), pane.why
    assert "can't tell" in downward.said([pane])[0]


# A pick held by geometry


def _solid(volume: float = 1000.0, at: float = 5.0) -> dict[str, Any]:
    faces = [["PLANE", 100.0, [at, at, z]] for z in (0.0, 10.0)]
    faces += [["PLANE", 100.0, [at, y, 5.0]] for y in (at - 5.0, at + 5.0)]
    faces += [["CYLINDER", 31.4, [at, at, 5.0]]]
    return {
        "volume": volume,
        "area": 600.0,
        "centre": [at, at, 5.0],
        "box": [[at - 5, at - 5, 0.0], [at + 5, at + 5, 10.0]],
        "faces": faces,
    }


def test_the_same_solids_written_another_way_are_the_same_geometry() -> None:
    was = {"solids": [_solid(at=5.0), _solid(at=25.0)]}
    now = copy.deepcopy(was)
    now["solids"].reverse()
    now["solids"][0]["faces"].reverse()
    # A last digit's noise, as two writings of one solid read.
    now["solids"][1]["volume"] += 1e-9
    now["solids"][1]["centre"][0] += 1e-12
    assert judge.same_geometry(was, now) is None


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (lambda s: s["solids"].pop(), "it was picked as 2 solid(s), and is 1"),
        (lambda s: s["solids"][0].update(volume=1000.01), "solid 1's volume is 1000.01 mm³"),
        (lambda s: s["solids"][0]["box"][1].__setitem__(2, 10.001), "solid 1's box's most z is"),
        # A hole moved 0.01 mm inside the body: its volume, area and box are as they were.
        (
            lambda s: s["solids"][1]["faces"][4][2].__setitem__(0, 25.01),
            "solid 2's faces differ: a cylinder face of 31.4000 mm² at (25.0000",
        ),
        (lambda s: s["solids"][0]["faces"].pop(), "solid 1 has 4 faces, where it had 5"),
        (lambda s: s.clear(), "the clean run's couldn't be read for its solids"),
    ],
)
def test_any_change_to_the_geometry_breaks_it(change: Any, says: str) -> None:
    was = {"solids": [_solid(at=5.0), _solid(at=25.0)]}
    now = copy.deepcopy(was)
    change(now)
    differs = judge.same_geometry(was, now)
    assert differs is not None and differs.startswith(says), differs


class Solids:
    """A box whose `solids` helper answers from set readings, by which copy it was given: the
    picked one, or the clean run's."""

    def __init__(self, was: dict[str, Any], now: dict[str, Any]) -> None:
        self.was, self.now = was, now
        self.asked: list[list[str]] = []

    def helper(self, module: str, args: list[str], timeout: int = 120, where: Any = None) -> Ran:
        assert module == "solids"
        request = json.loads(args[0])
        self.asked.append(sorted(p.name for p in where.iterdir()))
        files = {f: self.was if f.startswith("picked-") else self.now for f in request["files"]}
        (where / request["answer"]).write_text(json.dumps({"files": files}))
        pointer = {answers.FILE: request["answer"], "bytes": 1}
        return Ran(0, SOLIDS_MARK + json.dumps(pointer) + "\n", 2.0)


def _picked(tmp_path: Path) -> tuple[dict[str, Any], Path, dict[str, str]]:
    kept = tmp_path / "asks" / "3" / "variants" / "A"
    kept.mkdir(parents=True)
    (kept / "frame.step").write_text("HEADER;\nDATA;\n#1 = POINT(0.);\nENDSEC;")
    (kept / "pane_red.step").write_text("HEADER;\nDATA;\n#1 = POINT(1.);\nENDSEC;")
    seen = {f"out/{p.name}": content_hash(p.name, p.read_bytes()) for p in kept.iterdir()}
    pick = {"pick": "A", "reason": "warm", "ask": 3, "by": "sponsor", "files": seen}
    judged = tmp_path / "round" / "judged"
    (judged / "out").mkdir(parents=True)
    (judged / "out" / "frame.step").write_text("HEADER;\nDATA;\n#1 = POINT(-1.E-16);\nENDSEC;")
    (judged / "out" / "pane_red.step").write_text("HEADER;\nDATA;\n#1 = POINT(1.);\nENDSEC;")
    left = {
        f"out/{p.name}": content_hash(p.name, p.read_bytes()) for p in (judged / "out").iterdir()
    }
    return pick, judged, left


def test_a_pick_holds_by_geometry_where_only_a_files_bytes_differ_and_says_so(
    tmp_path: Path,
) -> None:
    pick, judged, left = _picked(tmp_path)
    need = Need("look", "warm", "request", "eye", look_for="its colours")
    by_bytes = judge.eye_reading(need, {"look": pick}, left, ["out/frame.step"])
    assert not by_bytes.passed and "out/frame.step changed since" in by_bytes.why
    box = Solids({"solids": [_solid()]}, {"solids": [_solid()]})
    held = judge.by_geometry(box, pick, left, judged, tmp_path / "asks")  # type: ignore[arg-type]
    assert list(held) == ["out/frame.step"], "the pane file's bytes are as picked"
    assert box.asked == [["now-0.step", "picked-0.step"]]
    reading = judge.eye_reading(need, {"look": pick}, left, ["out/frame.step"], held)
    assert reading.passed, reading.why
    assert reading.why.startswith(
        "the Sponsor picked A: warm. It holds by geometry: out/frame.step's bytes aren't what "
        "was picked, and its geometry is: its 1 solid(s)"
    ), reading.why
    assert not list(judged.parent.glob("held-*"))


def test_a_pick_doesnt_hold_where_the_geometry_changed_and_says_what(tmp_path: Path) -> None:
    pick, judged, left = _picked(tmp_path)
    box = Solids({"solids": [_solid()]}, {"solids": [_solid(volume=990.0)]})
    held = judge.by_geometry(box, pick, left, judged, tmp_path / "asks")  # type: ignore[arg-type]
    stale = judge.not_what_it_saw(pick, left, None, held)
    assert stale == (
        "out/frame.step changed since, or a clean run didn't leave it (out/frame.step's "
        "geometry isn't what was picked: its solid's volume is 990 mm³, where it was 1000)"
    )


def test_a_pick_isnt_held_by_geometry_without_its_kept_copy_or_a_file_of_its_name(
    tmp_path: Path,
) -> None:
    pick, judged, left = _picked(tmp_path)
    box = Solids({"solids": [_solid()]}, {"solids": [_solid()]})
    # No asks folder, as before this: nothing is read, and it doesn't hold.
    assert judge.by_geometry(box, pick, left, judged, None) == {}  # type: ignore[arg-type]
    # The clean run left it under another name.
    moved = {k.replace("frame", "frame2"): v for k, v in left.items()}
    asks = tmp_path / "asks"
    assert judge.by_geometry(box, pick, moved, judged, asks) == {}  # type: ignore[arg-type]
    # The kept copy isn't what was picked.
    (tmp_path / "asks" / "3" / "variants" / "A" / "frame.step").write_text("changed")
    assert judge.by_geometry(box, pick, left, judged, asks) == {}  # type: ignore[arg-type]
    assert box.asked == []
