"""Specification for a study's bench jobs on a remote host's Docker over SSH, placed across
hosts by each one's budget and measured speed (mqucifer/print-shop#373).

The remote here is made up: `tests/fake_docker.py` stands in for `docker -H ssh://HOST`, its
volumes folders in the test's own, its `load` made up from the files it was sent. Docker and SSH
are never asked. The same study read here alone and across hosts gives the same table.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host, remote
from print_shop.bench.budget import Budget, Slots
from print_shop.bench.job import HERE, BenchJob, HostLost, JobResult, saved_files
from print_shop.bench.pool import Pool
from print_shop.bench.remote import NoRoom, Remote, RemoteHost, cores_of, cpuset, stated
from print_shop.study.loop import StudyLoop, StudyPart, run
from print_shop.study.reading import BenchLoad
from print_shop.study.table import table
from tests import fake_docker
from tests.test_settings_study import (  # noqa: F401 - `part` is a fixture
    BOLT,
    CASES,
    FOUR_WALLS,
    THREE_WALLS,
    WAIT,
    Model,
    part,
)

FAKE = Path(fake_docker.__file__)
#: The images' IDs, made up: this host's, and the remote's where they agree.
IDS = {host.IMAGE: "sha256:" + "b" * 64, host.CAE_IMAGE: "sha256:" + "c" * 64}
SETTINGS = {
    "BENCH_HOSTS": "spark",
    "BENCH_HOST_SPARK_SSH": "spark",
    "BENCH_HOST_SPARK_CORES": "5-9,15-19",
    "BENCH_HOST_SPARK_MEMORY": "15g",
    "BENCH_HOST_SPARK_FLOOR": "10g",
}


@pytest.fixture(autouse=True)
def _these_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """This host's images, as made up: Docker here is never asked."""
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: IDS.get(image))


def _remote(root: Path, memory: str = "6g", cores: str = "0-3", floor: str = "10g",
            images: dict[str, str] | None = None) -> Remote:
    root.mkdir(parents=True, exist_ok=True)
    (root / "volumes").mkdir(exist_ok=True)
    (root / "images.json").write_text(json.dumps(IDS if images is None else images))
    found = stated({"BENCH_HOSTS": "spark", "BENCH_HOST_SPARK_SSH": "spark",
                    "BENCH_HOST_SPARK_CORES": cores, "BENCH_HOST_SPARK_MEMORY": memory,
                    "BENCH_HOST_SPARK_FLOOR": floor})[0]
    return Remote(found, (sys.executable, str(FAKE), str(root)))


def _log(root: Path) -> list[dict[str, Any]]:
    path = root / "log.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _meminfo(root: Path, free_gib: float, available_gib: float = 90.0) -> None:
    (root / "meminfo").write_text(f"MemTotal: 127000000 kB\nMemFree: {int(free_gib * 1024**2)} "
                                  f"kB\nMemAvailable: {int(available_gib * 1024**2)} kB\n")


