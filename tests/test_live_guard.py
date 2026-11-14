"""No test reaches the broker, LiteLLM or Home Assistant unless its run opts in.

mqucifer/print-shop#115. The guard is `tests/conftest.py`'s, as is the one that disarms the
printer. None of these opens a connection: each is refused before anything leaves the process.
"""

from __future__ import annotations

import httpx
import paho.mqtt.client as paho
import pytest
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import SecretStr

from print_shop.core.mqtt import MqttBridge, MqttConfig
from tests.conftest import LiveServiceRefused
from tests.live import OPT_IN, RUN, broker_config


@pytest.fixture
def not_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in OPT_IN.values():
        monkeypatch.delenv(var, raising=False)


def test_a_broker_connect_is_refused_unless_opted_in(not_opted_in: None) -> None:
    with pytest.raises(LiveServiceRefused, match="PRINT_SHOP_TEST_BROKER"):
        paho.Client(CallbackAPIVersion.VERSION2).connect("broker.invalid", 1883)
    with pytest.raises(LiveServiceRefused):
        paho.Client(CallbackAPIVersion.VERSION2).connect_async("broker.invalid", 1883)


def test_a_bridge_as_the_live_device_is_refused_even_when_opted_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRINT_SHOP_TEST_BROKER", "broker.invalid")
    live = MqttConfig(
        host="broker.invalid", port=1883, user="u", password=SecretStr("p"),
        topic_prefix="3dprint-dev", discovery_prefix="homeassistant", node_id="3dprint_dev",
    )
    with pytest.raises(LiveServiceRefused, match="'3dprint-dev'"):
        MqttBridge(live).__enter__()


def test_a_test_broker_config_is_this_runs_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRINT_SHOP_TEST_BROKER", "broker.invalid:1884")
    monkeypatch.setenv("PRINT_SHOP_TEST_BROKER_USER", "u")
    monkeypatch.setenv("PRINT_SHOP_TEST_BROKER_PASSWORD", "p")
    cfg = broker_config()
    assert (cfg.host, cfg.port) == ("broker.invalid", 1884)
    assert cfg.topic_prefix == f"3dprint-test-{RUN}"
    assert cfg.node_id == f"3dprint_test_{RUN}"
    assert not cfg.discovery_prefix.startswith("homeassistant"), (
        "Home Assistant never sees a test's entities"
    )


def test_no_test_broker_is_said(not_opted_in: None) -> None:
    with pytest.raises(RuntimeError, match="set PRINT_SHOP_TEST_BROKER"):
        broker_config()


def test_a_call_to_litellm_is_refused_unless_opted_in(not_opted_in: None) -> None:
    with httpx.Client() as client, pytest.raises(LiveServiceRefused, match="PRINT_SHOP_TEST_LLM"):
        client.get("http://127.0.0.1:4000/v1/models")


@pytest.mark.mqtt
def test_a_marked_test_runs_only_when_opted_in() -> None:
    """Reaching this line means the run set PRINT_SHOP_TEST_BROKER; otherwise it's skipped."""
    assert broker_config().topic_prefix.startswith("3dprint-test-")
