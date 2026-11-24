"""The phone's page: the standard library's HTTP server, on the Mac's home address only
(mqucifer/print-shop#214, mqucifer/print-shop#221).

Every route is in `routes.py`'s table, and the server answers through it alone. Besides Start's
and pairing's below, the screens of `screens.py`: Main (`GET /`, the queue), a job's own page and
the queue's actions on it, and Finished with the card's Record. Each is shown or done only for a
signed-in phone on the home Wi-Fi; signed out, only how to sign in, and from away, "You're away
from home", with nothing shown or done.

Start's routes:
- `GET /start`: signed in, the job ready to start, what Start checks, and the bed's question,
  with a new confirm; from anywhere but a phone on the home Wi-Fi, the job and "You're away from
  home"; signed out, only how to sign in.
- `POST /start`: the confirm and the bed's answer. Checked in this order, each refusing in words
  with nothing armed: the page's own `Host` and `Origin`; signed in; from a phone on the home
  Wi-Fi (`home.py`); the confirm, spent now whatever follows, from the same phone and address,
  in time (`confirm.py`); the bed ticked. Then it goes to the gate as a Start
  (`Daemon.start_from_phone`), which checks the gate's word and the G-code's hash it was bound
  to, then the checks at Start, and arms that one start only if they pass.
- `GET /pair`, `POST /pair`: a phone types the code `phone.py pair` showed at the Mac, from the
  home Wi-Fi, and is signed in by a cookie (`phones.py`).

Anything else is a 404, and a method a path doesn't take a 405. No page shows a setting, a
token or an address but its own. Start opens in the phone's own browser: the sign-in's cookie is
`SameSite=Strict`, and every page refuses to be framed, so it's never inside Home Assistant.
"""

from __future__ import annotations

import errno
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from print_shop import reprint
from print_shop.arming import Armed
from print_shop.orchestrator.daemon import PhoneStart, ReadyToStart
from print_shop.orchestrator.print_queue import Line
from print_shop.phone.again import again_page, also_page, nozzle_typed
from print_shop.phone.confirm import Confirm, Confirms, secret_hash
from print_shop.phone.filaments import SwapJob, filaments_page, load_page, spools_for_job
from print_shop.phone.home import HOME_KEY, PORT_KEY, Home
from print_shop.phone.page import (
    away_page,
    message_page,
    nothing_ready_page,
    pair_page,
    signed_out_page,
    start_page,
)
from print_shop.phone.phones import Paired, Phones
from print_shop.phone.routes import route_for
from print_shop.phone.screens import (
    job_page,
    main_page,
    remove_page,
    run_page,
    runs_page,
)
from print_shop.phone.shop import AlsoDiffers, LoadAsk, Shop
from print_shop.phone.slice import slice_page

__all__ = ["COOKIE", "PhoneOff", "Starts", "attach", "handler_for", "make_server"]

logger = logging.getLogger(__name__)

def _number(value: str, digits: int) -> bool:
    """A plain number of at most `digits` digits: 0 to 9 only."""
    return value.isdecimal() and value.isascii() and 0 < len(value) <= digits


#: What a button did, said on the page it comes back to.
_DONE = {
         "again": "In the queue: a print again. Start is still yours, at the gate.",
         "moved": "Moved to the spool you picked. Another material goes back to its review, "
                  "with your answers kept.","hold": "Held: it's never offered until you let it go.",
         "release": "Let go: it's offered in its turn.",
         "first": "First in the queue now."}

#: The sign-in's cookie.
COOKIE = "ps_phone"
#: A year: the phone stays signed in until `phone.py forget`.
COOKIE_AGE_S = 365 * 24 * 3600
#: The most a POST may send: a confirm's id and the bed's answer, or a code; or an outcome's
#: note of 2000 characters, each of which can take 9 bytes encoded.
MAX_BODY = 20_000
#: The most fields a form has: an outcome's, with a pick for each spool on hand.
MAX_FIELDS = 64

_SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    # Not `no-referrer`: under it a browser sends a same-origin POST's `Origin` as "null", which
    # `_own_origin` refuses. `same-origin` sends the page's own, and nothing to another site.
    ("Referrer-Policy", "same-origin"),
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
     "frame-ancestors 'none'; base-uri 'none'"),
)


