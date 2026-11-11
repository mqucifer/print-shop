"""A call that ends without acting is asked what's missing, and compaction stops resending what
never finished (mqucifer/print-shop#47). The scripted model stands in for the served one: no model
call anywhere."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import tools, transcript
from print_shop.rounds.session import (
    COPY_AT,
    CUT_OFF,
    NOT_NOW,
    QUIET,
    Ending,
    Judged,
    Limits,
    Session,
)
from tests.rounds_fakes import Scripted, Spoken, tool


class Desk:
    """Every tool the run offers, each answered, with what it was called with kept."""

    specs = tools.specs()

    def __init__(self) -> None:
        self.called: list[tuple[str, Any]] = []

    def call(self, name: Any, raw: Any) -> Judged:
        self.called.append((name, raw))
        if name == "send_ask":
            return Judged("send_ask", "ok", "asked", ending=Ending("ask", {"n": 1}))
        if name == "blocked":
            return Judged("blocked", "ok", "", ending=Ending("blocked", {}))
        whole = name == "read"
        return Judged(str(name), "ok", f"{name} did it", detail={"whole": whole})


class Review(Desk):
    """The after-action review's tools: no `ask` and no `blocked`."""

    specs = [s for s in tools.specs() if s["function"]["name"] == "read"]


def session(tmp_path: Path, model: Scripted, desk: Any = None, **limits: Any) -> Session:
    s = Session(
        tools=desk or Desk(),
        send=model,
        alias="crew-code-think",
        events=tmp_path / "events.jsonl",
        limits=Limits(**limits),
        sleep=lambda _: None,
    )
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": "go"})
    return s


def offered(body: dict[str, Any]) -> list[str]:
    return [s["function"]["name"] for s in body["tools"]]


#: What the lock offers before its allowance is spent: the asking tools, and once each a build, a
#: run and a check (mqucifer/print-shop#86), in the order the specs give them.
LOCKED = ["write", "edit", "run", "bench", "ask", "send_ask", "blocked"]


def events(s: Session, kind: str) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(s.events) if e["kind"] == kind]


def rebuilds(s: Session, model: Scripted) -> None:
    for n, body in enumerate(model.bodies, 1):
        rebuilt = transcript.rebuild(s.events, n)
        assert rebuilt["messages"] == body["messages"], n
        assert rebuilt["tools"] == body["tools"], n


# As LiteLLM reports a call's reasoning.
THINKING = Spoken(
    "I keep going back and forth.",
    usage={"completion_tokens_details": {"reasoning_tokens": 21_340}},  # type: ignore[dict-item]
)


# What a call that ends without acting is told


