"""What a job needs from the person, on the phone (mqucifer/print-shop#221): the questions, the
review, a change by plain words, a hand-sliced file's look and the mesh gate. The screens are
print-shop-ux's approved ones.

Each form only says what was picked, as the words the terminal reads (`1`, `o no supports`,
`use 1 3`, `take 2; no 4 because ...`, `key=value because ...`, `ok`). The terminal's own code
answers with them (`session.Session`), so the phone decides nothing the terminal doesn't, and
what the terminal asks after them is asked here (`follow_page`).

Every value is escaped, and there's no script.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape

from print_shop.orchestrator.print_queue import Asked, Line, Need
from print_shop.phone.page import page
from print_shop.phone.review_layout import paragraphs, review_sections
from print_shop.phone.session import Session
from print_shop.setup.intake import DIFFERS_PROMPT, NOTES_PROMPT, WORDS_PROMPT, Question
from print_shop.setup.names import Found, plain, switched, with_unit
from print_shop.setup.offer import Item
from print_shop.setup.proposal import pose_value
from print_shop.setup.review import ReviewRequest
from print_shop.setup.screen import Moved
from print_shop.setup.suggest import by_number

__all__ = [
    "SCREENS", "change_page", "closer_page", "follow_page", "look_page", "mesh_page",
    "questions_page", "review_page", "screen_of", "since_of",
]

#: Each need's screen, by its path under the job's.
SCREENS = {Need.QUESTIONS: "questions", Need.CLOSER: "questions", Need.REVIEW: "review",
           Need.LOOK: "look", Need.MESH: "mesh"}
#: How wide the review's own lines are laid out for a phone.
REVIEW_WIDTH = 46


def _e(value: object) -> str:
    return escape(str(value))


def screen_of(asked: Asked | None) -> str:
    return SCREENS.get(asked.need, "") if asked is not None else ""


def since_of(asked: Asked) -> str:
    """The need's own version, as a form carries it: a form for another is refused."""
    return repr(asked.since)


def _head(line: Line, back: str = "/", back_words: str = "Back to queue") -> str:
    return (f"<a class=\"back\" href=\"{_e(back)}\">{_e(back_words)}</a>"
            f"<h1>{_e(line.job.part)}</h1>")


def _hidden(name: str, value: object) -> str:
    return f"<input type=\"hidden\" name=\"{_e(name)}\" value=\"{_e(value)}\">"


def _small(text: str) -> str:
    return f"<span class=\"small\">{_e(text)}</span>"


# --------------------------------------------------------------------------
# The questions
# --------------------------------------------------------------------------


def _shared_cost(question: Question) -> str:
    """What every option costs alike, said once under the question (as mqucifer/print-shop#266
    has the terminal say it), or ""."""
    costs = {o.cost() for o in question.options}
    return costs.pop() if len(costs) == 1 else ""


def _option_note(question: Question, n: int) -> str:
    """An option's impact, its reason where it's recommended, and its cost where the options'
    differ."""
    option = question.options[n]
    note = f"{option.why}. {option.impact}" if option.recommended and option.why else option.impact
    note = note[:1].upper() + note[1:] if note else note
    shared = _shared_cost(question)
    return note if shared else f"{note}. {option.cost()}".strip(". ")


def _question_card(question: Question, i: int, picked: str = "") -> str:
    radios = []
    for n, option in enumerate(question.options):
        mark = " <span class=\"tag\">recommended</span>" if option.recommended else ""
        on = " checked" if picked == str(n + 1) else ""
        radios.append(
            f"<label><input type=\"radio\" name=\"q{i}\" value=\"{n + 1}\"{on}> "
            f"<span class=\"part\">{_e(option.label)}</span>{mark}<br>"
            f"{_small(_option_note(question, n))}</label>")
    beside = f"<p class=\"small\">{_e(question.beside)}</p>" if question.beside else ""
    posed = f"<p class=\"small\">As it lies: {_e(question.posed)}</p>" if question.posed else ""
    shared = _shared_cost(question)
    cost = f"<p class=\"small\">Each: {_e(shared)}.</p>" if shared else ""
    return (f"<fieldset class=\"card\"><legend>{_e(question.text)}</legend>{beside}{posed}{cost}"
            f"{''.join(radios)}"
            f"<label><input type=\"radio\" name=\"q{i}\" value=\"o\"> In your own words</label>"
            f"<input type=\"text\" name=\"w{i}\" maxlength=\"300\" "
            f"aria-label=\"{_e(question.text)}, in your own words\"></fieldset>")


