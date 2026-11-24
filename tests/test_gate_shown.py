"""Each gate says which it is and why, in the terminal and the job's log (mqucifer/print-shop#200).

An STL whose mesh wasn't closed stopped at the mesh gate, and `print_job.py` showed "At the
approval gate" with no time, no plastic and no reason. It also showed only a job's first gate, so
one approved there never showed the approval gate's numbers. The meshes here are made up, the
slice is a recorded one, and the broker and printer are fakes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import trimesh

from print_shop.core.contracts import Gate
from print_shop.core.mqtt import gate_topic
from print_shop.ledger import Ledger
from print_shop.orchestrator.daemon import Daemon
from print_shop.setup import Proposer
from print_shop.setup.intake import replay_asker
from print_shop.setup.review import Review, ReviewRequest
from tests.fakes import CI_PROFILES, DATA, FramePrinter, MemoryBridge, RecordedSlicer
from tests.fixtures import write_stl
from tests.test_job_e2e import CFG
from tests.test_review import ANSWERS, _model, _questions

REASON = "Mesh is not manifold; cannot be trusted to slice."
ROOT = Path(__file__).resolve().parents[1]


class Desk:
    """`print_job.py` on fakes, and a person who answers each gate once the terminal has it."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        spec = importlib.util.spec_from_file_location(
            "print_job", ROOT / "scripts" / "print_job.py"
        )
        assert spec is not None and spec.loader is not None
        self.script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.script)
        self.tmp = tmp_path
        self.jobs = tmp_path / "jobs"
        self.jobs.mkdir()
        self.settings = tmp_path / "settings.env"
        self.settings.write_text(f"PRINT_SHOP_LEDGER={tmp_path}/ledger.sqlite\n")
        self.bridge = MemoryBridge()
        self.printer = FramePrinter()
        self.reviewed = 0
        #: How long the person takes over each gate.
        self.thinks_s = 0.0
        self._shown: list[dict[str, Any]] = []
        monkeypatch.delenv("PRINT_AGENT_ALLOW_HARDWARE", raising=False)
        monkeypatch.setattr(self.script, "WORK_DIR", self.jobs)
        monkeypatch.setattr(self.script, "build_run", self._build_run)

    def _review(self, request: ReviewRequest) -> Review:
        self.reviewed += 1
        return Review(approved=True)

    def _build_run(self, **kwargs: Any) -> SimpleNamespace:
        recordings = {(hashlib.sha256(self.part.read_bytes()).hexdigest(), "Cool Plate"):
                      DATA / "recorded" / "cube.gcode"}
        ledger = Ledger(self.tmp / "ledger.sqlite")
        daemon = Daemon(
            cfg=CFG, profiles=CI_PROFILES, workdir=self.jobs, printer=self.printer,
            slicer=RecordedSlicer(recordings), bridge=lambda cfg: self.bridge, ledger=ledger,
            proposer=Proposer(ask=_model, ask_questions=_questions),
            asker=replay_asker(ANSWERS), reviewer=self._review,
        )
        wait = daemon.wait_for_gate

        def waited(job_id: str, timeout_s: float, shown: int = 0) -> dict[str, Any] | None:
            gate = wait(job_id, timeout_s, shown)
            if gate is not None:
                self._shown.append(gate)
            return gate

        daemon.wait_for_gate = waited  # type: ignore[method-assign]
        return SimpleNamespace(daemon=daemon, ledger=ledger)

    def run(self, part: Path, *presses: str) -> int:
        """The command, each press given to the gate the terminal has just been handed."""
        self.part = part
        done = threading.Event()

        def person() -> None:
            for n, decision in enumerate(presses):
                while len(self._shown) <= n:
                    if done.wait(0.02):
                        return
                time.sleep(self.thinks_s)
                self.bridge.press(CFG, decision)

        thread = threading.Thread(target=person, daemon=True)
        thread.start()
        try:
            return int(self.script.main([
                str(part), "--nozzle", "0.4", "--filament", "Elegoo PLA",
                "--answers", str(DATA / "answers" / "cube.json"),
                "--settings", str(self.settings),
            ]))
        finally:
            done.set()
            thread.join(timeout=10)

    def log(self) -> str:
        (log,) = self.jobs.glob("*/job.log")
        return log.read_text()


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Desk]:
    yield Desk(tmp_path, monkeypatch)


