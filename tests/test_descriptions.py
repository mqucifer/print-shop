"""Each description of the part says which version it describes, a stale one is caught, and
what's relayed says who said it (mqucifer/print-shop#53).

No Docker: a scripted model drives each run, and a box runs nothing. Its "script" writes the part
from what the test last set, so the part changes when the test says. The request is a made-up
shelf bracket, nothing from a real run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import overlap as overlap_module
from print_shop.rounds import descriptions, records, transcript
from print_shop.rounds.box import Ran
from print_shop.rounds.run import Refused, Run
from tests.rounds_fakes import Scripted, tool
from tests.test_intake import BROKEN_DOWN, Box, picture

REQUEST = """\
A shelf bracket, made up for this test.

## Requirements
- It holds the shelf.

## Kind
bracket
"""
ARM = {
    "id": "arm",
    "what": "the arm reaches 120 mm from the wall",
    "source": "derived",
    "covers": ["R1"],
    "check": {"by": "bench", "measures": "size", "command": "info bracket.step"},
}
PLAN = "Arm 120 mm, plate 4 mm thick, one body."
PLAN_ASK = tool(
    "ask", question="Is this the way?", stop="plan", plan=PLAN, pictures=["profile.png"]
)
SENT = [tool("send_ask", says="My profile: an L, in black on white.")]
MAKE = tool("run", path="make.py")


def done(**more: Any) -> dict[str, Any]:
    return tool(
        "done",
        script="make.py",
        part=more.pop("part", ["bracket.step"]),
        fix_shape="first",
        closing="Here it is.",
        **more,
    )


def step(body: str, second: str = "10:00:00") -> str:
    """A STEP file whose header names the second it was written, as CadQuery's do."""
    return (
        f"ISO-10303-21;\nHEADER;\nFILE_NAME('bracket.step','2026-11-05T{second}');\nENDSEC;\n"
        f"DATA;\n{body}\nENDSEC;\nEND-ISO-10303-21;\n"
    )


class Maker(Box):
    """`run` and a clean run write the part as the test last set it; `overlap` finds none."""

    def __init__(self, work: Path) -> None:
        super().__init__(work)
        self.parts: dict[str, str] = {"bracket.step": step("#1=ARM(120);")}

    def python(self, script: str, where: Path | None = None, timeout: int | None = None) -> Ran:
        for name, text in self.parts.items():
            (where or self.work).joinpath(name).write_text(text)
        return Ran(0, "made\n", 0.1)

    def helper(
        self, module: str, args: list[str], timeout: int = 120, where: Path | None = None
    ) -> Ran:
        if module == "overlap":
            return Ran(0, overlap_module.MARK + json.dumps({"files": {}, "pairs": []}), 0.1)
        return super().helper(module, args, timeout, where)


def begin(root: Path, *turns: Any, text: str = REQUEST) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    run = Run.start(root, "r", text, [], model, sleep=lambda _: None)
    return boxed(run)


def again(run: Run, *turns: Any) -> Run:
    model = Scripted(*turns, [tool("blocked", reason="Out of turns.")])
    return boxed(Run.load(run.folder, model, sleep=lambda _: None))


def boxed(run: Run) -> Run:
    box: Any = Maker(run.work)
    run.box = box
    run.tools.box = box
    (run.work / "make.py").write_text("print('made')\n")
    picture(run)
    return run


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def pick(run: Run, n: int, by: str = "sponsor") -> None:
    found = {"pick": "yes", "reason": "Go on.", "by": by}
    (run.folder / "asks" / str(n) / "pick.json").write_text(json.dumps(found))


def planned(root: Path) -> Run:
    """The bracket's breakdown, needs and plan, written before any part file, and the plan
    passed by the Sponsor."""
    run = begin(root, [BROKEN_DOWN], [tool("needs", needs=[ARM])], [PLAN_ASK], SENT)
    assert run.drive().name == "ask"
    pick(run, 1)
    return run


# Wanted 1 and 2: each description keeps its version, and a stale one holds `done`


