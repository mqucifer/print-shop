"""The Sponsor's review of the proposed settings, on the command, before the approval gate.

mqucifer/print-shop#7, part 3: each change is shown against the preset's own
value with its reason; `ok` goes on to Home Assistant's gate, a correction by
name slices again, and `stop` ends the job before the gate. The ledger keeps
the intake, the proposal and the corrections (part 4).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import command_topic
from print_shop.core.state import AppliedOrientation, SliceResult
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.intake import replay_asker
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.reading import Seen, flags, read_slice
from print_shop.setup.review import _BY, Review, ReviewRequest, parse_answer, render
from tests.fakes import (
    CI_PROFILES,
    DATA,
    STORE_PROFILES,
    FramePrinter,
    MemoryBridge,
    RecordedSlicer,
)
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG

#: The cube's one question, answered with its second option (3 walls).
ANSWERS = DATA / "answers" / "cube.json"


def _request(proposal: Proposal | None = None) -> ReviewRequest:
    return ReviewRequest(
        presets=("Machine - Copy", "0.20mm - Copy", "PLA - Copy"),
        proposal=proposal or Proposal(
            reasoning="A 20 mm cube for show: its faces are what matter.",
            changes=(Change(key="wall_loops", preset="2", value="3", source=Source.MODEL,
                            reason="the flat faces show infill through 2 walls",
                            feature="flat_top", knowledge="walls"),),
            kept=(Change(key="enable_support", preset="0", value="0", source=Source.MODEL,
                         reason="nothing overhangs", feature="overhang", knowledge="supports"),),
            model="crew-code-think",
        ),
        slice=SliceResult(gcode_path=Path("x.gcode"), layer_count=100, max_z_mm=20,
                          filament_mm=900, filament_cm3=2.9, filament_g=3.6, print_time_s=654),
        orientation=AppliedOrientation(rotation_deg=(0.0, 0.0, 0.0), rationale="flat face down"),
        profiles=STORE_PROFILES,
        process={"wall_loops": "2", "layer_height": "0.2"},
        filament={"nozzle_temperature": ["220"]},
    )


def test_the_review_shows_each_change_against_the_preset_and_the_reasoning() -> None:
    text = render(_request())
    assert "How the part was read:\n  A 20 mm cube for show" in text
    assert "wall_loops  2 -> 3" in text
    assert "the flat faces show infill through 2 walls [model: flat_top, walls]" in text
    assert "kept enable_support at 0: nothing overhangs" in text
    assert "Orca's estimate: 10 min 54 s, 2.9 cm3, 3.6 g, 100 layers" in text
    assert "flat face down" in text


def test_every_source_has_a_name_in_the_review() -> None:
    assert set(_BY) == set(Source)


def test_a_change_from_an_intake_answer_is_shown_as_yours() -> None:
    answer = Proposal(changes=(Change(key="wall_loops", preset="2", value="3", source=Source.ANSWER,
                                      reason="holds shape under light use"),),
                      model="crew-code-think")
    assert "holds shape under light use [your answer]" in render(_request(answer))


def test_a_cut_off_is_shown_as_one() -> None:
    cut = Proposal(model="cut off: cut off at max_tokens=16000 after 16000 tokens",
                   call={"finish_reason": "length"})
    text = render(_request(cut))
    assert "CUT OFF: the model's answer was cut off: cut off at max_tokens=16000" in text
    assert "only the guards' changes stand" in text


def test_a_tuned_preset_shows_standard_then_tuning_then_this_print() -> None:
    hotter = Proposal(changes=(Change(key="nozzle_temperature", standard="210", preset="220",
                                      value="230", source=Source.MODEL, reason="bonding"),))
    text = render(_request(hotter))
    assert "setting             standard -> your tuning -> this print" in text
    assert "nozzle_temperature  210 -> 220 -> 230" in text


def test_ok_approves_and_stop_stops() -> None:
    assert parse_answer("ok", _request()) == Review(approved=True)
    assert parse_answer(" stop ", _request()) == Review(stop=True)


def test_a_correction_is_kept_against_the_presets_own_value() -> None:
    review = parse_answer(
        "wall_loops=4 because it gets handled; nozzle_temperature=215", _request()
    )
    assert isinstance(review, Review)
    assert [(c.key, c.preset, c.value, c.reason, c.source) for c in review.corrections] == [
        ("wall_loops", "2", "4", "it gets handled", Source.SPONSOR),
        ("nozzle_temperature", "220", "215", "your correction", Source.SPONSOR),
    ]


def test_a_value_may_have_spaces_as_orcas_own_do() -> None:
    request = _request()
    request = ReviewRequest(**{**request.__dict__,
                               "process": {**request.process, "ironing_type": "top"}})
    review = parse_answer("ironing_type=no ironing because the top is hidden", request)
    assert isinstance(review, Review)
    assert [(c.key, c.value, c.reason) for c in review.corrections] == [
        ("ironing_type", "no ironing", "the top is hidden")]
    dashed = parse_answer("wall_loops=4 - it gets handled", _request())
    assert isinstance(dashed, Review)
    assert [(c.value, c.reason) for c in dashed.corrections] == [("4", "it gets handled")]


# --------------------------------------------------------------------------
# The slice's reading at the review (mqucifer/print-shop#105)
# --------------------------------------------------------------------------


def _read_request() -> ReviewRequest:
    reading = read_slice(DATA / "recorded" / "cube.gcode")
    seen_all = Seen(walls=True, top=True, bottom=False, said="the walls and the top")
    found = flags(reading, seen_all, {"seam_position": "aligned"}, {})
    request = _request()
    return ReviewRequest(**{**request.__dict__, "reading": reading, "flags": found,
                            "process": {**request.process, "seam_position": "aligned"}})


def test_the_review_shows_the_slice_layer_by_layer_and_what_it_proposes() -> None:
    text = render(_read_request())
    assert "What the slice shows, layer by layer:\n  layers: 100, " in text
    assert "  Sparse infill: " in text
    assert "seam (aligned): " in text and "a line up the wall" in text
    assert "Proposed from the slice (not applied unless you take it):" in text
    assert "  1) seam_position  aligned -> back" in text
    assert "read: " in text
    assert "take what the slice proposes (take, or take 1)" in text


def test_a_reading_proposes_and_only_take_applies_it() -> None:
    request = _read_request()
    assert parse_answer("ok", request) == Review(approved=True), "never applied by itself"
    taken = parse_answer("take", request)
    assert isinstance(taken, Review) and taken.corrections == ()
    assert [(c.key, c.value, c.source) for c in taken.taken] == [
        ("seam_position", "back", Source.READING)]
    assert taken.taken[0].reading, "the reading it rests on goes with it"
    both = parse_answer("take 1; wall_loops=3 because it's handled", request)
    assert isinstance(both, Review) and len(both.taken) == 1 and len(both.corrections) == 1
    yours = parse_answer("take; seam_position=random because it's round", request)
    assert isinstance(yours, Review) and yours.taken == (), "your correction wins"
    assert parse_answer("take 3", request) == "take a number from 1 to 1"
    assert parse_answer("take", _request()) == "the slice proposes nothing to take"


@pytest.mark.parametrize(("answer", "why"), [
    ("walls=3", "'walls' isn't a setting in the process preset"),
    ("wall_loops 3", "'wall_loops 3' isn't setting=value"),
])
def test_an_unreadable_answer_says_why(answer: str, why: str) -> None:
    assert parse_answer(answer, _request()) == why


# --------------------------------------------------------------------------
# Through the daemon: review, correct, slice again, then the gate
# --------------------------------------------------------------------------


class Sponsor:
    """Answers the review from a script, and keeps what it was shown."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.shown: list[ReviewRequest] = []

    def __call__(self, request: ReviewRequest) -> Review:
        self.shown.append(request)
        parsed = parse_answer(self.answers.pop(0), request)
        assert isinstance(parsed, Review), parsed
        return parsed


