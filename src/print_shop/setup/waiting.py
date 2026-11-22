"""The questions at once: nobody waits on a model call to get the two that need no model.

mqucifer/print-shop#208. Over 17 jobs the questions call failed in 11, and each time the person
had waited 6 to 13 minutes for `PURPOSE` and `SEEN`, which need no model. So:

- **print-shop's two are asked at once,** while the model works;
- **the model's own are added when they arrive.** A list never changes under an open prompt:
  they follow the answers being given. Its purpose question can't replace one already answered,
  so it's shown as closer choices on that answer, where keeping the answer is the default and
  one key. Picking one replaces the answer, by the person's hand, and both are kept on record;
- **the wait for them says how long it has run,** and the person can go on without them. The
  call is then closed and nothing from it reopens the questions;
- **a failed call says so when it fails,** as cut off, timed out or unavailable, with its time.

Going on is a call without an answer, as a failed one is: the intake records which, the review
says it, and the proposal, its bounds and the guards are the same.

**What the part's notes give is read back** in the place of the question (mqucifer/print-shop#236).
Where they give both, nothing waits for the model: after the confirmation a question that's
already here is added as above, and a call still running is closed, saying so.

With recorded answers (`--answers`) nothing is asked early: the file answers the whole set once
the model's questions are known or the person goes on, and the answer it has on purpose stands
over any closer choices. Where the notes give both and the file answers just those, it's used
without waiting.
"""

from __future__ import annotations

import logging
import select
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from print_shop.setup.intake import (
    DIFFERS_PROMPT,
    NOT_WAITED,
    NOTES_PROMPT,
    Added,
    Answer,
    AnswersRefused,
    Asker,
    Intake,
    Question,
    drop_typed,
    parse,
)
from print_shop.setup.questions import PURPOSE, STOCK, Asked, only_stock

__all__ = ["GO_ON", "Prompted", "QuestionsCall", "Waiting", "ask_meanwhile", "cli_waiting", "took"]

logger = logging.getLogger(__name__)

#: What's typed to go on without the model's questions. A word, not Enter alone: a second
#: Enter after the last answer mustn't lose them unseen.
GO_ON = "go"
#: How often a wait that can't rewrite its line says how long it has run.
SAID_EVERY_S = 60.0
#: The most a call that has its answer is given to say so on the screen.
SAYING_S = 5.0


def took(seconds: float) -> str:
    """A wait's length as it's shown: "42 s", "6 min 1 s"."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


class Prompted:
    """`read`, knowing the prompt that's open, so that whatever lands under it can show it again."""

    def __init__(self, read: Callable[[str], str]) -> None:
        self._read = read
        self._prompt = ""

    def __call__(self, prompt: str) -> str:
        self._prompt = prompt
        try:
            return self._read(prompt)
        finally:
            self._prompt = ""

    def prompt(self) -> str:
        """The prompt waiting for an answer, or ""."""
        return self._prompt


class QuestionsCall:
    """The model's questions call, running on a thread of its own."""

    def __init__(
        self,
        propose: Callable[[threading.Event], Asked],
        on_end: Callable[[QuestionsCall, Asked], None],
        stock: tuple[Question, ...] = STOCK,
    ) -> None:
        #: Whether the notes give either of print-shop's two, and both, so nothing waits for
        #: this call (mqucifer/print-shop#236).
        self.from_notes = any(q.noted is not None for q in stock)
        self.unwaited = all(q.noted is not None for q in stock)
        self._propose = propose
        self._on_end = on_end
        self._started = time.monotonic()
        self._took: float | None = None
        self._stop = threading.Event()
        self._done = threading.Event()
        #: What it came to, or None while it runs.
        self.result: Asked | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="intake-questions")

    @property
    def seconds(self) -> float:
        """How long it has run, or ran."""
        return self._took if self._took is not None else time.monotonic() - self._started

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def start(self) -> None:
        self._started = time.monotonic()
        self._thread.start()

    def stop(self) -> None:
        """End it: the model's call is closed, and nothing it says later is used."""
        self._stop.set()

    def arrived(self) -> bool:
        """Whether its answer is here: it has ended, or is only saying so, which is waited out."""
        return self.ended() or (self.result is not None and self.ended(SAYING_S))

    def ended(self, timeout_s: float = 0.0) -> bool:
        """Whether it has ended, and said so, within `timeout_s`."""
        return self._done.wait(timeout_s)

    def _run(self) -> None:
        try:
            found = self._propose(self._stop)
        except Exception as exc:  # the thread's: the job goes on as for any failed call
            logger.exception("the questions call failed")
            why = f"was unavailable ({type(exc).__name__}: {exc})"[:300]
            found = Asked(STOCK, f"{only_stock(self.from_notes)}: the model {why}",
                          {"finish_reason": "unavailable"}, failed=why)
        if self.stopped:
            return
        self._took = time.monotonic() - self._started
        if found.failed:
            found.call = {"seconds": round(self._took, 1), **found.call}
        self.result = found
        try:
            self._on_end(self, found)
        finally:
            self._done.set()


