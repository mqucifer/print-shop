"""What the conversation keeps (mqucifer/print-shop#78), with the fake model and no Docker: the
facts the model sets, carried into every new conversation, compaction's note and resume; a note
after every compaction; compaction that drops what a later call replaced; a newest turn too big
to fit sent back without its reasoning; and the reasoning-trim switch, off unless it's turned on.

Every request, reply and figure here is made up in the test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import facts, prompt, transcript
from print_shop.rounds import run as run_module
from print_shop.rounds.run import Run
from print_shop.rounds.session import EARLIER_NOTE, WITHHELD, Judged, Limits, Session
from tests.rounds_fakes import Scripted, Spoken, tool
from tests.test_rounds import Echo, session

NEEDS = [{"id": f"n{i}", "what": "x" * 200} for i in range(4)]


def events(s: Session, kind: str) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(s.events) if e["kind"] == kind]


def arguments(s: Session, name: str) -> list[dict[str, Any]]:
    """Each call of a tool's arguments, as the conversation holds them now, in order."""
    return [
        json.loads(c["function"]["arguments"])
        for m in s.messages
        for c in m.get("tool_calls") or []
        if c["function"]["name"] == name
    ]


def rebuilds(s: Session, model: Scripted) -> None:
    """Every request is the one the records rebuild, rewrites and all."""
    for n, body in enumerate(model.bodies, 1):
        assert transcript.rebuild(s.events, n)["messages"] == body["messages"], n


# Compaction drops what a later call replaced


def test_after_three_needs_calls_and_a_compaction_only_the_latest_is_whole(
    tmp_path: Path,
) -> None:
    model = Scripted(
        [tool("breakdown", parts=["one"] * 40, joins=[], look=[])],
        [tool("needs", needs=NEEDS[:2])],
        [tool("ask", question="Which?", stop="plan", variants=[{"name": "A"}] * 20)],
        [tool("needs", needs=NEEDS[:3])],
        [tool("breakdown", parts=["two"] * 40, joins=[], look=[])],
        [tool("ask", question="Which now?", stop="plan", variants=[{"name": "B"}] * 20)],
        [tool("needs", needs=NEEDS)],
        [tool("x")],
        [tool("stop")],
    )
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    s.compact()
    sent = arguments(s, "needs")
    assert sent[-1] == {"needs": NEEDS}, "the latest stays whole"
    for earlier in sent[:-1]:
        assert earlier["needs"].startswith("(compacted: ")
        assert "a later `needs` replaced this: `record/needs.json` holds" in earlier["needs"]
    asked = arguments(s, "ask")
    assert asked[-1]["variants"] == [{"name": "B"}] * 20
    assert asked[0]["variants"].startswith("(compacted: ")
    assert (asked[0]["question"], asked[0]["stop"]) == ("Which?", "plan"), "short ones stay"
    broken = arguments(s, "breakdown")
    assert broken[-1]["parts"] == ["two"] * 40
    assert "`record/breakdown.md` holds the latest" in broken[0]["parts"]
    assert transcript.conversation(s.events) == s.messages, "every change is a recorded rewrite"
    s.compact()
    assert arguments(s, "needs") == sent, "a second compaction leaves the notes as they are"


def test_a_one_off_script_run_once_and_left_becomes_a_note_with_its_gist(
    tmp_path: Path,
) -> None:
    replies = {"run": "exit 0 after 0.5s\nmeasuring\nblock x 3.0-7.5, top z 12.0\n"}

    class Runs(Echo):
        def call(self, name: Any, raw_args: Any) -> Judged:
            if name == "run":
                return Judged("run", "ok", replies["run"])
            return super().call(name, raw_args)

    model = Scripted(
        [tool("write", path="make.py", content="a = 1\nb = 2\n" + "# main\n" * 50)],
        [tool("edit", path="make.py", old="a = 1", new="a = 3")],
        [tool("edit", path="make.py", old="b = 2", new="b = 4")],
        [tool("write", path="probe.py", content="# probe\n" * 60)],
        [tool("run", path="probe.py")],
        [tool("write", path="again.py", content="# run twice\n" * 60)],
        [tool("run", path="again.py")],
        [tool("run", path="again.py")],
        [tool("run", path="make.py")],
        [tool("x")],
        [tool("stop")],
    )
    s = session(tmp_path, model, Runs())
    assert s.go().name == "blocked"
    s.compact()
    written = {a["path"]: a["content"] for a in arguments(s, "write")}
    assert written["probe.py"] == (
        "(compacted: `probe.py`, a one-off script, run once and left since; it said: exit 0 "
        "after 0.5s; block x 3.0-7.5, top z 12.0. `read` gives it whole)"
    )
    assert written["again.py"] == "# run twice\n" * 60, "run twice, it isn't a one-off"
    assert "# main" in json.dumps(s.messages), "the main script stays, as one current copy"
    assert transcript.conversation(s.events) == s.messages


