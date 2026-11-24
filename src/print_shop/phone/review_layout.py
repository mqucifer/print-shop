"""The review laid out for a phone (mqucifer/print-shop#221): every fact the terminal's review
says (mqucifer/print-shop#223's sections), laid out, never cut. The Sponsor: "lay out, don't cut".

Each section is drawn from the review's own data, not from the terminal's columns:
- Look first, a card per item, its heading a label;
- the print, as rows;
- the settings, a row each, its plain name and from → to on one line, who set it at the right,
  its key small beneath and its why beneath that, grouped by who set them as at the terminal;
- what the slice shows, as the Slice screen's rows;
- the part, as paragraphs; the pose, as rows of pose → overhang, plate and tall; the lessons, a
  card each.

What only the terminal's own words say (the past print's offer in full, the model's proposals
that don't stand, how the model read the part) is said in its words, as paragraphs. Nothing is
in a fixed-width block. A test reads every word and figure of the terminal's review on this.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from html import escape

from print_shop.phone.slice import features_html, worth
from print_shop.setup.names import plain, switched
from print_shop.setup.offer import render as render_offer
from print_shop.setup.proposal import Change, Source, pose_value
from print_shop.setup.review import ReviewRequest
from print_shop.setup.screen import Moved, by_model, sections, warnings, who_set
from print_shop.setup.suggest import turning

__all__ = ["review_sections"]

#: The settings' groups, by who set them, in the terminal's order.
_GROUPS: tuple[tuple[str, tuple[Source, ...]], ...] = (
    ("Yours", (Source.SPONSOR, Source.ANSWER)),
    ("From the slice", (Source.READING,)),
    ("Lessons and guards", (Source.LESSON, Source.GUARD)),
    ("The model", (Source.MODEL,)),
)
#: A Look first line's heading: its leading capitals, as the terminal sets it.
_HEAD = re.compile(r"^((?:[A-Z][A-Z'’]+(?:,|\b)\s*)+?)(?=[:\s][^A-Z]|:|$)")
#: The width the terminal's own words are laid out to, before they're joined into paragraphs.
_WORDS = 200


def _e(value: object) -> str:
    return escape(str(value))


def _small(text: str) -> str:
    return f"<span class=\"small\">{_e(text)}</span>"


def _sentence(title: str) -> str:
    return title[:1].upper() + title[1:].lower()


def paragraphs(lines: Iterable[str]) -> list[str]:
    """The terminal's lines as paragraphs: a line set in deeper than the one its paragraph began
    with goes on it; a blank or a line set in no deeper begins another."""
    out: list[str] = []
    indent = -1
    for line in lines:
        text = line.strip()
        if not text:
            indent = -1
            continue
        here = len(line) - len(line.lstrip())
        if out and indent >= 0 and here > indent:
            out[-1] += f" {text}"
            continue
        out.append(text)
        indent = here
    return out


def _paras(lines: Iterable[str], cls: str = "") -> str:
    klass = f" class=\"{cls}\"" if cls else ""
    return "".join(f"<p{klass}>{_e(p)}</p>" for p in paragraphs(lines))


# --------------------------------------------------------------------------
# The sections
# --------------------------------------------------------------------------


def _look(request: ReviewRequest) -> str:
    cards = []
    for said in warnings(request):
        text = " ".join(said.full.split())
        head = _HEAD.match(text)
        label = head.group(1).strip(" ,") if head is not None else ""
        if not label and text.lower().startswith("note:"):
            label, text = "Note", text[5:].strip()
        rest = text[len(head.group(1)):].lstrip(" :") if head is not None else text
        cls = "card look waiting" if said.waiting else "card look"
        cards.append(f"<div class=\"{cls}\">"
                     + (f"<span class=\"label\">{_e(_sentence(label))}</span> " if label else "")
                     + f"{_linked(rest)}</div>")
    return "".join(cards)


#: The terminal's pointers to a section up its screen, and the phone's section they name.
_POINTERS = {"under What the slice shows": "slice", "under Settings": "settings",
             "under The part": "part", "under Pose": "pose", "under Lessons": "lessons"}


def _linked(text: str) -> str:
    """`text` escaped, with each pointer to a section a link to it on the page."""
    out = _e(text)
    for words, anchor in _POINTERS.items():
        title = words.removeprefix("under ")
        out = out.replace(_e(words), f"under <a href=\"#{anchor}\">{_e(title)}</a>")
    return out


def _print(request: ReviewRequest) -> str:
    s = request.slice
    machine, process, filament = request.presets
    plastic = f"{s.filament_cm3} cm³" + (f", {s.filament_g} g" if s.filament_g else "")
    rows = [("Time", f"{_clock(s.print_time_s)}, Orca's estimate"), ("Plastic", plastic),
            ("Layers", str(s.layer_count) + (" for the whole plate" if request.plate else "")),
            ("Machine", machine), ("Process", process), ("Filament", filament)]
    out = "".join(f"<dt>{_e(k)}</dt><dd>{_e(v)}</dd>" for k, v in rows)
    plate = _paras(request.plate.lines()) if request.plate is not None else ""
    return f"<dl>{out}</dl>{plate}"


def _clock(seconds: float) -> str:
    from print_shop.setup.screen import clock  # noqa: PLC0415 - the terminal's own m:ss

    return clock(seconds)


def _setting_row(c: Change, value: str, who: str, marked: bool, tuned: bool,
                 beneath: Sequence[str]) -> str:
    was = switched(c.key, c.preset) or "-"
    standard = switched(c.key, c.standard) if tuned and c.standard else ""
    standard = f" (standard {standard})" if standard and standard != was else ""
    mark = " <span class=\"tag\">moved</span>" if marked else ""
    below = "".join(f"<br>{_small(b)}" for b in beneath if b)
    return (f"<li><div class=\"row setting\"><span class=\"what\"><span class=\"part\">"
            f"{_e(plain(c.key))}</span>{mark} {_e(was)} → "
            f"<strong>{_e(switched(c.key, value))}</strong><br>{_small(c.key + standard)}"
            f"{below}</span>"
            f"<span class=\"who small\">{_e(who)}</span></div></li>")


def _why(change: Change, request: ReviewRequest) -> list[str]:
    """A change's reason, what it cites and what it was read from, as the review says them; a
    lesson's own words are under Lessons, by its number."""
    lessons = {g.id: g for g in request.proposal.lessons}
    lesson = lessons.get(change.lesson) if change.lesson is not None else None
    if lesson is not None and change.reason == f"learned on {lesson.stl_name}: {lesson.text}":
        return []
    cites = ", ".join(x for x in (change.feature, change.knowledge) if x)
    out = [change.reason + (f" [{cites}]" if cites else "")]
    if change.reading:
        out.append(f"read: {change.reading}")
    return out


