"""The phone's pages: Start, Away, signed out, pairing, and what came of a Start
(mqucifer/print-shop#214). The screens and words are print-shop-ux's.

Each is one HTML string, every value escaped, with no script at all: the bed's tick box greys
the button by CSS alone, and the server refuses a Start whose box wasn't ticked, whatever the
page did. No page shows a setting, a token, or any address but its own.
"""

from __future__ import annotations

from html import escape

from print_shop.orchestrator.start import Checked

__all__ = [
    "away_page", "message_page", "nothing_ready_page", "page", "pair_page", "signed_out_page",
    "start_page",
]

_STYLE = """
:root { color-scheme: light dark; --fg: #1b1b1b; --bg: #fafafa; --muted: #666; --line: #ddd;
  --bad: #b3261e; --button: #1d4ed8; --button-fg: #fff; --box: #f1f1f1; --amber: #8a5200;
  --dark: #1f2329; --dark-fg: #f5f5f5; --dark-muted: #b9bec6; --dark-amber: #ffc46b;
  --bad-button: #b3261e; }
@media (prefers-color-scheme: dark) { :root { --fg: #eee; --bg: #121212; --muted: #aaa;
  --line: #333; --bad: #f2b8b5; --button: #3b82f6; --box: #1e1e1e; --amber: #ffc46b;
  --dark: #2b3038; --bad-button: #c2413a; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; font: 17px/1.4 -apple-system, system-ui, sans-serif;
  color: var(--fg); background: var(--bg); max-width: 560px; }
h1 { font-size: 1.4em; margin: 0.2em 0 0.8em; overflow-wrap: anywhere; }
dl { display: grid; grid-template-columns: max-content 1fr; gap: 0.5em 1em; margin: 0 0 1.2em; }
dt { color: var(--muted); }
dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
dd.bad { color: var(--bad); text-align: left; grid-column: 1 / -1; }
.box { background: var(--box); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 14px; margin: 0 0 1.2em; }
.box strong { display: block; margin-bottom: 0.3em; }
.small { color: var(--muted); font-size: 0.85em; }
label.bed { display: flex; gap: 0.6em; align-items: center; margin: 0 0 1em; font-size: 1.05em; }
label.bed input { width: 1.4em; height: 1.4em; }
button { width: 100%; padding: 14px; font-size: 1.05em; border: 0; border-radius: 10px;
  background: var(--button); color: var(--button-fg); }
button[disabled], form:has(input[name=bed]:not(:checked)) button { opacity: 0.4; }
form:has(input[name=bed]:not(:checked)) .ready,
form:has(input[name=bed]:checked) .unready { display: none; }
input[type=text] { width: 100%; padding: 12px; font-size: 1.2em; letter-spacing: 0.15em;
  text-transform: uppercase; margin: 0 0 1em; }
a { color: var(--button); }
h2 { font-size: 1.05em; margin: 1.4em 0 0.5em; color: var(--muted); font-weight: 600; }
.now { background: var(--dark); color: var(--dark-fg); border-radius: 12px; padding: 14px;
  margin: 0 0 1em; }
.now .small { color: var(--dark-muted); }
.amber { color: var(--amber); }
.now .amber { color: var(--dark-amber); }
.good { color: var(--button); }
ul.rows { list-style: none; margin: 0 0 1em; padding: 0; }
ul.rows li { border-bottom: 1px solid var(--line); }
ul.rows a.row, ul.rows div.row { display: flex; gap: 0.8em; align-items: center;
  padding: 10px 0; color: inherit; text-decoration: none; }
.row .what { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.row .act { color: var(--button); font-weight: 600; white-space: nowrap; }
.row .tag { font-size: 0.8em; border: 1px solid var(--line); border-radius: 6px;
  padding: 1px 6px; white-space: nowrap; }
.part { font-weight: 600; }
q { font-style: italic; }
.foot { display: flex; gap: 10px; margin: 1.4em 0 0; }
.foot a, a.button, button.second { flex: 1; display: block; text-align: center; padding: 12px;
  border: 1px solid var(--line); border-radius: 10px; text-decoration: none;
  color: var(--button); background: var(--box); font-size: 1em; }
form.inline { display: inline; }
.buttons { display: flex; flex-wrap: wrap; gap: 8px; margin: 0.6em 0; }
.buttons form { flex: 1 1 40%; }
.buttons button { padding: 11px; }
button.danger { background: var(--bad-button); }
.back { display: inline-block; margin: 0 0 0.6em; }
fieldset { border: 1px solid var(--line); border-radius: 10px; margin: 0 0 1em;
  padding: 8px 12px; }
fieldset label { display: block; padding: 6px 0; }
textarea { width: 100%; min-height: 4em; font: inherit; padding: 8px; }
input[type=search] { width: 100%; padding: 12px; font-size: 1em; margin: 0 0 1em; }
.card { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px;
  margin: 0 0 0.8em; background: var(--box); }
.card .head { display: flex; gap: 0.8em; justify-content: space-between; }
.card .head .part { min-width: 0; overflow-wrap: anywhere; }
.card .head .outcome { text-align: right; }
.bars { display: flex; align-items: flex-end; height: 96px; margin: 0.4em 0 0.2em;
  border-bottom: 1px solid var(--line); }
.bars a { flex: 1 1 0; min-width: 0; background: var(--button); opacity: 0.75; }
.bars a.on { background: var(--amber); opacity: 1; }
.bars .gap { flex: 0 0 8px; }
.ends { display: flex; justify-content: space-between; gap: 0.6em; }
.ends .mid { text-align: center; }
.feat { margin: 0 0 0.7em; }
.feat .head { display: flex; justify-content: space-between; gap: 0.6em; }
.feat .bar { height: 8px; background: var(--button); border-radius: 4px; margin-top: 3px; }
.layer { display: flex; gap: 8px; align-items: center; margin: 0.6em 0; }
.layer input { width: 5em; padding: 8px; font-size: 1em; }
.layer button { width: auto; padding: 8px 14px; }
"""


