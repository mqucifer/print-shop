"""The phone's screens besides Start: Main (the queue), a job's own page, and Finished
(mqucifer/print-shop#221). The screens and their words are print-shop-ux's approved ones.

Each is one HTML string, every value escaped, with no script: a button is a form that posts
to the page's own route (`routes.py`), and a link opens another screen. No screen shows a
setting, a token, or an address but its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape

from print_shop import reprint
from print_shop.desk.read import OnHand
from print_shop.inventory import Pick
from print_shop.ledger import Result, Row, Symptom
from print_shop.orchestrator.kept import Waits, local_when
from print_shop.orchestrator.print_queue import Line, Need, SentTo
from print_shop.outcome_words import outcome_words
from print_shop.phone.filaments import load_button
from print_shop.phone.one_job import QUEUES_ALONE
from print_shop.phone.page import page
from print_shop.phone.shop import NowPrinting, ReadyGroup, ToRecord, spool_words

__all__ = [
    "job_page", "main_page", "remove_page", "run_page", "runs_page",
]

#: The word at a row's right, for what a job needs, as `queue>` names it.
_ACTION = {Need.QUESTIONS: "Answer", Need.CLOSER: "Answer", Need.REVIEW: "Review",
           Need.MESH: "Approve", Need.LOOK: "Look"}
#: Each need's screen, under the job's path.
_SCREEN = {Need.QUESTIONS: "questions", Need.CLOSER: "questions", Need.REVIEW: "review",
           Need.MESH: "mesh", Need.LOOK: "look"}


def _need_href(line: Line) -> str:
    """Where a row opens: the screen for what the job needs, or its own page."""
    screen = _SCREEN.get(line.needs) if line.needs is not None else None
    return f"/job/{line.job.job_id}/{screen}" if screen else f"/job/{line.job.job_id}"


def _e(value: object) -> str:
    return escape(str(value))


def _minutes(seconds: float) -> str:
    return f"{max(1, round(seconds / 60))} min"


def _named(line: Line) -> str:
    copied = f" ({line.job.copied})" if line.job.copied else ""
    return f"{line.job.part}{copied}"


def _row(href: str, what: str, act: str = "", act_href: str = "") -> str:
    """A row that opens `href`; with `act_href`, its action word at the right opens that
    instead (the job offered: its page, and Start)."""
    if act_href:
        return (f"<li><div class=\"row\"><a class=\"what\" href=\"{_e(href)}\">{what}</a>"
                f"<a class=\"act\" href=\"{_e(act_href)}\">{_e(act)}</a></div></li>")
    act_said = f"<span class=\"act\">{_e(act)}</span>" if act else ""
    return (f"<li><a class=\"row\" href=\"{_e(href)}\"><span class=\"what\">{what}</span>"
            f"{act_said}</a></li>")


def _small(text: str) -> str:
    return f"<br><span class=\"small\">{_e(text)}</span>"


def _step(line: Line) -> str:
    """Where a job is, as a sentence."""
    if line.job.held:
        return f"Held: it's never offered until you let it go. {line.step[:1].upper()}" \
               f"{line.step[1:]}."
    return f"{line.step[:1].upper()}{line.step[1:]}."


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _now_card(now: NowPrinting) -> str:
    pause = f"<br><span class=\"amber\">{_e(now.pause)}</span>" if now.pause else ""
    return (f"<div class=\"now\"><span class=\"small\">Printing now</span><br>"
            f"<span class=\"part\">{_e(now.part)}</span>"
            f"{_small(now.said) if now.said else ''}{pause}</div>")


def _waiting_rows(sends: Sequence[SentTo], lines: Sequence[Line],
                  to_record: Sequence[ToRecord]) -> list[str]:
    rows: list[str] = []
    in_a_send: set[str] = set()
    for send in sends:
        for line in send.lines:
            in_a_send.add(line.job.job_id)
            sent = send.sent
            note = f"<br><q>{_e(sent.note)}</q>" if sent.note else ""
            what = (f"<span class=\"part\">{_e(_named(line))}</span>"
                    f"{_small(f'From {sent.by}, {local_when(sent.at)}')}{note}"
                    f"{_small(_step(line))}")
            act = _ACTION.get(line.needs, "") if line.needs is not None else ""
            rows.append(_row(_need_href(line), what, act))
    for line in lines:
        if line.needs is None or line.job.job_id in in_a_send:
            continue
        what = f"<span class=\"part\">{_e(_named(line))}</span>{_small(_step(line))}"
        rows.append(_row(_need_href(line), what, _ACTION[line.needs]))
    for done in to_record:
        what = (f"<span class=\"part\">{_e(done.part)}, finished "
                f"{_e(local_when(done.ended_at))}</span>"
                f"{_small('How did it come out? Nothing waits on this.')}")
        rows.append(_row(f"/run/{done.job_id}", what, "Record"))
    return rows


def _ready_rows(groups: Sequence[ReadyGroup], on_hand: OnHand | None, changes: int) -> str:
    out = []
    first = True
    spools = {s.id: s for s in on_hand.spools} if on_hand is not None else {}
    for group in groups:
        where = "loaded" if group.loaded else "on the shelf"
        out.append(f"<h2>{_e(group.spool)}, {where}</h2>")
        spool = spools.get(group.spool_id) if group.spool_id is not None else None
        if not group.loaded and spool is not None:
            # The stock's own load: where what's on isn't known, it asks both halves first.
            out.append(f"<div class=\"buttons\">"
                       f"{load_button(spool, changes, f'I loaded {spool.colour}')}</div>")
        out.append("<ul class=\"rows\">")
        for ready in group.rows:
            line = ready.line
            count = f" × {ready.count}" if ready.count > 1 else ""
            each = f"{_minutes(ready.each_s)} each" if ready.count > 1 and ready.each_s else (
                _minutes(ready.each_s) if ready.each_s else "")
            since = f"Ready since {local_when(line.job.since)}" if line.job.since else "Ready"
            said = ". ".join(x for x in (each, since) if x) + "."
            if line.job.held:
                said = f"Held: it waits until you let it go. {said}"
            tag = " <span class=\"tag\">Next</span>" if first and not line.job.held else ""
            first = first and line.job.held
            what = (f"<span class=\"part\">{_e(line.job.part)}{_e(count)}</span>{tag}"
                    f"{_small(said)}")
            if line.offered:
                out.append(_row(f"/job/{line.job.job_id}", what, "Start", act_href="/start"))
            else:
                out.append(_row(f"/job/{line.job.job_id}", what))
        out.append("</ul>")
    return "".join(out)


def main_page(
    *, printing: Sequence[NowPrinting], sends: Sequence[SentTo], lines: Sequence[Line],
    to_record: Sequence[ToRecord], ready: Sequence[ReadyGroup], queue_on: bool,
    offered: str = "", on_hand: OnHand | None = None, changes: int = 0, alone: bool = False,
) -> str:
    """Main: what's printing, what's sent to or waits on the person, what's ready by spool, and
    what's getting ready. `offered` is the part offered at the gate in a run without a queue,
    or ""."""
    body = ["<h1>Printing</h1>"]
    body += [_now_card(now) for now in printing]
    if not printing:
        body.append("<p class=\"small\">Nothing is printing.</p>")
    if alone:
        # A lone job's run: its one job, and what it needs (mqucifer/print-shop#288).
        body.append("<h2>This job</h2><ul class=\"rows\">")
        for line in lines:
            what = f"<span class=\"part\">{_e(_named(line))}</span>{_small(_step(line))}"
            if line.needs is not None:
                body.append(_row(_need_href(line), what, _ACTION[line.needs]))
            elif line.offered:
                body.append(_row(f"/job/{line.job.job_id}", what, "Start", act_href="/start"))
            else:
                body.append(_row(f"/job/{line.job.job_id}", what))
        body.append("</ul>")
        if not lines:
            body.append("<p class=\"small\">Its job hasn't begun yet, or has ended.</p>")
        body.append(f"<p class=\"small\">{_e(QUEUES_ALONE[:1].upper() + QUEUES_ALONE[1:])}."
                    "</p>")
        lines, ready = [], []
    waiting = _waiting_rows(sends, lines, to_record)
    body.append("<h2>Sent to you, or waiting on you</h2>")
    body.append(f"<ul class=\"rows\">{''.join(waiting)}</ul>" if waiting
                else "<p class=\"small\">Nothing is waiting on you.</p>")
    if not queue_on:
        body.append("<div class=\"box\"><strong>The queue isn't running</strong>It runs at the "
                    "Mac: <code>print_job.py --queue</code>. What's printing and what's "
                    "finished show here either way.</div>")
        if offered:
            what = f"<span class=\"part\">{_e(offered)}</span>{_small('Ready, and offered.')}"
            body.append(f"<ul class=\"rows\">{_row('/start', what, 'Start')}</ul>")
    if ready:
        body.append("<h2>Ready. They wait until you start them.</h2>")
        body.append(_ready_rows(ready, on_hand, changes))
    getting = [li for li in lines if li.needs is None
               and li.job.waits is not Waits.READY]
    if getting:
        body.append("<h2>Getting ready</h2><ul class=\"rows\">")
        for line in getting:
            body.append(_row(f"/job/{line.job.job_id}",
                             f"<span class=\"part\">{_e(_named(line))}</span>"
                             f"{_small(_getting(line))}"))
        body.append("</ul>")
    return page("Printing", "".join(body))


def _getting(line: Line) -> str:
    if line.job.waits is Waits.PREPARING:
        return "Preparing. Preparing carries on while the printer is busy."
    return _step(line)


# --------------------------------------------------------------------------
# A job
# --------------------------------------------------------------------------


def _button(action: str, words: str, *, kind: str = "", fields: dict[str, str] | None = None,
            ) -> str:
    hidden = "".join(f"<input type=\"hidden\" name=\"{_e(k)}\" value=\"{_e(v)}\">"
                     for k, v in (fields or {}).items())
    cls = f" class=\"{kind}\"" if kind else ""
    return (f"<form method=\"post\" action=\"{_e(action)}\">{hidden}"
            f"<button type=\"submit\"{cls}>{_e(words)}</button></form>")


def job_page(line: Line, *, each_s: int | None, said: str = "", sliced: bool = False,
             spools: str = "", alone: bool = False) -> str:
    """A job's own page: where it is, what it needs, and the queue's actions on it."""
    job = line.job
    rows = [("Where", _step(line))]
    if line.spool:
        rows.append(("Spool", f"{line.spool}, {'loaded' if line.loaded else 'on the shelf'}"))
    if each_s:
        rows.append(("Time", _minutes(each_s)))
    if job.sent is not None and job.sent.by:
        rows.append(("Sent", f"{job.sent.said()[:1].upper()}{job.sent.said()[1:]}"))
    if job.since:
        rows.append(("Since", local_when(job.since)))
    told = f"<div class=\"box\">{_e(said)}</div>" if said else ""
    told += _reprinted(job.given)
    listed = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    needs = ""
    if line.needs is not None:
        needs = (f"<div class=\"box\"><strong>It needs you: {_e(line.needs)}</strong>"
                 f"<a class=\"button\" href=\"{_e(_need_href(line))}\">"
                 f"{_e(_ACTION[line.needs])}</a></div>")
    start = ("<p><a class=\"button\" href=\"/start\">Start it on your phone</a></p>"
             if line.offered else "")
    if sliced:
        start += (f"<p><a href=\"/job/{_e(job.job_id)}/slice\">What the slice shows</a></p>")
    hold = (_button(f"/job/{job.job_id}/release", "Let go") if job.held
            else _button(f"/job/{job.job_id}/hold", "Hold", kind="second"))
    buttons = (f"<div class=\"buttons\">{hold}"
               f"{_button(f'/job/{job.job_id}/first', 'First', kind='second')}</div>"
               f"<p><a href=\"/job/{_e(job.job_id)}/remove\">Remove from the queue</a></p>")
    if alone:
        buttons = (f"<p class=\"small\">{_e(QUEUES_ALONE[:1].upper() + QUEUES_ALONE[1:])}."
                   "</p>")
        spools = ""
    return page(_named(line), (
        "<a class=\"back\" href=\"/\">Back to queue</a>"
        f"<h1>{_e(_named(line))}</h1>{told}<dl>{listed}</dl>{needs}{start}{buttons}"
        f"{spools}"))


def _reprinted(given: dict[str, object]) -> str:
    """A print again's comparison with the run it came from, as it was queued with it."""
    found = given.get("sliced")
    again = found.get("reprinted") if isinstance(found, dict) else None
    if not isinstance(found, dict) or not isinstance(again, dict):
        return ""
    of = str(found.get("reprint_of") or "")[:8]
    differs = [str(d) for d in again.get("differs") or ()]
    said = (f"Sliced again, then compared with job {of}'s file: " + "; ".join(differs) + "."
            if differs else f"The same file as job {of}, not sliced again.")
    return f"<p class=\"small\">{_e(said)}</p>"


