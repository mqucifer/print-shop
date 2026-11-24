"""Filaments, on the phone (mqucifer/print-shop#221): print-shop-ux's approved screen.

What's loaded and what's on the shelf, as the desk reads the stock; a swap job's spools; and
Load, which is the stock's own load (`Shop.load`). Where a swap print left what's on unknown,
Load asks the stock's question first, which says both halves, with nothing pre-selected
(mqucifer/print-shop#218, #246's A), and a load from a page the stock has changed since is
refused. "Print it in … instead" is the queue's own `spool` (mqucifer/print-shop#210). Adding a
spool stays at the Mac.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from html import escape

from print_shop.desk.read import OnHand
from print_shop.inventory import Spool
from print_shop.orchestrator.print_queue import Line
from print_shop.phone.page import page
from print_shop.phone.shop import LoadAsk, spool_words
from print_shop.setup.pauses import PauseFor
from print_shop.setup.reading import SliceReading

__all__ = ["SwapJob", "filaments_page", "load_page", "load_button", "spools_for_job"]

#: A colour the dot can be drawn in: a plain word, which CSS knows or ignores.
_PLAIN_COLOUR = re.compile(r"^[a-z]{3,20}$")


def _e(value: object) -> str:
    return escape(str(value))


def _dot(spool: Spool) -> str:
    colour = spool.colour.strip().lower()
    fill = colour if _PLAIN_COLOUR.fullmatch(colour) else "grey"
    return f"<span class=\"dot\" style=\"background: {fill}\" aria-hidden=\"true\"></span>"


def load_button(spool: Spool, changes: int, words: str = "", *, kind: str = "") -> str:
    """Load, as a form: the stock as the page read it goes with it."""
    cls = f" class=\"{kind}\"" if kind else ""
    return ("<form method=\"post\" action=\"/filaments/load\">"
            f"<input type=\"hidden\" name=\"spool\" value=\"{spool.id}\">"
            f"<input type=\"hidden\" name=\"changes\" value=\"{changes}\">"
            f"<button type=\"submit\"{cls}>{_e(words or 'Load')}</button></form>")


class SwapJob:
    """A job in the queue whose print swaps spools at a pause: what it starts on, and what to."""

    def __init__(self, line: Line, reading: SliceReading) -> None:
        self.line = line
        self.swaps = [p for p in reading.pauses if p.kind is PauseFor.SWAP]


def _tuned(spool: Spool) -> str:
    return f"Tuned: {spool.tuning}." if spool.tuning else "Its variety's standard profile."


def filaments_page(
    on_hand: OnHand, *, changes: int, lines: Sequence[Line], swaps: Sequence[SwapJob],
    said: str = "",
) -> str:
    """Filaments: what's loaded, a swap job's spools, and what's on the shelf, each to load."""
    body = ["<a class=\"back\" href=\"/\">Back to queue</a><h1>Filaments</h1>"]
    if said:
        body.append(f"<div class=\"box\">{_e(said)}</div>")
    loaded_ids = set()
    for now in on_hand.printers:
        spool = now.spool
        if now.not_known:
            body.append(f"<div class=\"now\"><span class=\"amber\">{_e(now.not_known)}."
                        "</span><br><span class=\"small\">Load the one that's on: it asks "
                        "whether that print is over.</span></div>")
        elif spool is not None:
            loaded_ids.add(spool.id)
            body.append(f"<div class=\"now\">{_dot(spool)}<span class=\"part\">"
                        f"{_e(spool_words(spool))}</span><br><span class=\"small\">Loaded on "
                        f"{_e(now.printer.id)}. {_e(_tuned(spool))}</span></div>")
        else:
            body.append(f"<div class=\"now\">Nothing is loaded on {_e(now.printer.id)}.</div>")
    by_id = {s.id: s for s in on_hand.spools}
    for swap in swaps:
        job = swap.line.job
        first = by_id.get(job.spool_id) if job.spool_id is not None else None
        for p in swap.swaps:
            to = f", then {p.to}" if p.to else ""
            starts = spool_words(first) if first is not None else swap.line.spool
            load = (load_button(first, changes, f"I loaded {first.colour}")
                    if first is not None and first.id not in loaded_ids else "")
            body.append(
                f"<div class=\"card\"><span class=\"part amber\">{_e(job.part)}: swap at layer "
                f"{p.before_layer}</span><br><span class=\"small\">Starts on {_e(starts)}"
                f"{_e(to)}. Both are kept with the print.</span>"
                f"<div class=\"buttons\">{load}<a class=\"button\" href=\"/job/"
                f"{_e(job.job_id)}#spool\">Other spool</a></div></div>")
    shelf = [s for s in on_hand.spools if s.id not in loaded_ids]
    if shelf:
        body.append("<h2>On the shelf</h2><ul class=\"rows\">")
        for spool in shelf:
            waiting = sum(1 for li in lines if li.job.spool_id == spool.id)
            who = (f"{waiting} job{'s' if waiting != 1 else ''} waiting for it."
                   if waiting else "On hand.")
            body.append(
                f"<li><div class=\"row\">{_dot(spool)}<span class=\"what\"><span class=\"part\">"
                f"{_e(spool_words(spool))}</span> <span class=\"small\">#{spool.id}</span><br>"
                f"<span class=\"small\">{_e(who)} {_e(_tuned(spool))}</span></span>"
                f"{load_button(spool, changes, kind='second')}</div></li>")
        body.append("</ul>")
    body.append("<p class=\"small\">Add a spool at the Mac: <code>stock.py add</code>.</p>")
    return page("Filaments", "".join(body))


def load_page(ask: LoadAsk) -> str:
    """The stock's question before a load, both halves, with nothing pre-selected."""
    spool = ask.spool
    return page("Is that print over?", (
        "<a class=\"back\" href=\"/filaments\">Back to filaments</a>"
        f"<h1>Load {_e(spool_words(spool))}</h1>"
        f"<div class=\"box\">{_e(ask.question)}</div>"
        "<form method=\"post\" action=\"/filaments/load\">"
        f"<input type=\"hidden\" name=\"spool\" value=\"{spool.id}\">"
        f"<input type=\"hidden\" name=\"changes\" value=\"{ask.changes}\">"
        "<fieldset><label><input type=\"radio\" name=\"answer\" value=\"both\" required> "
        f"Yes: that print is over, and {_e(spool.named())} is on the printer now</label>"
        "<label><input type=\"radio\" name=\"answer\" value=\"alone\" required> "
        "No, or not sure: record the load alone, and Start still holds</label></fieldset>"
        "<button type=\"submit\">Load</button></form>"))


def spools_for_job(line: Line, on_hand: OnHand) -> str:
    """A job's "print it in … instead": the spools on hand but its own, each the queue's own
    move."""
    others = [s for s in on_hand.spools if s.id != line.job.spool_id]
    if not others:
        return ""
    rows = "".join(
        f"<li><div class=\"row\">{_dot(s)}<span class=\"what\">{_e(spool_words(s))} "
        f"<span class=\"small\">#{s.id}</span></span>"
        f"<form method=\"post\" action=\"/job/{_e(line.job.job_id)}/spool\">"
        f"<input type=\"hidden\" name=\"spool\" value=\"{s.id}\">"
        "<button type=\"submit\" class=\"second\">Use it</button></form></div></li>"
        for s in others)
    return (f"<h2 id=\"spool\">Print it in another spool instead?</h2><ul class=\"rows\">{rows}"
            "</ul><p class=\"small\">Another colour of the same filament: it moves at once, "
            "and its slice and review stand. Another material: it goes back to the review, "
            "with your answers kept.</p>")
