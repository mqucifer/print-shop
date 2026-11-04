"""The learning loop (mqucifer/print-shop#40), with no Docker: each lesson's area and routes, its
keys, delivery and score on each route, its status from each run's latest score, and a ledger from
before the five areas sorted once with every row kept.

Every run and lesson here is made up in the test.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import lessons
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_lessons import BY, again, approved, lesson, reviewed, rows

# Areas and routes


def written(tmp_path: Path, *found: dict[str, Any]) -> tuple[DesignLedger, list[int]]:
    run, _ = again(approved(tmp_path), reviewed(*found))
    assert run.after_action_review().name == "reviewed"
    return run.ledger, [int(r["id"]) for r in run.ledger.rows("SELECT id FROM lessons")]


def test_the_model_proposes_routes_and_the_reviewer_confirms_or_changes_them(
    tmp_path: Path,
) -> None:
    ledger, (mine, other) = written(
        tmp_path,
        lesson("Read the reference for how it's built.", routes=("model", "harness")),
        lesson("Say whether a lip is before play.", routes=("model",)),
    )
    assert sorted(lessons.routes_of(ledger, mine)) == ["harness", "model"]
    assert rows(ledger, "SELECT by FROM lesson_routes WHERE lesson=?", mine)[0]["by"] == "model"
    with pytest.raises(lessons.Refused, match="never given as a note"):
        lessons.accept(ledger, mine, BY)
    lessons.accept(ledger, mine, BY, "toolset_check")
    assert lessons.one(ledger, mine)["becomes"] == "toolset_check"
    # The reviewer sends it to the request and the operator instead.
    with pytest.raises(lessons.Refused, match="routes are one or more of"):
        lessons.accept(ledger, other, BY, routes=["sponsor"])
    lessons.accept(ledger, other, BY, routes=["request", "operator"])
    assert sorted(lessons.routes_of(ledger, other)) == ["operator", "request"]
    assert lessons.one(ledger, other)["becomes"] == "request"
    step = rows(ledger, "SELECT * FROM lesson_steps WHERE lesson=? ORDER BY id", other)[-1]
    assert step["why"] == "for operator, request, becomes request"
    assert json.loads(step["was"]) == {"routes": ["model"]}
    # Only one for the model is given at the start.
    assert [g["id"] for g in lessons.to_give(ledger)] == [mine]
    waiting = lessons.listing(ledger)
    assert "## Waiting to reach their route, for the operator" in waiting
    assert f"- Lesson {mine}, for harness: an issue in the next batch" in waiting
    assert f"`promote {other} --as checklist --where ...`" in waiting


def test_a_lesson_is_keyed_to_the_checks_it_names_or_its_evidence_shows(tmp_path: Path) -> None:
    run = approved(tmp_path)
    run.ledger.bench_call("r", 0, 2, ["capture", "frame.step"], 1, {"verdict": "FAIL"}, {}, 1.0)
    run.ledger.bench_call("r", 0, 2, ["thin", "frame.step"], 0, {"verdict": "OK"}, {}, 1.0)
    run.ledger.bench_call("r", 0, 2, ["info"], 3, None, {}, 1.0)
    run, _ = again(
        run,
        reviewed(
            lesson("Read the lip.", calls=[2]),
            lesson("Count the panes.", calls=[2], keys=[{"command": "capture --panes 3"}]),
        ),
    )
    run.after_action_review()
    shown, named = (int(r["id"]) for r in run.ledger.rows("SELECT id FROM lessons ORDER BY id"))
    assert lessons.keys_of(run.ledger, shown) == [{"command": "capture", "verdict": "FAIL"}]
    assert lessons.keys_of(run.ledger, named) == [{"command": "capture", "verdict": ""}]
    lessons.edit(run.ledger, shown, BY, keys=[{"command": "thin", "verdict": "FAIL"}], why="it")
    assert lessons.keys_of(run.ledger, shown) == [{"command": "thin", "verdict": "FAIL"}]
    assert "- **Keyed to:** `thin` FAIL" in lessons.listing(run.ledger, "r")
    with pytest.raises(lessons.Refused, match="keys are a list"):
        lessons.edit(run.ledger, shown, BY, keys=[{"verdict": "FAIL"}])


def test_the_start_holds_general_lessons_and_those_for_its_kind_of_part(tmp_path: Path) -> None:
    ledger, (general, glass) = written(
        tmp_path, lesson("Name each height once."), lesson("Blunt a pane's point.")
    )
    for n in (general, glass):
        lessons.accept(ledger, n, BY)
    lessons.edit(ledger, glass, BY, part_kind="stained-glass", why="only for panes")
    assert [g["id"] for g in lessons.to_give(ledger)] == [general]
    assert [g["id"] for g in lessons.to_give(ledger, "stained-glass")] == [general, glass]
    # Folded into a kind's skill, it's read there and not given at the start.
    lessons.promote(ledger, glass, BY, "skill", "src/print_shop/rounds/skills/glass")
    assert [g["id"] for g in lessons.to_give(ledger, "stained-glass")] == [general]


# Delivery and scores on each route


def test_each_route_is_scored_once_delivered_and_moves_the_status(tmp_path: Path) -> None:
    ledger, (n,) = written(tmp_path, lesson("Give a floor with every lip.", routes=("request",)))
    lessons.accept(ledger, n, BY)
    held, _ = lessons.evidence(ledger, "r", [1])
    with pytest.raises(lessons.Refused, match="wasn't given lesson"):
        lessons.score(ledger, "r", n, "used", held, BY)
    with pytest.raises(lessons.Refused, match="hasn't reached request yet"):
        lessons.score(ledger, "r", n, "used", held, BY, route="request")
    with pytest.raises(lessons.Refused, match="isn't for operator"):
        lessons.promote(ledger, n, BY, "checklist", "docs/rounds.md")
    lessons.promote(ledger, n, BY, "request", "project-print templates/request.md")
    assert lessons.one(ledger, n)["status"] == "promoted"
    lessons.score(ledger, "r", n, "used", held, BY, "the floor was given", route="request")
    assert lessons.one(ledger, n)["status"] == "held"
    found = rows(ledger, "SELECT route, score FROM lesson_scores WHERE lesson=?", n)
    assert found == [{"route": "request", "score": "used"}]
    shown = lessons.listing(ledger, "r")
    assert "## Scored by the analysis" in shown and "on request: used by" in shown


def test_a_lesson_built_elsewhere_stops_being_given_once_a_run_shows_it_holding(
    tmp_path: Path,
) -> None:
    ledger, (n,) = written(tmp_path, lesson("Read the reference's bars.", routes=("model",)))
    lessons.accept(ledger, n, BY)
    model = Scripted([tool("blocked", reason="x")])
    Run.start(tmp_path, "given", "A frame.", [], model, sleep=lambda _: None).drive()
    lessons.edit(ledger, n, BY, routes=["model", "harness"], becomes="harness", why="built too")
    lessons.promote(ledger, n, BY, "harness", "mqucifer/print-shop#36")
    assert [g["id"] for g in lessons.to_give(ledger)] == [n], "still given until it holds"
    held, _ = lessons.evidence(ledger, "given", [1])
    lessons.score(ledger, "given", n, "used", held, BY, "the breakdown held", route="harness")
    assert lessons.one(ledger, n)["status"] == "held"
    assert lessons.to_give(ledger) == []
    model_route = lessons.routes_of(ledger, n)["model"]
    assert model_route["ended_at"] and "its fix is built (harness)" in model_route["ended_why"]
    assert "its text stopped" in lessons.listing(ledger)


def test_a_corrected_score_replaces_that_runs_earlier_one(tmp_path: Path) -> None:
    ledger, (n,) = written(tmp_path, lesson("Run it before reasoning about it."))
    lessons.accept(ledger, n, BY)
    model = Scripted([tool("blocked", reason="x")])
    Run.start(tmp_path, "a", "A frame.", [], model, sleep=lambda _: None).drive()
    model = Scripted([tool("blocked", reason="x")])
    Run.start(tmp_path, "b", "A frame.", [], model, sleep=lambda _: None).drive()
    none: dict[str, Any] = {"calls": [], "readings": []}
    lessons.score(ledger, "a", n, "used", none, lessons.MODEL)
    assert lessons.one(ledger, n)["status"] == "held"
    lessons.score(ledger, "b", n, "in_the_way", none, lessons.MODEL)
    assert lessons.one(ledger, n)["status"] == "broken"
    # The reviewer's correction replaces b's score, and a's still says it holds.
    lessons.score(ledger, "b", n, "not_used", none, BY, "not following it isn't it in the way")
    assert lessons.one(ledger, n)["status"] == "held"
    lessons.score(ledger, "a", n, "not_used", none, BY, "it wasn't used after all")
    assert lessons.one(ledger, n)["status"] == "given"
    steps = rows(ledger, "SELECT status, why FROM lesson_steps WHERE lesson=? ORDER BY id", n)
    assert steps[-1] == {
        "status": "given",
        "why": "each run's latest score: no score since says whether it holds",
    }


# A ledger from before the five areas


def before_the_five(path: Path) -> None:
    """A ledger as it was: lessons of the three old kinds, one promoted, one given and scored
    in the way and then corrected to not used, as lesson 7 was."""
    ledger = DesignLedger(path)
    for run in ("old", "stainedglass-1"):
        ledger.run_started(
            run,
            request_sha="x",
            events="e",
            harness_commit="c",
            skills_sha="s",
            image="i",
            image_id=None,
            alias="crew-code-think",
            limits={},
        )
    ledger.add("calls", run="old", n=1, round=0, phase=0, segment=1, tools="[]")
    ledger.bench_call("old", 0, 1, ["thin", "a.step"], 1, {"verdict": "FAIL"}, {}, 1.0)
    old = [
        ("old", 1, "build", "held", None, None, None),
        ("old", 2, "check", "promoted", "refusal", "refusal", "mqucifer/print-shop#26"),
        ("old", 3, "build", "broken", None, None, None),
        ("old", 4, "context", "accepted", None, None, None),
        ("stainedglass-1", 10, "context", "accepted", None, None, None),
    ]
    with sqlite3.connect(path) as db:
        for run, n, kind, status, becomes, became, where in old:
            db.execute(
                "INSERT INTO lessons (id, run, written_by, written_at, fix_shape, kind, text, "
                "evidence, status, becomes, became, became_where) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?)",
                (n, run, BY, "t", "a shape", kind, f"lesson {n}", '{"calls": [1]}', status)
                + (becomes, became, where),
            )
            db.execute(
                "INSERT INTO lesson_steps (lesson, status, by, at) VALUES (?,?,?,?)",
                (n, status, BY, "t"),
            )
        db.execute("INSERT INTO run_lessons VALUES ('old', 3, 't', 'start', 'lesson 3')")
        db.execute("INSERT INTO run_lessons VALUES ('old', 2, 't', 'by hand', 'lesson 2')")
        for lesson_, score in ((2, "in_the_way"), (3, "in_the_way"), (3, "not_used")):
            db.execute(
                "INSERT INTO lesson_scores (run, lesson, score, calls, by, at) VALUES "
                "('old', ?, ?, '[]', ?, 't')",
                (lesson_, score, BY),
            )
        db.execute("DELETE FROM lesson_routes")
        db.execute("DELETE FROM lesson_keys")


def test_a_ledger_from_before_is_sorted_once_and_keeps_every_row(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    before_the_five(path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM lesson_steps WHERE by='the harness'")
        had = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("lessons", "lesson_scores", "run_lessons")}  # noqa: E501, S608
    ledger = DesignLedger(path)
    found = {r["id"]: dict(r) for r in ledger.rows("SELECT * FROM lessons")}
    assert {n: (f["kind"], f["status"], f["becomes"]) for n, f in found.items()} == {
        1: ("job", "held", None),
        2: ("verify", "promoted", "refusal"),
        3: ("job", "given", None),
        4: ("job", "accepted", None),
        10: ("job", "accepted", "request"),
    }
    routes = {n: sorted(lessons.routes_of(ledger, n)) for n in found}
    assert routes == {
        1: ["model"],
        2: ["harness"],
        3: ["model"],
        4: ["operator"],
        10: ["operator", "request"],
    }
    assert lessons.routes_of(ledger, 2)["harness"]["became_where"] == "mqucifer/print-shop#26"
    assert lessons.keys_of(ledger, 1) == [{"command": "thin", "verdict": "FAIL"}]
    sorted_ = rows(ledger, "SELECT * FROM lesson_steps WHERE lesson=3 AND by='the harness'")
    assert [(s["status"], json.loads(s["was"] or "null")) for s in sorted_] == [
        ("broken", {"kind": "build", "becomes": None}),
        ("given", None),
    ]
    assert "put right once" in sorted_[1]["why"]
    assert lessons.to_retire(ledger) == [], "the promoted one's score came before it was built"
    for table, n in had.items():
        assert ledger.rows(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == n  # noqa: S608
    steps = len(ledger.rows("SELECT * FROM lesson_steps"))
    DesignLedger(path)
    assert len(ledger.rows("SELECT * FROM lesson_steps")) == steps, "sorted once"
    # Older code writing a lesson of an old kind later is sorted when the ledger next opens.
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO lessons (run, written_by, written_at, fix_shape, kind, text, evidence, "
            "status) VALUES ('old', 'model', 't', 's', 'check', 'late', '{}', 'candidate')"
        )
    DesignLedger(path)
    late = ledger.rows("SELECT * FROM lessons WHERE text='late'")[0]
    assert late["kind"] == "verify" and sorted(lessons.routes_of(ledger, late["id"])) == [
        "harness"
    ]


# What the review sees


def test_the_index_shows_the_harnesss_record_and_peoples_words_at_their_calls(
    tmp_path: Path,
) -> None:
    from print_shop.rounds import after_action, needs
    from print_shop.rounds.needs import Need
    from tests.rounds_fakes import Spoken
    from tests.test_close import Answers

    lip = "capture frame.step --min-lip"
    model = Scripted(
        [tool("write", path="a.py", content="x = 1\n")],
        [tool("edit", path="a.py", old="x = 2", new="x = 3")],
        *[[tool("bench", command=f"{lip} {v}")] for v in ("1.2", "0.8", "0.5", "0.3")],
        Spoken([tool("read", path="a.py")], finish="length", usage={"completion_tokens": 900}),
        [tool("blocked", reason="Out of turns.")],
    )
    run = Run.start(tmp_path, "r", "A block.", [], model, sleep=lambda _: None)
    # `capture` is the stained-glass toolset's, as a request's `## Kind` loads it.
    run.state.kind = "stained-glass"
    run.tools.use(run.toolset)
    run.state.needs = needs.dump(
        [Need("held", "held", "request", "bench", command=f"{lip} 1.2", verdicts=["OK"])]
    )
    run.state.needs_open = False
    box: Any = Answers(run.work, 1, 1, 1, 1)
    run.tools.box = box
    assert run.drive().name == "blocked"
    run._note("The light has to pass\nthrough every pane.", "sponsor")
    run.session.compact()
    run.session._record(
        "compacted", estimated=130_000, estimated_after=40_000, prompt_before=1000, before_call=9
    )
    shown = "\n".join(after_action.index(run.ledger, "r"))
    assert "2: edit\n   a failed edit: edit.closest" in shown
    assert "6: bench `capture frame.step --min-lip 0.3` FAIL\n   nudged: `capture` on " in shown
    assert "7: read\n   runaway (limit): 900 tokens" in shown
    assert (
        "[said 1] before call 9, note from sponsor:\n    > The light has to pass\n"
        "    > through every pane."
    ) in shown
    assert (
        "(before call 9: compaction at about 130,000 tokens: 5 tool replies shrunk to a line "
        "(bench 3, edit 1, write 1); the reasoning of 6 turns dropped; 2 `write` or `edit` texts "
        "made a note)"
    ) in shown, "a whole read stays, and call 7's reasoning went at once, cut off at the limit"
    cut = rows(run.ledger, "SELECT said FROM timeline WHERE kind='compaction'")[0]["said"]
    assert json.loads(cut)["replies_shrunk"] == {"bench": 3, "edit": 1, "write": 1}
    assert [r["why"] for r in rows(run.ledger, "SELECT why FROM refusals")] == ["edit.closest"]


def test_the_review_weighs_each_thing_a_person_said(tmp_path: Path) -> None:
    run = approved(tmp_path)
    run, model = again(
        run,
        reviewed(lesson("Light passes through."), people=[]),
        reviewed(lesson("Light passes through."), people=[{"said": 1, "lesson": 2, "why": "x"}]),
        reviewed(
            lesson("Light passes through."),
            people=[{"said": 1, "lesson": 1, "why": "their note is a finding"}],
        ),
    )
    assert run.after_action_review().name == "reviewed"
    replies = [e["reply"] for e in rows_of_tools(run)]
    assert "[said 1] weren't weighed: say of each whether it should be a lesson" in replies[-3]
    assert "the number of one of your 1 lessons, or null" in replies[-2]
    asked = model.bodies[0]["messages"][-1]["content"]
    assert "[said 1] before call 4, approved from sponsor:\n    > Print it." in asked
    assert "A person's finding that never became a lesson is a lesson missed." in asked
    weighed = rows(run.ledger, "SELECT said, lesson, why, by FROM said_weighed")
    assert weighed == [{"said": 1, "lesson": 1, "why": "their note is a finding", "by": "model"}]


def rows_of_tools(run: Run) -> list[dict[str, Any]]:
    from print_shop.rounds import transcript

    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def test_a_review_near_its_limit_goes_on_from_the_record_and_is_recorded(tmp_path: Path) -> None:
    from print_shop.rounds.session import Limits

    run = approved(tmp_path)
    model = Scripted(*[reviewed(lesson("Ask on the layout."))], prompt_tokens=10_000)
    run = Run.load(run.folder, model, Limits(compact_at=500), sleep=lambda _: None)
    assert run.after_action_review().name == "reviewed"
    assert run.state.restarts == 1
    sent = model.bodies[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "user", "user"]
    assert "An earlier conversation about this ran out of room" in sent[1]["content"]
    assert sent[2]["content"] == "The Sponsor approved the part. Their note:\n\nPrint it."
    assert sent[3]["content"].startswith("The part is approved.")
    assert {t["function"]["name"] for t in model.bodies[-1]["tools"]} == {"read", "after_action"}
    tried = rows(run.ledger, "SELECT ended FROM after_actions")
    assert tried == [{"ended": "reviewed"}]
    line = rows(run.ledger, "SELECT kind, name FROM timeline ORDER BY seq")
    review = [(r["kind"], r["name"]) for r in line][-5:]
    assert review == [
        ("after_action", None),
        ("compaction", None),
        ("stop", "context_full"),
        ("restart", None),
        ("stop", "reviewed"),
    ]
    assert rows(run.ledger, "SELECT COUNT(*) AS n FROM lessons")[0]["n"] == 1


# The effort an alias sends


def model_info(*entries: dict[str, Any], status: int = 200) -> Any:
    """LiteLLM's `/model/info`, as its documentation gives it, and nothing else answering."""
    import httpx

    def answer(request: httpx.Request) -> httpx.Response:
        # LiteLLM answers it as `/model/info` and `/v1/model/info`, beside the calls' base.
        assert request.url.path.endswith("/model/info"), "no model call, and no other path"
        return httpx.Response(status, json={"data": list(entries)})

    return httpx.MockTransport(answer)


