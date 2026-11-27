"""An assembly run finishes with its assembly file as its part (mqucifer/print-shop#310).

No Docker: a scripted model drives each run, and a box answers each bench command and runs
nothing. The parts are made up here: two blocks, each "approved in its own run", given to an
assembly run that bolts them face to face, and a bracket the run makes itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import overlap as overlap_module
from print_shop.rounds import assembled, placing, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Run
from tests import test_print_side
from tests.rounds_fakes import Scripted, tool
from tests.test_holding import Answers

#: Two made-up given blocks: their bytes are all a box that runs nothing reads of them.
LEFT = b"ISO-10303-21;\nDATA;\n#1=BLOCK('left',20.,20.,10.);\nENDSEC;\n"
RIGHT = b"ISO-10303-21;\nDATA;\n#1=BLOCK('right',20.,20.,10.);\nENDSEC;\n"
LEFT_SHA = hashlib.sha256(LEFT).hexdigest()
RIGHT_SHA = hashlib.sha256(RIGHT).hexdigest()
#: A made-up stand-in for a load, given and never placed or printed (mqucifer/print-shop#365).
LOAD = b"ISO-10303-21;\nDATA;\n#1=BLOCK('load',40.,40.,0.4);\nENDSEC;\n"

REQUEST = f"""\
Bolt the two given blocks together, face to face, as one assembly. They're approved: don't
change them.

| part | hash | approved |
| --- | --- | --- |
| `left.step` | {LEFT_SHA[:12]} | approved in block-left-2 |
| `references/right.step` | {RIGHT_SHA} | the Sponsor, 2026-11-24 |

## Requirements
- The two blocks meet face to face.

