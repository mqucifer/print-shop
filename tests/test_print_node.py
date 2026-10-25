"""Specification for the hardware boundary — `src/printer/centauri.py` and `print_node`.

This is the one place in the project where a bug moves a 250 °C nozzle, so the
tests are organised around what must *refuse*, not around what must work.

Three layers, deliberately:

1. **`preflight` is pure** and is tested directly. Every refusal it can make is
   asserted, including the one that matters most — that an *unrecognised*
   printer state blocks. A whitelist is only a whitelist if something proves
   the default is "no".

2. **The node is driven through the real compiled graph**, with a double
   standing in for the printer. `PROJECT.md` learning 2 is that a stub passes
   forever, so `FakePrinter` is kept to the narrowest possible job: it stands
   in for the *transport*, and it records call order so "did it start a print
   it should have refused?" is answerable. It never decides anything.

3. **The real client runs against the real machine**, marked `printer` and
   deselected by default. That test uploads and deletes; it never calls
   `start_print`. Upload is reversible — `delete_files` is verified working —
   and starting a print is not. What the live test buys is the thing the fake
   cannot: proof that the transport, the mainboard id, and the status codes are
   what this code believes they are.

The gap left open on purpose: **nothing here has ever called `start_print` for
real.** The confirmation logic in `_confirm_started` is unexercised against
hardware. That is stated rather than hidden, and it is the one thing a human
has to do by hand before this path can be trusted.
"""

from __future__ import annotations

import ast
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from print_shop.core.contracts import Gate
from print_shop.core.state import EntryPath, PrintStatus
from print_shop.ledger import Ledger
from print_shop.orchestrator.graph import build_graph, new_job
from print_shop.printer.centauri import (
    READY_TO_START,
    PreflightFailed,
    PrinterSnapshot,
    preflight,
)
from print_shop.slicer.naming import gcode_filename
from print_shop.slicer.orca import SlicerProfiles
from tests.fixtures import write_stl

ORCA = Path("/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer")
DATADIR = Path(os.path.expanduser("~/Library/Application Support/OrcaSlicer"))
_SYS = DATADIR / "system" / "Elegoo"
MACHINE = _SYS / "machine" / "ECC" / "Elegoo Centauri Carbon 0.4 nozzle.json"
PROCESS = _SYS / "process" / "ECC" / "0.20mm Standard @Elegoo CC 0.4 nozzle.json"
FILAMENT = _SYS / "filament" / "ECC" / "Elegoo PLA @ECC.json"

needs_orca = pytest.mark.skipif(
    not (ORCA.exists() and MACHINE.exists() and PROCESS.exists()),
    reason="OrcaSlicer or the Elegoo Centauri Carbon profiles are not installed",
)

#: Free space on the real machine when measured: ~5.1 GB.
PLENTY = 5_098_209_280

#: SDCP `PrintInfo.Status` codes. 0 IDLE / 8 STOPPED / 9 COMPLETED are the
#: ready set; the rest are named here so the tests read as states, not numbers.
IDLE, PRINTING, PAUSED, ERROR, AUTO_LEVELING, STOPPED, COMPLETED = 0, 13, 6, 14, 15, 8, 9


def idle(free_bytes: int = PLENTY) -> PrinterSnapshot:
    return PrinterSnapshot(
        print_status=IDLE, active_filename=None, free_bytes=free_bytes
    )


# --------------------------------------------------------------------------
# preflight — pure, always runs
# --------------------------------------------------------------------------


def test_an_idle_printer_with_room_accepts(tmp_path: Path) -> None:
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    assert preflight(idle(), gcode).accepted


@pytest.mark.parametrize("status", [PRINTING, PAUSED, ERROR, AUTO_LEVELING])
def test_a_printer_that_is_not_free_is_refused(tmp_path: Path, status: int) -> None:
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    snapshot = PrinterSnapshot(
        print_status=status, active_filename=None, free_bytes=PLENTY
    )
    verdict = preflight(snapshot, gcode)
    assert not verdict.accepted
    assert [v.rule for v in verdict.violations] == ["printer.ready"]


