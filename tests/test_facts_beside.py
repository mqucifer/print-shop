"""A run's facts listed beside a person's answers, note or pick (mqucifer/print-shop#314), with
the fake model and no Docker: each to be marked kept or changed with `mark_facts` before `needs`
or `done`; a fact kept against an answer listed beside the next one again; a changed one
rewritten, its old words and who changed it kept in `record/FACTS.md`, and its new words in every
compaction's note and new conversation; and a run with no facts as it was.

Every request, answer, fact and figure here is made up in the test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.rounds import facts, transcript
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool

WHO = "stand-in: the operator"
#: The made-up fact a person corrects: on the design run, a finer mesh called coarser, and the
#: wrong part blamed for a failed reading.
WRONG = "A --mesh of 0.2 is coarser than 0.5, and the lid's reading failed."
RIGHT = "A --mesh of 0.2 is finer than 0.5, and the base's reading failed, not the lid's."
ANSWERS = "0.2 is the finer mesh, not the coarser one. It was the base that failed, not the lid."
MESH = tool("fact", name="mesh-step", text=WRONG, **{"from": "call 1, bench info"})
AIR = tool("fact", name="fan-airflow", text="Air goes in at -z.", **{"from": "call 1"})


def started(tmp_path: Path, *turns: Any) -> Run:
    model = Scripted(*turns)
    return Run.start(tmp_path / "runs", "r", "A block.", [], model, sleep=lambda _: None)


def again(run: Run, *turns: Any) -> Run:
    return Run.load(run.folder, Scripted(*turns), sleep=lambda _: None)


def answered(run: Run, text: str, *turns: Any) -> tuple[Run, list[str]]:
    """The run's latest questions answered by a stand-in, and the run loaded to go on."""
    n = run.state.questions
    (run.folder / "questions" / f"{n}.answers.md").write_text(text)
    loaded = again(run, *turns)
    return loaded, loaded.resume(note_from=WHO)


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def relayed(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "relayed"]


def asked(tmp_path: Path, *first: Any) -> Run:
    """A run that kept its facts and stopped with questions."""
    run = started(tmp_path, *first, [tool("questions", questions="Is the mesh right?")])
    assert run.drive().name == "questions"
    return run


# A fact contradicted by an answer, kept as it was, is shown beside the next answer again


def test_a_fact_kept_against_an_answer_is_listed_beside_the_next_answer_again(
    tmp_path: Path,
) -> None:
    run = asked(tmp_path, [MESH, AIR])
    kept = {
        "mesh-step": {"is": "kept", "why": "I still read 0.2 as coarser."},
        "fan-airflow": {"is": "kept", "why": "The answers don't touch the airflow."},
    }
    run, said = answered(
        run,
        ANSWERS,
        [tool("needs", needs=[{"id": "n", "what": "x"}])],
        [tool("mark_facts", marks={"mesh-step": kept["mesh-step"]})],
        [tool("mark_facts", marks=kept)],
        [tool("questions", questions="Anything else?")],
    )
    assert said == [
        "The model is shown its 2 facts beside the answers, and `needs` and `done` are refused "
        "until each is marked kept or changed against it."
    ]
    # The list comes after the answers, so it reads against them.
    assert [e["relay"] for e in relayed(run)[-2:]] == ["answers", "facts_beside"]
    listed = relayed(run)[-1]["message"]
    assert listed.startswith("The answers came while you hold these facts. Each, beside it:")
    assert f"- `mesh-step`: {WRONG} (from call 1, bench info, kept at call 1)" in listed
    assert "- `fan-airflow`: Air goes in at -z." in listed
    assert "Send `mark_facts` with each of these marked kept or changed" in listed
    assert run.compaction_note().startswith(facts.UNMARKED + "\n\n")
    assert "mark_facts" in run.stage_open()[0]

    assert run.drive().name == "questions"
    needs, short, marked, _ = told(run)[-4:]
    assert needs["why"] == "facts_unmarked"
    assert "`mesh-step`, `fan-airflow`" in needs["reply"]
    assert short["why"] == "not_marked"
    assert "`fan-airflow` isn't marked: it says `fan-airflow`: Air goes in at -z." in short[
        "reply"
    ]
    assert marked["outcome"] == "ok"
    assert marked["reply"] == "Marked against the answers: 2 kept, 0 changed."
    assert run.state.facts_beside is None and not run.state.facts_changed
    assert run.state.facts["mesh-step"]["text"] == WRONG, "kept as it was"
    assert not run.compaction_note().startswith(facts.UNMARKED)
    assert "mark_facts" not in run.stage_open()[0]
    refused = [r["why"] for r in run.ledger.rows("SELECT why FROM refusals ORDER BY seq")]
    assert refused.count("facts_unmarked") == 1 and refused.count("not_marked") == 1

    # The next answers list it again, as it still stands.
    run, said = answered(run, "The base is the part to fix.", [tool("blocked", reason="x")])
    assert said[0].startswith("The model is shown its 2 facts beside the answers")
    listed = relayed(run)[-1]["message"]
    assert relayed(run)[-1]["relay"] == "facts_beside"
    assert f"- `mesh-step`: {WRONG}" in listed