@dataclass(frozen=True)
class Waiting:
    """What the person is shown and can do while the model works on its questions."""

    #: The call has started.
    started: Callable[[QuestionsCall], None]
    #: The call has ended: said as it ends, from its thread, whatever is being typed.
    ended: Callable[[QuestionsCall, Asked], None]
    #: Wait for it. True once it has ended, False if the person goes on without it.
    wait: Callable[[QuestionsCall], bool]
    #: The model's closer choices on purpose, beside the answer given: an answer to them, or
    #: None to keep it. None itself keeps it always, as recorded answers do.
    closer: Callable[[Question, Answer, float], Answer | None] | None
    #: Ask print-shop's two before the model's are in. False for recorded answers.
    stock_first: bool = True
    #: The notes gave both, so it goes on with the call still running: said as it does.
    went_on: Callable[[QuestionsCall], None] = lambda call: None


def ask_meanwhile(
    propose: Callable[[threading.Event], Asked],
    asker: Asker,
    waiting: Waiting,
    price: Callable[[tuple[Question, ...]], tuple[Question, ...]],
    stock: tuple[Question, ...] = STOCK,
) -> Intake:
    """The intake, with print-shop's two asked while the model works on its own.

    Args:
        propose: The model's questions call, ended when the event it's given is set.
        asker: Answers a set of questions.
        waiting: What the person sees and does meanwhile.
        price: The questions with Orca's estimate on each option.
        stock: print-shop's two as this part's notes leave them (`questions.stock`).
    """
    call = QuestionsCall(propose, waiting.ended, stock)
    call.start()
    try:
        waiting.started(call)
        first = tuple(asker(stock)) if waiting.stock_first else ()
        replayed = False
        if call.unwaited and not waiting.stock_first:
            try:
                first, replayed = tuple(asker(stock)), True
            except AnswersRefused as refused:
                if not refused.only_more:
                    raise  # the model's questions wouldn't mend it, so it isn't kept waiting
                logger.debug("the answers file has more than the notes': the call is waited for")
        waited = not call.unwaited or not (waiting.stock_first or replayed)
        came = False
        if waited:
            came = waiting.wait(call)
        elif call.arrived():
            came = waiting.wait(call)
            if replayed and call.result is not None:
                # A file that answered just the notes' two takes none, so that it replays the
                # same whenever the model's arrive.
                came = not (call.result.added or call.result.closer)
        seconds = call.seconds
        asked = call.result
        without = ""
        if asked is not None and asked.failed:
            # A failure is recorded as what it was, also when they typed `go` as it failed.
            without = (f"the model {asked.failed} after {took(seconds)}, so "
                       + ("it added no question" if call.from_notes
                          else "of the questions only purpose and what's seen were asked"))
        elif not came or asked is None:
            late = ("its call was closed and none of them is used" if asked is None
                    else "they came, and your answers file doesn't answer them, so none is used"
                    if replayed else "they came as you did, and weren't used")
            call.stop()
            if not waited:
                waiting.went_on(call)
            without = (f"you went on without the model's questions after {took(seconds)}: {late}"
                       if waited else
                       "the notes gave what it's for and what's seen, so the model's questions "
                       f"weren't waited for: after {took(seconds)}, {late}")
            asked = Asked(STOCK, f"{only_stock(call.from_notes)}: {without}",
                          {**(asked.call if asked is not None and not waited else {}),
                           "finish_reason": "went on" if waited else NOT_WAITED,
                           "seconds": round(seconds, 1)})
    finally:
        call.stop()

    purpose = stock[0]
    # What the notes gave was read back: nothing is asked twice.
    closer = asked.closer if purpose.noted is None else None
    # Priced together, so that the limit on a print's slices is the one it was.
    priced = price(((closer,) if closer else ()) + asked.added)
    closer = priced[0] if closer else None
    added = priced[1:] if closer else priced
    note = asked.note
    if asked.over:
        left = ", ".join(f"'{q.text}'" for q in asked.over)
        note = "; ".join(x for x in (note, f"left out {left}: four questions at most") if x)

    if not waiting.stock_first and not replayed:
        questions, answers, offered = _replayed(asker, closer, added, stock)
        if offered:
            said = ("the answers file's purpose answer kept over the model's closer choices"
                    if questions[0] == purpose
                    else "the answers file picked from the model's closer choices on purpose")
            note = "; ".join(x for x in (note, said) if x)
        return Intake(questions=questions, answers=answers, note=note, call=asked.call,
                      offered=offered, without=without)

    answer = first[0]
    offered = ()
    if closer is not None:
        picked = waiting.closer(closer, answer, seconds) if waiting.closer is not None else None
        if picked is None:
            offered = (closer,)
            said = "kept your purpose answer over the model's closer choices"
        else:
            offered = (purpose,)
            said = (f"picked from the model's closer choices on purpose, in place of your first "
                    f"answer, '{purpose.label(answer)}'")
            purpose, answer = closer, picked
        note = "; ".join(x for x in (note, said) if x)
    more = tuple(asker(Added(added, before=len(first)))) if added else ()
    return Intake(questions=(purpose, *stock[1:], *added), answers=(answer, *first[1:], *more),
                  note=note, call=asked.call, offered=offered, without=without)


