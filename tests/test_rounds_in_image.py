"""A scripted fake model drives a real run, in real containers, with no model call.

Every ending, every tool, a resume after `ask`, a failing need going back, and a `done` the checks
refuse not ending the run. The part is one made up here, of plain boxes. Needs the image, from
`scripts/bench.py build`.
"""

from __future__ import annotations

import json
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import transcript
from print_shop.rounds.box import Box, Gone
from print_shop.rounds.run import Refused, Run, only_session, summarise, what_next
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, raw, tool

pytestmark = pytest.mark.bench

REQUEST = (
    "A small block with a tab beside it. Keep a 0.5 mm gap between them, and it should look good."
)


def png() -> bytes:
    """A one-pixel PNG, written out here."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")
    )


def script(gap: float, size: float = 20.0) -> str:
    return (
        "import cadquery as cq\n"
        f"SIZE = {size}\n"
        f"GAP = {gap}\n"
        'block = cq.Workplane("XY").box(SIZE, SIZE, 5)\n'
        'tab = cq.Workplane("XY").box(10, 10, 5).translate((SIZE / 2 + 5 + GAP, 0, 0))\n'
        'cq.exporters.export(block, "block.step")\n'
        'cq.exporters.export(tab, "tab.step")\n'
    )


GAP_NEED = {
    "id": "gap",
    "what": "a 0.5 mm gap between block and tab",
    "source": "request",
    "quote": "a 0.5 mm gap",
    "check": {
        "by": "bench",
        "command": "clearance block.step tab.step --min-gap 0.2",
        "number": "pairs.0.gap_mesh_mm",
        "at_least": 0.45,
        "at_most": 0.55,
    },
}
SIZE_NEED = {
    "id": "volume",
    "what": "the block is the size chosen",
    "source": "chosen",
    "check": {
        "by": "bench",
        "command": "info block.step",
        "number": "volume_mesh_mm3",
        "at_most": 3000,
    },
}
PARAM_NEED = {
    "id": "gap-drives",
    "what": "the gap's a parameter that moves the tab",
    "source": "derived",
    "check": {"by": "bench", "command": "params make.py --set GAP=3.0"},
}
EYE_NEED = {
    "id": "look",
    "what": "it looks good",
    "source": "request",
    "quote": "it should look good",
    "check": {"by": "eye", "look_for": "whether the proportions please"},
}


def begin(
    tmp_path: Path, *turns: Any, limits: Limits | None = None, **kw: Any
) -> tuple[Run, Scripted]:
    model = Scripted(*turns, **kw)
    run = Run.start(tmp_path, "proof", REQUEST, [], model, limits, sleep=lambda _: None)
    return run, model


def again(run: Run, *turns: Any, limits: Limits | None = None) -> tuple[Run, Scripted]:
    model = Scripted(*turns)
    return Run.load(run.folder, model, limits, sleep=lambda _: None), model


def replies(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def reply_of(run: Run, name: str) -> dict[str, Any]:
    return [e for e in replies(run) if e["name"] == name][-1]


def rows(run: Run, table: str) -> list[dict[str, Any]]:
    found = run.ledger.rows(f"SELECT * FROM {table} WHERE run=?", run.state.name)  # noqa: S608
    return [dict(r) for r in found]


def write(path: str, content: str, **more: Any) -> dict[str, Any]:
    return tool("write", path=path, content=content, **more)


# The whole way, with every tool


def test_a_run_goes_from_intake_through_refusals_and_a_pick_to_its_needs_met(
    tmp_path: Path,
) -> None:
    run, model = begin(
        tmp_path,
        [tool("lookup", query="Solid.fuse"), tool("lookup", query="Workplane.box", page=1)],
        [tool("docs", query="fillet edges"), tool("skill", name="bench")],
        [raw("write", "not json")],
        [tool("questions", questions="How big should the block be?")],
    )
    (run.work / "references" / "note.txt").write_text("a reference\n")
    (run.work / "references" / "look.png").write_bytes(png())
    (run.work / "references" / "not-a-picture.png").write_bytes(b"nope")
    model.turns.insert(
        3,
        [
            tool("read", path="references/note.txt"),
            tool("read", path="references/look.png"),
            tool("read", path="references/not-a-picture.png"),
        ],
    )
    ending = run.drive()

    # It stopped for the Sponsor, with its questions in a file.
    assert ending.name == "questions"
    assert (run.folder / "questions" / "1.md").read_text() == "How big should the block be?\n"
    assert "1.answers.md" in what_next(run, ending)
    assert run.state.stop == {"kind": "questions", "n": 1}
    assert rows(run, "runs")[0]["ending"] is None, "waiting isn't an ending"
    found = {e["name"]: e for e in replies(run)}
    looked = [e for e in replies(run) if e["name"] == "lookup"]
    assert "cq.Shape.fuse" in looked[0]["reply"], "an inherited name is found"
    assert found["docs"]["outcome"] == "ok" and "fillet" in found["docs"]["reply"].lower()
    assert "# The bench" in found["skill"]["reply"]
    read = [e for e in replies(run) if e["name"] == "read"]
    assert [e["outcome"] for e in read] == ["ok", "ok", "failed"]
    assert read[2]["why"] == "not_a_picture"
    assert [e["outcome"] for e in replies(run) if e["name"] == "write"] == ["malformed"]
    assert model.bodies[-1]["messages"][-1]["content"][1]["type"] == "image_url"

    # Going on is refused until the answers are there.
    run, model = again(run)
    with pytest.raises(Refused, match="aren't written"):
        run.resume()
    (run.folder / "questions" / "1.answers.md").write_text("Make it 20 mm across.\n")

    bad_quote = {**GAP_NEED, "quote": "a gap of 9 mm"}
    bad_command = {**SIZE_NEED, "check": {"by": "bench", "command": "info"}}
    need_set = [GAP_NEED, SIZE_NEED, PARAM_NEED, EYE_NEED]
    run, model = again(
        run,
        [tool("done", script="make.py", part=["block.step"])],
        [tool("needs", needs=[bad_quote])],
        [tool("needs", needs=[bad_command])],
        [tool("needs", needs=need_set)],
        [tool("needs", needs=need_set)],
        # A name on the wrong class: the hint says where it is.
        [write("oops.py", 'import cadquery as cq\ncq.Workplane("XY").isValid()\n', run=True)],
        # The first try: the gap's wrong.
        [write("make.py", script(gap=2.0), run="true")],
        [tool("read", path="make.py")],
        [tool("bench", command="info block.step")],
        [tool("bench", command="render block.step tab.step --views iso")],
        [tool("run", path="make.py")],
        # The checks refuse this `done`: the run doesn't end.
        [tool("done", script="make.py", part=["block.step"])],
        [tool("edit", path="make.py", old="GAP = 2.0", new="GAP = 0.5", run=True)],
        [tool("bench", command="clearance block.step tab.step --min-gap 0.2")],
        # The bench now passes, and the eye need is still open.
        [tool("done", script="make.py", part=["block.step"], fix_shape="narrowed the gap")],
        [write("b.py", script(gap=0.5, size=25.0).replace("block.step", "wide.step"), run=True)],
        [
            tool(
                "ask",
                question="Which proportions?",
                need="nope",
                variants=[{"name": "A", "files": ["block.step"]}],
            )
        ],
        [
            tool(
                "ask", question="Which proportions?", variants=[{"name": "A", "files": ["no.step"]}]
            )
        ],
        [
            tool(
                "ask",
                question="Which proportions?",
                need="look",
                variants=[
                    {"name": "A", "files": ["block.step", "tab.step"], "about": "the even one"},
                    {"name": "B", "files": ["wide.step", "tab.step"], "about": "the wide one"},
                ],
            ),
            tool("bench", command="info block.step"),
        ],
    )
    run.resume()
    assert run.session.messages[-1]["content"].startswith("The Sponsor's answers:")
    ending = run.drive()

    # The stop at `ask`.
    assert ending.name == "ask", ending
    folder = run.folder / "asks" / "1"
    assert (folder / "sheet.png").read_bytes().startswith(b"\x89PNG")
    assert "Which proportions?" in (folder / "question.md").read_text()
    assert (folder / "variants" / "B" / "wide.step").is_file()

    named = [e for e in replies(run) if e["name"] == "needs"]
    assert [e["outcome"] for e in named] == ["failed", "failed", "ok", "failed"]
    assert 'quotes "a gap of 9 mm"' in named[0]["reply"]
    assert "the bench can't read `info`" in named[1]["reply"]
    assert named[3]["why"] == "needs_settled", "settled needs can't be loosened by the model"
    assert json.loads((run.folder / "needs.json").read_text())[0]["id"] == "gap"

    calls = [e for e in replies(run) if e["name"] == "done"]
    assert calls[0]["why"] == "no_needs"
    first, second = calls[1], calls[2]
    assert first["outcome"] == "ok", "a refused `done` is a tool that did what it was asked"
    assert "Round 1: not met yet" in first["reply"]
    assert "gap, NOT MET" in first["reply"]
    assert "`pairs.0.gap_mesh_mm` is 2, over 0.55" in first["reply"]
    assert "Round 2 starts now" in first["reply"]
    assert "volume, met" in first["reply"]
    assert "gap-drives, met" in first["reply"]
    assert "Round 2: not met yet" in second["reply"]
    assert "gap, met" in second["reply"]
    assert "(last round: 2.0)" in second["reply"]
    assert "look, NOT MET" in second["reply"]
    assert "`ask`" in second["reply"]
    assert (
        "`isValid` isn't on Workplane"
        in [e for e in replies(run) if e["name"] == "write"][1]["reply"]
    )
    assert any(e["name"] == "bench" and e["outcome"] == "ok" for e in replies(run))
    assert "Round 2" not in (run.work / "record" / "ROUNDS.md").read_text().split("## Round 2")[0]
    assert "NOT MET" in (run.work / "record" / "ROUNDS.md").read_text()
    assert "GAP = 0.5" in (run.work / "record" / "round-2.diff").read_text()
    assert run.state.round == 3

    # Going on is refused until a pick that can be taken is there.
    run, model = again(run)
    with pytest.raises(Refused, match="isn't written"):
        run.resume()
    (folder / "pick.json").write_text('{"pick": "Z", "reason": "no", "by": "sponsor"}')
    with pytest.raises(Refused, match="the pick is one of A, B, or none"):
        run.resume()
    (folder / "pick.json").write_text('{"pick": "A", "reason": "", "by": "sponsor"}')
    with pytest.raises(Refused, match="reason"):
        run.resume()
    (folder / "pick.json").write_text('{"pick": "A", "reason": "The even one sits better."}')
    with pytest.raises(Refused, match="says who picked"):
        run.resume()
    (folder / "pick.json").write_text(
        '{"pick": "A", "reason": "The even one sits better.", "by": "sponsor"}'
    )

    run, model = again(run, [tool("done", script="make.py", part=["block.step"])])
    run.resume()
    assert run.session.messages[-1]["content"] == (
        "The Sponsor picked A. Their reason: The even one sits better."
    )
    ending = run.drive()
    assert ending.name == "met", ending
    assert (run.folder / "final-sheet.png").is_file()
    with pytest.raises(Refused, match="met its needs"):
        run.resume()

    # The ledger holds it, written by code.
    runs = rows(run, "runs")[0]
    assert runs["ending"] == "met"
    assert json.loads(runs["limits"])["calls"] == Limits().calls
    assert runs["image"] == host.IMAGE and runs["image_id"].startswith("sha256:")
    assert runs["alias"] == "crew-code-think"
    assert len(rows(run, "needs")) == 4
    rounds = sorted(rows(run, "rounds"), key=lambda r: r["round"])
    assert [r["met"] for r in rounds] == [0, 0, 1]
    assert "GAP = 0.5" in rounds[1]["diff"]
    assert rounds[1]["fix_shape_model_said"] == "narrowed the gap"
    assert "make.py" in json.loads(rounds[1]["start_files"])
    assert "gap" in json.loads(rounds[1]["failing_at_start"])
    assert "look" in json.loads(rounds[2]["failing_at_start"])
    changes = run.ledger.rows(
        "SELECT * FROM changes WHERE run=? AND need='gap' AND round=2", run.state.name
    )[0]
    assert (changes["before_value"], changes["after_passed"]) == (2.0, 1)
    assert changes["after_value"] == pytest.approx(0.5, abs=1e-3)
    pick = rows(run, "picks")[0]
    assert (pick["pick"], pick["need"], pick["round"]) == ("A", "look", 2 + 1)
    assert pick["reason"] == "The even one sits better."
    assert pick["picked_by"] == "sponsor"
    assert json.loads(sorted(rows(run, "rounds"), key=lambda r: r["round"])[0]["part"]) == [
        "block.step"
    ]
    asked = rows(run, "questions")[0]
    assert asked["answers"] == "Make it 20 mm across."
    bench = rows(run, "bench_calls")
    assert any(json.loads(b["argv"])[0] == "render" for b in bench)
    assert all(b["files"] for b in bench)

    # Each request rebuilds from the records, across every stop.
    events = run.session.events
    assert transcript.rebuild(events, 1)["messages"][0]["role"] == "system"
    for n in range(1, run.session.requests + 1):
        assert transcript.rebuild(events, n)["model"] == "crew-code-think", n
    assert (
        model.bodies[0]["messages"] == transcript.rebuild(events, run.session.requests)["messages"]
    )
    assert (run.folder / "rounds" / "002" / "readings" / "gap.json").is_file()
    figures = summarise(run.folder)
    assert figures["ending"]["name"] == "met"
    assert figures["requests_rebuilt"] == figures["requests_recorded"] == run.session.requests
    assert figures["by_tool_and_outcome"]["done ok"] == 3
    assert json.loads((run.folder / "summary.json").read_text())["rounds"] == 3
    assert (run.folder / "rounds" / "003" / "judged" / "block.step").is_file()


# The harness's own check


def test_a_part_left_over_from_an_earlier_script_doesnt_count(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [write("noop.py", "print('nothing to see')\n")],
        [tool("done", script="noop.py", part=["block.step"])],
        [tool("blocked", reason="I give up")],
    )
    assert run.drive().name == "blocked"
    said = reply_of(run, "done")["reply"]
    assert "there's no block.step: a clean run has to leave it" in said
    assert (run.work / "block.step").is_file(), "the model's own copy is untouched"
    assert rows(run, "readings")[0]["passed"] == 0


def test_a_script_that_fails_from_a_clean_start_is_said_with_its_output(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED])],
        [write("bad.py", "raise SystemExit('it broke')\n")],
        [tool("done", script="bad.py", part=["block.step"])],
        [tool("blocked", reason="stop")],
    )
    run.drive()
    said = reply_of(run, "done")["reply"]
    assert "exit 1" in said and "it broke" in said
    assert "didn't finish from a clean start" in said


def test_nothing_a_run_plants_can_stand_in_for_the_bench(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        # Modules that would shadow the bench's own, and a forged answer in its folder.
        [write("trimesh.py", "raise SystemExit('forged')\n")],
        [write("bench-out/forged.json", '{"verdict": "OK", "numbers": {"volume_mesh_mm3": 1}}')],
        [tool("bench", command="info block.step")],
        [tool("done", script="make.py", part=["block.step"])],
    )
    assert run.drive().name == "met"
    assert reply_of(run, "bench")["reply"].startswith("exit 0\nverdict: OK")
    assert rows(run, "readings")[0]["value"] == pytest.approx(2000.0, abs=1)


# Who picked, and what a sheet draws


def asked_a_pick(tmp_path: Path, **ask: Any) -> Run:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED, EYE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [
            tool(
                "ask",
                question="Which proportions?",
                need="look",
                views="iso",
                variants=[{"name": "A", "files": ["block.step"], "about": "the even one"}],
                **ask,
            )
        ],
    )
    assert run.drive().name == "ask"
    return run


@pytest.mark.parametrize(
    ("by", "whose"),
    [
        (None, "says who picked"),
        ("", "says who picked"),
        ("Sponsor", "says who picked"),
        ("stand-in", "says who picked"),
        ("stand-in:  ", "says who picked"),
    ],
)
def test_a_pick_that_doesnt_say_who_made_it_is_refused(
    tmp_path: Path, by: str | None, whose: str
) -> None:
    run = asked_a_pick(tmp_path)
    found = {"pick": "A", "reason": "It sits better."}
    (run.folder / "asks" / "1" / "pick.json").write_text(json.dumps({**found, "by": by}))
    run, _ = again(run)
    with pytest.raises(Refused, match=whose):
        run.resume()


def test_a_stand_ins_pick_is_marked_everywhere_and_never_says_the_sponsor_picked(
    tmp_path: Path,
) -> None:
    """A Claude builder stood in for the Sponsor on the first real run, and every record called it
    their pick: it could have passed for their taste in later training data."""
    run = asked_a_pick(tmp_path)
    (run.folder / "asks" / "1" / "pick.json").write_text(
        '{"pick": "A", "reason": "It sits better.", "by": "stand-in: a Claude builder"}'
    )
    run, _ = again(run, [tool("done", script="make.py", part=["block.step"])])
    run.resume()
    said = run.session.messages[-1]["content"]
    assert said == (
        "A stand-in for the Sponsor (a Claude builder) picked A. Their reason: It sits better."
    )
    assert run.drive().name == "met"
    assert rows(run, "picks")[0]["picked_by"] == "stand-in: a Claude builder"
    reading = [r for r in rows(run, "readings") if r["need"] == "look"][0]
    assert reading["passed"] == 1, "a run can be proved with no Sponsor there"
    assert reading["why"].startswith("a stand-in for the Sponsor (a Claude builder) picked A")
    state = json.loads((run.folder / "state.json").read_text())
    assert state["picks"]["look"]["by"] == "stand-in: a Claude builder"
    assert summarise(run.folder)["picked_by"] == {"look": "stand-in: a Claude builder"}
    # Nothing a person or a later reader takes from this run calls it the Sponsor's.
    records = [
        run.session.events,
        run.folder / "state.json",
        run.folder / "rounds" / "001" / "judgement.json",
        run.work / "record" / "ROUNDS.md",
    ]
    for path in records:
        text = path.read_text()
        assert "stand-in" in text, path
        assert "Sponsor picked" not in text, path


def test_the_sponsors_own_pick_says_so_everywhere(tmp_path: Path) -> None:
    run = asked_a_pick(tmp_path)
    (run.folder / "asks" / "1" / "pick.json").write_text(
        '{"pick": "A", "reason": "It sits better.", "by": "sponsor"}'
    )
    run, _ = again(run, [tool("done", script="make.py", part=["block.step"])])
    run.resume()
    assert run.drive().name == "met"
    reading = [r for r in rows(run, "readings") if r["need"] == "look"][0]
    assert reading["why"].startswith("the Sponsor picked A")
    assert "The Sponsor picked: A" in (run.work / "record" / "ROUNDS.md").read_text()


FIXTURE_SCRIPT = (
    script(gap=0.5)
    + 'piece = cq.Workplane("XY").box(4, 4, 4).translate((0, 60, 0))\n'
    + 'cq.exporters.export(piece, "test-piece.step")\n'
)
FIXTURE_NEED = {
    "id": "apart",
    "what": "the test piece stands clear of the block",
    "source": "chosen",
    "check": {"by": "bench", "command": "clearance block.step test-piece.step"},
}


def final_render(run: Run) -> dict[str, Any]:
    made = run.folder / "rounds" / "001" / "judged" / "bench-out"
    found = [json.loads(p.read_text()) for p in made.glob("render-*.json")]
    assert len(found) == 1
    return dict(found[0])


def test_the_final_sheet_draws_the_part_and_leaves_out_what_only_a_need_names(
    tmp_path: Path,
) -> None:
    """The first real run's final sheet drew its two test cylinders inside the stand."""
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[FIXTURE_NEED])],
        [write("make.py", FIXTURE_SCRIPT, run=True)],
        [tool("done", script="make.py", part=["block.step"])],
    )
    assert run.drive().name == "met"
    assert (run.folder / "final-sheet.png").is_file()
    drawn = final_render(run)
    assert "block.step" in drawn["argv"]
    assert not any("test-piece" in word for word in drawn["argv"])
    # 20 x 20 mm, with the tab's file and the piece left out: their outline would be wider.
    assert drawn["numbers"]["views"]["top"]["projected_mm"] == [20.0, 20.0]
    assert json.loads(rows(run, "rounds")[0]["part"]) == ["block.step"]


