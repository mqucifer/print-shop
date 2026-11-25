"""The phone's page serves HTTPS itself, from configured certificate files
(mqucifer/print-shop#312).

Every certificate here is self-signed and made in the test, for a made-up name under `.test`:
no real name, domain or key is in this public repository. Over a real socket the page listens on
loopback (`listen_on`, as tests only can), so where a check needs a phone at home, the phone
tests drive it (`tests/test_phone_start.py`, run over HTTPS too). Every other phone test runs
over both schemes (`phone_scheme`).
"""

from __future__ import annotations

import functools
import ipaddress
import logging
import shutil
import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509

from print_shop.phone.home import Home, HomeRefused
from print_shop.phone.phones import Phones
from print_shop.phone.server import PageServer, PhoneOff, make_server
from print_shop.phone.tls import (
    ONE_NAME,
    RENEW_WARN,
    Certificate,
    CertificateRefused,
    TlsFiles,
    day,
)
from print_shop.queue_terminal import _Terminal
from tests.test_phone_start import FakeStarts, ip
from tests.test_stock import _load
from tests.tls_made_up import NAME, make, phone_context, served

NETWORK = f"{ip(10, 77, 0, 0)}/24"
MAC = ip(10, 77, 0, 20)
ROUTER = ip(10, 77, 0, 1)


def free_port() -> int:
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port: int = free.getsockname()[1]
        return port


def https_home(files: TlsFiles, port: int = 8765, name: str = NAME) -> Home:
    return Home(address=ipaddress.IPv4Address(MAC), network=ipaddress.IPv4Network(NETWORK),
                port=port, router=ipaddress.IPv4Address(ROUTER), name=name, tls=files)


def plain_home(port: int) -> Home:
    return Home(address=ipaddress.IPv4Address(MAC), network=ipaddress.IPv4Network(NETWORK),
                port=port, router=ipaddress.IPv4Address(ROUTER))


@contextmanager
def running(home: Home, phones: Phones, **kw: Any) -> Iterator[PageServer]:
    server = make_server(home=home, phones=phones, starts=FakeStarts(), listen_on="127.0.0.1",
                         **kw)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def serial(files: TlsFiles) -> int:
    return x509.load_pem_x509_certificate(files.cert.read_bytes()).serial_number


@pytest.fixture
def phones(tmp_path: Path) -> Phones:
    return Phones(tmp_path / "phones.sqlite")


@pytest.fixture
def files(tmp_path: Path) -> TlsFiles:
    return make(tmp_path / "tls")


# --------------------------------------------------------------------------
# The settings: all three, or none
# --------------------------------------------------------------------------