def _job(folder: Path, walls: int = 3, case: tuple[str, ...] = CASES[0].args) -> BenchJob:
    """A `load` over an assembly of one part in its own folder, as a study makes one."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bar.step").write_text("STEP")
    (folder / "meshes").mkdir(exist_ok=True)
    (folder / "meshes" / "ring.stl").write_text("solid ring")
    raw = {"parts": [{"name": "bar", "file": "bar.step",
                      "print": {"walls": walls, "infill": 25,
                                "regions": [{"name": "ring", "mesh": "meshes/ring.stl"}]}}]}
    (folder / "study-assembly.json").write_text(json.dumps(raw))
    return BenchJob.of(["load", "study-assembly.json", *case], folder,
                       ["study-assembly.json", "bar.step", "meshes/ring.stl"])


def _here(job: BenchJob) -> JobResult:
    """This host's runner, made up: the same made-up `load`, over the job's own folder."""
    code, said = fake_docker.bench(list(job.args), job.folder, {"BENCH_HOST": HERE})
    return JobResult(code, said, "", saved_files(said))


# --------------------------------------------------------------------------
# The hosts, as the settings state them
# --------------------------------------------------------------------------


def test_a_host_is_every_key_its_settings_state() -> None:
    (spark,) = stated(SETTINGS)
    assert spark == RemoteHost("spark", "spark", (5, 6, 7, 8, 9, 15, 16, 17, 18, 19),
                               15 * 1024, 10 * 1024)
    assert spark.docker() == ("docker", "-H", "ssh://spark")
    assert stated({}) == [], "no hosts named, none used"


@pytest.mark.parametrize("key", ["SSH", "CORES", "MEMORY", "FLOOR"])
def test_nothing_about_a_host_is_guessed(key: str) -> None:
    settings = {k: v for k, v in SETTINGS.items() if k != f"BENCH_HOST_SPARK_{key}"}
    with pytest.raises(ValueError, match=f"don't state BENCH_HOST_SPARK_{key}: nothing"):
        stated(settings)


@pytest.mark.parametrize("change,said", [
    ({"BENCH_HOST_SPARK_SSH": "-oProxyCommand=x"}, "an SSH target is a name"),
    ({"BENCH_HOST_SPARK_SSH": "spark; rm"}, "an SSH target is a name"),
    ({"BENCH_HOST_SPARK_CORES": "five"}, "isn't cores"),
    ({"BENCH_HOST_SPARK_CORES": "9-5"}, "runs backwards"),
    ({"BENCH_HOST_SPARK_CORES": "5"}, "holds no bench command"),
    ({"BENCH_HOST_SPARK_MEMORY": "2g"}, "holds no bench command"),
    ({"BENCH_HOST_SPARK_FLOOR": "lots"}, "isn't a size"),
    ({"BENCH_HOSTS": "here"}, "not 'here'"),
    ({"BENCH_HOSTS": "spark,spark"}, "twice"),
])
def test_a_host_its_settings_dont_state_rightly_is_refused(change: dict[str, str],
                                                           said: str) -> None:
    with pytest.raises(ValueError, match=said):
        stated({**SETTINGS, **change})


def test_cores_are_read_and_said_as_cpuset_takes_them() -> None:
    assert cores_of("5-9,15-19") == (5, 6, 7, 8, 9, 15, 16, 17, 18, 19)
    assert cores_of("3, 1,2") == (1, 2, 3)
    assert cpuset((5, 6, 7, 8, 9, 15, 16, 17, 18, 19)) == "5-9,15-19"
    assert cpuset([7, 5]) == "5,7"


# --------------------------------------------------------------------------
# A job there
# --------------------------------------------------------------------------


def test_a_job_there_is_the_same_docker_run_but_for_its_mounts_and_its_cores(
    tmp_path: Path,
) -> None:
    """The same limits, user and no network: only where /work and the code come from, the
    Docker that runs it, and its cores pinned, differ."""
    words = host.bench_command(["load", "a.json"])
    here = host.container(tmp_path, words, "print-bench-x", image=host.CAE_IMAGE)
    there = host.container(tmp_path, words, "print-bench-x", image=host.CAE_IMAGE,
                           docker=("docker", "-H", "ssh://spark"), mounts=("w", "c"),
                           cpuset="5,6")
    assert there[:3] == ["docker", "-H", "ssh://spark"]
    assert there[there.index("--cpuset-cpus") + 1] == "5,6"
    rest = there[3:]
    del rest[rest.index("--cpuset-cpus"):rest.index("--cpuset-cpus") + 2]
    assert rest[rest.index("-v") + 1] == "w:/work"
    assert rest[rest.index("-v") + 3] == f"c:{host.MOUNT}:ro"
    differ = [(a, b) for a, b in zip(here[1:], rest, strict=True) if a != b]
    assert differ == [(f"{tmp_path.resolve()}:/work", "w:/work"),
                      (f"{host.HERE}:{host.MOUNT}:ro", f"c:{host.MOUNT}:ro")]
    for word in ("--network", "--user", "--cpus", "--memory", "--read-only", "--cap-drop"):
        assert word in rest


def test_a_job_sends_its_files_and_the_code_and_brings_back_what_it_saved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    job = _job(tmp_path / "job")
    done = there.run(job, (2, 3))
    assert done.returncode == 0 and done.host == "spark"
    assert done.saved and all((job.folder / f).exists() for f in done.saved)
    record = json.loads((job.folder / done.saved[1]).read_text())
    assert record["ran"]["host"] == "spark", "its record names the host"
    assert record["ran"]["image_id"] == IDS[host.CAE_IMAGE]
    log = _log(root)
    (extracted,) = [e["extracted"] for e in log if "extracted" in e]
    owners = {name: (uid, gid) for name, uid, gid in extracted}
    mine = (os.getuid(), os.getgid())
    assert owners["work"] == owners["work/meshes/ring.stl"] == mine, "its user's, as here"
    assert owners["code"] == owners["code/host.py"] == (0, 0)
    assert "code/remote.py" in owners and not any("__pycache__" in n for n in owners)
    (ran,) = [e["run"] for e in log if e.get("command", [""])[0] == host.CAE_PYTHON]
    assert ran[ran.index("--cpuset-cpus") + 1] == "2-3"
    assert ran[ran.index("--network") + 1] == "none"
    assert ran[ran.index("--user") + 1] == f"{mine[0]}:{mine[1]}"
    assert any(v.endswith(f"-code:{host.MOUNT}:ro") for v in ran if v.startswith("print-bench"))
    assert "BENCH_HOST=spark" in ran
    assert not list((root / "volumes").iterdir()), "its volumes are removed as it ends"


def test_its_volumes_are_removed_however_it_ends(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    job = _job(tmp_path / "job")
    (job.folder / "bar.step").unlink()
    with pytest.raises(FileNotFoundError, match="aren't in"):
        there.run(job, (0, 1))
    job = _job(tmp_path / "job")
    raw = json.loads((job.folder / "study-assembly.json").read_text())
    raw["parts"].append({"name": "post", "file": "post.step"})
    (job.folder / "study-assembly.json").write_text(json.dumps(raw))
    done = there.run(job, (0, 1))
    assert done.returncode == 2 and "post.step wasn't sent" in done.stdout
    assert not list((root / "volumes").iterdir())
    assert there.held() == set()


def test_a_host_that_stops_answering_mid_job_is_lost_and_its_volumes_said(
    tmp_path: Path,
) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "drop").write_text("")
    with pytest.raises(HostLost, match=r"failed `load .*` \(exit 255\): ssh: connect"):
        there.run(_job(tmp_path / "job"), (0, 1))
    assert len(there.left) == 2 and all(v.startswith("print-bench-") for v in there.left)


def test_it_touches_nothing_on_the_host_but_its_own(tmp_path: Path) -> None:
    """Every container and volume it names is its own, `print-bench-` and a new id; it never
    asks Docker about another's (no exec, inspect, logs or stop of one), and never runs one
    with a network."""
    root = tmp_path / "spark"
    there = _remote(root)
    there.reserve(3072)
    name = there.reserve(3072)
    there.run(_job(tmp_path / "job"), (0, 1), name)
    there.stop()
    for entry in _log(root):
        for key in ("rm", "names"):
            assert all(n.startswith("print-bench-") for n in entry.get(key, []))
        if "kill" in entry:
            assert entry["kill"].startswith("print-bench-")
        if "run" in entry:
            argv = entry["run"]
            assert argv[argv.index("--name") + 1].startswith("print-bench-")
            assert argv[argv.index("--network") + 1] == "none"
    asked = [e["argv"] for e in _log(root) if "argv" in e]
    assert {a[0] for a in asked} <= {"version", "image", "volume", "run", "rm", "kill", "stats",
                                     "ps"}
    assert all(a[1:3] == ["--filter", "name=print-bench-"] for a in asked if a[0] == "ps")
    assert all(a[:2] == ["image", "inspect"] for a in asked if a[0] == "image"), "its images"


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_host_whose_images_arent_this_ones_by_id_is_refused(tmp_path: Path) -> None:
    assert _remote(tmp_path / "a").refused() is None
    other = {**IDS, host.CAE_IMAGE: "sha256:" + "d" * 64}
    why = _remote(tmp_path / "b", images=other).refused()
    assert why is not None and f"{host.CAE_IMAGE} is sha256:dddddddddddd" in why
    assert "scripts/bench.py send spark" in why
    why = _remote(tmp_path / "c", images={host.CAE_IMAGE: IDS[host.CAE_IMAGE]}).refused()
    assert why is not None and f"{host.IMAGE} is none" in why
    down = _remote(tmp_path / "d")
    (tmp_path / "d" / "down").write_text("")
    assert "doesn't answer" in (down.refused() or "")


def test_a_job_isnt_started_where_memfree_is_under_its_floor_plus_the_job(
    tmp_path: Path,
) -> None:
    root = tmp_path / "spark"
    there = _remote(root, memory="15g")
    _meminfo(root, free_gib=12.5, available_gib=90)
    with pytest.raises(NoRoom, match="MemFree is 12.5 GiB, under its floor of 10.0 GiB plus "
                                     "the job's 3.0 GiB"):
        there.reserve(3072)
    _meminfo(root, free_gib=15.5)
    first = there.reserve(3072)
    with pytest.raises(NoRoom, match="3.0 GiB this study's other jobs there may take yet"):
        there.reserve(3072)
    there.release(first)
    assert there.reserve(3072)


def test_what_its_running_jobs_hold_already_is_out_of_memfree(tmp_path: Path) -> None:
    """A job of 3g holding 1 GiB now (as `docker stats` says) may take 2 GiB more."""
    root = tmp_path / "spark"
    there = _remote(root)
    name = there.reserve(3072)
    (root / "running").mkdir(exist_ok=True)
    (root / "running" / name).write_text("3g")
    _meminfo(root, free_gib=14.9)
    with pytest.raises(NoRoom):
        there.reserve(3072)
    _meminfo(root, free_gib=15.0)
    assert there.reserve(3072)


# --------------------------------------------------------------------------
# Placed across hosts
# --------------------------------------------------------------------------


def test_jobs_are_placed_across_hosts_each_within_its_budget(tmp_path: Path) -> None:
    """Here holds one load of 3g, the remote two, each pinned to cores of its own; a slice
    runs only here."""
    root = tmp_path / "spark"
    (root / "volumes").mkdir(parents=True)
    (root / "delay").write_text("0.3")
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_slow_here(0.3))
    seen: list[tuple[str, tuple[int, ...]]] = []
    lock = threading.Lock()
    now = {"here": 0, "spark": 0, "most": 0}

    def one(k: int) -> JobResult:
        with pool.take(3072, 2, kind="load standing") as cores:
            where = str(pool.where())
            with lock:
                seen.append((where, cores))
                now[where] += 1
                now["most"] = max(now["most"], now["here"] + now["spark"])
                assert now["here"] <= 1 and now["spark"] <= 2
            try:
                return pool.run(_job(tmp_path / f"job-{k}"))
            finally:
                with lock:
                    now[where] -= 1

    threads = [threading.Thread(target=one, args=(k,)) for k in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert now["most"] == 3 and pool.most == 3
    assert {w for w, _ in seen} == {"here", "spark"}
    assert all(c == () for w, c in seen if w == "here")
    assert all(len(c) == 2 and set(c) <= {0, 1, 2, 3} for w, c in seen if w == "spark")
    with pool.take(cpus=3) as cores:
        assert pool.where() == "here" and cores == ()
    said = pool.said()
    assert "spark (spark): cores 0-3, 6.0 GiB for jobs" in said
    assert "2 bench commands of 3g at once, each pinned to its own cores" in said
    assert not list((root / "volumes").iterdir())


def _slow_here(seconds: float) -> Any:
    def here(job: BenchJob) -> JobResult:
        time.sleep(seconds)
        return _here(job)
    return here


def test_the_fastest_host_for_a_kind_is_preferred_as_measured(tmp_path: Path) -> None:
    """Each host is tried for a kind before it's measured; then, where both have room, the
    one that read it faster takes it."""
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "delay").write_text("0.0")
    pool = Pool(Slots(Budget(8 * 1024, 4)), [there], here_runner=_slow_here(1.5))

    def where(kind: str, k: int) -> str:
        with pool.take(3072, 2, kind=kind):
            name = str(pool.where())
            pool.run(_job(tmp_path / f"{kind}-{k}"))
        return name

    assert [where("load a", k) for k in range(4)] == ["here", "spark", "spark", "spark"]
    assert pool.places[0].mean("load a") is not None
    assert where("load b", 0) == "here", "a kind not measured here yet is tried here first"
    (root / "delay").write_text("3.0")
    assert where("load c", 0) == "here"
    assert where("load c", 1) == "spark"
    assert where("load c", 2) == "here", "here read it faster"


def test_a_host_refused_for_its_memory_is_passed_over_and_asked_again(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    _meminfo(root, free_gib=11.0)
    pool = Pool(Slots(Budget(3 * 1024, 2)), [there], here_runner=_here)
    here_full = threading.Event()
    let_go = threading.Event()

    def hold_here() -> None:
        with pool.take(3072, 2, kind="load a"):
            here_full.set()
            let_go.wait(timeout=30)

    holding = threading.Thread(target=hold_here)
    holding.start()
    here_full.wait(timeout=30)
    threading.Timer(1.0, let_go.set).start()
    with pool.take(3072, 2, kind="load b"):
        assert pool.where() == "here", "spark's MemFree held none: it waited"
    holding.join()
    assert pool.places[1].refusals == 1, "asked once, then not again until a job ended"
    assert "spark's free memory held no more 1 times" in pool.said()
    pool.places[1].no_room_until = 0.0
    _meminfo(root, free_gib=30.0)
    with pool.take(3072, 2, kind="load c"), pool.take(3072, 2, kind="load d"):
        assert pool.where() == "spark", "its MemFree holds one now"


def test_a_job_runs_only_in_the_slot_its_thread_holds(tmp_path: Path) -> None:
    pool = Pool(Slots(Budget(8 * 1024, 4)), [], here_runner=_here)
    with pytest.raises(RuntimeError, match="take one"):
        pool.run(_job(tmp_path / "job"))


def test_no_host_holds_a_job_past_every_budget() -> None:
    pool = Pool(Slots(Budget(2 * 1024, 4)), [], here_runner=_here)
    with pytest.raises(ValueError, match="past the budget"), pool.take(3072, 2, kind="load"):
        pass


# --------------------------------------------------------------------------
# The same study, here and across hosts
# --------------------------------------------------------------------------


def _script() -> Model:
    return Model([THREE_WALLS], [FOUR_WALLS], [BOLT], [WAIT], [WAIT], [WAIT],
                 [("recommend", {"candidate": "c1", "reason": "it passes for the least time"})])


def _without_budget(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.startswith("Read within the host's budget"))


def _study(part: StudyPart, tmp_path: Path, k: int,  # noqa: F811
           slots: Slots | Pool, runner: Any) -> tuple[str, list[str | None]]:
    part.slots = slots
    part.loading = BenchLoad(tmp_path / "assembly.json", "bar", runner)
    part.workdir = tmp_path / f"study-{k}"
    result = run(StudyLoop(chat=_script()), part)
    assert result.recommended == "c1"
    hosts = [c.host for r in result.readings for c in r.cases]
    return table(result, "bar", [c.name for c in CASES]), hosts


def test_the_same_study_read_here_and_across_hosts_gives_the_same_table(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    root = tmp_path / "spark"
    alone, hosts_alone = _study(part, tmp_path, 0, Slots(Budget(4 * 1024, 4)), _here)
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_slow_here(0.05))
    across, hosts_across = _study(part, tmp_path, 1, pool, pool.run)
    assert set(hosts_alone) == {"here"}
    assert set(hosts_across) == {"here", "spark"}, "each record names the host that read it"
    assert _without_budget(alone) == _without_budget(across)
    assert "spark ran" in across
    assert not list((root / "volumes").iterdir())


def test_a_case_whose_host_stopped_answering_is_read_again_elsewhere(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    root = tmp_path / "spark"
    alone, _ = _study(part, tmp_path, 0, Slots(Budget(4 * 1024, 4)), _here)
    there = _remote(root)
    pool = Pool(Slots(Budget(4 * 1024, 4)), [there], here_runner=_slow_here(0.05))
    (root / "drop").write_text("")
    across, hosts = _study(part, tmp_path, 1, pool, pool.run)
    assert set(hosts) == {"here"}, "spark went down at its first job, and took no more"
    assert _without_budget(alone) == _without_budget(across)
    assert "spark took no more jobs" in across


# --------------------------------------------------------------------------
# Images, and the hosts command
# --------------------------------------------------------------------------


def test_images_are_sent_where_theirs_differ_and_checked_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "spark"
    there = _remote(root, images={host.IMAGE: IDS[host.IMAGE]})
    (root / "given.json").write_text(json.dumps(IDS))
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.setattr(remote, "LOCAL_DOCKER", (sys.executable, str(FAKE), str(here)))
    monkeypatch.setattr(host, "image_gb", lambda image=host.IMAGE: 3.5)
    assert remote.send(there) == 0
    said = capsys.readouterr().out
    assert f"spark has {host.IMAGE} sha256:bbbbbbbbbbbb, as here" in said
    assert f"sending {host.CAE_IMAGE} sha256:cccccccccccc to spark" in said
    assert json.loads((root / "images.json").read_text()) == IDS
    (root / "given.json").write_text(json.dumps({host.CAE_IMAGE: "sha256:" + "e" * 64}))
    (root / "images.json").write_text(json.dumps({host.IMAGE: IDS[host.IMAGE]}))
    assert remote.send(there) == 1
    assert "not sha256:cccccccccccc" in capsys.readouterr().out


def test_an_image_isnt_sent_where_the_hosts_disk_couldnt_hold_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    there = _remote(tmp_path / "spark", images={})
    monkeypatch.setattr(host, "image_gb", lambda image=host.IMAGE: 3.5)
    assert remote.send(there, (host.CAE_IMAGE,)) == 1
    assert "unknown, with no bench image there to read it" in capsys.readouterr().out


def test_the_hosts_command_checks_each_host_and_starts_no_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "spark"
    fake = _remote(root)
    monkeypatch.setattr(RemoteHost, "docker", lambda self: fake.docker)
    _meminfo(root, free_gib=27.0)
    assert remote.main(["hosts"], SETTINGS) == 0
    said = capsys.readouterr().out
    assert "spark (spark): cores 5-9,15-19, 15.0 GiB for jobs, MemFree kept over 10.0 GiB" in said
    assert "MemFree 27.0 GiB, 5 jobs of 3g over its floor now" in said
    assert not any("extracted" in e for e in _log(root))
    assert remote.main(["hosts"], {}) == 0
    assert remote.main(["send", "pc"], SETTINGS) == 2


# --------------------------------------------------------------------------
# The real remote, opted in
# --------------------------------------------------------------------------


def test_opting_in_to_a_real_remote_takes_its_ssh_target_cores_and_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_bench_remote_live import opted_in_host

    monkeypatch.delenv("PRINT_SHOP_TEST_REMOTE", raising=False)
    assert opted_in_host() is None
    monkeypatch.setenv("PRINT_SHOP_TEST_REMOTE", "spark")
    with pytest.raises(ValueError, match="PRINT_SHOP_TEST_REMOTE_CORES"):
        opted_in_host()
    monkeypatch.setenv("PRINT_SHOP_TEST_REMOTE_CORES", "5-9,15-19")
    monkeypatch.setenv("PRINT_SHOP_TEST_REMOTE_FLOOR", "10g")
    found = opted_in_host()
    assert found is not None and found.ssh == "spark" and found.floor_mb == 10240


def test_the_fake_docker_is_a_program_of_its_own() -> None:
    done = subprocess.run([sys.executable, str(FAKE), "/nonexistent", "version"],
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0 and done.stdout.strip() == "29.0.0"


def _settings_study() -> Any:
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "settings_study.py"
    spec = importlib.util.spec_from_file_location("settings_study_remote", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_study_reads_on_each_host_the_settings_name_that_has_these_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    script = _settings_study()
    here = Slots(Budget(4 * 1024, 4))
    assert script._with_remotes(here, {}) is here, "no hosts named: here alone"
    fake = _remote(tmp_path / "spark")
    monkeypatch.setattr(RemoteHost, "docker", lambda self: fake.docker)
    pool = script._with_remotes(here, SETTINGS)
    assert isinstance(pool, Pool) and [p.name for p in pool.places] == ["here", "spark"]
    assert pool.places[1].slots.budget.at_once(3072) == 5, "15g and 10 cores: five of 3g"
    (tmp_path / "spark" / "images.json").write_text(json.dumps({host.IMAGE: IDS[host.IMAGE]}))
    assert script._with_remotes(here, SETTINGS) is here
    assert f"not reading on spark: spark's {host.CAE_IMAGE} is none" in capsys.readouterr().err


# --------------------------------------------------------------------------
# The cold review's fixes
# --------------------------------------------------------------------------


def test_dockers_own_failure_is_the_hosts_never_the_parts(tmp_path: Path) -> None:
    """Docker's own exit codes (125: cores the host hasn't) are a host failure, though its
    Docker still answers: the job may run elsewhere, never read as the part's NO ANSWER."""
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "ncpu").write_text("4")
    with pytest.raises(HostLost, match="Requested CPUs are not available"):
        there.run(_job(tmp_path / "job"), (5, 6))
    assert there.answers()
    assert not list((root / "volumes").iterdir())


