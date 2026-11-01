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
