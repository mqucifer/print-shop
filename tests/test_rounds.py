"""The rounds with no Docker: path guards, argument judging, compaction, records and needs."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from print_shop.rounds import needs, paths, send, skills, tools, toolsets, transcript
from print_shop.rounds import run as run_module
from print_shop.rounds.box import Ran
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.needs import Need
from print_shop.rounds.session import COMPACTED, SHOWN_BEFORE, Ending, Judged, Limits, Session
from print_shop.rounds.tools import WorkTools
from tests.rounds_fakes import Scripted, raw, tool

#: The measured text, from mqucifer/model-performance `cad_eval/skills` at 98b3c5b.
MEASURED_SKILLS_SHA = "dc0154e0a7ea87c7f34fc7d706cf49afc1b273927f7dc90e67dd98a69214187c"
CAD_EVAL_SKILLS = Path("~/Documents/git/model-performance/cad_eval/skills").expanduser()

# Host-side paths: where `cad_eval`'s sandbox escapes came from.


def test_a_path_out_of_the_directory_is_refused_by_name(tmp_path: Path) -> None:
    for path in ("../x", "/etc/passwd", ".", "", "a/../../x"):
        assert paths.inside(tmp_path, path) is None, path
    assert paths.inside(tmp_path, "a/b.py") == tmp_path / "a/b.py"


def test_a_link_anywhere_in_a_path_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("no")
    work = tmp_path / "work"
    work.mkdir()
    (work / "file").symlink_to(outside / "secret")
    (work / "folder").symlink_to(outside)
    assert paths.inside(work, "file") is None
    assert paths.inside(work, "folder/secret") is None
    with pytest.raises(paths.Outside):
        paths.read_inside(work, "file")
    with pytest.raises(paths.Outside):
        paths.read_inside(work, "folder/secret")
    with pytest.raises(paths.Outside):
        paths.write_inside(work, "folder/new", "no")
    with pytest.raises(paths.Outside):
        paths.write_inside(work, "file", "overwritten")
    assert (outside / "secret").read_text() == "no"
    assert not (outside / "new").exists()


def test_a_fifo_is_refused_and_doesnt_hang_the_harness(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(paths.Outside):
        paths.read_inside(tmp_path, "pipe")


def test_a_clean_copy_never_follows_a_link_and_leaves_what_runs_make(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("no")
    work = tmp_path / "work"
    (work / "bench-out").mkdir(parents=True)
    (work / "bench-out" / "old.json").write_text("{}")
    (work / "make.py").write_text("x = 1\n")
    (work / "linked").symlink_to(outside)
    (work / "file").symlink_to(outside / "secret")
    target = tmp_path / "copy"
    paths.copy_plain(work, target, skip_made=True)
    assert sorted(str(p) for p in paths.walk_plain(target)) == ["make.py"]


# How a call is judged


class Desk:
    def __init__(self) -> None:
        self.bench: list[list[str]] = []

    def breakdown(self, args: dict[str, Any]) -> Judged:
        return Judged("breakdown", "ok", "kept")

    def questions(self, args: dict[str, Any]) -> Judged:
        return Judged("questions", "ok", "asked", ending=Ending("questions", {"n": 1}))

    def needs(self, args: dict[str, Any]) -> Judged:
        return Judged("needs", "ok", f"{len(args['needs'])} needs")

    def ask(self, args: dict[str, Any]) -> Judged:
        return Judged("ask", "ok", "drawn")

    def send_ask(self, args: dict[str, Any]) -> Judged:
        return Judged("send_ask", "ok", "asked")

    def done(self, args: dict[str, Any]) -> Judged:
        return Judged("done", "ok", "not met yet")

    def blocked(self, args: dict[str, Any]) -> Judged:
        return Judged("blocked", "ok", "", ending=Ending("blocked", {}))

    def after_action(self, args: dict[str, Any]) -> Judged:
        return Judged("after_action", "ok", "", ending=Ending("reviewed", {}))

    def standing_said(self) -> str:
        return ""

    def bench_said(self, argv: list[str], ran: Ran, record: dict[str, Any] | None) -> None:
        self.bench.append(argv)


class NoBox:
    """A box that says what it was asked and runs nothing: it's for the host-side logic."""

    def __init__(self, output: str = "", code: int = 0) -> None:
        self.output, self.code = output, code
        self.asked: list[list[str]] = []

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(["python", script])
        return Ran(self.code, self.output, 0.1)

    def bench(self, args: list[str], where: Path | None = None, timeout: int | None = None) -> Ran:
        self.asked.append(["bench", *args])
        return Ran(self.code, self.output, 0.1)

    def helper(self, module: str, args: list[str], timeout: int = 120) -> Ran:
        self.asked.append(["helper", module, *args])
        return Ran(self.code, self.output, 0.1)