def test_no_reply_at_all_is_the_hosts_failure(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "silent").write_text("")
    with pytest.raises(HostLost, match="no reply"):
        there.run(_job(tmp_path / "job"), (0, 1))


def test_a_case_docker_refused_there_is_read_here_and_the_host_takes_no_more(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    root = tmp_path / "spark"
    alone, _ = _study(part, tmp_path, 0, Slots(Budget(4 * 1024, 4)), _here)
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root, cores="4-7")],
                here_runner=_slow_here(0.05))
    (root / "ncpu").write_text("4")
    across, hosts = _study(part, tmp_path, 1, pool, pool.run)
    assert set(hosts) == {"here"}
    assert _without_budget(alone) == _without_budget(across)
    assert "spark took no more jobs" in across and "Requested CPUs" in across


def test_a_job_that_runs_past_its_limit_there_is_read_again_elsewhere(
    part: StudyPart, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    """A slower host mustn't turn a part's answer into NO ANSWER: a job stopped there on time
    alone is read again on another host, and that host stays in use."""
    root = tmp_path / "spark"
    alone, _ = _study(part, tmp_path, 0, Slots(Budget(4 * 1024, 4)), _here)
    monkeypatch.setattr(host, "timeout_for", lambda args, folder=None: 1)
    (root / "volumes").mkdir(parents=True)
    (root / "delay").write_text("3")
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here)
    across, hosts = _study(part, tmp_path, 1, pool, pool.run)
    assert set(hosts) == {"here"}
    assert _without_budget(alone) == _without_budget(across)
    assert not pool.places[1].down, "slower, not lost"


