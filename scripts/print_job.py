"""Run one print job, or serve the outcome gate, on the Mac.

    uv run python scripts/print_job.py PART.stl --nozzle 0.4 --filament NAME [--as-laid]
        [--context PATH] [--answers F] [--record]
    uv run python scripts/print_job.py A.stl B.stl C.stl [--as-laid] [--answers F] [--record]
    uv run python scripts/print_job.py --sliced FILE [--plate N] [--project P.3mf]
        [--loaded-ok] [--record]
    uv run python scripts/print_job.py --again JOB_ID [--loaded-ok] [--record]
    uv run python scripts/print_job.py --serve [--record]
    uv run python scripts/print_job.py --list
    uv run python scripts/print_job.py --filaments

**What's fitted and loaded comes from the inventory** (`scripts/stock.py`,
mqucifer/print-shop#11): the printer's fitted nozzle and loaded spool, which
the print names. `--nozzle` and `--filament` may say the same, and anything
else, not fitted or not loaded, is refused. Until the inventory is set up,
the print says so and takes `--nozzle` and `--filament` (by variety, "Elegoo
Rapid PETG", or by the name of one of the Sponsor's tunings), refusing
without them. `--filaments` lists what's in `profiles/`.
Their tuning is used where there is one, the standard where there isn't;
several tunings of one are listed to choose from. `--machine` and
`--process` name a profile, when the Sponsor picks.

**The part is shown as the file lays it, or on the side its context lays it,** and a turn of
the print loop's is proposed at the review (mqucifer/print-shop#242). With `--as-laid` no turn
is proposed: for a part already laid for printing, no orientation model is asked
(mqucifer/print-shop#128).

**The part's context goes with it** (mqucifer/print-shop#130): its fits, seen faces, purpose,
the side it's laid on and its holds, from a sidecar beside it (`PART.part.json`), or from the
design run whose folder it sits in. `--context` names a sidecar or a run's folder elsewhere. The
slice's seam and elephant foot read its fits, and the review says what was read, or that there
was none.
A sidecar's shape is in `docs/runbook-first-print.md`.

**Then a few questions about the part,** once it's oriented: each with numbered options, what
each would cost (Orca's estimate) and one recommended. Answer by number, or `o` and your own
words. What it's for and what's seen show at once, and need no model (mqucifer/print-shop#208).
The model's questions, from the part's shape, are added when they arrive. While you wait for
them the time shows, and `go` goes on without them. If the model has closer choices on what
it's for, Enter keeps the answer you gave. What the part's notes say of those two is read
back instead of asked (mqucifer/print-shop#236): Enter confirms it, a number changes one, and
with both in the notes nothing waits for the model's questions. `--answers file.json` replays
recorded answers, each naming its question by its text or its feature; a
file that doesn't answer exactly the questions asked is refused, and the job
ends saying which.

**The review shows at the first slice, and waits for no model** (mqucifer/print-shop#242): the
answers, the kept lessons, the guards and what the slice proposes. The model's settings are
proposals, added to the list by Enter once they're in, numbered on from the slice's: `take 2`
takes one, bare `take` stays the slice's, and `no 2 because ...` turns one down, saying why.
None is applied by arriving. `ok` goes on, saying once if it's without them; or correct a
setting by name and it slices again. Home Assistant's approval gate comes after that, unchanged.

**The questions and the review take as long as you take** (mqucifer/print-shop#125). The
hour for the slice to reach the approval gate starts when the review is approved; the gate
then waits an hour for Approve or Reject, and says by when. A wait that runs out ends the job
in one sentence, with a ledger row saying it timed out, and nothing printed.

**Each gate says which it is** (mqucifer/print-shop#200). A part whose mesh isn't closed stops
at the mesh gate first, before any question or slice: it's shown with its reason, the mesh's
facts, and what Approve and Reject each do there. Approved, the job goes on through the questions
and the review to the approval gate, the only gate that can start a print, and that is shown in
turn. Today such a job fails when that gate is answered, and nothing prints.

**Several parts are one job** (mqucifer/print-shop#109): name their files together, one file
twice for two of it. They're arranged on one plate and printed by object, one after another,
where the machine's clearances allow it, read from the head's moves in the G-code. Where they
don't, the plate prints by layer, and the review and the gate say why. Parts that don't fit one
plate, or a part whose mesh isn't closed, end the job unsliced. It's one set of questions, one
review and one gate, and once it has printed the outcome is asked part by part, with a ledger row
for each. Each part's context is read beside it, so `--context` is for one part. The print loop
reads one part, so a plate's poses and settings come from the single calls.

**A pause says what it's for** (mqucifer/print-shop#218): a swap (`colour_change=12.4 from #1 to
#2`, the stock's numbers) or an insert (`pause_at=2.6 for insert`), and what isn't typed is
asked. After a print with a swap print-shop doesn't know what's on the printer, and never moves
the stock by itself: before the next job prepares, at "From the inventory", it asks whether that
print is over and which spool is on, and one answer says both. Start holds until you say.

**A job starts only on what it was prepared for** (mqucifer/print-shop#218): the spool it was
sliced for, a swap's first, or another of the same variety, colour and tuning. An Approve with
anything else loaded starts nothing: it says what to load, and waits for a new press.

**This is the only place that arms the printer,** and only when the invocation
says so: `PRINT_AGENT_ALLOW_HARDWARE=1` on this command's own line. Without it
the job slices, parks at the approval gate in Home Assistant, and an approval
fails at the print node instead of starting anything. It lives outside the
package so that nothing importable can arm the printer for itself.

With a print started, it keeps running, watching the printer read-only, until
the outcome is recorded in Home Assistant. Stopping it (Ctrl-C) leaves the
ledger row open; `--serve` asks about it again later. The first deliberate
print follows `docs/runbook-first-print.md`.

**A file you sliced by hand in Orca goes through as it is,** with `--sliced FILE`
(mqucifer/print-shop#164): its G-code, or the plate's sliced file (`.gcode.3mf`), which also
carries each object's overrides (`--project` reads them from the Orca project it was sliced
from, where the sliced file doesn't carry them). Nothing is asked, proposed, turned or sliced
again, and no lesson is applied. Its bytes are what's sent: their sha256 is kept and checked
again just before the upload.

What's shown before the gate is what the ledger keeps: the settings from the file's
own config block, each that differs from the inventory's tuning for the loaded filament, each
object's overrides, Orca's estimate, and the slice's reading as a report with nothing proposed.
The arming rule, the approval gate, the watching, the frames and the outcome question are the
same as for any job. **`--again JOB_ID` prints a past hand-sliced job's file again,** as a new
job marked as a reprint of it, refused if the kept file has changed. Either is refused when the
file names another filament or nozzle than the inventory says is loaded and fitted, unless
`--loaded-ok` says to print it with what's loaded.

**One runs at a time** (mqucifer/print-shop#207). A part, `--sliced`, `--again` and `--serve`,
armed or not, first take a lock that goes when the process does. A second start says in one
sentence what's running and exits 2, having published and started nothing. `--list`, `--filaments`,
the print desk and `stock.py` run beside a job.

**What a job shows here is also kept in its folder,** as `jobs/<id>/job.log`, with each answer
typed, so it can be read after it's scrolled past (mqucifer/print-shop#139): by the print desk,
`scripts/print_desk.py`, and by the operator. No key, token or password is written to it.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from print_shop import sliced
from print_shop.arming import ALLOW_HARDWARE_ENV, hardware_allowed
from print_shop.core.contracts import Gate
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import STREAM_THREAD
from print_shop.inventory import SETUP, Inventory, InventoryError, Material
from print_shop.joblog import LOG_NAME, JobLog, Terminal, secrets
from print_shop.ledger import Ledger, ledger_path
from print_shop.opening import ThisJob
from print_shop.orchestrator.daemon import GATE_WAIT_S
from print_shop.orchestrator.live import AlreadyLive, Live, only_live
from print_shop.orchestrator.outcome import no_frames
from print_shop.orchestrator.spools import ask_on_now
from print_shop.printer.centauri import FrameRecorder, PrinterClient, live_printer
from print_shop.runtime import (
    WORK_DIR,
    Run,
    bed_type,
    build_run,
    describe,
    describe_all,
    describe_part,
    describe_plate,
    profiles_for,
    recording_path,
)
from print_shop.setup.context import read_context
from print_shop.setup.intake import Asker, cli_asker, drop_typed, replay_asker
from print_shop.setup.layout import terminal_width
from print_shop.setup.review import cli_reviewer
from print_shop.setup.suggest import THREAD
from print_shop.setup.waiting import Prompted, Waiting, can_ask, cli_waiting
from print_shop.slicer.orca import SlicerProfiles
from print_shop.slicer.store import STORE_DIR, NoChoice, Store, choose
from print_shop.slicer.store import describe as describe_store

#: From the review's `ok` (or the job's submit, with no review) to the approval gate: the slice
#: and its reading. The intake and the review aren't counted (mqucifer/print-shop#125).
APPROVAL_WAIT_S = 3600.0
FINISH_WAIT_S = 900.0  # after the press: preflight, upload, and a five-minute start watch


#: Ctrl-C at the question before preparing.
STOPPED_BEFORE = "\nStopped, before anything was prepared or sent."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stl", nargs="*", type=Path,
                        help="the part to print, or several for one job, on one plate")
    parser.add_argument("--serve", action="store_true", help="only watch and ask outcomes")
    parser.add_argument("--list", action="store_true", help="print the ledger and exit")
    parser.add_argument("--filaments", action="store_true", help="list profiles/ and exit")
    parser.add_argument("--record", action="store_true", help="record the printer's replies")
    parser.add_argument("--nozzle", type=float, help="the fitted nozzle, in mm (0.4)")
    parser.add_argument("--filament", help="the loaded filament: its variety, or a tuning's name")
    parser.add_argument("--machine", help="a machine profile's exact name, to pick one")
    parser.add_argument("--process", help="a process profile's exact name, to pick one")
    parser.add_argument("--printer", help="which printer, when the inventory has several")
    parser.add_argument("--answers", type=Path, help="recorded answers to the questions, as JSON")
    parser.add_argument("--as-laid", action="store_true",
                        help="keep the file's own pose: it's laid for printing")
    parser.add_argument("--context", type=Path,
                        help="the part's context: a sidecar, or its design run's folder")
    parser.add_argument("--sliced", type=Path, metavar="FILE",
                        help="a file you sliced by hand in Orca, sent as it is: its G-code, or "
                             "the plate's sliced file (.gcode.3mf)")
    parser.add_argument("--again", metavar="JOB_ID",
                        help="print a past hand-sliced job's file again, as a reprint of it")
    parser.add_argument("--plate", type=int,
                        help="which plate, for a sliced plate file that holds several")
    parser.add_argument("--project", type=Path, metavar="P.3mf",
                        help="with --sliced: the Orca project the file was sliced from, for "
                             "each object's overrides, where the file doesn't carry them")
    parser.add_argument("--loaded-ok", action="store_true",
                        help="print a hand-sliced file with what's loaded and fitted, though "
                             "it names another filament or nozzle")
    parser.add_argument(
        "--settings", type=Path, default=SETTINGS_PATH, help="the settings file (a path)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(on_screen)
        # A wait among the screens is said as the screens say it (mqucifer/print-shop#223).
        handler.setFormatter(Terminal("%(asctime)s %(levelname)s %(message)s"))
    by_hand = args.sliced is not None or args.again is not None
    if sum((bool(args.stl), args.sliced is not None, args.again is not None)) > 1:
        parser.error("give one of a part, --sliced FILE, or --again JOB_ID")
    if by_hand and (args.serve or args.list or args.filaments):
        parser.error("--sliced and --again send a job; leave out --serve, --list, --filaments")
    if args.sliced is not None and not args.sliced.is_file():
        parser.error(f"{args.sliced} is not a file")
    if by_hand:
        # Nothing is chosen for a hand-sliced file, so nothing that chooses is taken.
        for flag in ("nozzle", "filament", "machine", "process", "answers", "context"):
            if getattr(args, flag) is not None:
                parser.error(f"--{flag} is for a part: a hand-sliced file is sent as it is")
        if args.as_laid:
            parser.error("--as-laid is for a part: a hand-sliced file is sent as it is")
    elif args.plate is not None or args.loaded_ok:
        parser.error("--plate and --loaded-ok go with --sliced or --again")
    if args.project is not None and args.sliced is None:
        parser.error("--project goes with --sliced: a reprint keeps its original's overrides")
    if len(args.stl) > 1 and args.context is not None:
        parser.error("--context is for one part: with several, each part's context is read "
                     "beside it")
    a_job = bool(args.stl) or by_hand
    if args.list or args.filaments or not (a_job or args.serve):
        return _main(parser, args)  # it only reads, or has nothing to do: no lock
    try:
        # Before the inventory, the profiles' composing, the arming and the broker: a second
        # start mustn't write over what the live job slices with, or publish over its gate.
        with only_live(WORK_DIR, ledger_path(args.settings), _part(args)) as live:
            if args.serve:
                return _main(parser, args, live=live)
            # A job: what it shows is kept in its folder too (mqucifer/print-shop#139).
            job_log = JobLog(secrets(args.settings))
            with job_log.capturing():
                given = sys.argv[1:] if argv is None else argv
                files = args.stl or ([args.sliced] if args.sliced is not None else [])
                job_log.write(_header(files, given))
                try:
                    return _main(parser, args, job_log, live)
                finally:
                    job_log.close()
    except AlreadyLive as refused:
        print(refused, file=sys.stderr)
        return 2


def _part(args: argparse.Namespace) -> str | None:
    """The part as a refused second start is told it, until the job has its own name for it."""
    if args.serve:
        return None
    if args.again is not None:
        return f"a reprint of job {args.again}"
    if args.sliced is not None:
        return Path(args.sliced).name
    return ", ".join(part.name for part in args.stl)


def _header(parts: list[Path], given: list[str]) -> str:
    """The log's first lines: when, each part's full path, and the command as given. The print
    desk reads the part's path from here, for its "run it again" line. A reprint's part is the
    file its job kept, named once the ledger has been read."""
    named = "".join(f"part: {part.resolve()}\n" for part in parts)
    return (f"print_job.py, {datetime.now(UTC).isoformat(timespec='seconds')}\n{named}"
            f"command: uv run python scripts/print_job.py {shlex.join(given)}\n")


def _said_what_is_on(
    inventory: Inventory, args: argparse.Namespace, read: Callable[[str], str]
) -> None:
    """Before anything's prepared: what's on, where it isn't known since a print with a swap
    (mqucifer/print-shop#218)."""
    ask_on_now(inventory, inventory.printer(args.printer).id, read=read, write=print,
               forget=drop_typed, asks=can_ask())


def _hand_sliced(
    args: argparse.Namespace, job_log: JobLog | None, read: Callable[[str], str]
) -> sliced.SlicedJob | int:
    """The hand-sliced file to send, read and checked, with what's kept of it shown; or the
    exit code, having said why it isn't sent. Nothing is asked but what's on the printer, where
    that isn't known (`_said_what_is_on`)."""
    store = Store(STORE_DIR)
    reprint: sliced.Again | None = None
    try:
        if args.again is not None:
            with Ledger(ledger_path(args.settings)) as ledger:
                reprint = sliced.again(ledger, WORK_DIR, args.again)
            if job_log is not None:
                job_log.write(f"part: {reprint.path}\n")
            file = sliced.read(reprint.path)
        else:
            file = sliced.read(args.sliced, args.plate, args.project)
    except sliced.SlicedError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Sliced by hand: {file.said()}.")
    print("No questions, no proposal, no lesson applied, nothing sliced again.")
    if reprint is not None:
        print(f"A reprint of job {reprint.reprint_of} ({reprint.name}): the file it printed, "
              "kept in its folder, with the same bytes.")
    grams = f"{file.filament_g:g} g, " if file.filament_g is not None else ""
    print(f"  sha256      {file.sha256}")
    print(f"  Orca        {file.print_time_s // 60} min, {grams}{file.filament_cm3:g} cm3, "
          f"{file.layer_count} layers")

    material: Material | None = None
    compared: sliced.Comparison | None = None
    note = loaded_ok = ""
    with Inventory(ledger_path(args.settings)) as inventory:
        if not inventory.printers():
            # Never skipped silently (the operator, 2026-10-25).
            note = "the inventory isn't set up"
            print(f"The inventory isn't set up ({SETUP}), so what's loaded isn't checked "
                  "against the file, and there's no tuning to compare it with.")
        else:
            try:
                _said_what_is_on(inventory, args, read)
                material = inventory.material(args.printer)
            except InventoryError as exc:
                print(exc, file=sys.stderr)
                return 2
            except KeyboardInterrupt:
                print(STOPPED_BEFORE)
                return 130
            print(f"From the inventory: {material.line()}.")
    if material is not None:
        wrong = sliced.loaded_mismatch(file, material, store)
        if wrong and not args.loaded_ok:
            print("Not sent: " + "; ".join(wrong) + ". Load and fit what the file names "
                  "(`stock load <id>`, `stock fit <mm>`), or add --loaded-ok to print it with "
                  "what's loaded.", file=sys.stderr)
            return 2
        if wrong:
            loaded_ok = "; ".join(wrong)
            print(f"{loaded_ok[0].upper()}{loaded_ok[1:]}: printing with what's loaded, as "
                  "you said (--loaded-ok).")
        try:
            compared = sliced.compare(file.config, material, store,
                                      bed=bed_type(load_env(args.settings)).value)
        except NoChoice as exc:
            note = f"the store picks no tuning for what's loaded ({str(exc).splitlines()[0]})"
    job = sliced.SlicedJob.of(
        file, material=material, compared=compared, compared_note=note, loaded_ok=loaded_ok,
        name=reprint.name if reprint else None,
        reprint_of=reprint.reprint_of if reprint else None,
        overrides=reprint.overrides if reprint else None,
        overrides_from=reprint.overrides_from if reprint else "",
    )
    for line in job.record(store).lines():
        if not line.startswith("printed with what was loaded"):
            print(f"  {line}")
    return job


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace,
          job_log: JobLog | None = None, live: Live | None = None) -> int:
    read: Callable[[str], str] = job_log.reader(input) if job_log is not None else input
    if args.filaments:
        print(describe_store(Store(STORE_DIR)))
        return 0
    if args.list:
        with Ledger(ledger_path(args.settings)) as ledger:
            rows = ledger.rows()
            print(describe_all(rows) if rows else "the ledger is empty")
            unprinted = ledger.unprinted_jobs()
            if unprinted:
                print("\nEnded without printing:")
                for job in unprinted:
                    print(f"  {job.line()}")
        return 0
    by_hand = args.sliced is not None or args.again is not None
    if not args.serve and not args.stl and not by_hand:
        parser.error("give a part to print, --sliced FILE, --again JOB_ID, --serve, or --list")
    for part in args.stl:
        if not part.is_file():
            parser.error(f"{part} is not a file")
    if args.context is not None and not args.context.exists():
        parser.error(f"{args.context} is neither a sidecar nor a run's folder")

    profiles: SlicerProfiles | None = None
    asker: Asker | None = None
    waiting: Waiting | None = None
    hand_sliced: sliced.SlicedJob | None = None
    if by_hand:
        found = _hand_sliced(args, job_log, read)
        if isinstance(found, int):
            return found
        hand_sliced = found
    # The job's opening lines, said as one block before it's submitted (mqucifer/print-shop#266).
    this = ThisJob(part=str(args.stl[0]) if len(args.stl or ()) == 1 else "") if args.stl else None
    if args.stl:
        assert this is not None
        material: Material | None = None
        nozzle, filament = args.nozzle, args.filament
        with Inventory(ledger_path(args.settings)) as inventory:
            if not inventory.printers():
                # Never skipped silently (the operator, 2026-10-25).
                this.inventory = f"not set up ({SETUP}); using --nozzle and --filament"
                if nozzle is None or not filament:
                    parser.error("say what's fitted: --nozzle (mm) and --filament (a variety)")
            else:
                try:
                    _said_what_is_on(inventory, args, read)
                    material = inventory.for_print(nozzle, filament, args.printer)
                except InventoryError as exc:
                    print(exc, file=sys.stderr)
                    return 2
                except KeyboardInterrupt:
                    print(STOPPED_BEFORE)
                    return 130
                nozzle, filament = material.nozzle_mm, material.tuning or material.variety
                this.inventory = material.line()
        assert nozzle is not None and filament is not None
        try:
            choice = choose(
                Store(STORE_DIR),
                nozzle_mm=nozzle,
                filament=filament,
                machine=args.machine,
                process=args.process,
            )
        except NoChoice as exc:
            print(exc, file=sys.stderr)
            return 2
        profiles = profiles_for(choice, STORE_DIR, WORK_DIR / "composed", material)
        this.fitted = f"a {nozzle:g} mm nozzle and {filament}"
        this.presets = [("machine", choice.machine.name), ("process", choice.process.name),
                        ("filament", choice.filament.name)]
        this.notes = choice.notes
        answering = Prompted(read)
        try:
            asker = replay_asker(args.answers) if args.answers else cli_asker(read=answering)
        except ValueError as exc:
            parser.error(str(exc))
        waiting = cli_waiting(read=read, replayed=args.answers is not None,
                              prompt=answering.prompt, several=len(args.stl) > 1)

    recorder = FrameRecorder(recording_path()) if args.record else None
    printer: PrinterClient | None = None
    a_job = bool(args.stl) or hand_sliced is not None
    armed = ""
    if a_job and hardware_allowed():
        printer = live_printer(recorder=recorder)
        armed = f"ARMED by {ALLOW_HARDWARE_ENV}=1: an approval in Home Assistant starts the print."
    elif a_job:
        armed = f"Not armed ({ALLOW_HARDWARE_ENV} is not 1): an approval will NOT start a print."
    recording = f"Recording the printer's replies to {recorder.path}" if recorder else ""
    if this is not None:
        this.armed, this.recording = armed, recording
    else:
        for said in (armed, recording):
            if said:
                print(said)

    run = build_run(
        printer=printer,
        profiles=profiles,
        recorder=recorder,
        settings=args.settings,
        asker=asker,
        waiting=waiting,
        # A hand-sliced file is reviewed by nobody here: it's shown, and goes to the gate.
        reviewer=cli_reviewer(read=read) if hand_sliced is None else None,
        show=print if hand_sliced is not None else None,
        store=Store(STORE_DIR) if hand_sliced is not None else None,
        on_step=live.at if live is not None else None,
    )
    try:
        with run.daemon:
            if args.serve:
                print("Serving the outcome gate. Ctrl-C to stop; open prints stay open.")
                while True:
                    time.sleep(1)

            if hand_sliced is not None:
                job_id = run.daemon.submit_sliced(hand_sliced)
            elif len(args.stl) > 1:
                assert this is not None
                this.part = f"{len(args.stl)} parts, one job, on one plate"
                this.parts = [(part.name, "; ".join(read_context(part).lines()[:2]))
                              for part in args.stl]
                print("\n".join(this.lines(terminal_width())))
                job_id = run.daemon.submit_several(args.stl, as_laid=args.as_laid)
            else:
                assert this is not None
                context = read_context(args.stl[0], args.context)
                this.context = "; ".join(context.lines()[:2])
                print("\n".join(this.lines(terminal_width())))
                job_id = run.daemon.submit(args.stl[0], as_laid=args.as_laid,
                                           context=args.context)
            if job_log is not None:
                job_log.open(WORK_DIR / job_id / LOG_NAME)
            log = str(job_log.path) if job_log is not None else ""
            if hand_sliced is not None:
                if log:
                    print(f"Job {job_id}'s log: {log}")
                print(f"Job {job_id}: taking the file as it is, then the approval gate.")
            else:
                if len(args.stl) > 1:
                    how = ("each kept as laid, then a few questions about the parts together, here"
                           if args.as_laid else
                           "a few questions about the parts together, here, then each part's pose")
                else:
                    how = (f"{'kept as laid' if args.as_laid else 'orienting'}, then a few "
                           "questions about the part, here")
                print("\n".join(ThisJob.started(job_id, log, how, terminal_width())))
            shown = 0
            while True:  # a gate at a time, to the one before printing
                try:
                    gate = run.daemon.wait_for_gate(job_id, APPROVAL_WAIT_S, shown)
                except TimeoutError as ran_out:
                    print(ran_out)  # the sentence, and the ledger's row is written
                    return 1
                if gate is None:
                    break  # it ended first
                shown += 1
                for line in _gate_lines(gate, turned=hand_sliced is None):
                    print(line)
                print(f"  answer by   {_answer_by(gate)}; after that it ends and nothing prints")
                if gate.get("gate") == Gate.BEFORE_PRINT:
                    break
            try:
                result = run.daemon.wait_for_finish(
                    job_id, timeout_s=GATE_WAIT_S + FINISH_WAIT_S, tell=print)
            except TimeoutError:
                print(run.daemon.abandon(job_id, "waiting for the job to finish",
                                         GATE_WAIT_S + FINISH_WAIT_S))
                if run.ledger.get(job_id) is None:
                    return 1
                result = None
            ended = run.ledger.unprinted(job_id)
            if ended is not None:
                print(ended.detail)  # a gate's own wait ran out
                return 1
            if result is not None and result.error:
                print(f"The job ended without printing: {result.error}")
            row = run.ledger.get(job_id)
            if row is None:
                print("Nothing was started, so the ledger has no row for this job.")
                return 0 if result is None or not result.error else 1
            return _watch(run, job_id)
    except KeyboardInterrupt:
        print("\nStopped. Any print without an answer stays open in the ledger.")
        return 130
    finally:
        run.ledger.close()


