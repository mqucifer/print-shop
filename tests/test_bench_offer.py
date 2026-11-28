"""Specification for a `load` whose read met its container's memory, read again on a stated host
with more, as the same reading (mqucifer/print-shop#380).

The remote is made up: `tests/fake_docker.py` stands in for `docker -H ssh://HOST`, and, where a
test says so, for this host's own `docker` too. Its `load` of an assembly file that plants
`need_mb` meets a container given less memory, saying so as `load` does (`bench: met ...`), and
reads as any other under more. Docker and SSH are never asked.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import cae, host, job, load, progress, remote, report
from print_shop.bench.budget import Budget, Slots
from print_shop.bench.job import HERE, BenchJob, JobResult, Met, load_files, saved_files
from print_shop.bench.pool import Offers, Pool
from print_shop.bench.remote import Remote, RemoteHost, stated
from print_shop.bench.report import NoAnswer, Report
from print_shop.rounds.box import Box, Ran
from print_shop.study.loop import StudyLoop, StudyPart, run
from print_shop.study.reading import BenchLoad
from print_shop.study.table import table
from tests import fake_docker
from tests.test_bench_remote import (
    FAKE,
    IDS,
    _job,
    _log,
    _meminfo,
    _these_images,  # noqa: F401 - an autouse fixture, here too
    _without_budget,
)
from tests.test_settings_study import (  # noqa: F401 - `part` is a fixture
    BOLT,
    CASES,
    FOUR_WALLS,
    THREE_WALLS,
    WAIT,
    Model,
    part,
)

GIB = 2**30
#: A host as the settings state it, with a larger memory for a load.
SETTINGS = {
    "BENCH_HOSTS": "spark",
    "BENCH_HOST_SPARK_SSH": "spark",
    "BENCH_HOST_SPARK_CORES": "0-3",
    "BENCH_HOST_SPARK_MEMORY": "15g",
    "BENCH_HOST_SPARK_FLOOR": "10g",
    "BENCH_HOST_SPARK_LOAD_MEMORY": "8g",
}
#: What the planted read needs, in MiB: past a load's own 3g and 4g, within 8g.
NEED_MB = 6000


def _remote(root: Path, settings: dict[str, str] | None = None) -> Remote:
    root.mkdir(parents=True, exist_ok=True)
    (root / "volumes").mkdir(exist_ok=True)
    (root / "images.json").write_text(json.dumps(IDS))
    found = stated(settings or SETTINGS)[0]
    return Remote(found, (sys.executable, str(FAKE), str(root)))


def _planted(folder: Path, case: tuple[str, ...] = CASES[0].args) -> BenchJob:
    """A study's `load` whose read needs `NEED_MB`."""
    made = _job(folder, case=case)
    raw = json.loads((folder / "study-assembly.json").read_text())
    raw["need_mb"] = NEED_MB
    (folder / "study-assembly.json").write_text(json.dumps(raw))
    return made


def _here(memory: str | None = None) -> Any:
    """This host's runner, made up: the fake's `load` over the job's own folder, in its
    container's memory (the job's own, or `memory`), saying what it met as `load` would."""

    def here(given: BenchJob) -> JobResult:
        limit = memory or given.memory
        code, said = fake_docker.bench(list(given.args), given.folder, {"BENCH_HOST": HERE},
                                       limit)
        line = fake_docker.met(list(given.args), given.folder, limit)
        return JobResult(code, said, "", saved_files(said), met=Met.of([line] if line else []))

    return here


def _record(folder: Path, done: JobResult) -> dict[str, Any]:
    saved = next(f for f in done.saved if f.endswith(".json"))
    found: dict[str, Any] = json.loads((folder / saved).read_text())
    return found


def _runs(root: Path) -> list[list[str]]:
    return [e["run"] for e in _log(root) if "run" in e and e.get("command", [""])[0] != "sh"
            and "tar" not in e.get("command", [])]


# --------------------------------------------------------------------------
# What a read met, as `load` says it and the host reads it
# --------------------------------------------------------------------------