def test_done_with_no_part_is_told_and_costs_only_that_call(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [tool("done", script="make.py")],
        [tool("done", script="make.py", part=[])],
        [tool("done", script="make.py", part=["make.py"])],
        [tool("done", script="make.py", part=["../outside.step"])],
        [tool("done", script="make.py", part=["block.step"])],
    )
    assert run.drive().name == "met"
    calls = [e for e in replies(run) if e["name"] == "done"]
    assert [e["why"] for e in calls[:4]] == ["missing", "no_part", "not_geometry", "outside"]
    assert run.state.round == 1, "none of those was a round"


def test_a_part_a_clean_run_doesnt_leave_isnt_met(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [tool("done", script="make.py", part=["stand.step"])],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    said = reply_of(run, "done")["reply"]
    assert "not met yet" in said
    assert "`part` names `stand.step`, which a clean run didn't leave." in said
    assert rows(run, "rounds")[0]["met"] == 0
    assert not (run.folder / "final-sheet.png").exists()


def test_a_picks_sheet_can_hold_a_close_up_and_marks_a_test_piece(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("needs", needs=[FIXTURE_NEED, EYE_NEED])],
        [write("make.py", FIXTURE_SCRIPT, run=True)],
        [
            tool(
                "ask",
                question="Which proportions?",
                need="look",
                views="top",
                close="-5,0,2.5,4",
                test_pieces=["test-piece.step"],
                variants=[{"name": "A", "files": ["block.step"], "about": "the even one"}],
            )
        ],
    )
    assert run.drive().name == "ask"
    folder = run.folder / "asks" / "1"
    head = (folder / "sheet.png").read_bytes()[16:24]
    width, height = int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")
    assert (width, height) == (1024, 512), "the view, and the close-up beside it"
    asked = json.loads((folder / "asked.json").read_text())
    assert asked["test_pieces"] == ["test-piece.step"]
    assert (folder / "test-pieces" / "test-piece.step").is_file()
    said = rows(run, "bench_calls")
    assert said == [], "`ask` isn't a bench call the model made"