def test_done_waits_for_the_marks_too(tmp_path: Path) -> None:
    run = asked(tmp_path, [MESH])
    run, _ = answered(run, ANSWERS)
    run.state.needs = [
        {"id": "fit", "what": "it fits", "source": "request", "by": "eye", "look_for": "x"}
    ]
    judged = run.done({"part": ["lid.step"], "fix_shape": "x", "closing": "y", "script": "m.py"})
    assert judged.why == "facts_unmarked"
    assert judged.reply.startswith("`done` waits: your facts were listed beside the answers")
    # A try only reads, and isn't held.
    tried = run.needs({"needs": [{"id": "n", "what": "x"}], "try": True})
    assert tried.why != "facts_unmarked"


# A fact changed survives compaction in its new words


def test_a_fact_changed_against_an_answer_is_rewritten_and_compaction_gives_its_new_words(
    tmp_path: Path,
) -> None:
    run = asked(tmp_path, [MESH, AIR])
    changed = {"is": "changed", "why": "The stand-in says 0.2 is finer.", "text": RIGHT}
    run, _ = answered(
        run,
        ANSWERS,
        [tool("mark_facts", marks={"mesh-step": {**changed, "text": WRONG}, "fan-airflow": "x"})],
        [
            tool(
                "mark_facts",
                marks={"mesh-step": changed, "fan-airflow": {"is": "kept", "why": "untouched"}},
            )
        ],
        [tool("blocked", reason="after the marks")],
    )
    assert run.drive().name == "blocked"
    wrong, marked = told(run)[-3:-1]
    assert wrong["why"] == "not_marked"
    assert "`mesh-step` is marked changed, and its words are as they were" in wrong["reply"]
    assert "`fan-airflow` isn't marked" in wrong["reply"]
    assert marked["reply"].startswith("Marked against the answers: 1 kept, 1 changed.")
    assert "`mesh-step` is rewritten" in marked["reply"]

    (now,) = [one for one in facts.load(run.state.facts) if one.name == "mesh-step"]
    assert now.text == RIGHT
    assert now.source == "the answers from a stand-in for the Sponsor (the operator)"
    assert [one.name for one in facts.load(run.state.facts)] == ["mesh-step", "fan-airflow"]
    (row,) = run.state.facts_changed
    assert row["was"]["text"] == WRONG and row["by"] == (
        "a stand-in for the Sponsor (the operator)"
    )
    record = (run.work / "record" / "FACTS.md").read_text()
    head, _, changed_part = record.partition(facts.CHANGED_HEADING)
    assert RIGHT in head and WRONG not in head
    assert (
        "- `mesh-step`, rewritten at call 4 against the answers from a stand-in for the Sponsor "
        f"(the operator): The stand-in says 0.2 is finer. It was: {WRONG}"
    ) in changed_part
    (event,) = [e for e in transcript.events_of(run.session.events) if e["kind"] == "facts_marked"]
    assert event["marks"]["mesh-step"]["is"] == "changed"

    # Compaction's note, and a conversation started again from the record, give the new words.
    note = run.compaction_note()
    assert RIGHT in note and WRONG not in note
    run._from_the_record()
    first = run.session.messages[1]["content"]
    assert RIGHT in first and WRONG not in first


def test_a_fact_changed_to_nothing_is_removed_and_kept_in_the_record(tmp_path: Path) -> None:
    run = asked(tmp_path, [MESH])
    run, _ = answered(run, ANSWERS)
    gone = {"mesh-step": {"is": "changed", "why": "It was wrong twice over.", "text": ""}}
    stray = run.mark_facts({"marks": {**gone, "lid-size": {"is": "kept", "why": "x"}}})
    assert "`lid-size` isn't one of the facts listed" in stray.reply
    assert run.mark_facts({"marks": gone}).outcome == "ok"
    assert not run.state.facts
    assert "None yet." in (record := (run.work / "record" / "FACTS.md").read_text())
    at = run.session.requests
    assert f"- `mesh-step`, removed at call {at} against the answers from a stand-in" in record
    assert WRONG in record.partition(facts.CHANGED_HEADING)[2]
    again_said = run.mark_facts({"marks": gone})
    assert again_said.why == "nothing_listed"


