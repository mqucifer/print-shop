"""A late model proposal says what it is, the same change isn't numbered twice, and ok says what
it leaves out (mqucifer/print-shop#283).

The Sponsor, 2026-11-24, after a print's review: "it was kind of unclear what the model was
suggesting on the settings after it came back just before the last approval". The slice proposed
7) Ironing top -> topmost, the model then proposed 8) the same, under a line that named a section
far up the screen, and an ok approved with neither taken. Here the review runs with a made-up
slice proposal and a made-up model, let go while the prompt is open; nothing reaches a model,
printer, broker or ledger.
"""

from __future__ import annotations

import os
import pty
import re
import select
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from print_shop.setup.proposal import Change, Ending, Proposal, Source, Suggested
from print_shop.setup.reading import Flag
from print_shop.setup.review import (
    Review,
    ReviewRequest,
    cli_reviewer,
    parse_answer,
    render,
    render_arrived,
)
from print_shop.setup.suggest import Suggesting
from tests.reviewing import flat
from tests.test_review import _request

LAID = (0.0, 0.0, 0.0)
WAIT_S = 30.0
ROOT = Path(__file__).resolve().parent.parent
DEADLINE_S = 40.0

#: The slice's: ironing on the lower tops too, so topmost (as `reading._ironing` proposes it).
IRONING = Flag(id="ironing_inside", reading="ironing takes 7 min over 2400 mm2, on 12 heights: "
               "z 2, 4 mm", change=Change(key="ironing_type", preset="top", value="topmost",
                                          reason="the top is seen, the lower tops aren't",
                                          source=Source.READING))
SEAM = Flag(id="seam_seen", reading="98% of 90 wall loops start on the one below: a line",
            change=Change(key="seam_position", preset="aligned", value="back",
                          reason="the walls are seen", source=Source.READING))


def model(key: str, preset: str, value: str, why: str = "made up") -> Change:
    return Change(key=key, preset=preset, value=value, reason=why, source=Source.MODEL)


class Late:
    """The terminal's review, with a model whose proposals can be let go while it's open."""

    def __init__(self, flags: tuple[Flag, ...], *proposed: Change,
                 declined: tuple[str, ...] = ()) -> None:
        self.release = threading.Event()
        self.shown: list[str] = []
        self.forgotten: list[int] = []

        def find(stop: threading.Event) -> Suggested:
            self.release.wait(WAIT_S)
            return Suggested(pose=LAID, ending=Ending.CAME, proposed=proposed,
                             declined=declined, seconds=3.0)

        self.work = Suggesting(find, LAID)
        base = _request(Proposal())
        self.request = replace(base, flags=flags, suggested=self.work.now(),
                               suggesting=self.work)

    def arrive(self) -> None:
        self.release.set()
        assert self.work.wait(WAIT_S)
        until = time.monotonic() + WAIT_S
        while not any("came back after" in text for text in self.shown):
            assert time.monotonic() < until, "its arrival was never said"
            time.sleep(0.005)

    def review(self, *typed: str, arrive_at: int = 1) -> Review:
        answers = list(typed)
        asked = 0

        def read(prompt: str) -> str:
            nonlocal asked
            asked += 1
            if asked == arrive_at:
                self.arrive()
            return answers.pop(0)

        return cli_reviewer(read, self.shown.append,
                            forget=lambda: self.forgotten.append(len(self.shown)),
                            screen=lambda: None, width=lambda: 100)(self.request)

    @property
    def said(self) -> str:
        return "\n".join(self.shown)


def arrived(*proposed: Change, flags: tuple[Flag, ...] = (IRONING,),
            declined: tuple[str, ...] = ()) -> ReviewRequest:
    """A review with the model's proposals in and on screen, as after Enter."""
    found = Suggested(pose=LAID, ending=Ending.CAME, proposed=proposed, declined=declined,
                      seconds=3.0)
    return replace(_request(Proposal()), flags=flags, suggested=found,
                   suggesting=Suggesting.of(found))


# --------------------------------------------------------------------------
# Done when: the arrival line names its changes, and "the same as" where it is
# --------------------------------------------------------------------------


def test_the_arrival_line_names_its_change_and_the_slices_it_is() -> None:
    late = Late((IRONING,), model("ironing_type", "top", "topmost"))
    review = late.review("", "stop")
    assert review.stop
    assert re.search(r"\nThe model came back after \d+ s: 1 change, Ironing top -> topmost, the "
                     r"same as the slice's 1\. Enter shows it \(what you've typed is kept\)\.",
                     late.said)