def test_no_script_is_a_one_off_until_one_has_changed_three_times(tmp_path: Path) -> None:
    model = Scripted(
        [tool("write", path="make.py", content="# main\n" * 50)],
        [tool("run", path="make.py")],
        [tool("write", path="probe.py", content="# probe\n" * 60)],
        [tool("run", path="probe.py")],
        [tool("run", path="make.py")],
        [tool("x")],
        [tool("stop")],
    )
    s = session(tmp_path, model)
    assert s.go().name == "blocked"
    s.compact()
    written = {a["path"]: a["content"] for a in arguments(s, "write")}
    assert written == {"make.py": "# main\n" * 50, "probe.py": "# probe\n" * 60}


def test_only_the_latest_compaction_note_stays_whole(tmp_path: Path) -> None:
    over = {"now": True}
    model = Scripted([tool("x")], [tool("x")], [tool("x")], [tool("stop")])

    def send(body: dict[str, Any]) -> dict[str, Any]:
        over["now"] = True
        return model(body)

    s = session(tmp_path, model)
    s.send = send
    real = s.compact

    def compact() -> None:
        real()
        over["now"] = False

    s.compact = compact  # type: ignore[method-assign]
    s.estimate = lambda: 10**9 if over["now"] else 0  # type: ignore[method-assign]
    said = iter(["Compacted. First.", "Compacted. Second.", "Compacted. Third."])
    s.after_compaction = lambda: next(said)
    assert s.go().name == "blocked"
    users = [m["content"] for m in model.bodies[-1]["messages"] if m["role"] == "user"]
    assert users[-3:] == [EARLIER_NOTE, EARLIER_NOTE, "Compacted. Third."]
    assert len(events(s, "compaction_note")) == 3
    rebuilds(s, model)


def test_a_note_from_before_this_is_known_by_its_opening(tmp_path: Path) -> None:
    model = Scripted([tool("x")], [tool("stop")])
    s = session(tmp_path, model)
    s.say("Compacted. Still failing, from `record/CHECKS.md`:\n- `thin a.step`, call 1: FAIL")
    s.go()
    s.compact()
    assert EARLIER_NOTE in [m.get("content") for m in s.messages]


# A newest turn too big to fit


def test_a_newest_turn_over_the_limit_goes_back_without_its_reasoning_and_the_run_goes_on(
    tmp_path: Path,
) -> None:
    long = "the frame's left edge, then the clip's toe; " * 450
    model = Scripted(
        [tool("x")],
        Spoken([tool("x")], reasoning=long),
        [tool("stop")],
        prompt_tokens=1000,
    )
    s = session(tmp_path, model, compact_at=5000)
    assert s.go().name == "blocked", "not context_full"
    newest = [m for m in model.bodies[2]["messages"] if m["role"] == "assistant"][-1]
    assert newest["reasoning_content"] == "" and newest["tool_calls"]
    withheld = [e for e in events(s, "rewrite") if e.get("why") == "oversize"]
    assert len(withheld) == 1
    (compacted,) = events(s, "compacted")
    tokens = compacted["withheld_reasoning"]
    assert tokens > 5000 and compacted["compacted_to"] >= 5000
    assert compacted["estimated_after"] < 5000
    assert model.bodies[2]["messages"][-1] == {
        "role": "user",
        "content": WITHHELD.format(tokens=tokens),
    }
    rebuilds(s, model)


# The reasoning-trim switch


def reasoning(body: dict[str, Any]) -> list[str]:
    return [m["reasoning_content"] for m in body["messages"] if m["role"] == "assistant"]


def thinking(turns: int) -> Scripted:
    return Scripted(
        *[Spoken([tool("x")], reasoning=f"{n}" * 1000) for n in range(1, turns + 1)],
        [tool("stop")],
    )


def test_the_trim_switch_on_strips_all_but_the_last_three_turns_reasoning_in_one_step(
    tmp_path: Path,
) -> None:
    model = thinking(8)
    s = session(tmp_path, model, trim_reasoning_at=1700)
    assert s.go().name == "blocked"
    (trimmed,) = events(s, "reasoning_trimmed")
    assert trimmed["before_call"] == 7 and (trimmed["turns"], trimmed["kept"]) == (3, 3)
    kinds = [e["kind"] for e in transcript.events_of(s.events)]
    first = kinds.index("rewrite")
    assert kinds[first : first + 4] == ["rewrite", "rewrite", "rewrite", "reasoning_trimmed"]
    assert all(e.get("why") == "trimmed" for e in events(s, "rewrite"))
    assert reasoning(model.bodies[6]) == ["", "", "", "4" * 1000, "5" * 1000, "6" * 1000]
    assert all(reasoning(model.bodies[5])), "nothing changes before it passes the line"
    rebuilds(s, model)