def test_a_bought_parts_fact_changed_keeps_the_fields_it_doesnt_give(tmp_path: Path) -> None:
    fan = tmp_path / "given" / "fan.step"
    fan.parent.mkdir(parents=True)
    fan.write_text("made up\n")
    model = Scripted(
        [
            tool(
                "fact",
                name="references/fan.step",
                text="",
                box="x 0-30, y 0-30, z 0-10",
                way_in="+z",
                held_by="the lid",
                **{"from": "call 1"},
            )
        ],
        [tool("questions", questions="What holds the fan?")],
    )
    run = Run.start(tmp_path / "runs", "r", "A block.", [fan], model, sleep=lambda _: None)
    assert run.drive().name == "questions"
    run, _ = answered(run, "The base holds the fan, not the lid.")
    mark = {"is": "changed", "why": "The base holds it.", "text": "", "held_by": "the base"}
    assert run.mark_facts({"marks": {"references/fan.step": mark}}).outcome == "ok"
    (now,) = facts.load(run.state.facts)
    assert (now.box, now.way_in, now.held_by) == ("x 0-30, y 0-30, z 0-10", "+z", "the base")
    bad = {**mark, "way_in": "down"}
    run.state.facts_beside = {"by": "x", "whence": "the note", "facts": [now.name], "at_call": 1}
    refused = run.mark_facts({"marks": {"references/fan.step": bad}})
    assert "can't be changed so: `way_in` is one of" in refused.reply


def test_a_note_a_pick_and_a_send_back_each_list_the_facts(tmp_path: Path) -> None:
    run = asked(tmp_path, [MESH])
    run, _ = answered(run, ANSWERS)
    run.mark_facts({"marks": {"mesh-step": {"is": "kept", "why": "x"}}})
    said = run.resume("Mind the base.", False, "ops")
    assert said == [
        "The model is shown its 1 fact beside the note, and `needs` and `done` are refused until "
        "each is marked kept or changed against it."
    ]
    assert relayed(run)[-1]["message"].startswith("The note came while you hold these facts.")
    assert facts.whence(["pick", "fix_first", "note"]) == "the pick, the message and the note"
    assert facts.whence(["send_back"]) == "the send-back note"


def test_the_tool_is_offered_and_its_marks_may_come_as_text(tmp_path: Path) -> None:
    run = asked(tmp_path, [MESH])
    run, _ = answered(run, ANSWERS)
    assert "mark_facts" in [s["function"]["name"] for s in run.tools.specs]
    marks = json.dumps({"mesh-step": {"is": "kept", "why": "x"}})
    said = run.tools.call("mark_facts", json.dumps({"marks": marks}))
    assert said.outcome == "ok", said.reply


# A run with no facts, and an answer with none to touch, reads as on main


def test_a_run_with_no_facts_lists_nothing_and_holds_nothing(tmp_path: Path) -> None:
    run = asked(tmp_path)
    run, said = answered(
        run,
        ANSWERS,
        [tool("needs", needs=[{"id": "n", "what": "x"}])],
        [tool("blocked", reason="x")],
    )
    assert said == []
    assert relayed(run)[-1]["relay"] == "answers"
    assert "mark_facts" not in json.dumps(run.session.messages[2:])
    assert run.state.facts_beside is None
    assert run.stage_open() == (("questions", "breakdown", "needs"), "at intake")
    assert not run.compaction_note().startswith(facts.UNMARKED)
    assert run.drive().name == "blocked"
    assert told(run)[-2]["why"] != "facts_unmarked"
    assert facts.CHANGED_HEADING not in facts.record([])
    nothing = run.mark_facts({"marks": {}})
    assert nothing.why == "nothing_listed"


def test_a_resume_with_nothing_said_lists_nothing(tmp_path: Path) -> None:
    run = started(tmp_path, [MESH], [tool("blocked", reason="x")])
    assert run.drive().name == "blocked"
    assert run.resume() == []
    assert run.state.facts_beside is None


def test_facts_md_with_nothing_changed_reads_as_it_did() -> None:
    one = facts.Fact("a", "b", "call 1", 1)
    assert facts.record([one]) == (
        f"{facts.RECORD_HEADING}\n\n{facts.RECORD_ABOUT}\n\n"
        "- `a`: b (from call 1, kept at call 1)\n"
    )
