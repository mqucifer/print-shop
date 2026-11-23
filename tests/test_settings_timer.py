"""The model's settings wait, shown live under the review's prompt (mqucifer/print-shop#242).

The Sponsor, 2026-11-23: "would be good if this was showing that theres another timer like the
previous question step". The review's terminal runs in a child on a pseudo-terminal, as a person
has it, with a made-up model that answers after a few seconds: the timer ticks on the line above
the prompt, and what's typed meanwhile is read as it was typed. Nothing reaches a model.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEADLINE_S = 40.0

CHILD = """
import sys, threading, time
sys.path[:0] = [{root!r}, {src!r}]
from print_shop.setup.proposal import Change, Ending, Proposal, Source, Suggested
from print_shop.setup.review import ReviewRequest, cli_reviewer
from print_shop.setup.suggest import Suggesting
from tests.test_review import _request

def find(stop):
    stop.wait(3.2)
    return Suggested(pose=(0.0, 0.0, 0.0), ending=Ending.CAME, proposed=(Change(
        key="top_shell_layers", preset="4", value="5", reason="made up", source=Source.MODEL),))

work = Suggesting(find, (0.0, 0.0, 0.0))
base = _request(Proposal())
request = ReviewRequest(**{{**{{n: getattr(base, n) for n in base.__dataclass_fields__}},
                          "suggested": work.now(), "suggesting": work}})
review = cli_reviewer()(request)
print("RESULT", repr([(c.key, c.value, c.reason) for c in review.corrections]), flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a pseudo-terminal")
def test_the_settings_wait_ticks_above_the_prompt_and_what_is_typed_stays_put() -> None:
    pid, fd = pty.fork()
    if pid == 0:  # the child: the review, on the terminal
        os.chdir(ROOT)
        code = CHILD.format(root=str(ROOT), src=str(ROOT / "src"))
        os.execv(sys.executable, [sys.executable, "-B", "-c", code])
    seen = ""
    began = time.monotonic()

    def read_until(pattern: str, after: int = 0) -> re.Match[str]:
        nonlocal seen
        while time.monotonic() - began < DEADLINE_S:
            found = re.compile(pattern).search(seen, after)
            if found:
                return found
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    seen += os.read(fd, 65536).decode(errors="replace")
                except OSError:
                    break
        raise AssertionError(f"never saw {pattern!r} in:\n{seen[-3000:]}")

    try:
        first = read_until(r"waiting for the model's settings: (\d+) s\r?\n")
        read_until(r"> ", first.end())
        os.write(fd, b"wall_loops=3 bec")
        typed_at = len(seen)
        later = read_until(r"\x1b7\x1b\[1A\r\x1b\[2K  waiting for the model's settings: (\d+) s"
                           r"\x1b8", typed_at)
        assert int(later.group(1)) > int(first.group(1)), "it ticks"
        arrived = read_until(r"came after \d+ s\. Enter adds it to the list "
                             r"\(what you've typed is kept\)\.", later.end())
        assert "\x1b7\x1b[1A\r\x1b[2K\x1b8" in seen[later.end():arrived.start()], (
            "its line is cleared as the arrival is said")
        os.write(fd, b"ause it's decorative\n")
        result = read_until(r"RESULT ([^\r\n]*)\r?\n", arrived.end())
        assert result.group(1) == "[('wall_loops', '3', \"it's decorative\")]", (
            "what was typed across the ticks and the arrival is read as typed")
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