def remove_page(line: Line) -> str:
    """Remove asks once."""
    job = line.job
    remove = _button(f"/job/{job.job_id}/remove", "Remove it", kind="danger",
                     fields={"sure": "yes"})
    return page(f"Remove {_named(line)}?", (
        f"<a class=\"back\" href=\"/job/{_e(job.job_id)}\">Back</a>"
        f"<h1>Remove {_e(_named(line))}?</h1>"
        "<div class=\"box\">It leaves the queue, unprinted. What it was sent with stays in its "
        "folder.</div>"
        f"<div class=\"buttons\">{remove}</div>"
        f"<p><a href=\"/job/{_e(job.job_id)}\">Keep it</a></p>"))


# --------------------------------------------------------------------------
# Finished
# --------------------------------------------------------------------------


def _outcome(row: Row) -> tuple[str, str]:
    """The outcome in its words, and its colour's class."""
    if row.result is None:
        return ("Not recorded yet" if row.ended_at else "Printing"), "small"
    words = outcome_words(row.result.value, row.symptom.value if row.symptom else None)
    return words[:1].upper() + words[1:], ("amber" if row.result is Result.FAILED else "good")


def _when(at: str | None) -> str:
    if not at:
        return ""
    try:
        return f"{datetime.fromisoformat(at).astimezone():%A %-d %b, %H:%M}"
    except ValueError:
        return at