## Stops
- no plan
"""
BROKEN_DOWN = tool(
    "breakdown",
    parts=[
        {"name": "left", "what": "the given left block, unchanged"},
        {"name": "right", "what": "the given right block, unchanged"},
    ],
    joins=[{"parts": ["left", "right"], "how": "bolted face to face"}],
    look=["two blocks side by side"],
    **{"from": "the request"},
)
#: A breakdown with a part of the run's own beside the given blocks: a part run's.
BROKEN_DOWN_OWN = tool(
    "breakdown",
    parts=[
        {"name": "left", "what": "the given left block, unchanged"},
        {"name": "right", "what": "the given right block, unchanged"},
        {"name": "bracket", "what": "a bracket made here, holding the blocks together"},
    ],
    joins=[
        {"parts": ["left", "bracket"], "how": "screwed to the bracket"},
        {"parts": ["right", "bracket"], "how": "screwed to the bracket"},
    ],
    look=["two blocks on a bracket"],
    **{"from": "the request"},
)
MEET = {
    "id": "meet",
    "what": "the blocks meet face to face",
    "source": "request",
    "quote": "The two blocks meet face to face.",
    "covers": ["R1"],
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance references/left.step references/right.step --placed table.json",
        "verdicts": ["TOUCHING"],
    },
}
#: A need on the bracket the run makes, which is printed.
BRACKET = {
    "id": "bracket",
    "what": "the bracket clears the left block",
    "source": "chosen",
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance references/left.step bracket.step",
        "verdicts": ["CLEAR"],
    },
}
ASSEMBLY: dict[str, Any] = {
    "parts": [
        {"name": "left", "file": "references/left.step", "layers": "z", "at": [0, 0, 0]},
        {"name": "right", "file": "references/right.step", "layers": "z", "at": [20, 0, 0]},
    ],
    "joints": [
        {
            "between": ["left", "right"],
            "kind": "bolted",
            "bolt": "m4-8.8",
            "insert": "m4-heat-set",
            "at": [[20, 10, 5]],
        }
    ],
}
DONE = {"script": "make.py", "part": ["table.json"], "fix_shape": "first", "closing": "Bolted."}
#: The placed pair touching: what `clearance` gives two blocks face to face.
TOUCH = ("TOUCHING", {"pairs": [{"a": "left.step", "b": "right.step", "verdict": "TOUCHING"}]})
INTO = ("INTERFERE", {"pairs": [{"a": "left.step", "b": "right.step", "verdict": "INTERFERE"}]})


class Box(Answers):
    """`Answers`, keeping where each command ran, and writing `bracket.step` from the script,
    as a run that made a file would."""

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        (where or self.work).joinpath("bracket.step").write_bytes(b"made")
        return Ran(0, "ran\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "overlap":
            said = {"files": {name: {} for name in args}, "pairs": [], "unread": {}}
            return Ran(0, overlap_module.MARK + json.dumps(said), 0.1)
        return super().helper(module, args, timeout, where)


def given(root: Path, stand_in: bool = False) -> list[Path]:
    folder = root / "given"
    folder.mkdir(exist_ok=True)
    (folder / "left.step").write_bytes(LEFT)
    (folder / "right.step").write_bytes(RIGHT)
    if stand_in:
        (folder / "load.step").write_bytes(LOAD)
    return [folder / "left.step", folder / "right.step", *([folder / "load.step"] * stand_in)]


def _run(
    root: Path,
    *turns: Any,
    answers: tuple[Any, ...] = (TOUCH, TOUCH),
    assembly: dict[str, Any] | None = None,
    request: str = REQUEST,
    assembly_run: bool = True,
    stand_in: bool = False,
) -> Run:
    model = Scripted(*[[t] for t in turns], [tool("blocked", reason="Out of turns.")])
    run = Run.start(
        root / "runs",
        "r",
        request,
        given(root, stand_in),
        model,
        sleep=lambda _: None,
        assembly_run=assembly_run,
    )
    (run.work / "make.py").write_text("print('placed')\n")
    (run.work / "table.json").write_text(json.dumps(assembly or ASSEMBLY))
    box: Any = Box(run.work, *answers)
    run.box = box
    run.tools.box = box
    return run


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def done_reply(run: Run) -> dict[str, Any]:
    return [r for r in replies(run) if r["name"] == "done"][-1]


# The assembly run


def test_two_given_blocks_bolted_finish_with_the_assembly_and_the_stop_draws_both(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE))
    assert run.drive().name == "met"
    done = done_reply(run)
    assert done["outcome"] == "ok" and done["detail"]["met"] is True, done["reply"]
    reply = done["reply"]
    assert "The result is an assembly of given parts: `table.json`" in reply
    assert f"- `references/left.step`, sha256 {LEFT_SHA}, as given (request), approved in " \
        "`block-left-2`" in reply
    assert f"- `references/right.step`, sha256 {RIGHT_SHA}, as given (request), " \
        "approved run not named" in reply
    # The harness's own placed reading: every pair, placed, and nothing passing into anything.
    asked: list[list[str]] = run.box.asked  # type: ignore[attr-defined]
    assert [
        "clearance", "references/left.step", "references/right.step", "--placed", "table.json"
    ] in [a[2:] for a in asked], asked  # fmt: skip
    assert "nothing passes into anything; `left.step` and `right.step` touch" in reply
    # The printed-file readings aren't run on given parts, and the reply names their runs.
    assert "aren't run on the given parts: each was read in its own approved run" in reply
    assert not any("thin" in a for a in asked), asked
    # The stop draws the placed whole: both given blocks, placed, each its own colour and hash.
    (render,) = [a for a in asked if "render" in a]
    assert "table=references/left.step,references/right.step" in render
    assert render[render.index("--placed") + 1] == "table.json"
    looks = [a for a in render if a.startswith("--look=")]
    assert len(looks) == 2 and len({a.split("=")[2].split(":")[0] for a in looks}) == 2, looks
    assert any(LEFT_SHA[:12] in a for a in looks) and any(RIGHT_SHA[:12] in a for a in looks)
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"## {assembled.HEADING}" in stop
    assert f"`table.json`, sha256 {hashlib.sha256(json.dumps(ASSEMBLY).encode()).hexdigest()}" \
        in stop
    assert "None: this run made no file to print." in stop
    # The ledger and the record: an assembly of given parts, its hashes and their runs.
    (row,) = run.ledger.rows("SELECT * FROM assemblies")
    assert (row["assembly"], row["met"], row["approval"], json.loads(row["made"])) == (
        "table.json", 1, 1, []
    )  # fmt: skip
    kept = {one["file"]: one for one in json.loads(row["given"])}
    assert kept["references/left.step"]["sha"] == LEFT_SHA
    assert kept["references/left.step"]["approved_in"] == "block-left-2"
    assert kept["references/right.step"]["approved_in"] is None
    start = {r["file"]: r["sha"] for r in run.ledger.rows("SELECT * FROM given_files")}
    assert start == {"references/left.step": LEFT_SHA, "references/right.step": RIGHT_SHA}
    record = (run.work / "record" / "ROUNDS.md").read_text()
    assert "The result is an assembly of given parts: `table.json`" in record


def test_a_reading_of_a_placed_given_part_is_given_the_assembly(tmp_path: Path) -> None:
    run = _run(tmp_path, BROKEN_DOWN)
    against = ["clearance", "references/left.step", "references/right.step"]
    # Before `done` names it, a reading of given parts alone is run as it's written.
    assert run._placed_argv(list(against), run.work) == against
    run.state.assembly = "table.json"
    assert run._placed_argv(list(against), run.work) == [*against, "--placed", "table.json"]
    named = [*against, "--placed", "other.json"]
    assert run._placed_argv(list(named), run.work) == named
    assert run._placed_argv(["thin", "references/left.step"], run.work) == [
        "thin",
        "references/left.step",
    ]


def test_placed_passing_into_each_other_isnt_met(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        answers=(TOUCH, INTO),
    )
    assert run.drive().name == "blocked"
    done = done_reply(run)
    assert done["outcome"] == "ok" and done["detail"]["met"] is False, done["reply"]
    assert "`left.step` and `right.step` pass into each other" in done["reply"]
    (row,) = run.ledger.rows("SELECT * FROM assemblies")
    assert row["met"] == 0 and row["approval"] is None


def test_a_made_file_named_beside_the_assembly_is_read_as_a_print(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET, BRACKET]),
        tool(
            "done",
            **{**DONE, "part": ["table.json", "bracket.step"]},
            on_bed={"bracket.step": "-z"},
        ),
        answers=(TOUCH, "CLEAR", TOUCH, "OK"),
    )
    assert run.drive().name == "met"
    asked: list[list[str]] = run.box.asked  # type: ignore[attr-defined]
    # The harness's own `thin` on every printed file: the bracket, and no given part.
    assert ["thin", "bracket.step"] in [a[2:] for a in asked], asked
    assert not any("thin" in a and any("references/" in w for w in a) for a in asked)
    (row,) = run.ledger.rows("SELECT * FROM assemblies")
    assert json.loads(row["made"]) == ["bracket.step"]


# The controls


def _refused(run: Run) -> dict[str, Any]:
    assert run.drive().name == "blocked"
    done = done_reply(run)
    assert done["outcome"] == "failed", done["reply"]
    return done


def test_a_placed_reference_changed_by_one_byte_is_refused_naming_it(tmp_path: Path) -> None:
    run = _run(tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE))
    (run.work / "references" / "right.step").write_bytes(RIGHT[:-1] + b"!")
    done = _refused(run)
    assert done["why"] == "assembly_changed"
    assert "- `references/right.step` has changed:" in done["reply"]
    assert "left.step" not in done["reply"].split("\n", 1)[1]
    assert run.ledger.rows("SELECT * FROM assemblies") == []


def test_a_placed_file_the_run_made_is_refused_as_not_given(tmp_path: Path) -> None:
    made = {"parts": [*ASSEMBLY["parts"], {"name": "b", "file": "bracket.step", "layers": "z"}]}
    run = _run(
        tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE), assembly=made
    )
    (run.work / "bracket.step").write_bytes(b"made")
    done = _refused(run)
    assert done["why"] == "assembly_not_given"
    assert "- `bracket.step` isn't given to you" in done["reply"]


def test_a_file_the_run_wrote_under_references_is_refused_as_not_given(tmp_path: Path) -> None:
    smuggled = {
        "parts": [
            *ASSEMBLY["parts"],
            {"name": "b", "file": "references/bracket.step", "layers": "z"},
        ]
    }
    run = _run(
        tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE), assembly=smuggled
    )
    (run.work / "references" / "bracket.step").write_bytes(b"made")
    done = _refused(run)
    assert done["why"] == "assembly_not_given"
    assert "- `references/bracket.step` isn't given to you" in done["reply"]


def test_an_empty_assembly_is_refused(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        assembly={"parts": []},
    )
    done = _refused(run)
    assert done["why"] == "assembly_empty"
    assert "`table.json` places nothing" in done["reply"]


def test_the_script_changing_a_given_part_in_the_clean_run_isnt_met(tmp_path: Path) -> None:
    run = _run(tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE))
    box: Any = run.box

    def python(script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        (where or run.work).joinpath("references", "left.step").write_bytes(b"rewritten")
        return Ran(0, "ran\n", 0.1)

    box.python = python
    assert run.drive().name == "blocked"
    done = done_reply(run)
    assert done["outcome"] == "ok" and done["detail"]["met"] is False
    assert "- `references/left.step` has changed:" in done["reply"]
    assert (run.work / "references" / "left.step").read_bytes() == LEFT


def _through_a_link(work: Path) -> None:
    """What a script could do: a link under `references/` to a folder of its own, holding a
    file of its own with a given file's name."""
    (work / "made" / "x").mkdir(parents=True, exist_ok=True)
    (work / "made" / "left.step").write_bytes(b"the model's own block")
    (work / "references" / "sub").symlink_to("../made/x")


