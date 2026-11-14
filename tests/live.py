"""Tests that reach a live service run only when opted in, and never as the live device.

mqucifer/print-shop#115. The tests marked `mqtt`, `llm` and `ha` reach the broker, LiteLLM and
Home Assistant. Configured from the Mac's settings, they ran under the live daemon's own topic
prefix and node, and spent the settings' key, whenever `pytest` ran without `-m`. Now each is
skipped unless its env var opts in (`OPT_IN`, enforced in `conftest.py`), and a broker test's
config is the broker the opt-in names, under a prefix, node and discovery prefix of its own run
(`broker_config`), so nothing it publishes is the live device's, or an entity in Home Assistant.
"""

from __future__ import annotations

import os
import uuid

from pydantic import SecretStr

from print_shop.core.mqtt import MqttConfig

__all__ = ["OPT_IN", "RUN", "TEST_NODE", "TEST_PREFIX", "broker_config", "opted_in"]

#: Each marker that reaches a live service, and the env var that opts in to it.
OPT_IN = {
    "mqtt": "PRINT_SHOP_TEST_BROKER",  # host[:port] of a broker for tests
    "llm": "PRINT_SHOP_TEST_LLM",  # 1: real model calls through LiteLLM :4000
    "ha": "PRINT_SHOP_TEST_HA",  # 1: Home Assistant's REST API, read-only
}

#: This run's own id, so two runs, or a run and the live daemon, never share a topic.
RUN = uuid.uuid4().hex[:8]
#: What every broker test's topics and node start with. The live device's never do.
TEST_PREFIX = "3dprint-test"
TEST_NODE = "3dprint_test"


def opted_in(marker: str) -> bool:
    return bool(os.environ.get(OPT_IN[marker], "").strip())


def broker_config() -> MqttConfig:
    """The opted-in test broker, under this run's own prefix, node and discovery prefix.

    The credentials are the settings', or `PRINT_SHOP_TEST_BROKER_USER` and `..._PASSWORD`.
    """
    named = os.environ.get(OPT_IN["mqtt"], "").strip()
    if not named:
        raise RuntimeError(f"no test broker: set {OPT_IN['mqtt']}=host[:port] to opt in")
    host, _, port = named.partition(":")
    user = os.environ.get("PRINT_SHOP_TEST_BROKER_USER")
    password = os.environ.get("PRINT_SHOP_TEST_BROKER_PASSWORD")
    if user is None or password is None:
        settings = MqttConfig.from_env()
        user = user if user is not None else settings.user
        secret = settings.password if password is None else SecretStr(password)
    else:
        secret = SecretStr(password)
    return MqttConfig(
        host=host,
        port=int(port or 1883),
        user=user,
        password=secret,
        topic_prefix=f"{TEST_PREFIX}-{RUN}",
        discovery_prefix=f"{TEST_PREFIX}-{RUN}/discovery",
        node_id=f"{TEST_NODE}_{RUN}",
    )
