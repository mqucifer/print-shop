"""What every command returns: a verdict, its numbers, its second check, and where it's saved.

There's no silent success. A verdict that claims an answer with no second check behind it
becomes NO ANSWER, and every report is written to a file, whatever it says.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

#: The measured answer stands: both methods agree and no check failed.
GOOD = {"OK", "CLEAR", "TOUCHING"}
#: Both methods agree, and what they agree on is a problem.
BAD = {"FAIL", "INTERFERE", "TOO CLOSE", "TOO LOOSE", "NO CHANGE", "PARTIAL"}
#: There's no answer to rely on: the methods differ, or one couldn't run.
UNSURE = {"DISAGREE", "NO ANSWER"}
EXIT = {**dict.fromkeys(GOOD, 0), **dict.fromkeys(BAD, 1), **dict.fromkeys(UNSURE, 2)}


def how_it_ran() -> dict[str, Any]:
    """The limits as the container sees them, read and not assumed."""

    def read(path: str) -> str | None:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    # A namespace with no network still lists stub devices, all down: what counts is which are
    # up, and whether there's a route out of them.
    up = []
    try:
        for device in sorted(Path("/sys/class/net").iterdir()):
            flags = read(str(device / "flags"))
            if flags is not None and int(flags, 16) & 1:
                up.append(device.name)
    except OSError:
        up = ["unreadable"]
    routes = read("/proc/net/route")
    return {
        "image": os.environ.get("BENCH_IMAGE"),
        "image_id": os.environ.get("BENCH_IMAGE_ID"),
        "code": os.environ.get("BENCH_CODE"),
        "network_up": up,
        "network_routes": len(routes.splitlines()) - 1 if routes else None,
        "cpu_max": read("/sys/fs/cgroup/cpu.max"),
        "memory_max": read("/sys/fs/cgroup/memory.max"),
        "user": os.getuid(),
    }


class Report:
    """One command's answer, built up as it runs."""

    def __init__(self, command: str, argv: list[str]) -> None:
        self.command = command
        self.argv = argv
        self.word = "NO ANSWER"
        self.why = "the command didn't reach a verdict"
        self.facts: list[str] = []
        self.numbers: dict[str, Any] = {}
        self.second_check: list[str] = []
        self.notes: list[str] = []
        self.files: list[str] = []
        self.started = time.time()

    def fact(self, name: str, value: Any, unit: str = "", how: str = "") -> None:
        """A line of the report and a number in its saved form."""
        shown = f"{value:.4f}" if isinstance(value, float) else str(value)
        line = f"{name}: {shown}" + (f" {unit}" if unit else "") + (f" ({how})" if how else "")
        self.facts.append(line)
        self.numbers[name] = value

    def line(self, text: str) -> None:
        self.facts.append(text)

    def second(self, text: str) -> None:
        """What a different method found: every answer needs at least one of these."""
        self.second_check.append(text)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def wrote(self, path: str) -> None:
        self.files.append(path)

    def verdict(self, word: str, why: str) -> None:
        if word not in EXIT:
            raise ValueError(f"no such verdict: {word}")
        self.word, self.why = word, why

    @property
    def exit_code(self) -> int:
        return EXIT[self.word]

    def settle(self) -> None:
        """An answer with no second check behind it is no answer."""
        if self.word in GOOD | BAD and not self.second_check:
            self.notes.append(f"it said {self.word}: {self.why}")
            self.word, self.why = "NO ANSWER", "no second check ran"

    def body(self) -> str:
        lines = [f"verdict: {self.word}: {self.why}", *self.facts]
        lines += [f"second check: {s}" for s in self.second_check] or ["second check: none ran"]
        lines += [f"note: {n}" for n in self.notes]
        lines += [f"wrote: {f}" for f in self.files]
        return "\n".join(lines)

    def save(self, out: Path) -> Path:
        """Write the text and the numbers beside each other, and name them in the text."""
        self.settle()
        seconds = round(time.time() - self.started, 2)
        out.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        stem = f"{self.command}-{stamp}-{os.getpid()}"
        ran = how_it_ran()
        record = {
            "command": self.command,
            "argv": self.argv,
            "verdict": self.word,
            "why": self.why,
            "numbers": self.numbers,
            "second_check": self.second_check,
            "notes": self.notes,
            "files": self.files,
            "seconds": seconds,
            "ran": ran,
        }
        (out / f"{stem}.json").write_text(json.dumps(record, indent=1, default=_plain) + "\n")
        text = (
            self.body()
            + f"\nran: {seconds} s, image {str(ran['image_id'])[:19]}, code {ran['code']}, "
            + f"cpu.max {ran['cpu_max']}, memory.max {ran['memory_max']}, "
            + f"network up: {','.join(ran['network_up']) or 'none'}, "
            + f"routes {ran['network_routes']}"
            + f"\nsaved: {out / stem}.txt and .json\n"
        )
        (out / f"{stem}.txt").write_text(text)
        self.text = text
        return out / f"{stem}.json"


def _plain(value: Any) -> Any:
    """NumPy numbers, into what JSON holds."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