def questions_page(
    line: Line, asked: Asked, *, alike: Sequence[Line], reading_s: float | None,
    now: datetime, change: frozenset[int] = frozenset(), said: str = "",
) -> str:
    """The questions: what the notes give read back first with "That's right", then each other
    question, nothing pre-selected (Enter takes nothing there), and the same answers for the
    files alike."""
    job_id = line.job.job_id
    questions = asked.questions
    noted = [i for i, q in enumerate(questions) if q.noted is not None]
    body = [_head(line)]
    if said:
        body.append(f"<div class=\"box\">{_e(said)}</div>")
    body.append(f"<form method=\"post\" action=\"/job/{_e(job_id)}/questions\">"
                f"{_hidden('since', since_of(asked))}"
                f"{_hidden('change', ' '.join(str(i) for i in sorted(change)))}")
    if alike:
        names = " and ".join(li.job.part for li in alike)
        body.append(
            "<label class=\"band\"><input type=\"checkbox\" name=\"alike\" value=\""
            f"{_e(' '.join(li.job.job_id for li in alike))}\" checked> Same answers for "
            f"{_e(names)}</label>")
    kept = [i for i in noted if i not in change]
    if kept:
        rows = []
        for i in kept:
            q = questions[i]
            assert q.noted is not None
            rows.append(
                f"<li><div class=\"row\"><span class=\"what\"><span class=\"part\">"
                f"{_e(q.topic)}</span><br>{_small(q.options[q.noted].label)}"
                f"{'<br>' + _small(q.beside) if q.beside else ''}</span>"
                f"<a class=\"act\" href=\"/job/{_e(job_id)}/questions?change="
                f"{_e(' '.join(str(c) for c in sorted({*change, i})))}\">Change</a></div></li>")
        whose = questions[kept[0]].noted_from or "the part's notes"
        body.append(f"<div class=\"box\"><strong>From {_e(whose)}</strong>"
                    f"<ul class=\"rows\">{''.join(rows)}</ul></div>")
    for i, question in enumerate(questions):
        if i in noted and i not in change:
            continue
        body.append(_question_card(question, i))
    if reading_s is not None:
        since = now.timestamp() - reading_s
        at = datetime.fromtimestamp(since).astimezone()
        body.append(
            f"<div class=\"suggested\">The model is still reading the part (reading since "
            f"{at:%H:%M}).<br>{_small('If it has another question, it is added here. You do not have to wait for it.')}"  # noqa: E501
            f" <a href=\"/job/{_e(job_id)}/questions\">Check again</a></div>")
    words = "Done, go on without waiting" if reading_s is not None else "Done"
    if kept and len(kept) == len(questions):
        words = "That's right"
    body.append(f"<button type=\"submit\">{_e(words)}</button></form>")
    if reading_s is not None:
        body.append(f"<form method=\"post\" action=\"/job/{_e(job_id)}/go\">"
                    f"{_hidden('since', since_of(asked))}"
                    "<button type=\"submit\" class=\"second\">Skip the model's questions"
                    "</button></form>")
    return page(f"{line.job.part}: questions", "".join(body))