@pytest.mark.parametrize("mesh", ["nicked_cube", "open_box"])
def test_a_mesh_that_isnt_closed_shows_the_mesh_gate_its_reason_and_its_facts(
    desk: Desk, capsys: pytest.CaptureFixture[str], mesh: str
) -> None:
    assert desk.run(write_stl(mesh, desk.tmp), "reject") == 0

    out = capsys.readouterr().out
    log = desk.log()
    lines = (
        "At the mesh gate, before any question or slice. Approve or Reject on the dashboard.",
        f"  because     {REASON}",
        "  is_manifold False",
        "  volume_mm3  None: a mesh that isn't closed has no volume",
        "  bbox_mm     20 x 20 x 20",
        "  Approve     goes on to slice a mesh this gate calls untrustworthy, as the file lays "
        "it: the questions and the review follow, then the gate before printing, where today "
        "the job fails whatever you answer. Nothing prints.",
        "  Reject      ends the job: nothing is sliced or printed",
    )
    for shown, where in ((out, "the terminal"), (log, "job.log")):
        assert "\n".join(lines) in shown, where
        assert "Ready to print" not in shown, where
        assert "None cm3" not in shown and "time        0 min" not in shown, where
        assert "  It waits until you answer it on the card." in shown, where
    assert desk.reviewed == 0, "nothing was asked or sliced before the gate"
    assert desk.printer.calls == []


def test_approved_at_the_mesh_gate_the_approval_gate_is_shown_after_the_review(
    desk: Desk, capsys: pytest.CaptureFixture[str]
) -> None:
    # Not the exit code: how a job ends at its second gate isn't this issue's, only what's shown.
    desk.run(write_stl("nicked_cube", desk.tmp), "approve", "reject")

    for shown in (capsys.readouterr().out, desk.log()):
        first = shown.index("At the mesh gate")
        second = shown.index(
            "Ready to print. Start it here or on the card, or reject it on the card.")
        assert first < second
        after = shown[second:]
        assert "  time        16 min\n" in after and "  plastic     " in after
        assert "  turned      [0.0, 0.0, 0.0]\n" in after
        assert REASON not in after
    assert desk.reviewed == 1
    assert desk.printer.calls == []


def test_a_gate_answered_before_the_terminal_looked_is_still_shown_and_logged(
    desk: Desk, capsys: pytest.CaptureFixture[str]
) -> None:
    """An answer can land as the gate is published, before the command's thread has read it."""
    publish = desk.bridge.publish

    def answered_at_once(topic: str, payload: Any, retain: bool = False, qos: int = 1) -> None:
        publish(topic, payload, retain, qos)
        if topic == gate_topic(CFG) and payload.get("pending"):
            at_mesh = payload["gate"] == Gate.REVIEW_PROPOSAL.value
            desk.bridge.press(CFG, "approve" if at_mesh else "reject")

    desk.bridge.publish = answered_at_once  # type: ignore[method-assign]
    desk.run(write_stl("nicked_cube", desk.tmp))
    for shown in (capsys.readouterr().out, desk.log()):
        assert shown.index("At the mesh gate") < shown.index("Ready to print")


@pytest.mark.parametrize("last", ["approve", "reject"])
def test_what_the_mesh_gate_says_of_approve_is_true(
    desk: Desk, capsys: pytest.CaptureFixture[str], last: str
) -> None:
    """After Approve there, the approval gate's answer fails the job, with nothing sent to the
    printer. Whether to fix that is a decision of its own, as it opens a road to the printer. A
    fix changes this test and the gate's Approve line together."""
    assert desk.run(write_stl("nicked_cube", desk.tmp), "approve", last) == 1
    out = capsys.readouterr().out
    assert "where today the job fails whatever you answer. Nothing prints." in out
    assert "The job ended without printing:" in out
    # An Approve there is a Start, which reads the printer and sends it nothing
    # (mqucifer/print-shop#209).
    assert [verb for verb, _ in desk.printer.calls if verb != "snapshot"] == []


