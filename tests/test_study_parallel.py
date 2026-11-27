"""Specification for a settings study's reads run in parallel within a host's budget, each a
bench job any host with the same image could run (mqucifer/print-shop#364).

Every slice and every `load` here is made up (`tests/test_settings_study.py`'s fakes), slowed
by made-up waits so they overlap; Docker is never asked. The same study, read one at a time and
many at once, gives the same table.
"""

from __future__ import annotations

import json
import random
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host
from print_shop.bench.budget import Budget, Slots, megabytes, reserved
from print_shop.bench.job import BenchJob, JobResult, run_here, saved_files
from print_shop.study.candidate import Candidate
from print_shop.study.loop import StudyLoop, StudyPart, _load_memory, run
from print_shop.study.reading import BenchLoad, Case, Sliced
from print_shop.study.table import table
from tests.test_settings_study import (  # noqa: F401 - `part` is a fixture
    BOLT,
    CASES,
    FOUR_WALLS,
    THREE_WALLS,
    WAIT,
    Model,
    fake_loading,
    fake_slicing,
    part,
)

# --------------------------------------------------------------------------
# The budget, read from Docker less what's kept
# --------------------------------------------------------------------------


@pytest.mark.parametrize("size,mb", [("3g", 3072), ("4g", 4096), ("512m", 512), ("1.5g", 1536),
                                     ("12.5GiB", 12800), (3 * 1024**3, 3072)])
def test_a_size_is_read_as_docker_takes_it(size: str | int, mb: int) -> None:
    assert megabytes(size) == mb


def test_what_isnt_a_size_is_refused() -> None:
    with pytest.raises(ValueError, match="isn't a size"):
        megabytes("lots")


def test_whats_kept_is_as_given_then_as_the_settings_say_then_one_commands_own() -> None:
    assert reserved({}) == (3072, 2), "one bench command's 3g and 2 CPUs"
    settings = {"BENCH_RESERVE_MEMORY": "6g", "BENCH_RESERVE_CPUS": "3"}
    assert reserved(settings) == (6144, 3)
    assert reserved(settings, "1g", 0) == (1024, 0)
    with pytest.raises(ValueError, match="a count of CPUs"):
        reserved({"BENCH_RESERVE_CPUS": "two"})


def test_the_budget_is_dockers_memory_and_cpus_less_whats_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[list[str]] = []

    def docker(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, f"{int(12.5 * 1024**3)} 8\n", "")

    monkeypatch.setattr(subprocess, "run", docker)
    found = Budget.docker(3072, 2)
    assert asked == [["docker", "info", "--format", "{{.MemTotal}} {{.NCPU}}"]]
    assert (found.memory_mb, found.cpus, found.free_memory_mb, found.free_cpus) == (
        12800, 8, 9728, 6)
    assert found.at_once(3072) == 3, "memory holds 3 of 3g, the CPUs 6"
    assert found.at_once(4096) == 2
    assert found.said() == ("12.5 GiB and 8 CPUs by docker info, less 3.0 GiB and 2 CPUs kept "
                            "for other work: 3 bench commands of 3g at once")


def test_a_docker_that_doesnt_say_is_an_error_never_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: (
        subprocess.CompletedProcess(argv, 1, "", "Cannot connect to the Docker daemon")))
    with pytest.raises(RuntimeError, match="Cannot connect to the Docker daemon"):
        Budget.docker(0, 0)


# --------------------------------------------------------------------------
# Slots: never past the budget
# --------------------------------------------------------------------------


