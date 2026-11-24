"""Whether anything in this process may arm the printer, and for what.

**A person arms each start** (mqucifer/print-shop#214). There are two ways, and no others:

1. **The Start on a signed-in phone, on the home Wi-Fi** (`print_shop.phone`). Its confirm arms
   one job for one start: `OneStart` holds that, in this process's memory only, the print node
   takes it once, and it's disarmed straight after, however the start went. A restart has
   nothing armed.
2. **`PRINT_AGENT_ALLOW_HARDWARE=1` on the command's own line.** It is deliberately never read
   from `.env`: a `.env` value is ambient configuration, set once and in force for every run
   after, and that is the shape of what went wrong on 2026-09-16, when the prototype's test
   suite started two real prints. Arming has to be an act, made again on each invocation. Under
   it, the terminal's `start` and `y` starts the print. The name is the prototype's, kept so the
   Sponsor's command is the same one.

Nothing else arms: no setting, no Home Assistant entity or automation, no MQTT message (a
button's payload is in its retained discovery config, readable by every client of the broker),
and no test (`tests/conftest.py` refuses every armed connection, whatever the environment).

Who armed each start, how, from where and when is kept with the print's ledger row
(`Ledger.record_start_requested`'s `armed`).
"""

from __future__ import annotations

import os
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from print_shop.printer.centauri import PrinterClient

__all__ = [
    "ALLOW_HARDWARE_ENV", "Armed", "BY_FLAG", "OneStart", "armed_by_flag", "hardware_allowed",
    "spend",
]

ALLOW_HARDWARE_ENV = "PRINT_AGENT_ALLOW_HARDWARE"

#: Who armed a start the flag armed, as the ledger keeps it.
BY_FLAG = f"{ALLOW_HARDWARE_ENV}=1 on the command line"


def hardware_allowed() -> bool:
    """True only when the invocation's environment opted in, with exactly "1"."""
    return os.environ.get(ALLOW_HARDWARE_ENV) == "1"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Armed(BaseModel):
    """Who armed one job's start, how, from where and when: what the ledger keeps with its row.

    `token` is what makes one from a phone's confirm spendable, once (`spend`). It is never
    written anywhere: the ledger keeps the rest.
    """

    model_config = ConfigDict(frozen=True)

    job_id: str
    how: Literal["phone", "flag"]
    #: The phone's name, as it was paired, or `BY_FLAG`.
    by: str
    #: The phone's address on the home Wi-Fi, or "the Mac".
    where: str
    #: When it was armed, UTC.
    at: str
    token: str = ""

    def said(self) -> str:
        """As a line says it: "armed on the phone "Sponsor's phone" (192.168.1.31)"."""
        if self.how == "phone":
            return f'armed on the phone "{self.by}" ({self.where}), on the home Wi-Fi'
        return f"armed by {self.by}, at the Mac"


def armed_by_flag(job_id: str) -> Armed:
    """The record of a start the flag armed: the person who typed the command, at the Mac."""
    return Armed(job_id=job_id, how="flag", by=BY_FLAG, where="the Mac", at=_now())


#: The phone's starts handed to the print node and not yet spent, by token. Module-level, so
#: `live_printer` can tell one `OneStart.take` handed out from one made by hand.
_spendable: dict[str, Armed] = {}
_spendable_lock = threading.Lock()


def spend(armed: Armed) -> bool:
    """True, once, for a phone's start `OneStart.take` handed out; False for anything else,
    including the same one again. `live_printer` asks this before it builds an armed client."""
    if armed.how != "phone" or not armed.token:
        return False
    with _spendable_lock:
        held = _spendable.pop(armed.token, None)
    return held is not None and held == armed


class OneStart:
    """At most one armed start, for one job, from a phone's confirm (mqucifer/print-shop#214).

    The gate arms it only once its checks at Start pass (`Daemon`), the print node takes it once
    (`take`), and the gate disarms it in a `finally` after the graph's resume, whether the start
    went, failed or raised. It's in memory only: a new process has nothing armed.

    `build` is what makes the armed client for that one start: `scripts/print_job.py`'s, which
    calls `live_printer(armed=…)`. `src/` never does. None, and nothing here can arm.
    """

    def __init__(self, build: Callable[[Armed], PrinterClient] | None) -> None:
        self._build = build
        self._lock = threading.Lock()
        self._armed: Armed | None = None

    @property
    def can_arm(self) -> bool:
        """Whether this process can start a print from a phone's confirm at all."""
        return self._build is not None

    def arm(self, armed: Armed) -> None:
        """Arm `armed.job_id` for one start. Only the gate calls this, after its checks.

        Raises:
            RuntimeError: If this process can't arm, or another start is already armed.
        """
        if self._build is None:
            raise RuntimeError("this run can't arm from a phone")
        if armed.how != "phone":
            raise RuntimeError("only a phone's confirm arms one start here")
        with self._lock:
            if self._armed is not None:
                raise RuntimeError(f"a start is already armed, for job {self._armed.job_id}")
            self._armed = armed

    def armed(self) -> Armed | None:
        """What's armed now, if anything. Read-only."""
        with self._lock:
            return self._armed

    def take(self, job_id: str) -> tuple[PrinterClient, Armed] | None:
        """The armed client for `job_id`'s one start, and who armed it; or None if it isn't
        armed. Once: it's disarmed as it's taken, whatever comes of the start."""
        with self._lock:
            armed, self._armed = self._armed, None
        if armed is None or armed.job_id != job_id or self._build is None:
            return None
        spendable = armed.model_copy(update={"token": secrets.token_hex(16)})
        with _spendable_lock:
            _spendable[spendable.token] = spendable
        try:
            return self._build(spendable), spendable
        finally:
            # Built or not, the token can't be spent again.
            with _spendable_lock:
                _spendable.pop(spendable.token, None)

    def disarm(self) -> None:
        """Nothing armed, whatever was. Idempotent."""
        with self._lock:
            self._armed = None
