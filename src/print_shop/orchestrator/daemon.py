"""Long-running daemon that makes the approval gate a real button in Home Assistant.

This is the process that holds the MQTT subscription, publishes state, and
resumes the parked graph when a person presses Approve or Reject.

CONTRACTS.md §1: the pipeline is one process. This is that process gaining a
lifecycle, not a new service.

**Waiting is counted only while the job isn't with the person** (mqucifer/print-shop#125).
The intake's questions and the settings review take as long as the person takes; a caller's
wait for the gate counts from when the job last came back from them. The gate then waits
`GATE_WAIT_S` for Approve or Reject. A wait that runs out ends the job in one sentence, with a
ledger row whose outcome is that it timed out, and nothing is sent to the printer after it.

**An Approve with the wrong spool loaded starts nothing** (mqucifer/print-shop#218). The press is
checked before the graph resumes, on the stock and the ledger alone as they are when it's pressed
(`graph.why_not_start`): the printer is never waited on there. Held, it's spent: the gate says why
and waits for a new press, with its inbox emptied, so an earlier or doubled tap can't start the
job once the spool is loaded. A Reject that came while a press was checked wins, held or not. The
hour runs on from when the gate opened. The print node refuses the same, as the backstop.

**A press names its job** (mqucifer/print-shop#207). The buttons carry the job's id while its gate
is open, and a gate takes only a press that names its own job. That holds across processes, where
one gate at a time didn't. `live.py` is what keeps there from being a second process.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue
from typing import Any, ParamSpec, TypeVar

from langgraph.types import Command as LgCommand
from pydantic import BaseModel

from print_shop.core.checkpoint import open_checkpointer
from print_shop.core.contracts import Gate
from print_shop.core.mqtt import (
    Bridge,
    MqttBridge,
    MqttConfig,
    approval_buttons,
    command_topic,
    gate_topic,
    state_topic,
)
from print_shop.expert.client import LLMConfig
from print_shop.ledger import JobEnding, Ledger, LedgerError
from print_shop.orchestrator.frames import FrameKeeper
from print_shop.orchestrator.graph import build_graph, why_not_start
from print_shop.orchestrator.live import Step
from print_shop.orchestrator.outcome import OutcomeDesk, Watcher
from print_shop.printer.camera import Camera
from print_shop.printer.centauri import PrinterClient
from print_shop.setup import Proposer
from print_shop.setup.intake import Asker
from print_shop.setup.loop import PrintLoop
from print_shop.setup.review import Reviewer
from print_shop.setup.waiting import Waiting
from print_shop.sliced import SlicedJob
from print_shop.slicer.arrange import SlicePlate
from print_shop.slicer.orca import Slicer, SlicerProfiles, slice_model, slice_plate
from print_shop.slicer.store import Store

logger = logging.getLogger(__name__)

__all__ = [
    "GATE_WAIT_S",
    "Decision",
    "Command",
    "JobRejected",
    "JobResult",
    "parse_command",
    "state_payload",
    "Daemon",
]


#: How long the approval gate waits for Approve or Reject, from when it opens.
GATE_WAIT_S = 3600.0

_P = ParamSpec("_P")
_R = TypeVar("_R")


def duration(seconds: float) -> str:
    """A wait's length as a sentence says it: "60 min", "45 s"."""
    return f"{seconds / 60:g} min" if seconds >= 60 else f"{seconds:g} s"


# --------------------------------------------------------------------------
# Command parsing — pure
# --------------------------------------------------------------------------


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Command(BaseModel):
    """A parsed command from the MQTT command topic."""

    decision: Decision
    #: The job the press is for. A gate takes no other, and none that's empty.
    job_id: str = ""
    note: str = ""


class JobRejected(Exception):
    """Raised when a job is submitted while another job is already running."""


class JobResult(BaseModel):
    """The result of a completed job."""

    job_id: str
    approved: bool
    printed: bool
    error: str | None = None


