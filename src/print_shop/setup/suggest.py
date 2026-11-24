"""The model's settings as proposals: worked out beside the review, and taken by the person.

mqucifer/print-shop#242. The review used to wait for the settings call or the print loop, up to
800 s, and when that failed the person got the review they could have had at once. So:

- **the review shows at the first slice,** with what needs no model. The model works on a thread
  of its own (`Suggesting`);
- **what it comes to are proposals** (`proposal.Suggested`), numbered on from the slice's. None
  is applied by arriving, and only a number takes one: bare `take` stays the slice's;
- **one never replaces what the person set, used or took,** nor an answer's or a lesson's
  setting. It has no number, and says what stands (`listed`);
- **a pose of the print loop's is a proposal like the rest,** with its question before a big
  change as its words. The settings it chose with that pose are offered once the part is turned,
  with no second call;
- **`no 3 because ...` is the person's correction,** as a no to the loop's question was, and a
  lesson candidate by it. Not taking one is nothing;
- **going on, a turn, a cut-off, a timeout and a model that can't be reached** each end it as
  what it is, with its time. A call that's ended is closed, and nothing it says later is used.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from print_shop.setup.proposal import (
    ORIENTATION,
    Change,
    Ending,
    LoopCall,
    Proposal,
    Source,
    Suggested,
    pose_value,
)

__all__ = [
    "Beside", "Finding", "Listed", "Suggesting", "agreeing", "by_number", "didnt_come", "fell_back",
    "listed", "looked_up", "once_turned", "recorded", "suggested", "turning", "waited",
    "went_on", "THREAD",
]

#: The thread the model works on beside the review, whose log lines the terminal leaves to the
#: job's log: they'd land on the open prompt.
THREAD = "model-settings"

logger = logging.getLogger(__name__)


def waited(seconds: float) -> str:
    """A wait's length as it's said: "42 s", "6 min 1 s"."""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes} min {secs} s" if minutes else f"{secs} s"


def suggested(
    made: Proposal, pose: tuple[float, float, float], *,
    turn: Change | None = None, big: dict[str, str] | None = None,
) -> Suggested:
    """What a finished call proposes, from the proposal its reply made through the bounds and
    the guards: the model's changes, and what it would keep at the preset's value, which differs
    where a guard set it. With `turn`, those settings are for the pose it turns the part to."""
    ending = _failed(made.model)
    words = dict(big or {})
    theirs: tuple[Change, ...] = ()
    if ending is None:
        theirs = tuple(
            c.model_copy(update={"reading": words.pop(c.key, "")})
            for c in (*made.changes, *made.kept) if c.source is Source.MODEL
        )
    if turn is not None:
        words.pop(ORIENTATION, None)
    return Suggested(
        pose=pose,
        ending=ending or Ending.CAME,
        said=made.model if ending else "",
        proposed=(turn,) if turn is not None else theirs,
        once_turned=theirs if turn is not None else (),
        reasoning=made.reasoning,
        design_notes=made.design_notes,
        refused=made.refused,
        model=made.model,
        call=made.call,
        skill_groups=made.skill_groups,
        skill_sha256=made.skill_sha256,
        loop=made.loop,
        loop_ending=made.loop_ending,
        # What it would have asked that no proposal carries, as the guard set it already.
        asked=" ".join(dict.fromkeys(words.values())),
    )


def _failed(model: str) -> Ending | None:
    """How a call failed, as `model.propose_by_model` records one, or None."""
    for ending in (Ending.CUT_OFF, Ending.TIMED_OUT, Ending.UNAVAILABLE):
        if model.startswith(ending.value):
            return ending
    return None


def didnt_come(found: Suggested) -> str:
    """Why the model's settings aren't here, in the waiting line's place, or ""."""
    after = waited(found.seconds)
    if found.ending is Ending.TURNED:
        why = f"the part was turned after {after}, and its call for that pose was closed"
    elif found.ending is Ending.TIMED_OUT:
        why = f"timed out after {after}"
    elif found.ending is Ending.CUT_OFF:
        cause = found.said.partition("cut off: ")[2] or found.said
        why = f"cut off after {after} ({cause})" if cause else f"cut off after {after}"
    elif found.ending is Ending.UNAVAILABLE:
        cause = found.said.partition("unavailable: ")[2] or found.said
        why = f"unavailable after {after}" + (f" ({cause})" if cause else "")
    else:
        return ""
    return f"The model's settings didn't come: {why}. These are the settings without it."


