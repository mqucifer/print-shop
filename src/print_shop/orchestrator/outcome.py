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
With a camera, it also keeps the print's frames (`frames.FrameKeeper`,
mqucifer/print-shop#110): one each time the layer it reads has moved on, and
one at the end.

**The desk** is the gate. It shows the oldest unanswered print on a retained
MQTT topic, holds the Sponsor's draft (a result, a symptom, a note) as Home
Assistant's selects and text box set it, and records it on Record. An answer
that does not add up is refused and the reason shown, not stored. Like the
approval gate, a press is scoped to the print on show by time: only one print
is shown at once, the draft resets whenever the print on show changes, and the
part's name is on the card beside the buttons.

After a print with a swap (mqucifer/print-shop#218) what's on the printer isn't known until a
person says. The watcher never says it: an end, a layer, an idle or a file name moves no stock
and clears nothing. That print's own card offers the spools in words that say both halves, that
it's over and which is on now, with none pre-selected, and Record takes the pick.

With frames (mqucifer/print-shop#110), the desk also shows the last, and
pages through the rest with Earlier and Later, beside the answer: evidence,
never the answer. A print with no frame at all says why, in a sentence
(mqucifer/print-shop#123): the camera off, or why its last grab failed.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pycentauri.models import PrintStatus as SdcpPrintStatus

from print_shop.core.mqtt import (
    OUTCOME_SPOOL_AS_RECORDED,
    MqttConfig,
    outcome_frame_set_topic,
    outcome_frame_topic,
    outcome_note_topic,
    outcome_result_topic,
    outcome_spool_select,
    outcome_spool_topic,
    outcome_submit_topic,
    outcome_symptom_topic,
    outcome_topic,
)
from print_shop.inventory import Pick
from print_shop.ledger import Answer, Frame, Ledger, LedgerError, Result, Row, SaidSince, Symptom
from print_shop.orchestrator import spools
from print_shop.orchestrator.frames import FrameKeeper
from print_shop.printer.centauri import PrinterClient, PrinterSnapshot

__all__ = [
    "END_STATES",
    "UNSET",
    "OutcomeDesk",
    "Watcher",
    "end_name",
    "no_frames",
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


def no_frames(why: str | None) -> str:
    """Why a print has no camera frame at all, as the outcome step says it: one sentence."""
    if why is None:
        return "No camera frame was kept for this print: nothing was asked of the camera."
    return f"No camera frame was kept for this print: {why.rstrip('.')}."


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
        frames: FrameKeeper | None = None,
        confirming: Callable[[], str | None] | None = None,
    ) -> None:
        self._ledger = ledger
        self._frames = frames
        #: This process's own job, whose start the print node confirms itself.
        self._confirming = confirming
        self._said_unreadable = False
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
        # A swap's start is looked for however late, and though its process was killed: being
        # seen to begin can only make what's on not known again (mqucifer/print-shop#218).
        own = self._confirming() if self._confirming is not None else None
        unseen = [r for r in self._ledger.swaps_unseen() if r.job_id != own]
        if not running and not unconfirmed and not unseen:
            return
        try:
            snapshot = self._printer.snapshot()
        except Exception as exc:
            if running or unconfirmed:
                raise
            # Looking only for a start that may never come, for as long as it takes: said once.
            if not self._said_unreadable:
                logger.warning("the printer couldn't be read (%s): still looking for whether job "
                               "%s's start, never confirmed, begins", exc, unseen[-1].job_id)
                self._said_unreadable = True
            return
        self._said_unreadable = False

        for row in {r.job_id: r for r in (*unconfirmed, *unseen)}.values():
            if _is_ours(snapshot, row):
                logger.warning("job %s is printing after all; watching it", row.job_id)
                self._ledger.record_started(row.job_id)
                running.append(row)

        ended_any = False
        for row in running:
            if print_ended(snapshot):
                # One printer, one bed: what the agent started is over.
                if self._frames is not None:
                    self._frames.at_end(row.job_id, snapshot.layer, snapshot.total_layers)
                self._ledger.record_ended(row.job_id, snapshot.print_status)
            elif snapshot.active_filename and not _is_ours(snapshot, row):
                # Something else holds the printer, so ours ended while nobody
                # was looking. How it ended is not known, and is not guessed. The firmware
                # names no file in some frames, "" (`centauri._confirm_started`): that's no other.
                self._ledger.record_ended(row.job_id, None)
            else:
                if self._frames is not None and _is_ours(snapshot, row):
                    self._frames.at_layer(row.job_id, snapshot.layer, snapshot.total_layers)
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
        publish_image: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self._ledger = ledger
        self._cfg = cfg
        self._publish = publish
        self._publish_image = publish_image
        #: The frame on show, by its place in the print's frames; None is the last.
        self._frame: int | None = None
        #: What the image topic holds now, so a frame is sent once, not at every refresh.
        self._image: tuple[str, str] | None = None
        self._lock = threading.Lock()
        self._shown: str | None = None
        self._draft: dict[str, str] = {}
        self._error: str | None = None
        #: What the print on show can be told is on now, and those last sent as the select's.
        self._spools: dict[str, Pick] = {}
        #: The one picked, by its words, its number and what it was picked under: Record refuses
        #: it if that has changed since.
        self._picked: tuple[str, Pick] | None = None
        self._offered: tuple[str, ...] | None = None

    @property
    def topics(self) -> dict[str, Callable[[str, bytes, bool], None]]:
        """The command topics this desk answers, for the bridge to subscribe."""
        return {
            outcome_result_topic(self._cfg): self._setter("result", {UNSET, *Result}),
            outcome_symptom_topic(self._cfg): self._setter("symptom", {UNSET, *Symptom}),
            outcome_note_topic(self._cfg): self._setter("note", None),
            outcome_spool_topic(self._cfg): self._set_spool,
            outcome_submit_topic(self._cfg): self._on_submit,
            outcome_frame_set_topic(self._cfg): self._on_frame,
        }

    def refresh(self) -> None:
        """Show the oldest unanswered print, or that none is waiting."""
        with self._lock:
            self._show_locked()

    def _show_locked(self) -> None:
        waiting = self._ledger.awaiting_answer()
        if not waiting:
            self._shown, self._draft, self._error, self._frame = None, {}, None, None
            self._picked = None
            self._offer_spools({})
            self._publish(outcome_topic(self._cfg), {"pending": False, "waiting": 0})
            self._show_frame(None)
            return
        row = waiting[0]
        if row.job_id != self._shown:
            self._shown, self._draft, self._error, self._frame = row.job_id, {}, None, None
            self._picked = None
        frames = self._ledger.frames(row.job_id)
        at = min(self._frame, len(frames) - 1) if self._frame is not None else len(frames) - 1
        shown = frames[at] if frames else None
        self._show_frame(shown)
        can_say, spools_said = self._spools_of(row)
        if self._picked is not None:
            # Picked again, it's picked under what's shown now.
            can_say.setdefault(*self._picked)
        self._offer_spools(can_say)
        paused = row.reading.pauses if row.reading else ()
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
                # What it paused for (mqucifer/print-shop#119, mqucifer/print-shop#218): how
                # that went goes in the note. A swap's spools, and the pick of the one on now.
                "pause": "; ".join(p.line() for p in paused),
                "pause_for": next((p.kind.value for p in paused if p.kind), ""),
                "spools": spools_said,
                "spool": self._picked[0] if self._picked else OUTCOME_SPOOL_AS_RECORDED,
                "spool_choices": len(can_say),
                "result": self._draft.get("result", UNSET),
                "symptom": self._draft.get("symptom", UNSET),
                "note": self._draft.get("note", ""),
                "error": self._error,
                "frames": len(frames),
                "frame": at + 1 if frames else 0,
                "frame_at": shown.line() if shown else "",
                "frames_why": "" if frames else no_frames(self._ledger.frames_missed(row.job_id)),
            },
        )

    def _spools_of(self, row: Row) -> tuple[dict[str, Pick], str]:
        """A swap with no spool that could be recorded takes the pick too."""
        try:
            return spools.choices(self._ledger, row), spools.said(self._ledger, row)
        except Exception:  # noqa: BLE001 - the stock is never in the way of an answer
            logger.exception("the stock couldn't be read for job %s's outcome", row.job_id)
            return {}, ""

    def _offer_spools(self, can_say: dict[str, Pick]) -> None:
        """The select's options, sent before any state that's one of them."""
        self._spools = can_say
        if tuple(can_say) != self._offered:
            self._publish(*outcome_spool_select(self._cfg, can_say))
            self._offered = tuple(can_say)

    def _show_frame(self, frame: Frame | None) -> None:
        """The frame on show on the image topic, or an empty one, which clears it."""
        if self._publish_image is None:
            return
        want = (self._shown or "", frame.path) if frame else None
        if want == self._image:
            return
        jpeg = b""
        if frame is not None:
            try:
                jpeg = Path(frame.path).read_bytes()
            except OSError:
                logger.warning("frame %s is gone; nothing shown", frame.path)
        self._publish_image(outcome_frame_topic(self._cfg), jpeg)
        self._image = want

    def _on_frame(self, topic: str, payload: bytes, retain: bool) -> None:
        """Earlier, later, first or last: which frame is on show."""
        if retain:
            return
        move = payload.decode("utf-8", "replace").strip()
        if move not in ("earlier", "later", "first", "last"):
            return
        with self._lock:
            if self._shown is None:
                return
            count = len(self._ledger.frames(self._shown))
            if not count:
                return
            at = self._frame if self._frame is not None else count - 1
            at = {"earlier": max(at - 1, 0), "later": min(at + 1, count - 1),
                  "first": 0, "last": count - 1}[move]
            self._frame = None if at == count - 1 else at
            self._show_locked()

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

    def _set_spool(self, topic: str, payload: bytes, retain: bool) -> None:
        """The spool on at the end, picked from those offered and kept by its number."""
        if retain:
            return
        value = payload.decode("utf-8", "replace").strip()
        with self._lock:
            if self._shown is None:
                return
            # Picked now, so under what's so now: a load since the card was shown is older.
            row = self._ledger.get(self._shown)
            now = self._spools_of(row)[0] if row is not None else {}
            if picked := now.get(value, self._spools.get(value)):
                self._picked = (value, picked)
            elif value in (UNSET, OUTCOME_SPOOL_AS_RECORDED):
                self._picked = None
            self._error = None
            self._show_locked()

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
                # The answer, the pick and the stock: one write, or none of them.
                on = self._picked[1] if self._picked is not None else None
                self._ledger.record_answer(job_id, answer, on)
            except SaidSince as exc:
                self._error, self._picked = str(exc), None
            except sqlite3.Error as exc:
                self._error = f"it couldn't be written ({exc}): press Record again"
            except ValueError:
                self._error = "choose a result and a symptom first"
            except LedgerError as exc:
                self._error = str(exc)
            else:
                logger.info("job %s answered: %s", job_id, json.dumps(draft))
            self._show_locked()
