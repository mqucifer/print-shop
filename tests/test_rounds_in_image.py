"""A scripted fake model drives a real run, in real containers, with no model call.

Every ending, every tool, a resume after `ask`, a failing need going back, and a `done` the checks
refuse not ending the run. The part is one made up here, of plain boxes. Needs the image, from
`scripts/bench.py build`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from print_shop.bench import host
from print_shop.rounds import records, send, transcript
from print_shop.rounds.box import Box, Gone
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Refused, Run, only_session, summarise, what_next
from print_shop.rounds.session import Ending, Limits
from tests.rounds_fakes import Restating, Scripted, Spoken, raw, stream_of, tool

pytestmark = pytest.mark.bench

REQUEST = (
    "A small block with a tab beside it. Keep a 0.5 mm gap between them, and it should look good."
    "\n\n## Stops\n- no plan\n"
)
#: How it's built, before the needs (mqucifer/print-shop#36). The stops are `test_intake.py`'s.
BROKEN_DOWN = tool(
    "breakdown",
    parts=[{"name": "block", "what": "a box"}, {"name": "tab", "what": "a smaller box"}],
    joins=[{"parts": ["block", "tab"], "how": "apart, by the gap"}],
    look=["two boxes side by side"],
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


GAP_NEED: dict[str, Any] = {
    "id": "gap",
    "what": "a 0.5 mm gap between block and tab",
    "source": "request",
    "quote": "a 0.5 mm gap",
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance block.step tab.step --min-gap 0.2",
        "number": "pairs.0.gap_mesh_mm",
        "at_least": 0.45,
        "at_most": 0.55,
    },
}
SIZE_NEED: dict[str, Any] = {
    "id": "volume",
    "what": "the block is the size chosen",
    "source": "chosen",
    "check": {
        "by": "bench",
        "measures": "size",
        "command": "info block.step",
        "number": "volume_mesh_mm3",
        "at_most": 3000,
    },
}


def sized(name: str) -> dict[str, Any]:
    """A need that reads `name`, as `done` asks every file of the part to be read by one
    (mqucifer/print-shop#52), and passes on any part these tests make."""
    return {
        **SIZE_NEED,
        "id": f"volume-{Path(name).stem}",
        "what": f"{name} is the size chosen",
        "check": {**SIZE_NEED["check"], "command": f"info {name}"},
    }


PARAM_NEED = {
    "id": "gap-drives",
    "what": "the gap's a parameter that moves the tab",
    "source": "derived",
    "check": {
        "by": "bench",
        "measures": "parameter",
        "command": "params make.py --set GAP=3.0 --max-left 1",
    },
}
EYE_NEED = {
    "id": "look",
    "what": "it looks good",
    "source": "request",
    "quote": "it should look good",
    "check": {"by": "eye", "look_for": "whether the proportions please"},
}


#: `done` held for descriptions written before the part changed (mqucifer/print-shop#53): the
#: model here says each still holds, and sends `done` again (`Restating`).
STALE = "descriptions_stale"
#: After `ask` draws its sheet, the model says what it shows, and only then does it go.
SENT = [tool("send_ask", says="The block, in the colour the legend gives it.")]


def begin(
    tmp_path: Path, *turns: Any, limits: Limits | None = None, **kw: Any
) -> tuple[Run, Scripted]:
    model = Restating(*turns, **kw)
    run = Run.start(tmp_path, "proof", REQUEST, [], model, limits, sleep=lambda _: None)
    return run, model


def begin_stained_glass(tmp_path: Path, *turns: Any) -> tuple[Run, Scripted]:
    """A run whose request names stained glass, as `## Kind` will, with its toolset."""
    run, model = begin(tmp_path, *turns)
    run.state.kind = "stained-glass"
    run.tools.use(run.toolset)
    run.save()
    return run, model


def again(run: Run, *turns: Any, limits: Limits | None = None) -> tuple[Run, Scripted]:
    model = Restating(*turns)
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


def done(script: str = "make.py", part: Any = ("block.step",), **more: Any) -> dict[str, Any]:
    """`done`, with its words: what kind of change it was, and what it says at the end."""
    words = {"fix_shape": "a first try", "closing": "The block is here.", **more}
    return tool("done", script=script, part=list(part), **words)


# The whole way, with every tool


def test_a_run_goes_from_intake_through_refusals_and_a_pick_to_its_needs_met(
    tmp_path: Path,
) -> None:
    run, model = begin(
        tmp_path,
        [tool("lookup", query="Solid.fuse"), tool("lookup", query="Workplane.box", page=1)],
        [tool("docs", query="fillet edges"), tool("skill", name="using-the-bench")],
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
    # `using-the-bench` is the core's, whole in the start, and the reply says where
    # (mqucifer/print-shop#61).
    assert '`<skill name="using-the-bench">`' in found["skill"]["reply"]
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
    bad_command = {**SIZE_NEED, "check": {"by": "bench", "measures": "size", "command": "info"}}
    need_set = [GAP_NEED, SIZE_NEED, PARAM_NEED, EYE_NEED]
    run, model = again(
        run,
        [done()],
        [BROKEN_DOWN, tool("needs", needs=[bad_quote])],
        [BROKEN_DOWN, tool("needs", needs=[bad_command])],
        [BROKEN_DOWN, tool("needs", needs=need_set)],
        [BROKEN_DOWN, tool("needs", needs=need_set)],
        # A name on the wrong class: the hint says where it is.
        [write("oops.py", 'import cadquery as cq\ncq.Workplane("XY").isValid()\n', run=True)],
        # The first try: the gap's wrong.
        [write("make.py", script(gap=2.0), run="true")],
        [tool("read", path="make.py")],
        [tool("bench", command="info block.step")],
        [tool("bench", command="render block.step tab.step --views iso")],
        [tool("run", path="make.py")],
        # The checks refuse this `done`: the run doesn't end.
        [done(part=["block.step", "tab.step"])],
        [tool("edit", path="make.py", old="GAP = 2.0", new="GAP = 0.5", run=True)],
        [tool("bench", command="clearance block.step tab.step --min-gap 0.2")],
        # The bench now passes, and the eye need is still open.
        [done(part=["block.step", "tab.step"], fix_shape="narrowed the gap")],
        [
            write(
                "b.py",
                script(gap=0.5, size=25.0)
                .replace("block.step", "wide.step")
                .replace("tab.step", "wide-tab.step"),
                run=True,
            )
        ],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["nope"],
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
                need=["look"],
                variants=[
                    {"name": "A", "files": ["block.step", "tab.step"], "about": "the even one"},
                    {"name": "B", "files": ["wide.step", "wide-tab.step"], "about": "the wide one"},
                ],
            ),
            tool("bench", command="info block.step"),
        ],
        SENT,
    )
    run.resume(note_from="sponsor")
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

    calls = [e for e in replies(run) if e["name"] == "done" and e["why"] != STALE]
    assert calls[0]["why"] == "no_needs"
    assert any(e["why"] == STALE for e in replies(run)), "the gap changed after the first part"
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

    run, model = again(run, [done(part=["block.step", "tab.step"])])
    run.resume()
    assert run.session.messages[-1]["content"] == (
        "The Sponsor picked A. Their reason: The even one sits better."
    )
    ending = run.drive()
    assert ending.name == "met", ending
    assert (run.folder / "final-sheet.png").is_file()
    with pytest.raises(Refused, match="waiting for approval"):
        run.resume()
    assert rows(run, "runs")[0]["ending"] == "met"
    run, _ = again(run)
    assert run.approve("sponsor").name == "approved"
    with pytest.raises(Refused, match="approved"):
        run.resume()

    # The ledger holds it, written by code.
    runs = rows(run, "runs")[0]
    assert runs["ending"] == "approved"
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
        "block.step",
        "tab.step",
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
    assert figures["ending"]["name"] == "approved"
    assert figures["requests_rebuilt"] == figures["requests_recorded"] == run.session.requests
    assert figures["by_tool_and_outcome"]["done ok"] == 3
    assert json.loads((run.folder / "summary.json").read_text())["rounds"] == 3
    assert (run.folder / "rounds" / "003" / "judged" / "block.step").is_file()


# The harness's own check


