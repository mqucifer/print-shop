"""A figure against its limit: one rule for every bench check and need (mqucifer/print-shop#72).

On `eval-fit-3` `pause` said "0.400 mm above the pane at the pause, over 0.4 mm" and failed: the
room was 0.4 by the file's numbers, and 0.40000000000000036 by the floats that added the layers up.
`clearance` held its limits to a millionth of a millimetre, `pause` to none, and `needs` compared a
reply's figure rounded to four places. So a figure equal to its limit passed in one check and failed
in another.

**The rule.** A figure equal to its limit passes, to arithmetic's noise: `ARITHMETIC`, a millionth
of a millimetre (or of whatever unit the limit is in), a constant said here once. It's not a band:
no limit is loosened past it, and a figure off by more is over or under. Every limit comparison in
the bench, and a need's `at_least` and `at_most`, is `under` or `over` here.

**A figure said beside its limit** (`said`) has as many places as it takes to tell it from the
limit, so a reply never says a figure over its limit with the limit's own digits ("0.400 ... over
0.4"). This module is plain Python: `rounds/needs.py` imports it outside the image too.
"""

from __future__ import annotations

#: How far past its limit a figure may be and still be at it: arithmetic's noise, not a band.
ARITHMETIC = 1e-6

#: The most places `said` gives: past these a figure is within `ARITHMETIC` of any limit it meets.
PLACES = 6


def under(value: float, least: float) -> bool:
    """Whether `value` is under its least, by more than arithmetic's noise."""
    return value < least - ARITHMETIC


def over(value: float, most: float) -> bool:
    """Whether `value` is over its most, by more than arithmetic's noise."""
    return value > most + ARITHMETIC


def said(value: float, limit: float, places: int | None = 3) -> str:
    """`value` to `places`, or with `places` None as `:g` says it, and with as many more places as
    it takes to read apart from `limit` when it's over or under it: 0.4000004 against 0.4 is
    "0.400" (it's at the limit), and 0.4001 "0.4001", never "0.400"."""
    text = f"{value:g}" if places is None else f"{value:.{places}f}"
    shown = f"{limit:g}" if places is None else f"{limit:.{places}f}"
    if not (over(value, limit) or under(value, limit)) or float(text) != float(shown):
        return text
    for more in range(1, PLACES + 1):
        text = f"{value:.{more}f}"
        if float(text) != float(f"{limit:.{more}f}"):
            return text
    return f"{value:.{PLACES}f}"