def _settings(request: ReviewRequest, marks: Moved) -> str:
    proposal = request.proposal
    changes = proposal.changes
    tuned = any(c.standard for c in (*changes, *proposal.kept))
    out = []
    if not changes:
        out.append("<p class=\"small\">"
                   + ("No change reached the slice." if proposal.not_applied
                      else "No changes: your presets as they are.") + "</p>")
    else:
        out.append(f"<p class=\"small\">Each: {'your tuning' if tuned else 'your preset'} → "
                   "this print, and who set it.</p>")
    found = request.suggested
    here = found if found is not None and not turning(found) else None
    refused = list(dict.fromkeys((*proposal.refused, *(here.refused if here else ()))))
    for title, sources in _GROUPS:
        group = [c for c in changes if c.source in sources]
        model = title == "The model"
        if not group and not (model and refused):
            continue
        rows = [_setting_row(c, c.value, who_set(c), c.key in marks.keys, tuned,
                             _why(c, request)) for c in group]
        rows += [f"<li><div class=\"row\"><span class=\"what\">{_small('refused: ' + r)}"
                 "</span></div></li>" for r in refused] if model else []
        out.append(f"<h3>{_e(title)}</h3><ul class=\"rows\">{''.join(rows)}</ul>")
    if proposal.kept:
        rows = []
        for c in proposal.kept:
            who = (f"lesson {c.lesson}" if c.lesson is not None
                   else f"your answer: {c.feature}" if c.source is Source.ANSWER
                   else who_set(c).split(",")[0])
            rows.append(_setting_row(c, c.preset, who, c.key in marks.keys, tuned, [c.reason]))
        out.append(f"<h3>Kept at your preset</h3><ul class=\"rows\">{''.join(rows)}</ul>")
    if request.offering is not None:
        pose = pose_value(request.orientation.rotation_deg)
        out.append(_fold("The print it learned from, in full",
                         _paras(render_offer(request.offering, proposal, pose, width=_WORDS),
                                "small")))
    elif request.plate is not None:
        from print_shop.setup.screen import NOT_FOR_A_PLATE  # noqa: PLC0415

        out.append(f"<p class=\"small\">{_e(NOT_FOR_A_PLATE)}</p>")
    listed = by_model(request, _WORDS)
    if listed:
        out.append(_fold("The model's proposals, as the terminal lists them",
                         _paras(listed[2:], "small")))
    return "".join(out)


