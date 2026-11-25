"""A run's start lessons are chosen by what it will build, and a new lesson isn't starved
(mqucifer/print-shop#313), with no Docker.

Every run, ledger, request and lesson here is made up in the test.
"""

from __future__ import annotations

from pathlib import Path

from print_shop.rounds import lessons
from print_shop.rounds.ledger import DesignLedger
from print_shop.rounds.run import Run
from print_shop.rounds.session import Limits
from tests.rounds_fakes import Scripted, tool
from tests.test_lessons import BY, approved, lesson, rows
from tests.test_lessons_cap import room_for
from tests.test_loop import written

#: A made-up table whose request names its bolts and its nut slots.
TABLE = (
    "A small side table, its top held to each leg by two bolts, each with a nut in a slot.\n\n"
    "## Kind\nfurniture\n"
)
#: The same kind, naming none of the features.
STOOL = "A low stool with a round seat.\n\n## Kind\nfurniture\n"
NOT_HELD = [{"command": "clearance", "verdict": "NOT HELD"}]


def furniture(tmp_path: Path) -> tuple[DesignLedger, dict[str, int]]:
    """Four kept lessons for the model: three for furniture and one general, scored on two
    earlier runs, and two candidates the last of them wrote, not yet kept."""
    ledger, ids = written(
        tmp_path,
        lesson("Round each leg's foot, so it doesn't splay on the bed."),
        lesson("Give the shelf's front edge a small chamfer.", kind="verify"),
        lesson("Name the apron's height once, at the top of the script."),
        lesson("Check the top's overhang against the request's figure.", kind="verify"),
        lesson(
            "A bolted joint reads NOT HELD on a slide: read it with `load` instead.",
            kind="verify",
            keys=NOT_HELD,
        ),
        lesson("Leave a nut's slot open to the side it drops in from.", kind="job"),
    )
    n = dict(zip(("foot", "shelf", "apron", "overhang", "bolt", "nut"), ids, strict=True))
    approved(tmp_path, "s")
    for name in ("foot", "shelf", "apron", "overhang"):
        lessons.accept(ledger, n[name], BY)
    for name in ("foot", "shelf", "overhang"):
        lessons.edit(ledger, n[name], BY, part_kind="furniture", why="only for furniture")
    lessons.edit(ledger, n["apron"], BY, part_kind="general", why="for every kind")
    held, _ = lessons.evidence(ledger, "r", [1])
    for run, name, scored in (
        ("r", "foot", "used"),
        ("s", "foot", "used"),
        ("r", "shelf", "used"),
        ("r", "apron", "used"),
        ("r", "overhang", "not_used"),
        ("s", "overhang", "not_used"),
    ):
        if not ledger.rows("SELECT 1 FROM run_lessons WHERE run=? AND lesson=?", run, n[name]):
            lessons.give(ledger, run, [lessons.one(ledger, n[name])], "by hand", BY)
        lessons.score(ledger, run, n[name], scored, held, BY)
    return ledger, n


def accept_now(ledger: DesignLedger, *named: int) -> None:
    """Accepted for furniture just before the next run starts, so no run has scored them."""
    for one in named:
        lessons.accept(ledger, one, BY, part_kind="furniture")


def start_of(tmp_path: Path, request: str, budget: int) -> tuple[Run, str]:
    model = Scripted([tool("blocked", reason="x")])
    limits = Limits(start_lessons_chars=budget)
    run = Run.start(tmp_path, "next", request, [], model, limits, sleep=lambda _: None)
    run.drive()
    return run, str(model.bodies[0]["messages"][1]["content"])


def ids(found: list[dict[str, object]]) -> list[int]:
    return [int(str(one["id"])) for one in found]


def test_the_features_a_request_names_and_a_lesson_is_about(tmp_path: Path) -> None:
    assert lessons.features_named(TABLE, "furniture") == ("bolts", "nut slots")
    assert lessons.features_named(STOOL, "furniture") == ()
    assert lessons.features_named("A nutshell, screwless.", None) == ()
    assert lessons.features_named("A small panel.", "Stained glass") == ("glass",)
    assert lessons.features_named("It came out warped.", None) == ()
    ledger, n = furniture(tmp_path)
    # By its words, its keys (a slide's NOT HELD is a joint's) and its kind of part.
    assert lessons.features_of(ledger, lessons.one(ledger, n["bolt"])) == ("bolts", "joints")
    assert lessons.features_of(ledger, lessons.one(ledger, n["nut"])) == ("nut slots",)
    assert lessons.features_of(ledger, lessons.one(ledger, n["foot"])) == ()