def page(title: str, body: str) -> str:
    """One page, with the phone's own CSS, and nothing to load."""
    return _page(title, body)


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
            f"<body>{body}</body></html>")


def _rows(checks: list[Checked]) -> str:
    out = []
    for row in checks:
        out.append(f"<dt>{escape(row.label)}</dt>")
        out.append(f"<dd>{escape(row.value)}</dd>" if row.ok else
                   f"<dd class=\"bad\">{escape(row.value)}</dd>")
    return f"<dl>{''.join(out)}</dl>"


def start_page(*, part: str, checks: list[Checked], confirm: str, held: str = "") -> str:
    """The Start screen: the job, what was checked, the bed's tick box and the named Start."""
    name = escape(part)
    held_said = f"<div class=\"box\">{escape(held)}</div>" if held else ""
    return _page(f"Start {part}", (
        f"<h1>{name}</h1>{_rows(checks)}{held_said}"
        "<form method=\"post\" action=\"/start\">"
        f"<input type=\"hidden\" name=\"confirm\" value=\"{escape(confirm)}\">"
        "<label class=\"bed\"><input type=\"checkbox\" name=\"bed\" value=\"clear\" required>"
        " I can see the bed is clear</label>"
        "<p class=\"small\">Signed in, on home Wi-Fi. Start arms the printer for this one print "
        "and then disarms.</p>"
        f"<button type=\"submit\"><span class=\"unready\">Check the bed to start</span>"
        f"<span class=\"ready\">Start {name}</span></button>"
        "</form>"))


def away_page(*, part: str | None, why: str) -> str:
    """The Away screen: Start refused, in words, and the job waits."""
    heading = f"<h1>{escape(part)}</h1>" if part else ""
    return _page("You're away from home", (
        f"{heading}<div class=\"box\"><strong>You're away from home</strong>"
        "Printing starts only on your home Wi-Fi, where you can see the bed. This job will wait, "
        f"however long that is.</div><p class=\"small\">Start is refused: {escape(why)}.</p>"
        "<button type=\"button\" disabled>Start when you're home</button>"))


def signed_out_page() -> str:
    return _page("Sign in", (
        "<div class=\"box\"><strong>Not signed in</strong>Sign in on this phone at the Mac "
        "(<code>phone.py pair</code>) to start a print. The job waits.</div>"
        "<p><a href=\"/pair\">I have a code from the Mac</a></p>"))


def nothing_ready_page(name: str, last: str = "") -> str:
    """Signed in, with nothing at the gate: and how the last Start from a phone went, if any."""
    went = f"<div class=\"box\">{escape(last)}</div>" if last else ""
    return _page("Nothing to start", (
        f"<p class=\"small\">Signed in as {escape(name)}.</p>{went}"
        "<div class=\"box\"><strong>Nothing is ready to start</strong>A job ready to print shows "
        "here. Reload when one is.</div>"))


def pair_page(said: str = "") -> str:
    """Where the phone types the code `phone.py pair` showed at the Mac."""
    told = f"<div class=\"box\">{escape(said)}</div>" if said else ""
    return _page("Sign in this phone", (
        f"<h1>Sign in this phone</h1>{told}"
        "<form method=\"post\" action=\"/pair\">"
        "<label for=\"code\">The code the Mac shows</label>"
        "<input id=\"code\" type=\"text\" name=\"code\" autocomplete=\"one-time-code\" "
        "autocapitalize=\"characters\" spellcheck=\"false\" maxlength=\"20\" required>"
        "<button type=\"submit\">Sign in</button></form>"))


def message_page(title: str, said: str, *, again: bool = True, back_to: str = "/start",
                 back_words: str = "Back to Start") -> str:
    """What came of a Start, a pairing or a button, in words, with the way back: to the Start
    page unless it says another."""
    back = (f"<p><a href=\"{escape(back_to)}\">{escape(back_words)}</a></p>" if again
            else "")
    return _page(title, f"<div class=\"box\"><strong>{escape(title)}</strong>"
                        f"{escape(said)}</div>{back}")
