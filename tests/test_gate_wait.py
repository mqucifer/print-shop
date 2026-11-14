"""The approval gate's wait starts after the review, and a wait that runs out ends in a sentence.

mqucifer/print-shop#125. On 2026-11-14 a job ended in a `TimeoutError` traceback at the review
prompt: the hour for the gate counted from submit, so the time spent answering the questions and
reading the review ran it out. Now the person's time isn't counted, the gate shows when it ends,
and a wait that runs out ends the job in one sentence, with a ledger row saying it timed out and
nothing sent to the printer. Every service here is a fake, and the waits are fractions of a
second standing in for hours.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import command_topic, gate_topic
from print_shop.core.state import SliceResult
from print_shop.ledger import JobEnding, Ledger, LedgerError
from print_shop.orchestrator.daemon import Daemon, duration
from print_shop.setup import Proposer
from print_shop.setup.intake import Answer, Question, replay_asker
from print_shop.setup.review import Review, ReviewRequest
from print_shop.slicer.orca import SlicerProfiles
from tests.fakes import CI_PROFILES, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_ledger import record
from tests.test_review import ANSWERS, _model, _questions


class SlowReview:
    """A person who reads the review for `reading_s` before typing ok."""

    def __init__(self, reading_s: float) -> None:
        self.reading_s = reading_s
        self.shown = 0

    def __call__(self, request: ReviewRequest) -> Review:
        self.shown += 1
        time.sleep(self.reading_s)
        return Review(approved=True)


class SlowSlicer(RecordedSlicer):
    """Orca taking `slow_s` over each slice: time that isn't the person's."""

    slow_s: float = 0.0

    def __call__(
        self, stl_path: Path | str, profiles: SlicerProfiles, outdir: Path | str
    ) -> SliceResult:
        time.sleep(self.slow_s)
        return super().__call__(stl_path, profiles, outdir)


class Shop:
    """A daemon with the person's intake and review, a printer, a broker and a ledger, all fakes."""

    def __init__(self, tmp_path: Path, *, review: Any = None, asker: Any = None,
                 slicer: RecordedSlicer | None = None, gate_wait_s: float = 30.0) -> None:
        (tmp_path / "work").mkdir()
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.ledger = Ledger(tmp_path / "ledger.sqlite")
        self.stl = write_stl("cube", tmp_path)
        self.daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work", printer=self.printer,
            slicer=slicer or RecordedSlicer(), bridge=lambda cfg: self.bridge, ledger=self.ledger,
            proposer=Proposer(ask=_model, ask_questions=_questions),
            asker=asker or replay_asker(ANSWERS), reviewer=review or SlowReview(0),
            gate_wait_s=gate_wait_s,
        )

    def press(self, decision: str) -> None:
        self.bridge.deliver(command_topic(CFG), {"decision": decision})


def test_a_slow_review_doesnt_time_out(tmp_path: Path) -> None:
    """The person reads the review for four times the wait: the wait counts from their ok."""
    review = SlowReview(reading_s=1.2)
    shop = Shop(tmp_path, review=review)
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        shop.daemon.wait_for_gate(job_id, timeout_s=0.3)
        gate = shop.daemon.pending_gate()
        assert gate is not None and review.shown == 1
        shop.press("approve")
        assert shop.daemon.wait_for_finish(job_id, timeout_s=30).printed
    assert shop.ledger.get(job_id) is not None and shop.ledger.unprinted(job_id) is None


def test_slow_answers_to_the_questions_dont_time_out(tmp_path: Path) -> None:
    replay = replay_asker(ANSWERS)

    def thinking(questions: Sequence[Question]) -> tuple[Answer, ...]:
        time.sleep(1.2)
        return replay(questions)

    shop = Shop(tmp_path, asker=thinking)
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        shop.daemon.wait_for_gate(job_id, timeout_s=0.3)
        assert shop.daemon.pending_gate() is not None


def test_the_gate_says_when_its_wait_ends(tmp_path: Path) -> None:
    shop = Shop(tmp_path, gate_wait_s=600)
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        before = datetime.now(UTC)
        shop.daemon.wait_for_gate(job_id, timeout_s=30)
        gate = shop.daemon.pending_gate()
        assert gate is not None and gate["answer_within_s"] == 600
        left = (datetime.fromisoformat(gate["answer_by"]) - before).total_seconds()
        assert 590 <= left <= 601
        shop.press("reject")
        shop.daemon.wait_for_finish(job_id, timeout_s=30)


