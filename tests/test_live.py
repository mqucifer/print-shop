"""One live job at a time, and a press that names its job (mqucifer/print-shop#207).

Both faults were reproduced before they were fixed: a second start took down the first job's card
while its gate was open, and with two gates open one Approve started both prints. Everything
here is a fake broker, printer and slice, and a made-up ledger and part. What has to be shown
across processes (the lock going with a killed one, the reading commands beside a live one) runs
in processes of their own, with a throwaway HOME so the Mac's jobs folder is never reached.
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from print_shop.core.mqtt import approval_buttons, command_topic, gate_topic
from print_shop.ledger import Answer, Ledger, Result, Symptom
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.live import LOCK_NAME, AlreadyLive, Holder, Step, _take, only_live
from print_shop.printer.centauri import PrinterSnapshot
from tests.fakes import CI_PROFILES, DATA, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.sliced_fixtures import write_gcode
from tests.test_job_e2e import CFG
from tests.test_ledger import record

ROOT = Path(__file__).resolve().parents[1]


def _wait(until: Callable[[], object], timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not until():
        assert time.monotonic() < deadline, "waited too long"
        time.sleep(0.02)


def _refused(work: Path) -> str:
    """What a second start is told, as one sentence."""
    with pytest.raises(AlreadyLive) as refused, only_live(work, work / "unused.sqlite", "x.stl"):
        pass
    said = str(refused.value)
    assert said.startswith("Not started") and said.endswith(".")
    assert "\n" not in said and ". " not in said, "one sentence"
    return said


# --------------------------------------------------------------------------
# A press names its job
# --------------------------------------------------------------------------


class Job:
    """One process's daemon, with a printer and ledger of its own, on a bridge it may share."""

    def __init__(self, folder: Path, bridge: MemoryBridge,
                 on_step: Callable[[str, str, Step], None] | None = None) -> None:
        (folder / "work").mkdir(parents=True)
        self.bridge = bridge
        self.printer = FramePrinter()
        self.ledger = Ledger(folder / "ledger.sqlite")
        self.stl = write_stl("cube", folder)
        self.daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=folder / "work", printer=self.printer,
            slicer=RecordedSlicer(), bridge=lambda cfg: bridge, ledger=self.ledger,
            on_step=on_step,
        )

    def at_gate(self) -> str:
        job_id = self.daemon.submit(self.stl)
        self.daemon.wait_for_gate(job_id, timeout_s=30)
        return job_id


@pytest.fixture
def two_gates(tmp_path: Path) -> Iterator[tuple[Job, str, Job, str]]:
    """Two daemons at their gates on one broker, as two processes were."""
    bridge = MemoryBridge()
    a, b = Job(tmp_path / "a", bridge), Job(tmp_path / "b", bridge)
    with a.daemon, b.daemon:
        yield a, a.at_gate(), b, b.at_gate()


@pytest.mark.parametrize("first", [0, 1], ids=["the first gate's", "the second gate's"])
def test_with_two_gates_open_a_press_for_one_doesnt_reach_the_other(
    two_gates: tuple[Job, str, Job, str], first: int
) -> None:
    a, a_id, b, b_id = two_gates
    jobs = [(a, a_id), (b, b_id)]
    (pressed, pressed_id), (other, other_id) = jobs[first], jobs[1 - first]
    pressed.bridge.deliver(command_topic(CFG), {"decision": "approve", "job_id": pressed_id,
                                                "press": pressed.daemon._press})

    assert pressed.daemon.wait_for_finish(pressed_id, timeout_s=30).printed
    # Start reads the printer once, then the print node reads, uploads and starts.
    assert pressed.printer.verbs == ["snapshot", "snapshot", "upload", "start"]
    assert other.printer.verbs == [], "one Approve started one print"
    still = other.daemon.pending_gate()
    assert still is not None and still["job_id"] == other_id

    other.bridge.deliver(command_topic(CFG), {"decision": "reject", "job_id": other_id,
                                              "press": other.daemon._press})
    assert not other.daemon.wait_for_finish(other_id, timeout_s=30).printed
    assert other.ledger.rows() == []


