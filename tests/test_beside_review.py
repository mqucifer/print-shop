"""The review doesn't wait for the model's settings (mqucifer/print-shop#242).

Each of the issue's Done when, through the daemon, the graph, the gate and a made-up ledger, and
the terminal's review by itself. Every model is a scripted fake, every slice the cube's
recording: nothing reaches LiteLLM, Orca, the printer or the broker. Every wait is bounded, so
that a regression fails and doesn't hang.
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.core.contracts import Gate
from print_shop.desk.again import again
from print_shop.expert.client import CallInfo, CutOff, LLMConfig, Stopped, TimedOut
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup import lessons as print_lessons
from print_shop.setup.intake import Answer
from print_shop.setup.loop import PrintLoop
from print_shop.setup.proposal import ORIENTATION, Change, Ending, Proposal, Source, Suggested
from print_shop.setup.reading import Flag
from print_shop.setup.review import Review, Reviewer, ReviewRequest, cli_reviewer, parse_answer
from print_shop.setup.review import render as render_review
from print_shop.setup.suggest import Suggesting
from tests.fakes import STORE_PROFILES, FramePrinter, MemoryBridge
from tests.fixtures import write_stl
from tests.reviewing import flat, settled
from tests.test_job_e2e import CFG
from tests.test_print_loop import Model
from tests.test_review import _request
from tests.test_send_back import AnyPart

WAIT_S = 20.0
LAID = (0.0, 0.0, 0.0)
FLIPPED = (180.0, 0.0, 0.0)
ON_ITS_SIDE = (90.0, 0.0, 0.0)
WALLS = {"key": "wall_loops", "value": "4", "feature": "section", "knowledge": "walls-shells",
         "reason": "the made-up tab carries the load"}


def proposing(*changes: dict[str, str]) -> Callable[..., dict[str, Any]]:
    def ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"reasoning": "made up", "changes": list(changes), "design_notes": []}

    return ask


class Held:
    """A model that answers only when it's let go, or when its call is ended."""

    def __init__(self, *changes: dict[str, str]) -> None:
        self.answer = proposing(*changes)
        self.began = threading.Event()
        self.go = threading.Event()
        self.closed = threading.Event()
        self.calls = 0
        self.stops: list[threading.Event] = []

    def streamed(self, config: LLMConfig, stop: threading.Event | None = None) -> Any:
        """In `llm_ask`'s place: a call that `stop` ends, as the streamed one is."""

        def ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            assert stop is not None, "a review's call can always be ended"
            self.calls += 1
            self.stops.append(stop)
            self.began.set()
            until = time.monotonic() + WAIT_S
            while not self.go.is_set() and time.monotonic() < until:
                if stop.wait(0.01):
                    self.closed.set()
                    raise Stopped("the caller ended it")
            return self.answer(system, user, schema)

        return ask


class Script:
    """A reviewer that types from a script, and keeps each screen it was shown."""

    def __init__(self, *steps: str) -> None:
        self.steps = list(steps)
        self.seen: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> Review:
        self.seen.append(request)
        assert self.steps, "reviewed more often than was scripted"
        parsed = parse_answer(self.steps.pop(0), request)
        assert isinstance(parsed, Review), parsed
        return parsed


class Shop:
    """One job at a time through the gate, on fakes."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.ledger = Ledger(tmp_path / "ledger.sqlite")
        self.submitted = 0.0
        self.error = ""
        self.gate: dict[str, Any] = {}

    def run(
        self, stl: Path, reviewer: Reviewer | None, proposer: Proposer | None = None,
        loop: PrintLoop | None = None, llm: LLMConfig | None = None, **submit: Any,
    ) -> Row | None:
        (self.tmp_path / "work").mkdir(exist_ok=True)
        bridge = MemoryBridge()
        daemon = Daemon(
            cfg=CFG, profiles=STORE_PROFILES, workdir=self.tmp_path / "work",
            printer=FramePrinter(), slicer=AnyPart(), bridge=lambda cfg: bridge,
            ledger=self.ledger, proposer=proposer or Proposer(),
            asker=lambda questions: tuple(Answer(choice=0) for _ in questions),
            reviewer=reviewer, loop=loop, llm=llm,
        )
        with daemon:
            self.submitted = time.monotonic()
            job_id = daemon.submit(stl, **submit)
            daemon.wait_for_gate(job_id, timeout_s=WAIT_S)
            if (gate := daemon.pending_gate()) is not None:
                self.gate = gate
                bridge.press(CFG, "approve")
            self.error = daemon.wait_for_finish(job_id, timeout_s=WAIT_S).error or ""
        return self.ledger.get(job_id)


@pytest.fixture
def shop(tmp_path: Path) -> Iterator[Shop]:
    made = Shop(tmp_path)
    yield made
    made.ledger.close()


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> Iterator[Held]:
    model = Held(WALLS)
    monkeypatch.setattr("print_shop.setup.llm_ask", model.streamed)
    yield model
    model.go.set()


def by_llm() -> Proposer:
    """A proposer whose settings call is the configured model's, here `Held`."""
    return Proposer(llm=LLMConfig(api_key=SecretStr("not-a-key"), model="fake"),
                    ask_questions=lambda *_: {"questions": []})


def values(proposal: Proposal) -> set[tuple[str, str, Source]]:
    return {(c.key, c.value, c.source) for c in proposal.changes}


# A model that never answers
# --------------------------------------------------------------------------


def test_with_a_model_that_never_answers_the_review_shows_in_seconds_and_the_job_gets_to_ready(
    shop: Shop, held: Held, tmp_path: Path
) -> None:
    shown: list[str] = []
    shown_after: list[float] = []

    def read(prompt: str) -> str:
        if not shown_after:
            shown_after.append(time.monotonic() - shop.submitted)
        return "ok"

    row = shop.run(write_stl("cube", tmp_path),
                   cli_reviewer(read, shown.append, forget=lambda: None, screen=lambda: None),
                   by_llm())
    assert row is not None and row.proposal is not None, shop.error
    assert not held.go.is_set(), "it never answered"
    assert shown_after[0] < 10, "the review shows within seconds of the first slice"
    said = "\n".join(shown)
    assert "The model is still working on settings (" in said
    assert ("What it proposes is added under Settings when you press Enter, and nothing waits "
            "for it.") in flat(said)
    assert said.count("ok again to go on without its proposals, which closes its call") == 1
    assert held.closed.wait(WAIT_S), "going on closed its call"
    [found] = row.proposal.suggested
    assert (found.ending, found.proposed, found.taken) == (Ending.WENT_ON, (), ())
    went = "Went on without the model's settings: still working after"
    assert any(n.startswith(went) for n in row.proposal.notes)
    assert said.count(went) == 1, "shown as it's done"
    assert shop.gate["without"].startswith(went), "and at the gate"
    assert shop.gate["without"].endswith("its call was closed")
    assert "model proposed nothing: went on after" in describe(row)


