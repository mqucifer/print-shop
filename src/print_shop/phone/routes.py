"""Every route the phone's page answers, in one table (mqucifer/print-shop#214,
mqucifer/print-shop#221).

The server dispatches through this table and nothing else, so a route that isn't here isn't
answered, and a test reaches each one. One route arms: `POST /start`, #214's confirm. Every
other route reads, or calls one of the queue's or the card's own actions, and none of them
reaches the gate.

Every route but pairing needs a phone that's signed in and on the home Wi-Fi. Every POST needs
the page's own `Host` and `Origin` too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ARMS", "ROUTES", "Route", "route_for"]

#: A job's or a run's id in a path: a uuid, or any plain name, and nothing a path could climb with.
_ID = r"(?P<id>[A-Za-z0-9_-]{1,100})"


@dataclass(frozen=True)
class Route:
    method: str
    #: The path, matched whole.
    path: str
    #: The handler's method that answers it.
    name: str
    #: Whether it arms: only #214's Start.
    arms: bool = False
    #: Whether it's answered signed out, from the home Wi-Fi: only pairing.
    signed_out: bool = False

    def match(self, path: str) -> dict[str, str] | None:
        found = re.fullmatch(self.path, path)
        return found.groupdict() if found is not None else None


ROUTES: tuple[Route, ...] = (
    # Main: the queue (mqucifer/print-shop#221).
    Route("GET", r"/", "main"),
    # Start (mqucifer/print-shop#214): the only route that arms.
    Route("GET", r"/start", "start_screen"),
    Route("POST", r"/start", "start", arms=True),
    Route("GET", r"/pair", "pair_screen", signed_out=True),
    Route("POST", r"/pair", "pair", signed_out=True),
    # A job in the queue, and the queue's own actions on it.
    Route("GET", rf"/job/{_ID}", "job"),
    Route("GET", rf"/job/{_ID}/remove", "remove_screen"),
    Route("POST", rf"/job/{_ID}/(?P<verb>hold|release|first|remove)", "act"),
    # Print it in another spool: the queue's own move.
    Route("POST", rf"/job/{_ID}/spool", "move"),
    # Filaments: what's loaded and on the shelf, and the stock's own load.
    Route("GET", r"/filaments", "filaments"),
    Route("POST", r"/filaments/load", "load"),
    # What the job needs from the person: the terminal's own screens, answered from the phone
    # (`session.py`), and the queue's own `give` for a look and the mesh gate.
    Route("GET", rf"/job/{_ID}/questions", "questions"),
    Route("POST", rf"/job/{_ID}/questions", "answer"),
    Route("GET", rf"/job/{_ID}/review", "review"),
    Route("POST", rf"/job/{_ID}/review", "reviewed"),
    Route("GET", rf"/job/{_ID}/review/change", "change"),
    Route("POST", rf"/job/{_ID}/reply", "reply"),
    Route("POST", rf"/job/{_ID}/go", "go"),
    Route("GET", rf"/job/{_ID}/look", "look"),
    Route("POST", rf"/job/{_ID}/look", "looked"),
    Route("GET", rf"/job/{_ID}/mesh", "mesh"),
    Route("POST", rf"/job/{_ID}/mesh", "meshed"),
    # What the slice shows, for a job that's prepared and for a finished run, a failed one too.
    Route("GET", rf"/job/{_ID}/slice", "job_slice"),
    Route("GET", rf"/run/{_ID}/slice", "run_slice"),
    # Finished: the runs, a run, and its outcome by the card's own Record.
    Route("GET", r"/runs", "runs"),
    Route("GET", rf"/run/{_ID}", "run"),
    Route("POST", rf"/run/{_ID}/outcome", "outcome"),
    # Print again, as it was or with a change: the terminal's `again`. It only queues.
    Route("GET", rf"/run/{_ID}/again", "again_screen"),
    Route("POST", rf"/run/{_ID}/again", "again"),
)

#: The routes that arm: #214's Start, alone.
ARMS = tuple(r for r in ROUTES if r.arms)


def route_for(method: str, path: str) -> tuple[Route, dict[str, str]] | None | str:
    """The route for this request and what its path holds; or the methods its path takes, as
    "GET, POST", where it's there for another method; or None where nothing is."""
    allowed = []
    for route in ROUTES:
        found = route.match(path)
        if found is None:
            continue
        if route.method == method:
            return route, found
        allowed.append(route.method)
    return ", ".join(sorted(set(allowed))) if allowed else None