@pytest.mark.parametrize("decision", ["approve", "reject"])
@pytest.mark.parametrize(
    "named",
    [{}, {"job_id": ""}, {"job_id": "another-job"}, {"job_id": None}, {"job_id": 7}],
    ids=["no job", "an empty one", "another job", "null", "not a string"],
)
def test_a_gate_takes_no_press_that_doesnt_name_its_job(
    tmp_path: Path, decision: str, named: dict[str, Any]
) -> None:
    job = Job(tmp_path, MemoryBridge())
    with job.daemon:
        job_id = job.at_gate()
        job.bridge.deliver(command_topic(CFG), {"decision": decision, **named})
        inbox = job.daemon._gate_inbox
        assert inbox is not None and inbox.empty()
        job.bridge.press(CFG, "reject")
        assert not job.daemon.wait_for_finish(job_id, timeout_s=30).printed
    assert job.printer.verbs == []


def test_an_ignored_press_is_said_in_the_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    job = Job(tmp_path, MemoryBridge())
    with job.daemon:
        job_id = job.at_gate()
        job.bridge.deliver(command_topic(CFG), {"decision": "approve", "job_id": "another-job"})
        assert f"ignored approve for job 'another-job': this gate is job {job_id}'s" in caplog.text
        job.bridge.press(CFG, "reject")
        job.daemon.wait_for_finish(job_id, timeout_s=30)


def test_the_buttons_name_the_job_before_its_card_shows_and_none_after(tmp_path: Path) -> None:
    job = Job(tmp_path, MemoryBridge())
    with job.daemon:
        job_id = job.at_gate()
        for decision in ("approve", "reject"):
            sent = json.loads(job.bridge.button(CFG, decision))
            # And the gate's word for a press (mqucifer/print-shop#209).
            assert sent == {"decision": decision, "job_id": job_id, "press": job.daemon._press}
        named = approval_buttons(CFG, job_id, job.daemon._press)
        sent_at = [(p.topic, p.payload) for p in job.bridge.published]
        card = next(n for n, (topic, text) in enumerate(sent_at)
                    if topic == gate_topic(CFG) and json.loads(text)["pending"])
        for topic, config in named:
            assert sent_at.index((topic, json.dumps(config))) < card
            assert job.bridge.retained_json(topic) == config, "retained, for a restarted HA"

        job.bridge.press(CFG, "reject")
        job.daemon.wait_for_finish(job_id, timeout_s=30)
        for topic, config in approval_buttons(CFG):
            assert job.bridge.retained_json(topic) == config, "they name no job once it's closed"


def test_naming_the_job_changes_nothing_else_of_a_button() -> None:
    """Home Assistant keys the entity on `unique_id` and the dashboard on the name."""
    for (topic, plain), (same, named) in zip(
        approval_buttons(CFG), approval_buttons(CFG, "job-1"), strict=True
    ):
        assert topic == same
        assert {**named, "payload_press": plain["payload_press"]} == plain


def test_leaving_at_the_gate_leaves_no_button_naming_the_job(tmp_path: Path) -> None:
    job = Job(tmp_path, MemoryBridge())
    with job.daemon:
        job.at_gate()
    for decision in ("approve", "reject"):
        assert json.loads(job.bridge.button(CFG, decision)) == {"decision": decision}


def test_a_rejected_gate_leaves_no_button_naming_the_job(tmp_path: Path) -> None:
    job = Job(tmp_path, MemoryBridge())
    with job.daemon:
        job_id = job.at_gate()
        job.bridge.press(CFG, "reject")
        job.daemon.wait_for_finish(job_id, timeout_s=30)
        assert json.loads(job.bridge.button(CFG, "approve")) == {"decision": "approve"}


