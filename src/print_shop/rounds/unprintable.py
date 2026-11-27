"""A part file whose last `thin` failed is named in every later `bench` and `run` reply, until it
passes or leaves the part (mqucifer/print-shop#37, `suncatcher-2`'s lesson 21).

On `suncatcher-2` one pane failed `thin` nine times in a row, and the run moved on to the frame and
the other panes without it ever passing. A file that can't be printed is a failure of the part,
whatever the other files do. It isn't a refusal of anything: `ask` stays open, and its question
says which files fail.

**What counts.** Each `thin` on one file, the model's own or the harness's at `done` and `ask`,
at the settings the harness reads every file at: the nozzle's width and the bench's other
defaults. `--one-body` is a stricter check, so it counts. A `thin` with a wider nozzle or a looser
setting is a different check, and neither fails nor clears a file. `FAIL` fails it; `OK` clears it;
no answer to rely on (`DISAGREE`, `NO ANSWER`) leaves it as it was, as the harness takes it at a
stop as neither.

**Leaving the part.** A file that's gone from the directory has left it, and so has one a `done`
after its failure didn't name in `part`.

**Never a given file** (mqucifer/print-shop#365). A file under `references/` is given, and isn't
printed: `done` refuses one in `part`. An assembly run's placed copy of an approved part, or a
stand-in for a load, failing `thin` was named as a part file at every reply and at the stop, though
the approved part passed `thin` in its own run. A `thin` on a given file, at any settings, is never
held, and the reply to it says why.
"""

from __future__ import annotations

from dataclasses import dataclass

from print_shop.rounds import nudge

#: `thin`'s settings, as the harness reads every file to be printed (`bench/cli.py`'s defaults).
#: A call that gives another value for one of these is another check.
DEFAULTS = {
    "--nozzle": 0.4,
    "--layer": 0.2,
    "--min-body": 1.0,
    "--samples": 4000.0,
    "--max-layers": 400.0,
    "--wall-area": 2.0,
}
#: How much of what the bench said each line keeps.
WHY_CHARS = 160
#: Where the given files are: `references/<name>`.
GIVEN = "references/"
#: Why a given file isn't read for printing, nor its `thin` held: said in the reply to a `thin`
#: on one, and at an ask that draws one.
NOT_HELD_ON_GIVEN = (
    "given, not printed, so it isn't read or held for printing: only the files `done` delivers to "
    "print are, by `thin`, the side on the bed and the downward faces. An approved part given to "
    "place was read in its own run, and a stand-in isn't printed."
)


@dataclass(frozen=True)
class Thin:
    """One `thin` reading of one file: by the model's `bench`, or the harness at a stop."""

    at_call: int
    file: str
    verdict: str
    why: str = ""
    by: str = "bench"


def counted(argv: list[str]) -> str | None:
    """The file a `thin` call reads, if it's at the harness's settings: else None."""
    words = argv[1:] if argv[:1] == ["bench"] else argv
    if not words or words[0] != "thin":
        return None
    given = nudge.options(words)
    for name, values in given.items():
        if name == "--one-body":
            continue
        try:
            if name not in DEFAULTS or [float(v) for v in values] != [DEFAULTS[name]]:
                return None
        except ValueError:
            return None
    plain = _files(words)
    return plain[0] if len(plain) == 1 else None


def _files(words: list[str]) -> list[str]:
    """The files a `thin` call names, its options and their values left out."""
    plain: list[str] = []
    value = False
    for word in words[1:]:
        if value:
            value = False
        elif word.startswith("--"):
            value = "=" not in word and word not in nudge.FLAGS
        else:
            plain.append(word)
    return plain


def given(name: str) -> bool:
    """A given file, `references/<name>`: never printed, so never held to `thin`."""
    return name.removeprefix("/work/").removeprefix("./").startswith(GIVEN)


def on_given(argv: list[str]) -> list[str]:
    """The files a `thin` call reads, at any settings, where every one is given: else none."""
    words = argv[1:] if argv[:1] == ["bench"] else argv
    if not words or words[0] != "thin":
        return []
    plain = _files(words)
    return plain if plain and all(given(name) for name in plain) else []


def given_said(files: list[str]) -> str:
    """Why given files aren't read or held for printing, each named."""
    if not files:
        return ""
    named = ", ".join(f"`{name}`" for name in files)
    return f"{named}: {NOT_HELD_ON_GIVEN}"


def failing(
    readings: list[Thin], present: set[str], part: list[str] | None, part_at: int | None
) -> list[Thin]:
    """Each file whose last `thin` that answered failed, and that hasn't left the part, by the
    call it failed at. `readings` is oldest first."""
    last: dict[str, Thin] = {}
    for one in readings:
        if one.verdict in ("FAIL", "OK"):
            last[one.file] = one
    found = []
    for name, one in last.items():
        if one.verdict != "FAIL" or name not in present or given(name):
            continue
        if part is not None and part_at is not None and part_at > one.at_call and name not in part:
            continue
        found.append(one)
    return sorted(found, key=lambda t: t.at_call)


def said(found: list[Thin]) -> str:
    """The lines a reply gains: each file, its call and what `thin` said, and what to do."""
    if not found:
        return ""
    lines = ["Files of the part whose last `thin` failed:"]
    for one in found:
        why = " ".join(one.why.split())
        why = why if len(why) <= WHY_CHARS else f"{why[:WHY_CHARS]}…"
        who = "" if one.by == "bench" else f", by the harness at `{one.by}`"
        lines.append(f"- `{one.file}`, call {one.at_call}{who}: FAIL" + (f": {why}" if why else ""))
    lines.append(
        "A file that fails `thin` can't be printed, whatever the other files do. Fix it and run "
        "`thin` on it again before you go on to another file, or take it out of the part. If you "
        "can't, `ask`: its question says which files fail."
    )
    return "\n".join(lines)
