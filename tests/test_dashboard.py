"""Specification for the Lovelace dashboard.

A dashboard fails silently. A card pointed at an entity that does not exist
renders an "Entity not available" box, or — for a template in a markdown card —
renders nothing at all and looks like a design choice. Nobody notices until
they need it.

That is not hypothetical. The prototype's `lovelace_dashboard.yaml` (not ported) was written
against guessed entity ids for the first build, bound to nothing, and now
carries a REFERENCE ONLY header.

So the fitness function for a dashboard is: **every entity it names must be one
this project actually publishes.** The ids are not ours to choose — HA derives
them from the device and entity names — so `CONTRACTS_HA` in
`tests/test_mqtt.py` holds them and this checks the YAML against that list,
including the ids buried in Jinja templates, where a typo is least visible.

Being consistent with `CONTRACTS_HA` is still not the same as being correct:
`tests/test_dashboard_live.py` asks a running Home Assistant.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from print_shop.core.mqtt import MqttConfig, discovery_payloads
from tests.test_mqtt import CONTRACTS_HA, ha_slugify

DASHBOARD = Path("ha-configs/lovelace_3dprint.yaml")

#: Entities owned by other integrations. Nothing here is this project's to
#: publish, so each one needs a comment saying where it comes from.
#:
#: All of these belong to the Elegoo HACS integration and were read off the
#: live instance on 2026-09-14 — see `tests/test_dashboard_live.py`, which
#: re-checks them against a running HA rather than trusting this list.
EXTERNAL_ENTITIES: set[str] = {
    "camera.centauri_carbon_chamber_camera",
    "light.centauri_carbon_chamber_light",
    "sensor.centauri_carbon_print_status",
    "sensor.centauri_carbon_nozzle_temperature",
    "sensor.centauri_carbon_bed_temperature",
    "sensor.centauri_carbon_percent_complete",
    "sensor.centauri_carbon_current_layer",
    "sensor.centauri_carbon_total_layers",
    "sensor.centauri_carbon_remaining_print_time",
}

#: Card keys whose value is an entity id. `entity` covers most cards, but not
#: all of them: `picture-glance` names its feed with `camera_image`, and a
#: mutation test caught this walker silently ignoring a broken camera id
#: because of it. Any card key that takes an entity belongs here.
_ENTITY_KEYS = ("entity", "entity_id", "camera_image", "image_entity")

#: `states('sensor.x')`, `state_attr('sensor.x', 'y')`, `is_state('sensor.x', …)`
_TEMPLATE_REF = re.compile(r"(?:states|state_attr|is_state)\(\s*'([a-z_]+\.[a-z0-9_]+)'")


def _walk(node: Any) -> Any:
    """Yield every scalar and mapping in a nested YAML structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)
    else:
        yield node


def referenced_entities(doc: Any) -> set[str]:
    """Every entity id the dashboard names, from keys and from templates."""
    found: set[str] = set()
    for node in _walk(doc):
        if isinstance(node, dict):
            for key in _ENTITY_KEYS:
                value = node.get(key)
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
        elif isinstance(node, str):
            found.update(_TEMPLATE_REF.findall(node))
    return found


@pytest.fixture
def dashboard() -> Any:
    return yaml.safe_load(DASHBOARD.read_text())


def test_the_dashboard_parses(dashboard: Any) -> None:
    assert dashboard["views"], "no views"
    assert dashboard["views"][0]["cards"], "no cards"


def test_every_entity_on_the_dashboard_is_one_we_publish(dashboard: Any) -> None:
    """The check the first dashboard never had."""
    referenced = referenced_entities(dashboard)
    assert referenced, "parsed no entities at all — the walker is broken"

    unknown = referenced - set(CONTRACTS_HA) - EXTERNAL_ENTITIES
    assert not unknown, (
        f"dashboard references entities this project does not publish: "
        f"{sorted(unknown)}. Either fix the id or, if another integration owns "
        f"it, add it to EXTERNAL_ENTITIES with a note saying which."
    )


def test_the_contract_matches_what_discovery_publishes() -> None:
    """CONTRACTS_HA is only useful if it tracks the code.

    `test_entity_ids_follow_from_the_device_and_entity_names` checks the same
    thing from the discovery side; this one exists so a failure here points at
    the dashboard rather than at MQTT.
    """
    cfg = MqttConfig.from_env()
    published = set()
    for topic, payload in discovery_payloads(cfg):
        component = topic.split("/")[1]
        device = payload["device"]
        assert isinstance(device, dict)
        name = f"{device['name']} {payload['name']}"
        published.add(f"{component}.{ha_slugify(name)}")
    assert published == set(CONTRACTS_HA)


def test_the_approve_button_is_hidden_when_no_gate_is_pending(dashboard: Any) -> None:
    """AGENTS.md rule 5: a gate stops for a human.

    An Approve button that is always live trains people to press it without
    reading, and it is the one control on this dashboard that reaches a
    printer. It must sit inside a conditional card keyed on the gate.
    """
    conditionals = [
        node
        for node in _walk(dashboard)
        if isinstance(node, dict) and node.get("type") == "conditional"
    ]
    guarding = [
        c
        for c in conditionals
        if any(
            cond.get("entity") == "binary_sensor.3d_print_agent_awaiting_approval"
            and cond.get("state") == "on"
            for cond in c.get("conditions", [])
        )
    ]
    assert guarding, "no conditional card is keyed on the gate being pending"

    guarded = referenced_entities(guarding)
    for button in ("button.3d_print_agent_approve", "button.3d_print_agent_reject"):
        assert button in guarded, f"{button} is not behind the gate condition"

    # And nowhere else on the dashboard. Reject matters as much as Approve:
    # both resume a parked graph, and a Reject that is always live is a way to
    # cancel a job nobody was looking at.
    ungated = referenced_entities(
        [c for c in dashboard["views"][0]["cards"] if c not in guarding]
    )
    for button in ("button.3d_print_agent_approve", "button.3d_print_agent_reject"):
        assert button not in ungated, (
            f"{button} also appears outside the gate conditional"
        )