def _replayed(
    asker: Asker, closer: Question | None, added: tuple[Question, ...],
    stock: tuple[Question, ...],
) -> tuple[tuple[Question, ...], tuple[Answer, ...], tuple[Question, ...]]:
    """The whole set, for recorded answers.

    The file's answer to print-shop's purpose question stands over closer choices it never saw.
    A file that names one of those choices answers that question in its place.

    Raises:
        AnswersRefused: If the file answers neither set, listing the first.
    """
    questions = (*stock, *added)
    try:
        return questions, tuple(asker(questions)), ((closer,) if closer else ())
    except AnswersRefused as refused:
        if closer is None:
            raise
        instead = (closer, *stock[1:], *added)
        try:
            return instead, tuple(asker(instead)), (stock[0],)
        except AnswersRefused:
            raise refused from None


def _keys_waiting(timeout_s: float) -> bool:
    """Whether a typed line is waiting to be read, within `timeout_s`."""
    return bool(select.select([sys.stdin], [], [], timeout_s)[0])


def _terminal() -> TextIO | None:
    """The screen, where there's a person at a terminal: the timer is drawn straight on it, so
    the job's log keeps a line for the wait and not one for each second."""
    screen = sys.__stdout__
    if screen is not None and screen.isatty() and sys.stdin is not None and sys.stdin.isatty():
        return screen
    return None