def _watch(run: Run, job_id: str) -> int:
    """A started print, watched read-only until its outcome is answered, then shown. For a plate
    of several (mqucifer/print-shop#109), what the parts share once, then each part's own as it's
    answered."""
    print("Started. Watching the printer read-only; answer in Home Assistant when it ends.")
    rows = run.ledger.job_rows(job_id)
    if len(rows) > 1:
        print(f"It asks about each of the {len(rows)} parts in turn, by its number and its "
              "place on the plate.")
    shown: set[str] = set()
    while True:
        rows = run.ledger.job_rows(job_id)
        for row in rows:
            if row.result is not None and row.job_id not in shown:
                if row.plate is None:
                    print("\n" + describe(row))
                else:
                    if not shown:
                        print("\n" + describe_plate(row))
                    print("\n" + describe_part(row))
                shown.add(row.job_id)
        if rows and len(shown) == len(rows):
            break
        time.sleep(5)
    frames = run.ledger.frames(job_id)
    if frames:
        print(f"frames         {len(frames)}, the last at {frames[-1].line()}, in"
              f" {Path(frames[-1].path).parent}")
    else:
        print(f"frames         {no_frames(run.ledger.frames_missed(job_id))}")
    return 0


def on_screen(record: logging.LogRecord) -> bool:
    """Whether a log line shows on the terminal: not one from the model's work beside the review,
    which would land on the open prompt (mqucifer/print-shop#242), nor bookkeeping the person
    can't act on that lands among their answers: a model call's HTTP line, and the broker's
    availability said again (mqucifer/print-shop#266). The job's log keeps each one."""
    if record.threadName in (THREAD, STREAM_THREAD):
        return False
    if record.name.startswith(("httpx", "httpcore")) and record.levelno <= logging.INFO:
        return False
    return not str(record.msg).startswith(AVAILABILITY_SAID_AGAIN)


