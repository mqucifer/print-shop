"""A bench check that failed on a part's files stays named until it passes, or until the model says
why it doesn't apply (mqucifer/print-shop#45).

On `eval-tip-1` `capture` failed on the frame at call 32 and again at call 87. No need ran
`capture`, so nothing kept the failure in front of the model, and both times it went to its plan
stop without naming it. `unprintable.py` holds a failing `thin`, and `nudge.py` a check that keeps
failing; this holds any check, once.

**What's held.** A check is its command and the files it names (`nudge.check_of`). Each try that
failed (exit 1, or a need's own command that didn't pass its need) stays named in every later
`bench` and `run` reply, `done`'s, what `ask` sends, and the message after a compaction:
- **Where a settled need runs the check,** only a pass as that need checks it clears it: its
  command, read against its limit. A pass at other settings doesn't, as with `NOT THE
  REQUIREMENT`. The harness's own readings of the need, at `done` and at an ask, count too.
- **Where no need runs it,** each setting it failed at stays until a pass at those same settings:
  on `eval-tip-1` the model lowered `--min-lip` from 0.4 to 0.1, and a pass at 0.1 would have said
  nothing of 0.4. A pass at the settings the check was first tried at clears every setting, as the
  others were probes on the way: `eval-tip-1` went back to 0.4 and passed there.
- **No answer to rely on** (`DISAGREE`, `NO ANSWER`, a container's exit) leaves it as it was, and a
  wrong command (exit 3) isn't a try.
- `thin` at the harness's settings is `unprintable.py`'s, and isn't said twice.

**What lets it go.** Its files are all gone; or a `done` after it named the part, and none of its
files is in it. A check is the model's own files or the given references, so both are held.
Otherwise, `set_aside` with the model's reason, which a person reads at the next stop: a check a
settled need runs can't be set aside, as `done` decides it. A failure after the set-aside is held
again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from print_shop.rounds import nudge, unprintable

#: How much of what the bench said each line keeps.
WHY_CHARS = 160
HEADING = "Checks whose latest try failed, held until they pass:"
AFTER_COMPACTION = "Compacted. Still failing, from `record/CHECKS.md`:"
#: After a compaction the list is said short: the replies before it said the rest. The issue's
#: budget, from the records, is 376 tokens (`~/print-shop/proofs/compaction/README.md`).
AFTER_HOW = "Each stays until it passes, or until `set_aside` says why it doesn't apply."
#: How much of what the bench said a line keeps after a compaction.
AFTER_WHY_CHARS = 100
HOW = (
    "Each stays named until it passes: as its need runs it, or, where no need runs it, at the "
    "settings it failed at or those you first ran it at. A pass at other settings doesn't clear "
    "it. If one doesn't apply, say why with `set_aside`: the person at the next stop reads your "
    "reason."
)


@dataclass(frozen=True)
class Tried:
    """One reading of a check: the model's `bench` call, or the harness's of a need at a stop.
    `passed` is None where there's no answer to rely on."""

    at_call: int
    argv: list[str]
    passed: bool | None
    verdict: str = ""
    why: str = ""
    by: str = "bench"


@dataclass(frozen=True)
class Aside:
    """A check the model said doesn't apply, and why."""

    at_call: int
    check: nudge.Check
    why: str


@dataclass
class Held:
    """A check still failing: its latest failed try at each setting that hasn't passed since, and
    the needs that run it."""

    check: nudge.Check
    tries: list[Tried] = field(default_factory=list)
    needs: list[nudge.Wanted] = field(default_factory=list)

    @property
    def at_call(self) -> int:
        return max(one.at_call for one in self.tries)


