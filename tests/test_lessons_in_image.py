"""The learn loop's first part, the whole way, in real containers with a scripted model.

One run is met by the harness's own checks, approved, and writes its lessons in its after-action
review. A reviewer keeps one through the command line, and the next run starts with it, uses it,
and scores it. The part is plain boxes, made up in `test_rounds_in_image.py`. Its own file, under
the `bench` job's half, so the `rounds` job keeps its time. Needs the image.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.rounds import after_action, lessons
from print_shop.rounds.run import Run
from tests.rounds_fakes import Scripted, tool
from tests.test_rounds_in_image import SIZE_NEED, done, script, write

pytestmark = pytest.mark.bench

BY = "stand-in: a reviewer in a test"


def met(root: Path, name: str, *more: Any) -> tuple[Run, Scripted]:
    """A run whose needs pass the harness's own checks: needs, a script, `done`, then `more`."""
    model = Scripted(
        [tool("needs", needs=[SIZE_NEED])],
        [write("make.py", script(gap=0.5), run=True)],
        [done()],
        *more,
    )
    run = Run.start(root, name, "A block, 20 mm across.", [], model, sleep=lambda _: None)
    assert run.drive().name == "met"
    return run, model


def reviewed(run: Run, *turns: Any) -> Run:
    """Approved by a stand-in, then its after-action review, on the same session."""
    run.approve(BY, "Fine.")
    again = Run.load(run.folder, Scripted(*turns), sleep=lambda _: None)
    assert again.after_action_review().name == after_action.REVIEWED
    return again


def cli(root: Path, *words: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if "KEY" not in k} | {"HOME": str(root)}
    return subprocess.run(
        [sys.executable, str(host.REPOSITORY / "scripts" / "rounds.py"), "--root", str(root)]
        + list(words),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_a_runs_lessons_are_checked_kept_given_to_the_next_run_and_scored(
    tmp_path: Path,
) -> None:
    first, _ = met(tmp_path, "first")
    # Call 2 wrote and ran the script; call 3 was `done`, whose clean run read `volume`.
    first = reviewed(
        first,
        [
            tool(
                "after_action",
                lessons=[
                    {
                        "fix_shape": "one script for every file",
                        "kind": "job",
                        "routes": ["model"],
                        "text": "One script exports every file: run it as you write it.",
                        "calls": [2, 3],
                        "readings": [{"round": 1, "need": "volume"}],
                    },
                    {
                        "fix_shape": "read the volume",
                        "kind": "verify",
                        "routes": ["harness"],
                        "text": "Read the volume before calling done.",
                        "calls": [3],
                    },
                ],
                scores=[],
            )
        ],
    )
    build, check = (int(r["id"]) for r in first.ledger.rows("SELECT id FROM lessons ORDER BY id"))
    assert [r["status"] for r in first.ledger.rows("SELECT status FROM lessons")] == [
        "candidate",
        "candidate",
    ]

    # The reviewer reads each against the ledger, and keeps them, the check only as a check.
    listed = cli(tmp_path, "lessons", "first")
    assert listed.returncode == 0, listed.stderr
    assert "call 2 (round 1): write" in listed.stdout
    assert "round 1, `volume`: passed" in listed.stdout
    assert cli(tmp_path, "lessons", "accept", str(build), "--by", BY).returncode == 0
    refused = cli(tmp_path, "lessons", "accept", str(check), "--by", BY)
    assert refused.returncode == 3 and "never given as a note" in refused.stderr
    kept = cli(tmp_path, "lessons", "accept", str(check), "--by", BY, "--becomes", "bench_check")
    assert kept.returncode == 0, kept.stderr

    # The next run starts with the one for the model, and only that.
    second, model = met(tmp_path, "second")
    opening = model.bodies[0]["messages"][1]["content"]
    assert lessons.HEADING in opening
    assert (
        f"**Lesson {build}** (understanding the job), from `first`, written by the model: One "
        "script"
    ) in opening
    assert "Read the volume" not in opening
    given = [dict(r) for r in second.ledger.rows("SELECT * FROM run_lessons WHERE run='second'")]
    assert [(g["lesson"], g["how"]) for g in given] == [(build, "start")]
    assert lessons.one(second.ledger, build)["status"] == "given"

    # It scores what it was given, with the calls that show it, and the lesson holds.
    second = reviewed(
        second,
        [
            tool(
                "after_action",
                lessons=[],
                scores=[{"lesson": build, "score": "used", "calls": [2], "why": "one script"}],
            )
        ],
    )
    scores = [dict(r) for r in second.ledger.rows("SELECT * FROM lesson_scores")]
    assert [
        (s["run"], s["lesson"], s["score"], s["by"], json.loads(s["calls"])) for s in scores
    ] == [("second", build, "used", "model", [2])]
    assert lessons.one(second.ledger, build)["status"] == "held"
    shown = cli(tmp_path, "lessons", "second")
    assert f"Lesson {build} (start, now held): used by model (calls 2): one script" in shown.stdout
    for name in ("first", "second"):
        row = second.ledger.rows("SELECT ending FROM runs WHERE run=?", name)[0]
        assert row["ending"] == "approved", "the review leaves the approval as it was"
