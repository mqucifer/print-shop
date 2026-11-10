"""One recorded call sent again with one change, against the scripted model: no model call and
no Docker (mqucifer/print-shop#67). The run's records are made by a real session, so the replay
starts from `transcript.rebuild` as it would on a real run."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from print_shop.bench import host
from print_shop.rounds import replays, tools, transcript
from print_shop.rounds.lessons import HEADING
from print_shop.rounds.session import Ending, Judged, Limits, Session
from tests.rounds_fakes import Scripted, Spoken, tool

START = (
    "The Sponsor's request:\n\nA made-up peg.\n\nThey gave you nothing besides this.\n\n"
    "The skills this work needs throughout are here whole: `fitting-pegs`.\n\n"
    '<skill name="fitting-pegs">\nLeave 0.3 mm round a peg.\n</skill>\n\n'
    "The other skills, to read with `skill` when you need one: `bending-arms`, how arms bend."
)


class Desk:
    """Tools that answer by name: a skill read gives its text, and the bench a reading."""

    specs = [tools.specs()[0]]

    def call(self, name: Any, raw_args: Any) -> Judged:
        if name == "stop":
            return Judged("stop", "ok", "stopped", ending=Ending("blocked", {"reason": "x"}))
        if name == "skill" and "fitting-pegs" in str(raw_args):
            return Judged("skill", "ok", POINTER)
        if name == "skill":
            return Judged("skill", "ok", "An arm bends along its layers.")
        if name == "bench":
            return Judged("bench", "ok", "CLEAR: 0.2500 mm at the least, needs 0.3")
        return Judged(str(name), "ok", f"{name} did it")


#: What a `skill` read of a required skill answered at the run's commit: a pointer to the start.
POINTER = "`fitting-pegs` is in your first message, whole: read it there."


def a_run(tmp_path: Path, first: str = "bending-arms") -> Path:
    """A run's folder with three calls in its events: a skill read, a bench call, then a stop."""
    folder = tmp_path / "runs" / "peg-1"
    folder.mkdir(parents=True)
    model = Scripted(
        [tool("skill", name=first)],
        [tool("bench", argv=["clearance", "plate.step", "peg.step"])],
        [tool("stop")],
    )
    s = Session(
        tools=Desk(),
        send=model,
        alias="crew-code-think",
        events=folder / "events.jsonl",
        limits=Limits(),
        sleep=lambda _: None,
    )
    s.begin({"role": "system", "content": "sys"}, {"role": "user", "content": START})
    assert s.go().name == "blocked"
    return folder