def test_every_bench_container_on_the_host_counts_at_its_limit(tmp_path: Path) -> None:
    """Another study's, or a run by hand: its name and its memory label only, never inspected;
    one with no label counts at the most a bench command takes."""
    root = tmp_path / "spark"
    there = _remote(root)
    _meminfo(root, free_gib=19.5)
    (root / "others").write_text("print-bench-aaaaaaaaaaaa 3072\nprint-bench-bbbbbbbbbbbb\n"
                                 "llm-server 99999\n")
    with pytest.raises(NoRoom, match="7.0 GiB other bench containers there hold"):
        there.reserve(3072)
    (root / "others").write_text("print-bench-aaaaaaaaaaaa 3072\n")
    assert there.reserve(3072)
    asked = [e["argv"] for e in _log(root) if "argv" in e and e["argv"][0] == "ps"]
    assert asked and all("name=print-bench-" in a for a in asked)


def test_a_stop_during_the_upload_starts_no_job(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "slow_in").write_text("1.0")
    threading.Timer(0.3, there.stop).start()
    with pytest.raises(RuntimeError, match="stopped"):
        there.run(_job(tmp_path / "job"), (0, 1))
    assert not [e for e in _log(root) if e.get("command", [""])[0] == host.CAE_PYTHON]
    assert not list((root / "volumes").iterdir())


