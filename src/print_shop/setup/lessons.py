"""Print lessons: what a print taught, checked by a reviewer, and given to a later, similar part.

mqucifer/print-shop#106, M19. Every print saves its answers, proposal, corrections and outcome to
the ledger, and before this nothing read them back: on the first real prints the same correction
was typed at the review on every job. So, the way the CAD loop learns (`rounds/lessons.py`, with
its statuses, steps, scores and signatures, imported from there, not a second vocabulary):

- **Each correction at the review, and each outcome with something to say, is a candidate**
  (`draw`), with the job as its evidence. It's keyed to the part's traits (`traits.py`): long
  and thin, flat and wide, its overhangs, its bed contact. Not the material (the Sponsor,
  2026-11-13). The same correction on a later job is more evidence for the lesson it repeats, not a
  second lesson.
- **A reviewer checks each** (`accept`, `edit`, `reject`), signed `reviewer: <who>`, as CAD lessons
  are: nothing reaches a proposal without it. A lesson keyed to no trait would reach every part, so
  it's accepted only once it's keyed to some.
- **A kept lesson reaches a later part that has every trait it's keyed to** (`for_part`): it sets
  its setting, as an intake answer does, and the model reads it with where it came from. The
  review shows which lesson set what, and the ledger has a row of `job_lessons` for each given.
- **Each is scored at the review by what the Sponsor did:** a setting that stood is `used`, one
  they corrected `in_the_way`, and one another lesson or their answer set `not_used`. A lesson only
  read by the model is scored by the reviewer (`score`). Its status follows each job's latest score
  (`settle`), as the CAD loop's does.
- **One used on two prints that didn't fail, without the Sponsor correcting it, is listed to
  promote:** a line in the print-settings skill, by PR, recorded with `promote --as skill`. Once in
  the skill it isn't given as a lesson. One in the way is listed to retire; code retires nothing.

Every change is a row of `print_lesson_steps`, with who and why, and an edit keeps the words it
replaced. The tables live in the print ledger, created if missing (`ledger.Ledger`), so old rows
read back as they were. `scripts/print_lessons.py` is the reviewer's tool.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from print_shop.rounds.lessons import (
    ACCEPTED,
    AFTER_SCORE,
    CANDIDATE,
    EDITED,
    GIVEN,
    HARNESS,
    KEPT,
    PROMOTED,
    REJECTED,
    RETIRED,
    REVIEWER,
    SCORED,
    SCORES,
    UNUSED_TO_RETIRE,
    Refused,
    signed,
)
from print_shop.rounds.lessons import writer as _writer
from print_shop.setup.bounds import MATERIAL
from print_shop.setup.features import Feature
from print_shop.setup.proposal import PAUSE, GivenLesson, Source
from print_shop.setup.traits import TRAITS, traits

if TYPE_CHECKING:  # the ledger opens these tables, so it imports this
    from print_shop.ledger import Ledger, Row

__all__ = [
    "ANY",
    "REVIEWER",
    "SCHEMA",
    "SCORES",
    "Refused",
    "accept",
    "after_print",
    "draw",
    "edit",
    "for_part",
    "listing",
    "one",
    "promote",
    "reject",
    "retire",
    "score",
    "settle",
    "to_promote",
    "to_retire",
    "upgrade",
    "write",
]

#: A lesson for a setting the filament decides, widened by a reviewer to every filament.
ANY = "any"
#: Used on this many prints that didn't fail, without the Sponsor correcting it: a skill line.
USED_TO_PROMOTE = 2
#: What a print lesson becomes when it's delivered: a line in the print-settings skill.
BECOMES = ("skill",)
#: How a lesson was drawn from a print: a correction, an outcome, a change the Sponsor took from
#: the slice's reading (mqucifer/print-shop#105), or a reviewer by hand.
CORRECTION = "correction"
OUTCOME = "outcome"
READING = "reading"
BY_HAND = "by hand"
#: Where a delivery is: `owner/repo#n`, or a path.
_WHERE = re.compile(r"^([\w.-]+/[\w.-]+#\d+|[\w./-]+)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS print_lessons (
    -- What a print taught, for a later part with the same traits: drawn from a correction at the
    -- review or an outcome, or written by a reviewer, and checked before any reaches a proposal.
    id INTEGER PRIMARY KEY,
    -- The print it was first learned on; `print_lesson_evidence` has every print that shows it.
    job_id TEXT NOT NULL REFERENCES prints(job_id),
    drawn_from TEXT NOT NULL,                 -- correction, outcome, reading or by hand
    written_by TEXT NOT NULL,
    written_at TEXT NOT NULL,
    text TEXT NOT NULL,
    -- The setting it sets, and to what; NULL for a lesson only read by the model.
    setting TEXT,
    value TEXT,
    -- JSON [trait]: what it's keyed to (`traits.TRAITS`). A later part has every one.
    traits TEXT NOT NULL,
    -- For a setting the filament decides (`bounds.MATERIAL`): the filament type it holds for, as
    -- the print it was learned on was sliced (`filament_type`), or 'any' once a reviewer widens
    -- it. NULL is no such setting, or not known, and then it isn't given (mqucifer/print-shop#117).
    material TEXT,
    -- candidate; accepted, edited or rejected; given; held or broken; promoted or retired.
    status TEXT NOT NULL,
    status_by TEXT,
    status_at TEXT,
    -- What it became once delivered (a skill line), and where (`owner/repo#n`).
    became TEXT,
    became_where TEXT
);
CREATE TABLE IF NOT EXISTS print_lesson_steps (
    -- Each change to a lesson, who made it and why: an edit keeps the words it replaced.
    id INTEGER PRIMARY KEY,
    lesson INTEGER NOT NULL REFERENCES print_lessons(id),
    status TEXT NOT NULL,
    by TEXT NOT NULL,
    why TEXT,
    was TEXT,
    job_id TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS print_lesson_evidence (
    -- Each print that shows a lesson: the correction or outcome it was drawn from, or one seen
    -- again on a later print. `key` is the setting, or '' for an outcome.
    lesson INTEGER NOT NULL REFERENCES print_lessons(id),
    job_id TEXT NOT NULL REFERENCES prints(job_id),
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    detail TEXT NOT NULL,                     -- JSON: what the print said, as it said it
    at TEXT NOT NULL,
    PRIMARY KEY (lesson, job_id, kind, key)
);
CREATE TABLE IF NOT EXISTS job_lessons (
    -- Each lesson a print's proposal was given, whether it set its setting, and its words.
    job_id TEXT NOT NULL REFERENCES prints(job_id),
    lesson INTEGER NOT NULL REFERENCES print_lessons(id),
    given_at TEXT NOT NULL,
    applied INTEGER NOT NULL CHECK (applied IN (0, 1)),
    why TEXT,
    text TEXT NOT NULL,
    PRIMARY KEY (job_id, lesson)
);
CREATE TABLE IF NOT EXISTS print_lesson_scores (
    -- Whether a lesson held on a print it was given to: used, not_used or in_the_way. The review
    -- scores a setting by what the Sponsor did with it; a reviewer may score again.
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES prints(job_id),
    lesson INTEGER NOT NULL REFERENCES print_lessons(id),
    score TEXT NOT NULL,
    why TEXT,
    by TEXT NOT NULL,
    at TEXT NOT NULL
);
"""

