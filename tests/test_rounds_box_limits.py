"""A design run's bench command given the limits the bench gives it (mqucifer/print-shop#363's
review): a `load` reading a part as printed in an assembly with a `through` joint is given the
hour from the start, by its folder, as `host.run` gives it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from print_shop.bench import host
from print_shop.rounds.box import Box, Ran


def _limits(tmp_path: Path, joint: str) -> tuple[Any, Any]:
    (tmp_path / "a.json").write_text(json.dumps({
        "parts": [{"name": "a", "print": {"walls": 2}}, {"name": "b"}],
        "joints": [{"between": ["a", "b"], "kind": joint}]}))
    box = Box(tmp_path)
    box.built.add(host.CAE_IMAGE)
    asked: dict[str, Any] = {}

    def run(command: list[str], where: Path | None, timeout: int | None, image: str = "",
            env: dict[str, str] | None = None, longer: int | None = None,
            memory: str | None = None) -> Ran:
        asked.update(timeout=timeout, longer=longer)
        return Ran(0, "", 0.0)

    box._run = run  # type: ignore[method-assign]
    box.bench(["load", "a.json", "--weight", "1", "--at", "0,0,0"])
    return asked["timeout"], asked["longer"]


def test_a_design_run_s_printed_read_with_a_through_joint_is_given_the_hour(
    tmp_path: Path,
) -> None:
    assert _limits(tmp_path, "through") == (host.TWO_PASSES, None)


def test_a_design_run_s_printed_read_with_no_through_joint_keeps_load_s_own(
    tmp_path: Path,
) -> None:
    timeout, longer = _limits(tmp_path, "bonded")
    assert timeout is None and longer == host.TWO_PASSES
