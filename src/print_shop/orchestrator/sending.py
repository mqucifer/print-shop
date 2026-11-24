"""Files sent to the queue, by Claude or the person, with one command (mqucifer/print-shop#226).

The Sponsor, 2026-11-21: "im guessing i'll just have claude send the files over to it and i'll pick
up the queue when I can". The files are all over the Mac, and the queue is picked up later, often
from a phone.

- **Several files in one go** (`send`): parts and hand-sliced files, with who sent them and a
  note, and for each a quantity and a spool (the loaded one by default), and for a part what
  it's for (`purpose`, as mqucifer/print-shop#236 reads it) and which faces are seen.
- **The queue keeps its own copy** of each file and what belongs to it, in the job's folder
  (`sent/`): the file, its notes file, its context as read now (`context.keep`: from the sidecar
  or its design run), and for G-code the Orca project it was sliced from, given or found beside
  it by its objects' names. The original can then move or change: the job prints its copy.
- **It answers in words the sender can relay** (`Sending.lines`): what was queued, what came
  with each, and what wasn't taken and why. The same bytes as a job already waiting are said and
  not queued again; the same bytes as a past print are queued, saying that print and how it came
  out.
- **What the sender suggests for its settings is kept as theirs** (`Kept.suggest`): offered at
  the review, unticked, and applied only if the person uses it (`offer.suggested_items`).

**Sending can't answer, approve, arm or start.** Nothing here takes a printer, a daemon or an
answer: a sent job waits `SENT`, in the jobs folder, until a queue is open to prepare it
(`print_queue.PrintQueue` takes it up), and the questions, the review, the arming and the Start
are the person's there. A sent file sliced by hand is looked at by the person once, as
`print_job.py --sliced` shows it, before it's ready (`PrintQueue._looked`). `scripts/send.py`
is the command.
"""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from print_shop import sliced
from print_shop.inventory import Inventory, InventoryError, Material
from print_shop.ledger import Ledger, Row
from print_shop.orchestrator.kept import Kept, Sent, Suggestion, WaitingJob, WaitingJobs, Waits
from print_shop.outcome_words import outcome_words
from print_shop.setup import bounds
from print_shop.setup.context import (
    KEPT,
    LOADS,
    SIDECAR,
    PartContext,
    keep,
    plain_line,
    read_context,
    seen_from_words,
)
from print_shop.setup.proposal import PAUSE
from print_shop.slicer.orca import SlicerProfiles

__all__ = [
    "PARTS",
    "SLICED_SUFFIXES",
    "Adding",
    "Queued",
    "Sending",
    "loaded_from",
    "material_for",
    "queue_one",
    "send",
]

#: The suffixes of a file sliced by hand: sent as it is (mqucifer/print-shop#164). An Orca
#: `.3mf` is read as one too, and refused, saying what to export, where it holds no G-code.
SLICED_SUFFIXES = (".gcode", ".gcode.3mf", ".3mf")
#: The suffixes of a part, which the shop lays, asks about and slices.
PARTS = (".stl",)
#: The folder in a job's own that holds the queue's copy of what was sent.
SENT_DIR = "sent"

ProfilesFor = Callable[[Material | None, Path], SlicerProfiles]
SlicedFor = Callable[[Path, Material | None], sliced.SlicedJob]


@dataclass(frozen=True)
class Adding:
    """A file to queue: how many prints of it, the spool it's for (None: the loaded one), and
    what its sender says of it (mqucifer/print-shop#226): what it's for and how it's loaded, which
    faces are seen, the Orca project a G-code was sliced from, and settings they suggest."""

    path: Path
    copies: int = 1
    spool_id: int | None = None
    #: What it's for in words, and how it's loaded: one of `context.LOADS`.
    purpose: str = ""
    loaded: str = ""
    #: The faces seen, as a sidecar lists them: walls, top, bottom. None: not said.
    seen: tuple[str, ...] | None = None
    project: Path | None = None
    suggested: tuple[tuple[str, str, str], ...] = ()
    #: A finished run printed again (mqucifer/print-shop#216): `path` is its kept file, or a
    #: change's new slice, read as a hand-sliced file is and linked to that run, by its name.
    again: sliced.Again | None = None


@dataclass
class Queued:
    """One file as it was queued: its first job, its copies, and what came with it, in words."""

    job_id: str
    part: str
    copies: int
    came: list[str] = field(default_factory=list)