def test_the_trim_switch_off_changes_nothing(tmp_path: Path) -> None:
    model = thinking(8)
    s = session(tmp_path, model)
    assert Limits().trim_reasoning_at == 0, "off unless an eval's arm turns it on"
    assert s.go().name == "blocked"
    assert not events(s, "rewrite") and not events(s, "reasoning_trimmed")
    assert reasoning(model.bodies[-1]) == [f"{n}" * 1000 for n in range(1, 9)]


def test_a_run_started_with_the_switch_keeps_it_on_when_resumed(tmp_path: Path) -> None:
    model = Scripted()
    run = Run.start(
        tmp_path, "r", "A block.", [], model, Limits(trim_reasoning_at=40_000), sleep=lambda _: None
    )
    run.save()
    again = Run.load(run.folder, model)
    assert again.session.limits.trim_reasoning_at == 40_000
    assert again.session.limits.trim_keep == 3
    plain = Run.start(tmp_path, "s", "A block.", [], model, sleep=lambda _: None)
    assert plain.state.trim_reasoning is None


# Facts


def started(tmp_path: Path, *turns: Any, **limits: Any) -> Run:
    fan = tmp_path / "given" / "fan.step"
    fan.parent.mkdir()
    fan.write_text("made up\n")
    model = Scripted(*turns, prompt_tokens=limits.pop("prompt_tokens", 1000))
    return Run.start(tmp_path / "runs", "r", "A block.", [fan], model, Limits(**limits))


def test_a_fact_is_kept_replaced_and_removed_by_name(tmp_path: Path) -> None:
    run = started(tmp_path)
    said = run.fact({"name": "fan-airflow", "text": "Air goes in at -z.", "from": "call 4"})
    assert said.outcome == "ok" and "Kept, as the fact `fan-airflow`." in said.reply
    record = (run.work / "record" / "FACTS.md").read_text()
    assert "- `fan-airflow`: Air goes in at -z. (from call 4, kept at call 0)" in record
    again = run.fact({"name": "fan-airflow", "text": "Air goes in at +z.", "from": "call 9"})
    assert "replacing the one kept at call 0" in again.reply
    assert len(run.state.facts) == 1
    assert "+z" in (run.work / "record" / "FACTS.md").read_text()
    gone = run.fact({"name": "fan-airflow", "text": "", "from": "call 9"})
    assert gone.outcome == "ok" and not run.state.facts
    assert "None yet." in (run.work / "record" / "FACTS.md").read_text()
    nothing = run.fact({"name": "fan-airflow", "text": "", "from": "call 9"})
    assert nothing.outcome == "failed"


def test_a_bought_parts_fact_gives_its_box_its_way_in_and_what_holds_it(tmp_path: Path) -> None:
    run = started(tmp_path)
    fan = {"name": "references/fan.step", "text": "", "from": "call 3, bench info"}
    short = run.fact({**fan, "box": "x 0-30, y 0-30, z 0-10"})
    assert short.outcome == "failed"
    assert "`way_in`, `held_by` missing" in short.reply
    wrong = run.fact({**fan, "box": "x 0-30", "way_in": "down", "held_by": "nothing yet"})
    assert "`way_in` is one of +x, -x, +y, -y, +z, -z" in wrong.reply
    whole = {**fan, "box": "x 0-30, y 0-30, z 0-10", "way_in": "-z", "held_by": "the lid's toes"}
    assert run.fact(whole).outcome == "ok"
    (kept,) = facts.load(run.state.facts)
    assert kept.line() == (
        "- `references/fan.step`: box x 0-30, y 0-30, z 0-10; goes in along -z; held by the "
        "lid's toes. (from call 3, bench info, kept at call 0)"
    )
    absent = run.fact({**whole, "name": "references/board.step"})
    assert "There's no `references/board.step`: the given files are `references/fan.step`." in (
        absent.reply
    )
    elsewhere = run.fact({**whole, "name": "fan"})
    assert "belong to a bought part's fact" in elsewhere.reply
    too_long = run.fact({"name": "n", "text": "x" * 601, "from": "call 1"})
    assert "Split it into facts by name" in too_long.reply


def test_a_fact_set_before_a_forced_restart_is_in_the_restart_message(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        [tool("fact", name="fan-airflow", text="Air goes in at -z.", **{"from": "call 1"})],
        [tool("x")],
        [tool("blocked", reason="after the new conversation")],
        compact_at=500,
        prompt_tokens=10_000,
    )
    assert run.drive().name == "blocked"
    assert run.state.restarts >= 1
    model: Any = run.session.send
    first = model.bodies[-1]["messages"][1]["content"]
    assert facts.HEADING in first
    assert "- `fan-airflow`: Air goes in at -z. (from call 1, kept at call 1)" in first
    assert prompt.AGAIN_FACTS in first
    assert "`record/CHECKS.md` holds every check's tries." in first, "no tries to give"


