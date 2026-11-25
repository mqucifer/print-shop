"""The phone's page over HTTPS, from configured certificate files (mqucifer/print-shop#312).

The page's certificate is one for the page's name alone (`PHONE_NAME`), issued by the home
network's ACME client and copied, with its key, to the files `PHONE_TLS_CERT` and
`PHONE_TLS_KEY` name after each renewal. It's checked as the phone's browser will check it, but
for who issued it, which the phone checks: it names `PHONE_NAME` and nothing else (never a
wildcard), it's in date, and the key is its own. A certificate that fails keeps the page off,
saying why.

The files are read again for each connection, and a changed pair is checked and served from
then on, so a renewal needs no restart. A pair that fails (a certificate copied before its key)
is said once, and the one before is served while it lasts. Once the served one runs out, the
page answers nothing until a good one is copied in, and says so at the terminal.

Nothing here writes or says a key, or what's in a file: only the files' paths.
"""

from __future__ import annotations

import hashlib
import logging
import ssl
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "CERT_KEY", "KEY_KEY", "NAME_KEY", "RENEW_WARN", "Certificate", "CertificateRefused",
    "TlsFiles", "check", "ended", "serving",
]

logger = logging.getLogger(__name__)

#: The page's name, which its certificate names: `print.<domain>`.
NAME_KEY = "PHONE_NAME"
#: The certificate's file (PEM: the certificate, then its chain).
CERT_KEY = "PHONE_TLS_CERT"
#: Its key's file (PEM, not encrypted).
KEY_KEY = "PHONE_TLS_KEY"
#: An ACME client renews about 30 days ahead: with less than this left, the copy isn't running.
RENEW_WARN = timedelta(days=14)
#: How long a closing connection waits for the phone's close_notify.
CLOSE_WAIT_S = 2.0
#: Why more names than the page's is refused (print-shop-ux, on mqucifer/print-shop#312).
ONE_NAME = "a certificate for more names than the page's puts their key on this host"

#: OpenSSL's X509_V_FLAG_NO_CHECK_TIME, which `ssl` doesn't name.
_NO_CHECK_TIME = 0x200000
_PEM_BEGIN = "-----BEGIN CERTIFICATE-----"
_PEM_END = "-----END CERTIFICATE-----"


class CertificateRefused(ValueError):
    """The certificate can't be served: why, in words that follow "the phone's page is off: ".
    `retry_at` is when the same files may pass: a certificate that isn't valid yet."""

    def __init__(self, words: str, *, retry_at: datetime | None = None) -> None:
        super().__init__(words)
        self.retry_at = retry_at


@dataclass(frozen=True)
class TlsFiles:
    """The certificate's and its key's files."""

    cert: Path
    key: Path

    @property
    def folder(self) -> Path:
        """Where a renewal is copied to."""
        return self.cert.parent

    def read(self) -> tuple[bytes, bytes]:
        """Both files as they are now. Raises OSError if either can't be read."""
        return self.cert.read_bytes(), self.key.read_bytes()


@dataclass(frozen=True)
class Loaded:
    """A pair that passed: what serves it, when it runs out, and the files as they were."""

    context: ssl.SSLContext
    not_after: datetime
    #: The two files' digest as they were checked: never the key itself.
    pair: str


def digest(pair: tuple[bytes, bytes]) -> str:
    return hashlib.sha256(len(pair[0]).to_bytes(8, "big") + pair[0] + pair[1]).hexdigest()


def day(when: datetime) -> str:
    """A day as the person reads it: their own, or UTC's where theirs can't be told."""
    try:
        return f"{when.astimezone():%Y-%m-%d}"
    except (OverflowError, ValueError, OSError):
        return f"{when:%Y-%m-%d} (UTC)"


def check(files: TlsFiles, name: str, now: datetime) -> Loaded:
    """The pair on disk, checked, ready to serve.

    Raises:
        CertificateRefused: If a file can't be read, the certificate is out of date, doesn't name
            `name`, names anything else, or the key isn't its own; or anything else about it
            fails, said by its kind alone.
    """
    try:
        return _check(files, name, now)
    except CertificateRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - a strange certificate is refused, never served
        raise CertificateRefused(
            f"its certificate couldn't be checked ({type(exc).__name__})") from None