def test_the_larger_memory_is_stated_in_the_hosts_settings_never_guessed() -> None:
    assert stated(SETTINGS)[0].load_mb == 8 * 1024
    without = {k: v for k, v in SETTINGS.items() if not k.endswith("LOAD_MEMORY")}
    assert stated(without)[0].load_mb == 0, "none stated: nothing is offered there"
    assert "a load that met its memory offered 8.0 GiB" in stated(SETTINGS)[0].said()
    for wrong in ("4g", "3g", "16g"):
        with pytest.raises(ValueError, match="more than a load's own 4g, and within"):
            stated({**SETTINGS, "BENCH_HOST_SPARK_LOAD_MEMORY": wrong})


def test_load_says_once_as_it_ends_that_its_read_met_the_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """Where an unread whole met the memory and the answer is NO ANSWER, `load` says the met
    line with its limit, peak, need, room and step; where two passes then read it whole, it
    says nothing; where the band's lower bound read it, it says it."""
    group = tmp_path / "cgroup"
    group.mkdir()
    (group / "memory.max").write_text(f"{4 * GIB}\n")
    (group / "memory.peak").write_text(f"{4 * GIB - 5}\n")
    monkeypatch.setattr(progress, "CGROUP", group)
    monkeypatch.setattr(progress, "watch", lambda every=progress.EVERY: None)
    # Its scratch folder is the test's own, never /work.
    monkeypatch.setattr(cae, "DISK", tmp_path / "disk")

    def met_then(after: str) -> Any:
        def read(args: Any, given: Report) -> None:
            with progress.step("meshing pass 1's whole"):
                unread = load._Unread("met it", memory=True, need=5 * GIB, room=3 * GIB)
            if after == "raise":
                raise unread
            if after == "bound":
                given.numbers["lower_bound"] = {"parts": ["frame"]}
        return read

    for after, says in (("raise", True), ("whole", False), ("bound", True)):
        monkeypatch.setattr(load, "_read", met_then(after))
        try:
            load.run(None, Report("load", []))
        except NoAnswer:
            assert after == "raise"
        lines = [line for line in capsys.readouterr().err.splitlines()
                 if line.startswith(host.MET_LINE + " ")]
        assert len(lines) == (1 if says else 0), after
        if says:
            met = Met.of(lines)
            assert met == Met(4 * GIB, 4 * GIB - 5, 5 * GIB, 3 * GIB, "meshing pass 1's whole")
    monkeypatch.setattr(load, "_read", lambda args, given: None)
    load.run(None, Report("load", []))
    assert host.MET_LINE not in capsys.readouterr().err, "nothing met, nothing said"


def test_the_host_keeps_the_met_line_and_says_a_killed_command_met_its_memory() -> None:
    said = [f"{host.STEP_LINE} 3.0 meshing the joined parts",
            f"{host.MEMORY_LINE} 9.0 current=1 peak={4 * GIB - 7} anon=1 shmem=0"]
    err, kept = host.progress_lines("".join(f"{s}\n" for s in said)
                                    + f"{host.MET_LINE} 9.5 limit=1 peak=2 need=- room=- at x\n"
                                    + "an error of its own\n")
    assert err == "an error of its own\n" and len(kept) == 3, "the met line isn't passed on"
    met = Met.of([host.killed_met(said, "4g")])
    assert met == Met(4 * GIB, 4 * GIB - 7, None, None, "meshing the joined parts", killed=True)
    assert "the kernel killed it" in met.said() and "of its container's 4.0 GiB" in met.said()