def test_a_job_there_has_a_deadline_of_its_own_and_runs_by_image_id(tmp_path: Path) -> None:
    """It can't outlive its study: `timeout` past its longest limit kills it in its container,
    whatever became of the Mac. And it runs the image by the ID that was checked, not by a tag
    that could since have moved."""
    root = tmp_path / "spark"
    there = _remote(root)
    job = _job(tmp_path / "job")
    there.run(job, (0, 1))
    (ran,) = [e["run"] for e in _log(root) if host.CAE_PYTHON in e.get("run", [])]
    at = ran.index(IDS[host.CAE_IMAGE])
    assert ran[at + 1:at + 4] == ["timeout", "-s", "KILL"]
    assert int(ran[at + 4]) == job.timeout + remote.DEADLINE_MARGIN, "past its own limit"
    assert ran[at + 5] == host.CAE_PYTHON
    helpers = [e["run"] for e in _log(root) if "run" in e and e["image"] != IDS[host.CAE_IMAGE]]
    assert helpers and all(e[-1] != host.IMAGE for e in helpers)
    assert {e["image"] for e in _log(root) if "run" in e} <= set(IDS.values())


def test_a_tag_moved_there_since_its_check_is_a_host_failure(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    assert there.refused() is None
    (root / "images.json").write_text(json.dumps({**IDS, host.CAE_IMAGE: "sha256:" + "f" * 64}))
    with pytest.raises(HostLost, match="No such image"):
        there.run(_job(tmp_path / "job"), (0, 1))


def test_a_job_isnt_started_where_the_hosts_disk_is_under_its_floor(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "df_free").write_text(str(int((host.FLOOR_GB + remote.WORK_GB - 0.5) * host.GB)))
    with pytest.raises(NoRoom, match="Docker disk"):
        there.reserve(3072)
    (root / "df_free").write_text(str(int((host.FLOOR_GB + remote.WORK_GB + 0.5) * host.GB)))
    assert there.reserve(3072)


def test_a_work_folder_past_its_bound_is_said(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    (root / "du_k").write_text(str(int(remote.WORK_GB * host.GB / 1024) + 10))
    there.run(_job(tmp_path / "job"), (0, 1))
    assert there.over and "past its" in there.over[0]


def test_a_study_says_the_volumes_it_left_as_it_ends(capsys: pytest.CaptureFixture[str],
                                                     tmp_path: Path) -> None:
    script = _settings_study()
    there = _remote(tmp_path / "spark")
    there.left.append("print-bench-aaaaaaaaaaaa-work")
    script._say_left(Pool(Slots(Budget(4 * 1024, 4)), [there]))
    assert "left on spark: print-bench-aaaaaaaaaaaa-work" in capsys.readouterr().err
    import signal

    assert script._interrupted is not None
    with pytest.raises(KeyboardInterrupt):
        script._interrupted(signal.SIGTERM, None)


def test_a_kill_at_the_limit_doesnt_wait_for_ever(monkeypatch: pytest.MonkeyPatch,
                                                  tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    real = subprocess.run

    def run(argv: list[str], **kw: Any) -> Any:
        if argv[-2:-1] == ["kill"]:
            calls.append(kw)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, **kw)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(host, "_waited", lambda argv, timeout, longer: (None, timeout, False))
    host.run(["load", "a.json"], tmp_path, timeout=1, docker=("true",))
    assert calls and calls[0].get("timeout")


def test_a_candidates_cases_are_all_read_on_one_host(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    """The Spark's processors can round a figure's last binary digit otherwise than the Mac's
    (a bolt's sheared area, read on both): so the cases a candidate is compared by are read on
    one host, whichever it is, while candidates spread across hosts."""
    root = tmp_path / "spark"
    (root / "volumes").mkdir(parents=True)
    (root / "delay").write_text("0.2")
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_slow_here(0.2))
    part.slots = pool
    part.loading = BenchLoad(tmp_path / "assembly.json", "bar", pool.run)
    result = run(StudyLoop(chat=_script()), part)
    by_candidate = {r.candidate.name: {c.host for c in r.cases} for r in result.readings}
    assert all(len(hosts) == 1 for hosts in by_candidate.values()), by_candidate
    assert set().union(*by_candidate.values()) == {"here", "spark"}
    assert "each candidate's cases on one host" in result.budget


def test_a_group_whose_host_went_down_moves_and_says_so(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here)
    with pool.take(3072, 2, kind="load a"), pool.take(3072, 2, kind="load a", group="c1"):
        assert pool.where() == "spark"
    pool.places[1].down = "it stopped answering"
    with pool.take(3072, 2, kind="load b", group="c1"):
        assert pool.where() == "here"
    assert "c1's cases were read on more than one host: here, spark" in pool.said()


# --------------------------------------------------------------------------
# The re-review's fixes
# --------------------------------------------------------------------------


def _beside(root: Path) -> Remote:
    """Another process's remote, on the same made-up host."""
    found = stated({"BENCH_HOSTS": "spark", "BENCH_HOST_SPARK_SSH": "spark",
                    "BENCH_HOST_SPARK_CORES": "0-3", "BENCH_HOST_SPARK_MEMORY": "6g",
                    "BENCH_HOST_SPARK_FLOOR": "10g"})[0]
    return Remote(found, (sys.executable, str(FAKE), str(root)))


def test_a_job_holds_its_memory_on_the_host_from_its_reservation_to_its_end(
    tmp_path: Path,
) -> None:
    """From the moment it's reserved, through its files going over, to its end, another
    process counts the job at its whole limit: by one labelled container of its own."""
    root = tmp_path / "spark"
    there = _remote(root)
    _meminfo(root, free_gib=15.5)
    (root / "slow_in").write_text("1.5")
    name = there.reserve(3072)
    with pytest.raises(NoRoom, match="3.0 GiB other bench containers there hold"):
        _beside(root).reserve(3072)
    ran = threading.Thread(target=there.run, args=(_job(tmp_path / "job"), (0, 1), name))
    ran.start()
    time.sleep(0.6)
    with pytest.raises(NoRoom, match="3.0 GiB other bench containers there hold"):
        _beside(root).reserve(3072)
    ran.join()
    assert _beside(root).reserve(3072), "its hold ended with it"


def test_a_candidate_is_never_set_on_a_host_that_cant_hold_its_largest_case(
    tmp_path: Path,
) -> None:
    """A candidate whose largest case is a 4g `load --parts` isn't set on a host of 3g, though
    its first case of 3g would fit there: it waits for a host that holds all of them."""
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(8 * 1024, 2)), [_remote(root, memory="3g", cores="0-1")],
                here_runner=_here)
    here_full = threading.Event()

    def hold_here() -> None:
        with pool.take(3072, 2, kind="x"):
            here_full.set()
            time.sleep(0.5)

    holding = threading.Thread(target=hold_here)
    holding.start()
    here_full.wait(timeout=30)
    with pool.take(3072, 2, kind="load a", group="c1", group_mb=4096):
        assert pool.where() == "here"
    with pool.take(4096, 2, kind="load b --parts", group="c1", group_mb=4096):
        assert pool.where() == "here"
    holding.join()