def closer_page(line: Line, asked: Asked) -> str:
    """The model's closer choices on what it's for: keep yours (as Enter does), or one of them."""
    assert asked.kept is not None and asked.questions
    question = asked.questions[0]
    job_id = line.job.job_id
    radios = [f"<label><input type=\"radio\" name=\"closer\" value=\"\" checked> "
              f"<span class=\"part\">Keep your answer</span><br>"
              f"{_small(asked.kept.other or str(asked.kept.choice))}</label>"]
    radios += [f"<label><input type=\"radio\" name=\"closer\" value=\"{n + 1}\"> "
               f"<span class=\"part\">{_e(o.label)}</span><br>"
               f"{_small(_option_note(question, n))}</label>"
               for n, o in enumerate(question.options)]
    return page(f"{line.job.part}: closer choices", (
        f"{_head(line)}<div class=\"box\">The model read the part and has closer choices on "
        f"what it's for. Each says what it sets.</div>"
        f"<form method=\"post\" action=\"/job/{_e(job_id)}/questions\">"
        f"{_hidden('since', since_of(asked))}"
        f"<fieldset class=\"card\"><legend>{_e(question.text)}</legend>{''.join(radios)}"
        "</fieldset><button type=\"submit\">Done</button></form>"))


# --------------------------------------------------------------------------
# What the terminal asks next, asked here
# --------------------------------------------------------------------------


def _reply(job_id: str, version: str, typed: str, words: str, kind: str = "") -> str:
    cls = f" class=\"{kind}\"" if kind else ""
    return (f"<form method=\"post\" action=\"/job/{_e(job_id)}/reply\">"
            f"{_hidden('version', version)}{_hidden('typed', typed)}"
            f"<button type=\"submit\"{cls}>{_e(words)}</button></form>")


def _unused(request: ReviewRequest | None) -> list[str]:
    """What's ticked in Use these and not used, each as "Bed 70 to 75"."""
    if request is None or request.offering is None:
        return []
    pose = pose_value(request.orientation.rotation_deg)
    return [f"{plain(i.key)} {with_unit(i.key, i.preset) if i.preset else '-'} to "
            f"{with_unit(i.key, i.value)}"
            for i in request.offering.ticked(request.proposal, pose)]


def _at_review(job_id: str, version: str, said: str,
               request: ReviewRequest | None) -> tuple[str, list[str]]:
    """What the reviewer's prompt asks after an answer, in plain words, and buttons that each
    say what they do: never ok by another name."""
    if "ticked" in said and "used yet" in said:
        items = _unused(request)
        it = "it" if len(items) == 1 else "them"
        head = (f"{len(items)} ticked {'isn' if len(items) == 1 else 'aren'}'t used yet: "
                f"{'; '.join(items)}." if items else said)
        return head, [_reply(job_id, version, "use", f"Use {it} first"),
                      _reply(job_id, version, "ok", f"Make it ready without {it}", "second")]
    if "The model is still working" in said:
        return said, [_reply(job_id, version, "", "Check the model again", "second"),
                      _reply(job_id, version, "ok",
                             "Make it ready without the model's proposals", "second")]
    if "haven't seen" in said:
        return said, [_reply(job_id, version, "", "Show me the model's proposals"),
                      _reply(job_id, version, "ok", "Make it ready without them", "second")]
    if "wasn't set" in said and "approve without" in said:
        return said, [_reply(job_id, version, "ok", "Make it ready without it", "second")]
    if "type turn" in said:
        return said, [_reply(job_id, version, "turn", "Take the turn")]
    return said, []