def test_the_arrival_line_names_each_and_says_where_it_differs() -> None:
    late = Late((IRONING,), model("ironing_type", "top", "no ironing"),
                model("wall_loops", "2", "3"))
    late.review("", "stop")
    assert re.search(r"The model came back after \d+ s: 2 changes, Ironing top -> no ironing, "
                     r"where the slice's 1 has topmost; Walls 2 -> 3\. Enter shows them",
                     flat(late.said))


def test_more_than_three_are_named_up_to_three() -> None:
    late = Late((), *(model(k, "2", "3") for k in ("wall_loops", "top_shell_layers",
                                                   "bottom_shell_layers", "skirt_loops")))
    late.review("", "stop")
    assert ("4 changes, Walls 2 -> 3; Top layers 2 -> 3; Bottom layers 2 -> 3; and 1 more. "
            "Enter shows them") in flat(late.said)


# --------------------------------------------------------------------------
# Done when: one equal to the slice's isn't numbered; take 7 takes it
# --------------------------------------------------------------------------


def test_the_same_change_as_the_slices_isnt_numbered_again() -> None:
    request = arrived(model("ironing_type", "top", "topmost", "the lower tops are inside"))
    [row] = request.by_model
    assert (row.standing, row.number, row.slice) == ("agrees", 0, 1)
    text = render(request, 100)
    assert "  THE MODEL AGREES WITH 1: Ironing top -> topmost (take 1 takes it)" in text
    assert "        the lower tops are inside [model]" in text
    assert "    2) " not in text, "no second number for the same change"
    assert "THE MODEL AGREES WITH 1: Ironing top -> topmost (take 1)" in text, "in Look first"
    said = parse_answer("take 2", request)
    assert said == "take a number from 1 to 1"


def test_take_of_the_slices_number_takes_it_and_the_models_record_says_so() -> None:
    request = arrived(model("ironing_type", "top", "topmost"))
    review = parse_answer("take 1", request)
    assert isinstance(review, Review)
    assert [(c.key, c.value, c.source) for c in review.taken] == [
        ("ironing_type", "topmost", Source.READING)]
    assert review.agreed == ("ironing_type",), "the model's record says it was taken"
    bare = parse_answer("take", request)
    assert isinstance(bare, Review) and bare.agreed == ("ironing_type",)


def test_no_of_the_slices_number_turns_the_models_down_as_no_did() -> None:
    request = arrived(model("ironing_type", "top", "topmost"))
    review = parse_answer("no 1 because every top is seen", request)
    assert isinstance(review, Review)
    assert review.declined == ("ironing_type",) and review.agreed == ()
    [down] = review.corrections
    assert (down.key, down.value, down.said_no_to, down.reason) == (
        "ironing_type", "top", "topmost", "every top is seen")
    alone = arrived(model("wall_loops", "2", "3"))
    assert parse_answer("no 1 because no", alone) == (
        "no turns down one of the model's: 2"), "a slice's no one agrees with is the slice's"


def test_one_that_differs_keeps_its_number_and_names_the_slices() -> None:
    request = arrived(model("ironing_type", "top", "no ironing"))
    [row] = request.by_model
    assert (row.standing, row.number, row.slice) == ("open", 2, 1)
    text = render(request, 100)
    assert "    2) Ironing (ironing_type)  top -> no ironing; the slice's 1 has topmost" in text
    assert "THE MODEL PROPOSES 1 change: 2) Ironing top -> no ironing. None is applied" in text


def test_a_number_on_screen_before_the_arrival_means_the_same_after() -> None:
    # The slice's 1 and 2 are on screen; the model agrees with 1 and proposes walls. Its own
    # close up after the slice's, so 1 and 2 are still the slice's and walls are 3.
    request = arrived(model("ironing_type", "top", "topmost"), model("wall_loops", "2", "3"),
                      flags=(IRONING, SEAM))
    before = replace(request, suggested=None, suggesting=None)
    assert [n for n, _ in before.numbered] == [n for n, _ in request.numbered] == [1, 2]
    assert [(r.standing, r.number) for r in request.by_model] == [("agrees", 0), ("open", 3)]
    review = parse_answer("take 2", request)
    assert isinstance(review, Review)
    assert [(c.key, c.value) for c in review.taken] == [("seam_position", "back")]
    late = Late((IRONING, SEAM), model("ironing_type", "top", "topmost"),
                model("wall_loops", "2", "3"))
    typed = late.review("take 2")  # typed as it arrived, before Enter
    assert [(c.key, c.value) for c in typed.taken] == [("seam_position", "back")]


# --------------------------------------------------------------------------
# "under Settings" goes, and the design note says whose
# --------------------------------------------------------------------------