@pytest.mark.parametrize("part", ["cube", "tee", "lid", "snap"])
def test_with_the_model_absent_the_settings_are_a_failed_calls(
    shop: Shop, held: Held, tmp_path: Path, part: str
) -> None:
    """No guard, bound, lesson or review step is lost."""
    stl = write_stl(part, tmp_path)
    screen = Script("ok")
    waited_for_none = shop.run(stl, screen, by_llm(), as_laid=True)
    assert waited_for_none is not None and waited_for_none.proposal is not None, shop.error

    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("LiteLLM is down")

    # With no reviewer there's nothing to propose to, so that path is a failed call as it was.
    failed = shop.run(stl, None, Proposer(ask=down, ask_questions=lambda *_: {"questions": []}),
                      as_laid=True)
    assert failed is not None and failed.proposal is not None, shop.error
    assert values(waited_for_none.proposal) == values(failed.proposal)
    assert waited_for_none.proposal.lessons == failed.proposal.lessons
    assert values(screen.seen[0].proposal) == values(failed.proposal), "from the first screen"


def test_stopping_at_the_review_closes_the_call_too(
    shop: Shop, held: Held, tmp_path: Path
) -> None:
    def stops(request: ReviewRequest) -> Review:
        assert held.began.wait(WAIT_S)
        return Review(stop=True)

    assert shop.run(write_stl("cube", tmp_path), stops, by_llm()) is None
    assert held.closed.wait(WAIT_S)


# A reply that arrives
# --------------------------------------------------------------------------


