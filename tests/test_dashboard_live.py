"""The dashboard, checked against a Home Assistant that is actually running.

`tests/test_dashboard.py` checks the YAML against what this project *intends*
to publish. That is necessary and not sufficient: it would still pass if HA
named the entities something else entirely, which is precisely what happened.

The entity id is not ours to choose. HA derives it from the device and entity
names, and a discovery `object_id` does not override that — measured against
HA 2026.8.3: a probe published with `object_id: "3dprint_dev_probe"` under
device "3D Print Probe" materialised as `sensor.3d_print_probe_probe`. An
earlier version of this dashboard was written against `sensor.3dprint_dev_*`
on the strength of that key and would have bound to nothing, which is the exact
failure `ha-configs/lovelace_dashboard.yaml` already carries a header about.

So this asks the running instance. It is the only check here that can fail for
the right reason.

Needs `HA_TOKEN` in `.env` (Profile -> Security -> Long-lived access tokens)
and the daemon to have announced at least once. Skipped otherwise.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
import yaml

from print_shop.env import SETTINGS_PATH
from tests.test_dashboard import (
    DASHBOARD,
    EXTERNAL_ENTITIES,
    referenced_entities,
)
from tests.test_mqtt import CONTRACTS_HA


def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = SETTINGS_PATH
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    for key in ("HA_URL", "HA_TOKEN", "MQTT_HOST"):
        if key in os.environ:
            env.setdefault(key, os.environ[key])
    return env


_ENV = _env()
needs_ha = pytest.mark.skipif(
    not _ENV.get("HA_TOKEN"), reason="no HA_TOKEN in the Mac's settings"
)


def _ha_url() -> str:
    return _ENV.get("HA_URL") or f"http://{_ENV.get('MQTT_HOST', 'localhost')}:8123"


def _state_ids() -> set[str]:
    r = httpx.get(
        f"{_ha_url()}/api/states",
        headers={"Authorization": f"Bearer {_ENV['HA_TOKEN']}"},
        timeout=30,
    )
    r.raise_for_status()
    return {s["entity_id"] for s in r.json()}


@pytest.fixture(scope="module")
def live_entity_ids() -> set[str]:
    """Announce, then wait for HA to materialise the entities.

    This has to establish its own precondition rather than assume one.
    `tests/test_mqtt.py::test_cleanup_removes_the_entities` publishes empty
    retained discovery payloads, which is HA's signal to *delete* an entity —
    so whether these entities exist depends on what ran before. Asserting
    against that is a test that passes or fails on collection order.
    """
    import time

    from print_shop.core.mqtt import MqttBridge, MqttConfig

    cfg = MqttConfig.from_env()
    with MqttBridge(cfg, client_suffix="live-dashboard") as bridge:
        bridge.announce()
        bridge.wait_for(lambda: False, timeout_s=1.0)

    deadline = time.monotonic() + 45.0
    ids = _state_ids()
    while time.monotonic() < deadline and not set(CONTRACTS_HA) <= ids:
        time.sleep(2.0)
        ids = _state_ids()
    return ids


@pytest.mark.ha
@needs_ha
def test_every_entity_we_publish_exists_in_home_assistant(
    live_entity_ids: set[str],
) -> None:
    """`CONTRACTS_HA` is a claim about another system. Check it against that system."""
    missing = set(CONTRACTS_HA) - live_entity_ids
    assert not missing, (
        f"HA has no such entities: {sorted(missing)}. Either the daemon has "
        f"never announced to this broker, or HA named them something else — "
        f"read /api/states and correct CONTRACTS_HA rather than guessing."
    )


@pytest.mark.ha
@needs_ha
def test_every_entity_on_the_dashboard_resolves(live_entity_ids: set[str]) -> None:
    """Including the Elegoo ones, which no test of ours could otherwise catch."""
    doc: Any = yaml.safe_load(DASHBOARD.read_text())
    missing = referenced_entities(doc) - live_entity_ids
    assert not missing, f"dashboard cards point at entities HA does not have: {sorted(missing)}"


@pytest.mark.ha
@needs_ha
def test_the_external_entities_really_are_external(live_entity_ids: set[str]) -> None:
    """A typo in EXTERNAL_ENTITIES would silently excuse a broken card.

    The offline test treats anything in that set as fine by definition, so the
    set itself needs checking against something real.
    """
    missing = EXTERNAL_ENTITIES - live_entity_ids
    assert not missing, (
        f"EXTERNAL_ENTITIES names entities HA does not have: {sorted(missing)}"
    )
