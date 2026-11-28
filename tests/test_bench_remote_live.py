"""A real `load` on a real remote host, against the same `load` here (mqucifer/print-shop#373).

Skipped unless opted in, as in CI: `PRINT_SHOP_TEST_REMOTE` is the host's SSH target,
`PRINT_SHOP_TEST_REMOTE_CORES` the cores its jobs are pinned to, and
`PRINT_SHOP_TEST_REMOTE_FLOOR` the MemFree never to go under. Its jobs hold at most `MEMORY`
there. The Mac's settings aren't read: the host is the opt-in's alone.

    PRINT_SHOP_TEST_REMOTE=HOST PRINT_SHOP_TEST_REMOTE_CORES=5-9,15-19 \\
    PRINT_SHOP_TEST_REMOTE_FLOOR=10g uv run pytest -m remote -rs tests/test_bench_remote_live.py

The CAE suite's own fixtures are made here in a temporary folder, and its `load/one.json` read
here once and then three times through a pool of this host (one at a time) and the remote (two):
every figure is the same, only the seconds each step took differ. Then each of the cases whose
solves lean most on the libraries' own kernels, which may differ between this host's and the
remote's processors, is read once here and twice there, and every figure compared: the bowed
leg read as printed, its walls and its core in cut planes (mqucifer/print-shop#356), and a
plate pulled off its bolts (`through/pull.json`). Read so on the Spark, the plate's bolt's
sheared area differed from the Mac's in its last binary digit, its reply otherwise the same:
so a study reads each candidate's cases on one host (`bench.pool`), and this checks what that
rests on. Each host reads a case the same every time, to the last digit; across hosts the
verdict, its why and its notes are the same, and every figure but in its last digits
(`LAST_DIGITS`), each such one printed.
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
#: How far apart two hosts' figures may be, relative to them: their last few binary digits of
#: 53, as two processors' rounding gives them, and nothing a figure is ever said to.
LAST_DIGITS = 1e-12


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


def _job(fixtures: Path, folder: Path, assembly: str = "load/one.json",
         words: tuple[str, ...] | list[str] = LOAD) -> BenchJob:
    """A `load` of one of the fixtures' assembly files, over a copy of its folder."""
    source, name = assembly.split("/")
    shutil.copytree(fixtures / source, folder)
    raw = json.loads((folder / name).read_text())
    return BenchJob.of(["load", name, *words], folder, [name, *_files(raw)])


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The CAE suite's own fixtures, made here."""
    work = tmp_path_factory.mktemp("fixtures")
    shutil.copy(CASES / "make_fixtures.py", work / "make_fixtures.py")
    made = host.run(["make_fixtures.py"], work, python=True, image=host.CAE_IMAGE)
    assert made.returncode == 0, made.stdout + made.stderr
    return work


@pytest.fixture(scope="module")
def there() -> Remote:
    stated = opted_in_host()
    assert stated is not None
    found = Remote(stated)
    assert found.refused() is None, "its images are this host's, by ID"
    return found


def test_a_load_on_the_remote_host_reads_every_figure_as_here(
    tmp_path: Path, fixtures: Path, there: Remote,
) -> None:
    stated = there.host
    print(f"\n{stated.said()}: MemFree {there.memory_free_mb() / 1024:.1f} GiB before")
    work = fixtures

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


def _bowed(fixtures: Path) -> list[str]:
    at = json.loads((fixtures / "load/bowed-at.json").read_text())["bowed"]
    return ["--weight", "1", "--at", ",".join(f"{v:g}" for v in at)]


@pytest.mark.parametrize("assembly,words", [
    ("load/printed-bowed.json", None),
    ("through/pull.json", ["--weight", "20", "--at", "40,20,16", "--at", "60,20,16",
                           "--toward", "+z"]),
])
def test_every_figure_of_a_harder_case_is_the_same_there(
    tmp_path: Path, fixtures: Path, there: Remote, assembly: str, words: list[str] | None,
) -> None:
    said = words if words is not None else _bowed(fixtures)
    alone = _job(fixtures, tmp_path / "here", assembly, said)
    here = _record(alone, run_here(alone))
    assert here["verdict"] != "NO ANSWER", here.get("why")
    records = []
    for k in range(2):
        job = _job(fixtures, tmp_path / f"there-{k}", assembly, said)
        name = there.reserve(3 * 1024)
        records.append(_record(job, there.run(job, there.host.cores[:2], name)))
    record = records[0]
    print(f"\n{assembly}: {here['verdict']} here in {here['seconds']} s, {record['verdict']} on "
          f"{there.name} in {record['seconds']} s, pinned to {record['ran']['cpus_pinned']}")
    assert record["ran"]["host"] == there.name
    assert record["ran"]["image_id"] == here["ran"]["image_id"]
    assert _figures(records[1]["numbers"]) == _figures(record["numbers"]), "one host, every digit"
    assert (record["verdict"], record["why"]) == (here["verdict"], here["why"])
    assert record["notes"] == here["notes"]
    apart = _apart(_figures(here["numbers"]), _figures(record["numbers"]))
    for where, mine, theirs in apart:
        print(f"  {where}: {mine!r} here, {theirs!r} there")
    assert all(_close(mine, theirs) for _, mine, theirs in apart), apart
    assert there.ours_left() == [] and there.left == []


def _close(mine: Any, theirs: Any) -> bool:
    if isinstance(mine, float) and isinstance(theirs, float):
        return abs(mine - theirs) <= LAST_DIGITS * max(abs(mine), abs(theirs))
    return False


def _apart(mine: Any, theirs: Any, where: str = "") -> list[tuple[str, Any, Any]]:
    """Each figure two records give otherwise, by where it is in them."""
    if isinstance(mine, dict) and isinstance(theirs, dict):
        return [found for key in sorted(set(mine) | set(theirs))
                for found in _apart(mine.get(key), theirs.get(key), f"{where}.{key}")]
    if isinstance(mine, list) and isinstance(theirs, list) and len(mine) == len(theirs):
        return [found for k, (a, b) in enumerate(zip(mine, theirs, strict=True))
                for found in _apart(a, b, f"{where}[{k}]")]
    return [] if mine == theirs else [(where, mine, theirs)]