def follow_page(line: Line, session: Session, asked: Asked | None = None) -> str:
    """What the terminal's code said since the last answer, and its prompt, to answer as at
    the Mac: with buttons that each say what they do, and a box for anything else."""
    job_id = line.job.job_id
    prompt = session.prompt or ""
    version = session.stamp
    said = "\n".join(session.said_since()).strip("\n")
    buttons: list[str] = []
    if prompt == WORDS_PROMPT:
        buttons = [_reply(job_id, version, "y", "Yes, that's my decision"),
                   _reply(job_id, version, "n", "No: my words set nothing", "second")]
    elif prompt == DIFFERS_PROMPT:
        buttons = [_reply(job_id, version, "", "Take my answer"),
                   _reply(job_id, version, "n", "Keep what the notes say", "second")]
    elif prompt == NOTES_PROMPT:
        buttons = [_reply(job_id, version, "", "That's right")]
    elif prompt == "> ":
        said, buttons = _at_review(job_id, version, " ".join(said.split()),
                                   asked.review if asked is not None else None)
    elif prompt.endswith("> ") and prompt[:-2].isdigit():
        buttons = [_reply(job_id, version, "", "Keep my words as they are", "second")]
    typed = (f"<form method=\"post\" action=\"/job/{_e(job_id)}/reply\" class=\"typed\">"
             f"{_hidden('version', version)}<label for=\"typed\">Or answer as at the Mac, at "
             f"<code>{_e(prompt.strip())}</code></label>"
             "<input id=\"typed\" type=\"text\" name=\"typed\" maxlength=\"2000\" "
             "autocapitalize=\"off\" spellcheck=\"false\">"
             "<button type=\"submit\" class=\"second\">Answer</button></form>")
    told = "".join(f"<p>{_e(p)}</p>" for p in paragraphs(said.split("\n")))
    return page(f"{line.job.part}: it asks", (
        f"{_head(line)}<div class=\"box\">{told}</div>"
        f"<div class=\"buttons\">{''.join(buttons)}</div>{typed}"))


# --------------------------------------------------------------------------
# The review
# --------------------------------------------------------------------------


def _item_row(n: int, item: Item, request: ReviewRequest) -> str:
    offering = request.offering
    assert offering is not None
    pose = pose_value(request.orientation.rotation_deg)
    standing = offering.standing(item, request.proposal, pose)
    on = " checked" if standing.selected else ""
    usable = standing.mark in ("x", " ")
    box = (f"<input type=\"checkbox\" name=\"use\" value=\"{n}\"{on} aria-label=\"Use "
           f"{_e(plain(item.key))}\">" if usable else "")
    was = with_unit(item.key, item.preset) if item.preset else "-"
    why = " ".join(item.reason.split()) or item.fits
    whose = item.source or item.used.line()
    note = ""
    if not standing.selected and standing.why:
        state = "Not selected" if usable else "Not used"
        note = f"<br><span class=\"amber small\">{_e(state)}: {_e('; '.join(standing.why))}.</span>"
    return (f"<li><label class=\"row\">{box}<span class=\"what\"><span class=\"part\">"
            f"{n}. {_e(plain(item.key))}</span> <span class=\"small\">{_e(was)} to "
            f"{_e(with_unit(item.key, item.value))}</span>"
            f"{'<br>' + _small(why) if why else ''}"
            f"{'<br>' + _small(str(whose)) if whose else ''}{note}</span></label></li>")


def _use_these(request: ReviewRequest) -> str:
    offering = request.offering
    if offering is None or not offering.items():
        return ""
    past = offering.past
    head = ""
    if past is not None:
        head = (f"<div class=\"card\"><strong>Learned from your {_e(past.stl_name)}, "
                f"{_e(past.on)}</strong><br>{_small(past.why)}<br>{_small(past.came_out)}")
        if len(offering.options) > 1:
            others = "".join(
                f"<li>{_e(n)}. {_e(o.stl_name)}, {_e(o.on)}: {_e(o.why)}</li>"
                for n, o in enumerate(offering.options, 1))
            head += (f"<details><summary>Pick a different print</summary><ul>{others}</ul>"
                     f"<p class=\"small\">Type <code>from N</code> below, as at the Mac.</p>"
                     "</details>")
        head += "</div>"
    rows = "".join(_item_row(n, item, request)
                   for n, item in enumerate(offering.items(), 1))
    pose = pose_value(request.orientation.rotation_deg)
    ticked = len(offering.ticked(request.proposal, pose))
    return (f"{head}<h2>Use these</h2><ul class=\"rows\">{rows}</ul>"
            f"<button type=\"submit\" name=\"do\" value=\"use\">Use these {ticked}</button>")