def went_on(found: Suggested) -> str:
    """That the person went on without the model's settings, as the terminal, the gate and the
    record say it, or ""."""
    if found.ending is Ending.WENT_ON:
        why = f"still working after {waited(found.seconds)}, its call was closed"
    elif found.unseen:
        many = len(found.proposed)
        why = ("its 1 proposal came and wasn't seen" if many == 1
               else f"its {many} proposals came and weren't seen")
    else:
        return ""
    return f"Went on without the model's settings: {why}"


def turning(found: Suggested) -> bool:
    """Whether it proposes a turn, so that its reading and notes are for that pose and not for
    the one on screen, until it's taken."""
    return any(c.key == ORIENTATION for c in found.proposed)


def fell_back(found: Suggested) -> str:
    """That the print loop stopped and what's proposed is the single calls', or ""."""
    if not found.loop_ending or found.loop_ending.startswith(("proposed", "stopped")):
        return ""
    return (f"the print loop stopped, {found.loop_ending}: "
            "what's proposed came from the single calls")


def looked_up(proposal: Proposal, found: Suggested | None) -> Proposal:
    """The lessons the print loop looked up, marked so the review says it
    (mqucifer/print-shop#129)."""
    if found is None or not found.looked_up:
        return proposal
    return proposal.model_copy(update={"lessons": tuple(
        g.model_copy(update={"looked_up": True}) if g.id in found.looked_up else g
        for g in proposal.lessons)})


@dataclass(frozen=True)
class Listed:
    """One of the model's proposals as a review shows it."""

    change: Change
    #: "open" has a number. "theirs" says what stands in its place. "so" is so here already.
    #: "agrees" is the same change as one the slice proposes, said with it and not numbered
    #: again (mqucifer/print-shop#283).
    standing: Literal["open", "theirs", "so", "agrees"]
    #: Its number, from where the slice's end, for one that's open.
    number: int = 0
    #: What's set here now, and by whom, for "theirs".
    now: str = ""
    by: str = ""
    #: The slice's proposal on the same setting, by its number, and the value it proposes: the
    #: one it agrees with, or, for one that's open, the one it differs from.
    slice: int = 0
    slice_value: str = ""


def listed(
    found: Suggested | None, proposal: Proposal, pose: str, after: int,
    slices: Mapping[str, tuple[int, str]] | None = None,
) -> list[Listed]:
    """The model's proposals against what's set at this review, numbered on from `after`, the
    slice's. The numbers are for this screen: one that arrives later moves none of them.
    `slices` is the slice's proposals by key, each its number and value: one the model makes too
    is said with it and not numbered again (mqucifer/print-shop#283), so the model's close up
    among themselves, after every number drawn before they came."""
    slices = slices or {}
    # A turn can be in though the settings' own call failed, after the loop fell back.
    if found is None or not found.proposed:
        return []
    rows: list[Listed] = []
    number = after
    for change in found.proposed:
        if change.key in found.declined:
            continue
        if change.key == ORIENTATION:
            now, stands = None, ""
            here = pose
        else:
            now = next((c for c in (*proposal.changes, *proposal.not_applied, *proposal.kept)
                        if c.key == change.key), None)
            here = now.value if now is not None else change.preset
            stands = _whose(now) if now is not None else ""
        n, value = slices.get(change.key, (0, ""))
        if _same(here, change.value):
            rows.append(Listed(change, "so"))
        elif stands:
            rows.append(Listed(change, "theirs", now=here, by=stands))
        elif n and _same(value, change.value):
            rows.append(Listed(change, "agrees", slice=n, slice_value=value))
        else:
            number += 1
            rows.append(Listed(change, "open", number=number, slice=n, slice_value=value))
    return rows


def by_number(rows: list[Listed]) -> dict[int, Change]:
    """The proposals that can be taken or turned down, by the number on screen."""
    return {row.number: row.change for row in rows if row.standing == "open"}