#: Who scores a lesson from what the Sponsor did at the review: the code reading it.
AT_REVIEW = HARNESS


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def one(ledger: Ledger, lesson: int) -> dict[str, Any]:
    found = ledger.query("SELECT * FROM print_lessons WHERE id=?", lesson)
    if not found:
        raise Refused(f"there's no print lesson {lesson}")
    row = found[0]
    row["traits"] = json.loads(row["traits"])
    return row


def _step(
    ledger: Ledger, lesson: int, status: str, by: str, why: str | None = None,
    was: str | None = None, job_id: str | None = None,
) -> None:
    with ledger.writing() as db:
        _step_in(db, lesson, status, by, why, was, job_id)


def _step_in(
    db: sqlite3.Connection, lesson: int, status: str, by: str, why: str | None = None,
    was: str | None = None, job_id: str | None = None,
) -> None:
    db.execute(
        "INSERT INTO print_lesson_steps (lesson, status, by, why, was, job_id, at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (lesson, status, by, why, was, job_id, _now()),
    )


def _status(ledger: Ledger, lesson: int, status: str, by: str) -> None:
    with ledger.writing() as db:
        db.execute(
            "UPDATE print_lessons SET status=?, status_by=?, status_at=? WHERE id=?",
            (status, by, _now(), lesson),
        )


def _check_traits(given: Iterable[str]) -> list[str]:
    chosen = [t.strip() for t in given if t.strip()]
    unknown = [t for t in chosen if t not in TRAITS]
    if unknown:
        raise Refused(f"a trait is one of {', '.join(TRAITS)}: not {', '.join(unknown)}")
    return [t for t in TRAITS if t in chosen]


# Drawing candidates from a print's row


