"""The print queue at the terminal (mqucifer/print-shop#210): one screen that can drive it.

Everything here calls the queue's actions (`PrintQueue`) and the daemon's Start; nothing the queue
does lives in these prompts, so a phone's page can do the same (mqucifer/print-shop#221). The
questions and the review are the terminal's own (`cli_asker`, `cli_reviewer`), shown for the job
the person picks, in any order.

    queue>                     the queue again, with what's happened since
    answer 2 [4 5]             2's questions; the same answers to 4 and 5 where they ask alike
    go 2                       go on without the model's questions for 2, as `go` does
    review 3                   3's review
    mesh 2                     2's mesh gate: approve or reject it
    start [3]                  Start 3, or the job offered: it's offered first, then the bed's
                               question, naming the job and its spool, as at any gate
    reject                     reject the job offered: it ends, unprinted
    first 3 / place 3 1        the queue's order
    together 2                 the jobs for spool #2 first, together
    hold 3 / release 3         held, a job is never offered until it's let go
    remove 3                   out of the queue, unprinted
    retry 3                    a stopped job prepared again, with what you gave it
    spool 3 2                  3 moved to spool #2: another colour at once; another filament
                               prepared again, back to its review
    add FILE [x3] [spool=2]    more files, in one go
    quit                       put the queue by: every job waits where it is (Ctrl-C too)
"""

from __future__ import annotations

import shlex
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from print_shop.inventory import Inventory
from print_shop.orchestrator.daemon import Daemon
from print_shop.orchestrator.kept import Waits
from print_shop.orchestrator.print_queue import Adding, Need, PrintQueue
from print_shop.setup.intake import cli_asker, drop_typed
from print_shop.setup.review import cli_reviewer
from print_shop.setup.waiting import cli_waiting, terminal

__all__ = ["HELP", "BED_QUESTION", "parse_adding", "serve"]

#: The bed's question at a Start, in the phone's words (mqucifer/print-shop#214), as
#: `print_job.py` asks it.
BED_QUESTION = "  I can see the bed is clear [y/N]"

HELP = __doc__.split("\n\n")[2].rstrip() if __doc__ else ""

#: After an answer, how long the job is followed to what it asks next, before the queue again.
FOLLOW_S = 3.0
#: After a Start, the most it waits to say what came of it before the prompt is back: the
#: upload and the start's own limit (`print_queue.FINISH_WAIT_S`).
STARTED_WITHIN_S = 900.0
#: How often the line above an open `queue>` is read again, and rewritten where it's changed.
LIVE_EVERY_S = 0.5
#: What's said once a Start has come to something: started, held, ended or put back.
_START_ENDS = ("Started:", "Not started", "Ended without printing", "Rejected", "Timed out")


def parse_adding(tokens: list[str]) -> list[Adding] | str:
    """Files to add, each followed by its quantity (`x3`) and its spool (`spool=2`) where they
    aren't one and the loaded one. Returns them, or why they can't be read."""
    found: list[Adding] = []
    for token in tokens:
        low = token.lower()
        if low.startswith("x") and low[1:].isdigit() or low.startswith("spool="):
            if not found:
                return f"{token} goes after the file it's for"
            last = found[-1]
            if low.startswith("x"):
                found[-1] = Adding(last.path, copies=int(low[1:]), spool_id=last.spool_id)
            else:
                number = low.removeprefix("spool=").lstrip("#")
                if not number.isdigit():
                    return f"{token}: a spool is its number in the stock, spool=2"
                found[-1] = Adding(last.path, copies=last.copies, spool_id=int(number))
        else:
            found.append(Adding(Path(token).expanduser()))
    return found


def serve(
    queue: PrintQueue, daemon: Daemon, *, read: Callable[[str], str] = input,
    write: Callable[[str], None] = print, forget: Callable[[], None] = drop_typed,
    stock: Callable[[], str] | None = None, screen: Callable[[], TextIO | None] = terminal,
) -> int:
    """The queue at the terminal until `quit`, Ctrl-D or Ctrl-C: every job waits where it is.

    While `queue>` is open on a terminal, the line above it says what needs the person and what
    has just happened, rewritten in place as it changes, as the review's timer is
    (mqucifer/print-shop#260): what's being typed is left as it is. The whole of what happened
    is said at the next prompt."""
    term = _Terminal(queue, daemon, read, write, forget, stock)
    term.show()
    while True:
        live = _Live(term, screen())
        live.start()
        try:
            typed = read("queue> ").strip()
        except EOFError:
            write("")
            return 0
        finally:
            live.stop()
        if typed in ("quit", "exit", "q"):
            return 0
        term.do(typed)
        term.show()


