"""A single job on the phone, as a queue of one (mqucifer/print-shop#288).

`print_job.py part.stl` asks its questions, the model's closer choices and its review at the
Mac, with the terminal's own code. Where the phone's page is on, each of those asks is also a
real `Asked` the phone sees, through the queue's own interface (`OneJob`): so Main, the job's
page, Questions, Review and Change are the queue's screens, with the same session code and the
same `give` (mqucifer/print-shop#221). Nothing about the job is decided twice:

- **Whichever answers first stands.** The ask takes one answer. The Mac first: the phone's
  session sees its ask move on and is put by, and a later phone answer is refused in words.
  The phone first: the Mac's open prompt is put by, with a line saying what was decided and on
  which phone; what was typed there is dropped (`drop_typed`), so it never answers the next
  prompt; and the job goes on with the phone's answer.
- **The Mac's prompt waits on a typed line or the phone's answer** (`PhoneOrMac.read`): `select`
  on stdin, as the model's wait does (`waiting._keys_waiting`), never a second reader. In the
  terminal's own (canonical) mode a line is ready only once Enter is pressed, so a half-typed
  line is still the terminal's, and is dropped.
- **The model's wait takes the phone's go** as it takes `go` typed at the Mac.

Only where the page is on and stdin is a person's terminal: otherwise the lone job reads as it
always has. Hold, first, remove and another spool are the queue's alone.
"""

from __future__ import annotations

import select
import sys
import threading
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, TypeVar

from print_shop.orchestrator.kept import WaitingJob, Waits
from print_shop.orchestrator.print_queue import Adding, Asked, Line, Need
from print_shop.setup.intake import Answer, Asker, Question, drop_typed
from print_shop.setup.review import Review, Reviewer, ReviewRequest
from print_shop.setup.waiting import GO_ON, QuestionsCall, Waiting

__all__ = ["QUEUES_ALONE", "AnsweredOnPhone", "OneJob"]

#: What a lone job doesn't do, and where it's done.
QUEUES_ALONE = ("a single job: hold, first, remove and another spool are the queue's "
                "(`print_job.py --queue`)")
#: How often the Mac's open prompt looks whether the phone has answered.
LOOK_EVERY_S = 0.2

_T = TypeVar("_T")


class AnsweredOnPhone(BaseException):  # noqa: N818 - a signal, not an error
    """The phone answered the ask the Mac's prompt was for. Not an `EOFError`, which the
    terminal's code reads as Enter at some prompts."""


def _keys_ready(timeout_s: float) -> bool:
    return bool(select.select([sys.stdin], [], [], timeout_s)[0])


def _read_line(prompt: str) -> str:
    """`input(prompt)`, once a line is ready: the prompt is drawn, then the line read."""
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")


