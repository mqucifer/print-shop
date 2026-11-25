"""Run one print job, or serve the outcome gate, on the Mac.

    uv run python scripts/print_job.py PART.stl --nozzle 0.4 --filament NAME [--as-laid]
        [--context PATH] [--answers F] [--record]
    uv run python scripts/print_job.py A.stl B.stl C.stl [--as-laid] [--answers F] [--record]
    uv run python scripts/print_job.py --sliced FILE [--plate N] [--project P.3mf]
        [--loaded-ok] [--record]
    uv run python scripts/print_job.py --again JOB_ID [--change "KEY=VALUE; ..."] [--loaded-ok]
        [--record]
    uv run python scripts/print_job.py --runs [WORDS]
    uv run python scripts/print_job.py --resume [JOB_ID] [--record]
    uv run python scripts/print_job.py --queue [FILE [xN] [spool=S] ...] [--record]
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

**The questions, the review and the gates take as long as you take** (mqucifer/print-shop#125,
mqucifer/print-shop#209). Nothing waiting for you has a deadline: a job left ready waits for days.
Only the machine's steps are timed: the hour for the slice to reach the approval gate, from the
review's `ok`, and the upload and start after a Start. One that runs out ends the job in one
sentence, with a ledger row saying it timed out, and nothing printed.

**A job waiting for you survives this command** (mqucifer/print-shop#209). Stopped with Ctrl-C,
or by a restart, at the questions, at the review or ready to print, it waits in the jobs folder.
`--resume JOB_ID` takes it up at that step, with what you'd answered and corrected and its slice;
`--resume` alone takes the only one, and `--list` shows them. A job is never kept approved: a
restart leaves it ready, and nothing is armed.

**Start is the approval, given when it's time to print** (mqucifer/print-shop#209). At the approval
gate, type `start` here or press Yes on the card (in a run armed by the flag), or Start on your
signed-in phone, on the home Wi-Fi (mqucifer/print-shop#214). Start checks again the G-code's hash,
the part's file, the profiles, the loaded spool, the fitted nozzle and that the printer is free,
and asks that the bed is clear: here, "I can see the bed is clear", where only `y` starts it, and
on the phone its tick box. A change is said, with what to do, nothing prints, and the gate waits
for a new Start. `reject` here is the card's Reject. One Start a gate: the second is told it was
already taken. **The card's Start is an MQTT message,** which anyone on the broker can send, so it
starts only in a run the flag armed; in one it didn't, it's held before anything is read
(mqucifer/print-shop#214, the Sponsor's "B").

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

**A person arms each start, and this is the only place that builds what arms
the printer** (mqucifer/print-shop#214). Two ways: `PRINT_AGENT_ALLOW_HARDWARE=1`
on this command's own line, which arms the run for the terminal's and the card's
Start; or a signed-in phone's Start, on the home Wi-Fi, which arms one start of
one job, and disarms straight after. With `PHONE_HOME` in the settings, a job's
run serves the phone's Start page (`scripts/phone.py` pairs a phone). Without
either, a Start is held, saying so: the job waits, ready, for the phone or an
armed `--resume` (mqucifer/print-shop#209). It lives outside the package so that
nothing importable can arm the printer for itself.

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
same as for any job. Either is refused when the file names another filament or nozzle than the
inventory says is loaded and fitted, unless `--loaded-ok` says to print it with what's loaded.

**A finished run prints again, as it was or with a small change** (mqucifer/print-shop#216).
`--runs` lists the runs that ended, failed ones too, newest first, with the outcome, the note,
the spool and what you changed; `--runs WORDS` finds them by part, colour or note. `--again
JOB_ID` (or the start of it) shows the run and what's suggested, and Enter sends its kept file to
the gate as it is: nothing asked, proposed or sliced, refused if the file is gone or has changed
since it printed. Type a change instead (`layer_height=0.16; take 1`) and it's sliced again from
that run's own part, pose, profiles and settings, with no question and no model, and how the new
slice differs from the run's is shown, anything beyond what you changed named; Enter sends that.
A lesson kept since, and a setting that didn't reach that run's slice, are suggested, never
applied. `--change "KEY=VALUE; ..."` does the same without asking. The new run is a print again
of that one in the ledger, with its own outcome. Start, its checks and the arming are any job's.
Each job keeps its G-code's hash, a copy of its part and its three profiles from its start, for
this; for a run from before, what can't be checked is said. A hand-sliced file or a plate prints
again as it was.

**Many files queue** (mqucifer/print-shop#210), with `--queue`: parts and hand-sliced files
together, each followed by how many (`x3`) and the spool it's for (`spool=2`, the stock's number;
the loaded one by default, and it needn't be loaded to prepare). They're prepared while a print
runs, and the queue's prompt shows each job's step and spool and what needs you: `answer 2` and
`review 3` in any order (print-shop's two at once, the model's when they come, and `go 2` goes on
without them), `answer 2 4 5` to give 4 and 5 the same answers where they ask alike, `spool 3 2`
to move a waiting job to another spool (another colour of its filament at once; another variety or
tuning prepared again, back to its review), and `first`, `hold`, `remove`, `retry` and `together`
to change the queue; `help` lists them. Each job keeps its own `job.log`. Printing stays
one at a time: when nothing prints, the first ready job is offered at the gate, on the card and
here, saying the spool it needs, and `start` here, or Yes on the card, starts it as any job's
Start does. A quantity is that many prints, each with its own ledger row and outcome, prepared
once. `--queue` alone takes the queue up again, as it was: every job waits through a restart.
`PRINT_SHOP_PREPARE_AT_ONCE` in the settings says how many are sliced and asked of the model at
once (2).

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
import contextlib
import logging
import shlex
import sys
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from print_shop import reprint, sliced
from print_shop.arming import ALLOW_HARDWARE_ENV, Armed, OneStart, hardware_allowed
from print_shop.core.contracts import Gate
from print_shop.desk.read import parts_dir
from print_shop.env import SETTINGS_PATH, load_env
from print_shop.expert.client import STREAM_THREAD
from print_shop.inventory import SETUP, Inventory, InventoryError, Material
from print_shop.joblog import LOG_NAME, JobLog, JobLogs, Terminal, secrets
from print_shop.ledger import Ledger, ledger_path
from print_shop.opening import ThisJob
from print_shop.orchestrator.daemon import CARD_STARTS_UNDER_FLAG, Daemon
from print_shop.orchestrator.kept import WaitingJob, WaitingJobs
from print_shop.orchestrator.live import AlreadyLive, Live, only_live
from print_shop.orchestrator.outcome import no_frames
from print_shop.orchestrator.spools import ask_on_now
from print_shop.phone.home import Home, HomeRefused
from print_shop.phone.notices import Notices
from print_shop.phone.one_job import OneJob
from print_shop.phone.phones import Phones, phones_path
from print_shop.phone.server import PageServer, PhoneOff, attach, make_server
from print_shop.phone.shop import Shop
from print_shop.printer.centauri import FrameRecorder, PrinterClient, live_printer
from print_shop.queue_terminal import Runs, parse_adding, serve, stock_listing
from print_shop.runtime import (
    WORK_DIR,
    Run,
    bed_type,
    build_queue,
    build_run,
    describe,
    describe_all,
    describe_part,
    describe_plate,
    job_profiles,
    profiles_for,
    recording_path,
    sliced_reader,
)
from print_shop.setup.context import read_context
from print_shop.setup.intake import Asker, cli_asker, drop_typed, replay_asker
from print_shop.setup.layout import terminal_width
from print_shop.setup.review import cli_reviewer
from print_shop.setup.suggest import THREAD
from print_shop.setup.waiting import Prompted, Waiting, can_ask, cli_waiting
from print_shop.slicer.orca import SlicerProfiles, slice_model
from print_shop.slicer.store import STORE_DIR, NoChoice, Store, choose
from print_shop.slicer.store import describe as describe_store

#: From the review's `ok` (or the job's submit, with no review) to the approval gate: the slice
#: and its reading. The intake and the review aren't counted (mqucifer/print-shop#125).
APPROVAL_WAIT_S = 3600.0
FINISH_WAIT_S = 900.0  # after the Start: preflight, upload, and a five-minute start watch

#: The gate before printing, as the terminal says it, and how to answer (mqucifer/print-shop#209,
#: mqucifer/print-shop#214): see `start_words`.
READY = "Ready to print. Start it here, or reject it on the card."
START_HERE = "  Type start here. It waits until you start or reject it."
ON_THE_CARD = "  It waits until you start or reject it."
#: The bed's question at the terminal's Start, in the phone's words (mqucifer/print-shop#214).
BED_QUESTION = "  I can see the bed is clear [y/N]"




def start_words(*, phone: bool, here: bool) -> tuple[str, str]:
    """The gate before printing's two lines: where a Start is taken, and how to answer.

    `phone` is whether the phone's Start page is served, and `here` whether this terminal is
    read. The card's is taken where `CARD_STARTS_UNDER_FLAG` (mqucifer/print-shop#214): like a
    `start` here, it prints only in a run the flag armed, as the run's first line says.
    """
    card = CARD_STARTS_UNDER_FLAG
    where = [w for w, can in (("here", here), ("on your phone", phone), ("on the card", card))
             if can]
    if not where:
        ready = ("Ready to print. Nothing here can start it: take it up at a terminal "
                 "(`--resume`), or set up your phone's Start. Reject it on the card.")
    elif where == ["on the card"]:
        ready = "Ready to print. Start or reject it on the card."
    else:
        places = " or ".join(where) if len(where) < 3 else f"{where[0]}, {where[1]} or {where[2]}"
        ready = f"Ready to print. Start it {places}, or reject it on the card."
    if not here:
        return ready, ON_THE_CARD
    how = ["Type start here"]
    if phone:
        how.append("Start on your phone")
    if card:
        how.append("Yes on the card")
    return ready, f"  {', or '.join(how)}. It waits until you start or reject it."


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
                        help="print a finished run again: as it was, or with a small change")
    parser.add_argument("--change", metavar="KEY=VALUE; ...",
                        help="with --again: these changes, sliced again from the run, unasked")
    parser.add_argument("--runs", nargs="?", const="", metavar="WORDS",
                        help="list the finished runs, newest first; WORDS finds them by part, "
                             "colour or note")
    parser.add_argument("--resume", nargs="?", const="", metavar="JOB_ID",
                        help="take up a job that waits for you, where it waits: the only one, "
                             "without JOB_ID")
    parser.add_argument("--queue", action="store_true",
                        help="the print queue: the files given are added to it, each followed by "
                             "xN and spool=S where they aren't 1 and the loaded one")
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
    if args.change is not None and args.again is None:
        parser.error("--change goes with --again: it changes a finished run")
    if args.runs is not None:
        if by_hand or args.stl or args.queue or args.resume is not None or args.serve:
            parser.error("--runs only lists: give it alone, with words to find runs by")
        return _runs(args)
    if args.queue:
        for flag in ("sliced", "again", "resume", "answers", "context", "plate", "project"):
            if getattr(args, flag) is not None:
                parser.error(f"--{flag} isn't for --queue: give the files after it")
        if args.serve or args.list or args.filaments or args.as_laid or args.loaded_ok:
            parser.error("--queue takes files; leave out --serve, --list, --filaments, "
                         "--as-laid and --loaded-ok")
        try:
            with only_live(WORK_DIR, ledger_path(args.settings), "the print queue") as live:
                return _queue(args, live)
        except AlreadyLive as refused:
            print(refused, file=sys.stderr)
            return 2
    if sum((bool(args.stl), args.sliced is not None, args.again is not None,
            args.resume is not None)) > 1:
        parser.error("give one of a part, --sliced FILE, --again JOB_ID or --resume")
    if args.resume is not None:
        if args.serve or args.list or args.filaments:
            parser.error("--resume takes up a job; leave out --serve, --list, --filaments")
        for flag in ("nozzle", "filament", "machine", "process", "answers", "context", "plate",
                     "project"):
            if getattr(args, flag) is not None:
                parser.error(f"--{flag} isn't for --resume: the job keeps what it was given")
        if args.as_laid or args.loaded_ok:
            parser.error("--as-laid and --loaded-ok aren't for --resume: the job keeps its own")
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
    a_job = bool(args.stl) or by_hand or args.resume is not None
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
                    if args.resume is not None:
                        return _resume(args, job_log, live)
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
    if args.resume is not None:
        return f"job {args.resume}, taken up again" if args.resume else "a job taken up again"
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
    again: sliced.Again | None = None
    try:
        if args.again is not None:
            found = _again(args, read)
            if isinstance(found, int):
                return found
            again = found
            if job_log is not None:
                job_log.write(f"part: {again.path}\n")
            file = sliced.read(again.path)
        else:
            file = sliced.read(args.sliced, args.plate, args.project)
    except sliced.SlicedError as exc:
        print(exc, file=sys.stderr)
        return 2
    if again is None or again.reprinted is None:
        print(f"Sliced by hand: {file.said()}.")
        print("No questions, no proposal, no lesson applied, nothing sliced again.")
    if again is not None and again.reprinted is not None and again.reprinted.changes:
        print(f"A reprint of job {again.reprint_of} ({again.name}), sliced again from its part, "
              "pose, profiles and settings with: "
              + "; ".join(again.reprinted.said_changes()) + ". No questions, no model.")
    elif again is not None:
        print(f"A reprint of job {again.reprint_of} ({again.name}): the file it printed, "
              "kept in its folder, with the same bytes. Nothing asked, proposed or sliced.")
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
        file, material=material, compared=compared, compared_note=note, loaded_ok=loaded_ok)
    if again is not None:
        job = job.again(again)
    for line in job.record(store).lines():
        if not line.startswith("printed with what was loaded"):
            print(f"  {line}")
    return job


def _again(args: argparse.Namespace, read: Callable[[str], str]) -> sliced.Again | int:
    """A finished run to print again (mqucifer/print-shop#216): shown with what's kept of it and
    what's suggested; as it was, or with the changes typed here or given with --change. Returns
    what to send, or the exit code, having said why nothing is."""
    bed = bed_type(load_env(args.settings))
    try:
        with Ledger(ledger_path(args.settings)) as ledger:
            run = reprint.find(ledger, WORK_DIR, args.again, store_root=STORE_DIR, bed=bed,
                               parts=parts_dir(args.settings))
    except reprint.Refused as exc:
        print(exc, file=sys.stderr)
        return 2

    def into() -> Path:
        return reprint.slice_folder(WORK_DIR, run.row)

    try:
        if args.change is None and can_ask():
            chosen = reprint.screen(run, slicer=slice_model, into=into, read=read, write=print,
                                    forget=drop_typed)
            if chosen is None:
                print(STOPPED_BEFORE)
                return 130
            return chosen
        for line in run.lines(asking=False):
            print(line)
        if args.change is None:
            return run.as_it_was()
        changes = reprint.parse_changes(args.change, run)
        if isinstance(changes, str):
            print(f"--change not read: {changes}", file=sys.stderr)
            return 2
        folder = into()
        made = run.change(changes, folder, slice_model)
    except reprint.Refused as exc:
        print(exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(STOPPED_BEFORE)
        return 130
    for line in made.lines():
        print(line)
    if made.beyond:
        # Unasked, nothing that no change named is printed: at the terminal Enter asks once.
        print("Not sent: it also changes " + ", ".join(d.key for d in made.beyond) + ", which "
              "--change didn't name. Run --again without --change to see it and say.",
              file=sys.stderr)
        reprint.clear(folder)
        return 2
    return run.changed(made)


def _runs(args: argparse.Namespace) -> int:
    """The finished runs, newest first, or those `--runs WORDS` finds (mqucifer/print-shop#216).
    It only reads: no lock."""
    with Ledger(ledger_path(args.settings)) as ledger:
        found = reprint.runs(ledger, args.runs)
    if not found:
        print(f"No finished run has {args.runs!r}." if args.runs else "No run has finished yet.")
        return 0
    for row in found:
        for line in reprint.run_line(row):
            print(line)
    print("`--again JOB_ID` prints one again, as it was or with a small change.")
    return 0


def _main(parser: argparse.ArgumentParser, args: argparse.Namespace,
          job_log: JobLog | None = None, live: Live | None = None) -> int:
    # The run's one job, as the phone's queue of one (mqucifer/print-shop#288): its prompts
    # read as `input` does until the phone's page is on.
    one = OneJob(part="")
    read: Callable[[str], str] = job_log.reader(one.read) if job_log is not None else one.read
    wait_read = job_log.reader(one.wait_read) if job_log is not None else one.wait_read
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
        left = WaitingJobs(WORK_DIR / "jobs.sqlite").all()
        if left:
            print("\nWaiting for you (`--resume JOB_ID`):")
            for kept in left:
                print(f"  {kept.line()}")
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
            asker = one.asker(replay_asker(args.answers) if args.answers
                              else cli_asker(read=answering))
        except ValueError as exc:
            parser.error(str(exc))
        waiting = one.waiting(cli_waiting(
            read=wait_read, replayed=args.answers is not None, prompt=answering.prompt,
            several=len(args.stl) > 1, typed=one.keys_or_go))

    recorder = FrameRecorder(recording_path()) if args.record else None
    a_job = bool(args.stl) or hand_sliced is not None
    phone = PhonePage(args.settings, recorder) if a_job else PhonePage.off()
    one.answering_too(phone, part=(args.stl[0].name if args.stl else
                                   hand_sliced.name if hand_sliced is not None else ""),
                      replayed=bool(args.answers))
    printer, armed, recording = _arming(recorder, phone=phone.on) if a_job else (None, "", "")
    if this is not None:
        this.armed, this.recording = armed, recording
        if len(args.stl) > 1:
            this.part = f"{len(args.stl)} parts, one job, on one plate"
            this.parts = [(part.name, "; ".join(read_context(part).lines()[:2]))
                          for part in args.stl]
        else:
            this.context = "; ".join(read_context(args.stl[0], args.context).lines()[:2])
        # Said before anything that follows can fail, as each of its lines was.
        print("\n".join(this.lines(terminal_width())))
    else:
        for said in (recording, armed):
            if said:
                print(said)

    run = build_run(
        printer=printer,
        one_start=phone.one_start,
        start_page=phone.address,
        phone_page=phone.main,
        profiles=profiles,
        recorder=recorder,
        settings=args.settings,
        asker=asker,
        waiting=waiting,
        # A hand-sliced file is reviewed by nobody here: it's shown, and goes to the gate.
        reviewer=one.reviewer(cli_reviewer(read=read)) if hand_sliced is None else None,
        show=print if hand_sliced is not None else None,
        store=Store(STORE_DIR) if hand_sliced is not None else None,
        on_step=live.at if live is not None else None,
    )
    job_id = ""
    try:
        with run.daemon, phone.serving(run.daemon, one):
            if args.serve:
                print("Serving the outcome gate. Ctrl-C to stop; open prints stay open.")
                while True:
                    time.sleep(1)

            _say_waiting(run.daemon)
            if hand_sliced is not None:
                job_id = run.daemon.submit_sliced(hand_sliced)
            elif len(args.stl) > 1:
                job_id = run.daemon.submit_several(args.stl, as_laid=args.as_laid)
            else:
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
            return _follow(run, job_id, turned=hand_sliced is None, read=read,
                           armed=printer is not None, phone=phone.on)
    except KeyboardInterrupt:
        print(_stopped(run, job_id))
        return 130
    finally:
        run.ledger.close()


def _armed(recorder: FrameRecorder | None, *, phone: bool = False) -> PrinterClient | None:
    """The printer an approval commands, if this command's own line armed it, said either way."""
    printer, armed, recording = _arming(recorder, phone=phone)
    if recording:
        print(recording)
    print(armed)
    return printer


def _arming(
    recorder: FrameRecorder | None, *, phone: bool = False,
) -> tuple[PrinterClient | None, str, str]:
    """The printer a Start commands, if this command's own line armed it, and what's said of it:
    whether it's armed, with the phone's Start where it's served (mqucifer/print-shop#214), and
    where its replies are recorded, or "". THIS JOB says them as its rows
    (mqucifer/print-shop#266)."""
    recording = (f"Recording the printer's replies to {recorder.path}" if recorder is not None
                 else "")
    where = "a Start, here or on the card," if CARD_STARTS_UNDER_FLAG else "a Start here"
    if hardware_allowed():
        return (live_printer(recorder=recorder),
                f"ARMED by {ALLOW_HARDWARE_ENV}=1: {where} starts the print.", recording)
    waits = "a Start on your phone, or an armed `--resume`" if phone else "an armed `--resume`"
    return (None,
            f"Not armed ({ALLOW_HARDWARE_ENV} is not 1): {where} will NOT print. It's held, and "
            f"the job waits for {waits}.",
            recording)


class PhonePage:
    """The phone's page for this run (mqucifer/print-shop#214, mqucifer/print-shop#221): what a
    phone's confirm arms for one start, the server, on the home Wi-Fi only, and what its other
    screens read. Said either way as it's set up."""

    def __init__(self, settings: Path | None, recorder: FrameRecorder | None) -> None:
        self.one_start: OneStart | None = None
        self._server: PageServer | None = None
        #: Why the page is off though it's set up, for the queue's live line; or "".
        self._off = ""
        #: The Start page's address, and the page's own (the queue), for the card, or "".
        self.address = ""
        self.main = ""
        #: What the page's other screens read: given the daemon, and the queue where there is one.
        self.shop: Shop | None = None
        try:
            home = Home.from_settings(settings)
        except HomeRefused as exc:
            self._off = f"The phone's page is off: {exc}."
            print(self._off)
            return
        if home is None:
            return  # not set up: the terminal's Start, as before
        try:
            self._server = make_server(home=home, phones=Phones(phones_path(settings)),
                                       starts=None)
        except PhoneOff as exc:
            self._off = f"{str(exc)[:1].upper()}{str(exc)[1:]}."
            print(self._off)
            return

        def build(armed: Armed) -> PrinterClient:
            # The only other door to an armed client: a phone's confirm for one start, which
            # `live_printer` spends (mqucifer/print-shop#214).
            return live_printer(armed=armed, recorder=recorder)

        self.one_start = OneStart(build)
        self.shop = Shop(ledger_path(settings))
        self.address = f"{home.origin}/start"
        self.main = f"{home.origin}/"
        print(f"Your phone's page: {home.origin}/ , and its Start: {home.origin}/start , on the "
              "home Wi-Fi only (`scripts/phone.py pair` signs a phone in).")
        for said in self._server.said:  # pair again after the move to HTTPS; renew it soon
            print(said)

    @classmethod
    def off(cls) -> PhonePage:
        page = cls.__new__(cls)
        page.one_start = None
        page._server = None
        page.address = ""
        page.main = ""
        page.shop = None
        page._off = ""
        return page

    def trouble(self) -> str:
        """Why the page is off though it's set up, or answers nothing now (its certificate ran
        out), for the queue's live line (mqucifer/print-shop#312); or ""."""
        if self._server is None:
            return self._off
        now = self._server.trouble()
        return f"The phone's page is off: {now}." if now else ""

    @property
    def on(self) -> bool:
        return self._server is not None

    @contextlib.contextmanager
    def serving(self, daemon: Daemon, one: OneJob | None = None) -> Iterator[None]:
        """The page served for `daemon`, for as long as the run lasts; with `one`, a lone job's
        run, its job as the page's queue of one (mqucifer/print-shop#288)."""
        server = self._server
        if server is None:
            yield
            return
        notices = None
        if self.shop is not None:
            self.shop.daemon = daemon
            if one is not None:
                one.daemon = daemon
                # Only where the phone answers it too: not with replayed answers, nor without
                # a person's terminal for the Mac's prompts to be put by.
                if one.races:
                    self.shop.queue = one
            # A job that needs you, a pause near, a print ended: to the phone through Home
            # Assistant (mqucifer/print-shop#221).
            notices = Notices(self.shop, lambda n: daemon.notify(n.payload()))
            notices.start()
        attach(server, daemon, self.shop)
        thread = threading.Thread(target=server.serve_forever, name="phone-page", daemon=True)
        thread.start()
        try:
            yield
        finally:
            if notices is not None:
                notices.stop()
            server.shutdown()
            server.server_close()


def _say_waiting(daemon: Daemon) -> None:
    """Jobs that wait for the person from before, so a new one doesn't hide them."""
    waiting = daemon.waiting()
    if waiting:
        print("Waiting for you from before (`--resume JOB_ID` takes one up):")
        for kept in waiting:
            print(f"  {kept.line()}")


def _stopped(run: Run, job_id: str) -> str:
    """What Ctrl-C leaves: a job that waits says how to take it up again."""
    said = "\nStopped. Any print without an answer stays open in the ledger."
    if job_id and any(kept.job_id == job_id for kept in run.daemon.waiting()):
        said += (f"\nJob {job_id} waits where it was: "
                 f"`uv run python scripts/print_job.py --resume {job_id}` takes it up again.")
    return said


def _resume(args: argparse.Namespace, job_log: JobLog, live: Live) -> int:
    """A job that waits for the person, taken up at its step (mqucifer/print-shop#209)."""
    lone = OneJob(part="")  # the phone's queue of one (mqucifer/print-shop#288)
    read: Callable[[str], str] = job_log.reader(lone.read)
    jobs = WaitingJobs(WORK_DIR / "jobs.sqlite")
    kept: WaitingJob | None
    if args.resume:
        kept = jobs.get(args.resume)
        if kept is None:
            print(f"No job {args.resume} is waiting: `--list` shows those that are.",
                  file=sys.stderr)
            return 2
    else:
        waiting = jobs.all()
        if not waiting:
            print("Nothing is waiting for you.")
            return 0
        if len(waiting) > 1:
            print("Several jobs wait for you; say which with `--resume JOB_ID`:", file=sys.stderr)
            for one in waiting:
                print(f"  {one.line()}", file=sys.stderr)
            return 2
        kept = waiting[0]
    job_id = kept.job_id
    job_log.write(f"resumed: job {job_id} ({kept.part}), {kept.waits}\n")
    job_log.open(WORK_DIR / job_id / LOG_NAME)
    print(f"Job {job_id} ({kept.part}), {kept.waits}, taken up again. Its log: {job_log.path}")
    asker: Asker | None = None
    waiting_on: Waiting | None = None
    if not kept.by_hand:
        answering = Prompted(read)
        asker = lone.asker(cli_asker(read=answering))
        waiting_on = lone.waiting(cli_waiting(
            read=job_log.reader(lone.wait_read), replayed=False, prompt=answering.prompt,
            typed=lone.keys_or_go))
    recorder = FrameRecorder(recording_path()) if args.record else None
    phone = PhonePage(args.settings, recorder)
    lone.answering_too(phone, part=kept.part, replayed=False)
    printer = _armed(recorder, phone=phone.on)
    run = build_run(
        printer=printer,
        one_start=phone.one_start,
        start_page=phone.address,
        phone_page=phone.main,
        profiles=kept.profiles,
        recorder=recorder,
        settings=args.settings,
        asker=asker,
        waiting=waiting_on,
        reviewer=lone.reviewer(cli_reviewer(read=read)) if not kept.by_hand else None,
        show=print if kept.by_hand else None,
        store=Store(STORE_DIR) if kept.by_hand else None,
        on_step=live.at,
    )
    try:
        with run.daemon, phone.serving(run.daemon, lone):
            run.daemon.resume(job_id)
            return _follow(run, job_id, turned=not kept.by_hand, read=read,
                           armed=printer is not None, phone=phone.on)
    except KeyboardInterrupt:
        print(_stopped(run, job_id))
        return 130
    finally:
        run.ledger.close()


def _follow(
    run: Run, job_id: str, *, turned: bool, read: Callable[[str], str], armed: bool = False,
    phone: bool = False,
) -> int:
    """The job from its gates to its end: each gate shown, a Start taken here or on the phone,
    and a print watched until its outcome is answered."""
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
        here = can_ask()
        ready, how = start_words(phone=phone, here=here)
        lines = _gate_lines(gate, turned=turned, ready=ready)
        for line in lines:
            print(line)
        if gate.get("gate") == Gate.BEFORE_PRINT:
            if here:
                print(how)
                threading.Thread(target=_start_here, args=(run.daemon, job_id, read, print),
                                 daemon=True).start()
            else:
                print(ON_THE_CARD)
            break
        print("  It waits until you answer it on the card.")
    try:
        result = run.daemon.wait_for_finish(job_id, timeout_s=FINISH_WAIT_S, tell=print)
    except TimeoutError:
        print(run.daemon.abandon(job_id, "waiting for the job to finish", FINISH_WAIT_S))
        if run.ledger.get(job_id) is None:
            return 1
        result = None
    ended = run.ledger.unprinted(job_id)
    if ended is not None:
        print(ended.detail)  # a wait ran out
        return 1
    if result is not None and result.error:
        print(f"The job ended without printing: {result.error}")
    row = run.ledger.get(job_id)
    if row is None:
        print("Nothing was started, so the ledger has no row for this job.")
        return 0 if result is None or not result.error else 1
    return _watch(run, job_id)


def _queue(args: argparse.Namespace, live: Live) -> int:
    """The print queue, at this terminal (mqucifer/print-shop#210)."""
    adding = parse_adding([str(token) for token in args.stl])
    if isinstance(adding, str):
        print(adding, file=sys.stderr)
        return 2
    for handler in logging.getLogger().handlers:
        # The jobs prepare beside the prompt: what they log would land on it. The queue says
        # what happens to each, before each prompt.
        handler.setLevel(logging.WARNING)
    with Inventory(ledger_path(args.settings)) as inventory:
        if not inventory.printers():
            print(f"The inventory isn't set up ({SETUP}); every job slices with --nozzle and "
                  "--filament, and no spool can be named.")
            if args.nozzle is None or not args.filament:
                print("Say what's fitted: --nozzle (mm) and --filament (a variety).",
                      file=sys.stderr)
                return 2
        else:
            try:
                _said_what_is_on(inventory, args, input)
                print(f"From the inventory: {inventory.material(args.printer).line()}.")
            except InventoryError as exc:
                print(exc, file=sys.stderr)
                return 2
            except KeyboardInterrupt:
                print(STOPPED_BEFORE)
                return 130
    bed = bed_type(load_env(args.settings))

    def profiles(material: Material | None, into: Path) -> SlicerProfiles:
        return job_profiles(material, into, bed=bed, nozzle_mm=args.nozzle,
                            filament=args.filament, machine=args.machine, process=args.process)

    recorder = FrameRecorder(recording_path()) if args.record else None
    # The queue's jobs are offered at the daemon's gate, one at a time: the phone starts them
    # there as it does a lone job (mqucifer/print-shop#214).
    phone = PhonePage(args.settings, recorder)
    run = build_run(printer=_armed(recorder, phone=phone.on), profiles=None, recorder=recorder,
                    settings=args.settings, on_step=live.at, one_start=phone.one_start,
                    start_page=phone.address, phone_page=phone.main)
    # Each job keeps its own job.log, as a lone job does (mqucifer/print-shop#139).
    logs = JobLogs(secrets(args.settings), WORK_DIR)
    try:
        with run.daemon, phone.serving(run.daemon), logs.routing(), build_queue(
                run, settings=args.settings, profiles_for=profiles, sliced_for=_sliced_for(args),
                logs=logs) as queue:
            runs = Runs(ledger_path(args.settings), WORK_DIR, STORE_DIR, bed, slice_model,
                        parts_dir(args.settings))
            if phone.shop is not None:
                # The phone's Main is this queue, and its Again this `again`
                # (mqucifer/print-shop#221).
                phone.shop.queue, phone.shop.runs = queue, runs
            if adding:
                added, refused = queue.add(adding)
                print(f"Added {len(added)} to the queue." if added else "Nothing was added.")
                for why in refused:
                    print(f"Not added: {why}")
            return serve(queue, run.daemon, read=input,
                         stock=stock_listing(ledger_path(args.settings)), runs=runs,
                         phone=phone.on, phone_trouble=phone.trouble)
    except KeyboardInterrupt:
        print("\nPut by: every job waits where it was, and `--queue` takes them up again. Any "
              "print without an answer stays open in the ledger.")
        return 130
    finally:
        run.ledger.close()


def _sliced_for(args: argparse.Namespace) -> Callable[[Path, Material | None], sliced.SlicedJob]:
    """A hand-sliced file read for the queue, for the spool it's for (`runtime.sliced_reader`)."""
    return sliced_reader(args.settings)


def _start_here(
    daemon: Daemon, job_id: str, read: Callable[[str], str], write: Callable[[str], None],
    forget: Callable[[], None] = drop_typed,
) -> None:
    """Start and Reject at the terminal, while the gate before printing is open
    (mqucifer/print-shop#209). Start asks the bed's question, drawn first, with what was typed
    before it dropped, so a y typed ahead never answers it. Whatever comes of a Start is said by
    the job: started, or not and why."""
    while True:
        gate = daemon.pending_gate()
        if gate is None or gate.get("job_id") != job_id:
            return
        try:
            typed = read("> ").strip().lower()
        except (EOFError, OSError, ValueError):
            return
        if typed not in ("start", "reject"):
            write("  Type start, or reject.")
            continue
        if decided := daemon.decided(job_id):
            # Decided on the card while this prompt was open: said at once, nothing asked.
            write(f"  Not taken: {decided}.")
            return
        if typed == "reject":
            why = daemon.reject(job_id)
        else:
            write(BED_QUESTION)
            forget()  # shown only now: what was typed before it is no answer to it
            try:
                bed = read("bed clear> ").strip().lower()
            except (EOFError, OSError, ValueError):
                return
            why = daemon.start(job_id, bed_clear=bed in ("y", "yes"))
        if why:
            write(f"  Not taken: {why}.")


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


def _gate_lines(gate: dict[str, object], *, turned: bool, ready: str = READY) -> list[str]:
    """A gate as the terminal, and so the job's log, shows it (mqucifer/print-shop#200).

    `turned` is False for a hand-sliced file, which nothing turns.
    """
    lines = [f"At {Gate.named(gate)}. Approve or Reject on the dashboard."]
    if gate.get("gate") == Gate.BEFORE_PRINT:
        lines = [ready]
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


if __name__ == "__main__":
    sys.exit(main())
