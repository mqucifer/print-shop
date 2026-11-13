"""The agent's availability in Home Assistant stays honest across runs (mqucifer/print-shop#107).

On the first real prints Home Assistant showed the agent unavailable at the
gate: the client id was fixed, so a new run took over a stopped run's session
that was still open, the broker published that session's will ("offline",
retained), and nothing said "online" again until a later restart.

These run `MqttBridge` itself against an in-memory broker that keeps the
rules that matter here: retained messages, a will published when a
connection drops or its session is taken over by the same client id, and a
subscription forgotten with a clean session. No real broker is touched.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt
import pytest
from pydantic import SecretStr

from print_shop.core.mqtt import MqttBridge, MqttConfig, availability_topic

CFG = MqttConfig(
    host="broker.invalid", port=1883, user="u", password=SecretStr("p"),
    topic_prefix="3dprint-test", discovery_prefix="homeassistant", node_id="3dprint_test",
)
AVAILABILITY = availability_topic(CFG)


@dataclass
class _Message:
    topic: str
    payload: bytes
    retain: bool


@dataclass
class Broker:
    """Retained messages, wills, takeover by client id, clean sessions."""

    retained: dict[str, bytes] = field(default_factory=dict)
    connected: dict[str, FakeClient] = field(default_factory=dict)
    #: Every publish to the availability topic, in order, as the broker applied it.
    availability: list[str] = field(default_factory=list)

    def connect(self, client: FakeClient) -> None:
        old = self.connected.get(client.client_id)
        if old is not None:
            self.drop(old)  # a session taken over: the old one's will is published
        client.subscriptions = []  # a clean session
        self.connected[client.client_id] = client
        client.on_connect(client, None, None, 0, None)

    def drop(self, client: FakeClient) -> None:
        """The connection ends without a DISCONNECT: the broker publishes its will."""
        if self.connected.get(client.client_id) is client:
            del self.connected[client.client_id]
            if client.will is not None:
                self.publish(*client.will)

    def disconnect(self, client: FakeClient) -> None:
        if self.connected.get(client.client_id) is client:
            del self.connected[client.client_id]

    def publish(self, topic: str, payload: bytes, retain: bool) -> None:
        if topic == AVAILABILITY:
            self.availability.append(payload.decode())
        if retain:
            self.retained[topic] = payload
        for client in list(self.connected.values()):
            if any(mqtt.topic_matches_sub(s, topic) for s in client.subscriptions):
                client.on_message(client, None, _Message(topic, payload, False))

    def subscribe(self, client: FakeClient, topic: str) -> None:
        client.subscriptions.append(topic)
        for t, payload in list(self.retained.items()):
            if mqtt.topic_matches_sub(topic, t):
                client.on_message(client, None, _Message(t, payload, True))


class FakeClient:
    """The slice of paho's `Client` that `MqttBridge` uses, on a `Broker`."""

    broker: Broker
    made: list[FakeClient]

    def __init__(self, callback_api_version: Any = None, client_id: str = "") -> None:
        self.client_id = client_id
        self.will: tuple[str, bytes, bool] | None = None
        self.subscriptions: list[str] = []
        self.on_connect: Callable[..., None] = lambda *a: None
        self.on_message: Callable[..., None] = lambda *a: None
        self._sock = None
        self.made.append(self)

    def username_pw_set(self, user: str, password: str) -> None:
        pass

    def will_set(self, topic: str, payload: str, retain: bool = False, **_: Any) -> None:
        self.will = (topic, payload.encode(), retain)

    def connect(self, host: str, port: int) -> None:
        self.broker.connect(self)

    def reconnect(self) -> None:
        """What paho's network loop does after a dropped connection."""
        self.broker.connect(self)

    def loop_start(self) -> None:
        pass

    def loop_stop(self) -> None:
        pass

    def disconnect(self) -> None:
        self.broker.disconnect(self)

    def publish(self, topic: str, payload: str | bytes = b"", retain: bool = False,
                qos: int = 0) -> None:
        if self.broker.connected.get(self.client_id) is self:
            data = payload.encode() if isinstance(payload, str) else payload
            self.broker.publish(topic, data, retain)

    def subscribe(self, topic: str, qos: int = 0) -> None:
        if self.broker.connected.get(self.client_id) is self:
            self.broker.subscribe(self, topic)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> Iterator[Broker]:
    broker = Broker()
    client = type("Client", (FakeClient,), {"broker": broker, "made": []})
    monkeypatch.setattr(mqtt, "Client", client)  # the module the bridge makes its client from
    yield broker