def cli_waiting(
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    *,
    replayed: bool = False,
    prompt: Callable[[], str] = lambda: "",
    screen: Callable[[], TextIO | None] = _terminal,
    typed: Callable[[float], bool] = _keys_waiting,
    forget: Callable[[], None] = drop_typed,
) -> Waiting:
    """The wait on the command line. `replayed` is for recorded answers. `prompt` is the one
    that's open (`Prompted.prompt`): it's shown again under anything said while it's open.

    Without a terminal nobody can type `go`: the call runs to its own end, as it did.
    """
    lock = threading.Lock()  # the call's end and the timer both write
    at_prompt = threading.Event()  # an answer is being typed

    def started(call: QuestionsCall) -> None:
        if replayed:
            how = ("If your answers file answers only what the notes give, it doesn't wait for "
                   "the model's questions." if call.unwaited else
                   "Your answers file is used once they're in.")
        elif call.unwaited:
            how = "The notes give what it's for and what's seen, so nothing waits for it."
        else:
            how = "Answer these meanwhile: you won't have to wait for it."
        write(f"The model is reading the part for questions of its own. {how}")
        if not replayed:
            at_prompt.set()

    def ended(call: QuestionsCall, asked: Asked) -> None:
        with lock:
            if call.stopped:
                return
            after = took(call.seconds)
            if asked.failed:
                said = f"The model {asked.failed} after {after}. " + (
                    "It adds no question." if call.from_notes
                    else "Only purpose and what's seen are asked.")
            elif asked.closer or asked.added:
                said = f"The model's questions came after {after}."
                if at_prompt.is_set():
                    said += " They follow the answers you're giving."
            else:
                said = f"The model read the part in {after} and has no other question."
            out = screen()
            open_prompt = prompt() if at_prompt.is_set() else ""
            if open_prompt:
                keep = ("Enter still confirms the notes above" if open_prompt == NOTES_PROMPT
                        else "Enter still takes your answer" if open_prompt == DIFFERS_PROMPT
                        else f"Keep answering {open_prompt.rstrip('> ')}")
                said = f"\n{said}\n{keep} (what you've typed is kept):"
            elif at_prompt.is_set() or out is not None:
                said = f"\n{said}"  # off the line that's being typed on
            write(said)
            if open_prompt and out is not None:
                # On the screen alone: the log gets the prompt with its answer.
                out.write(open_prompt)
                out.flush()

    def wait(call: QuestionsCall) -> bool:
        at_prompt.clear()
        out = screen()
        if call.ended():
            if out is not None:
                forget()
            return True
        write(f"The model is still reading the part ({took(call.seconds)}). "
              "If it has another question, it's added here."
              + (f" Type {GO_ON} and Enter to go on without waiting." if out is not None else ""))
        said_at = time.monotonic()

        def draw() -> None:
            assert out is not None
            out.write(f"  waiting for the model: {took(call.seconds)}\n> ")
            out.flush()

        if out is not None:
            draw()
        while not call.ended():
            if out is None:
                if not call.ended(0.5) and time.monotonic() - said_at >= SAID_EVERY_S:
                    said_at = time.monotonic()
                    write(f"  still waiting for the model: {took(call.seconds)}")
                continue
            if typed(0.5):
                try:
                    line = read("")
                except EOFError:
                    out = None  # nobody left to type: it runs to its own end
                    continue
                if line.strip().lower() == GO_ON:
                    return False
                if not call.ended():
                    write(f"  Type {GO_ON} and Enter to go on without waiting.")
                    draw()
                continue
            with lock:
                if call.result is None:
                    # The line above what's being typed, rewritten, and the cursor put back.
                    out.write(f"\0337\033[1A\r\033[2K  waiting for the model: "
                              f"{took(call.seconds)}\0338")
                    out.flush()
        if out is not None:
            forget()
        return True

    def closer(question: Question, kept: Answer, seconds: float) -> Answer | None:
        lines = [f"1. {PURPOSE.text}  you: {PURPOSE.label(kept)}",
                 f"   The model has read the part since ({took(seconds)}) and has closer choices. "
                 "Each says what it sets:",
                 f"   {question.text}",
                 "   Enter) keep your answer"]
        lines += [f"   {i}) {o.line()}" for i, o in enumerate(question.options, 1)]
        lines.append("   o) other: say it in your own words")
        write("\n".join(lines))
        while True:
            line = read("1> ")
            if not line.strip():
                return None
            parsed = parse(line, question)
            if isinstance(parsed, Answer):
                return parsed
            write(f"  {parsed}, or Enter to keep your answer")

    def went_on(call: QuestionsCall) -> None:
        with lock:
            write("Going on without waiting for the model's questions: the notes gave what it's "
                  "for and what's seen.")

    return Waiting(started=started, ended=ended, wait=wait, went_on=went_on,
                   closer=None if replayed else closer, stock_first=not replayed)