def test_look_first_says_what_the_model_proposes_not_where() -> None:
    text = render(arrived(model("wall_loops", "2", "3")), 100)
    assert "THE MODEL PROPOSES 1 change: 2) Walls 2 -> 3. None is applied until you take it." in (
        flat(text))
    assert "under Settings. None is applied" not in flat(text)


def test_the_design_note_says_its_the_models() -> None:
    found = Suggested(pose=LAID, ending=Ending.CAME, proposed=(model("wall_loops", "2", "3"),),
                      design_notes=("a fillet at the base would print cleaner",))
    request = replace(_request(Proposal()), suggested=found, suggesting=Suggesting.of(found))
    text = render_arrived(request, 100)
    assert "  The model's note on the design: a fillet at the base would print cleaner" in text
    assert "  design: " not in text


# --------------------------------------------------------------------------
# Done when: ok says what stays and asks once, with #279's refused line
# --------------------------------------------------------------------------


def test_ok_with_a_slice_proposal_not_taken_says_what_stays_and_asks_once() -> None:
    shown: list[str] = []
    forgot: list[int] = []
    typed = iter(["ok", "ok"])
    review = cli_reviewer(lambda prompt: next(typed), shown.append,
                          forget=lambda: forgot.append(len(shown)), screen=lambda: None,
                          width=lambda: 100)(replace(_request(Proposal()), flags=(IRONING,)))
    assert review == Review(approved=True), "nothing is taken"
    assert flat(shown[1]) == ("ok approves without 1: Ironing stays top, not topmost (ironing "
                              "takes 7 min over 2400 mm2). ok again to approve.")
    assert forgot == [2], "what was typed ahead of it is dropped once it's shown"


def test_everything_ok_leaves_out_is_one_block_asked_once() -> None:
    late = Late((IRONING,), model("wall_loops", "2", "3"))
    review = late.review("ironing=0 because a test print", "ok", "ok", arrive_at=99)
    assert review.approved
    block = next(text for text in late.shown if "ok approves without" in text)
    assert block.splitlines()[0] == "  ok approves without:"
    assert block.splitlines()[1:4] == [
        "    - your 'ironing=0', which wasn't set",
        "    - 1: Ironing stays top, not topmost (ironing takes 7 min over 2400 mm2)",
    ] + [block.splitlines()[3]]
    assert block.splitlines()[3].startswith("    - the model's proposals: it's still working (")
    assert block.splitlines()[3].endswith("), and ok closes its call")
    assert block.splitlines()[-1] == "  ok again to approve."
    assert sum("ok again" in text for text in late.shown) == 1, "one prompt, never three"


def test_the_models_on_screen_not_taken_are_said_with_what_stays() -> None:
    late = Late((IRONING,), model("ironing_type", "top", "topmost"),
                model("wall_loops", "2", "3"))
    review = late.review("", "ok", "ok")
    assert review.approved and review.saw is not None
    block = next(text for text in late.shown if "ok approves without" in text)
    assert block.splitlines()[1:3] == [
        "    - 1: Ironing stays top, not topmost (ironing takes 7 min over 2400 mm2)",
        "    - 2, the model's: Walls stay 2, not 3",
    ], "the one the model agrees with is the slice's, said once"


def test_a_proposal_turned_down_isnt_left_out() -> None:
    request = arrived(model("wall_loops", "2", "3"), flags=(), declined=("wall_loops",))
    typed = iter(["ok"])
    shown: list[str] = []
    review = cli_reviewer(lambda prompt: next(typed), shown.append, forget=lambda: None,
                          screen=lambda: None)(request)
    assert review.approved and not any("ok approves without" in s for s in shown)


def test_an_ok_with_nothing_left_out_approves_at_once() -> None:
    typed = iter(["ok"])
    shown: list[str] = []
    review = cli_reviewer(lambda prompt: next(typed), shown.append, forget=lambda: None,
                          screen=lambda: None)(_request(Proposal()))
    assert review == Review(approved=True) and len(shown) == 1


def test_a_proposal_taken_isnt_left_out_at_the_next_ok() -> None:
    request = replace(_request(Proposal()), flags=(IRONING,))
    review = parse_answer("take 1", request)
    assert isinstance(review, Review) and review.taken
    # Taken, it's this print's: sliced again, the reading proposes it no more, and ok approves.
    taken = replace(request, proposal=Proposal(changes=review.taken))
    typed = iter(["ok"])
    approved = cli_reviewer(lambda prompt: next(typed), lambda text: None, forget=lambda: None,
                            screen=lambda: None)(taken)
    assert approved.approved


# --------------------------------------------------------------------------
# The phone's follow-up reads the one block (mqucifer/print-shop#282)
# --------------------------------------------------------------------------