def test_what_it_met_is_told_to_the_host_with_more_and_its_reply_says_both() -> None:
    met = Met(4 * GIB, 4 * GIB - 5, 5 * GIB, 3 * GIB, "meshing pass 1's whole")
    told = report.offered(met.told())
    assert told == {"host": "here", "limit": 4 * GIB, "peak": 4 * GIB - 5, "need": 5 * GIB,
                    "room": 3 * GIB, "killed": False, "at": "meshing pass 1's whole"}
    line = report.offered_said({"host": "spark", "memory_max": str(8 * GIB),
                                "memory_peak": str(6 * GIB), "offered": told})
    assert line == (
        "\noffered: read on spark in its 8.0 GiB, the same command with the same checks, as on "
        "here its read met its container's 4.0 GiB at meshing pass 1's whole: it needed 5.0 GiB "
        "with 3.0 GiB room, at most 4.0 GiB held. On spark it held at most 6.0 GiB of its 8.0 GiB")
    assert report.offered_said({"offered": None}) == ""


def test_a_load_names_every_file_it_reads_beside_its_assembly(tmp_path: Path) -> None:
    (tmp_path / "set").mkdir()
    (tmp_path / "set" / "a.json").write_text(json.dumps({"parts": [
        {"name": "frame", "file": "frame.step", "band": "band.step", "with": ["pane.stl"],
         "print": {"regions": [{"name": "ring", "mesh": "meshes/ring.stl"}]}},
        {"name": "leg", "file": "frame.step"}]}))
    assert load_files(tmp_path, ["load", "set/a.json", "--lift", "frame"]) == [
        "set/a.json", "set/frame.step", "set/pane.stl", "set/band.step", "set/meshes/ring.stl"]
    with pytest.raises(ValueError, match="not a load"):
        load_files(tmp_path, ["info", "x.step"])


# --------------------------------------------------------------------------
# Offered a host with more, as the same reading
# --------------------------------------------------------------------------


def test_a_read_that_meets_a_small_local_limit_reads_on_a_host_with_more_with_the_same_figures(
    tmp_path: Path,
) -> None:
    """The planted read needs 6000 MiB: here, in its 3g, it meets the memory; offered spark's
    8g, it reads, its figures those of the same read here in 8g, its record naming the host, its
    limit and what it met here."""
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    planted = _planted(tmp_path / "offered")
    with pool.take(3072, 2, kind="load standing"):
        assert pool.where() == HERE
        done = pool.run(planted)
    assert done.host == "spark" and done.returncode == 0 and done.met is None
    there = _record(planted.folder, done)
    same = _planted(tmp_path / "here-in-8g")
    alone = _record(same.folder, _here("8g")(same))
    assert there["numbers"] == alone["numbers"] and there["numbers"]
    assert (there["verdict"], there["why"], there["notes"]) == (
        alone["verdict"], alone["why"], alone["notes"])
    assert there["ran"]["host"] == "spark" and there["ran"]["memory_max"] == str(8 * GIB)
    told = report.offered(there["ran"]["offered"])
    assert told is not None and told["host"] == HERE and told["limit"] == 3 * GIB
    assert told["need"] == NEED_MB * 2**20
    (run,) = _runs(root)
    assert run[run.index("--memory") + 1] == "8192m" and "--cpuset-cpus" in run
    said = pool.said()
    assert "was read on spark in its 8.0 GiB" in said and "need 5.9 GiB" in said
    spark = pool.places[1].remote
    assert spark is not None and not spark.held()
    assert not list((root / "volumes").iterdir())


def test_where_no_host_states_more_the_honest_no_answer_stands(tmp_path: Path) -> None:
    without = {k: v for k, v in SETTINGS.items() if not k.endswith("LOAD_MEMORY")}
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root, without)], here_runner=_here())
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE and _record(planted.folder, done)["verdict"] == "NO ANSWER"
    assert _runs(root) == [], "nothing was run there"
    assert "no host states more for a load: its answer stands" in pool.said()


