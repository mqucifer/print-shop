"""The bench on the Sponsor's exported frame: a local run, skipped wherever the file is absent.

This repository is public, so nothing here holds a figure from the design: these tests compare
the frame's panes with each other and with the play they were asked for. The figures themselves
are saved outside the repository by `scripts/bench_local_run.py triforce`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host

pytestmark = pytest.mark.bench_local
FRAME = Path(
    os.environ.get("PRINT_BENCH_TRIFORCE", "~/Documents/cad-context/TriForceStandGlass.step")
).expanduser()
#: The frame's heights and play, off this public repository: see `scripts/bench_local_run.py`.
SETTINGS = Path(os.environ.get("PRINT_BENCH_LOCAL", "~/print-shop/bench-local.json")).expanduser()


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    if not FRAME.is_file() or not SETTINGS.is_file():
        pytest.skip(f"needs {FRAME} and {SETTINGS}: they're only on the Sponsor's Mac")
    local = json.loads(SETTINGS.read_text())
    if host.image_id() is None:
        pytest.fail(f"no image {host.IMAGE}: run `scripts/bench.py build` first")
    work = tmp_path_factory.mktemp("frame")
    shutil.copy(FRAME, work / "frame.step")
    found: dict[str, dict[str, Any]] = {}
    for name, args in {
        "info": ["info", "frame.step"],
        "panes": ["panes", "frame.step", "--pocket-z", str(local["pocket_z"]),
                  "--lip-z", str(local["lip_z"]), "--clearance", str(local["play"])],
        "capture": ["capture", "frame.step", "--pocket-z", str(local["pocket_z"]),
                    "--clearance", str(local["play"]),
                    "--pane-thickness", str(local["pane_thickness"])],
        "thin": ["thin", "frame.step"],
        "render": ["render", "frame.step", "--views", "top", "iso"],
    }.items():  # fmt: skip
        done = host.run(args, work)
        saved = re.search(r"saved: (\S+)\.txt and \.json", done.stdout)
        assert saved, done.stdout + done.stderr
        found[name] = json.loads((work / f"{saved.group(1)}.json").read_text())
    return found


def test_both_volumes_agree(answers: dict[str, dict[str, Any]]) -> None:
    assert answers["info"]["verdict"] == "OK", answers["info"]["why"]


def test_every_pane_fits_at_the_play_by_both_methods(answers: dict[str, dict[str, Any]]) -> None:
    panes = answers["panes"]["numbers"]["panes"]
    assert answers["panes"]["verdict"] == "OK", answers["panes"]["why"]
    assert panes
    play = json.loads(SETTINGS.read_text())["play"]
    for pane in panes:
        assert pane["gap_2d_mm"] == pytest.approx(play, abs=1e-3)
        assert pane["gap_3d_mm"] == pytest.approx(play, abs=0.005)
        assert pane["overlap_3d_mm3"] == pytest.approx(0.0, abs=1e-3)


def test_the_design_repeats_so_its_panes_match(answers: dict[str, dict[str, Any]]) -> None:
    panes = answers["panes"]["numbers"]["panes"]
    areas = [p["pane_mm2"] for p in panes]
    assert max(areas) - min(areas) < 0.01
    edges = [p["no_lip_mm"] for p in panes]
    assert max(edges) - min(edges) < 0.01


def test_the_no_lip_edge_reads_the_same_by_sections_and_rays(
    answers: dict[str, dict[str, Any]],
) -> None:
    for pane in answers["panes"]["numbers"]["panes"]:
        assert pane["no_lip_rays_mm"] == pytest.approx(pane["no_lip_mm"], abs=1.0)


def test_capture_gives_an_answer_with_its_two_methods_agreeing(
    answers: dict[str, dict[str, Any]],
) -> None:
    """Whether this frame holds its panes is the Sponsor's to judge from the figures, off this
    repository. Here it's that the methods agree, and that the panes repeat as the design does."""
    capture = answers["capture"]
    assert capture["verdict"] not in ("DISAGREE", "NO ANSWER"), capture["why"]
    panes = capture["numbers"]["panes"]
    assert panes
    for way in ("up", "down"):
        lips = [p[way]["least_lip_mm"] for p in panes if p[way]["least_lip_mm"] is not None]
        assert not lips or max(lips) - min(lips) < 0.05, way


def test_the_renders_are_the_shape_the_mesh_projects_to(answers: dict[str, dict[str, Any]]) -> None:
    assert answers["render"]["verdict"] == "OK", answers["render"]["why"]