def test_each_description_keeps_the_part_as_it_stood_when_it_was_written(tmp_path: Path) -> None:
    run = planned(tmp_path)
    kept = run.state.described
    assert set(kept) == {"breakdown", "need arm", "plan"}
    assert kept["breakdown"]["written_at"] == 1 and kept["breakdown"]["files"] == {}
    assert kept["need arm"]["text"] == "the arm reaches 120 mm from the wall"
    assert kept["plan"]["text"] == PLAN and kept["plan"]["what"] == "the plan, sent at ask 1"
    rows = run.ledger.rows("SELECT key, how, at_call FROM descriptions ORDER BY seq")
    assert [(r["key"], r["how"], r["at_call"]) for r in rows] == [
        ("breakdown", "written", 1),
        ("need arm", "written", 2),
        ("plan", "written", 4),
    ]


def test_done_waits_for_each_stale_description_and_the_stop_shows_what_was_said(
    tmp_path: Path,
) -> None:
    run = again(
        planned(tmp_path),
        [MAKE],
        [done()],
        [
            tool(
                "restate",
                descriptions=[
                    {"of": ["breakdown", "plan"], "holds": "One body, as built."},
                    {"of": "arm", "now": "the arm reaches 120 mm from the wall face"},
                ],
            )
        ],
        [done()],
    )
    run.resume()
    assert run.drive().name == "met"
    replies = told(run)
    made, refused, restated, met = replies[-4:]
    assert made["name"] == "run"
    assert refused["why"] == "descriptions_stale"
    assert refused["reply"].startswith("Nothing was checked: these describe the part as it was")
    assert (
        "- breakdown 1, written at call 1: since then `bracket.step` is new by call 5"
        in refused["reply"]
    )
    assert "- the need `arm`'s `what`, written at call 2: since then" in refused["reply"]
    assert "- the plan, sent at ask 1, written at call 4: since then" in refused["reply"]
    assert len(refused["detail"]["stale"]) == 3
    assert run.ledger.rows("SELECT COUNT(*) AS n FROM readings")[0]["n"] == 1, "one done ran"
    assert restated["outcome"] == "ok" and "Still written" not in restated["reply"]
    assert met["outcome"] == "ok"
    stop = (run.folder / "approvals" / "1" / "stop.md").read_text()
    assert descriptions.SINCE_HEADING in stop
    assert (
        "- breakdown 1, said to still hold at call 7, over `bracket.step` is new by call 5: "
        "One body, as built." in stop
    )
    assert "- the need `arm`'s `what`, sent again at call 7, over" in stop
    assert "  - was: the arm reaches 120 mm from the wall\n" in stop
    assert "  - now: the arm reaches 120 mm from the wall face" in stop
    # The need's description changed, and nothing else of it did.
    kept = json.loads((run.folder / "needs.json").read_text())
    assert kept[0]["what"] == "the arm reaches 120 mm from the wall face"
    assert kept[0]["command"] == "info bracket.step" and kept[0]["source"] == "derived"
    assert json.loads((run.work / "record" / "needs.json").read_text()) == kept
    stale = run.ledger.rows("SELECT key, found_at, changed FROM stale_descriptions ORDER BY seq")
    assert [(r["key"], r["found_at"]) for r in stale] == [
        ("breakdown", "done"),
        ("need arm", "done"),
        ("plan", "done"),
    ]
    assert json.loads(stale[0]["changed"]) == [["bracket.step", "new", 5]]
    shown = run.ledger.rows("SELECT key, how, why, shown_at FROM descriptions WHERE how!='written'")
    assert {(r["key"], r["how"], r["shown_at"]) for r in shown} == {
        ("breakdown", "holds", "approval 1"),
        ("plan", "holds", "approval 1"),
        ("need arm", "sent again", "approval 1"),
    }


def test_a_change_after_a_confirmation_makes_it_stale_again(tmp_path: Path) -> None:
    run = again(
        planned(tmp_path),
        [MAKE],
        [tool("restate", descriptions=[{"of": ["breakdown", "plan", "arm"], "holds": "As is."}])],
        [tool("write", path="note.txt", content="a script's change, no geometry\n")],
        [MAKE],
        [done()],
    )
    run.box.parts["bracket.step"] = step("#1=ARM(120);")  # type: ignore[attr-defined]
    run.resume()
    assert run.drive().name == "met", "a run that writes the same part changes nothing"
    run = again(run)
    run.state.stop = None
    run.state.ending = None
    run = again(run, [MAKE], [done()])
    run.box.parts["bracket.step"] = step("#1=ARM(140);")  # type: ignore[attr-defined]
    run.drive()
    refused = told(run)[-2]
    assert refused["why"] == "descriptions_stale"
    assert "said to hold at call 6: since then `bracket.step` changed by call" in refused["reply"]


