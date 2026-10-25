"""Run one print job, or serve the outcome gate, on the Mac.

    uv run python scripts/print_job.py PART.stl [--record]
    uv run python scripts/print_job.py --serve [--record]
    uv run python scripts/print_job.py --list

**This is the only place that arms the printer,** and only when the invocation
says so: `PRINT_AGENT_ALLOW_HARDWARE=1` on this command's own line. Without it
the job slices, parks at the approval gate in Home Assistant, and an approval
fails at the print node instead of starting anything. It lives outside the
package so that nothing importable can arm the printer for itself.

With a print started, it keeps running, watching the printer read-only, until
the outcome is recorded in Home Assistant. Stopping it (Ctrl-C) leaves the
ledger row open; `--serve` asks about it again later. The first deliberate
print follows `docs/runbook-first-print.md`.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from print_shop.arming import ALLOW_HARDWARE_ENV, hardware_allowed
from print_shop.env import SETTINGS_PATH
from print_shop.ledger import Ledger, ledger_path
from print_shop.printer.centauri import FrameRecorder, PrinterClient, live_printer
from print_shop.runtime import build_run, describe, recording_path

APPROVAL_WAIT_S = 3600.0  # the daemon's own gate gives up after an hour
FINISH_WAIT_S = 900.0  # after the press: preflight, upload, and a five-minute start watch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stl", nargs="?", type=Path, help="the part to print")
    parser.add_argument("--serve", action="store_true", help="only watch and ask outcomes")
    parser.add_argument("--list", action="store_true", help="print the ledger and exit")
    parser.add_argument("--record", action="store_true", help="record the printer's replies")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_PATH, help="the settings file (a path)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        with Ledger(ledger_path(args.settings)) as ledger:
            rows = ledger.rows()
            print("\n\n".join(describe(r) for r in rows) if rows else "the ledger is empty")
        return 0
    if not args.serve and args.stl is None:
        parser.error("give a part to print, or --serve, or --list")
    if args.stl is not None and not args.stl.is_file():
        parser.error(f"{args.stl} is not a file")

    recorder = FrameRecorder(recording_path()) if args.record else None
    printer: PrinterClient | None = None
    if args.stl is not None and hardware_allowed():
        printer = live_printer(recorder=recorder)
        print(f"ARMED by {ALLOW_HARDWARE_ENV}=1: an approval in Home Assistant starts the print.")
    elif args.stl is not None:
        print(f"Not armed ({ALLOW_HARDWARE_ENV} is not 1): an approval will NOT start a print.")
    if recorder is not None:
        print(f"Recording the printer's replies to {recorder.path}")

    run = build_run(printer=printer, recorder=recorder, settings=args.settings)
    try:
        with run.daemon:
            if args.serve:
                print("Serving the outcome gate. Ctrl-C to stop; open prints stay open.")
                while True:
                    time.sleep(1)

            job_id = run.daemon.submit(args.stl)
            print(f"Job {job_id}: slicing, then waiting for Approve in Home Assistant.")
            run.daemon.wait_for_gate(job_id, timeout_s=APPROVAL_WAIT_S)
            print("At the approval gate. Approve or Reject on the dashboard.")
            result = run.daemon.wait_for_finish(job_id, timeout_s=APPROVAL_WAIT_S + FINISH_WAIT_S)
            if result.error:
                print(f"The job ended without printing: {result.error}")
            row = run.ledger.get(job_id)
            if row is None:
                print("Nothing was started, so the ledger has no row for this job.")
                return 0 if not result.error else 1
            print("Started. Watching the printer read-only; answer in Home Assistant when it ends.")
            while True:
                row = run.ledger.get(job_id)
                if row is not None and row.result is not None:
                    break
                time.sleep(5)
            print("\n" + describe(row))
            return 0
    except KeyboardInterrupt:
        print("\nStopped. Any print without an answer stays open in the ledger.")
        return 130
    finally:
        run.ledger.close()


if __name__ == "__main__":
    sys.exit(main())