#: How the broker's client begins its line when it says "online" again over another session's
#: will (`core.mqtt`): nothing to act on, so it's left to the job's log.
AVAILABILITY_SAID_AGAIN = "availability read offline while this run is online"


def _gate_lines(gate: dict[str, object], *, turned: bool) -> list[str]:
    """A gate as the terminal, and so the job's log, shows it (mqucifer/print-shop#200).

    `turned` is False for a hand-sliced file, which nothing turns.
    """
    lines = [f"At {Gate.named(gate)}. Approve or Reject on the dashboard."]
    if gate.get("gate") == Gate.BEFORE_PRINT:
        seconds = gate.get("print_time_s")
        lines += [f"  time        {int(seconds) // 60 if isinstance(seconds, int | float) else 0}"
                  " min",
                  f"  plastic     {gate.get('filament_cm3')} cm3"]
        plate = gate.get("plate_lines")
        if isinstance(plate, list | tuple):
            # No one turn is a plate's: each part's is in the gate's why (mqucifer/print-shop#109).
            lines += [f"  plate       {line}" for line in plate]
        elif turned:
            lines.append(f"  turned      {gate.get('orientation_deg')}")
        lines.append(f"  because     {gate.get('orientation_rationale')}")
        if gate.get("without"):
            lines.append(f"  model       {gate['without']}")
        if gate.get("pause"):
            what = str(gate.get("pause_for") or "pause").upper()
            lines.append(f"  {what:<12}{gate['pause']}: be at the printer for it")
        if gate.get("spools"):
            lines.append(f"  SPOOL       {gate['spools']}")
        return lines
    lines.append(f"  because     {gate.get('reason') or 'it gave no reason'}")
    if gate.get("gate") != Gate.REVIEW_PROPOSAL:
        return lines
    lines[0] = (f"At {Gate.named(gate)}, before any question or slice. "
                "Approve or Reject on the dashboard.")
    closed = gate.get("is_manifold")
    volume, box = gate.get("volume_mm3"), gate.get("bbox_mm")
    lines.append(f"  is_manifold {closed}")
    if isinstance(volume, int | float):
        lines.append(f"  volume_mm3  {volume:.0f}")
    elif closed is False:
        lines.append("  volume_mm3  None: a mesh that isn't closed has no volume")
    if isinstance(box, list | tuple):
        lines.append(f"  bbox_mm     {' x '.join(f'{round(side, 1):g}' for side in box)}")
    stopped = ("a mesh this gate calls untrustworthy" if closed is False
               else "a part this gate stopped")
    return [
        *lines,
        f"  Approve     goes on to slice {stopped}, as the file lays it: the questions and the "
        "review follow, then the gate before printing, where today the job fails whatever you "
        "answer. Nothing prints.",
        "  Reject      ends the job: nothing is sliced or printed",
    ]


def _answer_by(gate: dict[str, object]) -> str:
    """The gate's deadline in local time, and the minutes left."""
    raw = gate.get("answer_by")
    if not isinstance(raw, str):
        return "within the hour"
    by = datetime.fromisoformat(raw).astimezone()
    left = max(0, round((by - datetime.now(by.tzinfo)).total_seconds() / 60))
    return f"{by:%H:%M} ({left} min left)"


if __name__ == "__main__":
    sys.exit(main())