def test_a_part_left_over_from_an_earlier_script_doesnt_count(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [write("noop.py", "print('nothing to see')\n")],
        [done("noop.py")],
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
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("bad.py", "raise SystemExit('it broke')\n")],
        [done("bad.py")],
        [tool("blocked", reason="stop")],
    )
    run.drive()
    said = reply_of(run, "done")["reply"]
    assert "exit 1" in said and "it broke" in said
    assert "didn't finish from a clean start" in said


def test_nothing_a_run_plants_can_stand_in_for_the_bench(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        # Modules that would shadow the bench's own, and a forged answer in its folder.
        [write("trimesh.py", "raise SystemExit('forged')\n")],
        [write("bench-out/forged.json", '{"verdict": "OK", "numbers": {"volume_mesh_mm3": 1}}')],
        [tool("bench", command="info block.step")],
        [done()],
    )
    assert run.drive().name == "met"
    assert reply_of(run, "bench")["reply"].startswith("exit 0\nverdict: OK")
    assert rows(run, "readings")[0]["value"] == pytest.approx(2000.0, abs=1)


# Who picked, and what a sheet draws


def asked_a_pick(tmp_path: Path, **ask: Any) -> Run:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, EYE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["look"],
                views="iso",
                variants=[{"name": "A", "files": ["block.step"], "about": "the even one"}],
                **ask,
            )
        ],
        SENT,
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
    run, _ = again(run, [done()])
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
    run, _ = again(run, [done()])
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
    "check": {"by": "bench", "measures": "fit", "command": "clearance block.step test-piece.step"},
}


def final_render(run: Run) -> dict[str, Any]:
    made = run.folder / "rounds" / "001" / "judged" / "bench-out"
    found = [json.loads(p.read_text()) for p in made.glob("render-*.json")]
    assert len(found) == 1
    return dict(found[0])


GIVEN_SCRIPT = (
    "import cadquery as cq\n"
    'piece = cq.Workplane("XY").box(4, 4, 4).translate((0, 60, 0))\n'
    'cq.exporters.export(piece, "references/test-piece.step")\n'
)
GIVEN_NEED = {
    "id": "apart",
    "what": "the block stands clear of the piece it's given",
    "source": "chosen",
    "check": {
        "by": "bench",
        "measures": "fit",
        "command": "clearance block.step references/test-piece.step",
    },
}


