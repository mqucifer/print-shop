"""Build-time configuration must not leak into the runtime.

`AGENTS.md` separates build-time words from runtime words, and the same split
applies to configuration — with teeth, because the dev swarm and the print
pipeline talk to the *same* SGLang server. A knob that belongs to the swarm
changes the conditions the pipeline runs under, and the failure is quiet: if
the swarm saturates the state pool, the pipeline's LLM call times out,
`orient_node` catches `RuntimeError`, falls back to `propose_geometric`, and
the job completes with an orientation no model ever proposed.

The boundary itself is simple — `src/` names the served model and posts to the
endpoint directly; it never names a pi profile, a dispatch variable, or a
launcher flag. Simple, and exactly the kind of thing that erodes when someone
debugging at 1am hardcodes a profile name to reproduce something.

So it is asserted rather than trusted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path("src/print_shop")

#: Names that belong to the swarm. Each is how a pi dispatch is configured;
#: none has any meaning to the print pipeline.
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

#: pi resolves these to an endpoint plus sampling settings. `src/` must name
#: the served model instead — see `_DEFAULT_MODEL` in `src/expert/client.py`.
#:
#: The suffix is optional because the swarm collapsed six role profiles
#: (`sglang-code`, `sglang-review`, ...) into one named `sglang` on 2026-09-16.
#: Matching only the hyphenated form would have left this guard passing
#: vacuously against the naming that now exists. The trailing slash is what
#: makes this a profile reference rather than the served model id, which
#: legitimately ends in `-sglang`.
PI_PROFILE = re.compile(r"sglang(-[a-z]+)?/")


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_there_are_source_files_to_check() -> None:
    """Guard the guard: a bad glob would make every test below vacuous."""
    files = source_files()
    assert len(files) >= 10, f"only found {len(files)} source files"


@pytest.mark.parametrize("name", BUILD_TIME_NAMES)
def test_build_time_configuration_never_appears_in_src(name: str) -> None:
    offenders = [f for f in source_files() if name in f.read_text()]
    assert not offenders, (
        f"{name!r} is build-time configuration and appears in "
        f"{[str(f) for f in offenders]}. See AGENTS.md, "
        f"'Configuration: two systems that share one endpoint'."
    )


def test_src_names_the_served_model_not_a_pi_profile() -> None:
    """`sglang/qwen3.8-27b-sglang` is a pi profile; the endpoint knows nothing of it."""
    offenders = [f for f in source_files() if PI_PROFILE.search(f.read_text())]
    assert not offenders, (
        f"a pi profile name appears in {[str(f) for f in offenders]}. The "
        f"runtime posts to the endpoint directly and must name the served "
        f"model, e.g. 'qwen3.8-27b-sglang'."
    )


def test_the_runtime_endpoint_stays_overridable() -> None:
    """The one deliberate seam: point the pipeline elsewhere without editing code.

    If this ever becomes a hardcoded URL, running the pipeline against a
    different server — or a stub — means patching source, and the boundary
    this file protects stops being expressible.
    """
    client = (SRC / "expert" / "client.py").read_text()
    for var in ("PRINT_AGENT_LLM_URL", "PRINT_AGENT_LLM_MODEL"):
        assert var in client, f"{var} is the documented runtime override"
