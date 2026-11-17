"""A run starts with a bounded set of lessons, and finds the rest with a tool
(mqucifer/print-shop#155), with no Docker.

Every run, ledger and lesson here is made up in the test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.rounds import after_action, lessons, transcript
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, tool
from tests.test_lessons import BY, approved, cli, lesson, rows
from tests.test_loop import written

GLASS = "A small panel for a window.\n\n## Kind\nstained-glass\n"


def kept(tmp_path: Path) -> tuple[DesignLedger, dict[str, int]]:
    """Seven kept lessons for the model: three general, three for stained glass and one for an
    enclosure, scored on two earlier runs."""
    ledger, ids = written(
        tmp_path,
        lesson("Name each height once, at the top of the script."),
        lesson("Lay the outline out in 2D before any solid."),
        lesson("Blunt a pane's point where two leads meet.", kind="verify"),
        lesson("Leave the came's channel open at the hanging loop."),
        lesson("Check each pane's seat with capture, not by eye.", kind="verify"),
        lesson("Give a lid's catch a lead-in."),
        lesson("Read the request's figures again before the plan.", kind="memory"),
    )
    named = dict(zip(("a", "b", "c", "d", "e", "f", "g"), ids, strict=True))
    # A second earlier run, started before any is kept, so its start gives none.
    approved(tmp_path, "s")
    for n in ids:
        lessons.accept(ledger, n, BY)
    for name in ("c", "d", "e"):
        lessons.edit(ledger, named[name], BY, part_kind="stained-glass", why="only for panes")
    lessons.edit(ledger, named["f"], BY, part_kind="enclosure", why="only for lids")
    held, _ = lessons.evidence(ledger, "r", [1])
    for run, name, scored in (
        ("r", "b", "used"),
        ("r", "g", "used"),
        ("s", "g", "used"),
        ("r", "e", "used"),
        ("r", "d", "in_the_way"),
    ):
        one = lessons.one(ledger, named[name])
        if not ledger.rows(
            "SELECT 1 FROM run_lessons WHERE run=? AND lesson=?", run, named[name]
        ):
            lessons.give(ledger, run, [one], "by hand", BY)
        lessons.score(ledger, run, named[name], scored, held, BY)
    return ledger, named


def room_for(ledger: DesignLedger, *given: int) -> int:
    """A budget that just holds these lessons' lines."""
    return sum(len(lessons.line(lessons.one(ledger, n))) for n in given)


def told(run: Run) -> list[dict[str, Any]]:
    return [e for e in transcript.events_of(run.session.events) if e["kind"] == "tool"]


def test_the_start_gives_the_kinds_lessons_first_then_general_best_scored_first(
    tmp_path: Path,
) -> None:
    ledger, n = kept(tmp_path)
    # Its kind's first, then general; in each, used most first, and in the way last.
    order = [n[k] for k in ("e", "c", "d", "g", "b", "a")]
    assert [g["id"] for g in lessons.to_give(ledger, "Stained glass")] == order
    assert [g["id"] for g in lessons.to_give(ledger)] == [n["g"], n["b"], n["a"]]
    assert [g["id"] for g in lessons.to_give(ledger, "enclosure")][0] == n["f"]
    given, left = lessons.within(lessons.to_give(ledger, "stained-glass"), room_for(ledger, *order))
    assert ([g["id"] for g in given], left) == (order, [])