class NotQueued(ValueError):
    """Why a file wasn't queued, in the sender's words."""


def loaded_from(words: str) -> str:
    """How a part is loaded, from a sender's words: one of `LOADS`, by its start or a word of it
    that names only it ("decoration", "handled", "load", "prototype"). Raises ValueError."""
    said = " ".join(words.casefold().split())
    if not said:
        return ""
    found = [load for load in LOADS if load.startswith(said)] or [
        load for load in LOADS if said in load.replace(",", "").split()]
    if len(found) != 1:
        raise ValueError(f"loaded is one of {'; '.join(repr(load) for load in LOADS)}: "
                         f"not {words!r}")
    return found[0]


def material_for(ledger_path: Path | None, spool_id: int | None) -> Material | None:
    """The printer, its nozzle and the spool a job is for: the loaded one by default. None where
    the stock isn't set up. Raises InventoryError, saying why."""
    if ledger_path is None:
        return None
    with Inventory(ledger_path) as inventory:
        if not inventory.printers():
            if spool_id is not None:
                raise InventoryError("the stock isn't set up, so no spool can be named")
            return None
        if spool_id is None:
            loaded = inventory.loaded(inventory.printer().id)
            if loaded is None:
                raise InventoryError("no spool is loaded: name the spool it's for")
            spool_id = loaded.id
        return inventory.for_spool(spool_id)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _day(at: str) -> str:
    try:
        when = datetime.fromisoformat(at).astimezone()
    except ValueError:
        return at
    return f"{when.day} {when:%b %Y}"


# --------------------------------------------------------------------------
# One file
# --------------------------------------------------------------------------


def queue_one(
    one: Adding, *, workdir: Path, ledger: Ledger | None, material: Material | None,
    profiles_for: ProfilesFor, sliced_for: SlicedFor | None, sent: Sent,
) -> Queued:
    """The file copied into a job's folder with what belongs to it, and the job and its copies
    written to the queue's table, waiting `SENT` for a queue to prepare them. Raises
    `NotQueued` (a ValueError), OSError or InventoryError, saying why it wasn't."""
    path = one.path.expanduser().absolute()
    if not path.is_file():
        raise NotQueued("not a file")
    if one.copies < 1:
        raise NotQueued("a quantity is 1 or more")
    low = path.name.lower()
    by_hand = low.endswith(SLICED_SUFFIXES) or one.again is not None
    if not by_hand and not low.endswith(PARTS):
        raise NotQueued("print-shop prints a part as an STL, or a file you sliced by hand "
                        "(G-code, or Orca's sliced plate file, .gcode.3mf): export it as one")
    if by_hand and sliced_for is None:
        raise NotQueued("this queue doesn't take files sliced by hand")
    jobs = WaitingJobs(workdir / "jobs.sqlite")
    sha = sliced.sha256_of(path)
    spool_id = material.spool_id if material is not None else None
    same = _waiting_with(jobs, sha)
    alike = next((j for j in same if j.spool_id == spool_id), None)
    if alike is not None and sent.by:
        # A sender can't add to the person's queue what waits there already. The person's own
        # `add` at the queue is theirs to make: it's queued, saying so.
        raise NotQueued(_already_waiting(alike))

    job_id = str(uuid.uuid4())
    folder = workdir / job_id / SENT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / path.name
    shutil.copy2(path, copy)
    came: list[str] = [
        f"the same bytes wait as job {j.job_id[:8]} ({j.part})"
        + (f" for spool #{j.spool_id}: this one is another job, for spool #{spool_id}"
           if j.spool_id != spool_id else ": this is another job of them, and `remove` takes "
           "either out") for j in same]
    profiles: SlicerProfiles | None = None
    suggestions: list[Suggestion] = []
    try:
        if by_hand:
            assert sliced_for is not None
            given, said = _sliced(one, path, copy, folder, material, sliced_for)
            came += said
        else:
            if one.project is not None:
                raise NotQueued("project= is for a file sliced by hand: a part is sliced here")
            profiles = profiles_for(material, workdir / job_id)
            given, said = _part(one, path, copy, folder, sent.by)
            came += said
            suggestions, refused = _suggestions(one, profiles, sent.by)
            came += [f"suggested, offered at the review unticked: {s.key}={s.value} ({s.reason})"
                     for s in suggestions]
            came += refused
    except BaseException:
        shutil.rmtree(workdir / job_id, ignore_errors=True)
        raise
    if ledger is not None:
        came += _printed_before(ledger, sha, given)
    if suggestions:
        Kept(workdir / job_id).suggest(suggestions)
    sent = sent.model_copy(update={"sha256": sha, "source": str(path), "came": tuple(came)})
    # A print again is named as its run is, not by its kept file (mqucifer/print-shop#216).
    part = one.again.name if one.again is not None else path.name
    jobs.add(WaitingJob(job_id=job_id, part=part, given=given, profiles=profiles,
                        waits=Waits.SENT, spool_id=spool_id, copies=one.copies, sent=sent))
    for n in range(2, one.copies + 1):
        jobs.add(WaitingJob(
            job_id=str(uuid.uuid4()), part=part, given=given, profiles=profiles,
            waits=Waits.WITH_FIRST, spool_id=spool_id, copy_of=job_id, copy_n=n,
            copies=one.copies, sent=sent))
    return Queued(job_id=job_id, part=part, copies=one.copies, came=came)


