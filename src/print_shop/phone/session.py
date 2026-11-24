"""The terminal's own screen for what a job needs, answered from the phone
(mqucifer/print-shop#221).

The questions, the model's closer choices and the review are each answered by the terminal's own
code (`cli_asker`, `cli_waiting`'s closer, `cli_reviewer`), run in a thread of their own. What
they read comes from the phone, and what they write is kept for the page. So the phone asks
nothing the terminal doesn't, and every follow-up the terminal asks (own words read back, a slip
in an answer, a pause's purpose, what's ticked and not used) is asked on the phone in its words.

A form answers the prompts it can ahead (`ahead`, by each prompt's own words, once each). A
prompt it didn't answer, or one asked again, waits for the phone: the page shows what was written
since the last answer, and the prompt, to answer as at the Mac. What the terminal's code returns
is given to the queue (`PrintQueue.give`), once.

A session belongs to one ask (`Asked.since`): if the job's need moves on (answered at the Mac,
put by, sliced again), it's closed and its thread ends. Nothing here starts, arms or approves a
print: a review's ok makes a job ready, and Start is still a person's at the gate.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

__all__ = ["Closed", "Session"]

logger = logging.getLogger(__name__)

#: How long a POST waits for the terminal's code to come to its next prompt or its end.
SETTLE_S = 30.0
#: How often a prompt that waits looks whether its need is still open.
LOOK_EVERY_S = 1.0
#: How long a prompt waits for the phone before the session ends; the job still waits, for the
#: Mac or the phone's next look.
IDLE_S = 30 * 60.0

Read = Callable[[str], str]
Write = Callable[[str], None]


class Closed(EOFError):
    """The session was closed while its code waited: its need moved on."""


class Session:
    """One job's need, answered by the terminal's own code in a thread, from the phone."""

    def __init__(self, job_id: str, since: float, run: Callable[[Read, Write], Any],
                 give: Callable[[Any], str], *, after: Callable[[Any], None] | None = None,
                 alive: Callable[[], bool] = lambda: True, idle_s: float = IDLE_S,
                 ) -> None:
        self.job_id, self.since = job_id, since
        self._cv = threading.Condition()
        self._lines: list[str] = []
        #: Where what was written since the last answer begins.
        self._from = 0
        self._ahead: dict[str, list[str]] = {}
        self._typed: list[str] = []
        self.prompt: str | None = None
        #: Each prompt that waits for the phone is a version: a reply to another is stale.
        self.version = 0
        self.ended = False
        #: How it ended: what the queue said of the answer ("" once given), or why not.
        self.said = ""
        self._closed = False
        self._run, self._give, self._after = run, give, after
        self._alive, self._idle_s = alive, idle_s
        self._thread = threading.Thread(target=self._go, name=f"phone-{job_id[:8]}",
                                        daemon=True)

    # -- the terminal's side -------------------------------------------------

    def _read(self, prompt: str) -> str:
        with self._cv:
            answers = self._ahead.get(prompt)
            if answers:
                typed = answers.pop(0)
                self._lines.append(f"{prompt}{typed}")
                self._from = len(self._lines)
                return typed
            self.prompt = prompt
            self.version += 1
            self._cv.notify_all()
            waited = 0.0
            while not self._typed and not self._closed:
                # A phone that walks away holds nothing: once the job's need moves on (answered
                # at the Mac, put by) or nobody answers for `idle_s`, the session ends.
                self._cv.wait(LOOK_EVERY_S)
                waited += LOOK_EVERY_S
                if not self._typed and (waited >= self._idle_s or not self._alive()):
                    self._closed = True
            if self._closed:
                raise Closed(self.job_id)
            typed = self._typed.pop(0)
            self.prompt = None
            self._lines.append(f"{prompt}{typed}")
            self._from = len(self._lines)
            return typed

    def _write(self, text: str) -> None:
        with self._cv:
            self._lines += text.split("\n")

    def _go(self) -> None:
        try:
            found = self._run(self._read, self._write)
        except Closed:
            said = "put by: answered elsewhere, or it moved on"
        except EOFError:
            said = "ended without an answer"
        except Exception as exc:  # noqa: BLE001 - said on the page; the job waits as it was
            logger.warning("the phone's session for job %s failed", self.job_id, exc_info=True)
            said = f"it couldn't be answered here ({type(exc).__name__}): answer it at the Mac"
        else:
            said = self._give(found)
            if not said and self._after is not None:
                try:
                    self._after(found)
                except Exception:  # noqa: BLE001 - what follows an answer is a courtesy
                    logger.warning("after job %s's answer", self.job_id, exc_info=True)
        with self._cv:
            self.ended, self.said, self.prompt = True, said, None
            self._cv.notify_all()

    # -- the phone's side ----------------------------------------------------

    def start(self, ahead: dict[str, list[str]]) -> None:
        """Begin, with the form's answers to the prompts it answers."""
        with self._cv:
            self._ahead = {k: list(v) for k, v in ahead.items()}
        self._thread.start()

    def reply(self, typed: str, version: int) -> str:
        """An answer to the prompt that waits, as typed at the Mac. Returns "" once taken, or
        why not: the page was of another prompt, or it's ended."""
        with self._cv:
            if self.ended:
                return "it's been answered already"
            if self.prompt is None or version != self.version:
                return "that page is out of date: look again"
            self._typed.append(typed)
            self._cv.notify_all()
            return ""

    def settle(self, timeout: float = SETTLE_S) -> None:
        """Wait until it waits for the phone again, or ends."""
        with self._cv:
            self._cv.wait_for(lambda: self.ended or (self.prompt is not None
                                                     and not self._typed), timeout)

    def said_since(self) -> list[str]:
        """What it wrote since the last answer: what the open prompt asks about."""
        with self._cv:
            return list(self._lines[self._from:])

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()

    @property
    def waiting(self) -> bool:
        with self._cv:
            return not self.ended and self.prompt is not None
