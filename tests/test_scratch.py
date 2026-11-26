"""A design run's `scratch/` holds what isn't its part (mqucifer/print-shop#315): a placed or
exploded compound to draw, a turned copy to cut, a gauge. Its files are drawn at a stop, and never
read as the part, described, or part of `done`'s clean run. No Docker: a scripted model drives each
run, and a box answers each bench command and runs nothing. Every file here is made up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import judge, paths, print_sides, transcript
from print_shop.rounds import run as run_module
from print_shop.rounds.needs import Need
from print_shop.rounds.run import Run
from tests import test_assembly_part as assembly
from tests.rounds_fakes import Scripted, tool
from tests.test_holding import Answers, settled

REQUEST = """\
A stand for a lamp.

## Requirements
- The stand holds the lamp.

## Stops
- no plan
"""
#: The run's own part, and a turned copy and an exploded compound it keeps to draw.
PART, TURNED, EXPLODED = "stand.step", "scratch/stand-upright.step", "scratch/exploded.step"
FITS = Need(
    "fits",
    "the stand is printable",
    "chosen",
    "bench",
    command=f"info {PART}",
    verdicts=["OK"],
)


def started(root: Path, *turns: Any, answers: tuple[Any, ...] = ()) -> Run:
    model = Scripted(*[[t] for t in turns], [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", REQUEST, [], model, sleep=lambda _: None)
    (run.work / "scratch").mkdir()
    for name in (PART, TURNED, EXPLODED, "make.py", "scratch/gauge.py"):
        (run.work / name).write_text(f"made: {name}")
    box: Any = Answers(run.work, *answers)
    run.box = box
    run.tools.box = box
    return run


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def reply(run: Run, name: str) -> dict[str, Any]:
    return [r for r in replies(run) if r["name"] == name][-1]


def asked(run: Run) -> list[list[str]]:
    return [a[2:] if a[:1] == ["--out"] else a for a in run.box.asked]  # type: ignore[attr-defined]


# What's in scratch/ is never read as the part


def test_a_compound_in_scratch_is_drawn_at_a_stop_and_not_read_by_thin(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool(
            "ask",
            question="Does the stand read right beside the lamp?",
            variants=[{"name": "A", "files": [PART, EXPLODED]}],
            cuts="x=10",
        ),
    )
    assert run.drive().name == "blocked"
    done = reply(run, "ask")
    assert done["outcome"] == "ok", done["reply"]
    (render,) = [a for a in asked(run) if "render" in a]
    assert f"A={PART},{EXPLODED}" in render and "--cut=x=10" in render
    # `thin` reads the part's file, and nothing in scratch/.
    thin = [a for a in asked(run) if a[:1] == ["thin"]]
    assert thin and all(not any(w.startswith("scratch/") for w in a) for a in thin), thin
    assert run_module.SCRATCH_DRAWN in done["reply"]
    # Nothing in scratch/ is described: the part's version is its own files.
    assert sorted(run.state.geometry) == [PART]
    assert run.state.draft is not None
    assert all(not s["file"].startswith("scratch/") for s in run.state.draft["standing"])


def test_a_failing_thin_on_a_scratch_copy_isnt_said_as_the_parts(tmp_path: Path) -> None:
    """The assembly run's upright copies failed `thin`, and were said as the part's."""
    run = started(
        tmp_path,
        tool("bench", command=f"thin {TURNED}"),
        tool("bench", command=f"thin {PART}"),
        answers=("FAIL", "FAIL"),
    )
    assert run.drive().name == "blocked"
    said = reply(run, "bench")["reply"]
    assert f"- `{PART}`, call 2: FAIL" in said
    assert TURNED not in said.split("Files of the part whose last `thin` failed:")[1]


def test_an_eye_need_isnt_asked_on_a_scratch_file(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool(
            "ask",
            question="Is the stand's outline right?",
            variants=[{"name": "A", "files": [PART, EXPLODED]}],
            need=["outline"],
        ),
    )
    eye = Need("outline", "the outline reads as a stand", "chosen", "eye", look_for="the outline")
    settled(run, eye)
    assert run.drive().name == "blocked"
    done = reply(run, "ask")
    assert done["outcome"] == "failed" and done["why"] == "eye_on_scratch", done["reply"]
    assert f"`{EXPLODED}`" in done["reply"]
    assert not any("render" in a for a in asked(run))


# `done` can't make a part from scratch/


def _done(root: Path, **given: Any) -> dict[str, Any]:
    args = {"script": "make.py", "part": [PART], "fix_shape": "first", "closing": "A stand."}
    run = started(root, tool("done", **{**args, **given}))
    settled(run, FITS)
    assert run.drive().name == "blocked"
    return reply(run, "done")


def test_a_part_under_scratch_is_refused_naming_it(tmp_path: Path) -> None:
    done = _done(tmp_path / "a", part=[PART, TURNED])
    assert done["outcome"] == "failed" and done["why"] == "scratch", done["reply"]
    assert f"`part` names `{TURNED}`, in `scratch/`" in done["reply"]
    # As a row of several files too, and by any spelling that leads into it.
    row = {"name": "both", "files": [PART, f"./{TURNED}"]}
    done = _done(tmp_path / "b", part=[row])
    assert done["why"] == "scratch", done["reply"]
    done = _done(tmp_path / "c", part=["Scratch/stand-upright.step"])
    assert done["why"] == "scratch", done["reply"]


def test_a_script_under_scratch_is_refused(tmp_path: Path) -> None:
    done = _done(tmp_path, script="scratch/gauge.py")
    assert done["outcome"] == "failed" and done["why"] == "scratch", done["reply"]
    assert "`script` names `scratch/gauge.py`" in done["reply"]


def test_an_assembly_under_scratch_is_refused(tmp_path: Path) -> None:
    named = {**assembly.DONE, "part": ["scratch/table.json"]}
    run = assembly._run(
        tmp_path, assembly.BROKEN_DOWN, tool("needs", needs=[assembly.MEET]), tool("done", **named)
    )
    (run.work / "scratch").mkdir()
    (run.work / "scratch" / "table.json").write_text(json.dumps(assembly.ASSEMBLY))
    done = assembly._refused(run)
    assert done["why"] == "scratch", done["reply"]
    assert "`part` names `scratch/table.json`, in `scratch/`" in done["reply"]
    assert run.ledger.rows("SELECT * FROM assemblies") == []


def test_an_assembly_placing_a_scratch_copy_is_refused_as_not_given(tmp_path: Path) -> None:
    copied = {
        "parts": [*assembly.ASSEMBLY["parts"][:1], {"name": "r", "file": TURNED, "layers": "z"}]
    }
    run = assembly._run(
        tmp_path,
        assembly.BROKEN_DOWN,
        tool("needs", needs=[assembly.MEET]),
        tool("done", **assembly.DONE),
        assembly=copied,
    )
    (run.work / "scratch").mkdir()
    (run.work / TURNED).write_bytes(assembly.RIGHT)
    done = assembly._refused(run)
    assert done["why"] == "assembly_not_given", done["reply"]
    assert f"- `{TURNED}` isn't given to you" in done["reply"]


# The clean run, the description, the size


def test_a_clean_run_doesnt_copy_scratch_and_its_size_still_counts(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "scratch").mkdir(parents=True)
    (work / "make.py").write_text("print('made')\n")
    (work / "scratch" / "gauge.py").write_text("print('gauge')\n")
    (work / "scratch" / "copy.step").write_bytes(b"x" * 1000)
    judge.clean_copy(work, tmp_path / "judged")
    assert [str(p) for p in paths.walk_plain(tmp_path / "judged")] == ["make.py"]
    assert list(paths.snapshot(work)) == ["make.py"]
    assert list(paths.texts(work)) == ["make.py"]
    assert paths.size_of(work) >= 1000
    # A copy of a picked part kept in scratch/ can't hold the pick for a part that changed.
    assert paths.geometry_hashes(work) == {}


def test_a_run_with_no_scratch_folder_reads_as_on_main(tmp_path: Path) -> None:
    """Only the top folder named `scratch` is scratch: one deeper is the run's own, as before."""
    work = tmp_path / "work"
    (work / "parts" / "scratch").mkdir(parents=True)
    (work / "parts" / "scratch" / "lid.step").write_text("lid")
    (work / "base.step").write_text("base")
    (work / "scratchpad.step").write_text("pad")
    walked = ["base.step", "parts/scratch/lid.step", "scratchpad.step"]
    assert sorted(str(p) for p in paths.walk_plain(work, skip_made=True)) == walked
    assert sorted(paths.snapshot(work)) == walked
    assert sorted(paths.geometry_hashes(work)) == walked
    assert not any(paths.in_scratch(n) for n in walked)
    assert paths.in_scratch("scratch/a.step") and paths.in_scratch("/work/scratch/a.step")
    assert paths.in_scratch("Scratch/a.step")


def test_a_stop_without_scratch_files_reads_every_drawn_file_as_before(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        tool("ask", question="Does it read right?", variants=[{"name": "A", "files": [PART]}]),
    )
    assert run.drive().name == "blocked"
    done = reply(run, "ask")
    assert done["outcome"] == "ok", done["reply"]
    assert run_module.SCRATCH_DRAWN not in done["reply"]
    assert ["thin", PART] in asked(run)


def test_a_compound_placed_in_scratch_isnt_the_runs_assembly(tmp_path: Path) -> None:
    """A placed or exploded compound drawn with `--placed scratch/…` places nothing the harness
    reads: the run's assembly files are its own, outside `scratch/`."""
    work = tmp_path / "work"
    (work / "scratch").mkdir(parents=True)
    for name in ("table.json", "scratch/exploded.json"):
        (work / name).write_text(json.dumps(assembly.ASSEMBLY))
    named = ["table.json", "scratch/exploded.json", "./scratch/exploded.json"]
    assert print_sides.assemblies(work, named) == ["table.json"]
    assert print_sides.assemblies(work, []) == ["table.json"]
    # With none outside it, there's none at all, and a run without scratch/ finds its own.
    (work / "table.json").unlink()
    assert print_sides.assemblies(work, named) == []
