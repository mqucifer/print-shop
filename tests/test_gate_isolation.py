"""Specification: one gate, one decision.

`AGENTS.md` rule 5 says an approval gate is never routed around. The daemon
satisfied the obvious reading — it waits for a human before resuming — while
leaving a way through that needs no malice, only an impatient press.

`Daemon._on_command` buffered any command that arrived while a job was
*active*, and `_active_job_id` is set in `submit()`, before the mesh is even
analysed. Every command between submit and the gate landed in one long-lived
`Queue`, and `_handle_interrupt` took whatever was at the head:

  - Press Approve while the job is still slicing. `before_print_node` opens,
    calls `get()`, and finds the press already waiting. The print is approved
    without anyone having seen the print time or the filament volume.
  - Press Approve twice at the *first* gate. The second press outlives it and
    is consumed by `before_print_node` the moment it opens.

Both reach the hardware boundary on a decision made about something else. The
docstring already claimed the correct behaviour ("Commands arriving while no
gate is pending are also ignored rather than buffered for the next job"); only
the code disagreed.

The fix is to scope the mailbox to the gate rather than to the job: a gate
opens a fresh inbox, and closes it when it has its answer. A command that
arrives when no inbox is open has nothing to be an answer to, and is dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import MqttConfig, command_topic
from print_shop.env import SETTINGS_PATH
from print_shop.orchestrator.daemon import Decision

APPROVE = json.dumps({"decision": "approve"}).encode()
REJECT = json.dumps({"decision": "reject"}).encode()


def _env_present() -> bool:
    p = SETTINGS_PATH
    return p.exists() and "MQTT_USER" in p.read_text()


needs_broker = pytest.mark.skipif(not _env_present(), reason="no MQTT credentials")



@pytest.fixture
def cfg() -> MqttConfig:
    return MqttConfig.from_env()


@pytest.fixture
def daemon(cfg: MqttConfig, tmp_path: Path) -> Any:
    from print_shop.orchestrator.daemon import Daemon
    from print_shop.slicer.orca import SlicerProfiles
    from tests.fakes import STORE_FILAMENT, STORE_MACHINE, STORE_PROCESS

    profiles = SlicerProfiles(
        machine=STORE_MACHINE,
        process=STORE_PROCESS,
        filament=STORE_FILAMENT,
    )
    d = Daemon(cfg=cfg, profiles=profiles, workdir=tmp_path)
    with d:
        yield d


# --------------------------------------------------------------------------
# The inbox is scoped to a gate, not to a job.
# --------------------------------------------------------------------------


@pytest.mark.mqtt
@needs_broker
def test_no_gate_open_means_no_inbox(daemon: Any) -> None:
    """A daemon with nothing awaiting approval has nowhere to put a decision."""
    assert daemon._gate_inbox is None


@pytest.mark.mqtt
@needs_broker
def test_a_press_before_the_gate_opens_is_dropped(daemon: Any, cfg: MqttConfig) -> None:
    """The impatient press. This is the bypass that reached the hardware.

    A job is active — slicing, say — but no gate is open. The press is about
    nothing: the numbers a human would approve do not exist yet.
    """
    daemon._active_job_id = "job-under-test"
    try:
        daemon._on_command(command_topic(cfg), APPROVE, False)
        assert daemon._gate_inbox is None, "a gate was opened by an incoming command"

        inbox = daemon._open_gate()
        assert inbox.empty(), "a press from before the gate was waiting inside it"
    finally:
        daemon._close_gate()
        daemon._active_job_id = None


@pytest.mark.mqtt
@needs_broker
def test_a_press_at_an_open_gate_is_delivered(daemon: Any, cfg: MqttConfig) -> None:
    daemon._active_job_id = "job-under-test"
    try:
        inbox = daemon._open_gate()
        daemon._on_command(command_topic(cfg), APPROVE, False)
        assert inbox.get_nowait().decision is Decision.APPROVE
    finally:
        daemon._close_gate()
        daemon._active_job_id = None


@pytest.mark.mqtt
@needs_broker
def test_a_second_press_cannot_outlive_its_gate(daemon: Any, cfg: MqttConfig) -> None:
    """The double-press. Gate two must not inherit gate one's spare answer.

    A fresh inbox per gate is what makes this true by construction, rather
    than by remembering to drain.
    """
    daemon._active_job_id = "job-under-test"
    try:
        first = daemon._open_gate()
        daemon._on_command(command_topic(cfg), APPROVE, False)
        daemon._on_command(command_topic(cfg), APPROVE, False)
        assert first.get_nowait().decision is Decision.APPROVE  # consumed
        daemon._close_gate()

        second = daemon._open_gate()
        assert second.empty(), "the spare press was inherited by the next gate"
        assert second is not first, "a gate must get a fresh inbox"

        # And without an intervening close. Every path in `_handle_interrupt`
        # closes before it reopens, so this is belt-and-braces — but the
        # guarantee is "a fresh Queue every time", and a guarantee nothing
        # tests is one a later refactor is free to drop. Reusing a
        # not-yet-closed inbox would hand a gate someone else's unread mail.
        daemon._on_command(command_topic(cfg), APPROVE, False)
        third = daemon._open_gate()
        assert third is not second, "_open_gate reused an inbox that was still open"
        assert third.empty(), "a press from the previous gate survived into this one"
    finally:
        daemon._close_gate()
        daemon._active_job_id = None


@pytest.mark.mqtt
@needs_broker
def test_a_retained_press_is_still_dropped_at_an_open_gate(
    daemon: Any, cfg: MqttConfig
) -> None:
    """The retain guard must survive the rework.

    A replayed approval is stale whether or not a gate happens to be open —
    it was published before this gate existed.
    """
    daemon._active_job_id = "job-under-test"
    try:
        inbox = daemon._open_gate()
        daemon._on_command(command_topic(cfg), APPROVE, True)
        assert inbox.empty(), "a retained approval was delivered to a gate"
    finally:
        daemon._close_gate()
        daemon._active_job_id = None


@pytest.mark.mqtt
@needs_broker
def test_a_press_with_no_job_at_all_is_dropped(daemon: Any, cfg: MqttConfig) -> None:
    daemon._on_command(command_topic(cfg), APPROVE, False)
    assert daemon._gate_inbox is None


@pytest.mark.mqtt
@needs_broker
def test_closing_a_gate_leaves_nothing_behind(daemon: Any, cfg: MqttConfig) -> None:
    """Close must be idempotent — `_finish_job` may close an already-closed gate."""
    daemon._active_job_id = "job-under-test"
    try:
        daemon._open_gate()
        daemon._close_gate()
        daemon._close_gate()
        assert daemon._gate_inbox is None

        daemon._on_command(command_topic(cfg), REJECT, False)
        assert daemon._gate_inbox is None
    finally:
        daemon._active_job_id = None
