"""The Sponsor's review of the proposed settings, on the command, before the approval gate.

mqucifer/print-shop#7, part 3: each change is shown against the preset's own
value with its reason; `ok` goes on to Home Assistant's gate, a correction by
name slices again, and `stop` ends the job before the gate. The ledger keeps
the intake, the proposal and the corrections (part 4).
"""

from __future__ import annotations

import json
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
from print_shop.setup.intake import Intake, ask, read_intake
from print_shop.setup.proposal import Change, Proposal, Source
from print_shop.setup.review import Review, ReviewRequest, parse_answer, render
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

INTAKE = read_intake(DATA / "intake" / "cube.json")


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


def test_a_cut_off_is_shown_as_one() -> None:
    cut = Proposal(model="cut off: cut off at max_tokens=16000 after 16000 tokens",
                   call={"finish_reason": "length"})
    text = render(_request(cut))
    assert "CUT OFF: the model's answer was cut off: cut off at max_tokens=16000" in text
    assert "only the guards' changes stand" in text


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


@pytest.mark.parametrize(("answer", "why"), [
    ("walls=3", "'walls' isn't a setting in the process preset"),
    ("wall_loops 3", "'wall_loops 3' isn't setting=value"),
])
def test_an_unreadable_answer_says_why(answer: str, why: str) -> None:
    assert parse_answer(answer, _request()) == why


def test_the_intake_is_asked_until_each_answer_is_a_choice() -> None:
    answers = iter(["decorative", "cosmetic", "none", "none", "outdoor", ""])
    said: list[str] = []
    intake = ask(lambda _: next(answers), said.append)
    assert intake == Intake.model_validate(
        {"purpose": "cosmetic", "load": "none", "fit": "none", "environment": "outdoor"}
    )
    assert said == ["  one of: cosmetic, functional"]


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


def _model(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "reasoning": "a cube for show",
        "changes": [{"key": "wall_loops", "value": "3", "feature": "flat_top",
                     "knowledge": "walls", "reason": "flat faces show the infill"}],
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
                    ledger=ledger, proposer=Proposer(ask=_model), reviewer=sponsor)
    daemon.press = lambda d: bridge.deliver(command_topic(CFG), {"decision": d})  # type: ignore[attr-defined]
    with daemon:
        yield daemon, sponsor, slicer, ledger, write_stl("cube", tmp_path)


def test_a_correction_slices_again_and_the_ledger_keeps_it(
    setup: tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path],
) -> None:
    daemon, sponsor, slicer, ledger, stl = setup
    job_id = daemon.submit(stl, INTAKE)
    daemon.wait_for_gate(job_id, timeout_s=30)

    assert len(slicer.calls) == 2, "sliced, corrected, sliced again"
    assert [c.value for c in sponsor.shown[0].proposal.changes] == ["3"]
    assert [(c.value, c.source) for c in sponsor.shown[1].proposal.changes] == [
        ("4", Source.SPONSOR)
    ]

    daemon.press("approve")  # type: ignore[attr-defined]
    assert daemon.wait_for_finish(job_id, timeout_s=30).printed
    row = ledger.get(job_id)
    assert row is not None and row.intake == INTAKE
    assert row.proposal is not None
    assert [(c.key, c.value) for c in row.proposal.changes] == [("wall_loops", "4")]
    assert [(c.key, c.preset, c.value) for c in row.corrections or ()] == [
        ("wall_loops", "2", "4")
    ]
    assert "corrected      wall_loops=4" in describe(row)


def test_stop_at_the_review_never_reaches_the_gate(
    setup: tuple[Daemon, Sponsor, RecordedSlicer, Ledger, Path],
) -> None:
    daemon, sponsor, _, ledger, stl = setup
    sponsor.answers = ["stop"]
    job_id = daemon.submit(stl, INTAKE)
    result = daemon.wait_for_finish(job_id, timeout_s=30)
    assert (result.approved, result.printed) == (False, False)
    assert "stopped at the settings review" in (result.error or "")
    assert ledger.get(job_id) is None