@pytest.mark.parametrize("status", [STOPPED, COMPLETED])
def test_a_finished_or_stopped_printer_is_free(tmp_path: Path, status: int) -> None:
    """A print that ended is not a print in progress."""
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    snapshot = PrinterSnapshot(
        print_status=status, active_filename=None, free_bytes=PLENTY
    )
    assert preflight(snapshot, gcode).accepted


def test_an_unknown_status_code_blocks(tmp_path: Path) -> None:
    """The point of a whitelist. Firmware gains states; none of them mean 'go'.

    99 is not in Elegoo's table today. If READY_TO_START were ever inverted
    into a blacklist this is the test that would notice.
    """
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    snapshot = PrinterSnapshot(print_status=99, active_filename=None, free_bytes=PLENTY)
    assert not preflight(snapshot, gcode).accepted
    assert 99 not in READY_TO_START


def test_a_missing_status_blocks(tmp_path: Path) -> None:
    """`print_status` is None when the printer told us nothing useful."""
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    snapshot = PrinterSnapshot(
        print_status=None, active_filename=None, free_bytes=PLENTY
    )
    assert not preflight(snapshot, gcode).accepted


def test_an_idle_code_still_blocks_while_a_file_is_held(tmp_path: Path) -> None:
    """Status and file disagree — believe the more alarming one."""
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 1000)
    snapshot = PrinterSnapshot(
        print_status=IDLE, active_filename="something.gcode", free_bytes=PLENTY
    )
    assert not preflight(snapshot, gcode).accepted


def test_a_full_printer_is_refused(tmp_path: Path) -> None:
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G1" * 100_000)
    verdict = preflight(idle(free_bytes=1000), gcode)
    assert not verdict.accepted
    assert [v.rule for v in verdict.violations] == ["printer.free_space"]


def test_headroom_is_required_not_just_a_bare_fit(tmp_path: Path) -> None:
    """A file that fits to the last byte still leaves the machine with nothing."""
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G" * 10_000)
    assert not preflight(idle(free_bytes=10_000), gcode).accepted


def test_unknown_free_space_is_refused_rather_than_assumed(tmp_path: Path) -> None:
    """The client reports 0 when the printer did not say. 0 must not mean 'fine'."""
    gcode = tmp_path / "x.gcode"
    gcode.write_bytes(b"G" * 10_000)
    assert not preflight(idle(free_bytes=0), gcode).accepted


def test_a_missing_gcode_is_refused(tmp_path: Path) -> None:
    verdict = preflight(idle(), tmp_path / "never-sliced.gcode")
    assert not verdict.accepted
    assert "gcode.present" in [v.rule for v in verdict.violations]


def test_every_reason_is_reported_at_once(tmp_path: Path) -> None:
    """A human fixing one problem should not discover the next one by retrying."""
    snapshot = PrinterSnapshot(
        print_status=PRINTING, active_filename="other.gcode", free_bytes=0
    )
    verdict = preflight(snapshot, tmp_path / "never-sliced.gcode")
    assert {v.rule for v in verdict.violations} == {"gcode.present", "printer.ready"}


def test_no_preflight_failure_is_auto_fixable(tmp_path: Path) -> None:
    """None of these is a value a corrector could nudge into range.

    A busy printer needs time, a full one needs a human, a missing file needs a
    re-slice. If any of them were fixable the loop would retry into hardware.
    """
    snapshot = PrinterSnapshot(print_status=PRINTING, active_filename=None, free_bytes=0)
    verdict = preflight(snapshot, tmp_path / "never-sliced.gcode")
    assert not verdict.fixable
    assert all(not v.auto_fixable for v in verdict.violations)


# --------------------------------------------------------------------------
# enable_control containment
# --------------------------------------------------------------------------