def desk_tools(tmp_path: Path, box: Any = None) -> WorkTools:
    found: Any = box or NoBox()
    return WorkTools(tmp_path, found, Desk())


@pytest.mark.parametrize(
    ("name", "arguments", "outcome", "why"),
    [
        ("nope", "{}", "malformed", "no_tool"),
        ("write", "not json", "malformed", "not_an_object"),
        ("write", '{"path": "a", "content": "b", "extra": 1}', "malformed", "unknown"),
        ("write", '{"path": "a"}', "malformed", "missing"),
        ("write", '{"path": 1, "content": "b"}', "ill_typed", "type"),
        ("read", '{"path": "a", "start": true}', "ill_typed", "type"),
        ("read", '{"path": "missing"}', "failed", "no_file"),
        ("read", '{"path": "../x"}', "failed", "outside"),
        ("write", '{"path": "../x", "content": "b"}', "failed", "outside"),
        ("run", '{"path": "missing.py"}', "failed", "no_file"),
        ("edit", '{"path": "missing.py", "old": "a", "new": "b"}', "failed", "edit.no_file"),
        ("skill", '{"name": "nope"}', "failed", "unknown"),
        ("lookup", '{"query": "  "}', "failed", "not_a_query"),
        ("docs", '{"query": "x", "page": 0}', "failed", "not_a_page"),
        ("bench", '{"command": "\\""}', "failed", "not_a_command"),
        ("bench", '{"command": ""}', "failed", "not_a_command"),
    ],
)
def test_each_call_is_judged_and_told_back(
    tmp_path: Path, name: str, arguments: str, outcome: str, why: str
) -> None:
    judged = desk_tools(tmp_path).call(name, arguments)
    assert (judged.outcome, judged.why) == (outcome, why)
    assert judged.reply


def test_run_sent_as_text_is_read_as_the_boolean_it_names(tmp_path: Path) -> None:
    box = NoBox("hello")
    judged = desk_tools(tmp_path, box).call(
        "write", json.dumps({"path": "a.py", "content": "x\n", "run": "true"})
    )
    assert judged.outcome == "ok"
    assert box.asked == [["python", "a.py"]]
    assert "\n\nRan a.py: exit 0" in judged.reply


def test_a_list_sent_as_its_json_text_is_read_as_the_list(tmp_path: Path) -> None:
    judged = desk_tools(tmp_path).call("needs", json.dumps({"needs": '[{"id": "a"}]'}))
    assert (judged.outcome, judged.reply) == ("ok", "1 needs")


def test_a_refused_run_after_changes_nothing(tmp_path: Path) -> None:
    judged = desk_tools(tmp_path).call(
        "write", json.dumps({"path": "a.py", "content": "x\n", "run_script": "missing.py"})
    )
    assert judged.why == "no_file"
    assert not (tmp_path / "a.py").exists()


@pytest.mark.parametrize(
    ("text", "old", "new", "why"),
    [
        ("a\nb\na\n", "a", "c", "edit.twice"),
        ("alpha\n", "alp", "c", "edit.part_of_a_line"),
        ("c\n", "a", "c", "edit.already"),
        ("alpha\nbeta\n", "alphx", "c", "edit.closest"),
        ("alpha\n", "zzzzzz", "c", "edit.not_found"),
        ("alpha\n", "alpha", "alpha", "edit.same"),
        ("alpha\n", "  ", "c", "edit.empty"),
        ("x = 1\n", "x = 1", "x = (", "edit.parse_guard"),
    ],
)
def test_edit_refuses_under_its_own_rule(
    tmp_path: Path, text: str, old: str, new: str, why: str
) -> None:
    (tmp_path / "a.py").write_text(text)
    judged = desk_tools(tmp_path).call("edit", json.dumps({"path": "a.py", "old": old, "new": new}))
    assert (judged.outcome, judged.why) == ("failed", why)
    assert (tmp_path / "a.py").read_text() == text


