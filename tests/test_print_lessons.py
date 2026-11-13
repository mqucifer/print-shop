"""Print lessons: a correction on one print reaches a later, similar part's proposal by itself.

mqucifer/print-shop#106. Each correction at the review and each outcome with something to say is
a candidate, keyed to the part's traits; a reviewer accepts, edits or rejects it, the CAD loop's
way; a kept lesson sets its setting for a later part with the same traits, and the review says
which lesson set it; the review scores it, and one used twice is listed to promote to the skill.
Everything here is a fixture: no Sponsor part, measurement or ledger row.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import trimesh

from print_shop.core.mqtt import command_topic
from print_shop.ledger import Answer, Ledger, PrintRecord, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.rounds.lessons import Refused
from print_shop.runtime import describe
from print_shop.setup import Proposer, lessons
from print_shop.setup.features import Feature, measure
from print_shop.setup.intake import Answer as IntakeAnswer
from print_shop.setup.intake import Intake, Option, Question, replay_asker
from print_shop.setup.proposal import Change, GivenLesson, PartFacts, Source, from_lessons, guard
from print_shop.setup.review import render
from print_shop.setup.traits import traits
from print_shop.slicer.orca import parse_config_block
from tests.fakes import CI_PROFILES, DATA, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import ANSWERS, Sponsor, _model, _questions

REVIEWER = "reviewer: operator"
CONFIG = parse_config_block(DATA / "recorded" / "cube.gcode")


def _features(**values: float) -> tuple[Feature, ...]:
    base = {"height": 20.0, "footprint": 20.0, "aspect": 1.0, "bed_contact": 400.0,
            "slenderness": 1.0, "overhang": 0.0, "steepest_overhang": 0.0, "bridge": 0.0,
            "flat_top": 400.0, "section": 6.0, "volume": 8000.0}
    return tuple(Feature(id=k, value=v, unit="", says=k) for k, v in (base | values).items())


#: A long thin bar's features, and another long thin part's: the traits they share.
RAIL = _features(footprint=150.0, aspect=7.5, height=20.0, bed_contact=3000.0)
STRIP = _features(footprint=120.0, aspect=6.0, height=10.0, bed_contact=1500.0, section=1.6)
CUBE = _features()


def _record(job_id: str, features: tuple[Feature, ...], *corrections: Change,
            stl: str = "rail.stl") -> PrintRecord:
    return PrintRecord(
        job_id=job_id, stl_name=stl, stl_sha256="ab" * 32, rotation_deg=(0.0, 0.0, 0.0),
        orientation_source="geometric", orientation_rationale="flat face down",
        orientation_accepted=True, gcode_name=f"{job_id}.gcode", print_time_s=600,
        filament_cm3=3.0, layer_count=100, slicer_config=CONFIG,
        printer_machine="Centauri Carbon", printer_firmware="V1.1.46",
        corrections=corrections, features=features,
    )


def _brim(reason: str = "the ends of a long part lifted") -> Change:
    return Change(key="brim_type", preset="no_brim", value="outer_only", reason=reason,
                  source=Source.SPONSOR)


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "ledger.sqlite") as opened:
        yield opened


# The part's traits
# --------------------------------------------------------------------------


def test_traits_come_from_the_features_by_their_thresholds() -> None:
    assert traits(RAIL) == ("long_and_thin",)
    assert traits(STRIP) == ("long_and_thin", "thin_walls")
    assert traits(CUBE) == ()
    assert traits(()) == (), "a part from before its features were kept has no traits, not 0s"


def test_a_measured_long_bar_is_long_and_thin(tmp_path: Path) -> None:
    bar = trimesh.creation.box(extents=(150, 20, 20))
    bar.apply_translation((75, 10, 10))
    bar.export(tmp_path / "bar.stl")
    features = measure(tmp_path / "bar.stl", 3000.0)
    assert next(f.value for f in features if f.id == "aspect") == 7.5
    assert "long_and_thin" in traits(features)


# Candidates, drawn from a print's row
# --------------------------------------------------------------------------


def test_a_correction_is_a_candidate_keyed_to_the_parts_traits(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", RAIL, _brim()))
    [found] = ledger.query("SELECT * FROM print_lessons")
    assert (found["setting"], found["value"], found["status"]) == (
        "brim_type", "outer_only", "candidate"
    )
    assert json.loads(found["traits"]) == ["long_and_thin"]
    assert found["text"] == "the ends of a long part lifted"
    [evidence] = ledger.query("SELECT * FROM print_lesson_evidence")
    assert evidence["job_id"] == "job-1"
    assert json.loads(evidence["detail"])["preset"] == "no_brim"
    assert lessons.for_part(ledger, RAIL) == (), "nothing reaches a proposal before a review"


def test_the_same_correction_again_is_more_evidence_not_a_second_lesson(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", RAIL, _brim()))
    ledger.record_start_requested(_record("job-2", STRIP, _brim("lifted again"), stl="strip.stl"))
    assert len(ledger.query("SELECT * FROM print_lessons")) == 1
    assert [e["job_id"] for e in ledger.query("SELECT * FROM print_lesson_evidence")] == [
        "job-1", "job-2"
    ]
    lessons.after_print(ledger, "job-2")
    assert len(ledger.query("SELECT * FROM print_lesson_evidence")) == 2, "drawn once"


def test_an_outcome_with_something_to_say_is_a_candidate(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", RAIL))
    ledger.record_started("job-1")
    ledger.record_ended("job-1", 9)
    ledger.record_answer("job-1", Answer(result=Result.FAILED, symptom=Symptom.WARPING,
                                         note="the ends curled up"))
    [found] = ledger.query("SELECT * FROM print_lessons")
    assert (found["drawn_from"], found["setting"], found["text"]) == (
        "outcome", None, "the ends curled up"
    )


def test_a_quiet_success_teaches_nothing(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", RAIL))
    ledger.record_started("job-1")
    ledger.record_ended("job-1", 9)
    ledger.record_answer("job-1", Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
    assert ledger.query("SELECT * FROM print_lessons") == []


# The reviewer's steps
# --------------------------------------------------------------------------


def test_a_reviewer_signs_and_a_rejection_says_why(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", RAIL, _brim()))
    with pytest.raises(Refused, match="says who"):
        lessons.accept(ledger, 1, "operator")
    with pytest.raises(Refused, match="says why"):
        lessons.reject(ledger, 1, REVIEWER, "")
    lessons.reject(ledger, 1, REVIEWER, "a one-off")
    assert lessons.one(ledger, 1)["status"] == "rejected"
    steps = ledger.query("SELECT status, by, why FROM print_lesson_steps ORDER BY id")
    assert [s["status"] for s in steps] == ["candidate", "rejected"]
    assert steps[-1]["by"] == REVIEWER


def test_a_lesson_keyed_to_no_trait_is_keyed_before_its_accepted(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", CUBE, _brim()))
    with pytest.raises(Refused, match="every part"):
        lessons.accept(ledger, 1, REVIEWER)
    with pytest.raises(Refused, match="a trait is one of"):
        lessons.edit(ledger, 1, REVIEWER, keyed=["long"])
    lessons.edit(ledger, 1, REVIEWER, keyed=["long_and_thin"], text="brim long parts",
                 why="the cube's correction was about the rail beside it")
    found = lessons.one(ledger, 1)
    assert (found["status"], found["traits"], found["text"]) == (
        "edited", ["long_and_thin"], "brim long parts"
    )
    [step] = ledger.query("SELECT was FROM print_lesson_steps WHERE status='edited'")
    assert json.loads(step["was"])["text"] == "the ends of a long part lifted", "kept"


def test_only_a_kept_lesson_for_the_parts_traits_is_given(ledger: Ledger) -> None:
    ledger.record_start_requested(_record("job-1", STRIP, _brim()))
    lessons.accept(ledger, 1, REVIEWER)
    assert lessons.for_part(ledger, RAIL) == (), "the rail hasn't thin walls"
    [given] = lessons.for_part(ledger, STRIP)
    assert (given.id, given.key, given.value, given.reviewed_by) == (
        1, "brim_type", "outer_only", "operator"
    )
    lessons.edit(ledger, 1, REVIEWER, keyed=["long_and_thin"])
    assert [g.id for g in lessons.for_part(ledger, RAIL)] == [1]
    lessons.promote(ledger, 1, REVIEWER, "skill", "mqucifer/print-shop#999")
    assert lessons.for_part(ledger, RAIL) == (), "in the skill, it's read there"


# Giving: the lesson sets its setting, as an answer does
# --------------------------------------------------------------------------


def _facts(intake: Intake | None = None, **process: str) -> PartFacts:
    return PartFacts(intake=intake or Intake(), features=STRIP, machine={},
                     process={"brim_type": "no_brim", "enable_support": "0", **process},
                     filament={})


def _given(id: int, key: str, value: str, *keyed: str) -> GivenLesson:
    return GivenLesson(id=id, text=f"lesson {id}", key=key, value=value, job_id="job-0",
                       stl_name="rail.stl", traits=keyed or ("long_and_thin",),
                       reviewed_by="operator")


def test_a_lesson_sets_its_setting_and_the_closer_one_wins() -> None:
    changes, kept, marked = from_lessons(_facts(), (
        _given(1, "brim_type", "outer_only"),
        _given(2, "brim_type", "outer_and_inner", "long_and_thin", "thin_walls"),
    ))
    assert [(c.value, c.source, c.lesson) for c in changes] == [
        ("outer_and_inner", Source.LESSON, 2)
    ]
    assert kept == []
    assert [(g.id, g.applied, g.why) for g in marked] == [
        (1, False, "lesson 2, keyed closer, set it"), (2, True, "")
    ]


def test_an_intake_answer_comes_before_a_lesson() -> None:
    option = Option(label="decorative", settings={"brim_type": "no_brim"}, impact="")
    intake = Intake(questions=(Question(feature="purpose", text="for?", options=(option,)),),
                    answers=(IntakeAnswer(choice=0),))
    changes, _, marked = from_lessons(_facts(intake), (_given(1, "brim_type", "outer_only"),))
    assert changes == []
    assert marked[0].why == "your intake answer set it"


def test_a_lesson_that_keeps_the_preset_is_a_decision_the_guard_leaves() -> None:
    """The Sponsor typed enable_support=0 on every overhung part: as a lesson, it holds."""
    facts = _facts()
    facts = PartFacts(**{**facts.__dict__, "features": _features(overhang=394.0)})
    _, kept, _ = from_lessons(facts, (_given(1, "enable_support", "0", "overhangs"),))
    from print_shop.setup.proposal import Proposal  # noqa: PLC0415

    guarded = guard(facts, Proposal(kept=tuple(kept)))
    assert guarded.get("enable_support") is None, "the guard didn't turn supports on"
    assert [(c.key, c.lesson) for c in guarded.kept] == [("enable_support", 1)]


# Old rows read back
# --------------------------------------------------------------------------


def test_a_ledger_from_before_reads_back_and_its_rows_can_teach(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with Ledger(path) as made:
        made.record_start_requested(_record("job-1", RAIL, _brim()))
    db = sqlite3.connect(path)
    for table in ("print_lesson_scores", "job_lessons", "print_lesson_evidence",
                  "print_lesson_steps", "print_lessons"):
        db.execute(f"DROP TABLE {table}")
    db.execute("ALTER TABLE prints DROP COLUMN features")  # as a version-3 file is
    db.commit()
    db.close()
    with Ledger(path) as ledger:
        row = ledger.get("job-1")
        assert row is not None and row.features is None
        assert [c.key for c in row.corrections or ()] == ["brim_type"]
        lessons.after_print(ledger, "job-1")
        [found] = ledger.query("SELECT * FROM print_lessons")
        assert json.loads(found["traits"]) == [], "no features kept: not keyed, not guessed"
        assert "NOT KEYED" in lessons.listing(ledger)
    with Ledger(path) as again:
        assert len(again.query("SELECT * FROM print_lessons")) == 1, "a second open adds nothing"


# Through the daemon: job 1's correction reaches job 2's proposal by itself
# --------------------------------------------------------------------------


def _bar(name: str, length: float, width: float, directory: Path) -> Path:
    bar = trimesh.creation.box(extents=(length, width, width))
    bar.apply_translation((length / 2, width / 2, width / 2))
    path = directory / f"{name}.stl"
    bar.export(path)
    return path


class Printers(FramePrinter):
    """A printer that's idle again once a print is seen started, for the next job."""

    def idle(self) -> None:
        self._started = None


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[tuple[Daemon, Sponsor, Ledger, Path]]:
    """A daemon with the fake model, whose slicer replays the cube's slice for any part."""
    (tmp_path / "work").mkdir()
    process = json.loads(CI_PROFILES.process.read_text())
    (tmp_path / "process.json").write_text(
        json.dumps({**process, "wall_loops": "2", "brim_type": "no_brim"})
    )
    profiles = CI_PROFILES.model_copy(update={"process": tmp_path / "process.json"})
    cube = write_stl("cube", tmp_path)
    replay = RecordedSlicer()
    sponsor = Sponsor()
    ledger = Ledger(tmp_path / "ledger.sqlite")
    bridge = MemoryBridge()
    daemon = Daemon(
        cfg=CFG, profiles=profiles, workdir=tmp_path / "work", printer=(printer := Printers()),
        slicer=lambda stl, p, out: replay(cube, p, out), bridge=lambda cfg: bridge,
        ledger=ledger,
        proposer=Proposer(ask=_model, ask_questions=_questions,
                          lessons=lambda features: lessons.for_part(ledger, features)),
        asker=replay_asker(ANSWERS), reviewer=sponsor,
    )
    daemon.press = lambda d: bridge.deliver(command_topic(CFG), {"decision": d})  # type: ignore[attr-defined]
    daemon.idle = printer.idle  # type: ignore[attr-defined]
    with daemon:
        yield daemon, sponsor, ledger, tmp_path