def test_enable_control_is_armed_in_exactly_one_place() -> None:
    """`enable_control=True` is the switch that lets this project move hardware.

    It must appear exactly once, inside `live_printer()`, which nothing in
    `src/` calls. Arming is then something a caller does by name, and the set
    of processes that can move the machine is the set that mention it.

    This test is stricter than it was. It used to accept the literal anywhere
    inside `print_node`, which was true while the node *also* built a client
    from the environment whenever the caller passed none — and that default is
    what let the test suite start a print on 2026-09-16.
    """
    sources = sorted(Path("src/print_shop").rglob("*.py"))
    assert len(sources) >= 10, "bad glob would make this test vacuous"

    hits: list[tuple[Path, int]] = []
    for path in sources:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "enable_control"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    hits.append((path, node.lineno))

    assert len(hits) == 1, f"enable_control=True must appear exactly once, found {hits}"

    path, lineno = hits[0]
    assert path == Path("src/print_shop/printer/centauri.py")

    tree = ast.parse(path.read_text())
    enclosing = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.lineno <= lineno <= (n.end_lineno or n.lineno)
    ]
    assert enclosing == ["live_printer"], (
        f"enable_control=True is at {path}:{lineno}, inside {enclosing}, but it "
        f"belongs in live_printer() and nowhere else — one named function a "
        f"caller has to invoke on purpose."
    )


# --------------------------------------------------------------------------
# The node, through the real graph
# --------------------------------------------------------------------------


@dataclass
class FakePrinter:
    """Stands in for the transport, and for nothing else.

    It makes no decisions: `preflight` is the only thing that judges a
    snapshot, and this just hands one over. `calls` exists so a test can assert
    that `start` was *not* reached — which is the assertion that matters, since
    every refusal in this file is about a print that must not begin.
    """

    snapshot_value: PrinterSnapshot
    stored_as: str | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def snapshot(self) -> PrinterSnapshot:
        self.calls.append(("snapshot", ""))
        return self.snapshot_value

    def upload(self, local_path: Path) -> str:
        self.calls.append(("upload", str(local_path)))
        return self.stored_as if self.stored_as is not None else Path(local_path).name

    def start(self, remote_name: str) -> PrinterSnapshot:
        self.calls.append(("start", remote_name))
        return self.snapshot_value.model_copy(
            update={"print_status": PRINTING, "active_filename": remote_name}
        )

    @property
    def verbs(self) -> list[str]:
        return [verb for verb, _ in self.calls]


@pytest.fixture
def profiles() -> SlicerProfiles:
    return SlicerProfiles(
        machine=MACHINE,
        process=PROCESS,
        filament=FILAMENT if FILAMENT.exists() else None,
        datadir=DATADIR,
    )


@pytest.fixture
def build(
    profiles: SlicerProfiles, tmp_path: Path
) -> Iterator[Callable[[FakePrinter], Any]]:
    """Build a graph around a given fake printer, on a real checkpointer.

    `Command(resume=...)` is how an approval re-enters a parked graph, and
    LangGraph refuses it without a checkpointer — so the gate cannot be
    exercised at all without one. The connection is held open for the lifetime
    of the graph (CODE_REVIEW.md P0-3) and closed at teardown.
    """
    conns: list[sqlite3.Connection] = []

    def _build(fake: FakePrinter) -> Any:
        conn = sqlite3.connect(
            tmp_path / f"jobs-{len(conns)}.sqlite", check_same_thread=False
        )
        conns.append(conn)
        return build_graph(
            profiles,
            outdir_root=tmp_path / "jobs",
            checkpointer=SqliteSaver(conn),
            printer=fake,
            ledger=Ledger(tmp_path / "ledger.sqlite"),
        )

    try:
        yield _build
    finally:
        for conn in conns:
            conn.close()


def run_to_gate(app: Any, tmp_path: Path, shape: str = "cube") -> dict[str, Any]:
    stl = write_stl(shape, tmp_path)
    job = new_job(stl, entry=EntryPath.STL_UPLOAD)
    cfg: dict[str, Any] = {"configurable": {"thread_id": job["job_id"]}}
    out = app.invoke(job, config=cfg)
    assert out["__interrupt__"][0].value["gate"] == Gate.BEFORE_PRINT.value
    return {"cfg": cfg, "job_id": job["job_id"], "state": out}


