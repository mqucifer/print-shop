"""A finished run prints again, as it was or with a small change (mqucifer/print-shop#216).

There was no way back into a finished run. Repeating a good print meant running it again from
the start, the model, the questions and the overrides again, with that day's lessons and
profiles, and it didn't come out the same.

- **Finished runs are listed** (`runs`, `run_line`): each that ended, failed ones too, with its
  outcome (`outcome_words`), the note, the spool and what the person changed, newest first, and
  found by part, colour, note or id.
- **Print again** (`Reprint.as_it_was`): any finished run's kept file goes to ready as it is,
  with nothing asked, proposed or sliced. It's checked against the sha256 the ledger keeps of
  what was sent. **From now on each job keeps** that hash, a copy of its part and the three
  profiles it sliced with (`keep`, at its slice; a queued copy has its first's). A run from
  before kept no hash: its file's settings, Orca's time and its layers are checked against the
  ledger's, and that its bytes can't be is said. A file that's gone, or changed, prints
  nothing, saying so.
- **Change and print again** (`Reprint.change`): from that run's exact part, pose, profiles and
  settings, with no question and no model. The settings are those that reached its slice, its
  pause given at the height that pauses where it did (`desk.again._pause_again`), and one whose
  G-code doesn't say where it paused left out, saying so. The person's changes go over them and
  it's sliced again. A run from before has no kept part or profiles: its part is found by its
  sha256, and its profiles are the store's files it names, each checked by its hash. What
  can't be checked is said, and the comparison shows what it changes.
- **The new slice is compared with that run's** (`compare`), read from both G-codes' config
  blocks: what differs is shown, and anything beyond what was changed is named.
- **A newer lesson is a suggestion here, never applied by itself** (`Reprint.suggestions`), and
  so is a setting asked at that run that didn't reach its slice (mqucifer/print-shop#206).
  `take N` takes one, as a change.
- **The new run is linked to the one it came from** and has its own outcome: it's a sliced
  job (`sliced.SlicedJob`), marked a reprint of that run, with what was changed, how it
  differs and what was suggested (`sliced.Reprinted`). Start's checks and arming are those of
  any job: it goes to the same gate.

A hand-sliced file prints again as it was: it's changed in Orca. A plate of several parts prints
again as it was. A different or revised file is mqucifer/print-shop#211's ("Use these").

Nothing here arms, starts or approves anything: it reads the ledger, and slices with Orca.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from print_shop import sliced
from print_shop.ingest.orientation import write_rotated
from print_shop.ledger import Ledger, Row
from print_shop.outcome_words import outcome_words
from print_shop.plate import plate_job
from print_shop.setup import lessons as print_lessons
from print_shop.setup.proposal import (
    ORIENTATION,
    PAUSE,
    PAUSE_NAMES,
    Change,
    NotPlaced,
    Proposal,
    Source,
    apply,
    placeable,
)
from print_shop.setup.reach import unplaced
from print_shop.slicer.orca import (
    BedType,
    Slicer,
    SlicerProfiles,
    parse_config_block,
    parse_gcode_header,
    with_bed_type,
)
from print_shop.slicer.store import KINDS, ProfileRef, Store

__all__ = [
    "KEPT_DIR",
    "Changed",
    "Refused",
    "Reprint",
    "Suggestion",
    "compare",
    "copy_kept",
    "find",
    "keep",
    "parse_changes",
    "run_line",
    "runs",
]

logger = logging.getLogger(__name__)

#: The folder in a job's own where what it sliced with is kept, from its start.
KEPT_DIR = "again"
#: What's kept there, with each file's sha256.
KEPT_JSON = "kept.json"
#: Where a change's slice is made, under the jobs folder, before it's a job of its own.
SLICES_DIR = "again"


class Refused(Exception):  # noqa: N818 - a refusal, said as what happened
    """It can't be printed again so, said in one sentence: nothing is sent."""


# --- keeping, from each job's start --------------------------------------------------------------


