"""The request's set form, and what it holds a run's needs and stops to (mqucifer/print-shop#36,
mqucifer/print-shop#38).

A request is free text, with any of four headings of its own, a line each under them:

    ## Requirements       what the part must do. Each line is covered by a need that names it.
    ## Non-negotiables    what mustn't change. Each line is covered by a need marked `fixed`,
                          quoted from the line.
    ## Kind               one word for the kind of object, such as `stained-glass`. Its toolset is
                          mqucifer/print-shop#37's.
    ## Stops              what's shown to a person before the run goes on, in order, such as
                          "the 2D layout". Each is held until its `ask` has a pick.

Each line has an id the model names it by: `R1`, `R2` for requirements, `N1` for non-negotiables,
and `S1`, `S2` for stops. **The plan is the first stop,** `plan`, unless `## Stops` has the line
`no plan`. Anything else in the request, under any other heading or none, is read as it always was.
A request in the old free form has no lines, so nothing is checked for coverage, and still has the
plan as its stop.

Coverage is held by ids, as `needs` holds quotes: a need names the lines it covers, and nothing is
guessed from words. The only reading of a line's words is whether it holds a figure (a digit), so a
non-negotiable's need says how its figure is read.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from print_shop.rounds.needs import Need, normal

#: The headings, by their letters alone, and the section each starts.
HEADINGS = {
    "requirements": "requirement",
    "nonnegotiables": "non_negotiable",
    "kind": "kind",
    "stops": "stops",
}
PREFIX = {"requirement": "R", "non_negotiable": "N", "stops": "S"}
PLAN = "plan"
#: What the plan stop shows, as the model and the person are told it.
PLAN_WHAT = "the plan: your breakdown, the needs, and how you'll go about it"
#: The line under `## Stops` that leaves the plan out.
NO_PLAN = "no plan"
KIND = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
ITEM = re.compile(r"^(?:[-*+]|\d{1,3}[.)])\s+")
FIGURE = re.compile(r"\d")


class NotAForm(ValueError):
    """What's wrong with the request's set form, in plain words."""


@dataclass
class Line:
    id: str
    section: str
    text: str


@dataclass
class Form:
    """The request's lines, its kind, and its stops, the plan first unless it says otherwise."""

    requirements: list[Line] = field(default_factory=list)
    non_negotiables: list[Line] = field(default_factory=list)
    kind: str | None = None
    stops: list[Line] = field(default_factory=list)

    def lines(self) -> list[Line]:
        return [*self.requirements, *self.non_negotiables]

    def line(self, line_id: str) -> Line | None:
        return next((one for one in self.lines() if one.id == line_id), None)

    def stop(self, stop_id: str) -> Line | None:
        return next((one for one in self.stops if one.id == stop_id), None)

    def plain(self) -> dict[str, Any]:
        return asdict(self)


def load(found: dict[str, Any] | None) -> Form | None:
    """A run's form from its state. None for a run started before the form, which carries on as it
    began: no lines, no stops."""
    if found is None:
        return None
    return Form(
        [Line(**one) for one in found.get("requirements") or []],
        [Line(**one) for one in found.get("non_negotiables") or []],
        found.get("kind"),
        [Line(**one) for one in found.get("stops") or []],
    )


def _sections(text: str) -> dict[str, list[str]]:
    """Each set heading's lines. A line that goes on indented, and isn't an item, is joined to the
    line above it. A heading of the same level or above ends a section."""
    found: dict[str, list[str]] = {}
    section: str | None = None
    level = 0
    for raw in text.splitlines():
        heading = HEADING.match(raw)
        if heading:
            depth = len(heading.group(1))
            name = re.sub(r"[^a-z]", "", heading.group(2).casefold())
            if depth == 2 and name in HEADINGS:
                section = HEADINGS[name]
                level = depth
                if section in found:
                    raise NotAForm(f"the request has `## {heading.group(2)}` twice")
                found[section] = []
                continue
            if section is not None and depth <= level:
                section = None
            continue
        if section is None or not raw.strip():
            continue
        lines = found[section]
        item = ITEM.match(raw.strip())
        if raw[:1].isspace() and lines and not item:
            lines[-1] = f"{lines[-1]} {raw.strip()}"
            continue
        lines.append(ITEM.sub("", raw.strip(), count=1).strip())
    return found


def read(text: str) -> Form:
    """The request's form, or `NotAForm` saying what's wrong. With none of the headings, it's the
    old free form: no lines, and the plan as its stop."""
    sections = _sections(text)
    form = Form()
    for section, given in sections.items():
        if not given:
            raise NotAForm(
                f"the request's `## {_heading(section)}` has no lines: a line each, or leave the "
                "heading out"
            )
    for section in ("requirement", "non_negotiable"):
        lines = [
            Line(f"{PREFIX[section]}{n}", section, one)
            for n, one in enumerate(sections.get(section, []), 1)
        ]
        setattr(form, "requirements" if section == "requirement" else "non_negotiables", lines)
    kind = sections.get("kind")
    if kind is not None:
        if len(kind) != 1 or not KIND.fullmatch(kind[0].strip("`").strip()):
            raise NotAForm(
                "the request's `## Kind` is one line, one word of lowercase letters, digits and "
                f"`-`, such as `stained-glass`: not {' / '.join(kind)!r}"
            )
        form.kind = kind[0].strip("`").strip()
    stops = sections.get("stops", [])
    plan = not any(normal(one) == NO_PLAN for one in stops)
    named = [one for one in stops if normal(one) != NO_PLAN]
    form.stops = [Line(PLAN, "stop", PLAN_WHAT)] if plan else []
    form.stops += [Line(f"S{n}", "stop", one) for n, one in enumerate(named, 1)]
    return form