def _part(
    one: Adding, path: Path, copy: Path, folder: Path, by: str,
) -> tuple[dict[str, Any], list[str]]:
    """A part's state, read from the queue's copy, with its context as it's read now, from its
    notes beside the original or its design run, kept beside the copy."""
    context = read_context(path)
    came = [_context_came(context)]
    sidecar = path.with_name(path.stem + SIDECAR)
    if sidecar.is_file():
        shutil.copy2(sidecar, folder / sidecar.name)
    given_purpose, given_seen = _given(one, by)
    if given_purpose or given_seen:
        context, said = _with_given(context, given_purpose, given_seen, by)
        came += said
    kept = keep(context, folder / f"{copy.stem}{KEPT}", str(path))
    return ({"entry": "stl_upload", "stl_path": str(copy), "as_laid": False,
             "context_path": str(kept)}, came)


def _context_came(context: PartContext) -> str:
    if not context.found:
        why = "; ".join(context.notes)
        return ("its notes: none" + (f" ({why})" if why else ": no sidecar beside it and no "
                                     "design run above it"))
    what = [_count(len(context.fits), "fit")] if context.fits else []
    what += [_count(len(context.holds), "hold")] if context.holds else []
    if context.loaded or context.purpose:
        what.append("what it's for")
    if context.seen is not None:
        what.append("which faces are seen")
    if context.down:
        what.append(f"the side it's laid on ({context.down})")
    what += [_count(len(context.facts), "fact")] if context.facts else []
    what += [_count(len(context.findings), "finding")] if context.findings else []
    notes = "".join(f"; {n}" for n in context.notes)
    return f"its notes, from {context.source}: {', '.join(what) or 'nothing it reads'}{notes}"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _given(one: Adding, by: str) -> tuple[tuple[str, str] | None, tuple[str, ...] | None]:
    purpose = None
    if one.purpose or one.loaded:
        purpose = (plain_line(one.purpose), loaded_from(one.loaded) if one.loaded else "")
    return purpose, one.seen


def _with_given(
    context: PartContext, purpose: tuple[str, str] | None, seen: tuple[str, ...] | None, by: str,
) -> tuple[PartContext, list[str]]:
    """The context with what the sender said of the part: it stands in place of the notes',
    and is read back at the questions, as the notes are, never the person's until they say so
    (mqucifer/print-shop#236)."""
    update: dict[str, Any] = {}
    said: list[str] = []
    if purpose is not None:
        words, loaded = purpose
        was = "; ".join(w for w in (context.purpose, context.loaded) if w)
        update.update(purpose=words, loaded=loaded)
        now = "; ".join(w for w in (words, f"loaded as '{loaded}'" if loaded else "") if w)
        said.append(f"what it's for, as {by} said: {now}"
                    + (f" (in place of the notes': {was})" if was else "")
                    + ("" if loaded else ": words alone, so what it's for is still asked"))
    if seen is not None:
        update["seen"] = seen_from_words(seen, f"as {by} said")
        said.append(f"seen, as {by} said: {', '.join(seen) or 'nothing'}"
                    + (" (in place of the notes')" if context.seen is not None else ""))
    if not context.found:
        update["source"] = f"what {by} said when sending it"
    return context.model_copy(update=update), said


