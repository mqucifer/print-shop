"""The design ledger: what each round changed and which number moved, written by code.

SQLite, separate from the print ledger (`print_shop.ledger`), at `~/print-shop/design-runs/
ledger.sqlite`. Learning is much like model training (the Sponsor), so rounds are its data, kept
from the first one, beside the session's full requests (`events.jsonl`, which `transcript.rebuild`
replays), so it could later train an adapter (M14). ADR 0010: code records the failing need, the
starting state, what changed and which number moved. The model's account of itself isn't a source
for any of it.

The exceptions are marked as such: `rounds.fix_shape_model_said` holds what kind of change the round
was, in the model's own words, with the vocabulary settled later, and `rounds.closing_model_said`
what it said to the person at the end. They decide nothing.

Looking lessons up isn't here. `changes` gives each need's reading before and after each round.

`records.py` writes `calls`, `interventions`, `timeline`, `runaways` and `tries` from a run's
events, so a run's own records can be read back through them. Older code can still be running
against this file, from a checkout nothing edits until it ends: tables and columns are only added,
and never to one it inserts into by position (`needs`, `readings`).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from print_shop.rounds.needs import Need, Reading

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    request_sha TEXT NOT NULL,
    events TEXT NOT NULL,
    harness_commit TEXT NOT NULL,
    skills_sha TEXT NOT NULL,
    image TEXT NOT NULL,
    image_id TEXT,
    alias TEXT NOT NULL,
    limits TEXT NOT NULL,
    ending TEXT,
    ending_said TEXT,
    ended_at TEXT,
    calls INTEGER,
    usage TEXT,
    outcomes TEXT
);
CREATE TABLE IF NOT EXISTS needs (
    run TEXT NOT NULL REFERENCES runs(run),
    version INTEGER NOT NULL,
    need TEXT NOT NULL,
    what TEXT NOT NULL,
    source TEXT NOT NULL,
    quote TEXT,
    by TEXT NOT NULL,
    command TEXT,
    verdicts TEXT,
    number TEXT,
    at_least REAL,
    at_most REAL,
    look_for TEXT,
    accepted_at TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    PRIMARY KEY (run, version, need)
);
CREATE TABLE IF NOT EXISTS rounds (
    run TEXT NOT NULL REFERENCES runs(run),
    round INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    first_call INTEGER NOT NULL,
    needs_version INTEGER NOT NULL,
    -- The starting state: each file's hash, and the needs failing as it began.
    start_files TEXT NOT NULL,
    failing_at_start TEXT NOT NULL,
    ended_at TEXT,
    last_call INTEGER,
    script TEXT,
    -- The change: the text files' unified diff from the round's start.
    diff TEXT,
    end_files TEXT,
    clean_run_exit INTEGER,
    clean_run_seconds REAL,
    met INTEGER,
    -- In the model's own words, marked as such by its name. It decides nothing.
    fix_shape_model_said TEXT,
    -- The files `done` named as the part, in its words too: what's left of a need's files are
    -- test pieces.
    part TEXT,
    -- The same as the sheet's rows, each with its name.
    part_rows TEXT,
    -- What the model said to the person, in its words. It decides nothing.
    closing_model_said TEXT,
    -- What the clean run left, each piece of geometry by what it holds.
    judged_files TEXT,
    PRIMARY KEY (run, round)
);
CREATE TABLE IF NOT EXISTS readings (
    run TEXT NOT NULL,
    round INTEGER NOT NULL,
    need TEXT NOT NULL,
    by TEXT NOT NULL,
    exit INTEGER,
    verdict TEXT,
    number TEXT,
    value REAL,
    at_least REAL,
    at_most REAL,
    passed INTEGER NOT NULL,
    why TEXT NOT NULL,
    numbers TEXT,
    ran TEXT,
    seconds REAL,
    PRIMARY KEY (run, round, need),
    FOREIGN KEY (run, round) REFERENCES rounds(run, round)
);
CREATE TABLE IF NOT EXISTS bench_calls (
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL REFERENCES runs(run),
    round INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    argv TEXT NOT NULL,
    exit INTEGER NOT NULL,
    verdict TEXT,
    why TEXT,
    numbers TEXT,
    files TEXT NOT NULL,
    seconds REAL
);
CREATE TABLE IF NOT EXISTS picks (
    run TEXT NOT NULL REFERENCES runs(run),
    ask INTEGER NOT NULL,
    round INTEGER NOT NULL,
    need TEXT,
    question TEXT NOT NULL,
    variants TEXT NOT NULL,
    sheet TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    pick TEXT,
    reason TEXT,
    picked_at TEXT,
    -- Who picked: `sponsor`, or `stand-in: <who>`, whose taste this isn't the Sponsor's.
    picked_by TEXT,
    -- What it saw: each variant's files and test piece by what it holds, and the sheet's hash.
    seen TEXT,
    PRIMARY KEY (run, ask)
);
CREATE TABLE IF NOT EXISTS questions (
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    text TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    answers TEXT,
    answered_at TEXT,
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS calls (
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    at TEXT,
    seconds REAL,
    -- Which round it fell in, how many times a person had sent something, and which process it was.
    round INTEGER NOT NULL,
    phase INTEGER NOT NULL,
    segment INTEGER NOT NULL,
    finish TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    reasoning_tokens INTEGER,
    cached_tokens INTEGER,
    reasoning_chars INTEGER,
    content_chars INTEGER,
    tool_args_chars INTEGER,
    tools TEXT,
    -- The share of its reasoning, by characters, repeating earlier sentences word for word.
    repeat_share REAL,
    -- Chunk times and how far a tool call's arguments got, from the stream. None for an older run.
    stream TEXT,
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS interventions (
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    at TEXT,
    -- The first call it reached.
    at_call INTEGER NOT NULL,
    round INTEGER,
    phase INTEGER NOT NULL,
    -- note, answers, pick, send_back or reopen_needs.
    kind TEXT NOT NULL,
    from_who TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS timeline (
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    -- start, resume, stop, compaction or restart.
    kind TEXT NOT NULL,
    name TEXT,
    at TEXT,
    at_call INTEGER,
    segment INTEGER NOT NULL,
    harness_commit TEXT,
    said TEXT,
    -- A compaction's estimate either side, and the prompts measured either side.
    estimated_before INTEGER,
    estimated_after INTEGER,
    prompt_before INTEGER,
    prompt_after INTEGER,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS runaways (
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    -- limit, cut or repeats, by commas: nothing is cut for any of these. It's for review.
    why TEXT NOT NULL,
    at TEXT,
    round INTEGER,
    phase INTEGER,
    finish TEXT,
    completion_tokens INTEGER,
    reasoning_tokens INTEGER,
    -- Tokens no text received accounts for, which a stream that held arguments back leaves.
    tokens_unaccounted INTEGER,
    repeat_share REAL,
    repeat_limit REAL,
    -- What code can say it was doing, and what it had and hadn't got. A reviewer writes the note.
    doing TEXT NOT NULL,
    lacked TEXT NOT NULL,
    review_note TEXT,
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS tries (
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    need TEXT NOT NULL,
    command TEXT,
    passed INTEGER NOT NULL,
    why TEXT NOT NULL,
    exit INTEGER,
    verdict TEXT,
    value REAL,
    numbers TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS approvals (
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    round INTEGER NOT NULL,
    asked_at TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    sheet TEXT,
    sheet_sha TEXT,
    -- One row of the sheet each, and each file by what it holds.
    part_rows TEXT NOT NULL,
    part_hashes TEXT NOT NULL,
    closing_model_said TEXT,
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL REFERENCES runs(run),
    approval INTEGER NOT NULL,
    -- approved or sent_back. Who: `sponsor`, or `stand-in: <who>`.
    decision TEXT NOT NULL,
    by TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL,
    FOREIGN KEY (run, approval) REFERENCES approvals(run, n)
);
CREATE VIEW IF NOT EXISTS phases AS
SELECT calls.run, calls.phase, MIN(calls.n) AS first_call, MAX(calls.n) AS last_call,
       COUNT(*) AS calls, SUM(calls.seconds) AS seconds,
       SUM(calls.completion_tokens) AS completion_tokens,
       SUM(calls.reasoning_tokens) AS reasoning_tokens,
       (SELECT GROUP_CONCAT(i.kind || ' from ' || i.from_who, '; ') FROM interventions AS i
         WHERE i.run = calls.run AND i.phase = calls.phase) AS begun_by
FROM calls GROUP BY calls.run, calls.phase;
CREATE VIEW IF NOT EXISTS round_costs AS
SELECT run, round, MIN(n) AS first_call, MAX(n) AS last_call, COUNT(*) AS calls,
       SUM(seconds) AS seconds, SUM(completion_tokens) AS completion_tokens,
       SUM(reasoning_tokens) AS reasoning_tokens
FROM calls GROUP BY run, round;
CREATE VIEW IF NOT EXISTS changes AS
SELECT after.run, after.round, after.need, after.by, after.number,
       before.value AS before_value, after.value AS after_value,
       before.verdict AS before_verdict, after.verdict AS after_verdict,
       before.passed AS before_passed, after.passed AS after_passed,
       rounds.diff, rounds.fix_shape_model_said
FROM readings AS after
JOIN rounds ON rounds.run = after.run AND rounds.round = after.round
LEFT JOIN readings AS before
       ON before.run = after.run AND before.need = after.need AND before.round = after.round - 1;
"""

