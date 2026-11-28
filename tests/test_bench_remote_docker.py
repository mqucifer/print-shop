"""A remote job's way of running, over this host's own Docker (mqucifer/print-shop#373).

`Remote` over SSH is `docker -H ssh://HOST`; over `docker` alone it's this host's Docker, run the
remote's way: its files and the bench's code sent into volumes of their own, the same `docker
run`, what it saved brought back, its volumes removed. So the volumes, the files' owners and the
tar in and out are checked against a real Docker wherever the bench's image is built, CI's bench
job included, with no remote host at all. `tests/test_bench_remote_live.py` runs a real one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.bench.job import BenchJob, JobResult, run_here
from print_shop.bench.remote import Remote, RemoteHost

pytestmark = pytest.mark.bench

BLOCK = """
import cadquery as cq
cq.exporters.export(cq.Workplane().box(20, 10, 5), "block.step")
"""


def _record(job: BenchJob, done: JobResult) -> dict[str, Any]:
    saved = next(f for f in done.saved if f.endswith(".json"))
    record: dict[str, Any] = json.loads((job.folder / saved).read_text())
    return record


def test_a_job_run_the_remotes_way_reads_as_it_does_here(tmp_path: Path) -> None:
    work = tmp_path / "made"
    work.mkdir()
    (work / "block.py").write_text(BLOCK)
    made = host.run(["block.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    jobs = []
    for name in ("here", "there"):
        folder = tmp_path / name / "parts"
        folder.mkdir(parents=True)
        (folder / "block.step").write_bytes((work / "block.step").read_bytes())
        jobs.append(BenchJob.of(["info", "parts/block.step"], tmp_path / name,
                                ["parts/block.step"]))
    here = run_here(jobs[0])
    there = Remote(RemoteHost("docker", "localhost", (0, 1), 3 * 1024, 0), ("docker",))
    done = there.run(jobs[1], (0, 1))
    assert done.returncode == here.returncode == 0, done.stdout + done.stderr
    assert done.host == "docker" and len(done.saved) == 2
    mine, theirs = _record(jobs[0], here), _record(jobs[1], done)
    assert theirs["numbers"] == mine["numbers"]
    assert theirs["ran"]["host"] == "docker" and mine["ran"]["host"] == "here"
    assert theirs["ran"]["user"] == mine["ran"]["user"] != 0, "the same user, as here"
    assert theirs["ran"]["network_up"] == ["lo"]
    assert theirs["ran"]["cpus_pinned"] == "0-1"
    assert theirs["ran"]["code"] == mine["ran"]["code"]
    assert there.ours_left() == [] and there.left == []