def test_edit_replaces_whole_lines_and_shows_them(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    judged = desk_tools(tmp_path).call(
        "edit", json.dumps({"path": "a.py", "old": "\nx = 1\n", "new": "x = 3"})
    )
    assert judged.outcome == "ok"
    assert (tmp_path / "a.py").read_text() == "x = 3\ny = 2\n"
    assert "x = 3" in judged.reply


def test_read_says_when_it_shows_the_whole_file(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("\n".join(str(n) for n in range(500)) + "\n")
    wt = desk_tools(tmp_path)
    assert wt.call("read", '{"path": "a.txt"}').detail == {"whole": False}
    assert wt.call("read", '{"path": "a.txt", "start": 1, "end": 500}').detail == {"whole": False}
    assert "(more: `start` 401)" in wt.call("read", '{"path": "a.txt"}').reply
    (tmp_path / "b.txt").write_text("one\n")
    assert wt.call("read", '{"path": "b.txt"}').detail == {"whole": True}
    (tmp_path / "c.step").write_text("ISO")
    assert wt.call("read", '{"path": "c.step"}').why == "not_text"


def test_bench_is_any_bench_command_and_its_answer_goes_back(tmp_path: Path) -> None:
    box = NoBox("verdict: OK: fine\nsecond check: yes\nsaved: bench-out/a.txt and .json\n")
    (tmp_path / "bench-out").mkdir()
    (tmp_path / "bench-out" / "a.json").write_text(
        json.dumps({"verdict": "OK", "command": "info", "files": []})
    )
    desk = Desk()
    wt = WorkTools(tmp_path, box, desk)  # type: ignore[arg-type]
    judged = wt.call("bench", '{"command": "bench info \\"a b.step\\""}')
    assert judged.outcome == "ok"
    assert judged.reply.startswith("exit 0\nverdict: OK")
    assert box.asked == [["bench", "info", "a b.step"]]
    assert desk.bench == [["info", "a b.step"]]


def test_a_render_whose_picture_goes_through_a_link_shows_nothing(tmp_path: Path) -> None:
    secret = tmp_path / "outside.png"
    secret.write_bytes(b"secret")
    work = tmp_path / "work"
    (work / "bench-out").mkdir(parents=True)
    (work / "renders").symlink_to(tmp_path)
    (work / "bench-out" / "a.json").write_text(
        json.dumps({"command": "render", "files": ["renders/outside.png"]})
    )
    box = NoBox("verdict: OK: x\nsaved: bench-out/a.txt and .json\n")
    judged = desk_tools(work, box).call("bench", '{"command": "render a.step"}')
    assert judged.images == []


# The skills


def test_the_ported_skills_are_the_measured_text() -> None:
    assert skills.sha(("cadquery", "references")) == MEASURED_SKILLS_SHA


def test_the_ported_skills_match_their_source_where_it_is_present() -> None:
    if not CAD_EVAL_SKILLS.is_dir():
        pytest.skip("mqucifer/model-performance isn't on this machine")
    for name in ("cadquery", "references"):
        ours = skills.SKILLS / name
        for path in sorted(p for p in ours.rglob("*.md")):
            theirs = CAD_EVAL_SKILLS / name / path.relative_to(ours)
            assert path.read_bytes() == theirs.read_bytes(), path


def test_each_skill_has_a_name_a_line_and_a_body() -> None:
    found = skills.registry(skills.ALL)
    assert {"using-the-bench", "cadquery", "references", "designing-stained-glass"} <= set(found)
    for name, (about, body) in found.items():
        assert about.strip() and body.strip(), name
        assert f"`{name}`" in skills.start_line(skills.ALL)


@pytest.mark.parametrize("toolset", [toolsets.CORE, toolsets.STAINED_GLASS])
def test_a_toolsets_skills_name_every_command_it_has_and_no_other(
    toolset: toolsets.Toolset,
) -> None:
    """The core's `bench` skill says what each core command answers, and a kind's skill each of
    its own: a run with the core only isn't told of `capture`."""
    bodies = "\n".join(body for _, body in skills.registry(toolset.skills).values())
    for command in toolset.commands:
        assert f"`{command} " in bodies, command
    core = skills.registry(toolsets.CORE.skills)["using-the-bench"][1]
    for command in toolsets.STAINED_GLASS_COMMANDS:
        assert f"`{command} " not in core, command


# Needs


def bench_need(**check: Any) -> dict[str, Any]:
    return {
        "id": "fit",
        "what": "it fits",
        "source": "chosen",
        "check": {"by": "bench", "command": "clearance a.step b.step", **check},
    }


def test_a_need_is_held_to_its_shape() -> None:
    found, wrong = needs.read([bench_need()], "")
    assert not wrong
    assert found[0].files() == ["a.step", "b.step"]
    for bad, says in (
        ("nope", "isn't JSON"),
        ([], "at least one"),
        (["x"], "object"),
        ([{**bench_need(), "id": "a b"}], "`id`"),
        ([bench_need(), bench_need()], "two needs"),
        ([{**bench_need(), "what": ""}], "`what`"),
        ([{**bench_need(), "source": "vibes"}], "`source`"),
        ([{**bench_need(), "check": "no"}], "`check`"),
        ([{**bench_need(), "check": {"by": "magic"}}], "bench or eye"),
        ([{**bench_need(), "check": {"by": "eye"}}], "`look_for`"),
        ([{**bench_need(), "check": {"by": "bench", "command": "fly a"}}], "bench command"),
        ([bench_need(verdicts=["NO ANSWER"])], "no answer"),
        ([bench_need(number="gap")], "needs its limit"),
        ([bench_need(at_least=1)], "needs the `number`"),
        ([bench_need(number="gap", at_least="wide")], "one number"),
        ([{**bench_need(), "extra": 1}], "only id"),
    ):
        assert any(says in w for w in needs.read(bad, "")[1]), says


def test_a_need_cant_pass_on_the_benchs_fail() -> None:
    """On `cup-1` a need passed on `capture`'s FAIL."""
    wrong = needs.read([bench_need(verdicts=["FAIL"], number="least_lip_mm", at_least=1.0)], "")[1]
    assert len(wrong) == 1
    assert "FAIL is the bench saying what `clearance` checks is wrong" in wrong[0]
    assert not needs.read([bench_need(verdicts=["OK", "TOUCHING"])], "")[1]


def test_a_quote_from_the_sponsor_is_held_to_their_words() -> None:
    said = "It should have a **0.2mm** gap all round, and   45 degrees on the chamfer."
    quoted = {**bench_need(), "source": "request", "quote": "0.2 mm gap all round"}
    assert not needs.read([quoted], said)[1]
    assert not needs.read([{**quoted, "quote": "45° on the chamfer"}], said)[1]
    wrong = needs.read([{**quoted, "quote": "a 0.5 mm gap"}], said)[1]
    assert 'quotes "a 0.5 mm gap"' in wrong[0]
    assert "give its `quote`" in needs.read([{**quoted, "quote": ""}], said)[1][0]


def judged(need: Need, code: int, verdict: str, numbers: dict[str, Any] | None = None) -> Any:
    record = {"verdict": verdict, "why": "because", "numbers": numbers or {}}
    return needs.judge(need, code, f"verdict: {verdict}: because\n", record)


def test_a_need_passes_only_on_the_benchs_own_answer() -> None:
    need = Need("a", "x", "chosen", "bench", command="info a.step")
    assert judged(need, 0, "OK").passed
    assert not judged(need, 1, "FAIL").passed
    assert not judged(need, 2, "DISAGREE").passed
    assert not judged(need, 2, "NO ANSWER").passed
    wrong = needs.judge(need, 3, "usage error: nope\n", None)
    assert not wrong.passed
    assert "the check's own command is wrong" in wrong.why
    assert not needs.judge(need, 0, "", None).passed


def test_a_press_fit_may_ask_for_a_verdict_that_would_otherwise_fail() -> None:
    need = Need("a", "x", "chosen", "bench", command="clearance a.step", verdicts=["INTERFERE"])
    assert judged(need, 1, "INTERFERE").passed
    assert not judged(need, 0, "CLEAR").passed


def test_a_number_is_held_to_its_limit_and_read_from_the_answer() -> None:
    need = Need(
        "a",
        "x",
        "chosen",
        "bench",
        command="clearance a.step b.step",
        number="pairs.0.gap",
        at_least=0.2,
        at_most=0.6,
    )
    assert judged(need, 0, "CLEAR", {"pairs": [{"gap": 0.3}]}).value == 0.3
    assert judged(need, 0, "CLEAR", {"pairs": [{"gap": 0.3}]}).passed
    assert not judged(need, 0, "CLEAR", {"pairs": [{"gap": 0.1}]}).passed
    assert not judged(need, 0, "CLEAR", {"pairs": [{"gap": 0.7}]}).passed
    assert "has no number" in judged(need, 0, "CLEAR", {"other": 1}).why
    assert "not a number" in judged(need, 0, "CLEAR", {"pairs": [{"gap": None}]}).why
    assert "not a number" in judged(need, 0, "CLEAR", {"pairs": [{"gap": True}]}).why


# The session's records


class Echo:
    """Tools that answer everything, for what the session does around them."""

    specs = [tools.specs()[0]]

    def __init__(self, images: list[tuple[str, str]] | None = None) -> None:
        self.images = images or []

    def call(self, name: Any, raw_args: Any) -> Judged:
        if name == "stop":
            return Judged("stop", "ok", "stopped", ending=Ending("blocked", {"reason": "x"}))
        whole = name == "read"
        images = self.images if name == "see" else []
        return Judged(str(name), "ok", f"{name} did it", images=images, detail={"whole": whole})


def session(tmp_path: Path, model: Scripted, tools_: Any = None, **limits: Any) -> Session:
    s = Session(
        tools=tools_ or Echo(),
        send=model,
        alias="crew-code-think",
        events=tmp_path / "events.jsonl",
        limits=Limits(**limits),
        sleep=lambda _: None,
    )
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": "go"})
    return s


def test_every_request_rebuilds_from_the_records(tmp_path: Path) -> None:
    picture = ("p.png", "aGVsbG8=")
    model = Scripted(
        [tool("write", path="a.py", content="x" * 500)],
        [tool("see")],
        [tool("see")],
        [tool("edit", path="a.py", old="x", new="y")],
        "no call",
        [tool("stop")],
    )
    s = session(tmp_path, model, Echo([picture]))
    assert s.go().name == "blocked"
    for n, body in enumerate(model.bodies, 1):
        rebuilt = transcript.rebuild(s.events, n)
        assert rebuilt["messages"] == body["messages"], n
        assert rebuilt["tools"] == body["tools"]


def test_reasoning_goes_back_as_it_came(tmp_path: Path) -> None:
    model = Scripted([tool("x")], [tool("stop")])
    session(tmp_path, model).go()
    said = [m for m in model.bodies[1]["messages"] if m["role"] == "assistant"]
    assert said[0]["reasoning_content"] == "thinking 1"


def test_only_the_latest_pictures_stay_and_each_is_kept(tmp_path: Path) -> None:
    model = Scripted([tool("see")], [tool("see")], [tool("stop")])
    s = session(tmp_path, model, Echo([("p.png", "aGVsbG8=")]))
    s.go()
    last = model.bodies[-1]["messages"]
    pictures = [m for m in last if isinstance(m.get("content"), list)]
    assert len(pictures) == 1
    assert SHOWN_BEFORE in [m.get("content") for m in last]
    assert (tmp_path / "views" / "2.png").read_bytes() == b"hello"


def test_every_call_in_a_turn_gets_its_reply_even_after_a_stop(tmp_path: Path) -> None:
    model = Scripted([tool("stop"), tool("x"), tool("y")], [tool("x")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    called = [m for m in s.messages if m["role"] == "tool"]
    assert len(called) == 3
    assert s.by_tool["x"] == 0, "what comes after a stop isn't carried out"


def test_compaction_keeps_the_script_the_sponsor_and_the_latest_answers(tmp_path: Path) -> None:
    model = Scripted(
        [tool("write", path="a.py", content="OLD" * 100)],
        [tool("write", path="a.py", content="NEW" * 100)],
        [tool("run", path="a.py")],
        [tool("bench", command="info")],
        [tool("run", path="a.py")],
        [tool("x")],
        [tool("stop")],
    )
    s = session(tmp_path, model)
    s.say("The Sponsor's answers: keep me")
    s.go()
    s.compact()
    text = json.dumps(s.messages)
    assert "keep me" in text
    assert "NEW" * 100 in text, "the file's latest full text stays"
    assert "OLD" * 100 not in text
    assert "thinking 1" not in text, "reasoning goes before the newest turn"
    assert "thinking 7" in text
    replies = [m["content"] for m in s.messages if m["role"] == "tool"]
    assert replies[2].startswith(COMPACTED.split("{")[0]), "an earlier run's reply shrinks"
    assert replies[3] == "bench did it", "the latest bench answer stays"
    assert replies[4] == "run did it", "the latest run's reply stays"
    rebuilt_up_to = transcript.conversation(s.events)
    assert rebuilt_up_to, "the rewrites are in the records"


def test_a_conversation_that_wont_fit_says_so_and_is_compacted_first(tmp_path: Path) -> None:
    model = Scripted([tool("x")], prompt_tokens=500)
    s = session(tmp_path, model, compact_at=400)
    assert s.go().name == "context_full"
    assert s.compactions == 1
    assert [e for e in transcript.events_of(s.events) if e["kind"] == "compacted"]


def test_a_failed_call_is_tried_again_then_ended_by_name(tmp_path: Path) -> None:
    request = httpx.Request("POST", "http://x")
    busy = httpx.HTTPStatusError("busy", request=request, response=httpx.Response(503))
    model = Scripted([tool("stop")])
    model.failures = [busy, httpx.ConnectError("no")]
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    retries = [e for e in transcript.events_of(s.events) if e["kind"] == "call_retry"]
    assert len(retries) == 2

    model = Scripted([tool("stop")])
    model.failures = [busy] * 3
    (tmp_path / "again").mkdir()
    s = session(tmp_path / "again", model, call_retries=2)
    ending = s.go()
    assert (ending.name, ending.said["status"]) == ("call_failed", 503)


def test_a_400_isnt_tried_again_and_says_only_its_status(tmp_path: Path) -> None:
    request = httpx.Request("POST", "http://x")
    bad = httpx.HTTPStatusError("x", request=request, response=httpx.Response(400, text="SECRET"))
    model = Scripted()
    model.failures = [bad]
    s = session(tmp_path, model)
    ending = s.go()
    assert ending.name == "call_failed"
    assert "SECRET" not in s.events.read_text()


@pytest.mark.parametrize(
    ("limits", "turns", "name"),
    [
        ({"calls": 1}, [[tool("x")], [tool("x")]], "call_cap"),
        ({"quiet": 2}, ["one", "two"], "no_tool_call"),
        ({"model_seconds": 0}, [], "model_time"),
    ],
)
def test_each_limit_ends_by_name(
    tmp_path: Path, limits: dict[str, Any], turns: list[Any], name: str
) -> None:
    s = session(tmp_path, Scripted(*turns), **limits)
    assert s.go().name == name
    ended = [e for e in transcript.events_of(s.events) if e["kind"] == "ended"]
    assert ended[-1]["name"] == name


def test_a_stop_file_stops_it_before_the_next_call(tmp_path: Path) -> None:
    s = session(tmp_path, Scripted())
    s.stop_file = tmp_path / "stop"
    s.stop_file.touch()
    assert s.go().name == "stopped"


def test_a_session_comes_back_from_its_events(tmp_path: Path) -> None:
    model = Scripted([tool("write", path="a.py", content="x")], [tool("stop")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    again = Scripted([tool("x")], [tool("stop")])
    back = Session(
        tools=Echo(),
        send=again,
        alias="crew-code-think",
        events=s.events,
        limits=Limits(),
        sleep=lambda _: None,
    )
    back.restore()
    assert back.messages == s.messages
    assert (back.calls, back.requests, back.by_tool["write"]) == (2, 2, 1)
    assert back.replies == s.replies
    back.say("Go on.")
    assert back.go().name == "blocked"
    assert again.bodies[0]["messages"][: len(s.messages)] == s.messages
    assert again.bodies[0]["messages"][len(s.messages)] == {"role": "user", "content": "Go on."}
    for n in range(1, 5):
        rebuilt = transcript.rebuild(s.events, n)
        body = (model.bodies + again.bodies)[n - 1]
        assert rebuilt["messages"] == body["messages"], n


@pytest.mark.parametrize("cut_off", ["", '{"path": "a.py", "cont'])
def test_a_cut_off_tool_call_goes_back_as_json_and_is_told_as_malformed(
    tmp_path: Path, cut_off: str
) -> None:
    model = Scripted([raw("write", cut_off)], [tool("stop")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    sent = model.bodies[1]["messages"]
    called = next(m for m in sent if m.get("tool_calls"))
    assert called["tool_calls"][0]["function"]["arguments"] == "{}"
    for n in (1, 2):
        assert transcript.rebuild(s.events, n)["messages"] == model.bodies[n - 1]["messages"]
    came = [e for e in transcript.events_of(s.events) if e.get("kind") == "tool"]
    assert came[0]["arguments"] == cut_off, "the record keeps what the model sent"


def test_a_run_that_stopped_on_a_cut_off_call_can_resume(tmp_path: Path) -> None:
    model = Scripted([raw("write", "")], [tool("stop")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    # As a run from before the fix left it: the bad arguments in its records.
    for i, m in enumerate(s.messages):
        if m.get("tool_calls"):
            bad = {
                **m,
                "tool_calls": [
                    {**m["tool_calls"][0], "function": {"name": "write", "arguments": ""}}
                ],
            }
            s.messages[i] = bad
            s._record("rewrite", index=i, message=bad)
    again = Scripted([tool("stop")])
    back = Session(
        tools=Echo(),
        send=again,
        alias="crew-code-think",
        events=s.events,
        limits=Limits(),
        sleep=lambda _: None,
    )
    back.restore()
    assert back.go().name == "blocked"
    for m in again.bodies[0]["messages"]:
        for call in m.get("tool_calls") or []:
            json.loads(call["function"]["arguments"])
    last = transcript.rebuild(s.events, 3)["messages"]
    assert last == again.bodies[0]["messages"]


def test_a_new_conversation_after_one_that_wouldnt_fit_still_rebuilds(tmp_path: Path) -> None:
    model = Scripted([tool("x")], [tool("stop")])
    s = session(tmp_path, model)
    s.limits = Limits(calls=1)
    assert s.go().name == "call_cap"
    s.limits = Limits()
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": "again"})
    assert s.go().name == "blocked"
    assert transcript.rebuild(s.events, 2)["messages"][1]["content"] == "again"
    assert transcript.rebuild(s.events, 1)["messages"][1]["content"] == "go"


# The sender


def stream(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def test_a_streamed_call_is_put_back_together(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer the-key"
        return httpx.Response(
            200,
            headers={
                "x-litellm-model-id": "abc",
                "x-litellm-model-api-base": "http://an-address:8888",
                "x-litellm-key-spend": "1",
            },
            content=stream(
                {"id": "r", "choices": [{"delta": {"reasoning_content": "hm"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {"name": "run", "arguments": '{"pa'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": 'th": "a"}'}}]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                {"usage": {"prompt_tokens": 5}, "choices": []},
            ),
        )

    sender = send.Sender("the-key", transport=httpx.MockTransport(answer))
    got = sender({"model": "m", "messages": []})
    assert seen[0]["stream"] is True
    assert seen[0]["stream_options"] == {"include_usage": True}
    message = got["choices"][0]["message"]
    assert message["reasoning_content"] == "hm"
    assert message["tool_calls"][0]["function"] == {"name": "run", "arguments": '{"path": "a"}'}
    assert got["usage"] == {"prompt_tokens": 5}
    assert sender.served == {"x-litellm-model-id": "abc"}, "no address, no key, no spend"


def test_whitespace_running_on_is_cut_as_crews_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        spaces = {"choices": [{"delta": {"content": " " * send.RUNAWAY_CHARS}}]}
        return httpx.Response(
            200, content=stream(spaces, {"choices": [{"delta": {"content": "x"}}]})
        )

    got = send.Sender("k", transport=httpx.MockTransport(answer))({"messages": []})
    assert got["choices"][0]["finish_reason"] == "runaway"


def test_a_failed_call_holds_only_its_status() -> None:
    def answer(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="the request echoed")

    with pytest.raises(httpx.HTTPStatusError) as caught:
        send.Sender("k", transport=httpx.MockTransport(answer))({"messages": []})
    assert "echoed" not in str(caught.value)


def test_no_key_refuses_before_any_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(send, "SETTINGS_PATH", tmp_path / "none")
    monkeypatch.delenv("CREW_LLM_API_KEY", raising=False)
    with pytest.raises(LookupError):
        send.Sender()


# The ledger


def test_the_ledger_gives_each_needs_reading_before_and_after(tmp_path: Path) -> None:
    ledger = DesignLedger(tmp_path / "ledger.sqlite")
    ledger.run_started(
        "r",
        request_sha="s",
        events="e",
        harness_commit="c",
        skills_sha="k",
        image="i",
        image_id="d",
        alias="a",
        limits={"calls": 1},
    )
    need = Need("gap", "x", "chosen", "bench", command="c", number="n", at_least=1.0)
    ledger.needs_accepted("r", 1, [need], 3)
    for n, value, passed in ((1, 0.5, False), (2, 1.5, True)):
        ledger.round_started("r", n, n, 1, {"a": "b"}, [] if n == 1 else ["gap"])
        ledger.reading(
            "r",
            n,
            need,
            needs.Reading(
                "gap", "bench", passed, "why", 0, "OK", value, "", {"numbers": {"n": value}}
            ),
        )
        ledger.round_ended(
            "r", n, n, "a.py", "the diff", {"a": "c"}, 0, 1.0, passed, "made it wider"
        )
    rows = ledger.rows("SELECT * FROM changes WHERE run='r' ORDER BY round")
    assert rows[0]["before_value"] is None
    assert (rows[1]["before_value"], rows[1]["after_value"]) == (0.5, 1.5)
    assert (rows[1]["before_passed"], rows[1]["after_passed"]) == (0, 1)
    assert rows[1]["diff"] == "the diff"
    assert rows[1]["fix_shape_model_said"] == "made it wider"
    assert ledger.rows("SELECT limits FROM runs")[0]["limits"] == '{"calls": 1}'


def test_a_ledger_from_before_a_new_column_gains_it_and_keeps_its_rows(tmp_path: Path) -> None:
    """The real ledger has a row from the first run, so a column can't only be in the schema."""
    path = tmp_path / "ledger.sqlite"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE runs (run TEXT PRIMARY KEY);"
        "INSERT INTO runs VALUES ('r');"
        "CREATE TABLE picks (run TEXT, ask INTEGER, pick TEXT, reason TEXT,"
        " PRIMARY KEY (run, ask));"
        "INSERT INTO picks VALUES ('r', 1, 'B', 'because');"
        "CREATE TABLE rounds (run TEXT, round INTEGER, met INTEGER, PRIMARY KEY (run, round));"
        "CREATE TABLE readings (run TEXT, round INTEGER, need TEXT, by TEXT, exit INTEGER,"
        " verdict TEXT, number TEXT, value REAL, passed INTEGER, why TEXT);"
    )
    old.close()
    for _ in range(2):  # Opening it twice adds nothing twice.
        ledger = DesignLedger(path)
    row = ledger.rows("SELECT * FROM picks")[0]
    assert (row["pick"], row["reason"]) == ("B", "because")
    assert row["picked_by"] is None, "nothing says who it was, so nothing says it was the Sponsor"


def test_a_pick_says_who_picked_in_plain_words() -> None:
    assert run_module.picked_by_words("sponsor") == "The Sponsor"
    assert run_module.picked_by_words("stand-in: a Claude builder") == (
        "A stand-in for the Sponsor (a Claude builder)"
    )


def test_the_ledger_is_its_own_file_and_refuses_an_unknown_run(tmp_path: Path) -> None:
    ledger = DesignLedger(tmp_path / "ledger.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        ledger.round_started("nobody", 1, 1, 1, {}, [])