def keep(folder: Path, part: Path, part_sha256: str, profiles: SlicerProfiles) -> list[str]:
    """At a job's slice: its part and the three profiles it sliced with, copied into its folder,
    with each one's sha256, so the run can be sliced again as it was. The profiles are the
    whole ones Orca was given, before the job's settings, which the ledger keeps. Returns what
    wasn't kept, each saying why; nothing here stops the print."""
    into = folder / KEPT_DIR
    missed: list[str] = []
    kept: dict[str, dict[str, str]] = {}
    try:
        into.mkdir(parents=True, exist_ok=True)
        data = part.read_bytes()
        if part_sha256 and _sha(data) != part_sha256:
            missed.append(f"the part, {part.name}: it changed after it was read")
        else:
            name = "part" + "".join(part.suffixes[-1:]).lower()
            (into / name).write_bytes(data)
            kept["part"] = {"file": name, "name": part.name, "sha256": _sha(data)}
        for kind in KINDS:
            given = getattr(profiles, kind)
            if given is None:
                continue
            raw = Path(given).read_bytes()
            (into / f"{kind}.json").write_bytes(raw)
            kept[kind] = {"file": f"{kind}.json", "sha256": _sha(raw)}
        (into / KEPT_JSON).write_text(json.dumps(kept, indent=1))
    except OSError as exc:
        missed.append(f"what it sliced with: {exc}")
    for each in missed:
        logger.warning("Not kept for a print again: %s", each)
    return missed