#: `references/sub/../left.step`: cleaned as text it's the given block, and opened by the OS
#: through the link, `references/sub` to `made/x`, it's `made/left.step`.
CROOKED: dict[str, Any] = {
    "parts": [
        {"name": "left", "file": "references/sub/../left.step", "layers": "z", "at": [0, 0, 0]},
        ASSEMBLY["parts"][1],
    ]
}


def test_a_made_file_through_a_link_and_dots_isnt_taken_for_a_reference(tmp_path: Path) -> None:
    """The review's probe: the OS reads the model's bytes where the name, cleaned, is a given
    file's, so the name is refused as it's written."""
    run = _run(
        tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE), assembly=CROOKED
    )
    _through_a_link(run.work)
    assert (run.work / "references/sub/../left.step").read_bytes() == b"the model's own block"
    done = _refused(run)
    assert done["why"] == "assembly_not_given"
    assert "- `references/sub/../left.step` isn't a plain path to a given file" in done["reply"]


@pytest.mark.parametrize(
    "name", ["references/./left.step", "references//left.step", "/work/references/left.step"]
)
def test_a_placed_name_that_isnt_as_written_is_refused(tmp_path: Path, name: str) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "left.step").write_bytes(LEFT)
    (tmp_path / "a.json").write_text(json.dumps({"parts": [{"name": "l", "file": name}]}))
    checked = assembled.check(tmp_path, "a.json", {"references/left.step": LEFT_SHA}, {}, {})
    assert checked.crooked == [name] and checked.given == []