def _check(files: TlsFiles, name: str, now: datetime) -> Loaded:
    try:
        pair = files.read()
    except OSError as exc:
        raise CertificateRefused(
            f"its certificate or key can't be read ({files.cert}, {files.key}: "
            f"{exc.strerror or type(exc).__name__})") from None
    text = pair[0].decode("ascii", "replace")
    if _PEM_BEGIN not in text or _PEM_END not in text:
        raise CertificateRefused(f"{files.cert} isn't a PEM certificate")
    leaf = text[text.index(_PEM_BEGIN):text.index(_PEM_END) + len(_PEM_END)]
    try:
        not_before, not_after = _validity(ssl.PEM_cert_to_DER_cert(leaf))
    except (ValueError, IndexError):
        raise CertificateRefused(f"{files.cert} couldn't be read as a certificate") from None
    if now >= not_after:
        raise CertificateRefused(
            f"its certificate ran out on {day(not_after)}, and no good renewal is in "
            f"{files.folder}")
    if now < not_before:
        raise CertificateRefused(f"its certificate isn't valid until {day(not_before)}",
                                 retry_at=not_before)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        # A key that wants a password isn't asked for one: no prompt on the terminal.
        server.load_cert_chain(files.cert, files.key, password=lambda: b"")
    except (ssl.SSLError, OSError):
        raise CertificateRefused(
            f"the key in {files.key} isn't the certificate's own, or can't be read (an "
            "encrypted key isn't taken)") from None
    try:
        again = files.read()
    except OSError:
        again = (b"", b"")
    if again != pair:
        raise CertificateRefused(f"the files in {files.folder} changed as they were read")
    names = _names(server, leaf)
    wildcards = [n for n in names if "*" in n]
    if wildcards:
        raise CertificateRefused(
            f"its certificate is a wildcard's ({wildcards[0]}): {ONE_NAME}: use one for "
            f"{name} alone")
    if name not in names:
        raise CertificateRefused(
            f"its certificate isn't for {name}: it names {', '.join(names) or 'no name'}")
    others = [n for n in names if n != name]
    if others:
        raise CertificateRefused(
            f"its certificate names more than {name} ({', '.join(others)}): {ONE_NAME}: use one "
            f"for {name} alone")
    return Loaded(server, not_after, digest(pair))


def _names(server: ssl.SSLContext, leaf: str) -> list[str]:
    """The names the certificate is for, as the phone's browser reads them: a handshake in
    memory, with a client that holds the certificate itself as trusted, so who issued it isn't
    asked here. It proves the key and the certificate serve together."""
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False  # each name is weighed below, more strictly than a browser
    client.load_verify_locations(cadata=leaf)
    # Its dates are weighed by `check`, on the page's own clock, so a renewal not yet valid is
    # taken the moment it is, and said in the same words either way.
    client.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN | _NO_CHECK_TIME
    to_server, to_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    from_server, from_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    serving = server.wrap_bio(to_server, from_server, server_side=True)
    asking = client.wrap_bio(to_client, from_client)
    done = False
    try:
        for _ in range(8):
            for side in (asking, serving):
                with suppress(ssl.SSLWantReadError):
                    side.do_handshake()
            to_server.write(from_client.read())
            to_client.write(from_server.read())
            try:
                asking.do_handshake()
                serving.do_handshake()
                done = True
                break
            except ssl.SSLWantReadError:
                continue
    except ssl.SSLCertVerificationError as exc:
        raise CertificateRefused(
            f"its certificate couldn't be checked ({exc.verify_message})") from None
    except ssl.SSLError as exc:
        raise CertificateRefused(
            f"its certificate and key don't serve together ({exc.reason or 'TLS'})") from None
    if not done:
        raise CertificateRefused("its certificate and key don't serve together")
    seen: dict[str, Any] = dict(asking.getpeercert() or {})
    alt: tuple[tuple[str, str], ...] = tuple(seen.get("subjectAltName", ()))
    return list(dict.fromkeys(str(entry[1]).lower() for entry in alt))


def _validity(der: bytes) -> tuple[datetime, datetime]:
    """A certificate's notBefore and notAfter, from its DER (X.509's TBSCertificate)."""

    def tlv(at: int) -> tuple[int, int, int]:
        tag, size = der[at], der[at + 1]
        at += 2
        if size & 0x80:
            count = size & 0x7F
            if not 0 < count <= 4:
                raise ValueError("a length it can't take")
            size = int.from_bytes(der[at:at + count], "big")
            at += count
        if at + size > len(der):
            raise ValueError("short")
        return tag, at, at + size

    tag, start, _ = tlv(0)
    if tag != 0x30:
        raise ValueError("not a certificate")
    tag, at, _ = tlv(start)  # TBSCertificate
    if tag != 0x30:
        raise ValueError("not a certificate")
    tag, _, end = tlv(at)
    if tag == 0xA0:  # its version
        at = end
    for _ in range(3):  # its serial, signature algorithm and issuer
        _, _, at = tlv(at)
    tag, inside, _ = tlv(at)
    if tag != 0x30:
        raise ValueError("no validity")
    first, a, b = tlv(inside)
    second, c, d = tlv(b)
    return _time(first, der[a:b]), _time(second, der[c:d])