def after_print(ledger: Ledger, job_id: str) -> None:
    """Everything a print's row teaches, each part done once however often it's read: the
    lessons it was given, their scores from the review, and candidates from its corrections and
    its outcome. The ledger calls it when the row is written and when it's answered."""
    row = ledger.get(job_id)
    if row is None:
        raise Refused(f"job {job_id} isn't in the ledger")
    _given(ledger, row)
    _scored_at_review(ledger, row)
    draw(ledger, row)


def _part(row: Row) -> dict[str, Any]:
    """What a print says of its part, for a lesson's evidence: its shape, and what its slice
    showed and flagged (mqucifer/print-shop#105), where it was read."""
    features = row.features or ()
    part: dict[str, Any] = {
        "stl_name": row.stl_name,
        "filament": row.filament,
        "traits": list(traits(features)),
        "features": {f.id: f.value for f in features},
        "flags": [{"id": f.id, "reading": f.reading} for f in row.flags or ()],
    }
    if row.reading is not None:
        shortest = row.reading.shortest()
        ironing = row.reading.feature("Ironing")
        part["slice"] = {
            "shortest_layer_s": shortest.time_s if shortest else None,
            "ironing_s": ironing.time_s if ironing else 0.0,
            "seam_stacked": row.reading.seam.stacked,
        }
    return part


def material_of(settings: dict[str, Any]) -> str | None:
    """A print's filament type, as it was sliced (`filament_type`), or None if it doesn't say."""
    found = str(settings.get("filament_type") or "").strip().upper()
    return found or None


def upgrade(db: sqlite3.Connection) -> list[int]:
    """A ledger from before mqucifer/print-shop#117: `print_lessons.material` added if missing,
    and each lesson for a setting the filament decides given its print's filament type, read
    from that print's own record, with a step saying so. Nothing is guessed: a print that doesn't
    say leaves it unknown, and it isn't given. The lessons given a material, by id."""
    held = {r[1] for r in db.execute("PRAGMA table_info(print_lessons)")}
    if "material" not in held:
        db.execute("ALTER TABLE print_lessons ADD COLUMN material TEXT")
    filled = []
    for lesson, setting, settings in db.execute(
        "SELECT l.id, l.setting, p.slicer_settings FROM print_lessons l JOIN prints p"
        " ON p.job_id = l.job_id WHERE l.material IS NULL AND l.setting IS NOT NULL ORDER BY l.id"
    ).fetchall():
        if setting not in MATERIAL:
            continue
        found = material_of(json.loads(settings or "{}"))
        if found is None:
            continue
        db.execute("UPDATE print_lessons SET material=? WHERE id=?", (found, lesson))
        _step_in(db, lesson, str(db.execute(
            "SELECT status FROM print_lessons WHERE id=?", (lesson,)).fetchone()[0]),
            HARNESS, f"its material, {found}, read from the print it was learned on")
        filled.append(int(lesson))
    return filled


def draw(ledger: Ledger, row: Row) -> list[int]:
    """Candidates from a print's corrections and outcome, and the new ones' ids. A correction
    a live lesson already makes is more evidence for it. Each is drawn once."""
    made: list[int] = []
    last: dict[str, tuple[str, Any]] = {}
    for c in row.corrections or ():
        if c.key == PAUSE:
            # A colour change is a choice for that one part, not a lesson: it stays in the
            # job's corrections and its reading (mqucifer/print-shop#119, the operator).
            continue
        last[c.key] = (CORRECTION, c)  # corrected twice at one review: the one that stood
    for c in row.proposal.changes if row.proposal else ():
        if c.source is Source.READING and c.key not in last:
            last[c.key] = (READING, c)  # taken from the slice's reading, its own kind
    part = _part(row)
    for key, (kind, c) in last.items():
        if _drawn(ledger, row.job_id, kind, key):
            continue
        detail = {**part, "key": key, "preset": c.preset, "value": c.value, "reason": c.reason}
        if kind == READING:
            detail["reading"] = c.reading
        made_of = material_of(row.slicer_settings) if key in MATERIAL else None
        same = [
            found for found in ledger.query(
                "SELECT id, traits, material FROM print_lessons WHERE setting=? AND value=? AND"
                " status NOT IN (?, ?) ORDER BY id",
                key, c.value, REJECTED, RETIRED,
            )
            # A filament's setting is the same lesson only for the same filament.
            if key not in MATERIAL or found["material"] in (made_of, ANY)
        ]
        if same:
            lesson = int(same[0]["id"])
            keyed = json.loads(same[0]["traits"])
            fits = set(keyed) <= set(part["traits"]) and bool(keyed)
            said = f"the same {kind} again on {row.stl_name}" + (
                "" if fits else ", whose part isn't every trait it's keyed to"
            )
            with ledger.writing() as db:
                _evidence(db, lesson, row.job_id, kind, key, detail)
                _step_in(db, lesson, one_in(db, lesson), HARNESS, said, job_id=row.job_id)
            continue
        text = c.reason or ("the Sponsor's correction" if kind == CORRECTION else c.reading)
        made.append(_new(
            ledger, row.job_id, kind, HARNESS, text, part["traits"], (kind, key, detail), key,
            c.value, made_of,
        ))
    said = (row.note or "").strip()
    if row.result is not None and (row.result == "failed" or said) and not _drawn(
        ledger, row.job_id, OUTCOME, ""
    ):
        text = said or f"it failed: {row.symptom}"
        detail = {**part, "result": str(row.result), "symptom": str(row.symptom), "note": said}
        if row.symptom is not None and row.symptom != "none":
            # Where a symptom was recorded, the camera's frames show where it began
            # (mqucifer/print-shop#110): each with its layer, for the reviewer to page through.
            detail["frames"] = [
                {"kind": f.kind, "layer": f.layer, "total_layers": f.total_layers,
                 "path": f.path}
                for f in ledger.frames(row.job_id)
            ]
        made.append(_new(
            ledger, row.job_id, OUTCOME, HARNESS, text, part["traits"], (OUTCOME, "", detail)
        ))
    return made