def agreeing(rows: list[Listed]) -> dict[int, Change]:
    """The model's proposals that are the slice's too, by the slice's number: `take` of that
    number takes both, and `no` turns the model's down (mqucifer/print-shop#283)."""
    return {row.slice: row.change for row in rows if row.standing == "agrees"}


def _whose(now: Change) -> str:
    """Whose a setting is, where it's one the model's never replaces, or ""."""
    if now.source is Source.ANSWER:
        return "your answer"
    if now.source is Source.LESSON:
        return f"lesson {now.lesson}" if now.lesson is not None else "a lesson"
    if now.source in (Source.SPONSOR, Source.READING) or now.taken:
        return "you"
    return ""


def _same(here: str, value: str) -> bool:
    try:
        return float(here.rstrip("%")) == float(value.rstrip("%"))
    except ValueError:
        return here == value


#: What works out the proposals for one pose: the settings call, or the print loop. It's ended
#: when the event it's given is set.
Finding = Callable[[threading.Event], Suggested]


class Suggesting:
    """The model at work on one pose's proposals, on a thread of its own, beside the review."""

    def __init__(
        self, find: Finding, pose: tuple[float, float, float],
        calls: list[LoopCall] | None = None,
    ) -> None:
        self._find = find
        self._calls = calls
        self._stop = threading.Event()
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._found = Suggested(pose=pose)
        self._heard: Callable[[Suggested], None] | None = None
        self._thread = threading.Thread(target=self._run, daemon=True, name=THREAD)
        self._thread.start()

    @classmethod
    def of(cls, found: Suggested) -> Suggesting:
        """Proposals already in, with no call: the loop's settings, once the part is turned to
        the pose it chose them for."""
        made = cls.__new__(cls)
        made._find = lambda stop: found
        made._calls = None
        made._stop, made._done = threading.Event(), threading.Event()
        made._done.set()
        made._lock = threading.Lock()
        made._started = time.monotonic()
        made._found, made._heard = found, None
        return made

    def now(self) -> Suggested:
        """What it has come to by now, or how long it has run."""
        with self._lock:
            found = self._found
        if found.ending is Ending.WORKING:
            return found.model_copy(update={"seconds": self._seconds()})
        return found

    def wait(self, timeout_s: float | None = None) -> bool:
        """Whether it has ended, within `timeout_s`. The review never calls it."""
        return self._done.wait(timeout_s)

    def hear(self, heard: Callable[[Suggested], None] | None) -> None:
        """Who's told as it ends, from its thread: whoever has the prompt open. One at a time,
        and None for nobody."""
        with self._lock:
            self._heard = heard

    def stop(self, why: Ending) -> Suggested:
        """End it: its call is closed, and nothing it says later is used. Returns how it ended,
        which is what it came to where it already had."""
        with self._lock:
            if self._found.ending is Ending.WORKING:
                calls = tuple(self._calls or ())
                # The loop's calls so far are kept, as every call it makes is.
                kept = {"loop": calls, "loop_ending": (
                    f"stopped as you {'turned the part' if why is Ending.TURNED else 'went on'},"
                    f" after {len(calls)} calls")} if self._calls is not None else {}
                self._found = self._found.model_copy(update={
                    "ending": why, "seconds": self._seconds(), **kept,
                })
            self._heard = None
            found = self._found
        self._stop.set()
        self._done.set()
        return found

    def changed(self, update: dict[str, object]) -> Suggested:
        """Note on the record what was taken or turned down of it."""
        with self._lock:
            self._found = self._found.model_copy(update=update)
            return self._found

    def _seconds(self) -> float:
        return round(time.monotonic() - self._started, 1)

    def _run(self) -> None:
        try:
            found = self._find(self._stop)
        except Exception as exc:  # the thread's: the job goes on as for any failed call
            if not self._stop.is_set():
                logger.exception("the model's settings couldn't be worked out")
            found = self._found.model_copy(update={
                "ending": Ending.UNAVAILABLE,
                "said": f"{type(exc).__name__}: {exc}"[:300],
            })
        with self._lock:
            if self._found.ending is not Ending.WORKING:
                return  # ended first: a late answer changes nothing
            self._found = found.model_copy(update={"seconds": self._seconds()})
            ended, heard = self._found, self._heard
        self._done.set()
        if heard is not None:
            heard(ended)


