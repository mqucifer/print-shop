"""What `load` says as it goes, and where a killed command was (mqucifer/print-shop#336).

A whole assembly killed at the container's 3 GiB said only that it was killed: no step, and no
figure. Each step `load` names is said on stderr as it begins, with the container's memory, and
the memory every few seconds; the host keeps the last of each and says them where the command is
killed, by `host.run` and by a design round's `Box`. Real child processes stand in for the
container here, with a cgroup of files: no Docker.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from print_shop.bench import host, load, progress
from print_shop.bench.report import NoAnswer

GIB = 2**30


def _cgroup(tmp_path: Path, current: int, peak: int, shmem: int) -> Path:
    group = tmp_path / "cgroup"
    group.mkdir()
    (group / "memory.current").write_text(f"{current}\n")
    (group / "memory.peak").write_text(f"{peak}\n")
    (group / "memory.stat").write_text(f"anon {current - shmem}\nfile 7\nshmem {shmem}\n")
    return group


def test_each_step_is_said_as_it_begins_and_the_one_it_was_inside_as_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nested steps: the inner said as it begins, the outer again as it ends, and once the last
    has ended, that it's between steps; the memory with each step's start, from the cgroup."""
    group = _cgroup(tmp_path, 2 * GIB, 3 * GIB - 1, GIB // 4)
    monkeypatch.setattr(progress, "CGROUP", group)
    with progress.step("joining the parts"), progress.step("reading  the\n joint a+b"):
        pass
    said = capsys.readouterr().err.splitlines()
    kinds = [line.split(" ", 3)[1] + " " + line.split(" ", 3)[3] for line in said]
    assert kinds == [
        "step joining the parts",
        f"memory current={2 * GIB} peak={3 * GIB - 1} anon={2 * GIB - GIB // 4} shmem={GIB // 4}",
        "step reading the joint a+b",
        f"memory current={2 * GIB} peak={3 * GIB - 1} anon={2 * GIB - GIB // 4} shmem={GIB // 4}",
        "step joining the parts",
        "step between steps, after joining the parts",
    ]
    assert all(line.startswith(("bench: step ", "bench: memory ")) for line in said)
    assert (progress.STEP_LINE, progress.MEMORY_LINE) == (host.STEP_LINE, host.MEMORY_LINE)


def test_a_cgroup_with_no_counts_is_said_unknown(tmp_path: Path) -> None:
    assert progress.memory(tmp_path / "none") == dict.fromkeys(
        ("current", "peak", "anon", "shmem")
    )


def test_load_s_own_steps_say_themselves_and_what_stops_one_is_still_said_with_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`load.step` says its step as it begins, and still names it where it stops
    (mqucifer/print-shop#319)."""
    stopped = "load stopped meshing the whole: ValueError: no"
    with pytest.raises(NoAnswer, match=stopped), load.step("meshing the whole"):
        raise ValueError("no")
    err = capsys.readouterr().err
    assert " meshing the whole\n" in err and "between steps, after meshing the whole" in err


def test_the_memory_is_said_while_it_runs_until_it_s_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    monkeypatch.setattr(progress, "CGROUP", _cgroup(tmp_path, GIB, GIB, 0))
    progress.watch(0.05)
    time.sleep(0.4)
    progress.stop()
    time.sleep(0.15)
    capsys.readouterr()
    time.sleep(0.2)
    assert capsys.readouterr().err == "", "nothing is said once it's stopped"


#: What a killed `load` had said as it went: two steps, the second the one it was in, and the
#: memory after it began.
SAID = (
    "bench: step 1.0 joining the parts\n"
    "bench: memory 1.0 current=100 peak=200 anon=100 shmem=0\n"
    "bench: step 1210.4 the second pass's part post-upper0 on its own, at 30 mm\n"
    f"bench: memory 1215.4 current={int(2.5 * GIB)} peak={int(2.9 * GIB)} anon={2 * GIB} "
    f"shmem={GIB // 2}\n"
)


def test_the_kill_names_the_step_it_was_in_and_the_memory_it_had_held() -> None:
    said = host.killed(SAID.splitlines())
    assert said == (
        f"verdict: NO ANSWER: it was killed, most likely at the {host.MEMORY} memory limit. "
        "Where it was: the second pass's part post-upper0 on its own, at 30 mm, from 1210.4 s "
        f"in. The container's memory: at most 2.90 GiB of its {host.MEMORY} by 1215.4 s in, when "
        "it last said, 2.50 GiB then, 0.50 GiB of it files in /tmp\n"
    )
    # A command that says nothing as it goes is said to be killed, as before.
    assert host.killed([]) == (
        f"verdict: NO ANSWER: it was killed, most likely at the {host.MEMORY} memory limit\n"
    )
    # The kernel kept no count.
    assert "at most unknown of its" in host.killed(["bench: memory 3.0 current=- peak=-"])


def _child(err: str, code: int) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.stderr.write({err!r}); sys.exit({code})"]


def _host_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, child: list[str], args: list[str],
              **more: Any) -> subprocess.CompletedProcess[str]:  # fmt: skip
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(host, "container", lambda *_, **__: child)
    return host.run(args, tmp_path, **more)


@pytest.mark.parametrize(
    "args",
    [
        # Offered the hour: waited for as it goes, its stderr read line by line.
        ["load", "a.json", "--weight", "1"],
        # Given the hour from the start: waited for to its end.
        ["load", "a.json", "--weight", "1", "--parts"],
    ],
)
def test_a_killed_load_run_by_the_host_says_where_it_was(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: list[str]
) -> None:
    trace: list[str] = []
    done = _host_run(
        monkeypatch, tmp_path, _child("its own\n" + SAID, host.KILLED), args, trace=trace
    )
    assert done.returncode == 2
    assert done.stdout == host.killed(SAID.splitlines())
    assert "Where it was: the second pass's part post-upper0 on its own" in done.stdout
    assert done.stderr == "its own\n", "what it says as it goes is the host's, not passed on"
    assert trace == SAID.splitlines()


def test_a_load_that_ends_passes_on_its_stderr_less_what_it_said_as_it_went(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    done = _host_run(monkeypatch, tmp_path, _child(SAID + "said\n", 0), ["load", "a.json"])
    assert (done.returncode, done.stderr) == (0, "said\n")


def test_a_design_round_s_killed_load_says_where_it_was(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A design round's bench calls go through `rounds/box.py`, its stdout and stderr as one:
    a killed `load` there says where it was too, and what it said as it went isn't the
    answer's."""
    from print_shop.rounds import box

    child = f"import sys; print('read'); sys.stdout.flush(); sys.stderr.write({SAID!r}); "
    child += f"sys.exit({host.KILLED})"
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(
        host, "container", lambda *_, **__: [sys.executable, "-c", child]
    )
    for args in (["load", "a.json", "--weight", "1"], ["load", "a.json", "--parts"]):
        ran = box.Box(tmp_path, 60).bench(args)
        assert ran.code == 2, ran.output
        assert ran.output == "read\n\n" + host.killed(SAID.splitlines())