def test_a_link_the_clean_run_makes_isnt_met(tmp_path: Path) -> None:
    """`copy_plain` drops links, and a script can make them again in `judged/`."""
    run = _run(tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE))
    box: Any = run.box

    def python(script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        here = where or run.work
        _through_a_link(here)
        (here / "table.json").write_text(json.dumps(CROOKED))
        return Ran(0, "ran\n", 0.1)

    box.python = python
    assert run.drive().name == "blocked"
    done = done_reply(run)
    assert done["outcome"] == "ok" and done["detail"]["met"] is False
    assert "`references/sub/../left.step` isn't a plain path to a given file" in done["reply"]


def test_one_given_part_placed_alone_is_no_assembly(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        assembly={"parts": [ASSEMBLY["parts"][0]]},
    )
    done = _refused(run)
    assert done["why"] == "assembly_too_few"
    assert "places `references/left.step`: an assembly puts 2 or more" in done["reply"]


def test_a_part_run_naming_an_assembly_file_is_refused_as_on_main(tmp_path: Path) -> None:
    """A run that's no assembly run, unmarked, of no assembly kind, with a part of its own in
    its breakdown, can't finish having made nothing: it's held to the part rules."""
    run = _run(
        tmp_path,
        BROKEN_DOWN_OWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        assembly_run=False,
    )
    assert run._assembly_run_by() is None
    done = _refused(run)
    assert (done["why"], done["reply"]) == (
        "not_geometry", "`part` names STEP or STL files, and `table.json` isn't one."
    )  # fmt: skip
    assert run.ledger.rows("SELECT * FROM assemblies") == []


def test_a_person_marks_a_run_already_started_as_an_assembly_run(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN_OWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        assembly_run=False,
    )
    run.mark_assembly_run("sponsor")
    assert run.drive().name == "met"
    marked = [
        e for e in transcript.events_of(run.session.events) if e["kind"] == "assembly_run"
    ]
    assert [e["by"] for e in marked] == ["sponsor"]
    assert Run.load(run.folder, run.session.send).state.assembly_run is True


# An assembly run by its request's kind, or its breakdown (mqucifer/print-shop#365)


def test_an_unmarked_run_whose_breakdown_places_only_given_parts_finishes_with_its_assembly(
    tmp_path: Path,
) -> None:
    """A design run's assembly was stopped at `done` with every need passing: it wasn't marked,
    so a JSON file in `part` was refused. Its breakdown names only the given blocks."""
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        assembly_run=False,
    )
    assert run.drive().name == "met"
    assert run._assembly_run_by() == "breakdown"
    done = done_reply(run)
    assert done["outcome"] == "ok" and done["detail"]["met"] is True, done["reply"]
    assert "The result is an assembly of given parts: `table.json`" in done["reply"]
    assert f"- `references/left.step`, sha256 {LEFT_SHA}, as given (request)" in done["reply"]
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert f"## {assembled.HEADING}" in stop and "None: this run made no file to print." in stop
    (row,) = run.ledger.rows("SELECT * FROM assemblies")
    assert (row["assembly"], row["met"], json.loads(row["made"])) == ("table.json", 1, [])


