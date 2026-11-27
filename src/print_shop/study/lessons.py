"""A settings study in the ledger, and what it found as lessons for a later, similar part
(mqucifer/print-shop#339, the way mqucifer/print-shop#106's print lessons are kept).

A study isn't a print: nothing was printed, so it has no row of `prints`, and its lessons are in
tables of their own beside the print lessons, created if missing, in the same file:
- **`settings_studies`**: each study, its part, its features as it prints and the keys they
  give, its load cases, every candidate read with its figures, what it recommended and why, and
  every tool call;
- **`study_lessons`**: what it found, each a candidate, keyed by the part's features: its
  traits (`setup.traits`, read from the mesh as it prints) and its joints (`joint:through`,
  `joint:dovetail`, `joint:latch`, `joint:fit`, ...). Drawn from the model's own findings, each
  with the candidates that show it and their figures as its evidence, and by code from the
  recommendation against the defaults;
- **`study_lesson_steps`**: each step, who and why, an edit keeping the words it replaced.

**A reviewer lets each through** (`accept`, `edit`, `reject`), signed `reviewer: <who>`, as print
lessons are, with the same words for their statuses (`rounds/lessons.py`). Nothing reaches a
study until then. **A kept lesson reaches a later study of a part that has every key it's keyed
to** (`for_part`): the model reads it at the start and from `past_studies`, with the earlier
studies' recommendations (`earlier`). It sets nothing: the model weighs it against what it
reads.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from print_shop.bench.assembly import Assembly
from print_shop.ingest.orientation import write_rotated
from print_shop.rounds.lessons import ACCEPTED, CANDIDATE, EDITED, REJECTED, Refused, signed
from print_shop.setup.context import PartContext
from print_shop.setup.features import Feature, measure
from print_shop.setup.traits import TRAITS, traits
from print_shop.study.reading import margin_said
from print_shop.study.schema import SCHEMA

if TYPE_CHECKING:  # the ledger opens these tables, so it imports this
    from print_shop.ledger import Ledger
    from print_shop.study.loop import StudyResult

__all__ = [
    "BY_CODE",
    "SCHEMA",
    "accept",
    "earlier",
    "edit",
    "for_part",
    "keys",
    "listing",
    "measured",
    "record",
    "reject",
]

#: Who draws a study's lessons: the code reading it.
BY_CODE = "the study"
#: The joints' keys: `joint:` and the joint's kind, or `latch` or `fit`.
JOINT = "joint:"
#: The statuses a lesson is given in.
KEPT = (ACCEPTED, EDITED)



def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def measured(stl: Path, turn: tuple[float, float, float], folder: Path,
             bed_contact_mm2: float) -> tuple[Feature, ...]:
    """A part's features as it prints in `turn`, measured laid but not turned about its up axis
    (mqucifer/print-shop#353's review): a turn about the plate's upright changes nothing of how
    it prints, but its box on the plate covers its diagonal, so its narrowest width read off that
    box would key a bar `flat_and_wide` and take `tall_and_narrow` from a fin."""
    laid = write_rotated(stl, (turn[0], turn[1], 0.0), folder / stl.name)
    return measure(laid, bed_contact_mm2)


def keys(features: Iterable[Feature], assembly: Assembly | None = None,
         part: str | None = None, context: PartContext | None = None) -> tuple[str, ...]:
    """What a part's study lessons are keyed by: its traits, and each kind of joint it's in."""
    found = list(traits(tuple(features)))
    joints: set[str] = set()
    if assembly is not None and part is not None:
        for joint in assembly.joints:
            if part in joint.between:
                joints.add(JOINT + joint.kind)
                if joint.latch is not None and joint.latch.on == part:
                    joints.add(JOINT + "latch")
    if context is not None and context.fits:
        joints.add(JOINT + "fit")
    return (*found, *sorted(joints))


def _reaches(held: Iterable[str], has: Iterable[str]) -> bool:
    held_set, has_set = set(held), set(has)
    return bool(held_set) and held_set <= has_set


def _evidence(result: StudyResult, names: Iterable[str]) -> list[dict[str, Any]]:
    found = []
    by_name = {r.candidate.name: r for r in result.readings}
    for name in names:
        reading = by_name.get(name)
        if reading is None:
            continue
        worst = reading.worst
        found.append({
            "candidate": name, "said": reading.candidate.said(),
            "time_s": reading.sliced.time_s if reading.sliced else None,
            "grams": reading.sliced.grams if reading.sliced else None,
            "passes": reading.passes, "worst": worst.plain() if worst else None,
        })
    return found


def _recommended_text(result: StudyResult) -> str | None:
    chosen, base = result.recommendation, result.defaults
    if chosen is None or base is None or chosen is base:
        return None
    said = [f"Studied: {chosen.candidate.said()} over the profile's defaults"]
    s, b = chosen.sliced, base.sliced
    if s is not None and b is not None and s.time_s is not None and b.time_s is not None:
        said.append(f"{(s.time_s - b.time_s) / 60:+.1f} min")
    if s is not None and b is not None and s.grams is not None and b.grams is not None:
        said.append(f"{s.grams - b.grams:+.1f} g")
    if chosen.margin is not None:
        said.append(f"its worst figure at {margin_said(chosen.margin)}"
                    + (f", the defaults' at {margin_said(base.margin)}"
                       if base.margin is not None and base.answered else ""))
    return ", ".join(said) + f". Its reason: {result.reason}"[:900]


def record(
    ledger: Ledger, *, part: str, stl_name: str, stl_sha256: str, features: Iterable[Feature],
    part_keys: Iterable[str], filament: str, cases: list[dict[str, Any]], result: StudyResult,
    started_at: str,
) -> tuple[str, list[int]]:
    """The study, and its lessons as candidates, in one transaction: its id and theirs."""
    from print_shop.study.table import as_plain

    study = f"study-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    plain = as_plain(result, part)
    keyed = list(part_keys)
    drawn: list[tuple[str, str, list[str]]] = [
        ("finding", lesson["text"], list(lesson["shown_by"])) for lesson in result.lessons]
    if (text := _recommended_text(result)) is not None:
        drawn.append(("recommendation", text, [str(result.recommended), "defaults"]))
    made: list[int] = []
    with ledger.writing() as db:
        db.execute(
            "INSERT INTO settings_studies (id, part, stl_name, stl_sha256, features, keys,"
            " filament, cases, model, ended, readings, recommended, reason, calls, started_at,"
            " finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (study, part, stl_name, stl_sha256,
             json.dumps([f.model_dump() for f in features]), json.dumps(keyed), filament,
             json.dumps(cases), result.model, result.ended, json.dumps(plain["readings"]),
             result.recommended, result.reason, json.dumps(plain["calls"]), started_at, _now()),
        )
        for kind, text, names in drawn:
            cursor = db.execute(
                "INSERT INTO study_lessons (study_id, drawn_from, text, keys, evidence,"
                " written_by, written_at, status, status_by, status_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (study, kind, text, json.dumps(keyed), json.dumps(_evidence(result, names)),
                 BY_CODE if kind == "recommendation" else f"model: {result.model}", _now(),
                 CANDIDATE, BY_CODE, _now()),
            )
            lesson = int(cursor.lastrowid or 0)
            _step_in(db, lesson, CANDIDATE, BY_CODE, f"from the study {study}'s {kind}")
            made.append(lesson)
    return study, made


def _step_in(db: sqlite3.Connection, lesson: int, status: str, by: str, why: str | None = None,
             was: str | None = None) -> None:
    db.execute("INSERT INTO study_lesson_steps (lesson, status, by, why, was, at)"
               " VALUES (?, ?, ?, ?, ?, ?)", (lesson, status, by, why, was, _now()))


def one(ledger: Ledger, lesson: int) -> dict[str, Any]:
    found = ledger.query("SELECT * FROM study_lessons WHERE id=?", lesson)
    if not found:
        raise Refused(f"there's no study lesson {lesson}")
    row = found[0]
    row["keys"] = json.loads(row["keys"])
    row["evidence"] = json.loads(row["evidence"])
    return row


def _set(ledger: Ledger, lesson: int, status: str, by: str, why: str | None = None,
         was: str | None = None, **columns: Any) -> None:
    with ledger.writing() as db:
        sets = ", ".join(f"{k}=?" for k in columns)
        db.execute(f"UPDATE study_lessons SET status=?, status_by=?, status_at=?"
                   f"{', ' + sets if sets else ''} WHERE id=?",
                   (status, by, _now(), *columns.values(), lesson))
        _step_in(db, lesson, status, by, why, was)


def accept(ledger: Ledger, lesson: int, by: str) -> None:
    """Let it through as it stands. Keyed to nothing, it would reach every part, so it's refused
    until it's keyed (`edit --keys`)."""
    by = signed(by)
    found = one(ledger, lesson)
    if not found["keys"]:
        raise Refused(f"study lesson {lesson} is keyed to nothing, so it would reach every "
                      "part: key it first (edit --keys)")
    if found["status"] != CANDIDATE:
        raise Refused(f"study lesson {lesson} is {found['status']}, not a candidate")
    _set(ledger, lesson, ACCEPTED, by)


def edit(ledger: Ledger, lesson: int, by: str, *, text: str | None = None,
         keyed: Iterable[str] | None = None, why: str) -> None:
    """Its words or its keys changed, and it's let through: the words it replaced are kept."""
    by = signed(by)
    found = one(ledger, lesson)
    if found["status"] == REJECTED:
        raise Refused(f"study lesson {lesson} was rejected")
    columns: dict[str, Any] = {}
    was: dict[str, Any] = {}
    if text is not None and text.strip():
        columns["text"], was["text"] = text.strip(), found["text"]
    if keyed is not None:
        chosen = [k.strip() for k in keyed if k.strip()]
        unknown = [k for k in chosen if k not in TRAITS and not k.startswith(JOINT)]
        if unknown:
            raise Refused(f"not a trait or a joint: {', '.join(unknown)}. The traits are "
                          f"{', '.join(TRAITS)}; a joint is {JOINT}KIND")
        columns["keys"], was["keys"] = json.dumps(chosen), found["keys"]
    if not columns:
        raise Refused("an edit changes its text or its keys")
    _set(ledger, lesson, EDITED, by, why, json.dumps(was), **columns)


def reject(ledger: Ledger, lesson: int, by: str, why: str) -> None:
    by = signed(by)
    one(ledger, lesson)
    if not why.strip():
        raise Refused("a rejection says why")
    _set(ledger, lesson, REJECTED, by, why)


def for_part(ledger: Ledger, part_keys: Iterable[str]) -> list[dict[str, Any]]:
    """The kept study lessons that reach a part with these keys: every one they're keyed to."""
    has = list(part_keys)
    found = []
    for row in ledger.query("SELECT * FROM study_lessons WHERE status IN (?, ?) ORDER BY id",
                            *KEPT):
        row["keys"] = json.loads(row["keys"])
        if _reaches(row["keys"], has):
            found.append(row)
    return found


def earlier(ledger: Ledger, part_keys: Iterable[str], most: int = 6) -> list[str]:
    """Earlier studies of parts with every key they had, newest first, in words."""
    has = list(part_keys)
    lines = []
    for row in ledger.query("SELECT * FROM settings_studies ORDER BY finished_at DESC"):
        if not _reaches(json.loads(row["keys"]), has):
            continue
        readings = {r["candidate"]["name"]: r for r in json.loads(row["readings"])}
        chosen = readings.get(row["recommended"] or "")
        said = (f"recommended {chosen['candidate']['said']}" if chosen
                else "recommended nothing")
        lines.append(f"{row['part']} ({row['stl_name']}), study {row['id']}: {said}"
                     + (f"; {row['reason']}" if row["reason"] else ""))
        if len(lines) == most:
            break
    return lines


def listing(ledger: Ledger, study: str | None = None) -> str:
    """Every study lesson, or one study's, for the reviewer."""
    rows = ledger.query("SELECT * FROM study_lessons"
                        + (" WHERE study_id=?" if study else "") + " ORDER BY id",
                        *((study,) if study else ()))
    if not rows:
        return "No study lessons" + (f" from {study}" if study else "") + ".\n"
    out = []
    for row in rows:
        out.append(f"- Lesson {row['id']} [{row['status']}], {row['drawn_from']} of "
                   f"{row['study_id']}, keyed to {', '.join(json.loads(row['keys'])) or 'nothing'}"
                   f": {row['text']}")
        for item in json.loads(row["evidence"]):
            worst = item.get("worst") or {}
            out.append(f"  - {item['candidate']}: {item['said']}; {item.get('time_s')} s, "
                       f"{item.get('grams')} g; worst {100 * float(worst.get('share', 0)):.0f}% "
                       f"of its limit ({worst.get('what', '-')})")
    return "\n".join(out) + "\n"
