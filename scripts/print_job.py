"""Run one print job, or serve the outcome gate, on the Mac.

    uv run python scripts/print_job.py PART.stl --nozzle 0.4 --filament NAME [--intake F] [--record]
    uv run python scripts/print_job.py --serve [--record]
    uv run python scripts/print_job.py --list
    uv run python scripts/print_job.py --filaments

**The Sponsor says what's fitted:** the nozzle (mm) and the loaded filament,
by variety ("Elegoo Rapid PETG") or by the name of one of their tunings.
Without them a print is refused. `--filaments` lists what's in `profiles/`.
Their tuning is used where there is one, the standard where there isn't;
several tunings of one are listed to choose from. `--machine` and
`--process` name a profile, when the Sponsor picks.

**Then a few questions about the part,** proposed from its shape once it's
oriented: each with numbered options, what each would cost (Orca's estimate)
and one recommended. Answer by number, or `o` and your own words. A part
whose shape leaves nothing open gets none. `--answers file.json` replays
recorded answers. The settings are then proposed and shown here for review:
`ok`, or correct a setting by name and it slices again. Home Assistant's
approval gate comes after that, unchanged.

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
from print_shop.runtime import WORK_DIR, build_run, describe, profiles_for, recording_path
from print_shop.setup.intake import Asker, cli_asker, replay_asker
from print_shop.setup.review import cli_reviewer
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import STORE_DIR, NoChoice, Store, choose
from print_shop.slicer.store import describe as describe_store

APPROVAL_WAIT_S = 3600.0  # the daemon's own gate gives up after an hour
FINISH_WAIT_S = 900.0  # after the press: preflight, upload, and a five-minute start watch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stl", nargs="?", type=Path, help="the part to print")
    parser.add_argument("--serve", action="store_true", help="only watch and ask outcomes")
    parser.add_argument("--list", action="store_true", help="print the ledger and exit")
    parser.add_argument("--filaments", action="store_true", help="list profiles/ and exit")
    parser.add_argument("--record", action="store_true", help="record the printer's replies")
    parser.add_argument("--nozzle", type=float, help="the fitted nozzle, in mm (0.4)")
    parser.add_argument("--filament", help="the loaded filament: its variety, or a tuning's name")
    parser.add_argument("--machine", help="a machine profile's exact name, over the pairing")
    parser.add_argument("--process", help="a process profile's exact name, over the pairing")
    parser.add_argument("--answers", type=Path, help="recorded answers to the questions, as JSON")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_PATH, help="the settings file (a path)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.filaments:
        print(describe_store(Store(STORE_DIR)))
        return 0
    if args.list:
        with Ledger(ledger_path(args.settings)) as ledger:
            rows = ledger.rows()
            print("\n\n".join(describe(r) for r in rows) if rows else "the ledger is empty")
        return 0
    if not args.serve and args.stl is None:
        parser.error("give a part to print, or --serve, or --list")
    if args.stl is not None and not args.stl.is_file():
        parser.error(f"{args.stl} is not a file")

    profiles: SlicerProfiles | None = None
    asker: Asker | None = None
    if args.stl is not None:
        if args.nozzle is None or not args.filament:
            parser.error("say what's fitted: --nozzle (mm) and --filament (PLA, or a name)")
        try:
            choice = choose(
                Store(STORE_DIR),
                nozzle_mm=args.nozzle,
                filament=args.filament,
                machine=args.machine,
                process=args.process,
            )
        except NoChoice as exc:
            print(exc, file=sys.stderr)
            return 2
        profiles = profiles_for(choice, STORE_DIR, WORK_DIR / "composed")
        print(f"Fitted: a {args.nozzle:g} mm nozzle and {args.filament}. Slicing with:")
        print(f"  machine   {choice.machine.name}")
        print(f"  process   {choice.process.name}")
        print(f"  filament  {choice.filament.name}")
        for note in choice.notes:
            print(f"  ({note})")
        try:
            asker = replay_asker(args.answers) if args.answers else cli_asker()
        except ValueError as exc:
            parser.error(str(exc))

    recorder = FrameRecorder(recording_path()) if args.record else None
    printer: PrinterClient | None = None
    if args.stl is not None and hardware_allowed():
        printer = live_printer(recorder=recorder)
        print(f"ARMED by {ALLOW_HARDWARE_ENV}=1: an approval in Home Assistant starts the print.")
    elif args.stl is not None:
        print(f"Not armed ({ALLOW_HARDWARE_ENV} is not 1): an approval will NOT start a print.")
    if recorder is not None:
        print(f"Recording the printer's replies to {recorder.path}")

    run = build_run(
        printer=printer,
        profiles=profiles,
        recorder=recorder,
        settings=args.settings,
        asker=asker,
        reviewer=cli_reviewer(),
    )
    try:
        with run.daemon:
            if args.serve:
                print("Serving the outcome gate. Ctrl-C to stop; open prints stay open.")
                while True:
                    time.sleep(1)

            job_id = run.daemon.submit(args.stl)
            print(f"Job {job_id}: orienting, then a few questions about the part, here.")
            run.daemon.wait_for_gate(job_id, timeout_s=APPROVAL_WAIT_S)
            gate = run.daemon.pending_gate() or {}
            print("At the approval gate. Approve or Reject on the dashboard.")
            print(f"  time        {int(gate.get('print_time_s') or 0) // 60} min")
            print(f"  plastic     {gate.get('filament_cm3')} cm3")
            print(f"  turned      {gate.get('orientation_deg')}")
            print(f"  because     {gate.get('orientation_rationale')}")
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
