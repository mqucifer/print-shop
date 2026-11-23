"""Reviewers for tests of what the model's settings come to (mqucifer/print-shop#242).

The review doesn't wait for the model, so a test of its settings waits here and, where it needs
them in the print, takes them as a person would: by the numbers on screen. A test of not waiting
uses neither.
"""

from __future__ import annotations

from dataclasses import replace

from print_shop.setup.proposal import ORIENTATION
from print_shop.setup.review import Review, Reviewer, ReviewRequest, parse_answer
from print_shop.setup.suggest import by_number

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
        models = by_number(request.by_model)
        if not models:
            return reviewer(request)
        turns = [n for n, change in models.items() if change.key == ORIENTATION]
        taken = parse_answer("take " + " ".join(map(str, turns or models)), request)
        assert isinstance(taken, Review), taken
        return taken

    return settled(take)