def _questions(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    option = {"settings": [], "impact": "", "recommended": False, "why": ""}
    return {"questions": [{"feature": "purpose", "text": "What is the cube for?", "options": [
        {**option, "label": "a test print", "recommended": True, "why": "it's a cube"},
        {**option, "label": "a gift", "settings": [{"key": "wall_loops", "value": "3"}],
         "impact": "sturdier faces"},
    ]}]}


def _model(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasoning": "a cube for show",
        "changes": [{"key": "wall_loops", "value": "5", "feature": "flat_top",
                     "knowledge": "walls-shells", "reason": "flat faces show the infill"}],
        "design_notes": [],
    }


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path]]:
    (tmp_path / "work").mkdir()
    process = json.loads(CI_PROFILES.process.read_text())
    (tmp_path / "process.json").write_text(json.dumps({**process, "wall_loops": "2"}))
    profiles = CI_PROFILES.model_copy(update={"process": tmp_path / "process.json"})
    sponsor = Sponsor("wall_loops=4 because I handle it", "ok")
    slicer = RecordedSlicer()
    ledger = Ledger(tmp_path / "ledger.sqlite")
    bridge = MemoryBridge()
    daemon = Daemon(cfg=CFG, profiles=profiles, workdir=tmp_path / "work",
                    printer=FramePrinter(), slicer=slicer, bridge=lambda cfg: bridge,
                    ledger=ledger, proposer=Proposer(ask=_model, ask_questions=_questions),
                    asker=replay_asker(ANSWERS), reviewer=sponsor)
    daemon.press = lambda d: bridge.deliver(command_topic(CFG), {"decision": d})  # type: ignore[attr-defined]
    with daemon:
        yield daemon, sponsor, slicer, ledger, write_stl("cube", tmp_path)


