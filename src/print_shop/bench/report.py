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
GOOD = {"OK", "CLEAR", "TOUCHING", "HELD"}
#: Both methods agree, and what they agree on is a problem.
BAD = {
    "FAIL",
    "INTERFERE",
    "TOO CLOSE",
    "TOO LOOSE",
    "NO CHANGE",
    "PARTIAL",
    "NOT HELD",
    "HELD BY A SEPARATE BODY",
    "BLOCKED",
}
#: There's no answer to rely on: the methods differ, one couldn't run, or what would give first
#: has no figure to be read against (`load`'s `UNREAD`, mqucifer/print-shop#156).
UNSURE = {"DISAGREE", "NO ANSWER", "UNREAD"}
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
        # The cores it was pinned to, where it was (`--cpuset-cpus`, mqucifer/print-shop#373), and
        # the host that ran it, where it was a study's job: "here", or a remote's name.
        "cpus_pinned": read("/sys/fs/cgroup/cpuset.cpus") or None,
        "host": os.environ.get("BENCH_HOST"),
        "memory_max": read("/sys/fs/cgroup/memory.max"),
        # The most the container has held so far, in bytes (mqucifer/print-shop#295): read as
        # the reply is saved, so the whole command's peak, where the kernel keeps one.
        "memory_peak": read("/sys/fs/cgroup/memory.peak"),
        "user": os.getuid(),
        # The limit the host gave the command, in s, as it stood at the end: `load` in two
        # passes takes up a longer one as it runs (mqucifer/print-shop#325). None where no host
        # said.
        "limit_s": _seconds(os.environ.get("BENCH_TIMEOUT")),
        # Where the read met the memory of the container it ran in first, and was offered this
        # host's larger memory (mqucifer/print-shop#380): what it met there, in bytes.
        "offered": offered(os.environ.get("BENCH_OFFERED")),
    }


def offered(text: str | None) -> dict[str, Any] | None:
    """What a command offered a host with more memory met where it ran first, as the host told
    it (`job.Met.told`): `host`, and its `limit`, `peak`, `need` and `room` in bytes, each None
    where unsaid, whether the kernel `killed` it, and the step it was `at`."""
    if not text:
        return None
    fields, _, at = text.partition(" at ")
    found = dict(f.split("=", 1) for f in fields.split() if "=" in f)
    numbers = {k: int(found[k]) if found.get(k, "-").isdigit() else None
               for k in ("limit", "peak", "need", "room")}
    return {"host": found.get("host"), **numbers, "killed": found.get("killed") == "1",
            "at": at.strip()}


def _gib(count: Any) -> str:
    return f"{int(count) / 2**30:.1f} GiB" if str(count).isdigit() else "unknown"


def offered_said(ran: dict[str, Any]) -> str:
    """The line a reply read on a host with more memory says, naming the host, its limit, and the
    need and room on both (mqucifer/print-shop#380); or nothing."""
    met = ran.get("offered")
    if not met:
        return ""
    how = (
        "the kernel killed it, most likely at the memory"
        if met["killed"]
        else f"it needed {_gib(met['need'])} with {_gib(met['room'])} room"
        if met["need"] is not None or met["room"] is not None
        else "the kernel stopped a process of it for memory, by the container's own count"
    )
    return (
        f"\noffered: read on {ran.get('host')} in its {_gib(ran.get('memory_max'))}, the same "
        f"command with the same checks, as on {met['host']} its read met its container's "
        f"{_gib(met['limit'])} at {met['at']}: {how}, at most {_gib(met['peak'])} held. On "
        f"{ran.get('host')} it held at most {_gib(ran.get('memory_peak'))} of its "
        f"{_gib(ran.get('memory_max'))}"
    )


def _seconds(text: str | None) -> int | None:
    return int(text) if text is not None and text.isdigit() else None


class NoAnswer(Exception):
    """A method couldn't run, so the command can't say. Its message is the reason. It's here, not
    with the shapes, so the second image's commands raise it with no CadQuery to import."""


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

    def again(self) -> None:
        """The answer started again from nothing, as when a command reads the same thing a
        second way (mqucifer/print-shop#295), keeping when it started."""
        started = self.started
        self.__init__(self.command, self.argv)  # type: ignore[misc]
        self.started = started

    def fact(self, name: str, value: Any, unit: str = "", how: str = "") -> None:
        """A line of the report and a number in its saved form."""
        shown = f"{value:.4f}" if isinstance(value, float) else str(_shown(value))
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
            + f"\nran: {seconds} s"
            + (f" of its {ran['limit_s']} s limit" if ran["limit_s"] is not None else "")
            + f", image {str(ran['image_id'])[:19]}, code {ran['code']}, "
            + f"cpu.max {ran['cpu_max']}, memory.max {ran['memory_max']}, "
            + (f"memory.peak {ran['memory_peak']}, " if ran.get("memory_peak") else "")
            + f"network up: {','.join(ran['network_up']) or 'none'}, "
            + f"routes {ran['network_routes']}"
            + offered_said(ran)
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


def _shown(value: Any) -> Any:
    """A figure as a line says it: floats to 4 places, in a dict or a list too. The saved numbers
    keep each figure as it is (`limits`)."""
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _shown(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shown(v) for v in value]
    return value