def _slice(request: ReviewRequest, page: str) -> str:
    reading = request.reading
    if reading is None:
        return ""
    found = next((s for s in sections(request, _WORDS) if s.key == "slice"), None)
    said = []
    for text in paragraphs(found.lines if found is not None else ()):
        for label in ("layers", "seam", "by object"):
            if text.startswith(label):
                said.append(f"<p><span class=\"label\">{_e(_sentence(label))}</span> "
                            f"{_e(text[len(label):].strip())}</p>")
    looks = worth(reading, request.flags)
    return (f"<p><a href=\"{_e(page)}\">What the slice shows, a bar a layer</a></p>"
            "<p class=\"small\">Each feature: its time, its area in mm², how many layers and "
            f"its z range.</p>{features_html(reading)}{''.join(said)}"
            + ("<h3>Worth a look</h3>" if looks else "")
            + "".join(f"<div class=\"card look\">{_e(w)}</div>" for w in looks))


def _part(request: ReviewRequest) -> str:
    part = next((s for s in sections(request, _WORDS) if s.key == "part"), None)
    if part is None:
        return ""
    out = []
    for text in paragraphs(part.lines):
        if text in ("How the model read it", "Seen", "Design") or text.startswith(
                "Its context"):
            out.append(f"<h3>{_e(text)}</h3>")
        else:
            out.append(f"<p>{_e(text)}</p>")
    return "".join(out)


def _pose_rows(request: ReviewRequest, name: str = "") -> str:
    o = request.orientation
    out = [f"<h3>{_e(name)}</h3>" if name else "",
           f"<p><span class=\"label\">This print</span> {_e(pose_value(o.rotation_deg))}: "
           f"{_e(o.rationale)}</p>",
           f"<p class=\"small\">Features read in this pose: {_e(o.pose())}</p>"]
    if request.slice.turn_deg:
        from print_shop.core.state import turn_words  # noqa: PLC0415

        taken = "taken" if request.turn_taken else "not taken: see Look first"
        out.append(f"<p class=\"small\">On the plate: turned "
                   f"{_e(turn_words(request.slice.turn_deg))} to fit, {_e(taken)}</p>")
    if not request.candidates:
        return "".join(out)
    measuring = request.measuring
    pending = measuring.pending() if measuring is not None else ()
    failed = measuring.failed() if measuring is not None else {}
    ready = measuring.ready() if measuring is not None else request.candidates
    rows = []
    for c in ready:
        this = c.rotation_deg == o.rotation_deg
        left = f", left out {c.flare_area_mm2:g} mm²" if c.flare_area_mm2 else ""
        no_fit = ". Doesn't fit, however it's turned on the plate" if c.fits_no_way else ""
        rows.append(
            f"<li><div class=\"row\"><span class=\"what\"><span class=\"part\">"
            f"{_e(pose_value(c.rotation_deg))}</span>"
            f"{' <span class=\"tag\">this print</span>' if this else ''}<br>"
            f"{_small(f'overhang {c.overhang_area_mm2:g} mm², on the plate {c.bed_contact_area_mm2:g} mm², tall {c.height_mm:g} mm{left}{no_fit}')}"  # noqa: E501
            "</span></div></li>")
    measured = {c.rotation_deg for c in ready}
    rows += [f"<li><div class=\"row\">{_small(f'{pose_value(r)}: still being measured, orientation waits for it')}</div></li>"  # noqa: E501
             for r in pending if r not in measured and r not in failed]
    rows += [f"<li><div class=\"row\">{_small(f'{pose_value(r)}: could not be measured: {why}')}</div></li>"  # noqa: E501
             for r, why in failed.items()]
    flare = any(c.flare_area_mm2 for c in ready)
    out.append("<p class=\"small\">The poses it weighed. Overhang is what would need support."
               + (" Left out: the round or chamfer rising from the plate, left out of the "
                  "overhang, as the layers under it carry it." if flare else "") + "</p>"
               f"<ul class=\"rows\">{''.join(rows)}</ul>")
    return "".join(out)