def test_the_gate_before_printing_shows_as_it_did(
    desk: Desk, capsys: pytest.CaptureFixture[str]
) -> None:
    assert desk.run(write_stl("cube", desk.tmp), "reject") == 0

    (gate,) = [g for g in desk.bridge.on(gate_topic(CFG)) if g.get("pending")]
    expected = (
        "Ready to print. Start it here or on the card, or reject it on the card.\n"
        "  time        16 min\n"
        f"  plastic     {gate['filament_cm3']} cm3\n"
        f"  turned      {gate['orientation_deg']}\n"
        f"  because     {gate['orientation_rationale']}\n"
        "  Type start here, or Yes on the card. It waits until you start or reject it.\n"
    )
    for shown in (capsys.readouterr().out, desk.log()):
        assert expected in shown
        assert "after that it ends and nothing prints" not in shown
        assert "mesh gate" not in shown


@pytest.mark.parametrize(
    "gate",
    [g.value for g in Gate if g is not Gate.BEFORE_PRINT] + ["gate.one_not_written_yet", None],
)
@pytest.mark.parametrize("facts", [{}, {"is_manifold": False}, {"is_manifold": True}])
def test_no_gate_but_the_one_before_printing_is_shown_as_the_approval_gate(
    desk: Desk, gate: str | None, facts: dict[str, Any]
) -> None:
    lines: Callable[..., list[str]] = desk.script._gate_lines
    shown = "\n".join(lines({"gate": gate, "reason": "why it stopped", **facts}, turned=True))
    assert "approval gate" not in shown and "Ready to print" not in shown
    assert "why it stopped" in shown
    for number in ("time", "plastic", "turned"):
        assert f"  {number} " not in shown, "only the gate before printing has them"


def test_the_gates_say_they_wait_with_no_deadline(
    desk: Desk, capsys: pytest.CaptureFixture[str]
) -> None:
    """mqucifer/print-shop#209: no gate has an hour. Each says how it's answered, and that it
    waits. The mesh gate's line is in the test above."""
    assert desk.run(write_stl("cube", desk.tmp), "reject") == 0
    for shown in (capsys.readouterr().out, desk.log()):
        assert ("Type start here, or Yes on the card. It waits until you start or reject it."
                in shown)
        assert "answer by" not in shown and "Timed out" not in shown
    with Ledger(desk.tmp / "ledger.sqlite") as ledger:
        assert ledger.unprinted_jobs() == []


def test_the_time_at_the_mesh_gate_isnt_counted_against_the_slice(
    desk: Desk, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate is the person's time, as the questions and the review are
    (mqucifer/print-shop#125)."""
    monkeypatch.setattr(desk.script, "APPROVAL_WAIT_S", 3.0)
    desk.thinks_s = 3.5
    desk.run(write_stl("nicked_cube", desk.tmp), "approve", "reject")
    out = capsys.readouterr().out
    assert "Timed out" not in out
    assert "Ready to print" in out


def test_a_closed_mesh_with_no_pose_that_fits_isnt_called_unclosed(tmp_path: Path) -> None:
    """The same gate is reached from the orientation's check, and it used to give the mesh's
    reason there too."""
    (tmp_path / "work").mkdir()
    big = tmp_path / "big.stl"
    trimesh.creation.box(extents=(300, 300, 300)).export(big)
    bridge = MemoryBridge()
    daemon = Daemon(cfg=CFG, profiles=CI_PROFILES, workdir=tmp_path / "work",
                    slicer=RecordedSlicer(), bridge=lambda cfg: bridge,
                    ledger=Ledger(tmp_path / "ledger.sqlite"))
    with daemon:
        job_id = daemon.submit(big)
        daemon.wait_for_gate(job_id, timeout_s=30)
        gate = json.loads(bridge.retained[gate_topic(CFG)])
        assert gate["gate"] == Gate.REVIEW_PROPOSAL.value and gate["is_manifold"] is True
        assert "not manifold" not in gate["reason"]
        assert gate["reason"].startswith("No pose passed its check")
        assert "exceeds build volume" in gate["reason"]
        bridge.press(CFG, "reject")
        daemon.wait_for_finish(job_id, timeout_s=30)