def test_the_phones_buttons_read_the_block() -> None:
    from print_shop.phone.needs import _at_review  # noqa: PLC0415

    block = ("ok approves without: - your 'ironing=0', which wasn't set - 1: Ironing stays top, "
             "not topmost - the model's proposals: it's still working (2 s), and ok closes its "
             "call ok again to approve.")
    head, buttons = _at_review("j1", "v1", block, None)
    assert head.splitlines() == [
        "ok approves without:", "- your 'ironing=0', which wasn't set",
        "- 1: Ironing stays top, not topmost",
        "- the model's proposals: it's still working (2 s), and ok closes its call",
        "ok again to approve."]
    said = " ".join(buttons)
    assert "Check the model again" in said and "Make it ready without them" in said
    assert "Use" not in said and "Show me" not in said
    one, [only] = _at_review("j1", "v1", "ok approves without 1: Ironing stays top, not "
                             "topmost. ok again to approve.", None)
    assert "Make it ready without it" in only and "value=\"ok\"" in only
    assert one == "ok approves without 1: Ironing stays top, not topmost. ok again to approve."


# --------------------------------------------------------------------------
# Done when: one block, a second ok approves, an ok typed ahead doesn't (a pty)
# --------------------------------------------------------------------------

CHILD = """
import sys, threading, time
from dataclasses import replace
from pathlib import Path
sys.path[:0] = [{root!r}, {src!r}]
from print_shop.setup.proposal import Proposal, Suggested
from print_shop.setup.review import cli_reviewer
from print_shop.setup.suggest import Suggesting
from tests.test_late_proposal import IRONING, LAID
from tests.test_review import _request

flag = Path({flag!r})

def find(stop):
    stop.wait(60)  # still working when ok is typed
    return Suggested(pose=LAID)

work = Suggesting(find, LAID)
request = replace(_request(Proposal()), flags=(IRONING,), suggested=work.now(), suggesting=work)

def write(text):
    if "ok again to approve" in text:
        # Held until the person has typed ahead of it.
        print("HOLD", flush=True)
        began = time.monotonic()
        while not flag.exists() and time.monotonic() - began < 30:
            time.sleep(0.02)
    print(text, flush=True)

review = cli_reviewer(write=write, asks=lambda: False)(request)
print("RESULT", review.approved, [(c.key, c.value) for c in review.taken], flush=True)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="a pseudo-terminal")
def test_the_block_asks_once_and_an_ok_typed_ahead_never_answers_it(tmp_path: Path) -> None:
    flag = tmp_path / "typed-ahead"
    fd, side = pty.openpty()
    code = CHILD.format(root=str(ROOT), src=str(ROOT / "src"), flag=str(flag))
    child = subprocess.Popen([sys.executable, "-B", "-c", code], cwd=ROOT, stdin=side,
                             stdout=side, stderr=side, start_new_session=True)
    os.close(side)
    seen = ""
    began = time.monotonic()

    def read_until(pattern: str, after: int = 0) -> re.Match[str]:
        nonlocal seen
        while time.monotonic() - began < DEADLINE_S:
            found = re.compile(pattern, re.S).search(seen, after)
            if found:
                return found
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    seen += os.read(fd, 65536).decode(errors="replace")
                except OSError:
                    break
        raise AssertionError(f"never saw {pattern!r} in:\n{seen[-3000:]}")

    send: Callable[[bytes], int] = lambda data: os.write(fd, data)  # noqa: E731
    try:
        at = read_until(r"\n> ").end()
        send(b"ironing=0 because a test print\n")
        at = read_until(r"To turn it off:\s+ironing_type=no\s+ironing", at).end()
        at = read_until(r"> ", at).end()
        send(b"ok\n")
        at = read_until(r"HOLD\r?\n", at).end()
        send(b"ok\n")  # typed ahead of the block, as it's being drawn
        flag.touch()
        block = read_until(r"ok approves without:.*?ok again to approve\.", at)
        text = " ".join(block.group(0).split())
        assert "your 'ironing=0', which wasn't set" in text
        assert "1: Ironing stays top, not topmost (ironing takes 7 min over 2400 mm2)" in text
        assert "the model's proposals: it's still working (" in text, "one block, not three"
        assert seen.count("ok again") == 1
        at = read_until(r"> ", block.end()).end()
        send(b"find ironing\n")
        found = read_until(r"Settings that fit 'ironing'", at)
        assert "RESULT" not in seen[:found.start()], "the ok typed ahead didn't approve"
        send(b"ok\n")
        result = read_until(r"RESULT ([^\r\n]*)\r?\n", found.end())
        assert result.group(1) == "True []", "the ok after the block approves, taking nothing"
    finally:
        child.kill()
        child.wait(timeout=DEADLINE_S)
        os.close(fd)