def test_a_host_whose_memfree_holds_no_more_leaves_the_answer_and_says_why(
    tmp_path: Path,
) -> None:
    """Its MemFree under its floor plus the 8g, with nothing of this process's there to end:
    nothing is started, and the first answer stands, said."""
    root = tmp_path / "spark"
    there = _remote(root)
    _meminfo(root, free_gib=17.0)
    pool = Pool(Slots(Budget(4 * 1024, 4)), [there], here_runner=_here())
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE and _record(planted.folder, done)["verdict"] == "NO ANSWER"
    assert _runs(root) == []
    said = pool.said()
    assert "no host with more read it: spark: spark's MemFree is 17.0 GiB, under its floor" in said
    assert "plus the job's 8.0 GiB" in said


def test_a_host_that_fails_the_offered_read_leaves_the_answer_and_takes_no_more(
    tmp_path: Path,
) -> None:
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    (root / "drop").write_text("")
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE and _record(planted.folder, done)["verdict"] == "NO ANSWER"
    assert pool.places[1].down, "it takes no more jobs"
    assert "was offered spark's 8.0 GiB, which failed it" in pool.said()


def test_a_read_that_met_the_memory_on_a_remote_is_offered_more_there(tmp_path: Path) -> None:
    """Placed on spark in a load's own 3g, it met it there; spark states 8g for a load, so it's
    read there again in that."""
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(0, 4)), [_remote(root)], here_runner=_here())
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        assert pool.where() == "spark"
        done = pool.run(planted)
    assert done.host == "spark" and _record(planted.folder, done)["verdict"] != "NO ANSWER"
    first, again = _runs(root)
    assert first[first.index("--memory") + 1] == "3g"
    assert again[again.index("--memory") + 1] == "8192m"
    told = report.offered(_record(planted.folder, done)["ran"]["offered"])
    assert told is not None and told["host"] == "spark"


def test_this_hosts_runner_reads_what_its_command_said_it_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through `host.run` and this host's Docker (the fake here), the job's memory is its
    container's, and the met line is read off its stderr and not passed on."""
    here = tmp_path / "here-docker"
    here.mkdir()
    (here / "images.json").write_text(json.dumps({"tag": host.CAE_IMAGE}))
    monkeypatch.setattr(job, "DOCKER", (sys.executable, str(FAKE), str(here)))
    planted = _planted(tmp_path / "job")
    done = job.run_here(planted)
    assert done.met is not None and done.met.limit == 3 * GIB and done.met.need == NEED_MB * 2**20
    assert host.MET_LINE not in done.stderr
    (run,) = [e["run"] for e in _log(here) if "run" in e]
    assert run[run.index("--memory") + 1] == "3g"
    bigger = job.run_here(dataclasses.replace(planted, memory="8g", offered=done.met))
    assert bigger.met is None and bigger.returncode == 0
    told = report.offered(_record(planted.folder, bigger)["ran"]["offered"])
    assert told is not None and told["limit"] == 3 * GIB


# --------------------------------------------------------------------------
# Studies and rounds
# --------------------------------------------------------------------------


def _study(part: StudyPart, tmp_path: Path, k: int, slots: Slots | Pool,  # noqa: F811
           runner: Any) -> tuple[str, list[str | None]]:
    """The study `tests.test_bench_remote` reads, waited for as its readings land, however many
    times that takes: an offered read lands later."""
    part.slots = slots
    part.loading = BenchLoad(tmp_path / "assembly.json", "bar", runner)
    part.workdir = tmp_path / f"study-{k}"
    recommend = ("recommend", {"candidate": "c1", "reason": "it passes for the least time"})
    result = run(StudyLoop(chat=Model([THREE_WALLS], [FOUR_WALLS], [BOLT],
                                      *([WAIT],) * 8, [recommend], [recommend])), part)
    assert result.recommended == "c1"
    hosts = [c.host for r in result.readings for c in r.cases]
    return table(result, "bar", [c.name for c in CASES]), hosts


