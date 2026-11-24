"""Change and print again, on the phone (mqucifer/print-shop#221): print-shop-ux's approved
screen, over the terminal's own `again` (mqucifer/print-shop#216).

The run's values stay pinned and "you changed" is the same, because it's `reprint`'s path: as it
was, or sliced again from the run's part, pose, profiles and settings with the change over them,
then compared with its G-code. With no script, − and + are forms that draw the page again, and
nothing slices until "Put it in the queue", which only queues it: Start is still a person's, at
the gate. Any other change waits for the review on the phone; until then it's typed at the Mac.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from print_shop.phone.page import page
from print_shop.reprint import Reprint, Suggestion
from print_shop.setup.names import named, switched

__all__ = ["NOZZLE", "NOZZLE_STEP", "again_page", "also_page", "nozzle_typed"]

#: The setting − and + change, and by how much: the nozzle's temperature.
NOZZLE = "nozzle_temperature"
NOZZLE_STEP = 5
#: The most it goes from the run's, either way.
NOZZLE_SPAN = 40


def _e(value: object) -> str:
    return escape(str(value))


def _day(at: str | None) -> str:
    """The run's day, as the screen names it: "Tuesday"."""
    if not at:
        return "that run"
    try:
        return f"{datetime.fromisoformat(at).astimezone():%A}"
    except ValueError:
        return "that run"


def _first(value: str) -> str:
    return value.split(",")[0].strip()


def was_nozzle(found: Reprint) -> int | None:
    """The run's nozzle temperature, as its G-code says it, or None."""
    value = _first(found.config.get(NOZZLE, ""))
    try:
        return round(float(value))
    except ValueError:
        return None


def nozzle_typed(found: Reprint, asked: str) -> str:
    """The change − and + made, as `again>` takes one, or "" for none: within the span of the
    run's, in its steps."""
    was = was_nozzle(found)
    if was is None or not (asked.isdecimal() and asked.isascii()):
        return ""
    now = int(asked)
    if now == was or abs(now - was) > NOZZLE_SPAN:
        return ""
    return f"{NOZZLE}={now}"


def _stepper(run_id: str, was: int, now: int, noted: str) -> str:
    def step(to: int, words: str, label: str) -> str:
        off = abs(to - was) > NOZZLE_SPAN
        return (f"<form method=\"get\" action=\"/run/{_e(run_id)}/again\">"
                f"<input type=\"hidden\" name=\"nozzle\" value=\"{to}\">"
                f"<button type=\"submit\" class=\"second step\" aria-label=\"{label}\""
                f"{' disabled' if off else ''}>{words}</button></form>")

    head = f"<strong>{_e(noted)}</strong>" if noted else ""
    return (f"<div class=\"card\">{head}<div class=\"stepper\"><span class=\"what\">Nozzle"
            f"<br><span class=\"small\">{was} °C last time</span></span>"
            f"{step(now - NOZZLE_STEP, '−', 'Cooler')}"
            f"<span class=\"value\">{now} °C</span>"
            f"{step(now + NOZZLE_STEP, '+', 'Hotter')}</div>"
            f"<p class=\"small\">Change something else, at the Mac for now: <code>again "
            f"{_e(run_id[:8])}</code> at <code>queue&gt;</code>.</p></div>")


def _suggestion(s: Suggestion, day: str) -> str:
    return (f"<div class=\"suggested\">Suggested since {_e(day)}: {_e(named(s.key))} "
            f"{_e(switched(s.key, s.value))}. It isn't applied here."
            f"<details><summary>See why</summary>{_e(s.why)}. Something learned or known "
            f"since {_e(day)}'s print: take it at the Mac with <code>take N</code> at "
            "<code>again&gt;</code>.</details></div>")


def again_page(found: Reprint, *, nozzle: str = "") -> str:
    """The run, again: the change − and + made, what's kept from it, and what's suggested."""
    row = found.row
    day = _day(row.ended_at or row.start_requested_at)
    run_id = row.job_id
    typed = nozzle_typed(found, nozzle) if not found.no_change else ""
    body = [f"<a class=\"back\" href=\"/runs\">Back to finished</a>"
            f"<h1>{_e(row.part_said)}, again</h1>",
            f"<div class=\"box\">Starts exactly as {_e(day)}'s print. No questions, and nothing "
            "changes unless you change it.</div>"]
    was = was_nozzle(found)
    if found.no_change:
        body.append(f"<p class=\"small\">To change this one, slice it again in Orca. "
                    f"({_e(found.no_change)}.)</p>")
    elif was is not None:
        now = int(nozzle) if typed else was
        noted = ""
        if row.symptom is not None and row.symptom != "none":
            noted = f"You noted {row.symptom.value.replace('_', ' ')} last time"
        body.append(_stepper(run_id, was, now, noted))
    if found.settings:
        rows = "".join(f"<dt>{_e(named(c.key))}</dt><dd>{_e(switched(c.key, c.value))}</dd>"
                       for c in found.settings)
        body.append(f"<h2>Kept from {_e(day)}</h2><dl>{rows}</dl>")
    body.append("<p class=\"small\">Pose and all other settings: the same.</p>")
    body += [_suggestion(s, day) for s in found.suggestions]
    body += [f"<p class=\"small\">{_e(said)}.</p>" for said in (*found.unchecked, *found.notes)]
    if typed:
        body.append(f"<p>Put it in the queue slices it again from {_e(day)}'s part, pose and "
                    f"settings, then compares it with {_e(day)}'s file.</p>")
    elif found.gcode is not None:
        body.append(f"<p>No changes: the same file as {_e(day)}, not sliced again.</p>")
    else:
        body.append(f"<p class=\"amber\">It can't print as it was: {_e(found.gone)}. Change "
                    "something to slice it again.</p>")
    can = bool(typed) or found.gcode is not None
    hidden = (f"<input type=\"hidden\" name=\"nozzle\" value=\"{_e(nozzle)}\">" if typed
              else "")
    body.append(f"<form method=\"post\" action=\"/run/{_e(run_id)}/again\">{hidden}"
                f"<button type=\"submit\"{'' if can else ' disabled'}>Put it in the queue"
                "</button></form>")
    return page(f"{row.part_said}, again", "".join(body))


def also_page(run_id: str, *, nozzle: str, lines: tuple[str, ...],
              beyond: tuple[str, ...]) -> str:
    """A change's slice that differs in more than was changed: asked once, as at the terminal."""
    shown = "".join(f"<li><div class=\"row\"><span class=\"what\">{_e(line.strip())}</span>"
                    "</div></li>" for line in lines)
    return page("It also changes more", (
        f"<a class=\"back\" href=\"/run/{_e(run_id)}/again?nozzle={_e(nozzle)}\">Back</a>"
        "<h1>It also changes more</h1>"
        f"<div class=\"box\">This also changes {_e('; '.join(beyond))}, which no change of "
        "yours named.</div>"
        f"<ul class=\"rows\">{shown}</ul>"
        f"<form method=\"post\" action=\"/run/{_e(run_id)}/again\">"
        f"<input type=\"hidden\" name=\"nozzle\" value=\"{_e(nozzle)}\">"
        "<input type=\"hidden\" name=\"also\" value=\"yes\">"
        "<button type=\"submit\">Put it in the queue so</button></form>"
        f"<p><a href=\"/run/{_e(run_id)}/again\">Back to the run as it was</a></p>"))
