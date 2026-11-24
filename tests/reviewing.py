"""Reviewers for tests of what the model's settings come to (mqucifer/print-shop#242).

The review doesn't wait for the model, so a test of its settings waits here and, where it needs
them in the print, takes them as a person would: by the numbers on screen. A test of not waiting
uses neither.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from print_shop.setup.proposal import ORIENTATION
from print_shop.setup.review import Review, Reviewer, ReviewRequest, parse_answer
from print_shop.setup.suggest import agreeing, by_number

#: The longest a test waits for a scripted model, so that a regression fails and doesn't hang.
WAIT_S = 30.0


def settled(reviewer: Reviewer) -> Reviewer:
    """The reviewer, shown each review once the model's work for it has ended."""

    def review(request: ReviewRequest) -> Review:
        if request.suggesting is None:
            return reviewer(request)
        assert request.suggesting.wait(WAIT_S), "the scripted model never ended"
        request = request.refreshed()
        answer = reviewer(request)
        # It was shown what came, so it's said so, as a reviewer that refreshes must.
        return answer if answer.saw is not None else replace(answer, saw=request.suggested)

    return review


def takes(reviewer: Reviewer) -> Reviewer:
    """Takes each of the model's proposals by number, a turn alone first, then reviews as
    `reviewer` does."""

    def take(request: ReviewRequest) -> Review:
        # The model's that agree with the slice's are taken by the slice's number, as a person
        # takes them (mqucifer/print-shop#283).
        models = {**by_number(request.by_model), **agreeing(request.by_model)}
        if not models:
            return reviewer(request)
        turns = [n for n, change in models.items() if change.key == ORIENTATION]
        taken = parse_answer("take " + " ".join(map(str, turns or models)), request)
        assert isinstance(taken, Review), taken
        return taken

    return settled(take)


#: How the block of what an `ok` goes without ends (mqucifer/print-shop#283).
OK_AGAIN = "ok again to approve."


def answering(typed: list[str], shown: list[str]) -> Callable[[str], str]:
    """A person's scripted answers at the review, who answers the block of what an `ok` goes
    without (mqucifer/print-shop#283) with ok again, as one who meant their ok: a test of
    something else scripts the ok it means once. A test of the block reads its own answers."""

    def read(prompt: str) -> str:
        if prompt == "> " and shown and shown[-1].rstrip().endswith(OK_AGAIN):
            return "ok"
        return typed.pop(0)

    return read


def screen(shown: list[str]) -> str:
    """The last thing shown that isn't the block of what an `ok` goes without: the review, or
    what its answer was told (mqucifer/print-shop#283)."""
    return next(text for text in reversed(shown) if not text.rstrip().endswith(OK_AGAIN))


def flat(text: str) -> str:
    """A review's text with its wrapping undone, to find what it says whatever the width
    (mqucifer/print-shop#223)."""
    return " ".join(text.split())