def test_a_ledger_with_more_than_the_budget_says_how_many_it_left_out(tmp_path: Path) -> None:
    ledger, n = kept(tmp_path)
    first = [n[k] for k in ("e", "c", "d", "g")]
    limits = Limits(start_lessons_chars=room_for(ledger, *first) + 5)
    model = Scripted([tool("blocked", reason="x")])
    run = Run.start(tmp_path, "next", GLASS, [], model, limits, sleep=lambda _: None)
    run.drive()
    start = model.bodies[0]["messages"][1]["content"]
    at = [start.index(f"**Lesson {i}**") for i in first]
    assert at == sorted(at)
    for left in ("a", "b", "f"):
        assert f"**Lesson {n[left]}**" not in start
    assert (
        "2 more kept lessons aren't here, to keep the start short: 2 general. The `lessons` "
        "tool finds them"
    ) in start
    given = rows(ledger, "SELECT lesson, how FROM run_lessons WHERE run='next' ORDER BY rowid")
    assert [(g["lesson"], g["how"]) for g in given] == [(i, "start") for i in first]
    out = rows(ledger, "SELECT * FROM run_lessons_left WHERE run='next' ORDER BY rowid")
    assert [o["lesson"] for o in out] == [n["b"], n["a"]]
    assert f"the start's budget of {limits.start_lessons_chars} characters" in out[0]["why"]
    # One left out isn't given: it stays as it was, and it's never dropped.
    assert lessons.one(ledger, n["a"])["status"] == "accepted"
    assert lessons.one(ledger, n["c"])["status"] == "given"
    assert rows(ledger, "SELECT limits FROM runs WHERE run='next'")[0]["limits"].count(
        "start_lessons_chars"
    )
    # A conversation started again from the record says the same.
    run._from_the_record()
    assert "2 more kept lessons aren't here" in run.session.messages[1]["content"]
    # The listing of the run says what it was given and what was left out.
    listed = lessons.listing(ledger, "next")
    assert "## Left out of its start" in listed
    assert f"- Lesson {n['b']}: over the start's budget" in listed


def test_the_budget_has_a_stated_default_and_none_fits_a_budget_of_nothing(
    tmp_path: Path,
) -> None:
    ledger, n = kept(tmp_path)
    assert Limits().start_lessons_chars == lessons.START_CHARS == 6000
    found = lessons.to_give(ledger, "stained-glass")
    given, left = lessons.within(found, 0)
    assert (given, [one["id"] for one in left]) == ([], [one["id"] for one in found])
    said = lessons.block(given, left, "stained-glass")
    assert said.startswith(lessons.HEADING)
    assert "6 more kept lessons aren't here" in said
    assert "3 for stained-glass parts and 3 general" in said
    # They're taken in order: the first that doesn't fit ends the start's, so none jumps a better.
    given, left = lessons.within(found, room_for(ledger, n["e"]) + 3)
    assert [g["id"] for g in given] == [n["e"]]
    assert len(left) == 5


def test_the_tool_finds_a_lesson_left_out_by_words_and_by_kind(tmp_path: Path) -> None:
    ledger, n = kept(tmp_path)
    limits = Limits(start_lessons_chars=room_for(ledger, n["e"], n["c"], n["d"], n["g"]))
    model = Scripted(
        [tool("lessons", words="2D outline")],
        [tool("lessons", kind="general")],
        [tool("lessons", kind="Enclosure")],
        [tool("lessons", id=n["b"])],
        [tool("lessons", id=999)],
        [tool("lessons", words="gearbox")],
        [tool("lessons")],
        [tool("lessons", id=n["e"])],
        [tool("blocked", reason="x")],
    )
    run = Run.start(tmp_path, "next", GLASS, [], model, limits, sleep=lambda _: None)
    run.drive()
    assert "lessons" in {t["function"]["name"] for t in model.bodies[0]["tools"]}
    by_words, general, lids, whole, missing, nothing, bare, had, _ = told(run)
    # A short reply: each lesson's number, area, kind of part and fix's shape, not its words.
    assert by_words["outcome"] == "ok"
    assert f"- Lesson {n['b']} (understanding the job, general): plan in 2D" in by_words["reply"]
    assert "Lay the outline out" not in by_words["reply"]
    assert by_words["detail"]["found"][0] == n["b"]
    assert [n[k] for k in ("g", "b", "a")] == general["detail"]["found"]
    assert f"- Lesson {n['g']} (memory within a run, general, in your start)" in general["reply"]
    assert lids["detail"]["found"] == [n["f"]]
    assert f"- Lesson {n['f']} (understanding the job, for enclosure parts)" in lids["reply"]
    # One whole, by its number: as the start would have given it, and the run was given it.
    assert whole["outcome"] == "ok"
    assert lessons.line(lessons.one(ledger, n["b"])) in whole["reply"]
    assert whole["detail"] == {"read": n["b"], "given": "tool"}
    assert missing["outcome"] == "failed" and "no kept lesson 999" in missing["reply"]
    assert nothing["outcome"] == "ok" and nothing["detail"]["found"] == []
    assert "There's no kept lesson with 'gearbox'. Try other words" in nothing["reply"]
    # With nothing asked, the run's own: its kind's and the general ones.
    assert len(bare["detail"]["found"]) == 6
    # One it had at the start is read again, and stays as given at the start.
    assert had["detail"] == {"read": n["e"], "given": "start"}
    given = rows(ledger, "SELECT lesson, how, text FROM run_lessons WHERE run='next'")
    assert {(g["lesson"], g["how"]) for g in given} == {
        *((n[k], "start") for k in ("e", "c", "d", "g")),
        (n["b"], "tool"),
    }
    assert lessons.one(ledger, n["b"])["status"] == "held"
    assert lessons.one(ledger, n["a"])["status"] == "accepted"
    # Its use is recorded like any tool's, and the review is asked to score the one it read.
    used = rows(ledger, "SELECT tools FROM calls WHERE run='next' ORDER BY n")
    assert sum("lessons" in u["tools"] for u in used) == 8
    asked = after_action.ask(ledger, "next", 9)
    assert f"- Lesson {n['b']}, given tool: " in asked
    assert f"- Lesson {n['a']}," not in asked
    held, _ = lessons.evidence(ledger, "next", [4])
    lessons.score(ledger, "next", n["b"], "used", held, BY)
    with pytest.raises(lessons.Refused, match="wasn't given lesson"):
        lessons.score(ledger, "next", n["a"], "used", held, BY)