def _heading(section: str) -> str:
    return {
        "requirement": "Requirements",
        "non_negotiable": "Non-negotiables",
        "kind": "Kind",
        "stops": "Stops",
    }[section]


def said(form: Form) -> str:
    """What the model's first message says of the form: each line by its id, and the stops."""
    parts: list[str] = []
    if form.requirements or form.non_negotiables:
        parts.append(
            "The request's lines, by the ids `needs` names them by in `covers`. Each requirement "
            "is covered by at least one need, and each non-negotiable by a need marked `fixed`, "
            "quoting its line:"
        )
        parts += [f"- `{one.id}`: {one.text}" for one in form.lines()]
    else:
        parts.append(
            "The request has no `## Requirements` or `## Non-negotiables`, so no line is held to "
            "a need."
        )
    if form.kind:
        parts.append(f"Its kind: `{form.kind}`.")
    if form.stops:
        parts.append(
            "Its stops, in order. Show each with `ask`, naming it in `stop`. Until a stop's ask "
            "has a pick, `done` and the next stop's ask wait: writing, running and the bench stay "
            "open throughout."
        )
        parts += [f"- `{one.id}`: {one.text}" for one in form.stops]
    else:
        parts.append("It names no stops.")
    return "\n".join(parts)


def held(found: list[Need], form: Form) -> list[str]:
    """What's wrong with the needs against the request's lines: each need's `covers`, `fixed` and
    `figure`, and every line covered. It names the lines left over, and reads nothing into words."""
    wrong: list[str] = []
    ids = [one.id for one in form.lines()]
    shown = ", ".join(ids) or "none: the request has no `## Requirements` or `## Non-negotiables`"
    for need in found:
        name = f"the need {need.id!r}"
        for line_id in need.covers:
            if form.line(line_id) is None:
                wrong.append(
                    f"{name} covers `{line_id}`, which isn't a line of the request: {shown}"
                )
        covered = [line for line_id in need.covers if (line := form.line(line_id)) is not None]
        theirs = [line for line in covered if line.section == "non_negotiable"]
        if theirs and not need.fixed:
            named = ", ".join(f"`{line.id}`" for line in theirs)
            wrong.append(
                f"{name} covers {named}, a non-negotiable: a need that covers one is marked "
                "`fixed`, and quotes its line"
            )
        if need.fixed:
            wrong += _fixed(need, theirs, name)
        if need.by == "eye" and covered and not need.no_bench:
            wrong.append(
                f"{name} covers a line of the request by the Sponsor's eye: a line is covered by "
                "an eye check only where no bench command measures it. Say why none does in "
                "`check.no_bench`, or check it on the bench. A bought part's hold is always "
                "the bench's: `clearance <part> <bought part> --slide <its way out>`, with `HELD`"
            )
    for line in form.requirements:
        if not any(line.id in need.covers for need in found):
            wrong.append(
                f"`{line.id}` isn't covered: \"{line.text}\". Name it in a need's `covers`"
            )
    for line in form.non_negotiables:
        if not any(line.id in need.covers and need.fixed for need in found):
            wrong.append(
                f"`{line.id}` isn't covered: \"{line.text}\". It's a non-negotiable: a need marked "
                "`fixed` names it in `covers` and quotes it"
            )
    return wrong


def _fixed(need: Need, theirs: list[Line], name: str) -> list[str]:
    if not theirs:
        return [
            f"{name} is `fixed`, which is for the request's non-negotiables: it covers one in "
            "`covers`"
        ]
    wrong: list[str] = []
    if need.source == "request" and not any(
        normal(need.quote) in normal(line.text) for line in theirs
    ):
        named = ", ".join(f"`{line.id}`" for line in theirs)
        wrong.append(f'{name} is fixed, and its quote, "{need.quote}", isn\'t from {named}')
    elif need.source not in ("request", "note"):
        wrong.append(
            f"{name} is fixed: its `source` is `request`, quoting its line, or `note`, quoting "
            "the Sponsor's note that changed it"
        )
    figures = [line for line in theirs if FIGURE.search(line.text)]
    if figures and not need.figure.strip():
        line = figures[0]
        wrong.append(
            f'{name} covers `{line.id}`, which holds a figure: "{line.text}". Say in `figure` how '
            "it's read: what's measured, and where something changes it, such as before or after "
            "play, which. If the request doesn't say, ask with `questions` before settling"
        )
    return wrong