def test_a_study_reads_a_case_that_meets_here_on_the_host_with_more(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    """A study whose cases meet here's memory reads them on spark, as the same table as one read
    here with memory enough."""
    assembly = tmp_path / "assembly.json"
    raw = json.loads(assembly.read_text())
    raw["need_mb"] = NEED_MB
    assembly.write_text(json.dumps(raw))
    enough, _ = _study(part, tmp_path, 0, Slots(Budget(16 * 1024, 4)), _here("8g"))
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(8 * 1024, 4)), [_remote(root)], here_runner=_here())
    offered, hosts = _study(part, tmp_path, 1, pool, pool.run)
    assert set(hosts) == {"spark"}, "every case met here's memory and was read there"
    assert _without_budget(offered) == _without_budget(enough)
    assert "was read on spark in its 8.0 GiB" in offered
    assert "as one was offered a host with more memory" in offered
    assert "as one failed them" not in offered


def test_a_rounds_load_that_meets_its_memory_reads_on_the_host_with_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A design round's `load` that met its container's memory (here a made-up reply, as its
    container would give it) is offered spark; its reply there is the round's, with what became
    of it. Offered nothing where none is stated, its reply stands."""
    root = tmp_path / "spark"
    fake = _remote(root)
    monkeypatch.setattr(RemoteHost, "docker", lambda self: fake.docker)
    planted = _planted(tmp_path / "work")
    met = Met(3 * GIB, 3 * GIB - 1, NEED_MB * 2**20, 2 * GIB, "meshing pass 1's whole")
    box = Box(planted.folder)
    monkeypatch.setattr(box, "_run", lambda *a, **k: Ran(
        2, "verdict: NO ANSWER: it met the container's memory\n", 4.0, met=met))
    ran = box.bench(list(planted.args))
    assert "note:" not in ran.output and ran.met == met, "no host stated: here alone"
    offers = Offers(SETTINGS)
    assert offers and [h.name for h in offers.hosts] == ["spark"]
    box.offers = offers
    ran = box.bench(list(planted.args))
    assert ran.code == 0 and "saved: bench-out/" in ran.output
    record = json.loads((planted.folder / saved_files(ran.output)[1]).read_text())
    assert record["ran"]["host"] == "spark" and record["ran"]["memory_max"] == str(8 * GIB)
    (sent,) = [e["extracted"] for e in _log(root) if "extracted" in e]
    assert {name for name, *_ in sent} >= {"work/study-assembly.json", "work/bar.step",
                                           "work/meshes/ring.stl"}
    (root / "images.json").write_text(json.dumps({host.IMAGE: IDS[host.IMAGE]}))
    box.offers = Offers(SETTINGS)
    ran = box.bench(list(planted.args))
    assert ran.code == 2 and "note: spark wasn't offered it:" in ran.output


def test_a_rounds_command_the_kernel_killed_met_its_memory() -> None:
    """The round's own reading of what its container said: the met line set aside with the
    steps, and a killed command's said as met."""
    progress_ = __import__("print_shop.rounds.box", fromlist=["_Progress"])._Progress()
    out = progress_.take(f"verdict: x\n{host.MET_LINE} 1.0 limit=5 peak=4 need=- room=- at y\n"
                         .encode())
    assert out == b"verdict: x\n"
    assert Met.of(progress_.said()) == Met(5, 4, None, None, "y")


def test_the_setting_is_read_from_the_hosts_settings_alone() -> None:
    assert remote.LOAD_MEMORY == "LOAD_MEMORY" and "LOAD_MEMORY" not in remote.KEYS


