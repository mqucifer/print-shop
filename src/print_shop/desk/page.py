"""The desk's pages, as plain HTML with their own CSS: no script but the copy buttons', no build.

The look is redone after the Sponsor's design session, so this is clean structure, not polish:
one section per thing a job holds, readable in light and dark. Every value from the ledger, the
inventory or a log is escaped.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

from print_shop.desk.again import again
from print_shop.desk.read import JobRecord, JobSummary, OnHand, has_swap, status
from print_shop.inventory import SETUP
from print_shop.ledger import Row
from print_shop.orchestrator.outcome import end_name, no_frames
from print_shop.outcome_words import outcome_words
from print_shop.plate import row_id
from print_shop.setup.proposal import PAUSE, Change, Source

__all__ = ["index_page", "job_page", "not_found_page"]

#: Who each change is from, as the page names it.
_BY = {Source.ANSWER: "your answer", Source.MODEL: "the model", Source.GUARD: "a guard",
       Source.SPONSOR: "you, at the review", Source.READING: "the slice's reading",
       Source.LESSON: "lesson"}

_CSS = """
:root { color-scheme: light dark; --bg: #fbfbfa; --fg: #1d1d1b; --muted: #66645e;
  --line: #deddd8; --card: #ffffff; --accent: #2457a6; --bad: #a12a2a; --good: #2b6e3a;
  --code: #f1f0ec; }
@media (prefers-color-scheme: dark) { :root { --bg: #171716; --fg: #e9e8e4; --muted: #a3a19a;
  --line: #34332f; --card: #1f1f1d; --accent: #8ab4f0; --bad: #f08a8a; --good: #8fd19e;
  --code: #262624; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
header, main { max-width: 72rem; margin: 0 auto; padding: 0 16px; }
header { padding-top: 1.25rem; }
header p { color: var(--muted); margin: .25rem 0 0; }
h1 { font-size: 1.5rem; margin: 0; } h2 { font-size: 1.15rem; margin: 0 0 .5rem; }
h3 { font-size: 1rem; margin: 1rem 0 .25rem; }
a { color: var(--accent); }
section { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 1rem; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: .35rem .5rem;
  border-bottom: 1px solid var(--line); }
th { color: var(--muted); font-weight: 600; font-size: .85rem; }
.scroll { overflow-x: auto; }
.muted { color: var(--muted); } .bad { color: var(--bad); } .good { color: var(--good); }
pre, code { font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
  background: var(--code); border-radius: 4px; }
pre { padding: .6rem .75rem; overflow-x: auto; white-space: pre-wrap; word-break: break-word;
  margin: .25rem 0; }
code { padding: 0 .25rem; }
.copy { display: flex; gap: .5rem; align-items: flex-start; }
.copy pre { flex: 1; min-width: 0; }
button, .button { font: inherit; font-size: .85rem; padding: .3rem .7rem; cursor: pointer;
  border: 1px solid var(--line); border-radius: 6px; background: var(--bg); color: var(--fg);
  text-decoration: none; white-space: nowrap; }
ul { margin: .25rem 0; padding-left: 1.25rem; }
.chosen { font-weight: 600; }
.frames { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
  gap: .75rem; }
.frames figure { margin: 0; } .frames img { width: 100%; border-radius: 4px; display: block; }
.frames figcaption { font-size: .85rem; color: var(--muted); }
.swatch { display: inline-block; width: .8em; height: .8em; border-radius: 50%;
  border: 1px solid var(--line); vertical-align: -.05em; margin-right: .3em; }
"""

_SCRIPT = """
document.querySelectorAll("button[data-copy]").forEach(function (b) {
  b.addEventListener("click", function () {
    var text = document.getElementById(b.dataset.copy).textContent;
    var done = function () { b.textContent = "Copied"; setTimeout(function () {
      b.textContent = "Copy"; }, 1500); };
    if (navigator.clipboard) { navigator.clipboard.writeText(text).then(done); return; }
    var r = document.createRange(); r.selectNodeContents(document.getElementById(b.dataset.copy));
    var s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand("copy");
    done();
  });
});
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        "<header><h1><a href=\"/\" style=\"color:inherit;text-decoration:none\">Print desk</a>"
        "</h1><p>Reads the ledger, the inventory and the jobs folder. It never starts a job, "
        "answers a question or arms the printer.</p></header>"
        f"<main>{body}</main><script>{_SCRIPT}</script></body></html>"
    )


def _when(iso: str | None) -> str:
    """An ISO time from the ledger, in the Mac's own time."""
    if not iso:
        return "-"
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%Y-%m-%d %H:%M %Z}"
    except ValueError:
        return iso


def _minutes(seconds: int | float | None) -> str:
    if seconds is None:
        return "-"
    minutes = round(seconds / 60)
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _swatch(colour: str) -> str:
    """A dot of the spool's colour, when its name is one CSS knows by a plain word."""
    word = colour.strip().lower().replace(" ", "")
    if not word.isalpha():
        return ""
    return f'<span class="swatch" style="background:{word}"></span>'


def _copy(element_id: str, text: str) -> str:
    return (f'<div class="copy"><pre id="{element_id}">{escape(text)}</pre>'
            f'<button type="button" data-copy="{element_id}">Copy</button></div>')


# --- the front page ----------------------------------------------------------


def index_page(on_hand: OnHand, jobs: list[JobSummary], ledger: Path) -> str:
    return _page("Print desk", _on_hand(on_hand) + _jobs(jobs, ledger))


def _on_hand(on_hand: OnHand) -> str:
    if not on_hand.printers:
        return ("<section><h2>On the printer</h2><p>The inventory isn't set up. "
                f"<code>{escape(SETUP)}</code></p></section>")
    parts = ["<section><h2>On the printer</h2>"]
    for now in on_hand.printers:
        nozzle = (f"a {now.nozzle.diameter_mm:g} mm nozzle"
                  + (f" ({escape(now.nozzle.note)})" if now.nozzle.note else "")
                  if now.nozzle else '<span class="bad">no nozzle fitted</span>')
        if now.spool is not None:
            s = now.spool
            spool = (f"{_swatch(s.colour)}{escape(s.colour)} {escape(s.variety)} "
                     f"<span class=\"muted\">(spool #{s.id}"
                     + (f", {s.grams_left:g} g left" if s.grams_left is not None else "")
                     + (f", tuning “{escape(s.tuning)}”" if s.tuning else "") + ")</span>")
        else:
            spool = '<span class="bad">no spool loaded</span>'
        parts.append(f"<p><strong>{escape(now.printer.id)}</strong>, "
                     f"{escape(now.printer.model)}: {nozzle}; {spool}</p>")
    parts.append("<h3>Spools on hand</h3><div class=\"scroll\"><table><tr><th>#</th>"
                 "<th>Colour</th><th>Variety</th><th>Type</th><th>Left</th><th>Tuning</th>"
                 "<th>Where</th></tr>")
    for s in on_hand.spools:
        where = f"loaded on {escape(s.printer_id)}" if s.printer_id else "on hand"
        parts.append(
            f"<tr><td>{s.id}</td><td>{_swatch(s.colour)}{escape(s.colour)}</td>"
            f"<td>{escape(s.variety)}</td><td>{escape(s.type)}</td>"
            f"<td>{f'{s.grams_left:g} g' if s.grams_left is not None else '-'}</td>"
            f"<td>{escape(s.tuning or '-')}</td><td>{where}</td></tr>")
    parts.append("</table></div><h3>Nozzles</h3><ul>")
    parts += [f"<li>{escape(n.line())}</li>" for n in on_hand.nozzles]
    parts.append("</ul></section>")
    return "".join(parts)


def _jobs(jobs: list[JobSummary], ledger: Path) -> str:
    if not jobs:
        return (f"<section><h2>Jobs</h2><p>No jobs yet in <code>{escape(str(ledger))}</code>."
                "</p></section>")
    rows = []
    for j in jobs:
        cls = ' class="bad"' if j.failed else ""
        rows.append(
            f"<tr><td>{_when(j.at)}</td>"
            f"<td><a href=\"/job/{escape(j.job_id)}\">{escape(j.part)}</a></td>"
            f"<td{cls}>{escape(j.status)}</td><td>{_minutes(j.print_time_s)}</td>"
            f"<td>{f'{j.filament_cm3:g} cm³' if j.filament_cm3 is not None else '-'}</td>"
            f"<td>{'colour change' if j.swap else ''}</td>"
            f"<td>{j.frames or ''}</td></tr>")
    return ("<section><h2>Jobs, newest first</h2><div class=\"scroll\"><table><tr>"
            "<th>When</th><th>Part</th><th>Where it is</th><th>Time</th><th>Plastic</th>"
            "<th>Swap</th><th>Frames</th></tr>" + "".join(rows) + "</table></div></section>")


# --- one job -----------------------------------------------------------------


def job_page(record: JobRecord) -> str:
    row = record.row
    if row is None:
        title = record.unprinted.stl_name if record.unprinted else (
            record.part_path.name if record.part_path else record.job_id)
        body = [f"<section><h2>{escape(title)}</h2><p class=\"muted\">job "
                f"{escape(record.job_id)}</p>"]
        if record.unprinted is not None:
            body.append(f"<p>Ended without printing ({escape(str(record.unprinted.outcome))}, "
                        f"{_when(record.unprinted.ended_at)}): "
                        f"{escape(record.unprinted.detail)}</p>")
        else:
            body.append("<p>Never sent to the printer, so the ledger has no row for it. Its log "
                        "is below.</p>")
        body.append("</section>")
        return _page(title, "".join(body) + _log(record))
    if row.sliced is not None:
        # Sliced by hand: no pose, question, proposal, correction or lesson to show.
        sections = [
            _summary(row, record), _again(row, record), _sliced(row), _reading(row),
            _swap(row), _outcome(row, record), _log(record),
        ]
        return _page(row.stl_name, "".join(sections))
    sections = [
        _summary(row, record), _plate(row), _again(row, record), _pose(row), _questions(row),
        _settings(row), _corrections(row), _lessons(row), _reading(row), _swap(row),
        _outcome(row, record), _log(record),
    ]
    return _page(row.stl_name, "".join(sections))


def _summary(row: Row, record: JobRecord) -> str:
    material = row.material.line() if row.material else row.filament
    return (
        f"<section><h2>{escape(row.stl_name)}</h2>"
        f"<p class=\"muted\">job {escape(row.job_id)}, asked to start "
        f"{_when(row.start_requested_at)}</p>"
        f"<p><strong>{escape(status(row))}</strong>. Orca's estimate: "
        f"{_minutes(row.print_time_s)}, {row.filament_cm3:g} cm³, {row.layer_count} layers"
        + ("; with a colour change" if has_swap(row) else "")
        + ("; sliced by hand" if row.sliced is not None else "") + ".</p>"
        f"<p>{escape(material)}</p></section>"
    )


def _again(row: Row, record: JobRecord) -> str:
    a = again(row, record.part_path, record.printers, record.part_found, record.plate_parts,
              record.plate_rows)
    parts = ["<section><h2>Run it again</h2>",
             "<h3>The command</h3>", _copy("again-command", a.command)]
    parts.append("<p class=\"muted\">It doesn't arm the printer: that's on the command's own "
                 "line, as the runbook says.</p>")
    if row.sliced is None:
        parts.append("<h3>The correction line, at the review</h3>")
        parts.append(_copy("again-corrections", a.corrections) if a.corrections
                     else "<p class=\"muted\">No corrections were made at the review.</p>")
    if a.answers is not None:
        parts.append("<h3>The answers file</h3>")
        parts.append(_copy("again-answers", json.dumps(a.answers, indent=2)))
        parts.append(f"<p><a class=\"button\" href=\"/job/{escape(row.job_id)}/answers.json\" "
                     f"download=\"{escape(a.answers_name)}\">Save {escape(a.answers_name)}"
                     "</a></p>")
    if a.notes:
        parts.append("<ul>" + "".join(f"<li>{escape(n)}</li>" for n in a.notes) + "</ul>")
    parts.append("</section>")
    return "".join(parts)


def _sliced(row: Row) -> str:
    """A hand-sliced file's own record (mqucifer/print-shop#164): where it came from, the
    settings Orca wrote, what differs from the tuning, each object's overrides, and Orca's
    estimate beside the real time."""
    kept = row.sliced
    assert kept is not None
    took = row.real_time_s
    parts = [
        "<section><h2>Sliced by hand</h2>",
        "<p>Sent as the file is: nothing was asked, proposed, turned or sliced again, and no "
        "lesson was applied.</p><ul>",
        f"<li>From {escape(kept.source)}"
        + (f", plate {kept.plate} of Orca's sliced plate file" if kept.kind == "plate" else "")
        + f"; the bytes sent have sha256 <code>{escape(kept.gcode_sha256)}</code></li>",
    ]
    if kept.reprint_of:
        parts.append(f"<li>A reprint of <a href=\"/job/{escape(kept.reprint_of)}\">job "
                     f"{escape(kept.reprint_of)}</a></li>")
    parts.append(
        f"<li>Orca's estimate: {_minutes(kept.print_time_s)}, "
        + (f"{kept.filament_g:g} g, " if kept.filament_g is not None else "")
        + f"{kept.filament_cm3:g} cm³. Real: "
        + (f"{_minutes(took)} from its start to its end, heating included"
           if took is not None else "not ended yet")
        + "; the printer doesn't report the filament it used</li>")
    parts.append("</ul><ul>")
    for line in kept.lines():
        tag = "<li class=\"muted\">" if line.startswith("  ") else "<li>"
        parts.append(f"{tag}{escape(line.strip())}</li>")
    parts.append("</ul></section>")
    return "".join(parts)


def _plate(row: Row) -> str:
    """The plate an object was printed on, with a link to each of its parts
    (mqucifer/print-shop#109)."""
    plate = row.plate
    if plate is None:
        return ""
    items = []
    for part in plate.layout.objects:
        said = escape(part.line())
        if part.n == plate.n:
            said = f"<strong>{said}</strong> ← this part"
        else:
            said = f"<a href=\"/job/{escape(row_id(plate.job_id, part.n))}\">{said}</a>"
        items.append(f"<li>{said}</li>")
    return (f"<section><h2>On a plate with others</h2><p>{escape(plate.layout.headline())}</p>"
            f"<ul>{''.join(items)}</ul><p class=\"muted\">The time, plastic and layers are the "
            "whole plate's. The outcome is this part's own.</p></section>")


def _pose(row: Row) -> str:
    accepted = "" if row.orientation_accepted else " (not accepted: the file's own pose)"
    turned = ", ".join(f"{a:g}°" for a in row.rotation_deg)
    return (f"<section><h2>Pose</h2><p>Turned {escape(turned)} by "
            f"{escape(row.orientation_source)}{accepted}.</p>"
            f"<p>{escape(row.orientation_rationale)}</p></section>")


def _questions(row: Row) -> str:
    intake = row.intake
    if intake is None:
        return ""
    parts = ["<section><h2>Questions and answers</h2>"]
    if not intake.questions:
        parts.append(f"<p class=\"muted\">{escape(intake.note or intake.summary())}</p>")
    for n, q in enumerate(intake.questions):
        answer = intake.answers[n] if n < len(intake.answers) else None
        parts.append(f"<h3>{n + 1}. {escape(q.text)}</h3><ul>")
        for i, o in enumerate(q.options):
            mine = answer is not None and answer.choice == i
            mark = " ← your answer" if mine else ""
            cls = ' class="chosen"' if mine else ""
            parts.append(f"<li{cls}>{escape(o.line())}{escape(mark)}</li>")
        parts.append("</ul>")
        if answer is not None and answer.choice is None:
            parts.append(f"<p class=\"chosen\">Your own words: {escape(answer.other)}</p>")
    if intake.note and intake.questions:
        parts.append(f"<p class=\"muted\">{escape(intake.note)}</p>")
    parts.append("</section>")
    return "".join(parts)


def _change_rows(changes: tuple[Change, ...]) -> str:
    rows = []
    for c in changes:
        was = (f"{escape(c.standard)} → {escape(c.preset or '-')}" if c.standard
               else escape(c.preset or "-"))
        by = _BY[c.source] + (f" {c.lesson}" if c.lesson is not None else "")
        cites = ", ".join(x for x in (c.feature, c.knowledge) if x)
        read = f"<br><span class=\"muted\">read: {escape(c.reading)}</span>" if c.reading else ""
        rows.append(
            f"<tr><td><code>{escape(c.key)}</code></td><td>{was}</td>"
            f"<td>{_this_print(c)}</td><td>{escape(c.reason)}{read}</td>"
            f"<td>{escape(by)}{f'<br><span class=muted>{escape(cites)}</span>' if cites else ''}"
            "</td></tr>")
    return ("<div class=\"scroll\"><table><tr><th>Setting</th><th>Preset</th><th>This print</th>"
            "<th>Why</th><th>From</th></tr>" + "".join(rows) + "</table></div>")


def _this_print(change: Change) -> str:
    """The value it printed with, or that it didn't (mqucifer/print-shop#206)."""
    if change.not_applied:
        return f"not applied: {escape(change.not_applied)}"
    return f"<strong>{escape(change.value)}</strong>"


def _settings(row: Row) -> str:
    proposal = row.proposal
    if proposal is None:
        return ""
    parts = ["<section><h2>Settings, over the presets</h2>"]
    if row.profiles is not None:
        used = row.profiles
        parts.append(f"<p class=\"muted\">{escape(used.machine.name)} · "
                     f"{escape(used.process.name)} · {escape(used.filament.name)}, profiles/ at "
                     f"{escape(used.commit[:12] or 'no commit')}"
                     + (" with uncommitted changes" if used.store_dirty else "") + "</p>")
    if proposal.reasoning:
        parts.append(f"<p>{escape(proposal.reasoning)}</p>")
    parts.append(_change_rows(proposal.changes) if proposal.changes
                 else "<p>No change reached the slice.</p>" if proposal.not_applied
                 else "<p>No changes: the presets as they are.</p>")
    if proposal.not_applied:
        parts.append("<h3>Didn't reach the slice</h3>" + _change_rows(proposal.not_applied))
    lists = (
        ("Kept at the preset", [f"{c.key} at {c.preset}: {c.reason}" for c in proposal.kept]),
        ("Notes", list(proposal.notes)),
        ("For the design", list(proposal.design_notes)),
        ("Refused from the model", list(proposal.refused)),
    )
    for title, items in lists:
        if items:
            parts.append(f"<h3>{title}</h3><ul>"
                         + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul>")
    parts.append("</section>")
    return "".join(parts)


def _corrections(row: Row) -> str:
    if row.corrections is None:
        return ""
    if not row.corrections:
        return ("<section><h2>Corrections at the review</h2><p class=\"muted\">None: approved "
                "as proposed.</p></section>")
    return ("<section><h2>Corrections at the review</h2>" + _change_rows(row.corrections)
            + "</section>")


def _lessons(row: Row) -> str:
    given = row.proposal.lessons if row.proposal else ()
    if not given:
        return ""
    items = []
    for g in given:
        used = (f"not given: {g.withheld}" if g.withheld
                else "set it" if g.applied else (g.why or "read by the model"))
        items.append(f"<li>{escape(g.line())} <span class=\"muted\">({escape(used)})</span></li>")
    return ("<section><h2>Lessons from earlier prints</h2><ul>" + "".join(items)
            + "</ul></section>")


def _reading(row: Row) -> str:
    if row.reading is None:
        return ""
    parts = ["<section><h2>The slice, read layer by layer</h2><ul>"]
    parts += [f"<li>{escape(line)}</li>" for line in row.reading.lines()]
    parts.append("</ul>")
    if row.flags:
        parts.append("<h3>Flagged</h3><ul>")
        for f in row.flags:
            proposed = (f" <span class=\"muted\">proposed {escape(f.change.key)}="
                        f"{escape(f.change.value)}: {escape(f.change.reason)}</span>"
                        if f.change else "")
            parts.append(f"<li><strong>{escape(f.id)}</strong>: {escape(f.reading)}{proposed}"
                         "</li>")
        parts.append("</ul>")
    parts.append("</section>")
    return "".join(parts)


def _swap(row: Row) -> str:
    pauses = row.reading.pauses if row.reading else ()
    change = row.proposal.get(PAUSE) if row.proposal else None
    if not pauses and (change is None or change.value == "none"):
        return ""
    parts = ["<section><h2>Colour change</h2><ul>"]
    if change is not None and change.value != "none":
        parts.append(f"<li>At {escape(change.value)} mm: {escape(change.reason)}</li>")
    parts += [f"<li>{escape(p.line())}</li>" for p in pauses]
    parts.append("</ul></section>")
    return "".join(parts)


def _outcome(row: Row, record: JobRecord) -> str:
    parts = ["<section><h2>Outcome</h2><ul>",
             f"<li>Started: {_when(row.started_at)}</li>"]
    if row.start_error:
        parts.append(f"<li class=\"bad\">The start failed: {escape(row.start_error)}</li>")
    if row.ended_at:
        parts.append(f"<li>Ended: {_when(row.ended_at)} (the printer said "
                     f"{escape(end_name(row.printer_end_status))})</li>")
    if row.result is not None:
        cls = "good" if row.result == "success" else "bad"
        said = escape(outcome_words(row.result, row.symptom))
        parts.append(f"<li class=\"{cls}\"><strong>{said}</strong>, "
                     f"answered {_when(row.answered_at)}</li>")
        if row.note:
            parts.append(f"<li>Your note: {escape(row.note)}</li>")
    elif row.ended_at:
        parts.append("<li>No answer yet: it's asked in Home Assistant.</li>")
    parts.append("</ul>")
    if record.frames:
        parts.append(f"<h3>Frames ({len(record.frames)})</h3><div class=\"frames\">")
        for n, frame in enumerate(record.frames):
            src = f"/job/{escape(row.job_id)}/frame/{n}"
            parts.append(f"<figure><a href=\"{src}\"><img src=\"{src}\" loading=\"lazy\" "
                         f"alt=\"{escape(frame.line())}\"></a><figcaption>"
                         f"{escape(frame.line())}, {_when(frame.taken_at)}</figcaption></figure>")
        parts.append("</div>")
        if record.frames_missed:
            parts.append(f"<p class=\"muted\">Some grabs failed; the last: "
                         f"{escape(record.frames_missed)}</p>")
    elif row.started_at:
        parts.append(f"<p class=\"muted\">{escape(no_frames(record.frames_missed))}</p>")
    parts.append("</section>")
    return "".join(parts)


def _log(record: JobRecord) -> str:
    if record.log is None:
        return ("<section><h2>The job's log</h2><p class=\"muted\">None: this job is from "
                "before print_job.py kept one.</p></section>")
    return (f"<section><h2>The job's log</h2><p><a href=\"/job/{escape(record.job_id)}/log\">"
            f"job.log as text</a></p><pre>{escape(record.log)}</pre></section>")


def not_found_page(what: str) -> str:
    return _page("Not found", f"<section><h2>Not found</h2><p>{escape(what)}</p>"
                 "<p><a href=\"/\">Back to the desk</a></p></section>")