def test_a_placement_that_fails_is_never_the_parts_answer(
    part: StudyPart, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from print_shop.bench.pool import NoPlace

    pool = Pool(Slots(Budget(4 * 1024, 4)), [], here_runner=_here)
    real = pool.take

    def take(memory_mb: int = 0, cpus: int = 1, kind: str | None = None,
             **more: Any) -> Any:
        if kind is not None and "--lift" in kind:
            raise NoPlace("no host takes a job of 3072 MiB: made up")
        return real(memory_mb, cpus, kind, **more)

    monkeypatch.setattr(pool, "take", take)
    part.slots = pool
    part.loading = BenchLoad(tmp_path / "assembly.json", "bar", pool.run)
    result = run(StudyLoop(chat=_script()), part)
    for reading in result.readings:
        assert reading.error.startswith("not read: no host could take"), reading.error
        assert not any(c.verdict == "NO ANSWER" for c in reading.cases)


def test_a_job_stopped_before_it_started_is_cut_off_not_the_parts(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    from print_shop.bench.job import Stopped
    from print_shop.study.candidate import Candidate
    from print_shop.study.loop import CUT_OFF, read_candidate

    def stopped(*_: Any) -> dict[str, Any]:
        raise Stopped("the study was stopped before it started")

    part.loading = stopped
    reading = read_candidate(part, Candidate("c1", part.defaults))
    assert reading.error == CUT_OFF, [c.why for c in reading.cases]


def test_every_volume_is_labelled_and_stale_ones_are_cleared_before_a_study(
    tmp_path: Path,
) -> None:
    """Only print-shop's, past the longest deadline, whose job's container is gone; each one
    said."""
    root = tmp_path / "spark"
    there = _remote(root)
    there.run(_job(tmp_path / "job"), (0, 1))
    made = [e for e in _log(root) if e.get("volume") == "create"]
    assert made and all({"print-shop.bench.job", "print-shop.bench.created"}
                        <= set(e["labels"]) for e in made)
    old = int(time.time()) - remote.STALE_S - 60
    for name, job, created in (("print-bench-aaaaaaaaaaaa-work", "print-bench-aaaaaaaaaaaa", old),
                               ("print-bench-bbbbbbbbbbbb-work", "print-bench-bbbbbbbbbbbb", old),
                               ("print-bench-cccccccccccc-work", "print-bench-cccccccccccc",
                                int(time.time()))):
        (root / "volumes" / name).mkdir()
        (root / "volumes" / f"{name}.labels").write_text(json.dumps(
            {"print-shop.bench.job": job, "print-shop.bench.created": str(created)}))
    (root / "volumes" / "model-weights").mkdir()
    (root / "running").mkdir(exist_ok=True)
    (root / "running" / "print-bench-bbbbbbbbbbbb").write_text("3072")
    assert there.clear_stale() == (["print-bench-aaaaaaaaaaaa-work"], [])
    left = sorted(p.name for p in (root / "volumes").iterdir() if p.is_dir())
    assert left == ["model-weights", "print-bench-bbbbbbbbbbbb-work",
                    "print-bench-cccccccccccc-work"]


def test_a_job_is_killed_where_the_hosts_disk_falls_under_its_floor_as_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    monkeypatch.setattr(remote, "DISK_LOOK", 0.2)
    (root / "delay").write_text("5")
    threading.Timer(0.8, lambda: (root / "df_free").write_text(str(int(1 * host.GB)))).start()
    began = time.monotonic()
    with pytest.raises(HostLost, match="Docker disk fell to 1.0 GB"):
        there.run(_job(tmp_path / "job"), (0, 1))
    assert time.monotonic() - began < 4, "killed, not waited for"
    assert any(e.get("kill") for e in _log(root))


def test_other_processes_jobs_count_at_their_disk_bound(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    room = host.FLOOR_GB + 2 * remote.WORK_GB + 0.5
    (root / "df_free").write_text(str(int(room * host.GB)))
    assert there.reserve(3072), "its own and one more"
    (root / "others").write_text("print-bench-aaaaaaaaaaaa 3072\n")
    with pytest.raises(NoRoom, match="Docker disk"):
        _beside(root).reserve(3072)


def test_every_run_there_never_pulls(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    there.reserve(3072)
    there.run(_job(tmp_path / "job"), (0, 1))
    runs = [e["argv"] for e in _log(root) if e.get("argv", [""])[0] == "run"]
    assert runs and all(a[1:3] == ["--pull", "never"] for a in runs)


# --------------------------------------------------------------------------
# The third review's clean-ups
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fails", ["hold", "count"])
def test_a_reservation_that_fails_after_its_hold_leaves_nothing_held(
    tmp_path: Path, fails: str,
) -> None:
    """SSH failing as the hold is made (made, then no reply), or as the count is read again
    after: the reservation is let go and its hold removed, by its own name, before it's raised."""
    root = tmp_path / "spark"
    there = _remote(root)
    (root / f"{fails}_fails").write_text("")
    with pytest.raises(HostLost):
        there.reserve(3072)
    assert there.held() == set() and not there._holds
    removed = [n for e in _log(root) for n in e.get("rm", []) if n.endswith("-hold")]
    assert removed and not [p for p in (root / "running").iterdir() if p.name.endswith("-hold")]


def test_clear_stale_says_what_it_removed_and_what_it_couldnt(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    old = str(int(time.time()) - remote.STALE_S - 60)
    for job in ("print-bench-aaaaaaaaaaaa", "print-bench-bbbbbbbbbbbb"):
        (root / "volumes" / f"{job}-work").mkdir()
        (root / "volumes" / f"{job}-work.labels").write_text(json.dumps(
            {"print-shop.bench.job": job, "print-shop.bench.created": old}))
    (root / "rm_fails").write_text("print-bench-bbbbbbbbbbbb-work")
    assert there.clear_stale() == (["print-bench-aaaaaaaaaaaa-work"],
                                   ["print-bench-bbbbbbbbbbbb-work"])