def _yours(row: Row) -> str:
    """What the person changed, in a sentence, as `runs` says it: a print again says what it
    came from, and a file sliced by hand that it was."""
    if row.sliced is not None and row.sliced.reprint_of:
        again = row.sliced.reprinted
        how = ("changed: " + ", ".join(again.said_changes()) if again and again.changes
               else "as it was")
        return f"A print again of job {row.sliced.reprint_of[:8]}, {how}."
    if row.sliced is not None:
        return "Sliced by you in Orca. To change this one, slice it again in Orca."
    said = reprint.yours(row)
    return f"{said[:1].upper()}{said[1:]}."


def _again_buttons(row: Row) -> str:
    """Print again, and Change and print again: the terminal's `again`. A file sliced by hand
    prints again as it was: it's changed in Orca."""
    run = _e(row.job_id)
    plain = (f"<form method=\"post\" action=\"/run/{run}/again\"><button type=\"submit\" "
             "class=\"second\">Print again</button></form>")
    if row.sliced is not None and not row.sliced.reprint_of:
        return f"<div class=\"buttons\">{plain}</div>"
    return (f"<div class=\"buttons\">{plain}<a class=\"button\" href=\"/run/{run}/again\">"
            "Change and print again</a></div><p class=\"small\">A new version of this file: "
            "send it to the queue, and its review offers this run's settings.</p>")


