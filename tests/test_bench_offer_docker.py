"""A real `load` that meets a small local limit, read again the remote's way with more, against
the same read here (mqucifer/print-shop#380).

`Remote` over `docker` alone is this host's Docker run the remote's way (as in
`tests/test_bench_remote_docker.py`): its files and the bench's code sent into volumes of their
own, the same `docker run`, what it saved brought back. Here it stands for a host whose settings
state a larger memory for a load. The CAE suite's `load/one.json` is read in a container of
`SMALL`, which it meets: a check of a step's need against its room refuses it, or the kernel
stops it, and the host says so (`host.killed_met`). Offered
the stand-in's `OFFERED` (it holds about 120 MiB at most, so a busy Docker's MemFree holds it
too), it reads, and every figure is the same read's in 3g here, but for the seconds
each step took.

Docker here is shared with other sessions' benches, which the stand-in's reservation would count
against its MemFree as any remote's does (`Remote.others`, tested in `tests/test_bench_remote.py`
and `tests/test_bench_offer.py`): here they're left out, as a remote of its own wouldn't have
them.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.bench.budget import Budget, Slots
from print_shop.bench.job import BenchJob, JobResult, load_files, run_here
from print_shop.bench.pool import Pool
from print_shop.bench.remote import Remote, RemoteHost

pytestmark = pytest.mark.cae

CASES = Path(__file__).parent / "cae_cases"
LOAD = ("--weight", "5", "--at", "115,10,10")
#: A container this read meets: it holds about 120 MiB at its most in this image.
SMALL = "96m"
#: The memory the stand-in offers it, in MiB: more than `SMALL`.
OFFERED_MB = 1024


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


def _job(fixtures: Path, folder: Path, memory: str) -> BenchJob:
    shutil.copytree(fixtures / "load", folder)
    args = ("load", "one.json", *LOAD)
    return BenchJob(args, folder, tuple(load_files(folder, args)), memory, host.TIMEOUT)


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("fixtures")
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr
    return work


def test_a_load_that_meets_a_small_limit_reads_with_more_as_the_same_read(
    tmp_path: Path, fixtures: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    enough = _job(fixtures, tmp_path / "enough", host.MEMORY)
    alone = run_here(enough)
    assert alone.met is None
    mine = _record(enough, alone)
    assert mine["verdict"] == "OK", alone.stdout

    there = Remote(RemoteHost("docker", "localhost", (0, 1), 3 * 1024, 0, OFFERED_MB),
                   ("docker",))
    monkeypatch.setattr(there, "others", lambda held: (0, 0.0))
    pool = Pool(Slots(Budget(4 * 1024, 2)), [there])
    small = _job(fixtures, tmp_path / "small", SMALL)
    with pool.take(96, 2, kind="load small"):
        assert pool.where() == "here"
        done = pool.run(small)
    assert done.host == "docker", pool.said()
    theirs = _record(small, done)
    assert (theirs["verdict"], theirs["why"], theirs["notes"]) == (
        mine["verdict"], mine["why"], mine["notes"])
    assert _figures(theirs["numbers"]) == _figures(mine["numbers"])
    met = theirs["ran"]["offered"]
    # Met by a check of a step's need against its room (mqucifer/print-shop#376), or where the
    # kernel stopped it first, by Docker's own `oom` event.
    assert met is not None and met["host"] == "here" and met["limit"] == 96 * 2**20
    assert met["killed"] or met["need"] > met["room"], met
    assert theirs["ran"]["memory_max"] == str(OFFERED_MB * 2**20)
    assert theirs["ran"]["host"] == "docker"
    assert "offered: read on docker in its 1.0 GiB" in done.stdout
    assert "as on here its read met its container's 0.1 GiB" in done.stdout
    said = pool.said()
    assert "was read on docker in its 1.0 GiB" in said
    assert "met its container's memory (on here" in said
    assert there.ours_left() == [] and there.left == [] and not there.held()
