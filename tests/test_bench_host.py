"""The bench's limits and its report, with no Docker: the rules that protect the disk and say
what an answer is."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from print_shop.bench import host
from print_shop.bench.report import EXIT, Report


def test_the_container_has_no_network_and_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:abc")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    argv = host.container(Path("/tmp/x"), ["python", "-V"], "n")
    pairs = dict(zip(argv, argv[1:], strict=False))
    assert pairs["--network"] == "none"
    assert pairs["--cpus"] == "2"
    assert pairs["--memory"] == "3g"
    assert pairs["--memory-swap"] == "3g", "no swap past the cap"
    assert "--read-only" in argv
    assert pairs["--cap-drop"] == "ALL"
    assert argv[argv.index(host.IMAGE) + 1 :] == ["python", "-V"]
    assert f"{host.HERE}:{host.MOUNT}:ro" in argv, "the bench's own code is read-only to it"
    assert pairs["--log-driver"] == "none", "output mustn't fill Docker's disk through its log"
    assert pairs["--ulimit"] == f"fsize={host.MAX_FILE}", "/work has no quota of its own"


def test_a_bench_command_cant_be_shadowed_from_the_working_directory() -> None:
    assert host.bench_command(["info", "a.step"])[:2] == ["python", "-I"]


def test_the_build_refuses_when_the_disk_would_fall_under_its_floor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + host.IMAGE_LIMIT_GB - 0.5)
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert host.build() == 1
    assert not ran, "nothing may be built"
    assert "refused" in capsys.readouterr().out


def test_check_says_when_the_image_or_the_disk_is_over(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:abc")
    monkeypatch.setattr(host, "image_gb", lambda image=host.IMAGE: 2.1)
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB - 0.1)
    assert host.check() == 1
    assert "over the limit" in capsys.readouterr().out
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + 1)
    assert host.check() == 0


def test_a_killed_process_is_said_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    def killed(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, host.KILLED, "", "")

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:abc")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    monkeypatch.setattr(subprocess, "run", killed)
    done = host.run(["info", "a.step"], Path("/tmp/x"))
    assert done.returncode == 2
    assert "NO ANSWER" in done.stdout and "memory limit" in done.stdout


def test_an_answer_with_no_second_check_is_no_answer(tmp_path: Path) -> None:
    report = Report("info", ["info", "a.step"])
    report.verdict("OK", "looks fine")
    report.save(tmp_path)
    assert report.word == "NO ANSWER"
    assert report.exit_code == 2
    assert "second check: none ran" in report.text
    assert "looks fine" in report.text, "what it had said stays visible"


def test_every_report_is_saved_and_names_its_file(tmp_path: Path) -> None:
    report = Report("info", ["info", "a.step"])
    report.second("by another method: 1.0 mm³")
    report.fact("volume_mm3", 1.0, how="mesh")
    report.verdict("OK", "fine")
    saved = report.save(tmp_path)
    assert saved.is_file() and saved.with_suffix(".txt").is_file()
    assert report.text.startswith("verdict: OK: fine\n")
    assert saved.with_suffix(".txt").name in report.text


def test_a_report_cant_use_an_unknown_verdict() -> None:
    with pytest.raises(ValueError):
        Report("info", []).verdict("PROBABLY", "fine")


@pytest.mark.parametrize(
    ("word", "code"),
    [("OK", 0), ("CLEAR", 0), ("TOUCHING", 0), ("FAIL", 1), ("INTERFERE", 1), ("TOO CLOSE", 1)]
    + [("NO CHANGE", 1), ("DISAGREE", 2), ("NO ANSWER", 2)],
)
def test_exit_codes_tell_the_three_kinds_of_answer_apart(word: str, code: int) -> None:
    assert EXIT[word] == code


# The second image (mqucifer/print-shop#142)


def test_fit_on_and_bend_run_in_the_second_image_and_everything_else_in_the_first() -> None:
    assert host.image_for(["fit-on", "lid.step", "--onto", "box.step"]) == host.CAE_IMAGE
    assert host.image_for(["--out", "elsewhere", "bend", "clip.step"]) == host.CAE_IMAGE
    assert host.image_for(["--out=elsewhere", "bend", "clip.step"]) == host.CAE_IMAGE
    assert host.image_for(["clearance", "a.step", "bend.step"]) == host.IMAGE
    assert host.image_for(["--out", "bend", "info", "a.step"]) == host.IMAGE, "a folder's name"
    assert host.image_for([]) == host.IMAGE
    import argparse

    from print_shop.bench.cli import parser

    commands = next(a for a in parser()._actions if isinstance(a, argparse._SubParsersAction))
    assert set(host.CAE_COMMANDS) < set(commands.choices), "each is a command of the bench's"


def test_the_second_image_s_container_has_the_same_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: f"sha256:{image}")
    monkeypatch.setattr(host, "code_version", lambda: "abc")
    command = host.bench_command(["fit-on", "lid.step", "--onto", "box.step"])
    assert command[:2] == [host.CAE_PYTHON, "-I"], "nothing in /work can shadow an import"
    first = host.container(Path("/tmp/x"), ["python", "-V"], "n")
    second = host.container(Path("/tmp/x"), command, "n", image=host.CAE_IMAGE)
    cut = second.index(host.CAE_IMAGE)
    assert second[cut + 1 :] == command
    limits = [w for w in first[: first.index(host.IMAGE)] if not w.startswith("BENCH_IMAGE")]
    assert [w for w in second[:cut] if not w.startswith("BENCH_IMAGE")] == limits
    assert f"BENCH_IMAGE={host.CAE_IMAGE}" in second
    assert f"BENCH_IMAGE_ID=sha256:{host.CAE_IMAGE}" in second, "the report names its own image"


@pytest.mark.parametrize(
    ("here", "free", "built"),
    [
        # Both to build: the second waits for room for both.
        ((), host.FLOOR_GB + host.CAE_LIMIT_GB + host.IMAGE_LIMIT_GB - 0.5, False),
        ((), host.FLOOR_GB + host.CAE_LIMIT_GB + host.IMAGE_LIMIT_GB + 0.5, True),
        # The first is here: the second needs its own limit over the floor.
        ((host.IMAGE,), host.FLOOR_GB + host.CAE_LIMIT_GB - 0.5, False),
        ((host.IMAGE,), host.FLOOR_GB + host.CAE_LIMIT_GB + 0.5, True),
        # It's here already: a build of it is its layers again, and isn't refused.
        ((host.IMAGE, host.CAE_IMAGE), host.FLOOR_GB + 0.5, True),
    ],
)
def test_the_second_image_s_build_counts_both_images_against_the_floor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    here: tuple[str, ...],
    free: float,
    built: bool,
) -> None:
    ran: list[list[str]] = []

    def build(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(host, "free_gb", lambda: free)
    monkeypatch.setattr(
        host, "image_id", lambda image=host.IMAGE: "sha256:a" if image in here else None
    )
    monkeypatch.setattr(host, "check", lambda: 0)
    monkeypatch.setattr(subprocess, "run", build)
    assert host.build("cae") == (0 if built else 1)
    said = capsys.readouterr().out
    if built:
        (argv,) = ran
        assert argv[:2] == ["docker", "build"] and host.CAE_IMAGE in argv
        assert argv[-1].endswith("cae-image")
    else:
        assert not ran, "nothing may be built"
        assert "refused" in said and host.CAE_IMAGE in said


def test_a_rebuild_removes_the_image_it_replaced_and_no_other(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    ids = iter(["sha256:old", "sha256:old", "sha256:old", "sha256:new"])

    def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + 0.5)
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: next(ids))
    monkeypatch.setattr(host, "check", lambda: 0)
    monkeypatch.setattr(subprocess, "run", run)
    assert host.build("cae") == 0
    assert [argv[:3] for argv in ran] == [
        ["docker", "build", "--memory"],
        ["docker", "image", "rm"],
    ]
    assert ran[1][3:] == ["sha256:old"], "by its id: nothing else is pruned"
    assert "removed the" in capsys.readouterr().out


def test_the_first_image_s_build_leaves_room_for_the_second_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + host.IMAGE_LIMIT_GB + 0.5)
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    assert host.build() == 1 and not ran
    said = capsys.readouterr().out
    assert host.IMAGE in said and host.CAE_IMAGE in said


def test_a_build_of_an_image_theres_none_of_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert host.build("fea") == 3
    assert "bench, cae" in capsys.readouterr().out


def test_check_holds_each_image_to_its_limit_and_names_tags_left_behind(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sizes = {host.IMAGE: 2.1, host.CAE_IMAGE: 3.2}
    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: "sha256:abc")
    monkeypatch.setattr(host, "image_gb", lambda image=host.IMAGE: sizes[image])
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + 1)
    monkeypatch.setattr(host, "others", lambda image: ["print-shop/cae:0.0"])
    assert host.check() == 0
    said = capsys.readouterr().out
    assert (
        f"{host.IMAGE} sha256:abc: 2.10 GB" in said
        and f"{host.CAE_IMAGE} sha256:abc: 3.20 GB" in said
    )
    assert "print-shop/cae:0.0" in said and "docker image rm" in said
    sizes[host.CAE_IMAGE] = host.CAE_LIMIT_GB + 0.1
    assert host.check() == 1
    assert "over the limit" in capsys.readouterr().out


def test_check_passes_with_the_second_image_still_to_build(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        host, "image_id", lambda image=host.IMAGE: "sha256:abc" if image == host.IMAGE else None
    )
    monkeypatch.setattr(host, "image_gb", lambda image=host.IMAGE: 2.1)
    monkeypatch.setattr(host, "free_gb", lambda: host.FLOOR_GB + 1)
    assert host.check() == 0
    assert "scripts/bench.py build cae" in capsys.readouterr().out


def test_the_second_image_s_packages_are_each_by_version_build_and_md5() -> None:
    """Both platforms' lists: nothing is solved at a build, and the three the issue names are at
    the versions it names."""
    folder = host.HERE / host.IMAGES["cae"][2]
    for arch, platform in (("arm64", "linux-aarch64"), ("amd64", "linux-64")):
        lines = (folder / f"lock-{arch}.txt").read_text().splitlines()
        packages = [line.split() for line in lines if line and not line.startswith("#")]
        assert len(packages) > 250
        for subdir, _name, version, _build, kind, md5 in packages:
            assert subdir in (platform, "noarch") and kind in ("conda", "tar.bz2")
            assert version.startswith("v") and len(md5) == 32 and int(md5, 16) >= 0
        pins = {name: version for _, name, version, *_ in packages}
        assert (pins["freecad"], pins["calculix"], pins["gmsh"]) == ("v1.1.3", "v2.23", "v4.15.2")
        assert pins["occt"] == "v7.9.3"
    dockerfile = (folder / "Dockerfile").read_text()
    assert "@sha256:" in dockerfile.split("FROM ")[1].splitlines()[0], "the base by its digest"
    assert "lock-${TARGETARCH}.txt" in dockerfile and "@EXPLICIT" in dockerfile
    assert "micromamba install -y -n base -f /tmp/packages.txt" in dockerfile, "no solve"


def test_a_rounds_run_without_the_second_image_is_told_and_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from print_shop.rounds.box import Box

    monkeypatch.setattr(host, "image_id", lambda image=host.IMAGE: None)
    done = Box(tmp_path).bench(["fit-on", "lid.step", "--onto", "box.step"])
    assert done.code == 3 and "scripts/bench.py build cae" in done.output
