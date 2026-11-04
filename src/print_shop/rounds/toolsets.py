"""Toolsets: the core every run has, and the checks, bench commands and skills of one kind of
object (mqucifer/print-shop#37).

The Sponsor, 2026-11-03: "not everything is going to be a stained glass print". So a check for
one kind of object comes in that kind's toolset, and the core stays general. The request's
`## Kind` names the kind, and a run with none has the core only. A run from before toolsets has
every command, as it had then, and its needs are read as they were.

**What a need measures.** Each bench need says what it's about, in `check.measures`, from its
toolset's short list, and the harness checks that against its command. In all three stained-glass
runs the hanging requirement was settled on a stand-in, the frame's bounds by `info` or the
whole part's `thin`, where `wall` measures the wall round the hole. A need's words are a weak
signal of what it's about: "wall" is in "the panes sit between the frame's walls" as well as in
"2 mm of wall round the hanging hole", and a check a word list refuses wrongly stops a run. A
short list the need declares is exact: a refusal names the command that measures it, and the
person at each stop reads what each need said it measures beside its quote.

**A limit is the request's.** A measure marked `limited` has no default anywhere: its need gives a
limit, and the figure is in the need's quote, from the request, an answer or a note.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from print_shop.rounds import nudge

if TYPE_CHECKING:
    from print_shop.rounds.needs import Need


@dataclass(frozen=True)
class Measure:
    """One thing a need can be about, and the commands that measure it."""

    name: str
    about: str
    commands: tuple[str, ...]
    #: The command as a need gives it.
    usage: str
    #: An option the command must be given to measure this, such as `--one-body`.
    flag: str = ""
    #: Options that give its limit. A `limited` measure takes one of these, or a need's `number`
    #: with its limit, and the figure is in the need's quote.
    limits: tuple[str, ...] = ()
    limited: bool = False


@dataclass(frozen=True)
class Toolset:
    """What a run has: its bench commands, what its needs may measure, and its skills."""

    kind: str | None
    title: str
    commands: tuple[str, ...]
    measures: tuple[Measure, ...] = ()
    skills: tuple[str, ...] = ()
    #: From before toolsets: every command, and needs read as they were.
    legacy: bool = False
    added: tuple[str, ...] = field(default=())

    def measure(self, name: str) -> Measure | None:
        return next((m for m in self.measures if m.name == name), None)

    def said(self) -> str:
        """The line the start gives: which toolset, and what it adds to the core."""
        if self.kind is None:
            return (
                "This run's toolset is the core: the bench commands "
                f"{_listed(self.commands)}. A request's `## Kind` adds a kind's own."
            )
        return (
            f"This run's toolset is {self.title}: the core's bench commands and "
            f"{_listed(self.added)}, and the `{self.skills[-1]}` skill, which says how to use "
            "them for this kind."
        )


def _listed(names: tuple[str, ...]) -> str:
    return ", ".join(f"`{n}`" for n in names)


CORE_COMMANDS = (
    "info",
    "clearance",
    "readback",
    "rays",
    "thin",
    "wall",
    "pause",
    "params",
    "render",
)
CORE_SKILLS = ("bench", "cadquery", "references")
CORE_MEASURES = (
    Measure(
        "size",
        "the part's outer size, volume, bodies or flat heights, as a whole",
        ("info",),
        "info FILE",
    ),
    Measure(
        "fit",
        "the gap or overlap between parts, or between a file's solids",
        ("clearance",),
        "clearance A.step B.step --min-gap G",
    ),
    Measure(
        "cut",
        "a cut or a join changed the body by what it should",
        ("readback",),
        "readback BEFORE.step AFTER.step --expect-delta V",
    ),
    Measure(
        "hole",
        "a cut feature's size, depth or floor, read from its axis",
        ("rays",),
        "rays FILE --centre X,Y,Z --d D",
    ),
    Measure(
        "wall",
        "the wall round one hole, such as the hole a piece hangs from",
        ("wall",),
        "wall FILE --hole X,Y,Z --min-wall W",
    ),
    Measure(
        "printable",
        "nothing narrower than the nozzle, no floor under a layer, no tiny piece",
        ("thin",),
        "thin FILE",
    ),
    Measure(
        "one-piece",
        "the part is one connected solid",
        ("thin",),
        "thin FILE --one-body",
        flag="--one-body",
    ),
    Measure(
        "insert",
        "the layer to pause after to drop an insert in, and the room over it",
        ("pause",),
        "pause FILE --pocket-z Z --pane-thickness T --min-room R",
    ),
    Measure(
        "parameter",
        "a top-level number moves every piece it should",
        ("params",),
        "params SCRIPT --set NAME=VALUE",
    ),
)
CORE = Toolset(None, "the core", CORE_COMMANDS, CORE_MEASURES, CORE_SKILLS)

#: `capture` and `panes` read a pane in its pocket, held by lips: only stained glass has those.
#: `light` and `thickness` read what a person sees through a pane. `pause` stays in the core: an
#: insert dropped in mid-print, a nut or a magnet, is any kind's.
STAINED_GLASS_COMMANDS = ("capture", "panes", "light", "thickness")
STAINED_GLASS = Toolset(
    "stained-glass",
    "stained glass",
    CORE_COMMANDS + STAINED_GLASS_COMMANDS,
    CORE_MEASURES
    + (
        Measure(
            "pane-hold",
            "each pane held by its lips on both faces, wherever its play lets it sit",
            ("capture",),
            "capture FRAME --pocket-z Z --clearance C --pane-thickness T --min-lip M --panes N",
        ),
        Measure(
            "pane-fit",
            "a pane from each opening, and how it fits its pocket",
            ("panes",),
            "panes FRAME --pocket-z Z --clearance C",
        ),
        Measure(
            "light",
            "the share of each pane open straight through the frame, as light passes",
            ("light",),
            "light FRAME PANE... --min-open P",
            limits=("--min-open",),
            limited=True,
        ),
        Measure(
            "pane-thickness",
            "how thick each pane is, straight through",
            ("thickness",),
            "thickness PANE... --max T",
            limits=("--max", "--min"),
            limited=True,
        ),
    ),
    CORE_SKILLS + ("designing-stained-glass",),
    added=STAINED_GLASS_COMMANDS,
)
KINDS = {STAINED_GLASS.kind: STAINED_GLASS}
#: A run from before toolsets: every command it had then, its skills, and needs read as they were.
EVERY = Toolset(
    None,
    "every command, from before toolsets",
    CORE_COMMANDS + ("capture", "panes"),
    skills=CORE_SKILLS,
    legacy=True,
)


class UnknownKind(ValueError):
    """A kind no toolset has."""


def key(kind: str) -> str:
    """A kind as a request may write it, `Stained glass`, as its toolset's key."""
    return re.sub(r"[\s_]+", "-", kind.strip().casefold())


