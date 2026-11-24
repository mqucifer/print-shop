"""The phone's Start confirm: made at each view of the Start page, spent at its first use
(mqucifer/print-shop#214).

A confirm is a random 128-bit id, held only in this process's memory, never on disk: a restart
forgets every one. It's bound to:

- the signed-in phone (its secret's hash), and its name;
- the job's id;
- the gate's word for a press, new at each gate and after each Start it spends
  (mqucifer/print-shop#209);
- the G-code's SHA-256;
- the phone's address;
- when it was made.

`spend` takes it out first, whatever follows, so the same confirm never goes to the gate twice:
a POST sent again, byte for byte, finds nothing. A confirm older than `CONFIRM_LASTS_S` is
refused.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["CONFIRM_LASTS_S", "Confirm", "Confirms", "EXPIRED", "HELD_AT_MOST", "UNKNOWN"]

#: How long a confirm lasts from the page's view: enough to open the page, go to the printer and
#: look at the bed. The Sponsor's choice, 2026-11-24 (mqucifer/print-shop#280): "10 minutes".
CONFIRM_LASTS_S = 600.0

#: The most confirms held at once: the oldest goes first. A page viewed many times makes many.
HELD_AT_MOST = 32

EXPIRED = (f"That confirm has expired (it lasts {CONFIRM_LASTS_S / 60:g} minutes): tick the bed "
           "again to start")
UNKNOWN = ("That confirm was already used, or isn't one: nothing was started. Reload the page "
           "to start")
OTHER_PHONE = "That confirm was made for another phone: nothing was started. Reload the page"
OTHER_ADDRESS = ("That confirm was made at another address: nothing was started. Reload the "
                 "page")


def secret_hash(secret: str) -> str:
    """How a confirm names the phone it's for: its secret's hash, never the secret."""
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass(frozen=True)
class Confirm:
    """What one confirm is bound to."""

    phone_hash: str
    phone: str
    job_id: str
    press: str
    gcode_sha256: str
    address: str
    made_at: float


class Confirms:
    """The confirms made and not yet spent, in memory only."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._held: OrderedDict[str, Confirm] = OrderedDict()

    def make(self, *, phone_hash: str, phone: str, job_id: str, press: str, gcode_sha256: str,
             address: str) -> str:
        """A new confirm's id, bound as above."""
        made = secrets.token_hex(16)  # 128 bits
        with self._lock:
            self._held[made] = Confirm(phone_hash=phone_hash, phone=phone, job_id=job_id,
                                       press=press, gcode_sha256=gcode_sha256, address=address,
                                       made_at=self._clock())
            while len(self._held) > HELD_AT_MOST:
                self._held.popitem(last=False)
        return made

    def spend(self, made: str, *, phone_hash: str, address: str) -> Confirm | str:
        """The confirm `made`, spent now whatever follows; or why it can't be used, in words."""
        with self._lock:
            found = self._held.pop(made, None) if made else None
        if found is None:
            return UNKNOWN
        if found.phone_hash != phone_hash:
            return OTHER_PHONE
        if found.address != address:
            return OTHER_ADDRESS
        if self._clock() - found.made_at > CONFIRM_LASTS_S:
            return EXPIRED
        return found

    def forget_all(self) -> None:
        with self._lock:
            self._held.clear()