ADDRESS = "http://served.example:30000/v1"


def deployment(alias: str, **params: Any) -> dict[str, Any]:
    return {
        "model_name": alias,
        "litellm_params": {"model": "openai/served", "api_base": ADDRESS, **params},
        "model_info": {"id": "abc", "mode": "chat"},
    }


@pytest.mark.parametrize(
    ("entries", "status", "said"),
    [
        ([deployment("crew-code-think", reasoning_effort="low")], 200, ("low", "reasoning_effort")),
        (
            [deployment("crew-code-think", extra_body={"chat_template_kwargs": {"reasoning_effort": "high"}})],  # noqa: E501
            200,
            ("high", "extra_body.chat_template_kwargs.reasoning_effort"),
        ),
        ([deployment("crew-code-think")], 200, ("unknown", "doesn't name an effort")),
        ([deployment("other", reasoning_effort="low")], 200, ("unknown", "has no `crew-code")),
        ([], 403, ("unknown", "LiteLLM answered 403")),
        (
            [
                deployment("crew-code-think", reasoning_effort="low"),
                deployment("crew-code-think", reasoning_effort="high"),
            ],
            200,
            ("unknown", "deployments send high, low"),
        ),
    ],
)
def test_a_run_records_the_effort_its_alias_sends_as_litellm_says_it(
    entries: list[dict[str, Any]], status: int, said: tuple[str, str]
) -> None:
    from print_shop.rounds.send import Sender

    sender = Sender(key="k", transport=model_info(*entries, status=status))
    effort, where = sender.effort("crew-code-think")
    assert effort == said[0] and said[1] in where
    assert ADDRESS not in where and "served.example" not in where


def test_the_runs_row_and_summary_say_the_effort_or_that_its_unknown(tmp_path: Path) -> None:
    from print_shop.rounds.run import summarise

    class Told(Scripted):
        def effort(self, alias: str) -> tuple[str, str]:
            return "low", f"LiteLLM's /model/info for `{alias}`, litellm_params.reasoning_effort"

    model = Told([tool("blocked", reason="x")])
    run = Run.start(tmp_path, "told", "A block.", [], model, sleep=lambda _: None)
    run.drive()
    row = rows(run.ledger, "SELECT reasoning_effort, reasoning_effort_from FROM runs")[0]
    assert row["reasoning_effort"] == "low" and "litellm_params" in row["reasoning_effort_from"]
    assert summarise(run.folder)["reasoning_effort"]["effort"] == "low"
    quiet = Run.start(
        tmp_path, "quiet", "A block.", [], Scripted([tool("blocked", reason="x")]),
        sleep=lambda _: None,
    )
    quiet.drive()
    found = rows(quiet.ledger, "SELECT reasoning_effort FROM runs WHERE run='quiet'")[0]
    assert found["reasoning_effort"] == "unknown"
