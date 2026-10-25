"""Specification for the MQTT bridge and Home Assistant discovery.

HA speaks MQTT natively, so there is no custom integration to write. The
pipeline publishes retained discovery configs and HA materialises the entities
itself — no `configuration.yaml` edits, no restarts, and a new field appears the
next time we publish.

The retained `ha-configs/mqtt_entities.yaml` from the first build is *not* the
model here. It hand-declares entities with wildcard state topics
(`3dprint/+/status`), which is not how HA subscribes, and every change to it
needs a restart. Discovery replaces it; that file survives as a record of the
topic names.

Everything in this file except the `mqtt`-marked tests runs offline. The marked
ones publish to the real broker, under `MQTT_TOPIC_PREFIX` (`3dprint-dev`) and a
distinct discovery node id, so nothing here can collide with entities that
matter.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from print_shop.core.mqtt import (
    MqttConfig,
    availability_topic,
    command_topic,
    discovery_payloads,
    gate_topic,
    state_topic,
)
from print_shop.env import SETTINGS_PATH


def _env_present() -> bool:
    env = SETTINGS_PATH
    if not env.exists():
        return False
    text = env.read_text()
    return "MQTT_USER" in text and "MQTT_PASSWORD" in text


needs_broker = pytest.mark.skipif(
    not _env_present(), reason="no MQTT credentials in the Mac's settings"
)


@pytest.fixture
def cfg() -> MqttConfig:
    return MqttConfig.from_env()


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@needs_broker
def test_reads_credentials_from_env(cfg: MqttConfig) -> None:
    assert cfg.host and cfg.port
    assert cfg.user and cfg.password
    assert cfg.topic_prefix == "3dprint-dev", "dev prefix, not the live one"
    assert cfg.discovery_prefix == "homeassistant"


def test_the_password_never_appears_in_a_repr() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("hunter2"),
        topic_prefix="p", discovery_prefix="homeassistant", node_id="n",
    )
    assert "hunter2" not in repr(c)
    assert "hunter2" not in str(c)


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------


def test_topics_are_namespaced_under_the_prefix() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="n",
    )
    for t in (state_topic(c), gate_topic(c), command_topic(c), availability_topic(c)):
        assert t.startswith("3dprint-dev/"), t
    assert len({state_topic(c), gate_topic(c), command_topic(c)}) == 3


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_publishes_one_config_per_entity() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    payloads: dict[str, dict[str, Any]] = dict(discovery_payloads(c))
    components = {t.split("/")[1] for t in payloads}
    assert {"sensor", "binary_sensor", "button"} <= components

    objects = {t.split("/")[3] for t in payloads}
    assert {"status", "print_time", "filament", "awaiting_approval",
            "approve", "reject"} <= objects


def test_every_discovery_topic_has_the_shape_ha_expects() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    for topic in dict(discovery_payloads(c)):
        parts = topic.split("/")
        assert parts[0] == "homeassistant"
        assert parts[2] == "3dprint_dev"
        assert parts[4] == "config"
        assert len(parts) == 5


def test_entities_share_one_device_so_ha_groups_them() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    devices: set[tuple[str, ...]] = set()
    for _topic, payload in discovery_payloads(c):
        d = dict(payload["device"])  # type: ignore[call-overload]
        devices.add(tuple(sorted(d["identifiers"])))
        assert d.get("name")
    assert len(devices) == 1, "all entities must belong to one device"


def test_every_entity_declares_unique_id_and_availability() -> None:
    """Without unique_id HA will not let a person rename or reassign the entity.

    Without availability the dashboard shows stale values as current after the
    daemon dies, which is the difference between "waiting" and "nothing is
    running" at a gate.
    """
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    for topic, payload in discovery_payloads(c):
        assert payload.get("unique_id"), topic
        assert payload.get("availability_topic") == availability_topic(c), topic


def test_the_buttons_publish_to_the_command_topic() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    buttons: dict[str, dict[str, Any]] = {
        t.split("/")[3]: p for t, p in discovery_payloads(c) if t.split("/")[1] == "button"
    }
    assert set(buttons) == {"approve", "reject"}
    for name, payload in buttons.items():
        assert payload["command_topic"] == command_topic(c)
        assert json.loads(str(payload["payload_press"]))["decision"] == name


def test_discovery_payloads_are_json_serialisable() -> None:
    c = MqttConfig(
        host="h", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    for _topic, payload in discovery_payloads(c):
        json.dumps(payload)


# --------------------------------------------------------------------------
# Against the real broker
# --------------------------------------------------------------------------


@pytest.mark.mqtt
@needs_broker
def test_connects_and_round_trips(cfg: MqttConfig) -> None:
    from print_shop.core.mqtt import MqttBridge

    got: list[tuple[str, bytes]] = []
    with MqttBridge(cfg) as bridge:
        bridge.subscribe(f"{cfg.topic_prefix}/selftest", lambda t, p, r: got.append((t, p)))
        bridge.publish(f"{cfg.topic_prefix}/selftest", {"ok": True})
        bridge.wait_for(lambda: bool(got), timeout_s=5.0)
    assert got and json.loads(got[0][1])["ok"] is True


@pytest.mark.mqtt
@needs_broker
def test_announce_publishes_every_entity_retained(cfg: MqttConfig) -> None:
    """Retained, so HA rebuilds the entities after its own restart."""
    from print_shop.core.mqtt import MqttBridge

    seen: dict[str, bytes] = {}
    with MqttBridge(cfg) as bridge:
        bridge.announce()
        bridge.wait_for(lambda: True, timeout_s=1.0)

    with MqttBridge(cfg, client_suffix="reader") as reader:
        reader.subscribe(
            f"{cfg.discovery_prefix}/+/{cfg.node_id}/+/config",
            lambda t, p, r: seen.__setitem__(t, p),
        )
        reader.wait_for(lambda: len(seen) >= 6, timeout_s=6.0)

    assert len(seen) >= 6, f"only saw {sorted(seen)}"
    for payload in seen.values():
        assert json.loads(payload)["unique_id"]


@pytest.mark.mqtt
@needs_broker
def test_availability_goes_offline_when_the_daemon_dies(cfg: MqttConfig) -> None:
    """The broker publishes the LWT on an ungraceful disconnect.

    A dashboard that shows "awaiting approval" after the process has died is
    worse than one that shows nothing.
    """
    from print_shop.core.mqtt import MqttBridge

    seen: list[bytes] = []
    with MqttBridge(cfg, client_suffix="watcher") as watcher:
        watcher.subscribe(availability_topic(cfg), lambda t, p, r: seen.append(p))
        # Simulates the daemon, so it must own availability — only the owner
        # sets a Last Will.
        with MqttBridge(cfg, client_suffix="victim", owns_availability=True) as victim:
            victim.publish_availability(online=True)
            watcher.wait_for(lambda: b"online" in seen, timeout_s=5.0)
            victim.kill_without_disconnecting()
        watcher.wait_for(lambda: b"offline" in seen, timeout_s=15.0)

    assert b"online" in seen and b"offline" in seen


@pytest.mark.mqtt
@needs_broker
def test_cleanup_removes_the_entities(cfg: MqttConfig) -> None:
    """An empty retained payload tells HA to delete the entity.

    Without this, every experiment leaves permanent orphans in someone's home.
    """
    from print_shop.core.mqtt import MqttBridge

    with MqttBridge(cfg) as bridge:
        bridge.announce()
        bridge.wait_for(lambda: True, timeout_s=1.0)
        bridge.retract()
        bridge.wait_for(lambda: True, timeout_s=1.0)

    seen: dict[str, bytes] = {}
    with MqttBridge(cfg, client_suffix="reader2") as reader:
        reader.subscribe(
            f"{cfg.discovery_prefix}/+/{cfg.node_id}/+/config",
            lambda t, p, r: seen.__setitem__(t, p),
        )
        reader.wait_for(lambda: False, timeout_s=3.0)

    assert all(p == b"" for p in seen.values()), "retained configs were not cleared"

    # Put them back. An empty retained payload is HA's signal to *delete* the
    # entity, so without this a test run silently wipes the real dashboard and
    # leaves every card showing "Entity not available" until the daemon next
    # starts. It also made `tests/test_dashboard_live.py` pass or fail on
    # collection order, which is the kind of test that teaches people to
    # re-run until green.
    with MqttBridge(cfg, client_suffix="restore") as bridge:
        bridge.announce()
        bridge.wait_for(lambda: False, timeout_s=1.0)

    # And check it landed. `wait_for(lambda: False, ...)` is a sleep, not a
    # confirmation: if the restore publish were dropped, this test would still
    # pass and the only symptom would be a dead dashboard in someone's house.
    restored: dict[str, bytes] = {}
    with MqttBridge(cfg, client_suffix="restore-check") as reader:
        reader.subscribe(
            f"{cfg.discovery_prefix}/+/{cfg.node_id}/+/config",
            lambda t, p, r: restored.__setitem__(t, p),
        )
        reader.wait_for(lambda: len(restored) >= 7, timeout_s=10.0)

    assert len(restored) >= 7, f"restore announced only {sorted(restored)}"
    assert all(p for p in restored.values()), (
        "a discovery config is still empty after restore — HA will treat it as "
        "a deletion and the dashboard will stay blank"
    )


def test_env_file_is_gitignored() -> None:
    assert ".env" in Path(".gitignore").read_text()
    assert not os.environ.get("MQTT_PASSWORD"), "credentials must not leak into the env"


@pytest.mark.mqtt
@needs_broker
def test_two_subscriptions_each_receive_only_their_own_topic(cfg: MqttConfig) -> None:
    """Registering a second handler must not steal the first one's messages.

    `subscribe()` assigned `client.on_message` directly, so the last handler
    registered received *every* message on *every* topic. Nothing caught it
    because no test had ever subscribed to two topics at once — which is
    exactly what the daemon does.
    """
    from print_shop.core.mqtt import MqttBridge

    a: list[bytes] = []
    b: list[bytes] = []
    with MqttBridge(cfg, client_suffix="fanout") as bridge:
        bridge.subscribe(f"{cfg.topic_prefix}/fan/a", lambda t, p, r: a.append(p))
        bridge.subscribe(f"{cfg.topic_prefix}/fan/b", lambda t, p, r: b.append(p))
        bridge.wait_for(lambda: False, timeout_s=0.5)
        bridge.publish(f"{cfg.topic_prefix}/fan/a", {"which": "a"})
        bridge.publish(f"{cfg.topic_prefix}/fan/b", {"which": "b"})
        bridge.wait_for(lambda: bool(a) and bool(b), timeout_s=6.0)

    assert len(a) == 1 and json.loads(a[0])["which"] == "a"
    assert len(b) == 1 and json.loads(b[0])["which"] == "b"


@pytest.mark.mqtt
@needs_broker
def test_a_wildcard_subscription_still_matches(cfg: MqttConfig) -> None:
    from print_shop.core.mqtt import MqttBridge

    got: list[str] = []
    with MqttBridge(cfg, client_suffix="wild") as bridge:
        bridge.subscribe(f"{cfg.topic_prefix}/wild/+", lambda t, p, r: got.append(t))
        bridge.wait_for(lambda: False, timeout_s=0.5)
        bridge.publish(f"{cfg.topic_prefix}/wild/one", {"n": 1})
        bridge.publish(f"{cfg.topic_prefix}/wild/two", {"n": 2})
        bridge.wait_for(lambda: len(got) >= 2, timeout_s=6.0)

    assert sorted(got) == [f"{cfg.topic_prefix}/wild/one", f"{cfg.topic_prefix}/wild/two"]


@pytest.mark.mqtt
@needs_broker
def test_only_the_owner_publishes_availability(cfg: MqttConfig) -> None:
    """A read-only client must not claim the daemon is alive.

    `_on_connect` published `online` retained for *every* bridge, so any probe,
    test reader or one-off script announced the daemon as available. After a
    real daemon died and its Last Will fired, the next client to connect flipped
    availability back to `online` — leaving Home Assistant showing a live device
    and a working Approve button attached to nothing.

    Availability belongs to whoever owns the lifecycle, not to whoever connects.
    """
    from print_shop.core.mqtt import MqttBridge

    seen: list[str] = []
    with MqttBridge(cfg, client_suffix="owner-watch") as watch:
        watch.subscribe(availability_topic(cfg), lambda t, p, r: seen.append(p.decode()))
        watch.wait_for(lambda: bool(seen), timeout_s=4.0)

        with MqttBridge(cfg, client_suffix="owner") as owner:
            owner.publish_availability(online=False)
            watch.wait_for(lambda: seen[-1] == "offline", timeout_s=5.0)
        assert seen[-1] == "offline"

        # A plain bridge connecting must not resurrect it.
        with MqttBridge(cfg, client_suffix="bystander"):
            watch.wait_for(lambda: seen[-1] != "offline", timeout_s=3.0)

    assert seen[-1] == "offline", f"a bystander changed availability: {seen}"


@pytest.mark.mqtt
@needs_broker
def test_handlers_are_told_whether_a_message_was_retained(cfg: MqttConfig) -> None:
    """The retain flag must reach the handler, or a guard on it is theatre.

    `Daemon._on_command` documented that it ignores retained commands so a
    stale approval cannot apply to a job nobody has seen. It took
    ``retain: bool = False`` and `_on_message` called it with two arguments,
    so the flag was always False and the guard never fired once — a retained
    approve replayed on reconnect would have walked through an approval gate
    (AGENTS.md rule 5).

    Publish retained *before* subscribing, so the broker replays it on
    subscribe exactly as it would after a reconnect.
    """
    from print_shop.core.mqtt import MqttBridge

    topic = f"{cfg.topic_prefix}/retaintest"
    seen: list[tuple[bytes, bool]] = []

    with MqttBridge(cfg, client_suffix="retain-writer") as writer:
        writer.publish(topic, {"n": "stale"}, retain=True)
        writer.wait_for(lambda: False, timeout_s=0.5)

    try:
        with MqttBridge(cfg, client_suffix="retain-reader") as reader:
            reader.subscribe(topic, lambda t, p, r: seen.append((p, r)))
            # The replayed retained message arrives on subscribe.
            reader.wait_for(lambda: bool(seen), timeout_s=5.0)
            reader.publish(topic, {"n": "live"}, retain=False)
            reader.wait_for(lambda: len(seen) >= 2, timeout_s=5.0)
    finally:
        with MqttBridge(cfg, client_suffix="retain-cleanup") as cleanup:
            cleanup.publish(topic, "", retain=True)
            cleanup.wait_for(lambda: False, timeout_s=0.5)

    by_name = {json.loads(p)["n"]: r for p, r in seen if p}
    assert by_name.get("stale") is True, "replayed retained message was not flagged"
    assert by_name.get("live") is False, "a live message was flagged as retained"


# --------------------------------------------------------------------------
# Entity ids and value templates
#
# These are the contract the Lovelace dashboard is written against. The first
# build's dashboard guessed the ids, every card silently bound to nothing, and
# the file now carries a REFERENCE ONLY header. Pinning them here means a
# rename breaks a test instead of a dashboard.
# --------------------------------------------------------------------------


#: The entity ids Home Assistant actually creates, verified live against HA
#: 2026.8.3 on 2026-09-14 by announcing and reading `/api/states` back.
#:
#: These are derived by HA from the names, not chosen by us. An `object_id` in
#: the discovery payload does NOT override them — that was tried and measured:
#: a probe entity published with `object_id: "3dprint_dev_probe"` under device
#: name "3D Print Probe" came out as `sensor.3d_print_probe_probe`.
CONTRACTS_HA = {
    "sensor.3d_print_agent_status": "sensor/3dprint_dev/status",
    "sensor.3d_print_agent_orientation": "sensor/3dprint_dev/orientation",
    "sensor.3d_print_agent_print_time": "sensor/3dprint_dev/print_time",
    "sensor.3d_print_agent_filament": "sensor/3dprint_dev/filament",
    "binary_sensor.3d_print_agent_awaiting_approval": (
        "binary_sensor/3dprint_dev/awaiting_approval"
    ),
    "button.3d_print_agent_approve": "button/3dprint_dev/approve",
    "button.3d_print_agent_reject": "button/3dprint_dev/reject",
}


def ha_slugify(text: str) -> str:
    """HA's entity-id slug: lowercase, non-alphanumerics collapsed to `_`.

    Enough of `homeassistant.util.slugify` for the names this project uses.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def test_entity_ids_follow_from_the_device_and_entity_names(cfg: MqttConfig) -> None:
    """The names are the contract, because HA builds the id out of them.

    Rename the device from "3D Print Agent", or an entity from "Print Time",
    and every card pointing at the old id silently stops resolving. This
    recomputes the ids the way HA does so that rename fails here first.
    """
    payloads = {t: p for t, p in discovery_payloads(cfg)}
    assert len(payloads) == len(CONTRACTS_HA)

    got = {}
    for topic, payload in payloads.items():
        component = topic.split("/")[1]
        device = payload["device"]
        assert isinstance(device, dict)
        entity_id = f"{component}.{ha_slugify(str(device['name']) + ' ' + str(payload['name']))}"
        got[entity_id] = "/".join(topic.split("/")[1:4])

    assert got == CONTRACTS_HA


