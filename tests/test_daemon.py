"""Specification for the daemon — the process that makes the gate a button.

This is `CODE_REVIEW.md` §P2-2 arriving for real. Until now the graph has been
invoked synchronously from a test and `interrupt()` parked it in SQLite with
nobody listening. A gate in Home Assistant needs something long-running that
holds the MQTT subscription, publishes state, and resumes the parked graph when
a person presses Approve.

`CONTRACTS.md` §1 says the pipeline is one process. This is that process
acquiring a lifecycle, not a new service.

Two properties matter more than the rest:

- **One job at a time.** A printer has one bed. Concurrency here is not a
  throughput win, it is two jobs fighting over hardware.
- **Availability is honest.** If the daemon dies while a gate is pending, the
  broker's Last Will must say so. An Approve button that looks live but is
  attached to a dead process is worse than no button.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.core.mqtt import MqttConfig
from print_shop.env import SETTINGS_PATH
from print_shop.orchestrator.daemon import (
    Decision,
    JobRejected,
    parse_command,
    state_payload,
)
from tests.test_print_node import FakePrinter, idle

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# --------------------------------------------------------------------------
# Command parsing — pure
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b'{"decision": "approve"}', Decision.APPROVE),
        (b'{"decision": "reject"}', Decision.REJECT),
        (b'{"decision":"approve","note":"looks right"}', Decision.APPROVE),
    ],
)
def test_parses_the_button_payloads(raw: bytes, expected: Decision) -> None:
    """These are exactly what the discovery `payload_press` publishes."""
    assert parse_command(raw).decision is expected


def test_carries_an_optional_note() -> None:
    assert parse_command(b'{"decision":"reject","note":"wrong part"}').note == "wrong part"
    assert parse_command(b'{"decision":"approve"}').note == ""


@pytest.mark.parametrize(
    "raw",
    [b"", b"approve", b"{}", b'{"decision":"maybe"}', b"{not json", b'{"decision":null}'],
)
def test_rejects_anything_that_is_not_a_decision(raw: bytes) -> None:
    """A malformed command must never be read as approval.

    Anyone on the LAN can publish to this topic. The failure mode to design
    against is a garbled payload being interpreted as consent to run a printer.
    """
    with pytest.raises(ValueError):
        parse_command(raw)


# --------------------------------------------------------------------------
# State payload — must match what the discovery configs already subscribe to
# --------------------------------------------------------------------------


def test_the_state_payload_matches_the_discovery_templates() -> None:
    """The HA entities were published with these exact value_templates:

        value_json.status   value_json.orientation_deg
        value_json.print_time_s   value_json.filament_cm3

    A key renamed here silently blanks a card in someone's house.
    """
    payload = state_payload(
        {
            "job_id": "j1",
            "status": "awaiting_approval",
            "orientation_deg": [180.0, 0.0, 0.0],
            "orientation_rationale": "zero overhang",
            "print_time_s": 850,
            "filament_cm3": 2.39,
        }
    )
    parsed = json.loads(payload) if isinstance(payload, str) else payload
    for key in ("status", "orientation_deg", "print_time_s", "filament_cm3"):
        assert key in parsed, f"{key} is referenced by a published HA entity"


def test_an_idle_payload_is_still_well_formed() -> None:
    """Before any job runs, HA still subscribes. Blank, not missing."""
    parsed = state_payload({"status": "idle"})
    parsed = json.loads(parsed) if isinstance(parsed, str) else parsed
    assert parsed["status"] == "idle"
    for key in ("orientation_deg", "print_time_s", "filament_cm3"):
        assert key in parsed


# --------------------------------------------------------------------------
# Lifecycle — against the real broker
# --------------------------------------------------------------------------


def _env_present() -> bool:
    p = SETTINGS_PATH
    return p.exists() and "MQTT_USER" in p.read_text()


needs_broker = pytest.mark.skipif(not _env_present(), reason="no MQTT credentials")

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
DATADIR = Path.home() / "Library" / "Application Support" / "OrcaSlicer"
_SYS = DATADIR / "system" / "Elegoo"
needs_orca = pytest.mark.skipif(not ORCA.exists(), reason="OrcaSlicer not installed")


@pytest.fixture
def cfg() -> MqttConfig:
    return MqttConfig.from_env()


@pytest.fixture
def daemon(cfg: MqttConfig, tmp_path: Path) -> Any:
    from print_shop.orchestrator.daemon import Daemon
    from print_shop.slicer.orca import SlicerProfiles

    profiles = SlicerProfiles(
        machine=_SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json",
        process=_SYS / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json",
        filament=_SYS / "filament" / "ECC" / "Elegoo PLA @ECC.json",
        datadir=DATADIR,
    )
    # An explicit double. This fixture drives real MQTT and presses real
    # Approve buttons, so a daemon here must never be able to reach a real
    # printer — on 2026-09-16 it could, and it started a print.
    d = Daemon(
        cfg=cfg,
        profiles=profiles,
        workdir=tmp_path,
        printer=FakePrinter(snapshot_value=idle()),
    )
    with d:
        yield d


@pytest.mark.mqtt
@needs_broker
def test_announces_itself_and_goes_online(daemon: Any, cfg: MqttConfig) -> None:
    from print_shop.core.mqtt import MqttBridge, availability_topic

    seen: list[bytes] = []
    with MqttBridge(cfg, client_suffix="probe") as probe:
        probe.subscribe(availability_topic(cfg), lambda t, p, r: seen.append(p))
        probe.wait_for(lambda: b"online" in seen, timeout_s=6.0)
    assert b"online" in seen


@pytest.mark.mqtt
@needs_broker
def test_publishes_idle_state_before_any_job(daemon: Any, cfg: MqttConfig) -> None:
    from print_shop.core.mqtt import MqttBridge, state_topic

    seen: list[bytes] = []
    with MqttBridge(cfg, client_suffix="probe2") as probe:
        probe.subscribe(state_topic(cfg), lambda t, p, r: seen.append(p))
        probe.wait_for(lambda: bool(seen), timeout_s=6.0)
    assert seen and json.loads(seen[-1])["status"] in ("idle", "starting")


@pytest.mark.mqtt
@pytest.mark.slicer
@pytest.mark.llm
@needs_broker
@needs_orca
def test_a_job_runs_parks_at_the_gate_and_announces_it(
    daemon: Any, cfg: MqttConfig, tmp_path: Path
) -> None:
    """The whole point: a job reaches a human and waits."""
    from print_shop.core.mqtt import MqttBridge, gate_topic
    from tests.fixtures import write_stl

    gate: list[dict[str, Any]] = []
    with MqttBridge(cfg, client_suffix="probe3") as probe:
        probe.subscribe(gate_topic(cfg), lambda t, p, r: gate.append(json.loads(p or b"{}")))
        job_id = daemon.submit(write_stl("tee", tmp_path))
        probe.wait_for(lambda: any(g.get("pending") for g in gate), timeout_s=180.0)

    pending = [g for g in gate if g.get("pending")]
    assert pending, "no gate was ever announced"
    g = pending[-1]
    assert g["job_id"] == job_id
    assert g["print_time_s"] > 0
    assert g["orientation_deg"] == [180.0, 0.0, 0.0]
    assert g["orientation_rationale"]


@pytest.mark.mqtt
@pytest.mark.slicer
@pytest.mark.llm
@needs_broker
@needs_orca
def test_pressing_reject_ends_the_job_without_printing(
    daemon: Any, cfg: MqttConfig, tmp_path: Path
) -> None:
    """Publishing the button's own payload must drive the graph."""
    from print_shop.core.mqtt import MqttBridge, command_topic
    from tests.fixtures import write_stl

    job_id = daemon.submit(write_stl("tee", tmp_path))
    daemon.wait_for_gate(job_id, timeout_s=180.0)

    with MqttBridge(cfg, client_suffix="presser") as presser:
        presser.publish(command_topic(cfg), '{"decision": "reject", "note": "no"}')
        presser.wait_for(lambda: True, timeout_s=1.0)

    result = daemon.wait_for_finish(job_id, timeout_s=60.0)
    assert result.approved is False
    assert result.printed is False


