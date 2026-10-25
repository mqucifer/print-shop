"""After a print: see that it ended, then ask the Sponsor how it came out.

mqucifer/print-shop#2: "the outcome is asked through Home Assistant when the
print ends, the same way as the approval gate. A print with no answer stays
open, never guessed." Two parts:

**The watcher** looks at the printer, read-only, until a print the ledger
holds as running reaches an end state, then records when and in which state.
That state is what the printer said (`COMPLETED`, `STOPPED`, `ERROR`); it is
never taken as the outcome. A start that raised gets a bounded look as well:
if the printer turns out to be printing our file after all, the row is marked
started and watched like any other. If not, it stays open with its error.

**The desk** is the gate. It shows the oldest unanswered print on a retained
MQTT topic, holds the Sponsor's draft (a result, a symptom, a note) as Home
Assistant's selects and text box set it, and records it on Record. An answer
that does not add up is refused and the reason shown, not stored. Like the
approval gate, a press is scoped to the print on show by time: only one print
is shown at once, the draft resets whenever the print on show changes, and the
part's name is on the card beside the buttons.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pycentauri.models import PrintStatus as SdcpPrintStatus

from print_shop.core.mqtt import (
    MqttConfig,
    outcome_note_topic,
    outcome_result_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
)
from print_shop.ledger import Answer, Ledger, LedgerError, Result, Row, Symptom
from print_shop.printer.centauri import PrinterClient, PrinterSnapshot

__all__ = [
    "END_STATES",
    "UNSET",
    "OutcomeDesk",
    "Watcher",
    "end_name",
    "print_ended",
]

logger = logging.getLogger(__name__)

#: `PrintInfo.Status` codes in which a print is over. A whitelist, as the
#: ready set is: an unknown code, or none, means keep watching.
END_STATES: frozenset[int] = frozenset(
    {
        SdcpPrintStatus.IDLE,
        SdcpPrintStatus.STOPPED,
        SdcpPrintStatus.COMPLETED,
        SdcpPrintStatus.ERROR,
    }
)

_END_NAMES = {
    SdcpPrintStatus.IDLE: "idle",
    SdcpPrintStatus.STOPPED: "stopped",
    SdcpPrintStatus.COMPLETED: "completed",
    SdcpPrintStatus.ERROR: "error",
}

#: The selects' empty choice. Home Assistant needs a state that is one of the
#: options, so "nothing chosen yet" is an option too.
UNSET = "unset"

#: How long after a start that raised the watcher keeps looking for it. The
#: start-confirm window is five minutes; this is generous on purpose.
UNCONFIRMED_WINDOW = timedelta(minutes=30)


def print_ended(snapshot: PrinterSnapshot) -> bool:
    """Whether the printer has reached an end state. Pure."""
    return snapshot.print_status in END_STATES


def end_name(status: int | None) -> str:
    return _END_NAMES.get(status, "unknown") if status is not None else "unknown"


def _is_ours(snapshot: PrinterSnapshot, row: Row) -> bool:
    active = snapshot.active_filename
    return active is not None and active.rsplit("/", 1)[-1] == row.gcode_name


class Watcher:
    """Watches the ledger's running prints on a thread, through a read-only client.

    `printer` must be unarmed: watching needs `snapshot` and nothing else, and
    a watcher that held an armed connection every minute for hours would be
    the ambient-arming shape again.
    """

    def __init__(
        self,
        *,
        ledger: Ledger,
        printer: PrinterClient,
        on_ended: Callable[[], None],
        poll_s: float = 60.0,
    ) -> None:
        self._ledger = ledger
        self._printer = printer
        self._on_ended = on_ended
        self._poll_s = poll_s
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="outcome-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def poke(self) -> None:
        """A print just started: look now rather than at the next poll."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.look()
            except Exception:
                # A printer that does not answer is the normal case when it is
                # off. Keep the rows open and look again later.
                logger.exception("outcome watcher could not read the printer")
            self._wake.wait(timeout=self._poll_s)
            self._wake.clear()

    def look(self) -> None:
        """One pass: read the printer once if anything is worth reading it for."""
        running = self._ledger.running()
        cutoff = datetime.now(UTC) - UNCONFIRMED_WINDOW
        unconfirmed = [
            r
            for r in self._ledger.unconfirmed_starts()
            if datetime.fromisoformat(r.start_requested_at) >= cutoff
        ]
        if not running and not unconfirmed:
            return
        snapshot = self._printer.snapshot()

        for row in unconfirmed:
            if _is_ours(snapshot, row):
                logger.warning("job %s is printing after all; watching it", row.job_id)
                self._ledger.record_started(row.job_id)
                running.append(row)

        ended_any = False
        for row in running:
            if print_ended(snapshot):
                # One printer, one bed: what the agent started is over.
                self._ledger.record_ended(row.job_id, snapshot.print_status)
            elif snapshot.active_filename is not None and not _is_ours(snapshot, row):
                # Something else holds the printer, so ours ended while nobody
                # was looking. How it ended is not known, and is not guessed.
                self._ledger.record_ended(row.job_id, None)
            else:
                continue
            ended_any = True
        if ended_any:
            self._on_ended()


