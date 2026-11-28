"""A remote job's way of running, over this host's own Docker (mqucifer/print-shop#373).

`Remote` over SSH is `docker -H ssh://HOST`; over `docker` alone it's this host's Docker, run the
remote's way: its files and the bench's code sent into volumes of their own, the same `docker
run`, what it saved brought back, its volumes removed. So the volumes, the files' owners and the
tar in and out are checked against a real Docker wherever the bench's image is built, CI's bench
job included, with no remote host at all. `tests/test_bench_remote_live.py` runs a real one.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
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
    # Its free memory and disk, read in a container of its own, and every bench container on it,
    # counted by its name and label: what `reserve` reads before each start. Docker here is
    # shared with other sessions' benches, so what they come to isn't asserted.
    free_mb, disk_gb = there.probe()
    assert free_mb > 0 and disk_gb > 0
    assert there.others_mb({}) >= 0
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
    assert there.ours_left() == [] and there.left == [] and there.over == []
    # Its hold went with it, and nothing of its own is left for a later study to clear.
    assert not there.held() and not there._holds
    assert there.clear_stale() == ([], [])


def _provenance(path: Path) -> None:
    """An extended attribute on a file, as macOS gives every file a copy makes
    (`com.apple.provenance`), or a user one where that's what the system allows; none where it
    allows none."""
    # Linux's call; off it, as on macOS, the `xattr` command below.
    set_attribute: Any = getattr(os, "setxattr", None)
    for name in ("com.apple.provenance", "user.provenance") if set_attribute else ():
        try:
            set_attribute(path, name, b"\x01\x02")
            return
        except OSError:
            continue
    with contextlib.suppress(OSError):
        subprocess.run(["xattr", "-w", "com.apple.provenance", "x", str(path)], check=False,
                       capture_output=True)


def test_a_folder_whose_files_are_named_between_others_goes_over_whole(tmp_path: Path) -> None:
    """A job's files in a folder of their own, named in between files above it, as an assembly
    names its parts' references and their bands in turn, each with an extended attribute
    (mqucifer/print-shop#380). The helper's tar, as root with only CHOWN and FOWNER, restored the
    folder's owner, the job's user, as it moved past it, and then couldn't write the next file
    into it: "Cannot open: Permission denied". All of them go over and the job reads."""
    work = tmp_path / "made"
    work.mkdir()
    (work / "block.py").write_text(BLOCK)
    made = host.run(["block.py"], work, python=True)
    assert made.returncode == 0, made.stdout + made.stderr
    folder = tmp_path / "job"
    (folder / "references").mkdir(parents=True)
    (folder / "block.step").write_bytes((work / "block.step").read_bytes())
    files = ["block.step"]
    for k in range(12):
        for name in (f"references/part-{k:02d}.step", f"band-{k:02d}.step"):
            (folder / name).write_bytes(os.urandom(2000))
            _provenance(folder / name)
            files.append(name)
    _provenance(folder / "references")
    job = BenchJob.of(["info", "block.step"], folder, files)
    there = Remote(RemoteHost("docker", "localhost", (0, 1), 3 * 1024, 0), ("docker",))
    done = there.run(job, (0, 1))
    assert done.returncode == 0, done.stdout + done.stderr
    assert _record(job, done)["ran"]["host"] == "docker"
    assert there.ours_left() == [] and there.left == [] and not there.held()