def test_the_final_sheet_draws_the_part_and_leaves_out_what_only_a_need_names(
    tmp_path: Path,
) -> None:
    """The first real run's final sheet drew its two test cylinders inside the stand. A need is
    checked on the part or the given references (mqucifer/print-shop#45), so what only a need
    names is a reference here, made in a container as it would be given."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[GIVEN_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [done()],
    )
    (run.work / "given.py").write_text(GIVEN_SCRIPT)
    assert run.box.python("given.py").code == 0
    (run.work / "given.py").unlink()
    assert (run.work / "references" / "test-piece.step").is_file()
    assert run.drive().name == "met"
    assert (run.folder / "final-sheet.png").is_file()
    drawn = final_render(run)
    assert "block=block.step" in drawn["argv"]
    assert not any("test-piece" in word for word in drawn["argv"])
    # 20 x 20 mm, with the tab's file and the piece left out: their outline would be wider.
    assert drawn["numbers"]["views"]["block top"]["projected_mm"] == [20.0, 20.0]
    assert json.loads(rows(run, "rounds")[0]["part"]) == ["block.step"]


def test_done_with_no_part_is_told_and_costs_only_that_call(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [tool("done", script="make.py", fix_shape="x", closing="y")],
        [done(part=[])],
        [done(part=["make.py"])],
        [done(part=["../outside.step"])],
        [done()],
    )
    assert run.drive().name == "met"
    calls = [e for e in replies(run) if e["name"] == "done"]
    assert [e["why"] for e in calls[:4]] == ["missing", "no_part", "not_geometry", "outside"]
    assert run.state.round == 1, "none of those was a round"


def test_a_part_a_clean_run_doesnt_leave_isnt_met(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, sized("stand.step")])],
        [write("make.py", script(gap=0.5), run=True)],
        [done(part=["block.step", "stand.step"])],
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
        [BROKEN_DOWN, tool("needs", needs=[FIXTURE_NEED, EYE_NEED])],
        [write("make.py", FIXTURE_SCRIPT, run=True)],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["look"],
                views="top",
                close="-5,0,2.5,4",
                test_pieces=["test-piece.step"],
                variants=[{"name": "A", "files": ["block.step"], "about": "the even one"}],
            )
        ],
        SENT,
    )
    assert run.drive().name == "ask"
    folder = run.folder / "asks" / "1"
    head = (folder / "sheet.png").read_bytes()[16:24]
    width, height = int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")
    assert (width, height) == (1024, 512 + legend_px(1, 1024)), "the view and the close-up"
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
        "check": {
            "by": "bench",
            "measures": "size",
            "command": "info block.step",
            "number": "nope",
            "at_most": 1,
        },
    }
    run, _ = begin(
        tmp_path,
        [write("make.py", script(gap=0.5), run=True)],
        [tool("needs", needs=[wrong_name, GAP_NEED, EYE_NEED], **{"try": "true"})],
        [tool("needs", needs=[SIZE_NEED], **{"try": True})],
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
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


#: The eye need, and one that reads `s.step`, the part `DONE_S` names: a clean run of `s.py`
#: doesn't leave it, so the round isn't met.
EYE_ONLY = [BROKEN_DOWN, tool("needs", needs=[EYE_NEED, sized("s.step")])]
A_SCRIPT = [write("s.py", "print('hi')\n")]
DONE_S = done("s.py", part=["s.step"])


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
        [BROKEN_DOWN, tool("needs", needs=[EYE_NEED])],
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
    # It's one process going on, under one commit, and its stop and restart both say so.
    line = rows(run, "timeline")
    assert {"stop", "restart"} <= {r["kind"] for r in line}
    assert "context_full" in [r["name"] for r in line]
    assert {r["segment"] for r in line} == {1}
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
        [BROKEN_DOWN, tool("needs", needs=[EYE_NEED, SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [
            tool(
                "ask",
                question="Does this work?",
                need=["look"],
                views="iso",
                variants=[{"name": "A", "files": ["block.step"]}],
            )
        ],
        SENT,
    )
    assert run.drive().name == "ask"
    (run.folder / "asks" / "1" / "pick.json").write_text(
        '{"pick": "none", "reason": "Too plain.", "by": "sponsor"}'
    )
    run, _ = again(run, [done()], [tool("blocked", reason="stop")])
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
        [BROKEN_DOWN, tool("needs", needs=[EYE_NEED])],
        [tool("questions", questions="Can I change the needs?")],
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
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
    # The core's required skills, whole (mqucifer/print-shop#61).
    assert '<skill name="using-the-bench">' in first and '<skill name="cadquery">' in first
    assert "This run's toolset is the core" in first
    assert (run.work / "references" / "drawing.txt").read_text() == "a drawing"


def test_only_one_session_runs_under_a_root(tmp_path: Path) -> None:
    with (
        only_session(tmp_path),
        pytest.raises(Refused, match="another session"),
        only_session(tmp_path),
    ):
        pass


# The records: what caused a run's changes, and what they cost


def asked_with_needs(tmp_path: Path, *need_list: dict[str, Any], **ask: Any) -> Run:
    run, _ = begin(
        tmp_path,
        [write("make.py", script(gap=0.5), run=True)],
        [BROKEN_DOWN, tool("needs", needs=list(need_list))],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["look"],
                views="iso",
                variants=[{"name": "A", "files": ["block.step"], "about": "the even one"}],
                **ask,
            )
        ],
        SENT,
    )
    assert run.drive().name == "ask"
    return run


def picked(run: Run, n: int = 1, by: str = "sponsor") -> None:
    (run.folder / "asks" / str(n) / "pick.json").write_text(
        json.dumps({"pick": "A", "reason": "It sits better.", "by": by})
    )


def test_every_note_and_answer_relayed_is_a_row_saying_who_when_and_at_which_call(
    tmp_path: Path,
) -> None:
    run, _ = begin(tmp_path, [tool("questions", questions="How big?")])
    assert run.drive().name == "questions"
    (run.folder / "questions" / "1.answers.md").write_text("20 mm across.\n")
    run, _ = again(
        run,
        [write("make.py", script(gap=0.5), run=True)],
        [BROKEN_DOWN, tool("needs", needs=[EYE_NEED])],
        [
            tool(
                "ask",
                question="Which?",
                need=["look"],
                views="iso",
                variants=[{"name": "A", "files": ["block.step"]}],
            )
        ],
        SENT,
    )
    run.resume(note_from="stand-in: a builder in a test")
    assert run.session.messages[-1]["content"].startswith(
        "Answers from a stand-in for the Sponsor (a builder in a test):"
    )
    assert run.drive().name == "ask"
    picked(run)
    run, _ = again(run, [tool("blocked", reason="stop")])
    run.resume("Try a rounder corner.", note_from="the operator")
    assert run.session.messages[-1]["content"].startswith("A note from the operator:")
    assert run.drive().name == "blocked"
    run, _ = again(run, [tool("blocked", reason="stop again")])
    run.resume("And a little taller.", reopen_needs=True)
    assert run.drive().name == "blocked"

    found = sorted(rows(run, "interventions"), key=lambda r: r["seq"])
    said = [(r["kind"], r["from_who"], r["text"]) for r in found]
    assert said == [
        ("answers", "stand-in: a builder in a test", "20 mm across."),
        ("pick", "sponsor", "A: It sits better."),
        ("note", "the operator", "Try a rounder corner."),
        ("reopen_needs", "the people running this", said[3][2]),
        ("note", "the people running this", "And a little taller."),
    ]
    assert all(r["at"] for r in found)
    # The answer reached call 2, and what arrived together is one phase. `ask` and `send_ask`
    # are two calls.
    assert [r["at_call"] for r in found] == [2, 6, 6, 7, 7]
    assert [r["phase"] for r in found] == [1, 2, 2, 3, 3]
    assert found[1]["round"] == 1
    # Each went to the model as the row says.
    bodies = "\n".join(
        str(m["content"])
        for m in transcript.conversation(run.session.events)
        if m["role"] == "user"
    )
    assert "Try a rounder corner." in bodies and "And a little taller." in bodies


def test_each_stop_resume_and_compaction_has_its_size_and_its_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("print_shop.rounds.run.repository_version", lambda: ("commit-one", False))
    run, _ = begin(
        tmp_path,
        # Reasoning is what compaction takes, so the second call leaves it something to take.
        Spoken([write("a.txt", "x")], reasoning="thinking " * 400, usage={"prompt_tokens": 100}),
        Spoken([write("b.txt", "y" * 400)], usage={"prompt_tokens": 1900}),
        Spoken([tool("questions", questions="Well?")], usage={"prompt_tokens": 800}),
        limits=Limits(compact_at=1950),
    )
    assert run.drive().name == "questions"
    (run.folder / "questions" / "1.answers.md").write_text("Fine.")
    monkeypatch.setattr("print_shop.rounds.run.repository_version", lambda: ("commit-two", False))
    run, _ = again(run, [tool("blocked", reason="stop")], limits=Limits())
    run.resume(note_from="sponsor")
    assert run.drive().name == "blocked"

    found = sorted(rows(run, "timeline"), key=lambda r: r["seq"])
    kinds = [r["kind"] for r in found]
    assert kinds[0] == "start"
    assert "compaction" in kinds
    squeezed = next(r for r in found if r["kind"] == "compaction")
    assert squeezed["estimated_before"] >= 1950
    assert squeezed["estimated_after"] < squeezed["estimated_before"]
    assert squeezed["prompt_before"] == 1900
    assert squeezed["prompt_after"] == 800, "the next call's, measured"
    assert squeezed["at_call"] == 3
    assert [r["name"] for r in found if r["kind"] == "stop"] == ["questions", "blocked"]
    assert [r["harness_commit"] for r in found if r["kind"] in ("start", "resume")] == [
        "commit-one",
        "commit-two",
    ]
    assert rows(run, "runs")[0]["harness_commit"] == "commit-one", "the first, as it always was"
    assert found[-1]["segment"] == 2


def test_tokens_and_seconds_split_by_what_a_person_sent_and_by_round(tmp_path: Path) -> None:
    run = asked_with_needs(tmp_path, EYE_NEED)
    picked(run)
    run, _ = again(run, [write("x.txt", "x")], [tool("blocked", reason="stop")])
    run.resume("Go on.")
    assert run.drive().name == "blocked"

    calls = rows(run, "calls")
    # `ask` and `send_ask` are two calls.
    assert [c["n"] for c in sorted(calls, key=lambda c: c["n"])] == [1, 2, 3, 4, 5, 6]
    phases = run.ledger.rows("SELECT * FROM phases WHERE run=? ORDER BY phase", run.state.name)
    assert [(p["first_call"], p["last_call"]) for p in phases] == [(1, 4), (5, 6)]
    assert phases[0]["begun_by"] is None
    assert phases[1]["begun_by"] == "pick from sponsor; note from the people running this"
    assert sum(p["completion_tokens"] for p in phases) == 60
    by_round = run.ledger.rows(
        "SELECT * FROM round_costs WHERE run=? ORDER BY round", run.state.name
    )
    assert [(r["round"], r["calls"]) for r in by_round] == [(0, 2), (1, 4)]
    assert by_round[0]["completion_tokens"] == 20


def test_a_try_reaches_the_ledger_with_its_reading(tmp_path: Path) -> None:
    wrong_name = {
        **SIZE_NEED,
        "check": {
            "by": "bench",
            "measures": "size",
            "command": "info block.step",
            "number": "nope",
            "at_most": 1,
        },
    }
    run, _ = begin(
        tmp_path,
        [write("make.py", script(gap=0.5), run=True)],
        [tool("needs", needs=[wrong_name, GAP_NEED, EYE_NEED], **{"try": True})],
        [tool("blocked", reason="enough")],
    )
    assert run.drive().name == "blocked"
    tried = sorted(rows(run, "tries"), key=lambda r: r["seq"])
    assert [(t["need"], t["passed"], t["at_call"]) for t in tried] == [
        ("volume", 0, 2),
        ("gap", 1, 2),
    ]
    assert "no number `nope`" in tried[0]["why"]
    assert tried[1]["value"] == pytest.approx(0.5, abs=1e-3)
    assert tried[1]["command"] == "clearance block.step tab.step --min-gap 0.2"
    assert json.loads(tried[1]["numbers"])["pairs"][0]["gap_mesh_mm"] == pytest.approx(
        0.5, abs=1e-3
    )
    assert rows(run, "needs") == [], "a try settles nothing"


def test_a_need_from_a_relayed_note_says_so_and_is_held_to_its_words(tmp_path: Path) -> None:
    """A quote from a note used to be refused, so a person's words were marked `derived`."""
    run, _ = begin(tmp_path, [tool("questions", questions="Anything else?")])
    assert run.drive().name == "questions"
    (run.folder / "questions" / "1.answers.md").write_text("No.")
    from_note = {
        "id": "open",
        "what": "it sits flat",
        "source": "note",
        "quote": "sit flat on a shelf",
        "check": {"by": "eye", "look_for": "how it sits"},
    }
    run, _ = again(
        run,
        [BROKEN_DOWN, tool("needs", needs=[{**from_note, "source": "answer"}])],
        [BROKEN_DOWN, tool("needs", needs=[{**from_note, "quote": "words nobody sent"}])],
        [BROKEN_DOWN, tool("needs", needs=[from_note])],
        [tool("blocked", reason="stop")],
    )
    run.resume("It has to sit flat on a shelf, whatever else.", note_from="sponsor")
    assert run.drive().name == "blocked"
    said = [e for e in replies(run) if e["name"] == "needs"]
    assert "isn't in the request or the answers, but is in a note" in said[0]["reply"]
    assert "isn't in a note you were sent" in said[1]["reply"]
    assert said[2]["outcome"] == "ok"
    need = rows(run, "needs")[0]
    assert (need["source"], need["quote"]) == ("note", "sit flat on a shelf")


def test_a_conversation_from_the_record_keeps_the_notes(tmp_path: Path) -> None:
    run, _ = begin(tmp_path, [tool("blocked", reason="stop")])
    assert run.drive().name == "blocked"
    run, model = again(
        run,
        [tool("blocked", reason="after the new conversation")],
        limits=Limits(compact_at=500),
    )
    model.prompt_tokens = 10_000
    run.resume("Make the corners rounder.")
    run.session.last_prompt = 10_000
    assert run.drive().name == "blocked"
    assert "Make the corners rounder." in model.bodies[-1]["messages"][1]["content"]
    assert [r["kind"] for r in rows(run, "timeline")].count("restart") >= 1


