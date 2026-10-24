"""Run this project's checks the way the crew's sandbox runs them.

The commands come from the project's record, `.crew/project.yaml`: the design's
sandbox image, its setup and its checks. So CI always runs what the design names,
and no story writes CI. Each command is its own container, as in the crew's
sandbox (mqucifer/crew `src/crew_org/tools/sandbox.py`): read-only, no
capabilities, not root, a home inside the checkout, and a network only for setup.

A project whose design names no image keeps the crew's Python defaults
(`src/crew_org/tools/workspace.py`), so the two stay the same. Before the design
is merged there is nothing to check, and the job passes, so the record's and the
design's pull requests aren't blocked.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

RECORD = Path(".crew/project.yaml")
HOME_DIR = ".crew-home"

# The crew's defaults for a design that names no image of its own.
IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
SETUP = "uv sync --extra dev --quiet"
CHECKS = ["uv run ruff check .", "uv run pytest -q"]

TIMEOUT = 900


def plan(record: dict) -> tuple[str, str | None, list[str]]:
    """The image, setup and checks a record's design names, or the crew's defaults."""
    design = record.get("design") or {}
    image = design.get("sandbox_image")
    own = bool(image)
    checks = list(design.get("checks") or ([] if own else CHECKS))
    setup = design.get("setup") or (None if own else SETUP)
    return image or IMAGE, setup, checks


def image_path(image: str) -> str | None:
    done = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        return None
    env = json.loads(done.stdout) or []
    return next((e.split("=", 1)[1] for e in env if e.startswith("PATH=")), None)


def container(
    image: str, path: str | None, cache: str, command: str, network: bool
) -> list[str]:
    home = f"/work/{HOME_DIR}"
    found = ["-e", f"PATH={home}/.local/bin:{path}"] if path else []
    return [
        "docker", "run", "--rm",
        "--network", "bridge" if network else "none",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        "--tmpfs", "/tmp:rw,nosuid,size=512m",
        "--memory", "2g", "--memory-swap", "2g",
        "--pids-limit", "512",
        "--cpus", "2",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "-v", f"{Path.cwd()}:/work",
        "-v", f"{cache}:/cache",
        "-w", "/work",
        "-e", "UV_CACHE_DIR=/cache",
        "-e", f"HOME={home}",
        "-e", "XDG_CACHE_HOME=/cache",
        *found,
        image,
        *shlex.split(command),
    ]  # fmt: skip


def run(image: str, path: str | None, cache: str, command: str, network: bool) -> int:
    print(f"::group::{command}", flush=True)
    try:
        code = subprocess.run(
            container(image, path, cache, command, network), timeout=TIMEOUT
        ).returncode
    except subprocess.TimeoutExpired:
        print(f"timed out after {TIMEOUT}s")
        code = 124
    print("::endgroup::", flush=True)
    print(
        f"{'passed' if code == 0 else f'failed (exit {code})'}: {command}", flush=True
    )
    return code


def main() -> int:
    if not RECORD.exists():
        print(
            f"No {RECORD}: the project isn't onboarded yet, so there is nothing to check."
        )
        return 0
    record = yaml.safe_load(RECORD.read_text(encoding="utf-8")) or {}
    if not record.get("design"):
        print("The design isn't merged yet, so there is nothing to check.")
        return 0
    image, setup, checks = plan(record)
    if not checks:
        print("The design names no checks, so there is nothing to check.")
        return 0
    if subprocess.run(["docker", "pull", "--quiet", image]).returncode != 0:
        print(f"Couldn't pull the design's image {image}.")
        return 1
    path = image_path(image)
    (Path.cwd() / HOME_DIR).mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as cache:
        if setup and run(image, path, cache, setup, network=True) != 0:
            print("Setup failed, so the checks can't run.")
            return 1
        failed = [c for c in checks if run(image, path, cache, c, network=False) != 0]
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print(f"All {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