def test_the_restart_gives_each_checks_tries_or_points_at_checks_md_never_both() -> None:
    tried = prompt.again("start", [], "[]", "This is intake.", "# Each check's tries\n- a try")
    assert prompt.AGAIN_TRIES in tried
    assert "starting with `record/ROUNDS.md`." in tried
    assert "`record/CHECKS.md` holds every" not in tried
    untried = prompt.again("start", [], "[]", "This is intake.")
    assert prompt.AGAIN_TRIES not in untried


def test_a_compaction_with_nothing_failing_leaves_a_note_with_the_facts(tmp_path: Path) -> None:
    run = started(
        tmp_path,
        [tool("fact", name="fan-airflow", text="Air goes in at -z.", **{"from": "call 1"})],
        [tool("blocked", reason="done here")],
    )
    compacted: list[bool] = []
    real = run.session.compact

    def compact() -> None:
        real()
        compacted.append(True)

    run.session.compact = compact  # type: ignore[method-assign]
    run.session.estimate = lambda: 0 if compacted else 10**9  # type: ignore[method-assign]
    assert run.drive().name == "blocked"
    model: Any = run.session.send
    note = model.bodies[1]["messages"][-1]
    assert note["role"] == "user"
    assert note["content"].startswith(run_module.NOTHING_FAILING)
    assert "- `fan-airflow`: Air goes in at -z." in note["content"]
    assert "No needs are settled yet" in note["content"]


def test_with_no_facts_the_note_says_what_one_is_for(tmp_path: Path) -> None:
    run = started(tmp_path)
    assert facts.NONE_YET in run.compaction_note()


def test_the_note_names_the_needs_not_yet_met(tmp_path: Path) -> None:
    run = started(tmp_path)
    run.state.needs = [
        {"id": "fit", "what": "it fits", "source": "request", "by": "eye", "look_for": "x"},
        {"id": "lid", "what": "it closes", "source": "request", "by": "eye", "look_for": "x"},
        {"id": "edge", "what": "soft", "source": "request", "by": "eye", "look_for": "x"},
    ]
    run.state.last_readings = {
        "fit": {"passed": True, "value": None, "verdict": "", "why": "fine"},
        "lid": {"passed": False, "value": None, "verdict": "TOO CLOSE", "why": "0.1 mm at the lip"},
    }
    said = run.compaction_note()
    assert "Needs not yet met, of the 3 settled (`record/needs.json`):" in said
    assert "- `lid`, NOT MET at the last `done`: 0.1 mm at the lip" in said
    assert "- not read at a `done` yet: `edge`" in said
    assert "`fit`" not in said


def test_a_note_that_asks_something_says_to_answer_it_with_a_fact(tmp_path: Path) -> None:
    run = started(tmp_path)
    asking = "The lid is fine. Say which side the plug faces. Which way does air go?"
    run.resume(asking, False, "ops")
    note = run.session.messages[-1]["content"]
    assert note.endswith(
        'This note asks you "Say which side the plug faces." and "Which way does air go?". Answer '
        "each with `fact`, named for what it asks, so the answer is carried into every later "
        "conversation, then go on."
    )
    assert 'asked you "Say which side the plug faces."' in run.compaction_note()
    run.resume("The lid is fine.", False, "ops")
    assert run.session.messages[-1]["content"] == "A note from ops:\n\nThe lid is fine."
    assert 'said: "The lid is fine." It\'s above in full.' in run.compaction_note()


def test_every_resume_gives_the_facts_whole(tmp_path: Path) -> None:
    run = started(tmp_path)
    run.resume()
    assert facts.HEADING not in json.dumps(run.session.messages), "nothing to give yet"
    run.fact({"name": "fan-airflow", "text": "Air goes in at -z.", "from": "call 4"})
    run.resume("Go on.", False, "ops")
    gave = [m["content"] for m in run.session.messages if m["role"] == "user"][-2:]
    assert gave[0].startswith(facts.HEADING) and "fan-airflow" in gave[0]
    assert gave[1].startswith("A note from ops")


def test_the_start_says_what_a_fact_is_for_in_two_lines(tmp_path: Path) -> None:
    run = started(tmp_path)
    assert facts.START in run.session.messages[1]["content"]
    assert len(facts.START.splitlines()) == 2


def test_the_fact_tool_is_offered_and_judged(tmp_path: Path) -> None:
    run = started(tmp_path)
    names = [s["function"]["name"] for s in run.tools.specs]
    assert "fact" in names
    said = run.tools.call("fact", json.dumps({"name": "a", "text": "b"}))
    assert said.outcome == "malformed" and "it needs `from`" in said.reply
