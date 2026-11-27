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
    monkeypatch.setattr(progress, "heap", lambda: {"used": GIB, "kept": GIB // 2})
    with progress.step("joining the parts"), progress.step("reading  the\n joint a+b"):
        pass
    said = capsys.readouterr().err.splitlines()
    kinds = [line.split(" ", 3)[1] + " " + line.split(" ", 3)[3] for line in said]
    assert kinds == [
        "step joining the parts",
        f"memory current={2 * GIB} peak={3 * GIB - 1} anon={2 * GIB - GIB // 4} shmem={GIB // 4} "
        f"used={GIB} kept={GIB // 2}",
        "step reading the joint a+b",
        f"memory current={2 * GIB} peak={3 * GIB - 1} anon={2 * GIB - GIB // 4} shmem={GIB // 4} "
        f"used={GIB} kept={GIB // 2}",
        "step joining the parts",
        "step between steps, after joining the parts",
    ]
    assert all(line.startswith(("bench: step ", "bench: memory ")) for line in said)
    assert (progress.STEP_LINE, progress.MEMORY_LINE) == (host.STEP_LINE, host.MEMORY_LINE)


def test_a_cgroup_with_no_counts_is_said_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(progress, "heap", lambda: {"used": None, "kept": None})
    assert progress.memory(tmp_path / "none") == dict.fromkeys(
        ("current", "peak", "anon", "shmem", "used", "kept")
    )


def test_the_heap_is_counted_where_there_s_a_c_library_that_counts_it() -> None:
    """glibc's counts on Linux, as in the image and on CI; none on the Mac."""
    found = progress.heap()
    if sys.platform.startswith("linux"):
        assert found["used"] is not None and found["used"] > 0 and found["kept"] is not None
    else:
        assert found == {"used": None, "kept": None}


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
    assert done.stdout == host.killed(SAID.splitlines(), host.memory_for(args))
    assert f"most likely at the {host.memory_for(args)} memory limit" in done.stdout
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
        assert ran.output == "read\n\n" + host.killed(SAID.splitlines(), host.memory_for(args))


def test_what_the_process_freed_is_given_back_before_gmsh_and_calculix_and_after_each_step(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """gmsh and CalculiX are processes of their own in the same container: what `load`'s own
    process freed and the C library kept is given back before each starts, and as each step
    ends, where it ends in a reason too."""
    from types import SimpleNamespace

    from print_shop.bench import cae

    order: list[str] = []
    monkeypatch.setattr(cae, "give_back", lambda: order.append("given back"))
    monkeypatch.setattr(load, "give_back", lambda: order.append("given back"))
    def meshed() -> None:
        order.append("gmsh")

    tools = SimpleNamespace(create_mesh=meshed)
    cae.gmsh(tools)
    with load.step("reading"):
        order.append("read")
    with pytest.raises(NoAnswer), load.step("stopping"):
        raise ValueError("no")

    def ran(argv: list[str], **_: Any) -> subprocess.CompletedProcess[bytes]:
        order.append("calculix")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", ran)
    fea = SimpleNamespace(setup_ccx=lambda: None, inp_file_name="/tmp/x/a.inp", ccx_binary="ccx")
    cae.ccx_one_thread(fea)
    assert order == [
        "given back", "gmsh", "read", "given back", "given back", "given back", "calculix",
    ]  # fmt: skip
    capsys.readouterr()


def test_giving_back_runs_where_there_is_no_c_library_to_ask() -> None:
    from print_shop.bench import cae

    cae.give_back()


def _load_trace() -> Any:
    import importlib.util

    path = Path(__file__).parent.parent / "scripts" / "load_trace.py"
    spec = importlib.util.spec_from_file_location("load_trace", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_diagnostic_says_no_name_and_no_figure(tmp_path: Path) -> None:
    """`scripts/load_trace.py` is run on a design's own assembly: each part and joint is said by
    its number and the joint's kind, and every figure in a step's words is `#`."""
    trace = _load_trace()
    made = tmp_path / "a.json"
    made.write_text(
        '{"parts": [{"name": "seat-2"}, {"name": "leg"}], '
        '"joints": [{"between": ["seat-2", "leg"], "kind": "through"}]}'
    )
    said = trace.names(made)
    words = "judging the joint seat-2+leg, through, between seat-2 and leg, at 3.5 mm by 12"
    assert trace.plain(words, said) == (
        "judging the joint joint1 (through), through, between part1 and part2, at # mm by #"
    )
    lines = trace.report(
        [
            "bench: step 1.0 reading the part leg, at 4 mm",
            f"bench: memory 1.0 current={GIB} peak={GIB} anon={GIB} shmem=0 used={GIB} kept=0",
            f"bench: memory 6.0 current={2 * GIB} peak={2 * GIB} anon=1 shmem=0 used=1 kept=1",
            "bench: step 9.0 between steps, after reading the part leg, at 4 mm",
        ],
        said,
    )
    assert lines[1] == (
        "     1.0      8.0  1.00 2.00  2.00 0.00  0.00 0.00  reading the part part2, at # mm"
    )
    assert "leg" not in "\n".join(lines) and "4 mm" not in "\n".join(lines)


def test_a_load_in_two_passes_asked_for_is_given_the_larger_memory_and_nothing_else_is(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mqucifer/print-shop#336: a design run's whole table meshed pass 1's whole in 3.32 GiB at
    the most, past the 3 GiB. `load --parts` is given `TWO_PASSES_MEMORY`, by `host.run` and by
    a design round's `Box`, as it's given the hour; a plain `load`, every other command and a
    script keep `MEMORY`, so what each proves of the 3 GiB stands."""
    from print_shop.rounds import box

    assert (host.MEMORY, host.TWO_PASSES_MEMORY) == ("3g", "4g")
    given: list[str | None] = []

    def container(*_: Any, memory: str | None = None, **__: Any) -> list[str]:
        given.append(memory)
        return [sys.executable, "-c", "pass"]

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    real = host.container
    monkeypatch.setattr(host, "container", container)
    host.run(["load", "a.json", "--weight", "1", "--parts"], tmp_path)
    host.run(["--out", "o", "load", "a.json", "--parts"], tmp_path)
    host.run(["load", "a.json", "--weight", "1"], tmp_path)
    host.run(["bend", "c.step", "--parts"], tmp_path)
    host.run(["load.py", "--parts"], tmp_path, python=True)
    rounds = box.Box(tmp_path, 60)
    rounds.bench(["load", "a.json", "--parts"])
    rounds.bench(["load", "a.json"])
    rounds.python("make.py")
    assert given == ["4g", "4g", "3g", "3g", "3g", "4g", "3g", None]
    for memory, said in ((None, "3g"), ("4g", "4g")):
        argv = real(tmp_path, ["x"], "n", memory=memory)
        assert argv[argv.index("--memory") + 1] == argv[argv.index("--memory-swap") + 1] == said


def test_a_design_round_sets_what_load_says_as_it_goes_aside_before_its_output_is_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """mqucifer/print-shop#336, the cold review: `Box` keeps the last 256,000 bytes of output, and
    a long `--parts` read says some 210 KB as it goes. Counted, it would push the reply's start
    out, cut mid line, and a killed run's reply wouldn't start with its verdict. Set aside as
    it's read, the reply is whole, whatever was said before it."""
    from print_shop.rounds import box

    # Made by the child itself: Linux takes no argument past 128 KiB.
    line = "bench: step {k}.0 reading the joint j{k}\nbench: memory {k}.5 current=1 peak=2\n"
    count = 8000
    assert count * len(line.format(k=count)) > box.KEEP
    child = (
        "import sys\n"
        f"for k in range({count}):\n"
        f"    sys.stderr.write({line!r}.format(k=k))\n"
        "sys.stderr.flush(); print('verdict: NO ANSWER: the reply'); sys.stdout.flush()\n"
        f"sys.stderr.write({SAID!r}); sys.exit({host.KILLED})\n"
    )
    assert len(child) < 4096
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:x")
    monkeypatch.setattr(host, "container", lambda *_, **__: [sys.executable, "-c", child])
    ran = box.Box(tmp_path, 60).bench(["load", "a.json", "--parts"])
    assert ran.output.startswith("verdict: NO ANSWER: the reply\n"), ran.output[:200]
    assert ran.dropped == 0, "nothing of the reply is dropped"
    assert "bench: " not in ran.output and "ory " not in ran.output.split("\n")[0]
    assert ran.output.endswith(host.killed(SAID.splitlines(), "4g"))


def test_progress_is_told_from_the_output_across_any_chunk_s_edge() -> None:
    """A line of what `load` says, split across reads, is still set aside whole; any other line
    is passed on as it comes, one of any length with no end of its own too."""
    from print_shop.rounds import box

    text = (
        b"one\nbench: step 1.0 joining the parts\nbe it so\n"
        b"bench: memory 2.0 current=5 peak=6\ntwo"
    )
    for size in (1, 2, 3, 7, len(text)):
        progress = box._Progress()
        out = b"".join(progress.take(text[k : k + size]) for k in range(0, len(text), size))
        out += progress.end()
        assert out == b"one\nbe it so\ntwo", size
        assert progress.said() == [
            "bench: step 1.0 joining the parts", "bench: memory 2.0 current=5 peak=6",
        ]  # fmt: skip
    # A line that isn't one is passed on as it's read, not held to its end.
    progress = box._Progress()
    assert progress.take(b"x" * 10_000) == b"x" * 10_000
    assert progress.take(b"y\nbench: st") == b"y\n" and progress.end() == b"bench: st"


def test_load_takes_each_option_only_in_full() -> None:
    """The host gives `--parts` the hour and its memory by its words: `--part` isn't read as it,
    by the host or by `load`."""
    from print_shop.bench import cli

    assert cli.parser().parse_args(["load", "a.json", "--parts"]).parts
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["load", "a.json", "--part"])
    assert not host.asks_two_passes(["load", "a.json", "--part"])


def test_the_memory_s_thread_has_ended_once_it_s_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import threading

    monkeypatch.setattr(progress, "CGROUP", _cgroup(tmp_path, GIB, GIB, 0))
    progress.watch(0.05)
    progress.stop()
    assert not any(t.name == "bench-memory" for t in threading.enumerate())
    capsys.readouterr()