def _run_card(row: Row) -> str:
    outcome, colour = _outcome(row)
    spool = spool_words(row.material)
    took = row.real_time_s or row.print_time_s
    facts = ". ".join(x for x in (_when(row.ended_at or row.start_requested_at), spool,
                                  _minutes(took) if took else "") if x) + "."
    lines = [_small(facts)]
    if row.note:
        lines.append(_small(f"Your note: {row.note}"))
    if yours := _yours(row):
        lines.append(_small(yours))
    record = (f"<p><a class=\"button\" href=\"/run/{_e(row.job_id)}\">Record how it came "
              "out</a></p>" if row.awaiting_answer else "")
    again = _again_buttons(row)
    return (f"<div class=\"card\"><div class=\"head\"><a class=\"part\" "
            f"href=\"/run/{_e(row.job_id)}\">{_e(row.part_said)}</a>"
            f"<span class=\"outcome {colour}\">{_e(outcome)}</span></div>"
            f"{''.join(lines)}{record}{again}</div>")


def runs_page(rows: Sequence[Row], *, find: str) -> str:
    """Finished: every run that ended, newest first, or those `find` finds."""
    cards = "".join(_run_card(row) for row in rows)
    if not rows:
        cards = (f"<p class=\"small\">No finished run has \"{_e(find)}\".</p>" if find
                 else "<p class=\"small\">No print has finished yet.</p>")
    return page("Finished prints", (
        "<a class=\"back\" href=\"/\">Back to queue</a><h1>Finished prints</h1>"
        "<form method=\"get\" action=\"/runs\"><input type=\"search\" name=\"find\" "
        f"value=\"{_e(find)}\" placeholder=\"Find by part, colour or note\" "
        "aria-label=\"Find by part, colour or note\"></form>"
        f"{cards}"))