def parse_command(raw: bytes) -> Command:
    """Parse a command payload from the MQTT command topic.

    A malformed payload must never read as approval. Raises ValueError on
    empty input, non-JSON, a missing decision, a null decision, or any value
    that is not exactly "approve" or "reject".
    """
    if not raw:
        raise ValueError("empty command payload")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("command payload must be a JSON object")

    decision = data.get("decision")
    if decision is None:
        raise ValueError("missing decision field")

    if decision not in (Decision.APPROVE, Decision.REJECT):
        raise ValueError(f"invalid decision: {decision!r}")

    note = data.get("note", "")
    if not isinstance(note, str):
        raise ValueError("note must be a string")

    job_id = data.get("job_id", "")
    if not isinstance(job_id, str):
        raise ValueError("job_id must be a string")

    return Command(decision=Decision(decision), job_id=job_id, note=note)


def state_payload(fields: dict[str, Any]) -> str:
    """Build a state payload JSON string with all four HA entity keys present.

    Always emits status, orientation_deg, print_time_s, and filament_cm3,
    using None for missing values so HA cards never go blank.
    """
    payload: dict[str, Any] = {
        "status": fields.get("status", "idle"),
        "orientation_deg": fields.get("orientation_deg"),
        "print_time_s": fields.get("print_time_s"),
        "filament_cm3": fields.get("filament_cm3"),
    }
    # Include any extra fields the caller provides (e.g. job_id).
    for key, value in fields.items():
        if key not in payload:
            payload[key] = value
    return json.dumps(payload)


# --------------------------------------------------------------------------
# Daemon
# --------------------------------------------------------------------------


def _rejection(inbox: Queue[Command]) -> Command | None:
    """A Reject among the presses that came while an Approve was checked."""
    while True:
        try:
            cmd = inbox.get_nowait()
        except Empty:
            return None
        if cmd.decision is Decision.REJECT:
            return cmd


