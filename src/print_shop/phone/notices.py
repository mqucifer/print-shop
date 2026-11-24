"""Notices to the phone through Home Assistant (mqucifer/print-shop#221): a job needs the person,
a pause is near, or a print ended.

A thread reads what the phone's screens read (`Shop`), and each new thing is sent once, as a
message on the broker's notice topic (`notice_topic`), never retained, so a replay sends nothing.
Home Assistant's automation (`ha-configs/print_shop_notices.yaml`) turns each into a phone
notification whose link opens the phone's page in the phone's browser: never framed, and never
through Home Assistant.

A notice carries the part's name, what it's about, and a path on the page: nothing else. The
automation puts the page's own address, set in it once, before the path, and takes only a path
of the page's own shape: a message on the topic from anything else can't point the link
elsewhere.
Nothing here starts, arms or approves anything.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from print_shop.phone.shop import Shop

__all__ = ["PAUSE_AHEAD_S", "Notice", "Notices"]

logger = logging.getLogger(__name__)

#: How long before a pause the person is told: time to get to the printer.
PAUSE_AHEAD_S = 5 * 60
#: How often it looks.
EVERY_S = 15.0


@dataclass(frozen=True)
class Notice:
    """One notification: its kind ("needs_you", "pause_near", "ended"), its words, and the
    path on the page to open: "/", "/job/<id>" or "/run/<id>"."""

    kind: str
    title: str
    message: str
    path: str

    def payload(self) -> dict[str, str]:
        return asdict(self)


class Notices:
    """What's new since it last looked, sent once each, for as long as the run lasts."""

    def __init__(self, shop: Shop, send: Callable[[Notice], None], *,
                 every_s: float = EVERY_S, pause_ahead_s: float = PAUSE_AHEAD_S) -> None:
        self.shop, self.send = shop, send
        self.every_s, self.pause_ahead_s = every_s, pause_ahead_s
        self._sent: set[tuple[str, ...]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def look(self) -> list[Notice]:
        """One look: each thing new since the last is sent, and returned."""
        found: list[tuple[tuple[str, ...], Notice]] = []
        for line in self.shop.lines():
            if line.needs is None:
                continue
            found.append(((line.job.job_id, "needs", str(line.needs)), Notice(
                "needs_you", "print-shop needs you",
                f"{line.job.part}: {line.needs}.", f"/job/{line.job.job_id}")))
        for now in self.shop.printing():
            ahead = self.shop.pause_ahead(now.job_id)
            if ahead is None or not 0 < ahead[1] <= self.pause_ahead_s:
                continue
            found.append(((now.job_id, "pause", str(ahead[0])), Notice(
                "pause_near", "A pause is near", f"{now.part}: {now.pause}", "/")))
        for done in self.shop.to_record():
            found.append(((done.job_id, "ended"), Notice(
                "ended", "A print ended", f"{done.part} ended. How did it come out?",
                f"/run/{done.job_id}")))
        sent = []
        for key, notice in found:
            if key in self._sent:
                continue
            try:
                self.send(notice)
            except Exception:  # noqa: BLE001 - a notice is a courtesy: the run goes on
                logger.warning("a notice couldn't be sent", exc_info=True)
                continue
            self._sent.add(key)
            sent.append(notice)
        return sent

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="phone-notices", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.look()
            except Exception:  # noqa: BLE001 - looked again next time
                logger.warning("the notices couldn't look", exc_info=True)
            self._stop.wait(self.every_s)