def test_a_call_that_ends_without_acting_is_asked_whats_missing(tmp_path: Path) -> None:
    model = Scripted(THINKING, [tool("blocked", reason="x")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    said = model.bodies[1]["messages"][-1]
    assert said["role"] == "user"
    assert "You reasoned for 21,340 tokens" in said["content"]
    assert "something is missing: a figure, a reading" in said["content"]
    assert "`ask`" in said["content"] and "`blocked`" in said["content"]
    assert said["content"] != QUIET
    assert offered(model.bodies[1]) == offered(model.bodies[0]), "the first time, every tool"
    (kept,) = events(s, "quiet_reply")
    assert (kept["n"], kept["reasoning_tokens"], kept["only"]) == (1, 21_340, None)
    assert kept["reply"] == said["content"]


def test_a_second_one_soon_after_is_offered_only_ask_and_blocked_until_it_asks(
    tmp_path: Path,
) -> None:
    model = Scripted(
        THINKING,
        [tool("run", path="a.py")],
        THINKING,
        [tool("run", path="a.py")],
        [tool("ask", question="Which way should the lip go?", plan="x")],
        [tool("send_ask", says="the plan")],
    )
    desk = Desk()
    s = session(tmp_path, model, desk)
    assert s.go().name == "ask"
    every = offered(model.bodies[0])
    assert offered(model.bodies[2]) == every
    assert offered(model.bodies[3]) == LOCKED
    for n in (4, 5):
        assert offered(model.bodies[n]) == ["write", "edit", "bench", "ask", "send_ask", "blocked"]
    assert "It's happened again, 2 calls after the last time" in str(
        model.bodies[3]["messages"][-1]["content"]
    )
    ran = str(model.bodies[4]["messages"][-1]["content"])
    assert ran.startswith("run did it\n\n(Under the lock: you may still build one change")
    assert [name for name, _ in desk.called] == ["run", "run", "ask", "send_ask"]
    (_, forced) = events(s, "quiet_reply")
    assert forced["only"] == ["ask", "send_ask", "blocked"]
    assert forced["allowed"] == ["write", "edit", "run", "bench"]
    assert forced["after"] == 2
    assert s.only == (), "a stop ends it"
    rebuilds(s, model)


def test_the_lock_leaves_room_for_one_build_and_one_check_then_only_asking(
    tmp_path: Path,
) -> None:
    """On `eval-fan-3` the lock stopped a model that had twice decided to build a lip and read the
    slide, and four calls with no tool followed (mqucifer/print-shop#86). Made up here: an edit,
    a check, then a second edit, a `read` and a second check, all refused."""
    model = Scripted(
        THINKING,
        [tool("run", path="a.py")],
        THINKING,
        [tool("edit", path="a.py", old="1", new="2", run=True)],
        [tool("bench", command="clearance lid.step references/fan.step --slide -z")],
        [tool("edit", path="a.py", old="2", new="3", run=True)],
        [tool("read", path="a.py")],
        [tool("bench", command="info lid.step")],
        THINKING,
        [tool("ask", question="The slide reads NOT HELD at the lip. Which way?", plan="x")],
        [tool("send_ask", says="the plan")],
    )
    desk = Desk()
    s = session(tmp_path, model, desk)
    assert s.go().name == "ask"
    locked = str(model.bodies[3]["messages"][-1]["content"])
    assert "you may build one change and read one check, then ask or end" in locked
    assert "one `write` or `edit` (`run: true` runs it, or one `run`), and one `bench`" in locked
    assert offered(model.bodies[3]) == LOCKED
    built, read_, again, other, twice = (
        str(model.bodies[n]["messages"][-1]["content"]) for n in (4, 5, 6, 7, 8)
    )
    assert built == (
        "edit did it\n\n(Under the lock: you may still `run` it and read one check with "
        "`bench`. Then `ask` what's missing, and `send_ask`; or end with `blocked`.)"
    )
    assert offered(model.bodies[4]) == ["run", "bench", "ask", "send_ask", "blocked"]
    assert read_.endswith(
        "(Under the lock: you may still `run` it. Then `ask` what's missing, and `send_ask`; or "
        "end with `blocked`.)"
    )
    assert offered(model.bodies[5]) == ["run", "ask", "send_ask", "blocked"]
    assert again == (
        "Not carried out: under the lock you've had your one build. Now `ask` what's missing, "
        "showing what you have, then `send_ask`; or end with `blocked`, saying what's missing."
    )
    assert other == NOT_NOW
    assert twice.startswith("Not carried out: under the lock you've had your one bench check.")
    still = str(model.bodies[9]["messages"][-1]["content"])
    assert still.endswith(
        "Only `ask`, `send_ask` and `blocked` work until you've asked or ended. Besides them, you "
        "may still `run` it, once, then ask or end."
    )
    assert [name for name, _ in desk.called] == ["run", "edit", "bench", "ask", "send_ask"]
    whys = [(e["name"], e["why"]) for e in events(s, "tool") if e["outcome"] == "failed"]
    assert whys == [("edit", "ask_only_spent"), ("read", "ask_only"), ("bench", "ask_only_spent")]
    assert (s.only, s.spent) == ((), set()), "a stop ends the lock and its allowance"
    rebuilds(s, model)

    back = Session(
        tools=Desk(), send=Scripted(), alias="crew-code-think", events=s.events, sleep=print
    )
    back.restore()
    assert (back.only, back.spent) == ((), set())


def test_a_session_back_from_its_events_keeps_what_the_locks_allowance_has_had(
    tmp_path: Path,
) -> None:
    model = Killed(
        THINKING, THINKING, [tool("edit", path="a.py", old="1", new="2")], THINKING
    )
    model.stop_at = 4
    s = session(tmp_path, model)
    with pytest.raises(KeyboardInterrupt):
        s.go()
    again = Scripted([tool("blocked", reason="x")])
    back = Session(
        tools=Desk(), send=again, alias="crew-code-think", events=s.events, sleep=lambda _: None
    )
    back.restore()
    assert back.spent == {"build"}
    assert back.go().name == "blocked"
    assert offered(again.bodies[0]) == ["run", "bench", "ask", "send_ask", "blocked"]


def test_a_quiet_call_long_after_the_last_is_only_asked(tmp_path: Path) -> None:
    model = Scripted(THINKING, [tool("x")], [tool("x")], THINKING, [tool("blocked", reason="x")])
    s = session(tmp_path, model, ask_within=2)
    assert s.go().name == "blocked"
    assert offered(model.bodies[4]) == offered(model.bodies[0])
    assert [e["only"] for e in events(s, "quiet_reply")] == [None, None]


def test_the_limit_is_recorded_on_the_run() -> None:
    assert Limits().recorded()["ask_within"] == 15


def test_where_theres_no_ask_its_told_to_call_a_tool_as_before(tmp_path: Path) -> None:
    model = Scripted("hmm", "hmm", [tool("read", path="a.py")], "hmm")
    s = session(tmp_path, model, Review(), quiet=10, calls=4)
    assert s.go().name == "call_cap"
    told = [m["content"] for m in s.messages if m["role"] == "user"][1:]
    assert told == [QUIET, QUIET, QUIET]
    assert all(offered(b) == ["read"] for b in model.bodies)


def test_the_quiet_ending_stays_as_it_is(tmp_path: Path) -> None:
    model = Scripted(THINKING, THINKING, THINKING)
    s = session(tmp_path, model, quiet=3)
    assert s.go().name == "no_tool_call"


class Killed(Scripted):
    """The process is killed during the third call, or the one `stop_at` names: no ending is
    recorded."""

    stop_at = 2

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        if len(self.bodies) == self.stop_at:
            raise KeyboardInterrupt
        return super().__call__(body)


def test_a_session_back_from_its_events_still_offers_only_the_asking_tools(
    tmp_path: Path,
) -> None:
    s = session(tmp_path, Killed(THINKING, THINKING))
    with pytest.raises(KeyboardInterrupt):
        s.go()
    again = Scripted([tool("blocked", reason="x")])
    back = Session(
        tools=Desk(), send=again, alias="crew-code-think", events=s.events, sleep=lambda _: None
    )
    back.restore()
    assert (back.quiet_at, back.only) == (2, ("ask", "send_ask", "blocked"))
    assert back.go().name == "blocked"
    assert offered(again.bodies[0]) == LOCKED


# A call cut off at the token limit


def test_a_cut_off_call_goes_back_without_its_reasoning(tmp_path: Path) -> None:
    loop = "I've been going in circles. " * 400
    model = Scripted(
        Spoken("Let me think about the lip again", reasoning=loop, finish="length"),
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    sent = model.bodies[1]["messages"]
    cut = next(m for m in sent if m["role"] == "assistant")
    assert cut["reasoning_content"] == ""
    assert cut["content"] == "Let me think about the lip again", "its text stays"
    assert CUT_OFF in sent[-1]["content"]
    (call,) = [e for e in events(s, "call") if e["n"] == 1]
    assert call["message"]["reasoning_content"] == loop, "the call's event keeps it all"
    (rewrite,) = events(s, "rewrite")
    assert rewrite["why"] == "cut_off"
    rebuilds(s, model)


def test_a_cut_off_call_with_tool_calls_keeps_them_and_loses_its_reasoning(
    tmp_path: Path,
) -> None:
    model = Scripted(
        Spoken([tool("x")], reasoning="round and round", finish="length"),
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model)
    s.go()
    cut = next(m for m in model.bodies[1]["messages"] if m["role"] == "assistant")
    assert (cut["reasoning_content"], cut["tool_calls"][0]["function"]["name"]) == ("", "x")
    rebuilds(s, model)


# A cut call keeps what its reasoning settled (mqucifer/print-shop#99)


#: Made up, in the shape `eval-pocket-4` and `eval-fan-5` were cut in: a design settled, then the
#: script written in the reasoning and cut off in the middle of it.
DRAFTING = (
    "The fan reads 40 x 40 x 10 on the bench. Let me think about the pocket again. "
    * 40
    + "\n\nOK, I'm confident now. Here's my final design spec:\n\n"
    "- pocket 40.4 x 40.4, 10.2 deep\n- bump 0.5 over the fan's edge\n\n"
    "Now the script:\n\n"
    "```python\nimport cadquery as cq\n\nlid = cq.Workplane('XY').box(50, 50, 3)\n"
    "pocket = lid.faces('>Z').workplane().rect(40.4, 40.4).cutBlind(-2)\nbump = ("
)
#: Round and round on a reading: no script anywhere.
CIRCLING = (
    "The plan: a lid with a lip, 1.2 mm, all round.\n\n"
    + "The slide says NOT HELD, but is that right? Let me read it again. " * 60
)


def cut(reasoning: str) -> Spoken:
    return Spoken("", reasoning=reasoning, finish="length")


def test_a_call_cut_while_writing_a_script_is_told_to_write_it(tmp_path: Path) -> None:
    model = Scripted(cut(DRAFTING), [tool("blocked", reason="x")])
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    said = str(model.bodies[1]["messages"][-1]["content"])
    assert "while you were writing a script in your reasoning" in said
    assert "`record/cut-reasoning-1.md`" in said
    assert "write the script with `write`, run it, and read the part on the bench" in said
    assert "something is missing" not in said, "it isn't asked what's missing"
    assert "unfinished where it was cut" in said
    kept = (tmp_path / "work" / "record" / "cut-reasoning-1.md").read_text()
    assert "pocket = lid.faces('>Z').workplane().rect(40.4, 40.4).cutBlind(-2)\nbump = (" in kept
    assert "import cadquery as cq" in kept
    assert "Here's my final design spec:" in kept and "- bump 0.5 over the fan's edge" in kept
    assert "Let me think about the pocket again" not in kept, "only the design, not the rest"
    (event,) = events(s, "quiet_reply")
    assert (event["script"], event["kept"], event["only"]) == (
        True,
        "record/cut-reasoning-1.md",
        None,
    )
    assert cut_off_reasoning(model) == "", "the reasoning still isn't sent back"
    rebuilds(s, model)


def cut_off_reasoning(model: Scripted) -> str:
    return str(
        next(m for m in model.bodies[1]["messages"] if m["role"] == "assistant")[
            "reasoning_content"
        ]
    )


def test_a_cut_call_with_no_script_is_asked_as_before_and_its_design_is_kept(
    tmp_path: Path,
) -> None:
    model = Scripted(cut(CIRCLING), [tool("blocked", reason="x")])
    s = session(tmp_path, model)
    s.go()
    said = str(model.bodies[1]["messages"][-1]["content"])
    assert said.startswith("You reasoned for ")
    assert CUT_OFF in said and "something is missing" in said
    assert "The design it last stated is kept in `record/cut-reasoning-1.md`" in said
    kept = (tmp_path / "work" / "record" / "cut-reasoning-1.md").read_text()
    assert "The plan: a lid with a lip, 1.2 mm, all round." in kept
    assert "```python" not in kept
    (event,) = events(s, "quiet_reply")
    assert (event["script"], event["kept"]) == (False, "record/cut-reasoning-1.md")


def test_a_second_cut_while_writing_a_script_doesnt_narrow_the_tools(tmp_path: Path) -> None:
    """On `eval-fan-4` the second quiet reply came after 73 minutes, narrowed the tools to the
    asking ones, and the run ended with no part (mqucifer/print-shop#99)."""
    model = Scripted(
        cut(CIRCLING),
        [tool("run", path="a.py")],
        cut(DRAFTING),
        [tool("write", path="lid.py", content="x = 1\n")],
        [tool("run", path="lid.py")],
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    every = offered(model.bodies[0])
    assert all(offered(b) == every for b in model.bodies), "every tool, every call"
    second = str(model.bodies[3]["messages"][-1]["content"])
    assert "It's happened again" not in second
    assert "`record/cut-reasoning-3.md`" in second
    assert [e["only"] for e in events(s, "quiet_reply")] == [None, None]
    assert (tmp_path / "work" / "record" / "cut-reasoning-3.md").is_file()


def test_a_second_quiet_call_with_no_script_still_narrows_the_tools(tmp_path: Path) -> None:
    model = Scripted(cut(DRAFTING), [tool("run", path="a.py")], cut(CIRCLING), *ASKED)
    s = session(tmp_path, model)
    assert s.go().name == "ask"
    assert offered(model.bodies[3]) == LOCKED


ASKED = (
    [tool("ask", question="Which way should the lip go?", plan="x")],
    [tool("send_ask", says="the plan")],
)


def test_the_kept_design_is_the_paragraph_or_list_before_the_script() -> None:
    from print_shop.rounds.cut_reasoning import settled

    kept = settled(DRAFTING)
    assert kept.script is not None and kept.script.startswith("import cadquery as cq\n")
    assert kept.unfinished
    assert kept.design.startswith("OK, I'm confident now. Here's my final design spec:")
    assert kept.design.endswith("Now the script:")
    closed = settled("Plan settled.\n\n```python\nx = 1\n```\n\nThat's it, I think.")
    assert (closed.script, closed.unfinished, closed.design) == ("x = 1\n", False, "Plan settled.")
    assert settled("Thinking.\n\nStill thinking.").design == "Still thinking."
    opened_only = settled("A design.\n\n```python\n")
    assert (opened_only.script, opened_only.design) == (None, "A design.")


def test_a_finished_call_keeps_its_reasoning(tmp_path: Path) -> None:
    model = Scripted(Spoken([tool("x")], reasoning="done thinking"), [tool("blocked", reason="")])
    session(tmp_path, model).go()
    kept = next(m for m in model.bodies[1]["messages"] if m["role"] == "assistant")
    assert kept["reasoning_content"] == "done thinking"


# One current copy of each file


SCRIPT = "a = 1\nb = 2\nc = 3\n"


def edited(tmp_path: Path, *more: Any) -> tuple[Session, Scripted]:
    model = Scripted(
        [tool("write", path="make.py", content=SCRIPT)],
        [tool("edit", path="make.py", old="b = 2", new="b = 20")],
        [tool("edit", path="make.py", old="c = 3", new="c = 30\nd = 4")],
        [tool("run", path="make.py")],
        *more,
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    return s, model


def file_text(s: Session) -> str:
    return json.dumps(s.messages)


def test_compaction_keeps_one_current_copy_of_an_edited_file(tmp_path: Path) -> None:
    s, _ = edited(tmp_path)
    s.compact()
    write = s.messages[2]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(write)["content"].startswith("(compacted: ")
    for i in (4, 6):
        args = json.loads(s.messages[i]["tool_calls"][0]["function"]["arguments"])
        assert args["old"].startswith("(compacted: ") and args["new"].startswith("(compacted: ")
    first_reply = s.messages[5]["content"]
    assert first_reply == "(compacted: edit: edit did it)"
    last_reply = s.messages[7]["content"]
    found = COPY_AT.search(last_reply)
    assert found is not None and found.group(1) == "make.py"
    assert last_reply[found.end() :] == ("    1| a = 1\n    2| b = 20\n    3| c = 30\n    4| d = 4")
    assert file_text(s).count("a = 1") == 1, "one copy"
    assert transcript.conversation(s.events) == s.messages


def test_a_later_edit_starts_from_the_copy_and_the_old_copy_goes(tmp_path: Path) -> None:
    s, model = edited(tmp_path)
    s.compact()
    model.turns = [
        [tool("edit", path="make.py", old="a = 1", new="a = 10")],
        [tool("x")],
        [tool("blocked", reason="x")],
    ]
    assert s.go().name == "blocked"
    s.compact()
    copies = [
        m["content"]
        for m in s.messages
        if m["role"] == "tool" and COPY_AT.search(str(m["content"]))
    ]
    assert len(copies) == 1
    assert copies[0].endswith("    1| a = 10\n    2| b = 20\n    3| c = 30\n    4| d = 4")
    assert s.messages[7]["content"] == "(compacted: edit: edit did it)"


def test_an_edit_in_the_newest_turn_keeps_the_text_and_its_edits_whole(tmp_path: Path) -> None:
    model = Scripted(
        [tool("write", path="make.py", content=SCRIPT)],
        [tool("edit", path="make.py", old="b = 2", new="b = 20")],
    )
    s = session(tmp_path, model, calls=2)
    assert s.go().name == "call_cap"
    s.compact()
    assert SCRIPT in json.loads(s.messages[2]["tool_calls"][0]["function"]["arguments"])["content"]
    assert not any(COPY_AT.search(str(m.get("content"))) for m in s.messages)


def test_a_copy_that_cant_be_rebuilt_keeps_the_text_and_its_edits_whole(tmp_path: Path) -> None:
    # The second edit's lines aren't in the text the conversation has: a run changed the file.
    model = Scripted(
        [tool("write", path="make.py", content=SCRIPT)],
        [tool("edit", path="make.py", old="b = 2", new="b = 20")],
        [tool("edit", path="make.py", old="e = 5", new="e = 50")],
        [tool("x")],
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model)
    s.go()
    s.compact()
    assert not any(COPY_AT.search(str(m.get("content"))) for m in s.messages)
    args = json.loads(s.messages[6]["tool_calls"][0]["function"]["arguments"])
    assert args["new"] == "e = 50", "as before: the text and every edit since stay whole"


def test_a_whole_read_is_a_files_text_to_edit_from(tmp_path: Path) -> None:
    class Reads(Desk):
        def call(self, name: Any, raw: Any) -> Judged:
            if name != "read":
                return super().call(name, raw)
            lines = SCRIPT.splitlines()
            shown = "\n".join(f"{n:5}| {line}" for n, line in enumerate(lines, 1))
            reply = f"`make.py`, lines 1-3 of 3:\n{shown}"
            return Judged("read", "ok", reply, detail={"whole": True})

    model = Scripted(
        [tool("read", path="make.py")],
        [tool("edit", path="make.py", old="b = 2", new="b = 20")],
        [tool("x")],
        [tool("blocked", reason="x")],
    )
    s = session(tmp_path, model, Reads())
    s.go()
    s.compact()
    assert s.messages[3]["content"].startswith("(compacted: read: ")
    assert s.messages[5]["content"].endswith("    1| a = 1\n    2| b = 20\n    3| c = 3")


# A compaction's note isn't a file's text


@pytest.mark.parametrize(
    ("name", "args", "key"),
    [
        ("write", {"path": "make.py", "content": "(compacted: 2340 characters)"}, "content"),
        ("write", {"path": "make.py", "content": "x = 1\n(compacted: 80 characters)\n"}, "content"),
        ("edit", {"path": "make.py", "old": "x = 1", "new": "(compacted: 9 characters)"}, "new"),
        ("edit", {"path": "make.py", "old": "(compacted: 9 characters)", "new": "x"}, "old"),
    ],
)
def test_a_write_or_edit_whose_text_is_a_compaction_note_is_refused(
    tmp_path: Path, name: str, args: dict[str, Any], key: str
) -> None:
    model = Scripted([tool(name, **args)], [tool("blocked", reason="x")])
    desk = Desk()
    s = session(tmp_path, model, desk)
    s.go()
    assert [n for n, _ in desk.called] == ["blocked"], "the file isn't touched"
    reply = model.bodies[1]["messages"][-1]["content"]
    assert f"`{key}` holds a compaction note" in reply
    assert "`read` gives it whole" in reply
    tried = events(s, "tool")[0]
    assert (tried["outcome"], tried["why"]) == ("failed", "compaction_note")


def test_a_file_that_only_mentions_compaction_is_written(tmp_path: Path) -> None:
    text = 'print("compaction is (compacted: not) a note here")\n'
    model = Scripted([tool("write", path="a.py", content=text)], [tool("blocked", reason="x")])
    desk = Desk()
    session(tmp_path, model, desk).go()
    assert [n for n, _ in desk.called] == ["write", "blocked"]


# After a compaction


def test_what_should_be_said_after_a_compaction_is_said_before_the_next_call(
    tmp_path: Path,
) -> None:
    model = Scripted(
        Spoken([tool("x")], reasoning="r" * 3000),
        Spoken([tool("x")], usage={"prompt_tokens": 2000}),
        [tool("blocked", reason="x")],
        prompt_tokens=10,
    )
    s = session(tmp_path, model, compact_at=1500)
    s.after_compaction = lambda: "Still failing: `capture frame.step` at call 1."
    assert s.go().name == "blocked"
    assert s.compactions == 1
    last = model.bodies[2]["messages"][-1]
    assert last == {"role": "user", "content": "Still failing: `capture frame.step` at call 1."}
    rebuilds(s, model)


def test_nothing_is_said_after_a_compaction_when_theres_nothing_to_say(tmp_path: Path) -> None:
    model = Scripted(
        Spoken([tool("x")], reasoning="r" * 3000),
        Spoken([tool("x")], usage={"prompt_tokens": 2000}),
        [tool("blocked", reason="x")],
        prompt_tokens=10,
    )
    s = session(tmp_path, model, compact_at=1500)
    s.after_compaction = lambda: ""
    s.go()
    assert model.bodies[2]["messages"][-1]["role"] == "tool"


# In the ledger, for review


def test_the_index_shows_each_quiet_call_and_what_the_next_request_offered(
    tmp_path: Path,
) -> None:
    from print_shop.rounds import after_action
    from print_shop.rounds.run import Run

    model = Scripted(THINKING, THINKING, [tool("blocked", reason="Which side the lip goes.")])
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    assert offered(model.bodies[2]) == LOCKED
    shown = "\n".join(after_action.index(run.ledger, "r"))
    assert (
        "1: no tool\n"
        "   no tool call, after 21,340 tokens of reasoning in 0 s: asked what's missing\n"
        "2: no tool\n"
        "   no tool call, after 21,340 tokens of reasoning in 0 s: asked what's missing; the next "
        "request offered only `ask`, `send_ask`, `blocked`, as it was 1 call after the last, and "
        "once each, to build and read, `write`, `edit`, `run`, `bench`\n"
        "3: blocked"
    ) in shown
    rows = run.ledger.rows("SELECT name, at_call, said FROM timeline WHERE kind='asked'")
    assert [(r["name"], r["at_call"]) for r in rows] == [("what_is_missing", 1), ("ask_only", 2)]
    assert json.loads(rows[1]["said"])["only"] == ["ask", "send_ask", "blocked"]
    assert Limits().recorded() == json.loads(
        run.ledger.rows("SELECT limits FROM runs WHERE run='r'")[0]["limits"]
    )


def test_the_index_shows_a_cut_call_told_to_write_its_script(tmp_path: Path) -> None:
    from print_shop.rounds import after_action
    from print_shop.rounds.run import Run

    model = Scripted(cut(DRAFTING), [tool("blocked", reason="Which side the lip goes.")])
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    assert run.drive().name == "blocked"
    assert (run.work / "record" / "cut-reasoning-1.md").is_file()
    shown = "\n".join(after_action.index(run.ledger, "r"))
    assert (
        "cut off at the limit, its reasoning not sent back: told to write the script it drafted, "
        "kept in `record/cut-reasoning-1.md`"
    ) in shown
    rows = run.ledger.rows("SELECT name, said FROM timeline WHERE kind='asked'")
    assert [r["name"] for r in rows] == ["write_the_script"]
    said = json.loads(rows[0]["said"])
    assert (said["script"], said["kept"]) == (True, "record/cut-reasoning-1.md")