def test_jobs_never_hold_more_than_the_budget_at_once() -> None:
    slots = Slots(Budget(memory_mb=7 * 1024, cpus=8))
    held = {"memory": 0, "most_memory": 0}
    lock = threading.Lock()

    def job(memory: int) -> None:
        with slots.take(memory):
            with lock:
                held["memory"] += memory
                held["most_memory"] = max(held["most_memory"], held["memory"])
            time.sleep(0.02)
            with lock:
                held["memory"] -= memory

    threads = [threading.Thread(target=job, args=(3072,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert held["most_memory"] == 6144 and slots.most == 2, "7 GiB holds two of 3g"


def test_a_job_the_whole_budget_cant_hold_is_refused() -> None:
    with pytest.raises(ValueError, match="past the budget: 3072 MiB and 1 CPUs free"), \
            Slots(Budget(3072, 1)).take(4096):
        pass


def test_load_takes_its_own_containers_memory() -> None:
    assert _load_memory(Case("lifted", ("--lift", "top"))) == 3072
    assert _load_memory(Case("parts", ("--weight", "30", "--parts", "a,b"))) == 4096


# --------------------------------------------------------------------------
# A bench job: a unit any host with the same image could run
# --------------------------------------------------------------------------


def test_a_jobs_limits_are_its_commands_own(tmp_path: Path) -> None:
    one = BenchJob.of(["load", "a.json", "--lift", "top"], tmp_path, ["a.json"])
    two = BenchJob.of(["load", "a.json", "--parts", "x,y"], tmp_path, ["a.json"])
    assert (one.memory, two.memory) == ("3g", "4g")
    assert one.timeout == host.timeout_for(["load", "a.json", "--lift", "top"])


def test_the_files_a_reply_says_it_saved_are_each_of_their_endings() -> None:
    said = "verdict: OK\nsaved: bench-out/load-1.txt and .json\n"
    assert saved_files(said) == ("bench-out/load-1.txt", "bench-out/load-1.json")


def test_a_job_run_here_is_a_bench_command_over_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "a.json").write_text("{}")
    ran: list[tuple[list[str], Path]] = []

    def bench(args: list[str], work: Path, **_: Any) -> subprocess.CompletedProcess[str]:
        ran.append((args, work))
        return subprocess.CompletedProcess(args, 0, "verdict: OK\nsaved: bench-out/load-1.txt "
                                           "and .json\n", "")

    monkeypatch.setattr(host, "run", bench)
    done = run_here(BenchJob.of(["load", "a.json"], tmp_path, ["a.json"]))
    assert ran == [(["load", "a.json"], tmp_path)]
    assert done.saved == ("bench-out/load-1.txt", "bench-out/load-1.json")
    with pytest.raises(FileNotFoundError, match="aren't in"):
        run_here(BenchJob.of(["load", "b.json"], tmp_path, ["b.json"]))


def test_a_studys_load_is_a_job_whose_folder_holds_every_file_it_reads(
    part: StudyPart, tmp_path: Path,  # noqa: F811
) -> None:
    """A runner on another host is given only the job: its command names only its files, each
    in its folder, and what it saves comes back into that folder."""
    assembly = tmp_path / "assembly.json"
    assembly.write_text(json.dumps({"parts": [{"name": "bar", "file": "bar.step"}]}))
    (tmp_path / "bar.step").write_text("STEP")
    given: list[BenchJob] = []

    def elsewhere(job: BenchJob) -> JobResult:
        given.append(job)
        assert all((job.folder / f).exists() for f in job.files)
        assert json.loads((job.folder / job.args[1]).read_text())["parts"][0]["print"]
        (job.folder / "bench-out").mkdir()
        (job.folder / "bench-out" / "load-1.json").write_text(json.dumps({"verdict": "OK"}))
        return JobResult(0, "saved: bench-out/load-1.txt and .json\n", "",
                         ("bench-out/load-1.txt", "bench-out/load-1.json"))

    sliced = fake_slicing([])(Candidate("c1", part.defaults), tmp_path)
    record = BenchLoad(assembly, "bar", elsewhere)(Candidate("c1", part.defaults), sliced,
                                                    CASES[0], tmp_path / "c1" / "standing")
    assert record == {"verdict": "OK"}
    (job,) = given
    assert job.args == ("load", "study-assembly.json", *CASES[0].args)
    assert set(job.files) == {"study-assembly.json", "bar.step"}
    assert job.memory == "3g"


# --------------------------------------------------------------------------
# The study, read in parallel
# --------------------------------------------------------------------------


def _slow(seen: list[Any], seed: int) -> tuple[Any, Any, dict[str, int]]:
    """The fakes, each slowed by a made-up wait, and how many ran at once."""
    slicing, loading = fake_slicing(seen), fake_loading([])
    rng = random.Random(seed)
    waits = [rng.uniform(0.0, 0.03) for _ in range(200)]
    count = {"now": 0, "most": 0}
    lock = threading.Lock()

    def timed(fn: Any) -> Any:
        def wrapped(*args: Any) -> Any:
            with lock:
                count["now"] += 1
                count["most"] = max(count["most"], count["now"])
                pause = waits.pop()
            time.sleep(pause)
            try:
                return fn(*args)
            finally:
                with lock:
                    count["now"] -= 1
        return wrapped

    return timed(slicing), timed(loading), count


def _script() -> Model:
    return Model([THREE_WALLS], [FOUR_WALLS], [BOLT], [WAIT], [WAIT], [WAIT],
                 [("recommend", {"candidate": "c1", "reason": "it passes for the least time"})])


def _without_budget(text: str) -> str:
    return "\n".join(line for line in text.splitlines()
                     if not line.startswith("Read within the host's budget"))


def test_the_same_study_read_serially_and_in_parallel_gives_the_same_table(
    part: StudyPart,  # noqa: F811
) -> None:
    tables, most = [], []
    for k, slots in enumerate((Slots(Budget.serial()), Slots(Budget(16 * 1024, 8)))):
        part.slicing, part.loading, count = _slow([], seed=k)
        part.slots = slots
        part.workdir = part.workdir.with_name(f"study-{k}")
        result = run(StudyLoop(chat=_script()), part)
        assert result.recommended == "c1"
        tables.append(table(result, "bar", [c.name for c in CASES]))
        most.append(count["most"])
    assert most[0] == 1 and most[1] > 1, most
    assert _without_budget(tables[0]) == _without_budget(tables[1])
    assert "one job at a time: at most 1 slices and reads at once" in tables[0]
    assert "16.0 GiB and 8 CPUs by given" in tables[1]


def test_reads_take_no_more_memory_than_the_budget_holds(part: StudyPart) -> None:  # noqa: F811
    held = {"now": 0, "most": 0}
    lock = threading.Lock()
    loading = fake_loading([])

    def heavy(*args: Any) -> dict[str, Any]:
        with lock:
            held["now"] += 1
            held["most"] = max(held["most"], held["now"])
        time.sleep(0.02)
        with lock:
            held["now"] -= 1
        return loading(*args)

    part.loading = heavy
    part.slots = Slots(Budget(memory_mb=7 * 1024, cpus=8))
    run(StudyLoop(chat=_script()), part)
    assert held["most"] == 2, "7 GiB holds two loads of 3g at once"


def test_the_model_goes_on_while_its_candidates_are_read(part: StudyPart) -> None:  # noqa: F811
    """A slice held back: `read` replies at once, a second is queued, `recommend` waits for
    them, and `wait` gives each as it lands, in the candidates' order."""
    release = threading.Event()
    slicing = fake_slicing([])

    def held(candidate: Candidate, out: Path) -> Sliced:
        if candidate.name != "defaults":
            assert release.wait(timeout=10)
        return slicing(candidate, out)

    part.slicing = held
    part.slots = Slots(Budget(16 * 1024, 8))

    def let_go(_: str) -> list[tuple[str, dict[str, Any]]]:
        release.set()
        return [WAIT, WAIT]

    model = Model([THREE_WALLS, FOUR_WALLS],
                  [("recommend", {"candidate": "c1", "reason": "r"})], let_go,
                  [("recommend", {"candidate": "c1", "reason": "r"})])
    tables: list[str] = []
    result = run(StudyLoop(chat=model), part,
                 progress=lambda so_far: tables.append(table(so_far, "bar", ["standing",
                                                                             "lifted"])))
    said = [(c.tool, c.result) for c in result.calls]
    assert said[0][1].startswith("queued c1: 3 walls, 25% infill")
    assert said[1][1].startswith("queued c2:") and "Reading or queued: c1, c2" in said[1][1]
    assert said[2] == ("recommend", "refused: c1, c2 still reading: `wait` for them first")
    waited = "\n".join(r for t, r in said if t == "wait")
    assert waited.index("c1: 3 walls") < waited.index("c2: 4 walls")
    assert result.recommended == "c1"
    assert [r.candidate.name for r in result.readings] == ["defaults", "c1", "c2"]
    # The table fills in as they land: the defaults first, then each, never one still reading
    # at the end.
    assert "| c1 |" not in tables[0] and "| defaults |" in tables[0]
    assert len(tables) == 3, "the defaults, then c1 and c2 as each lands"
    assert "reading |" not in table(result, "bar", ["standing", "lifted"])


def test_a_row_still_reading_says_so() -> None:
    from print_shop.bench.printed import Settings  # noqa: PLC0415
    from print_shop.study.loop import StudyResult  # noqa: PLC0415
    from print_shop.study.reading import Reading  # noqa: PLC0415

    so_far = StudyResult("reading", [Reading(Candidate("c1", Settings()))], [], 0, 0.0, "m")
    assert "| c1 | the profile's own | - | - | reading | reading | reading | - |" in table(
        so_far, "bar", ["standing", "lifted"])


def test_a_study_that_ends_doesnt_begin_what_it_queued(part: StudyPart) -> None:  # noqa: F811
    seen: list[Candidate] = []
    slicing = fake_slicing(seen)
    gate = threading.Event()

    def slow(candidate: Candidate, out: Path) -> Sliced:
        if candidate.name == "c1":
            gate.wait(timeout=0.3)
        return slicing(candidate, out)

    part.slicing = slow
    model = Model([THREE_WALLS], [FOUR_WALLS])
    result = run(StudyLoop(chat=model, max_calls=2), part)
    assert result.ended == "past its limit of 2 calls, with no recommendation"
    c1, c2 = result.readings[1:]
    assert c1.sliced is not None, "what was running was waited for"
    assert c2.error == "not read: the study ended before it was"
    assert [c.name for c in seen] == ["defaults", "c1"]