def test_done_asks_for_the_fixs_shape_and_its_closing_words(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [tool("done", script="make.py", part=["block.step"], closing="Here it is.")],
        [tool("done", script="make.py", part=["block.step"], fix_shape="first", closing="  ")],
        [done(fix_shape="a plain block", closing="It's a plain block.")],
    )
    assert run.drive().name == "met"
    calls = [e for e in replies(run) if e["name"] == "done"]
    assert [e["why"] for e in calls[:2]] == ["missing", "empty"]
    assert "`fix_shape`" in calls[0]["reply"]
    assert run.state.round == 1, "neither of those was a round"
    row = rows(run, "rounds")[0]
    assert row["fix_shape_model_said"] == "a plain block"
    assert row["closing_model_said"] == "It's a plain block."


def test_the_same_part_written_again_doesnt_make_a_description_stale(tmp_path: Path) -> None:
    """CadQuery's STEP header names the second it was written, so running the same script again
    changes the file's bytes and not what it holds (mqucifer/print-shop#53)."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    before = (run.work / "block.step").read_bytes()
    time.sleep(1.1)  # so the header's second differs
    run, model = again(run, [tool("run", path="make.py")], [done()])
    run.resume()
    assert run.drive().name == "met"
    assert (run.work / "block.step").read_bytes() != before, "written again, a second later"
    assert [e["outcome"] for e in replies(run) if e["name"] == "done"] == ["ok"]
    assert run.state.geometry["block.step"][1] == 2, "seen as it is since the first run"
    assert run.state.described["breakdown"]["stamped_at"] == 2, "its first version"
    assert not rows(run, "stale_descriptions")


# Runaways are evidence, and nothing here cuts a call


CHATTER = " ".join(f"This is a sentence of its own about the block, number {i}." for i in range(80))


def test_a_call_whose_reasoning_repeats_gets_a_row_and_goes_on_uncut(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [tool("skill", name="using-the-bench")],
        Spoken([write("a.txt", "x")], reasoning=CHATTER),
        Spoken([write("b.txt", "y")], reasoning=CHATTER),
        [tool("run", path="nope.py")],
        Spoken([write("c.txt", "z")], reasoning=f"{CHATTER} {CHATTER}"),
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    found = {r["n"]: r for r in rows(run, "calls")}
    assert found[2]["repeat_share"] == 0.0, "said for the first time"
    assert found[3]["repeat_share"] == 1.0
    assert (run.work / "c.txt").read_text() == "z", "the calls all went on"
    flagged = sorted(rows(run, "runaways"), key=lambda r: r["n"])
    assert [(r["n"], r["why"]) for r in flagged] == [(3, "repeats"), (5, "repeats")]
    assert flagged[0]["repeat_limit"] == 0.5
    doing = json.loads(flagged[1]["doing"])
    assert doing["tool_calls"][0]["name"] == "write"
    assert doing["tool_calls"][0]["path"] == "c.txt"
    lacked = json.loads(flagged[1]["lacked"])
    assert lacked["skills_read"] == ["using-the-bench"]
    # Given whole in the start, so neither is unread (mqucifer/print-shop#61).
    assert lacked["skills_given"] == ["cadquery", "using-the-bench"]
    assert "cadquery" not in lacked["skills_not_read"]
    assert "cadquery-errors" in lacked["skills_not_read"]
    assert lacked["last_tool_calls_refused"] == [["run", "no_file"]]
    assert flagged[1]["review_note"] is None, "that's for a reviewer"


def test_a_call_at_the_limit_gets_a_row_saying_what_it_was_doing(tmp_path: Path) -> None:
    cut = {
        "id": "c",
        "type": "function",
        "function": {"name": "write", "arguments": '{"path": "draft.py", "content": "import'},
    }
    run, model = begin(
        tmp_path,
        Spoken(
            "",
            reasoning="thinking",
            finish="length",
            usage={"completion_tokens": 65536, "reasoning_tokens": 2559},
        ),
        [tool("blocked", reason="stop")],
    )
    real = model.__call__

    def with_a_cut_call(body: dict[str, Any]) -> dict[str, Any]:
        answer = real(body)
        if answer["choices"][0]["finish_reason"] == "length":
            answer["choices"][0]["message"]["tool_calls"] = [cut]
        return answer

    run.session.send = with_a_cut_call
    assert run.drive().name == "blocked"
    row = rows(run, "runaways")[0]
    assert (row["n"], row["why"], row["finish"]) == (1, "limit", "length")
    # The limit less its reasoning and the 40-odd characters that came: the rest arrived nowhere.
    assert row["tokens_unaccounted"] > 62_900
    doing = json.loads(row["doing"])
    assert doing["tool_calls"][0]["path"] == "draft.py"
    assert doing["tool_calls"][0]["arguments_begin"].endswith('"content": "import')
    assert reply_of(run, "write")["outcome"] == "malformed"


def streamed_turn(*parts: dict[str, Any], finish: str, usage: dict[str, Any]) -> bytes:
    chunks: list[dict[str, Any]] = [
        {"id": "r", "choices": [{"delta": {"reasoning_content": "Now the block."}}]},
        *[{"choices": [{"delta": {"tool_calls": [part]}}]} for part in parts],
        {"choices": [{"delta": {}, "finish_reason": finish}]},
        {"usage": usage, "choices": []},
    ]
    return stream_of(*chunks)


def test_streamed_arguments_are_kept_as_they_arrive_so_a_cut_off_call_can_be_read(
    tmp_path: Path,
) -> None:
    """A call cut off at the limit once lost its `write`, in the events and at the proxy alike."""
    pieces = [
        {"index": 0, "id": "c1", "function": {"name": "write", "arguments": '{"path": "a.py", '}},
        {"index": 0, "function": {"arguments": '"content": "import cadquery'}},
    ]
    whole = [
        {
            "index": 0,
            "id": "c2",
            "function": {"name": "blocked", "arguments": '{"reason": "stop"}'},
        },
    ]
    answers = [
        httpx.Response(
            200,
            content=streamed_turn(
                *pieces,
                finish="length",
                usage={
                    "prompt_tokens": 9,
                    "completion_tokens": 65536,
                    "completion_tokens_details": {"reasoning_tokens": 2559},
                },
            ),
        ),
        httpx.Response(
            200,
            content=streamed_turn(
                *whole, finish="tool_calls", usage={"prompt_tokens": 9, "completion_tokens": 5}
            ),
        ),
    ]

    def answer(request: httpx.Request) -> httpx.Response:
        # `start` reads the alias's effort from the model info first, which says nothing here.
        if request.url.path.endswith("/model/info"):
            return httpx.Response(404)
        return answers.pop(0)

    sender = send.Sender("the-key", transport=httpx.MockTransport(answer))
    run = Run.start(tmp_path, "proof", REQUEST, [], sender, sleep=lambda _: None)
    assert run.drive().name == "blocked"

    kept = [
        json.loads(line) for line in (run.folder / "streams" / "1.jsonl").read_text().splitlines()
    ]
    assert [k["arguments"] for k in kept] == ['{"path": "a.py", ', '"content": "import cadquery']
    assert kept[0]["name"] == "write"
    assert not (run.folder / "streams" / "2.jsonl").exists(), "a whole call's are in its event"
    assert reply_of(run, "write")["outcome"] == "malformed"
    assert not (run.work / "a.py").exists(), "a cut-off call runs nothing"

    call = sorted(rows(run, "calls"), key=lambda c: c["n"])[0]
    stream = json.loads(call["stream"])
    assert stream["kept"] == "streams/1.jsonl"
    assert stream["tool_args_chars"] == 44
    assert stream["chunks"] >= 4
    assert stream["longest_gap_s"] >= 0
    row = rows(run, "runaways")[0]
    assert row["n"] == 1 and row["why"] == "limit"
    assert json.loads(row["doing"])["stream"]["tool_args_chars"] == 44


# A `met` that's true: a pick holds only for what it saw


def test_a_pick_holds_the_hashes_of_what_was_on_its_sheet(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [write("make.py", FIXTURE_SCRIPT, run=True)],
        [
            tool(
                "ask",
                question="Which?",
                views="iso",
                test_pieces=["test-piece.step"],
                variants=[
                    {"name": "A", "files": ["block.step", "tab.step"]},
                    {"name": "B", "files": ["block.step"]},
                ],
            )
        ],
        SENT,
    )
    assert run.drive().name == "ask"
    seen = json.loads(rows(run, "picks")[0]["seen"])
    assert set(seen["variants"]) == {"A", "B"}
    assert set(seen["variants"]["A"]) == {"block.step", "tab.step"}
    assert seen["variants"]["A"]["block.step"] == seen["variants"]["B"]["block.step"]
    assert set(seen["test_pieces"]) == {"test-piece.step"}
    sheet = (run.folder / "asks" / "1" / "sheet.png").read_bytes()
    assert seen["sheet"] == hashlib.sha256(sheet).hexdigest()


def test_a_pick_on_files_that_havent_changed_holds_across_a_clean_run(tmp_path: Path) -> None:
    """A STEP file's header names the second it was written, and a clean run writes it again."""
    run = asked_with_needs(tmp_path, SIZE_NEED, EYE_NEED)
    picked(run)
    time.sleep(1.1)
    run, _ = again(run, [done()])
    run.resume()
    assert run.drive().name == "met"
    assert [r["passed"] for r in rows(run, "readings")] == [1, 1]


