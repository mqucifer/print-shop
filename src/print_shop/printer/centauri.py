"""The hardware boundary: put G-code on the printer and start it.

This is the one module in `src/` that can make something reach 250 °C, so it is
written to be boring and to refuse by default.

Three ideas shape it:

**A snapshot proposes, a validator disposes.** `preflight` is a pure function
over a typed `PrinterSnapshot` and returns a `Verdict` from
`src/core/contracts.py` — the same machinery the orientation node uses. Nothing
here decides anything from prose, and every refusal is a `Violation` carrying
the observed value, so a gate or a log can render exactly what was wrong.

**The ready set is a whitelist.** `READY_TO_START` names the three printer
states in which starting a job is safe. Every other code — printing, paused,
levelling, preheating, error, and any status Elegoo adds later — blocks. A
blacklist would treat an unrecognised state as permission.

**Arming is a deliberate act, never a default.** `enable_control` is False on
this client and on `pycentauri.Printer`, so a client built by accident can read
the printer and nothing else. `live_printer()` is the one function in this
project that builds an armed client, and it is the one occurrence of
`enable_control=True`; `tests/test_print_node.py` asserts both.

This was not the original design and the original design was wrong. `print_node`
used to fall back to building an armed client from the environment whenever the
caller passed no printer. On 2026-09-16 that turned `tests/test_daemon.py`,
which publishes a real MQTT approval, into a live one — and it started a real
print. Every guard in this file worked; the defect was that the *default* was
armed. A caller who wants hardware now has to say so.

Facts measured on the machine (firmware V1.1.46), so nobody has to
rediscover them:

* `attributes().RemainingMemory` is **bytes** — 5098209280 when checked.
  `disk_info()` raises on this printer; it is Centauri Carbon 2 only.
* An upload is not immediately visible in `list_files`. Confirming a file
  exists straight after uploading it gives a false negative, so `upload`
  retries before believing the file is absent.
* `list_files` returns the whole list and pages client-side.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pycentauri import Printer
from pycentauri.models import PrintStatus as SdcpPrintStatus
from pycentauri.models import Status
from pydantic import BaseModel

from print_shop.core.contracts import NoAutoFix, Verdict, Violation
from print_shop.env import SETTINGS_PATH, load_env

__all__ = [
    "ALLOW_HARDWARE_ENV",
    "READY_TO_START",
    "CentauriClient",
    "PreflightFailed",
    "PrinterClient",
    "PrinterConfig",
    "PrinterSnapshot",
    "live_printer",
    "preflight",
]

#: `PrintInfo.Status` codes on which starting a new print is safe. A whitelist:
#: anything not named here — PRINTING, PAUSED, AUTO_LEVELING, PREHEATING,
#: ERROR, or a code from firmware newer than this — means the machine is not
#: free, and an unknown state must never read as permission.
READY_TO_START: frozenset[int] = frozenset(
    {
        SdcpPrintStatus.IDLE,
        SdcpPrintStatus.STOPPED,
        SdcpPrintStatus.COMPLETED,
    }
)

#: Opt-in for a client that can move the machine. Read from `os.environ` only,
#: never from `.env`: a `.env` value is ambient configuration — set once,
#: forgotten, in force for every run thereafter — and that is the precise shape
#: of what went wrong on 2026-09-16. Arming has to be an act, not a setting.
ALLOW_HARDWARE_ENV = "PRINT_AGENT_ALLOW_HARDWARE"

#: Never fill the printer's storage to the last byte. A slice is tens of MB and
#: the machine has ~5 GB, so this costs nothing and keeps a wedged filesystem
#: off the list of things that can go wrong mid-print.
_FREE_SPACE_HEADROOM_BYTES = 16 * 1024 * 1024

#: An upload lands before it is listable. Measured once at 211 KB: absent
#: immediately, present seconds later.
_VISIBILITY_ATTEMPTS = 6
_VISIBILITY_DELAY_S = 2.0

#: How long to watch for the printer to leave the ready set after being told to
#: print. This was 30 s in the first version of this file, which was wrong in a
#: way the same commit documented elsewhere: after a stop, this machine spends
#: *minutes* cycling PREHEATING / HOMING / PREHEATING_COMPLETED. A start is no
#: faster — the bed alone takes a minute or two to reach 60 C.
#:
#: The failure that made it matter runs the wrong way round: too short a window
#: raises on the *success* path, so a human-approved print gets recorded as a
#: failed job while the nozzle heats. Five minutes is generous rather than
#: measured, because nobody has yet started a print on purpose (BACKLOG.md).
#: Measure it on the first deliberate start and replace this number.
_START_CONFIRM_TIMEOUT_S = 300.0


class PreflightFailed(RuntimeError):
    """Raised when the printer is not in a state that may be printed onto.

    Carries the `Verdict` so a caller can render every reason at once rather
    than the first one that tripped.
    """

    def __init__(self, verdict: Verdict) -> None:
        self.verdict = verdict
        reasons = "; ".join(
            f"{v.rule}: observed {v.observed!r}" for v in verdict.violations
        )
        super().__init__(f"printer preflight failed — {reasons}")


class PrinterSnapshot(BaseModel):
    """What the printer says about itself, as a typed object (rule 4).

    `print_status` is the SDCP `PrintInfo.Status` code. `active_filename` is
    None when no job holds a file, which is not the same as being ready —
    a printer in ERROR holds no file either.
    """

    model_config = {"frozen": True}

    print_status: int | None
    active_filename: str | None
    free_bytes: int
    machine: str = ""
    firmware: str = ""

    @property
    def ready_to_start(self) -> bool:
        return self.print_status in READY_TO_START and self.active_filename is None


class PrinterClient(Protocol):
    """What `print_node` needs from a printer.

    Deliberately three methods. The node does not get `pause`, `stop`, or
    anything else — emergency stop lives in Home Assistant and does not depend
    on this project's code, which is the arrangement worth keeping.
    """

    def snapshot(self) -> PrinterSnapshot:
        """Read status and storage. Never requires control."""
        ...

    def upload(self, local_path: Path) -> str:
        """Upload and confirm the file is listed. Returns its name on the printer."""
        ...

    def start(self, remote_name: str) -> PrinterSnapshot:
        """Start printing an already-uploaded file. Returns the state after starting."""
        ...


# --------------------------------------------------------------------------
# The validator
# --------------------------------------------------------------------------


def preflight(snapshot: PrinterSnapshot, gcode_path: Path) -> Verdict:
    """Decide whether this file may be sent to this printer, right now.

    Pure, and every violation is `NoAutoFix`: none of these is a value a
    corrector could nudge into range. A busy printer needs time, a full one
    needs a human to delete something, and a missing file needs a re-slice.
    """
    violations: list[Violation] = []

    if not gcode_path.is_file():
        violations.append(
            Violation(
                field="gcode_path",
                rule="gcode.present",
                observed=str(gcode_path),
                allowed=NoAutoFix("the sliced G-code is not on disk"),
                detail="nothing to upload; the job must be sliced again",
            )
        )
        size = 0
    else:
        size = gcode_path.stat().st_size

    if not snapshot.ready_to_start:
        violations.append(
            Violation(
                field="print_status",
                rule="printer.ready",
                observed=snapshot.print_status,
                allowed=NoAutoFix("the printer is not free to start a new job"),
                detail=(
                    f"status {snapshot.print_status} with "
                    f"active file {snapshot.active_filename!r}; "
                    f"ready states are {sorted(READY_TO_START)}"
                ),
            )
        )

    required = size + _FREE_SPACE_HEADROOM_BYTES
    if size and snapshot.free_bytes < required:
        violations.append(
            Violation(
                field="free_bytes",
                rule="printer.free_space",
                observed=snapshot.free_bytes,
                allowed=NoAutoFix("not enough room on the printer for this file"),
                detail=(
                    f"{size} bytes of G-code plus "
                    f"{_FREE_SPACE_HEADROOM_BYTES} headroom needs {required}, "
                    f"but only {snapshot.free_bytes} is free"
                ),
            )
        )

    return Verdict(tuple(violations))


# --------------------------------------------------------------------------
# The real client
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrinterConfig:
    """Where the printer is. Runtime configuration, so it comes from the Mac's settings.

    `mainboard_id` is not optional in practice. Without it the client waits for
    the firmware's spontaneous Attributes push before it will send anything,
    and in paused or error states that push never comes — which is exactly when
    a status read matters most.
    """

    host: str
    mainboard_id: str
    connect_timeout_s: float = 15.0
    request_timeout_s: float = 20.0
    upload_timeout_s: float = 300.0

    @classmethod
    def from_env(cls, path: Path | None = None) -> PrinterConfig:
        """Read the Mac's settings (`~/.config/print-shop/.env`) with `load_env`.

        A value already exported in the environment wins, as in a shell. The
        file is parsed, never sourced, and nothing is exported.
        """
        env = load_env(path or SETTINGS_PATH)

        def get(key: str, default: str = "") -> str:
            return env.get(key, default)

        host = get("PRINTER_HOST")
        mainboard_id = get("PRINTER_MAINBOARD_ID")
        if not host or not mainboard_id:
            raise RuntimeError(
                "PRINTER_HOST and PRINTER_MAINBOARD_ID must be set in the Mac's "
                "settings (~/.config/print-shop/.env) before the print node can "
                "reach the printer"
            )
        return cls(host=host, mainboard_id=mainboard_id)


class CentauriClient:
    """`PrinterClient` over SDCP, for an Elegoo Centauri Carbon 1.

    Each call opens a connection, does one thing, and closes it. A print job
    makes three of them, so the cost is irrelevant next to a multi-hour print,
    and it buys away every bug class that comes with a socket held across a
    checkpoint.

    Synchronous on purpose: `pycentauri` is asyncio, LangGraph nodes are not,
    and the daemon runs the graph on a worker thread with no event loop. Each
    method is therefore an `asyncio.run` around one short coroutine. Calling
    these from inside a running loop will raise, which is the correct outcome
    rather than a silent deadlock.
    """

    def __init__(
        self,
        config: PrinterConfig,
        *,
        enable_control: bool = False,
        auto_leveling: bool = False,
    ) -> None:
        """`auto_leveling` is off by default, which is not pycentauri's default.

        `start_print` sets `Calibration_switch`, and with it on the machine runs
        a full levelling sweep before the first layer — fans to 100%, several
        minutes of noise. That is a decision about the *machine*, not about the
        job, and it should not arrive as a side effect of printing something.
        Turn it on deliberately, after moving the bed or changing a nozzle.
        """
        self.config = config
        self.enable_control = enable_control
        self.auto_leveling = auto_leveling

    # --- the protocol -----------------------------------------------------

    def snapshot(self) -> PrinterSnapshot:
        return asyncio.run(self._snapshot())

    def upload(self, local_path: Path) -> str:
        return asyncio.run(self._upload(Path(local_path)))

    def start(self, remote_name: str) -> PrinterSnapshot:
        return asyncio.run(self._start(remote_name))

    # --- coroutines -------------------------------------------------------

    async def _connect(self) -> Printer:
        return await asyncio.wait_for(
            Printer.connect(
                self.config.host,
                mainboard_id=self.config.mainboard_id,
                enable_control=self.enable_control,
            ),
            timeout=self.config.connect_timeout_s,
        )

    async def _snapshot(self) -> PrinterSnapshot:
        printer = await self._connect()
        try:
            return await self._read_snapshot(printer)
        finally:
            await printer.close()

    async def _read_snapshot(self, printer: Printer) -> PrinterSnapshot:
        status = await asyncio.wait_for(
            printer.status(), timeout=self.config.request_timeout_s
        )
        return await self._snapshot_from(printer, status)

    async def _snapshot_from(self, printer: Printer, status: Status) -> PrinterSnapshot:
        attributes = await asyncio.wait_for(
            printer.attributes(), timeout=self.config.request_timeout_s
        )
        remaining = attributes.raw.get("RemainingMemory")
        return PrinterSnapshot(
            print_status=status.print_status,
            active_filename=status.active_filename,
            # Bytes. Absent means unknown, and unknown must not read as room:
            # zero makes the free-space check refuse rather than guess.
            free_bytes=int(remaining) if isinstance(remaining, int | str) else 0,
            machine=attributes.machine_name or "",
            firmware=attributes.firmware_version or "",
        )

    async def _upload(self, local_path: Path) -> str:
        printer = await self._connect()
        try:
            remote = await printer.upload_file(
                local_path, timeout=self.config.upload_timeout_s
            )
            if not await self._wait_until_listed(printer, remote):
                raise RuntimeError(
                    f"uploaded {remote!r} but the printer never listed it after "
                    f"{_VISIBILITY_ATTEMPTS} checks; not starting a print of a "
                    f"file that may not be there"
                )
            return remote
        finally:
            await printer.close()

    async def _wait_until_listed(self, printer: Printer, remote_name: str) -> bool:
        """Poll `list_files` until the upload appears.

        An upload is not immediately visible, so a single check would be a
        false negative. `limit=0` asks for the whole list — the firmware
        returns everything and pycentauri pages it client-side, so a file can
        sit past the first page of 100.
        """
        wanted = remote_name.rsplit("/", 1)[-1]
        for attempt in range(_VISIBILITY_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_VISIBILITY_DELAY_S)
            listing = await asyncio.wait_for(
                printer.list_files(limit=0), timeout=self.config.request_timeout_s
            )
            names = {
                str(f.get("filename", "")).rsplit("/", 1)[-1]
                for f in listing.get("file_list", [])
            }
            if wanted in names:
                return True
        return False

    async def _start(self, remote_name: str) -> PrinterSnapshot:
        printer = await self._connect()
        try:
            response = await asyncio.wait_for(
                printer.start_print(remote_name, auto_leveling=self.auto_leveling),
                timeout=self.config.request_timeout_s,
            )
            ack = ((response.inner or {}).get("Data") or {}).get("Ack")
            if ack not in (None, 0):
                raise RuntimeError(
                    f"printer refused to start {remote_name!r}: Ack={ack!r}"
                )
            return await self._confirm_started(printer, remote_name)
        finally:
            await printer.close()

    async def _confirm_started(
        self, printer: Printer, remote_name: str
    ) -> PrinterSnapshot:
        """Watch until the printer leaves the ready set, or give up saying so.

        An acknowledged command is not a running print. Reporting success
        without looking is the failure `PROJECT.md` learning 2 is about — the
        previous implementation set `print_status = "done"` unconditionally.

        `watch()` rather than repeated `status()` calls: `status()` returns the
        cached last push and would answer "idle" forever. `watch()` is the
        supported way to see the state change, and it re-requests a frame each
        push period if the firmware's push scheduler wedges.
        """

        async def until_busy() -> Status:
            async for status in printer.watch():
                if (
                    status.print_status not in READY_TO_START
                    or status.active_filename is not None
                ):
                    return status
            raise RuntimeError("the printer's status stream ended unexpectedly")

        def is_ours(status: Status) -> bool:
            """Whether the job the printer picked up is the one we sent.

            Leaving the ready set is not the same as printing *our* file — the
            machine could have been started from its own screen between our
            upload and our start. When the firmware names the active file we
            check it; when it does not (it reports an empty filename in some
            frames, and we have never watched a deliberate start) we accept the
            transition rather than fail a print that is probably ours. Tighten
            this once someone has watched a real start and knows which frames
            carry the name.
            """
            active = status.active_filename
            return active is None or active.rsplit("/", 1)[-1] == remote_name

        try:
            status = await asyncio.wait_for(
                until_busy(), timeout=_START_CONFIRM_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise RuntimeError(
                f"told the printer to start {remote_name!r} and it acknowledged, "
                f"but it never left a ready state within "
                f"{_START_CONFIRM_TIMEOUT_S:.0f}s. Check the machine before "
                f"retrying — it may or may not be printing."
            ) from exc

        if not is_ours(status):
            raise RuntimeError(
                f"started {remote_name!r} but the printer reports "
                f"{status.active_filename!r} as its active file. Not reporting this "
                f"job as printing — "
                f"something else was started on the machine."
            )

        return await self._snapshot_from(printer, status)


def live_printer(*, auto_leveling: bool = False) -> CentauriClient:
    """Build a client that can actually move the machine.

    The only armed constructor in this project, and the only occurrence of
    `enable_control=True`. It is a named function rather than a default so that
    arming hardware is something a caller does on purpose, in a place a reader
    can find by grepping one string.

    Refuses unless `PRINT_AGENT_ALLOW_HARDWARE=1` is in the process
    environment. Host and mainboard id come from `.env` because they are
    addresses and never change; the arming flag does not, because it is a
    decision and it should have to be made again every time.

    Nothing in `src/` calls this. The process that runs a real printer does —
    see `Daemon(printer=...)`. Tests pass a double, and `tests/conftest.py`
    refuses armed connections outright for the whole session.

    Raises:
        RuntimeError: If the environment has not opted in.
    """
    if os.environ.get(ALLOW_HARDWARE_ENV) != "1":
        raise RuntimeError(
            f"refusing to build an armed printer client: {ALLOW_HARDWARE_ENV} is "
            f"not set to '1' in the environment. Commanding a machine that "
            f"reaches 250 C is a deliberate act — run the daemon with "
            f"{ALLOW_HARDWARE_ENV}=1 when you mean it."
        )
    return CentauriClient(
        PrinterConfig.from_env(), enable_control=True, auto_leveling=auto_leveling
    )
