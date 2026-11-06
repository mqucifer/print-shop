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

Lessons are here (`lessons`, `run_lessons`, `lesson_scores`, `lesson_steps`, and `after_actions`
for the model's review that writes them): `lessons.py` says how they're checked and given. Looking a
lesson up when a reading fails isn't here yet. `changes` gives each need's reading before and after
each round.

`records.py` writes `calls`, `interventions`, `timeline`, `runaways`, `tries` and `nudges` from a
run's events, so a run's own records can be read back through them. Older code can still be running
against this file, from a checkout nothing edits until it ends: tables and columns are only added,
and never to one it inserts into by position (`needs`, `readings`). Three tables were made again
once, keeping every row (`_made_again`): two as a close needs columns that may be empty, and
`lesson_scores` as a lesson's routes are scored apart from what a run was given.
"""

from __future__ import annotations

import json
import re
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
    -- Who gave the answers: `sponsor`, or `stand-in: <who>`. Empty from before it was asked.
    answered_by TEXT,
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
    -- start, resume, stop, compaction, restart, after_action, or asked: a call that ended
    -- without a tool call, asked what's missing.
    kind TEXT NOT NULL,
    name TEXT,
    at TEXT,
    at_call INTEGER,
    segment INTEGER NOT NULL,
    harness_commit TEXT,
    -- A stop's ending said, or what a compaction cut: replies shrunk by tool, turns' reasoning
    -- dropped, and `write` or `edit` texts made a note.
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
    -- The stop for approval it answers. A close of a run that never reached one has none.
    approval INTEGER,
    -- approved, sent_back or closed: a close ends a run without approving it. Who: `sponsor`, or
    -- `stand-in: <who>`.
    decision TEXT NOT NULL,
    by TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL,
    FOREIGN KEY (run, approval) REFERENCES approvals(run, n)
);
CREATE TABLE IF NOT EXISTS standing (
    -- A check the harness runs itself on files to be printed or shown, whatever the needs say:
    -- `thin` on each file before the stop for approval and at an ask, and the needs' own checks on
    -- what an ask shows. `stop` is done, ask or approval.
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL REFERENCES runs(run),
    round INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    stop TEXT NOT NULL,
    ask INTEGER,
    check_name TEXT NOT NULL,
    file TEXT,
    command TEXT NOT NULL,
    passed INTEGER NOT NULL,
    -- 1 where the bench had no answer to rely on: said, and not taken as a pass or a fail.
    unsure INTEGER NOT NULL,
    verdict TEXT,
    why TEXT NOT NULL,
    numbers TEXT,
    file_sha TEXT
);
CREATE TABLE IF NOT EXISTS need_swaps (
    -- A need changed or dropped while its old check failed on the files as they stood, and the new
    -- one passes or there is none: the reason the model gave, and both checks, shown at the next
    -- stop. On `cup-1` two needs were weakened this way so they would pass.
    run TEXT NOT NULL REFERENCES runs(run),
    version INTEGER NOT NULL,
    need TEXT NOT NULL,
    old_check TEXT NOT NULL,
    old_reading TEXT NOT NULL,
    new_check TEXT,
    new_reading TEXT,
    reason TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    shown_at TEXT,
    PRIMARY KEY (run, version, need)
);
CREATE TABLE IF NOT EXISTS after_actions (
    -- Each try at the after-action review, a model call on the run's own session after a
    -- person's approval or close. The run stays as they left it whatever this says.
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    -- The approval it follows, if it follows one: a closed run may have none.
    approval INTEGER,
    begun_at TEXT NOT NULL,
    first_call INTEGER NOT NULL,
    harness_commit TEXT NOT NULL,
    -- `reviewed`, or the ending that stopped it, and what it said.
    ended TEXT,
    ended_said TEXT,
    ended_at TEXT,
    last_call INTEGER,
    -- The decision it follows, approved or closed, by `decisions.id`.
    decision INTEGER REFERENCES decisions(id),
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS nudges (
    -- A bench check that kept failing, by its command and the files it names whatever its other
    -- arguments, and the lines its reply gained: each try and why, then show what you have and ask
    -- what you need, or end with `blocked`. It's never a limit. Written from the run's events.
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    command TEXT NOT NULL,
    files TEXT NOT NULL,
    -- The failures it followed, without a pass as a need checks it, and how many it takes.
    failures INTEGER NOT NULL,
    after INTEGER NOT NULL,
    -- Each try it named, by call, a pass with other arguments than a need's among them.
    calls TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS refusals (
    -- Each tool call that wasn't carried out as asked: malformed, ill-typed, or refused, with the
    -- reason's code (`edit.closest`, `not_settled`...). Written from the run's events, for the
    -- after-action review's index (mqucifer/print-shop#40).
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    tool TEXT NOT NULL,
    outcome TEXT NOT NULL,
    why TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS said_weighed (
    -- What the after-action review made of each thing a person said (`interventions`): whether
    -- it should be a lesson, which lesson of the review it became, and why.
    run TEXT NOT NULL REFERENCES runs(run),
    said INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    lesson INTEGER REFERENCES lessons(id),
    why TEXT NOT NULL,
    by TEXT NOT NULL,
    PRIMARY KEY (run, said, at_call)
);
CREATE TABLE IF NOT EXISTS lessons (
    -- What a run taught, for the next: written by the model in its after-action review, or by a
    -- person, and checked by a reviewer before any reaches another run.
    id INTEGER PRIMARY KEY,
    -- The run it came from.
    run TEXT NOT NULL REFERENCES runs(run),
    -- `model`, or the person who wrote it, signed as a pick is.
    written_by TEXT NOT NULL,
    written_at TEXT NOT NULL,
    -- The after-action review's call, for the model's.
    at_call INTEGER,
    -- The fix's shape, in its writer's words, with the vocabulary settled later.
    fix_shape TEXT NOT NULL,
    -- The area it's about, one of the five: job, verify, process, memory or loop. Before
    -- mqucifer/print-shop#40, build, check or context, each sorted once by `lessons.migrate`.
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    -- The calls and readings that show it, by the ledger's keys.
    evidence TEXT NOT NULL,
    -- candidate; accepted, edited or rejected; given; held or broken; promoted or retired.
    status TEXT NOT NULL,
    status_by TEXT,
    status_at TEXT,
    -- What a lesson for the harness becomes, never a note: a refusal, a bench check, a toolset
    -- check, a stop or a harness change; or `request`, a change to the request.
    becomes TEXT,
    -- What it became last, once promoted on a route (`lesson_routes` has each), and where.
    became TEXT,
    became_where TEXT
);
CREATE TABLE IF NOT EXISTS lesson_steps (
    -- Each change to a lesson, who made it and why: an edit keeps the words it replaced.
    id INTEGER PRIMARY KEY,
    lesson INTEGER NOT NULL REFERENCES lessons(id),
    status TEXT NOT NULL,
    by TEXT NOT NULL,
    why TEXT,
    was TEXT,
    run TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_lessons (
    -- Each lesson a run was given, and the words it was given in.
    run TEXT NOT NULL REFERENCES runs(run),
    lesson INTEGER NOT NULL REFERENCES lessons(id),
    given_at TEXT NOT NULL,
    -- `start`, in the first message, or by hand in the request, before there was code for it.
    how TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (run, lesson)
);
CREATE TABLE IF NOT EXISTS lesson_scores (
    -- Whether a lesson held on one of its routes in a run: used, not_used or in_the_way, with the
    -- calls that show it. The model scores what it was given at its after-action review, and a
    -- reviewer may score again; the analysis scores the other routes (mqucifer/print-shop#40).
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL REFERENCES runs(run),
    lesson INTEGER NOT NULL REFERENCES lessons(id),
    score TEXT NOT NULL,
    calls TEXT NOT NULL,
    why TEXT,
    by TEXT NOT NULL,
    at_call INTEGER,
    at TEXT NOT NULL,
    -- model, harness, request or operator: empty is the model's, from before routes.
    route TEXT
);
CREATE TABLE IF NOT EXISTS lesson_routes (
    -- Who acts on a lesson: the model, the harness, the request or the operator and stand-in
    -- (mqucifer/print-shop#40). The model proposes them, and a reviewer confirms or changes them.
    lesson INTEGER NOT NULL REFERENCES lessons(id),
    route TEXT NOT NULL,
    by TEXT NOT NULL,
    at TEXT NOT NULL,
    -- Where it reached on this route once delivered: what it became, and where that is.
    became TEXT,
    became_where TEXT,
    delivered_at TEXT,
    -- Only a score after the delivery, by `lesson_scores.id`, says whether it holds there.
    after_score INTEGER,
    -- A lesson for the model whose fix is built elsewhere and held stops being given as text.
    ended_at TEXT,
    ended_why TEXT,
    PRIMARY KEY (lesson, route)
);
CREATE TABLE IF NOT EXISTS lesson_keys (
    -- The checks a lesson is about, by command and verdict: what a look-up at a failure reads.
    -- An empty verdict is any.
    lesson INTEGER NOT NULL REFERENCES lessons(id),
    command TEXT NOT NULL,
    verdict TEXT NOT NULL,
    PRIMARY KEY (lesson, command, verdict)
);
CREATE TABLE IF NOT EXISTS request_lines (
    -- The request's set form (mqucifer/print-shop#36): each line by its id, its section
    -- (`requirement`, `non_negotiable`, `kind` or `stop`) and its text. The plan stop is `plan`.
    run TEXT NOT NULL REFERENCES runs(run),
    line TEXT NOT NULL,
    section TEXT NOT NULL,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (run, line)
);
CREATE TABLE IF NOT EXISTS breakdowns (
    -- How the reference and the request are built, as the model wrote it before its needs: the
    -- parts, what holds or connects what, and what the look rests on. Each call a version.
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    body TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (run, n)
);
CREATE TABLE IF NOT EXISTS need_marks (
    -- What ties each need of a version to the request: the lines it covers, whether it's a
    -- non-negotiable's (`fixed`), how its figure is read, and why no bench command measures a
    -- line an eye need covers. Apart from `needs`, which older code inserts into by position.
    run TEXT NOT NULL REFERENCES runs(run),
    version INTEGER NOT NULL,
    need TEXT NOT NULL,
    covers TEXT NOT NULL,
    fixed INTEGER NOT NULL,
    figure TEXT,
    no_bench TEXT,
    PRIMARY KEY (run, version, need)
);
CREATE TABLE IF NOT EXISTS need_measures (
    -- What each bench need of a version says it measures, from its toolset's short list
    -- (mqucifer/print-shop#37). A table of its own, keyed as `need_marks`: that one is inserted
    -- into by position, so a column added there would break older code writing to this ledger.
    run TEXT NOT NULL REFERENCES runs(run),
    version INTEGER NOT NULL,
    need TEXT NOT NULL,
    measures TEXT NOT NULL,
    PRIMARY KEY (run, version, need)
);
CREATE TABLE IF NOT EXISTS skill_reads (
    -- Each skill a run read, at which call, and the file it is: how a run finds its skills
    -- (mqucifer/print-shop#37). `found` 0 is a name no skill of its toolset has. `how` is `read`
    -- by `skill`, or `given` whole in a conversation's start, as its kind requires
    -- (mqucifer/print-shop#61); empty from before, when every one was read.
    run TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    name TEXT NOT NULL,
    file TEXT,
    found INTEGER NOT NULL,
    how TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS set_asides (
    -- A failing bench check the model said doesn't apply, with its reason, by its command and the
    -- files it names, and the failed tries it set aside: out of the list of failing checks until
    -- it fails again, and shown at the next stop (mqucifer/print-shop#45).
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    command TEXT NOT NULL,
    files TEXT NOT NULL,
    tries TEXT NOT NULL,
    why TEXT NOT NULL,
    shown_at TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS descriptions (
    -- Each description of the part the model wrote, sent again or said to still hold: the
    -- breakdown, a need's `what`, or the plan's figures, with the part's files as they stood, by
    -- what they hold (mqucifer/print-shop#53). `changed` is what had changed since the one
    -- before, for one sent again or said to hold, and `shown_at` where a person read that.
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    key TEXT NOT NULL,
    what TEXT NOT NULL,
    how TEXT NOT NULL,
    at_call INTEGER NOT NULL,
    round INTEGER,
    files TEXT NOT NULL,
    text TEXT,
    was TEXT,
    why TEXT,
    changed TEXT,
    shown_at TEXT,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS stale_descriptions (
    -- Each description found stale, where: `done`, which refused, or the ask it went beside.
    run TEXT NOT NULL REFERENCES runs(run),
    seq INTEGER NOT NULL,
    key TEXT NOT NULL,
    what TEXT NOT NULL,
    written_at INTEGER NOT NULL,
    held_at INTEGER,
    at_call INTEGER NOT NULL,
    found_at TEXT NOT NULL,
    changed TEXT NOT NULL,
    PRIMARY KEY (run, seq)
);
CREATE TABLE IF NOT EXISTS refinements (
    -- The refinement pass on an approved run (mqucifer/print-shop#51): which approval and which
    -- decision it follows, the code and Orca it ran with, what it sliced with, and how it ended.
    id INTEGER PRIMARY KEY,
    run TEXT NOT NULL REFERENCES runs(run),
    n INTEGER NOT NULL,
    approval INTEGER NOT NULL,
    decision INTEGER NOT NULL REFERENCES decisions(id),
    begun_at TEXT NOT NULL,
    harness_commit TEXT NOT NULL,
    orca TEXT NOT NULL,
    context TEXT NOT NULL,
    folder TEXT NOT NULL,
    ended TEXT,
    ended_at TEXT,
    UNIQUE (run, n)
);
CREATE TABLE IF NOT EXISTS refine_files (
    -- Each file of the approved folder: what it holds, its part in the print, its colour, and how
    -- it was meshed for Orca, against OCCT.
    refinement INTEGER NOT NULL REFERENCES refinements(id),
    folder TEXT NOT NULL,
    file TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    role TEXT NOT NULL,
    colour TEXT,
    label TEXT,
    meshed TEXT NOT NULL,
    PRIMARY KEY (refinement, folder, file)
);
CREATE TABLE IF NOT EXISTS refine_slices (
    -- Each plate Orca sliced: its files, filaments and pauses, the command, and what it said.
    refinement INTEGER NOT NULL REFERENCES refinements(id),
    plate TEXT NOT NULL,
    files TEXT NOT NULL,
    filaments TEXT NOT NULL,
    pauses TEXT,
    command TEXT NOT NULL,
    exit INTEGER NOT NULL,
    gcode TEXT,
    gcode_sha TEXT,
    layers INTEGER,
    seconds INTEGER,
    filament TEXT,
    warnings TEXT,
    PRIMARY KEY (refinement, plate)
);
CREATE TABLE IF NOT EXISTS refine_findings (
    -- What the pass read, where (file, layer, z) and what it means for the print. `source` is
    -- whose reading it is: `orca` (its warnings, labels, widths and figures), `bench` (a
    -- command's), or `pass` (the pass's own, from Orca's lines against the part).
    id INTEGER PRIMARY KEY,
    refinement INTEGER NOT NULL REFERENCES refinements(id),
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    plate TEXT,
    file TEXT,
    layer_from INTEGER,
    layer_to INTEGER,
    z_from REAL,
    z_to REAL,
    read TEXT NOT NULL,
    means TEXT NOT NULL,
    needs_change INTEGER NOT NULL,
    numbers TEXT,
    UNIQUE (refinement, seq)
);
CREATE TABLE IF NOT EXISTS refine_proposals (
    -- A change to the part a finding needs, proposed and never made: its words, its cost, and
    -- whether it was checked (`params` on the design's script, the run's needs, `pause` and Orca
    -- again) or is words with its measured reason.
    id INTEGER PRIMARY KEY,
    refinement INTEGER NOT NULL REFERENCES refinements(id),
    finding INTEGER NOT NULL REFERENCES refine_findings(id),
    change TEXT NOT NULL,
    cost TEXT NOT NULL,
    parameter TEXT,
    was REAL,
    becomes REAL,
    checked TEXT NOT NULL,
    check_said TEXT,
    folder TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposal_decisions (
    -- A person's yes or no to a proposal, signed as any decision is: `sponsor` or `stand-in: who`.
    id INTEGER PRIMARY KEY,
    proposal INTEGER NOT NULL REFERENCES refine_proposals(id),
    decision TEXT NOT NULL,
    by TEXT NOT NULL,
    note TEXT,
    at TEXT NOT NULL
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
    # What the model said its sheet shows, after seeing it and before it went: its own words.
    ("picks", "said_of_sheet_model_said", "TEXT"),
    # A build fault sent back apart from the pick, which is about the look.
    ("picks", "fix_first", "TEXT"),
    # The groups and files of the delivered folder at a stop, and where it was written.
    ("approvals", "review", "TEXT"),
    # The run's toolset: its kind, bench commands and skills (mqucifer/print-shop#37). Empty for a
    # run from before toolsets, which had every command.
    ("runs", "toolset", "TEXT"),
    # The request's stop an ask showed, by its id (`plan`, `S1`), if it was one.
    ("picks", "stop", "TEXT"),
    # The route a score is on (mqucifer/print-shop#40): empty is the model's, from before.
    ("lesson_scores", "route", "TEXT"),
    # The kind of object a lesson is for, as a request names it: empty is a general lesson.
    ("lessons", "part_kind", "TEXT"),
    # The reasoning effort the run's alias sends, read through LiteLLM, or `unknown`, and how.
    ("runs", "reasoning_effort", "TEXT"),
    ("runs", "reasoning_effort_from", "TEXT"),
    # Who gave intake's answers (mqucifer/print-shop#53): empty from before it was asked.
    ("questions", "answered_by", "TEXT"),
    # A part file the model set aside as no need reading it, with no command
    # (mqucifer/print-shop#52).
    ("set_asides", "unread_file", "TEXT"),
    # Whether a skill was read with `skill` or given whole in the start (mqucifer/print-shop#61).
    ("skill_reads", "how", "TEXT"),
)
#: Each stop asked, at which call, and picked when and by whom: made once `picks.stop` is there.
STOPS_VIEW = """CREATE VIEW IF NOT EXISTS stops AS
SELECT picks.run, picks.stop, request_lines.text AS what, picks.ask, picks.round, picks.at_call,
       picks.asked_at, picks.pick, picks.reason, picks.picked_at, picks.picked_by
FROM picks LEFT JOIN request_lines
       ON request_lines.run = picks.run AND request_lines.line = picks.stop
WHERE picks.stop IS NOT NULL"""


#: The approval decision a review from before `after_actions.decision` followed: its approval's.
APPROVAL_OF = (
    "(SELECT d.id FROM decisions AS d WHERE d.run = {a}.run AND d.approval = {a}.approval AND "
    "d.decision = 'approved' AND d.at <= {a}.begun_at ORDER BY d.id DESC LIMIT 1)"
)
FOLLOWED = (
    f"UPDATE after_actions SET decision = {APPROVAL_OF.format(a='after_actions')} "
    "WHERE decision IS NULL AND approval IS NOT NULL"
)
#: Tables made again once, by `DesignLedger._made_again`, and how each copies its rows. Each is
#: made again only while it's as it was: `decisions` and `after_actions` while `approval` can't be
#: empty, and `lesson_scores` while a score needs the lesson given as text to the run, which a
#: score on a route that isn't the model's has no row of (mqucifer/print-shop#40).
REMADE = (
    (
        "decisions",
        "INSERT INTO decisions_remade (id, run, approval, decision, by, note, at) "
        "SELECT id, run, approval, decision, by, note, at FROM decisions",
    ),
    (
        "after_actions",
        "INSERT INTO after_actions_remade (run, n, approval, begun_at, first_call, harness_commit, "
        "ended, ended_said, ended_at, last_call, decision) "
        "SELECT a.run, a.n, a.approval, a.begun_at, a.first_call, a.harness_commit, a.ended, "
        f"a.ended_said, a.ended_at, a.last_call, {APPROVAL_OF.format(a='a')} "
        "FROM after_actions AS a",
    ),
    (
        "lesson_scores",
        "INSERT INTO lesson_scores_remade (id, run, lesson, score, calls, why, by, at_call, at, "
        "route) SELECT id, run, lesson, score, calls, why, by, at_call, at, route "
        "FROM lesson_scores",
    ),
)


def _as_it_was(db: sqlite3.Connection, table: str) -> bool:
    """Whether a table in `REMADE` is still as it was, and so is to be made again."""
    held = {r["name"]: r for r in db.execute(f"PRAGMA table_info({table})")}
    if table == "lesson_scores":
        refers = {r["table"] for r in db.execute(f"PRAGMA foreign_key_list({table})")}
        return "run_lessons" in refers
    return not all(c in held for c in _columns(table)) or bool(held["approval"]["notnull"])


def _body(table: str) -> str:
    """A table's columns and keys, as `SCHEMA` makes it."""
    found = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);", SCHEMA, re.DOTALL)
    if found is None:
        raise LookupError(f"no table {table} in SCHEMA")
    return found.group(1)