def test_a_step_file_written_again_the_same_isnt_a_change(tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "a.step").write_text(step("#1=X;", "10:00:00"))
    first = descriptions.seen({}, descriptions.geometry(tmp_path / "w"), 3)
    (tmp_path / "w" / "a.step").write_text(step("#1=X;", "10:05:00"))
    second = descriptions.seen(first, descriptions.geometry(tmp_path / "w", first), 9)
    assert second["a.step"][:2] == [first["a.step"][0], 3]
    (tmp_path / "w" / "a.step").write_text(step("#1=Y;"))
    third = descriptions.seen(second, descriptions.geometry(tmp_path / "w", second), 12)
    assert third["a.step"][1] == 12
    (tmp_path / "w" / "a.step").unlink()
    assert descriptions.seen(third, {}, 14)["a.step"] == [None, 14]


def test_an_ask_after_a_change_shows_each_stale_description_under_its_heading(
    tmp_path: Path,
) -> None:
    run = again(
        planned(tmp_path),
        [MAKE],
        [tool("ask", question="Does it look right?", pictures=["profile.png"])],
        SENT,
    )
    run.resume()
    assert run.drive().name == "ask"
    drawn = told(run)[-2]
    assert "Descriptions written before the part changed, which go beside" in drawn["reply"]
    question = (run.folder / "asks" / "2" / "question.md").read_text()
    assert descriptions.HEADING in question
    assert (
        "- breakdown 1, written at call 1: since then `bracket.step` is new by call 5" in question
    )
    assert "- the need `arm`'s `what`, written at call 2" in question
    found = run.ledger.rows("SELECT found_at FROM stale_descriptions")
    assert {r["found_at"] for r in found} == {"ask 2"}


def test_a_helper_outside_the_part_doesnt_make_a_description_stale(tmp_path: Path) -> None:
    run = again(
        planned(tmp_path),
        [MAKE],
        [tool("restate", descriptions=[{"of": ["breakdown", "plan", "arm"], "holds": "As is."}])],
        [MAKE],
        [done()],
    )
    run.box.parts["pocket_cavity.step"] = step("#1=HELPER;")  # type: ignore[attr-defined]
    run.resume()
    # The helper is new after the confirmation, and it isn't in `part`.
    assert run.drive().name == "met"


def test_restate_is_refused_with_what_is_wrong_and_nothing_is_kept(tmp_path: Path) -> None:
    run = again(
        planned(tmp_path),
        [tool("restate", descriptions=[{"of": "breakdown", "holds": "Still right."}])],
        [MAKE],
        [tool("restate", descriptions=[{"of": "breakdown", "now": "new words"}])],
        [tool("restate", descriptions=[{"of": "shelf", "holds": "x"}])],
        [tool("restate", descriptions=[{"of": "arm", "holds": "x", "now": "y"}])],
        [tool("restate", descriptions=[{"of": ["arm", "plan"], "now": "y"}])],
        [tool("restate", descriptions=[])],
    )
    run.resume()
    run.drive()
    fresh, _, whole, unknown, both, several, empty = told(run)[-8:-1]
    assert fresh["outcome"] == "ok", "nothing to say holds isn't a refusal"
    assert "- breakdown 1: no part file has changed since call 1, so it's kept as it was" in (
        fresh["reply"]
    )
    assert fresh["reply"].startswith("Nothing was kept.")
    assert "The breakdown is sent again with `breakdown`, whole." in whole["reply"]
    assert (
        "`shelf` isn't a description kept. They are: `breakdown`, `arm`, `plan`."
        in (unknown["reply"])
    )
    assert (
        "give `holds`, why it still holds, or `now`, its new words, and not both" in (both["reply"])
    )
    assert "new words in `now` are for one at a time" in several["reply"]
    assert "`descriptions` is a list" in empty["reply"]
    assert not run.state.restated
    assert run.state.described["breakdown"]["held_at"] is None