def held(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def sent(tmp_path: Path, change: replays.Change, *turns: Any, **kw: Any) -> tuple[Any, Scripted]:
    folder = a_run(tmp_path)
    model = Scripted(*(turns or ([tool("write", path="a.py", content="x")],) * 3))
    done = replays.replay(folder, 3, change, model, root=tmp_path / "replays", **kw)
    return done, model


def test_a_control_sends_the_recorded_request_as_it_was(tmp_path: Path) -> None:
    done, model = sent(tmp_path, replays.Change())
    recorded = transcript.rebuild(tmp_path / "runs" / "peg-1" / "events.jsonl", 3)
    assert len(model.bodies) == 3
    assert all(body["messages"] == recorded["messages"] for body in model.bodies)
    assert done.places_changed == 0
    assert done.change["request_sha_sent"] == done.change["request_sha_recorded"]


def test_a_skills_text_changes_in_the_start_and_in_each_reply_that_read_it(
    tmp_path: Path,
) -> None:
    given = replays.Change("skill", "fitting-pegs", "Leave 0.5 mm round a peg.\n")
    done, model = sent(tmp_path, given)
    start = model.bodies[0]["messages"][1]["content"]
    assert '<skill name="fitting-pegs">\nLeave 0.5 mm round a peg.\n</skill>' in start
    assert "0.3 mm" not in start and done.places_changed == 1
    read = replays.Change("skill", "bending-arms", "An arm bends across its layers.")
    done, model = sent(tmp_path / "2", read)
    replies = [m["content"] for m in model.bodies[0]["messages"] if m["role"] == "tool"]
    assert replies[0] == "An arm bends across its layers."
    assert model.bodies[0]["messages"][1]["content"] == START


def test_a_tool_reply_is_named_by_its_call_and_place(tmp_path: Path) -> None:
    change = replays.Change("reply", "2.1", "CLEAR, but 0.05 mm under the 0.3 the need asks.")
    _, model = sent(tmp_path, change)
    replies = [m["content"] for m in model.bodies[0]["messages"] if m["role"] == "tool"]
    assert replies == ["An arm bends along its layers.", change.text]
    for wrong, said in (("2.2", "none at 2"), ("9.1", "no call 9"), ("two", "CALL.K")):
        with pytest.raises(replays.Refused, match=said):
            sent(tmp_path / wrong, replays.Change("reply", wrong, "x"))


def test_the_start_skills_go_as_a_card(tmp_path: Path) -> None:
    card = "A card of the rules: 0.3 mm round a peg; arms bend along layers."
    _, model = sent(tmp_path, replays.Change("skills-card", "", card))
    start = model.bodies[0]["messages"][1]["content"]
    assert card in start and "<skill" not in start
    assert start.endswith("`bending-arms`, how arms bend."), "the other skills stay named"


def test_a_lesson_goes_in_under_its_heading(tmp_path: Path) -> None:
    _, model = sent(tmp_path, replays.Change("lesson", "", "Read the gap on the printed file."))
    start = model.bodies[0]["messages"][1]["content"]
    assert start.index(HEADING) < start.index("- Read the gap on the printed file.")
    assert start.index("- Read the gap") < start.index("The skills this work needs")


def test_a_lesson_joins_the_lessons_a_start_already_has() -> None:
    from print_shop.rounds.lessons import ABOUT

    start = f"{START[:40]}\n\n{HEADING}\n\n{ABOUT}\n\n- **Lesson 1**: one.\n\nThe skills..."
    body = {"messages": [{"role": "user", "content": start}]}
    made, n = replays.changed(body, replays.Change("lesson", "", "two."), Path("none"))
    assert n == 1
    assert "- **Lesson 1**: one.\n- two.\n\nThe skills..." in made["messages"][0]["content"]


def test_an_exact_text_is_replaced_everywhere_and_a_change_that_finds_nothing_is_refused(
    tmp_path: Path,
) -> None:
    change = replays.Change("replace", "", "0.4 mm", old="0.3 mm")
    done, model = sent(tmp_path, change)
    assert "0.4 mm round a peg" in model.bodies[0]["messages"][1]["content"]
    assert done.places_changed == 1
    with pytest.raises(replays.Refused, match="finds nothing to change"):
        sent(tmp_path / "2", replays.Change("replace", "", "y", old="not in it"))
    with pytest.raises(replays.Refused, match="finds nothing to change"):
        sent(tmp_path / "3", replays.Change("skill", "no-such-skill", "y"))


def test_a_tools_spec_is_given_in_its_place_and_the_messages_stay_as_recorded(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#93 replays a call with the `needs` tool's new schema."""
    spec = json.loads(json.dumps(tools.specs()[0]))
    spec["function"]["description"] = "Write a file, said another way."
    done, model = sent(tmp_path, replays.Change("tool", "write", json.dumps(spec)))
    recorded = transcript.rebuild(tmp_path / "runs" / "peg-1" / "events.jsonl", 3)
    assert model.bodies[0]["tools"] == [spec]
    assert model.bodies[0]["messages"] == recorded["messages"]
    assert done.places_changed == 1
    with pytest.raises(replays.Refused, match="finds nothing to change"):
        other = {"type": "function", "function": {"name": "needs"}}
        sent(tmp_path / "2", replays.Change("tool", "needs", json.dumps(other)))
    with pytest.raises(replays.Refused, match="isn't one of the tool `write`"):
        sent(tmp_path / "3", replays.Change("tool", "write", json.dumps({"function": {}})))
    with pytest.raises(replays.Refused, match="a tool's spec is JSON"):
        sent(tmp_path / "4", replays.Change("tool", "write", "not json"))


def test_each_try_records_its_reasoning_the_call_it_ends_in_and_whether_its_long(
    tmp_path: Path,
) -> None:
    done, _ = sent(
        tmp_path,
        replays.Change(),
        Spoken([tool("write", path="a.py", content="x")], reasoning="r" * 25_000),
        Spoken([tool("bench", argv=["info", "a.step"])], usage={"reasoning_tokens": 40}),
        Spoken("I'd ask first.", finish="stop"),
        times=3,
    )
    long, bench, none = done.tries
    assert (long["ends_in"], long["long"], long["reasoning_chars"]) == ("build", True, 25_000)
    assert (bench["ends_in"], bench["reasoning_tokens"], bench["long"]) == ("bench", 40, False)
    assert (none["ends_in"], none["tools"]) == ("none", [])
    summary = done.summary()
    assert summary["long"] == 1
    assert summary["ends_in"] == {"bench": 1, "build": 1, "none": 1}
    assert summary["recorded"]["ends_in"] == "other", "call 3 recorded was `stop`"
    assert replays.ends_in({"tool_calls": [{"function": {"name": "send_ask"}}]}) == "ask"


def test_the_runs_folder_is_left_as_it_was_and_the_replay_has_its_own(tmp_path: Path) -> None:
    folder = a_run(tmp_path)
    before = held(folder)
    model = Scripted(*([tool("write", path="a.py", content="x")],) * 4)
    first = replays.replay(folder, 3, replays.Change(), model, times=2, root=tmp_path / "out")
    again = replays.replay(folder, 3, replays.Change(), model, times=2, root=tmp_path / "out")
    assert held(folder) == before
    assert Path(first.folder).name == "none" and Path(again.folder).name == "none-2"
    kept = json.loads((Path(first.folder) / "replay.json").read_text())
    assert kept["call"] == 3 and len(kept["tries"]) == 2 and kept["summary"]["tries"] == 2
    assert (Path(first.folder) / "README.md").read_text().count("\n| ") == 4
    assert json.loads((Path(first.folder) / "request.json").read_text())["messages"]
    with pytest.raises(replays.Refused, match="inside the run's folder"):
        replays.replay(folder, 3, replays.Change(), model, root=folder / "replays")
    with pytest.raises(replays.Refused, match="public"):
        replays.replay(
            folder, 3, replays.Change(), model, root=host.REPOSITORY / "x", public=host.REPOSITORY
        )


def test_a_failed_try_says_only_its_kind_and_status_and_the_rest_go_on(tmp_path: Path) -> None:
    folder = a_run(tmp_path)
    model = Scripted([tool("write", path="a.py", content="x")])
    request = httpx.Request("POST", "http://proxy.invalid/chat")
    model.failures.append(
        httpx.HTTPStatusError("at http://proxy.invalid", request=request,
                              response=httpx.Response(500, request=request))
    )
    done = replays.replay(folder, 3, replays.Change(), model, times=2, root=tmp_path / "out")
    assert done.tries[0] == {"try": 1, "error": "HTTPStatusError", "status": 500}
    assert done.tries[1]["ends_in"] == "build"
    assert "proxy.invalid" not in (Path(done.folder) / "replay.json").read_text()


CARD = "A card of the rules: 0.3 mm round a peg."
WHOLE = "Leave 0.3 mm round a peg."


def test_under_a_card_an_earlier_read_of_a_required_skill_has_its_text(tmp_path: Path) -> None:
    folder = a_run(tmp_path, first="fitting-pegs")
    model = Scripted([tool("write", path="a.py", content="x")])
    replays.replay(folder, 3, replays.Change("skills-card", "", CARD), model, times=1,
                   root=tmp_path / "out")
    replies = [m["content"] for m in model.bodies[0]["messages"] if m["role"] == "tool"]
    assert replies[0] == WHOLE, "the pointer to the start is now the skill's text"
    control = Scripted([tool("write", path="a.py", content="x")])
    replays.replay(folder, 3, replays.Change(), control, times=1, root=tmp_path / "out")
    assert [m["content"] for m in control.bodies[0]["messages"] if m["role"] == "tool"][0] == (
        POINTER
    ), "the control is the request as recorded"


def test_under_a_card_a_read_of_a_required_skill_is_answered_and_the_try_goes_on(
    tmp_path: Path,
) -> None:
    folder = a_run(tmp_path)
    model = Scripted(
        Spoken([tool("skill", name="fitting-pegs")], reasoning="r" * 100),
        Spoken([tool("write", path="a.py", content="x")], reasoning="s" * 30),
        [tool("skill", name="fitting-pegs"), tool("bench", argv=["info", "a.step"])],
    )
    done = replays.replay(folder, 3, replays.Change("skills-card", "", CARD), model, times=2,
                          root=tmp_path / "out")
    first, second = done.tries
    assert first["read_required"] == ["fitting-pegs"]
    assert (first["ends_in"], first["reasoning_chars"], first["reasoning_chars_total"]) == (
        "build", 30, 130
    )
    assert [c["ends_in"] for c in first["calls"]] == ["other", "build"]
    sent = model.bodies[1]["messages"]
    assert sent[-2]["role"] == "assistant" and sent[-2]["reasoning_content"] == "r" * 100
    assert sent[-1] == {"role": "tool", "tool_call_id": "call-1-0", "content": WHOLE}
    # A turn that reads one and calls anything more ends there: no other tool is run.
    assert len(model.bodies) == 3
    assert (second["read_required"], second["ends_in"], "calls" in second) == (
        ["fitting-pegs"], "other", False
    )
    assert done.summary()["read_required"] == 2


def test_without_a_card_a_read_of_a_required_skill_is_recorded_and_the_try_ends(
    tmp_path: Path,
) -> None:
    folder = a_run(tmp_path)
    model = Scripted([tool("skill", name="fitting-pegs")], [tool("skill", name="bending-arms")])
    done = replays.replay(folder, 3, replays.Change(), model, times=2, root=tmp_path / "out")
    assert [t["read_required"] for t in done.tries] == [["fitting-pegs"], []]
    assert len(model.bodies) == 2
    assert done.recorded["read_required"] == []