def test_a_gate_that_never_answers_ends_with_a_sentence_and_a_row(tmp_path: Path) -> None:
    shop = Shop(tmp_path, gate_wait_s=0.3)
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        shop.daemon.wait_for_gate(job_id, timeout_s=30)
        result = shop.daemon.wait_for_finish(job_id, timeout_s=30)
        sentence = ("Timed out waiting for Approve or Reject at the approval gate after 0.3 s, "
                    "so nothing printed.")
        assert (result.printed, result.error) == (False, sentence)
        assert shop.bridge.retained_json(gate_topic(CFG)) == {"pending": False}, (
            "the dashboard's gate is cleared, not left pending for a job that ended"
        )
        shop.press("approve")  # too late: there's no gate to press
    ended = shop.ledger.unprinted(job_id)
    assert ended is not None
    assert (ended.outcome, ended.detail, ended.stl_name) == (JobEnding.TIMED_OUT, sentence,
                                                             shop.stl.name)
    assert shop.ledger.get(job_id) is None, "not a print: nothing was sent to the printer"
    assert shop.printer.verbs == []


def test_a_wait_that_runs_out_before_the_gate_ends_the_job_and_no_gate_opens(
    tmp_path: Path,
) -> None:
    """Orca's time isn't the person's: a wait for the gate that runs out on it ends the job,
    and when the slice does come, no gate opens for a job the person was told is over."""
    slicer = SlowSlicer()
    slicer.slow_s = 0.4
    shop = Shop(tmp_path, slicer=slicer)
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        with pytest.raises(TimeoutError) as ran_out:
            shop.daemon.wait_for_gate(job_id, timeout_s=0.3)
        assert str(ran_out.value) == (
            "Timed out waiting for the approval gate after 0.3 s, so nothing printed."
        )
        result = shop.daemon.wait_for_finish(job_id, timeout_s=30)
        assert (result.printed, result.error) == (False, str(ran_out.value))
        assert shop.daemon.pending_gate() is None
        assert shop.bridge.retained_json(gate_topic(CFG)) == {"pending": False}
    ended = shop.ledger.unprinted(job_id)
    assert ended is not None and ended.outcome is JobEnding.TIMED_OUT
    assert shop.printer.verbs == []


def test_only_the_persons_time_is_left_out(tmp_path: Path) -> None:
    """Orca's slices before the questions count, while the slow review never gets the chance."""
    slicer = SlowSlicer()
    slicer.slow_s = 0.2
    shop = Shop(tmp_path, slicer=slicer, review=SlowReview(reading_s=1.0))
    with shop.daemon:
        job_id = shop.daemon.submit(shop.stl)
        with pytest.raises(TimeoutError, match="nothing printed"):
            shop.daemon.wait_for_gate(job_id, timeout_s=0.1)


def test_the_wait_is_said_as_a_person_would() -> None:
    assert (duration(3600), duration(900), duration(45), duration(0.3)) == (
        "60 min", "15 min", "45 s", "0.3 s"
    )


# The ledger
# --------------------------------------------------------------------------


@pytest.fixture
def ledger(tmp_path: Path) -> Iterator[Ledger]:
    with Ledger(tmp_path / "ledger.sqlite") as opened:
        yield opened


def test_a_job_that_ended_unprinted_is_never_started_after(ledger: Ledger) -> None:
    ledger.record_unprinted("job-1", "cube.stl", JobEnding.TIMED_OUT, "Timed out, so nothing.")
    with pytest.raises(LedgerError, match="already ended unprinted"):
        ledger.record_start_requested(record("job-1"))
    assert ledger.get("job-1") is None
    assert [j.line() for j in ledger.unprinted_jobs()] == [
        "job job-1  cube.stl  timed_out: Timed out, so nothing."
    ]


def test_a_print_is_never_recorded_as_unprinted(ledger: Ledger) -> None:
    ledger.record_start_requested(record("job-1"))
    with pytest.raises(LedgerError, match="it's a print"):
        ledger.record_unprinted("job-1", "cube.stl", JobEnding.TIMED_OUT, "no")
    assert ledger.unprinted("job-1") is None


def test_an_older_ledger_gains_the_table_and_keeps_its_rows(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as first:
        first.record_start_requested(record("job-1"))
        first._conn.execute("DROP TABLE unprinted_jobs")
    with Ledger(path) as again:
        assert [r.job_id for r in again.rows()] == ["job-1"]
        assert again.unprinted_jobs() == []