@dataclass
class OneJob:
    """The run's one job, as the queue's reads and `give` show it to the phone."""

    part: str
    write: Callable[[str], None] = print
    #: Which phone a job's last answer came from, by job: the page says it as it begins.
    who: Callable[[str], str] = lambda job_id: ""
    #: Whether the Mac's prompts wait on the phone too: a person's terminal, and the page on.
    races: bool = False
    keys_ready: Callable[[float], bool] = _keys_ready
    read_line: Callable[[str], str] = _read_line
    forget: Callable[[], None] = drop_typed
    #: The run's daemon, for the job's id and its gate: given once it's built.
    daemon: Any = None
    alone: bool = field(default=True, init=False)
    _asked: Asked | None = field(default=None, init=False)
    _job_id: str = field(default="", init=False)
    _call: QuestionsCall | None = field(default=None, init=False)
    _go: threading.Event = field(default_factory=threading.Event, init=False)
    #: The wait loop saw the phone's go: its next read is it.
    _go_now: bool = field(default=False, init=False)
    #: The Mac's answers to the open ask's prompts, by their number, said if the phone's stand.
    _typed_here: list[str] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def answering_too(self, phone: Any, *, part: str, replayed: bool) -> None:
        """The phone answers this run's job too, where its page is on, the Mac's prompts are a
        person's terminal's, and the answers aren't replayed from a file."""
        self.part = part
        shop = getattr(phone, "shop", None)
        self.races = bool(getattr(phone, "on", False) and shop is not None and not replayed
                          and sys.stdin is not None and sys.stdin.isatty())
        if shop is not None:
            self.who = lambda job_id: shop.by.get(job_id, "")

    # -- the Mac's side ------------------------------------------------------

    def read(self, prompt: str) -> str:
        """The Mac's prompt: a typed line, or the phone's answer to what it's for."""
        if not self.races:
            return input(prompt)
        asked = self._asked
        out = sys.stdout
        out.write(prompt)
        out.flush()
        while not self.keys_ready(LOOK_EVERY_S):
            if asked is not None and not asked.open:
                raise AnsweredOnPhone
        line = self.read_line("")
        number = prompt.rstrip().rstrip(">").strip()
        if asked is not None and asked.open and number.isdigit():
            self._typed_here.append(number)
        return line

    def wait_read(self, prompt: str) -> str:
        """The model's wait reads `go` from the phone as from the Mac: only once its loop has
        seen it (`keys_or_go`), never at another prompt."""
        if self._go_now:
            self._go_now = False
            self.write(printable(f"  {GO_ON}: on your phone{self._by(self._job_id)}."))
            return GO_ON
        return self.read(prompt) if self.races else input(prompt)

    def keys_or_go(self, timeout_s: float) -> bool:
        """Whether the model's wait has a line to read: typed at the Mac, or the phone's go."""
        waited = 0.0
        while waited < timeout_s:
            if self._go.is_set():
                self._go.clear()
                self._go_now = True
                return True
            step = min(LOOK_EVERY_S, timeout_s - waited)
            if self.keys_ready(step):
                return True
            waited += step
        return False

    def _by(self, job_id: str) -> str:
        name = self.who(job_id)
        return f" ({name})" if name else ""

    def _race(self, asked: Asked, terminal: Callable[[], _T],
              said: Callable[[Any], str]) -> _T:
        """The terminal's own code at the Mac, with the phone answering the same ask: whichever
        answers first stands."""
        if not self.races:
            return terminal()  # the page is off, or the answers are replayed: the Mac alone
        with self._lock:
            self._asked, self._typed_here = asked, []
        mine: Any = None
        try:
            mine = terminal()
        except AnsweredOnPhone:
            pass
        except BaseException:
            asked._cancel()  # the Mac's side ended (Ctrl-C, EOF): the phone's page is put by
            raise
        finally:
            with self._lock:
                self._asked = None
        if asked._give(mine):
            found: _T = mine
            return found
        theirs: _T = asked._answer
        self.write(f"\n  {printable(said(theirs))}")
        if self._typed_here:
            numbers = " and ".join(self._typed_here)
            self.write(f"  Your answers here to {numbers} aren't used: the phone's stand.")
        # What was typed here meanwhile was for a prompt that's gone: it answers nothing next.
        self.forget()
        return theirs

    def _job(self) -> str:
        found = self.daemon.active() if self.daemon is not None else None
        if found:
            self._job_id = found
        return self._job_id

    # -- the steps, wrapped ---------------------------------------------------

    def asker(self, terminal: Asker) -> Asker:
        """The questions, at the Mac and on the phone."""
        def ask(questions: Sequence[Question]) -> tuple[Answer, ...]:
            job_id = self._job()
            asked = Asked(job_id=job_id, need=Need.QUESTIONS, questions=tuple(questions))
            return self._race(asked, lambda: terminal(questions),
                              lambda answers: self._answered(job_id, questions, answers))

        return ask

    def reviewer(self, terminal: Reviewer) -> Reviewer:
        """The review, at the Mac and on the phone."""
        def review(request: ReviewRequest) -> Review:
            job_id = self._job()
            asked = Asked(job_id=job_id, need=Need.REVIEW, review=request)
            return self._race(asked, lambda: terminal(request),
                              lambda answer: self._reviewed(job_id, answer))

        return review

    def waiting(self, waiting: Waiting) -> Waiting:
        """The model's wait: its call, for the phone to read and go on without; and its closer
        choices, at the Mac and on the phone."""
        started, ended, closer = waiting.started, waiting.ended, waiting.closer

        def on_start(call: QuestionsCall) -> None:
            self._call = call
            self._go.clear()
            started(call)

        def on_end(call: QuestionsCall, asked: Any) -> None:
            self._go.clear()  # a go the wait never used goes with the call
            ended(call, asked)

        if closer is None:
            return replace(waiting, started=on_start, ended=on_end)
        terminal = closer

        def closer_too(question: Question, kept: Answer, seconds: float) -> Answer | None:
            job_id = self._job()
            asked = Asked(job_id=job_id, need=Need.CLOSER, questions=(question,), kept=kept,
                          seconds=seconds)
            def said(answer: Answer | None) -> str:
                what = _short(question, answer) if answer is not None else "your answer kept"
                return f"Answered on your phone{self._by(job_id)}: {what}. It goes on with that."

            return self._race(asked, lambda: terminal(question, kept, seconds), said)

        return replace(waiting, started=on_start, ended=on_end, closer=closer_too)

    def _answered(self, job_id: str, questions: Sequence[Question],
                  answers: Sequence[Answer]) -> str:
        said = "; ".join(_short(q, a) for q, a in zip(questions, answers, strict=False))
        return f"Answered on your phone{self._by(job_id)}: {said}. It goes on with those."

    def _reviewed(self, job_id: str, answer: Review) -> str:
        by = self._by(job_id)
        if answer.stop:
            return f"Stopped on your phone{by}."
        if answer.approved and not (answer.corrections or answer.taken):
            return f"Approved on your phone{by}."
        changes = [f"{c.key}={c.value}" for c in (*answer.corrections, *answer.taken)]
        if changes:
            return f"Changed on your phone{by}: {', '.join(changes)}; slicing again."
        return f"Answered on your phone{by}."

    # -- the queue's reads and `give`, for one job ------------------------------

    def lines(self) -> list[Line]:
        job_id = self._job()
        if not job_id:
            return []
        asked = self.asked(job_id)
        gate = self.daemon.pending_gate() if self.daemon is not None else None
        offered = bool(gate and gate.get("job_id") == job_id)
        if asked is not None:
            step, waits = f"needs you: {asked.need}", Waits.QUESTIONS
        elif offered:
            step, waits = "offered to print: Start it here, at the Mac or on the card", \
                Waits.READY
        else:
            step, waits = "being prepared", Waits.PREPARING
        job = WaitingJob(job_id=job_id, part=self.part, given={}, waits=waits)
        return [Line(number=1, job=job, step=step,
                     needs=asked.need if asked is not None else None, offered=offered,
                     spool="", loaded=True)]

    def sent(self) -> list[Any]:
        return []

    def find(self, named: str) -> WaitingJob | None:
        return next((li.job for li in self.lines() if li.job.job_id.startswith(named)), None)

    def asked(self, job_id: str) -> Asked | None:
        asked = self._asked
        if asked is None or not asked.open or asked.job_id != job_id:
            return None
        return asked

    def give(self, job_id: str, answer: Any, *, to: Asked | None = None) -> str:
        asked = self.asked(job_id)
        if asked is None:
            return "it isn't waiting for you"
        if to is not None and asked is not to:
            return "it moved on since: what it asks now wasn't answered"
        if asked.need is Need.QUESTIONS and len(tuple(answer)) != len(asked.questions):
            return f"it asks {len(asked.questions)} questions"
        if asked.need is Need.REVIEW and not isinstance(answer, Review):
            return "a review is answered with a Review"
        return "" if asked._give(answer) else "it was answered already"

    def give_alike(self, job_ids: Sequence[str], questions: Sequence[Question],
                   answers: Sequence[Answer]) -> list[str]:
        return []

    def go(self, job_id: str) -> str:
        call = self._call
        if call is None or call.ended() or call.stopped:
            return "it isn't waiting for the model's questions"
        self._go.set()
        return ""

    def reading(self, job_id: str) -> float | None:
        call = self._call
        if call is None or call.ended() or call.stopped:
            return None
        return call.seconds

    def hold(self, job_id: str, held: bool = True) -> str:
        return QUEUES_ALONE

    def first(self, job_id: str) -> str:
        return QUEUES_ALONE

    def remove(self, job_id: str) -> str:
        return QUEUES_ALONE

    def move(self, job_id: str, spool_id: int) -> list[str] | str:
        return QUEUES_ALONE

    def add(self, adding: Sequence[Adding]) -> tuple[list[str], list[str]]:
        return [], [QUEUES_ALONE]


def printable(text: str) -> str:
    """`text` with no control character: what the phone sent is told at the Mac as words, never
    as the terminal's own codes (a cursor move, a title, the clipboard)."""
    return "".join(c if c in "\n\t" or unicodedata.category(c) != "Cc" else "?" for c in text)


def _short(question: Question, answer: Answer) -> str:
    """An answer in short, as the Mac is told it: its option, or its words."""
    if answer.choice is not None and 0 <= answer.choice < len(question.options):
        return question.options[answer.choice].label
    return answer.other or "the notes' answer"