def test_a_correction_slices_again_and_the_ledger_keeps_it(
    setup: tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path],
) -> None:
    daemon, sponsor, slicer, ledger, stl = setup
    job_id = daemon.submit(stl)
    daemon.wait_for_gate(job_id, timeout_s=30)

    assert len(slicer.calls) == 3, (
        "the option that sets walls priced (the one that sets nothing isn't), sliced, "
        "corrected, sliced again"
    )
    first = sponsor.shown[0].proposal
    assert [(c.value, c.source) for c in first.changes] == [("3", Source.ANSWER)]
    assert first.refused == ("wall_loops=5: you answered it at the intake",)
    assert [(c.value, c.source) for c in sponsor.shown[1].proposal.changes] == [
        ("4", Source.SPONSOR)
    ]

    daemon.press("approve")  # type: ignore[attr-defined]
    assert daemon.wait_for_finish(job_id, timeout_s=30).printed
    row = ledger.get(job_id)
    assert row is not None and row.intake is not None
    # What's seen, asked last by print-shop (print-shop#105), answered in the recording.
    assert row.intake.summary() == (
        "What is the cube for? a gift; Which faces of this part will be seen once it's in "
        "use? the walls and the top as it prints"
    )
    option = row.intake.questions[0].options[1]
    assert option.estimate is not None, "Orca's estimate, from the recorded slice"
    assert row.proposal is not None
    assert [(c.key, c.value) for c in row.proposal.changes] == [("wall_loops", "4")]
    assert [(c.key, c.preset, c.value) for c in row.corrections or ()] == [
        ("wall_loops", "2", "4")
    ]
    assert "corrected      wall_loops=4" in describe(row)


def test_the_reading_is_shown_taken_sliced_again_and_kept_with_the_job(
    setup: tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path],
) -> None:
    """mqucifer/print-shop#105, through the daemon. The cube's walls are seen (the recommended
    answer to what's seen) and its seam is a line up a corner, so moving it is proposed."""
    daemon, sponsor, slicer, ledger, stl = setup
    sponsor.answers = ["take", "ok"]
    job_id = daemon.submit(stl)
    daemon.wait_for_gate(job_id, timeout_s=30)

    first, second = sponsor.shown
    assert first.reading is not None and len(first.reading.layers) == 100
    assert [f.change.key for f in first.proposed if f.change] == ["seam_position"]
    assert first.proposal.get("seam_position") is None, "proposed, not applied"
    taken = second.proposal.get("seam_position")
    assert taken is not None and (taken.value, taken.source) == ("back", Source.READING)

    daemon.press("approve")  # type: ignore[attr-defined]
    assert daemon.wait_for_finish(job_id, timeout_s=30).printed
    row = ledger.get(job_id)
    assert row is not None and row.reading is not None and row.flags is not None
    assert row.corrections == (), "taking a reading's change isn't a correction"
    assert row.proposal is not None and row.proposal.get("seam_position") == taken
    assert "slice read     layers: 100, " in describe(row)


def test_stop_at_the_review_never_reaches_the_gate(
    setup: tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path],
) -> None:
    daemon, sponsor, _, ledger, stl = setup
    sponsor.answers = ["stop"]
    job_id = daemon.submit(stl)
    result = daemon.wait_for_finish(job_id, timeout_s=30)
    assert (result.approved, result.printed) == (False, False)
    assert "stopped at the settings review" in (result.error or "")
    assert ledger.get(job_id) is None


def test_answers_written_for_other_questions_end_the_job_before_the_gate(
    tmp_path: Path,
) -> None:
    """mqucifer/print-shop#107: a file for other questions is refused, and says so at once.

    It once answered whatever was asked, by position. `wait_for_gate` returns as
    the job ends, rather than leaving `print_job.py` waiting out the hour.
    """
    (tmp_path / "work").mkdir()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"answers": [{"question": "overhang", "choice": 1}]}))
    sponsor = Sponsor("ok")
    ledger = Ledger(tmp_path / "ledger.sqlite")
    daemon = Daemon(cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work",
                    printer=FramePrinter(), slicer=RecordedSlicer(),
                    bridge=lambda cfg: MemoryBridge(), ledger=ledger,
                    proposer=Proposer(ask=_model, ask_questions=_questions),
                    asker=replay_asker(answers), reviewer=sponsor)
    with daemon:
        job_id = daemon.submit(write_stl("cube", tmp_path))
        started = time.monotonic()
        daemon.wait_for_gate(job_id, timeout_s=60)
        assert time.monotonic() - started < 30 and daemon.pending_gate() is None
        result = daemon.wait_for_finish(job_id, timeout_s=5)
    assert (result.approved, result.printed) == (False, False)
    assert "doesn't answer these questions, so none of it is used" in (result.error or "")
    assert "1. [purpose] What is the cube for?" in (result.error or "")
    assert not sponsor.shown and ledger.get(job_id) is None