def _outcome_form(row: Row, choices: dict[str, Pick], said: str) -> str:
    told = f"<div class=\"box\">{_e(said)}</div>" if said else ""
    results = "".join(
        f"<label><input type=\"radio\" name=\"result\" value=\"{r.value}\" required> "
        f"{_e(r.value.capitalize())}</label>" for r in Result)
    symptoms = "".join(
        f"<label><input type=\"radio\" name=\"symptom\" value=\"{s.value}\" required> "
        f"{_e(s.value.replace('_', ' ').capitalize())}</label>" for s in Symptom)
    pick = ""
    if choices:
        # #246's A: both halves in the stock's own words, nothing pre-selected.
        options = "".join(
            f"<label><input type=\"radio\" name=\"pick\" value=\"{_e(words)}\" required> "
            f"{_e(words)}<input type=\"hidden\" name=\"picked_{_e(i)}\" "
            f"value=\"{p.spool_id}:{p.changes}\"></label>"
            for i, (words, p) in enumerate(choices.items()))
        pick = (f"<fieldset><legend>What's on the printer now</legend>{options}</fieldset>")
    return (f"{told}<form method=\"post\" action=\"/run/{_e(row.job_id)}/outcome\">"
            f"<fieldset><legend>How did it come out?</legend>{results}</fieldset>"
            f"<fieldset><legend>What went wrong, if anything</legend>{symptoms}</fieldset>"
            f"{pick}<label for=\"note\">Note</label>"
            "<textarea id=\"note\" name=\"note\" maxlength=\"2000\"></textarea>"
            "<p><button type=\"submit\">Record</button></p></form>")


def run_page(row: Row, *, choices: dict[str, Pick], said: str = "", spools_said: str = "",
             ) -> str:
    """A finished run: its outcome and note, and, where it waits for one, the card's Record."""
    outcome, colour = _outcome(row)
    rows = [("Outcome", f"<span class=\"{colour}\">{_e(outcome)}</span>")]
    when = _when(row.ended_at or row.start_requested_at)
    if when:
        rows.append(("Ended" if row.ended_at else "Sent", _e(when)))
    if spool := spool_words(row.material):
        rows.append(("Spool", _e(spool)))
    took = row.real_time_s or row.print_time_s
    if took:
        rows.append(("Time", _e(_minutes(took))))
    if row.note:
        rows.append(("Your note", _e(row.note)))
    if spools_said:
        rows.append(("Spools", _e(spools_said)))
    for pause in row.reading.pauses if row.reading else ():
        rows.append(("Pause", _e(pause.line())))
    listed = "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in rows)
    yours = f"<p>{_e(_yours(row))}</p>" if _yours(row) else ""
    if row.reading is not None:
        yours += f"<p><a href=\"/run/{_e(row.job_id)}/slice\">What the slice shows</a></p>"
    record = _outcome_form(row, choices, said) if row.awaiting_answer else (
        f"<div class=\"box\">{_e(said)}</div>" if said else "")
    return page(row.part_said, (
        "<a class=\"back\" href=\"/runs\">Back to finished</a>"
        f"<h1>{_e(row.part_said)}</h1><dl>{listed}</dl>{yours}{record}"))
