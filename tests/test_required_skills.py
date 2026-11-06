"""Each kind's required skills, whole in the start, and still there after every compaction and
every new conversation (mqucifer/print-shop#61). No Docker, and no model: a scripted one.

On `suncatcher-3` the start named 15 skills in one sentence, and in 325 calls `cadquery` and
`using-the-bench` were never read. A read was lost at the next compaction, of 31. Every case here
is made up in the test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import skills, toolsets, transcript
from print_shop.rounds.run import Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, Spoken, tool

GLASS_REQUEST = """A frame of three panes.

## Requirements

- At least 2 mm of wall round the hanging hole.

## Kind

stained-glass
"""


def body(name: str) -> str:
    return skills.registry(skills.ALL)[name][1].strip()


def test_each_toolset_names_its_required_skills_and_each_is_one_of_its_own() -> None:
    assert toolsets.CORE.required == ("cadquery", "using-the-bench")
    assert toolsets.STAINED_GLASS.required == (
        "designing-stained-glass",
        "cadquery",
        "using-the-bench",
    )
    assert toolsets.EVERY.required == ()
    for toolset in (toolsets.CORE, *toolsets.KINDS.values()):
        assert set(toolset.required) <= set(skills.registry(toolset.skills)), toolset.title


def test_the_start_gives_each_required_skill_whole_and_names_the_rest() -> None:
    glass = toolsets.STAINED_GLASS
    said = skills.start_skills(glass.skills, glass.required)
    at = [said.index(f'<skill name="{name}">\n{body(name)}\n</skill>') for name in glass.required]
    assert at == sorted(at), "in the order the toolset gives them"
    rest = said[said.index("The other skills") :]
    for name, (about, _) in skills.registry(glass.skills).items():
        if name in glass.required:
            assert f"`{name}`," not in rest, name
        else:
            assert f"`{name}`, {about.rstrip('.')}" in rest, name
    with pytest.raises(KeyError):
        skills.start_skills(toolsets.CORE.skills, ("designing-stained-glass",))


@pytest.mark.parametrize(
    ("request_text", "given"),
    [
        ("A block.", ["cadquery", "using-the-bench"]),
        (GLASS_REQUEST, ["designing-stained-glass", "cadquery", "using-the-bench"]),
    ],
)
def test_a_run_starts_with_its_kinds_required_skills_and_the_ledger_has_them_as_given(
    tmp_path: Path, request_text: str, given: list[str]
) -> None:
    model = Scripted([tool("blocked", reason="Only the start.")])
    run = Run.start(tmp_path, "r", request_text, [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    start = model.bodies[0]["messages"][1]["content"]
    for name in given:
        assert f'<skill name="{name}">\n{body(name)}\n</skill>' in start, name
    assert "Read one with `skill` when you need it" not in start
    kept = run.ledger.rows("SELECT seq, at_call, name, how, found FROM skill_reads ORDER BY seq")
    assert [(r["name"], r["how"], r["at_call"], r["found"]) for r in kept] == [
        (name, "given", 1, 1) for name in given
    ]


def test_reading_a_required_skill_points_to_the_start_and_the_rest_read_as_before(
    tmp_path: Path,
) -> None:
    model = Scripted(
        [tool("skill", name="cadquery"), tool("skill", name="cadquery-errors")],
        [tool("blocked", reason="Read.")],
    )
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    told = [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]
    assert told[0]["outcome"] == "ok"
    assert '`<skill name="cadquery">`' in told[0]["reply"]
    assert "through every compaction" in told[0]["reply"]
    assert told[1]["reply"] == skills.registry(skills.ALL)["cadquery-errors"][1]
    kept = run.ledger.rows("SELECT name, how FROM skill_reads WHERE how = 'read' ORDER BY seq")
    assert [(r["name"], r["how"]) for r in kept] == [
        ("cadquery", "read"),
        ("cadquery-errors", "read"),
    ]


#: Skills of the core read whole, to give compaction something to take away.
READS = (
    "cadquery-sketch-extrude",
    "cadquery-errors",
    "ref-fasteners",
    "ref-fdm",
    "cadquery-selectors",
)


def test_the_required_skills_are_still_whole_after_a_compaction(tmp_path: Path) -> None:
    """A compaction makes an earlier `skill` reply a note, and leaves the start as it was."""
    model = Scripted(
        [tool("skill", name=name) for name in READS],
        Spoken([tool("list", path=".")], usage={"prompt_tokens": 20_000}),
        [tool("blocked", reason="After a compaction.")],
    )
    run = Run.start(
        tmp_path, "r", "A block.", [], model, Limits(compact_at=16_000), sleep=lambda _: None
    )
    assert run.drive().name == "blocked"
    start = model.bodies[0]["messages"][1]["content"]
    assert run.session.compactions == 1
    after = model.bodies[-1]["messages"]
    assert after[1]["content"] == start
    for name in toolsets.CORE.required:
        assert f'<skill name="{name}">\n{body(name)}\n</skill>' in after[1]["content"], name
    read_replies = [m for m in after if m["role"] == "tool" and "(compacted: skill" in m["content"]]
    assert len(read_replies) == len(READS), "the reads were compacted, the start wasn't"
    # The request as the records rebuild it is the one sent.
    assert transcript.rebuild(run.session.events, len(model.bodies))["messages"] == after


def test_a_new_conversation_from_the_record_starts_with_them_again(tmp_path: Path) -> None:
    model = Scripted(
        [tool("list", path=".")],
        [tool("blocked", reason="After the new conversation.")],
        prompt_tokens=10_000,
    )
    run = Run.start(
        tmp_path, "r", GLASS_REQUEST, [], model, Limits(compact_at=9_000), sleep=lambda _: None
    )
    assert run.drive().name == "blocked"
    assert run.state.restarts >= 1
    first = model.bodies[-1]["messages"][1]["content"]
    assert "An earlier conversation about this ran out of room" in first
    for name in toolsets.STAINED_GLASS.required:
        assert f'<skill name="{name}">\n{body(name)}\n</skill>' in first, name
    kept = run.ledger.rows("SELECT at_call, name FROM skill_reads WHERE how = 'given'")
    calls: dict[int, list[str]] = {}
    for row in kept:
        calls.setdefault(int(row["at_call"]), []).append(str(row["name"]))
    assert len(calls) == 1 + run.state.restarts, "given at the start and at each new one"
    assert all(names == list(toolsets.STAINED_GLASS.required) for names in calls.values())


def test_a_runaway_counts_a_given_skill_as_had_not_as_unread(tmp_path: Path) -> None:
    import json

    looped = "The same long sentence comes back here, word for word, again and again. " * 40
    model = Scripted(
        Spoken([tool("list", path=".")], reasoning=looped),
        [tool("blocked", reason="Done.")],
    )
    run = Run.start(
        tmp_path, "r", "A block.", [], model, Limits(review_repeat_share=0.2), sleep=lambda _: None
    )
    assert run.drive().name == "blocked"
    found: list[Any] = run.ledger.rows("SELECT lacked FROM runaways")
    assert found, "the repeating call is a runaway"
    lacked = json.loads(found[0]["lacked"])
    assert lacked["skills_given"] == ["cadquery", "using-the-bench"]
    assert "cadquery" not in lacked["skills_not_read"]
    assert "cadquery-errors" in lacked["skills_not_read"]


def test_a_layouts_picture_comes_to_the_model_as_a_renders_does(tmp_path: Path) -> None:
    """`layout`'s labelled picture is the reading's point: which piece is which."""
    from tests.test_rounds import NoBox, desk_tools

    (tmp_path / "bench-out").mkdir()
    (tmp_path / "layout").mkdir()
    (tmp_path / "layout" / "flat-layout.png").write_bytes(b"picture")
    (tmp_path / "bench-out" / "a.json").write_text(
        '{"command": "layout", "files": ["layout/flat-layout.png"]}'
    )
    box = NoBox("verdict: OK: x\nsaved: bench-out/a.txt and .json\n")
    judged = desk_tools(tmp_path, box).call("bench", '{"command": "layout flat.json"}')
    assert judged.outcome == "ok"
    assert [name for name, _ in judged.images] == ["layout/flat-layout.png"]


#: print-shop's own stained-glass skill, as batch 3's evals read it, at 5a23a4c: its additions
#: come after `skills.ADDED`, and the text above is held to this (mqucifer/print-shop#61).
STAINED_GLASS_MEASURED = "b82538dafaf79b17b35dd92ad8d305025df14da45dea9210b35a8520ce3314c1"


def test_the_additions_come_after_the_measured_text_and_say_what_61_asks() -> None:
    import hashlib

    glass = skills.SKILLS / "designing-stained-glass" / "SKILL.md"
    assert hashlib.sha256(skills.measured(glass)).hexdigest() == STAINED_GLASS_MEASURED
    for path in (glass, skills.SKILLS / "cadquery" / "SKILL.md"):
        added = path.read_text().split(skills.ADDED, 1)[1]
        assert "from pieces" in added and "point by point" in added, path
        assert "--cut z=Z" in added, "a cut after each build, before reasoning about it"
    added = glass.read_text().split(skills.ADDED, 1)[1]
    assert "Read for step 5: `cadquery`" in added and "`building-curves-from-a-layout`" in added
    assert "bench layout layout.json --inset" in added
    bench = skills.registry(skills.ALL)["using-the-bench"][1]
    assert "| `layout FILE.json" in bench and "**Look at what you built.**" in bench