@pytest.mark.mqtt
@pytest.mark.slicer
@pytest.mark.llm
@needs_broker
@needs_orca
def test_approving_reaches_the_print_node(
    daemon: Any, cfg: MqttConfig, tmp_path: Path
) -> None:
    """An approve press on the MQTT topic runs the job all the way to the printer.

    This is the whole product in one test: a person presses a button in Home
    Assistant and a part starts printing. The printer is a double — this
    fixture presses Approve for real, so it must not be able to reach the
    machine.
    """
    from print_shop.core.mqtt import MqttBridge, command_topic
    from tests.fixtures import write_stl

    job_id = daemon.submit(write_stl("cube", tmp_path))
    daemon.wait_for_gate(job_id, timeout_s=180.0)

    with MqttBridge(cfg, client_suffix="presser2") as presser:
        presser.publish(command_topic(cfg), '{"decision": "approve"}')
        presser.wait_for(lambda: True, timeout_s=1.0)

    result = daemon.wait_for_finish(job_id, timeout_s=60.0)
    assert result.approved is True
    assert result.error is None, f"the print node failed: {result.error}"
    assert result.printed is True
    assert daemon.is_alive()


@pytest.mark.mqtt
@pytest.mark.slicer
@pytest.mark.llm
@needs_broker
@needs_orca
def test_only_one_job_runs_at_a_time(daemon: Any, tmp_path: Path) -> None:
    """A printer has one bed. The second submission waits or is refused."""
    from tests.fixtures import write_stl

    a = daemon.submit(write_stl("tee", tmp_path))
    daemon.wait_for_gate(a, timeout_s=180.0)
    with pytest.raises(JobRejected):
        daemon.submit(write_stl("cube", tmp_path))



# `test_a_retained_approval_never_reaches_the_gate` lived here. It asserted on
# `Daemon._command_queue`, the single job-scoped queue that
# `tests/test_gate_isolation.py` replaced with a gate-scoped inbox. The retain
# guard it covered is now covered by
# `test_a_retained_press_is_still_dropped_at_an_open_gate`, against the
# structure that actually exists.