def once_turned(found: Suggested, to: str) -> Suggested | None:
    """The print loop's settings for the pose its turn puts the part in, for the review after
    it's turned there: no second call. None where it proposed no turn to that pose."""
    turn = next((c for c in found.proposed if c.key == ORIENTATION and c.value == to), None)
    if turn is None:
        return None
    x, y, z = (float(part) for part in to.split(","))
    return found.model_copy(update={
        "pose": (x, y, z), "proposed": found.once_turned, "once_turned": (),
        "taken": (), "declined": (), "unseen": False, "loop": (), "loop_ending": "",
    })


class Beside:
    """The model's work beside a job's reviews: one at a time, a pose at a time, and what each
    came to, for the record."""

    def __init__(self) -> None:
        self.work: Suggesting | None = None
        self._ended: list[Suggested] = []

    def begin(
        self, find: Finding, pose: tuple[float, float, float],
        calls: list[LoopCall] | None = None,
    ) -> None:
        """`calls` is the print loop's, as it makes them, so that they're kept however it
        ends."""
        self.work = Suggesting(find, pose, calls)

    def carry(self, found: Suggested) -> None:
        """Proposals that are in already: the loop's, for the pose it's now turned to."""
        self.work = Suggesting.of(found)

    def note(self, taken: list[str], declined: list[str]) -> None:
        """What the person took and turned down of those on screen."""
        if self.work is None or not (taken or declined):
            return
        now = self.work.now()
        self.work.changed({"taken": (*now.taken, *taken), "declined": (*now.declined, *declined)})

    def end(
        self, why: Ending, saw: Suggested | None = None, dropped: tuple[str, ...] = (),
    ) -> Suggested | None:
        """The work under way ended, as `why` where it hadn't ended by itself, and kept for the
        record. `saw` is the last screen's: proposals that came after it weren't seen.
        `dropped` is what was taken of it and left behind by a turn."""
        if self.work is None:
            return None
        found = self.work.stop(why)
        if saw is not None and saw.ending is Ending.WORKING and found.proposed:
            found = found.model_copy(update={"unseen": True})
        if dropped:
            found = found.model_copy(update={"dropped": dropped})
        self._ended.append(found)
        self.work = None
        return found

    def close(self) -> None:
        """The job is done with it, however it ended: no call is left running for it."""
        self.end(Ending.WENT_ON)

    @property
    def ended(self) -> tuple[Suggested, ...]:
        return tuple(self._ended)


def recorded(proposal: Proposal, found: tuple[Suggested, ...]) -> Proposal:
    """The proposal as the job records it: each pose's, whole, and what the older readers read
    of the model at the top (its reasoning, call, refusals and the loop's calls)."""
    if not found:
        return proposal
    last = found[-1]
    looped = next((each for each in found if each.loop or each.loop_ending), None)
    notes = list(proposal.notes)
    for each in found:
        here = each is last
        if stopped := fell_back(each):
            notes.append(stopped if here else f"For the pose {pose_value(each.pose)}: {stopped}")
        if each.ending is not Ending.TURNED and (said := didnt_come(each)):
            # Only the last pose's is this print's: an earlier one's is said as that pose's.
            notes.append(said if here else f"For the pose {pose_value(each.pose)}: "
                         + said.replace(" These are the settings without it.", ""))
        if said := went_on(each):
            notes.append(said)
    return proposal.model_copy(update={
        "suggested": found,
        # A reading made for a turn nobody took isn't this print's: it stays with that proposal.
        "reasoning": "" if turning(last) else last.reasoning,
        "model": last.model or last.ending.value,
        "call": last.call or {"finish_reason": last.ending.value, "seconds": last.seconds},
        "skill_groups": last.skill_groups,
        "skill_sha256": last.skill_sha256,
        "design_notes": tuple(dict.fromkeys(
            (*proposal.design_notes, *(() if turning(last) else last.design_notes)))),
        "refused": tuple(dict.fromkeys(
            (*proposal.refused, *(() if turning(last) else last.refused)))),
        "loop": looped.loop if looped else (),
        "loop_ending": looped.loop_ending if looped else "",
        "notes": tuple(dict.fromkeys(notes)),
    })