def _pose(request: ReviewRequest) -> str:
    if not request.parts:
        return _pose_rows(request)
    from dataclasses import replace  # noqa: PLC0415

    return "".join(_pose_rows(replace(request, orientation=part.orientation,
                                      candidates=part.candidates),
                              f"Part {part.n}, {part.name}") for part in request.parts)


def _lessons(request: ReviewRequest) -> str:
    out = []
    said: dict[str, int] = {}
    for g in request.proposal.lessons:
        used = (f"not given: {g.withheld}" if g.withheld
                else "set it" if g.applied else (g.why or "read by the model"))
        if g.looked_up:
            used += "; the print loop looked it up with past_prints"
        made = f", in {g.material}" if g.material and g.material != "any" else ""
        same = said.get(g.text)
        words = f"the same words as lesson {same}" if same else g.text
        said.setdefault(g.text, g.id)
        setting = f"{plain(g.key)} {g.value} ({g.key})" if g.key else "no setting"
        out.append(
            f"<div class=\"card\"><span class=\"label\">Lesson {g.id}</span> "
            f"<strong>{_e(setting)}</strong>: {_e(used)}<br>{_e(words)}<br>"
            f"{_small(f'From {g.stl_name}, for a part that is {", ".join(g.traits)}{made}. Kept by {g.reviewed_by}.')}"  # noqa: E501
            "</div>")
    return "".join(out)


def _fold(title: str, body: str, *, big: bool = False) -> str:
    """A part of the review that's longer than it's often read: in the page, a tap to open."""
    cls = " class=\"section\"" if big else ""
    return f"<details{cls}><summary>{_e(title)}</summary>{body}</details>"


#: The sections that fold: what's read when it's needed, not each time.
_FOLDED = frozenset({"The part", "Pose",
                     "Lessons from earlier prints, kept by a reviewer, for a part like this"})


def review_sections(request: ReviewRequest, since: Moved | None, slice_page: str) -> str:
    """Every section of the terminal's review, in its order, laid out for a phone."""
    marks = since or Moved()
    parts = [
        ("Look first", _look(request)),
        ("The print", _print(request)),
        ("Settings, over your presets", _settings(request, marks)),
        ("What the slice shows", _slice(request, slice_page)),
        ("The part", _part(request)),
        ("Pose", _pose(request)),
        ("Lessons from earlier prints, kept by a reviewer, for a part like this",
         _lessons(request)),
    ]
    anchors = {"Settings, over your presets": "settings", "What the slice shows": "slice",
               "The part": "part", "Pose": "pose"}
    out = []
    for title, body in parts:
        if not body:
            continue
        anchor = anchors.get(title, "lessons" if title.startswith("Lessons") else "")
        if title in _FOLDED:
            out.append(f"<div id=\"{anchor}\">{_fold(title, body, big=True)}</div>")
        else:
            out.append(f"<h2{f' id={chr(34)}{anchor}{chr(34)}' if anchor else ''}>{_e(title)}"
                       f"</h2>{body}")
    return "".join(out)
