"""The phone's Start page: the standard library's HTTP server, on the Mac's home address only
(mqucifer/print-shop#214).

Routes:
- `GET /`: signed in, the job ready to start, what Start checks, and the bed's question, with a
  new confirm; from anywhere but a phone on the home Wi-Fi, the job and "You're away from home";
  signed out, only how to sign in.
- `POST /start`: the confirm and the bed's answer. Checked in this order, each refusing in words
  with nothing armed: the page's own `Host` and `Origin`; signed in; from a phone on the home
  Wi-Fi (`home.py`); the confirm, spent now whatever follows, from the same phone and address,
  in time (`confirm.py`); the bed ticked. Then it goes to the gate as a Start
  (`Daemon.start_from_phone`), which checks the gate's word and the G-code's hash it was bound
  to, then the checks at Start, and arms that one start only if they pass.
- `GET /pair`, `POST /pair`: a phone types the code `phone.py pair` showed at the Mac, from the
  home Wi-Fi, and is signed in by a cookie (`phones.py`).

Anything else is a 404, and every other method a 405. No page shows a setting, a token or an
address but its own. Start opens in the phone's own browser: the sign-in's cookie is
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
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from print_shop.arming import Armed
from print_shop.orchestrator.daemon import PhoneStart, ReadyToStart
from print_shop.phone.confirm import Confirm, Confirms, secret_hash
from print_shop.phone.home import HOME_KEY, PORT_KEY, Home
from print_shop.phone.page import (
    away_page,
    message_page,
    nothing_ready_page,
    pair_page,
    signed_out_page,
    start_page,
)
from print_shop.phone.phones import Phones

__all__ = ["COOKIE", "PhoneOff", "Starts", "attach", "handler_for", "make_server"]

logger = logging.getLogger(__name__)

#: The sign-in's cookie.
COOKIE = "ps_phone"
#: A year: the phone stays signed in until `phone.py forget`.
COOKIE_AGE_S = 365 * 24 * 3600
#: The most a POST may send: a confirm's id and the bed's answer, or a code.
MAX_BODY = 2048

_SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
     "frame-ancestors 'none'; base-uri 'none'"),
)


class Starts(Protocol):
    """What the page starts with: `Daemon`'s two actions for the phone."""

    def ready_to_start(self) -> ReadyToStart | None: ...

    def start_from_phone(
        self, job_id: str, *, armed: Armed, press: str, gcode_sha256: str,
    ) -> PhoneStart: ...


class PhoneOff(RuntimeError):
    """The page couldn't listen: said, and the run goes on without the phone's Start."""


def make_server(
    *, home: Home, phones: Phones, starts: Starts | None, confirms: Confirms | None = None,
    listen_on: str | None = None, clock: Callable[[], datetime] | None = None,
) -> ThreadingHTTPServer:
    """The page's server on `home.address` at `home.port`. Not started.

    `starts` None serves pairing alone (`phone.py pair`, with no job running). `listen_on` is for
    tests, to listen on loopback: a request from there is refused as coming through the Mac.

    Raises:
        PhoneOff: If it can't listen there, saying why.
    """

    handler = handler_for(home=home, phones=phones, starts=starts, confirms=confirms,
                          clock=clock)
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
    clock: Callable[[], datetime] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """The page's request handler, bound to these: `make_server`'s, and a test's to drive with
    a peer address of its own."""

    class Handler(_Handler):
        pass

    Handler.home = home
    Handler.phones = phones
    Handler.starts = starts
    Handler.confirms = confirms or Confirms()
    Handler.now = staticmethod(clock or (lambda: datetime.now(UTC)))
    return Handler


def attach(server: ThreadingHTTPServer, starts: Starts) -> None:
    """The job's daemon, given to a server made before it (so the run can say whether the page
    is on before it builds the daemon)."""
    handler = server.RequestHandlerClass
    if not (isinstance(handler, type) and issubclass(handler, _Handler)):
        raise TypeError("not the phone page's server")
    handler.starts = starts


class _Handler(BaseHTTPRequestHandler):
    home: Home
    phones: Phones
    starts: Starts | None
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
        path = urlsplit(self.path).path
        if path == "/":
            return self._start_screen()
        if path == "/pair":
            return self._send(pair_page())
        return self._send(message_page("Not here", "Nothing is at that address.", again=True),
                          code=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._own_host() or not self._own_origin():
            return
        path = urlsplit(self.path).path
        form = self._form()
        if form is None:
            return
        if path == "/start":
            return self._start(form)
        if path == "/pair":
            return self._pair(form)
        return self._send(message_page("Not here", "Nothing is at that address."),
                          code=HTTPStatus.NOT_FOUND)

    def _refuse_method(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _refuse_method  # noqa: N815

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # a request is logged only where it's refused, without its cookie or code

    # -- the screens ---------------------------------------------------------

    def _start_screen(self) -> None:
        name, secret = self._signed_in()
        if name is None:
            return self._send(signed_out_page())
        ready = self.starts.ready_to_start() if self.starts is not None else None
        if ready is None:
            return self._send(nothing_ready_page(name))
        away = self._away()
        if away:
            return self._send(away_page(part=ready.part, why=away))
        made = self.confirms.make(
            phone_hash=secret_hash(secret), phone=name, job_id=ready.job_id, press=ready.press,
            gcode_sha256=ready.gcode_sha256, address=self._peer())
        self._send(start_page(part=ready.part, checks=ready.checks, confirm=made,
                              held=ready.held))

    def _start(self, form: dict[str, str]) -> None:
        name, secret = self._signed_in()
        if name is None:
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
        return self._send(message_page("Not started", result.said), code=HTTPStatus.CONFLICT)

    def _pair(self, form: dict[str, str]) -> None:
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
        self._send(message_page("Signed in", f"This phone is signed in as {name}."),
                   cookies=(cookie,))

    # -- checks --------------------------------------------------------------

    def _peer(self) -> str:
        """Where the request came from: the socket's address, never a header's."""
        return str(self.client_address[0])

    def _away(self) -> str:
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

    def _signed_in(self) -> tuple[str | None, str]:
        """The signed-in phone's name and its secret, or (None, "")."""
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None, ""
        held = cookie.get(COOKIE)
        secret = held.value if held is not None else ""
        name = self.phones.signed_in(secret)
        return (name, secret) if name is not None else (None, "")

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
        fields = parse_qs(body, max_num_fields=8)
        return {key: values[0] for key, values in fields.items() if values}

    def _armed(self, confirm: Confirm) -> Armed:
        return Armed(job_id=confirm.job_id, how="phone", by=confirm.phone, where=self._peer(),
                     at=self.now().isoformat(timespec="seconds"))

    def _refused(self, why: str) -> None:
        logger.warning("phone page, from %s: %s", self._peer(), why)

    # -- sending -------------------------------------------------------------

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
