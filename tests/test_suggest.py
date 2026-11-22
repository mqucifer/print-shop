"""The model's settings as proposals, worked out beside the review (mqucifer/print-shop#242).

The parts under the review: the thread that works them out, what it comes to, and the numbers
they're taken by. Every proposal is made up, and no model is called.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from print_shop.expert.client import CallInfo, CutOff, Stopped, TimedOut
from print_shop.ingest.analyzer import analyze
from print_shop.ingest.orientation import candidates
from print_shop.setup import Proposer
from print_shop.setup.features import measure
from print_shop.setup.intake import Intake
from print_shop.setup.loop import Part, PrintLoop, run
from print_shop.setup.proposal import ORIENTATION, Change, Ending, Proposal, Source, Suggested
from print_shop.setup.skill import GROUPS, load
from print_shop.setup.suggest import Suggesting, by_number, didnt_come, listed, suggested
from tests.fakes import STORE_PROFILES
from tests.fixtures import write_stl
from tests.test_print_loop import Model
from tests.test_send_back import AnyPart

WAIT_S = 10.0
LAID = (0.0, 0.0, 0.0)


def change(key: str, value: str, source: Source = Source.MODEL, **more: Any) -> Change:
    return Change(key=key, preset="2", value=value, reason="made up", source=source, **more)


def came(*proposed: Change) -> Suggested:
    return Suggested(pose=LAID, ending=Ending.CAME, proposed=proposed)


def test_a_call_that_never_answers_is_ended_and_a_late_answer_changes_nothing() -> None:
    release, returned = threading.Event(), threading.Event()

    def late(stop: threading.Event) -> Suggested:
        release.wait(WAIT_S)
        returned.set()
        return came(change("wall_loops", "4"))

    work = Suggesting(late, LAID)
    assert work.now().ending is Ending.WORKING and not work.wait(0.0)
    heard: list[Suggested] = []
    work.hear(heard.append)
    ended = work.stop(Ending.WENT_ON)
    release.set()
    assert returned.wait(WAIT_S)
    time.sleep(0.05)
    assert ended.ending is Ending.WENT_ON and work.now() == ended
    assert work.now().proposed == () and heard == []


def test_stopping_ends_the_call_under_way() -> None:
    stopped = threading.Event()

    def call(stop: threading.Event) -> Suggested:
        stop.wait(WAIT_S)
        stopped.set()
        raise Stopped("stopped")

    work = Suggesting(call, LAID)
    work.stop(Ending.TURNED)
    assert stopped.wait(WAIT_S), "the event it was given is set"
    assert work.now().ending is Ending.TURNED


def test_who_has_the_prompt_is_told_as_it_ends() -> None:
    release = threading.Event()

    def find(stop: threading.Event) -> Suggested:
        release.wait(WAIT_S)
        return came(change("wall_loops", "4"))

    work = Suggesting(find, LAID)
    told = threading.Event()
    work.hear(lambda found: told.set())
    release.set()
    assert told.wait(WAIT_S) and work.now().ending is Ending.CAME


def test_an_error_nobody_expected_is_said_as_the_model_being_unavailable() -> None:
    def broken(stop: threading.Event) -> Suggested:
        raise ValueError("a made-up bug")

    work = Suggesting(broken, LAID)
    assert work.wait(WAIT_S)
    assert work.now().ending is Ending.UNAVAILABLE and "a made-up bug" in didnt_come(work.now())


def _cut() -> CutOff:
    return CutOff("cut off as its answer ran on as whitespace", CallInfo(
        model="fake", finish_reason="runaway", prompt_tokens=0, completion_tokens=0,
        reasoning_tokens=0, max_tokens=16000, seconds=361.0))


@pytest.mark.parametrize(("raised", "ending", "says"), [
    (_cut(), Ending.CUT_OFF, "cut off after"),
    (TimedOut(800.0, 800.0), Ending.TIMED_OUT, "timed out after"),
    (RuntimeError("the proxy is down"), Ending.UNAVAILABLE, "unavailable after"),
])
def test_each_failure_is_what_it_is_and_leaves_the_settings_without_the_model(
    raised: Exception, ending: Ending, says: str, tmp_path: Path
) -> None:
    stl = write_stl("snap", tmp_path)
    features = measure(stl, analyze(stl).bed_contact_area_mm2)

    def fails(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise raised

    proposer = Proposer(ask=fails)
    made = proposer(Intake(), features, STORE_PROFILES)
    found = suggested(made, LAID)
    assert found.ending is ending and found.proposed == ()
    said = didnt_come(found)
    assert says in said and said.endswith("These are the settings without it.")
    base = proposer.without_model(Intake(), features, STORE_PROFILES)
    assert base.changes == made.changes, "the settings of a failed call today"


def test_what_the_model_would_keep_at_the_preset_is_proposed_too() -> None:
    made = Proposal(model="fake",
                    changes=(change("wall_loops", "4"),
                             change("enable_support", "1", Source.GUARD)),
                    kept=(change("sparse_infill_density", "2"),))
    assert [c.key for c in suggested(made, LAID).proposed] == [
        "wall_loops", "sparse_infill_density"]


def test_numbers_run_on_from_the_slices() -> None:
    rows = listed(came(change("wall_loops", "4"), change("layer_height", "0.16")),
                  Proposal(), "0,0,0", after=2)
    assert [row.number for row in rows] == [3, 4]
    assert [c.key for c in by_number(rows).values()] == ["wall_loops", "layer_height"]


@pytest.mark.parametrize(("stands", "by"), [
    (change("wall_loops", "3", Source.SPONSOR), "you"),
    (change("wall_loops", "3", Source.READING), "you"),
    (change("wall_loops", "3", taken=True), "you"),
    (change("wall_loops", "3", Source.ANSWER), "your answer"),
    (change("wall_loops", "3", Source.LESSON, lesson=7), "lesson 7"),
])
def test_one_never_replaces_whats_the_persons_and_says_what_stands(
    stands: Change, by: str
) -> None:
    [row] = listed(came(change("wall_loops", "4")), Proposal(changes=(stands,)), "0,0,0", 0)
    assert (row.standing, row.now, row.by, row.number) == ("theirs", "3", by, 0)
    assert by_number([row]) == {}


def test_a_guards_setting_can_be_changed_by_taking_the_models() -> None:
    guarded = Proposal(changes=(change("enable_support", "1", Source.GUARD),))
    [row] = listed(came(change("enable_support", "0")), guarded, "0,0,0", 0)
    assert row.standing == "open"


def test_one_thats_so_already_or_turned_down_has_no_number() -> None:
    found = came(change("wall_loops", "2"), change("layer_height", "0.16"))
    [same] = listed(found.model_copy(update={"declined": ("layer_height",)}),
                    Proposal(), "0,0,0", 0)
    assert (same.standing, same.change.key) == ("so", "wall_loops")


def _part(tmp_path: Path) -> Part:
    stl = write_stl("tee", tmp_path)
    proposer = Proposer()
    facts = proposer.facts(Intake(), measure(stl, analyze(stl).bed_contact_area_mm2),
                           STORE_PROFILES)
    return Part(stl=stl, facts=facts, candidates=tuple(candidates(stl)),
                build_volume_mm=(256.0, 256.0, 256.0), skill=load(GROUPS),
                profiles=STORE_PROFILES, slicer=AnyPart(), workdir=tmp_path / "loop",
                proposes=True)


def test_beside_a_review_the_loop_asks_nobody_and_its_question_is_the_turns_words(
    tmp_path: Path,
) -> None:
    model = Model(
        [("ask", {"question": "Turn it over?", "reason": "no supports that way",
                  "rotation": [180, 0, 0]})],
        [("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "slab down"})],
    )

    def nobody(question: str) -> str:
        raise AssertionError("nobody is asked beside a review")

    found = run(PrintLoop(chat=model, person=nobody), _part(tmp_path), threading.Event())
    assert found.proposed and found.pose is not None
    assert found.pose.rotation_deg == (180.0, 0.0, 0.0)
    assert found.corrections == ()
    words = found.big[ORIENTATION]
    assert words.startswith('"Turn it over? (because no supports that way)"')
    assert "It's a big change: turn it to 180,0,0 from the file's own pose." in words
    assert "big" not in found.reply


def test_a_big_change_it_didnt_ask_of_is_still_said_as_one(tmp_path: Path) -> None:
    model = Model([("propose", {"rotation": [180, 0, 0], "settings": [], "reasoning": "x"})])
    found = run(PrintLoop(chat=model, person=lambda q: "no"), _part(tmp_path))
    assert found.proposed, "nothing is applied by proposing, so it needn't ask first"
    assert found.big[ORIENTATION].startswith("It's a big change:")


def test_a_stopped_loop_makes_no_more_calls(tmp_path: Path) -> None:
    stop = threading.Event()
    stop.set()
    model = Model()
    found = run(PrintLoop(chat=model, person=lambda q: "no"), _part(tmp_path), stop)
    assert found.ended == "stopped" and model.shown == []