def named(kind: str | None) -> Toolset:
    """The toolset for a kind, or the core for none."""
    if kind is None or not kind.strip():
        return CORE
    found = KINDS.get(key(kind))
    if found is None:
        known = ", ".join(f"`{k}`" for k in KINDS)
        raise UnknownKind(f"there's no toolset for the kind {kind!r}: the kinds are {known}")
    return found


def of_run(kind: str | None, toolsets: bool) -> Toolset:
    """A run's toolset: `toolsets` is false for a run from before them."""
    return named(kind) if toolsets else EVERY


def where(command: str) -> Toolset | None:
    """The kind's toolset a command comes in, if it isn't the core's."""
    return next((t for t in KINDS.values() if command in t.added), None)


def _listing(toolset: Toolset) -> str:
    return "; ".join(f"`{m.name}`, {m.about}" for m in toolset.measures)


def _figure_in(value: float, quote: str) -> bool:
    shown = f"{value:g}"
    return re.search(rf"(?<![\d.]){re.escape(shown)}(?![\d])", quote) is not None


def wrong_with(need: Need, toolset: Toolset) -> list[str]:
    """What's wrong with a bench need's check in this toolset: its command, what it says it
    measures, and its limit. Nothing, for a run from before toolsets."""
    if toolset.legacy or need.by != "bench":
        return []
    shown = f"the need {need.id!r}"
    words = need.argv()
    command = words[0] if words else ""
    if command and command not in toolset.commands:
        home = where(command)
        if home is not None:
            return [
                f"{shown}: `{command}` is in the {home.title} toolset, and this run's is "
                f"{toolset.title}: a request's `## Kind` loads it"
            ]
        return [f"{shown}: `{command}` isn't a bench command of this run's"]
    if not need.measures:
        return [
            f"{shown}: `check.measures` says what this need is about, one of: {_listing(toolset)}"
        ]
    measure = toolset.measure(need.measures)
    if measure is None:
        other = next((t for t in KINDS.values() if t.measure(need.measures)), None)
        home = f": it's in the {other.title} toolset" if other is not None else ""
        return [
            f"{shown}: `{need.measures}` isn't a measure of this run's toolset{home}. Its "
            f"measures are: {_listing(toolset)}"
        ]
    if command not in measure.commands:
        return [
            f"{shown} is about `{measure.name}`, {measure.about}, and `{command}` doesn't measure "
            f"that: `{measure.commands[0]}` does, as `{measure.usage}`"
        ]
    given = nudge.options(words)
    if measure.flag and measure.flag not in given:
        return [f"{shown}: `{measure.name}` is read by `{command}` with `{measure.flag}`"]
    if measure.limited:
        return _limit_wrong(need, measure, given, shown)
    return []


def _limit_wrong(need: Need, measure: Measure, given: nudge.Options, shown: str) -> list[str]:
    """A limit from the request, never a default: given, and its figure in the need's quote."""
    figures: list[float] = []
    for option in measure.limits:
        for value in given.get(option, ()):
            try:
                figures.append(float(value))
            except ValueError:
                continue
    if need.number:
        figures += [v for v in (need.at_least, need.at_most) if v is not None]
    ways = " or ".join(f"`{o}`" for o in measure.limits)
    if not figures:
        return [
            f"{shown}: a `{measure.name}` need gives its limit, {ways}, or a `number` with its "
            "limit. There's no default: the limit is the request's, so ask for it with "
            "`questions` if you weren't given one"
        ]
    if need.source not in ("request", "answer", "note"):
        return [
            f"{shown}: a `{measure.name}` limit is the request's, so its `source` is `request`, "
            f"`answer` or `note`, with the words that give it in `quote`, not `{need.source}`"
        ]
    missing = [f for f in figures if not _figure_in(f, need.quote)]
    if missing:
        named = ", ".join(f"{f:g}" for f in missing)
        return [
            f"{shown}: its limit {named} isn't in its quote. A `{measure.name}` limit is the "
            "request's: quote the words that give it, or ask for it with `questions`"
        ]
    return []