def test_a_request_that_names_bolts_gets_the_bolt_lesson_ahead_of_an_older_unrelated_one(
    tmp_path: Path,
) -> None:
    ledger, n = furniture(tmp_path)
    accept_now(ledger, n["bolt"])
    # Before, its standing was the runs that used it: the foot's two first, the bolt's none last.
    before = lessons.shown(ledger, lessons.to_give(ledger, "furniture"), "furniture")
    assert ids(before)[:3] == [n["foot"], n["shelf"], n["overhang"]]
    assert ids(before)[3] == n["bolt"]
    # The request names bolts, so the bolt lesson is taken first of its kind.
    named = lessons.features_named(TABLE, "furniture")
    assert ids(lessons.to_give(ledger, "furniture", named))[0] == n["bolt"]
    # With room for one lesson of the kind, it's the one given; the older foot's is left out.
    run, start = start_of(tmp_path, TABLE, room_for(ledger, n["bolt"]))
    assert f"**Lesson {n['bolt']}**" in start
    assert f"**Lesson {n['foot']}**" not in start
    given = rows(ledger, "SELECT lesson, how FROM run_lessons WHERE run='next'")
    assert given == [{"lesson": n["bolt"], "how": "start"}]
    # A request that names none still gets its best-scored first.
    assert ids(lessons.to_give(ledger, "furniture", ()))[0] == n["foot"]


def test_a_lesson_accepted_a_minute_before_a_start_is_in_it_when_it_fits(tmp_path: Path) -> None:
    ledger, n = furniture(tmp_path)
    # Its first run's newest, accepted just before the rerun starts.
    accept_now(ledger, n["nut"])
    newest = lessons.one(ledger, n["nut"])
    assert newest["status"] == "accepted" and not lessons.latest_scores(ledger, n["nut"])
    budget = room_for(ledger, n["foot"], n["nut"])
    # Before, it fell to id order behind each older scored one, and wasn't given.
    before, _ = lessons.within(
        lessons.shown(ledger, lessons.to_give(ledger, "furniture"), "furniture"), budget
    )
    assert n["nut"] not in ids(before)
    run, start = start_of(tmp_path, TABLE, budget)
    assert f"**Lesson {n['nut']}**" in start
    assert lessons.one(ledger, n["nut"])["status"] == "given"


def test_an_unscored_lesson_competes_on_a_prior_not_on_nothing(tmp_path: Path) -> None:
    ledger, n = furniture(tmp_path)
    accept_now(ledger, n["nut"])
    # With nothing named: used on every run that scored it is ahead of a new one, at a half, and
    # a new one is ahead of one no run that scored it used.
    order = ids(lessons.to_give(ledger, "furniture", ()))
    assert order == [n["foot"], n["shelf"], n["nut"], n["overhang"], n["apron"]]
    assert (lessons.PRIOR_USED, lessons.PRIOR_RUNS) == (1, 2)


def test_the_start_names_each_lesson_it_left_out(tmp_path: Path) -> None:
    ledger, n = furniture(tmp_path)
    accept_now(ledger, n["bolt"], n["nut"])
    run, start = start_of(tmp_path, TABLE, room_for(ledger, n["bolt"], n["nut"]))
    left = [n[k] for k in ("foot", "shelf", "overhang", "apron")]
    assert (
        "4 more kept lessons aren't here, to keep the start short: 3 for furniture parts and 1 "
        "general. The `lessons` tool finds them"
    ) in start
    assert (
        "Left out, each by its number, area and kind of part:\n"
        f"- Lesson {left[0]} (understanding the job, for furniture parts)\n"
        f"- Lesson {left[1]} (verification, for furniture parts)\n"
        f"- Lesson {left[2]} (verification, for furniture parts)\n"
        f"- Lesson {left[3]} (understanding the job, general)"
    ) in start
    out = rows(ledger, "SELECT lesson FROM run_lessons_left WHERE run='next' ORDER BY rowid")
    assert [o["lesson"] for o in out] == left
    # The record says the same.
    assert (run.folder / "lessons.md").read_text(encoding="utf-8").count("- Lesson ") == 4
    # One about a feature says so, and one too long for any start says that too.
    found = {**lessons.one(ledger, n["bolt"]), "about": ("bolts", "joints"), "too_long": True}
    assert lessons.left_line(found) == (
        f"- Lesson {n['bolt']} (verification, for furniture parts; about bolts, joints); "
        "too long for the start"
    )


def test_a_ledger_whose_lessons_all_fit_reads_as_it_did(tmp_path: Path) -> None:
    ledger, n = furniture(tmp_path)
    accept_now(ledger, n["bolt"], n["nut"])
    every = [n[k] for k in ("foot", "shelf", "overhang", "bolt", "nut", "apron")]
    # Chosen by what the table names, the bolt's and the nut's are taken first ...
    named = lessons.features_named(TABLE, "furniture")
    assert ids(lessons.to_give(ledger, "furniture", named))[:2] == [n["bolt"], n["nut"]]
    # ... but with room for all, the start is as it was: its kind's, used most first, then
    # general, oldest first among equals, and nothing said to be left out.
    run, start = start_of(tmp_path, TABLE, lessons.START_CHARS)
    lines = [lessons.line(lessons.one(ledger, i)) for i in every]
    was = "\n".join([lessons.HEADING, "", lessons.ABOUT, "", *lines])
    assert (run.folder / "lessons.md").read_text(encoding="utf-8") == was + "\n"
    assert was in start
    assert "aren't here" not in start and "Left out" not in start
    given = rows(ledger, "SELECT lesson FROM run_lessons WHERE run='next' ORDER BY rowid")
    assert [g["lesson"] for g in given] == every
    assert not rows(ledger, "SELECT 1 FROM run_lessons_left WHERE run='next'")
