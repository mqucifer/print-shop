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

**Or by geometry, where the bytes alone differ** (mqucifer/print-shop#180). On eval-organic-3 a
clean run's `pieces build` wrote two pane files with another 17th digit in a line's point, so a
stand-in's pick of the finished suncatcher didn't hold, and the model spent about 17 calls
testing its own pipeline. `pieces build` now writes the same bytes every time; this is the
second guard, for any other way the same part comes out written differently. Where a picked
file's hash isn't among what the clean run left, and the clean run left a file of the same name,
the bench reads both (`bench/solids.py`): each solid's volume, area, centre of mass and box, and
each face's kind, area and centre. Where every figure agrees to arithmetic's noise
(`limits.ARITHMETIC`: a millionth of a mm, or of a volume or area), the pick holds, and the
reading says it was held by geometry. Any change to the geometry moves one of them, and breaks
the hold as before.
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

from print_shop.bench.limits import ARITHMETIC
from print_shop.bench.solids import MARK as SOLIDS_MARK
from print_shop.rounds.box import Box, Unanswered, answered
from print_shop.rounds.needs import BED, GEOMETRY, UNSURE, Need, Reading, bed_reading, judge
from print_shop.rounds.paths import (
    content_hash,
    copy_plain,
    geometry_hashes,
    inside,
    read_bytes_inside,
    walk_plain,
)
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


#: The files the bench reads by their solids: what `shapes.load` reads.
BY_SOLIDS = (".step", ".stp", ".stl")
#: Where the bench writes what it read of each file's solids, in its scratch folder.
SOLIDS_ANSWER = "solids.json"
#: A pick's files are compared by their solids' figures rounded to this many places, to put
#: their solids and faces in the same order; each figure is then held to arithmetic's noise.
ORDER_PLACES = 4


@dataclass
class ByGeometry:
    """A picked file whose bytes a clean run didn't leave, read against the file of its name that
    it did: `same` where every figure agrees, and what's said of it either way."""

    same: bool
    why: str


def _agree(was: float, now: float, by_size: bool) -> bool:
    """Two figures of one thing, the same to arithmetic's noise: a millionth of a mm for a length,
    and a millionth of itself for a volume or an area."""
    noise = ARITHMETIC * max(abs(was), abs(now), 1.0) if by_size else ARITHMETIC
    return abs(was - now) <= noise


def _point(p: list[float]) -> str:
    return "(" + ", ".join(f"{v:.4f}" for v in p) + ")"


def same_geometry(was: dict[str, Any], now: dict[str, Any]) -> str | None:
    """None where two readings of a file's solids (`bench/solids.py`) are the same geometry, or
    the first thing that differs."""
    for one, what in ((was, "the picked file"), (now, "the clean run's")):
        if "solids" not in one:
            return f"{what} couldn't be read for its solids: {one.get('unread') or 'no answer'}"
    a, b = was["solids"], now["solids"]
    if len(a) != len(b):
        return f"it was picked as {len(a)} solid(s), and is {len(b)}"

    def order(solid: dict[str, Any]) -> tuple[float, ...]:
        return tuple(round(v, ORDER_PLACES) for v in [*solid["centre"], solid["volume"]])

    def face_order(face: list[Any]) -> tuple[Any, ...]:
        return (face[0], *[round(v, ORDER_PLACES) for v in [*face[2], face[1]]])

    for k, (x, y) in enumerate(zip(sorted(a, key=order), sorted(b, key=order), strict=True)):
        named = f"solid {k + 1}" if len(a) > 1 else "its solid"
        figures = [
            ("volume", True, x["volume"], y["volume"], "mm³"),
            ("area", True, x["area"], y["area"], "mm²"),
        ]
        figures += [
            (f"centre's {c}", False, u, v, "mm")
            for c, u, v in zip("xyz", x["centre"], y["centre"], strict=True)
        ]
        for end, low_high_x, low_high_y in zip(("least", "most"), x["box"], y["box"], strict=True):
            figures += [
                (f"box's {end} {c}", False, u, v, "mm")
                for c, u, v in zip("xyz", low_high_x, low_high_y, strict=True)
            ]
        for what, by_size, u, v, unit in figures:
            if not _agree(u, v, by_size):
                return f"{named}'s {what} is {v:.6g} {unit}, where it was {u:.6g}"
        if len(x["faces"]) != len(y["faces"]):
            return f"{named} has {len(y['faces'])} faces, where it had {len(x['faces'])}"
        ordered = sorted(x["faces"], key=face_order), sorted(y["faces"], key=face_order)
        for fx, fy in zip(*ordered, strict=True):
            if (
                fx[0] != fy[0]
                or not _agree(fx[1], fy[1], True)
                or not all(_agree(u, v, False) for u, v in zip(fx[2], fy[2], strict=True))
            ):
                return (
                    f"{named}'s faces differ: a {str(fx[0]).lower()} face of {fx[1]:.4f} mm² at "
                    f"{_point(fx[2])} is a {str(fy[0]).lower()} of {fy[1]:.4f} mm² at "
                    f"{_point(fy[2])}"
                )
    return None


def by_geometry(
    box: Box,
    pick: dict[str, Any],
    left: dict[str, str],
    judged: Path,
    asks: Path | None,
) -> dict[str, ByGeometry]:
    """Each file of a pick whose bytes the clean run didn't leave, where it left a file of that
    name, and the ask kept the picked copy: the two read by the bench for their solids, in a
    container, and compared here. A file that couldn't be compared isn't here, and so doesn't
    hold."""
    seen = pick.get("files") or {}
    now = set(left.values())
    changed = sorted(
        name
        for name, sha in seen.items()
        if sha not in now and name in left and Path(name).suffix.lower() in BY_SOLIDS
    )
    if not changed or asks is None or pick.get("ask") is None:
        return {}
    kept = asks / str(pick["ask"]) / "variants" / str(pick.get("pick"))
    scratch = judged.parent / f"held-{uuid.uuid4().hex[:8]}"
    pairs: dict[str, tuple[str, str]] = {}
    try:
        for i, name in enumerate(changed):
            copy = kept / Path(name).name
            if not copy.is_file() or content_hash(name, copy.read_bytes()) != seen[name]:
                continue
            suffix = Path(name).suffix.lower()
            scratch.mkdir(parents=True, exist_ok=True)
            pairs[name] = (f"picked-{i}{suffix}", f"now-{i}{suffix}")
            shutil.copyfile(copy, scratch / pairs[name][0])
            (scratch / pairs[name][1]).write_bytes(read_bytes_inside(judged, name))
        if not pairs:
            return {}
        request = {"files": [f for pair in pairs.values() for f in pair], "answer": SOLIDS_ANSWER}
        ran = box.helper("solids", [json.dumps(request)], timeout=600, where=scratch)
        try:
            read = answered(ran, SOLIDS_MARK, scratch).get("files") or {}
        except Unanswered as exc:
            return {
                name: ByGeometry(False, f"its geometry couldn't be read to compare: {exc}")
                for name in pairs
            }
        found: dict[str, ByGeometry] = {}
        for name, (was, is_now) in pairs.items():
            differs = same_geometry(read.get(was) or {}, read.get(is_now) or {})
            if differs is None:
                count = len(read[was]["solids"])
                found[name] = ByGeometry(
                    True,
                    f"{name}'s bytes aren't what was picked, and its geometry is: its {count} "
                    "solid(s), each by its volume, area, centre and box, and each face by its "
                    "kind, area and centre, to arithmetic's noise",
                )
            else:
                why = f"{name}'s geometry isn't what was picked: {differs}"
                found[name] = ByGeometry(False, why)
        return found
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def not_what_it_saw(
    pick: dict[str, Any],
    left: dict[str, str],
    part: list[str] | None,
    geometry: dict[str, ByGeometry] | None = None,
) -> str | None:
    """Why a pick doesn't hold for what a clean run left, or None if it does. A file in
    `geometry` that's the same holds by it (`by_geometry`)."""
    seen = pick.get("files")
    if not seen:
        return "it holds no record of the files on its sheet"
    now = set(left.values())
    held = {name for name, one in (geometry or {}).items() if one.same}
    changed = sorted(name for name, sha in seen.items() if sha not in now and name not in held)
    if changed:
        why = f"{', '.join(changed)} changed since, or a clean run didn't leave it"
        differs = [
            (geometry or {})[name].why for name in changed if name in (geometry or {})
        ]
        return why + (f" ({'; '.join(differs)})" if differs else "")
    if part and not (
        set(seen.values()) & {left.get(name) for name in part} or held & set(part)
    ):
        return f"none of `part` ({', '.join(part)}) was on its sheet"
    return None


def eye_reading(
    need: Need,
    picks: dict[str, dict[str, Any]],
    left: dict[str, str],
    part: list[str] | None = None,
    geometry: dict[str, ByGeometry] | None = None,
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
    stale = not_what_it_saw(pick, left, part, geometry)
    if stale is not None:
        why = (
            f"{who} picked {pick['pick']} at ask {pick.get('ask')}, and that pick doesn't hold: "
            f"{stale}. It's asked again: `ask`, naming it in `need` with every other eye need its "
            "sheet answers"
        )
        return Reading(need.id, "eye", False, why)
    held = [one.why for one in (geometry or {}).values() if one.same]
    said = f"{who} picked {pick['pick']}: {reason}"
    if held:
        said += ". It holds by geometry: " + "; ".join(held)
    return Reading(need.id, "eye", True, said)


def check(
    box: Box,
    work: Path,
    script: str,
    needs: list[Need],
    picks: dict[str, dict[str, Any]],
    round_dir: Path,
    part: list[str] | None = None,
    on_bed: dict[str, str] | None = None,
    asks: Path | None = None,
) -> Judgement:
    """`done`, as the harness runs it. A script that fails leaves every bench need unread, and a
    part a clean run didn't leave isn't met, whatever the needs read. A bed need is read against
    `done`'s `on_bed` (mqucifer/print-shop#73)."""
    judged = round_dir / "judged"
    judgement = run_clean(box, work, script, judged)
    judgement.part_missing = [
        f for f in part or [] if not (judged / f).is_file() or inside(judged, f) is None
    ]
    (round_dir / "run.txt").write_text(judgement.tail, encoding="utf-8")
    # Before any check runs, as `panes` writes geometry of its own.
    judgement.left = geometry_hashes(judged)
    # A picked file the clean run wrote differently, read for its geometry once a pick.
    compared: dict[tuple[Any, Any], dict[str, ByGeometry]] = {}
    for need in needs:
        if need.by == "eye":
            pick = picks.get(need.id)
            geometry: dict[str, ByGeometry] = {}
            if pick is not None:
                key = (pick.get("ask"), pick.get("pick"))
                if key not in compared:
                    compared[key] = by_geometry(box, pick, judgement.left, judged, asks)
                geometry = compared[key]
            judgement.readings.append(eye_reading(need, picks, judgement.left, part, geometry))
        elif need.by == BED:
            judgement.readings.append(bed_reading(need, on_bed or {}, part))
        elif judgement.code != 0:
            why = f"the script didn't finish from a clean start: exit {judgement.code}"
            judgement.readings.append(Reading(need.id, "bench", False, why))
        else:
            judgement.readings.append(read_need(box, need, judged, round_dir / "readings"))
    (round_dir / "judgement.json").write_text(
        json.dumps(judgement.plain(), indent=1), encoding="utf-8"
    )
    return judgement
