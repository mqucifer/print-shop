"""The phones paired at the Mac, and the one pairing code waiting (mqucifer/print-shop#214).

A phone is signed in by a random 256-bit secret, set in its cookie when it's paired. This file
keeps only the secret's SHA-256, the name the person gave it and when it was paired: a hash
isn't a sign-in, so nothing on the Mac, in the settings or in Home Assistant can sign in.

Pairing takes a code minted at the Mac's own terminal (`scripts/phone.py pair`): 10 characters
of Crockford's base32 (about 50 bits), valid for `CODE_LASTS_S`, used once, and dead after
`WRONG_TRIES` wrong tries. Only its hash is kept. One code waits at a time: a new one replaces it.

`forget` deletes a phone's row, and every request looks its sign-in up here, so a forgotten
phone is signed out at once.

The page's origin is kept too (`served_at`). When the page first serves over HTTPS
(mqucifer/print-shop#312), every phone paired before is signed out: its sign-in went over plain
HTTP, where a copy of it could be read, so a copy mustn't sign in over HTTPS.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager, suppress
from pathlib import Path

from pydantic import BaseModel

from print_shop.env import SETTINGS_PATH, load_env, refused

__all__ = [
    "ALPHABET", "CODE_LASTS_S", "CODE_LENGTH", "DEFAULT_PATH", "Paired", "Phones", "WRONG_TRIES",
    "normal_code", "phones_path",
]

#: Where the paired phones are kept, beside the ledger.
DEFAULT_PATH = Path("~/print-shop/phones.sqlite").expanduser()

#: Where the settings may put the paired phones' file instead.
PATH_KEY = "PRINT_SHOP_PHONES"


def phones_path(settings: Path | None = None) -> Path:
    """The paired phones' file: `PRINT_SHOP_PHONES` in the settings, or the default."""
    value = load_env(settings or SETTINGS_PATH).get(PATH_KEY)
    return Path(value).expanduser() if value else DEFAULT_PATH


