"""A real `load` on a real remote host, against the same `load` here (mqucifer/print-shop#373).

Skipped unless opted in, as in CI: `PRINT_SHOP_TEST_REMOTE` is the host's SSH target,
`PRINT_SHOP_TEST_REMOTE_CORES` the cores its jobs are pinned to, and
`PRINT_SHOP_TEST_REMOTE_FLOOR` the MemFree never to go under. Its jobs hold at most `MEMORY`
there. The Mac's settings aren't read: the host is the opt-in's alone.

    PRINT_SHOP_TEST_REMOTE=HOST PRINT_SHOP_TEST_REMOTE_CORES=5-9,15-19 \\
    PRINT_SHOP_TEST_REMOTE_FLOOR=10g uv run pytest -m remote -rs tests/test_bench_remote_live.py

The CAE suite's own fixtures are made here in a temporary folder, and its `load/one.json` read
here once and then three times through a pool of this host (one at a time) and the remote (two):
every figure is the same, only the seconds each step took differ.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.bench.budget import Budget, Slots
from print_shop.bench.job import BenchJob, JobResult, run_here
from print_shop.bench.pool import Pool
from print_shop.bench.remote import Remote, RemoteHost, cores_of, cpuset
from print_shop.study.reading import _files

OPT_IN = "PRINT_SHOP_TEST_REMOTE"
#: What its jobs may hold there at once: two `load`s of 3g.
MEMORY = 6 * 1024
CASES = Path(__file__).parent / "cae_cases"
LOAD = ["--weight", "5", "--at", "115,10,10"]


def opted_in_host() -> RemoteHost | None:
    """The remote host the opt-in names, or None where there's none.

    Raises:
        ValueError: If it names one and not its cores and floor.
    """
    target = os.environ.get(OPT_IN, "").strip()
    if not target:
        return None
    said = {k: os.environ.get(f"{OPT_IN}_{k}", "").strip() for k in ("CORES", "FLOOR")}
    missing = [f"{OPT_IN}_{k}" for k, v in said.items() if not v]
    if missing:
        raise ValueError(f"{OPT_IN} names a host: set {', '.join(missing)} too")
    from print_shop.bench.budget import megabytes

    return RemoteHost("remote", target, cores_of(said["CORES"]), MEMORY,
                      megabytes(said["FLOOR"]))


pytestmark = [
    pytest.mark.remote,
    pytest.mark.cae,
    pytest.mark.skipif(not os.environ.get(OPT_IN, "").strip(),
                       reason=f"runs on a real remote host: set {OPT_IN} to opt in"),
]


def _figures(value: Any) -> Any:
    """A record's numbers, less the seconds each step took."""
    if isinstance(value, dict):
        return {k: _figures(v) for k, v in value.items() if not k.endswith("_s")}
    if isinstance(value, list):
        return [_figures(v) for v in value]
    return value


def _record(job: BenchJob, done: JobResult) -> dict[str, Any]:
    saved = next(f for f in done.saved if f.endswith(".json"))
    record: dict[str, Any] = json.loads((job.folder / saved).read_text())
    return record


def _job(fixtures: Path, folder: Path) -> BenchJob:
    shutil.copytree(fixtures / "load", folder)
    raw = json.loads((folder / "one.json").read_text())
    return BenchJob.of(["load", "one.json", *LOAD], folder, ["one.json", *_files(raw)])


def test_a_load_on_the_remote_host_reads_every_figure_as_here(tmp_path: Path) -> None:
    stated = opted_in_host()
    assert stated is not None
    there = Remote(stated)
    assert there.refused() is None, "its images are this host's, by ID"
    print(f"\n{stated.said()}: MemFree {there.memory_free_mb() / 1024:.1f} GiB before")
    work = tmp_path / "fixtures"
    work.mkdir()
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr

    alone = _job(work, tmp_path / "alone")
    done = run_here(alone)
    here = _record(alone, done)
    assert here["verdict"] == "OK", done.stdout
    assert here["ran"]["host"] == "here"

    pool = Pool(Slots(Budget(3 * 1024, 2)), [there])
    results: dict[int, tuple[BenchJob, JobResult, tuple[int, ...]]] = {}

    def one(k: int) -> None:
        with pool.take(3 * 1024, 2, kind="load one.json") as cores:
            job = _job(work, tmp_path / f"job-{k}")
            results[k] = (job, pool.run(job), cores)

    threads = [threading.Thread(target=one, args=(k,)) for k in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(pool.said())
    print(f"MemFree {there.memory_free_mb() / 1024:.1f} GiB after")
    assert len(results) == 3
    assert {r.host for _, r, _ in results.values()} == {"here", "remote"}
    for job, result, cores in results.values():
        record = _record(job, result)
        assert record["ran"]["host"] == result.host
        assert record["ran"]["image_id"] == here["ran"]["image_id"]
        assert record["ran"]["code"] == here["ran"]["code"]
        if result.host == "remote":
            assert record["ran"]["cpus_pinned"] == cpuset(cores)
        assert record["verdict"] == here["verdict"]
        assert _figures(record["numbers"]) == _figures(here["numbers"]), result.host
    assert there.ours_left() == [] and there.left == [], "its volumes are removed"