def _print(daemon: Daemon, stl: Path) -> str:
    job_id = daemon.submit(stl)
    daemon.wait_for_gate(job_id, timeout_s=60)
    daemon.press("approve")  # type: ignore[attr-defined]
    assert daemon.wait_for_finish(job_id, timeout_s=60).printed
    daemon.idle()  # type: ignore[attr-defined]
    return job_id


def test_a_correction_on_one_print_reaches_a_later_similar_parts_proposal(
    shop: tuple[Daemon, Sponsor, Ledger, Path],
) -> None:
    daemon, sponsor, ledger, folder = shop

    # Job 1: the Sponsor corrects the brim at the review.
    sponsor.answers = ["brim_type=outer_only because the ends of a long part lift", "ok"]
    first = _print(daemon, _bar("rail", 150, 20, folder))
    row = ledger.get(first)
    assert row is not None and row.features is not None
    assert "long_and_thin" in traits(row.features)
    [candidate] = ledger.query("SELECT * FROM print_lessons")
    assert candidate["status"] == "candidate"

    # The reviewer's step: the one thing between the correction and the next print.
    lessons.accept(ledger, candidate["id"], REVIEWER)

    # Job 2: a different long thin part. Nobody types the brim.
    sponsor.answers = ["ok"]
    second = _print(daemon, _bar("strip", 120, 15, folder))
    proposal = sponsor.shown[-1].proposal
    brim = proposal.get("brim_type")
    assert brim is not None
    assert (brim.value, brim.source, brim.lesson) == ("outer_only", Source.LESSON, 1)
    shown = render(sponsor.shown[-1])
    assert re.search(r"brim_type +no_brim -> outer_only", shown)
    assert "[lesson 1: long_and_thin]" in shown, "the review says which lesson set it"
    assert "Lesson 1, from rail.stl" in shown

    # The ledger records it was given, and the review scored it: it stood.
    assert [(g["lesson"], g["applied"]) for g in
            ledger.query("SELECT * FROM job_lessons WHERE job_id=?", second)] == [(1, 1)]
    [scored] = ledger.query("SELECT * FROM print_lesson_scores")
    assert (scored["job_id"], scored["score"]) == (second, "used")
    assert lessons.one(ledger, 1)["status"] == "held"
    later = ledger.get(second)
    assert later is not None and "lesson         1, from rail.stl" in describe(later)
    assert lessons.to_promote(ledger) == []

    # Job 3: used again without a correction, it's listed to promote to the skill.
    sponsor.answers = ["ok"]
    _print(daemon, _bar("rail-2", 160, 18, folder))
    [(promoting, parts)] = lessons.to_promote(ledger)
    assert promoting["id"] == 1 and len(parts) == 2
    assert "## To promote" in lessons.listing(ledger)

    # A cube isn't long and thin: it's given nothing.
    sponsor.answers = ["ok"]
    _print(daemon, write_stl("cube", folder))
    assert sponsor.shown[-1].proposal.lessons == ()