def copy_kept(first: Path, copy: Path) -> None:
    """What a job kept to print again, given to a copy of it, which printed the same slice."""
    if (first / KEPT_DIR).is_dir():
        shutil.copytree(first / KEPT_DIR, copy / KEPT_DIR, dirs_exist_ok=True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- the runs, listed ----------------------------------------------------------------------------


def runs(ledger: Ledger, find: str = "") -> list[Row]:
    """Every finished run, newest first: each print that ended, failed ones too, once for a
    plate. `find` keeps those whose part, colour, filament, outcome, note or id has each of its
    words."""
    words = find.lower().split()
    found = [row for row in ledger.rows() if row.ended_at is not None and row.heads_job]
    if words:
        plates: dict[str, list[Row]] = {}
        if any(row.plate is not None for row in found):
            for row in ledger.rows():
                if row.plate is not None:
                    plates.setdefault(row.plate.job_id, []).append(row)
        found = [row for row in found
                 if all(w in _searched(row, plates.get(plate_job(row.job_id), ()))
                        for w in words)]
    return found[::-1]  # the ledger's order, by start and then row, newest first


def _searched(row: Row, plate: Iterable[Row]) -> str:
    said = [row.job_id, row.stl_name, *(r.stl_name for r in plate), row.note or "",
            str(row.result or ""), str(row.symptom or "")]
    if row.material is not None:
        said += [row.material.colour, row.material.variety, row.material.tuning or "",
                 f"#{row.material.spool_id}"]
    said += [row.filament]
    return " ".join(said).lower()


def run_line(row: Row, n: int | None = None) -> list[str]:
    """A finished run as the list says it: when, the part, the outcome and note, the spool, and
    what the person changed."""
    when = (row.ended_at or row.start_requested_at)[:16].replace("T", " ")
    outcome = (outcome_words(row.result, row.symptom) if row.result is not None
               else "its outcome not yet recorded")
    number = f"{n:>2}. " if n is not None else ""
    part = row.stl_name if row.plate is None else f"a plate of {len(row.plate.parts)}: " + \
        ", ".join(p.name for p in row.plate.parts)
    lines = [f"{number}{when}  {part}  [{row.job_id[:8]}]"]
    pad = " " * len(number)
    said = outcome + (f': "{row.note}"' if row.note else "")
    lines.append(f"{pad}    {said}")
    spool = row.material.named() if row.material is not None else row.filament
    lines.append(f"{pad}    on {spool}")
    changed = [f"{c.key}={c.value}" for c in row.corrections or ()
               if c.key != ORIENTATION and not c.said_no_to]
    turned = [c for c in row.corrections or () if c.key == ORIENTATION]
    if turned:
        changed.insert(0, f"turned to {turned[-1].value}")
    again = row.sliced.reprinted if row.sliced is not None else None
    if row.sliced is not None and row.sliced.reprint_of:
        how = ("changed: " + "; ".join(again.said_changes()) if again and again.changes
               else "as it was")
        lines.append(f"{pad}    a print again of job {row.sliced.reprint_of[:8]}, {how}")
    elif row.sliced is not None:
        lines.append(f"{pad}    sliced by hand")
    elif changed:
        lines.append(f"{pad}    you changed: " + "; ".join(changed))
    return lines


# --- one run, found to print again ---------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    """A change the run didn't have that's suggested, never applied by itself: a lesson kept or
    edited since, or what's known to be wrong with that run."""

    key: str
    value: str
    why: str

    def change(self, preset: str = "") -> Change:
        return Change(key=self.key, preset=preset, value=self.value,
                      reason=f"suggested at a print again: {self.why}", source=Source.SPONSOR)


@dataclass(frozen=True)
class Difference:
    """A setting whose value differs between the run's G-code and the new one."""

    key: str
    was: str
    now: str
    changed: bool  # one of the person's changes named it

    def line(self) -> str:
        return f"{self.key}: {_short(self.was)} -> {_short(self.now)}"


def _short(value: str) -> str:
    return value if len(value) <= 60 else value[:57] + "..."


@dataclass(frozen=True)
class Changed:
    """A change's slice: where its G-code is, and how it differs from the run's."""

    gcode: Path
    changes: tuple[Change, ...]
    differs: tuple[Difference, ...]
    #: The pause, and Orca's time, filament and layers, then and now.
    totals: tuple[str, ...] = ()

    @property
    def beyond(self) -> tuple[Difference, ...]:
        """What differs that no change named."""
        return tuple(d for d in self.differs if not d.changed)

    def lines(self) -> list[str]:
        out = ["Sliced again. Against that run's G-code:"]
        out += [f"  {t}" for t in self.totals]
        named = [d for d in self.differs if d.changed]
        out += [f"  changed      {d.line()}" for d in named]
        unmoved = [c.key for c in self.changes
                   if c.key != PAUSE and not any(d.key == c.key for d in self.differs)]
        out += [f"  NOT CHANGED  {key}: the slice has the run's value still" for key in unmoved]
        out += [f"  ALSO DIFFERS {d.line()}: no change of yours named it" for d in self.beyond]
        if not self.beyond:
            out.append("  Nothing else differs.")
        return out


def compare(was: dict[str, str], now: dict[str, str],
            changed: Iterable[str]) -> tuple[Difference, ...]:
    """The settings whose values differ between two config blocks, each marked with whether a
    change named it."""
    named = set(changed)
    return tuple(
        Difference(key=key, was=was.get(key, "(not in it)"), now=now.get(key, "(not in it)"),
                   changed=key in named)
        for key in sorted(set(was) | set(now)) if was.get(key) != now.get(key)
    )


@dataclass
class Reprint:
    """A finished run, found to print again: what's kept of it and checked, its settings, and
    what's suggested."""

    #: The run asked of.
    row: Row
    #: The run whose part, pose, profiles and settings a change starts from: the row itself, or
    #: the run a changed print again was made from.
    first: Row
    #: The changes a print again of `first` was made with, which a change of it keeps.
    earlier: tuple[Change, ...]
    #: Its kept G-code, where it still holds the bytes it printed; else why not.
    gcode: Path | None
    gone: str
    #: The part and profiles a change slices with, where they're found; else why not.
    part: Path | None
    profiles: SlicerProfiles | None
    no_change: str
    #: `first`'s settings, as they reached its slice, with `earlier` over them.
    settings: tuple[Change, ...]
    #: The run's config block, from its kept G-code or the ledger.
    config: dict[str, str]
    suggestions: tuple[Suggestion, ...]
    #: What can't be checked, and what's said of its settings, each a sentence.
    unchecked: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: For each kept profile, as Orca's slice reads it: the process and filament.
    _presets: dict[str, dict[str, object]] = field(default_factory=dict)

    def lines(self, asking: bool = True) -> list[str]:
        """The run, as the screen before printing it again shows it: with how to answer where
        it's `asking`."""
        out = run_line(self.row)
        out[0] = "Print again: " + out[0]
        if self.gcode is not None:
            out.append(f"Its file is kept ({self.gcode.name}), with the bytes it printed: Enter "
                       "prints it as it was.")
        else:
            out.append(f"It can't print as it was: {self.gone}.")
        out += [f"  ({u})" for u in self.unchecked]
        if self.no_change:
            out.append(f"It can't be changed here: {self.no_change}.")
        else:
            out.append("Its settings over its profiles"
                       + (" (and the changes its print again had)" if self.earlier else "")
                       + ": " + ("; ".join(f"{c.key}={c.value}" for c in self.settings)
                                 if self.settings else "none, its profiles as they were") + ".")
            out += [f"  ({n})" for n in self.notes]
            if self.suggestions:
                out.append("Suggested, and not applied" + (": take N takes one." if asking
                                                           else ":"))
                out += [f"  {n}. {s.key}={s.value}: {s.why}"
                        for n, s in enumerate(self.suggestions, 1)]
            if asking:
                out.append("Type a change as key=value (several with ;), and it's sliced again "
                           "from its part, pose, profiles and settings, with no question and no "
                           "model.")
        if asking:
            out.append("Enter prints what's shown; stop ends it, with nothing sent.")
        return out

    def as_it_was(self) -> sliced.Again:
        """Its kept file, to send as a new job marked a print again of it.

        Raises:
            Refused: If the file is gone, or no longer holds the bytes it printed.
        """
        if self.gcode is None:
            raise Refused(f"job {self.row.job_id[:8]} can't print as it was: {self.gone}, so "
                          "nothing was sent")
        row = self.row
        hand = row.sliced
        if hand is not None and hand.reprinted is None:
            found = sliced.Again(path=self.gcode, name=row.stl_name, reprint_of=row.job_id,
                                 overrides=hand.overrides, overrides_from=hand.overrides_from)
            if found.overrides is not None and not found.overrides_from.startswith("job "):
                found = found.model_copy(update={
                    "overrides_from": f"job {row.job_id}'s record, read from "
                                      f"{hand.overrides_from}"})
            return found
        return sliced.Again(
            path=self.gcode, name=row.stl_name, reprint_of=row.job_id, sliced_as=sliced.AGAIN,
            reprinted=sliced.Reprinted(
                first=self.first.job_id,
                changes=tuple(sliced.Made(key=c.key, value=c.value, reason=c.reason)
                              for c in self.earlier),
                unchecked=self.unchecked, pose=self.first.rotation_deg))

    def change(self, changes: Sequence[Change], into: Path, slicer: Slicer) -> Changed:
        """Sliced again from the run's part, pose, profiles and settings, with `changes` over
        them, into `into`; compared with the run's G-code.

        Raises:
            Refused: If it can't be changed here, a change can't be placed in a profile, or the
                slice fails.
        """
        if self.no_change or self.part is None or self.profiles is None:
            raise Refused(f"it can't be changed here: {self.no_change}")
        asked = Proposal(changes=self.settings).corrected(list(changes))
        process, filament = self._presets.get("process", {}), self._presets.get("filament", {})
        refused = [f"{c.key}={c.value}: {why}" for c in changes if c.key != PAUSE
                   if (why := unplaced(c.key, process, filament))]
        if refused:
            raise Refused("; ".join(refused))
        into.mkdir(parents=True, exist_ok=True)
        try:
            profiles = apply(self.profiles, placeable(asked, process, filament),
                             into / "profiles")
        except NotPlaced as exc:
            raise Refused(str(exc)) from exc
        rotation = self.first.rotation_deg
        stl = (self.part if rotation == (0.0, 0.0, 0.0)
               else write_rotated(self.part, rotation, into / "rotated.stl"))
        try:
            result = slicer(stl, profiles, into / "slice")
        except (RuntimeError, OSError) as exc:
            raise Refused(f"it couldn't be sliced: {str(exc).splitlines()[0]}") from exc
        now = parse_config_block(result.gcode_path)
        named = {c.key for c in changes} | {c.key for c in self.earlier}
        differs = compare(self.config, now, named)
        totals = [f"Orca         {self.row.print_time_s // 60} min, "
                  f"{self.row.filament_cm3:g} cm3, {self.row.layer_count} layers then; "
                  f"{result.print_time_s // 60} min, {result.filament_cm3:g} cm3, "
                  f"{result.layer_count} layers now"]
        totals += _pauses_then_and_now(self.row, result.gcode_path)
        return Changed(gcode=result.gcode_path, changes=tuple(changes), differs=differs,
                       totals=tuple(totals))

    def changed(self, made: Changed) -> sliced.Again:
        """A change's slice, to send as a new job marked a print again of the run, with what
        was changed, how it differs and what was suggested."""
        return sliced.Again(
            path=made.gcode, name=self.row.stl_name, reprint_of=self.row.job_id,
            sliced_as=sliced.AGAIN,
            reprinted=sliced.Reprinted(
                first=self.first.job_id,
                changes=tuple(sliced.Made(key=c.key, value=c.value, reason=c.reason)
                              for c in (*self.earlier, *made.changes)),
                differs=tuple(d.line() for d in made.differs),
                beyond=tuple(d.key for d in made.beyond),
                suggested=tuple(f"{s.key}={s.value}: {s.why}" for s in self.suggestions),
                unchecked=self.unchecked, pose=self.first.rotation_deg))


def _pauses_then_and_now(row: Row, gcode: Path) -> list[str]:
    """The run's pauses and the new slice's, where either has one."""
    from print_shop.setup.reading import read_slice  # noqa: PLC0415 - read only for a pause

    then = row.reading.pauses if row.reading is not None else None
    try:
        now = read_slice(gcode).pauses
    except (OSError, ValueError):
        return []
    if then is None or (not then and not now):
        return []
    return [f"pause        {'; '.join(p.where() for p in then) or 'none'} then; "
            f"{'; '.join(p.where() for p in now) or 'none'} now"]


def find(ledger: Ledger, jobs: Path, job_id: str, *, store_root: Path,
         bed: BedType, parts: Path | None = None) -> Reprint:
    """A finished run, by its id or the start of it, with what's kept of it checked. `parts`
    is the parts folder, where a run from before has its part looked for by its sha256.

    Raises:
        Refused: If no run has that id, or several start with it, or it hasn't finished.
    """
    row = _run(ledger, job_id)
    if row.plate is not None and not row.heads_job:
        head = ledger.get(plate_job(row.job_id))
        row = head if head is not None else row
    if row.ended_at is None:
        raise Refused(f"job {row.job_id[:8]} hasn't finished: it's printing, or its start was "
                      "never seen")
    unchecked: list[str] = []
    gcode, gone = _kept_gcode(ledger, jobs, row, unchecked)
    config = _config(ledger, row, gcode)
    first = row
    earlier: tuple[Change, ...] = ()
    again = row.sliced.reprinted if row.sliced is not None else None
    if again is not None:
        found = ledger.get(again.first)
        if found is None:
            raise Refused(f"job {row.job_id[:8]} printed job {again.first[:8]} again, which "
                          "isn't in the ledger")
        first = found
        earlier = tuple(Change(key=m.key, value=m.value, preset="", reason=m.reason,
                               source=Source.SPONSOR) for m in again.changes)
    reprint = Reprint(row=row, first=first, earlier=earlier, gcode=gcode, gone=gone, part=None,
                      profiles=None, no_change="", settings=(), config=config, suggestions=())
    if row.sliced is not None and again is None:
        reprint.no_change = "it was sliced by hand: change it in Orca, and send it with --sliced"
    elif first.plate is not None:
        reprint.no_change = "a plate of several parts prints again as it was"
    else:
        _for_a_change(reprint, ledger, jobs, store_root, bed, unchecked, parts)
    reprint.unchecked = tuple(unchecked)
    return reprint


def _run(ledger: Ledger, job_id: str) -> Row:
    row = ledger.get(job_id)
    if row is not None:
        return row
    found = [r for r in ledger.rows() if r.job_id.startswith(job_id)] if len(job_id) >= 4 else []
    if len(found) == 1:
        return found[0]
    if len({plate_job(r.job_id) for r in found}) == 1 and found:
        return next(r for r in found if r.heads_job)
    if found:
        raise Refused(f"several runs start with {job_id}: give more of its id")
    raise Refused(f"job {job_id} isn't in the ledger, so there's nothing to print again: "
                  "--runs lists the runs it has")


def _kept_gcode(ledger: Ledger, jobs: Path, row: Row,
                unchecked: list[str]) -> tuple[Path | None, str]:
    """The run's kept G-code, if it still holds what it printed, and else why not."""
    folder = jobs / plate_job(row.job_id)
    path = folder / row.gcode_name
    if not path.is_file():
        return None, f"its file isn't in its folder any more ({path})"
    sha = row.gcode_sha256 or (row.sliced.gcode_sha256 if row.sliced is not None else "")
    now = sliced.sha256_of(path)
    if sha:
        if now != sha:
            return None, (f"its file has changed since it printed (sha256 {sha[:12]}… then, "
                          f"{now[:12]}… now)")
        return path, ""
    # A run from before the hash was kept: what the ledger has of the file, checked.
    was = ledger.config(row.job_id)
    try:
        head = parse_gcode_header(path)
        config = parse_config_block(path)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, f"its file can't be read ({exc})"
    if config != was or (head.print_time_s, head.layer_count) != (row.print_time_s,
                                                                  row.layer_count):
        return None, "its file's settings or totals aren't those the ledger has: it has changed"
    unchecked.append("its G-code's hash wasn't kept then: its settings, Orca's time and its "
                     "layers are the ledger's, but its bytes can't be checked")
    return path, ""


def _config(ledger: Ledger, row: Row, gcode: Path | None) -> dict[str, str]:
    if gcode is not None:
        try:
            return parse_config_block(gcode)
        except (OSError, RuntimeError):
            pass
    return ledger.config(row.job_id) or {}


def _for_a_change(reprint: Reprint, ledger: Ledger, jobs: Path, store_root: Path, bed: BedType,
                  unchecked: list[str], parts: Path | None) -> None:
    """What a change slices with: the first run's part, profiles and settings, checked."""
    first = reprint.first
    folder = jobs / first.job_id
    kept = _read_kept(folder)
    reprint.part = _part(first, folder, kept, unchecked, parts)
    if reprint.part is None:
        reprint.no_change = (f"its part, {first.stl_name}, wasn't kept, and no file with the bytes "
                             f"it printed (sha256 {first.stl_sha256[:12]}…) is where its log "
                             "names it, in its folder or in the parts folder")
        return
    profiles = _profiles(first, folder, kept, store_root, bed, unchecked)
    if isinstance(profiles, str):
        reprint.no_change = profiles
        return
    reprint.profiles = profiles
    reprint._presets = {
        "process": json.loads(profiles.process.read_text()),
        "filament": json.loads(profiles.filament.read_text()) if profiles.filament else {},
    }
    settings, notes = _settings(first)
    if first.proposal is None:
        notes.append("that run kept no settings of its own: it's sliced with its profiles alone, "
                     "and the comparison shows anything that differs")
    reprint.settings = Proposal(changes=tuple(settings)).corrected(list(reprint.earlier)).changes
    if first.spools:
        notes.append("it had a swap: the print again pauses where it did, and its spools aren't "
                     "tracked through it, as for a file sliced by hand")
    reprint.notes = tuple(notes)
    reprint.suggestions = tuple(_suggestions(ledger, first, reprint.config))


def _read_kept(folder: Path) -> dict[str, dict[str, str]]:
    try:
        found = json.loads((folder / KEPT_DIR / KEPT_JSON).read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _part(first: Row, folder: Path, kept: dict[str, dict[str, str]],
          unchecked: list[str], parts: Path | None) -> Path | None:
    """Its kept part; for a run from before, the file with the bytes it printed: where its log
    names it, then in its folder, then in the parts folder."""
    own = kept.get("part")
    if own is not None:
        path = folder / KEPT_DIR / own["file"]
        if path.is_file() and sliced.sha256_of(path) == first.stl_sha256:
            return path
        unchecked.append("its kept part has changed since it printed, so it's looked for by its "
                         "sha256")
    named: list[Path] = []
    try:
        log = (folder / "job.log").read_text(encoding="utf-8", errors="replace")
        named = [Path(m.strip()) for m in re.findall(r"^part: (.+)$", log, re.M)]
    except OSError:
        pass
    places = [(p, False) for p in named] + [(folder, True)] + ([(parts, True)] if parts else [])
    for path, deep in places:
        found = sorted(path.rglob("*")) if deep and path.is_dir() else [path]
        for each in found:
            if (each.is_file() and each.suffix.lower() in _PART_SUFFIXES
                    and sliced.sha256_of(each) == first.stl_sha256):
                if own is None:
                    unchecked.append(f"its part wasn't kept then: {each} has the bytes it "
                                     "printed")
                return each
    return None


_PART_SUFFIXES = (".stl", ".3mf", ".obj")


def _profiles(first: Row, folder: Path, kept: dict[str, dict[str, str]], store_root: Path,
              bed: BedType, unchecked: list[str]) -> SlicerProfiles | str:
    """The profiles it sliced with: those kept in its folder, checked by their hash; for a run
    from before, the store's files it names, each checked by the hash the ledger has."""
    paths: dict[str, Path] = {}
    for kind in KINDS:
        own = kept.get(kind)
        if own is None:
            break
        path = folder / KEPT_DIR / own["file"]
        if not path.is_file() or sliced.sha256_of(path) != own["sha256"]:
            break
        paths[kind] = path
    if len(paths) == len(KINDS):
        return SlicerProfiles(machine=paths["machine"], process=paths["process"],
                              filament=paths["filament"], used=first.profiles)
    used = first.profiles
    if used is None:
        return "it kept no profiles, and the ledger doesn't name the ones it sliced with"
    unchecked.append("its profiles weren't kept then: it's sliced with the store's files it "
                     f"names, on today's plate setting ({bed.value})")
    store = Store(store_root)
    into = folder / KEPT_DIR / "from-store"
    composed: dict[str, Path] = {}
    for kind, ref in (("machine", used.machine), ("process", used.process),
                      ("filament", used.filament)):
        profile = next((p for p in store.all(kind) if p.path.relative_to(store_root).as_posix()
                        == ref.file), None)
        if profile is None:
            return f"its {kind} profile, {ref.name}, isn't in the store any more"
        edited = _edited(profile.path, ref, profile.standard.path if profile.standard else None)
        if edited:
            unchecked.append(f"its {kind} profile, {ref.name}, {edited} since: the comparison "
                             "shows what that changes")
        into.mkdir(parents=True, exist_ok=True)
        composed[kind] = into / f"{kind}.json"
        composed[kind].write_text(json.dumps(profile.composed, indent=2))
    profiles = SlicerProfiles(machine=composed["machine"], process=composed["process"],
                              filament=composed["filament"], used=used)
    return with_bed_type(profiles, bed, into)


def _edited(path: Path, ref: ProfileRef, standard: Path | None) -> str:
    if sliced.sha256_of(path) != ref.sha256:
        return "has been edited"
    if standard is not None and ref.standard_sha256 and \
            sliced.sha256_of(standard) != ref.standard_sha256:
        return "has had its standard edited"
    return ""


def _settings(first: Row) -> tuple[list[Change], list[str]]:
    """The run's settings as they reached its slice. Its pause is given at the height that
    pauses where it did, and one whose G-code doesn't say where it paused is left out, saying
    so (rule 9: never a pause height copied raw)."""
    from print_shop.desk.again import _pause_again  # noqa: PLC0415 - the desk imports the graph

    out: list[Change] = []
    notes: list[str] = []
    for c in first.proposal.changes if first.proposal is not None else ():
        if c.key == ORIENTATION or c.said_no_to:
            continue
        if c.key == PAUSE:
            height = _pause_again(first, c)
            if height is None:
                notes.append(f"{PAUSE}={c.value} is left out: that run's G-code doesn't say where "
                             f"it paused. To pause, add {PAUSE}= the top of the layer to pause "
                             "after")
                continue
            if height == "none":
                continue
            if height != c.value:
                notes.append(f"{PAUSE}={c.value} is given as {PAUSE}={height}, to pause where "
                             "that run did: a pause height is now the top of the layer it comes "
                             "after")
            c = c.model_copy(update={"value": height})
        out.append(c.model_copy(update={"not_applied": ""}))
    return out, notes


def _suggestions(ledger: Ledger, first: Row, config: dict[str, str]) -> list[Suggestion]:
    """A lesson kept or edited since the run, for its part and filament, whose setting it
    printed otherwise; and a setting asked at that run that didn't reach its slice."""
    found: list[Suggestion] = []
    proposal = first.proposal
    if first.features:
        given = {g.id: g for g in proposal.lessons} if proposal is not None else {}
        filament_type = first.slicer_settings.get("filament_type", "")
        for lesson in print_lessons.for_part(ledger, first.features, filament_type,
                                             print_lessons.variety_of(first)):
            if not lesson.key or lesson.withheld or lesson.key in PAUSE_NAMES:
                continue
            had = given.get(lesson.id)
            if _first(config.get(lesson.key, "")) == lesson.value:
                continue
            since = ("edited since that run" if had is not None and had.value != lesson.value
                     else "kept since that run" if had is None else "not set at that run")
            found.append(Suggestion(key=lesson.key, value=lesson.value,
                                    why=f"lesson {lesson.id}, {since}: {lesson.text}"))
    for c in proposal.not_applied if proposal is not None else ():
        if any(s.key == c.key for s in found) or c.key in PAUSE_NAMES:
            continue
        found.append(Suggestion(key=c.key, value=c.value, why=(
            f"asked at that run, and it didn't reach its slice ({c.not_applied}): the print "
            "again has what the slice had")))
    return found


def _first(value: str) -> str:
    return re.split(r"[;,]", value.strip())[0].strip().strip('"')


# --- what the person types -----------------------------------------------------------------------

_CHANGE = re.compile(r"^\s*([A-Za-z_][\w]*)\s*=\s*(\S+)\s*(?:because\s+(.+))?$")


def parse_changes(typed: str, reprint: Reprint) -> list[Change] | str:
    """`key=value [because why]; ...` and `take N`, as changes, or why they can't be read."""
    out: list[Change] = []
    for part in filter(None, (p.strip() for p in typed.split(";"))):
        took = re.fullmatch(r"take\s+(\d+)", part, re.I)
        if took is not None:
            n = int(took.group(1))
            if not 1 <= n <= len(reprint.suggestions):
                return f"there's no suggestion {n}"
            s = reprint.suggestions[n - 1]
            out.append(s.change(reprint.config.get(s.key, "")))
            continue
        match = _CHANGE.match(part)
        if match is None:
            return f"'{part}' isn't key=value, or take N"
        key, value, reason = match.group(1), match.group(2), (match.group(3) or "").strip()
        if key == ORIENTATION:
            return "the pose is the run's: a part in another pose is a new job"
        if key in PAUSE_NAMES and key != PAUSE:
            return (f"a swap is set at a part's review; here a pause is {PAUSE}=Z, the top of the "
                    "layer to pause after, or none")
        out.append(Change(key=key, preset=_first(reprint.config.get(key, "")), value=value,
                          reason=reason or "your change at a print again",
                          source=Source.SPONSOR))
    return out if out else "nothing to change was typed"


def screen(
    reprint: Reprint, *, slicer: Slicer, into: Callable[[], Path],
    read: Callable[[str], str], write: Callable[[str], None],
) -> sliced.Again | None:
    """The run at the terminal, before it prints again: Enter prints what's shown, a change
    slices it again and shows how it differs, and stop ends it. Returns what to send, or None
    for stop."""
    for line in reprint.lines():
        write(line)
    made: Changed | None = None
    changes: list[Change] = []
    folders: list[Path] = []  # each change's slice: those not sent are cleared

    def leaving(kept: Path | None = None) -> None:
        for folder in folders:
            if kept is None or not kept.is_relative_to(folder):
                shutil.rmtree(folder, ignore_errors=True)

    while True:
        try:
            typed = read("again> ").strip()
        except EOFError:
            leaving()
            return None
        if typed.lower() in ("stop", "q", "quit"):
            leaving()
            return None
        if typed.lower() in ("", "ok", "y", "yes"):
            if made is not None:
                leaving(made.gcode)
                return reprint.changed(made)
            try:
                as_it_was = reprint.as_it_was()
            except Refused as exc:
                write(f"  {exc}. " + ("Type stop." if reprint.no_change
                                      else "Type a change to slice it again, or stop."))
                continue
            leaving()
            return as_it_was
        if typed.lower() == "undo":
            made, changes = None, []
            write("  Back to the run as it was.")
            continue
        found = parse_changes(typed, reprint)
        if isinstance(found, str):
            write(f"  Not read: {found}.")
            continue
        asked = list(Proposal(changes=tuple(changes)).corrected(found).changes)
        folders.append(into())
        try:
            made = reprint.change(asked, folders[-1], slicer)
        except Refused as exc:
            write(f"  Not sliced: {exc}.")
            continue
        changes = asked
        for line in made.lines():
            write(line)
        write("Enter prints this; another change slices it again; undo goes back to the run as "
              "it was; stop ends it.")


def slice_folder(jobs: Path, row: Row) -> Path:
    """A new folder for a change's slice, under the jobs folder: the job takes its bytes from
    here, as it takes a hand-sliced file's."""
    folder = jobs / SLICES_DIR / f"{row.job_id[:8]}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder

