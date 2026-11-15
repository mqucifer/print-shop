"""Serve the print desk on the Mac: every job, what's loaded, and how to run a job again.

    uv run python scripts/print_desk.py [--port 8719]

Then open http://127.0.0.1:8719/ in a browser on the Mac. It listens on 127.0.0.1 only, so
nothing else on the network can reach it. Ctrl-C stops it.

It reads the ledger (`PRINT_SHOP_LEDGER` in the settings, default `~/print-shop/ledger.sqlite`)
read-only, and the jobs folder (`~/print-shop/jobs`), and writes nothing. It never starts a job,
answers a question or arms the printer (mqucifer/print-shop#139, `print_shop.desk`).
`--ledger` and `--jobs` point it elsewhere, at a copy or a test's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from print_shop.desk import HOST, Desk, make_server
from print_shop.env import SETTINGS_PATH
from print_shop.ledger import ledger_path
from print_shop.runtime import WORK_DIR

PORT = 8719


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=PORT, help=f"the port ({PORT})")
    parser.add_argument("--ledger", type=Path, help="the ledger file, if not the settings'")
    parser.add_argument("--jobs", type=Path, default=WORK_DIR, help="the jobs folder")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_PATH, help="the settings file (a path)"
    )
    args = parser.parse_args(argv)
    ledger = args.ledger or ledger_path(args.settings)
    server = make_server(Desk(ledger, args.jobs), args.port)
    port = server.server_address[1]
    print(f"The print desk: http://{HOST}:{port}/  (reading {ledger} and {args.jobs}; "
          "Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