def _suggestions(
    one: Adding, profiles: SlicerProfiles, by: str,
) -> tuple[list[Suggestion], list[str]]:
    """The sender's suggested settings, each checked against its bound here: those inside it
    are kept to offer, and the rest are said, not taken."""
    if not one.suggested:
        return [], []
    import json  # noqa: PLC0415 - only for a send with suggestions

    presets = {
        "machine": json.loads(profiles.machine.read_text()),
        "process": json.loads(profiles.process.read_text()),
        "filament": json.loads(profiles.filament.read_text()) if profiles.filament else {},
    }
    kept, refused = [], []
    for key, value, reason in one.suggested:
        if key == PAUSE:
            why = "a pause is set at the review, where its height is read against the slice"
        elif not reason.strip():
            why = "a suggestion says why"
        else:
            why = bounds.check(key, value, presets)
        if why:
            refused.append(f"not taken: the suggestion {key}={value}: {why}")
        else:
            kept.append(Suggestion(key=key, value=value, reason=plain_line(reason), by=by))
    return kept, refused


def _sliced(
    one: Adding, path: Path, copy: Path, folder: Path, material: Material | None,
    sliced_for: SlicedFor,
) -> tuple[dict[str, Any], list[str]]:
    """A hand-sliced file read from the queue's copy, for its spool, with its objects'
    overrides: its own, or the Orca project's, given or found beside it."""
    came: list[str] = []
    if one.purpose or one.loaded or one.seen is not None or one.suggested:
        came.append("not taken: what it's for, what's seen and suggested settings: a file "
                    "sliced by hand is sent as it is, with nothing asked or proposed")
    job = sliced_for(copy, material)
    if one.again is not None:
        # A finished run printed again: what its record carries goes with it, its objects'
        # overrides included, and nothing is looked for beside the file (mqucifer/print-shop#216).
        changed = one.again.reprinted is not None and bool(one.again.reprinted.changes)
        came.append(f"a print again of job {one.again.reprint_of[:8]}, "
                    + ("with your change" if changed else "as it was"))
        return {"entry": "sliced_file", "sliced": job.again(one.again)}, came
    file = job.file
    if file.kind == "plate":
        if one.project is not None:
            came.append(f"not taken: the project {one.project.name}: the plate's sliced file "
                        "carries its objects' overrides")
        n = len(file.overrides or ())
        came.append(f"its objects' overrides: from the plate's sliced file itself ({n})")
        return {"entry": "sliced_file", "sliced": job}, came
    project, said = _project(one.project, path, file.objects)
    came += said
    if project is not None:
        kept = folder / project.name
        shutil.copy2(project, kept)
        overrides, where = sliced.project_overrides(kept, file.objects)
        job = job.model_copy(update={"file": file.model_copy(update={
            "overrides": overrides, "overrides_from": where})})
    return {"entry": "sliced_file", "sliced": job}, came


def _project(
    given: Path | None, gcode: Path, objects: tuple[str, ...],
) -> tuple[Path | None, list[str]]:
    """The Orca project a G-code was sliced from: the one given, or one found beside it whose
    objects with overrides are the G-code's, by name. Which was taken is said, and why none."""
    if given is not None:
        project = given.expanduser().absolute()
        sliced.project_overrides(project, objects)  # refused, saying why, if it isn't
        return project, [f"its objects' overrides: from the project {project.name}, as given"]
    if not objects:
        return None, ["its objects' overrides: none, as its G-code names no object"]
    looked = sorted({p for folder in (gcode.parent, gcode.parent.parent)
                     for p in folder.glob("*.3mf") if not p.name.lower().endswith(".gcode.3mf")})
    matched = []
    for candidate in looked:
        try:
            sliced.project_overrides(candidate, objects)
        except (sliced.SlicedError, OSError, ValueError):
            continue
        matched.append(candidate)
    if not matched:
        where = (f"none of {', '.join(p.name for p in looked)} names its objects"
                 if looked else "no Orca project was found beside it")
        return None, [f"its objects' overrides: none ({where}). G-code carries none of its "
                      "own: give project=P.3mf, or send the plate's sliced file (.gcode.3mf)"]
    matched.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    taken = matched[0]
    others = (f"; also matched, older: {', '.join(p.name for p in matched[1:])}"
              if matched[1:] else "")
    return taken, [f"its objects' overrides: from the project {taken.name}, found beside it by "
                   f"its objects' names ({', '.join(objects)}){others}"]