def _settings(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "settings.env"
    path.write_text(f"PHONE_HOME={MAC}/24\nPHONE_ROUTER={ROUTER}\n{extra}")
    return path


def test_with_the_three_set_the_page_is_its_name_over_https(tmp_path: Path,
                                                             files: TlsFiles) -> None:
    home = Home.from_settings(_settings(tmp_path, (
        f"PHONE_NAME=Print.Made-Up.test\nPHONE_TLS_CERT={files.cert}\n"
        f"PHONE_TLS_KEY={files.key}\nPHONE_PORT=9443\n")))
    assert home is not None
    assert home.name == NAME and home.tls == files
    assert home.host == f"{NAME}:9443"
    assert home.origin == f"https://{NAME}:9443"
    assert str(home.address) == MAC, "it listens where it did"


def test_without_them_the_page_reads_as_now(tmp_path: Path) -> None:
    home = Home.from_settings(_settings(tmp_path))
    assert home is not None and home.name is None and home.tls is None
    assert home.host == f"{MAC}:8765" and home.origin == f"http://{MAC}:8765"


@pytest.mark.parametrize(("given", "said"), [
    (("PHONE_NAME",), "PHONE_TLS_CERT and PHONE_TLS_KEY aren't set (PHONE_NAME is)"),
    (("PHONE_TLS_CERT",), "PHONE_NAME and PHONE_TLS_KEY aren't set (PHONE_TLS_CERT is)"),
    (("PHONE_NAME", "PHONE_TLS_CERT"),
     "PHONE_TLS_KEY isn't set (PHONE_NAME and PHONE_TLS_CERT are)"),
    (("PHONE_TLS_CERT", "PHONE_TLS_KEY"),
     "PHONE_NAME isn't set (PHONE_TLS_CERT and PHONE_TLS_KEY are)"),
])
def test_one_or_two_of_them_keep_the_page_off_saying_whats_missing(
    tmp_path: Path, files: TlsFiles, given: tuple[str, ...], said: str,
) -> None:
    """print-shop-ux, Q1: never plain HTTP for a half-done setup."""
    values = {"PHONE_NAME": NAME, "PHONE_TLS_CERT": str(files.cert),
              "PHONE_TLS_KEY": str(files.key)}
    with pytest.raises(HomeRefused) as refused:
        Home.from_settings(_settings(tmp_path, "".join(f"{k}={values[k]}\n" for k in given)))
    assert str(refused.value).startswith(said)
    assert "isn't served as plain HTTP" in str(refused.value)


@pytest.mark.parametrize("name", [
    MAC, "*.made-up.test", f"{NAME}:8765", "print", "print..made-up.test", "-print.made-up.test",
    "print.made-up.test.", "print_shop.made-up.test",
])
def test_the_pages_name_is_a_name(tmp_path: Path, files: TlsFiles, name: str) -> None:
    with pytest.raises(HomeRefused, match="PHONE_NAME"):
        Home.from_settings(_settings(tmp_path, (
            f"PHONE_NAME={name}\nPHONE_TLS_CERT={files.cert}\nPHONE_TLS_KEY={files.key}\n")))


def test_443_is_refused_over_https_as_80_is(files: TlsFiles) -> None:
    with pytest.raises(HomeRefused, match="can't be 443 over HTTPS"):
        https_home(files, port=443)
    with pytest.raises(HomeRefused, match="can't be 80"):
        https_home(files, port=80)
    assert plain_home(443).origin.startswith("http:"), "over plain HTTP, 443 is just a port"


# --------------------------------------------------------------------------
# Done when: a certificate that doesn't name PHONE_NAME, or is out of date, keeps the page off
# --------------------------------------------------------------------------


def _off(home: Home, phones: Phones) -> str:
    """Why the page is off. It's said before listening: `home`'s address isn't this machine's,
    and the certificate's words come first."""
    with pytest.raises(PhoneOff) as off:
        make_server(home=home, phones=phones, starts=None)
    said = str(off.value)
    assert said.startswith("the phone's page is off: "), said
    return said


def test_a_certificate_that_doesnt_name_the_page_keeps_it_off(tmp_path: Path,
                                                              phones: Phones) -> None:
    other = make(tmp_path / "other", ("other.made-up.test",))
    said = _off(https_home(other), phones)
    assert f"isn't for {NAME}: it names other.made-up.test" in said


@pytest.mark.parametrize(("names", "words"), [
    (("*.made-up.test",), "its certificate is a wildcard's (*.made-up.test)"),
    ((NAME, "*.made-up.test"), "its certificate is a wildcard's (*.made-up.test)"),
    ((NAME, "nas.made-up.test"), f"its certificate names more than {NAME} (nas.made-up.test)"),
])
def test_a_wildcard_or_more_names_than_the_pages_keep_it_off(
    tmp_path: Path, phones: Phones, names: tuple[str, ...], words: str,
) -> None:
    """print-shop-ux, Q2: a certificate for the page's name alone, held in code."""
    said = _off(https_home(make(tmp_path / "many", names)), phones)
    assert words in said
    assert f"{ONE_NAME}: use one for {NAME} alone" in said


def test_an_out_of_date_certificate_keeps_it_off(tmp_path: Path, phones: Phones) -> None:
    ran_out = make(tmp_path / "old", starts=timedelta(days=-90), lasts=timedelta(days=-1))
    runs_out = x509.load_pem_x509_certificate(ran_out.cert.read_bytes()).not_valid_after_utc
    said = _off(https_home(ran_out), phones)
    assert (f"its certificate ran out on {day(runs_out)}, and no good renewal is in "
            f"{ran_out.folder}") in said
    early = make(tmp_path / "early", starts=timedelta(days=2))
    starts = x509.load_pem_x509_certificate(early.cert.read_bytes()).not_valid_before_utc
    assert f"its certificate isn't valid until {day(starts)}" in _off(https_home(early), phones)


def test_a_key_that_isnt_the_certificates_or_a_file_that_isnt_one_keeps_it_off(
    tmp_path: Path, phones: Phones, files: TlsFiles,
) -> None:
    other = make(tmp_path / "other")
    crossed = TlsFiles(files.cert, other.key)
    assert f"the key in {other.key} isn't the certificate's own" in _off(
        https_home(crossed), phones)
    missing = TlsFiles(tmp_path / "nowhere.pem", files.key)
    assert "its certificate or key can't be read" in _off(https_home(missing), phones)
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate")
    assert "isn't a PEM certificate" in _off(https_home(TlsFiles(junk, files.key)), phones)


def test_no_refusal_or_log_line_holds_the_key(tmp_path: Path, phones: Phones, files: TlsFiles,
                                             caplog: pytest.LogCaptureFixture) -> None:
    key = files.key.read_text()
    secret_line = key.splitlines()[1]
    words = []
    for bad in (TlsFiles(files.cert, make(tmp_path / "x").key),
                make(tmp_path / "y", ("other.made-up.test",)),
                TlsFiles(files.key, files.key)):
        with pytest.raises(CertificateRefused) as refused:
            Certificate(bad, NAME).load()
        words.append(str(refused.value))
    assert all(secret_line not in said and "PRIVATE KEY" not in said for said in words)
    assert secret_line not in caplog.text


# --------------------------------------------------------------------------
# Done when: with the two paths set, the page answers on HTTPS only
# --------------------------------------------------------------------------


def _plain(port: int, request: bytes) -> bytes:
    """What a plain-HTTP request to the port gets back: closed, or reset."""
    got = b""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(request)
        try:
            while chunk := raw.recv(65536):
                got += chunk
        except ConnectionResetError:
            pass
    return got


def test_over_https_the_page_answers_and_plain_http_gets_nothing(
    phones: Phones, files: TlsFiles,
) -> None:
    port = free_port()
    home = https_home(files, port)
    with running(home, phones):
        assert served("127.0.0.1", port, files).serial_number == serial(files)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, \
                phone_context(files).wrap_socket(raw, server_hostname=NAME) as conn:
            conn.sendall(f"GET /pair HTTP/1.1\r\nHost: {home.host}\r\n\r\n".encode())
            reply = conn.recv(65536)
        assert reply.startswith(b"HTTP/1.0 ")
        got = _plain(port, f"GET /pair HTTP/1.1\r\nHost: {home.host}\r\n\r\n".encode())
        assert b"HTTP/" not in got and b"pair" not in got, "no page over plain HTTP"


def test_over_https_the_address_isnt_the_pages_own_host(phones: Phones, files: TlsFiles) -> None:
    """Host and Origin are the name's: the address is another host now, as a rebinding is."""
    port = free_port()
    with running(https_home(files, port), phones), \
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw, \
            phone_context(files).wrap_socket(raw, server_hostname=NAME) as conn:
        conn.sendall(f"GET / HTTP/1.1\r\nHost: {MAC}:{port}\r\n\r\n".encode())
        reply = b""
        while chunk := conn.recv(65536):
            reply += chunk
    assert b" 421 " in reply.split(b"\r\n")[0]
    assert f"https://{NAME}:{port}/".encode() in reply


def test_a_phone_that_asks_for_another_name_isnt_given_the_page(
    phones: Phones, files: TlsFiles,
) -> None:
    port = free_port()
    with running(https_home(files, port), phones), \
            socket.create_connection(("127.0.0.1", port), timeout=5) as raw, \
            pytest.raises(ssl.SSLCertVerificationError):
        phone_context(files).wrap_socket(raw, server_hostname="other.made-up.test")


# --------------------------------------------------------------------------
# Done when: a renewed certificate on disk is served without a restart
# --------------------------------------------------------------------------


def test_a_renewed_certificate_is_served_without_a_restart(
    phones: Phones, files: TlsFiles, caplog: pytest.LogCaptureFixture,
) -> None:
    port = free_port()
    with running(https_home(files, port), phones):
        first = serial(files)
        assert served("127.0.0.1", port, files).serial_number == first
        make(files.folder)  # the copy job, after a renewal: a new key and certificate
        renewed = serial(files)
        assert renewed != first
        assert served("127.0.0.1", port, files).serial_number == renewed
    assert "serves its renewed certificate" in caplog.text


def test_a_pair_caught_mid_copy_keeps_the_one_before_until_the_key_follows(
    tmp_path: Path, phones: Phones, files: TlsFiles, caplog: pytest.LogCaptureFixture,
) -> None:
    before = TlsFiles(tmp_path / "before.pem", tmp_path / "before.key")
    shutil.copy(files.cert, before.cert)
    port = free_port()
    with running(https_home(files, port), phones):
        new = make(tmp_path / "new")
        shutil.copy(new.cert, files.cert)  # the certificate copied, its key not yet
        for _ in range(2):
            assert served("127.0.0.1", port, before).serial_number == serial(before)
        assert caplog.text.count("wasn't taken") == 1, "said once, not per connection"
        assert "isn't the certificate's own" in caplog.text
        shutil.copy(new.key, files.key)
        assert served("127.0.0.1", port, new).serial_number == serial(new)


# --------------------------------------------------------------------------
# print-shop-ux's Q3: running out is said, and warned of before
# --------------------------------------------------------------------------


def test_once_it_runs_out_the_page_answers_nothing_and_says_why_until_a_renewal(
    phones: Phones, files: TlsFiles, caplog: pytest.LogCaptureFixture,
) -> None:
    now = [datetime.now(UTC)]
    port = free_port()
    with running(https_home(files, port), phones, clock=lambda: now[0]) as server:
        assert server.trouble() == ""
        runs_out = server.certificate.runs_out if server.certificate else None
        assert runs_out is not None
        now[0] = runs_out + timedelta(minutes=1)
        why = (f"its certificate ran out on {day(runs_out)}, and no good renewal is in "
               f"{files.folder}")
        assert server.trouble() == why
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, \
                    pytest.raises((ssl.SSLError, ConnectionError)):
                phone_context(files).wrap_socket(raw, server_hostname=NAME)
        assert caplog.text.count(f"The phone's page is off: {why}") == 1
        make(files.folder, lasts=timedelta(days=120))
        assert served("127.0.0.1", port, files).serial_number == serial(files)
        assert server.trouble() == ""


@pytest.mark.parametrize(("lasts", "warned"), [(timedelta(days=10), True),
                                                (RENEW_WARN + timedelta(days=1), False)])
def test_a_certificate_running_out_within_14_days_is_said_at_the_start(
    tmp_path: Path, phones: Phones, lasts: timedelta, warned: bool,
) -> None:
    files = make(tmp_path / "tls", lasts=lasts)
    runs_out = x509.load_pem_x509_certificate(files.cert.read_bytes()).not_valid_after_utc
    with running(https_home(files, free_port()), phones) as server:
        said = (f"The phone's certificate runs out on {day(runs_out)} and hasn't been renewed: "
                "check the job that copies it.")
        assert (said in server.said) == warned


# --------------------------------------------------------------------------
# The move to HTTPS: every phone pairs again
# --------------------------------------------------------------------------


def _pair(phones: Phones, name: str) -> None:
    assert isinstance(phones.pair(phones.mint(name)), tuple)


def test_the_move_to_https_signs_out_sign_ins_made_over_plain_http_and_says_pair_again(
    phones: Phones, files: TlsFiles,
) -> None:
    port = free_port()
    with running(plain_home(port), phones) as server:
        assert server.said == ()
    _pair(phones, "Made-up phone")
    with running(https_home(files, port), phones) as server:
        assert server.said[0] == (
            f"The phone's page is now https://{NAME}:{port}/: pair your phone again with "
            "`phone.py pair`. Signed out, as each sign-in went over plain HTTP: Made-up phone.")
    assert phones.all() == []
    _pair(phones, "Made-up phone")
    with running(https_home(files, port), phones) as server:
        assert server.said == (), "said once, the first time"
    assert [p.name for p in phones.all()] == ["Made-up phone"], "a sign-in over HTTPS is kept"


def test_phones_paired_before_the_page_kept_its_origin_are_signed_out_too(
    phones: Phones, files: TlsFiles,
) -> None:
    _pair(phones, "Made-up phone")  # paired before this change: over plain HTTP
    with running(https_home(files, free_port()), phones) as server:
        assert "pair your phone again" in server.said[0]
    assert phones.all() == []


def test_a_first_start_over_https_with_no_phone_says_nothing_of_pairing_again(
    phones: Phones, files: TlsFiles,
) -> None:
    with running(https_home(files, free_port()), phones) as server:
        assert server.said == ()


def test_back_to_plain_http_and_over_https_again_signs_out_again(phones: Phones,
                                                                 files: TlsFiles) -> None:
    port = free_port()
    with running(https_home(files, port), phones):
        pass
    with running(plain_home(port), phones):
        pass
    _pair(phones, "Made-up phone")  # over plain HTTP again
    with running(https_home(files, port), phones):
        pass
    assert phones.all() == []


# --------------------------------------------------------------------------
# The links print-shop gives, and what the run says
# --------------------------------------------------------------------------


@pytest.fixture
def print_job(monkeypatch: pytest.MonkeyPatch) -> Any:
    script = _load("print_job")
    # On loopback, as tests only can: the made-up home address isn't this machine's.
    monkeypatch.setattr(script, "make_server", functools.partial(make_server,
                                                                  listen_on="127.0.0.1"))
    return script


def test_the_runs_line_and_the_cards_links_are_https(
    tmp_path: Path, files: TlsFiles, print_job: Any, capsys: pytest.CaptureFixture[str],
) -> None:
    port = free_port()
    settings = _settings(tmp_path, (
        f"PHONE_PORT={port}\nPHONE_NAME={NAME}\nPHONE_TLS_CERT={files.cert}\n"
        f"PHONE_TLS_KEY={files.key}\nPRINT_SHOP_PHONES={tmp_path / 'phones.sqlite'}\n"))
    page = print_job.PhonePage(settings, None)
    try:
        assert page.on
        origin = f"https://{NAME}:{port}"
        # What the card links to: "Start it on your phone", and "The queue, on your phone".
        assert page.address == f"{origin}/start" and page.main == f"{origin}/"
        assert f"Your phone's page: {origin}/ , and its Start: {origin}/start" in (
            capsys.readouterr().out)
        assert page.trouble() == ""
    finally:
        page._server.server_close()


def test_a_half_done_setup_is_said_at_the_runs_start_and_on_the_live_line(
    tmp_path: Path, files: TlsFiles, print_job: Any, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = _settings(tmp_path, f"PHONE_NAME={NAME}\nPHONE_TLS_CERT={files.cert}\n")
    page = print_job.PhonePage(settings, None)
    said = ("The phone's Start is off: PHONE_TLS_KEY isn't set (PHONE_NAME and PHONE_TLS_CERT "
            "are)")
    assert not page.on
    assert said in capsys.readouterr().out
    assert page.trouble().startswith(said)


def test_an_out_of_date_certificate_is_said_at_the_runs_start(
    tmp_path: Path, print_job: Any, capsys: pytest.CaptureFixture[str],
) -> None:
    old = make(tmp_path / "old", starts=timedelta(days=-90), lasts=timedelta(days=-1))
    settings = _settings(tmp_path, (
        f"PHONE_PORT={free_port()}\nPHONE_NAME={NAME}\nPHONE_TLS_CERT={old.cert}\n"
        f"PHONE_TLS_KEY={old.key}\nPRINT_SHOP_PHONES={tmp_path / 'phones.sqlite'}\n"))
    page = print_job.PhonePage(settings, None)
    out = capsys.readouterr().out
    assert not page.on
    assert "The phone's page is off: its certificate ran out on " in out
    assert f"and no good renewal is in {old.folder}." in out
    assert page.trouble() == out.strip().splitlines()[-1]


def test_home_assistants_notices_are_told_the_https_address() -> None:
    """The notices' link is built in Home Assistant, from the page's address set in the
    automation: its words say the https one, once the page is on HTTPS."""
    text = (Path(__file__).parents[1] / "ha-configs" / "print_shop_notices.yaml").read_text()
    assert "https://print.<domain>:<PHONE_PORT>" in text


class _Queue:
    def lines(self) -> list[Any]:
        return []

    def said(self, since: int) -> list[str]:
        return []


def test_the_queues_live_line_says_the_page_is_off() -> None:
    term = _Terminal(_Queue(), None, input, print, lambda: None, None)  # type: ignore[arg-type]
    assert "phone" not in term.live()
    why = ["The phone's page is off: its certificate ran out on 2026-11-20, and no good "
           "renewal is in the folder."]
    term.phone_trouble = lambda: why[0]
    assert why[0].rstrip(".") in term.live()
    why[0] = ""
    assert "phone" not in term.live()


def test_logging_names_no_file_contents(phones: Phones, files: TlsFiles,
                                        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    port = free_port()
    with running(https_home(files, port), phones):
        assert _plain(port, b"GET / HTTP/1.1\r\n\r\n") == b""
    assert "no HTTPS" in caplog.text
    assert "PRIVATE KEY" not in caplog.text and "CERTIFICATE-----" not in caplog.text