def test_the_probe_says_where_the_read_went_by_counts_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator's probe (`scripts/load_offer_probe.py`) over a planted read: here, through
    this host's Docker (the fake), it meets the memory; offered spark, it reads there. It prints
    codes, kinds and memory, never a step's words, a name from the assembly or a figure."""
    import importlib.util

    import print_shop.env

    path = Path(__file__).resolve().parents[1] / "scripts" / "load_offer_probe.py"
    spec = importlib.util.spec_from_file_location("load_offer_probe", path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    here = tmp_path / "here-docker"
    here.mkdir()
    (here / "images.json").write_text(json.dumps({"tag": host.CAE_IMAGE}))
    monkeypatch.setattr(job, "DOCKER", (sys.executable, str(FAKE), str(here)))
    fake = _remote(tmp_path / "spark")
    monkeypatch.setattr(RemoteHost, "docker", lambda self: fake.docker)
    monkeypatch.setattr(print_shop.env, "load_env", lambda path=None: dict(SETTINGS))
    planted = _planted(tmp_path / "work")
    monkeypatch.chdir(planted.folder)
    assert probe.main(list(planted.args)) == 0
    out = capsys.readouterr().out
    assert "here: exit 2, verdict NO ANSWER, read nothing" in out
    assert "met the memory: yes, by a step's need against its room; limit 3.00 GiB" in out
    assert "offer: read there" in out
    assert "spark: exit 0, verdict OK, read the whole" in out
    for unsaid in ("meshing", "bar", "ring", "made up", "across"):
        assert unsaid not in out, unsaid
    monkeypatch.setattr(print_shop.env, "load_env", lambda path=None: {})
    assert probe.main(list(planted.args)) == 2
    assert "refused: no host the settings name states" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The cold review's fixes
# --------------------------------------------------------------------------


def _bounded(folder: Path, need_mb: int, bound_up_to_mb: int) -> BenchJob:
    """A planted read that meets the memory under `need_mb`, and then reads a glass band's
    lower bound in a container of at most `bound_up_to_mb`."""
    made = _planted(folder)
    raw = json.loads((folder / "study-assembly.json").read_text())
    raw.update(need_mb=need_mb, bound_up_to_mb=bound_up_to_mb)
    (folder / "study-assembly.json").write_text(json.dumps(raw))
    return made


def test_an_offered_reply_that_reads_no_more_never_replaces_the_first(tmp_path: Path) -> None:
    """A band's bound read here, offered spark's 8g, meets the memory there too and leaves the
    band no time: its NO ANSWER doesn't replace the bound, and both are said."""
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    planted = _bounded(tmp_path / "job", need_mb=9000, bound_up_to_mb=4096)
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE
    assert "lower_bound" in _record(planted.folder, done)["numbers"]
    assert len(_runs(root)) == 1, "it was read there"
    said = pool.said()
    assert ("as NO ANSWER, NO ANSWER: no more than its first answer, OK, a glass band's lower "
            "bound, which stands. There it met its memory too") in said
    assert pool.last_offer is not None
    assert pool.last_offer.outcome == "read there, no more than its first answer"


def test_an_offered_reply_that_reads_the_whole_replaces_a_bound(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    planted = _bounded(tmp_path / "job", need_mb=NEED_MB, bound_up_to_mb=4096)
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == "spark"
    assert "lower_bound" not in _record(planted.folder, done)["numbers"]
    assert "the whole, where its first answer was a glass band's lower bound" in pool.said()


@pytest.mark.parametrize("wrong", ["12", "12000", "0", "lots", "4g", "16g"])
def test_a_stated_load_memory_that_isnt_a_size_over_a_loads_own_is_refused(wrong: str) -> None:
    with pytest.raises(ValueError, match="BENCH_HOST_SPARK_LOAD_MEMORY is"):
        stated({**SETTINGS, "BENCH_HOST_SPARK_LOAD_MEMORY": wrong})


def test_a_rounds_offered_read_has_the_runs_own_limits_of_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spark"
    fake = _remote(root)
    monkeypatch.setattr(RemoteHost, "docker", lambda self: fake.docker)
    planted = _planted(tmp_path / "work")
    met = Met(3 * GIB, 3 * GIB - 1, NEED_MB * 2**20, 2 * GIB, "meshing")
    box = Box(planted.folder, timeout=450)
    box.offers = Offers(SETTINGS)
    monkeypatch.setattr(box, "_run", lambda *a, **k: Ran(2, "verdict: NO ANSWER\n", 4.0, met=met))
    assert box.bench(list(planted.args)).code == 0
    (run,) = _runs(root)
    told = [run[k + 1] for k, word in enumerate(run) if word == "-e"]
    assert "BENCH_TIMEOUT=450" in told, "the run's own limit, as here"
    assert f"{host.OFFER}={host.TWO_PASSES}" in told, "and the hour it's offered, as here"


def test_an_unexpected_error_in_the_offered_read_keeps_the_first_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "spark"
    there = _remote(root)
    pool = Pool(Slots(Budget(4 * 1024, 4)), [there], here_runner=_here())

    def broken(*args: Any, **kwargs: Any) -> JobResult:
        raise OSError("a made-up failure of the read's own")

    monkeypatch.setattr(there, "run", broken)
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE and _record(planted.folder, done)["verdict"] == "NO ANSWER"
    assert "whose read raised OSError: a made-up failure" in pool.said()
    assert not there.held()

    import tarfile

    met = Met(3 * GIB, None, None, None, "meshing")
    box = Box(planted.folder)
    monkeypatch.setattr(box, "_run", lambda *a, **k: Ran(2, "verdict: NO ANSWER\n", 4.0, met=met))

    def tar_broken(*args: Any) -> tuple[JobResult | None, str]:
        raise tarfile.TarError("a made-up bad tar")

    box.offers = tar_broken
    ran = box.bench(list(planted.args))
    assert ran.code == 2 and "note: it wasn't offered a host with more memory: TarError" in (
        ran.output)


def test_a_kill_thats_not_the_kernels_for_memory_isnt_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 137 is met memory only where Docker's `oom` event for that container says so: a
    kill of any other kind, as a stopped study's, offers nothing."""
    here = tmp_path / "here-docker"
    here.mkdir()
    (here / "images.json").write_text(json.dumps({"tag": host.CAE_IMAGE}))
    monkeypatch.setattr(job, "DOCKER", (sys.executable, str(FAKE), str(here)))
    planted = _planted(tmp_path / "job")
    (here / "kill").write_text("")
    killed = job.run_here(planted)
    assert killed.returncode == 2 and "it was killed" in killed.stdout and killed.met is None
    (here / "kill").write_text("")
    (here / "oom").write_text("")
    stopped = job.run_here(planted)
    assert stopped.met is not None and stopped.met.killed and stopped.met.limit == 3 * GIB
    events = [e["argv"] for e in _log(here) if e.get("argv", [""])[0] == "events"]
    assert events and all(any(w.startswith("container=print-bench-") for w in e) for e in events)


def test_nothing_is_offered_while_the_study_is_stopping(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    pool.stop()
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE and _runs(root) == []
    assert "as the study was stopping: it wasn't offered more" in pool.said()
    offers = Offers(SETTINGS)
    offers.stop()
    assert offers(planted, Met(1, 1, None, None, "x"))[0] is None


def test_a_host_that_fails_an_offer_says_how_by_its_kind(tmp_path: Path) -> None:
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    (root / "drop").write_text("")
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        pool.run(planted)
    assert pool.last_offer is not None and pool.last_offer.outcome == "the host failed it"
    assert pool.last_offer.failures == ["running it: ssh's exit 255"]
    assert "which failed it (running it: ssh's exit 255)" in pool.said()
    assert "takes no more jobs this run" in pool.said()


def test_an_evals_run_is_offered_nothing_unless_it_opts_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util

    import print_shop.env

    path = Path(__file__).resolve().parents[1] / "scripts" / "rounds.py"
    spec = importlib.util.spec_from_file_location("rounds_offering", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    monkeypatch.setattr(print_shop.env, "load_env", lambda path=None: dict(SETTINGS))

    class Run:
        def __init__(self, folder: Path) -> None:
            self.folder = folder
            self.box = Box(folder)

    for name, opted, offered in (("eval-tip-9", False, False), ("eval-tip-9", True, True),
                                 ("design-table", False, True)):
        run = Run(tmp_path / name)
        script._offering(run, opted)
        assert (run.box.offers is not None) == offered, (name, opted)
    kept = tmp_path / "scenario"
    kept.mkdir()
    (kept / "right.json").write_text("{}")
    assert script.is_eval(kept) and not script.is_eval(tmp_path / "design-table")


def test_the_settings_example_reads_as_the_settings_reader_reads_it(tmp_path: Path) -> None:
    """Each comment in the docs' example is a line of its own: read by `load_env`, every value
    is the value, with no comment in it."""
    from print_shop.env import load_env

    text = (Path(__file__).resolve().parents[1] / "docs" / "settings-study.md").read_text()
    block = text[text.index("BENCH_HOSTS=spark\n"):]
    block = block[:block.index("```")]
    (tmp_path / ".env").write_text(block)
    found = stated(load_env(tmp_path / ".env"))[0]
    assert (found.ssh, found.memory_mb, found.floor_mb, found.load_mb) == (
        "spark", 15 * 1024, 10 * 1024, 12 * 1024)


def test_a_refusal_as_the_files_go_over_is_said_by_its_exit_and_words_alone(
    tmp_path: Path,
) -> None:
    """A helper's `tar -x` that refuses a file: its kind is the step, its exit code and its words,
    with the file's name and every figure left out (`remote.words_only`)."""
    root = tmp_path / "spark"
    pool = Pool(Slots(Budget(4 * 1024, 4)), [_remote(root)], here_runner=_here())
    (root / "in_fails").write_text("tar: work/bar.step: Cannot open: No space left on device\n"
                                   "tar: Exiting with failure status due to previous errors\n")
    planted = _planted(tmp_path / "job")
    with pool.take(3072, 2, kind="load standing"):
        done = pool.run(planted)
    assert done.host == HERE
    assert pool.last_offer is not None
    (kind,) = pool.last_offer.failures
    assert kind == ("sending its files: docker refused it (exit 2: tar: … Cannot open: No space "
                    "left on device tar: Exiting with failure status due to previous errors)")
    assert "bar" not in kind and not list((root / "volumes").iterdir())
    assert remote.words_only('Error "/print-bench-1a-in" 12 MiB, Conflict.') == "Error … MiB …"


def test_a_pre_check_that_refuses_a_step_says_its_need_and_room(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """mqucifer/print-shop#376's checks of a step's need against the room pass both on, so the
    met line says them: here a mesh too big to set up in load's own process."""
    group = tmp_path / "cgroup"
    group.mkdir()
    (group / "memory.max").write_text(f"{4 * GIB}\n")
    monkeypatch.setattr(progress, "CGROUP", group)
    progress.forget_met()
    with pytest.raises(load._Unread):
        load._memory_unread("a made-up mesh's set-up", need=5 * GIB, room=GIB)
    progress.say_met()
    (line,) = [x for x in capsys.readouterr().err.splitlines() if x.startswith(host.MET_LINE)]
    met = Met.of([line])
    assert met is not None and (met.need, met.room, met.limit) == (5 * GIB, GIB, 4 * GIB)


def test_a_bundle_sends_each_folders_files_together(tmp_path: Path) -> None:
    """Named in between files above it, a folder's files still go over together, after the
    folder itself: the helper's tar restores a folder's owner as it moves past it, and can't
    write into it again (mqucifer/print-shop#380)."""
    import io
    import tarfile

    (tmp_path / "references").mkdir()
    names = []
    for k in range(3):
        for name in (f"references/part-{k}.step", f"band-{k}.step"):
            (tmp_path / name).write_bytes(b"x")
            names.append(name)
    given = BenchJob(("load", "band-0.step"), tmp_path, tuple(names))
    with tarfile.open(fileobj=io.BytesIO(remote.bundle(given))) as tar:
        work = [m.name for m in tar if m.name.startswith("work/")]
    inside = [k for k, name in enumerate(work) if name.startswith("work/references/")]
    assert inside == list(range(inside[0], inside[0] + 3)), work
    assert work[inside[0] - 1] == "work/references"
    assert sorted(work) == sorted(["work/references", *(f"work/{n}" for n in names)])