def _client(bridge: MqttBridge) -> FakeClient:
    """The bridge's client, which the fixture made a `FakeClient`."""
    client: Any = bridge._client
    assert isinstance(client, FakeClient)
    return client


def test_each_run_connects_with_its_own_client_id(broker: Broker) -> None:
    """So a new run never takes over an old run's session, and never fires its will."""
    with MqttBridge(CFG, owns_availability=True) as old:
        old.publish_availability(online=True)
        with MqttBridge(CFG, owns_availability=True) as new:
            new.publish_availability(online=True)
            assert old.client_id != new.client_id
            assert old.client_id.startswith("3dprint-3dprint_test-")
            assert len(broker.connected) == 2, "neither took the other's session over"
    assert "offline" not in broker.availability[:2]


def test_a_run_still_stopping_when_the_next_starts_leaves_it_online(broker: Broker) -> None:
    """The night's fault, as paho's loop makes it: each reconnect takes the other's session.

    With one client id for every run, each takeover published the other's will,
    and the run left standing never said "online" again.
    """
    old = MqttBridge(CFG, owns_availability=True).__enter__()
    old.publish_availability(online=True)
    with MqttBridge(CFG, owns_availability=True) as new:
        new.publish_availability(online=True)
        _client(old).reconnect()  # paho's loop, its session having been taken over
        _client(new).reconnect()  # and the new run's, likewise
        old.publish_availability(online=False)  # the old run finishes stopping
        old.__exit__(None, None, None)
        assert broker.retained[AVAILABILITY] == b"online"


def test_a_stopped_runs_late_will_is_answered_with_online(broker: Broker) -> None:
    """The night's fault: the old session's "offline" arrives after the new run's "online"."""
    old = MqttBridge(CFG, owns_availability=True).__enter__()
    old.publish_availability(online=True)
    # The old run is stopped, but its connection lingers until the broker times it out.
    with MqttBridge(CFG, owns_availability=True) as new:
        new.publish_availability(online=True)
        broker.drop(_client(old))  # its will, retained, after the new run's online
        assert broker.availability[-2:] == ["offline", "online"]
        assert broker.retained[AVAILABILITY] == b"online"


def test_a_dropped_connection_says_online_again_when_it_reconnects(broker: Broker) -> None:
    """Its own will fired while it was down; the reconnect corrects it, and resubscribes."""
    got: list[bytes] = []
    with MqttBridge(CFG, owns_availability=True) as run:
        run.publish_availability(online=True)
        run.subscribe(f"{CFG.topic_prefix}/command", lambda t, p, r: got.append(p))
        client = _client(run)
        broker.drop(client)
        assert broker.retained[AVAILABILITY] == b"offline"
        client.reconnect()
        assert broker.retained[AVAILABILITY] == b"online"
        broker.publish(f"{CFG.topic_prefix}/command", b"approve", retain=False)
        assert got == [b"approve"], "the command subscription survives the reconnect"


def test_a_run_that_says_offline_isnt_contradicted_by_itself(broker: Broker) -> None:
    with MqttBridge(CFG, owns_availability=True) as run:
        run.publish_availability(online=True)
        run.publish_availability(online=False)
    assert broker.availability == ["online", "offline"]
    assert broker.retained[AVAILABILITY] == b"offline"


def test_a_stale_offline_retained_at_start_is_left_until_the_run_says_online(
    broker: Broker,
) -> None:
    broker.retained[AVAILABILITY] = b"offline"
    with MqttBridge(CFG, owns_availability=True) as run:
        assert broker.availability == [], "not online yet, so nothing to say"
        run.publish_availability(online=True)
    assert broker.availability[0] == "online"


def test_only_the_owner_speaks_for_availability(broker: Broker) -> None:
    with MqttBridge(CFG, owns_availability=True) as run:
        run.publish_availability(online=True)
        with MqttBridge(CFG, client_suffix="probe") as probe:
            assert _client(probe).will is None
            broker.drop(_client(probe))
            broker.publish(AVAILABILITY, b"offline", retain=True)  # someone else's
        assert broker.retained[AVAILABILITY] == b"online", "the owner answered it"
        assert broker.availability.count("online") == 2