#: Columns added after a ledger may already exist: `CREATE TABLE IF NOT EXISTS` leaves it alone.
#: A pick from before `picked_by` has it empty, which says nothing of who made it.
ADDED = (
    ("picks", "picked_by", "TEXT"),
    ("picks", "seen", "TEXT"),
    ("rounds", "part", "TEXT"),
    ("rounds", "part_rows", "TEXT"),
    ("rounds", "closing_model_said", "TEXT"),
    ("rounds", "judged_files", "TEXT"),
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class DesignLedger:
    """Each write its own transaction, so a run that dies leaves everything before it."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as db:
            db.executescript(SCHEMA)
            for table, column, kind in ADDED:
                held = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in held:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        with self._open() as db:
            return list(db.execute(sql, args))

    def add(self, table: str, **row: Any) -> None:
        """A row a run's events give, the same each time they're read: a second reading adds
        nothing, so a run's records can be read back at any point."""
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._open() as db:
            db.execute(
                f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
                tuple(row.values()),
            )

    def change(self, sql: str, *args: Any) -> None:
        with self._open() as db:
            db.execute(sql, args)

    def run_resumed(self, run: str) -> None:
        """It's going again, so it hasn't ended."""
        self.change("UPDATE runs SET ending=NULL, ending_said=NULL, ended_at=NULL WHERE run=?", run)

    def run_started(self, run: str, **facts: Any) -> None:
        with self._open() as db:
            db.execute(
                "INSERT INTO runs (run, started_at, request_sha, events, harness_commit, "
                "skills_sha, image, image_id, alias, limits) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    now(),
                    facts["request_sha"],
                    facts["events"],
                    facts["harness_commit"],
                    facts["skills_sha"],
                    facts["image"],
                    facts["image_id"],
                    facts["alias"],
                    _json(facts["limits"]),
                ),
            )

    def run_ended(self, run: str, ending: str | None, said: Any, **totals: Any) -> None:
        """`ending` is None while it waits for the Sponsor: it hasn't ended."""
        with self._open() as db:
            db.execute(
                "UPDATE runs SET ending=?, ending_said=?, ended_at=?, calls=?, usage=?, outcomes=? "
                "WHERE run=?",
                (
                    ending,
                    _json(said) if ending else None,
                    now() if ending else None,
                    totals.get("calls"),
                    _json(totals.get("usage")),
                    _json(totals.get("outcomes")),
                    run,
                ),
            )

    def needs_accepted(self, run: str, version: int, needs: list[Need], at_call: int) -> None:
        with self._open() as db:
            for n in needs:
                db.execute(
                    "INSERT OR REPLACE INTO needs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run,
                        version,
                        n.id,
                        n.what,
                        n.source,
                        n.quote,
                        n.by,
                        n.command,
                        _json(n.verdicts),
                        n.number,
                        n.at_least,
                        n.at_most,
                        n.look_for,
                        now(),
                        at_call,
                    ),
                )

    def round_started(
        self,
        run: str,
        round_n: int,
        first_call: int,
        needs_version: int,
        files: dict[str, str],
        failing: list[str],
    ) -> None:
        with self._open() as db:
            db.execute(
                "INSERT OR REPLACE INTO rounds (run, round, started_at, first_call, needs_version, "
                "start_files, failing_at_start) VALUES (?,?,?,?,?,?,?)",
                (run, round_n, now(), first_call, needs_version, _json(files), _json(failing)),
            )

    def round_ended(
        self,
        run: str,
        round_n: int,
        last_call: int,
        script: str,
        diff: str,
        files: dict[str, str],
        code: int,
        seconds: float,
        met: bool,
        fix_shape_model_said: str | None,
        part: list[str] | None = None,
        part_rows: list[dict[str, Any]] | None = None,
        closing_model_said: str | None = None,
        judged_files: dict[str, str] | None = None,
    ) -> None:
        with self._open() as db:
            db.execute(
                "UPDATE rounds SET ended_at=?, last_call=?, script=?, diff=?, end_files=?, "
                "clean_run_exit=?, clean_run_seconds=?, met=?, fix_shape_model_said=?, part=?, "
                "part_rows=?, closing_model_said=?, judged_files=? WHERE run=? AND round=?",
                (
                    now(),
                    last_call,
                    script,
                    diff,
                    _json(files),
                    code,
                    seconds,
                    int(met),
                    fix_shape_model_said,
                    _json(part),
                    _json(part_rows),
                    closing_model_said,
                    _json(judged_files),
                    run,
                    round_n,
                ),
            )

    def reading(self, run: str, round_n: int, need: Need, reading: Reading) -> None:
        with self._open() as db:
            db.execute(
                "INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    round_n,
                    need.id,
                    reading.by,
                    reading.exit,
                    reading.verdict,
                    need.number,
                    reading.value,
                    need.at_least,
                    need.at_most,
                    int(reading.passed),
                    reading.why,
                    _json(reading.record.get("numbers")) if reading.record else None,
                    _json(reading.record.get("ran")) if reading.record else None,
                    reading.seconds,
                ),
            )

    def bench_call(
        self,
        run: str,
        round_n: int,
        at_call: int,
        argv: list[str],
        code: int,
        record: dict[str, Any] | None,
        files: dict[str, str],
        seconds: float,
    ) -> None:
        """Each bench answer the model asked for, between the harness's: calls to the first clean
        bench pass come from these (ADR 0010's fourth signal)."""
        record = record or {}
        with self._open() as db:
            db.execute(
                "INSERT INTO bench_calls (run, round, at_call, argv, exit, verdict, why, numbers, "
                "files, seconds) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    round_n,
                    at_call,
                    _json(argv),
                    code,
                    record.get("verdict"),
                    record.get("why"),
                    _json(record.get("numbers")),
                    _json(files),
                    seconds,
                ),
            )

    def asked(
        self,
        run: str,
        ask: int,
        round_n: int,
        need: str | None,
        question: str,
        variants: list[dict[str, Any]],
        sheet: str,
        at_call: int,
        seen: dict[str, Any] | None = None,
    ) -> None:
        with self._open() as db:
            db.execute(
                "INSERT OR REPLACE INTO picks (run, ask, round, need, question, variants, sheet, "
                "asked_at, at_call, seen) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    ask,
                    round_n,
                    need,
                    question,
                    _json(variants),
                    sheet,
                    now(),
                    at_call,
                    _json(seen),
                ),
            )

    def picked(self, run: str, ask: int, pick: str, reason: str, by: str) -> None:
        with self._open() as db:
            db.execute(
                "UPDATE picks SET pick=?, reason=?, picked_at=?, picked_by=? WHERE run=? AND ask=?",
                (pick, reason, now(), by, run, ask),
            )

    def approval_asked(self, run: str, n: int, **facts: Any) -> None:
        self.add(
            "approvals",
            run=run,
            n=n,
            round=facts["round"],
            asked_at=now(),
            at_call=facts["at_call"],
            sheet=facts["sheet"],
            sheet_sha=facts["sheet_sha"],
            part_rows=_json(facts["part_rows"]),
            part_hashes=_json(facts["part_hashes"]),
            closing_model_said=facts["closing"],
        )

    def decided(self, run: str, approval: int, decision: str, by: str, note: str) -> None:
        with self._open() as db:
            db.execute(
                "INSERT INTO decisions (run, approval, decision, by, note, at) "
                "VALUES (?,?,?,?,?,?)",
                (run, approval, decision, by, note or None, now()),
            )

    def question(self, run: str, n: int, text: str, at_call: int) -> None:
        with self._open() as db:
            db.execute(
                "INSERT OR REPLACE INTO questions (run, n, text, asked_at, at_call) "
                "VALUES (?,?,?,?,?)",
                (run, n, text, now(), at_call),
            )

    def answered(self, run: str, n: int, answers: str) -> None:
        with self._open() as db:
            db.execute(
                "UPDATE questions SET answers=?, answered_at=? WHERE run=? AND n=?",
                (answers, now(), run, n),
            )