def _columns(table: str) -> list[str]:
    lines = [line.strip() for line in _body(table).splitlines()]
    return [
        line.split()[0]
        for line in lines
        if line and not line.startswith(("--", "PRIMARY KEY", "FOREIGN KEY"))
    ]


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
            db.execute(STOPS_VIEW)
        self._made_again()
        from print_shop.rounds import lessons  # noqa: PLC0415 - lessons reads the ledger

        lessons.migrate(self)

    def _made_again(self) -> None:
        """A close may follow no stop for approval, and a review names the decision it follows
        (mqucifer/print-shop#32), so `decisions.approval` and `after_actions.approval` may be
        empty, and `after_actions` gains `decision`. SQLite can't drop a NOT NULL in place, so each
        table is made again as `SCHEMA` has it and every row copied, in one transaction. A review
        from before names the approval decision of its approval, which is what it followed. An
        older checkout still running against this file inserts by column name into both.
        `lesson_scores` is made again so a score needn't follow a lesson given as text: one on
        the harness's, the request's or the operator's route has none (mqucifer/print-shop#40)."""
        db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN IMMEDIATE")
            try:
                for table, copy in REMADE:
                    if not _as_it_was(db, table):
                        continue
                    db.execute(f"CREATE TABLE {table}_remade ({_body(table)})")
                    db.execute(copy)
                    db.execute(f"DROP TABLE {table}")
                    db.execute(f"ALTER TABLE {table}_remade RENAME TO {table}")
                # A review written since by older code, which doesn't know the column.
                db.execute(FOLLOWED)
                for table, _ in REMADE:
                    if db.execute(f"PRAGMA foreign_key_check({table})").fetchall():
                        raise sqlite3.IntegrityError(f"{table} made again breaks a reference")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        finally:
            db.close()

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

    def insert(self, table: str, **row: Any) -> int:
        """A new row, and its id."""
        names = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._open() as db:
            cursor = db.execute(
                f"INSERT INTO {table} ({names}) VALUES ({marks})",  # noqa: S608
                tuple(row.values()),
            )
            return int(cursor.lastrowid or 0)

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
                "skills_sha, image, image_id, alias, limits, reasoning_effort, "
                "reasoning_effort_from) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    facts.get("reasoning_effort"),
                    facts.get("reasoning_effort_from"),
                ),
            )
            if facts.get("toolset") is not None:
                db.execute("UPDATE runs SET toolset=? WHERE run=?", (_json(facts["toolset"]), run))

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

    def picked(
        self, run: str, ask: int, pick: str, reason: str, by: str, fix_first: str | None = None
    ) -> None:
        with self._open() as db:
            db.execute(
                "UPDATE picks SET pick=?, reason=?, picked_at=?, picked_by=?, fix_first=? "
                "WHERE run=? AND ask=?",
                (pick, reason, now(), by, fix_first, run, ask),
            )

    def sheet_said(self, run: str, ask: int, said: str) -> None:
        self.change(
            "UPDATE picks SET said_of_sheet_model_said=? WHERE run=? AND ask=?", said, run, ask
        )

    def stood(self, run: str, **row: Any) -> None:
        """One standing check's reading."""
        numbers = row.pop("numbers", None)
        with self._open() as db:
            db.execute(
                "INSERT INTO standing (run, round, at_call, stop, ask, check_name, file, command, "
                "passed, unsure, verdict, why, numbers, file_sha) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run,
                    row["round"],
                    row["at_call"],
                    row["stop"],
                    row.get("ask"),
                    row["check"],
                    row.get("file"),
                    row["command"],
                    int(bool(row["passed"])),
                    int(bool(row.get("unsure"))),
                    row.get("verdict"),
                    row["why"],
                    _json(numbers) if numbers is not None else None,
                    row.get("file_sha"),
                ),
            )

    def swapped(self, run: str, version: int, at_call: int, swap: dict[str, Any]) -> None:
        self.add(
            "need_swaps",
            run=run,
            version=version,
            need=swap["need"],
            old_check=_json(swap["old"]),
            old_reading=swap["old_reading"],
            new_check=_json(swap["new"]) if swap.get("new") else None,
            new_reading=swap.get("new_reading"),
            reason=swap["reason"],
            at_call=at_call,
        )

    def set_aside(self, run: str, seq: int, aside: dict[str, Any]) -> None:
        self.add(
            "set_asides",
            run=run,
            seq=seq,
            at_call=aside["at_call"],
            round=aside.get("round"),
            command=aside["command"],
            files=_json(aside["files"]),
            tries=_json(aside["tries"]),
            why=aside["why"],
            unread_file=aside.get("unread_file"),
        )

    def swaps_shown(self, run: str, where: str) -> None:
        self.change("UPDATE need_swaps SET shown_at=? WHERE run=? AND shown_at IS NULL", where, run)

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
            review=_json(facts.get("review")) if facts.get("review") else None,
        )

    def decided(self, run: str, approval: int | None, decision: str, by: str, note: str) -> int:
        """A person's decision, and its id: a review names the one it follows."""
        return self.insert(
            "decisions",
            run=run,
            approval=approval,
            decision=decision,
            by=by,
            note=note or None,
            at=now(),
        )

    def question(self, run: str, n: int, text: str, at_call: int) -> None:
        with self._open() as db:
            db.execute(
                "INSERT OR REPLACE INTO questions (run, n, text, asked_at, at_call) "
                "VALUES (?,?,?,?,?)",
                (run, n, text, now(), at_call),
            )

    def answered(self, run: str, n: int, answers: str, by: str | None = None) -> None:
        with self._open() as db:
            db.execute(
                "UPDATE questions SET answers=?, answered_at=?, answered_by=? WHERE run=? AND n=?",
                (answers, now(), by, run, n),
            )

    def described(self, run: str, row: dict[str, Any]) -> None:
        """A description of the part kept, sent again or said to hold (`descriptions.py`)."""
        with self._open() as db:
            seq = db.execute("SELECT COUNT(*) FROM descriptions WHERE run=?", (run,)).fetchone()[0]
        self.add("descriptions", run=run, seq=int(seq) + 1, **row)

    def stale_found(self, run: str, at_call: int, found_at: str, stale: list[Any]) -> None:
        """Each description found stale, at `done` or an ask."""
        with self._open() as db:
            counted = db.execute("SELECT COUNT(*) FROM stale_descriptions WHERE run=?", (run,))
            seq = int(counted.fetchone()[0])
        for one in stale:
            seq += 1
            self.add(
                "stale_descriptions",
                run=run,
                seq=seq,
                key=one.key,
                what=one.what,
                written_at=one.written_at,
                held_at=one.held_at,
                at_call=at_call,
                found_at=found_at,
                changed=_json([list(c) for c in one.changed]),
            )

    def request_read(self, run: str, form: Any) -> None:
        """The request's lines and stops, as `request.read` found them."""
        lines = [*form.requirements, *form.non_negotiables, *form.stops]
        with self._open() as db:
            for seq, line in enumerate(lines, 1):
                db.execute(
                    "INSERT OR REPLACE INTO request_lines VALUES (?,?,?,?,?)",
                    (run, line.id, line.section, seq, line.text),
                )
            if form.kind:
                db.execute(
                    "INSERT OR REPLACE INTO request_lines VALUES (?,?,?,?,?)",
                    (run, "kind", "kind", len(lines) + 1, form.kind),
                )

    def broken_down(self, run: str, n: int, at_call: int, body: dict[str, Any]) -> None:
        self.add("breakdowns", run=run, n=n, at_call=at_call, body=_json(body), at=now())

    def needs_marked(self, run: str, version: int, needs: list[Need]) -> None:
        with self._open() as db:
            for n in needs:
                db.execute(
                    "INSERT OR REPLACE INTO need_marks VALUES (?,?,?,?,?,?,?)",
                    (
                        run,
                        version,
                        n.id,
                        _json(n.covers),
                        int(n.fixed),
                        n.figure or None,
                        n.no_bench or None,
                    ),
                )
                if n.measures:
                    db.execute(
                        "INSERT OR REPLACE INTO need_measures (run, version, need, measures) "
                        "VALUES (?,?,?,?)",
                        (run, version, n.id, n.measures),
                    )

    def stop_asked(self, run: str, ask: int, stop: str) -> None:
        self.change("UPDATE picks SET stop=? WHERE run=? AND ask=?", stop, run, ask)