def _proposals(request: ReviewRequest) -> str:
    rows = []
    for n, flag in request.numbered:
        c = flag.change
        assert c is not None
        rows.append(
            f"<li><label class=\"row\"><input type=\"checkbox\" name=\"take\" value=\"{n}\">"
            f"<span class=\"what\"><span class=\"part\">{n}. {_e(plain(c.key))}</span> "
            f"{_small(f'{switched(c.key, c.preset) or chr(45)} to {switched(c.key, c.value)}')}"
            f"<br>{_small(c.reason)} {_small(f'[the slice: {c.knowledge}]')}"
            f"<br>{_small('The slice read: ' + flag.reading)}</span>"
            "</label></li>")
    for n, c in sorted(by_number(request.by_model).items()):
        rows.append(
            f"<li><label class=\"row\"><input type=\"checkbox\" name=\"take\" value=\"{n}\">"
            f"<span class=\"what\"><span class=\"part\">{n}. {_e(plain(c.key))}</span> "
            f"{_small(f'to {switched(c.key, c.value)}, the model')}<br>{_small(c.reason)}"
            f"</span></label><label class=\"small turn-down\">Turn it down: why?"
            f"<input type=\"text\" name=\"no{n}\" maxlength=\"300\"></label></li>")
    if not rows:
        return ""
    return ("<h2>Proposed, not applied unless you take it</h2>"
            f"<ul class=\"rows\">{''.join(rows)}</ul>"
            "<button type=\"submit\" name=\"do\" value=\"take\" class=\"second\">Take what's "
            "ticked, and turn down what has a why</button>")


def review_page(line: Line, asked: Asked, said: str = "", since: Moved | None = None) -> str:
    """The review: Use these first, then what's proposed, then every section the terminal's
    review has, in its order, with nothing dropped; and "Looks good, make it ready"."""
    request = asked.review
    assert request is not None
    job_id = line.job.job_id
    told = f"<div class=\"box\">{_e(said)}</div>" if said else ""
    if since:
        told += ("<div class=\"box\"><strong>Moved since your last answer</strong><ul>"
                 + "".join(f"<li>{_e(m)}</li>" for m in since.lines) + "</ul></div>")
    parts = review_sections(request, since, f"/job/{job_id}/slice")
    send_back = ""
    if request.candidates or request.parts:
        send_back = (f"<p class=\"small\">To send the pose back, at the Mac: <code>review "
                     f"{line.number}</code>, then <code>orientation</code>.</p>")
    return page(f"{line.job.part}: review", (
        f"{_head(line)}{told}"
        f"<form method=\"post\" action=\"/job/{_e(job_id)}/review\">"
        f"{_hidden('since', since_of(asked))}{_use_these(request)}{_proposals(request)}"
        f"<p><a href=\"/job/{_e(job_id)}/slice\">What the slice shows</a> · "
        f"<a href=\"/job/{_e(job_id)}/review/change\">Change other</a></p>"
        f"{parts}{send_back}"
        "<button type=\"submit\" name=\"do\" value=\"ok\">Looks good, make it ready</button>"
        "<p class=\"small\">It waits for Start then, which is still yours.</p>"
        "<button type=\"submit\" name=\"do\" value=\"stop\" class=\"second danger-text\">"
        "Stop this job</button></form>"))