def test_a_picks_close_up_that_cant_be_read_doesnt_go_to_the_sponsor(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [write("make.py", script(gap=0.5), run=True)],
        [
            tool(
                "ask",
                question="Which?",
                close="500,500,0,4",
                variants=[{"name": "A", "files": ["block.step"]}],
            )
        ],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    said = reply_of(run, "ask")
    assert said["why"] == "no_sheet" and "nothing of the parts" in said["reply"]


# The sandbox, for real


def test_generated_code_has_no_network_no_key_and_its_own_user(tmp_path: Path) -> None:
    probe = (
        "import os, socket, json\n"
        "try:\n"
        "    socket.create_connection(('example.com', 443), timeout=3)\n"
        "    net = 'reached'\n"
        "except OSError as exc:\n"
        "    net = type(exc).__name__\n"
        "print('NET', net)\n"
        "print('ROUTES', len(open('/proc/net/route').read().splitlines()) - 1)\n"
        "print('KEY', [k for k in os.environ if 'CREW' in k or 'LITELLM' in k or 'API' in k])\n"
        "print('UID', os.getuid())\n"
        "try:\n"
        "    open('/opt/bench/bench/cli.py', 'a')\n"
        "    print('CODE writable')\n"
        "except OSError:\n"
        "    print('CODE read-only')\n"
    )
    (tmp_path / "probe.py").write_text(probe)
    done = Box(tmp_path).python("probe.py")
    assert "NET reached" not in done.output
    assert "ROUTES 0" in done.output
    assert "KEY []" in done.output
    assert "UID 0" not in done.output
    assert "CODE read-only" in done.output


def test_a_link_made_in_a_container_gets_no_host_file_out(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("the harness's own")
    run, _ = begin(
        tmp_path / "root",
        [
            write(
                "plant.py",
                "import os\n"
                f"os.symlink({str(tmp_path / 'secret.txt')!r}, 'linked.txt')\n"
                "os.symlink('/', 'up')\n"
                "os.mkfifo('pipe.txt')\n",
                run=True,
            )
        ],
        [tool("read", path="linked.txt")],
        [write("linked.txt", "overwritten")],
        [tool("edit", path="linked.txt", old="whatever is there", new="x")],
        [tool("read", path="up/etc/passwd")],
        [tool("read", path="pipe.txt")],
        [tool("blocked", reason="done looking")],
    )
    assert run.drive().name == "blocked"
    found = replies(run)
    assert [e["why"] for e in found[1:6]] == [
        "outside",
        "outside",
        "edit.no_file",
        "outside",
        "no_file",
    ]
    assert (tmp_path / "secret.txt").read_text() == "the harness's own"
    assert "the harness's own" not in run.session.events.read_text()


def test_nothing_a_run_starts_outlives_it(tmp_path: Path) -> None:
    (tmp_path / "daemon.py").write_text(
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    while True:\n"
        "        open('alive', 'a').write('x')\n"
        "        time.sleep(0.05)\n"
    )
    assert Box(tmp_path).python("daemon.py").code == 0
    first = (tmp_path / "alive").stat().st_size if (tmp_path / "alive").exists() else 0
    time.sleep(1)
    last = (tmp_path / "alive").stat().st_size if (tmp_path / "alive").exists() else 0
    assert first == last


def test_a_script_that_prints_without_end_costs_only_its_tail(tmp_path: Path) -> None:
    (tmp_path / "loud.py").write_text(
        "import sys\nfor _ in range(4000):\n    sys.stdout.write('x' * 1000 + chr(10))\n"
        "print('END')\n"
    )
    done = Box(tmp_path).python("loud.py")
    assert done.code == 0
    assert done.dropped > 1_000_000
    assert done.output.rstrip().endswith("END")
    assert len(done.output) < 400_000


def test_a_run_past_its_time_is_stopped_and_says_so(tmp_path: Path) -> None:
    (tmp_path / "slow.py").write_text("import time\nprint('got this far')\ntime.sleep(60)\n")
    began = time.monotonic()
    done = Box(tmp_path, timeout=3).python("slow.py")
    assert time.monotonic() - began < 30
    assert done.timed_out
    assert "past the 3 s limit" in done.output
    assert "got this far" in done.output, (
        "what it printed before it was stopped says where it got to"
    )


def test_needs_can_be_tried_before_theyre_settled(tmp_path: Path) -> None:
    wrong_name = {
        **SIZE_NEED,
        "check": {"by": "bench", "command": "info block.step", "number": "nope", "at_most": 1},
    }
    run, _ = begin(
        tmp_path,
        [write("make.py", script(gap=0.5), run=True)],
        [tool("needs", needs=[wrong_name, GAP_NEED, EYE_NEED], **{"try": "true"})],
        [tool("needs", needs=[SIZE_NEED], **{"try": True})],
        [tool("needs", needs=[SIZE_NEED])],
        [tool("blocked", reason="enough")],
    )
    assert run.drive().name == "blocked"
    tried = [e for e in replies(run) if e["name"] == "needs"]
    assert "Nothing is settled" in tried[0]["reply"]
    assert "volume, WOULDN'T PASS: this answer has no number `nope`" in tried[0]["reply"]
    assert "gap, would pass" in tried[0]["reply"]
    assert "look: for the Sponsor's eye" in tried[0]["reply"]
    assert "volume, would pass" in tried[1]["reply"]
    assert "settled" in tried[2]["reply"] and "Nothing is" not in tried[2]["reply"]
    assert run.state.needs_version == 1, "a try settles nothing"
    assert not list(run.folder.glob(".try-*")), "and leaves nothing"
    assert not (run.work / "bench-out").exists(), "and touches none of the model's files"


# Every ending, by name


def endings(run: Run) -> str:
    ended = [e for e in transcript.events_of(run.session.events) if e["kind"] == "ended"]
    state = json.loads((run.folder / "state.json").read_text())
    assert state["ending"]["name"] == ended[-1]["name"]
    assert rows(run, "runs")[0]["ending"] == ended[-1]["name"]
    return str(ended[-1]["name"])


EYE_ONLY = [tool("needs", needs=[EYE_NEED])]
A_SCRIPT = [write("s.py", "print('hi')\n")]
DONE_S = tool("done", script="s.py", part=["s.step"])


@pytest.mark.parametrize(
    ("limits", "turns", "name"),
    [
        ({}, [[tool("blocked", reason="no such material")]], "blocked"),
        ({"quiet": 2}, ["thinking aloud", "still"], "no_tool_call"),
        ({"calls": 2}, [A_SCRIPT, A_SCRIPT, A_SCRIPT], "call_cap"),
        ({"model_seconds": 0}, [], "model_time"),
        (
            {"rounds": 2},
            [EYE_ONLY, A_SCRIPT, [DONE_S], [write("s.py", "print(1)\n")], [DONE_S]],
            "round_cap",
        ),
        (
            {"stuck_rounds": 2},
            [EYE_ONLY, A_SCRIPT] + [[DONE_S]] * 3,
            "stuck",
        ),
        (
            {"work_gb": 0.0000001},
            [[write("s.py", "open('x','w').write('y'*999)\n", run=True)]],
            "work_full",
        ),
    ],
)
def test_a_limit_or_the_models_own_word_ends_by_name(
    tmp_path: Path, limits: dict[str, Any], turns: list[Any], name: str
) -> None:
    run, _ = begin(tmp_path, *turns, limits=Limits(**limits))
    assert run.drive().name == name
    assert endings(run) == name


def test_a_stuck_run_says_how_many_rounds_changed_nothing(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        EYE_ONLY,
        A_SCRIPT,
        *[[DONE_S]] * 3,
        limits=Limits(stuck_rounds=2),
    )
    ending = run.drive()
    assert ending.said == {"rounds": 2, "limit": 2}


def test_the_disk_floor_ends_it_before_a_call_and_between_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB - 0.1)
    run, model = begin(tmp_path / "a", [tool("blocked", reason="never")])
    assert run.drive().name == "disk_floor"
    assert model.bodies == []
    assert endings(run) == "disk_floor"

    levels = [host.FLOOR_GB + 1, host.FLOOR_GB - 0.1]
    monkeypatch.setattr(host, "free_gb", lambda: levels.pop(0))
    run, _ = begin(tmp_path / "b", EYE_ONLY, A_SCRIPT, [DONE_S])
    assert run.drive().name == "disk_floor"


def test_a_stop_file_and_a_signal_both_end_it_and_it_goes_on_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = begin(tmp_path, [write("a.txt", "x")], [tool("blocked", reason="after")])
    (run.folder / "stop").touch()
    assert run.drive().name == "stopped"
    assert endings(run) == "stopped"
    (run.folder / "stop").unlink()

    run, model = again(run, [write("a.txt", "y")])

    def interrupted(_: dict[str, Any]) -> dict[str, Any]:
        raise KeyboardInterrupt

    run.session.send = interrupted
    run.resume()
    assert run.drive().name == "stopped"
    run, model = again(run, [tool("blocked", reason="after all")])
    run.resume()
    assert run.drive().name == "blocked"
    assert model.bodies[0]["messages"][0]["role"] == "system"


def test_a_failed_call_ends_named_and_resumes_from_where_it_was(tmp_path: Path) -> None:
    run, model = begin(tmp_path, A_SCRIPT, limits=Limits(call_retries=0))
    model.turns.append([tool("blocked", reason="x")])
    model.failures = []

    real = model.__call__
    state = {"n": 0}

    def fails_second(body: dict[str, Any]) -> dict[str, Any]:
        state["n"] += 1
        if state["n"] == 2:
            raise ValueError("a stream that made no sense")
        return real(body)

    run.session.send = fails_second
    ending = run.drive()
    assert (ending.name, ending.said["error"]) == ("call_failed", "ValueError")
    run, model = again(run, [tool("blocked", reason="x")])
    run.resume()
    assert run.drive().name == "blocked"
    assert any(m.get("tool_calls") for m in model.bodies[0]["messages"])


def test_docker_gone_is_the_infrastructures_ending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = begin(tmp_path, [write("a.py", "x = 1\n", run=True)])

    def gone(*_: Any, **__: Any) -> Any:
        raise Gone("docker couldn't run the container")

    monkeypatch.setattr(run.box, "python", gone)
    assert run.drive().name == "container_failed"
    assert endings(run) == "container_failed"


def test_a_fault_in_the_harness_ends_named_with_its_trace_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _ = begin(tmp_path, [tool("blocked", reason="x")])

    def broken(_: Any) -> Any:
        raise TypeError("a bug of ours")

    monkeypatch.setattr(run, "blocked", broken)
    assert run.drive().name == "harness_fault"
    assert endings(run) == "harness_fault"
    assert "a bug of ours" in (run.folder / "harness-fault.txt").read_text()


def test_a_conversation_that_wont_fit_goes_on_from_the_record(tmp_path: Path) -> None:
    run, model = begin(
        tmp_path,
        [tool("needs", needs=[EYE_NEED])],
        [tool("blocked", reason="after the new conversation")],
        limits=Limits(compact_at=500),
        prompt_tokens=10_000,
    )
    assert run.drive().name == "blocked"
    assert run.state.restarts >= 1
    last = model.bodies[-1]["messages"]
    assert len(last) == 2, "a new conversation"
    assert "An earlier conversation about this ran out of room" in last[1]["content"]
    assert REQUEST in last[1]["content"]
    assert "the needs, as settled" in last[1]["content"].lower()
    for n in range(1, run.session.requests + 1):
        assert transcript.rebuild(run.session.events, n)["messages"][0]["role"] == "system"


def test_a_conversation_that_never_fits_ends_when_its_restarts_run_out(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        A_SCRIPT,
        A_SCRIPT,
        limits=Limits(compact_at=500, restarts=1),
        prompt_tokens=10_000,
    )
    assert run.drive().name == "context_full"
    assert endings(run) == "context_full"


def test_a_pick_of_none_leaves_the_need_open_and_says_why(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        EYE_ONLY,
        [write("make.py", script(gap=0.5), run=True)],
        [
            tool(
                "ask",
                question="Does this work?",
                need="look",
                views="iso",
                variants=[{"name": "A", "files": ["block.step"]}],
            )
        ],
    )
    assert run.drive().name == "ask"
    (run.folder / "asks" / "1" / "pick.json").write_text(
        '{"pick": "none", "reason": "Too plain.", "by": "sponsor"}'
    )
    run, _ = again(
        run, [tool("done", script="make.py", part=["block.step"])], [tool("blocked", reason="stop")]
    )
    run.resume()
    assert (
        "didn't pick any of them. Their reason: Too plain." in run.session.messages[-1]["content"]
    )
    assert run.drive().name == "blocked"
    assert "look, NOT MET" in reply_of(run, "done")["reply"]
    assert rows(run, "picks")[0]["pick"] == "none"


def test_needs_reopen_only_when_a_person_says_so(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        EYE_ONLY,
        [tool("questions", questions="Can I change the needs?")],
        [tool("needs", needs=[SIZE_NEED])],
        [tool("blocked", reason="the need's check is wrong")],
    )
    assert run.drive().name == "blocked"
    calls = {e["name"]: e for e in replies(run)}
    assert calls["questions"]["why"] == "needs_settled"
    assert calls["needs"]["why"] == "needs_settled"

    run, _ = again(run, [tool("needs", needs=[SIZE_NEED])], [tool("blocked", reason="fine")])
    run.resume("It's fine to change them.", reopen_needs=True)
    assert run.drive().name == "blocked"
    assert reply_of(run, "needs")["outcome"] == "ok"
    assert run.state.needs_version == 2
    assert {r["version"] for r in rows(run, "needs")} == {1, 2}
    assert run.state.round == 1, "a change of needs isn't a new round"


# Starting


def test_a_start_is_refused_in_the_repository_or_over_a_run(tmp_path: Path) -> None:
    model = Scripted()
    with pytest.raises(Refused, match="public"):
        Run.start(host.REPOSITORY / "x", "proof", REQUEST, [], model)
    with pytest.raises(Refused, match="name"):
        Run.start(tmp_path, "../up", REQUEST, [], model)
    with pytest.raises(Refused, match="empty"):
        Run.start(tmp_path, "proof", "  ", [], model)
    with pytest.raises(Refused, match="no reference"):
        Run.start(tmp_path, "proof", REQUEST, [tmp_path / "none"], model)
    Run.start(tmp_path, "proof", REQUEST, [], model)
    with pytest.raises(Refused, match="exists"):
        Run.start(tmp_path, "proof", REQUEST, [], model)


def test_references_are_copied_and_named_at_the_start(tmp_path: Path) -> None:
    given = tmp_path / "given"
    given.mkdir()
    (given / "drawing.txt").write_text("a drawing")
    model = Scripted([tool("blocked", reason="x")])
    run = Run.start(tmp_path / "root", "proof", REQUEST, [given / "drawing.txt"], model)
    run.drive()
    first = model.bodies[0]["messages"][1]["content"]
    assert "`references/drawing.txt` (9 bytes)" in first
    assert REQUEST in first
    assert "`bench`," in first and "`cadquery`," in first
    assert (run.work / "references" / "drawing.txt").read_text() == "a drawing"


def test_only_one_session_runs_under_a_root(tmp_path: Path) -> None:
    with (
        only_session(tmp_path),
        pytest.raises(Refused, match="another session"),
        only_session(tmp_path),
    ):
        pass