class OutcomeDesk:
    """The outcome gate's state and its answers. Thread-safe."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        cfg: MqttConfig,
        publish: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._ledger = ledger
        self._cfg = cfg
        self._publish = publish
        self._lock = threading.Lock()
        self._shown: str | None = None
        self._draft: dict[str, str] = {}
        self._error: str | None = None

    @property
    def topics(self) -> dict[str, Callable[[str, bytes, bool], None]]:
        """The command topics this desk answers, for the bridge to subscribe."""
        return {
            outcome_result_topic(self._cfg): self._setter("result", {UNSET, *Result}),
            outcome_symptom_topic(self._cfg): self._setter("symptom", {UNSET, *Symptom}),
            outcome_note_topic(self._cfg): self._setter("note", None),
            outcome_submit_topic(self._cfg): self._on_submit,
        }

    def refresh(self) -> None:
        """Show the oldest unanswered print, or that none is waiting."""
        with self._lock:
            self._show_locked()

    def _show_locked(self) -> None:
        waiting = self._ledger.awaiting_answer()
        if not waiting:
            self._shown, self._draft, self._error = None, {}, None
            self._publish(outcome_topic(self._cfg), {"pending": False, "waiting": 0})
            return
        row = waiting[0]
        if row.job_id != self._shown:
            self._shown, self._draft, self._error = row.job_id, {}, None
        self._publish(
            outcome_topic(self._cfg),
            {
                "pending": True,
                "waiting": len(waiting),
                "job_id": row.job_id,
                "stl_name": row.stl_name,
                "gcode_name": row.gcode_name,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "printer_end": end_name(row.printer_end_status),
                "result": self._draft.get("result", UNSET),
                "symptom": self._draft.get("symptom", UNSET),
                "note": self._draft.get("note", ""),
                "error": self._error,
            },
        )

    def _setter(
        self, field: str, allowed: set[str] | None
    ) -> Callable[[str, bytes, bool], None]:
        def handle(topic: str, payload: bytes, retain: bool) -> None:
            if retain:
                return  # a replayed message is not a choice anyone made now
            try:
                value = payload.decode("utf-8").strip()
            except UnicodeDecodeError:
                return
            if allowed is not None and value not in allowed:
                return
            with self._lock:
                if self._shown is None:
                    return  # nothing on show, so nothing to be choosing for
                if value == UNSET:
                    self._draft.pop(field, None)
                else:
                    self._draft[field] = value[:2000]
                self._error = None
                self._show_locked()

        return handle

    def _on_submit(self, topic: str, payload: bytes, retain: bool) -> None:
        if retain:
            return
        with self._lock:
            job_id = self._shown
            if job_id is None:
                return
            draft = dict(self._draft)
            try:
                answer = Answer(
                    result=Result(draft.get("result", "")),
                    symptom=Symptom(draft.get("symptom", "")),
                    note=draft.get("note", ""),
                )
                self._ledger.record_answer(job_id, answer)
            except ValueError:
                self._error = "choose a result and a symptom first"
            except LedgerError as exc:
                self._error = str(exc)
            else:
                logger.info("job %s answered: %s", job_id, json.dumps(draft))
            self._show_locked()
