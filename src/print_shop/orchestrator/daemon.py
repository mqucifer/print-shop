"""Long-running daemon that makes the approval gate a real button in Home Assistant.

This is the process that holds the MQTT subscription, publishes state, and
resumes the parked graph when a person presses Approve or Reject.

CONTRACTS.md §1: the pipeline is one process. This is that process gaining a
lifecycle, not a new service.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from langgraph.types import Command as LgCommand
from pydantic import BaseModel

from print_shop.core.checkpoint import open_checkpointer
from print_shop.core.mqtt import (
    Bridge,
    MqttBridge,
    MqttConfig,
    command_topic,
    gate_topic,
    state_topic,
)
from print_shop.expert.client import LLMConfig
from print_shop.ledger import Ledger
from print_shop.orchestrator.graph import build_graph
from print_shop.printer.centauri import PrinterClient
from print_shop.slicer.orca import Slicer, SlicerProfiles, slice_model

__all__ = [
    "Decision",
    "Command",
    "JobRejected",
    "JobResult",
    "parse_command",
    "state_payload",
    "Daemon",
]


# --------------------------------------------------------------------------
# Command parsing — pure
# --------------------------------------------------------------------------


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Command(BaseModel):
    """A parsed command from the MQTT command topic."""

    decision: Decision
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

    return Command(decision=Decision(decision), note=note)


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
        profiles: SlicerProfiles,
        workdir: Path,
        printer: PrinterClient | None = None,
        llm: LLMConfig | None = None,
        slicer: Slicer = slice_model,
        bridge: Callable[[MqttConfig], Bridge] | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        """`printer` is what the approval gate ultimately commands.

        None means this daemon cannot print: jobs still run, slice and park at
        the gate, and an approval fails at the hardware boundary instead of
        reaching for a machine nobody named. Pass `live_printer()` to make it
        real. The default is inert on purpose — see `src/printer/centauri.py`.

        `llm` is the orientation model (`LLMConfig.from_settings()`); None
        means geometric orientation, said so at the gate. `slicer` and
        `bridge` default to the real Orca and the real broker; tests pass a
        recorded slice and an in-memory broker. `ledger` records each print
        the agent starts; without one, an approval fails at the print node
        rather than start a print that goes unrecorded.
        """
        self._cfg = cfg
        self._profiles = profiles
        self._workdir = workdir

        # Checkpointer — kept open for the daemon's lifetime (CODE_REVIEW.md P0-3).
        checkpointer, conn = open_checkpointer(workdir / "jobs.sqlite")
        self._checkpointer = checkpointer
        #: Closed on exit; the graph must not outlive it (CODE_REVIEW.md P0-3).
        self._conn: sqlite3.Connection | None = conn

        # Compiled graph.
        self._graph = build_graph(
            profiles,
            outdir_root=workdir,
            checkpointer=self._checkpointer,
            llm=llm,
            printer=printer,
            slicer=slicer,
            ledger=ledger,
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
        self._at_gate = threading.Event()

        # Gate-scoped inbox: the MQTT callback pushes into it, the gate that
        # opened it consumes it. None when no gate is open — a command that
        # arrives with no inbox has nothing to be an answer to, and is dropped.
        self._gate_inbox: Queue[Command] | None = None

        # Results storage.
        self._results: dict[str, JobResult] = {}

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
        return self

    def __exit__(self, *exc: object) -> None:
        if self._bridge is not None:
            # Same reasoning as on enter: never leave a retained pending gate
            # behind after a graceful shutdown.
            self._bridge_publish(gate_topic(self._cfg), {"pending": False})
            self._bridge.publish_availability(online=False)
            self._bridge.__exit__(*exc)
            self._bridge = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- public API ---------------------------------------------------------

    def submit(self, stl_path: Path | str) -> str:
        """Submit a new job. Returns the job_id.

        Raises JobRejected if a job is already active.
        """
        with self._job_lock:
            if self._active_job_id is not None:
                raise JobRejected(
                    f"job {self._active_job_id} is already running"
                )
            job_id = str(uuid.uuid4())
            self._active_job_id = job_id

        self._publish_state({"status": "analyzing", "job_id": job_id})

        self._worker = threading.Thread(
            target=self._run_job,
            args=(job_id, str(stl_path)),
            daemon=True,
        )
        self._worker_alive = True
        self._at_gate.clear()
        self._worker.start()
        return job_id

    def wait_for_gate(self, job_id: str, timeout_s: float) -> None:
        """Block until the job reaches a gate (or times out).

        Raises TimeoutError if the gate is not reached within timeout_s.
        """
        if not self._at_gate.wait(timeout=timeout_s):
            raise TimeoutError(f"timed out waiting for gate on job {job_id}")

    def wait_for_finish(self, job_id: str, timeout_s: float) -> JobResult:
        """Block until the job finishes (or times out).

        Returns the JobResult. Raises TimeoutError on timeout.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._job_lock:
                if self._active_job_id != job_id:
                    # Job finished (or failed) — active_job_id was cleared.
                    # Retrieve the result.
                    result = self._results.pop(job_id, None)
                    if result is not None:
                        return result
            time.sleep(0.1)
        raise TimeoutError(f"timed out waiting for job {job_id} to finish")

    def is_alive(self) -> bool:
        """Report whether the daemon process is still operational.

        A node raising (e.g. the unimplemented print node) finishes the worker
        thread for that job but does not kill the daemon. The daemon is alive
        as long as its MQTT bridge is connected.
        """
        return self._bridge is not None

    # -- internals ----------------------------------------------------------

    def _open_gate(self) -> Queue[Command]:
        """Install a fresh inbox and return it. Called when a gate parks.

        A new ``Queue`` is created every time: draining and reusing one is not
        equivalent, because draining is a step someone can forget and a fresh
        object cannot be.

        **This scopes a decision to a gate by time, not by identity**, and that
        is only sound because at most one gate is ever open: `submit` refuses a
        second job while one is active, and a single job's gates are strictly
        sequential. `Command` carries no job_id, so nothing here would notice
        a decision arriving for a different gate.

        If concurrent jobs ever become a thing, this is the assumption that
        breaks, and it breaks silently — a press delivered to the wrong gate,
        which is AGENTS.md rule 5 again. Put the job_id on `Command` and match
        it before that happens, not after.
        """
        with self._job_lock:
            self._gate_inbox = Queue()
            return self._gate_inbox

    def _close_gate(self) -> None:
        """Drop the inbox. Idempotent — may be called on an already-closed gate."""
        with self._job_lock:
            self._gate_inbox = None

    def _on_command(self, topic: str, payload: bytes, retain: bool) -> None:
        """MQTT callback: parse the command and push it to the queue.

        ``retain`` is the MQTT retain flag for the received message. Retained
        messages are replayed by the broker to late subscribers; a stale
        approval must never apply to a job a person has not seen, so retained
        commands are ignored. Commands arriving while no gate is pending are
        also ignored rather than buffered for the next job.

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
            # Check and put under the same lock: the gate can close between a
            # read of the inbox and a put, which would re-open the race this
            # scoping exists to close.
            inbox.put(cmd)

    def _run_job(self, job_id: str, stl_path: str) -> None:
        """Run the graph for one job. Runs on a background thread."""
        thread_config = {"configurable": {"thread_id": job_id}}
        initial_state: dict[str, Any] = {
            "job_id": job_id,
            "trace_id": str(uuid.uuid4()),
            "entry": "stl_upload",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "stl_path": stl_path,
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

        # Open a fresh inbox before publishing the gate, so a press that lands
        # the moment the dashboard updates has somewhere to go. It is scoped to
        # this gate and closed once we have our answer (or time out).
        inbox = self._open_gate()

        # Publish the gate with pending: true.
        gate_msg = {**gate_payload, "pending": True, "job_id": job_id}
        self._bridge_publish(gate_topic(self._cfg), gate_msg)

        # Publish state with awaiting_approval.
        self._publish_state(
            {
                "status": "awaiting_approval",
                "job_id": job_id,
                "orientation_deg": gate_payload.get("orientation_deg"),
                "print_time_s": gate_payload.get("print_time_s"),
                "filament_cm3": gate_payload.get("filament_cm3"),
            }
        )

        # Signal that we're at a gate.
        self._at_gate.set()

        # Wait for a command from this gate's inbox.
        try:
            cmd = inbox.get(timeout=3600)
        except Empty:
            self._close_gate()
            self._finish_job(
                job_id,
                approved=False,
                printed=False,
                error="timed out waiting for approval",
            )
            return

        approved = cmd.decision is Decision.APPROVE
        note = cmd.note

        # We have our answer; close this gate's inbox so a stale press cannot
        # leak into the next gate.
        self._close_gate()

        # Clear the gate.
        self._bridge_publish(gate_topic(self._cfg), {"pending": False})

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

    def _finish_job(
        self,
        job_id: str,
        *,
        approved: bool,
        printed: bool,
        error: str | None = None,
    ) -> None:
        """Finalize a job: publish state, clear the active job."""
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

    def _bridge_publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Publish to a topic via the bridge.

        Retained so late subscribers see the latest gate state.
        """
        if self._bridge is not None:
            self._bridge.publish(topic, payload, retain=True)