@pytest.mark.slicer
@needs_orca
def test_an_approved_job_uploads_then_starts(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    """The milestone: approval at the gate puts a file on the printer and starts it."""
    fake = FakePrinter(snapshot_value=idle())
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    out = app.invoke(Command(resume={"approved": True}), config=run["cfg"])

    assert fake.verbs == ["snapshot", "upload", "start"], "wrong order or a missing step"
    assert out["print_status"] is PrintStatus.PRINTING
    assert out["print_attempts"] == 1

    # It started the file it uploaded, and that file carries this job's id.
    _, started = fake.calls[-1]
    assert run["job_id"] in started


@pytest.mark.slicer
@needs_orca
def test_a_busy_printer_is_never_sent_a_job(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    """The restart case: the daemon died mid-print and came back not knowing."""
    busy = PrinterSnapshot(
        print_status=PRINTING, active_filename="someone-elses.gcode", free_bytes=PLENTY
    )
    fake = FakePrinter(snapshot_value=busy)
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    with pytest.raises(PreflightFailed) as exc:
        app.invoke(Command(resume={"approved": True}), config=run["cfg"])

    assert "printer.ready" in [v.rule for v in exc.value.verdict.violations]
    assert fake.verbs == ["snapshot"], "it uploaded or started despite a busy printer"


@pytest.mark.slicer
@needs_orca
def test_a_full_printer_is_never_sent_a_job(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    fake = FakePrinter(snapshot_value=idle(free_bytes=1024))
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    with pytest.raises(PreflightFailed):
        app.invoke(Command(resume={"approved": True}), config=run["cfg"])

    assert "start" not in fake.verbs


@pytest.mark.slicer
@needs_orca
def test_a_file_the_printer_renamed_is_never_started(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    """`upload_file` returns the name the *printer* chose, not the one we sent.

    If the machine truncated or deduplicated it, the job id no longer
    round-trips and we are about to start a file we cannot account for. This is
    the call site that makes `job_id_from_filename` load-bearing rather than
    decorative.
    """
    fake = FakePrinter(
        snapshot_value=idle(),
        stored_as=gcode_filename(job_id=str(uuid.uuid4()), part="cube", print_time_s=60),
    )
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    with pytest.raises(RuntimeError, match="does not carry job"):
        app.invoke(Command(resume={"approved": True}), config=run["cfg"])

    assert fake.verbs == ["snapshot", "upload"], "it started a file it could not identify"


@pytest.mark.slicer
@needs_orca
def test_a_file_with_no_job_id_at_all_is_never_started(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    """The printer storing it as `plate_1.gcode` is the collision this all guards."""
    fake = FakePrinter(snapshot_value=idle(), stored_as="plate_1.gcode")
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    with pytest.raises(RuntimeError, match="does not carry job"):
        app.invoke(Command(resume={"approved": True}), config=run["cfg"])

    assert "start" not in fake.verbs


@pytest.mark.slicer
@needs_orca
def test_a_graph_with_no_printer_cannot_reach_one(
    profiles: SlicerProfiles, tmp_path: Path
) -> None:
    """The regression test for 2026-09-16.

    `print_node` used to fall back to building an armed client out of `.env`
    whenever `build_graph` was given no printer. `tests/test_daemon.py`
    publishes a real MQTT approval, so the moment `PRINTER_HOST` existed that
    test became live and started a print on the real machine.

    An approval must now fail at the boundary rather than reach for a printer
    nobody named. Note what this asserts about *reachability*: the job runs,
    slices, parks, and is genuinely approved — everything works — and the
    hardware is still not touched.
    """
    conn = sqlite3.connect(tmp_path / "unarmed.sqlite", check_same_thread=False)
    try:
        app = build_graph(
            profiles,
            outdir_root=tmp_path / "jobs",
            checkpointer=SqliteSaver(conn),
        )
        run = run_to_gate(app, tmp_path)
        with pytest.raises(RuntimeError, match="built without a printer"):
            app.invoke(Command(resume={"approved": True}), config=run["cfg"])
    finally:
        conn.close()


def test_nothing_in_src_builds_an_armed_printer_for_itself() -> None:
    """`live_printer()` is for a caller to invoke, not for `src/` to reach for.

    The moment any module calls it on its own behalf, arming stops being a
    decision someone made and becomes a default again — which is precisely the
    shape of the 2026-09-16 defect.
    """
    callers: list[str] = []
    for path in sorted(Path("src/print_shop").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "live_printer"
            ):
                callers.append(f"{path}:{node.lineno}")
    assert callers == [], f"src/ must not call live_printer(): {callers}"


@pytest.mark.slicer
@needs_orca
def test_rejecting_never_touches_the_printer(
    build: Callable[[FakePrinter], Any], tmp_path: Path
) -> None:
    """AGENTS.md rule 5 from the other side: no approval, no contact at all."""
    fake = FakePrinter(snapshot_value=idle())
    app = build(fake)
    run = run_to_gate(app, tmp_path)

    app.invoke(Command(resume={"approved": False}), config=run["cfg"])

    assert fake.calls == [], "the printer was contacted for a rejected job"


# --------------------------------------------------------------------------
# The disarm guard itself
# --------------------------------------------------------------------------


def test_the_guard_still_patches_something_that_exists() -> None:
    """Guard the guard.

    The disarm works by wrapping `Printer.connect` and intercepting
    `enable_control`. If pycentauri renamed either, the patch would still apply
    cleanly to a name nothing calls and the protection would vanish in silence.
    """
    import inspect

    from pycentauri import Printer

    assert hasattr(Printer, "connect")
    params = inspect.signature(Printer.connect).parameters
    assert "enable_control" in params, (
        "pycentauri.Printer.connect no longer takes enable_control — "
        "tests/conftest.py is guarding a door that has moved."
    )
    assert hasattr(Printer, "_require_control"), (
        "pycentauri no longer funnels write actions through _require_control; "
        "re-check that enable_control is still the only thing arming a client."
    )


def test_live_printer_refuses_unless_the_environment_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Addresses come from `.env`; arming does not.

    `PRINTER_HOST` living in `.env` is what made the machine reachable by
    accident. The flag that arms it is kept out of `.env` on purpose, so it has
    to be supplied per invocation.
    """
    from print_shop.printer.centauri import ALLOW_HARDWARE_ENV, live_printer

    monkeypatch.delenv(ALLOW_HARDWARE_ENV, raising=False)
    with pytest.raises(RuntimeError, match=ALLOW_HARDWARE_ENV):
        live_printer()


def test_auto_leveling_is_off_unless_asked_for() -> None:
    """A levelling sweep throws the fans to 100% for minutes.

    That is a decision about the machine, not about the job, and pycentauri
    defaults it on. Printing something must not drag it along.
    """
    from print_shop.printer.centauri import CentauriClient, PrinterConfig

    cfg = PrinterConfig(host="printer.invalid", mainboard_id="deadbeef")
    assert CentauriClient(cfg).auto_leveling is False
    assert CentauriClient(cfg, auto_leveling=True).auto_leveling is True


def test_the_start_confirmation_window_outlasts_the_machines_own_startup() -> None:
    """The finding all three reviewer passes agreed on, 2026-09-16.

    The first version of this file watched 30 s for the printer to leave the
    ready set — while the same commit's `AGENTS.md` entry recorded that this
    machine spends *minutes* cycling PREHEATING / HOMING after a stop.

    The failure runs backwards from the usual one: too short a window raises on
    the **success** path, so a print a human approved gets recorded as failed
    while the nozzle heats. `PROJECT.md` learning 2 with the sign flipped.

    300 s is generous rather than measured, because nobody has started a print
    on purpose yet. This test exists to stop it quietly shrinking again.
    """
    from print_shop.printer.centauri import _START_CONFIRM_TIMEOUT_S

    assert _START_CONFIRM_TIMEOUT_S >= 180.0, (
        "the confirmation window is shorter than a bed takes to reach 60 C; "
        "see AGENTS.md on how long this machine cycles after a state change"
    )


def test_completed_is_treated_as_free_and_that_is_a_decision() -> None:
    """Pins a judgement call so it cannot drift silently.

    `READY_TO_START` contains COMPLETED (9), which means a printer that has just
    finished a part — with that part still sitting on the plate — reads as
    ready. A reviewer pass raised this and it is real: the bed-clear check is a
    `BACKLOG.md` item precisely because nothing verifies the plate is empty, and
    including COMPLETED asserts the opposite.

    It stays in for now because the human at `Gate.BEFORE_PRINT` is the check,
    and because it is unknown whether the CC1 ever returns to IDLE on its own —
    requiring IDLE could block every print after the first. Measure that before
    tightening.
    """
    from pycentauri.models import PrintStatus as Sdcp

    from print_shop.printer.centauri import READY_TO_START

    assert Sdcp.COMPLETED in READY_TO_START
    assert Sdcp.ERROR not in READY_TO_START
    assert Sdcp.PAUSED not in READY_TO_START