def _drawn(ledger: Ledger, job_id: str, kind: str, key: str) -> bool:
    return bool(ledger.query(
        "SELECT 1 FROM print_lesson_evidence WHERE job_id=? AND kind=? AND key=?",
        job_id, kind, key,
    ))


def _evidence(
    db: sqlite3.Connection, lesson: int, job_id: str, kind: str, key: str,
    detail: dict[str, Any],
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO print_lesson_evidence (lesson, job_id, kind, key, detail, at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (lesson, job_id, kind, key, _json(detail), _now()),
    )


def one_in(db: sqlite3.Connection, lesson: int) -> str:
    """A lesson's status, read inside a transaction."""
    return str(db.execute("SELECT status FROM print_lessons WHERE id=?", (lesson,)).fetchone()[0])


def _new(
    ledger: Ledger, job_id: str, drawn_from: str, by: str, text: str, keyed: list[str],
    evidence: tuple[str, str, dict[str, Any]] | None = None,
    setting: str | None = None, value: str | None = None, material: str | None = None,
) -> int:
    """A candidate, with its evidence and its step, in one transaction: a lesson is never left
    without the print it was drawn from, so a draw again never makes it twice."""
    sets = f"{setting}={value}" if setting else "no setting"
    if material:
        sets += f", in {material}"
    with ledger.writing() as db:
        cursor = db.execute(
            "INSERT INTO print_lessons (job_id, drawn_from, written_by, written_at, text,"
            " setting, value, traits, material, status, status_by, status_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, drawn_from, by, _now(), text.strip(), setting, value, _json(keyed),
             material, CANDIDATE, by, _now()),
        )
        lesson = int(cursor.lastrowid or 0)
        if evidence is not None:
            kind, key, detail = evidence
            _evidence(db, lesson, job_id, kind, key, detail)
        _step_in(db, lesson, CANDIDATE, by, f"from {drawn_from}: {sets}", job_id=job_id)
    return lesson


def write(
    ledger: Ledger, job_id: str, by: str, text: str, keyed: Iterable[str],
    setting: str | None = None,
) -> int:
    """A lesson a reviewer writes by hand, from a print it's about: a candidate, like any."""
    by = signed(by)
    printed = ledger.get(job_id)
    if printed is None:
        raise Refused(f"job {job_id} isn't in the ledger")
    if not text.strip():
        raise Refused("a lesson needs its text")
    key, value = _setting(setting)
    made_of = material_of(printed.slicer_settings) if key in MATERIAL else None
    return _new(ledger, job_id, BY_HAND, by, text, _check_traits(keyed), None, key, value, made_of)


def _setting(given: str | None) -> tuple[str | None, str | None]:
    """`key=value`, or nothing for a lesson only read."""
    if not given or given.strip() in ("", "none"):
        return None, None
    key, sep, value = given.partition("=")
    if not sep or not re.fullmatch(r"[a-z_0-9]+", key.strip()) or not value.strip():
        raise Refused(f"a setting is key=value, or none: not {given!r}")
    return key.strip(), value.strip()


# The reviewer's steps


