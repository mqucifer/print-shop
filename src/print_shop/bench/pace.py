"""How long a reading of many pairs will take, and the longer limit it takes up for it
(mqucifer/print-shop#354).

A whole assembly's `clearance`, its pairs read exactly at several seconds a pair, ran past the
bench's 600 s and was stopped, saying only that. The host offers it the hour, as it offers `load`
(`host.offered`); it takes that up only where its pairs read exactly so far, read in an order
spread through them, project past its limit. It then states its own limit: the projection with
room, `ROOM`, capped at what was offered, so a small reading keeps 600 s and a large one isn't
given more than it needs. It stops itself before whichever limit it's under, by `margin`, so its
reply says where it was. The handshake is `load`'s: the same environment and stderr line, which
the host and the harness's box read.

Standard library only, so the rule is tested with no CAD.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

#: The container's limit as the host gives it, the longer one it offers, and the line on stderr
#: that takes that up: the same as `host.TIMEOUT`'s variable, `host.OFFER` and `host.TAKEN`, which
#: run outside the image, and as `load_parts` uses them.
TIMEOUT = "BENCH_TIMEOUT"
OFFER = "BENCH_TIMEOUT_TWO_PASSES"
TAKEN = "bench: two passes take the longer limit"
#: The limit stated is the projected time times this.
ROOM = 1.5
#: How long before a limit it stops itself, in s, at most: time to write its reply.
MARGIN = 20.0


def _seconds(name: str) -> float | None:
    value = os.environ.get(name, "")
    return float(value) if value.isdigit() else None


@dataclass
class Pace:
    """A reading's limit as it goes: the host's, then any it states having taken the offer."""

    #: The limit it's under now, in s from when the command began; None where none is given.
    limit: float | None
    #: The longer limit offered, which it may take up; None where none is.
    offer: float | None
    #: Where it took the offer: its projection then, in s, and how many pairs it had read.
    took: tuple[float, int] | None = None

    @classmethod
    def from_env(cls) -> Pace:
        return cls(_seconds(TIMEOUT), _seconds(OFFER))

    def margin(self, limit: float) -> float:
        return min(MARGIN, 0.1 * limit)

    def stated(self, projected: float) -> float:
        """The limit stated for a projection: with room, never under the limit it was under,
        and capped at the offer."""
        assert self.offer is not None
        wanted = math.ceil(projected * ROOM + self.margin(self.offer))
        return float(min(self.offer, max(wanted, self.limit or 0.0)))

    def after(self, elapsed: float, done: int, spent: float, left: int) -> str | None:
        """After `done` pairs read exactly in `spent` s, `elapsed` s into the command, with
        `left` to go: "take" where it takes up the offer, stating its limit; "raise" where it
        has, and states a higher one, up to the offer; "stop" where it's within its margin of
        its limit; else None."""
        if self.limit is None or done == 0:
            return None
        projected = elapsed + left * spent / done
        said = None
        if self.offer is not None and projected > self.limit - self.margin(self.limit):
            higher = self.stated(projected)
            if higher > self.limit:
                said = "take" if self.took is None else "raise"
                if self.took is None:
                    self.took = (projected, done)
                    print(TAKEN, file=sys.stderr, flush=True)
                self.limit = higher
                # The `ran:` line's limit is the one it's under from here on.
                os.environ[TIMEOUT] = str(int(higher))
        # A reading with nothing left to read is done: it's never thrown away for its margin.
        if left > 0 and elapsed > self.limit - self.margin(self.limit):
            return "stop"
        return said


def spread(count: int) -> list[int]:
    """0 to `count` - 1 in an order spread through them, each step about 0.618 of the way on, so
    the first read are like the rest: a ring's first pairs read cheaper than its last."""
    if count < 3:
        return list(range(count))
    step = max(1, round(count * 0.6180339887))
    while math.gcd(step, count) != 1:
        step += 1
    return [(k * step) % count for k in range(count)]
