"""The harness's own check at `done`: the model's word and a tool's success don't count.

As `cad_eval`'s judging (its "race review of #41"): the script runs again from a clean start in a
fresh container over a plain copy of the working directory, with no links and nothing a run left
behind, and every piece of geometry outside `references/` taken away first. Then each need's bench
command runs in another fresh container, where no model code has run. `params` is the one exception
that runs the script by its nature, so it gets a scratch copy, and its reading is only as good as
its own second check.

What's kept for a round: the run's output, what the script wrote, and each reading's text and
numbers.

An eye need's pick holds only for what it saw. The sheet's files are held by what they contain, and
the clean run has to leave the same, with some of it in the `part`. Otherwise the need is asked
again, since a pick on an older design says nothing of this one.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds.box import Box
from print_shop.rounds.needs import GEOMETRY, UNSURE, Need, Reading, judge
from print_shop.rounds.paths import copy_plain, geometry_hashes, inside, walk_plain
from print_shop.rounds.tools import SAVED, recorded

REFERENCES = "references"
#: Who made a pick: `pick.json`'s `by`, the Sponsor or a named stand-in.
SPONSOR = "sponsor"
STAND_IN = "stand-in:"
TEXT_KEPT = 200_000
NOT_IN_A_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Judgement:
    script: str
    code: int
    seconds: float
    tail: str
    readings: list[Reading] = field(default_factory=list)
    timed_out: bool = False
    part_missing: list[str] = field(default_factory=list)
    # What the clean run left, each piece of geometry by what it holds.
    left: dict[str, str] = field(default_factory=dict)

    @property
    def met(self) -> bool:
        return self.code == 0 and not self.part_missing and all(r.passed for r in self.readings)

    def plain(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "code": self.code,
            "seconds": self.seconds,
            "timed_out": self.timed_out,
            "met": self.met,
            "part_missing": self.part_missing,
            "left": self.left,
            "tail": self.tail,
            "readings": [
                {k: v for k, v in r.__dict__.items() if k not in ("text", "record")}
                for r in self.readings
            ],
        }


def clean_copy(work: Path, target: Path) -> None:
    """What a clean start has: the working directory's plain files, less what runs make, and less
    all geometry outside `references/`, which the script has to make again."""
    copy_plain(work, target, skip_made=True)
    for name in walk_plain(target):
        if name.suffix.lower() in GEOMETRY and name.parts[0] != REFERENCES:
            (target / name).unlink()


def run_clean(box: Box, work: Path, script: str, into: Path) -> Judgement:
    clean_copy(work, into)
    ran = box.python(script, where=into)
    return Judgement(script, ran.code, ran.seconds, ran.output[-8000:], timed_out=ran.timed_out)


def read_need(box: Box, need: Need, judged: Path, kept: Path) -> Reading:
    """One bench need's check, in a fresh container, its answer kept."""
    missing = [f for f in need.files() if not (judged / f).is_file() or inside(judged, f) is None]
    if missing:
        named = ", ".join(missing)
        return Reading(need.id, "bench", False, f"there's no {named}: a clean run has to leave it")
    out = f".judge-{uuid.uuid4().hex[:12]}"
    argv = ["--out", out, *need.argv()]
    where = judged
    scratch: Path | None = None
    if need.argv()[0] == "params":
        scratch = judged.parent / out
        copy_plain(judged, scratch)
        where = scratch
    try:
        ran = box.bench(argv, where=where)
        record = recorded(ran.output, where)
        reading = judge(need, ran.code, ran.output, record)
        reading.seconds = ran.seconds
        kept.mkdir(parents=True, exist_ok=True)
        (kept / f"{need.id}.txt").write_text(ran.output[-TEXT_KEPT:], encoding="utf-8")
        if record is not None:
            (kept / f"{need.id}.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        reading.text = SAVED.sub(f"saved: readings/{need.id}.txt and .json", ran.output)
    finally:
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(where / out, ignore_errors=True)
    return reading


@dataclass
class Standing:
    """A check the harness runs on a file whatever the needs say. `unsure` is the bench having no
    answer to rely on, said, and taken as neither a pass nor a fail."""

    check: str
    file: str
    command: str
    passed: bool
    unsure: bool
    verdict: str
    why: str
    numbers: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.passed and not self.unsure

    def plain(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "numbers"}


#: Every file to be printed is read by `thin`, at the nozzle's width: on `cup-1` two panes the
#: Sponsor found too thin in Orca passed every need, as no need ran `thin` on a pane.
PRINTABLE = "printable"


def printable(box: Box, files: list[str], where: Path, kept: Path) -> list[Standing]:
    """`thin` on each file, each in a fresh container."""
    found = []
    for name in files:
        command = f"thin {shlex.quote(name)}"
        need = Need(
            f"thin-{NOT_IN_A_NAME.sub('_', name)}"[:80],
            "nothing narrower than the nozzle",
            "derived",
            "bench",
            command=command,
            verdicts=["OK"],
        )
        reading = read_need(box, need, where, kept)
        unsure = reading.verdict in UNSURE or reading.exit in (2, 3) or not reading.verdict
        # The bench's own words, which say where and how thin, over the need's "must be OK".
        said = str((reading.record or {}).get("why") or "")
        why = f"{reading.verdict}: {said}" if reading.verdict and said else reading.why
        found.append(
            Standing(
                PRINTABLE,
                name,
                command,
                reading.passed,
                unsure and not reading.passed,
                reading.verdict,
                why,
                dict((reading.record or {}).get("numbers") or {}),
            )
        )
    return found


def not_what_it_saw(
    pick: dict[str, Any], left: dict[str, str], part: list[str] | None
) -> str | None:
    """Why a pick doesn't hold for what a clean run left, or None if it does."""
    seen = pick.get("files")
    if not seen:
        return "it holds no record of the files on its sheet"
    now = set(left.values())
    changed = sorted(name for name, sha in seen.items() if sha not in now)
    if changed:
        return f"{', '.join(changed)} changed since, or a clean run didn't leave it"
    if part and not set(seen.values()) & {left.get(name) for name in part}:
        return f"none of `part` ({', '.join(part)}) was on its sheet"
    return None


def eye_reading(
    need: Need,
    picks: dict[str, dict[str, Any]],
    left: dict[str, str],
    part: list[str] | None = None,
) -> Reading:
    """An eye need passes on a pick that saw these files, and on nothing else. A stand-in's passes
    it too, so a run can be proved with no Sponsor there, and its reading says who it was."""
    pick = picks.get(need.id)
    if pick is None:
        why = (
            f"this is for the Sponsor's eye ({need.look_for}): `ask`, naming it in `need` with "
            "every other eye need its sheet answers"
        )
        return Reading(need.id, "eye", False, why)
    reason = pick.get("reason")
    if pick.get("by") == SPONSOR:
        who = "the Sponsor"
    else:
        who = f"a stand-in for the Sponsor ({str(pick.get('by'))[len(STAND_IN) :].strip()})"
    stale = not_what_it_saw(pick, left, part)
    if stale is not None:
        why = (
            f"{who} picked {pick['pick']} at ask {pick.get('ask')}, and that pick doesn't hold: "
            f"{stale}. It's asked again: `ask`, naming it in `need` with every other eye need its "
            "sheet answers"
        )
        return Reading(need.id, "eye", False, why)
    return Reading(need.id, "eye", True, f"{who} picked {pick['pick']}: {reason}")


def check(
    box: Box,
    work: Path,
    script: str,
    needs: list[Need],
    picks: dict[str, dict[str, Any]],
    round_dir: Path,
    part: list[str] | None = None,
) -> Judgement:
    """`done`, as the harness runs it. A script that fails leaves every bench need unread, and a
    part a clean run didn't leave isn't met, whatever the needs read."""
    judged = round_dir / "judged"
    judgement = run_clean(box, work, script, judged)
    judgement.part_missing = [
        f for f in part or [] if not (judged / f).is_file() or inside(judged, f) is None
    ]
    (round_dir / "run.txt").write_text(judgement.tail, encoding="utf-8")
    # Before any check runs, as `panes` writes geometry of its own.
    judgement.left = geometry_hashes(judged)
    for need in needs:
        if need.by == "eye":
            judgement.readings.append(eye_reading(need, picks, judgement.left, part))
        elif judgement.code != 0:
            why = f"the script didn't finish from a clean start: exit {judgement.code}"
            judgement.readings.append(Reading(need.id, "bench", False, why))
        else:
            judgement.readings.append(read_need(box, need, judged, round_dir / "readings"))
    (round_dir / "judgement.json").write_text(
        json.dumps(judgement.plain(), indent=1), encoding="utf-8"
    )
    return judgement