def accept(ledger: Ledger, lesson: int, by: str) -> None:
    """Let through as it stands. Keyed to no trait it would reach every part, so it's refused
    until it's keyed to some (`edit --traits`)."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (CANDIDATE, REJECTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a candidate is accepted")
    if not found["traits"]:
        raise Refused(
            f"lesson {lesson} is keyed to no trait, so it would reach every part: key it to the "
            f"traits it's about first (`edit {lesson} --traits ...`: {', '.join(TRAITS)})"
        )
    _status(ledger, lesson, ACCEPTED, by)
    _step(ledger, lesson, ACCEPTED, by, f"for a part that's {', '.join(found['traits'])}")


def edit(
    ledger: Ledger, lesson: int, by: str, *, text: str | None = None,
    setting: str | None = None, keyed: Iterable[str] | None = None,
    material: str | None = None, why: str = "",
) -> None:
    """The reviewer's own words, setting, traits or material, kept beside the ones they replace.
    A candidate edited is let through as edited, once it's keyed to a trait. `setting` "none"
    makes it a lesson only read. `material` "any" widens a filament's setting to every
    filament; a type names the one it holds for."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] in (REJECTED, RETIRED):
        raise Refused(f"lesson {lesson} is {found['status']}")
    if text is None and setting is None and keyed is None and material is None:
        raise Refused("an edit changes the text, the setting, the traits or the material")
    key, value = (
        _setting(setting) if setting is not None else (found["setting"], found["value"])
    )
    chosen = _check_traits(keyed) if keyed is not None else found["traits"]
    made_of = found["material"]
    if material is not None:
        if key not in MATERIAL:
            raise Refused(
                f"{key or 'a lesson only read'} goes by the part's shape alone: a material "
                f"applies to the filament's temperatures and fans ({', '.join(sorted(MATERIAL))})"
            )
        made_of = ANY if material.strip().lower() == ANY else material.strip().upper()
        if not made_of:
            raise Refused('a material is a filament type, such as PETG, or "any"')
    elif key not in MATERIAL:
        made_of = None
    elif made_of is None:
        printed = ledger.get(found["job_id"])
        made_of = material_of(printed.slicer_settings) if printed else None
    was = {k: found[k] for k in ("text", "setting", "value", "traits", "material")}
    lets_through = found["status"] in (CANDIDATE, ACCEPTED, EDITED)
    if lets_through and not chosen:
        raise Refused(
            f"lesson {lesson} would be keyed to no trait and reach every part: give --traits"
        )
    with ledger.writing() as db:
        db.execute(
            "UPDATE print_lessons SET text=?, setting=?, value=?, traits=?, material=? WHERE id=?",
            ((text or found["text"]).strip(), key, value, _json(chosen), made_of, lesson),
        )
    if lets_through:
        _status(ledger, lesson, EDITED, by)
    _step(ledger, lesson, EDITED, by, why.strip() or None, was=_json(was))


def reject(ledger: Ledger, lesson: int, by: str, why: str) -> None:
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (CANDIDATE, ACCEPTED, EDITED):
        raise Refused(f"lesson {lesson} is {found['status']}: it's retired, not rejected, now")
    if not why.strip():
        raise Refused("a rejection says why: it's what the next review learns from")
    _status(ledger, lesson, REJECTED, by)
    _step(ledger, lesson, REJECTED, by, why.strip())


