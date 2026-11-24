"""Long-running daemon that makes the approval gate a real button in Home Assistant.

This is the process that holds the MQTT subscription, publishes state, and
resumes the parked graph when a person presses Approve or Reject.

CONTRACTS.md §1: the pipeline is one process. This is that process gaining a
lifecycle, not a new service.

**Waiting is counted only while the job isn't with the person** (mqucifer/print-shop#125).
The intake's questions, the settings review and the gates take as long as the person takes,
with no deadline (mqucifer/print-shop#209): a job left ready waits for days. A caller's wait
counts only the machine's steps, from when the job last came back from the person: the slice
to the gate, and the upload and the start after a Start. A wait that runs out ends the job in
one sentence, with a ledger row whose outcome is that it timed out, and nothing is sent to the
printer after it: a job it ran out on never opens a gate, even where the step it ran out on
ends as it's ended.

**A waiting job outlives its process** (mqucifer/print-shop#209, `kept.py`). Each job is in the
jobs folder's list of waiting jobs from its submit to its end, and `resume` takes one up again at
the step it waits at, with what was answered and corrected, and its slice. A job a restart caught
after its Start, and before anything reached the printer, is taken up ready again: an approval
is never kept for later, and nothing is armed by a restart.

**Start is the approval, given when it's time to print** (mqucifer/print-shop#209). An Approve
at the gate before printing, or `start` at the terminal, checks again the G-code, the part, the
profiles, the loaded spool, the fitted nozzle and the printer (`start.why_not_now`), and that the
bed was said to be clear. The card's dialog asks that, and it can't be checked from here until
mqucifer/print-shop#214; the terminal asks it itself. Each press names its gate's word, new at
each gate and after each press it spends, so a press sent again starts nothing.

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
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue
from typing import Any, ParamSpec, TypeVar

from langgraph.types import Command as LgCommand
from pydantic import BaseModel

from print_shop.arming import ALLOW_HARDWARE_ENV
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
from print_shop.orchestrator.kept import WaitingJob, WaitingJobs, Waits
from print_shop.orchestrator.live import Step
from print_shop.orchestrator.outcome import OutcomeDesk, Watcher
from print_shop.orchestrator.start import why_not_now
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
    "BED_AT_TERMINAL",
    "BED_ON_CARD",
    "Decision",
    "Command",
    "JobRejected",
    "JobResult",
    "parse_command",
    "state_payload",
    "Daemon",
]


#: How the bed was said to be clear, as the job's record keeps it: the card's dialog, which
#: can't be checked from here until mqucifer/print-shop#214, or the terminal's own question.
BED_ON_CARD = "on the card's dialog"
BED_AT_TERMINAL = "at the terminal"

#: What a Start in a run that isn't armed is told: held, so the ready job isn't lost.
NOT_ARMED = (f"this run isn't armed ({ALLOW_HARDWARE_ENV} is not 1), so nothing can print. Stop "
             f"it, and take the job up with `{ALLOW_HARDWARE_ENV}=1 uv run python "
             "scripts/print_job.py --resume <job>`")

#: The most a Start from the terminal waits to be checked: a printer's snapshot, and the stock.
_CHECKED_WITHIN_S = 60.0

#: What a Start with no word on the bed is told.
BED_NOT_SAID = "the bed isn't confirmed clear. Clear it, then start again and answer y"

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
    #: The gate's word for this press (`approval_buttons`): a gate takes only its current one.
    press: str = ""
    note: str = ""
    #: How the person said the bed is clear, for a Start: never read from a payload.
    bed: str = ""
    #: Where it was given, as it's said: "on the card" or "here", at the terminal.
    by: str = ""
    #: A terminal command's own id, to tell it was weighed. Never read from a payload.
    id: str = ""


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

    press = data.get("press", "")
    if not isinstance(press, str):
        raise ValueError("press must be a string")

    return Command(decision=Decision(decision), job_id=job_id, press=press, note=note)


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
        loop: PrintLoop | None = None,
        show: Callable[[str], None] | None = None,
        store: Store | None = None,
        on_step: Callable[[str, str, Step], None] | None = None,
        waiting: Waiting | None = None,
        store_root: Path | None = None,
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
        `wait_for_gate` doesn't count the time (mqucifer/print-shop#125). A gate waits for its
        press with no deadline (mqucifer/print-shop#209).

        `loop` is the print loop (mqucifer/print-shop#129), which decides the pose and the
        settings after the questions; its questions to the person are the person's time too.

        `show` is where a hand-sliced file's report is shown before the gate, and `store` the
        profiles its settings are sorted by (`submit_sliced`, mqucifer/print-shop#164).

        `on_step` is told each job's id, part and step as it moves, for the live lock to say to
        a second start (mqucifer/print-shop#207).

        `waiting` is the person's side of the wait for the model's questions
        (mqucifer/print-shop#208). Choosing among its closer choices is their time; the wait
        for the model is counted, as the call always was.

        `store_root` is the profile store the slices' profiles come from, read again by hash at
        Start (mqucifer/print-shop#209); None leaves them unread there.

        `plate_slicer` is what slices a job of several parts (`submit_several`,
        mqucifer/print-shop#109).
        """
        self._cfg = cfg
        self._on_step = on_step
        self._profiles = profiles
        self._workdir = workdir
        self._store_root = store_root
        #: Read once at Start, for a snapshot only: the watcher's read-only client, or else the
        #: printer the print node commands.
        self._reads_printer = watch_printer or printer
        #: Whether a Start here can reach a printer: without one it's held, and the job waits
        #: for an armed run to take it up (mqucifer/print-shop#209).
        self._commands_printer = printer is not None
        #: The terminal's commands each gate has weighed, by their id.
        self._weighed: set[str] = set()
        #: The jobs that wait for the person, which a restart takes up again.
        self._waiting = WaitingJobs(workdir / "jobs.sqlite")

        # Checkpointer — kept open for the daemon's lifetime (CODE_REVIEW.md P0-3).
        checkpointer, conn = open_checkpointer(workdir / "jobs.sqlite")
        self._checkpointer = checkpointer
        #: Closed on exit; the graph must not outlive it (CODE_REVIEW.md P0-3).
        self._conn: sqlite3.Connection | None = conn

        # Whether the intake or the review is with the person, and when the job last came back
        # from them (mqucifer/print-shop#125): their time isn't counted against a wait.
        self._with_person = threading.Event()
        self._back_at = time.monotonic()
        #: Jobs a wait ran out on, with the sentence that ended them: no gate opens for one.
        #: Marked before the ledger's row is written, so a step that ends meanwhile sees it.
        self._abandoned: dict[str, str] = {}
        #: Why each Start that was held started nothing, and how a Start taken was confirmed,
        #: in order, for the terminal to say.
        self._held: dict[str, list[str]] = {}
        #: The open gate's word for a press (`approval_buttons`), "" with none open.
        self._press = ""
        #: The daemon is closing: a job its worker can't finish still waits.
        self._closing = False
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
            asker=self._persons(asker, Waits.QUESTIONS) if asker is not None else None,
            reviewer=self._persons(reviewer, Waits.REVIEW) if reviewer is not None else None,
            loop=(dataclasses.replace(loop, person=self._persons(loop.person, Waits.QUESTIONS))
                  if loop is not None else None),
            show=show,
            store=store,
            waiting=(dataclasses.replace(
                waiting, closer=self._persons(waiting.closer, Waits.QUESTIONS))
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
        #: How each job's last gate was decided, as the terminal says it once it's decided:
        #: "started on the card", "rejected here".
        self._decided: dict[str, str] = {}

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
        # A job cut short from here on still waits, to be taken up again.
        self._closing = True
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
            # Absolute, so a `--resume` from another folder finds it (mqucifer/print-shop#209).
            "stl_path": str(Path(stl_path).absolute()),
            "as_laid": as_laid,
            "context_path": str(Path(context).absolute()) if context else None,
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
        """Start a job's worker with `given` as its state, if no job is active. The job waits in
        the jobs folder until it ends, with a copy of the profiles it slices with."""
        job_id = self._take(part, str(uuid.uuid4()))
        try:
            self._waiting.add(WaitingJob(
                job_id=job_id, part=part, given=given, profiles=self._kept_profiles(job_id)))
        except Exception:  # noqa: BLE001 - the job runs either way, as it did before
            # A job that couldn't be kept still runs: it just won't survive its process.
            logger.exception("job %s couldn't be kept to survive a restart", job_id)
        self._work(job_id, self._run_job, given)
        return job_id

    def _take(self, part: str, job_id: str) -> str:
        """Make `job_id` the active job.

        Raises:
            JobRejected: If another is.
        """
        with self._job_lock:
            if self._active_job_id is not None:
                raise JobRejected(
                    f"job {self._active_job_id} is already running"
                )
            self._active_job_id = job_id
            self._parts[job_id] = part
            self._back_at = time.monotonic()
        return job_id

    def _work(self, job_id: str, target: Callable[..., None], *args: Any) -> None:
        """The job's worker, started."""
        self._step(job_id, Step.PREPARING)
        self._publish_state({"status": "analyzing", "job_id": job_id})
        self._worker = threading.Thread(target=target, args=(job_id, *args), daemon=True)
        self._worker_alive = True
        self._at_gate.clear()
        self._worker.start()

    def _kept_profiles(self, job_id: str) -> SlicerProfiles | None:
        """The profiles this daemon slices with, copied into the job's folder, where the next
        job's can't write over them. None with none."""
        profiles = self._profiles
        if profiles is None:
            return None
        into = self._workdir / job_id / "profiles"
        into.mkdir(parents=True, exist_ok=True)
        copied: dict[str, Path] = {}
        for kind in ("machine", "process", "filament"):
            path: Path | None = getattr(profiles, kind)
            if path is not None:
                copied[kind] = Path(shutil.copy2(path, into / f"{kind}.json"))
        return profiles.model_copy(update=copied)

    def waiting(self) -> list[WaitingJob]:
        """The jobs that wait for the person, the oldest first (mqucifer/print-shop#209)."""
        return self._waiting.all()

    def resume(self, job_id: str) -> str:
        """Take a waiting job up again, at the step it waits at (mqucifer/print-shop#209).

        At the questions or the review, they're shown again with what was answered and corrected
        before; ready, its gate opens again, unapproved. A job a restart caught after its Start
        and before anything reached the printer is ready again. Returns its part.

        Raises:
            KeyError: If no job by that id is waiting.
            JobRejected: If another job is active.
        """
        kept = self._waiting.get(job_id)
        if kept is None:
            raise KeyError(job_id)
        self._take(kept.part, job_id)
        self._work(job_id, self._resume_job, kept)
        return kept.part

    def start(self, job_id: str, *, bed_clear: bool) -> str:
        """Start at the terminal (mqucifer/print-shop#209): the same Start as the card's, for
        the job whose gate before printing is open. `bed_clear` is the person's own yes to the
        bed's question, asked as they Start. Returns "" once it's been checked, whether it
        started the job or was held (said, as the card's is, to `wait_for_finish`'s `tell`), or
        why it wasn't taken: one Start a gate, whichever comes first.
        """
        return self._from_terminal(Command(
            decision=Decision.APPROVE, job_id=job_id,
            bed=BED_AT_TERMINAL if bed_clear else ""))

    def reject(self, job_id: str) -> str:
        """Reject at the terminal: as the card's. Returns "", or why it wasn't taken."""
        return self._from_terminal(Command(decision=Decision.REJECT, job_id=job_id))

    def decided(self, job_id: str) -> str:
        """Why a Start or Reject from the terminal can't be taken now, its gate being decided or
        gone: "it was started on the card", or "" while it's open (mqucifer/print-shop#209)."""
        with self._job_lock:
            gate = self._pending_gate
            if (self._active_job_id == job_id and self._gate_inbox is not None
                    and gate is not None and gate.get("job_id") == job_id):
                return ""
            how = self._decided.get(job_id)
        return f"it was {how}" if how else "it was already started or decided"

    def _from_terminal(self, cmd: Command) -> str:
        cmd = cmd.model_copy(update={"by": "here"})
        if why := self.decided(cmd.job_id):
            return why
        with self._job_lock:
            gate = self._pending_gate
            if (self._active_job_id != cmd.job_id or self._gate_inbox is None or gate is None
                    or gate.get("job_id") != cmd.job_id):
                how = self._decided.get(cmd.job_id)
                return f"it was {how}" if how else "it was already started or decided"
            if cmd.decision is Decision.APPROVE and gate.get("gate") != Gate.BEFORE_PRINT:
                return "only the gate before printing starts a print"
            word = self._press
            cmd = cmd.model_copy(update={"press": word, "id": uuid.uuid4().hex})
            self._gate_inbox.put(cmd)
        # Back once it's been checked: the gate has a new word for a press, or none.
        end = time.monotonic() + _CHECKED_WITHIN_S
        while self._press == word and time.monotonic() < end:
            time.sleep(0.02)
        with self._job_lock:
            if cmd.id in self._weighed:
                return ""
        # The card's came first, and this one was spent with it, unweighed.
        return "the card's came first, and is what's said of the gate now"

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
        count, however long they take (mqucifer/print-shop#125, mqucifer/print-shop#209).

        Whether the gate opened or the wait ran out is decided once, under the job's lock: a
        gate that opens as it runs out is returned, and a job it ran out on opens none.

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
                with_person = self._with_person.is_set() or self._pending_gate is not None
                if not with_person and time.monotonic() - self._back_at >= timeout_s:
                    sentence = self._mark_abandoned(job_id, "waiting for the approval gate",
                                                    timeout_s)
                    ran_out = True
                else:
                    ran_out = False
                    self._at_gate.clear()
            if ran_out:
                raise TimeoutError(self._record_abandoned(job_id, sentence, "waiting for the "
                                                          "approval gate", timeout_s))
            self._at_gate.wait(timeout=0.2)

    def abandon(self, job_id: str, what: str, waited_s: float) -> str:
        """End a job a wait ran out on: the sentence that says so, and the ledger's row.

        Nothing is sent to the printer after this: no gate opens for the job, and the ledger
        refuses its start. One sentence says what ran out, after how long, and that nothing
        printed, and the ledger keeps it as timed out (mqucifer/print-shop#125). A job already
        sent to the printer is a print, and its row stays open, said so instead.

        The job is marked ended before the ledger's row is written, so a step that ends while
        it's written opens no gate (mqucifer/print-shop#209).
        """
        with self._job_lock:
            sentence = self._mark_abandoned(job_id, what, waited_s)
        return self._record_abandoned(job_id, sentence, what, waited_s)

    def _mark_abandoned(self, job_id: str, what: str, waited_s: float) -> str:
        """Mark the job ended. Called with the job's lock held."""
        sentence = f"Timed out {what} after {duration(waited_s)}, so nothing printed."
        self._abandoned.setdefault(job_id, sentence)
        return sentence

    def _record_abandoned(self, job_id: str, sentence: str, what: str, waited_s: float) -> str:
        """The ledger's row for a job just marked ended, and the sentence as it stands."""
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
        self._forget(job_id)
        return sentence

    def _forget(self, job_id: str) -> None:
        """The job no longer waits: it ended."""
        try:
            self._waiting.remove(job_id)
        except (OSError, sqlite3.Error):
            logger.exception("job %s ended, and couldn't be taken off the waiting jobs", job_id)

    def wait_for_finish(
        self, job_id: str, timeout_s: float, tell: Callable[[str], None] | None = None
    ) -> JobResult:
        """Block until the job finishes (or times out).

        `tell` is told each Start that was held, and why, as it happens
        (mqucifer/print-shop#218), and how the bed was confirmed for the one taken.

        `timeout_s` counts the machine's steps only, from when the job last left the person:
        an open gate waits as long as they take (mqucifer/print-shop#209).

        Returns the JobResult. Raises TimeoutError on timeout; the caller ends the job with
        `abandon`.
        """
        began = time.monotonic()
        told = 0
        while True:
            with_person = self._with_person.is_set() or self._pending_gate is not None
            if not with_person and time.monotonic() - max(began, self._back_at) >= timeout_s:
                break
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

    def _persons(self, step: Callable[_P, _R], waits: Waits) -> Callable[_P, _R]:
        """`step` (the intake or the review), marked as with the person while it runs, and the
        job as waiting there, for a restart (mqucifer/print-shop#209)."""

        def with_person(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            self._with_person.set()
            job_id = self._active_job_id
            self._waits(job_id, waits)
            try:
                return step(*args, **kwargs)
            finally:
                self._back_at = time.monotonic()
                self._with_person.clear()
                self._waits(job_id, Waits.PREPARING)

        return with_person

    def _waits(self, job_id: str | None, waits: Waits) -> None:
        if job_id is None:
            return
        try:
            self._waiting.at(job_id, waits)
        except (OSError, sqlite3.Error):
            logger.warning("couldn't keep where job %s waits", job_id, exc_info=True)

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
            # A new word for the next press, too: one sent again names a word that's gone.
            self._press = uuid.uuid4().hex
            return self._gate_inbox

    def _close_gate(self) -> None:
        """Drop the inbox. Idempotent — may be called on an already-closed gate."""
        with self._job_lock:
            self._gate_inbox = None
            self._press = ""

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
        that names another job, or none (mqucifer/print-shop#207), and one that
        doesn't name the open gate's word for its press: one from before a
        restart, or one already spent there (mqucifer/print-shop#209).

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
            if cmd.press != self._press:
                logger.warning("ignored %s for job %s: it names a press this gate has spent, "
                               "or none, so it was sent before, or again", cmd.decision,
                               cmd.job_id)
                return
            # The card asks the bed in a dialog, which can't be checked from here
            # (mqucifer/print-shop#214 is what binds it to a person): said as such, never more.
            cmd = cmd.model_copy(update={"bed": BED_ON_CARD, "by": "on the card"})
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
        self._ran(job_id, lambda: self._graph.invoke(initial_state, config=thread_config),
                  thread_config)

    def _ran(
        self, job_id: str, run: Callable[[], dict[str, Any]], thread_config: dict[str, Any],
    ) -> None:
        """Run the graph to its first gate, or its end, and go on from there."""
        try:
            result = run()
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

    def _resume_job(self, job_id: str, kept: WaitingJob) -> None:
        """Take a waiting job up again from its checkpoint (mqucifer/print-shop#209)."""
        config: dict[str, Any] = {"configurable": {"thread_id": job_id}}
        try:
            snapshot = self._graph.get_state(config)
            ended = self._ledger.unprinted(job_id) if self._ledger is not None else None
            row = self._ledger.get(job_id) if self._ledger is not None else None
        except Exception as exc:
            self._finish_job(job_id, approved=False, printed=False,
                             error=f"it couldn't be read back ({type(exc).__name__}: {exc})")
            return
        if ended is not None:
            self._finish_job(job_id, approved=False, printed=False, error=ended.detail)
            return
        if row is not None:
            # Its start was asked of the printer: a print, which the ledger and the watcher have.
            self._finish_job(job_id, approved=True, printed=False,
                             error="it had been sent to the printer before the restart: its "
                                   "row in the ledger says what came of it")
            return
        if not snapshot.values:
            # Stopped before its first step ended: run from the start, with what it was given.
            self._run_job(job_id, kept.state())
            return
        if tuple(snapshot.next) == ("print",):
            # Started, and stopped before anything reached the printer: an approval isn't kept,
            # so it's ready again, from the checkpoint at its gate. Without one it ends: the
            # print node never runs on a Start from before the restart.
            at_gate = self._at_its_gate(config)
            if at_gate is None:
                self._finish_job(job_id, approved=False, printed=False, error=(
                    "it was started before the restart, and its gate couldn't be found again, "
                    "so nothing was sent: run the job again"))
                return
            snapshot = at_gate
        interrupts = [i for task in snapshot.tasks for i in task.interrupts]
        if interrupts:
            self._ran(job_id, lambda: {"__interrupt__": interrupts}, config)
        elif snapshot.next:
            self._ran(job_id, lambda: self._graph.invoke(None, config=config), config)
        else:
            self._finish_job(job_id, approved=False, printed=False,
                             error="it had already ended before the restart")

    def _at_its_gate(self, config: dict[str, Any]) -> Any:
        """The job's last checkpoint at the gate before printing, made its latest, unanswered;
        or None if there's none."""
        for past in self._graph.get_state_history(config):
            if tuple(past.next) == (Gate.BEFORE_PRINT.value,) and any(
                    t.interrupts for t in past.tasks):
                # Written again as the newest, so the graph goes on from there, and the press
                # it had is gone with what came after.
                again = self._graph.update_state(past.config, None)
                return self._graph.get_state({"configurable": {
                    "thread_id": config["configurable"]["thread_id"],
                    "checkpoint_id": again["configurable"]["checkpoint_id"]}})
        return None

    def _handle_interrupt(
        self, job_id: str, result: dict[str, Any], thread_config: dict[str, Any]
    ) -> None:
        """Handle a parked graph: publish gate state, wait for command, resume."""
        # LangGraph 1.x returns `__interrupt__` as a tuple of Interrupt objects;
        # the payload lives on `.value`.
        interrupt = result["__interrupt__"][0]
        gate_payload: dict[str, Any] = dict(interrupt.value)
        before_print = gate_payload.get("gate") == Gate.BEFORE_PRINT
        with self._job_lock:
            # Decided once, under the lock `wait_for_gate` decides a wait under: a job a wait ran
            # out on opens no gate, and one whose gate opens isn't run out on
            # (mqucifer/print-shop#209).
            ended = self._abandoned.get(job_id)
            if ended is None:
                self._pending_gate = {**gate_payload, "job_id": job_id}
        if ended is not None:
            # A wait ran out on it before the gate: the person was told nothing would print.
            self._finish_job(job_id, approved=False, printed=False, error=ended)
            return
        self._waits(job_id, Waits.READY if before_print else Waits.MESH)

        # Open a fresh inbox before publishing the gate, so a press that lands
        # the moment the dashboard updates has somewhere to go. It is scoped to
        # this gate and closed once we have our answer.
        inbox = self._open_gate()
        self._step(job_id, Step.AT_GATE)
        # The buttons name the job, and the press, before the card that shows them does.
        self._name_buttons(job_id, self._press)

        # Publish the gate with pending: true.
        gate_msg = {**gate_payload, "pending": True, "job_id": job_id}
        self._bridge_publish(gate_topic(self._cfg), gate_msg)

        # Publish state with awaiting_approval.
        self._publish_state(self._waiting_state(job_id, gate_payload))

        # Signal that we're at a gate, now it's published.
        with self._job_lock:
            self._opened.setdefault(job_id, []).append(dict(self._pending_gate or {}))
            self._at_gate.set()

        # Wait for a command from this gate's inbox, as long as the person takes
        # (mqucifer/print-shop#209). A Start that's held waits for another
        # (mqucifer/print-shop#218).
        while True:
            cmd = inbox.get()
            self._weigh(cmd)
            if cmd.decision is not Decision.APPROVE or not before_print:
                break
            why = self._not_yet(thread_config, cmd)
            # This press is spent, and so is any that came while it was checked: none is kept
            # to start the job later. A Reject among them still stands, whatever the check said.
            checked, inbox = inbox, self._open_gate()
            if rejected := _rejection(checked):
                cmd = rejected
                self._weigh(cmd)
                break
            if not why:
                break
            self._hold(job_id, gate_payload, why)

        approved = cmd.decision is Decision.APPROVE
        note = cmd.note
        by = cmd.by or "on the card"
        with self._job_lock:
            if approved and before_print:
                said = f"the bed confirmed clear {cmd.bed}"
                note = f"{note}; {said}" if note else said
                self._held.setdefault(job_id, []).append(f"Start taken, {said}.")
                self._decided[job_id] = f"started {by}"
            elif approved:
                self._decided[job_id] = f"approved {by}"
            else:
                self._held.setdefault(job_id, []).append(
                    f"Rejected {by}: nothing printed, and the job has ended.")
                self._decided[job_id] = f"rejected {by}"

        # We have our answer; close this gate's inbox so a stale press cannot
        # leak into the next gate.
        self._close_gate()
        self._left_gate()

        # Clear the gate.
        self._bridge_publish(gate_topic(self._cfg), {"pending": False})
        self._name_buttons()
        if approved:
            self._waits(job_id, Waits.PREPARING)
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

    def _weigh(self, cmd: Command) -> None:
        """`cmd` is the one the gate weighs, for a terminal's Start to know it was."""
        if cmd.id:
            with self._job_lock:
                self._weighed.add(cmd.id)

    def _not_yet(self, thread_config: dict[str, Any], cmd: Command) -> str:
        """Why this Start mustn't start the job, with what to do, or "" (mqucifer/print-shop#218,
        mqucifer/print-shop#209).

        The spool first, then what Start checks again (`start.why_not_now`), then the bed. The
        printer is read once, for a snapshot, and never waited on for a Reject: one that came
        meanwhile wins. A check that can't be made holds.
        """
        try:
            state = self._graph.get_state(thread_config).values
            found = [why for why in (why_not_start(state, self._ledger),) if why]
            found += why_not_now(state, self._ledger, store_root=self._store_root,
                                 printer=self._reads_printer)
        except Exception as exc:  # noqa: BLE001 - never started on a check that wasn't made
            logger.exception("what Start checks couldn't be checked")
            return f"what Start checks couldn't be checked ({exc}): Start again"
        if not cmd.bed:
            found.append(BED_NOT_SAID)
        if not self._commands_printer:
            # Held, so the job keeps waiting: an unarmed run never ends a ready job.
            found.append(NOT_ARMED)
        return ". Also, ".join(found)

    def _hold(self, job_id: str, gate_payload: dict[str, Any], why: str) -> None:
        """A Start held: the gate stays open, saying why, on the card and in the terminal, with a
        new word for the next press."""
        said = f"Not started: {why}"
        # What's held says what to load, so the gate's own line, from when it opened, gives way.
        gate_payload = {**gate_payload, "spools": ""}
        if self._pending_gate is not None:
            self._pending_gate = {**self._pending_gate, "held": said}
        self._name_buttons(job_id, self._press)
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

        # It no longer waits, however it ended. A job cut short by this daemon closing isn't
        # ended: it waits to be taken up again (mqucifer/print-shop#209).
        if not self._closing:
            self._forget(job_id)

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

    def _name_buttons(self, job_id: str = "", press: str = "") -> None:
        """Approve and Reject republished, naming `job_id` and the gate's word for a press, or no
        job (mqucifer/print-shop#207, mqucifer/print-shop#209)."""
        for topic, config in approval_buttons(self._cfg, job_id, press):
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
