"""The harness's own check at `done`: the model's word and a tool's success don't count.

As `cad_eval`'s judging (its "race review of #41"): the script runs again from a clean start in a
fresh container over a plain copy of the working directory, with no links and nothing a run left
behind, and every piece of geometry outside `references/` taken away first. Then each need's bench
command runs in another fresh container, where no model code has run. `params` is the one exception
that runs the script by its nature, so it gets a scratch copy, and its reading is only as good as
its own second check.

What's kept for a round: the run's output, what the script wrote, and each reading's text and
numbers.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from print_shop.rounds.box import Box
from print_shop.rounds.needs import GEOMETRY, Need, Reading, judge
from print_shop.rounds.paths import copy_plain, inside, walk_plain
from print_shop.rounds.tools import SAVED, recorded

REFERENCES = "references"
TEXT_KEPT = 200_000


@dataclass
class Judgement:
    script: str
    code: int
    seconds: float
    tail: str
    readings: list[Reading] = field(default_factory=list)
    timed_out: bool = False

    @property
    def met(self) -> bool:
        return self.code == 0 and all(r.passed for r in self.readings)

    def plain(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "code": self.code,
            "seconds": self.seconds,
            "timed_out": self.timed_out,
            "met": self.met,
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


def eye_reading(need: Need, picks: dict[str, dict[str, Any]]) -> Reading:
    """An eye need passes on the Sponsor's pick, and on nothing else."""
    pick = picks.get(need.id)
    if pick is None:
        why = f"this is for the Sponsor's eye ({need.look_for}): `ask`, naming it as `need`"
        return Reading(need.id, "eye", False, why)
    reason = pick.get("reason")
    return Reading(need.id, "eye", True, f"the Sponsor picked {pick['pick']}: {reason}")


def check(
    box: Box,
    work: Path,
    script: str,
    needs: list[Need],
    picks: dict[str, dict[str, Any]],
    round_dir: Path,
) -> Judgement:
    """`done`, as the harness runs it. A script that fails leaves every bench need unread."""
    judged = round_dir / "judged"
    judgement = run_clean(box, work, script, judged)
    (round_dir / "run.txt").write_text(judgement.tail, encoding="utf-8")
    for need in needs:
        if need.by == "eye":
            judgement.readings.append(eye_reading(need, picks))
        elif judgement.code != 0:
            why = f"the script didn't finish from a clean start: exit {judgement.code}"
            judgement.readings.append(Reading(need.id, "bench", False, why))
        else:
            judgement.readings.append(read_need(box, need, judged, round_dir / "readings"))
    (round_dir / "judgement.json").write_text(
        json.dumps(judgement.plain(), indent=1), encoding="utf-8"
    )
    return judgement