class Starts(Protocol):
    """What the page starts with: `Daemon`'s two actions for the phone."""

    def ready_to_start(self) -> ReadyToStart | None: ...

    def last_phone_start(self) -> str: ...

    def start_from_phone(
        self, job_id: str, *, armed: Armed, press: str, gcode_sha256: str,
    ) -> PhoneStart: ...


class PhoneOff(RuntimeError):
    """The page couldn't listen: said, and the run goes on without the phone's Start."""


def make_server(
    *, home: Home, phones: Phones, starts: Starts | None, confirms: Confirms | None = None,
    listen_on: str | None = None, clock: Callable[[], datetime] | None = None,
    shop: Shop | None = None,
) -> ThreadingHTTPServer:
    """The page's server on `home.address` at `home.port`. Not started.

    `starts` None serves pairing alone (`phone.py pair`, with no job running). `listen_on` is for
    tests, to listen on loopback: a request from there is refused as coming through the Mac.

    Raises:
        PhoneOff: If it can't listen there, saying why.
    """

    handler = handler_for(home=home, phones=phones, starts=starts, confirms=confirms,
                          clock=clock, shop=shop)
    address = listen_on or str(home.address)
    try:
        return ThreadingHTTPServer((address, home.port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRNOTAVAIL:
            raise PhoneOff(
                f"the phone's Start is off: this Mac doesn't have {home.address} now, "
                f"{HOME_KEY}'s address. It's away from home, or its address changed: reserve "
                "the Mac's address at the router, so it keeps it") from exc
        if exc.errno == errno.EADDRINUSE:
            raise PhoneOff(
                f"the phone's Start is off: port {home.port} is in use on {home.address}. "
                f"Set another with {PORT_KEY}") from exc
        raise PhoneOff(f"the phone's Start is off: it can't listen on {home.host} ({exc})") from exc


def handler_for(
    *, home: Home, phones: Phones, starts: Starts | None, confirms: Confirms | None = None,
    clock: Callable[[], datetime] | None = None, shop: Shop | None = None,
) -> type[BaseHTTPRequestHandler]:
    """The page's request handler, bound to these: `make_server`'s, and a test's to drive with
    a peer address of its own."""

    class Handler(_Handler):
        pass

    Handler.home = home
    Handler.phones = phones
    Handler.starts = starts
    Handler.shop = shop
    Handler.confirms = confirms or Confirms()
    Handler.now = staticmethod(clock or (lambda: datetime.now(UTC)))
    return Handler


def attach(server: ThreadingHTTPServer, starts: Starts, shop: Shop | None = None) -> None:
    """The job's daemon, given to a server made before it (so the run can say whether the page
    is on before it builds the daemon), and what its other screens read (mqucifer/print-shop#221).
    A queue is given to `shop` once it's built (`Shop.queue`)."""
    handler = server.RequestHandlerClass
    if not (isinstance(handler, type) and issubclass(handler, _Handler)):
        raise TypeError("not the phone page's server")
    handler.starts = starts
    if shop is not None:
        handler.shop = shop


class _Handler(BaseHTTPRequestHandler):
    home: Home
    phones: Phones
    starts: Starts | None
    shop: Shop | None
    confirms: Confirms
    now: Callable[[], datetime]
    server_version = "print-shop-phone"
    sys_version = ""
    #: A slow client doesn't hold a thread.
    timeout = 15

    # -- methods -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the standard library's name
        if not self._own_host():
            return
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        if not self._own_host() or not self._own_origin():
            return
        self._dispatch("POST")

    def _refuse_method(self, allowed: str = "GET, POST") -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", allowed)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _no_such_method(self) -> None:
        self._refuse_method()

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _no_such_method  # noqa: N815

    def _dispatch(self, method: str) -> None:
        """The request, answered by its route in the table, and by nothing else."""
        split = urlsplit(self.path)
        found = route_for(method, split.path)
        if found is None:
            return self._send(message_page("Not here", "Nothing is at that address.",
                                           back_to="/", back_words="Back to queue"),
                              code=HTTPStatus.NOT_FOUND)
        if isinstance(found, str):
            return self._refuse_method(found)
        route, held = found
        answer: Callable[[dict[str, str], Any], None] = getattr(self, f"_r_{route.name}")
        if route.signed_out or route.arms or route.name == "start_screen":
            # Pairing, and #214's Start, check for themselves, in their own order.
            return answer(held, split.query)
        phone, _ = self._signed_in()
        if phone is None:
            return self._send(signed_out_page(), code=HTTPStatus.OK if method == "GET"
                              else HTTPStatus.FORBIDDEN)
        away = self._away()
        if away:
            if method == "POST":
                self._refused(f"{split.path} refused: {away}")
            return self._send(away_page(part=None, why=away),
                              code=HTTPStatus.OK if method == "GET" else HTTPStatus.FORBIDDEN)
        if method == "POST":
            form = self._form()
            if form is None:
                return
            return answer(held, form)
        return answer(held, split.query)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # a request is logged only where it's refused, without its cookie or code

    # -- the screens ---------------------------------------------------------

    def _r_start_screen(self, held: dict[str, str], query: str) -> None:
        phone, secret = self._signed_in()
        if phone is None:
            return self._send(signed_out_page())
        name = phone.name
        ready = self.starts.ready_to_start() if self.starts is not None else None
        if ready is None:
            last = self.starts.last_phone_start() if self.starts is not None else ""
            return self._send(nothing_ready_page(name, last))
        away = self._away()
        if away:
            return self._send(away_page(part=ready.part, why=away))
        made = self.confirms.make(
            phone_hash=secret_hash(secret), phone=name, job_id=ready.job_id, press=ready.press,
            gcode_sha256=ready.gcode_sha256, address=self._peer())
        self._send(start_page(part=ready.part, checks=ready.checks, confirm=made,
                              held=ready.held))

    def _r_pair_screen(self, held: dict[str, str], query: str) -> None:
        self._send(pair_page())

    def _r_start(self, held: dict[str, str], query: str) -> None:
        form = self._form()
        if form is None:
            return
        phone, secret = self._signed_in()
        if phone is None:
            return self._send(signed_out_page(), code=HTTPStatus.FORBIDDEN)
        away = self._away()
        if away:
            self._refused(f"Start refused: {away}")
            return self._send(away_page(part=None, why=away), code=HTTPStatus.FORBIDDEN)
        spent = self.confirms.spend(form.get("confirm", ""), phone_hash=secret_hash(secret),
                                    address=self._peer())
        if isinstance(spent, str):
            self._refused(f"Start refused: {spent}")
            return self._send(message_page("Not started", spent), code=HTTPStatus.FORBIDDEN)
        if form.get("bed") != "clear":
            return self._send(message_page(
                "Not started", "Tick \"I can see the bed is clear\" to start: reload the page."),
                code=HTTPStatus.FORBIDDEN)
        if self.starts is None:
            return self._send(message_page("Not started", "Nothing is ready to start."),
                              code=HTTPStatus.CONFLICT)
        result = self.starts.start_from_phone(
            spent.job_id, armed=self._armed(spent), press=spent.press,
            gcode_sha256=spent.gcode_sha256)
        if result.started:
            return self._send(message_page("Started", result.said, again=False))
        if result.pending:
            return self._send(message_page("Starting", result.said), code=HTTPStatus.ACCEPTED)
        return self._send(message_page("Not started", result.said), code=HTTPStatus.CONFLICT)

    def _r_pair(self, held: dict[str, str], query: str) -> None:
        form = self._form()
        if form is None:
            return
        away = self._away()
        if away:
            self._refused(f"pairing refused: {away}")
            return self._send(away_page(part=None, why=away), code=HTTPStatus.FORBIDDEN)
        paired = self.phones.pair(form.get("code", ""))
        if isinstance(paired, str):
            self._refused(f"pairing refused: {paired}")
            return self._send(pair_page(paired), code=HTTPStatus.FORBIDDEN)
        name, secret = paired
        logger.warning("a phone was paired as %r from %s", name, self._peer())
        cookie = (f"{COOKIE}={secret}; HttpOnly; SameSite=Strict; Path=/; "
                  f"Max-Age={COOKIE_AGE_S}")
        self._send(message_page("Signed in", f"This phone is signed in as {name}.",
                                back_to="/", back_words="To the queue"),
                   cookies=(cookie,))

    # -- the queue and the finished runs (mqucifer/print-shop#221) -----------

    def _no_shop(self) -> None:
        self._send(message_page("Not here", "This run doesn't serve that screen.", back_to="/",
                                back_words="Back to queue"), code=HTTPStatus.NOT_FOUND)

    def _r_main(self, held: dict[str, str], query: str) -> None:
        shop = self.shop
        offered = ""
        if shop is None or shop.queue is None:
            ready = self.starts.ready_to_start() if self.starts is not None else None
            offered = ready.part if ready is not None else ""
        if shop is None:
            return self._send(main_page(printing=(), sends=(), lines=(), to_record=(),
                                        ready=(), queue_on=False, offered=offered))
        lines = shop.lines()
        self._send(main_page(
            printing=shop.printing(), sends=shop.sent(), lines=lines,
            to_record=shop.to_record(), ready=shop.ready(lines), queue_on=shop.queue is not None,
            offered=offered, on_hand=shop.on_hand(), changes=shop.stock_changes()))

    def _job_line(self, held: dict[str, str]) -> Line | None:
        line = self.shop.line(held["id"]) if self.shop is not None else None
        if line is None:
            self._send(message_page("Not in the queue", "That job isn't in the queue now: it "
                                    "printed, or was removed.", back_to="/",
                                    back_words="Back to queue"), code=HTTPStatus.NOT_FOUND)
        return line

    def _r_job(self, held: dict[str, str], query: str) -> None:
        line = self._job_line(held)
        if line is None or self.shop is None:
            return
        said = parse_qs(query).get("said", [""])[0][:200]
        sliced = self.shop.prepared(line.job.copy_of or line.job.job_id).get("slice_reading")
        self._send(job_page(line, each_s=self.shop.each_s(line.job), said=_DONE.get(said, ""),
                            sliced=sliced is not None,
                            spools=spools_for_job(line, self.shop.on_hand())))

    def _r_remove_screen(self, held: dict[str, str], query: str) -> None:
        line = self._job_line(held)
        if line is not None:
            self._send(remove_page(line))

    def _r_act(self, held: dict[str, str], form: dict[str, str]) -> None:
        """Hold, Let go, First and Remove: the queue's own, as `queue>` does them."""
        line = self._job_line(held)
        if line is None or self.shop is None:
            return
        verb = held["verb"]
        if verb == "remove" and form.get("sure") != "yes":
            return self._send(remove_page(line), code=HTTPStatus.BAD_REQUEST)
        why = self.shop.act(verb, line.job.job_id)
        if why:
            return self._send(message_page("Not done", why, back_to=f"/job/{line.job.job_id}",
                                           back_words="Back to the job"),
                              code=HTTPStatus.CONFLICT)
        if verb == "remove":
            return self._see_other("/")
        self._see_other(f"/job/{line.job.job_id}?said={verb}")

    def _r_move(self, held: dict[str, str], form: dict[str, str]) -> None:
        """Print it in another spool: the queue's own `spool`."""
        line = self._job_line(held)
        if line is None or self.shop is None:
            return
        spool = form.get("spool", "")
        if not _number(spool, 9):
            return self._send(message_page("Not moved", "That isn't a spool.",
                                           back_to=f"/job/{line.job.job_id}",
                                           back_words="Back to the job"),
                              code=HTTPStatus.BAD_REQUEST)
        said = self.shop.move(line.job.job_id, int(spool))
        if isinstance(said, str):
            return self._send(message_page("Not moved", said, back_to=f"/job/{line.job.job_id}",
                                           back_words="Back to the job"),
                              code=HTTPStatus.CONFLICT)
        self._see_other(f"/job/{line.job.job_id}?said=moved")

    def _r_filaments(self, held: dict[str, str], query: str) -> None:
        if self.shop is None:
            return self._no_shop()
        lines = self.shop.lines()
        swaps = []
        for line in lines:
            reading = self.shop.prepared(line.job.copy_of or line.job.job_id).get(
                "slice_reading")
            if reading is not None and (swap := SwapJob(line, reading)).swaps:
                swaps.append(swap)
        said = "Loaded." if parse_qs(query).get("said", [""])[0] == "loaded" else ""
        self._send(filaments_page(self.shop.on_hand(), changes=self.shop.stock_changes(),
                                  lines=lines, swaps=swaps, said=said))

    def _r_load(self, held: dict[str, str], form: dict[str, str]) -> None:
        """The stock's own load, asking both halves first where what's on isn't known."""
        if self.shop is None:
            return self._no_shop()
        spool, changes = form.get("spool", ""), form.get("changes", "")
        if not (_number(spool, 9) and _number(changes, 12)):
            return self._send(message_page("Not loaded", "That wasn't the page's form.",
                                           back_to="/filaments", back_words="Back to filaments"),
                              code=HTTPStatus.BAD_REQUEST)
        done = self.shop.load(int(spool), changes=int(changes), answer=form.get("answer", ""))
        if isinstance(done, LoadAsk):
            return self._send(load_page(done))
        if isinstance(done, str):
            return self._send(message_page("Not loaded", done, back_to="/filaments",
                                           back_words="Back to filaments"),
                              code=HTTPStatus.CONFLICT)
        self._see_other("/filaments?said=loaded")

    def _r_again_screen(self, held: dict[str, str], query: str) -> None:
        if self.shop is None:
            return self._no_shop()
        nozzle = parse_qs(query).get("nozzle", [""])[0][:6]
        with self.shop.again_of(held["id"]) as found:
            if isinstance(found, str):
                return self._send(message_page("Can't print it again", found, back_to="/runs",
                                               back_words="Back to finished"),
                                  code=HTTPStatus.CONFLICT)
            self._send(again_page(found, nozzle=nozzle))

    def _r_again(self, held: dict[str, str], form: dict[str, str]) -> None:
        """Put it in the queue: as it was, or with − and +'s change, sliced again."""
        if self.shop is None:
            return self._no_shop()
        nozzle = form.get("nozzle", "")[:6]
        typed = ""
        if nozzle:
            with self.shop.again_of(held["id"]) as found:
                if isinstance(found, str):
                    return self._send(message_page("Not queued", found, back_to="/runs",
                                                   back_words="Back to finished"),
                                      code=HTTPStatus.CONFLICT)
                typed = nozzle_typed(found, nozzle)
        done = self.shop.again(held["id"], typed, also=form.get("also") == "yes")
        if isinstance(done, AlsoDiffers):
            return self._send(also_page(held["id"], nozzle=nozzle, lines=done.lines,
                                        beyond=done.beyond))
        if isinstance(done, str):
            return self._send(message_page("Not queued", done, back_to=f"/run/{held['id']}",
                                           back_words="Back to the run"),
                              code=HTTPStatus.CONFLICT)
        # Its own page, so a reload doesn't queue it twice; it says what it was compared with.
        self._see_other(f"/job/{done.job_id}?said=again")

    def _layer(self, query: str) -> int | None:
        found = parse_qs(query).get("layer", [""])[0]
        return int(found) if found.isdecimal() and found.isascii() and len(found) < 7 else None

    def _r_job_slice(self, held: dict[str, str], query: str) -> None:
        line = self._job_line(held)
        if line is None or self.shop is None:
            return
        state = self.shop.prepared(line.job.copy_of or line.job.job_id)
        reading = state.get("slice_reading")
        if reading is None:
            return self._send(message_page(
                "Not sliced yet", "It hasn't been sliced yet: what the slice shows is here once "
                "it has.", back_to=f"/job/{line.job.job_id}", back_words="Back to the job"),
                code=HTTPStatus.NOT_FOUND)
        result = state.get("slice")
        path = f"/job/{line.job.job_id}"
        self._send(slice_page(
            part=line.job.part, reading=reading, flags=tuple(state.get("slice_flags", ())),
            print_time_s=result.print_time_s if result is not None else None,
            grams=result.filament_g if result is not None else None, here=f"{path}/slice",
            back=path, back_words="Back to the job", layer=self._layer(query)))

    def _r_run_slice(self, held: dict[str, str], query: str) -> None:
        row = self.shop.run(held["id"]) if self.shop is not None else None
        if row is None or row.reading is None:
            return self._send(message_page(
                "Not here", "No slice was read for that run.", back_to="/runs",
                back_words="Back to finished"), code=HTTPStatus.NOT_FOUND)
        path = f"/run/{row.job_id}"
        self._send(slice_page(
            part=row.part_said, reading=row.reading, flags=row.flags or (),
            print_time_s=row.print_time_s, grams=None, here=f"{path}/slice", back=path,
            back_words="Back to the run", layer=self._layer(query)))

    def _r_runs(self, held: dict[str, str], query: str) -> None:
        if self.shop is None:
            return self._no_shop()
        find = parse_qs(query).get("find", [""])[0][:200]
        with self.shop.ledger() as ledger:
            rows = reprint.runs(ledger, find) if ledger is not None else []
        self._send(runs_page(rows, find=find))

    def _r_run(self, held: dict[str, str], query: str, *, said: str = "",
               code: int = HTTPStatus.OK) -> None:
        if self.shop is None:
            return self._no_shop()
        row = self.shop.run(held["id"])
        if row is None:
            return self._send(message_page("Not found", "No run has that id.", back_to="/runs",
                                           back_words="Back to finished"),
                              code=HTTPStatus.NOT_FOUND)
        if not said and parse_qs(query).get("said", [""])[0] == "recorded":
            said = "Recorded."
        choices = self.shop.choices(row.job_id) if row.awaiting_answer else {}
        self._send(run_page(row, choices=choices, said=said), code=code)

    def _r_outcome(self, held: dict[str, str], form: dict[str, str]) -> None:
        """The card's own Record, for the run this page names."""
        if self.shop is None:
            return self._no_shop()
        pick = form.get("pick", "")
        picked = None
        if pick:
            # What the pick meant when the page was shown: a load since refuses it.
            row = self.shop.run(held["id"])
            choices = list(self.shop.choices(held["id"]).items()) if row is not None else []
            shown = [v for k, v in form.items() if k.startswith("picked_")]
            picked = next((p for words, p in choices if words == pick
                           and f"{p.spool_id}:{p.changes}" in shown), None)
            if picked is None:
                return self._r_run(held, "", said="What's on the printer has changed since the "
                                   "page was shown: look again and pick.",
                                   code=HTTPStatus.CONFLICT)
        why = self.shop.record(held["id"], result=form.get("result", ""),
                               symptom=form.get("symptom", ""), note=form.get("note", ""),
                               pick=pick, picked=picked)
        if why:
            return self._r_run(held, "", said=f"Not recorded: {why}.", code=HTTPStatus.CONFLICT)
        self._see_other(f"/run/{held['id']}?said=recorded")

    # -- checks --------------------------------------------------------------

    def _peer(self) -> str:
        """Where the request came from: the socket's address, never a header's."""
        return str(self.client_address[0])

    def _away(self) -> str:
        """Why this isn't a phone on the home Wi-Fi, in words; or ""."""
        return self.home.why_not(self._peer(), self.headers.keys())

    def _own_host(self) -> bool:
        """`Host` is the page's own address and port: a name that points here by DNS (a
        rebinding) is refused."""
        if self.headers.get("Host", "") == self.home.host:
            return True
        self._refused("refused: its Host isn't the page's own")
        self._send(message_page("Not here", f"This page answers only at {self.home.origin}/.",
                                again=False), code=HTTPStatus.MISDIRECTED_REQUEST)
        return False

    def _own_origin(self) -> bool:
        """A POST's `Origin` is the page's own: no other page can post to it."""
        if self.headers.get("Origin", "") == self.home.origin:
            return True
        self._refused("refused: its Origin isn't the page's own")
        self._send(message_page("Not started", "That came from another page: nothing was "
                                "started. Open the Start page on your phone."),
                   code=HTTPStatus.FORBIDDEN)
        return False

    def _signed_in(self) -> tuple[Paired | None, str]:
        """The signed-in phone and its secret, or (None, "")."""
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None, ""
        held = cookie.get(COOKIE)
        secret = held.value if held is not None else ""
        phone = self.phones.signed_in(secret)
        return (phone, secret) if phone is not None else (None, "")

    def _form(self) -> dict[str, str] | None:
        kind = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            length = -1
        if kind != "application/x-www-form-urlencoded" or not 0 <= length <= MAX_BODY:
            self._send(message_page("Not started", "That wasn't the page's form."),
                       code=HTTPStatus.BAD_REQUEST)
            return None
        body = self.rfile.read(length).decode("utf-8", "replace")
        try:
            fields = parse_qs(body, max_num_fields=MAX_FIELDS)
        except ValueError:
            self._send(message_page("Not done", "That wasn't the page's form."),
                       code=HTTPStatus.BAD_REQUEST)
            return None
        return {key: values[0] for key, values in fields.items() if values}

    def _armed(self, confirm: Confirm) -> Armed:
        return Armed(job_id=confirm.job_id, how="phone", by=confirm.phone, where=self._peer(),
                     at=self.now().isoformat(timespec="seconds"))

    def _refused(self, why: str) -> None:
        logger.warning("phone page, from %s: %s", self._peer(), why)

    # -- sending -------------------------------------------------------------

    def _see_other(self, path: str) -> None:
        """After a button: the page it changed, shown again, so a reload doesn't post twice."""
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)
        self.end_headers()

    def _send(self, body: str, *, code: int = HTTPStatus.OK, cookies: tuple[str, ...] = ()) -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in _SECURITY_HEADERS:
            self.send_header(name, value)
        for cookie in cookies:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)