def _settings(argv: list[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """A try's settings, so two tries that differ only in how a number is written are one."""
    found = []
    for name, values in sorted(nudge.options(argv).items()):
        shown = []
        for value in values:
            try:
                shown.append(repr(float(value)))
            except ValueError:
                shown.append(value)
        found.append((name, tuple(shown)))
    return tuple(found)


def failing(
    tries: list[Tried],
    wanted: list[nudge.Wanted],
    present: set[str],
    part: list[str] | None,
    part_at: int | None,
    asides: list[Aside] | None = None,
) -> list[Held]:
    """Each check whose failure is still open, oldest first. `tries` is oldest first."""
    by_check: dict[nudge.Check, list[Tried]] = {}
    for one in tries:
        check = nudge.check_of(one.argv)
        if check[0]:
            by_check.setdefault(check, []).append(one)
    found: list[Held] = []
    for check, mine in by_check.items():
        needs = nudge.wanted_for(check, wanted)
        open_: dict[object, Tried] = {}
        first = _settings(mine[0].argv)
        for one in mine:
            key: object = "need" if needs else _settings(one.argv)
            if one.passed is None:
                continue
            if one.passed is False:
                open_[key] = one
            elif needs:
                if nudge.as_a_need(one.argv, needs):
                    open_.clear()
            elif key == first:
                # Back at the settings it was first tried at, and passing: a try at others was a
                # probe on the way (`eval-tip-1` went from 0.4 to 0.1 and back).
                open_.clear()
            else:
                open_.pop(key, None)
        for aside in asides or []:
            if aside.check == check:
                open_ = {k: v for k, v in open_.items() if v.at_call > aside.at_call}
        if not open_:
            continue
        held = Held(check, sorted(open_.values(), key=lambda t: t.at_call), needs)
        files = check[1]
        if files and not any(name in present for name in files):
            continue
        if (
            files
            and part is not None
            and part_at is not None
            and part_at > held.at_call
            and not any(name in part for name in files)
        ):
            continue
        found.append(held)
    return sorted(found, key=lambda h: h.at_call)


def _line(one: Tried, chars: int = WHY_CHARS) -> str:
    why = " ".join(one.why.split())
    why = why if len(why) <= chars else f"{why[:chars]}…"
    who = "" if one.by == "bench" else f", by the harness at `{one.by}`"
    verdict = one.verdict or "FAIL"
    return f"- `{' '.join(nudge._words(one.argv))}`, call {one.at_call}{who}: {verdict}" + (
        f": {why}" if why else ""
    )


def lines(found: list[Held], chars: int = WHY_CHARS) -> list[str]:
    """One line a failing try, and a second where a need runs the check."""
    said: list[str] = []
    for held in found:
        for one in held.tries:
            said.append(_line(one, chars))
        if held.needs:
            named = "; ".join(f"`{w.need}` runs it as `{w.command}`" for w in held.needs)
            said.append(f"  {named}, which `done` checks.")
    return said


def said(found: list[Held]) -> str:
    """What a reply gains: each check still failing, and what clears it."""
    if not found:
        return ""
    return "\n".join([HEADING, *lines(found), HOW])


def after_compaction(found: list[Held], thin: list[unprintable.Thin] | None = None) -> str:
    """The message after a compaction: the same list, each line shorter, with each part file
    whose last `thin` failed (`unprintable.py`) in the same form."""
    said = lines(found, AFTER_WHY_CHARS)
    for one in thin or []:
        said.append(
            _line(
                Tried(one.at_call, ["thin", one.file], False, one.verdict, one.why, one.by),
                AFTER_WHY_CHARS,
            )
        )
    if not said:
        return ""
    return "\n".join([AFTER_COMPACTION, *said, AFTER_HOW])


def plain(found: list[Held]) -> list[dict[str, object]]:
    """As a reply's detail and a question keep it."""
    return [
        {
            "command": held.check[0],
            "files": list(held.check[1]),
            "needs": [w.need for w in held.needs],
            "tries": [
                {
                    "at_call": one.at_call,
                    "argv": list(nudge._words(one.argv)),
                    "verdict": one.verdict,
                    "why": one.why,
                    "by": one.by,
                }
                for one in held.tries
            ],
        }
        for held in found
    ]