def test_a_request_of_the_assembly_kind_finishes_with_its_assembly(tmp_path: Path) -> None:
    request = REQUEST.replace("## Stops", "## Kind\nassembly\n\n## Stops")
    run = _run(
        tmp_path,
        BROKEN_DOWN_OWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        request=request,
        assembly_run=False,
    )
    assert run._assembly_run_by() == "kind"
    assert run.drive().name == "met"
    assert done_reply(run)["detail"]["met"] is True


def test_an_assembly_run_that_adds_a_part_holds_it_to_the_part_rules(
    tmp_path: Path,
) -> None:
    """A run that adds a printed file names it beside the assembly, and it's read with every
    check, as on a marked run."""
    request = REQUEST.replace("## Stops", "## Kind\nassembly\n\n## Stops")
    run = _run(
        tmp_path,
        BROKEN_DOWN_OWN,
        tool("needs", needs=[MEET, BRACKET]),
        tool(
            "done",
            **{**DONE, "part": ["table.json", "bracket.step"]},
            on_bed={"bracket.step": "-z"},
        ),
        answers=(TOUCH, "CLEAR", TOUCH, "FAIL"),
        request=request,
        assembly_run=False,
    )
    assert run.drive().name == "blocked"
    done = done_reply(run)
    assert done["detail"]["met"] is False, done["reply"]
    asked: list[list[str]] = run.box.asked  # type: ignore[attr-defined]
    assert ["thin", "bracket.step"] in [a[2:] for a in asked], asked
    assert not any("thin" in a and any("references/" in w for w in a) for a in asked)
    standing = json.loads((run.folder / "rounds" / "001" / "standing.json").read_text())
    assert [(one["file"], one["passed"]) for one in standing["printable"]] == [
        ("bracket.step", False)
    ]  # fmt: skip