def test_a_reply_that_arrives_is_listed_and_none_of_it_is_applied(
    shop: Shop, tmp_path: Path
) -> None:
    screen = Script("ok")
    row = shop.run(write_stl("cube", tmp_path), settled(screen),
                   Proposer(ask=proposing(WALLS)), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    shown = render_review(screen.seen[0])
    assert "PROPOSED BY THE MODEL (not applied unless you take it)" in shown
    assert ") wall_loops  2 -> 4" in shown
    assert "[model: section, walls-shells]" in shown
    assert row.proposal.get("wall_loops") is None
    [found] = row.proposal.suggested
    assert (found.ending, [c.key for c in found.proposed], found.taken) == (
        Ending.CAME, ["wall_loops"], ())
    assert not found.unseen, "it was on screen"


def test_one_that_came_before_ok_but_after_the_screen_is_recorded_as_unseen(
    shop: Shop, tmp_path: Path
) -> None:
    let_go = threading.Event()

    def ask(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        let_go.wait(WAIT_S)
        return proposing(WALLS)(system, user, schema)

    def review(request: ReviewRequest) -> Review:
        assert request.suggesting is not None
        let_go.set()
        assert request.suggesting.wait(WAIT_S)
        return Review(approved=True)

    row = shop.run(write_stl("cube", tmp_path), review, Proposer(ask=ask), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    [found] = row.proposal.suggested
    assert found.unseen and row.proposal.get("wall_loops") is None
    without = "Went on without the model's settings: its 1 proposal came and wasn't seen"
    assert without in row.proposal.notes and shop.gate["without"] == without


def test_one_taken_is_sliced_and_is_the_persons_taking_everywhere(
    shop: Shop, tmp_path: Path
) -> None:
    screen = Script("take 1", "ok")

    def review(request: ReviewRequest) -> Review:
        if len(screen.seen) == 0:
            [number] = [row.number for row in request.by_model]
            screen.steps[0] = f"take {number}"
        return screen(request)

    stl = write_stl("cube", tmp_path)
    row = shop.run(stl, settled(review), Proposer(ask=proposing(WALLS)), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    taken = row.proposal.get("wall_loops")
    assert taken is not None
    assert (taken.value, taken.source, taken.taken) == ("4", Source.MODEL, True)
    assert not row.corrections, "taking isn't a correction"
    assert row.proposal.suggested[0].taken == ("wall_loops",)
    assert re.search(r"\n  wall_loops +2 +2 +4 +model, taken by you\n",
                     render_review(screen.seen[1]))
    assert "(model, taken by you)" in describe(row)
    assert "Taken from the model's proposals" in "\n".join(again(row, stl).notes)
    assert "wall_loops=4 (taken)" in describe(row)
    assert "  -  wall_loops 4: taken." in render_review(screen.seen[1])

    # The next print of it is offered it as the person's own (mqucifer/print-shop#211).
    second = Script("ok")
    assert shop.run(stl, second, as_laid=True) is not None, shop.error
    offered = render_review(second.seen[0])
    assert "YOUR OVERRIDES" in offered and "you took the model's" in offered


def test_one_turned_down_saying_why_is_a_correction_and_a_lesson_candidate(
    shop: Shop, tmp_path: Path
) -> None:
    screen = Script("", "ok")

    def review(request: ReviewRequest) -> Review:
        if not screen.seen:
            [number] = [row.number for row in request.by_model]
            screen.steps[0] = f"no {number} because two walls are plenty here"
        return screen(request)

    row = shop.run(write_stl("cube", tmp_path), settled(review),
                   Proposer(ask=proposing(WALLS)), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    [correction] = row.corrections or ()
    assert (correction.key, correction.value, correction.source, correction.reason) == (
        "wall_loops", "2", Source.SPONSOR, "two walls are plenty here")
    assert row.proposal.suggested[0].declined == ("wall_loops",)
    assert "two walls are plenty here [you, saying no to the model's 4]" in (
        render_review(screen.seen[1]))
    assert ") wall_loops" not in render_review(screen.seen[1]), "it isn't asked again"
    print_lessons.draw(shop.ledger, row)
    [lesson] = shop.ledger.query("SELECT * FROM print_lessons WHERE setting='wall_loops'")
    assert lesson["text"] == "two walls are plenty here"


class Supporting(Proposer):
    """Proposes supports on, whatever the part's skill groups would let a model say."""

    def __call__(self, *args: Any, **kwargs: Any) -> Proposal:
        made = super().__call__(*args, **kwargs)
        if not self.suggests:
            return made
        return made.model_copy(update={"changes": (*made.changes, Change(
            key="enable_support", preset="0", value="1", reason="made up", source=Source.MODEL))})


def test_supports_taken_of_the_models_are_still_tree(shop: Shop, tmp_path: Path) -> None:
    """Taken over a preset whose supports aren't tree, with none set at the first slice, so
    the guard had nothing to hold then."""
    seen: list[ReviewRequest] = []

    def review(request: ReviewRequest) -> Review:
        seen.append(request)
        if len(seen) > 1:
            return Review(approved=True)
        [number] = [r.number for r in request.by_model if r.change.key == "enable_support"]
        parsed = parse_answer(f"take {number}", request)
        assert isinstance(parsed, Review), parsed
        return parsed

    row = shop.run(write_stl("cube", tmp_path), settled(review), Supporting(ask=proposing()),
                   as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    assert json.loads(STORE_PROFILES.process.read_text())["support_type"] != "tree(auto)"
    assert values(row.proposal) >= {("enable_support", "1", Source.MODEL),
                                    ("support_type", "tree(auto)", Source.GUARD)}


# Going on, a cut-off and a timeout, each as what it is
# --------------------------------------------------------------------------


def _cut() -> CutOff:
    return CutOff("its answer ran on as whitespace", CallInfo(
        model="fake", finish_reason="runaway", prompt_tokens=0, completion_tokens=0,
        reasoning_tokens=0, max_tokens=16000, seconds=361.0))


@pytest.mark.parametrize(("raised", "ending", "says"), [
    (_cut(), Ending.CUT_OFF, "cut off after"),
    (TimedOut(800.0, 800.0), Ending.TIMED_OUT, "timed out after"),
    (RuntimeError("LiteLLM is down"), Ending.UNAVAILABLE, "unavailable after"),
])
def test_each_failure_is_shown_and_recorded_as_what_it_is(
    shop: Shop, tmp_path: Path, raised: Exception, ending: Ending, says: str
) -> None:
    def fails(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise raised

    screen = Script("ok")
    row = shop.run(write_stl("cube", tmp_path), settled(screen), Proposer(ask=fails),
                   as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    said = render_review(screen.seen[0])
    assert f"The model's settings didn't come: {says}" in said
    assert "These are the settings without it." in flat(said)
    assert "still working" not in said, "it takes the waiting line's place"
    [found] = row.proposal.suggested
    assert found.ending is ending
    assert row.proposal.model.startswith(ending.value)
    assert any(says in n for n in row.proposal.notes)


# As-laid, context-laid and loop-posed
# --------------------------------------------------------------------------


def turning(*settings: dict[str, str]) -> Model:
    return Model([("propose", {"rotation": [180, 0, 0], "settings": list(settings),
                               "reasoning": "slab down"})])


def nobody(question: str) -> str:
    raise AssertionError("nobody is asked beside a review")


def test_as_laid_the_loop_isnt_run_and_the_settings_are_proposed_for_that_pose(
    shop: Shop, tmp_path: Path
) -> None:
    model = turning()
    screen = Script("ok")
    row = shop.run(write_stl("tee", tmp_path), settled(screen), Proposer(ask=proposing(WALLS)),
                   PrintLoop(chat=model, person=nobody, model="fake"), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    assert model.shown == []
    assert (row.rotation_deg, row.orientation_source) == (LAID, "as_laid")
    assert [found.pose for found in row.proposal.suggested] == [LAID]
    assert [r.change.key for r in screen.seen[0].by_model] == ["wall_loops"]


def test_context_laid_the_review_shows_that_side_without_waiting_for_the_loop(
    shop: Shop, tmp_path: Path
) -> None:
    release = threading.Event()

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        release.wait(WAIT_S)
        raise RuntimeError("no model here")

    stl = write_stl("tee", tmp_path)
    (tmp_path / "tee.part.json").write_text(json.dumps({"down": "+z"}))
    screen = Script("ok")
    try:
        row = shop.run(stl, screen, loop=PrintLoop(chat=chat, person=nobody, model="fake"))
        assert not release.is_set(), "the loop never answered"
    finally:
        release.set()
    assert row is not None and row.proposal is not None, shop.error
    assert screen.seen[0].orientation.rotation_deg == FLIPPED
    assert "as the sidecar" in screen.seen[0].orientation.rationale
    assert (row.rotation_deg, row.orientation_source) == (FLIPPED, "context")
    assert row.proposal.suggested[0].ending is Ending.WENT_ON


def test_loop_posed_the_review_shows_the_files_pose_and_the_turn_is_a_proposal(
    shop: Shop, tmp_path: Path
) -> None:
    screen = Script("ok")
    row = shop.run(write_stl("tee", tmp_path), settled(screen),
                   loop=PrintLoop(chat=turning(WALLS), person=nobody, model="fake"))
    assert row is not None and row.proposal is not None, shop.error
    first = screen.seen[0]
    shown = render_review(first)
    assert first.orientation.rotation_deg == LAID
    assert "as the file lays it; the model can only propose a turn" in shown
    assert ("the check on poses says another pose has less to support: 0 mm2, where this has "
            "285.9 mm2") in flat(shown)
    assert "How the model read it" not in shown, "the reading is the turned pose's, until taken"
    assert row.proposal.reasoning == "", "and in the record it stays with that proposal"
    assert row.proposal.suggested[0].reasoning == "slab down"
    assert ") orientation  0,0,0 -> 180,0,0" in shown
    assert "It's a big change: turn it to 180,0,0 from the file's own pose." in shown
    assert "Once turned it would also propose: wall_loops 2 -> 4" in shown
    assert (row.rotation_deg, row.orientation_source) == (LAID, "as_filed"), "approving took none"
    assert row.proposal.get("wall_loops") is None


def test_a_turn_taken_brings_the_loops_settings_as_proposals_with_no_second_call(
    shop: Shop, held: Held, tmp_path: Path
) -> None:
    model = turning(WALLS)
    screen = Script("", "ok")

    def review(request: ReviewRequest) -> Review:
        if not screen.seen:
            [turn] = request.by_model
            screen.steps[0] = f"take {turn.number}"
        return screen(request)

    row = shop.run(write_stl("tee", tmp_path), settled(review), by_llm(),
                   PrintLoop(chat=model, person=nobody, model="fake"))
    assert row is not None and row.proposal is not None, shop.error
    assert (row.rotation_deg, row.orientation_source) == (FLIPPED, "loop:fake")
    assert "taken by you at the review" in row.orientation_rationale
    assert not row.corrections, "a turn taken is no correction, and no lesson"
    after = screen.seen[1]
    assert after.orientation.rotation_deg == FLIPPED
    assert ") wall_loops  2 -> 4" in render_review(after)
    assert "How the model read it\n    slab down" in render_review(after), "taken, it's this pose's"
    assert row.proposal.get("wall_loops") is None, "turned, they're still only proposed"
    assert (len(model.shown), held.calls) == (1, 0), "no second call"
    assert [found.pose for found in row.proposal.suggested] == [LAID, FLIPPED]


def test_a_turn_to_another_pose_ends_the_call_and_asks_again_for_that_pose(
    shop: Shop, held: Held, tmp_path: Path
) -> None:
    seen: list[ReviewRequest] = []

    def review(request: ReviewRequest) -> Review:
        seen.append(request)
        if len(seen) > 1:
            held.go.set()
            assert request.suggesting is not None and request.suggesting.wait(WAIT_S)
            return Review(approved=True)
        assert held.began.wait(WAIT_S)
        assert request.measuring is not None
        request.measuring.all_six()
        return Review(corrections=(Change(
            key=ORIENTATION, preset="0,0,0", value="90,0,0", reason="a made-up reason",
            source=Source.SPONSOR),))

    row = shop.run(write_stl("cube", tmp_path), review, by_llm(), as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    assert held.stops[0].is_set(), "the first pose's call was closed"
    assert held.calls == 2
    assert [(f.pose, f.ending) for f in row.proposal.suggested] == [
        (LAID, Ending.TURNED), (ON_ITS_SIDE, Ending.CAME)]


def test_a_turn_turned_down_is_a_correction_and_the_settings_are_asked_for_this_pose(
    shop: Shop, tmp_path: Path
) -> None:
    screen = Script("", "ok")
    asked: list[str] = []

    def settings(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        asked.append(user)
        return proposing(WALLS)(system, user, schema)

    def review(request: ReviewRequest) -> Review:
        if not screen.seen:
            [turn] = request.by_model
            screen.steps[0] = f"no {turn.number} because the slab's top is the face that shows"
        return screen(request)

    row = shop.run(write_stl("tee", tmp_path), settled(review), Proposer(ask=settings),
                   PrintLoop(chat=turning(), person=nobody, model="fake"))
    assert row is not None and row.proposal is not None, shop.error
    assert row.rotation_deg == LAID
    assert [(c.key, c.preset, c.value, c.reason) for c in row.corrections or ()] == [
        ("orientation", "180,0,0", "0,0,0", "the slab's top is the face that shows")]
    asked_for = (*row.proposal.changes, *row.proposal.not_applied)
    assert ORIENTATION not in {c.key for c in asked_for}, (
        "a pose is no setting of the slice, not even one that couldn't be placed")
    assert len(asked) == 1, "its settings were for the turn, so it's asked for this pose"
    assert [r.change.key for r in screen.seen[1].by_model] == ["wall_loops"]


def test_a_part_that_doesnt_fit_as_it_lies_is_shown_in_a_pose_that_does(
    shop: Shop, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()

    def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        release.wait(WAIT_S)
        raise RuntimeError("no model here")

    # The lid is 100 mm across: it fits only stood up on a plate this narrow.
    monkeypatch.setattr(graph, "build_volume_mm", lambda machine: (256.0, 50.0, 256.0))
    screen = Script("ok")
    try:
        row = shop.run(write_stl("lid", tmp_path), screen,
                       loop=PrintLoop(chat=chat, person=nobody, model="fake"))
        assert not release.is_set()
    finally:
        release.set()
    assert row is not None, shop.error
    assert row.rotation_deg != LAID and row.orientation_source == "geometric"
    assert screen.seen[0].orientation.rationale.startswith(
        "it doesn't fit the plate as the file lays it, so it's shown at ")
    assert "The model had said" not in screen.seen[0].orientation.rationale, "none was asked"


# No number moves under an open prompt
# --------------------------------------------------------------------------


def slices_one() -> tuple[Flag, ...]:
    return (Flag(id="ironing_hidden", reading="made up", change=Change(
        key="ironing_type", preset="top", value="no ironing", reason="the top isn't seen",
        source=Source.READING)),)


class Open:
    """The terminal's review, with a model that can be let go while the prompt is open."""

    def __init__(self, flags: tuple[Flag, ...] = ()) -> None:
        self.release = threading.Event()
        self.shown: list[str] = []
        self.forgotten = 0

        def find(stop: threading.Event) -> Suggested:
            self.release.wait(WAIT_S)
            return Suggested(pose=LAID, ending=Ending.CAME, proposed=(Change(
                key="top_shell_layers", preset="4", value="5", reason="made up",
                source=Source.MODEL),))

        self.work = Suggesting(find, LAID)
        base = _request(Proposal())
        self.request = ReviewRequest(**{
            **{name: getattr(base, name) for name in base.__dataclass_fields__},
            "flags": flags, "suggested": self.work.now(), "suggesting": self.work,
        })

    def arrive(self) -> None:
        self.release.set()
        assert self.work.wait(WAIT_S)
        until = time.monotonic() + WAIT_S
        while not any("came after" in text for text in self.shown):
            assert time.monotonic() < until, "its arrival was never said"
            time.sleep(0.005)

    def forget(self) -> None:
        self.forgotten += 1

    def review(self, read: Callable[[str], str]) -> Review:
        return cli_reviewer(read, self.shown.append, forget=self.forget,
                            screen=lambda: None)(self.request)

    @property
    def said(self) -> str:
        return "\n".join(self.shown)


def test_one_that_arrives_under_the_prompt_moves_no_number_until_enter_adds_it() -> None:
    term = Open(slices_one())
    typed = iter(["take 2", "", "take 2"])
    screens: list[int] = []

    def read(prompt: str) -> str:
        if not screens:
            term.arrive()
        screens.append(term.said.count("PROPOSED BY THE MODEL ("))
        return next(typed)

    found = term.review(read)
    assert "Enter adds it to the list (what you've typed is kept)." in term.said
    assert "take a number from 1 to 1" in term.said, "a 2 typed then names what was on screen"
    assert screens == [0, 0, 1], "only Enter drew a new list"
    assert term.said.count("SETTINGS, over your presets") == 1, "and only that block and the prompt"
    assert term.said.count("Orca's estimate") == 1
    assert term.forgotten == 2, "written, then forgotten, then read: each drawing"
    [taken] = found.taken
    assert (taken.key, taken.taken) == ("top_shell_layers", True)
    assert found.saw is not None and found.saw.ending is Ending.CAME


def test_bare_take_stays_the_slices_alone_once_the_models_are_listed() -> None:
    term = Open(slices_one())
    typed = iter(["", "take"])
    first = True

    def read(prompt: str) -> str:
        nonlocal first
        if first:
            first = False
            term.arrive()
        return next(typed)

    found = term.review(read)
    assert [(c.key, c.source) for c in found.taken] == [("ironing_type", Source.READING)]
    assert "take (the slice's) · take 2 (the model's) · no 2 because …" in flat(term.said)


def test_ok_before_theyve_been_shown_says_so_once() -> None:
    term = Open()
    first = True

    def read(prompt: str) -> str:
        nonlocal first
        if first:
            first = False
            term.arrive()
        return "ok"

    found = term.review(read)
    assert found.approved and not found.taken
    assert term.said.count("are in and you haven't seen") + term.said.count(
        "is in and you haven't seen") == 1


def test_an_ok_warned_while_it_worked_warns_again_once_theyre_in_unseen() -> None:
    term = Open()
    prompts = 0

    def read(prompt: str) -> str:
        nonlocal prompts
        prompts += 1
        if prompts == 2:
            term.arrive()
        return "ok"

    found = term.review(read)
    assert found.approved and prompts == 3
    assert term.said.count("ok again to go on without its proposals") == 1
    assert term.said.count("is in and you haven't seen it") == 1
    assert "Went on without the model's settings: its 1 proposal came and wasn't seen" in term.said


def test_a_failure_enter_adds_is_its_one_line_and_the_prompt() -> None:
    release = threading.Event()

    def find(stop: threading.Event) -> Suggested:
        release.wait(WAIT_S)
        return Suggested(pose=LAID, ending=Ending.TIMED_OUT, said="timed out: made up")

    work = Suggesting(find, LAID)
    base = _request(Proposal())
    request = ReviewRequest(**{
        **{name: getattr(base, name) for name in base.__dataclass_fields__},
        "suggested": work.now(), "suggesting": work,
    })
    shown: list[str] = []
    typed = iter(["", "ok"])

    def read(prompt: str) -> str:
        release.set()
        assert work.wait(WAIT_S)
        return next(typed)

    assert cli_reviewer(read, shown.append, forget=lambda: None,
                        screen=lambda: None)(request).approved
    added = shown[-1].strip().splitlines()
    assert added[0].startswith("The model's settings didn't come: timed out after")
    assert added[-1].startswith("Answers: ok") and len(added) == 3


def test_the_gate_on_the_terminal_says_it_went_on(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    without = "Went on without the model's settings: its 1 proposal came and wasn't seen"
    lines = module._gate_lines({"gate": Gate.BEFORE_PRINT.value, "without": without}, turned=True)
    assert f"  model       {without}" in lines


def test_ok_while_it_works_says_so_once_and_then_goes_on() -> None:
    term = Open()
    found = term.review(lambda prompt: "ok")
    term.release.set()
    assert found.approved
    assert term.said.count("ok again to go on without its proposals") == 1


def test_a_turn_is_taken_alone(tmp_path: Path) -> None:
    turn = Change(key=ORIENTATION, preset="0,0,0", value="180,0,0", reason="slab down",
                  source=Source.MODEL, knowledge="loop:fake")
    base = _request(Proposal())
    request = ReviewRequest(**{
        **{name: getattr(base, name) for name in base.__dataclass_fields__},
        "suggested": Suggested(pose=LAID, ending=Ending.CAME, proposed=(turn, Change(
            key="top_shell_layers", preset="4", value="5", reason="made up",
            source=Source.MODEL))),
    })
    assert parse_answer("take 1 2", request) == (
        "that turns the part, which slices it afresh: take 1 alone first, then the rest at the "
        "next review")
    assert parse_answer("no 2", request) == (
        "say why, it's what the next print learns from: no 2 because ...")


def test_a_turn_is_still_listed_where_the_settings_call_after_it_failed() -> None:
    """The loop fell back: the orientation call answered, and the settings call didn't."""
    turn = Change(key=ORIENTATION, preset="0,0,0", value="180,0,0", reason="no supports",
                  source=Source.MODEL, knowledge="geometric")
    base = _request(Proposal())
    request = ReviewRequest(**{
        **{name: getattr(base, name) for name in base.__dataclass_fields__},
        "suggested": Suggested(
            pose=LAID, ending=Ending.UNAVAILABLE, said="unavailable: down", proposed=(turn,),
            loop_ending="past its limit of 3 calls, after 3 calls in 1 s"),
    })
    said = render_review(request)
    assert "1) orientation  0,0,0 -> 180,0,0" in said
    assert "the print loop stopped, past its limit of 3 calls" in said
    assert "The model's settings didn't come: unavailable" in said


# What the cold review found (mqucifer/print-shop#242)
# --------------------------------------------------------------------------


def number(key: str) -> Callable[[ReviewRequest], str]:
    def of(request: ReviewRequest) -> str:
        [n] = [r.number for r in request.by_model if r.change.key == key]
        return str(n)

    return of


class Steps:
    """A reviewer typing from steps, each text or a function of the screen, or a Review."""

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.seen: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> Review:
        self.seen.append(request)
        assert self.steps, "reviewed more often than was scripted"
        step = self.steps.pop(0)
        answer = step(request) if callable(step) else step
        if isinstance(answer, Review):
            return answer
        parsed = parse_answer(answer, request)
        assert isinstance(parsed, Review), parsed
        return parsed


def unused(messages: Any, tools: Any) -> dict[str, Any]:
    raise AssertionError("the stoppable model is the one called")


def sent_to(pose: str) -> Callable[[ReviewRequest], Review]:
    def send(request: ReviewRequest) -> Review:
        assert request.measuring is not None
        request.measuring.all_six()
        return Review(corrections=(Change(
            key=ORIENTATION, preset="0,0,0", value=pose, reason="a made-up reason",
            source=Source.SPONSOR, feature=", ".join(request.laid_traits)),))

    return send


def test_supports_of_the_models_used_again_are_still_tree(shop: Shop, tmp_path: Path) -> None:
    stl = write_stl("cube", tmp_path)
    first = Steps(lambda r: f"take {number('enable_support')(r)}", "ok")
    assert shop.run(stl, settled(first), Supporting(ask=proposing()), as_laid=True), shop.error
    again_ = Steps("use", "ok")
    row = shop.run(stl, again_, as_laid=True)
    assert row is not None, shop.error
    [used] = [c for c in row.corrections or () if c.key == "enable_support"]
    assert used.used is not None and used.used.of_model
    assert used.used.by is Source.READING, "read by older code as a take"
    assert row.slicer_settings["support_type"] == "tree(auto)"


def test_a_no_sets_nothing_and_a_guards_setting_stays_the_guards(
    shop: Shop, tmp_path: Path
) -> None:
    off = {"key": "enable_support", "value": "0", "feature": "overhang",
           "knowledge": "supports", "reason": "bridge it"}
    steps = Steps(lambda r: f"no {number('enable_support')(r)} because it needs them", "ok")
    row = shop.run(write_stl("tee", tmp_path), settled(steps), Proposer(ask=proposing(off)),
                   as_laid=True)
    assert row is not None, shop.error
    [no] = row.corrections or ()
    assert (no.key, no.value, no.said_no_to) == ("enable_support", "1", "0")
    assert row.slicer_settings["enable_support"] == "1"
    assert row.slicer_settings["support_type"] == "tree(auto)"
    supports = row.proposal.get("enable_support") if row.proposal else None
    assert supports is not None and supports.source is Source.GUARD
    assert "enable_support stays 1: it needs them [you, saying no to the model's 0]" in (
        render_review(steps.seen[1]))


def test_a_no_is_its_poses_and_isnt_carried_through_a_turn(shop: Shop, tmp_path: Path) -> None:
    steps = Steps(lambda r: f"no {number('wall_loops')(r)} because two are plenty",
                  sent_to("90,0,0"), "ok")
    row = shop.run(write_stl("cube", tmp_path), settled(steps), Proposer(ask=proposing(WALLS)),
                   as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    assert row.proposal.get("wall_loops") is None, "turned, the no sets nothing either"
    assert [(c.key, c.said_no_to) for c in row.corrections or ()] == [
        ("wall_loops", "4"), (ORIENTATION, "")]
    assert "wall_loops=" not in again(row, None).corrections, "and print again doesn't type it"
    assert any(n.startswith("You turned down the model's wall_loops=4, because two are plenty")
               for n in again(row, None).notes)


def test_used_and_turned_down_in_one_answer_slices_whats_used(
    shop: Shop, tmp_path: Path
) -> None:
    stl = write_stl("cube", tmp_path)
    assert shop.run(stl, Steps("wall_loops=3 because I like three", "ok"), as_laid=True)
    steps = Steps(lambda r: f"use; no {number('wall_loops')(r)} because four is too many", "ok")
    row = shop.run(stl, settled(steps), Proposer(ask=proposing(WALLS)), as_laid=True)
    assert row is not None, shop.error
    assert row.slicer_settings["wall_loops"] == "3"
    assert [(c.value, c.said_no_to) for c in row.corrections or ()] == [("3", ""), ("3", "4")]


def test_whats_taken_and_left_behind_by_a_turn_is_said(shop: Shop, tmp_path: Path) -> None:
    steps = Steps(lambda r: f"take {number('wall_loops')(r)}", sent_to("90,0,0"), "ok")
    row = shop.run(write_stl("cube", tmp_path), settled(steps), Proposer(ask=proposing(WALLS)),
                   as_laid=True)
    assert row is not None and row.proposal is not None, shop.error
    said = ("Taken at 0,0,0 and left behind by the turn: wall_loops=4. Take it again if it's "
            "proposed here, or type it.")
    assert said in row.proposal.notes
    assert "note: " + said in flat(render_review(steps.seen[2]))
    assert "wall_loops=4 (taken, then left behind by a turn)" in describe(row)


def test_a_turn_taken_is_the_persons_choice_for_this_print_and_the_next(
    shop: Shop, tmp_path: Path
) -> None:
    stl = write_stl("tee", tmp_path)
    assert shop.run(stl, Steps(sent_to("90,0,0"), "ok"), as_laid=True), shop.error
    steps = Steps(lambda r: f"take {number(ORIENTATION)(r)}", "ok")
    row = shop.run(stl, settled(steps), loop=PrintLoop(chat=turning(), person=nobody,
                                                      model="fake"))
    assert row is not None and row.rotation_deg == FLIPPED, shop.error
    after = steps.seen[1]
    assert after.offering is not None
    assert after.offering.past is not None
    [past] = [i for i in after.offering.past.items if i.key == ORIENTATION]
    standing = after.offering.standing(past, after.proposal, "180,0,0")
    assert not standing.selected
    assert standing.why == ("you chose this pose for this print (you took the model's turn at "
                            "this job)",)
    assert any(n.startswith("The model's turn to 180,0,0 was taken at the review")
               for n in again(row, stl).notes)
    # The next print of it is offered that pose, as the person's taking.
    third = Steps("ok")
    assert shop.run(stl, third, as_laid=True), shop.error
    offered = third.seen[0].offering
    assert offered is not None and offered.past is not None
    [turn] = [i for i in offered.past.items if i.key == ORIENTATION]
    assert (turn.value, turn.used.by, turn.used.of_model) == ("180,0,0", Source.READING, True)


@pytest.mark.parametrize(("lesson", "step", "scored"), [
    ("0,0,0", lambda r: f"no {number(ORIENTATION)(r)} because the top shows", "used"),
    ("0,0,0", lambda r: f"take {number(ORIENTATION)(r)}", "in_the_way"),
    ("180,0,0", None, "not_used"),
])
def test_a_pose_lesson_is_scored_by_what_was_printed(
    shop: Shop, tmp_path: Path, lesson: str, step: Any, scored: str
) -> None:
    from print_shop.ingest.analyzer import analyze  # noqa: PLC0415
    from print_shop.setup.features import measure  # noqa: PLC0415
    from print_shop.setup.traits import traits  # noqa: PLC0415
    from tests.test_ledger import record  # noqa: PLC0415

    stl = write_stl("tee", tmp_path)
    shop.ledger.record_start_requested(record("job-0"))
    keyed = list(traits(measure(stl, analyze(stl).bed_contact_area_mm2)))
    made = print_lessons.write(shop.ledger, "job-0", "reviewer: test", "a made-up pose lesson",
                               keyed, f"orientation={lesson}")
    print_lessons.accept(shop.ledger, made, "reviewer: test")
    steps = Steps(*([step] if step else []), "ok")
    row = shop.run(stl, settled(steps), loop=PrintLoop(chat=turning(), person=nobody,
                                                      model="fake"))
    assert row is not None, shop.error
    [found] = shop.ledger.query(
        "SELECT score FROM print_lesson_scores WHERE job_id=? AND lesson=?", row.job_id, made)
    assert found["score"] == scored


def test_going_on_while_the_fallback_orientation_call_runs_asks_nothing_more(
    shop: Shop, held: Held, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, ended = threading.Event(), threading.Event()
    ended_by: list[bool] = []

    def orientation(request: Any, config: Any, stop: threading.Event | None = None) -> Any:
        assert stop is not None, "beside a review, the call can be ended"
        started.set()
        ended_by.append(stop.wait(WAIT_S))
        ended.set()
        raise Stopped("ended")

    monkeypatch.setattr(graph, "propose_orientation", orientation)

    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("the made-up loop model is down")

    def goes_on(request: ReviewRequest) -> Review:
        assert started.wait(WAIT_S)
        return Review(approved=True)

    row = shop.run(write_stl("tee", tmp_path), goes_on, by_llm(),
                   PrintLoop(chat=down, person=nobody, model="fake"),
                   llm=LLMConfig(api_key=SecretStr("not-a-key"), model="fake"))
    assert row is not None, shop.error
    assert ended.wait(WAIT_S) and ended_by == [True], "going on ended the orientation call"
    time.sleep(0.3)
    assert held.calls == 0, "no settings call starts once the person went on"


def test_going_on_ends_the_fallbacks_settings_call(
    shop: Shop, held: Held, tmp_path: Path
) -> None:
    def down(messages: Any, tools: Any) -> dict[str, Any]:
        raise RuntimeError("the made-up loop model is down")

    def goes_on(request: ReviewRequest) -> Review:
        assert held.began.wait(WAIT_S), "the settings call ran, after the loop"
        return Review(approved=True)

    row = shop.run(write_stl("tee", tmp_path), goes_on, by_llm(),
                   PrintLoop(chat=down, person=nobody, model="fake"))
    assert row is not None, shop.error
    assert held.closed.wait(WAIT_S), "going on ended it"


def test_the_runtimes_loop_can_be_ended(monkeypatch: pytest.MonkeyPatch) -> None:
    from print_shop import runtime  # noqa: PLC0415

    made: list[threading.Event | None] = []
    def llm_chat(config: LLMConfig, stop: threading.Event | None = None) -> Any:
        made.append(stop)
        return unused

    monkeypatch.setattr(runtime, "llm_chat", llm_chat)
    loop = runtime.print_loop(LLMConfig(api_key=SecretStr("not-a-key"), model="fake"))
    stop = threading.Event()
    assert loop.stoppable is not None
    loop.stoppable(stop)
    assert made == [None, stop]


def test_the_loops_calls_are_kept_when_the_person_goes_on(shop: Shop, tmp_path: Path) -> None:
    second = threading.Event()
    calls = 0

    def stoppable(stop: threading.Event) -> Any:
        def chat(messages: Any, tools: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"content": "", "tool_calls": [{"id": "a", "type": "function", "function": {
                    "name": "orca_setting", "arguments": json.dumps({"key": "wall_loops"})}}]}
            second.set()
            assert stop.wait(WAIT_S), "going on ends the call under way"
            raise Stopped("ended")

        return chat

    def goes_on(request: ReviewRequest) -> Review:
        assert second.wait(WAIT_S)
        return Review(approved=True)

    loop = PrintLoop(chat=unused, person=nobody, model="fake", stoppable=stoppable)
    row = shop.run(write_stl("tee", tmp_path), goes_on, loop=loop)
    assert row is not None and row.proposal is not None, shop.error
    assert [c.tool for c in row.proposal.loop] == ["orca_setting"]
    assert row.proposal.loop_ending == "stopped as you went on, after 1 calls"


def test_the_models_log_lines_are_kept_off_the_open_prompt() -> None:
    import logging  # noqa: PLC0415

    script = Path(__file__).resolve().parent.parent / "scripts" / "print_job.py"
    spec = importlib.util.spec_from_file_location("print_job", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def said(thread: str) -> bool:
        # A line of the job's own: httpx's are kept off the terminal on any thread
        # (mqucifer/print-shop#266).
        record = logging.LogRecord("print_shop.setup.suggest", logging.INFO, "", 0, "a line",
                                   (), None)
        record.threadName = thread
        return bool(module.on_screen(record))

    assert not said("model-settings") and not said("llm-stream")
    assert said("MainThread")


def test_a_question_on_the_guards_supports_is_still_said(shop: Shop, tmp_path: Path) -> None:
    model = Model(
        [("ask", {"question": "Supports under the clip?", "reason": "its hook hangs",
                  "rotation": [0, 0, 0]})],
        [("propose", {"rotation": [0, 0, 0], "settings": [WALLS], "reasoning": "as it lies"})],
    )
    steps = Steps("ok")
    row = shop.run(write_stl("snap", tmp_path), settled(steps),
                   loop=PrintLoop(chat=model, person=nobody, model="fake"))
    assert row is not None, shop.error
    said = render_review(steps.seen[0])
    assert ('It would have asked first: "Supports under the clip?" It\'s a big change: supports '
            "on, for") in said


def test_an_earlier_poses_failure_is_said_as_that_poses() -> None:
    from print_shop.setup.suggest import recorded  # noqa: PLC0415

    failed = Suggested(pose=LAID, ending=Ending.UNAVAILABLE, said="unavailable: down")
    came = Suggested(pose=ON_ITS_SIDE, ending=Ending.CAME)
    notes = recorded(Proposal(), (failed, came)).notes
    assert notes == ("For the pose 0,0,0: The model's settings didn't come: unavailable after 0 s "
                     "(down).",)


def test_an_end_just_after_the_prompt_looks_is_still_said_under_it() -> None:
    term = Open()
    real = term.work.now
    first = True

    def now() -> Suggested:
        nonlocal first
        found = real()
        if first:
            first = False
            term.release.set()  # it ends after it was looked at, before the prompt opens
            assert term.work.wait(WAIT_S)
        return found

    term.work.now = now  # type: ignore[method-assign]
    said: list[bool] = []

    def read(prompt: str) -> str:
        until = time.monotonic() + WAIT_S
        while not any("came after" in s for s in term.shown) and time.monotonic() < until:
            time.sleep(0.01)
        said.append(any("came after" in s for s in term.shown))
        return "stop"

    term.review(read)
    assert said == [True]


def test_the_loops_calls_and_the_settings_call_can_be_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from print_shop.setup import loop as loop_module  # noqa: PLC0415
    from print_shop.setup import model as model_module  # noqa: PLC0415

    stop = threading.Event()
    config = LLMConfig(api_key=SecretStr("not-a-key"), model="fake")
    chats: list[Any] = []
    def chat_with_tools(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], None]:
        chats.append(kwargs)
        return {"content": ""}, None

    monkeypatch.setattr(loop_module, "chat_with_tools", chat_with_tools)
    loop_module.llm_chat(config, stop)([], [])
    assert chats == [{"answer_chars": loop_module.LOOP_ANSWER_CHARS, "stop": stop}]
    asks: list[Any] = []
    monkeypatch.setattr(model_module, "llm_ask_streamed", lambda *a: asks.append(a))
    model_module.llm_ask(config, stop)
    assert asks == [(config, 16_000, stop)]


def test_the_loop_uses_its_stoppable_model_and_ends_with_stop(tmp_path: Path) -> None:
    from print_shop.setup.loop import run  # noqa: PLC0415
    from tests.test_suggest import _part  # noqa: PLC0415

    given: list[threading.Event] = []

    def stoppable(stop: threading.Event) -> Any:
        given.append(stop)

        def chat(messages: Any, tools: Any) -> dict[str, Any]:
            stop.set()
            raise Stopped("ended")

        return chat

    stop = threading.Event()
    found = run(PrintLoop(chat=unused, person=nobody, stoppable=stoppable), _part(tmp_path), stop)
    assert given == [stop] and found.ended == "stopped"


@pytest.mark.parametrize("part", ["cube", "tee", "snap"])
def test_with_the_loop_and_no_model_the_settings_are_a_failed_calls_in_the_pose_shown(
    shop: Shop, tmp_path: Path, part: str
) -> None:
    """Not as-laid: the pose shown is the file's own, and its settings are a failed call's
    there."""
    stl = write_stl(part, tmp_path)

    def never(stop: threading.Event) -> Any:
        def chat(messages: Any, tools: Any) -> dict[str, Any]:
            stop.wait(WAIT_S)
            raise Stopped("ended")

        return chat

    screen = Script("ok")
    shown = shop.run(stl, screen, loop=PrintLoop(chat=unused, person=nobody, model="fake",
                                                stoppable=never))
    assert shown is not None and shown.proposal is not None, shop.error
    assert shown.rotation_deg == LAID

    def down(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("LiteLLM is down")

    failed = shop.run(stl, None, Proposer(ask=down, ask_questions=lambda *_: {"questions": []}),
                      as_laid=True)
    assert failed is not None and failed.proposal is not None, shop.error
    assert values(shown.proposal) == values(failed.proposal)


def test_a_bare_no_asks_which_once_the_models_are_listed() -> None:
    base = _request(Proposal())
    request = ReviewRequest(**{
        **{name: getattr(base, name) for name in base.__dataclass_fields__},
        "suggested": Suggested(pose=LAID, ending=Ending.CAME, proposed=(Change(
            key="top_shell_layers", preset="4", value="5", reason="x", source=Source.MODEL),)),
    })
    assert parse_answer("no", request) == (
        "no turns one of the model's down, saying why: no 1 because ... To end the job, type "
        "stop.")
    assert parse_answer("stop", request) == Review(stop=True)


def test_named_twice_the_models_is_taken() -> None:
    base = _request(Proposal())
    model = Change(key="ironing_type", preset="top", value="topmost", reason="x",
                   source=Source.MODEL)
    request = ReviewRequest(**{
        **{name: getattr(base, name) for name in base.__dataclass_fields__},
        "flags": slices_one(),
        "suggested": Suggested(pose=LAID, ending=Ending.CAME, proposed=(model,)),
    })
    for typed in ("take 1 2", "take 2 1", "take 2; take"):
        found = parse_answer(typed, request)
        assert isinstance(found, Review)
        assert [(c.value, c.taken) for c in found.taken] == [("topmost", True)], typed
