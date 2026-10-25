"""MQTT bridge and Home Assistant discovery for the print pipeline.

Publishes retained discovery configs so HA materialises entities without a
restart, and manages the availability LWT so a dead daemon never shows stale
state on the dashboard.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import BaseModel, SecretStr

from print_shop.env import SETTINGS_PATH, load_env

logger = logging.getLogger(__name__)

#: A subscription callback: ``(topic, payload, retain)``.
#:
#: The retain flag is part of the contract rather than an optional extra. See
#: `MqttBridge.subscribe`.
MessageHandler = Callable[[str, bytes, bool], None]

__all__ = [
    "ORIENTATION_TEMPLATE",
    "Bridge",
    "MessageHandler",
    "MqttConfig",
    "MqttBridge",
    "state_topic",
    "gate_topic",
    "command_topic",
    "availability_topic",
    "discovery_payloads",
    "outcome_note_topic",
    "outcome_result_topic",
    "outcome_submit_topic",
    "outcome_symptom_topic",
    "outcome_topic",
]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


class MqttConfig(BaseModel):
    host: str
    port: int
    user: str
    password: SecretStr
    topic_prefix: str
    discovery_prefix: str
    node_id: str

    @classmethod
    def from_env(cls, path: Path | None = None) -> MqttConfig:
        """Read the Mac's settings (`~/.config/print-shop/.env`) with `load_env`.

        A value already exported in the environment wins, as in a shell. The
        file is parsed, never sourced, and nothing is exported.
        """
        env = load_env(path or SETTINGS_PATH)

        def get(key: str, default: str = "") -> str:
            return env.get(key, default)

        return cls(
            host=get("MQTT_HOST", "localhost"),
            port=int(get("MQTT_PORT", "1883")),
            user=get("MQTT_USER"),
            password=SecretStr(get("MQTT_PASSWORD")),
            topic_prefix=get("MQTT_TOPIC_PREFIX", "3dprint-dev"),
            discovery_prefix=get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
            node_id=get("MQTT_NODE_ID", "3dprint_dev"),
        )


# --------------------------------------------------------------------------
# Topic helpers
# --------------------------------------------------------------------------


def state_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/state"


def gate_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/gate"


def command_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/command"


def availability_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/availability"


def outcome_topic(cfg: MqttConfig) -> str:
    """The outcome gate: the print on show, the draft answer, any error. Retained."""
    return f"{cfg.topic_prefix}/outcome"


def outcome_result_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/outcome/set/result"


def outcome_symptom_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/outcome/set/symptom"


def outcome_note_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/outcome/set/note"


def outcome_submit_topic(cfg: MqttConfig) -> str:
    return f"{cfg.topic_prefix}/outcome/submit"


# --------------------------------------------------------------------------
# Discovery payloads
# --------------------------------------------------------------------------


#: Renders the Euler XYZ triple as "180° / 0° / 0°", or an em dash when no job
#: has set one. A bare `{{ value_json.orientation_deg }}` renders the Python
#: repr of the list, brackets and all.
ORIENTATION_TEMPLATE = (
    "{% set o = value_json.orientation_deg %}"
    "{% if o is sequence and o is not string and o | length == 3 %}"
    "{{ o[0] | round(1) }}° / {{ o[1] | round(1) }}° / {{ o[2] | round(1) }}°"
    "{% else %}—{% endif %}"
)


#: The outcome selects' options. "unset" is the empty choice; the rest are the
#: ledger's own `Result` and `Symptom` values, which `tests/test_outcome.py`
#: checks against these.
OUTCOME_RESULTS = ("unset", "success", "failed")
OUTCOME_SYMPTOMS = (
    "unset",
    "none",
    "adhesion",
    "warping",
    "stringing",
    "layer_shift",
    "spaghetti",
    "under_extrusion",
    "other",
)

#: The part on show, with any refusal the dashboard should see.
OUTCOME_PART_TEMPLATE = (
    "{% if value_json.pending %}{{ value_json.stl_name }}"
    "{% if value_json.error %} — {{ value_json.error[:150] }}{% endif %}"
    "{% else %}—{% endif %}"
)


def discovery_payloads(cfg: MqttConfig) -> Iterable[tuple[str, dict[str, object]]]:
    """Yield ``(topic, payload)`` pairs for every HA entity.

    Topic shape: ``{discovery_prefix}/{component}/{node_id}/{object}/config``.
    All entities share one ``device`` block so HA groups them into a single card.

    **The entity id is derived from the names, so the names are the contract.**
    HA builds it as ``{component}.{slugify(device name + " " + entity name)}``:
    device "3D Print Agent" plus entity "Print Time" gives
    ``sensor.3d_print_agent_print_time``. Renaming either one renames the
    entity and silently unbinds every dashboard card pointing at it.

    A discovery ``object_id`` key was tried here as a way to pin the ids
    explicitly. **It is ignored.** Measured against Home Assistant 2026.8.3:
    an entity published with ``object_id: "3dprint_dev_probe"`` and device
    name "3D Print Probe" materialised as ``sensor.3d_print_probe_probe`` —
    the derived name, not the requested one. Do not re-add it believing it
    pins anything.

    `CONTRACTS_HA` in `tests/test_mqtt.py` holds the resulting ids, and a test
    recomputes them from the names so a rename fails there instead of on a
    dashboard.
    """
    device = {
        "identifiers": [cfg.node_id],
        "name": "3D Print Agent",
    }
    avail = availability_topic(cfg)

    entities: list[tuple[str, dict[str, object]]] = [
        (
            f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/status/config",
            {
                "name": "Status",
                "unique_id": f"{cfg.node_id}_status",
                "device_class": None,
                "state_topic": state_topic(cfg),
                "value_template": "{{ value_json.status }}",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/orientation/config",
            {
                "name": "Orientation",
                "unique_id": f"{cfg.node_id}_orientation",
                "state_topic": state_topic(cfg),
                # orientation_deg is an Euler XYZ triple, not a scalar. This
                # carried `unit_of_measurement: "°"`, which tells HA to expect
                # a number, and then fed it "[180.0, 0.0, 0.0]" — HA logs a
                # non-numeric-value warning and the card shows the brackets.
                # Formatted as text here; the unit is in the string.
                "value_template": ORIENTATION_TEMPLATE,
                "icon": "mdi:rotate-3d-variant",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/print_time/config",
            {
                "name": "Print Time",
                "unique_id": f"{cfg.node_id}_print_time",
                "state_topic": state_topic(cfg),
                "value_template": "{{ value_json.print_time_s }}",
                "unit_of_measurement": "s",
                "device_class": "duration",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/filament/config",
            {
                "name": "Filament",
                "unique_id": f"{cfg.node_id}_filament",
                "state_topic": state_topic(cfg),
                "value_template": "{{ value_json.filament_cm3 }}",
                "unit_of_measurement": "cm³",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/binary_sensor/{cfg.node_id}/awaiting_approval/config",
            {
                "name": "Awaiting Approval",
                "unique_id": f"{cfg.node_id}_awaiting_approval",
                "device_class": "opening",
                "state_topic": gate_topic(cfg),
                "value_template": "{{ 'ON' if value_json.pending else 'OFF' }}",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/button/{cfg.node_id}/approve/config",
            {
                "name": "Approve",
                "unique_id": f"{cfg.node_id}_approve",
                "command_topic": command_topic(cfg),
                "payload_press": json.dumps({"decision": "approve"}),
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/button/{cfg.node_id}/reject/config",
            {
                "name": "Reject",
                "unique_id": f"{cfg.node_id}_reject",
                "command_topic": command_topic(cfg),
                "payload_press": json.dumps({"decision": "reject"}),
                "availability_topic": avail,
                "device": device,
            },
        ),
        # The outcome gate (mqucifer/print-shop#2): when a print ends, the
        # Sponsor says how it came out. The daemon holds the draft; Record
        # commits it to the ledger. See `print_shop.orchestrator.outcome`.
        (
            f"{cfg.discovery_prefix}/binary_sensor/{cfg.node_id}/awaiting_outcome/config",
            {
                "name": "Awaiting Outcome",
                "unique_id": f"{cfg.node_id}_awaiting_outcome",
                "state_topic": outcome_topic(cfg),
                "value_template": "{{ 'ON' if value_json.pending else 'OFF' }}",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/sensor/{cfg.node_id}/outcome_part/config",
            {
                "name": "Outcome Part",
                "unique_id": f"{cfg.node_id}_outcome_part",
                "state_topic": outcome_topic(cfg),
                "value_template": OUTCOME_PART_TEMPLATE,
                "json_attributes_topic": outcome_topic(cfg),
                "icon": "mdi:printer-3d-nozzle-alert-outline",
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/select/{cfg.node_id}/outcome_result/config",
            {
                "name": "Outcome Result",
                "unique_id": f"{cfg.node_id}_outcome_result",
                "command_topic": outcome_result_topic(cfg),
                "state_topic": outcome_topic(cfg),
                "value_template": "{{ value_json.result | default('unset') }}",
                "options": list(OUTCOME_RESULTS),
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/select/{cfg.node_id}/outcome_symptom/config",
            {
                "name": "Outcome Symptom",
                "unique_id": f"{cfg.node_id}_outcome_symptom",
                "command_topic": outcome_symptom_topic(cfg),
                "state_topic": outcome_topic(cfg),
                "value_template": "{{ value_json.symptom | default('unset') }}",
                "options": list(OUTCOME_SYMPTOMS),
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/text/{cfg.node_id}/outcome_note/config",
            {
                "name": "Outcome Note",
                "unique_id": f"{cfg.node_id}_outcome_note",
                "command_topic": outcome_note_topic(cfg),
                "state_topic": outcome_topic(cfg),
                "value_template": "{{ value_json.note | default('') }}",
                "max": 255,
                "availability_topic": avail,
                "device": device,
            },
        ),
        (
            f"{cfg.discovery_prefix}/button/{cfg.node_id}/record_outcome/config",
            {
                "name": "Record Outcome",
                "unique_id": f"{cfg.node_id}_record_outcome",
                "command_topic": outcome_submit_topic(cfg),
                "payload_press": "record",
                "availability_topic": avail,
                "device": device,
            },
        ),
    ]

    yield from entities


# --------------------------------------------------------------------------
# Bridge
# --------------------------------------------------------------------------


class Bridge(Protocol):
    """What the daemon needs from a broker connection. `MqttBridge` is the real one.

    A seam so the whole job can run in CI against an in-memory broker
    (`tests/fakes.py`), with the daemon's own gate logic unchanged.
    """

    def __enter__(self) -> Bridge: ...

    def __exit__(self, *exc: object) -> None: ...

    def announce(self) -> None: ...

    def publish_availability(self, online: bool) -> None: ...

    def publish(
        self, topic: str, payload: dict[str, object] | str, retain: bool = False, qos: int = 1
    ) -> None: ...

    def subscribe(self, topic: str, handler: MessageHandler) -> None: ...


class MqttBridge:
    """Context manager over a paho-mqtt client.

    Usage::

        with MqttBridge(cfg) as bridge:
            bridge.announce()
            bridge.publish(state_topic(cfg), {"status": "printing"})
    """

    def __init__(
        self,
        cfg: MqttConfig,
        client_suffix: str = "",
        owns_availability: bool = False,
    ) -> None:
        self._cfg = cfg
        client_id = f"3dprint-{cfg.node_id}"
        if client_suffix:
            client_id += f"-{client_suffix}"

        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        self._client.username_pw_set(cfg.user, cfg.password.get_secret_value())
        # Only the lifecycle owner speaks for availability. A read-only probe
        # that set a will would mark a healthy daemon offline when it
        # disconnected; one that published "online" on connect would resurrect a
        # dead one. Both happened before `owns_availability` existed.
        if owns_availability:
            self._client.will_set(
                availability_topic(cfg),
                payload="offline",
                retain=True,
            )
        self._connected = False
        self._subscriptions: list[tuple[str, MessageHandler]] = []
        self._client.on_message = self._on_message

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> MqttBridge:
        self._client.on_connect = self._on_connect
        self._client.connect(self._cfg.host, self._cfg.port)
        self._client.loop_start()
        # Wait for the connection to be established.
        deadline = time.monotonic() + 10.0
        while not self._connected and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self._connected:
            raise ConnectionError(
                f"Could not connect to MQTT broker at {self._cfg.host}:{self._cfg.port}"
            )
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    # -- callbacks ----------------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: mqtt.ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code == 0:
            self._connected = True
            # Availability is published explicitly by the owner, never here.
            # See `owns_availability`.
        else:
            raise ConnectionError(f"MQTT connect failed: {reason_code}")

    # -- public API ---------------------------------------------------------

    def publish(
        self,
        topic: str,
        payload: dict[str, object] | str,
        retain: bool = False,
        qos: int = 1,
    ) -> None:
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        self._client.publish(topic, payload=payload, retain=retain, qos=qos)

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: object,
        msg: mqtt.MQTTMessage,
    ) -> None:
        for topic_filter, handler in self._subscriptions:
            if mqtt.topic_matches_sub(topic_filter, msg.topic):
                try:
                    handler(msg.topic, msg.payload, msg.retain)
                except Exception:
                    logger.exception(
                        "Handler for topic filter %r raised on topic %s",
                        topic_filter,
                        msg.topic,
                    )

    def subscribe(
        self,
        topic: str,
        handler: MessageHandler,
    ) -> None:
        """Register ``handler`` for ``topic``.

        The handler's third argument is the MQTT retain flag. It is not
        optional: a retained message is one the broker replayed from before the
        subscriber existed, and a handler that cannot tell the difference will
        eventually act on a stale one. `Daemon._on_command` had exactly that
        bug — it declared ``retain: bool = False`` and was never passed
        anything else, so its guard against replayed approvals never fired.
        """
        self._client.subscribe(topic, qos=1)
        self._subscriptions.append((topic, handler))

    def wait_for(self, predicate: Callable[[], bool], timeout_s: float) -> bool:
        """Poll until ``predicate()`` is true or ``timeout_s`` elapses.

        Returns True if the predicate became true, False on timeout.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def publish_availability(self, online: bool) -> None:
        payload = "online" if online else "offline"
        self._client.publish(
            availability_topic(self._cfg),
            payload=payload,
            retain=True,
        )

    def announce(self) -> None:
        """Publish every discovery payload, retained."""
        for topic, payload in discovery_payloads(self._cfg):
            self._client.publish(
                topic,
                payload=json.dumps(payload),
                retain=True,
                qos=1,
            )

    def retract(self) -> None:
        """Publish an empty retained payload to each discovery topic.

        HA interprets an empty retained payload as "delete this entity."
        """
        for topic, _ in discovery_payloads(self._cfg):
            self._client.publish(topic, payload="", retain=True, qos=1)

    def kill_without_disconnecting(self) -> None:
        """Stop the network loop and close the socket without DISCONNECT.

        This causes the broker to fire the Last Will, which publishes
        ``offline`` on the availability topic.
        """
        self._client.loop_stop()
        # Close the underlying socket directly to avoid sending DISCONNECT.
        sock = self._client._sock
        if sock is not None:
            sock.close()
        self._connected = False