def test_a_given_stand_in_failing_thin_isnt_held_against_the_assembly(tmp_path: Path) -> None:
    """At the same stop the harness listed a stand-in and a placed copy of an approved part as
    part files whose last `thin` failed. A given file is never printed."""
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("bench", command="thin references/load.step"),
        tool("bench", command="thin references/left.step --nozzle 0.6"),
        tool(
            "ask",
            question="Do the blocks sit right with the load on them?",
            variants=[
                {"name": "A", "files": ["references/left.step", "references/right.step"]},
                {"name": "B", "files": ["references/load.step"]},
            ],
        ),
        tool("done", **DONE),
        answers=("FAIL", "FAIL", TOUCH, TOUCH, TOUCH),
        assembly_run=False,
        stand_in=True,
    )
    assert run.drive().name == "met"
    benched = [r for r in replies(run) if r["name"] == "bench"]
    said = benched[0]["reply"]
    assert "`references/load.step`: given, not printed, so it isn't read or held" in said
    assert "whose last `thin` failed" not in said and "Checks whose latest try" not in said
    assert benched[0]["detail"]["thin_on_given"] == ["references/load.step"]
    assert "Checks whose latest try" not in benched[1]["reply"]
    assert run._thin_failing() == [] and run._holding() == []
    # The ask draws the given files, and reads none of them for printing: it says why.
    ask = [r for r in replies(run) if r["name"] == "ask"][-1]
    assert ask["outcome"] == "ok", ask["reply"]
    assert "given, not printed, so it isn't read or held for printing" in ask["reply"]
    assert "whose last `thin` failed" not in ask["reply"]
    assert run.state.draft is None or not run.state.draft.get("thin_failing")
    asked: list[list[str]] = run.box.asked  # type: ignore[attr-defined]
    # The model's own two `thin` calls, and none of the harness's, which run with `--out`.
    harness = [a for a in asked if a[:1] == ["--out"] and a[2:3] == ["thin"]]
    assert not any(w.startswith("references/") for a in harness for w in a), harness
    assert [a for a in asked if a[:1] == ["thin"]] == [
        ["thin", "references/load.step"], ["thin", "references/left.step", "--nozzle", "0.6"]
    ]  # fmt: skip
    # `done` passes with the assembly file, and says why the stand-in isn't read.
    done = done_reply(run)
    assert done["detail"]["met"] is True, done["reply"]
    assert "Nor on the other given files, `references/load.step`" in done["reply"]
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "whose last `thin` failed" not in stop
    assert "Nor on the other given files, `references/load.step`" in stop


# A reference replaced while the run goes on


def test_a_reference_swapped_by_a_note_with_its_hash_is_held_to_that_hash(tmp_path: Path) -> None:
    run = _run(tmp_path, BROKEN_DOWN, tool("needs", needs=[MEET]), tool("done", **DONE))
    swapped = RIGHT + b"\n"
    (run.work / "references" / "right.step").write_bytes(swapped)
    run.state.notes.append(
        "The Sponsor approved a changed right block: "
        f"`right.step` {hashlib.sha256(swapped).hexdigest()[:12]}, approved in block-right-3."
    )
    assert run.drive().name == "met"
    done = done_reply(run)
    assert "approved in `block-right-3`" in done["reply"]
    assert "stated in note 1:" in done["reply"]