def test_the_review_lists_a_kept_lesson_with_no_kind_and_gives_it_one(tmp_path: Path) -> None:
    ledger, (plain, lid, pane, candidate) = written(
        tmp_path,
        lesson("Name each height once."),
        lesson("Give a lid's catch a lead-in."),
        lesson("Blunt a pane's point."),
        lesson("Keep the hinge's pin loose."),
    )
    for one in (plain, lid, pane):
        lessons.accept(ledger, one, BY)
    listed = lessons.listing(ledger)
    assert "## Kept for the model with no kind of part, for the reviewer" in listed
    for one in (plain, lid, pane):
        assert f"- Lesson {one} (understanding the job): plan in 2D." in listed
    assert f"- Lesson {candidate} (" not in listed
    assert "`edit ID --part-kind K`, or `--part-kind general`" in listed
    # Said to be general, it's general and no longer listed; given a kind, it's that kind's.
    said = cli(tmp_path, "lessons", "edit", str(plain), "--by", BY, "--part-kind", "general")
    assert said.returncode == 0, said.stderr
    said = cli(tmp_path, "lessons", "edit", str(lid), "--by", BY, "--part-kind", "Enclosure")
    assert said.returncode == 0, said.stderr
    said = cli(
        tmp_path, "lessons", "accept", str(candidate), "--by", BY, "--part-kind", "stained glass"
    )
    assert said.returncode == 0, said.stderr
    kinds = {
        r["id"]: r["part_kind"] for r in rows(ledger, "SELECT id, part_kind FROM lessons")
    }
    assert kinds == {
        plain: "general",
        lid: "enclosure",
        pane: None,
        candidate: "stained-glass",
    }
    step = rows(ledger, "SELECT * FROM lesson_steps WHERE lesson=? ORDER BY id", candidate)[-1]
    assert step["status"] == "accepted" and "for stained-glass parts" in step["why"]
    listed = cli(tmp_path, "lessons").stdout
    assert f"- Lesson {pane} (understanding the job)" in listed
    for one in (plain, lid, candidate):
        assert f"- Lesson {one} (understanding the job): plan in 2D." not in listed
    # A general one, by either way of saying so, starts every run.
    assert [g["id"] for g in lessons.to_give(ledger, "enclosure")] == [lid, plain, pane]
    assert [g["id"] for g in lessons.to_give(ledger)] == [plain, pane]
    # Its kind's lessons that its skill doesn't hold yet are listed, to fold in.
    assert "## To fold into a kind's skill, for the reviewer" in listed
    assert f"- `enclosure`, the `designing-enclosures` skill: lesson {lid}." in listed
    assert f"- `stained-glass`, the `designing-stained-glass` skill: lesson {candidate}." in listed
    lessons.promote(ledger, lid, BY, "skill", "src/print_shop/rounds/skills/enclosure")
    assert f"lesson {lid}." not in lessons.listing(ledger)
    assert [g["id"] for g in lessons.to_give(ledger, "enclosure")] == [plain, pane]