# --------------------------------------------------------------------------
# The same bytes
# --------------------------------------------------------------------------


def _waiting_with(jobs: WaitingJobs, sha: str) -> list[WaitingJob]:
    """The jobs waiting with the same bytes, each its first copy."""
    return [j for j in jobs.all() if not j.copy_of and j.sent is not None
            and j.sent.sha256 == sha]


def _already_waiting(job: WaitingJob) -> str:
    sent = job.sent
    who = f", {sent.said()}" if sent is not None and sent.by else ""
    copies = f", {job.copies} prints of it" if job.copies > 1 else ""
    spool = f" for spool #{job.spool_id}" if job.spool_id is not None else ""
    return (f"the same bytes wait in the queue already{spool}, as job {job.job_id[:8]} "
            f"({job.part}{copies}{who}; {job.waits}), so it isn't queued twice. Give xN for more "
            "prints of one file, or remove that job first")


def _printed_before(ledger: Ledger, sha: str, given: dict[str, Any]) -> list[str]:
    """The past prints of the same bytes, the latest said with how it came out."""
    job = given.get("sliced")
    gcode = job.file.sha256 if isinstance(job, sliced.SlicedJob) else ""
    rows = [r for r in ledger.rows() if r.stl_sha256 == sha or (
        r.sliced is not None and sha in (r.sliced.gcode_sha256, r.sliced.container_sha256))
        or (gcode and r.sliced is not None and r.sliced.gcode_sha256 == gcode)]
    if not rows:
        return []
    last = rows[-1]
    more = f" (and {len(rows) - 1} before it)" if len(rows) > 1 else ""
    return [f"the same bytes printed before: {last.stl_name}, job {last.job_id[:8]}, "
            f"{_day(last.start_requested_at)}: {_came_out(last)}{more}"]


def _came_out(row: Row) -> str:
    if row.result is not None:
        return outcome_words(str(row.result), row.symptom)
    return "no outcome recorded yet" if row.started_at else "it may not have started"


# --------------------------------------------------------------------------
# Several, in one go
# --------------------------------------------------------------------------


@dataclass
class Sending:
    """What one send did: each file queued with what came with it, and those that weren't."""

    by: str
    note: str
    queued: list[Queued] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The answer, in words the sender can relay."""
        n = len(self.queued)
        out = [f"Queued {n} file{'s' if n != 1 else ''} for the person, as sent by {self.by}"
               + (f", with the note '{self.note}'" if self.note else "") + "." if n
               else "Nothing was queued."]
        for q in self.queued:
            copies = f", {q.copies} prints" if q.copies > 1 else ""
            out.append(f"- {q.part}{copies}: job {q.job_id[:8]}")
            out += [f"    {line}" for line in q.came]
        out += [f"- not queued: {why}" for why in self.refused]
        if n:
            out.append("They wait for the person: the questions, the review, the Start and the "
                       "print are theirs, at `print_job.py --queue`. Nothing was prepared, "
                       "approved, armed or started by sending.")
        return out


def send(
    adding: Sequence[Adding], *, by: str, note: str, workdir: Path, ledger_path: Path | None,
    profiles_for: ProfilesFor, sliced_for: SlicedFor | None,
) -> Sending:
    """Several files queued in one go, with who sent them and a note: each copied with what
    belongs to it, and waiting `SENT` for a queue to prepare it. Nothing is prepared, answered,
    approved, armed or started."""
    by, note = plain_line(by) or "you", plain_line(note)
    done = Sending(by=by, note=note)
    sent = Sent(by=by, note=note, at=_now(), batch=str(uuid.uuid4()))
    ledger = Ledger(ledger_path) if ledger_path is not None and ledger_path.is_file() else None
    try:
        for one in adding:
            try:
                material = material_for(ledger_path, one.spool_id)
                done.queued.append(queue_one(
                    one, workdir=workdir, ledger=ledger, material=material,
                    profiles_for=profiles_for, sliced_for=sliced_for, sent=sent))
            except (OSError, ValueError, InventoryError) as exc:
                done.refused.append(f"{one.path.name}: {exc}")
    finally:
        if ledger is not None:
            ledger.close()
    return done