def test_a_pick_on_files_that_changed_doesnt_hold_and_the_need_is_asked_again(
    tmp_path: Path,
) -> None:
    """A real run passed a need on a pick about an older design."""
    run = asked_with_needs(tmp_path, SIZE_NEED, EYE_NEED)
    picked(run)
    ask_again = tool(
        "ask",
        question="And this one?",
        need=["look"],
        views="iso",
        variants=[{"name": "A", "files": ["block.step"], "about": "narrower"}],
    )
    run, _ = again(
        run,
        [write("make.py", script(gap=0.5, size=18.0), run=True)],
        [done()],
        [ask_again],
        SENT,
    )
    run.resume()
    assert run.drive().name == "ask"
    said = reply_of(run, "done")["reply"]
    assert "Round 1: not met yet" in said
    assert "look, NOT MET" in said
    assert "at ask 1, and that pick doesn't hold: block.step changed since" in said
    assert "It's asked again: `ask`" in said
    assert rows(run, "rounds")[0]["met"] == 0
    assert not (run.folder / "final-sheet.png").exists()

    picked(run, 2)
    run, _ = again(run, [done()])
    run.resume()
    assert run.drive().name == "met", "asked again, on what's there now, it holds"


def test_a_pick_of_files_the_part_isnt_made_of_doesnt_hold(tmp_path: Path) -> None:
    # A need is checked on the part (mqucifer/print-shop#45), so this one reads the tab.
    check = {
        "by": "bench",
        "measures": "size",
        "command": "info tab.step",
        "number": "volume_mesh_mm3",
        "at_most": 3000,
    }
    tab_size = {**SIZE_NEED, "id": "tab-volume", "check": check}
    run = asked_with_needs(tmp_path, tab_size, EYE_NEED)
    picked(run)
    run, _ = again(run, [done(part=["tab.step"])], [tool("blocked", reason="stop")])
    run.resume()
    assert run.drive().name == "blocked"
    assert "none of `part` (tab.step) was on its sheet" in reply_of(run, "done")["reply"]


def test_a_pick_of_none_takes_back_the_one_before_it(tmp_path: Path) -> None:
    run = asked_with_needs(tmp_path, SIZE_NEED, EYE_NEED)
    picked(run)
    # Something new beside it: the same sheet again is already picked (mqucifer/print-shop#59).
    ask_again = tool(
        "ask",
        question="Still?",
        need=["look"],
        views="iso",
        variants=[
            {"name": "A", "files": ["block.step"]},
            {"name": "B", "files": ["block.step", "tab.step"]},
        ],
    )
    run, _ = again(run, [ask_again], SENT)
    run.resume()
    assert run.drive().name == "ask"
    (run.folder / "asks" / "2" / "pick.json").write_text(
        '{"pick": "none", "reason": "On reflection, no.", "by": "sponsor"}'
    )
    run, _ = again(run, [done()], [tool("blocked", reason="stop")])
    run.resume()
    assert run.drive().name == "blocked"
    assert "look, NOT MET" in reply_of(run, "done")["reply"]


# A `met` that's true: checks hold what a need says


@pytest.mark.parametrize(
    ("measures", "command", "extra", "verdict_said"),
    [
        ("one-piece", "thin parts.step --one-body", {}, "2 separate bodies, and one was asked for"),
        ("parameter", "params make.py --set GAP=3.0", {}, "PARTIAL"),
    ],
)
def test_a_check_holds_what_a_need_says_at_done(
    tmp_path: Path, measures: str, command: str, extra: dict[str, Any], verdict_said: str
) -> None:
    need = {
        "id": "holds",
        "what": "what the need says",
        "source": "chosen",
        "check": {"by": "bench", "measures": measures, "command": command, **extra},
    }
    made = (
        script(gap=0.5)
        + "both = cq.Compound.makeCompound([block.val(), tab.val()])\n"
        + 'cq.exporters.export(cq.Workplane(obj=both), "parts.step")\n'
    )
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[need, sized("parts.step")])],
        [write("make.py", made, run=True)],
        [done(part=["parts.step"])],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    said = reply_of(run, "done")["reply"]
    assert "holds, NOT MET" in said and verdict_said in said
    assert rows(run, "readings")[0]["passed"] == 0


FRAME_SCRIPT = (
    "import cadquery as cq\n"
    'plate = cq.Workplane("XY").box(60, 60, 4, centered=(True, True, False))\n'
    'pocket = cq.Workplane("XY").workplane(offset=1).rect(40, 40).extrude(2)\n'
    'window = cq.Workplane("XY").rect(36, 36).extrude(4)\n'
    'cq.exporters.export(plate.cut(pocket).cut(window), "frame.step")\n'
)
#: A pane in the frame's pocket, for stained glass's own checks to read.
PANE_LINE = (
    'pane = cq.Workplane("XY").workplane(offset=1).rect(39.8, 39.8).extrude(2)\n'
    'cq.exporters.export(pane, "pane.step")\n'
)


@pytest.mark.parametrize(
    ("measures", "command", "said"),
    [
        (
            "insert",
            "pause frame.step --pocket-z 2 --pane-thickness 2 --min-room 0.2",
            "under the 0.2 mm",
        ),
        (
            "pane-hold",
            # A figure the need holds: a pane count follows the part (mqucifer/print-shop#59).
            "capture frame.step --pocket-z 2 --clearance 0.1 --pane-thickness 2 --min-lip 3",
            "is under 3.0 mm after 0.1 mm of play",
        ),
    ],
)
def test_a_frames_check_holds_what_a_need_says_at_done(
    tmp_path: Path, measures: str, command: str, said: str
) -> None:
    need = {
        "id": "holds",
        "what": "what the need says",
        "source": "chosen",
        "check": {"by": "bench", "measures": measures, "command": command},
    }
    fit = {
        "id": "fit",
        "what": "the pane sits in its pocket",
        "source": "chosen",
        "check": {"by": "bench", "measures": "fit", "command": "clearance frame.step pane.step"},
    }
    glass = [
        {
            "frame": "frame.step",
            "panes": ["pane.step"],
            "pocket_z": 2,
            "clearance": 0.1,
            "pane_thickness": 2,
        }
    ]
    run, _ = begin_stained_glass(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[need, fit])],
        [write("frame.py", FRAME_SCRIPT + PANE_LINE, run=True)],
        [done("frame.py", part=["frame.step", "pane.step"], glass=glass)],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    told = reply_of(run, "done")["reply"]
    assert "holds, NOT MET" in told and said in told


# Approval is a stop


def met_with_a_sheet(tmp_path: Path, **more: Any) -> Run:
    # A need reads each file of the part, as `done` asks (mqucifer/print-shop#52).
    rows_ = more.get("part", ["block.step"])
    files = [f for r in rows_ for f in (r["files"] if isinstance(r, dict) else [r])]
    read = [SIZE_NEED, *[sized(f) for f in files if f != "block.step"]]
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=read)],
        [write("make.py", script(gap=0.5), run=True)],
        [done(closing="I couldn't try the fit, so look at the shape.", **more)],
    )
    assert run.drive().name == "met"
    return run