def test_a_plan_with_no_figure_describes_nothing(tmp_path: Path) -> None:
    words = tool(
        "ask", question="This way?", stop="plan", plan="Profile first.", pictures=["profile.png"]
    )
    run = begin(tmp_path, [BROKEN_DOWN], [tool("needs", needs=[ARM])], [words], SENT)
    assert run.drive().name == "ask"
    assert "plan" not in run.state.described


def test_a_run_from_before_this_has_no_descriptions_and_done_isnt_held(tmp_path: Path) -> None:
    run = planned(tmp_path)
    kept = json.loads((run.folder / "state.json").read_text())
    for key in ("described", "geometry", "restated", "notes_from"):
        kept.pop(key)
    (run.folder / "state.json").write_text(json.dumps(kept))
    run = again(run, [MAKE], [done()])
    run.resume()
    assert run.drive().name == "met"


# Wanted 3 and 4: what's relayed says who said it, and the records keep the same


def asked(root: Path) -> Run:
    run = begin(root, [tool("questions", questions="How deep is the shelf?")])
    assert run.drive().name == "questions"
    (run.folder / "questions" / "1.answers.md").write_text("200 mm.\n")
    return run


def test_answers_say_who_gave_them_and_are_refused_without_who(tmp_path: Path) -> None:
    run = asked(tmp_path)
    with pytest.raises(Refused, match='the answers say who gave them: --from "sponsor"'):
        again(run).resume()
    with pytest.raises(Refused, match="Not 'the operator'"):
        again(run).resume(note_from="the operator")
    run = again(run)
    run.resume(note_from="stand-in: a builder in a test")
    said = run.session.messages[-1]["content"]
    assert said == "Answers from a stand-in for the Sponsor (a builder in a test):\n\n200 mm."
    assert "Sponsor's" not in said
    found = run.ledger.rows("SELECT answers, answered_by FROM questions")
    assert [(r["answers"], r["answered_by"]) for r in found] == [
        ("200 mm.", "stand-in: a builder in a test")
    ]
    run.drive()
    relayed = run.ledger.rows("SELECT kind, from_who FROM interventions")
    assert [(r["kind"], r["from_who"]) for r in relayed] == [
        ("answers", "stand-in: a builder in a test")
    ]


def test_the_sponsors_answers_are_said_as_theirs(tmp_path: Path) -> None:
    run = again(asked(tmp_path))
    run.resume(note_from="sponsor")
    assert run.session.messages[-1]["content"] == "The Sponsor's answers:\n\n200 mm."


@pytest.mark.parametrize(
    ("who", "words"),
    [
        ("sponsor", "A note from the Sponsor:"),
        ("stand-in: a builder in a test", "A note from a stand-in for the Sponsor (a builder in"),
        ("the people running this", "A note from the people running this:"),
        ("the operator", "A note from the operator:"),
    ],
)
def test_a_note_says_who_its_from_and_so_does_a_conversation_from_the_record(
    tmp_path: Path, who: str, words: str
) -> None:
    run = begin(tmp_path, [tool("blocked", reason="stop")])
    run.drive()
    run = again(run)
    run.resume("Make the arm rounder.", note_from=who)
    assert run.session.messages[-1]["content"].startswith(words)
    assert run.state.notes_from == [who]
    run._from_the_record()
    assert f"{words}" in run.session.messages[-1]["content"]
    if who != "sponsor":
        assert "the Sponsor:" not in run.session.messages[-1]["content"]


def test_a_conversation_from_the_record_keeps_older_notes_as_from_the_people_running_this(
    tmp_path: Path,
) -> None:
    run = begin(tmp_path, [tool("blocked", reason="stop")])
    run.drive()
    run.state.notes = ["An older note."]
    run.save()
    run = again(run)
    run.resume("A new one.", note_from="sponsor")
    run._from_the_record()
    first = run.session.messages[-1]["content"]
    assert "A note from the people running this:\n\nAn older note." in first
    assert "A note from the Sponsor:\n\nA new one." in first


def test_answers_from_before_this_still_read_as_relayed_as_the_sponsors() -> None:
    assert records._an_old_message("The Sponsor's answers:\n\nyes") == (
        "answers",
        records.ANSWERS_FROM,
    )