class Daemon:
    """Long-running process that manages one print job at a time.

    On enter: opens the MQTT bridge, announces discovery configs, publishes
    availability online, subscribes to the command topic, and publishes an
    idle state.

    On exit: publishes availability offline and disconnects cleanly.

    One job at a time — a printer has one bed. A second submit while a job
    is active raises JobRejected.
    """

    def __init__(
        self,
        *,
        cfg: MqttConfig,
        profiles: SlicerProfiles | None,
        workdir: Path,
        printer: PrinterClient | None = None,
        llm: LLMConfig | None = None,
        slicer: Slicer = slice_model,
        bridge: Callable[[MqttConfig], Bridge] | None = None,
        ledger: Ledger | None = None,
        watch_printer: PrinterClient | None = None,
        watch_poll_s: float = 60.0,
        camera: Camera | None = None,
        proposer: Proposer | None = None,
        asker: Asker | None = None,
        reviewer: Reviewer | None = None,
        gate_wait_s: float = GATE_WAIT_S,
        loop: PrintLoop | None = None,
        show: Callable[[str], None] | None = None,
        store: Store | None = None,
        on_step: Callable[[str, str, Step], None] | None = None,
        waiting: Waiting | None = None,
        plate_slicer: SlicePlate = slice_plate,
    ) -> None:
        """`printer` is what the approval gate ultimately commands.

        None means this daemon cannot print: jobs still run, slice and park at
        the gate, and an approval fails at the hardware boundary instead of
        reaching for a machine nobody named. Pass `live_printer()` to make it
        real. The default is inert on purpose — see `src/printer/centauri.py`.

        `profiles` None serves the outcome gate only: a job submitted to it
        fails before slicing, saying the nozzle and filament were not given.

        `llm` is the orientation model (`LLMConfig.from_settings()`); None
        means geometric orientation, said so at the gate. `slicer` and
        `bridge` default to the real Orca and the real broker; tests pass a
        recorded slice and an in-memory broker. `ledger` records each print
        the agent starts; without one, an approval fails at the print node
        rather than start a print that goes unrecorded.

        `watch_printer` is a **read-only** client the outcome watcher polls
        every `watch_poll_s` for the end of a print the ledger holds as
        running; `CentauriClient(PrinterConfig.from_env())` is unarmed. With a
        ledger the outcome gate runs either way; without a watcher nothing
        sees a print end, and its row stays open, which is honest.

        `camera` is the printer's, read-only, and never its own stream
        (`camera.py`, mqucifer/print-shop#123): with a watcher, each
        print keeps a frame at each new layer the watcher reads and one at the
        end, in `workdir/<job>/frames/`, and the outcome gate shows them
        (mqucifer/print-shop#110). `CameraOff` says once for each print why
        there are none; None keeps none and says nothing.

        `asker` and `reviewer` are the person's: while either has the job,
        `wait_for_gate` doesn't count the time (mqucifer/print-shop#125).
        `gate_wait_s` is how long the approval gate waits for a press.

        `loop` is the print loop (mqucifer/print-shop#129), which decides the pose and the
        settings after the questions; its questions to the person are the person's time too.

        `show` is where a hand-sliced file's report is shown before the gate, and `store` the
        profiles its settings are sorted by (`submit_sliced`, mqucifer/print-shop#164).

        `on_step` is told each job's id, part and step as it moves, for the live lock to say to
        a second start (mqucifer/print-shop#207).

        `waiting` is the person's side of the wait for the model's questions
        (mqucifer/print-shop#208). Choosing among its closer choices is their time; the wait
        for the model is counted, as the call always was.

        `plate_slicer` is what slices a job of several parts (`submit_several`,
        mqucifer/print-shop#109).
        """
        self._cfg = cfg
        self._on_step = on_step
        self._profiles = profiles
        self._workdir = workdir

        # Checkpointer — kept open for the daemon's lifetime (CODE_REVIEW.md P0-3).
        checkpointer, conn = open_checkpointer(workdir / "jobs.sqlite")
        self._checkpointer = checkpointer
        #: Closed on exit; the graph must not outlive it (CODE_REVIEW.md P0-3).
        self._conn: sqlite3.Connection | None = conn

        # Whether the intake or the review is with the person, and when the job last came back
        # from them (mqucifer/print-shop#125): their time isn't counted against a wait.
        self._with_person = threading.Event()
        self._back_at = time.monotonic()
        self._gate_wait_s = gate_wait_s
        #: Jobs a wait ran out on, with the sentence that ended them: no gate opens for one.
        self._abandoned: dict[str, str] = {}
        #: Why each Approve that was held started nothing, in order, for the terminal to say.
        self._held: dict[str, list[str]] = {}
        #: Each job's part, by name, for the ledger's row if it ends unprinted.
        self._parts: dict[str, str] = {}

        # Compiled graph.
        self._graph = build_graph(
            profiles,
            outdir_root=workdir,
            checkpointer=self._checkpointer,
            llm=llm,
            printer=printer,
            slicer=slicer,
            ledger=ledger,
            proposer=proposer,
            asker=self._persons(asker) if asker is not None else None,
            reviewer=self._persons(reviewer) if reviewer is not None else None,
            loop=(dataclasses.replace(loop, person=self._persons(loop.person))
                  if loop is not None else None),
            show=show,
            store=store,
            waiting=(dataclasses.replace(waiting, closer=self._persons(waiting.closer))
                     if waiting is not None and waiting.closer is not None else waiting),
            plate_slicer=plate_slicer,
        )

        # MQTT bridge.
        self._make_bridge: Callable[[MqttConfig], Bridge] = bridge or (
            lambda cfg: MqttBridge(cfg, owns_availability=True)
        )
        self._bridge: Bridge | None = None

        # Job tracking.
        self._job_lock = threading.Lock()
        self._active_job_id: str | None = None
        self._worker: threading.Thread | None = None
        self._worker_alive = False
        self._at_gate = threading.Event()  # wakes `wait_for_gate` as one opens
        #: Each gate a job has opened, in order, answered or not: what `wait_for_gate` returns.
        self._opened: dict[str, list[dict[str, Any]]] = {}

        # Gate-scoped inbox: the MQTT callback pushes into it, the gate that
        # opened it consumes it. None when no gate is open — a command that
        # arrives with no inbox has nothing to be an answer to, and is dropped.
        self._gate_inbox: Queue[Command] | None = None
        #: What the open gate shows, for a caller to print beside the dashboard.
        self._pending_gate: dict[str, Any] | None = None

        # Results storage.
        self._results: dict[str, JobResult] = {}

        # After the print: the outcome gate and what watches for the end.
        self._ledger = ledger
        self._watch_printer = watch_printer
        self._watch_poll_s = watch_poll_s
        self._camera = camera
        self._desk: OutcomeDesk | None = None
        self._watcher: Watcher | None = None

    # -- context manager ----------------------------------------------------

    def __enter__(self) -> Daemon:
        # The daemon owns the lifecycle, so it alone sets the Last Will and
        # publishes availability.
        self._bridge = self._make_bridge(self._cfg)
        self._bridge.__enter__()
        self._bridge.announce()
        self._bridge.publish_availability(online=True)
        # Clear any gate a dead daemon left behind: the gate is retained so HA
        # sees a pending approval across its own restarts, but if we die with a
        # gate pending the broker keeps serving pending: true with a dead job's
        # id — HA shows "Awaiting Approval" forever with an Approve button
        # attached to a job that no longer exists. Same failure the Last Will
        # covers for availability. A daemon that has just started has, by
        # definition, nothing awaiting approval.
        self._bridge_publish(gate_topic(self._cfg), {"pending": False})
        self._bridge.subscribe(command_topic(self._cfg), self._on_command)
        self._publish_state({"status": "idle"})
        if self._ledger is not None:
            # Unlike the approval gate, the outcome gate is not cleared on
            # start: its prints live in the ledger, not in this process, so a
            # restart shows the oldest unanswered one again.
            self._desk = OutcomeDesk(
                ledger=self._ledger, cfg=self._cfg, publish=self._bridge_publish,
                publish_image=self._bridge_publish_image,
            )
            for topic, handler in self._desk.topics.items():
                self._bridge.subscribe(topic, handler)
            self._desk.refresh()
            if self._watch_printer is not None:
                self._watcher = Watcher(
                    ledger=self._ledger,
                    printer=self._watch_printer,
                    on_ended=self._desk.refresh,
                    poll_s=self._watch_poll_s,
                    confirming=lambda: self._active_job_id,
                    frames=(
                        FrameKeeper(camera=self._camera, ledger=self._ledger, jobs=self._workdir)
                        if self._camera is not None
                        else None
                    ),
                )
                self._watcher.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        if self._bridge is not None:
            # Same reasoning as on enter: never leave a retained pending gate
            # behind after a graceful shutdown, nor buttons that name its job.
            self._bridge_publish(gate_topic(self._cfg), {"pending": False})
            self._name_buttons()
            self._bridge.publish_availability(online=False)
            self._bridge.__exit__(*exc)
            self._bridge = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- public API ---------------------------------------------------------

    def submit(
        self, stl_path: Path | str, *, as_laid: bool = False, context: Path | str | None = None,
    ) -> str:
        """Submit a new job. Returns the job_id.

        `as_laid` keeps the file's own pose, laid for printing by the person: no
        orientation is chosen for it (mqucifer/print-shop#128). `context` names the part's
        context, a sidecar or a design run's folder; None looks beside the STL and above it
        (mqucifer/print-shop#130).

        Raises JobRejected if a job is already active.
        """
        return self._submit(Path(stl_path).name, {
            "entry": "stl_upload",
            "stl_path": str(stl_path),
            "as_laid": as_laid,
            "context_path": str(context) if context else None,
        })

    def submit_several(self, stl_paths: list[Path], *, as_laid: bool = False) -> str:
        """Submit several parts as one job, arranged on one plate and printed by object where
        the head has the room (mqucifer/print-shop#109). Returns the job_id.

        One review, one gate and one print, as any job's. `as_laid` keeps every part's own pose.
        Each part's context is read beside it.

        Raises:
            ValueError: With fewer than two parts: one is `submit`'s.
            JobRejected: If a job is already active.
        """
        if len(stl_paths) < 2:
            raise ValueError("several parts are two or more: one part is submitted by itself")
        return self._submit(", ".join(p.name for p in stl_paths), {
            "entry": "stl_upload",
            "stl_paths": tuple(str(p) for p in stl_paths),
            "as_laid": as_laid,
        })

    def _submit(self, part: str, given: dict[str, Any]) -> str:
        """Start a job's worker with `given` as its state, if no job is active."""
        with self._job_lock:
            if self._active_job_id is not None:
                raise JobRejected(
                    f"job {self._active_job_id} is already running"
                )
            job_id = str(uuid.uuid4())
            self._active_job_id = job_id
            self._parts[job_id] = part
            self._back_at = time.monotonic()

        self._step(job_id, Step.PREPARING)
        self._publish_state({"status": "analyzing", "job_id": job_id})

        self._worker = threading.Thread(
            target=self._run_job,
            args=(job_id, given),
            daemon=True,
        )
        self._worker_alive = True
        self._at_gate.clear()
        self._worker.start()
        return job_id

    def submit_sliced(self, job: SlicedJob) -> str:
        """Submit a hand-sliced file, to be sent as it is (mqucifer/print-shop#164). Returns
        the job_id.

        It goes to the same approval gate, print node, ledger and watcher as any job. Nothing
        is asked or proposed for it, no lesson is applied, and nothing is sliced.

        Raises JobRejected if a job is already active.
        """
        return self._submit(job.name, {"entry": "sliced_file", "sliced": job})

    def wait_for_gate(
        self, job_id: str, timeout_s: float, shown: int = 0
    ) -> dict[str, Any] | None:
        """Block until the job has opened a gate past the `shown` it's known to have, and return
        it, or None if the job ends first (or a wait runs out).

        A job can have two (the mesh gate's Approve goes on to the approval gate). Each is kept,
        so one answered before the caller looked is still returned, to be shown and logged
        (mqucifer/print-shop#200).

        A job that ended without a gate, refused or stopped before it, returns
        at once, with `pending_gate()` None and its error in `wait_for_finish`,
        rather than leaving the caller waiting out the timeout.

        `timeout_s` counts from when the job last came back from the person:
        its submit, its last intake answer, the review's `ok` or `take`, or
        its last gate's answer, for a job with another gate after it.
        While the intake, the review or an open gate is with them it doesn't
        count, however long they take (mqucifer/print-shop#125).

        Raises:
            TimeoutError: If `timeout_s` runs out first. The job is ended
                (`abandon`), and the error is the sentence that says so.
        """
        while True:
            with self._job_lock:
                opened = self._opened.get(job_id, [])
                if len(opened) > shown:
                    return dict(opened[shown])
                if self._active_job_id != job_id:
                    return None
                self._at_gate.clear()
            if self._at_gate.wait(timeout=0.2):
                continue
            # An open gate has a wait of its own (`gate_wait_s`).
            with_person = self._with_person.is_set() or self._pending_gate is not None
            if not with_person and time.monotonic() - self._back_at >= timeout_s:
                raise TimeoutError(
                    self.abandon(job_id, "waiting for the approval gate", timeout_s)
                )

    def abandon(self, job_id: str, what: str, waited_s: float) -> str:
        """End a job a wait ran out on: the sentence that says so, and the ledger's row.

        Nothing is sent to the printer after this: no gate opens for the job, and the ledger
        refuses its start. One sentence says what ran out, after how long, and that nothing
        printed, and the ledger keeps it as timed out (mqucifer/print-shop#125). A job already
        sent to the printer is a print, and its row stays open, said so instead.
        """
        sentence = f"Timed out {what} after {duration(waited_s)}, so nothing printed."
        if self._ledger is not None:
            try:
                self._ledger.record_unprinted(
                    job_id, self._parts.get(job_id, "?"), JobEnding.TIMED_OUT, sentence
                )
            except LedgerError:
                sentence = (
                    f"Timed out {what} after {duration(waited_s)}, after the printer was told to "
                    "start, so its row in the ledger stays open."
                )
        with self._job_lock:
            self._abandoned[job_id] = sentence
        return sentence

    def wait_for_finish(
        self, job_id: str, timeout_s: float, tell: Callable[[str], None] | None = None
    ) -> JobResult:
        """Block until the job finishes (or times out).

        `tell` is told each Approve that was held, and why, as it happens
        (mqucifer/print-shop#218).

        Returns the JobResult. Raises TimeoutError on timeout; the caller ends the job with
        `abandon`.
        """
        deadline = time.monotonic() + timeout_s
        told = 0
        while time.monotonic() < deadline:
            with self._job_lock:
                held = list(self._held.get(job_id, ()))
            if tell is not None:
                for said in held[told:]:
                    tell(said)
            told = len(held)
            with self._job_lock:
                if self._active_job_id != job_id and told == len(self._held.get(job_id, ())):
                    # Job finished (or failed) — active_job_id was cleared.
                    # Retrieve the result.
                    result = self._results.pop(job_id, None)
                    if result is not None:
                        return result
            time.sleep(0.1)
        raise TimeoutError(f"timed out waiting for job {job_id} to finish")

    def pending_gate(self) -> dict[str, Any] | None:
        """The payload of the gate a job is parked at, or None. Read-only."""
        gate = self._pending_gate
        return dict(gate) if gate is not None else None

    def is_alive(self) -> bool:
        """Report whether the daemon process is still operational.

        A node raising (e.g. the unimplemented print node) finishes the worker
        thread for that job but does not kill the daemon. The daemon is alive
        as long as its MQTT bridge is connected.
        """
        return self._bridge is not None

    # -- internals ----------------------------------------------------------

    def _persons(self, step: Callable[_P, _R]) -> Callable[_P, _R]:
        """`step` (the intake or the review), marked as with the person while it runs."""

        def with_person(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            self._with_person.set()
            try:
                return step(*args, **kwargs)
            finally:
                self._back_at = time.monotonic()
                self._with_person.clear()

        return with_person

    def _open_gate(self) -> Queue[Command]:
        """Install a fresh inbox and return it. Called when a gate parks.

        A new ``Queue`` is created every time: draining and reusing one is not
        equivalent, because draining is a step someone can forget and a fresh
        object cannot be.

        **A decision is scoped to a job by identity, and to that job's gate by
        time.** `_on_command` delivers only a press naming the active job
        (mqucifer/print-shop#207), so another process's press can't land here.
        A press doesn't say which of its job's gates it's for: those are
        strictly sequential, and the fresh inbox is what keeps one's press
        from the next.
        """
        with self._job_lock:
            self._gate_inbox = Queue()
            return self._gate_inbox

    def _close_gate(self) -> None:
        """Drop the inbox. Idempotent — may be called on an already-closed gate."""
        with self._job_lock:
            self._gate_inbox = None

    def _left_gate(self) -> None:
        """The gate has its answer, or its wait ran out. Its time was the person's, so the wait
        for the next gate counts from now (mqucifer/print-shop#125)."""
        self._back_at = time.monotonic()
        self._pending_gate = None

    def _on_command(self, topic: str, payload: bytes, retain: bool) -> None:
        """MQTT callback: parse the command and push it to the queue.

        ``retain`` is the MQTT retain flag for the received message. Retained
        messages are replayed by the broker to late subscribers; a stale
        approval must never apply to a job a person has not seen, so retained
        commands are ignored. Commands arriving while no gate is pending are
        also ignored rather than buffered for the next job. So is a command
        that names another job, or none (mqucifer/print-shop#207).

        ``retain`` had a ``= False`` default here, and `MqttBridge` called the
        handler with two arguments, so this guard never fired. A reconnect
        while a job was parked at a gate would have replayed a retained
        approval straight through it — AGENTS.md rule 5. The flag is now
        required, so the same mistake cannot typecheck.
        """
        if retain:
            return
        try:
            cmd = parse_command(payload)
        except ValueError:
            # Malformed command — ignore. A malformed payload must never
            # read as approval.
            return
        with self._job_lock:
            if self._active_job_id is None:
                # No job is active, so no gate is pending. Ignore the command
                # instead of buffering it for a future job.
                return
            inbox = self._gate_inbox
            if inbox is None:
                # A job is active but no gate is open yet (e.g. still slicing).
                # This press is about nothing — the numbers a human would
                # approve do not exist yet. Drop it rather than buffering it
                # for the next gate.
                return
            if cmd.job_id != self._active_job_id:
                logger.warning(
                    "ignored %s for %s: this gate is job %s's",
                    cmd.decision,
                    f"job {cmd.job_id[:80]!r}" if cmd.job_id else "no named job",
                    self._active_job_id,
                )
                return
            # Check and put under the same lock: the gate can close between a
            # read of the inbox and a put, which would re-open the race this
            # scoping exists to close.
            inbox.put(cmd)

    def _run_job(self, job_id: str, given: dict[str, Any]) -> None:
        """Run the graph for one job. Runs on a background thread."""
        thread_config = {"configurable": {"thread_id": job_id}}
        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "trace_id": str(uuid.uuid4()),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **given,
        }

        try:
            # First invoke: run until the first interrupt (or completion).
            result = self._graph.invoke(initial_state, config=thread_config)

            # Check if we're parked at a gate.
            if "__interrupt__" in result:
                self._handle_interrupt(job_id, result, thread_config)
            else:
                # Graph completed without hitting a gate.
                self._finish_job(job_id, approved=False, printed=False)

        except Exception as exc:
            self._finish_job(
                job_id,
                approved=False,
                printed=False,
                error=str(exc),
            )

    def _handle_interrupt(
        self, job_id: str, result: dict[str, Any], thread_config: dict[str, Any]
    ) -> None:
        """Handle a parked graph: publish gate state, wait for command, resume."""
        # LangGraph 1.x returns `__interrupt__` as a tuple of Interrupt objects;
        # the payload lives on `.value`.
        interrupt = result["__interrupt__"][0]
        gate_payload: dict[str, Any] = dict(interrupt.value)
        with self._job_lock:
            ended = self._abandoned.get(job_id)
        if ended is not None:
            # A wait ran out on it before the gate: the person was told nothing would print.
            self._finish_job(job_id, approved=False, printed=False, error=ended)
            return
        # The time left to answer, shown with the gate (mqucifer/print-shop#125).
        answer_by = datetime.now(UTC) + timedelta(seconds=self._gate_wait_s)
        self._pending_gate = {
            **gate_payload, "job_id": job_id, "answer_within_s": self._gate_wait_s,
            "answer_by": answer_by.isoformat(timespec="seconds"),
        }

        # Open a fresh inbox before publishing the gate, so a press that lands
        # the moment the dashboard updates has somewhere to go. It is scoped to
        # this gate and closed once we have our answer (or time out).
        inbox = self._open_gate()
        self._step(job_id, Step.AT_GATE)
        # The buttons name the job before the card that shows them does.
        self._name_buttons(job_id)

        # Publish the gate with pending: true.
        gate_msg = {**gate_payload, "pending": True, "job_id": job_id}
        self._bridge_publish(gate_topic(self._cfg), gate_msg)

        # Publish state with awaiting_approval.
        self._publish_state(self._waiting_state(job_id, gate_payload))

        # Signal that we're at a gate, now it's published.
        with self._job_lock:
            self._opened.setdefault(job_id, []).append(dict(self._pending_gate))
            self._at_gate.set()

        # Wait for a command from this gate's inbox. An Approve that's held waits for another,
        # within the same hour (mqucifer/print-shop#218).
        deadline = time.monotonic() + self._gate_wait_s
        before_print = gate_payload.get("gate") == Gate.BEFORE_PRINT
        while True:
            try:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise Empty
                cmd = inbox.get(timeout=left)
            except Empty:
                self._close_gate()
                self._left_gate()
                self._bridge_publish(gate_topic(self._cfg), {"pending": False})
                self._name_buttons()
                sentence = self.abandon(
                    job_id, f"waiting for Approve or Reject at {Gate.named(gate_payload)}",
                    self._gate_wait_s,
                )
                self._finish_job(job_id, approved=False, printed=False, error=sentence)
                return
            if cmd.decision is not Decision.APPROVE or not before_print:
                break
            why = self._not_yet(thread_config)
            # This press is spent, and so is any that came while it was checked: none is kept
            # to start the job later. A Reject among them still stands, whatever the check said.
            checked, inbox = inbox, self._open_gate()
            if rejected := _rejection(checked):
                cmd = rejected
                break
            if not why:
                break
            self._hold(job_id, gate_payload, why)

        approved = cmd.decision is Decision.APPROVE
        note = cmd.note

        # We have our answer; close this gate's inbox so a stale press cannot
        # leak into the next gate.
        self._close_gate()
        self._left_gate()

        # Clear the gate.
        self._bridge_publish(gate_topic(self._cfg), {"pending": False})
        self._name_buttons()
        if approved:
            before_print = gate_payload.get("gate") == Gate.BEFORE_PRINT
            self._step(job_id, Step.SENDING if before_print else Step.PREPARING)

        # Resume the graph.
        try:
            resume_cmd: LgCommand[Any] = LgCommand(resume={"approved": approved, "note": note})
            result = self._graph.invoke(resume_cmd, config=thread_config)

            if "__interrupt__" in result:
                # Another gate — handle recursively.
                self._handle_interrupt(job_id, result, thread_config)
            else:
                # Graph completed without another gate. If approved, the print
                # node ran (and may have raised, caught below). If rejected,
                # the graph routed to END without printing.
                self._finish_job(job_id, approved=approved, printed=approved)

        except Exception as exc:
            self._finish_job(
                job_id,
                approved=approved,
                printed=False,
                error=str(exc),
            )

    def _not_yet(self, thread_config: dict[str, Any]) -> str:
        """Why this Approve mustn't start the job, or "" (mqucifer/print-shop#218).

        Only the stock and the ledger are read, so that the press is judged on them as they
        are when it's pressed, and a slow printer can't hold up a Reject or stretch the hour.
        A check that can't be made holds.
        """
        try:
            state = self._graph.get_state(thread_config).values
            return why_not_start(state, self._ledger)
        except Exception as exc:  # noqa: BLE001 - never started on a check that wasn't made
            logger.exception("the spool couldn't be checked before the start")
            return f"what's loaded couldn't be checked ({exc}): approve again"

    def _hold(self, job_id: str, gate_payload: dict[str, Any], why: str) -> None:
        """An Approve held: the gate stays open, saying why, on the card and in the terminal."""
        said = f"Not started: {why}"
        # What's held says what to load, so the gate's own line, from when it opened, gives way.
        gate_payload = {**gate_payload, "spools": ""}
        if self._pending_gate is not None:
            self._pending_gate = {**self._pending_gate, "held": said}
        self._bridge_publish(
            gate_topic(self._cfg),
            {**gate_payload, "pending": True, "job_id": job_id, "held": said},
        )
        self._publish_state(self._waiting_state(job_id, gate_payload, said))
        with self._job_lock:
            self._held.setdefault(job_id, []).append(said)

    def _waiting_state(
        self, job_id: str, gate_payload: dict[str, Any], held: str = ""
    ) -> dict[str, Any]:
        return {
            "status": "awaiting_approval",
            "job_id": job_id,
            "orientation_deg": gate_payload.get("orientation_deg"),
            "print_time_s": gate_payload.get("print_time_s"),
            "filament_cm3": gate_payload.get("filament_cm3"),
            "pause": gate_payload.get("pause") or "",
            "pause_for": gate_payload.get("pause_for") or "",
            "spools": gate_payload.get("spools") or "",
            "held": held,
            # How a plate of several prints, and why (mqucifer/print-shop#109).
            **({"plate": gate_payload["plate"]} if gate_payload.get("plate") else {}),
        }

    def _finish_job(
        self,
        job_id: str,
        *,
        approved: bool,
        printed: bool,
        error: str | None = None,
    ) -> None:
        """Finalize a job: publish state, clear the active job."""
        self._step(job_id, Step.ENDING)
        status = "failed" if error else ("done" if printed else "rejected")
        self._publish_state({"status": status, "job_id": job_id})

        # Store the result for wait_for_finish to retrieve.
        self._results[job_id] = JobResult(
            job_id=job_id,
            approved=approved,
            printed=printed,
            error=error,
        )

        # Clear the active job.
        with self._job_lock:
            if self._active_job_id == job_id:
                self._active_job_id = None
        self._worker_alive = False
        self._at_gate.clear()
        # Idempotent: a gate that already closed (or never opened) is a no-op.
        self._close_gate()
        if approved and self._watcher is not None:
            # A print started, or a start raised and may have taken: look now.
            self._watcher.poke()

    def _step(self, job_id: str, step: Step) -> None:
        if self._on_step is not None:
            self._on_step(job_id, self._parts.get(job_id, "?"), step)

    def _name_buttons(self, job_id: str = "") -> None:
        """Approve and Reject republished, naming `job_id`, or no job (mqucifer/print-shop#207)."""
        for topic, config in approval_buttons(self._cfg, job_id):
            self._bridge_publish(topic, config)

    def _publish_state(self, fields: dict[str, Any]) -> None:
        """Publish a state update to the state topic.

        Retained so a subscriber that connects after the publish (e.g. a Home
        Assistant restart) immediately sees the current state instead of a
        blank card until the next transition.
        """
        if self._bridge is not None:
            self._bridge.publish(
                state_topic(self._cfg), state_payload(fields), retain=True
            )

    def _bridge_publish_image(self, topic: str, jpeg: bytes) -> None:
        """A camera frame, retained, so a dashboard opened later shows it. Empty clears it."""
        if self._bridge is not None:
            self._bridge.publish(topic, jpeg, retain=True)

    def _bridge_publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish to a topic via the bridge.

        Retained so late subscribers see the latest gate state.
        """
        if self._bridge is not None:
            self._bridge.publish(topic, payload, retain=True)