def test_object_id_is_not_used_to_pin_entity_ids(cfg: MqttConfig) -> None:
    """A guard against re-adding a key that looks like it works and does not.

    HA 2026.8.3 ignores `object_id` in MQTT discovery. Setting it again would
    imply the ids are pinned when they are still name-derived, which is the
    more dangerous of the two states — it reads as a guarantee.
    """
    for _, payload in discovery_payloads(cfg):
        assert "object_id" not in payload, (
            "object_id is ignored by HA; the entity id comes from the names. "
            "See discovery_payloads()."
        )


def _render(template: str, payload: dict[str, Any]) -> str:
    """Render a discovery value_template the way HA would."""
    from jinja2 import Environment

    env = Environment(autoescape=False)
    return env.from_string(template).render(value_json=payload)


@pytest.mark.parametrize(
    "orientation_deg,expected",
    [
        ([180.0, 0.0, 0.0], "180.0° / 0.0° / 0.0°"),
        ([0.0, 0.0, 0.0], "0.0° / 0.0° / 0.0°"),
        ([90.5, -45.25, 12.0], "90.5° / -45.2° / 12.0°"),
        (None, "—"),
    ],
)
def test_orientation_renders_as_a_readable_triple(
    orientation_deg: Any, expected: str
) -> None:
    """The state topic carries an Euler XYZ triple, and a card must show it.

    This sensor declared `unit_of_measurement: "°"` and a bare
    `{{ value_json.orientation_deg }}`, so HA was told to expect a number and
    handed `[180.0, 0.0, 0.0]`. The result is a non-numeric-value warning in
    the log and brackets on the dashboard.

    The `[0.0, 0.0, 0.0]` case is the one that matters most: "no rotation" is a
    real answer and must not render as the "no job yet" em dash.
    """
    from print_shop.core.mqtt import ORIENTATION_TEMPLATE

    payload = json.loads(
        state_payload_for_test(orientation_deg)
    )
    assert _render(ORIENTATION_TEMPLATE, payload) == expected


def state_payload_for_test(orientation_deg: Any) -> str:
    """The exact payload shape the daemon publishes."""
    from print_shop.orchestrator.daemon import state_payload

    return state_payload(
        {
            "status": "awaiting_approval",
            "orientation_deg": orientation_deg,
            "print_time_s": 850,
            "filament_cm3": 2.4,
        }
    )


def test_the_orientation_sensor_no_longer_claims_to_be_numeric(
    cfg: MqttConfig,
) -> None:
    """A unit on a text state is what produced the warning in the first place."""
    payload = dict(discovery_payloads(cfg))[
        f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/orientation/config"
    ]
    assert "unit_of_measurement" not in payload
    assert "device_class" not in payload
    assert "state_class" not in payload