#: Crockford's base32: no I, L, O or U, so nothing is misread.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10
#: How long a pairing code lasts.
CODE_LASTS_S = 600.0
#: Wrong tries before the code dies.
WRONG_TRIES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS phones (
    name           TEXT PRIMARY KEY,
    secret_sha256  TEXT NOT NULL UNIQUE,
    paired_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pairing (
    one            INTEGER PRIMARY KEY CHECK (one = 1),
    name           TEXT NOT NULL,
    code_sha256    TEXT NOT NULL,
    expires_at     REAL NOT NULL,
    wrong_tries    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS page (
    one            INTEGER PRIMARY KEY CHECK (one = 1),
    origin         TEXT NOT NULL
);
"""


class Paired(BaseModel):
    """A phone that's signed in: its name, and when it was paired (epoch seconds)."""

    name: str
    paired_at: float


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def normal_code(typed: str) -> str:
    """A code as typed, read as Crockford's base32 reads it: case and spaces and dashes don't
    count, and O, I and L are the 0 and 1 they look like."""
    out = typed.upper().replace("-", "").replace(" ", "")
    return out.translate(str.maketrans("OIL", "011"))


class Phones:
    """The paired phones' file. Opened per call, so a pairing minted by `phone.py` is seen by
    the page a running job serves, and a phone forgotten there is signed out here at once."""

    def __init__(self, path: Path = DEFAULT_PATH, *,
                 clock: Callable[[], float] = time.time) -> None:
        refused(path, "the paired phones")  # under tests, never the Mac's
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        """The file, opened for one call and closed after it, readable by its owner only."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, isolation_level=None, timeout=5)) as conn:
            with suppress(OSError):
                self.path.chmod(0o600)
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            yield conn

    def mint(self, name: str) -> str:
        """A new pairing code for a phone to be called `name`, replacing any waiting. Only
        `scripts/phone.py pair`, at the Mac's own terminal, calls this.

        Raises:
            ValueError: If `name` is empty or too long.
        """
        name = " ".join(name.split())
        if not name or len(name) > 60:
            raise ValueError("a phone's name is 1 to 60 characters")
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        with self._lock, self._open() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pairing (one, name, code_sha256, expires_at, wrong_tries)"
                " VALUES (1, ?, ?, ?, 0)",
                (name, _sha256(code), self._clock() + CODE_LASTS_S))
        return code

    def pair(self, typed: str) -> tuple[str, str] | str:
        """Pair a phone with the code it typed: (its name, its new secret), to set in its cookie;
        or why not, in words. A right code is spent; a wrong one counts, and the code dies at
        `WRONG_TRIES`."""
        code = normal_code(typed)
        with self._lock, self._open() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                waiting = conn.execute("SELECT * FROM pairing WHERE one = 1").fetchone()
                if waiting is None:
                    said: tuple[str, str] | str = (
                        "No code is waiting: run `phone.py pair` at the Mac for a new one")
                elif waiting["expires_at"] <= self._clock():
                    conn.execute("DELETE FROM pairing")
                    said = "That code has expired: run `phone.py pair` at the Mac for a new one"
                elif not hmac.compare_digest(_sha256(code), waiting["code_sha256"]):
                    tries = waiting["wrong_tries"] + 1
                    if tries >= WRONG_TRIES:
                        conn.execute("DELETE FROM pairing")
                        said = (f"That code is wrong, {WRONG_TRIES} times, so it's dead: run "
                                "`phone.py pair` at the Mac for a new one")
                    else:
                        conn.execute("UPDATE pairing SET wrong_tries = ?", (tries,))
                        said = (f"That code is wrong. {WRONG_TRIES - tries} more wrong, and it "
                                "dies")
                else:
                    secret = secrets.token_urlsafe(32)  # 256 bits
                    conn.execute("DELETE FROM pairing")
                    conn.execute(
                        "INSERT OR REPLACE INTO phones (name, secret_sha256, paired_at) "
                        "VALUES (?, ?, ?)", (waiting["name"], _sha256(secret), self._clock()))
                    said = (waiting["name"], secret)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return said

    def signed_in(self, secret: str) -> Paired | None:
        """The phone whose cookie holds `secret`, or None."""
        if not secret or len(secret) > 200:
            return None
        with self._lock, self._open() as conn:
            found = conn.execute("SELECT name, paired_at FROM phones WHERE secret_sha256 = ?",
                                 (_sha256(secret),)).fetchone()
        return Paired(name=found["name"], paired_at=found["paired_at"]) if (
            found is not None) else None

    def all(self) -> list[Paired]:
        """The paired phones, the oldest first."""
        with self._lock, self._open() as conn:
            rows = conn.execute("SELECT name, paired_at FROM phones ORDER BY paired_at").fetchall()
        return [Paired(name=r["name"], paired_at=r["paired_at"]) for r in rows]

    def forget(self, name: str) -> bool:
        """Sign the phone called `name` out, at once. True if there was one."""
        with self._lock, self._open() as conn:
            gone = conn.execute("DELETE FROM phones WHERE name = ?", (name,)).rowcount
        return gone > 0

    def served_at(self, origin: str) -> tuple[str | None, list[str]]:
        """The page serves at `origin` now: the origin it served at before (None if none was
        kept), and the phones signed out by the move. Moving to HTTPS from plain HTTP, or from
        before origins were kept, signs out every phone paired before: each sign-in went over
        plain HTTP (mqucifer/print-shop#312)."""
        with self._lock, self._open() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("SELECT origin FROM page WHERE one = 1").fetchone()
                before: str | None = row["origin"] if row is not None else None
                out: list[str] = []
                if (origin != before and origin.startswith("https:")
                        and not (before or "").startswith("https:")):
                    out = [r["name"] for r in conn.execute(
                        "SELECT name FROM phones ORDER BY paired_at").fetchall()]
                    conn.execute("DELETE FROM phones")
                conn.execute("INSERT OR REPLACE INTO page (one, origin) VALUES (1, ?)", (origin,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return before, out