def change_page(line: Line, asked: Asked, *, words: str, found: Sequence[Found],
                filament: str, key: str = "") -> str:
    """Change something: a setting found by plain words, as `find` finds it, then its value
    and why, as a correction typed at the review."""
    job_id = line.job.job_id
    back = f"/job/{job_id}/review"
    body = [f"<a class=\"back\" href=\"{_e(back)}\">Back to review</a>"
            "<h1>Change something</h1>"
            f"<form method=\"get\" action=\"/job/{_e(job_id)}/review/change\">"
            "<label for=\"words\">Say it in your words</label>"
            f"<input id=\"words\" type=\"search\" name=\"words\" value=\"{_e(words)}\" "
            "placeholder=\"bridge fan, wall speed, brim\"></form>"]
    if words and not found:
        body.append(f"<p class=\"small\">Nothing this filament or process has fits "
                    f"'{_e(words)}'. Try other words, such as bridge fan, bed or wall speed.</p>")
    if found:
        best = next((f for f in found if f.key == key), found[0])
        stands = (f"<br>{_small(f'{filament or chr(84) + 'his filament'} uses this one for {best.stands_in}.')}"  # noqa: E501
                  if best.stands_in else "")
        body.append(
            f"<form method=\"post\" action=\"/job/{_e(job_id)}/review\" class=\"card\">"
            f"{_hidden('since', since_of(asked))}{_hidden('do', 'change')}"
            f"{_hidden('key', best.key)}"
            f"<strong>{_e(best.plain)}</strong>{stands}<br>{_small(f'Orca calls it {best.key}.')}"
            f"<p><label>From {_e(with_unit(best.key, best.now))} to "
            f"<input type=\"text\" name=\"value\" required maxlength=\"60\" "
            f"inputmode=\"decimal\" class=\"value\"></label></p>"
            f"<label>Why? It's shown next time this is suggested."
            f"<input type=\"text\" name=\"reason\" maxlength=\"300\"></label>"
            "<p class=\"small\">After slicing you're told whether the new value is in the file, "
            "before it can be started.</p>"
            "<button type=\"submit\">Change and slice again</button></form>")
        others = [f for f in found if f is not best]
        if others:
            body.append("<ul class=\"rows\">" + "".join(
                _change_row(job_id, words, f) for f in others) + "</ul>")
    return page("Change something", "".join(body))


def _change_row(job_id: str, words: str, f: Found) -> str:
    href = (f"/job/{job_id}/review/change?words={_e(words)}&key={_e(f.key)}")
    return (f"<li><a class=\"row\" href=\"{href}\"><span class=\"what\">{_e(f.plain)}, now "
            f"{_e(with_unit(f.key, f.now))}</span></a></li>")


# --------------------------------------------------------------------------
# A hand-sliced file's look, and the mesh gate
# --------------------------------------------------------------------------


def look_page(line: Line, asked: Asked) -> str:
    """What a sent, hand-sliced file prints, all of it as `look` shows it, then OK or Reject."""
    job_id = line.job.job_id
    return page(f"{line.job.part}: look", (
        f"{_head(line)}<pre class=\"said\">{_e(chr(10).join(asked.shown))}</pre>"
        f"<form method=\"post\" action=\"/job/{_e(job_id)}/look\">"
        f"{_hidden('since', since_of(asked))}"
        "<div class=\"buttons\"><button type=\"submit\" name=\"ok\" value=\"yes\">OK, it's ready"
        "</button><button type=\"submit\" name=\"ok\" value=\"no\" class=\"danger\">Reject: "
        "take it out</button></div></form>"
        "<p class=\"small\">OK lets it be ready, offered in its turn: Start is still yours. "
        "Reject takes it out of the queue, and nothing prints.</p>"))


def mesh_page(line: Line, asked: Asked) -> str:
    """The mesh gate: why it stopped, then Approve or Reject, as `mesh` asks."""
    gate = asked.gate or {}
    job_id = line.job.job_id
    return page(f"{line.job.part}: mesh", (
        f"{_head(line)}<dl><dt>Because</dt><dd>{_e(gate.get('reason') or 'it gave no reason')}"
        f"</dd><dt>Manifold</dt><dd>{_e(gate.get('is_manifold'))}</dd></dl>"
        f"<form method=\"post\" action=\"/job/{_e(job_id)}/mesh\">"
        f"{_hidden('since', since_of(asked))}"
        "<div class=\"buttons\"><button type=\"submit\" name=\"approve\" value=\"yes\">Approve"
        "</button><button type=\"submit\" name=\"approve\" value=\"no\" class=\"danger\">"
        "Reject</button></div></form>"
        "<p class=\"small\">Approve goes on to slice it as the file lays it: the questions and "
        "the review follow. Reject ends the job: nothing is sliced or printed.</p>"))
