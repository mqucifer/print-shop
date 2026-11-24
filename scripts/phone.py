"""The phones that may start a print, on the home Wi-Fi (mqucifer/print-shop#214).

    uv run python scripts/phone.py pair "Sponsor's phone"   # at the Mac's own terminal
    uv run python scripts/phone.py list
    uv run python scripts/phone.py forget "Sponsor's phone"

`pair` shows the Start page's address and a one-time code: 10 characters, valid for 10 minutes,
used once, and dead after 5 wrong tries. Open the address on the phone, on the home Wi-Fi, tap
"I have a code from the Mac", and type it: that phone is signed in. Only a hash of its sign-in is
kept on the Mac. `pair` runs only at the Mac's own terminal: not from a script, a session's
shell, or a login from elsewhere. Where no print-shop run is serving the page, `pair` serves the
pairing page itself until the phone is paired or the code dies.

`forget` signs a phone out at once. `list` shows the paired phones.

The page is set up by `PHONE_HOME` in the settings: the Mac's address on the home Wi-Fi with its
network, such as `192.168.1.20/24` (reserve it at the router). Optional: `PHONE_PORT` (8765), and
`PHONE_ROUTER` where the default route can't say the router's address. Nothing here arms: a
signed-in phone's Start does, for one start, on the home Wi-Fi (`docs/runbook-first-print.md`).
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TextIO

from print_shop.env import SETTINGS_PATH
from print_shop.phone.home import HOME_KEY, Home, HomeRefused
from print_shop.phone.phones import CODE_LASTS_S, WRONG_TRIES, Phones, phones_path
from print_shop.phone.server import PhoneOff, make_server

#: Set in a login from elsewhere: pairing isn't done from there.
SSH_VARS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")


def at_the_macs_own_terminal(
    stdin: TextIO | None = None, stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Why this isn't the Mac's own terminal, or "" if it is: standard input and output are a
    terminal, and it isn't a login from elsewhere."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    environ = environ if environ is not None else os.environ
    if not (stdin.isatty() and stdout.isatty()):
        return "it isn't a terminal (a script or a session's shell)"
    if any(environ.get(var) for var in SSH_VARS):
        return "it's a login from elsewhere (SSH)"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--settings", type=Path, default=None,
                        help=f"the settings file (default {SETTINGS_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="sign a phone in, with a code typed on it")
    pair.add_argument("name", help="what to call the phone, as the ledger keeps who armed")
    sub.add_parser("list", help="the paired phones")
    forget = sub.add_parser("forget", help="sign a phone out, at once")
    forget.add_argument("name")
    args = parser.parse_args(argv)

    phones = Phones(phones_path(args.settings))
    if args.command == "list":
        paired = phones.all()
        if not paired:
            print("No phone is paired.")
        for one in paired:
            when = datetime.fromtimestamp(one.paired_at).strftime("%Y-%m-%d %H:%M")
            print(f"  {one.name}  (paired {when})")
        return 0
    if args.command == "forget":
        if phones.forget(args.name):
            print(f"{args.name} is signed out.")
            return 0
        print(f"No phone is called {args.name!r}: `phone.py list` shows them.", file=sys.stderr)
        return 1
    return _pair(phones, args.name, args.settings)


def _pair(phones: Phones, name: str, settings: Path | None) -> int:
    why = at_the_macs_own_terminal()
    if why:
        print(f"Not paired: pairing is done at the Mac's own terminal, and {why}.",
              file=sys.stderr)
        return 2
    try:
        home = Home.from_settings(settings)
    except HomeRefused as exc:
        print(f"Not paired: {exc}.", file=sys.stderr)
        return 2
    if home is None:
        print(f"Not paired: the phone's Start isn't set up. Set {HOME_KEY} in the settings to "
              "the Mac's address on the home Wi-Fi with its network, such as 192.168.1.20/24.",
              file=sys.stderr)
        return 2
    try:
        code = phones.mint(name)
    except ValueError as exc:
        print(f"Not paired: {exc}.", file=sys.stderr)
        return 2
    print(f"On the phone, on the home Wi-Fi, open {home.origin}/pair and type:\n")
    print(f"    {code[:5]} {code[5:]}\n")
    print(f"It's for {' '.join(name.split())}, lasts {CODE_LASTS_S / 60:g} minutes, works once, "
          f"and dies after {WRONG_TRIES} wrong tries.")
    try:
        server = make_server(home=home, phones=phones, starts=None)
    except PhoneOff as exc:
        if "in use" in str(exc):
            print("A print-shop run is serving the page: type the code there.")
            return 0
        print(f"{exc}.", file=sys.stderr)
        return 1
    print("Serving the pairing page until the phone is paired or the code dies. Ctrl-C stops.")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    began = time.time()
    try:
        while time.time() - began < CODE_LASTS_S:
            if any(p.name == " ".join(name.split()) and p.paired_at >= began
                   for p in phones.all()):
                print("Paired.")
                return 0
            time.sleep(1)
        print("The code has expired, and the phone wasn't paired.")
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