def retire(ledger: Ledger, lesson: int, by: str, why: str) -> None:
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (*KEPT, PROMOTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a kept lesson is retired")
    if not why.strip():
        raise Refused("retiring one says why")
    _status(ledger, lesson, RETIRED, by)
    _step(ledger, lesson, RETIRED, by, why.strip())


def promote(ledger: Ledger, lesson: int, by: str, became: str, where: str) -> None:
    """Delivered as a line in the print-settings skill, by the PR that adds it. It's read there
    from then on, and no longer given as a lesson."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] not in (*KEPT, PROMOTED):
        raise Refused(f"lesson {lesson} is {found['status']}: only a kept lesson is promoted")
    if became not in BECOMES:
        raise Refused(f"a print lesson becomes {', '.join(BECOMES)}: not {became!r}")
    if not _WHERE.match(where.strip()):
        raise Refused("say where it is now: `owner/repo#n`, or a path")
    with ledger.writing() as db:
        db.execute(
            "UPDATE print_lessons SET became=?, became_where=? WHERE id=?",
            (became, where.strip(), lesson),
        )
    _status(ledger, lesson, PROMOTED, by)
    _step(ledger, lesson, PROMOTED, by, f"became {became}: {where.strip()}")


# Giving them to a later part, and scoring them


def reviewed_by(ledger: Ledger, lesson: int) -> str:
    """Who let a lesson through: the latest accept or edit."""
    found = ledger.query(
        "SELECT by FROM print_lesson_steps WHERE lesson=? AND status IN (?, ?) ORDER BY id DESC",
        lesson, ACCEPTED, EDITED,
    )
    return _writer(found[0]["by"]) if found else "a reviewer"


def for_part(
    ledger: Ledger, features: tuple[Feature, ...], filament_type: str = ""
) -> tuple[GivenLesson, ...]:
    """Each kept lesson keyed to traits this part has every one of, oldest first. One in the
    skill is read there, not given. One for a setting the filament decides holds only for its
    own filament type (mqucifer/print-shop#117): for another, or when either isn't known, it's
    listed as withheld, with why, and sets nothing."""
    has = set(traits(features))
    this = filament_type.strip().upper()
    given = []
    for row in ledger.query(
        "SELECT l.*, p.stl_name FROM print_lessons l JOIN prints p ON p.job_id = l.job_id"
        f" WHERE l.status IN ({','.join('?' * len(KEPT))}, ?)"  # noqa: S608
        " AND COALESCE(l.became, '') != 'skill' ORDER BY l.id",
        *KEPT, PROMOTED,
    ):
        keyed = json.loads(row["traits"])
        if not keyed or not set(keyed) <= has:
            continue
        made_of = row["material"] or ""
        withheld = ""
        if row["setting"] in MATERIAL and made_of != ANY:
            if not made_of:
                withheld = "its filament isn't known: the print it was learned on doesn't say"
            elif made_of != this:
                withheld = (
                    f"learned on {made_of}; this print is {this or 'of a filament not known'}"
                )
        given.append(GivenLesson(
            id=row["id"], text=row["text"], key=row["setting"] or "", value=row["value"] or "",
            job_id=row["job_id"], stl_name=row["stl_name"], traits=tuple(keyed),
            reviewed_by=reviewed_by(ledger, int(row["id"])),
            material=made_of if row["setting"] in MATERIAL else "", withheld=withheld,
        ))
    return tuple(given)


def _given(ledger: Ledger, row: Row) -> None:
    """A row of `job_lessons` for each lesson the print's proposal was given, and each given the
    first time is `given`."""
    for g in row.proposal.lessons if row.proposal else ():
        if g.withheld:
            continue  # its shape matched, its filament didn't: never given
        if ledger.query("SELECT 1 FROM job_lessons WHERE job_id=? AND lesson=?", row.job_id, g.id):
            continue
        with ledger.writing() as db:
            db.execute(
                "INSERT INTO job_lessons (job_id, lesson, given_at, applied, why, text)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (row.job_id, g.id, _now(), int(g.applied), g.why or None, g.line()),
            )
        if one(ledger, g.id)["status"] in (ACCEPTED, EDITED):
            _status(ledger, g.id, GIVEN, HARNESS)
        said = "set its setting" if g.applied else (g.why or "given")
        _step(ledger, g.id, GIVEN, HARNESS, f"to {row.stl_name}: {said}", job_id=row.job_id)


def _scored_at_review(ledger: Ledger, row: Row) -> None:
    """Each setting a lesson set here, by what the Sponsor did with it at the review: stood is
    `used`, corrected `in_the_way`. One that set nothing is `not_used`. Once for each print."""
    corrected = {c.key: c for c in row.corrections or ()}
    stood = {c.key: c for c in row.proposal.changes} if row.proposal else {}
    for g in row.proposal.lessons if row.proposal else ():
        if not g.key or g.withheld or ledger.query(
            "SELECT 1 FROM print_lesson_scores WHERE job_id=? AND lesson=? AND by=?",
            row.job_id, g.id, AT_REVIEW,
        ):
            continue
        if not g.applied:
            scored, why = "not_used", g.why
        elif g.key in corrected:
            c = corrected[g.key]
            scored, why = "in_the_way", f"corrected at the review to {c.value}: {c.reason}"
        elif g.key in stood and stood[g.key].source is Source.READING:
            c = stood[g.key]
            scored, why = "in_the_way", f"the slice's reading was taken instead: {c.value}"
        else:
            scored, why = "used", f"{g.key}={g.value} stood at the review"
        _score(ledger, row.job_id, g.id, scored, AT_REVIEW, why)


def score(ledger: Ledger, job_id: str, lesson: int, scored: str, by: str, why: str = "") -> None:
    """A reviewer's score of a lesson a print was given: one only read, or the review's
    corrected. The latest for each print counts."""
    by = signed(by)
    if not ledger.query("SELECT 1 FROM job_lessons WHERE job_id=? AND lesson=?", job_id, lesson):
        raise Refused(f"job {job_id} wasn't given lesson {lesson}")
    _score(ledger, job_id, lesson, scored, by, why)


def _score(ledger: Ledger, job_id: str, lesson: int, scored: str, by: str, why: str) -> None:
    if scored not in SCORES:
        raise Refused(f"a score is {', '.join(SCORES)}: not {scored!r}")
    with ledger.writing() as db:
        db.execute(
            "INSERT INTO print_lesson_scores (job_id, lesson, score, why, by, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, lesson, scored, why.strip() or None, by, _now()),
        )
    settle(ledger, lesson, by)


def latest_scores(ledger: Ledger, lesson: int) -> dict[str, dict[str, Any]]:
    """Each print's latest score of a lesson, by job, oldest first."""
    found: dict[str, dict[str, Any]] = {}
    for row in ledger.query(
        "SELECT * FROM print_lesson_scores WHERE lesson=? ORDER BY id", lesson
    ):
        found.pop(row["job_id"], None)
        found[row["job_id"]] = row
    return found


def settle(ledger: Ledger, lesson: int, by: str) -> str:
    """A scored lesson's status from each print's latest score, as the CAD loop's: the newest
    that says anything, used as `held` and in the way as `broken`. Each change is a step."""
    found = one(ledger, lesson)
    if found["status"] not in SCORED:
        return str(found["status"])
    moved, said = (PROMOTED if found["became"] else GIVEN), "no score says whether it holds"
    for row in latest_scores(ledger, lesson).values():
        if row["score"] in AFTER_SCORE:
            moved = AFTER_SCORE[row["score"]]
            said = f"scored {row['score']} on job {row['job_id']}"
    if moved != found["status"]:
        _status(ledger, lesson, moved, by)
        _step(ledger, lesson, moved, by, f"each print's latest score: {said}")
    return moved


def to_promote(ledger: Ledger) -> list[tuple[dict[str, Any], list[str]]]:
    """Each kept lesson used on `USED_TO_PROMOTE` prints that didn't fail, without the Sponsor
    correcting it there: a line in the print-settings skill. With those prints' parts."""
    shown = []
    for row in ledger.query(
        f"SELECT * FROM print_lessons WHERE status IN ({','.join('?' * len(KEPT))})"  # noqa: S608
        " ORDER BY id",
        *KEPT,
    ):
        good = []
        for job, s in latest_scores(ledger, int(row["id"])).items():
            printed = ledger.get(job)
            if s["score"] == "used" and printed is not None and printed.result != "failed":
                good.append(printed.stl_name + (f" ({printed.result})" if printed.result else ""))
        if len(good) >= USED_TO_PROMOTE:
            shown.append((row, good))
    return shown


def to_retire(ledger: Ledger) -> list[tuple[dict[str, Any], str]]:
    """Each live lesson that got in the way, or was given twice and not used, and why."""
    shown = []
    for row in ledger.query(
        f"SELECT * FROM print_lessons WHERE status IN ({','.join('?' * len(SCORED))})"  # noqa: S608
        " ORDER BY id",
        *SCORED,
    ):
        scores = latest_scores(ledger, int(row["id"])).values()
        in_the_way = sorted(s["job_id"] for s in scores if s["score"] == "in_the_way")
        unused = sorted(s["job_id"] for s in scores if s["score"] == "not_used")
        if in_the_way:
            shown.append((row, f"got in the way on {', '.join(in_the_way)}"))
        elif len(unused) >= UNUSED_TO_RETIRE:
            shown.append((row, f"given and not used on {', '.join(unused)}"))
    return shown


# What the reviewer reads


def _line(row: dict[str, Any]) -> str:
    keyed = json.loads(row["traits"]) if isinstance(row["traits"], str) else row["traits"]
    sets = f"{row['setting']}={row['value']}" if row["setting"] else "read only"
    if row["setting"] in MATERIAL:
        made_of = row.get("material")
        sets += (
            " in any filament" if made_of == ANY else f" in {made_of}" if made_of
            else " in a filament NOT KNOWN (not given until a reviewer names it)"
        )
    where = f" -> {row['became']}: {row['became_where']}" if row["became"] else ""
    return (
        f"- {row['id']}. [{row['status']}{where}] {sets}, for a part that's "
        f"{', '.join(keyed) or 'NOT KEYED (no features kept for its print)'}: {row['text'][:200]}"
    )


def _evidence_lines(ledger: Ledger, lesson: int) -> list[str]:
    lines = []
    for e in ledger.query(
        "SELECT e.*, p.stl_name, p.result, p.symptom FROM print_lesson_evidence e"
        " JOIN prints p ON p.job_id = e.job_id WHERE e.lesson=? ORDER BY e.at, e.job_id",
        lesson,
    ):
        d = json.loads(e["detail"])
        part = f"{e['stl_name']} ({', '.join(d.get('traits') or []) or 'no traits kept'})"
        flagged = ", ".join(f["id"] for f in d.get("flags") or [])
        outcome = f"{e['result']}, {e['symptom']}" if e["result"] else "no outcome yet"
        if e["kind"] == CORRECTION:
            said = f"corrected {d['key']} {d['preset']} -> {d['value']}: {d['reason']}"
        elif e["kind"] == READING:
            said = f"took {d['key']} {d['preset']} -> {d['value']} from the slice: {d['reading']}"
        else:
            said = f"{d['result']}, {d['symptom']}: {d['note']}" + _frames_said(d)
        lines.append(
            f"    - job {e['job_id']}, {part}, {said} [print: {outcome}"
            + (f"; the slice flagged {flagged}" if flagged else "") + "]"
        )
    for g in ledger.query(
        "SELECT j.*, p.stl_name, p.result, p.symptom FROM job_lessons j"
        " JOIN prints p ON p.job_id = j.job_id"
        " WHERE j.lesson=? ORDER BY j.given_at",
        lesson,
    ):
        s = latest_scores(ledger, lesson).get(g["job_id"])
        scored = f"{s['score']} ({s['why']})" if s else "not scored"
        outcome = f"{g['result']}, {g['symptom']}" if g["result"] else "no outcome yet"
        lines.append(
            f"    - given to {g['stl_name']}, job {g['job_id']}: {scored} [print: {outcome}]"
        )
    return lines


def _frames_said(detail: dict[str, Any]) -> str:
    """The frames an outcome's evidence holds, in a phrase: how many, their layers, where."""
    frames = detail.get("frames") or []
    if not frames:
        return ""
    layers = [f["layer"] for f in frames if f.get("layer")]
    span = f", layers {min(layers)} to {max(layers)}" if layers else ""
    where = str(Path(frames[-1]["path"]).parent)
    return f" ({len(frames)} frames{span}, in {where})"


def listing(ledger: Ledger, job_id: str | None = None) -> str:
    """Every lesson, or those a print taught and was given, each with its evidence; then those
    to promote and to retire."""
    lines: list[str] = []
    if job_id is not None:
        printed = ledger.get(job_id)
        if printed is None:
            raise Refused(f"job {job_id} isn't in the ledger")
        has = ", ".join(traits(printed.features or ()))
        lines += [
            f"# {printed.stl_name}, job {job_id}",
            "",
            f"Its part is {has or 'not measured (from before its features were kept)'}.",
            "",
            "## What it taught",
            "",
        ]
        taught = ledger.query(
            "SELECT DISTINCT l.* FROM print_lessons l JOIN print_lesson_evidence e"
            " ON e.lesson = l.id WHERE e.job_id=? ORDER BY l.id", job_id,
        )
        for t in taught:
            lines += [_line(t), *_evidence_lines(ledger, int(t["id"]))]
        if not taught:
            lines.append("Nothing: no correction, and no outcome with something to say.")
        lines += ["", "## What it was given", ""]
        given = ledger.query(
            "SELECT * FROM job_lessons WHERE job_id=? ORDER BY lesson", job_id
        )
        for g in given:
            s = latest_scores(ledger, int(g["lesson"])).get(job_id)
            scored = f"{s['score']} by {s['by']} ({s['why']})" if s else "not scored"
            lines.append(f"- {g['text']}\n    - {scored}")
        if not given:
            lines.append("None.")
        lines.append("")
    else:
        lines += ["# Every print lesson", ""]
        found = ledger.query("SELECT * FROM print_lessons ORDER BY id")
        for row in found:
            lines += [_line(row), *_evidence_lines(ledger, int(row["id"]))]
        if not found:
            lines.append("None yet.")
        lines.append("")
    promoting = to_promote(ledger)
    if promoting:
        lines += ["## To promote, for the operator", ""]
        lines += [
            f"- Lesson {row['id']}: used on {', '.join(good)} without a correction. Add it to "
            f"`skills/print-settings/` by PR, then `promote {row['id']} --as skill --where "
            "owner/repo#n`"
            for row, good in promoting
        ]
        lines.append("")
    retiring = to_retire(ledger)
    if retiring:
        lines += ["## To retire, for the reviewer", ""]
        lines += [f"- Lesson {row['id']}: {why}. `retire {row['id']}`" for row, why in retiring]
        lines.append("")
    return "\n".join(lines)