def test_the_mesh_gate_names_its_job_and_approving_it_is_preparing_again(tmp_path: Path) -> None:
    """Approve there goes on to slice: it isn't a job on its way to the printer."""
    steps: list[Step] = []
    job = Job(tmp_path, MemoryBridge(), on_step=lambda job_id, part, step: steps.append(step))
    job.stl = write_stl("open_box", tmp_path)
    with job.daemon:
        job_id = job.at_gate()
        assert json.loads(job.bridge.button(CFG, "approve"))["job_id"] == job_id
        job.bridge.press(CFG, "approve")
        _wait(lambda: len(steps) > 2)
        assert steps[:3] == [Step.PREPARING, Step.AT_GATE, Step.PREPARING]


# --------------------------------------------------------------------------
# One live process, and what a second is told
# --------------------------------------------------------------------------


def test_a_second_start_is_told_the_job_its_part_and_each_step(tmp_path: Path) -> None:
    work = tmp_path / "jobs"
    ledger_file = tmp_path / "ledger.sqlite"
    with only_live(work, ledger_file, "cube.stl") as live:
        pid = os.getpid()
        assert _refused(work) == (
            "Not started, as only one runs at a time: a job for cube.stl is preparing, "
            f"in process {pid}."
        )

        job = Job(tmp_path, MemoryBridge(), on_step=live.at)
        uploading, release = threading.Event(), threading.Event()
        upload = job.printer.upload

        def slow_upload(local_path: Path) -> str:
            uploading.set()
            assert release.wait(30)
            return upload(local_path)

        job.printer.upload = slow_upload  # type: ignore[method-assign]
        starting, started = threading.Event(), threading.Event()
        start = job.printer.start

        def slow_start(remote_name: str) -> PrinterSnapshot:
            starting.set()
            assert started.wait(30)
            return start(remote_name)

        job.printer.start = slow_start  # type: ignore[method-assign]
        with job.daemon:
            job_id = job.at_gate()

            def told(doing: str) -> str:
                return (f"Not started, as only one runs at a time: job {job_id} (cube.stl) is "
                        f"{doing}, in process {pid}.")

            assert _refused(work) == told("at the gate, waiting for Approve or Reject")
            job.bridge.press(CFG, "approve")
            assert uploading.wait(30)
            assert _refused(work) == told("approved and being sent to the printer")
            release.set()
            assert starting.wait(30)
            assert job.ledger.get(job_id) is not None, "its row is written before the start"
            assert _refused(work) == told("approved and being sent to the printer")
            started.set()
            assert job.daemon.wait_for_finish(job_id, timeout_s=30).printed
            assert _refused(work) == told("printing")
            job.ledger.record_ended(job_id, 9)
            assert _refused(work) == told("waiting for an outcome")
            job.ledger.record_answer(job_id, Answer(result=Result.SUCCESS, symptom=Symptom.NONE))
            assert _refused(work) == told("ending")


def test_preparing_is_said_with_the_jobs_id_once_it_has_one(tmp_path: Path) -> None:
    with only_live(tmp_path, tmp_path / "no-ledger.sqlite", "cube.stl") as live:
        live.at("job-1", "two\nlines.stl", Step.PREPARING)
        assert "job job-1 (two lines.stl) is preparing" in _refused(tmp_path)