def test_a_lesson_the_sponsor_corrects_got_in_the_way(
    shop: tuple[Daemon, Sponsor, Ledger, Path],
) -> None:
    daemon, sponsor, ledger, folder = shop
    sponsor.answers = ["brim_type=outer_only because the ends lift", "ok"]
    _print(daemon, _bar("rail", 150, 20, folder))
    lessons.accept(ledger, 1, REVIEWER)

    sponsor.answers = ["brim_type=no_brim because this one is wide enough", "ok"]
    second = _print(daemon, _bar("strip", 120, 15, folder))
    [scored] = ledger.query("SELECT * FROM print_lesson_scores")
    assert (scored["score"], scored["why"]) == (
        "in_the_way", "corrected at the review to no_brim: this one is wide enough"
    )
    assert lessons.one(ledger, 1)["status"] == "broken"
    assert [(row["id"], why) for row, why in lessons.to_retire(ledger)] == [
        (1, f"got in the way on {second}")
    ]
    assert ledger.query("SELECT setting, value FROM print_lessons WHERE id=2") == [
        {"setting": "brim_type", "value": "no_brim"}
    ], "and the correction is a candidate of its own"


def test_the_reviewers_tool_works_on_a_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        "print_lessons", Path(__file__).parents[1] / "scripts" / "print_lessons.py"
    )
    assert spec is not None and spec.loader is not None
    tool: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path = tmp_path / "copy.sqlite"
    with Ledger(path) as ledger:
        ledger.record_start_requested(_record("job-1", RAIL, _brim()))
    assert tool.main(["accept", "1", "--ledger", str(path), "--by", "operator"]) == 2
    assert "says who" in capsys.readouterr().err
    assert tool.main(["accept", "1", "--ledger", str(path), "--by", REVIEWER]) == 0
    assert "lesson 1 is accepted" in capsys.readouterr().out
    assert tool.main(["job-1", "--ledger", str(path)]) == 0
    out = capsys.readouterr().out
    assert "brim_type=outer_only, for a part that's long_and_thin" in out
    assert tool.main(["traits", "--ledger", str(path)]) == 0
    assert "long_and_thin" in capsys.readouterr().out
