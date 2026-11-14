"""A part laid for printing keeps its pose (mqucifer/print-shop#128).

On 2026-11-14 a lid laid top face down, 12 mm tall, was turned on its edge by the orientation
model, and nothing could keep the file's own pose. `print_job.py --as-laid` keeps it: no
orientation is chosen, the rotation is (0, 0, 0), and the review says it was kept as laid, by
the person, and names the pose the part's features were read in. Every service here is a fake.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import command_topic
from print_shop.expert.orientation import OrientationRequest, propose_geometric
from print_shop.ingest.orientation import candidates
from print_shop.ledger import Ledger
from print_shop.orchestrator import graph
from print_shop.orchestrator.daemon import Daemon
from print_shop.runtime import describe
from print_shop.setup import Proposer
from print_shop.setup.intake import replay_asker
from print_shop.setup.review import render
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import ANSWERS, Sponsor, _model, _questions

#: The CC1's build volume, as the machine profile has it.
PLATE = (256.0, 256.0, 256.0)


def _no_orienting(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a part kept as laid is never oriented")


@pytest.fixture
def unoriented(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither the model nor the geometric proposer may choose a pose."""
    monkeypatch.setattr(graph, "propose_orientation", _no_orienting)
    monkeypatch.setattr(graph, "propose_geometric", _no_orienting)


def test_a_part_laid_with_an_overhang_keeps_its_pose(tmp_path: Path) -> None:
    """The tee as authored stands on its pillar, its slab's underside an overhang; the flipped
    pose has none, and is what any proposer would pick. Laid, it stays as it is."""
    found = tuple(candidates(write_stl("tee", tmp_path)))
    outcome = graph._as_laid(OrientationRequest(candidates=found, build_volume_mm=PLATE))
    assert outcome.accepted and outcome.attempts == 0
    kept = outcome.proposal.value
    assert kept.rotation_deg == (0.0, 0.0, 0.0)
    assert kept.overhang_area_mm2 > min(c.overhang_area_mm2 for c in found)
    assert outcome.proposal.source == "as_laid"
    assert outcome.proposal.rationale == (
        "kept as laid, by you (--as-laid): no orientation model was asked"
    )


def test_a_laid_part_that_doesnt_fit_the_plate_says_so(tmp_path: Path) -> None:
    found = tuple(
        c.model_copy(update={"bbox_mm": (300.0, 20.0, 20.0)}) for c in candidates(
            write_stl("cube", tmp_path))
    )
    with pytest.raises(RuntimeError, match="laid as it is, the part .* doesn't fit"):
        graph._as_laid(OrientationRequest(candidates=found, build_volume_mm=PLATE))


def test_as_laid_through_the_daemon_asks_no_model_and_the_review_says_so(
    tmp_path: Path, unoriented: None
) -> None:
    (tmp_path / "work").mkdir()
    bridge = MemoryBridge()
    sponsor = Sponsor("ok")
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work", printer=FramePrinter(),
            slicer=RecordedSlicer(), bridge=lambda cfg: bridge, ledger=ledger,
            proposer=Proposer(ask=_model, ask_questions=_questions),
            asker=replay_asker(ANSWERS), reviewer=sponsor,
        )
        with daemon:
            job_id = daemon.submit(write_stl("cube", tmp_path), as_laid=True)
            daemon.wait_for_gate(job_id, timeout_s=30)
            gate = daemon.pending_gate()
            assert gate is not None and gate["orientation_deg"] == [0.0, 0.0, 0.0]
            assert gate["orientation_rationale"].startswith("kept as laid, by you")
            bridge.deliver(command_topic(CFG), {"decision": "approve"})
            assert daemon.wait_for_finish(job_id, timeout_s=30).printed
        [shown] = sponsor.shown
        review = render(shown)
        assert ("Orientation: (0.0, 0.0, 0.0), kept as laid, by you (--as-laid): no "
                "orientation model was asked") in review
        assert ("  the part's features were read in this pose: rotation (0.0, 0.0, 0.0), "
                "20 mm tall, 400 mm2 on the plate") in review
        row = ledger.get(job_id)
        assert row is not None
        assert (row.rotation_deg, row.orientation_source, row.orientation_accepted) == (
            (0.0, 0.0, 0.0), "as_laid", True
        )
        assert "by as_laid" in describe(row)


def test_without_as_laid_a_pose_is_still_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is unchanged: the proposer chooses."""
    asked: list[OrientationRequest] = []
    chooses = propose_geometric

    def geometric(request: OrientationRequest, *args: Any) -> Any:
        asked.append(request)
        return chooses(request, *args)

    monkeypatch.setattr(graph, "propose_geometric", geometric)
    (tmp_path / "work").mkdir()
    bridge = MemoryBridge()
    with Ledger(tmp_path / "ledger.sqlite") as ledger:
        daemon = Daemon(cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work",
                        printer=FramePrinter(), slicer=RecordedSlicer(),
                        bridge=lambda cfg: bridge, ledger=ledger)
        with daemon:
            job_id = daemon.submit(write_stl("cube", tmp_path))
            daemon.wait_for_gate(job_id, timeout_s=30)
            bridge.deliver(command_topic(CFG), {"decision": "reject"})
            daemon.wait_for_finish(job_id, timeout_s=30)
    assert len(asked) == 1