def legend_px(files: int, width: int) -> int:
    """The legend's height under a sheet, as `render` draws it: a line for each few files."""
    return 18 * -(-files // max(1, width // 440)) + 8


def sheet_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[16:24]
    return int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")


def test_every_need_passing_stops_for_approval_and_doesnt_end_the_run(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path)
    assert run.state.stop == {"kind": "met", "n": 1}
    assert run.state.ending is None, "`done` can't end a run"
    assert rows(run, "runs")[0]["ending"] == "met"
    assert not rows(run, "decisions")
    told = reply_of(run, "done")["reply"]
    assert "every need is met" in told and "It isn't approved yet" in told
    # The model's closing words reach the person, with the sheet.
    words = "I couldn't try the fit, so look at the shape."
    next_step = what_next(run, Ending("met", {"approval": 1}))
    assert words in next_step
    assert "--approve" in next_step and "--send-back" in next_step
    folder = run.folder / "approvals" / "1"
    assert words in (folder / "stop.md").read_text()
    assert "isn't approved yet" in (folder / "stop.md").read_text()
    assert (folder / "closing.md").read_text().strip() == words
    asked = rows(run, "approvals")[0]
    assert asked["closing_model_said"] == words
    assert asked["sheet_sha"] == hashlib.sha256((folder / "sheet.png").read_bytes()).hexdigest()
    assert set(json.loads(asked["part_hashes"])) == {"block.step"}
    assert all(json.loads(asked["part_hashes"]).values())
    # And the model sees what the person sees.
    assert any(
        e["kind"] == "picture" and e["name"] == "approvals/1/sheet.png"
        for e in transcript.events_of(run.session.events)
    )
    with pytest.raises(Refused, match="waiting for approval"):
        run.resume()


def test_approval_ends_it_with_no_call_and_says_who(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path)
    run, model = again(run)
    with pytest.raises(Refused, match="says who picked"):
        run.approve("Sponsor")
    ending = run.approve("sponsor", "Good enough to print.")
    assert ending.name == "approved"
    assert model.bodies == [], "no model call"
    assert rows(run, "runs")[0]["ending"] == "approved"
    decision = rows(run, "decisions")[0]
    assert (decision["decision"], decision["by"], decision["note"], decision["approval"]) == (
        "approved",
        "sponsor",
        "Good enough to print.",
        1,
    )
    assert json.loads((run.folder / "state.json").read_text())["ending"]["name"] == "approved"
    with pytest.raises(Refused, match="isn't waiting for approval"):
        run.approve("sponsor")
    assert not (run.folder / "stop").exists()


def test_a_send_back_goes_on_as_a_new_round_with_its_note(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path)
    run, _ = again(run)
    with pytest.raises(Refused, match="needs its note"):
        run.send_back("sponsor", "  ")
    run, model = again(run, [done(closing="Shorter now.", fix_shape="shortened")])
    run.send_back("stand-in: a Claude builder", "Make it shorter.")
    assert run.state.round == 2
    assert not run.state.stop
    assert rows(run, "runs")[0]["ending"] is None, "it's going again"
    assert run.drive().name == "met"
    told = model.bodies[0]["messages"][-1]["content"]
    assert told.startswith("A stand-in for the Sponsor (a Claude builder) looked at the final part")
    assert "Make it shorter." in told
    decision = rows(run, "decisions")[0]
    assert (decision["decision"], decision["by"]) == ("sent_back", "stand-in: a Claude builder")
    row = next(r for r in rows(run, "interventions") if r["kind"] == "send_back")
    assert row["from_who"] == "stand-in: a Claude builder"
    assert row["text"] == "Make it shorter."
    assert run.state.stop == {"kind": "met", "n": 2}
    assert [r["n"] for r in sorted(rows(run, "approvals"), key=lambda r: r["n"])] == [1, 2]


def test_after_approval_only_a_send_back_by_hand_goes_on(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path)
    run, _ = again(run)
    run.approve("sponsor")
    run, _ = again(run)
    with pytest.raises(Refused, match="only a send-back"):
        run.resume("Go on.")
    run, _ = again(run, [tool("blocked", reason="stop")])
    run.send_back("sponsor", "On reflection it's too wide.")
    assert run.drive().name == "blocked"
    made = sorted(rows(run, "decisions"), key=lambda r: r["id"])
    assert [(d["decision"], d["approval"]) for d in made] == [("approved", 1), ("sent_back", 1)]


def test_a_sheet_at_the_stop_shows_one_part_a_row(tmp_path: Path) -> None:
    """A final sheet once drew two parts on top of each other."""
    run = met_with_a_sheet(
        tmp_path,
        part=["block.step", {"name": "second", "files": ["tab.step"]}],
    )
    sheet = run.folder / "approvals" / "1" / "sheet.png"
    assert sheet_size(sheet) == (3 * 512, 2 * 512 + legend_px(2, 3 * 512)), "three views, 2 rows"
    assert sheet.read_bytes() == (run.folder / "final-sheet.png").read_bytes()
    drawn = final_render(run)
    assert drawn["numbers"]["views"]["block top"]["projected_mm"] == [20.0, 20.0]
    assert drawn["numbers"]["views"]["second top"]["projected_mm"] == [10.0, 10.0]
    assert json.loads(rows(run, "rounds")[0]["part"]) == ["block.step", "tab.step"]
    assert json.loads(rows(run, "rounds")[0]["part_rows"])[1]["name"] == "second"


def test_a_sheet_too_tall_to_send_stays_with_the_person(tmp_path: Path) -> None:
    many = script(gap=0.5) + "".join(
        f'cq.exporters.export(block, "copy{i}.step")\n' for i in range(4)
    )
    copies = ["block.step", *[f"copy{i}.step" for i in range(4)]]
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, *[sized(f) for f in copies[1:]]])],
        [write("make.py", many, run=True)],
        # Five copies in one place: each prints on its own, or they'd take the same space.
        [done(part=copies, groups=[{"name": f"g{k}", "files": [f]} for k, f in enumerate(copies)])],
    )
    assert run.drive().name == "met"
    tall = (3 * 512, 5 * 512 + legend_px(5, 3 * 512))
    assert sheet_size(run.folder / "approvals" / "1" / "sheet.png") == tall
    events = transcript.events_of(run.session.events)
    assert not any(e["kind"] == "picture" for e in events)


def test_files_seen_together_are_one_row(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path, part=[{"name": "whole", "files": ["block.step", "tab.step"]}])
    assert sheet_size(run.folder / "approvals" / "1" / "sheet.png") == (
        3 * 512,
        512 + legend_px(2, 3 * 512),
    )


