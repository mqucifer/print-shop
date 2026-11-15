"""A job's terminal output, kept in its folder as `job.log` (mqucifer/print-shop#139).

The Sponsor, 2026-11-15: "log this where you can see etc. Hard to read via the command line all
the time." `scripts/print_job.py` shows a job as it goes (the questions, the proposal, the slice
reading, the review), and until now that was gone once it scrolled past. So what the terminal
shows for a job is also written to `jobs/<id>/job.log`, beside its G-code, where the print desk
(`print_shop.desk`) and the operator read it:
- what's printed, to stdout and stderr, and the log records the terminal shows;
- each answer typed at a prompt, after the prompt, as it looked on the screen.

**No secret is written.** The log holds only what the terminal shows, and before a line is
written every value of a setting or environment variable whose name says it's a key, token,
password or secret is replaced with `[redacted]`. A line is redacted whole, so a value split
across two writes is still found.

The job's id is known only once it's submitted, so what's shown before (the inventory, the
profiles chosen) is held and written first, when the folder is known.
"""

from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from print_shop.env import load_env

__all__ = ["LOG_NAME", "REDACTED", "JobLog", "secrets"]

#: The log's name in the job's folder.
LOG_NAME = "job.log"

REDACTED = "[redacted]"

#: A setting's name that says its value is a secret.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS$|CREDENTIAL|AUTH", re.I)

#: Shorter values are left alone: redacting every "true" would garble the log, and no key, token
#: or password the settings hold is that short.
_SHORTEST = 6


def secrets(settings: Path) -> list[str]:
    """Every value the log must never hold: the settings file's and the environment's, by
    name. Longest first, so a secret holding another is redacted whole."""
    named = {**os.environ, **load_env(settings)}
    found = {v for k, v in named.items() if _SECRET_NAME.search(k) and len(v) >= _SHORTEST}
    return sorted(found, key=len, reverse=True)


class JobLog:
    """One job's terminal output, redacted, held until its folder is known."""

    def __init__(self, secrets: Iterable[str]) -> None:
        self._secrets = sorted({s for s in secrets if len(s) >= _SHORTEST}, key=len,
                               reverse=True)
        self._lock = threading.Lock()
        self._held: list[str] = []
        self._partial = ""
        self._file: TextIO | None = None
        self._closed = False
        self._quiet = threading.local()
        self.path: Path | None = None

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    def write(self, text: str) -> None:
        """Text as the terminal shows it. Written a whole line at a time."""
        if getattr(self._quiet, "on", False):
            return
        with self._lock:
            if self._closed:
                return
            lines = (self._partial + text).split("\n")
            self._partial = lines.pop()
            for line in lines:
                self._line(line)

    def _line(self, line: str) -> None:
        line = self.redact(line) + "\n"
        if self._file is None:
            self._held.append(line)
        else:
            self._file.write(line)
            self._file.flush()

    def open(self, path: Path) -> None:
        """Start writing to `path`, what was held first."""
        with self._lock:
            if self._closed or self._file is not None:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("a", encoding="utf-8")
            self.path = path
            self._file.writelines(self._held)
            self._file.flush()
            self._held.clear()

    def close(self) -> None:
        """Write what's left of the last line, and stop. Held text with no folder is dropped:
        no job was submitted, so there's no job to keep it for."""
        with self._lock:
            if self._closed:
                return
            if self._partial:
                self._line(self._partial)
                self._partial = ""
            self._closed = True
            if self._file is not None:
                self._file.close()

    def reader(self, read: Callable[[str], str]) -> Callable[[str], str]:
        """`read` (`input`), with the prompt and the answer typed kept as the screen shows
        them. The prompt isn't kept twice where `input` writes it through stdout."""

        def logged(prompt: str) -> str:
            self._quiet.on = True
            try:
                answer = read(prompt)
            finally:
                self._quiet.on = False
            self.write(f"{prompt}{answer}\n")
            return answer

        return logged

    @contextmanager
    def capturing(self) -> Iterator[None]:
        """stdout, stderr and log records also go here, while it's open."""
        out, err = sys.stdout, sys.stderr
        handler = _Handler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root = logging.getLogger()
        sys.stdout, sys.stderr = _Tee(out, self), _Tee(err, self)
        root.addHandler(handler)
        try:
            yield
        finally:
            root.removeHandler(handler)
            sys.stdout, sys.stderr = out, err


class _Tee(io.TextIOBase):
    """A stream that writes to the terminal and the log. It is the terminal's for everything
    else, its file descriptor included, so `input()` still edits lines as it did."""

    def __init__(self, stream: TextIO, log: JobLog) -> None:
        self._stream = stream
        self._log = log

    def write(self, text: str) -> int:
        written = self._stream.write(text)
        self._log.write(text)
        return written

    def flush(self) -> None:
        self._stream.flush()

    def fileno(self) -> int:
        return self._stream.fileno()

    def isatty(self) -> bool:
        return self._stream.isatty()

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._stream.encoding


class _Handler(logging.Handler):
    """Log records into the log. The terminal's own handler still shows them there."""

    def __init__(self, log: JobLog) -> None:
        super().__init__(logging.INFO)
        self._log = log

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._log.write(self.format(record) + "\n")
        except Exception:  # noqa: BLE001 - a log line never stops a job
            self.handleError(record)