def test_a_start_the_printer_didnt_confirm_is_said_as_that(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as ledger:
        ledger.record_start_requested(record("job-1"))
        ledger.record_start_failed("job-1", "TimeoutError: never left a ready state")
    with only_live(tmp_path, path, "cube.stl") as live:
        live.at("job-1", "cube.stl", Step.ENDING)
        assert "waiting on a start the printer didn't confirm" in _refused(tmp_path)


def test_serve_is_said_with_the_print_it_serves(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    with Ledger(path) as ledger, only_live(tmp_path, path, None):
        serving = ("Not started, as only one runs at a time: print_job.py --serve is running "
                   f"(process {os.getpid()}), ")
        assert _refused(tmp_path) == serving + "with no print open."
        ledger.record_start_requested(record("job-1"))
        ledger.record_started("job-1")
        assert _refused(tmp_path) == serving + "and job job-1 (cube.stl) is printing."
        ledger.record_ended("job-1", 9)
        assert _refused(tmp_path) == serving + "and job job-1 (cube.stl) is waiting for an outcome."


def test_a_ledger_that_cant_be_read_still_gets_a_refusal(tmp_path: Path) -> None:
    broken = tmp_path / "ledger.sqlite"
    broken.write_text("not a database, and long enough that SQLite reads a header from it " * 4)
    with only_live(tmp_path, broken, "cube.stl") as live:
        live.at("job-1", "cube.stl", Step.AT_GATE)
        assert "is at the gate" in _refused(tmp_path)


def test_a_holder_that_hasnt_said_who_it_is_still_keeps_a_second_out(tmp_path: Path) -> None:
    fd = os.open(tmp_path / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        assert _refused(tmp_path) == (
            "Not started, as only one runs at a time and another print_job.py is live."
        )
    finally:
        os.close(fd)


def test_what_a_killed_holder_left_is_never_the_next_ones(tmp_path: Path) -> None:
    stale = Holder(pid=1, ledger=tmp_path / "l.sqlite", part="old.stl", job_id="old-job")
    (tmp_path / LOCK_NAME).write_text(stale.model_dump_json())
    fd = _take(tmp_path)  # the next has the lock and hasn't written who it is yet
    try:
        assert "old" not in _refused(tmp_path)
    finally:
        os.close(fd)
    (tmp_path / LOCK_NAME).write_text(stale.model_dump_json())
    with only_live(tmp_path, tmp_path / "l.sqlite", "new.stl"):
        said = _refused(tmp_path)
    assert "new.stl" in said and "old" not in said


def test_a_step_after_the_lock_is_let_go_writes_nothing(tmp_path: Path) -> None:
    """A job's worker can outlive the command's wait for it."""
    with only_live(tmp_path, tmp_path / "l.sqlite", "cube.stl") as live:
        pass
    live.at("job-1", "cube.stl", Step.ENDING)
    assert (tmp_path / LOCK_NAME).read_bytes() == b""


# --------------------------------------------------------------------------
# print_job.py
# --------------------------------------------------------------------------


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("print_job", ROOT / "scripts" / "print_job.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _settings(tmp_path: Path) -> Path:
    path = tmp_path / "settings.env"
    path.write_text(
        "MQTT_HOST=broker.invalid\n"
        "MQTT_USER=print-shop\n"
        "PRINTER_HOST=printer.invalid\n"
        f"PRINT_SHOP_LEDGER={tmp_path}/ledger.sqlite\n"
    )
    return path


def _start(kind: str, tmp_path: Path) -> list[str]:
    if kind == "part":
        return [str(write_stl("cube", tmp_path)), "--nozzle", "0.4", "--filament", "Elegoo PLA",
                "--answers", str(DATA / "answers" / "cube.json")]
    if kind == "sliced":
        return ["--sliced", str(write_gcode(tmp_path))]
    if kind == "again":
        return ["--again", "a-past-job"]
    return ["--serve"]


@pytest.mark.parametrize("armed", [False, True], ids=["unarmed", "armed"])
@pytest.mark.parametrize("kind", ["part", "sliced", "again", "serve"])
def test_a_second_start_is_refused_and_has_published_and_started_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    kind: str, armed: bool,
) -> None:
    """The issue's reproduction: the first's card stayed up only if the second did nothing."""
    script, settings = _script(), _settings(tmp_path)
    args = [*_start(kind, tmp_path), "--settings", str(settings)]
    work = tmp_path / "jobs"
    bridge = MemoryBridge()
    reached: list[str] = []

    def would_publish(**kwargs: Any) -> SimpleNamespace:
        reached.append("build_run")
        raise AssertionError("a second start built its run")

    def would_arm(**kwargs: Any) -> None:
        reached.append("live_printer")
        raise AssertionError("a second start armed the printer")

    monkeypatch.setattr(script, "WORK_DIR", work)
    monkeypatch.setattr(script, "build_run", would_publish)
    monkeypatch.setattr(script, "live_printer", would_arm)
    # Never the environment's flag: no test sets it. The script is only told it was given.
    monkeypatch.setattr(script, "hardware_allowed", lambda: armed)

    ledger_file = tmp_path / "ledger.sqlite"
    with only_live(work, ledger_file, "cube.stl") as live:
        first = Job(tmp_path / "first", bridge, on_step=live.at)
        with first.daemon:
            job_id = first.at_gate()
            published = list(bridge.published)
            files = {p: p.read_bytes() for p in work.rglob("*") if p.is_file()}
            capsys.readouterr()

            assert script.main(args) == 2

            shown = capsys.readouterr()
            assert shown.out == "", "nothing but the refusal"
            assert shown.err == (
                f"Not started, as only one runs at a time: job {job_id} (cube.stl) is at the "
                f"gate, waiting for Approve or Reject, in process {os.getpid()}.\n"
            )
            assert bridge.published == published, "it published nothing"
            assert bridge.retained_json(gate_topic(CFG))["pending"] is True
            assert reached == [], "it built and armed nothing"
            assert {p: p.read_bytes() for p in work.rglob("*") if p.is_file()} == files, (
                "it composed no profiles over the live job's, and wrote no log"
            )
            assert not ledger_file.exists(), "it didn't so much as make a ledger"


@pytest.mark.parametrize("kind", ["part", "sliced", "again", "serve"])
def test_the_lock_is_let_go_when_the_command_ends_however_it_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    script, settings = _script(), _settings(tmp_path)
    work = tmp_path / "jobs"
    monkeypatch.setattr(script, "WORK_DIR", work)
    monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)

    class Broke(Exception):
        pass

    def broke(**kwargs: Any) -> None:
        assert "only one runs at a time" in _refused(work), "held before anything is built"
        raise Broke

    monkeypatch.setattr(script, "build_run", broke)
    args = [*_start(kind, tmp_path), "--settings", str(settings)]
    if kind == "again":
        assert script.main(args) == 2  # no such job, said before anything is built
    else:
        with pytest.raises(Broke):
            script.main(args)
    with only_live(work, tmp_path / "ledger.sqlite", "next.stl"):
        pass


# --------------------------------------------------------------------------
# Across processes
# --------------------------------------------------------------------------

#: `print_job.py` at its gate, in a process of its own: the real script, lock and daemon, with
#: the broker a fake and no printer.
AT_GATE = """
import importlib.util, sys
from types import SimpleNamespace
from print_shop.ledger import Ledger, ledger_path
from print_shop.orchestrator.daemon import Daemon
from tests.fakes import MemoryBridge
from tests.test_job_e2e import CFG
spec = importlib.util.spec_from_file_location("print_job", "scripts/print_job.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)
def build(**kw):
    ledger = Ledger(ledger_path(kw["settings"]))
    daemon = Daemon(cfg=CFG, profiles=None, workdir=script.WORK_DIR, ledger=ledger,
                    bridge=lambda cfg: MemoryBridge(), show=kw["show"], store=kw["store"],
                    on_step=kw["on_step"])
    return SimpleNamespace(daemon=daemon, ledger=ledger)
script.build_run = build
sys.exit(script.main(sys.argv[1:]))
"""


class Mac:
    """A throwaway HOME, and commands run in it as processes of their own."""

    def __init__(self, tmp_path: Path) -> None:
        self.settings = _settings(tmp_path)
        self.part = write_gcode(tmp_path)
        self.lock = tmp_path / "home" / "print-shop" / "jobs" / LOCK_NAME
        # Built from nothing, so neither the arming flag nor a setting comes in with it.
        self.env = {"HOME": str(tmp_path / "home"), "PATH": os.environ["PATH"],
                    "PYTHONPATH": str(ROOT), "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8"}
        self.running: list[subprocess.Popen[str]] = []

    def popen(self, *args: str) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [sys.executable, *args], cwd=ROOT, env=self.env, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.running.append(process)
        return process

    def run(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, f"scripts/{script}.py", "--settings", str(self.settings), *args],
            cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=60,
            stdin=subprocess.DEVNULL,
        )

    def job_at_gate(self) -> tuple[subprocess.Popen[str], Holder]:
        process = self.popen(
            "-c", AT_GATE, "--sliced", str(self.part), "--settings", str(self.settings)
        )

        def holder() -> Holder | None:
            assert process.poll() is None, process.communicate()[0]
            try:
                found = Holder.model_validate_json(self.lock.read_bytes())
            except (OSError, ValueError):
                return None
            return found if found.pid == process.pid and found.step is Step.AT_GATE else None

        _wait(holder, 60)
        found = holder()
        assert found is not None
        return process, found

    def stop(self) -> None:
        for process in self.running:
            process.kill()
            process.communicate(timeout=30)


@pytest.fixture
def mac(tmp_path: Path) -> Iterator[Mac]:
    made = Mac(tmp_path)
    try:
        yield made
    finally:
        made.stop()


@pytest.mark.parametrize("how", [signal.SIGKILL, signal.SIGTERM, signal.SIGHUP],
                         ids=["killed", "terminated", "terminal closed"])
def test_after_the_first_is_killed_the_next_start_works_with_nothing_to_clean_up(
    mac: Mac, how: signal.Signals
) -> None:
    first, was = mac.job_at_gate()
    first.send_signal(how)
    first.wait(timeout=30)
    assert mac.lock.read_text(), "what it wrote is still there, as nothing tidied up"

    _, now = mac.job_at_gate()
    assert now.job_id != was.job_id


def test_what_a_job_runs_doesnt_keep_a_killed_jobs_lock(tmp_path: Path) -> None:
    """Orca is a child, and may outlive the process that was killed."""
    holding = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time\n"
         "from pathlib import Path\n"
         "from print_shop.orchestrator.live import only_live\n"
         "work = Path(sys.argv[1])\n"
         "with only_live(work, work / 'l.sqlite', 'cube.stl'):\n"
         "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
         "                             stdout=subprocess.DEVNULL)\n"
         "    print(child.pid, flush=True)\n"
         "    time.sleep(60)\n",
         str(tmp_path)],
        stdout=subprocess.PIPE, text=True,
    )
    assert holding.stdout is not None
    child = int(holding.stdout.readline())
    try:
        holding.kill()
        holding.wait(timeout=30)
        os.kill(child, 0)  # still running
        with only_live(tmp_path, tmp_path / "l.sqlite", "next.stl"):
            pass
    finally:
        holding.stdout.close()
        os.kill(child, signal.SIGKILL)


