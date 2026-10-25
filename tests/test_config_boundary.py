"""The runtime reaches a model only through LiteLLM, and build-time knobs stay out.

Two boundaries, both asserted rather than trusted.

**Every model call goes through LiteLLM :4000** (mqucifer/project-print
`CLAUDE.md`). The prototype's default posted to the Spark's SGLang by hostname
and named the served model. Here `src/` names a LiteLLM alias, the only URL it
holds is the proxy on loopback, and the thinking switch belongs to the alias's
own settings, not to this code.

**Build-time configuration never appears in `src/`.** In the prototype the dev
swarm and the print pipeline shared one server, so a knob set for the swarm
changed the conditions the pipeline ran under, and the failure was quiet: a
timed-out model call fell back to `propose_geometric` and the job completed
with an orientation no model proposed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from print_shop.expert import client

SRC = Path("src/print_shop")

#: Names that belong to the prototype's build swarm. None has any meaning to
#: the print pipeline.
BUILD_TIME_NAMES = (
    "WORK_MODEL",
    "REVIEW_MODEL",
    "REVIEW_PASSES",
    "REVIEW_FOCUS",
    "max-mamba-cache-size",
    "max_mamba_cache_size",
    "start-dflash",
    "append-system-prompt",
)

#: What a direct call to the Spark would need: SGLang's port, the served
#: model's id, or a pi profile. Any of them in `src/` means a path around the
#: proxy.
AROUND_THE_PROXY = (
    re.compile(r":8888\b"),
    re.compile(r"qwen[\w.-]*-sglang"),
    re.compile(r"sglang(-[a-z]+)?/"),
)

#: Owned by the alias in LiteLLM's config. Sent from here, it would fight it.
ALIAS_SETTINGS = ("enable_thinking", "chat_template_kwargs", "temperature")

URL = re.compile(r"https?://[^\s\"'`)]+")


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_there_are_source_files_to_check() -> None:
    """Guard the guard: a bad glob would make every test below vacuous."""
    files = source_files()
    assert len(files) >= 10, f"only found {len(files)} source files"


@pytest.mark.parametrize("name", BUILD_TIME_NAMES)
def test_build_time_configuration_never_appears_in_src(name: str) -> None:
    offenders = [f for f in source_files() if name in f.read_text()]
    assert not offenders, f"{name!r} is build-time configuration, in {offenders}"


@pytest.mark.parametrize("pattern", AROUND_THE_PROXY, ids=lambda p: p.pattern)
def test_src_has_no_way_around_the_proxy(pattern: re.Pattern[str]) -> None:
    offenders = [f for f in source_files() if pattern.search(f.read_text())]
    assert not offenders, f"{pattern.pattern!r} would reach the Spark directly: {offenders}"


def test_the_only_url_in_src_is_the_proxy_on_loopback() -> None:
    urls = {u for f in source_files() for u in URL.findall(f.read_text())}
    assert urls == {"http://127.0.0.1:4000/v1"}, urls


@pytest.mark.parametrize("name", ALIAS_SETTINGS)
def test_the_client_leaves_sampling_and_thinking_to_the_alias(name: str) -> None:
    code = (SRC / "expert" / "client.py").read_text()
    # The docstring may explain the choice; the code must not send it.
    body = code.split('"""', 2)[2]
    assert name not in body, f"{name!r} is the alias's setting, not the client's"


def test_the_default_alias_is_one_the_proxy_serves() -> None:
    """The crew's five aliases, all the 27B (the operator, 2026-10-24)."""
    assert client.DEFAULT_MODEL in {
        "crew-local",
        "crew-draft",
        "crew-analysis",
        "crew-code",
        "crew-code-think",
    }


def test_the_endpoint_stays_overridable_from_the_settings() -> None:
    """Point the pipeline at another proxy, or a stub, without editing code."""
    assert client.URL_KEY == "PRINT_SHOP_LLM_URL"
    assert client.MODEL_KEY == "PRINT_SHOP_LLM_MODEL"