def _time(tag: int, raw: bytes) -> datetime:
    text = raw.decode("ascii")
    if tag == 0x17:  # UTCTime: YYMMDDHHMMSSZ, 1950 to 2049
        when = datetime.strptime(text, "%y%m%d%H%M%SZ")
        if when.year >= 2050:
            when = when.replace(year=when.year - 100)
    elif tag == 0x18:  # GeneralizedTime: YYYYMMDDHHMMSSZ
        when = datetime.strptime(text, "%Y%m%d%H%M%SZ")
    else:
        raise ValueError("not a time")
    return when.replace(tzinfo=UTC)


class Certificate:
    """The page's certificate as it's served: checked at start, and again whenever its files
    change, so a renewal is served without a restart."""

    def __init__(self, files: TlsFiles, name: str, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.files, self.name = files, name
        self.now = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._loaded: Loaded | None = None
        #: The pair on disk last refused (its digest), said once, and tried again once it
        #: changes, or once it may pass (`_retry_at`: one that wasn't valid yet).
        self._refused = ""
        self._retry_at: datetime | None = None
        #: Why the page answers nothing now, said once; "" while it serves.
        self._off = ""

    def load(self) -> None:
        """Check the pair on disk, at start. Raises CertificateRefused, saying why."""
        loaded = check(self.files, self.name, self.now())
        with self._lock:
            self._loaded, self._refused, self._retry_at, self._off = loaded, "", None, ""

    def context(self) -> ssl.SSLContext | None:
        """What serves the next connection: a renewal on disk once it's checked, or the one
        before while it lasts; None once nothing good is left, which closes the connection."""
        with self._lock:
            self._renewed()
            loaded = self._loaded
            if loaded is None:
                return None
            if self.now() >= loaded.not_after:
                why = (f"its certificate ran out on {day(loaded.not_after)}, and no good renewal "
                       f"is in {self.files.folder}")
                if why != self._off:
                    logger.warning("The phone's page is off: %s.", why)
                    self._off = why
                return None
            self._off = ""
            return loaded.context

    def trouble(self) -> str:
        """Why the page answers nothing now, or "": for the queue's live line."""
        self.context()
        with self._lock:
            return self._off

    def ahead(self) -> str:
        """A warning, once at a run's start, if what's served runs out within `RENEW_WARN`: an
        ACME client renews well before, so the job that copies it isn't running."""
        with self._lock:
            loaded = self._loaded
        if loaded is None or loaded.not_after - self.now() >= RENEW_WARN:
            return ""
        return (f"The phone's certificate runs out on {day(loaded.not_after)} and hasn't been "
                "renewed: check the job that copies it.")

    @property
    def runs_out(self) -> datetime | None:
        with self._lock:
            return self._loaded.not_after if self._loaded is not None else None

    def _renewed(self) -> None:
        try:
            pair = self.files.read()
        except OSError:
            return  # being copied, or moved: the one before is served while it lasts
        seen = digest(pair)
        if self._loaded is not None and seen == self._loaded.pair:
            return
        if seen == self._refused and (self._retry_at is None or self.now() < self._retry_at):
            return
        try:
            loaded = check(self.files, self.name, self.now())
        except CertificateRefused as exc:
            if seen != self._refused:
                logger.warning("A new certificate for the phone's page wasn't taken: %s. The "
                               "one before is served while it lasts.", exc)
            self._refused, self._retry_at = seen, exc.retry_at
            return
        self._loaded, self._refused, self._retry_at = loaded, "", None
        logger.warning("The phone's page serves its renewed certificate, good until %s.",
                       day(loaded.not_after))


def serving(certificate: Certificate, sock: object) -> ssl.SSLSocket:
    """An accepted connection, as the page serves it over HTTPS. The handshake is done in the
    request's own thread, on its first read, under the page's timeout, so a slow client holds
    nothing else.

    Raises:
        OSError: If the page answers nothing now: the connection is closed.
    """
    import socket  # noqa: PLC0415 - typed loosely above, for a test's socketpair

    assert isinstance(sock, socket.socket)
    try:
        context = certificate.context()
    except Exception:  # noqa: BLE001 - never the accept loop's end: this connection is closed
        logger.warning("the phone's page couldn't serve a connection", exc_info=True)
        context = None
    if context is None:
        sock.close()
        raise OSError("the phone's page answers nothing now")
    return context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)


def ended(conn: ssl.SSLSocket) -> None:
    """Say the reply is whole (TLS's close_notify) before the connection is closed, so a phone
    reads the end of the reply, not a broken connection. It waits `CLOSE_WAIT_S` at most for the
    phone's own, in the request's thread: nothing else waits on it."""
    try:
        conn.settimeout(CLOSE_WAIT_S)
        conn.unwrap()
    except (ssl.SSLError, OSError, ValueError):
        pass