def test_the_commands_that_only_read_run_beside_a_job_and_a_real_second_start_doesnt(
    mac: Mac,
) -> None:
    first, held = mac.job_at_gate()

    second = mac.run("print_job", "--serve")
    assert second.returncode == 2
    assert second.stderr == (
        f"Not started, as only one runs at a time: job {held.job_id} ({mac.part.name}) is at "
        f"the gate, waiting for Approve or Reject, in process {first.pid}.\n"
    )

    listed = mac.run("print_job", "--list")
    assert listed.returncode == 0, listed.stderr
    # The job at its gate waits for the person, so it's listed (mqucifer/print-shop#209).
    assert listed.stdout == (
        "the ledger is empty\n\nWaiting for you (`--resume JOB_ID`):\n"
        f"  job {held.job_id}  {mac.part.name}  ready to print, since "
    ) + listed.stdout.split(", since ", 1)[1]
    filaments = mac.run("print_job", "--filaments")
    assert filaments.returncode == 0 and "Elegoo PLA" in filaments.stdout, filaments.stderr
    for stock in ((), ("printer", "add", "cc1", "Elegoo Centauri Carbon"), ("fit", "0.4")):
        ran = mac.run("stock", *stock)
        assert ran.returncode == 0, ran.stderr

    desk = mac.popen("scripts/print_desk.py", "--port", "0", "--settings", str(mac.settings))
    assert desk.stdout is not None
    said = desk.stdout.readline()
    assert "The print desk: http://" in said, said
    url = said.split()[3]
    with urllib.request.urlopen(url, timeout=30) as page:
        assert page.status == 200 and held.job_id in page.read().decode()

    assert first.poll() is None, "the job is still at its gate"
    still = Holder.model_validate_json(mac.lock.read_bytes())
    assert still == held