class _Live:
    """The line above an open `queue>`, kept up to date on the screen alone: the job's log and
    what's typed are left as they are."""

    def __init__(self, term: _Terminal, out: TextIO | None) -> None:
        self.term, self.out = term, out
        self.told = term.told
        self.shown = term.live()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="queue-live")

    def start(self) -> None:
        if self.out is not None:
            self.thread.start()

    def stop(self) -> None:
        self.stopped.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run(self) -> None:
        assert self.out is not None
        while not self.stopped.wait(LIVE_EVERY_S):
            try:
                now = self.term.live(self.told)
            except Exception:  # noqa: BLE001 - the line is a courtesy: the prompt goes on
                continue
            if now == self.shown:
                continue
            self.shown = now
            width = shutil.get_terminal_size((100, 24)).columns
            text = now if len(now) < width else now[: max(width - 2, 10)] + "…"
            # The line above what's being typed, rewritten, and the cursor put back.
            self.out.write(f"\0337\033[1A\r\033[2K{text}\0338")
            self.out.flush()


class _Terminal:
    def __init__(self, queue: PrintQueue, daemon: Daemon, read: Callable[[str], str],
                 write: Callable[[str], None], forget: Callable[[], None],
                 stock: Callable[[], str] | None) -> None:
        self.queue, self.daemon = queue, daemon
        self.read, self.write, self.forget, self.stock = read, write, forget, stock
        self.told = 0

    def drain(self) -> list[str]:
        """What's happened since it was last said, said now."""
        new = self.queue.said(self.told)
        for said in new:
            self.write(f"  * {said}")
        self.told += len(new)
        return new

    def live(self, since: int | None = None) -> str:
        """One line: what needs the person, and the last thing that happened since `since`, for
        above the open prompt."""
        lines = self.queue.lines()
        asks = [f"{_verb(li.needs)} {li.number} ({li.job.part}, {li.needs})"
                for li in lines if li.needs is not None]
        said = [f"Needs you: {', '.join(asks)}"] if asks else ["Nothing needs you now"]
        offered = next((li for li in lines if li.offered), None)
        if offered is not None:
            said.append(f"offered to print: {offered.number} {offered.job.part}")
        new = self.queue.said(self.told if since is None else since)
        if new:
            said.append(new[-1] + (f" (+{len(new) - 1} more)" if len(new) > 1 else ""))
        return "  " + " · ".join(said)

    def show(self) -> None:
        """What's happened since, the print, and the queue in its order, and the line that's kept
        up to date above the prompt."""
        self.drain()
        self.write("")
        printing = self.queue.printing()
        self.write("QUEUE" + ("" if printing else "  (nothing printing)"))
        for running in printing:
            self.write(f"  {running}")
        lines = self.queue.lines()
        if not lines:
            self.write("  empty: `add FILE` adds one, or several")
        for line in lines:
            self.write(f"  {line.said()}")
        asks = [f"`{_verb(li.needs)} {li.number}`" for li in lines if li.needs is not None]
        if asks:
            self.write("Needs you: " + ", ".join(asks) + ". `help` lists the rest.")
        # The line kept up to date while the prompt is open (`_Live`).
        self.write(self.live())

    def do(self, typed: str) -> None:
        if not typed or typed == "list":
            return
        try:
            words = shlex.split(typed)
        except ValueError as exc:
            self.write(f"  Not read: {exc}")
            return
        verb, args = words[0].lower(), words[1:]
        action = {
            "help": self._help, "answer": self._answer, "review": self._review,
            "mesh": self._mesh, "start": self._start, "reject": self._reject,
            "first": self._first, "place": self._place, "together": self._together,
            "hold": self._hold, "release": self._release, "remove": self._remove,
            "spool": self._spool, "add": self._add, "stock": self._stock, "go": self._go,
            "retry": self._retry,
        }.get(verb)
        if action is None:
            self.write(f"  Not a queue command: {verb}. `help` lists them.")
            return
        action(args)

    # -- the person's answers -----------------------------------------------

    def _job(self, args: list[str], at: int = 0) -> str | None:
        if len(args) <= at:
            self.write("  Which job? Its number in the queue, or the start of its id.")
            return None
        job = self.queue.find(args[at])
        if job is None:
            self.write(f"  No job {args[at]} in the queue.")
            return None
        return job.job_id

    def _io(self, job_id: str) -> tuple[Callable[[str], str], Callable[[str], None]]:
        """Reading and writing for one job's screen: kept in its own log too, with what's typed,
        as a lone job's terminal is."""
        log = self.queue.log(job_id)
        if log is None:
            return self.read, self.write
        write = self.write

        def written(text: str) -> None:
            write(text)
            log.write(f"{text}\n")

        return log.reader(self.read), written

    def _answer(self, args: list[str]) -> None:
        job_id = self._job(args)
        if job_id is None:
            return
        others = [j for j in (self.queue.find(a) for a in args[1:]) if j is not None]
        asked = self.queue.asked(job_id)
        if asked is not None and asked.need is Need.CLOSER and asked.kept is not None:
            self._title(job_id)
            read, write = self._io(job_id)
            waiting = cli_waiting(read=read, write=write, forget=self.forget)
            assert waiting.closer is not None
            self.queue.give(job_id, waiting.closer(asked.questions[0], asked.kept, asked.seconds))
            self._follow(job_id)
            return
        if asked is None or asked.need is not Need.QUESTIONS:
            self.write("  It has no questions waiting" + (
                f": it needs {asked.need}" if asked is not None else "") + ".")
            return
        self._title(job_id)
        read, write = self._io(job_id)
        answers = cli_asker(read=read, write=write, forget=self.forget)(asked.questions)
        if why := self.queue.give(job_id, answers):
            self.write(f"  Not taken: {why}.")
            return
        for said in self.queue.give_alike([j.job_id for j in others], asked.questions, answers):
            self.write(f"  {said}")
        # The model's questions that follow are given to the same others.
        self._follow(job_id, [j.job_id for j in others])

    def _review(self, args: list[str]) -> None:
        job_id = self._job(args)
        if job_id is None:
            return
        asked = self.queue.asked(job_id)
        if asked is None or asked.need is not Need.REVIEW or asked.review is None:
            self.write("  It has no review waiting" + (
                f": it needs {asked.need}" if asked is not None else "") + ".")
            return
        self._title(job_id)
        read, write = self._io(job_id)
        review = cli_reviewer(read=read, write=write, forget=self.forget)(asked.review)
        if why := self.queue.give(job_id, review):
            self.write(f"  Not taken: {why}.")
            return
        self._follow(job_id)

    def _mesh(self, args: list[str]) -> None:
        job_id = self._job(args)
        if job_id is None:
            return
        asked = self.queue.asked(job_id)
        if asked is None or asked.need is not Need.MESH or asked.gate is None:
            self.write("  It isn't at the mesh gate.")
            return
        self._title(job_id)
        read, write = self._io(job_id)
        gate = asked.gate
        write(f"  because     {gate.get('reason') or 'it gave no reason'}")
        write(f"  is_manifold {gate.get('is_manifold')}")
        write("  approve     goes on to slice it as the file lays it: the questions and the "
              "review follow")
        write("  reject      ends the job: nothing is sliced or printed")
        typed = read("mesh> ").strip().lower()
        if typed not in ("approve", "reject"):
            self.write("  Nothing decided: `mesh` asks again.")
            return
        self.queue.give(job_id, (typed == "approve", "at the terminal"))
        self._follow(job_id)

    def _follow(self, job_id: str, others: list[str] | None = None) -> None:
        """What the job asks next, if it asks within a moment: else back to the queue, saying
        what came of it, or that it's being prepared."""
        end = time.monotonic() + FOLLOW_S
        while time.monotonic() < end:
            asked = self.queue.asked(job_id)
            if asked is not None:
                if asked.need is Need.QUESTIONS:
                    self._answer([job_id, *(others or ())])
                else:
                    {Need.CLOSER: self._answer, Need.REVIEW: self._review,
                     Need.MESH: self._mesh}[asked.need]([job_id])
                return
            job = self.queue.find(job_id[:8])
            if job is None or job.waits in (Waits.READY, Waits.STOPPED):
                return  # ready, stopped or ended: said at the prompt, once
            time.sleep(0.05)
        if self.queue.reading(job_id) is not None:
            self.write("  The model is still reading it for questions of its own: they're under "
                       "\"Needs you\" when they come.")
        else:
            self.write("  It's being prepared: it's back under \"Needs you\" when it asks again.")

    def _title(self, job_id: str) -> None:
        job = next((li for li in self.queue.lines() if li.job.job_id == job_id), None)
        if job is not None:
            copied = f" ({job.job.copied})" if job.job.copied else ""
            spool = f", for {job.spool}" if job.spool else ""
            self.write(f"\n-- {job.number}. {job.job.part}{copied}{spool}  "
                       f"[job {job_id}] --")

    # -- printing -----------------------------------------------------------

    def _start(self, args: list[str]) -> None:
        gate = self.daemon.pending_gate()
        job_id = self._job(args) if args else (str(gate["job_id"]) if gate else None)
        if job_id is None:
            if not args:
                self.write("  Nothing is offered to print: `start N` names one that's ready.")
            return
        job = self.queue.find(job_id[:8])
        if job is not None and job.held:
            self.write("  It's held: `release` it first.")
            return
        if gate is None or gate.get("job_id") != job_id:
            if why := self.queue.first(job_id):
                self.write(f"  Not started: {why}.")
                return
            end = time.monotonic() + 10.0
            while time.monotonic() < end:
                gate = self.daemon.pending_gate()
                if gate is not None and gate.get("job_id") == job_id:
                    break
                time.sleep(0.05)
            else:
                self.write("  It's first now, and isn't offered yet: it's offered once it's "
                           "ready and nothing prints. `start` then.")
                return
        self.drain()
        line = next((li for li in self.queue.lines() if li.job.job_id == job_id), None)
        named = ""
        if line is not None:
            copied = f" ({line.job.copied})" if line.job.copied else ""
            spool = f" on {line.spool}" if line.spool else ""
            named = f"Start {line.job.part}{copied} (job {job_id[:8]}){spool}? "
        read, write = self._io(job_id)
        write(f"  {named}{BED_QUESTION.strip()}")
        self.forget()  # shown only now: what was typed before it is no answer to it
        bed = read("bed clear> ").strip().lower()
        why = self.daemon.start(job_id, bed_clear=bed in ("y", "yes"))
        if why:
            self.write(f"  Not taken: {why}.")
            return
        # What came of it, said as it comes: started, or held and why.
        end = time.monotonic() + STARTED_WITHIN_S
        while time.monotonic() < end:
            if any(said.startswith(_START_ENDS) for said in self.drain()):
                return
            time.sleep(0.1)

    def _reject(self, args: list[str]) -> None:
        gate = self.daemon.pending_gate()
        if gate is None:
            self.write("  Nothing is offered to reject. `remove N` takes a job out.")
            return
        if why := self.daemon.reject(str(gate["job_id"])):
            self.write(f"  Not taken: {why}.")

    # -- changing the queue -------------------------------------------------

    def _done(self, why: str) -> None:
        if why:
            self.write(f"  Not done: {why}.")

    def _first(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.first(job_id))

    def _place(self, args: list[str]) -> None:
        job_id = self._job(args)
        if job_id and len(args) > 1 and args[1].isdigit():
            self._done(self.queue.place(job_id, int(args[1])))
        elif job_id:
            self.write("  `place N K` moves N to K in the queue.")

    def _together(self, args: list[str]) -> None:
        if args and args[0].lstrip("#").isdigit():
            self._done(self.queue.together(int(args[0].lstrip("#"))))
        else:
            self.write("  `together S` takes the jobs for spool S first: its number in the stock.")

    def _hold(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.hold(job_id))

    def _release(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.hold(job_id, False))

    def _remove(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.remove(job_id))

    def _spool(self, args: list[str]) -> None:
        job_id = self._job(args)
        if job_id is None:
            return
        if len(args) < 2 or not args[1].lstrip("#").isdigit():
            self.write("  `spool N S` moves N to spool S, its number in the stock (`stock`).")
            return
        said = self.queue.move(job_id, int(args[1].lstrip("#")))
        if isinstance(said, str):
            self.write(f"  Not moved: {said}.")
            return
        for line in said:
            self.write(f"  {line}")

    def _add(self, args: list[str]) -> None:
        adding = parse_adding(args)
        if isinstance(adding, str):
            self.write(f"  Not added: {adding}.")
            return
        added, refused = self.queue.add(adding)
        self.write(f"  Added {len(added)}." if added else "  Nothing added.")
        for why in refused:
            self.write(f"  Not added: {why}")

    def _go(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.go(job_id))
            if self.queue.asked(job_id) is None:
                self._follow(job_id)

    def _retry(self, args: list[str]) -> None:
        if job_id := self._job(args):
            self._done(self.queue.retry(job_id))

    def _stock(self, args: list[str]) -> None:
        self.write(self.stock() if self.stock is not None else "  The stock isn't set up.")

    def _help(self, args: list[str]) -> None:
        self.write(HELP)


def _verb(need: Need | None) -> str:
    return {Need.QUESTIONS: "answer", Need.CLOSER: "answer", Need.REVIEW: "review",
            Need.MESH: "mesh"}.get(
        need or Need.QUESTIONS, "answer")


def stock_listing(ledger_path: Path) -> Callable[[], str]:
    """The stock, as `stock.py` lists it, for `stock`."""

    def listing() -> str:
        with Inventory(ledger_path) as inventory:
            return inventory.listing()

    return listing