def test_a_reference_swapped_with_no_hash_stated_is_refused(tmp_path: Path) -> None:
    request = REQUEST.replace(f"| `references/right.step` | {RIGHT_SHA} |", "| right | |")
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **DONE),
        request=request,
    )
    (run.work / "references" / "right.step").write_bytes(RIGHT + b"\n")
    run.state.notes.append("The right block was replaced.")
    done = _refused(run)
    assert "- `references/right.step` has changed:" in done["reply"]
    assert "(start)" in done["reply"]


# A part run, as on main


def test_a_part_run_names_no_assembly_and_reads_as_before(tmp_path: Path) -> None:
    run = test_print_side._run(
        tmp_path,
        test_print_side.BROKEN_DOWN,
        tool("needs", needs=[test_print_side.ONE_PIECE, test_print_side.SIDE]),
        tool("done", on_bed={"tray.step": "-z"}, **test_print_side.DONE),
    )
    assert run.drive().name == "met"
    done = done_reply(run)
    assert assembled.HEADING not in done["reply"]
    assert "aren't run on the given parts" not in done["reply"]
    assert run.ledger.rows("SELECT * FROM assemblies") == []
    assert run.state.assembly is None
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert assembled.HEADING not in stop and "Open one folder only." in stop


def test_naming_the_references_in_part_is_still_refused(tmp_path: Path) -> None:
    run = _run(
        tmp_path,
        BROKEN_DOWN,
        tool("needs", needs=[MEET]),
        tool("done", **{**DONE, "part": ["references/left.step", "references/right.step"]}),
    )
    done = _refused(run)
    assert done["why"] == "no_part"
    assert "given to you, which isn't printed" in done["reply"]


# The hashes a person states


@pytest.mark.parametrize(
    ("line", "found"),
    [
        (f"| `left.step` | {LEFT_SHA[:12]} | the Sponsor |", ("references/left.step", None)),
        (f"`references/left.step` {LEFT_SHA}, approved in block-left-2", (
            "references/left.step", "block-left-2"
        )),
        (f"`left.step` {LEFT_SHA[:11]}", None),
        (f"`left.step` and `right.step`: {LEFT_SHA[:12]}", None),
        (f"`left.step` {LEFT_SHA[:12]} {RIGHT_SHA[:12]}", None),
        (f"`notes.md` {LEFT_SHA[:12]}", None),
    ],
)  # fmt: skip
def test_a_hash_is_stated_beside_one_named_file(
    line: str, found: tuple[str, str | None] | None
) -> None:
    said = assembled.stated_in(line, "request")
    if found is None:
        assert said == []
        return
    (one,) = said
    assert (one.file, one.approved_in) == found
    assert LEFT_SHA.startswith(one.sha)


def test_the_latest_stated_hash_holds(tmp_path: Path) -> None:
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / "left.step").write_bytes(LEFT)
    (tmp_path / "a.json").write_text(json.dumps({"parts": [ASSEMBLY["parts"][0]]}))
    start = {"references/left.step": "0" * 64}
    later = assembled.stated(
        "", [f"`left.step` {'1' * 12}", f"`left.step` {LEFT_SHA[:16]}, approved in b-2"]
    )
    checked = assembled.check(tmp_path, "a.json", start, later, {})
    assert not checked.wrong and checked.given[0].source == "note 2"
    assert [c["source"] for c in checked.given[0].changes] == ["note 1", "note 2"]
    before = assembled.check(tmp_path, "a.json", None, {}, {})
    assert before.wrong and "has no hash held for it" in before.refused()
    assert placing.placed_by(tmp_path, "a.json") == {"references/left.step"}