def test_the_command_line_approves_with_no_model_and_no_key(tmp_path: Path) -> None:
    run = met_with_a_sheet(tmp_path)
    command = [
        sys.executable,
        str(host.REPOSITORY / "scripts" / "rounds.py"),
        "--root",
        str(tmp_path),
    ]
    # A home of its own, so the after-action review finds no key and reaches no LiteLLM.
    env = {k: v for k, v in os.environ.items() if "KEY" not in k} | {"HOME": str(tmp_path)}
    refused = subprocess.run(
        [*command, "resume", "proof", "--approve", "--by", "nobody"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert refused.returncode == 3 and "says who picked" in refused.stderr
    done_it = subprocess.run(
        [*command, "resume", "proof", "--approve", "--by", "sponsor"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert done_it.returncode == 0, done_it.stderr
    assert rows(run, "decisions")[0]["decision"] == "approved"
    assert "the after-action review didn't begin" in done_it.stdout
    assert rows(run, "after_actions")[0]["ended"] == "not_begun"


# The bench check through a run's `read-back`, and a run from before all this


def test_a_live_runs_rows_are_what_reading_it_back_gives(tmp_path: Path) -> None:
    """One way of writing them: what a table says doesn't depend on when it was read."""
    run = asked_with_needs(tmp_path, SIZE_NEED, EYE_NEED)
    picked(run)
    run, _ = again(run, [done()])
    run.resume("Thinner.")
    assert run.drive().name == "met"
    run.approve("sponsor")
    tables = ("calls", "interventions", "timeline", "runaways", "tries")
    live = {t: rows(run, t) for t in tables}
    assert live["calls"] and live["interventions"] and live["timeline"]

    fresh = DesignLedger(tmp_path / "fresh.sqlite")
    with sqlite3.connect(tmp_path / "ledger.sqlite") as source, sqlite3.connect(fresh.path) as to:
        row = source.execute("SELECT * FROM runs").fetchone()
        to.execute(f"INSERT INTO runs VALUES ({','.join('?' * len(row))})", row)
        for one in source.execute("SELECT * FROM rounds").fetchall():
            to.execute(f"INSERT INTO rounds VALUES ({','.join('?' * len(one))})", one)
    records.read_back(run.folder, fresh)
    for table in tables:
        again_read = [dict(r) for r in fresh.rows(f"SELECT * FROM {table}")]  # noqa: S608
        assert again_read == live[table], table


# Requirements, pictures and the delivered result say what's true (mqucifer/print-shop#26)

#: The block with a fin standing out 8 mm from its side, `FIN` mm thick: under the nozzle's 0.4 mm,
#: `thin` fails it.
FIN_LINES = (
    'fin = cq.Workplane("XY").box(8, FIN, 5).translate((-SIZE / 2 - 4 + 0.01, 0, 0))\n'
    "block = block.union(fin)\n"
)
FIN_SCRIPT = (
    script(gap=0.5)
    .replace("tab = ", f"{FIN_LINES}tab = ")
    .replace("GAP = 0.5\n", "GAP = 0.5\nFIN = {fin}\n")
)
#: The block made two ways, as a frame is: whole, or as a base and a top that print together.
TWO_WAYS = script(gap=0.5) + (
    'cq.exporters.export(cq.Workplane("XY").box(20, 20, 3).translate((0, 0, -1)), "base.step")\n'
    'cq.exporters.export(cq.Workplane("XY").box(20, 20, 2).translate((0, 0, 1.5)), "top.step")\n'
)
TWO_WAYS_PART = ["block.step", "base.step", "top.step", "tab.step"]
TWO_WAYS_GROUPS = [
    {"name": "one-piece", "files": ["block.step"], "about": "printed whole"},
    {"name": "two-pieces", "files": ["base.step", "top.step"], "about": "base, then top"},
]
LOOKS = {
    "tab.step": {"colour": "amber", "label": "the tab"},
    "block.step": {"colour": "grey", "label": "block"},
}


def test_the_run_doesnt_stop_for_approval_with_a_file_under_the_nozzles_width(
    tmp_path: Path,
) -> None:
    """On `cup-1` two panes the Sponsor found too thin in Orca passed every need."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, sized("tab.step")])],
        [write("make.py", FIN_SCRIPT.format(fin=0.2), run=True)],
        [done(part=["block.step", "tab.step"])],
        [write("make.py", FIN_SCRIPT.format(fin=2.0), run=True)],
        [done(part=["block.step", "tab.step"])],
    )
    assert run.drive().name == "met"
    first, second = [e["reply"] for e in replies(run) if e["name"] == "done" and e["why"] != STALE]
    assert "Round 1: not met yet. Every need passed" in first
    assert "- block.step, TOO THIN: FAIL: " in first and "narrower than the 0.4 mm nozzle" in first
    assert "the thinnest 0.200 mm" in first and "- tab.step, ok:" in first
    assert "Round 2: every need is met." in second
    assert run.state.stop == {"kind": "met", "n": 1}
    stood = [r for r in rows(run, "standing") if r["check_name"] == "printable"]
    assert {(r["round"], r["file"], r["passed"]) for r in stood} == {
        (1, "block.step", 0),
        (1, "tab.step", 1),
        (2, "block.step", 1),
        (2, "tab.step", 1),
    }
    assert all(r["stop"] == "done" and r["file_sha"] for r in stood)
    met_column = {r["round"]: r["met"] for r in rows(run, "rounds")}
    assert met_column == {1: 0, 2: 1}, "a met is only what was checked"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "Every file to be printed, by `thin`" in stop and "`block.step`, ok" in stop


def test_files_printed_together_cant_take_the_same_space(tmp_path: Path) -> None:
    """Both real runs handed over `frame` beside `frame_base` and `cap`, its two halves."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, *[sized(f) for f in TWO_WAYS_PART[1:]]])],
        [write("make.py", TWO_WAYS, run=True)],
        [done(part=TWO_WAYS_PART)],
        [done(part=TWO_WAYS_PART, groups=TWO_WAYS_GROUPS, looks=LOOKS)],
    )
    assert run.drive().name == "met"
    first = [e["reply"] for e in replies(run) if e["name"] == "done" and e["why"] != STALE][0]
    assert "`block.step` and `base.step` share 1200 mm³, 1200 by OCCT" in first
    assert "the group `part` prints them together" in first
    review = run.folder / "review"
    assert sorted(p.name for p in (review / "A-one-piece").iterdir()) == [
        "block.step",
        "tab-the-tab-amber.step",
    ]
    assert sorted(p.name for p in (review / "B-two-pieces").iterdir()) == [
        "base.step",
        "tab-the-tab-amber.step",
        "top.step",
    ]
    judged = run.folder / "rounds" / "002" / "judged"
    assert (review / "B-two-pieces" / "top.step").read_bytes() == (judged / "top.step").read_bytes()
    readme = (review / "README.md").read_text()
    assert "**Open one folder only.**" in readme
    assert "| **volume**: the block is the size chosen |" in readme
    assert "| `tab-the-tab-amber.step` | `tab.step` | the tab | amber |" in readme
    assert "**Size:** 30.5 x 20 x 5 mm" in readme
    # The folder's own copy is the stop's.
    assert (run.folder / "approvals" / "1" / "review" / "README.md").read_text() == readme
    asked = rows(run, "approvals")[0]
    held = json.loads(asked["review"])
    assert [g["folder"] for g in held["groups"]] == ["A-one-piece", "B-two-pieces"]
    assert "A-one-piece" in (run.folder / "approvals" / "1" / "stop.md").read_text()


def test_a_group_that_names_a_file_outside_the_part_is_told(tmp_path: Path) -> None:
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [done(groups=[{"name": "one", "files": ["tab.step"]}])],
        [done(looks={"block.step": {"colour": "sparkly"}})],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    told = [e for e in replies(run) if e["name"] == "done"]
    assert told[0]["why"] == "not_a_group" and "isn't in `part`" in told[0]["reply"]
    assert told[1]["why"] == "not_a_look" and "sparkly" in told[1]["reply"]
    assert not rows(run, "readings"), "neither cost a clean run"


def failing_gap_then_stopped(tmp_path: Path) -> Run:
    """Needs settled with a gap the part misses, a round that says so, and a stop."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[GAP_NEED, SIZE_NEED])],
        [write("make.py", script(gap=2.0), run=True)],
        [done()],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == "blocked"
    return run


def test_a_need_whose_check_fails_isnt_swapped_for_one_that_passes_without_a_reason(
    tmp_path: Path,
) -> None:
    """On `cup-1` `capture` was swapped for `clearance`, and `pause` pointed at a file with no
    pockets, so both passed."""
    run = failing_gap_then_stopped(tmp_path)
    looser = {
        **GAP_NEED,
        "check": {"by": "bench", "measures": "fit", "command": "clearance block.step tab.step"},
    }
    reason = "The gap was chosen too fine for this printer."
    run, _ = again(
        run,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED])],
        [BROKEN_DOWN, tool("needs", needs=[looser, SIZE_NEED])],
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED], why_changed={"gap": reason})],
        [done()],
    )
    run.resume(reopen_needs=True)
    assert run.drive().name == "met"
    said = [e for e in replies(run) if e["name"] == "needs"][-3:]
    assert [e["outcome"] for e in said] == ["failed", "failed", "ok"]
    assert said[0]["why"] == "swapped_failing"
    assert "`gap`'s check, `clearance block.step tab.step --min-gap 0.2`" in said[0]["reply"]
    assert "is 2, over 0.55. This set drops it" in said[0]["reply"]
    assert "changes its check to `clearance block.step tab.step`" in said[1]["reply"]
    assert "the person at the next stop sees both checks and your reason" in said[2]["reply"]
    swap = rows(run, "need_swaps")[0]
    assert (swap["need"], swap["reason"], swap["new_check"]) == ("gap", reason, None)
    assert swap["shown_at"] == "approval 1"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert "## Needs changed while their old check failed" in stop
    assert "was `clearance block.step tab.step --min-gap 0.2`" in stop and reason in stop
    assert reason in (run.folder / "review" / "README.md").read_text()
    assert run.state.swaps == [], "shown once"


def test_a_need_changed_to_a_check_that_fails_too_needs_no_reason(tmp_path: Path) -> None:
    run = failing_gap_then_stopped(tmp_path)
    tighter = {**GAP_NEED, "check": {**GAP_NEED["check"], "at_most": 0.5}}
    run, _ = again(run, [tool("needs", needs=[tighter, SIZE_NEED])], [tool("blocked", reason="x")])
    run.resume(reopen_needs=True)
    assert run.drive().name == "blocked"
    assert reply_of(run, "needs")["outcome"] == "ok"
    assert not rows(run, "need_swaps")


def test_an_asks_sheet_goes_to_the_model_first_and_only_then_with_its_words(
    tmp_path: Path,
) -> None:
    """On both real runs the model described the design it meant, not what the sheet showed."""
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[GAP_NEED, EYE_NEED])],
        [write("make.py", script(gap=2.0), run=True)],
        [tool("send_ask", says="nothing yet")],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["look"],
                views="top",
                cuts="z=0",
                looks={"block.step": {"colour": "blue", "label": "block"}},
                variants=[{"name": "A", "files": ["block.step", "tab.step"], "about": "even"}],
            )
        ],
        [write("make.py", script(gap=2.5), run=True)],
        [tool("send_ask", says="The block, blue.")],
        [
            tool(
                "ask",
                question="Which proportions?",
                need=["look"],
                views="top",
                looks={"block.step": {"colour": "blue", "label": "block"}},
                variants=[{"name": "A", "files": ["block.step", "tab.step"], "about": "even"}],
            )
        ],
        [tool("send_ask", says="The block in blue, the tab beside it in another colour.")],
    )
    assert run.drive().name == "ask"
    sent = [e for e in replies(run) if e["name"] == "send_ask"]
    assert sent[0]["why"] == "no_draft"
    assert sent[1]["why"] == "changed", "a file changed after its sheet was drawn"
    drawn = [e for e in replies(run) if e["name"] == "ask"]
    assert all(e["outcome"] == "ok" for e in drawn)
    assert "nothing has gone out yet" in drawn[0]["reply"]
    assert "drawn: block.step: block (blue)" in drawn[0]["reply"]
    # The harness's own reading of what the sheet shows: the gap fails, as `done` would say.
    assert (
        "clearance block.step tab.step --min-gap 0.2: `pairs.0.gap_mesh_mm` is 2"
        in drawn[0]["reply"]
    )
    pictures = [e for e in transcript.events_of(run.session.events) if e["kind"] == "picture"]
    assert len(pictures) == 2, "each drawn sheet is shown to the model"
    assert len(rows(run, "picks")) == 1, "only what was sent is asked"
    folder = run.folder / "asks" / "1"
    question = (folder / "question.md").read_text()
    assert "## What the model says the sheet shows" in question
    assert "The block in blue, the tab beside it in another colour." in question
    assert "## Checks that don't pass on these files" in question
    assert "`clearance block.step tab.step --min-gap 0.2`: `pairs.0.gap_mesh_mm` is 2.5" in question
    assert '"fix_first"' in question
    pick = rows(run, "picks")[0]
    assert pick["said_of_sheet_model_said"].startswith("The block in blue")
    at_ask = [r for r in rows(run, "standing") if r["stop"] == "ask"]
    assert {r["check_name"] for r in at_ask} == {"need gap", "printable", "curves"}
    assert all(r["ask"] == 1 for r in at_ask)
    asked = json.loads((folder / "asked.json").read_text())
    assert asked["says_model_said"].startswith("The block in blue")
    assert asked["looks"] == {"block.step": {"colour": "blue", "label": "block"}}


def test_a_fault_to_fix_first_goes_back_apart_from_the_pick(tmp_path: Path) -> None:
    """The Sponsor was asked to answer `none` for a build fault, which mixed two answers."""
    run = asked_with_needs(tmp_path, SIZE_NEED, EYE_NEED)
    folder = run.folder / "asks" / "1"
    (folder / "pick.json").write_text(
        json.dumps({"pick": "A", "reason": "It reads.", "by": "sponsor", "fix": "x"})
    )
    run, model = again(run, [tool("blocked", reason="stop")])
    with pytest.raises(Refused, match="holds pick, reason, by and fix_first"):
        run.resume()
    (folder / "pick.json").write_text(
        json.dumps(
            {"pick": "A", "reason": "It reads.", "by": "sponsor", "fix_first": "Split pane 9."}
        )
    )
    run.resume()
    assert run.drive().name == "blocked"
    told = [m["content"] for m in model.bodies[0]["messages"] if m["role"] == "user"][-2:]
    assert told[0] == "The Sponsor picked A. Their reason: It reads."
    assert told[1] == "Apart from the look, the Sponsor says to fix this first:\n\nSplit pane 9."
    assert rows(run, "picks")[0]["fix_first"] == "Split pane 9."
    kinds = [(r["kind"], r["text"]) for r in rows(run, "interventions")]
    assert ("fix_first", "Split pane 9.") in kinds
    assert run.state.picks["look"]["pick"] == "A", "the pick stands as a pick"


def test_a_send_back_can_reopen_the_needs(tmp_path: Path) -> None:
    """On `cup-1` the model tried to put its checks right after a send-back, and was refused."""
    run = met_with_a_sheet(tmp_path)
    run, _ = again(
        run,
        [BROKEN_DOWN, tool("needs", needs=[SIZE_NEED, {**SIZE_NEED, "id": "printable"}])],
        [tool("blocked", reason="stop")],
    )
    run.send_back("sponsor", "Add the check you were missing.", reopen_needs=True)
    assert run.drive().name == "blocked"
    assert reply_of(run, "needs")["outcome"] == "ok"
    kinds = [r["kind"] for r in rows(run, "interventions")]
    assert kinds[-2:] == ["send_back", "reopen_needs"]


# A gap read by place (mqucifer/print-shop#72)

#: A pane rounded 3 mm, 1.2 mm thick, in a frame's pocket 0.25 mm wider round its edge and 0.15 mm
#: over each face, as `bench_cases/make_fixtures.py`'s `places/` pair.
POCKET_SCRIPT = (
    "import cadquery as cq\n"
    "def rounded(w, h, r, z0, z1):\n"
    '    return cq.Workplane("XY").rect(w, h).extrude(z1 - z0).edges("|Z").fillet(r)'
    ".translate((0, 0, z0))\n"
    "pane = rounded(20, 14, 3, 0, 1.2)\n"
    'frame = cq.Workplane("XY").box(28, 22, 3.5, centered=(True, True, False))'
    ".translate((0, 0, -1.15))\n"
    "frame = frame.cut(rounded(20.5, 14.5, 3.25, -0.15, 1.35)).cut(rounded(18, 12, 2, -2, 3))\n"
    'cq.exporters.export(frame, "frame.step")\n'
    'cq.exporters.export(pane, "pane.step")\n'
)


@pytest.mark.parametrize(("least", "met"), [(0.25, True), (0.2501, False)])
def test_a_need_on_a_place_is_held_at_done(tmp_path: Path, least: float, met: bool) -> None:
    """On `eval-fit-3` the edge play's need couldn't reach `done`: `clearance` read the face play,
    a section was refused, and a size wasn't the play. A need on the edge's place, on the printed
    frame and the given pane, passes at 0.25 and fails past it."""
    need = {
        "id": "edge-play",
        "what": "0.25 mm of play round the pane's edge",
        "source": "chosen",
        "check": {
            "by": "bench",
            "measures": "fit",
            "command": "clearance frame.step pane.step --box edge=9.6,-3,0.1,10.6,3,1.1",
            "verdicts": ["CLEAR"],
            "number": "pairs.0.places.edge.gap_mm",
            "at_least": least,
            "at_most": 0.25,
        },
    }
    run, _ = begin(
        tmp_path,
        [BROKEN_DOWN, tool("needs", needs=[need, sized("frame.step"), sized("pane.step")])],
        [write("make.py", POCKET_SCRIPT, run=True)],
        [done(part=["frame.step", "pane.step"])],
        [tool("blocked", reason="stop")],
    )
    assert run.drive().name == ("met" if met else "blocked")
    reading = next(r for r in rows(run, "readings") if r["need"] == "edge-play")
    assert reading["passed"] == int(met)
    if not met:
        told = reply_of(run, "done")["reply"]
        assert "`pairs.0.places.edge.gap_mm` is 0.25, under 0.2501" in told
